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

## luna 守門 round 1：cause 累計窗口污染（已修，見下）

luna 抓到一個真 Major：`owner_runs()` 輸出的 None-run 只到 `cand_start`
為止（候選人成立後、正在撐穩定時長的 frame 屬於**下一段** owner run 的
醞釀期），但原本的累計是逐 frame 一路加到確立那一刻才分類，等於把
候選人撐穩定時長的 active frame 也算進了 cause 統計——本該是
`floor_gated` 的區間被這些「後段清楚領先」的 frame 污染成 `unstable`。

最小重現（luna 給的）：前 0.10s 三軌都在底噪（領先僅 2dB）、後 0.20s
某軌穩定領先 30dB（足以確立所有權）。修前 `owner_runs()` 回傳
`(0.0, 0.1, None, 'unstable')`，應為 `'floor_gated'`。

修法：`owner_runs()` 內部改成 committed／pending 雙桶。`pend_*` 是目前
存活候選人正在累積、還沒確定會不會被最終輸出排除的 frame；候選人換人
或本身失敗（below_floor 擋下／margin 不足）時，`pend_*` 併回
`comm_*`（那些 frame 仍在這個 None-run 的輸出範圍內）；候選人撐滿穩定
時長真的確立所有權時，`pend_*` 直接丟棄不進 `comm_*`（那些 frame 已經
是下一段 owner run 的一部分）。`cause` 分類只看 `comm_*`。已補回歸測試
`test_cause_window_excludes_frames_that_belong_to_the_confirmed_owner`，
紅（重現 `'unstable'` 誤判）→ 綠（`'floor_gated'` 正確）。commit
`b587d0e`。未動門檻/hysteresis/#726 bypass 本身，純修累計窗口對位。

## 量測（EP16 實資料，production `split_phrase` 直接產出的 `reason`
欄位，非另一套分類腳本；n_unc 前後一致 =708，證實純標示不改筆數）

**修正版跟先前送審版的數字有差**——先前版本吃到 luna 抓到的累計窗口
污染，偏差方向是「本該 floor_gated 的被誤標成 unstable」，修完後
floor_gated 佔比明顯回升：

| reason 文案 | 筆數(修前) | 筆數(修後) | 筆數%(修後) | 時長(s)(修後) | 時長%(修後) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 歸屬不確定（三軌皆近底噪，領先不足） | 400 | **494** | 69.8% | 261.1 | 79.4% |
| 歸屬不確定（領先未能持續） | 299 | **201** | 28.4% | 66.9 | 20.3% |
| 歸屬不確定（三軌差距 <3dB） | 9 | **13** | 1.8% | 0.7 | 0.2% |
| TOTAL | 708 | 708 | 100.0% | 328.7 | 100.0% |

（跑法：worktree `feat/728-reason-split` 的 production code＋主 checkout
`/Users/marslo/GithubRepo_mm-xyz/good-students-note/sessions/2026-07-27_
EP16-不要跟工作談戀愛/`（唯讀，worktree 內 `sessions/` 被 gitignore 沒有
實體）；腳本吃跟 `pertrack_blocks.main()` 完全一致的 D1/D2 呼叫鏈與
argparse 預設參數，直接讀 `split_phrase` 產出的 `reason` 欄位，不是另一套
分類腳本——這個路徑先前送審版就已經是這樣跑，本次只是用修好的 code
重跑一次；n_unc=708 前後不變，證實純標示改動不影響筆數這件事在 fix
前後都成立。below_margin 13 筆＝1.8%，仍跟 #677「字面成立僅 0.3%」
同量級，不是這次修正影響的分類邊界。）

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
