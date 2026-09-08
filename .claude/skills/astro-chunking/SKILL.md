---
name: astro-chunking
description: 命理（占星）課程語料的概念級 chunking——把課程逐字稿（.srt/.txt）按「行星×星座×宮位×相位」切成一概念一檔的 Obsidian 知識庫（RAG-ready）。Use when 把占星課程材料切塊進 MarsDots/astro/，或建立同型命理知識庫。
---

# astro-chunking：命理課程語意切塊

> **2026-08-01 起，新切塊工作優先走 `/good-student`**（good-students-note repo 的通用知識點線，ADR 0009）：切法同本 skill，另強制「啟動談視角＋試切 5 張驗收＋每張帶 🎯 記憶鉤」——解決純內容轉述「看過就忘」的問題。本檔保留占星受控詞彙／目錄結構／ASR 錯字表，作為該線的占星領域參考。

## 核心原則（為什麼這樣切）

1. **Domain 結構就是 chunk 邊界**。占星語料天然按概念單位組織（金星在第七宮＝一個自足概念），直接按結構切勝過任何自動 semantic chunking（臨床領域實測：對齊概念邊界 87% vs 固定大小 13%）。LLM 的工作是「讀懂逐字稿、抽出概念、改寫成自足概念檔」，不是找邊界演算法。
2. **一概念一檔，300–800 字敘述性 prose**（Zettelkasten atomic note × 繁中 RAG 實測甜蜜點的交集）。不要純條列——embedding 讀不好表格式筆記。
3. **首段＝一句自足摘要**（「金星在第七宮指⋯⋯」），等於 Anthropic contextual retrieval 的 chunk context（top-20 檢索失敗率 -35%）。
4. **星／宮／相位拆成受控 metadata 欄位**（不是塞 tags），讓檢索先 filter（`planet=venus AND house=7`）再語意搜。
5. **輕量 parent-child**：概念檔（child）自足可檢索，`source`＋`source_ts` 指回原始逐字稿（parent），不維護雙索引。原始檔留在原地（如 `_distill/`），不複製進 vault。

## 流程

1. **前處理**：`scripts/srt_clean.py input.srt > cleaned.txt`——去序號/時間軸、保留每分鐘一個 `[HH:MM:SS]` 標記（供 `source_ts`）。
2. **判型**：由檔名/內容判斷該講屬於哪種 type（見受控詞彙）。一檔常含多個概念單位（如「金星落入的星座」含 12 塊）。
3. **切塊撰寫**：對每個概念單位產出一個 .md，格式完全照下方範本。內容忠於老師原意與關鍵措辭，去掉口語填充（「對、然後、就是說」）；明顯 ASR 同音錯字校正（見對照表）。**不腦補課程沒講的內容**；跨堂合成才標 `confidence: synthesized`。
4. **驗收**：跑下方 checklist。

## 受控詞彙（slug 一律英文小寫）

| 行星 planet | slug | 星座 sign | slug | 相位 aspect | slug |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 太陽 | sun | 牡羊 | aries | 合相（0°） | conjunction |
| 月亮 | moon | 金牛 | taurus | 對分/對沖（180°） | opposition |
| 水星 | mercury | 雙子 | gemini | 四分/刑（90°） | square |
| 金星 | venus | 巨蟹 | cancer | 三分/拱（120°） | trine |
| 火星 | mars | 獅子 | leo | 六分/六合（60°） | sextile |
| 木星 | jupiter | 處女 | virgo | | |
| 土星 | saturn | 天秤 | libra | | |
| 天王星 | uranus | 天蠍 | scorpio | | |
| 海王星 | neptune | 射手 | sagittarius | | |
| 冥王星 | pluto | 摩羯 | capricorn | | |
| 凱龍星 | chiron | 水瓶 | aquarius | | |
| 上升（點） | ascendant | 雙魚 | pisces | | |

次相位：半六分（30°）semi-sextile、半四分（45°）semi-square、八分之三（135°）sesquiquadrate、十二分之五（150°）quincunx。

宮位＝`house-01` … `house-12`（frontmatter 用整數 `house: 7`）。其他特殊點（北交點 north-node、福點 part-of-fortune…）type 用 `point`。

**type 受控值**：`planet` | `sign` | `house` | `aspect` | `point` | `planet-in-sign` | `planet-in-house` | `aspect-pair` | `concept`（容許度/推運/元素模式等機制）| `application`（學業事業/合盤/財務等應用講）。

## 目錄結構（vault 根目錄下，一課程一資料夾）

