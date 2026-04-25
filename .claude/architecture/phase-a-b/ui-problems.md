# Phase B-a: UI 概念の重複表現マップ

**事実情報のみを記録。判断・評価・改善提案は含めない。**

## 調査範囲

- PyQt デスクトップ UI (src/: .py, .ui)
- OBS HTML レイアウト (layout/: *.html, *.css, *.js)
- WebEngine 画像レンダラー (src/thumbnail/, src/result/)
- React Web UI (web/src/)

**分類ルール:**
- **アプリ内部**: PyQt デスクトップ UI + React Web UI (データ編集・入力)
- **クリエイティブ領域**: OBS HTML テンプレ (配信表示) + WebEngine 画像生成 (画像出力) → 重複容認

---

## 1. スコア表示 (数値 + +/- ボタン)

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaScoreboardWidget.py:1480-1481 | score_display_style(scale) 関数参照 | アプリ内部 |
| PyQt | src/ui_constants.py:191-193 | score_display_style(scale) 関数定義 | アプリ内部 |
| React Web | web/src/NumberInput.tsx | +/- ボタン付き数値入力 | アプリ内部 |
| React Web | web/src/ScoreboardPage/SetScore.jsx:129-143 | スコア左右の NumberInput | アプリ内部 |
| OBS HTML | layout/scoreboard/index.js:Update() | jQuery .score 要素更新 | クリエイティブ |
| OBS HTML | layout/main.css | --p1-score-bg-color, --p2-score-bg-color | クリエイティブ |
| WebEngine | layout/thumbnail/thumbnail.js:initThumbnail() | サムネイル内スコア JSON | クリエイティブ |
| WebEngine | layout/result/result.js:initResult() | リザルト画像スコア JSON | クリエイティブ |

---

## 2. プレイヤー名/タグ表示 (gamerTag, real_name)

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaScoreboardPlayerWidget.py:48-100 | QLineEdit name/real_name 編集 | アプリ内部 |
| PyQt | src/AutonaBasePlayerWidget.py | 基底クラス (各プレイヤー UI 共通) | アプリ内部 |
| React Web | web/src/ScoreboardPage/Player.jsx:95-258 | gamerTag/name/realName + Autocomplete | アプリ内部 |
| React Web | web/src/ScoreboardPage/Player.jsx:194-195 | ペイロード: gamerTag, name 変換 | アプリ内部 |
| OBS HTML | layout/scoreboard/index.js:Update() | jQuery .player-name / .real-name 更新 | クリエイティブ |
| OBS HTML | layout/commentators/tag.html | コメンテーター名表示テンプレ | クリエイティブ |
| WebEngine | layout/thumbnail/thumbnail.js | JSON: players[i].name, players[i].real_name | クリエイティブ |
| WebEngine | layout/result/result.js | JSON: players[i].name, players[i].real_name | クリエイティブ |

---

## 3. キャラクター選択 UI (ゲーム別キャラのドロップダウン/アイコン)

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaScoreboardPlayerWidget.py:62 | .ui ロード (characterModel QComboBox) | アプリ内部 |
| React Web | web/src/ScoreboardPage/CharSelector.jsx:25-130 | Autocomplete + Skin select | アプリ内部 |
| React Web | web/src/ScoreboardPage/Player.jsx:408-419 | CharSelector 複数マウント | アプリ内部 |
| React Web | Redux: tshCharacters slice | キャラクター DB (en_name, display_name, skins) | アプリ内部 |
| OBS HTML | layout/scoreboard/index.js:Update() | jQuery .character DOM 更新 | クリエイティブ |
| WebEngine | layout/thumbnail/thumbnail.js:initThumbnail() | data.score[].team[].player[].character JSON | クリエイティブ |
| WebEngine | layout/result/result.js:initResult() | 同上 | クリエイティブ |

---

## 4. チームカラー選択 (red/blue 色変更)

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaScoreboardWidget.py | チームカラー UI + API 呼び出し | アプリ内部 |
| PyQt | src/AutonaColorButton.py:55-57 | QColorDialog で色選択 | アプリ内部 |
| React Web | web/src/ScoreboardPage/Header.jsx | チームカラー変更ボタン | アプリ内部 |
| React Web | REST: /scoreboard<N>-team<team>-color-<color> | 色変更 API | アプリ内部 |
| OBS HTML | layout/main.css | --p1-score-bg-color, --p2-score-bg-color | クリエイティブ |
| OBS HTML | layout/scoreboard/index.js:Update() | team[].color JSON から DOM 反映 | クリエイティブ |
| WebEngine | layout/thumbnail/thumbnail.js | サムネイル team[].color | クリエイティブ |
| WebEngine | layout/result/result.js | リザルト team[].color | クリエイティブ |

---

## 5. トーナメント情報表示 (大会名・フェーズ・マッチ番号)

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaTournamentInfoWidget.py:40-100 | .ui ロード + QLineEdit/QSpinBox/QDateEdit 編集 | アプリ内部 |
| PyQt | src/AutonaTournamentInfoWidget.py:63-77 | editingFinished/valueChanged → StateManager.Set() | アプリ内部 |
| React Web | web/src/ScoreboardPage/SetScore.jsx:34-48, 86-96 | phase/match/best_of フィールド + API 送信 | アプリ内部 |
| React Web | REST: /scoreboard<N>-set | 設定値送信 | アプリ内部 |
| OBS HTML | layout/scoreboard/index.js:Update() | jQuery .phase, .match, .tournament-name 更新 | クリエイティブ |
| OBS HTML | layout/main.css | 大会情報テキスト表示スタイル | クリエイティブ |
| WebEngine | src/thumbnail/main_generate_thumbnail.py:196-224 | StateManager.state から tournamentInfo 取得 | クリエイティブ |
| WebEngine | layout/thumbnail/thumbnail.js:initThumbnail() | JSON: data.tournamentInfo.* | クリエイティブ |

