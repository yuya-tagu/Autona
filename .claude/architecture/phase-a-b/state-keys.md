# StateManager キーパス Read/Write マップ

調査日: 2026-04-08
対象: `src/**/*.py`
方法: `grep -n "StateManager\.(Set|Get|Unset)"` + コンテキスト読解。動的パスは実際の引数を追跡して展開。

---

## API サマリー

| メソッド | 用途 | スレッドセーフ |
|---------|------|-------------|
| `StateManager.Set(key, value)` | 状態書き込み（ドット区切りキーパス） | Yes (RLock) |
| `StateManager.Get(key, default)` | 状態読み取り | Yes (via deep_get) |
| `StateManager.Unset(key)` | キー削除 | Yes (RLock) |
| `StateManager.BlockSaving()` | ファイル出力抑制（ネスト可） | No (カウンタのみ) |
| `StateManager.ReleaseSaving()` | ブロック解除。カウンタ0で SaveState 発火 | No |
| `StateManager.SaveBlock()` | context manager (Block + Release) | — |
| `StateManager.state` | 直接参照（AutonaWebServerActions のみ） | ⚠️ ロックなし |

---

## キーパスツリー

凡例: **W** = Set（書き込み）、**R** = Get（読み取り）、**D** = Unset（削除）

### `score`

スコアボード。現在は単一スコアボードのみ対応。`{N}` は常に `1`（複数スコアボード機能を廃止したが、キーパスは互換性のため残している）。

