# CHANGELOG

> 剪輯線（feat/podcast-cut）的功能史。決策的「為什麼」住 `docs/adr/`，這裡只記「什麼時候做了什麼」。

## 2026-08-11

- **分軌剪輯線上線**（ADR 0014 路線分流、ADR 0015 SOP）：`tracks/` 存在就從三軌
  各自 gate → speech bus 混音出片，沒有就照舊從 `source.wav` 剪。動機是「別人講話時
  的附和聲剪不掉」——混音逐字稿看不到、時間軸上也切不開。新增
  `pertrack_blocks.py`（canonical 文字＋逐軌波形歸屬）、`pertrack_cells.py`
  （atomic cells，同軌矛盾勾選直接 FAIL）、`pertrack_attrib.py`（串音校準／CFAR
  附和偵測）、`pertrack_render.py`（activity mask 混音鏈）。音檔線測試 220 → 320。
  - 對照組實驗：同一規格派 Opus 5 與 codex luna(max) 各實作一版，取 Opus 版
    （luna 版人審資產丟 48 處刪除線、D5 activity mask 未實作、長度短 162 秒）。
  - **逐軌 ASR 不可當出片文字**：Mars 直錄軌在 295–323 秒陷入「嘗」重複迴圈，
    而 Sarah／KIN 軌的**串音** ASR 反而轉出了 Mars 原句。canonical 文字一律取
    混音逐字稿，逐軌波形只決定歸屬與靜音。
  - 修：支配判定缺噪聲底閘，三軌都在底噪時仍硬選贏家，KIN 的麥在「前陣子」
    中間被關掉 0.5 秒（MM 實聽 0:37 疊音）。改成「三軌都低於各自底噪就不換手」。

- **刪除線不再被邊界微調還原**（ADR 0016）：原順序 subtract → refine → word_guard
  會讓 word_guard 把刻意製造的切點推回字外，等於把要刪的字塞回去。EP16 實測
  **146 處刪除線只有 27% 生效**、30.1 秒該消失卻還在，**過去每一集都中**。改成
  人審點名的剪除（刪除線／停頓收緊／`## ✂`）一律在邊界微調之後才套 → 失效率 4%。

- **段落重疊修掉**：`snap`／`refine`／`word_guard` 逐 unit 各自算，前段尾巴與後段
  開頭會被推到重疊，同一段音訊播兩次。混音線 v7 有 0.48s、分軌線放大到 44 處
  共 15.6s。新增 `enforce_monotonic`，兩條線一起受惠。抓到它的是本地 whisper
  重轉聽到「任務」念了兩次，不是任何數字檢查。

- **`## ➕` 外部補錄插入**（ADR 0011）：事後補錄的音檔不在 `source.wav` 時間軸上，
  以 `## ➕ <檔案> gain=auto` 插進 cutplan。`gain=auto` 用**插入點前後各 15s 的
  保留語音**當基準對齊電平（用全片平均會對錯：EP16 插入點前後 30s 就差 3.8dB）。
  後續改走 `insert_prepare.py` 逐句 S block，補錄也能像正片一樣勾選與字級精剪。

- **P 停頓列**（`pause_rows.py`）：G 列只涵蓋 block 之間 ≥2s 的空白，自動收緊處理
  的是 block 內 >0.9s 的靜音——中間那段沒有列可勾，人審想留也沒得留。P 列復用
  既有 G 列機制（不勾＝剪、勾＝保留原聲）。

- **diff 片段**（`diff_clips.py`）：比對前後兩份 cutplan 的人審決定，只切出改動處
  ±5s 給人聽，不產文檔。一輪人審從重聽 30 分鐘降到幾分鐘。

- **session 目錄分類**（ADR 0011）：`raw/`／`tracks/`／`_meta/`／`_bak/`／
  `vNN_<時戳>_<標籤>/`，管線工作檔留在 session 根（約 15 處硬編碼）。
  工具 `tidy_session.py`，EP15／16／17 已套用。

- **`cut.py` 補完一行指令**：local 端也建版本目錄（原本只有 Drive 有，每次都要人工
  `mkdir`）、版本號取兩邊最大號 +1（避免分叉）、`--plan` 直通（分軌線可用）、
  `render.txt` 記錄走哪條線。cutplan 改住集數資料夾根，不進 `_meta/`。

