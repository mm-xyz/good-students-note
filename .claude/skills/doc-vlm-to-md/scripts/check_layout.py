#!/usr/bin/env python3
"""版面層驗證：抓「圖的邊界劃錯／插入位置歪掉」，不是抓「圖的內容讀錯」。

**不帶參數就能跑**，會自動判斷這份材料適用哪些檢查：

    python3 check_layout.py <cleaned.md>                    # 通用檢查（永遠適用）
    python3 check_layout.py <cleaned.md> --unit 宮          # 加表格系列檢查（自動推期望值）
    python3 check_layout.py <cleaned.md> --unit 宮 --expect 12
    python3 check_layout.py <sessions 根目錄> --all         # 整批掃

## 兩層檢查

**A 通用**（零設定，任何材料都適用）
  - 插入點吃字：FIG 區塊前只剩孤字 → merge 的 off-by-one
  - 圖連結斷鏈：`![](images/x.jpg)` 指不到檔案
  - FIG 名稱重複：同一張圖被插兩次

**B 表格系列**（要 `--unit`，只有「多張圖湊成一組清單」的材料才適用）
  - 項數是否齊（十二宮就該 12）
  - 單張內是否自身重複
  - 相鄰兩圖是否大量重疊 → 跨頁分裂的指紋

單張敘述型圖說沒有「單位」可比，只跑 A 就對了，**不必硬套 B**。
不給 `--unit` 時會自己看有沒有表格系列，有才提示你可以加。

## 為什麼需要這支

2026-09-07：直排表格跨頁分裂，五顆星的卡摻進前一顆星的內容——
內容每一句都是真的，組合起來全錯，內容檢查必定放行。
2026-09-08：merge off-by-one 把下一段第一個字吞進圖說前面，
853 張中 407 處中招——字沒少、圖沒丟，落地率與連結檢查全部通過。
**這兩種錯都只有做結構檢查才看得到。**
"""
import argparse, collections, json, pathlib, re, sys

FIG = re.compile(r"<!-- FIG:(\S+?) BEGIN -->(.*?)<!-- FIG:\1 END -->", re.S)
ORPHAN = re.compile(r"(?:^|\n)([一-鿿])\n\n<!-- FIG:(\S+?) BEGIN")
IMGLINK = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
UNIT_HINT = re.compile(r"^\|\s*([^\s|]{1,5}([宮章節星節課]))\s*\|", re.M)


def check_universal(md: pathlib.Path, text: str):
    """零設定檢查，回傳 (問題數, 訊息列表)。"""
    msgs, bad = [], 0

    orphan = ORPHAN.findall(text)
    if orphan:
        bad += len(orphan)
        msgs.append(f"❗ {len(orphan)} 處 FIG 前有孤字（插入點 off-by-one，吃掉下一段第一個字）")
        for ch, fig in orphan[:3]:
            msgs.append(f"     {fig} 前孤零零一個「{ch}」")

    names = [n for n, _ in FIG.findall(text)]
    dup = [k for k, c in collections.Counter(names).items() if c > 1]
    if dup:
        bad += len(dup)
        msgs.append(f"❗ {len(dup)} 張圖被插入多次：{'、'.join(dup[:4])}")

    dead = [l for l in IMGLINK.findall(text)
            if not l.startswith(("http://", "https://", "data:"))
            and not (md.parent / l).exists()]
    if dead:
        bad += len(dead)
        msgs.append(f"❗ {len(dead)} 條圖連結指不到檔案：{'、'.join(dead[:3])}")

    return bad, msgs, len(names)


def detect_unit(items):
    """從 OCR 的短標籤自動推「單位」：出現最多次的共同字尾。

    每本書的結構不一樣，不該由人按書設 --unit。短標籤（≤4 字）的字尾統計
    很穩定：紫微的表是「宮」，課程講義可能是「章」「節」「課」。
    只取出現 ≥6 次的，避免把偶然的字尾當成單位。
    """
    tail = collections.Counter(s[-1] for s in items if 2 <= len(s) <= 4)
    for ch, n in tail.most_common(3):
        if n >= 6:
            return ch, n
    return None, 0


