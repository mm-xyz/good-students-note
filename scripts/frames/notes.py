#!/usr/bin/env python3
"""frames Stage 4 — notes：本地 LM Studio map-reduce 筆記蒸餾（零雲端 token）。

取代原本「Claude 雲端 subagent 讀逐字稿寫筆記」的收尾（#557，MM 2026-07-27
拍板全管線不吃雲端 token）。硬體約束：gemma-4-26b-a4b-qat 載入 15.6GB、
context 只能 4096 — 所以走 map-reduce：

  map    SRT cues 沿時間錨點切段（prompt＋輸入＋輸出估算 ≤ context），
         每段抽重點候選＋金句候選（帶時間碼）
  filter 機械防幻覺：時間碼須落在 SRT 總時長內、金句去空白後 grep 得回
         SRT 全文（比照 render_cut.validate_program 的 flat-text 作法）
  reduce outline（分小節＋TL;DR＋挑金句）→ 逐小節寫內文；候選清單超過
         context 就對半分治再合併 TL;DR
  gate   產出前跑機械驗收三件（時間碼界內／金句 grep／格式 lint），
         不過自動重試（預設 2 輪、--retries 可調），重試耗盡就明確失敗

輸出格式逐字鎖死同現有五場筆記（YAML frontmatter → # 題名 — 筆記 →
> [!info] Ref source → ## TL;DR → ## 重點筆記（### 小節 [起–訖]）→
## 金句／可剪片段候選 表格）。品質預期 MM 已在卡上接受：TL;DR 較乾、
「為什麼值得剪」可留短，不算缺陷。

用法：
  python3 scripts/frames/notes.py <slug> [--retries 2] [--out PATH] [--force] [--unload]

SRT 選檔沿用 extract.py 順序：transcript.speakers.srt > cleaned.srt >
transcript.srt；session 目錄都沒有時退回 frames/manifest.json 的 srt 路徑
（幀線 session 的 SRT 常住影片旁）。LLM 只打 LM_STUDIO_URL（localhost:1234，
OpenAI 兼容）；token 讀 mars-cc/.env（跨專案慣例，見 common.load_config）。
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (SESSIONS_DIR, extract_json_from_message, fmt_ts, llm_chat,
                    load_config, manifest_path, parse_srt)

SRT_CANDIDATES = ("transcript.speakers.srt", "cleaned.srt", "transcript.srt")
DEFAULT_CONTEXT = 4096   # 卡上實測：24GB RAM 下 KV cache 只能撐 4096
OUTPUT_RESERVE = 1500    # 正常 context 下留給推理＋輸出的額度
OUTPUT_RESERVE_MIN = 200  # context 很小（測試）時至少保留這麼多


class NoteGenError(Exception):
    """筆記生成失敗（候選抽不出來 / 機械驗收重試耗盡）。"""


# ── Prompts（頭部為常數，供測試分流辨識）────────────────────────────

MAP_PROMPT = """你在讀一場繁體中文節目/演講逐字稿的其中一段，每行格式「[時間碼] 內容」。
任務：
1. points：抽 2-4 條這段的重點候選。每條 = {"start":"M:SS","end":"M:SS","point":"一句話重點（40字內，保留具體案例/數字）"}，時間碼必須取自本段出現過的時間碼。
2. quotes：抽 0-2 條金句候選（最有記憶點、可獨立成立的句子）。每條 = {"start":"M:SS","end":"M:SS","text":"逐字照抄原文，不可改字、不可翻譯、不可摘要（可用……串接鄰近兩句）","why":"一句為什麼值得剪（25字內）"}。講者標籤如 [Mars] 不要抄進 text。
只回傳 JSON，不要其他文字，格式：
{"points": [...], "quotes": [...]}

