"""test_session_doc_line.py — scripts/session.py 文件輸入線(PDF/EPUB/TXT)
end-to-end smoke 測試(#607)。

鎖住:`session.py new <file>` 依副檔名自動偵測分流 —— .pdf/.txt 走
scripts/doc/extract.py 確定性抽取線,直接產出 cleaned.md,**跳過**
transcribe/phase-a/phase-b(ASR 專屬清理,不產 transcript.srt/cleaned.srt);
metadata.json 標 source_type=doc;--vlm(僅 pdf)先跑 scripts/doc/figures.py
渲染圖表進 images/,再複用既有 --images 的 marker 呼叫路徑寫
.images_pending.json / .image_insert_pending.json(不重造 describe/insert
邏輯,不需要真的呼叫 Antigravity/VLM)。

音檔線本身零改動,不在本檔測試範圍 —— 零迴歸靠 run_all.sh 既有測試組 +
本檔證明只有 is_doc 分支被觸碰(session.py 原音檔程式碼逐行原樣搬進
`if not is_doc:` 區塊)。

跑法(需要 fitz/PIL,主環境沒有,走 .venv-doc):
    /Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-doc/bin/python \
        -m pytest scripts/tests/test_session_doc_line.py -v

注意:若在 git worktree 下跑,PROJECT_ROOT(= session.py 所在 checkout 根目錄)
底下要有 .venv-doc(figures.py/extract.py 用的重依賴 venv)—— 主樹已有,
worktree 若缺可 `ln -s ../../.venv-doc .venv-doc`(gitignored,不影響版控)。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_PY = REPO_ROOT / "scripts" / "session.py"
SESSIONS_DIR = REPO_ROOT / "sessions"


def _cleanup(slug: str) -> None:
    sdir = SESSIONS_DIR / slug
    if sdir.exists():
        shutil.rmtree(sdir)


def _slug_for(stem: str) -> str:
    import datetime as dt
    return f"{dt.date.today().isoformat()}_{stem}"


def run_session_new(input_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SESSION_PY), "new", str(input_path), *extra_args]
    return subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)


def make_text_pdf(path: Path, title: str, body: str) -> None:
    """用 fitz 現場產生一份含繁體中文的迷你 PDF(china-ts 內建字型,避免亂碼)。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), title, fontname="china-ts", fontsize=18)
    page.insert_text((72, 120), body, fontname="china-ts", fontsize=11)
    doc.save(str(path))
    doc.close()


