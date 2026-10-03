#!/usr/bin/env python3
"""test_render_tracks.py — `## ⚙ line=mixdown audio=tracks` 的 render 端。

決定層＝合軌節目單(時間剪輯結果必須跟 line=mixdown 逐毫秒相同),音源＝
session `tracks/` 的分軌,每個 block 的軌欄決定哪幾軌出聲(沒勾 −27dB)。

兩組測試:
  · 合成 session(永遠跑):路線護欄、dump-ranges 等同、真的出片後量頻率能量
  · 真音訊(EP22 分軌 session 在本機才跑):從真 session 裁一段窗,
    出片後用最小平方法從 bus 反解各軌增益 —— 只開 Kin 的 block,Mars 軌
    比全開低 ~27dB;時間軸與 line=mixdown 的 --dump-ranges 完全相同。

跑法:
    python3 scripts/tests/test_render_tracks.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
REPO_ROOT = AUDIO_DIR.parent.parent
sys.path.insert(0, str(AUDIO_DIR))

import numpy as np  # noqa: E402

from render_cut import parse_program  # noqa: E402

RENDER = AUDIO_DIR / "render_cut.py"
COLUMNS = AUDIO_DIR / "tracks_columns.py"
NAMES = ["Mars", "Sarah", "Kin"]
FREQ = {"Mars": 300.0, "Sarah": 500.0, "Kin": 700.0}


def write_wav(path: Path, sr: int, x: np.ndarray, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(sr)
        if channels == 2:
            x = np.repeat(x[:, None], 2, axis=1).reshape(-1)
        f.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as f:
        sr, ch, n = f.getframerate(), f.getnchannels(), f.getnframes()
        raw = f.readframes(n)
        w = f.getsampwidth()
    dt = {2: "<i2", 4: "<i4"}[w]
    x = np.frombuffer(raw, dtype=dt).astype(np.float64) / (2 ** (8 * w - 1))
    return x.reshape(-1, ch).mean(axis=1), sr


def tone_db(x: np.ndarray, sr: int, f: float) -> float:
    """單一頻率的振幅(dB),DFT 投影;窗長取整數週期附近即可。"""
    t = np.arange(len(x)) / sr
    c = np.abs(np.dot(x, np.exp(-2j * np.pi * f * t))) * 2 / len(x)
    return 20 * np.log10(c + 1e-12)


ROWS = ("- [x] B0001 [0:00–0:02] [Mars] 第一句。\n"
        "- [x] B0002 [0:02–0:04] [Kin] 第二句。\n"
        "- [ ] B0003 [0:05–0:06] [Sarah] 剪掉段。 ← 離題\n")


def make_session(td: str, cfg: str = "## ⚙ line=mixdown max-pause=0",
                 rows: str = ROWS, tracks: bool = True) -> Path:
    """三軌各自一個持續的純音(Mars 300/Sarah 500/Kin 700Hz),source=三軌相加。"""
    sr = 16000
    sdir = Path(td) / "ep"
    sdir.mkdir()
    t = np.arange(int(6.0 * sr)) / sr
    sig = {n: 0.15 * np.sin(2 * np.pi * FREQ[n] * t) for n in NAMES}
    if tracks:
        (sdir / "tracks").mkdir()
        for i, n in enumerate(NAMES, 1):
            write_wav(sdir / "tracks" / f"{i}_{n}.wav", sr, sig[n])
    write_wav(sdir / "source.wav", sr, sum(sig.values()), channels=2)
    (sdir / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n[Mars] 第一句。\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n[Kin] 第二句。\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\n[Sarah] 剪掉段。\n", encoding="utf-8")
    blocks = [
        {"id": "B0001", "start": 0.0, "end": 2.0, "speaker": "Mars",
         "text": "第一句。", "keep": True, "reason": "", "cue_idx": [1]},
        {"id": "B0002", "start": 2.0, "end": 4.0, "speaker": "Kin",
         "text": "第二句。", "keep": True, "reason": "", "cue_idx": [2]},
        {"id": "B0003", "start": 5.0, "end": 6.0, "speaker": "Sarah",
         "text": "剪掉段。", "keep": True, "reason": "", "cue_idx": [3]},
    ]
    (sdir / "cutplan.json").write_text(json.dumps(
        {"blocks": blocks, "gaps": []}, ensure_ascii=False), encoding="utf-8")
    (sdir / "cutplan.md").write_text(f"# Cutplan — ep\n\n{cfg}\n\n{rows}",
                                     encoding="utf-8")
    (sdir / "prosody.json").write_text(json.dumps({"silences": []}),
                                       encoding="utf-8")
    return sdir


def with_cols(rows: str, cols: dict[str, str]) -> str:
    out = []
    for line in rows.splitlines():
        bid = line[6:11]                      # `- [x] B0001 …` 的 id
        out.append(line + (f" ⟦{cols[bid]}⟧" if bid in cols else ""))
    return "\n".join(out) + "\n"


TR_COLS = {"B0001": "Mars● Sarah● Kin●", "B0002": "Mars○ Sarah○ Kin●",
           "B0003": "Mars○ Sarah● Kin○"}
TR_CFG = "## ⚙ line=mixdown audio=tracks max-pause=0"


def render(sdir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RENDER), "--session", str(sdir),
                           *extra], capture_output=True, text=True, cwd=REPO_ROOT)


class TestParseColumn(unittest.TestCase):
    def test_column_is_split_off_before_text_validation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cutplan.md"
            p.write_text(TR_CFG + "\n" + with_cols(ROWS, TR_COLS),
                         encoding="utf-8")
            prog = [it for it in parse_program(p) if it["kind"] == "block"]
        self.assertEqual(prog[1]["raw"], "第二句。")
        self.assertEqual(prog[1]["tracks"], "Mars○ Sarah○ Kin●")
        self.assertEqual(prog[2]["raw"], "剪掉段。")          # 理由照舊切掉

    def test_mixdown_rows_have_no_tracks(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cutplan.md"
            p.write_text(ROWS, encoding="utf-8")
            prog = [it for it in parse_program(p) if it["kind"] == "block"]
        self.assertTrue(all(it.get("tracks") is None for it in prog))


class TestRouteGuards(unittest.TestCase):
    def test_tracks_timeline_identical_to_mixdown(self):
        with tempfile.TemporaryDirectory() as td:
            a = make_session(td)
            ra = render(a, "--dry-run", "--dump-ranges", str(Path(td) / "a.json"))
            self.assertEqual(ra.returncode, 0, ra.stdout + ra.stderr)
            (a / "cutplan.md").write_text(
                f"# Cutplan — ep\n\n{TR_CFG}\n\n" + with_cols(ROWS, TR_COLS),
                encoding="utf-8")
            rb = render(a, "--dry-run", "--dump-ranges", str(Path(td) / "b.json"))
            self.assertEqual(rb.returncode, 0, rb.stdout + rb.stderr)
            self.assertEqual((Path(td) / "a.json").read_text(),
                             (Path(td) / "b.json").read_text())
        self.assertIn("audio=tracks", rb.stdout)
        self.assertIn("只開一軌", rb.stdout)

    def test_columns_without_audio_tracks_fail(self):
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, rows=with_cols(ROWS, TR_COLS))
            r = render(s, "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("audio=tracks", r.stdout + r.stderr)

    def test_audio_tracks_on_pertrack_line_fails(self):
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, cfg="## ⚙ line=pertrack audio=tracks",
                             rows=with_cols(ROWS, TR_COLS))
            cp = json.loads((s / "cutplan.json").read_text(encoding="utf-8"))
            cp["tracks"] = [{"prefix": "MR", "speaker": "Mars", "file": "x",
                             "blocks": []}]
            (s / "cutplan.json").write_text(json.dumps(cp), encoding="utf-8")
            r = render(s, "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("audio=tracks 只在 line=mixdown", r.stdout + r.stderr)

    def test_missing_column_fails_and_points_to_tool(self):
        cols = dict(TR_COLS)
        del cols["B0002"]
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, cfg=TR_CFG, rows=with_cols(ROWS, cols))
            r = render(s, "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("B0002", r.stdout + r.stderr)
        self.assertIn("tracks_columns.py", r.stdout + r.stderr)

    def test_unknown_track_name_fails(self):
        cols = dict(TR_COLS, B0002="Mars○ Sarah○ King●")
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, cfg=TR_CFG, rows=with_cols(ROWS, cols))
            r = render(s, "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("B0002", r.stdout + r.stderr)

    def test_no_tracks_dir_fails(self):
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, cfg=TR_CFG, rows=with_cols(ROWS, TR_COLS),
                             tracks=False)
            r = render(s, "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tracks/", r.stdout + r.stderr)


class TestRenderSynthetic(unittest.TestCase):
    """真的出片(wav、關 dynaudnorm/loudnorm),量成品裡各軌頻率的振幅。"""

    def test_unchecked_tracks_are_ducked_27db_in_the_product(self):
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, cfg=TR_CFG, rows=with_cols(ROWS, TR_COLS))
            r = render(s, "--out", "out.wav", "--loudnorm", "",
                       "--dynaudnorm", "", "--track-offset", "0")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            x, sr = read_wav(s / "out.wav")
        all_on = x[int(0.4 * sr):int(1.6 * sr)]
        kin_only = x[int(2.4 * sr):int(3.6 * sr)]
        d = {n: tone_db(kin_only, sr, FREQ[n]) - tone_db(all_on, sr, FREQ[n])
             for n in NAMES}
        self.assertAlmostEqual(d["Kin"], 0.0, delta=0.5, msg=d)
        self.assertAlmostEqual(d["Mars"], -27.0, delta=1.0, msg=d)
        self.assertAlmostEqual(d["Sarah"], -27.0, delta=1.0, msg=d)

    def test_switch_has_no_click(self):
        """2.0s 的切換點:相鄰樣本差不得比穩態大很多(raised-cosine 過渡)。"""
        with tempfile.TemporaryDirectory() as td:
            s = make_session(td, cfg=TR_CFG, rows=with_cols(ROWS, TR_COLS))
            r = render(s, "--out", "out.wav", "--loudnorm", "",
                       "--dynaudnorm", "", "--track-offset", "0")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            x, sr = read_wav(s / "out.wav")
        steady = np.abs(np.diff(x[int(0.5 * sr):int(1.5 * sr)])).max()
        sw = np.abs(np.diff(x[int(1.9 * sr):int(2.1 * sr)])).max()
        self.assertLessEqual(sw, steady * 1.05)


# ── 真音訊:EP22 分軌 session(本機有才跑)──────────────────────────────
EP22 = "2026-10-02_EP22-初級大人的指南針-建立原則與底線-分軌"


def find_ep22() -> Path | None:
    cands = [os.environ.get("GSN_SESSIONS_DIR", ""), str(REPO_ROOT / "sessions"),
             str(Path.home() / "GithubRepo_mm-xyz/good-students-note/sessions")]
    for c in cands:
        p = Path(c) / EP22 if c else None
        if p and (p / "tracks").is_dir() and (p / "_asset" / "cutplan.json").is_file():
            return p
    return None


def crop(src: Path, dst: Path, a: float, b: float, stereo: bool = False) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-i",
                    str(src), "-t", f"{b - a:.3f}", "-c:a", "pcm_s16le", str(dst)],
                   check=True)


def build_mini_ep22(real: Path, out: Path, t0: float, t1: float) -> list[dict]:
    """把真 session 的 [t0,t1) 裁成自足的迷你 session(時間平移到 0)。"""
    cp = json.loads((real / "_asset" / "cutplan.json").read_text(encoding="utf-8"))
    blocks = [dict(b, start=round(b["start"] - t0, 3), end=round(b["end"] - t0, 3))
              for b in cp["blocks"] if b["start"] >= t0 + 0.3 and b["end"] <= t1 - 0.3]
    out.mkdir()
    (out / "tracks").mkdir()
    for p in sorted((real / "tracks").iterdir()):
        crop(p, out / "tracks" / (p.stem + ".wav"), t0, t1)
    crop(real / "source.wav", out / "source.wav", t0, t1)
    words = json.loads((real / "_asset" / "words.json").read_text(encoding="utf-8"))
    (out / "words.json").write_text(json.dumps(
        [dict(w, start=w["start"] - t0, end=w["end"] - t0) for w in words
         if w["start"] >= t0 and w["end"] <= t1], ensure_ascii=False),
        encoding="utf-8")
    pros = json.loads((real / "_asset" / "prosody.json").read_text(encoding="utf-8"))
    (out / "prosody.json").write_text(json.dumps({"silences": [
        dict(s, start=s["start"] - t0, end=s["end"] - t0)
        for s in pros.get("silences", []) if s["start"] >= t0 and s["end"] <= t1]}),
        encoding="utf-8")
    srt = []
    for i, b in enumerate(blocks, 1):
        def ts(v):
            ms = int(round(v * 1000))
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
        srt.append(f"{i}\n{ts(b['start'])} --> {ts(b['end'])}\n{b['text']}\n")
    (out / "transcript.srt").write_text("\n".join(srt), encoding="utf-8")
    (out / "cutplan.json").write_text(json.dumps(
        {"blocks": blocks, "gaps": []}, ensure_ascii=False), encoding="utf-8")
    rows = [f"- [x] {b['id']} [0:00–0:00] [{b.get('speaker', '?')}] {b['text']}"
            for b in blocks]
    (out / "cutplan.md").write_text("# t\n\n## ⚙ line=mixdown\n\n"
                                    + "\n".join(rows) + "\n", encoding="utf-8")
    return blocks


@unittest.skipIf(find_ep22() is None, "EP22 分軌 session 不在本機")
class TestRealAudioEP22(unittest.TestCase):
    T0, T1 = 60.0, 100.0

    def test_kin_only_block_ducks_mars_27db_and_timeline_matches(self):
        real = find_ep22()
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "mini"
            blocks = build_mini_ep22(real, s, self.T0, self.T1)
            long_ = [b for b in blocks if b["end"] - b["start"] >= 1.5]
            self.assertGreaterEqual(len(long_), 2, "窗內長 block 不夠")
            r0 = render(s, "--dry-run", "--dump-ranges", str(Path(td) / "mix.json"))
            self.assertEqual(r0.returncode, 0, r0.stdout + r0.stderr)
            rc = subprocess.run([sys.executable, str(COLUMNS), "--session", str(s)],
                                capture_output=True, text=True)
            self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)
            md = (s / "cutplan.md").read_text(encoding="utf-8")
            # 兩個長 block 強制指定:一個只開 Kin、一個三軌全開(其餘照預設)
            kin_b, all_b = long_[0], long_[1]
            lines = []
            for line in md.splitlines():
                if line.startswith(f"- [x] {kin_b['id']} "):
                    line = line.rsplit(" ⟦", 1)[0] + " ⟦Mars○ Sarah○ Kin●⟧"
                elif line.startswith(f"- [x] {all_b['id']} "):
                    line = line.rsplit(" ⟦", 1)[0] + " ⟦Mars● Sarah● Kin●⟧"
                lines.append(line)
            (s / "cutplan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            r1 = render(s, "--dry-run", "--dump-ranges", str(Path(td) / "trk.json"))
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            self.assertEqual((Path(td) / "mix.json").read_text(),
                             (Path(td) / "trk.json").read_text(),
                             "audio=tracks 的時間軸必須跟 line=mixdown 逐毫秒相同")
            r2 = render(s, "--out", "out.wav", "--track-offset", "0",
                        "--keep-tracks-bus")
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            bus, sr = read_wav(s / "out.tracks_bus.wav")
            segmap = json.loads((s / "out.tracks_bus.json").read_text())
            trk = {}
            for p in sorted((s / "tracks").iterdir()):
                trk[p.stem.split("_", 1)[1]], _ = read_wav(p)

            def gains(b):
                mid_a = b["start"] + 0.35 * (b["end"] - b["start"])
                mid_b = b["start"] + 0.65 * (b["end"] - b["start"])
                seg = next(m for m in segmap
                           if m["src_a"] <= mid_a and mid_b <= m["src_b"])
                i0 = int(round((seg["bus_a"] + mid_a - seg["src_a"]) * sr))
                j0 = int(round(mid_a * sr))
                n = int((mid_b - mid_a) * sr)
                A = np.stack([trk[k][j0:j0 + n] for k in NAMES], axis=1)
                g, *_ = np.linalg.lstsq(A, bus[i0:i0 + n], rcond=None)
                return dict(zip(NAMES, g))
            gk, ga = gains(kin_b), gains(all_b)
        mars_db = 20 * np.log10(abs(gk["Mars"]) / abs(ga["Mars"]))
        kin_db = 20 * np.log10(abs(gk["Kin"]) / abs(ga["Kin"]))
        self.assertAlmostEqual(mars_db, -27.0, delta=1.5, msg=(gk, ga))
        self.assertAlmostEqual(kin_db, 0.0, delta=0.5, msg=(gk, ga))


if __name__ == "__main__":
    unittest.main()
