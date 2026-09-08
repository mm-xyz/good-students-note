#!/usr/bin/env python3
"""
audio_to_md.py  v1.2.0 ── audio-to-md｜Phase 1：本地把「影音的聲音」轉成逐字稿骨架
─────────────────────────────────────────────────────────────────────────────
把音訊或影片（直接讀，自動抽音軌）用**本地 Whisper** 轉成一份帶時間戳的
Markdown 逐字稿骨架——留好「校稿、段落摘要、重點」空格，交給 Claude（Phase 2）填。

設計哲學（沿襲 doc-to-md / vlm-to-md 家族）＋ 指揮 AI 的分工：
  • 「聽打」這種機械重活 → 本地 Whisper（免費、離線、0 token、0 API key）
  • 「校稿、理解、抓重點、寫摘要」→ 交給 Claude（Phase 2，花少量 token）
  把對的工序派給對的工具，就是最省 token 的 AI 指揮。

引擎：faster-whisper（跨平台、CPU 友善、內含 PyAV 解碼，不需系統 ffmpeg）。
與家族分工：
  • doc-to-md → 文字檔的文字 ｜ vlm-to-md → 圖的視覺 ｜ audio-to-md → 影音的聲音
─────────────────────────────────────────────────────────────────────────────
"""
import argparse
import datetime
import json
import os
import re
import sys

# === Windows 中文輸出修正（v: cp1252 fix）===
# Windows 預設 stdout 編碼為 cp1252，輸出中文（argparse --help / log 進度）會 UnicodeEncodeError。
# 強制 stdout/stderr 改 UTF-8（被導向 >/dev/null 或檔案時尤其重要）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from faster_whisper import WhisperModel
    HAS_FW = True
except ImportError:
    HAS_FW = False

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma", ".aiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mts", ".m2ts", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".m4v"}

DEFAULT_MODEL = "large-v3-turbo"   # 對應使用者要的 turbo；弱機可改 small / base
DEFAULT_CHUNK_MIN = 6              # 逐字稿依時間切段，每段約 N 分鐘 + 一個段落摘要空格
LOWCONF_THRESHOLD = -0.8          # 片段 avg_logprob 低於此值 → 標 🔸（模型較不確定，Phase 2 優先複核）
DEFAULT_SPLIT_MIN = 35            # 時長超過 N 分鐘 → 自動切成多個短檔（避免長稿被 AI 摘要）；0 = 不分檔
DEFAULT_PART_MIN = 18             # 分檔時每檔約 N 分鐘（會對齊到 chunk-min 的倍數）


def log(msg: str):
    print(msg, flush=True)


