## 1．做了什麼

**任務 A：補錄插入**

- 新增 `## 🎙️` 外部語音語法、參數驗證、逐行播放順序、電平自動對齊、淡入淡出、頻帶清理及 `cut_map.json` 追蹤資料。
- 原有 block 的 Markdown／JSON／SRT 三方驗證保持不變，補錄只獨立驗證檔案與裁切範圍。
- 使用補錄 `3．250～41．950` 秒，插在 B0642 後、B0643 前。
- 新成品：[final_cut_v5.mp3](/Users/marslo/GithubRepo_mm-xyz/gsn-sol/sessions/2026-07-27_EP16-不要跟工作談戀愛/v5_20260810-2329-AI/final_cut_v5.mp3)
- 成品長度 `1818．057` 秒，較 v4 增加 `36．791` 秒。
- SHA256：`dbe9077979d7824e6f7e31e06266d50a6a69159a7be8760a6bab294d9911280f`
- 整體響度 `－17．0 LUFS`，LRA `7．5 LU`，true peak `－1．4 dBFS`。
- 詳細紀錄：[render.txt](/Users/marslo/GithubRepo_mm-xyz/gsn-sol/sessions/2026-07-27_EP16-不要跟工作談戀愛/v5_20260810-2329-AI/render.txt)
- commit hash：無，原因見第六節。

**任務 B：session 重整**

- EP15、EP16、EP17 均已套用 `raw／`、`_meta／`、`_bak／`、`v<N>_...／` 慣例。
- EP16 原有四版成品與六個備份均已歸位，原檔雜湊保持不變；v5 另建版本資料夾，沒有覆蓋既有成品。
- 本地 v4 與 Drive v1 採「本地剪輯版號／Drive 發佈版號」雙軌語意，以 [version_map.md](/Users/marslo/GithubRepo_mm-xyz/gsn-sol/sessions/2026-07-27_EP16-不要跟工作談戀愛/_meta/version_map.md) 對映，不改寫 Drive。
- `cut.py` 後續會先把本地成品、cutplan 快照及 `render.txt` 封存進版本資料夾。
- `prosody.py`、`copy_prompt_build.py`、`render_cut.py` 與 `session.py` 已配合 `_meta／` 慣例。
- 已更新 [CLAUDE.md](/Users/marslo/GithubRepo_mm-xyz/gsn-sol/CLAUDE.md)、[README.md](/Users/marslo/GithubRepo_mm-xyz/gsn-sol/README.md)，並新增 [ADR 0011](/Users/marslo/GithubRepo_mm-xyz/gsn-sol/docs/adr/0011-external-speech-inserts-and-podcast-session-layout.md)。
- 已重建並更新 [EP16_seam_list.md](/Users/marslo/GithubRepo_mm-xyz/gsn-sol/_local/EP16_seam_list.md)。
- 全程未存取、未上傳、未修改 Google Drive。
- commit hash：無，原因見第六節。

## 2．測試證據

紅燈關鍵行：

```text
FAIL: test_full_program_shapes (__main__.TestParseProgram.test_full_program_shapes)
AssertionError: 'chapter' != 'insert'

FAIL: test_insert_is_concatenated_faded_and_level_matched
AssertionError: '補錄串接: 1 段' not found

RED_EXIT render_cut=1 render_audio=1
```

完成實作後的綠燈：

```text
PASS  scripts/tests/test_render_audio.py — Ran 7 tests in 0.496s
PASS  scripts/tests/test_render_cut.py — Ran 41 tests in 0.568s
PASS  scripts/tests/test_cut.py — Ran 19 tests in 0.012s
PASS  scripts/tests/test_copy_prompt_build.py — Ran 8 tests in 0.115s
✅ 全部測試通過
```

完整音訊線測試合計十二支測試檔皆通過；Python 編譯檢查與 `git diff --check` 亦通過。

重整後 dry-run：

```text
EP15：EXIT=0，70 segments，疊接音樂 3 首
EP16：EXIT=0，109 segments，疊接音樂 3 首，補錄串接 1 段
EP17：EXIT=0，84 segments，疊接音樂 0 首
```

接縫檢查：

