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

**正本は `src/core/StateManager.py`**（headless core Step 1）。新規コードは
`from .core.StateManager import StateManager`。

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
- **core は `src/core` 外の `src` モジュールを import しない**（ADR-005 S0 で逆依存ゼロ。
  `tests/test_core_qt_free.py` が AST（関数内の遅延 import 含む）と subprocess の
  `sys.modules` の両方で pin）。core が必要とする Qt-free の部品は core に置く:
  `core/DictHelper.py`（旧 `Helpers/AutonaDictHelper`）/ `core/CommentaryModel.py`
  （旧 `src/AutonaCommentaryModel`）/ `core/Top8Model.py`（旧 `src/AutonaTop8Model`）/
  `core/SponsorHelper.py`（旧 `Helpers/AutonaSponsorHelper`）/ `core/TextHelper.py`
  （`remove_accents_lower`）。ローカライズ等 Qt 側にしか無いものは
  `bootstrap_core()` から callable で注入する

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
  `SetBestOf` / **第 2 弾で追加**: `ClearTeamRoster` /
  `TrimTeamRoster`（チームの選手スロット数。スロットの中身は `PlayerService`）/
  **第 5 弾で追加**: `SetLayoutOption`（`LayoutOptionsManager` + `layout_options.*` の
  両面ミラー）/ `SetStreamUrl` / `SetStreamTitle` / `PublishCommentary` /
  `PublishResultPlayers`（後 2 つはパネルが組んだ dict をサブツリーごと publish する操作）/
  **ADR-005 S2c で追加**: `SwapTeams`（board 1,2。選手スロット・スコア・LOSERS・チーム名・
  ロゴ・ゲームトラッカーの勝敗（鏡映）を 1 保存サイクルで入れ替え、`score.{n}.teamsSwapped` を
  反転。チームカラーは入れ替えない。board 3 は False を返し、呼び出し側がクルーウィジェットに
  委ねる）/ `SwapPlayers`（同じチームの 2 スロットを入れ替え。カードの上下移動）/
  **ADR-005 S2d で追加**: `ClearScore`（スコア 0・LOSERS False。match バーは残す = セット読込の前処理）/
  `ResetMatch`（`ClearScore` + `ClearMatchInfo`）/ `ClearPlayers(board, no_mains)`（両チームの全スロットを
  旧カードの `Clear()` と同じ内容で空にする。birthday・city・mains は残る）/ `ClearBoard`（すべての入力値をリセット =
  選手 + `ResetMatch` + チーム名・ロゴ）。いずれも board 1,2 だけで 1 保存サイクル、board 3 は False
- **クリアは state の書き換え**（S2d）。旧実装はボードのウィジェットを空に戻していた（カード 1 枚ごとに 1 サイクル）。
  ボードに残るのは `AutonaScoreboardWidget._ClearingPlayers` の 3 つ: 入力中の欄の確定（`CommitFocusedEdit`。打ちかけの文字がクリアで消えるのは選手欄だけで、match / チーム名 / スコア欄の打ちかけは旧実装どおりフォーカスアウトで確定する。セット読込の前処理でもフォーカスが外れる）・
  追跡中セットを忘れる（同じセットの再読込が効くように）・id が変わったチームの統計を 1 回取り直す。
  Web の `clear_all` は `ClearBoard` ではなく、旧順序（`ResetMatch` → best_of → 人数・キャラ数 → 選手）で呼ぶ
  （チーム名を残し、人数・キャラ数の変更が mains を決める順を保つ。ADR-005 S2 判断 3）
