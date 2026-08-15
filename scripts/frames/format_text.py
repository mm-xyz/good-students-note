#!/usr/bin/env python3
"""Stage 2.6 — format：OCR 文字＋畫面一起進地端 VLM 重排版面。

用法：
  .venv/bin/python scripts/format_text.py <slug> [--min-chars 80] [--limit N] [--redo]

針對 text_source=ocr 且字數 ≥ min-chars 的幀（多欄/表格版面 OCR 行序會交錯），
把 OCR 逐行文字連同畫面丟給 VLM：字元以 OCR 為準（VLM 不用自己認字），
版面由 VLM 對照畫面重排——是表格就排成 Markdown 表格。成功後 text_source=ocr+vlm。
失敗保留 OCR 原文不動（資訊不丟，只是沒排版）。跑完記得卸模型：lms unload <model>。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import frames_workdir, load_config, load_manifest, save_manifest
from screen import call_vlm

FORMAT_PROMPT = """這是一張投影片畫面，附上它的 OCR 逐行文字（字元正確，但行序可能因多欄版面而交錯）。
請對照畫面版面，把 OCR 文字重排成結構化 Markdown：
- 多欄版面依欄分組，欄標題用粗體
- 是表格就排成 Markdown 表格
- 條列用「- 」；文字以 OCR 為準，不增刪、不改寫（明顯 OCR 錯字可依畫面修正）
- 忽略講者小窗、台標、背景牆雜訊
只回傳 JSON，不要其他文字：{"text": "重排後的 Markdown"}

OCR 逐行文字：
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--min-chars", type=int, default=80,
                    help="OCR 字數達標才值得排版（預設 80，短文字不需要）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--redo", action="store_true", help="含已排版（ocr+vlm）的幀重跑")
    args = ap.parse_args()

    cfg = load_config()
    if "LM_STUDIO_TOKEN" not in cfg:
        sys.exit("mars-cc/.env 找不到 LM_STUDIO_TOKEN")
    manifest = load_manifest(args.slug)
    sources = ("ocr",) if not args.redo else ("ocr", "ocr+vlm")
    todo = [f for f in manifest["frames"]
            if f.get("screen", {}).get("text_source") in sources
            and f["screen"].get("ocr_chars", 0) >= args.min_chars]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{args.slug}：待排版 {len(todo)} 張（model={cfg['LM_STUDIO_MODEL']}）")

    t0 = time.time()
    done = errors = 0
    for f in todo:
        path = frames_workdir(args.slug) / f["file"]
        try:
            out = call_vlm(cfg, path, FORMAT_PROMPT + f["screen"]["text"])
            text = str(out.get("text", "")).strip()
            if not text:
                raise ValueError("排版結果空白")
            # 守門：排版只准重排不准摘要——內容字數掉到 OCR 的六成以下視為吞字，拒收
            plain = len(text.replace("\n", "").replace("|", "").replace("-", "").replace(" ", ""))
            if plain < 0.6 * f["screen"].get("ocr_chars", 0):
                raise ValueError(f"排版吞字（{plain} 字 < OCR {f['screen']['ocr_chars']} 字的六成）")
            f["screen"]["text"] = text
            f["screen"]["text_source"] = "ocr+vlm"
            print(f"📐 {f['file']}  {len(text)} 字")
        except Exception as e:
            errors += 1
            print(f"⚠️ {f['file']}: {str(e)[:160]}（保留 OCR 原文）")
        done += 1
        save_manifest(args.slug, manifest)

    print(f"完成 {done} 張（{errors} 錯誤，{time.time()-t0:.0f}s）")
    print("提醒：批次跑完卸載模型 → lms unload " + cfg["LM_STUDIO_MODEL"])


if __name__ == "__main__":
    main()
