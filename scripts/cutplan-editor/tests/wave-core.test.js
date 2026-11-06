'use strict';
// scripts/cutplan-editor/tests/wave-core.test.js
//
// Lifov #1201 (#1218 M1):
// 1. 全軌與詞級 100% MECE 切分不變式 (buildTrackPartition / atomizeBlock / assertMECE)
// 2. 三層覆蓋代數 Final = (Base \ Cut) ∪ Keep 與 AA/BB/CC 硬切成 ABBC 的高優先級覆蓋
// 3. ## ✂ / ## ✚ <start>-<end> wave|audacity 無損解析、序列化與 undo
// 4. findIllegalEdit 護欄放行受控 ✂/✚ 行、擋下不合法時間或竄改非管理行

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  parseCutplan,
  serializeCutplan,
  createHistory,
  undo,
  findIllegalEdit,
  timelineStatus,
  parseTimeline,
  normalizeIntervals,
  unionIntervals,
  subtractIntervals,
  intersectIntervals,
  computeFinalIntervals,
  applyTrimOverride,
  forceWindowOverride,
  extractReviewOverrides,
  applyReviewOverridesToDoc,
  applyReviewOverridesWithHistory,
  assertMECE,
  atomizeBlock,
  buildTrackPartition,
  computeBaseIntervalsFromDoc,
  buildPeaks,
  queryPeaksMinMax,
  queryPeaksRms,
  findValleySeamMs,
  trimDragSnap,
  buildCutPlaylist,
  mapPlaybackTimeToSourceMs,
} = require('../cutplan-core.js');

// ── 1. 全軌與詞級 100% MECE 切分測試 ───────────────────────────────────────

test('MECE: buildTrackPartition 消除 G/P 重疊、補齊 <0.9s 停頓與頭尾空洞,達成 100% MECE', () => {
  const cutplanData = {
    blocks: [
      // B0001 與 B0002 有 40ms 微重疊,B0002 與 B0003 之間有 500ms 短停頓(<0.9s),
      // B0003 與 B0004 之間有 3000ms 長空白(同時有重疊的 G0001 與 P0001)
      { id: 'B0001', start: 1.04, end: 3.52, speaker: 'Alice', text: '大家好' },
      { id: 'B0002', start: 3.48, end: 6.00, speaker: 'Bob', text: '歡迎收聽' },
      { id: 'B0003', start: 6.50, end: 10.00, speaker: 'Alice', text: '今天聊原則' },
      { id: 'B0004', start: 13.00, end: 15.20, speaker: 'Bob', text: '沒錯' },
    ],
    gaps: [
      { id: 'G0001', start: 10.00, end: 13.00, duration: 3.0 },
      { id: 'P0001', start: 10.00, end: 12.80, duration: 2.8 }, // 與 G0001 重疊!
    ],
  };
  const words = [
    { word: '大家', start: 1.06, end: 2.10 },
    { word: '好', start: 2.15, end: 3.50 },
    { word: '歡迎', start: 3.50, end: 4.60 },
    { word: '收聽', start: 4.58, end: 5.98 }, // 詞間微重疊
    { word: '今天', start: 6.50, end: 7.20 },
    { word: '聊', start: 7.60, end: 8.10 },   // 詞間有 400ms 停頓 (>= 250ms)
    { word: '原則', start: 8.12, end: 10.00 },
  ];
  const durationMs = 16500;

  const part = buildTrackPartition(cutplanData, words, durationMs);
  assert.equal(part.durationMs, 16500);

  // 1) 頂層 blocks 必須 100% MECE 覆蓋 [0, 16500)
  assert.equal(assertMECE(part.blocks, 0, durationMs), null);

  // 2) 每個 block 內部的子積木 (atoms) 必須 100% MECE 覆蓋 [b.s, b.e)
  for (const b of part.blocks) {
    assert.equal(assertMECE(b.atoms, b.s, b.e), null, `block ${b.id} 內部 atoms 必須 MECE`);
  }

  // 3) 全軌展開的所有 allAtoms 必須 100% MECE 覆蓋 [0, 16500)
  assert.equal(assertMECE(part.allAtoms, 0, durationMs), null);

  // 4) 驗證開頭 ~^、句間 ~B0002、重疊 G0001/P0001 合併、結尾 ~$ 都存在且不重疊
  assert.equal(part.blocks[0].id, '~^');
  assert.deepEqual([part.blocks[0].s, part.blocks[0].e], [0, 1040]);

  const gap34 = part.blocks.find((b) => b.s === 10000 && b.e === 13000);
  assert.ok(gap34, '10s~13s 空白必須是單一 MECE 區段');
  assert.deepEqual(gap34.legacyIds, ['G0001', 'P0001']);

  const tail = part.blocks[part.blocks.length - 1];
  assert.equal(tail.id, '~$');
  assert.deepEqual([tail.s, tail.e], [15200, 16500]);

  // 5) B0003 內部有 >=250ms 的詞間停頓,應拆出 B0003-1, B0003-1~2, B0003-2, B0003-3
  const b3 = part.blocks.find((b) => b.id === 'B0003');
  assert.deepEqual(
    b3.atoms.map((a) => a.id),
    ['B0003-1', 'B0003-1~2', 'B0003-2', 'B0003-3'],
  );
});

