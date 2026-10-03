# ADR-2026-10-03-audio-tracks-line — 第四條路線：合軌決定 ＋ 分軌出聲

- 識別碼：ADR-2026-10-03-audio-tracks-line
- 日期：2026-10-03
- 狀態：已採納（MM 2026-10-03 拍板需求；實作待 MM 實聽 EP22 `tracks_test.mp3`）
- Lifov 卡：派工未提供卡號
- 相關：ADR-2026-09-06-mixdown-audio-line（反方向的 `line=pertrack audio=mixdown`）、
  ADR-2026-09-14-mixdown-by-default、ADR 0001（cutplan 是人審真相源）、
  ADR 0015（podcast 剪輯 SOP）、ADR-2026-08-11-674（編輯器唯讀護欄）、
  PERTRACK_IMPL.md D5/D6（bus 混音鏈與接縫）

## 脈絡

MM 2026-10-03：「因為大家的總錄音時間都是固定，可以先用合軌去切片段，但是實際上會用分軌去剪音訊。分軌的時候每一軌都是一欄。」

到目前為止有三條路線：

| ⚙ | 決定層 | 音源 |
| :--- | :--- | :--- |
| `line=mixdown` | 合軌 block | `source.wav` |
| `line=pertrack` | 逐軌 block（兩層模型） | speech bus（三軌重混） |
| `line=pertrack audio=mixdown` | 逐軌 block | `source.wav` |

分軌線（`line=pertrack`）被放下的原因，是**逐軌節目單**：每一軌各自的 block 會在接縫處切得很怪（ADR-2026-09-14）。不是分軌音訊本身有問題。合軌節目單切得乾淨，但合軌音源做不到「這段只留某一軌、把其他人的串音壓下去」。

三條分軌（`tracks/1_Mars.WAV` 等）跟 `source.wav` 等長，而且 sample-aligned（EP22 實測位移 −0.07／0／+0.43 ms）。所以合軌節目單算出來的時間區間，可以直接拿去切分軌。

## 決策

新增 `## ⚙ line=mixdown audio=tracks`：

- **決定層是合軌節目單**。B#### block、G 列、勾選、刪除線、`## ✂`、停頓收緊、章節、🎵、➕ 全部走 `line=mixdown` 既有的邏輯，一行都沒改。時間剪輯結果跟 `line=mixdown` **逐毫秒相同**，以 `--dump-ranges` 驗證：EP22 全集 56 段完全相同，另有測試鎖住。
- **音源是 `tracks/` 的分軌**。render 用同一組區間切三軌，每軌套上該軌的增益包絡，混成一條 bus，之後的 dynaudnorm → BGM → loudnorm 照舊走 `run_ffmpeg`。
- **每個 B／G 列多一欄「每軌一格」的開關**。列勾選代表「保留這段時間」，語意不變；軌欄代表「這段哪幾軌出聲」。
  - ● 出聲（0 dB），○ 壓低 **−27 dB**（沿用 pertrack 的 DUCK，不全靜音）。
  - 軌間切換沿用 `pertrack_render.envelope_curve` 的等功率 raised-cosine（`--gate-fade` 15 ms），不會有 click。
  - **hold**：block 之間的空隙沿用前一個 block 的軌狀態，開著的軌開到下一個 block 起點才關，避免底噪在空隙裡抽動。
  - **lead**（本 ADR 補的）：某一軌在下一個 block 才「新開」時，提早 `tracks-lead`（預設 0.15 s）打開，但不早於前一個 block 的結尾。SRT 起點常比真正的字頭晚（同 `extend_unit_edges` 的理由），不提早的話，新講者的字頭會被壓 −27 dB 吃掉。要關的軌不受影響，照 hold 規則走。
  - 重疊講話時，前一個 block 的軌開到它自己的結尾，取聯集，不互搶。
