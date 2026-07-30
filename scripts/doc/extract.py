#!/usr/bin/env python3
"""scripts/doc/extract.py — 確定性文件抽取器(Phase-1,0 token)

PDF(PyMuPDF)/EPUB(ebooklib)/TXT → 結構化繁體 markdown 純文字。

架構原則:本檔只做確定性抽取(文字抽取 + 結構偵測 + 簡繁轉換 + 清理雜訊),
絕不呼叫任何 LLM、絕不自己寫章節摘要或好學生筆記——摘要/理解一律留給下游
共用的 phase-b/notes 層。輸出相當於「音檔線去時間軸後的乾淨逐字文本」。

用 .venv-doc 的 python 跑(已裝 fitz/ebooklib/chardet/opencc):
    /Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-doc/bin/python \
        scripts/doc/extract.py <input> -o <output.md> [--corrections <file>]

注意:這裡沒有 --context。--context 是統一管線既有語意(音檔線
scripts/audio/transcribe_local.py 用它餵 ASR initial_prompt 的自由文本),
doc 線不重複這個入口、也不搶這個字面意思。本檔的補充修正表(find=replace
逐行對照,見 apply_corrections())改名 --corrections,避免兩條輸入線
對同一個旗標名有衝突語意。

輸出:單一 UTF-8 markdown 檔 = 繁體乾淨文本 + ##/### 章節標題;
無 YAML frontmatter、無摘要、無 callout、無 code fence 包裹。
stdout 印一行 JSON stats:{"input_type","chars","sections","lang","vertical_pages"}。
非零 exit + stderr 清楚訊息 on 失敗(檔案不存在/格式不支援/抽出空內容)。

直排(vertical text)處理:PDF 逐頁用 get_text("dict") 讀每個 text line 的
'dir' 向量,y 分量顯著大於 x 分量即判定該行為直排。一頁裡多數行為直排時,
該頁視為「直排頁」,重排順序= 欄序右→左、欄內上→下(傳統中文直排讀序),
不採用 PyMuPDF 預設依插入順序輸出的橫排邏輯(那套順序在直排頁上會錯亂)。
無法完美重排的極端版式,至少會被計入 stats.vertical_pages,不會靜默吐錯亂文字。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

# ── Dependency check(只在 main() 呼叫,保持純函式可在其他直譯器下被 import 測試）──


def check_deps() -> None:
    missing = []
    for pkg, import_name in [
        ("PyMuPDF", "fitz"),
        ("ebooklib", "ebooklib"),
        ("lxml", "lxml"),
        ("chardet", "chardet"),
        ("opencc", "opencc"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[extract] ERROR: 缺少套件: {', '.join(missing)}", file=sys.stderr)
        print("[extract] 用 .venv-doc 的 python 跑:", file=sys.stderr)
        print("  /Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-doc/bin/python "
              "scripts/doc/extract.py ...", file=sys.stderr)
        sys.exit(1)


class ExtractError(Exception):
    """使用者可見的抽取失敗(檔案不存在/格式不支援/抽出空內容/解析失敗)。"""


# ── OpenCC 簡→繁台灣化(與 scripts/audio/transcribe_local.py 同一慣例:s2twp）──

_CONVERTER = None


def get_converter():
    global _CONVERTER
    if _CONVERTER is None:
        import opencc
        _CONVERTER = opencc.OpenCC("s2twp")
    return _CONVERTER


def convert_to_tw(text: str) -> str:
    try:
        return get_converter().convert(text)
    except Exception:
        return text


# ── 語言偵測(粗略,足夠填 stats.lang）──────────────────────────────────


def detect_lang(text: str) -> str:
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    total = cjk + latin
    if total == 0:
        return "unknown"
    return "zh" if cjk / total >= 0.3 else "en"


# ── 文字清理工具 ──────────────────────────────────────────────────────

# 常見 PDF 抽取亂碼:控制字元、Unicode 私用區(自訂字型 cid 對應失敗常見特徵)、
# U+FFFD replacement character。
_GARBLE_CHARS = "".join([
    "".join(chr(c) for c in range(0x00, 0x09)),
    "".join(chr(c) for c in range(0x0B, 0x0D)),
    "".join(chr(c) for c in range(0x0E, 0x20)),
    "".join(chr(c) for c in range(0xE000, 0xF8FF + 1)),
    chr(0xFFFD),
])
_GARBLE_RE = re.compile("[" + re.escape(_GARBLE_CHARS) + "]")

_DECORATIVE_CHARS = set("─━—–-=~·•◆▪□■○●※★☆†‡§¶∙‧")

_PAGE_NOISE_RES = [
    re.compile(r"^\d{1,4}$"),                                  # 裸頁碼
    re.compile(r"^[-–—]\s*\d{1,4}\s*[-–—]$"),                  # - 3 -
    re.compile(r"^第\s*\d{1,4}\s*頁(?:\s*/\s*\d+)?$"),          # 第 3 頁 / 第3頁/10
    re.compile(r"^[Pp]age\s+\d+(\s+of\s+\d+)?$"),               # Page 3 / Page 3 of 10
]


def strip_garbled_chars(text: str) -> str:
    return _GARBLE_RE.sub("", text)


def clean_text_block(text: str) -> str:
    """去頁碼/頁眉頁腳雜訊行、去純裝飾符號行,並收斂每行前後空白。"""
    cleaned = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if any(p.match(line) for p in _PAGE_NOISE_RES):
            continue
        if line and all(c in _DECORATIVE_CHARS for c in line):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def dehyphenate_and_merge(text: str) -> str:
    """
    去連字號斷字(hyphenation)+ 合併軟換行斷句。
    - 英文行尾以 "word-" 結尾、下一行以小寫字母開頭 → 直接拼接去掉連字號。
    - 一般軟換行(行尾非句末標點、下一行非大寫/中文/標題起頭、行夠長)→ 補空格拼接。
    """
    lines = text.splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        while i + 1 < len(lines) and lines[i + 1]:
            nxt = lines[i + 1]
            if re.search(r"[A-Za-z]-$", line) and re.match(r"^[a-z]", nxt):
                line = line[:-1] + nxt
                i += 1
                continue
            if (not re.search(r"[。！？!?.…]\s*$", line)
                    and not re.match(r"^[A-Z一-鿿#\-*]", nxt)
                    and len(line) > 40):
                line = line.rstrip() + " " + nxt.lstrip()
                i += 1
                continue
            break
        result.append(line)
        i += 1
    return "\n".join(result)


def collapse_blank_lines(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def strip_repeated_headers_footers(pages_text: list[str]) -> list[str]:
    """偵測出現在 >50% 頁面首/尾 3 行的重複行(常是頁首/頁尾)並移除。"""
    if len(pages_text) < 4:
        return pages_text

    all_lines = []
    for page in pages_text:
        lines = [l.strip() for l in page.splitlines() if l.strip()]
        all_lines.extend(lines[:3] + lines[-3:])

    line_counts = Counter(all_lines)
    threshold = max(3, len(pages_text) * 0.5)
    repeated = {line for line, count in line_counts.items() if count >= threshold and len(line) > 3}

    if not repeated:
        return pages_text

    cleaned = []
    for page in pages_text:
        lines = page.splitlines()
        cleaned.append("\n".join(l for l in lines if l.strip() not in repeated))
    return cleaned


# ── 章節偵測(啟發式,用於 PDF 無書籤 / TXT）──────────────────────────────

SECTION_PATTERNS = [
    (r"^第\s*([零一二三四五六七八九十百千\d]+)\s*章\s*(.{0,60})", "chapter"),
    (r"^第\s*([零一二三四五六七八九十百千\d]+)\s*部\s*(.{0,60})", "part"),
    (r"^第\s*([零一二三四五六七八九十百千\d]+)\s*[節节]\s*(.{0,60})", "section"),
    (r"^第\s*([零一二三四五六七八九十百千\d]+)\s*[講讲]\s*(.{0,60})", "lecture"),
    (r"^第\s*([零一二三四五六七八九十百千\d]+)\s*[課课]\s*(.{0,60})", "lesson"),
    (r"^第\s*([零一二三四五六七八九十百千\d]+)\s*單元\s*(.{0,60})", "unit"),
    (r"^第\s*([零一二三四五六七八九十百千\d]+)\s*单元\s*(.{0,60})", "unit"),
    (r"^Chapter\s+(\d+|[IVXLCDM]+)[:\.\s]\s*(.{0,60})", "chapter"),
    (r"^Part\s+(\d+|[IVXLCDM]+)[:\.\s]\s*(.{0,60})", "part"),
    (r"^Section\s+(\d+\.?\d*)[:\.\s]\s*(.{0,60})", "section"),
    (r"^Lecture\s+(\d+)[:\.\s]\s*(.{0,60})", "lecture"),
    (r"^Lesson\s+(\d+)[:\.\s]\s*(.{0,60})", "lesson"),
    (r"^Module\s+(\d+)[:\.\s]\s*(.{0,60})", "module"),
]
# 2026-07-31 對抗性驗收 Critical-1:移除過的 numbered/numbered_sub pattern
# (`^\d+\.\s+...` / `^\d+\.\d+...`)。好學生筆記的輸入主力是條列步驟
# (「1. 先讀目錄抓框架」「2. 用便利貼標記重點頁」……),純文字層級無法可靠
# 分辨「這是子章節標題」還是「這是條列項」,誤判會把整份筆記的章節結構污染
# 成噪音。子章節偵測改用更可靠信號(PDF 書籤 TOC),沒有書籤就讓編號清單維持
# 條列文字,不再猜測升格。

_SUBSECTION_LEVELS = {"section"}


def detect_sections(text: str) -> list[dict]:
    """回傳 [{'line_index', 'level', 'original_line'}, ...],偵測依原文行序。"""
    sections = []
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or len(stripped) < 2:
            continue
        for pattern, level in SECTION_PATTERNS:
            m = re.match(pattern, stripped, re.IGNORECASE)
            if m:
                sections.append({"line_index": i, "level": level, "original_line": stripped})
                break
    return sections


def heading_prefix(level: str) -> str:
    return "###" if level in _SUBSECTION_LEVELS else "##"


def assemble_with_heuristic_sections(text: str) -> str:
    """把啟發式偵測到的章節行原地換成 ##/### 標題(保留原文順序)。"""
    lines = text.splitlines()
    sec_by_line = {s["line_index"]: s for s in detect_sections(text)}
    out = []
    for i, line in enumerate(lines):
        if i in sec_by_line:
            sec = sec_by_line[i]
            out.append("")
            out.append(f"{heading_prefix(sec['level'])} {sec['original_line']}")
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


