---
name: good-student-notes
description: 將媒體(音訊/影片)或文件(PDF/EPUB/TXT)檔案轉為結構化文本，經 QAQC 清理與 AI 校稿後，生成好學生筆記。Use when user provides a media or document file and asks for transcription, extraction, notes, or 好學生筆記.
argument-hint: <media_or_doc_file> [identity] [--context <data-or-file>] [--domain <name>] [--vlm]
allowed-tools: Bash, Read, Write, Glob, Grep, Edit
---

# 好學生筆記 CLI 工作流程

你是一個好學生筆記生成系統。當使用者提供媒體或文件檔案時，嚴格按照以下步驟執行。

**所有作業規範請遵循本專案的 `CLAUDE.md`。**

---

## 輸入解析

使用者會以下列方式呼叫：
```
/good-student-notes <media_or_doc_file> [identity] [--context <data-or-file>] [--domain <name>] [--vlm]
```

- `<media_or_doc_file>`:輸入檔案路徑(必要)。`scripts/session.py new` 依副檔名自動分流
  (CLAUDE.md 原則 12):
  - **媒體(音訊/影片)**:走既有轉錄線,Groq/本地 Whisper → SRT → QAQC 校稿
  - **文件(`.pdf` / `.epub` / `.txt`)**:走文件抽取線,`scripts/doc/extract.py`
    (Phase-1 確定性抽取,0 token)直接產出 `cleaned.md`,**跳過** transcribe/phase-a/
    phase-b(ASR 專屬清理對已乾淨文件無意義)
- `[identity]`:使用者身份/專業背景(選填,例如:建築師、軟體工程師)
- `--context <data-or-file>`:本次錄音的背景資料(選填,**僅媒體輸入適用** — 文件線
  的 `scripts/session.py new` 目前不透傳 `extract.py` 自己的 `--corrections` 選項,
  文件輸入沒有對應的 context 旗標)
  - 可以是一段文字(以引號包起)
  - 可以是一個 `.txt` 檔路徑
  - 可以是「剛才使用者貼在對話裡的資料」— 若對話中有補充內容,你應該先把它寫入 `/tmp/context_<session>.txt` 再傳入
- `--domain <name>`:領域詞典(選填,例如:parenting, tech)— 對應 `dict/typo_dict.<name>.json`(僅媒體輸入適用)
- `--vlm`:文件輸入限定,僅對 `.pdf` 有意義 — 先跑 `scripts/doc/figures.py` 渲染內嵌圖表/
  向量圖表頁/掃描頁進 `images/`,再複用既有 `--images` 的圖片理解(`describe_images.py`)
  + 自動插圖(`insert_images.py`)marker 呼叫路徑,不重造理解邏輯
- 如果未提供 identity,跳過好學生筆記生成(只產結構化文本和合併校稿)

---

## 核心架構原則(必須遵循)

1. **SRT 不可變**:`transcript.srt` 是原始證據,絕不回寫(僅媒體輸入適用;文件輸入沒有 SRT)。
2. **時間軸保護**:Phase B 的 LLM 校稿永遠**看不到時間戳**(由 `scripts/qaqc_phase_b.py --mode structured` 保證)。
3. **Context 綁 Session**:使用者提供的 context 綁在 `sessions/<id>/context.txt`,不汙染專案。
4. **Session 容器**:所有產物歸位在 `sessions/<id>/`。
5. **輸入轉接器(CLAUDE.md 原則 12)**:抽取器只做 Phase-1 確定性抽取(0 token),絕不
   呼叫 LLM 或自己寫摘要;摘要/理解一律留給下游共用的 enhance/notes 層。

---

## 執行步驟(一行命令)

整個流程已封裝為 `scripts/session.py`,你只需呼叫:

