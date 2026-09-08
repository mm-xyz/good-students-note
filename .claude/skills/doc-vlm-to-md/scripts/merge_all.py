#!/usr/bin/env python3
"""把所有批次的轉錄結果，依 session 分流塞回各自的 cleaned.md。
用 doc-vlm-to-md 的 merge_figures 邏輯；冪等（重跑先清舊插入塊）。"""
import json, pathlib, re, subprocess, sys, collections
T1 = pathlib.Path(__file__).resolve().parent
S = pathlib.Path("/Users/marslo/GithubRepo_mm-xyz/good-students-note/sessions")
MERGE = pathlib.Path.home()/".claude/skills/doc-vlm-to-md/scripts/merge_figures.py"

rows = json.loads((T1/"t1_list.json").read_text(encoding="utf-8"))
# ⚠️ 圖檔名跨書會重複（pic008.jpg 在五本書都有），所以標頭一律用 sess__file
blocks = collections.defaultdict(dict)                 # session → {圖檔: 轉錄}
missing = []
for ob in sorted(T1.glob("out_*.md")):
    tx = ob.read_text(encoding="utf-8")
    found = re.findall(r"^## (\S+?)__(\S+\.(?:jpg|jpeg|png))\s*$\n(.*?)(?=^## \S+?__\S+\.(?:jpg|jpeg|png)\s*$|\Z)",
                       tx, re.S | re.M)
    for sess, name, body in found:
        if not (S/sess).exists(): missing.append((ob.name, f"{sess}__{name}")); continue
        blocks[sess][name] = body.strip()
    print(f"{ob.name}: 解析出 {len(found)} 張")

total = 0
for sess, mp in blocks.items():
    tmp = T1/f"_merged_{sess}.md"
    tmp.write_text("\n".join(f"## {n}\n\n{b}\n" for n, b in mp.items()), encoding="utf-8")
    cleaned = S/sess/"cleaned.md"
    if not cleaned.exists(): cleaned = S/sess/"extracted.md"   # 2026-08-08 那批用 extracted.md
    fig = S/sess/"figures.json"
    if not (cleaned.exists() and fig.exists()):
        print(f"  ⚠️ {sess}: 缺 cleaned.md 或 figures.json，跳過"); continue
    r = subprocess.run([sys.executable, str(MERGE), str(cleaned), str(fig), str(tmp)],
                       capture_output=True, text=True)
    print(f"  {sess[:34]:34} {r.stdout.strip().splitlines()[0] if r.stdout.strip() else r.stderr[:80]}")
    total += len(mp)
print(f"\n合計處理 {total} 張")
for ob, n in missing: print(f"  ⚠️ {ob} 的 {n} 不在 T1 清單")
