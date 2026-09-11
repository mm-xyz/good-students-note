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

# 工作檔住哪。"" = 留在 session 根;給個名字就整批搬進那個子資料夾。
# 2026-09-11 MM:「不適合露在眼花撩亂」——根目錄只留人要看的東西。
WORK_SUBDIR = "_asset"


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
    """建立工作檔資料夾。**只有 session 建立與遷移可以呼叫。**

    ⚠️ 管線執行途中絕對不要呼叫:`work_dir()` 的 fallback 看的是「子資料夾存不
    存在」,中途把它建出來會讓同一次執行的前後半段指向不同地方。2026-09-11 實踩
    ——precut 的 transcribe 在 `_asset/` 還不存在時把 transcript.srt 寫進根,
    接著 diarize 的 ensure_wav 建了 `_asset/`,下一行 pick_transcript 就改去
    `_asset/` 找,當場 FileNotFoundError。管線內一律用 `work_dir()`。
    """
    if not WORK_SUBDIR:
        return sdir
    sub = sdir / WORK_SUBDIR
    sub.mkdir(parents=True, exist_ok=True)
    return sub


# 人看的伴隨檔(highlights / chapters / 文案 / 封面)住哪。ADR 0011 的 `_meta/`。
META_SUBDIR = "_meta"


def meta_dir(sdir: Path) -> Path:
    """讀用:人看的伴隨檔在哪。沒有 _meta/ 就退回根(舊 session)。"""
    sub = sdir / META_SUBDIR
    return sub if sub.is_dir() else sdir


def ensure_meta_dir(sdir: Path) -> Path:
    """寫用:直接產在 _meta/,不要先丟根再等 tidy 搬。

    產在根再靠 tidy 歸位的話,每跑一次 prosody/render 根目錄就髒一次——
    「根目錄乾淨」不能是一個要定期執行的動作。
    """
    sub = sdir / META_SUBDIR
    sub.mkdir(parents=True, exist_ok=True)
    return sub
