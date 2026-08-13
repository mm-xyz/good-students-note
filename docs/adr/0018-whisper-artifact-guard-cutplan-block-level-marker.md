# ADR 0018 — whisper artifact 守門接進混音線，block 級標記不自動剪

- 識別碼：ADR-2026-08-14-675
- 日期：2026-08-14
- 狀態：已採納
- 相關：ADR 0001（cutplan 是人審真相源）、ADR 0011（session 目錄與外部補錄）、
  PERTRACK_IMPL.md D5（逐軌 ASR 不可當出片文字）、CHANGELOG 2026-08-11
  （分軌線先上守門，混音線待補）

## 脈絡

混音線（`cutplan.py`）的逐字稿是人審 cutplan.md 的信任基礎——人只看得到文
字，看不到底層音訊，改勾選、加刪除線都是照著文字做判斷。whisper 偶發陷入
重複迴圈或亂碼，這段文字會變成假的，但外觀仍是合法的一行 block，人審沒有
線索分辨。

實證：EP16 B0068 `[7:33–7:53] [Sarah]`，20.4 秒被轉成「反而」重複 110 次
（219 字、僅 2 種字元）。分軌線（`pertrack_blocks.py`）2026-08-11 已經踩過
同款問題並上了守門（commit `7c555fc`，`is_artifact()`）：Mars 軌 309–314s
的 words.json 是「嘗」×40 + U+FFFD。分軌線的因應是**丟棄**整個 block（逐
軌 ASR 只是佐證，不是文字真相源，PERTRACK_IMPL.md 已定調「逐軌 ASR 不可當
出片文字」）。混音線不能照搬丟棄——混音的 transcript.speakers.srt **是**
文字真相源，丟棄一個 block 等於憑空抹掉這段音訊在人審視角裡的存在（人審甚
至不會知道這裡曾經有話）。

## 決策

### 1. 判準：`detect_asr_artifact()`（`scripts/audio/cutplan.py`）

延續 `pertrack_blocks.is_artifact()` 的核心三條，加一條混音線需要的：

1. 同一字元連續重複 ≥4 次（`ARTIFACT_RUN_MIN`）
2. 含 U+FFFD（解碼失敗替代字元）
3. 整句只由 ≤2 種字元組成，且長度 ≥6（`ARTIFACT_DEGENERATE_LEN`）——
   「反而反而反而…」逐字交替，判準 1 的連續重複抓不到，靠這條
4. **新增**：任一長度 2–6 的短語（n-gram）連續重複 ≥4 次
   （`ARTIFACT_PHRASE_LENS` / `ARTIFACT_PHRASE_REPEAT_MIN`）——混音線的
   block 可能前段正常對話、只有後段跑進迴圈（分軌線的 block 粒度細到
   「每秒一個」，較少出現這種混合型態，混音線 block 可以到 45 秒）

四條都是離散可數的字串判準，零音訊分析、零第三方套件，跟現有 `is_artifact()`
同量級。門檻寫成模組常數，之後要調鬆緊直接改常數，不必碰函式邏輯。

### 2. 輸出：block 級標記，不自動剪、不丟棄

- `cutplan.json` 每個 block 補兩個結構化欄位：`asr_artifact`(bool)、
  `asr_artifact_reason`(str)。
- `cutplan.md` 沿用既有的 `reason` 欄位（本來是給人/agent 寫剪輯理由的）：
  `reason` 為空時自動補 `⚠ASR-artifact：<原因>`，render 到 md 是
  ` ← ⚠ASR-artifact：...`。**不新增語法、不改 `render_cut.py` 的
  `LINE_RE`/`rsplit(" ← ", 1)`**——標記天生走既有的「理由顯示在行尾」通道，
  人審一眼看到，render 照舊把它跟文字一起 strip 掉。
- `keep` 維持 `build_blocks()` 的預設 `True`，不因為標記而改動。**標記是
  「這段文字不可信」，不是「這段音訊要剪掉」**——那是人審的判斷，不是偵測
  器的判斷（工程原則：偵測與決策分離）。

### 3. 同根因掃描結果

| 文字產出點 | 現況 | 本卡動作 |
| :--- | :--- | :--- |
| `cutplan.py`（混音線正片） | 無守門（EP16 B0068 曝險） | 新增 `detect_asr_artifact`/`flag_artifacts`，接進 `prepare()` |
| `pertrack_blocks.py`（分軌線） | 已有 `is_artifact()`（2026-08-11，丟棄型） | 不動——分軌線的丟棄策略是刻意的架構決策（逐軌 ASR 非真相源），跟本卡「混音線要標記不丟棄」的前提不同，沒有理由統一 |
| `insert_prepare.py`（補錄，ADR 0011） | 無守門 | 補錄跟正片一樣是 whisper 轉出來的文字、一樣會進 cutplan.md 給人審，同根因；`build_blocks()`/`md_lines()` 接上同一份 `detect_asr_artifact()`，不重造判準 |

未掃：`transcribe_local.py`、`resegment_srt.py` 只是產生/重切 SRT，不組
block、不是人審看到的最終文字面（下游 `cutplan.py`/`insert_prepare.py` 已
覆蓋）；`diarize.py`、`pertrack_attrib.py`、`pertrack_cells.py` 做講者歸
屬與跨軌去重，不產生新文字。

## EP16 全集實跑（真實 `transcript.speakers.srt`，948 blocks）

| block | 時間 | 講者 | 觸發原因 |
| :--- | :--- | :--- | :--- |
| B0068 | 7:33–7:53（20.4s） | Sarah | 整句僅由 2 種字元組成（219 字）——已知案例，本卡動機 |
| B0400 | 18:57–18:59（1.9s） | KIN | 同字元連續重複 5 次（「哦對對對對對」） |
| B0667 | 25:27–25:28（0.8s） | KIN | 同字元連續重複 5 次（「懂懂懂懂懂」） |

3/948（0.3%）被標記，遠低於「超過 20 條＝門檻太鬆」的收緊警戒線，維持預設
常數不調整。B0400/B0667 跟 B0068 性質不同——時長／字數比值落在正常語速範
圍內，很可能是真實的口語附和重複（中文常見「對對對對對」「懂懂懂懂懂」），
不是 whisper 迴圈；已標記但保守起見列入 MM 聽感抽驗清單，而非直接視為誤判
撤掉判準 3（同字元連續重複）——那條判準同時是 `is_artifact()` 抓「嘗」×40
的主力，拿掉會讓分軌線同款案例在混音線失守。

## Rollback

純 code：`git revert` 本卡三個 commit 即可，無資料遷移、無 schema 破壞性
變更（新增欄位，未刪改既有欄位）。
