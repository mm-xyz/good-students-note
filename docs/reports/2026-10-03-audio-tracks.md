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

---

## 驗收 FAIL 後的修正（codex luna xhigh 對抗性驗收：F-1／F-2／F-3）

三項都照 TDD 做：先寫測試、確認它紅，再修到綠。

### F-1（High）：🎬 集錦列的軌欄變動要算剪輯決定

- **問題**：`cut.py semantic_diff` 只記錄正文列的軌欄，但 render 會讓 🎬 集錦區的複製列各自套用自己的軌欄。所以只改集錦列時，diff 會印「不影響剪輯」。
- **修法**：集錦列另外用一個 key 記錄，同一個 id 在集錦區第 n 次出現記成 `B0001@🎬n`，不跟正文列互相蓋掉。
- **紅→綠**：`test_cut.py::TestAudioTracks::test_clip_row_track_toggle_is_visible_in_diff`。修之前 FAIL，修之後 PASS。

### F-2（High）：分軌前提必須強制檢查

- **問題**：刪掉 `3_Kin.wav`、把軌欄改成兩軌後，`--dry-run` 仍然 exit 0。
- **修法**：新增 `tracks_columns.validate_tracks()`，`tracks_columns.py`（寫檔之前）和 `render_cut.py`（`audio=tracks` 時）都會呼叫。任何一條不成立就 FAIL，並逐條印出原因，不會補零。檢查四件事：
  - **軌數 = 講者數**：講者數取 speakers.json 的 `num_speakers`（沒有就數 `speakers`）。缺軌、多軌都擋。session 沒有 speakers.json 也 FAIL。
  - **軌名一致**：speakers.json 有 `tracks` 對照時（ingest_tracks 產的），軌名集合必須跟 `tracks/` 推出來的完全相同。diarize 產的 `SPEAKER_00` 這類標籤沒有對照，只比數量。
  - **取樣率**：每一軌都要等於 source.wav。
  - **長度**：每一軌跟 source.wav 的差距要 ≤ `TRACK_LEN_TOL = 0.02 s`。理由寫在程式註解：同一次錄製的多軌實測逐樣本等長（EP22 四個檔都是 3042.915556 s），20 ms 只留給容器或編碼的尾端取整，遠小於任何一個字。
- **軌欄與 tracks/ 的對應**沿用原本的檢查：同名、同順序，每一個 B／G 列都要符合。
- **紅→綠**：
  - `test_tracks_columns.py::TestValidateTracks` 共 10 項：ok、缺 speakers.json、缺軌、多軌、軌名不符、取樣率不符、長度不符、容差內通過、diarize 標籤只比數量、CLI 缺軌時拒絕且不寫檔。
  - `test_render_tracks.py::TestRouteGuards` 新增 3 項：`test_missing_track_with_two_track_columns_fails`（重現驗收時的情境）、`test_track_length_mismatch_fails`、`test_track_sample_rate_mismatch_fails`。
  - 以上修之前全部紅（9 ERROR＋4 FAIL），修之後全綠。
- **真資料**：EP22 照樣通過驗證，`audio=tracks` 的 dry-run 結果跟合軌 dump-ranges 仍然完全相同（56 段）。

### F-3（Medium）：run_all.sh 用錯直譯器

- **修法**：新增 `scripts/tests/pick_python.sh`。依序找 repo 自己的 `.venv-audio/bin/python3.13`，再找 `git rev-parse --git-common-dir` 所在主樹的同一路徑。都找不到就 exit 2 並說明安裝方式，**不會退回系統 python3**。`run_all.sh` 開頭改用它，並印出 `python: <路徑>`，所有音訊測試都用這支跑。原本的 prosody 特例也一併併掉了。
- **紅→綠**：`test_run_all_python.py::TestPickPython`（4 項）：
  - 自己的 venv 優先。
  - worktree 沒有時退回主樹的 venv。
  - 兩邊都沒有時 FAIL，stdout 為空。
  - run_all.sh 確實呼叫 picker，不再有 `py=python3`。
- **`test_precut.py::test_diarize_uses_from_tracks_zero_model` 在 venv 下會失敗：查證結果是測試的假設錯了，程式行為是對的。**
  - `precut.plan_stages` 讓 `diarize --from-tracks`（零模型）跑 `sys.executable`，讓需要模型的轉錄跑 `AUDIO_VENV`。這是刻意的設計：分軌歸屬不需要模型，任何 python 都能跑。
  - 舊測試用「cmd[0] 不含 `.venv-audio`」來代表「沒有寫死模型 venv」。一旦 runner 本身就是 venv 的 python，`sys.executable` 就在 `.venv-audio` 裡，於是出現假紅。
  - 測試已改成直接斷言要鎖的事：diarize 的 `cmd[0] == sys.executable`，同時轉錄那段確實指向 `.venv-audio/bin/python`。改之前在 venv 下紅，改之後在 venv 和系統 python 下都綠。

### 修後測試總結（原文摘要）

```
bash scripts/tests/run_all.sh
python: /Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-audio/bin/python3.13
PASS  scripts/tests/test_cut.py — Ran 40 tests
PASS  scripts/tests/test_precut.py — Ran 32 tests
PASS  scripts/tests/test_render_cut.py — Ran 100 tests
PASS  scripts/tests/test_render_tracks.py — Ran 16 tests
PASS  scripts/tests/test_run_all_python.py — Ran 4 tests
PASS  scripts/tests/test_tracks_columns.py — Ran 31 tests
…(共 30 個檔全 PASS,616 項,失敗 0)
SKIP  文件線測試 — .venv-doc 不存在(既有狀態)
✅ 全部測試通過

node --test "scripts/cutplan-editor/tests/**/*.test.js"
# tests 93  # pass 93  # fail 0
```

