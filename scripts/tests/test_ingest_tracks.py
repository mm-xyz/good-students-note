#!/usr/bin/env python3
"""test_ingest_tracks.py — scripts/audio/ingest_tracks.py 的驗證測試(卡 #565)。

覆蓋(依卡 body 四條驗收):
    ① synthetic fixture(程式生成正弦波/靜音 wav)全綠
    ② tracks/ 存在時產出三件產物(source.wav / audio16k.wav / speakers.json),
       speakers.json schema 與 diarize.py 寫出的相容(model/num_speakers/speakers/turns)
    ③ 各軌長度不一致(>0.1s)時 FAIL 列出各軌長度,不靜默;sample rate 不一致同 FAIL
    ④ 無 tracks/ 時明確報單軌模式 exit 0,零產物零影響

另覆蓋:--force 覆蓋規則、長度差 ≤0.1s 容忍、VAD turns 落在正確時間窗。

跑法:
    python3 scripts/tests/test_ingest_tracks.py
"""
from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR / "audio"))

import ingest_tracks  # noqa: E402

INGEST = SCRIPTS_DIR / "audio" / "ingest_tracks.py"
HAS_FFMPEG = shutil.which("ffmpeg") is not None


# ── synthetic fixture ──────────────────────────────────────────────

def write_wav(path: Path, spans: list[tuple[float, float]], duration: float,
              sr: int = 16000, freq: float = 440.0, amp: float = 0.5) -> None:
    """生成測試 wav:spans 內是正弦波(講話),其餘全靜音。16-bit mono。"""
    n = int(round(duration * sr))
    samples = bytearray()
    for i in range(n):
        t = i / sr
        active = any(a <= t < b for a, b in spans)
        v = int(amp * 32767 * math.sin(2 * math.pi * freq * t)) if active else 0
        samples += struct.pack("<h", v)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(samples))


def run_cli(session_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INGEST), "--session", str(session_dir), *extra],
        capture_output=True, text=True)


class IngestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ingest_tracks_test_"))
        self.session = self.tmp / "sessions" / "test-ep"
        self.session.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_two_tracks(self, dur_a: float = 4.0, dur_b: float = 4.0,
                        sr_a: int = 16000, sr_b: int = 16000):
        """Alice 講 0.5–1.5s,Bob 講 2.0–3.5s,其餘靜音。"""
        write_wav(self.session / "tracks" / "Alice.wav",
                  [(0.5, 1.5)], dur_a, sr=sr_a, freq=330.0)
        write_wav(self.session / "tracks" / "Bob.wav",
                  [(2.0, 3.5)], dur_b, sr=sr_b, freq=220.0)


# ── ④ 無 tracks/ = 單軌模式 ────────────────────────────────────────

class TestSingleTrackMode(IngestBase):
    def test_no_tracks_dir_exits_zero_and_touches_nothing(self):
        r = run_cli(self.session)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("單軌模式", r.stdout + r.stderr)
        self.assertEqual(sorted(p.name for p in self.session.iterdir()), [])

    def test_tracks_dir_without_wav_fails_loudly(self):
        (self.session / "tracks").mkdir()
        (self.session / "tracks" / ".DS_Store").write_bytes(b"junk")
        r = run_cli(self.session)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("單軌模式", r.stdout + r.stderr)

    def test_missing_session_dir_fails(self):
        r = run_cli(self.tmp / "nope")
        self.assertNotEqual(r.returncode, 0)


# ── ③ 驗證:長度 / sample rate 不一致 ──────────────────────────────

class TestValidation(IngestBase):
    def test_length_mismatch_fails_and_lists_durations(self):
        self.make_two_tracks(dur_a=4.0, dur_b=4.5)  # 差 0.5s > 0.1s
        r = run_cli(self.session)
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("Alice", out)
        self.assertIn("Bob", out)
        self.assertIn("4.0", out)   # 各軌長度要列出來
        self.assertIn("4.5", out)
        self.assertFalse((self.session / "source.wav").exists())
        self.assertFalse((self.session / "speakers.json").exists())

    def test_length_diff_within_tolerance_passes(self):
        self.make_two_tracks(dur_a=4.0, dur_b=4.05)  # 差 0.05s ≤ 0.1s
        r = run_cli(self.session)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_sample_rate_mismatch_fails(self):
        self.make_two_tracks(sr_a=16000, sr_b=48000)
        r = run_cli(self.session)
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("16000", out)
        self.assertIn("48000", out)
        self.assertFalse((self.session / "source.wav").exists())


