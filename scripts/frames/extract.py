#!/usr/bin/env python3
"""frames Stage 1 — extract：ffmpeg 場景變化偵測抽候選幀＋dHash 去重。

（2026-07-28 自 invisible-context 併入，改吃 session 容器）

用法：
  python3 scripts/frames/extract.py --session sessions/<slug> \
      [--scene 0.06] [--min-gap 1.5] [--dedup 2] [--region x0,y0,x1,y1]

session 模式：影片=source.<ext>、SRT 自動選 transcript.speakers.srt >
cleaned.srt > transcript.srt。產出 sessions/<slug>/frames/img/*.jpg +
sessions/<slug>/frames/manifest.json。也可用 legacy 位置參數直接給影片路徑。
"""
import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (SESSIONS_DIR, dhash, ffprobe_duration, frames_workdir,
                    hamming, load_config, save_manifest, slugify)

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")


def resolve_session(session: Path) -> tuple[Path, Path | None, str]:
    """session 目錄 → (video, srt, slug)。"""
    video = next((p for p in sorted(session.glob("source.*"))
                  if p.suffix.lower() in VIDEO_EXTS), None)
    if not video:
        sys.exit(f"{session} 沒有影片（source.mp4/mov/…）— frames 線只吃影片")
    srt = next((session / n for n in
                ("transcript.speakers.srt", "cleaned.srt", "transcript.srt")
                if (session / n).exists()), None)
    return video.resolve(), srt, session.name


def run_scene_detect(video: Path, frames_dir: Path, threshold: float,
                     region: tuple | None = None) -> list[tuple[float, Path]]:
    """跑 ffmpeg select=scene，回傳 [(pts_time, jpg_path)]。

    region=(x0,y0,x1,y1) 正規化座標：先裁切 deck 區再偵測——講者移動/台標
    完全不觸發抽幀、也不進輸出幀（固定版面錄影如 AI 小聚適用）。
    另外永遠抓 t=1s 的開場幀（片頭/講題通常在這）。
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_dir / "raw_%05d.jpg")
    vf = f"select='gt(scene,{threshold})',showinfo"
    if region:
        x0, y0, x1, y1 = region
        vf = (f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}," + vf)
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video),
         "-vf", vf,
         "-fps_mode", "vfr", "-q:v", "3", pattern],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"ffmpeg 失敗：\n{proc.stderr[-2000:]}")
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]
    raws = sorted(frames_dir.glob("raw_*.jpg"))
    if len(times) != len(raws):
        print(f"⚠️ showinfo 時間戳 {len(times)} 筆 vs 幀 {len(raws)} 張，取 min 對齊")
    pairs = list(zip(times, raws))

    opening = frames_dir / "raw_00000.jpg"
    opening_cmd = ["ffmpeg", "-hide_banner", "-ss", "1", "-i", str(video)]
    if region:
        x0, y0, x1, y1 = region
        opening_cmd += ["-vf", f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}"]
    subprocess.run(opening_cmd + ["-frames:v", "1", "-q:v", "3", str(opening)],
                   capture_output=True, text=True)
    if opening.exists():
        pairs.insert(0, (1.0, opening))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path, nargs="?",
                    help="（legacy）影片路徑；建議改用 --session")
    ap.add_argument("--session", type=Path,
                    help="sessions/<slug> 目錄（影片/SRT 自動解析）")
    ap.add_argument("--srt", type=Path, default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--speaker", default=None)
    ap.add_argument("--scene", type=float, default=None)
    ap.add_argument("--min-gap", type=float, default=None)
    ap.add_argument("--dedup", type=int, default=None)
    ap.add_argument("--region", default=None,
                    help="deck 區正規化座標 x0,y0,x1,y1（如 AI 小聚 0.016,0.028,0.792,0.82）；"
                         "裁切後才偵測，講者/台標不進幀也不觸發抽幀")
    args = ap.parse_args()

    cfg = load_config()
    scene = args.scene if args.scene is not None else float(cfg["SCENE_THRESHOLD"])
    min_gap = args.min_gap if args.min_gap is not None else float(cfg["MIN_GAP_SEC"])
    dedup_max = args.dedup if args.dedup is not None else int(cfg["DEDUP_HAMMING"])

    if args.session:
        session = args.session.expanduser().resolve()
        if not session.is_dir():
            sys.exit(f"session 不存在：{session}")
        video, srt, slug = resolve_session(session)
        title = args.title or slug
    elif args.video:
        video = args.video.expanduser().resolve()
        if not video.exists():
            sys.exit(f"找不到影片：{video}")
        srt = args.srt.expanduser().resolve() if args.srt else video.with_suffix(".srt")
        title = args.title or video.stem
        slug = slugify((args.speaker + "-" if args.speaker else "") + title)
        (SESSIONS_DIR / slug).mkdir(parents=True, exist_ok=True)
    else:
        sys.exit("要嘛給 --session sessions/<slug>，要嘛給影片路徑")

    region = tuple(float(v) for v in args.region.split(",")) if args.region else None
    frames_dir = frames_workdir(slug) / "img"
    pairs = run_scene_detect(video, frames_dir, scene, region)
    print(f"場景偵測（threshold={scene}）：{len(pairs)} 張候選")

    # min-gap ＋ dHash 去重，過關的改名成時間戳檔名
    kept, hashes, last_t = [], [], -1e9
    for t, raw in pairs:
        if t - last_t < min_gap:
            raw.unlink()
            continue
        h = dhash(raw)
        if hashes and min(hamming(h, prev) for prev in hashes) <= dedup_max:
            raw.unlink()
            continue
        name = f"t{int(t):05d}_{int((t % 1) * 10)}.jpg"
        final = frames_dir / name
        raw.rename(final)
        kept.append({"file": f"img/{name}", "t": round(t, 1), "dhash": f"{h:016x}"})
        hashes.append(h)
        last_t = t

    manifest = {
        "video": str(video),
        "srt": str(srt) if srt and srt.exists() else None,
        "title": title,
        "speaker": args.speaker,
        "slug": slug,
        "duration": ffprobe_duration(video),
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "region": list(region) if region else None,
        "params": {"scene": scene, "min_gap": min_gap, "dedup": dedup_max},
        "frames": kept,
    }
    save_manifest(slug, manifest)
    print(f"去重後留 {len(kept)} 張 → sessions/{slug}/frames/img/")
    print(f"manifest → sessions/{slug}/frames/manifest.json")
    if not manifest["srt"]:
        print("⚠️ 找不到 SRT，compose 前要先補（whisper-transcribe 可代產）")


if __name__ == "__main__":
    main()
