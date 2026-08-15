#!/usr/bin/env python3
"""
scripts/audio/diff_clips.py — 只切出「這次改動的地方」給人審聽

    python3 scripts/audio/diff_clips.py --session sessions/<slug> \
        --old <上一版 cutplan.md> [--plan cutplan.md] [--render <成品.mp3>] \
        [--pad 5] [--out-dir <目錄>]

2026-08-11 MM 拍板:「diff:節錄調整過的地方就好,＋− 5sec,不必出文檔」。

動機:改一次 cutplan 就要重聽 30 分鐘才知道改對沒——實際變動可能只有幾處。
本工具比對前後兩份 cutplan 的**人審決定**(勾選翻轉／刪除線增減),把每一處
變動對應到**新成品的時間軸**上,各切一段 ±pad 秒的小片段。一輪人審從 30 分鐘
降到幾分鐘。

刻意不產文檔:MM 要的是耳朵驗收,不是再讀一份報告。檔名已經帶成品時間碼與
block id,要回頭查 cutplan 用得上。

判準:
- 勾選翻轉(留→剪、剪→留)= 變動
- 刪除線增減(字級精剪改了)= 變動
- 只有理由文字改了、或 block 完全沒動 = 不算變動
被剪掉的 block 在新成品裡不存在,取它在源時間軸上的位置對應到最近的保留段,
聽到的就是「接縫」——那正是要確認的地方。
"""

import argparse
import bisect
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_cut import parse_program, parse_strikes  # noqa: E402
from srt_utils import fmt_mmss  # noqa: E402


def decisions(path: Path) -> dict[str, tuple[bool, tuple]]:
    """cutplan.md → {block id: (是否保留, 刪除線區間)}。只看人審決定。"""
    out = {}
    for it in parse_program(path):
        if it["kind"] != "block":
            continue
        _clean, spans = parse_strikes(it["raw"])
        out[it["id"]] = (it["keep"], tuple(tuple(s) for s in spans))
    return out


def changed_ids(old: dict, new: dict,
                resegment_ratio: float = 0.3) -> tuple[list[tuple[str, str]], str]:
    """回傳 ([(block id, 變動說明)], 附註)。

    兩邊都有的 id → 比勾選與刪除線。
    **只有新版有的 id 且是保留的 → 也算變動**(「新增了一段」正是最該聽的改動;
    EP16 的補錄就是 16 個全新的 S block,2026-08-11 實測發現原本會被整批跳過)。
    但新 id 佔比超過 resegment_ratio 就是**重切過**,那種情況整份都不一樣、
    逐塊 diff 沒有意義,改成只比共有 id 並在回報裡講明。
    """
    fresh = [b for b in new if b not in old]
    resegmented = len(fresh) > max(1, len(new) * resegment_ratio)
    out = []
    for bid in sorted(new):
        if bid not in old:
            if not resegmented and new[bid][0]:
                out.append((bid, "新增"))
            continue
        (ok, os_), (nk, ns) = old[bid], new[bid]
        if ok != nk:
            out.append((bid, "剪掉" if not nk else "改回保留"))
        elif os_ != ns:
            d = len(ns) - len(os_)
            out.append((bid, f"字級精剪{'＋' if d > 0 else '−'}{abs(d) or '調整'}"))
    note = (f"（新版有 {len(fresh)} 個新 id，佔比超過 {resegment_ratio:.0%}"
            f"＝判定重切過，只比共有的 block）" if resegmented else "")
    return out, note


def src_to_dst(cut_map: dict, t: float) -> float | None:
    """源時間 → 成品時間。落在被剪掉的區間就取後面最近的保留段起點(=接縫)。"""
    rs = cut_map["ranges"]
    starts = [r["src_start"] for r in rs]
    i = bisect.bisect_right(starts, t) - 1
    if 0 <= i < len(rs) and rs[i]["src_start"] <= t < rs[i]["src_end"]:
        tempo = cut_map.get("tempo", 1.0) or 1.0
        return rs[i]["dst_start"] + (t - rs[i]["src_start"]) / tempo
    nxt = bisect.bisect_left(starts, t)
    return rs[nxt]["dst_start"] if nxt < len(rs) else None


