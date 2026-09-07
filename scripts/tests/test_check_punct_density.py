#!/usr/bin/env python3
"""test_check_punct_density.py — 逐字稿標點密度稽核的行為鎖定測試。

這支工具存在的理由是「零標點沒有其他症狀」,所以它自己算錯就等於沒有守衛。
鎖的是三個會讓它算錯的地方:
    時間軸行的 `,` 與 `:` 混進標點數(會把零標點的檔判成正常)
    只算全形標點(whisper.cpp 的逗號是**半形**,會把正常的檔判成零標點)
    `[講者]` 前綴混進字數(灌水字數 ⇒ 密度被低估)

跑法:
    python3 scripts/tests/test_check_punct_density.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
REPO_ROOT = AUDIO_DIR.parent.parent
sys.path.insert(0, str(AUDIO_DIR))

from check_punct_density import (FAIL_MISSING, FAIL_SPARSE, FAIL_ZERO,  # noqa: E402
                                 audit, collect, measure)

SCRIPT = AUDIO_DIR / "check_punct_density.py"


def srt(*texts: str) -> str:
    """組一份 SRT。時間軸行刻意含 `,`(毫秒分隔)與 `:`,那是最容易誤算的來源。"""
    out = []
    for i, t in enumerate(texts, 1):
        a, b = (i - 1) * 2, i * 2
        out.append(f"{i}\n00:00:{a:02d},000 --> 00:00:{b:02d},000\n{t}\n")
    return "\n".join(out) + "\n"


class TestMeasure(unittest.TestCase):

    def test_halfwidth_comma_counts(self):
        """whisper.cpp 的逗號輸出半形、句號輸出全形,只算全形會誤判成零標點。"""
        n_char, n_punct, density, _ = measure("今天天氣很好,我們出門走走。")
        self.assertEqual(n_punct, 2)
        self.assertEqual(n_char, 12)
        self.assertAlmostEqual(density, 6.0)

    def test_zero_punctuation_has_no_density(self):
        """零標點回 None 而不是無限大 —— 下游不必處理 inf。"""
        n_char, n_punct, density, _ = measure("今天天氣很好我們出門走走")
        self.assertEqual((n_char, n_punct, density), (12, 0, None))

    def test_punctuation_not_counted_as_chars(self):
        """標點不算字數,不然密度會被自己灌水。"""
        self.assertEqual(measure("你好。")[0], 2)

    def test_cjk_ratio(self):
        self.assertAlmostEqual(measure("你好,world。")[3], 2 / 7)

    def test_consecutive_punctuation_counts_as_one(self):
        """whisper 用 `...` 標停頓;逐字元算會讓一個省略號變 3 個標點,
        EP18 實測因此變成每 2.0 字一個 —— 數的是句界不是標點字元。"""
        n_char, n_punct, density, _ = measure("拜拜...拜拜...拜拜。")
        self.assertEqual((n_char, n_punct), (6, 3))
        self.assertAlmostEqual(density, 2.0)


class TestAudit(unittest.TestCase):

    def _write(self, td: str, body: str, name: str = "transcript.srt") -> Path:
        d = Path(td) / "ep01"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_timestamp_lines_do_not_inflate_punctuation(self):
        """時間軸行有 6 個 `:` 和 2 個 `,`;算進去就會把零標點的檔判成正常。"""
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, srt("今天天氣很好我們出門走走一直走到天黑才回家"))
            r = audit(p, 60.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], FAIL_ZERO)
        self.assertEqual(r["puncts"], 0)

    def test_speaker_prefix_not_counted(self):
        """`[Sarah]` 前綴不是逐字稿內容,算進字數會低估密度。"""
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, srt("[Sarah] 你好。"))
            r = audit(p, 60.0)
        self.assertEqual(r["chars"], 2)
        self.assertTrue(r["ok"])

    def test_normal_transcript_passes(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, srt("今天天氣很好,我們出門走走。", "走到天黑才回家。"))
            r = audit(p, 60.0)
        self.assertTrue(r["ok"])
        self.assertLess(r["density"], 40)

    def test_sparse_but_nonzero_fails(self):
        """壞掉的實測是 0 個標點,但密度不足也要擋 —— 門檻是密度不是「有沒有」。"""
        body = "今天天氣很好我們出門走走一直走到天黑才回家" * 5 + "。"
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, srt(body))
            r = audit(p, 60.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], FAIL_SPARSE)

    def test_threshold_boundary(self):
        """剛好等於門檻算通過,超過才不合格。"""
        body = "一" * 60 + "。"
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, srt(body))
            self.assertTrue(audit(p, 60.0)["ok"])
            self.assertFalse(audit(p, 59.0)["ok"])

    def test_missing_and_empty_are_failures(self):
        """缺檔與空檔要進重轉清單,不能因為「沒有標點問題」就放行。"""
        with tempfile.TemporaryDirectory() as td:
            empty = self._write(td, "")
            self.assertEqual(audit(empty, 60.0)["reason"], FAIL_MISSING)
            self.assertEqual(
                audit(Path(td) / "nope" / "transcript.srt", 60.0)["reason"],
                FAIL_MISSING)


class TestCollect(unittest.TestCase):

    def test_batch_output_layout(self):
        """batch_llmnode.sh 的輸出是 <輸出根>/<檔名去副檔名>/transcript.srt。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("ep02", "ep01"):
                (root / name).mkdir()
                (root / name / "transcript.srt").write_text("x", encoding="utf-8")
            (root / "空的").mkdir()
            got = collect([root], "transcript.srt")
        self.assertEqual([p.parent.name for p in got], ["ep01", "ep02"])

    def test_session_dir_itself(self):
        """指到 session 目錄本身也要收(剪輯線的用法)。"""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "transcript.srt").write_text("x", encoding="utf-8")
            self.assertEqual(len(collect([Path(td)], "transcript.srt")), 1)


class TestCli(unittest.TestCase):

    def _root(self, td: str) -> Path:
        root = Path(td)
        (root / "good").mkdir()
        (root / "good" / "transcript.srt").write_text(
            srt("今天天氣很好,我們出門走走。"), encoding="utf-8")
        (root / "bad").mkdir()
        (root / "bad" / "transcript.srt").write_text(
            srt("今天天氣很好我們出門走走一直走到天黑才回家"), encoding="utf-8")
        return root

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, cwd=REPO_ROOT)

    def test_list_mode_prints_only_directory_names(self):
        """--list 的輸出要能直接餵回批次:一行一個目錄名,沒有裝飾。"""
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(str(self._root(td)), "--list")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.split(), ["bad"])

    def test_check_mode_exit_code(self):
        """--check 當 gate:有不合格回非零,全過回 0。"""
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            self.assertEqual(self._run(str(root), "--check").returncode, 1)
            self.assertEqual(
                self._run(str(root / "good"), "--check").returncode, 0)

    def test_default_report_lists_both(self):
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(str(self._root(td)))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("0 標點", proc.stdout)
        self.assertIn("通過 1 / 不合格 1", proc.stdout)

    def test_no_targets_is_an_error_not_a_pass(self):
        """路徑打錯時要報錯,不能因為「零份不合格」就靜靜回 0。"""
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(str(Path(td) / "nothing-here"), "--check")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