逐字稿段落：
"""

OUTLINE_PROMPT = """以下是整場逐字稿逐段蒸餾出的重點候選（P 編號）與金句候選（Q 編號），各帶 [起–訖] 時間碼、依時間排序。
任務：
1. sections：把重點候選依時間順序分組成 4-8 個小節（每小節＝連續時間段），每小節 = {"title":"小節標題（15字內）","point_ids":["P1",...]}。point_id 必須存在於清單，不可發明。
2. tldr：整場摘要一段（120-200 字，繁體中文，具體不空泛，保留最有記憶點的細節）。
3. quote_ids：從金句候選挑最多 8 條值得進筆記的（保留時間順序）。
只回傳 JSON，不要其他文字，格式：
{"tldr":"...","sections":[{"title":"...","point_ids":[...]}],"quote_ids":[...]}

候選清單：
"""

BODY_PROMPT = "你在為演講筆記的小節「"

BODY_PROMPT_TAIL = """」寫內文。材料＝這個小節的重點候選（帶時間碼）。
用繁體中文寫 80-200 字連貫段落：保留講者原始邏輯與具體案例/數字，不空泛化；不要寫時間碼；不要提「候選」「本段」這類字眼。
只回傳 JSON，不要其他文字，格式：{"body":"..."}

重點候選：
"""

TLDR_PROMPT = """以下是一場演講筆記的各小節標題與重點。寫一段 120-200 字的繁體中文 TL;DR，具體不空泛。
只回傳 JSON，不要其他文字，格式：{"tldr":"..."}

小節與重點：
"""


def _noop(*_args, **_kw):
    pass


# ── 基礎工具 ─────────────────────────────────────────────────────────

_SPK = re.compile(r"\[[^\[\]\n]{1,20}\]")  # [Mars] 講者標籤 / [1:18] 時間碼
_ELL = re.compile(r"…+|⋯+|\.{3,}")


def strip_speakers(text: str) -> str:
    return _SPK.sub("", text)


def normalize(text: str) -> str:
    """去講者標籤＋去空白 — 金句 grep 的統一正規化（兩側都過這個）。"""
    return re.sub(r"\s+", "", strip_speakers(text))


def estimate_tokens(text: str) -> int:
    """粗估 token：CJK ≈ 1 字/token、其餘 ≈ 4 字/token（偏保守）。"""
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    return cjk + (len(text) - cjk + 3) // 4


def parse_mmss(v) -> float:
    """'M:SS' / 'H:MM:SS' / 數字 → 秒。看不懂就 ValueError（候選過濾用）。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    if ":" in s:
        parts = s.split(":")
        if not 2 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
            raise ValueError(f"看不懂的時間碼：{v!r}")
        nums = [int(p) for p in parts]
        if len(nums) == 2:
            return float(nums[0] * 60 + nums[1])
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"看不懂的時間碼：{v!r}") from None


def ts_range(a: float, b: float) -> str:
    return f"{fmt_ts(a)}–{fmt_ts(b)}"


def quote_fragments(cell_text: str) -> list[str]:
    """金句 cell → 待 grep 的正規化片段。「」內優先；……切開逐段驗。"""
    spans = re.findall(r"「([^「」]+)」", cell_text) or [cell_text]
    frags = []
    for span in spans:
        for f in _ELL.split(span):
            f = normalize(f)
            if len(f) >= 4:  # 太短的片段（掰掰/語助詞）驗不出東西，跳過
                frags.append(f)
    return frags


def pick_srt(session_dir: Path) -> Path | None:
    """SRT 選檔沿用 extract.py 順序。"""
    return next((session_dir / n for n in SRT_CANDIDATES
                 if (session_dir / n).exists()), None)


# ── Map：切段＋抽候選 ────────────────────────────────────────────────

def render_cue_line(cue: dict) -> str:
    return f"[{fmt_ts(cue['start'])}] {cue['text']}"


def chunk_cues(cues: list[dict], budget: int) -> list[dict]:
    """cues 沿時間錨點切段，每段文字估算 ≤ budget tokens。"""
    chunks, cur, cur_tok = [], [], 0

    def flush():
        if cur:
            chunks.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                           "text": "\n".join(render_cue_line(c) for c in cur)})

    for c in cues:
        t = estimate_tokens(render_cue_line(c)) + 1
        if cur and cur_tok + t > budget:
            flush()
            cur, cur_tok = [], 0
        cur.append(c)
        cur_tok += t
    flush()
    return chunks