- **スワップは state の入れ替え**（S2c）。旧実装は選手カード間の値コピー（`SwapWith`、削除済み）で、
  キャラのバリアントと mains を落とし、ゲームトラッカー ON で勝敗の並びを崩していた。
  スロットは**キー単位**で入れ替える（両方にあるキーは各 dict 内の位置を保つ = `program_state.json` /
  Socket.IO delta のキー順を旧実装と一致させる。片側にしかないキーは `SLOT_DEFAULTS`、`id` / `city` は
  「None のために作らない」、seed は旧 `SetSeed` と同じく常に int で両側に書く）。チーム入替は両チームにある
  スロットだけを入れ替える（片側だけのスロットは動かさない）。キー順は 6a6f04cc で録画した全 dict の挿入順
  （`tests/fixtures/board_operations/key_order.json`）と照合する（`tests/test_player_card_render.py`。
  値が意図して変わったステップも含む）。
  **`teamsSwapped` の正本は state**（ボード属性は廃止）。`SetLoadService.DisplayTeam` と
  `WebServerActions.get_swap` は state を読む。スワップ後のカード・スコア・チームカード・ステージボタンは
  held サイクルからの描画で追従し、統計の再取得はボードが held サイクルでフラグの反転（チーム入替）を検知して
  1 回だけ出す（上下移動は `_MovePlayer` が出す）。アップセット係数は操作が core 内で再計算する。
  入替サイクルではボードがゲームトラッカーにスコアの変化を伝えない（`_SyncScoreDisplays(followStages=False)`。
  記録は入替が鏡映済み）。**入力中の欄は入替・移動の前に確定する**（`AutonaScoreboardWidget.CommitFocusedEdit`
  がそのボード内のフォーカスを外し、Qt に `editingFinished` を出させる。欄の補完ポップアップが開いていれば
  先に閉じる（開いたままだと確定されない）。SWAP ボタン・上下移動・ホットキー・Web の全入口。
  旧実装は打ちかけの文字を選手と一緒に運んでいた）
- **クルーボード（board 3）は `CrewBattleService` が書き手**（Step 5 第 4 弾）。
  ただし *入口* のうち board 1/2 と語義が同じもの — チーム名（`SetTeamName`）・
  チームカラー（`SetTeamColor`）・match バーの 3 combo（`SetMatchField`）— は
  `AutonaActions` を呼ぶ。`AdjustScore` / `ResetScore` は board 3 では今も no-op で、
  クルーの「リセット」= 全員ストック満タン復元はウィジェットが入口
  （`WebServerActions.reset_scores` の `@gui_thread_sync` はそのために残っている）
- **best_of は state 主導**（ADR-005 S2e）。`SetBestOf(board, n)`（board 1,2。board 3 は False で、
  クルーはスピンボックス → `CrewBattleService`）が 6 キーとステージ行を 1 `SaveBlock` で書く。
  0〜99 に丸め、値が変わらなければ何も書かない（旧 `valueChanged` と同じ。同じ値でゲームの記録は消えない）。
  ゲームのロードは `RepublishBestOf(board)`（文言を作り直し、同じ行数で行をリセット）。
  行はボードが構築時に `RegisterGameTracker(board, tracker)` で登録したトラッカーに、操作の block の中で
  `SetStageCount` させる（通知ハンドラの中で書かない = 再入しない。解除はボードの `destroyed` から。未構築ボードは
  キーだけ）。スピンボックスと行は held サイクルの `_SyncBestOf` が描画する（`_SyncSetFields` の後、
  `_SyncScoreDisplays` の前。入力中のスピンボックスは飛ばし、`editingFinished` で描画し直す）。
  入口はすべてこの操作: スピンボックス（`_onBestOfChanged`）・セット読込（`SetLoadService.BestOfFromSetData`。
  0・空は運ばない）・Web `set_route` の bestOf（v1 `PATCH set` 含む）・`_reset_best_of`・`ClearBoard`（最後に 0）
- **ローカライズ文字列は callable で注入**する。`best_of_text` / `first_to_text` は
  `AutonaLocaleHelper.FormatMatchText` を `bootstrap_core()` が `AutonaActions` に渡す
  （`TournamentInfoService.formatEventDate` と同じ形）。未注入時と、注入関数が例外を出したとき
  （ロケール未ロードの空の表・`.format()` が通らない独自用語）は英語既定にフォールバックする
  （後者は警告を出す。`SetBestOf` は入口から直接呼ばれるので、例外を上げるとセット読込の残りや
  ゲームのロードの `CommitHeld` が止まる。ADR-005 S2e）。int にできない best_of は警告して何も書かない
