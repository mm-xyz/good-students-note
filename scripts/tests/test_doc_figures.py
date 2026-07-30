#!/usr/bin/env python3
"""test_doc_figures.py — scripts/doc/figures.py（PDF 圖表確定性渲染器）的行為鎖定測試。

鎖住:嵌入點陣圖抽取＋四維篩選(寬/高/面積/長寬比,沿襲 vlm_prep.py:73-76)、
向量圖表頁偵測整頁渲染、輸出落在 <session>/images/(對齊 describe_images.py
245-248「有 images/ 就只掃 images/」的掃描慣例)、manifest schema、續跑不重複
產出、total_pages/scanned_pages 統計語意。Fixture 一律程式生成（fitz + PIL），
不外連、不依賴真實 PDF。

跑法:
    /Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-doc/bin/python \
        -m pytest scripts/tests/test_doc_figures.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIGURES_PY = SCRIPTS_DIR / "doc" / "figures.py"

# describe_images.py:27 的 IMG_EXTS——用來在測試裡複製它「有 images/ 就只掃 images/」
# 的掃描邏輯(describe_images.py:245-248),證明本檔輸出真的撿得到,不是自己講自己爽。
IMG_EXTS_DESCRIBE = (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")

# 向量圖表頁偵測門檻（與 figures.py 內部常數對齊，見該檔 VECTOR_DRAW_THRESHOLD）。
# fixture 刻意畫超過此值的向量物件，確保觸發整頁渲染。
VECTOR_SHAPES = 30

# 讓「平均每頁可抽取文字」明顯高於 figures.py 的 SCANNED_CHARS_PER_PAGE 門檻，
# 避免 fixture 被誤判成掃描 PDF（那樣會整頁渲染，蓋掉本測試要驗的 embedded/vector-page 分流）。
FILLER_TEXT = "這是一段用來確保本頁可抽取文字量足夠、不被誤判為掃描頁的填充文字內容。" * 4


def make_fixture_pdf(tmp_path: Path) -> Path:
    """造一個兩頁的迷你 PDF：
    第 1 頁：
      - big.png    300x300  → 應被抽取為 embedded(寬高/面積/長寬比全合格)
      - icon.png    40x40   → 應被四維篩選濾掉(全部不合格,裝飾 icon)
      - banner.png 2000x130 → 應被濾掉(高度<預設200、長寬比15.4>8——長寬比恆定,
                              --min-size 覆寫也救不回來,對照 vlm_prep.py 的橫幅/分隔線案例)
      - narrow.png  150x550 → 預設被濾掉(寬150<預設250),但面積82500/長寬比3.67皆合格,
                              --min-size 調低寬高門檻後應被放行(驗證 override 語意)
    第 2 頁＝純向量繪圖（矩形陣列，無內嵌點陣圖），應被判定為 vector-page 整頁渲染。
    """
    icon_png = tmp_path / "icon.png"
    big_png = tmp_path / "big.png"
    banner_png = tmp_path / "banner.png"
    narrow_png = tmp_path / "narrow.png"
    Image.new("RGB", (40, 40), color=(200, 30, 30)).save(icon_png)
    Image.new("RGB", (300, 300), color=(30, 120, 200)).save(big_png)
    Image.new("RGB", (2000, 130), color=(90, 200, 90)).save(banner_png)
    Image.new("RGB", (150, 550), color=(200, 200, 30)).save(narrow_png)

    doc = fitz.open()

    page1 = doc.new_page(width=595, height=842)
    page1.insert_image(fitz.Rect(50, 50, 350, 350), filename=str(big_png))
    page1.insert_image(fitz.Rect(400, 50, 440, 90), filename=str(icon_png))
    page1.insert_image(fitz.Rect(60, 700, 500, 760), filename=str(banner_png))
    page1.insert_image(fitz.Rect(520, 380, 570, 620), filename=str(narrow_png))
    page1.insert_text((50, 400), FILLER_TEXT, fontsize=8)

    page2 = doc.new_page(width=595, height=842)
    for i in range(VECTOR_SHAPES):
        x = 50 + (i % 10) * 45
        y = 50 + (i // 10) * 45
        page2.draw_rect(fitz.Rect(x, y, x + 35, y + 35), color=(0, 0, 0), width=1)
    page2.insert_text((50, 500), FILLER_TEXT, fontsize=8)

    pdf_path = tmp_path / "fixture.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def run_figures(pdf_path: Path, session_dir: Path, *extra_args: str) -> subprocess.CompletedProcess:
    session_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, str(FIGURES_PY), str(pdf_path), "--session", str(session_dir), *extra_args],
        capture_output=True, text=True,
    )


@pytest.fixture()
def fixture_pdf(tmp_path: Path) -> Path:
    return make_fixture_pdf(tmp_path)


def test_extracts_embedded_and_vector_page(tmp_path: Path, fixture_pdf: Path):
    session_dir = tmp_path / "session"
    proc = run_figures(fixture_pdf, session_dir)
    assert proc.returncode == 0, proc.stderr

    stats = json.loads(proc.stdout.strip().splitlines()[-1])
    assert stats["figures"] == 2
    assert stats["total_pages"] == 2
    assert stats["scanned_pages"] == 0  # 文字型 PDF,沒有任何頁被整頁當「掃描頁」處理

    images_dir = session_dir / "images"
    pngs = sorted(images_dir.glob("fig_p*.png"))
    assert [p.name for p in pngs] == ["fig_p001_01.png", "fig_p002_01.png"]
    assert list(session_dir.glob("fig_p*.png")) == []  # session 根目錄不該有圖,一律落 images/

    manifest_path = session_dir / "doc_figures.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest) == 2

    p1 = next(m for m in manifest if m["file"] == "images/fig_p001_01.png")
    assert p1["page"] == 1
    assert p1["kind"] == "embedded"
    assert isinstance(p1["bbox"], list) and len(p1["bbox"]) == 4

    p2 = next(m for m in manifest if m["file"] == "images/fig_p002_01.png")
    assert p2["page"] == 2
    assert p2["kind"] == "vector-page"
    assert isinstance(p2["bbox"], list) and len(p2["bbox"]) == 4


def test_banner_and_icon_filtered_by_default_four_dim_filter(tmp_path: Path, fixture_pdf: Path):
    """對齊 vlm_prep.py:73-76 的四維篩選:寬/高/面積/長寬比全部合格才留。
    2000x130 橫幅較小邊(130)在舊版單維 min-size(128)下會被誤放行,四維篩選下
    因高度<200(預設)+長寬比15.4>8(恆定)被濾掉;40x40 icon 四維全不合格被濾掉;
    150x550 窄圖預設也因寬<250被濾掉。只有 300x300 大圖存活。"""
    session_dir = tmp_path / "session"
    run_figures(fixture_pdf, session_dir)
    manifest = json.loads((session_dir / "doc_figures.json").read_text(encoding="utf-8"))
    embedded_on_p1 = [m for m in manifest if m["page"] == 1 and m["kind"] == "embedded"]
    assert len(embedded_on_p1) == 1
    assert len(manifest) == 2  # 大圖(embedded) + 向量頁(page2),icon/banner/narrow 全被濾掉


def test_min_size_override_admits_narrow_but_not_wide_banner(tmp_path: Path, fixture_pdf: Path):
    """--min-size 覆寫『寬、高』下限(此例調到 100):
    - narrow.png(150x550) 寬150/高550 都 ≥100、面積82500≥80000、長寬比3.67≤8 → 放行
    - icon.png(40x40) 寬高仍 <100 → 依舊濾掉
    - banner.png(2000x130) 面積/寬高即使覆寫也過關,但長寬比15.4>8『恆定不受覆寫』→ 依舊濾掉
    證明 --min-size 只動寬高兩維,面積與長寬比是來源常數,不能被鬆綁。"""
    session_dir = tmp_path / "session"
    proc = run_figures(fixture_pdf, session_dir, "--min-size", "100")
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((session_dir / "doc_figures.json").read_text(encoding="utf-8"))
    embedded_on_p1 = [m for m in manifest if m["page"] == 1 and m["kind"] == "embedded"]
    assert len(embedded_on_p1) == 2  # big + narrow;icon 與 banner 依舊被濾掉
    assert len(manifest) == 3  # 2 個 embedded + 1 個 vector-page(page2)


def test_resume_skips_already_rendered_files(tmp_path: Path, fixture_pdf: Path):
    session_dir = tmp_path / "session"
    run_figures(fixture_pdf, session_dir)
    images_dir = session_dir / "images"
    pngs = sorted(images_dir.glob("fig_p*.png"))
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in pngs}

    proc2 = run_figures(fixture_pdf, session_dir)
    assert proc2.returncode == 0, proc2.stderr
    stats2 = json.loads(proc2.stdout.strip().splitlines()[-1])
    assert stats2["figures"] == 2  # 續跑統計數不變,不重複累加

    pngs_after = sorted(images_dir.glob("fig_p*.png"))
    assert [p.name for p in pngs_after] == [p.name for p in pngs]  # 沒有多產出檔案
    mtimes_after = {p.name: p.stat().st_mtime_ns for p in pngs_after}
    assert mtimes_after == mtimes_before  # 既有 PNG 沒有被重新寫入

    manifest = json.loads((session_dir / "doc_figures.json").read_text(encoding="utf-8"))
    assert len(manifest) == 2  # manifest 沒有重複 append


def test_coexists_with_existing_images_dir_and_describe_images_scan_picks_both(
    tmp_path: Path, fixture_pdf: Path
):
    """整合測試(對應 ⚡Major1):模擬 scripts/session.py:504 --images 慣例已經把照片
    複製進 <session>/images/,figures.py 跑完後兩者要能共存,且複製自
    describe_images.py:245-248 的「有 images/ 就只掃 images/」掃描邏輯要能同時
    撿到既有照片與新產出的 fig_p*.png(不是只認得其中一種、也不是被漏掃)。"""
    session_dir = tmp_path / "session"
    images_dir = session_dir / "images"
    images_dir.mkdir(parents=True)
    existing_photo = images_dir / "photo_001.jpg"
    Image.new("RGB", (640, 480), color=(10, 10, 10)).save(existing_photo)

    proc = run_figures(fixture_pdf, session_dir)
    assert proc.returncode == 0, proc.stderr

    # 逐字對照 describe_images.py:245-248 的掃描邏輯
    img_dir = session_dir / "images"
    scan_dir = img_dir if img_dir.is_dir() else session_dir
    scanned = sorted(
        p.name for p in scan_dir.iterdir()
        if p.is_file() and p.suffix in IMG_EXTS_DESCRIBE and p.name != "cover.jpg"
    )
    assert "photo_001.jpg" in scanned
    assert "fig_p001_01.png" in scanned
    assert "fig_p002_01.png" in scanned
    assert list(session_dir.glob("fig_p*.png")) == []  # 沒有落在根目錄的漏網之魚


def test_rejects_non_pdf_input(tmp_path: Path):
    txt_path = tmp_path / "not_a_pdf.txt"
    txt_path.write_text("hello", encoding="utf-8")
    session_dir = tmp_path / "session"
    proc = run_figures(txt_path, session_dir)
    assert proc.returncode != 0
    assert proc.stderr.strip() != ""


def test_rejects_missing_input(tmp_path: Path):
    session_dir = tmp_path / "session"
    proc = run_figures(tmp_path / "does_not_exist.pdf", session_dir)
    assert proc.returncode != 0
