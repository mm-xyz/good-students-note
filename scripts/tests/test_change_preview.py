#!/usr/bin/env python3
"""test_change_preview.py — 改動試聽(2026-10-05 MM:「編輯後的片段應該要放上去」)。

每次出片跟上一版比,改到的地方前後各切幾秒串成一個短 mp3,放進版本資料夾,
MM 只聽改過的地方。鎖的是「哪裡算改動」與「試聽檔真的切在那裡」。

跑法:
    python3 scripts/tests/test_change_preview.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from change_preview import build_clips, change_spots, load_version  # noqa: E402


def write_version(vdir: Path, rows: list[str], timeline: dict) -> Path:
    vdir.mkdir(parents=True)
    (vdir / "cutplan.md").write_text(
        "# Cutplan\n\n## ⚙ line=mixdown\n\n" + "\n".join(rows) + "\n",
        encoding="utf-8")
    (vdir / "cutplan.timeline.json").write_text(
        json.dumps({"version": vdir.name, "blocks": timeline}), encoding="utf-8")
    return vdir


ROWS = [
    "- [x] B0001 [0:00–0:02] [Mars] 第一句。 ⟦Mars● Sarah○⟧",
    "- [x] B0002 [0:02–0:04] [Sarah] 第二句。 ⟦Mars○ Sarah●⟧",
    "- [x] B0003 [0:04–0:06] [Mars] 第三句。 ⟦Mars● Sarah○⟧",
    "- [x] B0004 [0:06–0:08] [Mars] 第四句。 ⟦Mars● Sarah○⟧",
]
TL = {"B0001": [10.0], "B0002": [12.0], "B0003": [14.0], "B0004": [16.0]}


class TestChangeSpots(unittest.TestCase):
    def _spots(self, new_rows, new_tl, old_rows=ROWS, old_tl=TL):
        with tempfile.TemporaryDirectory() as td:
            old = load_version(write_version(Path(td) / "v1", old_rows, old_tl))
            new = load_version(write_version(Path(td) / "v2", new_rows, new_tl))
            return change_spots(old, new)

    def test_identical_versions_have_no_spots(self):
        self.assertEqual(self._spots(ROWS, TL), [])

    def test_removed_row_is_anchored_at_next_kept_row(self):
        rows = list(ROWS)
        rows[1] = rows[1].replace("[x]", "[ ]")
        spots = self._spots(rows, {"B0001": [10.0], "B0002": None,
                                   "B0003": [12.0], "B0004": [14.0]})
        self.assertEqual([round(t, 2) for t, _, _ in spots], [12.0])
        self.assertEqual(spots[0][2], ["B0002"])     # 檔名用被剪掉的那列
        self.assertIn("剪掉", spots[0][1])

    def test_restored_row_is_anchored_at_itself(self):
        old = list(ROWS)
        old[2] = old[2].replace("[x]", "[ ]")
        spots = self._spots(ROWS, TL, old_rows=old,
                            old_tl={"B0001": [10.0], "B0002": [12.0],
                                    "B0003": None, "B0004": [14.0]})
        self.assertEqual([(round(t, 2), ids) for t, _, ids in spots],
                         [(14.0, ["B0003"])])
        self.assertIn("加回", spots[0][1])

    def test_strike_and_column_changes_count(self):
        rows = list(ROWS)
        rows[0] = rows[0].replace("第一句", "第~~一~~句")
        rows[3] = rows[3].replace("Sarah○", "Sarah●")
        spots = self._spots(rows, TL)
        self.assertEqual([round(t, 2) for t, _, _ in spots], [10.0, 16.0])
        self.assertIn("刪除線", spots[0][1])
        self.assertIn("軌欄", spots[1][1])

    def test_engine_only_timing_change_is_caught(self):
        """cutplan 沒動、render 改了(例如停頓收緊):相鄰保留列間距變了也算。"""
        spots = self._spots(ROWS, {"B0001": [10.0], "B0002": [12.0],
                                   "B0003": [13.2], "B0004": [15.2]})
        self.assertEqual([round(t, 2) for t, _, _ in spots], [13.2])
        self.assertIn("間距", spots[0][1])

    def test_tiny_timing_jitter_is_ignored(self):
        spots = self._spots(ROWS, {"B0001": [10.0], "B0002": [12.05],
                                   "B0003": [14.05], "B0004": [16.05]})
        self.assertEqual(spots, [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "需要 ffmpeg/ffprobe")
class TestBuildClips(unittest.TestCase):
    """2026-10-05 MM:「檔名可以直接變 Block ID」——每處一個檔,檔名=Block ID。"""

    def _dur(self, p: Path) -> float:
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)], capture_output=True, text=True,
            check=True).stdout)

    def test_one_file_per_spot_named_by_block_id(self):
        with tempfile.TemporaryDirectory() as td:
            full = Path(td) / "full.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=60", "-q:a", "5",
                            str(full)], check=True)
            outdir = Path(td) / "改動試聽"
            listing = build_clips(full, [(10.0, "剪掉 B0002", ["B0002"]),
                                         (11.0, "軌欄 B0003", ["B0003"]),
                                         (40.0, "加回 B0009", ["B0009"])],
                                  outdir, context=3.0)
            names = sorted(p.name for p in outdir.iterdir())
            durs = {p.name: self._dur(p) for p in outdir.iterdir()}
        # 10 與 11 相距 <2×context → 併成一檔 7–14;40 → 37–43
        self.assertEqual(names, ["B0002+B0003.mp3", "B0009.mp3"])
        self.assertAlmostEqual(durs["B0002+B0003.mp3"], 7.0, delta=0.3)
        self.assertAlmostEqual(durs["B0009.mp3"], 6.0, delta=0.3)
        self.assertEqual(len(listing), 2)
        self.assertIn("B0002+B0003.mp3", listing[0])
        self.assertIn("0:07", listing[0])
        self.assertIn("剪掉 B0002", listing[0])

    def test_rerun_clears_stale_clips(self):
        with tempfile.TemporaryDirectory() as td:
            full = Path(td) / "full.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=20", "-q:a", "5",
                            str(full)], check=True)
            outdir = Path(td) / "改動試聽"
            build_clips(full, [(5.0, "x", ["B0001"])], outdir)
            build_clips(full, [(15.0, "y", ["B0007"])], outdir)
            self.assertEqual([p.name for p in outdir.iterdir()], ["B0007.mp3"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
