# ADR 0021 — 「歸屬不確定」reason 按實際成因分流，判定當下就記原因碼

- 識別碼：ADR-2026-08-14-728
- 日期：2026-08-14
- 狀態：已採納
- 相關：卡 #728（本卡）、卡 #677（診斷來源，成因分佈）、卡 #726（底噪閘
  相對領先例外，改變了成因佔比但不動判定邏輯）、`scripts/audio/
  pertrack_attrib.py` `owner_runs()` / `split_phrase()`

## 脈絡

`owner_runs()` 判 owner=None 時，`split_phrase()` 一律標同一句「歸屬不
確定（三軌差距 <3dB）」。#677 診斷把 880 筆（#726 前）逐一分類，字面
「差距 <3dB」成立的只有 0.3%；組成其實是底噪閘擋下(59%)／連續性中斷
(38%)／真差距不足(0.3%)。同一句話蓋掉三種不同成因，人審看到這句話時
的心智模型（"喔，兩人聲音差不多大聲"）跟實際情況（多數時候其實是清楚
的贏家被底噪閘擋掉、或贏家撐不滿穩定時長）不符。

## 決策

`owner_runs()` 回傳從 3-tuple `(start, end, owner)` 改成 4-tuple
`(start, end, owner, cause)`。owner 已判定時 `cause` 一律 `None`；
owner=None 時 `cause` 是下列三者之一（`_uncertain_cause`，優先序
floor_gated → below_margin → unstable，對齊 #677 診斷腳本
`diag1_causes.py` 的分類法，但在判定當下、用同一份逐 frame 資料就地
分類——不是事後對 L_attr 另跑一遍分析）：

| cause | 定義 | 文案（`UNCERTAIN_REASON_TEXT`） |
| :--- | :--- | :--- |
| `floor_gated` | 這段至少半數 frame 三軌都在各自底噪之下 | 歸屬不確定（三軌皆近底噪，領先不足） |
| `below_margin` | 扣掉 floor_gated frame 後，對第二名的領先從未達到 margin | 歸屬不確定（三軌差距 <3dB） |
| `unstable` | 領先幅度達過 margin，但同一候選人沒能連續撐滿穩定時長 | 歸屬不確定（領先未能持續） |

`split_phrase()` 的 `owner_of()` 沿用既有「佔多數的那一位」邏輯：一個
phrase 若跨了多個 owner=None 的 run（各自 cause 可能不同），取重疊時長
最長的 cause 當代表，不另外發明規則。既有 reason 通道（`pertrack_
blocks.py` 的「← 」尾註、`_merge_reasons` 的「；」分隔與 token 去重、
「（暫掛 diarize 判的 X）」後綴）完全不動，只換 `split_phrase` 產出的
文字內容。

**明確不動的範圍**：門檻（margin/stable/switch/floor_margin）、
hysteresis、#726 的底噪閘相對領先豁免，全部原封不動——本卡純標示，
不是判定邏輯調整。

## UNEXPECTED_SHOULD_HAVE_OWNER 根因（#677 診斷法的既有落差，非 production bug）

`diag1_causes.py` 用「post-hoc 重算」判 owner=None 的原因：對每個
owner=None 的 `part`（來自 `split_phrase`/`enforce_phrase_len`，邊界是
canonical word 邊界），拿它的 `[start,end]` 直接切一段 L_attr 重新算
`longest_margin_streak_s`，跟 `need_stable` 比對。#677 抓到 23 筆
（post-#726 重跑為 19 筆）這個重算出的 streak ≥ need_stable，判定「理論
上應該已經有 owner」——但 production 明明判了 None，看起來像 bug。