```
score                                D   AutonaScoreboardManager.__init__
├── {N}                              W R AutonaScoreboardWidget.__init__ / AutonaWebServerActions
│   ├── team
│   │   └── {1|2}
│   │       ├── teamName             W R AutonaScoreboardWidget(QLineEdit) / AutonaWebServerActions
│   │       ├── {QLineEdit.objectName}  W  AutonaScoreboardWidget(動的: team列のQLineEdit)
│   │       ├── {QCheckBox.objectName}  W  AutonaScoreboardWidget(動的: team列のQCheckBox, e.g. losers)
│   │       ├── score                W R core/Actions / core/GameTrackerService（+ 未移行のスピンボックス経路: セット読込 / ClearScore / SwapTeams）
│   │       ├── color                W   AutonaScoreboardWidget.SetColor / ChangeSetData
│   │       ├── logo                 W   AutonaScoreboardWidget.ExportTeamLogo
│   │       └── player
│   │           └── {M}              D   AutonaScoreboardWidget.CommandClearAll
│   │               ├── name         R   AutonaScoreboardWidget / AutonaStatsWidget
│   │               ├── gamerTag     W   AutonaScoreboardPlayerWidget(QLineEdit "name" → objectName)
│   │               ├── team         W   AutonaScoreboardPlayerWidget(QLineEdit "team")
│   │               ├── seed         W R AutonaScoreboardPlayerWidget / AutonaStatsUtil
│   │               ├── id           W R AutonaScoreboardPlayerWidget.ExportPlayerId / AutonaStatsUtil
│   │               ├── city         W R AutonaScoreboardPlayerWidget.ExportPlayerCity
│   │               ├── birthday     W   AutonaScoreboardPlayerWidget(QCheckBox)
│   │               ├── custom_textbox  W  AutonaScoreboardPlayerWidget(QPlainTextEdit)
│   │               ├── online_avatar  W R AutonaScoreboardPlayerWidget.ExportPlayerImages / SwapWith
│   │               ├── avatar       W   AutonaScoreboardPlayerWidget.ExportPlayerImages
│   │               ├── mergedName   W   AutonaScoreboardPlayerWidget.ExportMergedName
│   │               ├── mergedOnlyName  W  AutonaScoreboardPlayerWidget.ExportMergedName
│   │               ├── character    W R AutonaScoreboardPlayerWidget.CharactersChanged / AutonaScoreboardWidget
│   │               │   └── {K}      (dict: name, en_name, assets, skin, variant)
│   │               ├── mains        W   AutonaScoreboardPlayerWidget.CharactersChanged(includeMains)
│   │               ├── country_code W   AutonaScoreboardPlayerWidget.ComboBoxIndexChanged
│   │               ├── state_code   W   AutonaScoreboardPlayerWidget.ComboBoxIndexChanged
│   │               ├── sponsor_logo W D AutonaSponsorHelper.ExportValidSponsors
│   │               ├── sponsor_logos  W D AutonaSponsorHelper.ExportValidSponsors
│   │               │   └── {I}      W   (スポンサーロゴパス)
│   │               └── {QLineEdit.objectName}  W  AutonaScoreboardPlayerWidget(動的: 全QLineEdit)
│   ├── best_of                      W R AutonaScoreboardWidget / AutonaWebServerActions
│   ├── best_of_short_text           W   AutonaScoreboardWidget
│   ├── best_of_text                 W   AutonaScoreboardWidget
│   ├── first_to                     W   AutonaScoreboardWidget
│   ├── first_to_short_text          W   AutonaScoreboardWidget
│   ├── first_to_text                W   AutonaScoreboardWidget
│   ├── match                        W R AutonaScoreboardWidget.ChangeSetData / AutonaWebServerActions
│   ├── {QComboBox.objectName}       W   AutonaScoreboardWidget(動的: scoreColumn の QComboBox)
│   ├── phase                        W R AutonaScoreboardWidget.ChangeSetData / AutonaWebServerActions / AutonaStatsWidget
│   ├── pool                         W R AutonaScoreboardWidget.ChangeSetData / AutonaWebServerActions (フェーズと分離したプール名。オーバーレイは表示時に "phase - pool" を合成)
│   ├── top_n                        W R AutonaScoreboardWidget.ChangeSetData / AutonaStatsWidget
│   ├── set_id                       W   AutonaScoreboardWidget.SetFromSetData
│   ├── auto_update                  W   AutonaScoreboardWidget.SetFromSetData / StopAutonaUpdate
│   ├── station                      W   AutonaScoreboardWidget.SetFromSetData / StopAutonaUpdate
│   ├── station_queue                W   AutonaScoreboardWidget.StationQueueLoaded
│   ├── teamsSwapped                 W   AutonaScoreboardWidget.CommandSwapTeams / SetFromSetData
│   ├── stage_strike                 W D AutonaScoreboardWidget / AutonaStageStrikeLogic / AutonaWebServerActions
│   │   ├── currGame                 (int)
│   │   ├── currPlayer               (int)
│   │   ├── currStep                 (int)
│   │   ├── strikedStages            (list)
│   │   ├── strikedBy                (list)
│   │   ├── stagesWon                (dict)
│   │   ├── stagesPicked             (list)
│   │   ├── selectedStage            (str|None)
│   │   ├── lastWinner               (int|None)
│   │   ├── gentlemans               (bool)
│   │   ├── canUndo                  (bool)
│   │   └── canRedo                  (bool)
│   ├── stages                       W R core/GameTrackerService
│   │   └── {I}                      W   (ステージデータ dict)
│   │       ├── t1_win               W   core/GameTrackerService
│   │       ├── t2_win               W   core/GameTrackerService
│   │       └── tie                  W   core/GameTrackerService
│   ├── last_sets
│   │   ├── 1                        W R core/StateSchema.SeedBoard / core/StatsService / AutonaStatsWidget
│   │   │   └── {I}                  W   core/StatsService (set data dict)
│   │   └── 2                        W R core/StateSchema.SeedBoard / core/StatsService / AutonaStatsWidget
│   │       └── {I}                  W   core/StatsService
│   ├── history_sets
│   │   ├── 1                        W R core/StateSchema.SeedBoard / core/StatsService / AutonaStatsWidget
│   │   │   └── {I}                  W   core/StatsService
│   │   └── 2                        W R core/StateSchema.SeedBoard / core/StatsService / AutonaStatsWidget
│   │       └── {I}                  W   core/StatsService
│   ├── recent_sets                  W R core/StatsService
│   │   ├── sets                     R   AutonaStatsWidget
│   │   └── request_time             R   core/StatsService
│   └── upset_factor                 W R core/StatsService / AutonaStatsWidget
├── ruleset                          W R AutonaScoreboardStageWidget / AutonaScoreboardWidget / AutonaWebServerActions
└── bracket_type                     W R AutonaScoreboardWidget / core/StatsService
```

