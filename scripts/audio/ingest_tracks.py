#!/usr/bin/env python3
"""
scripts/audio/ingest_tracks.py — 多軌錄音進料端 v1(卡 #565,ADR 0003)

任何 python 可跑(純 stdlib + ffmpeg,零重依賴零 LLM):
    python3 scripts/audio/ingest_tracks.py --session sessions/<slug> [--force] \
        [--silence-db -40] [--min-speech 0.25] [--min-gap 0.35]

目錄慣例:sessions/<slug>/tracks/<Speaker>.wav,檔名=speaker 名。
偵測到 tracks/ 即多軌模式;沒有 tracks/ 就是單軌,明確報告後 exit 0(零影響)。

Flow(ADR 0003 進料端三步):
    1. 驗證各軌 sample rate / 位深 / 聲道一致,長度一致(同機錄音天然
       sample-aligned;差異 > 0.1s 直接 FAIL 列出各軌長度,不靜默)
    2. ffmpeg mixdown:等權混音 → source.wav(保留原始 SR/位深)
       + audio16k.wav(16kHz mono,與 diarize.ensure_wav 同規格,下游分析線照舊)
    3. 每軌能量 VAD(短窗 RMS,純 stdlib)→ speakers.json
       schema 與 diarize.py 相容(model/generated_at/elapsed_secs/num_speakers/
       speakers/turns),speaker 直接用軌名=真名 — 哪軌有能量就是誰,
       diarization 升級為 ground truth,不再靠 pyannote 聲紋猜測。

已存在 source.wav / audio16k.wav / speakers.json 時要 --force 才覆蓋。
產物皆落 sessions/<slug>/,不進版控(sessions/ 已 gitignore)。
session.py wiring 是 follow-up,本腳本先可獨立手跑。
"""

import argparse
import datetime as dt
import json
import math
import subprocess
import sys
import time
import wave
from pathlib import Path

LENGTH_TOL_SECS = 0.1          # 各軌長度差容忍(同機錄音應相同)
VAD_WIN_SECS = 0.03            # RMS 短窗(30ms,非重疊)
VAD_STRIDE_SAMPLES = 96        # 每窗最多取樣點數(subsample,控 python 迴圈成本)
OUTPUTS = ("source.wav", "audio16k.wav", "speakers.json")
PCM_CODEC = {2: "pcm_s16le", 3: "pcm_s24le", 4: "pcm_s32le"}


def die(msg: str) -> None:
    print(f"[ingest] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def probe_tracks(tracks: list[Path]) -> dict[str, dict]:
    """讀各軌 wav header → {speaker: {sr, sampwidth, channels, duration}}。"""
    info = {}
    for p in sorted(tracks):
        try:
            with wave.open(str(p), "rb") as wf:
                info[p.stem] = {
                    "file": p,
                    "sr": wf.getframerate(),
                    "sampwidth": wf.getsampwidth(),
                    "channels": wf.getnchannels(),
                    "duration": wf.getnframes() / wf.getframerate(),
                }
        except (wave.Error, EOFError) as e:
            die(f"{p.name} 不是可讀的 wav({e})")
    return info


def validate_tracks(info: dict[str, dict]) -> None:
    """sample rate / 位深 / 聲道 / 長度一致性;不一致列出全部,不靜默。"""

    def check_same(key: str, label: str) -> None:
        vals = {name: t[key] for name, t in info.items()}
        if len(set(vals.values())) > 1:
            listing = ", ".join(f"{n}={v}" for n, v in vals.items())
            die(f"各軌 {label} 不一致:{listing}")

    check_same("sr", "sample rate")
    check_same("sampwidth", "位深(sampwidth)")
    check_same("channels", "聲道數")

    durs = {name: t["duration"] for name, t in info.items()}
    spread = max(durs.values()) - min(durs.values())
    if spread > LENGTH_TOL_SECS:
        listing = ", ".join(f"{n}={d:.3f}s" for n, d in durs.items())
        die(f"各軌長度不一致(差 {spread:.3f}s > {LENGTH_TOL_SECS}s;"
            f"同機錄音應相同):{listing}")


def mixdown(info: dict[str, dict], session_dir: Path) -> None:
    """等權混音 → source.wav(保留原始 SR/位深)→ audio16k.wav(16k mono)。"""
    tracks = [t["file"] for t in info.values()]
    first = next(iter(info.values()))
    codec = PCM_CODEC.get(first["sampwidth"])
    if codec is None:
        die(f"不支援的位深 sampwidth={first['sampwidth']}(支援 16/24/32-bit PCM)")

    source = session_dir / "source.wav"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for p in tracks:
        cmd += ["-i", str(p)]
    if len(tracks) > 1:
        # amix 預設等權(每軌 1/N),長度已驗證一致,duration=longest 只是保險
        cmd += ["-filter_complex",
                f"amix=inputs={len(tracks)}:duration=longest"]
    cmd += ["-ar", str(first["sr"]), "-c:a", codec, str(source)]
    print(f"[ingest] mixdown {len(tracks)} 軌 → source.wav "
          f"({first['sr']} Hz, {first['sampwidth'] * 8}-bit)")
    subprocess.run(cmd, check=True)

    # 與 diarize.ensure_wav 同規格,下游(轉錄/diarize/prosody)直接重用
    wav16k = session_dir / "audio16k.wav"
    print("[ingest] source.wav → audio16k.wav (16kHz mono)")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav16k)],
        check=True)


