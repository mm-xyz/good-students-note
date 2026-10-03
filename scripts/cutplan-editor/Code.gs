/**
 * scripts/cutplan-editor/Code.gs — Cutplan 編輯器 Apps Script Web App 後端(#674)。
 *
 * 只開放兩個編輯動作(勾選切換、字級刪除線),其餘一切唯讀 —— 這支檔案是
 * 唯讀護欄的「第二道防線」:前端已經只暴露這兩個動作的 UI,但 saveCutplan()
 * 在寫回 Drive 前還會再用 cutplan-core.js 的 parseCutplan/splitStrikes
 * (本檔與它一起部署,函式全域共享,見 README「為什麼 Code.gs 能直接呼叫
 * parseCutplan」)逐行比對舊檔與新內容,任何 id/時間碼/speaker/理由/非
 * block 行的變動,或 block 內文「拿掉刪除線標記後的文字」不同,一律拒絕
 * 寫入 —— 就算前端被繞過或有 bug,壞資料也進不了 Drive。
 *
 * doGet() 只回傳 UI;真正的讀寫都是 google.script.run 呼叫下面三個函式。
 */

// Drive 路徑:「我的雲端硬碟」/水星貓的生活實驗室/1_Podcast 音檔/<集數>/
//   cutplan.md 或 <集數>/_meta/cutplan.md 都要找(舊/新兩種佈局並存,ADR 0011)。
var ROOT_FOLDER_PATH = ['水星貓的生活實驗室', '1_Podcast 音檔'];
var CUTPLAN_FILENAME = 'cutplan.md';

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('Cutplan 編輯器')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// Index.html 用 `<?!= include('cutplan-core-inline'); ?>` 內嵌瀏覽器版的
// cutplan-core.js(包成 <script> 的那份,見 tests/inline-sync.test.js)。
function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

/** 依序找子資料夾,找不到就丟錯(不靜默建立、不猜路徑)。 */
function findSubfolder_(parent, name) {
  var it = parent.getFoldersByName(name);
  if (!it.hasNext()) {
    throw new Error('找不到資料夾「' + name + '」(在「' + parent.getName() + '」底下)');
  }
  return it.next();
}

function findRootFolder_() {
  var folder = DriveApp.getRootFolder();
  for (var i = 0; i < ROOT_FOLDER_PATH.length; i++) {
    folder = findSubfolder_(folder, ROOT_FOLDER_PATH[i]);
  }
  return folder;
}

/** 資料夾底下直接找一個檔名,找不到回 null(不是錯誤——集數資料夾兩種佈局並存)。 */
function findFileByName_(folder, name) {
  var it = folder.getFilesByName(name);
  return it.hasNext() ? it.next() : null;
}

/**
 * 掃「1_Podcast 音檔/」底下每個集數子資料夾,找 cutplan.md(舊佈局,直接在
 * 集數資料夾底下)或 _meta/cutplan.md(新佈局,ADR 見 docs/adr/0011)。
 * 回傳 [{name, fileId, layout, modifiedAt}, ...],新的在前面(用檔案最後修改
 * 時間排序——集數資料夾名不保證都是可排序的日期字串)。
 */
function listEpisodes() {
  var root = findRootFolder_();
  var episodes = [];
  var folders = root.getFolders();
  while (folders.hasNext()) {
    var epFolder = folders.next();
    var direct = findFileByName_(epFolder, CUTPLAN_FILENAME);
    var layout = 'direct';
    var file = direct;
    if (!file) {
      var metaIt = epFolder.getFoldersByName('_meta');
      if (metaIt.hasNext()) {
        file = findFileByName_(metaIt.next(), CUTPLAN_FILENAME);
        layout = 'meta';
      }
    }
    if (file) {
      episodes.push({
        name: epFolder.getName(),
        fileId: file.getId(),
        layout: layout,
        modifiedAt: file.getLastUpdated().toISOString(),
      });
    }
  }
  episodes.sort(function (a, b) { return b.modifiedAt.localeCompare(a.modifiedAt); });
  return episodes;
}

var TIMELINE_FILENAME = 'cutplan.timeline.json';

function loadCutplan(fileId) {
  var file = DriveApp.getFileById(fileId);
  return {
    fileId: fileId,
    name: file.getName(),
    content: file.getBlob().getDataAsString('UTF-8'),
    timeline: loadTimeline_(file),
  };
}

/**
 * 同資料夾的 cutplan.timeline.json(Lifov #1078,cut.py 出片時推上來的成品時間
 * 對照)。沒有就回 null —— 那只代表還沒出過片或舊版,不是錯誤,不報錯。
 * 編輯器對它只讀,saveCutplan 不寫它(唯讀護欄不受影響)。
 */
function loadTimeline_(file) {
  try {
    var parents = file.getParents();
    if (!parents.hasNext()) return null;
    var tl = findFileByName_(parents.next(), TIMELINE_FILENAME);
    return tl ? tl.getBlob().getDataAsString('UTF-8') : null;
  } catch (e) {
    return null;
  }
}

/**
 * 逐行比對 oldContent → newContent,回傳第一個「超出允許編輯範圍」的說明字串,
 * 沒問題回 null。只允許:block 行的 mark、block 內文「拿掉 ~~ 之後的文字不變」
 * 這兩種改動;其他任何差異(行數、非 block 行、id/時間碼/speaker/理由、
 * 逐字稿文字本身)一律視為不合法。
 *
 * 用的是跟 Node 測試同一支 cutplan-core.js(parseCutplan/splitStrikes 是它
 * 匯出的全域函式,Apps Script 專案裡多個 .gs/.js 檔共用同一個全域命名空間,
 * 這支檔案只要跟 cutplan-core.js 一起部署,不需要額外 require/import)。
 */
function findIllegalEdit_(oldContent, newContent) {
  // 規則本體住 cutplan-core.js 的 findIllegalEdit(2026-10-03 搬過去,讓 Node
  // 測試守得到;tests/tracks.test.js 用 vm 把本檔跟 core 一起載入驗 saveCutplan)。
  // 允許:勾選、加/去刪除線、⚙ 有 audio=tracks 時翻軌欄 ●/○;其餘一律拒絕。
  return findIllegalEdit(oldContent, newContent);
}

/**
 * 寫回**同一個檔案**(DriveApp setContent 原地覆寫,不改檔名、不改路徑、
 * 不建新檔)。寫入前用 findIllegalEdit_ 再驗一次,擋掉任何超出「勾選切換 +
 * 字級刪除線」範圍的內容 —— 唯讀是護欄,不是省事(見 PRD/#674)。
 */
function saveCutplan(fileId, content) {
  var file = DriveApp.getFileById(fileId);
  var oldContent = file.getBlob().getDataAsString('UTF-8');
  var problem = findIllegalEdit_(oldContent, content);
  if (problem) {
    throw new Error('儲存被拒絕(超出允許的編輯範圍):' + problem);
  }
  file.setContent(content);
  return { ok: true, savedAt: new Date().toISOString() };
}
