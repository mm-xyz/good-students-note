#!/usr/bin/env python3
"""test_insert_prepare.py — 補錄 block 產生器的 whisper artifact 守門(#675)。

同根因掃描:補錄(insert_prepare.py)跟正片(cutplan.py)一樣是 whisper ASR
轉出來的文字,一樣可能陷入重複迴圈,守門判準共用 cutplan.detect_asr_artifact()
(不重造一份判準)。

跑法:
    python3 scripts/tests/test_insert_prepare.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
sys.path.insert(0, str(AUDIO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from insert_prepare import build_blocks, md_lines  # noqa: E402
from fixtures.ep16_artifact_samples import (  # noqa: E402
    B0068_ARTIFACT_TEXT, B0067_CLEAN_TEXT)


def cue(start, end, text):
    return {"start": start, "end": end, "text": text}


class TestInsertBuildBlocksArtifactGuard(unittest.TestCase):
    def test_artifact_cue_flagged(self):
        blocks = build_blocks([cue(0.0, 20.4, B0068_ARTIFACT_TEXT)], "Sarah")
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0]["asr_artifact"])
        self.assertIn("⚠ASR-artifact", blocks[0]["reason"])
        self.assertTrue(blocks[0]["keep"])  # 只標記,不自動剪

    def test_clean_cue_not_flagged(self):
        blocks = build_blocks([cue(0.0, 10.0, B0067_CLEAN_TEXT)], "Sarah")
        self.assertFalse(blocks[0]["asr_artifact"])
        self.assertNotIn("reason", blocks[0])

    def test_md_lines_shows_marker_only_on_artifact(self):
        blocks = build_blocks(
            [cue(0.0, 1.0, B0067_CLEAN_TEXT), cue(1.0, 21.4, B0068_ARTIFACT_TEXT)],
            "Sarah")
        lines = md_lines(blocks)
        self.assertNotIn("⚠ASR-artifact", lines[0])
        self.assertIn("⚠ASR-artifact", lines[1])
        self.assertTrue(lines[1].startswith("- [x] S0002 "))


if __name__ == "__main__":
    unittest.main(verbosity=2)
