#!/usr/bin/env python3
"""test_srt_utils.py — srt_utils 的行為鎖定測試(characterization)。

srt_utils 是音訊管線所有 stage 的地基(transcribe/diarize/cutplan/render 都吃它),
這裡把 2026-07-29 phrase-level 改版後的既定行為鎖住:時間碼互轉、SRT 解析/輸出
roundtrip、join_words 的英數補空格規則、split_words_to_phrases 的標點/停頓切點
與零長度 artifact 併組(EP17 首跑實踩的坑,見設計文件 §5)。

跑法:
    python3 scripts/tests/test_srt_utils.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from srt_utils import (ts_to_sec, sec_to_ts, fmt_mmss, parse_srt, write_srt,  # noqa: E402
                       join_words, split_words_to_phrases)


class TestTimestamps(unittest.TestCase):
    def test_ts_to_sec_basic(self):
        self.assertEqual(ts_to_sec("00:01:02,500"), 62.5)
        self.assertEqual(ts_to_sec("01:00:00,000"), 3600.0)

    def test_ts_to_sec_dot_separator(self):
        # 有些 SRT 用 . 當毫秒分隔
        self.assertEqual(ts_to_sec("00:00:01.250"), 1.25)

    def test_ts_to_sec_short_ms_padded(self):
        # 毫秒不足三位往右補零:,5 = 500ms
        self.assertEqual(ts_to_sec("00:00:01,5"), 1.5)

    def test_ts_to_sec_bad_raises(self):
        with self.assertRaises(ValueError):
            ts_to_sec("not a timestamp")

    def test_sec_to_ts_basic(self):
        self.assertEqual(sec_to_ts(62.5), "00:01:02,500")

    def test_sec_to_ts_negative_clamps_to_zero(self):
        self.assertEqual(sec_to_ts(-3.0), "00:00:00,000")

    def test_sec_to_ts_rounding_carry(self):
        # 999.5ms 四捨五入到 1000 → 進位到下一秒,不能輸出 ,1000
        self.assertEqual(sec_to_ts(1.9995), "00:00:02,000")

    def test_roundtrip(self):
        for sec in (0.0, 0.001, 59.999, 61.5, 3661.042):
            self.assertAlmostEqual(ts_to_sec(sec_to_ts(sec)), sec, places=3)

    def test_fmt_mmss(self):
        self.assertEqual(fmt_mmss(65), "1:05")
        self.assertEqual(fmt_mmss(0), "0:00")
        self.assertEqual(fmt_mmss(3661), "1:01:01")


class TestParseWriteSrt(unittest.TestCase):
    def _parse(self, content: str) -> list[dict]:
        with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False,
                                         encoding="utf-8") as f:
            f.write(content)
            p = Path(f.name)
        try:
            return parse_srt(p)
        finally:
            p.unlink()

    def test_basic_two_cues(self):
        cues = self._parse(
            "1\n00:00:00,000 --> 00:00:01,500\n你好\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n世界\n")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0], {"idx": 1, "start": 0.0, "end": 1.5,
                                   "text": "你好", "speaker": None})
        self.assertEqual(cues[1]["start"], 2.0)

    def test_speaker_prefix_extracted(self):
        cues = self._parse("1\n00:00:00,000 --> 00:00:01,000\n[Sarah] 嗨大家好\n")
        self.assertEqual(cues[0]["speaker"], "Sarah")
        self.assertEqual(cues[0]["text"], "嗨大家好")

    def test_missing_index_line_tolerated(self):
        # 容錯:沒有 index 行的 block 也要吃得下
        cues = self._parse("00:00:00,000 --> 00:00:01,000\n沒編號的句子\n")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "沒編號的句子")

    def test_garbage_block_skipped(self):
        cues = self._parse("這不是 SRT block\n\n"
                           "1\n00:00:00,000 --> 00:00:01,000\n正常句\n")
        self.assertEqual(len(cues), 1)

    def test_idx_renumbered_sequentially(self):
        # 原檔編號亂跳,parse 後 idx 一律照序重編
        cues = self._parse(
            "7\n00:00:00,000 --> 00:00:01,000\nA\n\n"
            "99\n00:00:02,000 --> 00:00:03,000\nB\n")
        self.assertEqual([c["idx"] for c in cues], [1, 2])

    def test_multiline_text_joined(self):
        cues = self._parse("1\n00:00:00,000 --> 00:00:01,000\n第一行\n第二行\n")
        self.assertEqual(cues[0]["text"], "第一行\n第二行")

    def test_write_read_roundtrip_with_speaker(self):
        cues = [{"start": 0.0, "end": 1.5, "text": "你好", "speaker": "S1"},
                {"start": 2.0, "end": 3.25, "text": "hello world", "speaker": None}]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out.srt"
            write_srt(cues, p)
            back = parse_srt(p)
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0]["speaker"], "S1")
        self.assertEqual(back[0]["text"], "你好")
        self.assertEqual(back[1]["speaker"], None)
        self.assertEqual(back[1]["text"], "hello world")
        self.assertAlmostEqual(back[1]["end"], 3.25, places=3)


class TestJoinWords(unittest.TestCase):
    def test_chinese_no_space(self):
        ws = [{"word": "你好"}, {"word": "世界"}]
        self.assertEqual(join_words(ws, "你好世界"), "你好世界")

    def test_english_space_from_ref(self):
        # 原文有 "hello world" 才補空格
        ws = [{"word": "hello"}, {"word": "world"}]
        self.assertEqual(join_words(ws, "hello world"), "hello world")

    def test_split_english_word_not_spaced(self):
        # whisper 把單字拆半:"M"+"ars" 不能補成 "M ars"
        ws = [{"word": "M"}, {"word": "ars"}]
        self.assertEqual(join_words(ws, "Mars"), "Mars")

    def test_mixed_chinese_english(self):
        ws = [{"word": "我是"}, {"word": "Sarah"}]
        self.assertEqual(join_words(ws, "我是Sarah"), "我是Sarah")


def w(start, end, word):
    return {"start": start, "end": end, "word": word}


class TestSplitWordsToPhrases(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(split_words_to_phrases([], ""), [])

    def test_punctuation_cut(self):
        ws = [w(0.0, 0.5, "你打扮一下,"), w(0.5, 1.0, "好,"), w(1.0, 1.5, "謝了。"),
              w(1.5, 2.0, "再來一次")]
        ph = split_words_to_phrases(ws, "你打扮一下,好,謝了。再來一次")
        self.assertEqual([p["text"] for p in ph],
                         ["你打扮一下,", "好,", "謝了。", "再來一次"])
        self.assertEqual(ph[0]["start"], 0.0)
        self.assertEqual(ph[0]["end"], 0.5)
        self.assertEqual(ph[-1]["start"], 1.5)

    def test_gap_cut(self):
        # 無標點,但字間停頓 ≥ 0.5s 也要斷
        ws = [w(0.0, 0.5, "前半"), w(1.2, 1.7, "後半")]
        ph = split_words_to_phrases(ws, "前半後半")
        self.assertEqual([p["text"] for p in ph], ["前半", "後半"])

    def test_gap_below_threshold_no_cut(self):
        ws = [w(0.0, 0.5, "前半"), w(0.8, 1.3, "後半")]
        ph = split_words_to_phrases(ws, "前半後半")
        self.assertEqual([p["text"] for p in ph], ["前半後半"])

    def test_custom_gap_threshold(self):
        ws = [w(0.0, 0.5, "前半"), w(0.8, 1.3, "後半")]
        ph = split_words_to_phrases(ws, "前半後半", gap=0.2)
        self.assertEqual(len(ph), 2)

    def test_trailing_punctuation_no_empty_group(self):
        # 最後一個字帶標點,不能多出空 phrase
        ws = [w(0.0, 0.5, "結尾。")]
        ph = split_words_to_phrases(ws, "結尾。")
        self.assertEqual(len(ph), 1)

    def test_halfwidth_punctuation_cuts(self):
        ws = [w(0.0, 0.5, "ok,"), w(0.5, 1.0, "next")]
        ph = split_words_to_phrases(ws, "ok,next")
        self.assertEqual(len(ph), 2)

    def test_zero_length_group_merged_into_previous(self):
        # whisper 偶發 start==end 的 artifact word(EP17 首跑實踩):
        # 全零長度的 group 不能自成 0 長度短句,要併回前一組
        ws = [w(0.0, 1.0, "好。"), w(1.0, 1.0, "哈囉。"), w(1.5, 2.0, "繼續")]
        ph = split_words_to_phrases(ws, "好。哈囉。繼續")
        self.assertEqual([p["text"] for p in ph], ["好。哈囉。", "繼續"])
        self.assertEqual(ph[0]["start"], 0.0)
        self.assertEqual(ph[0]["end"], 1.0)

    def test_leading_zero_length_group_merged_into_next(self):
        ws = [w(0.0, 0.0, "哈。"), w(0.0, 1.0, "正文")]
        ph = split_words_to_phrases(ws, "哈。正文")
        self.assertEqual(len(ph), 1)
        self.assertEqual(ph[0]["text"], "哈。正文")
        self.assertEqual(ph[0]["end"], 1.0)

    def test_all_zero_length_single_group(self):
        # 整個 cue 只有零長度字:退化成單一 phrase,不炸
        ws = [w(1.0, 1.0, "哈。")]
        ph = split_words_to_phrases(ws, "哈。")
        self.assertEqual(len(ph), 1)

    def test_text_rebuilt_via_ref(self):
        # 英文空格由原 cue 文字決定(與 join_words 同源)
        ws = [w(0.0, 0.4, "I"), w(0.4, 0.8, "see。"), w(0.8, 1.2, "好")]
        ph = split_words_to_phrases(ws, "I see。好")
        self.assertEqual([p["text"] for p in ph], ["I see。", "好"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
