'use strict';
// scripts/cutplan-editor/tests/tracks.test.js
//
// `## ⚙ line=mixdown audio=tracks` 的軌欄(ADR-2026-10-03-audio-tracks-line)。
// 每個 B/G 列行尾一欄 ` ⟦Mars● Sarah○ Kin○⟧`:● = 這段這一軌出聲,○ = 壓 −27dB。
// 編輯器只多開「切換軌欄」這一個口——欄位本身(有沒有、哪幾軌、順序、格式)
// 一律唯讀,伺服端 saveCutplan 的護欄也只放行 ●/○ 的翻轉。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const core = require('../cutplan-core.js');
const {
  parseCutplan,
  serializeCutplan,
  toggleCheckbox,
  applyStrike,
  createHistory,
  peekHistory,
  undo,
  parseTracks,
  hasTracksAudio,
  toggleTrack,
  toggleTrackWithHistory,
  findIllegalEdit,
} = core;

const PLAN = [
  '# Cutplan — 2099-01-01_TEST-tracks',
  '',
  '## ⚙ line=mixdown template=x audio=tracks',
  '',
  '- [ ] G0001 [0:00–0:02] ⬜ 空白/非語音 2.0s(靜音;勾選=保留原聲) ⟦Alice● Bob● Cara●⟧',
  '- [x] B0001 [0:02–0:05] [Alice] 大家好我是愛麗絲。 ⟦Alice● Bob○ Cara○⟧',
  '- [x] B0002 [0:05–0:08] [Bob] ~~嗯~~我覺得很好。 ← 二剪 ⟦Alice○ Bob● Cara○⟧',
  '## ➕ 假的補錄.wav gain=auto',
  '- [x] S0001 [0:00–0:01] [Bob] 補錄。',
  '',
].join('\n');

const idxOf = (text, prefix) => text.split('\n').findIndex((l) => l.startsWith(prefix));

// ── 解析 / 序列化 ──────────────────────────────────────────────────────────

test('round-trip:帶軌欄的節目單逐 byte 還原(LF / CRLF)', () => {
  assert.equal(serializeCutplan(parseCutplan(PLAN)), PLAN);
  const crlf = PLAN.split('\n').join('\r\n');
  assert.equal(serializeCutplan(parseCutplan(crlf)), crlf);
});

test('parse:軌欄切成獨立欄位,不在 bodyRaw、也不在 reason 裡', () => {
  const doc = parseCutplan(PLAN);
  const b1 = doc.lines[idxOf(PLAN, '- [x] B0001')];
  assert.equal(b1.bodyRaw, '大家好我是愛麗絲。');
  assert.equal(b1.reason, '');
  assert.equal(b1.tracksRaw, ' ⟦Alice● Bob○ Cara○⟧');
  const b2 = doc.lines[idxOf(PLAN, '- [x] B0002')];
  assert.equal(b2.bodyRaw, '~~嗯~~我覺得很好。');
  assert.equal(b2.reason, ' ← 二剪');
  assert.equal(b2.tracksRaw, ' ⟦Alice○ Bob● Cara○⟧');
  const s1 = doc.lines[idxOf(PLAN, '- [x] S0001')];
  assert.equal(s1.tracksRaw, '');
});

test('parseTracks:欄位 → [{name,on}];格式不對回 null', () => {
  assert.deepEqual(parseTracks(' ⟦Alice● Bob○ Cara○⟧'), [
    { name: 'Alice', on: true }, { name: 'Bob', on: false }, { name: 'Cara', on: false },
  ]);
  assert.equal(parseTracks(''), null);
  assert.equal(parseTracks(' ⟦Alice? Bob○⟧'), null);
  assert.equal(parseTracks(' ⟦Alice● Alice○⟧'), null);
});

test('hasTracksAudio:只看 ⚙ 行有沒有 audio=tracks', () => {
  assert.equal(hasTracksAudio(parseCutplan(PLAN)), true);
  assert.equal(hasTracksAudio(parseCutplan(PLAN.replace(' audio=tracks', ''))), false);
});

test('勾選 / 刪除線不會動到軌欄', () => {
  const doc = parseCutplan(PLAN);
  const i = idxOf(PLAN, '- [x] B0001');
  const t1 = serializeCutplan(toggleCheckbox(doc, i));
  assert.equal(t1.split('\n')[i],
    '- [ ] B0001 [0:02–0:05] [Alice] 大家好我是愛麗絲。 ⟦Alice● Bob○ Cara○⟧');
  const t2 = serializeCutplan(applyStrike(doc, i, 0, 3));
  assert.equal(t2.split('\n')[i],
    '- [x] B0001 [0:02–0:05] [Alice] ~~大家好~~我是愛麗絲。 ⟦Alice● Bob○ Cara○⟧');
});

// ── 切換軌欄 ───────────────────────────────────────────────────────────────

test('toggleTrack:只翻那一格的 ●/○,其餘逐 byte 不動;翻兩次還原', () => {
  const doc = parseCutplan(PLAN);
  const i = idxOf(PLAN, '- [x] B0002');
  const once = serializeCutplan(toggleTrack(doc, i, 'Cara'));
  const a = PLAN.split('\n');
  const b = once.split('\n');
  assert.equal(b[i], '- [x] B0002 [0:05–0:08] [Bob] ~~嗯~~我覺得很好。 ← 二剪 ⟦Alice○ Bob● Cara●⟧');
  b.forEach((l, k) => { if (k !== i) assert.equal(l, a[k]); });
  const twice = serializeCutplan(toggleTrack(parseCutplan(once), i, 'Cara'));
  assert.equal(twice, PLAN);
});

