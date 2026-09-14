# Podcast Cut（剪輯線）

把已建 session 的 podcast 集數，依最新 cutplan 出片。核心是零 LLM 零 token：`cut.py` 一行搞定 Drive 同步＋dry-run＋render＋版本目錄；agent 只在「改了 cutplan 沒效果」「要提案剪輯」時介入。

## 觸發條件

「剪 podcast」「剪 EP18」「照最新 cutplan 出片」「/podcast-cut」。
**不要**觸發 `/good-student`（那是知識點切卡線，同 repo 不同產品）。

## 預設走合軌（2026-09-14 MM 拍板）

**所有集數預設 `line=mixdown`**，音源＝錄音機自己的合軌（session 的 `source.<ext>`）。
MM 實聽分軌線的結論是「不太能用，每一線切合很怪」；EP18 也是同一個結論（ADR-2026-09-06），兩集實際出片最後都走合軌。

有 `tracks/` 分軌素材時**仍然**會跑 ingest 與 `diarize --from-tracks`——前者是合軌的來源（錄音機只給分軌時，`source.wav` 就是 ingest 合出來的），後者只提升講者歸屬準確度、不碰音質。**停掉的是逐軌節目單**（`pertrack_blocks`），那才是接縫的來源。

分軌線沒有退場（卡 #681 是 pending 不是丟棄），但要明寫才走：`precut.py --line pertrack`、cutplan `## ⚙ line=pertrack`。節目單是逐軌格式卻沒明寫 `line=pertrack` 會 FAIL，不靜默走合軌。

## 真相源與角色（先搞懂再動手）

- render 真相源＝session 的 `_asset/cutplan.md`（ADR 0001）；**MM 人審介面＝Google Drive 集數資料夾的副本**。兩邊會漂，`cut.py` 開頭自動做語意 diff（勾選翻轉／刪除線增減／✂／⚙），詢問預設擇新。
- **人審不可跳過**：cutplan 勾選是 MM 的剪輯決定。agent 只提案，文字與時間碼不可改，只准翻勾選、加刪除線與結構行。提案完刪掉 `.cutplan_pending.json` 才能 render。
- render 參數與剪輯路線住 cutplan 的 `## ⚙` 行，不住 render.txt——cutplan 是參數真相源，重跑時它是輸入不只是紀錄。

## session 的標準形狀（ADR-2026-09-11-session-layout-standard）

```
sessions/{錄音日}_{名字}/
├── _asset/            管線工作檔（transcript / words / prosody / cutplan / cut_map / metadata）
├── _meta/             人看的伴隨檔（chapters.txt / highlights.md / 文案 / 封面）
├── source.<ext>       原始錄音（symlink，保留原副檔名）
└── vN_<時戳>[-AI]/    每一版：mp3 ＋ cutplan 快照 ＋ render.txt ＋ diff/
```

日期是**錄音日**（從檔名解析，解不出來用 mtime），不是今天。名字用 `--slug` 給——錄音機檔名（`2026_0907_1917`）沒有意義。

要換工作檔位置只改 `scripts/audio/session_paths.py` 的 `WORK_SUBDIR` 一個值，然後跑 `tidy_session.py --migrate-work`（fallback 只保證讀得到舊位置，不會自己搬）。

## 流程

1. **定位 session**：repo＝`~/GithubRepo_mm-xyz/good-students-note`，session 在 `sessions/` 用 EP 編號 grep；Drive 對應目錄看 `sessions/<slug>/.drive_dir`（`cut.py` 第一次配對後自動記住）。`--drive` 指到段落子夾會自動上修到集數資料夾——cutplan 一律住集數根，放深一層 Apps Script 編輯器會整集選不到且不報錯。
2. **出片（標準路徑，一行）**：
   ```bash
   cd ~/GithubRepo_mm-xyz/good-students-note
   python3 scripts/audio/cut.py --session sessions/<slug>
   ```
   只驗不剪 `--check`；不推 Drive `--no-push`；非互動 `--yes`；AI 有介入剪輯決策時**必加 `--ai`**（版本目錄掛 `-AI`）。分軌線才加 `--plan cutplan.pertrack.md`。
   它自動做：Drive↔session cutplan 語意 diff → dry-run 摘要 → render → local＋Drive 各建 `vN_時戳/` → 工作版 cutplan 同步回 Drive → 刪掉 session 根的工作檔（版本目錄已有逐 byte 相同的快照）。
