#!/usr/bin/env python3
"""scripts/audio/check_punct_density.py — 逐字稿標點密度稽核(零 LLM)。

whisper 的 `--prompt` 寫成頓號分隔的專名列舉,會讓整份逐字稿**一個標點都沒有**
(見 docs/adr/ADR-2026-09-07-asr-segmented-transcription.md)。這個失敗沒有其他
症狀:cue 數、總字數、時間軸單調性、簡繁轉換全部正常,**只有標點密度會暴露**。
下游短句切分靠標點與 ≥0.5s 停頓找切點,零標點就切不出 cue。

判準用**密度**(每 N 字一個標點)不用標點數 —— 長短檔的標點數沒有可比性。
實測正常中文落在每 10–40 字一個(本 repo:EP16 每 32.0、EP18 每 5.1),壞掉的
是每幾千字才一個(EP15 是 11602 字 / 5 標點 = 每 2320 字)或整份 0 個。兩群相差
兩個數量級,所以預設門檻 60 有很寬的安全邊界,不必為了幾個百分點去調。

    # 稽核 batch_llmnode.sh 的輸出根(遞迴找 <子目錄>/transcript.srt)
    python3 scripts/audio/check_punct_density.py <輸出根>

    # 只印重轉清單(一行一個目錄名),餵回批次
    python3 scripts/audio/check_punct_density.py <輸出根> --list

    # 當 gate 用:有任何一份不合格就 exit 1
    python3 scripts/audio/check_punct_density.py <輸出根> --check

字數＝去掉空白與標點後的字元數(與 whisper 輸出的中文逐字稿對齊)。密度校準在
中文上,非中文為主的檔會標 ⚠ 但不判定失敗。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt  # noqa: E402

# 全形＋半形都要算:whisper.cpp 的逗號輸出**半形 `,`**、句號輸出全形 `。`,
# 只算全形會把正常的檔誤判成零標點。
PUNCT = set("。，、！？；：…・.,!?;:")

FAIL_ZERO = "零標點"
FAIL_SPARSE = "密度不足"
FAIL_MISSING = "缺逐字稿"


def measure(text: str) -> tuple[int, int, float | None, float]:
    """→ (字數, 標點數, 密度=每 N 字一個標點 或 None, CJK 佔比)。

    字數不含空白與標點;密度在零標點時是 None(不是無限大,避免下游要處理 inf)。

    ⚠️ **連續標點算一個**。whisper 用 `...` 標停頓,逐字元算的話一個省略號就是
    3 個標點:EP18 實測 6350 字 / 3250 標點字元 = 每 2.0 字一個,密度整個失真,
    真正零標點但省略號很多的檔會被放行。數的是「句界記號」不是標點字元。
    """
    body = [c for c in text if not c.isspace() and c not in PUNCT]
    n_punct, prev = 0, False
    for c in text:
        hit = c in PUNCT
        if hit and not prev:
            n_punct += 1
        prev = hit
    n_char = len(body)
    n_cjk = sum(1 for c in body if "一" <= c <= "鿿")
    density = (n_char / n_punct) if n_punct else None
    return n_char, n_punct, density, (n_cjk / n_char if n_char else 0.0)


def audit(srt: Path, threshold: float) -> dict:
    """稽核一份 SRT。parse_srt 只回字幕文字 —— 序號行、時間軸行(裡面的 `,`
    與 `:` 會嚴重灌水標點數)、`[講者]` 前綴都不會進來。"""
    if not srt.exists() or srt.stat().st_size == 0:
        return {"path": srt, "ok": False, "reason": FAIL_MISSING,
                "chars": 0, "puncts": 0, "density": None, "cjk": 0.0}
    text = "".join(c["text"] for c in parse_srt(srt))
    n_char, n_punct, density, cjk = measure(text)
    if not n_char:
        ok, reason = False, FAIL_MISSING
    elif n_punct == 0:
        ok, reason = False, FAIL_ZERO
    elif density > threshold:
        ok, reason = False, FAIL_SPARSE
    else:
        ok, reason = True, ""
    return {"path": srt, "ok": ok, "reason": reason, "chars": n_char,
            "puncts": n_punct, "density": density, "cjk": cjk}


def collect(targets: list[Path], name: str) -> list[Path]:
    """路徑 → 待稽核的 SRT 清單。檔案直接收;目錄找 <子目錄>/<name>
    (batch_llmnode.sh 的輸出結構),自己就是 session 目錄時也收。"""
    out = []
    for t in targets:
        if t.is_file():
            out.append(t)
        elif t.is_dir():
            if (t / name).exists():
                out.append(t / name)
            out += sorted(p / name for p in t.iterdir()
                          if p.is_dir() and (p / name).exists())
        else:
            print(f"[punct] 找不到:{t}", file=sys.stderr)
    # 去重且保序
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def label(r: dict) -> str:
    if r["reason"] == FAIL_MISSING:
        return "缺檔/空檔"
    if r["density"] is None:
        return "0 標點"
    return f"每 {r['density']:.1f} 字"


def main():
    ap = argparse.ArgumentParser(description="逐字稿標點密度稽核")
    ap.add_argument("targets", nargs="+", type=Path,
                    help="SRT 檔,或含 <子目錄>/transcript.srt 的資料夾")
    ap.add_argument("--threshold", type=float, default=60.0,
                    help="每 N 字一個標點,超過就不合格(預設 60;"
                         "正常中文每 10–40 字一個)")
    ap.add_argument("--name", default="transcript.srt",
                    help="目錄底下要稽核的檔名(預設 transcript.srt)")
    ap.add_argument("--list", action="store_true",
                    help="只印不合格的目錄名(一行一個),給批次重轉用")
    ap.add_argument("--check", action="store_true",
                    help="有任何一份不合格就 exit 1(當 gate 用)")
    args = ap.parse_args()

    srts = collect(args.targets, args.name)
    if not srts:
        print("[punct] 沒有可稽核的逐字稿", file=sys.stderr)
        sys.exit(2)

    results = [audit(p, args.threshold) for p in srts]
    bad = [r for r in results if not r["ok"]]

    if args.list:
        for r in bad:
            print(r["path"].parent.name)
    else:
        print(f"[punct] {len(results)} 份逐字稿,門檻 每 {args.threshold:g} 字一個標點")
        # 壞的排前面(密度越差越前),好的按密度排
        order = sorted(results, key=lambda r: (r["ok"], -(r["density"] or 1e9)))
        for r in order:
            mark = "☑️" if r["ok"] else "❌"
            warn = " ⚠ 非中文為主" if r["ok"] and r["chars"] and r["cjk"] < 0.5 else ""
            print(f"  {mark} {r['path'].parent.name[:40]:<40} "
                  f"{r['chars']:>6} 字 / {r['puncts']:>5} 標點  → {label(r)}{warn}")
        print(f"[punct] 通過 {len(results) - len(bad)} / 不合格 {len(bad)}")
        if bad:
            print(f"[punct] 重轉清單:加 --list 印出目錄名"
                  f"(刪掉它們的 {args.name} 後重跑 batch,resume 才會補轉)")

    sys.exit(1 if (args.check and bad) else 0)


if __name__ == "__main__":
    main()
