#!/usr/bin/env python3
"""用 macOS Vision 對每張圖 OCR，寫回 figures.json。**本機跑、不碰 llm-node。**

MM 2026-09-08：「OCR 可以 MAC 本機跑吧」——可以，而且影片線（scripts/frames/ocr.py）
早就在用了，是我沒複用：把 1,090 張全丟遠端，還把 llm-node 壓垮
（實測 37 張連續 Connection refused，load average 一度 9.3）。

該線註解實測數字：**Vision 每張 ~0.2s、繁中準、不用載模型**；
VLM 逐字抄錄每張 20–60s。差兩個數量級，而且 OCR 與 VLM 可以並行——
Mac 做 OCR、llm-node 專心做需要理解的 VLM 判斷。

與 ocr_figures.py（llm-node RapidOCR 版）寫同一個 `ocr` 欄位、可互換，
多一個 `engine` 欄位標明來源。

需要 pyobjc 的 Vision binding：本 repo 的 `.venv-audio/bin/python` 已具備。

用法：
    .venv-audio/bin/python ocr_mac.py <session 目錄> [--force]
    .venv-audio/bin/python ocr_mac.py --all <sessions 根目錄>
"""
import argparse, json, pathlib, sys, time

try:
    import Foundation, Vision
except ImportError:
    print("需要 macOS Vision binding（pyobjc）。"
          "用 good-students-note 的 .venv-audio/bin/python 跑。", file=sys.stderr)
    raise SystemExit(1)

LANGS = ["zh-Hant", "zh-Hans", "en-US", "ja-JP"]


def ocr_one(path: pathlib.Path):
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(LANGS)
    req.setUsesLanguageCorrection_(True)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(str(err))
    lines = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)
        if cand and len(cand):
            s = str(cand[0].string()).strip()
            if s:
                lines.append(s)
    return {"text": "\n".join(lines), "lines": lines, "count": len(lines),
            "engine": "macos-vision"}


def run(session: pathlib.Path, force: bool):
    fj = next((c for c in (session/"figures.json", session/"assets"/"figures.json")
               if c.exists()), None)
    if not fj:
        return None
    data = json.loads(fj.read_text(encoding="utf-8"))
    imgs = next((c for c in (fj.parent/"images", session/"images") if c.is_dir()), None)
    if not imgs:
        return None
    done = skip = fail = 0
    t0 = time.time()
    for i, it in enumerate(data, 1):
        cur = it.get("ocr")
        if isinstance(cur, dict) and cur.get("lines") and not force:
            skip += 1; continue
        p = imgs/it["file"]
        if not p.exists():
            it["ocr"] = {"error": "圖檔不存在", "engine": "macos-vision"}; fail += 1; continue
        try:
            it["ocr"] = ocr_one(p); done += 1
        except Exception as e:
            it["ocr"] = {"error": str(e)[:100], "engine": "macos-vision"}; fail += 1
        if i % 50 == 0:
            fj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    fj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return done, skip, fail, len(data), time.time() - t0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    sessions = []
    for p in a.paths:
        p = pathlib.Path(p).expanduser()
        sessions += [q for q in sorted(p.iterdir()) if q.is_dir()] if a.all else [p]

    T = [0, 0, 0, 0, 0.0]
    for s in sessions:
        r = run(s, a.force)
        if r is None:
            print(f"  {s.name[:44]:<46}（無 figures.json／images，跳過）"); continue
        d, k, f, n, el = r
        T = [T[0]+d, T[1]+k, T[2]+f, T[3]+n, T[4]+el]
        print(f"  {s.name[:44]:<46}新 {d}／跳過 {k}／失敗 {f}　共 {n}　{el:.1f}s")
    rate = f"{T[4]/T[0]:.2f}s/張" if T[0] else "—"
    print(f"\n合計 {T[3]} 張：新做 {T[0]}、跳過 {T[1]}、失敗 {T[2]}　用時 {T[4]:.0f}s（{rate}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
