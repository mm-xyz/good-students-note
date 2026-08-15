#!/usr/bin/env python3
"""test_prosody.py — prosody.py 確定性部分的行為鎖定測試。

librosa 特徵抽取(extract_features)不在測試範圍(重依賴,.venv-audio);
鎖的是:chinese_chars、zscore_by_speaker(z-score 分軌正規化+excitement 映射,
需 numpy,沒裝就 skip)、write_highlights(前 N% + 分數門檻)。

跑法:
    python3 scripts/tests/test_prosody.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from prosody import chinese_chars, write_highlights  # noqa: E402

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class TestChineseChars(unittest.TestCase):
    def test_counts_cjk_only(self):
        self.assertEqual(chinese_chars("你好abc123嗎?"), 3)
        self.assertEqual(chinese_chars("english only"), 0)


@unittest.skipUnless(HAS_NUMPY, "numpy not installed")
class TestZscoreBySpeaker(unittest.TestCase):
    @staticmethod
    def _cue(spk, rms, f0, f0r, rate):
        return {"speaker": spk, "feat": {"rms_db": rms, "f0_p75": f0,
                                         "f0_range": f0r, "rate": rate}}

    def test_symmetric_pair_maps_to_68_32(self):
        from prosody import zscore_by_speaker
        # 兩段對稱資料:z=±1,combined=±1 → excitement = 50±18
        cues = [self._cue("S1", -20.0, 200.0, 50.0, 5.0),
                self._cue("S1", -10.0, 300.0, 100.0, 7.0)]
        stats = zscore_by_speaker(cues)
        self.assertEqual(cues[0]["excitement"], 32.0)
        self.assertEqual(cues[1]["excitement"], 68.0)
        self.assertEqual(stats["S1"]["segments"], 2)
        self.assertEqual(stats["S1"]["rms_db"]["mean"], -15.0)

    def test_normalized_per_speaker(self):
        from prosody import zscore_by_speaker
        # 「高昂」是相對自己的平常狀態:各 speaker 分開正規化,
        # 天生大聲的人不會壓過小聲的人
        loud = [self._cue("大聲公", -6.0, 250.0, 60.0, 6.0),
                self._cue("大聲公", -4.0, 260.0, 62.0, 6.2)]
        soft = [self._cue("小聲者", -30.0, 180.0, 40.0, 4.0),
                self._cue("小聲者", -28.0, 190.0, 42.0, 4.2)]
        zscore_by_speaker(loud + soft)
        self.assertEqual(loud[1]["excitement"], soft[1]["excitement"])

    def test_nan_feature_zero_z(self):
        from prosody import zscore_by_speaker
        # 無聲段 f0 是 NaN:z 記 0,不汙染分數
        cues = [self._cue("S1", -20.0, float("nan"), float("nan"), 5.0),
                self._cue("S1", -10.0, float("nan"), float("nan"), 7.0)]
        zscore_by_speaker(cues)
        self.assertEqual(cues[0]["z"]["f0_p75"], 0.0)
        self.assertEqual(cues[1]["excitement"], 59.9)  # 只剩 rms+rate 的 ±0.55


class TestWriteHighlights(unittest.TestCase):
    @staticmethod
    def _cue(start, score, text="句子"):
        return {"start": start, "end": start + 1.0, "text": text,
                "speaker": "S1", "excitement": score}

    def _write(self, cues, top_percent):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "highlights.md"
            n = write_highlights(cues, p, top_percent)
            return n, p.read_text(encoding="utf-8")

    def test_top_n_sorted_by_start(self):
        cues = [self._cue(30, 80, "乙"), self._cue(10, 65, "甲"),
                self._cue(50, 90, "丙"), self._cue(70, 30), self._cue(90, 10)]
        n, md = self._write(cues, top_percent=40)
        self.assertEqual(n, 3)   # n = max(3, 40% of 5) = 3,全過 60 分門檻
        # 輸出按時間排序,不是按分數
        self.assertLess(md.index("甲"), md.index("乙"))
        self.assertLess(md.index("乙"), md.index("丙"))
        self.assertIn("(score 90)", md)
        self.assertIn("[0:10–0:11]", md)

    def test_min_score_gate(self):
        cues = [self._cue(10, 80), self._cue(20, 55), self._cue(30, 50),
                self._cue(40, 40), self._cue(50, 30)]
        n, _ = self._write(cues, top_percent=60)
        self.assertEqual(n, 1)   # 前 3 名裡只有 80 過 min_score=60


if __name__ == "__main__":
    unittest.main(verbosity=2)
