#!/usr/bin/env python3
"""test_resegment_migrate.py — phrase-level 改版兩支工具的行為鎖定測試。

resegment_srt.py:既有 session 的 transcript.srt 事後補切短句(零模型),
鎖住:短句重切、首尾沿用原 cue 邊界、備份不覆蓋、speaker 傳承、mismatch 回報。

migrate_marks.py:cutplan 重生成後搬 Gemma ~~刪除線~~(字元流 difflib 對齊),
鎖住:同 block 移植、跨 block 拆 span、對不上整段丟棄(寧缺勿錯)、
註記行插入、新檔已有刪除線 abort。

設計文件:docs/design/2026-07-29_phrase-level-cutplan-spec.md
跑法:
    python3 scripts/tests/test_resegment_migrate.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
REPO_ROOT = AUDIO_DIR.parent.parent
sys.path.insert(0, str(AUDIO_DIR))

from srt_utils import parse_srt  # noqa: E402
from migrate_marks import parse_blocks, map_spans  # noqa: E402


def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(AUDIO_DIR / name), *args],
                          capture_output=True, text=True, cwd=REPO_ROOT)


class TestResegmentE2E(unittest.TestCase):
    def _make_session(self, td: str) -> Path:
        sdir = Path(td) / "ep-test"
        sdir.mkdir()
        # 一個 8–10s 式長 cue(NG 黏正式開場的 B0006 形狀)+ 一個單句 cue
        (sdir / "transcript.srt").write_text(
            "1\n00:00:00,000 --> 00:00:04,000\n[Sarah] 謝了,再來一次。嗨,歡迎收聽。\n\n"
            "2\n00:00:05,000 --> 00:00:07,000\n[Mars] 尾聲\n",
            encoding="utf-8")
        (sdir / "words.json").write_text(json.dumps([
            {"start": 0.2, "end": 1.0, "word": "謝了,"},
            {"start": 1.0, "end": 2.0, "word": "再來一次。"},
            {"start": 2.2, "end": 3.0, "word": "嗨,"},
            {"start": 3.0, "end": 3.8, "word": "歡迎收聽。"},
            {"start": 5.2, "end": 6.5, "word": "尾聲"},
        ], ensure_ascii=False), encoding="utf-8")
        return sdir

    def test_resegment_full_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            original = (sdir / "transcript.srt").read_bytes()
            proc = run_script("resegment_srt.py", "--session", str(sdir))
            self.assertEqual(proc.returncode, 0, proc.stderr)

            cues = parse_srt(sdir / "transcript.srt")
            # 長 cue 依標點切成 4 短句 + 單句 cue 照舊 = 5
            self.assertEqual([c["text"] for c in cues],
                             ["謝了,", "再來一次。", "嗨,", "歡迎收聽。", "尾聲"])
            # NG 段與正式開場分屬不同 cue(設計文件驗收判準 1)
            # 首尾沿用原 cue 邊界(與 split_cues_by_turns 同慣例)
            self.assertEqual(cues[0]["start"], 0.0)   # 原 cue start,非 word 的 0.2
            self.assertEqual(cues[3]["end"], 4.0)     # 原 cue end,非 word 的 3.8
            self.assertEqual(cues[4]["start"], 5.0)
            self.assertEqual(cues[4]["end"], 7.0)
            # 中間切點用 word 時間
            self.assertEqual(cues[1]["start"], 1.0)
            # speaker 傳承
            self.assertEqual([c["speaker"] for c in cues],
                             ["Sarah", "Sarah", "Sarah", "Sarah", "Mars"])
            # 備份=原檔 byte-identical
            bak = sdir / "transcript.srt.bak-longsegs"
            self.assertEqual(bak.read_bytes(), original)
            # 無 mismatch
            self.assertIn("不符 0 段", proc.stdout)

            # 二跑:備份已存在不覆蓋(仍是第一版原檔)
            proc2 = run_script("resegment_srt.py", "--session", str(sdir))
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertEqual(bak.read_bytes(), original)

    def test_mismatch_reported_not_fatal(self):
        # words 重建與原 cue 文字不符(OpenCC 兩路徑差異的形狀)→ 回報不中斷
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "ep-test"
            sdir.mkdir()
            (sdir / "transcript.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n原文版本\n", encoding="utf-8")
            (sdir / "words.json").write_text(json.dumps([
                {"start": 0.0, "end": 1.0, "word": "簡體。"},
                {"start": 1.0, "end": 2.0, "word": "版本"},
            ], ensure_ascii=False), encoding="utf-8")
            proc = run_script("resegment_srt.py", "--session", str(sdir))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("不符 1 段", proc.stdout)

    def test_missing_words_json_fails(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "ep-test"
            sdir.mkdir()
            (sdir / "transcript.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n句\n", encoding="utf-8")
            proc = run_script("resegment_srt.py", "--session", str(sdir))
            self.assertEqual(proc.returncode, 1)


class TestMigrateParseBlocks(unittest.TestCase):
    def test_strikes_extracted_reason_split(self):
        lines = ["# Cutplan — test", "",
                 "- [x] B0001 [0:00–0:04] [Sarah] 你打扮,~~嗯~~不對 ← 假起頭",
                 "隨便一行不是 block"]
        blocks, stream, spans = parse_blocks(lines)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "你打扮,嗯不對")   # ~~ 已剝掉
        self.assertEqual(blocks[0]["suffix"], " ← 假起頭")
        self.assertEqual(stream, "你打扮,嗯不對")
        self.assertEqual(spans, [(4, 5)])                      # 嗯 的字元流座標

    def test_unchecked_block_also_parsed(self):
        lines = ["- [ ] B0002 [0:04–0:05] [Mars] 剪掉段"]
        blocks, stream, spans = parse_blocks(lines)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(spans, [])


class TestMapSpans(unittest.TestCase):
    def test_identical_streams_identity_mapping(self):
        mapped, n_drop = map_spans([(2, 5)], "abcdefgh", "abcdefgh")
        self.assertEqual(mapped, [(2, 5)])
        self.assertEqual(n_drop, 0)

    def test_shifted_stream_remapped(self):
        # 新流前面插了字,span 座標要跟著平移
        mapped, n_drop = map_spans([(0, 3)], "abcdef", "XXabcdef")
        self.assertEqual(mapped, [(2, 5)])
        self.assertEqual(n_drop, 0)

    def test_span_text_missing_dropped(self):
        # span 蓋到的字在新流不存在 → 整段丟棄(寧缺勿錯)
        mapped, n_drop = map_spans([(3, 6)], "abcXYZdef", "abcdef")
        self.assertEqual(mapped, [])
        self.assertEqual(n_drop, 1)


OLD_CUTPLAN = """# Cutplan — test

