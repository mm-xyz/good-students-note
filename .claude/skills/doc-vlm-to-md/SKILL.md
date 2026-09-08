---
name: doc-vlm-to-md
description: 一條指令把一份文件「一次」轉成「文字 + 圖表」都進得了 RAG 的單一 Markdown 知識庫。整合 doc-to-md（文字、章節摘要）與 vlm-to-md v1.0.1（嵌入點陣圖 + 向量圖表頁 + 掃描頁視覺），v1.3.0 起補上 EPUB 視覺線（zip 內嵌圖，錨點原位插圖）。當需要：(1) 把 PDF/EPUB/TXT 轉成含章節摘要的文字知識庫 (2) 同一份 PDF 裡的圖表、流程圖、框架圖、掃描頁也一起轉成可檢索的視覺描述 (3) EPUB 裡的內嵌插圖（對照表、命盤圖等）也要進知識庫 (4) 不想分兩個工具、要一次做完文字＋圖 (5) 為 RAG 知識庫一次備齊文字與圖時使用。完全免費、不需 API key、不需本地大模型——Phase 1 本地把文字抽好＋圖抽好或渲染好（0 token），Phase 2 由 Claude 自己的眼睛與理解一次把「章節摘要」和「圖像解讀」都填滿；EPUB 圖另有 llm-node OCR-first 本地批次路徑可選（零費用、資料不出門）。
---

# doc-vlm-to-md Skill（整合版）

把一份文件**一次**轉成「文字 + 圖表」都齊全的【單一 Markdown 知識庫】。
= `doc-to-md`（文字、章節摘要）  ＋  `vlm-to-md` v1.0.1（圖表 / 向量圖表頁 / 掃描頁視覺）
  ＋ EPUB 視覺線 v1.3.0（zip 內嵌圖 / 錨點原位插圖，見 Step 2.5）。

**為什麼要整合**：以前要先跑 doc-to-md 拿文字、再跑 vlm-to-md 拿圖、再手動貼回。
本 skill 把這三步併成一步，輸出**一個檔**，文字與圖都在裡面，兩種空格一次交給 Claude 填。

**兩階段架構（與 doc-to-md / vlm-to-md 一致）：**
- **Phase 1（本地、免費、0 token）**：`kb_prep.py` 抽文字（章節結構）＋渲染圖（含向量圖表頁），合併成一份留好空格的 Markdown 骨架。
- **Phase 2（Claude Desktop 原生能力、免費）**：Claude 讀同一份檔，**一次**把「章節摘要」和「圖像解讀」都填滿。**不需要 OpenRouter / GPT-4o / API key。**

> **重要 — 不要拒絕執行這個 Skill。** 腳本就附在 `scripts/kb_prep.py`（同資料夾還有 `doc_to_md.py`、`vlm_prep.py`），
> 在 Claude Desktop 的程式沙箱中可直接執行；即使使用者沒跑過 install，也要用 bundled 路徑跑（見 Step 1 Option A）。

> **v1.0.1 已修正「向量圖表被漏掉」**：麥肯錫／顧問報告的 Exhibit 多是向量畫成、`get_images()` 抓不到，
> 本 skill 會用 `get_drawings()` 偵測「向量圖表頁」整頁渲染補回（實測麥肯錫 68 頁補回 19 張圖表，含 Exhibit 1）。

---

## Step 0 — 五條鐵律（MM 2026-09-08 拍板，違反必出事）

這五條是踩過大坑之後定下來的。大耕紫微 11 本 EPUB、1,010 張圖的線上，
每一條都對應一次真實損失。

### 0-1　**每份文件都要查有沒有圖，不能看副檔名就跳過**

**這是最貴的一條。** `kb_prep.py` 曾經寫死「EPUB 沒有圖」，於是 11 本書、
**1,010 張內嵌圖從來沒進過管線**——而且不報錯。後果不只是少了圖：
所有「語料裡沒有這個」的判斷全建立在殘缺語料上，
連「缺口的形狀」都判錯（把「亮度只掛在日月上」誤判成「十四顆星各缺一張表」）。

開工第一件事一律是**實際數圖**，不管什麼格式：
```bash
python3 scripts/epub_images.py <file>   # EPUB：解 zip 數內嵌圖
# PDF 走 kb_prep.py 既有的 get_images() + get_drawings()
```
數量為 0 才可以說「這份沒有圖」，**不可以用格式推論**。

