# ADR-2026-09-14-chapters-by-source-time — 章節時間碼改用來源時間反查

- 識別碼：ADR-2026-09-14-chapters-by-source-time
- 日期：2026-09-14
- 狀態：已採納
- 相關：卡 #991、ADR 0015（SOP）、ADR 0002（render 吃時間區間不吃檔案）

## 脈絡

EP19-0 下了 6 個 `## 章節`，`chapters.txt` 只產出 **3 個相異時間碼**，後四章全部
擠在 `09:22.186`。而且 `[render] chapters.txt: 6 章` 印得一片祥和——它只數行數。

原本的算法：

```python
seg_i = unit_first_seg.get(ch["anchor"])
chap_lines.append(f"{sec_to_ts(dst_starts[seg_i])} {ch['title']}")
```

`anchor` 記的是「章節行之後的下一個 **unit** index」。問題在 unit 的粒度——
註解自己寫得很清楚：「doc 連續且 src 時間連續的 kept blocks 併成一個 speech
unit」。EP19-0 的 382 個 block 只合成 **9 個** speech unit，於是落在同一個 unit
裡的章節全部指向同一個 index、拿到同一個時間碼。

**剪得越乾淨，這個 bug 越嚴重**：保留的段落越連續 → unit 越少 → 越多章節撞在
一起。EP19 第一版剪 22 個 block（12 segments）得到 4 個相異值，第二版只剪 17 個
（9 segments）就退化成 3 個。

先前把它描述成「被 snap 到剪點邊界」不夠精確，真正的問題是**章節的解析度是
unit，而它應該是 block**。

## 決策

繞開 index，改用**來源時間反查**：

1. 建 chapters 時，往後找第一個**保留的正片 block**，記下它的來源起始時間
   （`src`）。章節後面第一個 block 被剪掉是常有的事，所以要找「第一個保留的」。
2. 產 `chapters.txt` 時用 `src_to_dst(src, cut_map)` 換算——`cut_map` 本來就記著
   每段保留區間的 `src_start / src_end / dst_start`。
3. 落在被剪掉的空隙就對到下一段保留的開頭：章節指向的內容被剪掉時，人要的是
   「從這裡開始的內容在成品的哪裡」。

`unit_first_seg` 保留為 fallback，只在「章節後面一個保留 block 都沒有」時才用。

### 順便補上不變量檢查

```
[render] ⚠ 6 章只有 3 個相異時間碼 — 有章節撞在一起,chapters.txt 不能直接用
```

原本 `chapters.txt: N 章` 只數行數，**永遠會綠**。驗收要驗的是「章節數＝相異時間
碼數」，不是「有沒有產出檔案」。

## 驗證

- `test_render_cut.py::TestChapterSrcToDst` 7 項：區間內保持位移、四個章節四個
  相異值、落在剪掉的空隙、前後越界夾住、空 ranges、邊界含首。
- EP19-0 實跑：6 章 → **6 個相異時間碼**，且對得回原始位置（0:59→0:37、
  2:43→2:18、9:14→8:47，差值就是前面剪掉的量）。

## 後果

- 好：章節精度回到 block 級，剪得再乾淨也不會撞。
- 好：不再依賴 segment index，`enforce_monotonic` 丟棄過短片段造成的索引偏移也
  一併免疫。
- 代價：多掃一次 `program` 找下一個保留 block（O(n) per chapter，n 是 block 數，
  對這個量級無感）。