def safe_stem(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[^\w一-鿿\-]+", "_", stem).strip("_")
    return stem or "audio"


def fmt_ts(sec: float, with_hour: bool) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if with_hour else f"{m:02d}:{s:02d}"


PLACEHOLDER_SUMMARY = (
    "> [!note] 段落摘要\n"
    "> **摘要**：（Claude 將填入這段的 2-3 句重點）\n"
    "> **關鍵字**：（Claude 將填入 4-8 個檢索關鍵字）"
)


def build_scaffold(stem: str, source: str, model: str, language: str,
                   duration: float, segments: list, chunk_min: int,
                   lowconf_threshold: float = LOWCONF_THRESHOLD,
                   part_info: dict = None) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with_hour = duration >= 3600
    dur_str = fmt_ts(duration, duration >= 3600)
    n_words = sum(len(s["text"]) for s in segments)

    yaml = (
        "---\n"
        f'title: "{stem.replace("_", " ")}"\n'
        f'source: "{source}"\n'
        "type: AV-transcript-pending\n"
        f"model: faster-whisper / {model}\n"
        f"language: {language}\n"
        f"duration: \"{dur_str}\"\n"
        f"segments: {len(segments)}\n"
        f'generated_at: "{now}"\n'
        "lang_out: zh-TW\n"
        "---\n\n"
    )

    head = f"# {stem.replace('_', ' ')}｜影音逐字稿知識庫\n\n"
    if part_info:
        head += (
            f"> [!important] 📄 長稿分檔：第 {part_info['idx']} / {part_info['total']} 部分"
            f"（{part_info['start']}–{part_info['end']}）\n"
            f"> **只校「這一個檔」**，校完換下一個 part；最後依 part 順序首尾相接＝完整稿。"
            f"做法見 `{part_info['guide']}`。\n\n"
        )
    head += (
        "> [!danger] 校稿鐵律：逐字主體零刪改、禁摘要（最重要，先讀）\n"
        "> 這份逐字稿可能很長。Phase 2 **只做校稿、不做摘要**——\n"
        "> 1. **逐字主體 100% 保留**：每一個 `**[時間戳]**` 行都必須留著，內容一字不刪、不合併、不改寫、不潤飾成書面語。校稿能動的只有：簡體→繁體、錯字、標點、合理斷句、（多人時）句首標講者。\n"
        "> 2. **禁止用摘要代替逐字**：不可以把一段話濃縮成幾句「代替」原文。摘要只能**額外**寫進每段的「段落摘要」欄，原始逐字照樣全留。\n"
        "> 3. **必須分段處理（否則會自動截斷成摘要）**：一次只處理一個 `## ⏱ 時間段`、用 Edit 逐段填，**禁止一次重寫全文**——長稿單趟輸出會撞長度上限，AI 就會偷偷改用摘要、掉時間戳。\n"
        "> 4. **完工自檢（雙指標）**：(a) 校稿後 `**[` 行數必須 **≥ 原稿、且 ≥ 本檔開頭 YAML 的 `segments` 值**（只會因斷句變多、不該變少）；(b) 校稿後中文字元數必須 **≥ 原稿 ×0.95**（簡繁／標點只增不減）。任一段字數掉 >10%＝摘要了，重做該段。\n"
        "> 5. **一次只輸出一段**：用 Edit 逐段填，同一次回覆只能涵蓋一個 `## ⏱ 時間段`；嚴禁同次塞多段（多段＝撞上限＝被截成摘要）。斷句＝只加標點換行、不增刪換任何一字（語助詞、重複、口頭禪全留）。\n"
        "> 6. **不覆寫原檔**：校稿存到新檔 `_校稿.md`，保留原始骨架供自檢對拍。\n\n"
    )
    head += (
        "> [!info] 這份還沒完成——本地 Whisper 已把「聲音」轉成字，等 Claude 來「理解」\n"
        "> Phase 1（本地、0 token）已完成轉錄。請在 Claude 裡讓它做 Phase 2：\n"
        "> 1) **校稿**：簡體→繁體（台灣用語）、修錯字、加標點、合理斷句（**不要改變語意**）。\n"
        "> 2) 把每段的 `段落摘要` 空格填好。3) 最後補「全篇重點 / 待辦 / 金句」。\n"
        "> 這一步花的 token 很少——重活（聽打）本地免費做掉了。\n\n"
    )
    head += (
        "> [!tip] 兩層校稿（語感層 + 專名查證層）\n"
        "> **Layer 1 語感校稿（鐵律不變）**：簡繁轉換、錯字、標點、斷句、（多人時）標講者。**禁止**憑空改寫或新增講者沒說的內容；聽不清標「（聽不清）」。\n"
        "> **Layer 2 專名查證（解決「自信地把生僻專名聽成同音常見詞」，如 企業重心→勤業眾信、Tina Selig→Seelig）**：\n"
        "> 用**語法角色**而非信心分數篩候選——句子主語且後接需行為者的動詞／後接稱謂類別詞（XX 教授・公司・遊戲・書）／前有「叫做」「就是」引介／你對其真實性沒高把握的人名地名品牌書名機構／標了 🔸 的片段。\n"
        "> 篩出候選（通常 5-20 個）逐一 **WebSearch 查證**：查到更可信版本→修正並**留痕**`（查證：原「企業重心」→「勤業眾信」）`；查不到→保留原字並標 `⚠️ 專名待查證`；無 WebSearch 可用→全部標 `⚠️ 專名待查證`，**不要憑空猜改**。修正必須有外部依據＋留痕。\n"
        "> 🔸 = Phase 1 模型對該片段信心較低（avg_logprob 偏低），請**優先複核**。\n\n"
    )

    # 依時間切段
    body = ""
    chunk_sec = chunk_min * 60
    cur_chunk = -1
    for s in segments:
        idx = int(s["start"] // chunk_sec)
        if idx != cur_chunk:
            cur_chunk = idx
            cstart = fmt_ts(idx * chunk_sec, with_hour)
            cend = fmt_ts(min((idx + 1) * chunk_sec, duration), with_hour)
            body += f"\n## ⏱ {cstart}–{cend}\n\n"
            body += PLACEHOLDER_SUMMARY + "\n\n"
            body += "<!-- 原始逐字：逐行校稿，禁止跨行合併、禁止刪句、禁止跨時間戳搬移文字；每個 **[時間戳]** 行獨立保留 -->\n\n"
        lp = s.get("avg_logprob")
        mark = "🔸" if (lp is not None and lp < lowconf_threshold) else ""
        body += f"**[{fmt_ts(s['start'], with_hour)}]** {mark}{s['text'].strip()}\n\n"

    tail = (
        "\n---\n\n"
        "## 📌 全篇重點（Claude 填）\n\n"
        "> [!note] 重點 / 待辦 / 金句\n"
        "> **3-5 個重點**：（Claude 將填入）\n"
        "> **待辦或行動項**：（若有）\n"
        "> **可摘金句**：（1-3 句）\n"
    )
    return yaml + head + body + tail


def build_guide(stem: str, duration: float, written: list) -> str:
    """長稿分檔後，給學員的純文字操作說明（一檔一貼、最後串接）。"""
    dur_min = int(round(duration / 60))
    lines = [
        "【長稿分檔校稿說明】",
        "",
        f"你的錄音較長（約 {dur_min} 分鐘），已自動切成 {len(written)} 個短檔，",
        "目的是避免「太長 → AI 自行摘要、掉時間戳、內文被改寫」。",
        "",
        "檔案清單（依順序）：",
    ]
    for name, _ns, ps, pe in written:
        lines.append(f"  {name}　（{ps}–{pe}）")
    lines += [
        "",
        "怎麼做（重點：一個檔一次，不要一次貼多個 part）：",
        "  1. 把 part1 檔丟回 Claude，說：",
        "     「幫我校稿這份逐字稿，逐字不要摘要、保留每個時間戳：<part1 路徑>」",
        "  2. 校好後存成 part1 的 _校稿.md。",
        "  3. 換 part2，重複步驟 1–2……直到所有 part 都校完。",
        "  4. 最後把各 part 的校稿版「依 part 順序」首尾接起來＝完整逐字稿。",
        "     （時間戳是連續的，直接相接即可。）",
        "",
        "為什麼要分檔：",
        "  長稿一次塞進一則回覆會超過長度上限，AI 會偷偷改用「摘要」交差，",
        "  於是時間戳掉了、整段被壓縮。切成短檔後，每檔都短到能一次完整校完，",
        "  逐字稿就不會被壓縮——這是架構上的解法，不是靠提示詞硬撐。",
    ]
    return "\n".join(lines) + "\n"


def transcribe(input_path: str, model: str, language: str, device: str,
               compute_type: str, beam_size: int) -> tuple:
    log(f"🎧 載入 Whisper 模型：{model}（device={device}, compute={compute_type}）")
    log("   （第一次使用會自動下載模型，turbo 約 1.5GB，需幾分鐘）")
    if any(m in model.lower() for m in ("tiny", "base", "small")):
        log("⚠ 你指定了較小的模型——中文品質會明顯變差、連校稿都難救回。"
            "強烈建議改用 large-v3-turbo（本工具品質底線）。")
    wm = WhisperModel(model, device=device, compute_type=compute_type)
    log(f"🗣️  開始轉錄：{os.path.basename(input_path)}")
    seg_iter, info = wm.transcribe(
        input_path,
        language=(None if language in ("auto", "", None) else language),
        vad_filter=True,
        beam_size=beam_size,
    )
    segments = []
    for seg in seg_iter:   # 這個迴圈才真正執行轉錄
        segments.append({"start": seg.start, "end": seg.end, "text": seg.text,
                         "avg_logprob": getattr(seg, "avg_logprob", None)})
        if len(segments) % 25 == 0:
            log(f"   …已轉錄到 {fmt_ts(seg.end, info.duration >= 3600)}")
    return segments, info


def process(input_path: str, out_dir: str, model: str, language: str,
            device: str, compute_type: str, beam_size: int, chunk_min: int,
            lowconf_threshold: float = LOWCONF_THRESHOLD,
            split_min: int = DEFAULT_SPLIT_MIN, part_min: int = DEFAULT_PART_MIN) -> int:
    if not os.path.exists(input_path):
        log(f"❌ 找不到輸入：{input_path}")
        return 1
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in AUDIO_EXTS and ext not in VIDEO_EXTS:
        log(f"❌ 不支援的格式：{ext}。支援音訊 {sorted(AUDIO_EXTS)} / 影片 {sorted(VIDEO_EXTS)}")
        return 1
    if not HAS_FW:
        log("❌ 需要 faster-whisper：pip install -r scripts/requirements.txt")
        return 1

    os.makedirs(out_dir, exist_ok=True)
    kind = "影片" if ext in VIDEO_EXTS else "音訊"
    log(f"🎬 輸入：{os.path.basename(input_path)}（{kind}；影片會自動抽音軌）")

    segments, info = transcribe(input_path, model, language, device, compute_type, beam_size)
    if not segments:
        log("❌ 沒有轉錄出任何內容（檔案可能沒有語音或全是靜音）。")
        return 1

    stem = safe_stem(input_path)
    lang = getattr(info, "language", language) or "auto"
    duration = float(getattr(info, "duration", segments[-1]["end"]))
    n_low = sum(1 for s in segments if (s.get("avg_logprob") is not None and s["avg_logprob"] < lowconf_threshold))
    with_hour = duration >= 3600
    src = os.path.basename(input_path)

    def _write(md_text: str, fname: str) -> str:
        p = os.path.join(out_dir, fname)
        with open(p, "w", encoding="utf-8") as f:
            f.write(md_text)
        return p

    # 長稿自動分檔：把「隔離」放在 Phase 1 輸出。每個短檔都能在一次校稿裡完整吐完，
    # 不會撞輸出上限→被 AI 摘要；Desktop 也適用（不需 Claude Code / subagent / API）。
    part_chunks = max(1, round(part_min / chunk_min)) if part_min > 0 else 1
    part_sec = part_chunks * chunk_min * 60
    do_split = split_min > 0 and duration > split_min * 60

    written = []   # [(檔名, 段數, 起, 迄)]
    if do_split:
        parts = {}
        for s in segments:
            parts.setdefault(int(s["start"] // part_sec), []).append(s)
        keys = sorted(parts)
        n = len(keys)
        for i, k in enumerate(keys, 1):
            pseg = parts[k]
            pstart = fmt_ts(pseg[0]["start"], with_hour)
            pend = fmt_ts(pseg[-1]["end"], with_hour)
            part_info = {"idx": i, "total": n, "start": pstart, "end": pend,
                         "guide": f"{stem}_長稿校稿說明.txt"}
            md = build_scaffold(stem, src, model, lang, duration, pseg, chunk_min,
                                lowconf_threshold, part_info)
            name = f"{stem}_逐字稿知識庫_part{i}of{n}.md"
            _write(md, name)
            written.append((name, len(pseg), pstart, pend))
        _write(build_guide(stem, duration, written), f"{stem}_長稿校稿說明.txt")
    else:
        md = build_scaffold(stem, src, model, lang, duration, segments, chunk_min, lowconf_threshold)
        name = f"{stem}_逐字稿知識庫.md"
        _write(md, name)
        written.append((name, len(segments), fmt_ts(0, with_hour), fmt_ts(duration, with_hour)))

    manifest = {
        "source": input_path, "kind": kind, "model": model, "language": lang,
        "duration_sec": round(duration, 1), "segments": len(segments),
        "split": do_split, "parts": [w[0] for w in written],
        "generated_at": datetime.datetime.now().isoformat(),
    }
    with open(os.path.join(out_dir, f"{stem}_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    log("")
    log("✅ Phase 1 完成（本地、0 token）")
    log(f"   • 時長：{fmt_ts(duration, with_hour)}　語言：{lang}　段落：{len(segments)}　🔸低信心：{n_low}")
    if do_split:
        log(f"   • 長稿已自動切成 {len(written)} 個短檔（每檔約 {part_chunks * chunk_min} 分鐘），避免被 AI 摘要：")
        for name, ns, ps, pe in written:
            log(f"     - {name}（{ps}–{pe}，{ns} 段）")
        log(f"   • 校稿說明：{stem}_長稿校稿說明.txt")
        log("")
        log("👉 下一步（Phase 2）：一個 part 檔丟回 Claude 校稿一次，校完換下一個，最後依序接起來。")
        log('   對每個檔說：「幫我校稿這份逐字稿，逐字不要摘要、保留每個時間戳：<part 路徑>」')
    else:
        out_path = os.path.join(out_dir, written[0][0])
        log(f"   • 逐字稿骨架：{out_path}")
        log("")
        log("👉 下一步（Phase 2，花少量 token）：在 Claude 裡說")
        log(f'   「幫我校稿並完成這份逐字稿知識庫（逐字不要摘要、保留時間戳）：{out_path}」')
    log(f"[DONE] Phase 1 done. output dir: {out_dir}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="audio-to-md Phase 1：本地 Whisper 把影音轉成逐字稿 Markdown 骨架（免費、0 token、0 API key）")
    ap.add_argument("input", help="音訊或影片檔（mp3/m4a/wav/mp4/mov/mts… 影片會自動抽音軌）")
    ap.add_argument("-o", "--output", default=None, help="輸出資料夾（預設：輸入檔所在目錄）")
    ap.add_argument("--auto", action="store_true", help="便捷旗標（與家族一致）")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"Whisper 模型（建議固定用預設 {DEFAULT_MODEL}＝中文品質底線；不建議降級成 small/base）")
    ap.add_argument("--language", default="auto", help="語言代碼（預設 auto 自動偵測；中文可指定 zh）")
    ap.add_argument("--device", default="cpu", help="cpu（預設、跨平台）或 cuda")
    ap.add_argument("--compute-type", default="int8", help="int8（預設、省記憶體）/ int8_float16 / float16")
    ap.add_argument("--beam-size", type=int, default=5, help="beam size（預設 5；想更快可設 1）")
    ap.add_argument("--chunk-min", type=int, default=DEFAULT_CHUNK_MIN,
                    help=f"逐字稿依時間切段，每段約 N 分鐘（預設 {DEFAULT_CHUNK_MIN}）")
    ap.add_argument("--lowconf-threshold", type=float, default=LOWCONF_THRESHOLD,
                    help=f"avg_logprob 低於此值的片段標 🔸 供 Phase 2 優先複核（預設 {LOWCONF_THRESHOLD}）")
    ap.add_argument("--split-min", type=int, default=DEFAULT_SPLIT_MIN,
                    help=f"時長超過 N 分鐘 → 自動切成多個短檔避免長稿被 AI 摘要（預設 {DEFAULT_SPLIT_MIN}；0=不分檔）")
    ap.add_argument("--part-min", type=int, default=DEFAULT_PART_MIN,
                    help=f"分檔時每檔約 N 分鐘（會對齊到 chunk-min 倍數；預設 {DEFAULT_PART_MIN}）")
    args = ap.parse_args()

    out_dir = args.output or os.path.dirname(os.path.abspath(args.input))
    sys.exit(process(args.input, out_dir, args.model, args.language,
                     args.device, args.compute_type, args.beam_size, args.chunk_min,
                     args.lowconf_threshold, args.split_min, args.part_min))


if __name__ == "__main__":
    main()
