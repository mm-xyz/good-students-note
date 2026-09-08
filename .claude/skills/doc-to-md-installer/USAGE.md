# doc-to-md 使用說明

> 把 PDF / EPUB / TXT 轉成 Markdown 知識庫，搭配 Claude 自動寫章節摘要

---

## 運作原理

doc-to-md 分成兩個階段，各自負責不同的事：

```
┌─────────────────────────────┐     ┌─────────────────────────────┐
│  Phase 1：本地轉換（免費）    │     │  Phase 2：Claude 摘要        │
│  ─────────────────────────  │     │  ─────────────────────────  │
│  你的電腦上執行              │ ──→ │  Claude Desktop 執行         │
│  不花任何 token              │     │  消耗 Claude token           │
│  不需要網路                  │     │  需要 Claude 帳號            │
│                             │     │                             │
│  • PDF/EPUB/TXT 文字提取     │     │  • 讀取轉好的 Markdown       │
│  • 章節結構偵測              │     │  • 為每個章節寫摘要           │
│  • 簡體→繁體中文轉換         │     │  • 填入關鍵字                │
│  • 頁首頁尾去除              │     │  • 存檔                     │
│  • 斷行合併                  │     │                             │
│  • YAML frontmatter 生成     │     │                             │
│  • Obsidian 錨點標記         │     │                             │
└─────────────────────────────┘     └─────────────────────────────┘
      Python 腳本（本地）                  Claude AI（雲端）
```

**重點：Phase 1 完全免費、離線、不花 token。** 即使你沒有 Claude 帳號，Phase 1 產出的 Markdown 也已經是可用的知識庫——只是少了章節摘要。

---

## 哪些功能在本地運行（不花 token）

| 功能 | 在哪裡執行 | 花 token 嗎 |
|------|-----------|------------|
| PDF 文字提取 | 本地（PyMuPDF） | 不花 |
| EPUB 內容提取 | 本地（ebooklib） | 不花 |
| TXT 編碼偵測 | 本地（chardet） | 不花 |
| 簡體→繁體轉換 | 本地（OpenCC） | 不花 |
| 章節標題偵測 | 本地（正則比對） | 不花 |
| 頁首頁尾去除 | 本地（統計分析） | 不花 |
| 斷行合併 | 本地（規則判斷） | 不花 |
| YAML frontmatter | 本地（自動生成） | 不花 |
| Obsidian 錨點 | 本地（自動編號） | 不花 |
| **章節摘要撰寫** | **Claude 雲端** | **花 token** |
| **關鍵字提取** | **Claude 雲端** | **花 token** |

**結論：90% 的工作在本地完成，只有「寫摘要」這一步用到 Claude。**

---

## Token 消耗估算

Claude 在 Phase 2 需要讀取整份 Markdown 並填寫摘要。消耗量取決於檔案大小：

| 原始檔案大小 | 轉換後 Markdown | Claude token 估算 | 免費帳號夠用嗎 |
|------------|----------------|-------------------|--------------|
| < 1 MB（短文/報告） | ~50 KB | ~20K tokens | 輕鬆夠用 |
| 1-5 MB（一般書籍） | 100-300 KB | 50-100K tokens | 夠用 |
| 5-20 MB（大部頭）| 300 KB - 1 MB | 100-300K tokens | 可能需分次 |
| > 20 MB（超大檔） | > 1 MB | > 300K tokens | 需要 Pro 帳號或分次處理 |

> **省 token 技巧**：Phase 1 轉換完之後，你可以只讓 Claude 處理「你需要的章節」，不必一次處理整本書。

---

## Context 限制與解決方案

### 什麼是 Context 限制？

Claude 每次對話能處理的文字量有上限（稱為 context window）。如果你的 Markdown 檔案太大，Claude 可能無法一次讀完。

### 會遇到限制嗎？

| 情境 | 會遇到嗎 | 說明 |
|------|---------|------|
| 一般書籍（200-400 頁） | 通常不會 | 轉換後約 100-200 KB，Claude 可以處理 |
| 大部頭（500+ 頁） | 可能會 | 轉換後可能超過 300 KB |
| 多本書一次處理 | 一定會 | 請一本一本處理 |

### 如果遇到了怎麼辦？

**方法 1：分段處理（推薦）**

跟 Claude 說：
```
幫我處理這個 Markdown 的前 300 行摘要：/path/to/file.md
```
完成後再說：
```
繼續處理第 301 行到第 600 行
```

**方法 2：只處理需要的章節**

跟 Claude 說：
```
幫我只寫第 3 章到第 5 章的摘要：/path/to/file.md
```

**方法 3：跳過 Phase 2**

Phase 1 的輸出本身就是完整的 Markdown 知識庫，只是沒有摘要。如果你只需要全文搜尋，Phase 1 就夠了。

---

## 使用方式

### 方式 A：跟 Claude 說（最簡單）

打開 Claude Desktop，直接說：

**Mac：**
```
幫我把這個 PDF 轉成 Markdown：/Users/你的名字/Desktop/書名.pdf
```

**Windows：**
```
幫我把這個 PDF 轉成 Markdown：C:\Users\你的名字\Desktop\書名.pdf
```

Claude 會自動執行 Phase 1 + Phase 2。

