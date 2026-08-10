#!/usr/bin/env python3
"""
scripts/tests/fixtures/build_fixtures.py — 從真 session 擷取「踩過的坑」成回歸 fixture

    python3 scripts/tests/fixtures/build_fixtures.py \
        --session sessions/2026-07-27_EP16-不要跟工作談戀愛

每個 case 是一個**能直接餵給 render_cut.py --dry-run 的迷你 session**:
音訊、words、silences、blocks 全部裁到窗內並平移到 0 起算,外加 expect.json
(must_cut / must_keep 斷言)。test_render_audio.py 逐個跑,誰改壞了誰就紅。

為什麼要真音訊:既有 38 項單元測試用假資料鎖時間區間運算,2026-08-10 那三個
bug(字級對齊退回內插、長靜音兩端 snap 會合、word 保護擋掉真停頓)**全部躲過**
——它們錯在「算得對但對到錯的字」,只有拿真的 words/silences/波形才驗得出來。

case 內容一律避開節目裡被剪掉的敏感段落(MM 2026-08-10:字級對齊不挑內容,
換一段同樣有英文詞邊界的就好),所以 strike_alignment 用 B0084 而不是 B0024。
"""

import argparse
import json
import re
import shutil
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "audio"))
from srt_utils import sec_to_ts  # noqa: E402

HERE = Path(__file__).resolve().parent

# 每個 case:窗、要進 cutplan 的 block 與其標記、✂ 行、斷言
# mark: "x"=保留 / " "=剪掉;strike=(要包刪除線的原文片段,)
CASES = [
    {
        "name": "case1_strike_alignment",
        "why": "字級刪除線的字元流對齊 — win 前後都會多抓鄰接 block 的字,"
               "從 win[0] 起算會整條偏移(舊算法內插到 522.02,切進「想法就是」)",
        "win": (517.0, 526.5),
        "strikes": {"B0084": "那個"},
        "must_cut": [(521.58, 521.74)],          # 「那個」
        "must_keep": [(520.56, 520.76),          # 「Tim」
                      (522.20, 522.85)],         # 「就是」不准被切
    },
    {
        "name": "case2_repeat_cut",
        "why": "跳針只剪第一次 — 剪錯成第二個「相」會把「相同的問題」剪壞",
        "win": (401.0, 412.0),
        "strikes": {"B0058": "相,"},
        "must_cut": [(405.05, 405.35)],          # 第一個「相,」
        "must_keep": [(405.65, 406.30),          # 第二個「相」
                      (406.35, 408.35)],         # 「同的問題」
    },
    {
        "name": "case3_filler",
        "why": "贅詞字級精剪落在正確的字上",
        "win": (424.5, 434.0),
        "strikes": {"B0063": "然後呢,"},
        "must_cut": [(429.25, 429.68)],
        "must_keep": [(428.10, 428.70),          # 「過一兩天」
                      (430.30, 430.95)],         # 「被主管」
    },
    {
        "name": "case4_swallowed_pause",
        "why": "whisper 把停頓吃進字的時長(「任」736.24-737.70 佔 1.46s),"
               "word 保護會擋掉自動收緊 — 只有 ✂ 手動剪除吃得下去",
        "win": (730.0, 740.0),
        "cuts": [(736.45, 737.45)],
        "must_cut": [(736.60, 737.30)],
        "must_keep": [(735.85, 736.20),          # 「臨時」
                      (737.72, 737.86)],         # 「務」
    },
    {
        "name": "case5_short_deadair",
        "why": "未達 max-pause 門檻的死寂(1.38s,聽起來像當機)靠 ✂ 剪掉",
        "win": (1135.0, 1147.0),
        "cuts": [(1139.40, 1140.35)],
        "must_cut": [(1139.50, 1140.25)],
        "must_keep": [(1137.30, 1139.15),        # 「哦對對對對對」
                      (1140.60, 1141.64)],       # 「那你先說」
    },
    {
        "name": "case6_long_silence",
        "why": "G 列沒勾(=該剪)的長靜音,前後兩個 unit 的邊界會 snap 到"
               "同一個靜音中點 → 剪除量 0,整段 3.4s 原封不動留在成品裡",
        "win": (1594.0, 1606.0),
        "must_cut": [(1599.6, 1601.8)],          # G0010 中段必須消失
        "must_keep": [(1597.5, 1598.9),          # B0701
                      (1602.5, 1603.9)],         # B0702
    },
]


def slice_wav(src: Path, dst: Path, t0: float, t1: float) -> None:
    with wave.open(str(src), "rb") as w:
        sr, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        w.setpos(int(t0 * sr))
        frames = w.readframes(int((t1 - t0) * sr))
    with wave.open(str(dst), "wb") as o:
        o.setnchannels(nch)
        o.setsampwidth(sw)
        o.setframerate(sr)
        o.writeframes(frames)