- `score.{n}.team.{t}.score` の書き手は **core 内に 3 系統**（`AutonaActions`、
  ステージ集計の `GameTrackerService.PublishStageResults` / `UpdateScore`、
  セット読込の `SetLoadService.ApplyScores`。`AutonaActions` の書き手は `SetScore` / `AdjustScore` / `ResetScore` と、S2c からの `SwapTeams`、S2d からのクリア）。
  加えて**スコア欄への数値の手入力**だけがスコアスピンボックスを駆動し、その `valueChanged` が
  `GameTrackerService.UpdateScore` を呼ぶ形で書く（`ClearScore` / `ResetAllInputs` は S2d で core 操作になった）。
  **core の外から直接 `Set` する経路は残っていない**（`StageResultsToScore` の
  再 Set は検収レビューで削除済み。表示更新のみ）。すべて state 経由で
  のみ噛み合う。ステージ集計側は自分が書いている区間を
  `GameTrackerService.writingScore` で公開し（ウィジェットは property で委譲）、
  `_SyncScoreDisplays` はその間 `SyncStagesToScore` を呼ばない（呼ぶと 1 クリックで
  次のゲームまで勝ちになる）。完全一本化は Step 5。
  スコアへの行の追従は両チーム分を 1 つの `SaveBlock` で書く（ADR-005 S2e。旧は 1 行 1 サイクル）ので、
  トラッカー ON で何ゲーム分動いても「スコア 1 + 追従 1」サイクル
- 呼び出し側は Qt ウィジェット・`AutonaWebServerActions`・`AutonaHotkeys` の 3 つ。
  **どのスレッドから呼んでもよい**（Qt に触れないのが層の定義）。委譲した
  WebServerActions メソッドからは `@gui_thread_sync` を外す。例外は `swap_teams`（S2c）で、
  S2f まで GUI スレッドで同期実行する（選手カードの入力確定が GUI スレッド上で「フィールド → 合成名」の
  2 書き込みに分かれており、その間にスワップが割り込むと合成名が反対のスロットに残るため。入力中の欄の確定も
  GUI スレッドで行う）。未構築のボードは従来どおり構築してから入れ替える（`get_swap` も構築する）。
  S2d のクリア（`reset_match` / `reset_players` / `clear_all`）・`get_set`・`set_route` の
  phase / pool / match / losers / teamName / bestOf（S2e）も同じ理由で GUI スレッドに残し、未構築ボードを
  `EnsureScoreboard` で構築してから書く（人数 / キャラ数はまだボードのスピンボックス。S2g）
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
  対象ボードは `AutonaActions.ActiveBoard()`（`SettingsManager.scoreboard.battle_format`）。
  `swap_teams` も S2c からここ（旧: 構築済みの全ボードに接続しており、形式切替後は両ボードが入れ替わった）。
  ただし `swap_teams` だけは、アクティブボードが未構築なら何もしない（旧実装どおり。スコアのホットキーは
  未構築でも効く。未構築ボードは held でないので、入れ替えると即時公開され、構築時のシードで消える）。
  ボードに残るのはパネル操作の `load_set` だけ（`refresh_phase_group` は Window）
- **未構築ボードは held 登録しない**（held 登録はボードの `__init__` の `StateSchema.HoldBoard` だけ）。
  未構築ボードへの core 直結の書き込み（`score:adjust` / `score:set` / `score:reset` / チームカラー）は即時公開される。
  起動時から held にする案（ADR-005 S2 判断 7）は S2c で撤回した: Web UI には反映ボタンが無く、デスクトップの反映は
  表示中のボードだけなので、形式と食い違う面への書き込みがどこにも出ないまま、構築時の `SeedBoard` で消える。
  held が残り続けると保存サイクルごとに state 全量の複製も走る

## ドメインサービス（core の per-board オブジェクト）

`AutonaActions` が「1 回の操作 = 1 メソッド」の入口なのに対し、状態を持つ／ボードごとに
インスタンスが要るドメインロジックは core のサービスに置く（headless core Step 4a）。
**Qt 側は薄いアダプタに徹する**（`AutonaStatsUtil` / `AutonaIndividualGameTracker` が実例）。

