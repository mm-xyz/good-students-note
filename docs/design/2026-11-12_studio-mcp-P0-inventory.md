> 來源：卡 #1322，in-process Explore 於 2026-11-12 產出的唯讀盤點，原文照錄。去留欄的「待定」已由 `2026-11-12_studio-mcp-architecture.md` §14 逐項定案；兩者不一致時以 §14 為準。

# P0 盤點回報（卡 #1322）

**先講一件盤點途中發生的事。** 開工時 `git status --short` 有 3 處未提交：`M .claude/skills/good-student/SKILL.md`、`M scripts/frames/extract.py`、`?? scripts/audio/restore_punct_txt.py`。盤點進行到一半，這三處在 2026-11-12 23:38:09 被別的 session 提交了：`2dc61eb`（frames/extract.py）、`b5d98c5`（新增 restore_punct_txt.py）、`236c4de`（good-student SKILL.md）。現在 `git status` 是乾淨的。設計文件 §10 第一個 Major 風險已經解除；`restore_punct_txt.py` 也從「未追蹤」變成有追蹤，所以下表的計數以現在的 HEAD 為準。

## 數字核對（驗收條件）

- `git ls-files scripts SRT dict prompts shared-material .claude/skills docs | wc -l` 得 **305**（scripts 176、SRT 7、dict 8、prompts 4、shared-material 2、.claude/skills 44、docs 64）。
- repo 根目錄的檔：15 個（`git ls-files | awk -F/ 'NF==1'`）。
- `.agent/`、`.gemini/`：4 個（根目錄的 agent 設定，我併進範圍）。
- good-students-note 逐檔合計 **324 列**。
- 目錄層級另 4 列：web/（18 檔）、assets/（31 檔）、sessions/（未追蹤）、atoms/（未追蹤）。324 + 18 + 31 = 373，等於 `git ls-files | wc -l`。
- ai-reels：`git ls-files scripts template docs | wc -l` 得 **17**。
- A 表總列數 = 324 + 4 + 17 = **345**。

## A. 逐檔去留表

家族名稱照 §7。「被誰呼叫」一欄只列 grep 到的實際呼叫（import、subprocess、shell、skill 指令、測試）；只在文件裡被提到的，標「（文件）」。

