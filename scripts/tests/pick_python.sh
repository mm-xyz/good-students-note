#!/usr/bin/env bash
# scripts/tests/pick_python.sh <repo-root> — 印出 run_all.sh 該用的 python(驗收 F-3)
#
#   1. <repo-root>/.venv-audio/bin/python3.13(repo 或 worktree 自己的)
#   2. 沒有 → git common dir 所在主樹的 .venv-audio/bin/python3.13
#      (worktree 不會帶 venv:它在 .gitignore 外面另裝,只有主樹有)
#   3. 都沒有 → stderr 說明、exit 2。**不退回系統 python3**:系統 python 的套件
#      跟 venv 不同,在那上面綠不代表管線在實際環境綠。
set -u
root="${1:?用法: pick_python.sh <repo-root>}"
rel=".venv-audio/bin/python3.13"
if [[ -x "$root/$rel" ]]; then
  echo "$root/$rel"
  exit 0
fi
common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "$common" ]]; then
  main="$(dirname "$common")"
  if [[ -x "$main/$rel" ]]; then
    echo "$main/$rel"
    exit 0
  fi
fi
echo "[run_all] FAIL: 找不到 $rel(找過 $root${common:+ 與主樹 $(dirname "$common")})。" >&2
echo "  安裝:python3.13 -m venv .venv-audio && .venv-audio/bin/pip install -r requirements-audio.txt" >&2
echo "  不退回系統 python3 —— 套件不同,綠燈不算數。" >&2
exit 2
