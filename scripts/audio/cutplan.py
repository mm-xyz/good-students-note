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
import math
import sys
import wave
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


def build_gaps(blocks: list[dict], min_gap: float = 2.0) -> list[dict]:
    """block 之間 ≥ min_gap 的空白(打板/笑聲/環境音)→ G 列,預設不勾=照舊剪掉。

    2026-07-29 MM 拍板:空白要看得見、可選擇保留;預設行為不變(render 本來就
    會丟掉 unit 之間的空白,G 列只是把隱形的東西攤出來)。"""
    gaps = []
    prev_end = 0.0
    for b in blocks:
        if b["start"] - prev_end >= min_gap:
            gaps.append({"start": round(prev_end, 3), "end": round(b["start"], 3),
                         "before": b["id"]})
        prev_end = b["end"]
    for i, g in enumerate(gaps, 1):
        g["id"] = f"G{i:04d}"
        g["keep"] = False
    return gaps


def refine_gaps(gaps: list[dict], wav_path: Path,
                burst_db: float = -40.0, min_burst: float = 0.12,
                merge_gap: float = 0.35, pad: float = 0.15) -> list[dict]:
    """讀 audio16k.wav 量每個 gap 的能量,把「有聲」的小段拆成獨立 G 列
    (2026-07-29 MM 拍板):勾選=只保留那聲笑/打板/音效,不連帶前後死空白;
    真靜音的 gap 維持單列標「靜音」。回傳重編號後的新 gaps。"""
    if not wav_path.exists():
        return gaps
    out = []
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n_total = wf.getnframes()
        win = int(0.03 * sr)
        for g in gaps:
            i0 = max(0, int(g["start"] * sr))
            i1 = min(n_total, int(g["end"] * sr))
            wf.setpos(i0)
            raw = wf.readframes(i1 - i0)
            dbs = []
            for w in range(max(1, (len(raw) // 2) // win)):
                seg = raw[w * win * 2:(w + 1) * win * 2]
                if len(seg) < 4:
                    dbs.append(-96.0)
                    continue
                acc = 0
                for i in range(0, len(seg), 2):
                    v = int.from_bytes(seg[i:i + 2], "little", signed=True)
                    acc += v * v
                rms = math.sqrt(acc / (len(seg) // 2)) / 32768
                dbs.append(20 * math.log10(rms) if rms > 0 else -96.0)
            # 連續有聲窗 → bursts(間隔 < merge_gap 合併,短於 min_burst 丟棄)
            bursts = []
            for w, db in enumerate(dbs):
                if db <= burst_db:
                    continue
                t0 = g["start"] + w * 0.03
                t1 = t0 + 0.03
                if bursts and t0 - bursts[-1][1] < merge_gap:
                    bursts[-1][1] = t1
                    bursts[-1][2] = max(bursts[-1][2], db)
                else:
                    bursts.append([t0, t1, db])
            bursts = [b for b in bursts if b[1] - b[0] >= min_burst]
            if not bursts:
                out.append({**g, "kind": "silence", "peak_db": round(max(dbs), 1)})
                continue
            for t0, t1, pk in bursts:
                out.append({"start": round(max(g["start"], t0 - pad), 3),
                            "end": round(min(g["end"], t1 + pad), 3),
                            "before": g["before"], "keep": False,
                            "kind": "sound", "peak_db": round(pk, 1)})
    for i, g in enumerate(out, 1):
        g["id"] = f"G{i:04d}"
    return out


def gap_line(g: dict) -> str:
    mark = "x" if g.get("keep") else " "
    dur = g["end"] - g["start"]
    if g.get("kind") == "sound":
        return (f"- [{mark}] {g['id']} [{fmt_mmss(g['start'])}–{fmt_mmss(g['end'])}] "
                f"🔊 聲音事件 {dur:.1f}s(峰值 {g['peak_db']:.0f}dB;笑/打板/音效?"
                f"勾選=保留這一小段)")
    note = "靜音" if g.get("kind") == "silence" else "打板/笑/環境音?"
    return (f"- [{mark}] {g['id']} [{fmt_mmss(g['start'])}–{fmt_mmss(g['end'])}] "
            f"⬜ 空白/非語音 {dur:.1f}s({note};勾選=保留原聲)")


def write_cutplan_md(blocks: list[dict], path: Path, slug: str, srt_name: str,
                     gaps: list[dict] | None = None) -> None:
    lines = [
        f"# Cutplan — {slug}",
        "",
        f"> 來源:{srt_name}。`- [x]` = 保留,`- [ ]` = 剪掉;**改勾選就是剪輯**。",
        "> **字級精剪**:保留的 block 內把贅字/贅句包進 `~~刪除線~~`,render 會用",
        "> word 級時間軸精準剪掉那幾個字(需 words.json;--asr local 自動產)。",
        "> 停頓不用標:render 自動把 >1.5s 的停頓收緊到 0.6s(--max-pause 可調)。",
        "> `G` 列=block 之間 ≥2s 的空白(打板/笑/環境音),預設不勾=照舊剪掉,",
        "> 勾選=保留該段原聲;G 列文字只是說明,可自由改。",
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
    gap_before: dict[str, list] = {}
    for g in (gaps or []):
        gap_before.setdefault(g["before"], []).append(g)
    for b in blocks:
        for g in gap_before.get(b["id"], []):
            lines.append(gap_line(g))
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
    gaps = build_gaps(blocks, args.min_gap)
    gaps = refine_gaps(gaps, session_dir / "audio16k.wav")

    cp_json = session_dir / "cutplan.json"
    cp_json.write_text(json.dumps({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_srt": srt_src.name,
        "merge_gap": args.merge_gap,
        "max_block": args.max_block,
        "blocks": blocks,
        "gaps": gaps,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    cp_md = session_dir / "cutplan.md"
    write_cutplan_md(blocks, cp_md, session_dir.name, srt_src.name, gaps)
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


def add_gaps(args):
    """對既有 session 補 G 列(冪等):不動任何既有行(含刪除線/勾選/章節)。"""
    session_dir = Path(args.session).resolve()
    cp_json = session_dir / "cutplan.json"
    cp_md = session_dir / "cutplan.md"
    data = json.loads(cp_json.read_text(encoding="utf-8"))
    if data.get("gaps"):
        print(f"[cutplan] cutplan.json 已有 {len(data['gaps'])} 個 gap,不重複加")
        return
    gaps = build_gaps(data["blocks"], args.min_gap)
    gaps = refine_gaps(gaps, session_dir / "audio16k.wav")
    data["gaps"] = gaps
    cp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    gap_before: dict[str, list] = {}
    for g in gaps:
        gap_before.setdefault(g["before"], []).append(g)
    out = []
    for line in cp_md.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        for bid, gs in gap_before.items():
            if s.startswith(f"- [x] {bid} ") or s.startswith(f"- [ ] {bid} "):
                out.extend(gap_line(g) for g in gs)
                break
        out.append(line)
    cp_md.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[cutplan] 補 {len(gaps)} 個 G 列(≥{args.min_gap}s 空白)→ "
          f"{rel(cp_md, PROJECT_ROOT)}")


def main():
    ap = argparse.ArgumentParser(description="Podcast cutplan 產生器(文字剪輯)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="SRT → cutplan.md/json + 提案 marker")
    p.add_argument("--session", required=True)
    p.add_argument("--merge-gap", type=float, default=0.0,
                   help="0(預設)=依原 SRT 短句一行一句(2026-07-28 MM 拍板);"
                        ">0 則同 speaker 間隔小於此秒數的句子併成一個 block")
    p.add_argument("--max-block", type=float, default=45.0)
    p.add_argument("--min-gap", type=float, default=2.0,
                   help="block 間空白 ≥ 此秒數列成 G 列(預設 2.0)")
    g = sub.add_parser("add-gaps", help="對既有 cutplan 補 G 空白列(冪等)")
    g.add_argument("--session", required=True)
    g.add_argument("--min-gap", type=float, default=2.0)
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args)
    elif args.cmd == "add-gaps":
        add_gaps(args)


if __name__ == "__main__":
    main()
