# doc-to-md 安裝與使用說明

> 把 PDF / EPUB / TXT 檔案轉成乾淨的 Markdown 知識庫，搭配 Claude 自動寫章節摘要

---

## 安裝（只需做一次，約 2 分鐘）

### Step 1：解壓縮

雙擊下載的 `doc-to-md-安裝包.zip`，解壓縮到任意資料夾。

### Step 2：執行安裝程式

#### Mac 用戶

按 `⌘ + 空白鍵` → 輸入 `Terminal` → 按 Enter。

在 Terminal 中輸入 `bash `（注意 bash 後面有一個空格），然後**把 `install.sh` 檔案拖進 Terminal 視窗**，按 Enter：

```
bash /Users/你的名字/Downloads/doc-to-md-安裝包/install.sh
```

> 不用手動打路徑！直接從 Finder 拖檔案進 Terminal 就好。

#### Windows 用戶

在解壓縮的資料夾中，**雙擊 `install.bat`** 即可自動安裝。

> 如果出現「Windows 已保護您的電腦」提示，點「其他資訊」→「仍要執行」。

安裝程式會自動：
- 檢查你的 Python 版本
- 建立獨立的虛擬環境（不影響你電腦其他程式）
- 安裝所有需要的套件
- 驗證安裝是否成功

**如果出現「找不到 Python 3.8+」**：
1. 安裝程式會自動打開 Python 下載頁面
2. 下載安裝 Python 3.12（點網頁上的大黃色按鈕）
3. **Windows 用戶：安裝時務必勾選「Add Python to PATH」**
4. 安裝完成後，**關閉 Terminal / 命令提示字元 重新開啟**
5. 再次執行安裝程式

### Step 3：在 Claude Desktop 加入技能

1. 打開 **Claude Desktop**
2. 點左上方 **Customize** → **Skills** → **+** 號 → **Create Skill** → **Upload a skill**
3. 選擇安裝包裡的 **skill.zip** 上傳
4. 確認 `doc-to-md` 出現在 Skills 列表中

---

## 使用方式

### 方式 A：直接跟 Claude 說（推薦）

在 Claude Desktop 的對話框中輸入：

**Mac：**
```
幫我把這個 PDF 轉成 Markdown：/Users/你的名字/Desktop/書名.pdf
```

**Windows：**
```
幫我把這個 PDF 轉成 Markdown：C:\Users\你的名字\Desktop\書名.pdf
```

Claude 會自動：
1. 執行轉換腳本
2. 讀取輸出的 Markdown
3. 為每個章節填寫摘要和關鍵字
4. 儲存完成的知識庫

### 方式 B：手動在 Terminal 執行

**Mac：**
```bash
~/.doc-to-md/doc-to-md --auto ~/Desktop/書名.pdf -o ~/Desktop/
```

**Windows（PowerShell 或命令提示字元）：**
```
%USERPROFILE%\.doc-to-md\doc-to-md.bat --auto C:\Users\你的名字\Desktop\書名.pdf -o C:\Users\你的名字\Desktop\
```

轉換完成後，把輸出的 `.md` 檔案丟給 Claude 填寫摘要。

---

## 輸出範例

轉換後的 Markdown 長這樣：

```markdown
---
title: "原子習慣"
author: "James Clear"
format: PDF
pages: 320
converted_at: "2026-04-25 17:00"
---

# 原子習慣

## 第1章 微小習慣的驚人力量 ^ch-01

> [!note] 章節摘要
> **摘要**：本章說明為什麼每天進步 1% 的複利效應遠超預期…
> **關鍵字**：複利效應、1% 法則、習慣系統、長期思維

英國自行車隊在 Dave Brailsford 的帶領下，採用了「邊際增益」策略…
```

---

## 常見問題

| 問題 | 解法 |
|------|------|
| 安裝時說「Python 版本太舊」 | 到 python.org 下載 Python 3.12，安裝後重開 Terminal 再試 |
| Windows 顯示「已保護您的電腦」 | 點「其他資訊」→「仍要執行」 |
| Claude 說找不到轉換器 | 確認有執行過安裝程式，Mac 試 `~/.doc-to-md/doc-to-md --help`，Windows 試 `%USERPROFILE%\.doc-to-md\doc-to-md.bat --help` |
| 轉出來是亂碼 | 加上 `--no-convert-chinese` 參數再試一次 |
| PDF 內容是掃描圖片 | 這種 PDF 需要先用 OCR 軟體處理（如 Adobe Acrobat） |
| EPUB 章節是空的 | 可能有 DRM 保護，需先移除 |
| Windows 安裝 Python 後仍找不到 | 確認安裝時有勾選「Add Python to PATH」，若沒勾要重新安裝 |

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

然後在 Claude Desktop → Skills 中移除 `doc-to-md`。

---

## 技術資訊（給老師看的）

| 項目 | Mac | Windows |
|------|-----|---------|
| 安裝位置 | `~/.doc-to-md/` | `%USERPROFILE%\.doc-to-md\` |
| 虛擬環境 | `~/.doc-to-md/venv/` | `%USERPROFILE%\.doc-to-md\venv\` |
| 啟動器 | `~/.doc-to-md/doc-to-md`（bash） | `%USERPROFILE%\.doc-to-md\doc-to-md.bat` |
| Python 需求 | 3.8+（建議 3.12） | 3.8+（建議 3.12） |
| 套件依賴 | PyMuPDF, ebooklib, beautifulsoup4, chardet, opencc-python-reimplemented, lxml | 同左 |
| 支援格式 | PDF, EPUB, TXT | PDF, EPUB, TXT |
| 輸出格式 | Markdown + YAML frontmatter + Obsidian callout blocks | 同左 |
