# 分軌剪輯實作規格（EP16）

你的 cwd 就是你的獨立工作區（完整 repo，可以自由 commit）。目標：讓
`render_cut.py` 能**從三條分軌出片**，並讓人審在 cutplan 上控制「誰的聲音在
什麼時候出得來」。

先讀：`CLAUDE.md`、`docs/adr/0011`、`scripts/audio/render_cut.py`、
`scripts/audio/pertrack_blocks.py`、`scripts/audio/migrate_marks.py`、
`sessions/2026-07-27_EP16-不要跟工作談戀愛/cutplan.pertrack.md`。

session：`sessions/2026-07-27_EP16-不要跟工作談戀愛`（三軌 symlink 在
`tracks/`，逐軌逐字稿與字級時間戳在 `pertrack/`，都已備妥）。

---

## 背景（已完成、不要重做）

三人錄音室節目，三條 sample-aligned 分軌 ＋ 一條混音 `source.wav`。現行
`render_cut.py` 從**混音**剪片段再 concat。問題：EP16 成品 04:47 有一串 KIN 的
「嗯 嗯 嗯」壓在 Mars 講話底下，混音逐字稿看不到、時間軸上也剪不開。

已完成並驗證：

1. 逐軌轉錄（本地 mlx-whisper，零 token）→ `pertrack/*.srt` ＋ `*.words.json`
2. 串音校準：從「某人獨講且領先 ≥12dB」的窗取 `level_i − level_j` 第 30 百分位
   當串音增益。EP16：Mars←Sarah −17.4／Sarah←Mars −15.8／KIN←Mars −19.7dB
3. block 細切到中位 0.94s（MM 規格：每秒一個可勾選的 block）
4. 人審資產遷移工具 `migrate_marks.py --with-checkboxes`（真實資料往返驗證：
   21 個剪除 ＋ 154 刪除線零失真）
5. whisper artifact 守門（同字重複 ≥4／U+FFFD／整句只有 1–2 種字元）

---

## 設計定案（兩位獨立評審 ＋ 實測資料收斂的結論，這些是前提不是選項）

### D1　混音逐字稿是唯一 canonical 文字來源

**逐軌 ASR 不可當出片文字。** 實證：Mars 直錄軌在 295–323 秒陷入「嘗」重複
迴圈，而 Sarah／KIN 軌的**串音** ASR 反而轉出了 Mars 原句。逐軌 ASR 在單一
麥克風上（訊噪比差、缺少其他人語境）比混音更容易崩。

所以：
- 正式文字 ＝ `transcript.speakers.srt`／`cutplan.json` 的既有 block 文字，
  依 `words.json` 細切成 0.4–1.2 秒的 canonical phrase。
- 逐軌波形只決定**這句歸哪一軌**與**各軌何時靜音**。
- 逐軌 ASR 降級成「重疊語句的救援證據」，不直接產生正式 block。

現行 `pertrack_blocks.py` 是「逐軌 ASR 直接變 block」，**要改成上面這套**。

### D2　文字歸屬：支配 ＋ hysteresis ＋ 字界對齊

- 每個 canonical phrase，在三軌同步波形上以 40ms frame、約 100ms 積分窗算能量。
- 第一名領先第二名 **≥3dB 且穩定 ≥200ms** → 歸第一名。
- 差距 <3dB → 標「歸屬不確定」，**不可硬選**，交人審。
- hysteresis：挑戰者必須連續領先 150–200ms 才換手（避免逐 frame 抖動）。
- 換手只能落在 canonical word boundary；附近 250ms 找不到字界就不切。
- 同一句跨越換手 → 按 word boundary 拆兩個 block。兩人真的同時講不同內容 →
  建立兩條平行 lexical row，**不要把兩句塞成一行**。

### D3　附和偵測（無文字出聲）

**關鍵認知（會簡化很多事）**：「沒有任何 block 覆蓋的軌 ＝ 預設 MUTE」。
所以 Mars 講話期間 KIN 軌本來就全段關閉，**不管有沒有偵測到那些「嗯」**。
附和偵測的召回率只影響「人看不看得見、能不能撈回來」，不影響「消不消得掉」。
不要為了追求召回率把門檻放到淹沒 cutplan。

門檻不要固定拍一組數字，改用每軌的 CFAR 式自適應門檻：
1. 從高信心「別人獨講」窗取負樣本。
2. 串音預測改用**線性功率相加**：`P_bleed = Σ_j P_j × g[i][j] + P_noise`
   （現行用 `max_j`，是錯的物理）。
3. excess 門檻 ＝ 該軌負樣本殘差的 P99.5。
4. 絕對下限 ＝ 該軌靜音噪聲 P99.5 ＋ 6dB。
5. duration 門檻 ＝ 負樣本錯誤 run 長度的 P99.5。
6. 掃描改 20–40ms frame ＋ 80ms gap closing ＋ ~120ms voiced duration 合併
   （現行 0.2s 掃 ＋ 0.3s 最短 ＝ 實際至少 0.4s，結構性漏掉 0.2–0.3s 的嗯聲）。

事件文字用 `（非詞彙出聲／待辨 0.4s）`，**不要叫「附和」**——純能量分不出
嗯聲、呼吸、碰桌、椅子。預設不勾。

驗收：高信心列 precision ≥90%；可見候選 ≤每分鐘 2 列；低信心收進折疊區。