**其他說法也行：**
- 「轉換這個 EPUB 成知識庫」
- 「把這份 TXT 轉成 Markdown 格式」
- 「幫我處理這本電子書」

### 方式 B：自己跑 Phase 1，Claude 跑 Phase 2

適合大檔案或想省 token 的情境：

**Step 1**：在 Terminal / PowerShell 手動執行轉換

Mac：
```bash
~/.doc-to-md/doc-to-md --auto ~/Desktop/書名.pdf -o ~/Desktop/
```

Windows：
```
%USERPROFILE%\.doc-to-md\doc-to-md.bat --auto C:\Users\你的名字\Desktop\書名.pdf -o C:\Users\你的名字\Desktop\
```

**Step 2**：把轉好的 .md 檔案路徑給 Claude
```
幫我為這個 Markdown 填寫章節摘要：/Users/xxx/Desktop/作者_書名_知識庫.md
```

### 方式 C：純本地使用（完全不花 token）

只跑 Phase 1，不讓 Claude 寫摘要：

Mac：
```bash
~/.doc-to-md/doc-to-md --auto ~/Desktop/書名.pdf -o ~/Desktop/
```

Windows：
```
%USERPROFILE%\.doc-to-md\doc-to-md.bat --auto C:\Users\你的名字\Desktop\書名.pdf -o C:\Users\你的名字\Desktop\
```

輸出的 Markdown 有完整的章節結構、錨點、YAML metadata，只是摘要欄位是空的 placeholder。你可以：
- 直接丟進 Obsidian 使用
- 自己手動填摘要
- 之後有空再讓 Claude 補

---

## 輸出檔案說明

轉換後的 Markdown 結構：

```markdown
---
title: "原子習慣"          ← 書名（自動從 PDF metadata 提取）
author: "James Clear"       ← 作者
format: PDF                 ← 原始格式
pages: 320                  ← 頁數
converted_at: "2026-04-25"  ← 轉換日期
---

# 原子習慣

## 第1章 微小習慣的驚人力量 ^ch-01    ← 章節標題 + Obsidian 錨點

> [!note] 章節摘要                    ← Claude 會填寫這裡
> **摘要**：本章說明每天進步 1% 的複利效應…
> **關鍵字**：複利效應、1% 法則、習慣系統

英國自行車隊在 Dave Brailsford…       ← 原文內容（完整保留）
```

**檔名格式**：`{作者}_{書名}_知識庫.md`

**Obsidian 友善**：
- `^ch-01` 錨點可在 Obsidian 中跨檔案連結
- `> [!note]` callout 在 Obsidian 中會顯示為美觀的摘要卡片
- YAML frontmatter 可被 Obsidian Dataview 查詢

---

## 支援的檔案格式

| 格式 | 副檔名 | 適合 | 注意事項 |
|------|--------|------|---------|
| PDF | `.pdf` | 電子書、報告、論文 | 掃描版 PDF 需先 OCR |
| EPUB | `.epub` | 電子書 | 有 DRM 的需先移除 |
| TXT | `.txt` | 逐字稿、純文字 | 自動偵測編碼 |

### 不支援（需要先轉檔）

| 格式 | 建議做法 |
|------|---------|
| Word (.docx) | 先「另存為 PDF」 |
| Google Docs | 先「下載為 PDF」 |
| 網頁 | 先「列印為 PDF」或用瀏覽器存成 PDF |
| 掃描圖片 PDF | 先用 Adobe Acrobat 或 tesseract 做 OCR |

---

## 常用參數

| 參數 | 效果 | 什麼時候用 |
|------|------|-----------|
| `--auto` | 自動偵測檔案格式 | 永遠都加（預設行為） |
| `--no-convert-chinese` | 不做簡→繁轉換 | 原文就是繁體、或轉換後亂碼時 |
| `-o 目錄` | 指定輸出位置 | 想存到特定資料夾時 |

---

## 常見問題

| 問題 | 原因 | 解法 |
|------|------|------|
| 「Python 3.8+ required」 | Python 太舊 | 到 python.org 下載 3.12 |
| Windows 顯示「已保護您的電腦」 | Windows SmartScreen | 點「其他資訊」→「仍要執行」 |
| 「No module named fitz」 | 沒裝好依賴 | 重跑安裝程式 |
| 轉出來是亂碼 | OpenCC 轉換問題 | 加 `--no-convert-chinese` |
| 「0 sections detected」 | 書的標題格式特殊 | 仍可使用，只是沒有章節分割 |
| Claude 說檔案太大 | 超過 context 限制 | 分段處理（見上方說明） |
| EPUB 章節是空的 | DRM 保護 | 需先移除 DRM |
| PDF 只有圖片沒有文字 | 掃描版 PDF | 需先 OCR |
| Claude 沒有自動執行 | Skill 沒裝好 | 確認 Claude Desktop Skills 列表有 doc-to-md |
| Windows 找不到 Python | 安裝時沒勾 PATH | 重新安裝 Python，勾選「Add Python to PATH」 |

---

## 解除安裝

**Mac：**
```bash
rm -rf ~/.doc-to-md
```

**Windows（PowerShell）：**
```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.doc-to-md"
```

然後在 Claude Desktop → Settings → Skills 中移除 `doc-to-md`。
