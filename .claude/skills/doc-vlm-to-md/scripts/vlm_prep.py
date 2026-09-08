#!/usr/bin/env python3
"""
vlm_prep.py  v1.0.3
─────────────────────────────────────────────────────────────────────────────
VLM-to-MD｜Phase 1：本地視覺前處理（免費、離線、0 token）

這支腳本「不做 AI 判讀」。它只負責一件事：
把任何視覺輸入（掃描 PDF / 圖表 PDF / 一疊圖片 / 單張截圖）
變成「Claude 看得懂的圖檔」＋「一份留好空格的 Markdown 骨架」。

真正的「看圖說話」交給 Claude Desktop 內建視覺能力（Phase 2），不需要 API key。

設計沿襲使用者 v7.0 RAG 流程的兩個核心：
  1. 智能圖片篩選（MIN 250x200px, 80K area, aspect<8）— 濾掉 logo / 裝飾條
  2. blockquote 圖表描述格式（類型 + 2-3 句內容）

v1.0.1 變更（重要）：
  修正「向量圖表被漏掉」的破口。get_images() 只抓得到「嵌入的點陣圖」，
  但麥肯錫／顧問報告的 Exhibit 多是「用向量畫出來」的圖表，會被靜默漏掉。
  新增 extract_vector_chart_pages()：用 get_drawings() 的向量物件數偵測「圖表頁」，
  整頁渲染補回。figures / auto(文字型) 模式預設啟用；--no-vector-pages 可關閉。

v1.0.2 變更（重要）：
  修正「圖變黑底」的破口。透明 PNG 被 PDF flatten 成黑底，舊的 extract_image() 照單全收。
  extract_figures() 預設改用 page.get_image_rects(xref)+get_pixmap(clip=rect) 白底渲染
  （模擬 PDF viewer，黑底/alpha 問題消失，解析度可調）；需原始 bitmap 用 --raw-figures。

與 doc-to-md 的分工：
  • doc-to-md  → 文字型 PDF/EPUB/TXT 的「文字」
  • vlm-to-md  → 掃描 PDF / 圖片 / 圖表 的「視覺」
  兩者合起來＝完整的 RAG 知識庫進料管線。
─────────────────────────────────────────────────────────────────────────────
"""

import argparse
import datetime
import json
import os
import re
import sys

# === Windows 中文輸出修正（v: cp1252 fix）===
# Windows 預設 stdout 編碼為 cp1252，輸出中文（argparse --help / log 進度）會 UnicodeEncodeError。
# 強制 stdout/stderr 改 UTF-8（被導向 >/dev/null 或檔案時尤其重要）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── PyMuPDF（PDF 渲染與圖片提取）──────────────────────────────────────────────
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    try:
        import pymupdf as fitz
        HAS_FITZ = True
    except ImportError:
        HAS_FITZ = False

# ── Pillow（讀取獨立圖片尺寸，選用）────────────────────────────────────────────
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ════════════════════════════════════════════════════════════════════════════
#  常數（沿襲 v7.0 圖片篩選門檻）
# ════════════════════════════════════════════════════════════════════════════
MIN_IMG_WIDTH = 250
MIN_IMG_HEIGHT = 200
MIN_IMG_AREA = 80000
MAX_ASPECT_RATIO = 8          # 過濾極端長寬比（橫幅 / 分隔線 / logo）

PAGE_RENDER_DPI = 150         # 整頁渲染解析度（掃描頁 / 投影片 / 資訊圖）
SCANNED_CHARS_PER_PAGE = 60   # 平均每頁可抽取文字 < 此值 → 判定為掃描/圖像 PDF
VECTOR_DRAW_THRESHOLD = 25    # v1.2.0：40→25，補回較簡單的向量圖表（實測麥肯錫漏抽 6 張靠 25 補回）

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


