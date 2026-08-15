#!/usr/bin/env python3
"""scripts/audio/fillers_local.py — cutplan 贅字標記提案(Local LLM 實驗,#569)

    python3 scripts/audio/fillers_local.py --session sessions/<slug> \
        [--chunk 18] [--max-chunks 0] [--out cutplan.gemma-proposal.md]

把 cutplan.md 的保留 block(- [x])分 chunk 送 LM Studio(gemma-4 系),
請模型在**原文字上**包 ~~贅字/口頭禪~~(嗯、啊、欸、就是、然後…),產出
`cutplan.gemma-proposal.md` = 完整複製 cutplan.md + 通過驗證的刪除線標記。

鐵律(與 render_cut.py 對齊):文字一字不可改/增/刪,只准加 ~~ ~~。
防幻覺主閘:每個回覆 block 去掉 ~~ 與空白後必須逐字等於原文,不等就
丟棄該 block 並計數 — 寧可漏標,不可改字。

跳過:🎬 集錦區(重複行,標記只該落在正文出現處)、已含 ~~ 的 block
(MM 已有人工標記,不動)、未勾選 block。

**絕不改 cutplan.md 本體、絕不 render 出片** — 產出只是提案,人審真相源
仍是 cutplan.md;MM 認可後自己把標記搬過去(或 diff 套用)。

已知坑(2026-07-29 實測):
- gemma-4 QAT 是 reasoning 模型,思考也吃 max_tokens,太低 content 會空白
  — 預設 4096,content 空白自動放大重試一次。
- **26b-a4b-qat 的 reasoning 隨 chunk 行數爆炸**(5 行 ≈ 900 token 可收斂,
  18 行 >4093 直接燒光額度、content 永遠空白;reasoning_effort=low 也壓不住),
  且長版嚴詞 system prompt 會誘發無限自我檢查。全檔跑批用 `--model
  google/gemma-4-e4b`(18 行/chunk ≈ 45s 收斂);26b 只適合 ≤5 行小 chunk。
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frames"))
from common import load_config  # env 優先序:環境變數 > repo/.env > mars-cc/.env

LINE_RE = re.compile(r"^- \[( |x|X)\] (B\d{3,5}) \[([^\]]+)\] (.*)$")
REPLY_RE = re.compile(r"^\**\s*(B\d{3,5})\**\s*[::]\s*(.*?)\s*$")

# 注意:更長更嚴詞的版本會誘發 gemma-4 QAT 無限 reasoning(見檔頭坑),勿加料。
SYSTEM_PROMPT = (
    "你是 podcast 逐字稿的贅字標記員。把無資訊量的填充詞"
    "(嗯、啊、欸、哦、就是、然後、那個、對啊、其實 之類)用 ~~ 包起來;"
    "只有純填充才標,承載語義就不標;一字不可改增刪、標點空白照抄;"
    "輸出每行 `B編號: 帶刪除線全文`;沒有要標的行不輸出;"
    "不要任何說明或 code fence。")


def parse_cutplan(path: Path) -> tuple[list[str], list[dict]]:
    """回傳 (原始行, 候選 block 列表)。候選=正文區的 - [x] 且不含 ~~。

    🎬 集錦區(`## 🎬` 起到下一個 `## ` 止)整段跳過 — 那裡是正文行的複製,
    標記只該落在正文出現處。以行號定位,同 id 重複出現不會互相污染。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    cands = []
    in_teaser = False
    for i, line in enumerate(lines):
        s = line.rstrip()
        if s.startswith("## "):
            in_teaser = s.startswith("## 🎬")
            continue
        if in_teaser:
            continue
        m = LINE_RE.match(s)
        if not m or m.group(1).lower() != "x":
            continue
        body = m.group(4)
        tail = ""
        if " ← " in body:
            body, t = body.rsplit(" ← ", 1)
            tail = " ← " + t
        sp = re.match(r"^(\[[^\]]{1,20}\]\s*)", body)
        speaker = sp.group(1) if sp else ""
        text = body[len(speaker):]
        if "~~" in text or not text.strip():
            continue  # MM 已有人工標記(或空行)不動
        cands.append({
            "lineno": i, "id": m.group(2), "text": text,
            "prefix": s[: s.index(body)] + speaker, "tail": tail,
        })
    return lines, cands


