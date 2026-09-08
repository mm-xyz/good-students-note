# doc-vlm-to-md 安裝與使用說明

> 一條指令，把一份文件**一次**轉成「文字＋圖表」都齊全的 Markdown 知識庫。
> = doc-to-md（文字、章節摘要）＋ vlm-to-md v1.0.2（嵌入圖＋向量圖表頁＋掃描頁視覺）。
> 完全免費、不需 API key、不需本地大模型。**Mac 與 Windows 皆支援。**

---

## 這是什麼？跟之前兩個工具的關係

| | doc-to-md | vlm-to-md | **doc-vlm-to-md（本工具）** |
|---|-----------|-----------|------------------------------|
| 文字（章節摘要） | ✅ | — | ✅ |
| 圖表 / 掃描頁視覺 | — | ✅ | ✅ |
| 向量圖表（麥肯錫 Exhibit 那種） | — | ✅ v1.0.2 | ✅ v1.0.2 |
| 輸出 | 文字 MD | 視覺 MD | **單一合併 MD（一次做完）** |

以前要先跑 doc-to-md、再跑 vlm-to-md、再手動拼。現在一個指令、一份輸出檔搞定。

---

## 安裝（只需做一次，約 2 分鐘）

### Step 1：解壓縮
雙擊下載的 `doc-vlm-to-md-安裝包.zip`，解壓縮到任意資料夾。

### Step 2：執行安裝程式

#### 🍎 Mac 用戶
1. 按 `⌘ + 空白鍵` → 輸入 `Terminal` → 按 Enter。
2. 在 Terminal 輸入 `bash `（**bash 後面有一個空格**），然後**把 `install.sh` 拖進 Terminal 視窗**，按 Enter：
   ```
   bash /Users/你的名字/Downloads/doc-vlm-to-md-installer/install.sh
   ```
   > 不用手打路徑！直接從 Finder 把檔案拖進 Terminal 就好。

#### 🪟 Windows 用戶
在解壓縮的資料夾中，**雙擊 `install.bat`**。
> 若出現「Windows 已保護您的電腦」，點「其他資訊」→「仍要執行」。

安裝程式（兩平台都會）自動：檢查 Python → 建立獨立虛擬環境 → 安裝套件 → 驗證安裝。

**若提示找不到 Python 3.8+**：到 https://www.python.org/downloads/ 下載 Python 3.12。
**Windows 安裝時務必勾選「Add Python to PATH」**，裝完關閉視窗重開，再執行一次。

### Step 3：在 Claude Desktop 加入技能（Mac / Windows 相同）
1. 打開 **Claude Desktop**
2. 左上 **Customize** → **Skills** → **+** → **Create Skill** → **Upload a skill**
3. 上傳安裝包裡的 **skill.zip**
4. 確認 `doc-vlm-to-md` 出現在 Skills 列表中

---

## 使用方式

### 方式 A：直接跟 Claude 說（推薦）

#### 🍎 Mac
```
幫我把這份 PDF 轉成知識庫（文字＋圖表）：/Users/你的名字/Desktop/報告.pdf
```
#### 🪟 Windows
```
幫我把這份 PDF 轉成知識庫（文字＋圖表）：C:\Users\你的名字\Desktop\報告.pdf
```

Claude 會自動：
1. 跑 Phase 1：抽文字（章節結構）＋渲染圖（含向量圖表頁）→ 合併成一份骨架
2. 跑 Phase 2：**一次**把「章節摘要」和「圖像解讀」都填好（掃描頁逐字謄寫）
3. 存成完成的單一知識庫

### 方式 B：手動在 Terminal 跑 Phase 1（省 token / 大檔）

#### 🍎 Mac
```bash
~/.doc-vlm-to-md/doc-vlm-to-md --auto ~/Desktop/報告.pdf -o ~/Desktop/
```
#### 🪟 Windows（PowerShell 或命令提示字元）
```
%USERPROFILE%\.doc-vlm-to-md\doc-vlm-to-md.bat --auto C:\Users\你的名字\Desktop\報告.pdf -o C:\Users\你的名字\Desktop\
```
然後把輸出的 `*_完整知識庫.md` 丟給 Claude：「幫我把章節摘要和圖像解讀都填好」。

