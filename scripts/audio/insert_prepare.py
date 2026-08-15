#!/usr/bin/env python3
"""
scripts/audio/insert_prepare.py — 把補錄音檔變成 cutplan 上可勾選的 block

    python3 scripts/audio/insert_prepare.py --session sessions/<slug> \
        --file raw/2_Sarah_補錄.WAV [--speaker Sarah] [--note "補錄說明"]

2026-08-10 MM:「補錄能不能也變成一樣的做法,基於逐字稿下去修,而不是直接整段
放進去」。整段塞進去的補錄是黑盒——人審看不到內容、不能剪贅字、不能改順序,
跟正片用 block 勾選的模式完全不一致。這支把補錄拉齊到同一個模式:

    1. 補錄自己的 SRT(沒有就先用 transcribe_local.py 產)→ 一 cue 一 block
    2. block id 用 `S` 前綴(S0001…),與正片 `B`/空白 `G` 分開命名空間;
       **時間碼是補錄檔自己的時間軸**,不是 source 的
    3. 寫進 cutplan.json 的 `inserts`(json=防幻覺驗證的真相源)
    4. 在 cutplan.md 的 `## ➕ <檔案>` 標頭底下插入這些 block 行

之後人審就跟剪正片一模一樣:改勾選=剪掉整句、加 `~~刪除線~~`=字級精剪。
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, fmt_mmss  # noqa: E402
from cutplan import detect_asr_artifact  # noqa: E402 — #675 同根因:補錄跟正片
# 一樣是 whisper ASR 轉出來的,一樣可能陷入重複迴圈,守門判準與正片共用一份。

INSERT_HDR_RE = re.compile(r"^##\s*➕\s*(\S+)")


def build_blocks(cues: list[dict], speaker: str) -> list[dict]:
    blocks = []
    for i, c in enumerate(cues, 1):
        text = c["text"].strip()
        if not text:
            continue
        reason = detect_asr_artifact(text)
        b = {"id": f"S{i:04d}", "start": round(c["start"], 3),
             "end": round(c["end"], 3), "text": text,
             "speaker": speaker, "keep": True,
             "asr_artifact": bool(reason), "asr_artifact_reason": reason or ""}
        if reason:
            b["reason"] = f"⚠ASR-artifact：{reason}"
        blocks.append(b)
    return blocks


def md_lines(blocks: list[dict]) -> list[str]:
    out = []
    for b in blocks:
        reason = f" ← {b['reason']}" if b.get("reason") else ""
        out.append(f"- [x] {b['id']} [{fmt_mmss(b['start'])}–{fmt_mmss(b['end'])}]"
                   f" [{b['speaker']}] {b['text']}{reason}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="補錄音檔 → cutplan 可勾選 block")
    ap.add_argument("--session", required=True)
    ap.add_argument("--file", required=True, help="補錄音檔(session 相對路徑)")
    ap.add_argument("--speaker", default="", help="講者名(預設從檔名猜)")
    ap.add_argument("--note", default="", help="`## ➕` 標頭後面的說明")
    ap.add_argument("--params", default="gain=auto",
                    help="`## ➕` 標頭的參數(預設 gain=auto)")
    args = ap.parse_args()

    sdir = Path(args.session)
    media = sdir / args.file
    if not media.exists():
        print(f"[insert] ✗ 找不到補錄檔:{media}", file=sys.stderr)
        return 2
    srt = media.with_suffix(".srt")
    if not srt.exists():
        print(f"[insert] ✗ 找不到 {srt.name} — 先跑:\n"
              f"    .venv-audio/bin/python scripts/audio/transcribe_local.py "
              f"{media} -o {srt} --context {sdir}/context.txt", file=sys.stderr)
        return 2

    speaker = args.speaker or (re.split(r"[_\W]", media.stem)[1]
                               if "_" in media.stem else media.stem)
    blocks = build_blocks(parse_srt(srt), speaker)
    if not blocks:
        print(f"[insert] ✗ {srt.name} 沒有任何 cue", file=sys.stderr)
        return 2

    # ── cutplan.json:inserts[檔案] = blocks(防幻覺驗證的 json 真相源)──
    cj = sdir / "cutplan.json"
    cp = json.loads(cj.read_text(encoding="utf-8"))
    inserts = {i["file"]: i for i in cp.get("inserts", [])}
    inserts[args.file] = {"file": args.file, "speaker": speaker,
                          "blocks": blocks}
    cp["inserts"] = list(inserts.values())
    cj.write_text(json.dumps(cp, ensure_ascii=False), encoding="utf-8")

    # ── cutplan.md:在 `## ➕ <檔案>` 標頭底下放 block 行 ──
    md = sdir / "cutplan.md"
    lines = md.read_text(encoding="utf-8").splitlines()
    hdr_i = next((i for i, l in enumerate(lines)
                  if (m := INSERT_HDR_RE.match(l.strip())) and m.group(1) == args.file),
                 None)
    if hdr_i is None:
        print(f"[insert] ✗ cutplan.md 找不到 `## ➕ {args.file}` 標頭 — "
              f"先把標頭放到你要插入的位置(它的行序=播放順序)", file=sys.stderr)
        return 2
    # 標頭底下既有的 S 行先清掉(重跑要冪等)
    end = hdr_i + 1
    while end < len(lines) and re.match(r"^- \[[ xX]\] S\d{4} ", lines[end].strip()):
        end += 1
    note = f"  {args.note}" if args.note else ""
    lines[hdr_i] = f"## ➕ {args.file} {args.params}{note}".rstrip()
    lines[hdr_i + 1:end] = md_lines(blocks)
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    total = sum(b["end"] - b["start"] for b in blocks)
    print(f"[insert] ✓ {args.file} → {len(blocks)} 個 block（{total:.1f}s，"
          f"講者 {speaker}）已寫進 cutplan.json/.md；現在可以像剪正片一樣"
          f"改勾選與加刪除線")
    return 0


if __name__ == "__main__":
    sys.exit(main())