### `game`

選択中ゲーム情報。AutonaGameAssetManager が一括書き込みする。

```
game                                 W R AutonaGameAssetManager / AutonaWebServerActions
├── name                             (str: ゲーム名)
├── smashgg_id                       (int: start.gg ID)
├── codename                         (str: 内部コード名)
├── logo                             (str: ロゴパス)
├── defaults                         R   AutonaScoreboardWidget（Commentary は 2026-07 リワークで参照廃止）
│   ├── players_per_team             R   上記ウィジェット群
│   └── characters_per_player        R   上記ウィジェット群
├── mods_active                      R   Autona.Window
├── has_stages                       R   AutonaIndividualGameTracker
├── has_variants                     (bool)
├── has_colors                       R   AutonaScoreboardWidget
├── has_modded_content               W   AutonaGameAssetManager
└── (初期化時は空dict)               W   AutonaGameAssetManager.__init__
```

### `bracket`

ブラケット表示情報。AutonaBracketWidget / AutonaBracketView が管理。

```
bracket                              W   AutonaBracketWidget.__init__
├── phase                            W   AutonaBracketWidget.PhaseGroupChanged
├── phaseGroup                       W   AutonaBracketWidget.PhaseGroupChanged
├── bracket
│   ├── progressionsIn               W   AutonaBracketWidget / AutonaBracketView
│   ├── progressionsOut              W   AutonaBracketWidget / AutonaBracketView
│   ├── winnersOnly                  W   AutonaBracketWidget
│   ├── winnersOnlyProgressions      W   AutonaBracketView
│   ├── limitExportNumber            W   AutonaBracketView
│   └── rounds                       W   AutonaBracketView.UpdateBracket
└── (team データ)                    R   AutonaBracketView (score.{N}.team.{T} 経由で表示)
```

### `commentary`

実況・解説者情報。AutonaCommentaryWidget が管理（2026-07 リワークでスキーマ刷新、
正本は `src/AutonaCommentaryModel.py`）。スロット固定 12 名・欠番なし。
旧スキーマは起動時 `NormalizeState` でマイグレーション（custom_textbox → note、他は破棄）。

```
commentary                           W R  AutonaCommentaryWidget / AutonaWebServerActions
└── {1..12}                               (スロット固定・常時 12 エントリ)
    ├── name, team, twitter
    ├── role                              (str 自由入力。補完候補は ROLES の 6 種)
    ├── icon                              (str。""|"mic"|"megaphone"|"phone"。
    │                                      旧 role/roles のアイコンラベルは起動時移行)
    ├── note                              (自由コメント)
    └── display                           (int。0=非表示、1=表示。表示順はスロット順=カード順、
                                           最大 4 名。旧チェック順採番値は起動時に 1 へ移行)
```

### `tournamentInfo`

大会メタデータ。AutonaTournamentInfoWidget が管理。

```
tournamentInfo                       W R
├── tournamentName                   R   AutonaScoreboardWidget (サムネイル生成時)
├── {QDateEdit.objectName}           W   AutonaTournamentInfoWidget (日付フィールド)
├── {QLineEdit.objectName}           W   AutonaTournamentInfoWidget (テキストフィールド)
├── {QSpinBox.objectName}            W   AutonaTournamentInfoWidget (数値フィールド)
├── {key}                            W   AutonaTournamentInfoWidget.UpdateData (API取得データ)
├── themeColor                       W   Autona.py _SeedPanelState (起動時に SettingsManager
│                                        general.theme_color から展開)
│                                     W   大会情報設定パネル「大会カラー」カード
│                                        (AutonaFeatureColors.SetTournamentColor)
└── accentColor                      W   同上 (general.accent_color)
```

### `colors`

機能別カラーの上書き。`src/AutonaFeatureColors.py` が管理（正本は SettingsManager
`colors.{feature}.theme_color / accent_color`）。**明示設定がある feature のみ**が
state に現れ、未設定の feature はレイアウト JS 側で `tournamentInfo` にフォールバックする。

