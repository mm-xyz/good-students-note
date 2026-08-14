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

四個前提有一個不滿足就整支略過(印出理由,不假裝跑過):
    1. .venv-audio 存在(mlx-whisper/pyannote/librosa 都在裡面)
    2. fixture 音訊存在(gitignored,見 ADR 0013,跑
       scripts/tests/fixtures/build_fixtures.py 補產生)
    3. HF_TOKEN 可用(pyannote gated model 需要)
    4. **Metal device 真的可用**(luna 守門 Major #1:headless/無 GPU 環境
       裝得到 mlx 套件,但 transcribe_local.py 底層 mlx.core 呼叫 Metal 會撞
       `[metal::load_device] No Metal device available` 直接 exit -6;
       前三項前提俱在也擋不住這個。用 stage 實際依賴的那套 API 探測
       ——`.venv-audio` 起一個子行程呼叫 `mlx.core.metal.is_available()`,
       不猜平台/不猜有沒有 GPU,也不做 CPU fallback(那是改 stage 行為,
       超出本卡範圍,見 ADR 0015/#679 討論)。

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


def probe_metal_available(venv_python: Path, timeout: float = 30.0) -> bool:
    """探測 `.venv-audio` 的 mlx 是否有真的 Metal device 可用。

    探的是 transcribe_local.py 實際依賴的那套 API(`mlx.core.metal`)本身,
    不是猜 `sys.platform`/有沒有獨立 GPU——headless CI 常常裝得到套件但
    沒有 Metal device,`mlx_whisper.transcribe()` 會在載入模型時直接原生
    當掉(`[metal::load_device] No Metal device available`,exit -6),不是
    Python 例外,接不住,只能在跑之前先探測。
    """
    if not venv_python.exists():
        return False
    try:
        r = subprocess.run(
            [str(venv_python), "-c",
             "import sys; import mlx.core as mx; "
             "sys.exit(0 if mx.metal.is_available() else 1)"],
            capture_output=True, timeout=timeout)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def build_skip_reason(has_venv: bool, has_fixture: bool, has_token: bool,
                      has_metal: bool) -> str:
    """組 skip 理由字串,拆成獨立函式方便測試模擬各種前提缺席組合
    (含 luna 的 headless 案例:venv/fixture/token 三前提俱在,只有
    Metal 探測不過)。"""
    return (
        ("" if has_venv else "[.venv-audio 不存在;安裝見 requirements-audio.txt] ")
        + ("" if has_fixture else f"[fixture 音訊不存在:{FIXTURE};"
           "跑 scripts/tests/fixtures/build_fixtures.py 補產生] ")
        + ("" if has_token else "[HF_TOKEN 不可用;diarize 的 pyannote gated model 需要它] ")
        + ("" if has_metal else "[Metal device 不可用(mlx.core.metal.is_available()=False,"
           "常見於 headless/無 GPU 環境);transcribe 階段會原生當掉,無法真跑] ")
    ).strip()


HAS_VENV = AUDIO_VENV.exists()
HAS_FIXTURE = FIXTURE.exists()
HAS_TOKEN = bool(load_env_token())
HAS_METAL = probe_metal_available(AUDIO_VENV)
SKIP_REASON = build_skip_reason(HAS_VENV, HAS_FIXTURE, HAS_TOKEN, HAS_METAL)


class TestProbeMetalAvailable(unittest.TestCase):
    """探測邏輯本身的行為鎖定,不受本機有沒有 Metal 影響,一律會跑
    (不像下面的 E2E 類別會被 skip)——用假 interpreter 模擬三種真實狀態,
    對應 luna 回報的 headless 案例(`.venv-audio` 存在、套件裝得到,
    但 `mx.metal.is_available()` 回 False)。"""

    def test_missing_interpreter_is_unavailable(self) -> None:
        """`.venv-audio` 整個不存在(OSError 分支)。"""
        self.assertFalse(probe_metal_available(Path("/no/such/interpreter-xyz")))

    def test_interpreter_reports_metal_unavailable(self) -> None:
        """模擬 luna 的 headless 案例:interpreter 跑得動,但探測腳本判定
        沒有 Metal device(對應 `mx.metal.is_available()` 回 False → exit 1)。
        用一支忽略所有參數、固定 exit 1 的假 shell script 頂替 venv_python,
        不需要真的裝 mlx 就能鎖住「exit 1 → False」這個判讀。"""
        with tempfile.TemporaryDirectory() as t:
            fake = Path(t) / "fake-python-no-metal"
            fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            fake.chmod(0o755)
            self.assertFalse(probe_metal_available(fake))

    def test_interpreter_reports_metal_available(self) -> None:
        """對照組:假 interpreter 回 exit 0(有 Metal)要判 True,不是隨口猜。"""
        with tempfile.TemporaryDirectory() as t:
            fake = Path(t) / "fake-python-has-metal"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            self.assertTrue(probe_metal_available(fake))

    def test_timeout_counts_as_unavailable(self) -> None:
        """探測腳本卡住(理論上不該發生,但 timeout 要保底當不可用,不是掛住整支測試)。"""
        with tempfile.TemporaryDirectory() as t:
            fake = Path(t) / "fake-python-hangs"
            fake.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
            fake.chmod(0o755)
            self.assertFalse(probe_metal_available(fake, timeout=0.2))


class TestSkipReasonHeadlessSimulation(unittest.TestCase):
    """headless 模擬:venv/fixture/token 三前提俱在、只有 Metal 探測不過
    (luna 回報的實際狀況)→ skip 訊息要明確點名 Metal,不能只印一句
    「前提不滿足」讓人猜。"""

    def test_all_present_except_metal_names_metal_in_message(self) -> None:
        reason = build_skip_reason(has_venv=True, has_fixture=True,
                                   has_token=True, has_metal=False)
        self.assertIn("Metal device 不可用", reason)
        self.assertIn("mlx.core.metal.is_available()=False", reason)
        self.assertNotIn(".venv-audio 不存在", reason)
        self.assertNotIn("fixture 音訊不存在", reason)
        self.assertNotIn("HF_TOKEN 不可用", reason)

    def test_all_present_including_metal_is_empty_reason(self) -> None:
        self.assertEqual(build_skip_reason(True, True, True, True), "")

    def test_multiple_missing_all_listed(self) -> None:
        reason = build_skip_reason(has_venv=False, has_fixture=True,
                                   has_token=False, has_metal=False)
        self.assertIn(".venv-audio 不存在", reason)
        self.assertIn("HF_TOKEN 不可用", reason)
        self.assertIn("Metal device 不可用", reason)
        self.assertNotIn("fixture 音訊不存在", reason)


@unittest.skipUnless(HAS_VENV and HAS_FIXTURE and HAS_TOKEN and HAS_METAL,
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
