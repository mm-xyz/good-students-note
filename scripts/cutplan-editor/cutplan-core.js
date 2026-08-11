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
 *
 * ## 內部資料結構
 *
 * Doc = { lines: Line[] }
 * Line(唯讀行)  = { editable:false, raw, term }
 * Line(block 行) = { editable:true, term, mark, id, timecode, prefix, bodyRaw, reason }
 *   - raw/bodyRaw 等欄位重組回去('- [' + mark + '] ' + id + ' [' + timecode + '] '
 *     + prefix + bodyRaw + reason)必須逐 byte 等於原始行內容 —— parseLine 只是
 *     單純把同一個字串切成幾段,天生可逆,不需要額外驗證。
 *   - term = 該行的行尾符號('\n' / '\r\n' / '\r' / '')。檔案最後一行沒有結尾
 *     換行時 term = ''。
 *
 * ## 刪除線(strike)座標系統
 *
 * splitStrikes(bodyRaw) 把 `~~...~~` 標記拆掉,回傳:
 *   - clean:  拿掉所有 `~~` 標記後「看得到」的文字(空白照留,方便 UI 直接
 *     用一般 JS 字串索引對應反白範圍,不像 Python parse_strikes 用去空白座標)
 *   - pieces: 依序排列的 {kind:'plain'|'struck', raw, cleanStart, cleanEnd}
 *     連續片段,raw 部分 plain 片段 raw === clean.slice(cleanStart,cleanEnd)
 *     (identity),struck 片段的 raw = 內文(不含 `~~`),clean 對應同一段內文。
 *     pieces 串接 raw(struck 片段外加 `~~...~~`)可以精確還原 bodyRaw。
 *
 * `~~` 配對規則抄 Python parse_strikes:由左到右找 `~~`,配下一個 `~~` 當
 * 收尾;找不到收尾就當字面文字。這個算法天生不會有「巢狀」——第一個 `~~`
 * 一定跟「下一個」`~~` 配對,配對區間內部不可能再出現 `~~`(出現了就會被
 * 當成收尾提早結束)。因此 pieces 永遠是扁平的一維序列,沒有巢狀樹狀結構
 * 要處理。
 *
 * ## 反白範圍的合法性(邊界 f)
 *
 * classifySelection(bodyRaw, start, end) 回四種結果,不留未定義行為:
 *   - 'add'    — [start,end) 是非空範圍,且不與任何既有 struck 片段相交 →
 *                整段包 `~~`。
 *   - 'remove' — [start,end) 是非空範圍,且跟至少一個既有 struck 片段有交集
 *                (完全包含它、被它完全包含、部分重疊、或橫跨多個 struck
 *                片段皆算)→ **每一個有交集的 struck 片段整段拆掉 `~~`**,
 *                即使選取只蓋到該片段的一部分;選取範圍內原本就是 plain
 *                的文字不受影響。這是 2026-08-11 MM 實測回報的兩個 bug
 *                (「`~~1~~ 23 ~~4~~` 沒辦法批次取消」「現在也沒辦法取消」)
 *                的修正——舊版只有「選取恰好等於單一 struck 片段邊界」才
 *                判定可取消,手機長按拖曳選取幾乎不可能精準對齊那個邊界,
 *                於是幾乎所有真實的「取消刪除線」操作都落回 'invalid'。
 *   - 'empty'  — start === end(合法索引但沒有反白任何文字)。跟 'invalid'
 *                分開列一種狀態,是因為呼叫端(UI)要用它來顯示「請先反白
 *                文字」這種可見提示,而不是跟「索引根本不合法」用同一種
 *                靜默失敗處理。
 *   - 'invalid'— 索引不合法(非整數、負數、end < start、超出 clean 文字
 *                長度)。toggleStrike/applyStrike 對 'empty' 與 'invalid'
 *                都會丟錯(訊息不同),不會靜默猜測使用者想做什麼、也不會
 *                產生巢狀 `~~~~` 這種無法回頭解析的輸出。
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

// 行尾符號(保留 CRLF/LF/CR 與「無結尾換行」四種狀態)。
const EOL_RE = /(\r\n|\r|\n)/;