# ── PDF 抽取(含直排偵測與重排）──────────────────────────────────────────


def _line_is_vertical(direction: tuple[float, float]) -> bool:
    dx, dy = direction
    return abs(dy) > abs(dx)


def _analyze_page(page) -> tuple[str, bool]:
    """
    回傳(該頁文字, 是否為直排頁)。
    直排頁的判定:get_text('dict') 抽出的 text line 中,'dir' 向量 y 分量
    dominant(abs(dy) > abs(dx))的行數超過半數。
    直排頁的重排順序 = 欄序右→左(bbox x 中心由大到小)、欄內依 span 原序
    (PyMuPDF 對垂直書寫模式的 line 本身已經是「一欄」,span 順序即欄內讀序)。
    """
    d = page.get_text("dict")
    lines = []
    for block in d.get("blocks", []):
        if block.get("type", 0) != 0:  # 跳過圖片 block
            continue
        lines.extend(block.get("lines", []))

    if not lines:
        return page.get_text("text"), False

    vertical_lines = [l for l in lines if _line_is_vertical(l.get("dir", (1, 0)))]
    is_vertical = (len(vertical_lines) / len(lines)) > 0.5

    if not is_vertical:
        return page.get_text("text"), False

    def x_center(l):
        x0, _y0, x1, _y1 = l["bbox"]
        return (x0 + x1) / 2

    ordered = sorted(lines, key=lambda l: (-x_center(l), l["bbox"][1]))
    parts = ["".join(span["text"] for span in l["spans"]) for l in ordered]
    text = "\n".join(p for p in parts if p.strip())
    return text, True


