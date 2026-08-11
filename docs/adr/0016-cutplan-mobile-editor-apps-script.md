# ADR 0016 — cutplan.md 手機編輯器選 Apps Script、只開放兩個動作

- 日期：2026-08-11
- 狀態：已採納
- 相關：ADR 0001（cutplan.md 是人審真相源）、ADR 0015（podcast 剪輯線 SOP）
- Lifov 卡：#674

## 脈絡

MM 剪 podcast 時，人審 cutplan.md 的實際介面是 Google Drive 上的集數副本。
Drive 網頁版不能編輯 `.md`，MM 得回 Mac 開文字編輯器才能勾選/加刪除線——
手機、平板、外出時段全部剪不了，人審的節奏被綁在「有沒有帶電腦」上。

人審實際只做兩件事：翻勾選（`- [x]` ↔ `- [ ]`）、對贅字加刪除線
（`~~文字~~`）。其餘欄位（block id、時間碼、`[Speaker]`、逐字稿文字、
`## ✂`/`## 🎵`/`## ⚙`/`## 🎬`/`## 章節` 這些結構行）動不得——
`scripts/audio/render_cut.py` 的 `validate_program()` 會逐 block 把
cutplan.md 的文字跟 `cutplan.json`、來源 SRT 逐字比對，文字或時間碼被改壞
直接 FAIL。在桌面編輯器上這個 FAIL 訊息 MM 看得到；在手機上如果哪天真的
手滑改到一個字，MM 可能要等到出片失敗甚至出片但內容跑掉才發現。

## 決策

### 1. 用 Google Apps Script Web App，不做獨立 web app + OAuth

Apps Script 直接跑在 MM 的 Google 帳號底下，`DriveApp` 存取 Drive 檔案不需要
自己申請 OAuth client、管理 refresh token、擔心 token 外洩或過期。部署出來
是一個 `script.google.com/macros/.../exec` 網址，MM 加到手機主畫面就是一個
「App」。

代價：UI 只能用 Apps Script 的 `HtmlService`（一支 HTML 檔案送到 iframe），
沒有現代前端框架、沒有 hot reload、除錯要看 Apps Script 的執行紀錄。這條線
音檔處理全程 stdlib/ffmpeg、零 npm 依賴（見 `scripts/tests/run_all.sh`），
一個純 HTML + vanilla JS 的小工具跟這個技術選型一致，不需要為了開發體驗
引入 Node 後端 + 前端框架 + 自架 OAuth 這一整套。

### 2. 做專用介面，不做通用 markdown 編輯器

如果做一個通用 `.md` 編輯器（textarea 直接編全文），MM 在手機上一樣可以
手滑改到逐字稿文字或時間碼，跟現在拿桌面編輯器開檔案沒有本質差異——只是
換了個地方犯同一種錯,而且手機打字比桌面更容易誤觸。

專用介面把「勾選」「加/去刪除線」做成 UI 唯二的互動元件（checkbox、一個
浮動按鈕），id/時間碼/speaker/逐字稿文字全部渲染成不可編輯的純文字。**唯讀
是護欄,不是省事**——設計成「MM 想改壞都很難」，比「相信 MM 不會手滑」
更可靠。

### 3. 唯讀護欄做兩道,不只信任前端

前端 UI 只暴露兩個互動元件是第一道;但如果只做到這裡,前端一有 bug（例如
selection 座標算錯、re-render 邏輯出錯）壞資料一樣會被送到後端寫進 Drive。
所以 `Code.gs` 的 `saveCutplan()` 在覆寫檔案前,拿 Drive 上「現在」的版本跟
前端送來的新內容各自解析、逐行比對——只放行「block 的 `mark` 改變」與
「block 內文拿掉 `~~` 標記後文字不變」這兩種差異,其他一律拒絕寫入並丟出
具體是哪一行、哪個原因。這道檢查重用跟 Node 測試相同的 `cutplan-core.js`
(`parseCutplan`/`splitStrikes`)——Apps Script 專案裡的 `.gs`/`.js` 檔共享
全域命名空間,`cutplan-core.js` 原封不動部署成伺服端檔案就能被 `Code.gs`
直接呼叫,不需要另外重寫一份驗證邏輯。

### 4. 語意真相源是 Python,JS 只是另一個消費者

cutplan.md 的格式（LINE_RE、speaker 前綴、` ← 理由`、`~~刪除線~~` 配對規則）
唯一真相源是 `scripts/audio/cutplan.py`（產生）與 `scripts/audio/render_cut.py`
（`parse_program`/`parse_strikes` 解析、驗證）。`cutplan-core.js` 的正則與
拆解邏輯照抄這兩支檔案的規則,不自創第二套語意。差一處:Python 的
`parse_strikes` 用「去空白字元座標」記錄刪除線範圍(給後續換算時間碼用);
JS 版的 `splitStrikes` 保留空白字元座標(給瀏覽器 DOM 選取直接用)——這個
差異只存在於 JS 自己的內部資料結構,不影響寫回 Drive 的 `.md` 文字本身,
`render_cut.py` 讀到的 markdown 語法完全相同,不需要 JS 端知道 Python 端的
座標系統,兩邊各自獨立解析、輸出格式一致即可。

## 後果

- 好:MM 剪輯不再被「有沒有帶電腦」卡住,人審節奏可以碎片化。
- 好:兩道唯讀護欄讓「手機手滑改壞逐字稿」這個風險在寫入 Drive 前就被擋掉,
  不必等 `render_cut.py` FAIL 才發現。
- 代價:`cutplan-core.js` 要同時滿足 Node 測試與 Apps Script 瀏覽器端兩種
  執行環境,而 Apps Script `HtmlService` 只能內嵌 `.html` 檔案,所以多了一份
  `cutplan-core-inline.html`(同一份原始碼包 `<script>` 標籤)。用
  `tests/inline-sync.test.js` 逐 byte 比對防止兩份漂移,`tools/build-inline.js`
  是唯一產生方式,不靠人工複製貼上維持同步。
- 代價:部署仍需要 MM 手動跑一次(`clasp push` 或手動貼上 + 走一次 Google
  帳號授權流程),AI 不能代跑——`DriveApp` 的存取範圍要 MM 本人的帳號同意。
- 尚未落地:沒有離線編輯、沒有多裝置同時編輯的版本衝突偵測(`saveCutplan`
  是後寫覆蓋)。單人使用場景風險低,先不做;真的變成多人協作再回頭補。
