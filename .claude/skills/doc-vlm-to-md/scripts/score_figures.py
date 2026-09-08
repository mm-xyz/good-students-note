#!/usr/bin/env python3
"""給圖評「知識含量」分數並直接輸出待處理清單。0 LLM、純規則。

用法：
    python3 score_figures.py <session 目錄> [...]            # 只看分布
    python3 score_figures.py <session 目錄> --top 80 --out t1.json
    python3 score_figures.py <sessions 根目錄> --all --top 80 --out t1.json

**計分與選取寫在同一支程式裡，直接吐清單。**
2026-09-07 事故：舊版只印報告、清單由人從報告裡挑，結果挑成「按書配額」——
《格局篇》（86 張）與《攻略3下》（109 張）**整本零入選**，
而它們最強的圖全域排名是 #9 與 #31。事後重算才發現清單與公平排序只有 43/86 重疊。
所以 --top 一律**全域排序**取前 N，不按來源配額。

分數 ＝ 錨點前後文命中的關鍵字數 ＋ 大圖加 1 ＋ 多錨點加 1。
這只是弱代理：它靠圖**周邊的文字**猜，圖本身沒被讀過。
表格若周邊敘述簡短就會低估——所以低分區抽樣驗過再放棄，不要直接砍。
"""
import argparse, json, pathlib, sys
from collections import Counter

KEYWORDS = ["表", "對照", "廟", "旺", "落陷", "四化", "天干", "宮位",
            "星曜", "組合", "格局", "命盤", "圖"]
SIZE_THRESHOLD = 150 * 1024


def load(session: pathlib.Path):
    """figures.json 可能在 session 根目錄，也可能在 assets/ 底下。"""
    for cand in (session / "figures.json", session / "assets" / "figures.json"):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8")), cand
            except (OSError, json.JSONDecodeError) as e:
                print(f"❗ {cand} 讀取失敗：{e}", file=sys.stderr)
                return [], cand
    return None, None


def score(item):
    anchors = item.get("anchors", [])
    combined = " ".join(a.get("before", "") + " " + a.get("after", "") for a in anchors)
    kw = sum(1 for k in KEYWORDS if k in combined)
    return kw + (1 if item.get("bytes", 0) > SIZE_THRESHOLD else 0) + (1 if len(anchors) > 1 else 0)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="session 目錄；配 --all 則給 sessions 根目錄")
    ap.add_argument("--all", action="store_true", help="把根目錄下每個子目錄都當 session")
    ap.add_argument("--top", type=int, help="全域排序取前 N，輸出清單")
    ap.add_argument("--min-score", type=int, help="只留分數 >= 此值（可與 --top 併用）")
    ap.add_argument("--out", help="清單輸出 JSON；不給就印到畫面")
    a = ap.parse_args(argv)

    sessions = []
    for p in a.paths:
        p = pathlib.Path(p).expanduser()
        sessions += [q for q in sorted(p.iterdir()) if q.is_dir()] if a.all else [p]

    rows, stats = [], []
    for s in sessions:
        data, src = load(s)
        if data is None:
            continue
        for it in data:
            rows.append({"score": score(it), "bytes": it.get("bytes", 0),
                         "sess": s.name, "file": it["file"],
                         "n_anchors": len(it.get("anchors", []))})
        stats.append((s.name, len(data)))

    if not rows:
        print("找不到 figures.json（找過 <session>/ 與 <session>/assets/）", file=sys.stderr)
        return 1

    for name, n in stats:
        print(f"  {name[:44]:<46}{n:>5} 張")
    dist = Counter(r["score"] for r in rows)
    print(f"\n共 {len(rows)} 張，分數分布："
          + "、".join(f"{s} 分×{dist[s]}" for s in sorted(dist, reverse=True)))

    sel = sorted(rows, key=lambda r: (-r["score"], -r["bytes"]))
    if a.min_score is not None:
        sel = [r for r in sel if r["score"] >= a.min_score]
    if a.top:
        sel = sel[:a.top]
    if not (a.top or a.min_score is not None):
        return 0

    print(f"\n選出 {len(sel)} 張（**全域排序**，不按來源配額），分數 "
          f"{sel[0]['score']} → {sel[-1]['score']}")
    for name, n in Counter(r["sess"] for r in sel).most_common():
        print(f"  {name[:44]:<46}{n:>5} 張")
    zero = [nm for nm, _ in stats if nm not in {r["sess"] for r in sel}]
    if zero:
        print(f"⚠️  這 {len(zero)} 個 session 一張都沒選上——"
              f"先確認不是配額問題再放棄：{'、'.join(z[:22] for z in zero[:3])}")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(sel, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        print(f"\n→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
