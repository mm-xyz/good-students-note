1. **Q1 的建議方案**

結論：不建議把「三份逐軌 ASR 文字去重」當成主要文字歸屬器。能量只能證明某軌有聲音，不能證明該軌說出了那段文字。

建議拆成三層：

- **文字層**：以混音後的 `transcript.speakers.srt` 作為 canonical text，搭配講者標籤與逐字能量校驗。逐軌 ASR 只作候選證據，不直接成為出片文字。
- **能量層**：沿用串音校準 `g[i][j]`，以 0.2 秒窗計算 excess。通過 `excess ≥ 10 dB`、絕對音量 `≥ -45 dB`、長度 `≥ 0.3 秒`、且別人正在說話，產生無文字的 overlap／backchannel event。
- **補充文字層**：只有能量層發現「可能是另一句完整話」時，才對該軌該小區間做 targeted ASR；通過時間穩定性、文字一致性後才列為低信心文字，不直接覆蓋 canonical text。

若仍需去重，判準應是：

- 先做 NFKC、大小寫、全半形、空白與標點正規化；中文以字元與 2／3-gram，比單純 token cosine 穩定。
- 以「時間相交＋文字相似」雙重成群。時間先擴張 ±0.4 秒；實際相交至少 0.25 秒，或中心點差不超過 0.6 秒。
- 文字用字元級 banded alignment，搭配 n-gram overlap。初始門檻可設為：長度 ≥12 字時相似度 ≥0.65 且雙向覆蓋 ≥0.55；短句提高到相似度 ≥0.75。短於 6 字不可只靠文字去重。
- 成群後選 canonical／medoid 文字，不選 excess 最高者。excess 是「聲音超出串音預測多少」，不是文字正確度。
- 28 秒一類的超長 cue 必須先依 VAD／停頓／字級時間戳重切，不能直接拿整句比較。

「能量真的有、但文字被拿走」應保留成：

`（疊話／附和，0.8s，文字未定）`

它應是獨立的 track-local block，附帶 `source_text_id`、能量證據與信心，不得把 Mars 的文字複製到 KIN。預設不勾可作為安全預設，但必須明確標為「待人審候選」，不能假裝已完成判斷。

我否決的方向：

- **全量逐軌轉錄再文字去重**：會把串音文字、長 cue、ASR 幻覺混在一起。
- **forced alignment 解決歸屬**：它適合把已知文字對到時間，不適合判斷文字到底是哪個人說的；串音文字也可能被成功對齊。
- **只用誰最大聲**：會再次殺掉小聲附和。
- **直接做波形相減**：不同麥克風的延遲、EQ、房間反射不同，容易產生音色洞與相位殘影。

目前腳本本身也有一個方法問題：`--excess` 參數只被宣告，沒有參與文字歸屬；文字仍是整個 cue 取 RMS 後「誰最大聲就歸誰」。

2. **Q2 的建議方案**

兩層模型方向正確，但必須改成「原子時間區間」模型：

- 先收集所有軌道 block、G 列、手動剪除、刪除線對應出的時間邊界。
- 以所有邊界切成半開區間 `[start，end)`。
- 每個原子區間都有一個軌道向量，例如 `[keep，mute，keep]`。
- 所有軌道都不保留，才從共同時間軸刪除。
- 只要有一軌保留，時間就存在，其餘軌道在該區間靜音。
- 若沒有 block 但能量或 canonical text 顯示可能有語音，狀態應是 `unknown`，不能直接當成可刪除。

這會解決部分覆蓋、邊界重疊與 block 邊界不同的問題。相同軌道的重疊 block 應報錯；不同軌道同時有聲則是合法 overlap。

`~~刪除線~~` 應改成軌道區域的剪除：

- 只作用於文字所屬的 speaker track。
- 跨 block 的刪除線要依新 block 邊界拆開。
- 不可把同一段串音文字的刪除線複製到其他軌道。
- `## ✂` 仍是共同時間軸剪除，作用於所有軌道。

建議處理鏈：

`各軌 trim／gate／短 fade → 各軌固定增益與必要 EQ → 各軌同步 concat → amix(normalize=0) → 保守 dynaudnorm → BGM envelope／amix → 最後 loudnorm`

其中：

- `dynaudnorm`、BGM 不應混在同一個動態均衡器裡。
- BGM 的人聲偵測應使用「至少一軌可聽」的最終時間遮罩，而不是原始 block。
- 保留固定 pan 與 headroom；`amix` 後加 limiter，最後仍以 `loudnorm` 作響度錨點。
- 初版為了和 v7 可比，建議先維持 voice bus 上的 `dynaudnorm`，不要同時引入三套獨立動態壓縮。

軌道靜音不應硬切。建議：

- 軌層 mute／unmute 使用 10–20ms raised-cosine 或 equal-power gate。
- 共同時間軸真的刪除時，保留現行 40ms crossfade；遇到 silence、insert 或已烘 fade 的接縫，維持 10ms。
- 用 clean solo 段校準各軌固定增益與 EQ，必要時保留極低的 room tone floor，避免突然變成無底噪的「真空」。
- 17–23dB 串音代表「靜音一軌」不等於「消除該人聲」。其他軌仍會留下他的 bleed。可用串音校準做 sidechain gate，但不應直接波形相減；若要求 overlap 中完全刪除某人，現有錄音條件不足，需 source separation 或重錄。

目前的 `render_cut.py` 仍是單一 `[0:a]` 與單一 `cat`，所以這不是加一個 mute 參數就能完成，必須建立 N 條同步音訊鏈。

3. **Q3 的建議方案**

遷移時要把舊資料分成兩種語義，不能全部丟給同一個 `migrate_marks.py` 字元流：