// ── 行層級解析 ──────────────────────────────────────────────────────────

function parseLine(content, term) {
  const m = BLOCK_LINE_RE.exec(content);
  if (!m) {
    return { editable: false, raw: content, term };
  }
  const [, mark, id, timecode, restFull] = m;
  const sepIdx = restFull.lastIndexOf(REASON_SEP);
  let rest = restFull;
  let reason = '';
  if (sepIdx >= 0) {
    rest = restFull.slice(0, sepIdx);
    reason = restFull.slice(sepIdx);
  }
  const sm = SPEAKER_PREFIX_RE.exec(rest);
  let prefix = '';
  let bodyRaw = rest;
  if (sm) {
    prefix = sm[0];
    bodyRaw = rest.slice(sm[0].length);
  }
  return { editable: true, term, mark, id, timecode, prefix, bodyRaw, reason };
}

function serializeLine(line) {
  if (!line.editable) return line.raw;
  return `- [${line.mark}] ${line.id} [${line.timecode}] ${line.prefix}${line.bodyRaw}${line.reason}`;
}

// ── 文件層級 parse / serialize ────────────────────────────────────────────

function parseCutplan(text) {
  const parts = text.split(EOL_RE);
  const lines = [];
  for (let i = 0; i < parts.length; i += 2) {
    const content = parts[i];
    const term = parts[i + 1] !== undefined ? parts[i + 1] : '';
    lines.push(parseLine(content, term));
  }
  return { lines };
}

function serializeCutplan(doc) {
  return doc.lines.map((l) => serializeLine(l) + l.term).join('');
}

function isEditableLine(doc, lineIndex) {
  const line = doc.lines[lineIndex];
  return !!(line && line.editable);
}

function requireEditableLine(doc, lineIndex, fnName) {
  const line = doc.lines[lineIndex];
  if (!line || !line.editable) {
    throw new Error(
      `cutplan-core: ${fnName} — 第 ${lineIndex} 行不是可編輯的 block 行(唯讀)`,
    );
  }
  return line;
}

// ── 勾選切換 ────────────────────────────────────────────────────────────

function toggleCheckbox(doc, lineIndex) {
  const line = requireEditableLine(doc, lineIndex, 'toggleCheckbox');
  const newMark = line.mark === ' ' ? 'x' : ' ';
  const lines = doc.lines.slice();
  lines[lineIndex] = { ...line, mark: newMark };
  return { lines };
}

// ── 刪除線:解析 ────────────────────────────────────────────────────────

function splitStrikes(bodyRaw) {
  const pieces = [];
  let cleanPos = 0;
  let curPlain = '';
  let curPlainStart = 0;

  function flushPlain() {
    if (curPlain.length) {
      pieces.push({
        kind: 'plain',
        raw: curPlain,
        cleanStart: curPlainStart,
        cleanEnd: curPlainStart + curPlain.length,
      });
      cleanPos += curPlain.length;
    }
    curPlain = '';
    curPlainStart = cleanPos;
  }

  let i = 0;
  while (i < bodyRaw.length) {
    if (bodyRaw.startsWith('~~', i)) {
      const j = bodyRaw.indexOf('~~', i + 2);
      if (j < 0) {
        // 未閉合的 ~~:當字面文字,原樣併入目前的 plain 片段。
        curPlain += '~~';
        i += 2;
        continue;
      }
      const inner = bodyRaw.slice(i + 2, j);
      flushPlain();
      pieces.push({
        kind: 'struck',
        raw: inner,
        cleanStart: cleanPos,
        cleanEnd: cleanPos + inner.length,
      });
      cleanPos += inner.length;
      curPlainStart = cleanPos;
      i = j + 2;
      continue;
    }
    curPlain += bodyRaw[i];
    i += 1;
  }
  flushPlain();

  const clean = pieces.map((p) => p.raw).join('');
  return { clean, pieces };
}

function serializePieces(pieces) {
  return pieces
    .map((p) => (p.kind === 'struck' ? `~~${p.raw}~~` : p.raw))
    .join('');
}

