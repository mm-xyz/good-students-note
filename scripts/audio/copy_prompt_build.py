#!/usr/bin/env python3
"""scripts/audio/copy_prompt_build.py — 組裝集數文案 prompt(零 LLM)

    python3 scripts/audio/copy_prompt_build.py --session sessions/<slug> --ep 15

shared-material 模板(prompt_集數文案.md)+ session 的 copy_material.md +
cutplan 保留 block → sessions/<slug>/copy_prompt.md(貼給 agy/codex 即用)。

- 逐字稿每個發言段標 (hh:mm:ss) **final-cut 時間**(經 cut_map.json 換算,
  同一 src 同時住 🎬 集錦與正文時取 dst 較大的正文 range)。
- 🎬 集錦區的重複 block 不進逐字稿;章節標題織進逐字稿當分隔。
- 同講者連續 block 合併成一句,時間取段首。
- 跑文案的時機=MM 驗收 final_cut 之後(卡 #571;本腳本只組裝不呼叫引擎)。

前置:render_cut.py 先跑過(要 cut_map.json 與最新 cutplan);copy_material.md
由對話 agent 撰寫(該集章節+內容重點+金句紅線)。
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TEMPLATE = (PROJECT_ROOT / "shared-material" / "水星貓的生活實驗室_v2"
                    / "prompt_集數文案.md")
LINE_RE = re.compile(r"^- \[(x|X)\] (B\d{3,5}) \[([^\]]+)\] (?:\[([^\]]{1,20})\] )?(.*)$")
MUSIC_RE = re.compile(r"^##\s*🎵")
TEASER_RE = re.compile(r"^##\s*🎬")
CONFIG_RE = re.compile(r"^##\s*⚙")
CUT_RE = re.compile(r"^##\s*✂")     # 手動剪除標記,不是章節標題


def hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def build_transcript(sdir: Path) -> str:
    blocks = {b["id"]: b for b in
              json.loads((sdir / "cutplan.json").read_text(encoding="utf-8"))["blocks"]}
    cm = json.loads((sdir / "cut_map.json").read_text(encoding="utf-8"))
    ranges = cm["ranges"]
    # 語速加速過的成品:src 區間長度會被壓縮 tempo 倍才落到 dst
    # (配樂沒變速,但配樂不在 ranges 裡)。少除這一下,整份時間碼會越後面越飄。
    tempo = cm.get("tempo") or 1.0

    def to_dst(t: float) -> float | None:
        hits = [r["dst_start"] + (t - r["src_start"]) / tempo for r in ranges
                if r["src_start"] - 0.5 <= t <= r["src_end"] + 0.5]
        return max(hits) if hits else None

    lines: list[str] = []
    prev_spk = None
    clip = False
    for raw in (sdir / "cutplan.md").read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if TEASER_RE.match(s):
            clip = True
            continue
        if MUSIC_RE.match(s) or CONFIG_RE.match(s) or CUT_RE.match(s):
            clip = False
            continue
        if s.startswith("## "):
            clip = False
            lines.append(f"\n### {s[3:]}")
            prev_spk = None
            continue
        m = LINE_RE.match(s)
        if not m or clip:
            continue
        bid, spk = m.group(2), m.group(4) or "?"
        body = m.group(5).rsplit(" ← ", 1)[0].replace("~~", "")
        if spk == prev_spk:
            lines[-1] += body
        else:
            dst = to_dst(blocks[bid]["start"])
            ts = f"({hms(dst)}) " if dst is not None else ""
            lines.append(f"{ts}{spk}:{body}")
            prev_spk = spk
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="組裝集數文案 prompt")
    ap.add_argument("--session", required=True)
    ap.add_argument("--ep", required=True, help="集數(填進模板 {{集數}})")
    ap.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    args = ap.parse_args()

    sdir = Path(args.session).resolve()
    for need in ("cutplan.md", "cutplan.json", "cut_map.json", "copy_material.md"):
        if not (sdir / need).exists():
            sys.exit(f"[copy-prompt] FAIL: 缺 {need}"
                     + ("(先跑 render_cut.py)" if need == "cut_map.json" else
                        "(先寫該集素材)" if need == "copy_material.md" else ""))
    tpl = args.template.read_text(encoding="utf-8")
    out = (tpl.split("---", 1)[1].lstrip("\n")
           .replace("{{集數}}", args.ep)
           .replace("{{素材}}", (sdir / "copy_material.md").read_text(encoding="utf-8"))
           .replace("{{逐字稿}}", build_transcript(sdir)))
    dst = sdir / "copy_prompt.md"
    dst.write_text(
        f"# EP{args.ep} 集數文案 — 組裝完成的完整 prompt(貼給 agy/codex 即用)\n"
        "\n> 由 shared-material 模板+copy_material+cutplan 保留段組裝;"
        "逐字稿時間=final-cut 時間軸(經 cut_map 換算);"
        "跑的時機=MM 驗收 final_cut 之後。\n\n" + out, encoding="utf-8")
    print(f"[copy-prompt] ✅ {dst}({dst.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