def mark_strike(text: str, frag: str) -> str:
    i = text.find(frag)
    if i < 0:
        sys.exit(f"[fixtures] FAIL: 「{frag}」不在 block 文字裡:{text[:40]}")
    return text[:i] + f"~~{frag}~~" + text[i + len(frag):]


def build(case: dict, sess: Path, out_root: Path) -> str:
    t0, t1 = case["win"]
    d = out_root / case["name"]
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)

    cp = json.loads((sess / "cutplan.json").read_text(encoding="utf-8"))
    words = json.loads((sess / "words.json").read_text(encoding="utf-8"))
    pros = json.loads((sess / "prosody.json").read_text(encoding="utf-8"))

    # 窗要**完整**含住每個沾到的 block:被截半的 block 會讓 words 不齊,
    # 字元流對齊直接失敗、退回線性內插,測出來的是 fixture 的病不是 code 的
    for _ in range(6):
        sel = [b for b in cp["blocks"] if b["end"] > t0 and b["start"] < t1]
        if not sel:
            sys.exit(f"[fixtures] FAIL: {case['name']} 窗內沒有 block")
        n0 = min(min(b["start"] for b in sel) - 0.4, t0)
        n1 = max(max(b["end"] for b in sel) + 0.4, t1)
        if (n0, n1) == (t0, t1):
            break
        t0, t1 = n0, n1
    t0 = max(0.0, t0)
    blocks = [b for b in cp["blocks"] if b["end"] > t0 and b["start"] < t1]
    gaps = [g for g in cp.get("gaps", []) if g["end"] > t0 and g["start"] < t1]

    def shift(o: dict, keys=("start", "end")) -> dict:
        return {**o, **{k: round(o[k] - t0, 3) for k in keys}}

    blocks = [shift(b) for b in blocks]
    gaps = [shift(g) for g in gaps]
    words = [shift(w) for w in words if w["end"] > t0 and w["start"] < t1]
    sil = [shift(s) for s in pros.get("silences", [])
           if s["end"] > t0 and s["start"] < t1]

    slice_wav(sess / "audio16k.wav", d / "audio16k.wav", t0, t1)
    (d / "words.json").write_text(json.dumps(words, ensure_ascii=False),
                                  encoding="utf-8")
    (d / "prosody.json").write_text(json.dumps({"silences": sil},
                                               ensure_ascii=False), encoding="utf-8")
    (d / "cutplan.json").write_text(json.dumps(
        {**{k: v for k, v in cp.items() if k not in ("blocks", "gaps")},
         "blocks": blocks, "gaps": gaps}, ensure_ascii=False), encoding="utf-8")

    srt = []
    for i, b in enumerate(blocks, 1):
        srt.append(f"{i}\n{sec_to_ts(b['start'])} --> {sec_to_ts(b['end'])}\n"
                   f"[{b['speaker']}] {b['text']}\n")
    (d / "transcript.speakers.srt").write_text("\n".join(srt), encoding="utf-8")

    md = [f"# Cutplan fixture — {case['name']}", "",
          f"> {case['why']}", "",
          "## ⚙ clip-gap=0.5 max-pause=1.5"]
    for c in case.get("cuts", []):
        md.append(f"## ✂ {round(c[0] - t0, 3)}-{round(c[1] - t0, 3)}")
    rows = sorted(blocks + gaps, key=lambda x: x["start"])
    for b in rows:
        if b["id"].startswith("G"):
            md.append(f"- [ ] {b['id']} [{b['start']:.0f}–{b['end']:.0f}] "
                      f"⬜ 空白/非語音 {b['end'] - b['start']:.1f}s")
            continue
        text = b["text"]
        if b["id"] in case.get("strikes", {}):
            text = mark_strike(text, case["strikes"][b["id"]])
        md.append(f"- [x] {b['id']} [{b['start']:.0f}–{b['end']:.0f}] "
                  f"[{b['speaker']}] {text}")
    (d / "cutplan.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    (d / "expect.json").write_text(json.dumps({
        "why": case["why"],
        "source": {"session": sess.name, "window": [t0, t1]},
        "must_cut": [[round(a - t0, 3), round(b - t0, 3)]
                     for a, b in case.get("must_cut", [])],
        "must_keep": [[round(a - t0, 3), round(b - t0, 3)]
                      for a, b in case.get("must_keep", [])],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    kb = sum(f.stat().st_size for f in d.iterdir()) / 1024
    return f"{case['name']:26} {t1 - t0:5.1f}s  {len(blocks):2} blocks  {kb:6.0f} KB"


def main() -> None:
    ap = argparse.ArgumentParser(description="建回歸 fixture")
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", type=Path, default=HERE / "ep16")
    args = ap.parse_args()
    sess = Path(args.session).resolve()
    for line in (build(c, sess, args.out) for c in CASES):
        print("[fixtures] " + line)
    print(f"[fixtures] ✅ {len(CASES)} 個 case → {args.out}")


if __name__ == "__main__":
    main()
