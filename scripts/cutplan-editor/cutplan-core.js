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

// `## <emoji>` 結構行的標記清單 —— 對齊 scripts/audio/render_cut.py 的
// CONFIG_RE/CUT_RE/MUSIC_RE/INSERT_RE/TEASER_RE/ROOMTONE_RE。這些行是 render
// 的參數,對人審沒有意義,不當章節分隔線顯示(仍原封不動保留在 doc 裡)。
// **新增 render 端結構行時一定要同步加進來**,否則編輯器會把它畫成章節標題
// —— 清單放這裡而不是 Index.html,是因為只有這裡有測試守著。
const STRUCTURE_MARKERS = ['⚙', '✂', '🎵', '➕', '🎬', '🔇'];

// 只有這種 `## 標題` 才算章節分隔線;結構行一律不是。
function isChapterDivider(raw) {
  const m = /^## (.+)$/.exec(raw || '');
  if (!m) return null;
  for (const marker of STRUCTURE_MARKERS) {
    if (m[1].startsWith(marker)) return null;
  }
  return m[1];
}

// speaker 前綴,如 `[KIN] `——對齊 render_cut.py `re.sub(r"^\[[^\]]{1,20}\]\s*", "", body)`
const SPEAKER_PREFIX_RE = /^\[[^\]]{1,20}\]\s*/;

// 行尾人工註記,如 ` ← 二剪:...`——對齊 render_cut.py `body.rsplit(" ← ", 1)`
// (取「最後一個」` ← ` 當分界,跟 Python rsplit 語意一致)。
const REASON_SEP = ' ← ';

// 行尾符號(保留 CRLF/LF/CR 與「無結尾換行」四種狀態)。
const EOL_RE = /(\r\n|\r|\n)/;

// `## ⚙ line=mixdown audio=tracks` 的行尾軌欄 ` ⟦Mars● Sarah○ Kin○⟧`
// (ADR-2026-10-03-audio-tracks-line)——對齊 scripts/audio/tracks_columns.py 的
// COL_RE / CELL_RE。先於 ` ← 理由` 切掉,所以理由與逐字稿都看不到它。
const TRACKS_COL_RE = /\s*⟦([^⟦⟧]*)⟧\s*$/;
const TRACK_ON = '●';
const TRACK_OFF = '○';
const TRACK_CELL_RE = /^(\S+?)([●○])$/;

// ── 行層級解析 ──────────────────────────────────────────────────────────

function parseLine(content, term) {
  const m = BLOCK_LINE_RE.exec(content);
  if (!m) {
    return { editable: false, raw: content, term };
  }
  const [, mark, id, timecode, restAll] = m;
  const cm = TRACKS_COL_RE.exec(restAll);
  const tracksRaw = cm ? restAll.slice(cm.index) : '';
  const restFull = cm ? restAll.slice(0, cm.index) : restAll;
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
  return { editable: true, term, mark, id, timecode, prefix, bodyRaw, reason, tracksRaw };
}

