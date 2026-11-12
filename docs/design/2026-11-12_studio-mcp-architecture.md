# studio MCP — 架構與分期計畫（卡 #1267，併 #1175＋ai-reels）

> 2026-11-12 · 狀態：**提案，等 MM 拍板 §11 剩下的五件事**（第 1、6 項已定）。本檔只有架構與規劃，沒有實作。
> 範圍（MM 2026-11-12）：#1267 ＋ #1175 ＋ ai-reels 的 HyperFrames 短影音線，整個重構。
> 分工：Fable 做架構與規劃；實作由 worker 照 §8 工單做。
> 2026-11-12 追加（MM）：skill 整組重新設計，跟 MCP 一起翻修，見 §13。
> 2026-11-12 拍板（MM）：studio MCP 有自己的 repo；不需要給外部使用者。見 §4.2、§11。

## 0. 一頁結論

1. **三條產線，一個入口**：知識線（good-student）、剪輯線（podcast-cut）、短影音線（ai-reels），共用 session 容器。
2. **CLI 是合約，MCP 是轉接頭**：每個能力先有一支吐 JSON 的命令列，MCP 工具只負責轉呼叫。沒有 MCP 的執行器（agy、cron）用同一支命令列。
3. **自己的 repo，但不搬管線**：新 repo `studio-mcp` 放轉接頭、命令列入口、4 顆 skill、測試。管線程式留在 good-students-note 與 ai-reels，只加 `--json`。
4. **MCP server 不匯入任何管線程式**：一律開子行程，用該產線自己的直譯器跑。
5. **長工作走 job**：轉錄、初剪、出片先回 job id，再查狀態。
6. **會傷到 MM 人審的寫入預設關閉**：推回 Drive 要明示；兩份 cutplan 不同時，要明示用哪一份。
7. **skill 從 8 顆收成 4 顆**（§13）：skill 只寫何時用、順序、判斷點；路徑與參數全部交給工具。每條產線的 skill 跟該線的工具同一期做。
8. 分 6 期。P1 骨架做完就能在 Claude Code 裡找 session；P2 做完剪輯線可用。

## 1. 名詞

| 名詞 | 意思 |
| :--- | :--- |
| studio MCP | 本檔要做的 MCP server，註冊名 `studio` |
| Web 工作室 | 既有的 `web/studio.html`（CLAUDE.md 的 B 軌）。**跟本檔無關**，不納入 |
| 產線（line） | knowledge／podcast／reels 三者之一 |
| 轉接頭 | MCP 工具本體：收參數、開子行程、回 JSON，不含業務邏輯 |

程式目錄取名 `studio/`，文件裡提到 Web 工作室一律寫全名，避免混淆。

## 2. 現況（2026-11-12 實查）

| 項目 | 數字 |
| :--- | :--- |
| good-students-note `scripts/` 的 py／sh／js | 132 檔，約 5 萬行（含測試 36 支） |
| 最大單檔 | `scripts/audio/render_cut.py` 2,650 行 |
| skill 自帶腳本 | 約 25 支，`doc_to_md.py` 有 3 份副本 |
| ai-reels 自有程式 | `scripts/reel-build.mjs` ＋ `lib/` 6 檔，共 770 行；`hyperframes/` 是上游 clone，不進版控 |
| sessions | 124 個，6.6 GB，不進版控 |
| fork 與上游 | 領先 295 commit、落後 0；上游最後更新 2026-07-17。**重構不必顧慮上游合併** |
| 直譯器 | `.venv-audio`（Python 3.13：pyannote、mlx-whisper、librosa、numpy）、`.venv-doc`（fitz、ebooklib）、node 22、系統 python3 |
| 既有 MCP 先例 | accounting-ledger 的 beanquery：venv 裡的 Python `mcp` 1.28.1，stdio |

介面上的四個問題，是這次要解的：

1. **入口分散**：`session.py`、`precut.py`、`cut.py`、`reel-build.mjs` 各有各的參數，路徑要先找。
2. **輸出是給人讀的文字**：agent 靠讀 `[render]` 開頭的行猜結果。
3. **`cut.py` 會停下來問人**（`input()`，1 處）。加 `--yes` 則依修改時間自動選較新的 cutplan。
4. **路徑寫死**：Drive 根目錄、素材根目錄、`sys.path.insert`、腳本互叫的絕對位置。