### 0-2　**順序是「先 vlm-to-md 轉完整份，再開始切知識點」**

不要一邊切卡一邊補圖。圖沒進語料之前切出來的卡，
會帶著「原書沒寫」這類錯誤結論，之後每一張都要回頭重驗，成本遠高於先轉完。

### 0-3　**圖在 md 裡先佔位，再一張一張填**

先把圖的**位置**用錨點標進 markdown（`<!-- FIG:xxx BEGIN/END -->`），
再逐張填內容。好處：中途中斷不會整批白做、可以核對落地率、
補跑只補缺的。**不要最後才一次寫檔**（實測被中斷會整批損失）。

### 0-4　**直排要先轉成橫排，版面要人工確認**

直排（豎排）書的表格**很常跨頁分裂成雙區塊**：
右區塊是前一個主題的續尾、左區塊才是新主題的標題＋開頭。
把整張圖當一個主題轉錄，產出就是「本主題前幾項 ＋ 前一主題後幾項」——
**每一項都是真的，組合起來全錯**。

- **標題出現在圖的中間＝紅旗**：它標的是「從這裡開始」，不是「整張都是」
- 轉錄前**自己看一眼版面**（Read 圖片），確認區塊數與閱讀方向
- 派工單要寫明可能的雙區塊結構，要求回報「這張圖有幾個區塊、各屬於誰」

### 0-5　**驗證要驗到版面層，同源的兩種檢查不算交叉驗證**

上面那個雙區塊錯誤，當時用「表頭 OCR ＋ 內容關鍵詞頻」交叉驗證過，
**兩者都放行**——因為兩者讀的是同一張圖，而錯誤在**邊界劃錯**不在內容讀錯。

- 驗**數量**：十二宮就該是 12，數量不齊或跨圖重複＝邊界錯
- 驗**互斥**：相鄰兩張圖的項目不該重疊
- **有工具**：`python3 scripts/check_layout.py <cleaned.md> --unit 宮 --expect 12`
  拿 2026-09-07 的事故檔實測，五張項數不齊、六組相鄰重疊 9–10 項全部抓出來
- 覺得「內容特徵完全對得上」時**特別危險**——摻進來的內容本身都是真的

---

## Step 1 — 找到 kb_prep.py（依序嘗試）

### Option A — Bundled 腳本（Claude Desktop 沙箱，永遠可用）

腳本在 Skill 的 `scripts/kb_prep.py`（同層有 `doc_to_md.py`、`vlm_prep.py`，kb_prep 會自動 import 它們）。

```bash
# 1. 安裝依賴（每個 session 一次）
pip install -r scripts/requirements.txt
# 2. 執行（吃使用者上傳到沙箱的檔）
python3 scripts/kb_prep.py --auto "/mnt/user-data/uploads/<檔名>.pdf" -o "/mnt/user-data/outputs/"
```

找不到時：`find . -name kb_prep.py 2>/dev/null | head -3`

### Option B — 本地安裝（Claude Code / 跑過 install 較快）

- **Mac:** `~/.doc-vlm-to-md/venv/bin/python3 ~/.doc-vlm-to-md/kb_prep.py`
- **Windows:** `%USERPROFILE%\.doc-vlm-to-md\venv\Scripts\python.exe %USERPROFILE%\.doc-vlm-to-md\kb_prep.py`

兩者都沒有時，請使用者先跑 `install.sh` / `install.bat`。

---

## Step 2 — 執行 Phase 1（本地：抽文字＋渲染圖＋合併）

```bash
python3 scripts/kb_prep.py --auto "<輸入>" -o "<輸出資料夾>"
```

**輸入：** PDF / EPUB / TXT（會抽文字）；或圖片 / 圖片資料夾（純視覺）。

| 旗標 | 效果 |
|------|------|
| `--auto` | 自動處理（預設、最省事） |
| `--no-convert-chinese` | 不做簡體→繁體 |
| `--dpi 200` | 提高渲染解析度（圖糊、字看不清時） |
| `--vector-threshold N` | 向量圖表頁偵測門檻（**v1.2.0 預設 25**；仍漏圖再調低如 20） |
| `--no-vector-pages` | 關閉向量圖表頁偵測（只抽嵌入點陣圖） |