**大会カラーを変更すると、全 feature の同じ kind の上書きが削除される**
（`SetTournamentColor` が SettingsManager / StateManager 両方を Unset）。
フォールバックにより新しい大会カラーが全レイアウトへ即反映される。個別編集すれば再び独立する。

```
colors                               W R
└── {feature}                            board / thumbnail / result / top8 /
    │                                    commentary / stats
    ├── themeColor                   W   Autona.py _SeedPanelState → SeedColorState (起動時)
    │                                W   各設定パネルの「〜の色」カード
    │                                    (AutonaFeatureColors.SetFeatureColor)
    │                                  R OBS レイアウト JS (scoreboard_* / camborder_Universal /
    │                                    bracket_top8* / commentators* / 統計 5 レイアウト)
    └── accentColor                  W R 同上
```

※ サムネイル / リザルトは SocketIO ではなく Python が payload を組むため、
`colors.{feature}` を `main_generate_thumbnail.prepare_data()` /
`main_generate_result.prepare_data()` が読んで `config.theme_color` /
`config.accent_color` に解決する。

### `stream`

配信（大会ではなく配信チャンネル）単位の設定。AutonaStreamSettingsWidget（配信設定パネル）が管理。

```
stream                               W R
├── url                              W R AutonaStreamSettingsWidget (配信先コンボ)
│                                        R AutonaScoreboardWidget.PostToX / AutonaResultSettingsWidget / Web UI (CurrentSet.jsx)
├── title                            W   AutonaStreamSettingsWidget (配信タイトル)
├── orgIconVersion                   W   AutonaStreamSettingsWidget (layout/stream_org_icon.png の mtime)
└── orgThumbVersion                  W   AutonaStreamSettingsWidget (layout/stream_org_thumbnail.png の mtime)
```

**階層の分離**（この 3 つを混ぜないこと）:

| 階層 | パネル | サブツリー | 例 |
|------|--------|-----------|-----|
| 大会 | 大会情報 | `tournamentInfo.*` | 大会名・日付・会場・大会アイコン/バナー |
| 配信 | 配信設定 | `stream.*` | 配信先・配信タイトル・配信団体アイコン/サムネイル |
| 試合 | スコアボード入力 | `score.{N}.*` | 選手・スコア・フェーズ・ラウンド |

`stream.*` は非ゲート（`score.{N}` の held subtree に入れない）。過去に配信先を
`score.{N}.stream_url` に置いていたため、(1) 「反映」を押すまで Web UI に届かない、
(2) 全ボードへミラーすると非表示側のボードまで dirty になる、(3) 起動時の score
サブツリー wipe で消える、という 3 つの問題が同時に出ていた。

`orgIconVersion` / `orgThumbVersion` は `tournamentInfo.logoVersion` と同じ
キャッシュバスト用 mtime。OBS オーバーレイは `/layout/stream_org_icon.png?v={version}`
のように参照する（`layout/` は AutonaWebServer が静的配信）。**0 は未登録**を意味する。

### `notes`

メモ情報。AutonaNotesWidget が管理。

```
notes
└── notes_widget_{N}                 W   AutonaNotesWidget
    ├── title                        W   AutonaNotesWidget (QLineEdit)
    └── contents                     W   AutonaNotesWidget (QPlainTextEdit)
```

### `player_list`（廃止）

旧 PLAYERS 順位ロスター編集（AutonaPlayerListWidget 系）は機能ごと削除済み。
残留キーは `AutonaPlayerBrowserWidget.__init__` が起動時に Unset する。

### `player_editor`

選手一覧パネル（AutonaPlayerBrowserWidget）の編集ダイアログが使う一時パス。
ダイアログ close 時と起動時に Unset される（program_state.json に残さない）。

```
player_editor
└── 1                                W D  _PlayerEditorDialog 内の AutonaScoreboardPlayerWidget
    └── (AutonaScoreboardPlayerWidget と同じ player キー群)
```

### `team_battle`

チームバトル情報。AutonaTeamBattleWidget が管理。