```bash
# 媒體(音訊/影片)輸入:
python3 scripts/session.py new "<media_file>" \
  [--context "<data-or-file>"] \
  [--domain <name>] \
  [--stop-at {transcribe|phase-a|phase-b|enhance|notes}]  # default: phase-b
  [--keywords "term1,term2"] \   # 觸發 Step 3
  [--enhance] \                  # 觸發 Step 3(自動辨識術語)
  [--identity "<立場>"]          # 觸發 Step 4(需 --stop-at notes)

# 文件(pdf/epub/txt)輸入(同一入口,副檔名自動分流):
python3 scripts/session.py new "<doc_file.pdf>" \
  [--vlm] \                      # 僅 pdf 有意義:圖表/掃描頁理解
  [--identity "<立場>"] --stop-at notes   # 一路到好學生筆記,寫法與媒體輸入相同
```

### 停點原則(R6.2)

**不要預設每次跑完 Step 4**。大宗使用者在 Step 2(cleaned.md)就已滿足。依使用者
意圖選擇停點:

| 使用者意圖 | `--stop-at` |
|-----------|-------------|
| 只要 SRT 字幕檔(僅媒體輸入) | `transcribe` |
| 要時間軸保留的錯字修正版(僅媒體輸入) | `phase-a`(同 `--skip-phase-b`) |
| 要去時間軸、合併、通順的稿 | `phase-b`(預設) |
| 外加專有名詞補充 | `enhance` 並提供 `--keywords` 或 `--enhance` |
| 一路到好學生筆記 | `notes` 並提供 `--identity` |

**文件輸入(pdf/epub/txt)沒有 `transcribe`/`phase-a`/`phase-b` 這幾個中間停點**——
`extract.py` 一律直接產出完整的 `cleaned.md`(見 CLAUDE.md 原則 12),`--stop-at`
對文件輸入只在 `enhance`/`notes` 才有意義。

### 腳本自動完成

**媒體(音訊/影片)輸入:**

1. 計算 session slug:`YYYY-MM-DD_<sanitized-filename>`,建立 `sessions/<slug>/`
2. symlink 音檔為 `source.<ext>`,寫入 `context.txt`、`metadata.json` 骨架
3. 呼叫 Groq Whisper → `transcript.srt`(時間軸保留、以 context.txt 作 prompt)
4. 呼叫 `SRT/qaqc_srt.py --domain <name>` → Phase A(錯字、幻覺、亂碼過濾)
5. (若未 `--stop-at` 早於 phase-b)呼叫 `scripts/qaqc_phase_b.py --mode merged` → `cleaned.md`
6. (若 `--stop-at enhance|notes` 且有 keywords/enhance 旗標)→ `enhanced.md`
7. (若 `--stop-at notes` 且有 `--identity`)→ `notes_<立場>.md`
8. 寫入 `metadata.json` 的字數比率、耗時、錯字命中數、stop_at 等

**文件(pdf/epub/txt)輸入,取代步驟 3-5:**

1-2. 同上(slug、session 目錄、symlink、`context.txt`)
3'. 呼叫 `scripts/doc/extract.py`(用 `.venv-doc`)→ 直接產出 `cleaned.md`,
    **不產** `transcript.srt`/`cleaned.srt`(文件輸入無 ASR、無時間軸)
3.5'. (若 `--vlm` 且為 pdf)呼叫 `scripts/doc/figures.py` → `images/` +
    `doc_figures.json`,再寫 `.images_pending.json` / `.image_insert_pending.json`
    marker(同既有 `--images` 呼叫路徑,marker 接手協議見 CLAUDE.md 原則 5)
6-8. 之後與媒體輸入共用(enhance/notes/metadata)

---

## 你(Claude)的角色

腳本跑完後,你要做的是**檢視與回報**,不要再逐字校稿:

1. `ls sessions/<slug>/` 確認所有產物齊全:
   - `source.<ext>`(symlink)
   - `context.txt`
   - `transcript.srt`(**僅媒體輸入**;文件輸入不產這份,`cleaned.md` 是直接抽取結果)
   - `cleaned.md`
   - `metadata.json`
   - (若有 identity)`notes_<identity>.md`
   - (文件輸入 + `--vlm`)`images/`、`doc_figures.json`
2. 讀 `metadata.json`,檢查:
   - **媒體輸入**:`qaqc.ratio_chinese` 是否在 [0.95, 1.05] 區間、`summary_words_check` 是否為 0
   - **文件輸入**:`source_type` 為 `doc`、`doc_extraction` 欄位的 stats(`sections`/`lang`/`vertical_pages`)是否合理(這些是確定性抽取,沒有 ASR 字數比率門檻)
3. 讀 `cleaned.md`,若發現明顯錯字或專名錯誤,寫入 `sessions/<slug>/corrections.json`:
   ```json
   {"_meta": {"session": "<slug>", "domain_candidate": "<name>"},
    "corrections": {"錯字": "正字", ...}}
   ```
   這份 `corrections.json` **不會**自動合進 `dict/typo_dict.<domain>.json`,
   需要使用者手動審閱後推送(避免單 session 的偶發誤判污染共用詞典)。
4. 若 Phase B 的字數比率 < 0.95,或 `structured` 長度檢查失敗,**不要**嘗試
   自己補救;在回報中明確標示,讓使用者決定是否重跑或手動修。

---

## 完成報告

```
✅ 好學生筆記工作流程完成

📁 Session:sessions/<slug>/
  - source.<ext>             (symlink, N MB)
  - context.txt              (N bytes)
  - transcript.srt           (N segments)
  - cleaned.md               (N chars, ratio 0.XX)
  - notes_<identity>.md      (若有)
  - metadata.json

📊 統計:
  - Groq 耗時:N.N 秒
  - Phase A 命中:M 組錯字、K 個幻覺段
  - Phase B 字數比率:0.XX (target [0.95, 1.05])
  - 領域詞典:base + <domain>(N 組)

⚠  待人工確認:(若有)
  - corrections.json 候選條目:K 組(尚未推送至 dict/)
```

**文件輸入變體**:無 `transcript.srt`、無 Groq/Phase A/Phase B 統計;改報
`doc_extraction` stats(`input_type`/`chars`/`sections`/`lang`/`vertical_pages`),
`--vlm` 有跑則加報 `images/` 圖片數與 marker 狀態。

---

## 不要做的事

- ❌ 不要自己從 `.srt` 做 Phase B 校稿 — 永遠呼叫 `scripts/qaqc_phase_b.py`
- ❌ 不要在根目錄寫產出檔 — 一律寫到 `sessions/<slug>/`
- ❌ 不要用 `SRT/context.example.txt` 當 context — 這是範例檔
- ❌ 不要自動把 `corrections.json` 合進 `dict/typo_dict.*.json` — 需人工審閱
- ❌ 不要在輸出中使用第三人稱描述(「講者提到...」「本段討論...」等)
- ❌ 文件輸入(pdf/epub/txt)不要對 `extract.py` 的輸出做摘要/濃縮 — 它已是零省略確定性抽取,理解/校對一律走 Step 3/4,不要提前幫它「整理」

---

## 降級路徑(若腳本不可用)

若 `scripts/session.py` 或 `scripts/qaqc_phase_b.py` 出錯,可手動執行:

```bash
# Step 1: 轉錄
python3 .claude/skills/good-student-notes/scripts/groq_transcribe.py \
  "<audio>" "<output_dir>" "<context_file>"

# Step 2: Phase A 清理
python3 SRT/qaqc_srt.py "<srt>" --domain <name> -o "<cleaned.srt>"

# Step 3: Phase B 校稿
python3 scripts/qaqc_phase_b.py --mode merged --context "<ctx>" \
  "<cleaned_text>" -o "<cleaned.md>"
```

但正常情況下一律走 `scripts/session.py new`。