## 3. 不可違反的既有決策

實作時每一條都要有測試或檢查對應。

| 來源 | 規則 | 對 studio MCP 的意義 |
| :--- | :--- | :--- |
| CLAUDE.md 原則 5 | 誰開的就由誰執行，腳本不打付費 API | 工具遇到需要 LLM 的階段，回傳待辦 marker，由呼叫的 agent 處理 |
| CLAUDE.md 原則 6 | 確定性工作用工具，LLM 只做判斷 | marker 的驗字數、刪 marker、改 metadata 做成工具 |
| ADR 0001 | render 真相源是 session 的 cutplan；人審介面是 Drive 副本 | 兩份不同時不得自動挑 |
| ADR-2026-09-11-session-paths-single-source | 管線執行中不得建立 `_asset/` | **job 紀錄不放 session 裡**（見 §4.4） |
| 記憶：審稿中勿寫 Drive cutplan | 寫入 Drive 會讓 MM 開著的編輯器整份存檔被拒 | 推回 Drive 預設關閉 |
| podcast-cut skill | AI 介入剪輯決策必加 `--ai` | `ai` 是必填參數，沒有預設值 |
| 卡 #1267 邊界 | `finalize --apply` 不開放 | 只開 dry-run |
| run_all.sh | 不退回系統 python | 轉接頭找不到 venv 就報錯，不換直譯器 |

## 4. 目標架構

### 4.1 分層

```
【studio-mcp repo】
Claude Code ──stdio──▶ studio/mcp_server.py      轉接頭（.venv-studio，只裝 mcp）
agy / cron / MM ─────▶ bin/studio <line> <verb> --json     同一個入口
                              │
                              ▼
                       studio/core/              stdlib only
                       config · runners · jobs · result · sessions
                              │ 開子行程（路徑來自設定檔）
【good-students-note repo】                    【ai-reels repo】
        ┌─────────────────────┼──────────────────────┐
        ▼                     ▼                      ▼
  .venv-audio            .venv-doc / python3        node
  scripts/audio/*        scripts/session.py …       ai-reels/scripts/reel-build.mjs
```

規則：

1. `studio/` 只用標準函式庫。`mcp` 套件只有 `mcp_server.py` 匯入。
2. `studio/` 不匯入管線 repo 的任何模組，只用設定檔裡的路徑開子行程。原因：`cut.py` 匯入 `render_cut`，後者需要 numpy；一匯入，server 就綁死在 `.venv-audio`，而且 pyannote 與 torch 會留在長駐行程裡。
3. 每支被包的腳本加 `--json`：stdout 最後一行是一個 JSON 物件。人讀的訊息照舊印，行為不變。
4. 轉接頭不解析人讀的文字。腳本沒有 `--json` 就不包，先補腳本。

### 4.2 目錄（三個 repo 各放什麼）

```
studio-mcp/                     新 repo，私有（Forgejo），不對外發布
├── bin/studio                  命令列入口（shell，轉呼叫 python3 -m studio）
├── studio/
│   ├── __main__.py             studio <line> <verb> [參數] --json
│   ├── mcp_server.py           FastMCP；一個工具對一個 verb
│   ├── core/
│   │   ├── config.py           讀 studio.toml ＋ studio.local.toml
│   │   ├── runners.py          line → 哪個 repo 的哪個直譯器
│   │   ├── jobs.py             start／status／cancel／list，單一重工作鎖
│   │   ├── result.py           回傳信封與錯誤碼
│   │   └── sessions.py         find／status（只讀檔案系統）
│   └── lines/                  knowledge.py／podcast.py／reels.py：參數 → 命令列
├── skills/                     to-md／good-student／podcast-cut／reel-cut（§13）
├── tests/                      unittest：合約、失敗注入、MCP 冒煙
├── docs/                       本檔與 ADR 在 P1 搬過來
├── studio.toml                 進版控的預設值（門檻、逾時）
├── studio.local.toml           不進版控：兩個管線 repo 的位置、Drive 根、素材根
└── _local/jobs/                不進版控：job 紀錄

good-students-note/             管線留在原地
└── scripts/                    不搬。逐支加 --json、改讀設定；新增 markers.py、input_probe.py、audio/loudness.py
                                .claude/skills/ 的舊 skill 在各期下架

ai-reels/
└── scripts/reel-build.mjs      加 --json
```

