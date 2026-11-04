// 由 Index.html 產生 local.html(本機版,Lifov #1199)。
// UI 與編輯邏輯一律沿用 Index.html,只把 google.script.run 後端換成本機檔案:
//   開檔 = File System Access API(Chrome/Edge,可原地寫回);沒有就退回下載。
// 用法:node tools/build-local.js   (改了 Index.html 後重跑)
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname, '..');
let s = fs.readFileSync(path.join(dir, 'Index.html'), 'utf8');

function cut(startMarker, endMarker, replacement) {
  const a = s.indexOf(startMarker);
  const b = s.indexOf(endMarker, a + 1);
  if (a < 0 || b < 0) throw new Error('marker not found: ' + startMarker + ' / ' + endMarker);
  s = s.slice(0, a) + replacement + s.slice(b);
}
function sub(from, to) {
  if (!s.includes(from)) throw new Error('not found: ' + from.slice(0, 60));
  s = s.replace(from, () => to);
}

// 殼:Apps Script 的 include 換成同資料夾的 core
sub("<?!= include('cutplan-core-inline'); ?>", '<script src="cutplan-core.js"></script>');
s = s.replace(/<!--[\s\S]*?-->/, '<!-- local.html 由 tools/build-local.js 自 Index.html 產生,不要手改 -->');
sub('<header>', '<title>Cutplan 本機編輯器</title>\n<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n<header>');

// 開檔列取代集數下拉
sub(/<select id="episodeSelect">[\s\S]*?<\/select>/.exec(s)[0],
  `<div id="openRow" class="open-row">
    <button id="openPlan" type="button">① 開啟 cutplan.md</button>
    <button id="openTl" type="button">② timeline.json(選填)</button>
    <label id="pickAudio2" class="pick">③ 音檔 mp3<input id="audioFile2" type="file" accept="audio/*,.mp3,.m4a,.wav" hidden></label>
    <input id="planFallback" type="file" accept=".md,text/*" hidden>
    <input id="tlFallback" type="file" accept=".json,application/json" hidden>
  </div>`);
sub('header.collapsed #episodeSelect,', 'header.collapsed #openRow,');
sub('</style>', `.open-row { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:8px; }
  .open-row button, .open-row .pick { font-size:14px; padding:8px 12px; border-radius:8px; cursor:pointer;
    border:1px solid var(--border); background:var(--card-bg); color:var(--fg); }
  .open-row .ok { border-color:var(--accent); color:var(--accent); }
</style>`);

// 成品載入按鈕(Drive 專用)拿掉
sub(/    <button id="loadLatest"[^\n]*\n/.exec(s)[0], '');
sub(/    <button id="clearCache"[^\n]*\n/.exec(s)[0], '');
sub('<label id="pickAudio">改選本機音檔<input id="audioFile" type="file" accept="audio/*,.mp3,.m4a,.wav"></label>', '');
sub("    episodeSelect: document.getElementById('episodeSelect'),\n", '');
sub("header.collapsed #clearCache,\n", '');
sub("header.collapsed #pickAudio,", 'header.collapsed #pickAudio2,');

// 播放列:有載入 cutplan 就顯示(不必等 timeline)
sub('playerRow.hidden = !(state.doc && (state.timeline || Core.getTemplateVersion(state.doc) !== null));',
  'playerRow.hidden = !state.doc;');

// 另存副本 → 下載
cut('  // ── 存不了就另存副本', '  function setSaveMsg', `  // ── 存不了就下載副本 ─────────────────────────────────────────────────
  var copiedRev = -1;
  function download(name, content) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  function saveCopyFallback(content, why) {
    if (copiedRev === rev) return;
    copiedRev = rev;
    var d = new Date(), p = function (n) { return String(n).padStart(2, '0'); };
    var name = 'cutplan_' + String(d.getFullYear()).slice(2) + p(d.getMonth() + 1) + p(d.getDate())
      + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()) + '.md';
    download(name, content);
    setSaveMsg(why + ';已下載副本 ' + name + ',編輯沒丟', 'error');
  }

`);

