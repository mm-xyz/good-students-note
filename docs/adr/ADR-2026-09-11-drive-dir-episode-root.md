# ADR-2026-09-11-drive-dir-episode-root — `--drive` 一律上修到集數資料夾

- 識別碼：ADR-2026-09-11-drive-dir-episode-root
- 日期：2026-09-11
- 狀態：已採納
- 相關：ADR 0015（cutplan 住集數資料夾根）、ADR 0001（cutplan 是人審真相源）

## 脈絡

EP19-0 的錄音放在 `EP19-0_包棟！/包棟介紹/`——同一集底下還有「試錄」，一集多段
（正片／試錄／分段錄）本來就是常態。出片時 `cut.py --drive` 指了**音檔所在的那層**，
於是 cutplan.md 與版本目錄 `v1_*/` 全落在段落子夾裡。

結果是 MM 在 cutplan 線上編輯器（`scripts/cutplan-editor`，Apps Script）的下拉選單裡
**整集選不到**。`Code.gs` 的 `listEpisodes()` 只掃兩種位置：

    <集數>/cutplan.md          layout='direct'
    <集數>/_meta/cutplan.md    layout='meta'

**不遞迴**，多一層就掃不到；而且掃不到不是錯誤，是那一集安靜地不出現在清單裡。

對照組早就在 Drive 上：EP16 底下有 7 個子資料夾、EP18 有 2 個，兩集的 cutplan 一律在
集數資料夾根。慣例是對的，是呼叫端把「集數資料夾」讀成了「音檔資料夾」。

## 決策

`cut.py` 新增 `episode_root(p)`：把路徑往上爬回 `DRIVE_ROOT` 的**直接子資料夾**。
`find_drive_dir()` 的兩條路徑都套用——`--drive` 傳進來的、以及既存 `.drive_dir` 讀回來的
（讀回時一併改寫 memo，修掉已經指錯層的 session），上修時印一行說明而不是靜默修正。

不在 `DRIVE_ROOT` 底下的路徑原樣尊重（測試 tempdir、別的掛載點）。

**為什麼是護欄不是筆記**：cut.py 自己的 docstring 裡就記著 MM 2026-08-11 的原話——
「這種都應該 script 化——mkdir、複製到 Drive、sync cutplan、備份當下的 cutplan，全部由
這支做，**不要靠人記得**」。這次踩的正是同一類：一個要靠呼叫端每次選對的參數。判準是
可以由程式算出正確值的東西，就不該留給人選。

## 後果

- 好：`--drive` 指到集數底下任何深度都會落在對的位置，編輯器永遠掃得到。
- 好：既有指錯層的 session 下次跑 `cut.py` 會自動被修回來，不用人工處理。
- 代價：真的想把 cutplan 放在段落子夾（例如同一集兩段各自出片）就做不到了。目前沒有這種
  需求——EP16／EP18 都是一集一份 cutplan；真的要支援得先改編輯器的 `listEpisodes()`，
  護欄跟著一起放寬，兩邊必須同時改，不能只鬆一邊。

## 測試

`scripts/tests/test_cut.py::TestFindDriveDir` 五個新案例：段落子夾上修、已在集數根不動、
多層深路徑爬到底、既存 memo 指錯層讀回時修正、DRIVE_ROOT 之外原樣尊重。