function serializeLine(line) {
  if (!line.editable) return line.raw;
  return `- [${line.mark}] ${line.id} [${line.timecode}] ${line.prefix}${line.bodyRaw}${line.reason}`
    + (line.tracksRaw || '');
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

// ── undo 堆疊(2026-08-11:選取穩定後自動套用,原按鈕改「復原」)──────────
//
// History = { stack: Entry[] },Entry = { doc, lineIndex, start, end }。
// 純資料、不含任何 DOM 參照——doc 是套用「這筆操作之前」的完整文件快照,
// lineIndex/start/end 是這筆操作套用當下的邏輯位置(給 UI 換算畫面上復原
// 按鈕該出現在哪裡用)。用快照而不是存反向操作,是因為 toggleStrike 的
// 'remove' 分支在新語意下一次可能拆好幾段刪除線,反向操作不是單純再呼叫
// 一次就能還原;存快照最簡單也保證復原後逐 byte 跟套用前完全相同。
//
// 全部函式都不會改動傳入的 history/doc(回傳新物件),跟這支檔案其餘函式
// 的不可變風格一致。

function createHistory() {
  return { stack: [] };
}

function pushHistory(history, entry) {
  return { stack: [...history.stack, entry] };
}

function popHistory(history) {
  if (history.stack.length === 0) {
    return { history, entry: null };
  }
  const entry = history.stack[history.stack.length - 1];
  const stack = history.stack.slice(0, -1);
  return { history: { stack }, entry };
}

function canUndo(history) {
  return history.stack.length > 0;
}

// 堆疊頂端(最近一次操作)的邏輯位置,不 pop——UI 拿它算復原按鈕要指向
// 畫面上的哪個位置。堆疊空時回 null。
function peekHistory(history) {
  if (history.stack.length === 0) return null;
  const top = history.stack[history.stack.length - 1];
  const out = { lineIndex: top.lineIndex, start: top.start, end: top.end };
  // 軌欄切換沒有「選取範圍」可以貼復原按鈕,UI 要改貼在那顆開關旁邊
  if (top.kind) {
    out.kind = top.kind;
    out.track = top.track;
  }
  return out;
}

// applyStrike + 自動推入歷史的便利包裝。選取不合法一樣丟錯(沿用
// applyStrike/toggleStrike 既有行為),丟錯時不會推入任何東西——
// 呼叫端傳入的 history 物件本身也不會被動到(不可變)。
function applyStrikeWithHistory(doc, history, lineIndex, cleanStart, cleanEnd) {
  const nextDoc = applyStrike(doc, lineIndex, cleanStart, cleanEnd);
  const nextHistory = pushHistory(history, {
    doc,
    lineIndex,
    start: cleanStart,
    end: cleanEnd,
  });
  return { doc: nextDoc, history: nextHistory };
}

// 復原一步。堆疊空時優雅不做事(undone:false,doc 原封不動回傳同一個
// 物件),不丟錯——「復原到底之後再復原」是使用者正常會做的事,不是
// 呼叫端的程式錯誤,跟 requireEditableLine 那種丟錯的情境不同。
function undo(doc, history) {
  const popped = popHistory(history);
  if (popped.entry === null) {
    return { doc, history: popped.history, undone: false };
  }
  return { doc: popped.entry.doc, history: popped.history, undone: true };
}


// ── 室噪留白(`## 🔇`)──────────────────────────────────────────────────
//
// **這是編輯器裡唯一可新增／可刪除的結構行**,其餘結構行(⚙ ✂ 🎵 ➕ 🎬)維持
// 完全唯讀。這是刻意開的一個口,不是放寬整體護欄 —— 開它的理由:「哪裡該留白」
// 只有人審聽得出來,而且既然人可以插,就必須看得到、也刪得掉,否則在手機上插錯
// 沒有退路。要再開第二個口請先想清楚同樣的兩個條件成不成立。
//
// 行格式對齊 render_cut.py 的 ROOMTONE_RE(`^##\s*🔇\s*(.*)$`,第一個 token
// 要 float 得動;秒數省略時由 `## ⚙ template=` 的樣板補)。
const ROOMTONE_LINE_RE = /^##\s*🔇(\s|$)/;

// 手機上不做自由輸入框 —— 打錯的成本(整段留白長度不對)遠高於少幾個選項。
const ROOMTONE_SECONDS = [0.5, 1.0, 1.5, 2.0];

function isRoomtoneLine(doc, lineIndex) {
  const line = doc.lines[lineIndex];
  return !!(line && !line.editable && ROOMTONE_LINE_RE.test(line.raw || ''));
}

// 文件慣用的行尾。插入的新行要跟著它,不能寫死 '\n' —— 否則一份 CRLF 的節目單
// 會被插出一行落單的 LF。
function dominantTerm(doc) {
  for (const line of doc.lines) {
    if (line.term) return line.term;
  }
  return '\n';
}

function insertRoomtone(doc, lineIndex, seconds) {
  requireEditableLine(doc, lineIndex, 'insertRoomtone');
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) {
    throw new Error(
      `cutplan-core: insertRoomtone — 秒數必須是大於 0 的數字,拿到「${seconds}」`,
    );
  }
  const term = dominantTerm(doc);
  const anchor = doc.lines[lineIndex];
  const lines = doc.lines.slice();
  // 錨點若是最後一行且沒有結尾換行,先把換行補給它,新行才不會黏在同一行上。
  if (!anchor.term) {
    lines[lineIndex] = { ...anchor, term };
  }
  lines.splice(lineIndex + 1, 0, {
    editable: false,
    raw: `## 🔇 ${seconds.toFixed(1)}  留白`,
    term: anchor.term ? anchor.term : '',
  });
  return { ...doc, lines };
}

function removeRoomtone(doc, lineIndex) {
  if (!isRoomtoneLine(doc, lineIndex)) {
    throw new Error(
      `cutplan-core: removeRoomtone — 第 ${lineIndex} 行不是 \`## 🔇\` 室噪行,` +
      '只有室噪行可以在編輯器裡刪除',
    );
  }
  const lines = doc.lines.slice();
  lines.splice(lineIndex, 1);
  return { ...doc, lines };
}