---

## 6. コメンテーター情報 (名前・twitter)

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaCommentaryWidget.py:14-100+ | QDockWidget: commentator name/twitter 入力 | アプリ内部 |
| PyQt | src/AutonaCommentaryWidget.py:40-56 | SetCommentatorNumber(), SetCharacterNumber() | アプリ内部 |
| React Web | web/src/ScoreboardPage/Commentators.jsx | コメンテーター表示/編集 | アプリ内部 |
| React Web | REST: /load-commentator-from-tag-<C> | コメンテーター読み込み API | アプリ内部 |
| OBS HTML | layout/commentators/tag.html | コメンテーター名表示 (3 バリアント) | クリエイティブ |
| OBS HTML | layout/commentators/index.css | コメンテーター名タグスタイル | クリエイティブ |

---

## 7. ステージ表示 / ステージストライク

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaScoreboardStageWidget.py:39-100+ | QDockWidget: ruleset/stage UI | アプリ内部 |
| PyQt | src/AutonaStageStrikeLogic.py | ステージストライク ロジック (undo/redo) | アプリ内部 |
| React Web | web/src/StageStrikePage/index.jsx:27-100+ | Stage Strike Page (class component) | アプリ内部 |
| React Web | web/src/StageStrikePage/StageCard.jsx | 個別ステージカード表示 | アプリ内部 |
| React Web | REST: /stage_strike_* (8 種) | ステージ操作 API | アプリ内部 |
| OBS HTML | layout/scoreboard, bracket | stage display 統合 | クリエイティブ |

---

## 8. ブラケット表示

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | src/AutonaBracketView.py | QGraphicsView カスタム描画（seed/UF 視覚化） | アプリ内部 |
| PyQt | src/AutonaBracketWidget.py | Bracket dock 全体 | アプリ内部 |
| React Web | （表示ページなし） | — | — |
| OBS HTML | layout/bracket_top8/, top12/, top16/, ultimate/ | 4 種テンプレ | クリエイティブ |

## 9. 国/州セレクター

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | AutonaScoreboardPlayerWidget の QComboBox | 国/州コード | アプリ内部 |
| React Web | web/src/CountrySelector.jsx, CountryStateSelector.jsx | Autocomplete + フラグ画像 | アプリ内部 |
| Redux | tshCountries slice | 国 DB | アプリ内部 |

## 10. best_of / phase / match 編集

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | AutonaScoreboardWidget.py の SpinBox/LineEdit | 編集 | アプリ内部 |
| React Web | web/src/ScoreboardPage/SetScore.jsx | SetScore コンポ | アプリ内部 |
| REST | `/scoreboard<N>-set` | API | アプリ内部 |

## 11. upcoming / recent / history sets

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | AutonaStatsWidget, AutonaScoreboardWidget | テーブル表示 | アプリ内部 |
| React Web | web/src/ScoreboardPage/UpcomingSets.jsx | クラスコンポ | アプリ内部 |
| OBS HTML | layout/recent_sets/, tournament_history/ | 表示 | クリエイティブ |

## 12. ロゴ・スポンサー画像

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | AutonaThumbnailSettingsWidget, AutonaTournamentInfoWidget | パス入力 + プレビュー | アプリ内部 |
| WebEngine | src/thumbnail/main_generate_thumbnail.py | `config.logo_path`, `background_path` 取得 | クリエイティブ |
| WebEngine | layout/thumbnail/thumbnail.js, layout/result/result.js | レンダ | クリエイティブ |

## 13. 設定ダイアログ全般

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | AutonaSettingsWindow, AutonaLayoutOptionsWindow, *SettingsWidget 5 種 | 設定 UI を PyQt に集約 | アプリ内部 |
| React Web | （ダイアログなし） | — | — |

## 14. ゲーム選択 UI

| 系統 | ファイル | 役割 | 領域 |
|---|---|---|---|
| PyQt | AutonaScoreboardWidget の ComboBox + AutonaGameAssetManager | ゲーム切替 | アプリ内部 |
| React Web | web/src/ScoreboardPage/Header.jsx + Redux tshGames | ヘッダ Select | アプリ内部 |
| OBS HTML | body class で `.arms`, `.sf6_online` 等 16 種の分岐 | ゲーム別表示 | クリエイティブ |

## B-a 統計サマリー

| 概念 | PyQt | React | OBS | WebEngine | 領域分類 |
|------|------|-------|-----|-----------|---------|
| 1〜14 | 14/14 | 11/14 | 12/14 | 7/14 | 大部分が混合 |

**重複の特徴:**

**アプリ内部（統一対象）**:
- 同一データを QSS vs MUI sx の異なるフレームワークで表現
- フィールド名不一致: `name`/`gamerTag`、`real_name`/`name` 等
- DB 連携度の差: React は Autocomplete、PyQt はシンプル入力

**クリエイティブ（容認）**:
- OBS: Socket.IO 受信 → jQuery DOM 更新
- WebEngine: Python JSON 注入 → 静止画レンダリング
- 目的が異なる（配信表示 vs 画像出力）

**技術的結合点:**
1. StateManager (PyQt) が全状態の単一源
2. Redux (React) は Socket.IO で StateManager と同期
3. AutonaWebServer が REST/Socket.IO の仲介
4. `program_state.json` が OBS/WebEngine の共通データ形式
5. AutonaStageStrikeLogic は PyQt/React で論理共有

---

# Phase B-b: 状態同期経路の正確化

## StateManager 中心の同期アーキテクチャ

Autona の全 UI 系統は **StateManager**（Python グローバル状態辞書）を中心に同期される:

