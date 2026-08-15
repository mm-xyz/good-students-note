#!/usr/bin/env bash
# scripts/tests/run_all.sh — 跑全部回歸測試(改 scripts/ 下的管線腳本,改完必跑)
#
# 慣例:每份 test_*.py 都可獨立直接執行(unittest,零 pytest 依賴)。
# test_prosody.py 的 numpy 測試在主環境會 skip,.venv-audio 存在就用它跑該檔。
# 文件線三份(test_doc_extract/test_doc_figures/test_session_doc_line)需要
# fitz/ebooklib/lxml/PIL(見 requirements-doc.txt),主環境沒裝,獨立用
# .venv-doc/bin/python -m pytest 跑(見下段);.venv-doc 不存在則印安裝提示、
# 跳過該段,不讓整支 run_all 失敗。
set -u
cd "$(dirname "$0")/../.."
VENV_PY=".venv-audio/bin/python"
DOC_VENV_PY=".venv-doc/bin/python"
DOC_TESTS=(scripts/tests/test_doc_extract.py scripts/tests/test_doc_figures.py scripts/tests/test_session_doc_line.py)
fail=0

# ── 音檔線(既有,零改動)──────────────────────────────────────────
for t in scripts/tests/test_*.py; do
  skip=0
  for d in "${DOC_TESTS[@]}"; do
    [[ "$t" == "$d" ]] && skip=1
  done
  [[ $skip -eq 1 ]] && continue
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

# ── 文件線(doc/session,需 .venv-doc)────────────────────────────────
if [[ -x "$DOC_VENV_PY" ]]; then
  if out=$("$DOC_VENV_PY" -m pytest "${DOC_TESTS[@]}" 2>&1); then
    echo "PASS  ${DOC_TESTS[*]} — $(echo "$out" | grep -E '^=+ .*passed' | tail -1)"
  else
    fail=1
    echo "FAIL  ${DOC_TESTS[*]}"
    echo "$out" | tail -40
  fi
else
  echo "SKIP  文件線測試(test_doc_extract/test_doc_figures/test_session_doc_line)"
  echo "      — .venv-doc 不存在,安裝見 requirements-doc.txt"
fi

if [[ $fail -ne 0 ]]; then
  echo "❌ 有測試失敗 — 修好再動 scripts/audio/"
else
  echo "✅ 全部測試通過"
fi
exit $fail