// ── 2. AA / BB / CC 硬切成 ABBC 的強制覆蓋優先級測試 ────────────────────────

test('強制覆蓋 (AA/BB/CC -> ABBC): 手動剪輯範圍優先級最高,壓過詞邊界與既有刪除線', () => {
  // 假設某句展開為三個詞級 MECE 子積木:
  //   AA: [10000, 10400)
  //   BB: [10400, 10900)  (假設 BB 原本甚至被刪除線砍掉!)
  //   CC: [10900, 11300)
  const block = { id: 'B0001', start: 10.0, end: 11.3, text: 'AABBCC' };
  const words = [
    { word: 'AA', start: 10.0, end: 10.4 },
    { word: 'BB', start: 10.4, end: 10.9 },
    { word: 'CC', start: 10.9, end: 11.3 },
  ];
  const atoms = atomizeBlock(block, words);
  assert.equal(assertMECE(atoms, 10000, 11300), null);
  assert.deepEqual(
    atoms.map((a) => [a.id, a.text, a.s, a.e]),
    [
      ['B0001-1', 'AA', 10000, 10400],
      ['B0001-2', 'BB', 10400, 10900],
      ['B0001-3', 'CC', 10900, 11300],
    ],
  );

  // Base 假設原本保留 AA 與 CC,或者保留整段 [9920, 11380) (含 seam-pad 80ms)
  const baseWithSeamPad = [[9920, 10400], [10900, 11380]];

  // 使用者硬要把 [9920, 11380) 切成只留 ABBC = [10200, 11100)
  // (切在 AA 正中間 10.200s、強制救回整段 BB、切在 CC 正中間 11.100s)
  const ov0 = { cut: [], keep: [] };
  const ov1 = forceWindowOverride(ov0, [9920, 11380], [10200, 11100]);

  assert.deepEqual(ov1.cut, [[9920, 10200], [11100, 11380]]);
  assert.deepEqual(ov1.keep, [[10200, 11100]]);
  // cut 與 keep 互斥
  assert.deepEqual(intersectIntervals(ov1.cut, ov1.keep), []);

  // 計算 Final = (Base \ Cut) ∪ Keep → 必須 100% 等於 ABBC [10200, 11100)!
  const finalRanges = computeFinalIntervals(baseWithSeamPad, ov1);
  assert.deepEqual(finalRanges, [[10200, 11100]]);
});

test('applyTrimOverride: 先 ✚ 後 ✂ 同一子區間時,自動從 keep 挖除,確保後手 ✂ 生效', () => {
  let ov = { cut: [], keep: [] };
  // 先強制保留 [10000, 12000)
  ov = applyTrimOverride(ov, [10000, 12000], 'keep');
  assert.deepEqual(ov.keep, [[10000, 12000]]);
  // 再從中間強制切掉 [10500, 11000)
  ov = applyTrimOverride(ov, [10500, 11000], 'cut');
  assert.deepEqual(ov.keep, [[10000, 10500], [11000, 12000]]);
  assert.deepEqual(ov.cut, [[10500, 11000]]);
  assert.deepEqual(computeFinalIntervals([], ov), [[10000, 10500], [11000, 12000]]);
});