| サービス | 所有者 | 責務 |
|---------|--------|------|
| `src/core/StatsService.py` | `AutonaScoreboardWidget.stats.service` | 統計の取得可否判断・アップセット計算・`last_sets`/`history_sets`/`recent_sets`/`upset_factor` の書き込み |
| `src/core/GameTrackerService.py` | `AutonaIndividualGameTracker.service`（ボードが `AutonaActions.RegisterGameTracker` で登録する。ADR-005 S2e） | ステージ勝敗の記録（`score.{n}.stages.*`）・ステージ⇔スコアの相互整合・`writingScore` ガード・行の作り直し（`SetStageCount`。呼ぶのは `AutonaActions.SetBestOf` / `RepublishBestOf` だけで、`rowsVersion` が作り直しのたびに進み、ウィジェットの `RenderStageRows` がそれを見てボタンを作り直す）。存在しない行への `SetStageResult` / `SetStageAsset` は lock の中で断る（警告して False。別スレッドの `SetBestOf` で行が消えた後、作り直し前の古いボタンが押されても行を書き戻さない） |
| `src/core/SetLoadService.py` | `AutonaScoreboardWidget.setLoad` | セット読込の正規化（ラウンド/フェーズ/プール・スコア・敗者フラグ・チーム名とロゴ・`bracket_type`・追従中セット）。最後に読んだセット `lastSetSelected` も持つ（state の外。ADR-005 S2d でボード属性から移動。クリアと `ClearSetTracking()` で忘れるので、同じセットを選び直すと再読込される。`get_set` と stale 判定が読む）。スワップ後の面の解決は `DisplayTeam()` が唯一の正本（`score.{n}.teamsSwapped` を読む。S2c までボード属性を callable で注入していた） |
| `src/core/TournamentInfoService.py` | `TournamentInfoService.instance`（`bootstrap_core()` 生成） | `tournamentInfo.*` の書き込み全部（provider ペイロードの適用・**EVENT タブ入力欄の `Set{Text,Number,Date}Field`**・表示大会名の 3 系統の上書き規則・`logoVersion`・起動時シード）。EVENT パネル不在でも動くことが遅延構築の前提。**両方向が同じ 3 操作を通る**（provider 側の `_ApplyKey` も入力側と同じメソッドを呼ぶ）。ただし `themeColor`/`accentColor` の 2 キーだけは **`core/ColorService.py` の持ち物**で本サービスを通らない（歴史的にこのプレフィックスなだけで、意味はカラー機能側）。**表示大会名の入力欄は SCORE パネルとクルーボードの 2 箇所にあり、どちらも `SetDisplayName` を呼ぶ**（クルー側は Step 5 第 4 弾で合流） |
| `src/core/PlayerService.py` | `AutonaBasePlayerWidget.player`（選手ウィジェット 1 枚につき 1 個。path に束縛）。加えて**一時インスタンス**: モジュール関数 `RepublishTeamMergedNames(board, team)` が、state にあるそのチームの各スロットの path で 1 個ずつ作って使い捨てる（カードを経由しない） | 選手スロット配下の書き込み全部（入力フィールド・seed/birthday・国/州・`mergedName`/`mergedOnlyName`・アバターとスポンサーロゴ・キャラスロット・`city`/`id`・スロット削除）。`BoardNumber()` が `score.{N}` と `player_editor.1` を見分ける。**`mergedName` / `mergedOnlyName` の ` [L]` は state のチームの `losers` から付ける**（`PublishMergedName(sponsor, tag)` が `TeamLosersKey()` を読む。ADR-005 S2b）。フラグの書き手は同じ SaveBlock で `RepublishTeamMergedNames` を呼び、チーム全員の名前を state の `team` / `name` から作り直す: `AutonaActions.SetTeamLosers`（LOSERS チェックボックス・v1 `PATCH set` の `WebServerActions.set_team_losers`）と `SetLoadService.ApplyLosers`（セット読込・legacy `-set?losers=` の `ChangeSetData`。`DisplayTeam` で解いたチーム）。どちらもフラグが変わったときだけ。カードは名前確定で `_OnTextChanged`（state に書く）→ `NameChanged`（publish）の順に走るので、作り直しがどのスレッドから割り込んでも同じ名前になる。**国/州とキャラスロットはカードから識別子だけを受け取り、値はここで組む**（ADR-005 S1d）: `SetCountry(code)` / `SetRegion(countryCode, regionCode)` は `CountryCatalog.CountryByCode` / `State` から、`PublishCharacterSlots([CharacterSlot(en_name, skin 行, variant en_name, フォールバック名), ...])` は `GameAssetCatalog.CharacterRow` / `SkinRow` / `VariantRow` から（1 回の publish は `Current()` 1 回のスナップショットで解決）。入れ子のキー順は旧実装が Qt モデル経由で出していた QVariantMap 順を `DictHelper.qvariantmap_copy` で再現する（`program_state.json` と Socket.IO delta のバイト互換。`tests/test_player_slot_publish.py` と `tests/test_player_service.py` が移行前の golden と照合）。カタログが知らない識別子は、キャラなら `{"name": フォールバック名}`（手入力テキスト、または選ばれた行自身の `name`。コンボの「表示名 / en_name」ラベルは使わない）、国/州なら `{}`。カタログは `games=` / `countries=` で差し替え可能（既定は呼び出し時の `.instance`） |
| `src/core/CrewBattleService.py` | `AutonaCrewBattleWidget.crew` | クルーボード（board 3）の書き込み全部（起動シード・メンバー名/ストック・`memberNum`/`activeMember`・**クルー合計の算出**（名前が入った表示中メンバーの stock 合計 → `team.{t}.score`）・crew 版のセットデータ正規化・`best_of`（raw のみ）・スワップ/リセット/クリア後の全体 publish）。合計は**state から算出**する（旧実装はウィジェットの値を合計していた） |
| `src/core/Top8Service.py` | なし（モジュール関数） | `top8.*` の書き込み全部（選手 8 枠の publish・表示ラベルの既定値・反映（staged スコアのマージ + `Recompute`）・全リセット・Start.gg 取り込み）。トポロジ自体は Qt-free の `src/core/Top8Model.py` が正本 |
| `src/core/ColorService.py` | なし（モジュール関数） | 大会カラー（`tournamentInfo.themeColor`/`accentColor`）と機能別カラー（`colors.{feature}.*`）の読み・書き・起動シード。**`tournamentInfo` プレフィックスだがこの族は本サービスのもの**で、`TournamentInfoService` は触らない（歴史的なキー名） |
| `src/core/GameAssetCatalog.py` | `GameAssetCatalog.instance`（`bootstrap_core()` 生成。Qt 側は `AutonaGameAssetManager` が包む） | ゲームアセットの正本（スキャン・ロード・キャラ/スキン/ステージ/アセットパス・Start.gg ID 解決）と `game.*` の書き込み。ロード結果は不変スナップショットで差し替え、リーダーはロックを取らない。書き手のロック保持中に `run_sync` / Qt シグナル / observer を呼ばない（ADR-005 S1a）。**例外は `SelectGame` の `progress` コールバックだけ**でロック保持中に呼ばれるため、渡す側はブロックしない（queued で投げるだけ。アダプタは `AutonaStatusManager` を渡す）。observer から書き手を呼ばない（agent-pitfalls #16）。**Web / Start.gg のリーダーはカタログを直接読む**（ADR-005 S1b）: `WebServerActions.get_games` / `get_characters` / `get_variants` は `*Payload()` を返すだけで `@gui_thread_sync` を持たない。`StartGGDataProvider` は `CharacterFromStartGGId` / `StageFromStartGGId` で解決し、1 つの結果（1 セットのパース・1 エントラント）を組む間は `Current()` を 1 回取って `snapshot=` で渡す（start.gg の ID → 名前の表 `_startggCharacters` はスナップショット外だが、ゲーム非依存なので実害なし）。ゲームの切り替えは codename で `LoadGameByCodename` を呼ぶ（スキャン上の位置を引いてから位置でロードしない。間の再スキャンで別ゲームになる）。終了を待つ側は `done=`（成否を問わず 1 回）を使い、onLoad（実際にロードされたときだけ）で待たない。新しいリーダーをアダプタの旧プロパティ（`games` / `selectedGame` / `characters` ほか。GUI 側の残りは S4 で消える）に繋がない。ペイロードの入れ子のキー順は旧モデル経由の QVariantMap 順を再現している（Socket.IO のバイト互換。`DictHelper.qvariantmap_copy`（S1d で core 共用に移動。選手スロットの値も使う）。str キーの dict と list だけ変換し、tuple と非 str キーの dict は PySide6 と同じく並べ替えない） |
| `src/core/CountryCatalog.py` | `CountryCatalog.instance`（`bootstrap_core()` 生成。Qt 側は `AutonaCountryHelper` が国モデルを作る） | 国・地域（states）・カスタム国旗・都市→地域表の正本（ADR-005 S1c）。`Load()` が唯一の書き手で不変スナップショットを差し替え、リーダーはロックを取らない。state は書かない。ロードは `Window.__init__` の `AutonaCountryHelper.instance.init()`（国名がロケール依存なので `LoadLocale()` の後。ロケールは callable 注入）。**データを返すリーダーは必ずコピー**を返し、表に書き戻さない（旧 `LoadStates` と Start.gg の次セット解決が地域 dict に `asset` を書き込み、`get_states` の出力が「デスクトップがその国を表示したか」で揺れていた）。地域の `asset`（旗パス）は `States()` / `State()` が呼ばれた時点で解決する。`WebServerActions.get_states` は `States()` を返すだけで `@gui_thread_sync` を持たない。Start.gg は `CountryCodeFromEnName` / `FindState` / `State` / `BasicCountryInfo` で解決する。選手の国を code から引くのは `CountryByCode`（標準国 → カスタム国旗の順。国モデル走査と同じ答え。`PlayerService.CountryValue` が使う） |
| `src/core/SponsorHelper.py` | なし（静的メソッド） | `...player.{M}.sponsor_logo` / `sponsor_logos.{I}` の解決と書き込み。`PlayerService.PublishImages` から呼ばれる |

