#!/usr/bin/env python3
"""test_session_paths.py — 工作檔路徑單一真相源的行為鎖定測試。

跑法:
    python3 scripts/tests/test_session_paths.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audio"))
import session_paths  # noqa: E402
from session_paths import ensure_work_dir, work_dir  # noqa: E402


class TestDefaultIsSessionRoot(unittest.TestCase):
    """預設不改行為:WORK_SUBDIR 空字串 = 工作檔留在 session 根。"""

    def test_work_dir_is_session_root(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(work_dir(Path(t)), Path(t))

    def test_ensure_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(ensure_work_dir(Path(t)), Path(t))
            self.assertEqual(list(Path(t).iterdir()), [])


class TestRelocated(unittest.TestCase):
    """改一個值就整批搬家——這是這支模組存在的理由。"""

    def test_existing_subdir_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            (sdir / "_work").mkdir()
            with mock.patch.object(session_paths, "WORK_SUBDIR", "_work"):
                self.assertEqual(work_dir(sdir), sdir / "_work")

    def test_ensure_creates_the_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            with mock.patch.object(session_paths, "WORK_SUBDIR", "_work"):
                self.assertEqual(ensure_work_dir(sdir), sdir / "_work")
            self.assertTrue((sdir / "_work").is_dir())

    def test_unmigrated_session_falls_back_to_root(self) -> None:
        """設定改了但這個 session 還沒遷移,要讀得到舊位置,不是整批壞掉。"""
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            (sdir / "words.json").write_text("x", encoding="utf-8")
            with mock.patch.object(session_paths, "WORK_SUBDIR", "_work"):
                self.assertEqual(work_dir(sdir), sdir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