1. **PyQt UI** — StateManager を直接 Set/Get
2. **OBS HTML** — `program_state.json` を 64ms ポーリング、または Socket.IO 受信
3. **WebEngine 画像生成** — StateManager.state を直接読み込み（prepare_data 内）
4. **React Web UI** — Socket.IO で差分受信、Redux に適用

## 表 1: キー × 系統マトリックス（代表キー）

| キーパス | PyQt | OBS HTML | WebEngine | React Web |
|---------|------|---------|-----------|----------|
| `score.{N}.team.{T}.player.{M}.gamerTag` | R/W (edit) | R (poll/socket) | R (prepare) | R/W (socket) |
| `score.{N}.team.{T}.player.{M}.name` | R/W | R | R | R/W |
| `score.{N}.team.{T}.player.{M}.character` | R/W (combo) | R | R | R/W |
| `score.{N}.team.{T}.player.{M}.country_code` | W (combo) | R | R | R/W |
| `score.{N}.team.{T}.score` | R/W (tracker) | R | R | R/W (API) |
| `score.{N}.team.{T}.color` | W (button) | R | R | W (API) |
| `score.{N}.best_of` | W (combo) | R | R | R |
| `score.{N}.match` | W (edit) | R | R | R |
| `score.{N}.phase` | W (combo) | R | R | R |
| `score.{N}.stage_strike` | R/W (logic) | R (/ruleset) | — | R/W (socket) |
| `score.{N}.stages.*` | W (tracker) | R | R | — |
| `score.{N}.last_sets` | W (stats) | R | R | R |
| `score.{N}.history_sets` | W (stats) | R | R | R |
| `commentary.{N}.*` | W/D | — | — | — |
| `tournamentInfo.*` | W (edit) | R (partial) | R | R (partial) |
| `game.*` | W (manager) | R | R | R (socket) |
| `bracket.*` | W (view) | R (layout dependent) | — | — |
| `player_list.*` | W (widget) | — | — | — |
| `result_players` | R/W (widget) | — | R (prepare) | — |

凡例: R = 読み取り、W = 書き込み、R/W = 両方、— = 関与なし

## 表 2: PyQt → StateManager（直接書き込み・読み取り）

| 操作 | 所在 | キー |
|------|-----|-----|
| 書き込み | src/AutonaScoreboardPlayerWidget.py:793 | `score.{N}.team.{T}.player.{M}.gamerTag` 等 |
| 書き込み | src/AutonaScoreboardWidget.py:1000+ | `score.{N}.best_of`, `match`, `phase` |
| 書き込み | src/AutonaIndividualGameTracker.py:150+ | `score.{N}.stages.{I}.*` |
| 書き込み | src/AutonaTournamentInfoWidget.py:63-77 | `tournamentInfo.*` |
| 書き込み | src/AutonaCommentaryWidget.py:85+ | `commentary.{N}.*` |
| 読み取り | src/AutonaScoreboardWidget.py `__init__` | 全キー（UI 初期化時） |
| 読み取り | src/AutonaStatsWidget.py | `last_sets`, `history_sets` |
| 読み取り | src/AutonaBracketView.py | `score`, `bracket` |
| シグナル定義 | src/StateManager.py:23-24 | `state_updated(delta)` |
| シグナル定義 | src/StateManager.py:22 | `state_big_change`（100+ delta） |

## 表 3: OBS HTML 受信フロー

```
ブラウザ load
  ├─ LoadEverything() [globals.js:27]
  ├─ InitAll() [globals.js:84]
  │   ├─ LoadSettings(): global/local settings.json マージ
  │   └─ UpdateData_SocketIO() OR UpdateData() 選択
  │
  ├─ Socket.IO パス
  │   ├─ io(ws://localhost:5000/) 接続
  │   ├─ 'program_state' 受信（初期データ）
  │   ├─ 'program_state_update' 受信（delta + delta_index）
  │   └─ tsh_update CustomEvent → Update(event)
  │
  └─ Fallback: UpdateData() ポーリング
      ├─ program_state.json fetch（64ms）
      ├─ ApplyUpdate(data) で jQuery DOM 更新
      └─ tsh_update CustomEvent
```

