#!/usr/bin/env python3
"""test_cut.py — cut.py(一行出片)的行為鎖定測試。

鎖的是不碰 ffmpeg / 不碰 Drive 的純邏輯:Drive↔session cutplan 的語意差異
比對、Drive 資料夾配對(memo 優先)、輸出檔名遞增。

跑法:
    python3 scripts/tests/test_cut.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import datetime as dt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audio"))
from cut import (drive_cutplan, find_drive_dir, next_out_name,  # noqa: E402
                 next_version_dir, semantic_diff)

HEAD = "# Cutplan — test\n\n## ⚙ max-pause=1.5 tempo=1.0\n"
ROWS = ("- [x] B0001 [0:02–0:05] [Sarah] 嗨大家好。\n"
        "- [x] B0002 [0:06–0:09] [KIN] 我是KIN。\n"
        "- [ ] B0003 [0:10–0:12] [Mars] 呃這個。\n")


def write(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


class TestSemanticDiff(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.a = write(self.d, "a.md", HEAD + ROWS)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_keep_flip_reported_with_direction(self) -> None:
        b = write(self.d, "b.md", HEAD + ROWS.replace(
            "- [x] B0002", "- [ ] B0002"))
        out = "\n".join(semantic_diff(self.a, b))
        self.assertIn("勾選翻轉 1 個", out)
        self.assertIn("B0002(留→剪)", out)

    def test_strike_count_change_reported(self) -> None:
        b = write(self.d, "b.md", HEAD + ROWS.replace(
            "[KIN] 我是KIN。", "[KIN] 我是~~KIN~~。"))
        out = "\n".join(semantic_diff(self.a, b))
        self.assertIn("刪除線變動", out)
        self.assertIn("B0002 0→1", out)

    def test_manual_cut_rows_diffed_per_side(self) -> None:
        b = write(self.d, "b.md", HEAD + "## ✂ 12.5-13.5 空白\n" + ROWS)
        out = "\n".join(semantic_diff(self.a, b))
        self.assertIn("✂ 只在 B", out)
        self.assertIn("12.5-13.5", out)

    def test_config_change_reported(self) -> None:
        b = write(self.d, "b.md", (HEAD.replace("max-pause=1.5", "max-pause=0.9")
                                   + ROWS))
        out = "\n".join(semantic_diff(self.a, b))
        self.assertIn("max-pause: 1.5→0.9", out)

    def test_block_set_mismatch_warns_regenerated_cutplan(self) -> None:
        """一邊重新產生過(block 編號位移)→ 必須警告,不能無腦覆蓋。"""
        b = write(self.d, "b.md", HEAD + ROWS
                  + "- [x] B0004 [0:13–0:15] [Sarah] 多一句。\n")
        out = "\n".join(semantic_diff(self.a, b))
        self.assertIn("block 集合不一致", out)

    def test_music_param_change_reported(self) -> None:
        """🎵 參數(lead/end)動了要看得見 — EP16 的結語就是被 lead=13 蓋掉的。"""
        head = HEAD + "## 🎵 ending fadein=2 lead=13\n"
        a = write(self.d, "ma.md", head + ROWS)
        b = write(self.d, "mb.md",
                  HEAD + "## 🎵 ending end=20 fadein=2 lead=3\n" + ROWS)
        out = "\n".join(semantic_diff(a, b))
        self.assertIn("🎵 ending", out)
        self.assertIn("lead 13.0→3.0", out)
        self.assertIn("end None→20.0", out)

    def test_music_only_on_one_side_reported(self) -> None:
        b = write(self.d, "mb.md", HEAD + "## 🎵 break start=0 end=8\n" + ROWS)
        self.assertIn("🎵 break:只在 B", "\n".join(semantic_diff(self.a, b)))

    def test_cosmetic_only_diff_says_no_edit_impact(self) -> None:
        b = write(self.d, "b.md", HEAD + "> 隨手寫的註解\n" + ROWS)
        self.assertIn("不影響剪輯", "\n".join(semantic_diff(self.a, b)))


class TestOutName(unittest.TestCase):
    def test_explicit_name_wins(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(next_out_name(Path(t), "x.mp3"), "x.mp3")

    def test_increments_past_existing_versions(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.assertEqual(next_out_name(d, None), "final_cut_v2.mp3")
            (d / "final_cut_v2.mp3").touch()
            (d / "final_cut_v3.mp3").touch()
            self.assertEqual(next_out_name(d, None), "final_cut_v4.mp3")


class TestVersionDir(unittest.TestCase):
    NOW = dt.datetime(2026, 8, 10, 18, 30)

    def test_first_version_and_ai_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.assertEqual(next_version_dir(d, False, self.NOW).name,
                             "v1_20260810-1830")
            self.assertEqual(next_version_dir(d, True, self.NOW).name,
                             "v1_20260810-1830-AI")

    def test_number_continues_and_ignores_non_version_dirs(self) -> None:
        """_meta/raw 這種非版本資料夾不能影響編號。"""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for n in ("v1_20260810-1830-AI", "v2_20260810-1900", "_meta", "raw"):
                (d / n).mkdir()
            self.assertEqual(next_version_dir(d, False, self.NOW).name,
                             "v3_20260810-1830")


class TestDriveCutplanMigration(unittest.TestCase):
    def test_legacy_root_cutplan_moves_into_meta(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "cutplan.md").write_text("legacy", encoding="utf-8")
            p = drive_cutplan(d)
            self.assertEqual(p, d / "_meta" / "cutplan.md")
            self.assertEqual(p.read_text(encoding="utf-8"), "legacy")
            self.assertFalse((d / "cutplan.md").exists(), "舊檔要搬走,不留兩份分叉")

    def test_existing_meta_wins_and_root_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "_meta").mkdir()
            (d / "_meta" / "cutplan.md").write_text("new", encoding="utf-8")
            (d / "cutplan.md").write_text("stale", encoding="utf-8")
            self.assertEqual(drive_cutplan(d).read_text(encoding="utf-8"), "new")


class TestFindDriveDir(unittest.TestCase):
    def test_override_is_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir, drive = Path(t) / "s", Path(t) / "drive"
            sdir.mkdir()
            drive.mkdir()
            self.assertEqual(find_drive_dir(sdir, drive), drive)
            # 第二次不給 override 也要從 memo 撈回來
            self.assertEqual(find_drive_dir(sdir, None), drive)

    def test_stale_memo_pointing_nowhere_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t) / "2026-01-01_EP99-無此集"
            sdir.mkdir()
            (sdir / ".drive_dir").write_text("/nope/gone", encoding="utf-8")
            self.assertIsNone(find_drive_dir(sdir, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
