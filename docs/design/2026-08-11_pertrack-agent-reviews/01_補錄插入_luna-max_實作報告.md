### 1．做了什麼

A、完成外部補錄 `MIC3_Sarah_2.WAV` 的可重現 cutplan 語法、渲染、電平對齊與淡入淡出，產出 [EP16_v5.mp3](/Users/marslo/GithubRepo_mm-xyz/gsn-luna/sessions/2026-07-27_EP16-不要跟工作談戀愛/v5_20260810-2318/EP16_v5.mp3)。

B、完成 EP15／EP16／EP17 的 `_meta`、`_bak`、版本目錄重整，更新 [CLAUDE.md](/Users/marslo/GithubRepo_mm-xyz/gsn-luna/CLAUDE.md)、README 與 [ADR 0011](/Users/marslo/GithubRepo_mm-xyz/gsn-luna/docs/adr/0011-external-voice-in-cutplan.md)。未碰 Google Drive。

成品時長 `1826.577 秒`，大小 `43,839,446 bytes`。

主要 hash：

- 原始補錄：`70aa9d6d8188f064a2e0fc266708e27bd9fa6a9e43ca2eb38eff95a6e97f8fd8`
- EP16 v5：`77342d107fad43225466ae0f6c5487af7822b8e15fdfc183a30630f38ab239c5`

### 2．測試證據

TDD 紅燈：

- `test_external_voice_insert_is_a_program_item`：預期 `external`，實際為 `chapter`
- `test_external_voice_is_rendered_between_source_segments`：輸出 `1.0s`，未達 `>1.4s`

綠燈：

- `test_render_cut.py`：42 tests passed
- `test_render_audio.py`：7 tests passed
- 完整測試套件全部通過
- EP15／EP16／EP17 dry-run 全部 exit 0

文件線測試因 `.venv-doc` 不存在而跳過。

### 3．落點驗證

獨立比對 `final_cut_v3.mp3` 22:30 與 source 波形，最佳對應為 source `1500.02s`，落在 B0642 `1499.340–1500.520s`。

因此採用語意完整的接點：B0642「平平淡淡的」之後、B0643 之前。詳見 [EP16 v5 cutplan](/Users/marslo/GithubRepo_mm-xyz/gsn-luna/sessions/2026-07-27_EP16-不要跟工作談戀愛/v5_20260810-2318/cutplan.md) 與 [render.txt](/Users/marslo/GithubRepo_mm-xyz/gsn-luna/sessions/2026-07-27_EP16-不要跟工作談戀愛/v5_20260810-2318/render.txt)。

### 4．設計決策

使用：

```text
## 🎙 raw/MIC3_Sarah_2.WAV start=0 end=45.029 fadein=0.20 fadeout=0.25 gain=1.0
```

外部語音進入 concat 主鏈，使用 `loudnorm=I=-20:TP=-2:LRA=7`，並記錄於 `cut_map.json.external`。不寫入 source SRT，也不污染 source timeline。

拒絕手工 shell 拼接、偽造 SRT、以及當作 BGM overlay，原因是不可重現或會破壞時間軸與驗證邊界。

### 5．你發現但規格沒提到的問題

規格指定的 `_local/EP16_seam_list.md` 原本不存在，因此無法更新舊內容；已建立替代版 [EP16_seam_list.md](/Users/marslo/GithubRepo_mm-xyz/gsn-luna/_local/EP16_seam_list.md)。

此外，EP16 舊版 v4 的本地版本號與 Drive 版本號不同，已在各版本 `render.txt` 中記錄時間對照。

### 6．沒做完的部分

核心需求全部完成。

唯一未完成的是 Git commit：此 worktree 的 Git metadata 位於工作區外且唯讀，建立 `index.lock` 時被環境拒絕。所有修改仍保留在目前工作樹，`git diff --check` 已通過；`EP16_SPEC.md` 維持原本的使用者未追蹤檔案。