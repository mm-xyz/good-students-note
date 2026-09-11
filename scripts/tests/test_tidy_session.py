#!/usr/bin/env python3
"""test_tidy_session.py — tidy_session.py(session 目錄分類)的行為鎖定測試。

鎖的是 plan() 的純邏輯(不真的搬檔):_meta/_bak/raw 分流、版本目錄編號接號、
以及「已經有 cut.py 快照的根成品不重複歸位」。

跑法:
    python3 scripts/tests/test_tidy_session.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audio"))
from tidy_session import plan  # noqa: E402


def dests(sdir: Path) -> dict[str, str]:
    """{來源檔名: 相對目的路徑} — 比對用。"""
    return {s.name: str(d.relative_to(sdir)) for s, d in plan(sdir, {})}


class TestBasicRouting(unittest.TestCase):
    def test_meta_bak_and_asset_go_to_their_folders(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            (sdir / "highlights.md").write_text("x", encoding="utf-8")
            (sdir / "chapters.txt").write_text("x", encoding="utf-8")
            (sdir / "cutplan.md.bak-20260911").write_text("x", encoding="utf-8")
            (sdir / "opening_430.mp3").write_bytes(b"")
            d = dests(sdir)
            self.assertEqual(d["highlights.md"], "_meta/highlights.md")
            self.assertEqual(d["chapters.txt"], "_meta/chapters.txt")
            self.assertEqual(d["cutplan.md.bak-20260911"],
                             "_bak/cutplan.md.bak-20260911")
            self.assertEqual(d["opening_430.mp3"], "raw/opening_430.mp3")

    def test_pipeline_workfiles_stay_put(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            for n in ("cutplan.md", "words.json", "source.wav", "context.txt"):
                (sdir / n).write_text("x", encoding="utf-8")
            self.assertEqual(plan(sdir, {}), [])


class TestVersionDirNumbering(unittest.TestCase):
    """版本目錄編號要接既有最大號,不可以從 v00 重新開始(2026-09-11 EP19-0 實踩)。

    原本 `v{len(vdirs):02d}` 只數這次要建幾個,完全不看 session 裡已經有的
    vN_ 目錄——cut.py 早就建過 v1_/v2_ 了,tidy 還是會造一個 v00_,版本序列
    當場分岔成兩套編號。
    """

    def test_numbering_continues_after_existing_version_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            (sdir / "v1_20260911-0816-AI").mkdir()
            (sdir / "v2_20260911-0900").mkdir()
            (sdir / "preview_mix.mp3").write_bytes(b"new render")
            d = dests(sdir)
            self.assertTrue(d["preview_mix.mp3"].startswith("v3_"),
                            f"應接在 v2 之後,實際:{d['preview_mix.mp3']}")

    def test_numbering_starts_at_v1_on_empty_session(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            (sdir / "preview_mix.mp3").write_bytes(b"x")
            d = dests(sdir)
            self.assertTrue(d["preview_mix.mp3"].startswith("v1_"),
                            f"第一版該是 v1,實際:{d['preview_mix.mp3']}")


class TestAlreadySnapshotted(unittest.TestCase):
    """cut.py 已經把成品複製進 vN_ 版本目錄(改名 EP19_vN.mp3),session 根那份
    是工作檔。tidy 不可以再把它歸位成另一個版本目錄——那會讓同一份音訊在兩個
    版本編號下各存一份(2026-09-11 EP19-0 實踩:v1_ 已存在,tidy 仍要造 v00_)。
    """

    def test_root_render_with_identical_snapshot_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            vdir = sdir / "v1_20260911-0816-AI"
            vdir.mkdir()
            (vdir / "EP19_v1.mp3").write_bytes(b"same audio bytes")
            (sdir / "final_cut_v2.mp3").write_bytes(b"same audio bytes")
            self.assertEqual(plan(sdir, {}), [],
                             "已快照的工作檔不該再被歸位")

    def test_root_render_without_snapshot_is_still_filed(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            sdir = Path(t)
            vdir = sdir / "v1_20260911-0816-AI"
            vdir.mkdir()
            (vdir / "EP19_v1.mp3").write_bytes(b"version one")
            (sdir / "final_cut_v2.mp3").write_bytes(b"a different render")
            d = dests(sdir)
            self.assertTrue(d["final_cut_v2.mp3"].startswith("v2_"),
                            f"沒快照過的成品仍要歸位,實際:{d}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
