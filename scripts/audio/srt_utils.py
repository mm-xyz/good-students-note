#!/usr/bin/env python3
"""
scripts/audio/srt_utils.py — 音訊分析線共用的 SRT 解析/輸出工具

只用 stdlib,主環境與 .venv-audio 都能 import。
時間一律用 float 秒;SRT 時間碼格式 HH:MM:SS,mmm。
"""

import re
from pathlib import Path

_TS_RE = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})")
# speaker 前綴慣例:[S1] 或 [語嫣](diarize 對齊後的 transcript.speakers.srt 用)
SPEAKER_PREFIX_RE = re.compile(r"^\[([^\]]{1,20})\]\s*")


def ts_to_sec(ts: str) -> float:
    m = _TS_RE.search(ts)
    if not m:
        raise ValueError(f"bad SRT timestamp: {ts!r}")
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def sec_to_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = round((sec % 1) * 1000)
    if ms == 1000:  # rounding carry
        ms = 0
        sec += 1
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d},{ms:03d}"


def fmt_mmss(sec: float) -> str:
    """人讀格式 M:SS / H:MM:SS(cutplan.md、highlights.md 用)。"""
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60}:{sec % 60:02d}"


def parse_srt(path: Path) -> list[dict]:
    """Parse SRT → [{idx, start, end, text, speaker|None}, ...](start/end 秒)。

    text 已去掉 speaker 前綴;原始前綴放進 speaker 欄。
    """
    cues = []
    blocks = path.read_text(encoding="utf-8").strip().split("\n\n")
    for block in blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            # 容錯:有些 SRT 沒有 index 行
            if lines and "-->" in lines[0]:
                lines = [""] + lines
            else:
                continue
        start_s, end_s = lines[1].split("-->")
        text = "\n".join(lines[2:]).strip()
        speaker = None
        m = SPEAKER_PREFIX_RE.match(text)
        if m:
            speaker = m.group(1)
            text = text[m.end():]
        cues.append({
            "idx": len(cues) + 1,
            "start": ts_to_sec(start_s),
            "end": ts_to_sec(end_s),
            "text": text,
            "speaker": speaker,
        })
    return cues


def write_srt(cues: list[dict], path: Path) -> None:
    """Write cues(可含 speaker 欄)回 SRT;speaker 以 [X] 前綴呈現。"""
    out = []
    for i, c in enumerate(cues, 1):
        prefix = f"[{c['speaker']}] " if c.get("speaker") else ""
        out.append(f"{i}\n{sec_to_ts(c['start'])} --> {sec_to_ts(c['end'])}\n"
                   f"{prefix}{c['text']}\n")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def rel(path: Path, root: Path) -> str:
    """path 相對 root 的字串;不在 root 底下(session 在 repo 外)就給絕對路徑。"""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def pick_transcript(session_dir: Path) -> Path:
    """分析線的逐字稿來源:優先 Phase A 的 cleaned.srt(時間碼與 transcript.srt
    一致、錯字已修),沒有才退回 IMMUTABLE 的 transcript.srt。"""
    for name in ("cleaned.srt", "transcript.srt"):
        p = session_dir / name
        if p.exists():
            return p
    raise FileNotFoundError(f"no SRT found in {session_dir} (need cleaned.srt or transcript.srt)")


def find_source_media(session_dir: Path) -> Path:
    """session 內的 source.<ext> symlink(音檔或影片)。"""
    for p in sorted(session_dir.glob("source.*")):
        if p.suffix.lower() not in (".srt", ".md", ".json", ".txt"):
            return p
    raise FileNotFoundError(f"no source media in {session_dir}")


def join_words(ws: list[dict], ref_text: str) -> str:
    """word 文字串接(words.json 每字已 strip,原空格遺失)。英數字相鄰時
    以原 cue 文字為準:原文有 "a b" 才補空格(whisper 常把單字拆半,
    "M"+"ars" 不能補成 "M ars")。"""
    parts = []
    for w in ws:
        if parts and parts[-1][-1:].isascii() and parts[-1][-1:].isalnum() \
                and w["word"][:1].isascii() and w["word"][:1].isalnum() \
                and f"{parts[-1]} {w['word']}" in ref_text:
            parts.append(" ")
        parts.append(w["word"])
    return "".join(parts)


# 短句切點:句末+句中標點(全形為主,whisper zh 標點跟在 word 字尾)
PHRASE_PUNCT = "。?!…,、;:?!,;:"


def split_words_to_phrases(ws: list[dict], ref_text: str,
                           gap: float = 0.5) -> list[dict]:
    """把一個 cue 的 words 切成 EP15 式短句:字尾帶標點、或與下一字間隔
    ≥ gap 秒就斷句(2026-07-29 MM 拍板,cutplan 粒度以 EP15 為準)。
    回傳 [{start, end, text}, ...];文字由 words 重建(與 render 字級對齊
    同源,ADR 0005)。ws 為空回傳 []。"""
    if not ws:
        return []
    groups: list[list[dict]] = [[]]
    for i, w in enumerate(ws):
        groups[-1].append(w)
        is_last = i == len(ws) - 1
        cut = (w["word"][-1:] in PHRASE_PUNCT
               or (not is_last and ws[i + 1]["start"] - w["end"] >= gap))
        if cut and not is_last:
            groups.append([])
    groups = [g for g in groups if g]
    # 全由零長度 artifact word 組成的 group(whisper 偶發 start==end)不能
    # 自成短句(會產生 0 長度 cue,下游換手切開會把字吃進前句留下孤兒),
    # 併回前一組(開頭就出現則併進下一組)
    merged: list[list[dict]] = []
    for g in groups:
        if merged and g[-1]["end"] - g[0]["start"] <= 0:
            merged[-1].extend(g)
        else:
            merged.append(g)
    if len(merged) > 1 and merged[0][-1]["end"] - merged[0][0]["start"] <= 0:
        merged[1][:0] = merged[0]
        merged.pop(0)
    return [{"start": g[0]["start"], "end": g[-1]["end"],
             "text": join_words(g, ref_text)} for g in merged]
