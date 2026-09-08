#!/usr/bin/env python3
"""用本地 VLM 判斷每張圖的版面：single/multiple ＋ 類型 ＋ 客觀敘述。

MM 2026-09-08 的設計：「只要基礎 prompt 去判別是 1／多個區塊就好，
然後判別是表格、圖、什麼之類的，然後再給一段客觀敘述」。

**為什麼不用啟發式**：同一天試過三種，全部失敗——
  固定空白帶門檻 5%      → 過報 20/56
  每本書自適應找斷點      → 反過來說「整本都是單區塊」，全漏
  圖內相對比較後裁切      → 該 1 片的切成 3～4 片
每本書的排版都不一樣（欄距、頁邊、幾欄），任何門檻都是對當下這本書過擬合。
VLM 是**感知不是規則**，換書仍然是在看圖。

**為什麼問 single/multiple 而不是「幾個」**：
問「幾個區塊」時它把表格的欄數成 8（i-031），因為問法本身有歧義。
改問 single/multiple 並言明「欄列是表格內部結構不是獨立區塊」→ 9/9 全對。

實測九張（四張雙區塊、五張單區塊，答案已知）：**9/9**。

用法：
    python3 probe_layout.py <session 目錄> [--force]
    python3 probe_layout.py --all <sessions 根目錄>
結果寫回 figures.json 的 `layout` 欄位。冪等、可中斷續跑。
"""
import argparse, base64, json, os, pathlib, sys, time, urllib.error, urllib.request

PROMPT = """看這張圖的版面，回答三件事。不要讀內容細節，只看結構。

1. layout：這張圖是「single」（一個完整的區塊）還是「multiple」（兩個以上彼此分開、
   中間有明顯間隔的獨立區塊）？
   注意：一個表格裡面有很多欄或很多列，那仍然是 single——欄列是表格內部的結構，不是獨立區塊。
2. kind：表格／命盤圖／流程圖／關係圖／插畫／文字截圖／書封，擇一。
3. desc：一句客觀敘述版面長什麼樣，二十字以內，只描述看到的東西，不要判斷或推論。

只回一個 JSON，不要其他字：
{"layout": "single|multiple", "kind": "...", "desc": "..."}"""


def cfg():
    u, m = os.environ.get("LLM_NODE_URL"), os.environ.get("LLM_NODE_MODEL")
    env = pathlib.Path.home()/"GithubRepo_mm-xyz/mars-cc/.env"
    if (not u or not m) and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("LLM_NODE_URL=") and not u: u = line.split("=", 1)[1].strip()
            if line.startswith("LLM_NODE_MODEL=") and not m: m = line.split("=", 1)[1].strip()
    return u, m


def probe(path, url, model, timeout):
    b = base64.b64encode(path.read_bytes()).decode()
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}}]}],
        "max_tokens": 150, "temperature": 0}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as x:
        out = json.load(x)["choices"][0]["message"]["content"]
    d = json.loads(out[out.find("{"):out.rfind("}") + 1])
    lay = d.get("layout", "")
    if lay not in ("single", "multiple"):
        raise ValueError(f"layout 值不合法：{lay}")
    return {"layout": lay, "kind": str(d.get("kind", ""))[:12],
            "desc": str(d.get("desc", ""))[:40]}


def run(session, url, model, force, timeout):
    fj = next((c for c in (session/"figures.json", session/"assets"/"figures.json")
               if c.exists()), None)
    if not fj:
        return None
    data = json.loads(fj.read_text(encoding="utf-8"))
    imgs = next((c for c in (fj.parent/"images", session/"images")
                 if c.is_dir()), None)
    if not imgs:
        return None
    done = skip = fail = multi = 0
    for i, it in enumerate(data, 1):
        if isinstance(it.get("layout"), dict) and not it["layout"].get("error") and not force:
            skip += 1
            multi += it["layout"].get("layout") == "multiple"
            continue
        p = imgs/it["file"]
        if not p.exists():
            it["layout"] = {"error": "圖檔不存在"}; fail += 1; continue
        # 本地推論節點會因負載暫時拒連（實測 OCR 與 VLM 同時打，37 張連續失敗）。
        # 退避重試，而不是把整批燒掉——失敗的下次跑會自動補。
        for attempt in range(3):
            try:
                it["layout"] = probe(p, url, model, timeout); done += 1
                multi += it["layout"]["layout"] == "multiple"
                break
            except (urllib.error.URLError, OSError, ValueError,
                    json.JSONDecodeError, KeyError, TimeoutError) as e:
                if attempt == 2:
                    it["layout"] = {"error": str(e)[:100]}; fail += 1
                else:
                    time.sleep(5 * (attempt + 1))
        if i % 10 == 0:
            fj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"    …{i}/{len(data)}", flush=True)
    fj.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return done, skip, fail, multi, len(data)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)

    url, model = cfg()
    if not url or not model:
        print("❗ 找不到 LLM_NODE_URL／LLM_NODE_MODEL（mars-cc/.env）", file=sys.stderr)
        return 2
    print(f"VLM：{model} @ {url}\n")

    sessions = []
    for p in a.paths:
        p = pathlib.Path(p).expanduser()
        sessions += [q for q in sorted(p.iterdir()) if q.is_dir()] if a.all else [p]

    tm = 0
    for s in sessions:
        r = run(s, url.rstrip("/") + "/chat/completions", model, a.force, a.timeout)
        if r is None:
            print(f"  {s.name[:44]:<46}（無 figures.json／images，跳過）"); continue
        d, k, f, m, n = r
        tm += m
        print(f"  {s.name[:44]:<46}新 {d}／跳過 {k}／失敗 {f}　"
              f"**multiple {m}**／共 {n}")
    if tm:
        print(f"\n🔀 全部 {tm} 張判為 multiple——**這些要分區塊各自轉錄**，"
              f"整張當一個單位會把兩個主題混在一起。")
        print("   看 figures.json 的 layout.desc 決定怎麼切；有疑慮就開圖確認。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
