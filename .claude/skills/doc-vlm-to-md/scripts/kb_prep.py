#!/usr/bin/env python3
"""
kb_prep.py  v1.0.1 ── doc-vlm-to-md 整合 Phase 1（一次做完「文字 + 視覺」）
─────────────────────────────────────────────────────────────────────────────
一條指令把一份文件轉成「文字 + 圖表」都進得了 RAG 的【單一 Markdown 知識庫】：

  • 文字     → 重用 doc-to-md（章節結構 + 章節摘要 placeholder）
  • 視覺     → 重用 vlm-to-md v1.0.1（嵌入點陣圖 + 向量圖表頁 + 掃描頁，圖像解讀 placeholder）
  • 合併     → 文字在前、「📊 圖表與視覺內容（依頁碼）」附錄在後，兩種 placeholder 一次交給 Claude 填

本地、免費、0 token、0 API key。真正的「寫摘要 / 看圖說話」交給 Claude（Phase 2）。

與舊的兩個獨立 skill 的關係：
  doc-to-md（只文字）＋ vlm-to-md（只視覺）→ 本 skill 把兩步併成一步，輸出單一檔。
─────────────────────────────────────────────────────────────────────────────
"""
import argparse
import datetime
import json
import os
import sys

# === Windows 中文輸出修正（v: cp1252 fix）===
# Windows 預設 stdout 編碼為 cp1252，輸出中文（argparse --help / log 進度）會 UnicodeEncodeError。
# 強制 stdout/stderr 改 UTF-8（被導向 >/dev/null 或檔案時尤其重要）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 讓 kb_prep 能 import 同資料夾的 doc_to_md / vlm_prep / epub_images
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_to_md          # noqa: E402
import vlm_prep           # noqa: E402
import epub_images        # noqa: E402  — EPUB 圖抽取＋錨點（zip 內嵌圖，不需渲染）

try:
    import fitz           # PyMuPDF
except ImportError:       # pragma: no cover
    try:
        import pymupdf as fitz
    except ImportError:
        fitz = None

TEXT_EXTS = {".pdf", ".epub", ".txt"}


def log(msg: str):
    print(msg, flush=True)


# ════════════════════════════════════════════════════════════════════════════
#  視覺附錄（重用 vlm_prep 的 PLACEHOLDER 與 item 結構）
# ════════════════════════════════════════════════════════════════════════════
def build_visual_section(items: list, scanned: bool) -> str:
    if not items:
        return ""
    out = ["\n\n---\n", "# 📊 圖表與視覺內容（依頁碼）\n"]
    if scanned:
        out.append(
            "> **⚠️ 偵測到掃描／圖像型 PDF**\n"
            "> 這份 PDF 抽不出文字（內容是圖片）。每頁都已渲染成圖檔，\n"
            "> 請 Claude 不只描述、還要把整頁文字**逐字謄寫**成 Markdown。\n"
        )
    else:
        out.append(
            "> **ℹ️ 這一區在等 Claude 的眼睛**\n"
            "> 下面每張圖下方都有「圖像解讀」空格，請讓 Claude **逐張開圖**填寫\n"
            "> （📊 數據圖表務必細看、抄出處；🖼 裝飾照可簡述）。完全免費、不需 API key。\n"
        )
    for i, it in enumerate(items, 1):
        if it["kind"] == "chart_page":
            loc = f"第 {it['page']} 頁（向量圖表）"; tag = "📊 數據圖表（必須開圖細看、抄出處）"
        elif it["kind"] in ("page", "figure"):
            loc = f"第 {it['page']} 頁"; tag = "🖼 圖片（開圖判斷：圖表細看／裝飾照簡述）"
        else:
            loc = it.get("orig_name", f"圖片 {it['page']}"); tag = "🖼 圖片"
        out.append(f"\n## 圖 {i:02d} — {loc}（{it['size']}）｜{tag}\n")
        out.append(f"![圖 {i:02d}]({it['rel_path']})\n")
        out.append(vlm_prep.PLACEHOLDER + "\n")
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════════════════
#  EPUB 視覺前處理 —— zip 內嵌圖不需渲染，直接抽（重用 epub_images.extract()）
#  補上原本「EPUB / TXT：沒有頁面圖像」的錯誤假設：EPUB 的圖是 zip 裡的獨立檔案。
# ════════════════════════════════════════════════════════════════════════════
def collect_epub_images(input_path: str, out_dir: str, stem: str) -> list:
    """呼叫 epub_images.extract() 把 EPUB 內嵌圖抽到 assets/<stem>/images/，
    並把它的 figures.json（尺寸 + 文字錨點）轉成 kb_prep 標準 item 格式。"""
    assets_dir = os.path.join(out_dir, "assets", stem)
    n, n_anchors = epub_images.extract(input_path, assets_dir)
    log(f"   🖼️  EPUB 內嵌圖抽取：{n} 張圖、{n_anchors} 個文字錨點")
    fig_json = os.path.join(assets_dir, "figures.json")
    if not os.path.exists(fig_json):
        return []
    with open(fig_json, encoding="utf-8") as f:
        idx = json.load(f)
    items = []
    for it in idx:
        fn = it["file"]
        img_path = os.path.join(assets_dir, "images", fn)
        size = "?"
        if vlm_prep.HAS_PIL:
            try:
                with vlm_prep.Image.open(img_path) as im:
                    size = f"{im.width}x{im.height}"
            except Exception:
                pass
        items.append({
            "kind": "epub_figure",
            "page": 0,
            "filename": fn,
            "img_path": img_path,
            "rel_path": f"assets/{stem}/images/{fn}",
            "size": size,
            "orig_name": fn,
            "anchors": it.get("anchors", []),
        })
    return items