function insertRoomtoneWithHistory(doc, history, lineIndex, seconds) {
  const nextDoc = insertRoomtone(doc, lineIndex, seconds);
  return {
    doc: nextDoc,
    history: pushHistory(history, { doc, lineIndex, start: 0, end: 0 }),
  };
}

function removeRoomtoneWithHistory(doc, history, lineIndex) {
  const nextDoc = removeRoomtone(doc, lineIndex);
  return {
    doc: nextDoc,
    history: pushHistory(history, { doc, lineIndex, start: 0, end: 0 }),
  };
}

// ── 軌欄切換(`## ⚙ … audio=tracks`)──────────────────────────────────
//
// **第二個刻意開的口**,只開「翻 ●/○」這一個動作:欄位有沒有、哪幾軌、順序、
// 格式全部唯讀(那是 tracks_columns.py 產的,改壞了 render 會 FAIL,MM 在手機
// 上不會發現)。開它的理由跟 🔇 同一組條件:①「這段哪幾軌該出聲」只有耳朵
// 聽得出來(串音、笑聲、搶話),預設值只是猜;②翻錯一定看得到(開關就畫在
// 卡片上)、也翻得回來(吃既有的復原)。

function parseTracks(tracksRaw) {
  const m = TRACKS_COL_RE.exec(tracksRaw || '');
  if (!m) return null;
  const out = [];
  const seen = new Set();
  for (const tok of m[1].split(/\s+/).filter(Boolean)) {
    const c = TRACK_CELL_RE.exec(tok);
    if (!c || seen.has(c[1])) return null;
    seen.add(c[1]);
    out.push({ name: c[1], on: c[2] === TRACK_ON });
  }
  return out.length ? out : null;
}