3. **給 diff 片段**：二剪起用 `scripts/audio/diff_clips.py` 只切改動處 ±5s 給 MM 聽，不產文檔。片段自動落在該版的 `vN_<時戳>/diff/` 並鏡像到 Drive 同名版本目錄。修 cutplan → 再跑 cut.py，迴圈到定稿。
4. **新集數（尚無 session）**：
   ```bash
   python3 scripts/session.py new <audio> --cut --slug EP19-0-包棟介紹 --num-speakers 3
   ```
   走初剪全自動（本地 whisper→diarize→prosody→cutplan）。或用 `scripts/audio/precut.py` 對已建 session 補跑。交 MM 人審後回到步驟 2。標準流程全文見 ADR 0015。
5. **定稿歸檔**：`finalize.py --session <目錄> --final v12`（dry-run 預設，`--apply` 才搬）。根只留定稿版本目錄／`raw/`／原始錄音，其餘進 `_archive/`，工作檔進 `_archive/pipeline/`。要重剪 `--restore` 搬回。同一支也吃 Drive 集數資料夾。

## cutplan 標記速查

`- [ ]`＝剪整句｜`~~刪除線~~`＝剪字｜`## ✂ 起-迄`＝手動剪空白｜G 列勾＝保留留白（剪停頓是常態、留白才標）｜`## 章節`＝chapters｜`## 🎬`集錦／`## 🎵`BGM／`## ➕`補錄插入（`gain=auto`）／`## 🔇`室噪留白｜`## ⚙`＝render 參數。

## 已知坑

- **Drive 路徑 TCC 擋（Operation not permitted）**：tmux 底下連 MM 用 `!` 親跑都會被擋（TCC 歸屬給 tmux server）。處置＝**用 orca 開一個 terminal/agent 跑**（MM 2026-09-04 拍板）。別誤判成 Drive 沒掛載。
- 改 `scripts/audio/` 任何檔，改完必跑 `scripts/tests/run_all.sh`（真音訊回歸，假資料驗不出「算得對但對到錯的字」）。
- **本地 whisper 不是確定性的**：同一個音檔、同樣參數跑兩次，辨識結果會不同（EP19 實測差 207 行、cues 446→435）。所以「重跑一次比對輸出」不能當回歸驗收，只有測試算數。
- **驗章節要數相異時間碼**，不是數行數：`[render] chapters.txt: N 章` 只數行數，永遠會綠。章節數 ≠ 相異時間碼數時 render 會另外出聲。
- **`## 🔇` 需要一段乾淨室噪窗**，講話太滿的集數湊不出來會 FAIL（EP19 最長只有 0.3s）。卡 #1000。
- MM 說「有個小聲音」「聽起來是人聲」先當真去量，不要急著套自己的假設（EP18 room-tone 鬼影事故，ADR 0017）。
- session cutplan 中途重生成過會讓 block 編號位移，**而且會蓋掉 AI 提案**：移植 MM 編輯用文字序列對齊＋時間範圍 fallback，勿按 block id 硬對；重生成前先確認版本目錄或 Drive 有快照可還原。

## 輸出

local 與 Drive 各一份 `vN_<時戳>[-AI]/`（mp3＋cutplan 快照＋render.txt，二剪起含 `diff/`）。
回報 MM：版本號、剪了什麼（dry-run 摘要）、Drive 路徑。
