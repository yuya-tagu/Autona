---
paths:
  - "src/core/**"
  - "src/StateManager.py"
  - "src/StateManagerBridge.py"
  - "src/AutonaWebServer.py"
  - "src/AutonaWebServerActions.py"
  - "src/Helpers/**"
---

# 状態管理・Web サーバー開発ルール

## StateManager

**正本は `src/core/StateManager.py`**（headless core Step 1）。`src/StateManager.py` は
互換 re-export シムで、1 世代だけ残す。新規コードは `from .core.StateManager import StateManager`。

- StateManager は static クラス（インスタンス化しない）
- 状態アクセスは `StateManager.Get(key)` / `StateManager.Set(key, value)`
- キーパスはドット区切り: `score.1.team.1.player.1.gamerTag`
- 一括更新時は `StateManager.SaveBlock()` コンテキストマネージャーで囲む
  - ブロック内ではファイル出力が抑制され、ブロック終了時にまとめて反映される
- スレッドセーフ: `StateManager.lock`（RLock）を使用。直接 state dict を操作しない
- **core は Qt を import しない**（`tests/test_core_qt_free.py` が subprocess で pin）。
  Qt が要る処理はアダプタ（`src/StateManagerBridge.py` が実例）へ出す

## StateSchema（キー定義とシードの正本）

`src/core/StateSchema.py`（headless core Step 2）。**state の「何があるか」の定義を
ウィジェットに書かない**。

- held exempt (`STATS_KEYS`) / 表示トグル (`DISPLAY_OPTIONS`) / 起動時シード
  (`SeedBoard` / `SeedPanelState`) はここが正本。ウィジェット側に同じ定義を置き直さない
- `SeedBoard(n)` は「埋める」ではなく **サブツリーを skeleton に戻す**。生きているボードに
  対して呼ばない（未構築ボードのプレースホルダと、遅延構築時の再シードが用途）
- `KEY_CATALOG` / `DYNAMIC_KEY_FAMILIES` は基本的に**列挙のみ**（マイグレーション層の
  アンカー）。新しい state キーを足したらここにも書く（テストで強制できないため
  ベストエフォート運用。**PR レビュー時に KEY_CATALOG との差分を確認する**）
- 例外は**静的化済みのキー名**: `SCORE_MATCH_FIELDS`（`phase`/`pool`/`match`）と
  `SCORE_TEAM_INPUT_KEYS`（`teamName`/`losers`）は Step 5 第 1 弾で、
  `PLAYER_INPUT_KEYS`（`team`/`name`/`real_name`/`twitter`/`custom_textbox`/
  `seed`/`birthday`/`country`/`state`）は第 2 弾で、
  `TOURNAMENT_INPUT_KEYS`（`tournamentName`/`eventName`/`address`/`numEntrants`/
  `startAt`）は第 3 弾で実際に消費されるようになった —
  `AutonaActions` / `PlayerService` / `TournamentInfoService` がこれらを書き、
  `AutonaScoreboardWidget` / `AutonaScoreboardPlayerWidget` /
  `AutonaTournamentInfoWidget` は `findChild` の**引数として**使う
  （objectName から作らない）。**文字列は .ui の objectName と一致していなければ
  ならない**（既存 `program_state.json` の互換に加えて、eye メニューの表示トグルも
  同じ名前で widget を引く）。`tests/test_objectname_keypath_contract.py` が
  一致・「objectName() 非使用」・「選手ウィジェット / EVENT パネルは StateManager を
  直接書かない」を pin
- `DYNAMIC_KEY_FAMILIES` は Step 5 の残タスク一覧として縮み、**第 3 弾で空になった**。
  新しいウィジェットを objectName 由来キーで書かない（`tests/test_state_schema.py` が
  空であることを pin）
- **サブツリールートも定数**（Step 5 第 5 弾）: `SCORE_ROOT` / `GAME_ROOT` / `TOP8_ROOT` /
  `COMMENTARY_ROOT` / `TOURNAMENT_INFO_ROOT` / `STREAM_ROOT` / `COLORS_ROOT` /
  `DISPLAY_OPTIONS_ROOT` / `LAYOUT_OPTIONS_ROOT` / `RESULT_PLAYERS_ROOT` /
  `PROVIDER_VIDEOGAME_ROOT`。`KEY_CATALOG` はこれで keying されているので、
  ルート名を変えるとカタログ側も必ず追随する
