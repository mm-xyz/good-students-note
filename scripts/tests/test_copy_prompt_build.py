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

from copy_prompt_build import build_transcript, hms  # noqa: E402

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
