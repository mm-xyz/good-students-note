#!/usr/bin/env python3
"""figures.py — PDF 圖表/流程圖/框架圖確定性渲染器（Phase-1，本地、免費、0 token）。

這支腳本「不做 AI 判讀」。它只負責一件事：把 PDF 裡的內嵌圖表（點陣圖）、
含向量繪圖的整頁（流程圖/框架圖多半是這種畫法），以及整頁掃描頁，
渲染成 PNG，落進指定的 session 目錄，供既有的 scripts/describe_images.py
（看圖描述、會呼叫 LLM 的那層）續接消費。

分工：
  • figures.py         → PDF「圖」的抽取渲染（本檔，確定性、0 token）
  • describe_images.py → 對 session 目錄裡的 PNG 逐張「看圖描述」（LLM，見該檔頭部）
兩者合起來才是「PDF → 看圖理解」的完整管線；本檔絕不呼叫 LLM/VLM。

渲染邏輯移植自 mars-cc 000_Agent/skills/doc-vlm-to-md/scripts/vlm_prep.py
（extract_figures / extract_vector_chart_pages / is_scanned_pdf），適配成
describe_images.py 吃得下的扁平檔名（session 目錄底下、非 assets/ 巢狀子夾）。

用法:
    python3 scripts/doc/figures.py <input.pdf> --session <session_dir> \
        [--min-size N] [--dpi N]

輸出:
    <session_dir>/images/fig_p<頁碼3位>_<序2位>.png  — 渲染出的圖
        （落在 images/ 子目錄，對齊 scripts/session.py 的 --images 慣例；
        describe_images.py:245-248 掃圖時「有 images/ 就只掃 images/」，
        圖若落在 session 根會被那條規則靜默漏掃，故不可落根目錄）
    <session_dir>/doc_figures.json                    — manifest(list[dict]，
        metadata 不是圖，留在 session 根；file 欄位含 "images/" 前綴，
        對應 describe_images.py 內部記錄的 rel path 慣例）
    stdout 最後一行：{"figures": N, "total_pages": M, "scanned_pages": K}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        fitz = None  # type: ignore


# ════════════════════════════════════════════════════════════════════════════
#  常數（原樣沿襲 vlm_prep.py:73-76 的四維篩選門檻，別再收斂成單一維度——
#   2000×130 這種橫幅/分隔線較小邊可能 ≥128，但寬/高/面積/長寬比任一項不合格
#   仍要濾掉，否則下游 describe_images.py 對它多花一次 VLM 成本）
# ════════════════════════════════════════════════════════════════════════════
MIN_IMG_WIDTH = 250           # 內嵌圖寬度下限（--min-size 未指定時的預設）
MIN_IMG_HEIGHT = 200          # 內嵌圖高度下限（--min-size 未指定時的預設）
MIN_IMG_AREA = 80000          # 內嵌圖面積下限（px²，恆固定，不受 --min-size 覆寫）
MAX_ASPECT_RATIO = 8          # 長寬比上限（恆固定，擋橫幅/分隔線，不受 --min-size 覆寫）
DEFAULT_DPI = 150             # 整頁渲染解析度（vector-page / scanned-page）
VECTOR_DRAW_THRESHOLD = 25    # 單頁向量物件數 ≥ 此值 → 判定為「向量圖表頁」整頁渲染
SCANNED_CHARS_PER_PAGE = 60   # 平均每頁可抽取文字 < 此值 → 判定為掃描/圖像型 PDF


def passes_figure_filter(w0: int, h0: int, min_size: int | None) -> bool:
    """四維篩選（沿襲 vlm_prep.py extract_figures 的門檻）：
    寬 / 高 / 面積 / 長寬比 全部合格才保留（非裝飾小圖、非橫幅/分隔線）。
    --min-size 若有指定，覆寫『寬、高』這兩維共用同一個下限值（較小邊override
    入口）；面積與長寬比恆用來源常數，不受 --min-size 影響。"""
    width_floor = MIN_IMG_WIDTH if min_size is None else min_size
    height_floor = MIN_IMG_HEIGHT if min_size is None else min_size
    if w0 < width_floor or h0 < height_floor:
        return False
    if w0 * h0 < MIN_IMG_AREA:
        return False
    if max(w0, h0) / max(min(w0, h0), 1) > MAX_ASPECT_RATIO:
        return False
    return True


class FiguresError(Exception):
    """輸入不合法（非 PDF / 找不到檔案 / 缺 PyMuPDF）時拋出，CLI 層轉成非零 exit。"""


# ════════════════════════════════════════════════════════════════════════════
#  PDF：判斷掃描 vs 文字
# ════════════════════════════════════════════════════════════════════════════
def is_scanned_pdf(doc) -> bool:
    """回傳這份 PDF 是否為掃描/圖像型（抽不出文字，需整頁當圖處理）。"""
    sample = min(len(doc), 10)
    if sample == 0:
        return False
    total_chars = sum(len(doc[i].get_text("text").strip()) for i in range(sample))
    avg = total_chars / sample
    return avg < SCANNED_CHARS_PER_PAGE


# ════════════════════════════════════════════════════════════════════════════
#  規劃階段：只決定「哪些頁 / 哪些內嵌圖」要渲染，不做實際渲染
#  （續跑時檔名已存在的項目可以跳過渲染，規劃與渲染分離讓這件事容易做對）
# ════════════════════════════════════════════════════════════════════════════
def plan_items(doc, min_size: int | None) -> tuple[list[dict], bool]:
    """回傳 (planned_items, scanned)。
    每個 planned item: {"page": 1-based 頁碼, "page_index": 0-based, "kind": ..., "xref": 選填}
    kind ∈ embedded | vector-page | scanned-page。
    """
    scanned = is_scanned_pdf(doc)
    planned: list[dict] = []

    if scanned:
        for i in range(len(doc)):
            planned.append({"page": i + 1, "page_index": i, "kind": "scanned-page"})
        return planned, scanned

    # 內嵌點陣圖（沿襲 vlm_prep.extract_figures 的篩選：用內嵌圖原始尺寸過濾裝飾小圖）
    for page_index in range(len(doc)):
        page = doc[page_index]
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                w0, h0 = int(img_info[2]), int(img_info[3])
            except Exception:
                continue
            if not passes_figure_filter(w0, h0, min_size):
                continue
            planned.append({
                "page": page_index + 1,
                "page_index": page_index,
                "kind": "embedded",
                "xref": xref,
            })

    # 向量圖表頁（沿襲 vlm_prep.extract_vector_chart_pages）：
    # 已經有內嵌圖被抓走的頁不重複整頁渲染。
    skip_pages = {it["page_index"] for it in planned}
    for page_index in range(len(doc)):
        if page_index in skip_pages:
            continue
        try:
            n_draw = len(doc[page_index].get_drawings())
        except Exception:
            n_draw = 0
        if n_draw >= VECTOR_DRAW_THRESHOLD:
            planned.append({
                "page": page_index + 1,
                "page_index": page_index,
                "kind": "vector-page",
            })

    # 頁碼由小到大排序；同頁內先 embedded 後 vector-page（理論上同頁不會兩者並存，
    # 因 vector-page 偵測已排除 embedded 命中的頁，這裡排序只是求輸出穩定好讀）。
    planned.sort(key=lambda it: (it["page"], 0 if it["kind"] == "embedded" else 1))
    return planned, scanned


# ════════════════════════════════════════════════════════════════════════════
#  渲染階段：給定一個 planned item，算出 pixmap + bbox
# ════════════════════════════════════════════════════════════════════════════
def render_item(doc, item: dict, render_scale: float):
    page = doc[item["page_index"]]

    if item["kind"] == "embedded":
        xref = item["xref"]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        if rects:
            # 白底渲染（page.get_pixmap clip=rect）：模擬 PDF viewer 合成頁面白底，
            # 修正透明 PNG 被 flatten 成黑底的問題（沿襲 vlm_prep v1.0.2 修法）。
            rect = rects[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale), clip=rect)
            bbox = [rect.x0, rect.y0, rect.x1, rect.y1]
        else:
            # 找不到圖在頁面上的位置 → 退回直接取內嵌 bitmap
            pix = fitz.Pixmap(doc, xref)
            if pix.colorspace and pix.colorspace.n > 3:   # CMYK → RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            bbox = [0, 0, pix.width, pix.height]
        return pix, bbox

    # vector-page / scanned-page：整頁渲染
    pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale))
    rect = page.rect
    bbox = [rect.x0, rect.y0, rect.x1, rect.y1]
    return pix, bbox


# ════════════════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════════════════
def process(input_path: str, session_dir: str, min_size: int | None = None,
           dpi: int = DEFAULT_DPI) -> dict:
    """跑完整渲染流程，回傳 stats dict {"figures": N, "total_pages": M, "scanned_pages": K}。
    非法輸入（非 PDF / 檔案不存在 / 缺 PyMuPDF）一律拋 FiguresError。"""
    if fitz is None:
        raise FiguresError("需要 PyMuPDF（pip install PyMuPDF），找不到 fitz 模組")

    in_path = Path(input_path)
    if in_path.suffix.lower() != ".pdf":
        raise FiguresError(
            f"figures.py 只支援 PDF 輸入（EPUB/TXT 無圖表概念）：收到 {in_path.suffix or '(無副檔名)'}"
        )
    if not in_path.exists():
        raise FiguresError(f"找不到輸入檔：{in_path}")

    out_dir = Path(session_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 落 images/ 子目錄，對齊 scripts/session.py:504 的 --images 慣例，且讓
    # describe_images.py:245-248「有 images/ 就只掃 images/」的邏輯撿得到。
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(in_path))
    try:
        total_pages = len(doc)
        planned, _scanned = plan_items(doc, min_size)
        render_scale = dpi / 72.0
        scanned_pages = sum(1 for it in planned if it["kind"] == "scanned-page")

        manifest: list[dict] = []
        seq_per_page: dict[int, int] = {}

        for item in planned:
            page = item["page"]
            seq_per_page[page] = seq_per_page.get(page, 0) + 1
            seq = seq_per_page[page]
            filename = f"fig_p{page:03d}_{seq:02d}.png"
            filepath = images_dir / filename

            pix, bbox = render_item(doc, item, render_scale)
            if not filepath.exists():
                pix.save(str(filepath))
            # 已存在則跳過落盤（續跑不重複產），但 manifest 仍照當次規劃重建，
            # 確保 doc_figures.json 永遠反映「這份 PDF 現在該有的圖」而不是逐次疊加。

            manifest.append({
                "file": f"images/{filename}",
                "page": page,
                "kind": item["kind"],
                "bbox": [round(v, 2) for v in bbox],
            })

        manifest_path = out_dir / "doc_figures.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return {
            "figures": len(manifest),
            "total_pages": total_pages,
            "scanned_pages": scanned_pages,
        }
    finally:
        doc.close()


# ════════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(
        description="figures.py — PDF 圖表確定性渲染器（Phase-1，本地、免費、0 token，不呼叫 LLM）"
    )
    ap.add_argument("input", help="PDF 檔路徑")
    ap.add_argument("--session", required=True, help="輸出 session 目錄")
    ap.add_argument("--min-size", type=int, default=None,
                    help=f"覆寫內嵌圖『寬、高』下限(px，兩者共用同一值；預設寬"
                         f"{MIN_IMG_WIDTH}/高{MIN_IMG_HEIGHT})。面積下限"
                         f"({MIN_IMG_AREA}px²)與長寬比上限({MAX_ASPECT_RATIO})恆固定、"
                         f"不受此參數覆寫，用來擋橫幅/分隔線")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                    help=f"整頁渲染解析度（向量圖表頁/掃描頁，預設 {DEFAULT_DPI}）")
    args = ap.parse_args()

    try:
        stats = process(args.input, args.session, args.min_size, args.dpi)
    except FiguresError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1

    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
