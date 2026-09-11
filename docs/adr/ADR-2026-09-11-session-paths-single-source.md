# ADR-2026-09-11-session-paths-single-source — 工作檔路徑吃變數，位置改一處就搬家

- 識別碼：ADR-2026-09-11-session-paths-single-source
- 日期：2026-09-11
- 狀態：已採納
- 相關：ADR 0011（session 目錄分類，**本 ADR 推翻其「不做」的結論**）、
  ADR-2026-09-11-diff-lives-in-version-dir

## 脈絡

MM：「我想要每一集的根目錄都是乾淨的」「這種東西是不是應該要有一個
tool/asset/{名字} 的資料夾，就放裡面，也不適合露在眼花撩亂」。

擋路的是 ADR 0011（2026-08-10）——它評估過「把管線工作檔搬進 `_work/`」，結論是
否決，理由是「要改 `scripts/audio/*.py` 與 `session.py` **約 15 處**硬編碼路徑」，
收益只是根目錄乾淨，風險大於收益。

實際盤點：**85 處、23 個檔**。當年的估計低了五倍多。這讓否決的理由更強，不是更弱。

MM 的裁示把問題換了個問法：「把路徑變成變數，全部吃變數，之後改路徑比較方便」。

這是對的工程順序。**搬家**與**解耦**是兩件事，而 ADR 0011 把它們綁在一起評估，
所以只看得到「一次改 85 處還要驗證沒斷」的大風險。拆開之後：解耦這一步位置完全
不動，行為零改變，測試全綠就是正確性證明；搬家變成之後改一個常數的事。

## 決策

新增 `scripts/audio/session_paths.py`，單一真相源：

```python
WORK_SUBDIR = ""          # "" = 留在 session 根（現況）

def work_dir(sdir: Path) -> Path:
    if not WORK_SUBDIR:
        return sdir
    sub = sdir / WORK_SUBDIR
    return sub if sub.is_dir() else sdir    # 未遷移的 session 退回根
```

77 處呼叫點改成 `work_dir(sdir) / "words.json"`。**這一版 `WORK_SUBDIR` 是空字串，
所有檔案位置一個都沒動**——純解耦。

放在 `scripts/audio/` 而不是 `scripts/`：那裡 14 個檔已經有指向同目錄的
`sys.path.insert`，import 零成本；`scripts/` 根的三個檔與 `frames/` 各補一行。

### 沒有改的 6 處，與為什麼

機械替換會誤傷，這幾處刻意留著：

- `AUDIO_DIR / "prosody.py"`、`AUDIO / "copy_prompt_build.py"` — **腳本**路徑，
  不是工作檔，只是檔名前綴撞上白名單。
- `ddir / "cutplan.md"`（`cut.py`）— 那是 **Drive** 集數資料夾那份，人審介面，
  不該跟著 local 工作檔搬家。
- `sdir / "_meta" / "final" / "transcript.srt"` — `_meta/` 底下的產物，不是工作檔。
- `render_cut.py` docstring 裡的一段說明文字。

## 後果

- 好：要把工作檔收進子資料夾，改 `WORK_SUBDIR` 一個值，23 個檔跟著走。
- 好：fallback 讓「設定改了但某個 session 還沒遷移」讀得到舊位置，不會整批壞掉。
- **未完成：真正的搬家還沒做。** `WORK_SUBDIR` 仍是 `""`。要搬必須先寫遷移
  （把既有 session 的工作檔搬進子資料夾）——fallback 只保證讀得到舊位置，
  **不會自動搬**。設了 `WORK_SUBDIR` 卻沒遷移，等於什麼都沒發生（永遠走 fallback），
  這是刻意的安全側，但也意味著「改一個值就搬家」要配上遷移腳本才成立。
- 代價：每次取路徑多一次函式呼叫，可忽略。
- 代價：`session_paths.py` 放在 `scripts/audio/` 而它是跨線概念（文件線、frames 線
  也在吃）。選它是因為 import 成本最低；語義上有點歪。

## 驗證

`WORK_SUBDIR=""` 下全套 `run_all.sh` 全綠（含真音訊 e2e 與 `.venv-doc` 的文件線
三份），代表 77 處替換沒有改變任何行為——這是這次重構唯一有意義的驗收條件。

過程中回歸抓到一個真 bug：補 `sys.path` 時對 `scripts/` 根的三個檔算錯層級
（`parent.parent / "audio"` 指到 repo 根底下不存在的 `audio/`），`test_session_doc_line.py`
五項當場失敗。**這正是「位置不動、只看測試綠不綠」這個做法要抓的東西。**
