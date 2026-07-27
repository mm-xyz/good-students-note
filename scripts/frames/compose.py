#!/usr/bin/env python3
"""frames Stage 3 — compose：SRT ＋ 審過的幀 → Obsidian 逐字稿與筆記骨架。

（2026-07-28 自 invisible-context 併入，吸收音訊線圖層：
 prosody.json 有真實聲學停頓就取代 SRT-gap proxy；高昂段落標 🔥；
 SRT 用 transcript.speakers.srt 時段落自帶講者標籤。）

用法：
  python3 scripts/frames/compose.py <slug> --course "2026_AI訂閱年會小聚" [--folder 名稱]

產出（OUTPUT_ROOT/<course>/<folder>/）：
  <folder>_逐字稿.md   時間錨點段落＋插圖＋停頓標注＋🔥高昂標注
  <folder>_筆記.md     骨架（ref 回逐字稿與關鍵畫面，內容留給 Claude/人）
  attachments/         保留幀（檔名帶 slug 前綴，避免 Obsidian 全域 wikilink 撞名）
"""
import argparse
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (SESSIONS_DIR, frames_workdir, fmt_ts, load_config,
                    load_manifest, parse_srt)


def build_paragraphs(cues: list[dict], para_gap: float, max_chars: int = 220) -> list[dict]:
    """cues → [{start, end, text}] 段落；靠時間縫隙與長度斷段。"""
    paras = []
    cur = None
    for c in cues:
        if cur and (c["start"] - cur["end"] >= para_gap or len(cur["text"]) >= max_chars):
            paras.append(cur)
            cur = None
        if cur is None:
            cur = {"start": c["start"], "end": c["end"], "text": c["text"]}
        else:
            cur["text"] += c["text"] if re.match(r"^[，。？！、,.?!]", c["text"]) else " " + c["text"]
            cur["end"] = c["end"]
    if cur:
        paras.append(cur)
    return paras


def find_pauses(cues: list[dict], pause_min: float) -> list[dict]:
    out = []
    for a, b in zip(cues, cues[1:]):
        gap = b["start"] - a["end"]
        if gap >= pause_min:
            out.append({"t": a["end"], "gap": round(gap, 1)})
    return out