- **預設值**（`tracks_columns.py` 產生欄位時）：
  - B 列：該 block 時間內**校準後**最大聲的那一軌開，其餘關。
  - G 列：三軌全開。
  - 校準方式：每軌先量自己的底噪（P10）和自己講話時的電平（P97），用 100 ms 積分、80 Hz high-pass，跟 `pertrack_blocks.track_power` 同一套。再看 block 電平落在該軌動態範圍的哪個位置，取最高的那一軌。不能直接比原始 RMS：麥克風增益大的那一軌，收到的串音會贏過真正在講話的人，測試 `test_calibrated_choice_beats_raw_rms` 鎖住這一點。
- **➕ 補錄**：這版仍用補錄的合軌檔，不做分軌補錄，render 會明講。`gain=auto` 照合軌線的語意對 `source.wav` 的鄰段量（仍夾 ±12 dB），再加上 bus 與 source 在同一批鄰段的實測落差，這個落差**不夾**。EP22 實踩：分軌 bus 比錄音機合軌小聲約 16–20 dB，直接拿 bus 當基準會被 ±12 夾住，補錄段在成品裡比正片大聲 3.4 LU。修正後補錄與前後 15 s 正片只差 +0.1 LU（合軌線參考值是 +1.0 LU）。
- **🔇 室噪**照舊取自合軌 `source.wav`。
- **static gain**沿用 D5：只取「單獨開某一軌」的保留 block，量各軌 LUFS 後拉齊，夾在 ±6 dB 內。EP22 結果：Mars +4.3、Sarah −0.8、Kin −3.6 dB。

### 護欄（路線寫錯一律 FAIL，不靜默）

- 節目單有軌欄，但 ⚙ 沒寫 `audio=tracks` → FAIL。
- `audio=tracks` 寫在 `line=pertrack` 上 → FAIL。
- `audio=tracks` 但 session 沒有 `tracks/` → FAIL。
- `audio=tracks` 但有 B／G 列缺軌欄，或軌名、順序跟 `tracks/` 不符 → FAIL，訊息會列出 id，並指向 `tracks_columns.py`。
- S 列（➕ 補錄）帶軌欄 → FAIL。
- `cut.py route_label` 顯示「合軌決定＋分軌音源」。`semantic_diff` 會列出「軌欄變動 N 個」，只切軌欄也是剪輯決定，不能印成「不影響剪輯」。

## 欄位格式

行尾一欄，用 `⟦ ⟧` 包住，每軌一格，格式是「軌名 + ●／○」，以單一空白分隔，順序等於 `tracks/` 檔名排序：

```
## ⚙ line=mixdown template=水星貓的生活實驗室_v1 audio=tracks

- [x] B0024 [0:41–0:44] [Mars] Fuji Rock是一個日本很有名的音樂技。 ← MM 11:07 分軌版人審 ⟦Mars● Sarah○ Kin○⟧
- [x] B0026 [0:44–0:48] [Mars] ~~就是它就是會辦~~在夏天的滑雪場裡面。 ⟦Mars● Sarah○ Kin○⟧
- [ ] G0001 [5:09–5:11] 🔊 聲音事件 1.3s(峰值 -29dB;…) ⟦Mars● Sarah● Kin●⟧
## ➕ raw/補錄_2026_1002_2004.WAV gain=auto  結尾補錄
- [ ] S0001 [0:00–0:03] [Mars] 主持人:「戀愛腦嗎?」,      ← S 列不加欄
```

為什麼這樣設計，對照需求 (a)–(e)：