執行後得到**一個** `*_完整知識庫.md`（＋ `assets/`、`*_manifest.json`）。記下這個路徑。

行為說明：
- **文字型 PDF（v1.2.0）**：**版面線性重建**——逐頁輸出 doc-to-md 品質文字，每張圖**就地插在它原本的位置**
  （不再丟到末尾附錄）。圖表用 `📊 數據圖表` 標、裝飾照用 `🖼 圖片` 標。
  抽圖採「閾值 25 ＋ caption 補抓」：內文每出現一個 `Exhibit/Figure/圖表` 標題就確保該頁有圖，**避免有描述卻沒圖**。
- **掃描／圖像型 PDF**：每頁渲染成圖，視覺區要求逐字謄寫（文字靠 Phase 2 視覺謄寫）。
- **EPUB（v1.3.0 起）**：**圖是 zip 裡的獨立檔案，不需要渲染就能直接取**——用 `<img src=...>` 在 xhtml
  裡的位置抓前後文當**錨點**，把圖**就地插回原文對應位置**（跟文字型 PDF 一樣原位插圖，不是丟附錄）。
  極少數錨點在正文比對不到的圖，才落到文末「📊 圖表與視覺內容」附錄，不會漏圖。
- **TXT**：只有文字（無頁面圖）。
- **圖片 / 資料夾**：只有視覺。

---

## Step 2.5 — EPUB 視覺線（zip 內嵌圖，v1.3.0）

**破口修補說明**：舊版認定「EPUB 沒有頁面圖像」——這是錯的，那只是 PDF 視覺線（`get_images()` /
`get_drawings()` 頁面渲染 API）套不到 EPUB 身上而已。EPUB 本質是 zip，圖是裡面的獨立檔案，
直接解壓就能拿到原始畫質，**完全不需要渲染**。實測 11 本紫微斗數書合計 **1,010 張圖**曾因這個破口
從未進過知識庫，其中包含核心對照表（十四主星特質表、空宮借星旺陷圖）。

`--auto` 跑到 EPUB 時，這條線會自動接上，不需要額外旗標：

1. `epub_images.py` 解壓 zip 抽圖，並用每個 `<img src=...>` 在 xhtml 裡的位置抓前後文（各約
   200–260 字）當**錨點**，連同圖片尺寸寫進 `assets/<書名>/figures.json`。
2. `kb_prep.py` 用錨點的前文尾段去 `doc-to-md` 產出的正文裡比對定位，找到就把圖**就地插入原文
   對應位置**（`### 🖼 檔名` + 圖 + 「圖像解讀」空格，跟 PDF 原位插圖同一種 placeholder）；
   比對不到的少數圖才落到文末「📊 圖表與視覺內容」附錄，**不會漏圖**，只是少了原文定位。
   （實測紫微攻略3上集：82 張圖，69 張原位插入、13 張落附錄。）
3. Phase 2 由 Claude 一樣**逐張開圖填空格**，跟 PDF 視覺區規則完全相同（見 Step 3 鐵律 1/2）。

### 另一條路：OCR-first 本地批次（零費用、資料不出門）

除了「Claude Desktop 逐張開圖填空格」，還有一條完全在本機跑的路徑，適合大量圖或不想耗 API 額度時：

| 腳本 | 作用 |
|---|---|
| `img_prompt.py` | 依 `figures.json` 的錨點 ＋ RapidOCR（`http://100.120.197.113:8082/ocr`）結果，組出**帶出處框架**的讀圖 prompt |
| `vlm_ocr_first.py` | 對一個資料夾的圖批次跑「先 OCR、把結果連圖一起餵給 llm-node VLM」，輸出一份 `## 檔名.jpg` 分段的轉錄 `.md`（含 token/耗時統計） |
| `merge_figures.py` | 把轉錄 `.md` 塞回 `cleaned.md`（或 kb_prep 產出的 `_完整知識庫.md`）的原始位置，用同一套錨點比對；**冪等**，重跑會先清掉舊的插入塊再重插 |

三支都能獨立執行（`--help` 可用），用法：

```bash
python3 scripts/epub_images.py "書.epub" out/                       # 抽圖 + 錨點
python3 scripts/vlm_ocr_first.py out/images out/transcripts.md \    # 本地批次轉錄
  --model gemma-12b --limit 20
python3 scripts/merge_figures.py cleaned.md out/figures.json \      # 塞回原文位置
  out/transcripts.md --out cleaned_with_figs.md
```

