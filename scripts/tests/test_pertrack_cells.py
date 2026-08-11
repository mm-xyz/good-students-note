#!/usr/bin/env python3
"""test_pertrack_cells.py — 分軌剪輯的 atomic cell 模型(D4/D5/D6)。

鎖的是純邏輯:三軌 block 起訖/刪除線/✂ 手動剪點取聯集切 cell、每個 cell×每軌
只有 KEEP/DUCK/SILENT、同軌矛盾勾選 FAIL、activity mask(橋接短空隙、
lookahead/hangover)、長停頓收緊、保留區間輸出、等功率 fade 形狀。

跑法:
    python3 scripts/tests/test_pertrack_cells.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from pertrack_cells import (  # noqa: E402
    KEEP, DUCK, SILENT, ConflictError, TrackPlan,
    build_cells, apply_mask, resolve_time, merge_cells, retained_ranges,
    track_envelopes, equal_power_ramp,
)


def blk(bid, a, b, keep=True, kind="speech", strikes=()):
    return {"id": bid, "start": a, "end": b, "keep": keep, "kind": kind,
            "strikes": [list(s) for s in strikes]}


def T(name, *blocks, prefix=None):
    return TrackPlan(name=name, prefix=prefix or name[:2].upper(),
                     blocks=list(blocks))


def states_at(cells, t):
    for c in cells:
        if c["a"] <= t < c["b"]:
            return c["state"]
    return None


class TestBuildCells(unittest.TestCase):
    def test_boundaries_are_the_union_and_cells_do_not_overlap(self):
        cells = build_cells([T("Mars", blk("MR1", 1.0, 2.0)),
                             T("KIN", blk("KN1", 1.5, 3.0))])
        edges = [(c["a"], c["b"]) for c in cells]
        self.assertEqual(edges, [(1.0, 1.5), (1.5, 2.0), (2.0, 3.0)])
        for x, y in zip(cells, cells[1:]):
            self.assertLessEqual(x["b"], y["a"])

    def test_uncovered_track_defaults_to_duck(self):
        cells = build_cells([T("Mars", blk("MR1", 1.0, 2.0)), T("KIN")])
        self.assertEqual(states_at(cells, 1.5), {"Mars": KEEP, "KIN": DUCK})

    def test_unchecked_block_is_silent_not_duck(self):
        cells = build_cells([T("Mars", blk("MR1", 1.0, 2.0)),
                             T("KIN", blk("KN1", 1.0, 2.0, keep=False,
                                          kind="voicing"))])
        self.assertEqual(states_at(cells, 1.5), {"Mars": KEEP, "KIN": SILENT})

    def test_strike_span_silences_only_that_track(self):
        cells = build_cells([T("Mars", blk("MR1", 1.0, 2.0,
                                           strikes=[(1.2, 1.4)])),
                             T("KIN", blk("KN1", 1.0, 2.0))])
        self.assertEqual(states_at(cells, 1.3), {"Mars": SILENT, "KIN": KEEP})
        self.assertEqual(states_at(cells, 1.5), {"Mars": KEEP, "KIN": KEEP})

    def test_manual_cut_silences_every_track(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 4.0))],
                            manual_cuts=[[1.0, 2.0]])
        self.assertEqual(states_at(cells, 1.5), {"Mars": SILENT})
        self.assertEqual(states_at(cells, 3.0), {"Mars": KEEP})

    def test_checked_gap_row_opens_every_track(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 1.0)), T("KIN")],
                            gaps=[{"id": "G1", "start": 1.0, "end": 4.0,
                                   "keep": True}])
        self.assertEqual(states_at(cells, 2.0), {"Mars": KEEP, "KIN": KEEP})

    def test_unchecked_gap_row_silences_every_track(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 1.0)), T("KIN")],
                            gaps=[{"id": "G1", "start": 1.0, "end": 4.0,
                                   "keep": False}])
        self.assertEqual(states_at(cells, 2.0), {"Mars": SILENT, "KIN": SILENT})

    def test_same_track_overlapping_contradictory_blocks_fail(self):
        with self.assertRaises(ConflictError) as cm:
            build_cells([T("Mars", blk("MR1", 1.0, 2.0, keep=True),
                           blk("MR2", 1.5, 2.5, keep=False))])
        self.assertIn("MR1", str(cm.exception))
        self.assertIn("MR2", str(cm.exception))

    def test_same_track_overlapping_agreeing_blocks_are_fine(self):
        cells = build_cells([T("Mars", blk("MR1", 1.0, 2.0),
                               blk("MR2", 1.5, 2.5))])
        self.assertEqual(states_at(cells, 1.7), {"Mars": KEEP})

    def test_strike_over_a_kept_overlapping_block_is_a_conflict(self):
        with self.assertRaises(ConflictError):
            build_cells([T("Mars", blk("MR1", 1.0, 2.0, strikes=[(1.2, 1.4)]),
                           blk("MR2", 1.1, 1.5))])


class TestActivityMask(unittest.TestCase):
    def test_short_all_duck_gap_is_bridged_by_previous_speaker(self):
        cells = apply_mask(build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                                        T("KIN", blk("KN1", 1.5, 2.5))]),
                           hold=0.9, lookahead=0.0, hangover=0.0)
        self.assertEqual(states_at(cells, 1.2), {"Mars": KEEP, "KIN": DUCK})

    def test_long_all_duck_gap_is_not_bridged(self):
        cells = apply_mask(build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                                        T("KIN", blk("KN1", 5.0, 6.0))]),
                           hold=0.9, lookahead=0.0, hangover=0.0)
        self.assertEqual(states_at(cells, 3.0), {"Mars": DUCK, "KIN": DUCK})

    def test_bridging_never_overrides_an_explicit_silent_cell(self):
        cells = apply_mask(build_cells([
            T("Mars", blk("MR1", 0.0, 1.0), blk("MR2", 1.6, 2.6)),
            T("KIN", blk("KN1", 1.1, 1.5, keep=False, kind="voicing"))]),
            hold=0.9, lookahead=0.0, hangover=0.0)
        self.assertEqual(states_at(cells, 1.3)["KIN"], SILENT)
        self.assertEqual(states_at(cells, 1.3)["Mars"], KEEP)

    def test_lookahead_and_hangover_extend_the_gate(self):
        cells = apply_mask(build_cells([
            T("Mars", blk("MR1", 2.0, 3.0)),
            T("KIN", blk("KN1", 0.0, 0.5), blk("KN2", 6.0, 7.0))]),
            hold=0.0, lookahead=0.05, hangover=0.15)
        self.assertEqual(states_at(cells, 1.97)["Mars"], KEEP)
        self.assertEqual(states_at(cells, 1.90)["Mars"], DUCK)
        self.assertEqual(states_at(cells, 3.10)["Mars"], KEEP)
        self.assertEqual(states_at(cells, 3.20)["Mars"], DUCK)


class TestResolveTime(unittest.TestCase):
    def test_all_silent_cell_is_dropped_from_the_timeline(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                             T("KIN", blk("KN1", 1.0, 2.0, keep=False))])
        kept, dropped = resolve_time(cells, max_pause=0.9, pause_keep=0.6)
        self.assertEqual(retained_ranges(kept), [[0.0, 1.0]])
        self.assertEqual(dropped, [[1.0, 2.0]])

    def test_one_keep_holds_the_time_open_for_everyone(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 2.0)),
                             T("KIN", blk("KN1", 1.0, 2.0, keep=False))])
        kept, dropped = resolve_time(cells, max_pause=0.9, pause_keep=0.6)
        self.assertEqual(retained_ranges(kept), [[0.0, 2.0]])
        self.assertEqual(dropped, [])
        self.assertEqual(states_at(kept, 1.5), {"Mars": KEEP, "KIN": SILENT})

    def test_long_dead_air_is_collapsed_to_pause_keep(self):
        cells = apply_mask(build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                                        T("KIN", blk("KN1", 5.0, 6.0))]),
                           hold=0.9, lookahead=0.0, hangover=0.0)
        kept, dropped = resolve_time(cells, max_pause=0.9, pause_keep=0.6)
        total = sum(b - a for a, b in retained_ranges(kept))
        self.assertAlmostEqual(total, 1.0 + 0.6 + 1.0, places=6)
        self.assertEqual(len(dropped), 1)
        self.assertAlmostEqual(dropped[0][0], 1.3, places=6)
        self.assertAlmostEqual(dropped[0][1], 4.7, places=6)

    def test_collapsed_pause_keeps_a_mic_open_so_the_floor_never_drops(self):
        cells = apply_mask(build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                                        T("KIN", blk("KN1", 5.0, 6.0))]),
                           hold=0.9, lookahead=0.0, hangover=0.0)
        kept, _ = resolve_time(cells, max_pause=0.9, pause_keep=0.6)
        self.assertEqual(states_at(kept, 1.1)["Mars"], KEEP)
        self.assertEqual(states_at(kept, 4.9)["KIN"], KEEP)

    def test_leading_and_trailing_dead_air_are_dropped(self):
        cells = build_cells([T("Mars", blk("MR1", 10.0, 11.0))],
                            gaps=[{"id": "G1", "start": 0.0, "end": 10.0,
                                   "keep": False}])
        kept, _ = resolve_time(cells, max_pause=0.9, pause_keep=0.6)
        self.assertEqual(retained_ranges(kept), [[10.0, 11.0]])


class TestRetainedRangesAndEnvelopes(unittest.TestCase):
    def test_adjacent_retained_cells_merge_into_one_range(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                             T("KIN", blk("KN1", 1.0, 2.0))])
        self.assertEqual(retained_ranges(cells), [[0.0, 2.0]])

    def test_envelope_gain_levels_per_track(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                             T("KIN", blk("KN1", 0.0, 1.0, keep=False))])
        env = track_envelopes(cells, [[0.0, 1.0]], duck_db=-27.0,
                              silent_db=-60.0)
        self.assertEqual(env["Mars"], [(0.0, 1.0, 0.0)])
        self.assertEqual(env["KIN"], [(0.0, 1.0, -60.0)])

    def test_envelope_is_expressed_in_bus_time_not_source_time(self):
        cells = build_cells([T("Mars", blk("MR1", 0.0, 1.0)),
                             T("KIN", blk("KN1", 5.0, 6.0))])
        env = track_envelopes(cells, [[0.0, 1.0], [5.0, 6.0]], duck_db=-27.0,
                              silent_db=-60.0)
        self.assertEqual(env["KIN"], [(0.0, 1.0, -27.0), (1.0, 2.0, 0.0)])

    def test_equal_power_ramp_preserves_power_at_the_midpoint(self):
        ramp = equal_power_ramp(1.0, 0.0, 5)      # 1.0 → 0.0 振幅
        self.assertAlmostEqual(ramp[0], 1.0, places=6)
        self.assertAlmostEqual(ramp[-1], 0.0, places=6)
        self.assertAlmostEqual(ramp[2] ** 2, 0.5, places=6)

    def test_merge_cells_collapses_runs_with_identical_state(self):
        cells = [{"a": 0.0, "b": 1.0, "state": {"Mars": KEEP}},
                 {"a": 1.0, "b": 2.0, "state": {"Mars": KEEP}},
                 {"a": 2.0, "b": 3.0, "state": {"Mars": DUCK}}]
        self.assertEqual([(c["a"], c["b"]) for c in merge_cells(cells)],
                         [(0.0, 2.0), (2.0, 3.0)])


if __name__ == "__main__":
    unittest.main(verbosity=1)
