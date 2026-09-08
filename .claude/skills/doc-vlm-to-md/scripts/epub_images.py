#!/usr/bin/env python3
"""EPUB 內嵌圖抽取 + 錨定：把 zip 裡的圖抽出來，並記錄它在哪個章節、前後文是什麼。
0 LLM、確定性。補 doc-vlm-to-md 的破口（kb_prep.py 曾經認定「EPUB 沒有頁面圖像」，
現已由 kb_prep.py 呼叫本檔的 extract() 接上）。

單獨執行：
    python3 epub_images.py <book.epub> <outdir>
會在 <outdir>/images/ 存出所有圖、<outdir>/figures.json 存每張圖的尺寸與文字錨點。
"""
import argparse
import zipfile, pathlib, re, json, sys, html

# === Windows 中文輸出修正（cp1252 fix）===
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def extract(epub, outdir):
    out = pathlib.Path(outdir); (out/"images").mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(epub)
    names = z.namelist()
    imgs = [n for n in names if re.search(r"\.(jpe?g|png|gif)$", n, re.I)]
    docs = [n for n in names if re.search(r"\.x?html?$", n, re.I)]
    # 讀出每個 xhtml 的純文字與它引用的圖
    anchors = []
    for d in sorted(docs):
        try: raw = z.read(d).decode("utf-8", "ignore")
        except Exception: continue
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        for m in re.finditer(r'src=["\']([^"\']+\.(?:jpe?g|png|gif))["\']', raw, re.I):
            src = m.group(1).split("/")[-1]
            # 圖前後各取一段文字當錨點
            pos = m.start()
            pre = html.unescape(re.sub(r"<[^>]+>", " ", raw[max(0,pos-1200):pos]))
            post = html.unescape(re.sub(r"<[^>]+>", " ", raw[pos:pos+600]))
            anchors.append({"image": src, "doc": d,
                            "before": re.sub(r"\s+"," ",pre).strip()[-260:],
                            "after": re.sub(r"\s+"," ",post).strip()[:160]})
    saved = {}
    for n in imgs:
        base = n.split("/")[-1]
        data = z.read(n)
        (out/"images"/base).write_bytes(data)
        saved[base] = len(data)
    # 合併：每張圖 → 尺寸 + 所有錨點
    idx = []
    for base, size in sorted(saved.items(), key=lambda kv: -kv[1]):
        a = [x for x in anchors if x["image"] == base]
        idx.append({"file": base, "bytes": size, "anchors": a})
    (out/"figures.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(saved), len(anchors)

def main():
    ap = argparse.ArgumentParser(
        description="從 EPUB 抽出內嵌圖片，並用 xhtml 裡 <img src=...> 前後文當文字錨點記錄位置。")
    ap.add_argument("epub", help="輸入的 .epub 檔")
    ap.add_argument("outdir", help="輸出資料夾（會建立 images/ 與 figures.json）")
    args = ap.parse_args()
    n, a = extract(args.epub, args.outdir)
    print(f"抽出 {n} 張圖，{a} 個文字錨點 → {args.outdir}")


if __name__ == "__main__":
    main()