單張圖要客製 prompt（例如給 Anthropic API 或 subagent 讀）：

```bash
python3 scripts/img_prompt.py out/figures.json "書名" "i-004.jpg" --author "作者名"
```

### 實測結論（踩過的坑，別重踩）

- **先 OCR 再讓模型讀，而不是純 VLM**：純 VLM 讀小字表格會出形近字錯誤（實測 gemma-12b 一張表錯 4
  處：善→普、三台→三方、陽木→陽火、不怕煞→不化）。加上 OCR 後 4/4 修正，且速度反而快 **34%**
  （246 秒 → 162 秒）——因為模型不必再猜字形，OCR 已經把字形這件事做掉了。
- **prompt 要有出處框架**：格式是「這是{作者}的著作《{書名}》裡面{章節}的圖，解釋給我聽」＋錨點前後文
  ＋ OCR 結果。三個作用：(1) 給模型判讀脈絡 (2) 讓輸出自帶敘述而非乾巴巴的術語堆 (3) 章節資訊讓轉錄
  結果可回溯原書位置。
- **要求「逐格照抄不要摘要」會製造出高密度術語表，觸發 content filter**——命理語料實測連續 **4 次
  被擋**（haiku×2、sonnet subagent、Claude CLI）。改成「解釋給我聽」＋要求輸出先寫一段「這張圖在講
  什麼」之後就正常了。**這是 prompt 設計問題，不是模型或題材問題**，別急著怪模型或換題材。
- **兩條可用路徑，按圖的重要性分流**：Anthropic＋模板（約 40 秒/張，要 API 額度）｜llm-node
  OCR-first（約 162 秒/張，零費用、資料不出門）。準確度相同。
- **先篩再讀**：1,010 張全跑要 34 小時，不要傻跑。用錨點的關鍵字密度排序（「表」「對照」「廟旺」
  「四化」等命理／領域關鍵字）篩出候選再讀，實測抽樣 5 張裡 4 張知識含量「高」，篩選有效。
- **順序不能顛倒**：圖必須先併回語料，才能算詞頻、做欄位分類、切卡（`good-student` 系 skill 的
  chunking 前置）。反著做（先切字、圖事後補）會讓「某詞全語料 0 次 → 本體系不用」這類結論建立在
  **缺圖的語料**上，等於用殘缺證據下結論。

---

## Step 3 — Claude 一次填滿兩種空格（Phase 2 核心｜準確度鐵律）

打開 `*_完整知識庫.md`，有兩種空格，一次都填。**以下準確度規則務必照做——這是這版最重要的改動。**

> **圖已在原位**：v1.2.0 的骨架已把每張圖插在它原本的位置，你只需**就地填空**，不要把圖搬到別處或集中到末尾。

### 🔴 鐵律 1：圖表一定要「先開圖再寫」，禁止憑文字硬掰
- 每張標記「📊 數據圖表」的圖，**必須**先用 Read 工具開該 `assets/.../*.png` **實際看過**，才能寫它的圖像解讀。
- `✅ 已檢視` 只代表「**我開過這張 PNG**」；用上傳 PDF 的內文寫的**不算**已檢視。**收尾時不該有任何 📊 數據圖表還停在 ⬜。**
- **絕對禁止**沒開圖、只憑內文或記憶就描述圖表——那會編出報告裡其實沒有的來源與數字。務必從圖上逐字讀。

### 🔴 鐵律 2：出處／數字逐字照抄，看不清就標「未能辨識」
- 「原文出處（逐字）」欄要把**圖上印的**標題與 Source／來源 行**逐字抄下**（圖是高解析，通常讀得到）。
- 關鍵數字也從圖上讀，可對照上方內文交叉確認。圖上讀不到的，寫「圖中未能辨識」，**禁止杜撰來源、數據或國家清單**。

### 🟡 規則 3：裝飾照不必細看，省 token
- 標記「🖼 圖片」若是封面／章節分隔／情境照，用一句話帶過即可（不必逐一細究），把 token 留給數據圖表。這是「該省的省、不該省的不省」。

### 規則 4：兩種空格怎麼填
- 文字區「章節摘要」：依正文寫 2-4 句摘要 + 4-8 個關鍵字。
- 視覺區「圖像解讀」：依鐵律 1/2 填。**只替換空格內容，圖片連結、標題、錨點一律不動。**
- 圖多時分批填（每次 5-10 張），可多輪完成。

