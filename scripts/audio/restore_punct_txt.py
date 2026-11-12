#!/usr/bin/env python3
"""
scripts/audio/restore_punct_txt.py — 用本地 LLM 補回「純文字」逐字稿的標點，字元序列完全不動。

與 restore_punct.py 的關係（機制原封不動搬過來，只換 I/O 格式）：
- 核心是**驗證**不是呼叫：LLM 只被允許「插入標點」，其餘一律不准動。
  每個 chunk 都跑一次硬檢查——把輸出的標點與空白全部剝掉之後，字元序列必須與輸入
  **完全相同**；不同就退回原文，那個 chunk 不採用。所以最壞情況是「沒補到」，
  不會是「被改字」。
- 同一個 endpoint（llm-node，OpenAI 相容 /v1/chat/completions）、同一組 SYSTEM/USER
  prompt、同一組允許插入的 PUNCT 集合、同一個 strip_marks() 剝除比對邏輯、同一個
  「chunk 驗證失敗就對半重試，最終還是不過就退回原文」策略、同一個「已達密度門檻就跳過
  不重跑」的安全閘（重跑已補好的檔案有「，，」剝完等於「，」的風險，見 restore_punct.py
  process() 的註解）。density()/strip_marks()/call_llm() 三個函式的邏輯逐行對應。
- 差異只在容器格式：restore_punct.py 吃 SRT，cue = 帶時間戳的字幕區塊，輸出要保留
  時間軸與 cue 編號、跨 cue 合併。這支吃純 txt，cue = 一行文字，輸出只需保留「行對行」
  的對應關係與行數，沒有時間軸、沒有 cue 編號。redistribute() 沿用「非標點字元計數
  對齊」的做法，只是切回去的單位從 SRT cue 換成 txt 的行。
- 用途：賴佩霞課程逐字稿（純 txt，一行一句、無時間戳）偶發整篇零標點，
  跟 restore_punct.py 處理的 whisper 零標點是同一種病灶，只是容器格式不同、
  也不需要時間軸這件事。
- 額外新增（restore_punct.py 沒有）：對「連線失敗」（llama-swap 重載模型中／
  暫時斷線）做有限次數退避重試，跟「驗證不過」是兩件事分開處理——連線失敗重試，
  驗證失敗（字元被改）永遠不重試、直接退回原文。理由：2026-09-30 實跑 48 檔批次時，
  llm-node 的 gemma-12b 在跑到第 2 個檔案時開始整批 Connection refused，
  重新手動呼叫證實模型會重新載入成功，只是需要時間；批次模式下沒有重試會讓後面
  40+ 檔全部落空。

用法：
    # 單檔
    python3 restore_punct_txt.py <transcript.txt> [-o OUT] [--dry-run]

    # 整個目錄批次跑，輸出到另一個目錄（檔名不變，原檔不動）
    python3 restore_punct_txt.py <來源目錄> --out-dir <輸出目錄> [--glob "*.txt"]
        [--stats-json <路徑>]   # 逐檔統計（chunk 數／退回數／密度）寫成 JSON，供驗收用
"""

import argparse
import glob as globmod
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

# 連線失敗（非驗證失敗）的重試退避秒數；驗證失敗（字元被改）不受此影響，永不重試。
RETRY_BACKOFFS = (10, 30, 60)

# 允許 LLM 插入的標點（剝除比對時也用這一組）—— 與 restore_punct.py 完全相同
PUNCT = set("。，、！？；：「」『』（）〈〉《》…—～·-.,!?;:\"'()[]")
WS = set(" \t　")

DEFAULT_URL = "http://100.120.197.113:8080/v1"
DEFAULT_MODEL = "gemma-12b"

SYSTEM = "你是中文標點校對工具。只做一件事：在句子之間插入標點符號。"

USER_TMPL = """在下面這段中文逐字稿裡加上標點符號。

嚴格規則：
1. 只能「插入」標點符號（，。、？！；：）
2. 不可以增加、刪除、修改任何一個文字
3. 不可以調整語序、不可以斷行、不可以加空白
4. 不要加引號、括號、破折號
5. 直接輸出加好標點的結果，不要任何說明或前後綴

原文：
{text}"""


def strip_marks(s: str) -> str:
    """剝掉標點與空白，留下純字元序列——比對用的不變量。與 restore_punct.py 相同。"""
    return "".join(c for c in s if c not in PUNCT and c not in WS)


def parse_txt(path: pathlib.Path):
    """讀純 txt，回傳 (lines, trailing_newline)。

    lines：一行一個 cue（不含行尾換行符）。
    trailing_newline：原檔是否以換行結尾（寫回時要保留，避免 diff 噪音）。
    """
    raw = path.read_text(encoding="utf-8")
    parts = raw.split("\n")
    trailing_newline = parts and parts[-1] == ""
    if trailing_newline:
        parts = parts[:-1]
    return parts, trailing_newline