function hasTracksAudio(doc) {
  return doc.lines.some((l) => !l.editable
    && /^##\s*⚙/.test(l.raw || '') && /(^|\s)audio=tracks(\s|$)/.test(l.raw));
}

function toggleTrack(doc, lineIndex, name) {
  const line = requireEditableLine(doc, lineIndex, 'toggleTrack');
  const cells = parseTracks(line.tracksRaw);
  if (!cells) {
    throw new Error(`cutplan-core: toggleTrack — 第 ${lineIndex} 行(${line.id})沒有軌欄`);
  }
  if (!cells.some((c) => c.name === name)) {
    throw new Error(`cutplan-core: toggleTrack — ${line.id} 的軌欄沒有「${name}」這一軌`);
  }
  // 只換那一格的 ●/○ 一個字元,欄位其餘 byte(空白、括號)原樣保留
  const re = new RegExp(`(^|[\\s⟦])(${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})([●○])`);
  const tracksRaw = line.tracksRaw.replace(re, (_all, pre, n, mk) =>
    pre + n + (mk === TRACK_ON ? TRACK_OFF : TRACK_ON));
  const lines = doc.lines.slice();
  lines[lineIndex] = { ...line, tracksRaw };
  return { ...doc, lines };
}

function toggleTrackWithHistory(doc, history, lineIndex, name) {
  const nextDoc = toggleTrack(doc, lineIndex, name);
  return {
    doc: nextDoc,
    history: pushHistory(history, {
      doc, lineIndex, start: 0, end: 0, kind: 'tracks', track: name,
    }),
  };
}

// ── 唯讀護欄(伺服端第二道防線;Code.gs 的 saveCutplan 呼叫它)────────
//
// 逐行比對 oldContent → newContent,回傳第一個「超出允許編輯範圍」的說明字串,
// 沒問題回 null。允許:block 行的 mark、block 內文「拿掉 ~~ 之後的文字不變」、
// (⚙ 有 audio=tracks 時)軌欄只翻 ●/○;其他任何差異一律不合法。
// 從 Code.gs 搬來這裡是為了讓 Node 測試守得到(規則本身沒改)。

function trackSkeleton(tracksRaw) {
  return (tracksRaw || '').replace(/[●○]/g, '?');
}

function findIllegalEdit(oldContent, newContent) {
  const oldDoc = parseCutplan(oldContent);
  const newDoc = parseCutplan(newContent);
  if (oldDoc.lines.length !== newDoc.lines.length) {
    return `行數不一致(${oldDoc.lines.length} → ${newDoc.lines.length})`;
  }
  const tracksOk = hasTracksAudio(oldDoc);
  for (let i = 0; i < oldDoc.lines.length; i++) {
    const a = oldDoc.lines[i];
    const b = newDoc.lines[i];
    const n = i + 1;
    if (a.editable !== b.editable) {
      return `第 ${n} 行的可編輯性改變了(唯讀 ↔ 可編輯)`;
    }
    if (!a.editable) {
      if (a.raw !== b.raw || a.term !== b.term) {
        return `第 ${n} 行是唯讀行,但內容被改動了`;
      }
      continue;
    }
    if (a.id !== b.id || a.timecode !== b.timecode
        || a.prefix !== b.prefix || a.reason !== b.reason || a.term !== b.term) {
      return `第 ${n} 行(${a.id})的 id/時間碼/speaker/理由被改動了`;
    }
    if (a.tracksRaw !== b.tracksRaw) {
      if (!a.tracksRaw || !b.tracksRaw) {
        return `第 ${n} 行(${a.id})的軌欄被新增或刪除了(只能切換 ●/○)`;
      }
      if (!tracksOk) {
        return `第 ${n} 行(${a.id})的軌欄被改動了,但 ⚙ 沒寫 audio=tracks`;
      }
      if (trackSkeleton(a.tracksRaw) !== trackSkeleton(b.tracksRaw)
          || !parseTracks(b.tracksRaw)) {
        return `第 ${n} 行(${a.id})的軌欄軌名/順序/格式被改動了(只能切換 ●/○)`;
      }
    }
    if (a.bodyRaw !== b.bodyRaw) {
      const cleanA = splitStrikes(a.bodyRaw).clean;
      const cleanB = splitStrikes(b.bodyRaw).clean;
      if (cleanA !== cleanB) {
        return `第 ${n} 行(${a.id})的逐字稿文字被改動了(只能加/去刪除線)`;
      }
    }
  }
  return null;
}

// ── 成品時間對照(cutplan.timeline.json,Lifov #1078)──────────────────
//
// render 出片時在 cutplan.md 旁產 timeline:block id → 成品起點秒數陣列,或
// null=成品裡沒有。判定是 render 看**實際保留範圍**算的,不是看勾選——所以
// 「勾了卻沒出現」「取消卻仍出現」在這裡才看得到。timeline 是另一個檔,編輯器
// 只讀不寫,唯讀護欄不受影響。

function parseTimeline(text) {
  if (!text || typeof text !== 'string') return null;
  let d;
  try {
    d = JSON.parse(text);
  } catch (e) {
    return null;
  }
  if (!d || typeof d !== 'object' || !d.blocks || typeof d.blocks !== 'object'
      || Array.isArray(d.blocks)) {
    return null;
  }
  // 每一列都要合格(null 或有限非負數字陣列),任一不合格整份不採信(驗收 TL-2):
  // 部分採信會讓「v3 未出現」這種結論建立在壞資料上,MM 看不出哪幾列是假的
  const okNum = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
  for (const v of Object.values(d.blocks)) {
    if (v !== null && !(Array.isArray(v) && v.every(okNum))) return null;
  }
  // 欄位不存在=舊版 timeline,走舊行為;明寫了就必須合法(null 也不行,TL-2-1)
  const hasFd = Object.prototype.hasOwnProperty.call(d, 'final_duration_secs');
  const fd = d.final_duration_secs;
  if (hasFd && !okNum(fd)) return null;
  const version = String(d.version || '');
  return {
    version,
    versionShort: version.split('_')[0] || version,
    generatedAt: String(d.generated_at || ''),
    finalDuration: hasFd ? fd : null,
    blocks: d.blocks,
  };
}

// loadCutplan 回來的 timeline 字串 → 'absent'(沒有這個檔)/'bad'(有檔但空白
// 或格式不合格)/'ok'。空白檔也是「有檔」,要顯示格式錯誤(TL-2-2),不能當沒檔。
function timelineFileState(text) {
  if (text === null || text === undefined) return 'absent';
  return parseTimeline(text) ? 'ok' : 'bad';
}

// findLineAtTime 的「超出成品長度」結果(驗收 TL-1):UI 要說「成品只有 43:28」,
// 不能默默跳到最後一張卡讓人以為那裡就是。
const OUT_OF_RANGE = 'out-of-range';

// 「12:34」「754」「1:02:03」「12:34.5」→ 秒;格式看不懂回 null(不猜)。
// 前面帶一個負號(「-1」「-0:10」)照樣解析成負數 —— 那是「超出範圍」,不是
// 「看不懂」,UI 要講對原因(驗收 TL-1-1)。
function parseTimeInput(text) {
  let s = String(text == null ? '' : text).trim();
  const neg = s.startsWith('-');
  if (neg) s = s.slice(1);
  if (!/^\d+(?:\.\d+)?$|^\d+:\d{1,2}(?:\.\d+)?$|^\d+:\d{1,2}:\d{1,2}(?:\.\d+)?$/.test(s)) {
    return null;
  }
  const parts = s.split(':').map(Number);
  for (let i = 1; i < parts.length; i++) {
    if (parts[i] >= 60) return null;
  }
  const v = parts.reduce((acc, x) => acc * 60 + x, 0);
  return neg ? -v : v;
}

function formatTime(secs) {
  const t = Math.floor(Math.max(0, secs));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = String(t % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

// 卡片上的小字與不一致判定;timeline 沒有這一列(或沒有 timeline)回 null。
function timelineStatus(line, timeline) {
  if (!timeline || !line || !line.editable) return null;
  if (!Object.prototype.hasOwnProperty.call(timeline.blocks, line.id)) return null;
  const times = timeline.blocks[line.id];
  const present = Array.isArray(times) && times.length > 0;
  const checked = line.mark !== ' ';
  return {
    times: present ? times : null,
    label: present
      ? `▶ ${timeline.versionShort} ${times.map(formatTime).join(' · ')}`
      : `${timeline.versionShort} 未出現`,
    mismatch: checked !== present,
  };
}

// 成品時間 → 哪一張卡:起點 ≤ 該時間的最後一個 block。同一 id 出現在 🎬 集錦
// 與正文時,集錦的時間(成品前段)對到文件裡第一次出現的那列(集錦區),其餘
// 時間對到最後一次出現的那列(正文)。
function findLineAtTime(doc, timeline, secs) {
  if (!timeline || typeof secs !== 'number' || Number.isNaN(secs)) return null;
  if (secs < 0) return OUT_OF_RANGE;          // 負數永遠超出(TL-1-1)
  if (timeline.finalDuration !== null && timeline.finalDuration !== undefined
      && secs > timeline.finalDuration) {
    return OUT_OF_RANGE;
  }
  let best = null;
  for (const [id, times] of Object.entries(timeline.blocks)) {
    if (!Array.isArray(times)) continue;
    times.forEach((t, k) => {
      if (t <= secs && (!best || t > best.t)) best = { t, id, k, n: times.length };
    });
  }
  if (!best) return null;
  const lines = [];
  doc.lines.forEach((l, i) => { if (l.editable && l.id === best.id) lines.push(i); });
  if (!lines.length) return null;
  if (lines.length > 1 && best.n > 1) {
    return best.k === 0 ? lines[0] : lines[lines.length - 1];
  }
  return lines[lines.length - 1];
}

// 「B0059」「b59」「B0101+B0106.mp3」(改動試聽檔名直接貼)→ 那一列的 index。
// 要有字母前綴:純數字是成品時間(findLineAtTime 的事)。同一個 ID 出現多次
// (🎬 集錦在前、正片在後)跳最後一個=正片。找不到回 null。
function findLineById(doc, text) {
  const m = /^\s*([A-Za-z]{1,2})0*(\d+)/.exec(String(text == null ? '' : text));
  if (!m) return null;
  const want = m[1].toUpperCase() + ':' + Number(m[2]);
  let hit = null;
  doc.lines.forEach((l, i) => {
    const k = l.editable && l.id && /^([A-Z]{1,2})0*(\d+)$/.exec(l.id);
    if (k && k[1] + ':' + Number(k[2]) === want) hit = i;
  });
  return hit;
}

const api = {
  parseCutplan,
  parseTimeline,
  parseTimeInput,
  formatTime,
  timelineStatus,
  findLineAtTime,
  findLineById,
  OUT_OF_RANGE,
  timelineFileState,
  serializeCutplan,
  toggleCheckbox,
  splitStrikes,
  classifySelection,
  toggleStrike,
  applyStrike,
  isEditableLine,
  createHistory,
  pushHistory,
  popHistory,
  canUndo,
  peekHistory,
  applyStrikeWithHistory,
  undo,
  BLOCK_LINE_RE,
  STRUCTURE_MARKERS,
  isChapterDivider,
  ROOMTONE_SECONDS,
  isRoomtoneLine,
  insertRoomtone,
  removeRoomtone,
  insertRoomtoneWithHistory,
  removeRoomtoneWithHistory,
  SPEAKER_PREFIX_RE,
  REASON_SEP,
  TRACKS_COL_RE,
  parseTracks,
  hasTracksAudio,
  toggleTrack,
  toggleTrackWithHistory,
  findIllegalEdit,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = api;
}
if (typeof window !== 'undefined') {
  window.CutplanCore = api;
}
