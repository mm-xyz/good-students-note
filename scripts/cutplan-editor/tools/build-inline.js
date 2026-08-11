#!/usr/bin/env node
'use strict';
// scripts/cutplan-editor/tools/build-inline.js
//
// 把 cutplan-core.js 逐字包一層 <script> 標籤,重新產生 cutplan-core-inline.html
// (Apps Script HtmlService 給瀏覽器用的版本)。改完 cutplan-core.js 之後跑:
//   node scripts/cutplan-editor/tools/build-inline.js
// 再跑 `node --test "scripts/cutplan-editor/tests/**/*.test.js"` 確認同步。
// 零依賴、零 npm install。

const fs = require('node:fs');
const path = require('node:path');

const CORE_PATH = path.join(__dirname, '..', 'cutplan-core.js');
const INLINE_PATH = path.join(__dirname, '..', 'cutplan-core-inline.html');

const core = fs.readFileSync(CORE_PATH, 'utf8');
fs.writeFileSync(INLINE_PATH, `<script>\n${core}</script>\n`);
console.log(`[build-inline] ${path.relative(process.cwd(), INLINE_PATH)} 已重新產生`
  + `(來源 ${path.relative(process.cwd(), CORE_PATH)}, ${core.length} bytes)`);
