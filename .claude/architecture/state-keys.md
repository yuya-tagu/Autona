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
| `StateManager.state` | 直接参照（TSHWebServerActions のみ） | ⚠️ ロックなし |

---

## キーパスツリー

凡例: **W** = Set（書き込み）、**R** = Get（読み取り）、**D** = Unset（削除）

### `score`

スコアボード。`{N}` はスコアボード番号（1始まり）。

```
score                                D   TSHScoreboardManager.__init__
├── {N}                              W R TSHScoreboardWidget.__init__ / TSHWebServerActions
│   ├── team
│   │   └── {1|2}
│   │       ├── teamName             W R TSHScoreboardWidget(QLineEdit) / TSHWebServerActions
│   │       ├── {QLineEdit.objectName}  W  TSHScoreboardWidget(動的: team列のQLineEdit)
│   │       ├── {QCheckBox.objectName}  W  TSHScoreboardWidget(動的: team列のQCheckBox, e.g. losers)
│   │       ├── score                W R TSHScoreboardWidget / TSHIndividualGameTracker
│   │       ├── color                W   TSHScoreboardWidget.SetColor / ChangeSetData
│   │       ├── logo                 W   TSHScoreboardWidget.ExportTeamLogo
│   │       └── player
│   │           └── {M}              D   TSHScoreboardWidget.CommandClearAll
│   │               ├── name         R   TSHScoreboardWidget / TSHStatsWidget
│   │               ├── gamerTag     W   TSHScoreboardPlayerWidget(QLineEdit "name" → objectName)
│   │               ├── team         W   TSHScoreboardPlayerWidget(QLineEdit "team")
│   │               ├── seed         W R TSHScoreboardPlayerWidget / TSHStatsUtil
│   │               ├── id           W R TSHScoreboardPlayerWidget.ExportPlayerId / TSHStatsUtil
│   │               ├── city         W R TSHScoreboardPlayerWidget.ExportPlayerCity
│   │               ├── birthday     W   TSHScoreboardPlayerWidget(QCheckBox)
│   │               ├── custom_textbox  W  TSHScoreboardPlayerWidget(QPlainTextEdit)
│   │               ├── online_avatar  W R TSHScoreboardPlayerWidget.ExportPlayerImages / SwapWith
│   │               ├── avatar       W   TSHScoreboardPlayerWidget.ExportPlayerImages
│   │               ├── mergedName   W   TSHScoreboardPlayerWidget.ExportMergedName
│   │               ├── mergedOnlyName  W  TSHScoreboardPlayerWidget.ExportMergedName
│   │               ├── character    W R TSHScoreboardPlayerWidget.CharactersChanged / TSHScoreboardWidget
│   │               │   └── {K}      (dict: name, en_name, assets, skin, variant)
│   │               ├── mains        W   TSHScoreboardPlayerWidget.CharactersChanged(includeMains)
│   │               ├── country_code W   TSHScoreboardPlayerWidget.ComboBoxIndexChanged
│   │               ├── state_code   W   TSHScoreboardPlayerWidget.ComboBoxIndexChanged
│   │               ├── sponsor_logo W D TSHSponsorHelper.ExportValidSponsors
│   │               ├── sponsor_logos  W D TSHSponsorHelper.ExportValidSponsors
│   │               │   └── {I}      W   (スポンサーロゴパス)
│   │               └── {QLineEdit.objectName}  W  TSHScoreboardPlayerWidget(動的: 全QLineEdit)
│   ├── best_of                      W R TSHScoreboardWidget / TSHWebServerActions
│   ├── best_of_short_text           W   TSHScoreboardWidget
│   ├── best_of_text                 W   TSHScoreboardWidget
│   ├── first_to                     W   TSHScoreboardWidget
│   ├── first_to_short_text          W   TSHScoreboardWidget
│   ├── first_to_text                W   TSHScoreboardWidget
│   ├── match                        W R TSHScoreboardWidget.ChangeSetData / TSHWebServerActions
│   ├── {QComboBox.objectName}       W   TSHScoreboardWidget(動的: scoreColumn の QComboBox)
│   ├── phase                        W R TSHScoreboardWidget.ChangeSetData / TSHWebServerActions / TSHStatsWidget
│   ├── top_n                        W R TSHScoreboardWidget.ChangeSetData / TSHStatsWidget
│   ├── stream_url                   W R TSHScoreboardWidget(QLineEdit) / TSHStatsWidget
│   ├── set_id                       W   TSHScoreboardWidget.SetFromSetData
│   ├── auto_update                  W   TSHScoreboardWidget.SetFromSetData / StopAutoUpdate
│   ├── station                      W   TSHScoreboardWidget.SetFromSetData / StopAutoUpdate
│   ├── station_queue                W   TSHScoreboardWidget.StationQueueLoaded
│   ├── teamsSwapped                 W   TSHScoreboardWidget.CommandSwapTeams / SetFromSetData
│   ├── stage_strike                 W D TSHScoreboardWidget / TSHStageStrikeLogic / TSHWebServerActions
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
│   ├── stages                       W R TSHIndividualGameTracker
│   │   └── {I}                      W   (ステージデータ dict)
│   │       ├── t1_win               W   TSHIndividualGameTracker
│   │       ├── t2_win               W   TSHIndividualGameTracker
│   │       └── tie                  W   TSHIndividualGameTracker
│   ├── last_sets
│   │   ├── 1                        W R TSHScoreboardWidget.__init__ / TSHStatsUtil / TSHStatsWidget
│   │   │   └── {I}                  W   TSHStatsUtil (set data dict)
│   │   └── 2                        W R TSHScoreboardWidget.__init__ / TSHStatsUtil / TSHStatsWidget
│   │       └── {I}                  W   TSHStatsUtil
│   ├── history_sets
│   │   ├── 1                        W R TSHScoreboardWidget.__init__ / TSHStatsUtil / TSHStatsWidget
│   │   │   └── {I}                  W   TSHStatsUtil
│   │   └── 2                        W R TSHScoreboardWidget.__init__ / TSHStatsUtil / TSHStatsWidget
│   │       └── {I}                  W   TSHStatsUtil
│   ├── recent_sets                  W R TSHStatsUtil
│   │   ├── sets                     R   TSHStatsWidget
│   │   └── request_time             R   TSHStatsUtil
│   └── upset_factor                 W R TSHStatsUtil / TSHStatsWidget
├── ruleset                          W R TSHScoreboardStageWidget / TSHScoreboardWidget / TSHWebServerActions
├── bracket_type                     W R TSHScoreboardWidget / TSHStatsUtil
└── upset_factor                     W   TSHStatsUtil (スコアボード番号なし — 要確認)
```

