#!/usr/bin/env python3
"""
scripts/audio/cut.py — 剪一集 podcast 的一行指令(零 LLM,零 token)

    python3 scripts/audio/cut.py --session sessions/<slug> [--out X.mp3] [--yes]

做四件事:
    1. 對照 Drive 副本與 session 的 cutplan.md,**內容不同時列出語意差異**
       (哪些 block 的勾選翻了、刪除線增減、✂/⚙ 改了什麼)並讓人選用哪一份
    2. 用選定的那份跑 --dry-run,把剪輯摘要印出來給人看一眼
    3. render 出片(檔名自動遞增 vN,不覆蓋既有成品)
    4. 把 cutplan 與成品同步回 Drive,兩邊保持一致

為什麼要對照:MM 人審 cutplan 的實際介面是 Drive 副本,但 render 的真相源是
session 那份(ADR 0001)。2026-07-29 EP16 踩過——編輯全在 Drive、render 吃到
session 的舊版,出了一個沒剪的成品。
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_cut import parse_program  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DRIVE_ROOT = (Path.home() / "Library/CloudStorage"
              / "GoogleDrive-atommars.l@gmail.com" / "My Drive"
              / "水星貓的生活實驗室" / "1_Podcast 音檔")
EP_RE = re.compile(r"(EP\d+)", re.I)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mtime(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")


def find_drive_dir(sdir: Path, override: Path | None) -> Path | None:
    """session slug 與 Drive 資料夾名對不起來(EP15 一邊叫「前任」一邊叫
    「情緒管理」),只能靠 EP 編號配對;配對結果記進 .drive_dir 免得每次猜。"""
    memo = sdir / ".drive_dir"
    if override:
        memo.write_text(str(override), encoding="utf-8")
        return override
    if memo.exists():
        p = Path(memo.read_text(encoding="utf-8").strip())
        if p.is_dir():
            return p
    m = EP_RE.search(sdir.name)
    if not m or not DRIVE_ROOT.is_dir():
        return None
    hits = sorted(p for p in DRIVE_ROOT.iterdir()
                  if p.is_dir() and p.name.upper().startswith(m.group(1).upper() + "_"))
    if len(hits) != 1:
        print(f"[cut] ⚠ Drive 找到 {len(hits)} 個 {m.group(1)}_ 開頭的資料夾"
              + (f":{'、'.join(p.name for p in hits)}" if hits else "")
              + " — 用 --drive 指定")
        return None
    memo.write_text(str(hits[0]), encoding="utf-8")
    return hits[0]


def semantic_diff(a: Path, b: Path) -> list[str]:
    """兩份 cutplan 的**語意**差異(不是逐行 diff):勾選翻轉、刪除線增減、
    ✂ 手動剪除、⚙ 參數。逐行 diff 對這種一行幾百字的檔案沒有可讀性。"""
    def load(p: Path) -> tuple[dict, dict, set, dict]:
        keep, strikes, cuts, cfg = {}, {}, set(), {}
        for it in parse_program(p):
            if it["kind"] == "block":
                keep[it["id"]] = it["keep"]
                strikes[it["id"]] = it["raw"].count("~~") // 2
            elif it["kind"] == "cut":
                cuts.add((round(it["a"], 2), round(it["b"], 2)))
            elif it["kind"] == "config":
                cfg.update(it["params"])
        return keep, strikes, cuts, cfg

    ka, sa, ca, ga = load(a)
    kb, sb, cb, gb = load(b)
    out = []
    flipped = [i for i in ka if i in kb and ka[i] != kb[i]]
    if flipped:
        out.append(f"  勾選翻轉 {len(flipped)} 個:"
                   + "、".join(f"{i}({'留→剪' if ka[i] else '剪→留'})"
                               for i in flipped[:8])
                   + (" …" if len(flipped) > 8 else ""))
    ds = [(i, sa.get(i, 0), sb.get(i, 0)) for i in set(sa) | set(sb)
          if sa.get(i, 0) != sb.get(i, 0)]
    if ds:
        out.append(f"  刪除線變動 {len(ds)} 個 block:"
                   + "、".join(f"{i} {x}→{y}" for i, x, y in ds[:8])
                   + (" …" if len(ds) > 8 else ""))
    for tag, s in (("只在 A", ca - cb), ("只在 B", cb - ca)):
        if s:
            out.append(f"  ✂ {tag}:" + "、".join(f"{x}-{y}" for x, y in sorted(s)))
    dg = [k for k in set(ga) | set(gb) if ga.get(k) != gb.get(k)]
    if dg:
        out.append("  ⚙ " + "、".join(f"{k}: {ga.get(k, '無')}→{gb.get(k, '無')}"
                                      for k in sorted(dg)))
    only = set(ka) ^ set(kb)
    if only:
        out.append(f"  ⚠ block 集合不一致({len(only)} 個只在一邊)"
                   " — 有一邊是重新產生過的 cutplan,別直接覆蓋")
    return out or ["  (內容有差異但不影響剪輯:註解、理由文字之類)"]


def ask(prompt: str, options: str, default: str) -> str:
    if not sys.stdin.isatty():
        print(f"[cut] 非互動環境,採用預設 {default}")
        return default
    while True:
        a = input(f"{prompt} [{options}] (預設 {default}): ").strip().upper()
        if not a:
            return default
        if a in options.upper().split("/"):
            return a


def next_out_name(sdir: Path, given: str | None) -> str:
    if given:
        return given
    n = 2
    while (sdir / f"final_cut_v{n}.mp3").exists():
        n += 1
    return f"final_cut_v{n}.mp3"


def main() -> None:
    ap = argparse.ArgumentParser(description="改完 cutplan → 出片 → 同步 Drive")
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", help="輸出檔名(預設自動遞增 final_cut_vN.mp3)")
    ap.add_argument("--drive", type=Path, help="Drive 集數資料夾(第一次配對後會記住)")
    ap.add_argument("--yes", action="store_true", help="全部用預設,不互動")
    ap.add_argument("--no-push", action="store_true", help="出片後不推回 Drive")
    ap.add_argument("--check", action="store_true",
                    help="只做 cutplan 對照與 dry-run,不出片(改完先驗證用)")
    args, passthru = ap.parse_known_args()

    sdir = Path(args.session).resolve()
    local = sdir / "cutplan.md"
    if not local.exists():
        sys.exit(f"[cut] FAIL: 找不到 {local}")
    ddir = find_drive_dir(sdir, args.drive)
    drive = ddir / "cutplan.md" if ddir else None

    # ── 1. 對照 Drive vs local ──
    if drive and drive.exists():
        if sha(drive) == sha(local):
            print(f"[cut] ✓ Drive 與 session 的 cutplan 一致({mtime(local)})")
        else:
            newer = "D" if drive.stat().st_mtime > local.stat().st_mtime else "L"
            print("[cut] ⚠ 兩份 cutplan 內容不同:")
            print(f"    Drive   {mtime(drive):>12}  "
                  + ("← 較新" if newer == "D" else ""))
            print(f"    session {mtime(local):>12}  "
                  + ("← 較新" if newer == "L" else ""))
            print("  語意差異(Drive → session):")
            for line in semantic_diff(drive, local):
                print(line)
            pick = newer if args.yes else ask(
                "  用哪一份出片?", "D/L/Q", newer)
            if pick == "Q":
                sys.exit("[cut] 已中止,兩邊都沒動")
            if pick == "D":
                shutil.copy2(drive, local)
                print("  → 採用 Drive 版,已覆蓋 session")
            else:
                print("  → 採用 session 版(出片後才會推回 Drive)")
    elif drive:
        print(f"[cut] Drive 還沒有 cutplan.md,出片後會推上去")
    else:
        print("[cut] 沒有配對到 Drive 資料夾,只在本地出片")

    # ── 2. dry-run ──
    render = [sys.executable, str(Path(__file__).parent / "render_cut.py"),
              "--session", str(sdir), *passthru]
    print("\n[cut] ── dry-run ──")
    r = subprocess.run(render + ["--dry-run"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("[render]"):
            print("  " + line)
    if r.returncode != 0:
        print(r.stdout[-2000:] or r.stderr[-2000:])
        sys.exit("[cut] FAIL: dry-run 沒過 — cutplan 有問題,先修再跑")
    if args.check:
        print("\n[cut] ✓ --check:cutplan 沒問題,要出片把 --check 拿掉再跑一次")
        return
    if not args.yes and ask("\n  照這樣出片?", "Y/N", "Y") == "N":
        sys.exit("[cut] 已中止")

    # ── 3. render ──
    out = next_out_name(sdir, args.out)
    print(f"\n[cut] ── render → {out} ──")
    if subprocess.run(render + ["--out", out]).returncode != 0:
        sys.exit("[cut] FAIL: render 失敗")

    # ── 4. 推回 Drive ──
    if ddir and not args.no_push:
        shutil.copy2(local, ddir / "cutplan.md")
        ep = EP_RE.search(sdir.name)
        stem = (ep.group(1) if ep else "cut") + "_" + Path(out).stem
        shutil.copy2(sdir / out, ddir / f"{stem}.mp3")
        print(f"[cut] ☑️ 已推回 Drive:cutplan.md + {stem}.mp3")
    print(f"[cut] ✅ 完成 → {sdir / out}")


if __name__ == "__main__":
    main()
