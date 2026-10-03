# 報告：`line=mixdown audio=tracks`（合軌決定＋分軌出聲）

- 日期：2026-10-03
- 分支：`worktree-agent-a2341aa25fa493779`（未 push、未 merge）
- 決策紀錄：`docs/adr/ADR-2026-10-03-audio-tracks-line.md`（含建議的 SKILL 段落）

## 結論

- 新路線已實作並出片。EP22 成品 `tracks_test.mp3` 長 43:28，時間軸跟 `line=mixdown` 逐毫秒相同（56 段 dump-ranges 完全一致）。
- 測試全綠：音訊回歸 29 個檔、598 項（新增 38 項）；編輯器 93 項（新增 15 項）。8 種注入失敗全部會讓測試變紅。
- e2e 抓到並修好一個真問題：➕ 補錄在分軌 bus 上電平對錯，原本比正片大聲 3.4 LU，修正後只差 +0.1 LU。
- **待 MM 實聽**：分軌 bus 的音色；預設只開一軌在搶話段落的效果。

## 欄位格式

行尾一欄 ` ⟦軌名●/○ …⟧`。● 代表出聲，○ 代表壓 −27 dB。軌的順序等於 `tracks/` 檔名排序，軌名是檔名去掉數字前綴。⚙ 行要補上 `audio=tracks`。

```
## ⚙ line=mixdown template=水星貓的生活實驗室_v1 audio=tracks
- [x] B0024 [0:41–0:44] [Mars] Fuji Rock是一個日本很有名的音樂技。 ← MM 11:07 分軌版人審 ⟦Mars● Sarah○ Kin○⟧
- [ ] G0001 [5:09–5:11] 🔊 聲音事件 1.3s(…) ⟦Mars● Sarah● Kin●⟧
- [ ] S0001 [0:00–0:03] [Mars] 主持人:「戀愛腦嗎?」,      ← ➕ 補錄的 S 列不加欄
```

這個格式怎麼滿足 (a)–(e) 五項要求，見 ADR 的「欄位格式」一節。重點如下：

- 軌欄錨在行尾，每個解析器都先把它切掉，文字驗證照舊。
- 沒有軌欄的節目單，行為完全不變。
- 編輯器只能翻 ●／○。
- B#### 編號不變。
- 加欄工具冪等，不會覆蓋已有的欄位。

## 改了哪些檔

| 檔案 | 內容 |
| :--- | :--- |
| `scripts/audio/tracks_columns.py`（新增） | 加欄 CLI（`--session`、`--dry-run`、`--plan`）；格式解析與輸出；校準後預設值；render 用的 `on_intervals`（處理 hold、lead、重疊）與 `segment_envelopes`（換算到 bus 時間軸） |
| `scripts/audio/render_cut.py` | `parse_program` 先切軌欄，block item 多一個 `tracks` 欄位；新增 `audio=tracks` 路線與護欄；改用分軌 bus 混音，重用 `pertrack_render.mix_ranges`、`measure_static_gains`、`measure_track_offset`；新增 `insert_gain_db`；新旋鈕 `--tracks-lead`（也可寫在 ⚙）和 `--keep-tracks-bus` |
| `scripts/audio/cut.py` | `route_label` 新增「合軌決定＋分軌音源」；`semantic_diff` 新增「軌欄變動」 |
| `scripts/audio/copy_prompt_build.py`、`migrate_marks.py`、`fillers_local.py` | 軌欄不進逐字稿、字元流或 LLM 輸入；寫回時原樣保留 |
| `scripts/cutplan-editor/cutplan-core.js`（＋重新產生 `cutplan-core-inline.html`） | `tracksRaw` 欄位，round-trip 逐 byte 一致；新增 `parseTracks`、`hasTracksAudio`、`toggleTrack(WithHistory)`；`peekHistory` 帶 `kind`；新增 `findIllegalEdit`（護欄規則從 Code.gs 搬過來，並新增軌欄規則） |
| `scripts/cutplan-editor/Code.gs` | `findIllegalEdit_` 改成直接呼叫 core |
| `scripts/cutplan-editor/Index.html` | ⚙ 有 `audio=tracks` 時，每張卡片多一排 22 px 的軌開關；復原鈕貼在開關旁邊 |
| `scripts/cutplan-editor/README.md` | 說明第二個開放的編輯口 |
| 測試 | 新增 `test_tracks_columns.py`（21 項）、`test_render_tracks.py`（13 項，含 EP22 真音訊）、`cutplan-editor/tests/tracks.test.js`（15 項）；擴充 `test_cut.py`（+2）、`test_copy_prompt_build.py`（+1）、`test_resegment_migrate.py`（+1） |
| 文件 | ADR、本報告 |

