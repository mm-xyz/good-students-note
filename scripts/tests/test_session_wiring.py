#!/usr/bin/env python3
"""test_session_wiring.py — session.py 多軌 ingest 接線的行為測試(卡 #572)。

不跑真 ASR/ffmpeg:monkeypatch session.run 記錄指令並偽造各腳本產物,
鎖的是接線本身(ADR 0003 進料端):
    ① 輸入是含 tracks/ 的 session 目錄 → 先跑 ingest_tracks,再用 mixdown 的
       source.wav 走同一條 pipeline(單一 pipeline,不 fork)
    ② diarize stage 偵測到 ingest 版 speakers.json(source=tracks)→ 改走
       --from-tracks(零模型、任何 python 可跑),不碰 pyannote
    ③ 單檔輸入行為不變(不跑 ingest、metadata.multitrack=False)
    ④ 目錄輸入但沒 tracks/ → 明確拒收;已有 transcript.srt → 拒重跑

跑法:
    python3 scripts/tests/test_session_wiring.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import session  # noqa: E402

SRT_MIN = "1\n00:00:00,000 --> 00:00:01,000\n哈囉測試\n"


def make_args(audio: str, **over) -> argparse.Namespace:
    """session.py new 的 argparse Namespace(預設值與 CLI 對齊)。"""
    base = dict(cmd="new", audio=audio, context=None, domain=None, identity=None,
                keywords=None, enhance=False, images=None, asr="groq",
                diarize=False, prosody=False, cut=False, num_speakers=None,
                frames=False, stop_at="phase-b", skip_phase_b=False,
                structured_srt=False, engine="none", dry_run=False)
    base.update(over)
    return argparse.Namespace(**base)


class TestIngestGroundTruthHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="session_wiring_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, payload: str):
        (self.tmp / "speakers.json").write_text(payload, encoding="utf-8")

    def test_ingest_json_detected(self):
        self._write(json.dumps({"source": "tracks", "turns": []}))
        self.assertTrue(session.ingest_ground_truth(self.tmp))

    def test_pyannote_json_not_ground_truth(self):
        self._write(json.dumps({"model": "pyannote/x", "turns": []}))
        self.assertFalse(session.ingest_ground_truth(self.tmp))

    def test_missing_or_corrupt_not_ground_truth(self):
        self.assertFalse(session.ingest_ground_truth(self.tmp))
        self._write("{not json")
        self.assertFalse(session.ingest_ground_truth(self.tmp))


class SessionWiringBase(unittest.TestCase):
    """monkeypatch session.run + SESSIONS_DIR,記錄指令、偽造產物。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="session_wiring_"))
        self.sessions = self.tmp / "sessions"
        self.sessions.mkdir()
        self.calls: list[list[str]] = []
        self._orig_run = session.run
        self._orig_sessions_dir = session.SESSIONS_DIR
        session.run = self._fake_run
        session.SESSIONS_DIR = self.sessions

    def tearDown(self):
        session.run = self._orig_run
        session.SESSIONS_DIR = self._orig_sessions_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── fake 各 pipeline 腳本的最小產物 ──
    def _fake_run(self, cmd, cwd=None, check=True):
        self.calls.append([str(c) for c in cmd])
        script = str(cmd[1]) if len(cmd) > 1 else ""
        if "ingest_tracks.py" in script:
            sdir = Path(str(cmd[cmd.index("--session") + 1]))
            (sdir / "source.wav").write_bytes(b"RIFF-fake-mixdown")
            (sdir / "audio16k.wav").write_bytes(b"RIFF-fake-16k")
            (sdir / "speakers.json").write_text(json.dumps({
                "model": "ingest-tracks/energy-vad-v1", "source": "tracks",
                "num_speakers": 2, "speakers": ["Alice", "Bob"], "turns": [],
            }), encoding="utf-8")
        elif "groq_transcribe" in script:
            out_dir = Path(str(cmd[3]))
            (out_dir / "source.srt").write_text(SRT_MIN, encoding="utf-8")
        elif "qaqc_srt" in script:
            out = Path(str(cmd[cmd.index("-o") + 1]))
            out.write_text(SRT_MIN, encoding="utf-8")
        elif "diarize.py" in script:
            sdir = Path(str(cmd[cmd.index("--session") + 1]))
            (sdir / "transcript.speakers.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n[Alice] 哈囉測試\n",
                encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    def _script_calls(self, needle: str) -> list[list[str]]:
        return [c for c in self.calls if any(needle in part for part in c)]

    def make_tracks_session(self, name: str = "2026-07-31_ep-test") -> Path:
        sdir = self.sessions / name
        (sdir / "tracks").mkdir(parents=True)
        (sdir / "tracks" / "Alice.wav").write_bytes(b"RIFF-fake")
        (sdir / "tracks" / "Bob.wav").write_bytes(b"RIFF-fake")
        return sdir


class TestMultitrackWiring(SessionWiringBase):
    def test_tracks_dir_runs_ingest_then_asr_then_from_tracks_diarize(self):
        sdir = self.make_tracks_session()
        session.new_session(make_args(str(sdir), diarize=True))

        # ① ingest 先跑,且在轉錄之前
        ingest = self._script_calls("ingest_tracks.py")
        asr = self._script_calls("groq_transcribe")
        self.assertEqual(len(ingest), 1)
        self.assertEqual(len(asr), 1)
        self.assertLess(self.calls.index(ingest[0]), self.calls.index(asr[0]))

        # 轉錄吃 mixdown 的 source.wav
        self.assertIn(str(sdir / "source.wav"), asr[0])
        self.assertTrue((sdir / "transcript.srt").exists())

        # ② diarize 走 --from-tracks(零模型,python3 即可,不進 .venv-audio)
        dia = self._script_calls("diarize.py")
        self.assertEqual(len(dia), 1)
        self.assertIn("--from-tracks", dia[0])
        self.assertEqual(dia[0][0], "python3")

        # metadata 記錄多軌與 from-tracks 結果
        meta = json.loads((sdir / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["multitrack"])
        self.assertEqual(meta["audio_analysis"]["diarize"], "done_from_tracks")

    def test_dir_without_tracks_refused(self):
        sdir = self.sessions / "2026-07-31_no-tracks"
        sdir.mkdir()
        with self.assertRaises(SystemExit):
            session.new_session(make_args(str(sdir)))
        self.assertEqual(self.calls, [])   # 什麼都不該跑

    def test_existing_transcript_refused(self):
        sdir = self.make_tracks_session()
        (sdir / "transcript.srt").write_text(SRT_MIN, encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            session.new_session(make_args(str(sdir)))
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.calls, [])


class TestSingleFileUnchanged(SessionWiringBase):
    def test_single_file_never_runs_ingest(self):
        audio = self.tmp / "ep.m4a"
        audio.write_bytes(b"fake-audio")
        session.new_session(make_args(str(audio)))

        self.assertEqual(self._script_calls("ingest_tracks.py"), [])
        sdirs = [p for p in self.sessions.iterdir() if p.is_dir()]
        self.assertEqual(len(sdirs), 1)
        meta = json.loads((sdirs[0] / "metadata.json").read_text(encoding="utf-8"))
        self.assertFalse(meta["multitrack"])
        self.assertEqual(meta["source_audio"], "ep.m4a")

    def test_single_file_diarize_without_ingest_json_uses_pyannote_path(self):
        # 沒有 ingest ground truth → diarize 照舊走 .venv-audio 的 pyannote 路徑
        # (.venv-audio 不存在時整條音訊分析線 skip,不會誤走 --from-tracks)
        audio = self.tmp / "ep.m4a"
        audio.write_bytes(b"fake-audio")
        session.new_session(make_args(str(audio), diarize=True))
        for c in self._script_calls("diarize.py"):
            self.assertNotIn("--from-tracks", c)


if __name__ == "__main__":
    unittest.main(verbosity=2)
