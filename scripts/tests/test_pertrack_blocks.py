#!/usr/bin/env python3
"""test_pertrack_blocks.py — 逐軌 cutplan 產生器(D1 文字來源、粒度、文件結構)。

鎖的是不碰音檔就能驗的部分:
  · canonical phrase 粒度 0.4–1.2s
  · 軌前綴兩碼,不可與 B/G/S(補錄)/I 撞號
  · cutplan.md 的 ⚙/✂/🎵/➕＋S 列/章節/G 列**原樣搬過來**並落在正確時間位置
  · 產出的 cutplan.pertrack.md 能被 render_cut.parse_program 直接吃
  · 非詞彙出聲列預設不勾、低信心收折疊區、折疊區標題不可長得像章節

跑法:
    python3 scripts/tests/test_pertrack_blocks.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))

from pertrack_blocks import (  # noqa: E402
    derive_prefixes, enforce_phrase_len, carry_over_program, build_md,
    pick_visible, backfill_artifact_flags, merge_sentence_rows,
    _merge_reasons,
)
from render_cut import parse_program  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures.ep16_artifact_samples import (  # noqa: E402
    B0068_ARTIFACT_TEXT, B0067_CLEAN_TEXT)


def ph(a, b, text="字", reason=""):
    return {"start": a, "end": b, "text": text, "owner": 0,
            "uncertain": False, "reason": reason,
            "words": [{"start": a, "end": b, "word": text}]}


class TestPrefixes(unittest.TestCase):
    def test_two_letter_prefixes_do_not_collide_with_reserved_ids(self):
        p = derive_prefixes(["Mars", "Sarah", "KIN"])
        self.assertEqual(list(p.values()), ["MR", "SR", "KN"])
        for v in p.values():
            self.assertEqual(len(v), 2)

    def test_sarah_must_not_become_bare_S(self):
        """單碼 S 會跟補錄 block(insert_prepare 的 S0001)撞號。"""
        self.assertNotEqual(derive_prefixes(["Sarah"])["Sarah"], "S")

    def test_duplicate_derivations_get_disambiguated(self):
        p = derive_prefixes(["Mars", "Marco"])
        self.assertEqual(len(set(p.values())), 2)

    def test_non_ascii_names_fall_back_to_track_index(self):
        p = derive_prefixes(["語嫣", "小明"])
        self.assertEqual(list(p.values()), ["T1", "T2"])


class TestPhraseLength(unittest.TestCase):
    def test_short_phrase_is_merged_into_its_neighbour(self):
        out = enforce_phrase_len([ph(0.0, 0.2, "啊"), ph(0.2, 0.8, "今天很好")],
                                 lo=0.4, hi=1.2)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "啊今天很好")

    def test_merge_is_skipped_when_it_would_break_the_upper_bound(self):
        out = enforce_phrase_len([ph(0.0, 0.2, "啊"), ph(0.2, 1.4, "很長的一句")],
                                 lo=0.4, hi=1.2)
        self.assertEqual(len(out), 2)

    def test_owners_are_never_merged_across(self):
        a, b = ph(0.0, 0.2, "啊"), ph(0.2, 0.8, "今天")
        b["owner"] = 1
        self.assertEqual(len(enforce_phrase_len([a, b], lo=0.4, hi=1.2)), 2)

    def test_merge_keeps_both_reasons_uncertain_marker_never_lost(self):
        """#725(#676 同根因殘留):enforce_phrase_len 合併碎片時舊版沿用
        『prev reason 空才採後列』——前列已有換手未切開的理由、後列是
        「歸屬不確定」時,後列會被悄悄蓋掉,人審的安全網 marker 消失。
        改用 _merge_reasons 後兩邊理由都要留著。"""
        a = ph(0.0, 0.2, "前", reason="換手點附近 250ms 內沒有字界，未切開")
        b = ph(0.2, 0.5, "後", reason="歸屬不確定（三軌差距 <3dB）")
        out = enforce_phrase_len([a, b], lo=0.4, hi=1.2)
        self.assertEqual(len(out), 1)
        self.assertIn("換手點附近 250ms 內沒有字界，未切開", out[0]["reason"])
        self.assertIn("歸屬不確定", out[0]["reason"])

    def test_merge_reason_dedupes_identical_text(self):
        a = ph(0.0, 0.2, "前", reason="同一理由")
        b = ph(0.2, 0.5, "後", reason="同一理由")
        out = enforce_phrase_len([a, b], lo=0.4, hi=1.2)
        self.assertEqual(out[0]["reason"], "同一理由")

    def test_merge_keeps_prev_reason_when_later_has_none(self):
        a = ph(0.0, 0.2, "前", reason="前列理由")
        b = ph(0.2, 0.5, "後", reason="")
        out = enforce_phrase_len([a, b], lo=0.4, hi=1.2)
        self.assertEqual(out[0]["reason"], "前列理由")

    def test_three_fragment_chain_does_not_stack_duplicate_marker(self):
        """三段連併(都 <lo,逐一併進同一個 prev)、都帶「歸屬不確定」——
        合併後該 marker 只出現一次,不因逐段合併而堆疊成三份。"""
        a = ph(0.0, 0.1, "一", reason="歸屬不確定（三軌差距 <3dB）")
        b = ph(0.1, 0.2, "二", reason="歸屬不確定（三軌差距 <3dB）")
        c = ph(0.2, 0.3, "三", reason="歸屬不確定（三軌差距 <3dB）")
        out = enforce_phrase_len([a, b, c], lo=0.4, hi=1.2)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reason"].count("歸屬不確定"), 1)

    def test_leading_fragment_forward_merge_keeps_reason_and_uncertain(self):
        """#731:開頭那個碎片沒有前鄰可併(第一列本身太短、第二列本身不短,
        第一輪 prev-merge 不會碰到它們)時走 138-144 的「往後併」分支——
        舊版只搬 start/text/words,out[0] 被 pop 掉時它的 reason／uncertain
        整個消失。改成比照 previous-branch 用 _merge_reasons＋uncertain OR。"""
        a = ph(0.0, 0.2, "前", reason="開頭理由")
        a["uncertain"] = True
        b = ph(0.2, 0.9, "後", reason="後列理由")
        b["uncertain"] = False
        out = enforce_phrase_len([a, b], lo=0.4, hi=1.2)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 0.9)
        self.assertEqual(out[0]["text"], "前後")
        self.assertTrue(out[0]["uncertain"])
        self.assertIn("開頭理由", out[0]["reason"])
        self.assertIn("後列理由", out[0]["reason"])

    def test_leading_fragment_not_mergeable_keeps_metadata_on_both_rows(self):
        """開頭碎片與下一列 owner 不同(或合併會超過 hi)時不能併——原樣保留
        兩列各自的 reason／uncertain,不得消失。這裡同時涵蓋 owner 不同與
        超過 hi 兩種擋下合併的路徑。"""
        a = ph(0.0, 0.2, "前", reason="開頭理由")
        a["uncertain"] = True
        b = ph(0.2, 0.9, "後", reason="後列理由")
        b["owner"] = 1
        out = enforce_phrase_len([a, b], lo=0.4, hi=1.2)
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0]["uncertain"])
        self.assertEqual(out[0]["reason"], "開頭理由")
        self.assertEqual(out[1]["reason"], "後列理由")

        c = ph(0.0, 0.2, "前", reason="開頭理由2")
        c["uncertain"] = True
        d = ph(0.2, 1.3, "後", reason="後列理由2")
        out2 = enforce_phrase_len([c, d], lo=0.4, hi=1.2)
        self.assertEqual(len(out2), 2)
        self.assertTrue(out2[0]["uncertain"])
        self.assertEqual(out2[0]["reason"], "開頭理由2")
        self.assertEqual(out2[1]["reason"], "後列理由2")


CUTPLAN_MD = """# Cutplan — demo

