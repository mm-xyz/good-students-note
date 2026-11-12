# studio MCP — 架構與分期計畫（卡 #1267，併 #1175＋ai-reels）

> 2026-11-12 · 狀態：**§11 七件事全部拍板（MM 2026-11-12）**，進入執行。決策摘要見 `docs/adr/ADR-2026-11-12-1267.md`。本檔只有架構與規劃，沒有實作。
> 範圍（MM 2026-11-12）：#1267 ＋ #1175 ＋ ai-reels 的 HyperFrames 短影音線，整個重構。
> 分工：Fable 做架構與規劃；實作由 worker 照 §8 工單做。
> 2026-11-12 追加（MM）：skill 整組重新設計，跟 MCP 一起翻修，見 §13。
> 2026-11-12 拍板（MM）：studio MCP 有自己的 repo；不需要給外部使用者；管線程式搬進新 repo，過去的 session 封存。見 §4.2、§4.7、§11。

## 0. 一頁結論

1. **三條產線，一個入口**：知識線（good-student）、剪輯線（podcast-cut）、短影音線（ai-reels），共用 session 容器。
2. **CLI 是合約，MCP 是轉接頭**：每個能力先有一支吐 JSON 的命令列，MCP 工具只負責轉呼叫。沒有 MCP 的執行器（agy、cron）用同一支命令列。
3. **自己的 repo，管線一起搬**：新 repo `studio-mcp` 放管線程式、轉接頭、命令列入口、4 顆 skill、測試。good-students-note 與 ai-reels 凍結；過去的 session 留在原地封存，只讀。
3a. **先搬家，後改裝**：搬的那一期只改路徑，不改行為，以既有測試全過為準。加 `--json`、做工具都排在搬完之後。
4. **MCP server 不匯入任何管線程式**：一律開子行程，用該產線自己的直譯器跑。
5. **長工作走 job**：轉錄、初剪、出片先回 job id，再查狀態。
6. **出片不碰雲端**：`cut_render` 只寫本地。推到雲端是另一個工具，裡面就是 rclone 加目的路徑。兩份 cutplan 不同時，要明示用哪一份。
7. **skill 從 8 顆收成 4 顆**（§13）：skill 只寫何時用、順序、判斷點；路徑與參數全部交給工具。每條產線的 skill 跟該線的工具同一期做。
8. 分 8 期。P1 搬家，P2 骨架做完就能在 Claude Code 裡找 session，P3 做完剪輯線可用。

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
| 記憶：審稿中勿寫 Drive cutplan | 寫入 Drive 會讓 MM 開著的編輯器整份存檔被拒 | 出片不寫雲端；覆蓋雲端 cutplan 要另外明示 |
| podcast-cut skill | AI 介入剪輯決策必加 `--ai` | `ai` 是必填參數，沒有預設值 |
| 卡 #1267 邊界 | `finalize --apply` 不開放 | 只開 dry-run |
| run_all.sh | 不退回系統 python | 轉接頭找不到 venv 就報錯，不換直譯器 |

## 4. 目標架構

### 4.1 分層

```
Claude Code ──stdio──▶ studio/mcp_server.py      轉接頭（.venv-studio，只裝 mcp）
agy / cron / MM ─────▶ bin/studio <line> <verb> --json     同一個入口
                              │
                              ▼
                       studio/core/              stdlib only
                       config · runners · jobs · result · sessions
                              │ 開子行程
        ┌─────────────────────┼──────────────────────┐
        ▼                     ▼                      ▼
  .venv-audio            .venv-doc / python3        node
  scripts/audio/*        scripts/session.py …       reels/scripts/reel-build.mjs
```

規則：

1. `studio/` 只用標準函式庫。`mcp` 套件只有 `mcp_server.py` 匯入。
2. `studio/` 不匯入 `scripts/` 與 `reels/` 的任何模組，只開子行程。同一個 repo 也一樣。原因：`cut.py` 匯入 `render_cut`，後者需要 numpy；一匯入，server 就綁死在 `.venv-audio`，而且 pyannote 與 torch 會留在長駐行程裡。
3. 每支被包的腳本加 `--json`：stdout 最後一行是一個 JSON 物件。人讀的訊息照舊印，行為不變。
4. 轉接頭不解析人讀的文字。腳本沒有 `--json` 就不包，先補腳本。

### 4.2 目錄