```tsv
路徑	行數	家族	被誰呼叫或匯入	有無對應測試	去留	理由
scripts/audio/audacity_labels.py	548	剪輯核心	podcast-cut SKILL.md:75,78	有 test_audacity_labels	搬	剪輯線主體
scripts/audio/batch_llmnode.sh	63	轉錄與標點	good-student SKILL.md:282；本身呼叫 transcribe_llmnode.py	間接（test_check_punct_density:132 鎖它的輸出格式）	搬	要保留的 skill 用到
scripts/audio/change_preview.py	164	剪輯核心	cut.py:37 import	有 test_change_preview	搬	cut.py 依賴
scripts/audio/check_punct_density.py	161	轉錄與標點	good-student SKILL.md:277	有 test_check_punct_density	搬	skill＋測試
scripts/audio/copy_prompt_build.py	244	文案	finalize_copy.py:124 subprocess	有 test_copy_prompt_build	搬	測試用到（§7 寫暫不包 MCP，但檔要在）
scripts/audio/cut.py	448	剪輯核心	podcast-cut SKILL.md:50；finalize_copy.py:98；test_timeline.py:136 import	有 test_cut	搬	剪輯線入口
scripts/audio/cutplan.py	380	剪輯核心	session.py:68,398；precut.py:61；insert_prepare.py:30、pertrack_blocks.py:38 import	有 test_cutplan	搬	剪輯核心
scripts/audio/diarize.py	376	剪輯核心	session.py:66,378；precut.py:59；prosody.py:36 import；test_precut_e2e.py:45 import；good-student SKILL.md:290	有 test_diarize_align	搬	剪輯核心
scripts/audio/diff_clips.py	259	剪輯核心	podcast-cut SKILL.md	有 test_diff_clips	搬	剪輯核心
scripts/audio/fillers_local.py	234	轉錄與標點	無	無	待定	§7 列在轉錄家族「由 ingest/precut 帶動」，但 grep 整個 repo 沒有呼叫者，也沒有測試；要決定它是死碼還是手動工具
scripts/audio/finalize.py	130	歸檔	podcast-cut SKILL.md	有 test_finalize	搬	測試＋skill
scripts/audio/finalize_copy.py	206	文案	無（只有測試）	有 test_finalize_copy	搬	run_all 的測試要它
scripts/audio/ingest_tracks.py	260	剪輯核心	precut.py:57	有 test_ingest_tracks	搬	剪輯核心
scripts/audio/insert_prepare.py	127	剪輯核心	render_cut.py 的訊息提到（:887）；無程式呼叫	有 test_insert_prepare	搬	測試用到；補錄流程
scripts/audio/migrate_marks.py	356	歸檔	無（CLAUDE.md 文件）	有 test_resegment_migrate	搬	測試用到
scripts/audio/pause_rows.py	176	剪輯核心	無	無	待定	§7 列在剪輯核心，但 grep 沒有呼叫者，也沒有測試（mars-cc session-adr.log:1223 寫過「要接上」）；要決定是否死碼
scripts/audio/pertrack_attrib.py	438	剪輯核心	pertrack_blocks.py:334、tracks_columns.py:484 import	有 test_pertrack_attrib	搬	剪輯核心
scripts/audio/pertrack_blocks.py	549	剪輯核心	precut.py:62；tracks_columns.py:485 import	有 test_pertrack_blocks	搬	剪輯核心
scripts/audio/pertrack_cells.py	359	剪輯核心	pertrack_render.py:38、render_cut.py:1920 import	有 test_pertrack_cells	搬	剪輯核心
scripts/audio/pertrack_render.py	411	剪輯核心	render_cut.py:1919,2466、tracks_columns.py:176 import	有 test_pertrack_render	搬	剪輯核心
scripts/audio/precut.py	250	剪輯核心	podcast-cut SKILL.md	有 test_precut、test_precut_e2e	搬	剪輯核心
scripts/audio/prosody.py	215	剪輯核心	session.py:67,390；precut.py:60	有 test_prosody	搬	剪輯核心
scripts/audio/render_cut.py	2650	剪輯核心	cut.py:35 import、:358 subprocess；diff_clips.py:41、pause_rows.py:43、pertrack_render.py:347 import	有 test_render_cut、test_render_audio、test_render_tracks、test_timeline	搬	剪輯核心
scripts/audio/resegment_srt.py	79	轉錄與標點	無（CLAUDE.md 文件）	有 test_resegment_migrate	搬	測試用到
scripts/audio/restore_punct.py	291	轉錄與標點	無（只有 mars-cc 000_Agent/memory/handoff/2026-09-30_*.md 記錄手動用過）	無	待定	§7 寫由 ingest/precut 帶動，但沒有任何程式呼叫；要決定手動工具是否保留
scripts/audio/restore_punct_txt.py	312	轉錄與標點	無（同上 handoff；docstring 說邏輯照抄 restore_punct，但不 import 它）	無	待定	b5d98c5 剛提交；跟 restore_punct.py 同一個問題
scripts/audio/session_paths.py	76	剪輯核心（共用函式庫）	23 個以上的檔 import（audio/*、session.py:55、describe_images.py:27、pipeline_logger.py:29、prepublish_gate.py:27、frames/extract.py:25、compose.py:29）	有 test_session_paths	搬	全管線的路徑單一真相源
scripts/audio/srt_utils.py	205	剪輯核心（共用函式庫）	cutplan、diarize、diff_clips、insert_prepare、migrate_marks、pause_rows、pertrack_*、precut、prosody、render_cut、resegment_srt、transcribe_* 都 import	有 test_srt_utils	搬	共用函式庫
scripts/audio/tidy_session.py	206	歸檔	finalize.py:38 import；podcast-cut SKILL.md	有 test_tidy_session	搬	finalize 依賴
scripts/audio/tracks_columns.py	645	剪輯核心	render_cut.py:35、change_preview.py:24、copy_prompt_build.py:28、migrate_marks.py:28 import	有 test_tracks_columns、test_render_tracks	搬	剪輯核心
scripts/audio/transcribe_llmnode.py	277	轉錄與標點	session.py:70,307（--asr 預設 llm-node）；batch_llmnode.sh；good-student SKILL.md:136	有 test_transcribe_llmnode	搬	預設 ASR
scripts/audio/transcribe_local.py	101	轉錄與標點	session.py:69,289；precut.py:58；finalize_copy.py:115；good-student SKILL.md:140	間接（test_precut_e2e 真的跑它）	搬	ASR
scripts/build_genai2026_day1.py	73	build_genai2026	無	無	不搬	一次性；ROOT 寫死 /Users/shuotaochiang（:16）
scripts/build_genai2026_day2.py	88	build_genai2026	無	無	不搬	同上（:19）
scripts/compress_images.py	38	出版	publish_goodedunote.sh	無	不搬	出版部署
scripts/dedupe_images.py	171	圖片線	session.py:637,753 的 marker 流程；publish_qaqc.py:513 import	無	搬	圖片線 marker 的第 1 步
scripts/describe_images.py	327	圖片線	session.py:611,722 的 marker tool 欄位；pipeline_autopilot.sh	間接（test_doc_figures 複製它的掃描邏輯，test_session_doc_line:203 驗 marker）	搬	圖片線
scripts/finalize_placement.py	88	圖片線	無（prompts/publish_qaqc.md:262 文件）	無	待定	只在出版規則被提到，用途是「已出版 deck」的排序收尾；要決定算圖片線還是出版
scripts/image_notes_session.py	130	圖像視角筆記	.agent/workflows/*.md、.gemini/commands/*.toml（/note、/好學生筆記）	無	待定	「指定身份」的圖像版筆記，是否屬 ADR 第 10 條「身份視角註解筆記不搬」？sessions 裡找不到任何 note/ 或 base_p*.png 產物（find 得 0）
scripts/img_context_score.py	64	圖片線	dedupe_images.py:30、propose_anchors.py:25、placement_supervisor.py:27、prepublish_gate.py:134 import	間接（test_placement）	搬	圖片線依賴
scripts/insert_images.py	247	圖片線	session.py:633,749 marker；finalize_placement.py:22、propose_anchors.py:26、placement_supervisor.py:28 import；pipeline_autopilot.sh	間接（test_placement）	搬	圖片線
scripts/lock_sessions.py	72	出版	無	無	不搬	goodedunote 出版頁軟鎖；ROOT 寫死 /Users/shuotaochiang（:16）
scripts/md_to_a4_png.py	194	圖像視角筆記	image_notes_session.py:53 import	無	待定	跟 image_notes_session.py 同一個問題；依賴的 Playwright 兩個 venv 都沒裝
scripts/normalize_punctuation.py	185	知識線統籌與閘	session.py:62,574；prepublish_gate.py:24 import；pipeline_autopilot.sh	無	搬	知識線閘
scripts/pipeline_autopilot.sh	40	知識線統籌與閘	無程式呼叫（CLAUDE.md:38 Quick Start 列為入口）	無	搬	知識線閉環入口（只跑 session.py＋gate，deploy 留人工）
scripts/pipeline_logger.py	224	知識線統籌與閘	insert_images.py:136、prepublish_gate.py:165、publish_qaqc.py:696 import	有 test_pipeline_logger	搬	閘＋測試
scripts/placement_check.py	143	圖片線	prepublish_gate.py:67、publish_qaqc.py:467 import	有 test_placement	搬	圖片線
scripts/placement_supervisor.py	154	圖片線	insert_images.py:125、prepublish_gate.py:85 的提示訊息	有 test_placement:329 subprocess	搬	測試用到
scripts/prepublish_gate.py	187	知識線統籌與閘	publish_goodedunote.sh；pipeline_autopilot.sh	無（test_pipeline_logger 只用它的名稱字串）	搬	§7 列在知識線閘
scripts/propose_anchors.py	161	圖片線	placement_supervisor.py:29 import；session.py marker	有 test_placement	搬	圖片線
scripts/publish_goodedunote.sh	60	出版	無（CLAUDE.md、prompts 文件）	無	不搬	Firebase 部署
scripts/publish_qaqc.py	715	出版	publish_goodedunote.sh 流程；pipeline_autopilot.sh 文字	無	不搬	出版
scripts/qaqc_phase_b.py	493	知識線統籌與閘	session.py:61,485,835,897；SRT/qaqc_srt.py:181	無	搬	知識線主體
scripts/session.py	1070	知識線統籌與閘	good-student SKILL.md:344、podcast-cut SKILL.md:57、video-to-md SKILL.md:32、good-student-notes SKILL；pipeline_autopilot.sh	有 test_session_slug（import）、test_session_doc_line（subprocess）	搬	知識線統籌
scripts/cutplan-editor/.claspignore	6	cutplan-editor	clasp push	無	搬	§4.2 人審介面
scripts/cutplan-editor/Code.gs	207	cutplan-editor	Apps Script；tests/timeline.test.js:157、tracks.test.js:183 vm 載入	有 node 測試	搬	同上
scripts/cutplan-editor/Index.html	1448	cutplan-editor	Apps Script；tools/build-local.js:8 讀取	無直接測試	搬	同上
scripts/cutplan-editor/README.md	232	cutplan-editor	無（文件）	—	搬	部署與測試說明
scripts/cutplan-editor/appsscript.json	10	cutplan-editor	clasp	無	搬	同上
scripts/cutplan-editor/cutplan-core-inline.html	1977	cutplan-editor	Index.html include；由 tools/build-inline.js 產生	有 inline-sync.test.js	搬	同上（cutplan-core.js 的產物）
scripts/cutplan-editor/cutplan-core.js	1975	cutplan-editor	tests/*.test.js require；check-real-file.js:18	有	搬	同上
scripts/cutplan-editor/local.html	1536	cutplan-editor	由 build-local.js:385 產生；wave-core.test.js:389 讀取	有	搬	同上
scripts/cutplan-editor/tests/cutplan-core.test.js	683	cutplan-editor	node --test	—	搬	P1 要求 node 測試數量不變
scripts/cutplan-editor/tests/inline-sync.test.js	29	cutplan-editor	node --test	—	搬	同上
scripts/cutplan-editor/tests/timeline.test.js	272	cutplan-editor	node --test	—	搬	同上
scripts/cutplan-editor/tests/tracks.test.js	305	cutplan-editor	node --test	—	搬	同上
scripts/cutplan-editor/tests/wave-core.test.js	399	cutplan-editor	node --test	—	搬	同上
scripts/cutplan-editor/tools/build-inline.js	20	cutplan-editor	人工執行（README:102）	—	搬	產 inline 檔的唯一方式
scripts/cutplan-editor/tools/build-local.js	386	cutplan-editor	人工執行	—	搬	產 local.html
scripts/cutplan-editor/tools/check-real-file.js	113	cutplan-editor	無（README:62 寫「一次性、不進版控」，但實際有追蹤）	—	搬	跟著目錄走；README 與版控狀態矛盾
scripts/cutplan-editor/wave-editor.js	1478	cutplan-editor	local.html；wave-core.test.js:390 讀取	有	搬	同上
scripts/doc/__init__.py	0	文件線	讓 scripts/doc 成為套件（test_placement 提到 __init__.py）	—	搬	文件線
scripts/doc/extract.py	818	文件線	session.py:82,670；test_doc_extract.py:32 import	有 test_doc_extract、test_session_doc_line	搬	管線 A
scripts/doc/figures.py	284	文件線	session.py:83,698（--vlm）	有 test_doc_figures、test_session_doc_line	搬	管線 A
scripts/frames/common.py	140	影片線	frames/extract、compose、diagram、format_text、ocr、screen import；audio/fillers_local.py:41、transcribe_llmnode.py:45 import；mars-cc test_e2e.py:51	無	搬	transcribe_llmnode 依賴
scripts/frames/compose.py	222	影片線	session.py:424 提示；video-to-md SKILL.md:58	無	搬	影片線
scripts/frames/diagram.py	87	影片線	video-to-md SKILL.md:52	無	搬	影片線
scripts/frames/extract.py	180	影片線	session.py:73,419	無	搬	影片線（2dc61eb 剛改）
scripts/frames/format_text.py	78	影片線	video-to-md SKILL.md:55	無	搬	影片線
scripts/frames/hardsub_ocr.py	112	影片線	無（mars-cc 000_Agent/adr/ADR-2026-10-10-xwx-video-to-cards-pipeline.md:11 文件）	無	待定	沒有程式或 skill 呼叫，只有 mars-cc 的 ADR 提到；手動工具是否保留
scripts/frames/ocr.py	227	影片線	session.py:423 提示；video-to-md SKILL.md:45	無	搬	影片線
scripts/frames/screen.py	195	影片線	session.py:422 提示；video-to-md SKILL.md:41；diagram.py:23、format_text.py:19 import	無	搬	影片線
scripts/lang/README.md	76	scripts/lang	無	—	不搬	上游、付費 API
scripts/lang/dedup_to_srt.py	107	scripts/lang	lang/README	無	不搬	同上
scripts/lang/en/groq_transcribe_en.py	271	scripts/lang	lang/README	無	不搬	同上
scripts/lang/en/md_to_html.py	313	scripts/lang（出版）	publish_goodedunote.sh；build_genai2026_day*	無	不搬	出版用
scripts/lang/en/srt_to_md.py	116	scripts/lang	lang/README	無	不搬	同上
scripts/lang/en/srt_zhtw.py	249	scripts/lang	srt_to_md.py	無	不搬	同上
scripts/lang/it/batch_accademia.py	167	scripts/lang	lang/README	無	不搬	同上
scripts/lang/it/batch_accademia_en.py	133	scripts/lang	lang/README	無	不搬	同上
scripts/lang/it/batch_qaqc.py	93	scripts/lang	lang/README	無	不搬	同上
scripts/lang/it/clean_srt_it.py	78	scripts/lang	lang/README	無	不搬	同上
scripts/lang/it/groq_transcribe_it.py	184	scripts/lang	lang/README	無	不搬	同上
scripts/lang/it/retry_accademia.py	157	scripts/lang	lang/README	無	不搬	同上
scripts/lang/ja/gemini_transcribe_ja.py	118	scripts/lang	lang/README	無	不搬	同上
scripts/lang/ja/groq_transcribe_ja.py	257	scripts/lang	lang/README	無	不搬	同上
scripts/lang/ja/qaqc_phase_b_ja.py	144	scripts/lang	lang/README	無	不搬	同上
scripts/lang/ja/qaqc_srt_ja.py	118	scripts/lang	lang/README	無	不搬	同上
scripts/lang/srt_clean_md.py	110	scripts/lang	lang/README	無	不搬	同上
scripts/lang/vtt_to_txt.py	77	scripts/lang	lang/README	無	不搬	同上
scripts/tests/fixtures/build_fixtures.py	206	測試	test_precut_e2e、test_render_audio	—	搬	§4.2 測試與 fixture
scripts/tests/fixtures/ep16/case1_strike_alignment/cutplan.json	0	測試 fixture	test_render_audio	—	搬	單行 JSON（沒有換行，wc 得 0）
scripts/tests/fixtures/ep16/case1_strike_alignment/cutplan.md	13	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case1_strike_alignment/expect.json	25	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case1_strike_alignment/prosody.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case1_strike_alignment/transcript.speakers.srt	31	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case1_strike_alignment/words.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case2_repeat_cut/cutplan.json	0	測試 fixture	test_render_audio、test_precut_e2e:49	—	搬	fixture
scripts/tests/fixtures/ep16/case2_repeat_cut/cutplan.md	7	測試 fixture	同上	—	搬	fixture
scripts/tests/fixtures/ep16/case2_repeat_cut/expect.json	25	測試 fixture	同上	—	搬	fixture
scripts/tests/fixtures/ep16/case2_repeat_cut/prosody.json	0	測試 fixture	同上	—	搬	fixture
scripts/tests/fixtures/ep16/case2_repeat_cut/transcript.speakers.srt	7	測試 fixture	同上	—	搬	fixture
scripts/tests/fixtures/ep16/case2_repeat_cut/words.json	0	測試 fixture	同上	—	搬	fixture
scripts/tests/fixtures/ep16/case3_filler/cutplan.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case3_filler/cutplan.md	13	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case3_filler/expect.json	25	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case3_filler/prosody.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case3_filler/transcript.speakers.srt	31	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case3_filler/words.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case4_swallowed_pause/cutplan.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case4_swallowed_pause/cutplan.md	22	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case4_swallowed_pause/expect.json	25	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case4_swallowed_pause/prosody.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case4_swallowed_pause/transcript.speakers.srt	63	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case4_swallowed_pause/words.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case5_short_deadair/cutplan.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case5_short_deadair/cutplan.md	22	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case5_short_deadair/expect.json	25	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case5_short_deadair/prosody.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case5_short_deadair/transcript.speakers.srt	59	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case5_short_deadair/words.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case6_long_silence/cutplan.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case6_long_silence/cutplan.md	20	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case6_long_silence/expect.json	25	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case6_long_silence/prosody.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case6_long_silence/transcript.speakers.srt	55	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16/case6_long_silence/words.json	0	測試 fixture	test_render_audio	—	搬	fixture
scripts/tests/fixtures/ep16_artifact_samples.py	16	測試	test_cutplan:30、test_insert_prepare:22、test_pertrack_blocks:30 import	—	搬	測試共用資料
scripts/tests/pick_python.sh	27	測試	run_all.sh:14；precut.py 文字；test_precut；test_run_all_python	—	搬	run_all 選直譯器
scripts/tests/run_all.sh	57	測試	podcast-cut SKILL.md；CLAUDE.md	有 test_run_all_python	搬	P1 驗收入口
scripts/tests/test_audacity_labels.py	457	測試	run_all.sh 的 glob	—	搬	36 支測試之一
scripts/tests/test_change_preview.py	142	測試	run_all.sh	—	搬	同上
scripts/tests/test_check_punct_density.py	195	測試	run_all.sh	—	搬	同上
scripts/tests/test_copy_prompt_build.py	289	測試	run_all.sh	—	搬	同上
scripts/tests/test_cut.py	469	測試	run_all.sh	—	搬	同上
scripts/tests/test_cutplan.py	380	測試	run_all.sh	—	搬	同上
scripts/tests/test_diarize_align.py	207	測試	run_all.sh	—	搬	同上
scripts/tests/test_diff_clips.py	110	測試	run_all.sh	—	搬	同上
scripts/tests/test_doc_extract.py	598	測試	run_all.sh:22（.venv-doc pytest）	—	搬	同上
scripts/tests/test_doc_figures.py	217	測試	run_all.sh:22（.venv-doc pytest）	—	搬	同上
scripts/tests/test_finalize.py	135	測試	run_all.sh	—	搬	同上
scripts/tests/test_finalize_copy.py	133	測試	run_all.sh	—	搬	同上
scripts/tests/test_ingest_tracks.py	233	測試	run_all.sh	—	搬	同上
scripts/tests/test_insert_prepare.py	54	測試	run_all.sh	—	搬	同上
scripts/tests/test_pertrack_attrib.py	383	測試	run_all.sh	—	搬	同上
scripts/tests/test_pertrack_blocks.py	466	測試	run_all.sh	—	搬	同上
scripts/tests/test_pertrack_cells.py	225	測試	run_all.sh	—	搬	同上
scripts/tests/test_pertrack_render.py	389	測試	run_all.sh	—	搬	同上
scripts/tests/test_pipeline_logger.py	197	測試	run_all.sh	—	搬	同上
scripts/tests/test_placement.py	421	測試	run_all.sh	—	搬	同上
scripts/tests/test_precut.py	412	測試	run_all.sh；test_precut_e2e	—	搬	同上
scripts/tests/test_precut_e2e.py	230	測試	run_all.sh	—	搬	同上（需要 HF_TOKEN 與 fixture wav，缺了就 skip，見 E）
scripts/tests/test_prosody.py	106	測試	run_all.sh	—	搬	同上
scripts/tests/test_render_audio.py	109	測試	run_all.sh	—	搬	同上（缺 wav 時警告並少跑，:100）
scripts/tests/test_render_cut.py	1642	測試	run_all.sh	—	搬	同上
scripts/tests/test_render_tracks.py	467	測試	run_all.sh；test_timeline:110 import	—	搬	同上（:333 找 EP22 真 session）
scripts/tests/test_resegment_migrate.py	379	測試	run_all.sh	—	搬	同上
scripts/tests/test_run_all_python.py	86	測試	run_all.sh	—	搬	同上
scripts/tests/test_session_doc_line.py	286	測試	run_all.sh:22（.venv-doc pytest）	—	搬	同上
scripts/tests/test_session_paths.py	83	測試	run_all.sh	—	搬	同上
scripts/tests/test_session_slug.py	89	測試	run_all.sh	—	搬	同上
scripts/tests/test_srt_utils.py	220	測試	run_all.sh	—	搬	同上
scripts/tests/test_tidy_session.py	105	測試	run_all.sh	—	搬	同上
scripts/tests/test_timeline.py	155	測試	run_all.sh	—	搬	同上
scripts/tests/test_tracks_columns.py	483	測試	run_all.sh	—	搬	同上
scripts/tests/test_transcribe_llmnode.py	90	測試	run_all.sh	—	搬	同上
SRT/.agent/workflows/check.md	140	SRT 上游文件	無	—	不搬	上游 agy 工作流；程式不讀
SRT/.agent/workflows/merge.md	115	SRT 上游文件	無	—	不搬	同上
SRT/Agent.md	148	SRT 上游文件	無（CLAUDE.md 文件）	—	不搬	以 transcribe.py（Groq）為第一階段的舊流程說明
SRT/SRT_QA_QC_檢查清單.md	384	SRT 規則文件	無（CLAUDE.md、check.md 文件）	—	待定	清理規則的人讀版，程式不讀；要不要當字典文件一起帶
SRT/context.example.txt	41	SRT 範例	無（good-student-notes SKILL、CLAUDE.md 文件）	—	待定	context 檔範本，只被不搬的 good-student-notes 引用
SRT/qaqc_srt.py	269	知識線統籌與閘（清理）	session.py:60,337,348；SRT/transcribe.py:118	無	搬	§4.2 點名必搬；import dict.load（:39）
SRT/transcribe.py	159	SRT 上游工具	無程式呼叫（CLAUDE.md:277,360 文件）	無	不搬	互動式，直打 Groq 付費 API（:45-47）
dict/_manifest.json	13	字典	web/dict-loader.js:28,105	—	搬	字典的 domain/版本表（CLAUDE.md:795-805 規定跟字典一起維護）
dict/hallucination_prefixes.json	33	字典	dict/load.py:54,61	—	搬	§4.2 點名
dict/load.py	117	字典	SRT/qaqc_srt.py:39 import	無	搬	§4.2 點名
dict/qaqc_config.json	15	字典	dict/load.py:72	—	搬	§4.2 點名
dict/typo_dict.bim.json	123	字典	dict/load.py:39（--domain bim 動態載入）	—	搬	§4.2 點名
dict/typo_dict.json	12	字典	dict/load.py:35	—	搬	§4.2 點名
dict/typo_dict.parenting.json	29	字典	dict/load.py:39	—	搬	§4.2 點名
dict/typo_dict.productivity.json	57	字典	dict/load.py:39	—	搬	§4.2 點名
prompts/image_notes_design.md	334	圖像視角筆記	無程式讀取（.agent/.gemini workflows、web 文件）	—	待定	跟 image_notes_session.py 同一個問題
prompts/image_notes_skill.md	44	圖像視角筆記	無程式讀取（.agent/.gemini workflows）	—	待定	同上
prompts/publish_qaqc.md	574	知識線規則	session.py:610,632,721,748 的 marker rules_ref；圖片線各檔 docstring 的 SSoT	—	搬	marker 指向它
prompts/qaqc_core_rules.md	219	知識線規則	qaqc_phase_b.py:55 執行時讀取	—	搬	管線執行時讀
shared-material/水星貓的生活實驗室_v1	symlink	素材	render_cut.py:41 MATERIAL_ROOT；copy_prompt_build.py:31	—	搬	相對 symlink → ../../branding-kit/aquacatlivinglab/show-kit/v1
shared-material/水星貓的生活實驗室_v2	symlink	素材	render_cut.py:41 MATERIAL_ROOT	—	搬	相對 symlink → ../../branding-kit/.../v2
.claude/skills/astro-chunking/SKILL.md	122	skill 本體	~/.claude/skills symlink；good-student SKILL.md:648	—	待定	§13.3 說 P1 舊 skill 原樣搬，但 ADR 第 9 條說封存、P5b 才抽三段；P1 要不要帶
.claude/skills/astro-chunking/scripts/srt_clean.py	31	skill 自帶腳本	astro-chunking SKILL.md:21	無	待定	同上
.claude/skills/audio-to-md/SKILL.md	198	skill 本體	~/.claude/skills symlink	—	搬	§13.3 P1 原樣搬
.claude/skills/audio-to-md/references/usage-guide.md	32	skill 本體	audio-to-md SKILL	—	搬	同上
.claude/skills/audio-to-md/scripts/audio_to_md.py	340	skill 自帶腳本	audio-to-md SKILL.md:88	無	搬	同上；注意它要 faster-whisper，兩個 venv 與系統 python3 都沒裝
.claude/skills/audio-to-md/scripts/requirements.txt	1	skill 自帶腳本	—	—	搬	同上
.claude/skills/doc-to-md-installer/README.md	159	installer	無	—	不搬	§11 第 6 項、§13.6
.claude/skills/doc-to-md-installer/USAGE.md	265	installer	無	—	不搬	同上
.claude/skills/doc-to-md-installer/install.bat	201	installer	無	—	不搬	同上
.claude/skills/doc-to-md-installer/install.sh	167	installer	無	—	不搬	同上
.claude/skills/doc-to-md/SKILL.md	123	skill 本體	~/.claude/skills symlink	—	搬	P1 原樣搬
.claude/skills/doc-to-md/references/usage-guide.md	259	skill 本體	doc-to-md SKILL	—	搬	同上（內文寫 ~/.claude/skills/doc-to-md/... 路徑）
.claude/skills/doc-to-md/scripts/doc_to_md.py	602	skill 自帶腳本	doc-to-md SKILL.md:36	無	搬	同上（3 份副本之一，見 B）
.claude/skills/doc-to-md/scripts/requirements.txt	6	skill 自帶腳本	—	—	搬	同上
.claude/skills/doc-vlm-to-md-installer/README.md	159	installer	無	—	不搬	§11 第 6 項
.claude/skills/doc-vlm-to-md-installer/USAGE.md	81	installer	無	—	不搬	同上
.claude/skills/doc-vlm-to-md-installer/install.bat	162	installer	無	—	不搬	同上
.claude/skills/doc-vlm-to-md-installer/install.sh	146	installer	無	—	不搬	同上
.claude/skills/doc-vlm-to-md/SKILL.md	401	skill 本體	~/.claude/skills symlink	—	搬	P1 原樣搬
.claude/skills/doc-vlm-to-md/references/usage-guide.md	19	skill 本體	doc-vlm-to-md SKILL	—	搬	同上
.claude/skills/doc-vlm-to-md/scripts/check_layout.py	233	skill 自帶腳本	doc-vlm-to-md SKILL.md:118-120	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/count_figures.py	132	skill 自帶腳本	good-student SKILL.md:68（Step 0 圖片閘門）；doc-vlm SKILL	無	搬	good-student 閘門
.claude/skills/doc-vlm-to-md/scripts/doc_to_md.py	609	skill 自帶腳本	kb_prep.py:34 import	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/epub_images.py	68	skill 自帶腳本	kb_prep.py:36 import；SKILL.md:41,217	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/gen_prompts.py	49	skill 自帶腳本	無程式呼叫（SKILL.md:349 寫「派工前」用）	無	待定	一次性：寫死 GSN sessions 路徑（:4）與 tailnet IP（:6），而且寫入 skill 目錄下的 prompts/（:15）
.claude/skills/doc-vlm-to-md/scripts/img_prompt.py	79	skill 自帶腳本	SKILL.md:227	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/kb_prep.py	467	skill 自帶腳本	SKILL.md:142,159	無	搬	管線 B 入口
.claude/skills/doc-vlm-to-md/scripts/merge_all.py	36	skill 自帶腳本	無程式呼叫（SKILL.md:351 自稱「一次性腳本，路徑寫死」）	無	待定	模組層就讀同目錄的 t1_list.json（:8），但該檔不存在，現狀一跑就壞
.claude/skills/doc-vlm-to-md/scripts/merge_figures.py	150	skill 自帶腳本	SKILL.md:220；merge_all.py:7	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/ocr_figures.py	120	skill 自帶腳本	SKILL.md:60	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/ocr_mac.py	109	skill 自帶腳本	無（SKILL、CLAUDE.md 都 grep 不到）	無	待定	沒有呼叫者；跟 ocr_figures 寫同一個欄位，可互換
.claude/skills/doc-vlm-to-md/scripts/package_kb.py	91	skill 自帶腳本	SKILL.md:365	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/probe_layout.py	142	skill 自帶腳本	無（SKILL、CLAUDE.md 都 grep 不到）	無	待定	沒有呼叫者
.claude/skills/doc-vlm-to-md/scripts/requirements.txt	7	skill 自帶腳本	—	—	搬	同上
.claude/skills/doc-vlm-to-md/scripts/score_figures.py	106	skill 自帶腳本	SKILL.md:92,348	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/sync_corpus.py	112	skill 自帶腳本	SKILL.md:283-285	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/vlm_ocr_first.py	83	skill 自帶腳本	SKILL.md:211,218	無	搬	管線 B
.claude/skills/doc-vlm-to-md/scripts/vlm_prep.py	552	skill 自帶腳本	kb_prep.py:35 import	無	搬	管線 B
.claude/skills/good-student-notes/SKILL.md	201	skill 本體	沒有 symlink 進 ~/.claude/skills（只在 GSN 專案內生效）	—	不搬	ADR 第 10 條、§13.1
.claude/skills/good-student-notes/scripts/groq_transcribe.py	332	skill 自帶腳本	**scripts/session.py:59 GROQ_SCRIPT、:316 subprocess（--asr groq，非預設）**	無	待定	ADR 說 good-student-notes 不搬，但 session.py 仍引用；不搬的話新 repo 的 --asr groq 會壞。要決定：單檔搬過去，還是 session.py 拿掉 groq 選項
.claude/skills/good-student/SKILL.md	649	skill 本體	~/.claude/skills symlink	—	搬	§4.2 點名
.claude/skills/good-student/scripts/build_canvas.py	283	skill 自帶腳本	good-student SKILL.md:542	無	搬	§4.2 點名
.claude/skills/podcast-cut/SKILL.md	98	skill 本體	~/.claude/skills symlink	—	搬	P1 原樣搬
.claude/skills/video-to-md/SKILL.md	98	skill 本體	~/.claude/skills symlink	—	搬	P1 原樣搬
docs/adr/0001-cutplan-md-as-human-review-source-of-truth.md	35	ADR	無（文件）	—	搬	§4.7 ADR 一起搬
docs/adr/0002-render-cuts-timeline-ranges-not-files.md	44	ADR	無（文件）	—	搬	同上
docs/adr/0003-multitrack-single-pipeline-ingest-only.md	53	ADR	無（文件）	—	搬	同上
docs/adr/0004-music-overlay-not-concat.md	42	ADR	無（文件）	—	搬	同上
docs/adr/0005-word-timestamps-guard-chain.md	41	ADR	無（文件）	—	搬	同上
docs/adr/0006-bgm-two-stage-ducking-envelope.md	55	ADR	無（文件）	—	搬	同上
docs/adr/0007-gap-rows-in-cutplan.md	37	ADR	無（文件）	—	搬	同上
docs/adr/0008-input-adapter-doc-line.md	101	ADR	無（文件）	—	搬	同上
docs/adr/0009-knowledge-atom-line.md	60	ADR	無（文件）	—	搬	同上
docs/adr/0010-pause-default-cut-not-keep.md	44	ADR	無（文件）	—	搬	同上
docs/adr/0011-session-layout-and-external-insert.md	94	ADR	無（文件）	—	搬	同上
docs/adr/0012-cut-one-command-semantic-reconcile.md	75	ADR	無（文件）	—	搬	同上
docs/adr/0013-real-audio-regression-fixtures.md	86	ADR	無（文件）	—	搬	同上
docs/adr/0014-track-availability-decides-the-cut-line.md	59	ADR	無（文件）	—	搬	同上
docs/adr/0015-podcast-editing-sop.md	113	ADR	無（文件）	—	搬	同上
docs/adr/0016-deliberate-cuts-apply-after-boundary-polish.md	80	ADR	無（文件）	—	搬	同上
docs/adr/0019-pertrack-sentence-level-merge.md	128	ADR	無（文件）	—	搬	同上
docs/adr/0020-floor-gate-relative-lead-exception.md	94	ADR	無（文件）	—	搬	同上
docs/adr/0021-uncertain-reason-cause-split.md	140	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-08-11-672.md	90	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-08-11-674.md	253	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-08-14-675.md	113	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-06-mixdown-audio-line.md	76	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-07-asr-segmented-transcription.md	86	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-07-structure-anchor-rewind.md	58	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-10-cutplan-template.md	78	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-10-roomtone-insert.md	106	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-11-diff-lives-in-version-dir.md	72	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-11-drive-dir-episode-root.md	49	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-11-session-layout-standard.md	48	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-11-session-paths-single-source.md	76	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-11-workfiles-move-to-asset.md	96	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-14-chapters-by-source-time.md	67	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-09-14-mixdown-by-default.md	58	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-10-03-audio-tracks-line.md	153	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-10-03-keep-cue-level-block-granularity.md	32	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-10-30-editor-insert-break.md	23	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-11-03-audacity-fine-trim.md	44	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-11-03-bgm-loudness-normalize.md	44	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-11-03-tempo-map.md	26	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-11-06-wave-editor-mece-override.md	38	ADR	無（文件）	—	搬	同上
docs/adr/ADR-2026-11-12-1267.md	41	ADR	無（文件）	—	搬	同上
docs/adr/README.md	33	ADR	無（文件）	—	搬	ADR 索引
docs/adr/check_adr_ids.sh	58	ADR 工具	無（docs/adr/README.md 文件）	無	搬	ADR 編號檢查，跟著 ADR 走
docs/design/2026-07-29_ep-visual-assets.md	80	設計文件	無	—	待定	§4.2 寫 docs/design/ 放「ADR 與本檔」，§8 P1 寫「設計文件」；沒被 ADR 引用的舊設計稿要不要帶
docs/design/2026-07-29_phrase-level-cutplan-spec.md	79	設計文件	test_resegment_migrate.py 引用	—	搬	測試指向它
docs/design/2026-07-29_post-approval-copy-automation.md	118	設計文件	無	—	待定	同 ep-visual-assets 的問題
docs/design/2026-08-11_pertrack-agent-reviews/01_補錄插入_luna-max_實作報告.md	57	設計文件	無	—	待定	同上
docs/design/2026-08-11_pertrack-agent-reviews/02_補錄插入_sol_實作報告.md	117	設計文件	無	—	待定	同上
docs/design/2026-08-11_pertrack-agent-reviews/03_分軌架構_設計評審規格.md	127	設計文件	無	—	待定	同上
docs/design/2026-08-11_pertrack-agent-reviews/03_底噪方案_luna.md	75	設計文件	ADR-2026-08-11-672.md:89 引用	—	搬	ADR 引用
docs/design/2026-08-11_pertrack-agent-reviews/04_分軌架構_luna-max_評審.md	151	設計文件	無	—	待定	同 ep-visual-assets
docs/design/2026-08-11_pertrack-agent-reviews/04_底噪方案_sol.md	48	設計文件	ADR-2026-08-11-672.md:89 引用	—	搬	ADR 引用
docs/design/2026-08-11_pertrack-agent-reviews/05_分軌架構_sol_評審.md	235	設計文件	無	—	待定	同 ep-visual-assets
docs/design/2026-08-11_pertrack-agent-reviews/05_底噪方案_fable.md	134	設計文件	ADR-2026-08-11-672.md:89 引用	—	搬	ADR 引用
docs/design/2026-08-11_pertrack-agent-reviews/06_分軌實作_規格.md	168	設計文件	無	—	待定	同 ep-visual-assets
docs/design/2026-08-11_pertrack-agent-reviews/07_分軌實作_luna-max_報告.md	67	設計文件	無	—	待定	同上
docs/design/2026-08-11_pertrack-agent-reviews/README.md	44	設計文件	CLAUDE.md 引用	—	待定	同上（只有 CLAUDE.md 指它）
docs/design/2026-08-11_pertrack-noise-floor-brief.md	118	設計文件	ADR-2026-08-11-672.md:90 引用	—	搬	ADR 引用
docs/design/2026-11-12_studio-mcp-architecture.md	427	設計文件	mars-cc 100_Todo/projects/2026-11-12_studio-mcp/architecture.md 是 symlink 指向它	—	搬	§4.2「本檔」
docs/llm-node-502-2026-09-18.md	79	事故紀錄	無	—	待定	llm-node 事故筆記，屬管線維運知識；放哪裡沒定
docs/llm-node-502-2026-09-25-resolved.md	43	事故紀錄	無	—	待定	同上
docs/origin-story.md	230	上游文件	無（CLAUDE.md、.gitignore:94 註解）	—	不搬	上游專案敘事，管線不讀
docs/reports/2026-10-03-audio-tracks.md	362	報告	無	—	待定	分軌線實測報告，沒被 ADR 引用
.gitignore	116	根目錄設定	git	—	搬	*.wav（:14）、sessions/（:9）、.env 等規則；fixture wav 靠它被忽略
AGENTS.md	1	根目錄設定	agy/codex（內容只有「CLAUDE.md」一行）	—	待定	看 CLAUDE.md 怎麼決定
CHANGELOG.md	329	根目錄文件	無	—	不搬	不帶歷史（§4.7）
CLAUDE.md	913	根目錄設定	Claude Code	—	待定	含 Web 工作室、上游、出版段落；新 repo 是沿用改寫還是重寫？§13.6 要求舊 repo 的 CLAUDE.md 原地更新
GEMINI.md	1	根目錄設定	gemini（內容只有「CLAUDE.md」一行）	—	待定	看 CLAUDE.md 怎麼決定
GenAI2026_day1的七場.md	937	上游講稿	build_genai2026_day1.py	—	不搬	§4.2 上游講稿
GenAI2026_day2的全天.md	2137	上游講稿	build_genai2026_day2.py	—	不搬	同上
LICENSE	33	授權	無	—	待定	MIT，著作權人 Shuotao Chiang。要搬的 session.py、qaqc_phase_b.py、normalize_punctuation.py、dict/*、prompts/*、圖片線各檔首次提交作者都是 shuotao（git log --diff-filter=A），MIT 要求複製時保留授權聲明
LICENSE-CONTENT	47	授權	無	—	待定	CC BY 4.0（內容）；同上
NOTICE	30	授權	無	—	待定	雙軌授權總覽；同上
PERTRACK_IMPL.md	168	設計文件	ADR-672、675、09-06、09-14、10-03 五份 ADR 引用	—	搬	ADR 引用
README.md	469	根目錄文件	無	—	不搬	留在舊 repo 加指路（§4.7）
index.html	13	Web	無（轉址到 web/index.html）	—	不搬	上游網站
requirements-audio.txt	21	根目錄設定	session.py、precut.py:224、pick_python.sh 的安裝提示	—	搬	§4.7 照它重建 venv
requirements-doc.txt	14	根目錄設定	session.py:664、run_all.sh 提示	—	搬	同上（缺 pytest，見 E）
.agent/workflows/note.md	18	圖像視角筆記（agy 指令）	agy /note → image_notes_session.py note	—	待定	跟 image_notes_session.py 同一個問題
.agent/workflows/好學生筆記.md	25	圖像視角筆記（agy 指令）	agy /好學生筆記	—	待定	同上
.gemini/commands/note.toml	17	圖像視角筆記（gemini 指令）	gemini /note	—	待定	同上
.gemini/commands/好學生筆記.toml	23	圖像視角筆記（gemini 指令）	gemini /好學生筆記	—	待定	同上
web/（目錄，18 檔）	—	Web 工作室	瀏覽器；web/dict-loader.js:15-17 讀 ../dict/	—	不搬	Web 工作室另一條軌（§1、§12）；舊 repo 的 dict/ 留著，它不受影響
sessions/（目錄，未追蹤，124 個，6.6G）	—	session 資料	全管線	—	不搬	§4.7 原地封存
atoms/（目錄，未追蹤，5 個空子目錄，0B）	—	知識原子輸出	無程式寫入此處（good-student SKILL.md:373 寫的預設是 sessions/<slug>/atoms/）	—	不搬	空目錄
assets/（目錄，31 檔）	—	上游網站素材	無程式呼叫（render_cut.py:58 會試 PROJECT_ROOT/<token>，但 assets/ 裡沒有音檔）	—	不搬	上游講座圖片
ai-reels/scripts/README.md	82	短影音線	無（文件）	—	搬	§4.7
ai-reels/scripts/lib/audio.mjs	110	短影音線	reel-build.mjs:17 import	無	搬	同上
ai-reels/scripts/lib/captions.mjs	97	短影音線	reel-build.mjs:19 import	無	搬	同上
ai-reels/scripts/lib/config.mjs	23	短影音線	reel-build.mjs:21 import	無	搬	同上
ai-reels/scripts/lib/envelope.mjs	48	短影音線	reel-build.mjs:18 import	無	搬	同上
ai-reels/scripts/lib/highlights.mjs	98	短影音線	reel-build.mjs:20 import	無	搬	同上
ai-reels/scripts/lib/template.mjs	116	短影音線	reel-build.mjs:22 import	無	搬	同上
ai-reels/scripts/reel-build.mjs	278	短影音線	人工／README	無	搬	短影音線入口
ai-reels/template/assets/kin.jpg	8	短影音線範本	template.mjs:106-112 複製；config.default.json:15-16	—	搬	頭像
ai-reels/template/assets/mars.jpg	16	短影音線範本	同上（config.default.json:18）	—	搬	頭像
ai-reels/template/assets/sarah.jpg	20	短影音線範本	同上（config.default.json:17）	—	搬	頭像
ai-reels/template/config.default.json	32	短影音線範本	reel-build.mjs:126	—	搬	預設設定
ai-reels/template/config.schema.json	63	短影音線範本	無（沒有程式讀它）	—	搬	跟範本目錄走；只有說明用途
ai-reels/template/index.html	610	短影音線範本	template.mjs:60	—	搬	HyperFrames 範本
ai-reels/docs/adr/0001-hyperframes-local-render-no-codex.md	11	ai-reels ADR	無（文件）	—	搬	§4.7 三份 ADR
ai-reels/docs/adr/0002-template-data-externalized.md	11	ai-reels ADR	無（文件）	—	搬	同上
ai-reels/docs/adr/0003-chat-ui-design-system.md	13	ai-reels ADR	無（文件）	—	搬	同上
```

