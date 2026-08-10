#!/usr/bin/env python3
"""
scripts/audio/tidy_session.py — podcast session 目錄分類(與 Google Drive 同構)

    python3 scripts/audio/tidy_session.py --session sessions/<slug> [--apply]
        [--label <mp3檔名>=<版本標籤> ...]

預設 dry-run,`--apply` 才真的搬。**只搬不刪**。

分類規則(ADR 0011「方案 A」:人看檔分類,管線工作檔留在 session 根):

    raw/        原始素材(補錄、電話音、未進管線的錄音);已對齊的管線分軌維持 tracks/
    _meta/      人看的伴隨檔:highlights / chapters / pipeline_run / 文案 / 封面 /
                生圖 prompt / gemma 提案 / 筆記標註
    _bak/       所有 *.bak-* 快照
    vNN_<時戳>[_標籤]/  每一版成品一個資料夾(mp3 + 該版 cutplan 快照 + render.txt)
    (根)        管線工作檔:source/audio16k/transcript*/words/prosody/cutplan/
                cut_map/speakers*/context — 這些檔名被 scripts/audio/*.py 與
                session.py 寫死,搬走就斷,故留在根。

為什麼不是完全鏡像 Drive:Drive 只放人看的東西,local 還要養管線工作檔。
硬把工作檔塞進 _work/ 要改約 15 處硬編碼路徑,風險遠大於收益(ADR 0011)。
"""

import argparse
import re
import shutil
import sys
import datetime as dt
from pathlib import Path

# 管線工作檔:留在 session 根,不准搬(scripts 寫死這些名字)
PIPELINE_KEEP = {
    "source.wav", "source.mp3", "source.m4a", "audio16k.wav",
    "transcript.srt", "transcript.speakers.srt", "words.json",
    "prosody.json", "cutplan.json", "cutplan.md", "cut_map.json",
    "speakers.json", "speakers_map.json", "context.txt",
    "corrections.json", "metadata.json", "pipeline_log.jsonl",
    "cleaned.md", "cleaned.srt", "transcript.cleaned.srt",
}
# 管線子目錄:不動
KEEP_DIRS = {"tracks", "images", "note", "frames", "raw", "_meta", "_bak"}

META_EXACT = {
    "highlights.md", "chapters.txt", "pipeline_run.md",
    "cutplan.gemma-proposal.md",
}
META_PATTERNS = [
    re.compile(r"^copy_.*\.md$"),
    re.compile(r"^ig_copy_.*\.md$"),
    re.compile(r"^gen_img_prompt_.*\.md$"),
    re.compile(r"^cover_.*\.(png|jpg|jpeg|webp)$"),
    re.compile(r"^.*_notes_(annotations\.json|preview\.png)$"),
]
BAK_RE = re.compile(r"\.bak-[^/]*$")
AUDIO_OUT_RE = re.compile(r"\.(mp3|m4a)$", re.I)
# 音樂素材(共用素材庫的複本)不是成品,別讓它佔掉一個版本編號
ASSET_RE = re.compile(r"^(opening|break|ending)[_.]", re.I)


def is_meta(name: str) -> bool:
    return name in META_EXACT or any(p.match(name) for p in META_PATTERNS)


def stamp(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y%m%d-%H%M")


def plan(sdir: Path, labels: dict[str, str]) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    renders: list[Path] = []

    for p in sorted(sdir.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            if p.name in KEEP_DIRS or re.match(r"^v\d+", p.name):
                continue
            continue
        if p.name in PIPELINE_KEEP:
            continue
        # 成品判定優先於 .bak-:`final_cut.bak-v2music.mp3` 是「初剪」那一版的
        # 成品,不是快照檔——丟進 _bak/ 會讓一整版成品從版本序列裡消失。
        if AUDIO_OUT_RE.search(p.name) and ASSET_RE.match(p.name):
            moves.append((p, sdir / "raw" / p.name))
        elif AUDIO_OUT_RE.search(p.name):
            renders.append(p)
        elif BAK_RE.search(p.name):
            moves.append((p, sdir / "_bak" / p.name))
        elif is_meta(p.name):
            moves.append((p, sdir / "_meta" / p.name))

    # 成品:依 mtime 排序,每版一個資料夾;標籤由 --label 給,沒給就用檔名 stem。
    # 同標籤的多個檔(例:同一次預聽的 normalized + stereo)歸同一個版本資料夾。
    renders.sort(key=lambda x: x.stat().st_mtime)
    vdirs: dict[str, Path] = {}
    for p in renders:
        label = labels.get(p.name, p.stem)
        if label not in vdirs:
            vdirs[label] = sdir / f"v{len(vdirs):02d}_{stamp(p)}_{label}"
        moves.append((p, vdirs[label] / p.name))
    return moves


def main() -> int:
    ap = argparse.ArgumentParser(description="podcast session 目錄分類(Drive 同構)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--apply", action="store_true", help="真的搬(預設 dry-run)")
    ap.add_argument("--label", action="append", default=[],
                    metavar="MP3=標籤", help="指定某支成品的版本標籤")
    args = ap.parse_args()

    sdir = Path(args.session)
    if not sdir.is_dir():
        print(f"[tidy] ✗ 找不到 session:{sdir}", file=sys.stderr)
        return 2

    labels = {}
    for kv in args.label:
        if "=" not in kv:
            print(f"[tidy] ✗ --label 要寫成 檔名=標籤:{kv}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        labels[k] = v

    moves = plan(sdir, labels)
    if not moves:
        print(f"[tidy] {sdir.name}:已經是分類後的樣子,沒有要搬的檔案。")
        return 0

    print(f"[tidy] {sdir.name}:{len(moves)} 個檔案要歸位"
          f"{'' if args.apply else '(dry-run,加 --apply 才真的搬)'}")
    for src, dst in moves:
        print(f"    {src.name}  →  {dst.relative_to(sdir)}")
        if args.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                print(f"    ⚠ 目的地已存在,跳過:{dst.relative_to(sdir)}")
                continue
            shutil.move(str(src), str(dst))
    if args.apply:
        print("[tidy] ✓ 完成(只搬不刪;管線工作檔留在根)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
