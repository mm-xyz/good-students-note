# CHANGELOG

> 剪輯線（feat/podcast-cut）的功能史。決策的「為什麼」住 `docs/adr/`，這裡只記「什麼時候做了什麼」。

## 2026-07-29

- **BGM 二段式 ducking 包絡**（ADR 0006，CLAUDE.md 原則 11）：fadein＝疊人聲
  0→duck＋人聲結束 rise 秒 duck→solo；fadeout＝predrop 2 秒 solo→duck＋人聲進場後
  fadeout 秒 duck→0。人聲位置從時間軸推導，`volume` expr 逐 frame 內插取代 afade；
  全域旋鈕 `--bgm-duck/solo/predrop/rise`。總原則入 CLAUDE.md：全程音量收放
  必須是舒服的遞增遞減，不得跳變。
- **實聽微調（同日 Amendment）**：duck/solo 預設 0.4/0.7 → **0.15/0.55**
  （振幅乘數，人耳對數）、rise 1.0→1.5s；ramp 曲線線性 → **smoothstep**。
- **共用素材庫前綴解析**：`shared-material/水星貓的生活實驗室_v2/`，素材命名
  `opening_*/break_*/ending_*`，cutplan 寫 `## 🎵 opening` 前綴對了就中；
  歧義 FAIL 列候選；`--material-dir` 可換庫。
- **cutplan ⚙ config 區**：cutplan.md 頂部 `## ⚙ key=value ...`＝該集 render 參數
  真相源（覆蓋 CLI/預設，未知鍵 FAIL）；cutplan.py 模板固定產出。集錦間隔預設
  1.0s→**0.5s**（MM 實聽「有點尬」）。
- **集數文案 prompt 模板入共用素材庫**：`shared-material/<節目版本>/prompt_集數文案.md`
  （profile/格式/語氣規範），session 只放 `copy_material.md` 素材，`{{集數}}`/`{{素材}}`
  置換組裝；實跑等 MM 驗收 final_cut 後派 agy＋codex。

- **音樂 overlay 疊接架構**（`559b244`，ADR 0004）：🎵 退出 concat 鏈改 amix 疊接，
  `lead`/`tail` 宣告進出場重疊、`start`/`end` 可選取音檔區間；片尾音樂自然收尾。
  EP15 實配：Opening（集錦尾 5s 淡入、尾 8s 淡出、末 5s 主音軌進場）＋間奏＋片尾（末 10s 淡入）。
- **🎬 集錦節奏改版**（`73f26a0`→`559b244`）：unit 級淡出淡入烘進 segment
  （`--clip-fade-in/out` 預設 2s）、集錦間隔 `--clip-gap` 預設 1s。
- **人聲動態均衡**（`559b244`）：`dynaudnorm`（m=4:p=0.9）插在 loudnorm 前，
  三人同軌音量拉齊；EP15 實測 -18.4 → -16.6 LUFS。
- **剪點防護鏈**（`73f26a0`＋`559b244`，ADR 0005）：>3s 異常 word 丟棄
  （EP15「好」16.6s 造成 17s 重複音訊）；unit 邊界用 words.json 字級對齊
  （「惜嗎」句尾被切）；exact 邊界跳過 snap/谷底/word_guard 外推。

## 2026-07-28

- **剪輯線三份 ADR**（`cbd8e08`）：cutplan 人審真相源／時間軸抽象／多軌單一 pipeline。
- **節目結構 v1**（`30ebffd`）：🎬 精華集錦（block 可複製重排）＋🎵 音樂床，
  播放順序＝cutplan.md 文件行序。
- **frames 線併入**（`e1045f9`＋`2486188`）：invisible-context 抽幀/VLM 篩圖/OCR/compose
  移植進 session 容器，`/invisible-context` skill 改指本 repo。
- **README 補完整流程**（`af5297f`＋`6d63bff`）：前置/開 session/命名/人審/出片/旋鈕。

## 2026-07-27

- **字級精剪＋停頓收緊**（`5607ac3`）：cutplan 的 `~~刪除線~~` 走 words.json 精準剪字；
  >1.5s 停頓自動收緊到 0.6s（word 邊界保護 `35ad5a4`）。
- **波形平滑接縫**（`a249e3b`）：剪點滑到能量谷底＋acrossfade 三角交疊。
- **出片預設 mp3**（`8b961ca`）：libmp3lame 192k，副檔名決定編碼。
- **剪輯 pipeline 立線**（diarize/prosody/cutplan/render 四件套）：SRT 轉錄＋說話人分離
  →韻律分析→cutplan.md 人審→ffmpeg 全自動出片。
