#!/usr/bin/env python3
"""Stage 2.5 — ocr：macOS Vision OCR 抄錄保留幀的畫面文字，帶篩選機制。

用法：
  .venv/bin/python scripts/ocr.py <slug> [--min-chars 40] [--max-chars 1200] [--redo]

篩選機制（每張 keep 幀三態分診）：
  ocr-full   OCR 字數 ≥ min-chars → screen.text 換成 OCR 全文（text_source=ocr）
  keep-vlm   OCR 字數 < min-chars（照片/demo 類）→ 保留 VLM 的描述不動
  ocr-fail   Vision 丟例外 → 保留原 text，列出來；要更好的抄錄可對該幀跑 screen.py --enrich

比起 VLM 逐字抄錄（每張 20–60s、吃 GPU 記憶體），Vision OCR 每張 ~0.2s、繁中準、
不用載模型——先 OCR 決定，VLM 只留給 OCR 讀不出來的版面。
"""
import argparse
import re
import sys
from pathlib import Path

import Foundation
import Vision

sys.path.insert(0, str(Path(__file__).parent))
from common import frames_workdir, load_manifest, save_manifest

# 講者小窗/台標常駐畫面右側，OCR 會撿到背景牆的雜訊字——右緣起點超過這條線的觀測值丟棄
PIP_X_CUTOFF = 0.78

# 雜訊行（MM 定的分診：亂碼/slogan/桌面雜訊 remove）——substring 不分大小寫
NOISE_SUBSTR = ["generative ai", "生成式 ai 年會小聚", "google translate",
                "q 搜尋", "進入全螢幕", "进入全屏", "分享"]
NOISE_RE = [
    re.compile(r"^[\d\s:/,.+×xX%°C\-①-⑩④]+$"),      # 純數字/符號（頁碼、時間、× 鈕）
    re.compile(r"^(上午|下午)\s?\d|\d+°C"),            # 工作列時間/氣溫
    re.compile(r"多雲|多雰|時陰|時晴|降雨機率"),        # 天氣列
    re.compile(r"^第?\s?\d+\s?[页頁]$|^\d+\s?/\s?\d+$"),  # 簡報頁碼
    re.compile(r"[a-z0-9.-]+\.(com|ai|io|net|org)/\S{6,}"),  # 長網址（瀏覽器網址列）
]


def is_noise(line: str) -> bool:
    low = line.lower()
    if any(s in low for s in NOISE_SUBSTR):
        return True
    if any(r.search(line) for r in NOISE_RE):
        return True
    if len(line) == 1:
        return True
    if len(line) == 2 and not re.fullmatch(r"[一-鿿]{2}", line):
        return True
    return False


def detect_qr(path: Path, x_cutoff: float = PIP_X_CUTOFF) -> list[str]:
    """Vision 條碼偵測：解出 QR payload（網址等）。x_cutoff 濾掉講者小窗區的 QR。"""
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNDetectBarcodesRequest.alloc().init()
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return []
    out = []
    for obs in req.results() or []:
        box = obs.boundingBox()
        payload = obs.payloadStringValue()
        if payload and box.origin.x + box.size.width / 2 <= x_cutoff and payload not in out:
            out.append(payload)
    return out


def ocr_image(path: Path, pip_cutoff: float = PIP_X_CUTOFF,
              crop_heuristic: bool = True) -> str:
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["zh-Hant", "en-US"])
    req.setUsesLanguageCorrection_(True)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(str(err))
    obs_list = []
    for obs in req.results() or []:
        box = obs.boundingBox()
        if box.origin.x > pip_cutoff:
            continue
        cand = obs.topCandidates_(1)
        if cand and cand[0].confidence() >= 0.3:
            text = cand[0].string().strip().lstrip("•。·．◦▪ ")
            if text:
                obs_list.append((box.origin.x, 1.0 - box.origin.y, text))
    total = len(obs_list)
    obs_list = [o for o in obs_list if not is_noise(o[2])]
    # 雜訊比例高＝桌面/瀏覽器截圖 → 只留中央區（chrome 分頁列/書籤列/工作列都貼邊緣）
    if crop_heuristic and total and (total - len(obs_list)) / total >= 0.35:
        obs_list = [o for o in obs_list if 0.18 <= o[0] <= pip_cutoff and 0.12 <= o[1] <= 0.9]
    # 近重複去重（瀏覽器多分頁同標題、跑馬字）：前 10 字相同只留第一筆
    seen, deduped = set(), []
    for o in obs_list:
        key = re.sub(r"\s+", "", o[2])[:10]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(o)
    return layout_text(deduped)