**未追蹤或被忽略、但實際會被用到的檔**（`git status --short --ignored`）。這些不會跟著 `git ls-files` 複製過去：
- `scripts/tests/fixtures/ep16/case{1..6}_*/audio16k.wav`（6 檔，整個 fixtures/ep16 目錄 4.5M），被 `.gitignore:14 *.wav` 忽略。test_render_audio.py:100 缺 wav 時只警告、少跑；test_precut_e2e.py:49,187 缺 wav 時整個 skip。**只照 ls-files 複製，測試數量會對不上。**
- `.env`（GSN 根目錄，只有 `HF_TOKEN` 一個鍵）。diarize.py:48、frames/common.py:46 會讀它；test_precut_e2e 的 HAS_TOKEN 也看它。
- `scripts/cutplan-editor/.clasp.json`（含 scriptId；README:137-146 說明遺失會很麻煩）。
- `.venv-audio/`、`.venv-doc/`（§4.7 要重建）。
- `restore_punct_txt.py` 盤點開始時是 `??`，現在已提交（b5d98c5）。

## B. 重複與死碼清單

**內容完全相同（md5）**：範圍內 305 檔逐一 `md5 -r`（2 個 symlink 指向目錄，跳過），`uniq -d` 得 **0 組**。下列重複都是範圍外的副本或「近似重複」。

