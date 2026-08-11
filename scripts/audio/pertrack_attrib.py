#!/usr/bin/env python3
"""
scripts/audio/pertrack_attrib.py — 分軌歸屬(D2)與非詞彙出聲偵測(D3)的訊號邏輯

全部吃「已經算好的 frame 電平」,不碰音檔、不呼叫 ffmpeg,所以整套可以用合成
資料單元測試(scripts/tests/test_pertrack_attrib.py)。

D2 文字歸屬:支配 ＋ hysteresis ＋ 字界對齊
    · 三軌同步波形上以固定 hop(預設 10ms)算能量,積分窗 ~100ms
    · 第一名領先第二名 ≥3dB 且穩定 ≥200ms → 歸第一名
    · 差距 <3dB → 標「歸屬不確定」,不硬選
    · 挑戰者必須連續領先 switch 秒才換手(避免逐 frame 抖動)
    · 換手只落在 canonical word boundary;附近 snap 秒找不到字界就不切

D3 非詞彙出聲:CFAR 式自適應門檻
    · 串音預測用**線性功率相加** P_bleed = Σ_j P_j×g[i][j] + P_noise
      (舊版用 max_j,是錯的物理:兩個各 −20dB 的來源加起來是 −17dB 不是 −20dB)
    · excess 門檻 = 該軌負樣本殘差的 P99.5;duration 門檻同法
    · 掃描 20–40ms frame ＋ 80ms gap closing ＋ ~120ms 最短長度
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import join_words  # noqa: E402

BLEED_FALLBACK_DB = -12.0     # 樣本不足時保守估一個偏大的串音,寧可少抓


def integrate(power, win: int):
    """frame 功率的移動平均(置中,邊緣按實際樣本數平均,長度不變)。"""
    p = np.asarray(power, dtype=float)
    if win <= 1:
        return p
    pad = win // 2
    cs = np.concatenate([[0.0], np.cumsum(p)])
    lo = np.clip(np.arange(len(p)) - pad, 0, len(p))
    hi = np.clip(np.arange(len(p)) - pad + win, 0, len(p))
    return (cs[hi] - cs[lo]) / np.maximum(1, hi - lo)


def db(power):
    return 10.0 * np.log10(np.maximum(np.asarray(power, dtype=float), 1e-20))


def lin(dbv):
    return np.power(10.0, np.asarray(dbv, dtype=float) / 10.0)


def calibrate_bleed(lv_db, dominance_db: float = 12.0, pct: float = 30.0,
                    min_samples: int = 20, active_db: float = -60.0):
    """量每一對軌的串音增益 g[i][j] ≈「j 講話時 i 軌會收到多少」(dB,負值)。

    只挑「j 明顯獨大(領先第二名 ≥dominance)」的 frame —— 那種時刻 i 軌收到的
    幾乎純粹是串音。再取這些差值的低分位數(預設 P30):i 自己也在出聲的 frame
    會把差值往上拉,取低分位才抓得到「i 安靜時」的真實串音底線。
    """
    lv = np.asarray(lv_db, dtype=float)
    n = lv.shape[0]
    order = np.argsort(-lv, axis=0)
    top, second = order[0], order[1]
    idx = np.arange(lv.shape[1])
    lead = lv[top, idx] - lv[second, idx]
    ok = (lv[top, idx] > active_db) & (lead >= dominance_db)
    g = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            sel = ok & (top == j)
            xs = np.sort(lv[i, idx[sel]] - lv[j, idx[sel]])
            g[i][j] = (BLEED_FALLBACK_DB if len(xs) < min_samples
                       else float(xs[int(len(xs) * pct / 100.0)]))
    return g


def predict_bleed_db(lv_db, g_db, noise_db):
    """P_bleed[i] = Σ_{j≠i} P_j × g[i][j] + P_noise[i](dB 回傳)。"""
    lv = np.asarray(lv_db, dtype=float)
    n = lv.shape[0]
    out = np.zeros_like(lv)
    for i in range(n):
        acc = np.full(lv.shape[1], lin(noise_db[i]))
        for j in range(n):
            if i != j:
                acc = acc + lin(lv[j]) * lin(g_db[i][j])
        out[i] = db(acc)
    return out


def owner_runs(lv_db, hop: float, t0: float, t1: float, margin: float = 3.0,
               stable: float = 0.2, switch: float = 0.18):
    """支配 ＋ hysteresis → [(start, end, owner|None), ...](owner=軌索引)。

    · 還沒有 owner:第一名要領先**第二名** ≥margin 並持續 ≥stable 秒才成立
    · 已經有 owner:挑戰者要領先**現任** ≥margin 並持續 ≥switch 秒才換手
      (跟第二名比會讓「三人中兩人差不多大聲」一直翻面)
    · 從頭到尾都湊不到 → owner=None ＝ 歸屬不確定,交人審,不硬選
    """
    lv = np.asarray(lv_db, dtype=float)
    i0 = max(0, int(round(t0 / hop)))
    i1 = min(lv.shape[1], int(round(t1 / hop)))
    if i1 <= i0:
        return [(t0, t1, None)]
    runs: list[tuple[float, float, int | None]] = []
    owner: int | None = None
    run_start = i0
    cand: int | None = None
    cand_start = i0
    for f in range(i0, i1):
        col = lv[:, f]
        order = np.argsort(-col)
        best = int(order[0])
        if owner is not None and best == owner:
            cand = None
            continue
        ref = col[owner] if owner is not None else col[int(order[1])]
        need = switch if owner is not None else stable
        if col[best] - ref >= margin:
            if cand != best:
                cand, cand_start = best, f
            if (f - cand_start + 1) * hop >= need - 1e-9:
                if cand_start > run_start:
                    runs.append((run_start * hop, cand_start * hop, owner))
                owner, run_start, cand = best, cand_start, None
        else:
            cand = None
    runs.append((run_start * hop, i1 * hop, owner))
    return [(round(a, 6), round(b, 6), o) for a, b, o in runs if b - a > 1e-9]


def annotate_canonical(words: list[dict], block_text: str) -> list[dict]:
    """給每個 word 掛上它在 **canonical block 文字** 裡對應的字(`ctext`)。

    D1 說正式文字是 cutplan.json 的既有 block 文字,不是 words.json 重建的字。
    兩者會不一樣 —— EP16 的 B0085,SRT 是人工校過的「只要」,words.json 還是
    whisper 原本的「隻要」。拿 words 重建 phrase 文字,render 的防幻覺驗證
    (「文字須逐字存在於來源 SRT」)就會直接 FAIL。

    用 difflib 把「words 字元流」對齊到「canonical 字元流」,取單調遞增的
    切點,所以所有 ctext 接起來**一定**等於 canonical 文字(去空白後),
    多出來的字尾由最後一個 word 吸收,不會被丟掉。
    """
    flat = re.sub(r"\s+", "", block_text)
    chars = [re.sub(r"\s+", "", w["word"]) for w in words]
    wflat = "".join(chars)
    pos = [0] * (len(wflat) + 1)
    sm = difflib.SequenceMatcher(None, wflat, flat, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for k in range(i1, i2):
            if tag == "equal":
                pos[k] = j1 + (k - i1)
            else:
                span = max(1, i2 - i1)
                pos[k] = j1 + int(round((j2 - j1) * (k - i1) / span))
    pos[len(wflat)] = len(flat)
    out, at = [], 0
    for w, c in zip(words, chars):
        lo = pos[at] if at else 0
        at += len(c)
        hi = pos[at] if at < len(wflat) else len(flat)
        out.append({**w, "ctext": flat[lo:hi]})
    if out:
        out[-1]["ctext"] = flat[pos[max(0, len(wflat) - len(chars[-1]))]:]
    return out


def split_phrase(words: list[dict], ref_text: str, runs, snap: float = 0.25):
    """canonical phrase 依 owner 換手點切開,切點只落在 canonical word boundary。

    附近 snap 秒內找不到字界 → **不切**,整句歸給佔時間最多的那位並標不確定
    (D2:換手只能落在字界;硬切會把字切成兩半)。
    回傳 [{start, end, text, owner, uncertain, reason, words}]。
    文字一律由 canonical words 重建 —— 逐軌 ASR 不參與(D1)。
    """
    if not words:
        return []
    gaps = [( (words[k - 1]["end"] + words[k]["start"]) / 2.0, k)
            for k in range(1, len(words))]
    cuts: list[int] = []
    dropped = False
    for a, _b, _o in runs[1:]:
        best = min(gaps, key=lambda x: abs(x[0] - a), default=None)
        if best is None or abs(best[0] - a) > snap or best[1] in cuts:
            dropped = True
            continue
        cuts.append(best[1])
    cuts = sorted(set(cuts))

    def owner_of(a: float, b: float):
        acc: dict[int | None, float] = {}
        for ra, rb, o in runs:
            ov = min(b, rb) - max(a, ra)
            if ov > 0:
                acc[o] = acc.get(o, 0.0) + ov
        if not acc:
            return None
        return max(acc.items(), key=lambda kv: kv[1])[0]

    out = []
    for lo, hi in zip([0] + cuts, cuts + [len(words)]):
        ws = words[lo:hi]
        if not ws:
            continue
        a, b = ws[0]["start"], ws[-1]["end"]
        o = owner_of(a, b)
        reason = ""
        if o is None:
            reason = "歸屬不確定（三軌差距 <3dB）"
        elif dropped and len(cuts) < len(runs) - 1:
            reason = "換手點附近 250ms 內沒有字界，未切開"
        text = ("".join(x["ctext"] for x in ws) if "ctext" in ws[0]
                else join_words(ws, ref_text))
        out.append({"start": a, "end": b, "text": text,
                    "owner": o, "uncertain": bool(reason), "reason": reason,
                    "words": ws})
    return out


def cfar_percentile(xs, pct: float, default: float | None = None):
    """樣本的第 pct 百分位;樣本為空回 default(CFAR 門檻算不出來時的退路)。"""
    a = np.asarray(list(xs), dtype=float)
    if a.size == 0:
        return default
    return float(np.percentile(a, pct))


def find_events(hits, hop: float, gap_close: float = 0.08,
                min_dur: float = 0.12):
    """布林命中序列 → 事件區間:先合併 <gap_close 的空洞,再丟掉 <min_dur 的。"""
    runs: list[list[int]] = []
    for f, h in enumerate(hits):
        if not h:
            continue
        if runs and runs[-1][1] == f:
            runs[-1][1] = f + 1
        else:
            runs.append([f, f + 1])
    merged: list[list[int]] = []
    for r in runs:
        if merged and (r[0] - merged[-1][1]) * hop < gap_close - 1e-9:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    return [(round(a * hop, 6), round(b * hop, 6)) for a, b in merged
            if (b - a) * hop >= min_dur - 1e-9]


def drop_self_adjacent(events, own_spans, guard: float = 0.25):
    """丟掉緊貼「自己台詞」的出聲事件。

    D3 講的是「壓在別人話底下、人審剪不掉的附和」。緊貼自己下一句開頭
    (EP16 5:10 的 MR0109 距離自己開講只有 0.01 秒)的能量是自己的字頭、
    吸氣、椅子聲 —— 這種列預設不勾 ＝ 靜音,留著等於把自己的字頭削掉。
    """
    out = []
    for e in events:
        a, b = e["start"], e["end"]
        if any(a < y + guard and b > x - guard for x, y in own_spans):
            continue
        out.append(e)
    return out
