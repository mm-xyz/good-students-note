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
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
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


def run_cli(input_path: Path, output_path: Path, corrections_path: Path | None = None) -> tuple[int, str, str]:
    """透過 subprocess 跑實際 CLI(驗介面契約:argv/stdout stats JSON/exit code)。"""
    cmd = [sys.executable, str(DOC_DIR / "extract.py"), str(input_path), "-o", str(output_path)]
    if corrections_path:
        cmd += ["--corrections", str(corrections_path)]
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

    def test_corrections_flag_applied(self):
        """
        2026-07-31 對抗性驗收 Major-3:find=replace 補充修正表改名 --corrections,
        把 --context 這個旗標名讓回音檔線既有語意(ASR initial_prompt 自由文本),
        避免兩條輸入線對同一個 CLI 旗標名有衝突語意。行為不變,只驗證新旗標名可用。
        """
        src = self.tmp / "ctx.txt"
        src.write_text("这里有一个術語錯字需要修正。\n", encoding="utf-8")
        corrections = self.tmp / "corrections.txt"
        corrections.write_text("術語錯字=正確術語\n", encoding="utf-8")
        out = self.tmp / "ctx.md"
        rc, _, stderr = run_cli(src, out, corrections_path=corrections)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")
        self.assertIn("正確術語", content)
        self.assertNotIn("術語錯字", content)

    def test_numbered_list_not_treated_as_headings(self):
        """
        2026-07-31 對抗性驗收 Critical-1:好學生筆記/講義常見的編號條列步驟
        (「1. 先讀目錄抓框架」)之前會被 numbered/numbered_sub pattern
        誤判成 ## 章節標題,把單一小節的筆記炸成 4 個假章節。
        這兩條 pattern 已移除——條列項要維持條列文字,只有真正的「第X章」
        字樣才算章節,sections 應該是 1 而不是 4。
        """
        src = self.tmp / "notes.txt"
        src.write_text(
            "第一章 讀書方法\n"
            "\n"
            "以下是三個步驟:\n"
            "1. 先讀目錄抓框架\n"
            "2. 用便利貼標記重點頁\n"
            "3. 讀完後寫一段摘要\n",
            encoding="utf-8",
        )
        out = self.tmp / "notes.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)

        content = out.read_text(encoding="utf-8")
        self.assertIn("## 第一章 讀書方法", content)
        for item in ("1. 先讀目錄抓框架", "2. 用便利貼標記重點頁", "3. 讀完後寫一段摘要"):
            self.assertIn(item, content)
            self.assertNotIn(f"## {item}", content)
            self.assertNotIn(f"### {item}", content)

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["sections"], 1)


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

    def test_pdf_vertical_text_reassembles_per_character_lines(self):
        """
        真書重現(#615):insert_textbox(rotate=270) 那種 fixture 會被 PyMuPDF
        自動把整欄合併成一個 line,測不到「一字一行」這個真書才會現形的
        bug。真實排版/掃描 PDF 常把直排的每個字各自拆成獨立 line(bbox 極
        窄、x 中心幾乎相同、dir 仍是垂直的 (0, ±1))——這裡用 fitz.TextWriter
        搭配 90 度旋轉 morph 逐字元各自 write_text,重現這種「每字一個
        line」的真實情境,斷言修 bug 後同一直欄的字元會接成連續字串,不再
        被逐字拆成一字一行。
        """
        src = self.tmp / "vertical_percall.pdf"
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)

        def write_vertical_char(x, y, ch, fontsize=18):
            tw = fitz.TextWriter(page.rect)
            tw.append((x, y), ch, fontsize=fontsize, font=fitz.Font("china-s"))
            tw.write_text(page, morph=(fitz.Point(x, y), fitz.Matrix(90)))

        # 右欄「天地玄黄」、左欄「宇宙洪荒」,每個字各自呼叫一次
        # write_vertical_char,y 遞增模擬直排由上到下、右欄 x 大於左欄 x
        # 模擬欄序右到左——完全比照真書 PDF 逐字各自一個 line 的結構。
        y = 30
        for ch in "天地玄黄":
            write_vertical_char(260, y, ch)
            y += 20
        y = 30
        for ch in "宇宙洪荒":
            write_vertical_char(230, y, ch)
            y += 20

        doc.save(str(src))
        doc.close()

        out = self.tmp / "vertical_percall.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")

        # 核心斷言:同一直欄的字元接成連續字串。修 bug 前這裡會變成
        # 「天\n地\n玄\n黄」一字一行,以下兩個 assertIn 都會失敗。
        # (簡→繁轉換會把「黄」轉成「黃」,故用繁體字斷言。)
        self.assertIn("天地玄黃", content)
        self.assertIn("宇宙洪荒", content)

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["vertical_pages"], 1)

    def test_pdf_vertical_duplicate_ocr_pass_not_interleaved(self):
        """
        對抗性驗收 FAIL 重現(#615 二修 + reviewer 測試品質糾正):真書
        《紫微攻略2》的隱藏 OCR 文字層會把同一段直排文字用兩個
        near-duplicate pass 各寫一次,兩份 pass 每個字元的 bbox x 中心
        各自帶獨立小抖動(實測落在 0.3~0.5px,不是整份 pass 固定同一個
        偏移量)。若把兩份 pass 併進同一欄後單純按全域 x 中心排序,兩份
        pass 的字元會依各自抖動值交錯穿插,比一字一行更難讀(reviewer
        實測 page 9 拿到「…打轉富固定什麼星，，宮是什麼星，但…」這種
        字元級亂碼)。

        踩坑記錄:第一版 fixture 讓兩份 pass 全程固定同一個 x 偏移
        (x_a=200.0、x_b=200.5,每個字元都一樣),結果排序主鍵是
        x_center,固定偏移只會把兩份 pass 乾淨分成兩個連續區塊,根本不
        會觸發交錯——把 extract.py 換回 round-1(b249e24,還沒有本輪
        修法)重跑舊 fixture 竟然也 PASS,不是真紅綠測試。真書的抖動是
        「每個字元獨立」,全域排序才會把兩份 pass 交錯穿插;這裡改成兩份
        pass 各自帶不同、逐字元變化的 x 抖動(在 fitz.TextWriter 精確
        指定座標,不用真隨機以保證測試可重現),已用 round-1 版
        extract.py 實測確認 FAIL(輸出「該往應老公如何但往如何但往老公
        應該往」之類的交錯亂碼),對現在的實作 PASS。

        斷言:
        1. 該欄的字元(不論是哪一份 pass)接成的子字串,是原文「老公應該
           如何但往往」的完整連續片段,而不是逐字交錯的亂碼;
        2. 全文任何 6 字元滑動視窗內,同一字元出現次數 < 4(交錯亂碼會讓
           同一批字元在小視窗內反覆出現;乾淨文字——即使重複整段——不會)。
        """
        src = self.tmp / "vertical_dup_pass.pdf"
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)

        def write_char(x, y, ch, fontsize=14):
            tw = fitz.TextWriter(page.rect)
            tw.append((x, y), ch, fontsize=fontsize, font=fitz.Font("china-s"))
            tw.write_text(page, morph=(fitz.Point(x, y), fitz.Matrix(90)))

        clause = "老公應該如何但往往"
        y = 40
        base_x = 200.0
        # 兩份 pass 各自逐字元變化的 x 抖動(px 級,落在真書實測的
        # 0.3~0.5px 範圍),刻意讓兩份 pass 在某些字元上 A 較右、某些
        # 字元上 B 較右,而不是整份固定同一個偏移——這樣全域 x 排序才會
        # 真正把兩份 pass 交錯穿插,而非乾淨分成兩塊。
        jitter_a = [0.30, 0.10, 0.35, 0.05, 0.30, 0.10, 0.30, 0.05, 0.30]
        jitter_b = [0.10, 0.30, 0.05, 0.35, 0.10, 0.30, 0.10, 0.35, 0.10]
        for i, ch in enumerate(clause):
            write_char(base_x + jitter_a[i], y + i * 16, ch)
            write_char(base_x + jitter_b[i], y + i * 16 + 2.0, ch)  # 第二份 pass:y 也偏移約 2px

        doc.save(str(src))
        doc.close()

        out = self.tmp / "vertical_dup_pass.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)
        content = out.read_text(encoding="utf-8")

        # 核心斷言 1:原文的連續片段必須完整、原序出現(不論重複幾次),
        # 不能被拆散交錯。修 bug 前(track 用純 y 排序合併兩份 pass)這裡
        # 會產出「老老公公應應該該…」或更破碎的交錯亂碼,兩個 assertIn
        # 都會失敗。
        self.assertIn("老公應該如何但往往", content)

        # 核心斷言 2:交錯亂碼偵測——任何 6 字元滑動視窗內,同一字元最多
        # 出現 3 次;字元級交錯(如「說模模模的糊況」)會讓同一字元在小
        # 視窗內反覆出現 4 次以上,乾淨文字(即使整句重複兩次)不會。
        cjk_only = re.sub(r"[^一-鿿]", "", content)
        max_repeat_in_window = 0
        window = 6
        for i in range(max(0, len(cjk_only) - window + 1)):
            counts = Counter(cjk_only[i:i + window])
            max_repeat_in_window = max(max_repeat_in_window, max(counts.values()))
        self.assertLess(
            max_repeat_in_window, 4,
            msg=f"疑似字元級交錯亂碼,content={content!r}",
        )

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
        # toc 標題刻意跟內文 h1 一致(真書常見:toc 就是抄章名),同時鎖住
        # 「toc 對得到時優先用 toc」與「toc 標題=body 標題時輸出不變」兩件事。
        book.toc = (epub.Link("chap1.xhtml", "第一章 软件设计", "chap1"),
                    epub.Link("chap2.xhtml", "第二章 结论", "chap2"))
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

    def _build_epub_with_nonlinear_page(self, path: Path):
        """spine 含一頁 linear="no"(作者手動標的目錄頁,非 ebooklib 自動 EpubNav)。"""
        book = epub.EpubBook()
        book.set_identifier("test-id-002")
        book.set_title("测试电子书2")
        book.set_language("zh")

        toc_page = epub.EpubHtml(title="目錄", file_name="toc_page.xhtml", lang="zh")
        toc_page.content = "<html><body><h1>目錄</h1><p>第一章……第二章……</p></body></html>"
        c1 = epub.EpubHtml(title="第一章", file_name="chap1.xhtml", lang="zh")
        c1.content = "<html><body><h1>第一章 緒論</h1><p>這是正文內容一，講緒論。</p></body></html>"

        book.add_item(toc_page)
        book.add_item(c1)
        # toc 標題跟內文 h1 一致(理由同 _build_epub 的註解)。
        book.toc = (epub.Link("chap1.xhtml", "第一章 緒論", "chap1"),)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        # (item, "no") — EPUB 規範的 linear="no",標「非閱讀順序輔助頁」
        book.spine = ["nav", (toc_page, "no"), c1]
        epub.write_epub(str(path), book)

    def test_epub_linear_no_page_excluded(self):
        """
        2026-07-31 對抗性驗收 Critical-2:spine 裡 linear="no" 的頁面
        (作者手動標的目錄/封面等輔助頁)之前只被 is_chapter() 擋掉
        ebooklib 自動產生的 EpubNav,linear="no" 的真實 EpubHtml 頁沒被擋,
        「## 目錄」會被誤抽成一個章節,灌水 sections。
        """
        src = self.tmp / "book_nonlinear.epub"
        self._build_epub_with_nonlinear_page(src)
        out = self.tmp / "book_nonlinear.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)

        content = out.read_text(encoding="utf-8")
        self.assertNotIn("## 目錄", content)
        self.assertIn("## 第一章 緒論", content)
        self.assertIn("這是正文內容一", content)

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["sections"], 1)

    def _build_epub_with_toc_titles_no_headings(self, path: Path):
        """
        兩節內文都是純段落(完全沒有 h1/h2/h3),但 book.toc 有描述性真章名——
        對齊真書《跑者都該懂的跑步關鍵數據》的實際結構(章名躲在 toc/nav,
        不在內文 heading tag)。外層 Link 是 depth=1(該對到 ##),巢狀
        Section 底下的子 Link 是 depth=2(該對到 ###)。
        """
        book = epub.EpubBook()
        book.set_identifier("test-id-toc-titles")
        book.set_title("测试书toc")
        book.set_language("zh")

        c1 = epub.EpubHtml(title="c1", file_name="chap1.xhtml", lang="zh")
        c1.content = "<html><body><p>這裡完全沒有標題標籤，只有純段落內容，講第一節的東西。</p></body></html>"
        c2 = epub.EpubHtml(title="c2", file_name="chap2.xhtml", lang="zh")
        c2.content = "<html><body><p>第二節同樣沒有 h1/h2/h3，只有純段落內容。</p></body></html>"
        c3 = epub.EpubHtml(title="c3", file_name="chap3.xhtml", lang="zh")
        c3.content = "<html><body><p>子節內容也沒有標題標籤，講子節的細節。</p></body></html>"

        book.add_item(c1)
        book.add_item(c2)
        book.add_item(c3)
        book.toc = (
            epub.Link("chap1.xhtml", "【導讀一】真實描述性章名", "chap1"),
            (epub.Section("真實描述性章名二（有子節）", href="chap2.xhtml"),
             (epub.Link("chap3.xhtml", "子節：真實描述性子章名", "chap3"),)),
        )
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", c1, c2, c3]
        epub.write_epub(str(path), book)

    def test_epub_uses_toc_title_when_no_body_headings(self):
        """
        2026-07-31 卡 #614:真書實測揭露的缺口——EPUB 章名偵測只抓每個
        spine 文件的第一個 h1/h2/h3,章名不在那些 tag 時全部 fallback 成
        空殼「第N章」。修法:章名優先用 toc/nav 標題,對不到才退回 h1-h3、
        再退回「第N章」。這裡斷言 toc 標題被用上,不是 fallback 編號。
        """
        src = self.tmp / "book_toc_titles.epub"
        self._build_epub_with_toc_titles_no_headings(src)
        out = self.tmp / "book_toc_titles.md"
        rc, stdout, stderr = run_cli(src, out)
        self.assertEqual(rc, 0, msg=stderr)

        content = out.read_text(encoding="utf-8")
        self.assertIn("## 【導讀一】真實描述性章名", content)
        self.assertIn("## 真實描述性章名二（有子節）", content)
        self.assertIn("### 子節：真實描述性子章名", content)
        # 不該退回空殼「第N章」fallback(toc 對得到就不該用它)
        self.assertNotIn("## 第1章", content)
        self.assertNotIn("## 第2章", content)
        self.assertNotIn("## 第3章", content)

        stats = json.loads(stdout.strip().splitlines()[-1])
        self.assertEqual(stats["sections"], 3)


if __name__ == "__main__":
    unittest.main()
