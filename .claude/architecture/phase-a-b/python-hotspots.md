# Python ホットスポット分析

調査日: 2026-04-08
対象: `src/**/*.py` (62ファイル, 22,314行)
方法: `wc -l` + `ast.parse` による関数サイズ計測 + `diff`/`comm` によるコード重複分析

---

## ファイルサイズ上位（500行超）

| # | ファイル | 行数 | 関数数 | クラス数 | 主な役割 |
|---|---------|------|--------|---------|---------|
| 1 | `TournamentDataProvider/StartGGDataProvider.py` | 1,979 | 34 | 1 | Start.gg API クライアント |
| 2 | `AutonaGameAssetManager.py` | 1,602 | 28 | 6 | ゲーム別アセット管理 |
| 3 | `AutonaScoreboardWidget.py` | 1,562 | 38 | 3 | スコアボード UI |
| 4 | `thumbnail/main_generate_thumbnail.py` | 1,534 | 17 | 0 | サムネイル画像生成 |
| 5 | `Autona.py` | 1,321 | 35 | 4 | メインウィンドウ |
| 6 | `AutonaScoreboardPlayerWidget.py` | 1,030 | 28 | 3 | 選手ウィジェット（スコアボード用） |
| 7 | `AutonaThumbnailSettingsWidget.py` | 1,001 | 20 | 3 | サムネイル設定 UI |
| 8 | `AutonaTeamPlayerWidget.py` | 951 | 36 | 2 | 選手ウィジェット（チームバトル用） |
| 9 | `AutonaBracketView.py` | 737 | 14 | 2 | ブラケット描画 |
| 10 | `AutonaWebServer.py` | 713 | 112 | 2 | Flask + SocketIO サーバー |
| 11 | `AutonaScoreboardStageWidget.py` | 573 | 21 | 4 | ステージ選択 UI |
| 12 | `AutonaWebServerActions.py` | 543 | 52 | 1 | REST API ハンドラ |
| 13 | `AutonaAssetDownloader.py` | 537 | 19 | 4 | アセットダウンローダー |

---

## 巨大関数 TOP 15（100行超）

| # | ファイル | 関数 | 行数 | 開始行 | 問題の種類 |
|---|---------|------|------|--------|-----------|
| 1 | `AutonaScoreboardWidget.py` | `__init__` | 688 | L93 | God __init__ |
| 2 | `AutonaGameAssetManager.py` | `LoadGameAssets` | 678 | L212 | 内部クラス2つ含む |
| 3 | `Autona.py` | `__init__` | 629 | L319 | God __init__ |
| 4 | `AutonaThumbnailSettingsWidget.py` | `__init__` | 473 | L66 | God __init__ |
| 5 | `thumbnail/main_generate_thumbnail.py` | `paste_image_matrix` | 323 | L242 | 座標計算の塊 |
| 6 | `AutonaGameAssetManager.py` | `AssetsLoaderThread.run` | 327 | L221 | LoadGameAssets 内部クラス |
| 7 | `AutonaGameAssetManager.py` | `AssetsLoader.run` | 312 | L554 | 同上（同期版） |
| 8 | `StartGGDataProvider.py` | `ParseMatchDataOldApi` | 265 | L747 | レスポンスパース |
| 9 | `thumbnail/main_generate_thumbnail.py` | `generate` | 261 | L1274 | エントリーポイント |
| 10 | `AutonaThumbnailSettingsWidget.py` | `updateFromSettings` | 243 | L550 | 設定反映 |
| 11 | `AutonaScoreboardPlayerWidget.py` | `__init__` | 207 | L47 | God __init__ |
| 12 | `AutonaScoreboardPlayerWidget.py` | `SetData` | 174 | L740 | データ設定 |
| 13 | `AutonaTeamPlayerWidget.py` | `SetData` | 174 | L742 | データ設定（↑と酷似） |
| 14 | `StartGGDataProvider.py` | `ParseMatchDataNewApi` | 166 | L580 | レスポンスパース |
| 15 | `AutonaGameAssetManager.py` | `GetCharacterAssets` | 166 | L1414 | アセットパス解決 |

---

## コード重複: AutonaScoreboardPlayerWidget ↔ AutonaTeamPlayerWidget

**最大の重複ホットスポット**。2ファイル合計 1,981行、19個の共通メソッド名を持つ。

### 共通メソッド（19個）

