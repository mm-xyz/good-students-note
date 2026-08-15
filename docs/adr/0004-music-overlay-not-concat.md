# ADR 0004 — 音樂改 overlay 疊接，不進 concat 鏈

- **Status**: Accepted（2026-07-28 深夜 MM 給進出場規格、當日實作 `559b244`）
- **Date**: 2026-07-29
- **Related**: ADR 0002（節目結構住 cutplan.md）；取代其中「音樂是 concat 鏈的一段」的舊做法

## Context

第一版節目結構（`30ebffd`）把 🎵 音樂當成 concat 鏈裡的一個 segment：集錦 → 音樂 →
正片依序排隊，接縫用 acrossfade。EP15 實剪後 MM 給出真實的進出場需求：

- Opening 要在集錦收尾的**最後 5 秒就開始淡入**（音樂疊在語音尾巴下面）；
- Opening **結尾前 8 秒開始淡出、最後 5 秒主音軌淡入**（語音疊在音樂尾巴下面）；
- 結尾音樂在正片**最後 10 秒開始淡入**，語音結束後獨奏收尾。

Sequential concat 表達不了「同一時間兩軌都有聲音」——這是廣播式 bed music 的基本手感，
架構必須換。

## Decision

**音樂完全退出 concat 鏈，改成 overlay**：

1. concat 鏈只剩 speech / silence。音樂原本佔的位置換成一段長度＝
   `採用長度 − lead − tail` 的靜音 gap（音樂中段獨奏的空間）。
2. 每首音樂依 dst 時間 `adelay` 後用 `amix`（`normalize=0`）疊上語音軌：
   起點＝gap 起點 − `lead`（蓋過前段語音尾 lead 秒）；後段語音在音樂結束前 `tail` 秒
   進場（烘 `--music-speech-fade` 淡入）。片尾音樂（後面沒有語音）錨定語音結束點，
   自然延伸收尾。
3. cutplan 的 🎵 行語法擴充：`## 🎵 檔案 fadein= fadeout= lead= tail= start= end=`
   ——`start`/`end` 可選，只取音檔區間，預設整首（MM 2026-07-28 需求）。
4. 處理鏈順序固定：concat → dynaudnorm（人聲均衡）→ amix 音樂 → loudnorm。
   音樂不過 dynaudnorm（bed 音量不該被人聲均衡器改寫），整體響度最後由 loudnorm 定錨。

## Consequences

- 「疊接」成為一等公民：lead/tail 是宣告式參數，MM 在 cutplan 一行內就能調手感，
  不用碰 ffmpeg。
- concat 鏈變純語音，acrossfade 規則簡化（silence 或 baked-fade 接縫一律 10ms 微交疊）。
- 🎬 集錦的淡出淡入同輪改為烘進 segment（unit 級 2s in/out、間隔 1s），與音樂 overlay
  同屬「fade 是內容屬性、不是接縫屬性」的方向。
- amix 疊加後可能瞬間過峰，依賴後段 loudnorm（TP -1.5）壓住——目前實測 OK，未來如加
  多首同時疊的音樂要留意。
