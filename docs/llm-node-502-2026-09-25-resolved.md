# p4 screen pipeline：llm-node 502／connection refused — 根因確認與修復

接續 `llm-node-502-2026-09-18.md`。該報告的 bounded probe 判定「request 格式／影像大小
不是目前錯誤的最可能原因」。**本次實測推翻這一條**：影像大小就是主因。

## 為什麼前一份報告排除錯了

前一份的證據是「單張 1920×1080 canary 以 screen.py 同形 payload 成功回 200」。
單張成功不等於連續批次安全——兩者不是同一件事：

- gemma-12b 在 llm-node 以 `--ctx-size 16384` + `mmproj-F16` 載入，llama-server RSS
  約 12.6–13.4 GB，而機器 total 只有 14 GB（`available` 僅 4 GB）。
- Gemma 的 vision encoder 是固定 896×896 patch。送 1920×1080 會觸發 pan-and-scan
  切成多塊，單張的 image token 數翻倍。單張還撐得住，連續送就把 RSS 推過上限。
- llama-server 被 OOM killer 收掉後由 llama-swap 重啟，**對 client 的表現就是
  connection refused（TCP 無 listener）與 502（gateway 上游不在）**，而不是 OOM 錯誤。
  這也是為什麼事後探測總是 4/4 成功：探測時它剛重啟完、是乾淨的。

## 本次證據（2026-09-25）

| 條件 | 結果 |
| :--- | :--- |
| 原圖 1920×1080 連續批次 | 314 次 connection refused + 1 次 502；`ps` 上 llama-server 的 ELAPSED 反覆回到 00:39–00:41（一直在重啟） |
| 縮圖至最長邊 1024 連續 10 張 | **0 錯誤**，214 秒；同時側錄 llama-server RSS 穩定 12.6–12.7 GB，ELAPSED 一路長到 04:23 未重啟 |

判活的關鍵在**側錄 llama-server 的 ELAPSED**：只看 HTTP 回應會誤判成「服務好好的」，
因為每次重啟後的第一個請求都會成功。

## 修復

`scripts/frames/screen.py` 新增 `--brief`（預設行為不變）：

- 只判 `keep`/`kind`/`caption`，不要 VLM 逐字抄錄畫面文字。密集表格／命盤類抄錄會吃掉
  數千 token（llm-node 上每 token 約 280 ms），單張就足以撞上 `timeout=600`。
- `--brief` 下先把圖縮到最長邊 1024（`SCREEN_BRIEF_MAXDIM` 可調）再送。
- 逐字抄錄本來就是後續 `ocr.py` 的工作（macOS Vision，吃原圖、每張約 0.2 s、繁中準），
  不必讓 VLM 做第二次。

## 給下次的判準

- llm-node 上「connection refused／502」先假設是**記憶體擠爆導致服務重啟**，不是網路問題。
  查法是 `ssh llm-node "ps -eo rss,etime,comm | grep llama-server"` 看 ELAPSED 有沒有一直歸零。
- 要證明「不是圖片大小的問題」，canary 必須是**連續批次**，不能是單張。
