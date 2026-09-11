#!/usr/bin/env python3
"""test_diff_clips.py — diff 片段落點與 Drive 鏡像的行為鎖定測試。

2026-09-11 MM 拍板:diff 要住「那一版」裡面,不要摔在 session 根
(EP18 曾散出 diff_clips / _v5 / _v6 / _v10 / _v11 五堆)。

跑法:
    python3 scripts/tests/test_diff_clips.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audio"))
from diff_clips import mirror_to_drive, resolve_out_dir  # noqa: E402


class TestResolveOutDir(unittest.TestCase):
    def test_render_inside_version_dir_puts_diff_beside_it(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            vdir = sdir / "v3_20260911-1030-AI"
            vdir.mkdir()
            render = vdir / "EP19_v3.mp3"
            render.write_bytes(b"")
            self.assertEqual(resolve_out_dir(sdir, render, None), vdir / "diff")

    def test_render_at_session_root_falls_back(self) -> None:
        """成品還在根(工作檔)時沒有版本目錄可掛,退回舊行為。"""
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            render = sdir / "final_cut_v4.mp3"
            render.write_bytes(b"")
            self.assertEqual(resolve_out_dir(sdir, render, None),
                             sdir / "diff_clips")

    def test_explicit_override_always_wins(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            vdir = sdir / "v3_20260911-1030-AI"
            vdir.mkdir()
            render = vdir / "EP19_v3.mp3"
            render.write_bytes(b"")
            self.assertEqual(resolve_out_dir(sdir, render, "my_clips"),
                             sdir / "my_clips")


class TestMirrorToDrive(unittest.TestCase):
    def test_diff_lands_in_matching_drive_version_dir(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            sdir = root / "session"
            vdir = sdir / "v3_20260911-1030-AI"
            out = vdir / "diff"
            out.mkdir(parents=True)
            (out / "01_0-12_B0007.mp3").write_bytes(b"clip")
            drive = root / "EP19-0"
            (drive / "v3_20260911-1030-AI").mkdir(parents=True)
            (sdir / ".drive_dir").write_text(str(drive), encoding="utf-8")

            self.assertEqual(mirror_to_drive(out, sdir),
                             drive / "v3_20260911-1030-AI" / "diff")
            self.assertEqual(
                (drive / "v3_20260911-1030-AI" / "diff"
                 / "01_0-12_B0007.mp3").read_bytes(), b"clip")

    def test_no_drive_memo_is_a_quiet_noop(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            out = sdir / "v1_20260911-0816" / "diff"
            out.mkdir(parents=True)
            self.assertIsNone(mirror_to_drive(out, sdir))

    def test_drive_missing_that_version_dir_is_a_quiet_noop(self) -> None:
        """Drive 上還沒有這一版(--no-push 出的片)就別自己造目錄。"""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            sdir = root / "session"
            out = sdir / "v9_20260911-0816" / "diff"
            out.mkdir(parents=True)
            drive = root / "EP19-0"
            drive.mkdir()
            (sdir / ".drive_dir").write_text(str(drive), encoding="utf-8")
            self.assertIsNone(mirror_to_drive(out, sdir))
            self.assertEqual(list(drive.iterdir()), [])

    def test_stale_clips_on_drive_are_cleared(self) -> None:
        """重跑 diff 時 Drive 那份也要跟著換掉,不留上一輪的片段。"""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            sdir = root / "session"
            out = sdir / "v3_20260911-1030" / "diff"
            out.mkdir(parents=True)
            (out / "01_new.mp3").write_bytes(b"new")
            drive = root / "EP19-0"
            dst = drive / "v3_20260911-1030" / "diff"
            dst.mkdir(parents=True)
            (dst / "01_old.mp3").write_bytes(b"old")
            (sdir / ".drive_dir").write_text(str(drive), encoding="utf-8")

            mirror_to_drive(out, sdir)
            self.assertEqual(sorted(p.name for p in dst.iterdir()),
                             ["01_new.mp3"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
