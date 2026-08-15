#!/usr/bin/env python3
"""
scripts/audio/pertrack_render.py — 三軌 → speech bus(D5 混音鏈、D6 接縫)

被 render_cut.py 在「cutplan 有 tracks 區」時呼叫。做的事只有一件:把三條
sample-aligned 分軌,依 atomic cell 算出來的保留區間與逐軌增益包絡,混成一條
**已經時間剪過、已經 gate 過**的 speech bus WAV。之後的 dynaudnorm → BGM
overlay → loudnorm 全部沿用混音線既有的 run_ffmpeg,一行沒改。

D5 順序(不要每軌各自 dynaudnorm,會把靜音與串音拉起來):

    各軌 static gain / high-pass
      → 三軌套用**完全相同**的全域 atrim 時間段
      → 段內套各軌 gain envelope(KEEP 0dB / DUCK −27dB / 明確不要 −60dB)
      → 等功率 pan 混成 speech bus
      → (交回 render_cut)保守 dynaudnorm → BGM overlay → loudnorm

音色跳變:不在三軌全開的混音裡只於零星事件突然關一軌 —— 那會改變延遲串音的
相位組合。改用整集一致的 activity mask,非主要軌常態衰減而非硬關,gate 邊緣
走等功率 raised-cosine 過渡(頭尾斜率為 0,不會有 click)。

**不做串音反相消除**:相位隨距離/轉頭改變,會產生金屬聲。KIN 軌靜音後其他兩軌
仍留 17–23dB 的 KIN 串音,通常被主聲遮蔽,這是可接受的物理下限。
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pertrack_cells import TrackPlan  # noqa: E402

CHUNK_SECS = 30.0     # 讀檔/混音的分塊上限,長 range 也不會一次吃掉幾百 MB


def _amp(db_: float) -> float:
    return 10.0 ** (db_ / 20.0)


def envelope_curve(segments, sr: int, total_secs: float, fade: float = 0.015,
                   default_db: float = -27.0):
    """逐軌增益包絡 → 逐樣本振幅曲線(gate 邊緣是等功率 raised-cosine)。

    a(x) = sqrt(p0·cos²(πx/2) + p1·sin²(πx/2))
    既是等功率(cos²+sin² ≡ 1,中點功率＝兩端平均),頭尾斜率又是 0 ——
    純 sqrt 的線性功率插值在收到 0 的那一端斜率發散,最後幾個樣本會跳一大格,
    那正是「關麥 click」的來源。
    """
    n = int(round(total_secs * sr))
    cur = np.full(n, _amp(default_db), dtype=np.float64)
    for a, b, g in segments:
        i0 = max(0, int(round(a * sr)))
        i1 = min(n, int(round(b * sr)))
        if i1 > i0:
            cur[i0:i1] = _amp(g)
    edges = np.nonzero(np.diff(cur))[0] + 1
    if not len(edges) or fade <= 0:
        return cur
    h = max(1, int(round(fade * sr / 2)))
    x = np.linspace(0.0, 1.0, 2 * h + 1)
    c2, s2 = np.cos(np.pi * x / 2) ** 2, np.sin(np.pi * x / 2) ** 2
    for e in edges:
        lo, hi = e - h, e + h + 1
        if lo < 0 or hi > n:
            continue
        p0, p1 = cur[lo] ** 2, cur[hi - 1] ** 2
        cur[lo:hi] = np.sqrt(p0 * c2 + p1 * s2)
    return cur


def _read_mono(path: Path, sr: int, a: float, b: float):
    """讀 [a,b) 秒的單聲道樣本(float64,−1..1)。16-bit PCM 走 wave 快路徑。"""
    try:
        with wave.open(str(path), "rb") as f:
            if (f.getnchannels() == 1 and f.getsampwidth() == 2
                    and f.getframerate() == sr):
                i0 = max(0, int(round(a * sr)))
                n = max(0, int(round(b * sr)) - i0)
                f.setpos(min(i0, f.getnframes()))
                raw = f.readframes(n)
                x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
                if len(x) < n:
                    x = np.concatenate([x, np.zeros(n - len(x))])
                return x
    except (wave.Error, EOFError):
        pass
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{a:.6f}", "-i", str(path),
         "-t", f"{b - a:.6f}", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    n = int(round((b - a) * sr))
    return np.concatenate([x, np.zeros(max(0, n - len(x)))])[:n]


def _spread_db(x, sr: int, win: float = 0.02) -> float:
    """20ms 短窗 RMS 的「最大 vs 第 10 百分位」差 —— 窗裡有沒有事件的量尺。

    純底噪的起伏很小(<10dB);呼吸、衣物摩擦、遠處人聲會讓峰值高出十幾 dB。
    """
    k = max(1, int(win * sr))
    m = len(x) // k * k
    if m < k * 4:
        return 0.0
    r = np.sqrt((x[:m].reshape(-1, k) ** 2).mean(axis=1)) + 1e-12
    return float(20 * np.log10(r.max() / np.percentile(r, 10)))


def find_quiet_spans(tracks, dur: float, sr: int, want: int = 8,
                      seg: float = 1.2, probes: int = 400,
                      max_spread_db: float = 12.0):
    """在整集裡找最安靜**且最平坦**的幾段當 room-tone 取樣點。

    2026-08-11 EP18 事故(ADR-2026-08-11-672):原本只排「三軌能量和最小」,挑出來的
    8 段有 7 段的短窗峰谷差 15.7–25.7dB —— 全是含呼吸/衣物/微弱人聲的窗。
    那些事件被鋪成每 7.84 秒重複一次的循環,MM 實聽在 0:36–0:38 抓到。
    **能量低不等於乾淨**,挑窗必須同時看平坦度。

    **安靜與平坦兩個條件都要**,不能只換判準:連續講話在 20ms 短窗尺度上
    同樣很平坦,只看平坦度會挑到「有人穩定說話」的窗(修這支 bug 時實踩:
    底噪從 −60.9 變 −42.1dBFS,比原本更吵)。做法是先取最安靜的一小群
    候選,再在裡面挑最平坦的。

    平坦的窗不足 `want` 個時回傳較少(甚至 0)——寧可不鋪底,也不鋪一段
    帶事件的音訊;呼叫端負責 fallback。
    """
    paths = [t[1] if isinstance(t, tuple) else Path(t) for t in tracks]
    cand = []
    step = max(seg, (dur - seg) / max(1, probes))
    t = 0.0
    while t + seg <= dur:
        e = 0.0
        acc = None
        for p in paths:
            x = _read_mono(p, sr, t, t + seg)
            e += float((x ** 2).mean())
            acc = x if acc is None else acc + x[:len(acc)]
        cand.append((e, t, _spread_db(acc, sr)))
        t += step
    if not cand:
        return []
    cand.sort()                                   # 先按能量:最安靜的在前
    pool = cand[:max(want * 4, math.ceil(0.1 * len(cand)))]
    quiet_and_flat = [(e, t) for e, t, s in pool if s <= max_spread_db]
    return [(t, t + seg) for _e, t in quiet_and_flat[:want]]


def measure_noise_floor(tracks, spans, sr: int, win: float = 0.02,
                        pct: float = 25.0) -> float:
    """真實底噪電平(dBFS):取樣段內 win 秒短窗 RMS 的第 pct 百分位。

    取樣段本身的平均沒有用 —— 1.2 秒的窗幾乎一定含呼吸或衣物摩擦,拿它當
    鋪底電平會高出十幾 dB。要的是「窗裡最安靜的那部分」。
    """
    paths = [t[1] if isinstance(t, tuple) else Path(t) for t in tracks]
    vals = []
    w = max(1, int(win * sr))
    for a, b in spans:
        acc = None
        for p in paths:
            x = _read_mono(p, sr, a, b)
            acc = x if acc is None else acc + x[:len(acc)]
        if acc is None:
            continue
        m = len(acc) // w * w
        if m:
            vals += list(np.sqrt((acc[:m].reshape(-1, w) ** 2).mean(axis=1)))
    if not vals:
        return -90.0
    return float(20 * np.log10(max(np.percentile(vals, pct), 1e-9)))


ROOM_TONE_NFFT = 2048


def build_room_tone(tracks, spans, n: int, sr: int, seg: float = 1.0,
                    normalize_to_db: float | None = None, seed: int = 0):
    """真靜音區取樣 → **頻譜合成**長度 n 的 room-tone bed(穩態、無循環)。

    D5:每次關麥噪聲地板都抽動一下,聽起來會「呼吸」。鋪一層固定的房間底噪
    就不會 —— EP16 實測不鋪的話,全軌都被 duck 的區間會掉到 −92dBFS
    (幾乎是數位靜音),跟旁邊 −68dBFS 的開麥底噪差 24dB。

    2026-08-11 改成頻譜合成(ADR-2026-08-11-672)。**原本是把取樣段輪流交叉淡接後
    循環**,問題不在接縫而在內容:取樣段裡的呼吸/衣物/微弱人聲會跟著循環,
    EP18 實測變成每 7.84 秒重複一次的鬼影(自相關 0.77、內部峰谷差 23.5dB),
    整集 20 分鐘每輪逼人聽一次,MM 一分鐘就聽不下去。

    做法:量取樣段的平均幅度譜(這是房間的音色),用**隨機相位**重新合成
    ——音色一模一樣,但沒有任何事件、沒有任何週期。相位隨機、50% 重疊、
    Hann 窗 overlap-add,能量恆定。seed 固定,同一集重跑結果一致。
    """
    paths = [t[1] if isinstance(t, tuple) else Path(t) for t in tracks]
    frames = []
    nfft = min(ROOM_TONE_NFFT, max(64, 1 << (int(seg * sr).bit_length() - 1)))
    win = np.hanning(nfft)
    for a, b in spans:
        take = min(seg, b - a)
        acc = None
        for p in paths:
            x = _read_mono(p, sr, a, a + take)
            acc = x if acc is None else acc + x[:len(acc)]
        if acc is None:
            continue
        for i in range(0, max(0, len(acc) - nfft) + 1, nfft // 2):
            frames.append(np.abs(np.fft.rfft(acc[i:i + nfft] * win)))
    if not frames or n <= 0:
        return np.zeros(max(0, n))
    mag = np.median(np.stack(frames), axis=0)     # 中位數:單一事件不影響音色
    rng = np.random.default_rng(seed)
    out = np.zeros(n + nfft)
    hop = nfft // 2
    for at in range(0, n, hop):
        ph = rng.uniform(0.0, 2 * np.pi, len(mag))
        ph[0] = 0.0
        frame = np.fft.irfft(mag * np.exp(1j * ph), nfft) * win
        out[at:at + nfft] += frame
    out = out[:n]
    cur = float(np.sqrt((out ** 2).mean()))
    if normalize_to_db is not None and cur > 1e-12:
        out = out * (10 ** (normalize_to_db / 20) / cur)
    elif cur > 1e-12:                             # 沒指定就對齊取樣段的實際電平
        ref = float(np.sqrt((np.stack(frames) ** 2).mean()) ** 0.5)
        out = out * (ref / cur) if ref > 1e-12 else out
    return out


def measure_track_offset(ref: Path, track: Path, probes=None, sr: int = 44100,
                         min_rho: float = 0.55) -> float:
    """量「分軌相對於混音 source.wav」的時間位移(秒,負值＝分軌比較早)。

    所有 block 時間碼都長在 source.wav 的時間軸上,分軌卻不見得跟它對齊 ——
    EP16 實測整集固定差 219 樣本(4.97ms),規格只講三軌彼此 sample-aligned,
    沒講混音那條。不補位移,每個剪點都偏 5ms。
    只採信相關係數 ≥min_rho 的探測點(那代表這一段是這位在主講),取中位數。
    """
    probes = probes or [(t, t + 4.0) for t in (30, 200, 500, 900, 1400, 1900)]
    lags = []
    for a, b in probes:
        try:
            x = _read_mono(ref, sr, a, b)
            y = _read_mono(track, sr, a, b)
        except Exception:
            continue
        n = min(len(x), len(y))
        if n < sr:
            continue
        x, y = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
        c = np.correlate(x, y, "full")
        den = math.sqrt(float((x ** 2).sum() * (y ** 2).sum())) or 1.0
        if float(c.max()) / den >= min_rho:
            lags.append(int(c.argmax()) - (n - 1))
    if not lags:
        return 0.0
    return -float(np.median(lags)) / sr


def mix_ranges(tracks, ranges, env: dict, out: Path, sr: int = 44100,
               gate_fade: float = 0.015, static_db: dict | None = None,
               pan: dict | None = None, duck_default_db: float = -27.0,
               track_offset: dict | None = None,
               room_tone=None, highpass: float = 0.0) -> dict:
    """三軌 → speech bus WAV(32-bit PCM,留足 headroom)。

    tracks  = [Path, ...] 或 [(name, Path), ...](只給 Path 時 name=檔名 stem)
    ranges  = 全域保留區間(來源時間軸);**三軌套用完全相同的一組**
    env     = {name: [(bus_start, bus_end, gain_db), ...]}(bus 時間軸)
    回傳 {"frames", "clipped", "peak"}。
    """
    norm = [(t if isinstance(t, tuple) else (Path(t).stem, Path(t)))
            for t in tracks]
    static_db = static_db or {}
    pan = pan or {}
    track_offset = track_offset or {}
    n_total = sum(int(round(b * sr)) - int(round(a * sr)) for a, b in ranges)
    curves = {n: envelope_curve(env.get(n, []), sr, n_total / sr, gate_fade,
                                duck_default_db) for n, _p in norm}
    lr = {}
    for n, _p in norm:
        theta = (pan.get(n, 0.0) + 1.0) * math.pi / 4.0
        lr[n] = (math.cos(theta), math.sin(theta))

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr),
         "-ac", "2", "-i", "-", "-c:a", "pcm_s32le", str(out)],
        stdin=subprocess.PIPE)
    clipped = 0
    peak = 0.0
    off = 0            # bus 上已寫出的樣本數
    for a, b in ranges:
        n_r = int(round(b * sr)) - int(round(a * sr))
        done = 0
        while done < n_r:
            take = min(n_r - done, int(CHUNK_SECS * sr))
            ca = a + done / sr
            cb = ca + take / sr
            acc = np.zeros((take, 2))
            for n, p in norm:
                d = track_offset.get(n, 0.0)
                x = _read_mono(p, sr, max(0.0, ca + d), cb + d)[:take]
                if len(x) < take:
                    x = np.concatenate([x, np.zeros(take - len(x))])
                gs = static_db.get(n, 0.0)
                if abs(gs) > 1e-9:
                    x = x * _amp(gs)
                x = x * curves[n][off + done:off + done + take]
                l, r = lr[n]
                acc[:, 0] += x * l
                acc[:, 1] += x * r
            if room_tone is not None:
                bed = room_tone[off + done:off + done + take]
                if len(bed) < take:
                    bed = np.concatenate([bed, np.zeros(take - len(bed))])
                acc[:, 0] += bed
                acc[:, 1] += bed
            peak = max(peak, float(np.max(np.abs(acc))) if take else 0.0)
            clipped += int(np.count_nonzero(np.abs(acc) > 1.0))
            proc.stdin.write(np.clip(acc, -1.0, 1.0)
                             .astype("<f4").tobytes())
            done += take
        off += n_r
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("[pertrack] speech bus 寫檔失敗")
    if highpass:                                     # 保留參數,實作在各軌讀取端
        pass
    return {"frames": n_total, "clipped": clipped, "peak": peak}


BLOCK_ID_RE = re.compile(r"^([A-Z]{1,2})\d{3,5}$")


def plan_from_program(program: list[dict], cp: dict, words: list[dict] | None):
    """render_cut 的 program(已 validate)→ (TrackPlan[], 手動剪除, G 列)。

    勾選來自 markdown(人審真相源),時間碼來自 cutplan.json,`~~刪除線~~`
    用 canonical words.json 換算成**該軌**的字級靜音區間 —— 絕不把同一段串音
    文字的刪除線複製到其他軌。
    """
    from render_cut import strike_removals

    by_prefix = {t["prefix"]: t for t in cp.get("tracks", [])}
    marks: dict[str, dict] = {}
    cuts: list[list[float]] = []
    gap_keep: dict[str, bool] = {}
    for it in program:
        if it["kind"] == "cut":
            cuts.append([it["a"], it["b"]])
            continue
        if it["kind"] != "block":
            continue
        m = BLOCK_ID_RE.match(it["id"])
        if m and m.group(1) in by_prefix:
            marks[it["id"]] = it
        elif it["id"].startswith("G"):
            gap_keep[it["id"]] = it["keep"]

    tracks = []
    for t in cp.get("tracks", []):
        blocks = []
        for b in t["blocks"]:
            it = marks.get(b["id"])
            if it is None:
                continue
            spans = it.get("spans") or []
            strikes = (strike_removals(b, spans, words)
                       if spans and words else [])
            blocks.append({"id": b["id"], "start": b["start"], "end": b["end"],
                           "keep": it["keep"], "kind": b.get("kind", "speech"),
                           "strikes": strikes})
        tracks.append(TrackPlan(name=t["speaker"], prefix=t["prefix"],
                                blocks=blocks, file=t["file"]))
    gaps = [{"id": g["id"], "start": g["start"], "end": g["end"],
             "keep": gap_keep.get(g["id"], False)} for g in cp.get("gaps", [])]
    return tracks, sorted(cuts), gaps


def measure_static_gains(session: Path, tracks, cells, sample_secs: float = 90.0,
                         limit_db: float = 6.0) -> dict[str, float]:
    """各軌 static gain:量每位講者「自己 KEEP 區間」的 integrated LUFS,拉齊到平均。

    麥距與音量三個人本來就不同,先用一個**固定**增益拉齊,dynaudnorm 才不必
    在 bus 上做大動作(動態增益一大,靜音與串音就會被拉上來)。
    """
    from pertrack_cells import KEEP
    from render_cut import measure_lufs_ranges

    lufs = {}
    for t in tracks:
        runs, acc = [], 0.0
        for c in cells:
            if c["state"].get(t.name) != KEEP or acc >= sample_secs:
                continue
            take = min(c["b"] - c["a"], sample_secs - acc)
            runs.append([c["a"], c["a"] + take])
            acc += take
        v = measure_lufs_ranges(session / t.file, runs) if runs else None
        if v is not None:
            lufs[t.name] = v
    if len(lufs) < 2:
        return {}
    target = sum(lufs.values()) / len(lufs)
    return {n: max(-limit_db, min(limit_db, target - v))
            for n, v in lufs.items()}
