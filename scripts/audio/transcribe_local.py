#!/usr/bin/env python3
"""
scripts/audio/transcribe_local.py — 本地 ASR stage(mlx-whisper,全本地零雲端)

2026-07-27 MM 拍板:轉錄主線改本地 whisper,Groq 降為選配(--asr groq)。
用 .venv-audio 的 python 跑:
    .venv-audio/bin/python scripts/audio/transcribe_local.py <media> -o transcript.srt \
        [--context context.txt] [--language zh] [--model mlx-community/whisper-large-v3-turbo]

- Apple Silicon MLX 推理,39.5 分鐘音檔實測約 84s
- segment 級時間軸 → SRT(全域時間軸,不切 chunk)
- OpenCC s2twp 簡→繁台灣化(whisper 中文常出簡體)
- context 內容餵 initial_prompt(人名/專名辨識;與 Groq 線同一份 context.txt)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import sec_to_ts

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def main():
    ap = argparse.ArgumentParser(description="本地 mlx-whisper 轉錄 → SRT")
    ap.add_argument("media", help="音檔或影片路徑")
    ap.add_argument("-o", "--output", required=True, help="輸出 SRT 路徑")
    ap.add_argument("--context", help="context 檔路徑,內容餵 initial_prompt")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    import mlx_whisper
    import opencc

    kw = {}
    if args.context:
        ctx = Path(args.context)
        if ctx.exists():
            text = ctx.read_text(encoding="utf-8").strip()
            if text:
                # whisper prompt 窗口有限,取前 200 字(人名/專名放 context 開頭最有效)
                kw["initial_prompt"] = text[:200]

    print(f"[transcribe-local] {args.model} language={args.language} …")
    result = mlx_whisper.transcribe(
        args.media, path_or_hf_repo=args.model, language=args.language,
        verbose=False, **kw)

    cc = opencc.OpenCC("s2twp")
    blocks = []
    n = 0
    for seg in result["segments"]:
        text = cc.convert(seg["text"].strip())
        if not text:
            continue
        n += 1
        blocks.append(f"{n}\n{sec_to_ts(seg['start'])} --> {sec_to_ts(seg['end'])}\n{text}\n")
    if not blocks:
        print("[transcribe-local] ERROR: 零 segment 輸出", file=sys.stderr)
        sys.exit(1)
    Path(args.output).write_text("\n".join(blocks) + "\n", encoding="utf-8")
    print(f"[transcribe-local] {n} segments → {args.output}")


if __name__ == "__main__":
    main()
