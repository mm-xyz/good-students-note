#!/usr/bin/env node
'use strict';
// scripts/cutplan-editor/tools/check-real-file.js
//
// 一次性驗證工具(不進 repo 的 fixture,只讀真檔驗 parser——見 README「真檔
// round-trip 驗證」)。用法:
//   node scripts/cutplan-editor/tools/check-real-file.js /path/to/cutplan.md
//
// 做兩件事,只讀不寫:
//   1. parse → serialize,跟原檔案內容逐 byte 比對(round-trip 零差異)。
//   2. 在記憶體裡模擬「3 個勾選切換 + 加 1 段刪除線 + 去 1 段既有刪除線」,
//      印出 unified diff 讓人眼驗證只有這 5 處變動。
// 全程只 fs.readFileSync 原檔,從不 fs.writeFileSync 回真實路徑。

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const Core = require('../cutplan-core.js');

const target = process.argv[2];
if (!target) {
  console.error('用法: node check-real-file.js /path/to/cutplan.md');
  process.exit(2);
}

function md5(text) {
  return crypto.createHash('md5').update(text, 'utf8').digest('hex');
}

function unifiedDiffLines(a, b) {
  // 極簡逐行 diff(這支工具的資料量小、不追求 LCS 演算法,夠用就好——
  // ponytail:不為了一次性檢查工具重造 diff 演算法)。
  const al = a.split('\n');
  const bl = b.split('\n');
  const n = Math.max(al.length, bl.length);
  const out = [];
  for (let i = 0; i < n; i++) {
    if (al[i] !== bl[i]) {
      out.push({ line: i + 1, before: al[i], after: bl[i] });
    }
  }
  return out;
}

const before = fs.readFileSync(target, 'utf8');
const beforeMd5 = md5(before);

console.log('=== 1) round-trip(零編輯)===');
const doc = Core.parseCutplan(before);
const roundTripped = Core.serializeCutplan(doc);
const rtDiff = unifiedDiffLines(before, roundTripped);
if (rtDiff.length === 0 && roundTripped === before) {
  console.log('OK — parse → serialize 與原檔逐 byte 相同(diff 為空)');
} else {
  console.log(`FAIL — ${rtDiff.length} 行不同:`);
  rtDiff.slice(0, 10).forEach((d) => {
    console.log(`  L${d.line} - ${JSON.stringify(d.before)}`);
    console.log(`  L${d.line} + ${JSON.stringify(d.after)}`);
  });
}

console.log('\n=== 2) 模擬編輯:3 勾選切換 + 1 加刪除線 + 1 去刪除線 ===');
// 找前 3 個 block 行(不分 B/G)做勾選切換;找 1 個目前無刪除線的 block
// 加一段刪除線;找 1 個目前有既有刪除線的 block 去掉它。
let doc2 = doc;
const editableIdx = doc.lines
  .map((l, i) => (l.editable ? i : -1))
  .filter((i) => i >= 0);

const toggled = editableIdx.slice(0, 3);
for (const idx of toggled) {
  doc2 = Core.toggleCheckbox(doc2, idx);
}

let addedAt = null;
for (const idx of editableIdx) {
  const { clean, pieces } = Core.splitStrikes(doc.lines[idx].bodyRaw);
  const hasStrike = pieces.some((p) => p.kind === 'struck');
  if (!hasStrike && clean.length >= 2 && !toggled.includes(idx)) {
    doc2 = Core.applyStrike(doc2, idx, 0, Math.min(2, clean.length));
    addedAt = idx;
    break;
  }
}

let removedAt = null;
for (const idx of editableIdx) {
  if (idx === addedAt) continue;
  const { pieces } = Core.splitStrikes(doc.lines[idx].bodyRaw);
  const struck = pieces.find((p) => p.kind === 'struck');
  if (struck) {
    doc2 = Core.applyStrike(doc2, idx, struck.cleanStart, struck.cleanEnd);
    removedAt = idx;
    break;
  }
}

const after = Core.serializeCutplan(doc2);
const editDiff = unifiedDiffLines(before, after);
console.log(`勾選切換行:${toggled.map((i) => doc.lines[i].id).join(', ')}`);
console.log(`加刪除線行:${addedAt !== null ? doc.lines[addedAt].id : '(找不到符合條件的行)'}`);
console.log(`去刪除線行:${removedAt !== null ? doc.lines[removedAt].id : '(找不到符合條件的行)'}`);
console.log(`\n實際變動 ${editDiff.length} 行(預期 5 行):`);
editDiff.forEach((d) => {
  console.log(`  L${d.line} - ${d.before}`);
  console.log(`  L${d.line} + ${d.after}`);
});

console.log('\n=== 3) 真檔未被本工具動過 ===');
const afterReadBackMd5 = md5(fs.readFileSync(target, 'utf8'));
console.log(`md5 before = ${beforeMd5}`);
console.log(`md5 after  = ${afterReadBackMd5}`);
console.log(beforeMd5 === afterReadBackMd5 ? 'OK — 真檔 md5 不變' : 'FAIL — 真檔被動過!');
