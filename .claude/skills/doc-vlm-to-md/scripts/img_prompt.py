#!/usr/bin/env python3
"""依 MM 模板產生讀圖 prompt：出處框架 ＋ 章節錨點 ＋ OCR 結果 → 「解釋給我聽」。
框架有三個作用：(1) 給模型判讀脈絡 (2) 讓輸出自帶敘述而非密集術語表（避開 content filter 誤判）
(3) 章節資訊讓轉錄結果可回溯原書位置。

單獨執行：
    python3 img_prompt.py <figures.json> <書名> <圖檔名> [--author 作者] [--ocr-url URL]
"""
import argparse
import json, pathlib, re, base64, urllib.request, sys

# === Windows 中文輸出修正（cp1252 fix）===
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OCR_URL = "http://100.120.197.113:8082/ocr"

def ocr(img, ocr_url=OCR_URL):
    body = json.dumps({"image_b64": base64.b64encode(pathlib.Path(img).read_bytes()).decode()}).encode()
    req = urllib.request.Request(ocr_url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r); return d.get("text", ""), d.get("count", 0)

def chapter_of(anchor_before: str) -> str:
    """從錨點前文推章節：抓最後出現的『第N章』『N-N』或粗體小標。"""
    for pat in (r"第[一二三四五六七八九十百]+章[^。，]{0,24}", r"\b\d+[-‑]\d+\s*[^。，]{0,20}"):
        m = list(re.finditer(pat, anchor_before))
        if m: return m[-1].group(0).strip()
    return "（章節不明，見下方前文）"

def build(figures_json, book, image_name, author="大耕老師", ocr_url=OCR_URL):
    idx = json.loads(pathlib.Path(figures_json).read_text(encoding="utf-8"))
    it = next((x for x in idx if x["file"] == image_name), None)
    if not it: return None
    a = it["anchors"][0] if it["anchors"] else {"before": "", "after": ""}
    ch = chapter_of(a.get("before", ""))
    otext, ocount = ocr(pathlib.Path(figures_json).parent / "images" / image_name, ocr_url)
    return f"""這是{author}的著作《{book}》裡面 {ch} 的一張圖，解釋給我聽。

【這張圖在原文的位置（前後文）】
…{a.get('before','')[-200:]}
〔圖〕
{a.get('after','')[:120]}…

【OCR 對這張圖的文字辨識（{ocount} 行；字形較準但無版面結構，可能有簡繁與形近字錯誤，
例如 食狼→貪狼、毅→殺、槿→權、贞→貞、氯→氣）】
{otext}

請結合圖片本身與上述資訊，產出：

**這張圖在講什麼**：兩三句話說明它的用途，以及各欄位／各區塊的意義。
**類型**：表格／流程圖／命盤圖／插畫
**標題**：圖上寫的標題
**知識含量**：高／中／低／無
**內容轉錄**：表格→完整 markdown 表格；流程圖→階層列點；命盤圖→描述各格位置與內容；插畫→一句話
**判讀價值**：這張圖回答了什麼問題

版面結構以圖為準，個別文字以 OCR 為準（除非 OCR 明顯是簡體或形近錯字）。
兩者衝突且無法判斷時寫「（存疑：圖看似 X，OCR 為 Y）」。看不清的寫「（辨識不清）」，不要猜。
繁體中文，全形標點。"""

def main():
    ap = argparse.ArgumentParser(
        description="依 figures.json 的錨點 + OCR 結果，產生一份帶出處框架的讀圖 prompt。")
    ap.add_argument("figures_json", help="epub_images.py 產生的 figures.json 路徑")
    ap.add_argument("book", help="書名（會填進 prompt 的《書名》）")
    ap.add_argument("image_name", help="figures.json 裡的圖檔名（file 欄位）")
    ap.add_argument("--author", default="大耕老師", help="作者（預設：大耕老師，可覆寫）")
    ap.add_argument("--ocr-url", default=OCR_URL, help=f"RapidOCR 端點（預設 {OCR_URL}）")
    args = ap.parse_args()
    p = build(args.figures_json, args.book, args.image_name, args.author, args.ocr_url)
    print(p if p else "找不到該圖")


if __name__ == "__main__":
    main()