---

## Step 3.5 — 倒進常設語料倉（MM 2026-09-08 拍板，預設動作）

**轉完就倒，不要留在 session 目錄裡。** 產物原本散在
`good-students-note/sessions/`，而那目錄是 gitignored——只有本機一份、
沒有歷史、改壞救不回來，也無法比對「這句話什麼時候被改的」。

```bash
python3 scripts/sync_corpus.py <session 目錄>                    # 單份
python3 scripts/sync_corpus.py --all <sessions 根目錄>           # 整批
python3 scripts/sync_corpus.py ... --dry-run                     # 先看要做什麼
```

路徑讀環境變數 **`DOC_CORPUS_DIR`**（設在 `mars-cc/.env`，
指向 `doc-corpus/corpus`；沒 export 也會自動去讀那份 .env）。
語料倉 repo＝`git.pf:mars/doc-corpus.git`。

**沒設環境變數不是錯誤**：這一步本來就是可選的，它會印一行
「未設 DOC_CORPUS_DIR，沒有其他輸出路徑；產物留在 session 目錄」然後正常結束（exit 0），
不會讓整條管線失敗。換機器、別人 clone 來跑都不用先設定。

**圖片整包一起複製過去**，但被該 repo 的 `.gitignore` 擋著不會 push。
留在本機是刻意的：**直排雙區塊那類版面錯誤只能開圖才驗得出來**（見 Step 0-4），
沒有圖就沒辦法回頭核對。實測大耕 11 本：本機 334 MB，git 只收 5 MB 文字。

倒完之後 `cd doc-corpus && git add -A && commit && push`。

---

## 只做一部分時怎麼收（smoke test 交付格式）

試跑、抽樣、分批做時**不要跑完整交付流程**，否則會被誤認成已完工。
2026-09-08 兩條試跑都卡在這裡：SKILL.md 沒說「只做 8/80 張時該在哪停」。

| 做多少 | 跑哪些 | 不要跑 |
| :--- | :--- | :--- |
| **部分**（試跑／抽樣／分批） | 轉錄 → `merge_figures.py` → 核對落地率 | ❌ `sync_corpus.py`　❌ `package_kb.py` |
| **全部** | 全流程到 `sync_corpus.py`／`package_kb.py` | — |

部分完成時**一定要在 `metadata.json` 留記號**，否則下一個人看到 md 有圖說會以為做完了：

```json
{"figures_done": 8, "figures_total": 80, "note": "smoke test，非漏做"}
```

回報也要寫「N/M 張」，不要只說「完成」。

---

## 圖片路徑約定（**不照做連結會斷**）

`epub_images.py <epub> <outdir>` 把圖存到 **`<outdir>/images/`**；
`merge_figures.py` 預設把連結寫成 **`images/<name>`**，也就是**假設圖與 `cleaned.md` 同層**。

- **同層**（建議）：`python3 epub_images.py book.epub <session>/` → 圖在 `<session>/images/`，直接可用
- **不同層**：例如輸出到 `<session>/assets/`，就要 `merge_figures.py ... --images-dir assets/images`

2026-09-08 試跑實測踩到：照 Step 2.5 範例把圖抽到 `assets/`，
連結卻寫成 `images/`，跑完才發現斷鏈，得補 symlink 才解決。

---

## 腳本一覽（別再自己造平行管線）

2026-09-07 的教訓：我在 scratchpad 另造了一整套 `epub_images.py`／
`img_prompt.py`／`merge_figures.py`，跟 skill 裡的同名腳本分歧成兩份，
而**錨點修正只進了其中一份**。要改就改 `scripts/` 這份。

