#!/usr/bin/env python3
"""把 session 的轉檔產物倒進常設語料倉（doc-corpus）。

MM 2026-09-08 拍板：以後 doc-vlm-to-md 的產出全部往一個地方倒，
路徑寫在環境變數 `DOC_CORPUS_DIR`（mars-cc/.env），指向 doc-corpus/corpus。

**圖片整包一起複製**：它們被 doc-corpus 的 .gitignore 擋著、不會 push，
但留在本機很重要——直排雙區塊那類錯誤只能開圖才驗得出來，
沒有圖就沒辦法回頭核對版面。

用法：
    python3 sync_corpus.py <session 目錄> [...]      # 指定幾份
    python3 sync_corpus.py --all <sessions 根目錄>   # 整批
    python3 sync_corpus.py ... --dry-run             # 只看要做什麼
"""
import argparse, os, pathlib, shutil, sys

# 進版控：可 diff、校正時要用
# 2026-09-08 補 GLOB：kb_prep.py 產出的檔名是 <書名>_完整知識庫.md，
# 不叫 cleaned.md——原本只認固定檔名，害試跑的 agent 得先改名才能同步。
TRACKED = ["cleaned.md", "extracted.md", "figures.json",
           "metadata.json", "context.txt", "corrections.txt", "corrections.json"]
TRACKED_GLOB = ["*_完整知識庫.md", "*_with_figs.md", "cleaned_with_figs.md"]
# 不進版控但要留本機：核對版面用
UNTRACKED_DIRS = ["images"]


def corpus_root(explicit=None):
    if explicit:
        return pathlib.Path(explicit).expanduser()
    env = os.environ.get("DOC_CORPUS_DIR")
    if env:
        return pathlib.Path(env).expanduser()
    # 沒設環境變數就去讀 mars-cc/.env，省得每次要 source
    for c in (pathlib.Path.home() / "GithubRepo_mm-xyz/mars-cc/.env",):
        if c.exists():
            for line in c.read_text(encoding="utf-8").splitlines():
                if line.startswith("DOC_CORPUS_DIR="):
                    return pathlib.Path(line.split("=", 1)[1].strip()).expanduser()
    return None


def sync_one(src: pathlib.Path, root: pathlib.Path, dry=False):
    dst = root / src.name
    files = imgs = 0
    picked = [src / f for f in TRACKED if (src / f).exists()]
    for pat in TRACKED_GLOB:
        picked += [q for q in sorted(src.glob(pat)) if q.is_file() and q not in picked]
    for s in picked:
        if not dry:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, dst / s.name)
        files += 1
    for d in UNTRACKED_DIRS:
        s = src / d
        if not s.is_dir():
            continue
        n = sum(1 for _ in s.iterdir())
        if not dry:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copytree(s, dst / d, dirs_exist_ok=True)
        imgs += n
    return files, imgs


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="session 目錄；配 --all 則給 sessions 根目錄")
    ap.add_argument("--all", action="store_true", help="把根目錄下每個子目錄都當 session")
    ap.add_argument("--corpus", help="覆寫語料倉路徑（預設讀 DOC_CORPUS_DIR）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    root = corpus_root(a.corpus)
    if root is None:
        # 沒設就是沒有第二個落點，不是錯誤——這一步本來就是可選的。
        # 回 0，不要讓整條管線因為「沒設定」而失敗。
        print("（未設 DOC_CORPUS_DIR，沒有其他輸出路徑；產物留在 session 目錄）")
        return 0

    sessions = []
    for p in a.paths:
        p = pathlib.Path(p).expanduser()
        if a.all:
            sessions += [q for q in sorted(p.iterdir()) if q.is_dir()]
        else:
            sessions.append(p)
    # 沒有語料檔的目錄不是 session，跳過但要說出來
    real = [s for s in sessions
            if any((s / f).exists() for f in TRACKED)
            or any(s.glob(pat) for pat in TRACKED_GLOB)]
    skipped = [s for s in sessions if s not in real]

    print(f"語料倉：{root}{'　（--dry-run，不會真的寫）' if a.dry_run else ''}\n")
    tf = ti = 0
    for s in real:
        f, i = sync_one(s, root, a.dry_run)
        tf += f; ti += i
        print(f"  {s.name:<52} {f} 檔" + (f" ＋ {i} 張圖" if i else ""))
    print(f"\n合計 {len(real)} 份 session、{tf} 個語料檔、{ti} 張圖")
    if skipped:
        print(f"⚠️  {len(skipped)} 個目錄沒有語料檔、已跳過："
              f"{'、'.join(s.name for s in skipped[:4])}{' …' if len(skipped) > 4 else ''}")
    if not a.dry_run:
        print("\n下一步：cd 到 doc-corpus，git add -A && commit && push"
              "（images/ 會被 .gitignore 擋掉，不會進版控）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
