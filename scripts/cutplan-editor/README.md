# Cutplan 編輯器(Apps Script Web App)

Lifov 卡 **#674**。讓 MM 用手機/平板/桌機瀏覽器直接編輯 Google Drive 上的
podcast `cutplan.md`,只開放兩個動作:

1. **勾選切換** `- [x]` ↔ `- [ ]`
2. **字級刪除線** — 反白 block 內文一段文字包成 `~~文字~~`;反白既有刪除線
   範圍還原。

其餘一切(block id、時間碼、`[Speaker]`、逐字稿文字、`## ✂`/`## 🎵`/`## ⚙`/
`## 🎬`/`## 章節`、註解、引言)**唯讀** —— 這不是省事,是護欄:
`scripts/audio/render_cut.py` 會逐 block 對 SRT 驗證,文字或時間碼被改壞
就 render 不出來,MM 在手機上不會發現(見下方 ADR)。

## 檔案

```
scripts/cutplan-editor/
├── cutplan-core.js          純函式核心(parse/serialize/toggleCheckbox/applyStrike…)
│                            零依賴,Node 測試與 Apps Script 伺服端共用同一份原始碼
├── cutplan-core-inline.html cutplan-core.js 逐字包 <script>,給瀏覽器端用
│                            (Apps Script HtmlService 限制,見下方「為什麼有兩份」)
├── Code.gs                  Apps Script 後端:doGet / listEpisodes / loadCutplan / saveCutplan
├── Index.html                前端 UI(手機優先)
├── appsscript.json           Apps Script 專案 manifest
├── tests/
│   ├── cutplan-core.test.js  40 個測試裡的 39 個:round-trip/checkbox/strike/唯讀/邊界
│   └── inline-sync.test.js   確保 cutplan-core-inline.html 沒有跟 cutplan-core.js 漂移
└── tools/
    ├── build-inline.js       重新產生 cutplan-core-inline.html
    └── check-real-file.js    一次性驗證工具(讀真實 cutplan.md 驗 round-trip,不進版控)
```

## 跑測試

```bash
node --test "scripts/cutplan-editor/tests/**/*.test.js"
```

