#!/usr/bin/env python3
"""
scripts/audio/prosody.py — 聲音情緒/精華段分析 stage(librosa,全本地零 LLM)

用 .venv-audio 的 python 跑:
    .venv-audio/bin/python scripts/audio/prosody.py --session sessions/<slug> \
        [--top-percent 10] [--silence-db -38] [--min-silence 0.35]

對每個 SRT segment 抽聲學特徵,合成「高昂度」(excitement)分數:
    能量   RMS dB(講得大聲)                weight 0.35
    音高   F0 p75(pyin,講得高亢)          weight 0.35
    音域   F0 range p90-p10(起伏大)        weight 0.10
    語速   中文字/秒(講得快)               weight 0.20

特徵按 speaker 做 z-score 正規化(每個人基準音高/音量不同,「高昂」是相對
自己的平常狀態),再映射到 0-100。有 speakers.json 就分軌,沒有就全局。

另做真實靜音偵測(RMS 門檻),取代 SRT gap proxy;silences 供 cut stage
把剪點 snap 到靜音處。

產物:
    prosody.json   — per-segment 特徵+分數、silences、per-speaker 統計
    highlights.md  — 分數前 N%(預設 10%)段落,帶起訖時間碼(精華/預告/shorts 候選)
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, pick_transcript, fmt_mmss
from diarize import ensure_wav  # 共用 audio16k.wav(冪等)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

WEIGHTS = {"rms_db": 0.35, "f0_p75": 0.35, "f0_range": 0.10, "rate": 0.20}
SR = 16000


def chinese_chars(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def extract_features(wav_path: Path, cues: list[dict], silence_db: float,
                     min_silence: float) -> tuple[list[dict], list[dict]]:
    import librosa
    import numpy as np

    print(f"[prosody] loading {wav_path.name} …")
    y, sr = librosa.load(str(wav_path), sr=SR, mono=True)
    hop = 512
    frame = 2048

    print("[prosody] RMS energy …")
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=1.0)
    times = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=hop)

    print("[prosody] F0 (pyin) … 40 分鐘音檔約需幾分鐘")
    f0, voiced_flag, _ = librosa.pyin(
        y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"),
        sr=sr, frame_length=frame, hop_length=hop)

    def seg_slice(arr, start, end):
        i0 = int(start * sr / hop)
        i1 = max(i0 + 1, int(end * sr / hop))
        return arr[i0:i1]

    for c in cues:
        r = seg_slice(rms_db, c["start"], c["end"])
        f = seg_slice(f0, c["start"], c["end"])
        v = seg_slice(voiced_flag, c["start"], c["end"])
        fv = f[(v == True) & ~np.isnan(f)]  # noqa: E712 — voiced_flag 是 ndarray
        dur = max(0.2, c["end"] - c["start"])
        c["feat"] = {
            "rms_db": float(np.percentile(r, 75)) if len(r) else -80.0,
            "f0_p75": float(np.percentile(fv, 75)) if len(fv) else float("nan"),
            "f0_range": (float(np.percentile(fv, 90) - np.percentile(fv, 10))
                         if len(fv) >= 4 else float("nan")),
            "rate": chinese_chars(c["text"]) / dur,
            "voiced_ratio": float(np.mean(v)) if len(v) else 0.0,
        }

    # ── 真實靜音偵測(供 cut stage snap 剪點)──
    quiet = rms_db < silence_db
    silences = []
    run_start = None
    for t, q in zip(times, quiet):
        if q and run_start is None:
            run_start = t
        elif not q and run_start is not None:
            if t - run_start >= min_silence:
                silences.append({"start": round(run_start, 3), "end": round(t, 3)})
            run_start = None
    if run_start is not None and times[-1] - run_start >= min_silence:
        silences.append({"start": round(run_start, 3), "end": round(float(times[-1]), 3)})
    print(f"[prosody] silences: {len(silences)} 段(< {silence_db} dB,"
          f" ≥ {min_silence}s)")
    return cues, silences


def zscore_by_speaker(cues: list[dict]) -> dict:
    """按 speaker 分組 z-score;無 speaker 標籤 → 全部同組('*')。回傳 per-speaker 統計。"""
    import numpy as np

    groups: dict[str, list[dict]] = {}
    for c in cues:
        groups.setdefault(c.get("speaker") or "*", []).append(c)

    stats = {}
    for spk, seg in groups.items():
        stats[spk] = {"segments": len(seg)}
        for key in WEIGHTS:
            vals = np.array([s["feat"][key] for s in seg], dtype=float)
            ok = ~np.isnan(vals)
            mean = float(vals[ok].mean()) if ok.any() else 0.0
            std = float(vals[ok].std()) or 1.0
            stats[spk][key] = {"mean": round(mean, 2), "std": round(std, 2)}
            for s, v in zip(seg, vals):
                z = 0.0 if np.isnan(v) else (v - mean) / std
                s.setdefault("z", {})[key] = round(float(np.clip(z, -3, 3)), 3)

    for c in cues:
        combined = sum(WEIGHTS[k] * c["z"][k] for k in WEIGHTS)
        # 50 為個人平常水位,+1σ 的綜合高昂 ≈ 68 分;clamp 0-100
        c["excitement"] = round(max(0.0, min(100.0, 50 + 18 * combined)), 1)
        c["combined_z"] = round(combined, 3)
    return stats


def write_highlights(cues: list[dict], out_path: Path, top_percent: float,
                     min_score: float = 60.0) -> int:
    n = max(3, round(len(cues) * top_percent / 100))
    ranked = sorted(cues, key=lambda c: c["excitement"], reverse=True)[:n]
    picked = sorted([c for c in ranked if c["excitement"] >= min_score],
                    key=lambda c: c["start"])
    lines = [
        "# 高昂精華段(prosody 自動偵測)",
        "",
        f"> 依聲學高昂度(能量/音高/音域/語速,按 speaker 正規化)取前 "
        f"{top_percent:.0f}% 段落,分數 ≥ {min_score:.0f}。",
        "> 用途:精華/預告/shorts 候選;剪輯時配合 cutplan 保留這些段。",
        "",
    ]
    for c in picked:
        spk = f"[{c['speaker']}] " if c.get("speaker") else ""
        lines.append(f"- **[{fmt_mmss(c['start'])}–{fmt_mmss(c['end'])}]** "
                     f"(score {c['excitement']:.0f}) {spk}{c['text']}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(picked)


def main():
    ap = argparse.ArgumentParser(description="聲音情緒/精華段分析(librosa,全本地)")
    ap.add_argument("--session", required=True, help="sessions/<slug> 目錄")
    ap.add_argument("--top-percent", type=float, default=10.0)
    ap.add_argument("--silence-db", type=float, default=-38.0,
                    help="RMS 低於此 dB 視為靜音(預設 -38)")
    ap.add_argument("--min-silence", type=float, default=0.35,
                    help="最短靜音長度秒(預設 0.35)")
    args = ap.parse_args()

    session_dir = Path(args.session).resolve()
    if not session_dir.is_dir():
        print(f"session 不存在: {session_dir}", file=sys.stderr)
        sys.exit(1)

    import time
    t0 = time.time()
    wav = ensure_wav(session_dir)

    # 有 diarize 產物就用帶 speaker 的 SRT(z-score 分軌),沒有就退回一般 SRT
    spk_srt = session_dir / "transcript.speakers.srt"
    srt_src = spk_srt if spk_srt.exists() else pick_transcript(session_dir)
    cues = parse_srt(srt_src)
    print(f"[prosody] {len(cues)} segments(來源 {srt_src.name},"
          f"{'含' if spk_srt.exists() else '無'} speaker 標籤)")

    cues, silences = extract_features(wav, cues, args.silence_db, args.min_silence)
    stats = zscore_by_speaker(cues)
    elapsed = round(time.time() - t0, 1)

    out = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_srt": srt_src.name,
        "elapsed_secs": elapsed,
        "weights": WEIGHTS,
        "speaker_stats": stats,
        "silence_params": {"db": args.silence_db, "min_secs": args.min_silence},
        "silences": silences,
        "segments": [{
            "idx": c["idx"], "start": c["start"], "end": c["end"],
            "speaker": c.get("speaker"), "text": c["text"],
            **c["feat"], "z": c["z"],
            "excitement": c["excitement"],
        } for c in cues],
    }
    pj = session_dir / "prosody.json"
    pj.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    hl = session_dir / "highlights.md"
    n_hl = write_highlights(cues, hl, args.top_percent)
    top = max(cues, key=lambda c: c["excitement"]) if cues else None
    print(f"[prosody] prosody.json + highlights.md({n_hl} 段精華,{elapsed}s)")
    if top:
        print(f"[prosody] 最高昂: [{fmt_mmss(top['start'])}] "
              f"score {top['excitement']:.0f} — {top['text'][:40]}")


if __name__ == "__main__":
    main()