- **block 粒度上限**（`--max-secs`，預設 1.2s）：切點只認標點與 ≥0.5s 停頓時，
  遇到連珠炮講話切不開（EP16 最長一個 27.9s，人審在那 27.9 秒裡沒有勾選粒度）。
  改用貪婪填滿＋最大字間空隙切點。⚠️ 目前偏碎（EP16 分軌線 69% 的行 ≤4 字），
  待調。

- **whisper artifact 守門**：同字重複 ≥4／U+FFFD／整句只有 1–2 種字元 → 丟棄。
  目前只在分軌線，混音線尚未接（EP16 B0068 的 20.4 秒「反而反而反而…」仍在）。

## 2026-08-01

- **新開知識點線 `/good-student`**（ADR 0009）：把 `cleaned.md`（或任意乾淨長文本）
  切成概念級、好記憶的知識點——一概念一檔、300–800 字 prose、🎯 視角類比＋一句鉤＋
  它回答的問題。切法承襲 mars-cc `astro-chunking`（domain 結構＝chunk 邊界、
  受控 frontmatter、parent-child source 指回），記憶點承襲 Step 4 好學生筆記的
  視角置入；**蒸餾產物，與零省略產線平行不取代**。鐵門：啟動必談切分軸/視角/
  輸出位置（視角每次重談）、auto mode 也要先試切 5 張驗收才可批量。
  skill 住 `.claude/skills/good-student/`，另 symlink 進 `~/.claude/skills/` 跨 repo 可用。
  - 同日補訂（MM）：**深度落點**（每張卡以使用者現有程度為基準恰好跨一步）、
    **檔名雙語**（`<slug>_<繁中>.md`）、**總覽 canvas**（`scripts/build_canvas.py`
    確定性生成 `_map.canvas` 矩陣體系地圖；試切 gate 出骨架＋佔位版當驗收介面，
    實測白瑜 523 檔 → 380 卡＋55 佔位、路徑全數有效）、**RAG 契約**
    （embedding 單位＝首段＋正文；🎯/相關行固定格式可剝離；受控欄位＝filter）。

## 2026-07-31