// 集數清單 + loadCutplan → 本機開檔
cut('  // ── 集數清單', '  // ── 渲染', `  // ── 本機開檔 ──────────────────────────────────────────────────────────
  var fileHandle = null;      // 可原地寫回的 cutplan.md(File System Access)
  var origText = '';          // 載入/上次存檔時的內容,護欄用它比對
  var hasFS = !!window.showOpenFilePicker;
  function readFile(f) { return new Promise(function (res, rej) {
    var r = new FileReader(); r.onload = function () { res(r.result); }; r.onerror = rej; r.readAsText(f); }); }
  function mk(id, ok) { document.getElementById(id).classList.toggle('ok', !!ok); }

  function openPlan() {
    if (state.dirty && !confirm('目前的編輯還沒儲存,確定要開別的檔嗎?')) return;
    if (!hasFS) { document.getElementById('planFallback').click(); return; }
    window.showOpenFilePicker({ types: [{ description: 'cutplan', accept: { 'text/*': ['.md'] } }] })
      .then(function (hs) { fileHandle = hs[0]; return hs[0].getFile(); })
      .then(function (f) { return readFile(f).then(function (t) { loadCutplanText(t, f.name); }); })
      .catch(function (e) { if (e && e.name !== 'AbortError') setStatus('開檔失敗:' + e.message, 'error'); });
  }
  function openTl() {
    if (!hasFS) { document.getElementById('tlFallback').click(); return; }
    window.showOpenFilePicker({ types: [{ description: 'timeline', accept: { 'application/json': ['.json'] } }] })
      .then(function (hs) { return hs[0].getFile(); })
      .then(function (f) { return readFile(f).then(applyTimeline); })
      .catch(function (e) { if (e && e.name !== 'AbortError') setStatus('開檔失敗:' + e.message, 'error'); });
  }
  function applyTimeline(text) {
    state.timeline = Core.parseTimeline(text);
    state.timelineBad = Core.timelineFileState(text) === 'bad';
    mk('openTl', !!state.timeline);
    if (state.doc) render();
    if (state.timelineBad) setStatus('timeline.json 格式不合格,未啟用成品時間對照', 'error');
  }
  document.getElementById('openPlan').addEventListener('click', openPlan);
  document.getElementById('openTl').addEventListener('click', openTl);
  document.getElementById('planFallback').addEventListener('change', function () {
    var f = this.files[0]; if (!f) return; fileHandle = null;
    readFile(f).then(function (t) { loadCutplanText(t, f.name); });
  });
  document.getElementById('tlFallback').addEventListener('change', function () {
    var f = this.files[0]; if (f) readFile(f).then(applyTimeline);
  });
  document.getElementById('audioFile2').addEventListener('change', function () {
    var f = this.files && this.files[0]; if (!f) return;
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    audioUrl = URL.createObjectURL(f);
    audio.src = audioUrl; audio.hidden = false;
    document.getElementById('skipRow').hidden = false;
    clearPlaying(); mk('pickAudio2', true);
    var vs = state.timeline && state.timeline.versionShort;
    if (vs && f.name.indexOf(vs) === -1) {
      setStatus('注意:檔名沒有「' + vs + '」,跟 timeline 的版本可能不同,橫軸跟捲動會對不上', 'error');
    } else setStatus('已載入 ' + f.name, 'ok');
  });

  function loadCutplanText(text, name) {
    state.fileId = name;
    origText = text;
    setDirty(false);
    state.history = Core.createHistory();
    hideUndoFloat();
    state.doc = Core.parseCutplan(text);
    state.tracksMode = Core.hasTracksAudio(state.doc);
    mk('openPlan', true);
    autoCollapseAfterLoad();
    snapLines();
    refreshLastEditBtn();
    render();
    setStatus('已載入:' + name + (fileHandle ? '(存檔會直接寫回這個檔)'
      : '(此瀏覽器無法原地寫回,存檔會下載新檔)'), 'ok');
  }

`);

// 音檔快取 + 載入最新成品(Drive 專用)整段拿掉
cut('  // 音檔快取(Cache Storage)', "  audio.addEventListener('error'", '');
// 舊的 audioFile 事件(元素已拿掉)
cut("  document.getElementById('audioFile').addEventListener('change'", '  function skip(secs)', '');

// 存檔:護欄 → 寫回
const gsr = /      google\.script\.run\n        \.withSuccessHandler\(function \(\) \{\n          if \(done\) return;[\s\S]*?\.saveCutplan\(state\.fileId, content\);/;
if (!gsr.test(s)) throw new Error('save block not found');
s = s.replace(gsr, () => `      function ok() {
        if (done) return;
        done = true; clearTimeout(timer); saving = false;
        origText = content;
        state.history = Core.createHistory();
        hideUndoFloat();
        if (rev === savedRev) {
          setDirty(false);
          setSaveMsg((fileHandle ? '已存回原檔 ✓ ' : '已下載 ✓ ') + new Date().toLocaleTimeString('zh-TW', { hour12: false }), 'ok');
        } else {
          els.saveBtn.disabled = false;
          setSaveMsg('已儲存一部分,繼續編輯中…', 'ok');
          autoTimer = setTimeout(function () { doSave(true); }, AUTOSAVE_MS);
        }
      }
      function fail(err) {
        if (done) return;
        done = true; clearTimeout(timer);
        var m = err && err.message ? err.message : String(err);
        retryOrFail('儲存失敗:' + m, true);
      }
      var problem = Core.findIllegalEdit(origText, content);   // 同 Code.gs 的護欄
      if (problem) { fail(new Error('儲存被拒絕(超出允許的編輯範圍):' + problem)); return; }
      if (fileHandle) {
        fileHandle.createWritable()
          .then(function (w) { return w.write(content).then(function () { return w.close(); }); })
          .then(ok, fail);
      } else {
        download(state.fileId || 'cutplan.md', content);
        ok();
      }`);

// 啟動
sub('  loadEpisodeList();\n', '  setStatus(\'先按 ① 開啟 cutplan.md\');\n');

fs.writeFileSync(path.join(dir, 'local.html'), s);
console.log('local.html', s.length, 'bytes');
