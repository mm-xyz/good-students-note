'use strict';
/**
 * scripts/cutplan-editor/cutplan-core.js — cutplan.md 的最小可編輯核心(純函式、零依賴)。
 *
 * 語意對齊 scripts/audio/cutplan.py(write_cutplan_md)與 scripts/audio/render_cut.py
 * (LINE_RE / parse_program / parse_strikes)——block 行格式、speaker 前綴、行尾
 * ` ← 理由` 後綴、`~~刪除線~~` 語法皆照抄。**只開放兩個編輯動作**:
 *   1. toggleCheckbox — 切換 `- [x]` / `- [ ]`
 *   2. applyStrike    — 對 block 內文反白範圍加/去 `~~刪除線~~`
 * 其餘一切(id、時間碼、非 block 行)一律唯讀,本檔不提供任何改動它們的 API。
 *
 * 這支檔案同時給 Node(`node --test`)與瀏覽器(`<script>` 內嵌)使用,
 * 檔尾用 module.exports guard,不用任何 import/export 語法。
 */

// `- [x] B0018 [1:59–2:10] 其餘內容...` — 對齊 scripts/audio/render_cut.py 的 LINE_RE。
// 注意:不 trim 就直接從行首匹配 —— 沒有前導空白的行才視為可編輯 block 行,
// 任何非標準排版(縮排、額外空白)一律落回唯讀,寧可少開放也不誤判可編輯。
const BLOCK_LINE_RE = /^- \[( |x|X)\] ([A-Z]{1,2}\d{3,5}) \[([^\]]+)\] (.*)$/;

// speaker 前綴,如 `[KIN] `——對齊 render_cut.py `re.sub(r"^\[[^\]]{1,20}\]\s*", "", body)`
const SPEAKER_PREFIX_RE = /^\[[^\]]{1,20}\]\s*/;

// 行尾人工註記,如 ` ← 二剪:...`——對齊 render_cut.py `body.rsplit(" ← ", 1)`
// (取「最後一個」` ← ` 當分界,跟 Python rsplit 語意一致)。
const REASON_SEP = ' ← ';

function notImplemented(name) {
  throw new Error(`cutplan-core: ${name} 尚未實作(TDD 紅燈階段)`);
}

function parseCutplan(_text) {
  notImplemented('parseCutplan');
}

function serializeCutplan(_doc) {
  notImplemented('serializeCutplan');
}

function toggleCheckbox(_doc, _lineIndex) {
  notImplemented('toggleCheckbox');
}

function splitStrikes(_bodyRaw) {
  notImplemented('splitStrikes');
}

function classifySelection(_bodyRaw, _cleanStart, _cleanEnd) {
  notImplemented('classifySelection');
}

function toggleStrike(_bodyRaw, _cleanStart, _cleanEnd) {
  notImplemented('toggleStrike');
}

function applyStrike(_doc, _lineIndex, _cleanStart, _cleanEnd) {
  notImplemented('applyStrike');
}

function isEditableLine(_doc, _lineIndex) {
  notImplemented('isEditableLine');
}

const api = {
  parseCutplan,
  serializeCutplan,
  toggleCheckbox,
  splitStrikes,
  classifySelection,
  toggleStrike,
  applyStrike,
  isEditableLine,
  BLOCK_LINE_RE,
  SPEAKER_PREFIX_RE,
  REASON_SEP,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = api;
}
if (typeof window !== 'undefined') {
  window.CutplanCore = api;
}