```
<vault>/astro/<課程名>/
├── planets/sun.md …            # 行星本義
├── signs/aries.md …            # 星座本義
├── houses/house-01.md …        # 宮位本義
├── aspects/conjunction.md …    # 相位本義
├── planet-in-sign/venus-in-libra.md …
├── planet-in-house/venus-in-house-07.md …
├── aspect-pairs/venus-mars.md …    # 行星對；速度快的行星在前（sun>moon>mercury>venus>mars>jupiter>…）
├── points/ascendant.md …           # 特殊點（上升、北交點…），type: point
├── concepts/orb.md …           # 機制概念，slug 用描述性英文
└── applications/career-study.md …
```

## Frontmatter 範本（欄位、順序、命名鎖死，不適用的欄位填 null 不刪除）

```yaml
---
title: 金星在第七宮
type: planet-in-house
planet: venus
house: 7
sign: null
aspect: null
keywords: [伴侶關係, 婚姻, 合作, 審美投射]
course: <課程名>
chapter: "4-5"
source: "4-5_金星 VS 宮位.srt"
source_ts: "00:12:34"
teacher: <講者>
tradition: modern-western
confidence: distilled   # verbatim(近逐字) | distilled(改寫) | synthesized(跨堂合成)
created: YYYY-MM-DD
---
```

`aspect-pair` 用 `planet` 放快行星、加一欄 `planet2` 放慢行星（緊接 `planet` 之後）。`keywords` 放老師實際講到的主題詞 3–6 個。

## 內文格式（鎖死）

```markdown
# 金星在第七宮

金星在第七宮指⋯⋯（一句自足摘要，讓這塊脫離上下文也讀得懂）。

（正文 300–800 字敘述性 prose，忠於老師講法與關鍵用詞，分 2–4 段。）

相關：[[venus|金星]]・[[house-07|第七宮]]
```

- 「相關：」行只連基礎層概念（行星/宮位/星座/相位本義），用 `[[slug|中文]]` 形式。
- 不加課程沒講的內容；老師特有的口訣/比喻要保留（那是這個知識庫的價值所在）。
- **名人星盤案例**（黛安娜等 case-study 解讀）不寫進概念檔正文；一般性教學內容才收。
- **相位粒度（白瑜課實測）**：老師按「行星對」授課（一段講完合/柔/硬相位）→ 一對一檔 `venus-mars.md`、`aspect: null`；只有明確獨立講單一相位才用 `venus-square-mars.md`。她的分堂設計＝快行星只往後配慢行星，跨堂不重複。

## ASR 錯字對照（遇到新錯字追加到這裡）

牧羊→牡羊、天平→天秤、魔羯→摩羯、名印→銘印、戴安娜→黛安娜（人名庫內統一）、戴娜→黛安娜、阿蒂密絲→阿蒂蜜絲、蘇湯普京→蘇湯普金（Sue Tompkins）、風向（星座/元素/個性語境）→風象、火向→火象、loading在→落在、南北焦點→南北交點、星月→新月、（新月滿月語境）蝕刻→時刻、散火→散夥、四焦點→四角點（上升/下降/天頂/天底）、四交點→四角點、天頂天頂→天頂天底、今星→金星、星星（金星語境重複誤聽）→金星、月向→月相、海龍→凱龍、相味→相位、病詬→詬病、超人情節→超人情結、自我褪變→自我蛻變、時事造英雄→時勢造英雄、提綱協領→提綱挈領、起程轉合→起承轉合、裁罰（制裁語境）→制裁、汲汲可營→汲汲營營、抛（簡體）→拋、薩杜恩/薩圖恩（土星農神譯名）統一薩圖恩。**非錯字備註**：「撿芝麻丟冬瓜」是講者慣用變體（非誤聽「西瓜」）照錄；「凱路斯」＝天王星羅馬名 Caelus 正確譯音，勿改。原則：同音異字用占星常識校正；不確定是錯字還是術語時保留原文並在後面標 `(sic)`。

## 驗收 checklist

- [ ] 每檔 frontmatter 欄位齊全、順序一致，slug 全部落在受控詞彙內（`grep` 抽查 planet/sign 值）
- [ ] 首段是一句自足摘要；正文是 prose 不是純條列
- [ ] `source` 檔案真實存在、`source_ts` 落在該檔時間範圍內
- [ ] 同一組合不重複建檔（一組合出現在多堂課→主檔在最完整那堂，其他堂內容併入並在 chapter 欄改成多值 `"4-5, 10-3"`）
- [ ] 檔名＝frontmatter 推得的 slug（`venus-in-house-07.md` ⇔ planet+house）

## 派工守則（平行 agent 批量切塊時）

- 委派 prompt 必須逐字附上 frontmatter 範本＋內文格式＋該檔預期產出的完整檔名清單，不留「格式自行調整」空間。
- 每個 agent 只處理一個來源檔；回報「建立的檔案清單＋沒切出來的預期單位＋新發現的 ASR 錯字」。
- 語意改寫類切塊派 sonnet；不要派 haiku（會照抄贅詞或腦補）。