- `colors.{feature}.*`（機能別カラー）のシード（`SeedColorState()`）は **Step 5 第 5 弾で
  `src/core/ColorService.py` へ移動**（この族の読み・書き・シードが 1 モジュールに揃った）。
  **大会カラー 2 キー（`tournamentInfo.themeColor`/`accentColor`）の起動シードは
  `StateSchema.SeedPanelState()` 側に残っている**（シード = StateSchema、編集 = ColorService
  の 2 モジュール分割。キー定数は `THEME_COLOR_KEY`/`ACCENT_COLOR_KEY` に一本化済みなので
  分割していても実体は 1 箇所を指す）。`Window._SeedPanelState()` は
  `StateSchema.SeedPanelState()` と別呼び出しのまま（ColorService が StateSchema を読むので
  schema からは呼べない）。同じ `SaveBlock` 内
- **core → src の逆依存は Qt-free モジュールのみ import 可**（`tests/test_core_qt_free.py`
  が推移的に検査する）。現行の逆依存:
  - `StateSchema` / `Top8Service` → `src` 直下の `AutonaCommentaryModel` / `AutonaTop8Model`
    （いずれも Qt 非依存の state モデル。将来 core へ移す候補）
  - `StateManager` / `SettingsManager`（従来から）+ `LayoutOptionsManager`（Step 5 第 5 弾で追加）
    → `src/Helpers/AutonaDictHelper`（`deep_get`/`deep_set`。Qt 非依存のユーティリティ）
  - `PlayerService` → `Helpers/AutonaSponsorHelper` は **Step 5 第 5 弾で解消**
    （`src/core/SponsorHelper.py` へ移動）。新たな逆依存を足すときはこの一覧に追記する

## AutonaActions（ドメイン操作の唯一の書き手）

`src/core/Actions.py`（headless core Step 3）。**移行済みの操作は
`AutonaActions.instance` 以外が書かない**。インスタンスは `bootstrap_core()` が作る。
永続側のストア 2 つ（`src/core/SettingsManager.py` / `src/core/LayoutOptionsManager.py`）も
core にあり、どちらも `bootstrap_core()` が `Initialize()` を呼ぶ（import 副作用ゼロ）。

- 移行済み: `AdjustScore` / `ResetScore` / `SetScore`（board 1,2 のみ。`SetScore` は /api/v1 `score:set` 用・
  状態側チーム指定）/ `SetTeamColor` /
  `SetDisplayOption` / `ApplyChanges`（反映 = `CommitHeld`）/ **Step 5 第 1 弾で追加**:
  `SetMatchField`（`phase`/`pool`/`match`）/ `ClearMatchInfo`（リセット時に match バー +
  `match_en` を空にする。S-17）/ `SetTeamName` / `SetTeamLosers` /
  `SetTeamsSwapped` / `SetBestOf` / **第 2 弾で追加**: `ClearTeamRoster` /
  `TrimTeamRoster`（チームの選手スロット数。スロットの中身は `PlayerService`）/
  **第 5 弾で追加**: `SetLayoutOption`（`LayoutOptionsManager` + `layout_options.*` の
  両面ミラー）/ `SetStreamUrl` / `SetStreamTitle` / `PublishCommentary` /
  `PublishResultPlayers`（後 2 つはパネルが組んだ dict をサブツリーごと publish する操作）
- 未移行（Qt 側が書き手のまま。理由は `Actions.py` の docstring）: チームスワップ操作
  そのもの（選手側は**ウィジェット間の値コピー**で、選手フィールドの正本が
  ウィジェットにある限り core に出せない。`teamsSwapped` は `SetTeamsSwapped` が
  ミラーを publish するだけで正本はウィジェット属性）
- **クルーボード（board 3）は `CrewBattleService` が書き手**（Step 5 第 4 弾）。
  ただし *入口* のうち board 1/2 と語義が同じもの — チーム名（`SetTeamName`）・
  チームカラー（`SetTeamColor`）・match バーの 3 combo（`SetMatchField`）— は
  `AutonaActions` を呼ぶ。`AdjustScore` / `ResetScore` は board 3 では今も no-op で、
  クルーの「リセット」= 全員ストック満タン復元はウィジェットが入口
  （`WebServerActions.reset_scores` の `@gui_thread_sync` はそのために残っている）