**注意**:`node --test scripts/cutplan-editor/tests/`(不帶 glob 的目錄路徑)
在 Node 18–26 實測都**不會**遞迴掃描目錄——Node 官方文件寫的是路徑參數會被
當成 [glob(7)](https://man7.org/linux/man-pages/man7/glob.7.html) pattern,
沒有萬用字元的目錄字面值不會匹配任何檔案。要嘛用上面的 glob 寫法,要嘛
`cd scripts/cutplan-editor/tests && node --test`(無路徑參數 = 掃描 cwd)。

## 為什麼 cutplan-core.js 要有兩份長相

Apps Script 的 `HtmlService` 只能把專案裡的 **HTML 型別檔案**(副檔名
`.html`)內嵌進網頁送到瀏覽器;`.js`/`.gs` 副檔名的檔案在 Apps Script 專案
裡會被當成**伺服端** `SERVER_JS` 檔案(所有 `.gs`/`.js` 共享同一個全域命名
空間,`Code.gs` 可以直接呼叫 `cutplan-core.js` 匯出的 `parseCutplan` 這類
全域函式,不需要 require/import)——但伺服端函式不會自動出現在瀏覽器的
`window` 裡。

所以:

- `cutplan-core.js`(不改副檔名、原封不動)**同時**部署成 Apps Script 專案
  的伺服端檔案 —— `saveCutplan()` 用它做寫入前的第二道驗證(見下)。
- `cutplan-core-inline.html` = 同一份原始碼包一層 `<script>`,`Index.html`
  用 `<?!= include('cutplan-core-inline'); ?>` 內嵌,給瀏覽器端做即時互動
  (checkbox 切換、反白判斷要不要顯示「加/去刪除線」按鈕)。

兩份不會漂移,因為:
1. `tools/build-inline.js` 是唯一產生 `cutplan-core-inline.html` 的方式
   (直接 `cat` 包一層 `<script>`,零手動複製)。
2. `tests/inline-sync.test.js` 逐 byte 比對兩份檔案,少一個字就 FAIL。

改完 `cutplan-core.js` 之後:

```bash
node scripts/cutplan-editor/tools/build-inline.js
node --test "scripts/cutplan-editor/tests/**/*.test.js"
```

## 唯讀護欄有兩道

1. **前端 UI**:只有 checkbox 跟「加/去刪除線」按鈕兩個互動元件,id/時間碼/
   speaker/逐字稿文字全部渲染成純文字 `<span>`(不是 `<input>`,沒開
   `contenteditable`)。
2. **`Code.gs` 的 `saveCutplan()`**:寫回 Drive 前,拿 Drive 上「現在」的
   內容跟前端送來的新內容各自 `parseCutplan()`,逐行比對——只允許
   block 行的 `mark` 改變,或 block 內文「拿掉 `~~` 之後的文字不變」;
   行數改變、任何非 block 行被動到、id/時間碼/speaker/理由被改、逐字稿
   文字本身變了,一律丟錯拒絕寫入。就算前端有 bug 或被繞過,壞資料也
   進不了 Drive。

## MM 要自己做的部署步驟

1. 打開 [script.google.com](https://script.google.com/) → 新專案,或裝
   [`clasp`](https://github.com/google/clasp)(`npm install -g @google/clasp`,
   MM 自己的機器上裝,跟本 repo 的「零 npm 依賴」規則無關——這是部署工具
   不是專案依賴)。
2. **用 clasp**(建議,不用手動複製貼上):
   ```bash
   cd scripts/cutplan-editor
   clasp login
   clasp create --title "Cutplan 編輯器" --type webapp
   clasp push
   ```
   **手動貼上**(不用 clasp):在 Apps Script 編輯器裡建立對應檔名的檔案
   (`Code.gs`、`Index.html`、`cutplan-core.js`、`cutplan-core-inline.html`、
   `appsscript.json` 用專案設定裡的「顯示 appsscript.json」開啟編輯),逐一
   複製貼上內容。
3. 第一次執行 `doGet` 或 `listEpisodes`(或直接部署後開網頁)會跳出 Google
   帳號授權畫面,同意「查看、編輯、建立及刪除您的 Google 雲端硬碟中的檔案」
   權限(`DriveApp` 需要)——這一步只有 MM 本人的 Google 帳號能做。
4. 上方選單「部署」→「新增部署作業」→類型選「網頁應用程式」;
   執行身分「我」,存取權「僅限我自己」(`appsscript.json` 已預設這組,
   多人共用要自己改)。
5. 部署完成會給一個 `https://script.google.com/macros/s/.../exec` 網址,
   加進手機主畫面(iOS Safari「加入主畫面」/ Android Chrome「新增至主畫面」)
   當作 App 用。
6. 之後改程式碼:`clasp push` 或手動貼上覆蓋,再「管理部署作業」→ 編輯現有
   部署 → 選新版本,網址不變。

## 已知限制

- `listEpisodes()` 掃「我的雲端硬碟/水星貓的生活實驗室/1_Podcast 音檔/」
  底下每個子資料夾;找不到這個路徑會直接丟錯(不會靜默回空清單),部署到
  別的 Google 帳號或資料夾改了名字要改 `Code.gs` 的 `ROOT_FOLDER_PATH`。
- G 列(空白/非語音)沿用跟 B 列一樣的 parser,理論上也能加刪除線,但
  UI 沒有為 G 列額外設計顯示——它的內文是說明文字不是逐字稿,加刪除線
  沒有實際意義,只是 parser 沒有特別擋。
- 沒有離線/衝突處理:兩個裝置同時開同一集、都存檔,後存的會覆蓋先存的
  (`saveCutplan` 沒有版本檢查)。單人使用場景下風險低,先不做。
- 不支援新增/刪除 block、新增章節、編輯 G 列說明文字——這些本來就不在
  卡片範圍內(唯讀是設計,不是還沒做完)。
- **前導空白的 block 行會被視為唯讀**(⚠️ Minor,2026-08-11 independent
  reviewer 找到)。`cutplan-core.js` 的 `parseLine` 從行首直接匹配
  `BLOCK_LINE_RE`,而 `scripts/audio/render_cut.py` 的 `LINE_RE` 是套用在
  `line.strip()` 之後——若 cutplan.md 出現縮排的 block 行,render 端仍當
  正常 block 處理,但本編輯器會把它鎖成唯讀、無法勾選/加刪除線。方向是
  安全的(保守側,不會誤判唯讀行為可編輯造成污染),四份真實 cutplan
  掃描零命中,目前不影響任何已知檔案。要解的話:`parseLine` 比對前先剝離
  前導空白,另存成一個欄位,serialize 時把它接回行首還原。
- **`loadCutplan`/`saveCutplan` 不驗證 fileId 來源**(⚠️ Minor,同上次
  reviewer 發現)。兩個函式對任何 `fileId` 都會透過 `DriveApp.getFileById`
  讀寫,不限於 `listEpisodes()` 掃出來的 cutplan 檔案。因為
  `appsscript.json` 是 `access: MYSELF`,威脅模型侷限在 MM 自己的瀏覽器
  console(要主動打開開發者工具手動呼叫 `google.script.run.saveCutplan(其他
  fileId, ...)`),而且 `findIllegalEdit_` 的逐行結構比對幾乎必定會擋下
  非 cutplan 格式的內容(行數/block 格式對不上就丟錯)。列為防禦縱深待辦,
  不是可被第三人利用的漏洞。要解的話:`saveCutplan` 收到 `fileId` 後,先跟
  `listEpisodes()` 的結果集合比對,不在清單裡直接拒絕。