def make_pdf_with_image(path: Path, title: str, body: str) -> None:
    """跟 make_text_pdf 一樣,額外內嵌一張 300x300 圖(供 --vlm/figures.py 測試撿)。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), title, fontname="china-ts", fontsize=18)
    page.insert_text((72, 120), body, fontname="china-ts", fontsize=11)

    import io
    img = Image.new("RGB", (300, 300), color=(80, 130, 190))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(72, 300, 372, 600), stream=buf.getvalue())

    doc.save(str(path))
    doc.close()


# ── 1. PDF 文件線:cleaned.md 產出、ASR 三段被跳過、metadata source_type=doc ──


def test_pdf_input_produces_cleaned_md_and_skips_asr_stages(tmp_path: Path):
    stem = "docline_smoke_pdf"
    pdf_path = tmp_path / f"{stem}.pdf"
    make_text_pdf(
        pdf_path,
        title="測試標題確認抽取",
        body="這是一段測試內文，用來確認文件抽取線正常運作，"
             "字數需要夠長才不會被誤判成掃描頁雜訊內容。" * 2,
    )

    slug = _slug_for(stem)
    _cleanup(slug)
    try:
        proc = run_session_new(pdf_path, "--stop-at", "phase-b", "--engine", "none")
        assert proc.returncode == 0, proc.stderr

        sdir = SESSIONS_DIR / slug
        assert sdir.exists(), f"session dir not created: {sdir}\nstderr={proc.stderr}"

        cleaned_md = sdir / "cleaned.md"
        assert cleaned_md.exists(), "cleaned.md not produced by doc line"
        content = cleaned_md.read_text(encoding="utf-8")
        assert "測試標題確認抽取" in content
        assert "確認文件抽取線正常運作" in content or "確認檔案抽取線正常運作" in content

        # ASR 三段(transcribe/phase-a/phase-b)必須被跳過:不產生 ASR 專屬產物
        assert not (sdir / "transcript.srt").exists(), \
            "transcribe stage ran for a doc input — should be skipped"
        assert not (sdir / "cleaned.srt").exists(), \
            "phase-a stage ran for a doc input — should be skipped"

        meta = json.loads((sdir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["source_type"] == "doc"
        assert meta["doc_extraction"]["input_type"] == "pdf"
        assert meta["doc_extraction"]["chars"] > 0
        assert meta["qaqc"]["phase_b"]["status"] == "skipped_doc_line"
        assert meta["artifacts"]["transcript_srt"] is None
        assert meta["artifacts"]["cleaned_srt"] is None
        assert meta["artifacts"]["cleaned_md"] == "cleaned.md"
    finally:
        _cleanup(slug)


# ── 2. TXT 文件線:同一套契約,證明分流不是只認 pdf ──


def test_txt_input_produces_cleaned_md_and_skips_asr_stages(tmp_path: Path):
    stem = "docline_smoke_txt"
    txt_path = tmp_path / f"{stem}.txt"
    txt_path.write_text(
        "測試標題TXT\n\n這是一份測試 txt 檔案，用來驗證 session.py 的文件"
        "分流是否正確運作，並確保這條線同樣直接產出 cleaned.md。",
        encoding="utf-8",
    )

    slug = _slug_for(stem)
    _cleanup(slug)
    try:
        proc = run_session_new(txt_path, "--stop-at", "phase-b", "--engine", "none")
        assert proc.returncode == 0, proc.stderr

        sdir = SESSIONS_DIR / slug
        cleaned_md = sdir / "cleaned.md"
        assert cleaned_md.exists()
        content = cleaned_md.read_text(encoding="utf-8")
        assert "測試標題TXT" in content

        assert not (sdir / "transcript.srt").exists()
        assert not (sdir / "cleaned.srt").exists()

        meta = json.loads((sdir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["source_type"] == "doc"
        assert meta["doc_extraction"]["input_type"] == "txt"
    finally:
        _cleanup(slug)


# ── 3. --vlm 接線:figures.py 填 images/ + 複用既有 describe/insert marker 路徑 ──


def test_vlm_flag_renders_figures_and_writes_image_markers(tmp_path: Path):
    """跑到底(--stop-at phase-b,非 images)時兩個 marker 都該寫。
    對照組見 test_vlm_stop_at_images_gate_only_writes_describe_marker:
    --stop-at images 時只該寫 describe marker,不寫 insert marker。"""
    stem = "docline_smoke_vlm"
    pdf_path = tmp_path / f"{stem}.pdf"
    make_pdf_with_image(
        pdf_path,
        title="測試標題含圖",
        body="這是一段測試內文，用來確認 --vlm 分支能正確渲染圖片並寫 marker。" * 2,
    )

    slug = _slug_for(stem)
    _cleanup(slug)
    try:
        # engine=claude 才會走 marker 鏈(跟既有 --images 一致的判斷),
        # describe_images.py 本身(需要 Antigravity/VLM)完全不在本測試呼叫範圍內。
        proc = run_session_new(
            pdf_path, "--vlm", "--stop-at", "phase-b", "--engine", "claude")
        assert proc.returncode == 0, proc.stderr

        sdir = SESSIONS_DIR / slug
        images_dir = sdir / "images"
        assert images_dir.is_dir(), "images/ not created by --vlm branch"
        rendered = list(images_dir.glob("*.png"))
        assert rendered, "figures.py produced no images under images/"

        assert (sdir / "doc_figures.json").exists()

        images_marker = sdir / ".images_pending.json"
        assert images_marker.exists(), "images marker not written — describe step not reached"
        m1 = json.loads(images_marker.read_text(encoding="utf-8"))
        assert m1["stage"] == "images"
        assert m1["tool"] == "scripts/describe_images.py"
        assert m1["engine"] == "claude"

        insert_marker = sdir / ".image_insert_pending.json"
        assert insert_marker.exists(), "image-insert marker not written — insert step not reached"
        m2 = json.loads(insert_marker.read_text(encoding="utf-8"))
        assert m2["stage"] == "image-insert"
        assert m2["tool"] == "scripts/insert_images.py"

        meta = json.loads((sdir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["qaqc"]["images"]["status"] == "pending_agent_handoff"
        assert meta["qaqc"]["image_insert"]["status"] == "pending_agent_handoff"
    finally:
        _cleanup(slug)


# ── 3b. --vlm + --stop-at images 守門:只寫 describe marker,不寫 insert marker ──
# (回歸鎖:對抗性驗收 Major — 原本 --vlm 分支漏了跟音檔線 --images 分支一致的
# `if args.stop_at != "images":` 守門,導致 --stop-at images 卻仍寫出
# .image_insert_pending.json,跟輸出宣稱的 stopped-at 矛盾)


def test_vlm_stop_at_images_gate_only_writes_describe_marker(tmp_path: Path):
    stem = "docline_smoke_vlm_gate"
    pdf_path = tmp_path / f"{stem}.pdf"
    make_pdf_with_image(
        pdf_path,
        title="測試標題含圖守門",
        body="這是一段測試內文，用來確認 --stop-at images 對 --vlm 分支同樣生效。" * 2,
    )

    slug = _slug_for(stem)
    _cleanup(slug)
    try:
        proc = run_session_new(
            pdf_path, "--vlm", "--stop-at", "images", "--engine", "claude")
        assert proc.returncode == 0, proc.stderr
        assert "stopped at: images" in proc.stdout

        sdir = SESSIONS_DIR / slug
        assert (sdir / "images").is_dir()
        assert list((sdir / "images").glob("*.png")), \
            "figures.py 仍應渲染圖(--stop-at images 只擋 insert marker,不擋 figures)"

        assert (sdir / ".images_pending.json").exists(), \
            "describe marker 應該寫(images 是使用者要求的終點)"
        assert not (sdir / ".image_insert_pending.json").exists(), \
            "--stop-at images 時不該寫 insert marker(gate 沒生效)"

        meta = json.loads((sdir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["stop_at"] == "images"
        assert meta["qaqc"]["images"]["status"] == "pending_agent_handoff"
        assert meta["qaqc"]["image_insert"] is None, \
            "image_insert 應維持初始值 None(--stop-at images 未觸發該 stage)"
    finally:
        _cleanup(slug)


# ── 4. --vlm 對非 pdf 無意義:txt + --vlm 不該跑 figures.py / 不產 images/ ──


def test_vlm_flag_ignored_for_non_pdf(tmp_path: Path):
    stem = "docline_smoke_vlm_txt"
    txt_path = tmp_path / f"{stem}.txt"
    txt_path.write_text("純文字輸入，--vlm 對它應該無效。", encoding="utf-8")

    slug = _slug_for(stem)
    _cleanup(slug)
    try:
        proc = run_session_new(
            txt_path, "--vlm", "--stop-at", "phase-b", "--engine", "none")
        assert proc.returncode == 0, proc.stderr
        assert "--vlm 只對 .pdf 有意義" in proc.stderr

        sdir = SESSIONS_DIR / slug
        assert not (sdir / "images").exists()
        assert not (sdir / "doc_figures.json").exists()
        assert not (sdir / ".images_pending.json").exists()
    finally:
        _cleanup(slug)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