**状態を持たないサービスはモジュール関数でよい**（`Top8Service` / `ColorService`）。
`bootstrap_core()` が作るのは、ボードごとに要る／台帳を持つもの（`SetLoadService` /
`TournamentInfoService` / `PlayerService` / `CrewBattleService` / `AutonaActions`）だけ。

- **`SetLoadService` はステージ行を書かない**。行数を決めるのは `GameTrackerService.SetStageCount()`
  だけで（呼ぶのは `AutonaActions.SetBestOf`。セットの bestOf は `BestOfFromSetData` で読んで
  `SetBestOf` に渡す）、読み込んだスコアは `team.{t}.score` を書いて `_SyncScoreDisplays` →
  `SyncStagesToScore()` に渡す（+1 と同じ経路）。`stages` を直接足す経路を作らない
- **held 配下は「同値なら書かない」**（実装は `StateManager.SetIfChanged` の 1 つ。
  `AutonaActions` / `SetLoadService` / `TournamentInfoService` / `CrewBattleService` が
  それぞれ `_SetIfChanged` として束ねている）。同値でも
  `Set` すればボードは dirty になり 反映 ボタンが点灯する。Qt 経路は
  `setChecked`/`setValue` が無変化なら emit しないことで偶然これを満たしていた。
  `SetIfChanged` も書き込みなので、`tests/test_objectname_keypath_contract.py` の
  書き手検出（`Set`/`Unset`）と同じく core 外のウィジェットからは呼ばない
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

