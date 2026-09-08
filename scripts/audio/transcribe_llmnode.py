#!/usr/bin/env python3
"""
scripts/audio/transcribe_llmnode.py — 遠端 ASR stage(llm-node whisper.cpp)

與 transcribe_local.py 同契約(同 CLI、同產物),差別只在推理跑在哪:
  local     Mac 的 mlx-whisper(Apple Silicon MLX,Mac 專用)
  llm-node  llm-node 的 whisper.cpp CPU 推理(Intel Linux,14 核)

用途:材料本來就在 llm-node(如 pCloud 拉下來的課程影片),或 Mac 要留著做別的事。
實測 llm-node 約 2.75x realtime(large-v3-turbo-q8_0,-t 12)。

⚠️ 分段是為了長檔跑得完,不是為了標點(2026-09-07 MM 拍板):
長檔單次跑 whisper 會讓 ssh 連線撐不住(106 分鐘那集實測失敗),分段後每段獨立、
失敗只賠一段。**零標點是另一件事,靠 --prompt 解,分段解決不了**——把中招的音檔
單獨拉出來(390 秒)帶同一份長 prompt 跑,前 90 秒照樣 0 標點(見
docs/adr/ADR-2026-09-07-asr-segmented-transcription.md)。兩件事不要混為一談。

分段規則(--segment-seconds / --max-segment-seconds):
  切 420s 一段;尾巴併進最後一段避免孤兒段;併完超過 480s 就對半切。

用 .venv-audio 的 python 跑(要 opencc):
    .venv-audio/bin/python scripts/audio/transcribe_llmnode.py <media> -o transcript.srt \
        [--context context.txt] [--language zh] [--remote-media <llm-node 上的路徑>]

--remote-media:媒體已經在 llm-node 上,不必從 Mac 上傳(<media> 只當標識用)。
沒給就在 Mac 抽 16k 單聲道 wav 再上傳(15 分鐘的課約 29 MB,比傳原片省得多)。

產物與 local 線完全一致,下游 QAQC/cleaned 不需要任何改動:
  <output>            EP15 式短句 SRT
  <output>/../words.json  word 級時間軸(字級精剪用)
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from srt_utils import sec_to_ts, split_words_to_phrases  # noqa: E402
from frames.common import load_config  # noqa: E402

# llm-node ASR 預設值(可用 env / repo .env / mars-cc .env 覆蓋,同 LLM_NODE_* 慣例)
ASR_DEFAULTS = {
    "LLM_NODE_SSH": "llm-node",
    "LLM_NODE_ASR_BIN": "~/whisper.cpp/build/bin/whisper-cli",
    "LLM_NODE_ASR_MODEL": "~/models/ggml-large-v3-turbo-q8_0.bin",
    "LLM_NODE_ASR_THREADS": "12",
}


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """跑一個指令,失敗就帶 stderr 中止。

    ⚠️ errors="replace" 是必要的,不是保險:whisper.cpp 會在緩衝邊界吐出半個
    多位元組字元,text=True 的預設 strict 解碼會直接 UnicodeDecodeError,
    讓整集轉錄失敗(2026-09-08 第 46 集實踩,重跑兩次都同一個位置)。
    轉錄結果本身走 JSON 檔不走 stdout,所以這裡替換掉壞位元組不影響產物。
    """
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)
    if p.returncode != 0:
        print(f"[transcribe-llmnode] FAILED: {' '.join(cmd[:3])}…\n{p.stderr[-2000:]}",
              file=sys.stderr)
        sys.exit(2)
    return p


def plan_segments(duration: float, seg: float, maxseg: float) -> list[tuple[float, float]]:
    """把 duration 切成 (start, length) 段落清單。

    切 seg 秒一段;尾巴併進最後一段(避免只有幾秒的孤兒段);
    併完若超過 maxseg 就對半切。回傳的段落完整覆蓋 [0, duration) 不重疊。
    """
    if duration <= maxseg:
        return [(0.0, duration)]
    n = int(duration // seg)
    rem = duration - n * seg
    bounds = [(i * seg, seg) for i in range(n - 1)]
    start = (n - 1) * seg
    last = seg + rem          # 尾巴併進第 n 段
    if last > maxseg:
        half = last / 2.0
        bounds.append((start, half))
        bounds.append((start + half, last - half))
    else:
        bounds.append((start, last))
    return bounds


def main():
    ap = argparse.ArgumentParser(description="llm-node whisper.cpp 分段轉錄 → SRT")
    ap.add_argument("media", help="音檔或影片路徑(--remote-media 時只當標識)")
    ap.add_argument("-o", "--output", required=True, help="輸出 SRT 路徑")
    ap.add_argument("--context", help="context 檔路徑,內容餵 whisper --prompt")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--remote-media", help="媒體已在 llm-node 上的絕對路徑,免上傳")
    ap.add_argument("--host", help="ssh host(預設 LLM_NODE_SSH)")
    ap.add_argument("--model", help="llm-node 上的 ggml 模型路徑")
    ap.add_argument("--threads", type=int, help="whisper 執行緒數")
    ap.add_argument("--segment-seconds", type=float, default=420.0,
                    help="分段長度秒數(預設 420=7 分鐘);設 0 關閉分段")
    ap.add_argument("--max-segment-seconds", type=float, default=480.0,
                    help="單段上限秒數(預設 480=8 分鐘),超過就對半切")
    ap.add_argument("--keep-remote", action="store_true", help="保留 llm-node 上的暫存檔")
    args = ap.parse_args()

    cfg = dict(ASR_DEFAULTS)
    cfg.update({k: v for k, v in load_config().items() if k in ASR_DEFAULTS})
    cfg.update({k: v for k, v in os.environ.items() if k in ASR_DEFAULTS})

    host = args.host or cfg["LLM_NODE_SSH"]
    model = args.model or cfg["LLM_NODE_ASR_MODEL"]
    threads = args.threads or int(cfg["LLM_NODE_ASR_THREADS"])
    asr_bin = cfg["LLM_NODE_ASR_BIN"]

    import opencc

    prompt = ""
    if args.context:
        ctx = Path(args.context)
        if ctx.exists():
            # 與 transcribe_local.py 同一份 context.txt、同一個截斷長度。
            # ⚠️ context.txt 控在 60 字上下、有標點的自然敘述,專名**嵌在句子裡**。
            #    不要寫成「專名放開頭」或頓號分隔的清單——那個形式會壓掉標點。
            #    prompt 的書寫形式會傳染給輸出:同音檔同模型只換 prompt,21 字與
            #    62 字的敘述都是每 ~11.7 字一個標點,147 字的敘述＋長串專名列舉
            #    是 0 個(各跑兩次數字相同)。下面的 [:200] 是硬截斷不是安全額度。
            prompt = ctx.read_text(encoding="utf-8").strip()[:200]

    stem = f"gsn_{os.getpid()}"
    rwav = f"/tmp/{stem}.wav"

    # 1. 在 llm-node 上取得完整的 16k 單聲道 wav
    if args.remote_media:
        print(f"[transcribe-llmnode] 遠端抽音軌:{Path(args.remote_media).name}")
        sh(["ssh", host,
            f"ffmpeg -nostdin -loglevel error -y -i {shlex.quote(args.remote_media)} "
            f"-vn -ar 16000 -ac 1 -c:a pcm_s16le {shlex.quote(rwav)}"])
    else:
        media = Path(args.media)
        if not media.exists():
            print(f"[transcribe-llmnode] ERROR: 找不到 {media}", file=sys.stderr)
            sys.exit(1)
        with tempfile.TemporaryDirectory() as td:
            lwav = Path(td) / "a.wav"
            print(f"[transcribe-llmnode] 本地抽音軌:{media.name}")
            sh(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(media),
                "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(lwav)])
            print(f"[transcribe-llmnode] 上傳 {lwav.stat().st_size / 1048576:.0f} MB → {host}")
            sh(["scp", "-q", str(lwav), f"{host}:{rwav}"])

    # 2. 量長度、規劃分段
    dur = float(sh(["ssh", host, f"ffprobe -v error -show_entries format=duration "
                                 f"-of csv=p=0 {shlex.quote(rwav)}"]).stdout.strip())
    if args.segment_seconds > 0:
        segs = plan_segments(dur, args.segment_seconds, args.max_segment_seconds)
    else:
        segs = [(0.0, dur)]
    print(f"[transcribe-llmnode] {dur/60:.1f} 分鐘 → {len(segs)} 段 "
          f"({', '.join(f'{L/60:.1f}m' for _, L in segs)})")

    # 3. 逐段轉錄。每段都重新套用 --prompt(標點靠 prompt 寫法保證,不靠分段)。
    #    -ojf(output-json-full)= mlx-whisper word_timestamps=True 的對應物:
    #    給「句子級 segment」+ 每段內含 tokens[] 的 per-token 時間軸。
    #    ⚠️ 不要用 -ml 1 取代(會把句子邊界攤平成單層 token 流,下游切不開);
    #    ⚠️ 更不要加 -sow(按空白分隔的詞切,中文沒空白 ⇒ 整句變一個 word)。
    pflag = f"--prompt {shlex.quote(prompt)} " if prompt else ""
    remote_cmds = []
    for i, (start, length) in enumerate(segs):
        sw = f"/tmp/{stem}_s{i}.wav"
        remote_cmds.append(
            f"ffmpeg -nostdin -loglevel error -y -ss {start:.3f} -t {length:.3f} "
            f"-i {shlex.quote(rwav)} -ar 16000 -ac 1 -c:a pcm_s16le {shlex.quote(sw)} && "
            f"{asr_bin} -m {model} -l {shlex.quote(args.language)} -t {threads} -ojf "
            f"{pflag}-of {shlex.quote(f'/tmp/{stem}_s{i}')} {shlex.quote(sw)} >/dev/null 2>&1 ; "
            f"rm -f {shlex.quote(sw)}")
    print(f"[transcribe-llmnode] {host}: whisper.cpp -t {threads},逐段轉錄中 …")
    sh(["ssh", host, " ; ".join(remote_cmds)])

    # 4. 取回各段 JSON,並把時間軸平移回全域
    entries = []
    with tempfile.TemporaryDirectory() as td:
        # 多來源 scp 一次收回;目的地給目錄,檔名維持 <stem>_sN.json
        sh(["scp", "-q"] + [f"{host}:/tmp/{stem}_s{i}.json" for i in range(len(segs))]
           + [td + "/"])
        for i, (start, _) in enumerate(segs):
            jp = Path(td) / f"{stem}_s{i}.json"
            if not jp.exists():
                print(f"[transcribe-llmnode] ERROR: 第 {i+1} 段沒有 JSON", file=sys.stderr)
                sys.exit(2)
            # ⚠️ errors="replace" 是必要的：whisper.cpp 偶爾會把半個多位元組字元
            # 寫進 JSON（2026-09-08 第 46 集實踩，position 990-991，重跑三次都同一處）。
            # strict 解碼會讓整集轉錄失敗；replace 只讓那一個字變成 U+FFFD，
            # 是看得見的損壞標記，比整集拿不到好。
            data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
            off = int(round(start * 1000))
            for seg in data.get("transcription", []):
                for key in ("offsets",):
                    o = seg.get(key)
                    if o:
                        o["from"] = o.get("from", 0) + off
                        o["to"] = o.get("to", 0) + off
                for tk in seg.get("tokens", []):
                    o = tk.get("offsets")
                    if o:
                        o["from"] = o.get("from", 0) + off
                        o["to"] = o.get("to", 0) + off
                entries.append(seg)

    if not args.keep_remote:
        junk = [rwav] + [f"/tmp/{stem}_s{i}.json" for i in range(len(segs))]
        subprocess.run(["ssh", host, "rm -f " + " ".join(shlex.quote(x) for x in junk)],
                       capture_output=True)

    if not entries:
        print("[transcribe-llmnode] ERROR: 零 segment 輸出", file=sys.stderr)
        sys.exit(1)

    # 5. 後製與 local 線同步:OpenCC s2twp + 逐 segment 切 EP15 式短句。
    #    迴圈結構刻意與 transcribe_local.py 一致(segment 外層、word 內層),
    #    ref_text 用該 segment 的原始 text,join_words 才判得出英數字之間
    #    該不該補空格(見 srt_utils.join_words)。
    def secs(o: dict, key: str) -> float:
        return round(o.get(key, 0) / 1000.0, 3)

    cc = opencc.OpenCC("s2twp")
    words = []
    blocks = []
    n_seg = 0
    n = 0
    for seg in entries:
        text = cc.convert(seg.get("text", "").strip())
        if not text:
            continue
        n_seg += 1
        seg_off = seg.get("offsets", {})
        seg_words = []
        for tk in seg.get("tokens", []):
            raw = tk.get("text", "")
            # whisper.cpp 會把 [_BEG_]／[_TT_123] 這類特殊 token 也放進 tokens[]
            if not raw.strip() or raw.startswith("[_"):
                continue
            wt = cc.convert(raw.strip())
            if not wt:
                continue
            off = tk.get("offsets", {})
            seg_words.append({"start": secs(off, "from"), "end": secs(off, "to"),
                              "word": wt})
        words.extend(seg_words)
        phrases = split_words_to_phrases(seg_words, text) or \
            [{"start": secs(seg_off, "from"), "end": secs(seg_off, "to"), "text": text}]
        for p in phrases:
            n += 1
            blocks.append(f"{n}\n{sec_to_ts(p['start'])} --> {sec_to_ts(p['end'])}\n"
                          f"{p['text']}\n")
    if not blocks:
        print("[transcribe-llmnode] ERROR: 零 segment 輸出", file=sys.stderr)
        sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    words_path = out.parent / "words.json"
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    print(f"[transcribe-llmnode] {n_seg} segments → {n} 短句 cues → {args.output}"
          f"({len(words)} words → {words_path.name})")


if __name__ == "__main__":
    main()