def _norm_for_anchor(s: str) -> str:
    """去空白＋清掉 HTML 殘骸（錨點取自原始 xhtml，尾巴常黏著 <img …）。"""
    import re
    return re.sub(r"\s+", "", re.sub(r"<[^>]*$|<[^>]*>", "", s))


def _locate_anchor(body_norm: str, anchor_before: str, min_len: int = 40) -> int:
    """用錨點前文的尾段在正文裡找插入點；找不到就逐步縮短再試。同 merge_figures.locate()。"""
    a = _norm_for_anchor(anchor_before)
    for take in (160, 120, 90, 60, min_len):
        if len(a) < take:
            continue
        probe = a[-take:]
        i = body_norm.find(probe)
        if i != -1:
            return i + len(probe)
    return -1


def insert_epub_figures_inline(text_md: str, items: list) -> tuple:
    """把 EPUB 圖依錨點插回 text_md 的原始位置（不是堆成附錄）。
    回傳 (新 text_md, 已插入 items, 插不進去的 items)——插不進去的留給呼叫端放進附錄，不會丟圖。"""
    pos_map = [i for i, ch in enumerate(text_md) if not ch.isspace()]
    body_norm = "".join(text_md[i] for i in pos_map)

    inserts, placed, unplaced = [], [], []
    for it in items:
        anchors = it.get("anchors") or []
        if not anchors:
            unplaced.append(it)
            continue
        p = _locate_anchor(body_norm, anchors[0].get("before", ""))
        if p == -1:
            unplaced.append(it)
            continue
        real = pos_map[min(p, len(pos_map) - 1)] + 1
        inserts.append((real, it))
        placed.append(it)

    for real, it in sorted(inserts, key=lambda x: x[0], reverse=True):
        tag = "🖼 圖片（開圖判斷：圖表細看／裝飾照簡述）"
        loc = it.get("orig_name", it["filename"])
        block = (f"\n\n### 🖼 {loc}（{it['size']}）｜{tag}\n"
                 f"![{loc}]({it['rel_path']})\n"
                 f"{vlm_prep.PLACEHOLDER}\n")
        text_md = text_md[:real] + block + text_md[real:]

    return text_md, placed, unplaced


