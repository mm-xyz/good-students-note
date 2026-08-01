# ADR 0009 — 知識點線：概念級 chunking＋記憶鉤，與零省略產線平行

- 日期：2026-08-01
- 狀態：Accepted（需求方模式：AI 決、MM 事後否決）
- 相關：CLAUDE.md 原則 12（輸入轉接器）、§4 好學生筆記規範、mars-cc `astro-chunking` skill

## Context

2026-07-31 用 astro-chunking 把白瑜占星課切成 523 張概念檔（MarsDots/astro/白瑜/）。
MM 驗收回饋：**切法是對的（domain 結構＝chunk 邊界、一概念一檔），但每張只是內容
轉述，太籠統、沒有記憶點、看過就忘。**

同時 MM 拍板方向：chunking 能力應該進 good-students-note，讓本工具能把
**各種形式的長文本**處理成「一個個乾淨、好記憶的知識點」——而不是留在
mars-cc 的單一領域 skill 裡。

本 repo 既有的解藥正好互補：好學生筆記（Step 4）的視角置入（類比／應用／連結）
就是記憶點的來源，但 Step 4 是整段式產物，沒有概念級切分。

## Decision

1. **新開「知識點線」**，入口為 `/good-student` skill
   （`.claude/skills/good-student/SKILL.md`，另 symlink 進 `~/.claude/skills/` 全域可用）。
2. **接口位置**：吃 `cleaned.md`（或任意乾淨長文本），坐在理解層、與 Step 3/4 平行。
   前段輸入一律走原則 12 的轉接器（`session.py new`），知識點線不自己碰
   ASR／抽取。
3. **產品性質＝蒸餾，與零省略平行不取代**：零省略鐵律只管 Step 2/3/4；
   知識點是 300–800 字改寫濃縮（`confidence: distilled`），兩產線各自驗收、
   絕不混用 checklist。
4. **記憶點三件套**（對「看過就忘」的直接解法）：每張知識點在忠實 prose 之外
   強制帶 🎯 區塊——視角類比＋一句鉤＋它回答的問題；作者原生比喻／口訣原話保留。
5. **啟動儀式＋試切 gate**（MM 明訂）：每次啟動必先與使用者討論
   切分軸／視角／輸出位置——**視角每次重談，不沿用上次**；
   auto mode 也必須先試切 5 張、使用者驗收 GO 後才可批量。
6. **深度落點**（MM 同日補訂）：內容要「有點難但不會太難」——以啟動儀式
   問到的使用者現有程度為基準，每張卡讓讀者恰好跨**一步**（一次「原來如此」）。
   太淺＝不看材料也寫得出（→ 挖機制/條件/例外/竅門，沒料就併卡）；
   太深＝前置概念沒搭橋（→ 一句話帶過＋`[[連結]]`，或拆卡）。
   深度基準必須逐字進派工 prompt，否則平行 agent 深淺不一。
7. **檔名雙語＋總覽 canvas＋RAG 契約**（MM 同日補訂）：
   檔名＝`<slug>_<繁中title>.md`（slug 保受控 ASCII id、繁中利 Obsidian 閱讀）；
   輸出根建 `_map.canvas` 按軸排矩陣（`scripts/build_canvas.py` 確定性生成，
   0 token；試切 gate 先出「骨架＋佔位＋5 張實卡」版**當驗收介面**，
   批量後重跑補實）；知識庫之後是 RAG 基礎——embedding 單位＝首段＋正文，
   🎯 區塊/「相關：」行格式固定可確定性剝離，受控欄位＝檢索 filter。
8. 通用化自 astro-chunking：frontmatter 受控欄位、slug 慣例、parent-child
   （source 指回原文）、派工守則（格式逐字鎖、sonnet 不派 haiku）全數沿用；
   受控詞彙從寫死占星改為啟動儀式時按語料定義。

## Consequences

- ✅ 任何輸入（音檔／影片／PDF／EPUB／TXT／貼上的文本）都能一路走到
  「RAG-ready 且人記得住」的知識庫；占星只是第一個語料。
- ✅ 「太籠統」有了可操作判準（換 title 讀起來像別的概念＝重寫）與
  自測法（蓋內文看鉤子能否複述）。
- ⚠️ 蒸餾線與零省略線並存，agent 有混用風險——SKILL.md 開頭以「定位」段落
  明文隔離，違規樣態寫死。
- 既有 523 張白瑜概念檔為舊版產物（無 🎯 區塊）；是否用新線重跑由 MM 另行決定，
  不在本 ADR 範圍。
- Web studio 端（Step 4 之後加知識點 step）暫不實作，待 CLI 線驗證後再議。
