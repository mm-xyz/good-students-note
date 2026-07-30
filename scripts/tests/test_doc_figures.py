#!/usr/bin/env python3
"""test_doc_figures.py — scripts/doc/figures.py（PDF 圖表確定性渲染器）的行為鎖定測試。

鎖住:嵌入點陣圖抽取＋min-size 篩選、向量圖表頁偵測整頁渲染、輸出檔名/manifest
schema、續跑不重複產出。Fixture 一律程式生成（fitz + PIL），不外連、不依賴真實 PDF。

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

# 向量圖表頁偵測門檻（與 figures.py 內部常數對齊，見該檔 VECTOR_DRAW_THRESHOLD）。
# fixture 刻意畫超過此值的向量物件，確保觸發整頁渲染。
VECTOR_SHAPES = 30

# 讓「平均每頁可抽取文字」明顯高於 figures.py 的 SCANNED_CHARS_PER_PAGE 門檻，
# 避免 fixture 被誤判成掃描 PDF（那樣會整頁渲染，蓋掉本測試要驗的 embedded/vector-page 分流）。
FILLER_TEXT = "這是一段用來確保本頁可抽取文字量足夠、不被誤判為掃描頁的填充文字內容。" * 4


def make_fixture_pdf(tmp_path: Path) -> Path:
    """造一個兩頁的迷你 PDF：
    第 1 頁＝一張大內嵌點陣圖（應被抽取）＋一張小裝飾 icon（應被 min-size 濾掉）。
    第 2 頁＝純向量繪圖（矩形陣列，無內嵌點陣圖），應被判定為 vector-page 整頁渲染。
    """
    icon_png = tmp_path / "icon.png"
    big_png = tmp_path / "big.png"
    Image.new("RGB", (40, 40), color=(200, 30, 30)).save(icon_png)
    Image.new("RGB", (300, 300), color=(30, 120, 200)).save(big_png)

    doc = fitz.open()

    page1 = doc.new_page(width=595, height=842)
    page1.insert_image(fitz.Rect(50, 50, 350, 350), filename=str(big_png))
    page1.insert_image(fitz.Rect(400, 50, 440, 90), filename=str(icon_png))
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
    assert stats["pages_scanned"] == 2

    pngs = sorted(session_dir.glob("fig_p*.png"))
    assert [p.name for p in pngs] == ["fig_p001_01.png", "fig_p002_01.png"]

    manifest_path = session_dir / "doc_figures.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest) == 2

    p1 = next(m for m in manifest if m["file"] == "fig_p001_01.png")
    assert p1["page"] == 1
    assert p1["kind"] == "embedded"
    assert isinstance(p1["bbox"], list) and len(p1["bbox"]) == 4

    p2 = next(m for m in manifest if m["file"] == "fig_p002_01.png")
    assert p2["page"] == 2
    assert p2["kind"] == "vector-page"
    assert isinstance(p2["bbox"], list) and len(p2["bbox"]) == 4


def test_small_icon_filtered_by_min_size(tmp_path: Path, fixture_pdf: Path):
    session_dir = tmp_path / "session"
    run_figures(fixture_pdf, session_dir)
    manifest = json.loads((session_dir / "doc_figures.json").read_text(encoding="utf-8"))
    # 40x40 icon 遠小於預設 min-size(128),不應出現在任何一筆 manifest 項目裡
    assert all(m["kind"] != "embedded" or m["file"] != "fig_p001_02.png" for m in manifest)
    assert len(manifest) == 2  # 只有大圖 + 向量頁,icon 被濾掉


def test_min_size_override_can_admit_small_icon(tmp_path: Path, fixture_pdf: Path):
    session_dir = tmp_path / "session"
    proc = run_figures(fixture_pdf, session_dir, "--min-size", "20")
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((session_dir / "doc_figures.json").read_text(encoding="utf-8"))
    embedded_on_p1 = [m for m in manifest if m["page"] == 1 and m["kind"] == "embedded"]
    assert len(embedded_on_p1) == 2  # 大圖 + icon 都被抽出


def test_resume_skips_already_rendered_files(tmp_path: Path, fixture_pdf: Path):
    session_dir = tmp_path / "session"
    run_figures(fixture_pdf, session_dir)
    pngs = sorted(session_dir.glob("fig_p*.png"))
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in pngs}

    proc2 = run_figures(fixture_pdf, session_dir)
    assert proc2.returncode == 0, proc2.stderr
    stats2 = json.loads(proc2.stdout.strip().splitlines()[-1])
    assert stats2["figures"] == 2  # 續跑統計數不變,不重複累加

    pngs_after = sorted(session_dir.glob("fig_p*.png"))
    assert [p.name for p in pngs_after] == [p.name for p in pngs]  # 沒有多產出檔案
    mtimes_after = {p.name: p.stat().st_mtime_ns for p in pngs_after}
    assert mtimes_after == mtimes_before  # 既有 PNG 沒有被重新寫入

    manifest = json.loads((session_dir / "doc_figures.json").read_text(encoding="utf-8"))
    assert len(manifest) == 2  # manifest 沒有重複 append


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