```
team_battle                          D   AutonaTeamBattleWidget.__init__
└── team
    └── {1|2}                        R   AutonaTeamBattleWidget
        └── player
            └── {N}                  D   AutonaTeamBattleWidget.CommandClearAll
                ├── (AutonaTeamPlayerWidget: player キー群)
                ├── dynamic_spinner  W   AutonaTeamPlayerWidget
                ├── active           W   AutonaTeamPlayerWidget
                └── dead             W   AutonaTeamPlayerWidget
```

### `layout_options`

レイアウトオプション。LayoutOptionsWidget が管理。

```
layout_options
└── {settingsBase}                   W   LayoutOptionsWidget
    └── {setting}                    W   (QCheckBox: bool / QLineEdit: str / AutonaColorButton: str / QComboBox: str)
```

### `provider_videogame`

トーナメントプロバイダーのゲーム情報。

```
provider_videogame                   W   AutonaTournamentDataProvider.GameChanged
├── id                               R   AutonaStatsUtil
└── (将来拡張用の空間)
```

### `completed_sets`

完了済みセット。Autona.Window が管理。

```
completed_sets                       W D  Autona.Window
└── {I}                              W    (セットデータ dict)
```

### `streamQueue` / `currentStream`

配信キュー。AutonaStatsUtil が管理。

```
streamQueue                          W   AutonaStatsUtil.UpdateStreamQueue
currentStream                        W   AutonaStatsUtil.UpdateStreamQueue
```

---

## 動的パスの解決表

一部のキーパスはウィジェットのコンストラクタ引数やQtのobjectNameに依存する。

| 変数 | 解決先 | 備考 |
|------|--------|------|
| `self.path` (AutonaScoreboardPlayerWidget) | `score.{N}.team.{T}.player.{M}` | AutonaScoreboardWidget から |
| `self.path` (AutonaScoreboardPlayerWidget) | `player_editor.1` | AutonaPlayerBrowserWidget の編集ダイアログから（close で Unset） |
| `self.path` (AutonaTeamPlayerWidget) | `team_battle.team.{T}.player.{N}` | AutonaTeamBattleWidget から |
| `element.objectName()` | Qt Designer で定義された名前 | .ui ファイル依存。実行時まで不確定 |
| `self.settingsBase` (LayoutOptionsWidget) | レイアウト名 | AutonaLayoutOptionsWindow から渡される |

---

## シグナル接続

| シグナル | 接続先 | トリガー条件 |
|---------|--------|------------|
| `state_big_change` | `WebServer.ws_program_state` | delta 100件超 or LoadState |
| `state_big_change` | `AutonaStatsWidget._onStateChanged` | 同上 |
| `state_updated(delta)` | `WebServer.on_program_state_update` | delta 1-100件 |
| `state_updated(delta)` | `AutonaStatsWidget._onStateUpdated` | 同上 |

---

## 要注意事項

### 1. `score.upset_factor` vs `score.{N}.upset_factor` ⚠️

`AutonaStatsUtil.py:180` で `StateManager.Set(f"score.upset_factor", 0)` が呼ばれている。
一方 `AutonaStatsUtil.py:177` では `StateManager.Set(f"score.{self.scoreboardNumber}.upset_factor", ...)` が呼ばれている。
`score.upset_factor`（番号なし）は意図的かバグか不明。

### 2. Qt objectName 依存の動的キー ⚠️

AutonaScoreboardWidget の team 列、tournamentInfo のフィールドなど、複数箇所で `element.objectName()` をキーパスに使用している。
実際のキー名は `.ui` ファイルに定義されており、コードからは静的に特定できない。
`.ui` ファイルを変更するとキーパスが変わり、既存の保存データとの互換性が壊れる。

### 3. `StateManager.state` の直接参照 ⚠️

`AutonaWebServerActions.py:41` で `StateManager.state` を直接返している。
ロックを取得していないため、SaveState のスレッドとレースコンディションの可能性がある。
実際には Flask リクエストは短時間で完了するため問題になりにくいが、設計上のリスク。

### 4. `commentary.{N}` の欠番 — 解消済み

2026-07 の実況・解説リワークでスロット固定 8 名（常時 1..8 が存在）となり、
欠番の概念自体が消滅した。