各テンプレの `Update()` で jQuery 経由で DOM 更新（`.player-name`, `.score`, `.phase` 等）。
result/* は initResult() 直接呼び出しで Socket.IO 不使用（独立）。

## 表 4: WebEngine 画像生成フロー

```
AutonaThumbnailSettingsWidget.GeneratePreview()
  → main_generate_thumbnail.prepare_data()
      ├─ SettingsManager.Get("thumbnail_config")
      ├─ Template JSON ロード
      └─ StateManager.state 直接読み込み（fallback: program_state.json）
  → ThumbnailRenderer.renderSync(data, config, template)
      ├─ _load_template() — file:// URL で HTML 開く
      ├─ _start_render() — JS initThumbnail() 注入
      ├─ _on_title_changed() — RENDER_READY 検知（100ms delay）
      └─ _finalize_render() — pixmap grab → save_result()
```

## 表 5: React Web UI ↔ StateManager

**読み取り（Socket.IO）:**
```
WebSocket 接続 [websocketConnection.ts]
  → ScoreboardPage useEffect [websocketInit.ts:23-100]
      ├─ 'connect' → playerdb/characters/games リクエスト
      ├─ 'program_state' → tshStateSlice.actions.overwrite()
      └─ 'program_state_update' → addDeltas() → 1000ms 間隔で applySavedDeltas()
          → stateDelta.js applyDelta() で Immer 適用
```

**書き込み（REST + Socket.IO emit）:**
```
React UI 操作
  ├─ Socket.IO emit ('score', 'set', 'stage_strike_*')
  └─ REST API POST (`/scoreboard{N}-team{T}-color-{C}`, `/scoreboard{N}-clear-all` 等)

→ AutonaWebServer ハンドラ受信
  → AutonaWebServerActions（post_score, stage_clicked, team_scoreup/down, load_set 等）
  → StateManager.Set() or signal emit
  → StateManager.SaveState()
      ├─ DeepDiff で差分計算
      └─ adaptive debounce: <10 delta=50ms / 10-99=100ms / 100+=200ms + state_big_change
  → WebServer.on_program_state_update()
      └─ socketio.emit('program_state_update', payload) → 全クライアント配信
```

## 表 6: 同期遅延・順序保証

| 経路 | 遅延 | 順序保証 | 備考 |
|-----|------|--------|------|
| PyQt → StateManager | <1ms | あり（RLock） | 直接 Set |
| StateManager → OBS (Socket.IO) | 50-200ms (debounce) | あり（delta_index） | adaptive debounce |
| StateManager → OBS (poll) | 64ms 固定 | 曖昧 | 順序保証なし、最新 state を fetch |
| StateManager → WebEngine | 0ms | あり | prepare_data() 時点の snapshot |
| StateManager → React (Socket.IO) | 50-200ms (debounce) + 1000ms (batch) | あり（delta_index） | **最悪 1250ms** |
| React → StateManager | ~50ms | あり | API/Socket → AutonaWebServerActions → Set |
| 系統間ラウンドトリップ | ~50-1250ms | 曖昧（複数経路） | PyQt/React 同時操作で順序不定 |

## 表 7: StateManager シグナル接続一覧

| シグナル | 定義 | 接続先 | 条件 |
|---------|------|--------|-----|
| `state_updated(delta)` | StateManager.py:23 | `AutonaWebServer.on_program_state_update()` | delta 1-100 件 |
| `state_updated(delta)` | 同上 | `AutonaStatsWidget._onStateUpdated()` | 統計 UI 更新 |
| `state_big_change` | StateManager.py:22 | `AutonaWebServer.ws_program_state()` | 100+ delta |
| `state_big_change` | 同上 | `AutonaStatsWidget._onStateChanged()` | フル統計再計算 |

## B-b で検出された事実ベースの問題

### Issue 1: `score.upset_factor` の二重定義
- 所在: `src/AutonaStatsUtil.py:177-180`
- `StateManager.Set(f"score.{self.scoreboardNumber}.upset_factor", ...)` — 番号付き
- `StateManager.Set(f"score.upset_factor", 0)` — 番号なし（L180）
- 互換性意図不明

### Issue 2: `commentary.{N}` のインデックス欠番
- `AutonaCommentaryWidget.py:RemoveExtraCommentators()`: Unset 時にインデックスを詰めない
- 1, 3, 5 の欠番が発生しうる
- OBS/React が連続インデックス前提なら index out of bounds 可能

### Issue 3: StateManager.state の lock なし参照
- `src/AutonaWebServerActions.py:41` で `StateManager.state` 直接返却
- `GetFullState()` でロック取得なし → 設計上のレースコンディション（実害は低い）

### Issue 4: 同期遅延の複合効果
- React Web UI の操作は最大 1250ms 遅延（debounce 200ms + batch 1000ms）
- 高頻度操作で順序入れ替わり可能性

### Issue 5: OBS HTML の poll 順序保証なし
- Socket.IO なし環境で program_state.json ポーリング時、複数 delta が 1 ファイル fetch に圧縮
- 順序保証はファイル内 path 順（不定）

---

# Phase B-d: 設定の散在状況

## 表 1: 設定保存先一覧

| 設定種別 | 保存先 | 形式 | 所有クラス | ユーザー編集可? |
|---------|------|------|----------|--------------|
| 一般設定（キー・バリュー） | `./user_data/settings.json` | JSON | SettingsManager | △（直接編集・UI 双方） |
| 選手 DB | `./user_data/local_players.json` | JSON | AutonaPlayerDB | ○ |
| ウィンドウ状態（geometry） | QSettings（Windows Registry 相当） | バイナリ | Window(QMainWindow) | × |
| ウィンドウ状態（dockWidgets） | QSettings + `windowState_v8` | バイナリ | Window(STATE_VERSION=8) | × |
| ホットキー（ユーザーカスタム） | `./user_data/settings.json` キー `hotkeys` | JSON | AutonaHotkeys | ○ |
| ホットキー（デフォルト） | `AutonaHotkeys.py` 内ハードコード | Python Code | AutonaHotkeys | × |
| レイアウト（グローバル設定） | `layout/settings.json` | JSON | globals.js (LoadSettings) | △ |
| レイアウト（ローカル設定） | `layout/{template}/settings.json` | JSON | globals.js (LoadSettings) | △ |
| サムネイルテンプレ定義 | `assets/thumbnail_base/thumbnail_types/{type}.json` | JSON | （定義のみ） | × |
| ゲームアセット定義 | `user_data/games/{game}/{asset_type}/config.json` | JSON | AutonaGameAssetManager | × |
| ルールセット | `assets/rulesets.json` + `user_data/rulesets.json` | JSON | tournament provider | △ |

## 表 2: SettingsManager の主要キー（抜粋）

| キー | 用途 | 書き込み元 | 読み込み元 |
|------|-----|-----------|-----------|
| `light_mode` | ライトモード有効化 | Autona.py:917 | Autona.py, ui_constants.py |
| `program_language` | UI 言語 | Autona.py:482, 490 | AutonaLocaleHelper.py |
| `game_asset_language` | ゲームアセット言語 | Autona.py:508, 516 | AutonaLocaleHelper.py |
| `general.disable_thumbnail_widget` | サムネ生成 UI 無効化 | （読み込みのみ） | Autona.py |
| `general.disable_autoupdate` | 自動更新無効化 | AutonaScoreboardWidget.py:324, 1475 | AutonaScoreboardWidget.py |
| `general.disable_overwrite` | プレイヤー DB 上書き禁止 | （読み込みのみ） | AutonaPlayerDB.py 等 |
| `general.theme_color` / `accent_color` | テーマ色 | AutonaTournamentInfoWidget.py:313, 318 | AutonaTournamentInfoWidget.py |
| `general.team_1/2_default_color` | チームデフォルト色 | （読み込みのみ） | AutonaScoreboardWidget.py |
| `general.webserver_port` | Web サーバーポート | （読み込みのみ） | AutonaWebServer.py |
| `TOURNAMENT_URL` | トーナメント URL | AutonaTournamentDataProvider, WebServerActions | 複数 |
| `bsky_account.*` | Bluesky アカウント | （読み込みのみ） | AutonaBskyHelper.py 等 |
| `display_options.*` | 表示オプション | AutonaCommentaryWidget, ScoreboardWidget | 各 UI |
| `hotkeys.*` | ホットキー設定 | AutonaHotkeys.py | AutonaHotkeys.py |
| `thumbnail_config.*` | サムネイル生成設定 | AutonaThumbnailSettingsWidget | 複数 |
| `stream_channels` | 配信チャンネルリスト | AutonaScoreboardWidget.py:1132 | 同上 |

## 表 3: layout/ 設定ファイル構造

| パス | 役割 | マージ順序 | 設定キー例 |
|------|-----|----------|----------|
| `layout/settings.json` | グローバル | 3 番目（最優先） | `assets`, `japanese_transcription`, `automatic_theme` |
| `layout/{template}/settings.json` | テンプレ ローカル | 1 番目（最低優先） | `assets`, `icon` |
| `layout/include/globals.js LoadSettings()` | マージ実装 | `_.defaultsDeep(window.settings, file_settings, global_settings)` | |

## 表 4: user_data/ 一覧

| ファイル | 所有クラス | 形式 | 用途 |
|---------|----------|------|-----|
| `user_data/settings.json` | SettingsManager | JSON | 全般設定 |
| `user_data/local_players.json` | AutonaPlayerDB | JSON 配列 | ローカル選手 DB |
| `user_data/local_players.csv` | AutonaPlayerDB | CSV | 旧形式（自動削除） |
| `user_data/rulesets.json` | tournament provider | JSON | ルールセットキャッシュ |
| `user_data/games/{game}/{asset_type}/config.json` | AutonaGameAssetManager | JSON | アセット定義 |

## 表 5: ホットキー設定

| 項目 | 保存先 | 形式 |
|------|------|----------|
| デフォルト定義 | `src/AutonaHotkeys.py` クラス変数 `keys` | Python dict |
| ユーザーカスタム | `./user_data/settings.json` キー `hotkeys` | JSON |
| マージ | `AutonaHotkeys.LoadUserHotkeys()` | `deep_clone() + update()` |

## 表 6: レイアウト復元（Window State）

| 項目 | 保存先 | キー |
|------|------|------|
| ウィンドウサイズ・位置 | QSettings（レジストリ） | `geometry` |
| ドックウィジェット配置 | QSettings（レジストリ） | `windowState_v8` |
| 保存実装 | `Autona.py:820-821` | `saveGeometry()`, `saveState()` |
| 復元実装 | `Autona.py:703, 706-707` | `restoreGeometry()`, `restoreState()` |

## 表 7: 設定の重複・不整合事例

| 事例 | 内容 |
|------|-----|
| ホットキー定義 | `AutonaHotkeys.keys`（ハードコード）+ `hotkeys`（SettingsManager）に分散 |
| テーマ色 | `general.theme_color` / `accent_color` を SettingsManager に保存。一方で ui_constants.py / themes.js にも別値で定義 |
| アセット設定 | `layout/{template}/settings.json` + `thumbnail_config.*`（SettingsManager） |
| ウィンドウ状態 | QSettings + SettingsManager に役割分散（前者 = UI 状態、後者 = 機能設定） |
| トーナメント URL | `TOURNAMENT_URL`（永続）+ `current_url`（メモリキャッシュ） |

---

# Phase B-e: テーマ・色トークンの統合可能性

## 表 1: トークン対応マップ（DARK）

| 役割 | PyQt (ui_constants.py) | React (themes.js) | 一致? |
|------|----------------------|-------------------|---------|
| canvas | `#0A0A0A` | `#0A0A0A` | ✓ |
| bg_primary | `#121212` | `#121212` | ✓ |
| bg_secondary | `#1A1A1A` | `#1A1A1A` | ✓ |
| bg_tertiary | `#252524` | `#252524` | ✓ |
| bg_quaternary | `#2F2F2D` | `#2F2F2D` | ✓ |
| border_subtle | `#252524` | `#252524` | ✓ |
| border_default | `#393936` | `#393936` | ✓ |
| border_strong | `#55534E` | `#55534E` | ✓ |
| text_primary | `#F0EAE0` | `#F0EAE0` | ✓ |
| text_secondary | `#B5ADA0` | `#B5ADA0` | ✓ |
| text_muted | `#85807A` | `#85807A` | ✓ |
| text_label | `#A29A90` | `#A29A90` | ✓ |
| text_disabled | `#524F4A` | `#524F4A` | ✓ |
| brand | `#DC143C` | `#DC143C` | ✓ |
| brand_subtle | `#2A1418` | `#2A1418` | ✓ |
| **accent** | **`#8A1A2C` (クリムゾン)** | **`#F5A545` (オレンジ)** | **✗** |
| accent_hover | `#A62238` | `#FFB960` | ✗（accent 連動） |
| accent_subtle | `#33121A` | `#2F2820` | ✗（accent 連動） |
| gold | `#F0C45A` | `#F0C45A` | ✓ |
| gold_subtle | `#2E2A1E` | `#2E2A1E` | ✓ |
| positive | `#6EC77A` | (未定義) | — |
| negative | `#DC143C` | (brand で代用) | △ |
| on_accent | `#FFFFFF` | `#121212` (light モード時) | ✗ |

## 表 2: トークン対応マップ（LIGHT）

| 役割 | PyQt | React | 一致? |
|------|------|-------|---------|
| canvas | `#E8E7E4` | `#E8E7E4` | ✓ |
| bg_primary | `#F4F3F0` | `#F4F3F0` | ✓ |
| bg_secondary | `#FFFFFF` | `#FFFFFF` | ✓ |
| bg_tertiary | `#EAE8E3` | `#EAE8E3` | ✓ |
| bg_quaternary | `#D8D5CE` | `#D8D5CE` | ✓ |
| border_subtle/default/strong | `#E0DDD6/#C8C4BB/#9E968B` | 同 | ✓ |
| text_primary 〜 disabled | 全 5 種 | 全一致 | ✓ |
| brand | `#C41232` | `#C41232` | ✓ |
| brand_subtle | `#F5E0E4` | `#F5E0E4` | ✓ |
| **accent** | **`#8A1A2C`** | **`#A06314`** | **✗** |
| accent_hover | `#A62238` | `#B57018` | ✗ |
| accent_subtle | `#F6D8DD` | `#F2E8D5` | ✗ |
| gold | `#8E6812` | `#8E6812` | ✓ |
| gold_subtle | `#F0E8D0` | `#F0E8D0` | ✓ |
| positive | `#2E8C3E` | (未定義) | — |
| on_accent | `#FFFFFF` | `#F4F3F0` | ✗ |

## 表 3: 役割が片方にしかないトークン

| トークン | 所在 | 用途 |
|---------|------|------|
| positive | PyQt のみ（DARK `#6EC77A` / LIGHT `#2E8C3E`） | 成功状態。React 側は success: gold で代用 |
| p1color, p2color | React themes.js palette.autona のみ（`#DC143C`, `#3D8BE8`） | スコアボード用プレイヤー色。PyQt は AutonaColorButton で動的生成 |

## 表 4: フォント・寸法トークン対応

| 対象 | PyQt | React | 一致? |
|------|------|-------|------|
| 主フォント | FONT_FAMILY = "LINE Seed JP" | fontFamily に "LINE Seed JP" 第 1 位 | ✓ |
| 等幅フォント | FONT_FAMILY_MONO = "JetBrains Mono" | code 要素 "JetBrains Mono" | ✓ |
| Condensed | FONT_FAMILY_CONDENSED = "Roboto Condensed" | スタックに含まれず | ✗ |
| フォントサイズ | FONT_MD=11pt, FONT_LG=13pt | fontSize: 13px | △（単位差） |
| 枠線半径 | RADIUS_SM=3, MD=5, LG=8 | shape.borderRadius: 4 | ✗（粒度差） |
| スペース | SPACE_XS=2, SM=4, MD=8 等 | MUI spacing() (8px=1) | △（方式差） |
| アイコン | ICON_SM=24, MD=32, LG=64 | sx で個別指定 | △ |
| ボタン高 | BTN_SM=24, MD=28, LG=36 | MUI default | ✗ |

## 表 5: テーマ切替の同期状況

| 項目 | PyQt | React | 同期? |
|------|------|-------|------|
| 切替トリガー | `SettingsManager.Get("light_mode")` | index.js: darkTheme 固定 | ✗ |
| 切替時の動作 | `notify_theme_change()` で listeners コールバック | （実装なし） | ✗ |
| ライトモード状態 | SettingsManager 永続化 | localStorage 未実装 | ✗ |
| OBS layout 連携 | layout/main.css は static :root + runtime override（推定） | OBS 非対象 | — |

**注:** React 側は現状 darkTheme 固定。ライトモード切替の同期インフラは React 側未実装。

## 表 6: アクセント色の統合影響範囲

PyQt `#8A1A2C` ⇄ React `#F5A545` どちらに寄せるかを検討した場合:

| 対象 | 用途 | PyQt 参照 | React 参照 |
|------|------|----------|-----------|
| accent | ボタンホバー、タブ選択、フォーカス | QSS 内 34 行（直接） | themes.js 内 24 箇所 |
| accent_hover | ボタン押下、ホバー強調 | QSS 内 8 行 | MUI accentHover |
| accent_subtle | 選択背景 | QSS 内 6 行 | selection 等 |
| **合計** | | **48 行以上**（global_qss 関数内） | **palette + 複数 component override** |

OBS `layout/main.css` `--accent-color: #f0a500` は PdM 方針で分離（統合対象外）。

## 表 7: 命名規則の不一致

| パターン | PyQt | React | 対応 |
|---------|------|-------|------|
| snake_case vs camelCase | `border_subtle` | `borderSubtle` | 言語慣例（プログラム的に変換可能） |
| キー名対応 | 18/20 トークンで対応 | 同 | 容易に紐付け可能 |
| hex 大文字小文字 | 大文字 | 大文字 | 完全一致 |

## 表 8: 値の不一致箇所の詳細

| 不一致箇所 | 影響度 | 変更の複雑さ |
|-----------|-------|-----------|
| accent (#8A1A2C vs #F5A545) | 高 | 片方統一なら全 button/tab/input override 必要 |
| accent_hover/subtle | 中 | accent 統一時に自動解決 |
| on_accent | 低 | ライトモード対比は既に実装 |
| positive | 低 | React に追加可能 |
| LIGHT accent (#8A1A2C vs #A06314) | 高 | ただし React ライトモード未実装のため影響限定 |

## 最終集計（統合可能性）

| 領域 | 完全一致 | 値が異なる | 未実装/片側のみ |
|------|---------|----------|--------------|
| 背景色（bg_*） | 5/5 | - | - |
| 枠線色（border_*） | 3/3 | - | - |
| テキスト色（text_*） | 5/5 | - | - |
| ブランド色（brand/brand_subtle） | 2/2 (dark), 2/2 (light) | - | - |
| アクセント色（accent 一式） | - | 3/3 (dark), 3/3 (light) | - |
| ゴール色 | 2/2 | - | - |
| ポジティブ色 | - | - | 1/2（React 未定義） |
| フォント | 2/3 | - | 1/3（Condensed） |
| テーマ同期 | - | - | 1/2（React 切替未実装） |
| **合計トークン** | **17/20 (DARK)** | **3/20 (accent)** | **1/20 (positive)** |

## 参考: ファイル位置

- PyQt: `src/ui_constants.py` L53-105（palette）, L208-565（global_qss）
- React: `web/src/themes.js` L8-52（tokens）, L73-233（buildTheme）
- OBS: `layout/main.css` L46-62（:root、分離対象）
- 切替実装:
  - PyQt: `src/Autona.py`（light_mode toggle + notify_theme_change）
  - React: `web/src/index.js`（darkTheme 固定）

---

# Phase B-c: 拡張時の修正範囲

## シナリオ 1: 新ゲーム追加（例: 新格ゲー）

**触るファイル数: 5〜7**

| ファイル | 変更内容 | 必須? |
|---------|---------|------|
| `layout/scoreboard/{game}.html` | ゲーム別 HTML（`<body class="game名">`） | ○ |
| `layout/scoreboard/index.css` | `.{game} { ... }` 分岐 | ○ |
| `layout/scoreboard/index.js` | 必要に応じて分岐（多くの場合不要） | △ |
| `assets/games/{game}/base_files/config.json` | ゲーム定義（キャラ・ステージ等） | ○ |
| `assets/games/{game}/base_files/logo.png` | ゲームロゴ | ○ |
| `assets/games/{game}/base_files/` | キャラ画像等の追加アセット | △ |
| Web UI Redux tshGames | Socket.IO 自動配信。コード変更不要 | ✗ |

**ボトルネック**: `layout/scoreboard/index.css`（ゲーム数増加で CSS セレクタ増加）

## シナリオ 2: 新 OBS スコアボードレイアウト（例: 新デザイン）

**触るファイル数: 5**

| ファイル | 変更内容 | 必須? |
|---------|---------|------|
| `layout/scoreboard_{name}/index.html` | テンプレート HTML | ○ |
| `layout/scoreboard_{name}/index.css` | レイアウト専用 CSS | ○ |
| `layout/scoreboard_{name}/index.js` | Start() / Update() 実装、または空 | ○ |
| `layout/scoreboard_{name}/*_preview.png` | プレビュー画像 | ○ |
| Python 側 | 自動スキャン機能あれば自動追加 | ✗ |

## シナリオ 3: 新リザルト画像レイアウト（例: TOP32）

**触るファイル数: 5**

| ファイル | 変更内容 | 必須? |
|---------|---------|------|
| `src/result/ResultRenderer.py` L16-24 | LAYOUTS 辞書に追加（1 行） | ○ |
| `layout/result_top32/result.html` | 新規 HTML | ○ |
| `layout/result_top32/result.css` | 新規 CSS | ○ |
| `layout/result_top32/result.js` | 新規 JS（または不要） | △ |
| `src/AutonaResultSettingsWidget.py` L200-208 | layoutCombo.addItem() | ○ |

## シナリオ 4: 新サムネイルテンプレ（例: type_c）

**触るファイル数: 6**

| ファイル | 変更内容 | 必須? |
|---------|---------|------|
| `src/thumbnail/ThumbnailRenderer.py` L39-42 | TEMPLATE_SUBDIRS に追加（1 行） | ○ |
| `layout/thumbnail_type_c/thumbnail.html` | 新規 HTML | ○ |
| `layout/thumbnail_type_c/thumbnail.css` | 新規 CSS | ○ |
| `layout/thumbnail_type_c/thumbnail.js` | 新規 JS（initThumbnail() 実装） | ○ |
| `assets/thumbnail_base/thumbnail_types/type_c.json` | テンプレ設定 | ○ |
| `src/AutonaThumbnailSettingsWidget.py` L151-154 | _template_options に追加 | ○ |

## シナリオ 5: 新ドックウィジェット（例: 試合タイマー）

**触るファイル数: 3〜5**

| ファイル | 変更内容 | 必須? |
|---------|---------|------|
| `src/AutonaMatchTimerWidget.py` | 新ウィジェット | ○ |
| `src/layout/AutonaMatchTimer.ui` | .ui ファイル | △ |
| `src/Autona.py` L277-410 | import + addDockWidget + tabifyDockWidget + STATE_VERSION インクリメント | ○ |
| `src/ui_constants.py` | スタイル追加 | △ |
| `src/StateManager.py` | 初期化呼び出し | △ |

**ボトルネック**: `STATE_VERSION` のインクリメント忘れで既存ウィジェット非表示。

## シナリオ 6: 新 State キーパス（例: score.{N}.referee）

**触るファイル数: 1〜3**

| ファイル | 変更内容 | 必須? |
|---------|---------|------|
| `src/StateManager.py` | 動的キーパスのため変更不要 | ✗ |
| Web UI Redux Slice | state 構造拡張（必要に応じて） | △ |
| OBS HTML テンプレ | `Update()` 内で参照（自動対応） | △ |

## シナリオ 7: 新ホットキー

**触るファイル数: 3〜4**

| ファイル | 変更内容 | 必須? |
|---------|---------|------|
| `src/AutonaHotkeys.py` L98-102 | keys 辞書 + Signal 定義追加 | ○ |
| `src/Settings/AutonaSettingsWindow.py` | 設定 UI（要確認） | △ |
| メニューバー（`src/Autona.py` L433-644） | QAction + Signal connect | △ |
| 受け取り側ウィジェット | `signals.{name}.connect(...)` | ○ |

## まとめ表

| シナリオ | ファイル数 | 主な変更箇所 |
|--------|---------|-----------|
| 新ゲーム | 5〜7 | layout/scoreboard/, assets/games/ |
| 新 OBS レイアウト | 5 | layout/scoreboard_*/ + preview |
| 新リザルトレイアウト | 5 | ResultRenderer.LAYOUTS, layout/result_*/, UI widget |
| 新サムネイルテンプレ | 6 | ThumbnailRenderer.TEMPLATE_SUBDIRS, layout/thumbnail_*/, JSON, UI |
| 新ドックウィジェット | 3〜5 | src/Autona*.py, src/Autona.py (STATE_VERSION) |
| 新 State キーパス | 1〜3 | StateManager（不要）、Redux、OBS |
| 新ホットキー | 3〜4 | AutonaHotkeys.py, settings, widget |

