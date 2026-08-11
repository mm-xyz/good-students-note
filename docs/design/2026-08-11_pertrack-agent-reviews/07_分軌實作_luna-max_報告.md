1. **做了什麼**

- D1：改用 `cutplan.json`／混音 `words.json` 作 canonical 文字來源。
- D2：加入 40ms frame、100ms integration、3dB lead、hysteresis 與字界切分。
- D3：加入 power-domain 串音校準、CFAR P99.5、80ms gap closing、非詞彙候選。
- D4：加入 atomic cells、同軌矛盾重疊檢查、全軌 MUTE 時間移除。
- D5：加入分軌 gating、speech bus、dynaudnorm、BGM overlay、loudnorm。
- D6：加入 15ms 軌內 fade 與 40ms speech-bus crossfade。

Commit hash：無。環境禁止寫入 `.git`，`git add` 失敗：

```text
fatal: Unable to create '.git/index.lock': Operation not permitted
```

目前分支仍為 `feat/pertrack-luna`。

輸出：

- [final_cut_pertrack.mp3](/Users/marslo/GithubRepo_mm-xyz/gsn-impl-luna/sessions/2026-07-27_EP16-不要跟工作談戀愛/v08_20260811-1042_分軌/final_cut_pertrack.mp3)
- [render.txt](/Users/marslo/GithubRepo_mm-xyz/gsn-impl-luna/sessions/2026-07-27_EP16-不要跟工作談戀愛/v08_20260811-1042_分軌/render.txt)

2. **測試證據**

紅燈：

```text
ImportError: cannot import name 'map_spans_by_speaker' from 'migrate_marks'
❌ 有測試失敗 — 修好再動 scripts/audio/.
```

綠燈：

```text
PASS scripts/tests/test_pertrack_pipeline.py — Ran 7 tests
PASS scripts/tests/test_render_cut.py — Ran 56 tests
PASS scripts/tests/test_resegment_migrate.py — Ran 13 tests
✅ 全部測試通過
```

`run_all.sh` 全部通過；文件線測試因 `.venv-doc` 不存在而跳過。

3. **驗收數據**

- v7：1820.479 秒；v8：1657.955 秒；縮短 162.524 秒（−8.9％）。
- Integrated loudness：v7 −17.0 LUFS；v8 −17.8 LUFS。
- Peak：v7 −1.4 dB；v8 −1.5 dB，沒有 0 dBFS clipping。
- D4：3380 atomic cells，2819 KEEP，561 全軌 MUTE／剪點。
- D5：1011 個 render cells，3 首 BGM overlay。
- 目標區間 313–322 秒：KIN 非詞彙候選 3 段，全部未勾選並折疊；KIN 只有 canonical「了。」KEEP 0.120 秒，其餘共同保留時間 MUTE 7.080 秒。

4. **設計偏離**

- 大型 session 的 40ms crossfade 以每 64 個 cell 的 speech-bus chunk 執行，避免 FFmpeg filter graph 記憶體耗盡。
- 規格要求的 −24～−30dB 常態 activity mask 尚未套用；無 block 軌目前依 D4 直接 MUTE。
- 若出現不確定 speaker，會放入 canonical speaker 的建議軌，但保持未勾選，不會硬保留。

5. **發現但規格未提到的問題**

- 1011 cells × 3 軌若直接生成巨大 `aselect`／巢狀 `if`，FFmpeg 會出現 parser memory failure；已改成分段選取。
- v8 的 100ms RMS 最大跳變為 43.94dB，雖然 ≥12dB／≥18dB 跳變次數均下降，但最大單次跳變比 v7 的 34.41dB 更高，需要實聽確認。
- `.git` 唯讀是目前無法提交的外部環境限制。

6. **沒做完的部分**

- 無法建立 D1–D6 commit。
- 154 處刪除線遷移為 109 段，48 處因 speaker stream 對不上而丟棄；21 個舊剪除區間成功遷移 16 段。
- 尚未完成主觀實聽與頻譜層級的音色跳變驗證。