| 組 | 檔案 | 證據 |
|---|---|---|
| doc_to_md.py 第 1 型 | `.claude/skills/doc-to-md/scripts/doc_to_md.py`、`.claude/skills/doc-to-md-installer/skill/scripts/doc_to_md.py`（被忽略）、`~/.doc-to-md/doc_to_md.py` | md5 都是 7b1f468f… |
| doc_to_md.py 第 2 型 | `.claude/skills/doc-vlm-to-md/scripts/doc_to_md.py`、`~/.doc-vlm-to-md/doc_to_md.py` | md5 都是 7a192a6b… |
| 第 1 型對第 2 型 | — | `diff` 只差 7 行（第 2 型在 :9-15 多一段 Windows stdout UTF-8 修正） |
| vlm_prep.py | skill 版對 `~/.doc-vlm-to-md/vlm_prep.py` | md5 相同（677431e4…） |
| kb_prep.py | skill 版（90157039…）對 `~/.doc-vlm-to-md/kb_prep.py`（301c8d60…） | **不同**：家目錄版是舊版；`~/.doc-vlm-to-md/doc-vlm-to-md` 包裝腳本跑的是這份舊版 |
| installer 的 zip | `doc-to-md-installer/skill.zip`、`doc-vlm-to-md-installer/skill.zip`（被忽略） | 內含上述 doc_to_md、kb_prep、vlm_prep、package_kb 的副本（unzip -l） |
| cutplan-core | `cutplan-core.js` 對 `cutplan-core-inline.html` | difflib 1.00；後者由 build-inline.js 產生，inline-sync.test 鎖住 |
| cutplan 編輯器 | `Index.html` 對 `local.html` | 0.82；後者由 build-local.js:8,385 從前者產生 |
| groq 轉錄 | `groq_transcribe.py` 對 `lang/en/groq_transcribe_en.py` 0.78、對 `lang/ja/groq_transcribe_ja.py` 0.72、對 `lang/it/groq_transcribe_it.py` 0.48 | difflib 行級相似度 |
| installer 互比 | install.bat 0.78、install.sh 0.71 | 同上 |
| 標點補回 | `restore_punct.py` 對 `restore_punct_txt.py` | 0.53；後者 docstring 自述邏輯逐行對應前者 |
| figures 對 vlm_prep | `scripts/doc/figures.py` 對 skill 的 `vlm_prep.py` | 只有 0.17，但 figures.py:14-15 自述「渲染邏輯移植自 vlm_prep.py（extract_figures／extract_vector_chart_pages／is_scanned_pdf）」，門檻常數相同（figures.py:58-59 對 vlm_prep.py:80） |
| 家目錄的舊安裝 | `~/.doc-to-md/`（venv 9 個套件）、`~/.doc-vlm-to-md/`（venv 10 個套件，另有 `phase2_local.py`，repo 裡沒有） | ls |