- **`SetBestOf` はスピンボックス主導のまま**（state 先行に反転していない）。
  `AutonaScoreboardWidget._onBestOfChanged` が 1 つの `SaveBlock` で
  `SetBestOf` → `individualGameTracker.SetStageCount(value)` を呼ぶ。反転すると
  ステージ行の再構築（= state 書き込み）が通知ハンドラ内に入り、再入 + 2 サイクルになる
- **ローカライズ文字列は callable で注入**する。`best_of_text` / `first_to_text` は
  `AutonaLocaleHelper.FormatMatchText` を `bootstrap_core()` が `AutonaActions` に渡す
  （`TournamentInfoService.formatEventDate` と同じ形）。未注入時は英語既定にフォールバック
- `score.{n}.team.{t}.score` の書き手は **core 内に 3 系統**（`AutonaActions`、
  ステージ集計の `GameTrackerService.PublishStageResults` / `UpdateScore`、
  セット読込の `SetLoadService.ApplyScores`）。加えて**未移行の Qt 側経路**
  （`ClearScore` / `SwapTeams` / `ResetAllInputs`）がスコアスピンボックスを
  駆動し、その `valueChanged` が `GameTrackerService.UpdateScore` を呼ぶ形で書く。
  **core の外から直接 `Set` する経路は残っていない**（`StageResultsToScore` の
  再 Set は検収レビューで削除済み。表示更新のみ）。すべて state 経由で
  のみ噛み合う。ステージ集計側は自分が書いている区間を
  `GameTrackerService.writingScore` で公開し（ウィジェットは property で委譲）、
  `_SyncScoreDisplays` はその間 `SyncStagesToScore` を呼ばない（呼ぶと 1 クリックで
  次のゲームまで勝ちになる）。完全一本化は Step 5
- 呼び出し側は Qt ウィジェット・`AutonaWebServerActions`・`AutonaHotkeys` の 3 つ。
  **どのスレッドから呼んでもよい**（Qt に触れないのが層の定義）。委譲した
  WebServerActions メソッドからは `@gui_thread_sync` を外す
- **SaveBlock は複数キーを 1 サイクルにまとめる時だけ**使う（単一キーなら不要）。
  かつては「空 `changedKeys` の SaveState = `include_paths=[]` = フィルタなし =
  全 state 差分」という地雷があったが、**Step 4a で `SaveState` 側が空 `changedKeys`
  を早期 return するようになった**ので、包んでも全量 diff にはならない
  （`tests/test_state_manager_held.py` が DeepDiff 呼び出し回数で pin）
- ウィジェット側は通知（`held_state_updated` / `state_updated`）で表示を更新する。
  プログラム的更新は必ず `QSignalBlocker` で囲む（囲まないと `valueChanged` 等が
  発火して書き戻し = エコーループ）。再入ガードの実例は
  `AutonaScoreboardWidget._SyncScoreDisplays`
- ホットキーは `AutonaHotkeys.ConnectActions()` が `bootstrap_core()` から 1 回だけ結線する。
  対象ボードは `AutonaActions.ActiveBoard()`（`SettingsManager.scoreboard.battle_format`）

## ドメインサービス（core の per-board オブジェクト）

`AutonaActions` が「1 回の操作 = 1 メソッド」の入口なのに対し、状態を持つ／ボードごとに
インスタンスが要るドメインロジックは core のサービスに置く（headless core Step 4a）。
**Qt 側は薄いアダプタに徹する**（`AutonaStatsUtil` / `AutonaIndividualGameTracker` が実例）。

