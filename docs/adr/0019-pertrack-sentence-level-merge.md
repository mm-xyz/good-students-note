# ADR 0019 — 分軌 block 合併靠攏句子級，不限同一 canonical block

- 識別碼：ADR-2026-08-14-676
- 日期：2026-08-14
- 狀態：已採納
- 相關：commit `cc9ecc6`（block 粒度上限——每秒一個可勾選的 block，MM 規格）、
  ADR 0014（分軌線 vs 混音線）、卡 #677（歸屬不確定 44%，本卡不動）、
  卡 #675 / ADR-2026-08-14-675（whisper artifact 守門）

## 脈絡

分軌 cutplan（`pertrack_blocks.py`）EP16 實測 69.8% 的列 ≤4 字，常見一句
話被劈成 3 行（例：「上班忙著解副」/「本,」/「下班忙著開副」/「本,」）。
cutplan 是人審唯一介面，MM 讀不動這種粒度。

根因：D1（`split_words_to_phrases`）為了 cc9ecc6「每秒一個可勾選 block」
的規格，把 canonical block 切到 0.4–1.2s 的 phrase；D2 逐 phrase-cue 各自
判定歸屬、逐 phrase 各自呼叫 `enforce_phrase_len` 做局部合併。**這兩層都
只管「不要太大」，沒有人管「同一句被切開的碎片要不要黏回去」**——即使歸
屬相同、時間緊鄰,跨 phrase-cue（甚至跨原始 canonical block）的碎片從未
在下游合併。

## 決策

在 D1/D2 跑完、per-owner rows 已排序之後,加一道 `merge_sentence_rows`
後製合併(prior art:混音線 `cutplan.py` 的 `build_blocks(cues, merge_gap,
max_block)` 就是同一套「同講者、間隔小、有上限」的合併邏輯,這裡是同一
哲學搬到分軌線的下游)：

- 同 owner、間隔 `<0.45s`(略低於 D1 自己的 0.5s 停頓斷句門檻——只黏合
  「非因停頓、純因粒度上限被切開」的碎片,真實停頓維持斷點)。
- 合併後不超過 `2.0s`(擋住退回 cc9ecc6 之前 27.9s 大塊問題)。
- **不重新判定歸屬**——只吃 D2 已經決定好的 owner 分組,#677 的事不在這
  卡動。
- voicing(非詞彙出聲)列不參與合併,天然是斷點。

### 拿掉「同一 canonical block」限制,但補一道「不能跨過別人插話」的guard

最先做的版本只合併**同一個** canonical block(`src`)分裂出來的碎片,
EP16 實測卡在 32%,降不下去——因為同一講者連續講、中間沒有真實停頓,
常常會跨過上游混音線 `build_blocks` 自己切的 block 邊界(那個邊界是混音
線自己的 `merge_gap`/`max_block` 決定的,不代表句子邊界)。拿掉這個限制,
壓到 21.8%,但引出兩個必須處理的副作用:

1. **`src` 溯源失真**:跨邊界合併後只留第一段的 `src`,人審想查「這行
   是哪個原始 block 拼出來的」會查到錯的一半。修法:合併時 `src` 記所有
   來源 id(`B0013+B0014`)。
2. **跨過別人插的話**(較嚴重):A 講到一半、B 插了一句(通常是附和的
   「嗯」)、A 接著講——A 的兩個碎片間隔仍 <0.45s,合併起來的文字會跳過
   B 講的內容,不再是來源 SRT 的連續子字串,撞上 `render_cut.py`
   `validate_program` 的逐字防幻覺驗證,render 直接 FAIL。EP16 實跑
   `render_cut.py --dry-run` 抓到 17 處(merged 列的 5.0%)。
   修法:`merge_sentence_rows` 新增 `blocked_by` 參數(其他 owner 的
   speech 區間清單),間隔內只要有人插話就不合併,即使 `<gap`。加上這道
   guard 後,merged 列的 SRT 不一致率降到 2.1%(接近未合併列本身
   ~1.1% 的既有背景值,殘餘的幾筆是既有的 whisper 重複迴圈類 artifact,
   同一類問題在未合併的單一 block 上也存在——見 ADR-2026-08-14-675——不是本卡
   引入的新問題,也不在本卡改動範圍內)。

## 量測(EP16 實資料,pertrack_blocks.py 端到端真跑,非離線模擬)