def call_llm(text: str, url: str, model: str, token: str = "") -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER_TMPL.format(text=text)}],
        "temperature": 0,
        "max_tokens": len(text) * 3 + 256,
    }).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token or 'x'}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"].strip()


def call_llm_retrying(text: str, url: str, model: str, token: str) -> str:
    """call_llm() 加上連線失敗退避重試。只對「連不上／被拒絕」重試，不對驗證失敗重試。"""
    last_err = None
    for attempt_no, backoff in enumerate((0,) + RETRY_BACKOFFS):
        if backoff:
            print(f"  ⏳ 連線失敗，{backoff}s 後重試（第 {attempt_no}/{len(RETRY_BACKOFFS)} 次）…", flush=True)
            time.sleep(backoff)
        try:
            return call_llm(text, url, model, token)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            last_err = e
            continue
    raise last_err


def redistribute(punctuated: str, cue_texts: list[str]) -> list[str]:
    """把補好標點的整段文字，照原本每一行的字元數切回去。與 restore_punct.py 相同邏輯。"""
    out, i = [], 0
    for cue in cue_texts:
        need = len(strip_marks(cue))
        buf, got = [], 0
        while i < len(punctuated) and got < need:
            ch = punctuated[i]
            buf.append(ch)
            if ch not in PUNCT and ch not in WS:
                got += 1
            i += 1
        while i < len(punctuated) and punctuated[i] in PUNCT:
            buf.append(punctuated[i])
            i += 1
        out.append("".join(buf))
    return out


def density(texts: list[str]) -> float:
    """每 N 字一個標點；N 越小標點越密。無標點時回傳很大的數。"""
    flat = "".join(texts)
    n = len([c for c in flat if c not in PUNCT and c not in WS])
    p = len([c for c in flat if c in PUNCT])
    return n / p if p else float("inf")


def process(path: pathlib.Path, out_path: pathlib.Path, url: str, model: str,
            token: str, chunk_chars: int, dry: bool, threshold: float = 60.0) -> dict:
    """處理單一 txt 檔。回傳統計 dict（供批次模式彙整與驗收用）。"""
    lines, trailing_newline = parse_txt(path)
    cue_texts = lines
    stat = {
        "file": path.name, "ok": True, "skipped": False,
        "lines": len(cue_texts), "chunks": 0, "chunk_ok": 0, "chunk_failed": 0,
        "chunk_retried": 0, "density_before": None, "density_after": None,
        "punct_before": 0, "punct_after": 0,
    }

    if not cue_texts or not strip_marks("".join(cue_texts)):
        print(f"  ❌ 檔案沒有內容：{path}", file=sys.stderr)
        stat["ok"] = False
        return stat

    d0 = density(cue_texts)
    stat["density_before"] = None if d0 == float("inf") else round(d0, 2)
    stat["punct_before"] = sum(1 for t in cue_texts for c in t if c in PUNCT)

    # ⚠️ 已達標就跳過，不重跑（理由與 restore_punct.py 相同：
    # 剝標點比對擋不住「重複標點」，重跑已補好的檔案有風險）。
    if d0 <= threshold:
        print(f"  ⏭  已達標（每 {d0:.1f} 字一個標點），跳過")
        stat["skipped"] = True
        stat["density_after"] = stat["density_before"]
        stat["punct_after"] = stat["punct_before"]
        if not dry:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return stat

    # 以行為單位切 chunk，不切在行中間
    chunks, cur, cur_len = [], [], 0
    for idx, t in enumerate(cue_texts):
        cur.append(idx); cur_len += len(t)
        if cur_len >= chunk_chars:
            chunks.append(cur); cur, cur_len = [], 0
    if cur:
        chunks.append(cur)
    stat["chunks"] = len(chunks)

    new_texts = list(cue_texts)
    stats = {"ok": 0, "failed": 0, "retried": 0}

    def attempt(idxs, depth=0):
        flat = "".join(cue_texts[i] for i in idxs)
        if not strip_marks(flat):
            return
        try:
            got = unicodedata.normalize("NFC", call_llm_retrying(flat, url, model, token))
        except Exception as e:
            print(f"  ⚠️  呼叫失敗（{len(idxs)} 行，已重試 {len(RETRY_BACKOFFS)} 次），保留原文：{e}", flush=True)
            stats["failed"] += 1
            return
        a, b = strip_marks(flat), strip_marks(got)
        if a == b:
            dbl = re.findall(r"[。，、！？；：]{2,}", got)
            if dbl:
                print(f"  ⚠️  輸出有 {len(dbl)} 處重複標點（如 {dbl[0]}），保留原文", flush=True)
                stats["failed"] += 1
                return
            for j, t in zip(idxs, redistribute(got, [cue_texts[i] for i in idxs])):
                new_texts[j] = t
            stats["ok"] += 1
            return
        diff = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
        if len(idxs) > 1 and depth < 3:
            print(f"  ↻ 驗證失敗（{len(a)}→{len(b)} 字，差異在第 {diff} 字），"
                  f"對半重試 {len(idxs)} 行", flush=True)
            stats["retried"] += 1
            mid = len(idxs) // 2
            attempt(idxs[:mid], depth + 1)
            attempt(idxs[mid:], depth + 1)
        else:
            print(f"  ⚠️  驗證失敗且無法再切（{len(a)}→{len(b)} 字），保留原文", flush=True)
            stats["failed"] += 1

    for ci, idxs in enumerate(chunks, 1):
        print(f"  chunk {ci}/{len(chunks)} …", flush=True)
        attempt(idxs)

    stat["chunk_ok"] = stats["ok"]
    stat["chunk_failed"] = stats["failed"]
    stat["chunk_retried"] = stats["retried"]

    before = stat["punct_before"]
    after = sum(1 for t in new_texts for c in t if c in PUNCT)
    total = len(strip_marks("".join(new_texts)))
    stat["punct_after"] = after
    d1 = total / after if after else float("inf")
    stat["density_after"] = None if d1 == float("inf") else round(d1, 2)
    print(f"  chunk {stats['ok']} 成功 / {stats['failed']} 保留原文；"
          f"標點 {before} → {after}；密度 每 {d1:.1f} 字")

    if dry:
        return stat

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(new_texts)
    if trailing_newline:
        body += "\n"
    out_path.write_text(body, encoding="utf-8")
    return stat