def map_chunks(call, chunks: list[dict], retries: int = 2, log=_noop):
    """逐段抽候選；單段失敗重試 retries 次後跳過不炸整批（screen.py 精神）。"""
    points, quotes = [], []
    for i, ch in enumerate(chunks, 1):
        for attempt in range(retries + 1):
            try:
                out = call(MAP_PROMPT + ch["text"])
                p = list(out.get("points") or [])
                q = list(out.get("quotes") or [])
                points += p
                quotes += q
                log(f"map {i}/{len(chunks)} [{ts_range(ch['start'], ch['end'])}] "
                    f"重點+{len(p)} 金句+{len(q)}")
                break
            except Exception as e:
                log(f"⚠️ map {i}/{len(chunks)} 第 {attempt + 1} 次失敗：{e}")
        else:
            log(f"⚠️ map {i}/{len(chunks)} 放棄此段（不炸整批）")
    return points, quotes


def filter_candidates(points: list[dict], quotes: list[dict],
                      flat: str, duration: float):
    """機械防幻覺：時間碼可解析且界內；金句片段 grep 得回 SRT；金句去重。"""
    def times_ok(item):
        try:
            a, b = parse_mmss(item.get("start")), parse_mmss(item.get("end"))
        except (ValueError, TypeError):
            return None
        if a < -0.5 or b < a or b > duration + 1.5:
            return None
        return a, b

    ok_points = []
    for p in points:
        t = times_ok(p)
        text = str(p.get("point") or "").strip()
        if t and text:
            ok_points.append({"start": t[0], "end": t[1], "point": text})

    ok_quotes, seen = [], set()
    for q in quotes:
        t = times_ok(q)
        text = strip_speakers(str(q.get("text") or "")).strip()
        if not t or not text:
            continue
        frags = quote_fragments(text)
        if not frags or any(f not in flat for f in frags):
            continue  # 逐字對不回 SRT ＝ 幻覺，丟
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        ok_quotes.append({"start": t[0], "end": t[1], "text": text,
                          "why": str(q.get("why") or "").strip() or "有記憶點"})
    ok_points.sort(key=lambda p: p["start"])
    ok_quotes.sort(key=lambda q: q["start"])
    return ok_points, ok_quotes


# ── Reduce：outline ＋ 逐小節內文 ────────────────────────────────────

def _pick_by_ids(ids, prefix: str, pool: list):
    out = []
    for raw in ids or []:
        m = re.fullmatch(rf"{prefix}?(\d+)", str(raw).strip())
        if not m:
            raise NoteGenError(f"編號格式錯：{raw!r}")
        idx = int(m.group(1)) - 1
        if not 0 <= idx < len(pool):
            raise NoteGenError(f"編號不存在：{raw!r}")
        out.append(pool[idx])
    return out


def _parse_outline(out: dict, points: list, quotes: list) -> dict:
    tldr = str(out.get("tldr") or "").strip()
    if not tldr:
        raise NoteGenError("tldr 空白")
    sections = []
    for s in out.get("sections") or []:
        members = _pick_by_ids(s.get("point_ids"), "P", points)
        if not members:
            continue
        sections.append({
            "title": str(s.get("title") or "").strip() or "未命名小節",
            "start": min(p["start"] for p in members),
            "end": max(p["end"] for p in members),
            "points": members,
        })
    if not sections:
        raise NoteGenError("outline 沒有任何有效小節")
    sections.sort(key=lambda s: s["start"])
    try:
        sel = _pick_by_ids(out.get("quote_ids"), "Q", quotes)
    except NoteGenError:
        sel = []
    if not sel:
        sel = list(quotes)
    return {"tldr": tldr, "sections": sections, "quotes": sel[:8]}