### D4　render 兩層模型：atomic cells

把三軌所有 block 起訖、刪除線字界、`## ✂` 手動剪點的邊界取聯集，切成互不
重疊的 atomic cell。每個 cell × 每條軌只有 `KEEP` 或 `MUTE`：

- 三軌皆 MUTE → 從共同時間軸移除（時間消失，三軌一起）
- 至少一軌 KEEP → 時間保留，其餘軌在該 cell 靜音
- **同軌重疊 block 且勾選矛盾 → render 必須 FAIL**（不可「有一個勾就算留」，
  也不可「文件後者覆蓋」）
- 無 block 覆蓋 → 預設 MUTE

`~~刪除線~~` ＝ 該講者軌的字級 MUTE：同時間沒有其他軌保留 → 全域剪除；
另一人同時講話 → 時間保留、只靜音被劃線那條軌。**絕不把同一段串音文字的
刪除線複製到其他軌。**

### D5　混音鏈與音色跳變

順序（不要每軌各自 dynaudnorm，會把靜音與串音拉起來）：

```
各軌 static gain / high-pass / 輕量 EQ
  → 三軌套用完全相同的全域 atrim 時間段
  → 段內套各軌 gain envelope / mute envelope
  → 等功率 pan 混成 speech bus
  → speech bus 跑保守 dynaudnorm
  → BGM overlay（既有 duck/solo smoothstep 包絡，ADR 0004/0006 順序保留）
  → 整體 loudnorm
```

**音色跳變的處理（重要）**：不要在三軌全開的混音裡只於零星事件突然關一軌——
那會改變延遲串音的相位組合，產生音色與底噪抽動。改用**整集一致的 activity
mask**：
- 主要講者軌 0dB
- 非主要軌常態衰減 **−24～−30dB**
- 明確不要的事件才降到全靜音
- gate 邊緣 10–20ms equal-power fade ＋ ~50ms lookahead／hangover
- 從真靜音區取固定 **room-tone bed** 低量鋪底，避免每次關麥噪聲地板抽動

**不要做串音反相消除**（相位隨距離/轉頭改變，會產生金屬聲）。KIN 軌靜音後
其他兩軌仍留 17–23dB 的 KIN 串音，通常被主聲遮蔽，這是可接受的物理下限。

### D6　接縫

- 全域時間剪除：保留現行 40ms crossfade
- 軌內靜音切換：10–20ms fade，**不做** 40ms 時間 crossfade
- `snap` 要看三軌 aggregate／最大能量，不可只看某一軌
- `word_guard` 看所有仍保留講者的 word 區間聯集
- 先做軌內 gating、混成 speech bus，**再對 speech bus 做共同 crossfade**
  （避免分軌逐漸失去 sample alignment）

---

## 你要交付什麼

1. **改 `pertrack_blocks.py`**：照 D1 換文字來源、D2 歸屬、D3 附和偵測。
2. **改 `render_cut.py`**（或新增分軌 render 模組）：D4 atomic cells、
   D5 混音鏈與 activity mask、D6 接縫。**保留混音路徑不得破壞**——沒有
   `tracks` 區的 session 要照舊從 `source.wav` 出片，既有測試全綠。
3. **遷移**：把 EP16 現有 cutplan 的 21 個人審剪除 ＋ 154 處刪除線搬到新的
   逐軌結構（舊 block 有 `[講者]` 標籤可用；`migrate_marks.py` 的字元流
   difflib 對齊要擴充成 per-speaker 對齊）。
4. **出片**：`final_cut_pertrack.mp3`，放進 `v08_<時戳>_分軌/`，附 `render.txt`。
5. **驗收報告**：跟 v7（混音版，在 `v07_20260811-0010_逐句補錄/`）的可量測對照。
   自己設計量測方法，至少要能回答：長度差異、目標區間（源 313–322s，成品約
   04:47–04:49）KIN 附和是否消失、有沒有新的爆音／音色跳變、響度一致性。

## 紅線

- **TDD**：先寫紅燈測試再實作，回報要附紅燈→綠燈的實際輸出。repo 有既有
  pytest/unittest 套件（`bash scripts/tests/run_all.sh`），**必須全綠**。
- **不准碰 Google Drive**（`~/Library/CloudStorage/GoogleDrive-*`）；`cut.py`
  有同步回 Drive 的功能，不准跑那段。
- **不准 `git push`、不准改寫歷史、不准 `git stash`**（多 agent 並行，
  `refs/stash` 全 repo 共用會互吃）、不准動別的工作區。
- 不准刪除既有成品 mp3 或原始素材；不准改 `~/Local-drive/` 的原始錄音。
- 改完立刻 commit 到你自己的分支，不要留大片未 commit 的 working tree。

## 回報格式

1. **做了什麼**：分 D1–D6，附 commit hash。
2. **測試證據**：紅燈輸出 → 綠燈輸出，逐字貼關鍵行；`run_all.sh` 結果。
3. **驗收數據**：與 v7 的對照量測（實際數字，不要形容詞）。
4. **設計偏離**：規格哪裡你認為是錯的、你改成什麼、為什麼。
5. **你發現但規格沒提到的問題**（這項最有價值，不要省略）。
6. **沒做完的部分**，誠實列出。

全程繁體中文。中文標點一律全形。