### `game`

選択中ゲーム情報。TSHGameAssetManager が一括書き込みする。

```
game                                 W R TSHGameAssetManager / TSHWebServerActions
├── name                             (str: ゲーム名)
├── smashgg_id                       (int: start.gg ID)
├── codename                         (str: 内部コード名)
├── logo                             (str: ロゴパス)
├── defaults                         R   TSHScoreboardWidget / TSHCommentaryWidget / TSHPlayerListWidget / TSHBracketWidget
│   ├── players_per_team             R   上記ウィジェット群
│   └── characters_per_player        R   上記ウィジェット群
├── mods_active                      R   TournamentStreamHelper.Window
├── has_stages                       R   TSHIndividualGameTracker
├── has_variants                     (bool)
├── has_colors                       R   TSHScoreboardWidget
├── has_modded_content               W   TSHGameAssetManager
└── (初期化時は空dict)               W   TSHGameAssetManager.__init__
```

### `bracket`

ブラケット表示情報。TSHBracketWidget / TSHBracketView が管理。

```
bracket                              W   TSHBracketWidget.__init__
├── phase                            W   TSHBracketWidget.PhaseGroupChanged
├── phaseGroup                       W   TSHBracketWidget.PhaseGroupChanged
├── bracket
│   ├── progressionsIn               W   TSHBracketWidget / TSHBracketView
│   ├── progressionsOut              W   TSHBracketWidget / TSHBracketView
│   ├── winnersOnly                  W   TSHBracketWidget
│   ├── winnersOnlyProgressions      W   TSHBracketView
│   ├── limitExportNumber            W   TSHBracketView
│   └── rounds                       W   TSHBracketView.UpdateBracket
├── players                          (base for TSHPlayerList)
│   └── (以下 player_list と同構造)
└── (team データ)                    R   TSHBracketView (score.{N}.team.{T} 経由で表示)
```

