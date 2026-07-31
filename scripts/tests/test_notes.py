#!/usr/bin/env python3
"""test_notes.py — frames Stage 4 notes.py（本地 LM Studio 筆記蒸餾）單元測試。

LLM 呼叫全 mock（call 可注入），零網路、零 LM Studio 依賴：
- 切段：預算內、時間錨點單調、cue 全覆蓋
- 候選過濾：時間碼界內、金句 grep 得回 SRT（防幻覺）、去重
- 格式 lint：鎖死結構（frontmatter/主標/Ref source/TL;DR/重點筆記/金句表）
- JSON 修復＋reasoning_content 保底（common.extract_json_from_message）
- reduce 驗收不過自動重試、重試耗盡明確失敗

跑法：python3 scripts/tests/test_notes.py
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

FRAMES_DIR = Path(__file__).resolve().parent.parent / "frames"
sys.path.insert(0, str(FRAMES_DIR))

import notes  # noqa: E402
from common import extract_json_from_message, fmt_ts  # noqa: E402


# ── 測試材料 ──────────────────────────────────────────────────────────

def mk_cues(n=40, step=10.0):
    """n 句、每句 step 秒的假逐字稿 cues。文字帶編號可拼金句。"""
    cues = []
    for i in range(n):
        t = i * step
        cues.append({"start": t, "end": t + step - 0.5,
                     "text": f"這是第{i}句逐字稿，講者正在說明系統的重點細節。"})
    return cues


def mk_meta():
    return {"title": "測試演講", "speaker": "測試講者",
            "ref_lines": ["> - 逐字稿（SRT）：`/tmp/x.srt`"]}


def mk_sections(cues):
    return [{"title": "第一小節", "start": 0.0, "end": 95.0,
             "body": "這一節講了開場與問題意識，並給出具體例子。"},
            {"title": "第二小節", "start": 100.0, "end": 195.0,
             "body": "這一節收斂出方法論與操作步驟。"}]


def mk_quotes(cues):
    # 逐字取自 cues → grep 必過
    return [{"start": 30.0, "end": 42.0, "text": cues[3]["text"],
             "why": "有記憶點，可獨立成立"}]


# ── estimate_tokens / chunk_cues ─────────────────────────────────────

class TestChunking(unittest.TestCase):
    def test_estimate_tokens_cjk_heavier_than_ascii(self):
        self.assertEqual(notes.estimate_tokens("中文四個字"), 5)
        self.assertEqual(notes.estimate_tokens("abcdefgh"), 2)  # ascii ≈ 1 token / 4 chars

    def test_chunks_cover_all_cues_in_order(self):
        cues = mk_cues(40)
        chunks = notes.chunk_cues(cues, budget=120)
        self.assertGreater(len(chunks), 1)
        # 錨點單調、頭尾覆蓋
        self.assertEqual(chunks[0]["start"], cues[0]["start"])
        self.assertEqual(chunks[-1]["end"], cues[-1]["end"])
        for a, b in zip(chunks, chunks[1:]):
            self.assertLessEqual(a["end"], b["start"])
        # 每段文字都在預算內
        for ch in chunks:
            self.assertLessEqual(notes.estimate_tokens(ch["text"]), 120)
        # cue 行數全覆蓋
        total_lines = sum(ch["text"].count("\n") + 1 for ch in chunks)
        self.assertEqual(total_lines, len(cues))

    def test_single_oversized_cue_still_emitted(self):
        cues = [{"start": 0, "end": 5, "text": "超" * 500}]
        chunks = notes.chunk_cues(cues, budget=50)
        self.assertEqual(len(chunks), 1)

    def test_chunk_lines_carry_timestamps(self):
        cues = mk_cues(3)
        chunks = notes.chunk_cues(cues, budget=10_000)
        self.assertIn(f"[{fmt_ts(cues[1]['start'])}]", chunks[0]["text"])


# ── 時間碼解析 ───────────────────────────────────────────────────────

class TestTimecodes(unittest.TestCase):
    def test_parse_mmss(self):
        self.assertEqual(notes.parse_mmss("1:18"), 78.0)
        self.assertEqual(notes.parse_mmss("0:05"), 5.0)
        self.assertEqual(notes.parse_mmss("1:02:03"), 3723.0)

    def test_parse_mmss_numeric_passthrough(self):
        self.assertEqual(notes.parse_mmss(93), 93.0)
        self.assertEqual(notes.parse_mmss("93.5"), 93.5)

    def test_parse_mmss_bad_raises(self):
        with self.assertRaises(ValueError):
            notes.parse_mmss("abc")

    def test_ts_range_uses_endash(self):
        self.assertEqual(notes.ts_range(78, 124), "1:18–2:04")


# ── 候選過濾（防幻覺）──────────────────────────────────────────────

class TestFilterCandidates(unittest.TestCase):
    def setUp(self):
        self.cues = mk_cues(40)
        self.flat = notes.normalize("".join(c["text"] for c in self.cues))
        self.dur = max(c["end"] for c in self.cues)

    def filt(self, points=(), quotes=()):
        return notes.filter_candidates(list(points), list(quotes), self.flat, self.dur)

    def test_verbatim_quote_kept(self):
        _, qs = self.filt(quotes=[{"start": "0:30", "end": "0:40",
                                   "text": self.cues[3]["text"], "why": "w"}])
        self.assertEqual(len(qs), 1)
        self.assertIsInstance(qs[0]["start"], float)

    def test_hallucinated_quote_dropped(self):
        _, qs = self.filt(quotes=[{"start": "0:30", "end": "0:40",
                                   "text": "這句話從沒被說過完全是編造的內容", "why": "w"}])
        self.assertEqual(qs, [])

    def test_ellipsis_joined_fragments_ok(self):
        text = f"{self.cues[2]['text']}……{self.cues[5]['text']}"
        _, qs = self.filt(quotes=[{"start": "0:20", "end": "1:00",
                                   "text": text, "why": "w"}])
        self.assertEqual(len(qs), 1)

    def test_out_of_range_time_dropped(self):
        pts, _ = self.filt(points=[{"start": "99:00", "end": "99:30", "point": "越界"}])
        self.assertEqual(pts, [])
        _, qs = self.filt(quotes=[{"start": "0:10", "end": "99:30",
                                   "text": self.cues[1]["text"], "why": "w"}])
        self.assertEqual(qs, [])

    def test_unparsable_time_dropped_not_crash(self):
        pts, _ = self.filt(points=[{"start": "無", "end": "0:30", "point": "壞時間"}])
        self.assertEqual(pts, [])

    def test_duplicate_quotes_deduped(self):
        q = {"start": "0:30", "end": "0:40", "text": self.cues[3]["text"], "why": "w"}
        _, qs = self.filt(quotes=[q, dict(q)])
        self.assertEqual(len(qs), 1)

    def test_speaker_tag_stripped_from_quote(self):
        _, qs = self.filt(quotes=[{"start": "0:30", "end": "0:40",
                                   "text": f"[Mars] {self.cues[3]['text']}", "why": "w"}])
        self.assertEqual(len(qs), 1)
        self.assertNotIn("[Mars]", qs[0]["text"])


# ── render + 機械驗收三件 ───────────────────────────────────────────

class TestRenderAndValidate(unittest.TestCase):
    def setUp(self):
        self.cues = mk_cues(40)
        self.md = notes.render_note(mk_meta(), "整場在講一套測試方法論，具體不空泛。",
                                    mk_sections(self.cues), mk_quotes(self.cues))

    def test_rendered_note_passes_all_checks(self):
        self.assertEqual(notes.validate_note(self.md, self.cues), [])

    def test_locked_structure_present(self):
        for marker in ("# 測試演講 — 筆記", "> [!info] Ref source", "## TL;DR",
                       "## 重點筆記", "## 金句／可剪片段候選",
                       "| 起訖 | 內容 | 為什麼值得剪 |", "| :--- | :--- | :--- |",
                       'title: "測試演講（筆記）"', "tool: good-students-note"):
            self.assertIn(marker, self.md, marker)
        self.assertRegex(self.md, r"(?m)^### 第一小節 \[0:00–1:35\]$")

    def test_lint_missing_table_header(self):
        bad = self.md.replace("| 起訖 | 內容 | 為什麼值得剪 |", "| 時間 | 內容 | 理由 |")
        self.assertTrue(any("表頭" in e for e in notes.validate_note(bad, self.cues)))

    def test_lint_missing_section_header(self):
        bad = re.sub(r"(?m)^### .+$", "普通文字", self.md)
        self.assertTrue(any("小節" in e for e in notes.validate_note(bad, self.cues)))

    def test_timecode_beyond_duration_fails(self):
        bad = self.md.replace("[0:00–1:35]", "[0:00–99:35]")
        self.assertTrue(any("時長" in e or "越界" in e
                            for e in notes.validate_note(bad, self.cues)))

    def test_quote_not_in_srt_fails(self):
        bad = self.md.replace(mk_quotes(self.cues)[0]["text"], "完全虛構的一句金句內容啦")
        self.assertTrue(any("金句" in e for e in notes.validate_note(bad, self.cues)))

    def test_frontmatter_key_missing_fails(self):
        bad = self.md.replace('speaker: "測試講者"\n', "")
        self.assertTrue(any("speaker" in e for e in notes.validate_note(bad, self.cues)))

    def test_pipe_in_cell_escaped(self):
        quotes = [{"start": 30.0, "end": 42.0, "text": mk_cues(40)[3]["text"],
                   "why": "有梗|好剪"}]
        md = notes.render_note(mk_meta(), "tldr 內容。", mk_sections(self.cues), quotes)
        row = next(l for l in md.splitlines() if l.startswith("| 0:30"))
        self.assertEqual(row.count("|"), 4)  # 3 欄 = 4 根欄杆

    def test_tail_preserved(self):
        md = notes.render_note(mk_meta(), "tldr。", mk_sections(self.cues),
                               mk_quotes(self.cues), tail="## 關鍵畫面\n\n![[x.jpg]]")
        self.assertIn("## 關鍵畫面", md)
        self.assertEqual(notes.validate_note(md, self.cues), [])


# ── JSON 修復＋reasoning_content 保底（共用層）──────────────────────

class TestExtractJson(unittest.TestCase):
    def test_plain_json(self):
        out = extract_json_from_message({"content": '前言 {"a": 1} 後語'})
        self.assertEqual(out, {"a": 1})

    def test_illegal_escape_repaired(self):
        out = extract_json_from_message({"content": '{"text": "C:\\Users\\x"}'})
        self.assertEqual(out["text"], "C:\\Users\\x")

    def test_reasoning_content_fallback(self):
        msg = {"content": "", "reasoning_content": '想想看…… {"keep": true} 就這樣'}
        self.assertEqual(extract_json_from_message(msg), {"keep": True})

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            extract_json_from_message({"content": "沒有任何 JSON"})


# ── map-reduce 全流程（mock LLM）＋失敗自動重試 ─────────────────────

def fake_llm(cues, bad_outlines=0):
    """回傳 (call, counter)。依 prompt 開頭分流 map/outline/body/tldr。
    bad_outlines：前 N 次 outline 回壞 point_ids，測自動重試。"""
    state = {"map": 0, "outline": 0, "body": 0, "tldr": 0}

    def call(prompt: str) -> dict:
        if prompt.startswith(notes.MAP_PROMPT[:20]):
            state["map"] += 1
            # 從送進來的段落文字抓第一行當「逐字」金句 → grep 必過
            m = re.search(r"\[(\d+:\d{2})\] (.+)", prompt)
            ts, line = m.group(1), m.group(2)
            return {"points": [{"start": ts, "end": ts, "point": f"重點：{line[:20]}"}],
                    "quotes": [{"start": ts, "end": ts, "text": line, "why": "有記憶點"}]}
        if prompt.startswith(notes.OUTLINE_PROMPT[:20]):
            state["outline"] += 1
            if state["outline"] <= bad_outlines:
                return {"tldr": "壞輪", "sections": [{"title": "壞", "point_ids": ["P999"]}],
                        "quote_ids": []}
            pids = re.findall(r"(?m)^(P\d+) ", prompt)
            qids = re.findall(r"(?m)^(Q\d+) ", prompt)
            mid = max(1, len(pids) // 2)
            return {"tldr": "整場的具體摘要，一百二十字以內也可以但要具體。",
                    "sections": [{"title": "上半場", "point_ids": pids[:mid]},
                                 {"title": "下半場", "point_ids": pids[mid:]}],
                    "quote_ids": qids[:8]}
        if prompt.startswith(notes.BODY_PROMPT[:12]):
            state["body"] += 1
            return {"body": "這一節的連貫敘述，保留具體案例不空泛。"}
        state["tldr"] += 1
        return {"tldr": "分治合併後的摘要。"}

    return call, state


class TestBuildNotePipeline(unittest.TestCase):
    def setUp(self):
        self.cues = mk_cues(60)

    def test_end_to_end_mocked(self):
        call, state = fake_llm(self.cues)
        md = notes.build_note(call, self.cues, mk_meta(), retries=2,
                              context=700, log=lambda *_: None)
        self.assertEqual(notes.validate_note(md, self.cues), [])
        self.assertGreater(state["map"], 1)      # 真的有切段 map
        self.assertGreaterEqual(state["body"], 1)

    def test_bad_outline_retried_then_ok(self):
        call, state = fake_llm(self.cues, bad_outlines=1)
        md = notes.build_note(call, self.cues, mk_meta(), retries=2,
                              context=700, log=lambda *_: None)
        self.assertEqual(notes.validate_note(md, self.cues), [])
        self.assertGreaterEqual(state["outline"], 2)  # 壞一次 → 自動重試

    def test_retries_exhausted_raises(self):
        call, _ = fake_llm(self.cues, bad_outlines=99)
        with self.assertRaises(notes.NoteGenError):
            notes.build_note(call, self.cues, mk_meta(), retries=1,
                             context=700, log=lambda *_: None)

    def test_map_calls_respect_context_budget(self):
        seen = []
        inner, _ = fake_llm(self.cues)

        def call(prompt):
            seen.append(notes.estimate_tokens(prompt))
            return inner(prompt)

        notes.build_note(call, self.cues, mk_meta(), retries=2,
                         context=700, log=lambda *_: None)
        for tok in seen:
            self.assertLessEqual(tok, 700 - notes.OUTPUT_RESERVE_MIN)


# ── SRT 選檔順序 ─────────────────────────────────────────────────────

class TestPickSrt(unittest.TestCase):
    def test_order_speakers_first(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "transcript.srt").write_text("x", encoding="utf-8")
            (p / "cleaned.srt").write_text("x", encoding="utf-8")
            self.assertEqual(notes.pick_srt(p).name, "cleaned.srt")
            (p / "transcript.speakers.srt").write_text("x", encoding="utf-8")
            self.assertEqual(notes.pick_srt(p).name, "transcript.speakers.srt")

    def test_none_when_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(notes.pick_srt(Path(d)))


if __name__ == "__main__":
    unittest.main(verbosity=1)