**grep 不到任何呼叫者的腳本**（沒有程式、沒有 skill 指令、也沒有測試）：
- `scripts/audio/fillers_local.py`、`scripts/audio/pause_rows.py`
- `scripts/audio/restore_punct.py`、`scripts/audio/restore_punct_txt.py`（只有 mars-cc handoff 記錄手動用過）
- `scripts/frames/hardsub_ocr.py`（只有 mars-cc ADR-2026-10-10 提到）
- `scripts/lock_sessions.py`、`scripts/build_genai2026_day1.py`、`scripts/build_genai2026_day2.py`
- `scripts/finalize_placement.py`（只有 prompts/publish_qaqc.md:262 提到）
- `.claude/skills/doc-vlm-to-md/scripts/probe_layout.py`、`ocr_mac.py`
- `.claude/skills/doc-vlm-to-md/scripts/merge_all.py`、`gen_prompts.py`（SKILL 自稱一次性；merge_all 需要的 t1_list.json 不存在）
- `SRT/transcribe.py`（只有 CLAUDE.md 提到）
- `scripts/cutplan-editor/tools/check-real-file.js`（README 寫一次性）

**只有測試在用的**：`finalize_copy.py`、`migrate_marks.py`、`resegment_srt.py`、`insert_prepare.py`（render_cut 只在訊息裡提到它）。

