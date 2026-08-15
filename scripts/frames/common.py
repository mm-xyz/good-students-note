"""frames 線共用工具:env 載入、manifest 讀寫、dHash、SRT 解析。

2026-07-28 自 invisible-context repo 併入(P4):
- work/<slug>/ → sessions/<slug>/frames/(與音訊線同 session 容器)
- slug = session 目錄名;manifest 住 sessions/<slug>/frames/manifest.json
"""
import json
import os
import re
import subprocess
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent.parent
SESSIONS_DIR = REPO_DIR / "sessions"
MARS_CC_ENV = Path.home() / "GithubRepo_mm-xyz" / "mars-cc" / ".env"

DEFAULTS = {
    "LM_STUDIO_URL": "http://localhost:1234/v1",
    "LM_STUDIO_MODEL": "google/gemma-4-26b-a4b-qat",
    "OUTPUT_ROOT": str(Path.home() / "MarsDots" / "source" / "course"),
    "SCENE_THRESHOLD": "0.06",
    "MIN_GAP_SEC": "1.5",
    "DEDUP_HAMMING": "2",
    "PAUSE_MIN_SEC": "4.0",
    "PARA_GAP_SEC": "2.5",
}


def _read_env_file(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_config() -> dict:
    """優先序：程序環境變數 > repo/.env > mars-cc/.env（token 的家）> 預設值。"""
    cfg = dict(DEFAULTS)
    cfg.update(_read_env_file(MARS_CC_ENV))
    cfg.update(_read_env_file(REPO_DIR / ".env"))
    for k in list(cfg):
        if k in os.environ:
            cfg[k] = os.environ[k]
    return cfg


def slugify(text: str, maxlen: int = 40) -> str:
    text = re.sub(r"[\s　]+", "-", text.strip())
    text = re.sub(r"[^\w一-鿿\-]", "", text)
    return text[:maxlen].strip("-") or "untitled"


def frames_workdir(slug: str) -> Path:
    return SESSIONS_DIR / slug / "frames"


def manifest_path(slug: str) -> Path:
    return frames_workdir(slug) / "manifest.json"


def load_manifest(slug: str) -> dict:
    return json.loads(manifest_path(slug).read_text(encoding="utf-8"))


def save_manifest(slug: str, data: dict) -> None:
    p = manifest_path(slug)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)  # atomic：半夜長跑中斷不會留下壞檔


def ffprobe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def dhash(image_path: Path, size: int = 8) -> int:
    """64-bit difference hash，只靠 Pillow，不拉 numpy。"""
    from PIL import Image

    with Image.open(image_path) as im:
        im = im.convert("L").resize((size + 1, size), Image.LANCZOS)
        px = list(im.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _ts_to_sec(ts: str) -> float:
    h, m, s, ms = _TS.match(ts).groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(path: Path) -> list[dict]:
    """回傳 [{start, end, text}]，text 已把多行併成一行。"""
    raw = path.read_text(encoding="utf-8-sig")
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        time_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if time_idx is None or time_idx + 1 > len(lines):
            continue
        m = re.findall(_TS, lines[time_idx])
        if len(m) < 2:
            continue
        start = _ts_to_sec(lines[time_idx].split("-->")[0].strip())
        end = _ts_to_sec(lines[time_idx].split("-->")[1].strip())
        text = " ".join(lines[time_idx + 1:])
        if text:
            cues.append({"start": start, "end": end, "text": text})
    return cues


def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
