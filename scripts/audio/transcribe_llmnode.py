#!/usr/bin/env python3
"""
scripts/audio/transcribe_llmnode.py — 遠端 ASR stage(llm-node whisper.cpp)

與 transcribe_local.py 同契約(同 CLI、同產物),差別只在推理跑在哪:
  local     Mac 的 mlx-whisper(Apple Silicon MLX,Mac 專用)
  llm-node  llm-node 的 whisper.cpp CPU 推理(Intel Linux,14 核)

用途:材料本來就在 llm-node(如 pCloud 拉下來的課程影片),或 Mac 要留著做別的事。
實測 llm-node 約 2.75x realtime(large-v3-turbo-q8_0,-t 12)。

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
    """跑一個指令,失敗就帶 stderr 中止。"""
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        print(f"[transcribe-llmnode] FAILED: {' '.join(cmd[:3])}…\n{p.stderr[-2000:]}",
              file=sys.stderr)
        sys.exit(2)
    return p


def ssh_run(host: str, remote_cmd: str) -> subprocess.CompletedProcess:
    return sh(["ssh", host, remote_cmd])


def main():
    ap = argparse.ArgumentParser(description="llm-node whisper.cpp 轉錄 → SRT")
    ap.add_argument("media", help="音檔或影片路徑(--remote-media 時只當標識)")
    ap.add_argument("-o", "--output", required=True, help="輸出 SRT 路徑")
    ap.add_argument("--context", help="context 檔路徑,內容餵 whisper --prompt")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--remote-media", help="媒體已在 llm-node 上的絕對路徑,免上傳")
    ap.add_argument("--host", help="ssh host(預設 LLM_NODE_SSH)")
    ap.add_argument("--model", help="llm-node 上的 ggml 模型路徑")
    ap.add_argument("--threads", type=int, help="whisper 執行緒數")
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
            # whisper prompt 窗口有限,取前 200 字(人名/專名放 context 開頭最有效;
            # 與 transcribe_local.py 同一份 context.txt、同一個截斷長度)
            prompt = ctx.read_text(encoding="utf-8").strip()[:200]

    stem = f"gsn_{os.getpid()}"
    rwav = f"/tmp/{stem}.wav"
    rjson = f"/tmp/{stem}.json"

    # 1. 取得 llm-node 上的 16k 單聲道 wav
    if args.remote_media:
        print(f"[transcribe-llmnode] 遠端抽音軌:{Path(args.remote_media).name}")
        ssh_run(host, f"ffmpeg -nostdin -loglevel error -y -i {shlex.quote(args.remote_media)} "
                      f"-vn -ar 16000 -ac 1 -c:a pcm_s16le {shlex.quote(rwav)}")
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
            mb = lwav.stat().st_size / 1048576
            print(f"[transcribe-llmnode] 上傳 {mb:.0f} MB → {host}")
            sh(["scp", "-q", str(lwav), f"{host}:{rwav}"])

    # 2. 遠端轉錄。-ojf(output-json-full)= mlx-whisper word_timestamps=True 的對應物:
    #    給「句子級 segment」+ 每個 segment 內含 tokens[] 的 per-token 時間軸,
    #    兩層結構讓下游能照 local 線逐 segment 切短句。
    #    ⚠️ 不要用 -ml 1 取代:那會把句子邊界攤平成單層 token 流,
    #    split_words_to_phrases 少了 segment 兜底,沒標點又沒 ≥0.5s 停頓時整段
    #    切不開(實測 60s 中文只出 2 個 cue,每個 24–36 秒)。
    #    ⚠️ 更不要加 -sow(split-on-word):它按空白分隔的詞切,中文沒有空白 ⇒
    #    整句變成一個「word」(實測每段 4–22 字＝句子級,粒度直接失效)。
    cmd = (f"{asr_bin} -m {model} -l {shlex.quote(args.language)} -t {threads} "
           f"-ojf -of {shlex.quote('/tmp/' + stem)} {shlex.quote(rwav)}")
    if prompt:
        cmd += f" --prompt {shlex.quote(prompt)}"
    print(f"[transcribe-llmnode] {host}: whisper.cpp -t {threads} …")
    ssh_run(host, cmd)

    # 3. 取回 JSON
    with tempfile.TemporaryDirectory() as td:
        ljson = Path(td) / "r.json"
        sh(["scp", "-q", f"{host}:{rjson}", str(ljson)])
        data = json.loads(ljson.read_text(encoding="utf-8"))

    if not args.keep_remote:
        subprocess.run(["ssh", host, f"rm -f {shlex.quote(rwav)} {shlex.quote(rjson)}"],
                       capture_output=True)

    entries = data.get("transcription", [])
    if not entries:
        print("[transcribe-llmnode] ERROR: 零 segment 輸出", file=sys.stderr)
        sys.exit(1)

    # 4. 後製與 local 線同步:OpenCC s2twp + 逐 segment 切 EP15 式短句。
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