**只有 lang/README 指著的**：`scripts/lang/` 底下除 md_to_html、srt_zhtw 之外的檔。

## C. 兩條文件管線差異表（只讀程式碼，沒有執行）

A＝`scripts/doc/extract.py`＋`figures.py`（經 `session.py new`）；B＝`.claude/skills/doc-vlm-to-md/scripts/`（以 doc-to-md 的 `doc_to_md.py` 為文字層）。

| 能力 | A 有沒有 | A 證據 | B 有沒有 | B 證據 |
|---|---|---|---|---|
| PDF 文字抽取 | 有 | extract.py:461 extract_pdf；:507 | 有 | doc_to_md.py:215；kb_prep.py:242 build_inline_pdf_md |
| EPUB | 有 | extract.py:617（含 TOC 對映 :568-615） | 有 | doc_to_md.py:249（用 bs4，:50,263） |
| TXT | 有（chardet） | extract.py:685,694 | 有（chardet） | doc_to_md.py:134,287 |
| 簡轉繁 | 有，s2twp，固定做 | extract.py:80-88 | 有，s2twp，可用 --no-convert-chinese 關掉 | doc_to_md.py:114-123；kb_prep.py:444 |
| 去亂碼 | 有（控制字元／私用區） | extract.py:109-130 strip_garbled_chars，套用在 :508,676,704 | 部分：只濾頁碼行與純符號行 | doc_to_md.py:142-156 clean_text_block |
| 直排轉橫排 | 有 | extract.py:25-30,271,358-394；stats 的 vertical_pages（:789） | 無 | B 全部腳本 grep「直排／vertical」只有註解（check_layout.py:28、sync_corpus.py:8） |
| 嵌入點陣圖抽取 | 有 | figures.py:112-115 get_images | 有 | vlm_prep.py:142 extract_figures |
| 向量圖表頁渲染 | 有（門檻 25） | figures.py:58,130-144 | 有（門檻 25；另有圖說頁渲染） | vlm_prep.py:80,209,252,266 |
| 掃描頁 | 只整頁渲染成圖，**不做 OCR**（文字層明寫不含 OCR） | figures.py:85,107-109,180；extract.py:470 | 渲染＋OCR | vlm_prep.py:102,116；ocr_figures.py:21（llm-node 上的 RapidOCR）、ocr_mac.py:33-37（macOS Vision） |
| EPUB zip 內嵌圖＋錨點插回 | 無（figures.py 只吃 PDF） | figures.py:199 | 有 | epub_images.py:21；kb_prep.py:89,143；merge_figures.py:30 |
| OCR-first 本地批次 | 無 | — | 有 | vlm_ocr_first.py:14-15,26（寫死 tailnet IP 的 8082/8080） |
| VLM 圖說 | 有，走 marker 由 `antigravity` CLI 逐張描述 | session.py:722-726 → describe_images.py:139 | 有，三種：留 PLACEHOLDER 給 Claude 填；llm-node VLM；組讀圖 prompt | vlm_prep.py:343；vlm_ocr_first.py；img_prompt.py |
| 版面檢查（圖邊界、插入位置） | 無版面檢查；只有插圖位置檢查 | placement_check.py；session.py:759（insert_images --verify） | 有 | check_layout.py:42,83,140；probe_layout.py:49（VLM） |
| 語料倉同步 sync_corpus | 無 | — | 有（讀 DOC_CORPUS_DIR） | sync_corpus.py:28-43 |
| 打包 zip | 無 | — | 有（兼斷鏈檢查） | package_kb.py:30 |
| 字典糾錯 | 有 --corrections 參數，但 session.py 沒傳 | extract.py:713；session.py:670-671 | 無 | — |
| 產物格式 | `sessions/<slug>/cleaned.md`（沒有 frontmatter、沒有 callout）＋`images/fig_p###_##.png`＋`doc_figures.json`＋stdout 一行 JSON | session.py:669；extract.py:21-22；figures.py:23-30 | `<作者>_<書名>_知識庫.md`（YAML frontmatter＋章節摘要 callout）；kb_prep 產 `<stem>_完整知識庫.md`＋`assets/<stem>/`＋figures.json | doc_to_md.py:358,412,550-552；kb_prep.py:92,393 |
| 測試 | 有 3 支（test_doc_extract 598 行、test_doc_figures 217 行、test_session_doc_line 286 行），run_all.sh:22 用 .venv-doc 的 pytest 跑 | — | 無（skill 目錄找不到 test 檔；test_doc_figures 只在註解引用 vlm_prep 的門檻） | — |
| 執行環境 | `.venv-doc`（session.py:81） | — | SKILL 指令寫 `python3 scripts/...`，但系統 python3 沒有 fitz、bs4、chardet、ebooklib、opencc（find_spec 全空）；.venv-doc 也沒有 bs4。能跑的只有 `~/.doc-vlm-to-md/venv` | doc-vlm SKILL.md:142,149,159；doc_to_md.py:30,50 |

**實際產物的分布**（只讀 `find sessions`）：
- 管線 A：`doc_figures.json` 0 份。
- 管線 B：`figures.json` 12 份，都是 2026-08-08～09-08 的大耕紫微系列；`*知識庫.md` 0 份。
- `doc-corpus/corpus/` 有 15 個 session 目錄。

## D. 全機器引用清單