跨 repo 的合約：每支被包的命令列在 JSON 裡帶 `schema` 版號。`doctor` 會核對 studio-mcp 期待的版號與兩個管線 repo 實際吐出的版號，不合就回 `ENV_MISSING` 並寫明是哪一支、差在哪一版。改管線的輸出欄位時，同一張卡要同時改兩邊。

### 4.3 回傳信封

所有命令列與工具回同一個形狀：

```json
{"ok": true,  "data": {}, "warnings": [], "next": "建議的下一步，一句話"}
{"ok": false, "error": {"code": "CUTPLAN_DIVERGED", "message": "…", "hint": "…"}}
```

錯誤碼（固定清單，測試逐一覆蓋）：

| 碼 | 何時 |
| :--- | :--- |
| `NOT_FOUND`／`AMBIGUOUS` | 找不到 session；一個查詢對到多個（列出候選，不自動挑） |
| `ENV_MISSING` | 缺 venv、ffmpeg、node、hyperframes |
| `DRIVE_TCC` | Drive 路徑回 Operation not permitted。提示改用 orca terminal 開 Claude |
| `CUTPLAN_DIVERGED` | Drive 與 session 的 cutplan 不同，呼叫時沒指定用哪一份 |
| `REVIEW_PENDING` | `.cutplan_pending.json` 還在，人審或提案沒結束 |
| `JOB_BUSY` | 已有重工作在跑 |
| `GATE_FAILED` | dry-run、出版閘、摘要講者檢查沒過 |

### 4.4 Job

| 項目 | 設計 |
| :--- | :--- |
| 何時用 | 預期超過 20 秒：轉錄、diarize、初剪、出片、reels render |
| 啟動 | 工具立刻回 `job_id`；子行程脫離 server（server 重啟不殺工作） |
| 存放 | studio-mcp 的 `_local/jobs/<job_id>/`（`job.json`、`stdout.log`、`result.json`），不進版控 |
| 為何不放 session | 在未遷移的 session 建 `_asset/` 會讓 `work_dir()` 中途換位置（2026-09-11 實踩） |
| 查詢 | `job_status(job_id, wait_s)`：最多等 `wait_s` 秒，有結果就早回。回狀態、log 尾 20 行、結果 |
| 併發 | 重工作全域一次一個（`_local/heavy.lock`）。第二個回 `JOB_BUSY`，附正在跑的 job |
| 判活 | 看 pid 是否存活。判不出來就當還在跑，不重啟（記憶：判活偏向還在跑） |

### 4.5 設定

`studio.toml`（進版控）放不含個資的預設值；`studio.local.toml`（不進版控）放機器相關路徑。llm-node 位址照既有規定讀 `mars-cc/.env` 的 `LLM_NODE_URL`、`LLM_NODE_MODEL`，不複製。

第一批只改兩處寫死的路徑：`cut.py` 的 `DRIVE_ROOT`、`render_cut.py` 的 `MATERIAL_ROOT`。改成「有設定讀設定，沒有設定用原值」，舊用法不壞。其餘 20 多個檔留到 P6。

### 4.6 註冊

照 beanquery 先例，user scope，任何專案都叫得到：

```
claude mcp add --scope user studio -- <studio-mcp>/.venv-studio/bin/python -m studio.mcp_server
```

`/mcp-add` skill 只處理 npm 套件，這裡不適用。

## 5. 工具清單（18 個）

「寫入」欄：無＝只讀；本地＝只寫 session 或工作目錄；Drive＝會寫 Google Drive。

### 共用（6）

| 工具 | 做什麼 | 型態 | 寫入 |
| :--- | :--- | :--- | :--- |
| `doctor` | 檢查三個直譯器、ffmpeg、node、hyperframes、Drive 可讀、llm-node 可連 | 同步 | 無 |
| `session_find(query, line?)` | 用 EP 編號、slug 片段、日期找 session。回路徑、產線、Drive 目錄、最新版本 | 同步 | 無 |
| `session_status(session)` | 各階段產物在不在、待辦 marker、版本清單、cutplan 是否漂移、建議下一步 | 同步 | 無 |
| `job_status(job_id, wait_s)` | 見 §4.4 | 同步 | 無 |
| `job_list()` | 進行中與最近 10 個 | 同步 | 無 |
| `job_cancel(job_id)` | 結束該工作的行程群組 | 同步 | 本地 |

