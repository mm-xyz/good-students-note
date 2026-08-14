#!/usr/bin/env python3
"""test_cutplan.py — cutplan 產生器的行為鎖定測試(characterization)。

鎖住:build_blocks 合併規則(merge_gap=0 預設一 cue 一 block,2026-07-28 MM 拍板)、
build_gaps G 列門檻(2026-07-29 MM 拍板:空白要看得見)、refine_gaps 的 burst 拆分
(合成 wav 驗證有聲小段獨立成列)、prepare e2e 產物(cutplan.md/json + 提案 marker)。

跑法:
    python3 scripts/tests/test_cutplan.py
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
REPO_ROOT = AUDIO_DIR.parent.parent
sys.path.insert(0, str(AUDIO_DIR))

from cutplan import (build_blocks, build_gaps, refine_gaps, gap_line,  # noqa: E402
                     detect_asr_artifact, flag_artifacts)
from fixtures.ep16_artifact_samples import (  # noqa: E402
    B0068_ARTIFACT_TEXT, B0067_CLEAN_TEXT, B0001_CLEAN_TEXT)


def cue(idx, start, end, text, speaker=None):
    return {"idx": idx, "start": start, "end": end, "text": text, "speaker": speaker}


class TestBuildBlocks(unittest.TestCase):
    def test_merge_gap_zero_one_cue_one_block(self):
        # 預設 merge_gap=0:依原 SRT 短句一行一句(2026-07-28 MM 拍板)
        cues = [cue(1, 0.0, 1.0, "一", "S1"), cue(2, 1.1, 2.0, "二", "S1")]
        blocks = build_blocks(cues, merge_gap=0.0, max_block=45.0)
        self.assertEqual(len(blocks), 2)
        self.assertEqual([b["id"] for b in blocks], ["B0001", "B0002"])

    def test_same_speaker_within_gap_merged(self):
        cues = [cue(1, 0.0, 1.0, "一", "S1"), cue(2, 1.5, 2.5, "二", "S1")]
        blocks = build_blocks(cues, merge_gap=1.2, max_block=45.0)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "一二")
        self.assertEqual(blocks[0]["end"], 2.5)
        self.assertEqual(blocks[0]["cue_idx"], [1, 2])

    def test_speaker_change_not_merged(self):
        cues = [cue(1, 0.0, 1.0, "一", "S1"), cue(2, 1.1, 2.0, "二", "S2")]
        blocks = build_blocks(cues, merge_gap=1.2, max_block=45.0)
        self.assertEqual(len(blocks), 2)

    def test_gap_beyond_merge_gap_not_merged(self):
        cues = [cue(1, 0.0, 1.0, "一", "S1"), cue(2, 3.0, 4.0, "二", "S1")]
        blocks = build_blocks(cues, merge_gap=1.2, max_block=45.0)
        self.assertEqual(len(blocks), 2)

    def test_max_block_caps_merge(self):
        cues = [cue(1, 0.0, 40.0, "長段", "S1"), cue(2, 40.5, 50.0, "續", "S1")]
        blocks = build_blocks(cues, merge_gap=1.2, max_block=45.0)
        self.assertEqual(len(blocks), 2)

    def test_defaults_keep_true_reason_empty(self):
        blocks = build_blocks([cue(1, 0.0, 1.0, "一")], 0.0, 45.0)
        self.assertTrue(blocks[0]["keep"])
        self.assertEqual(blocks[0]["reason"], "")


class TestBuildGaps(unittest.TestCase):
    def test_gap_at_head_and_between(self):
        blocks = [{"id": "B0001", "start": 3.0, "end": 5.0},
                  {"id": "B0002", "start": 9.0, "end": 10.0}]
        gaps = build_gaps(blocks, min_gap=2.0)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0], {"start": 0.0, "end": 3.0, "before": "B0001",
                                   "id": "G0001", "keep": False})
        self.assertEqual(gaps[1]["before"], "B0002")
        self.assertEqual(gaps[1]["start"], 5.0)

    def test_gap_below_threshold_ignored(self):
        blocks = [{"id": "B0001", "start": 0.0, "end": 5.0},
                  {"id": "B0002", "start": 6.5, "end": 10.0}]
        self.assertEqual(build_gaps(blocks, min_gap=2.0), [])

    def test_gaps_default_unchecked(self):
        blocks = [{"id": "B0001", "start": 4.0, "end": 5.0}]
        gaps = build_gaps(blocks, min_gap=2.0)
        self.assertFalse(gaps[0]["keep"])  # 預設不勾=照舊剪掉


def write_wav(path: Path, secs: float, bursts: list[tuple[float, float]],
              sr: int = 16000, amp: int = 16000):
    """合成 16k mono wav:全靜音,只有 bursts 區間有恆定振幅方波。"""
    n = int(secs * sr)
    samples = bytearray(n * 2)
    for b0, b1 in bursts:
        for i in range(int(b0 * sr), min(n, int(b1 * sr))):
            samples[i * 2:i * 2 + 2] = int(amp * (1 if i % 32 < 16 else -1)) \
                .to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(samples))


class TestRefineGaps(unittest.TestCase):
    def test_missing_wav_returns_gaps_unchanged(self):
        gaps = [{"id": "G0001", "start": 0.0, "end": 2.0, "before": "B0001",
                 "keep": False}]
        out = refine_gaps(gaps, Path("/nonexistent/audio16k.wav"))
        self.assertEqual(out, gaps)

    def test_silent_gap_labeled_silence(self):
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio16k.wav"
            write_wav(wav, 5.0, bursts=[])
            gaps = [{"start": 1.0, "end": 4.0, "before": "B0002", "keep": False}]
            out = refine_gaps(gaps, wav)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "silence")
        self.assertEqual(out[0]["id"], "G0001")

    def test_burst_split_into_sound_gap(self):
        # gap 內 2.0–2.4s 有一聲(笑/打板):拆成獨立 sound G 列,±0.15s pad
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio16k.wav"
            write_wav(wav, 5.0, bursts=[(2.0, 2.4)])
            gaps = [{"start": 1.0, "end": 4.0, "before": "B0002", "keep": False}]
            out = refine_gaps(gaps, wav)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "sound")
        self.assertGreaterEqual(out[0]["start"], 1.7)
        self.assertLessEqual(out[0]["start"], 2.0)
        self.assertGreaterEqual(out[0]["end"], 2.4)
        self.assertLessEqual(out[0]["end"], 2.7)
        self.assertFalse(out[0]["keep"])

    def test_two_bursts_two_sound_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio16k.wav"
            write_wav(wav, 8.0, bursts=[(2.0, 2.3), (5.0, 5.3)])
            gaps = [{"start": 1.0, "end": 7.0, "before": "B0002", "keep": False}]
            out = refine_gaps(gaps, wav)
        self.assertEqual(len(out), 2)
        self.assertEqual([g["kind"] for g in out], ["sound", "sound"])
        self.assertEqual([g["id"] for g in out], ["G0001", "G0002"])

    def test_too_short_burst_dropped(self):
        # 短於 min_burst(0.12s)的有聲窗丟棄 → 整段仍算靜音
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio16k.wav"
            write_wav(wav, 5.0, bursts=[(2.0, 2.05)])
            gaps = [{"start": 1.0, "end": 4.0, "before": "B0002", "keep": False}]
            out = refine_gaps(gaps, wav)
        self.assertEqual(out[0]["kind"], "silence")


class TestDetectAsrArtifact(unittest.TestCase):
    """#675 — whisper 重複迴圈/亂碼守門(混音線,cutplan 產生階段)。

    判準沿用 pertrack_blocks.is_artifact()(2026-08-11,EP16 Mars 軌「嘗」×40):
    同字元連續重複 ≥4、含 U+FFFD、整句只由 ≤2 種字元組成(長度 ≥6),再加一條
    混音線需要的短語(n-gram)連續重複偵測,因為混音線的 block 可能是「正常開場
    + 重複段」混在一起,不是整句都退化。"""

    def test_ep16_b0068_repeat_loop_detected(self):
        # 實測:EP16 B0068 [7:33-7:53] 20.4 秒「反而反而反而…」
        reason = detect_asr_artifact(B0068_ARTIFACT_TEXT)
        self.assertIsNotNone(reason)

    def test_ep16_b0067_clean_block_not_flagged(self):
        self.assertIsNone(detect_asr_artifact(B0067_CLEAN_TEXT))

    def test_ep16_b0001_opening_not_flagged(self):
        self.assertIsNone(detect_asr_artifact(B0001_CLEAN_TEXT))

    def test_same_char_run_below_threshold_not_flagged(self):
        # 口語重複 3 次是正常停頓/強調(「對對對」),不到 4 次不算迴圈
        self.assertIsNone(detect_asr_artifact("對對對,我也覺得。"))

    def test_same_char_run_at_threshold_flagged(self):
        self.assertIsNotNone(detect_asr_artifact("啊啊啊啊啊啊啊啊"))

    def test_replacement_char_flagged(self):
        self.assertIsNotNone(detect_asr_artifact("正常一半��亂碼一半"))

    def test_short_normal_text_not_flagged(self):
        self.assertIsNone(detect_asr_artifact("我是King。"))

    def test_empty_text_not_flagged(self):
        # 空字串不是 artifact 的問題(是別處的問題),偵測器不對它下判斷
        self.assertIsNone(detect_asr_artifact(""))

    def test_phrase_repeat_mixed_with_real_content_flagged(self):
        # 混音線常見型態:正常起頭接一段短語迴圈,不是整句退化
        text = "然後我覺得" + "反而" * 10
        self.assertIsNotNone(detect_asr_artifact(text))


class TestFlagArtifacts(unittest.TestCase):
    def test_flags_artifact_block_reason_and_field(self):
        blocks = build_blocks(
            [cue(1, 0.0, 1.0, B0068_ARTIFACT_TEXT, "Sarah")], 0.0, 45.0)
        n = flag_artifacts(blocks)
        self.assertEqual(n, 1)
        self.assertTrue(blocks[0]["asr_artifact"])
        self.assertIn("⚠ASR-artifact", blocks[0]["reason"])
        self.assertTrue(blocks[0]["keep"])  # 只標記,不自動剪

    def test_clean_block_not_flagged(self):
        blocks = build_blocks(
            [cue(1, 0.0, 1.0, B0067_CLEAN_TEXT, "Sarah")], 0.0, 45.0)
        n = flag_artifacts(blocks)
        self.assertEqual(n, 0)
        self.assertFalse(blocks[0]["asr_artifact"])
        self.assertEqual(blocks[0]["reason"], "")

    def test_does_not_clobber_existing_reason(self):
        blocks = build_blocks(
            [cue(1, 0.0, 1.0, B0068_ARTIFACT_TEXT, "Sarah")], 0.0, 45.0)
        blocks[0]["reason"] = "人審已手動判斷"
        flag_artifacts(blocks)
        self.assertEqual(blocks[0]["reason"], "人審已手動判斷")
        self.assertTrue(blocks[0]["asr_artifact"])  # 結構化欄位仍要標記


class TestGapLine(unittest.TestCase):
    def test_sound_line_format(self):
        line = gap_line({"id": "G0001", "start": 60.0, "end": 61.5, "keep": False,
                         "kind": "sound", "peak_db": -12.3})
        self.assertTrue(line.startswith("- [ ] G0001 [1:00–1:01] 🔊 聲音事件 1.5s"))
        self.assertIn("-12dB", line)

    def test_silence_line_format_and_keep_mark(self):
        line = gap_line({"id": "G0002", "start": 0.0, "end": 3.0, "keep": True,
                         "kind": "silence"})
        self.assertTrue(line.startswith("- [x] G0002 [0:00–0:03] ⬜ 空白/非語音 3.0s"))
        self.assertIn("靜音", line)


class TestPrepareE2E(unittest.TestCase):
    """prepare 子指令端對端:temp session 只放 transcript.srt(無 wav,
    refine_gaps 走原樣返回),驗證三個產物落地與內容契約。"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.sdir = Path(cls._td.name) / "ep-test"
        cls.sdir.mkdir()
        (cls.sdir / "transcript.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n[Sarah] 開場\n\n"
            "2\n00:00:01,200 --> 00:00:02,000\n[Sarah] 繼續\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\n[Mars] 換人講\n",
            encoding="utf-8")
        cls.proc = subprocess.run(
            [sys.executable, str(AUDIO_DIR / "cutplan.py"), "prepare",
             "--session", str(cls.sdir)],
            capture_output=True, text=True, cwd=REPO_ROOT)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_exit_zero(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_json_blocks_default_granularity(self):
        data = json.loads((self.sdir / "cutplan.json").read_text(encoding="utf-8"))
        # merge_gap 預設 0 → 一 cue 一 block
        self.assertEqual(len(data["blocks"]), 3)
        self.assertEqual(data["blocks"][0]["speaker"], "Sarah")
        self.assertEqual(data["merge_gap"], 0.0)

    def test_json_gap_between_blocks(self):
        data = json.loads((self.sdir / "cutplan.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["gaps"]), 1)
        self.assertEqual(data["gaps"][0]["before"], "B0003")

    def test_md_block_lines(self):
        md = (self.sdir / "cutplan.md").read_text(encoding="utf-8")
        self.assertIn("- [x] B0001 [0:00–0:01] [Sarah] 開場", md)
        self.assertIn("- [x] B0003 [0:05–0:06] [Mars] 換人講", md)
        # G 列插在所屬 block 前面
        self.assertLess(md.index("G0001"), md.index("B0003"))

    def test_marker_written(self):
        marker = json.loads((self.sdir / ".cutplan_pending.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(marker["stage"], "cutplan-proposal")
        self.assertIn("instructions", marker)

    def test_add_gaps_idempotent(self):
        # cutplan.json 已有 gaps → add-gaps 不重複加
        proc = subprocess.run(
            [sys.executable, str(AUDIO_DIR / "cutplan.py"), "add-gaps",
             "--session", str(self.sdir)],
            capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("不重複加", proc.stdout)


class TestPrepareE2EArtifact(unittest.TestCase):
    """#675 — prepare 端對端接上 artifact 守門:混合一個正常 cue 與一個
    EP16 實測的重複迴圈 cue,驗證 cutplan.md/json 都標記,且不影響 keep 狀態。"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.sdir = Path(cls._td.name) / "ep-artifact-test"
        cls.sdir.mkdir()
        srt = (
            "1\n00:00:00,000 --> 00:00:01,000\n[Sarah] " + B0001_CLEAN_TEXT + "\n\n"
            "2\n00:00:05,000 --> 00:00:25,000\n[Sarah] " + B0068_ARTIFACT_TEXT + "\n")
        (cls.sdir / "transcript.srt").write_text(srt, encoding="utf-8")
        cls.proc = subprocess.run(
            [sys.executable, str(AUDIO_DIR / "cutplan.py"), "prepare",
             "--session", str(cls.sdir)],
            capture_output=True, text=True, cwd=REPO_ROOT)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_exit_zero(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_stdout_reports_artifact_count(self):
        self.assertIn("artifact", self.proc.stdout.lower())

    def test_json_marks_artifact_block_only(self):
        data = json.loads((self.sdir / "cutplan.json").read_text(encoding="utf-8"))
        by_id = {b["id"]: b for b in data["blocks"]}
        self.assertFalse(by_id["B0001"]["asr_artifact"])
        self.assertTrue(by_id["B0002"]["asr_artifact"])
        self.assertTrue(by_id["B0002"]["keep"])  # 標記不等於自動剪

    def test_md_shows_marker_on_artifact_line_only(self):
        md = (self.sdir / "cutplan.md").read_text(encoding="utf-8")
        lines = {}
        for l in md.splitlines():
            m = re.match(r"^- \[.\] (B\d{4}) ", l)
            if m:
                lines[m.group(1)] = l
        self.assertNotIn("⚠ASR-artifact", lines["B0001"])
        self.assertIn("⚠ASR-artifact", lines["B0002"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
