#!/usr/bin/env python3
"""test_transcribe_llmnode.py — 遠端 ASR 分段規劃的行為鎖定測試。

分段不是效能優化:whisper 的 --prompt 只條件化第一個 30 秒窗口,之後每個窗口
拿前一段輸出當脈絡,長音檔一旦滑進「不標點模式」就自我延續到結束(2026-09-07
實測 55 講裡 21 講整份零標點)。切段＝每段重新套 prompt,漂移不累積。

所以 plan_segments 的四個不變量都是產物正確性,不是美觀問題:
    覆蓋 — 漏一段就是逐字稿少一段話
    不重疊 — 重疊就是同一句被轉錄兩次
    不超上限 — 超過就是那段又會漂回不標點模式
    無孤兒段 — 只有幾秒的尾段轉出來多半是雜訊

跑法:
    python3 scripts/tests/test_transcribe_llmnode.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from transcribe_llmnode import plan_segments  # noqa: E402

SEG, MAXSEG = 420.0, 480.0


class TestPlanSegments(unittest.TestCase):

    def assert_invariants(self, dur: float, segs: list[tuple[float, float]]):
        self.assertTrue(segs, f"{dur}s 沒有切出任何段")
        self.assertAlmostEqual(segs[0][0], 0.0, places=6, msg=f"{dur}s 沒從 0 開始")
        for (a, la), (b, _) in zip(segs, segs[1:]):
            self.assertAlmostEqual(a + la, b, places=6,
                                   msg=f"{dur}s 段落之間有縫或重疊")
        self.assertAlmostEqual(segs[-1][0] + segs[-1][1], dur, places=6,
                               msg=f"{dur}s 尾巴沒被覆蓋")
        for _, L in segs:
            self.assertLessEqual(L, MAXSEG + 1e-9, f"{dur}s 有段超過上限")
        if len(segs) > 1:
            # 對半切的下限 = maxseg/2;比這更短就是孤兒段
            self.assertGreaterEqual(min(L for _, L in segs), MAXSEG / 2 - 1e-9,
                                    f"{dur}s 切出孤兒段")

    def test_invariants_across_lengths(self):
        """0.1s–6000s 全掃:覆蓋、不重疊、不超上限、無孤兒段。"""
        for i in range(1, 60001):
            dur = i / 10
            self.assert_invariants(dur, plan_segments(dur, SEG, MAXSEG))

    def test_short_media_is_one_segment(self):
        """≤ 上限就整支轉,不要為了切而切(多一段就多一次 ssh + whisper 啟動)。"""
        for dur in (1.0, 60.0, 419.0, MAXSEG):
            self.assertEqual(plan_segments(dur, SEG, MAXSEG), [(0.0, dur)])

    def test_tail_merges_into_last_segment(self):
        """尾巴併進最後一段:900s 是 420+480,不是 420+420+60。"""
        self.assertEqual(plan_segments(900.0, SEG, MAXSEG),
                         [(0.0, 420.0), (420.0, 480.0)])

    def test_merged_tail_over_cap_is_halved(self):
        """併完超過上限就對半切:901s 的尾段 481s → 兩段 240.5s。"""
        segs = plan_segments(901.0, SEG, MAXSEG)
        self.assertEqual(len(segs), 3)
        self.assertAlmostEqual(segs[1][1], 240.5)
        self.assertAlmostEqual(segs[2][1], 240.5)

    def test_just_over_cap_splits_in_half(self):
        """剛好超過上限一點點:走對半切,不會產生 480+0.1 的孤兒段。"""
        segs = plan_segments(MAXSEG + 0.1, SEG, MAXSEG)
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0][1], segs[1][1])

    def test_exact_multiple_of_segment(self):
        """整除:840s = 兩段 420,rem=0 不該生出長度 0 的段。"""
        self.assertEqual(plan_segments(840.0, SEG, MAXSEG),
                         [(0.0, 420.0), (420.0, 420.0)])

    def test_long_media_keeps_invariants(self):
        """一小時的課程影片:段數合理且不變量成立。"""
        segs = plan_segments(3600.0, SEG, MAXSEG)
        self.assert_invariants(3600.0, segs)
        self.assertGreaterEqual(len(segs), 8)


if __name__ == "__main__":
    unittest.main(verbosity=1)
