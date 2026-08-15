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
from migrate_marks import (parse_blocks, map_spans,  # noqa: E402
                           migrate_per_speaker)


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
        blocks, stream, spans, cuts = parse_blocks(lines)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "你打扮,嗯不對")   # ~~ 已剝掉
        self.assertEqual(blocks[0]["suffix"], " ← 假起頭")
        self.assertEqual(stream, "你打扮,嗯不對")
        self.assertEqual(spans, [(4, 5)])                      # 嗯 的字元流座標

    def test_unchecked_block_also_parsed(self):
        lines = ["- [ ] B0002 [0:04–0:05] [Mars] 剪掉段"]
        blocks, stream, spans, cuts = parse_blocks(lines)
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





class TestCheckboxMigration(unittest.TestCase):
    """勾選遷移(2026-08-11 MM):重切後 block id 全換,人審的「剪掉」決定
    不能跟著消失。migrate_marks 原本只搬 ~~刪除線~~,明講不搬勾選。"""

    OLD = (
        "# Cutplan — old\n\n"
        "- [x] B0001 [0:00–0:02] [Mars] 這句要留下來，很重要。\n"
        "- [ ] B0002 [0:02–0:04] [KIN] 這整段是離題的廢話應該剪掉。 ← 離題\n"
        "- [x] B0003 [0:04–0:06] [Sarah] 最後這句也要留著。\n")
    # 重切:同樣的字,切點不同、id 全換
    NEW = (
        "# Cutplan — new\n\n"
        "- [x] M0001 [0:00–0:01] [Mars] 這句要留下來，\n"
        "- [x] M0002 [0:01–0:02] [Mars] 很重要。\n"
        "- [x] K0001 [0:02–0:03] [KIN] 這整段是離題的廢話\n"
        "- [x] K0002 [0:03–0:04] [KIN] 應該剪掉。\n"
        "- [x] S0001 [0:04–0:06] [Sarah] 最後這句也要留著。\n")

    def _migrate(self, *extra: str):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        sdir = Path(td.name)
        (sdir / "cutplan.md").write_text(self.NEW, encoding="utf-8")
        old = sdir / "old.md"
        old.write_text(self.OLD, encoding="utf-8")
        proc = run_script("migrate_marks.py", "--session", str(sdir),
                          "--old", str(old), *extra)
        return proc, (sdir / "cutplan.md").read_text(encoding="utf-8")

    def test_unchecked_block_migrates_across_resegmentation(self):
        proc, out = self._migrate("--with-checkboxes")
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        # 舊的 B0002 被剪掉 → 重切後涵蓋同一段文字的兩個 block 都要取消勾選
        self.assertIn("- [ ] K0001", out)
        self.assertIn("- [ ] K0002", out)
        # 沒被剪的內容不准被誤傷
        self.assertIn("- [x] M0001", out)
        self.assertIn("- [x] M0002", out)
        self.assertIn("- [x] S0001", out)

    def test_checkboxes_untouched_without_the_flag(self):
        """預設行為不變——只搬刪除線,不動勾選(既有呼叫方不受影響)。"""
        proc, out = self._migrate()
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotIn("- [ ]", out)


def _rows(md: str) -> dict[str, str]:
    import re as _re
    out = {}
    for l in md.splitlines():
        m = _re.match(r"^- \[[ xX]\] ([A-Z]{1,2}\d{3,5}) ", l)
        if m:
            out[m.group(1)] = l
    return out