# ── ② 三件產物 + schema 相容 ──────────────────────────────────────

@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class TestIngestProducts(IngestBase):
    def run_ok(self, *extra):
        r = run_cli(self.session, *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_three_products_exist(self):
        self.make_two_tracks()
        self.run_ok()
        for name in ("source.wav", "audio16k.wav", "speakers.json"):
            p = self.session / name
            self.assertTrue(p.exists() and p.stat().st_size > 0, name)

    def test_source_wav_keeps_sr_audio16k_is_16k_mono(self):
        self.make_two_tracks()
        self.run_ok()
        with wave.open(str(self.session / "source.wav"), "rb") as wf:
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getsampwidth(), 2)
            # mixdown 長度 ≈ 軌長
            self.assertAlmostEqual(wf.getnframes() / wf.getframerate(), 4.0,
                                   delta=0.15)
        with wave.open(str(self.session / "audio16k.wav"), "rb") as wf:
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)

    def test_speakers_json_schema_compatible_with_diarize(self):
        self.make_two_tracks()
        self.run_ok()
        data = json.loads((self.session / "speakers.json").read_text("utf-8"))
        # diarize.py 寫出的欄位(superset 相容)
        for key in ("model", "generated_at", "elapsed_secs",
                    "num_speakers", "speakers", "turns"):
            self.assertIn(key, data, key)
        self.assertEqual(data["num_speakers"], 2)
        self.assertEqual(sorted(data["speakers"]), ["Alice", "Bob"])
        self.assertTrue(data["turns"])
        for t in data["turns"]:
            self.assertEqual(sorted(t), ["end", "speaker", "start"])
            self.assertIsInstance(t["start"], float)
            self.assertIsInstance(t["end"], float)
            self.assertGreater(t["end"], t["start"])
        # turns 按 start 排序(diarize 同款)
        starts = [t["start"] for t in data["turns"]]
        self.assertEqual(starts, sorted(starts))

    def test_vad_turns_land_in_correct_windows(self):
        self.make_two_tracks()  # Alice 0.5–1.5s,Bob 2.0–3.5s
        self.run_ok()
        data = json.loads((self.session / "speakers.json").read_text("utf-8"))
        alice = [t for t in data["turns"] if t["speaker"] == "Alice"]
        bob = [t for t in data["turns"] if t["speaker"] == "Bob"]
        self.assertTrue(alice and bob)
        for t in alice:
            self.assertGreaterEqual(t["start"], 0.5 - 0.2)
            self.assertLessEqual(t["end"], 1.5 + 0.2)
        for t in bob:
            self.assertGreaterEqual(t["start"], 2.0 - 0.2)
            self.assertLessEqual(t["end"], 3.5 + 0.2)

    def test_existing_output_requires_force(self):
        self.make_two_tracks()
        (self.session / "source.wav").write_bytes(b"old")
        r = run_cli(self.session)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--force", r.stdout + r.stderr)
        self.assertEqual((self.session / "source.wav").read_bytes(), b"old")
        # --force 後覆蓋成功
        self.run_ok("--force")
        with wave.open(str(self.session / "source.wav"), "rb") as wf:
            self.assertGreater(wf.getnframes(), 0)


# ── VAD 單元(不需 ffmpeg)─────────────────────────────────────────

class TestVadUnit(IngestBase):
    def test_energy_vad_detects_spans(self):
        p = self.session / "tracks" / "Solo.wav"
        write_wav(p, [(1.0, 2.0), (3.0, 3.8)], 5.0)
        turns = ingest_tracks.energy_vad(p, "Solo")
        self.assertEqual(len(turns), 2)
        self.assertAlmostEqual(turns[0]["start"], 1.0, delta=0.2)
        self.assertAlmostEqual(turns[0]["end"], 2.0, delta=0.2)
        self.assertAlmostEqual(turns[1]["start"], 3.0, delta=0.2)
        self.assertAlmostEqual(turns[1]["end"], 3.8, delta=0.2)
        for t in turns:
            self.assertEqual(t["speaker"], "Solo")

    def test_energy_vad_all_silence_returns_empty(self):
        p = self.session / "tracks" / "Mute.wav"
        write_wav(p, [], 3.0)
        self.assertEqual(ingest_tracks.energy_vad(p, "Mute"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
