#!/usr/bin/env python3
"""test_pertrack_render.py — 分軌 speech bus 的混音鏈與接縫(D5/D6)。

用極小的合成 WAV 真的跑一次 bus 渲染,鎖:
  · 全域 atrim 三軌完全相同(時間軸一致,不會逐漸失去 sample alignment)
  · 段內各軌 gain envelope(KEEP 0dB / DUCK 常態衰減 / 明確不要才全靜音)
  · gate 邊緣是等功率 fade,不是硬切(接縫不得有 step 爆音)
  · 等功率 pan 混成 speech bus
  · cutplan.md/pertrack.md → TrackPlan 的勾選與刪除線轉譯

跑法:
    python3 scripts/tests/test_pertrack_render.py
"""
from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

try:
    import numpy as np
except ImportError:                                    # pragma: no cover
    np = None

from pertrack_cells import KEEP, DUCK, SILENT  # noqa: E402
from pertrack_render import (mix_ranges, plan_from_program,  # noqa: E402
                             envelope_curve)

SR = 8000


def make_wav(path: Path, amp: float, secs: float = 2.0, freq: float = 0.0):
    n = int(SR * secs)
    if freq:
        vals = [amp * math.sin(2 * math.pi * freq * i / SR) for i in range(n)]
    else:
        vals = [amp] * n
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(b"".join(struct.pack("<h", int(v * 32767)) for v in vals))


@unittest.skipIf(np is None, "需要 numpy")
class TestEnvelopeCurve(unittest.TestCase):
    def test_gate_edge_is_an_equal_power_ramp_not_a_step(self):
        cur = envelope_curve([(0.0, 0.1, 0.0), (0.1, 0.2, -60.0)],
                             SR, 0.2, fade=0.04)
        mid = int(0.1 * SR)
        self.assertAlmostEqual(cur[0], 1.0, places=6)
        self.assertLess(cur[-1], 1e-2)
        # 過渡置中於 0.1s,中點功率 = 兩端功率的平均
        self.assertAlmostEqual(cur[mid] ** 2, (1.0 + 1e-6) / 2, places=3)
        step = float(np.max(np.abs(np.diff(cur))))
        self.assertLess(step, 0.01, "gate 邊緣不可有 step,否則就是爆音")

    def test_no_transition_means_a_flat_curve(self):
        cur = envelope_curve([(0.0, 0.2, -27.0)], SR, 0.2, fade=0.02)
        self.assertAlmostEqual(float(np.ptp(cur)), 0.0, places=9)
        self.assertAlmostEqual(float(cur[0]), 10 ** (-27 / 20), places=6)

    def test_uncovered_tail_falls_back_to_the_duck_level(self):
        cur = envelope_curve([(0.0, 0.1, 0.0)], SR, 0.2, fade=0.02,
                             default_db=-27.0)
        self.assertAlmostEqual(float(cur[-1]), 10 ** (-27 / 20), places=6)


