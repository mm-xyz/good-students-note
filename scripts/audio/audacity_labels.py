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
import subprocess
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


ENC = {1: "utf-8", 2: "utf-16-le", 4: "utf-32-le"}


def _ops(dict_blob: bytes, doc_blob: bytes):
    """Audacity binary XML(ProjectSerializer)逐 op 切開 →
    (names, cs, [(op, 起, 迄, 名稱, 值)]),起迄是 doc 內的 byte 位置。
    op 16 是 4.x 新增的二進位資料(縮圖 PNG),值不解。"""
    names: dict[int, str] = {}
    cs = 4
    out: list[tuple] = []
    for which, buf in ((0, dict_blob), (1, doc_blob)):
        p = 0
        while p < len(buf):
            st = p
            op = buf[p]
            p += 1
            name = val = None
            if op == 0:
                cs = buf[p]
                p += 1
            elif op == 15:
                i, n = struct.unpack_from("<HH", buf, p)
                p += 4
                names[i] = buf[p:p + n].decode(ENC[cs])
                p += n
            elif op in (1, 2):
                name = names.get(struct.unpack_from("<H", buf, p)[0])
                p += 2
            elif op == 3:
                i, n = struct.unpack_from("<Hi", buf, p)
                p += 6
                name, val = names.get(i), buf[p:p + n].decode(ENC[cs])
                p += n
            elif op in (4, 5, 6, 7, 8, 9, 10):
                fmt = {4: "<Hi", 5: "<HB", 6: "<Hi", 7: "<Hq", 8: "<HI",
                       9: "<Hfi", 10: "<Hdi"}[op]
                v = struct.unpack_from(fmt, buf, p)
                p += struct.calcsize(fmt)
                name, val = names.get(v[0]), v[1]
            elif op in (11, 12):
                p += 4 + struct.unpack_from("<i", buf, p)[0]
            elif op in (13, 14):
                pass
            elif op == 16:
                p += 6 + struct.unpack_from("<HI", buf, p)[1]
            else:
                raise AupError(f"不認得的 binary XML opcode {op}(位置 {st}),"
                               "Audacity 版本是不是又改格式了?")
            if which:
                out.append((op, st, p, name, val))
    return names, cs, out


def _decode(dict_blob: bytes, doc_blob: bytes) -> list[tuple]:
    """→ [("start", tag, attrs) | ("end", tag)]。"""
    out: list[tuple] = []
    for op, _, _, name, val in _ops(dict_blob, doc_blob)[2]:
        if op == 1:
            out.append(("start", name, {}))
        elif op == 2:
            out.append(("end", name))
        elif 3 <= op <= 10 and out and out[-1][0] == "start":
            out[-1][2][name] = val
    return out


def _tree(dict_blob: bytes, doc_blob: bytes):
    """→ (names, cs, 頂層元素),元素 = {"tag","s","attr_end","e","attrs","kids"};
    attrs[名稱] = (op, 起, 迄, 值)。改檔只在這些 byte 位置上剪貼,其餘原封不動。"""
    names, cs, ops = _ops(dict_blob, doc_blob)
    top: list[dict] = []
    stack: list[dict] = []
    for op, s, e, name, val in ops:
        if op == 1:
            if stack and stack[-1]["attr_end"] is None:
                stack[-1]["attr_end"] = s
            el = {"tag": name, "s": s, "attr_end": None, "e": None,
                  "attrs": {}, "kids": []}
            (stack[-1]["kids"] if stack else top).append(el)
            stack.append(el)
        elif op == 2:
            el = stack.pop()
            if el["attr_end"] is None:
                el["attr_end"] = s
            el["e"] = e
        elif 3 <= op <= 10 and stack and stack[-1]["attr_end"] is None:
            stack[-1]["attrs"][name] = (op, s, e, val)
    return names, cs, top


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


# ── .aup4 寫檔(inject)───────────────────────────────────────────────

def _descend(el, tag):
    for k in el["kids"]:
        if k["tag"] == tag:
            yield k
        yield from _descend(k, tag)


def read_clips(path: Path) -> dict[str, list[tuple[float, float, float]]]:
    """→ {音軌名: [(offset, trimLeft, trimRight), ...]}(驗證 heal 用)。"""
    con = _connect(path)
    try:
        d, doc = con.execute("SELECT dict, doc FROM project WHERE id = 1").fetchone()
    finally:
        con.close()
    _, _, top = _tree(d, doc)
    out = {}
    for prj in top:
        for wt in (k for k in prj["kids"] if k["tag"] == "wavetrack"):
            out[wt["attrs"]["name"][3]] = [
                tuple(c["attrs"][a][3] for a in ("offset", "trimLeft", "trimRight"))
                for c in wt["kids"] if c["tag"] == "waveclip"]
    return out


