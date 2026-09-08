#!/usr/bin/env python3
"""版面層驗證：抓「圖的邊界劃錯」，不是抓「圖的內容讀錯」。

存在理由（2026-09-07 事故）：直排書的表格跨頁分裂成雙區塊——
右區塊是前一個主題的續尾、左區塊才是新主題的標題＋開頭。
把整張圖當一個主題轉錄，產出就是「本主題前幾項 ＋ 前一主題後幾項」，
**每一項都是真的，組合起來全錯**。十顆星有五顆中招。

當時用「表頭 OCR ＋ 內容關鍵詞頻」驗過並放行——因為兩者讀的是同一張圖，
而錯誤在邊界不在內容。**同源的兩種檢查不算交叉驗證。**
這支只做結構檢查，不看內容，所以能抓到那類錯誤。

用法：
    python3 check_layout.py <cleaned.md> --unit 宮 --expect 12
    python3 check_layout.py <cleaned.md> --unit 宮        # 不給 expect 就用眾數當基準
"""
import argparse, collections, pathlib, re, sys

FIG = re.compile(r"<!-- FIG:(\S+?) BEGIN -->(.*?)<!-- FIG:\1 END -->", re.S)


def rows_of(block, unit):
    """抓表格首欄的項目名（| 命宮 | …），回傳出現順序。"""
    return re.findall(rf"^\|\s*([^\s|]{{1,6}}{re.escape(unit)})\s*\|", block, re.M)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md")
    ap.add_argument("--unit", required=True, help="項目的共同字尾，例如「宮」「章」「星」")
    ap.add_argument("--expect", type=int, help="每個單位應有幾項；不給就取眾數")
    ap.add_argument("--min-rows", type=int, default=3, help="少於幾列就不當成表（預設 3）")
    a = ap.parse_args(argv)

    text = pathlib.Path(a.md).read_text(encoding="utf-8")
    figs = [(n, rows_of(b, a.unit)) for n, b in FIG.findall(text)]
    figs = [(n, r) for n, r in figs if len(r) >= a.min_rows]
    if not figs:
        print(f"沒有含「{a.unit}」表格的圖說區塊（門檻 {a.min_rows} 列）"); return 1

    expect = a.expect or collections.Counter(len(r) for _, r in figs).most_common(1)[0][0]
    print(f"{len(figs)} 個含表圖說，基準每個 {expect} 項"
          f"{'（--expect）' if a.expect else '（取眾數）'}\n")

    bad = 0
    for n, r in figs:
        flags = []
        if len(r) != expect:
            flags.append(f"項數 {len(r)}≠{expect}")
        dup = [k for k, c in collections.Counter(r).items() if c > 1]
        if dup:
            flags.append(f"自身重複 {'、'.join(dup)}")
        if flags:
            bad += 1
            print(f"  ❗ {n:<14}{'；'.join(flags)}")

    # 插入點吃字：FIG 區塊前只剩孤零零一個漢字 → merge 的 off-by-one 指紋。
    # 2026-09-08 第二次試跑抓到：853 張圖說有 407 處中招（「因為」被切成
    # 「因」＋圖說＋「為」）。字沒少、順序沒亂，所以落地率、圖連結、項數檢查
    # **全部放行**——這種錯只有專門找才看得到。
    orphan = re.findall(r"(?:^|\n)([一-鿿])\n\n<!-- FIG:(\S+?) BEGIN", text)
    if orphan:
        bad += len(orphan)
        print(f"  ❗ {len(orphan)} 處 FIG 區塊前有孤字（插入點 off-by-one，吃掉下一段第一個字）")
        for ch, fig in orphan[:3]:
            print(f"       {fig} 前面孤零零一個「{ch}」")

    # 相鄰圖重疊：同一份材料裡兩張圖的項目大量相同 → 邊界劃錯的指紋
    print()
    for (n1, r1), (n2, r2) in zip(figs, figs[1:]):
        ov = set(r1) & set(r2)
        if len(ov) >= max(3, min(len(r1), len(r2)) * 0.5):
            bad += 1
            print(f"  ❗ {n1} 與 {n2} 重疊 {len(ov)} 項 "
                  f"→ 很可能是同一個單位被切成兩張，或邊界劃錯")

    if bad:
        print(f"\n共 {bad} 處可疑。**這些是結構問題，不是內容問題**——"
              f"回去開圖看版面（是不是雙區塊、標題在不在中間），不要只重讀文字。")
        return 2
    print("✅ 項數齊、無自身重複、相鄰不重疊")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
