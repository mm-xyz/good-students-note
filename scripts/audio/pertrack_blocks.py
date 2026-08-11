#!/usr/bin/env python3
"""
scripts/audio/pertrack_blocks.py — 逐軌逐字稿 → 逐軌 cutplan blocks（分軌剪輯）

    python3 scripts/audio/pertrack_blocks.py --session sessions/<slug> \
        [--bc-excess 10.0] [--backchannel-min 0.3] [--out-md cutplan.pertrack.md]

2026-08-11 MM 拍板改成分軌出片(方案 B:完整逐軌模型)。動機:EP16 04:47 有一串
KIN 的「嗯 嗯 嗯」壓在 Mars 講話底下,混音逐字稿看不到、人審剪不掉。

**為什麼不能只靠逐軌轉錄**:麥克風串音 17–23dB,whisper 對三軌都會把同一段話
轉出來(EP16 實測:Mars 軌只有 -58 LUFS 的串音,照樣轉出完整句子)。

**為什麼不能用「誰最大聲」判定**:兩人同時出聲時,小聲的那個會被當成串音丟掉——
而附和聲正是這種情況(EP16 313-322s:Mars 比 KIN 大 11.8dB,純支配判定把 KIN 的
「嗯嗯」全判給 Mars)。改用**串音校準**:先從「某人獨講」的乾淨片段量出每一對
軌的串音增益 g[i][j],再問「這一軌的能量有沒有超出純串音能解釋的量」。
EP16 實測 Mars→KIN 串音 -16.4dB,校準後在該區間抓到 5 段 KIN 自己的出聲
(超出預測 15-34dB),正是 MM 聽到的那串附和。

**為什麼附和不會有逐字稿**:whisper 不轉「嗯」這種非詞彙音(EP16 KIN 軌那區間
轉出來的全是 Mars 的串音內容)。所以附和只能靠 VAD 能量抓,抓到後以無文字的
`(附和/雜音 N.Ns)` 列呈現,**預設不勾**=該軌該區間靜音;要保留就勾回來。

輸出:
    cutplan.json 的 `tracks`: [{speaker, prefix, file, blocks:[...]}]
    cutplan.pertrack.md: 依時間排序、換講者就下一個 `## 軌 <Speaker>` 標頭

兩層剪輯模型(render 端實作):
    時間軸層 — 某區間三軌全部沒勾 → 整段移除(時間消失,三軌一起)
    軌  層 — 有人勾有人沒勾 → 時間保留,沒勾的那一軌在該區間靜音
"""

import argparse
import array
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, fmt_mmss, split_words_to_phrases  # noqa: E402

ARTIFACT_MIN_RUN = 4     # 同一個字連續重複幾次算 whisper 重複迴圈
ENV_RATE = 1000          # 能量包絡取樣率:33 分鐘只有 2M 點,夠準又夠快
STEP = 0.2               # 掃描步長(秒);附和聲常只有 0.3–0.8s
TRACK_PREFIX = {"Mars": "M", "Sarah": "S", "KIN": "K"}


def envelope(path: Path) -> list[float]:
    """整軌解成 1kHz 單聲道振幅包絡(abs 值),供逐窗能量比較。"""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
         "-ar", str(ENV_RATE), "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    a = array.array("f")
    a.frombytes(raw)
    return [abs(x) for x in a]


