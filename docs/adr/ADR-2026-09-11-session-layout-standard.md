# ADR-2026-09-11-session-layout-standard — session 目錄的標準形狀

- 識別碼：ADR-2026-09-11-session-layout-standard
- 日期：2026-09-11
- 狀態：已採納
- 相關：ADR 0011（原始分類）、ADR-2026-09-11-workfiles-move-to-asset、
  ADR-2026-09-11-diff-lives-in-version-dir

## 決策

MM 2026-09-11 畫定的形狀，podcast 線的 session 一律長這樣：

```
sessions/{錄音日}_{名字}/
├── _asset/            管線工作檔（transcript / words / prosody / cutplan / metadata…）
├── _meta/             人看的伴隨檔（chapters.txt / highlights.md / 文案 / 封面）
├── source.<ext>       原始錄音（symlink，保留原副檔名）
└── {版本}/            vN_<時戳>[-AI]/ — mp3 + cutplan 快照 + render.txt + diff/
```

### 名字：`{錄音日}_{人給的名字}`

`session.py new --slug EP19-0-包棟介紹` → `2026-09-07_EP19-0-包棟介紹`。

**日期取錄音日，不是今天。** 今天是「處理日」，跟這集何時錄的無關；更實際的問題
是同一個音檔今天跑跟下週跑會得到不同的 slug，重跑一次就多一個目錄。錄音日先從檔名
解析（錄音機都會帶，如 `2026_0907_1917`、`20260727`、`2026-09-07`），解不出來才退回
檔案 mtime。

解析時 ISO 那條 pattern 必須排在前面，否則 `2026-09-07` 會先被鬆散的
`(20\d{2})[_-]?(\d{2})(\d{2})` 咬掉一半。檔名裡的數字不是合法日期（`2026_1345`）
就跳過該條，不硬解。

**名字沒給就退回音檔檔名**，但錄音機檔名（`2026_0907_1917`）沒有意義——這正是
先前兩次重跑都得手動 `mv` 改名的原因。開新集數一律帶 `--slug`。

### `source.<ext>`：不要寫死小寫

`render_cut.py` 的室噪來源原本寫死 `sdir / "source.wav"`。錄音機給的是 `.WAV`，
macOS 檔名大小寫不敏感所以一直跑得動，**Linux 會直接 FileNotFound**。改用
`find_source_media()`（本來就是為此存在的），保留原副檔名。

## 後果

- 好：目錄名字不再取決於「哪天跑的」，重跑同一集得到同一個 slug。
- 好：不用再手動 `mv` 改名——這條路徑上最後一個靠人記得的動作也沒了。
- 代價：既有 18 個 session 不回頭改名。新舊都是 `{日期}_{名字}` 形狀，只是舊的
  日期可能是處理日而非錄音日，不影響任何程式判斷（slug 只是目錄名）。
