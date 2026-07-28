#!/usr/bin/env python3
"""
scripts/audio/cutplan.py — Podcast 文字剪輯:cutplan 產生器(純 stdlib,零 LLM)

Descript 式流程,人只碰 markdown:
    1. `cutplan.py prepare --session <dir>`(本腳本)
       把 SRT 合併成 utterance blocks,產出全保留的 cutplan.md + cutplan.json,
       並寫 .cutplan_pending.json marker — 剪輯「提案」是判斷工作,依 Engine
       Routing 原則 5 交對話 agent(翻勾選+寫理由),本腳本零 LLM 呼叫。
    2. 對話 agent 提案:把要剪的 block 改 `- [ ]` 並附理由;可加 `## 章節標題`
       行(render 會轉成 chapters)。文字與時間碼不可改動。
    3. MM 人審 cutplan.md 勾選 — 這一步就是「剪輯」。
    4. `render_cut.py --session <dir>` 出片(見該檔)。

Block 合併規則:同 speaker、間隔 < --merge-gap(預設 1.2s)的連續 cue 併成一個
block,上限 --max-block(預設 45s)— 粒度太細人審很累,太粗剪不乾淨。
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, pick_transcript, fmt_mmss, rel

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def build_blocks(cues: list[dict], merge_gap: float, max_block: float) -> list[dict]:
    blocks = []
    cur = None
    for c in cues:
        joinable = (merge_gap > 0
                    and cur is not None
                    and c.get("speaker") == cur["speaker"]
                    and c["start"] - cur["end"] < merge_gap
                    and c["end"] - cur["start"] <= max_block)
        if joinable:
            cur["end"] = c["end"]
            cur["text"] += c["text"]
            cur["cue_idx"].append(c["idx"])
        else:
            if cur:
                blocks.append(cur)
            cur = {"start": c["start"], "end": c["end"], "speaker": c.get("speaker"),
                   "text": c["text"], "cue_idx": [c["idx"]]}
    if cur:
        blocks.append(cur)
    for i, b in enumerate(blocks, 1):
        b["id"] = f"B{i:04d}"
        b["keep"] = True
        b["reason"] = ""
    return blocks


def write_cutplan_md(blocks: list[dict], path: Path, slug: str, srt_name: str) -> None:
    lines = [
        f"# Cutplan — {slug}",
        "",
        f"> 來源:{srt_name}。`- [x]` = 保留,`- [ ]` = 剪掉;**改勾選就是剪輯**。",
        "> **字級精剪**:保留的 block 內把贅字/贅句包進 `~~刪除線~~`,render 會用",
        "> word 級時間軸精準剪掉那幾個字(需 words.json;--asr local 自動產)。",
        "> 停頓不用標:render 自動把 >1.5s 的停頓收緊到 0.6s(--max-pause 可調)。",
        "> 除了加刪除線,文字與時間碼不可改動(render 會逐 block 對 SRT 驗證)。",
        "> 可在 block 前加 `## 章節標題` 行,render 會轉成 podcast chapters。",
        "> **節目結構**(選用,播放順序=本文件行序):`## 🎬 名稱`=精華集錦區,把正文的",
        "> block 行**複製貼上**進來(可重複、順序自訂,每段自帶淡入淡出);",
        "> `## 🎵 檔名 fadein= fadeout= lead= tail= start= end=`=BGM overlay 疊接",
        "> (檔名可只寫 opening/break/ending 前綴,共用素材庫自動匹配;二段式 ducking",
        "> 包絡見 CLAUDE.md 原則 11)。典型結構:🎬集錦 → 🎵opening → 正文 → 🎵break",
        "> → 正文 → 🎵ending。",
        "> **⚙ config 區**(下一行):render 參數住這裡,吃鍵值覆蓋 CLI 預設,",
        "> 可用鍵=render_cut.py 的數值型旋鈕(dash 寫法)。",
        "> 出片:`python3 scripts/audio/render_cut.py --session sessions/<slug>`",
        "",
        "## ⚙ clip-gap=0.5 bgm-duck=0.15 bgm-solo=0.55 max-pause=1.5",
        "",
    ]
    for b in blocks:
        mark = "x" if b["keep"] else " "
        spk = f"[{b['speaker']}] " if b.get("speaker") else ""
        reason = f" ← {b['reason']}" if b["reason"] else ""
        lines.append(f"- [{mark}] {b['id']} [{fmt_mmss(b['start'])}–{fmt_mmss(b['end'])}] "
                     f"{spk}{b['text']}{reason}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare(args):
    session_dir = Path(args.session).resolve()
    spk_srt = session_dir / "transcript.speakers.srt"
    srt_src = spk_srt if spk_srt.exists() else pick_transcript(session_dir)
    cues = parse_srt(srt_src)
    blocks = build_blocks(cues, args.merge_gap, args.max_block)

    cp_json = session_dir / "cutplan.json"
    cp_json.write_text(json.dumps({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_srt": srt_src.name,
        "merge_gap": args.merge_gap,
        "max_block": args.max_block,
        "blocks": blocks,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    cp_md = session_dir / "cutplan.md"
    write_cutplan_md(blocks, cp_md, session_dir.name, srt_src.name)
    total = sum(b["end"] - b["start"] for b in blocks)
    print(f"[cutplan] {len(blocks)} blocks({fmt_mmss(total)} 內容)→ "
          f"{rel(cp_md, PROJECT_ROOT)}")

    prosody_note = ("prosody.json 已就緒,高分段見 highlights.md,剪點會 snap 靜音"
                    if (session_dir / "prosody.json").exists()
                    else "prosody.json 不存在 — 建議先跑 prosody stage,剪點才能 snap 靜音")
    marker = session_dir / ".cutplan_pending.json"
    marker.write_text(json.dumps({
        "stage": "cutplan-proposal",
        "input_file": rel(cp_md, PROJECT_ROOT),
        "aux_files": ["highlights.md", "prosody.json", "context.txt"],
        "rules_ref": "scripts/audio/cutplan.py docstring",
        "instructions": (
            "Podcast 剪輯提案待對話 agent 接手,零 API 呼叫(原則 5)。"
            "讀 cutplan.md 全部 blocks + highlights.md 高昂段,提議句級粗剪:"
            "開場寒暄/離題/假起頭/重複內容改 `- [ ]` 並在行尾加 ` ← 理由`;"
            "highlights.md 出現的段落原則上保留;保留 block 內的贅字/口誤/假起頭"
            "用 `~~刪除線~~` 標記字級精剪。**只准翻勾選、加刪除線、加理由/章節標題,"
            "不得改動任何 block 的文字與時間碼**(render 會逐 block 驗證,改了會 FAIL)。"
            "適當位置加 `## 章節標題` 行。完成後刪本 marker,"
            "然後回報 MM 人審 cutplan.md — 人審完才跑 render_cut.py,絕不代審。"
            f"({prosody_note})"),
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cutplan] 剪輯提案待對話 agent 接手: {rel(marker, PROJECT_ROOT)}")


def main():
    ap = argparse.ArgumentParser(description="Podcast cutplan 產生器(文字剪輯)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="SRT → cutplan.md/json + 提案 marker")
    p.add_argument("--session", required=True)
    p.add_argument("--merge-gap", type=float, default=0.0,
                   help="0(預設)=依原 SRT 短句一行一句(2026-07-28 MM 拍板);"
                        ">0 則同 speaker 間隔小於此秒數的句子併成一個 block")
    p.add_argument("--max-block", type=float, default=45.0)
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args)


if __name__ == "__main__":
    main()
