# 驗收後自動出文案 — 全流程自動化設計（卡 #571）

> 2026-07-29 · branch feat/podcast-cut。範圍：MM 驗收 final_cut 之後，自動組裝 prompt、
> 平行派兩個 cloud 引擎出文案草稿、比稿收斂交 MM 選。
> **紅線先講：發布永遠是 MM 手動。這條線的終點＝草稿放進 session，僅此而已。**

## 1. 全流程

```
MM 驗收 final_cut（觸發訊號，見 §2）
        │
        ▼
scripts/audio/copy_prompt_build.py --session sessions/<slug> --ep <n>
  （已存在、零 LLM：shared-material 模板 + copy_material.md + cutplan 保留段
   → sessions/<slug>/copy_prompt.md，時間軸經 cut_map.json 換算成 final-cut 時間）
        │
        ├──────────────┬──────────────┐
        ▼              ▼              │ 平行、互不阻塞
  agy 線            codex 線          │
  agy -p "$(cat copy_prompt.md)" \    │
    --model "Gemini 3.1 Pro (High)"   │
                    codex exec 同 prompt
        │              │
        ▼              ▼
  copy_draft_agy.md   copy_draft_codex.md   ← 都存 sessions/<slug>/，
  （檔頭 `> engine: <cli 名+版本> · model: <模型名>`）  gitignored 不進版控
        │
        ▼
  格式機械驗證（§4）→ 不合格重試一次 → 仍不合格照實標記
        │
        ▼
  比稿收斂：對話 agent 出對照表（3 標題×兩版、開場 hook、觀察筆記句、
  逐字引用句是否原文照登、格式合規），**不合成單一版**，交 MM 選
        │
        ▼
  MM 選定 →（之後的發布動作 100% MM 手動，不在本線範圍）
```

前置條件（觸發前就該備妥，缺任一即 FAIL 不啟動）：
`render_cut.py` 已跑過（有 `cut_map.json`＋最新 cutplan）、對話 agent 已寫好
`copy_material.md`。這與 `copy_prompt_build.py` 的自身檢查一致。

## 2. 觸發機制：候選與推薦

| 候選 | 做法 | 優點 | 缺點 |
| :--- | :--- | :--- | :--- |
| A. Lifov 卡搬欄 | 該集卡片從 `review:wait-mm` 搬走＝驗收 | 符合 Review 欄三 stage 慣例、MM 零新動作 | 跨系統：要 poll Lifov API；final_cut 驗收（聽音檔）常發生在終端對話裡，卡片狀態滯後 |
| B. Lifov 留言關鍵字 | 卡上留「驗收 OK」類關鍵字 | 同 A | 同 A，且關鍵字比對脆弱 |
| C. 檔案 marker（推薦） | 驗收當下在 session 目錄寫 `.copy_pending.json` | **沿用 repo 既有 `.<stage>_pending.json` 契約**（CLAUDE.md「Marker file 契約」）：任何後續 agent（Claude/Gemini/Copilot）看到就接手、做完刪 marker；零新機制、離線可用 | MM 驗收動作若發生在 Lifov 而非終端，需要有人代寫 marker |

**推薦：C 為權威訊號，A/B 是入口。**
MM 驗收的形式不管是「在對話裡說驗收 OK」「跑驗收指令」還是「搬 Lifov 卡」，
一律由當下在場的對話 agent（或看到 Lifov 訊號的 orchestrator session）翻譯成
`sessions/<slug>/.copy_pending.json`。權威訊號單一化在 repo 檔案系統，
與既有 pipeline 的接手協議完全同構。

**誰來監聽：** 不建常駐 daemon。
- 主通道＝**對話 / orchestrator session**：依既有 marker 契約，任何 agent 進 session
  目錄看到 `.copy_pending.json` 就執行本線（與 phase-b/step-3 marker 同一套習慣）。
- 兜底（Phase 2、可選）＝**cron 每日掃一輪** `sessions/*/.copy_pending.json`，
  補接 unattended 時段；cron 只跑「組裝＋雙引擎＋驗證」，比稿收斂仍留給對話 agent。

### marker 規格（草案）

```json
{
  "stage": "copy",
  "ep": "15",
  "prompt_file": "copy_prompt.md",
  "engines": ["agy", "codex"],
  "outputs": ["copy_draft_agy.md", "copy_draft_codex.md"],
  "approved_by": "MM",
  "approved_at": "2026-07-29T00:00:00+08:00"
}
```

完成（兩版草稿落地且過驗證）→ **刪 marker**；任一步 FAIL → marker 留著＋
在 marker 旁寫 `.copy_failed.log` 說明原因，等下一個 agent 或 MM 處理。

## 3. 引擎呼叫（2026-07-29 EP15 實測）

- **agy**（Antigravity CLI，`agy` = `antigravity` 別名）：
  `agy -p "$(cat sessions/<slug>/copy_prompt.md)" --model "Gemini 3.1 Pro (High)"`
  ——非互動單次呼叫，走自帶 OAuth login（CLAUDE.md 原則 5 的合法 CLI 授權通道，
  不經 Gemini API key）。實測 1.1.3 一次成功、一次過格式檢查。
  `--print-timeout` 預設 5m，夠用。
- **codex**：`codex exec "$(cat …)"` 等效批次呼叫（本設計文件不落地 codex 線，
  由 codex 線的委派各自實測確認旗標）。
- 兩線平行、互不阻塞：單引擎失敗不影響另一引擎產出。
- 產物檔頭一律 `> engine: <cli 名與版本> · model: <模型名>`，比稿與事後歸因都靠它。

## 4. 失敗處理與重試

| 失敗情境 | 處置 |
| :--- | :--- |
| CLI 不存在 / 未登入（OAuth 過期） | **不硬裝、不代登入**。跳過該引擎，另一引擎照跑；報告寫明缺哪個。兩個都缺＝FAIL，marker 留著報 MM |
| 輸出格式不合格 | 機械檢查（下列五項）不過 → **同引擎原 prompt 重試一次**；再敗＝保留不合格輸出（檔名加 `_FAILED`）＋照實回報，**絕不自動改寫充數、不換引擎偷渡** |
| 引擎 timeout / 非零 exit | 視同該引擎失敗，同上處置；不重試超過一次 |
| 前置缺檔（cutplan/cut_map/copy_material） | `copy_prompt_build.py` 自身 FAIL，整線不啟動，marker 留著 |

格式機械檢查（全部 grep 可驗，對應 prompt Criteria）：
1. `EP<n>｜` 標題恰好 3 個
2. 含「🧪 本集實驗進度」段，且 timestamp 均為 `(hh:mm:ss)` 半形括號格式
3. 含「💡 實驗觀察筆記」段（≥3 句）
4. 「🧪 關於我們」三行成員介紹與模板逐字一致
5. copy_material 標「逐字引用、不可改寫」的句子原文出現在草稿中

比稿階段另做人工抽查：Criteria 8（不可發明情節）抽 2–3 句對回逐字稿。

## 5. 紅線（不可越界）

- **發布永遠是 MM 手動**。本線終點＝兩版草稿放進 `sessions/<slug>/`，
  任何自動化（含 cron 兜底）不得碰上傳、貼文、排程發布。
- 引擎只讀 `copy_prompt.md`、只寫 `copy_draft_*.md`；不動 cutplan、音檔、
  metadata 與其他 stage 的 marker。
- `sessions/` 整目錄 gitignored——草稿與 marker 都不進版控，這是既有慣例。
- 改文案規範改 `shared-material/.../prompt_集數文案.md` 模板，不在 session 裡改
  （CLAUDE.md 2026-07-29 條目）。