- 舊版 `[ ]` block、G 列與 `## ✂`：代表舊混音版的**共同時間軸刪除遮罩**。
- 舊版 `~~刪除線~~`：代表文字所屬講者的**軌道局部剪除**。

建議流程：

1. 先固定舊版 cutplan 快照，建立 `T_old(t)` 共同時間遮罩。
2. 逐軌 cue 先重切，所有新 block 必須低於 8 秒；目前資料仍有約 28 秒的 `M0065`，這種 block 不應進入遷移。
3. 新舊 block 以三元組對齊：`speaker＋時間＋文字`。
4. 時間使用 ±0.4～0.6 秒容差；文字使用 speaker-local 的字元 alignment。允許一對多、多對一，但部分覆蓋必須拆 block，不能整段四捨五入。
5. `migrate_marks.py` 的「完全對不上就丟棄並回報」原則可以保留，但必須改成每位 speaker 各自對齊，不能把三軌文字串成一條 stream。
6. 舊 checkbox 先寫入 `T_old(t)`，保證重切後仍能重現 v7。新軌道文字 block 在 `T_old＝keep` 時預設保留；backchannel 候選預設靜音。
7. 所有 ambiguous、部分覆蓋、文字重複但 speaker 不一致的項目列入人工清單，不靜默遷移。

驗收應至少包含：

- 舊版 21 個剪除決定全部出現在 `T_old(t)`。
- 154 個刪除線全部是「成功遷移」或「明確列出丟棄原因」，不得無聲消失。
- 新版 block 數量中，超過 8 秒者為零。
- 使用相同 global mask、tempo、BGM 與 insert，分軌版與 v7 的共同時間軸差異總和 ≤0.1 秒，單一剪點誤差 ≤50ms。
- 最終長度差異 ≤0.1 秒，響度差異 ≤0.5 LU，true peak 不超過 `-1.5 dBTP`。
- clean solo fixture：保留講者的短期響度在 ±1 LU 內，其他軌不應超過已校準的 bleed 基準加 3dB。
- KIN 目標附和事件建立人工 gold set；已知目標事件 recall 必須 100％，候選 precision 建議至少 80％。
- 每個 mute 邊界量測前後 100ms 的短期 LUFS，不得新增超過 3dB 的突跳；另檢查 spectral centroid 是否出現異常跳變。
- 最後再做盲聽，但盲聽是補充，不是唯一驗收。

時間座標必須透過 `cut_map.json` 對照。這次 raw source 的 320～322 秒約落在 v7 成品 4:47～4:49 附近，不能直接拿成品時間去比 raw cutplan。

4. **你認為這整個方向最大的風險是什麼**

最大風險不是「分軌本身」，而是把「軌道靜音」誤認成「來源分離」。

目前條件可以可靠地做到：

- 把某一軌的主要聲音靜音。
- 找到串音預測以外的附和候選。
- 讓 KIN 的「嗯」成為可人審的獨立事件。

但不能保證：

- KIN 的聲音從 Mars／Sarah 軌完全消失。
- overlap 中某人的完整文字一定能從另一人的串音中分離。
- 三份逐軌 ASR 能自動產生正確的講者文字真相。

因此我不否決分軌方向，但否決「目前 `pertrack_blocks.py` 產物直接進出片」。必須先完成長 cue 重切、文字／能量分層、global mask 遷移與 A/B fixture 驗收。

5. **你驗證過哪些數據**

我完整讀取了：

- [`CLAUDE.md`](/Users/marslo/GithubRepo_mm-xyz/good-students-note/CLAUDE.md)
- `docs/adr/0001` 至 `0013`，尤其 [`0011-session-layout-and-external-insert.md`](/Users/marslo/GithubRepo_mm-xyz/good-students-note/docs/adr/0011-session-layout-and-external-insert.md)
- [`render_cut.py`](/Users/marslo/GithubRepo_mm-xyz/good-students-note/scripts/audio/render_cut.py)
- [`pertrack_blocks.py`](/Users/marslo/GithubRepo_mm-xyz/good-students-note/scripts/audio/pertrack_blocks.py)
- [`migrate_marks.py`](/Users/marslo/GithubRepo_mm-xyz/good-students-note/scripts/audio/migrate_marks.py)

唯讀執行與結果：

- `ffprobe`：三條 track 都是 44.1kHz、mono、1,973.125805 秒；`source.wav` 是 stereo、同長度。
- `python3 scripts/audio/render_cut.py --session ... --dry-run`：
  - 948 個 mixed blocks。
  - 21 個超過 8 秒的 block。
  - 146 處實際字級精剪。
  - 29 處停頓收緊。
  - 2 段手動剪除，共 1.20 秒。
  - 109 個 segments、3 首 BGM。
- 現有 `cutplan.json` 的逐軌產物：
  - Mars：449 列，其中 442 speech、7 backchannel。
  - Sarah：282 列，其中 263 speech、19 backchannel。
  - KIN：414 列，其中 376 speech、38 backchannel。
- 以 `pertrack_blocks.py` 的純分析函式等價重算，未執行會回寫檔案的 `main`：
  - 原始逐軌 cues：Mars 1,084、Sarah 1,032、KIN 1,187。
  - 串音校準：Mars←Sarah `-17.4dB`、Mars←KIN `-22.6dB`；Sarah←Mars `-15.8dB`；KIN←Mars `-19.7dB`。
  - 現有產物中的 KIN 候選是 `320.0–320.8s` 與 `321.6–322.2s`；目前 ID 是 K0045、K0046，不是設計文件中的中間版本 K0054～K0057。
  - 目前還存在約 28 秒的 `M0065` 長 cue，這是遷移前必須處理的實際風險。
- `git status --short` 與 `git diff --stat` 最後均為空；沒有修改任何檔案。