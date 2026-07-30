#!/usr/bin/env bash
# scripts/tests/run_all.sh — 跑全部回歸測試(改 scripts/ 下的管線腳本,改完必跑)
#
# 慣例:每份 test_*.py 都可獨立直接執行(unittest,零 pytest 依賴)。
# test_prosody.py 的 numpy 測試在主環境會 skip,.venv-audio 存在就用它跑該檔。
set -u
cd "$(dirname "$0")/../.."
VENV_PY=".venv-audio/bin/python"
fail=0
for t in scripts/tests/test_*.py; do
  py=python3
  [[ "$t" == *prosody* && -x "$VENV_PY" ]] && py="$VENV_PY"
  if out=$("$py" "$t" 2>&1); then
    echo "PASS  $t — $(echo "$out" | grep -E '^Ran ' | head -1)"
  else
    fail=1
    echo "FAIL  $t"
    echo "$out" | tail -25
  fi
done
if [[ $fail -ne 0 ]]; then
  echo "❌ 有測試失敗 — 修好再動 scripts/audio/"
else
  echo "✅ 全部測試通過"
fi
exit $fail
