#!/usr/bin/env python3
"""
doc_to_md.py — Student-friendly document to Markdown converter
Supports: PDF, EPUB, TXT
Output: Clean Markdown with YAML frontmatter + section anchors for Claude to annotate
"""

import sys
import os
import re
import argparse
import datetime
from pathlib import Path
from collections import Counter

# ── Dependency checks ──────────────────────────────────────────────────────────

def check_deps():
    missing = []
    for pkg, import_name in [
        ("PyMuPDF", "fitz"),
        ("ebooklib", "ebooklib"),
        ("beautifulsoup4", "bs4"),
        ("chardet", "chardet"),
        ("opencc-python-reimplemented", "opencc"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[ERROR] Missing packages: {', '.join(missing)}")
        print("Install with:")
        print(f"  pip install {' '.join(missing)} --break-system-packages")
        sys.exit(1)

check_deps()

import chardet
import fitz  # PyMuPDF
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

try:
    import opencc
    OPENCC_AVAILABLE = True
except ImportError:
    OPENCC_AVAILABLE = False

# ── Section detection patterns ─────────────────────────────────────────────────

BOOK_SECTION_PATTERNS = [
    # Chinese book chapters (both Traditional 節 and Simplified 节 forms)
    (r'^第\s*([零一二三四五六七八九十百千\d]+)\s*章\s*(.{0,60})', 'chapter'),
    (r'^第\s*([零一二三四五六七八九十百千\d]+)\s*部\s*(.{0,60})', 'part'),
    (r'^第\s*([零一二三四五六七八九十百千\d]+)\s*[節节]\s*(.{0,60})', 'section'),
    # English book chapters
    (r'^Chapter\s+(\d+|[IVXLCDM]+)[:\.\s]\s*(.{0,60})', 'chapter'),
    (r'^Part\s+(\d+|[IVXLCDM]+)[:\.\s]\s*(.{0,60})', 'part'),
    (r'^Section\s+(\d+\.?\d*)[:\.\s]\s*(.{0,60})', 'section'),
    # Numbered headings like "1. Introduction" or "1.1 Overview"
    (r'^(\d+)\.\s+([A-Z\u4e00-\u9fff].{0,60})', 'numbered'),
]

TRANSCRIPT_SECTION_PATTERNS = [
    # Chinese lecture/course (both Traditional 講/課 and Simplified 讲/课 forms)
    (r'^第\s*([零一二三四五六七八九十百千\d]+)\s*[講讲]\s*(.{0,60})', 'lecture'),
    (r'^第\s*([零一二三四五六七八九十百千\d]+)\s*[課课]\s*(.{0,60})', 'lesson'),
    (r'^第\s*([零一二三四五六七八九十百千\d]+)\s*單元\s*(.{0,60})', 'unit'),
    (r'^第\s*([零一二三四五六七八九十百千\d]+)\s*单元\s*(.{0,60})', 'unit'),
    # English lecture
    (r'^Lecture\s+(\d+)[:\.\s]\s*(.{0,60})', 'lecture'),
    (r'^Lesson\s+(\d+)[:\.\s]\s*(.{0,60})', 'lesson'),
    (r'^Module\s+(\d+)[:\.\s]\s*(.{0,60})', 'module'),
    # Timestamps (transcript style)
    (r'^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.{0,80})', 'timestamp'),
]

ALL_SECTION_PATTERNS = BOOK_SECTION_PATTERNS + TRANSCRIPT_SECTION_PATTERNS

# ── Chinese number conversion ──────────────────────────────────────────────────

ZH_DIGITS = {'零':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'百':100,'千':1000}

def zh_num_to_int(s):
    """Convert Chinese number string to integer (best-effort)."""
    if s.isdigit():
        return int(s)
    result, current = 0, 0
    for ch in s:
        if ch in ZH_DIGITS:
            val = ZH_DIGITS[ch]
            if val >= 10:
                if current == 0:
                    current = 1
                result += current * val
                current = 0
            else:
                current = val
    return result + current

# ── OpenCC conversion ──────────────────────────────────────────────────────────

_converter = None

def get_converter():
    global _converter
    if _converter is None and OPENCC_AVAILABLE:
        try:
            _converter = opencc.OpenCC('s2twp')
        except Exception:
            _converter = None
    return _converter

def convert_to_tw(text: str) -> str:
    conv = get_converter()
    if conv is None:
        return text
    try:
        return conv.convert(text)
    except Exception:
        return text

# ── Text cleaning utilities ────────────────────────────────────────────────────

def detect_encoding(raw: bytes) -> str:
    result = chardet.detect(raw)
    enc = result.get('encoding') or 'utf-8'
    # Normalize common aliases
    enc = enc.lower().replace('-', '').replace('_', '')
    mapping = {'big5hkscs': 'big5', 'gb2312': 'gb18030', 'gbk': 'gb18030', 'gb18030': 'gb18030'}
    return mapping.get(enc, enc)

def clean_text_block(text: str) -> str:
    """Remove common PDF artifacts and normalize whitespace."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        # Strip leading/trailing whitespace
        line = line.strip()
        # Skip very short lines that are likely page numbers or artifacts
        if re.match(r'^\d+$', line) and len(line) <= 4:
            continue
        # Skip lines that are only punctuation/symbols (common PDF artifacts)
        if line and all(c in '─━—–-=~·•◆▪□■○●※★☆†‡§¶' for c in line):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)

def merge_broken_paragraphs(text: str) -> str:
    """
    Merge lines that were broken mid-sentence (soft line breaks in PDFs).
    Heuristic: if a line does NOT end with sentence-ending punctuation
    and the next line does NOT start with a capital letter or Chinese char at start of sentence,
    merge them.
    """
    lines = text.splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Check if this line should be merged with next
        while (i + 1 < len(lines)
               and line
               and lines[i + 1]
               and not re.search(r'[。！？!?\.…]\s*$', line)
               and not re.match(r'^[A-Z\u4e00-\u9fff#\-\*]', lines[i + 1])
               and not re.match(r'^\s*$', lines[i + 1])
               and len(line) > 40):  # Only merge reasonably long lines
            i += 1
            line = line.rstrip() + ' ' + lines[i].lstrip()
        result.append(line)
        i += 1
    return '\n'.join(result)

def strip_repeated_headers_footers(pages_text: list[str]) -> list[str]:
    """
    Detect lines appearing on >50% of pages and remove them
    (these are likely headers/footers).
    """
    if len(pages_text) < 4:
        return pages_text

    # Count line frequency across pages
    all_lines = []
    for page in pages_text:
        # Only check first 3 and last 3 lines of each page
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
        filtered = [l for l in lines if l.strip() not in repeated]
        cleaned.append('\n'.join(filtered))
    return cleaned

# ── PDF extraction ─────────────────────────────────────────────────────────────

def extract_pdf(path: str) -> tuple[dict, list[str]]:
    """Returns (metadata, pages_text_list)."""
    doc = fitz.open(path)
    meta = doc.metadata or {}

    # Check for image-only PDF
    total_chars = sum(len(page.get_text()) for page in doc)
    avg_chars = total_chars / max(len(doc), 1)
    if avg_chars < 80:
        print(f"[WARN] Low text density ({avg_chars:.0f} chars/page avg). This may be a scanned PDF.")
        print("[WARN] OCR not included — consider using Adobe Acrobat, tesseract, or MinerU first.")

    pages = []
    for page in doc:
        text = page.get_text("text")
        pages.append(text)

    doc.close()
    return meta, pages

def pdf_meta_to_dict(raw_meta: dict, path: str) -> dict:
    title = raw_meta.get('title') or Path(path).stem
    author = raw_meta.get('author') or 'Unknown'
    total_pages = raw_meta.get('page_count') or 0
    return {
        'title': title.strip(),
        'author': author.strip(),
        'source': Path(path).name,
        'pages': total_pages,
        'format': 'PDF',
    }

# ── EPUB extraction ────────────────────────────────────────────────────────────

def extract_epub(path: str) -> tuple[dict, list[str]]:
    """Returns (metadata, chapters_text_list)."""
    book = epub.read_epub(path)

    meta = {}
    for key in ['title', 'creator', 'language', 'identifier']:
        items = book.get_metadata('DC', key)
        if items:
            meta[key] = items[0][0] if isinstance(items[0], tuple) else str(items[0])

    chapters = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        try:
            content = item.get_content()
            soup = BeautifulSoup(content, 'lxml')
            # Remove script, style, nav
            for tag in soup(['script', 'style', 'nav', 'head']):
                tag.decompose()
            text = soup.get_text(separator='\n')
            text = re.sub(r'\n{3,}', '\n\n', text)
            if text.strip():
                chapters.append(text.strip())
        except Exception:
            continue

    return meta, chapters

def epub_meta_to_dict(raw_meta: dict, path: str) -> dict:
    return {
        'title': (raw_meta.get('title') or Path(path).stem).strip(),
        'author': (raw_meta.get('creator') or 'Unknown').strip(),
        'source': Path(path).name,
        'pages': len([]),  # Will be updated later
        'format': 'EPUB',
    }

# ── TXT extraction ─────────────────────────────────────────────────────────────

def extract_txt(path: str) -> tuple[dict, list[str]]:
    """Returns (metadata, [full_text])."""
    raw = Path(path).read_bytes()
    enc = detect_encoding(raw)
    try:
        text = raw.decode(enc, errors='replace')
    except (LookupError, UnicodeDecodeError):
        text = raw.decode('utf-8', errors='replace')

    meta = {'title': Path(path).stem, 'author': 'Unknown', 'format': 'TXT'}
    return meta, [text]

def txt_meta_to_dict(raw_meta: dict, path: str) -> dict:
    return {
        'title': raw_meta.get('title', Path(path).stem).strip(),
        'author': raw_meta.get('author', 'Unknown').strip(),
        'source': Path(path).name,
        'pages': 0,
        'format': 'TXT',
    }

# ── Section detection ──────────────────────────────────────────────────────────

def detect_sections(text: str) -> list[dict]:
    """
    Detect section boundaries. Returns list of:
    {'index': int, 'level': 'chapter'|'part'|..., 'num': str, 'title': str, 'line': str}
    """
    sections = []
    lines = text.splitlines()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) < 2:
            continue
        for pattern, level in ALL_SECTION_PATTERNS:
            m = re.match(pattern, stripped, re.IGNORECASE)
            if m:
                num_raw = m.group(1)
                title_raw = m.group(2).strip() if len(m.groups()) >= 2 else ''
                # Clean title (remove trailing punctuation)
                title_raw = re.sub(r'[：:。\s]+$', '', title_raw)
                sections.append({
                    'line_index': i,
                    'level': level,
                    'num': num_raw,
                    'title': title_raw,
                    'original_line': stripped,
                })
                break  # Only match first pattern per line

    return sections

def assign_anchors(sections: list[dict]) -> list[dict]:
    """Assign Obsidian-compatible anchor IDs to each section."""
    chapter_count = 0
    for sec in sections:
        level = sec['level']
        if level in ('chapter', 'lecture', 'lesson', 'unit', 'module', 'numbered'):
            chapter_count += 1
            sec['anchor'] = f"ch-{chapter_count:02d}"
        elif level == 'part':
            sec['anchor'] = f"part-{sec['num']}"
        elif level == 'timestamp':
            sec['anchor'] = f"ts-{sec['num'].replace(':', '-')}"
        else:
            sec['anchor'] = f"sec-{sec['num']}"
    return sections

# ── Markdown assembly ──────────────────────────────────────────────────────────

def make_yaml_frontmatter(meta: dict) -> str:
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [
        '---',
        f"title: \"{meta.get('title', 'Unknown').replace(chr(34), chr(39))}\"",
        f"author: \"{meta.get('author', 'Unknown').replace(chr(34), chr(39))}\"",
        f"source: \"{meta.get('source', '')}\"",
        f"format: {meta.get('format', 'Unknown')}",
        f"pages: {meta.get('pages', 0)}",
        f"converted_at: \"{now}\"",
        '---',
    ]
    return '\n'.join(lines)

def make_section_heading(sec: dict) -> str:
    """Generate heading with anchor for a detected section."""
    level = sec['level']
    num = sec['num']
    title = sec['title']
    anchor = sec.get('anchor', '')

    if level in ('chapter',):
        prefix = f"第{num}章"
        if title:
            heading = f"## {prefix} {title}"
        else:
            heading = f"## {prefix}"
    elif level == 'part':
        prefix = f"第{num}部"
        heading = f"# {prefix} {title}" if title else f"# {prefix}"
    elif level in ('lecture',):
        prefix = f"第{num}講"
        heading = f"## {prefix} {title}" if title else f"## {prefix}"
    elif level in ('lesson',):
        prefix = f"第{num}課"
        heading = f"## {prefix} {title}" if title else f"## {prefix}"
    elif level == 'unit':
        prefix = f"第{num}單元"
        heading = f"## {prefix} {title}" if title else f"## {prefix}"
    elif level == 'timestamp':
        heading = f"### {num} {title}" if title else f"### {num}"
    elif level == 'numbered':
        heading = f"## {num}. {title}"
    else:
        # Fallback: use original line as heading
        heading = f"## {sec['original_line']}"

    if anchor:
        heading += f" ^{anchor}"
    return heading

def section_placeholder(anchor: str) -> str:
    """Generate the placeholder callout block Claude will fill in."""
    return (
        f"> [!note] 章節摘要\n"
        f"> **摘要**：（Claude 將在此填入本節摘要）\n"
        f"> **關鍵字**：（Claude 將在此填入關鍵字）"
    )

def build_markdown(meta: dict, full_text: str, sections: list[dict], convert_chinese: bool) -> str:
    """Assemble the final Markdown document."""
    if convert_chinese and OPENCC_AVAILABLE:
        full_text = convert_to_tw(full_text)
        for sec in sections:
            sec['title'] = convert_to_tw(sec['title'])

    frontmatter = make_yaml_frontmatter(meta)
    lines = full_text.splitlines()

    # Build a map: line_index → section
    sec_map = {sec['line_index']: sec for sec in sections}

    output_parts = [frontmatter, '']

    # If no sections detected, add a single placeholder
    if not sections:
        # Add a generic document intro callout
        output_parts.append(f"# {meta.get('title', 'Document')}")
        output_parts.append('')
        output_parts.append(section_placeholder('doc-main'))
        output_parts.append('')
        output_parts.append(full_text)
        return '\n'.join(output_parts)

    # Add document title
    output_parts.append(f"# {meta.get('title', 'Document')}")
    output_parts.append('')

    current_section_lines = []
    current_anchor = None
    in_section = False

    for i, line in enumerate(lines):
        if i in sec_map:
            # Flush previous section content
            if in_section and current_section_lines:
                content = '\n'.join(current_section_lines).strip()
                if content:
                    output_parts.append(content)
                output_parts.append('')

            # Emit new section heading
            sec = sec_map[i]
            output_parts.append(make_section_heading(sec))
            output_parts.append('')
            output_parts.append(section_placeholder(sec.get('anchor', '')))
            output_parts.append('')

            current_section_lines = []
            current_anchor = sec.get('anchor')
            in_section = True
        else:
            if in_section:
                current_section_lines.append(line)
            else:
                # Pre-section content (intro, preface, etc.)
                output_parts.append(line)

    # Flush last section
    if in_section and current_section_lines:
        content = '\n'.join(current_section_lines).strip()
        if content:
            output_parts.append(content)

    return '\n'.join(output_parts)

# ── Main pipeline ──────────────────────────────────────────────────────────────

def process_file(input_path: str, output_dir: str, convert_chinese: bool) -> str:
    """Full pipeline. Returns path to output Markdown file."""
    path = Path(input_path)
    ext = path.suffix.lower()

    print(f"[INFO] Processing: {path.name} ({ext.upper()[1:]})")

    # 1. Extract text and metadata
    if ext == '.pdf':
        raw_meta, pages = extract_pdf(input_path)
        meta = pdf_meta_to_dict(raw_meta, input_path)
        meta['pages'] = len(pages)
        pages = strip_repeated_headers_footers(pages)
        full_text = '\n'.join(pages)
    elif ext == '.epub':
        raw_meta, chapters = extract_epub(input_path)
        meta = epub_meta_to_dict(raw_meta, input_path)
        meta['pages'] = len(chapters)
        full_text = '\n\n'.join(chapters)
    elif ext == '.txt':
        raw_meta, parts = extract_txt(input_path)
        meta = txt_meta_to_dict(raw_meta, input_path)
        full_text = parts[0]
    else:
        print(f"[ERROR] Unsupported format: {ext}")
        sys.exit(1)

    print(f"[INFO] Extracted ~{len(full_text):,} characters")

    # 2. Clean text
    full_text = clean_text_block(full_text)
    full_text = merge_broken_paragraphs(full_text)

    # 3. Detect sections
    sections = detect_sections(full_text)
    sections = assign_anchors(sections)
    print(f"[INFO] Detected {len(sections)} sections")

    # 4. Build Markdown
    md_content = build_markdown(meta, full_text, sections, convert_chinese)

    # 5. Determine output filename (also apply opencc to title/author)
    raw_title = meta.get('title', path.stem)
    raw_author = meta.get('author', 'Unknown')
    if convert_chinese and OPENCC_AVAILABLE:
        raw_title = convert_to_tw(raw_title)
        raw_author = convert_to_tw(raw_author)
        # Also fix YAML frontmatter inside md_content
        md_content = md_content.replace(
            f'title: "{meta.get("title", "")}"',
            f'title: "{raw_title}"', 1)
        md_content = md_content.replace(
            f'author: "{meta.get("author", "")}"',
            f'author: "{raw_author}"', 1)
        # Fix H1 line
        md_content = md_content.replace(
            f'# {meta.get("title", "")}',
            f'# {raw_title}', 1)
    title = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', raw_title)
    title = re.sub(r'\s+', '_', title.strip())[:50]
    author = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', raw_author)
    author = re.sub(r'\s+', '_', author.strip())[:20]

    if author and author != 'Unknown':
        out_name = f"{author}_{title}_知識庫.md"
    else:
        out_name = f"{title}_知識庫.md"

    out_path = Path(output_dir) / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md_content, encoding='utf-8')

    print(f"[INFO] Output: {out_path}")
    print(f"[INFO] Size: {out_path.stat().st_size:,} bytes")
    print(f"[INFO] Sections with placeholders: {len(sections)}")

    return str(out_path)

# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Convert PDF/EPUB/TXT to clean Markdown with section anchors',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 doc_to_md.py book.pdf
  python3 doc_to_md.py --auto lecture.txt -o ~/Documents/output/
  python3 doc_to_md.py --no-convert-chinese ebook.epub -o ./output/
        """
    )
    parser.add_argument('input', help='Input file (PDF, EPUB, or TXT)')
    parser.add_argument('--auto', action='store_true',
                        help='Auto-detect format (default behavior, kept for compatibility)')
    parser.add_argument('--no-convert-chinese', action='store_true',
                        help='Skip Simplified → Traditional Chinese conversion')
    parser.add_argument('-o', '--output-dir', default=None,
                        help='Output directory (default: same directory as input file)')

    args = parser.parse_args()

    # Resolve paths
    input_path = os.path.expanduser(args.input)
    if not os.path.isfile(input_path):
        print(f"[ERROR] File not found: {input_path}")
        sys.exit(1)

    if args.output_dir:
        output_dir = os.path.expanduser(args.output_dir)
    else:
        output_dir = str(Path(input_path).parent)

    convert_chinese = not args.no_convert_chinese
    if convert_chinese and not OPENCC_AVAILABLE:
        print("[WARN] opencc not available, skipping Chinese conversion")
        convert_chinese = False

    # Run pipeline
    out_path = process_file(input_path, output_dir, convert_chinese)
    print(f"\n[DONE] Markdown saved to:\n  {out_path}")
    print("\nNext step: Ask Claude to read this file and fill in the 章節摘要 callouts.")

if __name__ == '__main__':
    main()