Commits（依序）：

- `c0320c4`：音訊端
- `303f725`：編輯器
- `9675715`：➕ 補錄 gain 修正
- 最後一筆：文件

## 測試結果（原文摘要）

```
scripts/tests/run_all.sh
PASS  scripts/tests/test_render_cut.py — Ran 100 tests
PASS  scripts/tests/test_render_tracks.py — Ran 13 tests
PASS  scripts/tests/test_tracks_columns.py — Ran 21 tests
PASS  scripts/tests/test_cut.py — Ran 39 tests
PASS  scripts/tests/test_copy_prompt_build.py — Ran 15 tests
PASS  scripts/tests/test_resegment_migrate.py — Ran 21 tests
…(其餘 23 個檔全 PASS)
SKIP  文件線測試 — .venv-doc 不存在(既有狀態)
✅ 全部測試通過          → 29 個檔 / 598 項,失敗 0

node --test "scripts/cutplan-editor/tests/**/*.test.js"
# tests 93  # pass 93  # fail 0     (含 inline-sync)
```

改動前的基準也是全綠：27 個檔，編輯器 78 項。

真音訊斷言是 `TestRealAudioEP22`：從 EP22 session 裁出 60–100 s 的窗，跑完整條 render，再用最小平方法從 bus 反解各軌增益。

- Mars：只開 Kin 的 block 是 0.0231，三軌全開的 block 是 0.518，差 **−27.00 dB**。
- Kin：兩個 block 相差 0.00 dB。
- 時間軸：`--dump-ranges` 跟 `line=mixdown` 相同。

session 不在本機時，這項測試會 skip。

合成音測試量的是真的出片成品（wav），結果如下：

- Kin 0.00 dB，Mars −27.00 dB，Sarah −27.00 dB。
- 切換點相鄰樣本差 ≤ 穩態的 1.05 倍，也就是沒有 click。

UI 在真的瀏覽器裡驗過。Playwright MCP 被其他 session 佔用，改用 headless Chrome，對 Index.html 加 stub 跑自測：

- 開關 22×22 px，跟列勾選同大小。
- 切換後會出現復原鈕，按下能還原。
- 存檔經過 `findIllegalEdit` 放行，存下的行正確。
- 390 px 寬的截圖排版正常。

## 注入失敗驗證

做法：用 `scratchpad/probe/mutate.py` 一次改一處關鍵實作，跑對應的測試，再把檔案逐 byte 還原；最後 `git status` 是乾淨的。

| 注入 | 結果 |
| :--- | :--- |
| M1 包絡不壓低（沒勾的軌也是 0 dB） | 紅（合成成品 −27 dB 與 EP22 真音訊兩項） |
| M2 拿掉 raised-cosine（gate_fade=0） | 紅（`test_switch_has_no_click`） |
| M3 `parse_program` 不切軌欄 | 紅（5 項，含文字驗證、EP22） |
| M4 預設改比原始 RMS | 紅（`test_calibrated_choice_beats_raw_rms`、CLI 合成 session） |
| M5 拿掉 hold | 紅（3 項 `on_intervals`／包絡） |
| M6 加欄工具會覆蓋既有欄位 | 紅（冪等、不覆蓋人工、錯軌名，共 4 項） |
| M7 編輯器護欄不檢查軌欄 | 紅（3 項，含 vm 載入的 Code.gs `saveCutplan`） |
| M8 有軌欄但沒 `audio=tracks` 卻不擋 | 紅（`test_columns_without_audio_tracks_fail`） |

## e2e：EP22 分軌 session

session 路徑：`sessions/2026-10-02_EP22-初級大人的指南針-建立原則與底線-分軌`

1. 備份 `_asset/cutplan.before-tracks.md`，經 `cmp` 確認逐 byte 相同。
2. 用備份跑 `line=mixdown` 的 dry-run。結果：57 segments，語音 40:49，原始長度 50:42，刪除線 6 處，停頓收緊 24 處，➕ 137.8 s（77 個 S block）。
3. 跑 `tracks_columns.py --session …`，耗時 3 秒。
   - 校準值（底噪／講話電平）：Mars −80.2／−31.8 dB，Sarah −65.8／−32.3 dB，Kin −63.4／−27.0 dB。
   - 新加軌欄 2038 列（2027 個 B 列＋11 個 G 列），⚙ 補上 `audio=tracks`。
   - 驗證：跟備份逐行比對，除了軌欄和 ⚙ 之外 **0 處差異**。勾選數相同，章節、刪除線、➕ 全部保留。