# ════════════════════════════════════════════════════════════════════════════
#  小工具
# ════════════════════════════════════════════════════════════════════════════
def safe_stem(path: str) -> str:
    """檔名 → 檔案系統安全字串（給 assets 子資料夾用）"""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^\w一-鿿\-]+", "_", stem).strip("_")
    return stem or "vlm"


def log(msg: str):
    print(msg, flush=True)


# ════════════════════════════════════════════════════════════════════════════
#  PDF：判斷掃描 vs 文字
# ════════════════════════════════════════════════════════════════════════════
def is_scanned_pdf(doc) -> tuple:
    """回傳 (是否掃描, 平均每頁字數)。
    掃描/圖像 PDF 抽不出文字 → 走整頁渲染；文字 PDF → 只抽圖表。"""
    total_chars = 0
    sample = min(len(doc), 10)
    for i in range(sample):
        total_chars += len(doc[i].get_text("text").strip())
    avg = total_chars / max(sample, 1)
    return avg < SCANNED_CHARS_PER_PAGE, avg


# ════════════════════════════════════════════════════════════════════════════
#  PDF：整頁渲染成 PNG（掃描頁 / 投影片 / 資訊圖）
# ════════════════════════════════════════════════════════════════════════════
def render_pages(doc, stem: str, assets_dir: str, dpi: int) -> list:
    out_dir = os.path.join(assets_dir, stem)
    os.makedirs(out_dir, exist_ok=True)
    items = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    for i in range(len(doc)):
        pix = doc[i].get_pixmap(matrix=matrix)
        fn = f"page_{i + 1:03d}.png"
        fp = os.path.join(out_dir, fn)
        pix.save(fp)
        items.append({
            "kind": "page",
            "page": i + 1,
            "filename": fn,
            "img_path": fp,
            "rel_path": f"assets/{stem}/{fn}",
            "size": f"{pix.width}x{pix.height}",
        })
        log(f"   🖼️  渲染第 {i + 1}/{len(doc)} 頁 → {fn}")
    return items


# ════════════════════════════════════════════════════════════════════════════
#  PDF：智能提取嵌入圖表（沿襲 v7.0 extract_figures）
# ════════════════════════════════════════════════════════════════════════════
def extract_figures(doc, stem: str, assets_dir: str,
                    raw: bool = False, render_scale: float = 2.0) -> list:
    """抽嵌入圖表。
    預設（raw=False，v1.0.2 新做法）：用 page.get_image_rects(xref) 找到圖在頁面上的位置，
      再 page.get_pixmap(clip=rect) **渲染該區域**——頁面白底會一起合成，修正
      「透明 PNG 被 PDF flatten 成黑底（alpha 損壞）」的問題，呈現與 Preview/Adobe Reader 一致；
      解析度可由 render_scale 調（如 2x）。
    raw=True（舊做法）：直接取原始內嵌 bitmap（可能黑底），用於需要原始圖檔時。
    篩選一律用內嵌圖的原始尺寸（濾掉 logo / 裝飾條）。"""
    out_dir = os.path.join(assets_dir, stem)
    os.makedirs(out_dir, exist_ok=True)
    figures = []
    count = 0
    for page_num in range(len(doc)):
        page = doc[page_num]
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                # 用內嵌圖原始尺寸做篩選（get_images full=True：index 2,3 = width,height）
                w0, h0 = int(img_info[2]), int(img_info[3])
                if w0 < MIN_IMG_WIDTH or h0 < MIN_IMG_HEIGHT or w0 * h0 < MIN_IMG_AREA:
                    continue
                if max(w0, h0) / max(min(w0, h0), 1) > MAX_ASPECT_RATIO:
                    continue
                count += 1
                fn = f"fig_{count:02d}_p{page_num + 1}.png"
                fp = os.path.join(out_dir, fn)

                rects = [] if raw else page.get_image_rects(xref)
                if rects:
                    # 白底渲染：模擬 PDF viewer，透明/alpha 損壞的黑底問題消失
                    pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale),
                                          clip=rects[0])
                    note = "，白底渲染"
                else:
                    # raw 或找不到圖在頁面的位置 → 退回直接取內嵌 bitmap
                    pix = fitz.Pixmap(doc, xref)
                    if pix.colorspace and pix.colorspace.n > 3:   # CMYK → RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    note = ""
                pix.save(fp)
                size = f"{pix.width}x{pix.height}"
                figures.append({
                    "kind": "figure",
                    "page": page_num + 1,
                    "filename": fn,
                    "img_path": fp,
                    "rel_path": f"assets/{stem}/{fn}",
                    "size": size,
                })
                log(f"   📊 提取圖表 {fn}（{size}{note}）")
            except Exception:
                pass
    if not figures:
        try:
            os.rmdir(out_dir)
        except OSError:
            pass
    return figures


