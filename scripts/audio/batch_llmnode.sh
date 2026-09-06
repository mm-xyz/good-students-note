#!/usr/bin/env bash
# 批次把 llm-node 上一整個資料夾的影片/音檔轉成逐字稿(transcript.srt + words.json)。
#
# 推理跑在 llm-node、媒體不搬動(--remote-media),Mac 只做後製(OpenCC + 短句切分)。
# resume-safe:已產出且非空的 transcript.srt 會跳過,中斷後直接重跑同一行指令即可。
#
#   scripts/audio/batch_llmnode.sh <llm-node 上的資料夾> <本地輸出根目錄> [context.txt]
#
# 輸出:<輸出根>/<原檔名去副檔名>/transcript.srt(＋ words.json)

set -uo pipefail

REMOTE_DIR=${1:?用法: batch_llmnode.sh <llm-node 資料夾> <輸出根> [context.txt]}
OUT_ROOT=${2:?用法: batch_llmnode.sh <llm-node 資料夾> <輸出根> [context.txt]}
CONTEXT=${3:-}

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO_DIR/.venv-audio/bin/python"
ASR="$REPO_DIR/scripts/audio/transcribe_llmnode.py"
HOST=${LLM_NODE_SSH:-llm-node}

[ -x "$PY" ] || { echo "缺 .venv-audio(需要 opencc)" >&2; exit 3; }
mkdir -p "$OUT_ROOT"

# macOS 內建 bash 3.2 沒有 mapfile,用 while-read 收集(檔名含空白與中文,靠 IFS= 保住)
FILES=()
while IFS= read -r line; do
  [ -n "$line" ] && FILES+=("$line")
done < <(ssh "$HOST" "ls -1 '$REMOTE_DIR'" | grep -iE '\.(mp4|mov|mkv|webm|m4v|mp3|m4a|wav|flac)$')

total=${#FILES[@]}
[ "$total" -gt 0 ] || { echo "$REMOTE_DIR 沒有可轉錄的檔案" >&2; exit 1; }
echo "[batch] $total 個檔案 → $OUT_ROOT"

ok=0; skip=0; fail=0; failed_names=()
i=0
while [ "$i" -lt "$total" ]; do
  f=${FILES[$i]}
  i=$((i+1))
  stem=${f%.*}
  dir="$OUT_ROOT/$stem"
  srt="$dir/transcript.srt"
  if [ -s "$srt" ]; then
    skip=$((skip+1)); continue
  fi
  mkdir -p "$dir"
  echo "[batch] ($i/$total) $stem"
  cmd=("$PY" "$ASR" "$f" -o "$srt" --remote-media "$REMOTE_DIR/$f")
  [ -n "$CONTEXT" ] && cmd+=(--context "$CONTEXT")
  if "${cmd[@]}"; then
    ok=$((ok+1))
  else
    fail=$((fail+1)); failed_names+=("$stem")
    rm -f "$srt"   # 不留半成品,下次重跑才會重試
    echo "[batch] FAILED: $stem" >&2
  fi
done

echo "[batch] 完成:成功 $ok / 跳過(已存在) $skip / 失敗 $fail"
if [ "$fail" -gt 0 ]; then
  printf '[batch] 失敗清單:\n'; printf '  - %s\n' "${failed_names[@]}"
  exit 1
fi