> 說明行

## ⚙ tempo=1.06 max-pause=0.9
## ✂ 100.0-101.0 手動剪除
- [ ] G0001 [0:00–0:02] ⬜ 空白 2.5s
## 🎵 opening start=0 end=10 lead=3
- [x] B0001 [0:02–0:05] [Sarah] 嗨
- [ ] B0002 [0:05–0:06] [Mars] 贅句
## 第一章
- [x] B0003 [0:06–0:07] [KIN] 我是King
## ➕ raw/補錄.WAV gain=auto  Sarah 補錄
- [x] S0001 [0:00–0:02] [Sarah] 補錄句
## 🎵 ending end=20 lead=3
"""

ID_TIME = {"G0001": 0.0, "B0001": 2.5, "B0002": 5.0, "B0003": 6.0}


class TestCarryOverProgram(unittest.TestCase):
    def setUp(self):
        self.groups = carry_over_program(CUTPLAN_MD.splitlines(), ID_TIME)

    def test_b_rows_are_dropped_and_everything_else_survives(self):
        text = "\n".join(l for g in self.groups for l in g["lines"])
        self.assertNotIn("B0001", text)
        self.assertNotIn("B0003", text)
        for keep in ("## ⚙ tempo=1.06", "## ✂ 100.0-101.0", "G0001",
                     "## 🎵 opening", "## 第一章", "## ➕ raw/補錄.WAV",
                     "S0001", "## 🎵 ending"):
            self.assertIn(keep, text)

    def test_insert_header_and_its_s_rows_stay_one_group(self):
        g = next(x for x in self.groups if x["lines"][0].startswith("## ➕"))
        self.assertEqual(len(g["lines"]), 2)
        self.assertIn("S0001", g["lines"][1])

    def test_structural_lines_anchor_to_the_next_timed_row(self):
        def anchor(frag):
            return next(x["anchor"] for x in self.groups
                        if frag in x["lines"][0])
        self.assertEqual(anchor("🎵 opening"), 2.5)     # 下一個是 B0001
        self.assertEqual(anchor("第一章"), 6.0)          # 下一個是 B0003
        self.assertEqual(anchor("⚙ tempo"), 0.0)        # 下一個是 G0001

    def test_trailing_structure_sorts_to_the_end(self):
        g = next(x for x in self.groups if "ending" in x["lines"][0])
        self.assertEqual(g["anchor"], float("inf"))


class TestBuildMd(unittest.TestCase):
    def _md(self, rows, low=()):
        return build_md("demo", carry_over_program(CUTPLAN_MD.splitlines(),
                                                   ID_TIME), rows, list(low))

    ROWS = [{"id": "SR0001", "start": 2.5, "end": 3.0, "speaker": "Sarah",
             "text": "嗨", "kind": "speech", "keep": True, "reason": ""},
            {"id": "KN0001", "start": 6.0, "end": 6.6, "speaker": "KIN",
             "text": "我是King", "kind": "speech", "keep": True, "reason": ""},
            {"id": "MR0001", "start": 6.2, "end": 6.6, "speaker": "Mars",
             "text": "（非詞彙出聲／待辨 0.4s）", "kind": "voicing",
             "keep": False, "reason": "excess +12.0dB"}]

    def test_rows_land_in_time_order_between_the_carried_structure(self):
        lines = self._md(self.ROWS).splitlines()
        pos = {k: i for i, l in enumerate(lines)
               for k in ("🎵 opening", "SR0001", "第一章", "KN0001",
                         "➕ raw", "🎵 ending") if k in l}
        self.assertLess(pos["🎵 opening"], pos["SR0001"])
        self.assertLess(pos["SR0001"], pos["第一章"])
        self.assertLess(pos["第一章"], pos["KN0001"])
        self.assertLess(pos["KN0001"], pos["➕ raw"])
        self.assertLess(pos["➕ raw"], pos["🎵 ending"])

    def test_voicing_rows_are_unchecked_and_speech_rows_are_checked(self):
        lines = self._md(self.ROWS).splitlines()
        self.assertTrue(any(l.startswith("- [x] SR0001") for l in lines))
        self.assertTrue(any(l.startswith("- [ ] MR0001") for l in lines))

    def test_low_confidence_rows_go_to_a_fold_that_is_not_a_chapter(self):
        low = [{"id": "MR0002", "start": 9.0, "end": 9.4, "speaker": "Mars",
                "text": "（非詞彙出聲／待辨 0.4s）", "kind": "voicing",
                "keep": False, "reason": "excess +3.1dB"}]
        md = self._md(self.ROWS, low)
        self.assertIn("<details>", md)
        self.assertIn("MR0002", md)
        for line in md.splitlines():
            if line.startswith("## "):
                self.assertNotIn("低信心", line)

    def test_generated_md_is_parseable_by_render_cut(self):
        import tempfile
        low = [{"id": "MR0002", "start": 9.0, "end": 9.4, "speaker": "Mars",
                "text": "（非詞彙出聲／待辨 0.4s）", "kind": "voicing",
                "keep": False, "reason": ""}]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cutplan.pertrack.md"
            p.write_text(self._md(self.ROWS, low), encoding="utf-8")
            prog = parse_program(p)
        kinds = [it["kind"] for it in prog]
        self.assertEqual(kinds.count("config"), 1)
        self.assertEqual(kinds.count("cut"), 1)
        self.assertEqual(kinds.count("music"), 2)
        self.assertEqual(kinds.count("insert"), 1)
        self.assertEqual(kinds.count("chapter"), 1)
        ids = [it["id"] for it in prog if it["kind"] == "block"]
        self.assertEqual(ids, ["G0001", "SR0001", "KN0001", "MR0001",
                               "S0001", "MR0002"])
        s = next(it for it in prog if it.get("id") == "S0001")
        self.assertEqual(s["insert"], "raw/補錄.WAV")
        self.assertIsNone(next(it for it in prog
                               if it.get("id") == "SR0001")["insert"])


class TestBackfillArtifactFlags(unittest.TestCase):
    """#675 追修(luna FAIL 2026-08-14):5663ea3 改版把 7c555fc 的
    is_artifact() 一起砍掉了,現行 pertrack_blocks.py 直接吃 cp["blocks"]
    (混音線 canonical 文字)做逐軌切分,不帶任何 artifact 判斷;既有(舊格式)
    cutplan.json 的 block 也沒有 cutplan.flag_artifacts() 補的欄位。入口
    對缺欄位的 block 防禦性補跑 detect_asr_artifact(),新舊資料都安全,
    不需要 migration。"""

    def test_old_format_block_without_field_gets_backfilled(self):
        # 舊格式 cutplan.json 的 block:沒有 asr_artifact 欄位,B0068 型內容
        blocks = [{"id": "B0068", "start": 453.2, "end": 473.64,
                  "speaker": "Sarah", "text": B0068_ARTIFACT_TEXT,
                  "keep": True, "reason": ""}]
        n = backfill_artifact_flags(blocks)
        self.assertEqual(n, 1)
        self.assertTrue(blocks[0]["asr_artifact"])
        self.assertTrue(blocks[0]["asr_artifact_reason"])
        self.assertTrue(blocks[0]["keep"])  # 只標記,不改動任何既有欄位

    def test_old_format_clean_block_not_flagged(self):
        blocks = [{"id": "B0067", "start": 440.9, "end": 450.28,
                  "speaker": "Sarah", "text": B0067_CLEAN_TEXT,
                  "keep": True, "reason": ""}]
        n = backfill_artifact_flags(blocks)
        self.assertEqual(n, 0)
        self.assertFalse(blocks[0]["asr_artifact"])

    def test_existing_field_not_overwritten(self):
        # 已有欄位(不論真假)一律跳過,冪等
        blocks = [{"id": "B0068", "text": B0068_ARTIFACT_TEXT,
                  "asr_artifact": False, "asr_artifact_reason": "人工核可"}]
        n = backfill_artifact_flags(blocks)
        self.assertEqual(n, 0)
        self.assertFalse(blocks[0]["asr_artifact"])
        self.assertEqual(blocks[0]["asr_artifact_reason"], "人工核可")

    def test_idempotent_second_call_no_change(self):
        blocks = [{"id": "B0068", "text": B0068_ARTIFACT_TEXT}]
        backfill_artifact_flags(blocks)
        first = dict(blocks[0])
        backfill_artifact_flags(blocks)
        self.assertEqual(blocks[0], first)

    def test_mixed_batch_only_missing_field_backfilled(self):
        blocks = [
            {"id": "B0001", "text": B0067_CLEAN_TEXT},              # 舊格式,缺欄位
            {"id": "B0068", "text": B0068_ARTIFACT_TEXT,
             "asr_artifact": True, "asr_artifact_reason": "已標"},  # 新格式,已有欄位
        ]
        n = backfill_artifact_flags(blocks)
        self.assertEqual(n, 0)  # B0001 補上後判定為 False,B0068 本來就有欄位不重算
        self.assertFalse(blocks[0]["asr_artifact"])
        self.assertEqual(blocks[1]["asr_artifact_reason"], "已標")


def row(a, b, text, kind="speech", src="B0001", reason=""):
    return {"start": a, "end": b, "text": text, "kind": kind,
            "keep": True, "speaker": "Sarah", "reason": reason, "src": src}


class TestMergeSentenceRows(unittest.TestCase):
    """#676:同 owner、緊鄰無停頓的碎片併回句子級 block。"""

    def test_adjacent_same_owner_fragments_merge(self):
        rows = [row(0.0, 1.0, "上班忙著解副"), row(1.1, 1.3, "本,"),
                row(1.4, 2.0, "下班忙著開副"), row(2.05, 2.3, "本。")]
        out = merge_sentence_rows(rows, gap=0.45, max_block=2.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "上班忙著解副本,下班忙著開副本。")

    def test_real_pause_is_not_bridged(self):
        """間隔 ≥gap ＝真實停頓，不合併(保留原本的斷點)。"""
        rows = [row(0.0, 1.0, "嗨"), row(1.6, 2.0, "大家好")]
        out = merge_sentence_rows(rows, gap=0.45, max_block=2.5)
        self.assertEqual(len(out), 2)

    def test_max_block_cap_stops_the_merge(self):
        """合併後會超過 max_block 秒就不再併，避免退回 cc9ecc6 之前的大塊。"""
        rows = [row(0.0, 1.0, "一"), row(1.05, 2.0, "二"),
                row(2.05, 3.0, "三")]
        out = merge_sentence_rows(rows, gap=0.45, max_block=2.0)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "一二")
        self.assertEqual(out[1]["text"], "三")

    def test_voicing_row_breaks_the_chain(self):
        """非詞彙出聲列不參與合併,天然當講者換手斷點。"""
        rows = [row(0.0, 1.0, "一"),
                row(1.05, 1.2, "（非詞彙出聲／待辨 0.2s）", kind="voicing"),
                row(1.25, 2.0, "二")]
        out = merge_sentence_rows(rows, gap=0.45, max_block=5.0)
        self.assertEqual(len(out), 3)

    def test_timecodes_and_text_are_conserved(self):
        """只合不丟:envelope(首尾時間)不變,文字全部保留、順序不亂。"""
        rows = [row(0.0, 1.0, "一"), row(1.05, 2.0, "二"),
                row(2.1, 3.0, "三")]
        out = merge_sentence_rows(rows, gap=0.45, max_block=10.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 3.0)
        self.assertEqual(out[0]["text"], "一二三")

    def test_no_gap_before_first_row_is_never_dropped(self):
        rows = [row(5.0, 6.0, "只有一句")]
        out = merge_sentence_rows(rows, gap=0.45, max_block=2.5)
        self.assertEqual(out, rows)

    def test_does_not_bridge_over_another_owners_interjection(self):
        """A 講兩段、間隔中間 B 有插話 —— 就算間隔 <gap,合併起來的文字會
        跳過 B 講的內容,跟來源 SRT 對不上,不能併(render_cut 逐字驗證
        需要:每個 block 的文字必須是來源 SRT 的連續子字串)。"""
        rows = [row(0.0, 1.0, "前半句"), row(1.2, 2.0, "後半句")]
        other = [(1.05, 1.15)]  # B 在 A 的間隔裡插了一句
        out = merge_sentence_rows(rows, gap=0.45, max_block=2.5,
                                  blocked_by=other)
        self.assertEqual(len(out), 2)

    def test_merges_when_gap_is_clear_of_other_owners(self):
        rows = [row(0.0, 1.0, "前半句"), row(1.2, 2.0, "後半句")]
        other = [(5.0, 5.5)]  # 不在間隔範圍內,不擋
        out = merge_sentence_rows(rows, gap=0.45, max_block=2.5,
                                  blocked_by=other)
        self.assertEqual(len(out), 1)

    def test_merge_keeps_both_reasons_uncertain_marker_never_lost(self):
        """luna 守門 FAIL 的最小重現:前列已有其他理由、後列是「歸屬不確定」
        ——舊版『prev reason 空才採後列』會讓歸屬不確定 marker 消失,合併
        後看起來像確定,人審的安全網被拔掉。兩邊理由都要留著。"""
        a = row(0.0, 1.0, "換手處", reason="換手點附近 250ms 內沒有字界，未切開")
        b = row(1.05, 2.0, "後半句",
               reason="歸屬不確定（三軌差距 <3dB）（暫掛 diarize 判的 Sarah）")
        out = merge_sentence_rows([a, b], gap=0.45, max_block=2.5)
        self.assertEqual(len(out), 1)
        self.assertIn("換手點附近 250ms 內沒有字界，未切開", out[0]["reason"])
        self.assertIn("歸屬不確定", out[0]["reason"])

    def test_merge_reason_dedupes_identical_text(self):
        a = row(0.0, 1.0, "一", reason="同一理由")
        b = row(1.05, 2.0, "二", reason="同一理由")
        out = merge_sentence_rows([a, b], gap=0.45, max_block=2.5)
        self.assertEqual(out[0]["reason"], "同一理由")

    def test_merge_keeps_prev_reason_when_later_row_has_none(self):
        a = row(0.0, 1.0, "一", reason="前列理由")
        b = row(1.05, 2.0, "二", reason="")
        out = merge_sentence_rows([a, b], gap=0.45, max_block=2.5)
        self.assertEqual(out[0]["reason"], "前列理由")

    def test_merge_reasons_dedupes_tokens_not_whole_strings(self):
        """luna round-2:_merge_reasons("A；B","B") 舊版回 "A；B；B"——
        去重只比對完整字串,前一次結果已是複合字串時,後面重複的 token 照樣
        被追加堆疊。要先拆 token 再去重。"""
        self.assertEqual(_merge_reasons("A；B", "B"), "A；B")

    def test_three_row_chain_merge_does_not_stack_duplicate_marker(self):
        """三列連併,每列都帶「歸屬不確定」——合併後該 marker 只出現一次,
        不會因為逐列合併(第三列撞上已經是複合字串的 prev.reason)而重複
        堆疊。"""
        uncertain = "歸屬不確定（三軌差距 <3dB）（暫掛 diarize 判的 Sarah）"
        rows = [row(0.0, 1.0, "一", reason="換手點附近 250ms 內沒有字界，未切開"),
                row(1.05, 2.0, "二", reason=uncertain),
                row(2.1, 3.0, "三", reason=uncertain)]
        out = merge_sentence_rows(rows, gap=0.45, max_block=5.0)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reason"].count(uncertain), 1)
        self.assertEqual(out[0]["reason"],
                         f"換手點附近 250ms 內沒有字界，未切開；{uncertain}")

    def test_merge_across_original_block_boundary_keeps_src_traceable(self):
        """同一講者一直講、沒停頓,可以跨原本的 canonical block(src)邊界
        合併(這正是 no-src-limit 才壓得下 #676 的 69.8% 的原因)——但 src
        不能悄悄丟掉後半段來源,要看得出這行是哪幾個原始 block 拼的。"""
        rows = [row(0.0, 1.0, "它的模式呢?", src="B0013"),
                row(1.05, 2.0, "或者它的產品", src="B0014")]
        out = merge_sentence_rows(rows, gap=0.45, max_block=2.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "它的模式呢?或者它的產品")
        self.assertIn("B0013", out[0]["src"])
        self.assertIn("B0014", out[0]["src"])


class TestVisibleBudget(unittest.TestCase):
    def test_visible_candidates_are_capped_at_two_per_minute(self):
        ev = [{"score": float(i)} for i in range(100)]
        vis, low = pick_visible(ev, duration=60.0, per_min=2.0, high_db=6.0)
        self.assertEqual(len(vis), 2)
        self.assertEqual(len(low), 98)
        self.assertEqual([e["score"] for e in vis], [99.0, 98.0])

    def test_low_confidence_events_never_become_visible(self):
        ev = [{"score": 1.0}, {"score": 2.0}]
        vis, low = pick_visible(ev, duration=600.0, per_min=2.0, high_db=6.0)
        self.assertEqual(vis, [])
        self.assertEqual(len(low), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
