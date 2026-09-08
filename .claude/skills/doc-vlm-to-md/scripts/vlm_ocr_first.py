#!/usr/bin/env python3
"""OCR-first 圖轉文字：先跑 RapidOCR 拿字形，再把 OCR 結果連同圖餵給 VLM 做結構化與校正。
理由：OCR 準在字形、VLM 準在結構語意，兩者錯的地方不同，互為校驗。
用法：vlm_ocr_first.py <圖目錄> <輸出.md> [--model gemma-12b] [--limit N]"""
import base64, json, pathlib, sys, urllib.request, argparse, time

# === Windows 中文輸出修正（cp1252 fix）===
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OCR_URL = "http://100.120.197.113:8082/ocr"
VLM_URL = "http://100.120.197.113:8080/v1"

def post(url, payload, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)

def ocr(img: pathlib.Path):
    d = post(OCR_URL, {"image_b64": base64.b64encode(img.read_bytes()).decode()})
    return d.get("text", ""), d.get("count", 0)

def vlm(model, img: pathlib.Path, ocr_text: str):
    prompt = f"""這是一張紫微斗數書籍的插圖。下面附上 OCR 對同一張圖的文字辨識結果。

【OCR 辨識結果（字形較準，但沒有版面結構，且可能有簡繁與形近字錯誤）】
{ocr_text}

請結合「你看到的圖」與「上面的 OCR 文字」轉錄成 markdown：
- **版面結構以你看到的圖為準**（哪一欄對哪一列、表格幾欄幾列）
- **個別文字以 OCR 為準**，除非 OCR 明顯是簡體或錯字（例：食狼→貪狼、毅→殺、槿→權、贞→貞、氯→氣）
- 兩者衝突且你無法判斷時，寫「（存疑：圖看似 X，OCR 為 Y）」

輸出格式：
**類型**：表格／流程圖／命盤圖／插畫／其他
**標題**：（圖上的標題，沒有寫「無標題」）
**知識含量**：高／中／低／無
**內容轉錄**：（表格→完整 markdown 表格逐格照抄；流程圖→階層列點；命盤圖→描述各格位置與內容；插畫→一句話）
**判讀價值**：一到兩句

規則：逐格照抄不要摘要、不要補充圖上沒有的知識、繁體中文全形標點。"""
    b64 = base64.b64encode(img.read_bytes()).decode()
    d = post(f"{VLM_URL}/chat/completions", {"model": model, "max_tokens": 3500, "temperature": 0.1,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]})
    return d["choices"][0]["message"]["content"], (d.get("usage") or {})

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("imgdir"); ap.add_argument("out")
    ap.add_argument("--model", default="gemma-12b"); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    imgs = sorted(pathlib.Path(a.imgdir).glob("*.jpg"))
    if a.limit: imgs = imgs[:a.limit]
    out, stats = [f"# OCR-first 轉錄（VLM={a.model} ＋ RapidOCR）\n"], []
    for i, p in enumerate(imgs, 1):
        print(f"[{i}/{len(imgs)}] {p.name}", flush=True)
        t0 = time.time()
        try:
            otext, ocount = ocr(p); t_ocr = time.time() - t0
            print(f"    OCR {ocount} 行 / {t_ocr:.1f}s", flush=True)
            t1 = time.time(); txt, u = vlm(a.model, p, otext); t_vlm = time.time() - t1
            pt, ct, tt = u.get("prompt_tokens","?"), u.get("completion_tokens","?"), u.get("total_tokens","?")
            stats.append((p.name, p.stat().st_size//1024, ocount, round(t_ocr,1), pt, ct, tt, round(t_vlm,1), len(txt)))
            out.append(f"\n## {p.name}\n\n> OCR {ocount} 行／{t_ocr:.1f}s｜VLM {tt} tok（in {pt}／out {ct}）／{t_vlm:.1f}s\n\n{txt}\n")
            print(f"    VLM {tt} tok / {t_vlm:.1f}s ✓", flush=True)
        except Exception as e:
            out.append(f"\n## {p.name}\n\n❗ 失敗：{e}\n"); print(f"    ✗ {e}", flush=True)
    if stats:
        tot = sum(s[6] for s in stats if isinstance(s[6], int))
        out += ["\n---\n\n## 成本統計\n",
                "| 圖 | 大小 | OCR行 | OCR秒 | prompt tok | completion tok | 合計 tok | VLM秒 | 產出字元 |",
                "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for s in stats: out.append("| " + " | ".join(str(x) for x in s) + " |")
        out.append(f"\n**{len(stats)} 張合計 {tot} tokens**（平均 {tot//len(stats)}／張）——本地推論，零 API 費用。")
    pathlib.Path(a.out).write_text("\n".join(out), encoding="utf-8")
    print(f"→ {a.out}")

if __name__ == "__main__": main()