def extract_pdf(path: str) -> tuple[list[str], int, list]:
    """回傳(每頁文字 list, 直排頁數, TOC list[[level, title, page], ...])。"""
    import fitz

    doc = fitz.open(path)
    total_chars = sum(len(page.get_text()) for page in doc)
    avg_chars = total_chars / max(len(doc), 1)
    if avg_chars < 80:
        print(f"[extract] WARN: 文字密度偏低(平均 {avg_chars:.0f} 字/頁),"
              f"可能是掃描 PDF,本抽取器不含 OCR。", file=sys.stderr)

    pages_text = []
    vertical_count = 0
    for page in doc:
        text, is_vertical = _analyze_page(page)
        if is_vertical:
            vertical_count += 1
        pages_text.append(text)

    toc = doc.get_toc() or []
    doc.close()
    return pages_text, vertical_count, toc


def _build_toc_items(pages_text: list[str], toc: list) -> list[tuple]:
    breakpoints: dict[int, list[tuple]] = {}
    for level, title, page_num in toc:
        idx = page_num - 1
        if 0 <= idx < len(pages_text):
            breakpoints.setdefault(idx, []).append((level, title))

    items = []
    buf: list[str] = []
    for i, ptext in enumerate(pages_text):
        if i in breakpoints:
            if buf:
                items.append(("text", "\n".join(buf)))
                buf = []
            for level, title in breakpoints[i]:
                items.append(("heading", level, title))
        buf.append(ptext)
    if buf:
        items.append(("text", "\n".join(buf)))
    return items


