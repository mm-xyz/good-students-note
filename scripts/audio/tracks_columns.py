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
