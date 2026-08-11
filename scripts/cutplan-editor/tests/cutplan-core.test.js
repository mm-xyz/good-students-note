'use strict';
// scripts/cutplan-editor/tests/cutplan-core.test.js
// `node --test scripts/cutplan-editor/tests/`(零 npm 依賴,零 pytest 精神對齊
// scripts/tests/run_all.sh 的 unittest 慣例)。
//
// fixture 全部虛構(假人名 Alice/Bob、假對話),語法涵蓋 cutplan.md 全部行型別:
// 標題、引言、註解、⚙ config、✂ 手動剪除、🎵 BGM、章節、B/G 兩種 block 行、
// speaker 前綴、行尾 ← 理由、既有刪除線、未閉合 ~~。

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  parseCutplan,
  serializeCutplan,
  toggleCheckbox,
  splitStrikes,
  classifySelection,
  toggleStrike,
  applyStrike,
  isEditableLine,
} = require('../cutplan-core.js');

// ── fixtures ──────────────────────────────────────────────────────────────

const FIXTURE_LF = [
  '# Cutplan — 2099-01-01_TEST-fixture',
  '',
  '> 來源:fake.srt。`- [x]` = 保留,`- [ ]` = 剪掉;**改勾選就是剪輯**。',
  '> 測試用假資料,非真實逐字稿(Alice/Bob 為虛構人物)。',
  '',
  '<!-- 這是測試註解,不可被編輯 -->',
  '',
  '## ⚙ clip-gap=0.5 bgm-duck=0.15 max-pause=0.9',
  '',
  '## ✂ 12.5-13.0 假的手動剪除區間',
  '',
  '- [ ] G0001 [0:00–0:02] ⬜ 空白/非語音 2.0s(靜音;勾選=保留原聲)',
  '## 🎵 opening start=0 end=10 fadein=2 fadeout=3 lead=3 tail=3',
  '- [x] B0001 [0:02–0:05] [Alice] 大家好我是愛麗絲。',
  '- [ ] B0002 [0:05–0:08] [Bob] 呃這個那個其實我覺得很好。',
  '- [x] B0003 [0:08–0:12] [Alice] ~~嗯~~今天要聊的主題是假資料。',
  '- [x] B0004 [0:12–0:20] [Bob] 我們可以先講重點,~~然後再講細節好了,~~ ← 二剪:順序調整',
  '- [x] B0005 [0:20–0:23] [Alice] 這是字面的~~符號沒有配對',
  '## 休息一下章節標題',
  '- [x] B0006 [0:23–0:25] [Bob] 好的沒問題。',
  '',
].join('\n') + '\n';

// 找 fixture 裡某行的 0-based index(依內容前綴比對,避免每個測試手數行號)
function lineIndexOf(text, startsWith) {
  const lines = text.split('\n');
  const idx = lines.findIndex((l) => l.startsWith(startsWith));
  assert.notEqual(idx, -1, `fixture 裡找不到開頭是「${startsWith}」的行`);
  return idx;
}

// ── (a) byte-for-byte round-trip ────────────────────────────────────────

test('round-trip: parse → serialize 零編輯必須與原字串完全相同(LF fixture)', () => {
  const doc = parseCutplan(FIXTURE_LF);
  assert.equal(serializeCutplan(doc), FIXTURE_LF);
});

test('round-trip: CRLF 行尾原樣保留', () => {
  const crlf = '# T\r\n\r\n- [ ] B0001 [0:00–0:01] [Alice] 哈囉\r\n';
  const doc = parseCutplan(crlf);
  assert.equal(serializeCutplan(doc), crlf);
});

test('round-trip: 檔案不以換行結尾也要原樣保留', () => {
  const noEol = '# T\n- [ ] B0001 [0:00–0:01] [Alice] 哈囉';
  const doc = parseCutplan(noEol);
  assert.equal(serializeCutplan(doc), noEol);
});

test('round-trip: 空白行與連續空白行原樣保留', () => {
  const blanks = '# T\n\n\n\n- [ ] B0001 [0:00–0:01] [Alice] 哈囉\n\n';
  const doc = parseCutplan(blanks);
  assert.equal(serializeCutplan(doc), blanks);
});

test('round-trip: 空字串輸入', () => {
  const doc = parseCutplan('');
  assert.equal(serializeCutplan(doc), '');
});

// ── (b) 勾選切換只改一個字元 ──────────────────────────────────────────────