def sanitize_name(text: str) -> str:
    return re.sub(r'[/\\:*?"<>|#^\[\]｜]+', " ", text).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--course", required=True, help="課程資料夾名，如 2026_AI訂閱年會小聚")
    ap.add_argument("--folder", default=None, help="這場的資料夾名，預設 <speaker>_<title>")
    ap.add_argument("--force-note", action="store_true",
                    help="筆記已填內容時仍強制覆寫（預設跳過保護）")
    args = ap.parse_args()

    cfg = load_config()
    m = load_manifest(args.slug)
    if not m.get("srt"):
        sys.exit("manifest 沒有 SRT，先補逐字稿再 compose")
    cues = parse_srt(Path(m["srt"]))
    if not cues:
        sys.exit(f"SRT 解析不出任何字幕：{m['srt']}")

    kept = [f for f in m["frames"] if f.get("screen", {}).get("keep")]
    unscreened = [f for f in m["frames"] if "screen" not in f and "screen_error" not in f]
    if unscreened:
        print(f"⚠️ 還有 {len(unscreened)} 張沒過 screen，本次不插入它們")

    folder_name = sanitize_name(
        args.folder or f"{m['speaker'] + '_' if m.get('speaker') else ''}{m['title']}"
    )
    out_dir = Path(cfg["OUTPUT_ROOT"]).expanduser() / args.course / folder_name
    att_dir = out_dir / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    # 保留幀複製進 vault，slug 前綴防撞名
    for f in kept:
        src = frames_workdir(args.slug) / f["file"]
        f["vault_file"] = f"{args.slug}_{Path(f['file']).name}"
        shutil.copy2(src, att_dir / f["vault_file"])

    paras = build_paragraphs(cues, float(cfg["PARA_GAP_SEC"]))

    # 停頓:prosody.json 有真實聲學靜音就用它(音訊線產物),否則退回 SRT gap proxy
    pause_min = float(cfg["PAUSE_MIN_SEC"])
    prosody_path = SESSIONS_DIR / args.slug / "prosody.json"
    hot = []  # 高昂段(excitement ≥ 75)
    if prosody_path.exists():
        pj = json.loads(prosody_path.read_text(encoding="utf-8"))
        pauses = [{"t": s["start"], "gap": round(s["end"] - s["start"], 1)}
                  for s in pj.get("silences", [])
                  if s["end"] - s["start"] >= pause_min]
        hot = [s for s in pj.get("segments", []) if s.get("excitement", 0) >= 75]
        print(f"（prosody:真實靜音 {len(pauses)} 處、🔥 高昂段 {len(hot)} 段）")
    else:
        pauses = find_pauses(cues, pause_min)

    # 組逐字稿：段落、插圖、停頓標注全照時間軸合流
    events = [{"t": p["start"], "kind": "para", "p": p} for p in paras]
    events += [{"t": f["t"], "kind": "frame", "f": f} for f in kept]
    events += [{"t": p["t"], "kind": "pause", "p": p} for p in pauses]
    order = {"frame": 0, "pause": 1, "para": 2}  # 同時間點：圖先、停頓次、文字後
    events.sort(key=lambda e: (e["t"], order[e["kind"]]))

    title = m["title"]
    speaker = m.get("speaker") or ""
    lines = [
        "---",
        f'title: "{title}"',
        f'speaker: "{speaker}"',
        f"created: {date.today().isoformat()}",
        f"duration: {fmt_ts(m['duration'])}",
        f'source_video: "{m["video"]}"',
        f'source_srt: "{m["srt"]}"',
        "tool: good-students-note",
        "---",
        "",
        f"# {title} — 逐字稿",
        "",
        f"> 講者：{speaker or '—'}｜片長 {fmt_ts(m['duration'])}｜"
        f"插圖 {len(kept)} 張｜停頓標注 {len(pauses)} 處",
        "",
    ]
    for e in events:
        if e["kind"] == "para":
            p = e["p"]
            fire = next((h for h in hot
                         if h["start"] < p["end"] and h["end"] > p["start"]), None)
            mark = f" 🔥{fire['excitement']:.0f}" if fire else ""
            lines += [f"**[{fmt_ts(p['start'])}]**{mark} {p['text']}", ""]
        elif e["kind"] == "frame":
            f = e["f"]
            s = f["screen"]
            cap = s["caption"] or s["kind"]
            lines += [f"![[{f['vault_file']}]]",
                      f"> [!note] 🖼 [{fmt_ts(f['t'])}] {cap}（{s['kind']}）"]
            for qr in s.get("qr") or []:
                lines += [f"> 🔗 QR：{qr}"]
            text = s.get("text", "")
            if "```" in text:  # mermaid 等 fenced block 放 callout 外面才會渲染
                head, _, fenced = text.partition("```")
                lines += [f"> {ln}" for ln in head.splitlines() if ln.strip()]
                lines += ["", "```" + fenced]
            elif text:  # 畫面文字逐字抄錄（vlm-to-md 精神：圖上的資訊也要進 md、可檢索）
                lines += [f"> {ln}" if ln.strip() else ">"
                          for ln in text.splitlines()]
            lines += [""]
        else:
            p = e["p"]
            lines += [f"> ⏸ [{fmt_ts(p['t'])}] 停頓 {p['gap']} 秒", ""]

    transcript_name = f"{folder_name}_逐字稿"
    (out_dir / f"{transcript_name}.md").write_text("\n".join(lines), encoding="utf-8")

    note_path = out_dir / f"{folder_name}_筆記.md"
    if note_path.exists() and "（待寫）" not in note_path.read_text(encoding="utf-8"):
        if not args.force_note:
            print(f"✅ {out_dir}")
            print(f"   {transcript_name}.md（段落 {len(paras)}、插圖 {len(kept)}、停頓 {len(pauses)}）")
            print(f"   筆記已有內容，跳過不覆寫（要重生用 --force-note）")
            return

    note_lines = [
        "---",
        f'title: "{title}（筆記）"',
        f'speaker: "{speaker}"',
        f"created: {date.today().isoformat()}",
        "tool: good-students-note",
        "---",
        "",
        f"# {title} — 筆記",
        "",
        "> [!info] Ref source",
        f"> - 逐字稿（含畫面與停頓標注）：[[{transcript_name}]]",
        f"> - 影片原檔：`{m['video']}`",
        "",
        "## TL;DR",
        "",
        "（待寫）",
        "",
        "## 重點筆記",
        "",
        "（待寫——從 [[" + transcript_name + "]] 蒸餾）",
        "",
        "## 金句／可剪片段候選",
        "",
        "（待寫：起訖時間碼＋一句為什麼值得剪）",
        "",
        "## 關鍵畫面",
        "",
    ]
    for f in kept:
        s = f["screen"]
        note_lines += [f"![[{f['vault_file']}]]",
                       f"> [{fmt_ts(f['t'])}] {s['caption'] or s['kind']}"
                       + (f" — {s['text']}" if s["text"] else ""), ""]
    note_path.write_text("\n".join(note_lines), encoding="utf-8")

    print(f"✅ {out_dir}")
    print(f"   {transcript_name}.md（段落 {len(paras)}、插圖 {len(kept)}、停頓 {len(pauses)}）")
    print(f"   {folder_name}_筆記.md（骨架＋關鍵畫面 gallery）")
    print(f"   attachments/ {len(kept)} 張")


if __name__ == "__main__":
    main()