# ════════════════════════════════════════════════════════════════════════════
#  PDF：偵測「向量圖表頁」並整頁渲染（v1.0.1 新增）
#  ── get_images() 只抓得到「嵌入的點陣圖」；像麥肯錫 Exhibit 這種「用向量畫出來」
#     的圖表（線、面積、長條…）會被漏掉。這裡用 get_drawings() 的向量物件數偵測
#     「圖表頁」，把整頁渲染補回，讓真正的數據圖表不會在 figures 模式下消失。
# ════════════════════════════════════════════════════════════════════════════
def extract_vector_chart_pages(doc, stem: str, assets_dir: str, dpi: int,
                               threshold: int, skip_pages: set) -> list:
    out_dir = os.path.join(assets_dir, stem)
    os.makedirs(out_dir, exist_ok=True)
    items = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    for i in range(len(doc)):
        if i in skip_pages:               # 已被 extract_figures 以點陣圖抓走的頁，不重複
            continue
        try:
            n_draw = len(doc[i].get_drawings())
        except Exception:
            n_draw = 0
        if n_draw < threshold:            # 向量物件太少 → 多半是純文字頁，跳過
            continue
        try:
            pix = doc[i].get_pixmap(matrix=matrix)
        except Exception:
            continue
        fn = f"chart_p{i + 1:03d}.png"
        fp = os.path.join(out_dir, fn)
        pix.save(fp)
        items.append({
            "kind": "chart_page",
            "page": i + 1,
            "filename": fn,
            "img_path": fp,
            "rel_path": f"assets/{stem}/{fn}",
            "size": f"{pix.width}x{pix.height}",
            "drawings": n_draw,
        })
        log(f"   📈 偵測到向量圖表頁 第 {i + 1} 頁（向量物件 {n_draw}）→ {fn}")
    return items


# ════════════════════════════════════════════════════════════════════════════
#  v1.2.0：caption 驅動補抓 — 確保每個「Exhibit／Figure／圖表」標題頁都有圖
#  （向量物件數低於門檻、但內文有 Exhibit 標題的頁，也整頁渲染，避免「有描述沒圖」）
# ════════════════════════════════════════════════════════════════════════════
_EXHIBIT_HEAD = re.compile(r"^(exhibit|figure|圖表?|表)\s*\w", re.IGNORECASE)


def exhibit_caption_pages(doc) -> set:
    """回傳『文字含 Exhibit/Figure/圖表 標題行』的頁索引（0-based）。"""
    pages = set()
    for i in range(len(doc)):
        try:
            for line in doc[i].get_text("text").splitlines():
                if _EXHIBIT_HEAD.match(line.strip()):
                    pages.add(i)
                    break
        except Exception:
            pass
    return pages


def render_caption_pages(doc, stem: str, assets_dir: str, dpi: int,
                         pages: set, skip_pages: set) -> list:
    """把『有 Exhibit 標題但尚未被抽到圖』的頁整頁渲染補回。"""
    out_dir = os.path.join(assets_dir, stem)
    os.makedirs(out_dir, exist_ok=True)
    items = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    for i in sorted(pages):
        if i in skip_pages:
            continue
        try:
            pix = doc[i].get_pixmap(matrix=matrix)
        except Exception:
            continue
        fn = f"chart_p{i + 1:03d}.png"
        fp = os.path.join(out_dir, fn)
        pix.save(fp)
        items.append({
            "kind": "chart_page",
            "page": i + 1,
            "filename": fn,
            "img_path": fp,
            "rel_path": f"assets/{stem}/{fn}",
            "size": f"{pix.width}x{pix.height}",
            "via": "caption",
        })
        log(f"   📑 caption 補抓 Exhibit 頁 第 {i + 1} 頁 → {fn}")
    return items