```
studio-mcp/                     新 repo，私有（Forgejo），不對外發布
├── bin/studio                  命令列入口（轉呼叫 python3 -m studio）
├── studio/                     轉接頭：__main__.py、mcp_server.py、core/、lines/
├── scripts/                    從 good-students-note 搬來，子目錄結構照舊
│   ├── audio/                  剪輯核心、轉錄、歸檔
│   ├── doc/  frames/           文件線、影片線
│   ├── cutplan-editor/         MM 的人審介面（Apps Script＋本機版）與它的 node 測試
│   ├── session.py 等           知識線統籌、閘、圖片線
│   └── tests/                  36 支測試與真音訊 fixture
├── reels/                      從 ai-reels 搬來：scripts/、template/
├── skills/                     to-md／good-student／podcast-cut／reel-cut（§13）
├── dict/  prompts/  shared-material/     管線讀的字典、規則、節目配樂
├── docs/adr/  docs/design/     兩個舊 repo 的 ADR 與本檔
├── tests/                      轉接頭的測試：合約、失敗注入、MCP 冒煙
├── studio.toml                 進版控的預設值
├── studio.local.toml           不進版控：Drive 根、封存 session 的位置、hyperframes 位置
├── sessions/                   不進版控：新的 session 從這裡開始
└── _local/jobs/                不進版控：job 紀錄
```

`scripts/` 的子目錄在搬家時不改名。理由：23 個檔靠相對位置找彼此與專案根目錄，測試也是。目錄重整排在最後一期，那時已有合約測試保護。

**不搬、留在 good-students-note 的**：`web/`（上游網站與 Web 工作室）、`scripts/lang/`、`build_genai2026_day*.py`、出版部署（`publish_goodedunote.sh` 一組）、上游的講稿。逐檔的去留由 P0 盤點表定。

**一定要搬、容易漏的**（MM 2026-11-12 點名）：

| 東西 | 位置 | 說明 |
| :--- | :--- | :--- |
| 清理程式 | `SRT/qaqc_srt.py` | 轉錄後套字典改錯字、濾幻覺與亂碼，產 `cleaned.srt`。不在 `scripts/` 底下 |
| 字彙辭典 | `dict/` 7 個檔 | 通用、bim、parenting、productivity 四份錯字表，加幻覺開頭詞與清理設定 |
| 散在別處的字彙 | astro-chunking 的 ASR 錯字對照；11 個 session 的 `corrections.txt` | 當初該推回 `dict/` 但沒推。P5b 收成新的領域字典（占星、紫微、podcast），封存的 session 才不會把它們一起埋掉 |
| good-student | skill 本體＋`build_canvas.py` | 搬進 `skills/good-student/`，P5b 改寫 |

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
| `REMOTE_FAILED` | rclone 回非零：遠端沒設定、授權過期、路徑不存在。附 rclone 的錯誤原文 |
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

第一批只改兩處寫死的路徑：`cut.py` 的 `DRIVE_ROOT`、`render_cut.py` 的 `MATERIAL_ROOT`。改成「有設定讀設定，沒有設定用原值」，舊用法不壞。其餘 20 多個檔留到 P7。

### 4.6 註冊

照 beanquery 先例，user scope，任何專案都叫得到：

```
claude mcp add --scope user studio -- <studio-mcp>/.venv-studio/bin/python -m studio.mcp_server
```

`/mcp-add` skill 只處理 npm 套件，這裡不適用。

### 4.7 搬遷與 session 封存（MM 2026-11-12 拍板）

| 項目 | 做法 |
| :--- | :--- |
| 來源定版 | good-students-note 與 ai-reels 各打一個 tag `pre-studio-split`。搬的內容以這個 tag 為準，ADR 記下兩個 commit |
| 歷史 | 不帶 git 歷史，用複製。要查舊的 blame 回原 repo 看。40 多份 ADR 一起搬，「為什麼」不會丟 |
| 過去的 session | 124 個全部留在 `good-students-note/sessions/`，不搬、不改。`studio.local.toml` 的 `archive_roots` 指過去 |
| 封存的 session 能做什麼 | `session_find`、`session_status` 找得到，標示 `archived`。可以當短影音線的輸入（`reel_prepare` 只讀）。**不能**初剪、出片、處理 marker，這些工具對封存 session 回 `GATE_FAILED` |
| 要重剪舊集數 | 用命令列 `bin/studio session adopt <路徑>`：把該 session 整個搬進新的 `sessions/`（連同 `.drive_dir`）。不做成 MCP 工具，由 MM 或在 MM 指示下執行 |
| 還在進行中的集數 | 沒有。EP22 已上架（MM 2026-11-12），連同其餘 session 一起封存。下一集直接在新 repo 開 |
| 舊 repo | 搬完後 good-students-note 的 README 加一段指向新 repo；`.claude/skills/` 清空；程式不刪，維持可跑，但不再修改 |
| ai-reels | `scripts/`、`template/`、3 份 ADR 搬走；PoC 四版與 `runs/` 留在原 repo。原 repo 凍結 |
| 直譯器 | `.venv-audio`、`.venv-doc` 在新 repo 重建（照 `requirements-*.txt`）。模型快取在家目錄，不必重抓 |

