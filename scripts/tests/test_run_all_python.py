#!/usr/bin/env python3
"""test_run_all_python.py — run_all.sh 用哪一支 python(驗收 F-3)。

規則(scripts/tests/pick_python.sh):
  1. repo(或 worktree)自己的 .venv-audio/bin/python3.13
  2. 沒有 → git common dir 所在主樹的 .venv-audio/bin/python3.13
  3. 都沒有 → FAIL 並說明,**不准**默默退回系統 python3
系統 python 跟 venv 的套件不同(numpy、whisper…),用錯直譯器跑出來的綠燈
不代表管線在實際環境會綠。

跑法:
    python3 scripts/tests/test_run_all_python.py
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PICK = Path(__file__).resolve().parent / "pick_python.sh"
RUN_ALL = Path(__file__).resolve().parent / "run_all.sh"


def git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def fake_py(root: Path) -> Path:
    p = root / ".venv-audio" / "bin" / "python3.13"
    p.parent.mkdir(parents=True)
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


class TestPickPython(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        t = Path(self.td.name).resolve()
        self.main = t / "main"
        self.main.mkdir()
        git(self.main, "init", "-q")
        (self.main / "f").write_text("x")
        git(self.main, "add", "f")
        git(self.main, "commit", "-qm", "init")
        self.wt = t / "wt"
        git(self.main, "worktree", "add", "-q", str(self.wt))

    def tearDown(self):
        self.td.cleanup()

    def pick(self, root: Path):
        return subprocess.run(["bash", str(PICK), str(root)],
                              capture_output=True, text=True)

    def test_own_venv_first(self):
        fake_py(self.main)
        own = fake_py(self.wt)
        r = self.pick(self.wt)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(own))

    def test_worktree_falls_back_to_main_tree_venv(self):
        main_py = fake_py(self.main)
        r = self.pick(self.wt)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(main_py))

    def test_no_venv_anywhere_fails_loudly(self):
        r = self.pick(self.wt)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".venv-audio", r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_run_all_uses_picker_not_system_python(self):
        src = RUN_ALL.read_text(encoding="utf-8")
        self.assertIn("pick_python.sh", src)
        self.assertNotRegex(src, r"(?m)^\s*py=python3\s*$")


if __name__ == "__main__":
    unittest.main()
