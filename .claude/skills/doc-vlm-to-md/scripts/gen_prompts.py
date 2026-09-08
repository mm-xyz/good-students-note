#!/usr/bin/env python3
"""為 T1 候選圖批次產生讀圖 prompt（含 OCR）。確定性、可重跑。"""
import json, pathlib, base64, urllib.request, re, sys, time
S = pathlib.Path("/Users/marslo/GithubRepo_mm-xyz/good-students-note/sessions")
T1 = pathlib.Path(__file__).resolve().parent
OCR = "http://100.120.197.113:8082/ocr"

def ocr(p):
    b = json.dumps({"image_b64": base64.b64encode(p.read_bytes()).decode()}).encode()
    r = urllib.request.Request(OCR, data=b, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r, timeout=180) as x:
        d = json.load(x); return d.get("text",""), d.get("count",0)

rows = json.loads((T1/(sys.argv[1] if len(sys.argv)>1 else "t1_list.json")).read_text(encoding="utf-8"))
(T1/"prompts").mkdir(exist_ok=True)
done = 0
for i, r in enumerate(rows, 1):
    key = f"{r['sess']}__{r['file']}"
    op = T1/"prompts"/f"{key}.txt"
    if op.exists(): done += 1; continue
    img = S/r["sess"]/"images"/r["file"]
    if not img.exists(): print(f"[{i}] ✗ 圖不存在 {img}"); continue
    try: otext, ocount = ocr(img)
    except Exception as e: otext, ocount = f"（OCR 失敗：{e}）", 0
    op.write_text(f"""這是大耕老師的著作《{r['book']}》裡面的一張圖，解釋給我聽。

【這張圖在原文的位置（前文）】
…{r['before']}
〔此處為圖〕

【OCR 對這張圖的文字辨識（{ocount} 行；字形較準但無版面結構，可能有簡繁與形近字錯誤，
例如 食狼→貪狼、毅→殺、槿→權、贞→貞、氯→氣、宫→宮）】
{otext}

請結合圖片本身與上述資訊產出：

**這張圖在講什麼**：兩三句說明它的用途，以及各欄位／各區塊的意義。
**類型**：表格／流程圖／命盤圖／插畫
**標題**：圖上寫的標題，沒有寫「無標題」
**知識含量**：高／中／低／無
**內容轉錄**：表格→完整 markdown 表格；流程圖→階層列點或 mermaid；命盤圖→描述十二格各自的位置與內容；插畫→一句話
**判讀價值**：這張圖回答了什麼問題

版面結構以圖為準，個別文字以 OCR 為準（除非 OCR 明顯是簡體或形近錯字）。
兩者衝突且無法判斷時寫「（存疑：圖看似 X，OCR 為 Y）」。看不清的寫「（辨識不清）」，不要猜。
繁體中文，全形標點。""", encoding="utf-8")
    done += 1
    if i % 10 == 0: print(f"  {i}/{len(rows)} …", flush=True)
print(f"→ 產出 {done}/{len(rows)} 份 prompt")