def build_pdf_markdown(pages_text: list[str], toc: list) -> str:
    pages_text = [strip_garbled_chars(p) for p in pages_text]
    pages_text = strip_repeated_headers_footers(pages_text)

    if toc:
        parts = []
        for item in _build_toc_items(pages_text, toc):
            if item[0] == "heading":
                _, level_num, title = item
                prefix = "###" if level_num >= 2 else "##"
                parts += ["", f"{prefix} {title.strip()}", ""]
            else:
                _, chunk = item
                cleaned = clean_text_block(chunk)
                cleaned = dehyphenate_and_merge(cleaned)
                parts.append(cleaned)
        return "\n".join(parts)

    full_text = clean_text_block("\n".join(pages_text))
    full_text = dehyphenate_and_merge(full_text)
    return assemble_with_heuristic_sections(full_text)


# ── EPUB 抽取 ─────────────────────────────────────────────────────────

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
               "li", "tr", "blockquote", "br", "section", "article"}


def _walk_block_text(el, out: list[str]) -> None:
    if el.tag in _BLOCK_TAGS:
        out.append("\n")
    if el.text:
        out.append(el.text)
    for child in el:
        _walk_block_text(child, out)
        if child.tail:
            out.append(child.tail)
    if el.tag in _BLOCK_TAGS:
        out.append("\n")


def _html_chapter_title_and_text(html_content: bytes) -> tuple[str, str]:
    import lxml.html as lh

    doc = lh.fromstring(html_content)
    for bad in doc.xpath("//script|//style|//nav|//head"):
        bad.drop_tree()

    title = ""
    heading_els = doc.xpath("//h1|//h2|//h3")
    if heading_els:
        title = heading_els[0].text_content().strip()
        heading_els[0].drop_tree()

    out: list[str] = []
    _walk_block_text(doc, out)
    text = re.sub(r"\n{3,}", "\n\n", "".join(out))
    return title, text.strip()


def _flatten_toc(entries) -> list[tuple[int, str, str]]:
    """
    把 book.toc(ebooklib 巢狀結構:Link 葉節點 / (Section, children) tuple /
    純 list 分組)攤平成 [(depth, title, href), ...],depth 從 1 起算、跟隨巢狀層級。
    """
    def walk(items, depth):
        flat = []
        for entry in items:
            if isinstance(entry, tuple):
                head, children = entry
                href = getattr(head, "href", None)
                title = getattr(head, "title", None)
                if href and title:
                    flat.append((depth, title.strip(), href))
                flat.extend(walk(children, depth + 1))
            elif isinstance(entry, list):
                flat.extend(walk(entry, depth))
            else:
                href = getattr(entry, "href", None)
                title = getattr(entry, "title", None)
                if href and title:
                    flat.append((depth, title.strip(), href))
        return flat

    return walk(entries, 1)