### 知識線（4）

| 工具 | 包哪支 | 型態 | 寫入 |
| :--- | :--- | :--- | :--- |
| `input_probe(path)` | 新增 `scripts/input_probe.py`：類型、長度或頁數、圖片數（good-student 的 Step 0 圖片閘門） | 同步 | 無 |
| `ingest(input, slug, context, options)` | `session.py new`。options：`asr`、`diarize`、`frames`、`vlm`、`images`、`stop_at` | job | 本地 |
| `marker_list(session)` | 新增 `scripts/markers.py list`：待辦 marker 與順序 | 同步 | 無 |
| `marker_done(session, stage)` | `markers.py done`：驗字數區間、刪 marker、更新 `metadata.json` | 同步 | 本地 |

`ingest` 一律以 marker 模式執行（轉接頭明帶 engine，不靠環境變數偵測）。出版（`publish_goodedunote.sh`，會部署 Firebase）不做成工具。

### 剪輯線（6）

| 工具 | 包哪支 | 型態 | 寫入 |
| :--- | :--- | :--- | :--- |
| `precut(session, line, num_speakers)` | `precut.py` | job | 本地 |
| `cut_check(session, plan?)` | `cut.py --check --json`：cutplan 兩份的語意差異＋dry-run 摘要 | 同步 | 無 |
| `cut_render(session, ai, use?, push=false, plan?)` | `cut.py --yes --json`。`ai` 必填。兩份不同時 `use` 必填（`drive`／`session`） | job | 本地；`push=true` 才寫 Drive |
| `loudness_report(session, version?)` | 新增 `scripts/audio/loudness.py`：人聲平均 LUFS、三個間奏窗 LUFS、是否在 -17±2 | 同步 | 無 |
| `diff_clips(session, old?)` | `diff_clips.py` | 同步 | 本地 |
| `audacity(session, action, aup?)` | `audacity_labels.py`。action：`export`／`apply_preview`／`apply`／`inject` | 同步 | 本地（`inject` 改 .aup4） |

`finalize` 與 `tidy_session` 只提供預覽，併進 `session_status` 的輸出（「定稿後可歸檔 N 個檔」），不另開工具。要真的搬，MM 自己跑命令列。

### 短影音線（2）

| 工具 | 包哪支 | 型態 | 寫入 |
| :--- | :--- | :--- | :--- |
| `reel_prepare(session, start+end 或 highlight, out)` | `reel-build.mjs prepare --json`。回工作目錄與 `transcript-excerpt.md` 路徑 | 同步 | 本地 |
| `reel_render(out, quality)` | `reel-build.mjs render --json`。先驗 `summary.json` 的講者都在設定裡 | job | 本地 |

兩步中間寫 `summary.json` 是 agent 的判斷工作，照 ai-reels ADR 0002 保留，不自動化。

## 6. 安全閘

| 風險 | 閘 |
| :--- | :--- |
| 推回 Drive 撞上 MM 開著的編輯器 | `push` 預設 false。`push=true` 時工具說明寫明：先問 MM 編輯器是否已關。雜湊相同不算確認 |
| 自動選錯 cutplan | 有 `--json` 時，兩份不同且沒帶 `--use` 就回 `CUTPLAN_DIVERGED` 並附語意差異。不再依修改時間挑 |
| 跳過人審 | `.cutplan_pending.json` 存在時 `cut_render` 回 `REVIEW_PENDING` |
| AI 版本沒標記 | `ai` 沒有預設值，漏填即參數錯誤 |
| `--plan` 不是 cutplan.md 時蓋掉 Drive 合軌節目單（卡 #1081） | P2 一併修：推回的目標檔名跟著 `--plan` 走 |
| 歸檔誤搬 | `finalize --apply`、`tidy --apply` 不開放 |
| 打到付費 API | 轉接頭不帶任何 API key 類環境變數給子行程；`scripts/lang/` 整個不包 |
| Audacity 開著時改專案檔 | `inject` 沿用腳本既有的 `lsof` 檢查，失敗回 `GATE_FAILED` |