---

## 輸出範例（單一檔，文字＋圖一起）

```markdown
---
title: "2024麥肯錫報告"
pages: 68
---

> [!tip] 這份檔案要 Claude 兩件事一次做完
> 1. 把上面每個 `章節摘要` 空格填好；2. 把下面圖表的 `圖像解讀` 空格填好。

# 第1章 …
> [!note] 章節摘要
> **摘要**：（Claude 將填入）
> **關鍵字**：（Claude 將填入）

…（全文）…

---
# 📊 圖表與視覺內容（依頁碼）

## 🖼️ 圖 07 — 第 7 頁（向量圖表）（1241x1754）
![圖 07](assets/2024麥肯錫報告/chart_p007.png)
> [!note] 圖像解讀
> **類型**：（Claude 將填入）
> **內容**：（Claude 將填入）
> **可檢索關鍵字**：（Claude 將填入）
```

---

## 常見問題

| 問題 | 解法 |
|------|------|
| 安裝說「Python 版本太舊」 | 到 python.org 下載 3.12，重開 Terminal / 視窗再試 |
| Windows「已保護您的電腦」 | 點「其他資訊」→「仍要執行」 |
| Windows 裝 Python 後仍找不到 | 安裝時要勾「Add Python to PATH」，沒勾要重裝 |
| Claude 說找不到轉換器 | 確認跑過安裝程式；Mac 試 `~/.doc-vlm-to-md/doc-vlm-to-md --help`，Windows 試 `%USERPROFILE%\.doc-vlm-to-md\doc-vlm-to-md.bat --help` |
| 章節偵測為 0（英文報告） | 章節偵測以中文章節標記為主；文字仍完整保留，只是少了逐章摘要空格 |
| 文字型 PDF 圖表沒抓到 | 調低 `--vector-threshold`（如 30）；或加 `--dpi 200` |
| 轉出來是亂碼 | 加 `--no-convert-chinese` 再試 |
| 圖太多很慢 | Phase 2 分批填，每次 5-10 張 |
| 掃描 PDF 渲染太糊 | 加 `--dpi 200` |

---

## 解除安裝

#### 🍎 Mac
```bash
rm -rf ~/.doc-vlm-to-md
```
#### 🪟 Windows（PowerShell）
```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.doc-vlm-to-md"
```
然後在 Claude Desktop → Skills 移除 `doc-vlm-to-md`。

---

## 技術資訊（給老師看的）

| 項目 | 🍎 Mac | 🪟 Windows |
|------|--------|------------|
| 安裝位置 | `~/.doc-vlm-to-md/` | `%USERPROFILE%\.doc-vlm-to-md\` |
| 虛擬環境 | `~/.doc-vlm-to-md/venv/` | `%USERPROFILE%\.doc-vlm-to-md\venv\` |
| 啟動器 | `~/.doc-vlm-to-md/doc-vlm-to-md`（bash） | `%USERPROFILE%\.doc-vlm-to-md\doc-vlm-to-md.bat` |
| Python 需求 | 3.8+（建議 3.12） | 3.8+（建議 3.12） |
| 主腳本 | `kb_prep.py`（呼叫 `doc_to_md.py` + `vlm_prep.py`） | 同左 |
| 套件依賴 | PyMuPDF, Pillow, ebooklib, beautifulsoup4, chardet, opencc-python-reimplemented, lxml | 同左（pip 跨平台一致） |
| 支援輸入 | PDF, EPUB, TXT, 圖片, 圖片資料夾 | 同左 |
| 輸出 | 單一 `*_完整知識庫.md` + `assets/` + `manifest.json` | 同左 |
| VLM 運算 | Claude Desktop 內建視覺（0 API key） | 同左 |