搬家那一期（P1）的完成定義只有一條：**新 repo 裡 `scripts/tests/run_all.sh` 與 cutplan-editor 的 node 測試全過，數量與舊 repo 相同；把封存的 EP22 session 複製一份到暫存目錄，新舊 repo 各對它跑 `cut.py --check --no-push`，輸出逐行相同。** 這一期不加任何新功能。

## 5. 工具清單（19 個）

「寫入」欄：無＝只讀；本地＝只寫 session 或工作目錄；Drive＝會寫 Google Drive。

### 共用（6）

| 工具 | 做什麼 | 型態 | 寫入 |
| :--- | :--- | :--- | :--- |
| `doctor` | 檢查三個直譯器、ffmpeg、node、hyperframes、rclone 遠端可列目錄、llm-node 可連 | 同步 | 無 |
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

### 剪輯線（7）

| 工具 | 包哪支 | 型態 | 寫入 |
| :--- | :--- | :--- | :--- |
| `precut(session, line, num_speakers)` | `precut.py` | job | 本地 |
| `cut_check(session, plan?)` | `cut.py --check --json`：cutplan 兩份的語意差異＋dry-run 摘要 | 同步 | 無 |
| `cut_render(session, ai, use?, plan?)` | `cut.py --yes --json`。`ai` 必填。兩份不同時 `use` 必填（`remote`／`session`）。**不寫雲端** | job | 本地 |
| `publish_version(session, version?, sync_cutplan=false)` | 新增 `scripts/audio/remote.py`：`rclone copy <版本目錄> <遠端>:<集數資料夾>/<版本目錄>`。預設推最新一版。`sync_cutplan=true` 才把工作版 cutplan 覆蓋到集數根 | job | 雲端 |
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
| 推回雲端撞上 MM 開著的編輯器 | 推版本目錄是新增資料夾，不會撞。會撞的只有覆蓋集數根的 cutplan，所以 `sync_cutplan` 預設 false；設 true 前先問 MM 編輯器是否已關。雜湊相同不算確認 |
| 自動選錯 cutplan | 有 `--json` 時，兩份不同且沒帶 `--use` 就回 `CUTPLAN_DIVERGED` 並附語意差異。不再依修改時間挑 |
| 跳過人審 | `.cutplan_pending.json` 存在時 `cut_render` 回 `REVIEW_PENDING` |
| AI 版本沒標記 | `ai` 沒有預設值，漏填即參數錯誤 |
| `--plan` 不是 cutplan.md 時蓋掉 Drive 合軌節目單（卡 #1081） | P3 一併修：`publish_version` 推的 cutplan 檔名跟著 `--plan` 走 |
| 歸檔誤搬 | `finalize --apply`、`tidy --apply` 不開放 |
| 打到付費 API | 轉接頭不帶任何 API key 類環境變數給子行程；`scripts/lang/` 整個不包 |
| Audacity 開著時改專案檔 | `inject` 沿用腳本既有的 `lsof` 檢查，失敗回 `GATE_FAILED` |

## 7. 腳本歸屬（家族層級）

逐檔盤點表是 P0 的產出；這裡先定家族層級的去留。

