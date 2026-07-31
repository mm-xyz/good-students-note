#!/usr/bin/env python3
"""Stage 2 — screen：地端 VLM（LM Studio）逐張審幀：留不留、分類、圖說、畫面文字。

用法：
  .venv/bin/python scripts/screen.py <slug> [--limit N] [--redo]

- 逐張寫回 manifest（atomic），中斷可續跑（已審過的跳過，--redo 全部重審）。
- 設計成半夜 unattended 跑：單張失敗記 error 繼續，不炸整批。
"""
import argparse
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (extract_json_from_message, frames_workdir, llm_chat,
                    load_config, load_manifest, save_manifest)

ENRICH_PROMPT = """這張畫面幀已確認要插進演講筆記。請把畫面上的文字內容**完整逐字抄錄**（不是摘要）：
- 保留條列/欄位結構，用換行與「- 」呈現階層
- 繁體中文照原文；圖示/裝飾忽略；講者小窗忽略
- 只回傳 JSON，不要其他文字，格式：{"text": "完整抄錄內容"}"""

PROMPT = """你在審一場繁體中文技術演講影片抽出的畫面幀，決定它值不值得插進演講筆記。

判準：
- 保留（keep=true）：投影片、demo 畫面、圖表、程式碼、現場展示的作品——任何「只聽逐字稿會漏掉」的資訊。
- 丟棄（keep=false）：講者特寫、觀眾席、過場黑幀、模糊晃動幀、跟前後重複的畫面。

只回傳 JSON，不要其他文字，格式：
{"keep": true/false, "kind": "slide|demo|chart|code|speaker|transition|other", "caption": "一句繁體中文圖說（15字內）", "text": "畫面上的重點文字，逐字抄錄，沒有就空字串"}"""


def call_vlm(cfg: dict, image_path: Path, prompt: str) -> dict:
    # HTTP 與 JSON 修復/reasoning_content 保底已抽進 common（#557），notes.py 共用文字版
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    msg = llm_chat(
        cfg,
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        # reasoning 模型（如 gemma-4 QAT）思考也吃這個額度，太低會 length 截斷、content 空白；
        # 文字密的投影片思考會超標，預設 6000、可用 .env 的 SCREEN_MAX_TOKENS 再加大
        max_tokens=int(cfg.get("SCREEN_MAX_TOKENS", "6000")),
    )
    return extract_json_from_message(msg)


def screen_frame(cfg: dict, image_path: Path) -> dict:
    out = call_vlm(cfg, image_path, PROMPT)
    if not isinstance(out.get("keep"), bool):
        raise ValueError(f"keep 欄位不是布林：{out}")
    return {
        "keep": out["keep"],
        "kind": str(out.get("kind", "other")),
        "caption": str(out.get("caption", "")).strip(),
        "text": str(out.get("text", "")).strip(),
    }


def enrich_frame(cfg: dict, image_path: Path) -> str:
    out = call_vlm(cfg, image_path, ENRICH_PROMPT)
    return str(out.get("text", "")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--limit", type=int, default=None, help="只處理前 N 張（spot check 用）")
    ap.add_argument("--redo", action="store_true", help="忽略既有結果全部重審")
    ap.add_argument("--enrich", action="store_true",
                    help="對已保留（keep）的幀補跑完整畫面文字逐字抄錄，更新 screen.text；"
                         "失敗保留原 text 不動")
    args = ap.parse_args()

    cfg = load_config()
    if "LM_STUDIO_TOKEN" not in cfg:
        sys.exit("mars-cc/.env 找不到 LM_STUDIO_TOKEN")
    manifest = load_manifest(args.slug)
    frames = manifest["frames"]
    if args.enrich:
        todo = [f for f in frames if f.get("screen", {}).get("keep")
                and (args.redo or not f["screen"].get("enriched"))]
        verb = "待抄錄"
    else:
        todo = [f for f in frames if args.redo or "screen" not in f]
        verb = "待審"
    if args.limit:
        todo = todo[: args.limit]
    print(f"{args.slug}：{verb} {len(todo)}/{len(frames)} 張（model={cfg['LM_STUDIO_MODEL']}）")

    t0 = time.time()
    done = errors = 0
    for f in todo:
        path = frames_workdir(args.slug) / f["file"]
        try:
            if args.enrich:
                text = enrich_frame(cfg, path)
                if text:
                    f["screen"]["text"] = text
                f["screen"]["enriched"] = True
                print(f"✍️ {f['file']}  {len(text)} 字")
            else:
                f["screen"] = screen_frame(cfg, path)
                f.pop("screen_error", None)
                mark = "✓ keep" if f["screen"]["keep"] else "  drop"
                print(f"{mark}  {f['file']}  [{f['screen']['kind']}] {f['screen']['caption']}")
        except Exception as e:
            if not args.enrich:
                f["screen_error"] = str(e)[:300]
            errors += 1
            print(f"⚠️ {f['file']}: {e}")
        done += 1
        save_manifest(args.slug, manifest)  # 逐張落盤，中斷不丟進度

    kept = sum(1 for f in frames if f.get("screen", {}).get("keep"))
    print(f"完成 {done} 張（{errors} 錯誤，{time.time()-t0:.0f}s）；"
          f"目前全片保留 {kept}/{len(frames)} 張")


if __name__ == "__main__":
    main()