def is_artifact(text: str) -> bool:
    """whisper 重複迴圈/亂碼 artifact 偵測。

    2026-08-11 實踩:Mars 軌 309-314s 的 words.json 是「嘗」×40 + U+FFFD。
    原本埋在一個長 cue 裡看不見,block 細切後被攤成一整排垃圾 block。
    混音線早有同款守門(render_cut 丟棄 >3s 的異常長 word,EP16「反而」×N)。
    """
    t = text.strip()
    if not t:
        return True
    if "\ufffd" in t:                       # 解碼失敗的替代字元
        return True
    run = best = 1
    for a, b in zip(t, t[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    if best >= ARTIFACT_MIN_RUN:
        return True
    # 整句只由 1-2 種字元組成且夠長(「嘗嘗嘗」「反而反而反而」型)
    return len(t) >= 6 and len(set(t)) <= 2


def rms_db(env: list[float], a: float, b: float) -> float:
    i, j = int(a * ENV_RATE), int(b * ENV_RATE)
    seg = env[max(0, i):max(i + 1, j)]
    if not seg:
        return -120.0
    m = math.sqrt(sum(x * x for x in seg) / len(seg))
    return 20 * math.log10(m) if m > 1e-9 else -120.0


def calibrate_bleed(tracks: list[dict], dur: float,
                    dominance: float = 12.0) -> dict:
    """量每一對軌的串音增益 g[i][j] ≈「j 講話時,i 軌會收到多少」(dB,負值)。

    取樣窗只挑「j 明顯獨大(領先第二名 ≥dominance dB)」的時刻——那種時刻 i 軌
    收到的幾乎純粹是串音。再取這些差值的**第 30 百分位**當估計值:i 自己也在
    出聲的窗會把差值往上拉,取低分位數才抓得到「i 安靜時」的真實串音底線。
    """
    n = len(tracks)
    samples = {(i, j): [] for i in range(n) for j in range(n) if i != j}
    t = 0.0
    while t + STEP <= dur:
        lv = [rms_db(tk["env"], t, t + STEP) for tk in tracks]
        order = sorted(range(n), key=lambda k: lv[k], reverse=True)
        j, second = order[0], order[1]
        if lv[j] > -60 and lv[j] - lv[second] >= dominance:
            for i in range(n):
                if i != j:
                    samples[(i, j)].append(lv[i] - lv[j])
        t += STEP
    g = {}
    for (i, j), xs in samples.items():
        if len(xs) < 20:
            g[(i, j)] = -12.0     # 樣本太少 → 保守估一個偏大的串音,寧可少抓
        else:
            xs.sort()
            g[(i, j)] = xs[int(len(xs) * 0.30)]
    return g


def excess_db(tracks: list[dict], g: dict, i: int, a: float, b: float) -> float:
    """track i 在 [a,b] 的能量,超出「純串音能解釋的量」多少 dB。"""
    mine = rms_db(tracks[i]["env"], a, b)
    pred = max((rms_db(tracks[j]["env"], a, b) + g[(i, j)]
                for j in range(len(tracks)) if j != i), default=-120.0)
    return mine - pred


def main() -> int:
    ap = argparse.ArgumentParser(description="逐軌逐字稿 → 逐軌 cutplan blocks")
    ap.add_argument("--session", required=True)
    ap.add_argument("--max-secs", type=float, default=1.2,
                    help="block 長度上限(秒,0=不強制切)。2026-08-11 MM 定粒度:"
                         "「每秒一個 block 可以被勾選,block 內字級精簡發揮作用」。"
                         "超過就在最大字間空隙再對切,切點永遠落在字與字之間。"
                         "細切還有第二個好處:口頭禪更常自成一個 block,"
                         "「整句就是它」那條自動劃線規則(70%% 精確度)涵蓋率跟著上升")
    ap.add_argument("--bc-excess", type=float, default=10.0,
                    help="附和列的門檻要更嚴(預設 10.0dB)——6dB 會把呼吸/椅子/"
                         "環境音全抓進來(EP16 實測 1840 段,cutplan 沒法看)")
    ap.add_argument("--bc-floor", type=float, default=-45.0,
                    help="附和列的絕對音量下限(dB),濾掉呼吸與細碎雜音")
    ap.add_argument("--backchannel-min", type=float, default=0.3,
                    help="無文字的出聲段要多長才成一列(秒,預設 0.3)")
    ap.add_argument("--out-md", default="cutplan.pertrack.md")
    args = ap.parse_args()

    sdir = Path(args.session)
    pt = sdir / "pertrack"
    if not pt.is_dir():
        print(f"[pertrack] ✗ 找不到 {pt} — 先對每一軌跑 transcribe_local.py",
              file=sys.stderr)
        return 2
    srts = sorted(pt.glob("*.srt"))
    if not srts:
        print(f"[pertrack] ✗ {pt} 裡沒有逐軌 SRT", file=sys.stderr)
        return 2

    tracks = []
    for s in srts:
        speaker = s.stem.split("_", 1)[1] if "_" in s.stem else s.stem
        wav = sdir / "tracks" / f"{s.stem}.WAV"
        if not wav.exists():
            print(f"[pertrack] ✗ 對不到音軌:{wav}", file=sys.stderr)
            return 2
        tracks.append({"speaker": speaker, "srt": s, "wav": wav,
                       "prefix": TRACK_PREFIX.get(speaker, speaker[:1].upper())})

    print(f"[pertrack] 解 {len(tracks)} 軌能量包絡({ENV_RATE}Hz)…")
    for t in tracks:
        t["env"] = envelope(t["wav"])
        cues = parse_srt(t["srt"])
        wj = t["srt"].with_suffix(".words.json")
        if args.max_secs > 0 and wj.exists():
            words = json.loads(wj.read_text(encoding="utf-8"))
            fine = []
            for c in cues:
                ws = [w for w in words
                      if w["end"] > c["start"] - 1e-6 and w["start"] < c["end"] + 1e-6]
                parts = split_words_to_phrases(ws, c["text"], max_secs=args.max_secs)
                fine.extend(parts if parts else [c])
            print(f"    {t['speaker']:6s} {len(cues)} cues → 細切 {len(fine)}"
                  f"(上限 {args.max_secs}s)")
            cues = fine
        else:
            print(f"    {t['speaker']:6s} {len(cues)} cues")
        t["cues"] = cues
    dur = min(len(t["env"]) for t in tracks) / ENV_RATE

    g = calibrate_bleed(tracks, dur)
    print("[pertrack] 串音校準(收到方 ← 講話方):")
    for i, ti in enumerate(tracks):
        row = "  ".join(f"{tracks[j]['speaker']}:{g[(i, j)]:+.1f}dB"
                        for j in range(len(tracks)) if j != i)
        print(f"    {ti['speaker']:6s} ← {row}")

    # ── 有文字的 block:**波形支配**判定(2026-08-11 MM:「用波形看這段聲音
    #    主要在誰那,就用誰的為主」)。文字歸屬與附和偵測是兩件事,要用兩個判準:
    #      文字歸屬 → 支配(誰大聲就是誰的話);串音轉出來的重複句自然被排除
    #      附和偵測 → 串音校準的 excess(見下一段);小聲但真的有出聲照樣抓得到
    #    先前把兩者都換成 excess,結果 KIN 附和時他的軌通過檢定,連帶把 Mars
    #    串音轉出來的文字也算成 KIN 的話(K0054-K0057)。分開用就對了。 ──
    n_art = 0
    for i, t in enumerate(tracks):
        kept = []
        for c in t["cues"]:
            lv = [(rms_db(o["env"], c["start"], c["end"]), j)
                  for j, o in enumerate(tracks)]
            lv.sort(reverse=True)
            if lv[0][1] != i:
                continue                      # 這段主要不在他那,串音而已
            if is_artifact(c["text"]):
                n_art += 1
                continue
            kept.append({"start": round(c["start"], 3), "end": round(c["end"], 3),
                         "text": c["text"].strip(), "kind": "speech",
                         "excess_db": round(lv[0][0] - lv[1][0], 1)})
        t["kept"] = kept
        print(f"[pertrack] {t['speaker']:6s} {len(t['cues'])} 句 → 自己的 "
              f"{len(kept)} 句(判為串音 {len(t['cues']) - len(kept)})")

    if n_art:
        print(f"[pertrack] ⚠ 丟棄 {n_art} 個 whisper 重複迴圈/亂碼 block"
              f"(如 Mars 軌 5:09-5:14 的「嘗」×40)")

    # ── 無文字的出聲段=附和/雜音:掃全軌,扣掉已被文字 block 覆蓋的部分 ──
    # 只收「別人正在講話時」的出聲——那才是壓在別人話底下、人審剪不掉的附和。
    # 沒人講話時的獨立出聲要嘛是他自己的話(whisper 漏轉)、要嘛是雜音,不在本
    # 功能範圍;全都列出來只會把 cutplan 洗版(EP16 實測 1840 段)。
    others_speech = {}
    for i, t in enumerate(tracks):
        spans = [(b["start"], b["end"]) for j, o in enumerate(tracks) if j != i
                 for b in o["kept"] if b["kind"] == "speech"]
        others_speech[i] = sorted(spans)

    for i, t in enumerate(tracks):
        covered = [(b["start"], b["end"]) for b in t["kept"]]
        runs, cur = [], None
        x = 0.0
        while x + STEP <= dur:
            hit = (excess_db(tracks, g, i, x, x + STEP) >= args.bc_excess
                   and rms_db(t["env"], x, x + STEP) >= args.bc_floor
                   and any(a <= x < b for a, b in others_speech[i]))
            if hit and not any(a <= x < b for a, b in covered):
                cur = [x, x + STEP] if cur is None else [cur[0], x + STEP]
            else:
                if cur and cur[1] - cur[0] >= args.backchannel_min:
                    runs.append(cur)
                cur = None
            x += STEP
        if cur and cur[1] - cur[0] >= args.backchannel_min:
            runs.append(cur)
        for a, b in runs:
            t["kept"].append({"start": round(a, 3), "end": round(b, 3),
                              "text": f"（附和/雜音 {b - a:.1f}s）",
                              "kind": "backchannel", "excess_db": None})
        t["kept"].sort(key=lambda z: z["start"])
        print(f"[pertrack] {t['speaker']:6s} 另有 {len(runs)} 段無文字出聲"
              f"（附和/雜音，預設不勾＝靜音）")

    for t in tracks:
        for i2, b in enumerate(t["kept"], 1):
            b["id"] = f"{t['prefix']}{i2:04d}"

    cj = sdir / "cutplan.json"
    cp = json.loads(cj.read_text(encoding="utf-8"))
    cp["tracks"] = [{"speaker": t["speaker"], "prefix": t["prefix"],
                     "file": f"tracks/{t['wav'].name}",
                     "blocks": [{k: b[k] for k in
                                 ("id", "start", "end", "text", "kind")}
                                for b in t["kept"]]} for t in tracks]
    cj.write_text(json.dumps(cp, ensure_ascii=False), encoding="utf-8")

    rows = sorted(((b, t) for t in tracks for b in t["kept"]),
                  key=lambda x: (x[0]["start"], x[0]["id"]))
    lines = [f"# Cutplan（分軌）— {sdir.name}", "",
             "> 三軌各自轉錄 → **串音校準**判定每句是誰講的（不是比誰大聲——"
             "兩人同時出聲時小聲的那個會被誤判成串音）。",
             "> `（附和/雜音）`列＝該軌有出聲但沒有逐字稿（whisper 不轉「嗯」"
             "這種非詞彙音），**預設不勾＝該軌該區間靜音**，要保留就勾回來。",
             "> **兩層剪輯**：某區間三軌全部沒勾＝整段移除（時間消失）；"
             "有人勾有人沒勾＝時間保留、沒勾的那軌靜音。",
             "> 改勾選＝剪輯；`~~刪除線~~`＝字級精剪。", ""]
    cur = None
    for b, t in rows:
        if t["speaker"] != cur:
            lines.append(f"## 軌 {t['speaker']}")
            cur = t["speaker"]
        mark = " " if b["kind"] == "backchannel" else "x"
        lines.append(f"- [{mark}] {b['id']} [{fmt_mmss(b['start'])}–"
                     f"{fmt_mmss(b['end'])}] [{t['speaker']}] {b['text']}")
    out = sdir / args.out_md
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    n_sp = sum(1 for t in tracks for b in t["kept"] if b["kind"] == "speech")
    n_bc = sum(1 for t in tracks for b in t["kept"] if b["kind"] == "backchannel")
    print(f"[pertrack] ✓ {n_sp} 句語音 ＋ {n_bc} 段附和/雜音 → {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
