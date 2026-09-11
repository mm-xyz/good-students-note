# ADR-2026-09-11-diff-lives-in-version-dir — diff 住版本目錄，根不留重複成品

- 識別碼：ADR-2026-09-11-diff-lives-in-version-dir
- 日期：2026-09-11
- 狀態：已採納
- 相關：ADR 0011（session 目錄分類）、ADR 0012（cut.py 一行出片與版本目錄）、
  ADR 0015（剪輯 SOP 第 8 步 diff 片段）

## 脈絡

MM 剪 EP19-0 時說「session 的資料夾好亂」。實況：

- **成品重複兩份。** `cut.py` 出片時把工作檔 `final_cut_vN.mp3` 複製進
  `vN_<時戳>/`，根那份就留著。EP18 累積 13 個，逐 byte 比對全部都在版本目錄
  裡有一模一樣的快照，共 354MB 純重複。
- **diff 散在根。** `diff_clips.py --out-dir` 預設 `diff_clips`，每輪人審換一個
  名字，EP18 就長出 `diff_clips` / `_v5` / `_v6` / `_v10` / `_v11` 五堆，誰對哪
  一版全靠人記——而且對應關係是錯開的：`diff_clips_v10` 對的成品其實快照在
  `v9_` 目錄裡（工作檔編號與版本目錄編號差一號）。
- **tidy 會造出假版本。** `tidy_session.py` 的版本目錄編號是 `v{len(vdirs):02d}`，
  只數這次要建幾個、完全不看 session 裡已經有的 `vN_` 目錄。EP19-0 的
  `v1_20260911-0816-AI/` 已經存在，tidy 仍提議把根的 `final_cut_v2.mp3` 搬進
  新建的 `v00_...`，版本序列當場分岔成兩套編號。ADR 0011（2026-08-10）早於
  ADR 0012，tidy 沒認得後來才有的版本目錄。

## 決策

### 一、diff 片段住它所屬的那一版

`diff_clips.py` 的落點改由 `resolve_out_dir()` 推導：成品在 `vN_<時戳>/` 裡就寫
`vN_<時戳>/diff/`；成品還在 session 根（工作檔）就退回舊的 `diff_clips/`；明寫
`--out-dir` 永遠贏。

diff 是「這一版 vs 前一版」的產物，跟 mp3、cutplan 快照、render.txt 綁在同一個
資料夾才找得回來，也不必再用檔名編號去猜對應。

**並鏡像到 Drive 上同名的版本目錄**（`mirror_to_drive()`）。`cut.py` 出片當下就
把版本目錄推上 Drive 了，而 diff 是出片之後才跑的——不補這一步，Drive 那份版本
目錄永遠沒有 diff，人在手機上聽不到。沒有 `.drive_dir`、或 Drive 上沒有這一版
（當初 `--no-push`）就安靜跳過：只鏡像既有目錄，不替 Drive 造目錄。

### 二、session 根不留與快照重複的成品

判準是**內容**不是檔名：sha256 與某個版本目錄裡的檔案完全相同才刪。

連帶把 `cut.py` 的 `next_out_name()` 改成同時看根的檔名與版本目錄 `vN_`，兩邊取
最大 +1。只掃檔名的話，根清乾淨後 `default=1` 會讓下一版又叫 `final_cut_v2.mp3`，
檔名先後順序不再等於出片先後順序——正是那支函式 docstring 本來要避免的事。

### 三、tidy 不再造假版本

`plan()` 接既有 `vN_` 的最大號往上編（空 session 從 `v1` 起，不再是 `v00`），
並且**跳過已有快照的根成品**。要不要清掉根那份是另一個決定，tidy「只搬不刪」
的界線不越。

## 後果

- 好：一版的所有東西（mp3／cutplan 快照／render.txt／diff）在同一個資料夾，
  local 與 Drive 同構。
- 好：EP18＋EP19 回收 354MB，之後每出一版不再多留一份重複。
- 代價：diff 片段會跟著版本目錄被推上 Drive，Drive 空間用量增加；真的不想推就
  用 `--out-dir` 指到別處。
- 未處理：EP18 既有的五個 `diff_clips*` 無法可靠回推對應版本（工作檔編號與版本
  目錄編號錯開一號），沒有分派到各版本目錄，集中搬到 `_meta/old_diff/` 保留原樣。
  **刻意不猜**——標錯的紀錄比沒有紀錄更貴（ADR-2026-09-06 同一條教訓）。

## 測試

- `scripts/tests/test_diff_clips.py`（新增，7 項）：落點推導三態、Drive 鏡像、
  無 memo／Drive 缺該版時安靜跳過、重跑清掉上一輪殘留片段。
- `scripts/tests/test_tidy_session.py`（新增，6 項）：分流、接號、已快照不重複歸位。
- `scripts/tests/test_cut.py::TestNextOutNameSurvivesPrunedWorkfiles`（4 項）。
