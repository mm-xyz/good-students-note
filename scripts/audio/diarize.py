#!/usr/bin/env python3
"""
scripts/audio/diarize.py — Speaker diarization stage(pyannote-audio,全本地)

用 .venv-audio 的 python 跑(重依賴隔離,見 requirements-audio.txt):
    .venv-audio/bin/python scripts/audio/diarize.py --session sessions/<slug> \
        [--num-speakers N] [--min-speakers N] [--max-speakers N] [--device auto|mps|cpu]

    # speakers_map.json 填好人名後,重新輸出帶人名的 SRT(不重跑模型,任何 python 可跑):
    python3 scripts/audio/diarize.py --session sessions/<slug> --apply-map

Flow:
    1. ffmpeg: source.<ext> → audio16k.wav(16kHz mono,全長不切 chunk — 時間軸要全域)
    2. pyannote/speaker-diarization-community-1(gated model,.env 需 HF_TOKEN)
    3. speakers.json:diarization turns(start/end/speaker)
    4. 對齊 cleaned.srt|transcript.srt(max-overlap)→ transcript.speakers.srt([S1] 前綴)
    5. 寫 .speaker_naming_pending.json marker:S1/S2 → 人名的判斷交對話 agent /MM
       (Engine Routing 原則 5:本腳本零 LLM 呼叫)

產物皆落 sessions/<slug>/,不進版控(sessions/ 已 gitignore)。
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import parse_srt, write_srt, pick_transcript, find_source_media, fmt_mmss, rel

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODEL = "pyannote/speaker-diarization-community-1"
WAV_NAME = "audio16k.wav"


def load_env_token() -> str | None:
    """HF_TOKEN 來源:process env > 專案根 .env。"""
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"]
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("HF_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def ensure_wav(session_dir: Path) -> Path:
    """source.<ext> → audio16k.wav(冪等:已存在就重用;prosody 也吃同一份)。"""
    wav = session_dir / WAV_NAME
    if wav.exists() and wav.stat().st_size > 0:
        print(f"[diarize] reuse {wav.name}")
        return wav
    src = find_source_media(session_dir)
    print(f"[diarize] ffmpeg {src.name} → {WAV_NAME} (16kHz mono)")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)],
        check=True)
    return wav


def pick_device(explicit: str) -> str:
    import torch
    if explicit != "auto":
        return explicit
    return "mps" if torch.backends.mps.is_available() else "cpu"


def run_diarization(wav: Path, args) -> list[dict]:
    token = load_env_token()
    if not token:
        print("[diarize] ERROR: 找不到 HF_TOKEN。前置作業:\n"
              "  1. 在 https://hf.co/pyannote/speaker-diarization-community-1 同意條款\n"
              "  2. https://hf.co/settings/tokens 開 read token\n"
              "  3. 寫進專案根 .env:HF_TOKEN=hf_...", file=sys.stderr)
        sys.exit(1)

    import torch
    from pyannote.audio import Pipeline

    device = pick_device(args.device)
    print(f"[diarize] loading {args.model} (device={device})")
    pipeline = Pipeline.from_pretrained(args.model, token=token)

    kw = {}
    if args.num_speakers:
        kw["num_speakers"] = args.num_speakers
    if args.min_speakers:
        kw["min_speakers"] = args.min_speakers
    if args.max_speakers:
        kw["max_speakers"] = args.max_speakers

    def _run(dev: str):
        pipeline.to(torch.device(dev))
        print(f"[diarize] running on {dev} … (長音檔要幾分鐘到幾十分鐘)")
        return pipeline(str(wav), **kw)

    try:
        output = _run(device)
    except Exception as e:
        if device == "mps":
            print(f"[diarize] MPS failed ({e.__class__.__name__}: {e}); retry on cpu",
                  file=sys.stderr)
            output = _run("cpu")
        else:
            raise

    # pyannote 4.x: output.speaker_diarization;3.x: output 本身是 Annotation
    ann = getattr(output, "speaker_diarization", output)
    turns = []
    for turn, _, label in ann.itertracks(yield_label=True):
        turns.append({"start": round(turn.start, 3), "end": round(turn.end, 3),
                      "speaker": label})
    turns.sort(key=lambda t: t["start"])
    return turns


def relabel_turns(turns: list[dict]) -> list[dict]:
    """SPEAKER_00/01 → S1/S2(依總發言時長排序,S1=講最多的人,穩定可讀)。"""
    total = {}
    for t in turns:
        total[t["speaker"]] = total.get(t["speaker"], 0) + (t["end"] - t["start"])
    order = sorted(total, key=total.get, reverse=True)
    mapping = {raw: f"S{i + 1}" for i, raw in enumerate(order)}
    for t in turns:
        t["speaker"] = mapping[t["speaker"]]
    return turns


def assign_speakers(cues: list[dict], turns: list[dict]) -> list[dict]:
    """每個 SRT segment 指給 overlap 最大的 speaker;零 overlap 找最近的 turn。"""
    for c in cues:
        best, best_ov = None, 0.0
        for t in turns:
            ov = min(c["end"], t["end"]) - max(c["start"], t["start"])
            if ov > best_ov:
                best, best_ov = t["speaker"], ov
        if best is None and turns:
            mid = (c["start"] + c["end"]) / 2
            best = min(turns, key=lambda t: min(abs(t["start"] - mid),
                                                abs(t["end"] - mid)))["speaker"]
        c["speaker"] = best or "S1"
        c["speaker_overlap"] = round(best_ov, 3)
    return cues


def write_naming_marker(session_dir: Path, speakers: list[str]) -> None:
    marker = session_dir / ".speaker_naming_pending.json"
    ctx = session_dir / "context.txt"
    marker.write_text(json.dumps({
        "stage": "speaker-naming",
        "input_file": rel(session_dir / "transcript.speakers.srt", PROJECT_ROOT),
        "output_file": rel(session_dir / "speakers_map.json", PROJECT_ROOT),
        "speakers": speakers,
        "context_file": rel(ctx, PROJECT_ROOT) if ctx.exists() else None,
        "instructions": (
            "Speaker 命名待對話 agent(或 MM)接手,零 API 呼叫(原則 5)。"
            "讀 transcript.speakers.srt 開頭 10 分鐘 + context.txt 的人名線索,"
            "判斷 S1/S2/… 各是誰,寫 speakers_map.json(例:{\"S1\": \"語嫣\"});"
            "沒把握的標 \"S2\": \"S2\" 原樣保留、向 MM 確認。"
            "然後跑 `python3 scripts/audio/diarize.py --session <dir> --apply-map` "
            "重新輸出帶人名 SRT,成功會自動刪本 marker。"),
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[diarize] speaker 命名待接手: {rel(marker, PROJECT_ROOT)}")


def apply_map(session_dir: Path) -> None:
    """speakers_map.json → 重寫 transcript.speakers.srt 的 speaker 前綴。"""
    map_path = session_dir / "speakers_map.json"
    srt_path = session_dir / "transcript.speakers.srt"
    if not map_path.exists():
        print(f"[diarize] {map_path} 不存在;先填人名對照", file=sys.stderr)
        sys.exit(1)
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    cues = parse_srt(srt_path)
    for c in cues:
        c["speaker"] = mapping.get(c["speaker"], c["speaker"])
    write_srt(cues, srt_path)
    # speakers.json 同步換名,下游(prosody/cutplan)看到的是同一套標籤
    sj = session_dir / "speakers.json"
    if sj.exists():
        data = json.loads(sj.read_text(encoding="utf-8"))
        for t in data.get("turns", []):
            t["speaker"] = mapping.get(t["speaker"], t["speaker"])
        data["speakers_map"] = mapping
        sj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    marker = session_dir / ".speaker_naming_pending.json"
    if marker.exists():
        marker.unlink()
    print(f"[diarize] 已套用人名: {sorted(set(mapping.values()))} → {srt_path.name}")


def main():
    ap = argparse.ArgumentParser(description="Speaker diarization stage(pyannote,全本地)")
    ap.add_argument("--session", required=True, help="sessions/<slug> 目錄")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--num-speakers", type=int, help="已知人數就鎖定(準確度最好)")
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    ap.add_argument("--device", choices=["auto", "mps", "cpu"], default="auto")
    ap.add_argument("--apply-map", action="store_true",
                    help="只套 speakers_map.json 人名,不重跑模型")
    args = ap.parse_args()

    session_dir = Path(args.session).resolve()
    if not session_dir.is_dir():
        print(f"session 不存在: {session_dir}", file=sys.stderr)
        sys.exit(1)

    if args.apply_map:
        apply_map(session_dir)
        return

    import time
    t0 = time.time()
    wav = ensure_wav(session_dir)
    turns = relabel_turns(run_diarization(wav, args))
    speakers = sorted({t["speaker"] for t in turns}, key=lambda s: int(s[1:]))
    elapsed = round(time.time() - t0, 1)

    (session_dir / "speakers.json").write_text(json.dumps({
        "model": args.model,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "elapsed_secs": elapsed,
        "num_speakers": len(speakers),
        "speakers": speakers,
        "turns": turns,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[diarize] speakers.json: {len(turns)} turns, "
          f"{len(speakers)} speakers ({', '.join(speakers)}), {elapsed}s")

    srt_src = pick_transcript(session_dir)
    cues = assign_speakers(parse_srt(srt_src), turns)
    out_srt = session_dir / "transcript.speakers.srt"
    write_srt(cues, out_srt)
    switches = sum(1 for a, b in zip(cues, cues[1:]) if a["speaker"] != b["speaker"])
    print(f"[diarize] {out_srt.name}: {len(cues)} segments(來源 {srt_src.name}),"
          f"{switches} 次換手,首段 {fmt_mmss(cues[0]['start']) if cues else '-'}")

    if len(speakers) > 1:
        write_naming_marker(session_dir, speakers)
    else:
        print("[diarize] 單一 speaker,略過命名 marker")


if __name__ == "__main__":
    main()
