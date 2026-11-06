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
    <button id="openDir" type="button">選資料夾(自動抓 cutplan／timeline／mp3)</button>
    <span id="openState" style="font-size:13px;color:var(--muted)"></span>
    <input id="dirFallback" type="file" webkitdirectory hidden>
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
cut('  // ── 集數清單', '  // ── 渲染', `  // ── 本機開資料夾 ──────────────────────────────────────────────────────
  // 選的資料夾可以是整個 session(…-分軌)或單一 vN_ 版本資料夾:
  //   cutplan.md  = 資料夾自己的,沒有就用 _asset/cutplan.md(出片吃的那份)
  //   timeline/mp3 = 資料夾自己的,沒有就取編號最大的 vN_ 子資料夾
  // Chrome/Edge 用 showDirectoryPicker 可原地寫回 cutplan.md;其他瀏覽器退回下載。
  var fileHandle = null;      // 可原地寫回的 cutplan.md
  var origText = '';          // 載入/上次存檔時的內容,護欄用它比對
  var hasFS = !!window.showDirectoryPicker;
  function readFile(f) { return new Promise(function (res, rej) {
    var r = new FileReader(); r.onload = function () { res(r.result); }; r.onerror = rej; r.readAsText(f); }); }
  function openState(t) { document.getElementById('openState').textContent = t || ''; }

  // 目錄抽象:list() → [{name, kind, h}];child(name) → 子目錄;file(name) → File
  function fsDir(h) {
    return {
      label: h.name,
      list: async function () { var o = []; for await (var e of h.values()) o.push({ name: e.name, kind: e.kind }); return o; },
      child: async function (n) { return fsDir(await h.getDirectoryHandle(n)); },
      file: async function (n) { return (await h.getFileHandle(n)).getFile(); },
      handle: async function (n) { return h.getFileHandle(n); },
    };
  }
  function listDir(files) {   // webkitdirectory 退路:用 webkitRelativePath 還原樹(唯讀)
    function node(prefix) {
      return {
        label: prefix.split('/').filter(Boolean).pop() || '',
        list: async function () {
          var seen = {}, out = [];
          files.forEach(function (f) {
            var rel = f.webkitRelativePath; if (rel.indexOf(prefix) !== 0) return;
            var rest = rel.slice(prefix.length), i = rest.indexOf('/'), n = i < 0 ? rest : rest.slice(0, i);
            if (seen[n]) return; seen[n] = 1; out.push({ name: n, kind: i < 0 ? 'file' : 'directory' });
          });
          return out;
        },
        child: async function (n) { return node(prefix + n + '/'); },
        file: async function (n) { return files.filter(function (f) { return f.webkitRelativePath === prefix + n; })[0]; },
        handle: async function () { return null; },
      };
    }
    var top = files[0].webkitRelativePath.split('/')[0] + '/';
    return node(top);
  }

  async function has(dir, name) { return (await dir.list()).some(function (e) { return e.name === name; }); }
  async function findAudio(dir) {
    var e = (await dir.list()).filter(function (x) { return x.kind === 'file' && /\\.(mp3|m4a|wav)$/i.test(x.name); })[0];
    return e ? dir.file(e.name) : null;
  }
  async function latestVersionDir(dir) {
    var best = null, n = -1;
    (await dir.list()).forEach(function (e) {
      var m = e.kind === 'directory' && /^v(\\d+)_/.exec(e.name);
      if (m && Number(m[1]) > n) { n = Number(m[1]); best = e.name; }
    });
    return best ? dir.child(best) : null;
  }

  async function openFolder(dir) {
    // cutplan.md
    var planDir = null;
    if (await has(dir, 'cutplan.md')) planDir = dir;
    else if (await has(dir, '_asset')) { var as = await dir.child('_asset'); if (await has(as, 'cutplan.md')) planDir = as; }
    if (!planDir) { setStatus('這個資料夾(含 _asset)裡找不到 cutplan.md', 'error'); return; }
    // 版本資料夾:自己有 mp3 就是它,否則取最新 vN_
    var verDir = (await findAudio(dir)) ? dir : await latestVersionDir(dir);
    var audioFile = verDir ? await findAudio(verDir) : null;
    var tlText = null;
    for (var d of [verDir, dir, planDir]) {
      if (d && await has(d, 'cutplan.timeline.json')) { tlText = await readFile(await d.file('cutplan.timeline.json')); break; }
    }
    fileHandle = await planDir.handle('cutplan.md');
    var planFile = await planDir.file('cutplan.md');
    var text = await readFile(planFile);
    loadCutplanText(text, 'cutplan.md', planDir.label + '/cutplan.md');
    if (tlText !== null) applyTimeline(tlText);
    if (audioFile) loadAudioFile(audioFile);

    // Lifov #1201: 自動載入 cutplan.json / words.json / cut_map.json / audio16k.wav 供詞級波形編輯器與一鍵剪輯預覽使用
    var cutplanData = null, wordsData = null, cutMapData = null, waveAudioFile = null;
    for (var sd of [planDir, dir]) {
      if (!sd) continue;
      if (!cutplanData && await has(sd, 'cutplan.json')) {
        try { cutplanData = JSON.parse(await readFile(await sd.file('cutplan.json'))); } catch (e) {}
      }
      if (!wordsData && await has(sd, 'words.json')) {
        try { wordsData = JSON.parse(await readFile(await sd.file('words.json'))); } catch (e) {}
      }
      if (!cutMapData && await has(sd, 'cut_map.json')) {
        try { cutMapData = JSON.parse(await readFile(await sd.file('cut_map.json'))); } catch (e) {}
      }
      if (!waveAudioFile) {
        for (var wn of ['audio16k.wav', 'source.wav', 'source.flac', 'source.m4a', 'source.mp3']) {
          if (await has(sd, wn)) { waveAudioFile = await sd.file(wn); break; }
        }
      }
    }
    if (window.WaveEditor) {
      window.WaveEditor.loadSessionAssets({
        cutplanData: cutplanData,
        wordsData: wordsData,
        cutMapData: cutMapData,
        waveAudioFile: waveAudioFile || audioFile,
      });
    }
    openState((verDir ? verDir.label + ' · ' : '') + [
      tlText !== null ? 'timeline' : '無 timeline',
      wordsData ? 'words.json' : '無 words',
      waveAudioFile ? waveAudioFile.name : (audioFile ? 'mp3' : '無音檔'),
    ].join(' · '));
  }

  document.getElementById('openDir').addEventListener('click', function () {
    if (state.dirty && !confirm('目前的編輯還沒儲存,確定要開別的資料夾嗎?')) return;
    if (!hasFS) { document.getElementById('dirFallback').click(); return; }
    window.showDirectoryPicker({ mode: 'readwrite' })
      .then(function (h) { return openFolder(fsDir(h)); })
      .catch(function (e) { if (e && e.name !== 'AbortError') setStatus('開資料夾失敗:' + e.message, 'error'); });
  });
  document.getElementById('dirFallback').addEventListener('change', function () {
    var files = Array.prototype.slice.call(this.files);
    if (!files.length) return;
    openFolder(listDir(files)).catch(function (e) { setStatus('開資料夾失敗:' + e.message, 'error'); });
  });

  function applyTimeline(text) {
    state.timeline = Core.parseTimeline(text);
    state.timelineBad = Core.timelineFileState(text) === 'bad';
    if (state.doc) render();
    if (state.timelineBad) setStatus('timeline.json 格式不合格,未啟用成品時間對照', 'error');
  }
  function loadAudioFile(f) {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    audioUrl = URL.createObjectURL(f);
    audio.src = audioUrl; audio.hidden = false;
    document.getElementById('skipRow').hidden = false;
    clearPlaying();
    var vs = state.timeline && state.timeline.versionShort;
    if (vs && f.name.indexOf(vs) === -1) {
      setStatus('注意:音檔名沒有「' + vs + '」,跟 timeline 的版本可能不同,橫軸跟捲動會對不上', 'error');
    }
  }

  function loadCutplanText(text, name, where) {
    state.fileId = name;
    origText = text;
    setDirty(false);
    state.history = Core.createHistory();
    hideUndoFloat();
    state.doc = Core.parseCutplan(text);
    state.tracksMode = Core.hasTracksAudio(state.doc);
    autoCollapseAfterLoad();
    snapLines();
    refreshLastEditBtn();
    render();
    setStatus('已載入:' + where + (fileHandle ? '(存檔會直接寫回這個檔)'
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

// 啟動與波形編輯器掛載 (#1201)
sub('    renderDiffStrip();\n  }', '    renderDiffStrip();\n    if (window.WaveEditor && window.WaveEditor.onAppRendered) window.WaveEditor.onAppRendered();\n  }');
sub('  loadEpisodeList();\n})();\n</script>', `  window.__cutplanApp = {
    state: state,
    els: els,
    render: render,
    setDirty: setDirty,
    setStatus: setStatus,
    doSave: doSave,
  };
  setStatus('按「選資料夾」開始');
})();
</script>
<script src="wave-editor.js"></script>`);

fs.writeFileSync(path.join(dir, 'local.html'), s);
console.log('local.html', s.length, 'bytes');
