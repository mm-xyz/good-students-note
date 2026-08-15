# 分軌線底噪方案評審（luna，2026-08-11）

以下採信 brief 已量到的 EP18 數據，並以 `pertrack_render.py`、`pertrack_cells.py`、`render_cut.py` 與 `PERTRACK_IMPL.md` 的現況核對實作前提。核心判斷是：這次主要問題不是把某一個人的聲音歸錯，而是錄音底噪被整條鏈一起 loudnorm 拉高；因此降噪應是分軌 render 層的保守處理，不能反過來改寫人審語意。

## 1．降底噪的處置順序

### 結論

自適應 downward expander 應放在「逐軌、進 speech bus 之前」，位置是高通／輕量 EQ 與每軌電平校準之後、cell 的增益包絡與 pan 之前；不應放在 speech bus，因為 bus 已經失去「這個能量屬於哪支麥克風」的資訊。它也不應先改變 cells 的分析波形：歸屬與人審候選仍用原始或只做頻帶整理的訊號判定，render 時才套降噪。高通和 expander 都不能成為自動刪除 KEEP 的權威，明確 KEEP 的笑聲、氣音或非詞彙出聲只能被保守衰減，明確 SILENT 才能全靜音。

閘門確實可能吃掉氣音、笑聲尾巴與「嗯」，所以不採硬 gate；採有 lookahead、hangover、短 attack 的 downward expander，讓瞬態與尾巴先過，再把持續底噪壓低。門檻用每軌自己的統計，不用固定全集 dB：以真靜音窗短窗 RMS 的 P99.5 加安全裕度作絕對下限，再以高信心他人獨講窗的串音殘差 P99.5 作 excess 門檻，兩者取較嚴格者；duration 則由負樣本誤判 run 的 P99.5 決定。

### 實作切點與風險

- `pertrack_render.py:229–298` 的 `mix_ranges()` 正是逐軌讀取、套 static gain、套 envelope、再混成 bus 的切點；expander 應在這段逐軌處理內完成，不能塞進共用的 `render_cut.py:613–760`。
- brief 已驗證 HPF100 只有約 1.2dB 底噪改善，HPF100＋`afftdn` 也只有約 2.8dB；因此高通與頻譜降噪只能當前置整理，不能期待它們解決句間底噪。現況 `pertrack_render.py:296–297` 的 `highpass` 是 no-op，這是待補的實作缺口，不是已存在的效果。
- expander 的 detector 可參考線性功率相加的 bleed 預測，而不是把多個串音取最大值；但 detector 只負責「降多少」，不負責推翻人審標記。非詞彙候選若被人勾回，應保留其短事件的增益包絡，不能因為沒有文字就被 gate 吃掉。
- 主要風險是 pumping 與尾音變薄，尤其 Sarah 的原始訊噪比只有 26dB。驗收要同時看靜音 P1／P10、語音 P90、語音尾端保留率與非詞彙候選保留率；只看底噪最低值會把「靜音很好但聲音被吃掉」誤判成成功。

## 2．句間短空隙

### 結論

短空隙應保留時間軸，但不應像現行 `apply_mask()` 那樣一律把不超過 `--max-pause` 的整段升成前一位或後一位的 full KEEP。較穩的規則是：同一講者句內的空隙保留前一位約 50–150ms 的 hangover，之後由 expander 退到 room tone／低量底噪；換講者的空隙則保留原時間並做短交接，不把整段交給其中一支麥克風。超過 0.9s 的無主停頓才進行時間收緊，保留約 0.6s 的呼吸空間，且 G 列明確勾選時不得自動收緊。

這樣可避免講話變成連珠炮，又不會讓每個字間縫都維持一支開著的麥克風。`0.9s` 應是「何時可以壓縮時間」的上限，不應同時被當成「保持前一位軌道 full KEEP」的上限；時間保留與訊號是否持續開麥必須拆成兩個決策。

### 實作切點與風險

- `pertrack_cells.py:189–225` 目前 `apply_mask()` 會把短的 no-KEEP run 升成前／後講者的 KEEP，之後 `track_envelopes()` 再把它做成 0dB。這會把短空隙的「留白保護」與「開麥」混在一起，應改成由時間層保留、由逐軌 envelope／expander 決定音量。
- 判斷句內空隙或換手，應看相鄰 canonical block 的講者、word 邊界及 aggregate 能量，不用單一軌的谷底。若左右講者不同，前一軌只做 hangover，後一軌從字頭前做 lookahead；不要用一個 0.9s 門檻涵蓋兩種情況。
- 風險是「保留太多」使底噪持續，「收太多」使語速變急。驗收應統計原始停頓長度分布、成品停頓長度分布與每分鐘被壓縮的停頓數，並在 Sarah 最差的區段單獨聽檢。

## 3．MM 的單一主軌承載空白

### 結論

