#!/usr/bin/env python3
"""
scripts/audio/tracks_columns.py — `## ⚙ line=mixdown audio=tracks` 的軌欄

    python3 scripts/audio/tracks_columns.py --session sessions/<slug> [--dry-run]

ADR-2026-10-03-audio-tracks-line(MM 2026-10-03 拍板):
「大家的總錄音時間都是固定，可以先用合軌去切片段，但是實際上會用分軌去剪音訊。
分軌的時候每一軌都是一欄。」

    決定層 = 合軌節目單(B####/G/刪除線/✂/停頓/章節/🎵/➕ 全照 line=mixdown)
    音源   = session tracks/ 的分軌(與 source.wav 等長、sample-aligned)
    軌欄   = 每個 B/G 列行尾一欄 ` ⟦Mars● Sarah○ Kin○⟧`
             列勾選 = 留這段時間(語意不變);● = 這段這一軌出聲,○ = 壓 −27dB

本腳本把軌欄加到既有 cutplan.md(並在 ⚙ 行補 audio=tracks)。冪等:已經有軌欄
的列一律不動(人工改過的欄位與勾選都不會被覆蓋),再跑一次輸出逐 byte 相同。

預設值:B 列 = 該 block 時間內**校準後**最大聲的那一軌開、其餘關;G 列三軌全開。
校準:各軌底噪/增益不同,原始 RMS 直接比會讓「麥增益大的那支」的串音贏過真正
在講話的人。每軌量自己的底噪(P10)與自己講話的電平(P97),block 電平換算成
「在自己動態範圍裡的位置」再比(同 pertrack_attrib 的各軌底噪閘思路)。

render 端用到的純函式(軌欄解析、hold/lead 開關區間、bus 包絡)也住這裡,
render_cut.py import 它們 —— 格式只有一份定義。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_paths import work_dir  # noqa: E402

ON, OFF = "●", "○"
# 行尾軌欄。用 ⟦⟧ 這組不會出現在逐字稿裡的括號,錨在行尾:
# 文字驗證、` ← 理由`、speaker 前綴的切法都不受影響(先切軌欄再做原本的事)。
COL_RE = re.compile(r"\s*⟦([^⟦⟧]*)⟧\s*$")
CELL_RE = re.compile(rf"^(\S+?)([{ON}{OFF}])$")
ROW_RE = re.compile(r"^- \[( |x|X)\] ([A-Z]{1,2}\d{3,5}) \[([^\]]+)\] (.*)$")
CONFIG_RE = re.compile(r"^##\s*⚙️?\s*(.*)$")
INSERT_ID_RE = re.compile(r"^S\d{3,5}$")
AUDIO_EXTS = (".wav", ".flac")


class ColumnError(ValueError):
    """軌欄格式或節目單狀態不允許加軌欄。"""


# ── 格式 ────────────────────────────────────────────────────────────────
def split_col(body: str) -> tuple[str, str | None]:
    """`正文 ← 理由 ⟦Mars● …⟧` → (`正文 ← 理由`, `Mars● …`);沒有軌欄回 (body, None)。"""
    m = COL_RE.search(body)
    if not m:
        return body, None
    return body[:m.start()], m.group(1)


def parse_col(inner: str, names: list[str] | None = None) -> dict[str, bool]:
    """`Mars● Sarah○ Kin○` → {Mars:True, Sarah:False, Kin:False}(依欄序)。

    names 給了就必須**完全相同、同順序**——少一軌、多一軌、拼錯、換順序都
    FAIL,不猜。"""
    out: dict[str, bool] = {}
    for tok in inner.split():
        m = CELL_RE.match(tok)
        if not m:
            raise ColumnError(f"軌欄格子「{tok}」看不懂(要像 Mars● 或 Kin○)")
        name, mark = m.group(1), m.group(2)
        if name in out:
            raise ColumnError(f"軌欄重複出現「{name}」")
        out[name] = mark == ON
    if not out:
        raise ColumnError("軌欄是空的")
    if names is not None and list(out) != list(names):
        raise ColumnError(f"軌欄的軌「{' '.join(out)}」與 tracks/ 的"
                          f"「{' '.join(names)}」不符(要同名同順序)")
    return out


def format_col(states: dict[str, bool]) -> str:
    return " ⟦" + " ".join(f"{n}{ON if v else OFF}"
                           for n, v in states.items()) + "⟧"


def track_name(path: Path) -> str:
    """`tracks/1_Mars.WAV` → `Mars`(去掉數字前綴;沒有前綴就用 stem)。"""
    return re.sub(r"^\d+_", "", Path(path).stem)


def track_files(sdir: Path) -> list[tuple[str, Path]]:
    tdir = Path(sdir) / "tracks"
    if not tdir.is_dir():
        return []
    return [(track_name(p), p) for p in sorted(tdir.iterdir())
            if p.suffix.lower() in AUDIO_EXTS]


# 分軌與 source.wav 的長度容差(秒)。規格是「等長、sample-aligned」,錄音機
# 同一次錄製的多軌實測逐樣本等長(EP22 三軌與合軌都是 3042.915556s);容差
# 只留給容器/編碼的尾端取整(mp3/flac 轉檔常差幾個 frame),20ms 遠小於任何
# 一個字,超過就代表不是同一次錄音或被裁過——那樣切出來每個剪點都會偏,FAIL。
TRACK_LEN_TOL = 0.02


# ── 對齊(驗收 F-4)──────────────────────────────────────────────────────
# 每一軌對 source.wav 量位移:FFT 互相關,取幾個短窗、只採該軌在 source 裡
# 佔比夠高(相關係數 ≥ ALIGN_MIN_RHO)的窗,取中位數(單窗串音/雜訊不會帶偏)。
# 三軌彼此的位移 = 各自對 source 位移的差。**不直接拿麥對麥做互相關**:兩支麥
# 之間的串音帶著聲波傳遞的物理延遲(約 3ms/公尺),會被誤判成檔案沒對齊;
# source(錄音機合軌)直接含每一軌,是乾淨的共同參考。
#
# 門檻:
#   ALIGN_SRC_MAX=20ms —— 分軌對錄音機合軌本來就有固定位移(EP16 4.97、EP18
#     4.88ms,錄音機內部處理延遲),render 的 auto offset 會補,所以不能要求 0;
#     20ms 給那個已知延遲 4 倍餘裕,又遠低於「不同次錄音/被裁過」常見的幾十 ms
#     以上。超過就 FAIL:那已經不是延遲,是素材不對。
#   ALIGN_SPREAD_MAX=2ms —— 同一台錄音機的多軌彼此 sample-aligned,實測差異只有
#     互相關的量測抖動(EP22 0.5ms 量級)。三軌各差幾 ms 以上混在一起,串音會互相
#     疊出梳狀濾波/假回音,auto offset 雖然逐軌補得回來,但那代表素材被動過,FAIL
#     讓人去查,不默默修。
#   ALIGN_MAX_LAG=60ms —— 搜尋範圍,比 SRC 門檻大,量到邊界就一定超門檻。
ALIGN_SRC_MAX = 0.020
ALIGN_SPREAD_MAX = 0.002
ALIGN_MAX_LAG = 0.060
# 取窗:均勻 80 個 0.5 秒窗(EP22 50 分鐘只讀約 40 秒,不讀整條)。
# 只採信「這一軌在 source 裡佔主導」的窗(相關係數 ≥0.8):EP22 實測,相關 0.5–0.7
# 的窗量到的是**別人的聲音經過這支麥的串音路徑**(Mars 講話時 Sarah 麥收到的
# 延遲版),lag 散在 +4~+40ms;門檻拉到 0.8 之後三軌全部落在 −0.07~−0.09ms。
# (舊的 pertrack_render.measure_track_offset 用 0.55 門檻、6 個固定探點,EP22
# 量出 Kin +0.43ms、Sarah 沒有探點合格就默默回 0.0 —— 都是這個坑。)
#
# 2026-10-03 第三輪驗收 F-4-R1:原本 80 個窗只取 50 分鐘裡的 40 秒,窗與窗之間
# 隔幾十秒,局部錯位(某軌只在 29.4–30.1s 錯 3ms)抽不到。改成**全檔每 10 秒
# 一個 0.25 秒窗**(50 分鐘約 300 窗,仍只讀窗內樣本約 75 秒),而且不只看中位
# 數:每一軌每個採信窗的位移偏離該軌中位數 > ALIGN_SPREAD_MAX 就 FAIL 並印時間點。
# 窗縮到 0.25 秒:錯位段若只蓋到窗的一部分,相關係數會被未錯位的那一截拉低到
# 0.8 以下而被丟掉;窗短一點,落在錯位段裡的窗才會整窗都是錯位內容。
# **抓不到的**:錯位段落在兩個窗之間(短於約 10 秒且剛好沒蓋到窗),或錯位段
# 裡這一軌沒有主導(相關 <0.8)。同一台錄音機的多軌共用時脈,實務上不會局部
# 漂移;會出事的是人工剪接/轉檔掉段,通常長於 10 秒或會反映在長度上。
ALIGN_STEP = 10.0           # 長檔:每 10 秒一窗
ALIGN_MIN_WINDOWS = 80      # 短檔:至少約 80 窗(步距 = 可用長度/80,< 10 秒)
ALIGN_WIN_SECS = 0.25
ALIGN_MIN_RHO = 0.8
ALIGN_MIN_HITS = 3          # 至少 3 個窗採信才算量得到(取中位數)


def alignment_windows(dur: float) -> tuple[list[float], float]:
    """(各窗起點秒數, 窗長)。格點步距 = min(10 秒, 可用長度/80),窗中心在
    步距的整數倍(長檔即 10,20,30…秒);前後各留 ALIGN_MAX_LAG 給 lag 搜尋。
    短檔(< ~13 分鐘)窗會比 10 秒密 —— 太稀的話每一軌湊不到 3 個主導窗。"""
    L = ALIGN_MAX_LAG
    w = ALIGN_WIN_SECS
    usable = dur - 2 * L
    if usable < w:
        return [], w
    step = min(ALIGN_STEP, usable / ALIGN_MIN_WINDOWS)
    starts = []
    k = 1
    while k * step + w / 2 + L <= dur:
        if k * step - w / 2 >= L:
            starts.append(k * step - w / 2)
        k += 1
    return starts, w


def window_rho(s, track: Path, sr: int, a: float, w: float):
    """source 窗 s(起點 a 秒)對分軌的正規化互相關,lag −L..+L;讀不到回 None。"""
    import numpy as np
    from pertrack_render import _read_mono
    L = int(round(ALIGN_MAX_LAG * sr))
    n = len(s)
    # 多讀 4 個樣本再切:秒數→樣本的取整可能讓讀回來少 1 個,少了就整窗被跳過
    # (0.25 秒窗在 EP22 上 304 窗全被跳過,實踩)
    t = _read_mono(track, sr, a - L / sr, a + w + (L + 4) / sr)[:n + 2 * L]
    if len(t) < n + 2 * L or not s.any():
        return None
    m = 1 << int(np.ceil(np.log2(n + 2 * L + n)))
    c = np.fft.irfft(np.fft.rfft(t, m) * np.conj(np.fft.rfft(s, m)), m)
    c = c[:2 * L + 1]                             # c[k] = Σ s[i]·t[i+k]
    cs = np.concatenate([[0.0], np.cumsum(t ** 2)])
    et = cs[n:n + 2 * L + 1] - cs[:2 * L + 1]      # 每個 lag 對到的分軌能量
    return c / np.sqrt(np.maximum(et * float((s ** 2).sum()), 1e-20))


# 局部錯位要「鄰窗確認」:可疑窗前後 ±0.3 秒各再量一次,至少一個量到同一個位移
# (±0.5ms)且相關 ≥ALIGN_MIN_RHO 才算。EP22 實測剩下的 3 個可疑窗在中位數位移處
# 仍有 0.75–0.80 的相關(語音基頻週期的歧義,−6~−15ms),只出現在那一個窗;
# 真的錯位段 ≥約 0.55 秒就會延伸到鄰窗。代價:< 0.55 秒的錯位抓不到。
ALIGN_CONFIRM_DT = 0.3
ALIGN_CONFIRM_TOL = 0.0005


def confirm_local_offset(source: Path, track: Path, sr: int, t: float,
                         lag: float, w: float) -> bool:
    import numpy as np
    from pertrack_render import _read_mono
    L = int(round(ALIGN_MAX_LAG * sr))
    for dt in (-ALIGN_CONFIRM_DT, ALIGN_CONFIRM_DT):
        a = t + dt - w / 2
        if a - ALIGN_MAX_LAG < 0:
            continue
        rho = window_rho(_read_mono(source, sr, a, a + w), track, sr, a, w)
        if rho is None:
            continue
        k = int(np.argmax(rho))
        if rho[k] >= ALIGN_MIN_RHO and abs((k - L) / sr - lag) <= ALIGN_CONFIRM_TOL:
            return True
    return False


def measure_alignment(tracks: list[tuple[str, Path]], source: Path, sr: int,
                      dur: float, detail: dict | None = None
                      ) -> dict[str, float | None]:
    """{軌: 對 source 的位移秒數(正=分軌內容比較晚;讀分軌時 +這個值)|None}。

    None = 量不到(採信的窗 < ALIGN_MIN_HITS)。符號與 render 的 track_offset
    相同:mix_ranges 讀分軌用 `來源時間 + offset`。"""
    import numpy as np
    from pertrack_render import _read_mono

    L = int(round(ALIGN_MAX_LAG * sr))
    starts, w = alignment_windows(dur)
    if not starts:
        return {n: None for n, _p in tracks}
    out: dict[str, float | None] = {}
    acc: dict[str, list] = {n: [] for n, _p in tracks}   # (時間, lag, 相關曲線)
    names = [n for n, _p in tracks]
    rows = []                     # 每窗:{軌: (該軌窗內 dB, lag, ρ峰, ρ曲線)}
    for a in starts:
        s = _read_mono(source, sr, a, a + w)
        if not s.any():
            continue
        row = {}
        for name, p in tracks:
            rho = window_rho(s, p, sr, a, w)
            if rho is None:
                continue
            x = _read_mono(p, sr, a, a + w)
            lvl = 10 * np.log10(float((x ** 2).mean()) + 1e-20)
            k = int(np.argmax(rho))
            row[name] = (lvl, k - L, float(rho[k]), rho)
        rows.append((a, row))
    # 一個窗只採信「正在講話的那一軌」:以各軌**自己**的動態範圍(窗電平的
    # P10 底噪 → P97 講話電平,同 pick_loudest)判誰在講,不是比相關係數 ——
    # 相關係數與音量無關,安靜的麥只收到別人的串音也能到 0.88(EP22 1953.2s:
    # Mars 在講、ρ0.85;Sarah 麥收到他的聲音晚 17.9ms、ρ0.88,比主講者還高)
    lv = {n: [r[n][0] for _a, r in rows if n in r] for n in names}
    cal = {n: (float(np.percentile(v, 10)), float(np.percentile(v, 97)))
           for n, v in lv.items() if v}
    for a, row in rows:
        cand = [(n, (row[n][0] - cal[n][0]) / max(cal[n][1] - cal[n][0], 1e-6))
                for n in row if n in cal]
        if not cand:
            continue
        who = max(cand, key=lambda x: x[1])[0]
        _lvl, lag, rk, curve = row[who]
        if rk >= ALIGN_MIN_RHO:
            acc[who].append((a + w / 2, lag, curve))
    for name, _p in tracks:
        times = [x[0] for x in acc[name]]
        lags = [x[1] for x in acc[name]]
        curves = [x[2] for x in acc[name]]
        med = float(np.median(lags)) if len(lags) >= ALIGN_MIN_HITS else None
        out[name] = med / sr if med is not None else None
        if detail is not None:
            # (時間, 該窗位移, 該窗峰值相關, 該窗在「中位數位移」處的相關)
            mi = int(round(med)) + L if med is not None else None
            detail[name] = [(t, lg / sr, float(c[lg + L]),
                             float(c[mi]) if mi is not None else None)
                            for t, lg, c in zip(times, lags, curves)]
    if detail is not None:
        detail["_windows"] = len(starts)
        detail["_w"] = w
    return out


def _probe(path: Path) -> tuple[int | None, float | None]:
    """(取樣率, 秒);讀不到回 (None, None)。"""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True).stdout
        d = json.loads(out)
        return (int(d["streams"][0]["sample_rate"]),
                float(d["format"]["duration"]))
    except Exception:
        return None, None


def validate_tracks(sdir: Path, tracks: list[tuple[str, Path]],
                    source: Path | None, tol: float = TRACK_LEN_TOL,
                    report: dict | None = None) -> list[str]:
    """audio=tracks 的分軌前提(驗收 F-2)。回傳錯誤清單,空的才可以用。

    · 軌數 = speakers.json 的講者數(缺軌、多軌都擋;不靜默補零)
    · speakers.json 有 `tracks` 對照(ingest_tracks 產的)時,軌名集合必須相同
      (diarize 產的 SPEAKER_00 這種標籤沒有對照,只比數量)
    · 每一軌取樣率 = source.wav,長度與 source 差 ≤ tol 秒
    """
    errs: list[str] = []
    names = [n for n, _p in tracks]
    sj = work_dir(Path(sdir)) / "speakers.json"
    if not sj.is_file():
        errs.append(f"找不到 speakers.json({sj})—— 無法確認講者數與分軌數一致")
    else:
        try:
            sp = json.loads(sj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            sp = None
            errs.append(f"speakers.json 讀不了:{e}")
        if sp is not None:
            n_spk = sp.get("num_speakers") or len(sp.get("speakers") or [])
            if n_spk != len(names):
                errs.append(f"講者數 {n_spk}(speakers.json)≠ 分軌數 "
                            f"{len(names)}(tracks/:{'、'.join(names) or '無'})")
            tmap = sp.get("tracks")
            if isinstance(tmap, dict) and tmap:
                want = set(tmap)
                if want != set(names):
                    miss = sorted(want - set(names))
                    extra = sorted(set(names) - want)
                    errs.append("分軌軌名與 speakers.json 的 tracks 對照不符"
                                + (f";缺 {'、'.join(miss)}" if miss else "")
                                + (f";多 {'、'.join(extra)}" if extra else ""))
    if source is None or not Path(source).exists():
        errs.append("找不到 source.wav —— 無法確認分軌與合軌等長")
        return errs
    src_sr, src_dur = _probe(Path(source))
    if src_sr is None:
        errs.append(f"量不到 {Path(source).name} 的取樣率/長度")
        return errs
    for n, p in tracks:
        sr, dur = _probe(p)
        if sr is None:
            errs.append(f"{n}:量不到 {p.name} 的取樣率/長度")
            continue
        if sr != src_sr:
            errs.append(f"{n}:取樣率 {sr}Hz ≠ source {src_sr}Hz({p.name})")
        if abs(dur - src_dur) > tol:
            errs.append(f"{n}:長度 {dur:.3f}s 與 source {src_dur:.3f}s 差 "
                        f"{abs(dur - src_dur):.3f}s > 容差 {tol}s({p.name})")
    if errs:
        return errs          # 取樣率/長度都不對時,對齊量出來也沒有意義
    detail: dict = {}
    offs = measure_alignment(tracks, Path(source), src_sr, src_dur, detail)
    got = {n: v for n, v in offs.items() if v is not None}
    spread = (max(got.values()) - min(got.values())) if len(got) > 1 else 0.0
    if report is not None:
        report["offsets"] = offs
        report["spread"] = spread
        report["windows"] = detail.get("_windows", 0)
        report["hits"] = {n: len(detail.get(n, [])) for n, _p in tracks}
    # F-4-R1:逐窗檢查——任一採信窗偏離該軌中位數 > ALIGN_SPREAD_MAX 就是局部錯位
    paths = dict(tracks)
    for n, v in got.items():
        bad = [(t, lg) for t, lg, _rp, _rm in detail.get(n, [])
               if abs(lg - v) > ALIGN_SPREAD_MAX
               and confirm_local_offset(Path(source), paths[n], src_sr, t, lg,
                                        detail.get("_w", ALIGN_WIN_SECS))]
        if bad:
            errs.append(f"{n}:局部對齊不一致 {len(bad)} 處(偏離中位數 "
                        f"{v * 1000:+.2f}ms 超過 {ALIGN_SPREAD_MAX * 1000:.0f}ms):"
                        + "、".join(f"{_mmss(t)} {lg * 1000:+.2f}ms"
                                   for t, lg in bad[:6])
                        + (" …" if len(bad) > 6 else ""))
    for n, v in offs.items():
        if v is None:
            errs.append(f"{n}:量不到與 source 的對齊(互相關 "
                        f"{detail.get('_windows', 0)} 窗"
                        f"裡相關 ≥{ALIGN_MIN_RHO} 的不到 {ALIGN_MIN_HITS} 個)—— "
                        f"這一軌可能不是同一次錄音")
        elif abs(v) > ALIGN_SRC_MAX:
            errs.append(f"{n}:與 source 對齊位移 {v * 1000:+.2f}ms 超過門檻 "
                        f"±{ALIGN_SRC_MAX * 1000:.0f}ms(不是錄音機延遲,素材不對)")
    if spread > ALIGN_SPREAD_MAX:
        errs.append("三軌彼此對齊不一致:"
                    + "、".join(f"{n} {v * 1000:+.2f}ms" for n, v in got.items())
                    + f",互差 {spread * 1000:.2f}ms > 門檻 "
                      f"{ALIGN_SPREAD_MAX * 1000:.0f}ms(多軌應 sample-aligned)")
    return errs


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def format_alignment(report: dict) -> str:
    offs = report.get("offsets") or {}
    return ("、".join(f"{n} {'量不到' if v is None else f'{v * 1000:+.2f}ms'}"
                     for n, v in offs.items())
            + f";三軌互差 {report.get('spread', 0.0) * 1000:.2f}ms")


def find_source(sdir: Path) -> Path | None:
    hits = [p for p in sorted(Path(sdir).glob("source.*"))
            if p.suffix.lower() not in (".srt", ".md", ".json", ".txt")]
    return hits[0] if hits else None


# ── 加欄位(純文字轉換)─────────────────────────────────────────────────
def _with_audio_key(cfg_line: str) -> tuple[str, bool]:
    body = CONFIG_RE.match(cfg_line.strip()).group(1)
    kv = dict(re.findall(r"([\w-]+)=([\w.-]+)", body))
    if kv.get("line", "mixdown") != "mixdown":
        raise ColumnError(f"⚙ 寫的是 line={kv['line']} —— 軌欄只給合軌決定層"
                          f"(line=mixdown)用")
    audio = kv.get("audio")
    if audio == "tracks":
        return cfg_line, False
    if audio is not None:
        raise ColumnError(f"⚙ 已經寫了 audio={audio},不自動改成 audio=tracks"
                          f"—— 要換路線請人工改 ⚙")
    return cfg_line.rstrip() + " audio=tracks", True


def add_columns(md_text: str, defaults: dict[str, dict[str, bool]],
                names: list[str]) -> tuple[str, dict]:
    """在沒有軌欄的 B/G 列行尾加軌欄、⚙ 補 audio=tracks;回傳 (新文字, 統計)。

    已有軌欄的列原樣保留(只驗格式);S 列(➕ 補錄)不分軌、不加欄。
    勾選、刪除線、理由、其他任何行一個字都不動。"""
    lines = md_text.split("\n")
    cfg_i = next((i for i, l in enumerate(lines) if CONFIG_RE.match(l.strip())),
                 None)
    if cfg_i is None:
        raise ColumnError("cutplan.md 沒有 `## ⚙` 行 —— 不知道剪輯路線,不加軌欄")
    lines[cfg_i], audio_added = _with_audio_key(lines[cfg_i])
    added = kept = 0
    for i, line in enumerate(lines):
        m = ROW_RE.match(line)
        if not m:
            continue
        bid, body = m.group(2), m.group(4)
        if INSERT_ID_RE.match(bid):
            continue
        _rest, inner = split_col(body)
        if inner is not None:
            try:
                parse_col(inner, names)
            except ColumnError as e:
                raise ColumnError(f"{bid}: {e}") from None
            kept += 1
            continue
        if bid not in defaults:
            raise ColumnError(f"{bid} 不在 cutplan.json(blocks/gaps)裡,算不出預設")
        lines[i] = line.rstrip() + format_col(
            {n: bool(defaults[bid].get(n)) for n in names})
        added += 1
    return "\n".join(lines), {"added": added, "kept": kept,
                              "audio_added": audio_added}


# ── 預設值:校準後最大聲的一軌 ─────────────────────────────────────────
def calibrate(levels_db, floor_pct: float = 10.0, ref_pct: float = 97.0):
    """每軌 (底噪, 自己講話的電平)。levels_db = 軌 × frame 的 dB 矩陣。"""
    import numpy as np
    L = np.asarray(levels_db, dtype=float)
    return ([float(np.percentile(x, floor_pct)) for x in L],
            [float(np.percentile(x, ref_pct)) for x in L])


def pick_loudest(block_db, floors, refs) -> int:
    """block 電平在各軌自己動態範圍(底噪→講話電平)裡的位置,取最高者。"""
    best, best_i = -math.inf, 0
    for i, (v, f, r) in enumerate(zip(block_db, floors, refs)):
        score = (v - f) / max(r - f, 1e-6)
        if score > best:
            best, best_i = score, i
    return best_i


def compute_defaults(sdir: Path, blocks: list[dict], gaps: list[dict],
                     tracks: list[tuple[str, Path]]) -> dict[str, dict[str, bool]]:
    import numpy as np
    from pertrack_attrib import db, integrate
    from pertrack_blocks import HOP, track_power

    names = [n for n, _p in tracks]
    pw = [track_power(p) for _n, p in tracks]
    nf = min(len(x) for x in pw)
    P = np.vstack([x[:nf] for x in pw])
    floors, refs = calibrate(db(np.vstack([integrate(x, 10) for x in P])))
    print("[tracks] 校準(底噪 / 講話電平):" + "  ".join(
        f"{n} {f:.1f}/{r:.1f}dB" for n, f, r in zip(names, floors, refs)))
    out: dict[str, dict[str, bool]] = {}
    for b in blocks:
        f0 = min(nf - 1, max(0, int(b["start"] / HOP)))
        f1 = min(nf, max(f0 + 1, int(math.ceil(b["end"] / HOP))))
        lv = db(P[:, f0:f1].mean(axis=1))
        k = pick_loudest(list(lv), floors, refs)
        out[b["id"]] = {n: i == k for i, n in enumerate(names)}
    for g in gaps:
        out[g["id"]] = {n: True for n in names}
    return out


# ── render 端:開關區間與 bus 包絡 ──────────────────────────────────────
def on_intervals(items: list[dict], name: str, lead: float) -> list[list[float]]:
    """某一軌在來源時間軸上「開」的區間(同一個 unit 的保留 block 依序)。

    items = [{start, end, on:{軌:bool}}],依 start 排序。
    · hold:block 之間的空隙沿用前一個 block 的狀態(開著的軌開到下一個 block
      起點才關;避免底噪在空隙裡抽動)
    · lead:這一軌在下一個 block 才「新開」時,提早 lead 秒開 —— SRT 起點常
      比真正的字頭晚,不提早會吃掉字頭;但不早於前一個 block 的結尾
    · 重疊講話:前一個 block 的軌開到它自己的結尾(取聯集,不搶)
    · 第一個 block 之前、最後一個之後延伸到 ±inf(unit 邊界外推的那一點點)
    """
    items = sorted(items, key=lambda it: it["start"])
    out: list[list[float]] = []
    prev_end = -math.inf
    for k, it in enumerate(items):
        if it["on"].get(name):
            if k == 0:
                lo = -math.inf
            else:
                lo = it["start"] if prev_end >= it["start"] else \
                    max(prev_end, it["start"] - lead)
            hi = (max(items[k + 1]["start"], it["end"]) if k + 1 < len(items)
                  else math.inf)
            if out and lo <= out[-1][1] + 1e-9:
                out[-1][1] = max(out[-1][1], hi)
            else:
                out.append([lo, hi])
        prev_end = max(prev_end, it["end"])
    return out


def segment_envelopes(segments: list[dict], names: list[str], duck_db: float,
                      lead: float, sr: int) -> dict[str, list[tuple]]:
    """每個 speech segment(來源時間 a–b,帶 titems)→ 各軌 bus 時間軸包絡。

    bus 位移用取樣量化(round(b·sr)−round(a·sr)),跟 mix_ranges 寫出的
    樣本數同一套算法(同 pertrack_cells.track_envelopes 的理由)。
    回傳 {軌: [(bus_a, bus_b, gain_db), ...]},相鄰同增益合併。"""
    out: dict[str, list[tuple]] = {n: [] for n in names}
    off = 0.0
    for s in segments:
        a, b = s["a"], s["b"]
        for n in names:
            cuts = [a, b]
            ivs = on_intervals(s["titems"], n, lead)
            for lo, hi in ivs:
                cuts += [t for t in (lo, hi) if a < t < b]
            cuts = sorted(set(cuts))
            for x, y in zip(cuts, cuts[1:]):
                mid = (x + y) / 2
                g = 0.0 if any(lo <= mid < hi for lo, hi in ivs) else duck_db
                ba, bb = round(off + x - a, 6), round(off + y - a, 6)
                if out[n] and abs(out[n][-1][1] - ba) < 1e-6 \
                        and out[n][-1][2] == g:
                    out[n][-1] = (out[n][-1][0], bb, g)
                else:
                    out[n].append((ba, bb, g))
        off += (int(round(b * sr)) - int(round(a * sr))) / sr
    return out


# ── CLI ─────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="合軌節目單加分軌欄(audio=tracks)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--plan", default="cutplan.md")
    ap.add_argument("--dry-run", action="store_true", help="只印統計,不寫檔")
    args = ap.parse_args()

    sdir = Path(args.session).resolve()
    tracks = track_files(sdir)
    if not tracks:
        print(f"[tracks] FAIL: {sdir / 'tracks'} 沒有分軌音檔(.wav/.flac)",
              file=sys.stderr)
        return 2
    names = [n for n, _p in tracks]
    if len(set(names)) != len(names):
        print(f"[tracks] FAIL: 軌名重複:{names}", file=sys.stderr)
        return 2
    info: dict = {}
    errs = validate_tracks(sdir, tracks, find_source(sdir), report=info)
    if info:
        print("[tracks] 分軌對齊(相對 source.wav):" + format_alignment(info))
    if errs:
        print("[tracks] FAIL: 分軌前提不成立 ——\n  " + "\n  ".join(errs),
              file=sys.stderr)
        return 2
    plan = work_dir(sdir) / args.plan
    cp = json.loads((work_dir(sdir) / "cutplan.json").read_text(encoding="utf-8"))
    text = plan.read_text(encoding="utf-8")

    # 只替「還沒有軌欄」的列算預設(冪等:第二次跑什麼都不量)
    need = set()
    for line in text.split("\n"):
        m = ROW_RE.match(line)
        if m and not INSERT_ID_RE.match(m.group(2)) \
                and split_col(m.group(4))[1] is None:
            need.add(m.group(2))
    defaults: dict[str, dict[str, bool]] = {}
    if need:
        blocks = [b for b in cp["blocks"] if b["id"] in need]
        gaps = [g for g in cp.get("gaps", []) if g["id"] in need]
        print(f"[tracks] {len(tracks)} 軌:" + "、".join(names)
              + f";量 {len(blocks)} 個 block 的電平 …")
        defaults = compute_defaults(sdir, blocks, gaps, tracks)
    try:
        new, st = add_columns(text, defaults, names)
    except ColumnError as e:
        print(f"[tracks] FAIL: {e}", file=sys.stderr)
        return 2

    one = multi = none = 0
    for line in new.split("\n"):
        m = ROW_RE.match(line)
        if not m or not m.group(2).startswith("B"):
            continue
        inner = split_col(m.group(4))[1]
        if inner is None:
            continue
        k = sum(parse_col(inner).values())
        one += k == 1
        multi += k > 1
        none += k == 0
    print(f"[tracks] 新加軌欄 {st['added']} 列、保留既有 {st['kept']} 列"
          + (";⚙ 補上 audio=tracks" if st["audio_added"] else ""))
    print(f"[tracks] B 列:只開一軌 {one}、開兩軌以上 {multi}、全關 {none}")
    if args.dry_run:
        print("[tracks] --dry-run:不寫檔")
        return 0
    if new != text:
        plan.write_text(new, encoding="utf-8")
        print(f"[tracks] 寫回 {plan}")
    else:
        print("[tracks] 沒有變動(已經加過)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
