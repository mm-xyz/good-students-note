'use strict';
// scripts/cutplan-editor/tests/inline-sync.test.js
//
// cutplan-core.js 要「同一支檔案在 Node 測試與瀏覽器兩邊跑」,但 Google Apps
// Script 的 HtmlService 只能把 .html 檔案內嵌進網頁,不能直接 <script src>
// 一支 .js 檔(見 README「為什麼有 cutplan-core-inline.html」)。折衷做法:
// cutplan-core-inline.html = 原封不動的 cutplan-core.js 包一層 <script> 標籤。
// 這支測試就是防止兩邊漂移的自動防線——source of truth 永遠是 cutplan-core.js,
// inline.html 只能是它的逐字拷貝,少一個字都算 FAIL。

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const CORE_PATH = path.join(__dirname, '..', 'cutplan-core.js');
const INLINE_PATH = path.join(__dirname, '..', 'cutplan-core-inline.html');

test('cutplan-core-inline.html 與 cutplan-core.js 逐 byte 相同(只差 <script> 包裝)', () => {
  const core = fs.readFileSync(CORE_PATH, 'utf8');
  const inline = fs.readFileSync(INLINE_PATH, 'utf8');
  const expected = `<script>\n${core}</script>\n`;
  assert.equal(
    inline,
    expected,
    'cutplan-core-inline.html 漂移了 —— 改完 cutplan-core.js 記得重新產生 inline 版本'
    + '(scripts/cutplan-editor/tools/build-inline.js)',
  );
});