## 7. 腳本歸屬（家族層級）

逐檔盤點表是 P0 的產出；這裡先定家族層級的去留。

| 家族 | 檔數 | 進 MCP | 理由 |
| :--- | :--- | :--- | :--- |
| `scripts/audio/` 剪輯核心（cut、render_cut、cutplan、precut、pertrack_*、tracks_columns、ingest_tracks、prosody、diarize、pause_rows、change_preview、diff_clips、audacity_labels） | 約 17 | 是，P2 | 剪輯線主體 |
| `scripts/audio/` 轉錄與標點（transcribe_*、resegment_srt、restore_punct*、check_punct_density、fillers_local） | 約 8 | 間接 | 由 `ingest`／`precut` 帶動，不單獨開工具 |
| `scripts/audio/` 歸檔（finalize、tidy_session、migrate_marks） | 3 | 只預覽 | 破壞性 |
| `scripts/audio/` 文案（copy_prompt_build、finalize_copy） | 2 | 暫不 | 跟卡 #680 一起做 |
| `scripts/session.py` 與閘（qaqc_phase_b、normalize_punctuation、prepublish_gate、pipeline_logger） | 約 6 | 是，P4 | 知識線主體 |
| 圖片線（describe_images、insert_images、placement_*、propose_anchors…） | 約 10 | 間接 | 由 `ingest` 的 `images` 選項帶動 |
| `scripts/doc/`、`scripts/frames/` | 10 | 間接 | 由 `ingest` 依副檔名分流 |
| 出版（publish_goodedunote.sh、publish_qaqc、md_to_html、compress_images） | 4 | 否 | 對外發布，MM 手動 |
| `scripts/lang/`（義、日、英批次） | 17 | 否 | 上游遺留，走付費 API |
| `build_genai2026_day*.py` | 2 | 否 | 一次性 |
| `scripts/cutplan-editor/` | 13 | 否 | MM 的人審介面；它的 node 測試接進總測試 |
| skill 自帶腳本（doc-vlm-to-md 17 支等） | 約 25 | P4 評估 | 先盤點重複，再決定併入 `ingest` 或維持 skill 內呼叫 |
| `web/` | — | 否 | Web 工作室，另一條軌 |
| ai-reels `scripts/` | 7 | 是，P5 | 短影音線主體 |

## 8. 分期

每一期都是：worker 實作（TDD，先寫失敗的測試）→ 另一個 agent 對抗性驗收 → 併回 main。一期一張卡，掛在 #1267 底下。時間是預估，以一個 worker session 為單位。

| 期 | 內容 | 執行體 | 驗收重點 | 預估 |
| :--- | :--- | :--- | :--- | :--- |
| P0 | 逐檔盤點表（每支腳本：家族、誰呼叫它、有無測試、進不進 MCP）；重複腳本清單 | in-process Explore | 表列檔數＝`find` 實數 | 30 分鐘 |
| P1 | 建 `studio-mcp` repo（本地＋Forgejo 私有）；設計文件搬過去；`studio/` 骨架：config、runners、jobs、result、sessions；工具 `doctor`、`session_find`、`session_status`、`job_*`；`.venv-studio`；註冊 | Claude sonnet | 用 stdio 起 server，列得出 6 個工具；`session_find("EP22")` 回兩個 session 且不自動挑；殺掉 server 後 job 還活著 | 2–3 小時 |
| P2 | 剪輯線 6 個工具。`cut.py` 加 `--json`、`--use`；修 #1081；新增 `loudness.py`；**重寫 podcast-cut skill** | Claude sonnet | EP22 的 `cut_check` 摘要行與 `cut.py --check` 逐行相同；§6 每一個閘各有一個失敗案例測試；skill 過 §13.5 三項檢查 | 4–5 小時 |
| P3 | server 的共用說明（`instructions` 欄位）定稿：job 怎麼等、錯誤碼怎麼處置、三條產線怎麼選 | Claude sonnet | 新 session 不載任何 skill，只靠共用說明能完成「找 EP22 → 看狀態」 | 30 分鐘 |
| P4a | 兩條文件管線的取捨（§13.4），寫成 ADR | Claude opus | 差異表每一列有實跑證據；結論是「留哪一條、另一條的獨有能力怎麼搬」 | 1–2 小時 |
| P4b | 知識線 4 個工具。`session.py new` 加 `--json`；新增 `markers.py`、`input_probe.py`；**新寫 to-md skill、重寫 good-student skill**，舊 5 顆下架 | Claude sonnet | 用一個 30 秒音檔與一個 3 頁 PDF 跑完 `ingest`；marker 字數不足時 `marker_done` 拒絕；skill 過 §13.5 三項檢查 | 5–6 小時 |
| P5 | 短影音線 2 個工具。`reel-build.mjs` 加 `--json`；**新寫 reel-cut skill** | Claude sonnet | EP15 `runs/ep15` 重跑，產物與既有的 `config.used.json`、`captions.json` 相同；skill 過 §13.5 三項檢查 | 2–3 小時 |
| P6 | 清理：其餘寫死路徑改讀設定；`doc_to_md.py` 三份併一份；`render_cut.py` 拆檔另開卡評估 | Claude sonnet，機械部分 haiku | 全部測試通過；`grep` 寫死的家目錄路徑歸零（`scripts/lang/` 除外） | 2–3 小時 |

