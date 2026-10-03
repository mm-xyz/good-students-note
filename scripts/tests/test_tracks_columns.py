#!/usr/bin/env python3
"""test_tracks_columns.py — `line=mixdown audio=tracks` 的軌欄格式、預設值與包絡。

ADR-2026-10-03-audio-tracks-line。鎖的是:
  · 軌欄格式 ` ⟦Mars● Sarah○ Kin○⟧`(行尾、可拆、可還原,不碰 block 文字)
  · add_columns 冪等、不覆蓋人工改過的欄位與勾選、⚙ 補 audio=tracks
  · 預設「最大聲那一軌」用**校準後**的相對值,不是原始 RMS
  · 包絡:hold(空隙沿用前一個 block)、lead(新開的軌提早一點開)、bus 時間軸

跑法:
    python3 scripts/tests/test_tracks_columns.py
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

import numpy as np  # noqa: E402

from tracks_columns import (add_columns, calibrate, format_col,  # noqa: E402
                            on_intervals, parse_col, pick_loudest,
                            segment_envelopes, split_col, track_name,
                            ColumnError)

NAMES = ["Mars", "Sarah", "Kin"]


class TestColumnFormat(unittest.TestCase):
    def test_format_is_one_cell_per_track_in_order(self):
        self.assertEqual(format_col({"Mars": True, "Sarah": False, "Kin": False}),
                         " ⟦Mars● Sarah○ Kin○⟧")

    def test_split_takes_only_the_trailing_column(self):
        body = "[Kin] 我是King。 ← 理由 ⟦Mars○ Sarah○ Kin●⟧"
        rest, inner = split_col(body)
        self.assertEqual(rest, "[Kin] 我是King。 ← 理由")
        self.assertEqual(inner, "Mars○ Sarah○ Kin●")

    def test_split_without_column(self):
        self.assertEqual(split_col("[Kin] 我是King。"), ("[Kin] 我是King。", None))

    def test_parse_roundtrip(self):
        st = parse_col("Mars● Sarah○ Kin●", NAMES)
        self.assertEqual(list(st), NAMES)
        self.assertEqual(st, {"Mars": True, "Sarah": False, "Kin": True})
        self.assertEqual(format_col(st), " ⟦Mars● Sarah○ Kin●⟧")

    def test_parse_rejects_unknown_missing_duplicate_or_bad_mark(self):
        for bad in ("Mars● Sarah○", "Mars● Sarah○ Kin○ Bob●",
                    "Mars● Mars○ Kin○", "Mars● Sarah? Kin○", "Mars●Sarah○ Kin○",
                    "Sarah○ Mars● Kin○"):
            with self.subTest(bad=bad), self.assertRaises(ColumnError):
                parse_col(bad, NAMES)

    def test_track_name_strips_numeric_prefix(self):
        self.assertEqual(track_name(Path("tracks/1_Mars.WAV")), "Mars")
        self.assertEqual(track_name(Path("tracks/3_Kin.wav")), "Kin")
        self.assertEqual(track_name(Path("tracks/Sarah.wav")), "Sarah")


PLAN = """# Cutplan — t

> 說明

## ⚙ line=mixdown template=水星貓的生活實驗室_v1

