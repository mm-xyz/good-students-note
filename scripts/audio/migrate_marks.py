#!/usr/bin/env python3
"""
scripts/audio/migrate_marks.py — 把舊版 cutplan 的 ~~刪除線~~ 遷移到重生成的 cutplan.md

cutplan 重生成(重切斷句/重跑 prepare)後 block 結構全變,已標好的 Gemma 贅字
刪除線不能硬對 block id 搬——本腳本把兩版的 block 文字攤平成字元流,用
difflib 對齊後逐 span 移植(跨 block 的 span 自動按新 block 邊界拆開;
對不上的 span 丟棄並回報,不硬搬)。EP16 首例的手工流程固化版:
    python3 scripts/audio/migrate_marks.py --session sessions/<slug> \
        --old sessions/<slug>/cutplan.md.bak-longsegs

只搬 ~~刪除線~~;舊版的勾選/理由/章節不搬(人審狀態請照
feedback:podcast-cutplan-drive-copy 用 diff 對回來)。
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import rel

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BLOCK_RE = re.compile(r"^(- \[[ x]\] B\d{4} \[[^\]]+\] \[[^\]]+\] )(.*)$")
MARK_RE = re.compile(r"~~(.+?)~~")


def parse_blocks(md_lines: list[str]):
    """回傳 [(line_idx, prefix, plain_text, suffix)],以及全文字元流+每 block
    在流中的起點。text 部分的 ~~ 已剝掉;` ← 理由` 尾註切進 suffix。"""
    blocks = []
    stream = []
    offset = 0
    spans = []  # 舊檔用:字元流座標的刪除線區間
    for i, line in enumerate(md_lines):
        m = BLOCK_RE.match(line)
        if not m:
            continue
        prefix, body = m.groups()
        body, sep, reason = body.partition(" ← ")
        suffix = sep + reason if sep else ""
        plain = []
        pos = 0
        cur = 0  # plain 內游標
        for mk in MARK_RE.finditer(body):
            plain.append(body[pos:mk.start()])
            cur += mk.start() - pos
            spans.append((offset + cur, offset + cur + len(mk.group(1))))
            plain.append(mk.group(1))
            cur += len(mk.group(1))
            pos = mk.end()
        plain.append(body[pos:])
        plain = "".join(plain)
        blocks.append({"line": i, "prefix": prefix, "text": plain,
                       "suffix": suffix, "start": offset})
        stream.append(plain)
        offset += len(plain)
    return blocks, "".join(stream), spans


def map_spans(spans, old_stream, new_stream):
    """difflib 對齊兩條字元流,把舊 span 映到新流座標。任何字元落在非
    完全相同區段 → 該 span 整段丟棄(寧缺勿錯)。回傳 (mapped, n_drop)。"""
    sm = difflib.SequenceMatcher(None, old_stream, new_stream, autojunk=False)
    matches = sm.get_matching_blocks()
    mapped = []
    n_drop = 0
    for s, e in spans:
        parts = []
        covered = 0
        for m in matches:
            lo, hi = max(s, m.a), min(e, m.a + m.size)
            if lo < hi:
                parts.append((m.b + (lo - m.a), m.b + (hi - m.a)))
                covered += hi - lo
        if covered == e - s and parts:
            mapped.extend(parts)
        else:
            n_drop += 1
    return mapped, n_drop


def main():
    ap = argparse.ArgumentParser(description="cutplan ~~刪除線~~ 跨版遷移")
    ap.add_argument("--session", required=True)
    ap.add_argument("--old", required=True, help="帶刪除線的舊版 cutplan.md 路徑")
    args = ap.parse_args()

    session_dir = Path(args.session).resolve()
    new_path = session_dir / "cutplan.md"
    old_lines = Path(args.old).read_text(encoding="utf-8").splitlines()
    new_lines = new_path.read_text(encoding="utf-8").splitlines()

    _, old_stream, old_spans = parse_blocks(old_lines)
    new_blocks, new_stream, existing = parse_blocks(new_lines)
    if existing:
        print(f"新 cutplan 已有 {len(existing)} 處刪除線,不重複遷移", file=sys.stderr)
        sys.exit(1)
    if not old_spans:
        print("舊 cutplan 沒有刪除線,無事可做")
        return

    mapped, n_drop = map_spans(old_spans, old_stream, new_stream)

    # span 按新 block 邊界拆開、逐 block 由後往前插 ~~
    n_inserted = 0
    for b in new_blocks:
        b_end = b["start"] + len(b["text"])
        local = sorted((max(s, b["start"]) - b["start"], min(e, b_end) - b["start"])
                       for s, e in mapped if s < b_end and e > b["start"])
        text = b["text"]
        for s, e in reversed(local):
            if s >= e:
                continue
            text = text[:e] + "~~" + text[e:]
            text = text[:s] + "~~" + text[s:]
            n_inserted += 1
        new_lines[b["line"]] = b["prefix"] + text + b["suffix"]

    note = (f"> 🤖 Gemma 贅字預標已從舊版 cutplan 遷移(字元流對齊;原 "
            f"{len(old_spans)} 處 → 落到新 block {n_inserted} 段,對不上丟棄 "
            f"{n_drop} 處)。粗篩提案,人審為準,錯標直接刪掉刪除線即可。")
    # 標題(# 開頭)後插遷移註記;舊註記行(> 🤖 開頭)若已存在就替換
    for i, line in enumerate(new_lines):
        if line.startswith("> 🤖"):
            new_lines[i] = note
            break
    else:
        h1 = next((i for i, l in enumerate(new_lines) if l.startswith("# ")), 0)
        new_lines[h1 + 1:h1 + 1] = ["", note]

    new_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"[migrate-marks] {len(old_spans)} spans → 移植 {n_inserted} 段"
          f"(丟棄 {n_drop})→ {rel(new_path, PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