def _merge_tldr(call, sections: list, retries: int, log=_noop) -> str:
    mat = "\n".join(f"- {s['title']}：{s['points'][0]['point']}" for s in sections)
    for attempt in range(retries + 1):
        try:
            tldr = str(call(TLDR_PROMPT + mat).get("tldr") or "").strip()
            if tldr:
                return tldr
        except Exception as e:
            log(f"⚠️ 合併 TL;DR 第 {attempt + 1} 次失敗：{e}")
    return "、".join(s["title"] for s in sections) + "。"  # 機械保底（乾，卡上已接受）


def run_outline(call, points: list, quotes: list, budget: int,
                retries: int = 2, log=_noop, hint: str = "") -> dict:
    plines = [f"P{i + 1} [{ts_range(p['start'], p['end'])}] {p['point']}"
              for i, p in enumerate(points)]
    qlines = [f"Q{i + 1} [{ts_range(q['start'], q['end'])}] 「{q['text']}」"
              for i, q in enumerate(quotes)]
    blob = "\n".join(plines + qlines)
    if estimate_tokens(OUTLINE_PROMPT + blob) > budget and len(points) > 2:
        # 候選清單塞不進 context → 對半分治各自成小節，TL;DR 用小節標題合併
        mid = len(points) // 2
        left = run_outline(call, points[:mid], [], budget, retries, log)
        right = run_outline(call, points[mid:], [], budget, retries, log)
        sections = left["sections"] + right["sections"]
        return {"tldr": _merge_tldr(call, sections, retries, log),
                "sections": sections, "quotes": quotes[:8]}
    last = None
    for attempt in range(retries + 1):
        try:
            return _parse_outline(call(OUTLINE_PROMPT + blob + hint), points, quotes)
        except NoteGenError as e:
            last = e
            log(f"⚠️ outline 第 {attempt + 1} 次無效：{e}")
        except Exception as e:
            last = NoteGenError(str(e))
            log(f"⚠️ outline 第 {attempt + 1} 次失敗：{e}")
    raise last


def section_bodies(call, sections: list, retries: int = 2, log=_noop) -> list:
    out = []
    for s in sections:
        mat = "\n".join(f"- [{ts_range(p['start'], p['end'])}] {p['point']}"
                        for p in s["points"])
        body = ""
        for attempt in range(retries + 1):
            try:
                body = str(call(BODY_PROMPT + s["title"] + BODY_PROMPT_TAIL + mat)
                           .get("body") or "").strip()
                if body:
                    break
            except Exception as e:
                log(f"⚠️ 小節「{s['title']}」內文第 {attempt + 1} 次失敗：{e}")
        if not body:  # 機械保底：候選直接串起來（乾，但不空）
            body = "；".join(p["point"] for p in s["points"])
        out.append({**s, "body": body})
        log(f"小節「{s['title']}」[{ts_range(s['start'], s['end'])}] {len(body)} 字")
    return out


# ── Render ＋ 機械驗收三件 ──────────────────────────────────────────

def _cell(text: str) -> str:
    return str(text).replace("|", "｜").replace("\n", " ").strip()


def _clean_title(text: str) -> str:
    return re.sub(r"[\[\]#|\n]", "", text).strip()[:30] or "未命名小節"


