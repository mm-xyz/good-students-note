#!/usr/bin/env python3
"""test_pertrack_attrib.py — 分軌文字歸屬(D2)與非詞彙出聲偵測(D3)的訊號邏輯。

不碰音檔:所有函式都吃「已經算好的 frame 能量」,所以可以用合成資料把
支配判定、hysteresis、字界對齊、串音線性功率相加、CFAR 門檻、gap closing
全部鎖住。

跑法:
    python3 scripts/tests/test_pertrack_attrib.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

try:
    import numpy as np
except ImportError:                                    # pragma: no cover
    np = None

from pertrack_attrib import (  # noqa: E402
    calibrate_bleed, predict_bleed_db, owner_runs, split_phrase,
    cfar_percentile, find_events, integrate, drop_self_adjacent,
    annotate_canonical,
)

HOP = 0.01


def const(levels, secs=1.0):
    """levels=[每軌 dB],回傳 (n_tracks, n_frames) 的固定電平陣列。"""
    n = int(round(secs / HOP))
    return np.array([[x] * n for x in levels], dtype=float)


def w(a, b, text):
    return {"start": a, "end": b, "word": text}


@unittest.skipIf(np is None, "需要 numpy")
class TestIntegrate(unittest.TestCase):
    def test_moving_average_smooths_over_the_window(self):
        p = np.array([0.0, 0.0, 1.0, 0.0, 0.0])
        out = integrate(p, 3)
        self.assertAlmostEqual(out[2], 1 / 3, places=6)
        self.assertEqual(len(out), len(p))


@unittest.skipIf(np is None, "需要 numpy")
class TestBleedCalibration(unittest.TestCase):
    def test_recovers_the_planted_bleed_gain(self):
        # track0 講話(0dB / 靜音 -80dB),track1 只收到 -20dB 的串音
        speech = [0.0, -80.0] * 60
        lv = np.array([speech, [x - 20.0 for x in speech]], dtype=float)
        g = calibrate_bleed(lv, dominance_db=12.0, pct=30.0)
        self.assertAlmostEqual(g[1][0], -20.0, delta=0.5)

    def test_falls_back_when_there_are_too_few_samples(self):
        lv = np.array([[0.0] * 5, [0.0] * 5], dtype=float)
        g = calibrate_bleed(lv, dominance_db=12.0, pct=30.0, min_samples=20)
        self.assertEqual(g[1][0], -12.0)


@unittest.skipIf(np is None, "需要 numpy")
class TestBleedPrediction(unittest.TestCase):
    def test_two_equal_bleeders_add_in_power_not_max(self):
        """D3:`P_bleed = Σ_j P_j × g[i][j] + P_noise`,不是 max_j。

        兩個各自貢獻 -20dB 的來源,功率相加要比 max 多 3dB。
        """
        lv = np.array([[-100.0], [-20.0], [-20.0]], dtype=float)
        g = [[0.0, 0.0, 0.0], [0, 0, 0], [0, 0, 0]]
        g[0][1] = g[0][2] = 0.0        # 串音增益 0dB,方便看純相加
        pred = predict_bleed_db(lv, g, noise_db=[-200.0, -200.0, -200.0])
        self.assertAlmostEqual(pred[0][0], -20.0 + 3.0103, places=3)

    def test_noise_floor_is_included_in_the_prediction(self):
        lv = np.array([[-100.0], [-200.0]], dtype=float)
        g = [[0.0, -20.0], [-20.0, 0.0]]
        pred = predict_bleed_db(lv, g, noise_db=[-60.0, -60.0])
        self.assertAlmostEqual(pred[0][0], -60.0, places=2)


@unittest.skipIf(np is None, "需要 numpy")
class TestOwnerRuns(unittest.TestCase):
    def test_clear_dominance_gives_one_run(self):
        lv = const([0.0, -20.0])
        self.assertEqual(owner_runs(lv, HOP, 0.0, 1.0), [(0.0, 1.0, 0)])

    def test_handover_makes_two_runs_at_the_switch_point(self):
        lv = np.hstack([const([0.0, -20.0], 0.5), const([-20.0, 0.0], 0.5)])
        runs = owner_runs(lv, HOP, 0.0, 1.0)
        self.assertEqual(len(runs), 2)
        self.assertEqual([r[2] for r in runs], [0, 1])
        self.assertAlmostEqual(runs[0][1], 0.5, places=2)

    def test_short_challenger_does_not_flip_the_owner(self):
        """hysteresis:挑戰者連續領先不到 switch 秒就不換手(逐 frame 抖動)。"""
        lv = np.hstack([const([0.0, -20.0], 0.40), const([-20.0, 0.0], 0.10),
                        const([0.0, -20.0], 0.50)])
        self.assertEqual(owner_runs(lv, HOP, 0.0, 1.0), [(0.0, 1.0, 0)])

    def test_thin_margin_is_left_uncertain_not_hard_picked(self):
        lv = const([0.0, -2.0])
        self.assertEqual(owner_runs(lv, HOP, 0.0, 1.0), [(0.0, 1.0, None)])

    def test_nobody_above_the_floor_means_no_handover(self):
        """三軌都在噪聲底時不換手 —— 2026-08-11 MM 實聽抓到的破口。

        EP16 源 38.5–39.0s(「前陣子」的「前"):Mars −69.4／Sarah −62.9／
        KIN −67.6dB,三軌都是底噪,沒有人真的在出聲。但 Sarah 只因為底噪高
        4.7dB 就搶走所有權,KIN 的麥在詞中間被關掉 0.5 秒 —— 聽到的是 KIN
        的聲音透過 Sarah 的麥傳過來(距離感全變),而且切在「前陣子」中間。
        """
        floor = [-55.0, -55.0, -55.0]
        lv = np.hstack([const([-30.0, -60.0, -60.0], 0.4),      # 0 明確在講
                        const([-69.4, -62.9, -67.6], 0.3),      # 三軌都在底噪
                        const([-30.0, -60.0, -60.0], 0.3)])
        runs = owner_runs(lv, HOP, 0.0, 1.0, floor_db=floor)
        self.assertEqual([r[2] for r in runs], [0], "底噪區不該換手")

    def test_a_real_speaker_above_the_floor_still_takes_over(self):
        """門檻只擋底噪,不能擋掉真的換人講話。"""
        floor = [-55.0, -55.0, -55.0]
        lv = np.hstack([const([-30.0, -60.0, -60.0], 0.4),
                        const([-60.0, -30.0, -60.0], 0.6)])
        runs = owner_runs(lv, HOP, 0.0, 1.0, floor_db=floor)
        self.assertEqual([r[2] for r in runs], [0, 1])

    def test_margin_is_measured_against_the_incumbent_not_second_place(self):
        # 三軌:owner=0;挑戰者 1 只比 0 高 2dB(不足 3dB)雖然比 2 高很多
        lv = np.hstack([const([0.0, -20.0, -40.0], 0.4),
                        const([-2.0, 0.0, -40.0], 0.6)])
        self.assertEqual([r[2] for r in owner_runs(lv, HOP, 0.0, 1.0)], [0])


@unittest.skipIf(np is None, "需要 numpy")
class TestSplitPhrase(unittest.TestCase):
    WORDS = [w(0.00, 0.20, "今"), w(0.20, 0.45, "天"), w(0.52, 0.75, "很"),
             w(0.75, 1.00, "好")]

    def test_handover_splits_at_the_nearest_word_boundary(self):
        out = split_phrase(self.WORDS, "今天很好",
                           [(0.0, 0.50, 0), (0.50, 1.0, 1)], snap=0.25)
        self.assertEqual([(p["text"], p["owner"]) for p in out],
                         [("今天", 0), ("很好", 1)])
        self.assertAlmostEqual(out[0]["end"], 0.45, places=6)
        self.assertAlmostEqual(out[1]["start"], 0.52, places=6)

    def test_no_word_boundary_within_snap_means_no_split(self):
        out = split_phrase(self.WORDS, "今天很好",
                           [(0.0, 0.90, 0), (0.90, 1.0, 1)], snap=0.10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["owner"], 0)          # 佔多數的那一位
        self.assertEqual(out[0]["text"], "今天很好")

    def test_uncertain_run_is_flagged_and_keeps_the_canonical_text(self):
        out = split_phrase(self.WORDS, "今天很好", [(0.0, 1.0, None)], snap=0.25)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["owner"])
        self.assertTrue(out[0]["uncertain"])
        self.assertEqual(out[0]["text"], "今天很好")

    def test_text_always_comes_from_the_canonical_words(self):
        """D1:逐軌 ASR 不可當出片文字 —— 這裡連個逐軌參數都沒有。"""
        out = split_phrase(self.WORDS, "今天很好", [(0.0, 1.0, 1)], snap=0.25)
        self.assertEqual(out[0]["text"], "今天很好")

    def test_split_uses_the_canonical_slice_when_words_disagree(self):
        ws = annotate_canonical(
            [w(0.0, 0.2, "今"), w(0.2, 0.45, "天"), w(0.52, 0.75, "隻"),
             w(0.75, 1.0, "好")], "今天只好")
        out = split_phrase(ws, "今天只好", [(0.0, 0.5, 0), (0.5, 1.0, 1)],
                           snap=0.25)
        self.assertEqual([p["text"] for p in out], ["今天", "只好"])


@unittest.skipIf(np is None, "需要 numpy")
class TestCfarAndEvents(unittest.TestCase):
    def test_percentile_picks_the_upper_tail(self):
        xs = list(range(1000))
        self.assertAlmostEqual(cfar_percentile(xs, 99.5), 994.5, delta=1.0)

    def test_empty_sample_falls_back_to_the_given_default(self):
        self.assertEqual(cfar_percentile([], 99.5, default=7.0), 7.0)

    def test_gap_closing_joins_events_across_a_short_hole(self):
        hits = [True] * 20 + [False] * 5 + [True] * 20      # 0.2 / 0.05 / 0.2
        ev = find_events(hits, HOP, gap_close=0.08, min_dur=0.12)
        self.assertEqual(len(ev), 1)
        self.assertAlmostEqual(ev[0][1] - ev[0][0], 0.45, places=6)

    def test_long_gap_is_not_closed(self):
        hits = [True] * 20 + [False] * 20 + [True] * 20
        self.assertEqual(len(find_events(hits, HOP, 0.08, 0.12)), 2)

    def test_events_shorter_than_min_duration_are_dropped(self):
        hits = [True] * 8 + [False] * 40 + [True] * 20      # 0.08s / 0.2s
        ev = find_events(hits, HOP, gap_close=0.08, min_dur=0.12)
        self.assertEqual(len(ev), 1)
        self.assertAlmostEqual(ev[0][0], 0.48, places=6)


class TestAnnotateCanonical(unittest.TestCase):
    """D1:正式文字＝canonical block 文字,不是 words.json 重建的字。

    EP16 實踩:B0085 的 SRT 是人工校過的「只要」,words.json 還是 whisper 原本的
    「隻要」。用 words 重建 phrase 文字 → render 的防幻覺驗證直接 FAIL
    (KN0213「過但是只要交」不存在於來源 SRT)。
    """

    def test_words_are_mapped_onto_the_canonical_characters(self):
        ws = [w(0, 1, "無論"), w(1, 2, "隻要"), w(2, 3, "交")]
        out = annotate_canonical(ws, "無論只要交")
        self.assertEqual([x["ctext"] for x in out], ["無論", "只要", "交"])

    def test_concatenation_always_reproduces_the_canonical_text(self):
        ws = [w(0, 1, "今天"), w(1, 2, "天氣"), w(2, 3, "好")]
        out = annotate_canonical(ws, "今天 天氣 很好")
        self.assertEqual("".join(x["ctext"] for x in out), "今天天氣很好")

    def test_extra_canonical_tail_is_absorbed_not_dropped(self):
        ws = [w(0, 1, "今天")]
        out = annotate_canonical(ws, "今天很好啊")
        self.assertEqual(out[0]["ctext"], "今天很好啊")

    def test_original_word_field_is_left_alone(self):
        ws = [w(0, 1, "隻要")]
        out = annotate_canonical(ws, "只要")
        self.assertEqual(out[0]["word"], "隻要")
        self.assertEqual(out[0]["ctext"], "只要")


@unittest.skipIf(np is None, "需要 numpy")
class TestSelfAdjacentGuard(unittest.TestCase):
    """緊貼自己台詞的出聲不是「壓在別人話底下的附和」,是自己的字頭/換氣。

    預設不勾 = 靜音,所以留著會把自己的字頭削掉 —— EP16 實測 5:10 的 MR0109
    就落在自己下一句 310.84 開講前 0.01 秒。
    """

    OWN = [(310.84, 311.88), (320.0, 321.0)]

    def test_event_abutting_own_speech_is_dropped(self):
        ev = [{"start": 310.67, "end": 310.83}]
        self.assertEqual(drop_self_adjacent(ev, self.OWN, guard=0.25), [])

    def test_event_far_from_own_speech_survives(self):
        ev = [{"start": 314.0, "end": 314.6}]
        self.assertEqual(len(drop_self_adjacent(ev, self.OWN, guard=0.25)), 1)

    def test_guard_applies_on_both_sides(self):
        ev = [{"start": 312.0, "end": 312.2}]        # 自己 311.88 剛講完
        self.assertEqual(drop_self_adjacent(ev, self.OWN, guard=0.25), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
