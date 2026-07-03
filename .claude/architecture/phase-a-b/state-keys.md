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
│   │       ├── score                W R AutonaScoreboardWidget / AutonaIndividualGameTracker
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
│   ├── top_n                        W R AutonaScoreboardWidget.ChangeSetData / AutonaStatsWidget
│   ├── stream_url                   W R AutonaScoreboardWidget(QLineEdit) / AutonaStatsWidget
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
│   ├── stages                       W R AutonaIndividualGameTracker
│   │   └── {I}                      W   (ステージデータ dict)
│   │       ├── t1_win               W   AutonaIndividualGameTracker
│   │       ├── t2_win               W   AutonaIndividualGameTracker
│   │       └── tie                  W   AutonaIndividualGameTracker
│   ├── last_sets
│   │   ├── 1                        W R AutonaScoreboardWidget.__init__ / AutonaStatsUtil / AutonaStatsWidget
│   │   │   └── {I}                  W   AutonaStatsUtil (set data dict)
│   │   └── 2                        W R AutonaScoreboardWidget.__init__ / AutonaStatsUtil / AutonaStatsWidget
│   │       └── {I}                  W   AutonaStatsUtil
│   ├── history_sets
│   │   ├── 1                        W R AutonaScoreboardWidget.__init__ / AutonaStatsUtil / AutonaStatsWidget
│   │   │   └── {I}                  W   AutonaStatsUtil
│   │   └── 2                        W R AutonaScoreboardWidget.__init__ / AutonaStatsUtil / AutonaStatsWidget
│   │       └── {I}                  W   AutonaStatsUtil
│   ├── recent_sets                  W R AutonaStatsUtil
│   │   ├── sets                     R   AutonaStatsWidget
│   │   └── request_time             R   AutonaStatsUtil
│   └── upset_factor                 W R AutonaStatsUtil / AutonaStatsWidget
├── ruleset                          W R AutonaScoreboardStageWidget / AutonaScoreboardWidget / AutonaWebServerActions
├── bracket_type                     W R AutonaScoreboardWidget / AutonaStatsUtil
└── upset_factor                     W   AutonaStatsUtil (スコアボード番号なし — 要確認)
```

### `game`

選択中ゲーム情報。AutonaGameAssetManager が一括書き込みする。

```
game                                 W R AutonaGameAssetManager / AutonaWebServerActions
├── name                             (str: ゲーム名)
├── smashgg_id                       (int: start.gg ID)
├── codename                         (str: 内部コード名)
├── logo                             (str: ロゴパス)
├── defaults                         R   AutonaScoreboardWidget / AutonaPlayerListWidget（Commentary は 2026-07 リワークで参照廃止）
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
├── players                          (base for AutonaPlayerList)
│   └── (以下 player_list と同構造)
└── (team データ)                    R   AutonaBracketView (score.{N}.team.{T} 経由で表示)
```

### `commentary`

実況・解説者情報。AutonaCommentaryWidget が管理（2026-07 リワークでスキーマ刷新、
正本は `src/AutonaCommentaryModel.py`）。スロット固定 8 名・欠番なし。
旧スキーマは起動時 `NormalizeState` でマイグレーション（custom_textbox → note、他は破棄）。

```
commentary                           W R  AutonaCommentaryWidget / AutonaWebServerActions
└── {1..8}                                (スロット固定・常時 8 エントリ)
    ├── name, team, real_name, twitter
    ├── role                              ("実況"|"解説"|"選手"|"主催"|"")
    ├── note                              (自由コメント)
    └── display                           (int。0=非表示、1..4=表示順。チェック順に採番、左から表示)
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
├── themeColor                       W   AutonaTournamentInfoWidget (SettingsManager から初期化)
└── accentColor                      W   AutonaTournamentInfoWidget (SettingsManager から初期化)
```

### `notes`

メモ情報。AutonaNotesWidget が管理。

```
notes
└── notes_widget_{N}                 W   AutonaNotesWidget
    ├── title                        W   AutonaNotesWidget (QLineEdit)
    └── contents                     W   AutonaNotesWidget (QPlainTextEdit)
```

### `player_list`

選手リスト。AutonaPlayerListWidget が管理。

```
player_list                          W   AutonaPlayerListWidget.__init__
└── slot
    └── {I}                          D   AutonaPlayerList.RemoveSlot
        ├── name                     W   AutonaPlayerListSlotWidget (QLineEdit)
        ├── score                    W   AutonaPlayerListSlotWidget (QSpinBox)
        └── player
            └── {M}                  D   AutonaPlayerListSlotWidget.SetPlayersPerTeam
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
| `self.path` (AutonaScoreboardPlayerWidget) | `{base}.slot.{I}.player.{M}` | AutonaPlayerListSlotWidget から（commentary パスは 2026-07 リワークで廃止） |
| `self.path` (AutonaTeamPlayerWidget) | `team_battle.team.{T}.player.{N}` | AutonaTeamBattleWidget から |
| `self.base` (AutonaPlayerList) | `bracket.players` | AutonaBracketWidget から |
| `self.base` (AutonaPlayerList) | `player_list` | AutonaPlayerListWidget から（デフォルト値） |
| `element.objectName()` | Qt Designer で定義された名前 | .ui ファイル依存。実行時まで不確定 |
| `self.settingsBase` (LayoutOptionsWidget) | レイアウト名 | AutonaLayoutOptionsWindow から渡される |

---

## 直接 state アクセス（API 経由でない）

| ファイル | 行 | アクセス | 備考 |
|---------|-----|---------|------|
| `AutonaWebServerActions.py:41` | `StateManager.state` (R) + `StateManager.deltaIndex` (R) | WebSocket 初回接続時の全状態返却。ロックなし ⚠️ |

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