def check_from_ocr(session: pathlib.Path, unit=None, expect=None):
    """**轉錄之前**就能跑的版面檢查：直接讀 figures.json 的 OCR。

    比讀 markdown 早一步，好處有三：
      1. 花 token 轉錄前就抓到「這張圖只有 11 項、少一項」
      2. 不依賴轉錄者有沒有正確重現表格
      3. 單位自動推，不必按書手設
    """
    fj = next((c for c in (session/"figures.json", session/"assets"/"figures.json")
               if c.exists()), None)
    if not fj:
        return 0, ["（找不到 figures.json，跳過 OCR 版面檢查）"]
    data = json.loads(fj.read_text(encoding="utf-8"))
    have = [it for it in data if isinstance(it.get("ocr"), dict) and it["ocr"].get("lines")]
    if not have:
        return 0, ["（figures.json 沒有 OCR 資料——先跑 `ocr_figures.py`，"
                   "版面檢查就能在轉錄前跑）"]

    shorts = {it["file"]: [l.strip() for l in it["ocr"]["lines"] if 2 <= len(l.strip()) <= 4]
              for it in have}
    if not unit:
        unit, n = detect_unit([s for v in shorts.values() for s in v])
        if not unit:
            return 0, [f"（{len(have)} 張有 OCR，但看不出重複的項目單位，跳過）"]
    per = {f: sorted({s for s in v if s.endswith(unit)}) for f, v in shorts.items()}
    per = {f: v for f, v in per.items() if len(v) >= 3}
    if not per:
        return 0, [f"（沒有含「{unit}」項目的圖，跳過）"]

    exp = expect or collections.Counter(len(v) for v in per.values()).most_common(1)[0][0]
    # 只檢查「表格類」：項數接近基準的才是缺項，項數很少的是命盤示意圖那種
    # 本來就只畫幾格的圖，拿基準去套會製造大量誤報（實測 i-020／i-004／i-025
    # 只有 3–4 個宮，那是示意圖不是殘缺的表）。門檻 0.6×基準。
    floor = max(3, int(exp * 0.6))
    tablish = {f: v for f, v in per.items() if len(v) >= floor}
    diagram = len(per) - len(tablish)
    msgs = [f"OCR 版面檢查：{len(per)} 張含「{unit}」項目，基準每張 {exp} 項"
            f"{'（--expect）' if expect else '（取眾數，單位自動偵測）'}",
            f"其中 {len(tablish)} 張像表格（≥{floor} 項）、"
            f"{diagram} 張像示意圖（<{floor} 項，不計入缺項檢查）"]
    bad = 0
    for f, v in sorted(tablish.items()):
        if len(v) != exp:
            bad += 1
            msgs.append(f"❗ {f:<14}OCR 只讀到 {len(v)} 個「{unit}」，少 {exp-len(v)} 項"
                        f" → 可能跨頁分裂或圖被裁掉")
    # 相鄰要照檔名序，字典序會拿不相鄰的兩張互比、製造假重疊
    keys = sorted(tablish)
    per = tablish
    for a_, b_ in zip(keys, keys[1:]):
        ov = set(per[a_]) & set(per[b_])
        if len(ov) >= max(3, min(len(per[a_]), len(per[b_])) * 0.5):
            bad += 1
            msgs.append(f"❗ {a_} 與 {b_} 的「{unit}」重疊 {len(ov)} 個 → 邊界可能劃錯")
    return bad, msgs