def _build_toc_href_map(toc_entries) -> tuple[dict[str, tuple[int, str]], dict[str, tuple[int, str]]]:
    """
    把攤平後的 toc 條目依 href(去掉 #fragment、去 URL escape)對應到它指向的
    spine 檔案,回傳(完整路徑對照表, 檔名對照表)兩層——完整路徑優先比對,
    對不到才退回只比檔名(不同 EPUB 打包時 toc href 跟 item 內部路徑前綴
    可能不一致,檔名兜底比較穩)。同一檔案被多個 toc 條目指到(常見於外層
    Section 跟其子 Link 都指同一頁不同錨點)時,第一個(層級最淺)那個赢。
    """
    by_full: dict[str, tuple[int, str]] = {}
    by_basename: dict[str, tuple[int, str]] = {}
    for level, title, href in _flatten_toc(toc_entries):
        base = unquote(href.split("#", 1)[0])
        if not base:
            continue
        if base not in by_full:
            by_full[base] = (level, title)
        basename = base.rsplit("/", 1)[-1]
        if basename not in by_basename:
            by_basename[basename] = (level, title)
    return by_full, by_basename


def extract_epub(path: str) -> list[tuple[int, str, str]]:
    """
    回傳 [(標題層級, 章節標題, 章節內文), ...],依 spine 讀序,略過 nav/目錄頁。

    章節標題來源優先順序(跟 PDF 路徑用 doc.get_toc() 書籤標題同一邏輯):
    1. toc/nav 的標題文字(book.toc,對應到該 spine 檔的 href)——EPUB 的
       目錄結構本來就是作者填的真實章名,比內文猜 h1-h3 可靠得多。
    2. 對不到 toc 的節,退回內文第一個 h1/h2/h3。
    3. 兩者都沒有,退回「第N章」(N=該節在 spine 讀序中的位置)。
    標題層級:toc depth 1 → ##,depth ≥2 → ###(跟 PDF TOC level 換算一致);
    退回 h1-h3 或「第N章」一律當 ## 章節,不臆測子層級。
    """
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(path)
    toc_by_full, toc_by_basename = _build_toc_href_map(book.toc)

    def is_real_chapter(item) -> bool:
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            return False
        if hasattr(item, "is_chapter") and not item.is_chapter():
            return False
        return True

    ordered_items = []
    for idref, linear in book.spine:
        # linear="no" 是 EPUB 規範標「非閱讀順序輔助頁」的正式欄位(作者手動
        # 標的目錄/封面/勘誤頁等),跟 ebooklib 自動產生的 EpubNav 是兩回事——
        # is_chapter() 只擋得住後者,這裡要另外擋 linear="no"。
        if linear == "no":
            continue
        item = book.get_item_with_id(idref)
        if item is not None and is_real_chapter(item):
            ordered_items.append(item)

    if not ordered_items:
        ordered_items = [it for it in book.get_items_of_type(ebooklib.ITEM_DOCUMENT) if is_real_chapter(it)]

    chapters = []
    for idx, item in enumerate(ordered_items, 1):
        html_title, text = _html_chapter_title_and_text(item.get_content())
        name = item.get_name()
        toc_entry = toc_by_full.get(name) or toc_by_basename.get(name.rsplit("/", 1)[-1])
        if toc_entry:
            level, title = toc_entry
            heading_level = 2 if level >= 2 else 1
        elif html_title:
            heading_level, title = 1, html_title
        else:
            heading_level, title = 1, f"第{idx}章"
        chapters.append((heading_level, title, text))
    return chapters


def build_epub_markdown(chapters: list[tuple[int, str, str]]) -> str:
    parts = []
    for heading_level, title, text in chapters:
        prefix = "###" if heading_level >= 2 else "##"
        cleaned = clean_text_block(strip_garbled_chars(text))
        cleaned = dehyphenate_and_merge(cleaned)
        parts += ["", f"{prefix} {title}", "", cleaned]
    return "\n".join(parts)


