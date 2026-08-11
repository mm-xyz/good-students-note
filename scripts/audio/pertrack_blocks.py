#!/usr/bin/env python3
"""
scripts/audio/pertrack_blocks.py — canonical 逐字稿 ＋ 三軌波形 → 逐軌 cutplan

    python3 scripts/audio/pertrack_blocks.py --session sessions/<slug>

2026-08-11 改版(D1–D3)。**逐軌 ASR 不再產生正式 block。**

實證(EP16):Mars 直錄軌在 295–323 秒陷入「嘗」重複迴圈,而 Sarah／KIN 軌的
**串音** ASR 反而轉出了 Mars 的原句。逐軌 ASR 在單一麥克風上(訊噪比差、
缺少其他人語境)比混音更容易崩,所以:

    正式文字   = 混音 transcript(cutplan.json 的 block 文字)依 words.json
                 細切成 0.4–1.2 秒的 canonical phrase
    逐軌波形   = 只決定「這句歸哪一軌」與「各軌何時靜音」
    逐軌 ASR   = 降級成「重疊語句的救援證據」,只在歸屬不確定時附一條平行列

輸出:
    cutplan.json 的 `tracks`: [{speaker, prefix, file, blocks:[...]}]
    cutplan.pertrack.md:**完整節目單** —— cutplan.md 的 ⚙/✂/🎵/➕＋S 列/
        🎬/章節/G 列原樣搬過來,B 列換成逐軌列,依時間排序。
        render_cut.py --plan cutplan.pertrack.md 直接吃這一份。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import fmt_mmss, parse_srt, split_words_to_phrases  # noqa: E402

HOP = 0.01                # 基底 frame 網格(10ms),D2/D3 都在上面積分
SR = 8000                 # 能量分析取樣率(語音能量夠用,記憶體省)
ATTR_WIN = 0.10           # D2 積分窗 ~100ms
SCAN_WIN = 0.02           # D3 掃描窗 20ms
ROW_RE = re.compile(r"^- \[( |x|X)\] ([A-Z]{1,2}\d{3,5}) ")
INSERT_ID_RE = re.compile(r"^S\d{3,5}$")
VOWELS = set("AEIOU")


# ── 前綴 ────────────────────────────────────────────────────────────────
def derive_prefixes(names: list[str]) -> dict[str, str]:
    """講者名 → 兩碼前綴(Mars→MR、Sarah→SR、KIN→KN)。

    **一定要兩碼**:單碼 S 會跟 insert_prepare.py 產的補錄 block(S0001)撞號,
    單碼 B/G 是混音線的 block/gap。非 ASCII 名字退回 T1/T2/T3。
    """
    out: dict[str, str] = {}
    used: set[str] = set()
    for i, n in enumerate(names, 1):
        s = "".join(c for c in n.upper() if c.isascii() and c.isalpha())
        cand = ""
        if len(s) >= 2:
            tail = next((c for c in s[1:] if c not in VOWELS), s[1])
            cand = s[0] + tail
        if not cand or cand in used:
            cand = f"T{i}"
        k = 1
        while cand in used:
            cand, k = f"T{i}{k}", k + 1
        used.add(cand)
        out[n] = cand
    return out


# ── 粒度 ────────────────────────────────────────────────────────────────
def enforce_phrase_len(parts: list[dict], lo: float = 0.4,
                       hi: float = 1.2) -> list[dict]:
    """把 <lo 秒的碎片併進相鄰 phrase(併完不得超過 hi 秒;跨 owner 不併)。"""
    out: list[dict] = []
    for p in parts:
        prev = out[-1] if out else None
        if (prev and p["end"] - p["start"] < lo
                and prev["owner"] == p["owner"]
                and p["end"] - prev["start"] <= hi + 1e-9):
            prev["end"] = p["end"]
            prev["text"] += p["text"]
            prev["words"] = prev["words"] + p["words"]
            prev["uncertain"] = prev["uncertain"] or p["uncertain"]
            prev["reason"] = prev["reason"] or p["reason"]
            continue
        out.append(dict(p))
    # 開頭那個碎片沒有前鄰可併 → 往後併
    if len(out) > 1 and out[0]["end"] - out[0]["start"] < lo \
            and out[0]["owner"] == out[1]["owner"] \
            and out[1]["end"] - out[0]["start"] <= hi + 1e-9:
        out[1]["start"] = out[0]["start"]
        out[1]["text"] = out[0]["text"] + out[1]["text"]
        out[1]["words"] = out[0]["words"] + out[1]["words"]
        out.pop(0)
    return out


# ── 文件結構搬運 ─────────────────────────────────────────────────────────
def carry_over_program(md_lines: list[str], id_time: dict[str, float]):
    """cutplan.md 的非 B 列結構抽出來,附錨點時間(＝文件中它後面第一個計時列)。

    ➕ 補錄標頭與它底下的 S 列黏成同一組 —— S 列必須排在所屬 ➕ 標頭底下,
    拆開 render 會 FAIL。
    """
    items: list[dict] = []
    cur_insert: dict | None = None
    for raw in md_lines:
        s = raw.strip()
        m = ROW_RE.match(s)
        if m:
            bid = m.group(2)
            if INSERT_ID_RE.match(bid):
                if cur_insert is not None:
                    cur_insert["lines"].append(s)
                continue
            cur_insert = None
            t = id_time.get(bid)
            if bid.startswith("G"):
                items.append({"lines": [s], "anchor": t, "kind": "gap",
                              "time": t})
            else:
                items.append({"kind": "btime", "time": t, "lines": []})
            continue
        if s.startswith("## "):
            cur_insert = {"lines": [s], "anchor": None, "kind": "struct",
                          "time": None}
            items.append(cur_insert)
            if not s.startswith("## ➕"):
                cur_insert = None
            continue
    nxt = math.inf
    for it in reversed(items):
        if it["time"] is not None:
            nxt = it["time"]
        elif it["kind"] == "struct":
            it["anchor"] = nxt
    out = [it for it in items if it["kind"] != "btime"]
    for i, it in enumerate(out):
        it["seq"] = i
    return out


def _row_line(r: dict) -> str:
    mark = "x" if r["keep"] else " "
    tail = f" ← {r['reason']}" if r.get("reason") else ""
    return (f"- [{mark}] {r['id']} [{fmt_mmss(r['start'])}–{fmt_mmss(r['end'])}]"
            f" [{r['speaker']}] {r['text']}{tail}")


PREAMBLE = """> **分軌剪輯**。文字來源＝混音 canonical 逐字稿(逐軌 ASR 只當救援證據,
> 不產生正式文字 —— 單軌訊噪比差,whisper 更容易陷入重複迴圈)。
> 波形只決定「這句歸哪一軌」與「各軌何時靜音」。
>
> **兩層模型**:某區間三軌全部沒勾 → 整段移除(時間消失,三軌一起);
> 有人勾有人沒勾 → 時間保留、沒勾的那一軌在該區間靜音。
> 沒有任何 block 覆蓋的軌 ＝ 預設關著(常態衰減),不必特別標。
> `（非詞彙出聲／待辨 N.Ns）`＝該軌有出聲但沒有文字(嗯聲/呼吸/碰桌都可能),
> **預設不勾＝該軌該區間靜音**;要留就勾回來。
> `~~刪除線~~`＝**該講者軌**的字級靜音,不會影響其他軌。
> 同一軌兩個重疊 block 勾選矛盾 → render 直接 FAIL,不猜。
>
> 出片:`python3 scripts/audio/render_cut.py --session sessions/<slug> \\
> --plan cutplan.pertrack.md --out final_cut_pertrack.mp3`
"""


def build_md(session_name: str, groups: list[dict], rows: list[dict],
             low_rows: list[dict] | None = None) -> str:
    """節目單(搬過來的結構)＋逐軌列,依時間排序;低信心候選收進折疊區。

    折疊區**不能**用 `## 標題` —— parse_program 會把它當成 podcast 章節。
    """
    ent = [((g["anchor"], 0, g["seq"]), g["lines"]) for g in groups]
    ent += [((r["start"], 1, r["id"]), [_row_line(r)]) for r in rows]
    lines = [f"# Cutplan（分軌）— {session_name}", "", PREAMBLE]
    for _k, ls in sorted(ent, key=lambda x: (x[0][0], x[0][1], str(x[0][2]))):
        lines += ls
    if low_rows:
        lines += ["", "<details>",
                  f"<summary>低信心非詞彙出聲候選 {len(low_rows)} 筆"
                  f"（預設不勾＝已靜音；要撈回來才展開）</summary>", ""]
        lines += [_row_line(r) for r in sorted(low_rows,
                                               key=lambda r: r["start"])]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def pick_visible(events: list[dict], duration: float, per_min: float = 2.0,
                 high_db: float = 6.0):
    """可見候選 ≤每分鐘 per_min 列,且只收高信心(超出門檻 ≥high_db);其餘折疊。"""
    ranked = sorted(events, key=lambda e: -e["score"])
    budget = int(duration / 60.0 * per_min)
    vis = [e for e in ranked if e["score"] >= high_db][:budget]
    keep = {id(e) for e in vis}
    return vis, [e for e in ranked if id(e) not in keep]


# ── 音訊 ────────────────────────────────────────────────────────────────
def track_power(path: Path, hop: float = HOP, sr: int = SR):
    """整軌 → frame 功率序列(線性)。80Hz high-pass 先砍掉桌面撞擊與空調隆隆。"""
    import numpy as np
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
         "-af", "highpass=f=80", "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    step = int(round(sr * hop))
    n = (len(x) // step) * step
    return (x[:n] ** 2).reshape(-1, step).mean(axis=1)


def main() -> int:
    import numpy as np
    from pertrack_attrib import (annotate_canonical, calibrate_bleed,
                                 cfar_percentile, db, drop_self_adjacent,
                                 find_events, integrate, owner_runs,
                                 predict_bleed_db, split_phrase)

    ap = argparse.ArgumentParser(description="canonical 逐字稿＋三軌波形 → 逐軌 cutplan")
    ap.add_argument("--session", required=True)
    ap.add_argument("--plan", default="cutplan.md", help="搬結構用的來源節目單")
    ap.add_argument("--out-md", default="cutplan.pertrack.md")
    ap.add_argument("--margin", type=float, default=3.0,
                    help="歸屬所需的領先幅度(dB);不足就標歸屬不確定,不硬選")
    ap.add_argument("--stable", type=float, default=0.2)
    ap.add_argument("--switch", type=float, default=0.18)
    ap.add_argument("--snap", type=float, default=0.25,
                    help="換手點找 canonical 字界的搜尋半徑(秒);找不到就不切")
    ap.add_argument("--phrase-min", type=float, default=0.4)
    ap.add_argument("--phrase-max", type=float, default=1.2)
    ap.add_argument("--cfar-pct", type=float, default=99.5)
    ap.add_argument("--floor-margin", type=float, default=6.0)
    ap.add_argument("--gap-close", type=float, default=0.08)
    ap.add_argument("--voicing-min", type=float, default=0.12)
    ap.add_argument("--voicing-guard", type=float, default=0.25,
                    help="出聲事件距離自己台詞多近就丟掉(秒)。預設不勾＝靜音,"
                         "緊貼自己字頭的事件留著會把字頭削掉")
    ap.add_argument("--visible-per-min", type=float, default=2.0)
    ap.add_argument("--high-conf", type=float, default=6.0)
    args = ap.parse_args()

    sdir = Path(args.session)
    cj = sdir / "cutplan.json"
    cp = json.loads(cj.read_text(encoding="utf-8"))
    words = json.loads((sdir / "words.json").read_text(encoding="utf-8"))
    wavs = sorted(p for p in (sdir / "tracks").glob("*")
                  if p.suffix.lower() in (".wav", ".flac"))
    if not wavs:
        print(f"[pertrack] ✗ {sdir/'tracks'} 沒有分軌音檔", file=sys.stderr)
        return 2
    names = [p.stem.split("_", 1)[1] if "_" in p.stem else p.stem for p in wavs]
    pfx = derive_prefixes(names)
    print(f"[pertrack] {len(wavs)} 軌:"
          + "、".join(f"{n}({pfx[n]})" for n in names))

    print(f"[pertrack] 解能量包絡（{SR}Hz → {HOP*1000:.0f}ms frame）…")
    pw = [track_power(p) for p in wavs]
    nf = min(len(x) for x in pw)
    P = np.vstack([x[:nf] for x in pw])
    L_attr = db(np.vstack([integrate(P[i], int(ATTR_WIN / HOP))
                           for i in range(len(P))]))
    L_scan = db(np.vstack([integrate(P[i], max(1, int(SCAN_WIN / HOP)))
                           for i in range(len(P))]))
    dur = nf * HOP

    g = calibrate_bleed(L_attr)
    print("[pertrack] 串音校準(收到方 ← 講話方):")
    for i, n in enumerate(names):
        print(f"    {n:6s} ← " + "  ".join(
            f"{names[j]}:{g[i][j]:+.1f}dB" for j in range(len(names)) if j != i))

    # ── D1/D2:canonical phrase → 逐軌歸屬 ──────────────────────────────
    idx = {n: i for i, n in enumerate(names)}
    per_track: dict[str, list[dict]] = {n: [] for n in names}
    n_split = n_unc = n_phr = 0
    for b in cp["blocks"]:
        ws = [w for w in words
              if b["start"] - 1e-6 <= (w["start"] + w["end"]) / 2 < b["end"] + 1e-6]
        if not ws:
            continue
        # 文字一律取 canonical block 的字(words.json 可能還是校稿前的版本)
        ws = annotate_canonical(ws, b["text"])
        for cue in split_words_to_phrases(ws, b["text"], max_secs=args.phrase_max):
            cw = [w for w in ws
                  if cue["start"] - 1e-6 <= (w["start"] + w["end"]) / 2
                  < cue["end"] + 1e-6]
            if not cw:
                continue
            runs = owner_runs(L_attr, HOP, cue["start"], cue["end"],
                              args.margin, args.stable, args.switch)
            parts = enforce_phrase_len(
                split_phrase(cw, b["text"], runs, args.snap),
                args.phrase_min, args.phrase_max)
            n_phr += 1
            n_split += max(0, len(parts) - 1)
            for p in parts:
                owner = p["owner"]
                reason = p["reason"]
                if owner is None:
                    # 不硬選:退回 diarize 的講者標籤並標記,交人審
                    owner = idx.get(b.get("speaker", ""), None)
                    n_unc += 1
                    reason = (reason or "歸屬不確定") + \
                        f"（暫掛 diarize 判的 {b.get('speaker','?')}）"
                if owner is None:
                    owner = int(np.argmax(L_attr[:, int(p["start"] / HOP)]))
                per_track[names[owner]].append(
                    {"start": round(p["start"], 3), "end": round(p["end"], 3),
                     "text": p["text"].strip(), "kind": "speech", "keep": True,
                     "speaker": names[owner], "reason": reason, "src": b["id"]})
    print(f"[pertrack] canonical phrase {n_phr} 句 → 換手切開 {n_split} 處、"
          f"歸屬不確定 {n_unc} 處")

    # ── D3:非詞彙出聲(CFAR 自適應門檻)──────────────────────────────────
    cov = np.zeros((len(names), nf), dtype=bool)
    for n, rows in per_track.items():
        for r in rows:
            cov[idx[n], int(r["start"] / HOP):int(r["end"] / HOP) + 1] = True
    anyone = cov.any(axis=0)
    quiet = ~anyone & (L_attr.max(axis=0) < np.percentile(L_attr.max(axis=0), 20))
    noise = [float(cfar_percentile(L_scan[i][quiet], args.cfar_pct,
                                   default=float(np.percentile(L_scan[i], 1))))
             for i in range(len(names))]
    pred = predict_bleed_db(L_scan, g, noise)
    resid = L_scan - pred

    order = np.argsort(-L_attr, axis=0)
    ai = np.arange(nf)
    lead = L_attr[order[0], ai] - L_attr[order[1], ai]
    events: list[dict] = []
    for i, n in enumerate(names):
        neg = (lead >= 12.0) & (order[0] != i) & ~cov[i]
        thr = cfar_percentile(resid[i][neg], args.cfar_pct, default=10.0)
        floor = noise[i] + args.floor_margin
        others = cov[[j for j in range(len(names)) if j != i]].any(axis=0)
        hits = (resid[i] >= thr) & (L_scan[i] >= floor) & others & ~cov[i]
        dur_thr = cfar_percentile(
            [b - a for a, b in find_events(hits & neg, HOP, 0.0, 0.0)],
            args.cfar_pct, default=0.0) or 0.0
        min_dur = max(args.voicing_min, dur_thr)
        own = [(r["start"], r["end"]) for r in per_track[n]
               if r["kind"] == "speech"]
        raw_ev = [{"track": n, "start": round(a, 3), "end": round(b, 3),
                   "score": float(np.median(
                       resid[i][int(a / HOP):max(int(a / HOP) + 1,
                                                 int(b / HOP))]) - thr),
                   "thr": float(thr)}
                  for a, b in find_events(hits, HOP, args.gap_close, min_dur)]
        kept_ev = drop_self_adjacent(raw_ev, own, args.voicing_guard)
        events += kept_ev
        print(f"[pertrack] {n:6s} CFAR 門檻 excess ≥{thr:+.1f}dB、"
              f"絕對下限 {floor:.1f}dB、最短 {min_dur*1000:.0f}ms → "
              f"{len(kept_ev)} 段(貼著自己台詞丟棄 "
              f"{len(raw_ev) - len(kept_ev)} 段)")

    vis, low = pick_visible(events, dur, args.visible_per_min, args.high_conf)
    for e in events:
        e_row = {"start": e["start"], "end": e["end"],
                 "text": f"（非詞彙出聲／待辨 {e['end'] - e['start']:.1f}s）",
                 "kind": "voicing", "keep": False, "speaker": e["track"],
                 "reason": f"超出串音預測 {e['score'] + e['thr']:+.1f}dB"
                           f"（門檻 {e['thr']:+.1f}dB）", "src": ""}
        e["row"] = e_row
        per_track[e["track"]].append(e_row)

    # ── 編號 ＋ 落檔 ──────────────────────────────────────────────────
    rows_all: list[dict] = []
    tracks_json = []
    for i, n in enumerate(names):
        rows = sorted(per_track[n], key=lambda r: (r["start"], r["end"]))
        for k, r in enumerate(rows, 1):
            r["id"] = f"{pfx[n]}{k:04d}"
        rows_all += rows
        tracks_json.append({
            "speaker": n, "prefix": pfx[n], "file": f"tracks/{wavs[i].name}",
            "blocks": [{k: r[k] for k in ("id", "start", "end", "text", "kind",
                                          "src")} for r in rows]})
    cp["tracks"] = tracks_json
    cj.write_text(json.dumps(cp, ensure_ascii=False), encoding="utf-8")

    low_ids = {id(e["row"]) for e in low}
    id_time = {b["id"]: b["start"] for b in cp["blocks"]}
    id_time.update({gp["id"]: gp["start"] for gp in cp.get("gaps", [])})
    src_md = (sdir / args.plan).read_text(encoding="utf-8").splitlines()
    groups = carry_over_program(src_md, id_time)
    md = build_md(sdir.name, groups,
                  [r for r in rows_all if id(r) not in low_ids],
                  [r for r in rows_all if id(r) in low_ids])
    out = sdir / args.out_md
    out.write_text(md, encoding="utf-8")

    n_sp = sum(1 for r in rows_all if r["kind"] == "speech")
    print(f"[pertrack] ✓ {n_sp} 句語音 ＋ 非詞彙出聲 {len(vis)} 可見／"
          f"{len(low)} 折疊（{len(vis)/max(dur/60,1):.1f} 列/分）→ {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