def merge(points: list[tuple[float, float, str]], pad: float, dur: float):
    """相鄰變動**區間**合併(避免切出一堆幾乎重疊的小檔)。

    用區間不用點:一段 40 秒的補錄若只取中點 ±5s,聽到的是接縫而不是整段內容
    (2026-08-11 實測:16 個補錄 block 全錨在同一點,合併後只有 10 秒)。
    """
    out = []
    for t0, t1, label in sorted(points):
        a, b = max(0.0, t0 - pad), min(dur, t1 + pad)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b), out[-1][2] + [label])
        else:
            out.append((a, b, [label]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="只切出這次改動的地方給人審聽")
    ap.add_argument("--session", required=True)
    ap.add_argument("--old", required=True, help="上一版 cutplan.md")
    ap.add_argument("--plan", default="cutplan.md", help="這一版節目單")
    ap.add_argument("--render", help="成品檔(預設取 session 內最新的 mp3)")
    ap.add_argument("--cut-map", default="cut_map.json")
    ap.add_argument("--pad", type=float, default=5.0, help="前後各留幾秒 context")
    ap.add_argument("--out-dir", default="diff_clips")
    args = ap.parse_args()

    sdir = Path(args.session)
    new_plan, old_plan = sdir / args.plan, Path(args.old)
    for p in (new_plan, old_plan, sdir / args.cut_map):
        if not p.exists():
            print(f"[diff] ✗ 找不到 {p}", file=sys.stderr)
            return 2

    render = Path(args.render) if args.render else None
    if render is None:
        cands = sorted(sdir.rglob("*.mp3"), key=lambda p: p.stat().st_mtime)
        if not cands:
            print("[diff] ✗ session 內找不到成品 mp3,用 --render 指定",
                  file=sys.stderr)
            return 2
        render = cands[-1]
    print(f"[diff] 成品:{render.relative_to(sdir) if render.is_relative_to(sdir) else render}")

    ch, note = changed_ids(decisions(old_plan), decisions(new_plan))
    if note:
        print(f"[diff] {note}")
    if not ch:
        print("[diff] 兩版的人審決定完全相同,沒有要聽的地方。")
        return 0

    cp = json.loads((sdir / "cutplan.json").read_text(encoding="utf-8"))
    by_id = {b["id"]: b for b in cp.get("blocks", [])}
    for t in cp.get("tracks", []):
        by_id.update({b["id"]: b for b in t["blocks"]})
    # 補錄 block 的時間碼在**補錄檔自己的時間軸**上(0 起算),不是源時間軸——
    # 直接餵 src_to_dst 會對出離譜的位置(2026-08-11 實測:補錄在成品 22:33,
    # 卻被切在 0:04)。改用它在節目單裡**前一個正片 block** 的源時間定位。
    insert_ids = {b["id"] for i in cp.get("inserts", []) for b in i["blocks"]}
    for i in cp.get("inserts", []):
        by_id.update({b["id"]: b for b in i["blocks"]})
    anchor, last_regular = {}, None
    for it in parse_program(new_plan):
        if it["kind"] != "block":
            continue
        if it["id"] in insert_ids:
            if last_regular is not None:
                anchor[it["id"]] = last_regular
        else:
            last_regular = it["id"]
    cut_map = json.loads((sdir / args.cut_map).read_text(encoding="utf-8"))
    dur = float(cut_map.get("final_duration_secs") or 0) or 10 ** 9

    pts, miss = [], 0
    for bid, why in ch:
        ins = by_id.get(bid) if bid in insert_ids else None
        b = by_id.get(anchor.get(bid, bid))
        if not b:
            miss += 1
            continue
        d = src_to_dst(cut_map, (b["start"] + b["end"]) / 2)
        if d is None:
            miss += 1
            continue
        if ins:            # 補錄:錨點之後照補錄檔自己的時間軸往後推
            pts.append((d + ins["start"], d + ins["end"], f"{bid} {why}"))
        else:
            span = min(b["end"] - b["start"], 8.0) / (cut_map.get("tempo") or 1.0)
            pts.append((d, d + span, f"{bid} {why}"))
    if not pts:
        print(f"[diff] {len(ch)} 處變動都對不到成品時間軸(重切過?)", file=sys.stderr)
        return 1

    regions = merge(pts, args.pad, dur)
    out = sdir / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.mp3"):
        f.unlink()

    print(f"[diff] {len(ch)} 處人審變動 → {len(regions)} 段要聽"
          f"（±{args.pad:.0f}s，共 {sum(b - a for a, b, _ in regions):.0f} 秒）"
          + (f"；{miss} 處對不到時間軸" if miss else ""))
    for i, (a, b, labels) in enumerate(regions, 1):
        name = f"{i:02d}_{fmt_mmss(a).replace(':', '-')}_{labels[0].split()[0]}.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}",
                        "-t", f"{b - a:.3f}", "-i", str(render),
                        "-c:a", "libmp3lame", "-b:a", "160k", str(out / name)],
                       check=True)
        print(f"    {name}  ←  {'、'.join(labels[:3])}"
              + (f" 等 {len(labels)} 處" if len(labels) > 3 else ""))
    print(f"[diff] ✓ → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