def chat(cfg: dict, model: str, user_msg: str, max_tokens: int,
         reasoning_effort: str = "low") -> str:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user_msg}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    req = urllib.request.Request(
        f"{cfg['LM_STUDIO_URL']}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['LM_STUDIO_TOKEN']}",
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode(),
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        msg = json.load(resp)["choices"][0]["message"]
    return msg.get("content") or ""


def pick_model(cfg: dict) -> str:
    req = urllib.request.Request(
        f"{cfg['LM_STUDIO_URL']}/models",
        headers={"Authorization": f"Bearer {cfg['LM_STUDIO_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        ids = [m["id"] for m in json.load(resp)["data"]]
    for pat in ("gemma-4-26b", "gemma"):
        hit = [i for i in ids if pat in i]
        if hit:
            return hit[0]
    sys.exit(f"[fillers] FAIL: LM Studio 沒有 gemma 系 model(現有:{ids})")


def verify(marked: str, original: str) -> str | None:
    """回傳 None=通過;否則丟棄原因。主閘:去 ~~ 去空白後逐字等於原文。"""
    if marked.count("~~") % 2:
        return "~~ 未成對"
    if "~~" not in marked:
        return "無標記"  # 不算改字,單獨計
    clean = marked.replace("~~", "")
    if re.sub(r"\s+", "", clean) != re.sub(r"\s+", "", original):
        return "改字"
    return None  # 刪除線涵蓋全句也合法 — 人工標記有整行包掉的前例


def propose(cfg: dict, model: str, cands: list[dict], chunk_size: int,
            max_chunks: int, max_tokens: int,
            reasoning_effort: str = "low") -> tuple[dict, dict]:
    """回傳 (id→帶刪除線全文, 統計)。逐 chunk 送、逐 block 機械驗證。"""
    marks: dict[str, str] = {}
    stats = {"processed": 0, "marked": 0, "dropped": 0, "empty_retries": 0}
    chunks = [cands[i:i + chunk_size] for i in range(0, len(cands), chunk_size)]
    if max_chunks:
        chunks = chunks[:max_chunks]
    for ci, ch in enumerate(chunks, 1):
        by_id = {c["id"]: c for c in ch}
        user_msg = "\n".join(f"{c['id']}: {c['text']}" for c in ch)
        content = chat(cfg, model, user_msg, max_tokens, reasoning_effort)
        if not content.strip():  # reasoning 吃光額度 → 放大重試一次
            stats["empty_retries"] += 1
            content = chat(cfg, model, user_msg, max_tokens * 2, reasoning_effort)
        stats["processed"] += len(ch)
        got = dropped = 0
        for line in content.splitlines():
            m = REPLY_RE.match(line.strip())
            if not m:
                continue
            bid, marked = m.group(1), m.group(2)
            if bid not in by_id or bid in marks:
                stats["dropped"] += 1
                dropped += 1
                continue
            why = verify(marked, by_id[bid]["text"])
            if why == "無標記":
                continue
            if why:
                stats["dropped"] += 1
                dropped += 1
                continue
            marks[bid] = marked
            got += 1
        stats["marked"] += got
        print(f"[fillers] chunk {ci}/{len(chunks)}: {len(ch)} block → "
              f"標 {got} 丟 {dropped}", flush=True)
    return marks, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="cutplan 贅字標記提案(local LLM)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--chunk", type=int, default=18, help="每 chunk block 數")
    ap.add_argument("--max-chunks", type=int, default=0, help="只跑前 N chunk(0=全部)")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--model", default="",
                    help="LM Studio model id;不給則自動挑(偏好 gemma-4-26b,"
                         "但全檔跑批建議 google/gemma-4-e4b,見檔頭坑)")
    ap.add_argument("--reasoning-effort", default="low",
                    help="reasoning 模型的思考額度(low/medium/high;空字串=不送)")
    ap.add_argument("--out", default="cutplan.gemma-proposal.md")
    args = ap.parse_args()

    sdir = Path(args.session)
    plan = sdir / "cutplan.md"
    if not plan.is_file():
        sys.exit(f"[fillers] FAIL: 找不到 {plan}")
    cfg = load_config()
    if "LM_STUDIO_TOKEN" not in cfg:
        sys.exit("[fillers] FAIL: mars-cc/.env 找不到 LM_STUDIO_TOKEN")
    model = args.model or pick_model(cfg)
    lines, cands = parse_cutplan(plan)
    print(f"[fillers] {plan}: 候選 {len(cands)} block(已跳過 🎬 區與既有 ~~)"
          f",model={model}", flush=True)

    t0 = time.time()
    marks, stats = propose(cfg, model, cands, args.chunk, args.max_chunks,
                           args.max_tokens, args.reasoning_effort)
    elapsed = time.time() - t0

    # 套標記:整份複製,只動通過驗證的行(以行號定位,不碰 🎬 複製行)
    for c in cands:
        if c["id"] in marks:
            lines[c["lineno"]] = c["prefix"] + marks[c["id"]] + c["tail"]
    header = [
        "> 🤖 **Gemma 贅字標記提案**(fillers_local.py,#569 實驗)— 本檔是提案,"
        "真相源仍是 cutplan.md,**勿直接 render 本檔**。",
        f"> model={model} | 處理 {stats['processed']} block | "
        f"標記 {stats['marked']} | 驗證丟棄 {stats['dropped']} | "
        f"空回覆重試 {stats['empty_retries']} | 耗時 {elapsed:.0f}s",
        "",
    ]
    out = sdir / args.out
    out.write_text("\n".join(lines[:1] + [""] + header + lines[2:]) + "\n",
                   encoding="utf-8")
    print(f"[fillers] done: 標記 {stats['marked']}/{stats['processed']}"
          f"(丟棄 {stats['dropped']},{elapsed:.0f}s)→ {out}")


if __name__ == "__main__":
    main()