# ════════════════════════════════════════════════════════════════════════════
#  視覺前處理（依輸入型態分流，重用 vlm_prep 的函式）
# ════════════════════════════════════════════════════════════════════════════
def collect_visual_items(input_path: str, out_dir: str, dpi: int,
                         detect_vector_pages: bool, vector_threshold: int) -> tuple:
    """回傳 (items, scanned)。針對 PDF 抽圖表/向量頁；掃描 PDF 整頁渲染；圖片/資料夾收圖。"""
    assets_dir = os.path.join(out_dir, "assets")
    stem = vlm_prep.safe_stem(input_path)
    ext = os.path.splitext(input_path)[1].lower()

    if os.path.isdir(input_path) or ext in vlm_prep.IMG_EXTS:
        return vlm_prep.collect_images(input_path, stem, assets_dir), False

    if ext == ".pdf":
        if fitz is None:
            log("❌ 需要 PyMuPDF：pip install -r scripts/requirements.txt")
            return [], False
        doc = fitz.open(input_path)
        scanned, avg = vlm_prep.is_scanned_pdf(doc)
        log(f"🔍 視覺判定：平均每頁 {avg:.0f} 字 → "
            f"{'掃描／圖像型 PDF（整頁渲染）' if scanned else '文字型 PDF（抽圖表＋向量圖表頁）'}")
        items = []
        if scanned:
            items = vlm_prep.render_pages(doc, stem, assets_dir, dpi)
        else:
            figs = vlm_prep.extract_figures(doc, stem, assets_dir)
            items += figs
            if detect_vector_pages:
                skip = {it["page"] - 1 for it in figs}
                vpages = vlm_prep.extract_vector_chart_pages(
                    doc, stem, assets_dir, dpi, vector_threshold, skip)
                items += vpages
                if vpages:
                    log(f"   ✅ 補抓向量圖表頁 {len(vpages)} 頁（嵌入點陣圖抽不到的數據圖表）")
                # v1.2.0：caption 驅動補抓——確保每個 Exhibit 標題頁都有圖
                captured = {it["page"] - 1 for it in items}
                cpages = vlm_prep.render_caption_pages(
                    doc, stem, assets_dir, dpi, vlm_prep.exhibit_caption_pages(doc), captured)
                items += cpages
                if cpages:
                    log(f"   ✅ caption 補抓 Exhibit 頁 {len(cpages)} 頁（避免有描述卻無圖）")
        doc.close()
        return items, scanned

    if ext == ".epub":
        # EPUB 的圖是 zip 裡的獨立檔案，不需要渲染就能直接取（見 collect_epub_images）
        return collect_epub_images(input_path, out_dir, stem), False

    # TXT：純文字，沒有頁面圖像
    return [], False


# ════════════════════════════════════════════════════════════════════════════
#  v1.2.0：版面線性重建（原位 VLM）—— 逐頁輸出 doc-to-md 品質文字，
#  每頁的圖就地插在該頁文字之後，使每張圖的「圖像解讀」空格天生位於原始位置。
# ════════════════════════════════════════════════════════════════════════════
def _heading_kind(s: str, sec_pats, exh_pat):
    if len(s) > 80:
        return None
    for rx, kind in sec_pats:
        if rx.match(s):
            return kind
    # Exhibit/Box/Figure 標題：通常很短且獨立成行；長句（內文引用）不算標題
    if exh_pat.match(s) and len(s) <= 24 and '"' not in s and '?' not in s:
        return "exhibit"
    return None


def build_inline_pdf_md(input_path: str, items: list, convert_chinese: bool) -> str:
    import re
    meta, pages = doc_to_md.extract_pdf(input_path)
    try:
        pages = doc_to_md.strip_repeated_headers_footers(pages)
    except Exception:
        pass
    by_page = {}
    for it in items:
        by_page.setdefault(it["page"], []).append(it)

    title = (meta.get("title") or "").strip() or vlm_prep.safe_stem(input_path).replace("_", " ")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [
        "---", f'title: "{title}"', f'source: "{os.path.basename(input_path)}"',
        f"pages: {len(pages)}", "type: KB-pending", "language: zh-TW",
        f'generated_at: "{now}"', 'tools_used: ["doc-vlm-to-md (inline v1.2.0)"]', "---", "",
        f"# {title}", "",
        "> **📌 這份檔案要 Claude 兩件事一次做完（圖已在原位）**",
        "> 1. 每個章節標題下的 `章節摘要` 空格用文字填好；",
        "> 2. 每張圖（已就在它原本的位置）的 `圖像解讀` 空格用視覺填好——逐張開圖、抄出處、勿杜撰。",
        "",
    ]
    sec_pats = [(re.compile(p), k) for p, k in doc_to_md.ALL_SECTION_PATTERNS]
    exh_pat = re.compile(r"^(Exhibit|Box|Figure)\b", re.IGNORECASE)
    summary_kinds = {"chapter", "part", "section", "lecture", "lesson", "module", "unit"}

    for i, raw in enumerate(pages, 1):
        txt = doc_to_md.clean_text_block(raw)
        try:
            txt = doc_to_md.merge_broken_paragraphs(txt)
        except Exception:
            pass
        if convert_chinese:
            try:
                txt = doc_to_md.convert_to_tw(txt)
            except Exception:
                pass
        for line in txt.splitlines():
            s = line.strip()
            if not s:
                out.append("")
                continue
            kind = _heading_kind(s, sec_pats, exh_pat)
            if kind:
                out.append(f"## {s}")
                if kind in summary_kinds:
                    out.append("> **📋 章節摘要**")
                    out.append("> **摘要**：（依本節內容寫 2-4 句）")
                    out.append("> **關鍵字**：（4-8 個檢索關鍵字）")
            else:
                out.append(s)
        for it in by_page.get(i, []):   # 該頁的圖：就地插在該頁文字之後（原位）
            if it["kind"] == "chart_page":
                tag = "📊 數據圖表（必須開圖細看、抄出處）"; loc = f"第 {i} 頁（向量圖表）"
            elif it["kind"] in ("page", "figure"):
                tag = "🖼 圖片（開圖判斷：圖表細看／裝飾照簡述）"; loc = f"第 {i} 頁"
            else:
                tag = "🖼 圖片"; loc = it.get("orig_name", f"圖片 {i}")
            out += ["", f"### 🖼 {loc}（{it['size']}）｜{tag}",
                    f"![{loc}]({it['rel_path']})", vlm_prep.PLACEHOLDER, ""]
    return "\n".join(out) + "\n"


