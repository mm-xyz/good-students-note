# ADR 0003 — 多軌錄音：不分兩條 pipeline，只動進料端與出片端

- **Status**: Accepted（2026-07-28 MM 拍板；實作對應 Lifov 卡 #565）
- **Date**: 2026-07-28
- **Related**: ADR 0002（時間軸抽象是本決策的前提）、ADR 0001（人審層不變）

> **Amendment（2026-07-29）**：①mixdown 改 **stereo 出片**（MM 拍板）——各軌依
> 檔名排序做等功率 pan（±0.3，3 軌≈左/中/右），檔名可加數字前綴 `1_Mars.wav`
> 控排位（前綴不進 speaker 名）；mono 合軌 EP16 實測聽感悶、無空間感。
> ②amix `normalize=0`＋alimiter（等權 1/N 比錄音室合軌小 ~20 LU）。
> ③軌檔副檔名大小寫不敏感（錄音室輸出是大寫 .WAV）。

## Context

目前錄音是單一混音檔（`source.wav`），未來會改成**多軌：每軌一個檔案**（每位講者一軌）。
問題：pipeline 要怎麼支援？選項：

- A. 單軌一條 pipeline、多軌另一條。
- B. 維持單一 pipeline，多軌只改「進料」與「出片」兩端。

**已確認的前提：多軌來自同一台機器錄音**（MM 2026-07-28 確認），各軌天然 sample-aligned，
不需要 cross-correlation 對齊步驟。

## Decision

**選 B：單一 pipeline。** 理由：cutplan 這層剪的是「共同時間軸上的範圍」（ADR 0002），
與音檔數量無關；多軌同步錄音的所有軌共用同一條時間軸，同一組剪點對每一軌都成立。
fork 兩條 pipeline 等於把 snap/精剪/停頓收緊/谷底/word guard 全部維護兩份。

三段式落地：

1. **進料端（新增，多軌才跑）**
   - 目錄慣例：`sessions/<slug>/tracks/<Speaker>.wav`，**檔名＝speaker 名**；
     偵測到 `tracks/` 即多軌模式，否則照舊走 `source.wav`。
   - ingest 步驟：驗證各軌同長度 → mixdown 產 `source.wav` 與 `audio16k.wav`，
     下游分析線（轉錄、prosody）照舊吃這兩檔。
   - **diarization 升級為 ground truth**：多軌時不再用 pyannote 聲紋分離，
     每軌跑 VAD——哪軌有能量就是誰。多人搶話段的講者誤標直接歸零。
2. **中段（完全不變）**：cutplan.md 的人審體驗、格式、三方驗證一字不動。
3. **出片端（兩階段）**
   - **v1**：render 照舊吃 mixdown 的 `source.wav`——多軌唯一改動就是進料端，最小變更即可出片。
   - **v2（需要時才做）**：`run_ffmpeg` 改 N 個 input，每軌套同一組 atrim/crossfade，
     per-track 處理（獨立音量/EQ/降噪、壓掉未講話軌的環境音）後 amix。這是多軌音質的真正紅利，
     但不擋出片。

## Consequences

- 多軌不是另一條流程，是**同一條流程換了進料方式**；剪輯邏輯、人審層、節目結構零改動。
- 實作順序明確：第一步是 ingest（對齊驗證＋mixdown＋per-track VAD 產 speakers.json），
  render v1 完全不用碰。
- 若未來出現「各自錄再收檔」（不同機器）的情境，本 ADR 的免對齊前提失效，
  ingest 需補 cross-correlation 對齊——屆時應回頭修訂本 ADR，而非另開 pipeline。
- overlap 搶話的文字歸屬在 v1 仍靠 mixdown 轉錄的合併邏輯；v2 可升級成逐軌轉錄按時間合併。
