#!/usr/bin/env python3
"""
scripts/session_paths.py — session 內的檔案位置:單一真相源

管線工作檔(words.json / prosody.json / transcript*.srt / cutplan.* / audio16k.wav
…)原本由 23 個檔各自寫死 `sdir / "words.json"`,共 85 處。要換位置就得全部改一
遍——ADR 0011(2026-08-10)據此否決過「把工作檔搬進子資料夾」,當時估「約 15 處」,
實際是五倍多。

2026-09-11 MM:「把路徑變成變數,全部吃變數,之後改路徑比較方便」。

用法(每支腳本在入口算一次,之後都用它):

    from session_paths import work_dir
    w = work_dir(sdir)
    words = w / "words.json"

要把工作檔收進子資料夾,**只改 WORK_SUBDIR 一個值**,23 個檔都跟著走。
改之前記得先寫遷移(把既有 session 的工作檔搬進去)——fallback 只保證讀得到
舊位置,不會自動搬。
"""

from pathlib import Path

# "" = 工作檔留在 session 根(現況)。改成 "_work" 之類就整批搬家。
WORK_SUBDIR = ""


def work_dir(sdir: Path) -> Path:
    """這個 session 的管線工作檔住哪。

    fallback:設了 WORK_SUBDIR 但某個 session 還沒遷移(子資料夾不存在)時退回
    session 根,舊 session 不會因為改設定就整批讀不到檔。
    """
    if not WORK_SUBDIR:
        return sdir
    sub = sdir / WORK_SUBDIR
    return sub if sub.is_dir() else sdir


def ensure_work_dir(sdir: Path) -> Path:
    """寫入前用:需要子資料夾就建出來,回傳該寫進哪。"""
    if not WORK_SUBDIR:
        return sdir
    sub = sdir / WORK_SUBDIR
    sub.mkdir(parents=True, exist_ok=True)
    return sub
