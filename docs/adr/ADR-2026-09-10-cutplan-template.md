# ADR-2026-09-10-cutplan-template — `## ⚙ template=<kit>`：節目樣板只供「省略時的預設」

- 識別碼：ADR-2026-09-10-cutplan-template
- 日期：2026-09-10
- 狀態：已採納
- 相關：ADR 0001（cutplan 是人審真相源）、ADR 0011（`## ➕`）、
  ADR-2026-09-06-mixdown-audio-line（`## ⚙` 是參數真相源）、
  ADR-2026-09-10-roomtone-insert（`## 🔇`）

## 脈絡

素材庫位置寫死在 `render_cut.py`（`MATERIAL_DIR = shared-material/水星貓的生活實驗室_v1`），
而 `## 🎵 opening/break/ending` 每一行的參數，一直是 MM **手抄**進每一集的 cutplan。
`shared-material/` 下已有兩套 show-kit（v1、v2），但沒有任何機制可以選。

EP18 二剪把 break 間奏從 `end=8 tail=3`（solo 2s）調成 `end=14 fadeout=2.5 tail=2.5`
（solo 8.5s），並在間奏後加 1 秒室噪留白。這組調校如果只留在 EP18 的 cutplan 裡，
下一集又要靠人記得抄一次——手抄正是這次事故鏈的起點。

## 決策

### 1. `## ⚙ template=<kit>` 決定素材庫

kit 名是 `shared-material/` 底下的目錄名（含 CJK，`params_raw` 正則吃得下，已測）。
render 從它解析素材庫，**蓋過 CLI `--material-dir` 與預設**（cutplan 是參數真相源）。
找不到 kit → FAIL 並**列出可用的 kit 名單**，不默默 fallback。
新增 `--material-root` 讓測試可以指到別處。

**cutplan 沒寫 `template=` → 完全走舊路**，既有 session 行為一個字都不變。

### 2. 樣板是「省略時的預設」，不是產生器硬塞的行

這是本案的核心，別改成別的：

| 寫法 | 結果 |
| :--- | :--- |
| `## 🎵 break` | 參數全取樣板 |
| `## 🎵 break end=20` | **只有 end 用行內值**，其餘仍取樣板（部分覆蓋，不是整組取代） |
| `## 🔇` | 秒數取樣板的 `roomtone.seconds` |
| `## ⚙` 明寫的旋鈕 | 一律贏過樣板；沒寫的才由樣板補 |

**為什麼不做成產生器直接把 🎵 行寫進新 cutplan**：break 要插在哪一段之後是人審的
編輯決定，產生器猜不到。讓樣板只供參數，MM 只要把 `## 🎵 break` 放對位置就好。

優先序規則（哪些鍵該由樣板補）住 `apply_template()` 這個純函式裡，不住呼叫端——
住呼叫端就測不到（第一版就是這樣，mutation 掃描時該刀存活）。

### 3. 樣板檔：`<kit>/template.json`

v1 收的就是 EP18 這次調校後的值：break 間奏參數、`roomtone.seconds = 1.0`、
opening／ending 現行參數、⚙ 四個剪輯旋鈕。v2 先照抄 v1 並用 `_note` 註明尚未調校
（v2 的三個音樂檔目前是同一首複製三份的佔位）。

`cutplan.py` 產生新節目單時，⚙ 那行改成 `## ⚙ template=水星貓的生活實驗室_v1`，
剪輯旋鈕不再寫死在產生器裡——要逐集微調時再在那行補該鍵。

## 後果

- ⚠️ **這是跨 repo 的**：`shared-material/*` 是 symlink，指到獨立的 **branding-kit**
  repo（`aquacatlivinglab/show-kit/v1|v2`）。**參數住 branding-kit、讀它的程式住
  good-students-note**。單獨 clone good-students-note 時，寫了 `template=` 的節目單
  會 FAIL（訊息會說找不到樣板並列出可用 kit）；沒寫的照舊能跑。調參數要改
  branding-kit 並在那邊 commit。
- 換 show-kit 版本＝改一行 `template=`，音樂檔與所有預設參數一起換。
- 樣板改值會影響**之後每一次 render**，包括重跑舊集數。要凍結某一集就在該集的
  cutplan 行內明寫參數（行內永遠贏）。

## 驗證

- 新增 10 個測試（render_cut 84 → 93、cutplan 38 → 40）：CJK kit 名解析、
  裸 🎵 全取樣板、行內部分覆蓋、裸 🔇 取樣板秒數、兩條路都沒有時 FAIL、
  未知 kit 列出可用名單、⚙ 優先序、無 template 的向後相容、產生器帶 template=。
- **Mutation 掃描**：行內優先 ✅ 倒｜🔇 沒秒數默默給值 ✅ 倒｜找不到 kit 回空樣板 ✅ 倒｜
  ⚙ 樣板蓋掉明寫值 —— 第一版**存活**（規則住呼叫端），把規則搬進純函式後 ✅ 倒。
- 真音訊 dry-run（EP18，只 dry-run 不出片）：把 ⚙ 換成 `template=`、`## 🎵 break`
  與兩個 `## 🔇` 都改成不帶參數，算出來的間奏音樂長度 0:14、包絡 keypoints、
  室噪 1.0s ← source 8:44.92–8:45.92 —— 與手寫版**完全一致**。
- `run_all.sh`：0 failed、0 skipped。