// 重組 pieces 在 clean 座標 [from,to) 範圍內對應的原始 raw 文字(含既有 ~~)。
// 只在呼叫端已保證該範圍內不會「部分切到」某個 struck 片段時使用
// (見 toggleStrike 的 'add' 分支——classifySelection 已擋掉任何與 struck
// 片段部分交疊的選取,所以這裡遇到的 struck 片段一定整段落在範圍內或外)。
function serializePiecesRange(pieces, from, to) {
  let out = '';
  for (const p of pieces) {
    const s = Math.max(p.cleanStart, from);
    const e = Math.min(p.cleanEnd, to);
    if (s >= e) continue;
    const innerStart = s - p.cleanStart;
    const innerEnd = e - p.cleanStart;
    const seg = p.raw.slice(innerStart, innerEnd);
    out += p.kind === 'struck' ? `~~${seg}~~` : seg;
  }
  return out;
}

// ── 刪除線:合法性判斷(邊界 f)──────────────────────────────────────────

function classifySelection(bodyRaw, cleanStart, cleanEnd) {
  if (
    !Number.isInteger(cleanStart) ||
    !Number.isInteger(cleanEnd) ||
    cleanStart < 0 ||
    cleanEnd < cleanStart
  ) {
    return 'invalid';
  }
  const { clean, pieces } = splitStrikes(bodyRaw);
  if (cleanEnd > clean.length) return 'invalid';
  if (cleanEnd === cleanStart) return 'empty';

  const touchesStruck = pieces.some(
    (p) => p.kind === 'struck' && p.cleanStart < cleanEnd && p.cleanEnd > cleanStart,
  );
  return touchesStruck ? 'remove' : 'add';
}

// ── 刪除線:加 / 去(對 bodyRaw 字串直接操作)────────────────────────────

function toggleStrike(bodyRaw, cleanStart, cleanEnd) {
  const mode = classifySelection(bodyRaw, cleanStart, cleanEnd);
  if (mode === 'empty') {
    throw new Error(
      `cutplan-core: toggleStrike — 選取是空的(游標在 ${cleanStart},沒有反白`
      + '任何文字),請先選取要加/去刪除線的範圍',
    );
  }
  if (mode === 'invalid') {
    throw new Error(
      `cutplan-core: toggleStrike — 選取範圍 [${cleanStart},${cleanEnd}) 超出`
      + '內文長度或索引不合法',
    );
  }
  const { clean, pieces } = splitStrikes(bodyRaw);
  if (mode === 'remove') {
    // 選取範圍內「有交集」的既有刪除線片段,整段拆掉 ~~ —— 即使選取只蓋到
    // 該片段的一部分,也整段一起取消(MM 要的「批次取消」語意:
    // `~~1~~ 23 ~~4~~` 全選 → `1 23 4`)。選取範圍內原本就是 plain 的文字
    // 原樣通過,不會被新增標記——這個操作只拆既有的 ~~,不會新增。
    return pieces
      .map((p) => {
        const touched = p.kind === 'struck'
          && p.cleanStart < cleanEnd && p.cleanEnd > cleanStart;
        if (touched) return p.raw; // 拆掉這一段的 ~~,還原成字面文字
        return p.kind === 'struck' ? `~~${p.raw}~~` : p.raw;
      })
      .join('');
  }
  // mode === 'add':classifySelection 已保證 [cleanStart,cleanEnd) 內沒有任何
  // struck 片段(全交疊或不交疊,不會部分切到),所以 before/after 可以直接用
  // serializePiecesRange 保留既有刪除線,中段直接用 clean.slice 當新內文包 ~~。
  const before = serializePiecesRange(pieces, 0, cleanStart);
  const mid = clean.slice(cleanStart, cleanEnd);
  const after = serializePiecesRange(pieces, cleanEnd, clean.length);
  return `${before}~~${mid}~~${after}`;
}

// ── 刪除線:文件層級 wrapper ───────────────────────────────────────────

function applyStrike(doc, lineIndex, cleanStart, cleanEnd) {
  const line = requireEditableLine(doc, lineIndex, 'applyStrike');
  const newBody = toggleStrike(line.bodyRaw, cleanStart, cleanEnd);
  const lines = doc.lines.slice();
  lines[lineIndex] = { ...line, bodyRaw: newBody };
  return { lines };
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