- **統一輸入管線:文件(PDF/EPUB/TXT)接進 `session.py new`,音檔線零改動**
  （#605-#608,ADR 0008）:`scripts/session.py new <file>` 現按副檔名自動分流——
  音檔/影片走既有轉錄線,`.pdf`/`.epub`/`.txt` 走新的文件抽取線,兩線最後
  匯流到共用的理解層(enhance/notes)。
  - `scripts/doc/extract.py`(#605):Phase-1 確定性抽取器(0 token,不呼叫
    LLM)——PDF 用 PyMuPDF、EPUB 用 ebooklib、TXT 用 chardet 偵編碼,統一輸出
    簡→繁(s2twp)結構化 markdown,直接當 `cleaned.md`,**跳過** transcribe/
    phase-a/phase-b(ASR 專屬清理對已乾淨文件無意義)。內建連字號斷字合併、
    頁碼/頁眉頁腳雜訊清除、編號清單不誤判章節(改用 PDF 書籤 TOC 判斷子章節)。
  - **EPUB 章名優先用 toc/nav**(#614):章節標題來源改先查 `book.toc` 對應
    spine 檔的真實章名,對不到才退回內文 h1-h3、再退回「第N章」——比直接猜
    內文標題可靠,同時過濾 `linear="no"` 的非閱讀順序輔助頁(目錄/勘誤頁)。
  - **PDF 直排(vertical text)偵測與重排**(#615):逐頁判定多數 text line
    是否 y 分量 dominant 來認定直排頁,重排順序改「欄序右→左、欄內上→下」;
    真實掃描/OCR PDF 常把直排每個字拆成獨立 line、也常有近似重複的 OCR pass
    造成逐字交錯,補上細分子欄(緊 eps)+ 同視覺位置 slot 收斂 + 高相似度文字
    合併三道處理,把「一字一行」與「逐字交錯亂碼」都收斂回可讀連續文本。
  - `scripts/doc/figures.py`(#606):PDF 圖表確定性渲染器(0 token,不呼叫
    LLM/VLM)——內嵌點陣圖(四維篩選:寬/高/面積/長寬比,擋橫幅/分隔線裝飾圖)、
    向量圖表頁(單頁向量物件數 ≥ 門檻整頁渲染)、掃描頁(首 10 頁抽樣平均文字密度過低)
    三種來源渲染成 PNG 落 `<session>/images/`,manifest 寫 `doc_figures.json`,
    續接既有 `describe_images.py`(看圖描述,LLM 那層)。
  - `scripts/session.py` 分流(#607):`DOC_EXTS = {.pdf, .epub, .txt}` 判斷
    走哪條線;文件線 `--vlm`(僅 pdf 有意義)先跑 `figures.py`,再複用既有
    `--images` 的 `describe_images.py`/`insert_images.py` marker 呼叫路徑,
    不重造圖片理解邏輯;`metadata.json` 新增 `source_type`(`doc`/`audio`)與
    `doc_extraction` stats 欄位。
  - 重依賴(fitz/ebooklib/lxml/chardet/opencc)隔離在 `.venv-doc`
    （`requirements-doc.txt`）,與音訊線 `.venv-audio` 同構,主管線
    「`pip install requests` 就能跑」的輕量承諾不變。
  - 決策脈絡、純掃描書(無文字層)OCR 路線暫列 backlog(#616,無現成免費
    OCR 方案)見 `docs/adr/0008-input-adapter-doc-line.md`。
- **回歸測試套件整合文件線**（#608）:`scripts/tests/run_all.sh` 新增獨立段落,
  用 `.venv-doc/bin/python -m pytest` 跑 `test_doc_extract`/`test_doc_figures`/
  `test_session_doc_line` 三份(31 tests);`.venv-doc` 不存在則印安裝提示並
  跳過,不讓整支 run_all 失敗。音檔線既有測試迴圈零改動。CLAUDE.md 新增
  原則 12(輸入轉接器),`.claude/skills/good-student-notes/SKILL.md` 輸入
  心智模型從「媒體檔案」擴到「媒體或文件檔案」。`.gitignore` 修正
  `.venv-doc`/`.venv-audio` 去尾斜線,讓 worktree 內的 symlink venv 也被
  正確 ignore(尾斜線只匹配目錄不匹配 symlink)。

## 2026-07-30

- **音訊剪輯線補上全套回歸測試（新增 130 tests，行為鎖定）**：MM 拍板「把現在所有
  的行為都補上 test，不想每次回來處理被改壞的問題」。新增 `scripts/tests/`
  六份：`test_srt_utils`（斷句核心 split_words_to_phrases 的標點/停頓切點＋
  零長度 artifact 併組、join_words 英數補空格、SRT roundtrip）、`test_cutplan`
  （merge_gap=0 一 cue 一 block、G 列門檻、refine_gaps 合成 wav burst 拆分、
  prepare e2e 三產物）、`test_resegment_migrate`（短句重切首尾沿用原 cue 邊界、
  備份不覆蓋、刪除線字元流對齊移植/跨 block 拆/寧缺勿錯丟棄）、
  `test_diarize_align`（換手切開、英文字 junction 併回、from-tracks/apply-map
  e2e）、`test_render_cut`（防幻覺驗證五 FAIL 路徑、剪距運算全函式、BGM 包絡
  keypoints、⚙ config 覆蓋、dry-run e2e）、`test_copy_prompt_build`（cut_map
  時間換算、🎬 排除、同講者合併）＋ `test_prosody`（zscore 分軌正規化）。
  `run_all.sh` 一鍵全跑（test_prosody 自動用 .venv-audio）；模型呼叫與 ffmpeg
  出片不在範圍。CLAUDE.md 新增「改 scripts/audio/ 改完必跑」規則。
  坑：合成 wav 不能全靜音——谷底偵測在零能量下漂進相鄰字、word_guard 外推
  會把字級精剪 merge 回去,fixture 要在字界留 20ms 靜音縫才是真實剪點形狀。

## 2026-07-29

- **cutplan 斷句粒度改 EP15 式短句**（MM 拍板，SPEC：`docs/design/
  2026-07-29_phrase-level-cutplan-spec.md`）：EP17 NG 口白+正式開場黏同一
  block 的根因＝mlx-whisper 長 segment（8–10s 帶標點），換手/靜音兩種切點
  救不了「同人無停頓」段落。`srt_utils.split_words_to_phrases()` 用 word 級
  時間軸按「字尾標點或停頓 ≥0.5s」機械重切（零 LLM）；`transcribe_local.py`
  內建（EP18 起預設）；`resegment_srt.py` 給既有 session 事後補切；
  `migrate_marks.py` 把舊 cutplan 的 ~~刪除線~~ difflib 字元流對齊移植
  （EP16 手工流程固化）。EP17 實跑：198 cues→779 blocks、112 spans 移植
  丟棄 0、dry-run PASS。坑：零長度 artifact word 不能自成短句（會產 0 長度
  cue＋下游孤兒），已併回鄰組。
- **分軌對齊正式入 pipeline（`diarize.py --from-tracks`）＋多人大段切開**：
  EP16 開場 4.5 分鐘 whisper 在搶話/jingle 區輸出 30s 視窗大段（一段吞三人、
  逐段貼標必錯一半），cutplan 開頭整片粗塊。原 pipeline ③「分軌 turns→SRT」
  是 subagent 臨場代碼、只貼標不切段；現固化進 diarize.py——words.json 逐字
  對 speakers.json（每軌 VAD ground truth）歸屬講者，段內有換手就在換手處
  切開（sub-cue 文字由 words 重建，與 render 字級對齊同源，ADR 0005）；
  單人 cue 原文零改動。EP16 實測 867→948 blocks，64 段多人大段拆成逐句換手；
  Gemma 156 段刪除線以字元流 difflib 對齊遷移（丟 1 段），render --dry-run
  驗證鏈全通。舊檔留 `*.bak-30sblocks`。
- **BGM 二段式 ducking 包絡**（ADR 0006，CLAUDE.md 原則 11）：fadein＝疊人聲
  0→duck＋人聲結束 rise 秒 duck→solo；fadeout＝predrop 2 秒 solo→duck＋人聲進場後
  fadeout 秒 duck→0。人聲位置從時間軸推導，`volume` expr 逐 frame 內插取代 afade；
  全域旋鈕 `--bgm-duck/solo/predrop/rise`。總原則入 CLAUDE.md：全程音量收放
  必須是舒服的遞增遞減，不得跳變。
- **實聽微調（同日 Amendment）**：duck/solo 預設 0.4/0.7 → **0.15/0.55**
  （振幅乘數，人耳對數）、rise 1.0→1.5s；ramp 曲線線性 → **smoothstep**。
- **共用素材庫前綴解析**：`shared-material/水星貓的生活實驗室_v2/`，素材命名
  `opening_*/break_*/ending_*`，cutplan 寫 `## 🎵 opening` 前綴對了就中；
  歧義 FAIL 列候選；`--material-dir` 可換庫。
- **cutplan ⚙ config 區**：cutplan.md 頂部 `## ⚙ key=value ...`＝該集 render 參數
  真相源（覆蓋 CLI/預設，未知鍵 FAIL）；cutplan.py 模板固定產出。集錦間隔預設
  1.0s→**0.5s**（MM 實聽「有點尬」）。
- **集數文案 prompt 模板入共用素材庫**：`shared-material/<節目版本>/prompt_集數文案.md`
  （profile/格式/語氣規範），session 只放 `copy_material.md` 素材，`{{集數}}`/`{{素材}}`
  置換組裝；實跑等 MM 驗收 final_cut 後派 agy＋codex。
- **文案模板 v2**：改以 MM 的 `info_prompt.md` 為基底（SEO 寫手/🧪 實驗進度/
  💡 觀察筆記/關於我們），雙槽 `{{素材}}`＋`{{逐字稿}}`；組裝固化成
  `scripts/audio/copy_prompt_build.py`（逐字稿逐段標 final-cut 時間、集錦去重）。
- **多軌 ingest v1**（#565，ADR 0003 落地，reviewer PASS）：`scripts/audio/
  ingest_tracks.py`——tracks/ 偵測、mixdown 產 source.wav＋audio16k.wav、每軌
  能量 VAD 產 speakers.json（schema 與 diarize 相容）；13 tests（synthetic fixture）。
- **Gemma 贅字標記實驗**（#569 第一輪）：`scripts/audio/fillers_local.py`——
  LM Studio 分 chunk 標 ~~贅字~~，逐 block 機械驗證（去標記後必須逐字相等）。
  EP15 實跑：1535 block/393 標記/27 丟棄/39 分（gemma-4-e4b）；26b QAT reasoning
  隨輸入行數爆炸不可用（坑記 script 檔頭）。proposal 檔等 MM 審，不動 cutplan 本體。
- **EP16 首航實測修正**（ADR 0003 Amendment）：ingest 大寫 `.WAV` 相容、
  amix `normalize=0`＋alimiter（音量 -43→-33 LUFS）、**mixdown 改 stereo**
  （檔名數字前綴控聲像排位，等功率 pan ±0.3；mono 聽感悶）。
- **cutplan G 空白列**（ADR 0007）：block 間 ≥2s 空白（打板/笑/環境音）列成
  `G` 列預設不勾＝照舊剪掉、勾選＝保留原聲（render 當 raw unit 不做任何平滑）；
  `cutplan.py add-gaps` 對既有 session 冪等補列。
- **G 列能量標注＋burst 拆分**（ADR 0007 Amendment）：讀 audio16k 量能量——
  真靜音單列標「靜音」；有聲小段（>-40dB、≥0.12s、pad 0.15s）各自獨立成
  「🔊 聲音事件」列附峰值 dB，勾選＝只保留那聲笑/打板不連帶死空白。
  EP16 實測 13 列＝8 聲音事件＋5 靜音。
- **EP16 首航**：分軌管線全線打通——錄音室檔→含 Gemma 154 處預標的可人審
  cutplan≈33 分鐘機器時間（雲端 token 0），時間帳見 session 的 pipeline_run.md；
  stereo 預聽 A/B 後拍板直接出 stereo。
- **設計文件兩份**（#570/#571，等 MM 拍板）：`docs/design/2026-07-29_ep-visual-assets.md`
  （三平台封面規格/引擎比較/筆記圖流程/觸發機制）、`2026-07-29_post-approval-copy-
  automation.md`（驗收後文案自動化全流程）；EP15 筆記圖 prototype 與三版文案
  （Jarvis/agy/codex）落 session 待比稿。

- **音樂 overlay 疊接架構**（`559b244`，ADR 0004）：🎵 退出 concat 鏈改 amix 疊接，
  `lead`/`tail` 宣告進出場重疊、`start`/`end` 可選取音檔區間；片尾音樂自然收尾。
  EP15 實配：Opening（集錦尾 5s 淡入、尾 8s 淡出、末 5s 主音軌進場）＋間奏＋片尾（末 10s 淡入）。
- **🎬 集錦節奏改版**（`73f26a0`→`559b244`）：unit 級淡出淡入烘進 segment
  （`--clip-fade-in/out` 預設 2s）、集錦間隔 `--clip-gap` 預設 1s。
- **人聲動態均衡**（`559b244`）：`dynaudnorm`（m=4:p=0.9）插在 loudnorm 前，
  三人同軌音量拉齊；EP15 實測 -18.4 → -16.6 LUFS。
- **剪點防護鏈**（`73f26a0`＋`559b244`，ADR 0005）：>3s 異常 word 丟棄
  （EP15「好」16.6s 造成 17s 重複音訊）；unit 邊界用 words.json 字級對齊
  （「惜嗎」句尾被切）；exact 邊界跳過 snap/谷底/word_guard 外推。

## 2026-07-28

- **剪輯線三份 ADR**（`cbd8e08`）：cutplan 人審真相源／時間軸抽象／多軌單一 pipeline。
- **節目結構 v1**（`30ebffd`）：🎬 精華集錦（block 可複製重排）＋🎵 音樂床，
  播放順序＝cutplan.md 文件行序。
- **frames 線併入**（`e1045f9`＋`2486188`）：invisible-context 抽幀/VLM 篩圖/OCR/compose
  移植進 session 容器，`/invisible-context` skill 改指本 repo。
- **README 補完整流程**（`af5297f`＋`6d63bff`）：前置/開 session/命名/人審/出片/旋鈕。

## 2026-07-27

- **字級精剪＋停頓收緊**（`5607ac3`）：cutplan 的 `~~刪除線~~` 走 words.json 精準剪字；
  >1.5s 停頓自動收緊到 0.6s（word 邊界保護 `35ad5a4`）。
- **波形平滑接縫**（`a249e3b`）：剪點滑到能量谷底＋acrossfade 三角交疊。
- **出片預設 mp3**（`8b961ca`）：libmp3lame 192k，副檔名決定編碼。
- **剪輯 pipeline 立線**（diarize/prosody/cutplan/render 四件套）：SRT 轉錄＋說話人分離
  →韻律分析→cutplan.md 人審→ffmpeg 全自動出片。
