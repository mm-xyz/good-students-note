#!/usr/bin/env bash
# scripts/tests/run_all.sh — 跑全部回歸測試(改 scripts/ 下的管線腳本,改完必跑)
#
# 慣例:每份 test_*.py 都可獨立直接執行(unittest,零 pytest 依賴)。
# 音檔線全部用 .venv-audio/bin/python3.13 跑(pick_python.sh;找不到就 FAIL)。
# 文件線三份(test_doc_extract/test_doc_figures/test_session_doc_line)需要
# fitz/ebooklib/lxml/PIL(見 requirements-doc.txt),主環境沒裝,獨立用
# .venv-doc/bin/python -m pytest 跑(見下段);.venv-doc 不存在則印安裝提示、
# 跳過該段,不讓整支 run_all 失敗。
set -u
cd "$(dirname "$0")/../.."
# 音檔線一律用 .venv-audio 的 python3.13(worktree 沒有就用主樹的;都沒有 FAIL,
# 不默默用系統 python —— 驗收 F-3,規則見 pick_python.sh)
if ! PY="$(bash scripts/tests/pick_python.sh "$PWD")"; then
  exit 2
fi
echo "python: $PY"
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
  if out=$("$PY" "$t" 2>&1); then
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