採用「單一 carrier」的目的，但不採用固定 MIC1；每集從真靜音區以底噪電平、短窗變異與頻譜穩定度選出最乾淨且最穩定的一軌，讓它單獨產生 room-tone bed，其他人的聲音仍由各自分軌承載。room-tone bed 不拿掉，因為它解的是關麥時的音色與噪聲地板抽動，不是降低總 RMS；真正的降噪仍由逐軌 expander 與 activity mask 完成。

這是部分採用 MM 提案，而不是把一支真實麥克風永久當主聲道。固定 MIC1 沒有跨集保證，且單一麥克風可能有衣物摩擦、風扇或局部電流聲；每集自動選擇才符合現有量測前提。若該集沒有穩定的真靜音樣本，應停用 bed 並保留 expander 的結果，不要把一段帶突發噪音的軌硬鋪滿全集。

### 實作切點與風險

- 現況 `pertrack_render.py:103–118` 的 `find_quiet_spans()` 用三軌能量和找靜音，`146–196` 的 `build_room_tone()` 也把三軌樣本相加；這不是單一主軌 carrier，需在設計上明確改成「先選軌，再只從該軌取樣」。
- carrier 的選擇應在輸出前印出可追溯的軌名、取樣窗、P1／P10／中位與穩定度；room-tone 的目標電平不可高於實測底噪再加一個固定的大幅增益，否則 loudnorm 仍會把它一併抬高。
- 風險是單一 carrier 的音色與三支麥克風不完全相同。它是消除換手抽動的背景，不是要讓聽者辨認成另一個人的聲音，因此要以連續性與突發噪聲數量驗收，而不是只比較 bed 的單點 dB。

## 4．表格＋select 的人審介面

### 結論

不值得為了這次底噪問題把 1811 列的 cutplan 全面改成表格；控制力增加，但閱讀與驗證成本也會增加，還會把既有的勾選／刪除線資產帶進格式遷移風險。現行逐軌 block 已經能提供每軌 KEEP／SILENT 控制，較好的做法是保留 markdown 作為唯一真相源，只另外提供按 atomic cell 分組的檢視投影，優先顯示同時有多軌能量、歸屬不確定與非詞彙候選的列。

select 若要存在，只能是這個投影的操作介面，最後仍寫回穩定的既有 block ID、checkbox 與刪除線語意；不能讓表格列號或排序成為新的識別碼。這樣 MM 可以在少量高價值重疊區取得選軌控制，而不必逐一閱讀所有沒有歧義的普通 block，也不需要破壞原本的審稿格式。

### 實作切點與風險

- 顯示層至少要同時列出來源時間、各軌能量／相對串音 excess、canonical 文字、目前 checkbox、刪除線與建議狀態；否則 select 只是假象控制力。
- 表格輸入與 markdown 之間必須做雙向驗證：block ID 不得遺失、文字不得被改寫、刪除線不得被複製到別軌。這也符合 `pertrack_render.py:304–346` 對人審標記與 canonical words 的分工。
- 風險是表格把「建議的非詞彙事件」誤看成正式文字。候選應預設不勾，且低信心候選放折疊區；這次先做檢視投影即可，不應讓新介面阻塞底噪修正。

## 5．不要動的紅線

### 結論

五條紅線全部保留：checkbox 與刪除線是人審資產，降噪只能是出片時的衍生處理，不能自動改寫或重新解讀它們；沒有 `tracks` 的混音線必須維持 `source.wav` 的既有路徑；整條方案只用本地 Python／ffmpeg／既有資料，不能引入雲端 API。對明確 SILENT 的區間仍可全靜音，對 KEEP 的區間最多做保守 expander，不得因為底噪高就偷偷剪掉時間或文字。

新增的 high-pass、expander、單一 carrier bed 都應封裝在 pertrack 分支，不能放到 `render_cut.py` 共用的 `run_ffmpeg()` 後處理；否則會把 EP15 那類只有合軌的 session 一起改音色。mixdown 線仍可共用既有的 speech bus 後 `dynaudnorm`、BGM overlay、`loudnorm` 順序，但分軌線的降噪必須先在 speech bus 之前完成。

### 實作切點與風險

- `render_cut.py:868–883` 已有 pertrack／mixdown 分流；應以這個分流為守門，並增加「無 tracks 時輸出鏈與既有結果一致」的回歸驗證。不能只靠 CLI 預設值保證不影響混音線。
- `render_cut.py:1279–1338` 目前只在 pertrack 分支建立 room-tone、track envelope 與 speech bus；這是放新處理最安全的邊界。`render_cut.py:1372–1374` 之後的共用 loudnorm 仍會抬高剩餘底噪，所以成品驗收必須量 loudnorm 前後的 P1／P10，而非只看中間 bus。
- 人審原檔不應被「整理」或重排；若要保存 gate 門檻、carrier 選擇與候選統計，放在獨立的衍生報告或 render log，不回寫 cutplan。所有分析必須可離線重跑，並保留沒有雲端 API 的限制。

