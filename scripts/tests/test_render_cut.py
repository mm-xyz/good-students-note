#!/usr/bin/env python3
"""test_render_cut.py — render_cut.py 的行為鎖定測試(characterization)。

ffmpeg 出片本身不在測試範圍;鎖的是全部確定性剪輯邏輯:
cutplan 解析(parse_program/parse_strikes)、防幻覺驗證(validate_program 的
FAIL 路徑,ADR 0001/0005 防護鏈)、剪距運算(merge/subtract/word_guard/snap/
pause_removals/strike_removals)、BGM 包絡(bgm_envelope/env_to_expr,原則 11)、
音樂檔前綴匹配(resolve_music 歧義 FAIL)、⚙ config 覆蓋、合成 session 的
--dry-run 端對端。

跑法:
    python3 scripts/tests/test_render_cut.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
REPO_ROOT = AUDIO_DIR.parent.parent
sys.path.insert(0, str(AUDIO_DIR))

from render_cut import (parse_program, parse_strikes, strike_removals,  # noqa: E402
                        pause_removals, word_guard, subtract, merge_ranges,
                        snap_boundaries, validate_program, bgm_envelope,
                        env_to_expr, resolve_music, extend_unit_edges,
                        enforce_monotonic)


class TestEnforceMonotonic(unittest.TestCase):
    """剪點微調(snap/谷底/word_guard)是逐 unit 各自做的,前一段的尾巴與後一段的
    頭可能被推到互相重疊 —— 那段來源音訊就會**播兩次**。

    2026-08-11 實測:混音線 v7 有 1 處 0.48s 重複,分軌線因為剪點密集放大到
    44 處共 15.6s(12:16 那句「任務」在成品裡念了兩次,本地 whisper 重轉抓到)。
    """

    def _seg(self, a, b, clip=False):
        return {"kind": "speech", "a": a, "b": b, "clip": clip}

    def test_overlapping_neighbours_are_clamped(self):
        segs = [self._seg(0.0, 10.0), self._seg(9.0, 20.0)]
        out = enforce_monotonic(segs)
        self.assertEqual([(s["a"], s["b"]) for s in out],
                         [(0.0, 10.0), (10.0, 20.0)])

    def test_non_overlapping_neighbours_are_untouched(self):
        segs = [self._seg(0.0, 10.0), self._seg(12.0, 20.0)]
        self.assertEqual([(s["a"], s["b"]) for s in enforce_monotonic(segs)],
                         [(0.0, 10.0), (12.0, 20.0)])

    def test_a_swallowed_segment_is_dropped(self):
        segs = [self._seg(0.0, 10.0), self._seg(9.9, 10.02), self._seg(11.0, 12.0)]
        out = enforce_monotonic(segs, min_frag=0.12)
        self.assertEqual([(s["a"], s["b"]) for s in out],
                         [(0.0, 10.0), (11.0, 12.0)])

    def test_clip_segments_may_legitimately_go_backwards(self):
        segs = [self._seg(100.0, 110.0, clip=True),
                self._seg(20.0, 30.0, clip=True)]
        self.assertEqual([(s["a"], s["b"]) for s in enforce_monotonic(segs)],
                         [(100.0, 110.0), (20.0, 30.0)])

    def test_music_and_silence_units_do_not_break_the_chain(self):
        segs = [self._seg(0.0, 10.0), {"kind": "silence", "dur": 2.0},
                self._seg(9.0, 20.0)]
        out = enforce_monotonic(segs)
        self.assertEqual(out[2]["a"], 10.0)


def w(start, end, word):
    return {"start": start, "end": end, "word": word}


class TestParseProgram(unittest.TestCase):
    def _parse(self, content: str):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cutplan.md"
            p.write_text(content, encoding="utf-8")
            return parse_program(p)

    def test_full_program_shapes(self):
        prog = self._parse(
            "# Cutplan — test\n\n"
            "## ⚙ clip-gap=0.7 bgm-duck=0.2\n"
            "## 🎵 opening fadein=2 lead=3.5\n"
            "## 🎬 精華集錦\n"
            "- [x] B0001 [0:00–0:01] [Sarah] 集錦句 ← 理由A\n"
            "## 正式章節\n"
            "- [ ] B0002 [0:01–0:02] 沒speaker句\n")
        kinds = [it["kind"] for it in prog]
        self.assertEqual(kinds, ["config", "music", "block", "chapter", "block"])

        cfg = prog[0]
        self.assertEqual(cfg["params"], {"clip-gap": "0.7", "bgm-duck": "0.2"})

        mu = prog[1]
        self.assertEqual(mu["file"], "opening")
        self.assertEqual(mu["fadein"], 2.0)
        self.assertEqual(mu["fadeout"], 1.5)   # 預設
        self.assertEqual(mu["lead"], 3.5)
        self.assertEqual(mu["tail"], 0.0)
        self.assertIsNone(mu["end"])

        b1 = prog[2]
        self.assertEqual(b1["id"], "B0001")
        self.assertTrue(b1["keep"])
        self.assertTrue(b1["clip"])            # 🎬 區內
        self.assertEqual(b1["raw"], "集錦句")   # speaker 前綴+行尾理由都剝掉

        self.assertEqual(prog[3]["title"], "正式章節")

        b2 = prog[4]
        self.assertFalse(b2["keep"])
        self.assertFalse(b2["clip"])           # 章節標題結束 🎬 模式
        self.assertEqual(b2["raw"], "沒speaker句")

    def test_cutplan_heading_not_a_chapter(self):
        prog = self._parse("## Cutplan 說明\n- [x] B0001 [0:00–0:01] [S] 句\n")
        self.assertEqual([it["kind"] for it in prog], ["block"])


class TestParseStrikes(unittest.TestCase):
    def test_basic_span_whitespace_free_coords(self):
        clean, spans = parse_strikes("AB~~CD~~E F")
        self.assertEqual(clean, "ABCDE F")
        self.assertEqual(spans, [[2, 4]])   # 座標不含空白

    def test_multiple_spans(self):
        clean, spans = parse_strikes("~~嗯~~好的~~啊~~")
        self.assertEqual(clean, "嗯好的啊")
        self.assertEqual(spans, [[0, 1], [3, 4]])

    def test_unclosed_tilde_is_literal(self):
        # whisper 會轉出「哦~~」語氣詞:未閉合的 ~~ 當字面文字
        clean, spans = parse_strikes("哦~~真的")
        self.assertEqual(clean, "哦~~真的")
        self.assertEqual(spans, [])

    def test_whitespace_only_strike_no_span(self):
        clean, spans = parse_strikes("A~~ ~~B")
        self.assertEqual(spans, [])


class TestRangeMath(unittest.TestCase):
    def test_merge_ranges_sorts_and_merges(self):
        out = merge_ranges([[5, 6], [0, 1], [1.1, 2]], min_gap=0.2)
        self.assertEqual(out, [[0, 2], [5, 6]])

    def test_merge_ranges_drops_empty(self):
        self.assertEqual(merge_ranges([[3, 3], [1, 2]], min_gap=0.0), [[1, 2]])

    def test_subtract_middle_and_min_frag(self):
        out = subtract([[0.0, 10.0]], [[3.0, 4.0], [9.95, 10.0]])
        # 9.95–10.0 剪掉後尾端碎片 0.05s < 0.12 丟棄
        self.assertEqual(out, [[0.0, 3.0], [4.0, 9.95]])

    def test_word_guard_pushes_out_of_words(self):
        words = [w(0.9, 1.2, "字A"), w(1.9, 2.3, "字B")]
        out = word_guard([[1.0, 2.0]], words)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][0], 0.88)   # 字頭外推 word.start-0.02
        self.assertAlmostEqual(out[0][1], 2.32)   # 字尾外推 word.end+0.02

    def test_word_guard_boundary_outside_words_untouched(self):
        out = word_guard([[1.0, 2.0]], [w(3.0, 4.0, "遠")])
        self.assertEqual(out, [[1.0, 2.0]])

    def test_snap_boundaries_to_silence_mid(self):
        silences = [{"start": 1.0, "end": 1.4}]
        out = snap_boundaries([[0.8, 3.0]], silences, window=0.4)
        self.assertEqual(out, [[1.2, 3.0]])   # 頭 snap 到靜音中點,尾太遠不動

    def test_pause_removals_tightens_long_silence(self):
        out = pause_removals([[0.0, 10.0]], [{"start": 3.0, "end": 6.0}],
                             max_pause=1.5, keep=0.6, words=None)
        self.assertEqual(out, [[3.3, 5.7]])   # 頭尾各留 keep/2

    def test_pause_removals_word_protection(self):
        # 講小聲的字尾侵入「靜音」段(EP15 0:49 事故):靜音窗縮到字邊界外
        words = [w(2.8, 3.4, "小聲尾")]
        out = pause_removals([[0.0, 10.0]], [{"start": 3.0, "end": 6.0}],
                             max_pause=1.5, keep=0.6, words=words)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][0], 3.75)   # (3.4+0.05)+0.3
        self.assertAlmostEqual(out[0][1], 5.7)

    def test_pause_removals_word_in_middle_aborts_that_silence(self):
        words = [w(4.0, 4.5, "中間有字")]
        out = pause_removals([[0.0, 10.0]], [{"start": 3.0, "end": 6.0}],
                             max_pause=1.5, keep=0.6, words=words)
        self.assertEqual(out, [])   # 不是真停頓,放棄


class TestStrikeRemovals(unittest.TestCase):
    BLOCK = {"start": 1.0, "end": 3.0, "text": "第二句。"}

    def test_aligned_words_exact_times(self):
        words = [w(1.0, 1.4, "第"), w(1.4, 1.6, "二"), w(1.6, 2.0, "句。")]
        out = strike_removals(self.BLOCK, [[1, 2]], words)   # 刪「二」
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][0], 1.36)   # 1.4 - pad 0.04
        self.assertAlmostEqual(out[0][1], 1.64)

    def test_mismatched_words_linear_fallback(self):
        # 轉錄/繁化差異對不上 → 按字元比例線性內插,寧可近似也不炸
        words = [w(1.0, 1.4, "完全"), w(1.4, 2.0, "不同")]
        out = strike_removals(self.BLOCK, [[1, 2]], words)
        self.assertEqual(len(out), 1)
        # flat="第二句。" 4 字,span [1,2) → 1.0 + 2.0*(1/4) ± pad
        self.assertAlmostEqual(out[0][0], 1.46)
        self.assertAlmostEqual(out[0][1], 2.04)


class TestExtendUnitEdges(unittest.TestCase):
    def test_end_extended_to_real_word_end(self):
        # EP15「可惜嗎」的「惜嗎」被切在剪點外:end 只向外擴,標 end_exact
        block = {"start": 10.0, "end": 11.0, "text": "可惜嗎"}
        u = {"start": 10.0, "end": 11.0, "items": [{"block": block}]}
        words = [w(10.0, 10.3, "可"), w(10.3, 10.6, "惜"), w(10.9, 11.4, "嗎")]
        extend_unit_edges(u, words)
        self.assertEqual(u["end"], 11.4)
        self.assertTrue(u.get("end_exact"))
        self.assertEqual(u["start"], 10.0)
        self.assertFalse(u.get("start_exact", False))


class TestBgmEnvelope(unittest.TestCase):
    def test_standalone_music_simple_ramp(self):
        m = {"dur": 10.0, "has_prev": False, "has_next": False,
             "fadein": 1.0, "fadeout": 2.0, "lead": 0.0, "tail": 0.0}
        pts = bgm_envelope(m, duck=0.15, solo=0.55, predrop=2.0, rise=1.5)
        self.assertEqual(pts, [(0.0, 0.0), (1.0, 0.55), (8.0, 0.55), (10.0, 0.0)])

    def test_overlay_music_duck_solo_duck(self):
        m = {"dur": 30.0, "has_prev": True, "has_next": True,
             "fadein": 1.0, "fadeout": 1.5, "lead": 3.0, "tail": 4.0}
        pts = bgm_envelope(m, duck=0.15, solo=0.55, predrop=2.0, rise=1.5)
        self.assertEqual(pts, [(0.0, 0.0), (1.0, 0.15), (3.0, 0.15), (4.5, 0.55),
                               (24.0, 0.55), (26.0, 0.15), (27.5, 0.0), (30.0, 0.0)])

    def test_times_monotonic_when_solo_window_tiny(self):
        # 獨奏窗太短:夾單調遞增,不會時間倒流
        m = {"dur": 6.0, "has_prev": True, "has_next": True,
             "fadein": 1.0, "fadeout": 1.5, "lead": 3.0, "tail": 2.5}
        pts = bgm_envelope(m, duck=0.15, solo=0.55, predrop=2.0, rise=1.5)
        times = [t for t, _ in pts]
        self.assertEqual(times, sorted(times))
        self.assertLessEqual(times[-1], 6.0)

    def test_env_to_expr_structure(self):
        expr = env_to_expr([(0.0, 0.0), (1.0, 0.55), (8.0, 0.55), (10.0, 0.0)])
        self.assertTrue(expr.startswith("if(lt(t,"))
        self.assertIn("st(0,", expr)            # smoothstep 內插
        self.assertIn("*ld(0)*(3-2*ld(0))", expr)
        self.assertIn("0.550", expr)            # 平段常數


class TestResolveMusic(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.material = Path(self._td.name) / "material"
        self.material.mkdir()
        for name in ("opening_a.mp3", "opening_b.mp3", "break_x.mp3"):
            (self.material / name).touch()
        self.sdir = Path(self._td.name) / "session"
        self.sdir.mkdir()
        self.addCleanup(self._td.cleanup)

    def test_prefix_match_single_hit(self):
        p = resolve_music("break", self.sdir, self.material)
        self.assertEqual(p.name, "break_x.mp3")

    def test_ambiguous_prefix_fails(self):
        with self.assertRaises(SystemExit):
            resolve_music("opening", self.sdir, self.material)

    def test_exact_name_in_material(self):
        p = resolve_music("opening_a.mp3", self.sdir, self.material)
        self.assertEqual(p.name, "opening_a.mp3")

    def test_session_file_wins(self):
        (self.sdir / "jingle.wav").touch()
        p = resolve_music("jingle.wav", self.sdir, self.material)
        self.assertEqual(p.parent, self.sdir)

    def test_not_found_returns_none(self):
        self.assertIsNone(resolve_music("nothing", self.sdir, self.material))


class TestValidateProgram(unittest.TestCase):
    BLOCKS = [{"id": "B0001", "start": 0.0, "end": 1.0, "text": "第一句。",
               "keep": True, "reason": ""}]

    def _program(self, raw, keep=True, bid="B0001"):
        return [{"kind": "block", "id": bid, "keep": keep, "raw": raw,
                 "clip": False}]

    def test_ok_attaches_spans_and_block(self):
        prog = self._program("第~~一~~句。")
        validate_program(self.BLOCKS, prog, "第一句。其他內容")
        self.assertEqual(prog[0]["spans"], [[1, 2]])
        self.assertEqual(prog[0]["block"]["id"], "B0001")

    def test_md_missing_block_fails(self):
        with self.assertRaises(SystemExit):
            validate_program(self.BLOCKS, [], "第一句。")

    def test_md_extra_id_fails(self):
        prog = self._program("第一句。") + self._program("多的", bid="B0099")
        with self.assertRaises(SystemExit):
            validate_program(self.BLOCKS, prog, "第一句。多的")

    def test_text_tampered_fails(self):
        with self.assertRaises(SystemExit):
            validate_program(self.BLOCKS, self._program("被改的句。"), "第一句。")

    def test_json_text_not_in_srt_fails(self):
        # cutplan.json 被竄改:文字不存在於來源 SRT
        with self.assertRaises(SystemExit):
            validate_program(self.BLOCKS, self._program("第一句。"), "完全無關的逐字稿")

    def test_gap_id_valid_text_free(self):
        gaps = [{"id": "G0001", "start": 1.0, "end": 3.0, "keep": False}]
        prog = self._program("第一句。") + [
            {"kind": "block", "id": "G0001", "keep": False,
             "raw": "⬜ 空白/非語音 2.0s(文字隨便改也行)", "clip": False}]
        validate_program(self.BLOCKS, prog, "第一句。", gaps)
        self.assertTrue(prog[1].get("gap"))


def write_wav(path: Path, secs: float, bursts: list[tuple[float, float]],
              sr: int = 16000, amp: int = 12000):
    """合成 16k mono wav:bursts 區間有聲(方波),其餘靜音。全靜音 wav 會讓
    谷底偵測漂到搜尋窗最左端(病態),要在字界留靜音縫才是真實剪點形狀。"""
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


class TestDryRunE2E(unittest.TestCase):
    """合成 session 的 --dry-run 端對端:不碰 ffmpeg/ffprobe(無音樂、dry-run
    在 src 查找前 return),覆蓋 config 覆蓋、字級精剪、marker 拒跑、FAIL 路徑。"""

    def _make_session(self, td: str) -> Path:
        sdir = Path(td) / "ep-test"
        sdir.mkdir()
        (sdir / "transcript.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n[Sarah] 第一句。\n\n"
            "2\n00:00:01,200 --> 00:00:02,000\n[Sarah] 第二句。\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\n[Sarah] 剪掉段。\n",
            encoding="utf-8")
        blocks = [
            {"id": "B0001", "start": 0.0, "end": 1.0, "speaker": "Sarah",
             "text": "第一句。", "keep": True, "reason": "", "cue_idx": [1]},
            {"id": "B0002", "start": 1.2, "end": 2.0, "speaker": "Sarah",
             "text": "第二句。", "keep": True, "reason": "", "cue_idx": [2]},
            {"id": "B0003", "start": 5.0, "end": 6.0, "speaker": "Sarah",
             "text": "剪掉段。", "keep": True, "reason": "", "cue_idx": [3]},
        ]
        gaps = [{"id": "G0001", "start": 2.0, "end": 5.0, "before": "B0003",
                 "keep": False}]
        (sdir / "cutplan.json").write_text(json.dumps(
            {"blocks": blocks, "gaps": gaps}, ensure_ascii=False), encoding="utf-8")
        (sdir / "cutplan.md").write_text(
            "# Cutplan — ep-test\n\n"
            "## ⚙ max-pause=0\n\n"
            "- [x] B0001 [0:00–0:01] [Sarah] 第一句。\n"
            "- [x] B0002 [0:01–0:02] [Sarah] 第~~二~~句。\n"
            "- [ ] G0001 [0:02–0:05] ⬜ 空白/非語音 3.0s(靜音)\n"
            "- [ ] B0003 [0:05–0:06] [Sarah] 剪掉段。 ← 離題\n",
            encoding="utf-8")
        (sdir / "words.json").write_text(json.dumps([
            w(0.0, 0.3, "第"), w(0.3, 0.6, "一"), w(0.6, 1.0, "句。"),
            w(1.2, 1.4, "第"), w(1.4, 1.6, "二"), w(1.6, 2.0, "句。"),
            w(5.0, 6.0, "剪掉段。"),
        ], ensure_ascii=False), encoding="utf-8")
        (sdir / "prosody.json").write_text(json.dumps({"silences": []}),
                                           encoding="utf-8")
        # 每個字的時間有聲、字界留 20ms 靜音縫(真實語音的能量谷形狀)
        write_wav(sdir / "audio16k.wav", 6.0,
                  bursts=[(0.0, 1.19), (1.21, 1.39), (1.41, 1.59),
                          (1.61, 2.0), (5.0, 6.0)])
        return sdir

    def _render(self, sdir: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(AUDIO_DIR / "render_cut.py"),
             "--session", str(sdir), "--dry-run", *extra],
            capture_output=True, text=True, cwd=REPO_ROOT)

    def test_dry_run_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            proc = self._render(self._make_session(td))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("⚙ config: max-pause=0", proc.stdout)     # config 覆蓋生效
        self.assertIn("字級精剪: 1 處刪除線", proc.stdout)
        speech = [l for l in proc.stdout.splitlines()
                  if l.strip().startswith("speech")]
        # B0001+B0002 併一個 unit,刪「二」切成兩段;B0003 沒勾不出現
        self.assertEqual(len(speech), 2)

    def test_music_lead_clamped_so_it_cannot_bury_the_closing(self):
        """lead 是盲目秒數,不管那幾秒裡還有沒有話 — EP16 的 ending lead=13
        把整段結語壓在音樂底下。超過 --music-lead-max 要夾住並講出來。"""
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            write_wav(sdir / "ending_x.wav", 30.0, bursts=[(0.0, 30.0)])
            md = sdir / "cutplan.md"
            md.write_text(md.read_text(encoding="utf-8")
                          + "## 🎵 ending_x.wav fadein=2 lead=13\n",
                          encoding="utf-8")
            proc = self._render(sdir)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("夾到 3.0s", proc.stdout)
        # 包絡的 duck 段長度=夾過的 lead,不是 cutplan 寫的 13
        env = next(l for l in proc.stdout.splitlines() if "env " in l)
        self.assertIn("3.0s:15%", env)
        self.assertNotIn("13.0s:15%", env)

    def test_music_lead_within_limit_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            write_wav(sdir / "ending_x.wav", 30.0, bursts=[(0.0, 30.0)])
            md = sdir / "cutplan.md"
            md.write_text(md.read_text(encoding="utf-8")
                          + "## 🎵 ending_x.wav fadein=2 lead=2\n",
                          encoding="utf-8")
            proc = self._render(sdir)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotIn("夾到", proc.stdout)

    def test_pending_marker_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            (sdir / ".cutplan_pending.json").write_text("{}", encoding="utf-8")
            proc = self._render(sdir)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("cutplan_pending", proc.stderr + proc.stdout)

    def test_tampered_md_text_fails(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            md = sdir / "cutplan.md"
            md.write_text(md.read_text(encoding="utf-8")
                          .replace("第一句。", "被改的第一句。"), encoding="utf-8")
            proc = self._render(sdir)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("文字與 cutplan.json 不符", proc.stderr + proc.stdout)

    def test_unknown_config_key_fails(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            md = sdir / "cutplan.md"
            md.write_text(md.read_text(encoding="utf-8")
                          .replace("## ⚙ max-pause=0", "## ⚙ bogus-key=1"),
                          encoding="utf-8")
            proc = self._render(sdir)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不認識的鍵", proc.stderr + proc.stdout)

    def test_strikes_without_words_json_fails(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            (sdir / "words.json").unlink()
            proc = self._render(sdir)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("缺 words.json", proc.stderr + proc.stdout)


class TestInsertSyntax(unittest.TestCase):
    """`## ➕ 檔案` 補錄插入(2026-08-10,EP16 Sarah 補錄)。"""

    def _parse(self, content: str):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cutplan.md"
            p.write_text(content, encoding="utf-8")
            return parse_program(p)

    def test_defaults_and_explicit_params(self):
        prog = self._parse(
            "- [x] B0001 [0:00–0:01] [Sarah] 一句。\n"
            "## ➕ raw/補錄.WAV\n"
            "## ➕ raw/補錄2.WAV gain=+3.5 start=2.6 end=42.6 fade=0.05 tempo=1.06  說明文字\n")
        ins = [it for it in prog if it["kind"] == "insert"]
        self.assertEqual(len(ins), 2)
        self.assertEqual(ins[0]["file"], "raw/補錄.WAV")
        self.assertEqual(ins[0]["gain"], "auto")      # 預設自動電平對齊
        self.assertEqual(ins[0]["start"], 0.0)
        self.assertIsNone(ins[0]["end"])
        self.assertEqual(ins[1]["gain"], "+3.5")
        self.assertEqual(ins[1]["start"], 2.6)
        self.assertEqual(ins[1]["end"], 42.6)
        self.assertEqual(ins[1]["tempo"], 1.06)
        self.assertEqual(ins[1]["note"], "說明文字")

    def test_insert_line_is_not_swallowed_as_a_chapter(self):
        """CHAPTER_RE 是 `^## (.+)$`,會吃掉所有 `## ` 開頭的行。
        `## ✂` 曾經因此被當章節標題寫進 IG 文案(2026-08-10 實踩),
        `## ➕` 不准重蹈覆轍。"""
        prog = self._parse("## ➕ raw/補錄.WAV  Sarah 補錄\n")
        self.assertEqual([it["kind"] for it in prog], ["insert"])
        self.assertNotIn("chapter", [it["kind"] for it in prog])


class TestInsertRender(TestDryRunE2E):
    """補錄插入的端對端行為(沿用 TestDryRunE2E 的合成 session)。"""

    def test_insert_becomes_a_segment_and_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            write_wav(sdir / "補錄.wav", 4.0, bursts=[(0.2, 3.8)])
            md = sdir / "cutplan.md"
            # 插在 B0001 與 B0002 中間 → 兩者不得再併成同一個 speech unit
            md.write_text(md.read_text(encoding="utf-8").replace(
                "- [x] B0002", "## ➕ 補錄.wav gain=0  補錄說明\n- [x] B0002"),
                encoding="utf-8")
            proc = self._render(sdir)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("➕ 補錄 補錄.wav", proc.stdout)
        self.assertIn("補錄說明", proc.stdout)

    def _with_s_blocks(self, sdir: Path, marks=("x", "x")) -> None:
        """補錄改走「逐句 S block」模式(2026-08-10 MM:基於逐字稿下去修)。"""
        write_wav(sdir / "補錄.wav", 4.0, bursts=[(0.1, 1.9), (2.1, 3.9)])
        cj = sdir / "cutplan.json"
        data = json.loads(cj.read_text(encoding="utf-8"))
        data["inserts"] = [{"file": "補錄.wav", "speaker": "Sarah", "blocks": [
            {"id": "S0001", "start": 0.1, "end": 1.9, "text": "補錄第一句。",
             "speaker": "Sarah", "keep": True},
            {"id": "S0002", "start": 2.1, "end": 3.9, "text": "補錄第二句。",
             "speaker": "Sarah", "keep": True}]}]
        cj.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        md = sdir / "cutplan.md"
        md.write_text(md.read_text(encoding="utf-8").replace(
            "- [x] B0002",
            "## ➕ 補錄.wav gain=0  補錄說明\n"
            f"- [{marks[0]}] S0001 [0:00–0:01] [Sarah] 補錄第一句。\n"
            f"- [{marks[1]}] S0002 [0:02–0:03] [Sarah] 補錄第二句。\n"
            "- [x] B0002"), encoding="utf-8")

    def test_s_blocks_are_cut_like_normal_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            self._with_s_blocks(sdir)
            both = self._render(sdir)
        self.assertEqual(both.returncode, 0, both.stderr or both.stdout)
        self.assertIn("2 個 S block", both.stdout)

        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            self._with_s_blocks(sdir, marks=(" ", "x"))   # 第一句不勾=剪掉
            one = self._render(sdir)
        self.assertEqual(one.returncode, 0, one.stderr or one.stdout)
        self.assertIn("1 個 S block", one.stdout)

    def test_s_block_text_tampering_fails(self):
        """補錄 block 只准改勾選與加刪除線,不准改字(同正片的防幻覺規則)。"""
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            self._with_s_blocks(sdir)
            md = sdir / "cutplan.md"
            md.write_text(md.read_text(encoding="utf-8")
                          .replace("補錄第一句。", "我自己編的句子。"),
                          encoding="utf-8")
            proc = self._render(sdir)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不准改字", proc.stderr + proc.stdout)

    def test_missing_insert_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            md = sdir / "cutplan.md"
            md.write_text(md.read_text(encoding="utf-8")
                          + "## ➕ 不存在的補錄.wav\n", encoding="utf-8")
            proc = self._render(sdir)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("補錄檔不存在", proc.stderr + proc.stdout)

    def test_over_long_block_is_flagged(self):
        """block 是人審勾選的最小單位,過長=那段失去粒度。
        EP16 開頭 7:33 藏了 21 個(含兩個正好 30.0s 的舊分段指紋),
        一路帶進四版成品沒人發現 — 2026-08-10 MM 指出後補的把關。"""
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            cj = sdir / "cutplan.json"
            data = json.loads(cj.read_text(encoding="utf-8"))
            data["blocks"][0]["end"] = data["blocks"][0]["start"] + 30.0
            cj.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            proc = self._render(sdir)
        self.assertIn("超過 8.0s", proc.stdout)
        self.assertIn("長 30.0s", proc.stdout)

    def test_max_block_zero_disables_the_gate(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._make_session(td)
            cj = sdir / "cutplan.json"
            data = json.loads(cj.read_text(encoding="utf-8"))
            data["blocks"][0]["end"] = data["blocks"][0]["start"] + 30.0
            cj.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            proc = self._render(sdir, "--max-block", "0")
        self.assertNotIn("沒有勾選粒度", proc.stdout)

    def test_insert_does_not_disturb_a_plan_without_inserts(self):
        """沒有 ➕ 的 cutplan,行為必須跟加這個功能之前一模一樣。"""
        with tempfile.TemporaryDirectory() as td:
            proc = self._render(self._make_session(td))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertNotIn("➕", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
