#!/usr/bin/env python3
"""test_doc_extract.py — scripts/doc/extract.py 的行為鎖定測試。

extract.py 是文件輸入線的 Phase-1 確定性抽取器(0 token,不呼叫 LLM):
PDF(PyMuPDF)/EPUB(ebooklib)/TXT → 結構化繁體 markdown 純文字,供下游 phase-b 接手。
這裡鎖住:簡→繁(s2twp)、章節偵測成 ##/###、連字號斷字合併、頁碼/頁眉頁腳雜訊清除、
PDF 直排(vertical text)偵測與重排、stats JSON 欄位。

Fixture 一律程式內生成(不外連下載):
- TXT: 字串常數。
- PDF: fitz doc.new_page() + insert_text/insert_textbox 現場產生。
- EPUB: ebooklib EpubBook 現場產生。

跑法:
    /Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-doc/bin/python \
        -m pytest scripts/tests/test_doc_extract.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DOC_DIR = Path(__file__).resolve().parent.parent / "doc"
sys.path.insert(0, str(DOC_DIR))

import extract  # noqa: E402

try:
    import fitz  # noqa: E402
    HAVE_FITZ = True
except ImportError:
    HAVE_FITZ = False

try:
    import ebooklib  # noqa: E402
    from ebooklib import epub  # noqa: E402
    HAVE_EBOOKLIB = True
except ImportError:
    HAVE_EBOOKLIB = False


def run_cli(input_path: Path, output_path: Path, context_path: Path | None = None) -> tuple[int, str, str]:
    """透過 subprocess 跑實際 CLI(驗介面契約:argv/stdout stats JSON/exit code)。"""
    cmd = [sys.executable, str(DOC_DIR / "extract.py"), str(input_path), "-o", str(output_path)]
    if context_path:
        cmd += ["--context", str(context_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class TestTxtExtraction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

    def test_simplified_to_traditional_and_section_heading(self):
        src = self.tmp / "sample.txt"
        src.write_text(
            "第一章 软件设计\n"
            "\n"
            "这一段说明软件工程的基本概念,内容会被转成繁体。\n"
            "\n"
            "第二章 结论\n"
            "\n"
            "总结这一段内容。\n",
            encoding="utf-8",
        )
        out = self.tmp / "sample.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)

        content = out.read_text(encoding="utf-8")
        # s2twp 簡→繁台灣化:软件 → 軟體
        self.assertIn("軟體", content)
        self.assertNotIn("软件", content)
        # 章節標題被抽成 ##
        self.assertIn("## 第一章 軟體設計", content)
        self.assertIn("## 第二章 結論", content)

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["input_type"], "txt")
        self.assertEqual(stats["lang"], "zh")
        self.assertGreaterEqual(stats["sections"], 2)
        self.assertEqual(stats["chars"], len(content))
        self.assertIn("vertical_pages", stats)
        self.assertEqual(stats["vertical_pages"], 0)

    def test_no_yaml_frontmatter_no_code_fence_no_callout(self):
        src = self.tmp / "plain.txt"
        src.write_text("没有章节标题的一段普通文字。\n", encoding="utf-8")
        out = self.tmp / "plain.md"
        rc, _, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")
        self.assertFalse(content.startswith("---"))
        self.assertNotIn("```", content)
        self.assertNotIn("[!note]", content)
        self.assertNotIn("摘要", content)

    def test_missing_file_nonzero_exit(self):
        rc, _, stderr = run_cli(self.tmp / "does-not-exist.txt", self.tmp / "out.md")
        self.assertNotEqual(rc, 0)
        self.assertTrue(stderr.strip())

    def test_unsupported_format_nonzero_exit(self):
        src = self.tmp / "sample.docx"
        src.write_text("whatever", encoding="utf-8")
        rc, _, stderr = run_cli(src, self.tmp / "out.md")
        self.assertNotEqual(rc, 0)
        self.assertTrue(stderr.strip())

    def test_context_corrections_applied(self):
        src = self.tmp / "ctx.txt"
        src.write_text("这里有一个術語錯字需要修正。\n", encoding="utf-8")
        ctx = self.tmp / "context.txt"
        ctx.write_text("術語錯字=正確術語\n", encoding="utf-8")
        out = self.tmp / "ctx.md"
        rc, _, stderr = run_cli(src, out, context_path=ctx)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")
        self.assertIn("正確術語", content)
        self.assertNotIn("術語錯字", content)


class TestTextCleaningUnits(unittest.TestCase):
    """直接測內部函式(不經 CLI),鎖住去連字號斷字與去頁碼雜訊的行為。"""

    def test_dehyphenate_merge_english(self):
        text = "This is an under-\nstanding of hyphen break.\n"
        merged = extract.dehyphenate_and_merge(text)
        self.assertIn("understanding", merged)
        self.assertNotIn("under-\nstand", merged)

    def test_clean_text_block_strips_bare_page_number(self):
        text = "正文第一行內容在此。\n12\n正文第二行內容在此。\n"
        cleaned = extract.clean_text_block(text)
        lines = [l for l in cleaned.splitlines()]
        self.assertNotIn("12", lines)

    def test_clean_text_block_strips_page_label(self):
        text = "正文內容。\n第 3 頁\nPage 4 of 10\n- 5 -\n下一段內容。\n"
        cleaned = extract.clean_text_block(text)
        for noise in ("第 3 頁", "Page 4 of 10", "- 5 -"):
            self.assertNotIn(noise, cleaned)

    def test_detect_lang_zh_and_en(self):
        self.assertEqual(extract.detect_lang("這是一段中文內容，字數足夠判斷語言。"), "zh")
        self.assertEqual(extract.detect_lang("This is an English paragraph for language detection."), "en")


@unittest.skipUnless(HAVE_FITZ, "PyMuPDF(fitz) 未安裝,需用 .venv-doc 執行")
class TestPdfExtraction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

    def _build_pdf(self, path: Path):
        doc = fitz.open()
        p1 = doc.new_page(width=400, height=600)
        p1.insert_text((50, 60), "第一章 導論", fontname="china-ts", fontsize=16)
        p1.insert_text((50, 100), "這是第一頁的內文段落,說明本章要討論的主題與範圍。", fontname="china-ts", fontsize=11)
        p1.insert_text((50, 560), "1", fontsize=9)  # 頁碼雜訊

        p2 = doc.new_page(width=400, height=600)
        p2.insert_text((50, 60), "第二章 方法", fontname="china-ts", fontsize=16)
        p2.insert_text((50, 100), "這是第二頁的內文段落,延續說明具體的操作方法與步驟。", fontname="china-ts", fontsize=11)
        p2.insert_text((50, 560), "2", fontsize=9)  # 頁碼雜訊

        p3 = doc.new_page(width=400, height=600)
        p3.insert_text((50, 60), "第三章 結論", fontname="china-ts", fontsize=16)
        p3.insert_text((50, 100), "這是第三頁的內文段落,總結前述內容並提出結論。", fontname="china-ts", fontsize=11)
        p3.insert_text((50, 560), "3", fontsize=9)  # 頁碼雜訊

        doc.save(str(path))
        doc.close()

    def test_pdf_heuristic_sections_and_page_noise_removed(self):
        src = self.tmp / "book.pdf"
        self._build_pdf(src)
        out = self.tmp / "book.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)

        content = out.read_text(encoding="utf-8")
        self.assertIn("## 第一章 導論", content)
        self.assertIn("## 第二章 方法", content)
        self.assertIn("## 第三章 結論", content)
        self.assertIn("這是第一頁的內文段落", content)
        self.assertIn("這是第三頁的內文段落", content)

        # 頁碼雜訊(單獨一行的 1/2/3)不該出現在清理後的內容
        lines = content.splitlines()
        self.assertNotIn("1", lines)
        self.assertNotIn("2", lines)
        self.assertNotIn("3", lines)

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["input_type"], "pdf")
        self.assertGreaterEqual(stats["sections"], 3)
        self.assertEqual(stats["vertical_pages"], 0)

    def test_pdf_toc_headings_take_priority(self):
        src = self.tmp / "book_toc.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=400, height=600)
        p1.insert_text((50, 100), "第一部分的內文，沒有走啟發式標題偵測。", fontname="china-ts", fontsize=11)
        p2 = doc.new_page(width=400, height=600)
        p2.insert_text((50, 100), "第二部分的內文，同樣沒有啟發式標題字樣。", fontname="china-ts", fontsize=11)
        doc.set_toc([[1, "書籤：緒論", 1], [1, "書籤：總結", 2]])
        doc.save(str(src))
        doc.close()

        out = self.tmp / "book_toc.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")
        self.assertIn("## 書籤：緒論", content)
        self.assertIn("## 書籤：總結", content)
        self.assertIn("第一部分的內文", content)
        self.assertIn("第二部分的內文", content)

    def test_pdf_vertical_text_reading_order(self):
        """
        直排(vertical)模擬:用 insert_textbox(rotate=270) 讓 PyMuPDF 產生真正的
        垂直書寫方向文字行(line['dir'] 的 y 分量 dominant)。傳統直排中文欄序
        「右欄先於左欄」——這裡斷言重排後的輸出順序符合此規則,而非 PyMuPDF
        預設 get_text('text') 依插入順序輸出的錯亂順序。
        """
        src = self.tmp / "vertical.pdf"
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        rect = fitz.Rect(20, 20, 280, 80)
        # 千字文開頭「天地玄黄宇宙洪荒」,rotate=270 讓 PyMuPDF 按直排欄序
        # (右→左)自動換欄,line dir 會是 (0, ±1) 而非橫排的 (±1, 0)。
        page.insert_textbox(rect, "天地玄黄宇宙洪荒", fontsize=18, fontname="china-s", rotate=270)
        doc.save(str(src))
        doc.close()

        out = self.tmp / "vertical.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")
        joined = content.replace("\n", "")
        # 右欄「天地玄」必須先於左欄「洪荒」出現,不可被預設橫排順序打亂
        self.assertLess(joined.index("天地玄"), joined.index("洪荒"))

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["vertical_pages"], 1)

    def test_pdf_dehyphenation_removes_line_break_hyphen(self):
        src = self.tmp / "hyphen.pdf"
        doc = fitz.open()
        page = doc.new_page(width=400, height=200)
        # 手動模擬 PDF 常見的斷字換行:一行以 "under-" 結尾,下一行 "standing" 接續
        page.insert_text((50, 60), "This paragraph has an under-", fontsize=12)
        page.insert_text((50, 90), "standing of hyphenation cleanup.", fontsize=12)
        doc.save(str(src))
        doc.close()

        out = self.tmp / "hyphen.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")
        self.assertIn("understanding", content)


@unittest.skipUnless(HAVE_EBOOKLIB, "ebooklib 未安裝,需用 .venv-doc 執行")
class TestEpubExtraction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)

    def _build_epub(self, path: Path):
        book = epub.EpubBook()
        book.set_identifier("test-id-001")
        book.set_title("测试电子书")
        book.set_language("zh")
        book.add_author("測試作者")

        c1 = epub.EpubHtml(title="第一章", file_name="chap1.xhtml", lang="zh")
        c1.content = (
            "<html><body><h1>第一章 软件设计</h1>"
            "<p>这是第一章的内容，讲软件工程的基本概念。</p></body></html>"
        )
        c2 = epub.EpubHtml(title="第二章", file_name="chap2.xhtml", lang="zh")
        c2.content = (
            "<html><body><h1>第二章 结论</h1>"
            "<p>这是第二章的内容，总结前述讨论。</p></body></html>"
        )
        book.add_item(c1)
        book.add_item(c2)
        book.toc = (epub.Link("chap1.xhtml", "第一章", "chap1"), epub.Link("chap2.xhtml", "第二章", "chap2"))
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", c1, c2]
        epub.write_epub(str(path), book)

    def test_epub_two_chapters_extracted_as_h2(self):
        src = self.tmp / "book.epub"
        self._build_epub(src)
        out = self.tmp / "book.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)

        content = out.read_text(encoding="utf-8")
        self.assertIn("## 第一章 軟體設計", content)
        self.assertIn("## 第二章 結論", content)
        self.assertIn("這是第一章的內容", content)
        self.assertIn("這是第二章的內容", content)
        # nav.xhtml(目錄頁)不該被當成第三章內容抽出
        self.assertNotIn("## 第三章", content)

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["input_type"], "epub")
        self.assertEqual(stats["sections"], 2)
        self.assertEqual(stats["lang"], "zh")


if __name__ == "__main__":
    unittest.main()