---

# 付録: 比較対象アプリのデザインルール（OBS Studio / ClickUp）

## OBS Studio

### UI 実装方式のレイヤリング
ネイティブ UI（Qt/C++ による QMainWindow 中心のドックウィジェット）と Web UI（Browser Source 経由で Chromium Embedded Framework を使用）が共存。Qt フロントエンドはアプリ制御（ソース管理、出力設定）、Browser Source は配信画面上への HTML5 コンテンツ埋め込み（アニメーション、スコアボード）に特化。画像/動画レンダリングはネイティブ側（Direct3D/OpenGL/Metal）、Web コンテンツは独立して CEF が管理。

### テーマ・デザインシステム
QSS（Qt Style Sheets / CSS 2 サブセット）+ フォルダ内カスタム画像。デフォルトテーマは Yami。Dark/Light は OS レベル（macOS System、Linux Qt6 native）を活用。固定領域 = Qt フレームワーク自体、カスタマイズ領域 = QSS + SVG アイコンセット。

### 拡張性
ユーザー向けテンプレートは厳密には備えていない。代わりに plugin オブジェクト（sources/outputs/encoders/services）を C/C++ で実装。Browser Source + CEF でカスタム HTML/JS は「ソース」として読み込める（公式拡張フレームワークではない）。

