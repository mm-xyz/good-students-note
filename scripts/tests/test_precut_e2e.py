#!/usr/bin/env python3
"""test_precut_e2e.py — precut.py 混音線的真實全流程 smoke(卡 #679)。

跟 test_precut.py(純編排、假 runner)不同,這支**不 mock**——用
scripts/tests/fixtures/ep16 既有的 17 秒真音訊(ADR 0013 的回歸 fixture,
已避開敏感段落),真的跑 transcribe_local(mlx-whisper)→ diarize(pyannote)
→ prosody(librosa)→ cutplan prepare 一整條,驗證 precut.py 組出來的指令
真的能接起來跑通、冪等重跑真的全跳過。

只覆蓋混音線(fixture 只有 audio16k.wav,無分軌 tracks/ 素材);分軌線的
真實 ingest→pertrack_blocks 全流程沒有現成小 fixture,略過,已在
test_precut.py 用假 runner 鎖編排邏輯。

三個前提有一個不滿足就整支略過(印出理由,不假裝跑過):
    1. .venv-audio 存在(mlx-whisper/pyannote/librosa 都在裡面)
    2. fixture 音訊存在(gitignored,見 ADR 0013,跑
       scripts/tests/fixtures/build_fixtures.py 補產生)
    3. HF_TOKEN 可用(pyannote gated model 需要)

跑法:
    python3 scripts/tests/test_precut_e2e.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audio"))
from precut import AUDIO_VENV  # noqa: E402
from diarize import load_env_token  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PRECUT_SCRIPT = PROJECT_ROOT / "scripts/audio/precut.py"
FIXTURE = (PROJECT_ROOT / "scripts/tests/fixtures/ep16/case2_repeat_cut/audio16k.wav")

HAS_VENV = AUDIO_VENV.exists()
HAS_FIXTURE = FIXTURE.exists()
HAS_TOKEN = bool(load_env_token())
SKIP_REASON = (
    ("" if HAS_VENV else "[.venv-audio 不存在;安裝見 requirements-audio.txt] ")
    + ("" if HAS_FIXTURE else f"[fixture 音訊不存在:{FIXTURE};"
       "跑 scripts/tests/fixtures/build_fixtures.py 補產生] ")
    + ("" if HAS_TOKEN else "[HF_TOKEN 不可用;diarize 的 pyannote gated model 需要它] ")
).strip()


@unittest.skipUnless(HAS_VENV and HAS_FIXTURE and HAS_TOKEN,
                     SKIP_REASON or "前提不滿足")
class TestPrecutMixdownE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sdir = Path(self.tmp.name) / "sess"
        self.sdir.mkdir()
        shutil.copy2(FIXTURE, self.sdir / "source.wav")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PRECUT_SCRIPT), "--session", str(self.sdir)],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300)

    def test_full_pipeline_produces_cutplan(self) -> None:
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.sdir / "cutplan.md").exists())
        self.assertTrue((self.sdir / "cutplan.json").exists())
        self.assertTrue((self.sdir / "transcript.srt").exists())
        self.assertTrue((self.sdir / "words.json").exists())
        self.assertTrue((self.sdir / "transcript.speakers.srt").exists())
        self.assertTrue((self.sdir / "prosody.json").exists())
        self.assertTrue((self.sdir / "highlights.md").exists())
        self.assertIn("初剪完成", r.stdout)

    def test_rerun_is_idempotent_and_fast(self) -> None:
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self._run()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("已有產物,略過", second.stdout)
        self.assertNotIn("▶  transcribe", second.stdout)
        self.assertNotIn("▶  diarize", second.stdout)
        self.assertNotIn("▶  prosody", second.stdout)
        self.assertNotIn("▶  cutplan", second.stdout)


if __name__ == "__main__":
    unittest.main()
