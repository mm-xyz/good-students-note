#!/usr/bin/env python3
"""
scripts/audio/pertrack_cells.py — 分軌剪輯的兩層模型:atomic cells(D4)

純邏輯、零 I/O、零 ffmpeg,所以可以整套單元測試(scripts/tests/test_pertrack_cells.py)。

    三軌所有 block 起訖 ＋ 刪除線字界 ＋ `## ✂` 手動剪點 ＋ G 列邊界
        → 取聯集切成互不重疊的 atomic cell
    每個 cell × 每條軌只有三種狀態:
        KEEP    該軌在這個 cell 全開(0dB)
        SILENT  明確不要(未勾選的 block、刪除線、✂ 手動剪除)→ 降到全靜音
        DUCK    沒有任何 block 覆蓋 → 常態衰減(−24～−30dB)

為什麼 DUCK 不是「真的靜音」:D5。整集固定的 activity mask 比「零星事件突然
關一軌」安全——後者會改變延遲串音的相位組合,產生音色與底噪抽動。所以邏輯層
的「MUTE」在物理層實作成常態衰減,只有**明確不要的事件**才降到全靜音。

時間層規則:
    有任何一軌 KEEP        → 時間保留(其餘軌照自己的狀態衰減/靜音)
    沒有 KEEP、有 SILENT   → 時間移除(三軌一起)
    沒有 KEEP、全是 DUCK   → 「無主時間」,交給 activity mask / 停頓收緊處理

**無主時間是規格沒寫、但不處理就會出事的坑**:canonical phrase 之間天然有
0.1–0.5 秒的字間空隙,那些空隙沒有任何 block 覆蓋。照 D4 字面「三軌皆 MUTE →
時間移除」會把每一個換氣縫都剪掉,講話變成連珠炮。所以短空隙(≤hold)由
**前一位講者的軌**橋接(mic continue,底噪不抽動),長空隙才走停頓收緊。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

KEEP = "keep"
DUCK = "duck"
SILENT = "silent"

EPS = 1e-6


class ConflictError(Exception):
    """同一軌重疊 block 的勾選互相矛盾 — render 必須 FAIL,不可自行猜。"""


@dataclass
class TrackPlan:
    name: str
    prefix: str
    blocks: list[dict] = field(default_factory=list)
    file: str = ""

    def covering(self, a: float, b: float) -> list[dict]:
        return [x for x in self.blocks
                if x["start"] < b - EPS and x["end"] > a + EPS]


def _edges(*groups) -> list[float]:
    xs = set()
    for g in groups:
        for t in g:
            xs.add(round(float(t), 6))
    return sorted(xs)


def build_cells(tracks: list[TrackPlan], manual_cuts=(), gaps=()) -> list[dict]:
    """三軌 block/刪除線/✂/G 列邊界取聯集 → atomic cells(互不重疊)。

    優先序:✂ 手動剪除 > block(含刪除線)> G 列 > DUCK。
    ✂ 是人審的逃生艙,說了算,不跟 block 算矛盾;block 之間互相矛盾才 FAIL。
    """
    ts = []
    for t in tracks:
        for b in t.blocks:
            ts += [b["start"], b["end"]]
            for s, e in b.get("strikes") or []:
                ts += [s, e]
    for a, b in manual_cuts:
        ts += [a, b]
    for g in gaps:
        ts += [g["start"], g["end"]]
    bounds = _edges(ts)
    if len(bounds) < 2:
        return []

    cells = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < EPS:
            continue
        mid = (a + b) / 2
        state = {}
        for t in tracks:
            cut = any(x <= mid < y for x, y in manual_cuts)
            if cut:
                state[t.name] = SILENT
                continue
            implied: dict[str, list[str]] = {}
            for blk in t.covering(a, b):
                struck = any(s - EPS <= mid < e + EPS
                             for s, e in (blk.get("strikes") or []))
                st = SILENT if (struck or not blk.get("keep")) else KEEP
                implied.setdefault(st, []).append(blk["id"])
            if KEEP in implied and SILENT in implied:
                raise ConflictError(
                    f"[pertrack] FAIL: {t.name} 軌在 "
                    f"{a:.3f}–{b:.3f}s 有互相矛盾的重疊 block —— "
                    f"保留 {'/'.join(implied[KEEP])} vs 剪除/刪除線 "
                    f"{'/'.join(implied[SILENT])}。同軌重疊不可「有一個勾就算留」"
                    f"也不可「文件後者覆蓋」,請人審把矛盾解掉再 render。")
            if implied:
                state[t.name] = KEEP if KEEP in implied else SILENT
                continue
            hit = next((g for g in gaps if g["start"] <= mid < g["end"]), None)
            if hit is not None:
                state[t.name] = KEEP if hit.get("keep") else SILENT
            else:
                state[t.name] = DUCK
        cells.append({"a": a, "b": b, "state": state})
    return cells


def merge_cells(cells: list[dict]) -> list[dict]:
    """相鄰且狀態完全相同的 cell 併成一段(降低 ffmpeg/numpy 的段數)。"""
    out: list[dict] = []
    for c in cells:
        if (out and abs(out[-1]["b"] - c["a"]) < EPS
                and out[-1]["state"] == c["state"]):
            out[-1] = {"a": out[-1]["a"], "b": c["b"], "state": c["state"]}
        else:
            out.append({"a": c["a"], "b": c["b"], "state": dict(c["state"])})
    return out


def _keep_runs(cells: list[dict], name: str) -> list[list[float]]:
    runs: list[list[float]] = []
    for c in cells:
        if c["state"][name] != KEEP:
            continue
        if runs and abs(runs[-1][1] - c["a"]) < EPS:
            runs[-1][1] = c["b"]
        else:
            runs.append([c["a"], c["b"]])
    return runs


def _no_keep_runs(cells: list[dict]) -> list[list[float]]:
    """沒有任何一軌 KEEP 的最大區間(SILENT 也算在內 —— 那段時間沒人在講)。"""
    runs: list[list[float]] = []
    for c in cells:
        if KEEP in c["state"].values():
            continue
        if runs and abs(runs[-1][1] - c["a"]) < EPS:
            runs[-1][1] = c["b"]
        else:
            runs.append([c["a"], c["b"]])
    return runs


def _rebuild(cells: list[dict], extra_keep: dict[str, list[list[float]]],
             span: tuple[float, float] | None = None) -> list[dict]:
    """把「額外開 mic 的區間」疊回 cells;必要時延展 cell 宇宙並重切邊界。

    只把 DUCK 升成 KEEP —— SILENT 是人審明確說不要的,任何自動機制都不准蓋掉。
    """
    lo = min([c["a"] for c in cells] + [x[0] for v in extra_keep.values()
                                        for x in v])
    hi = max([c["b"] for c in cells] + [x[1] for v in extra_keep.values()
                                        for x in v])
    if span:
        lo, hi = min(lo, span[0]), max(hi, span[1])
    bounds = _edges([c["a"] for c in cells] + [c["b"] for c in cells],
                    [x for v in extra_keep.values() for r in v for x in r],
                    [lo, hi])
    names = list(cells[0]["state"]) if cells else list(extra_keep)
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < EPS:
            continue
        mid = (a + b) / 2
        old = next((c for c in cells if c["a"] - EPS <= mid < c["b"] + EPS),
                   None)
        state = dict(old["state"]) if old else {n: DUCK for n in names}
        for n, runs in extra_keep.items():
            if state.get(n) == DUCK and any(x <= mid < y for x, y in runs):
                state[n] = KEEP
        out.append({"a": a, "b": b, "state": state})
    return merge_cells(out)


def apply_mask(cells: list[dict], hold: float = 0.9, lookahead: float = 0.05,
               hangover: float = 0.15) -> list[dict]:
    """整集一致的 activity mask(D5)。

    1. **橋接**:沒有任何一軌 KEEP 且長度 ≤hold 的空隙,由「前一位 KEEP 的軌」
       延續開著(沒有前一位就交給後一位)。講者自己的換氣縫不關 mic,底噪連續。
    2. **lookahead / hangover**:每段 KEEP 提前 lookahead 開、延後 hangover 關,
       避免字頭被 gate 削掉、字尾被切斷。
    兩者都只把 DUCK 升成 KEEP,絕不動 SILENT。
    """
    if not cells:
        return cells
    names = list(cells[0]["state"])
    span = (cells[0]["a"], cells[-1]["b"])
    extra: dict[str, list[list[float]]] = {n: [] for n in names}

    if hold > 0:
        for a, b in _no_keep_runs(cells):
            if b - a > hold + EPS:
                continue
            prev = next((n for n in names
                         for r in _keep_runs(cells, n)
                         if abs(r[1] - a) < EPS), None)
            if prev is None:
                prev = next((n for n in names
                             for r in _keep_runs(cells, n)
                             if abs(r[0] - b) < EPS), None)
            if prev is not None:
                extra[prev].append([a, b])
    cells = _rebuild(cells, extra, span) if any(extra.values()) else cells

    extra = {n: [] for n in names}
    for n in names:
        for a, b in _keep_runs(cells, n):
            extra[n].append([max(span[0], a - lookahead),
                             min(span[1] + hangover, b + hangover)])
    return _rebuild(cells, extra, span)


def resolve_time(cells: list[dict], max_pause: float = 0.9,
                 pause_keep: float = 0.6) -> tuple[list[dict], list[list[float]]]:
    """時間層:決定哪些 cell 留在共同時間軸上,回傳 (保留的 cells, 被移除的區間)。

    - 有 KEEP → 留
    - 沒 KEEP 但有 SILENT → 整段移除(明確不要)
    - 沒 KEEP 全 DUCK(無主時間):
        * 落在頭尾(前面沒人講過 / 後面沒人再講)→ 移除
        * 長度 > max_pause → 收緊到 pause_keep(頭尾各留一半),
          留下來的部分由鄰接講者的軌橋接,不讓底噪在停頓中掉下去
        * 其餘 → 原樣保留
    """
    if not cells:
        return [], []
    names = list(cells[0]["state"])
    drops: list[list[float]] = []
    extra: dict[str, list[list[float]]] = {n: [] for n in names}
    keep_runs = {n: _keep_runs(cells, n) for n in names}
    first_keep = min([r[0] for n in names for r in keep_runs[n]], default=None)
    last_keep = max([r[1] for n in names for r in keep_runs[n]], default=None)

    for a, b in _no_keep_runs(cells):
        segs = [c for c in cells if c["a"] >= a - EPS and c["b"] <= b + EPS]
        if any(SILENT in c["state"].values() for c in segs):
            drops.append([a, b])
            continue
        if first_keep is None or b <= first_keep + EPS or a >= last_keep - EPS:
            drops.append([a, b])
            continue
        if b - a > max_pause + EPS:
            half = pause_keep / 2
            drops.append([a + half, b - half])
            prev = next((n for n in names for r in keep_runs[n]
                         if abs(r[1] - a) < EPS), None)
            nxt = next((n for n in names for r in keep_runs[n]
                        if abs(r[0] - b) < EPS), None)
            if prev:
                extra[prev].append([a, a + half])
            if nxt:
                extra[nxt].append([b - half, b])

    if any(extra.values()):
        cells = _rebuild(cells, extra)
    drops = _merge_ranges(drops)
    kept = []
    for c in cells:
        for a, b in _subtract_one(c["a"], c["b"], drops):
            kept.append({"a": a, "b": b, "state": dict(c["state"])})
    kept = [c for c in kept
            if KEEP in c["state"].values() or SILENT not in c["state"].values()]
    return merge_cells(kept), drops


def _merge_ranges(ranges: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a - out[-1][1] < EPS:
            out[-1][1] = max(out[-1][1], b)
        elif b - a > EPS:
            out.append([a, b])
    return out


def _subtract_one(a: float, b: float, drops) -> list[tuple[float, float]]:
    out = []
    cur = a
    for x, y in drops:
        if y <= cur + EPS or x >= b - EPS:
            continue
        if x > cur + EPS:
            out.append((cur, x))
        cur = max(cur, y)
    if b - cur > EPS:
        out.append((cur, b))
    return out


def retained_ranges(cells: list[dict]) -> list[list[float]]:
    """留在共同時間軸上的區間(相鄰的併起來)。"""
    out: list[list[float]] = []
    for c in cells:
        if out and abs(out[-1][1] - c["a"]) < EPS:
            out[-1][1] = c["b"]
        else:
            out.append([c["a"], c["b"]])
    return out


def track_envelopes(cells: list[dict], ranges: list[list[float]],
                    duck_db: float = -27.0, silent_db: float = -60.0,
                    sr: int | None = None) -> dict[str, list[tuple]]:
    """每軌的增益包絡,**換算到 bus 時間軸**(= ranges 依序接起來之後的時間)。

    給了 sr 就用**取樣量化**的位移(round(b·sr)−round(a·sr)),跟實際寫進 bus
    的樣本數同一套算法 —— 用 Σ(b−a) 浮點累加,146 段之後會差好幾個樣本,
    gate 時間點逐段漂移。
    回傳 {track: [(bus_start, bus_end, gain_db), ...]},相鄰同增益已合併。
    """
    names = list(cells[0]["state"]) if cells else []
    gain = {KEEP: 0.0, DUCK: duck_db, SILENT: silent_db}
    out: dict[str, list[tuple]] = {n: [] for n in names}
    off = 0.0
    for ra, rb in ranges:
        for c in cells:
            a, b = max(c["a"], ra), min(c["b"], rb)
            if b - a < EPS:
                continue
            for n in names:
                g = gain[c["state"][n]]
                ba, bb = off + (a - ra), off + (b - ra)
                if out[n] and abs(out[n][-1][1] - ba) < EPS \
                        and abs(out[n][-1][2] - g) < 1e-9:
                    out[n][-1] = (out[n][-1][0], bb, g)
                else:
                    out[n].append((ba, bb, g))
        off += ((int(round(rb * sr)) - int(round(ra * sr))) / sr if sr
                else rb - ra)
    return out


def equal_power_ramp(g0: float, g1: float, n: int) -> list[float]:
    """兩個振幅之間的**等功率**過渡(D6 的 10–20ms gate 邊緣用)。

    線性插值振幅會在中點掉 3dB(功率 = 0.25+0.25);等功率插值 = 對功率線性,
    中點功率剛好是兩端的平均,聽起來音量不會凹一個洞。
    """
    if n <= 1:
        return [g1]
    p0, p1 = g0 * g0, g1 * g1
    return [math.sqrt(max(0.0, p0 + (p1 - p0) * (k / (n - 1))))
            * (1.0 if g0 >= 0 and g1 >= 0 else 1.0)
            for k in range(n)]