| サービス | 所有者 | 責務 |
|---------|--------|------|
| `src/core/StatsService.py` | `AutonaScoreboardWidget.stats.service` | 統計の取得可否判断・アップセット計算・`last_sets`/`history_sets`/`recent_sets`/`upset_factor` の書き込み |
| `src/core/GameTrackerService.py` | `AutonaIndividualGameTracker.service` | ステージ勝敗の記録（`score.{n}.stages.*`）・ステージ⇔スコアの相互整合・`writingScore` ガード |
| `src/core/SetLoadService.py` | `AutonaScoreboardWidget.setLoad` | セット読込の正規化（ラウンド/フェーズ/プール・スコア・敗者フラグ・チーム名とロゴ・`bracket_type`・追従中セット）。スワップ後の面の解決は `DisplayTeam()` が唯一の正本 |
| `src/core/TournamentInfoService.py` | `TournamentInfoService.instance`（`bootstrap_core()` 生成） | `tournamentInfo.*` の書き込み全部（provider ペイロードの適用・**EVENT タブ入力欄の `Set{Text,Number,Date}Field`**・表示大会名の 3 系統の上書き規則・`logoVersion`・起動時シード）。EVENT パネル不在でも動くことが遅延構築の前提。**両方向が同じ 3 操作を通る**（provider 側の `_ApplyKey` も入力側と同じメソッドを呼ぶ）。ただし `themeColor`/`accentColor` の 2 キーだけは **`core/ColorService.py` の持ち物**で本サービスを通らない（歴史的にこのプレフィックスなだけで、意味はカラー機能側）。**表示大会名の入力欄は SCORE パネルとクルーボードの 2 箇所にあり、どちらも `SetDisplayName` を呼ぶ**（クルー側は Step 5 第 4 弾で合流） |
| `src/core/PlayerService.py` | `AutonaBasePlayerWidget.player`（選手ウィジェット 1 枚につき 1 個。path に束縛） | 選手スロット配下の書き込み全部（入力フィールド・seed/birthday・国/州の選択行・`mergedName`/`mergedOnlyName`・アバターとスポンサーロゴ・キャラスロット・`city`/`id`・スロット削除）。`BoardNumber()` が `score.{N}` と `player_editor.1` を見分ける |
| `src/core/CrewBattleService.py` | `AutonaCrewBattleWidget.crew` | クルーボード（board 3）の書き込み全部（起動シード・メンバー名/ストック・`memberNum`/`activeMember`・**クルー合計の算出**（名前が入った表示中メンバーの stock 合計 → `team.{t}.score`）・crew 版のセットデータ正規化・`best_of`（raw のみ）・スワップ/リセット/クリア後の全体 publish）。合計は**state から算出**する（旧実装はウィジェットの値を合計していた） |
| `src/core/Top8Service.py` | なし（モジュール関数） | `top8.*` の書き込み全部（選手 8 枠の publish・表示ラベルの既定値・反映（staged スコアのマージ + `Recompute`）・全リセット・Start.gg 取り込み）。トポロジ自体は Qt-free の `src/AutonaTop8Model.py` が正本 |
| `src/core/ColorService.py` | なし（モジュール関数） | 大会カラー（`tournamentInfo.themeColor`/`accentColor`）と機能別カラー（`colors.{feature}.*`）の読み・書き・起動シード。**`tournamentInfo` プレフィックスだがこの族は本サービスのもの**で、`TournamentInfoService` は触らない（歴史的なキー名） |
| `src/core/SponsorHelper.py` | なし（静的メソッド） | `...player.{M}.sponsor_logo` / `sponsor_logos.{I}` の解決と書き込み。`PlayerService.PublishImages` から呼ばれる |

**状態を持たないサービスはモジュール関数でよい**（`Top8Service` / `ColorService`）。
`bootstrap_core()` が作るのは、ボードごとに要る／台帳を持つもの（`SetLoadService` /
`TournamentInfoService` / `PlayerService` / `CrewBattleService` / `AutonaActions`）だけ。

- **`SetLoadService` はステージ行を書かない**。行数と `stage_widget_list` の長さを
  一致させているのは `GameTrackerService.SetStageCount()` だけなので、読み込んだ
  スコアは `team.{t}.score` を書いて `_SyncScoreDisplays` → `SyncStagesToScore()` に
  渡す（+1 と同じ経路）。`stages` を直接足す経路を作らない
