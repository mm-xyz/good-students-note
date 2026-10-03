'use strict';
// scripts/cutplan-editor/tests/timeline.test.js
//
// Lifov #1078:聽成品時對照 cutplan。render 出片時在 cutplan.md 旁產
// cutplan.timeline.json(block id → 成品起點秒數,或 null=成品裡沒有),
// 編輯器載入時一起讀,卡片顯示「▶ v3 12:34」/「v3 未出現」,勾選與實際
// 成品不一致的列標出來;頂端輸入成品時間可以跳到那張卡。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const {
  parseCutplan,
  parseTimeline,
  parseTimeInput,
  formatTime,
  timelineStatus,
  findLineAtTime,
} = require('../cutplan-core.js');

const PLAN = [
  '# t',
  '## ⚙ line=mixdown',
  '## 🎬 集錦',
  '- [x] B0003 [0:12–0:13] [Bob] 金句。',
  '## 開場',
  '- [x] B0001 [0:01–0:02] [Alice] 第一句。',
  '- [ ] B0002 [0:02–0:03] [Bob] 被併回的取消列。',
  '- [x] B0003 [0:12–0:13] [Bob] 金句。',
  '- [x] B0004 [0:20–0:21] [Alice] 勾了卻沒出現。',
  '- [ ] B0005 [0:30–0:31] [Bob] 剪掉。',
  '- [x] B0006 [0:40–0:41] [Bob] 時間軸沒有這一列。',
  '',
].join('\n');

const TL = JSON.stringify({
  version: 'v3_20261003-1200',
  generated_at: '2026-10-03T12:00:00',
  blocks: { B0001: [10.0], B0002: [11.0], B0003: [0.0, 22.5], B0004: null, B0005: null },
});

const idx = (prefix, nth = 0) => PLAN.split('\n')
  .map((l, i) => [l, i]).filter(([l]) => l.startsWith(prefix))[nth][1];

test('parseTimeline:合法 JSON → 物件;壞掉/格式不對 → null(不丟錯)', () => {
  const tl = parseTimeline(TL);
  assert.equal(tl.version, 'v3_20261003-1200');
  assert.equal(tl.versionShort, 'v3');
  assert.equal(tl.generatedAt, '2026-10-03T12:00:00');
  assert.deepEqual(tl.blocks.B0003, [0, 22.5]);
  assert.equal(parseTimeline(''), null);
  assert.equal(parseTimeline(null), null);
  assert.equal(parseTimeline('{not json'), null);
  assert.equal(parseTimeline('{"version":"v1"}'), null);
});

test('parseTimeInput:12:34、754、1:02:03、12:34.5;不合法回 null', () => {
  assert.equal(parseTimeInput('12:34'), 754);
  assert.equal(parseTimeInput('754'), 754);
  assert.equal(parseTimeInput(' 1:02:03 '), 3723);
  assert.equal(parseTimeInput('12:34.5'), 754.5);
  assert.equal(parseTimeInput('12:３4'), null);
  for (const bad of ['', 'abc', '1:2:3:4', '12:', '12:61', '--5', '- 5']) {
    assert.equal(parseTimeInput(bad), null, bad);
  }
});

test('formatTime:m:ss,一小時以上 h:mm:ss', () => {
  assert.equal(formatTime(0), '0:00');
  assert.equal(formatTime(754.9), '12:34');
  assert.equal(formatTime(3723), '1:02:03');
});

test('timelineStatus:標籤與「勾選 vs 實際成品」不一致', () => {
  const doc = parseCutplan(PLAN);
  const tl = parseTimeline(TL);
  const s1 = timelineStatus(doc.lines[idx('- [x] B0001')], tl);
  assert.equal(s1.label, '▶ v3 0:10');
  assert.equal(s1.mismatch, false);
  const s2 = timelineStatus(doc.lines[idx('- [ ] B0002')], tl);
  assert.equal(s2.mismatch, true);                       // 取消卻仍出現
  assert.match(s2.label, /▶ v3 0:11/);
  const s3 = timelineStatus(doc.lines[idx('- [x] B0003', 1)], tl);
  assert.equal(s3.label, '▶ v3 0:00 · 0:22');            // 集錦＋正文
  const s4 = timelineStatus(doc.lines[idx('- [x] B0004')], tl);
  assert.equal(s4.label, 'v3 未出現');
  assert.equal(s4.mismatch, true);                       // 勾了卻未出現
  const s5 = timelineStatus(doc.lines[idx('- [ ] B0005')], tl);
  assert.equal(s5.label, 'v3 未出現');
  assert.equal(s5.mismatch, false);
  assert.equal(timelineStatus(doc.lines[idx('- [x] B0006')], tl), null); // 時間軸沒這列
  assert.equal(timelineStatus(doc.lines[idx('- [x] B0001')], null), null);
});

test('findLineAtTime:找起點 ≤ 該時間的最後一列;集錦時間回集錦那一列', () => {
  const doc = parseCutplan(PLAN);
  const tl = parseTimeline(TL);
  assert.equal(findLineAtTime(doc, tl, 10.5), idx('- [x] B0001'));
  assert.equal(findLineAtTime(doc, tl, 11.2), idx('- [ ] B0002'));
  assert.equal(findLineAtTime(doc, tl, 25), idx('- [x] B0003', 1));
  assert.equal(findLineAtTime(doc, tl, 3), idx('- [x] B0003', 0));
  // 負數=超出範圍(第五輪 TL-1-1 起;原本回 null 會被 UI 講成「之前沒有 block」)
  assert.equal(findLineAtTime(doc, tl, -1), require('../cutplan-core.js').OUT_OF_RANGE);
  assert.equal(findLineAtTime(doc, null, 3), null);
});

