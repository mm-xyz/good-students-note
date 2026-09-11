#!/usr/bin/env python3
"""test_session_slug.py — session 目錄命名規則。

2026-09-11 MM 拍板:`sessions/{錄音日}_{人給的名字}/`。

日期用**錄音日**不是今天——今天是「處理日」,跟這一集什麼時候錄的無關,而且
同一集重跑一次名字就會變。名字由 `--slug` 給,沒給才退回音檔檔名。

跑法:
    python3 scripts/tests/test_session_slug.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from session import build_slug, recording_date  # noqa: E402


def wav(d: Path, name: str, mtime: dt.datetime | None = None) -> Path:
    p = d / name
    p.write_bytes(b"")
    if mtime:
        ts = mtime.timestamp()
        os.utime(p, (ts, ts))
    return p


class TestRecordingDate(unittest.TestCase):
    def test_underscore_form_from_recorder(self) -> None:
        """錄音機的慣用格式:2026_0907_1917.WAV"""
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "2026_0907_1917.WAV")
            self.assertEqual(recording_date(p), dt.date(2026, 9, 7))

    def test_iso_form(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "2026-09-07-訪談.wav")
            self.assertEqual(recording_date(p), dt.date(2026, 9, 7))

    def test_compact_form(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "EP18_季中回顧20260727-2.wav")
            self.assertEqual(recording_date(p), dt.date(2026, 7, 27))

    def test_falls_back_to_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "隨手錄.wav", dt.datetime(2026, 3, 14, 10, 0))
            self.assertEqual(recording_date(p), dt.date(2026, 3, 14))

    def test_impossible_date_in_name_falls_back(self) -> None:
        """檔名裡的數字不是合法日期(2026_1345)就別硬解,退回 mtime。"""
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "2026_1345_0000.wav", dt.datetime(2026, 3, 14, 10, 0))
            self.assertEqual(recording_date(p), dt.date(2026, 3, 14))


class TestBuildSlug(unittest.TestCase):
    def test_name_given_wins(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "2026_0907_1917.WAV")
            self.assertEqual(build_slug(p, name="EP19-0-包棟介紹"),
                             "2026-09-07_EP19-0-包棟介紹")

    def test_without_name_falls_back_to_filename(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "2026_0907_1917.WAV")
            self.assertEqual(build_slug(p), "2026-09-07_2026_0907_1917")

    def test_date_is_recording_day_not_today(self) -> None:
        """同一個音檔今天跑、下週跑,都要得到同一個 slug。"""
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "2026_0907_1917.WAV")
            self.assertTrue(build_slug(p, name="x").startswith("2026-09-07_"))

    def test_name_is_slugified(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            p = wav(Path(t), "2026_0907_1917.WAV")
            self.assertEqual(build_slug(p, name="EP19 包棟！特輯"),
                             "2026-09-07_EP19-包棟特輯")


if __name__ == "__main__":
    unittest.main(verbosity=2)