- `22：26．195` 與 `23：02．704` 前後各掃描三秒。
- 兩處均沒有低於 `－50 dB` 且長於 `0．20` 秒的靜音缺口。
- 補錄原始平均電平 `－35．8 dBFS`，鄰近正片 `－35．9 dBFS`，自動增益 `－0．10 dB`。

## 3．落點驗證

我沒有直接採用規格的概略換算，而是：

1. 分別擷取 v3 的 `22：24～22：36` 與 source 的 `1485～1520` 秒。
2. 轉成 `8 kHz` 單聲道，以 `20 ms` 視窗建立 log－RMS 一階差分包絡。
3. 掃描 `0．96～1．10` 倍速度，做正規化滑動相關比對。
4. 最佳結果為相關係數 `0．8882`、速度 `1．060`；v3 的 `22：27` 對應 source `1496．724` 秒。
5. 因此 v3 的 `22：30．000` 對應 source `1499．904` 秒，確實落在 B0642 的 `1499．340～1500．520` 秒內。

精確指定點位於「平平淡淡的」句中。為避免切斷字詞，實際採用 B0642 結束的 `1500．520` 秒，也就是在 B0642 後、B0643 前插入；相當於 v3 約 `22：30．616`。

## 4．設計決策

語法如下：

```markdown
## 🎙️ raw/MIC3_Sarah_2.WAV start=3.25 end=41.95 fadein=0.12 fadeout=0.18 level=auto
```

設計理由：

- 與既有 `## 🎵`、`## 🎬`、`## ✂` 同構，行序就是播放順序。
- `start／end` 明確屬於補錄檔自己的時間軸，不污染 source 時間軸。
- `level=auto` 以插入點前後最近各六秒的已保留正片語音對齊電平，增益限制在正負十二分貝。
- 補錄先經 `70 Hz` high－pass、`15 kHz` low－pass，再與正片共同經過動態及響度正規化。
- 接點使用個別淡入淡出及十毫秒微交疊。
- `cut_map.json` 的 `inserts` 欄位保存素材範圍、成品落點、增益與 fade，確保可追溯。

否決的方案包括：改寫 `source.wav`、把非同步補錄送進 `ingest_tracks.py`、重用 BGM overlay 語法、偽造來源 SRT block，以及把所有管線工作檔一起搬離 session 根。詳細理由已記錄於 ADR 0011。

## 5．你發現但規格沒提到的問題

- 規格所述的 `_local/EP16_seam_list.md` 在任務開始時實際不存在，因此只能依新成品重建路徑與本次兩個接縫，無法還原原清單條目。
- v1、v2、v3 沒有可證明是出片當下的精確 cutplan 快照。我以最接近的已知版本補存，並在各自 `render.txt` 明確標記不確定性，沒有偽稱為精確快照。
- 指定的 v3 `22：30` 位於 B0642 句中，若照毫秒直接插入會切斷「平平淡淡的」，因此落點必須移到句尾。
- 補錄文字目前只是定位用 Whisper 結果，沒有寫進不可變來源 SRT。這表示未來文案產生器不會自動取得補錄逐字內容；若要納入正式逐字稿，需要另設外部語音 transcript sidecar，不能偽造 source block。
- EP15 的 `opening.mp3` 是 cutplan 直接引用的管線輸入，不能當成人看產物搬走，因此仍留在 session 根。
- 舊的六個真音訊 fixture 缺少被 `.gitignore` 排除的 `audio16k.wav`；新補錄測試則會自行建立 WAV 並實際執行 FFmpeg，因此本次新增能力仍有真音訊回歸覆蓋。

## 6．沒做完的部分

- 無法建立 Git commit。此 sandbox 只允許讀取 `.git`，禁止建立 worktree index lock；實際錯誤為：

```text
fatal: Unable to create '/Users/marslo/GithubRepo_mm-xyz/good-students-note/.git/worktrees/gsn-sol/index.lock': Operation not permitted
```

  因此 test－first 與 implementation／reorg 兩個 commit hash 均無法產生，修改仍在工作樹中。

- 全套測試中的文件線測試因 `.venv-doc` 不存在而依既有測試腳本跳過；音訊線及本次涉及的測試全部通過。
- 此環境無真人聲音輸出，沒有進行主觀耳聽驗收；已完成電平、響度、true peak、靜音缺口與接縫位置的客觀檢查。