| 家族 | 檔數 | 進 MCP | 理由 |
| :--- | :--- | :--- | :--- |
| `scripts/audio/` 剪輯核心（cut、render_cut、cutplan、precut、pertrack_*、tracks_columns、ingest_tracks、prosody、diarize、pause_rows、change_preview、diff_clips、audacity_labels） | 約 17 | 是，P3 | 剪輯線主體 |
| `scripts/audio/` 轉錄與標點（transcribe_*、resegment_srt、restore_punct*、check_punct_density、fillers_local） | 約 8 | 間接 | 由 `ingest`／`precut` 帶動，不單獨開工具 |
| `scripts/audio/` 歸檔（finalize、tidy_session、migrate_marks） | 3 | 只預覽 | 破壞性 |
| `scripts/audio/` 文案（copy_prompt_build、finalize_copy） | 2 | 暫不 | 跟卡 #680 一起做 |
| `scripts/session.py` 與閘（qaqc_phase_b、normalize_punctuation、prepublish_gate、pipeline_logger） | 約 6 | 是，P5 | 知識線主體 |
| 圖片線（describe_images、insert_images、placement_*、propose_anchors…） | 約 10 | 間接 | 由 `ingest` 的 `images` 選項帶動 |
| `scripts/doc/`、`scripts/frames/` | 10 | 間接 | 由 `ingest` 依副檔名分流 |
| 出版（publish_goodedunote.sh、publish_qaqc、md_to_html、compress_images） | 4 | 否 | 對外發布，MM 手動 |
| `scripts/lang/`（義、日、英批次） | 17 | 否 | 上游遺留，走付費 API |
| `build_genai2026_day*.py` | 2 | 否 | 一次性 |
| `scripts/cutplan-editor/` | 13 | 否 | MM 的人審介面；它的 node 測試接進總測試 |
| skill 自帶腳本（doc-vlm-to-md 17 支等） | 約 25 | P5 評估 | 先盤點重複，再決定併入 `ingest` 或維持 skill 內呼叫 |
| `web/` | — | 否 | Web 工作室，另一條軌 |
| ai-reels `scripts/` | 7 | 是，P6 | 短影音線主體 |

## 8. 分期

每一期都是：worker 實作（TDD，先寫失敗的測試）→ 另一個 agent 對抗性驗收 → 併回 main。一期一張卡，掛在 #1267 底下。時間是預估，以一個 worker session 為單位。

| 期 | 內容 | 執行體 | 驗收重點 | 預估 |
| :--- | :--- | :--- | :--- | :--- |
| P0 | 逐檔盤點表（每支腳本：家族、誰呼叫它、有無測試、搬不搬、進不進 MCP）；重複腳本清單；兩條文件管線差異表；全機器引用 good-students-note 路徑的地方（launchd、mars-cc 腳本、skill symlink、llm-node 上的批次） | in-process Explore | 表列檔數＝`find` 實數 | 1 小時 |
| P1 | **搬家，不改行為**：建 `studio-mcp`（本地＋Forgejo 私有）；照盤點表複製 `scripts/`、`reels/`、`dict/`、`prompts/`、`shared-material/`、ADR、設計文件；重建兩個 venv；舊 repo 打 tag、加指路說明 | Claude sonnet | §4.7 的完成定義 | 3–4 小時 |
| P2 | `studio/` 骨架：config、runners、jobs、result、sessions（含封存根目錄與 `archived` 標示）；工具 `doctor`、`session_find`、`session_status`、`job_*`；`session adopt` 命令列；`.venv-studio`；註冊 | Claude sonnet | 用 stdio 起 server，列得出 6 個工具；`session_find("EP16")` 回封存的 session 並標 `archived`；殺掉 server 後 job 還活著 | 2–3 小時 |
| P3 | 剪輯線 7 個工具。`cut.py` 加 `--json`、`--use`，雲端讀寫改走新增的 `remote.py`（rclone）；修 #1081；新增 `loudness.py`；**重寫 podcast-cut skill** | Claude sonnet | EP22 的 `cut_check` 摘要行與 `cut.py --check` 逐行相同；§6 每一個閘各有一個失敗案例測試；對封存 session 出片被拒；`cut_render` 全程沒有呼叫 rclone 的寫入；skill 過 §13.5 三項檢查 | 4–5 小時 |
| P4 | server 的共用說明（`instructions` 欄位）定稿：job 怎麼等、錯誤碼怎麼處置、三條產線怎麼選、封存 session 的限制 | Claude sonnet | 新 session 不載任何 skill，只靠共用說明能完成「找 EP22 → 看狀態」 | 30 分鐘 |
| P5a | 兩條文件管線的取捨（§13.4），寫成 ADR | Claude opus | 差異表每一列有實跑證據；結論是「留哪一條、另一條的獨有能力怎麼搬」 | 1–2 小時 |
| P5b | 知識線 4 個工具。`session.py new` 加 `--json`；新增 `markers.py`、`input_probe.py`；**新寫 to-md skill、重寫 good-student skill**；舊 skill 下架；astro-chunking 封存 | Claude sonnet | 用一個 30 秒音檔與一個 3 頁 PDF 跑完 `ingest`；marker 字數不足時 `marker_done` 拒絕；skill 過 §13.5 三項檢查 | 5–6 小時 |
| P6 | 短影音線 2 個工具。`reel-build.mjs` 加 `--json`；**新寫 reel-cut skill** | Claude sonnet | 以封存的 EP15 session 為輸入重跑，產物與 ai-reels `runs/ep15` 的 `config.used.json`、`captions.json` 相同；skill 過 §13.5 三項檢查 | 2–3 小時 |
| P7 | 清理：其餘寫死路徑改讀設定；`doc_to_md.py` 三份併一份；`scripts/` 目錄重整與 `render_cut.py` 拆檔另開卡評估 | Claude sonnet，機械部分 haiku | 全部測試通過；`grep` 寫死的家目錄路徑歸零 | 2–3 小時 |

