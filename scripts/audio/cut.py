#!/usr/bin/env python3
"""
scripts/audio/cut.py — 剪一集 podcast 的一行指令(零 LLM,零 token)

    python3 scripts/audio/cut.py --session sessions/<slug> [--out X.mp3] [--yes]

做五件事(2026-08-11 MM:「這種都應該 script 化」——mkdir、複製到 Drive、
sync cutplan、備份當下的 cutplan,全部由這支做,不要靠人記得):
    1. 對照 Drive 副本與 session 的 cutplan,**內容不同時列出語意差異**
       (哪些 block 的勾選翻了、刪除線增減、✂/⚙ 改了什麼),詢問並預設擇新
    2. 用選定的那份跑 --dry-run,把剪輯摘要印出來給人看一眼
    3. render 出片(檔名自動遞增 vN,不覆蓋既有成品)
    4. **local 與 Drive 各建一個版本目錄**`vN_<時戳>/`,裡面放
       mp3 ＋ **當次 cutplan 快照** ＋ render.txt(含走哪條剪輯路線)
    5. 把工作版 cutplan 同步回 Drive,兩邊保持一致

分軌線用 `--plan cutplan.pertrack.md`。`--no-push` 只做 local 不碰 Drive。

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
    def load(p: Path) -> tuple[dict, dict, set, dict, dict]:
        keep, strikes, cuts, cfg, mus = {}, {}, set(), {}, {}
        for it in parse_program(p):
            if it["kind"] == "block":
                keep[it["id"]] = it["keep"]
                strikes[it["id"]] = it["raw"].count("~~") // 2
            elif it["kind"] == "cut":
                cuts.add((round(it["a"], 2), round(it["b"], 2)))
            elif it["kind"] == "config":
                cfg.update(it["params"])
            elif it["kind"] == "music":
                mus[it["file"]] = {k: it[k] for k in
                                   ("start", "end", "fadein", "fadeout",
                                    "lead", "tail")}
        return keep, strikes, cuts, cfg, mus

    ka, sa, ca, ga, ma = load(a)
    kb, sb, cb, gb, mb = load(b)
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
    for f in sorted(set(ma) | set(mb)):
        if ma.get(f) == mb.get(f):
            continue
        if f not in ma or f not in mb:
            out.append(f"  🎵 {f}:{'只在 B' if f in mb else '只在 A'}")
            continue
        d = [f"{k} {ma[f][k]}→{mb[f][k]}" for k in ma[f] if ma[f][k] != mb[f][k]]
        out.append(f"  🎵 {f}:" + "、".join(d))
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


OUT_RE = re.compile(r"^final_cut_v(\d+)\.")


def next_out_name(sdir: Path, given: str | None) -> str:
    """接**最大**號往上,不是找第一個沒被佔用的空號。

    後者在「v3 在、v2 被刪或改名」時會產生 v2,版本號往回跳 — 檔名的先後
    順序就不再等於出片的先後順序,對照哪一版是哪一版會錯亂。
    """
    if given:
        return given
    n = max((int(m.group(1)) for p in sdir.iterdir()
             for m in [OUT_RE.match(p.name)] if m), default=1) + 1
    return f"final_cut_v{n}.mp3"


# 版本目錄:`v3_20260810-1830`,但**人手取的名字常常沒有 -HHMM**
# (EP16 的 `v09_20260811-對照組`、`v10_20260811-你的定稿`)。時間碼設成選用,
# 否則那些目錄不被計數,下一版的號碼會往回跳到它們前面(2026-08-12 實踩)。
VER_RE = re.compile(r"^v(\d+)_\d{8}")


def drive_cutplan(ddir: Path) -> Path:
    """Drive 端 MM 編輯的那份住**集數資料夾根**的 `cutplan.md`。

    2026-08-11 MM 拍板:「一開始可以直接放在 /{EP Folder}/cutplan.md 不用進
    Meta(不論在 session 或 GDrive 上)」——兩邊同一個位置,不用記哪邊多一層。

    原本搬進 `_meta/` 的理由是「正在編輯的 cutplan」要跟「某一版出片當下的
    快照」分開,否則人會對著版本資料夾裡的快照改。這個顧慮**現在的結構仍然
    滿足**:活的那份在集數根、快照在 `vNN_<時戳>/` 裡面,`_meta/` 只是多一層
    而已。既有的 `_meta/cutplan.md` 自動搬回根(同資料夾內搬,要退回很容易)。
    """
    new, old = ddir / "cutplan.md", ddir / "_meta" / "cutplan.md"
    if not new.exists() and old.exists():
        shutil.move(str(old), str(new))
        print("[cut] 結構調整:_meta/cutplan.md → cutplan.md(以後編輯這份)")
    return new


def _max_ver(d: Path | None) -> int:
    if not d or not d.is_dir():
        return 0
    return max((int(m.group(1)) for p in d.iterdir() if p.is_dir()
                for m in [VER_RE.match(p.name)] if m), default=0)


def version_name(sdir: Path, ddir: Path | None, ai: bool, now: dt.datetime) -> str:
    """`v3_20260810-1830-AI`;版本號取 **local 與 Drive 兩邊的最大號** +1。

    只看單邊會讓兩邊版本號分叉(local 有 v9、Drive 只有 v1 → 同一次出片在兩邊
    叫不同名字,之後對不起來)。-AI 只在有 AI 介入時掛。
    """
    n = max(_max_ver(sdir), _max_ver(ddir)) + 1
    return f"v{n}_{now:%Y%m%d-%H%M}" + ("-AI" if ai else "")


def next_version_dir(ddir: Path, ai: bool, now: dt.datetime) -> Path:
    """(保留給既有呼叫端/測試)Drive 端版本目錄。"""
    return ddir / version_name(ddir, ddir, ai, now)


def main() -> None:
    ap = argparse.ArgumentParser(description="改完 cutplan → 出片 → 同步 Drive")
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", help="輸出檔名(預設自動遞增 final_cut_vN.mp3)")
    ap.add_argument("--plan", default="cutplan.md",
                    help="人審節目單(分軌線用 cutplan.pertrack.md)")
    ap.add_argument("--drive", type=Path, help="Drive 集數資料夾(第一次配對後會記住)")
    ap.add_argument("--yes", action="store_true", help="全部用預設,不互動")
    ap.add_argument("--no-push", action="store_true", help="出片後不推回 Drive")
    ap.add_argument("--check", action="store_true",
                    help="只做 cutplan 對照與 dry-run,不出片(改完先驗證用)")
    ap.add_argument("--ai", action="store_true",
                    help="這一版有 AI 介入剪輯決策(版本資料夾掛 -AI 後綴)")
    args, passthru = ap.parse_known_args()

    sdir = Path(args.session).resolve()
    local = sdir / args.plan
    if not local.exists():
        sys.exit(f"[cut] FAIL: 找不到 {local}")
    ddir = find_drive_dir(sdir, args.drive)
    drive = drive_cutplan(ddir) if ddir else None

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
              "--plan", args.plan,
              "--session", str(sdir), *passthru]
    print("\n[cut] ── dry-run ──")
    r = subprocess.run(render + ["--dry-run"], capture_output=True, text=True)
    summary = [l for l in r.stdout.splitlines() if l.startswith("[render]")]
    for line in summary:
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

    # ── 4. 版本目錄:**local 與 Drive 都要**,一版一個資料夾 ──
    #
    # 2026-08-11 MM:「mkdir 這應該直接變成 script 去做,複製到 google drive
    # 也應該要變成 script,sync cutplan.md 也是,而且當下的 cutplan 也應該備份」。
    # 原本只有 Drive 端建版本目錄,local 端得人工 mkdir——實測整整一輪都在手動
    # 做這幾步,還漏過一次(--out 指向不存在的目錄,整趟 render 白跑)。
    now = dt.datetime.now()
    ep = EP_RE.search(sdir.name)
    vname = version_name(sdir, ddir, args.ai, now)
    stem = (ep.group(1) if ep else "cut") + "_" + vname.split("_")[0]
    line = "分軌" if args.plan != "cutplan.md" else "混音"
    note = ("\n".join(summary)
            + f"\n\n剪輯路線:{line}線(--plan {args.plan})"
            + f"\n出片檔:{stem}.mp3\nsession:{sdir}\n")

    lvdir = sdir / vname
    lvdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sdir / out, lvdir / f"{stem}.mp3")
    shutil.copy2(local, lvdir / Path(args.plan).name)   # 這一版照哪份剪的
    (lvdir / "render.txt").write_text(note, encoding="utf-8")
    print(f"[cut] ☑️ local:{vname}/(mp3 + cutplan 快照 + render.txt)")

    if ddir and not args.no_push:
        vdir = ddir / vname
        vdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sdir / out, vdir / f"{stem}.mp3")
        shutil.copy2(local, vdir / Path(args.plan).name)
        (vdir / "render.txt").write_text(note, encoding="utf-8")
        shutil.copy2(local, drive)                     # Drive 工作版保持最新
        print(f"[cut] ☑️ Drive:{vdir.name}/(同上)")
    print(f"[cut] ✅ 完成 → {sdir / out}")


if __name__ == "__main__":
    main()
