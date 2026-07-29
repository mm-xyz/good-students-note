# ADR 0007 — cutplan G 列：空白/非語音段可見、預設不選

- **Status**: Accepted（2026-07-29 MM 拍板「空白的時間也標在 cutplan 中，預設不選」）
- **Date**: 2026-07-29
- **Related**: ADR 0001（cutplan 人審真相源）、ADR 0002（時間軸抽象）

## Context

cutplan 只列 SRT 語音 block；block 之間 ≥2s 的空白（開錄打板、大笑、環境音、
音效）在 cutplan 上**隱形**——render 建 unit 時自動落在 unit 之外被剪掉，
人審連「有東西被剪了」都看不到。EP16 開頭的拍手打板就是實例。

## Decision

- `cutplan.py prepare` 掃 block 間 ≥ `--min-gap`（預設 2.0s，與 render 的 unit
  切分閾值對齊）的空白，產出 **G 列**（`G0001` 起）插在對應位置，寫進
  cutplan.json 的 `gaps` 鍵；`add-gaps` 子命令對既有 session 冪等補列
  （不動任何既有行，含刪除線/勾選/章節）。
- **預設不勾＝行為與既往完全一致**（照舊剪掉）；勾選＝保留該段原聲。
- G 列文字純說明可自由改；render 驗證只查 id 存在於 `gaps`，不做 SRT 逐字比對。
- 勾選的 G 段在 render 是 **raw unit**：保留原聲原長，不 snap、不收停頓、
  不字級精剪（它本來就是「非語音」，任何平滑步驟都會誤傷）。

## Consequences

- 隱形剪裁變成顯式選項，人審的資訊完整性補齊；預設輸出零改變。
- 閾值 2.0s 以下的空白仍走停頓收緊（--max-pause），不列 G——避免行數爆炸。
- fillers_local／copy_prompt_build 的 LINE_RE 只認 `B` 列，G 列天然跳過。