- **held 配下は「同値なら書かない」**（`SetLoadService._SetIfChanged` /
  `TournamentInfoService._SetIfChanged` / `AutonaActions._SetIfChanged`）。同値でも
  `Set` すればボードは dirty になり 反映 ボタンが点灯する。Qt 経路は
  `setChecked`/`setValue` が無変化なら emit しないことで偶然これを満たしていた
  - **`PlayerService` はこの規則を採らない**（例外）。理由は同モジュールの docstring:
    ここに来る書き込みは「すでに変化したウィジェット」からのもので、かつ
    州コンボの再構築とキャラスロットは**同じ空値を 2 回連続で publish するのが正常**
    （リークテストがその回数を数えている）。元から guard があった `city` / `id` の
    2 つだけは guard を保持している

- **state が記録、ウィジェットは表示**。ステージ結果は `score.{n}.stages.{i}` が正本で、
  ボタンはそれを描画するだけ（`_SyncStageButtons`）。`setChecked()` は `toggled` しか
  出さず `clicked` は出ないので、この再描画はエコーループにならない
- Qt 依存が避けられない値（選手数 = `team1playerWidgets` の長さ）は**呼び出し可能オブジェクトで
  注入**する。core 側が Qt を import しないことが層の定義（`tests/test_core_qt_free.py`）
- **サービスは observer を購読しない**。購読すると所有ウィジェットと同じゴースト問題を
  core 側に持ち込むことになる。サービスは「呼ばれる」だけの存在にする

## 状態変更の通知（observer + Bridge）

通知の正本は Qt 非依存の observer チャンネル（`src/core/Observable.py` の
`ObserverChannel`）で、`StateManager.observers.<名前>` に 4 本ある。core 側は
`notify()` で発行する（8 箇所）。

| チャンネル | 引数 | 意味 |
|-----------|------|------|
| `state_big_change` | なし | 全面リロード。全ウィジェットのフル再描画をトリガーするため頻繁に発行しない |
| `state_updated` | payload dict（`revision` / `delta_index` / `delta`） | 差分のみ。WebSocket 経由で Web UI に配信される |
| `held_dirty_changed` | (prefix, bool) | held サブツリーに未反映の変更が出た(True)／反映された(False) |
| `held_state_updated` | (prefix) | held 配下のライブ値が変わった（保存サイクル単位で合体） |

購読側の使い分け:

- **Qt ウィジェット / Qt を持つオブジェクトは `StateManagerBridge.instance` の同名
  シグナルに `connect` する**（現在 13 箇所）。observer を直接 `subscribe` してはいけない
  — チャンネルは callback を**強参照**し、bound method でも自動解除されないため、
  破棄済みウィジェットが state を書き続ける（ゴーストボード）。
  `tests/test_signal_lifetime.py` が違反を検出する
- **core / 非 Qt コードだけが `StateManager.observers.<名前>.subscribe(cb)` を使う**。
  寿命が短い購読者は自分で `unsubscribe()` する
- `ObserverChannel.notify()` は購読順・同期・**購読者ごとの例外隔離**
  （例外は logger.exception でログして次の購読者へ進む。書き込み側は落ちない）
- `StateManagerBridge` は process 内で 1 個だけ（`bootstrap_core()` が生成。
  2 個目の生成は `__init__` が RuntimeError で拒否する）

## AutonaDictHelper

- `deep_get(d, keys)` / `deep_set(d, keys, value)` でネスト操作
- キーは文字列リスト: `["score", "1", "team", "1"]`
- StateManager 内部で使用。外部から直接呼ぶ場面は少ない

## Web サーバー（Flask + SocketIO）

- `AutonaWebServer` は QThread として別スレッドで動作
- Flask アプリと SocketIO インスタンスは static 変数
- ルートハンドラは `AutonaWebServerActions` に集約
- Web UI からの POST リクエスト → Actions → StateManager 更新 → WebSocket 配信の流れ
- 新しいエンドポイントを追加する場合は `AutonaWebServerActions` に記述する
- CORS は Flask-CORS で全オリジン許可済み

## Helpers

- `Helpers/` 配下はユーティリティ。各ファイルは単一責務
- `AutonaQtHelper.py` の `GuiExecutor` はワーカースレッドから GUI 操作する際の橋渡し
- `AutonaCountryHelper` / `AutonaLocaleHelper` はシグナルを持つ。変更通知が必要な場面で使う
- `AutonaBadWordFilter` は Trie ベース。パフォーマンスに配慮されている