def render_note(meta: dict, tldr: str, sections: list, quotes: list,
                tail: str = "") -> str:
    """輸出格式逐字鎖死同現有五場筆記（結構見模組 docstring）。"""
    esc_title = meta["title"].replace('"', '\\"')
    lines = [
        "---",
        f'title: "{esc_title}（筆記）"',
        f'speaker: "{meta.get("speaker", "")}"',
        f"created: {date.today().isoformat()}",
        "tool: good-students-note",
        "---",
        "",
        f"# {meta['title']} — 筆記",
        "",
        "> [!info] Ref source",
        *meta["ref_lines"],
        "",
        "## TL;DR",
        "",
        tldr.strip(),
        "",
        "## 重點筆記",
        "",
    ]
    for s in sections:
        lines += [f"### {_clean_title(s['title'])} [{ts_range(s['start'], s['end'])}]",
                  "", s["body"].strip(), ""]
    lines += [
        "## 金句／可剪片段候選",
        "",
        "| 起訖 | 內容 | 為什麼值得剪 |",
        "| :--- | :--- | :--- |",
    ]
    for q in quotes:
        lines.append(f"| {ts_range(q['start'], q['end'])} "
                     f"| 「{_cell(q['text'])}」 | {_cell(q['why'])} |")
    lines.append("")
    if tail:
        lines += [tail.strip(), ""]
    return "\n".join(lines)


_TS_PAT = r"\d+:\d{2}(?::\d{2})?"
_SEC_RE = re.compile(rf"(?m)^### .+ \[({_TS_PAT})–({_TS_PAT})\]\s*$")
_ROW_RE = re.compile(rf"^\|\s*({_TS_PAT})–({_TS_PAT})\s*\|")
TABLE_HEADER = "| 起訖 | 內容 | 為什麼值得剪 |"
TABLE_ALIGN = "| :--- | :--- | :--- |"


def validate_note(md: str, cues: list[dict]) -> list[str]:
    """機械驗收三件：(a) 時間碼界內 (b) 金句 grep 得回 SRT (c) 格式 lint。
    回傳錯誤清單，空 = 全過。"""
    errs = []
    duration = max(c["end"] for c in cues)
    flat = normalize("".join(c["text"] for c in cues))

    # (c) 格式 lint — frontmatter
    lines = md.splitlines()
    if not lines or lines[0].strip() != "---":
        errs.append("缺 YAML frontmatter")
    else:
        try:
            close = lines[1:].index("---") + 1
            keys = {l.split(":", 1)[0].strip() for l in lines[1:close] if ":" in l}
            for k in ("title", "speaker", "created", "tool"):
                if k not in keys:
                    errs.append(f"frontmatter 缺 {k}")
        except ValueError:
            errs.append("frontmatter 沒有關閉的 ---")

    if not re.search(r"(?m)^# .+ — 筆記\s*$", md):
        errs.append("缺主標題「# 題名 — 筆記」")
    if "> [!info] Ref source" not in md:
        errs.append("缺「> [!info] Ref source」callout")

    idx_t, idx_n, idx_q = (md.find("## TL;DR"), md.find("## 重點筆記"),
                           md.find("## 金句／可剪片段候選"))
    if not (0 <= idx_t < idx_n < idx_q):
        errs.append("三大節缺席或順序錯（## TL;DR → ## 重點筆記 → ## 金句／可剪片段候選）")
        return errs
    tldr_body = md[idx_t + len("## TL;DR"):idx_n].strip()
    if not tldr_body or "（待寫）" in tldr_body:
        errs.append("TL;DR 空白")

    notes_region = md[idx_n:idx_q]
    ranges = _SEC_RE.findall(notes_region)
    if not ranges:
        errs.append("重點筆記沒有任何「### 小節標題 [起–訖]」")

    quotes_region = md[idx_q:]
    if TABLE_HEADER not in quotes_region or TABLE_ALIGN not in quotes_region:
        errs.append(f"金句表表頭不符（須逐字為「{TABLE_HEADER}」＋對齊列）")
    rows = [l for l in quotes_region.splitlines()
            if l.startswith("|") and l not in (TABLE_HEADER, TABLE_ALIGN)]
    if not rows:
        errs.append("金句表沒有資料列")

    # (a) 時間碼界內（小節＋金句列）
    for row in rows:
        m = _ROW_RE.match(row)
        if not m:
            errs.append(f"金句列起訖欄格式錯：{row[:40]}")
        else:
            ranges.append((m.group(1), m.group(2)))
    for a, b in ranges:
        try:
            sa, sb = parse_mmss(a), parse_mmss(b)
        except ValueError:
            errs.append(f"時間碼解析失敗：{a}–{b}")
            continue
        if sa > sb or sb > duration + 1.5:
            errs.append(f"時間碼越界：{a}–{b}（SRT 總時長 {fmt_ts(duration)}）")

    # (b) 金句 grep 得回 SRT 全文（防幻覺）
    for row in rows:
        cells = row.split("|")
        if len(cells) < 4:
            continue  # 格式錯已在上面報
        for frag in quote_fragments(cells[2]):
            if frag not in flat:
                errs.append(f"金句對不回 SRT（幻覺？）：{frag[:30]}…")
    return errs


