#!/usr/bin/env python3
"""test_copy_prompt_build.py — 集數文案 prompt 組裝(#571 上游)的行為鎖定測試。

鎖住:build_transcript 的 final-cut 時間換算(cut_map)、同講者連續 block 合併、
🎬 集錦區排除、章節標題織入、刪除線/理由剝除;main 的前置缺檔 FAIL 與模板組裝。

跑法:
    python3 scripts/tests/test_copy_prompt_build.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
REPO_ROOT = AUDIO_DIR.parent.parent
sys.path.insert(0, str(AUDIO_DIR))

from copy_prompt_build import (build_transcript, hms,  # noqa: E402
                               attach_speakers, plan_sequence,
                               build_transcript_from_final)

CUTPLAN_MD = """# Cutplan — test

## 🎬 精華
- [x] B0002 [0:01–0:02] [Sarah] 歡迎收聽
## 🎵 opening
## 開場
- [x] B0001 [0:00–0:01] [Sarah] 哈囉~~嗯~~大家
- [x] B0002 [0:01–0:02] [Sarah] 歡迎收聽 ← 好句
- [ ] B0003 [0:03–0:04] [Sarah] 剪掉的
- [x] B0004 [0:05–0:06] [Mars] 我是Mars
## 收尾
- [x] B0005 [0:08–0:09] [Sarah] 掰掰
"""


def make_session(td: str) -> Path:
    sdir = Path(td) / "ep-test"
    sdir.mkdir()
    (sdir / "cutplan.md").write_text(CUTPLAN_MD, encoding="utf-8")
    (sdir / "cutplan.json").write_text(json.dumps({"blocks": [
        {"id": "B0001", "start": 0.0, "end": 1.0, "text": "哈囉嗯大家"},
        {"id": "B0002", "start": 1.2, "end": 2.0, "text": "歡迎收聽"},
        {"id": "B0003", "start": 3.0, "end": 4.0, "text": "剪掉的"},
        {"id": "B0004", "start": 5.0, "end": 6.0, "text": "我是Mars"},
        {"id": "B0005", "start": 8.0, "end": 9.0, "text": "掰掰"},
    ]}, ensure_ascii=False), encoding="utf-8")
    # final-cut 時間軸:0–2s 映到 10s 起,5–6s 映到 12s 起;8s 起不在任何 range
    (sdir / "cut_map.json").write_text(json.dumps({"ranges": [
        {"src_start": 0.0, "src_end": 2.0, "dst_start": 10.0},
        {"src_start": 5.0, "src_end": 6.0, "dst_start": 12.0},
    ]}), encoding="utf-8")
    return sdir


class TestHms(unittest.TestCase):
    def test_format(self):
        self.assertEqual(hms(0), "00:00:00")
        self.assertEqual(hms(3661.9), "01:01:01")


class TestBuildTranscript(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.sdir = make_session(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_transcript_shape(self):
        out = build_transcript(self.sdir)
        lines = [l for l in out.splitlines() if l]
        self.assertEqual(lines, [
            "### 開場",
            # 同講者連續 block 合併成一句、時間取段首(dst=final-cut 時間)、
            # 刪除線與行尾理由剝掉
            "(00:00:10) Sarah:哈囉嗯大家歡迎收聽",
            "(00:00:12) Mars:我是Mars",
            "### 收尾",
            # cut_map 沒涵蓋的 block:無時間戳
            "Sarah:掰掰",
        ])

    def test_external_insert_blocks_are_in_the_transcript(self):
        """`## ➕` 補錄的 S block 也是節目內容,不能從文案素材裡消失。

        2026-08-12 EP16 實踩:Sarah 的 36.9 秒補錄(S0001–S0014,「工作的火花
        在哪」)整段沒進 copy_prompt.md —— LINE_RE 只收 `B` 開頭。文案引擎
        看不到那 37 秒,寫出來的摘要與章節就會缺一塊。

        S block 的時間碼長在**補錄檔自己的時間軸**上(0 起算),不能拿去查
        cut_map(會對出離譜位置);錨點取它前面那個正片 block 的成品時間
        (與 diff_clips.py 同一套處理,ADR 0015)。
        """
        md = self.sdir / "cutplan.md"
        md.write_text(CUTPLAN_MD + (
            "## ➕ raw/補錄.WAV gain=auto  Sarah 補錄\n"
            "- [x] S0001 [0:03–0:06] [Sarah] 補錄第一句\n"
            "- [x] S0002 [0:06–0:07] [Sarah] 補錄第二句\n"), encoding="utf-8")
        cj = json.loads((self.sdir / "cutplan.json").read_text(encoding="utf-8"))
        cj["blocks"] += [{"id": "S0001", "start": 3.0, "end": 6.0,
                          "text": "補錄第一句", "kind": "insert"},
                         {"id": "S0002", "start": 6.0, "end": 7.0,
                          "text": "補錄第二句", "kind": "insert"}]
        (self.sdir / "cutplan.json").write_text(json.dumps(cj, ensure_ascii=False),
                                                encoding="utf-8")
        out = build_transcript(self.sdir)
        self.assertIn("補錄第一句", out)
        self.assertIn("補錄第二句", out)
        # 錨點=前一個正片 block(B0005,無 dst)→ 不硬掰時間戳,但內容要在
        self.assertNotIn("(00:00:03)", out, "補錄時間碼不可直接當成品時間")

    def test_clip_section_excluded(self):
        # 🎬 集錦區的重複 B0002 不進逐字稿(只出現在正文合併句裡一次)
        out = build_transcript(self.sdir)
        self.assertEqual(out.count("歡迎收聽"), 1)

    def test_unchecked_block_excluded(self):
        self.assertNotIn("剪掉的", build_transcript(self.sdir))

    def test_program_markers_never_become_chapter_headings(self):
        """`## ` 開頭的節目項不是章節標題,不准漏進文案 prompt。

        2026-08-10 `## ✂` 曾經被當章節標題寫進 IG 文案;同日新增 `## ➕`
        補錄插入標記時**又踩同一個坑**(排除清單漏加)。這條測試把整組節目項
        一起鎖住,下次再加新標記時會直接紅燈。
        """
        md = self.sdir / "cutplan.md"
        md.write_text(
            md.read_text(encoding="utf-8")
            + "## ✂ 12.0-13.0 手動剪除說明\n"
            + "## ➕ raw/補錄.WAV gain=auto start=2.6  Sarah 補錄說明\n"
            + "## 🎵 opening fadein=2\n"
            + "## ⚙ max-pause=0.9\n"
            + "## 🎬 精華集錦\n",
            encoding="utf-8")
        out = build_transcript(self.sdir)
        for leak in ("➕", "✂", "🎵", "⚙", "🎬",
                     "補錄.WAV", "gain=auto", "max-pause", "手動剪除說明"):
            self.assertNotIn(leak, out, f"節目項標記漏進文案 prompt:{leak}")


TEMPLATE = """meta 說明(--- 前不進 prompt)
---
# EP{{集數}} 文案任務

