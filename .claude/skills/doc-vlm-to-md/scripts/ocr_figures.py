#!/usr/bin/env python3
"""把 session 裡每一張圖 OCR 一遍，結果寫回 figures.json。**clean 完就跑，0 雲端 token。**

MM 2026-09-08：「每本書都不一樣，應該要可以被標準化處理，
看是不是 clean 完先對每一張做 OCR」。

OCR 一次、兩處受用：
  1. **轉錄 prompt**：先 OCR 再讓模型讀，字形錯誤大幅下降（見 SKILL.md Step 2.5 實測）
  2. **版面結構自動偵測**：`check_layout.py --from-ocr` 直接從 OCR 推單位與項數，
     **不必按書手設 `--unit`**，而且**在轉錄之前就能跑**——問題在花 token 前就抓到

冪等：已有 ocr 欄位的圖預設跳過，`--force` 才重跑。可中斷續跑。

用法：
    python3 ocr_figures.py <session 目錄> [...]
    python3 ocr_figures.py --all <sessions 根目錄>
    python3 ocr_figures.py <session> --force
"""
import argparse, base64, json, os, pathlib, sys, urllib.error, urllib.request

DEFAULT_OCR = "http://100.120.197.113:8082/ocr"


def ocr_url():
    if os.environ.get("OCR_URL"):
        return os.environ["OCR_URL"]
    env = pathlib.Path.home()/"GithubRepo_mm-xyz/mars-cc/.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OCR_URL="):
                return line.split("=", 1)[1].strip()
    return DEFAULT_OCR


def find_figures(session: pathlib.Path):
    for c in (session/"figures.json", session/"assets"/"figures.json"):
        if c.exists():
            return c
    return None


def images_dir(session: pathlib.Path, fj: pathlib.Path):
    for c in (fj.parent/"images", session/"images", session/"assets"/"images"):
        if c.is_dir():
            return c
    return None


def run(session: pathlib.Path, url: str, force: bool, timeout: int):
    fj = find_figures(session)
    if not fj:
        return None, "找不到 figures.json"
    data = json.loads(fj.read_text(encoding="utf-8"))
    imgs = images_dir(session, fj)
    if not imgs:
        return None, "找不到 images/ 目錄"

    done = skip = fail = 0
    for i, it in enumerate(data, 1):
        if it.get("ocr") and not force:
            skip += 1
            continue
        p = imgs/it["file"]
        if not p.exists():
            fail += 1
            it["ocr"] = {"error": "圖檔不存在"}
            continue
        try:
            body = json.dumps({"image_b64": base64.b64encode(p.read_bytes()).decode()}).encode()
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as x:
                d = json.load(x)
            it["ocr"] = {"text": d.get("text", ""), "lines": d.get("lines", []),
                         "count": d.get("count", 0)}
            done += 1
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
            # 單張失敗不中斷整批——記下來，續跑時 --force 或直接重跑會補
            it["ocr"] = {"error": str(e)[:120]}
            fail += 1
        if i % 20 == 0:
            fj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"    …{i}/{len(data)}", flush=True)
    fj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return (done, skip, fail, len(data)), None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--all", action="store_true", help="把根目錄下每個子目錄當 session")
    ap.add_argument("--force", action="store_true", help="已有 ocr 的也重跑")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args(argv)

    url = ocr_url()
    print(f"OCR 端點：{url}\n")
    sessions = []
    for p in a.paths:
        p = pathlib.Path(p).expanduser()
        sessions += [q for q in sorted(p.iterdir()) if q.is_dir()] if a.all else [p]

    tot = [0, 0, 0, 0]
    for s in sessions:
        res, err = run(s, url, a.force, a.timeout)
        if err:
            print(f"  {s.name[:44]:<46}（{err}，跳過）")
            continue
        d, k, f, n = res
        tot = [tot[0]+d, tot[1]+k, tot[2]+f, tot[3]+n]
        print(f"  {s.name[:44]:<46}新 {d}／跳過 {k}／失敗 {f}　共 {n}")
    print(f"\n合計 {tot[3]} 張：新做 {tot[0]}、已有跳過 {tot[1]}、失敗 {tot[2]}")
    if tot[2]:
        print("⚠️  失敗的已記在 figures.json 的 ocr.error，重跑本指令會自動補")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
