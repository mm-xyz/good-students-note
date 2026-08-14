#!/usr/bin/env python3
"""test_precut.py — precut.py(初剪一鍵入口,卡 #679)的編排邏輯行為鎖定測試。

不跑真 ASR/diarize/prosody(太貴、太重)。鎖的是純編排:素材形態偵測、階段
順序與指令組成、冪等跳過、--force 重跑、失敗中止與手動接手訊息。stage 呼叫
一律用假 runner 注入,不碰 subprocess/ffmpeg/whisper。

跑法:
    python3 scripts/tests/test_precut.py
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audio"))
from precut import (detect_material, plan_stages, run_pipeline,  # noqa: E402
                    MATERIAL_TRACKS, MATERIAL_MIXDOWN, Stage)


def args_ns(**over) -> argparse.Namespace:
    base = dict(force=False, num_speakers=None, context=None, language="zh")
    base.update(over)
    return SimpleNamespace(**base)


def touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


class TestDetectMaterial(unittest.TestCase):
    """luna 守門發現：EP18 的 tracks/ 是指到外接硬碟的 symlink，「目錄存在即分軌」
    在 symlink 斷鏈/空目錄時會誤判——本類別對照三種實際/合成佈局逐一驗證。"""

    def test_tracks_dir_with_real_file_is_tracks_line(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            touch(d / "tracks" / "1_Mars.wav")
            self.assertEqual(detect_material(d), MATERIAL_TRACKS)

    def test_no_tracks_dir_is_mixdown_line(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(detect_material(Path(t)), MATERIAL_MIXDOWN)

    def test_tracks_file_not_dir_does_not_count(self) -> None:
        """`tracks` 是檔案不是目錄(手誤)不該被當成分軌線。"""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "tracks").write_text("oops", encoding="utf-8")
            self.assertEqual(detect_material(d), MATERIAL_MIXDOWN)

    def test_ep16_layout_symlinks_to_real_files_is_tracks(self) -> None:
        """複刻 EP16 實物佈局：tracks/1_Mars.WAV -> 外接硬碟真實檔案(symlink)。"""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            external = d.parent / (Path(t).name + "_external")
            external.mkdir()
            targets = {"1_Mars.WAV": "MIC1_Mars.WAV", "2_Sarah.WAV": "MIC3_Sarah.WAV",
                      "3_KIN.WAV": "MIC4_KIN.WAV"}
            tracks = d / "tracks"
            tracks.mkdir()
            for link_name, real_name in targets.items():
                real = external / real_name
                touch(real)
                os.symlink(real, tracks / link_name)
            self.assertEqual(detect_material(d), MATERIAL_TRACKS)

    def test_ep18_layout_symlinks_to_real_files_is_tracks(self) -> None:
        """複刻 EP18 實物佈局:同款 symlink-to-external 結構,講者名大小寫不同。"""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            external = d.parent / (Path(t).name + "_external18")
            external.mkdir()
            targets = {"1_Mars.WAV": "MIC1_Mars.WAV", "2_Sarah.WAV": "MIC3_Sarah.WAV",
                      "3_KIN.WAV": "MIC4_Kin.WAV"}
            tracks = d / "tracks"
            tracks.mkdir()
            for link_name, real_name in targets.items():
                real = external / real_name
                touch(real)
                os.symlink(real, tracks / link_name)
            self.assertEqual(detect_material(d), MATERIAL_TRACKS)

    def test_synthetic_empty_tracks_dir_is_mixdown_with_warning(self) -> None:
        """合成佈局:tracks/ 存在但是空的(從未 ingest 過)→ 混音線,並印警告。"""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "tracks").mkdir()
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                material = detect_material(d)
            self.assertEqual(material, MATERIAL_MIXDOWN)
            self.assertIn("沒有可用的 .wav", buf.getvalue())

    def test_dangling_symlink_only_is_mixdown_with_warning(self) -> None:
        """tracks/ 只有斷鏈 symlink(外接硬碟沒插上)→ 混音線,並印警告。"""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            tracks = d / "tracks"
            tracks.mkdir()
            os.symlink(d / "does-not-exist" / "1_Mars.WAV", tracks / "1_Mars.WAV")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                material = detect_material(d)
            self.assertEqual(material, MATERIAL_MIXDOWN)
            self.assertIn("沒有可用的 .wav", buf.getvalue())

    def test_mixed_dangling_and_real_symlink_is_tracks(self) -> None:
        """一軌斷鏈、其他軌正常(外接硬碟只掉了一顆)→ 仍算分軌線(至少一個可用)。"""
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            external = d.parent / (Path(t).name + "_mixed")
            external.mkdir()
            real = external / "MIC1_Mars.WAV"
            touch(real)
            tracks = d / "tracks"
            tracks.mkdir()
            os.symlink(real, tracks / "1_Mars.WAV")
            os.symlink(d / "gone" / "MIC3_Sarah.WAV", tracks / "2_Sarah.WAV")
            self.assertEqual(detect_material(d), MATERIAL_TRACKS)


class TestPlanStagesMixdown(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        touch(self.d / "source.m4a")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_ingest_no_pertrack_stage(self) -> None:
        material, stages = plan_stages(self.d, args_ns())
        self.assertEqual(material, MATERIAL_MIXDOWN)
        names = [s.name for s in stages]
        self.assertFalse(any("ingest" in n for n in names))
        self.assertFalse(any("pertrack" in n for n in names))

    def test_stage_order_is_transcribe_diarize_prosody_cutplan(self) -> None:
        _, stages = plan_stages(self.d, args_ns())
        names = [s.name for s in stages]
        self.assertEqual(len(names), 4)
        self.assertIn("transcribe", names[0])
        self.assertIn("diarize", names[1])
        self.assertIn("prosody", names[2])
        self.assertIn("cutplan", names[3])

    def test_transcribe_targets_source_media_via_audio_venv(self) -> None:
        _, stages = plan_stages(self.d, args_ns())
        cmd = stages[0].cmd
        self.assertIn(".venv-audio", cmd[0])
        self.assertIn(str(self.d / "source.m4a"), cmd)
        self.assertIn("--language", cmd)
        self.assertIn("zh", cmd)

    def test_diarize_is_full_pyannote_not_from_tracks(self) -> None:
        _, stages = plan_stages(self.d, args_ns())
        cmd = stages[1].cmd
        self.assertNotIn("--from-tracks", cmd)
        self.assertIn(".venv-audio", cmd[0])

    def test_num_speakers_passthrough_only_when_given(self) -> None:
        _, stages = plan_stages(self.d, args_ns(num_speakers=2))
        self.assertIn("--num-speakers", stages[1].cmd)
        self.assertIn("2", stages[1].cmd)
        _, stages2 = plan_stages(self.d, args_ns())
        self.assertNotIn("--num-speakers", stages2[1].cmd)

    def test_context_included_only_if_file_exists(self) -> None:
        _, stages = plan_stages(self.d, args_ns())
        self.assertNotIn("--context", stages[0].cmd)
        touch(self.d / "context.txt")
        _, stages2 = plan_stages(self.d, args_ns())
        self.assertIn("--context", stages2[0].cmd)

    def test_no_source_media_raises(self) -> None:
        """混音線找不到 source.<ext> 要讓呼叫端能接住轉成乾淨的 FAIL,不是裸 traceback。"""
        with tempfile.TemporaryDirectory() as t2:
            with self.assertRaises(FileNotFoundError):
                plan_stages(Path(t2), args_ns())


class TestPlanStagesTracks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "tracks").mkdir()
        touch(self.d / "tracks" / "Mars.wav")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_six_stage_order(self) -> None:
        material, stages = plan_stages(self.d, args_ns())
        self.assertEqual(material, MATERIAL_TRACKS)
        names = [s.name for s in stages]
        self.assertEqual(len(names), 6)
        self.assertIn("ingest", names[0])
        self.assertIn("transcribe", names[1])
        self.assertIn("diarize", names[2])
        self.assertIn("prosody", names[3])
        self.assertIn("cutplan", names[4])
        self.assertIn("pertrack", names[5])

    def test_transcribe_targets_mixdown_source_wav(self) -> None:
        """分軌線的轉錄輸入是 ingest 產出的 source.wav(混音),不是任一單軌。"""
        _, stages = plan_stages(self.d, args_ns())
        self.assertIn(str(self.d / "source.wav"), stages[1].cmd)

    def test_diarize_uses_from_tracks_zero_model(self) -> None:
        _, stages = plan_stages(self.d, args_ns())
        cmd = stages[2].cmd
        self.assertIn("--from-tracks", cmd)
        self.assertNotIn(".venv-audio", cmd[0])  # 零模型,任何 python 可跑

    def test_force_passes_force_down_to_ingest(self) -> None:
        _, stages = plan_stages(self.d, args_ns(force=True))
        self.assertIn("--force", stages[0].cmd)
        _, stages2 = plan_stages(self.d, args_ns())
        self.assertNotIn("--force", stages2[0].cmd)

    def test_num_speakers_ignored_on_tracks_line(self) -> None:
        """分軌線講者=軌名=真名,--num-speakers 對 --from-tracks 沒有意義。"""
        _, stages = plan_stages(self.d, args_ns(num_speakers=3))
        self.assertNotIn("--num-speakers", stages[2].cmd)


class TestRunPipelineIdempotency(unittest.TestCase):
    def test_done_stage_is_skipped(self) -> None:
        calls = []
        stages = [Stage("a", ["cmd-a"], done=lambda: True),
                  Stage("b", ["cmd-b"], done=lambda: False)]
        rc = run_pipeline(stages, force=False,
                          runner=lambda cmd: calls.append(cmd) or SimpleNamespace(returncode=0))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [["cmd-b"]])

    def test_force_reruns_even_done_stages(self) -> None:
        calls = []
        stages = [Stage("a", ["cmd-a"], done=lambda: True),
                  Stage("b", ["cmd-b"], done=lambda: True)]
        run_pipeline(stages, force=True,
                     runner=lambda cmd: calls.append(cmd) or SimpleNamespace(returncode=0))
        self.assertEqual(calls, [["cmd-a"], ["cmd-b"]])

    def test_all_stages_done_calls_runner_zero_times(self) -> None:
        calls = []
        stages = [Stage("a", ["cmd-a"], done=lambda: True)]
        rc = run_pipeline(stages, force=False,
                          runner=lambda cmd: calls.append(cmd) or SimpleNamespace(returncode=0))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])


class TestRunPipelineFailure(unittest.TestCase):
    def test_failure_stops_before_later_stages(self) -> None:
        calls = []

        def runner(cmd):
            calls.append(cmd)
            return SimpleNamespace(returncode=1 if cmd == ["cmd-a"] else 0)

        stages = [Stage("a", ["cmd-a"], done=lambda: False),
                  Stage("b", ["cmd-b"], done=lambda: False)]
        rc = run_pipeline(stages, force=False, runner=runner)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [["cmd-a"]])  # b 從沒被呼叫

    def test_failure_message_names_the_stuck_stage_and_resume_command(self) -> None:
        import io
        import contextlib

        stages = [Stage("diarize（講者分離）", ["python3", "diarize.py"], done=lambda: False)]
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = run_pipeline(stages, force=False,
                              runner=lambda cmd: SimpleNamespace(returncode=1))
        self.assertEqual(rc, 1)
        err = buf.getvalue()
        self.assertIn("diarize（講者分離）", err)
        self.assertIn("python3 diarize.py", err)

    def test_bare_int_returncode_from_runner_also_works(self) -> None:
        """runner 若直接回傳 int(不是 CompletedProcess),照樣要能判斷成敗。"""
        stages = [Stage("a", ["cmd-a"], done=lambda: False)]
        rc = run_pipeline(stages, force=False, runner=lambda cmd: 1)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