依賴：P1 → P2 → P3；P1 → P4a → P4b；P1 → P5。P2、P4、P5 互不相依，但 Pro 方案同時只撐 2 條線，所以一次派一期實作＋一期驗收。P6 最後。

驗收一律由另一個 agent 做（agy，複雜判斷用 `claude-opus-5-5-high` 或 `gemini-4-argon-high`），工單要求注入失敗並對發現編號。

## 9. 測試策略

1. **合約測試**：每個 verb 的 `--json` 輸出欄位固定；轉接頭的工具清單與參數有快照。
2. **等價測試**：新入口與舊命令列在同一個 fixture session 上結果相同（P2 用既有的真音訊 fixture）。
3. **失敗注入**：§4.3 每個錯誤碼至少一個案例。Drive 不可讀用一個權限為 000 的假目錄模擬。
4. **不靠重跑比對**：本地 whisper 不是確定性的，轉錄相關只驗結構，不驗文字。
5. **測試分兩邊**：管線腳本新增的 `--json` 與新腳本，測試放 good-students-note 的 `scripts/tests/`，照舊由 `run_all.sh` 跑。轉接頭的測試放 studio-mcp 的 `tests/`，用 unittest，只需要標準函式庫。

## 10. 風險

| 風險 | 處置 |
| :--- | :--- |
| ❗ [Major] good-students-note 主樹現有 3 個未提交的改動（`good-student/SKILL.md`、`scripts/frames/extract.py`、`scripts/audio/restore_punct_txt.py`），不是本案的 | worker 一律在 worktree 做；P4 動 good-student skill 前先確認那份改動已提交 |
| ❗ [Major] 卡 #564（review:modifying）與 #1083 都會動 `cut.py` | P2 開工前先看這兩張卡的狀態；有人在改就排在後面 |
| MCP server 由 tmux 底下的 Claude 啟動時讀不到 Drive | `doctor` 與 `DRIVE_TCC` 明確回報；處置不變：用 orca terminal 開 |
| ❗ [Major] 三個 repo 的版本會漂：管線改了輸出，轉接頭沒跟上 | `schema` 版號＋`doctor` 核對（§4.2）；合約測試在 studio-mcp 這邊對真的管線 repo 跑 |
| 工具數量吃 context | 18 個為上限。要加新工具先看能不能併進既有工具的參數 |
| `--json` 加進 2,650 行的 `render_cut.py` 牽動大 | 不動 `render_cut.py` 的輸出。由 `cut.py` 收集它既有的 `[render]` 行，組進自己的 JSON |

## 11. 請 MM 拍板

每一項都有預設。回「好」就照預設走。第 1、6 項已定，剩 2、3、4、5、7。