def check_series(text: str, unit: str, expect=None, min_rows=3):
    """表格系列檢查，回傳 (問題數, 訊息列表)。"""
    rows_of = lambda b: re.findall(rf"^\|\s*([^\s|]{{1,6}}{re.escape(unit)})\s*\|", b, re.M)
    figs = [(n, rows_of(b)) for n, b in FIG.findall(text)]
    figs = [(n, r) for n, r in figs if len(r) >= min_rows]
    if not figs:
        return 0, [f"（沒有含「{unit}」表格的圖說，跳過表格系列檢查）"]

    exp = expect or collections.Counter(len(r) for _, r in figs).most_common(1)[0][0]
    msgs = [f"表格系列：{len(figs)} 張含表，基準每張 {exp} 項"
            f"{'（--expect）' if expect else '（取眾數）'}"]
    bad = 0
    for n, r in figs:
        flags = []
        if len(r) != exp: flags.append(f"項數 {len(r)}≠{exp}")
        d = [k for k, c in collections.Counter(r).items() if c > 1]
        if d: flags.append(f"自身重複 {'、'.join(d)}")
        if flags:
            bad += 1; msgs.append(f"❗ {n:<14}{'；'.join(flags)}")
    for (n1, r1), (n2, r2) in zip(figs, figs[1:]):
        ov = set(r1) & set(r2)
        if len(ov) >= max(3, min(len(r1), len(r2)) * 0.5):
            bad += 1
            msgs.append(f"❗ {n1} 與 {n2} 重疊 {len(ov)} 項 → 很可能是邊界劃錯或同一單位被切成兩張")
    return bad, msgs


def run_one(md: pathlib.Path, a):
    text = md.read_text(encoding="utf-8")
    bad, msgs, n_fig = check_universal(md, text)
    if n_fig == 0:
        return 0, [f"{md.parent.name}／{md.name}：沒有 FIG 圖說區塊，跳過"]
    head = [f"── {md.parent.name}／{md.name}（{n_fig} 張圖說）"]
    if a.unit:
        b2, m2 = check_series(text, a.unit, a.expect)
        bad += b2; msgs += m2
    else:
        hint = collections.Counter(m.group(2) for m in UNIT_HINT.finditer(text))
        if hint:
            u, c = hint.most_common(1)[0]
            msgs.append(f"💡 偵測到 {c} 列以「{u}」結尾的表格首欄——"
                        f"想驗項數齊不齊可加 `--unit {u}`")
    return bad, head + ["  " + m for m in msgs]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".",
                    help="md 檔；配 --all 給 sessions 根目錄；配 --from-ocr 則忽略")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--unit", help="表格首欄項目的共同字尾，例如「宮」。不給就只跑通用檢查")
    ap.add_argument("--expect", type=int, help="每張應有幾項；不給就取眾數")
    ap.add_argument("--from-ocr", metavar="SESSION",
                    help="改讀 figures.json 的 OCR 做版面檢查（**轉錄之前就能跑**，"
                         "單位自動偵測，不必給 --unit）。先跑 ocr_figures.py 產生 OCR。")
    a = ap.parse_args(argv)

    if a.from_ocr:
        s = pathlib.Path(a.from_ocr).expanduser()
        bad, msgs = check_from_ocr(s, a.unit, a.expect)
        print(f"── {s.name}")
        print("\n".join("  " + m for m in msgs))
        if bad:
            print(f"\n共 {bad} 處可疑。**轉錄前就抓到，省下白做的 token。**")
            return 2
        print("\n✅ OCR 版面檢查通過")
        return 0

    root = pathlib.Path(a.path).expanduser()
    mds = []
    if a.all:
        for d in sorted(root.iterdir()):
            if d.is_dir():
                mds += [q for q in (d/"cleaned.md", d/"extracted.md") if q.exists()]
    else:
        mds = [root]

    total = 0
    for md in mds:
        bad, lines = run_one(md, a)
        total += bad
        if bad or not a.all:
            print("\n".join(lines))
    if total:
        print(f"\n共 {total} 處可疑。**這些是結構問題不是內容問題**——"
              f"回去開圖看版面，或檢查 merge 的插入邏輯，不要只重讀文字。")
        return 2
    print(f"\n✅ {len(mds)} 份檢查通過")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