逐筆重播 `owner_runs()` 實際回傳的 run 邊界（frame-quantized）跟
`split_phrase` 產出的 part 邊界（word-quantized）並排比對後，19 筆全部
是同一種模式：**該 phrase 內部有 owner_runs 的換手點，但附近 250ms 內
找不到 canonical word 邊界可切（常見於單字詞，如「類」「內」「容」
「嗯」「對啊」），`split_phrase` 只好把 None 的 run 跟其後緊接著已建立
owner 的 run 併成同一個 part**。`diag1` 重算時用這個「合併後」的時間窗
去切 L_attr，量到的 longest streak 其實有一段來自**相鄰、已判定**的
run（那段本來就滿足 margin+stable，理所當然會有長 streak），被誤算成
「None 那段」的證據。

這是診斷腳本的量測窗口跟 production 實際判定單位（run）不一致造成的
系統性假訊號，不是 `owner_runs()`/`split_phrase()` 的判定 bug——production
從沒有在那個時間點宣告過 owner，它只是**沒被切開**，人審看到的仍是
正確的「合併後 part 判 None」。

**本卡的實作天然免疫這個假訊號**：新的 `cause` 是在 `owner_runs()` 逐
frame 迭代時，只用「該 None run 自己的 frame」累計分類，不會借用相鄰
已判定 run 的 frame；`split_phrase.owner_of()` 合併多個 run 時，`cause`
一樣只從 owner=None 的 run 取（成對累計 `cause_acc`），不會被 owner 已
判定的 run 污染。因此不需要另外修正——**只回報，不修**（要修的是
`diag1_causes.py` 這支診斷腳本本身的量測口徑，它是分析工具不是
production code，不在本卡範圍）。

## 量測（EP16 實資料，production `split_phrase` 直接產出的 `reason`
欄位，非另一套分類腳本；n_unc 前後一致 =708，證實純標示不改筆數）

| reason 文案 | 筆數 | 筆數% | 時長(s) | 時長% |
| :--- | :--- | :--- | :--- | :--- |
| 歸屬不確定（三軌皆近底噪，領先不足） | 400 | 56.5% | 222.8 | 67.8% |
| 歸屬不確定（領先未能持續） | 299 | 42.2% | 105.9 | 32.2% |
| 歸屬不確定（三軌差距 <3dB） | 9 | 1.3% | 0.0 | 0.0% |
| TOTAL | 708 | 100.0% | 328.7 | 100.0% |

（`FLOOR_GATED` 佔比較 #677 原始 59%／#726 前更高比重仍是最大宗但
筆數已從 520 降到 400——跟 #726「底噪閘相對領先例外」把清楚贏家撈出
不確定池一致；below_margin 9 筆＝1.3%，跟 #677「字面成立僅 0.3%」量級
吻合，`diag1` 的 0.3% 是筆數口徑；本卡是 D2 唯一入口，兩者統計母體一致。）

## 後果

- 好：人審讀到的三句文案彼此語意不同，一眼能分辨「沒人真的在講話」
  vs「講者換手太快」vs「真的旗鼓相當」，不用再自行腦補。
- 好：`owner_runs()` 的 cause 分類完全基於判定當下的 run 自身資料，
  不像診斷腳本的事後重算會被相鄰 run 污染（見上「UNEXPECTED 根因」）。
- 代價：`owner_runs()` 回傳簽名從 3-tuple 變 4-tuple，所有呼叫端與
  `scripts/tests/test_pertrack_attrib.py` 既有的 tuple 相等斷言需同步
  改（38 條測試，行為斷言未放鬆，純簽名對齊）。

## 同根因掃描

全 repo 搜尋 `3dB`/`歸屬不確定`：只有 `pertrack_attrib.py` 的
`split_phrase()` 一處寫死該文案（已改），`pertrack_blocks.py` 只是
組裝既有 `reason`/加「暫掛 diarize」尾註，不重複寫死文案；
`insert_prepare.py`、`cutplan.py`、`diff_clips.py` 搜尋無命中；
`pertrack_cells.py`/`pertrack_render.py` 出現的 `3dB` 是插值增益、
串音殘留量的物理註解，跟 D2 歸屬判定無關。