### `commentary`

実況者情報。TSHCommentaryWidget が管理。

```
commentary                           W D R  TSHCommentaryWidget / TSHWebServerActions
└── {N}                              D      TSHCommentaryWidget.RemoveExtraCommentators
    ├── (TSHScoreboardPlayerWidget と同じ player キー群)
    │   ├── name, team, seed, id, city, birthday
    │   ├── character, mains, country_code, state_code
    │   ├── mergedName, mergedOnlyName
    │   ├── online_avatar, avatar
    │   ├── sponsor_logo, sponsor_logos
    │   └── custom_textbox
    └── (commentary 固有キーは現状なし)
```

### `tournamentInfo`

大会メタデータ。TSHTournamentInfoWidget が管理。

```
tournamentInfo                       W R
├── tournamentName                   R   TSHScoreboardWidget (サムネイル生成時)
├── {QDateEdit.objectName}           W   TSHTournamentInfoWidget (日付フィールド)
├── {QLineEdit.objectName}           W   TSHTournamentInfoWidget (テキストフィールド)
├── {QSpinBox.objectName}            W   TSHTournamentInfoWidget (数値フィールド)
├── {key}                            W   TSHTournamentInfoWidget.UpdateData (API取得データ)
├── themeColor                       W   TSHTournamentInfoWidget (SettingsManager から初期化)
└── accentColor                      W   TSHTournamentInfoWidget (SettingsManager から初期化)
```

### `notes`

メモ情報。TSHNotesWidget が管理。

```
notes
└── notes_widget_{N}                 W   TSHNotesWidget
    ├── title                        W   TSHNotesWidget (QLineEdit)
    └── contents                     W   TSHNotesWidget (QPlainTextEdit)
```

### `player_list`

選手リスト。TSHPlayerListWidget が管理。

```
player_list                          W   TSHPlayerListWidget.__init__
└── slot
    └── {I}                          D   TSHPlayerList.RemoveSlot
        ├── name                     W   TSHPlayerListSlotWidget (QLineEdit)
        ├── score                    W   TSHPlayerListSlotWidget (QSpinBox)
        └── player
            └── {M}                  D   TSHPlayerListSlotWidget.SetPlayersPerTeam
                └── (TSHScoreboardPlayerWidget と同じ player キー群)
```

### `team_battle`

チームバトル情報。TSHTeamBattleWidget が管理。

```
team_battle                          D   TSHTeamBattleWidget.__init__
└── team
    └── {1|2}                        R   TSHTeamBattleWidget
        └── player
            └── {N}                  D   TSHTeamBattleWidget.CommandClearAll
                ├── (TSHTeamPlayerWidget: player キー群)
                ├── dynamic_spinner  W   TSHTeamPlayerWidget
                ├── active           W   TSHTeamPlayerWidget
                └── dead             W   TSHTeamPlayerWidget
```

### `layout_options`

レイアウトオプション。LayoutOptionsWidget が管理。

```
layout_options
└── {settingsBase}                   W   LayoutOptionsWidget
    └── {setting}                    W   (QCheckBox: bool / QLineEdit: str / TSHColorButton: str / QComboBox: str)
```

### `provider_videogame`

トーナメントプロバイダーのゲーム情報。

```
provider_videogame                   W   TSHTournamentDataProvider.GameChanged
├── id                               R   TSHStatsUtil
└── (将来拡張用の空間)
```