| メソッド | Scoreboard版 行数 | TeamBattle版 行数 | 備考 |
|---------|-------------------|-------------------|------|
| `__init__` | 207 | 121 | UI構築。TeamBattle版は stock/active/dead 追加 |
| `SetData` | 174 | 174 | ほぼ同一ロジック |
| `SetCharactersPerPlayer` | 125 | 122 | ほぼ同一ロジック |
| `CharactersChanged` | 43 | 39 | ほぼ同一 |
| `SwapWith` | 46 | 46 | 同一ロジック |
| `SwapCharacters` | 37 | 37 | 同一ロジック |
| `ExportMergedName` | 21 | 17 | 微差 |
| `ExportPlayerImages` | 29 | 26 | 微差（sponsor処理） |
| `ExportPlayerCity` | 5 | 5 | 同一 |
| `ComboBoxIndexChanged` | 3 | 3 | 同一 |
| `NameChanged` | 12 | 12 | 同一 |
| `LoadCountries` | 31 | 35 | ほぼ同一 |
| `LoadStates` | 38 | 38 | 同一 |
| `LoadSkinOptions` | 8 | 8 | 同一 |
| `ReloadCharacters` | 14 | 12 | 微差 |
| `SetupAutocomplete` | 14 | 11 | 微差 |
| `GetCurrentPlayerTag` | 4 | 4 | 同一 |
| `Clear` | 31 | 31 | ほぼ同一 |
| `SetIndex` | 10 | 3 | Scoreboard版が複雑 |

### Scoreboard版のみのメソッド
`ExportPlayerId`, `SetLosers`, `SavePlayerToDB`, `DeletePlayerFromDB`, `ManageSavePlayerToDBText`, `ManageDeletePlayerFromDBActive`, `deleteLater`, `refresh_theme_styles`

### TeamBattle版のみのメソッド
`ExportActiveStatus`, `ExportDeathStatus`, `GetIndex`, `GetSpinnerValue`, `IncreaseCall`, `DecreaseCall`, `IsActive`, `IsDead`, `SetActiveStatus`, `SetDeathStatus`, `SetBattleMode`, `SetDefaultSpinnerValue`, `SetDynamicSpinnerLabelText`, `ResetDynamicSpinner`, `SpinnerHandling`, `ToggleSponsorDisplay`, `SetStocksPerPlayer`

### 推定共通化可能行数
約 **600-700行** を基底クラスに抽出可能（`SetData`, `SetCharactersPerPlayer`, `SwapWith`, `SwapCharacters`, `CharactersChanged`, `LoadCountries`, `LoadStates`, `LoadSkinOptions`, `Clear` 等）

---

## AutonaWebServer.py のルート肥大化

- 101個の Flask/SocketIO ルートハンドラを1ファイルに集約
- 各ルートは 2-10行程度と短いが、ファイル全体が 713行
- REST ルートと WebSocket ルートが混在
- ルート追加のたびにファイルが膨張する構造

---

## God __init__ パターン

以下の3ファイルで `__init__` が 400行を超えている:

| ファイル | __init__ 行数 | 内容 |
|---------|-------------|------|
| `AutonaScoreboardWidget.py` | 688行 | UI構築 + シグナル接続 + メニュー構築 + タイマー設定 |
| `Autona.py` | 629行 | 全ドックウィジェット生成 + メニューバー構築 + レイアウト復元 |
| `AutonaThumbnailSettingsWidget.py` | 473行 | 設定フォーム全体の構築 |

---

## 分析サマリー

### リファクタ優先度（影響度 × 改善余地）

| 優先度 | 対象 | 理由 |
|--------|------|------|
| **高** | AutonaScoreboardPlayerWidget ↔ AutonaTeamPlayerWidget の重複 | ~700行の重複削除可能。バグ修正が2箇所に必要な状態 |
| **高** | StartGGDataProvider の巨大パース関数 | ParseMatchDataOldApi/NewApi が各 166-265行。テスト不能 |
| **中** | God __init__ 3ファイル | 可読性低下。ただし動作に問題なく段階的対応可 |
| **中** | AutonaGameAssetManager.LoadGameAssets の内部クラス | 非同期/同期の2版が各 300行超。ロジック重複 |
| **中** | thumbnail/main_generate_thumbnail.py | 関数型で書かれたモジュール。テスト追加の余地大 |
| **低** | AutonaWebServer.py のルート肥大化 | 各ハンドラは小さい。Blueprint分割で改善可能だが緊急性低 |
