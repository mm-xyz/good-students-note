#!/usr/bin/env python3
"""test_finalize.py — 定稿歸檔(finalize.py)的行為鎖定測試。

2026-09-11 MM:「定稿之後把沒用到的都收到 Archive folder,我想要每一集的根目錄
都是乾淨的」。

跑法:
    python3 scripts/tests/test_finalize.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audio"))
from finalize import plan_finalize, plan_restore  # noqa: E402


def build(root: Path) -> Path:
    """一個剪到第三版、定稿 v2 的典型集數。"""
    d = root / "ep"
    for v in ("v1_20260910-1000", "v2_20260910-1200-AI", "v3_20260911-0800"):
        (d / v).mkdir(parents=True)
        (d / v / f"EP_{v.split('_')[0]}.mp3").write_bytes(b"x")
    (d / "raw").mkdir()
    (d / "raw" / "補錄.wav").write_bytes(b"x")
    (d / "_meta").mkdir()
    (d / "_meta" / "highlights.md").write_text("x", encoding="utf-8")
    for n in ("cutplan.md", "cutplan.pertrack.md", "cutplan.json",
              "words.json", "prosody.json",
              "transcript.srt", "audio16k.wav", "cut_map.json", "context.txt"):
        (d / n).write_text("x", encoding="utf-8")
    (d / "source.wav").write_bytes(b"original recording")
    (d / "預聽_stereo.mp3").write_bytes(b"x")
    return d


def moved(d: Path, final: str) -> dict[str, str]:
    return {s.name: str(dst.relative_to(d)) for s, dst in plan_finalize(d, final)}


class TestWhatStaysAtRoot(unittest.TestCase):
    def test_final_version_dir_stays(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            self.assertNotIn("v2_20260910-1200-AI", moved(d, "v2"))

    def test_raw_stays(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            self.assertNotIn("raw", moved(d, "v2"))

    def test_original_recording_is_never_touched(self) -> None:
        """原始錄音不搬不刪——搬動它的風險遠高於根目錄乾淨的收益。"""
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            self.assertNotIn("source.wav", moved(d, "v2"))


class TestWhatGetsArchived(unittest.TestCase):
    def test_other_version_dirs_are_archived(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            m = moved(d, "v2")
            self.assertEqual(m["v1_20260910-1000"],
                             "_archive/v1_20260910-1000")
            self.assertEqual(m["v3_20260911-0800"],
                             "_archive/v3_20260911-0800")

    def test_pipeline_workfiles_go_to_archive_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            m = moved(d, "v2")
            for n in ("words.json", "prosody.json", "transcript.srt",
                      "audio16k.wav", "cut_map.json", "cutplan.json",
                      "cutplan.md", "cutplan.pertrack.md", "context.txt"):
                self.assertEqual(m[n], f"_archive/pipeline/{n}",
                                 f"{n} 應收進 _archive/pipeline/")

    def test_stray_renders_and_meta_are_archived(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            m = moved(d, "v2")
            self.assertEqual(m["預聽_stereo.mp3"], "_archive/預聽_stereo.mp3")
            self.assertEqual(m["_meta"], "_archive/_meta")


class TestFinalSelection(unittest.TestCase):
    def test_unknown_version_raises(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            with self.assertRaises(SystemExit):
                plan_finalize(d, "v9")

    def test_ambiguous_prefix_raises(self) -> None:
        """v1 不可以誤命中 v10——前綴比對要比到分隔線為止。"""
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            (d / "v10_20260912-0900").mkdir()
            m = moved(d, "v1")
            self.assertEqual(m["v10_20260912-0900"],
                             "_archive/v10_20260912-0900")

    def test_already_finalized_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            for src, dst in plan_finalize(d, "v2"):
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
            self.assertEqual(plan_finalize(d, "v2"), [])


class TestRestore(unittest.TestCase):
    def test_restore_puts_workfiles_back(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            for src, dst in plan_finalize(d, "v2"):
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
            back = {s.name: str(dst.relative_to(d)) for s, dst in plan_restore(d)}
            self.assertEqual(back["words.json"], "words.json")
            self.assertEqual(back["cutplan.md"], "cutplan.md")
            # 版本目錄留在 _archive/,restore 只管重剪需要的工作檔
            self.assertNotIn("v1_20260910-1000", back)

    def test_restore_without_archive_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = build(Path(t))
            self.assertEqual(plan_restore(d), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
