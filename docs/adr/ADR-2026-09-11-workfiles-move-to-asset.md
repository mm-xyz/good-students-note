# ADR-2026-09-11-workfiles-move-to-asset — 工作檔搬進 `_asset/`，根目錄只留人要看的

- 識別碼：ADR-2026-09-11-workfiles-move-to-asset
- 日期：2026-09-11
- 狀態：已採納
- 相關：ADR-2026-09-11-session-paths-single-source（前一步：解耦）、
  ADR 0011（session 目錄分類）、ADR-2026-09-11-diff-lives-in-version-dir

## 脈絡

前一個 ADR 做完解耦（85 處路徑吃 `work_dir()`），但 `WORK_SUBDIR` 留成 `""`，
檔案一個都沒動。MM 看到 session 根還是一片檔案：「我看都沒有收進去 _asset 之類的啊」。

解耦本身不是交付物，根目錄乾淨才是。本 ADR 把值打開。

## 決策

`WORK_SUBDIR = "_asset"`。session 根目錄收斂成四項：

```
_asset/       管線工作檔（transcript / words / prosody / cutplan / cut_map / metadata…）
_meta/        人看的伴隨檔（highlights / chapters / 文案 / 封面）
source.wav    原始錄音（symlink）
vN_<時戳>/    每一版成品
```

### 三個不搬的例外，各有理由

- **`source.*`** — 這一集的素材入口，而且 `render_cut` 的室噪直接吃
  `sdir/source.wav`。留在根看得到。
- **`cleaned.md`** — 文件線（PDF/EPUB/TXT）的**交付物**，一本書轉成的 markdown
  知識庫。它在 `PIPELINE_KEEP` 裡是為了 tidy 不亂搬，不代表它是中間檔；
  藏進 `_asset/` 等於把成品藏起來。獨立清單 `STAYS_VISIBLE` 標記。
- **`.drive_dir`** — 隱藏檔，配對記錄，本來就不在工作檔清單裡。

### 產出端直接寫進最終位置

`highlights.md`（prosody）與 `chapters.txt`（render）原本產在根、等 `tidy_session.py`
歸位。改成 `ensure_meta_dir()` 直接寫 `_meta/`——**「根目錄乾淨」不能是一個要定期
執行的動作**，否則每跑一次 prosody 根就髒一次。

### 新 session 先建好家

`session.py` 建立 session 時呼叫 `ensure_work_dir()`。`work_dir()` 的 fallback 是
「子資料夾不存在就用根」，新 session 沒先建就會整批寫進根，然後**永遠**走 fallback
——等於這個設定對新集數完全不生效，而且不會有任何錯誤訊息。

### 遷移

`tidy_session.py --migrate-work`（dry-run 預設）。fallback 只保證讀得到舊位置，
不會自己搬；改了 `WORK_SUBDIR` 不跑遷移，等於什麼都沒發生。

## 測試抓不到這件事，所以靠實跑

**fallback 讓測試失去鑑別力**：測試的 fixture 建在 tempdir 根，`_asset/` 不存在 →
`work_dir()` 退回根 → 測試照樣全綠，無論路徑有沒有改對。

所以驗收方式是**把 EP19-0 真的遷移掉再跑一次管線**。檔案真的不在根了，漏掉的路徑
就會當場 FileNotFound。這樣抓到三個機械替換漏掉的：

1. **`cut.py` 缺 import** — 它唯一那處 `ddir / "cutplan.md"` 是 Drive 路徑、判定
   不改，所以補 import 的腳本跳過了它；後來手動加的 `work_dir(sdir) / args.plan`
   就沒有對應的 import。`NameError` 當場炸。
2. **`diarize.py` 讀寫不一致** — `wav = session_dir / WAV_NAME` 用的是**常數**，
   字面白名單抓不到。結果讀 `_asset/audio16k.wav`、寫根目錄的 `audio16k.wav`，
   每跑一次 prosody 根目錄就多一個檔。這種 bug 不會報錯，只會靜靜長出垃圾。
3. **文件線的 `cleaned.md` / `metadata.json`** — 搬家影響的不只 podcast 線，
   `test_session_doc_line.py` 四項當場紅。

`sdir / args.plan` 這類**變數形式**的路徑也是機械替換的盲區，另外盤了一輪補上
14 處。

### 最難的一個：`ensure_work_dir` 的副作用讓同一次執行前後不一致

`work_dir()` 的 fallback 判斷的是「子資料夾存不存在」，所以**誰在執行途中把它建
出來，誰就改變了後半段的路徑**。實際發生的順序：

1. precut 的 transcribe 寫 `transcript.srt` — 此時 `_asset/` 不存在，寫進根
2. diarize 的 `ensure_wav` 呼叫 `ensure_work_dir()` — **`_asset/` 被建出來**
3. 下一行 `pick_transcript()` 的 `work_dir()` 現在指向 `_asset/` — 找不到第 1 步
   寫的檔，`FileNotFoundError`

修法是把規則講死：**`ensure_work_dir()` 只有 session 建立與遷移可以呼叫，管線執行
途中一律用 `work_dir()`**。寫進該函式的 docstring，不靠下一個人記得。

這類 bug 單元測試抓不到（fallback 讓它們全綠），是真音訊 e2e 抓到的。

## 後果

- 好：session 根四項，每跑一次管線都維持乾淨，不需要定期整理。
- 好：以後要再換位置仍然只改 `WORK_SUBDIR`。
- **代價：任何寫死 `sessions/<slug>/words.json` 的外部消費者都會斷**——本 repo
  以外的 script、n8n flow、其他 repo 的工具不在這次盤點範圍內。fallback 救不了
  它們（檔案是真的搬走了，不是「找不到就退回」）。
- 代價：`cleaned.md` 留在根是特例，後續若有別的「交付物型」工作檔要跟著加進
  `STAYS_VISIBLE`，這個清單會慢慢長出來。
