#!/usr/bin/env python3
"""test_finalize_copy.py — 定稿文案流程(重 render → 重轉錄 → prompt → 引擎)。

鎖住的是**流程判斷**,不是引擎輸出:成品挑最新版本號、已是最新就不重複
render/轉錄、引擎失敗要各自隔離不互相拖垮。

跑法:
    python3 scripts/tests/test_finalize_copy.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from finalize_copy import (latest_final_mp3, step_engine,  # noqa: E402
                           step_render, step_transcribe)


def touch(p: Path, mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class TestLatestFinalMp3(unittest.TestCase):
    def test_picks_the_highest_version_number(self):
        with tempfile.TemporaryDirectory() as td:
            s = Path(td)
            touch(s / "v07_20260811-0010_初剪" / "EP16_v7.mp3")
            touch(s / "v11_20260812-0005_定稿" / "EP16_v11.mp3")
            touch(s / "v09_20260811-對照組" / "B_分軌線.mp3")
            self.assertEqual(latest_final_mp3(s).name, "EP16_v11.mp3")

    def test_ignores_non_version_dirs_and_empty_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            s = Path(td)
            touch(s / "_meta" / "cover.mp3")
            touch(s / "raw" / "補錄.mp3")
            self.assertIsNone(latest_final_mp3(s))


class TestSkipsWork(unittest.TestCase):
    """已經最新就不要重跑 —— 重複 render 會多出一個沒有意義的版本目錄,
    重複轉錄則是白燒 90 秒。"""

    def test_render_skipped_when_mp3_newer_than_cutplan(self):
        with tempfile.TemporaryDirectory() as td:
            s = Path(td)
            now = time.time()
            touch(s / "cutplan.md", now - 100)
            mp3 = touch(s / "v1_20260812-0005" / "EP.mp3", now)
            self.assertEqual(step_render(s, "cutplan.md", mp3), mp3)

    def test_transcribe_skipped_when_srt_newer_than_mp3(self):
        with tempfile.TemporaryDirectory() as td:
            s = Path(td)
            now = time.time()
            mp3 = touch(s / "v1_20260812-0005" / "EP.mp3", now - 100)
            srt = touch(s / "_meta" / "final" / "transcript.srt", now)
            self.assertEqual(step_transcribe(s, mp3, force=False), srt)


class TestEngineIsolation(unittest.TestCase):
    def test_missing_engine_binary_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as td:
            import finalize_copy
            finalize_copy.ENGINES["_nope"] = ["definitely-not-a-real-binary"]
            try:
                ok, msg = step_engine("_nope", "prompt", Path(td), 5)
            finally:
                finalize_copy.ENGINES.pop("_nope")
        self.assertFalse(ok)
        self.assertIn("找不到指令", msg)

    def test_short_output_is_rejected_not_written(self):
        """引擎回一句「好的」不算文案 —— 寧可報失敗,也不要寫一個空殼草稿
        讓人以為跑完了。"""
        with tempfile.TemporaryDirectory() as td:
            import finalize_copy
            finalize_copy.ENGINES["_echo"] = [sys.executable, "-c",
                                              "print('好的')"]
            try:
                ok, msg = step_engine("_echo", "prompt", Path(td), 10)
            finally:
                finalize_copy.ENGINES.pop("_echo")
        self.assertFalse(ok)
        self.assertIn("不像文案", msg)
        self.assertFalse((Path(td) / "_meta" / "copy_draft__echo.md").exists())

    def test_good_output_is_written_with_engine_header(self):
        with tempfile.TemporaryDirectory() as td:
            import finalize_copy
            finalize_copy.ENGINES["_ok"] = [
                sys.executable, "-c", "print('標題參考:' + '文案內容' * 100)"]
            try:
                ok, msg = step_engine("_ok", "prompt", Path(td), 10)
            finally:
                finalize_copy.ENGINES.pop("_ok")
            self.assertTrue(ok, msg)
            body = (Path(td) / "_meta" / "copy_draft__ok.md").read_text(
                encoding="utf-8")
        self.assertTrue(body.startswith("> engine: _ok · "))
        self.assertIn("標題參考:", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
