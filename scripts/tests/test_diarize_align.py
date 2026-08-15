#!/usr/bin/env python3
"""test_diarize_align.py — diarize.py 零模型路徑的行為鎖定測試。

pyannote 模型呼叫不在測試範圍;鎖的是所有確定性邏輯:
relabel_turns(講最多的人=S1)、assign_speakers(max-overlap 貼標)、
word_speakers(零 overlap 繼承前字)、split_cues_by_turns(多人 30s 大段
換手切開,a57f38a,EP17 二航驗證 0 殘留)、align_from_tracks / apply_map e2e。

跑法:
    python3 scripts/tests/test_diarize_align.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from srt_utils import parse_srt  # noqa: E402
from diarize import (relabel_turns, assign_speakers, word_speakers,  # noqa: E402
                     split_cues_by_turns, align_from_tracks, apply_map)


def turn(start, end, speaker):
    return {"start": start, "end": end, "speaker": speaker}


def w(start, end, word):
    return {"start": start, "end": end, "word": word}


class TestRelabelTurns(unittest.TestCase):
    def test_most_talkative_becomes_s1(self):
        turns = [turn(0, 2, "SPEAKER_00"),      # 共 2s
                 turn(2, 8, "SPEAKER_01"),      # 共 6s → S1
                 turn(8, 9, "SPEAKER_00")]
        out = relabel_turns(turns)
        self.assertEqual([t["speaker"] for t in out], ["S2", "S1", "S2"])


class TestAssignSpeakers(unittest.TestCase):
    def test_max_overlap_wins(self):
        turns = [turn(0, 2, "S1"), turn(2, 10, "S2")]
        cues = [{"start": 1.5, "end": 4.0, "text": "跨兩個 turn", "idx": 1}]
        out = assign_speakers(cues, turns)
        self.assertEqual(out[0]["speaker"], "S2")  # overlap 2.0 > 0.5

    def test_zero_overlap_falls_back_to_nearest(self):
        turns = [turn(0, 1, "S1"), turn(10, 12, "S2")]
        cues = [{"start": 8.5, "end": 9.5, "text": "空窗", "idx": 1}]
        out = assign_speakers(cues, turns)
        self.assertEqual(out[0]["speaker"], "S2")  # 距 10 比距 1 近

    def test_no_turns_defaults_s1(self):
        cues = [{"start": 0.0, "end": 1.0, "text": "無 turn", "idx": 1}]
        out = assign_speakers(cues, [])
        self.assertEqual(out[0]["speaker"], "S1")


class TestWordSpeakers(unittest.TestCase):
    def test_zero_overlap_inherits_previous(self):
        # 0 長度 artifact word 零 overlap → 繼承前一個 word 的 speaker
        turns = [turn(0, 1, "S1"), turn(1, 2, "S2")]
        words = [w(0.2, 0.8, "一"), w(0.9, 0.9, "。"), w(1.2, 1.8, "二")]
        self.assertEqual(word_speakers(words, turns), ["S1", "S1", "S2"])

    def test_leading_orphan_gets_nearest_turn(self):
        turns = [turn(5, 6, "S1")]
        words = [w(0.0, 0.5, "開頭孤兒")]
        self.assertEqual(word_speakers(words, turns), ["S1"])


class TestSplitCuesByTurns(unittest.TestCase):
    def test_single_speaker_cue_untouched(self):
        turns = [turn(0, 4, "S1")]
        cues = [{"idx": 1, "start": 0.0, "end": 4.0, "text": "原文照舊",
                 "speaker": None}]
        words = [w(0.5, 1.5, "原文"), w(1.5, 3.5, "照舊")]
        out, n_split = split_cues_by_turns(cues, turns, words)
        self.assertEqual(n_split, 0)
        self.assertEqual(out[0]["text"], "原文照舊")   # 零改動
        self.assertEqual(out[0]["speaker"], "S1")

    def test_multi_speaker_cue_split_at_handoff(self):
        # whisper 30s 視窗大段:一 cue 兩人 → 在換手處切開
        turns = [turn(0, 2, "Sarah"), turn(2, 4, "Mars")]
        cues = [{"idx": 1, "start": 0.0, "end": 4.0,
                 "text": "我是Sarah大家好我是Mars", "speaker": None}]
        words = [w(0.0, 0.5, "我是"), w(0.5, 1.9, "Sarah"),
                 w(2.1, 2.6, "大家好"), w(2.6, 3.0, "我是"), w(3.0, 3.5, "Mars")]
        out, n_split = split_cues_by_turns(cues, turns, words)
        self.assertEqual(n_split, 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "我是Sarah")
        self.assertEqual(out[0]["speaker"], "Sarah")
        self.assertEqual(out[1]["text"], "大家好我是Mars")
        self.assertEqual(out[1]["speaker"], "Mars")
        # 首尾沿用原 cue 邊界;切點用 word 時間
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 1.9)
        self.assertEqual(out[1]["start"], 2.1)
        self.assertEqual(out[1]["end"], 4.0)
        self.assertEqual([c["idx"] for c in out], [1, 2])

    def test_english_word_junction_merged_back(self):
        # whisper cue 邊界切在英文字中間("…嗯。A" / "ra呢?"):
        # 同 speaker、零間隔、兩側英數字 → 併回一個 cue
        turns = [turn(0, 2, "S1")]
        cues = [{"idx": 1, "start": 0.0, "end": 1.0, "text": "嗯。A", "speaker": None},
                {"idx": 2, "start": 1.0, "end": 1.5, "text": "ra呢?", "speaker": None}]
        words = [w(0.0, 0.5, "嗯。"), w(0.5, 1.0, "A"), w(1.0, 1.5, "ra呢?")]
        out, n_split = split_cues_by_turns(cues, turns, words)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "嗯。Ara呢?")
        self.assertEqual(out[0]["end"], 1.5)

    def test_chinese_junction_not_merged(self):
        turns = [turn(0, 2, "S1")]
        cues = [{"idx": 1, "start": 0.0, "end": 1.0, "text": "第一句", "speaker": None},
                {"idx": 2, "start": 1.0, "end": 2.0, "text": "第二句", "speaker": None}]
        words = [w(0.0, 0.9, "第一句"), w(1.0, 1.9, "第二句")]
        out, _ = split_cues_by_turns(cues, turns, words)
        self.assertEqual(len(out), 2)


class TestAlignFromTracksE2E(unittest.TestCase):
    def _make_session(self, td: str, with_words: bool) -> Path:
        sdir = Path(td) / "ep-test"
        sdir.mkdir()
        (sdir / "speakers.json").write_text(json.dumps({
            "turns": [turn(0, 2, "Sarah"), turn(2, 4, "Mars")],
        }, ensure_ascii=False), encoding="utf-8")
        (sdir / "transcript.srt").write_text(
            "1\n00:00:00,000 --> 00:00:04,000\n我是Sarah大家好我是Mars\n",
            encoding="utf-8")
        if with_words:
            (sdir / "words.json").write_text(json.dumps([
                w(0.0, 0.5, "我是"), w(0.5, 1.9, "Sarah"),
                w(2.1, 2.6, "大家好"), w(2.6, 3.0, "我是"), w(3.0, 3.5, "Mars"),
            ], ensure_ascii=False), encoding="utf-8")
        return sdir

    def test_with_words_splits_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td, with_words=True)
            align_from_tracks(sdir)
            cues = parse_srt(sdir / "transcript.speakers.srt")
        self.assertEqual([(c["speaker"], c["text"]) for c in cues],
                         [("Sarah", "我是Sarah"), ("Mars", "大家好我是Mars")])

    def test_without_words_falls_back_whole_cue(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td, with_words=False)
            align_from_tracks(sdir)
            cues = parse_srt(sdir / "transcript.speakers.srt")
        self.assertEqual(len(cues), 1)   # 退回逐段貼標,不切
        # overlap 打平(各 2.0s)時先到的 turn 贏 — 鎖住 tie-break 行為
        self.assertEqual(cues[0]["speaker"], "Sarah")

    def test_missing_speakers_json_exits(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "ep-test"
            sdir.mkdir()
            with self.assertRaises(SystemExit):
                align_from_tracks(sdir)


class TestApplyMapE2E(unittest.TestCase):
    def test_names_applied_marker_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "ep-test"
            sdir.mkdir()
            (sdir / "transcript.speakers.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n[S1] 哈囉\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\n[S2] 嗨\n", encoding="utf-8")
            (sdir / "speakers_map.json").write_text(
                json.dumps({"S1": "Sarah", "S2": "S2"}), encoding="utf-8")
            (sdir / "speakers.json").write_text(json.dumps({
                "turns": [turn(0, 1, "S1"), turn(1, 2, "S2")],
            }), encoding="utf-8")
            marker = sdir / ".speaker_naming_pending.json"
            marker.write_text("{}", encoding="utf-8")

            apply_map(sdir)

            cues = parse_srt(sdir / "transcript.speakers.srt")
            self.assertEqual([c["speaker"] for c in cues], ["Sarah", "S2"])
            # speakers.json 同步換名,下游看到同一套標籤
            sj = json.loads((sdir / "speakers.json").read_text(encoding="utf-8"))
            self.assertEqual([t["speaker"] for t in sj["turns"]], ["Sarah", "S2"])
            self.assertEqual(sj["speakers_map"], {"S1": "Sarah", "S2": "S2"})
            self.assertFalse(marker.exists())

    def test_missing_map_exits(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "ep-test"
            sdir.mkdir()
            with self.assertRaises(SystemExit):
                apply_map(sdir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