def energy_vad(wav_path: Path, speaker: str, silence_db: float = -40.0,
               min_speech: float = 0.25, min_gap: float = 0.35) -> list[dict]:
    """單軌能量 VAD:短窗 RMS(dBFS)超過門檻=在講話。純 stdlib。

    每窗只 subsample 最多 VAD_STRIDE_SAMPLES 點算 RMS(參考 render_cut.py
    refine_boundaries 的短窗讀法),長音檔也跑得動。相鄰 speech 段間隔
    < min_gap 合併;短於 min_speech 的段丟棄。回傳 diarize 相容 turns。
    """
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        nch = wf.getnchannels()
        n_total = wf.getnframes()
        full_scale = float(2 ** (8 * sw - 1))
        win_frames = max(1, int(VAD_WIN_SECS * sr))
        stride = max(1, (win_frames * nch) // VAD_STRIDE_SAMPLES)

        spans: list[list[float]] = []   # [start, end) of active windows
        pos = 0
        while pos < n_total:
            buf = wf.readframes(win_frames)
            if not buf:
                break
            acc, cnt = 0.0, 0
            for off in range(0, len(buf) - sw + 1, stride * sw):
                v = int.from_bytes(buf[off:off + sw], "little", signed=True)
                acc += v * v
                cnt += 1
            rms = math.sqrt(acc / cnt) / full_scale if cnt else 0.0
            db = 20 * math.log10(rms) if rms > 0 else -120.0
            if db >= silence_db:
                t0, t1 = pos / sr, min(pos + win_frames, n_total) / sr
                if spans and t0 - spans[-1][1] < min_gap:
                    spans[-1][1] = t1
                else:
                    spans.append([t0, t1])
            pos += win_frames

    return [{"start": round(a, 3), "end": round(b, 3), "speaker": speaker}
            for a, b in spans if b - a >= min_speech]


def main():
    ap = argparse.ArgumentParser(
        description="多軌錄音進料端 v1:tracks/ 驗證 + mixdown + 每軌 VAD(ADR 0003)")
    ap.add_argument("--session", required=True, help="sessions/<slug> 目錄")
    ap.add_argument("--force", action="store_true",
                    help="覆蓋既有 source.wav / audio16k.wav / speakers.json")
    ap.add_argument("--silence-db", type=float, default=-40.0,
                    help="RMS 低於此 dBFS 視為靜音(預設 -40)")
    ap.add_argument("--min-speech", type=float, default=0.25,
                    help="最短講話段秒數,短於此丟棄(預設 0.25)")
    ap.add_argument("--min-gap", type=float, default=0.35,
                    help="間隔小於此秒數的講話段合併(預設 0.35)")
    args = ap.parse_args()

    session_dir = Path(args.session).resolve()
    if not session_dir.is_dir():
        print(f"session 不存在: {session_dir}", file=sys.stderr)
        sys.exit(1)

    tracks_dir = session_dir / "tracks"
    if not tracks_dir.is_dir():
        print("[ingest] 單軌模式(無 tracks/),不需 ingest;照舊走 source.wav")
        return

    track_files = sorted(p for p in tracks_dir.glob("*.wav") if p.is_file())
    if not track_files:
        die(f"{tracks_dir} 存在但沒有 .wav 軌(檔名慣例:tracks/<Speaker>.wav)")

    existing = [n for n in OUTPUTS if (session_dir / n).exists()]
    if existing and not args.force:
        die(f"已存在 {', '.join(existing)};要覆蓋請加 --force")

    t0 = time.time()
    info = probe_tracks(track_files)
    validate_tracks(info)
    names = list(info)
    print(f"[ingest] {len(names)} 軌 ({', '.join(names)}),"
          f"{next(iter(info.values()))['duration']:.1f}s")

    mixdown(info, session_dir)

    turns: list[dict] = []
    for name, t in info.items():
        tt = energy_vad(t["file"], name, args.silence_db,
                        args.min_speech, args.min_gap)
        print(f"[ingest] VAD {name}: {len(tt)} turns,"
              f"講話 {sum(x['end'] - x['start'] for x in tt):.1f}s")
        turns.extend(tt)
    turns.sort(key=lambda t: t["start"])
    elapsed = round(time.time() - t0, 1)

    # schema 與 diarize.py 相容(superset):下游把它當 diarization ground truth
    (session_dir / "speakers.json").write_text(json.dumps({
        "model": "ingest-tracks/energy-vad-v1",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "elapsed_secs": elapsed,
        "num_speakers": len(names),
        "speakers": sorted(names),
        "turns": turns,
        "source": "tracks",
        "tracks": {name: {"file": f"tracks/{t['file'].name}",
                          "duration_secs": round(t["duration"], 3),
                          "sample_rate": t["sr"]}
                   for name, t in info.items()},
        "vad_params": {"silence_db": args.silence_db,
                       "win_secs": VAD_WIN_SECS,
                       "min_speech_secs": args.min_speech,
                       "min_gap_secs": args.min_gap},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ingest] speakers.json: {len(turns)} turns, {len(names)} speakers "
          f"({', '.join(sorted(names))}), {elapsed}s — speaker=軌名,"
          f"已是真名,不需 pyannote/命名 marker")


if __name__ == "__main__":
    main()