def layout_text(obs_list: list[tuple]) -> str:
    """欄位聚類：多欄 slide 直接排成 Markdown 表格（欄當欄、y 對齊列），單欄照列序。

    依左緣 x 聚類（間隙 > 0.10 斷欄）；至少兩欄、每欄至少 3 行、且欄與欄
    垂直重疊才視為多欄版面——否則退回（列, x）排序，避免把縮排誤判成欄。
    """
    if not obs_list:
        return ""
    xs = sorted({round(x, 3) for x, _, _ in obs_list})
    clusters, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] > 0.10:
            clusters.append(cur)
            cur = [x]
        else:
            cur.append(x)
    clusters.append(cur)

    if len(clusters) >= 2:
        cols = [[o for o in obs_list if c[0] <= round(o[0], 3) <= c[-1]] for c in clusters]
        spans = [(min(y for _, y, _ in col), max(y for _, y, _ in col)) for col in cols if col]
        overlap = all(a[0] < b[1] and b[0] < a[1] for a, b in zip(spans, spans[1:]))
        if all(len(col) >= 3 for col in cols) and overlap:
            return columns_to_table(cols)

    obs_list.sort(key=lambda o: (round(o[1], 2), o[0]))  # 由上而下、同列由左而右
    return "\n".join(o[2] for o in obs_list)


def columns_to_table(cols: list[list[tuple]]) -> str:
    """欄 → Markdown 表格。列用 y 座標分箱對齊（同列容差 0.025），首列當表頭。"""
    ys = sorted(y for col in cols for _, y, _ in col)
    bins, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] > 0.025:
            bins.append((cur[0], cur[-1]))
            cur = [y]
        else:
            cur.append(y)
    bins.append((cur[0], cur[-1]))

    rows = []
    for lo, hi in bins:
        cells = []
        for col in cols:
            hit = sorted((o for o in col if lo <= o[1] <= hi), key=lambda o: (o[1], o[0]))
            cells.append(" ".join(o[2] for o in hit).replace("|", "／"))
        rows.append(cells)
    header, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(":---" for _ in cols) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--min-chars", type=int, default=40,
                    help="OCR 字數達標才覆寫 text（預設 40，低於視為照片/demo 留 VLM 描述）")
    ap.add_argument("--max-chars", type=int, default=1200,
                    help="抄錄上限，超過截斷加 …（預設 1200）")
    ap.add_argument("--redo", action="store_true",
                    help="text_source=ocr 的幀重跑（吃新版排版邏輯）；ocr+vlm 的幀永遠不動")
    args = ap.parse_args()

    manifest = load_manifest(args.slug)
    # 有 region＝幀在 extract 時已裁到 deck 區，PIP 濾與中央裁切不再需要
    has_region = bool(manifest.get("region"))
    cutoff = 1.0 if has_region else PIP_X_CUTOFF
    keeps = [f for f in manifest["frames"] if f.get("screen", {}).get("keep")]
    todo = [f for f in keeps
            if f["screen"].get("text_source") not in ("ocr", "ocr+vlm", "mermaid")
            or (args.redo and f["screen"].get("text_source") == "ocr")]
    print(f"{args.slug}：OCR {len(todo)}/{len(keeps)} 張 keep 幀"
          + ("（region 已裁切）" if has_region else ""))

    # QR 辨識：所有 keep 幀都掃（獨立於 text_source，已掃過且非 --redo 就跳過）
    qr_hits = 0
    for f in keeps:
        if "qr" in f["screen"] and not args.redo:
            continue
        f["screen"]["qr"] = detect_qr(frames_workdir(args.slug) / f["file"], cutoff)
        qr_hits += len(f["screen"]["qr"])
    save_manifest(args.slug, manifest)
    if qr_hits:
        print(f"🔗 QR 解出 {qr_hits} 筆")

    stats = {"ocr-full": 0, "keep-vlm": 0, "ocr-fail": 0}
    for f in todo:
        path = frames_workdir(args.slug) / f["file"]
        try:
            text = ocr_image(path, cutoff, crop_heuristic=not has_region)
        except Exception as e:
            stats["ocr-fail"] += 1
            print(f"⚠️ ocr-fail {f['file']}: {e}（保留原 text，可用 screen.py --enrich 補）")
            continue
        n = len(text.replace("\n", ""))
        f["screen"]["ocr_chars"] = n
        if n >= args.min_chars:
            if len(text) > args.max_chars:
                text = text[: args.max_chars] + "…"
            f["screen"]["text"] = text
            f["screen"]["text_source"] = "ocr"
            stats["ocr-full"] += 1
            print(f"✍️ ocr-full {f['file']}  {n} 字")
        else:
            if f["screen"].get("text_source") == "ocr":  # 之前跑進過 OCR 全文、這輪降級→清殘文
                f["screen"]["text"] = ""
                f["screen"].pop("text_source", None)
            stats["keep-vlm"] += 1
            print(f"   keep-vlm {f['file']}  {n} 字（照片/demo，留 VLM 描述）")
        save_manifest(args.slug, manifest)

    print(f"分診：ocr-full {stats['ocr-full']} / keep-vlm {stats['keep-vlm']} / "
          f"ocr-fail {stats['ocr-fail']}")


if __name__ == "__main__":
    main()