function loadCodeGs(files) {
  const mkFile = (name, content) => ({
    getBlob: () => ({ getDataAsString: () => content }),
    getName: () => name,
    getParents: () => {
      let done = false;
      return {
        hasNext: () => !done,
        next: () => {
          done = true;
          return {
            getFilesByName: (n) => {
              const hit = files[n];
              let used = false;
              return {
                hasNext: () => hit !== undefined && !used,
                next: () => { used = true; return mkFile(n, hit); },
              };
            },
          };
        },
      };
    },
  });
  const ctx = vm.createContext({
    DriveApp: { getFileById: () => mkFile('cutplan.md', files['cutplan.md']) },
    HtmlService: {},
    Date,
    JSON,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'cutplan-core.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8'), ctx);
  return ctx;
}

test('Code.gs loadCutplan:同資料夾有 timeline 就一起回傳,沒有回 null', () => {
  const withTl = loadCodeGs({ 'cutplan.md': PLAN, 'cutplan.timeline.json': TL });
  assert.equal(withTl.loadCutplan('id').timeline, TL);
  const without = loadCodeGs({ 'cutplan.md': PLAN });
  const r = without.loadCutplan('id');
  assert.equal(r.timeline, null);
  assert.equal(r.content, PLAN);
});

// ── 第四輪驗收 TL-1 / TL-2 ───────────────────────────────────────────────
const { OUT_OF_RANGE } = require('../cutplan-core.js');

test('TL-1 parseTimeline 保留 final_duration_secs', () => {
  const tl = parseTimeline(JSON.stringify({ version: 'v3', blocks: { B0001: [1] },
    final_duration_secs: 2608.9 }));
  assert.equal(tl.finalDuration, 2608.9);
  assert.equal(parseTimeline(TL).finalDuration, null);
});

test('TL-1 findLineAtTime:超過成品長度或負數 → OUT_OF_RANGE,不跳卡', () => {
  const doc = parseCutplan(PLAN);
  const tl = parseTimeline(JSON.stringify({ ...JSON.parse(TL), final_duration_secs: 30 }));
  assert.equal(findLineAtTime(doc, tl, 31), OUT_OF_RANGE);
  assert.equal(findLineAtTime(doc, tl, -1), OUT_OF_RANGE);
  assert.equal(findLineAtTime(doc, tl, 30), idx('- [x] B0003', 1));
  // 沒有 final_duration_secs:維持原行為(超過也找最後一張)
  assert.equal(findLineAtTime(doc, parseTimeline(TL), 9999), idx('- [x] B0003', 1));
});

test('TL-2 parseTimeline:任一 block 值不是 null 或有限非負數字陣列 → 整份 null', () => {
  const base = JSON.parse(TL);
  for (const bad of [
    { B0001: 'x' }, { B0001: [1, 'a'] }, { B0001: [-1] }, { B0001: [null] },
    { B0001: {} }, { B0001: 5 }, { B0001: [true] },
  ]) {
    const t = JSON.stringify({ ...base, blocks: { ...base.blocks, ...bad } });
    assert.equal(parseTimeline(t), null, JSON.stringify(bad));
  }
  assert.ok(parseTimeline(TL));
  assert.equal(parseTimeline(JSON.stringify({ version: 'v1', blocks: [] })), null);
  assert.equal(parseTimeline(JSON.stringify({ ...base, final_duration_secs: -3 })), null);
});

// ── 第五輪驗收 TL-1-1 / TL-2-1 / TL-2-2 ─────────────────────────────────
const { timelineFileState } = require('../cutplan-core.js');

test('TL-1-1 parseTimeInput:負數解析成負數(交給「超出範圍」),不是「看不懂」', () => {
  assert.equal(parseTimeInput('-1'), -1);
  assert.equal(parseTimeInput('-0:10'), -10);
  assert.equal(parseTimeInput(' -1:02:03 '), -3723);
  const doc = parseCutplan(PLAN);
  // 有成品長度 → OUT_OF_RANGE;沒有成品長度一樣 OUT_OF_RANGE(負數永遠超出)
  const withDur = parseTimeline(JSON.stringify({ ...JSON.parse(TL), final_duration_secs: 30 }));
  assert.equal(findLineAtTime(doc, withDur, parseTimeInput('-0:10')), OUT_OF_RANGE);
  assert.equal(findLineAtTime(doc, parseTimeline(TL), -1), OUT_OF_RANGE);
});

test('TL-2-1 final_duration_secs 明寫 null → 格式錯誤;欄位不存在才走舊行為', () => {
  const base = JSON.parse(TL);
  assert.equal(parseTimeline(JSON.stringify({ ...base, final_duration_secs: null })), null);
  assert.ok(parseTimeline(TL));
});

test('TL-2-2 timelineFileState:沒檔 absent;空白/壞掉 bad;合格 ok', () => {
  assert.equal(timelineFileState(null), 'absent');
  assert.equal(timelineFileState(undefined), 'absent');
  assert.equal(timelineFileState(''), 'bad');
  assert.equal(timelineFileState('   \n\t'), 'bad');
  assert.equal(timelineFileState('{nope'), 'bad');
  assert.equal(timelineFileState(TL), 'ok');
});