### `completed_sets`

完了済みセット。TournamentStreamHelper.Window が管理。

```
completed_sets                       W D  TournamentStreamHelper.Window
└── {I}                              W    (セットデータ dict)
```

### `streamQueue` / `currentStream`

配信キュー。TSHStatsUtil が管理。

```
streamQueue                          W   TSHStatsUtil.UpdateStreamQueue
currentStream                        W   TSHStatsUtil.UpdateStreamQueue
```

---

## 動的パスの解決表

一部のキーパスはウィジェットのコンストラクタ引数やQtのobjectNameに依存する。

| 変数 | 解決先 | 備考 |
|------|--------|------|
| `self.path` (TSHScoreboardPlayerWidget) | `score.{N}.team.{T}.player.{M}` | TSHScoreboardWidget から |
| `self.path` (TSHScoreboardPlayerWidget) | `commentary.{N}` | TSHCommentaryWidget から |
| `self.path` (TSHScoreboardPlayerWidget) | `{base}.slot.{I}.player.{M}` | TSHPlayerListSlotWidget から |
| `self.path` (TSHTeamPlayerWidget) | `team_battle.team.{T}.player.{N}` | TSHTeamBattleWidget から |
| `self.base` (TSHPlayerList) | `bracket.players` | TSHBracketWidget から |
| `self.base` (TSHPlayerList) | `player_list` | TSHPlayerListWidget から（デフォルト値） |
| `element.objectName()` | Qt Designer で定義された名前 | .ui ファイル依存。実行時まで不確定 |
| `self.settingsBase` (LayoutOptionsWidget) | レイアウト名 | TSHLayoutOptionsWindow から渡される |

---

## 直接 state アクセス（API 経由でない）

| ファイル | 行 | アクセス | 備考 |
|---------|-----|---------|------|
| `TSHWebServerActions.py:41` | `StateManager.state` (R) + `StateManager.deltaIndex` (R) | WebSocket 初回接続時の全状態返却。ロックなし ⚠️ |

---

## シグナル接続

| シグナル | 接続先 | トリガー条件 |
|---------|--------|------------|
| `state_big_change` | `WebServer.ws_program_state` | delta 100件超 or LoadState |
| `state_big_change` | `TSHStatsWidget._onStateChanged` | 同上 |
| `state_updated(delta)` | `WebServer.on_program_state_update` | delta 1-100件 |
| `state_updated(delta)` | `TSHStatsWidget._onStateUpdated` | 同上 |

---

## 要注意事項

### 1. `score.upset_factor` vs `score.{N}.upset_factor` ⚠️

`TSHStatsUtil.py:180` で `StateManager.Set(f"score.upset_factor", 0)` が呼ばれている。
一方 `TSHStatsUtil.py:177` では `StateManager.Set(f"score.{self.scoreboardNumber}.upset_factor", ...)` が呼ばれている。
`score.upset_factor`（番号なし）は意図的かバグか不明。

### 2. Qt objectName 依存の動的キー ⚠️

TSHScoreboardWidget の team 列、tournamentInfo のフィールドなど、複数箇所で `element.objectName()` をキーパスに使用している。
実際のキー名は `.ui` ファイルに定義されており、コードからは静的に特定できない。
`.ui` ファイルを変更するとキーパスが変わり、既存の保存データとの互換性が壊れる。

### 3. `StateManager.state` の直接参照 ⚠️

`TSHWebServerActions.py:41` で `StateManager.state` を直接返している。
ロックを取得していないため、SaveState のスレッドとレースコンディションの可能性がある。
実際には Flask リクエストは短時間で完了するため問題になりにくいが、設計上のリスク。

### 4. `commentary.{N}` の欠番 ⚠️

`TSHCommentaryWidget.RemoveExtraCommentators` で `commentary.{k}` を Unset しているが、
残った実況者のインデックスは詰めない（1,3 のように欠番が生じうる）。
Web UI 側でインデックスの連続性を前提としている場合にバグとなる可能性。
