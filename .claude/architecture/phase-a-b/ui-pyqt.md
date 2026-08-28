# PyQt UI レイヤー（Phase A 調査結果）

**Phase A の調査結果。事実情報のみ。判断・提案は別フェーズ。**

## 1. .ui ファイル一覧

| ファイル名 | Root Widget 型 | loadUi 呼び出し元 | 備考 |
|---|---|---|---|
| AutonaAbout.ui | QDialog | AutonaAboutWidget.py:12 | - |
| AutonaBattleTeam.ui | QWidget | AutonaTeamBattleWidget.py:206, 210 | 2 箇所で同一 .ui を team1/team2 用にロード |
| AutonaBracket.ui | QDockWidget | AutonaBracketWidget.py:29 | - |
| AutonaCommentary.ui | QGroupBox | (確認待ち) | - |
| AutonaCommentator.ui | QGroupBox | (使用箇所未確認) | - |
| AutonaResultSettings.ui | QWidget | AutonaResultSettingsWidget.py:87 | 戻り値を `self.settings` に格納 |
| AutonaScoreboardPlayer.ui | QWidget | AutonaScoreboardPlayerWidget.py:61 | - |
| AutonaScoreboardScore.ui | QWidget | AutonaScoreboardWidget.py:473 | - |
| AutonaScoreboardStage.ui | QWidget | AutonaScoreboardStageWidget.py:54 | innerWidget に loadUi |
| AutonaScoreboardTeam.ui | QWidget | AutonaScoreboardWidget.py:420, 481 | 2 箇所で同一 .ui |
| AutonaTeamPlayer.ui | QWidget | AutonaTeamPlayerWidget.py:52 | - |
| AutonaThumbnailSettings.ui | QWidget | AutonaThumbnailSettingsWidget.py:92 | 戻り値を `self.settings` に格納 |
| AutonaTournamentInfo.ui | QDockWidget | AutonaTournamentInfoWidget.py:40 | - |

`<stylesheet>` タグは 13 ファイルすべてで未検出。フォント・寸法は QSS または `ui_constants.py` が支配。

---

## 2. 動的に Python で UI を組み立てているクラス

**QMainWindow:**
- Window (src/Autona.py:181) — ドックウィジェット管理のみ、子 UI は動的

**QDialog（7 クラス）:**
- AutonaAboutWidget (src/AutonaAboutWidget.py:9) — .ui 使用
- AutonaSelectSetWindow (src/AutonaSelectSetWindow.py:10) — Python
- AutonaSelectStationWindow (src/AutonaSelectStationWindow.py:9) — Python
- AutonaLayoutOptionsWindow (src/LayoutOptions/AutonaLayoutOptionsWindow.py:7) — Python
- AutonaSettingsWindow (src/Settings/AutonaSettingsWindow.py:8) — Python
- AutonaUpdater._UpdateDialog (src/AutonaUpdater.py:308) — Python

**QWidget（11 クラス）:**
- BracketSetWidget (src/AutonaBracketView.py:104) — Python
- AutonaIndividualGameTracker (src/AutonaIndividualGameTracker.py:18) — Python
- ResultPreviewWidget (src/AutonaResultSettingsWidget.py:28) — Python + **paintEvent**
- AutonaScoreboardWidget (src/AutonaScoreboardWidget.py:90) — Python（大規模）
- LoadingOverlay (autona_splash.py:509) — Python + **paintEvent**
- PreviewWidget (src/AutonaThumbnailSettingsWidget.py:25) — Python + **paintEvent**
- LayoutOptionsWidget (src/LayoutOptions/LayoutOptionsWidget.py:9) — Python
- SettingsWidget (src/Settings/SettingsWidget.py:7) — Python

**QGraphicsView:**
- AutonaBracketView (src/AutonaBracketView.py:276) — カスタム描画（QPainterPath）

---

## 3. paintEvent / カスタム描画

| クラス | 所在:行 | 描画内容 | 使用 API |
|---|---|---|---|
| ResultPreviewWidget | src/AutonaResultSettingsWidget.py:58 | プレビュー背景・画像 | QPainter |
| PreviewWidget | src/AutonaThumbnailSettingsWidget.py:52 | サムネイル設定プレビュー | QPainter |
| LoadingOverlay | autona_splash.py:560 | スプラッシュスクリーン | QPainter |
| AutonaDockTabTheme (event filter) | src/AutonaDockTabTheme.py:311 | ドックタブバーペイント | QPainter + QPainterPath |
| AutonaBracketView | src/AutonaBracketView.py:663-664 | ブラケット接続線 | QPainterPath |

---

## 4. setStyleSheet() の使用

集計: **約 50 箇所**

| 形式 | 箇所数 | 例 |
|---|---|---|
| インライン文字列 | 35 | `"font-size: 9pt;"` |
| 外部関数呼び出し | 10 | `UI.readonly_style()`, `UI.score_display_style(scale=1.15)` |
| 変数置換 (%) | 5 | `"background-color: %s;" % color_value` |
| f-string 埋め込み | 0 | (未検出) |

