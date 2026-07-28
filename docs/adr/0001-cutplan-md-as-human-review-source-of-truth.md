# ADR 0001 — cutplan.md 勾選＝剪輯的人審真相源（防幻覺三方驗證）

- **Status**: Accepted
- **Date**: 2026-07-27（音訊分析線引入，commit `71fb5e0`；防篡改驗證隨 `render_cut.py` 落地）
- **Related**: CLAUDE.md 原則 10、原則 5（Engine Routing）、原則 1（SRT 不可變）

## Context

Podcast 文字剪輯（Descript 式「改文字＝剪聲音」）需要一個人審介面：MM 決定哪句留、哪句剪。
可選方案有：自製 GUI、直接改 JSON、或用 markdown checkbox。同時要防兩種風險：

1. **AI 幻覺**：LLM 提案剪輯時可能「發明」一段音檔裡不存在的話。
2. **人手滑**：人審時不小心改到正文或時間碼，導致 render 剪錯位置。

## Decision

**cutplan.md（markdown checkbox 清單）是剪輯的唯一人審真相源**：

- 一行一句（依 SRT 短句）：`- [x] B0012 [0:56–0:59] [Sarah] 正文` — 勾選＝保留，翻勾選就是剪輯。
- 人審**只准四種操作**：翻勾選、加 `~~刪除線~~`（字級精剪）、加行尾理由（` ← 文字`）、加 `## 章節標題`。
- `cutplan.json` 是機器可讀對照（block id → 精確時間碼），人不碰它。
- **agent 只提案、MM 必人審**：`.cutplan_pending.json` marker 還在就拒 render；agent 絕不代審直接出片。
- `render_cut.py` 出片前跑**三方逐字驗證**：
  1. md 與 json 的 block 集合一致（md 不得多、不得少）；
  2. md 每次出現的正文（去刪除線標記後）逐字 == json block 文字（正文不可改）；
  3. json block 文字逐字存在於來源 SRT（json 也不可竄改）。
  任一不符直接 FAIL，不出片。

## Consequences

- 剪輯工作流零 GUI：任何文字編輯器都是剪輯台；改完重跑 render 一分鐘出新版，迭代成本極低。
- 幻覺與手滑都被結構性擋住——LLM/人都「不可能發明一段不存在的話」還通過驗證。
- 代價：正文有錯字也不能在 cutplan 裡修（那是轉錄層的事）；驗證嚴格意味著任何格式偏離都 FAIL，
  需要清楚的錯誤訊息指路（已內建）。
- 衍生慣例：集錦區的 block 行「可複製貼上、可重複出現」合法（見 ADR 0002 節目結構）。
