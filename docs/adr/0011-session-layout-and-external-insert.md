# ADR 0011 — session 目錄分類（Drive 同構）與 `## ➕` 外部補錄插入

- 日期：2026-08-10
- 狀態：已採納
- 相關：ADR 0001（cutplan.md 是人審真相源）、ADR 0003（多軌 ingest）、
  ADR 0010（停頓預設剪掉）

## 脈絡

兩件事同時到期。

**一、session 根目錄爆炸。** EP16 一集就在 `sessions/<slug>/` 根平鋪 30+ 個檔：
四代成品（初剪／二剪／三剪／v4）、兩支預聽、六個 `.bak-*`、文案、封面、生圖
prompt，全部跟管線工作檔（`words.json`、`prosody.json`、`transcript*.srt`）混在
一起。Google Drive 那邊已經分好 `_meta/` + `raw/` + `vN_<時戳>-AI/`，兩邊對不上，
人要在兩個心智模型之間切換。

**二、事後補錄無處可放。** EP16 錄完後 Sarah 補錄了 45 秒（「工作的火花在哪」）。
這段音訊**不在 `source.wav` 的時間軸上**——不同時間、獨立麥克風、與三軌不同步，
走不了 `ingest_tracks.py`（它會驗 sample 對齊）。而 `render_cut.py` 的出片模型是
「從 `source.wav` atrim 出片段再 concat」，`## 🎵` 只做 BGM overlay 疊接。
換句話說：**現行管線沒有任何方式表達「這裡插一段外部語音」**。

## 決策

### 一、目錄分類採「方案 A：人看檔分類，管線工作檔留在根」

```
sessions/<slug>/
├── raw/                    原始素材(補錄、音樂素材複本等未進管線的檔)
├── tracks/                 已對齊的管線分軌(ingest_tracks.py 寫死,維持不動)
├── _meta/                  人看的伴隨檔:highlights / chapters / pipeline_run /
│                           文案 / 封面 / 生圖 prompt / gemma 提案 / 筆記標註
├── _bak/                   所有 *.bak-* 快照
├── vNN_<YYYYMMDD-HHMM>_<標籤>/   每版成品一個資料夾(mp3 + cutplan 快照 + render.txt)
└── (根)                    管線工作檔:source / audio16k / transcript* / words /
                            prosody / cutplan.{md,json} / cut_map / speakers* / context
```

**否決了「完全鏡像 Drive、管線工作檔進 `_work/`」。** 那要改 `scripts/audio/*.py`
與 `session.py` 約 15 處硬編碼路徑並加舊路徑 fallback，`CLAUDE.md` 的目錄慣例章節
要改寫（該慣例是筆記線、doc 線共用的，不只 podcast 線），還得重跑 render 驗證沒斷。
收益只是「根目錄再乾淨一點」，風險遠大於收益。

Drive 只放人看的東西，local 還要養管線工作檔——兩邊本來就不該逐檔相同，
**對齊的是分類語彙（raw / _meta / vN），不是檔案集合**。

工具：`scripts/audio/tidy_session.py`（dry-run 預設，`--apply` 才搬，只搬不刪）。
這是慣例不是一次性搬檔，EP15／EP16／EP17 已套用。

兩個實作上踩到的判定坑，寫死在腳本裡：

- `final_cut.bak-v2music.mp3` 是「初剪」那一版的**成品**，不是快照檔。
  成品判定必須優先於 `.bak-`，否則一整版成品會從版本序列裡消失。
- `opening.mp3` 這種音樂素材複本不是成品，不該佔掉一個版本編號 → 歸 `raw/`。

### 二、新增 `## ➕` 節目項：外部語音插入

語法（與既有 `## 🎵` / `## ✂` 同構，位置＝播放順序）：

```
## ➕ <檔案> [gain=auto|±dB] [start=S] [end=E] [fade=F] [tempo=T]  說明
```

- 檔案解析沿用 `resolve_music`（session 目錄 → repo 根 → 絕對路徑）。
- `gain=auto`（預設）：量 `source.wav` 與補錄各自的 **integrated LUFS**，
  差值當增益。補錄與正片是不同時間、不同增益錄的，直接接上去音量會跳，
  而人耳對接縫處的音量跳變特別敏感。用實測 LUFS 差比拍腦袋填 dB 可靠，
  且是確定性的——零 LLM、可重現。EP16 實測：正片 −33.3 / 補錄 −35.2 → +1.9dB。
- `tempo` 預設 **1.0**：補錄是另外錄的，本來就是自然語速；要跟正片一起變速
  才顯式寫（正片 EP16 走 `tempo=1.06`）。
- 接縫走 **10ms 微交疊**而非預設的 40ms crossfade：補錄兩側已烘好淡入淡出，
  而且是另一支麥克風的底噪，再疊 40ms 只會讓兩層底噪重疊出「唰」一聲。

**關鍵設計點：補錄不進防幻覺驗證。** `validate_program` 只處理 `kind == "block"`，
補錄的文字本來就不在來源 SRT 裡（它是新錄的），若比照 block 驗證必然 FAIL。
防幻覺鏈保護的是「不准竄改既有逐字稿」，補錄是新素材、由人明確指名檔案，
不在該威脅模型內。

**否決了「一次性 ffmpeg 拼接成品」。** 快，但補錄不會存在於 cutplan，
下一次重 render 就把它弄丟——而重 render 是這條線的常態（EP16 十天內出了六版）。
cutplan 是人審真相源，能表達的東西才活得下來。

## 後果

- 好：補錄成為可重現的一等公民；重 render 不再丟東西。
- 好：local 與 Drive 用同一套分類語彙，人不用切換心智模型。
- 代價：`## ➕` 是第四種 `##` 節目項，cutplan 語法表面積繼續長。
  `CHAPTER_RE` 是 `^## (.+)$` 會吃掉所有 `## ` 開頭的行，新項目一律要
  **排在 CHAPTER_RE 之前**並補一條回歸測試——`## ✂` 曾經因此被當成章節標題
  寫進 IG 文案（2026-08-10 實踩）。已鎖：
  `test_insert_line_is_not_swallowed_as_a_chapter`。
- 代價：`gain=auto` 每次 render 要對 `source.wav` 跑一次 ebur128（33 分鐘音檔
  數十秒）。只在 cutplan 真的有 `## ➕` 時才跑。