### 状態管理
libobs の Signal/Procedure システム。オブジェクトが signal を emit、他オブジェクトが slot で購読。Properties System で plugin が `obs_properties_t` で設定 UI を定義し、Qt フロントエンドが自動解釈してウィジェット生成。JSON 永続化（`obs_data_t`）。

### コンポーネント粒度
中粒度。Qt ドックウィジェット（Sources / Mixer / Scene panel など）が主要単位。Plugin API（`obs_frontend_get_main_window`）で追加可能。Component Library 的な階層化はなく、plugin インターフェースが設計の核。

## ClickUp

### UI 実装方式のレイヤリング
Web-only SaaS（React/Angular/TypeScript）。ネイティブ UI レイヤーは存在しない。React SPA がバックエンド（Ruby on Rails + PostgreSQL）と REST API/Webhook で通信。テーブル・タイムライン・Whiteboard は全て DOM 上で実行。Desktop/Mobile は React メディアクエリで吸収。

### テーマ・デザインシステム
Enterprise Plans で Workspace ロゴ・カラーパレット・カスタムドメインをホワイトレーベル可能。デザインシステムは内部で Component + Tokens 管理（Figma 連携推奨）。Color picker + dropper で Workspace 単位のカラー定義。Workspace 管理者が logo（72x72px）と rectangle（232x48px）の PNG をアップロード。

