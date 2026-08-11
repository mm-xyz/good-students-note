## 1．Q1 的建議方案

結論：我支持「文字歸屬用支配、附和偵測用 excess」的分離，但反對讓三份逐軌 ASR 成為三份文字真相源。最穩定的架構是：

1. **混音逐字稿是唯一 canonical 文字時間軸。**
2. **逐軌波形只決定文字屬於哪一軌，以及各軌何時靜音。**
3. **逐軌 ASR 降級成重疊語句的救援證據，不直接產生正式文字 block。**

EP16 已有直接證據：Mars 直錄軌在 295–323 秒陷入整段「嘗」的重複迴圈，但 Sarah／KIN 軌的串音 ASR 反而轉出了 Mars 原句。若採「主要在哪一軌，就使用該軌 ASR 文字」，這段會把正確文字丟掉。

### 建議演算法

- 先把混音逐字稿按 `words.json` 切成約 0.4–1.2 秒的 canonical phrase。
- 對每個 phrase，在三軌同步波形上以 40ms frame、約 100ms 積分窗計算能量。
- 文字歸屬：
  - 第一名領先第二名 ≥3dB，且至少穩定 200ms：歸第一名。
  - 差距 <3dB：標成「歸屬不確定」，不可硬選。
  - 使用 hysteresis：挑戰者必須連續領先約 150–200ms 才換手，避免逐 frame 抖動。
  - 換手只能落在 canonical word boundary；附近 250ms 找不到字界就不切，交人審。
- 若同一句跨越換手，按 word boundary 拆成兩個 block；若兩人真的同時講不同內容，建立兩條平行 lexical row，不把兩句塞成一行。

### 相似度與時間容差

相似度只用於「逐軌 ASR 救援／去重」，不決定正式文字：

- 正規化：NFKC、英文 casefold、移除空白與標點；不要移除實質詞彙。
- 候選時間窗：cue 各向外放寬 ±0.4 秒。
- 時間條件：`intersection／較短 cue 長度 ≥0.5`。
- 文字條件：字元級 Levenshtein／LCS 相似度 ≥0.60。
- 所有匹配必須錨在同一個 canonical phrase，不能用 unrestricted graph clustering，否則相鄰句會因傳遞關係黏成長段。

題目中的三個 ASR 版本，單純字元相似度為 0.644、0.677、0.733，因此 0.60 能涵蓋這類錯法；但正式文字仍應取混音 canonical 版本。

### 文字拿走後怎麼處理

若某軌的 ASR 文字被判定是別人的串音：

- 文字丟棄。
- 該軌若仍有獨立 excess，留下無文字事件：
  `（非詞彙出聲／待辨 0.4s）`
- 不宜直接命名「附和」，因為純能量無法區分嗯聲、呼吸、碰桌與椅子。
- 預設不勾；必要時由人改成保留。

更重要的是：應明定「沒有任何 block 覆蓋的軌＝預設靜音」。如此在 Mars 講話期間，KIN 的五個嗯聲即使只列出兩個事件，KIN 軌仍全段關閉；偵測 recall 主要影響「人能否看見並撈回附和」，不是能否消除它。

### 附和門檻的原則

不要固定拍一組 10dB／−45dB；改用每軌的 CFAR 式門檻：

1. 從高信心「別人獨講」窗取得負樣本。
2. 串音預測改用**線性功率相加**，不是 `max_j`：
   `P_bleed＝ΣP_j×g[i][j]＋P_noise`。
3. excess 門檻取該軌負樣本殘差的 P99.5。
4. 絕對下限取該軌靜音噪聲 P99.5＋6dB。
5. duration 門檻取負樣本錯誤 run 長度的 P99.5。
6. 用人工標記集驗收：高信心列 precision ≥90％；可見候選量 ≤每分鐘兩列；低信心候選收進折疊區。

現行 0.2 秒掃描配 0.3 秒最短長度，實際至少要連中兩格，也就是 **0.4 秒**，天然漏掉 0.2–0.3 秒的嗯聲。應改成 20–40ms frame，再用 80ms gap closing 與約 120ms voiced duration 合併事件。

### 我否決的方案

- **否決完整三份逐軌 ASR 直接合併**：串音使三份文字不是獨立證據，且直錄軌未必轉得最好。
- **否決純支配處理所有事情**：會消滅真實的小聲附和。
- **否決純 excess 處理所有事情**：已證明會把 Mars 文字裝進 KIN block。
- **不建議先能量切段後把每軌全部重轉**：低音量串音仍會被 Whisper 轉出；只應對「兩軌都有強 excess、混音 ASR 疑似漏句」的少量窗口做 targeted ASR。
- **建議 forced alignment，但只負責時間**：先有 canonical 文字，再對指派軌做 forced alignment；不要讓 forced alignment 兼任文字來源。

## 2．Q2 的建議方案

兩層模型成立，但要先增加「原子時間格」與衝突規則。

### 修正版模型

把三軌所有 block 起訖、刪除線字界與手動剪點的邊界取聯集，切成互不重疊的 atomic cells。每個 cell、每條軌只有 `KEEP` 或 `MUTE`：