test('toggleCheckbox: 只改該行的 [x]/[ ] 一個字元,其餘 byte 不動', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0001');
  const next = toggleCheckbox(doc, idx);
  const before = serializeCutplan(doc).split('\n');
  const after = serializeCutplan(next).split('\n');
  assert.equal(before.length, after.length);
  for (let i = 0; i < before.length; i++) {
    if (i === idx) {
      assert.equal(before[i], '- [x] B0001 [0:02–0:05] [Alice] 大家好我是愛麗絲。');
      assert.equal(after[i], '- [ ] B0001 [0:02–0:05] [Alice] 大家好我是愛麗絲。');
    } else {
      assert.equal(before[i], after[i], `第 ${i} 行不該變動`);
    }
  }
});

test('toggleCheckbox: 反向 [ ] → [x] 一樣只改一個字元,且可還原', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] B0002');
  const on = toggleCheckbox(doc, idx);
  assert.equal(
    serializeCutplan(on).split('\n')[idx],
    '- [x] B0002 [0:05–0:08] [Bob] 呃這個那個其實我覺得很好。',
  );
  const off = toggleCheckbox(on, idx);
  assert.equal(serializeCutplan(off), FIXTURE_LF, '切回去要跟原檔完全一致');
});

test('toggleCheckbox: G 列(空白/非語音)也是可勾選的 block 行', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] G0001');
  const next = toggleCheckbox(doc, idx);
  assert.equal(
    serializeCutplan(next).split('\n')[idx],
    '- [x] G0001 [0:00–0:02] ⬜ 空白/非語音 2.0s(靜音;勾選=保留原聲)',
  );
});

// ── (c) 加刪除線落在正確字元位置 ────────────────────────────────────────

test('applyStrike: 對無刪除線的 block 內文反白加刪除線', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] B0002');
  // clean text = "呃這個那個其實我覺得很好。"; 選 [0,2) = "呃這"
  const next = applyStrike(doc, idx, 0, 2);
  assert.equal(
    serializeCutplan(next).split('\n')[idx],
    '- [ ] B0002 [0:05–0:08] [Bob] ~~呃這~~個那個其實我覺得很好。',
  );
});

test('applyStrike: 選取範圍在文字中段一樣落在正確位置', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] B0002');
  // clean = "呃這個那個其實我覺得很好。"(12 字);選 [2,4) = "個那"
  const next = applyStrike(doc, idx, 2, 4);
  assert.equal(
    serializeCutplan(next).split('\n')[idx],
    '- [ ] B0002 [0:05–0:08] [Bob] 呃這~~個那~~個其實我覺得很好。',
  );
});

test('applyStrike: speaker 前綴與行尾理由不受影響、也不可被劃入選取範圍內容', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0006');
  // clean = "好的沒問題。"
  const next = applyStrike(doc, idx, 0, 2);
  assert.equal(
    serializeCutplan(next).split('\n')[idx],
    '- [x] B0006 [0:23–0:25] [Bob] ~~好的~~沒問題。',
  );
});

// ── (d) 反白既有刪除線範圍 → 還原,結果與加之前完全相同(往返一致) ─────

test('applyStrike: 反白既有刪除線的完整範圍 → 去掉 ~~ 還原', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0003');
  const { clean } = splitStrikes(doc.lines[idx].bodyRaw);
  assert.equal(clean, '嗯今天要聊的主題是假資料。');
  // 「嗯」是既有刪除線,clean 座標 [0,1)
  const restored = applyStrike(doc, idx, 0, 1);
  assert.equal(
    serializeCutplan(restored).split('\n')[idx],
    '- [x] B0003 [0:08–0:12] [Alice] 嗯今天要聊的主題是假資料。',
  );
});

test('applyStrike: 加刪除線後再對同範圍還原 = 原字串(往返一致)', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] B0002');
  const struck = applyStrike(doc, idx, 0, 2);
  const restored = applyStrike(struck, idx, 0, 2);
  assert.equal(serializeCutplan(restored), FIXTURE_LF);
});

test('applyStrike: 既有刪除線 + 行尾理由的 block 也能還原、理由原樣保留', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0004');
  const { clean } = splitStrikes(doc.lines[idx].bodyRaw);
  assert.equal(clean, '我們可以先講重點,然後再講細節好了,');
  // 既有刪除線範圍 = clean[9, 18) = "然後再講細節好了,"
  const restored = applyStrike(doc, idx, 9, 18);
  assert.equal(
    serializeCutplan(restored).split('\n')[idx],
    '- [x] B0004 [0:12–0:20] [Bob] 我們可以先講重點,然後再講細節好了, ← 二剪:順序調整',
  );
});

// ── (e) 非 block 行唯讀 ──────────────────────────────────────────────────