**参照される UI モジュール関数:**
- `readonly_style()` → AutonaScoreboardPlayerWidget.py:284, 286
- `disabled_style()` → (readonly_style と同一)
- `score_display_style(scale=...)` → AutonaScoreboardWidget.py:678, 683, 1480, 1481
- `score_btn_style(scale=...)` → (grep で使用未確認)

---

## 5. Qt 標準ダイアログ

**QMessageBox:** 30+ 箇所
- Autona.py:159, 551
- AutonaScoreboardWidget.py:1045-1195
- AutonaThumbnailSettingsWidget.py:629-654
- AutonaResultSettingsWidget.py:588-617
- AutonaTournamentInfoWidget.py:624-629
- AutonaWebServer.py:864-889
- AutonaUpdater.py:81-515

**QFileDialog:** 5 箇所
- Autona.py:954
- AutonaThumbnailSettingsWidget.py:561, 567
- AutonaTournamentInfoWidget.py:333, 404

**QInputDialog:** 2 箇所
- AutonaTournamentDataProvider.py:183, 201

**QColorDialog:** 1 箇所
- AutonaColorButton.py:55-57（ShowAlphaChannel オプション付き）

---

## 6. ドックウィジェット構成

- **STATE_VERSION:** 8 (src/Autona.py:183)
- **保存キー:** `windowState_v8` (src/Autona.py:705, 821)

**管理下のドック一覧 (Autona.py:277-410):**

| # | widget 変数名 | objectName | クラス | デフォルト表示 |
|---|---|---|---|---|
| 1 | tournamentInfo | "Tournament Info" | AutonaTournamentInfoWidget | ✓（最初に表示） |
| 2 | thumbnailSetting | "Thumbnail Settings" | AutonaThumbnailSettingsWidget | ✓（条件付き） |
| 3 | resultSetting | "Result Settings" | AutonaResultSettingsWidget | ✓ |
| 4 | bracket | "Bracket" | AutonaBracketWidget | ✓ |
| 5 | scoreboard | "Scoreboard" | AutonaScoreboardManager | ✓ |
| 6 | stageWidget | "Stage" | AutonaScoreboardStageWidget | ✗（hidden） |
| 7 | commentary | "Commentary" | AutonaCommentaryWidget | ✓ |
| 9 | notes | "Additional Notes" | AutonaNotesWidget | ✓ |
| 10 | statsWidget | "Statistics" | AutonaStatsWidget | ✓ |
| 11 | analysisWidget | "Analysis" | AutonaAnalysisWidget | ✓ |

`tabifyDockWidget()` による順序付け: L390-407。初期非表示は stageWidget のみ (L410)。

---

## 7. テーマ・配色定義

**定義元:** `src/ui_constants.py`

**内容:**
- タイポグラフィ: `FONT_XS`(8pt) 〜 `FONT_XL`(16pt)
- カラーパレット:
  - DARK (warm-dark): `bg_primary="#121212"`, `text_primary="#F0EAE0"`, `brand="#DC143C"` 等
  - LIGHT (warm-light): `bg_primary="#F4F3F0"`, `text_primary="#1A1917"` 等
- セマンティックカラー: `semantic_colors()`（readonly, toggle, etc.）
- スタイル生成関数: `readonly_style()`, `score_display_style()` 等
- グローバル QSS: `global_qss()`（500+ 行の QSS 定義）

**テーマ切替:**
- `_is_light_mode()` → `SettingsManager.Get("light_mode", False)`
- `notify_theme_change()` で登録済みリスナーに通知

**AutonaDockTabTheme.py の役割:**
- アプリケーションレベルの event filter（`QApplication` に install）
- dock tab bar をカスタムペイント（icon-above-text, warm gradient）
- `TAB_SHORT_LABELS` (L67-80): dock 名 → 6 文字ラベル
- gradient stops (L83-87): `#6B1020` → `#C41232` → `#B37018`
- `SIDEBAR_WIDTH=52px`, `ICON_SIZE=20x20px`

---

## 8. シグナルベースの UI 更新

**`*Signals(QObject)` クラス:** 25 個

主要なもの:
- WindowSignals (src/Autona.py) — `GameChanged` など
- StateManagerSignals (src/StateManager.py)
  - > **現状は `src/core` + `StateManagerBridge` が正（2026-08-28 Step 1）**: `StateManagerSignals` は廃止され、通知は Qt 非依存の observer（`StateManager.observers`）、Qt シグナルは `src/StateManagerBridge.py` が提供する
- AutonaScoreboardWidgetSignals (src/AutonaScoreboardWidget.py)
- AutonaStageStrikeStateSignals (src/AutonaStageStrikeLogic.py) — `state_updated`
- その他 `AutonaXxxWidgetSignals`（各ウィジェット専有）

**StateManager 接続例:**
- Autona.py:357-359 で webserver に GameChanged を接続
- 各ウィジェット `__init__` で StateManager.signals に connect
  - > **現状は `src/core` + `StateManagerBridge` が正（2026-08-28 Step 1）**: 接続先は `StateManagerBridge.instance` に変わった（シグナル名・引数は同一）。ウィジェットから observer を直接 subscribe してはいけない