class TestPerSpeakerMigration(unittest.TestCase):
    """把混音 cutplan 的人審資產搬到逐軌 cutplan —— 必須**逐講者**對齊。

    整份文件攤成一條字元流會出事:同一句話在混音版只出現一次,在逐軌版
    只出現在那個人的軌上;但別人的軌上也有大量長得很像的鄰句。全域 difflib
    很容易把 Sarah 的刪除線搬到 Mars 的列上 —— 那正是 D4 明文禁止的
    「把同一段串音文字的刪除線複製到其他軌」。
    """

    OLD = ("# Cutplan\n"
           "- [x] B0001 [0:00–0:01] [Sarah] 我覺得~~那個~~很好\n"
           "- [ ] B0002 [0:01–0:02] [Mars] 這句不要\n"
           "- [x] B0003 [0:02–0:03] [Mars] 我覺得那個很好\n")
    NEW = ("# Cutplan（分軌）\n"
           "- [x] SR0001 [0:00–0:00] [Sarah] 我覺得\n"
           "- [x] SR0002 [0:00–0:01] [Sarah] 那個很好\n"
           "- [x] MR0001 [0:01–0:02] [Mars] 這句不要\n"
           "- [x] MR0002 [0:02–0:03] [Mars] 我覺得那個很好\n"
           "- [ ] MR0003 [0:02–0:03] [Mars] （非詞彙出聲／待辨 0.4s）\n")

    def _run(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / "old.md"
            new = Path(td) / "cutplan.pertrack.md"
            old.write_text(self.OLD, encoding="utf-8")
            new.write_text(self.NEW, encoding="utf-8")
            stats = migrate_per_speaker(old, new, **kw)
            return new.read_text(encoding="utf-8"), stats

    def test_strike_lands_on_the_right_speaker_only(self):
        out, _ = self._run(with_checkboxes=True)
        lines = _rows(out)
        self.assertIn("~~那個~~", lines["SR0002"])
        self.assertNotIn("~~", lines["MR0002"])

    def test_unchecked_block_migrates_to_that_speakers_rows(self):
        out, _ = self._run(with_checkboxes=True)
        lines = _rows(out)
        self.assertTrue(lines["MR0001"].startswith("- [ ]"))
        self.assertTrue(lines["MR0002"].startswith("- [x]"))

    def test_voicing_rows_are_never_touched(self):
        out, _ = self._run(with_checkboxes=True)
        self.assertIn("- [ ] MR0003", out)

    def test_checkboxes_are_left_alone_without_the_flag(self):
        out, _ = self._run(with_checkboxes=False)
        self.assertTrue(any(l.startswith("- [x] MR0001")
                            for l in out.splitlines()))
        self.assertIn("~~那個~~", out)

    def test_whitespace_differences_do_not_drop_marks(self):
        """逐軌列的文字是 canonical 去空白後的切片,舊版保留空白;對齊要照樣過。"""
        old = ("# x\n- [x] B0001 [0:00–0:01] [Mars] 臨時 ~~任務~~ 很多\n")
        new = ("# x\n- [x] MR0001 [0:00–0:01] [Mars] 臨時任務很多\n")
        with tempfile.TemporaryDirectory() as td:
            o, n = Path(td) / "o.md", Path(td) / "n.md"
            o.write_text(old, encoding="utf-8")
            n.write_text(new, encoding="utf-8")
            st = migrate_per_speaker(o, n, with_checkboxes=True)
            out = n.read_text(encoding="utf-8")
        self.assertEqual(st["dropped"], 0)
        self.assertIn("臨時~~任務~~很多", out)

    def test_strike_follows_a_word_that_changed_track(self):
        """波形歸屬跟 diarize 不一致時,刪除線要跟著那個字走,不是被丟掉。

        EP16 實測:154 處刪除線裡有 31 處落在被重新歸屬的口頭禪(嗯/然後/哦)
        —— 那些字在混音版掛在 A 身上、逐軌版掛到 B 的軌上。只做逐講者對齊
        就會整批遺失人審成果,所以對不上的殘留要再做一次全域比對。
        """
        old = ("# x\n- [x] B0001 [0:00–0:01] [Mars] 我覺得很好~~嗯~~\n"
               "- [x] B0002 [0:01–0:02] [Sarah] 那我們繼續\n")
        new = ("# x\n- [x] MR0001 [0:00–0:01] [Mars] 我覺得很好\n"
               "- [x] SR0001 [0:01–0:01] [Sarah] 嗯\n"
               "- [x] SR0002 [0:01–0:02] [Sarah] 那我們繼續\n")
        with tempfile.TemporaryDirectory() as td:
            o, n = Path(td) / "o.md", Path(td) / "n.md"
            o.write_text(old, encoding="utf-8")
            n.write_text(new, encoding="utf-8")
            st = migrate_per_speaker(o, n, with_checkboxes=True)
            rows = _rows(n.read_text(encoding="utf-8"))
        self.assertEqual(st["dropped"], 0)
        self.assertEqual(st["rehomed"], 1)
        self.assertIn("~~嗯~~", rows["SR0001"])
        self.assertNotIn("~~", rows["MR0001"])

    def test_stats_report_what_moved_and_what_was_dropped(self):
        _out, st = self._run(with_checkboxes=True)
        self.assertEqual(st["strikes_in"], 1)
        self.assertEqual(st["strikes_out"], 1)
        self.assertEqual(st["unchecked"], 1)
        self.assertEqual(st["dropped"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