for (const [label, prefix] of [
  ['標題', '# Cutplan'],
  ['引言', '> 來源'],
  ['註解', '<!-- 這是測試註解'],
  ['⚙ config', '## ⚙'],
  ['✂ 手動剪除', '## ✂'],
  ['🎵 BGM', '## 🎵'],
  ['章節', '## 休息一下'],
  ['空白行', ''],
]) {
  test(`isEditableLine: ${label}行標記為唯讀`, () => {
    const doc = parseCutplan(FIXTURE_LF);
    const idx = lineIndexOf(FIXTURE_LF, prefix);
    assert.equal(isEditableLine(doc, idx), false);
  });

  test(`toggleCheckbox: 對${label}行操作要丟錯,不可靜默忽略`, () => {
    const doc = parseCutplan(FIXTURE_LF);
    const idx = lineIndexOf(FIXTURE_LF, prefix);
    assert.throws(() => toggleCheckbox(doc, idx));
  });
}

test('isEditableLine: block 行(B/G)標記為可編輯', () => {
  const doc = parseCutplan(FIXTURE_LF);
  assert.equal(isEditableLine(doc, lineIndexOf(FIXTURE_LF, '- [x] B0001')), true);
  assert.equal(isEditableLine(doc, lineIndexOf(FIXTURE_LF, '- [ ] G0001')), true);
});

// ── (f) 邊界:刪除線交疊/巢狀行為明確定義 ────────────────────────────────
//
// 2026-08-11 MM 實測回報兩個 bug(都在「取消刪除線」路徑):
//   1. 「`~~1~~ 23 ~~4~~` 不能批次取消」
//   2. 「現在也沒辦法取消標記」
// 根因:舊版 classifySelection 只有「選取恰好等於單一 struck 片段邊界」才
// 判定 'remove',其餘部分重疊/橫跨多段一律 'invalid' → toggleStrike 丟錯,
// 手機長按拖曳幾乎不可能精準對齊那個邊界。新語意:選取範圍內只要「碰到」
// 任何既有刪除線(完全包含/被包含/部分重疊/橫跨多段皆算),就整段整段拆掉
// 那些被碰到的刪除線;選取範圍內完全沒有既有刪除線才是 'add';空選取是
// 獨立的 'empty' 狀態(不是 'invalid'),UI 要能用它顯示可見提示。

test('classifySelection: 選取完全落在既有刪除線內 → remove,整段刪除線一起取消', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0004');
  // 既有刪除線 clean[9,18);選 [12,17) 完全落在裡面、邊界不對齊
  const mode = classifySelection(doc.lines[idx].bodyRaw, 12, 17);
  assert.equal(mode, 'remove');
  const restored = applyStrike(doc, idx, 12, 17);
  assert.equal(
    serializeCutplan(restored).split('\n')[idx],
    '- [x] B0004 [0:12–0:20] [Bob] 我們可以先講重點,然後再講細節好了, ← 二剪:順序調整',
  );
});

test('classifySelection: 選取橫跨「已刪除線」與「未刪除線」邊界 → remove,整段一起取消', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0004');
  // clean[9,18) 是既有刪除線,選 [8,12) 一半在外(plain)一半在內(struck)
  const mode = classifySelection(doc.lines[idx].bodyRaw, 8, 12);
  assert.equal(mode, 'remove');
  const restored = applyStrike(doc, idx, 8, 12);
  assert.equal(
    serializeCutplan(restored).split('\n')[idx],
    '- [x] B0004 [0:12–0:20] [Bob] 我們可以先講重點,然後再講細節好了, ← 二剪:順序調整',
  );
});

test('applyStrike: 選取範圍比既有刪除線更寬(左右都多包 plain 文字) → remove,plain 部分不受影響', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0004');
  // 既有刪除線 clean[9,18);選 [5,18) 左邊多包 5 個字的 plain 文字
  const mode = classifySelection(doc.lines[idx].bodyRaw, 5, 18);
  assert.equal(mode, 'remove');
  const restored = applyStrike(doc, idx, 5, 18);
  assert.equal(
    serializeCutplan(restored).split('\n')[idx],
    '- [x] B0004 [0:12–0:20] [Bob] 我們可以先講重點,然後再講細節好了, ← 二剪:順序調整',
  );
});

test('classifySelection: 空選取(start===end) → empty,跟 invalid 分開(UI 要能顯示可見提示)', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] B0002');
  assert.equal(classifySelection(doc.lines[idx].bodyRaw, 3, 3), 'empty');
});

test('applyStrike: 空選取要丟錯(不是靜默忽略),錯誤訊息要能分辨「沒選取」', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] B0002');
  assert.throws(() => applyStrike(doc, idx, 3, 3), /空|選取/);
});

test('classifySelection: 選取含 block 內文以外的位置(超出文字長度)→ invalid', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [ ] B0002');
  const { clean } = splitStrikes(doc.lines[idx].bodyRaw);
  assert.equal(classifySelection(doc.lines[idx].bodyRaw, 0, clean.length + 5), 'invalid');
  assert.throws(() => applyStrike(doc, idx, 0, clean.length + 5));
});

