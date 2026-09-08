#!/usr/bin/env python3
"""開工前清點：這份文件到底有幾張圖？**不看副檔名推論，實際去數。**

存在理由（2026-09-07 事故）：kb_prep.py 曾寫死「EPUB 沒有圖」，
於是大耕 11 本書、1,010 張內嵌圖從來沒進過管線——而且不報錯。
後果不只是少了圖，是所有「語料裡沒有這個」的判斷都建立在殘缺語料上。

用法：
    python3 count_figures.py <檔案或目錄> [...]

輸出每份文件的圖片數。**數量為 0 才可以說「這份沒有圖」。**
"""
import sys, zipfile, pathlib, re

IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tif", ".tiff"}


def count_epub(p):
    """EPUB＝zip，直接數內部圖檔。這是當初被跳過的那一條路。"""
    try:
        with zipfile.ZipFile(p) as z:
            return sum(1 for n in z.namelist()
                       if pathlib.Path(n).suffix.lower() in IMG_EXT), ""
    except Exception as e:
        return -1, f"讀取失敗：{e}"


def count_pdf(p):
    """PDF 有兩種圖：點陣（get_images）與向量圖表（get_drawings 偵測整頁）。
    只數點陣會漏掉顧問報告那種向量畫的 Exhibit。"""
    try:
        import fitz
    except ImportError:
        return -1, "需要 PyMuPDF（pip install pymupdf）"
    try:
        doc = fitz.open(p)
        raster = sum(len(pg.get_images(full=True)) for pg in doc)
        vector = sum(1 for pg in doc if len(pg.get_drawings()) > 40)
        doc.close()
        return raster + vector, f"（點陣 {raster}／向量圖表頁 {vector}）"
    except Exception as e:
        return -1, f"讀取失敗：{e}"


def count_md(p):
    """已轉好的 markdown：數圖片語法與 FIG 錨點，用來核對落地率。"""
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return -1, f"讀取失敗：{e}"
    inline = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", t))
    figs = len(re.findall(r"<!-- FIG:\S+ BEGIN -->", t))
    return inline + figs, f"（圖片語法 {inline}／已填圖說 {figs}）"


HANDLER = {".epub": count_epub, ".pdf": count_pdf, ".md": count_md, ".markdown": count_md}


def walk(target):
    p = pathlib.Path(target)
    if p.is_dir():
        for q in sorted(p.rglob("*")):
            if q.is_file() and q.suffix.lower() in HANDLER: yield q
    elif p.is_file():
        yield p


def main(argv):
    if not argv:
        print(__doc__); return 2
    rows, total, unknown = [], 0, []
    for target in argv:
        for f in walk(target):
            fn = HANDLER.get(f.suffix.lower())
            if not fn:
                unknown.append(f); continue
            n, note = fn(f)
            rows.append((f, n, note))
            if n > 0: total += n
    if not rows and not unknown:
        print("找不到可清點的檔案（支援 .epub / .pdf / .md）"); return 1
    w = max((len(r[0].name) for r in rows), default=10)
    for f, n, note in rows:
        mark = "❗" if n < 0 else ("　" if n else "⚠️")
        cnt = "讀取失敗" if n < 0 else f"{n:>5} 張"
        print(f"{mark} {f.name:<{w}}  {cnt}  {note}")
    print(f"\n合計 {total} 張圖，{len(rows)} 份文件")
    zero = [f.name for f, n, _ in rows if n == 0]
    if zero:
        print(f"⚠️  這 {len(zero)} 份實測 0 張：{'、'.join(zero[:5])}"
              f"{' …' if len(zero) > 5 else ''}")
        print("    （實測 0 才算沒有圖；不可以用副檔名推論就跳過）")
    if unknown:
        print(f"❗ {len(unknown)} 份格式不支援、**未清點**，不可當作沒有圖："
              f"{'、'.join(p.name for p in unknown[:5])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
