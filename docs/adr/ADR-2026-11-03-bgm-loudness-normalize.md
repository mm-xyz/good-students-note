# ADR-2026-11-03-bgm-loudness-normalize — 間奏音樂先正規化到人聲響度再套包絡

- 識別碼：ADR-2026-11-03-bgm-loudness-normalize
- 日期：2026-11-03
- 狀態：已採納
- 相關：ADR 0006（BGM 疊軌感知包絡，原則 11）、ADR-2026-09-10-cutplan-template

## 脈絡

EP22 v7 試聽意見「間奏聲音太大」。實測 v8：人聲約 -17.0 LUFS，素材庫三首原檔
opening -6.8／break -11.1／ending -8.6 LUFS（峰值過 0 dBFS）。包絡的 duck/solo 是
**振幅乘數**，同一組乘數壓在三首響度各異的原檔上，三首聽感大小不同，而且全部比人聲大
（break 成品 -11.3、ending 尾 -10.7 LUFS）。

## 決定

1. render 在套包絡之前，量每首間奏「實際用到的區間」（`ss`～`ss+dur`）的 integrated LUFS，
   補 `volume=Ndb` 拉到 `bgm-lufs`（預設 -17 ≈ 人聲響度）。量不到維持原音量。
2. 正規化後 `bgm-solo=1.0` 即「獨奏跟人聲一樣大」；`bgm-duck=0.4` 讓疊在人聲下的床維持
   約 -25 LUFS（等同舊設定對原檔平均 -8.8 LUFS 的結果，不讓床變得聽不見）。
3. 三個值住節目樣板 `template.json` 的 `config`（`bgm-lufs`/`bgm-duck`/`bgm-solo`），
   cutplan `## ⚙` 行可逐集覆蓋。程式預設 `--bgm-lufs 0`＝停用，未用樣板的舊流程不受影響。
4. 實測：合成音訊走完整 `run_ffmpeg`，獨奏窗量出 -17.0 LUFS（目標值）。

## 後果

- 用到 `水星貓的生活實驗室_v1/v2` 樣板的集數重出片，間奏音量會變（獨奏比原本小約 3 LU、與人聲同大）。
- 要更小聲：`## ⚙` 加 `bgm-solo=0.7`（約 -3 dB）；不要動 `bgm-lufs`，那是「人聲基準」。
- 人聲基準 -17 是 EP22 實測值；人聲響度不同的節目，在自己的樣板改 `bgm-lufs`。

## 修正（同日，EP22 v10 實測）

固定絕對值 `bgm-lufs -17` 是錯的：分軌 bus 在混音前只有約 -38 LUFS，最後的 loudnorm（-16）
會把「人聲＋間奏」一起拉高約 21 dB，v10 獨奏段因此比人聲大 5–6 LU（break -11.5／ending -10.7 vs 人聲 -17）。

改為 `bgm-match=1`（樣板預設）：先量混音前人聲鏈（concat→dynaudnorm）的 integrated LUFS，
間奏正規化到**同一個值**，loudnorm 之後相對大小才是設計值。`bgm-lufs` 保留為絕對值備用（`bgm-match=0` 時）。
回歸測試：人聲鏈很小聲＋loudnorm，獨奏段與人聲差 <1 LU。

## 配樂包絡（MM 2026-11-03 實聽拍板）

所有「人聲→配樂」：配樂提前 `lead=3s` 疊進人聲尾巴並升到 `duck=0.3`（人聲下最高 30%），
人聲結束後 `rise=3s` 慢慢升到 `solo=0.8`；人聲回來前 2 秒降回 30%，進場後 `tail/fadeout=5s` 內降到 0。
片尾同一規則（lead=3、fadein=3），不再讓音樂蓋過人聲。另加 `seam-pad=0.25`：跳接處補說話人自己的原聲停頓。
