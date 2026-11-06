'use strict';
// scripts/cutplan-editor/wave-editor.js
//
// Lifov #1201 (#1220 M3 + #1221 M4):
// 1. CutPlayer: Web Audio API 依剪輯範圍 (Final = (Base \ Cut) ∪ Keep) 無縫一鍵播放 + 4ms 微淡入淡出防爆音
// 2. SoundCraft 移植雙層 HiDPI Canvas 波形渲染 (Peaks BLOCK=32, FANOUT=4)
// 3. 全軌與詞級 100% MECE 子積木 (B0001-1, B0001-2, B0001-3... + 句間停頓)
// 4. Smart Tool 互動:
//    - 邊界 ±8px: TrimStart / TrimEnd 手把 (預設吸附 words.json 詞邊界,按住 Alt/Option 或 Cmd 切換 1ms 自由覆蓋)
//    - 波形上半部: Selector 框選範圍 → 一鍵「🎯 強制只留此範圍 (ABBC 覆蓋)」「✂ 強制切」「✚ 強制留」
//    - 波形下半部 / 詞積木列: Grabber 單擊切換子積木保留/切除,雙擊從該詞起算依剪輯範圍播放

(function () {
  var Core = window.CutplanCore;
  if (!Core) return;

  var IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || '');
  var HANDLE_HIT_PX = 8;
  var MIN_KEEP_MS = 30;

  // ── Web Audio CutPlayer (M3 #1220) ─────────────────────────────────────────
  function CutPlayer() {
    this.ctx = null;
    this.buffer = null;
    this.playlist = [];
    this.gen = 0;
    this.nodes = [];
    this.timer = null;
    this.rafId = null;
    this.playing = false;
    this.playStartCtxTime = 0;
    this.cursorSrcMs = 0;
    this.onTick = null;
    this.onStateChange = null;
  }

  CutPlayer.prototype.ensureContext = function () {
    if (!this.ctx) {
      var AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx({ sampleRate: 16000 });
    }
    if (this.ctx.state === 'suspended') {
      this.ctx.resume().catch(function () {});
    }
    return this.ctx;
  };

  CutPlayer.prototype.decodeBlob = async function (blob) {
    var ctx = this.ensureContext();
    var ab = await blob.arrayBuffer();
    var decoded = await ctx.decodeAudioData(ab);
    // 若為多聲道則混成單聲道 Float32Array 節省記憶體並供 Peaks 金字塔直接索引
    if (decoded.numberOfChannels > 1) {
      var mono = ctx.createBuffer(1, decoded.length, decoded.sampleRate);
      var out = mono.getChannelData(0);
      var chCount = decoded.numberOfChannels;
      for (var c = 0; c < chCount; c++) {
        var ch = decoded.getChannelData(c);
        for (var i = 0; i < decoded.length; i++) {
          out[i] += ch[i] / chCount;
        }
      }
      this.buffer = mono;
    } else {
      this.buffer = decoded;
    }
    return this.buffer;
  };

  CutPlayer.prototype.stop = function (silent) {
    this.gen++;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    if (this.ctx && this.nodes.length) {
      var now = this.ctx.currentTime;
      this.nodes.forEach(function (item) {
        try {
          item.gain.gain.cancelScheduledValues(now);
          item.gain.gain.setTargetAtTime(0, now, 0.002);
          item.source.stop(now + 0.01);
        } catch (e) { /* ignore already stopped */ }
      });
    }
    this.nodes = [];
    var wasPlaying = this.playing;
    this.playing = false;
    if (wasPlaying && !silent && typeof this.onStateChange === 'function') {
      this.onStateChange(false);
    }
  };

  CutPlayer.prototype.playIntervals = function (finalIntervalsMs, options) {
    options = options || {};
    if (!this.buffer) return false;
    var ctx = this.ensureContext();
    var startFrom = options.startFromSrcMs != null ? options.startFromSrcMs : this.cursorSrcMs;
    var maxTo = options.maxToSrcMs != null ? options.maxToSrcMs : Infinity;
    var pl = Core.buildCutPlaylist(finalIntervalsMs, {
      startFromSrcMs: startFrom,
      maxToSrcMs: maxTo,
      fadeMs: 4,
    });
    if (!pl.length && startFrom > 0) {
      // 若游標已在最末段之後,自動從頭播放
      pl = Core.buildCutPlaylist(finalIntervalsMs, {
        startFromSrcMs: 0,
        maxToSrcMs: maxTo,
        fadeMs: 4,
      });
    }
    if (!pl.length) return false;

    this.stop(true);
    var myGen = ++this.gen;
    this.playlist = pl;
    this.playing = true;
    var leadSec = 0.03;
    this.playStartCtxTime = ctx.currentTime + leadSec;
    var segIdx = 0;
    var self = this;

    function scheduleLookahead() {
      if (myGen !== self.gen || !self.playing) return;
      var horizonSec = ctx.currentTime + 1.2;
      while (segIdx < pl.length) {
        var seg = pl[segIdx];
        var whenSec = self.playStartCtxTime + seg.dstStartMs / 1000;
        if (whenSec > horizonSec) break;
        var durSec = seg.durationMs / 1000;
        var srcSec = seg.srcStartMs / 1000;
        var fadeSec = Math.min(0.004, durSec / 2);

        var srcNode = ctx.createBufferSource();
        var gainNode = ctx.createGain();
        srcNode.buffer = self.buffer;
        srcNode.connect(gainNode);
        gainNode.connect(ctx.destination);

        if (fadeSec > 0.0005) {
          gainNode.gain.setValueAtTime(0.0001, whenSec);
          gainNode.gain.linearRampToValueAtTime(1.0, whenSec + fadeSec);
          gainNode.gain.setValueAtTime(1.0, Math.max(whenSec + fadeSec, whenSec + durSec - fadeSec));
          gainNode.gain.linearRampToValueAtTime(0.0001, whenSec + durSec);
        } else {
          gainNode.gain.setValueAtTime(1.0, whenSec);
        }
        srcNode.start(whenSec, srcSec, durSec);
        self.nodes.push({ source: srcNode, gain: gainNode, endSec: whenSec + durSec });
        segIdx++;
      }
      // 清理已播畢的節點
      var now = ctx.currentTime;
      self.nodes = self.nodes.filter(function (n) { return n.endSec > now - 0.1; });
      if (segIdx < pl.length) {
        self.timer = setTimeout(scheduleLookahead, 40);
      }
    }

    function stepRaf() {
      if (myGen !== self.gen || !self.playing) return;
      var latSec = (ctx.outputLatency || ctx.baseLatency || 0);
      var elapsedMs = (ctx.currentTime - latSec - self.playStartCtxTime) * 1000;
      var mapped = Core.mapPlaybackTimeToSourceMs(pl, Math.max(0, elapsedMs));
      if (mapped.srcMs != null) {
        self.cursorSrcMs = mapped.srcMs;
      }
      if (typeof self.onTick === 'function') {
        self.onTick(self.cursorSrcMs, mapped);
      }
      if (!mapped.playing && elapsedMs > 0) {
        self.stop(false);
        return;
      }
      self.rafId = requestAnimationFrame(stepRaf);
    }

    scheduleLookahead();
    if (typeof this.onStateChange === 'function') {
      this.onStateChange(true);
    }
    this.rafId = requestAnimationFrame(stepRaf);
    return true;
  };

  // ── 波形編輯器主狀態 (M4 #1221) ───────────────────────────────────────────
  var wState = {
    player: new CutPlayer(),
    samples: null,
    sampleRate: 16000,
    peaks: null,
    durationMs: 0,
    cutplanData: null,
    wordsData: null,
    cutMapData: null,
    partition: null,
    baseIntervals: [],
    overrides: { cut: [], keep: [] },
    finalIntervals: [],
    snapPointsMs: [],
    viewStartMs: 0,
    viewEndMs: 15000,
    cursorMs: 0,
    focusedBlockId: null,
    slipModeForced: false, // UI 切換「🔓 強制覆蓋 (ABBC 自由切)」或按住 Alt/Cmd
    autoPreviewSeam: true,
    selection: null,       // { startMs, endMs }
    gesture: null,
    hoverTool: null,
    audioFileName: '',
  };

  // ── 注入 CSS 與 DOM 面板 ───────────────────────────────────────────────────
  function injectUI() {
    var style = document.createElement('style');
    style.textContent = [
      '#waveDock {',
      '  position: sticky; top: 0; z-index: 12;',
      '  background: var(--bg); border-bottom: 2px solid var(--border);',
      '  padding: 8px 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.35);',
      '}',
      '#waveDock[hidden] { display: none; }',
      '.wave-bar { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; margin-bottom: 6px; }',
      '.wave-bar button, .wave-bar .w-pick {',
      '  font-size: 13px; padding: 6px 10px; border-radius: 7px; cursor: pointer;',
      '  border: 1px solid var(--border); background: var(--card-bg); color: var(--fg);',
      '}',
      '.wave-bar button.primary {',
      '  background: var(--accent); color: #04241a; font-weight: 700; border: none;',
      '}',
      '.wave-bar button.active-mode {',
      '  border-color: #ff9f43; background: rgba(255, 159, 67, 0.18); color: #ffbe76; font-weight: 700;',
      '}',
      '.wave-badge {',
      '  font-size: 11px; padding: 3px 8px; border-radius: 999px;',
      '  background: rgba(61, 214, 140, 0.15); color: var(--accent); font-family: ui-monospace, monospace;',
      '}',
      '.wave-badge.warn { background: rgba(255, 107, 107, 0.18); color: var(--danger); }',
      '#waveStage {',
      '  position: relative; width: 100%; height: 158px;',
      '  background: #10141a; border: 1px solid var(--border); border-radius: 8px;',
      '  overflow: hidden; user-select: none; -webkit-user-select: none; touch-action: none;',
      '}',
      '#waveCanvas, #waveOverlay {',
      '  position: absolute; left: 0; top: 0; width: 100%; height: 100%; display: block;',
      '}',
      '#waveSelBar {',
      '  display: none; gap: 6px; align-items: center; flex-wrap: wrap;',
      '  margin-top: 6px; padding: 6px 10px; border-radius: 7px;',
      '  background: rgba(255, 159, 67, 0.12); border: 1px solid #ff9f43; font-size: 12px;',
      '}',
      '#waveSelBar.show { display: flex; }',
      '#waveSelBar button {',
      '  font-size: 12px; padding: 4px 9px; border-radius: 6px; cursor: pointer;',
      '  border: 1px solid var(--border); background: var(--card-bg); color: var(--fg);',
      '}',
      '#waveSelBar button.abbc-btn {',
      '  background: #ff9f43; color: #1a1204; font-weight: 700; border: none;',
      '}',
      '#waveAtomsBar {',
      '  display: flex; gap: 4px; overflow-x: auto; padding: 6px 2px 2px;',
      '  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;',
      '}',
      '.atom-chip {',
      '  flex: none; display: inline-flex; flex-direction: column; align-items: flex-start;',
      '  padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border);',
      '  background: var(--card-bg); cursor: pointer; position: relative; overflow: hidden;',
      '  min-width: 54px;',
      '}',
      '.atom-chip.cut { opacity: 0.48; text-decoration: line-through; border-style: dashed; }',
      '.atom-chip.partial { border-color: #ff9f43; }',
      '.atom-chip .atom-fill {',
      '  position: absolute; top: 0; bottom: 0; background: rgba(61, 214, 140, 0.22);',
      '  pointer-events: none;',
      '}',
      '.atom-chip .atom-id { font-size: 10px; color: var(--muted); z-index: 1; }',
      '.atom-chip .atom-txt { font-size: 13px; font-weight: 600; color: var(--fg); z-index: 1; }',
      '.wave-jump-btn {',
      '  font-size: 11px; padding: 2px 7px; border-radius: 5px; cursor: pointer;',
      '  border: 1px solid var(--border); background: transparent; color: var(--accent);',
      '  margin-left: auto;',
      '}',
      '.block-card { cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s; }',
      '.block-card:hover { border-color: var(--accent); }',
      '.block-card.focused-block { border-color: #2ed573 !important; box-shadow: 0 0 0 2px rgba(46, 213, 115, 0.35); }',
      '#waveHelpBox {',
      '  display: none; font-size: 12px; line-height: 1.5; background: var(--card-bg);',
      '  border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 8px;',
      '}',
      '#waveHelpBox.show { display: block; }',
      '#waveHelpBox code { font-family: ui-monospace, monospace; background: rgba(0,0,0,0.06); padding: 1px 4px; border-radius: 4px; }',
    ].join('\n');
    document.head.appendChild(style);

    var dock = document.createElement('section');
    dock.id = 'waveDock';
    dock.hidden = true;
    dock.innerHTML = [
      '<div class="wave-bar">',
      '  <button id="wPlayCutBtn" type="button" class="primary" title="依目前剪輯範圍無縫播放(自動跳過切除段,快捷鍵 Space)">▶ 一鍵依剪輯範圍播放</button>',
      '  <button id="wPlaySeamBtn" type="button" title="試聽游標前後 ±1.5 秒剪輯接縫 (Shift+Space)">🎧 試聽切點 (±1.5s)</button>',
      '  <button id="wSlipModeBtn" type="button" title="開啟後(或按住 Alt/Option/Cmd)拖拉不吸附詞邊界,可從 AA/BB/CC 硬切出 ABBC">🧲 詞邊界吸附 (按住 Alt 自由覆蓋)</button>',
      '  <button id="wUndoBtn" type="button" title="復原上一步剪輯 (⌘Z / Ctrl+Z)">↩ 復原</button>',
      '  <button id="wZoomInBtn" type="button" title="放大波形">🔍＋</button>',
      '  <button id="wZoomOutBtn" type="button" title="縮小波形">🔍－</button>',
      '  <button id="wZoomBlockBtn" type="button" title="聚焦目前語音段">🎯 聚焦本句</button>',
      '  <button id="wHelpBtn" type="button" title="查看加減切點與快捷鍵操作說明">❓ 操作指南</button>',
      '  <label class="w-pick" title="手動載入 audio16k.wav / source.wav">📂 換波形音檔<input id="wAudioInput" type="file" accept="audio/*,.wav,.flac,.m4a,.mp3" hidden></label>',
      '  <span id="wMeceBadge" class="wave-badge">等待載入音訊/詞時間碼…</span>',
      '  <span id="wTimeReadout" style="font-size:12px;color:var(--muted);font-family:ui-monospace,monospace;margin-left:auto">0.000s</span>',
      '</div>',
      '<div id="waveHelpBox">',
      '  <b>加減切點操作指南：</b><br>',
      '  • <b>加切點（切除 / 保留）</b>：',
      '    1. <b>點詞積木</b>：點擊下方詞積木（變紅切除；再點一次變綠保留）。<br>',
      '    2. <b>拖曳邊界</b>：拖拉亮綠波形邊緣手把（預設吸附詞邊界；按住 <code>Alt/Option</code> 自由滑動 1ms 硬切）。<br>',
      '    3. <b>波形框選</b>：上半部拖曳選取 ➔ 按 <code>X</code> 或 <code>Delete</code>（✂ 切除）／按 <code>A</code>（⚡ ABBC 覆蓋）／按 <code>K</code>（✚ 保留）。<br>',
      '    4. <b>游標快捷鍵</b>：游標停在詞積木上按 <code>S</code> 鍵切換切除。<br>',
      '  • <b>減切點（消除手動切點）</b>：',
      '    1. 框選包含切點的區間 ➔ 按 <code>D</code> 鍵或點「↺ 清除覆蓋」。<br>',
      '    2. 點擊變紅的詞積木恢復保留。<br>',
      '    3. 快捷鍵 <code>⌘Z</code> / <code>Ctrl+Z</code> 或點「↩ 復原」。<br>',
      '  • <b>逐字稿與播放</b>：點擊任何 Block 卡片即自動跳轉對齊；反白文字按 <code>Delete/S</code> 劃線；按 <code>Space</code> 一鍵播放剪輯範圍；按 <code>Shift+Space</code> 試聽接縫。',
      '</div>',
      '<div id="waveStage">',
      '  <canvas id="waveCanvas"></canvas>',
      '  <canvas id="waveOverlay"></canvas>',
      '</div>',
      '<div id="waveSelBar">',
      '  <span id="wSelLabel">選取區間:</span>',
      '  <button id="wForceAbbcBtn" type="button" class="abbc-btn" title="將所涉詞積木(如 AA/BB/CC)硬切成只留此選取範圍(ABBC),優先級最高">🎯 強制只留此範圍 (ABBC 覆蓋)</button>',
      '  <button id="wForceCutBtn" type="button" title="強制切除選取範圍 (## ✂ wave)">✂ 強制切除</button>',
      '  <button id="wForceKeepBtn" type="button" title="強制保留選取範圍 (## ✚ wave)">✚ 強制保留</button>',
      '  <button id="wClearOvBtn" type="button" title="清除選取範圍內的 ✂/✚ 手動覆蓋">↺ 清除覆蓋</button>',
      '  <button id="wPlaySelBtn" type="button" title="只試聽選取區間">▶ 試聽選取</button>',
      '</div>',
      '<div id="waveAtomsBar" title="點擊詞積木切換保留/切除;拖拉上方波形邊界手把或按住 Alt 可硬切 ABBC"></div>',
    ].join('\n');

    var blockList = document.getElementById('blockList');
    if (blockList && blockList.parentNode) {
      blockList.parentNode.insertBefore(dock, blockList);
    }
  }

  function app() {
    return window.__cutplanApp || null;
  }

  // ── 同步 Partition 與三層區間 (Base / Overrides / Final) ────────────────────
  function rebuildIntervals() {
    var a = app();
    if (!a || !a.state || !a.state.doc) return;
    var doc = a.state.doc;

    // 1) 提取 cutplan.md 內的 ✂ / ✚ 覆蓋
    var ext = Core.extractReviewOverrides(doc);
    wState.overrides = ext.combined;
    wState.managedOverrides = ext.managed;

    // 2) 建立或更新全軌 100% MECE Partition
    var durMs = wState.durationMs || 0;
    var seamFn = (wState.peaks && wState.samples)
      ? function (lo, hi) {
        return Core.findValleySeamMs(wState.peaks, wState.samples, wState.sampleRate, lo, hi, 5);
      }
      : null;

    var cutplanSource = wState.cutplanData;
    if (!cutplanSource || !Array.isArray(cutplanSource.blocks) || !cutplanSource.blocks.length) {
      // 若尚未載入 cutplan.json,從 cutplan.md 的 editable 行即時推導 blocks,照樣達成全軌 MECE!
      var derivedBlocks = [];
      doc.lines.forEach(function (l) {
        if (!l.editable || !l.id || !l.timecode) return;
        if (!/^B\d|^MR\d|^SR\d|^KN\d/.test(l.id)) return;
        var parts = String(l.timecode).split(/[-–~]/);
        if (parts.length !== 2) return;
        var sMs = Core.parseEdgeTimeMs(parts[0]);
        var eMs = Core.parseEdgeTimeMs(parts[1]);
        if (sMs !== null && eMs !== null && eMs > sMs) {
          derivedBlocks.push({
            id: l.id,
            start: sMs / 1000,
            end: eMs / 1000,
            speaker: (l.prefix || '').trim(),
            text: Core.splitStrikes(l.bodyRaw || '').clean,
          });
        }
      });
      cutplanSource = { blocks: derivedBlocks, gaps: [] };
    }

    wState.partition = Core.buildTrackPartition(
      cutplanSource,
      wState.wordsData || [],
      durMs || null,
      seamFn ? { seamFn: seamFn } : {},
    );
    if (!wState.durationMs && wState.partition.durationMs > 0) {
      wState.durationMs = wState.partition.durationMs;
      if (wState.viewEndMs > wState.durationMs) {
        wState.viewEndMs = Math.min(wState.durationMs, 15000);
      }
    }

    // 驗證全軌與所有子積木 100% MECE
    var meceErrTop = Core.assertMECE(wState.partition.blocks, 0, wState.partition.durationMs);
    var meceErrAtoms = Core.assertMECE(wState.partition.allAtoms, 0, wState.partition.durationMs);
    var badge = document.getElementById('wMeceBadge');
    if (badge) {
      if (!meceErrTop && !meceErrAtoms) {
        badge.className = 'wave-badge';
        badge.textContent = '✓ 全軌 MECE ('
          + wState.partition.blocks.length + ' 區塊 / '
          + wState.partition.allAtoms.length + ' 詞積木)';
      } else {
        badge.className = 'wave-badge warn';
        badge.textContent = '⚠ ' + (meceErrTop || meceErrAtoms);
      }
    }

    // 收集所有詞積木邊界供吸附
    var pts = new Set([0, wState.partition.durationMs]);
    wState.partition.allAtoms.forEach(function (at) {
      pts.add(at.s);
      pts.add(at.e);
    });
    wState.snapPointsMs = Array.from(pts).sort(function (x, y) { return x - y; });

    // 3) 計算 Base 與 Final = (Base \ Cut) ∪ Keep
    wState.baseIntervals = Core.computeBaseIntervalsFromDoc(doc, wState.partition);
    wState.finalIntervals = Core.computeFinalIntervals(wState.baseIntervals, wState.overrides);

    var dock = document.getElementById('waveDock');
    if (dock) dock.hidden = false;

    renderAtomsStrip();
    drawWaveLayer();
    drawOverlayLayer();
  }

  // ── 計算某個子區間 [s, e) 在 Final 中的保留比例與狀態 ──────────────────────
  function getAtomKeepStatus(s, e) {
    var kept = Core.intersectIntervals([[s, e]], wState.finalIntervals);
    var keptMs = kept.reduce(function (acc, r) { return acc + (r[1] - r[0]); }, 0);
    var total = Math.max(1, e - s);
    var ratio = keptMs / total;
    if (keptMs <= 0) return { state: 'cut', ratio: 0, keptRanges: [] };
    if (keptMs >= total - 1) return { state: 'kept', ratio: 1, keptRanges: kept };
    return { state: 'partial', ratio: ratio, keptRanges: kept };
  }

  // ── 詞積木橫列 (B0001-1 / B0001-2 / B0001-3 ...) ───────────────────────────
  function renderAtomsStrip() {
    var bar = document.getElementById('waveAtomsBar');
    if (!bar || !wState.partition) return;
    bar.innerHTML = '';

    // 顯示目前視窗 [viewStartMs, viewEndMs] 或聚焦 block 的詞積木
    var targetBlock = null;
    if (wState.focusedBlockId) {
      targetBlock = wState.partition.blocks.find(function (b) { return b.id === wState.focusedBlockId; });
    }
    var atoms = targetBlock
      ? targetBlock.atoms
      : wState.partition.allAtoms.filter(function (at) {
        return at.e > wState.viewStartMs && at.s < wState.viewEndMs;
      }).slice(0, 60);

    atoms.forEach(function (at) {
      var st = getAtomKeepStatus(at.s, at.e);
      var chip = document.createElement('div');
      chip.className = 'atom-chip'
        + (st.state === 'cut' ? ' cut' : st.state === 'partial' ? ' partial' : '');
      chip.title = at.id + ' [' + (at.s / 1000).toFixed(3) + 's – ' + (at.e / 1000).toFixed(3) + 's]'
        + (st.state === 'partial' ? ' (ABBC 部分保留 ' + Math.round(st.ratio * 100) + '%)' : '')
        + '\n單擊:切換此積木保留/切除 | 雙擊:從此處播放';

      if (st.keptRanges.length) {
        st.keptRanges.forEach(function (kr) {
          var fill = document.createElement('span');
          fill.className = 'atom-fill';
          var leftPct = ((kr[0] - at.s) / (at.e - at.s)) * 100;
          var widthPct = ((kr[1] - kr[0]) / (at.e - at.s)) * 100;
          fill.style.left = leftPct.toFixed(1) + '%';
          fill.style.width = widthPct.toFixed(1) + '%';
          chip.appendChild(fill);
        });
      }

      var idSpan = document.createElement('span');
      idSpan.className = 'atom-id';
      idSpan.textContent = at.id + (st.state === 'partial' ? ' (' + Math.round(st.ratio * 100) + '%)' : '');
      chip.appendChild(idSpan);

      var txtSpan = document.createElement('span');
      txtSpan.className = 'atom-txt';
      txtSpan.textContent = at.text || (at.kind === 'silence' ? '⏸ ' + ((at.e - at.s) / 1000).toFixed(2) + 's' : at.id);
      chip.appendChild(txtSpan);

      chip.addEventListener('click', function () {
        var mode = st.state === 'cut' ? 'keep' : 'cut';
        commitOverrideEdit(
          Core.applyTrimOverride(wState.managedOverrides, [at.s, at.e], mode),
          at.s,
        );
      });
      chip.addEventListener('dblclick', function (ev) {
        ev.stopPropagation();
        wState.cursorMs = at.s;
        wState.player.playIntervals(wState.finalIntervals, { startFromSrcMs: at.s });
      });

      bar.appendChild(chip);
    });
  }

  // ── 寫回受控 ✂ / ✚ 覆蓋至 state.doc 並觸發存檔與試聽 ─────────────────────────
  function commitOverrideEdit(nextManagedOverrides, seamPreviewMs) {
    var a = app();
    if (!a || !a.state || !a.state.doc) return;
    var step = Core.applyReviewOverridesWithHistory(
      a.state.doc,
      a.state.history,
      nextManagedOverrides,
      'wave',
    );
    a.state.doc = step.doc;
    a.state.history = step.history;
    a.setDirty(true);
    a.render();

    if (wState.autoPreviewSeam && seamPreviewMs != null && wState.player.buffer) {
      wState.player.playIntervals(wState.finalIntervals, {
        startFromSrcMs: Math.max(0, seamPreviewMs - 1200),
        maxToSrcMs: seamPreviewMs + 1200,
      });
    }
  }

  // ── 雙層 HiDPI Canvas 渲染 (SoundCraft Peaks 金字塔) ────────────────────────
  function syncCanvasSize(canvas) {
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    var w = Math.max(1, Math.round(rect.width * dpr));
    var h = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    return { w: w, h: h, cssW: rect.width, cssH: rect.height, dpr: dpr };
  }

  function msToX(ms, cssW) {
    var span = Math.max(1, wState.viewEndMs - wState.viewStartMs);
    return ((ms - wState.viewStartMs) / span) * cssW;
  }

  function xToMs(x, cssW) {
    var span = Math.max(1, wState.viewEndMs - wState.viewStartMs);
    return wState.viewStartMs + (x / Math.max(1, cssW)) * span;
  }

  function isMsKept(ms, intervals) {
    var lo = 0;
    var hi = intervals.length - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      var r = intervals[mid];
      if (ms < r[0]) hi = mid - 1;
      else if (ms >= r[1]) lo = mid + 1;
      else return true;
    }
    return false;
  }

  function drawWaveLayer() {
    var canvas = document.getElementById('waveCanvas');
    if (!canvas) return;
    var dim = syncCanvasSize(canvas);
    var ctx = canvas.getContext('2d');
    ctx.save();
    ctx.scale(dim.dpr, dim.dpr);
    ctx.clearRect(0, 0, dim.cssW, dim.cssH);

    var topBarH = 18;
    var subTrackH = 34;
    var waveTop = topBarH;
    var waveH = Math.max(40, dim.cssH - topBarH - subTrackH);
    var midY = waveTop + waveH / 2;

    // 1) 畫切除區底色與保留區底色
    ctx.fillStyle = 'rgba(255, 92, 92, 0.08)';
    ctx.fillRect(0, waveTop, dim.cssW, waveH);

    wState.finalIntervals.forEach(function (r) {
      if (r[1] <= wState.viewStartMs || r[0] >= wState.viewEndMs) return;
      var x0 = Math.max(0, msToX(r[0], dim.cssW));
      var x1 = Math.min(dim.cssW, msToX(r[1], dim.cssW));
      ctx.fillStyle = 'rgba(61, 214, 140, 0.11)';
      ctx.fillRect(x0, waveTop, Math.max(1, x1 - x0), waveH);
    });

    // 2) 頂部優先級覆蓋色條 (✂ 強制切 / ✚ 強制留)
    (wState.overrides.cut || []).forEach(function (r) {
      if (r[1] <= wState.viewStartMs || r[0] >= wState.viewEndMs) return;
      var x0 = Math.max(0, msToX(r[0], dim.cssW));
      var x1 = Math.min(dim.cssW, msToX(r[1], dim.cssW));
      ctx.fillStyle = '#ff5c5c';
      ctx.fillRect(x0, 0, Math.max(2, x1 - x0), 5);
    });
    (wState.overrides.keep || []).forEach(function (r) {
      if (r[1] <= wState.viewStartMs || r[0] >= wState.viewEndMs) return;
      var x0 = Math.max(0, msToX(r[0], dim.cssW));
      var x1 = Math.min(dim.cssW, msToX(r[1], dim.cssW));
      ctx.fillStyle = '#ffd166';
      ctx.fillRect(x0, 0, Math.max(2, x1 - x0), 5);
    });

    // 中線
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.beginPath();
    ctx.moveTo(0, midY);
    ctx.lineTo(dim.cssW, midY);
    ctx.stroke();

    // 3) 畫 SoundCraft Peaks 波形
    if (wState.peaks && wState.samples) {
      var sr = wState.sampleRate;
      var spanMs = Math.max(1, wState.viewEndMs - wState.viewStartMs);
      var cols = Math.floor(dim.cssW);
      for (var px = 0; px < cols; px++) {
        var m0 = wState.viewStartMs + (px / cols) * spanMs;
        var m1 = wState.viewStartMs + ((px + 1) / cols) * spanMs;
        var s0 = (m0 / 1000) * sr;
        var s1 = (m1 / 1000) * sr;
        var mm = Core.queryPeaksMinMax(wState.peaks, wState.samples, s0, s1);
        var kept = isMsKept((m0 + m1) * 0.5, wState.finalIntervals);
        ctx.strokeStyle = kept ? '#3dd68c' : 'rgba(160, 110, 115, 0.42)';
        var yMin = midY - mm[1] * (waveH * 0.46);
        var yMax = midY - mm[0] * (waveH * 0.46);
        if (Math.abs(yMax - yMin) < 1) yMax = yMin + 1;
        ctx.beginPath();
        ctx.moveTo(px + 0.5, yMin);
        ctx.lineTo(px + 0.5, yMax);
        ctx.stroke();
      }
    } else {
      ctx.fillStyle = 'rgba(255,255,255,0.45)';
      ctx.font = '12px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.fillText('尚未載入 WAV 波形 (已顯示全軌 MECE 詞級時間軸,開資料夾含 audio16k.wav 即自動繪製)', 12, midY + 4);
    }

    // 4) 底部 MECE 詞積木軌道 (B0001-1 / B0001-2 / B0001-3)
    var subTop = waveTop + waveH;
    ctx.fillStyle = '#161b22';
    ctx.fillRect(0, subTop, dim.cssW, subTrackH);

    if (wState.partition) {
      wState.partition.allAtoms.forEach(function (at) {
        if (at.e <= wState.viewStartMs || at.s >= wState.viewEndMs) return;
        var x0 = Math.max(0, msToX(at.s, dim.cssW));
        var x1 = Math.min(dim.cssW, msToX(at.e, dim.cssW));
        var w = x1 - x0;
        if (w < 1.5) return;

        var st = getAtomKeepStatus(at.s, at.e);
        ctx.fillStyle = st.state === 'kept'
          ? 'rgba(61, 214, 140, 0.22)'
          : st.state === 'partial'
            ? 'rgba(255, 159, 67, 0.25)'
            : 'rgba(255, 92, 92, 0.12)';
        ctx.fillRect(x0, subTop + 1, w, subTrackH - 2);

        ctx.strokeStyle = 'rgba(255,255,255,0.18)';
        ctx.strokeRect(x0 + 0.5, subTop + 0.5, Math.max(1, w - 1), subTrackH - 1);

        if (w >= 26) {
          ctx.fillStyle = st.state === 'cut' ? 'rgba(255,255,255,0.4)' : '#e6edf3';
          ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace';
          var label = (at.text || (at.kind === 'silence' ? '⏸' : '')) + ' (' + at.id.split('-').pop() + ')';
          ctx.save();
          ctx.beginPath();
          ctx.rect(x0 + 2, subTop + 2, w - 4, subTrackH - 4);
          ctx.clip();
          ctx.fillText(label, x0 + 4, subTop + 21);
          ctx.restore();
        }
      });
    }

    ctx.restore();
  }

  function drawOverlayLayer() {
    var canvas = document.getElementById('waveOverlay');
    if (!canvas) return;
    var dim = syncCanvasSize(canvas);
    var ctx = canvas.getContext('2d');
    ctx.save();
    ctx.scale(dim.dpr, dim.dpr);
    ctx.clearRect(0, 0, dim.cssW, dim.cssH);

    var topBarH = 18;
    var subTrackH = 34;
    var waveTop = topBarH;
    var waveH = Math.max(40, dim.cssH - topBarH - subTrackH);

    // 1) 時間尺刻度
    var spanMs = Math.max(1, wState.viewEndMs - wState.viewStartMs);
    var stepMs = spanMs <= 3000 ? 250 : spanMs <= 8000 ? 500 : spanMs <= 20000 ? 1000 : 5000;
    var firstTick = Math.ceil(wState.viewStartMs / stepMs) * stepMs;
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '10px ui-monospace, monospace';
    for (var t = firstTick; t <= wState.viewEndMs; t += stepMs) {
      var tx = msToX(t, dim.cssW);
      ctx.fillRect(tx, 13, 1, 5);
      ctx.fillText((t / 1000).toFixed(stepMs < 1000 ? 2 : 1) + 's', tx + 3, 12);
    }

    // 2) TrimStart / TrimEnd 邊界手把 (SoundCraft Smart Tool)
    wState.finalIntervals.forEach(function (r) {
      if (r[1] < wState.viewStartMs || r[0] > wState.viewEndMs) return;
      var xs = msToX(r[0], dim.cssW);
      var xe = msToX(r[1], dim.cssW);
      if (xe - xs < 6) return;

      // 左手把 [
      if (xs >= 0 && xs <= dim.cssW) {
        ctx.strokeStyle = '#3dd68c';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(xs + 5, waveTop + 3);
        ctx.lineTo(xs, waveTop + 3);
        ctx.lineTo(xs, waveTop + waveH - 3);
        ctx.lineTo(xs + 5, waveTop + waveH - 3);
        ctx.stroke();
      }
      // 右手把 ]
      if (xe >= 0 && xe <= dim.cssW) {
        ctx.strokeStyle = '#3dd68c';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(xe - 5, waveTop + 3);
        ctx.lineTo(xe, waveTop + 3);
        ctx.lineTo(xe, waveTop + waveH - 3);
        ctx.lineTo(xe - 5, waveTop + waveH - 3);
        ctx.stroke();
      }
    });

    // 3) 拖拉手把中的預覽線
    if (wState.gesture && (wState.gesture.kind === 'trimStart' || wState.gesture.kind === 'trimEnd')) {
      var gx = msToX(wState.gesture.currentMs, dim.cssW);
      var ox = msToX(wState.gesture.originMs, dim.cssW);
      ctx.fillStyle = 'rgba(255, 159, 67, 0.24)';
      ctx.fillRect(Math.min(ox, gx), waveTop, Math.abs(gx - ox), waveH);
      ctx.strokeStyle = '#ff9f43';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(gx, 0);
      ctx.lineTo(gx, dim.cssH);
      ctx.stroke();
    }

    // 4) Selector 框選範圍 (ABBC 自由覆蓋框)
    if (wState.selection) {
      var sx0 = msToX(wState.selection.startMs, dim.cssW);
      var sx1 = msToX(wState.selection.endMs, dim.cssW);
      ctx.fillStyle = 'rgba(255, 159, 67, 0.22)';
      ctx.fillRect(sx0, waveTop, Math.max(2, sx1 - sx0), waveH);
      ctx.strokeStyle = '#ff9f43';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(sx0, waveTop, Math.max(2, sx1 - sx0), waveH);
    }

    // 5) 播放游標 Playhead
    var cx = msToX(wState.cursorMs, dim.cssW);
    if (cx >= 0 && cx <= dim.cssW) {
      ctx.strokeStyle = '#ff4757';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, dim.cssH);
      ctx.stroke();
      ctx.fillStyle = '#ff4757';
      ctx.beginPath();
      ctx.moveTo(cx - 5, 0);
      ctx.lineTo(cx + 5, 0);
      ctx.lineTo(cx, 7);
      ctx.closePath();
      ctx.fill();
    }

    ctx.restore();

    var readout = document.getElementById('wTimeReadout');
    if (readout) {
      readout.textContent = (wState.cursorMs / 1000).toFixed(3) + 's / '
        + (wState.durationMs / 1000).toFixed(1) + 's';
    }
  }

  // ── Smart Tool Hit-Testing (移植自 SoundCraft input.rs) ──────────────────────
  function hitTestStage(ev, canvas) {
    var rect = canvas.getBoundingClientRect();
    var x = ev.clientX - rect.left;
    var y = ev.clientY - rect.top;
    var cssW = rect.width;
    var cssH = rect.height;
    var atMs = xToMs(x, cssW);

    var topBarH = 18;
    var subTrackH = 34;
    var waveTop = topBarH;
    var waveH = Math.max(40, cssH - topBarH - subTrackH);

    // 1) 時間尺 Scrub
    if (y < waveTop) {
      return { kind: 'scrub', atMs: atMs };
    }

    // 2) 底部詞積木 Grabber
    if (y >= waveTop + waveH && wState.partition) {
      var hitAtom = wState.partition.allAtoms.find(function (at) {
        return atMs >= at.s && atMs < at.e;
      });
      return { kind: 'atomGrabber', atMs: atMs, atom: hitAtom || null };
    }

    // 3) 檢查是否命中任何保留區段的 TrimStart / TrimEnd 手把 (±8px,取最近者)
    var bestEdge = null;
    var bestDistPx = HANDLE_HIT_PX + 1;
    wState.finalIntervals.forEach(function (r) {
      var xs = msToX(r[0], cssW);
      var xe = msToX(r[1], cssW);
      if (xe - xs < 10) return;
      var ds = Math.abs(x - xs);
      if (ds <= HANDLE_HIT_PX && ds < bestDistPx) {
        bestDistPx = ds;
        bestEdge = {
          kind: 'trimStart',
          originMs: r[0],
          regionStartMs: r[0],
          regionEndMs: r[1],
        };
      }
      var de = Math.abs(x - xe);
      if (de <= HANDLE_HIT_PX && de < bestDistPx) {
        bestDistPx = de;
        bestEdge = {
          kind: 'trimEnd',
          originMs: r[1],
          regionStartMs: r[0],
          regionEndMs: r[1],
        };
      }
    });
    if (bestEdge) return bestEdge;

    // 4) 波形上半部 = Selector (框選範圍做 ABBC 強制覆蓋),下半部 = Grabber
    if (y < waveTop + waveH * 0.55) {
      return { kind: 'selector', atMs: atMs };
    }
    var waveAtom = wState.partition
      ? wState.partition.allAtoms.find(function (at) { return atMs >= at.s && atMs < at.e; })
      : null;
    return { kind: 'atomGrabber', atMs: atMs, atom: waveAtom || null };
  }

  function isModifierHeld(ev) {
    return !!(ev.altKey || (IS_MAC ? ev.metaKey : ev.ctrlKey));
  }

  function updateSelectionBar() {
    var bar = document.getElementById('waveSelBar');
    var lab = document.getElementById('wSelLabel');
    if (!bar || !lab) return;
    if (!wState.selection || wState.selection.endMs <= wState.selection.startMs) {
      bar.classList.remove('show');
      return;
    }
    var s = wState.selection.startMs;
    var e = wState.selection.endMs;
    bar.classList.add('show');
    lab.textContent = '選取範圍 [' + (s / 1000).toFixed(3) + 's – ' + (e / 1000).toFixed(3) + 's] ('
      + ((e - s) / 1000).toFixed(3) + 's):';
  }

  // ── 綁定波形互動與按鈕事件 ─────────────────────────────────────────────────
  function bindEvents() {
    var stage = document.getElementById('waveStage');
    var overlay = document.getElementById('waveOverlay');
    if (!stage || !overlay) return;

    overlay.addEventListener('pointermove', function (ev) {
      var rect = overlay.getBoundingClientRect();
      var pxPerMs = rect.width / Math.max(1, wState.viewEndMs - wState.viewStartMs);
      var rawMs = xToMs(ev.clientX - rect.left, rect.width);

      if (!wState.gesture) {
        var hit = hitTestStage(ev, overlay);
        if (hit.kind === 'trimStart' || hit.kind === 'trimEnd') {
          overlay.style.cursor = 'ew-resize';
        } else if (hit.kind === 'selector') {
          overlay.style.cursor = 'crosshair';
        } else if (hit.kind === 'scrub') {
          overlay.style.cursor = 'col-resize';
        } else {
          overlay.style.cursor = 'pointer';
        }
        return;
      }

      var g = wState.gesture;
      var mod = isModifierHeld(ev) || wState.slipModeForced;
      if (g.kind === 'trimStart' || g.kind === 'trimEnd') {
        g.currentMs = Core.trimDragSnap(g, rawMs, {
          snapEnabled: true,
          modifierHeld: mod,
          minKeepMs: MIN_KEEP_MS,
          durMs: wState.durationMs,
          pxPerMs: pxPerMs,
          originSnapPx: 6,
        });
        drawOverlayLayer();
      } else if (g.kind === 'selector') {
        var snappedMs = mod
          ? Math.round(rawMs)
          : Core.findNearestSnapMs(wState.snapPointsMs, rawMs);
        snappedMs = Math.max(0, Math.min(wState.durationMs, snappedMs));
        wState.selection = {
          startMs: Math.min(g.anchorMs, snappedMs),
          endMs: Math.max(g.anchorMs, snappedMs),
        };
        updateSelectionBar();
        drawOverlayLayer();
      } else if (g.kind === 'scrub') {
        wState.cursorMs = Math.max(0, Math.min(wState.durationMs, Math.round(rawMs)));
        drawOverlayLayer();
      }
    });

    overlay.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0) return;
      overlay.setPointerCapture(ev.pointerId);
      var hit = hitTestStage(ev, overlay);
      var mod = isModifierHeld(ev) || wState.slipModeForced;

      if (hit.kind === 'trimStart' || hit.kind === 'trimEnd') {
        var snaps = wState.snapPointsMs.slice();
        if (snaps.indexOf(hit.originMs) === -1) {
          snaps.push(hit.originMs);
          snaps.sort(function (a, b) { return a - b; });
        }
        wState.gesture = {
          kind: hit.kind,
          originMs: hit.originMs,
          currentMs: hit.originMs,
          regionStartMs: hit.regionStartMs,
          regionEndMs: hit.regionEndMs,
          snapPointsMs: snaps,
        };
      } else if (hit.kind === 'selector') {
        var anchor = mod
          ? Math.round(hit.atMs)
          : Core.findNearestSnapMs(wState.snapPointsMs, hit.atMs);
        wState.gesture = { kind: 'selector', anchorMs: anchor };
        wState.selection = null;
        updateSelectionBar();
      } else if (hit.kind === 'scrub') {
        wState.cursorMs = Math.max(0, Math.min(wState.durationMs, Math.round(hit.atMs)));
        wState.gesture = { kind: 'scrub' };
      } else if (hit.kind === 'atomGrabber') {
        wState.cursorMs = Math.max(0, Math.min(wState.durationMs, Math.round(hit.atMs)));
        wState.gesture = { kind: 'atomGrabber', atom: hit.atom, downX: ev.clientX };
      }
      drawOverlayLayer();
    });

    function finishPointer(ev) {
      if (!wState.gesture) return;
      var g = wState.gesture;
      wState.gesture = null;

      if (g.kind === 'trimStart' && g.currentMs !== g.originMs) {
        var loS = Math.min(g.originMs, g.currentMs);
        var hiS = Math.max(g.originMs, g.currentMs);
        // 往右拉 = 縮小保留區 ('cut')；往左拉 = 擴大保留區 ('keep')
        var modeS = g.currentMs > g.originMs ? 'cut' : 'keep';
        wState.cursorMs = g.currentMs;
        commitOverrideEdit(
          Core.applyTrimOverride(wState.managedOverrides, [loS, hiS], modeS),
          g.currentMs,
        );
      } else if (g.kind === 'trimEnd' && g.currentMs !== g.originMs) {
        var loE = Math.min(g.originMs, g.currentMs);
        var hiE = Math.max(g.originMs, g.currentMs);
        // 往左拉 = 縮小保留區 ('cut')；往右拉 = 擴大保留區 ('keep')
        var modeE = g.currentMs < g.originMs ? 'cut' : 'keep';
        wState.cursorMs = g.currentMs;
        commitOverrideEdit(
          Core.applyTrimOverride(wState.managedOverrides, [loE, hiE], modeE),
          g.currentMs,
        );
      } else if (g.kind === 'atomGrabber' && g.atom && Math.abs(ev.clientX - g.downX) < 5) {
        var st = getAtomKeepStatus(g.atom.s, g.atom.e);
        var nextMode = st.state === 'cut' ? 'keep' : 'cut';
        commitOverrideEdit(
          Core.applyTrimOverride(wState.managedOverrides, [g.atom.s, g.atom.e], nextMode),
          g.atom.s,
        );
      }
      drawOverlayLayer();
    }

    overlay.addEventListener('pointerup', finishPointer);
    overlay.addEventListener('pointercancel', function () {
      wState.gesture = null;
      drawOverlayLayer();
    });

    // 滾輪縮放與水平捲動
    stage.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      var span = Math.max(500, wState.viewEndMs - wState.viewStartMs);
      if (ev.ctrlKey || ev.metaKey || Math.abs(ev.deltaY) > Math.abs(ev.deltaX)) {
        var factor = ev.deltaY > 0 ? 1.25 : 0.8;
        var nextSpan = Math.max(600, Math.min(wState.durationMs || 60000, Math.round(span * factor)));
        var center = (wState.viewStartMs + wState.viewEndMs) * 0.5;
        wState.viewStartMs = Math.max(0, Math.round(center - nextSpan * 0.5));
        wState.viewEndMs = Math.min(wState.durationMs || 60000, wState.viewStartMs + nextSpan);
      } else {
        var shift = Math.round((ev.deltaX / 300) * span);
        wState.viewStartMs = Math.max(0, Math.min((wState.durationMs || 60000) - span, wState.viewStartMs + shift));
        wState.viewEndMs = wState.viewStartMs + span;
      }
      renderAtomsStrip();
      drawWaveLayer();
      drawOverlayLayer();
    }, { passive: false });

    // 工具列按鈕
    var playCutBtn = document.getElementById('wPlayCutBtn');
    if (playCutBtn) {
      playCutBtn.addEventListener('click', function () {
        if (wState.player.playing) {
          wState.player.stop();
          return;
        }
        if (!wState.player.buffer) {
          var a = app();
          if (a) a.setStatus('請先載入含 audio16k.wav / source.wav 的資料夾,或點「📂 換波形音檔」', 'error');
          return;
        }
        rebuildIntervals();
        wState.player.playIntervals(wState.finalIntervals, { startFromSrcMs: wState.cursorMs });
      });
    }

    var playSeamBtn = document.getElementById('wPlaySeamBtn');
    if (playSeamBtn) {
      playSeamBtn.addEventListener('click', function () {
        if (!wState.player.buffer) return;
        rebuildIntervals();
        wState.player.playIntervals(wState.finalIntervals, {
          startFromSrcMs: Math.max(0, wState.cursorMs - 1500),
          maxToSrcMs: wState.cursorMs + 1500,
        });
      });
    }

    var slipBtn = document.getElementById('wSlipModeBtn');
    if (slipBtn) {
      slipBtn.addEventListener('click', function () {
        wState.slipModeForced = !wState.slipModeForced;
        slipBtn.classList.toggle('active-mode', wState.slipModeForced);
        slipBtn.textContent = wState.slipModeForced
          ? '🔓 強制覆蓋模式 ON (1ms 自由切 ABBC)'
          : '🧲 詞邊界吸附 (按住 Alt 自由覆蓋)';
      });
    }

    function triggerAppUndo() {
      var a = app();
      if (!a || !a.state || !a.state.doc) return;
      var res = Core.undo(a.state.doc, a.state.history);
      if (!res.undone) return;
      a.state.doc = res.doc;
      a.state.history = res.history;
      a.setDirty(true);
      a.render();
    }

    var wUndoBtn = document.getElementById('wUndoBtn');
    if (wUndoBtn) {
      wUndoBtn.addEventListener('click', triggerAppUndo);
    }

    var wHelpBtn = document.getElementById('wHelpBtn');
    if (wHelpBtn) {
      wHelpBtn.addEventListener('click', function () {
        var box = document.getElementById('waveHelpBox');
        if (box) box.classList.toggle('show');
      });
    }

    window.addEventListener('keydown', function (ev) {
      var tag = ev.target && ev.target.tagName ? ev.target.tagName.toUpperCase() : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (ev.target && ev.target.isContentEditable)) {
        return;
      }
      if (ev.code === 'Space') {
        var dock = document.getElementById('waveDock');
        if (!dock || dock.hidden || !wState.player.buffer) return;
        ev.preventDefault();
        if (ev.shiftKey) {
          rebuildIntervals();
          wState.player.playIntervals(wState.finalIntervals, {
            startFromSrcMs: Math.max(0, wState.cursorMs - 1500),
            maxToSrcMs: wState.cursorMs + 1500,
          });
        } else if (wState.player.playing) {
          wState.player.stop();
        } else {
          rebuildIntervals();
          wState.player.playIntervals(wState.finalIntervals, { startFromSrcMs: wState.cursorMs });
        }
      } else if (ev.key === 'Escape') {
        if (wState.gesture || wState.selection) {
          wState.gesture = null;
          wState.selection = null;
          updateSelectionBar();
          drawOverlayLayer();
        }
      } else if ((ev.metaKey || ev.ctrlKey) && !ev.shiftKey && ev.key.toLowerCase() === 'z') {
        ev.preventDefault();
        triggerAppUndo();
      } else if (wState.selection && wState.selection.endMs > wState.selection.startMs) {
        // 波形框選時的加減切點快捷鍵
        if (ev.key === 'Delete' || ev.key === 'Backspace' || ev.key === 'x' || ev.key === 'X') {
          // 加切點: 強制切除選取範圍
          ev.preventDefault();
          var rCut = [wState.selection.startMs, wState.selection.endMs];
          wState.selection = null;
          updateSelectionBar();
          commitOverrideEdit(Core.applyTrimOverride(wState.managedOverrides, rCut, 'cut'), rCut[0]);
        } else if (ev.key === 'k' || ev.key === 'K' || ev.key === 'c' || ev.key === 'C') {
          // 加切點: 強制保留選取範圍
          ev.preventDefault();
          var rKeep = [wState.selection.startMs, wState.selection.endMs];
          wState.selection = null;
          updateSelectionBar();
          commitOverrideEdit(Core.applyTrimOverride(wState.managedOverrides, rKeep, 'keep'), rKeep[0]);
        } else if (ev.key === 'a' || ev.key === 'A') {
          // 加切點: 強制覆蓋 ABBC
          ev.preventDefault();
          var selS = wState.selection.startMs;
          var selE = wState.selection.endMs;
          var touched = (wState.partition ? wState.partition.allAtoms : []).filter(function (at) {
            return at.e > selS && at.s < selE;
          });
          var scopeS = touched.length ? Math.min(selS, touched[0].s) : selS;
          var scopeE = touched.length ? Math.max(selE, touched[touched.length - 1].e) : selE;
          var nextOv = Core.forceWindowOverride(wState.managedOverrides, [scopeS, scopeE], [selS, selE]);
          wState.selection = null;
          updateSelectionBar();
          commitOverrideEdit(nextOv, selS);
        } else if (ev.key === 'd' || ev.key === 'D') {
          // 減切點: 清除此區手動覆蓋
          ev.preventDefault();
          var rClr = [wState.selection.startMs, wState.selection.endMs];
          wState.selection = null;
          updateSelectionBar();
          commitOverrideEdit(Core.applyTrimOverride(wState.managedOverrides, rClr, 'clear'), rClr[0]);
        }
      } else if (!wState.selection && (ev.key === 's' || ev.key === 'S')) {
        // 無框選時: 在游標處加切點 (若游標落在某個詞積木上,切換該詞切除/保留)
        if (wState.partition && wState.cursorMs !== null) {
          var curAtom = wState.partition.allAtoms.find(function (at) {
            return wState.cursorMs >= at.s && wState.cursorMs < at.e;
          });
          if (curAtom) {
            ev.preventDefault();
            var st = getAtomKeepStatus(curAtom.s, curAtom.e);
            var nextMode = st.state === 'cut' ? 'keep' : 'cut';
            commitOverrideEdit(
              Core.applyTrimOverride(wState.managedOverrides, [curAtom.s, curAtom.e], nextMode),
              curAtom.s,
            );
          }
        }
      }
    });

    var zoomInBtn = document.getElementById('wZoomInBtn');
    if (zoomInBtn) {
      zoomInBtn.addEventListener('click', function () {
        var span = Math.max(600, Math.round((wState.viewEndMs - wState.viewStartMs) * 0.65));
        var c = wState.cursorMs || (wState.viewStartMs + wState.viewEndMs) * 0.5;
        wState.viewStartMs = Math.max(0, Math.round(c - span * 0.5));
        wState.viewEndMs = Math.min(wState.durationMs || 60000, wState.viewStartMs + span);
        renderAtomsStrip();
        drawWaveLayer();
        drawOverlayLayer();
      });
    }

    var zoomOutBtn = document.getElementById('wZoomOutBtn');
    if (zoomOutBtn) {
      zoomOutBtn.addEventListener('click', function () {
        var span = Math.min(wState.durationMs || 60000, Math.round((wState.viewEndMs - wState.viewStartMs) * 1.5));
        var c = wState.cursorMs || (wState.viewStartMs + wState.viewEndMs) * 0.5;
        wState.viewStartMs = Math.max(0, Math.round(c - span * 0.5));
        wState.viewEndMs = Math.min(wState.durationMs || 60000, wState.viewStartMs + span);
        renderAtomsStrip();
        drawWaveLayer();
        drawOverlayLayer();
      });
    }

    var zoomBlockBtn = document.getElementById('wZoomBlockBtn');
    if (zoomBlockBtn) {
      zoomBlockBtn.addEventListener('click', function () {
        if (!wState.partition) return;
        var b = wState.focusedBlockId
          ? wState.partition.blocks.find(function (x) { return x.id === wState.focusedBlockId; })
          : wState.partition.blocks.find(function (x) { return wState.cursorMs >= x.s && wState.cursorMs < x.e; });
        if (b) focusBlockInWaveform(b.id);
      });
    }

    // Selector 框選動作列 (ABBC 強制覆蓋!)
    var forceAbbcBtn = document.getElementById('wForceAbbcBtn');
    if (forceAbbcBtn) {
      forceAbbcBtn.addEventListener('click', function () {
        if (!wState.selection || !wState.partition) return;
        var selS = wState.selection.startMs;
        var selE = wState.selection.endMs;
        // 找出與選取範圍重疊的所有詞積木 (如 AA / BB / CC),將外圍切掉、中間強制保留 = ABBC!
        var touched = wState.partition.allAtoms.filter(function (at) {
          return at.e > selS && at.s < selE;
        });
        var scopeS = touched.length ? Math.min(selS, touched[0].s) : selS;
        var scopeE = touched.length ? Math.max(selE, touched[touched.length - 1].e) : selE;
        var nextOv = Core.forceWindowOverride(wState.managedOverrides, [scopeS, scopeE], [selS, selE]);
        wState.selection = null;
        updateSelectionBar();
        commitOverrideEdit(nextOv, selS);
      });
    }

    var forceCutBtn = document.getElementById('wForceCutBtn');
    if (forceCutBtn) {
      forceCutBtn.addEventListener('click', function () {
        if (!wState.selection) return;
        var r = [wState.selection.startMs, wState.selection.endMs];
        wState.selection = null;
        updateSelectionBar();
        commitOverrideEdit(Core.applyTrimOverride(wState.managedOverrides, r, 'cut'), r[0]);
      });
    }

    var forceKeepBtn = document.getElementById('wForceKeepBtn');
    if (forceKeepBtn) {
      forceKeepBtn.addEventListener('click', function () {
        if (!wState.selection) return;
        var r = [wState.selection.startMs, wState.selection.endMs];
        wState.selection = null;
        updateSelectionBar();
        commitOverrideEdit(Core.applyTrimOverride(wState.managedOverrides, r, 'keep'), r[0]);
      });
    }

    var clearOvBtn = document.getElementById('wClearOvBtn');
    if (clearOvBtn) {
      clearOvBtn.addEventListener('click', function () {
        if (!wState.selection) return;
        var r = [wState.selection.startMs, wState.selection.endMs];
        wState.selection = null;
        updateSelectionBar();
        commitOverrideEdit(Core.applyTrimOverride(wState.managedOverrides, r, 'clear'), r[0]);
      });
    }

    var playSelBtn = document.getElementById('wPlaySelBtn');
    if (playSelBtn) {
      playSelBtn.addEventListener('click', function () {
        if (!wState.selection || !wState.player.buffer) return;
        wState.player.playIntervals([[wState.selection.startMs, wState.selection.endMs]], {
          startFromSrcMs: wState.selection.startMs,
          maxToSrcMs: wState.selection.endMs,
        });
      });
    }

    var audioInput = document.getElementById('wAudioInput');
    if (audioInput) {
      audioInput.addEventListener('change', function () {
        var f = this.files && this.files[0];
        if (f) loadWaveAudioBlob(f, f.name);
      });
    }

    // 播放器回呼
    wState.player.onStateChange = function (isPlaying) {
      var btn = document.getElementById('wPlayCutBtn');
      if (btn) {
        btn.textContent = isPlaying ? '⏸ 暫停剪輯預覽' : '▶ 一鍵依剪輯範圍播放';
      }
    };

    wState.player.onTick = function (srcMs) {
      wState.cursorMs = srcMs;
      var span = wState.viewEndMs - wState.viewStartMs;
      if (srcMs > wState.viewEndMs - span * 0.1 || srcMs < wState.viewStartMs) {
        wState.viewStartMs = Math.max(0, Math.round(srcMs - span * 0.2));
        wState.viewEndMs = Math.min(wState.durationMs || 60000, wState.viewStartMs + span);
        renderAtomsStrip();
        drawWaveLayer();
      }
      drawOverlayLayer();

      // 高亮目前播放中的語音卡片
      if (wState.partition) {
        var curBlock = wState.partition.blocks.find(function (b) {
          return srcMs >= b.s && srcMs < b.e;
        });
        if (curBlock && curBlock.id !== wState.focusedBlockId) {
          wState.focusedBlockId = curBlock.id;
          renderAtomsStrip();
          var a = app();
          if (a && a.state && a.state.doc) {
            var li = Core.findLineById(a.state.doc, curBlock.id);
            if (li !== null) {
              var old = document.querySelector('.block-card.playing');
              if (old) old.classList.remove('playing');
              var card = document.querySelector('.block-card[data-line-index="' + li + '"]');
              if (card) {
                card.classList.add('playing');
                var followEl = document.getElementById('follow');
                if (!followEl || followEl.checked) {
                  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
              }
            }
          }
        }
      }
    };

    window.addEventListener('resize', function () {
      drawWaveLayer();
      drawOverlayLayer();
    });
  }

  // ── 聚焦指定 Block 並展開其詞級子積木 (B0001-1/-2/-3) ──────────────────────
  function focusBlockInWaveform(blockId) {
    if (!wState.partition) rebuildIntervals();
    if (!wState.partition) return;
    var b = wState.partition.blocks.find(function (x) {
      return x.id === blockId || (x.legacyIds && x.legacyIds.indexOf(blockId) !== -1);
    });
    if (!b) return;
    wState.focusedBlockId = b.id;
    wState.cursorMs = b.s;
    var pad = Math.max(600, Math.round((b.e - b.s) * 0.35));
    wState.viewStartMs = Math.max(0, b.s - pad);
    wState.viewEndMs = Math.min(wState.durationMs || (b.e + pad), b.e + pad);
    renderAtomsStrip();
    drawWaveLayer();
    drawOverlayLayer();

    // 更新卡片視覺焦點高亮
    var a = app();
    if (a && a.state && a.state.doc) {
      var li = Core.findLineById(a.state.doc, b.id);
      document.querySelectorAll('#blockList .block-card.focused-block').forEach(function (c) {
        c.classList.remove('focused-block');
      });
      if (li !== null) {
        var card = document.querySelector('#blockList .block-card[data-line-index="' + li + '"]');
        if (card) {
          card.classList.add('focused-block');
        }
      }
    }
  }

  // 在每張 Block 卡片加入整卡點擊跳轉波形與「🌊 詞級細剪」按鈕
  function decorateBlockCards() {
    var a = app();
    if (!a || !a.state || !a.state.doc) return;
    var cards = document.querySelectorAll('#blockList .block-card');
    cards.forEach(function (card) {
      var li = Number(card.dataset.lineIndex);
      var line = a.state.doc.lines[li];
      if (!line || !line.id) return;

      // 點擊卡片任何空白/文字處立即跳至該 Block (除非點 checkbox / button 或正在選取文字)
      if (!card.dataset.waveClickBound) {
        card.dataset.waveClickBound = '1';
        card.style.cursor = 'pointer';
        card.addEventListener('click', function (ev) {
          if (ev.target.closest('input, button, label, select, a')) return;
          var sel = window.getSelection();
          if (sel && sel.toString().trim().length > 0) return;
          focusBlockInWaveform(line.id);
        });
      }

      var meta = card.querySelector('.block-meta');
      if (!meta || meta.querySelector('.wave-jump-btn')) return;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'wave-jump-btn';
      btn.textContent = '🌊 詞級細剪';
      btn.title = '在上方波形編輯器展開 ' + line.id + ' 的詞級子積木 (B0001-1/-2/-3)';
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        focusBlockInWaveform(line.id);
      });
      meta.appendChild(btn);
    });
  }

  async function loadWaveAudioBlob(blob, name) {
    var a = app();
    try {
      if (a) a.setStatus('正在解碼波形與建立 SoundCraft Peaks 金字塔: ' + name + ' …');
      var buf = await wState.player.decodeBlob(blob);
      wState.samples = buf.getChannelData(0);
      wState.sampleRate = buf.sampleRate;
      wState.durationMs = Math.round((buf.length / buf.sampleRate) * 1000);
      wState.peaks = Core.buildPeaks(wState.samples);
      wState.audioFileName = name || 'audio';
      rebuildIntervals();
      if (a) a.setStatus('已載入波形與一鍵剪輯預覽音源: ' + name + ' (' + (wState.durationMs / 1000).toFixed(1) + 's)', 'ok');
    } catch (e) {
      if (a) a.setStatus('波形音訊解碼失敗: ' + (e && e.message ? e.message : e), 'error');
    }
  }

  async function loadSessionAssets(assets) {
    assets = assets || {};
    if (assets.cutplanData) wState.cutplanData = assets.cutplanData;
    if (assets.wordsData) wState.wordsData = assets.wordsData;
    if (assets.cutMapData) wState.cutMapData = assets.cutMapData;
    rebuildIntervals();
    decorateBlockCards();
    if (assets.waveAudioFile) {
      await loadWaveAudioBlob(assets.waveAudioFile, assets.waveAudioFile.name);
    }
  }

  function onAppRendered() {
    rebuildIntervals();
    decorateBlockCards();
  }

  injectUI();
  bindEvents();

  window.WaveEditor = {
    state: wState,
    rebuildIntervals: rebuildIntervals,
    focusBlockInWaveform: focusBlockInWaveform,
    loadSessionAssets: loadSessionAssets,
    loadWaveAudioBlob: loadWaveAudioBlob,
    onAppRendered: onAppRendered,
  };
})();
