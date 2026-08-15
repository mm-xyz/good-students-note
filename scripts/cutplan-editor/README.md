# Cutplan 編輯器(Apps Script Web App)

Lifov 卡 **#674**。讓 MM 用手機/平板/桌機瀏覽器直接編輯 Google Drive 上的
podcast `cutplan.md`,只開放兩個動作:

1. **勾選切換** `- [x]` ↔ `- [ ]`
2. **字級刪除線** — **反白文字、選取穩定後自動套用**,不用再點按鈕
   (2026-08-11 MM 追加需求:剪一集要標數百處贅字,少一次點擊就是省一半
   動作)。依選取範圍內有沒有既有 `~~` 決定動作:完全沒有既有刪除線 →
   包成 `~~文字~~`;只要碰到既有刪除線(完全包含、部分重疊、橫跨多段皆
   算)→ 那些被碰到的刪除線整段一起拆掉,不要求選取邊界跟刪除線邊界對齊
   (這條語意的來歷、跟自動套用的判定策略,見 `docs/adr/ADR-2026-08-11-674.md`（第二節：刪除線互動模型）)。
   套用後會浮動出現一顆「復原」按鈕,貼在剛才那段文字的右上角,支援多步
   復原。選取為空(只有游標沒反白)不做事,不會靜默假裝套用成功。

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
│   ├── cutplan-core.test.js  52 個測試裡的 51 個:round-trip/checkbox/strike/唯讀/邊界/undo 堆疊
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
  (checkbox 切換、選取穩定後判斷要加還是去刪除線並自動套用、undo 堆疊)。

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

1. **前端 UI**:只有 checkbox、選取文字(自動套用刪除線)、「復原」按鈕
   三個互動路徑,id/時間碼/speaker/逐字稿文字全部渲染成純文字 `<span>`
   (不是 `<input>`,沒開 `contenteditable`)。
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
   clasp create --title "cutplan editor"      # 不要加 --type,見下方實測坑
   git checkout -- appsscript.json            # create 會蓋掉 manifest,務必還原
   clasp push --force
   clasp deploy -d "v1"                       # 回傳 deployment id
   ```
   網址 = `https://script.google.com/macros/s/<deployment id>/exec`。
   之後更新:`clasp push --force && clasp deploy -i <deployment id> -d "vN"`
   ——同一個 deployment id 重部署,網址不變。

   **2026-08-11 clasp 3.3.0 實測踩到的三個坑**(照上面的指令就避開了):
   1. **`--type webapp` 已不存在**。clasp 3.x 的 `--type` 只收
      `docs`/`forms`/`sheets`/`slides`/`standalone`(見 clasp 安裝目錄的
      `build/src/commands/create-script.js` 裡的 `DRIVE_FILE_MIMETYPES`),
      給 `webapp` 會報 `Invalid container file type`。`--help` 的說明文字
      還寫著「web app, or API」是 clasp 自己沒更新的文案。**這不影響功能**:
      web app 從來就不是專案類型而是部署類型,由 `appsscript.json` 的
      `webapp` 區塊決定,所以 standalone(預設)就是對的。
   2. **`clasp create` 會覆蓋本地的 `appsscript.json`**——它從新建的空專案
      拉一份預設 manifest 回來(`Cloned one file.. └─ appsscript.json`),
      `webapp` 區塊整個消失、`timeZone` 變成 `America/New_York`。**沒還原就
      `clasp push`,線上專案會沒有 web app 設定。** create 完務必先還原再 push。
   3. **需要先開 Apps Script API**。第一次跑 `clasp create` 會報
      `User has not enabled the Apps Script API`,去
      [script.google.com/home/usersettings](https://script.google.com/home/usersettings)
      把「Google Apps Script API」切成 On,等一兩分鐘生效。這是帳號層級的
      獨立開關,跟 `clasp login` 的 OAuth 授權是兩回事。

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
- **選取穩定判定是時間 heuristic,不是精確事件**(2026-08-11,見
  `docs/adr/ADR-2026-08-11-674.md`（第二節：刪除線互動模型）)。放開手指/滑鼠後等 60ms、純鍵盤選取等 450ms 沒
  再變化才視為穩定——這兩個數字沒有理論上界,是參考 prior art 抓的經驗值。
  極端情況(例如系統忙到事件延遲超過 60ms)理論上可能誤判成「已放開」而
  提早套用,但套用是可逆的(浮動出現的「復原」按鈕),不會弄壞資料,只是
  使用者要多點一次復原。
- **浮動復原按鈕的位置只在套用當下算一次**,不會跟著後續「跟這個位置無關
  的其他編輯」(例如勾選別的 block 的 checkbox)動態重算。因為那些操作不會
  改變已渲染文字的版面高度(checkbox 只切 CSS 透明度,不改變卡片高度),
  實測不會跑位;但如果未來版面規則改了(例如允許改變卡片高度的操作),
  要記得回頭檢查這個假設還成不成立。
- 復原按鈕的位置定位在**文件座標**(絕對定位,跟著頁面內容一起捲動),
  不是視窗座標;選了這個而不是「捲動就隱藏」,因為剪輯是斷續的,人可能
  盯著聽一段音檔才回頭決定要不要復原(見 ADR)。代價是如果視窗非常小
  導致按鈕怎麼夾都會蓋到附近的文字,目前只做水平/垂直邊界 clamp,沒有
  更精細的避讓演算法。
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