- 三軌皆 `MUTE`：從共同時間軸移除。
- 至少一軌 `KEEP`：時間保留；其他軌靜音。
- 同軌有重疊 block 且勾選狀態矛盾：render 必須 FAIL，不能採「有一個勾就算留」或依文件後者覆蓋。
- 無 block 覆蓋：預設 `MUTE`。

目前 WIP 還不符合此前提：三軌合計有 973 組相鄰重疊 block，因此不能直接進 render。

### 刪除線語意

`~~刪除線~~` 是該講者軌的字級 `MUTE`：

- 若同時間沒有其他軌保留，該字區間成為全域剪除，時間消失。
- 若另一人同時講話，時間保留，只靜音被劃線的那條軌。
- 字界使用 canonical word timeline，必要時再對 owner track forced-align。

### Render 順序

建議保留現行 DSP 鏈，但調整成：

1. 各軌靜態 gain、high-pass、必要的輕量 EQ／降噪。
2. 三軌套用**完全相同的全域 atrim 時間段**。
3. 在段內套各軌 gain envelope／mute envelope。
4. 依既有等功率 pan 混成 speech bus。
5. speech bus 跑保守的 `dynaudnorm`。
6. BGM overlay 與既有 duck／solo smoothstep 包絡。
7. 最後整體 `loudnorm`。

不要每軌各自 `dynaudnorm`，否則靜音與串音會被拉起來。BGM 仍不得進 `dynaudnorm`，ADR 0004／0006 的順序應保留。

### 音色跳變的具體處理

不要在三軌全開的混音中，只於零星事件突然關一軌。這會改變延遲串音的相位組合，產生音色與底噪跳變。

應採整集一致的 activity mask：

- 主要講者軌 0dB。
- 非主要軌常態衰減約 −24 至 −30dB。
- 明確不要的事件可降至全靜音。
- gate 邊緣使用 10–20ms equal-power fade，搭配約 50ms lookahead／hangover。
- 從真靜音區取得固定 room-tone bed，低量持續鋪底，避免每次關麥時噪聲地板抽動。
- 不嘗試用串音反相消除；串音增益與相位會隨距離、轉頭改變，容易產生更嚴重的金屬聲。

KIN 主軌靜音後，其他兩軌仍會留下低 17–23dB 的 KIN 串音，但通常會被 Mars 主聲遮蔽。這是可接受的物理下限，不應為了追求全消而做高風險消除。

### 接縫調整

- **全域時間剪除**：保留現行 40ms crossfade。
- **軌內靜音切換**：使用 10–20ms fade，不做 40ms 時間 crossfade。
- `snap` 應看三軌的 aggregate energy／最大能量，不可只看某一軌。
- `word_guard` 要看所有仍保留的講者 word 區間聯集。
- 所有軌的全域 crossfade 時長與剪點必須一致；最好先做軌內 gating、混成 speech bus，再對 speech bus 做共同 crossfade，避免分軌逐漸失去 sample alignment。

## 3．Q3 的建議方案

### 遷移策略

現行 `migrate_marks.py` 不能原封不動套到三軌：它雖然解析了 speaker，實際 mapping 仍把所有 block 串成一條字元流；三軌重複文字會讓對齊失真。

建議分兩層遷移：

1. **先把舊決定投影到 canonical source timeline。**
   - 舊 block 時間碼是主錨。
   - 舊 `[講者]` 是第二錨。
   - 文字相似度只做驗證與細部 word 對齊。
2. **再把 canonical interval 投影到三軌 atomic cells。**
   - 舊勾選 block：初始化該講者軌為 `KEEP`。
   - 舊未勾選 block：因其舊語意是「整段時間消失」，先產生 global tombstone，三軌全關，確保與 v7 相容。
   - 舊刪除線：同樣先保留為 global word-level tombstone。
   - 若新分軌發現 tombstone 中另有重要重疊語句，再列入人工覆核，由人決定是否改成單軌靜音。

候選配對條件：

- speaker 相同。
- 時間交集／較短 block ≥0.5，並允許 ±0.4 秒 ASR 邊界容差。
- 正規化字元相似度 ≥0.60。
- 同分或跨軌衝突不得自動搬，必須列出 unresolved report。

驗收不應要求「新版仍是 21 個未勾選、154 個刪除線」，因為重切會改變 block 數；應驗證這 21＋154 個舊決定在 source-time／word coverage 上 100％被保留，或明確出現在 unresolved 清單，禁止靜默丟棄。

### 與 v7 的可量測驗收