test('classifySelection/applyStrike: 跨段但只蓋到頭尾兩段的一部分 → 兩段都整段取消', () => {
  const twoWide = '- [x] B0098 [0:00–0:09] [Alice] ~~abc~~mid~~def~~\n';
  const doc = parseCutplan(twoWide);
  const { clean } = splitStrikes(doc.lines[0].bodyRaw);
  assert.equal(clean, 'abcmiddef');
  // struck1 = clean[0,3)"abc",plain = clean[3,6)"mid",struck2 = clean[6,9)"def"
  // 選 [1,7):只蓋到 struck1 的尾巴("bc")、全部 plain、struck2 的頭("de")
  const mode = classifySelection(doc.lines[0].bodyRaw, 1, 7);
  assert.equal(mode, 'remove');
  const restored = applyStrike(doc, 0, 1, 7);
  assert.equal(
    serializeCutplan(restored),
    '- [x] B0098 [0:00–0:09] [Alice] abcmiddef\n',
  );
});

test('MM 原話重現:`~~1~~ 23 ~~4~~` 全選 → 一次變成 `1 23 4`(批次取消)', () => {
  const mmExample = '- [x] B0097 [0:00–0:07] [Alice] ~~1~~ 23 ~~4~~\n';
  const doc = parseCutplan(mmExample);
  const { clean } = splitStrikes(doc.lines[0].bodyRaw);
  assert.equal(clean, '1 23 4');
  const mode = classifySelection(doc.lines[0].bodyRaw, 0, clean.length);
  assert.equal(mode, 'remove');
  const restored = applyStrike(doc, 0, 0, clean.length);
  assert.equal(
    serializeCutplan(restored),
    '- [x] B0097 [0:00–0:07] [Alice] 1 23 4\n',
  );
});

test('splitStrikes: 未閉合的 ~~ 當字面文字,不產生刪除線 span', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0005');
  const { clean, pieces } = splitStrikes(doc.lines[idx].bodyRaw);
  assert.equal(clean, '這是字面的~~符號沒有配對');
  assert.ok(pieces.every((p) => p.kind === 'plain'));
});

test('splitStrikes: 未閉合 ~~ 的 block 整段仍可視為未刪除線,可整段加刪除線', () => {
  const doc = parseCutplan(FIXTURE_LF);
  const idx = lineIndexOf(FIXTURE_LF, '- [x] B0005');
  const { clean } = splitStrikes(doc.lines[idx].bodyRaw);
  const next = applyStrike(doc, idx, 0, clean.length);
  assert.equal(
    serializeCutplan(next).split('\n')[idx],
    '- [x] B0005 [0:20–0:23] [Alice] ~~這是字面的~~符號沒有配對~~',
  );
});

test('splitStrikes: 兩段既有刪除線分別可各自還原,互不影響', () => {
  const twoStrikes =
    '- [x] B0099 [0:00–0:05] [Alice] ~~甲~~乙~~丙~~丁\n';
  const doc = parseCutplan(twoStrikes);
  const { clean, pieces } = splitStrikes(doc.lines[0].bodyRaw);
  assert.equal(clean, '甲乙丙丁');
  const struckPieces = pieces.filter((p) => p.kind === 'struck');
  assert.equal(struckPieces.length, 2);
  assert.deepEqual([struckPieces[0].cleanStart, struckPieces[0].cleanEnd], [0, 1]);
  assert.deepEqual([struckPieces[1].cleanStart, struckPieces[1].cleanEnd], [2, 3]);
  const restoredFirst = applyStrike(doc, 0, 0, 1);
  assert.equal(
    serializeCutplan(restoredFirst),
    '- [x] B0099 [0:00–0:05] [Alice] 甲乙~~丙~~丁\n',
  );
  const restoredBoth = applyStrike(restoredFirst, 0, 2, 3);
  assert.equal(
    serializeCutplan(restoredBoth),
    '- [x] B0099 [0:00–0:05] [Alice] 甲乙丙丁\n',
  );
});

test('classifySelection/applyStrike: 選取橫跨兩段既有刪除線(中間夾未刪除線)→ remove,兩段都取消、中間 plain 不變', () => {
  const twoStrikes =
    '- [x] B0099 [0:00–0:05] [Alice] ~~甲~~乙~~丙~~丁\n';
  const doc = parseCutplan(twoStrikes);
  // clean = "甲乙丙丁",甲=[0,1) 丙=[2,3);選 [0,3) 橫跨兩段刪除線與中間的乙
  const mode = classifySelection(doc.lines[0].bodyRaw, 0, 3);
  assert.equal(mode, 'remove');
  const restored = applyStrike(doc, 0, 0, 3);
  assert.equal(
    serializeCutplan(restored),
    '- [x] B0099 [0:00–0:05] [Alice] 甲乙丙丁\n',
  );
});