**符號連結**（`~/.claude/skills` 本身就是指向 `mars-cc/000_Agent/skills` 的 symlink）：
```tsv
symlink	指向	用途
/Users/marslo/GithubRepo_mm-xyz/mars-cc/000_Agent/skills/astro-chunking	/Users/marslo/GithubRepo_mm-xyz/good-students-note/.claude/skills/astro-chunking	全域 skill
/Users/marslo/GithubRepo_mm-xyz/mars-cc/000_Agent/skills/audio-to-md	…/good-students-note/.claude/skills/audio-to-md	全域 skill
/Users/marslo/GithubRepo_mm-xyz/mars-cc/000_Agent/skills/doc-to-md	…/good-students-note/.claude/skills/doc-to-md	全域 skill
/Users/marslo/GithubRepo_mm-xyz/mars-cc/000_Agent/skills/doc-vlm-to-md	…/good-students-note/.claude/skills/doc-vlm-to-md	全域 skill
/Users/marslo/GithubRepo_mm-xyz/mars-cc/000_Agent/skills/good-student	…/good-students-note/.claude/skills/good-student	全域 skill
/Users/marslo/GithubRepo_mm-xyz/mars-cc/000_Agent/skills/podcast-cut	…/good-students-note/.claude/skills/podcast-cut	全域 skill
/Users/marslo/GithubRepo_mm-xyz/mars-cc/000_Agent/skills/video-to-md	…/good-students-note/.claude/skills/video-to-md	全域 skill
/Users/marslo/GithubRepo_mm-xyz/mars-cc/good-students-note	/Users/marslo/GithubRepo_mm-xyz/good-students-note	repo 捷徑；mars-cc test_e2e.py 經由它 import
/Users/marslo/GithubRepo_mm-xyz/mars-cc/100_Todo/projects/2026-11-12_studio-mcp/architecture.md	../../../../good-students-note/docs/design/2026-11-12_studio-mcp-architecture.md	專案導覽
good-students-note/shared-material/水星貓的生活實驗室_v1	../../branding-kit/aquacatlivinglab/show-kit/v1	相對連結；放在 studio-mcp/shared-material 同樣深度一樣能解析
good-students-note/shared-material/水星貓的生活實驗室_v2	../../branding-kit/aquacatlivinglab/show-kit/v2	同上
```
（good-student-notes 沒有 symlink。`~/` 第 2 層、`/Users/marslo/GithubRepo_mm-xyz` 第 8 層以內，沒有其他指向這兩個 repo 的 symlink。）

**寫死路徑的地方**：
```tsv
檔案:行號	引用的路徑	用途
mars-cc/000_Agent/scripts/disk-housekeeping.sh:16	$HOME/GithubRepo_mm-xyz/good-students-note	GIT_REPOS 清單；由 launchd com.marslo.disk-housekeeping 執行（間接排程）
mars-cc/100_Todo/projects/2026-08-01_tang-podcast/scripts/pipeline.py:35-37	~/GithubRepo_mm-xyz/good-students-note/.venv-audio/bin/python、scripts/audio/transcribe_local.py	跨 repo 借用轉錄引擎（:201 找不到就退出）
mars-cc/000_Agent/scripts/whisper-transcribe/test_e2e.py:50-51	<mars-cc>/good-students-note/scripts/frames（經 symlink）	import common.parse_srt；失敗就跳過
mars-cc/.claude/settings.local.json:100-101	/Users/marslo/GithubRepo_mm-xyz/good-students-note	權限白名單（ln -s、git commit 那兩條）
mars-cc/000_Agent/adr/INDEX.md:36 等 43 處	../../../good-students-note/docs/adr/*.md	ADR 索引連結（由 build-adr-index.py 產生）
mars-cc/000_Agent/adr/INDEX.md（4 處）	ai-reels/docs/adr	同上
mars-cc/000_Agent/scripts/build-adr-index.py:14-15,142	WORKSPACE.iterdir() 掃各 repo；註解寫明 good-students-note、ai-reels 的格式	重產 INDEX.md
mars-cc/000_Agent/architecture/capability-index.md:3,15 等 42 處	GSN=GithubRepo_mm-xyz/good-students-note	能力索引（文件）
mars-cc/000_Agent/architecture/skill-topology.md:50	good-students-note（7 個 symlink）	拓撲文件
mars-cc/100_Todo/projects/2026-08-01_tang-podcast/README.md:11、docs/adr/0001-*.md:9,15	good-students-note/.venv-audio	文件
mars-cc/100_Todo/projects/2026-08-01_ai-reels/README.md:4,32	~/GithubRepo_mm-xyz/ai-reels/	專案導覽
mars-cc/000_Agent/skills/dot-skill/skills/celebrity/raymond-hou/knowledge/research/raw/{01,03,04,06,07}_*.md	/Users/marslo/GithubRepo_mm-xyz/good-students-note/sessions/2026-09-06_雷蒙超級個體工作術/…	出處 URL（sessions 原地封存，路徑仍有效）
mars-cc/000_Agent/research/2026-11-12_review-queue-triage.md:40	good-students-note/_local/EP16_seam_list.md	文件
mars-cc/000_Agent/adr/ADR-2026-10-10-xwx-video-to-cards-pipeline.md:11	good-students-note/scripts/frames/hardsub_ocr.py	文件
mars-cc/000_Agent/skills-archive/ARCHIVED.md:160,165	good-students-note/.claude/skills/	文件
mars-cc/000_Agent/{adr/ADR-2026-08-15-673.md:13,22、adr/ADR-2026-09-26-1047.md:5、adr/ADR-2026-08-23-802.md:24、adr/0018-*.md:46、adr/0020-*.md:18、adr/ADR-2026-11-12-destiny-*.md:8、architecture/tool-inventory.md:80、research/2026-11-12_memory-triage.md:49,209,221,355,378、scripts/backfill-adr.sh:63}	good-students-note／ai-reels（文字）	文件或提示文字
mars-cc/000_Agent/scripts/{session-adr.log、git-sync.log}、000_Agent/tmp/lai-pei-hsia-distill.log	good-students-note／ai-reels	log，沒有功能
personal-dashboard/design-documents/ARCHITECTURE.md:1110（pd-wt-1236 同檔 :1109）	good-students-note/_local/testdata/跑步關鍵數據_extracted.md	文件（_local 不搬）
doc-corpus/README.md:4,32	good-students-note/sessions/	文件
branding-kit/README.md:28、branding-kit/aquacatlivinglab/GAPS.md:4,16-17	good-students-note/shared-material/	文件（說明 symlink 的來歷）
good-students-note/.claude/skills/doc-vlm-to-md/scripts/gen_prompts.py:4	/Users/marslo/GithubRepo_mm-xyz/good-students-note/sessions	一次性批次
good-students-note/.claude/skills/doc-vlm-to-md/scripts/merge_all.py:6-7	…/good-students-note/sessions；~/.claude/skills/doc-vlm-to-md/scripts/merge_figures.py	一次性批次
good-students-note/.claude/skills/doc-vlm-to-md/scripts/ocr_mac.py:27	「good-students-note 的 .venv-audio」	錯誤訊息
good-students-note/.claude/skills/doc-vlm-to-md/SKILL.md:279	good-students-note/sessions/	skill 內文
good-students-note/.claude/skills/podcast-cut/SKILL.md:46,49	~/GithubRepo_mm-xyz/good-students-note	skill 指令裡 cd 的位置
good-students-note/.claude/skills/video-to-md/SKILL.md:13,25,29	~/GithubRepo_mm-xyz/good-students-note	同上
good-students-note/.claude/skills/good-student/SKILL.md:68	<mars-cc>/000_Agent/skills/doc-vlm-to-md/scripts/count_figures.py（經 symlink）	Step 0 圖片閘門
good-students-note/.claude/skills/good-student/SKILL.md:344	<good-students-note>/scripts/session.py	指令
good-students-note/.claude/skills/good-student/SKILL.md:648	mars-cc 000_Agent/skills/astro-chunking/SKILL.md（經 symlink）	領域參考
good-students-note/.claude/skills/doc-to-md/references/usage-guide.md:32,41,47,54,61	~/.claude/skills/doc-to-md/scripts/doc_to_md.py	指令（經 symlink）
good-students-note/scripts/doc/extract.py:11,66	/Users/marslo/GithubRepo_mm-xyz/good-students-note/.venv-doc/bin/python	用法說明、錯誤提示
good-students-note/scripts/tests/test_doc_extract.py:15、test_doc_figures.py:11、test_session_doc_line.py:17	同上	docstring 裡的執行說明
good-students-note/scripts/tests/test_render_tracks.py:334	~/GithubRepo_mm-xyz/good-students-note/sessions	找 EP22 真 session 的第 3 個備選位置
good-students-note/scripts/audio/cut.py:40-41	~/Library/CloudStorage/GoogleDrive-<帳號>/My Drive	DRIVE_ROOT（不是指這兩個 repo，但同樣寫死）
ai-reels/runs/ep15/meta.json:2,3,8	../../../good-students-note/sessions/2026-07-27_EP15-前任/…	run 證據裡的相對路徑（P6 等價比對要注意）
ai-reels/README.md:10、scripts/README.md:3,28、scripts/lib/audio.mjs:1、scripts/lib/highlights.mjs:1、CHANGELOG.md:25、docs/adr/0003-chat-ui-design-system.md:7	good-students-note（文字）	說明與註解；程式不寫死路徑，session 目錄由參數傳入
good-students-note/docs/adr/ADR-2026-11-12-1267.md、docs/design/2026-11-12_studio-mcp-architecture.md	ai-reels（文字）	GSN 內唯一提到 ai-reels 的地方，程式裡沒有
```
- 排程：`~/Library/LaunchAgents/*.plist` 沒有一份直接寫這兩個 repo 的路徑。唯一間接相關的是 `com.marslo.disk-housekeeping` → disk-housekeeping.sh:16。
- `crontab -l` 共 3 行，沒有一行碰到這兩個 repo。
- `git-sync.sh` 的 DEFAULT_REPOS（:147-152）沒有它們；mars-cc/.env 裡沒有 GIT_SYNC_REPOS 這個鍵。
- `~/.claude/settings*.json`、`~/.claude/hooks`、`~/.claude/projects` 都沒有這兩個 repo。

