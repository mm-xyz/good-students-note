#!/usr/bin/env python3
"""test_audacity_labels.py — Audacity 細剪來回轉(#1181)。

鎖的是:.aup4 讀檔(SQLite＋binary XML,Audacity 開著＝WAL 模式、關掉＝無
-shm 兩種狀態都要讀得到)、AI／MM 兩條標籤軌的差異、寫回 cutplan 的
`## ✂`／`## ✚` 行(重跑冪等、新舊衝突時新的贏)。

測試用的 .aup4 是照 Audacity 4.0.2 的 binary XML 格式自己編出來的
(含 4.x 新增的 op 16 二進位資料,真檔的縮圖 PNG 就是這樣存)。

跑法:
    python3 scripts/tests/test_audacity_labels.py
"""
from __future__ import annotations

import json
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
REPO_ROOT = AUDIO_DIR.parent.parent
sys.path.insert(0, str(AUDIO_DIR))

from audacity_labels import (read_aup4, labels_txt, diff_tracks,  # noqa: E402
                             merge_edges, AupError, inject, read_clips)
from render_cut import parse_ts  # noqa: E402


def _enc(tracks: list[tuple[str, list[tuple[float, float, str]]]]) -> tuple[bytes, bytes]:
    """[(標籤軌名, [(t, t1, title)])] → (dict blob, doc blob)。前面放一條
    wavetrack＋縮圖(op 16),確保讀檔會正確跳過不認得的元素。"""
    names = ["project", "rate", "wavetrack", "name", "thumbnail", "data",
             "labeltrack", "numlabels", "label", "t", "t1", "title"]
    ids = {n: i for i, n in enumerate(names)}
    d = bytearray(b"\x00\x04")
    for n, i in ids.items():
        raw = n.encode("utf-32-le")
        d += b"\x0f" + struct.pack("<HH", i, len(raw)) + raw

    def s(n, v):
        raw = v.encode("utf-32-le")
        return b"\x03" + struct.pack("<Hi", ids[n], len(raw)) + raw

    def dbl(n, v):
        return b"\x0a" + struct.pack("<Hdi", ids[n], v, 7)

    head = "<?xml ".encode("utf-32-le")      # 真檔開頭就是 op 12(raw 字串)
    doc = bytearray(b"\x0c" + struct.pack("<i", len(head)) + head)
    doc += b"\x01" + struct.pack("<H", ids["project"]) + dbl("rate", 44100.0)
    doc += b"\x01" + struct.pack("<H", ids["wavetrack"]) + s("name", "MIC1_Mars")
    doc += b"\x01" + struct.pack("<H", ids["thumbnail"])
    doc += b"\x10" + struct.pack("<HI", ids["data"], 4) + b"\x89PNG"
    doc += b"\x02" + struct.pack("<H", ids["thumbnail"])
    doc += b"\x02" + struct.pack("<H", ids["wavetrack"])
    for name, labels in tracks:
        doc += b"\x01" + struct.pack("<H", ids["labeltrack"]) + s("name", name)
        doc += b"\x04" + struct.pack("<Hi", ids["numlabels"], len(labels))
        for t, t1, title in labels:
            doc += (b"\x01" + struct.pack("<H", ids["label"])
                    + dbl("t", t) + dbl("t1", t1) + s("title", title)
                    + b"\x02" + struct.pack("<H", ids["label"]))
        doc += b"\x02" + struct.pack("<H", ids["labeltrack"])
    doc += b"\x02" + struct.pack("<H", ids["project"])
    return bytes(d), bytes(doc)


def make_aup4(path: Path, tracks, wal: bool = False) -> sqlite3.Connection | None:
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE project(id INTEGER PRIMARY KEY, dict BLOB, doc BLOB)")
    d, doc = _enc(tracks)
    con.execute("INSERT INTO project VALUES (1, ?, ?)", (d, doc))
    con.commit()
    if wal:
        return con          # 不關:模擬 Audacity 還開著,資料只在 -wal 裡
    con.close()
    return None