// ── 3. cutplan.md 雙向序列化、護欄與 Undo ──────────────────────────────────

test('applyReviewOverridesToDoc + findIllegalEdit: 寫入 ## ✂ / ## ✚ wave 通過護欄且可無損 undo', () => {
  const md = [
    '# Cutplan',
    '## ⚙ clip-gap=0.5',
    '## ✂ 12.5-13.0 人手備註的剪除區間',
    '- [x] B0001 [0:10–0:12] [Alice] AABBCC',
    '',
  ].join('\n');

  const doc0 = parseCutplan(md);
  const h0 = createHistory();
  const ov = forceWindowOverride({ cut: [], keep: [] }, [10000, 11300], [10200, 11100]);

  const step = applyReviewOverridesWithHistory(doc0, h0, ov, 'wave');
  const nextMd = serializeCutplan(step.doc);

  // 1) 包含管理行且保留原本的人手備註 ✂ 行
  assert.ok(nextMd.includes('## ✂ 10.000-10.200 wave\n'));
  assert.ok(nextMd.includes('## ✂ 11.100-11.300 wave\n'));
  assert.ok(nextMd.includes('## ✚ 10.200-11.100 wave\n'));
  assert.ok(nextMd.includes('## ✂ 12.5-13.0 人手備註的剪除區間\n'));

  // 2) 通過 findIllegalEdit 護欄
  assert.equal(findIllegalEdit(md, nextMd), null);

  // 3) 再解析回來,extractReviewOverrides 讀回相同毫秒區間
  const extracted = extractReviewOverrides(parseCutplan(nextMd));
  assert.deepEqual(extracted.managed.cut, [[10000, 10200], [11100, 11300]]);
  assert.deepEqual(extracted.managed.keep, [[10200, 11100]]);
  assert.deepEqual(extracted.readonly.cut, [[12500, 13000]]);

  // 4) Undo 逐 byte 還原
  const back = undo(step.doc, step.history);
  assert.equal(back.undone, true);
  assert.equal(serializeCutplan(back.doc), md);
});

test('findIllegalEdit: 竄改不帶 wave/audacity 的 ✂ 行或時間反轉 (s >= e) 必須被護欄擋下', () => {
  const md = [
    '# Cutplan',
    '## ⚙ clip-gap=0.5',
    '## ✂ 12.5-13.0 人手備註的剪除區間',
    '- [x] B0001 [0:10–0:12] [Alice] AABBCC',
    '',
  ].join('\n');

  // 刪掉人手備註的 ✂ 行 → 不合法
  const droppedReadonly = md.replace('## ✂ 12.5-13.0 人手備註的剪除區間\n', '');
  assert.match(findIllegalEdit(md, droppedReadonly), /行數不一致|唯讀行/);

  // 插入起點 >= 終點的 wave 行 → 不合法
  const badRange = md.replace(
    '## ⚙ clip-gap=0.5\n',
    '## ⚙ clip-gap=0.5\n## ✂ 15.000-12.000 wave\n',
  );
  assert.match(findIllegalEdit(md, badRange), /時間範圍不合法/);
});

