# Podcast Cut（剪輯線）

把已建 session 的 podcast 集數，依最新 cutplan 出片。核心是零 LLM 零 token：`cut.py` 一行搞定 Drive 同步＋dry-run＋render＋版本目錄；agent 只在「改了 cutplan 沒效果」「要提案剪輯」時介入。

## 觸發條件

「剪 podcast」「剪 EP18」「照最新 cutplan 出片」「/podcast-cut」。
**不要**觸發 `/good-student`（那是知識點切卡線，同 repo 不同產品）。

## 真相源與角色（先搞懂再動手）

- render 真相源＝`sessions/<slug>/cutplan.md`（ADR 0001）；**MM 人審介面＝Google Drive 集數資料夾的副本**。兩邊會漂，`cut.py` 開頭自動做語意 diff（勾選翻轉／刪除線增減／✂／⚙），詢問預設擇新。
- **人審不可跳過**：cutplan 勾選是 MM 的剪輯決定。agent 只提案（`.cutplan_pending.json`），不落地；文字與時間碼不可改，只准翻勾選、加刪除線與結構行。
- render 參數與剪輯路線住 cutplan 的 `## ⚙` 行（含 `line=pertrack|mixdown`），不住 render.txt——cutplan 是參數真相源，重跑時它是輸入不只是紀錄。

## 流程

1. **定位 session**：repo＝`~/GithubRepo_mm-xyz/good-students-note`，session 在 `sessions/` 用 EP 編號 grep；Drive 對應目錄看 `sessions/<slug>/.drive_dir`（cut.py 第一次配對後自動記住，slug 與 Drive 資料夾名對不上時用 `--drive` 指定）。
2. **出片（標準路徑，一行）**：
   ```bash
   cd ~/GithubRepo_mm-xyz/good-students-note
   python3 scripts/audio/cut.py --session sessions/<slug>
   ```
   分軌線加 `--plan cutplan.pertrack.md`；只驗不剪 `--check`；不推 Drive `--no-push`；非互動 `--yes`；AI 有介入剪輯決策時**必加 `--ai`**（版本目錄掛 `-AI`）。
   它自動做：Drive↔session cutplan 語意 diff → dry-run 摘要 → render（檔名自動遞增 vN 不覆蓋）→ local＋Drive 各建 `vN_時戳/`（mp3＋當次 cutplan 快照＋render.txt）→ 工作版 cutplan 同步回 Drive。
3. **給 diff 片段**：二剪起用 `scripts/audio/diff_clips.py` 只切改動處 ±5s 給 MM 聽，不產文檔。修 cutplan → 再跑 cut.py，迴圈到定稿。
4. **新集數（尚無 session）**：`python3 scripts/session.py new <audio>` 走初剪全自動（本地 whisper→diarize→prosody→cutplan），cutplan 放集數資料夾根（session 與 Drive 同一位置，不進 `_meta/`），交 MM 人審後回到步驟 2。標準流程全文見 good-students-note ADR 0015。

## cutplan 標記速查

`- [ ]`＝剪整句｜`~~刪除線~~`＝剪字｜`## ✂ 起-迄`＝手動剪空白｜G 列勾＝保留留白（剪停頓是常態、留白才標）｜`## 章節`＝chapters｜`## 🎬`集錦／`## 🎵`BGM／`## ➕`補錄插入（`gain=auto`）｜`## ⚙`＝render 參數。

## 已知坑

- **Drive 路徑 TCC 擋（Operation not permitted）**：tmux 底下連 MM 用 `!` 親跑都會被擋（TCC 歸屬給 tmux server）。處置＝**用 orca 開一個 terminal/agent 跑**（MM 2026-09-04 拍板）；Orca 也沒授權才請 MM 到系統設定→檔案與檔案夾給權限。別誤判成 Drive 沒掛載。
- 改 `scripts/audio/` 任何檔，改完必跑 `scripts/tests/run_all.sh`（真音訊回歸，假資料驗不出「算得對但對到錯的字」）。
- MM 說「有個小聲音」「聽起來是人聲」先當真去量，不要急著套自己的假設（EP18 room-tone 鬼影事故，ADR 0017）。
- session cutplan 中途重生成過會讓 block 編號位移：移植 MM 編輯用文字序列對齊＋時間範圍 fallback，勿按 block id 硬對。

## 輸出

`sessions/<slug>/final_cut_vN.mp3`＋local 與 Drive 各一份 `vN_<時戳>[-AI]/` 版本目錄（mp3＋cutplan 快照＋render.txt）。回報 MM：版本號、剪了什麼（dry-run 摘要）、Drive 路徑。
