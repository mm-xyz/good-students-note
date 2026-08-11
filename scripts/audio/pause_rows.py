#!/usr/bin/env python3
"""
scripts/audio/pause_rows.py — 把「會被自動收緊的停頓」變成 cutplan 上可勾選的列

    python3 scripts/audio/pause_rows.py --session sessions/<slug> \
        [--plan cutplan.pertrack.md] [--max-pause 0.9] [--apply]

2026-08-11 MM:「25:50 那裡有些空白被砍掉太多,一次講完有一點滿。應該也要有
空白列可以選,但是又沒有可以選的地方。」

## 為什麼沒得選

cutplan 上本來就有 G 列(空白/非語音),但它只涵蓋 **block 與 block 之間**
≥2 秒的空白(`cutplan.build_gaps` 的 min_gap=2.0)。而自動收緊處理的是
**block 範圍內** >max_pause(預設 0.9s)的靜音(`render_cut.pause_removals`)。

兩者是不同的東西:0.9–2 秒的句內停頓會被靜默收緊,cutplan 上**完全沒有對應的
列**,人審想留也沒得留。EP16 實測:收緊 29 處,可勾的 G 列只有 13 個。

## 這支做什麼

掃出「這一版 render 會收緊的那些靜音」,在 cutplan 裡插入 P 列:

    - [ ] P0001 [12:34–12:36] ⏸ 停頓 1.4s（不勾＝收緊到 0.6s，勾＝原長保留）

**不勾＝維持現狀**(照 ADR 0010 停頓預設剪掉),勾起來就整段保留。

## 為什麼用插入而不是重新產生 cutplan

MM 的人審成果(EP16 定稿時有 195 個未勾選 ＋ 180 行刪除線)住在現有的 block
id 上。重新產生會換掉所有 id,得再走一次遷移,有風險又浪費已經做完的工。
本支只**插入新列**,既有每一行原封不動。
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_cut import (parse_program, parse_strikes, snap_boundaries,  # noqa: E402
                        merge_ranges, pause_removals, strike_removals)
from srt_utils import fmt_mmss  # noqa: E402

ROW_RE = re.compile(r"^- \[[ xX]\] ([A-Z]{1,2}\d{3,5}) \[(\d+):(\d+)–")


def secs(m, i):
    return int(m.group(i)) * 60 + int(m.group(i + 1))


def main() -> int:
    ap = argparse.ArgumentParser(description="會被收緊的停頓 → 可勾選的 P 列")
    ap.add_argument("--session", required=True)
    ap.add_argument("--plan", default="cutplan.md")
    ap.add_argument("--max-pause", type=float, default=0.9)
    ap.add_argument("--pause-keep", type=float, default=0.6)
    ap.add_argument("--snap-window", type=float, default=0.4)
    ap.add_argument("--apply", action="store_true", help="真的寫進 cutplan")
    args = ap.parse_args()

    sdir = Path(args.session)
    plan = sdir / args.plan
    cp = json.loads((sdir / "cutplan.json").read_text(encoding="utf-8"))
    by_id = {b["id"]: b for b in cp.get("blocks", [])}
    for t in cp.get("tracks", []):
        by_id.update({b["id"]: b for b in t["blocks"]})
    words = [w for w in json.loads((sdir / "words.json").read_text(encoding="utf-8"))
             if w["end"] - w["start"] <= 3.0]
    sil = json.loads((sdir / "prosody.json").read_text(encoding="utf-8")).get(
        "silences", [])
    if not sil:
        print("[pause] prosody.json 沒有靜音資料,無事可做", file=sys.stderr)
        return 1

    # 「保留 block 之間、長度超過 max_pause 的空白」= 這一版會消失的時間。
    #
    # 為什麼用這個判準而不是重跑 pause_removals:兩條線的機制不同。
    #   混音線 — pause_removals 收緊 unit 內 >max_pause 的靜音
    #   分軌線 — 根本不跑 pause_removals(render_cut.py:1165 明確跳過),
    #            時間是被 atomic cell 的「三軌全 MUTE → 移除」吃掉的
    # 但兩者的**結果**是同一件事:保留內容之間的空白會消失。從 cutplan 的
    # 保留 block 直接算,對兩條線都成立,也不必複製 render 的 unit 合併邏輯
    # (我第一版逐 block 跑 pause_removals,只抓到 2 處,實際 render 收緊 29 處
    #  ——因為真正的收緊在 unit 上做,跨 block 的靜音逐 block 算不到)。
    kept: list[tuple[float, float, str]] = []
    for it in parse_program(plan):
        if it["kind"] != "block" or not it["keep"] or it["id"].startswith("P"):
            continue
        b = by_id.get(it["id"])
        if b and not str(it["raw"]).startswith("（非詞彙出聲"):
            kept.append((b["start"], b["end"], it["id"]))
    kept.sort()
    found: list[tuple[float, float, str]] = []
    prev_end, prev_id = None, None
    for a, z, bid in kept:
        if prev_end is not None and a - prev_end > args.max_pause:
            found.append((prev_end, a, prev_id))
        prev_end = max(prev_end or 0.0, z)
        prev_id = bid

    # 合併重疊(相鄰 block 可能算到同一個靜音)
    found.sort()
    merged: list[list] = []
    for a, z, bid in found:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], z)
        else:
            merged.append([a, z, bid])

    if not merged:
        print("[pause] 這一版沒有會被收緊的停頓")
        return 0

    lines = plan.read_text(encoding="utf-8").splitlines()
    # 已有的 P 列先移除(重跑要冪等);人審勾過的 P 列保留其勾選狀態
    kept_marks = {}
    out_lines = []
    for l in lines:
        m = re.match(r"^- \[([ xX])\] (P\d{4}) \[(\d+):(\d+)–", l)
        if m:
            kept_marks[(secs(m, 3))] = m.group(1)
            continue
        out_lines.append(l)
    lines = out_lines

    # 依時間插入:放在「起點晚於該停頓」的第一個 block 列之前
    ins: dict[int, list[str]] = {}
    for i, (a, z, bid) in enumerate(merged, 1):
        mark = kept_marks.get(int(a), " ")
        row = (f"- [{mark}] P{i:04d} [{fmt_mmss(a)}–{fmt_mmss(z)}] ⏸ 停頓 "
               f"{z - a:.1f}s（不勾＝收緊到 {args.pause_keep}s，勾＝原長保留）")
        pos = len(lines)
        for j, l in enumerate(lines):
            m = ROW_RE.match(l)
            if m and secs(m, 2) >= a:
                pos = j
                break
        ins.setdefault(pos, []).append(row)

    new_lines = []
    for j, l in enumerate(lines):
        new_lines.extend(ins.get(j, []))
        new_lines.append(l)
    new_lines.extend(ins.get(len(lines), []))

    total = sum(z - a for a, z, _ in merged)
    print(f"[pause] {len(merged)} 處會被收緊的停頓（共 {total:.1f}s）"
          f"{'' if args.apply else ' — dry-run，加 --apply 才寫入'}")
    for a, z, bid in merged[:8]:
        print(f"    {fmt_mmss(a)}–{fmt_mmss(z)}  {z - a:.1f}s  （{bid} 附近）")
    if len(merged) > 8:
        print(f"    …共 {len(merged)} 處")
    if kept_marks:
        print(f"[pause] 沿用既有 P 列的勾選狀態 {sum(1 for v in kept_marks.values() if v != ' ')} 個")
    if args.apply:
        # 復用既有的 G 列機制(ADR 0007):把 P 列寫進 cutplan.json 的 gaps,
        # render 就直接認得——不勾＝剪掉(現狀)、勾＝保留原聲。不必發明新的
        # render 邏輯,也不必動 atomic cell 模型。
        gaps = [g for g in cp.get("gaps", []) if not g["id"].startswith("P")]
        for i, (a, z, bid) in enumerate(merged, 1):
            gaps.append({"id": f"P{i:04d}", "start": round(a, 3),
                         "end": round(z, 3), "before": bid, "keep": False})
        cp["gaps"] = gaps
        (sdir / "cutplan.json").write_text(
            __import__("json").dumps(cp, ensure_ascii=False), encoding="utf-8")
        plan.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"[pause] ✓ 已插入 {len(merged)} 個 P 列 → {plan.name}"
              f"（既有每一行原封不動）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
