#!/usr/bin/env python3
"""test_render_audio.py — 拿真音訊跑的剪輯回歸(fixtures/ep16 的踩坑 case)。

test_render_cut.py 用假資料鎖時間區間運算;這一支相反,拿**真的 words /
silences / 波形**跑完整條剪輯鏈(snap → 字級精剪 → 停頓收緊 → ✂ → 谷底 →
word 保護),驗每個 case 該剪的剪掉了、不該碰的還在。

2026-08-10 的三個 bug(字級對齊退回線性內插、長靜音兩端 snap 會合、word 保護
擋掉真停頓)全部躲過了當時的 38 項單元測試——錯在「算得對但對到錯的字」,
假資料驗不出來。每個 case 就是一個那天的坑。

fixture 由 fixtures/build_fixtures.py 從真 session 產生(音訊裁窗、時間平移
到 0 起算);要加 case 改那支的 CASES。

**紅燈驗證過**(2026-08-10,把修復逐個退回去確認測得到,不是花瓶):
    字級對齊退回「從 win[0] 起算」    → case1 紅
    長靜音 snap 退回「一律取中點」    → case6 紅
    ✂ 移回 word_guard 之前            → case4 紅
case2/3/5 鎖的是同機制的其他表現(剪對哪一個「相」、贅詞落點、短死寂),
對上面三個 revert 不敏感,但擋得住未來動到這幾條路徑的改動。

跑法:
    python3 scripts/tests/test_render_audio.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDER = ROOT / "scripts" / "audio" / "render_cut.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ep16"


def kept_ranges(case: Path) -> list[list[float]]:
    """跑 render 的 dry-run,拿回保留區間(原始時間軸,毫秒精度)。"""
    with tempfile.TemporaryDirectory() as t:
        dump = Path(t) / "ranges.json"
        r = subprocess.run(
            [sys.executable, str(RENDER), "--session", str(case),
             "--dry-run", "--dump-ranges", str(dump)],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"{case.name} render FAIL:\n"
                                 f"{r.stdout[-1500:]}{r.stderr[-1500:]}")
        return json.loads(dump.read_text(encoding="utf-8"))


def covered(ranges: list[list[float]], a: float, b: float) -> float:
    """[a,b] 有多少比例落在保留區間裡。"""
    hit = sum(max(0.0, min(b, y) - max(a, x)) for x, y in ranges)
    return hit / (b - a) if b > a else 0.0


class TestEP16Regressions(unittest.TestCase):
    """每個 fixture case 一組斷言;case 目錄是自足的迷你 session。"""


def _make(case: Path):
    exp = json.loads((case / "expect.json").read_text(encoding="utf-8"))

    def test(self: unittest.TestCase) -> None:
        ranges = kept_ranges(case)
        self.assertTrue(ranges, f"{case.name}: 一段都沒保留")
        for a, b in exp["must_cut"]:
            # 只驗中段:剪點兩側各有 40ms 的 pad/谷底微調,那是設計不是缺陷,
            # 卡死到毫秒會讓測試對無關的參數調整過敏
            m = (b - a) * 0.25
            c = covered(ranges, a + m, b - m)
            self.assertLess(
                c, 0.05,
                f"\n{case.name} — 該剪掉的還在 {c:.0%}:"
                f"[{a + m:.2f}, {b - m:.2f}]\n"
                f"  {exp['why']}\n  實際保留:{ranges}")
        for a, b in exp["must_keep"]:
            c = covered(ranges, a, b)
            self.assertGreater(
                c, 0.90,
                f"\n{case.name} — 不該碰的被切掉了,只剩 {c:.0%}:"
                f"[{a:.2f}, {b:.2f}]\n  {exp['why']}\n  實際保留:{ranges}")

    test.__doc__ = exp["why"]
    return test


if not FIXTURES.is_dir():
    print(f"[test] ⚠ 找不到 fixtures:{FIXTURES}"
          "(跑 fixtures/build_fixtures.py 產生)", file=sys.stderr)
else:
    for _c in sorted(p for p in FIXTURES.iterdir() if (p / "expect.json").exists()):
        setattr(TestEP16Regressions, f"test_{_c.name}", _make(_c))


if __name__ == "__main__":
    unittest.main(verbosity=2)