class TestReadAup4(unittest.TestCase):
    TRACKS = [("AI", [(1.0, 2.0, "1"), (3.0, 4.5, "2")]),
              ("MM", [(1.2, 2.0, ""), (3.0, 5.0, "x")])]

    def test_reads_label_tracks_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ep.aup4"
            make_aup4(p, self.TRACKS)
            got = read_aup4(p)
        self.assertEqual([t["name"] for t in got], ["AI", "MM"])
        self.assertEqual(got[1]["labels"], [(1.2, 2.0, ""), (3.0, 5.0, "x")])

    def test_reads_while_audacity_holds_wal(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ep.aup4"
            con = make_aup4(p, self.TRACKS, wal=True)
            try:
                self.assertTrue(Path(str(p) + "-wal").exists())
                got = read_aup4(p)
            finally:
                con.close()
        self.assertEqual(len(got), 2)

    def test_reads_closed_wal_project_in_readonly_dir(self):
        """真檔:WAL 模式的專案關掉後沒有 -shm,mode=ro 開不起來(error 14)。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ep.aup4"
            con = make_aup4(p, self.TRACKS, wal=True)
            con.close()                    # 最後一個連線關閉 → checkpoint、刪 -wal/-shm
            self.assertFalse(Path(str(p) + "-shm").exists())
            Path(td).chmod(0o555)
            try:
                got = read_aup4(p)
            finally:
                Path(td).chmod(0o755)
        self.assertEqual(got[0]["labels"][1], (3.0, 4.5, "2"))

    def test_not_an_audacity_project(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.aup4"
            p.write_bytes(b"nope")
            with self.assertRaises(AupError):
                read_aup4(p)


class TestLabelsTxt(unittest.TestCase):
    def test_format_is_audacity_label_import(self):
        txt = labels_txt([[3.03, 17.744], [18.46, 19.49]])
        self.assertEqual(txt, "3.030000\t17.744000\t1\n18.460000\t19.490000\t2\n")


class TestDiffTracks(unittest.TestCase):
    AI = [(1.0, 2.0, "1"), (3.0, 4.0, "2"), (6.0, 7.0, "3")]

    def test_unchanged_is_empty(self):
        self.assertEqual(diff_tracks(self.AI, list(self.AI)), ([], []))

    def test_shrink_is_cut_and_extend_is_keep(self):
        mm = [(1.3, 2.0, ""), (3.0, 4.4, ""), (6.0, 7.0, "")]
        self.assertEqual(diff_tracks(self.AI, mm), ([[1.0, 1.3]], [[4.0, 4.4]]))

    def test_deleted_label_cuts_whole_range_new_label_keeps(self):
        mm = [(1.0, 2.0, ""), (5.0, 5.5, "新"), (6.0, 7.0, "")]
        self.assertEqual(diff_tracks(self.AI, mm), ([[3.0, 4.0]], [[5.0, 5.5]]))

    def test_snap_jitter_ignored(self):
        mm = [(1.004, 2.0, ""), (3.0, 3.995, ""), (6.0, 7.0, "")]
        self.assertEqual(diff_tracks(self.AI, mm), ([], []))

    def test_rounding_never_yields_zero_length(self):
        """驗收 #3:差異 > 0.01s 但四捨五入到兩位後 a == b,不能寫出零長度行。"""
        ai = [(1.0, 2.0, "")]
        mm = [(1.0, 1.98901, "")]          # 差 0.01099s → [1.99, 2.0] ok
        mm2 = [(1.0, 1.9949, ""), (1.9951, 2.0, "")]
        self.assertEqual(diff_tracks(ai, mm), ([[1.99, 2.0]], []))
        cuts, keeps = diff_tracks([(1.0, 1.004, "")], [(1.0, 1.0151, "")])
        self.assertTrue(all(b > a for a, b in cuts + keeps), (cuts, keeps))
        self.assertEqual(diff_tracks(ai, mm2), ([], []))

    def test_order_and_overlap_in_mm_track_do_not_matter(self):
        mm = [(6.0, 7.0, ""), (3.0, 4.0, ""), (1.0, 1.8, ""), (1.5, 2.0, "")]
        self.assertEqual(diff_tracks(self.AI, mm), ([], []))


PLAN = ("# Cutplan — ep\n\n"
        "## ⚙ max-pause=0.9\n"
        "## ✂ 0:10.0-0:11.0 手動\n\n"
        "- [x] B0001 [0:00–0:01] [Sarah] 第一句。\n")


def edges(text: str) -> tuple[list, list]:
    cuts, keeps = [], []
    for ln in text.splitlines():
        for mark, bucket in (("✂", cuts), ("✚", keeps)):
            if ln.startswith(f"## {mark} ") and ln.endswith(" audacity"):
                a, b = ln.split()[2].split("-")
                bucket.append([round(parse_ts(a), 2), round(parse_ts(b), 2)])
    return cuts, keeps


class TestMergeEdges(unittest.TestCase):
    def test_writes_after_config_and_keeps_manual_cut(self):
        out = merge_edges(PLAN, [[61.5, 62.25]], [[3000.0, 3001.5]])
        lines = out.splitlines()
        i = lines.index("## ⚙ max-pause=0.9")
        self.assertEqual(lines[i + 1], "## ✂ 1:01.50-1:02.25 audacity")
        self.assertEqual(lines[i + 2], "## ✚ 50:00.00-50:01.50 audacity")
        self.assertIn("## ✂ 0:10.0-0:11.0 手動", lines)
        self.assertIn("- [x] B0001 [0:00–0:01] [Sarah] 第一句。", lines)

    def test_rerun_with_no_diff_is_idempotent(self):
        once = merge_edges(PLAN, [[1.0, 2.0]], [[5.0, 6.0]])
        self.assertEqual(merge_edges(once, [], []), once)

    def test_accumulates_and_new_edge_beats_old_opposite(self):
        once = merge_edges(PLAN, [[1.0, 2.0]], [[5.0, 6.0]])
        # 第二輪:把上次救回的 5.5–6.0 又剪掉、把上次剪的 1.0–1.5 救回來
        twice = merge_edges(once, [[5.5, 6.0]], [[1.0, 1.5]])
        self.assertEqual(edges(twice), ([[1.5, 2.0], [5.5, 6.0]],
                                        [[1.0, 1.5], [5.0, 5.5]]))

    def test_no_config_line_inserts_before_first_block(self):
        plan = "# Cutplan\n\n- [x] B0001 [0:00–0:01] 一。\n"
        out = merge_edges(plan, [[1.0, 2.0]], [])
        self.assertLess(out.index("## ✂"), out.index("- [x] B0001"))


# ── inject:直接改 .aup4(#1181 第二輪)──────────────────────────────────
# 通用的樹狀編碼器:(tag, [(attr, op, value)], [children])。
# op: "s" 字串(3)、"i" int(4)、"d" double(10)、"b" bool(5)

def _enc_tree(root) -> tuple[bytes, bytes]:
    names: list[str] = []

    def nid(n):
        if n not in names:
            names.append(n)
        return names.index(n)

    def walk(node):
        tag, attrs, kids = node
        out = bytearray(b"\x01" + struct.pack("<H", nid(tag)))
        for k, op, v in attrs:
            if op == "s":
                raw = v.encode("utf-32-le")
                out += b"\x03" + struct.pack("<Hi", nid(k), len(raw)) + raw
            elif op == "i":
                out += b"\x04" + struct.pack("<Hi", nid(k), v)
            elif op == "b":
                out += b"\x05" + struct.pack("<HB", nid(k), v)
            else:
                out += b"\x0a" + struct.pack("<Hdi", nid(k), v, 19)
        for c in kids:
            out += walk(c)
        out += b"\x02" + struct.pack("<H", nid(tag))
        return out

    head = "<?xml ".encode("utf-32-le")
    doc = b"\x0c" + struct.pack("<i", len(head)) + head + bytes(walk(root))
    d = bytearray(b"\x00\x04")
    for i, n in enumerate(names):
        raw = n.encode("utf-32-le")
        d += b"\x0f" + struct.pack("<HH", i, len(raw)) + raw
    return bytes(d), doc


def _clip(trim_l, trim_r, blocks=(1, 2)):
    seq = ("sequence", [("numsamples", "i", 441000)],
           [("waveblock", [("start", "i", i * 1000), ("blockid", "i", b)], [])
            for i, b in enumerate(blocks)])
    return ("waveclip", [("offset", "d", 0.0), ("trimLeft", "d", trim_l),
                         ("trimRight", "d", trim_r), ("name", "s", "Sarah")],
            [seq, ("envelope", [("numpoints", "i", 0)], [])])


def _ltrack(name, labels):
    return ("labeltrack", [("name", "s", name), ("isSelected", "b", 1),
                           ("height", "i", 60), ("numlabels", "i", len(labels))],
            [("label", [("t", "d", a), ("t1", "d", b), ("title", "s", s)], [])
             for a, b, s in labels])


def make_project(path: Path, sarah_clips, label_tracks) -> None:
    root = ("project", [("rate", "d", 44100.0)], [
        ("tags", [], []),
        ("wavetrack", [("name", "s", "MIC4_Sarah")], sarah_clips),
        *label_tracks,
        ("wavetrack", [("name", "s", "MIC1_Mars")], [_clip(0.0, 0.0, (7,))]),
        ("effects", [], []),
    ])
    d, doc = _enc_tree(root)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE project(id INTEGER PRIMARY KEY, dict BLOB, doc BLOB)")
    con.execute("INSERT INTO project VALUES (1, ?, ?)", (d, doc))
    con.commit()
    con.close()


class TestInject(unittest.TestCase):
    AI = [[1.0, 2.0], [3.0, 4.5]]

    def _proj(self, td, clips=None):
        p = Path(td) / "ep.aup4"
        make_project(p, clips or [_clip(0.0, 8.0), _clip(2.5, 0.0)],
                     [_ltrack("Labels-Sarah", [(0.5, 0.7, "TEST")]),
                      _ltrack("MM", [(9.0, 9.5, "舊")])])
        return p

    def test_adds_ai_and_mm_and_drops_other_label_tracks(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td)
            inject(p, self.AI, drop_label_tracks=True, heal_clips=False)
            got = read_aup4(p)
        self.assertEqual([t["name"] for t in got], ["AI", "MM"])
        self.assertEqual([x[:2] for x in got[0]["labels"]], [(1.0, 2.0), (3.0, 4.5)])
        self.assertEqual(got[1]["labels"], got[0]["labels"])

    def test_keeps_other_label_tracks_without_flag_but_replaces_ai_mm(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td)
            inject(p, self.AI, drop_label_tracks=False, heal_clips=False)
            got = read_aup4(p)
        self.assertEqual([t["name"] for t in got], ["Labels-Sarah", "AI", "MM"])
        self.assertEqual(len(got[2]["labels"]), 2)       # 舊 MM 被換掉

    def test_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td)
            inject(p, self.AI, drop_label_tracks=True, heal_clips=True)
            con = sqlite3.connect(p)
            once = con.execute("SELECT doc FROM project").fetchone()[0]
            con.close()
            inject(p, self.AI, drop_label_tracks=True, heal_clips=True)
            con = sqlite3.connect(p)
            twice = con.execute("SELECT doc FROM project").fetchone()[0]
            con.close()
        self.assertEqual(once, twice)

    def test_heal_rejoins_split_clip_sharing_same_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td)
            inject(p, self.AI, drop_label_tracks=True, heal_clips=True)
            clips = read_clips(p)
        self.assertEqual(clips["MIC4_Sarah"], [(0.0, 0.0, 0.0)])
        self.assertEqual(clips["MIC1_Mars"], [(0.0, 0.0, 0.0)])

    def test_heal_refuses_clips_with_different_audio(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td, clips=[_clip(0.0, 8.0, (1, 2)), _clip(2.5, 0.0, (3, 4))])
            with self.assertRaises(AupError):
                inject(p, self.AI, drop_label_tracks=True, heal_clips=True)
            clips = read_clips(p)
        self.assertEqual(len(clips["MIC4_Sarah"]), 2)       # 失敗不寫入

    def test_empty_wal_left_by_readonly_query_is_not_in_use(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td)
            Path(str(p) + "-wal").write_bytes(b"")
            inject(p, self.AI, drop_label_tracks=True, heal_clips=False)
            self.assertEqual([t["name"] for t in read_aup4(p)], ["AI", "MM"])

    def test_refuses_while_file_is_open_by_a_process(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td)
            holder = subprocess.Popen([sys.executable, "-c",
                                       "import sys,time;f=open(sys.argv[1],'rb');"
                                       "print('ok',flush=True);time.sleep(30)", str(p)],
                                      stdout=subprocess.PIPE, text=True)
            try:
                holder.stdout.readline()
                with self.assertRaises(AupError):
                    inject(p, self.AI, drop_label_tracks=True, heal_clips=False)
            finally:
                holder.kill()
                holder.wait()

    def test_refuses_while_audacity_holds_wal(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._proj(td)
            Path(str(p) + "-wal").write_bytes(b"x")
            with self.assertRaises(AupError):
                inject(p, self.AI, drop_label_tracks=True, heal_clips=False)


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(AUDIO_DIR / "audacity_labels.py"),
                               *args], capture_output=True, text=True, cwd=REPO_ROOT)

    def _session(self, td: str) -> Path:
        sdir = Path(td) / "ep"
        (sdir / "_asset").mkdir(parents=True)
        (sdir / "_asset" / "cutplan.md").write_text(PLAN, encoding="utf-8")
        (sdir / "_asset" / "cut_map.json").write_text(json.dumps(
            {"ranges": [{"src_start": 1.0, "src_end": 2.0, "dst_start": 0.0},
                        {"src_start": 3.0, "src_end": 4.0, "dst_start": 1.0}]}),
            encoding="utf-8")
        return sdir

    def test_export_writes_ai_labels(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._session(td)
            p = self._run("export", "--session", str(sdir))
            self.assertEqual(p.returncode, 0, p.stderr)
            txt = (sdir / "_asset" / "audacity_AI.txt").read_text(encoding="utf-8")
        self.assertEqual(txt.splitlines()[1], "3.000000\t4.000000\t2")

    def test_apply_updates_cutplan_and_backs_up(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._session(td)
            aup = Path(td) / "ep.aup4"
            make_aup4(aup, [("AI", [(1.0, 2.0, "1"), (3.0, 4.0, "2")]),
                            ("MM", [(1.0, 2.5, "1"), (3.2, 4.0, "2")])])
            p = self._run("apply", "--session", str(sdir), "--aup", str(aup))
            self.assertEqual(p.returncode, 0, p.stderr)
            plan = (sdir / "_asset" / "cutplan.md").read_text(encoding="utf-8")
            backup = sdir / "_asset" / "cutplan.before-audacity.md"
            self.assertEqual(backup.read_text(encoding="utf-8"), PLAN)
        self.assertEqual(edges(plan), ([[3.0, 3.2]], [[2.0, 2.5]]))
        self.assertIn("✂ 多剪 1 段", p.stdout)
        self.assertIn("✚ 救回 1 段", p.stdout)

    def test_apply_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._session(td)
            aup = Path(td) / "ep.aup4"
            make_aup4(aup, [("AI", [(1.0, 2.0, "1")]), ("MM", [(1.5, 2.0, "1")])])
            p = self._run("apply", "--session", str(sdir), "--aup", str(aup),
                          "--dry-run")
            plan = (sdir / "_asset" / "cutplan.md").read_text(encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(plan, PLAN)

    def test_apply_missing_mm_track_fails_with_names(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = self._session(td)
            aup = Path(td) / "ep.aup4"
            make_aup4(aup, [("AI", [(1.0, 2.0, "1")]), ("Label-Mars", [])])
            p = self._run("apply", "--session", str(sdir), "--aup", str(aup))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Label-Mars", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