| # | 問題 | 預設 | 另一個選項 |
| :--- | :--- | :--- | :--- |
| 1 | studio MCP 放哪個 repo | ✔ **已定（MM 2026-11-12）：自己的 repo `studio-mcp`**。管線不搬，見 §4.2 | — |
| 2 | 管線程式要不要也搬進新 repo | **不搬**。good-students-note 與 ai-reels 維持原 repo，新 repo 用設定檔指路徑。sessions 6.6 GB、36 支測試、40 多份 ADR 都綁在原地 | 全部搬進 `studio-mcp`，一個 repo 管到底。要多一期搬遷，所有既有路徑與 ADR 引用要改 |
| 3 | 既有命令列 | **保留**，加 `--json`。MM 手動跑的習慣與 ADR 裡的指令都不變 | 邏輯搬進 `studio/`，舊命令列變空殼。風險高，收益是目錄好看 |
| 4 | `cut_render` 推回 Drive | **預設不推**，要明示 `push=true` | 照 `cut.py` 現行預設會推 |
| 5 | 卡 #1175 | **標為被 #1267 取代並 discard** | 留著當 P2 的卡 |
| 6 | 給外部使用者的版本 | ✔ **已定（MM 2026-11-12）：不需要**。Windows 安裝包、Claude Desktop 沙箱用法、兩個 installer 目錄直接刪除（git 歷史留得到），不另外凍結 | — |
| 7 | astro-chunking skill | **封存**。它是 good-student 的前身，已設成不自動觸發 | 留著 |

拍板後接著做的事：寫 ADR（`ADR-<拍板日>-1267.md`）、開 P0–P6 八張卡、派 P0 與 P1。

## 12. 不做

- 不搬 `scripts/` 的檔案，不改既有命令列的預設行為。
- 不包 Web 工作室、cutplan 編輯器、出版部署、`scripts/lang/`。
- 不開放 `finalize --apply`、`tidy --apply`。
- 不自動寫 `summary.json`、不自動翻 cutplan 勾選。這些是判斷，留給 agent 與 MM。
- 不在這一案拆 `render_cut.py`。

## 13. Skill 整體翻修（2026-11-12 MM 追加）

### 13.1 現況

| skill | 行數 | 問題 |
| :--- | :--- | :--- |
| good-student | 649 | 前 300 行是轉檔操作細節（ASR、diarize、llm-node 指令），後半才是切卡的判斷與格式 |
| doc-vlm-to-md | 401 | 自帶 17 支腳本，跟 `scripts/doc/` 是兩條平行的文件管線 |
| good-student-notes | 201 | 上游留下的 `session.py` 包裝，跟 `ingest` 重疊 |
| audio-to-md | 198 | 一半篇幅是 Windows 與 Claude Desktop 的安裝說明（不對外發布後整段可刪） |
| doc-to-md | 123 | 同上；`doc_to_md.py` 有 3 份副本 |
| video-to-md | 98 | 內文還寫 LM Studio（已退役） |
| podcast-cut | 98 | 指令與路徑佔大半 |
| astro-chunking | 122 | good-student 的前身，已設成不自動觸發 |
| 短影音線 | 0 | 沒有 skill，流程寫在 ai-reels 的 README |

合計 1,890 行。同一件事（怎麼轉錄、圖片閘門、session 長什麼樣）在多顆 skill 各寫一次。

### 13.2 分工原則

一句話：**工具管「怎麼做」，skill 管「何時做、先後順序、怎麼判斷」。**

| 內容 | 放哪裡 |
| :--- | :--- |
| 路徑、參數、指令、安裝步驟 | 工具本身。skill 不寫 |
| 單一工具的用途、參數、回傳 | 該工具的說明欄 |
| 跨工具的共用規矩（job 怎麼等、錯誤碼處置、Drive 讀不到怎麼辦） | MCP server 的 `instructions` 欄位。連上 server 就自動載入，不佔 skill |
| 何時觸發、步驟順序、人審關卡、品質標準、輸出格式 | skill 本文 |
| 只有某種輸入才用到的長規則（看圖填空鐵律、卡片格式全文） | skill 的 `references/`，用到才讀 |
| 程式擋得住的坑（章節數對不上、marker 沒清、講者沒定義） | 改成工具的閘與錯誤碼，從 skill 刪掉 |
| 要靠判斷的坑（MM 說有怪聲先去量、提案只能翻勾選） | 留在 skill |

檢查方式：skill 本文出現 `scripts/` 路徑或命令列旗標，就是缺陷。唯一例外是 cutplan 標記速查，那是內容格式，不是指令。

### 13.3 新的 skill 組（8 → 4）

