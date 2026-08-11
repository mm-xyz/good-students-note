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
DEFAULT_TEMPLATE = (PROJECT_ROOT / "shared-material" / "水星貓的生活實驗室_v1"
                    / "prompt_集數文案.md")
# `B`=正片 block、`S`=`## ➕` 外部補錄插入的逐句 block(ADR 0011)。
# 補錄也是節目內容,漏收會讓文案缺一整段(2026-08-12 EP16 實踩:Sarah 的
# 36.9 秒補錄整段沒進 copy_prompt.md)。
LINE_RE = re.compile(r"^- \[(x|X)\] ([BS]\d{3,5}) \[([^\]]+)\] (?:\[([^\]]{1,20})\] )?(.*)$")
MUSIC_RE = re.compile(r"^##\s*🎵")
TEASER_RE = re.compile(r"^##\s*🎬")
CONFIG_RE = re.compile(r"^##\s*⚙")
CUT_RE = re.compile(r"^##\s*✂")     # 手動剪除標記,不是章節標題
INSERT_RE = re.compile(r"^##\s*➕")  # 外部補錄插入標記,不是章節標題(ADR 0011)


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
        if (MUSIC_RE.match(s) or CONFIG_RE.match(s) or CUT_RE.match(s)
                or INSERT_RE.match(s)):
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
            # 補錄(S)的時間碼長在**補錄檔自己的時間軸**上(0 起算),拿去查
            # cut_map 會對出離譜位置(diff_clips.py 踩過:補錄在成品 22:33
            # 卻被切在 0:04)。沒有可靠錨點就不掰時間戳,內容照樣要進去。
            dst = None if bid.startswith("S") else to_dst(blocks[bid]["start"])
            ts = f"({hms(dst)}) " if dst is not None else ""
            lines.append(f"{ts}{spk}:{body}")
            prev_spk = spk
    return "\n".join(lines)


def plan_sequence(sdir: Path) -> list[tuple[str, str]]:
    """cutplan 的保留內容依播出順序 → [(講者, 文字), ...],**不帶時間**。

    只有講者歸屬是 cutplan 獨有的知識(逐軌歸屬/diarize);時間交給成品逐字稿。
    """
    seq: list[tuple[str, str]] = []
    clip = False
    for raw in (sdir / "cutplan.md").read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if TEASER_RE.match(s):
            clip = True
            continue
        if (MUSIC_RE.match(s) or CONFIG_RE.match(s) or CUT_RE.match(s)
                or INSERT_RE.match(s)):
            clip = False
            continue
        if s.startswith("## "):
            clip = False
            continue
        m = LINE_RE.match(s)
        if not m or clip:
            continue
        body = m.group(5).rsplit(" ← ", 1)[0].replace("~~", "")
        seq.append((m.group(4) or "?", body))
    return seq


_PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)


def attach_speakers(cues: list[dict], seq: list[tuple[str, str]]) -> list[dict]:
    """成品逐字稿的 cue ＋ cutplan 的講者序列 → 每個 cue 掛上講者。

    2026-08-12 MM:「應該是要拿最新的定稿再去轉逐字稿一次吧」。從 cutplan 的
    原始時間軸經 cut_map ＋ tempo 回推成品時間,是一條會漂的推導鏈 —— 補錄
    根本不在那條時間軸上(實測 luna 把它標在 21:10,真實位置 21:51,差 41 秒)。
    改成:**時間以成品逐字稿為準**(那是聽眾真正會聽到的東西),cutplan 只
    提供它獨有的知識——誰在講。

    對齊法:把 cutplan 文字接成一條字流(每個字記得它屬於誰),用一個只往前走
    的指標,在字流裡找每個 cue 開頭幾個字的位置,取該段字的多數講者。ASR 與
    cutplan 的用字會有出入,所以找不到就沿用前一位,不硬猜。
    """
    stream, owner = [], []
    for spk, text in seq:
        t = _PUNCT_RE.sub("", text)
        stream.append(t)
        owner += [spk] * len(t)
    flat = "".join(stream)
    out, p, last = [], 0, seq[0][0] if seq else "?"
    for c in cues:
        t = _PUNCT_RE.sub("", c.get("text", ""))
        spk = last
        if t:
            k = min(6, len(t))
            q = flat.find(t[:k], p, p + 600)
            if q < 0:                              # 放寬:整份找一次最近的
                q = flat.find(t[:k], p)
            if q >= 0:
                span = owner[q:q + max(1, len(t))]
                if span:
                    spk = max(set(span), key=span.count)
                p = q + max(1, int(len(t) * 0.9))
        out.append({**c, "speaker": spk})
        last = spk
    return out


def build_transcript_from_final(sdir: Path, final_srt: Path) -> str:
    """成品逐字稿(真實成品時間)＋ cutplan 的講者 → 文案用逐字稿。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from srt_utils import parse_srt
    cues = attach_speakers(parse_srt(final_srt), plan_sequence(sdir))
    lines: list[str] = []
    prev = None
    for c in cues:
        body = c["text"].strip()
        if not body:
            continue
        if c["speaker"] == prev:
            lines[-1] += body
        else:
            lines.append(f"({hms(c['start'])}) {c['speaker']}:{body}")
            prev = c["speaker"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="組裝集數文案 prompt")
    ap.add_argument("--session", required=True)
    ap.add_argument("--ep", required=True, help="集數(填進模板 {{集數}})")
    ap.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    ap.add_argument("--final-srt", type=Path,
                    help="**定稿成品**的逐字稿(對 final mp3 重轉)。給了就以它的"
                         "時間為準,cutplan 只提供講者 —— 不再從原始時間軸經 "
                         "cut_map/tempo 回推(那條鏈會漂,補錄更是不在上面)")
    args = ap.parse_args()

    sdir = Path(args.session).resolve()
    need_all = ("cutplan.md", "cutplan.json", "cut_map.json", "copy_material.md")
    for need in (need_all[:2] + need_all[3:] if args.final_srt else need_all):
        if not (sdir / need).exists():
            sys.exit(f"[copy-prompt] FAIL: 缺 {need}"
                     + ("(先跑 render_cut.py)" if need == "cut_map.json" else
                        "(先寫該集素材)" if need == "copy_material.md" else ""))
    if args.final_srt and not args.final_srt.exists():
        sys.exit(f"[copy-prompt] FAIL: 找不到成品逐字稿 {args.final_srt}")
    tpl = args.template.read_text(encoding="utf-8")
    out = (tpl.split("---", 1)[1].lstrip("\n")
           .replace("{{集數}}", args.ep)
           .replace("{{素材}}", (sdir / "copy_material.md").read_text(encoding="utf-8"))
           .replace("{{逐字稿}}",
                    build_transcript_from_final(sdir, args.final_srt)
                    if args.final_srt else build_transcript(sdir)))
    src = ("**定稿成品重轉的逐字稿**(時間即成品時間,講者取自 cutplan)"
           if args.final_srt else "cutplan 保留段(時間經 cut_map 換算)")
    dst = sdir / "copy_prompt.md"
    dst.write_text(
        f"# EP{args.ep} 集數文案 — 組裝完成的完整 prompt(貼給 agy/codex 即用)\n"
        f"\n> 由 shared-material 模板+copy_material+{src} 組裝;"
        "跑的時機=MM 驗收 final_cut 之後。\n\n" + out, encoding="utf-8")
    print(f"[copy-prompt] ✅ {dst}({dst.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
