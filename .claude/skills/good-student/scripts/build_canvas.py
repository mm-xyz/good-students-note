#!/usr/bin/env python3
"""build_canvas.py — 知識點目錄 → Obsidian 總覽 canvas（矩陣體系地圖）。

確定性工作、0 token（CLAUDE.md 原則 6）。讀取知識點 .md 的 frontmatter，
把兩軸 type 排成 行×列 矩陣、單軸/基礎 type 排成群組列，輸出 .canvas JSON。

用法見 /good-student SKILL.md「總覽 canvas」節。
  --section TYPE            該 type 排成 wrapped grid 群組（可多次）
  --matrix TYPE:ROW:COL     該 type 按 frontmatter 的 ROW/COL 欄位排矩陣（可多次）
  --order "AXIS=v1,v2,..."  軸值順序（可多次；未列到的值照發現順序附後）
  --placeholders            矩陣空格放佔位 text node（試切 gate 用）
  --vault-root PATH         Obsidian vault 根（canvas 內 file path 須 vault-relative）
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

CELL_W, CELL_H = 360, 240
HDR_H = 60
GAP = 40
BLOCK_GAP = 200
WRAP = 6  # section grid 每列幾張


def parse_frontmatter(path: Path) -> dict:
    """極簡 YAML frontmatter 解析：只認頂層 `key: value` 純量行。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if not line or line.startswith(("#", " ", "\t")):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.split("#")[0].strip().strip("\"'")
        if val in ("", "null", "~", "[]"):
            fm[key.strip()] = None
        else:
            fm[key.strip()] = val
    return fm


def node_id(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]