4. `audio=tracks` 的 dry-run：**dump-ranges 與第 2 步完全相同（56 段）**。
5. 出片：`render_cut.py --session <EP22> --out <EP22>/_asset/tracks_test.mp3`。worktree 沒有音樂檔（被 gitignore），所以另加 `--material-root <主樹>/shared-material`。
   - 成品長 **43:28**（2608.94 s），跟合軌參考片一樣長。
   - 耗時約 118 秒。
   - static gain：Mars +4.3，Sarah −0.8，Kin −3.6 dB。
   - 時間對齊：Mars −0.07，Sarah 0.00，Kin +0.43 ms。
   - bus peak −6.5 dBFS，削頂 0 樣本。
   - 章節 11 個。
6. 各軌開關統計：
   - B 列只開一軌：**2027**。
   - 開兩軌以上：**0**。
   - 全關：0。
   - G 列 11 個，三軌全開。
   - 預設開哪一軌：Mars 1075、Sarah 429、Kin 523。
   - 跟 diarize 標籤一致 1736／2027（85.6%）。分歧最多的組合是：標 Kin 卻預設 Mars 121 個、Mars→Sarah 45、Sarah→Mars 41、Kin→Sarah 48。
7. ➕ 補錄電平：取成品裡補錄前後各 15 s 的正片比較。

| 版本 | 前 15 s | 補錄 | 後 15 s | 補錄減前後平均 |
| :--- | :--- | :--- | :--- | :--- |
| 合軌參考（`mix_ref.mp3`，在 scratchpad） | −17.4 | −16.4 | −17.3 | +1.0 LU |
| tracks 修正前 | — | — | — | +3.4 LU（對前 4:40 正片） |
| tracks 修正後 | −19.4 | −19.3 | −19.5 | **+0.1 LU** |

session 裡被寫入或新增的檔案：

- `_asset/cutplan.md`：加了軌欄。
- `_asset/cutplan.before-tracks.md`：備份。
- `_asset/tracks_test.mp3`：成品。
- `_asset/cut_map.json`、`_meta/chapters.txt`：render 的附帶產物，原本不存在。最後一次寫入它們的是合軌參考 render；因為時間軸相同，內容等價。

其他東西都沒動。

## 已知限制與待辦

1. **音色要實聽**。分軌 bus 不是錄音機合軌：bus 鄰段約 −38.7 LUFS，合軌約 −22.7。最後雖然由 dynaudnorm 和 loudnorm 拉齊，但跟合軌的音色差異沒有任何測試量得到。
2. **預設值的品質**。「標 Kin 但開 Mars」有 121 個 block，可能是 diarize 標錯，也可能是 Mars 麥收到的串音被校準放大了，目前判斷不了。建議 MM 先抽聽 10 個左右再決定：要嘛調整校準方式（例如 ref 改用該軌主講時段的中位數），要嘛照現況交給人工切換。
3. **預設一律只開一軌**。搶話或笑聲的段落，第二個人會被壓 −27 dB，要人工打開。
4. **➕ 補錄不分軌**，這是依照規格。補錄的 gain 會加上 bus 落差，落差取自 30 s 鄰段實測。
5. **既有缺陷，不在本卡範圍**：`Code.gs` 的護欄要求「行數不變」，但編輯器的「＋ 之後留白」（`## 🔇`）會多插一行，所以**手機上插室噪再存檔會被伺服端拒絕**。這個問題在本次改動之前就存在：原 `findIllegalEdit_` 同樣比對行數，規則搬進 core 時原樣保留，沒有修。建議另開卡處理。
6. **部署**：編輯器改動要由派工者用 clasp 部署，我沒有執行 clasp。部署前 Code.gs 和 cutplan-core.js 必須一起推，因為 `findIllegalEdit` 住在 core 裡。
7. **跨 session 搬勾選**：本次沒有寫「從合軌 session 搬標記」的工具。編號對得上，現有的 `migrate_marks.py` 也已經會把軌欄當尾註處理。
8. 文件線測試（.venv-doc）本機沒有安裝，仍然是 SKIP，這是既有狀態。
