# ADR-2026-09-06-mixdown-audio-line — 第三條路線：分軌人審決定 ＋ 合軌音源

- 識別碼：ADR-2026-09-06-mixdown-audio-line
- 日期：2026-09-06
- 狀態：已採納
- 相關：ADR 0001（cutplan 是人審真相源）、ADR 0014（軌可用性決定剪輯路線）、
  ADR 0015（podcast 剪輯 SOP）、PERTRACK_IMPL.md D5/D6（bus 混音鏈與接縫）

## 脈絡

EP18（2026-07-27_EP18-季中回顧）已出到 v7，MM 聽完的結論是「分軌的品質不是
很理想」，要求改用錄音機自己的合軌（`合軌.WAV`＝session 的 `source.wav`）。

分軌線的成品不是原始音訊，是 `pertrack_render.mix_ranges()` 現場合出來的
speech bus：三軌各自 gate、量 static gain 拉齊 LUFS、互相關對時（EP18 實測
4.88ms 位移）、再鋪合成 room-tone。這條鏈上任何一環沒調準，都會比錄音機直
接給的合軌難聽——而合軌本來就存在，不需要被重建。

問題在於**人審長在分軌節目單上**。EP18 的 `cutplan.md` 是逐軌格式（block 帶
MR/SR/KN 兩碼前綴），MM 累積了 1515 個勾選、294 個靜音、16 處刪除線。混音線
的節目單是另一組 block（`cutplan.json` 的 426 個 `B####`），要走混音線就得
重生成節目單，等於把整批人審丟掉重來。

`migrate_marks.py` 能搬記號，但混音 block 的粒度粗得多（426 個 vs 逐軌
1809 個，平均近 7s，超過 `--max-block` 8s 的示警線一半以上），以 0.6 覆蓋率
門檻搬過去，細顆粒的剪除決定會整批對不上而被丟棄。

## 決策

新增 `## ⚙ audio=mixdown`（等價 CLI `--mixdown-audio`）：**剪輯決定層照分軌
人審跑完，音源改用混音 `source.wav`，不混 speech bus。**

分軌決定層算出來的 `segments` 時間碼本來就長在 `source.wav` 的時間軸上（bus
座標是最後才換算的），所以這條路線不是新演算法，而是**少做一步**——把「照
這些區間去切 source.wav」直接執行完。

路線因此有三條，都由 cutplan 的 ⚙ 決定（cutplan 是參數真相源）：

| ⚙ | 決定層 | 音源 |
| :--- | :--- | :--- |
| `line=mixdown` | 混音 block | `source.wav` |
| `line=pertrack` | 逐軌 block（兩層模型） | speech bus（三軌重混） |
| `line=pertrack audio=mixdown` | 逐軌 block（兩層模型） | `source.wav` |

`audio=mixdown` 寫在混音線節目單上直接 FAIL：混音線本來就吃 `source.wav`，
靜靜接受只會讓人以為自己切換了什麼。

## 代價（寫明，不是副作用）

分軌能做到的「留住時間、只把某一軌靜音」，在合軌上做不到——合軌是一條已經
混好的音軌，沒有可以單獨關掉的軌。那些被人審靜音的串音會以原始音量回到成品
裡。

EP18 實測：673 個 atomic cell 中有 176 個屬於「某軌 KEEP、另一軌 SILENT」，
共 31.8 秒，占保留時間（18:12）的 **2.9%**；render 以「191 處靜音事件會以原
始串音留在成品裡」印出來，不靜默吞掉。

判準：**整段沒人要的時間照樣會被剪掉**（那是全軌皆非 KEEP 的 cell，兩層模型
第一層，合軌完全套用得到）。失去的只有第二層的逐軌壓制。2.9% 換掉整條 bus
重混鏈的音質風險，EP18 這一集划算；哪一集串音特別重，就切回 `line=pertrack`。

## 連帶修掉的兩個記錄破口

這次之所以要重新盤查，是因為 MM 說「我記得已經有混音線的了，直接重跑不就好
了」——而 EP18 v4–v7 的 `render.txt` 確實四份都寫著「剪輯路線：混音線」。

1. **`cut.py` 用檔名猜路線**：判斷式是 `args.plan != "cutplan.md"`。EP18 的
   分軌節目單正好就叫 `cutplan.md`，於是四版分軌成品全被標成混音線，而同一
   份 render.txt 上一行印的是 `⚙ line=pertrack`，自己打自己。改成
   `route_label()` 從 ⚙ 讀，讀不到才照 block 前綴判（兩碼＝分軌、單碼＝混音）。
   **標錯的紀錄比沒有紀錄更貴**：它讓人以為某個選項已經試過了。
2. **`semantic_diff` 看不見路線改動**：它比的是 `params`，而 `params` 的值
   限定 `[\d.]+`，`line=`／`audio=` 這種字串鍵根本不在裡面。Drive↔session 對
   照時換一條剪輯路線只會印「內容有差異但不影響剪輯」。改成比 `params_raw`。

兩者都補了回歸測試（`TestRouteLabel`、`TestRouteChangeVisibleInDiff`）。
