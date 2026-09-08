# doc-to-md Usage Guide

Detailed examples, edge cases, and advanced usage for the `doc-to-md` skill.

---

## Installation Walkthrough

### macOS (Homebrew Python)
```bash
pip3 install PyMuPDF ebooklib beautifulsoup4 chardet opencc-python-reimplemented lxml \
  --break-system-packages
```

### Windows (PowerShell)
```powershell
pip install PyMuPDF ebooklib beautifulsoup4 chardet opencc-python-reimplemented lxml
```

### Linux (apt Python)
```bash
pip3 install PyMuPDF ebooklib beautifulsoup4 chardet opencc-python-reimplemented lxml \
  --break-system-packages
```

### Using a Virtual Environment (recommended for students)
```bash
python3 -m venv doc-env
source doc-env/bin/activate        # macOS/Linux
# doc-env\Scripts\activate.bat     # Windows
pip install PyMuPDF ebooklib beautifulsoup4 chardet opencc-python-reimplemented lxml
python3 ~/.claude/skills/doc-to-md/scripts/doc_to_md.py book.pdf
```

---

## Usage Examples

### Basic PDF conversion
```bash
python3 ~/.claude/skills/doc-to-md/scripts/doc_to_md.py "Atomic Habits.pdf"
# Output: Atomic_Habits_知識庫.md  (same folder)
```

### PDF to specific output folder
```bash
python3 ~/.claude/skills/doc-to-md/scripts/doc_to_md.py \
  "第一性原理.pdf" \
  -o ~/Documents/Obsidian/original/ebook/
```

### EPUB book
```bash
python3 ~/.claude/skills/doc-to-md/scripts/doc_to_md.py \
  --auto "deep_work.epub" \
  -o ~/Documents/output/
```

### TXT transcript (no Chinese conversion)
```bash
python3 ~/.claude/skills/doc-to-md/scripts/doc_to_md.py \
  --auto "lecture_transcript.txt" \
  --no-convert-chinese \
  -o ~/Documents/output/
```

---

## Output Format

### YAML Frontmatter
```yaml
---
title: "原子習慣"
author: "詹姆斯·克利爾"
source: "atomic-habits-zh.pdf"
format: PDF
pages: 320
converted_at: "2026-04-14 10:30"
---
```

### Section Heading with Anchor
```markdown
## 第1章 微小改變帶來的驚人力量 ^ch-01
```

### Section Placeholder (before Claude fills it)
```markdown
> [!note] 章節摘要
> **摘要**：（Claude 將在此填入本節摘要）
> **關鍵字**：（Claude 將在此填入關鍵字）
```

### After Claude Annotation
```markdown
> [!note] 章節摘要
> **摘要**：本章介紹「1%法則」——每天微小的改善，一年後會帶來37倍的複利效果。作者以英國自行車隊為例，說明累積優勢如何創造非凡成果。
> **關鍵字**：1%法則、複利效應、累積優勢、邊際增益、習慣系統
```

---

## Section Detection Patterns

The script detects sections matching these patterns (auto-detected):

### Books
| Pattern | Example | Type |
|---------|---------|------|
| `第N章 Title` | `第三章 習慣的科學` | chapter |
| `Chapter N: Title` | `Chapter 3: The Science of Habits` | chapter |
| `第N部 Title` | `第二部 行為改變的四個法則` | part |
| `Part N: Title` | `Part II: The Four Laws` | part |
| `N. Title` | `3. Building Better Habits` | numbered |

### Transcripts / Lectures
| Pattern | Example | Type |
|---------|---------|------|
| `第N講 Title` | `第五講 決策思考框架` | lecture |
| `第N課 Title` | `第一課 課程介紹` | lesson |
| `Lecture N: Title` | `Lecture 5: Decision Frameworks` | lecture |
| `HH:MM:SS Text` | `00:32:15 今天我們來談...` | timestamp |

---

## Handling Problematic Files

### Scanned / Image-only PDFs

**Symptom:** Script warns `Low text density (X chars/page avg)`

**Solution:** Pre-process with OCR first:
```bash
# Using tesseract (free)
tesseract input.pdf output_base pdf

# Using ocrmypdf (recommended)
pip install ocrmypdf
ocrmypdf input.pdf input_ocr.pdf
python3 doc_to_md.py input_ocr.pdf
```

### Garbled Chinese Text

**Symptom:** Output shows `□□□` or `???` characters

**Cause:** Encoding mismatch (Big5, GB2312, etc.)

**Solution 1:** Script auto-detects encoding for TXT files. Try:
```bash
python3 doc_to_md.py file.txt  # chardet handles it automatically
```

**Solution 2:** Manually specify by converting file first:
```bash
iconv -f big5 -t utf-8 input.txt > input_utf8.txt
python3 doc_to_md.py input_utf8.txt
```

### No Sections Detected

**Symptom:** Output has no `## Chapter` headings, only one big block

**Cause:** File uses non-standard heading format

**Solution:** After conversion, manually add headings in the Markdown, or ask Claude to detect and insert headings based on content flow.

### DRM-Protected EPUBs

**Symptom:** EPUB extracts but chapters are empty

**Cause:** Adobe DRM or other encryption

**Solution:** Use Calibre to remove DRM (only for books you own), then reconvert.

---

## Chinese Conversion Details

The script uses `opencc` with `s2twp` profile (Simplified → Traditional Taiwan):

| Input | Output |
|-------|--------|
| 软件 | 軟體 |
| 程序 | 程式 |
| 互联网 | 網際網路 |
| 视频 | 影片 |
| 文件 | 檔案 |

To skip conversion (e.g., book is already Traditional):
```bash
python3 doc_to_md.py book.pdf --no-convert-chinese
```

---

## Claude Annotation — Prompt Template

When Claude fills in the summaries, it uses this internal logic:

```
For each section in the Markdown file:
1. Read the heading and all body text until the next ## heading
2. Write a 2-4 sentence 摘要 in Traditional Chinese (Taiwan terms)
3. Extract 4-8 關鍵字 (single words or 2-4 char phrases)
4. Replace only the placeholder lines inside the > [!note] callout
5. Preserve all ^anchor tags and other content unchanged
```

**Example sections Claude handles well:**
- Dense academic text: extracts core arguments
- Narrative examples: identifies the principle being illustrated  
- Step-by-step instructions: summarizes the procedure
- Timestamp-based transcripts: captures main topic discussed

---

## File Naming Convention

| Condition | Output Filename |
|-----------|----------------|
| Author known | `{Author}_{Title}_知識庫.md` |
| Author unknown | `{Title}_知識庫.md` |
| Title has special chars | Special chars stripped, spaces → underscores |
| Title > 50 chars | Truncated to 50 chars |

**Examples:**
- `詹姆斯_克利爾_原子習慣_知識庫.md`
- `Cal_Newport_Deep_Work_知識庫.md`
- `第一性原理思考_知識庫.md`

---

## Integration with Obsidian

For direct Obsidian Vault integration:

```bash
python3 doc_to_md.py "book.pdf" -o ~/Documents/Obsidian/original/ebook/
```

The `^anchor-id` tags on headings are Obsidian block references, enabling:
```markdown
[[書名_知識庫#^ch-03|查看第3章]]
```

---

## Limitations

| Limitation | Details |
|-----------|---------|
| Scanned PDFs | No OCR — must pre-process |
| Images/charts | Not extracted (text-only output) |
| Complex PDF layouts | Tables, columns may merge incorrectly |
| DRM EPUB | Cannot decrypt |
| Very long TXT | No page count in metadata |
| Timestamp sections | Only detected if format is `HH:MM:SS` or `MM:SS` |