def main():
    ap = argparse.ArgumentParser(description="補回純文字逐字稿標點（字元不可變；不覆蓋原檔）")
    ap.add_argument("target", help="單一 .txt 檔，或來源目錄（配合 --out-dir 批次跑）")
    ap.add_argument("-o", "--output", help="輸出路徑（單檔模式）")
    ap.add_argument("--out-dir", help="輸出目錄（目錄批次模式；檔名與來源相同）")
    ap.add_argument("--glob", default="*.txt", help="目錄批次模式的檔案樣式，預設 *.txt")
    ap.add_argument("--stats-json", help="把逐檔統計寫成 JSON（批次模式用於驗收）")
    ap.add_argument("--url", default=os.environ.get("LLM_NODE_URL", DEFAULT_URL))
    ap.add_argument("--model", default=os.environ.get("LLM_NODE_MODEL", DEFAULT_MODEL))
    ap.add_argument("--token", default=os.environ.get("LLM_NODE_TOKEN", ""))
    ap.add_argument("--chunk-chars", type=int, default=700)
    ap.add_argument("--dry-run", action="store_true", help="只跑不寫檔")
    ap.add_argument("--threshold", type=float, default=60.0, help="密度達標門檻（每 N 字一個標點）")
    args = ap.parse_args()

    target = pathlib.Path(args.target)
    all_stats = []
    rc = 0

    if args.out_dir:
        if not target.is_dir():
            print(f"❌ --out-dir 模式需要 target 是目錄：{target}", file=sys.stderr)
            return 1
        out_dir = pathlib.Path(args.out_dir)
        files = sorted(target.glob(args.glob))
        print(f"目標 {len(files)} 份（{target}）；輸出 {out_dir}；模型 {args.model}；"
              f"chunk {args.chunk_chars} 字" + ("（dry-run）" if args.dry_run else ""))
        for i, f in enumerate(files, 1):
            print(f"\n[{i}/{len(files)}] {f.name}")
            out_path = out_dir / f.name
            stat = process(f, out_path, args.url, args.model, args.token,
                            args.chunk_chars, args.dry_run, args.threshold)
            all_stats.append(stat)
            if not stat["ok"]:
                rc = 1
    else:
        out_path = pathlib.Path(args.output) if args.output else target
        stat = process(target, out_path, args.url, args.model, args.token,
                        args.chunk_chars, args.dry_run, args.threshold)
        all_stats.append(stat)
        if not stat["ok"]:
            rc = 1

    if args.stats_json:
        pathlib.Path(args.stats_json).write_text(
            json.dumps(all_stats, ensure_ascii=False, indent=2), encoding="utf-8")

    return rc


if __name__ == "__main__":
    sys.exit(main())