依賴：P0 → P1 → P2，之後 P3 → P4、P5a → P5b、P6 三條互不相依。Pro 方案同時只撐 2 條線，所以一次派一期實作＋一期驗收。P7 最後。

**P1 開工到驗收通過之間，兩個舊 repo 不收任何程式改動**，否則搬過去的版本會落後。進行中的卡（#564、#1083、#1081 等）一律等 P1 完成後改在新 repo 做。

驗收一律由另一個 agent 做（agy，複雜判斷用 `claude-opus-5-5-high` 或 `gemini-4-argon-high`），工單要求注入失敗並對發現編號。

## 9. 測試策略

1. **合約測試**：每個 verb 的 `--json` 輸出欄位固定；轉接頭的工具清單與參數有快照。
2. **等價測試**：新入口與舊命令列在同一個 fixture session 上結果相同（P3 用既有的真音訊 fixture）。P1 另有一次跨 repo 的等價測試（§4.7）。
3. **失敗注入**：§4.3 每個錯誤碼至少一個案例。Drive 不可讀用一個權限為 000 的假目錄模擬。
4. **不靠重跑比對**：本地 whisper 不是確定性的，轉錄相關只驗結構，不驗文字。
5. **測試分兩處**：管線腳本的測試照舊在 `scripts/tests/`，由 `run_all.sh` 跑（要 `.venv-audio`）。轉接頭的測試在 `tests/`，用 unittest，只需要標準函式庫。

## 10. 風險

| 風險 | 處置 |
| :--- | :--- |
| ❗ [Major] good-students-note 主樹現有 3 個未提交的改動（`good-student/SKILL.md`、`scripts/frames/extract.py`、`scripts/audio/restore_punct_txt.py`），不是本案的 | **P1 打 tag 前必須先處理**：請當初改的 session 提交，或由 MM 決定丟棄。沒提交就打 tag，這三處改動不會被搬過去 |
| ❗ [Major] 卡 #564（review:modifying）與 #1083 都會動 `cut.py` | P1 開工前先確認這兩張卡的改動已進舊 repo 的 main，或明確延到新 repo 做 |
| rclone 的 Google 授權過期 | `doctor` 每次核對遠端可列目錄；失敗回 `REMOTE_FAILED`，由 MM 重新授權 |
| ❗ [Major] 搬家期間舊 repo 又被改，新 repo 落後 | 以 tag 為準；P1 期間舊 repo 凍結（§8）；P1 驗收時核對舊 repo 自 tag 以來沒有新 commit |
| ❗ [Major] 別處寫死了 good-students-note 的路徑（skill symlink、mars-cc 腳本、排程），搬完後靜默失效 | P0 全機器 `grep` 列清單，P1 逐一改；改不到的在舊 repo 原位留著仍可跑 |
| 新 venv 的套件版本跟舊的不同，結果有細微差異 | P1 先 `pip freeze` 舊 venv，新 venv 照同一份版本裝 |
| 封存的 session 之後格式跟不上新程式 | 封存 session 只讀、不保證能被新程式處理；要用就 adopt，adopt 時跑一次 `session_status` 檢查 |
| 工具數量吃 context | 19 個為上限。要加新工具先看能不能併進既有工具的參數 |
| `--json` 加進 2,650 行的 `render_cut.py` 牽動大 | 不動 `render_cut.py` 的輸出。由 `cut.py` 收集它既有的 `[render]` 行，組進自己的 JSON |

