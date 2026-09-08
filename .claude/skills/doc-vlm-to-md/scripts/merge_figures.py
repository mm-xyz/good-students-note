#!/usr/bin/env python3
"""把圖的轉錄結果塞回 cleaned.md 的原始位置（不是堆成附錄）。
定位靠 figures.json 的錨點前文——在 cleaned.md 裡找到那段文字，把轉錄插在它後面。
0 LLM、冪等（重跑會先移除舊的插入塊再重插）。"""
import json, pathlib, re, argparse, difflib, sys

# === Windows 中文輸出修正（cp1252 fix）===
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BEGIN = "<!-- FIG:{name} BEGIN -->"
END   = "<!-- FIG:{name} END -->"

def norm(s):
    """去空白＋清掉 HTML／CSS 殘骸（錨點取自原始 xhtml，雜訊有兩種）。

    2026-09-07 補 CSS：Kobo 匯出的 EPUB 會把 <style> 內容混進文字節點，
    錨點長成「紫微攻略．牌卡占卜.koboSpan{-webkit-text-combine:inherit;}紫微星」。
    原本只清 <…> 標籤，CSS 整段留著 → 錨點跟 cleaned.md 永遠對不上，
    《牌卡占卜》51 張裡有 21 張因此定位失敗。
    """
    s = re.sub(r"<[^>]*$|<[^>]*>", "", s)          # HTML 標籤（含被截斷的尾巴）
    s = re.sub(r"[.#@][\w-]+\s*\{[^}]*\}?", "", s)  # CSS 規則（含被截斷的）
    s = re.sub(r"\{[^}]*\}?", "", s)               # 落單的宣告區塊
    return re.sub(r"\s+", "", s)

def locate(body_norm, anchor_before, min_len=16):
    """用錨點前文的尾段在正文裡找插入點；找不到就逐步縮短再試。

    2026-09-07 修：原本只試 160→40 五段，且 `len(a) < take` 直接 continue，
    導致正規化後短於 40 字的錨點**每一段都被跳過、必定回 -1**。
    實測 510 張轉錄有 74 張因此靜默落地失敗（全部都有錨點，不是缺錨點）。
    改為：探針長度先夾到錨點實際長度，並補上 40 以下的短探針。
    """
    a = norm(anchor_before)
    if not a: return -1
    seen = set()
    for take in (160, 120, 90, 60, 40, 28, min_len):
        take = min(take, len(a))
        if take < min_len or take in seen: continue
        seen.add(take)
        i = body_norm.find(a[-take:])
        if i != -1: return i + take
    return -1