## E. 搬家會踩到的隱性依賴

**腳本靠相對位置去找的非程式資源**：
- session.py:57-83 → `sessions/`、`.venv-audio/bin/python`（:65）、`.venv-doc/bin/python`（:81）、`.claude/skills/good-student-notes/scripts/groq_transcribe.py`（:59）、`SRT/qaqc_srt.py`、`scripts/*`
- SRT/qaqc_srt.py:37-39 → 把 repo 根加進 sys.path 才 `import dict.load`（SRT/ 與 dict/ 必須同一層）；:181 → `scripts/qaqc_phase_b.py`
- dict/load.py:22,35,39,54,72 → 同目錄的 typo_dict*.json、hallucination_prefixes.json、qaqc_config.json
- qaqc_phase_b.py:55 → `prompts/qaqc_core_rules.md`；:80-89 從起點往上找 `.env`
- render_cut.py:41 → `shared-material/`；:58 → cutplan 🎵 token 依序找 session、repo 根、家目錄、素材庫
- copy_prompt_build.py:31 → `shared-material/水星貓的生活實驗室_v1/…`
- diarize.py:39,48 → repo 根的 `.env`（HF_TOKEN）
- precut.py:41-62 → `.venv-audio/bin/python`：先找自己的，找不到就用 git 主樹的（:45-51）
- finalize_copy.py:82 → `.venv-audio/bin/python`
- batch_llmnode.sh:17-18 → `.venv-audio/bin/python`
- run_all.sh:11,19 → 先 cd 到 repo 根；`.venv-doc/bin/python`；pick_python.sh → `.venv-audio/bin/python3.13`（主樹備援）
- frames/common.py:13-15,46 → `sessions/`、repo 根的 `.env`、`~/GithubRepo_mm-xyz/mars-cc/.env`；:20 OUTPUT_ROOT 預設 `~/MarsDots/source/course`
- pipeline_logger.py:32-33 → `build/`、`sessions/`；prepublish_gate.py:70、image_notes_session.py:29、test_session_doc_line.py:41 → `sessions/`
- 以 sys.path 串起來的跨目錄 import：
  - 加入 scripts/audio：session.py:54、describe_images.py:26、pipeline_logger.py:28、prepublish_gate.py:26、frames/extract.py:24、frames/compose.py:28
  - 加入 scripts/frames：audio/fillers_local.py:39
  - 加入 scripts/：audio/transcribe_llmnode.py:43
- cut.py:79、diff_clips.py:142 → `sessions/<slug>/.drive_dir`
- test_render_tracks.py:333-334 → EP22 真 session；test_precut_e2e.py:49、test_render_audio.py:100 → fixture wav
- skill 腳本：
  - kb_prep.py:33 → 同目錄 import
  - ocr_figures.py:27、probe_layout.py:41、sync_corpus.py:35 → `~/GithubRepo_mm-xyz/mars-cc/.env`
  - merge_all.py:8 → 同目錄的 `t1_list.json`
  - gen_prompts.py:15 → 寫進 skill 目錄的 prompts/
  - groq_transcribe.py:26-47 → 往上找 `.env`
- SRT/transcribe.py:118 → 依當前目錄找 `qaqc_srt.py`
- ai-reels：
  - reel-build.mjs:24-26 → `../template`（scripts/ 與 template/ 必須同層）
  - template.mjs:60,106-112 → 範本 index.html 與 assets 頭像
  - reel-build.mjs:158-159 → session 根目錄的 transcript.speakers.srt 與 words.json（不是 `_asset/`）；:86-88 → highlights.md，在根目錄或 `_meta/`
  - :241,257 → `npx hyperframes`（全域沒裝，見下一段）
- cutplan-editor 測試都以 `__dirname/..` 找檔（inline-sync:16-17、wave-core:388-390、timeline:156-157）。

**讀取的環境變數**（只列名稱）：
- 金鑰類：HF_TOKEN（diarize.py:46）、GROQ_API_KEY 與其變體（groq_transcribe.py）、GEMINI_API_KEY（qaqc_phase_b.py:437）
- llm-node：LLM_NODE_URL、LLM_NODE_MODEL（restore_punct*.py、probe_layout.py:40、frames/common.py）、LLM_NODE_TOKEN（restore_punct*.py）、LLM_NODE_SSH（transcribe_llmnode.py:52,116；batch_llmnode.sh:20）、LLM_NODE_ASR_BIN、LLM_NODE_ASR_MODEL、LLM_NODE_ASR_THREADS
- frames/common.py:48 會讀：OUTPUT_ROOT、SCENE_THRESHOLD、MIN_GAP_SEC、DEDUP_HAMMING、PAUSE_MIN_SEC、PARA_GAP_SEC
- 其他：OCR_URL（ocr_figures.py:25）、DOC_CORPUS_DIR（sync_corpus.py:31）、AI_AGENT（session.py:121）、GSN_SESSIONS_DIR（test_render_tracks.py:333）
- 偵測宿主 agent 用：CLAUDECODE、CLAUDE_CODE_ENTRYPOINT、CLAUDE_CODE_EXECPATH、GEMINI_CLI、GEMINI_CLI_SESSION、GOOGLE_GEMINI_CLI、GITHUB_COPILOT_CLI、GH_COPILOT_AGENT、COPILOT_AGENT_SESSION（session.py:94-98；qaqc_phase_b.py:394）
- 不搬的檔才讀：FIREBASE_ACCOUNT（publish_goodedunote.sh:56）
- ai-reels：沒有讀任何環境變數
- `.env` 實際有的鍵（只看名稱）：GSN 的 `.env` 只有 HF_TOKEN；mars-cc 的 `.env` 有 LLM_NODE_URL、LLM_NODE_MODEL、DOC_CORPUS_DIR

**寫死的網路位址**：
- ocr_figures.py:21、gen_prompts.py:6、vlm_ocr_first.py:14-15 → tailnet IP `100.120.197.113` 的 :8082/ocr 與 :8080/v1
- transcribe_llmnode.py:53-54 → llm-node 上的 `~/whisper.cpp`、`~/models`

**venv**：
- `.venv-audio/bin/pip freeze | wc -l` = **119**；`.venv-doc` = **12**；兩者都是 Python 3.13.13。
- requirements-doc.txt 只列 5 個套件，但 run_all.sh:40 要用 `.venv-doc` 的 pytest，而 pytest、lxml 都不在 requirements-doc.txt 裡。照 requirements 重建，文件線測試會跑不起來。
- 兩個 venv 都沒有 faster-whisper（audio-to-md 要）、bs4（skill 的 doc_to_md 要）、playwright（md_to_a4_png 要）。
- 家目錄另有 `~/.doc-vlm-to-md/venv`（10 個套件）、`~/.doc-to-md/venv`（9 個套件）。
- 外部工具：ffmpeg、ffprobe、rclone 在 /opt/homebrew/bin；node、clasp 3.3.0 在 ~/.local/bin。
- `hyperframes` 沒有全域安裝，ai-reels 也沒有 package.json；`npx hyperframes` 實際從哪裡解析出來，我沒查清楚。

**測試數**（沒有執行）：
- `scripts/tests/test_*.py` 共 **36** 檔，grep `def test_` 得 742 處。
- `scripts/cutplan-editor/tests/` 共 **5** 檔，都是 *.test.js，grep `test(`/`it(` 得 103 處。
- 這兩個數字是 grep 粗估，不是實際的測試案例數。
- ai-reels 沒有任何測試。

## 我沒查到或不確定的事

1. **llm-node 上的批次與排程沒有查**。這需要 ssh，本工單只讀本機。
2. `npx hyperframes` 怎麼解析：可能是 npm 快取，也可能每次從 registry 抓；搬家後還能不能跑，沒有驗證。
3. 下列判斷只靠 grep，沒有人實際看過使用紀錄：
   - fillers_local、pause_rows、restore_punct*、hardsub_ocr、probe_layout、ocr_mac 是真的死碼，還是 MM 手動在用。
   - image_notes 那一組算不算 ADR 第 10 條的範圍。
4. 管線 A 有沒有被用過：sessions 裡沒有 `doc_figures.json`，但有沒有走過 `session.py new <pdf>`（只產 cleaned.md、沒開 --vlm）的 session，我沒有逐一確認。
5. 呼叫關係有可能漏掉動態組字串的呼叫。我查了 import、subprocess、`_SCRIPT` 常數、shell 呼叫與 skill 指令，但沒有逐行讀完 2,650 行的 render_cut.py。
6. 全機搜尋的範圍：
   - `/Users/marslo/GithubRepo_mm-xyz` 底下的其他 repo 已全文搜過（排除 node_modules、.git、.venv*、sessions、hyperframes、dist、.wrangler、.next、resources）。
   - 家目錄的其他位置只看了 `~/.claude` 與第 2 層的 symlink；其他設定檔（例如 shell rc、Alfred、Raycast）沒查。
   - 原本要掃全機 `doc_to_md.py` 副本的 `find /` 跑太久，我中途停掉了。B 表的副本清單只涵蓋 repo、installer 目錄與 `~/.doc-*`。
7. LICENSE 的法律義務我沒有判斷，只列出要搬的檔裡哪些是上游作者首次提交的。
8. 這次盤點期間 HEAD 前進了 3 個 commit（最新 236c4de）。打 `pre-studio-split` tag 之前若還有改動，計數要重算。