| 指標 | Before | After |
| :--- | :--- | :--- |
| 分軌列 ≤4 字占比 | 69.8%（2778 列中 1939） | 22.3%（1282 列中 286） |
| median / mean 字數 | 3 / 3.55 | 7 / 7.70 |
| D2 canonical phrase / 換手切開 / 歸屬不確定 | 2135 / 627 / 880 | 2135 / 627 / 880（逐位元相同） |
| 各軌總字數(Mars/Sarah/KIN) | 3293 / 3255 / 3432 | 3293 / 3255 / 3432（逐字相同） |
| 各軌時間 envelope(首列 start–末列 end) | 完全相同 | 完全相同 |

歸屬判定與非詞彙出聲(D3)兩段的統計數字逐位元相同,證明 merge 是純下游
後製,沒有動到 D2/D3 任何一行邏輯。

## 後果

- 好:cutplan 人審可讀性大幅改善,MM 不用再拼湊三行才看懂一句話。
- 好:`merge_sentence_rows` 是純函式、零依賴 numpy/ffmpeg,`scripts/tests`
  可以完全用合成資料覆蓋,不需要真音檔就能跑 CI。
- 代價:`blocked_by` guard 在 `main()` 需要為每個 owner 現組「其他人的
  speech 區間」清單,O(n²) 檢查(對 EP16 規模的 session 是毫秒級,不成
  問題;若未來 session 長度數量級暴增可能要換 interval tree,目前沒必要
  先做)。
- 代價/已知限制:合併後留下的 2.1% SRT 不一致殘餘(同類 whisper 重複迴圈
  artifact)仍會讓 `render_cut.py --dry-run` 在少數幾行卡住,跟合併前
  就存在的背景值同源——已經是既有機制(cutplan 人審流程本來就會抓到
  文字跟 json 對不上要求重跑 `cutplan.py prepare`),不是本卡的新缺口。

## 同根因掃描

混音線(`cutplan.py` 的 `build_blocks`)EP16 `cutplan.md` 實測 ≤4 字占比
28.6%(964 列中 276 列),已經在 ≤30% 門檻之內,不算病態,推測是因為混音線
`build_blocks` 本來就是「同講者、間隔小、有上限」的合併邏輯(分軌線這次
補的正是這一套),沒有 D1 那種「先切到 0.4–1.2s 再也沒黏回去」的結構性
缺口。列為觀察項,不在本卡動,若 MM 想進一步壓低可另開卡。

## 追記(luna 守門 FAIL,同日修正)

luna 對照組發現:合併 reason 的邏輯是『prev reason 空才採後列』(先到先
贏)——前列已有其他理由(如「換手點附近未切開」)、後列是「歸屬不確定」
時,這個安全網 marker 會被悄悄蓋掉,合併後的 block 看起來像確定的,人審
沒有線索分辨。EP16 真跑實測:1266 句語音裡有 40 句(3.2%)踩到這個。

修法:新增 `_merge_reasons(a, b)`——依序保留、去重、用「；」串接(既有
分隔慣例,見本檔 PREAMBLE／`diff_clips.py`),不是誰先誰贏。`_row_line()`
不用改,它本來就原樣印出 `reason` 欄。確認過共存不打架:`⚠ASR-artifact`
只存在於混音線 `cutplan.py` 的 `flag_artifacts`(`cutplan.md` 專用),
pertrack 這邊的 `backfill_artifact_flags` 只設 `asr_artifact`/
`asr_artifact_reason` 兩個獨立欄位,從不寫進 per-track row 的 `reason`
——兩個通道不會混在一起。

回歸測試三條(`TestMergeSentenceRows`):兩邊理由都留著、完全相同的理由
去重、後列理由為空時維持前列理由不動。修正不影響 D1/D2/D3 統計與 ≤4 字
占比(仍是 22.3%)。

全套 `scripts/tests/run_all.sh` 實測 383 條(先前回報誤用 376,重新數
一次以此為準)。

### round 2:去重要拆 token,不能比對整個字串

luna 接著抓到 `_merge_reasons` 的去重比對的是**完整字串**——三列以上
逐列合併時,`prev.reason` 已經是上一次合併產出的複合字串(如「A；B」),
第三列若帶著跟 B 相同的理由,但不是逐字相同的複合字串,`p not in out`
比對不出來,一樣被當成新理由追加,變成「A；B；B」堆疊。改法:兩邊輸入
都先用「；」拆成 token 再對 token 去重、依序保留。回歸測試兩條:三列
連併(每列都帶「歸屬不確定」)→ marker 只出現一次;`_merge_reasons(
"A；B", "B")` → `"A；B"`。EP16 真跑掃描:1266 句語音 0 句 reason 出現
重複 token。

全套測試最終 385 條。
