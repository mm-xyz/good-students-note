#!/usr/bin/env python3
"""
scripts/audio/resegment_srt.py — 把既有 session 的 transcript.srt 重切成 EP15 式短句

transcribe_local.py 已內建同一套斷句(新 session 不需要本腳本);這支是給
「已轉錄完、不想重跑 ASR」的 session 事後補切用(吃現成 words.json,零模型):
    python3 scripts/audio/resegment_srt.py --session sessions/<slug> [--gap 0.5]

- 原 transcript.srt 備份成 transcript.srt.bak-longsegs(已存在就不覆蓋)
- 切完要重跑下游:diarize --from-tracks(或 --apply-map)→ cutplan prepare
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, write_srt, split_words_to_phrases, rel

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def main():
    ap = argparse.ArgumentParser(description="transcript.srt 重切 EP15 式短句")
    ap.add_argument("--session", required=True)
    ap.add_argument("--gap", type=float, default=0.5,
                    help="字間停頓 ≥ 此秒數就斷句(預設 0.5)")
    args = ap.parse_args()

    session_dir = Path(args.session).resolve()
    srt_path = session_dir / "transcript.srt"
    words_path = session_dir / "words.json"
    if not srt_path.exists() or not words_path.exists():
        print(f"需要 transcript.srt + words.json 都在 {session_dir}", file=sys.stderr)
        sys.exit(1)

    import json
    cues = parse_srt(srt_path)
    words = json.loads(words_path.read_text(encoding="utf-8"))

    out = []
    wi = 0
    n_mismatch = 0
    for c in cues:
        ws = []
        while wi < len(words):
            mid = (words[wi]["start"] + words[wi]["end"]) / 2
            if mid > c["end"] and words[wi]["start"] >= c["end"]:
                break
            ws.append(words[wi])
            wi += 1
        phrases = split_words_to_phrases(ws, c["text"], gap=args.gap)
        if not phrases:
            out.append(dict(c))
            continue
        if "".join(p["text"] for p in phrases).replace(" ", "") \
                != c["text"].replace(" ", ""):
            n_mismatch += 1
        # 首尾沿用原 cue 邊界(與 diarize.split_cues_by_turns 同慣例)
        phrases[0]["start"] = c["start"]
        phrases[-1]["end"] = c["end"]
        for p in phrases:
            out.append({"start": p["start"], "end": p["end"], "text": p["text"],
                        "speaker": c.get("speaker")})

    bak = srt_path.with_name("transcript.srt.bak-longsegs")
    if not bak.exists():
        bak.write_bytes(srt_path.read_bytes())
    write_srt(out, srt_path)
    print(f"[resegment] {len(cues)} cues → {len(out)} 短句 cues → "
          f"{rel(srt_path, PROJECT_ROOT)}(備份 {bak.name};"
          f"word 重建與原文不符 {n_mismatch} 段)")
    print("[resegment] 下游要重跑:diarize --from-tracks(或 --apply-map)→ "
          "cutplan prepare")


if __name__ == "__main__":
    main()