test('computeBaseIntervalsFromDoc + timelineStatus: 即時反映勾選、刪除線與手動 ✂ 覆蓋', () => {
  const md = [
    '# Cutplan',
    '## ⚙ clip-gap=0.5',
    '## ✂ 0.000-1.000 wave',
    '- [x] B0001 [0:00–0:03] [Alice] ~~大家~~好我是愛麗絲',
    '- [ ] B0002 [0:03–0:05] [Bob] 這句不留',
    '',
  ].join('\n');
  const doc = parseCutplan(md);
  const part = buildTrackPartition(
    {
      blocks: [
        { id: 'B0001', start: 0.0, end: 3.0, speaker: 'Alice', text: '大家好我是愛麗絲' },
        { id: 'B0002', start: 3.0, end: 5.0, speaker: 'Bob', text: '這句不留' },
      ],
      gaps: [],
    },
    [
      { word: '大家', start: 0.0, end: 1.0 },
      { word: '好', start: 1.0, end: 1.5 },
      { word: '我是愛麗絲', start: 1.5, end: 3.0 },
      { word: '這句不留', start: 3.0, end: 5.0 },
    ],
    5000,
  );
  const base = computeBaseIntervalsFromDoc(doc, part);
  // B0001 的「大家」[0, 1000) 被刪除線扣掉,B0002 未勾選 → base = [[1000, 3000]]
  assert.deepEqual(base, [[1000, 3000]]);

  // 驗證 timelineStatus: 當 B0001 被手動 ✂ 覆蓋時,即使沒刪除線也不誤判 mismatch
  const docNoStrike = parseCutplan('- [x] B0001 [0:00–0:03] [Alice] 大家好\n');
  const tl = parseTimeline(JSON.stringify({
    version: 'v1_test',
    generated_at: '2026-11-06T00:00:00',
    final_duration_secs: 2.0,
    blocks: { B0001: null },
  }));
  assert.equal(timelineStatus(docNoStrike.lines[0], tl, { cut: [[0, 1000]], keep: [] }).mismatch, false);
});

// ── 4. SoundCraft Peaks 金字塔 + ValleySeam + Smart Trim Snap 測試 (#1219 M2) ──

test('SoundCraft Peaks: 多解析度 queryPeaksMinMax 與 queryPeaksRms 與暴力掃描逐 sample 完全一致', () => {
  const N = 4096;
  const samples = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    samples[i] = Math.sin(i * 0.03) * 0.8 + Math.cos(i * 0.17) * 0.15;
  }
  const peaks = buildPeaks(samples);
  assert.equal(peaks.len, N);
  assert.ok(peaks.levels.length >= 4);

  const ranges = [
    [0, N],
    [13, 29],       // 同一 block 內
    [17, 311],      // 跨 1 層
    [5, 3891],      // 跨多層金字塔
  ];
  for (const [s, e] of ranges) {
    let bruteLo = Infinity;
    let bruteHi = -Infinity;
    let bruteSq = 0;
    for (let i = s; i < e; i++) {
      const v = samples[i];
      if (v < bruteLo) bruteLo = v;
      if (v > bruteHi) bruteHi = v;
      bruteSq += v * v;
    }
    const bruteRms = Math.sqrt(bruteSq / (e - s));
    const [lo, hi] = queryPeaksMinMax(peaks, samples, s, e);
    const rms = queryPeaksRms(peaks, samples, s, e);
    assert.ok(Math.abs(lo - bruteLo) < 1e-6, `lo [${s},${e}): ${lo} vs ${bruteLo}`);
    assert.ok(Math.abs(hi - bruteHi) < 1e-6, `hi [${s},${e}): ${hi} vs ${bruteHi}`);
    assert.ok(Math.abs(rms - bruteRms) < 1e-6, `rms [${s},${e}): ${rms} vs ${bruteRms}`);
  }
});

test('findValleySeamMs: 在兩詞交界區間內準確找到能量谷底', () => {
  const sr = 16000;
  // 1 秒音訊,在 480ms~520ms 之間做一個明顯的靜音谷底 (500ms 處振幅接近 0)
  const samples = new Float32Array(sr);
  for (let i = 0; i < sr; i++) {
    const ms = (i / sr) * 1000;
    const env = Math.min(1, Math.abs(ms - 500) / 50);
    samples[i] = Math.sin(i * 0.2) * env;
  }
  const peaks = buildPeaks(samples);
  const seam = findValleySeamMs(peaks, samples, sr, 420, 580, 5);
  assert.ok(Math.abs(seam - 500) <= 5, `谷底應落在 500ms 附近,拿到 ${seam}ms`);
});

