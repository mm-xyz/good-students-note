#!/usr/bin/env python3
"""
scripts/audio/render_cut.py — 依人審後的 cutplan.md 全自動出片(ffmpeg,零 LLM)

    python3 scripts/audio/render_cut.py --session sessions/<slug> \
        [--out final_cut.m4a] [--snap-window 0.4] [--fade 0.015] [--dry-run]

流程:
    1. 解析 cutplan.md 勾選(markdown 是人審真相源)+ cutplan.json 時間碼
    2. 驗證:md 與 json 的 block 集合一致、每個保留 block 的文字須逐字存在於
       來源 SRT(poddeck 式防幻覺:LLM/人不可能「發明」一段不存在的話)
    3. 剪點 snap:邊界若落在 prosody.json 的靜音段 ±snap-window 內,移到靜音中點
       (避免切在字中間);相鄰保留段間隔 < 0.2s 自動併成連續範圍
    4. ffmpeg filter_complex(atrim + 15ms fade in/out + concat)從**原始檔**
       (source.<ext>,非 16k 分析檔)剪出 final_cut.m4a
    5. cutplan.md 的 `## 章節標題` 行 → chapters.txt(新時間軸)+ cut_map.json

.cutplan_pending.json 還在時拒跑(提案未完成);MM 人審是流程步驟,本腳本不驗
「是否審過」— 排程/自主模式下,叫 render 的人要自己遵守「人審完才 render」。
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, pick_transcript, fmt_mmss, sec_to_ts

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# 2026-08-10 MM:節目音樂用 v1(三首各自的正式曲——開場 Park Avenue、
# 中場 just fun、片尾 to many mind);v2 的三個檔其實是同一首複製三份的佔位。
# 檔名尾巴標了建議取用區間(`M開_00：00-00：10`),cutplan 的 start=/end= 照它設。
MATERIAL_DIR = PROJECT_ROOT / "shared-material" / "水星貓的生活實驗室_v1"
LINE_RE = re.compile(r"^- \[( |x|X)\] ([BG]\d{3,5}) \[([^\]]+)\] (.*)$")
CHAPTER_RE = re.compile(r"^## (.+)$")
AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg")


def resolve_music(token: str, sdir: Path, material_dir: Path) -> Path | None:
    """🎵 檔名解析:session 目錄 → repo 根 → 絕對/家目錄路徑 → 共用素材庫。

    素材庫先找同名,再做**前綴匹配**(`opening` 命中 `opening_三個副本王.mp3`)
    ——素材檔名慣例 `opening_/break_/ending_` 開頭,cutplan 只要開頭對了就中;
    前綴命中多個=歧義,直接 FAIL 列候選,絕不靜默選第一個。"""
    f = Path(token)
    for c in (sdir / f, PROJECT_ROOT / f, f.expanduser(), material_dir / token):
        if c.is_file():
            return c
    if material_dir.is_dir():
        hits = sorted(p for p in material_dir.iterdir()
                      if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                      and p.name.startswith(token))
        if len(hits) > 1:
            sys.exit(f"[render] FAIL: 共用素材庫有 {len(hits)} 個 {token} 開頭的檔案:"
                     f"{'、'.join(p.name for p in hits)} — cutplan 的名字寫長一點")
        if hits:
            return hits[0]
    return None


MUSIC_RE = re.compile(r"^##\s*🎵\s*(\S+)((?:\s+\w+=[\d.]+)*)\s*$")
TEASER_RE = re.compile(r"^##\s*🎬\s*(.*)$")
CONFIG_RE = re.compile(r"^##\s*⚙️?\s*(.*)$")
CUT_RE = re.compile(r"^##\s*✂\s*([\d:.]+)\s*[-–~]\s*([\d:.]+)\s*(.*)$")
# cutplan ⚙ config 區可覆蓋的數值旋鈕(dash 寫法;config > CLI/預設)
CONFIG_KEYS = {"clip_gap", "clip_fade_in", "clip_fade_out", "music_speech_fade",
               "bgm_duck", "bgm_solo", "bgm_predrop", "bgm_rise",
               "max_pause", "pause_keep", "crossfade", "snap_window", "fade",
               "tempo"}


def parse_ts(tok: str) -> float:
    """`736.45` / `12:16.45` / `1:02:16.4` → 秒。"""
    parts = tok.split(":")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def bgm_envelope(m: dict, duck: float, solo: float, predrop: float,
                 rise: float) -> list[tuple[float, float]]:
    """疊軌感知的 BGM 音量包絡(分段線性 keypoints,音樂 local 時間軸,0–1 乘數)。

    有人聲疊著時壓在 duck(預設 40%),獨奏段升到 solo(預設 70%)並維持;
    人聲要進來前 predrop 秒先從 solo 降回 duck,人聲進場後再依 fadeout 秒
    一路到 0(fadeout 超過 tail 時夾到 tail)。人聲在哪是 render 從時間軸
    算出來的,不做音訊偵測。"""
    D = m["dur"]
    pts = [(0.0, 0.0)]
    if m["has_prev"]:  # 疊在前段人聲尾巴下進場:0→duck,人聲結束才升 solo
        fi = min(m["fadein"], max(m["lead"], 0.1))
        pts += [(fi, duck), (m["lead"], duck), (m["lead"] + rise, solo)]
    else:
        pts += [(m["fadein"], solo)]
    if m["has_next"]:  # 人聲要回來:提前 predrop 降回 duck,進場後 fadeout 到 0
        entry = D - m["tail"]
        fo = min(m["fadeout"], max(m["tail"], 0.1))
        pts += [(entry - predrop, solo), (entry, duck), (entry + fo, 0.0)]
    else:
        pts += [(D - m["fadeout"], solo)]
    pts.append((D, 0.0 if not m["has_next"] else pts[-1][1]))
    # 夾單調遞增(獨奏窗太短時退化成連續 ramp,不會時間倒流)
    out = []
    for t, v in pts:
        t = min(max(t, out[-1][0] if out else 0.0), D)
        if out and abs(t - out[-1][0]) < 1e-6 and abs(v - out[-1][1]) < 1e-6:
            continue
        out.append((t, v))
    return out


def env_to_expr(pts: list[tuple[float, float]]) -> str:
    """keypoints → ffmpeg volume 表達式(巢狀 if,段內 smoothstep 內插)。

    線性 ramp 在起終點有稜角、聽感生硬;smoothstep(x²(3-2x))頭尾都入彎,
    收放才是「舒服的遞增遞減」(原則 11)。st(0,x) 存進度供同段重用。"""
    expr = f"{pts[-1][1]:.3f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
        if t1 - t0 < 1e-6:
            continue
        if abs(v1 - v0) < 1e-6:
            seg = f"{v0:.3f}"
        else:
            seg = (f"({v0:.3f})+({v1 - v0:.3f})*"
                   f"st(0,(t-{t0:.3f})/{t1 - t0:.3f})*ld(0)*(3-2*ld(0))")
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def extend_unit_edges(u: dict, words: list[dict], limit: float = 0.8) -> None:
    """SRT block 時間常比實際語音短(EP15「可惜嗎」的「惜嗎」被切在剪點外):
    用 words.json 找 unit 首/末 block 首/尾字的真實時間,只向外擴、不內縮,
    上限 limit 秒——避免把 MM 剪掉的相鄰內容吃回來。"""
    for edge in ("start", "end"):
        b = (u["items"][0] if edge == "start" else u["items"][-1])["block"]
        flat = re.sub(r"\s+", "", b["text"])
        if not flat:
            continue
        win = [w for w in words if w["end"] > b["start"] - 0.3
               and w["start"] < b["end"] + limit]
        if edge == "end":
            for w in reversed(win):
                chars = re.sub(r"\s+", "", w["word"])
                if chars and chars[-1] == flat[-1]:
                    if b["end"] < w["end"] <= b["end"] + limit:
                        # 對齊到真實字尾=精確邊界:後續 snap/谷底/word_guard 都
                        # 跳過,否則會再外推、把 MM 剪掉的相鄰字吃回來
                        u["end"] = max(u["end"], w["end"])
                        u["end_exact"] = True
                    break
        else:
            for w in win:
                chars = re.sub(r"\s+", "", w["word"])
                if chars and chars[0] == flat[0]:
                    if b["start"] - limit <= w["start"] < b["start"]:
                        u["start"] = min(u["start"], w["start"])
                        u["start_exact"] = True
                    break


def parse_program(path: Path) -> list[dict]:
    """cutplan.md → 依文件順序的節目單(2026-07-28 節目結構,播放順序=文件順序)。

    items:
      {"kind":"block", "id", "keep", "raw", "clip"}  — clip=True 表示在 🎬 集錦區
                                                       (block 行可複製貼上、可重複出現)
      {"kind":"music", "file", "fadein", "fadeout", "lead", "tail", "start", "end"}
          — `## 🎵 檔案 [fadein=X fadeout=Y lead=L tail=T start=S end=E]`
            lead=音樂提前 L 秒疊進前面語音的尾巴;tail=後面語音提前 T 秒
            疊進音樂的尾巴;中段獨奏長度=採用長度-lead-tail(疊接式進出場);
            start/end=只取音檔的 S–E 秒(可選,預設從頭播到尾)
      {"kind":"cut", "a", "b", "note"}               — `## ✂ 12:16.3-12:17.4 說明`
          手動剪除區間(原始時間軸,秒或 mm:ss.s)。人審的逃生艙:whisper 字級
          時間戳把停頓吃進字的時長時,自動停頓收緊會被 word 保護擋下(EP16 12:00
          「臨時 任務」中間的 1.3s),這時直接標區間,不受 word_guard 攔阻。
      {"kind":"chapter", "title"}                    — 其他 `## 標題`
    raw = 去掉 speaker 前綴/行尾理由的正文,可能含 `~~刪除線~~`(對照 json 後才解析)。
    """
    program = []
    clip_mode = False
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        mcfg = CONFIG_RE.match(s)
        if mcfg:
            params = dict(re.findall(r"([\w-]+)=([\d.]+)", mcfg.group(1)))
            program.append({"kind": "config", "params": params})
            continue
        mm_ = MUSIC_RE.match(s)
        if mm_:
            params = dict(re.findall(r"(\w+)=([\d.]+)", mm_.group(2) or ""))
            program.append({"kind": "music", "file": mm_.group(1),
                            "fadein": float(params.get("fadein", 1.0)),
                            "fadeout": float(params.get("fadeout", 1.5)),
                            "lead": float(params.get("lead", 0.0)),
                            "tail": float(params.get("tail", 0.0)),
                            "start": float(params.get("start", 0.0)),
                            "end": (float(params["end"])
                                    if "end" in params else None)})
            clip_mode = False
            continue
        mx = CUT_RE.match(s)
        if mx:
            program.append({"kind": "cut", "a": parse_ts(mx.group(1)),
                            "b": parse_ts(mx.group(2)),
                            "note": mx.group(3).strip()})
            continue
        mt = TEASER_RE.match(s)
        if mt:
            clip_mode = True
            continue
        mc = CHAPTER_RE.match(s)
        if mc and not s.startswith("## Cutplan"):
            program.append({"kind": "chapter", "title": mc.group(1).strip()})
            clip_mode = False
            continue
        m = LINE_RE.match(s)
        if not m:
            continue
        mark, bid, body = m.group(1), m.group(2), m.group(4)
        body = body.rsplit(" ← ", 1)[0]
        body = re.sub(r"^\[[^\]]{1,20}\]\s*", "", body).strip()  # speaker 前綴
        program.append({"kind": "block", "id": bid, "keep": mark.lower() == "x",
                        "raw": body, "clip": clip_mode})
    return program


def parse_strikes(body: str) -> tuple[str, list[list[int]]]:
    """解析 `~~...~~` → (clean_text, 無空白座標區間)。未閉合的 ~~ 當字面文字。"""
    spans = []
    pos = 0          # 無空白字元座標
    i = 0
    clean = []
    while i < len(body):
        if body.startswith("~~", i):
            j = body.find("~~", i + 2)
            if j < 0:  # 未閉合:字面
                clean.append("~~")
                pos += 2
                i += 2
                continue
            seg = body[i + 2:j]
            seg_len = len(re.sub(r"\s+", "", seg))
            if seg_len:
                spans.append([pos, pos + seg_len])
            pos += seg_len
            clean.append(seg)
            i = j + 2
        else:
            ch = body[i]
            if not ch.isspace():
                pos += 1
            clean.append(ch)
            i += 1
    return "".join(clean), spans


def strike_removals(block: dict, spans: list[list[int]], words: list[dict],
                    pad: float = 0.04) -> list[list[float]]:
    """把 block 內的刪除線字元區間(無空白座標)換算成要移除的時間範圍。

    對齊法:取 block 時間窗內的 words,依序走字元流與 block 正文兩指標比對;
    對不上(轉錄/繁化差異)就退回按字元比例線性內插 — 寧可近似也不炸。
    """
    flat = re.sub(r"\s+", "", block["text"])
    win = [w for w in words if w["end"] > block["start"] - 0.3
           and w["start"] < block["end"] + 0.3]
    # char 座標 → (start_time, end_time)
    # win 取的是 block 時間窗 ±0.3s,**前後都會多抓到鄰接 block 的字**
    # (EP16 B0024:win[0]=「可能」192.76 其實是前一句 B0023 的尾巴)。
    # 逐字流對齊若假設 win[0] 就是 block 第一個字,整條座標會偏——B0024 實測
    # 偏 0.74s,剪點落在「我的partner。」中間,把 partner 切成半個字。
    # 正解:把 win 攤成字元流,先用 block 文字在裡面定位起點,再取對應時間。
    stream = [(ch, w["start"], w["end"])
              for w in win for ch in re.sub(r"\s+", "", w["word"])]
    at = "".join(c[0] for c in stream).find(flat)
    pos_ok = at >= 0
    char_times: list[tuple[float, float]] = (
        [(a, b) for _, a, b in stream[at:at + len(flat)]] if pos_ok else [])
    out = []
    dur = block["end"] - block["start"]
    for a, b in spans:
        if pos_ok and b <= len(char_times):
            t0 = char_times[a][0]
            t1 = char_times[b - 1][1]
        else:  # 線性內插 fallback
            t0 = block["start"] + dur * a / max(1, len(flat))
            t1 = block["start"] + dur * b / max(1, len(flat))
        out.append([max(block["start"], t0 - pad), min(block["end"], t1 + pad)])
    return out


def pause_removals(ranges: list[list[float]], silences: list[dict],
                   max_pause: float, keep: float,
                   words: list[dict] | None) -> list[list[float]]:
    """保留範圍內超過 max_pause 的靜音,收緊到 keep 秒(頭尾各留一半)。

    words 保護:RMS 門檻會把「講得小聲的字尾」誤判成靜音(EP15 0:49 事故),
    所以每個剪除範圍先夾進「真正沒有字」的空隙 — 與任何 word 區間都不相交。
    """
    out = []
    for a, b in ranges:
        for s in silences:
            if not (s["start"] > a + 0.3 and s["end"] < b - 0.3):
                continue
            c = clamp_silence(s, words)
            if not c:
                continue
            lo, hi = c
            if hi - lo > max_pause:
                out.append([lo + keep / 2, hi - keep / 2])
    return out


def word_guard(ranges: list[list[float]], words: list[dict],
               margin: float = 0.03) -> list[list[float]]:
    """最後防線:任何範圍邊界落在某個字的時間區間內 → 推到字邊界外,
    保證不切在字中間(塞音閉鎖段的能量谷會騙過谷底偵測)。"""
    def fix(t: float, is_start: bool) -> float:
        for w in words:
            if w["start"] + margin < t < w["end"] - margin:
                return w["start"] - 0.02 if is_start else w["end"] + 0.02
        return t
    return [[fix(a, True), fix(b, False)] for a, b in ranges if b > a]


def subtract(ranges: list[list[float]], removals: list[list[float]],
             min_frag: float = 0.12) -> list[list[float]]:
    """kept ranges 減去 removals;過短碎片丟棄。"""
    removals = merge_ranges([list(r) for r in removals], min_gap=0.0)
    out = []
    for a, b in ranges:
        cur = a
        for ra, rb in removals:
            if rb <= cur or ra >= b:
                continue
            if ra > cur and ra - cur >= min_frag:
                out.append([cur, ra])
            cur = max(cur, rb)
        if b - cur >= min_frag:
            out.append([cur, b])
    return out


def validate_program(blocks: list[dict], program: list[dict],
                     srt_text: str, gaps: list[dict] | None = None) -> None:
    """防幻覺/防手滑驗證 + 逐出現解析字級精剪(spans 掛回 program item)。
    (1) json 每個 block 至少在 md 出現一次;md 不得有 json 沒有的 id
        (重複出現合法 — 🎬 集錦區可複製貼上正文的行)
    (2) 每次出現的正文(去刪除線標記後)== json block 文字 — 不准改字
    (3) json block 文字逐字存在於來源 SRT(json 也不可竄改)
    G 列(空白/非語音):id 必須在 json 的 gaps,文字純說明不驗證。
    """
    json_by_id = {b["id"]: b for b in blocks}
    gap_by_id = {g["id"]: g for g in (gaps or [])}
    md_ids = {it["id"] for it in program if it["kind"] == "block"}
    missing = {i for i in json_by_id if i not in md_ids}
    extra = {i for i in md_ids if i not in json_by_id and i not in gap_by_id}
    if missing or extra:
        sys.exit(f"[render] FAIL: cutplan.md 與 cutplan.json block 不一致 "
                 f"(md 缺 {sorted(missing) or '無'} / md 多 {sorted(extra) or '無'})")
    flat = re.sub(r"\s+", "", srt_text)
    for it in program:
        if it["kind"] != "block":
            continue
        if it["id"] in gap_by_id:
            it["spans"] = []
            it["block"] = gap_by_id[it["id"]]
            it["gap"] = True
            continue
        b = json_by_id[it["id"]]
        jt = re.sub(r"\s+", "", b["text"])
        raw = it["raw"]
        # 原文本身含 ~~(whisper 會轉出「哦~~」這種語氣詞)→ 該 block 不解析刪除線
        if "~~" in raw and "~~" not in b["text"]:
            clean, spans = parse_strikes(raw)
        else:
            clean, spans = raw, []
        if re.sub(r"\s+", "", clean) != jt:
            sys.exit(f"[render] FAIL: {it['id']} cutplan.md 文字與 cutplan.json 不符"
                     f"(被改過?)— cutplan 只准翻勾選/加刪除線/加理由/加章節,"
                     f"文字不可動")
        if it["keep"] and jt and jt not in flat:
            sys.exit(f"[render] FAIL: {it['id']} 文字不存在於來源 SRT(cutplan.json "
                     f"被竄改?)— 重跑 cutplan.py prepare 再提案")
        it["spans"] = spans
        it["block"] = b


def clamp_silence(s: dict, words: list[dict] | None) -> tuple[float, float] | None:
    """把靜音段夾進「真正沒有字」的區間;字橫跨整段就不是停頓,回 None。

    RMS 門檻會把講得小聲的字尾算成靜音(EP15 0:49 事故),照著下刀會切掉字。
    pause_removals 與 snap_boundaries 共用這道保護。
    """
    lo, hi = s["start"], s["end"]
    for w in words or []:
        if w["end"] <= lo or w["start"] >= hi:
            continue
        if w["start"] <= lo:
            lo = max(lo, w["end"] + 0.05)
        elif w["end"] >= hi:
            hi = min(hi, w["start"] - 0.05)
        else:
            return None       # 字整個在中間 → 這段不是真停頓
    return (lo, hi) if hi > lo else None


def snap_boundaries(ranges: list[list[float]], silences: list[dict],
                    window: float, words: list[dict] | None = None,
                    long_silence: float = 1.0,
                    pad: float = 0.12) -> list[list[float]]:
    """每個範圍的頭尾若在靜音段 ±window 內,移到該靜音的中點。

    長靜音(>long_silence)例外:中點會把半段靜音吃進保留範圍,而前後兩個 unit
    的邊界又會 snap 到**同一個**靜音的中點 → 整段靜音原封不動留在成品裡
    (EP16 26:39 的 3.4s 事故:G0010 明明沒勾選,剪除量卻是 0)。長靜音改貼近端、
    只留 pad 秒呼吸:範圍尾→靜音起點+pad、範圍頭→靜音終點-pad。

    貼近端前先過 clamp_silence:靜音段的頭尾常含小聲字尾,直接貼會把字切掉
    (EP16「那你先說」的「說」被切 0.3s)。夾完仍算長靜音才貼,否則照中點走。
    """
    def snap(t: float, is_start: bool) -> float:
        for s in silences:
            if not (s["start"] - window <= t <= s["end"] + window):
                continue
            if s["end"] - s["start"] > long_silence:
                c = clamp_silence(s, words)
                if c and c[1] - c[0] > long_silence:
                    return c[1] - pad if is_start else c[0] + pad
                if c is None:
                    return t          # 字橫跨整段:根本不是停頓,別動邊界
            return (s["start"] + s["end"]) / 2
        return t
    return [[snap(a, True), snap(b, False)] for a, b in ranges]


def merge_ranges(ranges: list[list[float]], min_gap: float = 0.2) -> list[list[float]]:
    merged = []
    for a, b in sorted(ranges):
        if merged and a - merged[-1][1] < min_gap:
            merged[-1][1] = max(merged[-1][1], b)
        elif b > a:
            merged.append([a, b])
    return merged


def refine_boundaries(ranges: list[list[float]], wav_path: Path,
                      search: float = 0.09, win: float = 0.01) -> list[list[float]]:
    """波形平滑第一步:每個剪點滑到 ±search 秒內的能量谷底(短窗 RMS 最低點)。

    在能量最低的瞬間下刀,避免切在字頭/呼吸聲上。純 stdlib 讀 16k mono wav,
    只讀剪點附近的樣本,快。wav 不存在就原樣返回。
    """
    if not wav_path.exists():
        print("[render] ⚠ audio16k.wav 不存在,剪點不做波形谷底微調")
        return ranges
    import array
    import wave

    wf = wave.open(str(wav_path), "rb")
    sr = wf.getframerate()
    n_total = wf.getnframes()
    assert wf.getsampwidth() == 2 and wf.getnchannels() == 1, "expect 16-bit mono"

    def valley(t: float) -> float:
        i0 = max(0, int((t - search) * sr))
        i1 = min(n_total, int((t + search) * sr))
        step = int(win * sr)
        if i1 - i0 < step * 2:
            return t
        wf.setpos(i0)
        samples = array.array("h")
        samples.frombytes(wf.readframes(i1 - i0))
        best_off, best_e = 0, None
        for off in range(0, len(samples) - step, step // 2):
            e = sum(s * s for s in samples[off:off + step])
            if best_e is None or e < best_e:
                best_e, best_off = e, off
        return (i0 + best_off + step / 2) / sr

    out = [[valley(a), valley(b)] for a, b in ranges]
    wf.close()
    return [[a, b] for a, b in out if b - a > 0.1]


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def run_ffmpeg(src: Path, segments: list[dict], musics: list[dict], out: Path,
               fade: float, loudnorm: str | None, crossfade: float,
               dynaudnorm: str | None, tempo: float = 1.0) -> tuple[list[float], float]:
    """出片(節目結構 2026-07-28 v2:音樂改 overlay 疊接,不進 concat 鏈)。

    concat 鏈只有 speech/silence:
      {"kind":"speech","a","b","clip",fade_in?,fade_out?} | {"kind":"silence","dur"}
    fade_in/fade_out=烘進 segment 的淡入淡出(🎬 集錦頭尾、music 後主音軌進場);
    接縫規則:鄰接 silence 或任一側有 baked fade → 10ms 微交疊(fade 已烘好),
    其餘 speech↔speech=crossfade(40ms 三角交疊)。

    musics=[{"path","dur","env","lead","anchor_seg",...}]:每首依 dst 時間
    adelay 後 amix 疊上語音軌——anchor_seg=某 silence gap 的 segment index
    (音樂起點=gap 起點-lead,蓋過前面語音尾巴 lead 秒、後面語音頭 tail 秒);
    anchor_seg=None ⇒ 片尾音樂(起點=語音結束-lead,收在音樂自然結束)。
    音量走 env(bgm_envelope 的疊軌感知包絡,volume expr 逐 frame 內插),
    取代舊的 afade in/out。
    語音鏈之後依序:dynaudnorm(人聲動態均衡)→ amix 音樂 → loudnorm。

    tempo>1 = 語速加速:atempo **只烘進語音 segment**(音樂走 overlay 支線,
    不變速、長度不變),所以先加速再算 dst 時間軸,音樂錨點自然落在加速後的
    位置——等同「語速調完才拼 opening/closing」。
    回傳 (每個 segment 在新時間軸上的起點, 成品總長)。
    """
    n = len(segments)
    music_paths = []
    for m in musics:
        if m["path"] not in music_paths:
            music_paths.append(m["path"])
    input_idx = {p: i + 1 for i, p in enumerate(music_paths)}

    durs = []
    parts = []
    for i, s in enumerate(segments):
        if s["kind"] == "speech":
            d = (s["b"] - s["a"]) / tempo
            expr = (f"[0:a]atrim=start={s['a']:.3f}:end={s['b']:.3f},"
                    f"asetpts=PTS-STARTPTS")
            if abs(tempo - 1.0) > 1e-6:
                expr += f",atempo={tempo:.4f}"
            expr += (f",aformat=sample_rates=48000:"
                     f"channel_layouts=stereo")
            f_in = s.get("fade_in") or (fade if i == 0 else 0.0)
            f_out = s.get("fade_out") or (fade if i == n - 1 else 0.0)
            if f_in:
                expr += f",afade=t=in:d={f_in:.3f}"
            if f_out:
                expr += f",afade=t=out:st={max(0.0, d - f_out):.3f}:d={f_out:.3f}"
        else:  # silence
            d = s["dur"]
            expr = (f"anullsrc=r=48000:cl=stereo,atrim=start=0:end={d:.3f},"
                    f"asetpts=PTS-STARTPTS,aformat=sample_rates=48000:"
                    f"channel_layouts=stereo")
        parts.append(expr + f"[a{i}]")
        durs.append(d)

    def joint_cf(i: int) -> float:
        a, b = segments[i - 1], segments[i]
        if (a["kind"] == "silence" or b["kind"] == "silence"
                or a.get("fade_out") or b.get("fade_in")):
            cf = 0.01
        else:
            cf = crossfade
        return max(0.005, min(cf, durs[i - 1] / 2 - 0.005, durs[i] / 2 - 0.005))

    cfs = [joint_cf(i) for i in range(1, n)]
    prev = "a0"
    for i in range(1, n):
        nxt = f"x{i}" if i < n - 1 else "cat"
        parts.append(f"[{prev}][a{i}]acrossfade=d={cfs[i - 1]:.3f}:c1=tri:c2=tri[{nxt}]")
        prev = nxt
    if n == 1:
        parts.append("[a0]anull[cat]")

    dst_starts = []
    acc = 0.0
    for i, d in enumerate(durs):
        dst_starts.append(max(0.0, acc - sum(cfs[:i])))
        acc += d
    chain_end = dst_starts[-1] + durs[-1]

    post = "cat"
    if dynaudnorm:
        # 人聲動態均衡:三人同軌麥距/音量不同,先拉齊再交給 loudnorm 定錨
        parts.append(f"[cat]dynaudnorm={dynaudnorm}[dyn]")
        post = "dyn"
    total = chain_end
    for k, m in enumerate(musics):
        at = max(0.0, (chain_end if m["anchor_seg"] is None
                       else dst_starts[m["anchor_seg"]]) - m["lead"])
        m["at"] = at
        total = max(total, at + m["dur"])
        parts.append(
            f"[{input_idx[m['path']]}:a]atrim=start={m['ss']:.3f}"
            f":end={m['ss'] + m['dur']:.3f},"
            f"asetpts=PTS-STARTPTS,aformat=sample_rates=48000:"
            f"channel_layouts=stereo,"
            f"volume='{env_to_expr(m['env'])}':eval=frame,"
            f"adelay={int(round(at * 1000))}:all=1[m{k}]")
    if musics:
        labels = "".join(f"[m{k}]" for k in range(len(musics)))
        parts.append(f"[{post}]{labels}amix=inputs={len(musics) + 1}:"
                     f"duration=longest:normalize=0[mixed]")
        post = "mixed"
    if loudnorm:
        # 音量一致化(EBU R128 動態 loudnorm):整體拉到 podcast 標準
        parts.append(f"[{post}]loudnorm={loudnorm}[out]")
    else:
        parts.append(f"[{post}]anull[out]")

    script = out.parent / ".render_filter.txt"
    script.write_text(";\n".join(parts), encoding="utf-8")
    codec = {".wav": [],
             ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
             }.get(out.suffix, ["-c:a", "aac", "-b:a", "192k"])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    for mp in music_paths:
        cmd += ["-i", str(mp)]
    cmd += ["-filter_complex_script", str(script), "-map", "[out]", *codec, str(out)]
    print(f"[render] ffmpeg {n} 段語音/靜音 + {len(musics)} 首疊接音樂 → {out.name}")
    subprocess.run(cmd, check=True)
    script.unlink()
    return dst_starts, total


def main():
    ap = argparse.ArgumentParser(description="cutplan → ffmpeg 全自動出片")
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", default="final_cut.mp3",
                help="輸出檔名;副檔名決定編碼(.mp3/.m4a/.wav)")
    ap.add_argument("--snap-window", type=float, default=0.4)
    ap.add_argument("--fade", type=float, default=0.015)
    ap.add_argument("--loudnorm", default="I=-16:TP=-1.5:LRA=11",
                    help="EBU R128 音量一致化參數(預設 podcast 標準 -16 LUFS);"
                         "傳空字串停用")
    ap.add_argument("--crossfade", type=float, default=0.04,
                    help="接縫交疊秒數(acrossfade;預設 40ms,越大越滑順但會吃字尾)")
    ap.add_argument("--clip-fade-in", type=float, default=2.0,
                    help="🎬 集錦片段開頭的淡入秒數(預設 2.0)")
    ap.add_argument("--clip-fade-out", type=float, default=2.0,
                    help="🎬 集錦片段結尾的淡出秒數(預設 2.0)")
    ap.add_argument("--clip-gap", type=float, default=0.5,
                    help="🎬 集錦片段之間的靜音間隔秒數(預設 0.5;0=停用)")
    ap.add_argument("--music-speech-fade", type=float, default=1.5,
                    help="音樂 tail 疊接下、主音軌進場的淡入秒數(預設 1.5)")
    ap.add_argument("--bgm-duck", type=float, default=0.15,
                    help="BGM 疊到人聲時的振幅乘數(預設 0.15≈-16.5dB,"
                         "標準 speech bed;振幅非響度,0.4 其實只小 8dB)")
    ap.add_argument("--bgm-solo", type=float, default=0.55,
                    help="BGM 獨奏段的振幅乘數(預設 0.55≈-5dB)")
    ap.add_argument("--bgm-predrop", type=float, default=2.0,
                    help="人聲進場前幾秒開始把 solo 降回 duck(預設 2.0)")
    ap.add_argument("--bgm-rise", type=float, default=1.5,
                    help="人聲結束後 BGM 從 duck 升到 solo 的秒數(預設 1.5)")
    ap.add_argument("--material-dir", type=Path, default=MATERIAL_DIR,
                    help="共用素材庫(🎵 檔名找不到時在此做前綴匹配;"
                         "預設 shared-material/水星貓的生活實驗室_v1)")
    ap.add_argument("--dynaudnorm", default="m=4:p=0.9",
                    help="人聲動態均衡參數(ffmpeg dynaudnorm;多人同軌音量拉齊,"
                         "預設 m=4:p=0.9 保守增益;傳空字串停用)")
    ap.add_argument("--tempo", type=float, default=1.0,
                    help="語速倍率(只套語音,配樂不變速也不變長;1.06≈快一成不失真,"
                         ">1.15 會開始有壓迫感)")
    ap.add_argument("--max-pause", type=float, default=0.9,
                    help="保留段內超過此秒數的停頓自動收緊(0=停用)。"
                         "2026-08-10 MM 拍板 1.5→0.9:剪掉停頓是常態、留白才是"
                         "例外——人審在文字上分辨不出 1.4s 和 0.6s 的死寂"
                         "(EP16 18:28 那個 1.38s 兩位主持人都是聽成品才發現),"
                         "所以預設全收,要保留的用 G 列勾回來")
    ap.add_argument("--pause-keep", type=float, default=0.6,
                    help="收緊後保留的停頓長度(秒)")
    ap.add_argument("--dry-run", action="store_true", help="只印剪輯範圍,不跑 ffmpeg")
    ap.add_argument("--dump-ranges", type=Path,
                    help="把保留區間(原始時間軸,毫秒精度)寫成 JSON — "
                         "回歸測試與 debug 用,dry-run 的 mm:ss 看不出毫秒差")
    args = ap.parse_args()

    if not 0.5 <= args.tempo <= 2.0:
        sys.exit(f"[render] FAIL: tempo={args.tempo} 超出單顆 atempo 的合法範圍 "
                 f"0.5–2.0(語速用途實務上 0.9–1.2 就夠;要更極端得串接多顆)")
    sdir = Path(args.session).resolve()
    if (sdir / ".cutplan_pending.json").exists():
        sys.exit("[render] FAIL: .cutplan_pending.json 還在 — 剪輯提案未完成,"
                 "先讓對話 agent 提案 + MM 人審 cutplan.md")
    cp = json.loads((sdir / "cutplan.json").read_text(encoding="utf-8"))
    program = parse_program(sdir / "cutplan.md")

    # ── ⚙ config 區:cutplan 是參數真相源,覆蓋 CLI/預設 ──
    applied = {}
    for it in program:
        if it["kind"] != "config":
            continue
        for k, v in it["params"].items():
            attr = k.replace("-", "_")
            if attr not in CONFIG_KEYS:
                sys.exit(f"[render] FAIL: ⚙ config 不認識的鍵「{k}」(可用:"
                         + " ".join(sorted(x.replace("_", "-")
                                           for x in CONFIG_KEYS)) + ")")
            setattr(args, attr, float(v))
            applied[k] = v
    if applied:
        print("[render] ⚙ config: "
              + " ".join(f"{k}={v}" for k, v in applied.items()))

    spk_srt = sdir / "transcript.speakers.srt"
    srt_src = spk_srt if spk_srt.exists() else pick_transcript(sdir)
    srt_text = "".join(c["text"] for c in parse_srt(srt_src))
    validate_program(cp["blocks"], program, srt_text, cp.get("gaps"))

    wp = sdir / "words.json"
    words = json.loads(wp.read_text(encoding="utf-8")) if wp.exists() else None
    if words:
        # whisper 字級時間戳 artifact:單字橫跨十幾秒(EP15 的「好」718→735s),
        # 會讓 word_guard 把兩側剪點各推到假字頭尾 → 音訊重複;一律丟棄
        bad = [w for w in words if w["end"] - w["start"] > 3.0]
        if bad:
            print("[render] ⚠ 丟棄異常長 word(>3s,whisper 時間戳 artifact):"
                  + " ".join(f"「{w['word']}」{w['start']:.1f}s+{w['end']-w['start']:.1f}s"
                             for w in bad[:5])
                  + (f" …共 {len(bad)} 個" if len(bad) > 5 else ""))
            words = [w for w in words if w["end"] - w["start"] <= 3.0]
    silences = []
    pj = sdir / "prosody.json"
    if pj.exists():
        silences = json.loads(pj.read_text(encoding="utf-8")).get("silences", [])
    else:
        print("[render] ⚠ prosody.json 不存在,剪點不 snap 靜音、停頓不收緊")

    # ── program → units(播放順序=文件順序;doc 連續且 src 時間連續的 kept
    #    blocks 併成一個 speech unit;🎬 集錦行自成 clip units)──
    units = []       # {"kind":"speech","start","end","items":[...],"clip"} | music
    chapters = []    # {"title","anchor"=下一個 unit 的 index}
    manual_cuts = sorted([[it["a"], it["b"]] for it in program
                          if it["kind"] == "cut" and it["b"] > it["a"]])
    for it in program:
        if it["kind"] in ("config", "cut"):
            continue
        if it["kind"] == "chapter":
            chapters.append({"title": it["title"], "anchor": len(units)})
        elif it["kind"] == "music":
            path = resolve_music(it["file"], sdir, args.material_dir)
            if not path:
                sys.exit(f"[render] FAIL: 音樂檔不存在:{it['file']}(session/"
                         f"repo 根/絕對路徑與共用素材庫 {args.material_dir} "
                         f"的前綴匹配都找不到)")
            file_dur = ffprobe_duration(path)
            m_start = it["start"]
            m_end = min(it["end"], file_dur) if it["end"] else file_dur
            if m_end - m_start < 0.5:
                sys.exit(f"[render] FAIL: {it['file']} start={m_start}/"
                         f"end={m_end}(音檔長 {file_dur:.1f}s)取不出有效區間")
            units.append({"kind": "music", "path": path.resolve(),
                          "dur": m_end - m_start, "ss": m_start,
                          "fadein": it["fadein"], "fadeout": it["fadeout"],
                          "lead": it["lead"], "tail": it["tail"]})
        elif it["keep"]:
            b = it["block"]
            last = units[-1] if units else None
            joinable = (last and last["kind"] == "speech"
                        and not last.get("raw") and not it.get("gap")
                        and last["clip"] == it["clip"]
                        and 0 <= b["start"] - last["end"] < 2.0)
            if joinable:
                last["end"] = b["end"]
                last["items"].append(it)
            else:
                # 兩個 🎬 集錦 unit 相鄰(時間不連續)→ 插入靜音間隔
                if (args.clip_gap > 0 and last and last["kind"] == "speech"
                        and last["clip"] and it["clip"]):
                    units.append({"kind": "silence", "dur": args.clip_gap})
                units.append({"kind": "speech", "start": b["start"], "end": b["end"],
                              "items": [it], "clip": it["clip"],
                              "raw": it.get("gap", False)})

    # ── 🎵 music unit → overlay 疊接:中段換成獨奏長度的 silence gap,
    #    音樂本體記進 musics,render 時 adelay+amix 疊上語音軌 ──
    musics = []
    out_units = []
    for i, u in enumerate(units):
        if u["kind"] != "music":
            out_units.append(u)
            continue
        has_prev = any(v["kind"] == "speech" for v in units[:i])
        nxt = next((v for v in units[i + 1:] if v["kind"] == "speech"), None)
        lead = u["lead"] if has_prev else 0.0
        tail = u["tail"] if nxt else 0.0
        m = {"path": u["path"], "dur": u["dur"], "ss": u["ss"],
             "fadein": u["fadein"], "fadeout": u["fadeout"],
             "lead": lead, "tail": tail, "has_prev": has_prev,
             "has_next": nxt is not None, "anchor_ui": None}
        if nxt:
            gap = u["dur"] - lead - tail
            if gap < 0.1:
                sys.exit(f"[render] FAIL: {u['path'].name} 音檔長 {u['dur']:.1f}s "
                         f"不夠 lead={lead}+tail={tail} 的疊接,中段獨奏只剩 "
                         f"{gap:.1f}s — 調小 lead/tail 或換長一點的音樂")
            out_units.append({"kind": "silence", "dur": gap})
            m["anchor_ui"] = len(out_units) - 1
            nxt["after_music"] = True  # 主音軌在音樂 tail 下進場,烘淡入
        m["env"] = bgm_envelope(m, args.bgm_duck, args.bgm_solo,
                                args.bgm_predrop, args.bgm_rise)
        musics.append(m)
    units = out_units

    n_strike = 0
    if any(it.get("spans") for u in units if u["kind"] == "speech"
           for it in u["items"]) and not words:
        sys.exit("[render] FAIL: cutplan 有 ~~刪除線~~ 但缺 words.json — "
                 "用新版 transcribe_local.py 重轉錄一次產生")

    # ── 每個 speech unit:snap → 字級精剪/停頓收緊 → 谷底 → word 保護 ──
    segments = []
    unit_first_seg = {}
    n_pause = 0
    manual_secs = 0.0
    for ui, u in enumerate(units):
        if u["kind"] == "silence":
            unit_first_seg[ui] = len(segments)
            segments.append(u)
            continue
        if u.get("raw"):  # G 空白列:保留原聲原長,不 snap/不收停頓/不精剪
            unit_first_seg[ui] = len(segments)
            segments.append({"kind": "speech", "a": u["start"], "b": u["end"],
                             "clip": u["clip"]})
            continue
        if words:
            extend_unit_edges(u, words)
        ranges = merge_ranges(snap_boundaries([[u["start"], u["end"]]],
                                              silences, args.snap_window, words))
        removals = []
        for it in u["items"]:
            if it.get("spans"):
                removals += strike_removals(it["block"], it["spans"], words)
                n_strike += len(it["spans"])
        if args.max_pause > 0 and silences:
            pr = pause_removals(ranges, silences, args.max_pause,
                                args.pause_keep, words)
            n_pause += len(pr)
            removals += pr
        if removals:
            ranges = subtract(ranges, removals)
        ranges = refine_boundaries(ranges, sdir / "audio16k.wav")
        if words:
            ranges = word_guard(ranges, words)
        if manual_cuts:
            # ✂ 手動剪除擺在 word_guard 之後:人審點名的區間說了算,不受
            # 「whisper 說這裡有字」的保護攔阻(那正是它要救的失效情境)
            before = sum(b - a for a, b in ranges)
            ranges = subtract(ranges, manual_cuts)
            manual_secs += before - sum(b - a for a, b in ranges)
        ranges = merge_ranges(ranges, min_gap=0.05)
        if ranges and u.get("start_exact"):
            ranges[0][0] = u["start"]
        if ranges and u.get("end_exact"):
            ranges[-1][1] = u["end"]
        unit_first_seg[ui] = len(segments)
        segs = [{"kind": "speech", "a": a, "b": b, "clip": u["clip"]}
                for a, b in ranges]
        if segs:
            if u["clip"]:  # 🎬 集錦:頭尾烘 2s 淡入/淡出(unit 級,不是每個小段)
                segs[0]["fade_in"] = args.clip_fade_in
                segs[-1]["fade_out"] = args.clip_fade_out
            if u.get("after_music"):  # 音樂 tail 疊接下進場的主音軌
                segs[0]["fade_in"] = max(segs[0].get("fade_in", 0.0),
                                         args.music_speech_fade)
        segments.extend(segs)

    if not any(s["kind"] == "speech" for s in segments):
        sys.exit("[render] FAIL: 沒有任何保留 block")
    if n_strike:
        print(f"[render] 字級精剪: {n_strike} 處刪除線")
    if n_pause:
        print(f"[render] 停頓收緊: {n_pause} 處 >{args.max_pause}s")
    if manual_cuts:
        print(f"[render] ✂ 手動剪除: {len(manual_cuts)} 段標記,實際剪掉 "
              f"{manual_secs:.2f}s")
        if manual_secs < 0.05:
            print("[render] ⚠ ✂ 標記一秒都沒剪到 — 區間是不是落在已被剪掉的"
                  "範圍、或時間碼寫成成品時間軸了?(✂ 吃的是原始錄音時間軸)")
    if abs(args.tempo - 1.0) > 1e-6:
        print(f"[render] 語速 {args.tempo}x(只套語音,配樂原速)")

    if args.dump_ranges:
        args.dump_ranges.write_text(json.dumps(
            [[round(s["a"], 3), round(s["b"], 3)]
             for s in segments if s["kind"] == "speech"],
            ensure_ascii=False), encoding="utf-8")

    speech_secs = sum(s["b"] - s["a"] for s in segments if s["kind"] == "speech")
    n_clip = sum(1 for s in segments if s["kind"] == "speech" and s.get("clip"))
    total_src = cp["blocks"][-1]["end"] if cp["blocks"] else 0
    print(f"[render] {len(segments)} segments(集錦 {n_clip} 段)+ 疊接音樂 "
          f"{len(musics)} 首;語音 {fmt_mmss(speech_secs)}"
          f"(原始 {fmt_mmss(total_src)})")
    if args.dry_run:
        for s in segments:
            if s["kind"] == "speech":
                tag = " [🎬]" if s.get("clip") else ""
                fades = "".join(f" in={s['fade_in']}s" if k == "fade_in"
                                else f" out={s['fade_out']}s"
                                for k in ("fade_in", "fade_out") if s.get(k))
                print(f"  speech {fmt_mmss(s['a'])}–{fmt_mmss(s['b'])}{tag}{fades}")
            else:
                print(f"  silence {s['dur']:.1f}s")
        for m in musics:
            anchor = ("片尾" if m["anchor_ui"] is None
                      else f"gap@unit{m['anchor_ui']}")
            env = " → ".join(f"{t:.1f}s:{v * 100:.0f}%" for t, v in m["env"])
            print(f"  music  {m['path'].name}({fmt_mmss(m['dur'])},{anchor},"
                  f"lead {m['lead']}s)\n         env {env}")
        return

    for m in musics:
        m["anchor_seg"] = (None if m["anchor_ui"] is None
                           else unit_first_seg[m["anchor_ui"]])

    src = next(p for p in sorted(sdir.glob("source.*"))
               if p.suffix.lower() not in (".srt", ".md", ".json", ".txt"))
    out = sdir / args.out
    dst_starts, final_dur = run_ffmpeg(src, segments, musics, out, args.fade,
                                       args.loudnorm or None, args.crossfade,
                                       args.dynaudnorm or None, args.tempo)

    cut_map = [{"src_start": round(s["a"], 3), "src_end": round(s["b"], 3),
                "dst_start": round(d, 3)}
               for s, d in zip(segments, dst_starts) if s["kind"] == "speech"]
    music_map = [{"file": m["path"].name, "dst_start": round(m["at"], 3),
                  "dur": round(m["dur"], 3)} for m in musics]

    chap_lines = []
    for ch in chapters:
        seg_i = unit_first_seg.get(ch["anchor"])
        if seg_i is not None and seg_i < len(dst_starts):
            chap_lines.append(
                f"{sec_to_ts(dst_starts[seg_i]).replace(',', '.')} {ch['title']}")

    (sdir / "cut_map.json").write_text(json.dumps({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "final_duration_secs": round(final_dur, 3),
        "speech_secs": round(speech_secs, 3),
        "tempo": args.tempo,   # dst→src 反查要乘回去(語音已加速,音樂沒有)
        "ranges": cut_map,
        "music": music_map,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if chap_lines:
        (sdir / "chapters.txt").write_text("\n".join(chap_lines) + "\n",
                                           encoding="utf-8")
        print(f"[render] chapters.txt: {len(chap_lines)} 章")
    print(f"[render] ✅ {out.name}({fmt_mmss(final_dur)})+ cut_map.json")


if __name__ == "__main__":
    main()
