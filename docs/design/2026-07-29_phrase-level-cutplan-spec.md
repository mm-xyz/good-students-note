# 設計文件：cutplan 斷句粒度改版 — EP15 式短句重切（phrase-level）

> 2026-07-29 · MM 拍板「EP15 的斷句才是要的粒度」。scope＝斷句演算法＋四個實作位置＋既有 session 重生流程（EP17 首跑）。
> 上游脈絡：ADR 0005（words.json 剪點防護鏈）、`35ad5a4`「cutplan 預設粒度改原 SRT 短句一行一句」（2026-07-28 MM 拍板）——當時的「短句 SRT」來自舊轉錄線的天然輸出，本設計把這個粒度變成**管線自己保證的性質**，不再依賴 ASR 引擎的心情。

---

## 1. 問題

EP17 cutplan B0006 把 NG 口白和正式開場黏在同一個 block（同一 SRT cue）：

```
- [x] B0006 [0:07–0:16] [Sarah] 你打扮一下,好,謝了。~~欸~~不對,我不能再講謝了,再打扮一次。嗨,歡迎來到水星貓的生活實驗室。我是Sarah。
```

勾選層（block 級）剪不掉 NG 段，只能靠字級刪除線救。

**根因**：2026-07-27 轉錄主線改本地 mlx-whisper（`c85c5fc`）後，whisper 輸出的是**帶標點的長 segment**（8–10s 一段）。管線既有的切點只有兩種：

| 切點 | 住哪 | 為什麼救不了 B0006 |
|---|---|---|
| 說話人換手 | `diarize.py split_cues_by_turns`（`a57f38a`，EP17 二航驗證 0 殘留） | 整段都是 Sarah 一個人，無換手 |
| 靜音間隔 | `cutplan.py --merge-gap`（預設 0，一 cue 一 block） | NG 之後**沒有停頓、一口氣重來**，cue 內無 ≥ 閾值的靜音 |

同人、無停頓的段落，兩個訊號都不存在——SRT 層（`transcript.speakers.srt` cue #6）就已經黏死，cutplan.py 只是照抄。

**對照組**：EP15（舊轉錄線）的 transcript.srt 是**片語級短 cue**（約 40 分鐘 1,800+ cues，vs EP17 20 分鐘約 198 cues），一 cue 一 block 天然就是人審想要的粒度。EP16/17 換引擎後粒度退化，這不是 cutplan 設定問題，是 ASR segment 形狀問題。

## 2. 決策

**不退回舊引擎**（本地 mlx-whisper 主線不動），改在 **word 級時間軸上機械重切短句**：

- **切點** = 字尾標點（`。?!…,、;:` 全形＋ `?!,;:` 半形；whisper zh 的標點跟在 word 字尾，如 `了,`、`ine。`）**或**字間停頓 ≥ **0.5s**。
- **文字由 words.json 重建**：`join_words()`（英數字相鄰時以原 cue 文字決定補不補空格），與 render 的字級對齊同源（ADR 0005）——cutplan 文字、SRT、words 三方一致，render 逐 block 驗證照常通過。
- 全確定性、零 LLM、零雲端；既有 session 重切**不重跑 ASR**。

## 3. 實作位置

| 元件 | 角色 |
|---|---|
| `srt_utils.split_words_to_phrases(ws, ref_text, gap=0.5)` | 斷句核心，純函式（stdlib only，主環境/.venv-audio 都能 import）；`join_words` 一併從 `diarize.py` 移進 `srt_utils`（diarize 改 import） |
| `transcribe_local.py` | **新 session 預設短句輸出**：每個 segment 的 words 過 `split_words_to_phrases` 再寫 SRT；無 words 的 segment fallback 整段照舊 |
| `resegment_srt.py`（新） | **既有 session 事後補切**：吃現成 `transcript.srt` + `words.json`，零模型；原檔備份 `transcript.srt.bak-longsegs`（已存在不覆蓋）；首尾沿用原 cue 邊界（與 `split_cues_by_turns` 同慣例）；回報 word 重建與原文的 mismatch 數 |
| `migrate_marks.py`（新） | **重生成後搬 Gemma 刪除線**：兩版 cutplan 的 block 文字攤平成字元流，difflib 對齊後逐 span 移植；跨 block 的 span 按新 block 邊界拆開；**對不上整段丟棄不硬搬**（寧缺勿錯，丟棄數回報）；只搬 `~~刪除線~~`，勾選/理由/章節不搬（人審狀態照 feedback:podcast-cutplan-drive-copy 用 diff 對回）。EP16 手工「字元流對齊」流程的固化版 |