| 新 skill | 取代 | 本文內容 | 目標行數 |
| :--- | :--- | :--- | :--- |
| `to-md` | audio-to-md、video-to-md、doc-to-md、doc-vlm-to-md、good-student-notes | 任何輸入 → 乾淨 Markdown。順序：`input_probe` → `ingest` → 依序處理 marker。判斷點：圖片閘門、版面確認、校稿、要不要做立場筆記。`references/` 分 audio、video、figures、notes 四份 | 120 |
| `good-student` | good-student（後半）、astro-chunking | 知識點切卡。啟動儀式、試切 5 張、卡片格式、RAG 契約、canvas、驗收清單、派工守則。Step 0 縮成一句：先走 `to-md`，`input_probe` 說有圖就不准跳過 | 250 |
| `podcast-cut` | podcast-cut | 流程：`session_find` → `cut_check` → 提案或人審 → `cut_render` → `loudness_report` → `diff_clips`。判斷點：合軌或分軌、提案規則、響度判讀、何時走 Audacity、何時可以推 Drive。cutplan 標記速查 | 70 |
| `reel-cut`（新） | ai-reels README 的流程段 | `reel_prepare` → 讀逐字稿寫 `summary.json`（語氣、講者、貼圖規則）→ `reel_render` → 交 MM 過目 | 50 |

本文合計約 490 行，是現在的四分之一。

舊名稱的處理：四顆轉檔 skill 的觸發語（「只要逐字稿」「把這份 PDF 轉成 Markdown 就好」「帶截圖的逐字稿」）全部併進 `to-md` 的 description。`/audio-to-md` 等舊斜線指令不留別名；`~/.claude/skills/` 的 symlink 同步增刪。

4 顆都住 `studio-mcp/skills/`，跟轉接頭同一個 repo、同一個 commit 改；`~/.claude/skills/` 用 symlink 指過去。good-students-note 的 `.claude/skills/` 在各期把對應的舊 skill 刪掉。

不對外發布（§11 第 6 項）之後，skill 只需要寫給這台機器的 agent 看：不寫安裝步驟、不寫 Windows 路徑、不寫「沒有外網時的降級路徑」。

### 13.4 兩條文件管線要先選一條

現在有兩套做同一件事的程式：

| | 由誰用 | 入口 |
| :--- | :--- | :--- |
| A | `session.py new <pdf>`（ADR 0008） | `scripts/doc/extract.py`、`scripts/doc/figures.py` |
| B | doc-to-md、doc-vlm-to-md 兩顆 skill | skill 目錄裡的 `doc_to_md.py`、`kb_prep.py` 等 17 支 |

`ingest` 只能包一條。我沒有實跑比較過，不在這裡下結論。P0 盤點要交出兩條的差異表（各自獨有的能力：直排轉橫排、EPUB 內嵌圖、OCR-first、語料倉同步、打包 zip），P4a 由 opus 定案並寫 ADR。這是整案裡唯一還沒有答案的設計問題。

### 13.5 skill 的驗收（三項，每顆都要過）

1. **搬遷對照表**：舊 skill 的每一段（以標題為單位）都對到一個去處：工具的閘、工具說明、共用說明、新 skill、references、或「刪除＋理由」。沒有對不到去處的段落。
2. **機械檢查**：新 skill 本文 `grep` 不到 `scripts/`、`.venv`、`python3`；提到的每個工具名都在 server 的工具清單裡。
3. **冷啟動實測**：開一個新 session，只給 skill 與 MCP，交辦該線的標準任務（剪輯線：EP22 只驗不剪；知識線：一個 3 頁 PDF 轉 Markdown；短影音線：EP15 準備到寫摘要之前）。全程沒有讀 `scripts/` 底下任何檔案才算過。

第 3 項驗的是結果，不是文件寫得好不好看。

### 13.6 連帶要改的地方

- `good-students-note/CLAUDE.md` 的三軌分工表與 Quick Start：加上 studio MCP 這個入口。
- `mars-cc` 裡提到舊 skill 名稱的地方（其他 skill、protocols、記憶索引）：P4b 的工單要求先 `grep` 列出，再逐一改。
- `doc-to-md-installer/`、`doc-vlm-to-md-installer/`：P4b 刪除（§11 第 6 項）。
- 卡 #719（skill set 對外發布）：前提跟這四顆 skill 脫鉤，留言註明，不動狀態。