- [x] B0001 [0:00–0:04] [Sarah] 你打扮,~~嗯不對,~~再來。
- [x] B0002 [0:04–0:06] [Sarah] 獨有段~~噠~~
"""

# 重切後:同一條字元流拆成 3 個 block,且舊 B0002 的「噠」已不存在
NEW_CUTPLAN = """# Cutplan — test

> 來源:transcript.speakers.srt。

- [x] B0001 [0:00–0:01] [Sarah] 你打扮,嗯
- [ ] B0002 [0:01–0:03] [Sarah] 不對,再來。 ← 測理由
- [x] B0003 [0:04–0:06] [Sarah] 獨有段
"""


class TestMigrateE2E(unittest.TestCase):
    def _run(self, old_text: str, new_text: str):
        self._td = tempfile.TemporaryDirectory()
        td = Path(self._td.name)
        sdir = td / "ep-test"
        sdir.mkdir()
        old = td / "cutplan.md.bak"
        old.write_text(old_text, encoding="utf-8")
        (sdir / "cutplan.md").write_text(new_text, encoding="utf-8")
        proc = run_script("migrate_marks.py", "--session", str(sdir),
                          "--old", str(old))
        self.addCleanup(self._td.cleanup)
        return proc, (sdir / "cutplan.md").read_text(encoding="utf-8")

    def test_cross_block_span_split_and_drop(self):
        proc, out = self._run(OLD_CUTPLAN, NEW_CUTPLAN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 舊 span「嗯不對,」跨新 B0001/B0002 邊界 → 拆成兩段
        self.assertIn("- [x] B0001 [0:00–0:01] [Sarah] 你打扮,~~嗯~~", out)
        self.assertIn("- [ ] B0002 [0:01–0:03] [Sarah] ~~不對,~~再來。 ← 測理由", out)
        # 「噠」在新版不存在 → 整段丟棄,B0003 原樣
        self.assertIn("- [x] B0003 [0:04–0:06] [Sarah] 獨有段\n", out)
        self.assertIn("2 spans → 移植 2 段(丟棄 1)", proc.stdout)
        # 遷移註記插在標題後
        lines = out.splitlines()
        self.assertTrue(lines[2].startswith("> 🤖 Gemma 贅字預標已從舊版"), lines[:4])
        self.assertIn("對不上丟棄 1 處", lines[2])

    def test_new_already_marked_aborts(self):
        proc, out = self._run(
            OLD_CUTPLAN,
            "# Cutplan — test\n\n- [x] B0001 [0:00–0:01] [Sarah] 已有~~標~~\n")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("不重複遷移", proc.stderr)
        self.assertIn("已有~~標~~", out)  # 檔案未被動過

    def test_old_without_strikes_noop(self):
        proc, out = self._run(
            "# Cutplan — test\n\n- [x] B0001 [0:00–0:01] [Sarah] 乾淨\n",
            NEW_CUTPLAN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("無事可做", proc.stdout)
        self.assertNotIn("~~", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
