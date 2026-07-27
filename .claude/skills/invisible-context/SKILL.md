---
name: invisible-context
description: 影片 → 帶截圖與停頓標注的 Obsidian 逐字稿＋筆記。場景偵測抽幀、地端 VLM（LM Studio Gemma）篩圖寫圖說、SRT 合流成時間錨點逐字稿，Claude 收尾蒸餾筆記。觸發：「把這場演講做成筆記」「影片轉筆記」「/invisible-context」，或給出影片要筆記時。2026-07-28 起併入 good-students-note（frames 線），原獨立 repo 已封存。
---

# invisible-context — 影片演講 → 看得見畫面的筆記

把藏在**畫面**與**停頓時間點**裡的 invisible context 撈回筆記。
Repo：`~/GithubRepo_mm-xyz/good-students-note/`（frames 線；本檔實體在該 repo，
`~/.claude/skills/invisible-context` 是 symlink）。原 `invisible-context` repo 已於
2026-07-28 併入並封存；歷史 work/ 產物仍在本地舊 repo。

## 觸發條件

- `/invisible-context <影片路徑>`
- 「幫我把這場演講/影片做成筆記」「影片轉逐字稿筆記」
- 下游需求（剪 Podcast / Clips / Reels / 圖卡）需要「文字×畫面×時間」對齊資料時，先跑本管線

## 管線（session 容器＋分段腳本＋Claude 收尾）

工作目錄一律 repo 根（`~/GithubRepo_mm-xyz/good-students-note`）；
重依賴腳本用 `.venv-audio/bin/python`（安裝見 `requirements-audio.txt`）。

```bash
cd ~/GithubRepo_mm-xyz/good-students-note

# Stage 0（推薦入口）：session.py 一條龍 — 本地轉錄（SRT 內建，免外部字幕）＋抽幀
python3 scripts/session.py new "<video.mp4>" --context "講者, 專名" \
    --stop-at phase-a --frames
#   轉錄用 mlx-whisper（--asr local 預設，含 words.json）；--frames 觸發抽幀
#   已有 session 也可單獨跑：.venv-audio/bin/python scripts/frames/extract.py \
#       --session sessions/<slug> [--region 0.016,0.028,0.792,0.82]
#   --region＝固定版面錄影（AI 小聚類）鎖 deck 區：先裁切再偵測，講者移動/台標
#   不觸發抽幀也不進幀；座標記進 manifest，下游 ocr 自動免 PIP 濾/中央裁切

# Stage 2 screen：LM Studio VLM 逐張審（留不留/分類/圖說/畫面文字），逐張落盤可續跑
.venv-audio/bin/python scripts/frames/screen.py "<slug>"     # slug = session 目錄名
#   spot check 先 --limit 5；批量/半夜跑直接全量，單張失敗不炸批

# Stage 2.5 ocr：macOS Vision OCR ＋ 型態分診（MM 2026-07-27 定版流程）
.venv-audio/bin/python scripts/frames/ocr.py "<slug>"
#   OCR → 檢查型態 → 多欄/表格→Markdown 表格｜正文→乾淨直列｜
#   亂碼/slogan/桌面雜訊→remove；字數<40→keep-vlm 留 VLM 描述；
#   ocr-fail→對該幀跑 screen.py --enrich 用 VLM 補
#   同輪掃 QR（Vision 條碼偵測）：解出網址存 screen.qr、compose 渲染 🔗 行

# Stage 2.7 diagram：流程圖/概念圖 → mermaid（kind=chart 幀）
.venv-audio/bin/python scripts/frames/diagram.py "<slug>"

# Stage 2.6 format（可選）：OCR 文字＋畫面進 VLM 重排版面
.venv-audio/bin/python scripts/frames/format_text.py "<slug>"

# Stage 3 compose：SRT＋審過的幀 → Obsidian 逐字稿＋筆記骨架
python3 scripts/frames/compose.py "<slug>" --course "2026_AI訂閱年會小聚"
#   逐字稿插圖＝callout＋畫面文字全文；筆記已填內容自動跳過不覆寫（--force-note 強制）
#   session 若跑過 --prosody：停頓標注改用真實聲學靜音、高昂段落自動標 🔥、
#   有 transcript.speakers.srt 時段落自帶講者名（音訊線圖層，2026-07-28 併入升級）
```