# ════════════════════════════════════════════════════════════════════════════
#  圖片資料夾 / 單張圖片
# ════════════════════════════════════════════════════════════════════════════
def collect_images(input_path: str, stem: str, assets_dir: str) -> list:
    """把一張圖或一個資料夾的圖整理進 assets，回傳清單。"""
    import shutil
    out_dir = os.path.join(assets_dir, stem)
    os.makedirs(out_dir, exist_ok=True)

    srcs = []
    if os.path.isdir(input_path):
        for name in sorted(os.listdir(input_path)):
            if os.path.splitext(name)[1].lower() in IMG_EXTS:
                srcs.append(os.path.join(input_path, name))
    else:
        srcs = [input_path]

    items = []
    for idx, src in enumerate(srcs, 1):
        ext = os.path.splitext(src)[1].lower()
        fn = f"img_{idx:03d}{ext}"
        fp = os.path.join(out_dir, fn)
        shutil.copy2(src, fp)
        size = "?"
        if HAS_PIL:
            try:
                with Image.open(fp) as im:
                    size = f"{im.width}x{im.height}"
            except Exception:
                pass
        items.append({
            "kind": "image",
            "page": idx,
            "filename": fn,
            "img_path": fp,
            "rel_path": f"assets/{stem}/{fn}",
            "size": size,
            "orig_name": os.path.basename(src),
        })
        log(f"   🖼️  收錄圖片 {os.path.basename(src)} → {fn}")
    return items


# ════════════════════════════════════════════════════════════════════════════
#  產出 Markdown 骨架（留好 placeholder，給 Claude 視覺填寫）
# ════════════════════════════════════════════════════════════════════════════
PLACEHOLDER = (
    "> **🖼 圖像解讀**　｜　檢視狀態：⬜ 未檢視（實際開圖看過後改成 ✅ 已檢視）\n"
    "> - **類型**：（表格／圖表／流程圖／框架圖／截圖／照片／文字頁）\n"
    "> - **內容**：（2-3 句：顯示什麼、關鍵數據、主要結論。數字一律從圖上讀，勿憑印象）\n"
    "> - **原文出處（逐字）**：（把圖上印的標題與 Source／來源 行逐字抄下；看不清寫「圖中未能辨識」，**禁止杜撰來源或數據**）\n"
    "> - **可檢索關鍵字**：（4-8 個詞，逗號分隔）"
)


def build_scaffold(title: str, source: str, kind_label: str,
                   items: list, scanned: bool) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    yaml = (
        "---\n"
        f'title: "{title}"\n'
        f'source: "{source}"\n'
        "type: VLM-pending\n"
        f"items: {len(items)}\n"
        f"mode: {kind_label}\n"
        f'generated_at: "{now}"\n'
        "language: zh-TW\n"
        "---\n\n"
    )

    head = f"# {title}｜視覺知識庫\n\n"
    head += (
        "> **ℹ️ 這份檔案還沒完成——它在等 Claude 的眼睛**\n"
        "> 以下每張圖下方都有「圖像解讀」空格。請在 Claude Desktop 讓 Claude 用內建視覺能力\n"
        "> **逐張開圖**填寫（📊 數據圖表務必細看、抄出處；🖼 裝飾照可簡述）。完全免費、不需 API key。\n\n"
    )
    if scanned:
        head += (
            "> **⚠️ 偵測到掃描／圖像型 PDF**\n"
            "> 這份 PDF 抽不出文字（內容是圖片）。每一頁都已渲染成圖檔，\n"
            "> 請 Claude 不只描述、還要把整頁的**文字內容逐字謄寫**成 Markdown。\n\n"
        )

    body = ""
    for i, it in enumerate(items, 1):
        if it["kind"] == "chart_page":
            loc = f"第 {it['page']} 頁（向量圖表）"; tag = "📊 數據圖表（必須開圖細看、抄出處）"
        elif it["kind"] in ("page", "figure"):
            loc = f"第 {it['page']} 頁"; tag = "🖼 圖片（開圖判斷：是圖表就細看、是裝飾照可簡述）"
        else:
            loc = it.get("orig_name", f"圖片 {it['page']}"); tag = "🖼 圖片"
        body += f"## 圖像 {i:02d} — {loc}（{it['size']}）｜{tag}\n\n"
        body += f"![圖像 {i:02d}]({it['rel_path']})\n\n"
        body += PLACEHOLDER + "\n\n"

    return yaml + head + body


