#!/usr/bin/env python3
"""hardsub_ocr — 燒錄字幕影片：每秒取樣 → Vision OCR 抄底部字幕 ＋ 抓字幕以外的畫面文字（投影片/標題卡）。

用法：
  .venv-audio/bin/python scripts/frames/hardsub_ocr.py <video> -o <outdir> [--fps 1] [--band 0.80]

產物（<outdir>/）：
  hardsub.srt      去重後的字幕行（時間＝取樣秒；比 ASR 準，專名/英文照畫面）
  hardsub.txt      純文字（一行一句，連續重複合併）
  screen_text.md   字幕帶以外出現的畫面文字（>=8 字的時間點，連續相同合併）；空檔＝畫面無投影片文字
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import Foundation
import Vision


def ocr(path: Path):
    """回傳 [(text, x0, y_center_from_top, x1)]（Vision 座標 y 由下往上，已翻轉）"""
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    h = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    r = Vision.VNRecognizeTextRequest.alloc().init()
    r.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    r.setRecognitionLanguages_(["zh-Hant", "en-US"])
    ok, _ = h.performRequests_error_([r], None)
    out = []
    if not ok:
        return out
    for o in r.results() or []:
        c = o.topCandidates_(1)
        if not c:
            continue
        b = o.boundingBox()
        out.append((str(c[0].string()), b.origin.x, 1 - (b.origin.y + b.size.height / 2),
                    b.origin.x + b.size.width))
    return out


def ts(sec):
    ms = int(sec * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def norm(s):
    return re.sub(r"[\s，。、！？,.!?：:；;「」『』（）()]", "", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--band", type=float, default=0.80, help="字幕帶起點（畫面高度比例）")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["ffmpeg", "-v", "error", "-i", a.video, "-vf", f"fps={a.fps},scale=1280:-2",
                        "-q:v", "3", f"{td}/f%06d.jpg"], check=True)
        frames = sorted(Path(td).glob("f*.jpg"))
        subs, screens = [], []  # (t, text)
        for i, f in enumerate(frames):
            t = i / a.fps
            items = ocr(f)
            band = [x for x in items if x[2] >= a.band and not re.fullmatch(r"[A-Z0-9 .,'&-]{6,}", x[0])]
            band.sort(key=lambda x: x[1])
            sub = "".join(x[0] for x in band).strip()
            other = [x[0] for x in items if x[2] < a.band and not (x[1] < 0.12 and x[2] < 0.15)]
            subs.append((t, sub))
            if sum(len(norm(x)) for x in other) >= 8:
                screens.append((t, " / ".join(other)))

    # 字幕：連續相同（正規化後）合併成一條 cue
    cues = []
    for t, s in subs:
        n = norm(s)
        if not n:
            continue
        if cues and norm(cues[-1][2]) == n and t - cues[-1][1] <= 2.5 / a.fps:
            cues[-1][1] = t + 1 / a.fps
        else:
            cues.append([t, t + 1 / a.fps, s])
    (out / "hardsub.srt").write_text("\n".join(
        f"{k}\n{ts(c[0])} --> {ts(c[1])}\n{c[2]}\n" for k, c in enumerate(cues, 1)), encoding="utf-8")
    (out / "hardsub.txt").write_text("\n".join(c[2] for c in cues) + "\n", encoding="utf-8")

    # 畫面文字：連續相同合併
    blocks = []  # 3 秒內的連續取樣併成一段，OCR 逐幀有雜訊，留最長（最完整）的一版
    for t, s in screens:
        if blocks and t - blocks[-1][1] <= 3:
            blocks[-1][1] = t
            if len(s) > len(blocks[-1][2]):
                blocks[-1][2] = s
        else:
            blocks.append([t, t, s])
    md = ["# 畫面文字（字幕帶以外）", ""] + [
        f"- [{int(b[0]) // 60:02d}:{int(b[0]) % 60:02d}-{int(b[1]) // 60:02d}:{int(b[1]) % 60:02d}] {b[2]}"
        for b in blocks]
    if not blocks:
        md.append("（無：整集為講者畫面，沒有投影片或標題卡文字）")
    (out / "screen_text.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"[hardsub] {len(frames)} 秒取樣 → {len(cues)} 句字幕、{len(blocks)} 段畫面文字 → {out}")


if __name__ == "__main__":
    sys.exit(main())
