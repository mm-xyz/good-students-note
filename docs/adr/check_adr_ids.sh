#!/usr/bin/env bash
# docs/adr/check_adr_ids.sh — 機械檢查 ADR 撞號（卡 #673）
#
# 兩種撞號都會被抓：
#   1. 舊序號檔名前綴撞號（例如 0017-a.md 與 0017-b.md 都用 0017）
#   2. 新格式檔案內文「識別碼：」欄位重複（例如兩份不同檔名的 .md
#      都寫著 識別碼：ADR-2026-08-11-674）
#
# 用法：bash docs/adr/check_adr_ids.sh
# 撞號 → 印出清單、exit 1；乾淨 → 印一行 OK、exit 0。

set -euo pipefail

adr_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$adr_dir"

status=0

# --- 1. 舊序號檔名前綴撞號 -------------------------------------------------
dup_prefixes="$(
  find . -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9]-*.md' -print0 \
    | xargs -0 -n1 basename \
    | cut -c1-4 \
    | sort \
    | uniq -d
)"

if [[ -n "$dup_prefixes" ]]; then
  status=1
  echo "撞號（舊序號檔名前綴重複）："
  while IFS= read -r prefix; do
    [[ -z "$prefix" ]] && continue
    find . -maxdepth 1 -type f -name "${prefix}-*.md" -print | sed 's/^/  /'
  done <<< "$dup_prefixes"
fi

# --- 2. 新格式「識別碼：」欄位重複 -----------------------------------------
dup_ids="$(
  grep -h '^- 識別碼：' ./*.md 2>/dev/null \
    | sed 's/^- 識別碼：//' \
    | sort \
    | uniq -d
)"

if [[ -n "$dup_ids" ]]; then
  status=1
  echo "撞號（識別碼欄位重複）："
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    grep -l "^- 識別碼：${id}\$" ./*.md | sed 's/^/  /'
  done <<< "$dup_ids"
fi

if [[ "$status" -eq 0 ]]; then
  echo "OK：docs/adr/ 無撞號"
fi

exit "$status"