def ordered_values(found: list, order: list | None) -> list:
    """--order 指定的在前（僅保留實際出現或 placeholder 需要的），其餘照發現順序。"""
    if not order:
        return sorted(set(found), key=found.index)
    rest = [v for v in dict.fromkeys(found) if v not in order]
    return list(order) + rest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("atoms_dir", type=Path)
    ap.add_argument("--vault-root", type=Path, required=True)
    ap.add_argument("--section", action="append", default=[])
    ap.add_argument("--matrix", action="append", default=[],
                    help="TYPE:ROW_AXIS:COL_AXIS")
    ap.add_argument("--order", action="append", default=[],
                    help='"AXIS=v1,v2,..."')
    ap.add_argument("--placeholders", action="store_true")
    ap.add_argument("--triangular", action="append", default=[],
                    help="matrix TYPE：只畫上三角（col 在 row 軸序中必須晚於 row，"
                         "如 aspect-pair 快行星只往後配慢行星）；佔位適用，實卡永遠畫")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    atoms_dir = args.atoms_dir.resolve()
    vault_root = args.vault_root.resolve()
    if not atoms_dir.is_dir():
        print(f"error: not a directory: {atoms_dir}", file=sys.stderr)
        return 1

    orders: dict[str, list[str]] = {}
    for spec in args.order:
        axis, _, vals = spec.partition("=")
        orders[axis.strip()] = [v.strip() for v in vals.split(",") if v.strip()]

    files = []  # (vault_rel_path, frontmatter)
    for md in sorted(atoms_dir.rglob("*.md")):
        fm = parse_frontmatter(md)
        if not fm.get("type"):
            continue
        try:
            rel = md.resolve().relative_to(vault_root)
        except ValueError:
            print(f"error: {md} is outside vault root {vault_root}", file=sys.stderr)
            return 1
        files.append((str(rel), fm))

    by_type: dict[str, list] = {}
    for rel, fm in files:
        by_type.setdefault(fm["type"], []).append((rel, fm))

    nodes, warn = [], []
    y = 0

    def add_group(label: str, x0: int, y0: int, w: int, h: int) -> None:
        pad = GAP
        nodes.append({"id": node_id("group", label), "type": "group",
                      "label": label, "x": x0 - pad, "y": y0 - pad,
                      "width": w + 2 * pad, "height": h + 2 * pad})

    # ---- section 群組列 ----
    for sec in args.section:
        items = by_type.get(sec, [])
        if not items:
            warn.append(f"section '{sec}': 找不到任何檔案")
            continue
        x = 0
        top = y
        for i, (rel, _fm) in enumerate(items):
            r, c = divmod(i, WRAP)
            nodes.append({"id": node_id("file", rel), "type": "file", "file": rel,
                          "x": c * (CELL_W + GAP), "y": top + r * (CELL_H + GAP),
                          "width": CELL_W, "height": CELL_H})
        rows = (len(items) + WRAP - 1) // WRAP
        w = min(len(items), WRAP) * (CELL_W + GAP) - GAP
        h = rows * (CELL_H + GAP) - GAP
        add_group(sec, 0, top, w, h)
        y = top + h + BLOCK_GAP

    # ---- matrix 矩陣 ----
    for spec in args.matrix:
        try:
            mtype, row_axis, col_axis = spec.split(":")
        except ValueError:
            print(f"error: bad --matrix spec: {spec}", file=sys.stderr)
            return 1
        items = by_type.get(mtype, [])
        if not items and not args.placeholders:
            warn.append(f"matrix '{mtype}': 找不到任何檔案")
            continue
        cell: dict[tuple[str, str], str] = {}
        row_found, col_found = [], []
        for rel, fm in items:
            rv, cv = str(fm.get(row_axis)), str(fm.get(col_axis))
            if rv == "None" or cv == "None":
                warn.append(f"{rel}: 缺 {row_axis}/{col_axis} 欄位，未進矩陣")
                continue
            if (rv, cv) in cell:
                warn.append(f"矩陣 {mtype} 重複組合 {rv}×{cv}: {rel}")
            cell[(rv, cv)] = rel
            row_found.append(rv)
            col_found.append(cv)
        row_vals = ordered_values(row_found, orders.get(row_axis))
        col_vals = ordered_values(col_found, orders.get(col_axis))
        if not row_vals or not col_vals:
            warn.append(f"matrix '{mtype}': 軸值為空（需 --order 或至少一張卡）")
            continue
        top = y
        # 行列表頭
        for ci, cv in enumerate(col_vals):
            nodes.append({"id": node_id("hdr", mtype, "c", cv), "type": "text",
                          "text": f"**{cv}**",
                          "x": (ci + 1) * (CELL_W + GAP), "y": top,
                          "width": CELL_W, "height": HDR_H})
        for ri, rv in enumerate(row_vals):
            nodes.append({"id": node_id("hdr", mtype, "r", rv), "type": "text",
                          "text": f"**{rv}**",
                          "x": 0, "y": top + HDR_H + GAP + ri * (CELL_H + GAP),
                          "width": CELL_W, "height": CELL_H})
        # 內容格
        for ri, rv in enumerate(row_vals):
            for ci, cv in enumerate(col_vals):
                x0 = (ci + 1) * (CELL_W + GAP)
                y0 = top + HDR_H + GAP + ri * (CELL_H + GAP)
                rel = cell.get((rv, cv))
                if mtype in args.triangular and rel is None:
                    ref = orders.get(row_axis, row_vals)
                    if rv in ref and cv in ref and ref.index(cv) <= ref.index(rv):
                        continue  # 下三角＝無效組合，不放佔位
                if rel:
                    nodes.append({"id": node_id("file", rel), "type": "file",
                                  "file": rel, "x": x0, "y": y0,
                                  "width": CELL_W, "height": CELL_H})
                elif args.placeholders:
                    nodes.append({"id": node_id("ph", mtype, rv, cv),
                                  "type": "text",
                                  "text": f"（缺）{rv} × {cv}",
                                  "x": x0, "y": y0,
                                  "width": CELL_W, "height": CELL_H})
        w = (len(col_vals) + 1) * (CELL_W + GAP) - GAP
        h = HDR_H + GAP + len(row_vals) * (CELL_H + GAP) - GAP
        add_group(f"{mtype}（{row_axis} × {col_axis}）", 0, top, w, h)
        y = top + h + BLOCK_GAP

    canvas = {"nodes": nodes, "edges": []}
    args.out.write_text(json.dumps(canvas, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    n_files = sum(1 for n in nodes if n["type"] == "file")
    n_ph = sum(1 for n in nodes if n["type"] == "text" and n["text"].startswith("（缺）"))
    print(f"wrote {args.out}: {n_files} 張卡、{n_ph} 個佔位、{len(nodes)} nodes")
    for wmsg in warn:
        print(f"⚠ {wmsg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
