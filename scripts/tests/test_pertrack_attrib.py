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
    annotate_canonical, UNCERTAIN_REASON_TEXT,
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
        self.assertEqual(owner_runs(lv, HOP, 0.0, 1.0), [(0.0, 1.0, 0, None)])

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
        self.assertEqual(owner_runs(lv, HOP, 0.0, 1.0), [(0.0, 1.0, 0, None)])

    def test_block_shorter_than_stable_can_still_be_attributed(self):
        """比 `stable` 還短的 block 不該在數學上注定「歸屬不確定」。

        2026-08-11 MM 實聽 EP18 成品 0:49「有個遠方的 KIN 的嗯」。查:源
        53.10–53.20 只有 **0.10 秒**,而取得所有權要「領先 ≥3dB 持續 ≥0.2 秒」
        —— 0.1 秒的窗永遠湊不到 0.2 秒,於是退回混音 diarize 的標籤(Mars),
        開了 Mars 的麥、duck 掉 KIN 自己的麥,聽到的是 KIN 透過 Mars 的麥
        傳來的聲音。實測那 0.1 秒 KIN 領先 15dB,證據其實非常清楚。

        全片 218 個 <0.2s 的列有 51% 是這樣 —— 而「嗯」「對啊」這種附和正好
        全是短列,也正好是 diarize 在混音軌上最容易認錯的。
        """
        lv = const([-50.0, -35.0], 0.10)                # KIN(1) 全程領先 15dB
        runs = owner_runs(lv, HOP, 0.0, 0.10)
        self.assertEqual([o for _a, _b, o, _c in runs], [1],
                         "0.1s 的 block 領先 15dB 仍被判不確定 —— "
                         "穩定時長門檻必須隨 block 長度縮放")

    def test_short_block_with_thin_margin_is_still_uncertain(self):
        """縮放穩定門檻**不等於**放寬證據標準:差距不夠仍然是不確定。

        差距 2dB(<3dB margin)全程不變、沒有底噪門檻 → 原因碼
        below_margin(#728:從未達到 margin 的那 0.3%)。"""
        lv = const([-38.0, -36.0], 0.10)
        self.assertEqual(owner_runs(lv, HOP, 0.0, 0.10),
                         [(0.0, 0.10, None, "below_margin")])

    def test_thin_margin_is_left_uncertain_not_hard_picked(self):
        lv = const([0.0, -2.0])
        self.assertEqual(owner_runs(lv, HOP, 0.0, 1.0),
                         [(0.0, 1.0, None, "below_margin")])

    def test_floor_gated_cause_when_nobody_is_speaking(self):
        """#728:三軌全程都在各自底噪之下、領先幅度不到 #726 豁免門檻
        (10dB)→ 原因碼 floor_gated(#677 量到佔比最大的一類,59%)。"""
        floor = [-55.0, -55.0, -55.0]
        lv = const([-60.0, -62.0, -65.0], 1.0)          # 全程都在底噪,領先僅 2dB
        runs = owner_runs(lv, HOP, 0.0, 1.0, floor_db=floor)
        self.assertEqual([(r[2], r[3]) for r in runs], [(None, "floor_gated")])

    def test_unstable_cause_when_leader_flickers_before_stabilizing(self):
        """#728:領先幅度有達過 margin,但同一名候選人撐不滿穩定時長就被
        打斷(逐 frame 抖動)→ 原因碼 unstable(#677 量到 38%)。"""
        seg0 = const([-30.0, -35.0], 0.05)              # track0 領先 5dB
        seg1 = const([-35.0, -30.0], 0.05)              # track1 領先 5dB
        lv = np.hstack([seg0, seg1] * 10)               # 每 0.05s 換人,stable=0.2s 永遠湊不到
        runs = owner_runs(lv, HOP, 0.0, 1.0)
        self.assertEqual([(r[2], r[3]) for r in runs], [(None, "unstable")])

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

    def test_floor_gate_with_a_clear_relative_leader_still_attributes(self):
        """#726:三軌都低於底噪、但 leader 對第二名領先 ≥10dB → 跳過底噪閘。

        #677 診斷:880 筆「歸屬不確定」裡 520 筆(59%)是底噪閘擋掉 median
        領先 18.6dB 的清楚贏家 —— 跟 test_nobody_above_the_floor 的 4.7dB
        搶麥案例在數值上有 >5dB 的緩衝帶,不是同一種情境。這裡複刻同款三段
        結構,只把底噪段的 leader 領先幅度從 4.7dB 換成 18dB(Sarah −56.0 vs
        Mars −74.0,KIN −90.0 墊底),兩者都仍在各自 floor(−55dB)之下。
        """
        floor = [-55.0, -55.0, -55.0]
        lv = np.hstack([const([-30.0, -60.0, -60.0], 0.4),      # 0 明確在講
                        const([-74.0, -56.0, -90.0], 0.3)])     # 底噪但 Sarah 領先 18dB
        runs = owner_runs(lv, HOP, 0.0, 0.7, floor_db=floor)
        self.assertIn(1, [r[2] for r in runs],
                      "領先 18dB 的底噪區應該跳過底噪閘、正常判定歸屬")

    def test_floor_bypass_boundary_below_threshold_stays_gated(self):
        """邊界:lead=9.9dB(<10dB 門檻)不豁免,維持底噪閘不換手。"""
        floor = [-55.0, -55.0, -55.0]
        lv = np.hstack([const([-30.0, -60.0, -60.0], 0.4),
                        const([-65.9, -56.0, -90.0], 0.3)])     # lead = 9.9dB
        runs = owner_runs(lv, HOP, 0.0, 0.7, floor_db=floor)
        self.assertEqual([r[2] for r in runs], [0],
                         "9.9dB 領先未達門檻,不該豁免底噪閘")

    def test_floor_bypass_boundary_above_threshold_attributes(self):
        """邊界:lead=10.1dB(>10dB 門檻)豁免,正常判定歸屬。"""
        floor = [-55.0, -55.0, -55.0]
        lv = np.hstack([const([-30.0, -60.0, -60.0], 0.4),
                        const([-66.1, -56.0, -90.0], 0.3)])     # lead = 10.1dB
        runs = owner_runs(lv, HOP, 0.0, 0.7, floor_db=floor)
        self.assertIn(1, [r[2] for r in runs],
                      "10.1dB 領先已達門檻,應豁免底噪閘並判定歸屬")


