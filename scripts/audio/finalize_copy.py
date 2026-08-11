#!/usr/bin/env python3
"""
scripts/audio/finalize_copy.py — 定稿 → 重轉錄 → 出 prompt → 跑文案引擎(一行指令)

    python3 scripts/audio/finalize_copy.py --session sessions/<slug> --ep 16
    python3 scripts/audio/finalize_copy.py --session ... --ep 16 --engines agy,codex

2026-08-12 MM:「以後定稿的都是去重 render 重轉錄出逐字稿,把這個變成 script,
然後把 prompt 都出出來,跑一次 agy -p / codex -p」。

## 為什麼要重轉錄,而不是從 cutplan 回推時間

原本文案的逐字稿是「拿原始時間軸經 cut_map ＋ tempo 換算回推成品時間」——
那是一條會漂的推導鏈,而且**補錄根本不在那條時間軸上**(ADR 0011 的 `## ➕`
在自己的 0 起算時間軸)。EP16 實測:文案引擎把 Sarah 的補錄標在 21:10,
真實位置是 21:51,差 41 秒。

成品 mp3 才是聽眾真正會聽到的東西。對它重轉一次,時間戳直接就是對的,
補錄、變速、停頓收緊、集錦重播全都自動含在裡面,不需要任何換算。
cutplan 只保留它獨有的知識——**誰在講**(逐軌歸屬/diarize),靠文字對齊掛回去。

## 四步

    1. render   cutplan 比成品新就重出一版(已是最新就跳過,不製造重複版本目錄)
    2. 轉錄     本地 mlx-whisper 對**定稿 mp3** 重轉 → _meta/final/transcript.srt
    3. prompt   copy_prompt_build --final-srt → copy_prompt.md
    4. 引擎     agy -p / codex exec 各出一版 → _meta/copy_draft_<engine>.md

引擎走各自的 OAuth 登入額度(CLAUDE.md Auth 雙軌表),不打任何 API key。
任一引擎失敗不影響另一個,最後一起回報。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIO = Path(__file__).resolve().parent
VER_RE = re.compile(r"^v(\d+)_\d{8}")

# name: (指令, prompt 怎麼給)
#   stdin — 從標準輸入吃(codex exec 的 `-`)
#   arg   — 當成最後一個引數(agy --print 只吃引數;給 stdin 它會印 help 就跑掉)
# 兩者都走各自的 OAuth 登入額度(CLAUDE.md Auth 雙軌表),不打 API key。
ENGINES = {
    "agy": (["agy", "--print", "--print-timeout", "20m"], "arg"),
    "codex": (["codex", "exec", "--skip-git-repo-check", "-"], "stdin"),
}


def die(msg: str) -> None:
    print(f"[finalize] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def latest_final_mp3(sdir: Path) -> Path | None:
    """最新版本目錄裡的 mp3(版本號最大;同號取 mtime 較新)。"""
    best = None
    for d in sdir.iterdir():
        m = VER_RE.match(d.name) if d.is_dir() else None
        if not m:
            continue
        for f in d.glob("*.mp3"):
            key = (int(m.group(1)), f.stat().st_mtime)
            if best is None or key > best[0]:
                best = (key, f)
    return best[1] if best else None


def venv_python() -> str:
    p = PROJECT_ROOT / ".venv-audio" / "bin" / "python"
    return str(p) if p.exists() else sys.executable


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"[finalize] $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run([str(c) for c in cmd], cwd=PROJECT_ROOT, **kw)


def step_render(sdir: Path, plan: str, mp3: Path | None) -> Path:
    """cutplan 比成品新才重出;已經最新就跳過(不製造重複的版本目錄)。"""
    cutplan = sdir / plan
    if mp3 and mp3.stat().st_mtime >= cutplan.stat().st_mtime:
        print(f"[finalize] ☑️ 成品已是最新({mp3.parent.name}/{mp3.name}),跳過 render")
        return mp3
    print("[finalize] cutplan 比成品新 → 重出一版")
    r = run([venv_python(), AUDIO / "cut.py", "--session", sdir,
             "--plan", plan, "--yes"])
    if r.returncode != 0:
        die("render 失敗 — 先修 cutplan 再跑")
    got = latest_final_mp3(sdir)
    if not got:
        die("render 完卻找不到成品 mp3")
    return got


def step_transcribe(sdir: Path, mp3: Path, force: bool) -> Path:
    out = sdir / "_meta" / "final" / "transcript.srt"
    if out.exists() and not force and out.stat().st_mtime >= mp3.stat().st_mtime:
        print(f"[finalize] ☑️ 逐字稿已比成品新,跳過轉錄({out})")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    ctx = sdir / "context.txt"
    cmd = [venv_python(), AUDIO / "transcribe_local.py", mp3, "-o", out]
    if ctx.exists():
        cmd += ["--context", ctx]
    if run(cmd).returncode != 0:
        die("轉錄失敗")
    return out


def step_prompt(sdir: Path, ep: str, final_srt: Path, template: Path | None) -> Path:
    cmd = [sys.executable, AUDIO / "copy_prompt_build.py", "--session", sdir,
           "--ep", ep, "--final-srt", final_srt]
    if template:
        cmd += ["--template", template]
    if run(cmd).returncode != 0:
        die("prompt 組裝失敗")
    return sdir / "copy_prompt.md"


def step_engine(name: str, prompt: str, sdir: Path, timeout: int) -> tuple[bool, str]:
    cmd, how = ENGINES[name]
    if isinstance(how, str) and how == "arg":
        cmd, stdin = cmd + [prompt], None
    else:
        stdin = prompt
    dst = sdir / "_meta" / f"copy_draft_{name}.md"
    t0 = time.time()
    try:
        r = subprocess.run([str(c) for c in cmd], input=stdin, text=True,
                           capture_output=True, cwd=PROJECT_ROOT, timeout=timeout)
    except FileNotFoundError:
        return False, f"{name}:找不到指令 `{cmd[0]}`(沒裝或不在 PATH)"
    except subprocess.TimeoutExpired:
        return False, f"{name}:逾時 {timeout}s"
    if r.returncode != 0:
        return False, f"{name}:exit {r.returncode} — {(r.stderr or r.stdout)[-400:]}"
    body = (r.stdout or "").strip()
    if len(body) < 200:
        return False, f"{name}:輸出只有 {len(body)} 字,不像文案 — {body[:200]}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(f"> engine: {name} · {time.strftime('%Y-%m-%d %H:%M')}"
                   f" · {time.time() - t0:.0f}s\n\n{body}\n", encoding="utf-8")
    return True, f"{name}:✅ {dst}({dst.stat().st_size:,} bytes,{time.time() - t0:.0f}s)"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="定稿 → 重轉錄 → 出 prompt → 跑文案引擎")
    ap.add_argument("--session", required=True)
    ap.add_argument("--ep", required=True)
    ap.add_argument("--plan", default="cutplan.md",
                    help="人審節目單(分軌線用 cutplan.pertrack.md)")
    ap.add_argument("--mp3", type=Path, help="指定定稿 mp3(預設取版本號最大的)")
    ap.add_argument("--template", type=Path)
    ap.add_argument("--engines", default="agy,codex",
                    help=f"逗號分隔,可用:{','.join(ENGINES)};空字串=只出 prompt")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--retranscribe", action="store_true",
                    help="逐字稿已比成品新也強制重轉")
    args = ap.parse_args()

    sdir = Path(args.session).resolve()
    if not sdir.is_dir():
        die(f"找不到 session {sdir}")
    if not (sdir / "copy_material.md").exists():
        die("缺 copy_material.md(該集章節+內容重點+金句紅線,由對話 agent 撰寫)")

    mp3 = args.mp3 or latest_final_mp3(sdir)
    if not mp3:
        die("找不到任何版本目錄裡的成品 mp3 — 先出片")
    mp3 = step_render(sdir, args.plan, mp3)
    srt = step_transcribe(sdir, mp3, args.retranscribe)
    prompt_path = step_prompt(sdir, args.ep, srt, args.template)
    print(f"[finalize] ☑️ prompt {prompt_path}"
          f"({prompt_path.stat().st_size:,} bytes)")

    names = [e for e in args.engines.split(",") if e.strip()]
    for bad in set(names) - set(ENGINES):
        die(f"不認識的引擎 {bad}(可用:{','.join(ENGINES)})")
    if not names:
        print("[finalize] ✅ 只出 prompt,未跑引擎")
        return
    prompt = prompt_path.read_text(encoding="utf-8")
    results = [step_engine(n, prompt, sdir, args.timeout) for n in names]
    print()
    for ok, msg in results:
        print(f"[finalize] {'' if ok else '⚠ '}{msg}")
    print(f"[finalize] {'✅' if all(ok for ok, _ in results) else '⚠'} "
          f"{sum(ok for ok, _ in results)}/{len(results)} 個引擎完成")


if __name__ == "__main__":
    main()