## 11. 請 MM 拍板

每一項都有預設。回「好」就照預設走。七項全部已定。

| # | 問題 | 預設 | 另一個選項 |
| :--- | :--- | :--- | :--- |
| 1 | studio MCP 放哪個 repo | ✔ **已定（MM 2026-11-12）：自己的 repo `studio-mcp`** | — |
| 2 | 管線程式要不要也搬進新 repo | ✔ **已定（MM 2026-11-12）：搬。過去的 session 封存**，做法見 §4.7 | — |
| 3 | 搬過去的命令列 | ✔ **已定（MM 2026-11-12）：保留原樣，各加 `--json`**。MM 平常由 agent 經 MCP 呼叫，自己不打指令，所以選改動最小的做法 | — |
| 4 | 出片後推回雲端 | ✔ **已定（MM 2026-11-12）：包一個 rclone 加目的路徑**。獨立成 `publish_version` 工具，見 §5 與下方說明 | — |
| 5 | 卡 #1175 | ✔ **已定（MM 2026-11-12）：discard**，已執行 | — |
| 6 | 給外部使用者的版本 | ✔ **已定（MM 2026-11-12）：不需要**。Windows 安裝包、Claude Desktop 沙箱用法、兩個 installer 目錄直接刪除（git 歷史留得到），不另外凍結 | — |
| 7 | astro-chunking skill | ✔ **已定（MM 2026-11-12）：封存**。切卡方法已在 good-student；占星專用的三段先搬進 `good-student/references/astro.md` 再封存（§13.3） | — |

**第 4 項的落地（rclone）**：本機已有 rclone 1.75 與 `gdrive:`、`pcloud:` 兩個遠端。目的地寫在 `studio.local.toml`（例：`podcast.remote = "gdrive:<節目資料夾>"`），換雲端只改這一行，卡 #1202（編輯器改接 pCloud）之後可以直接沿用。P3 把 `cut.py` 裡讀寫 Drive 掛載路徑的部分全部換成 `remote.py`：讀遠端 cutplan 用 `rclone copyto` 抓到暫存檔再比對，寫用 `rclone copy`。好處是不再經過 macOS 的資料夾權限，tmux 底下被擋的問題（Operation not permitted）跟著消失；`.drive_dir` 的配對改記遠端路徑。

接著做的事：開 P0–P7 九張卡、派 P0。P1 要等 P0 的盤點表。

## 12. 不做

- 搬家時不改 `scripts/` 的子目錄結構，不改既有命令列的預設行為。
- 不搬過去的 session，不帶 git 歷史。
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
| good-student-notes | 201 | 上游留下的 `session.py` 包裝，跟 `ingest` 重疊。它的「身份視角註解筆記」124 個 session 沒有任何一個產出過，不搬 |
| audio-to-md | 198 | 一半篇幅是 Windows 與 Claude Desktop 的安裝說明（不對外發布後整段可刪） |
| doc-to-md | 123 | 同上；`doc_to_md.py` 有 3 份副本 |
| video-to-md | 98 | 內文還寫 LM Studio（已退役） |
| podcast-cut | 98 | 指令與路徑佔大半 |
| astro-chunking | 122 | good-student 的前身，已設成不自動觸發 |
| 短影音線 | 0 | 沒有 skill，流程寫在 ai-reels 的 README |

清理的實際使用情況（2026-11-12 實查）：109 個有逐字稿的 session 裡，104 個有 `cleaned.srt`（套字典的清理，保留時間軸），只有 11 個走到 `cleaned.md`（合併段落的全文）。知識線實際上是拿 `cleaned.srt` 直接切卡。`to-md` 的標準終點因此訂為 `cleaned.srt`，`cleaned.md` 是選配。

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
| `to-md` | audio-to-md、video-to-md、doc-to-md、doc-vlm-to-md、good-student-notes | 任何輸入 → 乾淨 Markdown。順序：`input_probe` → `ingest` → 依序處理 marker。判斷點：圖片閘門、版面確認、選哪個領域字典、校稿。`references/` 分 audio、video、figures 三份 | 120 |
| `good-student` | good-student（後半）、astro-chunking | 知識點切卡。啟動儀式、試切 5 張、卡片格式、RAG 契約、canvas、驗收清單、派工守則。Step 0 縮成一句：先走 `to-md`，`input_probe` 說有圖就不准跳過 | 250 |
| `podcast-cut` | podcast-cut | 流程：`session_find` → `cut_check` → 提案或人審 → `cut_render` → `loudness_report` → `diff_clips`。判斷點：合軌或分軌、提案規則、響度判讀、何時走 Audacity、何時可以推 Drive。cutplan 標記速查 | 70 |
| `reel-cut`（新） | ai-reels README 的流程段 | `reel_prepare` → 讀逐字稿寫 `summary.json`（語氣、講者、貼圖規則）→ `reel_render` → 交 MM 過目 | 50 |