def locate_fuzzy(body_norm, anchor_before, min_len=24):
    """精確比對失敗時的最後手段：用 difflib 找最相似的一段。

    錨點取自原始 xhtml，cleaned.md 經過清洗（去標籤、正規化標點），
    偶有一兩字差異就讓 find() 全盤落空。相似度門檻設 0.82，
    寧可放棄也不要插錯位置——插錯比沒插更難發現。
    """
    a = norm(anchor_before)
    if len(a) < min_len: return -1
    probe = a[-60:] if len(a) >= 60 else a
    sm = difflib.SequenceMatcher(None, probe, body_norm, autojunk=False)
    m = sm.find_longest_match(0, len(probe), 0, len(body_norm))
    if m.size >= max(min_len, int(len(probe) * 0.82)):
        return m.b + m.size
    return -1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cleaned"); ap.add_argument("figures_json"); ap.add_argument("transcripts")
    ap.add_argument("--out", default=None)
    ap.add_argument("--single", default=None, help="轉錄檔只含一張圖時，指定它對應的圖檔名")
    ap.add_argument("--images-dir", default="images",
                    help="圖片資料夾相對 cleaned.md 的路徑（預設 images）。"
                         "epub_images.py 若輸出到 <out>/images 而 <out> 不與 cleaned.md 同層，"
                         "這裡要給對應的相對路徑，例如 assets/images，否則連結會斷。")
    a = ap.parse_args()

    src = pathlib.Path(a.cleaned).read_text(encoding="utf-8")
    # 先移除既有插入塊（冪等）
    src = re.sub(r"<!-- FIG:[^>]+ BEGIN -->.*?<!-- FIG:[^>]+ END -->\n?", "", src, flags=re.S)
    idx = {x["file"]: x for x in json.loads(pathlib.Path(a.figures_json).read_text(encoding="utf-8"))}

    # 轉錄檔：以 "## <圖檔名>" 分段
    tx = pathlib.Path(a.transcripts).read_text(encoding="utf-8")
    blocks = {}
    for m in re.finditer(r"^## (\S+\.(?:jpg|jpeg|png)).*?$\n(.*?)(?=^## \S+\.(?:jpg|jpeg|png)|\Z)", tx, re.S | re.M):
        blocks[m.group(1)] = m.group(2).strip()
    if not blocks:   # 單圖轉錄檔沒有 ## 標頭：檔名取自 --single
        if a.single: blocks[a.single] = tx.strip()
        else: print("⚠️ 轉錄檔沒有 `## 檔名.jpg` 標頭，且未給 --single，無法對應")

    # 建立 正規化位置 → 原文位置 的映射
    pos_map, buf = [], []
    for i, ch in enumerate(src):
        if not ch.isspace(): pos_map.append(i)
    body_norm = "".join(src[i] for i in pos_map)

    inserts = []   # (原文插入位置, 圖名, 內容)
    miss = []
    for name, block in blocks.items():
        it = idx.get(name)
        if not it or not it["anchors"]: miss.append((name, "無錨點")); continue
        # 逐一試過所有錨點（不是只試第一個——同一張圖常被引用多次，
        # 第一個錨點所在的段落未必進了 cleaned.md）
        p = -1
        for anc in it["anchors"]:
            p = locate(body_norm, anc.get("before", ""))
            if p != -1: break
        if p == -1:      # 全部精確比對失敗才降級到模糊比對
            for anc in it["anchors"]:
                p = locate_fuzzy(body_norm, anc.get("before", ""))
                if p != -1: break
        if p == -1: miss.append((name, f"{len(it['anchors'])} 個錨點在正文都找不到")); continue
        # 2026-09-08 修：p 是「比對到的錨點後面那個非空白字元」在 body_norm 的索引，
        # pos_map[p] 已經是該字元在原文的位置——插在這裡才會落在段落分界，
        # 不吃掉下一段的第一個字。舊版無條件 +1，遇到「錨點恰好在段落結尾」
        # （最常見的情況，圖本來就常夾在兩段之間）就會多跳一個字，
        # 把下一段第一個字吞進插入區塊前面，例如「但是」被切成「但」+插入區塊+「是」。
        # 只有比對落在全文最後（p 已經超出 pos_map 範圍，沒有下一個非空白字元）
        # 才需要 +1，插在最後一個字「之後」。
        real = pos_map[p] if p < len(pos_map) else pos_map[-1] + 1
        inserts.append((real, name, block))

    for real, name, block in sorted(inserts, reverse=True):
        # 連原圖：校正時要能一眼對照「轉錄寫的」與「圖上畫的」。
        # 相對路徑 images/<name>，與 cleaned.md 同層，sync_corpus 也會把 images/ 一起帶過去。
        chunk = (f"\n\n{BEGIN.format(name=name)}\n"
                 f"> 📊 **原書插圖轉錄**（`{name}`）——以下內容由圖片轉成文字，非原文字層。\n\n"
                 f"![{name}]({a.images_dir.rstrip('/')}/{name})\n\n"
                 f"{block}\n{END.format(name=name)}\n")
        src = src[:real] + chunk + src[real:]

    # 定位不到的不丟掉——附在檔尾並標明「位置不明」。
    # 靜默丟棄是最糟的失敗模式：轉錄成本已經付了，而且沒人會發現少了東西。
    if miss:
        src += ("\n\n---\n\n## 附錄：位置不明的插圖轉錄\n\n"
                "> 以下轉錄無法用錨點定位回原文位置（多半是錨點文字沒進 cleaned.md，\n"
                "> 或原書該段只有圖沒有可比對的文字）。內容有效，**只是不知道該插在哪**。\n")
        for name, why in miss:
            blk = blocks.get(name, "").strip()
            if not blk: continue
            src += (f"\n{BEGIN.format(name=name)}\n"
                    f"> 📊 **原書插圖轉錄**（`{name}`，位置不明：{why}）\n\n"
                    f"{blk}\n{END.format(name=name)}\n")

    out = pathlib.Path(a.out or a.cleaned)
    out.write_text(src, encoding="utf-8")
    print(f"塞回 {len(inserts)} 張" + (f"，另 {len(miss)} 張放附錄" if miss else "") + f" → {out}")
    for n, why in miss: print(f"  ⚠️ {n}：{why}")

if __name__ == "__main__": main()