### 拡張性
Webhooks + REST API で外部システム連携。Integrations（Slack, Teams, GitLab 等）は Build-in。White-label（Enterprise）で custom domain + branding 可能だが、UI Component レベルのテンプレートは未提供。Frontend plugin は公式未対応。

### 状態管理
Redux Toolkit（推定）。API call → state 更新 → re-render。JSONB（PostgreSQL）で flexible custom field structure。リアルタイム更新は WebSocket（推定）で dashboard/task view 即座反映。

### コンポーネント粒度
細粒度。React Component の再利用性重視。Task Card / List View / Timeline / Whiteboard（tldraw SDK）。Design System Guide で Foundations（color, typography, spacing tokens）→ Components → Implementation を階層化。AI による component documentation 自動生成。

## 比較表

| 観点 | OBS Studio | ClickUp | Autona 現状 |
|-----|-----------|---------|-----------|
| ネイティブ vs Web | 混在（Qt + CEF Browser Source） | Web-only（React SPA） | 混在（PyQt + React + WebEngine） |
| 状態管理 | Signal/Procedure + JSON | Redux + REST API | StateManager（DeepDiff デルタ） + Redux |
| テーマシステム | QSS + SVG、OS native 活用 | Workspace ホワイトレーベル + tokens | ui_constants.py / themes.js / main.css の 3 系統並存 |
| 拡張性 | Plugin System (C/C++) + Browser Source | Webhooks + REST API | layout/ テンプレ + Hotkey + ドックウィジェット |
| コンポーネント粒度 | 中（dock panels） | 細（React components） | 中（dock + 個別 widget クラス） |
| デザイン定義 | QSS + images folder | Design tokens + Design system doc | 中央管理（ui_constants）+ ハードコード混在 |

## 出典

**OBS Studio**:
- https://docs.obsproject.com/backend-design
- https://docs.obsproject.com/reference-frontend-api
- https://obsproject.com/kb/themes-guide
- https://github.com/obsproject/obs-browser
- https://2021.desosa.nl/print/projects/obsstudio/posts/obs-architectural-decisions/

**ClickUp**:
- https://www.programstrategyhq.com/post/technical-architecture-teardown-clickup
- https://developer.clickup.com/
- https://help.clickup.com/hc/en-us/articles/6326176355991-Custom-Workspace-theme-avatar-and-branding
- https://consultevo.com/clickup-design-system-management/

