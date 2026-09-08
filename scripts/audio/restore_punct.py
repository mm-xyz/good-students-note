#!/usr/bin/env python3
"""
scripts/audio/restore_punct.py — 用本地 LLM 補回逐字稿的標點，時間軸與字元完全不動。

用途：whisper 偶發整份輸出零標點（成因見 ADR-2026-09-07-asr-segmented-transcription），
prompt 改短能救大部分但不是全部。這支負責收拾殘餘那幾份。

核心是**驗證**不是呼叫：LLM 只被允許「插入標點」，其餘一律不准動。
每個 chunk 都跑一次硬檢查——把輸出的標點與空白全部剝掉之後，
字元序列必須與輸入**完全相同**；不同就退回原文，那個 chunk 不採用。
所以最壞情況是「沒補到」，不會是「被改字」。

補完的標點會照原本的 cue 邊界放回去（走非標點字元計數對齊），
時間軸、cue 數、cue 編號一個都不變。

用法：
    python3 restore_punct.py <transcript.srt> [-o OUT] [--dry-run]
    python3 restore_punct.py <episodes 根目錄> --from-list <重轉清單.txt>
"""

import argparse
import json
import os
import pathlib
import re
import sys
import unicodedata
import urllib.request

# 允許 LLM 插入的標點（剝除比對時也用這一組）
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
    """剝掉標點與空白，留下純字元序列——比對用的不變量。"""
    return "".join(c for c in s if c not in PUNCT and c not in WS)


def parse_srt(path: pathlib.Path):
    """回傳 [(index, timing, [text lines])]，保留原始結構。"""
    blocks = []
    for blk in path.read_text(encoding="utf-8").split("\n\n"):
        lines = [l for l in blk.split("\n") if l.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            blocks.append((lines[0], lines[1], lines[2:]))
    return blocks


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


def redistribute(punctuated: str, cue_texts: list[str]) -> list[str]:
    """把補好標點的整段文字，照原本每個 cue 的字元數切回去。

    非標點字元的序列兩邊完全相同（已由 verify 保證），所以可以逐字元走：
    每個 cue 收滿它原有的非標點字元數之後，再把緊跟其後的標點一併吸收。
    """
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
            token: str, chunk_chars: int, dry: bool, threshold: float = 60.0) -> bool:
    blocks = parse_srt(path)
    if not blocks:
        print(f"  ❌ 解析不到 cue：{path}", file=sys.stderr)
        return False
    cue_texts = ["".join(b[2]) for b in blocks]

    # ⚠️ 已經有標點的檔案一律跳過,不要重跑。
    # 硬檢查（剝掉標點後字元序列相同）擋得住「改字」,但擋不住「重複標點」——
    # 「，，」剝掉之後與「，」相同,會通過驗證。所以重跑已補好的檔案是有風險的,
    # 唯一安全的做法是不要重跑（2026-09-08 差點實踩）。
    d0 = density(cue_texts)
    if d0 <= threshold:
        print(f"  ⏭  已達標（每 {d0:.1f} 字一個標點），跳過")
        return True

    # 以 cue 為單位切 chunk，不切在句子中間
    chunks, cur, cur_len = [], [], 0
    for idx, t in enumerate(cue_texts):
        cur.append(idx); cur_len += len(t)
        if cur_len >= chunk_chars:
            chunks.append(cur); cur, cur_len = [], 0
    if cur:
        chunks.append(cur)

    new_texts = list(cue_texts)
    stats = {"ok": 0, "failed": 0, "retried": 0}

    def attempt(idxs, depth=0):
        """對一組 cue 補標點；驗證不過就對半切重試，壞點只會影響更小的範圍。

        實測 700 字的 chunk 約有兩三成會被 LLM 改到字（含長度不變的置換），
        整塊放棄太浪費——對半之後多半只有一半是壞的。
        """
        flat = "".join(cue_texts[i] for i in idxs)
        if not strip_marks(flat):
            return
        try:
            got = unicodedata.normalize("NFC", call_llm(flat, url, model, token))
        except Exception as e:
            print(f"  ⚠️  呼叫失敗（{len(idxs)} cue），保留原文：{e}", flush=True)
            stats["failed"] += 1
            return
        a, b = strip_marks(flat), strip_marks(got)
        if a == b:
            # 剝標點比對擋不住「重複標點」（「，，」剝完與「，」相同），單獨擋一次
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
                  f"對半重試 {len(idxs)} cue", flush=True)
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
    ok_chunks, failed = stats["ok"], stats["failed"]
    if stats["retried"]:
        print(f"  （對半重試 {stats['retried']} 次）", flush=True)

    before = sum(1 for t in cue_texts for c in t if c in PUNCT)
    after = sum(1 for t in new_texts for c in t if c in PUNCT)
    total = len(strip_marks("".join(new_texts)))
    print(f"  chunk {ok_chunks} 成功 / {failed} 保留原文；"
          f"標點 {before} → {after}；密度 每 {total/max(1,after):.1f} 字")

    if dry:
        return ok_chunks > 0

    out = []
    for (idx, timing, _), text in zip(blocks, new_texts):
        out.append(f"{idx}\n{timing}\n{text}\n")
    out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return ok_chunks > 0


def main():
    ap = argparse.ArgumentParser(description="用本地 LLM 補回逐字稿標點（字元不可變）")
    ap.add_argument("target", help="單一 transcript.srt，或 episodes 根目錄")
    ap.add_argument("--from-list", help="重轉清單（一行一個子目錄名），配合 episodes 根目錄")
    ap.add_argument("-o", "--output", help="輸出路徑（單檔模式；預設原地覆寫）")
    ap.add_argument("--url", default=os.environ.get("LLM_NODE_URL", DEFAULT_URL))
    ap.add_argument("--model", default=os.environ.get("LLM_NODE_MODEL", DEFAULT_MODEL))
    ap.add_argument("--token", default=os.environ.get("LLM_NODE_TOKEN", ""))
    ap.add_argument("--chunk-chars", type=int, default=700)
    ap.add_argument("--dry-run", action="store_true", help="只跑不寫檔")
    args = ap.parse_args()

    target = pathlib.Path(args.target)
    targets = []
    if args.from_list:
        names = [l.strip() for l in pathlib.Path(args.from_list).read_text(encoding="utf-8").splitlines() if l.strip()]
        for n in names:
            p = target / n / "transcript.srt"
            if p.exists():
                targets.append(p)
            else:
                print(f"⚠️  找不到：{p}", file=sys.stderr)
    else:
        targets = [target]

    print(f"目標 {len(targets)} 份；模型 {args.model}；chunk {args.chunk_chars} 字"
          + ("（dry-run）" if args.dry_run else ""))
    rc = 0
    for p in targets:
        print(f"\n{p.parent.name[:44]}")
        out = pathlib.Path(args.output) if (args.output and not args.from_list) else p
        if not process(p, out, args.url, args.model, args.token, args.chunk_chars, args.dry_run):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
