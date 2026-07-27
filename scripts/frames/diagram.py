#!/usr/bin/env python3
"""Stage 2.7 — diagram：圖解類幀（kind=chart）用地端 VLM 轉 mermaid。

用法：
  .venv/bin/python scripts/diagram.py <slug> [--kinds chart] [--limit N] [--redo]

手繪/低對比圖解 OCR 只會出亂碼，但 VLM 讀得懂結構——轉成 mermaid 後 Obsidian
直接渲染。流程：Pillow autocontrast 增強 → VLM 產 mermaid → 語法守門
（flowchart/graph 開頭、節點數 ≥3）→ screen.text 換成 mermaid fence、
text_source=mermaid（ocr.py/format_text.py 都不會再動它）。失敗保留原 text。
跑完卸模型：lms unload <model>。
"""
import argparse
import re
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).parent))
from common import frames_workdir, load_config, load_manifest, save_manifest
from screen import call_vlm

MERMAID_PROMPT = """這是演講投影片裡的一張概念圖/流程圖（可能是手繪、低對比）。
請把它轉成 mermaid flowchart：
- 讀出每個框/節點的文字（手寫字盡力辨識，讀不出的節點用「?」）
- 箭頭方向照原圖；有分群就用 subgraph
- 節點 id 用 n1、n2…，文字放中括號標籤
- 只回傳 JSON，不要其他文字：{"mermaid": "flowchart TD\\n  n1[...] --> n2[...]", "note": "一句話說明這張圖在講什麼"}"""


def to_mermaid(cfg: dict, image_path: Path) -> tuple[str, str]:
    with Image.open(image_path) as im:
        boosted = ImageOps.autocontrast(im.convert("RGB"), cutoff=1)
        tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
        boosted.save(tmp, quality=90)
    try:
        out = call_vlm(cfg, tmp, MERMAID_PROMPT)
    finally:
        tmp.unlink(missing_ok=True)
    code = str(out.get("mermaid", "")).strip()
    note = str(out.get("note", "")).strip()
    if not re.match(r"^(flowchart|graph)\b", code):
        raise ValueError(f"不是 mermaid flowchart：{code[:80]}")
    if len(re.findall(r"\w+\[", code)) < 3:
        raise ValueError("節點少於 3 個，疑似沒讀懂")
    return code, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--kinds", default="chart", help="逗號分隔的目標 kind（預設 chart）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    if "LM_STUDIO_TOKEN" not in cfg:
        sys.exit("mars-cc/.env 找不到 LM_STUDIO_TOKEN")
    kinds = set(args.kinds.split(","))
    manifest = load_manifest(args.slug)
    todo = [f for f in manifest["frames"]
            if f.get("screen", {}).get("keep") and f["screen"].get("kind") in kinds
            and (args.redo or f["screen"].get("text_source") != "mermaid")]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{args.slug}：待轉圖 {len(todo)} 張（model={cfg['LM_STUDIO_MODEL']}）")

    t0 = time.time()
    errors = 0
    for f in todo:
        path = frames_workdir(args.slug) / f["file"]
        try:
            code, note = to_mermaid(cfg, path)
            f["screen"]["text"] = (note + "\n" if note else "") + f"```mermaid\n{code}\n```"
            f["screen"]["text_source"] = "mermaid"
            print(f"🧜 {f['file']}  {len(code)} 字 mermaid")
        except Exception as e:
            errors += 1
            print(f"⚠️ {f['file']}: {str(e)[:160]}（保留原 text）")
        save_manifest(args.slug, manifest)

    print(f"完成 {len(todo)} 張（{errors} 錯誤，{time.time()-t0:.0f}s）")
    print("提醒：批次跑完卸載模型 → lms unload " + cfg["LM_STUDIO_MODEL"])


if __name__ == "__main__":
    main()