---

## 第二輪驗收：F-4（對齊）與 F-5（precut 找主樹 venv）

### F-4（High）：validate_tracks 沒驗對齊

luna 的重現案例：Mars 軌的內容整體後移 441 samples（10 ms），長度不變，原本會被放行。

**修法**：新增 `tracks_columns.measure_alignment()`，由 `validate_tracks()` 呼叫。`tracks_columns.py` 和 render 共用這一條路徑。

- **量法**：每一軌都對 `source.wav` 做 FFT 互相關。均勻取 80 個 0.5 秒的窗，只讀約 40 秒，不讀整條音檔。只採信相關係數 ≥ 0.8 的窗，也就是「這一軌在合軌裡佔主導」的窗；至少要有 3 個這樣的窗，再取中位數。
- **三軌彼此的位移**：取各軌對 source 位移的差值。不拿麥對麥直接做互相關，因為兩支麥之間的串音本來就帶著聲波傳遞的物理延遲（約 3 ms／公尺），會被誤判成檔案沒對齊。

**門檻**（常數註解裡寫了理由）：

| 項目 | 門檻 | 超過時 |
| :--- | :--- | :--- |
| 軌 vs source | `ALIGN_SRC_MAX` = 20 ms | FAIL。門檻內交給 auto offset 補償；已知的錄音機延遲 4.9 ms 會通過，30 ms 會 FAIL |
| 三軌互差 | `ALIGN_SPREAD_MAX` = 2 ms | FAIL |
| 量不到（合格的窗不到 3 個） | — | FAIL，不默默當成 0 |

**(c) 的處理**：明寫 `--track-offset X`，而 X 跟實測差超過 2 ms 時，只**警告、不擋**。

- 走到這一步，實測值已經在 20 ms 門檻內（超過的在前面就 FAIL 了），切點偏幾 ms 會落在 snap 過的靜音裡，聽不出來。
- 明寫 offset 是人的刻意選擇（測試或診斷），所以照他寫的值走。
- `auto` 改用這次量到的值，不再呼叫 `ptr.measure_track_offset`。

**EP22 實測**（相對 source.wav，耗時約 3 秒）：

- Mars −0.07 ms
- Sarah −0.09 ms
- Kin −0.07 ms
- 三軌互差 0.02 ms

三軌實際上就是 sample-aligned，驗證通過。`audio=tracks` dry-run 的 dump-ranges 跟合軌仍然完全相同（56 段）。

**順帶抓到的舊坑**：

- 舊的 `ptr.measure_track_offset` 用 0.55 門檻、6 個固定探點。在 EP22 上它量出 Kin +0.43 ms（被串音帶偏），Sarah 則因為沒有任何探點合格，**默默回傳 0.0**。
- 第一次實作時門檻設 0.4，EP22 量出 Sarah +4.31、Kin +7.01 ms，也是串音造成的。把相關係數門檻拉到 0.8 之後，三軌就一致了。
- 第一輪 e2e 出片時用的時間補償是舊值（Kin +0.43 ms）。這個差距小於 1 ms，不影響第一輪的結論。

**紅→綠的測試**：

- `test_tracks_columns.py::TestAlignment`，共 4 項：
  - `test_luna_repro_one_track_shifted_10ms_fails`
  - `test_common_4_9ms_vs_source_passes_and_is_reported`
  - `test_common_30ms_vs_source_fails`
  - `test_unmeasurable_track_fails`
- `test_render_tracks.py::TestRouteGuards`，共 3 項：
  - `test_misaligned_track_fails_render`（Kin 後移 10 ms）
  - `test_alignment_is_reported`
  - `test_explicit_offset_far_from_measured_warns`

**測試資料的調整**：

- 合成 session 在剪掉的 4–6 秒區段，每一軌輪流加一段寬頻雜訊。純音是週期訊號，互相關會在整數週期上產生歧義，需要一段「這一軌主導」的窗。
- `TestValidateTracks` 的 fixture 從全零改成雜訊。全零的軌現在會被正確判為「量不到」。
- 真音訊測試改用 `balanced_window()`，挑三個人講話秒數最平均的 90 秒窗。原本固定用 60–100 秒那段，那裡 Sarah 和 Kin 都不主導，會被正確判為量不到。

### F-5（已做，改動 15 行）：precut.py 在 worktree 找不到主樹的 .venv-audio

- **修法**：新增 `precut.find_audio_venv()`，先找 repo 自己的 venv，再找 git common dir 所在主樹的 venv；兩邊都沒有就回傳 repo 自己的路徑，讓原本的「`.venv-audio` 不存在」FAIL 照常觸發。
- **紅→綠的測試**：`test_precut.py::TestFindAudioVenv`，共 3 項。
- **連帶影響**：`test_precut_e2e.py` 在 worktree 裡也能找到 venv 了，10 項全綠。

### 修後總結（原文摘要）

```
bash scripts/tests/run_all.sh
python: /Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-audio/bin/python3.13
PASS  scripts/tests/test_precut.py — Ran 35 tests
PASS  scripts/tests/test_precut_e2e.py — Ran 10 tests
PASS  scripts/tests/test_render_tracks.py — Ran 19 tests
PASS  scripts/tests/test_tracks_columns.py — Ran 35 tests
…(共 30 個檔全 PASS,626 項,失敗 0)
✅ 全部測試通過

node --test "scripts/cutplan-editor/tests/**/*.test.js"
# tests 93  # pass 93  # fail 0
```
