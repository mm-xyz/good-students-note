# ADR-2026-11-06-wave-editor-mece-override：詞級波形編輯器（全軌 MECE 切分、ABBC 高優先級覆蓋與一鍵 Playback）

- 識別碼：ADR-2026-11-06-wave-editor-mece-override
- 日期：2026-11-06
- 狀態：已拍板（Lifov #1201 / #1218 / #1219 / #1220 / #1221）
- 關聯：擴充並逐步取代 `ADR-2026-11-03-audacity-fine-trim` 的外部 Audacity 流程

## 背景

在 `ADR-2026-11-03-audacity-fine-trim` 中，毫秒級邊界微調需要匯出標籤至 Audacity、拉動 `MM` 標籤軌再導回 `cutplan.md`。MM 拍板將細剪直接收斂進本機版 `scripts/cutplan-editor/local.html`（與 `Index.html` 共用 `cutplan-core.js`），並提出四項關鍵架構要求：

1. **借鏡 `storytold/soundcraft` 架構**：移植其高效能多解析度波形金字塔與 Smart Tool 手把狀態機，不重造底層輪子。
2. **全軌與詞級 100% MECE（Mutually Exclusive, Collectively Exhaustive）**：初始區塊必須完整且互斥地覆蓋整條音軌 `[0, track_duration)`，展開為詞級子積木（`B0001-1/-2/-3`）時亦同。
3. **強制覆蓋高優先級（`AA / BB / CC` 硬切成 `ABBC`）**：當人審從詞中段硬切時，手動剪輯範圍優先級最高，強制壓過 `word_guard`、`snap_boundaries`、`pad_seams`、刪除線與停頓收緊。
4. **一鍵依剪輯範圍 Playback**：在瀏覽器內直接按一下即可無縫預覽保留區間（自動跳過所有切除區間）。

## 決策

### 1. 移植 `storytold/soundcraft` 的 `Peaks` 金字塔與 Smart Tool 狀態機
- 在 `cutplan-core.js` 實作 `buildPeaks`（`PEAKS_BLOCK = 32`、`PEAKS_FANOUT = 4`，儲存 `min / max / squares`）、`queryPeaksMinMax`、`queryPeaksRms` 與 `findValleySeamMs`。任意縮放倍率的像素欄查詢皆為 $O(\log N)$。
- 移植 `trimDragSnap`：預設硬吸附 `words.json` 詞積木邊界；按住修飾鍵（`Alt/Option` 或 `Cmd/Ctrl`）或開啟 UI「🔓 強制覆蓋模式」時切換為 **1ms 自由滑動（Slip Mode）**；距原點 `≤ 6px` 自動磁吸回原點防呆。

### 2. 切分（Partition）與標籤（Label）分離，達成全軌與詞級 100% MECE
- 時間軸一律採用**整數毫秒半開區間 `[s, e)`**。
- `buildTrackPartition` 先將相鄰微重疊的語音段 `B` 於能量谷底／中點接齊，再以 `B` 的補集自動填補所有靜音區間（`~^` 開頭、`~Bxxxx` 句間停頓、`~$` 結尾），並將既有的 `Gxxxx` / `Pxxxx` 映射為該靜音區段的 `legacyIds`。這消除了 `G` 與 `P` 重疊以及 `<0.9s` 停頓無區塊覆蓋的問題。
- `atomizeBlock` 將每個 `B` 區塊內的 `words.json` 詞時間戳正規化為 `B0001-1, B0001-2...`（微小詞間空隙於能量谷底接齊，顯著詞間停頓獨立為 `B0001-1~2` 靜音子積木）。
- 由 `assertMECE` 在載入時強制檢驗頂層區塊與全軌展開子積木（`allAtoms`）皆滿足 `list[0].s === 0`、`list[k-1].e === list[k].s`、`list[last].e === durationMs`。

### 3. 三層區間代數與 `ABBC` 強制覆蓋
- 前端與 `render_cut.py` 的 `apply_review_edges` 共用同構的三層區間代數：
  $$\text{Final} = (\text{Base} \setminus \text{Cut}) \cup \text{Keep}$$
  且維持不變式 $\text{Cut} \cap \text{Keep} = \emptyset$。
- 當使用者將 `AA / BB / CC` 框選或拖拉硬切成 `ABBC` 時，`forceWindowOverride` / `applyTrimOverride` 將範圍外標為 `## ✂ <start>-<end> wave`、範圍內標為 `## ✚ <start>-<end> wave`。
- `render_cut.py` 的 `apply_review_edges` 位於所有自動規則（`word_guard`、`snap_boundaries`、`pad_seams`）之後，並將 `min_frag` 下調為 `0.03s`（30ms），確保半詞精剪碎片不被濾除。
- `findIllegalEdit` 護欄放行格式受控的 `## ✂ / ## ✚ <start>-<end> wave|audacity` 行，其餘非管理結構行維持逐 byte 唯讀保護。

### 4. Web Audio API `CutPlayer` 一鍵剪輯預覽
- 棄用 `HTMLAudioElement` 輪詢 seek（有 20–100ms 跳躍延遲與 MP3 解碼 priming 偏移），改由 `CutPlayer` 以 Web Audio API `AudioBufferSourceNode` + 4ms 微淡入淡出排程 `_asset/audio16k.wav`（或 `source.wav`），達成 0 誤差跨切點無縫播放與游標同步跳躍。