## DictHelper（`src/core/DictHelper.py`）

- `deep_get(d, keys)` / `deep_set(d, keys, value)` でネスト操作
- キーはドット区切り文字列: `"score.1.team.1"`
- StateManager 内部で使用。外部から直接呼ぶ場面は少ない

## Web サーバー（Flask + SocketIO）

- `AutonaWebServer` は QThread として別スレッドで動作
- Flask アプリと SocketIO インスタンスは static 変数
- ルートハンドラは `AutonaWebServerActions` に集約
- Web UI からの POST リクエスト → Actions → StateManager 更新 → WebSocket 配信の流れ
- 新しいエンドポイントを追加する場合は `AutonaWebServerActions` に記述する
  - 例外: **読み取り専用のカタログ系エンドポイントは Actions を経由しない**
    （ADR-001 2026-10-22 Addendum）。`GET /api/v1/layouts` はルートから Qt 非依存の
    `src/core/LayoutCatalog.py` を直接呼ぶ。state を読み書きせず、パネル（Board /
    Commentary / Top 8）と同じ関数を呼ぶこと自体が一本化なので、`WebServerActions` に
    中継メソッドを足しても経路が増えるだけになる。state を書く操作はこの例外に入らない
- CORS は Flask-CORS で全オリジン許可済み。**例外: `/api/designer/imports`** は Origin を `null` / figma.com に限定し、ペアリングコード（`X-Autona-Pairing` ヘッダ）が必須

## Helpers

- `Helpers/` 配下はユーティリティ。各ファイルは単一責務
- `AutonaQtHelper.py` の `GuiExecutor` はワーカースレッドから GUI 操作する際の橋渡し
- `AutonaCountryHelper` / `AutonaLocaleHelper` はシグナルを持つ。変更通知が必要な場面で使う
  （`AutonaCountryHelper` は ADR-005 S1c 以降、国モデル `countryModel` と `countriesAboutToReload` / `countriesUpdated` だけを持つ
  Qt アダプタ。国・地域のデータは `core/CountryCatalog` を読む）
- `AutonaBadWordFilter` は Trie ベース。パフォーマンスに配慮されている