test('trimDragSnap: 預設硬吸附詞邊界、按住 Alt/Cmd 反轉為 1ms 自由滑動、原點 6px 磁吸防呆', () => {
  const gesture = {
    kind: 'trimStart',
    originMs: 10000,
    regionEndMs: 11300,
    snapPointsMs: [10000, 10400, 10900, 11300],
  };

  // 1) 未按修飾鍵:拖到 10213ms → 吸附到最近詞邊界 10400ms (或 10000ms,10213 距 10400 為 187 < 213)
  assert.equal(
    trimDragSnap(gesture, 10213, { snapEnabled: true, modifierHeld: false, pxPerMs: 0.2 }),
    10400,
  );

  // 2) 按住 Alt/Cmd 修飾鍵 (modifierHeld=true): 自由滑動,保留精確 10213ms (切出 ABBC!)
  assert.equal(
    trimDragSnap(gesture, 10213, { snapEnabled: true, modifierHeld: true, pxPerMs: 0.2 }),
    10213,
  );

  // 3) 距離原點 <= 6px (pxPerMs=0.2 時 20ms = 4px <= 6px) → 自動磁吸回原點 10000ms
  assert.equal(
    trimDragSnap(gesture, 10020, { snapEnabled: true, modifierHeld: true, pxPerMs: 0.2 }),
    10000,
  );

  // 4) 超過 regionEndMs - minKeepMs (11300 - 30 = 11270) 時自動 clamp
  assert.equal(
    trimDragSnap(gesture, 11295, { snapEnabled: true, modifierHeld: true, minKeepMs: 30 }),
    11270,
  );
});

// ── 5. CutPlayer 一鍵剪輯範圍 Playback 排程與跨切點游標跳躍測試 (#1220 M3) ───

test('buildCutPlaylist + mapPlaybackTimeToSourceMs: 自動跳過切除區間、游標在切點瞬間跳躍', () => {
  // 假設保留區間為 [1000, 2000) 與 ABBC [10210, 11050),中間 [2000, 10210) 被砍掉
  const finalIntervals = [[1000, 2000], [10210, 11050]];
  const pl = buildCutPlaylist(finalIntervals, { startFromSrcMs: 0, fadeMs: 4 });
  assert.equal(pl.length, 2);
  assert.deepEqual(
    pl.map((s) => [s.srcStartMs, s.srcEndMs, s.dstStartMs, s.dstEndMs]),
    [
      [1000, 2000, 0, 1000],
      [10210, 11050, 1000, 1840],
    ],
  );

  // 播放至 500ms → 位於第一段 [1000, 2000) 的 1500ms
  const p1 = mapPlaybackTimeToSourceMs(pl, 500);
  assert.equal(p1.playing, true);
  assert.equal(p1.srcMs, 1500);
  assert.equal(p1.segmentIndex, 0);

  // 播放至 1000ms (剛跨過切點!) → 游標瞬間從 2000ms 跳到 10210ms (ABBC 起點!)
  const p2 = mapPlaybackTimeToSourceMs(pl, 1000);
  assert.equal(p2.playing, true);
  assert.equal(p2.srcMs, 10210);
  assert.equal(p2.segmentIndex, 1);

  // 播放至 1840ms → 播放結束,停在 11050ms
  const p3 = mapPlaybackTimeToSourceMs(pl, 1840);
  assert.equal(p3.playing, false);
  assert.equal(p3.srcMs, 11050);

  // 若使用者游標停在被剪掉的 5000ms 按下一鍵播放 → 自動從下一段保留區間 10210ms 起播
  const plFromCut = buildCutPlaylist(finalIntervals, { startFromSrcMs: 5000 });
  assert.equal(plFromCut.length, 1);
  assert.equal(plFromCut[0].srcStartMs, 10210);
});

// ── 6. local.html 與 wave-editor.js 打包與語法整合測試 (#1221 M4) ─────────────

test('local.html + wave-editor.js: 打包產物包含 WaveEditor 掛載與語法零錯誤', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  const dir = path.join(__dirname, '..');
  const localHtml = fs.readFileSync(path.join(dir, 'local.html'), 'utf8');
  const waveEditorJs = fs.readFileSync(path.join(dir, 'wave-editor.js'), 'utf8');

  assert.ok(localHtml.includes('<script src="cutplan-core.js"></script>'));
  assert.ok(localHtml.includes('<script src="wave-editor.js"></script>'));
  assert.ok(localHtml.includes('window.__cutplanApp ='));
  assert.ok(localHtml.includes('window.WaveEditor.loadSessionAssets'));

  // 驗證 wave-editor.js 語法合法
  assert.doesNotThrow(() => new vm.Script(waveEditorJs, { filename: 'wave-editor.js' }));
});
