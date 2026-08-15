# 設計文件：驗收後自動出封面圖＋好學生筆記圖（#570）

> 2026-07-29 · scope＝設計文件＋筆記圖 prototype（卡 #570 comment 6566）。
> 本輪**不呼叫任何外部或花錢的圖生成 API**；封面引擎選定、尺寸模板、觸發機制三案皆**等 MM 拍板**。
> 上游脈絡：CLAUDE.md 原則 10「集數文案跑 cloud 模型，時機＝MM 驗收該集 final_cut 之後」——本設計把「文案之後」延伸到「文案＋視覺資產之後」。

---

## 1. 各平台集數封面規格（2026-07 查證）

| 平台 | 集數封面（episode art） | 格式限制 | 來源 |
|---|---|---|---|
| **Firstory**（託管端，上傳點） | 正方形、**≥ 1400×1400 px**；每集可各自設定，未設定 fallback 節目封面 | 一般圖檔（JPEG/PNG） | [Firstory Help: Setting the episode cover](https://firstory.crisp.help/en/article/setting-the-episode-cover-pxeunv/) |
| **Spotify** | 正方形 1:1，最低 **640×640**，建議 **3000×3000** | JPEG/PNG、RGB | [Spotify for Creators: dos and don'ts of show art](https://creators.spotify.com/resources/create/dos-donts-showart)、[Transistor: specs for podcast cover art](https://support.transistor.fm/en/article/specs-for-podcast-cover-art-on-apple-podcasts-spotify-etc-1dyjud8/) |
| **Apple Podcasts** | 節目封面 **1400×1400–3000×3000**（建議 3000）；**單集封面走 RSS/託管商上傳**（Firstory 說明頁註記：Apple 顯示單集圖以嵌入音檔的 artwork 為準，是各平台中的例外） | JPEG/PNG、RGB、72dpi；聚合商實務建議 <512KB | [Apple Podcasts for Creators: Episode Art](https://podcasters.apple.com/support/5516-episode-art-template)、[RSS.com: Episode Art Formatting Requirements](https://help.rss.com/en/support/solutions/articles/44000492924-episode-art-formatting-requirements)、[PRX: feed requirements](https://help.prx.org/hc/en-us/articles/360023748473-Overview-of-feed-requirements) |

**收斂結論（待 MM 拍板的「尺寸模板」推薦案）**：單一產物 **3000×3000 px、PNG、RGB、壓到 <500KB**，一張圖同時滿足 Firstory/Spotify/Apple；小字在 48px 縮圖下不可讀，模板設計以「大字集數＋一句題眼」為主。若要 Apple 也顯示單集圖，需另做「嵌入 mp3 artwork」一步（ffmpeg 可做，確定性、零成本，可併入 render_cut 下游）。

## 2. 封面圖生成引擎選項比較（**等 MM 拍板**）

前提盤點：**LM Studio（本地）只有 LLM/VLM，無圖像生成**——本地線出局（除非另架 SD/ComfyUI，維運成本不划算）。

| 選項 | 成本 | 品質預期 | 自動化難易 | 備註 |
|---|---|---|---|---|
| **A. Antigravity / Gemini CLI 影像工具**（＝repo 原則 5 的 Engine C，Nano Banana） | OAuth login 額度內，**零 API 費** | 高（banana 對風格指令服從度好） | **中偏難**：headless `agy -p` 通道存在（describe_images.py 先例），但生圖屬 MCP 互動工具；2026-05-31 教訓＝逐頁自動 banana 會重複第 1 頁。**單張封面**非逐頁序列，風險比筆記圖低，可實驗 | CLAUDE.md Auth 雙軌表已載明 Claude Code 無影像通道，此線必須換 host 執行 |
| **B. Cloudflare Workers AI 圖生成**（flux-1-schnell / SDXL 系） | 免費額度後按 neurons 計費（單張約 <US$0.01，趨近零） | 中：構圖 OK，**CJK 文字必爛**（模型畫不出正確中文字） | **易**：MCP/REST 全可腳本化，最適合 unattended | 文字需後製疊字才能用 |
| **C. Gemini API `gemini-2.5-flash-image`（API key 直打）** | 按 token 計費（每張約 US$0.04） | 高 | 易（純 REST） | 與 repo「CLI host 不打 API key」的原則 5 精神衝突，需明確豁免才可用 |
| **D. 確定性模板合成（零 AI）**：節目底圖＋每集大字標題/集數，用既有 Playwright（md_to_a4_png 同技術）或 PIL 疊字 | **零** | 中高：品牌一致性最好、**中文字 100% 正確**、縮圖可讀性可控 | **最易**：純腳本、可重跑、可稽核（符合原則 6「確定性工作用工具」） | 缺「每集不同的插畫感」；可與 A/B 混用（AI 只生無字背景，字由模板疊） |

**推薦（等 MM 拍板）**：**D 為骨、B 為皮**——確定性模板管版式與文字（零成本、零踩雷），背景插圖可選配 Cloudflare Workers AI 生無字底圖（成本趨近零、全自動）；A（Engine C）保留為高品質手動精修通道。**不建議 C**（與原則 5 衝突）。

## 3. 好學生筆記圖：既有工具盤點＋流程設計

### 既有工具能不能直接產出？——**能**（本輪已實測）

- **`scripts/md_to_a4_png.py`**：md → A4 白底 PNG（Playwright 真 DOM 渲染，零遺漏），且支援 `--annotations ann.json` 用**確定性 CSS** 疊好學生筆記六色註解（黃螢光/藍關鍵詞/紅!?/橘綠便利貼/💡洞察框），不靠影像模型、無選錯圖風險。
- **`scripts/frames/compose.py`**：產 Obsidian 逐字稿/筆記骨架（md 層），是筆記**內容**的上游，不是圖像工具；與本線的關係＝它產的 md 也能餵 md_to_a4_png。
- **`scripts/image_notes_session.py`**（Stage 1/2）：banana 影像版逐頁流程，屬 Engine C 手動線，本設計不依賴。

### 流程設計：文案素材 → 筆記 md → PNG（全確定性、零 LLM 圖生成）

```
sessions/<slug>/copy_material.md      （既有：文案素材，MM 驗收 final_cut 後產出線的輸入）
        │  + 註解 JSON（keyterms/highlights/marks/sidenotes/insight）
        │    ↑ 這一步是「判斷」：由對話 agent 從 copy_material 挑重點寫 ann.json（原則 5 marker 精神）
        ▼
python3 scripts/md_to_a4_png.py copy_material.md <out> --annotations ann.json
        ▼
sessions/<slug>/ep<NN>_notes_preview.png   （A4 直式筆記圖，社群/shownotes 用）
```

已知限制（本輪實測踩到）：A4 分頁是**幾何切割**，內容略超一頁時 insight 框會被攔腰切在頁界上；註解文字長度需控制或後續在 script 加「單頁模式」（頁高改 scrollHeight、不分頁）——**本輪未動共用 script**，列為候選小修。另：字體/marked 走 CDN，離線環境跑不了。

### EP15 prototype（已產出，sessions 為 gitignored、不 commit）

- `sessions/2026-07-27_EP15-前任/ep15_notes_preview.png`（一頁 A4，六類註解全上）
- `sessions/2026-07-27_EP15-前任/ep15_notes_annotations.json`（本次的註解 JSON，供重跑）
- 環境註：repo 端 python 未裝 Playwright（`.venv-audio` 也沒有），本輪用 session 隔離 venv 跑；若要進 pipeline，建議 `requirements-notes.txt` 或併入既有 venv 策略（等實作輪決定）。

## 4. 驗收觸發機制設計（**等 MM 拍板**）

「MM 驗收 final_cut」這個動作要長成什麼樣，才能觸發文案→封面→筆記圖產出線：

| 候選 | 做法 | 優點 | 缺點 |
|---|---|---|---|
| A. Lifov 卡留言關鍵字 | MM 在該集卡片 comment「驗收 OK」，agent/cron 輪詢 `todo activity` | MM 已有的習慣動作；有時間戳與紀錄 | 跨系統依賴（Lifov API）；輪詢才看得到；關鍵字要約定嚴格否則誤觸 |
| B. Lifov 搬欄動作 | 卡片 review:wait-mm → wait-deploy 的 stage 轉移＝驗收 | 對齊既有 Review 三 stage 慣例 | 一張卡對多集時對不上「哪一集」；同樣要輪詢外部系統 |
| **C. session 檔案 marker（推薦）** | MM 聽完 final_cut 後在 session 目錄放 `.final_cut_approved`（或 agent 代 MM 口頭確認後放，內容記時間與確認語） | **對齊 repo 既有 marker file 契約**（原則 5 的 `.{stage}_pending.json` 同一套語彙）；零外部依賴、`ls` 即可判斷、gate script 可硬擋「未驗收不得出資產」；per-session 天然對到集數 | MM 要多一個動作（可由對話 agent 代放，MM 只要說一句「EP15 驗收」）|

**推薦理由**：這條 pipeline 的真相源都在 `sessions/<slug>/`（cutplan 勾選＝剪輯決定也是檔案），驗收狀態放同一目錄才能被 `prepublish` 型 gate 確定性檢查；Lifov 留言可保留為**通知層**（人看的），不當機器觸發源。觸發後序列：`.final_cut_approved` 出現 → 集數文案（cloud 模型，既有規劃）→ 封面圖（引擎待拍板）→ 筆記圖（§3 流程）。

---

## 等 MM 拍板清單

1. **封面引擎**：D（確定性模板）為骨＋B（Cloudflare Workers AI 無字底圖）為皮，A（Engine C/banana）留手動精修——採不採？
2. **尺寸模板**：單一 3000×3000 PNG（<500KB）通吃三平台；Apple 單集圖另加「ffmpeg 嵌 mp3 artwork」一步——採不採？
3. **驗收觸發**：session 目錄 `.final_cut_approved` marker 為機器觸發源、Lifov 留言僅通知層——採不採？

By visual-design（#570）.