**鐵律：全程地端、零雲端 token**（MM 2026-07-27 拍板）。VLM/LLM 一律走 LM Studio；
**每批跑完把模型從記憶體卸載**：`lms unload google/gemma-4-26b-a4b-qat`（不然 RAM 會爆）。
影片檔案的去留由 MM 決定，管線與 Claude 都不主動刪。

### Stage 4 — Claude 收尾（本 skill 的主體工作）

compose 產出後，Claude 依序做：

1. **抽查插圖**：Read 逐字稿裡 3–5 張插圖位置前後文，確認圖文對得上；VLM 誤留的講者幀/誤寫的圖說直接改 md（或改 manifest 重跑 compose）。
2. **填筆記**：打開 `<folder>_筆記.md`，從逐字稿蒸餾——TL;DR、重點筆記（保留講者原始邏輯與案例，不空泛化）、金句／可剪片段候選（**必附起訖時間碼**，供剪 Podcast/Clips/Reels——剪法走同 repo 的 `--cut` 音訊線）。
3. **停頓判讀**：逐字稿裡的 `⏸ 停頓` 標注，對照前後文判斷是「換頁/操作 demo/現場反應/講者思考」，有意義的在筆記提一句，沒意義的不提。
4. 對外發布的衍生文字（社群貼文等）另走 `/speak-human-tw`，本 skill 不管。

## 輸出結構（Obsidian）

```
$OUTPUT_ROOT/<course>/<speaker>_<title>/
  <speaker>_<title>_逐字稿.md   # 時間錨點段落＋插圖＋圖說＋停頓標注（＋🔥）
  <speaker>_<title>_筆記.md     # TL;DR/重點/金句時間碼/關鍵畫面 gallery，ref 回逐字稿
  attachments/                  # 保留幀，檔名帶 slug 前綴防 vault 撞名
```

## 設定

- `repo/.env`：`OUTPUT_ROOT`（預設 `~/MarsDots/source/course`）、`LM_STUDIO_URL`/`LM_STUDIO_MODEL`、抽幀/停頓/斷段參數（預設值見 `scripts/frames/common.py`）
- `LM_STUDIO_TOKEN` 只住 `mars-cc/.env`（跨專案慣例），腳本自動讀
- 中間產物在 `sessions/<slug>/frames/`（sessions/ gitignored），重跑 compose 不用重抽/重審

## 邊界與 gotchas

- **沒有 SRT？不用管**：session.py 的 `--asr local`（mlx-whisper）直接產 transcript.srt＋words.json，舊「只輸出 .txt」的坑已解。
- **LM Studio 要先開**：`curl -s -H "Authorization: Bearer $LM_STUDIO_TOKEN" $LM_STUDIO_URL/models` 驗活；沒載入視覺模型會 404/400。26B 模型每張約 10–30 秒，整場 20–40 張＝幾分鐘到半小時，適合排半夜跑。
- **抽幀太少/太多**：調 `--scene`（預設 0.06；鏡頭剪接多的實錄影片調高、slide 錄影可更低）；`>2 分鐘無幀`的空窗值得人工看一眼是不是漏了。
- 舊 work/ 產物要搬進 session：`mkdir -p sessions/<slug>/frames && cp -r 舊repo/work/<slug>/. sessions/<slug>/frames/`（manifest 內 `frames/x.jpg` 相對路徑相容）。
- v2 才做：Region 框選監控（鎖 slide 區域變化才截）、Missing Object Detection。