# ════════════════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════════════════
def process(input_path: str, out_dir: str, mode: str, dpi: int,
            detect_vector_pages: bool = True,
            vector_threshold: int = VECTOR_DRAW_THRESHOLD,
            raw_figures: bool = False) -> int:
    if not os.path.exists(input_path):
        log(f"❌ 找不到輸入：{input_path}")
        return 1

    os.makedirs(out_dir, exist_ok=True)
    assets_dir = os.path.join(out_dir, "assets")
    stem = safe_stem(input_path)

    ext = os.path.splitext(input_path)[1].lower()
    is_pdf = ext == ".pdf"
    is_img = ext in IMG_EXTS
    is_dir = os.path.isdir(input_path)

    items = []
    scanned = False
    kind_label = mode

    if is_pdf:
        if not HAS_FITZ:
            log("❌ 需要 PyMuPDF。請先安裝：pip install -r scripts/requirements.txt")
            return 1
        doc = fitz.open(input_path)
        log(f"📄 開啟 PDF：{os.path.basename(input_path)}（{len(doc)} 頁）")

        scanned, avg = is_scanned_pdf(doc)
        log(f"🔍 平均每頁可抽取文字：{avg:.0f} 字 → "
            f"{'掃描／圖像型 PDF' if scanned else '文字型 PDF'}")

        use_mode = mode
        if mode == "auto":
            use_mode = "pages" if scanned else "figures"
            kind_label = use_mode
            log(f"🧭 auto 模式判定：採用「{use_mode}」")

        if use_mode in ("pages", "both"):
            items += render_pages(doc, stem, assets_dir, dpi)
        if use_mode in ("figures", "both"):
            figs = extract_figures(doc, stem, assets_dir,
                                    raw=raw_figures, render_scale=dpi / 72.0)
            items += figs
            # v1.0.1：補抓「向量圖表頁」（get_images 漏掉的那種，如麥肯錫 Exhibit）
            if use_mode == "figures" and detect_vector_pages and not scanned:
                skip = {it["page"] - 1 for it in figs}
                vpages = extract_vector_chart_pages(
                    doc, stem, assets_dir, dpi, vector_threshold, skip)
                items += vpages
                if vpages:
                    kind_label = "figures+charts"
                    log(f"   ✅ v1.0.1 補抓向量圖表頁 {len(vpages)} 頁"
                        f"（嵌入點陣圖抽不到的數據圖表）")
                # v1.2.0：caption 驅動補抓——確保每個 Exhibit 標題頁都有圖
                captured = {it["page"] - 1 for it in items}
                cpages = render_caption_pages(
                    doc, stem, assets_dir, dpi, exhibit_caption_pages(doc), captured)
                items += cpages
                if cpages:
                    log(f"   ✅ v1.2.0 caption 補抓 Exhibit 頁 {len(cpages)} 頁")
            if not items and use_mode == "figures":
                log("   ⚠ 沒有符合條件的嵌入圖表或向量圖表頁。"
                    "若這是掃描 PDF，請改用 --mode pages")

        if use_mode == "figures" and not scanned:
            log("   💡 這是文字型 PDF：文字部分建議交給 doc-to-md，"
                "本工具只負責其中的圖表視覺解讀（含向量 Exhibit）。")
        doc.close()

    elif is_img or is_dir:
        items = collect_images(input_path, stem, assets_dir)
        kind_label = "images"
    else:
        log(f"❌ 不支援的輸入格式：{ext or '(資料夾?)'}。"
            f"支援：PDF、圖片（{', '.join(sorted(IMG_EXTS))}）、圖片資料夾。")
        return 1

    if not items:
        log("❌ 沒有產生任何圖像，請檢查輸入內容。")
        return 1

    title = stem.replace("_", " ")
    md = build_scaffold(title, os.path.basename(input_path.rstrip("/")),
                        kind_label, items, scanned)

    md_name = f"{stem}_VLM待解讀.md"
    md_path = os.path.join(out_dir, md_name)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    # manifest 方便程式化追蹤
    manifest = {
        "source": input_path,
        "mode": kind_label,
        "scanned": scanned,
        "item_count": len(items),
        "md_file": md_name,
        "items": [{k: v for k, v in it.items() if k != "img_path"} for it in items],
    }
    with open(os.path.join(out_dir, f"{stem}_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    log("")
    log("✅ Phase 1 完成（本地、0 token）")
    log(f"   • 圖像數量：{len(items)}")
    log(f"   • 圖檔位置：{os.path.join(assets_dir, stem)}/")
    log(f"   • 待解讀檔：{md_path}")
    log("")
    log("👉 下一步（Phase 2，免費）：在 Claude Desktop 中說")
    log(f'   「幫我用視覺能力完成這份 VLM 待解讀檔：{md_path}」')
    log(f"[DONE] scaffold saved to: {md_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="VLM-to-MD Phase 1：把 PDF／圖片轉成圖檔 + Markdown 骨架（本地、免費、0 token）"
    )
    ap.add_argument("input", help="PDF 檔、單張圖片，或圖片資料夾")
    ap.add_argument("-o", "--output", default=None,
                    help="輸出資料夾（預設：輸入檔所在目錄）")
    ap.add_argument("--mode", choices=["auto", "pages", "figures", "both"],
                    default="auto",
                    help="auto=自動判定；pages=整頁渲染；figures=只抽圖表；both=兩者")
    ap.add_argument("--auto", action="store_true",
                    help="等同 --mode auto（與 doc-to-md 一致的便捷旗標）")
    ap.add_argument("--dpi", type=int, default=PAGE_RENDER_DPI,
                    help=f"整頁渲染解析度（預設 {PAGE_RENDER_DPI}）")
    ap.add_argument("--vector-threshold", type=int, default=VECTOR_DRAW_THRESHOLD,
                    help=f"向量圖表頁偵測門檻：單頁向量物件數 ≥ 此值即整頁渲染"
                         f"（預設 {VECTOR_DRAW_THRESHOLD}，v1.0.1）")
    ap.add_argument("--no-vector-pages", action="store_true",
                    help="關閉向量圖表頁偵測（只抽嵌入點陣圖，回到 v1.0.0 行為）")
    ap.add_argument("--raw-figures", action="store_true",
                    help="圖表用『直接取原始內嵌 bitmap』舊法（可能黑底）；"
                         "預設用白底渲染修正透明 PNG 黑底問題（v1.0.2）")
    args = ap.parse_args()

    if args.auto:
        args.mode = "auto"

    out_dir = args.output or (
        os.path.dirname(os.path.abspath(args.input))
        if not os.path.isdir(args.input) else args.input
    )
    sys.exit(process(args.input, out_dir, args.mode, args.dpi,
                     detect_vector_pages=not args.no_vector_pages,
                     vector_threshold=args.vector_threshold,
                     raw_figures=args.raw_figures))


if __name__ == "__main__":
    main()
