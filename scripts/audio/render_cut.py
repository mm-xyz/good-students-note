#!/usr/bin/env python3
"""
scripts/audio/render_cut.py — 依人審後的 cutplan.md 全自動出片(ffmpeg,零 LLM)

    python3 scripts/audio/render_cut.py --session sessions/<slug> \
        [--out final_cut.m4a] [--snap-window 0.4] [--fade 0.015] [--dry-run]

流程:
    1. 解析 cutplan.md 勾選(markdown 是人審真相源)+ cutplan.json 時間碼
    2. 驗證:md 與 json 的 block 集合一致、每個保留 block 的文字須逐字存在於
       來源 SRT(poddeck 式防幻覺:LLM/人不可能「發明」一段不存在的話)
    3. 剪點 snap:邊界若落在 prosody.json 的靜音段 ±snap-window 內,移到靜音中點
       (避免切在字中間);相鄰保留段間隔 < 0.2s 自動併成連續範圍
    4. ffmpeg filter_complex(atrim + 15ms fade in/out + concat)從**原始檔**
       (source.<ext>,非 16k 分析檔)剪出 final_cut.m4a
    5. cutplan.md 的 `## 章節標題` 行 → chapters.txt(新時間軸)+ cut_map.json

.cutplan_pending.json 還在時拒跑(提案未完成);MM 人審是流程步驟,本腳本不驗
「是否審過」— 排程/自主模式下,叫 render 的人要自己遵守「人審完才 render」。
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, pick_transcript, fmt_mmss, sec_to_ts

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LINE_RE = re.compile(r"^- \[( |x|X)\] (B\d{3}) \[([^\]]+)\] (.*)$")
CHAPTER_RE = re.compile(r"^## (.+)$")


def parse_cutplan_md(path: Path) -> tuple[dict, dict, list[dict]]:
    """回傳 ({block_id: keep}, {block_id: md_text}, [{'block': id, 'title': str}])。

    md_text = 去掉 speaker 前綴與行尾 ` ← 理由` 的正文,供與 cutplan.json 比對。
    """
    keeps = {}
    texts = {}
    chapters = []
    pending_chapter = None
    for line in path.read_text(encoding="utf-8").splitlines():
        mc = CHAPTER_RE.match(line.strip())
        if mc and not line.startswith("## Cutplan"):
            pending_chapter = mc.group(1).strip()
            continue
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        mark, bid, body = m.group(1), m.group(2), m.group(4)
        keeps[bid] = mark.lower() == "x"
        body = body.rsplit(" ← ", 1)[0]
        body = re.sub(r"^\[[^\]]{1,20}\]\s*", "", body)  # speaker 前綴
        texts[bid] = body.strip()
        if pending_chapter is not None:
            chapters.append({"block": bid, "title": pending_chapter})
            pending_chapter = None
    return keeps, texts, chapters


def validate_blocks(blocks: list[dict], keeps: dict, md_texts: dict,
                    srt_text: str) -> None:
    """防幻覺/防手滑三道驗證:
    (1) md 與 json 的 block id 集合一致
    (2) md 每行正文 == json block 文字(提案/人審只准翻勾選,不准改字)
    (3) json block 文字逐字存在於來源 SRT(json 也不可竄改)
    """
    md_ids = set(keeps)
    json_ids = {b["id"] for b in blocks}
    if md_ids != json_ids:
        missing = json_ids - md_ids
        extra = md_ids - json_ids
        sys.exit(f"[render] FAIL: cutplan.md 與 cutplan.json block 不一致 "
                 f"(md 缺 {sorted(missing) or '無'} / md 多 {sorted(extra) or '無'})")
    flat = re.sub(r"\s+", "", srt_text)
    for b in blocks:
        jt = re.sub(r"\s+", "", b["text"])
        mt = re.sub(r"\s+", "", md_texts[b["id"]])
        if mt != jt:
            sys.exit(f"[render] FAIL: {b['id']} cutplan.md 文字與 cutplan.json 不符"
                     f"(被改過?)— cutplan 只准翻勾選/加理由/加章節,文字不可動")
        if keeps[b["id"]] and jt and jt not in flat:
            sys.exit(f"[render] FAIL: {b['id']} 文字不存在於來源 SRT(cutplan.json "
                     f"被竄改?)— 重跑 cutplan.py prepare 再提案")


def snap_boundaries(ranges: list[list[float]], silences: list[dict],
                    window: float) -> list[list[float]]:
    """每個範圍的頭尾若在靜音段 ±window 內,移到該靜音的中點。"""
    def snap(t: float) -> float:
        for s in silences:
            mid = (s["start"] + s["end"]) / 2
            if s["start"] - window <= t <= s["end"] + window:
                return mid
        return t
    return [[snap(a), snap(b)] for a, b in ranges]


def merge_ranges(ranges: list[list[float]], min_gap: float = 0.2) -> list[list[float]]:
    merged = []
    for a, b in sorted(ranges):
        if merged and a - merged[-1][1] < min_gap:
            merged[-1][1] = max(merged[-1][1], b)
        elif b > a:
            merged.append([a, b])
    return merged


def run_ffmpeg(src: Path, ranges: list[list[float]], out: Path, fade: float,
               loudnorm: str | None) -> None:
    parts = []
    labels = []
    for i, (a, b) in enumerate(ranges):
        dur = b - a
        f = min(fade, dur / 4)
        parts.append(
            f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={f:.3f},afade=t=out:st={max(0.0, dur - f):.3f}:d={f:.3f}[a{i}]")
        labels.append(f"[a{i}]")
    concat_out = "cat" if loudnorm else "out"
    parts.append(f"{''.join(labels)}concat=n={len(ranges)}:v=0:a=1[{concat_out}]")
    if loudnorm:
        # 音量一致化(EBU R128 動態 loudnorm):三人麥距/音量不同也拉齊,podcast 標準
        parts.append(f"[cat]loudnorm={loudnorm}[out]")
    script = out.parent / ".render_filter.txt"
    script.write_text(";\n".join(parts), encoding="utf-8")
    codec = ["-c:a", "aac", "-b:a", "192k"] if out.suffix != ".wav" else []
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-filter_complex_script", str(script), "-map", "[out]", *codec, str(out)]
    print(f"[render] ffmpeg {len(ranges)} ranges → {out.name}")
    subprocess.run(cmd, check=True)
    script.unlink()


def main():
    ap = argparse.ArgumentParser(description="cutplan → ffmpeg 全自動出片")
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", default="final_cut.m4a")
    ap.add_argument("--snap-window", type=float, default=0.4)
    ap.add_argument("--fade", type=float, default=0.015)
    ap.add_argument("--loudnorm", default="I=-16:TP=-1.5:LRA=11",
                    help="EBU R128 音量一致化參數(預設 podcast 標準 -16 LUFS);"
                         "傳空字串停用")
    ap.add_argument("--dry-run", action="store_true", help="只印剪輯範圍,不跑 ffmpeg")
    args = ap.parse_args()

    sdir = Path(args.session).resolve()
    if (sdir / ".cutplan_pending.json").exists():
        sys.exit("[render] FAIL: .cutplan_pending.json 還在 — 剪輯提案未完成,"
                 "先讓對話 agent 提案 + MM 人審 cutplan.md")
    cp = json.loads((sdir / "cutplan.json").read_text(encoding="utf-8"))
    keeps, md_texts, chapters = parse_cutplan_md(sdir / "cutplan.md")

    spk_srt = sdir / "transcript.speakers.srt"
    srt_src = spk_srt if spk_srt.exists() else pick_transcript(sdir)
    srt_text = "".join(c["text"] for c in parse_srt(srt_src))
    validate_blocks(cp["blocks"], keeps, md_texts, srt_text)

    kept = [b for b in cp["blocks"] if keeps[b["id"]]]
    cut = [b for b in cp["blocks"] if not keeps[b["id"]]]
    if not kept:
        sys.exit("[render] FAIL: 沒有任何保留 block")

    silences = []
    pj = sdir / "prosody.json"
    if pj.exists():
        silences = json.loads(pj.read_text(encoding="utf-8")).get("silences", [])
    else:
        print("[render] ⚠ prosody.json 不存在,剪點不 snap 靜音(可能切在字中間)")

    ranges = [[b["start"], b["end"]] for b in kept]
    ranges = merge_ranges(snap_boundaries(ranges, silences, args.snap_window))

    # 新時間軸換算(cut_map + chapters 用)
    cut_map = []
    acc = 0.0
    for a, b in ranges:
        cut_map.append({"src_start": round(a, 3), "src_end": round(b, 3),
                        "dst_start": round(acc, 3)})
        acc += b - a

    def to_new_time(t: float) -> float | None:
        for r in cut_map:
            if r["src_start"] <= t <= r["src_end"]:
                return r["dst_start"] + (t - r["src_start"])
        later = [r for r in cut_map if r["src_start"] > t]
        return later[0]["dst_start"] if later else None

    block_by_id = {b["id"]: b for b in cp["blocks"]}
    chap_lines = []
    for ch in chapters:
        blk = block_by_id.get(ch["block"])
        if blk and keeps.get(ch["block"]):
            nt = to_new_time(blk["start"])
            if nt is not None:
                chap_lines.append(f"{sec_to_ts(nt).replace(',', '.')} {ch['title']}")

    total_src = cp["blocks"][-1]["end"] if cp["blocks"] else 0
    removed = sum(b["end"] - b["start"] for b in cut)
    print(f"[render] 保留 {len(kept)}/{len(cp['blocks'])} blocks → {len(ranges)} ranges;"
          f"剪掉 {fmt_mmss(removed)},成品約 {fmt_mmss(acc)}(原 {fmt_mmss(total_src)})")
    if args.dry_run:
        for a, b in ranges:
            print(f"  keep {fmt_mmss(a)}–{fmt_mmss(b)}")
        return

    src = next(p for p in sorted(sdir.glob("source.*"))
               if p.suffix.lower() not in (".srt", ".md", ".json", ".txt"))
    out = sdir / args.out
    run_ffmpeg(src, ranges, out, args.fade, args.loudnorm or None)

    (sdir / "cut_map.json").write_text(json.dumps({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "final_duration_secs": round(acc, 3),
        "removed_secs": round(removed, 3),
        "ranges": cut_map,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if chap_lines:
        (sdir / "chapters.txt").write_text("\n".join(chap_lines) + "\n", encoding="utf-8")
        print(f"[render] chapters.txt: {len(chap_lines)} 章")
    print(f"[render] ✅ {out.name}({fmt_mmss(acc)})+ cut_map.json")


if __name__ == "__main__":
    main()