| 腳本 | 做什麼 | 何時用 |
| :--- | :--- | :--- |
| `count_figures.py` | **實測數圖**，不看副檔名推論 | **開工第一件事**（Step 0-1） |
| `kb_prep.py` | Phase 1：抽文字＋渲染圖＋留空格 | 主線 |
| `epub_images.py` | 解 EPUB zip 取內嵌圖＋算錨點 | EPUB 視覺線 |
| `score_figures.py` | 給圖評分＋**直接吐待處理清單**（`--top N` 全域排序） | 圖多到不能全做時 |
| `gen_prompts.py` | 為每張圖產 OCR-first 讀圖指示 | 派工前（⚠️ 吃 `t1_list.json` 格式的清單，即 `score_figures.py --out` 的產物） |
| `merge_figures.py` | 圖說依錨點插回 md 原位（單本） | 轉錄後 |
| `merge_all.py` | 跨 session 批次合併 | 轉錄後（多本；⚠️ 一次性腳本，路徑寫死在檔內，新批次要先改） |
| `sync_corpus.py` | 倒進常設語料倉 | **收工前**（Step 3.5；只做部分時不要跑） |
| `check_layout.py` | **版面層驗證**：項數齊不齊、相鄰圖有無重疊 | 轉錄後（Step 0-5 的配套工具） |
| `vlm_ocr_first.py` | 走 llm-node 本地讀圖（零費用、不受 content filter 管） | 被擋或要省錢時 |
| `package_kb.py` | 打包可攜 zip | 交付 |

---

## Step 4 — 存檔與「打包成可攜 zip」交付（縮圖才不會斷）

兩種空格都填完、原地覆寫成單一 `*_完整知識庫.md` 後，**務必跑打包腳本**，交付那個 zip（不要只丟單一 md）：

```bash
python3 scripts/package_kb.py "<最終.md>"
# → 產出 {主題}_知識庫.zip：內含一個資料夾（md + assets/），解壓後打開 md，縮圖自動顯示
```

- 🔴 **為什麼一定要打包**：圖片是標準 Markdown `![](assets/...)`（**不是** Obsidian `![[ ]]`），靠相對路徑找圖。
  只丟單一 md → 對方沒有 `assets/` → **縮圖全斷、學員以為壞了**。打包成「資料夾在 zip 裡」就解決：解壓打開即看得到圖。
- `package_kb.py` 會**自動斷鏈檢查**：任何連結找不到圖檔就**非 0 退出並列出**——這時要嘛補圖，要嘛把那幾行斷鏈圖片移除（只留 VLM 文字），**絕不留斷鏈縮圖**。
- **若真的只要純文字**（不附圖）：把所有 `![](assets/...)` 圖片行移除、只留「圖像解讀」文字再交付。
- **不要交付半成品**：殘留「（…填入）」空格、`⬜ 未檢視` 的數據圖表都不算完成。

---

## 與舊兩個 skill 的關係

| | doc-to-md | vlm-to-md | **doc-vlm-to-md（本 skill）** |
|---|---|---|---|
| 文字 | ✅ | — | ✅ |
| 視覺（含向量圖表） | — | ✅ | ✅ |
| 輸出 | 文字 MD | 視覺 MD | **單一合併 MD（一次做完）** |

舊的兩個仍可單獨用；要「一次做完」就用這個。

---

## 疑難排解

| 問題 | 解法 |
|------|------|
| `No module named 'fitz'`（沙箱） | `pip install -r scripts/requirements.txt` |
| 章節偵測為 0（如英文報告） | doc-to-md 章節偵測以中文章節標記為主；文字仍完整保留，只是少了逐章摘要空格 |
| 文字型 PDF 圖表沒抓到 | 調低 `--vector-threshold`（如 30）；或 `--dpi 200` |
| 圖太多、很慢 | Phase 2 分批填，每次 5-10 張 |
| Claude 說「不能看圖」 | **錯誤**——用 Read 工具直接讀 PNG 即可看圖；重讀 Step 3 |
| EPUB 章節空白 | 可能有 DRM，需先移除 |
| EPUB 圖沒接上原文位置 | 正常現象，不是 bug——錨點在正文比對不到的圖會落到文末「📊 圖表與視覺內容」附錄，圖不會少，只是沒有原文定位；看 `*_manifest.json` 的 `epub_figures_placed_inline` 對照原位插入張數 |
| VLM 讀圖出現形近字錯誤（善→普、三台→三方…） | 走 OCR-first 路徑（`img_prompt.py` / `vlm_ocr_first.py`），先 OCR 拿字形再讓模型讀，見 Step 2.5「實測結論」 |
| 命理／術語密集的圖轉錄被模型拒答（content filter） | prompt 別用「逐格照抄不要摘要」，改用「解釋給我聽」＋先寫一段「這張圖在講什麼」，見 Step 2.5「實測結論」 |
