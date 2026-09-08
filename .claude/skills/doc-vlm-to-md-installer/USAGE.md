# doc-vlm-to-md 使用說明

> 一次把文件轉成「文字＋圖表」的單一 Markdown 知識庫。Mac / Windows 皆支援。

---

## 運作原理：本地備料，Claude 一次填字

```
┌─────────────────────────────┐     ┌─────────────────────────────┐
│  Phase 1：本地（免費、0 token）│     │  Phase 2：Claude 一次填字     │
│  ─────────────────────────  │     │  ─────────────────────────  │
│  kb_prep.py：               │ ──→ │  Claude Desktop 內建能力：    │
│  • doc_to_md → 抽文字＋章節  │     │  • 章節摘要（讀文字寫）       │
│  • vlm_prep  → 圖表/向量頁/  │     │  • 圖像解讀（用視覺看圖寫）    │
│    掃描頁渲染成 PNG          │     │  • 掃描頁逐字謄寫             │
│  • 合併成單一 *_完整知識庫.md │     │  不需要 API key              │
└─────────────────────────────┘     └─────────────────────────────┘
        Python（本地）                   Claude AI（你現有的訂閱內）
```

真正的「寫摘要 / 看圖說話」是 Claude 在 Phase 2 做的；Phase 1 只是把料準備好。

---

## 三種典型輸入

| 輸入 | 會做什麼 |
|------|---------|
| 文字型 PDF | 抽文字＋章節摘要空格 ＋ 抽嵌入圖表 ＋ **補抓向量圖表頁**（v1.0.2）|
| 掃描／圖像型 PDF | 每頁渲染成圖；Phase 2 逐字謄寫＋描述 |
| EPUB / TXT | 只抽文字（無頁面圖）|
| 圖片 / 圖片資料夾 | 只做視覺（批次描述）|

---

## 方式 A：跟 Claude 說（最簡單）

打開 Claude Desktop：

**🍎 Mac**
```
幫我把這份 PDF 轉成知識庫（文字＋圖表）：/Users/你的名字/Desktop/報告.pdf
```
**🪟 Windows**
```
幫我把這份 PDF 轉成知識庫（文字＋圖表）：C:\Users\你的名字\Desktop\報告.pdf
```

## 方式 B：手動跑 Phase 1（省 token / 大檔）

**🍎 Mac**
```bash
~/.doc-vlm-to-md/doc-vlm-to-md --auto ~/Desktop/報告.pdf -o ~/Desktop/
```
**🪟 Windows**
```
%USERPROFILE%\.doc-vlm-to-md\doc-vlm-to-md.bat --auto C:\Users\你的名字\Desktop\報告.pdf -o C:\Users\你的名字\Desktop\
```
然後把 `*_完整知識庫.md` 給 Claude：「幫我把章節摘要和圖像解讀都填好」。

---

## 常用參數（Mac / Windows 通用）

| 參數 | 效果 | 何時用 |
|------|------|--------|
| `--auto` | 自動處理 | 預設 |
| `--no-convert-chinese` | 不做簡→繁 | 內容本來就繁中／英文 |
| `--dpi 200` | 提高渲染解析度 | 圖糊、字看不清 |
| `--vector-threshold 30` | 降低向量圖表偵測門檻 | 有圖表沒被抓到 |
| `--no-vector-pages` | 關閉向量圖表偵測 | 只要嵌入點陣圖 |
| `-o 目錄` | 指定輸出位置 | — |

---

## 解除安裝

**🍎 Mac**：`rm -rf ~/.doc-vlm-to-md`
**🪟 Windows（PowerShell）**：`Remove-Item -Recurse -Force "$env:USERPROFILE\.doc-vlm-to-md"`
然後在 Claude Desktop → Skills 移除 `doc-vlm-to-md`。