def _in_use(path: Path) -> bool:
    """Audacity 開著:-wal 有內容,或有程式開著這幾個檔。唯讀查詢(read_aup4)
    會留下 0 byte 的 -wal 與 -shm,不算。"""
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        return True
    files = [str(path)] + [str(path) + s for s in ("-wal", "-shm")
                           if Path(str(path) + s).exists()]
    try:
        r = subprocess.run(["lsof", "-t", *files], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return bool(r.stdout.strip())


def inject(path: Path, ai_ranges, drop_label_tracks: bool, heal_clips: bool,
           titles=None) -> dict:
    """把 AI／MM 兩條標籤軌(內容都是 ai_ranges)直接寫進 .aup4。

    - 既有的 AI／MM 軌一律先拿掉(重跑冪等);drop_label_tracks 連其他標籤軌一起拿掉。
    - heal_clips:同一條音軌被切成多個 clip(Audacity 4 的刪除是非破壞性的,每個
      clip 都還帶著完整原音)→ 併回一個、trim 取最小,刪掉的段落接回來。只在
      各 clip 的 offset 與 blockid 全同時才做,否則整個中止、不寫入。
    - 只在 byte 位置上剪貼,其他元素(音訊、縮圖、效果)原封不動。
    - Audacity 開著(有 -wal)就拒絕,不跟它搶同一個檔。
    - titles:每個標籤的文字(block_titles 的輸出),沒給就用序號。
    """
    if _in_use(path):
        raise AupError(f"{path.name} 還被 Audacity 開著 —— 先關掉 Audacity"
                       ",或先複製一份出來改")
    con = sqlite3.connect(path)
    try:
        d, doc = con.execute("SELECT dict, doc FROM project WHERE id = 1").fetchone()
        names, cs, top = _tree(d, doc)
        ids = {n: i for i, n in names.items()}
        need = ["labeltrack", "label", "t", "t1", "title", "name", "numlabels"]
        miss = [n for n in need if n not in ids]
        if miss:
            raise AupError(f"專案字典缺 {'、'.join(miss)} —— 先在 Audacity 隨便加一條"
                           "標籤軌、存檔,再跑一次")
        prj = next((el for el in top if el["tag"] == "project"), None)
        if prj is None:
            raise AupError("找不到 <project> 元素")
        edits: list[tuple[int, int, bytes]] = []
        tracks = [k for k in prj["kids"] if k["tag"] in ("wavetrack", "labeltrack")]
        lts = [k for k in tracks if k["tag"] == "labeltrack"]
        # 新軌的屬性抄既有標籤軌(Audacity 認得的那一組),沒有就只給 name/numlabels
        tpl = lts[0] if lts else None
        for lt in lts:
            if drop_label_tracks or lt["attrs"].get("name", (0, 0, 0, ""))[3] in ("AI", "MM"):
                edits.append((lt["s"], lt["e"], b""))

        def s_(name, v):
            raw = v.encode(ENC[cs])
            return b"\x03" + struct.pack("<Hi", ids[name], len(raw)) + raw

        digits = 19
        if tpl:
            first = next(_descend(tpl, "label"), None)
            if first and first["attrs"].get("t", (0,))[0] == 10:
                _, a, b_, _ = first["attrs"]["t"]
                digits = struct.unpack_from("<i", doc, b_ - 4)[0]

        def track(name):
            out = bytearray(b"\x01" + struct.pack("<H", ids["labeltrack"]))
            if tpl:
                for an, (op, a, b_, v) in sorted(tpl["attrs"].items(), key=lambda x: x[1][1]):
                    if an == "name":
                        out += s_("name", name)
                    elif an == "numlabels":
                        out += b"\x04" + struct.pack("<Hi", ids[an], len(ai_ranges))
                    elif an in ("isSelected", "isFocused") and op in (4, 5, 6):
                        out += doc[a:a + 3] + b"\x00" * (b_ - a - 3)
                    else:
                        out += doc[a:b_]
            else:
                out += s_("name", name)
                out += b"\x04" + struct.pack("<Hi", ids["numlabels"], len(ai_ranges))
            for i, (a, b_) in enumerate(ai_ranges, 1):
                out += b"\x01" + struct.pack("<H", ids["label"])
                out += b"\x0a" + struct.pack("<Hdi", ids["t"], float(a), digits)
                out += b"\x0a" + struct.pack("<Hdi", ids["t1"], float(b_), digits)
                out += s_("title", titles[i - 1] if titles else str(i))
                out += b"\x02" + struct.pack("<H", ids["label"])
            out += b"\x02" + struct.pack("<H", ids["labeltrack"])
            return bytes(out)

        at = max(k["e"] for k in tracks) if tracks else prj["attr_end"]
        edits.append((at, at, track("AI") + track("MM")))

        healed = []
        if heal_clips:
            for wt in (k for k in tracks if k["tag"] == "wavetrack"):
                clips = [c for c in wt["kids"] if c["tag"] == "waveclip"]
                if len(clips) < 2:
                    continue
                sig = {(c["attrs"]["offset"][3],
                        tuple(b["attrs"]["blockid"][3] for b in _descend(c, "waveblock")))
                       for c in clips}
                name = wt["attrs"]["name"][3]
                if len(sig) != 1:
                    raise AupError(f"{name} 的 {len(clips)} 個 clip 音訊不同(offset 或"
                                   " blockid 不一致),不能自動接回;沒有寫入任何東西")
                keep = clips[0]
                for an in ("trimLeft", "trimRight"):
                    op, a, b_, _ = keep["attrs"][an]
                    v = min(c["attrs"][an][3] for c in clips)
                    edits.append((a + 3, a + 11, struct.pack("<d", v)))
                for c in clips[1:]:
                    edits.append((c["s"], c["e"], b""))
                healed.append(name)

        new = bytearray(doc)
        for a, b_, rep in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
            new[a:b_] = rep
        _tree(d, bytes(new))            # 寫之前自己先解一次,壞了就不寫
        con.execute("UPDATE project SET doc = ? WHERE id = 1", (bytes(new),))
        con.commit()
    finally:
        con.close()
    return {"labels": len(ai_ranges), "healed": healed}


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


def block_titles(ranges, blocks) -> list[str]:
    """每個保留段 → 「B0002–B0010」(段內第一個與最後一個 block 的 id)。

    blocks 是 cutplan.json 的 blocks(要有 id/start/end)。block 有一半以上落在
    段內才算這段的;段內沒有 block 就退回序號。MM 看到標籤就知道拉的是哪幾句,
    回 cutplan 找得到。apply 只比時間範圍、不看標籤文字,所以這裡怎麼命名都不影響差異。"""
    out = []
    for i, (a, b) in enumerate(ranges, 1):
        ids = [bl["id"] for bl in blocks
               if bl["end"] > bl["start"]
               and min(bl["end"], b) - max(bl["start"], a) > 0.5 * (bl["end"] - bl["start"])]
        if not ids:
            out.append(str(i))
        elif len(ids) == 1 or ids[0] == ids[-1]:
            out.append(ids[0])
        else:
            out.append(f"{ids[0]}–{ids[-1]}")
    return out


def _titles_for(wd: Path, ranges):
    """cutplan.json 在就用 block id 命名,讀不到就 None(序號)。"""
    cj = wd / "cutplan.json"
    try:
        blocks = json.loads(cj.read_text(encoding="utf-8"))["blocks"]
        return block_titles(ranges, blocks)
    except (OSError, ValueError, KeyError):
        return None


def labels_txt(ranges, titles=None) -> str:
    """Audacity 匯入標籤的格式:起\\t迄\\t標籤(秒,六位小數)。沒給 titles 標籤只放序號。"""
    return "".join(f"{a:.6f}\t{b:.6f}\t{titles[i - 1] if titles else i}\n"
                   for i, (a, b) in enumerate(ranges, 1))


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
    out.write_text(labels_txt(ranges, _titles_for(wd, ranges)), encoding="utf-8")
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


def cmd_inject(args) -> None:
    cm = work_dir(args.session) / "cut_map.json"
    if not cm.exists():
        sys.exit(f"[audacity] FAIL: 找不到 {cm} — 先用 cut.py 出一次片")
    ranges = [[r["src_start"], r["src_end"]]
              for r in json.loads(cm.read_text(encoding="utf-8"))["ranges"]]
    try:
        r = inject(args.aup, ranges, args.drop_label_tracks, args.heal_clips,
                   _titles_for(work_dir(args.session), ranges))
    except AupError as e:
        sys.exit(f"[audacity] FAIL: {e}")
    print(f"[audacity] {args.aup.name}:寫入 AI／MM 兩條標籤軌,各 {r['labels']} 個保留段"
          + (f";接回 {'、'.join(r['healed'])} 被刪掉的段落" if r["healed"] else ""))
    print("  打開 Audacity,只拉 MM 那條,⌘S 存檔,再跑 apply")


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
    j = sub.add_parser("inject", help="cut_map.json → 直接寫進 .aup4 的 AI／MM 標籤軌")
    j.add_argument("--session", type=Path, required=True)
    j.add_argument("--aup", type=Path, required=True)
    j.add_argument("--drop-label-tracks", action="store_true",
                   help="AI／MM 以外的標籤軌也一起拿掉")
    j.add_argument("--heal-clips", action="store_true",
                   help="被切成多段的音軌併回一段(刪掉的段落接回來)")
    args = ap.parse_args()
    {"export": cmd_export, "apply": cmd_apply, "inject": cmd_inject}[args.cmd](args)


if __name__ == "__main__":
    main()
