#!/usr/bin/env python3
"""
scripts/audio/finalize.py — 定稿之後把沒用到的收進 _archive/,根目錄留乾淨

    python3 scripts/audio/finalize.py --session sessions/<slug> --final v12 [--apply]
    python3 scripts/audio/finalize.py --session sessions/<slug> --restore [--apply]

預設 dry-run,`--apply` 才真的搬。**只搬不刪**,而且不碰原始錄音。

2026-09-11 MM:「定稿之後把沒用到的都收到 Archive folder,我想要每一集的根目錄
都是乾淨的」。

定稿後根目錄留什麼:

    <定稿版本目錄>/   vN_<時戳>[-AI]/ — 成品、cutplan 快照、render.txt、diff
    raw/              原始素材(補錄、音樂素材複本)
    tracks/           已對齊的管線分軌
    source.*          原始錄音 — **不搬不刪**

其餘一律進 `_archive/`:其他版本目錄、散落的 mp3/m4a、`_meta/`、`_bak/`、
文案草稿;管線工作檔另歸 `_archive/pipeline/`。

為什麼管線工作檔「定稿後」才收得走:`scripts/audio/*.py` 與 `session.py` 有
約 15 處寫死 `sessions/<slug>/words.json` 這種路徑(ADR 0011 據此否決過把工作檔
搬進 `_work/`)。那些硬編碼只在「還要剪」的時候需要生效——定稿＝不再剪,這時
收走不會斷任何東西。要重剪就 `--restore` 搬回來。

同一支也用在 Drive 集數資料夾(結構同構):
    python3 scripts/audio/finalize.py --session "<Drive 集數資料夾>" --final v12 --apply
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tidy_session import PIPELINE_KEEP, VER_DIR_RE  # noqa: E402

ARCHIVE = "_archive"
PIPELINE_SUB = "pipeline"
# 定稿後仍留在根的目錄(原始素材與管線分軌,不是產物)
KEEP_AT_ROOT = {"raw", "tracks", "images", "frames", "note", ARCHIVE}
# 原始錄音:不搬不刪
SOURCE_RE = re.compile(r"^source\.", re.I)


def _match_version(name: str, final: str) -> bool:
    """`v2` 只命中 `v2_...`,不可以誤命中 `v20_...`(比到分隔線為止)。"""
    return name == final or name.startswith(final + "_")


def plan_finalize(sdir: Path, final: str) -> list[tuple[Path, Path]]:
    vdirs = [p for p in sdir.iterdir() if p.is_dir() and VER_DIR_RE.match(p.name)]
    if not any(_match_version(p.name, final) for p in vdirs):
        print(f"[finalize] ✗ 找不到定稿版本 {final};這一集有:"
              + "、".join(sorted(p.name for p in vdirs) or ["(無)"]),
              file=sys.stderr)
        raise SystemExit(2)

    moves: list[tuple[Path, Path]] = []
    arc = sdir / ARCHIVE
    for p in sorted(sdir.iterdir()):
        if p.name.startswith(".") or p.name in KEEP_AT_ROOT:
            continue
        if p.is_dir():
            if VER_DIR_RE.match(p.name) and _match_version(p.name, final):
                continue                      # 定稿那一版留在根
            moves.append((p, arc / p.name))
        else:
            if SOURCE_RE.match(p.name):
                continue                      # 原始錄音不動
            dst = arc / PIPELINE_SUB if p.name in PIPELINE_KEEP else arc
            moves.append((p, dst / p.name))
    return moves


def plan_restore(sdir: Path) -> list[tuple[Path, Path]]:
    """把 `_archive/pipeline/` 的工作檔搬回根,讓這一集可以重剪。

    版本目錄刻意留在 `_archive/` — 重剪要的是工作檔,舊成品搬回來只會把根弄髒
    (要拿舊版自己去 `_archive/` 撈)。
    """
    src = sdir / ARCHIVE / PIPELINE_SUB
    if not src.is_dir():
        return []
    return [(p, sdir / p.name) for p in sorted(src.iterdir())]


def main() -> int:
    ap = argparse.ArgumentParser(description="定稿歸檔:根目錄只留定稿版與素材")
    ap.add_argument("--session", required=True,
                    help="session 目錄,或 Drive 集數資料夾(結構同構)")
    ap.add_argument("--final", help="定稿版本,例如 v12")
    ap.add_argument("--restore", action="store_true",
                    help="反向:把 _archive/pipeline/ 的工作檔搬回根(要重剪)")
    ap.add_argument("--apply", action="store_true", help="真的搬(預設 dry-run)")
    args = ap.parse_args()

    sdir = Path(args.session)
    if not sdir.is_dir():
        print(f"[finalize] ✗ 找不到:{sdir}", file=sys.stderr)
        return 2
    if args.restore == bool(args.final):
        print("[finalize] ✗ --final <版本> 與 --restore 二選一", file=sys.stderr)
        return 2

    moves = plan_restore(sdir) if args.restore else plan_finalize(sdir, args.final)
    verb = "搬回根" if args.restore else "收進 _archive/"
    if not moves:
        print(f"[finalize] {sdir.name}:沒有要{verb}的東西。")
        return 0

    print(f"[finalize] {sdir.name}:{len(moves)} 項要{verb}"
          f"{'' if args.apply else '(dry-run,加 --apply 才真的搬)'}")
    for src, dst in moves:
        print(f"    {src.name}  →  {dst.relative_to(sdir)}")
        if args.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                print(f"    ⚠ 目的地已存在,跳過:{dst.relative_to(sdir)}")
                continue
            src.rename(dst)
    if args.apply:
        print("[finalize] ✓ 完成(只搬不刪;原始錄音未動)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