# ════════════════════════════════════════════════════════════════════════════
#  主流程：文字 + 視覺 → 單一 MD
# ════════════════════════════════════════════════════════════════════════════
def process_combined(input_path: str, out_dir: str, convert_chinese: bool, dpi: int,
                     detect_vector_pages: bool, vector_threshold: int) -> int:
    if not os.path.exists(input_path):
        log(f"❌ 找不到輸入：{input_path}")
        return 1
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(input_path)[1].lower()
    is_text_doc = (not os.path.isdir(input_path)) and ext in TEXT_EXTS
    stem = vlm_prep.safe_stem(input_path)

    # ── 1) 視覺先跑（需先知道是否掃描，才能決定文字策略）────────────────────
    log("🖼️  [1/3] 視覺前處理（圖表 / 向量圖表頁 / caption 補抓 / 掃描頁）…")
    items, scanned = collect_visual_items(
        input_path, out_dir, dpi, detect_vector_pages, vector_threshold)

    is_pdf = is_text_doc and ext == ".pdf"
    text_md_path = None
    epub_placed = 0
    epub_placed_items = []

    if is_pdf and not scanned:
        # ── 文字型 PDF：v1.2.0 版面線性重建——圖就地插在原位（不再丟末尾附錄）──
        log("📝 [2/3] 文字抽取 + 原位插圖（doc-to-md 文字品質 × 逐頁交錯）…")
        log("🧩 [3/3] 合併 → 單一知識庫（每張圖在它的原始位置）…")
        combined = build_inline_pdf_md(input_path, items, convert_chinese)
    else:
        # ── 掃描 PDF / EPUB / TXT / 圖片資料夾：文字（若有）在前、視覺附錄在後 ──
        text_md = ""
        if is_text_doc:
            log("📝 [2/3] 文字抽取（doc-to-md）…")
            try:
                text_md_path = doc_to_md.process_file(input_path, out_dir, convert_chinese)
                with open(text_md_path, encoding="utf-8") as f:
                    text_md = f.read()
                text_md = text_md.replace("> [!note] 章節摘要", "> **📋 章節摘要**")
            except SystemExit:
                log("   ⚠ 文字抽取失敗，改以純視覺模式繼續")
            except Exception as e:
                log(f"   ⚠ 文字抽取出錯（{e}），改以純視覺模式繼續")
        else:
            log("📝 [2/3] 輸入為圖片／資料夾 → 純視覺模式")

        # ── EPUB：圖靠錨點塞回原文位置（不進附錄）；插不進去的才留給附錄 ──────
        if ext == ".epub" and text_md and items:
            text_md, epub_placed_items, items = insert_epub_figures_inline(text_md, items)
            epub_placed = len(epub_placed_items)
            log(f"   ✅ EPUB 圖原位插入：{epub_placed} 張成功定位"
                + (f"、{len(items)} 張找不到錨點改入附錄" if items else ""))

        log("🧩 [3/3] 合併文字 + 視覺附錄…")
        visual = build_visual_section(items, scanned)
        if text_md:
            fig_note = ("（EPUB 的圖已就地插在原文位置；找不到錨點的少數圖才在文末「📊 圖表與視覺內容」）"
                        if ext == ".epub" else "")
            header_note = (
                "\n> **📌 這份檔案要 Claude 兩件事一次做完**\n"
                "> 1. 把上面每個 `章節摘要` 空格用文字內容填好；\n"
                f"> 2. 把每張圖的 `圖像解讀` 空格用視覺填好（逐張開圖、抄出處、勿杜撰）{fig_note}。\n"
            )
            if text_md.startswith("---"):
                end = text_md.find("\n---", 3)
                if end != -1:
                    insert_at = text_md.find("\n", end + 4)
                    insert_at = insert_at if insert_at != -1 else len(text_md)
                    text_md = text_md[:insert_at] + "\n" + header_note + text_md[insert_at:]
                else:
                    text_md = header_note + "\n" + text_md
            else:
                text_md = header_note + "\n" + text_md
            combined = text_md.rstrip() + "\n" + visual + "\n"
        else:
            title = stem.replace("_", " ")
            combined = (
                "---\n"
                f'title: "{title}"\n'
                f'source: "{os.path.basename(input_path.rstrip("/"))}"\n'
                "type: KB-pending\n"
                f'generated_at: "{datetime.datetime.now():%Y-%m-%d %H:%M}"\n'
                "language: zh-TW\n"
                "---\n\n"
                f"# {title}｜視覺知識庫\n"
                + visual + "\n"
            )

    out_name = f"{stem}_完整知識庫.md"
    out_path = os.path.join(out_dir, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(combined)

    # 移除中繼的純文字 MD，只留一份整合檔（避免混淆）
    if text_md_path and os.path.abspath(text_md_path) != os.path.abspath(out_path):
        try:
            os.remove(text_md_path)
        except OSError:
            pass

    # manifest
    manifest = {
        "source": input_path,
        "combined_md": out_name,
        "has_text": is_text_doc,
        "inline_layout": bool((is_pdf and not scanned) or epub_placed),
        "epub_figures_placed_inline": epub_placed,
        "scanned": scanned,
        "visual_items": len(items) + epub_placed,
        "items": [{k: v for k, v in it.items() if k != "img_path"}
                  for it in (epub_placed_items + items)],
        "generated_at": datetime.datetime.now().isoformat(),
    }
    with open(os.path.join(out_dir, f"{stem}_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    n_charts = sum(1 for it in items if it["kind"] == "chart_page")
    log("")
    log("✅ Phase 1 完成（本地、0 token）")
    log(f"   • 文字知識庫：{'有（含章節摘要空格）' if is_text_doc else '無（純視覺）'}"
        f"{'｜版面：原位插圖' if (is_pdf and not scanned) else ''}"
        f"{f'｜EPUB 原位插圖 {epub_placed} 張' if epub_placed else ''}")
    log(f"   • 視覺項目：{len(items) + epub_placed} 張（其中向量圖表頁 {n_charts} 張"
        f"{f'、EPUB 原位 {epub_placed} 張' if epub_placed else ''}）")
    log(f"   • 單一輸出：{out_path}")
    log(f"   • 圖檔位置：{os.path.join(out_dir, 'assets', stem)}/")
    log("")
    log("👉 下一步（Phase 2，免費）：在 Claude Desktop 中說")
    log(f'   「幫我完成這份知識庫：把章節摘要和圖像解讀都填好：{out_path}」')
    log(f"[DONE] combined KB saved to: {out_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="doc-vlm-to-md 整合 Phase 1：一次把文件轉成『文字＋圖表』單一 Markdown 知識庫（本地、免費、0 token）")
    ap.add_argument("input", help="PDF / EPUB / TXT，或圖片、圖片資料夾")
    ap.add_argument("-o", "--output", default=None, help="輸出資料夾（預設：輸入檔所在目錄）")
    ap.add_argument("--auto", action="store_true", help="自動處理（與其他 skill 一致的便捷旗標）")
    ap.add_argument("--no-convert-chinese", action="store_true", help="不做簡體→繁體轉換")
    ap.add_argument("--dpi", type=int, default=vlm_prep.PAGE_RENDER_DPI,
                    help=f"整頁／向量圖表頁渲染解析度（預設 {vlm_prep.PAGE_RENDER_DPI}）")
    ap.add_argument("--vector-threshold", type=int, default=vlm_prep.VECTOR_DRAW_THRESHOLD,
                    help=f"向量圖表頁偵測門檻（預設 {vlm_prep.VECTOR_DRAW_THRESHOLD}）")
    ap.add_argument("--no-vector-pages", action="store_true",
                    help="關閉向量圖表頁偵測（只抽嵌入點陣圖）")
    args = ap.parse_args()

    out_dir = args.output or (
        os.path.dirname(os.path.abspath(args.input))
        if not os.path.isdir(args.input) else args.input
    )
    sys.exit(process_combined(
        args.input, out_dir,
        convert_chinese=not args.no_convert_chinese,
        dpi=args.dpi,
        detect_vector_pages=not args.no_vector_pages,
        vector_threshold=args.vector_threshold,
    ))


if __name__ == "__main__":
    main()