## 開場
- [x] B0001 [0:00–0:01] [Sarah] 嗨, ← 理由
- [ ] B0002 [0:01–0:02] [Kin] 我是~~King~~。
- [ ] G0001 [0:02–0:05] ⬜ 空白/非語音 3.0s(靜音;勾選=保留原聲)
## 🎬 集錦
- [x] B0001 [0:00–0:01] [Sarah] 嗨, ← 理由
## ➕ raw/x.WAV gain=auto
- [x] S0001 [0:00–0:01] [Mars] 補錄。
"""

DEFAULTS = {"B0001": {"Mars": False, "Sarah": True, "Kin": False},
            "B0002": {"Mars": False, "Sarah": False, "Kin": True},
            "G0001": {"Mars": True, "Sarah": True, "Kin": True}}


class TestAddColumns(unittest.TestCase):
    def test_adds_column_to_b_and_g_rows_and_audio_key(self):
        out, st = add_columns(PLAN, DEFAULTS, NAMES)
        lines = out.splitlines()
        self.assertIn("## ⚙ line=mixdown template=水星貓的生活實驗室_v1 audio=tracks",
                      lines)
        self.assertIn("- [x] B0001 [0:00–0:01] [Sarah] 嗨, ← 理由"
                      " ⟦Mars○ Sarah● Kin○⟧", lines)
        self.assertIn("- [ ] B0002 [0:01–0:02] [Kin] 我是~~King~~。"
                      " ⟦Mars○ Sarah○ Kin●⟧", lines)
        self.assertTrue(any(l.startswith("- [ ] G0001") and
                            l.endswith("⟦Mars● Sarah● Kin●⟧") for l in lines))
        # 🎬 集錦的複製行也要有欄位;➕ 補錄的 S 列不分軌
        self.assertEqual(sum(1 for l in lines if l.startswith("- [x] B0001")
                             and l.endswith("⟦Mars○ Sarah● Kin○⟧")), 2)
        self.assertIn("- [x] S0001 [0:00–0:01] [Mars] 補錄。", lines)
        self.assertEqual(st["added"], 4)
        self.assertTrue(st["audio_added"])

    def test_idempotent(self):
        once, _ = add_columns(PLAN, DEFAULTS, NAMES)
        twice, st = add_columns(once, DEFAULTS, NAMES)
        self.assertEqual(once, twice)
        self.assertEqual(st["added"], 0)
        self.assertFalse(st["audio_added"])

    def test_never_overwrites_human_edits(self):
        once, _ = add_columns(PLAN, DEFAULTS, NAMES)
        # 人工:B0002 改成 Mars+Kin、勾選打開;預設值後來變了也不能蓋
        edited = once.replace("- [ ] B0002 [0:01–0:02] [Kin] 我是~~King~~。"
                              " ⟦Mars○ Sarah○ Kin●⟧",
                              "- [x] B0002 [0:01–0:02] [Kin] 我是~~King~~。"
                              " ⟦Mars● Sarah○ Kin●⟧")
        other = {k: {n: True for n in NAMES} for k in DEFAULTS}
        again, st = add_columns(edited, other, NAMES)
        self.assertEqual(again, edited)
        self.assertEqual(st["added"], 0)

    def test_only_body_text_outside_column_is_untouched(self):
        out, _ = add_columns(PLAN, DEFAULTS, NAMES)
        stripped = "\n".join(split_col(l)[0] if l.startswith("- [") else l
                             for l in out.splitlines())
        orig = PLAN.replace(
            "## ⚙ line=mixdown template=水星貓的生活實驗室_v1",
            "## ⚙ line=mixdown template=水星貓的生活實驗室_v1 audio=tracks")
        self.assertEqual(stripped + "\n", orig)

    def test_refuses_pertrack_or_other_audio(self):
        for cfg in ("## ⚙ line=pertrack", "## ⚙ line=mixdown audio=mixdown"):
            with self.subTest(cfg=cfg), self.assertRaises(ColumnError):
                add_columns(PLAN.replace(
                    "## ⚙ line=mixdown template=水星貓的生活實驗室_v1", cfg),
                    DEFAULTS, NAMES)

    def test_refuses_without_config_line(self):
        with self.assertRaises(ColumnError):
            add_columns(PLAN.replace(
                "## ⚙ line=mixdown template=水星貓的生活實驗室_v1\n", ""),
                DEFAULTS, NAMES)

    def test_existing_column_with_wrong_tracks_is_an_error(self):
        bad = PLAN.replace("[Sarah] 嗨, ← 理由\n- [ ]",
                           "[Sarah] 嗨, ← 理由 ⟦Mars● Bob○⟧\n- [ ]", 1)
        with self.assertRaises(ColumnError):
            add_columns(bad, DEFAULTS, NAMES)


class TestPickLoudest(unittest.TestCase):
    def test_calibrated_choice_beats_raw_rms(self):
        """Mars 麥增益高、底噪也高:raw dB Mars 比較大,但那是串音;
        各自的動態範圍裡 Kin 已經接近他自己講話的電平。"""
        floors = [-40.0, -70.0, -70.0]
        refs = [-10.0, -40.0, -40.0]
        block = [-25.0, -68.0, -42.0]
        self.assertEqual(int(np.argmax(block)), 0)          # 原始 RMS 會挑錯
        self.assertEqual(pick_loudest(block, floors, refs), 2)

    def test_calibrate_floor_and_ref_per_track(self):
        rng = np.random.default_rng(0)
        quiet = rng.normal(-70, 1, 900)
        loud = rng.normal(-30, 1, 100)
        L = np.vstack([np.concatenate([quiet, loud]),
                       np.concatenate([quiet + 30, loud + 10])])
        floors, refs = calibrate(L)
        self.assertAlmostEqual(floors[0], -71.3, delta=1.0)
        self.assertAlmostEqual(floors[1], -41.3, delta=1.0)
        self.assertGreater(refs[0], -32)
        self.assertGreater(refs[1], -22)


def items(*rows):
    return [{"start": a, "end": b, "on": {n: (n in on) for n in NAMES}}
            for a, b, on in rows]


class TestOnIntervals(unittest.TestCase):
    def test_hold_across_gap_and_lead_for_new_track(self):
        it = items((0.0, 1.0, {"Mars"}), (1.5, 2.5, {"Kin"}))
        self.assertEqual(on_intervals(it, "Mars", lead=0.2),
                         [[-math.inf, 1.5]])
        self.assertEqual(on_intervals(it, "Kin", lead=0.2),
                         [[1.3, math.inf]])
        self.assertEqual(on_intervals(it, "Sarah", lead=0.2), [])

    def test_lead_never_reaches_back_into_previous_block(self):
        it = items((0.0, 1.4, {"Mars"}), (1.5, 2.5, {"Kin"}))
        self.assertEqual(on_intervals(it, "Kin", lead=0.2), [[1.4, math.inf]])

    def test_overlap_keeps_both_until_own_end(self):
        it = items((0.0, 2.0, {"Mars"}), (1.5, 3.0, {"Kin"}))
        self.assertEqual(on_intervals(it, "Mars", lead=0.2), [[-math.inf, 2.0]])
        self.assertEqual(on_intervals(it, "Kin", lead=0.2), [[1.5, math.inf]])

    def test_same_track_on_merges(self):
        it = items((0.0, 1.0, {"Mars"}), (1.5, 2.0, {"Mars", "Kin"}),
                   (2.5, 3.0, {"Kin"}))
        self.assertEqual(on_intervals(it, "Mars", lead=0.2), [[-math.inf, 2.5]])
        self.assertEqual(on_intervals(it, "Kin", lead=0.2), [[1.3, math.inf]])


class TestSegmentEnvelopes(unittest.TestCase):
    def test_bus_timeline_and_gains(self):
        sr = 1000
        it = items((10.0, 11.0, {"Mars"}), (11.5, 12.5, {"Kin"}))
        segs = [{"a": 10.0, "b": 12.5, "titems": it},
                {"a": 20.0, "b": 21.0,
                 "titems": items((20.0, 21.0, {"Mars", "Sarah", "Kin"}))}]
        env = segment_envelopes(segs, NAMES, duck_db=-27.0, lead=0.0, sr=sr)
        self.assertEqual(env["Mars"], [(0.0, 1.5, 0.0), (1.5, 2.5, -27.0),
                                       (2.5, 3.5, 0.0)])
        self.assertEqual(env["Kin"], [(0.0, 1.5, -27.0), (1.5, 3.5, 0.0)])
        self.assertEqual(env["Sarah"], [(0.0, 2.5, -27.0), (2.5, 3.5, 0.0)])


def write_wav(path: Path, sr: int, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def write_speakers(sdir: Path, names, with_tracks: bool = True) -> None:
    d = {"num_speakers": len(names), "speakers": sorted(names)}
    if with_tracks:
        d["tracks"] = {n: {"file": f"tracks/x_{n}.wav"} for n in names}
    (sdir / "speakers.json").write_text(json.dumps(d), encoding="utf-8")


class TestValidateTracks(unittest.TestCase):
    """驗收 F-2:audio=tracks 的分軌必須跟講者數一致、同取樣率、與 source 等長。
    缺軌/多軌/長度不符一律講出原因,不靜默補零。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.s = Path(self.td.name)
        (self.s / "tracks").mkdir()
        sr = 8000
        z = np.zeros(int(2.0 * sr))
        write_wav(self.s / "source.wav", sr, z)
        for i, n in enumerate(NAMES, 1):
            write_wav(self.s / "tracks" / f"{i}_{n}.wav", sr, z)
        write_speakers(self.s, NAMES)

    def tearDown(self):
        self.td.cleanup()

    def errs(self):
        from tracks_columns import track_files, validate_tracks
        return validate_tracks(self.s, track_files(self.s), self.s / "source.wav")

    def test_ok(self):
        self.assertEqual(self.errs(), [])

    def test_missing_speakers_json(self):
        (self.s / "speakers.json").unlink()
        self.assertTrue(any("speakers.json" in e for e in self.errs()))

    def test_missing_track(self):
        (self.s / "tracks" / "3_Kin.wav").unlink()
        e = self.errs()
        self.assertTrue(any("講者" in x and "3" in x for x in e), e)
        self.assertTrue(any("Kin" in x for x in e), e)

    def test_extra_track(self):
        write_wav(self.s / "tracks" / "4_Bob.wav", 8000, np.zeros(16000))
        self.assertTrue(any("Bob" in x for x in self.errs()))

    def test_track_name_not_in_speakers_tracks(self):
        (self.s / "tracks" / "3_Kin.wav").rename(self.s / "tracks" / "3_King.wav")
        self.assertTrue(any("King" in x for x in self.errs()))

    def test_sample_rate_mismatch(self):
        write_wav(self.s / "tracks" / "2_Sarah.wav", 16000, np.zeros(32000))
        self.assertTrue(any("取樣率" in x and "Sarah" in x for x in self.errs()))

    def test_length_mismatch(self):
        write_wav(self.s / "tracks" / "1_Mars.wav", 8000, np.zeros(int(1.9 * 8000)))
        self.assertTrue(any("長度" in x and "Mars" in x for x in self.errs()))

    def test_length_within_tolerance(self):
        from tracks_columns import TRACK_LEN_TOL
        n = int(round((2.0 - TRACK_LEN_TOL / 2) * 8000))
        write_wav(self.s / "tracks" / "1_Mars.wav", 8000, np.zeros(n))
        self.assertEqual(self.errs(), [])

    def test_diarize_speakers_without_tracks_map_only_count(self):
        write_speakers(self.s, ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
                       with_tracks=False)
        self.assertEqual(self.errs(), [])

    def test_cli_refuses_missing_track_and_leaves_plan(self):
        (self.s / "tracks" / "3_Kin.wav").unlink()
        (self.s / "cutplan.json").write_text(json.dumps(
            {"blocks": [{"id": "B0001", "start": 0, "end": 1, "text": "一"}]}),
            encoding="utf-8")
        md = self.s / "cutplan.md"
        md.write_text("## ⚙ line=mixdown\n- [x] B0001 [0:00–0:01] 一\n",
                      encoding="utf-8")
        r = subprocess.run([sys.executable, str(AUDIO_DIR / "tracks_columns.py"),
                            "--session", str(self.s)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("講者", r.stdout + r.stderr)
        self.assertNotIn("⟦", md.read_text(encoding="utf-8"))


class TestCliOnSyntheticSession(unittest.TestCase):
    """真的跑 tracks_columns.py:三軌合成音,Kin 的麥增益最低但 B0002 是他講。"""

    def test_cli_defaults_and_idempotence(self):
        sr = 8000
        t = np.arange(int(6.0 * sr)) / sr
        rng = np.random.default_rng(1)

        def tone(f, amp, spans):
            x = rng.normal(0, 1e-4, len(t))
            for a, b in spans:
                m = (t >= a) & (t < b)
                x[m] += amp * np.sin(2 * np.pi * f * t[m])
            return x
        mars = tone(300, 0.5, [(0.0, 1.0)]) + tone(700, 0.08, [(1.5, 2.5)])
        sarah = tone(500, 0.3, [(3.0, 4.0)])
        kin = tone(700, 0.05, [(1.5, 2.5)])             # 小聲麥,串音到 Mars 更大
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "ep"
            (sdir / "tracks").mkdir(parents=True)
            write_wav(sdir / "tracks" / "1_Mars.wav", sr, mars)
            write_wav(sdir / "tracks" / "2_Sarah.wav", sr, sarah)
            write_wav(sdir / "tracks" / "3_Kin.wav", sr, kin)
            write_wav(sdir / "source.wav", sr, (mars + sarah + kin) / 3)
            write_speakers(sdir, NAMES)
            blocks = [{"id": "B0001", "start": 0.0, "end": 1.0, "text": "一。"},
                      {"id": "B0002", "start": 1.5, "end": 2.5, "text": "二。"},
                      {"id": "B0003", "start": 3.0, "end": 4.0, "text": "三。"}]
            gaps = [{"id": "G0001", "start": 4.0, "end": 6.0}]
            (sdir / "cutplan.json").write_text(json.dumps(
                {"blocks": blocks, "gaps": gaps}, ensure_ascii=False),
                encoding="utf-8")
            md = sdir / "cutplan.md"
            md.write_text("# t\n\n## ⚙ line=mixdown\n\n"
                          "- [x] B0001 [0:00–0:01] [Mars] 一。\n"
                          "- [x] B0002 [0:01–0:02] [Mars] 二。\n"
                          "- [x] B0003 [0:03–0:04] [Sarah] 三。\n"
                          "- [ ] G0001 [0:04–0:06] ⬜ 空白\n", encoding="utf-8")
            cmd = [sys.executable, str(AUDIO_DIR / "tracks_columns.py"),
                   "--session", str(sdir)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            got = md.read_text(encoding="utf-8")
            self.assertIn("audio=tracks", got)
            self.assertIn("一。 ⟦Mars● Sarah○ Kin○⟧", got)
            self.assertIn("二。 ⟦Mars○ Sarah○ Kin●⟧", got)
            self.assertIn("三。 ⟦Mars○ Sarah● Kin○⟧", got)
            self.assertIn("空白 ⟦Mars● Sarah● Kin●⟧", got)
            r2 = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertEqual(md.read_text(encoding="utf-8"), got)


if __name__ == "__main__":
    unittest.main()
