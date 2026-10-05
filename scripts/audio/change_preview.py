#!/usr/bin/env python3
"""
scripts/audio/change_preview.py — 改動試聽:跟上一版比,改到的地方串成一處一個短 mp3(檔名=Block ID)

    python3 scripts/audio/change_preview.py <上一版目錄> <這一版目錄> <這一版.mp3>

2026-10-05 MM:「編輯後的片段應該要放上去」——每出一版都整集重聽太累,只想聽
改過的地方。cut.py 出片後自動跑,試聽檔與清單放進同一個版本資料夾(local＋Drive)。

「改動」從兩版各自的 cutplan 快照＋cutplan.timeline.json 算(零 LLM):
  · 列被剪掉/加回、刪除線變了、軌欄變了
  · cutplan 沒動但 render 改了(停頓收緊、#1079 這類修正):相鄰保留列在成品裡
    的間距變了 >GAP_TOL 秒
每處取成品時間點前後 context 秒,重疊的併成一段,段與段之間留一小段靜音。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tracks_columns import split_col  # noqa: E402

ROW_RE = re.compile(r"^- \[( |x|X)\] ([A-Z]{1,2}\d{3,5}) \[[^\]]*\] (.*)$")
GAP_TOL = 0.3


def load_version(vdir: Path) -> dict:
    """{order:[id], rows:{id:{keep,text,col}}, t:{id: 成品秒數|None}}"""
    rows, order = {}, []
    plan = vdir / "cutplan.md"
    if not plan.exists():                  # 分軌舊線的快照叫 cutplan.pertrack.md
        plan = sorted(vdir.glob("cutplan*.md"))[0]
    for line in plan.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        body, col = split_col(m.group(3))
        rows[m.group(2)] = {"keep": m.group(1) != " ",
                            "text": body.split(" ← ")[0].strip(), "col": col}
        order.append(m.group(2))
    tl = json.loads((vdir / "cutplan.timeline.json").read_text(encoding="utf-8"))
    t = {k: (v[0] if v else None) for k, v in tl.get("blocks", {}).items()}
    return {"order": order, "rows": rows, "t": t}


def _plain(text: str) -> str:
    return text.replace("~~", "")[:16]


def change_spots(old: dict, new: dict, gap_tol: float = GAP_TOL
                 ) -> list[tuple[float, str, list[str]]]:
    """[(這一版成品秒數, 說明, [改到的 Block ID])],依時間排序。"""
    spots: list[tuple[float, str, list[str]]] = []
    pending: list[str] = []          # 剪掉的列,錨到下一個還在成品裡的列
    pending_ids: list[str] = []
    last_both = None                 # 上一個兩版都在成品裡的列
    touched = False                  # last_both 之後有沒有已報的改動
    for bid in new["order"]:
        r, o = new["rows"][bid], old["rows"].get(bid)
        tn, to = new["t"].get(bid), old["t"].get(bid)
        if tn is None:
            if to is not None:
                pending.append(f"剪掉 {bid}「{_plain(r['text'])}」")
                pending_ids.append(bid)
                touched = True
            continue
        why, ids = pending, pending_ids
        pending, pending_ids = [], []
        own = len(why)
        if to is None and o is not None:
            why.append(f"加回 {bid}「{_plain(r['text'])}」")
        elif o is not None:
            if o["text"] != r["text"]:
                why.append(f"刪除線 {bid}「{_plain(r['text'])}」")
            if o["col"] != r["col"]:
                why.append(f"軌欄 {bid} {o['col']} → {r['col']}")
        if to is not None and last_both and not why and not touched:
            pb = last_both
            d_new, d_old = tn - new["t"][pb], to - old["t"][pb]
            if abs(d_new - d_old) > gap_tol:
                why.append(f"間距 {pb}→{bid} {d_old:.1f}s → {d_new:.1f}s")
        if len(why) > own:
            ids = ids + [bid]
        if why:
            spots.append((tn, "、".join(why), ids))
        if to is not None:
            last_both, touched = bid, False
        elif why:
            touched = True
    if pending and new["t"]:
        end = max(v for v in new["t"].values() if v is not None)
        spots.append((end, "、".join(pending), pending_ids))
    return sorted(spots)


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def build_clips(full: Path, spots: list[tuple[float, str, list[str]]],
                outdir: Path, context: float = 3.0) -> list[str]:
    """每處改動切成品前後 context 秒、一處一檔,檔名=Block ID(重疊的併成
    `B0012+B0014.mp3`);outdir 先清空,重跑不留舊檔。回傳每檔一行的清單。"""
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(full)],
        capture_output=True, text=True, check=True).stdout)
    segs: list[list] = []
    for t, why, ids in sorted(spots):
        a, b = max(0.0, t - context), min(dur, t + context)
        if segs and a <= segs[-1][1]:
            segs[-1][1] = max(segs[-1][1], b)
            segs[-1][2].append((t, why))
            segs[-1][3] += [i for i in ids if i not in segs[-1][3]]
        else:
            segs.append([a, b, [(t, why)], list(ids)])
    if outdir.exists():
        for p in outdir.glob("*.mp3"):
            p.unlink()
    if not segs:
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    lines = []
    for a, b, items, ids in segs:
        name = "+".join(ids[:4]) + ("+…" if len(ids) > 4 else "") + ".mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}",
                        "-t", f"{b - a:.3f}", "-i", str(full), "-q:a", "4",
                        str(outdir / name)], check=True)
        whys = ";".join(f"{_mmss(t)} {w}" for t, w in items)
        lines.append(f"{name}|成品 {_mmss(a)}–{_mmss(b)}|{whys}")
    return lines


def previous_version(sdir: Path, current: Path) -> Path | None:
    """同一個 session 裡、版號比 current 小的最新版本目錄(要有快照與 timeline)。"""
    def num(p: Path) -> int:
        m = re.match(r"v(\d+)_", p.name)
        return int(m.group(1)) if m else -1
    cands = [p for p in sdir.iterdir() if p.is_dir() and 0 <= num(p) < num(current)
             and (p / "cutplan.timeline.json").exists()
             and any(p.glob("cutplan*.md"))]
    return max(cands, key=num) if cands else None


def make(prev_dir: Path, new_dir: Path, mp3: Path) -> tuple[Path, int] | None:
    """cut.py 用:算改動、出 `改動試聽/<BlockID>.mp3` 與清單;沒有改動回 None。"""
    spots = change_spots(load_version(prev_dir), load_version(new_dir))
    outdir = new_dir / "改動試聽"
    lines = build_clips(mp3, spots, outdir)
    if not lines:
        return None
    (outdir / "改動清單.txt").write_text(
        f"跟 {prev_dir.name} 比:{len(spots)} 處改動、{len(lines)} 個試聽檔\n"
        f"(檔名=Block ID;前後各 3 秒;成品時間對 {mp3.name})\n\n"
        + "\n".join(lines) + "\n", encoding="utf-8")
    return outdir, len(lines)


if __name__ == "__main__":
    r = make(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    print(r or "沒有改動")
