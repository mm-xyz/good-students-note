# ADR 0006 — BGM 二段式 ducking 音量包絡

- **Status**: Accepted（2026-07-29 MM 拍板規格、當日實作；入 CLAUDE.md 原則 11）
- **Date**: 2026-07-29
- **Related**: ADR 0004（overlay 架構是本包絡的宿主；0004 的 afade in/out 淡入淡出自此作廢）

## Context

ADR 0004 的 overlay 讓 BGM 能疊在人聲下，但音量仍是單段 afade：進場一路淡到 100%、
出場一路淡到 0。實聽問題：疊著人聲時 BGM 太搶，獨奏時又沒有「接手」的存在感。
MM 給出正式定義：淡入淡出的定義應該是「幾秒內、音量從多少到多少」，且 BGM 要有
疊軌感知——**fadein/fadeout 都是二段式**，總原則是「整個剪輯過程的音量收放都應該
是舒服的遞增遞減」。

## Decision

BGM 音量走**分段線性包絡**（0–1 乘數，基線＝loudnorm 正規化後的音量＝100%）：

- **進場（疊著前段人聲）**：`fadein` 秒內 0 → duck（40%），人聲還在就壓在 duck；
  人聲結束後 `rise` 秒（預設 1s）duck → solo（70%）並維持——這就是二段式 fadein。
- **出場（後段人聲要回來）**：人聲進場前 `predrop` 秒（預設 2s）solo → duck；
  人聲進場後再依 `fadeout` 秒 duck → 0（fadeout 超過 tail 時夾到 tail）——二段式
  fadeout。
- **無疊軌的一側退化為單段**：片頭無前置人聲直接 0 → solo；片尾音樂無後續人聲
  直接在結尾前 `fadeout` 秒 solo → 0。

實作（`render_cut.py`）：

1. `bgm_envelope()` 從時間軸推導 keypoints——人聲在哪 render 本來就知道
   （lead/tail/gap 都是宣告值），**零音訊偵測、零 LLM**。
2. `env_to_expr()` 把 keypoints 轉成 ffmpeg `volume` 表達式（巢狀 if 分段線性內插，
   `eval=frame`），取代 0004 的兩顆 afade。
3. 獨奏窗太短時 keypoints 夾單調遞增，退化成連續 ramp，不會時間倒流或跳變。
4. 等級與時窗是全域旋鈕：`--bgm-duck 0.4`／`--bgm-solo 0.7`／`--bgm-predrop 2.0`／
   `--bgm-rise 1.0`；每首音樂的 `fadein/fadeout/lead/tail` 仍在 cutplan 🎵 行宣告。

EP15 實配（opening.mp3 18s、lead=5 tail=5 fadein=2 fadeout=5）：
`0s:0% → 2s:40% → 5s:40% → 6s:70% → 11s:70% → 13s:40% → 18s:0%`。

## Consequences

- 「舒服的遞增遞減」升格為全域原則（CLAUDE.md 原則 11）：語音側與 BGM 側的一切
  音量變化都必須是 ramp，不得跳變——之後新增任何 fade 類功能都受此約束。
- duck/solo 是全域旋鈕不是每首宣告——同一集節目的 bed 音量手感應該一致；若未來
  某首音樂需要例外，再擴充 🎵 行參數，不預先做。
- 包絡作用在 loudnorm **之前**：百分比是相對值，最終響度仍由 loudnorm 定錨，
  所以 duck/solo 的聽感比例在成品中大致保持（loudnorm 動態模式會有少量壓縮）。
