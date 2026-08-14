#!/usr/bin/env python3
"""
scripts/audio/precut.py — 「初剪」一鍵入口(卡 #679,ADR 0015 SOP 第 2 步)

    python3 scripts/audio/precut.py --session sessions/<slug> [--force] \
        [--num-speakers N] [--context context.txt] [--language zh]

拿到音檔到 cutplan 產出中間這一段,拿掉「MM 手動接力跑 ingest / transcribe /
diarize / prosody / cutplan prepare / pertrack_blocks 六支腳本」——ADR 0015(a)：
「初剪」全自動,不需要人介入,MM 的第一個接觸點是拿到 cutplan。

素材形態自動判斷路線(ADR 0014,不需要旗標不需要人選):

    session 有 tracks/  → 分軌線:ingest_tracks → transcribe → diarize --from-tracks
                          → prosody → cutplan prepare → pertrack_blocks
                          (產物:cutplan.pertrack.md)
    session 無 tracks/  → 混音線:transcribe(找 source.<ext>)→ diarize(pyannote)
                          → prosody → cutplan prepare
                          (產物:cutplan.md)

冪等/可續跑:每個階段先檢查產物是否已存在,存在就跳過(ASR/diarize 很貴,
重跑要能接續);`--force` 忽略既有產物、整條管線重來(含 ingest 自己的
`--force` 覆蓋保護)。任一階段失敗立刻停,印出卡在哪一步、怎麼手動接手
(就是印出失敗的那條指令,改完直接重跑本腳本即可自動接續,已完成的階段
不會重跑)。

只做編排,不改任何 stage 本體的邏輯或參數預設值。
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srt_utils import find_source_media  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIO_VENV = PROJECT_ROOT / ".venv-audio/bin/python"
AUDIO_DIR = Path(__file__).resolve().parent
INGEST_SCRIPT = AUDIO_DIR / "ingest_tracks.py"
TRANSCRIBE_SCRIPT = AUDIO_DIR / "transcribe_local.py"
DIARIZE_SCRIPT = AUDIO_DIR / "diarize.py"
PROSODY_SCRIPT = AUDIO_DIR / "prosody.py"
CUTPLAN_SCRIPT = AUDIO_DIR / "cutplan.py"
PERTRACK_BLOCKS_SCRIPT = AUDIO_DIR / "pertrack_blocks.py"

MATERIAL_TRACKS = "tracks"
MATERIAL_MIXDOWN = "mixdown"


@dataclass
class Stage:
    name: str
    cmd: list[str]
    done: Callable[[], bool]
    note: str = field(default="")


def detect_material(session_dir: Path) -> str:
    """ADR 0014:有 tracks/ 走分軌線,沒有走混音線——素材決定路線,不是人選路線。"""
    return MATERIAL_TRACKS if (session_dir / "tracks").is_dir() else MATERIAL_MIXDOWN


def plan_stages(session_dir: Path, args: argparse.Namespace) -> tuple[str, list[Stage]]:
    """依素材形態排出本次要跑的階段(不執行,只回傳計畫,方便測試與 dry 檢視)。

    `find_source_media` 在混音線找不到 source.<ext> 會丟 FileNotFoundError,
    呼叫端要接住轉成乾淨的 FAIL 訊息。
    """
    material = detect_material(session_dir)
    stages: list[Stage] = []

    if material == MATERIAL_TRACKS:
        ingest_cmd = [sys.executable, str(INGEST_SCRIPT), "--session", str(session_dir)]
        if args.force:
            ingest_cmd.append("--force")
        stages.append(Stage(
            name="ingest（多軌驗證 + mixdown + 逐軌 VAD）",
            cmd=ingest_cmd,
            done=lambda: all((session_dir / n).exists()
                             for n in ("source.wav", "audio16k.wav", "speakers.json")),
        ))
        media = session_dir / "source.wav"
    else:
        media = find_source_media(session_dir)

    transcribe_cmd = [str(AUDIO_VENV), str(TRANSCRIBE_SCRIPT), str(media),
                      "-o", str(session_dir / "transcript.srt"),
                      "--language", args.language]
    context = Path(args.context) if args.context else session_dir / "context.txt"
    if context.exists():
        transcribe_cmd += ["--context", str(context)]
    stages.append(Stage(
        name="transcribe（本地 whisper）",
        cmd=transcribe_cmd,
        done=lambda: (session_dir / "transcript.srt").exists()
        and (session_dir / "words.json").exists(),
    ))

    if material == MATERIAL_TRACKS:
        diarize_cmd = [sys.executable, str(DIARIZE_SCRIPT),
                       "--session", str(session_dir), "--from-tracks"]
        diarize_name = "diarize（分軌歸屬,零模型)"
    else:
        diarize_cmd = [str(AUDIO_VENV), str(DIARIZE_SCRIPT), "--session", str(session_dir)]
        if args.num_speakers:
            diarize_cmd += ["--num-speakers", str(args.num_speakers)]
        diarize_name = "diarize（講者分離,pyannote)"
    stages.append(Stage(
        name=diarize_name,
        cmd=diarize_cmd,
        done=lambda: (session_dir / "transcript.speakers.srt").exists(),
    ))

    stages.append(Stage(
        name="prosody（高昂度 + 靜音偵測）",
        cmd=[str(AUDIO_VENV), str(PROSODY_SCRIPT), "--session", str(session_dir)],
        done=lambda: (session_dir / "prosody.json").exists()
        and (session_dir / "highlights.md").exists(),
    ))

    stages.append(Stage(
        name="cutplan prepare",
        cmd=[sys.executable, str(CUTPLAN_SCRIPT), "prepare", "--session", str(session_dir)],
        done=lambda: (session_dir / "cutplan.md").exists()
        and (session_dir / "cutplan.json").exists(),
    ))

    if material == MATERIAL_TRACKS:
        stages.append(Stage(
            name="pertrack blocks（逐軌節目單）",
            cmd=[sys.executable, str(PERTRACK_BLOCKS_SCRIPT),
                "--session", str(session_dir)],
            done=lambda: (session_dir / "cutplan.pertrack.md").exists(),
        ))

    return material, stages


def run_stage(cmd: list[str]) -> subprocess.CompletedProcess:
    print(f"\n[precut] $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT))


def run_pipeline(stages: list[Stage], force: bool,
                 runner: Callable[[list[str]], object] = run_stage) -> int:
    """依序跑每個階段;`done()` 為真且非 force 就跳過。失敗立刻停,回傳非 0。"""
    for st in stages:
        if not force and st.done():
            print(f"[precut] ⏭  {st.name}:已有產物,略過(--force 重跑)")
            continue
        print(f"[precut] ▶  {st.name}")
        result = runner(st.cmd)
        rc = getattr(result, "returncode", result)
        if rc != 0:
            print(f"[precut] FAIL:{st.name} 失敗(exit {rc})", file=sys.stderr)
            print(f"  手動接手(卡在這一步):\n    {' '.join(st.cmd)}", file=sys.stderr)
            print("  修好後直接重跑 precut.py 即可自動接續(已完成的階段會跳過)",
                 file=sys.stderr)
            return 1
        print(f"[precut] ✓  {st.name}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="拿到音檔到 cutplan 產出的一鍵初剪(ADR 0015 SOP 第 2 步)")
    ap.add_argument("--session", required=True)
    ap.add_argument("--force", action="store_true", help="忽略既有產物,整條管線重來")
    ap.add_argument("--num-speakers", type=int,
                    help="混音線 diarize 已知講者人數就鎖定(分軌線用軌名=真名,用不到)")
    ap.add_argument("--context", help="context.txt 路徑(預設 session/context.txt,不存在則不帶)")
    ap.add_argument("--language", default="zh", help="transcribe 語言(預設 zh)")
    args = ap.parse_args()

    session_dir = Path(args.session).resolve()
    if not session_dir.is_dir():
        sys.exit(f"[precut] FAIL: session 不存在:{session_dir}")
    if not AUDIO_VENV.exists():
        sys.exit("[precut] FAIL: .venv-audio 不存在,轉錄/講者分離/prosody 都需要它。安裝:\n"
                 "  python3.13 -m venv .venv-audio && "
                 ".venv-audio/bin/pip install -r requirements-audio.txt")

    try:
        material, stages = plan_stages(session_dir, args)
    except FileNotFoundError as e:
        sys.exit(f"[precut] FAIL: {e}")

    label = "分軌(tracks/)" if material == MATERIAL_TRACKS else "混音(source only)"
    print(f"[precut] session={session_dir.name}  素材形態={label}  {len(stages)} 個階段")

    rc = run_pipeline(stages, args.force)
    if rc != 0:
        sys.exit(rc)

    plan = session_dir / ("cutplan.pertrack.md" if material == MATERIAL_TRACKS else "cutplan.md")
    print(f"\n[precut] ✅ 初剪完成 → {plan}")
    print(f"[precut] 下一步(SOP 第 3 步):讀 {plan.name} 人審勾選/理由,"
         "完稿後跑 cut.py 出片"
         + (f"（--plan {plan.name}）" if material == MATERIAL_TRACKS else ""))


if __name__ == "__main__":
    main()