test('toggleTrack:沒有軌欄的列、唯讀行、不存在的軌一律丟錯', () => {
  const doc = parseCutplan(PLAN);
  assert.throws(() => toggleTrack(doc, idxOf(PLAN, '- [x] S0001'), 'Bob'), /軌欄/);
  assert.throws(() => toggleTrack(doc, idxOf(PLAN, '## ⚙'), 'Bob'), /唯讀/);
  assert.throws(() => toggleTrack(doc, idxOf(PLAN, '- [x] B0001'), 'Zed'), /Zed/);
});

test('toggleTrackWithHistory → undo 回到逐 byte 相同;peekHistory 標記是軌欄', () => {
  const doc = parseCutplan(PLAN);
  const i = idxOf(PLAN, '- [x] B0001');
  const r = toggleTrackWithHistory(doc, createHistory(), i, 'Bob');
  assert.notEqual(serializeCutplan(r.doc), PLAN);
  assert.deepEqual(peekHistory(r.history),
    { lineIndex: i, start: 0, end: 0, kind: 'tracks', track: 'Bob' });
  const u = undo(r.doc, r.history);
  assert.equal(u.undone, true);
  assert.equal(serializeCutplan(u.doc), PLAN);
});

// ── 伺服端護欄(findIllegalEdit,Code.gs 的 saveCutplan 用它)────────────

const edit = (from, to) => PLAN.replace(from, to);

test('護欄放行:只翻軌欄', () => {
  assert.equal(findIllegalEdit(PLAN, edit('愛麗絲。 ⟦Alice● Bob○', '愛麗絲。 ⟦Alice● Bob●')), null);
});

test('護欄放行:既有兩個動作照舊(勾選、刪除線)', () => {
  assert.equal(findIllegalEdit(PLAN, edit('- [x] B0001', '- [ ] B0001')), null);
  assert.equal(findIllegalEdit(PLAN, edit('大家好我是', '~~大家好~~我是')), null);
});

test('護欄拒絕:軌欄被加、被刪、改名、換順序、亂改格式', () => {
  const cases = [
    edit('- [x] S0001 [0:00–0:01] [Bob] 補錄。', '- [x] S0001 [0:00–0:01] [Bob] 補錄。 ⟦Alice● Bob○ Cara○⟧'),
    edit('愛麗絲。 ⟦Alice● Bob○ Cara○⟧', '愛麗絲。'),
    edit('⟦Alice● Bob○ Cara○⟧', '⟦Alice● Bobby○ Cara○⟧'),
    edit('⟦Alice● Bob○ Cara○⟧', '⟦Bob○ Alice● Cara○⟧'),
    edit('⟦Alice● Bob○ Cara○⟧', '⟦Alice●  Bob○ Cara○⟧'),
    edit('⟦Alice● Bob○ Cara○⟧', '⟦Alice● Bob○⟧'),
  ];
  for (const c of cases) {
    assert.notEqual(c, PLAN);
    assert.match(findIllegalEdit(PLAN, c) || '', /軌欄/, c);
  }
});

test('護欄拒絕:⚙ 沒寫 audio=tracks 時連翻軌欄也不行', () => {
  const base = PLAN.replace(' audio=tracks', '');
  const changed = base.replace('愛麗絲。 ⟦Alice● Bob○', '愛麗絲。 ⟦Alice● Bob●');
  assert.match(findIllegalEdit(base, changed) || '', /audio=tracks/);
});

test('護欄拒絕:其他一切照舊(文字、理由、唯讀行、行數)', () => {
  assert.match(findIllegalEdit(PLAN, edit('大家好我是', '大家好我們是')) || '', /逐字稿文字/);
  assert.match(findIllegalEdit(PLAN, edit(' ← 二剪', ' ← 三剪')) || '', /理由/);
  assert.match(findIllegalEdit(PLAN, edit('template=x audio=tracks', 'template=y audio=tracks')) || '', /唯讀/);
  assert.match(findIllegalEdit(PLAN, PLAN + 'x\n') || '', /行數/);
});

// ── Code.gs 真的用這支護欄(vm 載入,DriveApp 打樁)─────────────────────

function loadCodeGs(fileContent) {
  const saved = { content: null };
  const file = {
    getBlob: () => ({ getDataAsString: () => fileContent }),
    setContent: (c) => { saved.content = c; },
    getName: () => 'cutplan.md',
  };
  const ctx = vm.createContext({
    DriveApp: { getFileById: () => file },
    HtmlService: {},
    Date,
  });
  // Apps Script:cutplan-core.js 與 Code.gs 共用同一個全域命名空間
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'cutplan-core.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8'), ctx);
  return { ctx, saved };
}

test('Code.gs saveCutplan:只改軌欄 → 寫入', () => {
  const { ctx, saved } = loadCodeGs(PLAN);
  const next = edit('愛麗絲。 ⟦Alice● Bob○', '愛麗絲。 ⟦Alice● Bob●');
  const r = ctx.saveCutplan('id', next);
  assert.equal(r.ok, true);
  assert.equal(saved.content, next);
});

test('Code.gs saveCutplan:軌欄被加/文字被改 → 拒絕、不寫入', () => {
  for (const bad of [
    edit('- [x] S0001 [0:00–0:01] [Bob] 補錄。', '- [x] S0001 [0:00–0:01] [Bob] 補錄。 ⟦Alice● Bob○ Cara○⟧'),
    edit('大家好我是', '大家好我們是'),
  ]) {
    const { ctx, saved } = loadCodeGs(PLAN);
    assert.throws(() => ctx.saveCutplan('id', bad), /儲存被拒絕/);
    assert.equal(saved.content, null);
  }
});
