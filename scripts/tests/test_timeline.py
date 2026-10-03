#!/usr/bin/env python3
"""test_timeline.py — cutplan.timeline.json(Lifov #1078):聽成品時對照 cutplan。

每個 block id(B/S/G)→ 在成品中的起點秒數(可能多個:🎬 集錦),或 null=成品裡
沒有。判定一律看 cut_map **實際保留範圍**,不看勾選——取消勾選卻被併回成品的列
要誠實標成「仍出現」,編輯器才看得出不一致。

跑法:
    python3 scripts/tests/test_timeline.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AUDIO_DIR))
sys.path.insert(0, str(TESTS_DIR))

from render_cut import build_timeline  # noqa: E402


def blk(bid, start, end, insert=None):
    return {"id": bid, "start": start, "end": end, "insert": insert}


SPEECH = [  # 成品:正文 10–20s → 0s 起、正文 30–40s → 10s 起、🎬 重播 12–14s → 25s 起
    {"src_start": 10.0, "src_end": 20.0, "dst_start": 0.0},
    {"src_start": 30.0, "src_end": 40.0, "dst_start": 10.0},
    {"src_start": 12.0, "src_end": 14.0, "dst_start": 25.0},
]


class TestBuildTimeline(unittest.TestCase):
    def test_kept_block_maps_to_product_time(self):
        tl = build_timeline([blk("B0001", 15.0, 16.0)], SPEECH, [])
        self.assertEqual(tl["B0001"], [5.0])

    def test_block_outside_kept_ranges_is_null(self):
        tl = build_timeline([blk("B0002", 22.0, 25.0)], SPEECH, [])
        self.assertIsNone(tl["B0002"])

    def test_clip_replay_lists_every_time(self):
        tl = build_timeline([blk("B0003", 12.5, 13.0)], SPEECH, [])
        self.assertEqual(tl["B0003"], [2.5, 25.5])

    def test_unchecked_block_merged_back_is_reported_present(self):
        """勾選不是依據:就算 md 是 [ ],起點落在保留範圍裡就是在成品裡。"""
        tl = build_timeline([blk("B0004", 31.0, 31.5)], SPEECH, [])
        self.assertEqual(tl["B0004"], [11.0])

    def test_start_slightly_before_range_counts(self):
        """剪點微調(谷底/word 保護)會把保留範圍起點往後推一點點:block 大半在
        保留範圍裡就算在成品裡,時間取它被保留那部分的起點。"""
        tl = build_timeline([blk("B0005", 29.95, 31.0)], SPEECH, [])
        self.assertEqual(tl["B0005"], [10.0])

    def test_edge_sliver_is_not_present(self):
        """EP22 實測:相鄰保留段的邊界 snap 進一句沒勾的「嗯」1–19%,用「起點落在
        範圍內」會把它標成「取消卻仍出現」(11 個假警報)。"""
        far = build_timeline([blk("B0006", 29.0, 30.05)], SPEECH, [])
        self.assertIsNone(far["B0006"])
        tail = build_timeline([blk("B0007", 19.9, 21.0)], SPEECH, [])
        self.assertIsNone(tail["B0007"])

    def test_strike_at_block_start_still_present(self):
        """block 開頭幾個字被刪除線剪掉,保留範圍從 block 中間開始:EP22 的
        B0026/B0028 就是這樣被誤標「勾了卻未出現」。"""
        sp = [{"src_start": 50.6, "src_end": 60.0, "dst_start": 100.0}]
        tl = build_timeline([blk("B0026", 50.0, 52.0)], sp, [])
        self.assertEqual(tl["B0026"], [100.0])

    def test_block_split_by_pause_trim_is_one_time(self):
        """停頓收緊把一句切成兩段保留範圍:還是同一次出現,只列一個時間。"""
        sp = [{"src_start": 70.0, "src_end": 71.0, "dst_start": 200.0},
              {"src_start": 71.6, "src_end": 73.0, "dst_start": 201.0}]
        tl = build_timeline([blk("B0010", 70.2, 72.5)], sp, [])
        self.assertEqual(tl["B0010"], [200.2])

    def test_tempo_scales_offset(self):
        tl = build_timeline([blk("B0001", 15.0, 16.0)], SPEECH, [], tempo=1.25)
        self.assertEqual(tl["B0001"], [4.0])

    def test_insert_s_rows_use_insert_segments(self):
        ins = [{"file": "raw/補錄.WAV", "a": 2.0, "b": 8.0, "dst_start": 50.0,
                "tempo": 1.0}]
        tl = build_timeline([blk("S0001", 3.0, 4.0, insert="raw/補錄.WAV"),
                             blk("S0002", 9.0, 10.0, insert="raw/補錄.WAV"),
                             blk("S0003", 3.0, 4.0, insert="raw/other.WAV")],
                            SPEECH, ins)
        self.assertEqual(tl["S0001"], [51.0])
        self.assertIsNone(tl["S0002"])
        self.assertIsNone(tl["S0003"])

    def test_same_id_listed_once_per_time(self):
        tl = build_timeline([blk("B0001", 15.0, 16.0), blk("B0001", 15.0, 16.0)],
                            SPEECH, [])
        self.assertEqual(tl["B0001"], [5.0])


class TestRenderWritesTimeline(unittest.TestCase):
    """真的出片(合成合軌 session):timeline 寫在 cutplan.md 旁邊。"""

    def test_render_writes_timeline_next_to_plan(self):
        from test_render_tracks import ROWS, make_session, render
        rows = ROWS + "## 🎬 集錦\n- [x] B0001 [0:00–0:02] [Mars] 第一句。\n"
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, rows=rows, tracks=False)
            r = render(s, "--out", "out.wav", "--loudnorm", "", "--dynaudnorm", "")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            tl = json.loads((s / "cutplan.timeline.json").read_text(encoding="utf-8"))
            cm = json.loads((s / "cut_map.json").read_text(encoding="utf-8"))
        self.assertEqual(tl["version"], "out")
        self.assertIn("generated_at", tl)
        self.assertIsNone(tl["blocks"]["B0003"])               # 沒勾、不在成品
        self.assertEqual(len(tl["blocks"]["B0001"]), 2)        # 正文＋集錦
        self.assertEqual(tl["blocks"]["B0001"][0], cm["ranges"][0]["dst_start"])
        self.assertIn("cutplan.timeline.json", r.stdout)

    def test_dry_run_does_not_write_timeline(self):
        from test_render_tracks import make_session, render
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, tracks=False)
            r = render(s, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((s / "cutplan.timeline.json").exists())


class TestCutStampsVersion(unittest.TestCase):
    def test_finalize_timeline_sets_version_and_copies(self):
        from cut import finalize_timeline
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            src = d / "cutplan.timeline.json"
            src.write_text(json.dumps({"version": "final_cut_v3",
                                       "generated_at": "x", "blocks": {}}),
                           encoding="utf-8")
            (d / "drive").mkdir()
            (d / "v3").mkdir()
            finalize_timeline(src, "v3_20261003-1200",
                              [d / "drive" / "cutplan.timeline.json",
                               d / "v3" / "cutplan.timeline.json"])
            for p in (src, d / "drive" / "cutplan.timeline.json",
                      d / "v3" / "cutplan.timeline.json"):
                self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["version"],
                                 "v3_20261003-1200")


if __name__ == "__main__":
    unittest.main()