@unittest.skipIf(np is None, "需要 numpy")
class TestSplitPhrase(unittest.TestCase):
    WORDS = [w(0.00, 0.20, "今"), w(0.20, 0.45, "天"), w(0.52, 0.75, "很"),
             w(0.75, 1.00, "好")]

    def test_handover_splits_at_the_nearest_word_boundary(self):
        out = split_phrase(self.WORDS, "今天很好",
                           [(0.0, 0.50, 0, None), (0.50, 1.0, 1, None)],
                           snap=0.25)
        self.assertEqual([(p["text"], p["owner"]) for p in out],
                         [("今天", 0), ("很好", 1)])
        self.assertAlmostEqual(out[0]["end"], 0.45, places=6)
        self.assertAlmostEqual(out[1]["start"], 0.52, places=6)

    def test_no_word_boundary_within_snap_means_no_split(self):
        out = split_phrase(self.WORDS, "今天很好",
                           [(0.0, 0.90, 0, None), (0.90, 1.0, 1, None)],
                           snap=0.10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["owner"], 0)          # 佔多數的那一位
        self.assertEqual(out[0]["text"], "今天很好")

    def test_uncertain_run_is_flagged_and_keeps_the_canonical_text(self):
        out = split_phrase(self.WORDS, "今天很好",
                           [(0.0, 1.0, None, "below_margin")], snap=0.25)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["owner"])
        self.assertTrue(out[0]["uncertain"])
        self.assertEqual(out[0]["text"], "今天很好")

    def test_reason_text_is_picked_by_the_cause_code(self):
        """#728:reason 文案按 owner_runs 回傳的 cause 分流,不再全部寫死
        同一句「三軌差距 <3dB」。"""
        for cause in ("floor_gated", "below_margin", "unstable"):
            with self.subTest(cause=cause):
                out = split_phrase(self.WORDS, "今天很好",
                                   [(0.0, 1.0, None, cause)], snap=0.25)
                self.assertEqual(out[0]["reason"], UNCERTAIN_REASON_TEXT[cause])
        # 三種文案彼此不同 —— 人審一眼要能分辨,不是換皮同一句
        self.assertEqual(len(set(UNCERTAIN_REASON_TEXT.values())), 3)

    def test_uncertain_reason_picks_the_cause_with_the_most_overlap(self):
        """一個 phrase 若跨了多個 owner=None 的 run,各自原因可能不同 ——
        取重疊時長最長的那個原因,跟「owner 本身佔多數的那一位」同一套邏輯。"""
        out = split_phrase(self.WORDS, "今天很好",
                           [(0.0, 0.30, None, "floor_gated"),
                            (0.30, 1.0, None, "unstable")], snap=0.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reason"], UNCERTAIN_REASON_TEXT["unstable"])

    def test_text_always_comes_from_the_canonical_words(self):
        """D1:逐軌 ASR 不可當出片文字 —— 這裡連個逐軌參數都沒有。"""
        out = split_phrase(self.WORDS, "今天很好", [(0.0, 1.0, 1, None)],
                           snap=0.25)
        self.assertEqual(out[0]["text"], "今天很好")

    def test_split_uses_the_canonical_slice_when_words_disagree(self):
        ws = annotate_canonical(
            [w(0.0, 0.2, "今"), w(0.2, 0.45, "天"), w(0.52, 0.75, "隻"),
             w(0.75, 1.0, "好")], "今天只好")
        out = split_phrase(ws, "今天只好",
                           [(0.0, 0.5, 0, None), (0.5, 1.0, 1, None)],
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