素材:
{{素材}}

逐字稿:
{{逐字稿}}
"""


class TestMainE2E(unittest.TestCase):
    def _run(self, sdir: Path, template: Path):
        return subprocess.run(
            [sys.executable, str(AUDIO_DIR / "copy_prompt_build.py"),
             "--session", str(sdir), "--ep", "15", "--template", str(template)],
            capture_output=True, text=True, cwd=REPO_ROOT)

    def test_assembles_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = make_session(td)
            (sdir / "copy_material.md").write_text("本集重點:測試素材",
                                                   encoding="utf-8")
            tpl = Path(td) / "tpl.md"
            tpl.write_text(TEMPLATE, encoding="utf-8")
            proc = self._run(sdir, tpl)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = (sdir / "copy_prompt.md").read_text(encoding="utf-8")
        self.assertIn("# EP15 文案任務", out)          # {{集數}} 代入
        self.assertIn("本集重點:測試素材", out)         # {{素材}} 代入
        self.assertIn("(00:00:10) Sarah:哈囉嗯大家歡迎收聽", out)
        self.assertNotIn("meta 說明", out)             # --- 前的 meta 不進 prompt
        self.assertNotIn("{{", out)                    # 佔位符全數換掉

    def test_missing_cut_map_fails(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = make_session(td)
            (sdir / "copy_material.md").write_text("素材", encoding="utf-8")
            (sdir / "cut_map.json").unlink()
            tpl = Path(td) / "tpl.md"
            tpl.write_text(TEMPLATE, encoding="utf-8")
            proc = self._run(sdir, tpl)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("缺 cut_map.json", proc.stderr + proc.stdout)

    def test_missing_copy_material_fails(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = make_session(td)
            tpl = Path(td) / "tpl.md"
            tpl.write_text(TEMPLATE, encoding="utf-8")
            proc = self._run(sdir, tpl)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("缺 copy_material.md", proc.stderr + proc.stdout)



class TestFinalCutTranscript(unittest.TestCase):
    """時間以**定稿成品重轉的逐字稿**為準,cutplan 只提供講者(2026-08-12 MM)。

    原本從原始時間軸經 cut_map + tempo 回推成品時間,是一條會漂的推導鏈;
    補錄根本不在那條時間軸上(實測文案把它標在 21:10,真實位置 21:51)。
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.sdir = make_session(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_plan_sequence_is_speakers_without_time(self):
        self.assertEqual(plan_sequence(self.sdir),
                         [("Sarah", "哈囉嗯大家"), ("Sarah", "歡迎收聽"),
                          ("Mars", "我是Mars"), ("Sarah", "掰掰")])

    def test_speaker_carried_over_by_text_alignment(self):
        cues = [{"start": 5.0, "end": 6.0, "text": "哈囉嗯大家歡迎收聽"},
                {"start": 6.0, "end": 7.0, "text": "我是Mars"},
                {"start": 9.0, "end": 9.5, "text": "掰掰"}]
        got = [(c["start"], c["speaker"]) for c in
               attach_speakers(cues, plan_sequence(self.sdir))]
        self.assertEqual(got, [(5.0, "Sarah"), (6.0, "Mars"), (9.0, "Sarah")])

    def test_timestamps_come_from_the_final_srt_not_cut_map(self):
        """cut_map 說 B0005「掰掰」對不到任何 range(舊法無時間戳);
        成品逐字稿說它在 00:00:09 —— 以成品為準。"""
        srt = Path(self._td.name) / "final.srt"
        srt.write_text("\n".join([
            "1", "00:00:05,000 --> 00:00:06,000", "哈囉嗯大家歡迎收聽", "",
            "2", "00:00:06,000 --> 00:00:07,000", "我是Mars", "",
            "3", "00:00:09,000 --> 00:00:09,500", "掰掰", "",
        ]), encoding="utf-8")
        out = build_transcript_from_final(self.sdir, srt)
        self.assertEqual(out.splitlines(), [
            "(00:00:05) Sarah:哈囉嗯大家歡迎收聽",
            "(00:00:06) Mars:我是Mars",
            "(00:00:09) Sarah:掰掰",
        ])
        self.assertNotIn("(00:00:10)", out, "不該再出現 cut_map 推導出來的時間")


if __name__ == "__main__":
    unittest.main(verbosity=2)