| 關卡 | 驗收條件 |
|---|---|
| 遷移完整性 | 舊 21 個剪除與 154 個刪除線：100％映射或明列 unresolved；靜默遺失＝0 |
| Null render | 用 v7 的 global cut mask、三軌全開、相同 pan／gain／DSP 出 WAV；與 source-based v7 對齊後相關係數 ≥0.99，SI-SDR ≥40dB |
| 時間軸 | 所有軌使用同一 global segment 表；總長誤差 ≤一個 PCM sample；`cut_map` 單調且無重複／倒退 |
| 文字保護 | 除人審刪除線與 global tombstone 外，保留字的 forced-alignment 區間被剪中數＝0 |
| 附和偵測 | 313–322 秒五個人工標記事件在「高＋低信心候選」中 5／5 可見；高信心列 precision ≥90％ |
| 目標抑制 | 五個 KIN 事件的 300–3400Hz 能量相對 v7 至少降低 12dB；同窗 Mars 主聲能量變化 ≤0.5dB |
| 接縫 | gate 前後 200ms 的噪聲地板差 <3dB；頻譜跳變不高於 v7 自然換手接縫分布的 P95 |
| 成品響度 | 相對 v7 integrated loudness 差 ≤0.5 LU；true peak 不高於 −1.0dBTP；LRA 差 ≤1 LU |
| 人耳盲測 | 針對五個附和、換手、刪除線與 BGM 接縫做隨機 A／B；新版本的爆音／悶聲事件數不得高於 v7，附和干擾評分必須下降 |

測試應分四版，不要直接只比最終成品：

1. v7 原版。
2. 三軌 null render。
3. 相同 global cut、只啟用軌層 gating。
4. 完整重切的人審新版。

如此能分清問題出在重混、gating，還是新一輪剪輯決策。

## 4．我認為整個方向最大的風險

最大的風險不是「分軌出片」，而是**把原始逐軌 ASR 直接變成人審真相源，導致資訊量爆炸且狀態含糊**。

現行 `cutplan.pertrack.md` 已從約一千行膨脹到 3010 行，含 2649 個 block；其中有 973 組同軌重疊、136 組相鄰同文重複。人審若面對這種資料，不可能穩定判斷哪一列控制哪一段聲音，勾選本身就會失去可信度。

所以我的判斷是：

- **分軌 render 應該做。**
- **完整三軌狀態模型應該做。**
- **三份逐軌 ASR 不該直接成為三份正式文字流。**

先建立 canonical 文字時間軸、非重疊 atomic cells 與明確衝突閘，再接 render。否則會把混音版「剪不到疊音」的單一問題，換成更難查的「同一毫秒有多個互相矛盾的 checkbox」。

## 5．我驗證過的數據

我做的都是唯讀檢查，沒有直接執行會覆寫 `cutplan.json`／`cutplan.pertrack.md` 的 `pertrack_blocks.py main`。

- 完整讀取了 `CLAUDE.md`、13 份 `docs/adr/*.md`、`render_cut.py`、`pertrack_blocks.py`、`migrate_marks.py`，以及必要的 `srt_utils.py` 切句函式。
- `ffprobe`：
  - 三條 WAV 與 `source.wav` 均為 1973.125805 秒。
  - 三軌皆為 44.1kHz mono；`source.wav` 為 stereo。
- `ffmpeg ebur128` 重量 38.97–45.6 秒：
  - Mars −58.2 LUFS。
  - Sarah −52.5 LUFS。
  - KIN −35.6 LUFS。
  - 與題目數字完全一致。
- 唯讀載入 `pertrack_blocks.py` 函式重算串音：
  - Mars←Sarah −17.4dB。
  - Sarah←Mars −15.8dB。
  - KIN←Mars −19.7dB。
  - 與題目列出的三項完全一致。
- 三個「中小型公司」版本的單純字元相似度為 0.644、0.677、0.733。
- 現有 `cutplan.json`：
  - Mars 945 blocks，Sarah 726，KIN 978。
  - 合計 2588 個 speech blocks＋61 個 backchannel blocks。
  - 同軌相鄰重疊 973 組；相鄰同文重複 136 組。
- 現行 `render_cut.py --dry-run`：
  - 948 個舊混音 block 中仍有 21 個超過 8 秒。
  - 146 處實際進入保留段的字級精剪。
  - 29 處停頓收緊。
  - 2 段手動剪除，共 1.20 秒。
  - 109 segments、3 首 BGM、語音 31：05、tempo 1.06。
- v7 實際成品測得：
  - Integrated loudness −17.0 LUFS。
  - LRA 7.4 LU。
  - True peak −1.4dBFS。
  - 長度約 30：20.47。
- 目前 workspace 的 WIP 與設計稿有小幅 snapshot drift：
  - 設計稿寫 64 段附和；目前檔案為 61 段。
  - 目前 Mars 295–323 秒逐軌 ASR 是「嘗」重複 artifact，會讓「必須發生在別人已接受的文字 block 中」這條條件漏掉 313–322 秒候選。這是我建議改用 canonical mix speech mask 的直接原因。
- 我直接採信、未重新主觀判定：
  - 313–322 秒人工聽到五段 KIN 出聲。
  - Whisper 不轉「嗯」。
  - 6dB／0.2 秒曾產生 1840 段。
  - 歷史遷移測試曾達成 21 個剪除＋154 處刪除線零失真。
  - MM 對「完整逐軌模型、這集重切」的產品決策。

最後以 `git status --short` 確認工作樹仍為 clean；沒有修改任何檔案。