## 4. 下游流程（既有 session 重生）

```
python3 scripts/audio/resegment_srt.py --session sessions/<slug>        # 短句重切 transcript.srt
python3 scripts/audio/diarize.py --session sessions/<slug> --from-tracks  # 換手貼標 → transcript.speakers.srt
python3 scripts/audio/cutplan.py prepare --session sessions/<slug>       # merge-gap 預設 0，一 cue 一 block（含 G 列）
python3 scripts/audio/migrate_marks.py --session sessions/<slug> --old <舊 cutplan 備份>
```

- **EP17 = 首個實跑案例**（未人審，MM 確認直接重生；88 處 Gemma 預標走 migrate_marks）。
- **EP16 已出片，不動**。
- 新 session（EP18 起）由 `transcribe_local.py` 內建，無須 resegment 步驟。

## 5. 邊界與已知限制

- **同人、無停頓、又完全沒標點**的長串仍切不開（兩種切點訊號＋標點都缺席）；whisper zh 帶 initial_prompt 時標點密度高，目前無此實例，出現再議（候選：超長 phrase 在最大字間隔強切）。
- **OpenCC 兩條路徑偶有差異**：words 是逐字 s2twp、segment 文字是整段 s2twp，片語級轉換（如需上下文的詞）可能不一致；resegment 對每個 cue 比對重建文字並**回報 mismatch 數**，不中斷（words 本就是 render 的真相源）。
- migrate_marks 只保證「文字完全相同」的 span 移植；斷句改變導致文字微差的 span 一律丟棄並計數，人審時錯標可直接刪。
- **零長度 artifact word 不能自成短句**（EP17 首跑實踩）：whisper 偶發 start==end 的 word（如笑聲後的「哈囉。」），若切成獨立 phrase 會產生 0 長度 cue——下游換手切開按 midpoint 收字時會把它的字吃進前句，留下無 speaker 的重複孤兒 cue。`split_words_to_phrases` 已把全零長度的 group 併回鄰組。diarize 換手切開自身在 artifact word 落在換手點時仍會產少量 0 長度 cue（EP17 舊版 3 個/新版 5 個），屬既有行為，EP16 同樣條件已正常出片，不在本次範圍。

## 6. 驗收判準

1. EP17 開場 **NG 段與正式開場分屬不同 block**（「…再打扮一次。」與「嗨,歡迎來到…」之間有切點）。
2. EP17 block 總數**量級接近 EP15 密度**（短句級，非 8–10s 長段）。
3. Gemma 刪除線遷移**丟棄數在個位數**（EP16 手工先例：156 → 丟 1）。
4. `render_cut.py --dry-run` 逐 block 對 SRT **驗證通過**。

## 7. EP17 實跑結果（2026-07-29，全數 PASS）

- 198 cues → 722 短句 cues → 換手切開後 **779 blocks**（20.4 分內容，密度與 EP15 的 1825 block/40 分同級）。
- NG 段（B0009–B0014）與正式開場（B0015–B0017）分開，勾選即剪。
- Gemma 刪除線 112 spans → 移植 118 段（跨 block 拆分），**丟棄 0**。
- resegment 回報 mismatch 17 段，抽查全屬 cue 交界字歸屬移動＋空格差，字元流無遺失。
- `--dry-run` 驗證通過。