# ── 組裝 ─────────────────────────────────────────────────────────────

def _reserve(context: int) -> int:
    return max(OUTPUT_RESERVE_MIN, min(OUTPUT_RESERVE, context // 3))


def build_note(call, cues: list[dict], meta: dict, retries: int = 2,
               context: int = DEFAULT_CONTEXT, log=print, tail: str = "") -> str:
    """map → 機械過濾 → reduce → render；機械驗收不過自動重試 retries 輪。"""
    duration = max(c["end"] for c in cues)
    flat = normalize("".join(c["text"] for c in cues))
    budget = context - _reserve(context)
    map_budget = budget - estimate_tokens(MAP_PROMPT)
    if map_budget <= 0:
        raise NoteGenError(f"context {context} 連 prompt 都放不下")

    chunks = chunk_cues(cues, map_budget)
    log(f"map-reduce：{len(cues)} cues → {len(chunks)} 段"
        f"（context {context}、段預算 {map_budget} tokens）")
    raw_p, raw_q = map_chunks(call, chunks, retries, log)
    points, quotes = filter_candidates(raw_p, raw_q, flat, duration)
    log(f"候選：重點 {len(points)}/{len(raw_p)}、金句 {len(quotes)}/{len(raw_q)}"
        f"（機械過濾後/原始）")
    if not points:
        raise NoteGenError("map 後沒有任何可用重點候選")

    last_errs = []
    hint = ""
    for attempt in range(retries + 1):
        outline = run_outline(call, points, quotes, budget, retries, log, hint)
        sections = section_bodies(call, outline["sections"], retries, log)
        md = render_note(meta, outline["tldr"], sections, outline["quotes"], tail)
        errs = validate_note(md, cues)
        if not errs:
            log(f"機械驗收 PASS（第 {attempt + 1} 輪）：時間碼界內 ✓ 金句 grep ✓ 格式 lint ✓")
            return md
        last_errs = errs
        hint = "\n\n上一輪產出未過機械驗收，請修正：" + "；".join(errs[:5])
        log(f"⚠️ 機械驗收未過（第 {attempt + 1} 輪）：{errs}")
    raise NoteGenError(f"機械驗收 {retries + 1} 輪未過：" + "；".join(last_errs))


def resolve_meta(slug: str, srt: Path) -> dict:
    """title/speaker/Ref source：有 frames/manifest.json 就沿用 compose 的口徑。"""
    sdir = SESSIONS_DIR / slug
    title, speaker, video, ref_lines = slug, "", None, []
    mp = manifest_path(slug)
    if mp.exists():
        m = json.loads(mp.read_text(encoding="utf-8"))
        title = m.get("title") or slug
        speaker = m.get("speaker") or ""
        video = m.get("video")
        from compose import sanitize_name
        folder = sanitize_name(f"{speaker + '_' if speaker else ''}{title}")
        ref_lines.append(f"> - 逐字稿（含畫面與停頓標注）：[[{folder}_逐字稿]]")
    else:
        ref_lines.append(f"> - 逐字稿（SRT）：`{srt}`")
    if video:
        ref_lines.append(f"> - 影片原檔：`{video}`")
    else:
        src = next(iter(sorted(sdir.glob("source.*"))), None)
        if src:
            ref_lines.append(f"> - 音檔原檔：`{src}`")
    return {"title": title, "speaker": speaker, "ref_lines": ref_lines}


def make_call(cfg: dict, counter: dict):
    max_tokens = int(cfg.get("NOTES_MAX_TOKENS", "6000"))

    def call(prompt: str) -> dict:
        counter["calls"] += 1
        msg = llm_chat(cfg, [{"role": "user", "content": prompt}],
                       max_tokens=max_tokens)
        return extract_json_from_message(msg)

    return call


def main():
    ap = argparse.ArgumentParser(description="frames Stage 4 — 本地筆記蒸餾（零雲端 token）")
    ap.add_argument("slug", help="sessions/<slug> 目錄名")
    ap.add_argument("--retries", type=int, default=2,
                    help="機械驗收不過的自動重試輪數（預設 2）")
    ap.add_argument("--out", type=Path, default=None,
                    help="輸出路徑（預設 sessions/<slug>/notes.md；"
                         "指到 compose 的 _筆記.md 會保留其「## 關鍵畫面」尾段）")
    ap.add_argument("--force", action="store_true",
                    help="輸出檔已有內容（無「（待寫）」）時仍強制覆寫")
    ap.add_argument("--context", type=int,
                    default=None, help=f"context 上限（預設 NOTES_CONTEXT 或 {DEFAULT_CONTEXT}）")
    ap.add_argument("--unload", action="store_true",
                    help="跑完 lms unload 卸載模型（RAM 守門）")
    args = ap.parse_args()

    cfg = load_config()
    if "LM_STUDIO_TOKEN" not in cfg:
        sys.exit("mars-cc/.env 找不到 LM_STUDIO_TOKEN")
    context = args.context or int(cfg.get("NOTES_CONTEXT", str(DEFAULT_CONTEXT)))

    sdir = SESSIONS_DIR / args.slug
    if not sdir.is_dir():
        sys.exit(f"找不到 session：{sdir}")
    srt = pick_srt(sdir)
    if not srt and manifest_path(args.slug).exists():
        m = json.loads(manifest_path(args.slug).read_text(encoding="utf-8"))
        if m.get("srt") and Path(m["srt"]).exists():
            srt = Path(m["srt"])
    if not srt:
        sys.exit(f"{args.slug} 找不到 SRT（{'/'.join(SRT_CANDIDATES)}，"
                 f"manifest.srt 也沒有）— 先跑 session.py/transcribe")
    cues = parse_srt(srt)
    if not cues:
        sys.exit(f"SRT 解析不出任何字幕：{srt}")

    out_path = args.out or (sdir / "notes.md")
    tail = ""
    if out_path.exists():
        old = out_path.read_text(encoding="utf-8")
        if "（待寫）" not in old and not args.force:
            sys.exit(f"{out_path} 已有內容，不覆寫（要重生用 --force）")
        i = old.find("\n## 關鍵畫面")
        if i >= 0:
            tail = old[i + 1:]

    meta = resolve_meta(args.slug, srt)
    counter = {"calls": 0}
    call = make_call(cfg, counter)
    print(f"{args.slug}：SRT={srt.name if srt.parent == sdir else srt}"
          f"（{len(cues)} cues、{fmt_ts(max(c['end'] for c in cues))}）"
          f"model={cfg['LM_STUDIO_MODEL']} context={context}")
    t0 = time.time()
    try:
        md = build_note(call, cues, meta, retries=args.retries,
                        context=context, log=print, tail=tail)
    except NoteGenError as e:
        sys.exit(f"❌ {e}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"✅ {out_path}（LLM 呼叫 {counter['calls']} 次、{time.time() - t0:.0f}s，"
          f"全程零雲端 token）")

    if args.unload:
        r = subprocess.run(["lms", "unload", cfg["LM_STUDIO_MODEL"]],
                           capture_output=True, text=True)
        print("lms unload：" + ("OK" if r.returncode == 0
                                else f"失敗（{(r.stderr or r.stdout).strip()[:120]}）"))


if __name__ == "__main__":
    main()
