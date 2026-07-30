#!/usr/bin/env python3
"""
scripts/session.py — 統籌「任何輸入 → markdown 知識庫」的整條 pipeline

Session 容器把所有產物與輸入都綁在 sessions/<slug>/,避免汙染專案根目錄。
輸入依副檔名自動偵測分流:音檔/影片走既有轉錄線(零改動),.pdf/.epub/.txt
走文件抽取線(scripts/doc/extract.py,確定性、零 LLM);兩線最後都匯流到
共用的理解層(enhance/notes)。

用法(音檔/影片線,零改動):
    python3 scripts/session.py new <audio_or_video_file> \\
        [--context "<data-or-file>"] \\
        [--domain <name>] \\
        [--identity "<身份>"] \\
        [--skip-phase-b]     # 僅跑到 Phase A,產出 cleaned.srt 不產 cleaned.md
        [--structured-srt]   # 另外產一份 transcript.cleaned.srt(結構保留型校稿)

用法(文件線 .pdf/.epub/.txt):
    python3 scripts/session.py new <doc_file> [--vlm] [--stop-at ...]
        # extract.py 產出已是乾淨結構化繁體文本,直接當 cleaned.md,跳過
        # transcribe/phase-a/phase-b(ASR 專屬清理對已乾淨文件無意義)。
        # --vlm(僅 pdf 有意義):額外跑 figures.py 渲染圖表進 images/,
        # 再走既有 --images 的圖片理解(describe_images.py)+
        # 自動插圖(insert_images.py)marker 呼叫路徑。

Flow(音檔/影片線):
    1. Build slug YYYY-MM-DD_<sanitized-filename>, mkdir sessions/<slug>/
    2. symlink audio → source.<ext>; write context.txt (and metadata.json skeleton)
    3. groq_transcribe.py → transcript.srt  (IMMUTABLE)
    4. qaqc_srt.py --domain <name> → cleaned.srt  (Phase A only)
    5. phase B merged → cleaned.md
    6. (optional) qaqc_srt.py --structured → transcript.cleaned.srt  (timecode-safe)
    7. (optional) identity-based notes_<identity>.md
    8. Write final metadata.json (ratios, timings, typo hits, etc.)

Flow(文件線,取代步驟 3-5):
    3'. scripts/doc/extract.py → cleaned.md  直接產出(略過 transcribe/phase-a/phase-b)
    3.5' (--vlm 且為 pdf) scripts/doc/figures.py → images/ + doc_figures.json,
         再寫 .images_pending.json / .image_insert_pending.json marker
         (同 --images 既有呼叫路徑)
    之後與音檔線共用步驟 7-8(enhance/notes/metadata)
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "sessions"
GROQ_SCRIPT = PROJECT_ROOT / ".claude/skills/good-student-notes/scripts/groq_transcribe.py"
QAQC_SCRIPT = PROJECT_ROOT / "SRT/qaqc_srt.py"
PHASE_B_SCRIPT = PROJECT_ROOT / "scripts/qaqc_phase_b.py"
NORMALIZE_SCRIPT = PROJECT_ROOT / "scripts/normalize_punctuation.py"

# 音訊分析線(diarize/prosody/cut)— 重依賴隔離在 .venv-audio(見 requirements-audio.txt)
AUDIO_VENV = PROJECT_ROOT / ".venv-audio/bin/python"
DIARIZE_SCRIPT = PROJECT_ROOT / "scripts/audio/diarize.py"
PROSODY_SCRIPT = PROJECT_ROOT / "scripts/audio/prosody.py"
CUTPLAN_SCRIPT = PROJECT_ROOT / "scripts/audio/cutplan.py"
LOCAL_ASR_SCRIPT = PROJECT_ROOT / "scripts/audio/transcribe_local.py"

# frames 線(invisible-context 併入):影片抽幀 → VLM 篩圖 → OCR → compose
FRAMES_EXTRACT_SCRIPT = PROJECT_ROOT / "scripts/frames/extract.py"
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")

# 文件輸入線(PDF/EPUB/TXT)— Phase-1 確定性抽取,重依賴(fitz/ebooklib/lxml/
# chardet/opencc)隔離在 .venv-doc(見 requirements-doc.txt)。extract.py 產出
# 已是乾淨結構化繁體文本,直接當 cleaned.md,跳過 ASR 專屬的 transcribe/phase-a/
# phase-b;下游 enhance/notes 與音檔線共用(理解跨線共用、ASR 清理不共用)。
DOC_EXTS = {".pdf", ".epub", ".txt"}
DOC_VENV = PROJECT_ROOT / ".venv-doc/bin/python"
DOC_EXTRACT_SCRIPT = PROJECT_ROOT / "scripts/doc/extract.py"
DOC_FIGURES_SCRIPT = PROJECT_ROOT / "scripts/doc/figures.py"


# ─── Engine routing(誰在叫我?)──────────────────────────────────────
# 規則:CLI host(Claude Code、Gemini CLI 等)用 OAuth login token 計費,絕不打
# LLM API key,Phase B/Step 3/Step 4 由對話 agent 接手。純 shell/cron 才走 API。
# 詳見 CLAUDE.md「Engine Routing」章節 + memory feedback_auth_model_split.md。

ENGINE_CHOICES = ("auto", "claude", "gemini", "copilot", "api", "none")

# host detection signals → engine。第一個命中就贏。
ENGINE_ENV_SIGNALS = (
    ("claude",  ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH")),
    ("gemini",  ("GEMINI_CLI", "GEMINI_CLI_SESSION", "GOOGLE_GEMINI_CLI")),
    ("copilot", ("GITHUB_COPILOT_CLI", "GH_COPILOT_AGENT", "COPILOT_AGENT_SESSION")),
)


def detect_engine(explicit: str | None = None) -> tuple[str, str]:
    """Return (engine, reason). Explicit non-auto value short-circuits detection."""
    if explicit and explicit != "auto":
        return explicit, f"--engine={explicit}"
    for engine, env_keys in ENGINE_ENV_SIGNALS:
        for k in env_keys:
            if os.environ.get(k):
                return engine, f"detected ${k}"
    return "unknown", "no host signal found"


def resolve_engine(args) -> str:
    """Print the routing decision and return the chosen engine.
    `unknown` defaults to refusing API calls — the user must opt in via --engine api."""
    engine, reason = detect_engine(args.engine)
    if engine == "unknown":
        print(f"[session] engine=unknown ({reason}); Phase B / Step 3 / Step 4 will be "
              f"SKIPPED to avoid burning API quotas. Pass --engine api to opt into "
              f"calling Gemini API explicitly.", file=sys.stderr)
        return "none"
    host_label = os.environ.get("AI_AGENT", "agent CLI" if engine != "api" else "Web/cron")
    print(f"[session] engine={engine} ({reason}, host={host_label})")
    return engine


# ─── Slug ───

def _slugify(name: str) -> str:
    """Convert audio filename stem into a filesystem-friendly slug."""
    s = re.sub(r"[^\w.\- ]", "", name, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip())
    return s


def build_slug(audio_path: Path, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    return f"{today.isoformat()}_{_slugify(audio_path.stem)}"


# ─── Context writer ───

def resolve_context(ctx: str | None) -> str:
    """Accept a path, a raw string, or None. Return content (possibly empty)."""
    if not ctx:
        return ""
    p = Path(ctx)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return ctx


# ─── Char metrics ───

def count_chars(text: str) -> dict:
    no_space = re.sub(r"\s+", "", text)
    chinese = re.findall(r"[一-鿿]", text)
    return {"no_space": len(no_space), "chinese": len(chinese)}


# ─── Srt parsing for metrics ───

def srt_effective_chars(srt_path: Path) -> dict:
    """Return char counts for SRT text portion only (excluding timecodes/indices)."""
    content = srt_path.read_text(encoding="utf-8")
    # Strip index lines (digits only) and timecode lines
    lines = []
    for line in content.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"\d+", s):
            continue
        if "-->" in s:
            continue
        lines.append(s)
    joined = " ".join(lines)
    return count_chars(joined)


# ─── Pipeline steps ───

def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, cwd=str(cwd or PROJECT_ROOT), check=check,
                          capture_output=False)


def new_session(args):
    # 0. Engine routing — 第一件事:確認誰在叫我,印出 routing 決策
    engine = resolve_engine(args)
    if getattr(args, "dry_run", False):
        print(f"[session] --dry-run: engine={engine}, exiting without doing work.")
        return

    audio = Path(args.audio).resolve()
    if not audio.exists():
        print(f"Audio not found: {audio}", file=sys.stderr)
        sys.exit(1)

    SESSIONS_DIR.mkdir(exist_ok=True)
    slug = build_slug(audio)
    sdir = SESSIONS_DIR / slug
    if sdir.exists():
        # If the session already exists, we do NOT overwrite its products; bail.
        print(f"Session already exists: {sdir}", file=sys.stderr)
        print("Remove it first or pick a different date.", file=sys.stderr)
        sys.exit(2)
    sdir.mkdir()

    print(f"[session] created: {sdir}")

    # 1. symlink audio
    ext = audio.suffix
    is_doc = ext.lower() in DOC_EXTS  # 副檔名自動偵測分流:文件線 vs 既有音檔/影片線
    doc_stats = None
    src_link = sdir / f"source{ext}"
    try:
        os.symlink(audio, src_link)
    except OSError:
        # Fallback to copy on filesystems that can't symlink
        import shutil
        shutil.copy2(audio, src_link)

    # 2. context.txt
    ctx_text = resolve_context(args.context)
    ctx_path = sdir / "context.txt"
    ctx_path.write_text(ctx_text, encoding="utf-8")
    print(f"[session] context.txt: {len(ctx_text)} chars / "
          f"{len(ctx_text.encode('utf-8'))} bytes")

    # 3. Metadata skeleton
    meta = {
        "session_id": slug,
        "source_audio": audio.name,
        "source_size_bytes": audio.stat().st_size,
        "source_type": "doc" if is_doc else "audio",
        "created_at": dt.date.today().isoformat(),
        "domain_candidate": args.domain,
        "identity": args.identity,
    }

    if not is_doc:
        # 4. 轉錄 → transcript.srt(IMMUTABLE)
        # 2026-07-27 MM 拍板:主線=本地 mlx-whisper(--asr local,零雲端零 key);
        # Groq 降為選配(--asr groq,要 GROQ_API_KEY)。
        t0 = time.time()
        transcript = sdir / "transcript.srt"
        asr_engine = args.asr
        if asr_engine == "local":
            if not AUDIO_VENV.exists():
                print("[session] ERROR: --asr local 需要 .venv-audio。安裝:\n"
                      "  python3.13 -m venv .venv-audio && "
                      ".venv-audio/bin/pip install -r requirements-audio.txt\n"
                      "或改用 --asr groq(需 GROQ_API_KEY)。", file=sys.stderr)
                sys.exit(3)
            cmd = [str(AUDIO_VENV), str(LOCAL_ASR_SCRIPT), str(src_link),
                   "-o", str(transcript)]
            if ctx_text:
                cmd += ["--context", str(ctx_path)]
            run(cmd)
            asr_label = "mlx-whisper large-v3-turbo (local)"
        else:  # groq
            # groq_transcribe.py signature: <media> [output_dir] [context_file]
            # We want output to be named transcript.srt (not <stem>.srt), so we handle rename.
            run(["python3", str(GROQ_SCRIPT), str(src_link), str(sdir), str(ctx_path)])
            # groq script outputs <stem>.srt — since we symlinked to source.<ext>, stem = "source"
            groq_out = sdir / "source.srt"
            if groq_out.exists():
                groq_out.rename(transcript)
            asr_label = "Groq Whisper large-v3"
        if not transcript.exists():
            print(f"[session] ERROR: {asr_engine} did not produce transcript.srt",
                  file=sys.stderr)
            meta["error"] = f"{asr_engine}_transcription_failed"
            (sdir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
            sys.exit(3)
        groq_secs = round(time.time() - t0, 1)
        print(f"[session] transcript.srt saved ({transcript.stat().st_size} bytes, "
              f"{groq_secs}s)")

        original_metrics = srt_effective_chars(transcript)

        # 5. Phase A cleanup → cleaned.srt
        cleaned_srt = sdir / "cleaned.srt"
        cmd = ["python3", str(QAQC_SCRIPT), str(transcript), "-o", str(cleaned_srt)]
        if args.domain:
            cmd += ["--domain", args.domain]
        run(cmd)

        phase_a_metrics = srt_effective_chars(cleaned_srt)

        # 6. Structured-preserving polish → transcript.cleaned.srt (optional)
        transcript_cleaned_srt = None
        if args.structured_srt:
            transcript_cleaned_srt = sdir / "transcript.cleaned.srt"
            cmd = ["python3", str(QAQC_SCRIPT), str(transcript),
                   "-o", str(transcript_cleaned_srt),
                   "--structured"]
            if args.domain:
                cmd += ["--domain", args.domain]
            if ctx_text:
                cmd += ["--context", str(ctx_path)]
            try:
                run(cmd)
            except subprocess.CalledProcessError as e:
                print(f"[session] structured polish failed: {e}", file=sys.stderr)
                transcript_cleaned_srt = None

        # 6.5 音訊分析線(diarize / prosody / cut)— 與 Phase B 平行的加值線,全本地
        # 零 LLM(pyannote + librosa);需判斷的部分(speaker 命名、剪輯提案)照原則 5
        # 由各腳本寫 marker 交對話 agent。--cut 隱含 --diarize --prosody
        # (podcast 剪輯需要 speaker 標籤 + 靜音 snap)。
        audio_stats = None
        want_diarize = args.diarize or args.cut
        want_prosody = args.prosody or args.cut
        if want_diarize or want_prosody:
            audio_stats = {}
            if not AUDIO_VENV.exists():
                print("[session] ⚠ .venv-audio 不存在,音訊分析線(diarize/prosody/cut)跳過。"
                      "安裝:\n  python3.13 -m venv .venv-audio && "
                      ".venv-audio/bin/pip install -r requirements-audio.txt",
                      file=sys.stderr)
                audio_stats["status"] = "skipped_no_venv"
            else:
                if want_diarize:
                    cmd = [str(AUDIO_VENV), str(DIARIZE_SCRIPT), "--session", str(sdir)]
                    if args.num_speakers:
                        cmd += ["--num-speakers", str(args.num_speakers)]
                    try:
                        run(cmd)
                        audio_stats["diarize"] = "done_naming_pending"
                    except subprocess.CalledProcessError as e:
                        print(f"[session] diarize failed: {e} — 續跑其餘 pipeline",
                              file=sys.stderr)
                        audio_stats["diarize"] = {"status": "error", "error": str(e)}
                if want_prosody:
                    try:
                        run([str(AUDIO_VENV), str(PROSODY_SCRIPT), "--session", str(sdir)])
                        audio_stats["prosody"] = "done"
                    except subprocess.CalledProcessError as e:
                        print(f"[session] prosody failed: {e} — 續跑其餘 pipeline",
                              file=sys.stderr)
                        audio_stats["prosody"] = {"status": "error", "error": str(e)}
                if args.cut:
                    try:
                        run(["python3", str(CUTPLAN_SCRIPT), "prepare",
                             "--session", str(sdir)])
                        audio_stats["cutplan"] = "pending_agent_proposal"
                    except subprocess.CalledProcessError as e:
                        print(f"[session] cutplan failed: {e}", file=sys.stderr)
                        audio_stats["cutplan"] = {"status": "error", "error": str(e)}

        # 6.6 frames 線(影片抽幀;invisible-context 併入,--frames 才啟用)
        # 抽幀後的 screen(LM Studio VLM)/ocr/compose 依原則 9 逐步執行,
        # 由使用者或對話 agent 分階段跑(見 scripts/frames/ 各檔 docstring)。
        frames_stats = None
        if args.frames:
            if ext.lower() not in VIDEO_EXTS:
                print(f"[session] --frames 需要影片輸入({ext} 不是影片),跳過", file=sys.stderr)
                frames_stats = {"status": "skipped_not_video"}
            elif not AUDIO_VENV.exists():
                print("[session] ⚠ .venv-audio 不存在,frames 線跳過(安裝見 requirements-audio.txt)",
                      file=sys.stderr)
                frames_stats = {"status": "skipped_no_venv"}
            else:
                try:
                    run([str(AUDIO_VENV), str(FRAMES_EXTRACT_SCRIPT), "--session", str(sdir)])
                    frames_stats = {"status": "extracted"}
                    print("[session] frames 下一步(逐步跑,原則 9):\n"
                          f"  .venv-audio/bin/python scripts/frames/screen.py {slug}\n"
                          f"  .venv-audio/bin/python scripts/frames/ocr.py {slug}\n"
                          f"  python3 scripts/frames/compose.py {slug} --course <課程名>")
                except subprocess.CalledProcessError as e:
                    print(f"[session] frames extract failed: {e}", file=sys.stderr)
                    frames_stats = {"status": "error", "error": str(e)}

        # 7. Phase B merged → cleaned.md
        # (skipped if --stop-at transcribe/phase-a OR --skip-phase-b)
        cleaned_md = sdir / "cleaned.md"
        phase_b_stats = None
        do_phase_b = (not args.skip_phase_b
                      and args.stop_at not in ("transcribe", "phase-a"))
        if do_phase_b:
            plain = _srt_to_plain(cleaned_srt)
            in_m = count_chars(plain)

            if engine in ("claude", "gemini", "copilot"):
                # Agent CLI 模式:寫 Phase A 純文字到 cleaned.md + 放 marker,等對話 agent 接手
                cleaned_md.write_text(plain, encoding="utf-8")
                target_lo = int(in_m["chinese"] * 0.95)
                target_hi = int(in_m["chinese"] * 1.05)
                marker_path = sdir / ".phase_b_pending.json"
                marker = {
                    "stage": "phase-b",
                    "engine": engine,
                    "input_file": str(cleaned_md.relative_to(PROJECT_ROOT)),
                    "rules_ref": "CLAUDE.md § 核心鐵律 + § QAQC 標準",
                    "ssot_rules": "prompts/qaqc_core_rules.md",
                    "input_chinese_chars": in_m["chinese"],
                    "target_chinese_chars_min": target_lo,
                    "target_chinese_chars_max": target_hi,
                    "context_file": str(ctx_path.relative_to(PROJECT_ROOT)),
                    "instructions": (
                        f"Phase B 待 {engine} agent 接手。讀 {cleaned_md.name} 純文字版,套 "
                        "CLAUDE.md 的 Phase B 規則(零省略、合併斷行、加標點接續詞、加 ## 標題、"
                        f"中文字數落在 {target_lo}-{target_hi} 區間),寫回 {cleaned_md.name}。"
                        "完成後刪本 marker。"
                    ),
                    "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                }
                marker_path.write_text(
                    json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[session] Phase B 待 {engine} agent 接手:")
                print(f"  marker: {marker_path.relative_to(PROJECT_ROOT)}")
                print(f"  input:  {cleaned_md.relative_to(PROJECT_ROOT)} "
                      f"({in_m['chinese']:,} 中文字)")
                print(f"  target: {target_lo:,}-{target_hi:,} 中文字 (95-105%)")
                phase_b_stats = {
                    "engine": engine,
                    "status": "pending_agent_handoff",
                    "marker_file": marker_path.name,
                    "in_chinese_chars": in_m["chinese"],
                    "target_min": target_lo,
                    "target_max": target_hi,
                }

            elif engine == "api":
                # 純 shell/cron 路徑:打 Gemini API
                tmp_in = sdir / ".phase_b_input.txt"
                tmp_in.write_text(plain, encoding="utf-8")
                try:
                    # session.py 已做過 engine routing(原則 5),帶 --force-api 通過守門
                    cmd = ["python3", str(PHASE_B_SCRIPT), str(tmp_in),
                           "-o", str(cleaned_md), "--mode", "merged", "--force-api"]
                    if ctx_text:
                        cmd += ["--context", str(ctx_path)]
                    run(cmd)
                    out_m = count_chars(cleaned_md.read_text(encoding="utf-8"))
                    phase_b_stats = {
                        "engine": "api",
                        "in_chars_no_space": in_m["no_space"],
                        "out_chars_no_space": out_m["no_space"],
                        "ratio_no_space": round(out_m["no_space"] / max(1, in_m["no_space"]), 4),
                        "ratio_chinese": round(out_m["chinese"] / max(1, in_m["chinese"]), 4),
                    }
                except subprocess.CalledProcessError as e:
                    print(f"[session] Phase B (API) failed: {e} — falling back to Phase A plaintext",
                          file=sys.stderr)
                    cleaned_md.write_text(plain, encoding="utf-8")
                    phase_b_stats = {"engine": "api", "fallback": "phase_a_plaintext",
                                     "error": str(e)}
                finally:
                    if tmp_in.exists():
                        tmp_in.unlink()

            else:  # engine == "none"
                cleaned_md.write_text(plain, encoding="utf-8")
                print(f"[session] engine=none: Phase B skipped, cleaned.md = Phase A plaintext")
                phase_b_stats = {"engine": "none", "status": "skipped"}

        # Phase C / Phase D(原則 9 強制門)標點正規化 + 通順/hook
        # cleaned.md 一旦產出,進 Step 5/6 出版前必須過 Phase C、D(SSoT: qaqc_core_rules.md § R7/§ R8)。
        # 依賴鏈:phase-b → phase-c → phase-d → step-3 → step-4(逐一清除,清一個驗一個)。
        # Phase A/B/C/D 都操作同一份 cleaned.md;Step 3+ 才換產物。
        phase_c_stats = None
        phase_d_stats = None
        do_c = do_phase_b  # 產 cleaned.md 就要過 Phase C
        do_d = do_phase_b and args.stop_at != "phase-c"
        if do_c:
            mc = sdir / ".phase_c_pending.json"
            md_ = sdir / ".phase_d_pending.json"
            if engine in ("claude", "gemini", "copilot"):
                marker = {
                    "stage": "phase-c",
                    "engine": engine,
                    "input_file": str(cleaned_md.relative_to(PROJECT_ROOT)),
                    "output_file": str(cleaned_md.relative_to(PROJECT_ROOT)),
                    "rules_ref": "prompts/qaqc_core_rules.md § R7",
                    "tool": "scripts/normalize_punctuation.py",
                    "depends_on": "phase-b 完成(cleaned.md 已校稿,不是純文字版)",
                    "instructions": (
                        f"Phase C 待 {engine} agent 接手。先確認 .phase_b_pending.json 已處理。"
                        "(1) 確定性全形化:跑 `python3 scripts/normalize_punctuation.py "
                        f"{cleaned_md.name} --in-place`(§ R7.1,機械步驟走工具不靠手)。"
                        "(2) 判斷:依 § R7.2 把『前指引導語(…是/就是/說/講說、概念名詞標頭、講者名、"
                        "例如/換句話說)』後的逗號/句號改成全形冒號「：」,引述補『』/「」。"
                        f"驗:`python3 scripts/normalize_punctuation.py {cleaned_md.name} --check` 殘留=0。"
                        "完成後刪本 marker,並把 metadata.json 的 qaqc.phase_c.status 改 done、actor 改自己。"
                    ),
                    "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                }
                mc.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[session] Phase C 待 {engine} agent 接手: {mc.relative_to(PROJECT_ROOT)}")
                phase_c_stats = {"engine": engine, "status": "pending_agent_handoff",
                                 "marker_file": mc.name}
                if do_d:
                    marker = {
                        "stage": "phase-d",
                        "engine": engine,
                        "input_file": str(cleaned_md.relative_to(PROJECT_ROOT)),
                        "output_file": str(cleaned_md.relative_to(PROJECT_ROOT)),
                        "rules_ref": "prompts/qaqc_core_rules.md § R8",
                        "depends_on": "phase-c 完成",
                        "instructions": (
                            f"Phase D 待 {engine} agent 接手。先確認 .phase_c_pending.json 已處理。"
                            "依 § R8 在話題轉換/舉例/回扣前文/進入下一點的接縫補『內容指涉型 hook』"
                            "(回指/框架/轉折/列點/過場/復述/收束 七類),而非只補裸連接詞。"
                            "零省略:hook 只插在句間,不得改寫或刪原句;原段落數 == 產物段落數(1:1)。"
                            "完成後刪本 marker,並把 metadata.json 的 qaqc.phase_d.status 改 done、actor 改自己。"
                        ),
                        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    }
                    md_.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"[session] Phase D 待 {engine} agent 接手: {md_.relative_to(PROJECT_ROOT)}")
                    phase_d_stats = {"engine": engine, "status": "pending_agent_handoff",
                                     "marker_file": md_.name}

            elif engine in ("api", "none"):
                # 全形化(§ R7.1)是確定性的,任何 engine 都能直接做;冒號(§ R7.2)/hook(§ R8)
                # 屬判斷,沒有對話 agent 無法完成 → 標 pending,出版閘(prepublish_gate.py)會擋。
                try:
                    run(["python3", str(NORMALIZE_SCRIPT), str(cleaned_md), "--in-place"])
                    phase_c_stats = {"engine": engine, "status": "fullwidth_done_colon_pending",
                                     "note": "§ R7.1 全形化已跑;§ R7.2 冒號需判斷,無 agent 未完成"}
                except subprocess.CalledProcessError as e:
                    phase_c_stats = {"engine": engine, "status": "error", "error": str(e)}
                phase_d_stats = {"engine": engine, "status": "skipped_no_agent",
                                 "note": "§ R8 hook 需判斷,api/none 無對話 agent"}
                print(f"[session] engine={engine}: 已跑全形化;冒號/hook 需 agent,"
                      "出版前 gate 會擋(原則 9)。")

        # 7.4 圖片理解 + 自動插圖(可選,--images 才啟用;SSoT § S4.5.11)
        # marker 鏈:phase-d → images → image-insert(→ step-3 → step-4)
        images_stats = None
        image_insert_stats = None
        if args.images and do_phase_b:
            img_src = Path(args.images).resolve()
            img_dst = sdir / "images"
            if not img_src.is_dir():
                print(f"[session] --images 不是目錄: {img_src}", file=sys.stderr)
                sys.exit(1)
            img_dst.mkdir(exist_ok=True)
            import shutil as _sh
            copied = 0
            for p in sorted(img_src.iterdir()):
                if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    _sh.copy2(p, img_dst / p.name)
                    copied += 1
            print(f"[session] images: {copied} 張 → {img_dst.relative_to(PROJECT_ROOT)}")

            if engine in ("claude", "gemini", "copilot"):
                mi = sdir / ".images_pending.json"
                mi.write_text(json.dumps({
                    "stage": "images",
                    "engine": engine,
                    "input_dir": str(img_dst.relative_to(PROJECT_ROOT)),
                    "output_file": str((sdir / "image_notes.json").relative_to(PROJECT_ROOT)),
                    "rules_ref": "prompts/publish_qaqc.md § S4.5.11",
                    "tool": "scripts/describe_images.py",
                    "depends_on": "phase-d 完成",
                    "instructions": (
                        f"圖片理解待 {engine} agent 接手。先確認 .phase_d_pending.json 已處理。"
                        f"跑 `python3 scripts/describe_images.py --session {sdir.relative_to(PROJECT_ROOT)}`"
                        "(Antigravity headless;逐張串行、可續跑;連續失敗自動熔斷,"
                        "熔斷後先以最小指令單測引擎再處置,規則見 § S4.5.11)。"
                        "驗:image_notes.json 每張 status=described、五欄位非空。"
                        "工具全數完成會自動刪本 marker。"),
                    "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[session] 圖片理解待 {engine} agent 接手: {mi.relative_to(PROJECT_ROOT)}")
                images_stats = {"engine": engine, "status": "pending_agent_handoff",
                                "marker_file": mi.name, "count": copied}

                if args.stop_at != "images":
                    mii = sdir / ".image_insert_pending.json"
                    mii.write_text(json.dumps({
                        "stage": "image-insert",
                        "engine": engine,
                        "input_file": str((sdir / "cleaned.md").relative_to(PROJECT_ROOT)),
                        "rules_ref": "prompts/publish_qaqc.md § S4.5.11",
                        "tool": "scripts/insert_images.py",
                        "depends_on": "images 完成(image_notes.json 全數 described)",
                        "instructions": (
                            f"自動插圖待 {engine} agent 接手。先確認 .images_pending.json 已清。"
                            "流程:(1) `dedupe_images.py --report`(§ S4.5.12)確認後 `--apply` 去重;"
                            "(2) `insert_images.py --plan` 取內容行清單;"
                            "(3) `propose_anchors.py --session <dir>`(純 py 文字比對+單調 DP,零 LLM)"
                            "產出 anchors_proposed.json;(3b) 開 Claude Haiku subagent(本 stage 規範"
                            "執行者,勿用更大模型)只複核 needs_llm_review=true 的條目(§ S4.5.11)。"
                            "(4) `insert_images.py --apply --anchors <json>`(零省略+單調約束,fail 即 rollback);"
                            "(5) `insert_images.py --verify` 過 → 刪本 marker;"
                            "needs_review 清單向使用者回報複核。"),
                        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"[session] 自動插圖待 {engine} agent 接手: {mii.relative_to(PROJECT_ROOT)}")
                    image_insert_stats = {"engine": engine, "status": "pending_agent_handoff",
                                          "marker_file": mii.name}
            else:
                print(f"[session] engine={engine}: 圖片描述/插圖需對話 agent(Haiku anchors),"
                      "已複製圖片但未寫 marker;出版前 gate 會擋未完成的圖片流程。")
                images_stats = {"engine": engine, "status": "skipped_no_agent", "count": copied}
    else:
        # ─── 文件輸入線(PDF/EPUB/TXT)──────────────────────────────────
        # extract.py 產出「已是乾淨結構化繁體文本」,直接當 cleaned.md,跳過
        # transcribe/phase-a/phase-b(那三段是 ASR 專屬清理,對已乾淨文件無意
        # 義且會改寫原文)。下游 enhance/notes 與音檔線共用同一份 cleaned.md
        # (理解跨線共用、ASR 清理不共用)。
        if not DOC_VENV.exists():
            print("[session] ERROR: 文件線(.pdf/.epub/.txt)需要 .venv-doc(PyMuPDF/"
                  "ebooklib/lxml/chardet/opencc)。安裝:\n"
                  "  python3.13 -m venv .venv-doc && "
                  ".venv-doc/bin/pip install -r requirements-doc.txt",
                  file=sys.stderr)
            sys.exit(3)

        t0 = time.time()
        cleaned_md = sdir / "cleaned.md"
        cmd = [str(DOC_VENV), str(DOC_EXTRACT_SCRIPT), str(src_link),
               "-o", str(cleaned_md)]
        extract_proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT),
                                      capture_output=True, text=True)
        if extract_proc.stderr:
            print(extract_proc.stderr, file=sys.stderr)
        if extract_proc.returncode != 0 or not cleaned_md.exists():
            print(f"[session] ERROR: extract.py failed (exit "
                  f"{extract_proc.returncode})", file=sys.stderr)
            meta["error"] = "doc_extract_failed"
            (sdir / "metadata.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            sys.exit(3)
        try:
            doc_stats = json.loads(extract_proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            doc_stats = {"raw_stdout": extract_proc.stdout.strip()}
        extract_secs = round(time.time() - t0, 1)
        print(f"[session] cleaned.md via extract.py ({cleaned_md.stat().st_size} "
              f"bytes, {extract_secs}s): {doc_stats}")

        # --vlm(僅 pdf 有意義):figures.py 渲染圖表/掃描頁進 images/,再複用
        # 既有 --images 的 describe_images.py / insert_images.py marker 呼叫
        # 路徑(見上方 § S4.5.11 那段,同一套 instructions 文字),不重造描述/
        # 插入邏輯,差別只在圖片來源是 figures.py 渲染而非人工資料夾複製。
        images_stats = None
        image_insert_stats = None
        if args.vlm and ext.lower() == ".pdf":
            fig_cmd = [str(DOC_VENV), str(DOC_FIGURES_SCRIPT), str(src_link),
                       "--session", str(sdir)]
            fig_proc = subprocess.run(fig_cmd, cwd=str(PROJECT_ROOT),
                                      capture_output=True, text=True)
            if fig_proc.returncode != 0:
                print(f"[session] figures.py failed (exit {fig_proc.returncode}): "
                      f"{fig_proc.stderr.strip()}", file=sys.stderr)
                images_stats = {"status": "error", "error": fig_proc.stderr.strip()}
            else:
                try:
                    fig_stats = json.loads(fig_proc.stdout.strip().splitlines()[-1])
                except (json.JSONDecodeError, IndexError):
                    fig_stats = {"raw_stdout": fig_proc.stdout.strip()}
                print(f"[session] figures.py: {fig_stats}")
                img_dst = sdir / "images"

                if engine in ("claude", "gemini", "copilot"):
                    mi = sdir / ".images_pending.json"
                    mi.write_text(json.dumps({
                        "stage": "images",
                        "engine": engine,
                        "input_dir": str(img_dst.relative_to(PROJECT_ROOT)),
                        "output_file": str((sdir / "image_notes.json").relative_to(PROJECT_ROOT)),
                        "rules_ref": "prompts/publish_qaqc.md § S4.5.11",
                        "tool": "scripts/describe_images.py",
                        "depends_on": "--vlm figures.py 完成(images/ 已渲染圖表/掃描頁)",
                        "instructions": (
                            f"圖片理解待 {engine} agent 接手。"
                            f"跑 `python3 scripts/describe_images.py --session {sdir.relative_to(PROJECT_ROOT)}`"
                            "(Antigravity headless;逐張串行、可續跑;連續失敗自動熔斷,"
                            "熔斷後先以最小指令單測引擎再處置,規則見 § S4.5.11)。"
                            "驗:image_notes.json 每張 status=described、五欄位非空。"
                            "工具全數完成會自動刪本 marker。"),
                        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"[session] 圖片理解待 {engine} agent 接手: "
                          f"{mi.relative_to(PROJECT_ROOT)}")
                    images_stats = {"engine": engine, "status": "pending_agent_handoff",
                                    "marker_file": mi.name, **fig_stats}

                    # --stop-at images 守門(跟音檔線 --images 分支的既有寫法一致,
                    # 見上方「7.4 圖片理解」的 `if args.stop_at != "images":`):
                    # 使用者明確要求停在 images,就只寫 describe marker,不寫 insert marker,
                    # 否則「兩個 marker 都寫了」跟輸出宣稱的 stopped-at 矛盾。
                    if args.stop_at != "images":
                        mii = sdir / ".image_insert_pending.json"
                        mii.write_text(json.dumps({
                            "stage": "image-insert",
                            "engine": engine,
                            "input_file": str(cleaned_md.relative_to(PROJECT_ROOT)),
                            "rules_ref": "prompts/publish_qaqc.md § S4.5.11",
                            "tool": "scripts/insert_images.py",
                            "depends_on": "images 完成(image_notes.json 全數 described)",
                            "instructions": (
                                f"自動插圖待 {engine} agent 接手。先確認 .images_pending.json 已清。"
                                "流程:(1) `dedupe_images.py --report`(§ S4.5.12)確認後 `--apply` 去重;"
                                "(2) `insert_images.py --plan` 取內容行清單;"
                                "(3) `propose_anchors.py --session <dir>`(純 py 文字比對+單調 DP,零 LLM)"
                                "產出 anchors_proposed.json;(3b) 開 Claude Haiku subagent(本 stage 規範"
                                "執行者,勿用更大模型)只複核 needs_llm_review=true 的條目(§ S4.5.11)。"
                                "(4) `insert_images.py --apply --anchors <json>`(零省略+單調約束,fail 即 rollback);"
                                "(5) `insert_images.py --verify` 過 → 刪本 marker;"
                                "needs_review 清單向使用者回報複核。"),
                            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                        }, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(f"[session] 自動插圖待 {engine} agent 接手: "
                              f"{mii.relative_to(PROJECT_ROOT)}")
                        image_insert_stats = {"engine": engine, "status": "pending_agent_handoff",
                                              "marker_file": mii.name}
                else:
                    print(f"[session] engine={engine}: 圖片描述/插圖需對話 agent"
                          "(Haiku anchors),已渲染圖表但未寫 marker;出版前 gate "
                          "會擋未完成的圖片流程。")
                    images_stats = {"engine": engine, "status": "skipped_no_agent",
                                    **fig_stats}
        elif args.vlm:
            print(f"[session] --vlm 只對 .pdf 有意義({ext} 非 pdf,跳過)",
                  file=sys.stderr)

        # doc 線沒有 ASR/音訊分析線(diarize/prosody/cut)/frames 線,以下欄位
        # 標 N/A 供 metadata 使用(不對文件套 ASR 品質門檻)
        transcript = None
        cleaned_srt = None
        original_metrics = None
        phase_a_metrics = None
        transcript_cleaned_srt = None
        audio_stats = None
        frames_stats = None
        groq_secs = extract_secs
        asr_label = "N/A(doc line — extract.py 確定性抽取,非 ASR)"
        phase_b_stats = {"status": "skipped_doc_line",
                         "note": "extract.py 產出已是乾淨結構化文本,cleaned.md 直接生成"}
        phase_c_stats = {"status": "skipped_doc_line",
                         "note": "文件輸入無 ASR 專屬標點/hook 需求"}
        phase_d_stats = {"status": "skipped_doc_line"}

    # 7.5 Step 3: 專有名詞補充 → enhanced.md
    # Runs if --keywords given OR --enhance flag OR stop-at in {enhance, notes}.
    enhanced_md = None
    enhance_stats = None
    do_enhance = (cleaned_md.exists()
                  and args.stop_at not in ("transcribe", "phase-a", "phase-b",
                                           "phase-c", "phase-d")
                  and (args.keywords or args.enhance
                       or (args.identity and args.stop_at == "notes")))
    if do_enhance:
        enhanced_md = sdir / "enhanced.md"

        if engine in ("claude", "gemini", "copilot"):
            # Agent CLI 模式:寫 marker,等對話 agent 接手(在 Phase B 處理完 cleaned.md 之後)
            marker_path = sdir / ".step_3_pending.json"
            marker = {
                "stage": "step-3-enhance",
                "engine": engine,
                "input_file": str(cleaned_md.relative_to(PROJECT_ROOT)),
                "output_file": str(enhanced_md.relative_to(PROJECT_ROOT)),
                "rules_ref": "CLAUDE.md § Step 3 + prompts/qaqc_core_rules.md",
                "keywords_explicit": args.keywords or None,
                "context_file": str(ctx_path.relative_to(PROJECT_ROOT)),
                "depends_on": "phase-b 完成(cleaned.md 已校稿,不是純文字版)",
                "instructions": (
                    f"Step 3 待 {engine} agent 接手。先確認 cleaned.md 已完成 Phase B "
                    "(如果 .phase_b_pending.json 還在,先處理它)。然後讀 cleaned.md,"
                    "標出專有名詞並補充說明,輸出 enhanced.md。完成後刪本 marker。"
                ),
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            marker_path.write_text(
                json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[session] Step 3 待 {engine} agent 接手: "
                  f"{marker_path.relative_to(PROJECT_ROOT)}")
            enhance_stats = {"engine": engine, "status": "pending_agent_handoff",
                             "marker_file": marker_path.name}
            enhanced_md = None  # 不要把不存在的檔當 source for Step 4

        elif engine == "api":
            try:
                cmd = ["python3", str(PHASE_B_SCRIPT), str(cleaned_md),
                       "-o", str(enhanced_md), "--mode", "enhance", "--force-api"]
                if args.keywords:
                    cmd += ["--keywords", args.keywords]
                if ctx_text:
                    cmd += ["--context", str(ctx_path)]
                run(cmd)
                in_m = count_chars(cleaned_md.read_text(encoding="utf-8"))
                out_m = count_chars(enhanced_md.read_text(encoding="utf-8"))
                enhance_stats = {
                    "engine": "api",
                    "in_chars_no_space": in_m["no_space"],
                    "out_chars_no_space": out_m["no_space"],
                    "ratio_no_space": round(out_m["no_space"] / max(1, in_m["no_space"]), 4),
                    "keywords_explicit": bool(args.keywords),
                }
            except subprocess.CalledProcessError as e:
                print(f"[session] Step 3 enhance (API) failed: {e}", file=sys.stderr)
                enhanced_md = None
                enhance_stats = {"engine": "api", "error": str(e)}

        else:  # none
            print("[session] engine=none: Step 3 skipped")
            enhanced_md = None
            enhance_stats = {"engine": "none", "status": "skipped"}

    # 7.6 Step 4: 立場置入好學生筆記 → notes_<identity>.md
    notes_md = None
    notes_stats = None
    do_notes = (args.identity and args.stop_at == "notes" and cleaned_md.exists())
    if do_notes:
        notes_md = sdir / f"notes_{args.identity}.md"
        source_md = enhanced_md if (enhanced_md and enhanced_md.exists()) else cleaned_md

        if engine in ("claude", "gemini", "copilot"):
            marker_path = sdir / ".step_4_pending.json"
            marker = {
                "stage": "step-4-notes",
                "engine": engine,
                "identity": args.identity,
                "input_file": str(source_md.relative_to(PROJECT_ROOT)),
                "output_file": str(notes_md.relative_to(PROJECT_ROOT)),
                "rules_ref": "CLAUDE.md § 好學生筆記規範 + prompts/qaqc_core_rules.md",
                "context_file": str(ctx_path.relative_to(PROJECT_ROOT)),
                "depends_on": ("step-3-enhance 完成(若有);否則 phase-b 完成"),
                "instructions": (
                    f"Step 4 待 {engine} agent 接手。讀 {source_md.name},以「{args.identity}」"
                    "立場插入專業視角類比區塊(類比/應用/連結),完整保留原文,字數比 95-105%,"
                    f"輸出 {notes_md.name}。完成後刪本 marker。"
                ),
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            marker_path.write_text(
                json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[session] Step 4 待 {engine} agent 接手: "
                  f"{marker_path.relative_to(PROJECT_ROOT)}")
            notes_stats = {"engine": engine, "status": "pending_agent_handoff",
                           "marker_file": marker_path.name, "identity": args.identity}
            notes_md = None

        elif engine == "api":
            try:
                cmd = ["python3", str(PHASE_B_SCRIPT), str(source_md),
                       "-o", str(notes_md), "--mode", "notes",
                       "--identity", args.identity, "--force-api"]
                if ctx_text:
                    cmd += ["--context", str(ctx_path)]
                run(cmd)
                in_m = count_chars(source_md.read_text(encoding="utf-8"))
                out_m = count_chars(notes_md.read_text(encoding="utf-8"))
                notes_stats = {
                    "engine": "api",
                    "source": source_md.name,
                    "in_chars_no_space": in_m["no_space"],
                    "out_chars_no_space": out_m["no_space"],
                    "ratio_no_space": round(out_m["no_space"] / max(1, in_m["no_space"]), 4),
                }
            except subprocess.CalledProcessError as e:
                print(f"[session] Step 4 notes (API) failed: {e}", file=sys.stderr)
                notes_md = None
                notes_stats = {"engine": "api", "error": str(e)}

        else:  # none
            print("[session] engine=none: Step 4 skipped")
            notes_md = None
            notes_stats = {"engine": "none", "status": "skipped"}

    # 8. Write metadata.json
    meta.update({
        "stop_at": args.stop_at,
        "transcription": {
            "engine": asr_label,
            "duration_secs": groq_secs,
            "context_bytes": len(ctx_text.encode("utf-8")),
            # doc 線無 ASR 輸出,original_metrics 為 None → 標 N/A(不套 ASR 品質門檻)
            "original_chars_no_space": original_metrics["no_space"] if original_metrics else "N/A",
            "original_chinese_chars": original_metrics["chinese"] if original_metrics else "N/A",
        },
        "doc_extraction": doc_stats,
        "audio_analysis": audio_stats,
        "frames": frames_stats,
        "qaqc": {
            "phase_a_chars_no_space": phase_a_metrics["no_space"] if phase_a_metrics else "N/A",
            "phase_a_chinese_chars": phase_a_metrics["chinese"] if phase_a_metrics else "N/A",
            "phase_b": phase_b_stats,
            "phase_c": phase_c_stats,
            "phase_d": phase_d_stats,
            "images": images_stats,
            "image_insert": image_insert_stats,
            "enhance": enhance_stats,
            "notes": notes_stats,
            "structured_srt_produced": transcript_cleaned_srt is not None,
        },
        "artifacts": {
            "source": src_link.name,
            "context": ctx_path.name,
            "transcript_srt": transcript.name if transcript else None,
            "cleaned_srt": cleaned_srt.name if cleaned_srt else None,
            "cleaned_md": cleaned_md.name if cleaned_md.exists() else None,
            "enhanced_md": enhanced_md.name if (enhanced_md and enhanced_md.exists()) else None,
            "notes_md": notes_md.name if (notes_md and notes_md.exists()) else None,
            "transcript_cleaned_srt": (transcript_cleaned_srt.name
                                       if transcript_cleaned_srt else None),
        },
    })
    (sdir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[session] ✅ complete: {sdir}")
    if phase_b_stats:
        print(f"  Phase B ratio_chinese = {phase_b_stats.get('ratio_chinese', 'N/A')}")
    if enhanced_md:
        print(f"  Step 3 enhanced.md produced")
    if notes_md:
        print(f"  Step 4 notes_{args.identity}.md produced")
    print(f"  stopped at: {args.stop_at}")


def _srt_to_plain(srt_path: Path) -> str:
    """Strip SRT indices + timecodes, return concatenated text with newlines between segments."""
    out_lines = []
    for block in srt_path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) >= 3:
            out_lines.append("\n".join(lines[2:]))
    return "\n".join(out_lines)


# ─── Main ───

def main():
    ap = argparse.ArgumentParser(
        description="好學生筆記 pipeline 統籌器 — sessions/<slug>/ 容器化所有產物",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new", help="Create & run a new session")
    new.add_argument("audio", help="Path to audio/video file, OR a document "
                     "(.pdf/.epub/.txt) — 副檔名自動偵測分流:文件走確定性抽取線"
                     "(scripts/doc/extract.py → cleaned.md,跳過 transcribe/"
                     "phase-a/phase-b),音檔/影片走既有轉錄線,兩者最後都匯流到"
                     "共用的 enhance/notes 理解層")
    new.add_argument("--context", help="Context: a string OR a path to a .txt file")
    new.add_argument("--domain", help="Typo dict domain overlay, e.g. parenting")
    new.add_argument("--identity",
                     help="Step 4 立場 for 好學生筆記 (e.g. 建築師);"
                          " requires --stop-at notes")
    new.add_argument("--keywords",
                     help="Step 3 comma-separated keyword list for 專有名詞補充;"
                          " omit + --enhance to let LLM auto-detect")
    new.add_argument("--enhance", action="store_true",
                     help="Run Step 3 (專有名詞補充) with auto-detected terms")
    new.add_argument("--images",
                     help="圖片資料夾:copy 進 sessions/<slug>/images/ 並啟用"
                          "圖片理解(describe_images.py)+ 自動插圖(insert_images.py)"
                          " stages(§ S4.5.11);marker 鏈 phase-d→images→image-insert")
    new.add_argument("--asr", choices=["local", "groq"], default="local",
                     help="轉錄引擎(僅音檔/影片線適用)。local(預設):mlx-whisper "
                          "本地零雲端零 key,需 .venv-audio;groq:Groq API"
                          "(需 .env GROQ_API_KEY)")
    new.add_argument("--vlm", action="store_true",
                     help="文件線限定,僅對 .pdf 有意義:先跑 scripts/doc/figures.py "
                          "渲染圖表/掃描頁進 images/,再複用既有 --images 的 "
                          "describe_images.py(圖片理解)+ insert_images.py(自動插圖)"
                          " marker 呼叫路徑(§ S4.5.11),讓 PDF 額外看圖")
    new.add_argument("--diarize", action="store_true",
                     help="音訊分析線:pyannote speaker diarization → speakers.json + "
                          "transcript.speakers.srt([S1] 前綴;命名走 marker 交 agent)。"
                          "需 .venv-audio + .env 的 HF_TOKEN(gated model)")
    new.add_argument("--prosody", action="store_true",
                     help="音訊分析線:librosa 能量/音高/語速 → prosody.json + "
                          "highlights.md(高昂精華段;全本地零 LLM)")
    new.add_argument("--cut", action="store_true",
                     help="Podcast 文字剪輯:隱含 --diarize --prosody,產 cutplan.md "
                          "(agent 提案 → MM 人審 → render_cut.py 出片)")
    new.add_argument("--num-speakers", type=int,
                     help="diarize:已知講者人數就鎖定(準確度最好)")
    new.add_argument("--frames", action="store_true",
                     help="frames 線(影片限定):場景偵測抽幀,後續 screen/ocr/"
                          "compose 逐步跑(invisible-context 併入)")
    new.add_argument("--stop-at",
                     choices=["transcribe", "phase-a", "phase-b",
                              "phase-c", "phase-d", "images", "image-insert",
                              "enhance", "notes"],
                     default="phase-b",
                     help="Stopping point (default: phase-b = cleaned.md). "
                          "Step 2 is the most common終點 for users who just want the "
                          "合併 cleaned.md — don't always run to notes. "
                          "phase-c/phase-d 是 cleaned.md 出版前的強制標點/通順門(§ R7/§ R8)。")
    new.add_argument("--skip-phase-b", action="store_true",
                     help="(Legacy alias of --stop-at phase-a) Skip Phase B; produce cleaned.srt only")
    new.add_argument("--structured-srt", action="store_true",
                     help="Also produce transcript.cleaned.srt via --structured mode")
    new.add_argument("--engine",
                     choices=ENGINE_CHOICES,
                     default="auto",
                     help="Phase B / Step 3 / Step 4 routing. "
                          "auto: detect host via env (CLAUDECODE/GEMINI_CLI/...); "
                          "claude|gemini|copilot: agent接手, 不打 API; "
                          "api: 走 Gemini API (純 shell/cron 用); "
                          "none: 跳過所有 LLM 步驟,只到 Phase A")
    new.add_argument("--dry-run", action="store_true",
                     help="Just print engine routing decision and exit (no Groq/no API)")

    args = ap.parse_args()
    if args.cmd == "new":
        new_session(args)


if __name__ == "__main__":
    main()
