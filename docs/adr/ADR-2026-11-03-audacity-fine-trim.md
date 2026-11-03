# ADR-2026-11-03-audacity-fine-trim — Audacity 細剪：AI／MM 標籤軌差異 → `## ✂`／`## ✚`

- 識別碼：ADR-2026-11-03-audacity-fine-trim
- 日期：2026-11-03
- 狀態：已採納
- 相關：卡 #1181；ADR-2026-11-03-tempo-map

## 脈絡

文字剪（勾選、刪除線）的精度受 whisper 字界限制：切不到字中間，也切不到被吃進字長的停頓。
`## ✂ 起-迄` 能切任意時間，但要手打時間碼、看不到波形。MM 的工作型態是「大部分交給 AI 剪，
只有少數邊界要微調」。

## 決定

1. **不 fork Audacity，只讀它的專案檔**。`scripts/audio/audacity_labels.py`：
   - `export`：最近一次出片的 `cut_map.json`（保留段，原始時間軸）→ `_asset/audacity_AI.txt`。
   - `apply`：直接讀 `.aup4`（SQLite＋binary XML，含 4.x 的 op 16），比 `AI` 與 `MM` 兩條標籤軌的
     **覆蓋範圍**（聯集相減，不按標籤順序配對），差異寫回 cutplan。
2. MM 只動 `MM` 軌（從 `AI` 複製）：拉窄／刪標籤＝多剪 → `## ✂ 起-迄 audacity`；
   拉寬／新增標籤＝少剪 → `## ✚ 起-迄 audacity`。< 0.01s 的差異視為拖曳誤差。
3. 新結構行 `## ✚ 起-迄 說明`＝強制保留。render 在所有自動步驟（含 seam-pad）之後由
   `apply_review_edges` 最後套一次 ✂ 與 ✚，✚ 贏 ✂。✚ 碰到既有語音段就併寬，沒碰到就新開一段、
   插在來源時間之前的最後一段語音後面；audio=tracks 時補上它覆蓋到的 block 軌欄。
4. 寫回只重寫帶 `audacity` 尾巴的行（整組放在 ⚙ 行之後），人手寫的 ✂ 不碰。每輪差異累加；
   新舊反向重疊時新的贏。改前備份 `cutplan.before-audacity.md`。
5. 只用一條標籤軌，三軌一起剪；分軌各自剪之後再說。
6. `inject`：把 AI／MM 兩條軌直接寫進 `.aup4`，省掉 MM 匯入、改名、複製三步。只在 binary XML 的
   byte 位置上剪貼（新軌屬性抄既有標籤軌），寫之前自己先解析一次，壞了就不寫。`--heal-clips` 把同一條
   音軌被切開的多個 clip 併回一段（Audacity 4 刪除是非破壞性的，各 clip 都帶完整原音；offset 與
   blockid 全同才做）。Audacity 開著（-wal 有內容或 lsof 有人開著）就拒絕。

## 後果

- Audacity 開的分軌 WAV 與 source.wav 同長（EP22 實測 3042.915556s），標籤時間不用換算。
  開剪過的成品或 `.pertrack_bus.wav` 會對錯軸。
- 讀檔：Audacity 開著用 `mode=ro`（讀得到 -wal），關掉後 WAL 專案沒有 -shm 會 error 14，改 `immutable=1`。
- `line=pertrack` 舊分軌線不吃 ✚（那條線本來就跳過 ✂）。
- 差異累加的前提是 AI 軌＝上一輪套完後的成品：出片後要重新 export、在 Audacity 換掉 AI 軌。
  沒換的話上一輪寫進 cutplan 的 ✂／✚ 會被當成沒發生；`apply` 偵測到 cut_map.json 比
  audacity_AI.txt 新會警告。
- ✚ 新開段落落在語音與間奏音樂之間時，插在音樂前面；比所有正片都早時，插在第一段正片前
  （開場 🎬 集錦之後）。
- Audacity 4 的標籤匯入入口與快捷鍵尚待 MM 實機確認。