# ── TXT 抽取 ──────────────────────────────────────────────────────────


def detect_encoding(raw: bytes) -> str:
    import chardet

    result = chardet.detect(raw)
    enc = (result.get("encoding") or "utf-8").lower().replace("-", "").replace("_", "")
    mapping = {"big5hkscs": "big5", "gb2312": "gb18030", "gbk": "gb18030", "gb18030": "gb18030"}
    return mapping.get(enc, enc)


def extract_txt(path: str) -> str:
    raw = Path(path).read_bytes()
    enc = detect_encoding(raw)
    try:
        return raw.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def build_txt_markdown(raw_text: str) -> str:
    cleaned = clean_text_block(strip_garbled_chars(raw_text))
    cleaned = dehyphenate_and_merge(cleaned)
    return assemble_with_heuristic_sections(cleaned)


# ── --corrections 補充修正(可選,find=replace 逐行對照表)──────────────
# 命名與音檔線的 --context(自由文本餵 initial_prompt)刻意區隔開來,
# 見檔頭說明。

def apply_corrections(text: str, corrections_path: str | None) -> str:
    if not corrections_path:
        return text
    path = Path(corrections_path)
    if not path.is_file():
        print(f"[extract] WARN: --corrections 檔案不存在,略過: {corrections_path}", file=sys.stderr)
        return text

    corrections = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        find, _, replace = line.partition("=")
        find = find.strip()
        if find:
            corrections.append((find, replace.strip()))

    for find, replace in corrections:
        text = text.replace(find, replace)
    return text


# ── 主流程 ────────────────────────────────────────────────────────────


def process_file(input_path: str, output_path: str, corrections_path: str | None = None) -> dict:
    path = Path(input_path)
    if not path.is_file():
        raise ExtractError(f"檔案不存在: {input_path}")

    ext = path.suffix.lower()
    vertical_pages = 0

    if ext == ".pdf":
        try:
            pages_text, vertical_pages, toc = extract_pdf(str(path))
        except ExtractError:
            raise
        except Exception as e:
            raise ExtractError(f"PDF 解析失敗: {e}") from e
        body = build_pdf_markdown(pages_text, toc)
    elif ext == ".epub":
        try:
            chapters = extract_epub(str(path))
        except Exception as e:
            raise ExtractError(f"EPUB 解析失敗: {e}") from e
        body = build_epub_markdown(chapters)
    elif ext == ".txt":
        try:
            raw_text = extract_txt(str(path))
        except Exception as e:
            raise ExtractError(f"TXT 解析失敗: {e}") from e
        body = build_txt_markdown(raw_text)
    else:
        raise ExtractError(f"不支援的格式: {ext}(僅支援 .pdf / .epub / .txt)")

    final_text = convert_to_tw(body)
    final_text = apply_corrections(final_text, corrections_path)
    final_text = collapse_blank_lines(final_text).strip()

    if not final_text:
        raise ExtractError("抽出空內容")

    final_text += "\n"

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(final_text, encoding="utf-8")

    sections = len(re.findall(r"^#{2,3}\s", final_text, re.MULTILINE))
    stats = {
        "input_type": ext.lstrip("."),
        "chars": len(final_text),
        "sections": sections,
        "lang": detect_lang(final_text),
        "vertical_pages": vertical_pages,
    }
    return stats


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="確定性文件抽取器(PDF/EPUB/TXT → 結構化繁體 markdown)")
    ap.add_argument("input", help="輸入檔案(.pdf / .epub / .txt)")
    ap.add_argument("-o", "--output", required=True, help="輸出 markdown 路徑")
    ap.add_argument("--corrections", default=None,
                     help="補充修正對照表(每行 find=replace),可選;"
                          "與音檔線 --context(餵 ASR initial_prompt)語意不同,故不共用旗標名")
    args = ap.parse_args()

    check_deps()

    try:
        stats = process_file(args.input, args.output, args.corrections)
    except ExtractError as e:
        print(f"[extract] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