@unittest.skipIf(np is None, "需要 numpy")
class TestMixRanges(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.a, self.b = d / "a.wav", d / "b.wav"
        make_wav(self.a, 0.5)
        make_wav(self.b, 0.25)
        self.out = d / "bus.wav"

    def tearDown(self):
        self.td.cleanup()

    def _mix(self, ranges, env, **kw):
        mix_ranges([self.a, self.b], ranges, env, self.out, sr=SR, **kw)
        with wave.open(str(self.out), "rb") as f:
            n, ch = f.getnframes(), f.getnchannels()
            raw = f.readframes(n)
        sw = 4
        with wave.open(str(self.out), "rb") as f:
            sw = f.getsampwidth()
        dt = {2: "<i2", 4: "<i4"}[sw]
        x = np.frombuffer(raw, dtype=dt).astype(float) / float(2 ** (8 * sw - 1))
        return x.reshape(-1, ch), n

    def test_every_track_gets_the_identical_global_atrim(self):
        x, n = self._mix([[0.0, 0.5], [1.0, 1.25]],
                         {"a": [(0.0, 0.75, 0.0)], "b": [(0.0, 0.75, 0.0)]})
        self.assertEqual(n, int(0.75 * SR))

    def test_keep_and_silent_levels(self):
        x, _ = self._mix([[0.0, 1.0]],
                         {"a": [(0.0, 1.0, 0.0)], "b": [(0.0, 1.0, -60.0)]})
        mid = x[int(0.5 * SR), 0]
        self.assertAlmostEqual(mid, 0.5 / math.sqrt(2), places=2)

    def test_duck_attenuates_instead_of_muting(self):
        x, _ = self._mix([[0.0, 1.0]],
                         {"a": [(0.0, 1.0, -60.0)], "b": [(0.0, 1.0, -27.0)]})
        mid = abs(x[int(0.5 * SR), 0])
        want = 0.25 * 10 ** (-27 / 20) / math.sqrt(2)
        self.assertAlmostEqual(mid, want, places=3)
        self.assertGreater(mid, 0.0)

    def test_equal_power_pan_puts_a_centred_track_equally_in_both_channels(self):
        x, _ = self._mix([[0.0, 1.0]],
                         {"a": [(0.0, 1.0, 0.0)], "b": [(0.0, 1.0, -60.0)]})
        i = int(0.5 * SR)
        self.assertAlmostEqual(x[i, 0], x[i, 1], places=6)

    def test_gate_transition_inside_a_range_has_no_step(self):
        x, _ = self._mix([[0.0, 1.0]],
                         {"a": [(0.0, 0.5, 0.0), (0.5, 1.0, -60.0)],
                          "b": [(0.0, 1.0, -60.0)]}, gate_fade=0.02)
        d = np.max(np.abs(np.diff(x[:, 0])))
        self.assertLess(d, 0.01, f"gate 邊緣 step={d:.4f},會聽到 click")

    def test_static_gain_is_applied_per_track(self):
        x, _ = self._mix([[0.0, 1.0]],
                         {"a": [(0.0, 1.0, 0.0)], "b": [(0.0, 1.0, -60.0)]},
                         static_db={"a": -6.0})
        mid = x[int(0.5 * SR), 0]
        self.assertAlmostEqual(mid, 0.5 * 10 ** (-6 / 20) / math.sqrt(2),
                               places=3)


class TestPlanFromProgram(unittest.TestCase):
    CP = {"tracks": [
        {"speaker": "Mars", "prefix": "MR", "file": "tracks/1_Mars.WAV",
         "blocks": [{"id": "MR0001", "start": 0.0, "end": 1.0,
                     "text": "今天很好", "kind": "speech"},
                    {"id": "MR0002", "start": 2.0, "end": 2.4,
                     "text": "（非詞彙出聲／待辨 0.4s）", "kind": "voicing"}]},
        {"speaker": "KIN", "prefix": "KN", "file": "tracks/3_KIN.WAV",
         "blocks": [{"id": "KN0001", "start": 1.0, "end": 2.0,
                     "text": "對啊", "kind": "speech"}]}]}
    WORDS = [{"start": 0.0, "end": 0.25, "word": "今"},
             {"start": 0.25, "end": 0.5, "word": "天"},
             {"start": 0.5, "end": 0.75, "word": "很"},
             {"start": 0.75, "end": 1.0, "word": "好"}]

    def _plan(self, md):
        from render_cut import parse_program, validate_program
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cutplan.pertrack.md"
            p.write_text(md, encoding="utf-8")
            prog = parse_program(p)
        blocks = [b for t in self.CP["tracks"] for b in t["blocks"]]
        validate_program(blocks, prog, "今天很好對啊")
        return plan_from_program(prog, self.CP, self.WORDS)

    def test_checkbox_becomes_keep(self):
        tr, cuts, gaps = self._plan(
            "# x\n- [x] MR0001 [0:00–0:01] [Mars] 今天很好\n"
            "- [ ] KN0001 [0:01–0:02] [KIN] 對啊\n"
            "- [ ] MR0002 [0:02–0:02] [Mars] （非詞彙出聲／待辨 0.4s）\n")
        by = {t.name: t for t in tr}
        self.assertTrue(by["Mars"].blocks[0]["keep"])
        self.assertFalse(by["KIN"].blocks[0]["keep"])
        self.assertFalse(by["Mars"].blocks[1]["keep"])

    def test_strike_becomes_a_time_span_on_that_track_only(self):
        tr, _c, _g = self._plan(
            "# x\n- [x] MR0001 [0:00–0:01] [Mars] 今天~~很好~~\n"
            "- [x] KN0001 [0:01–0:02] [KIN] 對啊\n"
            "- [ ] MR0002 [0:02–0:02] [Mars] （非詞彙出聲／待辨 0.4s）\n")
        by = {t.name: t for t in tr}
        (s, e), = by["Mars"].blocks[0]["strikes"]
        self.assertAlmostEqual(s, 0.5, delta=0.06)
        self.assertAlmostEqual(e, 1.0, delta=0.06)
        self.assertEqual(by["KIN"].blocks[0]["strikes"], [])

    def test_manual_cut_rows_are_collected(self):
        _t, cuts, _g = self._plan(
            "# x\n## ✂ 1.5-1.8 手動\n"
            "- [x] MR0001 [0:00–0:01] [Mars] 今天很好\n"
            "- [x] KN0001 [0:01–0:02] [KIN] 對啊\n"
            "- [ ] MR0002 [0:02–0:02] [Mars] （非詞彙出聲／待辨 0.4s）\n")
        self.assertEqual(cuts, [[1.5, 1.8]])


if __name__ == "__main__":
    unittest.main(verbosity=1)