本文合計約 490 行，是現在的四分之一。

astro-chunking 的去處（2026-11-12 實查）：通用的切卡方法（一概念一檔、frontmatter 鎖死、驗收清單、派工守則）已經在 good-student。但 good-student 第 648 行仍把三段占星專用內容指回 astro-chunking：受控詞彙表、目錄結構、ASR 錯字對照。astro-chunking 本體在 `good-students-note/.claude/skills/`，`mars-cc/000_Agent/skills/astro-chunking` 是指過去的 symlink（good-student 寫的是後者）。P5b 把這三段搬進 `good-student/references/astro.md`、改掉那個指標，再封存本體並移除 symlink。白瑜、唐綺陽的占星知識庫之後補切會用到，不能直接丟。

舊名稱的處理：四顆轉檔 skill 的觸發語（「只要逐字稿」「把這份 PDF 轉成 Markdown 就好」「帶截圖的逐字稿」）全部併進 `to-md` 的 description。`/audio-to-md` 等舊斜線指令不留別名；`~/.claude/skills/` 的 symlink 同步增刪。

4 顆都住 `studio-mcp/skills/`，跟工具同一個 repo、同一個 commit 改；`~/.claude/skills/` 用 symlink 指過去。P1 先把舊 skill 原樣搬來（只改 repo 路徑）讓日常工作不中斷，各期再換成新版。

不對外發布（§11 第 6 項）之後，skill 只需要寫給這台機器的 agent 看：不寫安裝步驟、不寫 Windows 路徑、不寫「沒有外網時的降級路徑」。

### 13.4 兩條文件管線要先選一條

現在有兩套做同一件事的程式：

| | 由誰用 | 入口 |
| :--- | :--- | :--- |
| A | `session.py new <pdf>`（ADR 0008） | `scripts/doc/extract.py`、`scripts/doc/figures.py` |
| B | doc-to-md、doc-vlm-to-md 兩顆 skill | skill 目錄裡的 `doc_to_md.py`、`kb_prep.py` 等 17 支 |

`ingest` 只能包一條。我沒有實跑比較過，不在這裡下結論。P0 盤點要交出兩條的差異表（各自獨有的能力：直排轉橫排、EPUB 內嵌圖、OCR-first、語料倉同步、打包 zip），P5a 由 opus 定案並寫 ADR。這是整案裡唯一還沒有答案的設計問題。

### 13.5 skill 的驗收（三項，每顆都要過）

1. **搬遷對照表**：舊 skill 的每一段（以標題為單位）都對到一個去處：工具的閘、工具說明、共用說明、新 skill、references、或「刪除＋理由」。沒有對不到去處的段落。
2. **機械檢查**：新 skill 本文 `grep` 不到 `scripts/`、`.venv`、`python3`；提到的每個工具名都在 server 的工具清單裡。
3. **冷啟動實測**：開一個新 session，只給 skill 與 MCP，交辦該線的標準任務（剪輯線：EP22 只驗不剪；知識線：一個 3 頁 PDF 轉 Markdown；短影音線：EP15 準備到寫摘要之前）。全程沒有讀 `scripts/` 底下任何檔案才算過。

第 3 項驗的是結果，不是文件寫得好不好看。

### 13.6 連帶要改的地方

- `good-students-note/CLAUDE.md` 的三軌分工表與 Quick Start：加上 studio MCP 這個入口。
- `mars-cc` 裡提到舊 skill 名稱的地方（其他 skill、protocols、記憶索引）：P5b 的工單要求先 `grep` 列出，再逐一改。
- `doc-to-md-installer/`、`doc-vlm-to-md-installer/`：不搬進新 repo（§11 第 6 項），留在舊 repo 隨它凍結。
- 卡 #719（skill set 對外發布）：前提跟這四顆 skill 脫鉤，留言註明，不動狀態。