- **(a) 文字驗證不受影響**：軌欄錨在行尾，所有解析器都先切掉軌欄，再照原本的方式切 ` ← 理由` 和 speaker 前綴。`validate_program` 看到的正文跟沒有軌欄時逐字相同。`⟦⟧` 不會出現在逐字稿裡。
- **(b) 合軌節目單不變**：沒有軌欄的列，解析結果與舊版完全相同。既有 100 項 `test_render_cut` 和 6 項真音訊回歸全部照過。
- **(c) 編輯器只多開一個口**：只能翻 ●／○。欄位的有無、軌名、順序、空白格式都是唯讀，伺服端護欄以「把 ●○ 換成 ? 之後必須逐 byte 相同」來檢查。
- **(d) 跨 session 搬標記**：B#### 編號不變，軌欄只是行尾附加。從合軌 session 搬勾選和刪除線是逐列直接對應；`migrate_marks.py` 也會把軌欄當尾註，不讓它進字元流。
- **(e) 產生工具冪等**：`tracks_columns.py` 只對**沒有軌欄**的列算預設值並補上，既有的軌欄（不論人工或先前產生）一律不動，只驗格式；勾選也不碰。第二次跑的輸出逐 byte 相同。

## 代價（寫明，不是副作用）

1. **成品的「底色」不再是錄音機的合軌**。bus 是三軌現場重混：錄音機合軌上的 fader、EQ、限制器都不在。EP22 的 bus peak 是 −6.5 dBFS，鄰段大約 −38.7 LUFS，合軌則約 −22.7 LUFS。最後由 dynaudnorm 和 loudnorm 拉齊，但音色可能跟合軌不同，**要 MM 實聽**。
2. **預設值是猜的**。EP22 有 2027 個 B 列，預設跟 diarize 的 `[speaker]` 標籤一致的有 1736 個（85.6%）。最大的分歧是「標 Kin、預設開 Mars」，共 121 個。誰對要靠耳朵判斷，這正是編輯器開軌欄這個口的理由。
3. **預設一律只開一軌**，搶話或笑聲會被壓 −27 dB，直到人工把第二軌打開。這是 MM 拍板的預設（「最大聲的那一軌開」），代價寫在這裡。
4. **檔案變大、行變長**：EP22 的 cutplan.md 從 172 KB 增加到 234 KB。手機上的 Drive 原文會多一截，但編輯器把它畫成開關，不顯示原文。
5. **分軌補錄沒做**：➕ 這一段仍是合軌音色。

## 附錄：建議的 SKILL 段落（`~/.claude/skills/podcast-cut/SKILL.md`，由派工者改）

建議插在「預設走合軌」一節之後：

```markdown
## 第四條路線：合軌決定＋分軌出聲(`audio=tracks`,ADR-2026-10-03-audio-tracks-line)

有 `tracks/` 分軌、又想壓掉串音時用:節目單照舊是合軌的(勾選/刪除線/✂/章節全照
`line=mixdown`,時間剪輯逐毫秒相同),只是出聲改用分軌,每個 B/G 列行尾一欄
`⟦Mars● Sarah○ Kin○⟧`(●=出聲,○=壓 −27dB)。

1. 先備份:`cp _asset/cutplan.md _asset/cutplan.before-tracks.md`
2. 加欄(冪等,不蓋人工改過的欄/勾選;⚙ 自動補 audio=tracks):
   `python3 scripts/audio/tracks_columns.py --session sessions/<slug>`
   預設=校準後最大聲那一軌開,G 列三軌全開。
3. MM 用編輯器切軌欄(每張卡片三個開關;吃復原;只能翻 ●/○)。
4. 照常 `cut.py` 出片;render.txt 路線會寫「合軌決定＋分軌音源」。

坑:
- 有軌欄但 ⚙ 沒寫 audio=tracks、缺欄、軌名不符 → render FAIL(故意的)。
- ➕ 補錄仍用合軌檔(不分軌);gain=auto 對合軌量再補 bus 落差。
- 預設跟 diarize 標籤只有 ~86% 一致(EP22),搶話/笑聲預設只開一軌——人審要聽。
- 要退回合軌:⚙ 拿掉 audio=tracks **並且**把軌欄拿掉(或還原 before-tracks 備份),
  只拿掉 ⚙ 會 FAIL。
```
