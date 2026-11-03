#!/usr/bin/env python3
"""
scripts/audio/audacity_labels.py — Audacity 細剪來回轉(Lifov #1181,零 LLM)

    python3 scripts/audio/audacity_labels.py export --session sessions/<slug>
    python3 scripts/audio/audacity_labels.py apply  --session sessions/<slug> \\
        --aup ~/Local-drive/pod22/Pod22.aup4 [--dry-run]

分工:AI 先照 cutplan 剪完,MM 只在 Audacity 上拉邊界。

    1. export:最近一次出片的 cut_map.json(保留段,原始錄音時間軸)→
       `_asset/audacity_AI.txt`。在 Audacity 匯入成標籤軌、改名 `AI`,
       再複製一條改名 `MM`。
    2. MM 只動 `MM` 那條:拉窄＝多剪、拉寬＝少剪、刪標籤＝整段不要、
       新拉一個＝把剪掉的救回來。標籤文字不用打。存檔(⌘S)。
    3. apply:直接讀 .aup4(不用匯出),比對 AI 與 MM 兩條軌的覆蓋範圍,
       差異寫回 cutplan:多剪 → `## ✂ 起-迄 audacity`、少剪 → `## ✚ 起-迄 audacity`。
       之後照常 cut.py 出片。

為什麼比「覆蓋範圍」不比「第幾個標籤」:MM 刪一個、新增一個、順序被
Audacity 重排,一對一配對就全錯;聯集相減不在乎這些。

為什麼 AI 軌要放在同一個 .aup4:差異的基準跟 MM 改的東西在同一個檔案裡,
cutplan 之後又被改過、又出過片,也不會拿錯基準去比。

時間軸:Audacity 裡開的分軌 WAV(`MIC*_*.WAV`)跟 source.wav 同長(EP22
實測 3042.915556s 全等),標籤時間＝cutplan 的原始時間軸,不用換算。
不要在 Audacity 裡開剪過的成品或 .pertrack_bus.wav。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_paths import work_dir  # noqa: E402

TAG = "audacity"
JITTER = 0.01      # Audacity 吸附/拖曳的誤差,小於這個的差異不算人審決定
EDGE_RE = re.compile(r"^##\s*(✂|✚)\s*([\d:.]+)\s*[-–~]\s*([\d:.]+)\s+" + TAG + r"\s*$")


class AupError(Exception):
    pass


# ── .aup4 讀檔 ──────────────────────────────────────────────────────────

def _connect(path: Path) -> sqlite3.Connection:
    """Audacity 開著 → 資料在 -wal,要用 mode=ro 才讀得到最新存檔;
    關掉後(WAL 模式、沒有 -shm)mode=ro 會 error 14,改 immutable。"""
    uri = path.resolve().as_uri()
    try:
        con = sqlite3.connect(f"{uri}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM project LIMIT 1")
        return con
    except sqlite3.DatabaseError as e:
        if Path(str(path) + "-wal").exists():
            raise AupError(f"{path.name} 讀不到:{e}") from e
    try:
        con = sqlite3.connect(f"{uri}?mode=ro&immutable=1", uri=True)
        con.execute("SELECT 1 FROM project LIMIT 1")
        return con
    except sqlite3.DatabaseError as e:
        raise AupError(f"{path.name} 不是 Audacity 專案檔(.aup4):{e}") from e


def _decode(dict_blob: bytes, doc_blob: bytes) -> list[tuple]:
    """Audacity binary XML(ProjectSerializer)→ [("start", tag, attrs) | ("end", tag)]。
    op 16 是 4.x 新增的二進位資料(縮圖 PNG),跳過。"""
    names: dict[int, str] = {}
    out: list[tuple] = []
    enc = {1: "utf-8", 2: "utf-16-le", 4: "utf-32-le"}
    cs = 4
    for buf in (dict_blob, doc_blob):
        p = 0

        def rd(fmt):
            nonlocal p
            v = struct.unpack_from(fmt, buf, p)
            p += struct.calcsize(fmt)
            return v[0] if len(v) == 1 else v

        def text(n):
            nonlocal p
            t = buf[p:p + n].decode(enc[cs])
            p += n
            return t

        def attr(k, v):
            if out and out[-1][0] == "start":
                out[-1][2][names.get(k, k)] = v

        while p < len(buf):
            op = rd("<B")
            if op == 0:
                cs = rd("<B")
            elif op == 15:
                i, n = rd("<HH")
                names[i] = text(n)
            elif op == 1:
                out.append(("start", names.get(rd("<H")), {}))
            elif op == 2:
                out.append(("end", names.get(rd("<H"))))
            elif op == 3:
                i, n = rd("<Hi")
                attr(i, text(n))
            elif op in (4, 6):
                attr(*rd("<Hi"))
            elif op == 5:
                attr(*rd("<HB"))
            elif op == 7:
                attr(*rd("<Hq"))
            elif op == 8:
                attr(*rd("<HI"))
            elif op in (9, 10):
                i, v, _ = rd("<Hfi" if op == 9 else "<Hdi")
                attr(i, v)
            elif op in (11, 12):
                p += rd("<i")          # 二進位資料/原始字串,不需要,不解碼
            elif op in (13, 14):
                pass
            elif op == 16:
                _, n = rd("<HI")
                p += n
            else:
                raise AupError(f"不認得的 binary XML opcode {op}(位置 {p - 1}),"
                               "Audacity 版本是不是又改格式了?")
    return out


def read_aup4(path: Path) -> list[dict]:
    """→ [{"name": 標籤軌名, "labels": [(t, t1, title), ...]}],照專案裡的順序。"""
    if not path.exists():
        raise AupError(f"找不到 {path}")
    con = _connect(path)
    try:
        row = con.execute("SELECT dict, doc FROM project WHERE id = 1").fetchone()
    finally:
        con.close()
    if not row:
        raise AupError(f"{path.name} 裡沒有存檔內容(還沒按過 ⌘S?)")
    tracks, cur = [], None
    for ev in _decode(row[0], row[1]):
        if ev[0] == "start" and ev[1] == "labeltrack":
            cur = {"name": ev[2].get("name", ""), "labels": []}
            tracks.append(cur)
        elif ev[0] == "start" and ev[1] == "label" and cur is not None:
            a = ev[2]
            cur["labels"].append((a.get("t", 0.0), a.get("t1", 0.0), a.get("title", "")))
        elif ev[0] == "end" and ev[1] == "labeltrack":
            cur = None
    return tracks


# ── 區間運算 ────────────────────────────────────────────────────────────

def _union(ranges) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted([list(r[:2]) for r in ranges if r[1] > r[0]]):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _minus(xs, ys, min_len: float = 0.0) -> list[list[float]]:
    ys = _union(ys)
    out = []
    for a, b in _union(xs):
        cur = a
        for ya, yb in ys:
            if yb <= cur or ya >= b:
                continue
            if ya - cur > min_len:
                out.append([cur, ya])
            cur = max(cur, yb)
        if b - cur > min_len:
            out.append([cur, b])
    return out


def diff_tracks(ai, mm) -> tuple[list[list[float]], list[list[float]]]:
    """AI／MM 兩條軌的覆蓋範圍相減 → (多剪 ✂, 救回 ✚),各自已合併排序。"""
    def r(xs):
        return [[round(a, 2), round(b, 2)] for a, b in xs
                if round(b, 2) > round(a, 2)]
    return r(_minus(ai, mm, JITTER)), r(_minus(mm, ai, JITTER))


def labels_txt(ranges) -> str:
    """Audacity 匯入標籤的格式:起\\t迄\\t標籤(秒,六位小數)。標籤只放序號。"""
    return "".join(f"{a:.6f}\t{b:.6f}\t{i}\n" for i, (a, b) in enumerate(ranges, 1))


# ── 寫回 cutplan ────────────────────────────────────────────────────────

def _ts(sec: float) -> str:
    m, s = divmod(round(sec, 2), 60)
    return f"{int(m)}:{s:05.2f}"


def _parse_ts(tok: str) -> float:
    secs = 0.0
    for p in tok.split(":"):
        secs = secs * 60 + float(p)
    return secs


def merge_edges(text: str, cuts, keeps) -> str:
    """把這一輪的差異併進 cutplan 既有的 audacity 行,整組重寫在 ⚙ 行之後。

    每一輪的 AI 軌都是「上一輪套完之後」出的片,所以差異是累加的;新的跟舊的
    反向決定重疊時,新的贏(上次救回、這次又剪 → 剪)。人手寫的 ✂ 不碰。"""
    lines = text.splitlines()
    old_c, old_k, kept = [], [], []
    for ln in lines:
        m = EDGE_RE.match(ln.strip())
        if m:
            (old_c if m.group(1) == "✂" else old_k).append(
                [_parse_ts(m.group(2)), _parse_ts(m.group(3))])
        else:
            kept.append(ln)
    all_c = _union(_minus(old_c, keeps) + [list(c) for c in cuts])
    all_k = _union(_minus(old_k, cuts) + [list(k) for k in keeps])
    block = ([f"## ✂ {_ts(a)}-{_ts(b)} {TAG}" for a, b in all_c]
             + [f"## ✚ {_ts(a)}-{_ts(b)} {TAG}" for a, b in all_k])
    cfg = [i for i, ln in enumerate(kept) if re.match(r"^##\s*⚙", ln.strip())]
    if cfg:
        at = cfg[-1] + 1
    else:
        at = next((i for i, ln in enumerate(kept) if re.match(r"^- \[[ xX]\]", ln)),
                  len(kept))
    out = kept[:at] + block + kept[at:]
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


# ── CLI ────────────────────────────────────────────────────────────────

def cmd_export(args) -> None:
    wd = work_dir(args.session)
    cm = wd / "cut_map.json"
    if not cm.exists():
        sys.exit(f"[audacity] FAIL: 找不到 {cm} — 先用 cut.py 出一次片")
    ranges = [[r["src_start"], r["src_end"]]
              for r in json.loads(cm.read_text(encoding="utf-8"))["ranges"]]
    out = wd / "audacity_AI.txt"
    out.write_text(labels_txt(ranges), encoding="utf-8")
    print(f"[audacity] {len(ranges)} 個保留段 → {out}")
    print("  1. Audacity:檔案 → 匯入 → 標籤,選這個檔;標籤軌改名 AI")
    print("  2. 複製 AI 那條軌,改名 MM;之後只動 MM")
    print("  3. 拉完存檔(⌘S),跑 apply")


def cmd_apply(args) -> None:
    try:
        tracks = read_aup4(args.aup)
    except AupError as e:
        sys.exit(f"[audacity] FAIL: {e}")
    by = {t["name"]: t["labels"] for t in tracks}
    missing = [n for n in ("AI", "MM") if n not in by]
    if missing:
        sys.exit(f"[audacity] FAIL: 找不到標籤軌 {'、'.join(missing)};"
                 f"專案裡有:{'、'.join(by) or '(沒有標籤軌)'}")
    wd = work_dir(args.session)
    cm, ai_txt = wd / "cut_map.json", wd / "audacity_AI.txt"
    if cm.exists() and (not ai_txt.exists()
                        or cm.stat().st_mtime > ai_txt.stat().st_mtime):
        # AI 軌必須是「上一輪套完之後」的成品,差異才是這一輪的決定;拿舊基準比,
        # 上一輪已寫進 cutplan 的 ✂/✚ 會被當成沒發生、MM 改回去也撤不掉
        print("[audacity] ⚠ cut_map.json 比 audacity_AI.txt 新 —— 出片後沒重新"
              " export,Audacity 裡的 AI 軌可能是上一版的保留段")
    cuts, keeps = diff_tracks(by["AI"], by["MM"])
    print(f"[audacity] ✂ 多剪 {len(cuts)} 段({sum(b - a for a, b in cuts):.2f}s)"
          f"、✚ 救回 {len(keeps)} 段({sum(b - a for a, b in keeps):.2f}s)")
    for mark, rs in (("✂", cuts), ("✚", keeps)):
        for a, b in rs:
            print(f"  {mark} {_ts(a)}-{_ts(b)}")
    plan = work_dir(args.session) / args.plan
    if not plan.exists():
        sys.exit(f"[audacity] FAIL: 找不到 {plan}")
    old = plan.read_text(encoding="utf-8")
    new = merge_edges(old, cuts, keeps)
    if new == old:
        print("[audacity] cutplan 不用改")
        return
    if args.dry_run:
        print("[audacity] --dry-run:cutplan 沒動")
        return
    shutil.copy2(plan, plan.with_name(plan.stem + ".before-audacity.md"))
    plan.write_text(new, encoding="utf-8")
    print(f"[audacity] 已寫回 {plan}(改前備份 {plan.stem}.before-audacity.md)")
    print("  下一步:cut.py 出片;出完再 export 一次,下一輪的 AI 軌才是新的")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="cut_map.json → Audacity 標籤檔")
    e.add_argument("--session", type=Path, required=True)
    a = sub.add_parser("apply", help=".aup4 的 AI／MM 差異 → cutplan ✂／✚")
    a.add_argument("--session", type=Path, required=True)
    a.add_argument("--aup", type=Path, required=True)
    a.add_argument("--plan", default="cutplan.md")
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    {"export": cmd_export, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
