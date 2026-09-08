#!/usr/bin/env python3
"""
package_kb.py — 把「最終知識庫 md + assets/」打包成單一 zip，內含一個資料夾。
解壓後打開資料夾裡的 md（如 Typora），縮圖會用標準相對連結自動顯示。

解決「只交付一個 md → 縮圖全斷、學員以為壞掉」的問題：交付這個 zip 即可。

用法：
    python3 package_kb.py <最終.md> [--assets <assets目錄>] [-o <輸出.zip>]

行為：
    - 只打包 md 實際引用到的圖（![](...) 相對連結），順便當「斷鏈檢查」。
    - 任一連結找不到圖檔 → 非 0 退出並列出，提醒你「補圖」或「移除該圖片行」。
"""
import argparse
import os
import re
import sys
import zipfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SUFFIX = re.compile(r"(_完整知識庫|_視覺知識庫|_知識庫)?\.md$")


def main():
    ap = argparse.ArgumentParser(description="把知識庫 md + assets 打包成可攜 zip（縮圖自動連結）")
    ap.add_argument("md", help="最終知識庫 md 路徑")
    ap.add_argument("--assets", default=None, help="assets 目錄（預設：md 同層 assets/）")
    ap.add_argument("-o", "--output", default=None, help="輸出 zip（預設：md 同層 {stem}_知識庫.zip）")
    args = ap.parse_args()

    md = os.path.abspath(args.md)
    if not os.path.isfile(md):
        print(f"❌ 找不到 md：{md}", file=sys.stderr)
        sys.exit(1)
    base = os.path.dirname(md)
    md_name = os.path.basename(md)
    stem = SUFFIX.sub("", md_name)
    folder = f"{stem}_知識庫"
    out_zip = args.output or os.path.join(base, f"{folder}.zip")

    text = open(md, encoding="utf-8").read()
    links = re.findall(r"!\[[^]]*\]\(([^)]+)\)", text)
    rels = [l for l in links if not l.startswith(("http://", "https://", "data:"))]

    missing = []
    packed = 0
    if os.path.exists(out_zip):
        os.remove(out_zip)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(md, f"{folder}/{md_name}")
        for rel in sorted(set(rels)):
            src = os.path.normpath(os.path.join(base, rel))
            if os.path.isfile(src):
                zf.write(src, f"{folder}/{rel}")
                packed += 1
            else:
                missing.append(rel)

    print(f"✅ 已打包：{out_zip}")
    print(f"   結構：{folder}/{md_name} ＋ {packed} 張圖（assets 隨行）")
    if missing:
        print(f"   ❌ 有 {len(missing)} 個圖片連結找不到實體檔（未打包，會斷鏈）：")
        for m in missing[:8]:
            print(f"        {m}")
        print("   → 請確認 assets 在 md 同層，或把這些斷鏈的圖片行移除（只留 VLM 文字）後重打包。")
        sys.exit(2)
    print(f"   📂 解壓後打開 {folder}/{md_name}（Typora 等）縮圖會自動顯示，0 斷鏈。")


if __name__ == "__main__":
    main()
