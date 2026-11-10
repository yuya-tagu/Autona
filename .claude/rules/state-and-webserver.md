---
paths:
  - "src/core/**"
  - "src/StateManager.py"
  - "src/StateManagerBridge.py"
  - "src/AutonaWebServer.py"
  - "src/AutonaWebOperations.py"
  - "src/headless.py"
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
  - held 通知と `state_updated` のハンドラはこの lock の中で走る（GUI スレッドで書いた変更ではウィジェットのハンドラも
    direct に）。その中で別スレッドを待たない（agent-pitfalls #17）
- **core は Qt を import しない**（`tests/test_core_qt_free.py` が subprocess で pin）。
  Qt が要る処理はアダプタ（`src/StateManagerBridge.py` が実例）へ出す
- **永続化の正本は `user_data/state.json`**（ADR-005 S3b。`src/core/StatePersistence.py`）。
  形式は `{"state_version": N, "state": {...}}`。`out/program_state.json` は出力に戻った
  （場所・形式・`timestamp` / `revision`・書き込み順は外部契約のまま。起動時に読むのは、
  `state.json` が無い・使えない・それより新しいときの移行だけ）
  - `SaveState` が公開のたびに `StatePersistence.Schedule(snapshot)` を呼び、専用の書き出しスレッド
    （非 daemon）が最後の変更から 1 秒・最大 5 秒で最新の 1 つを書く（mkstemp → `os.replace`）。
    `out/` と `general.disable_export` からは独立。終了時はインタプリタの join で書き切る
  - **ロック順は `StateManager.lock` → 保存側の lock の一方向**。`Schedule` は `StateManager.lock`
    保持中に呼ばれるので I/O をしない。書き出しスレッドは `StateManager.lock` を取らず、Qt に触らない
  - 起動時: `StateManager.LoadState()` → `StatePersistence.Load()`。使える `state.json` を読む → 無ければ、
    または `program_state.json` の方が新しければ（mtime。クラッシュで間引き中の変更を拾う）そちらから移行して
    すぐ `state.json` を書く。壊れた / 新しい版の `state.json` は `.corrupt-<日時>.bak` / `.v{N}.<日時>.bak` に
    退避して移行に倒す（上書きしない）。読めない（OSError が 3 回続く）・退避できない `state.json` は触らず、
    そのセッションは store を閉じる（書かない）。読み込んだ state に `timestamp` / `revision` は残さない。
    パスは `Load()` 時点の CWD で固定し、`Load()` していないプロセスは書かない
  - **版は封筒にだけ持つ**。state の木に `state_version` を入れない（Socket.IO と `program_state.json` に漏れる）
  - 保存済み state のキー名・形を変えるときは、`src/core/StateMigrations.py` に step を足して
    `CURRENT_VERSION` を上げる（前方一方向・出荷した step は凍結・**冪等**・`StateSchema` を import しない）。
    同じ版の state に残りうるものの起動ごとの掃除（S4n より前のビルドが残した `player_editor` など）は移行ではなく
    `StateSchema.SeedPanelState()` に置く
  - テスト: harness の `settle()` は `StatePersistence.Flush()` まで待つ。`StateManager.Initialize()` を
    呼ぶテストは tmp CWD で呼ぶ（そこで store のパスが決まる）

## StateSchema（キー定義とシードの正本）

`src/core/StateSchema.py`（headless core Step 2）。**state の「何があるか」の定義を
ウィジェットに書かない**。

- held exempt (`STATS_KEYS`) / 表示トグル (`DISPLAY_OPTIONS`) / 起動時シード
  (`SeedBoard` / `SeedPanelState`) はここが正本。ウィジェット側に同じ定義を置き直さない
- `SeedBoard(n)` は「埋める」ではなく **サブツリーを skeleton に戻す**。生きているボードに
  対して呼ばない（未構築ボードのプレースホルダと、`AutonaActions.SeedFreshBoard` の先頭が用途）
- **新しいボードの中身のシードは 1 つの操作 `AutonaActions.SeedFreshBoard(board)`**（ADR-005 S4t。どのスレッドでも・
  ボードの有無によらず動く）。1v1 / チームは `SeedBoard` → 既定のチーム色・空のチーム名・
  LOSERS → `SetRoster`（`DefaultPlayersPerTeam()` 人分。null / 数でないなら 1。足りないスロットを `PlayerService.SeedEmpty()` で埋める）→
  match バー → `best_of` 0 → スコア 0 を 1 サイクルで書き、クルーは `SeedBoard` + `CrewBattleService.SeedDefaults()`。
  **呼ぶのは両シェルで同じで、入口は `AutonaActions.EnsureBoard(board, panel=False)`**（ADR-005 S4y。ボードを 1 回だけ
  作る。2 回目と作っている最中の呼び出しは何もせず False。作ったかは `BoardEnsured(board)`（保存サイクルの書き込みの最後に、
  hold と同じ lock の保持の中で立つ）、忘れるのは `ResetBoards()`（`ResetScoreRoot` もここだけが呼ぶ）。作れなかった・
  描けなかったボードは `ForgetBoard(board)` がフラグを下ろして hold を外す（`StateManager.ReleaseSubtree`。保留中の値は公開する。
  `EnsureBoard` の例外と、デスクトップのウィジェットの構築の失敗）。`SeedFreshBoard` / `HoldBoard` / `ResetScoreRoot` /
  `ReleaseBoard` の呼び出し元は `tests/test_headless_seed.py` が AST で pin）:
  デスクトップは `AutonaScoreboardManager.EnsureScoreboard` がウィジェットの構築の前に `panel=True` で
  （カードは何も書かずにスロットに従う）、ヘッドレスは `src/headless._SeedBoards()` が起動時に全ボードを `panel` なしで。
  `tests/test_headless_seed.py` がボード 1 / 2 / 3 の全体をデスクトップと照合する。
  `panel=True`（デスクトップ）だけがする仕事は 3 つで、1 つの保存サイクルの中でシードの後に行う: 1v1 / チームの
  `schema.PublishDisplayOptions()`（全キーを設定から書き直す。ヘッドレスは `SeedPanelState` が欠けたキーを埋めるだけで、
  復元した値を変えない）と `SetFollowService.TurnOffPolling()`（`general.disable_autoupdate` の強制。ヘッドレスの
  取り直しの既定を変えない）と、最後に `schema.HoldBoard`（反映ボタン。シードより先に登録するとシードが公開されない。
  S4y まではボードの `__init__` がウィジェットの構築の後に登録していた。起動時登録に移すのは React の反映ボタンと同時か後）。
  デスクトップのボードホストの `Built` はこのフラグを読む（ウィジェットの構築はシェルの描画の仕事のまま）
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
  `AutonaScoreboardWidget` / `AutonaScoreboardPlayerWidget` は `findChild` の
  **引数として**使う（objectName から作らない）。**文字列は .ui の objectName と一致していなければ
  ならない**（保存済み state（`user_data/state.json`）の互換に加えて、eye メニューの表示トグルも
  同じ名前で widget を引く）。`tests/test_objectname_keypath_contract.py` が
  一致・「objectName() 非使用」・「選手ウィジェットは StateManager を直接書かない」を pin。
  EVENT タブ（`AutonaTournamentInfoWidget` と `.ui`）は ADR-005 S4g で React のページになり削除した。
  `TOURNAMENT_INPUT_KEYS` は今は `PATCH /api/v1/tournament/info` の field 名
  （3 つの文字列・`numEntrants`・`startAtDate`）で、キーの文字列は不変
- `DYNAMIC_KEY_FAMILIES` は Step 5 の残タスク一覧として縮み、**第 3 弾で空になった**。
  新しいウィジェットを objectName 由来キーで書かない（`tests/test_state_schema.py` が
  空であることを pin）
- **サブツリールートも定数**（Step 5 第 5 弾）: `SCORE_ROOT` / `GAME_ROOT` / `TOP8_ROOT` /
  `COMMENTARY_ROOT` / `TOURNAMENT_INFO_ROOT` / `STREAM_ROOT` / `COLORS_ROOT` /
  `DISPLAY_OPTIONS_ROOT` / `LAYOUT_OPTIONS_ROOT` / `RESULT_PLAYERS_ROOT` /
  `RESULT_TEAMS_ROOT` / `RESULT_RENDER_ROOT` / `RESULT_STANDINGS_ROOT`（ADR-005 S4q） / `RESULT_LAYOUT_ROOT`（S4r） /
  `PROVIDER_VIDEOGAME_ROOT` / `ANALYSIS_ROOT`（ADR-005 S4j）/ `SET_SELECTION_ROOT`（S4v）。`KEY_CATALOG` はこれで keying されているので、
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
  `core/Bracket.py`（旧 `src/AutonaBracket`。ラウンド名は呼び出し側が渡す。ADR-005 S3e-8）/
  `core/SponsorHelper.py`（旧 `Helpers/AutonaSponsorHelper`）/ `core/TextHelper.py`
  （`remove_accents_lower`）。ローカライズ等 Qt 側にしか無いものは
  `bootstrap_core()` から callable で注入する

## AutonaActions（ドメイン操作の唯一の書き手）

`src/core/Actions.py`（headless core Step 3）。**移行済みの操作は
`AutonaActions.instance` 以外が書かない**。インスタンスは `bootstrap_core()` が作る。
永続側のストア（`src/core/SettingsManager.py`）も core にあり、`bootstrap_core()` が `Initialize()` を呼ぶ（import 副作用ゼロ）。

- 移行済み: `AdjustScore` / `ResetScore` / `SetScore`（board 1,2 のみ。`SetScore` は /api/v1 `score:set` 用・
  状態側チーム指定）/ `SetTeamColor` /
  `SetDisplayOption` / `ApplyChanges`（反映 = `CommitHeld`）/ **Step 5 第 1 弾で追加**:
  `SetMatchField`（`phase`/`pool`/`match`）/ `ClearMatchInfo`（リセット時に match バー +
  `match_en` を空にする。S-17）/ `SetTeamName` / `SetTeamLosers` /
  `SetBestOf` / **第 2 弾で追加**: `ClearTeamRoster` /
  `TrimTeamRoster`（チームの選手スロット数。スロットの中身は `PlayerService`）/
  **第 5 弾で追加**: `SetStreamUrl` / `SetStreamTitle`（`SetLayoutOption` は呼び手も読み手も無く、ADR-005 S4s で `LayoutOptionsManager` ごと削除。パネルが組んだ dict をサブツリーごと publish していた `PublishCommentary` は
  ADR-005 S3e-2、`PublishResultPlayers` は S4r で削除し、書き込みは `core/CommentaryService.py` と `core/ResultService.py` へ移った）/
  **ADR-005 S2c で追加**: `SwapTeams`（board 1,2。選手スロット・スコア・LOSERS・チーム名・
  ロゴ・ゲームトラッカーの勝敗（鏡映）を 1 保存サイクルで入れ替え、`score.{n}.teamsSwapped` を
  反転。チームカラーは入れ替えない。board 3 は False を返し、呼び出し側が `CrewBattleService.SwapTeams` を
  呼ぶ（ADR-005 S3e-2。それまではクルーウィジェットに委ねていた））/ `SwapPlayers`（同じチームの 2 スロットを入れ替え。カードの上下移動）/
  **ADR-005 S2d で追加**: `ClearScore`（スコア 0・LOSERS False。match バーは残す = セット読込の前処理）/
  `ResetMatch`（`ClearScore` + `ClearMatchInfo`）/ `ClearPlayers(board, no_mains)`（両チームの全スロットを
  旧カードの `Clear()` と同じ内容で空にする。birthday・city・mains は残る）/ `ClearBoard`（すべての入力値をリセット =
  選手 + `ResetMatch` + チーム名・ロゴ）。いずれも board 1,2 だけで 1 保存サイクル、board 3 は False
- **クリアは state の書き換え**（S2d）。旧実装はボードのウィジェットを空に戻していた（カード 1 枚ごとに 1 サイクル）。
  ボードに残るのは `AutonaScoreboardWidget._ClearingPlayers` の 2 つ: 入力中の欄の確定（`CommitFocusedEdit`。打ちかけの文字がクリアで消えるのは選手欄だけで、match / チーム名 / スコア欄の打ちかけは旧実装どおりフォーカスアウトで確定する。セット読込の前処理でもフォーカスが外れる）・
  追跡中セットを忘れる（同じセットの再読込が効くように）。id が変わったチームの統計の取り直しは操作の仕事
  （`ClearPlayers` → `StatsService.TeamsChanged`。ADR-005 S3e-5。それまではボードが出していた）。
  Web の `clear_all` は `ClearBoard` ではなく（`ClearBoard` は v1 の `:reset-all` が呼ぶ。ADR-005 S4u）、旧順序（`ResetMatch` → best_of → 人数・キャラ数 → 選手）で呼ぶ
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
  `AutonaWebOperations.get_swap` は state を読む。スワップ後のカード・スコア・チームカード・ステージボタンは
  held サイクルからの描画で追従する。統計の再取得は操作が出す（`SwapTeams` / `SwapPlayers` → `StatsService.TeamsChanged`。
  イベントループに戻ってから 1 回。ADR-005 S3e-5。それまではボードが held サイクルでフラグの反転を検知して出していた）。
  アップセット係数は操作が core 内で再計算する。
  入替はスコアの変化を行に追従させない（`FollowScores` を呼ばない。記録は入替が鏡映済み。ADR-005 S4t）。
  **入力中の欄は入替・移動の前に確定する**（`AutonaScoreboardWidget.CommitFocusedEdit`
  がそのボード内のフォーカスを外し、Qt に `editingFinished` を出させる。欄の補完ポップアップが開いていれば
  先に閉じる（開いたままだと確定されない）。SWAP ボタン・上下移動・ホットキー・Web の全入口。
  旧実装は打ちかけの文字を選手と一緒に運んでいた）
- **クルーボード（board 3）は `CrewBattleService` が書き手**（Step 5 第 4 弾）。
  ただし *入口* のうち board 1/2 と語義が同じもの — チーム名（`SetTeamName`）・
  チームカラー（`SetTeamColor`）・match バーの 3 combo（`SetMatchField`）— は
  `AutonaActions` を呼ぶ。`AdjustScore` / `ResetScore` は board 3 では今も no-op で、
  クルーの操作（ストック・出場中のメンバー・人数・スワップ・ストックのリセット・クリア・セットデータ）は
  `CrewBattleService` の操作（ADR-005 S3e-2）。ボードの入力欄も Web（`AutonaWebOperations`）も同じ操作を呼び、
  操作は state から読んで書く。ヘッドレスでも動く。
  ボードは state を描画するだけ（`AutonaCrewBattleWidget._RenderFromState`。Bridge の通知で 1 回にまとめて遅延描画し、
  入力中の欄は飛ばす。確定した欄は選手カードと同じ held-back の規則で、描画が残した古い文字を書き戻さない）。
  スワップのフラグは board 1/2 と同じ `score.3.teamsSwapped`（ADR-005 S4w。ページが delta で追従するため。それまでは state の外で通知が無かった。
  シードは書かず、無ければ false。`SeedFreshBoard` の `SeedBoard` と起動時の `ResetScoreRoot` で消える）。
  v1 はボードの入力欄と同じ操作を呼ぶ（ADR-005 S4w。`external-control-api.md` §2.12）: 1 メンバーの `UpdateMember`（名前・ストック・出場中を 1 回の lock と 1 保存サイクルで）・
  `AdjustMemberStock`、`AutonaWebOperations.crew_update_set`（`PATCH set`。legacy の `set_route` は通らない: チーム名を書かず値を丸める）・`crew_team_color`。
  **同じ値は書かない**（`_RepublishBoard` は変わらないボードを書かない）。書き込みは `Prepare` で未構築のボードを構築してから。
  リセット 3 本は未構築のボードを構築しない（`AutonaApiV1._crew_not_built`）。読み取りの X 投稿文は `PostText`（ボタンも同じものを呼ぶ）。
  ボード 1 / 2 の X 投稿文は `AutonaActions.PostText(board)`（ADR-005 S4x。`(text, missing)` を lock の 1 回の保持で読む。
  ボタンはこれを呼んで、`missing` があれば断り、無ければサムネイルをコピーしてから読み直した `text` で X を開く）。
  v1 は `GET /scoreboards/{n}/post`（`get_board_post`。3 ボードとも）で、反映前の値と選手の X ハンドルを返すので PLAYERDB
- **対戦形式の切り替えは `AutonaActions.SetBattleFormat(fmt)`**（ADR-005 S4x。それまでは `Window.SetBattleFormat`）。
  サイドバーと v1 の `PUT /battle-format`（`AutonaWebOperations.set_battle_format`。ボードホストの `Run` の中）が呼ぶ。
  いまの形式（`ResolveBattleFormat`）と同じなら何も書かず何も知らせずに False。変わるときは設定 2 つ
  （`scoreboard.battle_format` と旧 `team_mode`）→ `AutonaActions.battleFormatChanges`（クラスの `ObserverChannel`。
  Bridge が `battle_format_changed` に変換し、`Window._ShowBattleFormat` がボードの表示とサイドバーを合わせる。
  両シェルの `WebServer.on_battle_format_change` が Socket.IO の `battle_format` にする（ADR-005 S4z。React の SCORE ページが形式のボードを出すため。S4x は「設定なので通知しない」だった））→ `ResultService.FollowBattleFormat` の順。通知は切り替えたスレッドで出る（デスクトップの v1 は
  GUI スレッド）。起動時の形式の決定（どのボードを最初に建てるか）は Window のまま（S4y の範囲）
- **best_of は state 主導**（ADR-005 S2e）。`SetBestOf(board, n)`（board 1,2。board 3 は False で、
  クルーはスピンボックス → `CrewBattleService`）が 6 キーとステージ行を 1 `SaveBlock` で書く。
  0〜99 に丸め、値が変わらなければ何も書かない（旧 `valueChanged` と同じ。同じ値でゲームの記録は消えない）。
  ゲームのロードは `RepublishBestOfs()`（シードされた全ボードで文言を作り直し、同じ行数で行をリセット。呼ぶのは
  `bootstrap.wire_game_roster` で、両シェルが同じ。ADR-005 S4t）。反映まで済ませるのは `CommitGameLoad()` で、
  デスクトップのアダプタの `_Publish` が `onLoad` の後に呼ぶ（カードの作り直しが onLoad の中で書くので、
  先に commit すると反映ボタンが点いたまま残る。ヘッドレスは held が無いので呼ばない。Qt のカードが消えたら結線へ移せる）。
  行は `AutonaActions.GameTracker(board)`（ボードごとに 1 個で常にある。ADR-005 S4t）に、操作の block の中で
  `SetStageCount` させる（通知ハンドラの中で書かない = 再入しない。ボードの有無によらないので、ヘッドレスにも行がある）。
  スピンボックスと行は held サイクルの `_SyncBestOf` が描画する（`_SyncSetFields` の後、
  `_SyncScoreDisplays` の前。入力中のスピンボックスは飛ばし、`editingFinished` で描画し直す）。
  入口はすべてこの操作: スピンボックス（`_onBestOfChanged`）・セット読込（`SetLoadService.BestOfFromSetData`。
  0・空は運ばない）・Web `set_route` の bestOf（v1 `PATCH set` 含む）・`AutonaWebOperations._reset_match`・`ClearBoard`（最後に 0）
- **ローカライズ文字列は callable で注入**する。`best_of_text` / `first_to_text` は
  `AutonaLocaleHelper.FormatMatchText` を `bootstrap_core()` が `AutonaActions` に渡す
  （`TournamentInfoService.formatEventDate` と同じ形）。未注入時と、注入関数が例外を出したとき
  （ロケール未ロードの空の表・`.format()` が通らない独自用語）は英語既定にフォールバックする
  （後者は警告を出す。`SetBestOf` は入口から直接呼ばれるので、例外を上げるとセット読込の残りや
  ゲームのロードの `CommitHeld` が止まる。ADR-005 S2e）。int にできない best_of は警告して何も書かない
- `score.{n}.team.{t}.score` の書き手は **core 内に 3 系統**（`AutonaActions`、
  ステージ集計の `GameTrackerService.PublishStageResults`、
  セット読込の `SetLoadService.ApplyScores`。`AutonaActions` の書き手は `SetScore` / `AdjustScore` / `ResetScore` と、S2c からの `SwapTeams`、S2d からのクリア）。
  **スコア欄への数値の手入力**も、その `valueChanged` が v1 の `score:set` と同じ `AutonaActions.SetScore` を呼ぶ
  （ADR-005 S4x。`scoreDisplay1` / `scoreDisplay2` がそれぞれ自分のチームの値だけを `SetScore(board, t, value)` で渡す。左の欄が state のチーム 1。それまでは `GameTrackerService.UpdateScore` を呼んでおり、行の追従と 2 サイクルに分かれていた。
  `UpdateScore` は削除した。`tests/test_score_typing.py` が手入力・デスクトップの route・ヘッドレスの route の書き込みを照合する）。
  **core の外から直接 `Set` する経路は残っていない**（`StageResultsToScore` の
  再 Set は検収レビューで削除済み。表示更新のみ）。
  **スコアへの行の追従はスコアを書いた操作自身が core で行う**（ADR-005 S4t。`before = tracker.Scores()` → 書き込み →
  `tracker.FollowScores(before)` を 1 つの `SaveBlock` と `StateManager.lock` の中で。`AdjustScore` / `ResetScore` /
  `SetScore` / `ClearScore`・セット読込の `SetFollowService.ApplySetPayload`）。旧実装はボードの held サイクル
  （`_SyncScoreDisplays`。旧値はスピンボックス）で行っていたので、ボードの無いヘッドレスには追従が無かった。
  トラッカー ON で何ゲーム分動いても 1 サイクル（旧は「スコア 1 + 追従 1」）。行を集計してスコアを書く側
  （`PublishStageResults`）と入替は追従を呼ばない（呼ぶと 1 クリックで次のゲームまで勝ちになる）ので、
  ガードの `writingScore` は要らなくなった。`_SyncScoreDisplays` は描画だけ
- 呼び出し側は Qt ウィジェット・`AutonaWebOperations`（Web の操作。ADR-005 S3c）・`AutonaHotkeys` の 3 つ。
  **どのスレッドから呼んでもよい**（Qt に触れないのが層の定義）。core 直結の Web 操作は呼び出し元
  （Flask / Socket.IO）のスレッドで core を呼び、`@gui_thread_sync` を持たない。
  S2 で core 化した操作（`swap_teams`・S2d のクリア `reset_match` / `reset_players` / `clear_all`・`get_set`・
  `get_swap`・`set_route` の phase / pool / match / losers / teamName / bestOf・`set_team_losers`・`reset_scores`）と、
  S3e-4 で移した選手の Web 操作（`update_player` / `set_team_data` / `load_player_from_tag`。`PlayerService.ApplyData`）と、
  S3e-5 で移した統計（`stats_*` 4 本・`refresh_stats`。`StatsService`）と、
  S3e-7 で移したセット読込と追従（`load_set` / `post_score` / `pull_stream_set`。`SetFollowService`）は
  どちらのシェルでも core を呼び、**その周りのボードの仕事をシェルが登録するボードホスト
  （`AutonaWebOperations.boardHost`）が受け持つ**:
  - デスクトップ（`DesktopBoardHost`。`bootstrap_gui()` が登録）: GUI スレッドで同期実行する（選手カードの入力確定が
    GUI スレッド上で「フィールド → 合成名」の 2 書き込みに分かれており、その間に割り込むと合成名が反対のスロットに
    残るため。選手の読込を core にした S3e-3 の後も、手入力の確定はこの 2 書き込みのまま。S3e-4 で見直して維持した。
    選手の Web 操作の後のカードの仕事（打ちかけのコンボの文字を捨てる。`PlayerLoaded`）も GUI スレッドの仕事）。未構築ボードを `EnsureScoreboard` で構築してから書く（未構築ボードは held でなく、
    書き込みが即時公開されたあと構築時のシードで消えるため）。入力中の欄を確定する。ボード 1 / 2 の
    人数 / キャラ数も ADR-005 S3e-6 から core（`AutonaActions.SetRoster`。それまではボードのスピンボックスで、
    ヘッドレスは 503。クルーボードの人数は S3e-2 から core）。カードは held サイクルで state のスロットに追従する。
    選手のクリア（`ClearPlayers`: 入力中の欄の確定 → `AutonaActions.ClearPlayers` → 全カードがコンボの打ちかけの文字を捨てる
    （`DiscardTypedText`。リストが開いているコンボも。ほかからの読込の後（`PlayerLoaded`）は入力中のコンボを残すが、クリアは操作者のボードの操作なので残さない）→
    最後に読んだセットを忘れる）と最後に読んだセット（`LastSet`。`SetLoadService`）は ADR-005 S4x から基底クラスの 1 つで、
    デスクトップが足すのは構築・`CommitEdit`・`DiscardTypedText`・`PlayerLoaded` だけ（それまではボードの `CommandClearAll` と属性。`get_set` もボードを構築しない）。
    キャラ枠の無いボードが次の選手に渡すキャラ数の記憶も core（`AutonaActions.EmptyRosterCharacters`。ボードの隠れたスピンボックスはそれを描画する）
  - ヘッドレス（`BoardHost`。既定）: 呼び出し元のスレッドで core を呼ぶ。1 本の再入可能ロック（`RLock`）で 1 つずつ
    実行する（GUI スレッドに並ばないので、`clear_all` や v1 `PATCH set` の途中に別の要求が混ざらないように。
    ロックの中でメインスレッドを待たない・`StateManager.lock` を持ったまま取らない。待つもの（`savePlayerToDb` の選手 DB への
    書き込み: `PlayerDB.changed` → `AutonaPlayerDB.SetupModel` がメインスレッドで走る）は `Run` を抜けてから行う。S3e-4 レビュー）。ボードが無いので held にならず即時公開
    （反映ボタンが無いのでこれが正しい）。ボード 1〜3 以外（どのシェルにも無いボード）は操作自身が両シェルとも `NotAvailable`（503）で断る
    （ADR-005 S4x でボードホストの `Widget()` を削除した。それまではそこで 503、デスクトップは 500）。ボード 1 / 2 の
    人数 / キャラ数（`set_route` の `players` / `characters`・`clear_all`）とゲームのロードの既定人数も、
    S3e-6 からは両シェルで同じ core の操作（ゲームのロードは `bootstrap.wire_game_roster`）。統計は両シェルとも
    core が取り直す（ADR-005 S3e-5。`PlayerLoaded` は何もしない）
- **SaveBlock は複数キーを 1 サイクルにまとめる時だけ**使う（単一キーなら不要）。
  かつては「空 `changedKeys` の SaveState = `include_paths=[]` = フィルタなし =
  全 state 差分」という地雷があったが、**Step 4a で `SaveState` 側が空 `changedKeys`
  を早期 return するようになった**ので、包んでも全量 diff にはならない
  （`tests/test_state_manager_held.py` が DeepDiff 呼び出し回数で pin）
- ウィジェット側は通知（`held_state_updated` / `state_updated`）で表示を更新する。
  プログラム的更新は必ず `QSignalBlocker` で囲む（囲まないと `valueChanged` 等が
  発火して書き戻し = エコーループ）。実例は `AutonaScoreboardWidget._SyncScoreDisplays`
  （描画だけで書かないので再入ガードは要らない。S4t で `_syncingScore` を削除）。
  パスを積み重ねないための再入ガードの実例は `_RenderPlayerCards`（`_renderingPlayers`）
- ホットキーは `AutonaHotkeys.ConnectActions()` が `bootstrap_gui()` から 1 回だけ結線する（ADR-005 S3c でデスクトップ側の段へ移動。ヘッドレスにはホットキーが無い）。
  対象ボードは `AutonaActions.ActiveBoard()`（`SettingsManager.scoreboard.battle_format`）。
  `swap_teams` も S2c からここ（旧: 構築済みの全ボードに接続しており、形式切替後は両ボードが入れ替わった）。
  ただし `swap_teams` だけは、アクティブボードが未構築なら何もしない（旧実装どおり。スコアのホットキーは
  未構築でも効く。未構築ボードは held でないので、入れ替えると即時公開され、構築時のシードで消える）。
  `swap_teams` の未構築判定と入力中の欄の確定はボードホスト（`boardHost.Built` / `CommitEdit`）に聞き、ボードの
  ウィジェットを引かない（ADR-005 S4y）。パネル操作の `load_set` も S4y から `ConnectActions()` が結線し、アクティブ
  ボード（1 / 2）のセット選択をシェルのアダプタ `AutonaShellOperations.ShowSetSelection` で開く（クルー形式・
  未構築なら何もしない。それまでは構築済みの 1v1 / チームの全ボードが自分のウィンドウを開いていた）。SCORE が React の
  ページのとき（ADR-005 S4ab-2）はメインウィンドウを前に出して SCORE に切り替え、ページのダイアログを開く（`Window.OpenScoreSets`
  → 共有ビューの `autonaPanelShell.openScoreSets`）。退路の Qt モード（`general.qt_scoreboard_panel`）では Qt のウィンドウ。
  ページへの経路の例外はログだけで、ホットキーのハンドラに漏らさない。
  ボードに残るホットキーは無い（`refresh_phase_group` は ADR-005 S3e-8 から
  `ConnectActions()` が `Top8Service.RefreshFromProvider` に直結する。それまでは Window が Top 8 パネルを構築して呼んでいた）
- **未構築ボードは held 登録しない**（held 登録は `AutonaActions.EnsureBoard(panel=True)` の `StateSchema.HoldBoard`
  だけ。S4y まではボードの `__init__`）。
  未構築ボードへの core 直結の書き込み（`score:adjust` / `score:set` / `score:reset` / チームカラー）は即時公開される。
  起動時から held にする案（ADR-005 S2 判断 7）は S2c で撤回した: Web UI には反映ボタンが無く、デスクトップの反映は
  表示中のボードだけなので、形式と食い違う面への書き込みがどこにも出ないまま、構築時の `SeedBoard` で消える。
  held が残り続けると保存サイクルごとに state 全量の複製も走る。
  起動時登録に移すのは React の反映ボタンと同時か後（`docs/changes/architecture-modernization.md` の「S4 SCORE の React 化」決定 3）

## ドメインサービス（core の per-board オブジェクト）

`AutonaActions` が「1 回の操作 = 1 メソッド」の入口なのに対し、状態を持つ／ボードごとに
インスタンスが要るドメインロジックは core のサービスに置く（headless core Step 4a）。
**Qt 側は薄いアダプタに徹する**（`AutonaStatsUtil` / `AutonaIndividualGameTracker` が実例）。

| サービス | 所有者 | 責務 |
|---------|--------|------|
| `src/core/StatsService.py` | なし（`StatsService(n)` を呼ぶたびに作る。取得の `fetcher`（`AutonaStatsUtil`。`bootstrap_core()` が登録）と要求番号はクラスの持ち物） | 統計のすべて（ADR-005 S3e-5）: いつ取り直すか（`PlayerChanged` = 選手の読込・クリア・カードの手入力の id / seed の変化。`PlayerService.Announce` が書き込みの保存ブロックの中で呼ぶ / `TeamsChanged` = スワップ・上下移動・クリア。`AutonaActions` が操作の後に呼び、`fetcher.Later` でイベントループに戻ってからボードごとに 1 回 / `Refresh` / `RefreshAll` = Web のルート。統計情報ページ（React の `StatsPage`。ADR-005 S4k）の「統計情報を取得する」は v1 の `POST /api/v1/scoreboards/1/stats:refresh`）・取得可否の判断・アップセット計算・`last_sets`/`history_sets`/`recent_sets`/`upset_factor` の書き込み。人数は state のスロット数（0 なら `DefaultPlayersPerTeam()`）。**遅れた答えは捨てる**: 要求ごとに（ボード, 統計, チーム）の番号を `StateManager.lock` の中で振り、答えはメインスレッドで、番号が最新かつスロットが要求したときの選手のままのときだけ書く。番号は 1 つのカウンタから出す（キーをまたいで重ならない）。取得の開始はロックの外。**取得の失敗は統計ごとにログに残して次へ進む**（`RefreshQuietly`。いくつも依頼する入口と Web のルートはこれを呼び、ルートは `"OK"` のまま。旧実装は統計ごとの Qt シグナルだった）。ボードの構築・シェルによらず動く |
| `src/core/SetFollowService.py` | なし（`SetFollowService(n)` を呼ぶたびに作る。何を追従しているか・ステーション・要求番号はボード番号ごとにクラスが持つ。取得と 5 秒の時計の `fetcher`（`AutonaSetFollowUtil`）とボードホストの引き方 `boardHost` は `bootstrap_core()` が登録） | セット読込と追従のすべて（ADR-005 S3e-7。それまではボードのウィジェット・2 つの QTimer・provider アダプタの Worker）: `LoadSet`（セット選択ウィンドウ・Web の `load_set`。クリア → 追従の記録 → 取得 → 答えを `UpdateSetData`。**大会が読み込まれていなければ何もしない**＝ボードを消さない）・`FollowStation`（ステーションウィンドウのステーション / 配信）・`PullStream`（Web の `pull_stream_set`。**追従中のチャンネルなら照会し直すだけ**で、セットが変わったときだけ読み直す。Web UI の自動更新スイッチが数秒ごとに送るので、毎回追従し直すとクリア → スコア 0 になる。追従を始める最初の 1 回も、最後に読んだセットと同じならクリアしない）・`StopFollowing`（タイマー行のキャンセル）・`Tick`（5 秒ごとの取り直し。`general.disable_autoupdate` が明示的に `false` のときだけ。デスクトップはボードの構築のたびに `true` にするが、設定画面の「Disable automatic set updating」を外すと次の構築まで取り直す）・`ApplySetPayload`（旧 `ChangeSetData`。取得したセットと `POST /score` の 1 保存サイクル）。**判定と書き込みはすべてボードホストの `Run` の中**（答えの照会番号・要求番号の判定もその先頭で行うので、判定の直後に別スレッドの Web 操作が割り込まない）。取得の答えはメインスレッドに届く（`fetcher` の `Signal(object)`。旧 Worker 自身のシグナルは GC で答えが消えることがあった）。**遅れた答えは捨てる**: 読んだセットでない答え（`IsStaleUpdate`）・後から全読込が始まった取り直し（要求番号）・手動 / Web の読込（読み込んだときだけ）・別のステーション・キャンセルの後に返った照会と、後から出した照会が先に答えた照会（照会番号。pull と tick で 1 列）。**全読込の取得が失敗したら（セットの無い答え・例外・取得できない）、最後に読んだセットを忘れる**（ボードはクリア済みで何も出ていない。次の pull・照会・tick・同じセットの選び直しで読み直す）。ステーションは先頭のセットだけを読み、後続セットは取得しない（ADR-005 S3f-3。表示する箇所が無く、`score.{n}.station_queue` は `{}` のまま）。ロックの中で取得を待たない（pitfalls #17）。1 人分の `POST /score`（`team` / `player`）は state に無いスロット（"0"・負の数を含む）を書かずにログに残す。**何を追従しているかは state に入れない**（`tests/test_set_follow_flow.py` の golden が全ての書き込みを照合し、`program_state.json` のキーも増えるため）。読むのは `Snapshot()`（`{station, poll: {mode, identifier, secondsLeft}, pollingEnabled, setId}`。どのスレッドでも。v1 の `GET /scoreboards/{n}/follow`）、変わったことを知るのはクラスのチャンネル `changes`（`ObserverChannel`。引数はボード番号。ホストの中で出るので `StateManager.lock` の中でありうる。受け手は積むだけにする。`WebServer.on_follow_change` が `scoreboard_follow` にする。**変わったときだけ出す**: ステーションか取り直しの時計が変わったとき（止めるものが無いキャンセル・同じステーションの選び直しでは出ない）と、ボードに書かずに `setId` が null に戻ったとき（`_LoadFailed`・`_Follow` の読み直し）。Qt のタイマー行は従来どおり `fetcher.FollowChanged`）。`fetcher.RemainingMs` は次の tick の時刻（`time.monotonic`）から計算するので**どのスレッドからでも呼べる**（ADR-005 S4v。`QTimer.remainingTime()` はイベントディスパッチャの無いスレッドから呼ぶとプロセスが落ちる。実測）。その時刻は別スレッドの `Poll` がその場で記録する（ヘッドレスの読み込みはリクエストのスレッドで走り、タイマーの開始はメインスレッドへ queued なので、先に出る通知の読み手に `secondsLeft: null` を見せない）。v1 の入口は `:follow` / `:stop-follow`（`AutonaWebOperations.follow_station` / `stop_follow`。窓の入口と同じ `FollowStation` / `StopFollowing(forget=True)` を呼ぶ。`:follow` は Prepare で未構築のボードを構築し、`:stop-follow` は `BoardHost.Built` を見て未構築なら構築も書き込みもしない。キャンセルの書き込みは `SetIfChanged` で、何も変わらなければ反映ボタンを点けない） |
| `src/core/GameTrackerService.py` | `AutonaActions.GameTracker(board)`（ボード 1 / 2 に 1 個ずつ、`AutonaActions` の生成時に作る。ボードの `AutonaIndividualGameTracker.service` は同じ 1 個。ADR-005 S4t。それまではボードが作って登録していた） | ステージ勝敗の記録（`score.{n}.stages.*`）・ステージ⇔スコアの相互整合（スコア → 行は `Scores()` + `FollowScores(before)`）・行の作り直し（`SetStageCount`。呼ぶのは `AutonaActions.SetBestOf` / `RepublishBestOf` だけで、`rowsVersion` が作り直しのたびに進み、ウィジェットの `RenderStageRows` がそれを見てボタンを作り直す）。存在しない行への `SetStageResult` / `SetStageAsset` は lock の中で断る（警告して False。別スレッドの `SetBestOf` で行が消えた後、作り直し前の古いボタンが押されても行を書き戻さない） |
| `src/core/SetLoadService.py` | `AutonaScoreboardWidget.setLoad`（`SetFollowService` も同じボードの 1 個を作って使う） | セット読込の正規化（ラウンド/フェーズ/プール・スコア・敗者フラグ・チーム名とロゴ・`bracket_type`・追従中セット）。最後に読んだセット `lastSetSelected` も持つ（state の外。ADR-005 S2d でボード属性から移動。S3e-7 からボード番号ごとにクラスが持つので、ボードのインスタンスでもヘッドレスでも同じ値を読む。クリアと `ClearSetTracking()` で忘れるので、同じセットを選び直すと再読込される。`get_set` と stale 判定が読む）。スワップ後の面の解決は `DisplayTeam()` が唯一の正本（`score.{n}.teamsSwapped` を読む。S2c までボード属性を callable で注入していた） |
| `src/core/TournamentInfoService.py` | `TournamentInfoService.instance`（`bootstrap_core()` 生成） | `tournamentInfo.*` の書き込み全部（provider ペイロードの適用・**大会情報ページの入力欄の `Set{Text,Number,Date}Field`**（`PATCH /api/v1/tournament/info`。日付は `SetDate`）・表示大会名の 3 系統の上書き規則・起動時シード（`logoVersion` / `bannerVersion` の publish と `media` の idle 戻し）。日付は整形済みの `startAt` と並べて `startAtDate`（`YYYY-MM-DD`）を同じ保存サイクルで公開し、日付欄は `DateOf` で描いて `SetDate(field, "YYYY-MM-DD")` で書く（その日の 0 時の unix 秒への変換は core。ADR-005 S4f）。ページを開いていなくても（ヘッドレスでも）動く。**両方向が同じ 3 操作を通る**（provider 側の `_ApplyKey` も入力側と同じメソッドを呼ぶ）。ただし `themeColor`/`accentColor` の 2 キーだけは **`core/ColorService.py` の持ち物**で本サービスを通らない（歴史的にこのプレフィックスなだけで、意味はカラー機能側）。**表示大会名の入力欄は SCORE パネルとクルーボードの 2 箇所にあり、どちらも `SetDisplayName` を呼ぶ**（クルー側は Step 5 第 4 弾で合流） |
| `src/core/PlayerService.py` | `AutonaBasePlayerWidget.player`（選手ウィジェット 1 枚につき 1 個。path に束縛）。加えて**一時インスタンス**: モジュール関数 `RepublishTeamMergedNames(board, team)` が、state にあるそのチームの各スロットの path で 1 個ずつ作って使い捨てる（カードを経由しない） | 選手スロット配下の書き込み全部（入力フィールド・seed/birthday・国/州・`mergedName`/`mergedOnlyName`・アバターとスポンサーロゴ・キャラスロット・`city`/`id`・スロット削除）。**選手の読込（`ApplyData(data, loadFromDB, clear, no_mains)`）・カードのクリア（`ClearSlot`）・DB への保存（`SavePlayerToDB` = state 側の `PrepareSaveToDB`（番号付きの記録を返す）+ DB 側の `SaveRecordToDB`。Web の操作は DB 側をボードホストの外で呼ぶ: `PlayerDB.changed` の受け手 `AutonaPlayerDB.SetupModel` がメインスレッドを待っていたため（ADR-005 S3f-2 で待たなくなった）。外へ出したので、同じ DB キーにもっと新しい番号の記録が保存済みなら古い方を捨てる。ADR-005 S3e-4 レビュー）もここ**（ADR-005 S3e-3）: 読込は DB の記録とのマージ・悪語フィルタ（コピーに対して。呼び出し側の dict と DB の記録は書き換えない）・国 / 地域（code と `FindState`）・mains → キャラスロット・seed・合成名と画像を 1 保存サイクルで書き、旧カードの `SetData` と同じ Set 列を出す（`tests/test_player_apply.py` / `tests/test_player_service.py` が ae922c24 の録画と照合）。操作の前後で id / seed が変わったか（`SlotChange`。戻り値でもある）で、読込自身が統計を 1 回だけ依頼する（`Announce` → `StatsService.PlayerChanged`。ボードのスロットだけ。`SetSeed` / `SetPlayerId` は `announce=True` のときにカードの手入力として依頼する。ADR-005 S3e-5。それまでは呼び出し側のカード・ボード・ボードホストが依頼していた）。**クリアしない読込（Web の選手編集・legacy `update-team`）で、書き込み後のスポンサー + タグの組が前と違えば別の選手として扱い、読込が書かない id・city を空にする**（`_NamesAnotherPlayer`。ADR-005 S3e-4。同じ組なら残す。残すかは読込が実際に書いたかで決めるので、途中の例外でも前の選手の id が残らない。online_avatar・seed は残す: カードの手入力の `NameChanged` と同じで、LIVE-BUG-1「タグを直しても顔写真は消えない」を守る）。保存はウィジェットではなく state から組む。**v1 の欄とキャラの行の編集もここ**（ADR-005 S4z）: `Edit(texts, country, region)` はカードの入力欄の確定（文字の欄・国・地域。スポンサー / タグが変われば合成名と画像を作り直して id を空にし、統計を依頼する。途中で例外が出ても書いたぶんについて同じことをする: `ApplyData` と同じ保証）、`EditCharacter(row, character, skin, variant, game)` はキャラ行の 3 つのコンボの選択（行は state のキャラ枠の数だけ。0 なら行は無い）。どちらも 1 保存サイクルで、断るときは何も書かずに理由のコードを返す。`AutonaActions.ClearPlayers` も各スロットの `ClearSlot` を呼ぶ。`BoardNumber()` は `score.{N}` の番号を返す（それ以外のパスは `None`）。**`mergedName` / `mergedOnlyName` の ` [L]` は state のチームの `losers` から付ける**（`PublishMergedName(sponsor, tag)` が `TeamLosersKey()` を読む。ADR-005 S2b）。フラグの書き手は同じ SaveBlock で `RepublishTeamMergedNames` を呼び、チーム全員の名前を state の `team` / `name` から作り直す: `AutonaActions.SetTeamLosers`（LOSERS チェックボックス・v1 `PATCH set` の `AutonaWebOperations.set_team_losers`）と `SetLoadService.ApplyLosers`（セット読込・legacy `-set?losers=` の `ChangeSetData`。`DisplayTeam` で解いたチーム）。どちらもフラグが変わったときだけ。カードは名前確定で `_OnTextChanged`（state に書く）→ `NameChanged`（publish）の順に走るので、作り直しがどのスレッドから割り込んでも同じ名前になる。**国/州とキャラスロットはカードから識別子だけを受け取り、値はここで組む**（ADR-005 S1d）: `SetCountry(code)` / `SetRegion(countryCode, regionCode)` は `CountryCatalog.CountryByCode` / `State` から、`PublishCharacterSlots([CharacterSlot(en_name, skin 行, variant en_name, フォールバック名), ...])` は `GameAssetCatalog.CharacterRow` / `SkinRow` / `VariantRow` から（1 回の publish は `Current()` 1 回のスナップショットで解決）。入れ子のキー順は旧実装が Qt モデル経由で出していた QVariantMap 順を `DictHelper.qvariantmap_copy` で再現する（`program_state.json` と Socket.IO delta のバイト互換。`tests/test_player_slot_publish.py` と `tests/test_player_service.py` が移行前の golden と照合）。カタログが知らない識別子は、キャラなら `{"name": フォールバック名}`（手入力テキスト、または選ばれた行自身の `name`。コンボの「表示名 / en_name」ラベルは使わない）、国/州なら `{}`。カタログは `games=` / `countries=` で差し替え可能（既定は呼び出し時の `.instance`） |
| `src/core/CrewBattleService.py` | `AutonaCrewBattleWidget.crew`。Web（`AutonaWebOperations`）は呼び出しごとに 1 個作る | クルーボード（board 3）の書き込み全部（起動シード・メンバー名・ストック（`AdjustMemberStock` / `SetMemberStock`。0〜99）・人数（`SetMemberCount`。1〜9）・出場中のメンバー（`SelectActiveMember` / `ReleaseActiveMember`。1 チーム 1 人）・**クルー合計の算出**（名前が入った表示中メンバーの stock 合計 → `team.{t}.score`）・crew 版のセットデータ正規化（`ApplySetData`）・`best_of`（raw のみ・0〜99）・スワップ / ストックのリセット / クリア（`SwapTeams` / `ResetStocks` / `ClearCrew`。両チームを 1 サイクルで publish。変わらないボードは書かない）・スワップのフラグ（`score.3.teamsSwapped`。ADR-005 S4w）・1 メンバーの部分更新（`RenameMember` / `UpdateMember`。ADR-005 S4w）・X 投稿文（`PostText`。読むだけ。ADR-005 S4w）。**操作は state を読んで書く**（ADR-005 S3e-2。旧実装はウィジェットの行を受け取っていた）。合計も state から算出する |
| `src/core/CommentaryService.py` | なし（モジュール関数） | `commentary.*` の書き込み全部（ADR-005 S3e-2）: 選手データでの 1 枠の上書き（`ApplyPlayerData`。Web と補完の選択。悪語フィルタ → 正規化 → 1 枠をマージしてルートごと 1 回 publish = 旧パネル経由と同じ Set）・タグからの読込（`LoadFromTag`。`PlayerDB`）・パネルの 1 欄の編集（`SetField`。値が変わったときだけ。表示は `MAX_DISPLAY` 人まで）・並べ替え（`Reorder`）。実況パネルは Web 版（React の `CommentaryPage`）だけで、state を描画するだけ（書き手は core の本サービス。ADR-005 S4c で Qt 版を削除） |
| `src/core/Top8Service.py` | なし（モジュール関数） | `top8.*` の書き込み全部（ページの選手の編集 `UpdatePlayer`（ADR-005 S4i から React のページが `PATCH /api/v1/top8/players/<slot>` で呼ぶ。1 リクエストの欄を `StateManager.lock` の中でゲームのカタログの 1 つの snapshot と国の 1 回の参照で解決し、1 つでも断れば何も書かず、1 保存サイクルで書く。キャラに `game` が付いていれば読み込み中のゲームの codename と照合する）と、1 欄の `SetPlayerText` / `SetPlayerCountry` / `SetPlayerCharacter`（その 1 キーだけを、変わったときだけ）・表示ラベルの既定値・反映（staged スコアのマージ + `Recompute`）・全リセット・Start.gg 取り込み）。**国とキャラは識別子で受け取り、値はここで組む**（ADR-005 S4h）: `SetPlayerCountry(slot, code)` は `PlayerService.BuildCountryValue`（選手カードと同じ形・キー順。カスタム国旗も code）、`SetPlayerCharacter(slot, en_name, skin)` は `CharacterValue` = `PlayerService.BuildCharacterValue`（スキンの行が範囲外なら最初のスキン。オーバーレイが描く `assets` 込み）。カタログが知らない code / en_name は書かずに False。取り込み（`ResolveImportedPlayer`）のキャラは `ImportedCharacterValue`: 従来のキャラの config のコピーをキー・値・順序ともそのまま残し、`CharacterValue` にしかないキーを後ろに足す（追加だけ。`out/top8/...` のテキストと自作レイアウトのため。コンボに無いキャラ = mods オフの modded はコピーだけ）。state の値 → 選択（en_name, スキンの行）は `CharacterPick`（codename が一致する行を優先。codename の無い値は全キャラのスキンから assets で探す。S4h より前の 3 つの形も読む）。ページは同じ規則を TS に持つ（`web/src/Top8Page/characterPick.ts`。`characterPick.cases.json` を `tests/test_top8_service.py` と jest の両方が読み、食い違いを検出する。S4i）。**反映 `ApplyScores` は渡された試合だけを更新するマージ**（渡されなかった試合は今の値を保つ。lock の中で読んで書く。スコアは 0〜`SCORE_MAX`(99) の int 2 つの list だけ。1 つでも不正なら何も書かずに None。S4h）。**取り込みの入口は `RefreshFromProvider()` の 1 つ**（ADR-005 S3e-8。ページの「大会結果を読み込む」（`POST /api/v1/bracket:refresh`）・ホットキー・legacy / Socket.IO が同じ関数を呼び、ヘッドレスでも動く）: 大会の最後の phase のダブルエリミのグループ（`TargetPhaseGroup`）→ 取得 → `core/Bracket` → `Top8Model.FromBracket` → 選手を `GameAssetCatalog` / `CountryCatalog` で解決 → `ImportBracket`。取得は `bootstrap_core()` が注入する `fetcher`（provider の `RunWithProvider`。provider のワーカースレッドで走る）。実行中フラグは取り込み全体（取得〜書き込み）で 1 つ、全入口が共有し、実行中の要求は `BUSY` で捨てる（キューしない）。選手の解決と書き込みは `StateManager.lock` の中（ゲームの切り替えとの競合で前のゲームのキャラが残らない）。空・失敗の結果は何も書かない。**ゲームが別のゲームに変わったときのキャラの消去 `ForgetCharacters()`** も持つ（キャラがある枠だけ・1 保存サイクル。呼ぶのは `GameAssetCatalog.SelectGame` だけ）。Top 8 のページ（React。S4i）は操作を依頼し、state を描画するだけ（実況パネルと同じ型）。トポロジ自体は Qt-free の `src/core/Top8Model.py` が正本 |
| `src/core/ColorService.py` | なし（モジュール関数） | 大会カラー（`tournamentInfo.themeColor`/`accentColor`）と機能別カラー（`colors.{feature}.*`）の読み・書き・起動シード。**`tournamentInfo` プレフィックスだがこの族は本サービスのもの**で、`TournamentInfoService` は触らない（歴史的なキー名） |
| `src/core/GameAssetCatalog.py` | `GameAssetCatalog.instance`（`bootstrap_core()` 生成。Qt 側は `AutonaGameAssetManager` が包む） | ゲームアセットの正本（スキャン・ロード・キャラ/スキン/ステージ/アセットパス・Start.gg ID 解決）と `game.*` の書き込み。ロード結果は不変スナップショットで差し替え、リーダーはロックを取らない。書き手のロック保持中に `run_sync` / Qt シグナル / observer を呼ばない（ADR-005 S1a）。**例外は `SelectGame` の `progress` コールバックだけ**でロック保持中に呼ばれるため、渡す側はブロックしない（queued で投げるだけ。アダプタは `AutonaStatusManager` を渡す）。observer から書き手を呼ばない（agent-pitfalls #16）。**`SelectGame` は、state の `game.codename` が空でなく新しい codename と違うとき、Top 8 と大会リザルトのキャラを空にする**（writer lock を離した後・同じ `SaveBlock` の中で `Top8Service.ForgetCharacters()` と `ResultService.ForgetCharacters()`（ADR-005 S4q）を直接呼ぶ。observer は使わない。起動時の復元（`game` が空）・同じゲームの選び直しでは呼ばない。ADR-005 S3e-8）。同じ判定はスナップショットの `replaced`（置き換えた前のゲームの codename。それ以外と `ForgetSelection` の後は None）に残り、ボードの選手のキャラと mains はロード後にシェルが走らせる `AutonaActions.ApplyGameRoster(replaced)` が空にする。値はロードが返したスナップショットのもので、ローダーの `gameLoaded`（`Signal(object)`。デスクトップはアダプタ、ヘッドレスは `HeadlessGames`。`onLoad` / `gameChanged` の直前に出る）で `bootstrap.wire_game_roster` に渡る。後から `Current()` を読まない（スロットが走る頃には、次のロードのスナップショットになりうる。ADR-005 S3e-6 レビュー）。**デスクトップのロードは `TaskExecutor` のジョブ 1 本（ローダー）**（ADR-005 S3f-4。旧 `AssetsLoaderThread`）: 要求は最新の 1 つだけを記録し（`_requestLock`）、ローダーが走っていなければ 1 本積む。ローダーは待っている最新の要求を、無くなるまで順に取る（要求ごとのジョブにしない: 立て続けの切り替えでジョブレーンを埋めない）。ローダーが保存ブロックを開いて `SelectGame` を呼び、モデルの作り直し・`gameLoaded`・`onLoad` は `run_sync` で GUI スレッドに渡した `_Publish` の中で順に走る（ロスターの書き込みも同じ保存サイクル）。ブロックを閉じるのも `_Publish` の終わりの GUI スレッドなので、通常は公開も held 通知も GUI スレッドから direct に出る（pitfalls #17 の原子性を保つ）。ただしそのとき別スレッドの SaveBlock が開いていれば、最後に閉じたスレッドが公開する。**ヘッドレス（`src/headless.HeadlessGames`）も同じ設計のジョブ 1 本**（ADR-005 S3f-6。旧: 要求ごとの daemon スレッド）で、保存ブロックを `SelectGame` の後に閉じ、ロード後の処理（選択の保存・`gameLoaded` / `gameChanged`）を `run_sync` でメインスレッドに渡して待つ（非同期の要求も。見捨てられたら `_SettleStopped`）。**後から来た要求が勝つ**（世代番号。待っている要求は新しい要求に置き換わると選ばずに終わる。同期ロード（`set_game`）は、自分か後の要求の試行が終わってから戻る。`"OK"` は成否を問わず「試行が終わった」で、GUI スレッドで同期ロードしていた頃と同じ）。ローダーが要求を取った直後に終了中（`gui_unavailable_reason()`）なら何も書かずに `done` へ進む（両シェル）。選択の後に終了が来たら、ロスター（前のゲームのキャラの消去）・ボードの反映・選択の保存をローダーのスレッドで書き、state.json を 1 つのゲームに揃える（デスクトップは `_SettleUnannounced`、ヘッドレスは `_SettleStopped`）。デスクトップでモデルの作り直しが失敗したとき（`FAILED`）も、`_Publish` がサイクルを閉じる前に GUI スレッドで同じことをする。GUI スレッドからの同期ロードは RuntimeError。要求の Future は取り消せない（`cancel()` は False。空の codename は答えの済んだ `SKIPPED`）。`done()` は試行を終わらせたスレッド（ローダーのジョブレーン・置き換えた要求のスレッド・シャットダウンのスレッドなど）で走るので、短く済ませ、別のロードを頼まない（ジョブレーンからはローダーを起こせず、同期なら自己デッドロック）。ゲームが変わったときの全量の Socket.IO 送信（`ws_game_changed`）は、ロードの公開（`StateManager.WhenPublished`）とその delta の送信（`WebServer._after_pending_deltas`）の後に出す（revision の契約: R+1 の delta が先、全量の revision は中身と一致。開いている delta の窓はこのときだけ待たずに送る）。**Web / Start.gg のリーダーはカタログを直接読む**（ADR-005 S1b）: `AutonaWebOperations.get_games` / `get_characters` / `get_variants` は `*Payload()` を返すだけで `@gui_thread_sync` を持たない。`StartGGDataProvider` は `CharacterFromStartGGId` / `StageFromStartGGId` で解決し、1 つの結果（1 セットのパース・1 エントラント）を組む間は `Current()` を 1 回取って `snapshot=` で渡す（start.gg の ID → 名前の表 `_startggCharacters` はスナップショット外だが、ゲーム非依存なので実害なし）。ゲームの切り替えは codename で `LoadGameByCodename` を呼ぶ（スキャン上の位置を引いてから位置でロードしない。間の再スキャンで別ゲームになる）。終了を待つ側は `done=`（成否を問わず 1 回）を使い、onLoad（実際にロードされたときだけ）で待たない。新しいリーダーをアダプタの旧プロパティ（`games` / `selectedGame` / `characters` ほか。GUI 側の残りは S4 で消える）に繋がない。ペイロードの入れ子のキー順は旧モデル経由の QVariantMap 順を再現している（Socket.IO のバイト互換。`DictHelper.qvariantmap_copy`（S1d で core 共用に移動。選手スロットの値も使う）。str キーの dict と list だけ変換し、tuple と非 str キーの dict は PySide6 と同じく並べ替えない） |
| `src/core/CountryCatalog.py` | `CountryCatalog.instance`（`bootstrap_core()` 生成。Qt 側は `AutonaCountryHelper` が国モデルを作る） | 国・地域（states）・カスタム国旗・都市→地域表の正本（ADR-005 S1c）。`Load()` が唯一の書き手で不変スナップショットを差し替え、リーダーはロックを取らない。state は書かない。ロードは `Window.__init__` の `AutonaCountryHelper.instance.init()`（ヘッドレスは `src/headless.py` が `Load()` を直接呼ぶ。国名がロケール依存なので `LoadLocale()` の後。ロケールは callable 注入）。都市→地域表（`FindState`）も `Load()` が 1 回だけ読む（最初の照会は選手の読込から `StateManager.lock` の中で来うるため。ADR-005 S3e-3。`Load()` していないカタログでは最初の照会で読む）。**データを返すリーダーは必ずコピー**を返し、表に書き戻さない（旧 `LoadStates` と Start.gg の次セット解決が地域 dict に `asset` を書き込み、`get_states` の出力が「デスクトップがその国を表示したか」で揺れていた）。地域の `asset`（旗パス）は `States()` / `State()` が呼ばれた時点で解決する（地域ごとにディスクを見る。旗の要らない照合 = 選手の読込の地域コード合わせは、`StateManager.lock` の中なので `StateCodes()`（code / original_code だけ）を使う。ADR-005 S3e-3）。`AutonaWebOperations.get_states` は `States()` を返すだけで `@gui_thread_sync` を持たない。Start.gg は `CountryCodeFromEnName` / `FindState` / `State` / `BasicCountryInfo` で解決する。選手の国を code から引くのは `CountryByCode`（標準国 → カスタム国旗の順。国モデル走査と同じ答え。`PlayerService.CountryValue` が使う） |
| `src/core/SponsorHelper.py` | なし（静的メソッド） | `...player.{M}.sponsor_logo` / `sponsor_logos.{I}` の解決と書き込み。`PlayerService.PublishImages` から呼ばれる |
| `src/core/TournamentMediaService.py` | `TournamentMediaService.instance`（`bootstrap_core()` 生成。今の provider の引き方 `currentProvider` は注入） | 大会アイコン / バナーのすべて（ADR-005 S4f）: ファイルからの取り込み `ImportMedia(kind, bytes)`（アイコンは長辺 512、バナーは長辺 1920 の PNG。検証・縮小・tmp → `os.replace` は配信団体素材と共通の `core/ImageImport.py`）・大会からの取得 `FetchMedia()`（`TaskExecutor` のジョブ 1 本、状態は `tournamentInfo.media.{status,error,revision}`。大会は URL（`provider.url`）で見分ける。実行中の要求は、同じ大会なら `BUSY`、別の大会なら `QUEUED`（走っている取得が終わったら同じジョブがその時点の大会で取り直す。大会の設定の `fetchMedia` が捨てられないように。S4f レビュー M1）。答えを待つ間に大会が変わったら書かずに `superseded`。照会の失敗（provider の `strict=True` の lookup が例外）は `fetch_failed` で、「メディアが無い」`no_media` と区別する）・既定に戻す `ResetMedia()`。`logoVersion` / `bannerVersion` を publish する。大会情報ページ（React の `EventPage`。ADR-005 S4g）は `/api/v1/tournament/media*` で依頼して state を描画するだけ |
| `src/core/TournamentService.py` | `TournamentService.instance`（`bootstrap_core()` 生成。provider のアダプタと、メインスレッドで走らせる `onMain`（GUI executor）は注入） | 大会の設定・解除（`TOURNAMENT_URL`）の唯一の経路（ADR-005 S4f）: `SetTournament(url, force, fetchMedia)`（event URL はメインスレッドで保存 → アダプタの `LoadTournament` → `AnnounceUrl` → 求められたら `FetchMedia`。admin / brackets の URL は `TaskExecutor` のジョブで event URL に解決してから（解決は QObject を作らない関数 `StartGGDataProvider.ResolveEventUrl`）。後から来た設定・解除が先に勝つ世代番号つき。それ以外の URL は `INVALID_URL` で何もしない）・`ClearTournament()`（URL を空 → **`GameAssetCatalog.ForgetSelection`** → `UnloadTournament`（その `tournament_changed` でゲームのコンボ・サイドバーが選択を読み直すので、忘れた後に出す）→ `AnnounceUrl("")`）・`Restore()`（起動時の読み直し）・`TournamentUrl()`（読み手）・`Load()`（最新の設定 / 解除の進み具合 `{status, error, revision}`。`idle` / `resolving` / `loading` / `loaded` / `error`（`unresolved` / `load_failed`。読み込みの例外も `load_failed`）。**state の `tournamentInfo.load` と、URL の写し `tournamentInfo.url` に publish する**（ADR-005 S4g レビュー M1。設定 / 解除は要求したスレッドで `StateManager.lock` → 自分の lock の順に取って次の revision を publish してから戻る。`GET /api/v1/tournament` と大会情報ページが読む）。読み込みの結果はシェルが `PayloadApplied` で伝える（`bootstrap.wire_tournament_data`）。今の provider でない取得の結果（遅れて届いた前の大会の結果）は provider のアダプタ（`AutonaTournamentDataProvider._onTournamentDataResult`）が捨てる（S4g レビュー M2）。ADR-005 S4g）。入口（サイドバーの URL ダイアログと解除の確認 `ConfirmClearTournament`・`AutonaWebOperations.load_tournament` / `clear_tournament`＝旧ルート / Socket.IO / `/api/v1` の `POST` / `DELETE /tournament`。大会情報ページはこの v1 を呼ぶ）はどれもここを呼ぶ。`tournament_url_update` は通知だけ（命令として emit しない） |
| `src/core/AnalysisService.py` | `AnalysisService.instance`（`bootstrap_core()` 生成。取得の `fetcher`（provider の `RunWithProvider`）・メインスレッドで走らせる `later`（GUI executor の `later`）・ラウンド名 `roundNames`（`AutonaLocaleHelper.matchNames`）は注入） | この大会の試合一覧のすべて（ADR-005 S4j。それまでは STATS パネルのサブタブ `AutonaAnalysisWidget`（S4k で削除）のメモリ）: `Refresh()`（`STARTED` / 実行中なら `BUSY`（キューしない）/ `NO_TOURNAMENT`）が、ジョブの中で毎回フェーズを取り直し（`GetTournamentPhases`）、id のある全 phase group（同じ id は 1 つ）を共有キューから**最大 2 本**（`PARALLEL_FETCHES`。S3f-2 判断 7。捨てた取得のジョブがまだ走っている間はその数だけ減らし、最低 1 本。試合一覧がジョブレーンを 2 本より多く使わない）で取る。provider には **strict で頼む**（`strict=True`: 要求の失敗を空の答えにせず例外にする。`TournamentMediaService` の lookup と同じ形）ので、`fetch_failed`（フェーズの要求が失敗・どの group の要求も失敗）と `no_phases`（phase group が無い）・sets の無い group を区別できる。2 本目はジョブから積めない（TaskExecutor の入れ子の禁止）ので、1 本目が `later` でメインスレッドに頼んで始める。どのジョブも別のジョブを待たない。最後の答えを記録したジョブが `core/Bracket` で行を組む（UpSet = 想定敗退ラウンドの差・Losers Run・SeedOver・複数 group のときのラウンドの前置き）。進み具合は state の `analysis`（`{status, error, done, total, failed, revision}`。取得のたびに次の revision を `Refresh` が戻る前に publish）に出し、**行は state に入れずメモリに持つ**（取得した大会の URL と組。`Result()` / `Rows()`。行を写さずにどの行かだけを返すのが `ResultRevision()`。error で終わった取得は前の行を残す）。行は構造（`round` / `roundOrder` / `p1`・`p2` の `{seed, name}` / `score`（未完了は None）/ `upset` / `losersRun` / `seedOver`）で、文言は表示側。`Forget()` は provider のアダプタの `LoadTournament` / `UnloadTournament` が **provider を差し替えた直後に**直接呼ぶ（observer は使わない。差し替えの前に呼ぶと、その間に別スレッドから来た `Refresh` が旧 provider で取得し、旧大会の行が新大会の結果になりうる）: 世代を進め、取得の残りを止め（遅れた答えは記録しない）、行を捨てて idle を次の revision で publish する。**ロック順は `StateManager.lock` → サービスの lock**。サービスの lock は自分のフィールドだけを守り、state の読み書き・fetcher・`later` はそれを離して呼ぶ。外からは v1 の `POST /api/v1/analysis:refresh`（`Refresh()`。202 / `BUSY` は 409 `CONFLICT` / `NO_TOURNAMENT` は 409 `NOT_READY`）と `GET /api/v1/analysis`（`{status, url, rows}`。`Result()` の URL が今の provider の URL と一致するときだけ行を返す）で使う（`AutonaWebOperations.refresh_analysis` / `get_analysis`。どちらも呼び出し元のスレッドで core を呼ぶ）。統計情報ページ（React の `StatsPage`。ADR-005 S4k）の「この大会の試合一覧」タブは、ボタンでこれを呼び、`analysis` の 1 行を描き、`analysis.revision` / `status` が変わったときだけ行を読み直すだけ |
| `src/core/SetSelectionService.py` | `SetSelectionService.instance`（`bootstrap_core()` 生成。取得の `fetcher`（provider の `RunWithProvider`）は注入） | セット選択の一覧のすべて（ADR-005 S4v。Qt のセット選択・配信台ウィンドウが出すものを、ウィンドウの無いシェルと SCORE ページのために）: `RefreshPhases()` / `RefreshStations()` / `RefreshSets(phase, finished)` / `CancelSets()`。どのセットを読むか・どの配信台を追従するかは `SetFollowService` の仕事で、ここは取って持つだけ。**窓は今までどおり provider のアダプタを通る**（寄せていない: アダプタのフェーズ取得はプール候補も使う。両入口が start.gg に同じ引数で頼み、同じ表を作ることは `tests/test_set_selection.py` が比較で pin する）。**リストごとに進み具合を state の `set_selection.{phases,sets,stations}`**（`{status, error, done, total, failed, revision}`。取得・止める・大会の変更のたびに次の revision を、呼び出しが戻る前に publish）、**行はメモリ**（大会の URL と組。`Result(kind)`。読むのは `AutonaWebOperations._get_selection_list` で、`StateManager.lock` の 1 回の保持の中で状態・行・今の URL を読み、URL が一致するときだけ行を渡す）。**失敗と空を区別する**（provider に `strict=True` で頼む。`GetMatches` は 1 ページ目がリトライ後も答えなければ例外で、`failed_pages(n)` が欠けたページ数を言う。`GetStations` は event が答えなければ例外）: `error` / `fetch_failed` は前の行を残し、`done` で行が空なら「何も無い」。取れなかったページと読めなかったセットは `failed`。**実行中の要求**: `phases` / `stations` は `BUSY`。`sets` は同じ `{phase, finished}` は `BUSY`、別の要求は走っている取得を止めて置き換える（窓のコンボと同じ。止めた取得の答えは捨てる）。止めた取得（置き換え・`CancelSets`・`Forget` のどれでも）の要求がまだ答えず、そのリストのジョブが `MAX_SET_JOBS`（2）本残っている間は、どの要求も `BUSY`（ジョブレーンは 4 本で全ての大会の取得が共有）。ジョブから別のジョブを待たない（1 取得 1 ジョブ）。**遅れた答えは捨てる**: 取得は 1 回ごとのオブジェクトで、`_fetches[kind]` が自分でなくなった（置き換え・`CancelSets`・`Forget`）答えは何もしない。世代番号は持たない。`Forget()` は provider のアダプタの `LoadTournament` / `UnloadTournament` が **provider を差し替えた直後に**直接呼ぶ（`AnalysisService` と同じ。observer は使わない）: 全リストを次の revision の idle へ、`phases` / `stations` の行は捨て、**`sets` の行は URL と組で残す**（窓の、全ボード共有・プロセス寿命のキャッシュと同じ。同じ大会を読み直すと戻る。枠は 1 つ）。**フェーズの解決**: フェーズの行があれば `None` は `PHASE_REQUIRED`、知らない id は `UNKNOWN_PHASE`、`"all"` は全グループ。無ければ大会全体で、id は `UNKNOWN_PHASE`。`query` は正規化した値（`"11"` は行の id の `11`、フェーズが無いときの `None` は `"all"`）で、同じ要求は 1 つ。解決と取得を current にするのは `_Start` の 1 回の lock の中（`plan`）: 間に `Forget` が入ると、次の大会に前の大会のグループを頼んでしまう。**行はホワイトリスト**（`PhaseRows` / `SetRow` / `StationRows`。タグとエントラント名だけなので READ で返せる。キャラ・実名を含まないのでゲームの変更で捨てない）。Qt の文言（ダブルスの「 (A, B)」・「完了したペアのみ」）は読む側の仕事で、規則は `external-control-api.md` §5.18。**ロック順は `StateManager.lock` → サービスの lock**。サービスの lock は自分のフィールドだけを守り、state の読み書き・fetcher はそれを離して呼ぶ。外からは v1 の `GET /tournament/{phases,sets,stations}` と `POST /tournament/{phases,stations,sets}:refresh`・`POST /tournament/sets:cancel` で使う（202 / `BUSY` は 409 `CONFLICT` / `NO_TOURNAMENT` は 409 `NOT_READY`）。`SeedPanelState` が起動のたびに idle に戻す（revision は続く） |
| `src/core/MatchNameCandidates.py` | `MatchNameCandidates.instance`（`bootstrap_core()` 生成。用語の候補 `generic`（`AutonaLocaleHelper.Get{Phase,Pool,Match}NameCandidates`）とラウンド名 `matchNames` は注入） | match バーの 3 つの編集可能コンボ（phase / pool / match）が出す候補（ADR-005 S4x。それまではボード 1 / 2 のウィジェットが provider の通知で足していた）: `Candidates(board)` が `{phase, pool, match}` を返す。ボード 1 / 2 は読み込んだ大会の名前（複数グループのフェーズのプール名・参加人数の `Winners / Losers TOP{n}`）を用語の前に足す（用語にある名前は用語の位置のまま）。ボード 3 と `None` は用語だけ。大会の名前は provider の `tournament_data_updated` / `tournament_phases_updated` を `bootstrap_core()` が `FollowTournamentData` / `FollowPhases` に繋いで持つ（読み手より先。state には入れない）。自分の lock は自分のフィールドだけを守る。ボードのコンボ（1 / 2 / 3）と v1 の `GET /match-names/candidates?board=` と Web の `MatchInfoBar` が同じリストを描く |
| `src/core/StreamService.py` | `StreamService.instance`（`bootstrap_core()` が生成して `Start()`。バナー URL の照会・`later`（GUI executor の `later`）・出力の剪定は注入） | 配信設定のすべて（ADR-005 S4d）: 配信タイトルの自動生成（大会名・ゲーム略称は **state の observer** で見る＝下の例外。上位の参加者は provider が `SetTopEntrants` で渡す。生成したタイトルは毎回 `stream.titleAuto` に publish し、`stream.title` は `TitleIsAuto`（前回の自動値か空）のときだけ上書きする。クライアントの欄の打ちかけを送るかは `TypedTitleWins`。パネルが無くても・ヘッドレスでも動く）・チャンネル CRUD（`stream_channels`）・団体素材の取り込みとリセット（PIL で縮小・`stream.org*Version`）・配信サムネ生成（`TaskExecutor` のジョブ 1 本、実行中の要求は `BUSY`、結果は `stream.thumbnail`）・生成オプション（`stream_thumbnail_options`）。配信設定ページ（React の `StreamPage`、`/api/v1/stream*`。ADR-005 S4e で Qt 版を削除）は操作を依頼し、state を描画するだけ |
| `src/core/ResultService.py` | `ResultService.instance`（`bootstrap_core()` が `ThumbnailService` の後に生成。`later`・描画の開始 `render`・翻訳・ロケール・出力の剪定・`shuttingDown`・取得の `fetcher`（provider の `RunWithProvider`）・大会情報の取り直し `refreshTournament`（`RefreshTournamentData`）・今の provider の引き方 `currentProvider` は注入） | 大会リザルトのすべて（ADR-005 S4q）: 行の読み書き（単独 16 行の `result_players`・団体 8 行の `result_teams`。`state.json` に残る。**読みは `_Rows`**（保存された別のゲームのキャラを無いものとして読む。state は消さない。判定は絵のパスの `GAMES_DIR/<codename>/`）、**書きの元は `_StoredRows`**）。`UpdatePlayer` / `UpdateTeam` は 1 行 1 保存サイクルで、キャラは識別子（en_name + スキンの行 + 選んだゲーム）で受けて 1 つのスナップショットで全部を解決してから書く（`Refusal` のときは何も書かない）。`characters` / `members` はリストなら全置換、dict なら名指した位置（`"1"` から）・キーだけを変える（ページはこちら。別のゲームのキャラを読めないまま書き戻さない）。`Reorder` / `Clear` / `ImportStandings` は 1 保存サイクル、`ForgetCharacters()` は `SelectGame` が直接呼ぶ。ペイロード `BuildPayload()`（`tests/fixtures/result/golden.json` と一致。差分は `tests/test_result_service.py` の `DIFFERENCES`）: キャラ欄が空の選手はスコアボードの同名の選手のキャラで描く（描画のときに解決し、state に書かない）・名前が無ければプレビューだけ固定のサンプル・背景はサムネイルの読み（`ThumbnailService.EffectiveBackgroundPath`。バナーは同じパスで置き換わるので `?v=<bannerVersion>` を付ける）。レイアウトは `settings.json` の `result_config.layout`（`SetLayout` / `FollowBattleFormat`。後者は `AutonaActions.SetBattleFormat` が呼ぶ（ADR-005 S4x。それまでは `Window.SetBattleFormat`）。今のレイアウトの写しを state の `result_layout` に出す: 対戦形式の切り替えはページの操作ではなく、保存が無ければ設定にも書かれないので、ページはここで知る。S4r）。描画は `core/RenderLane.py`（結果は `result_render`。データの無い `Generate` は `NO_DATA`。`Generate` の受理時に `SaveToDb()`。プレビューは DB を書かない）。standings の取り込み `LoadStandings(confirmed)`（実行中の判定 → 確認の判定の順。**`refreshTournament(done)` が大会情報の取得とゲームの読み込みを連鎖で済ませてから** provider のワーカーのジョブで取る。待たない: job レーンからの submit は `NestedSubmitError`。入力があれば確認を求める。結果は `result_standings`。provider の `GetStandings` は失敗を例外にするので、失敗と空を区別できる。取得の間にゲームか provider が変わったら書かずに `changed`。行に入れられなければ `import_failed`）。投稿文 `PostText()` は順位の順で、DB の X ハンドルを含むので `GET /result/post`（`PLAYERDB`）だけが返す。**ロック順は `StateManager.lock` → レーンの lock → 取り込みの lock。`PlayerDB` はロックを持たずに呼ぶ**。ページは React の `ResultPage`（`/web/result`。S4r）で、操作を依頼して state を描画するだけ。旧 Qt パネルが `result_players` に残した団体の行は `SeedPanelState` が起動のたびに消す |
| `src/core/RenderLane.py` | なし（`ThumbnailService` / `ResultService` の基底クラス。ADR-005 S4q で `ThumbnailService` から切り出した） | オフスクリーン描画の予約と結果: `Preview()` / `Generate()` / `PreviewImage()`。規則は下の `ThumbnailService` の「描画のレーン」のとおり。サブクラスが `_ReadRender` / `_RunPayload` / `_OutputStem` と出力先の定数を与える |
| `src/core/ThumbnailService.py` | `ThumbnailService.instance`（`bootstrap_core()` が生成。`later`（GUI executor の `later`）・描画の開始 `render`（`AutonaWebOperations.start_image_render`）・翻訳・ロケールの名前・出力の剪定・終了中か `shuttingDown`（`gui_unavailable_reason`）は注入） | 試合サムネイルのすべて（ADR-005 S4o）: `thumbnail_config` の副作用のない読み `Settings()`（既定値を書かない。残骸の `game.None` は読み飛ばして消さない）・全部を検証してから 1 回だけ書く `Update()`（未知のキー・パス・範囲外・一覧に無いゲームは全拒否。`background_path` / `main_icon_path` は受けない。v1 の描画には `Settings()` と同じ読みを通す: `GET` の値 = 描いた絵）・`Reset()`・動画タイトル `VideoTitle()`・ペイロード `BuildPayload()`（生の出力。`tests/fixtures/thumbnail/golden.json` と一致）と、描画に渡す `RenderPayload()`（背景の移行（描くコピーにだけ当て、`settings.json` は書かない）+ `BuildPayload` + 読み済みの設定。v1 の run と、同期で描く `DrawBoard`（SCORE の X 投稿・legacy の get-thumbnail。ADR-005 S4y）が同じ `RenderPayload` を描く。ADR-005 S4p）・カスタム背景の取り込み `ImportBackground()`（`user_data/thumbnail_background_<内容のハッシュ>.*`。テンプレートにキャッシュバスターが無いので同じパスで置き換えない）・ファイル名と 30 世代。**描画のレーン**（ADR-005 S4q から本体は `core/RenderLane.py`）: `Preview()` / `Generate()`。1 度に 1 つで、実行中の preview は最後の 1 件にまとめ、generate は 1 件だけ予約でき（予約の preview を兼ねる。兼ねた generate が試合なしで終わったら preview を続けて描く）、それ以上は `BUSY`。ペイロードは呼び出し元のスレッド、描画は GUI スレッド（`render` で開始するだけで待たない。イベントループを入れ子にしない）、符号化とファイルは `TaskExecutor` のジョブ。結果は `thumbnail.render = {status, path, error, revision}`（`error` はコードだけ。次の run は `later` で GUI スレッドから。run の終わりは 1 回だけ受ける）。ロック順は `StateManager.lock` → レーンの lock。レンダラーの土台 `src/AutonaOffscreenRenderer.py`（ADR-005 S4q で `src/thumbnail/ThumbnailRenderer.py` から切り出した。`src/result/ResultPageRenderer.py` も同じサブクラス）は描画を 1 度に 1 つにする（チケット）ので、SCORE の同期経路（`renderSync`）と同時に走っても互いの絵を取らない。読み込みの完了は `loadingChanged` の URL の番号（`?load=N`）で照合し、古い読み込みの完了を取らない。絵は `RENDER_READY` では撮らず、レンダラーのスクリプトが描画の番号付きで出す `RENDER_PAINTED`（フォント・画像の decode・rAF 2 回の後。上限は Qt 側で 1 秒。同じ文書で直前の描画がデコードした画像は頼み直さない）を待って撮る。内訳は描画ごとに `Thumbnail render complete:` の INFO 行に出る。読み込んだばかりのページの最初の 1 枚は、読み込み前のビューと同じ画素の間は撮り直す（上限 1 秒。S4o 判断 22）。プレビューの PNG だけ圧縮を下げて速くエンコードする（`encode("PNG", fast=True)` → zlib レベル 1。可逆で `image/png` のまま）。`out/thumbnails/` と legacy は既定のエンコードのまま（判断 23） |

**選手リストの行と記録は `src/core/PlayerDirectory.py`**（なし。モジュール関数。ADR-005 S4m）: `Registered()` / `Entrants()` が選手 DB を `{game, players}` / `{game, entrants}` にする（行の mains のゲームの codename を同じスナップショットから添える。今のゲームの mains を防御的に読み、文字列の mains・壊れた要素・タグの無い行で落ちない。`country_code` / `state_code` は `Save` が受け取る値に読み替える）。`Save(fields)` は `PrepareSaveToDB` と同じ記録を組み（state は書かない。`tests/test_player_directory.py` が golden で照合）、`SaveRecordToDB` で保存する（順序の番号は `PlayerService.NextSaveSequence()` で共有）。**編集していない値を壊さない**: `mains` と `country_code` / `state_code` はキーが無ければ DB の値のまま（カタログに照らさない）、`mains` は `game` が今の codename と一致するときだけ受け、選手が今のゲームで既に持っている要素（同じキャラ・スキン・バリアント）は検証しない。文字列の mains の他のゲーム分は `SaveRecordToDB`（`PlayerDB.KeepStoredMains`）が守るので、カードの `savePlayerToDb` も同じ。`Delete(key)` は存在検査つき。読むのは `PlayerDB.lock` の中（生の dict なので）、保存はロックなしで呼ぶ。**番号（`_saveLock`）は `PlayerDB.lock` を取る前に取る**（順序は `_saveLock` → `PlayerDB.lock`）。選手の値をログに出さない（ログの sink は全部 `diagnose=False`。loguru の既定は traceback にローカル変数の値を書く。`tests/test_api_v1_players.py` が pin）。

**状態を持たないサービスはモジュール関数でよい**（`Top8Service` / `ColorService` / `CommentaryService` / `PlayerDirectory`）。
`bootstrap_core()` が作るのは、ボードごとに要る／台帳を持つもの（`SetLoadService` /
`TournamentInfoService` / `PlayerService` / `CrewBattleService` / `AutonaActions` / `StreamService` /
`TournamentMediaService` / `TournamentService` / `AnalysisService` / `SetSelectionService` / `ThumbnailService` / `ResultService` /
`MatchNameCandidates`）だけ。

- **`SetLoadService` はステージ行を書かない**。行数を決めるのは `GameTrackerService.SetStageCount()`
  だけで（呼ぶのは `AutonaActions.SetBestOf`。セットの bestOf は `BestOfFromSetData` で読んで
  `SetBestOf` に渡す）、読み込んだスコアは `team.{t}.score` を書き、`SetFollowService.ApplySetPayload` が
  同じ block の中で `FollowScores` に渡す（+1 と同じ経路。追従は `SetBestOf` の後で、読込の前のスコアからの差。
  ADR-005 S4t）。`stages` を直接足す経路を作らない
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
  - **ただし v1 の編集 `PlayerService.Edit` / `EditCharacter`（ADR-005 S4z）は書く前に比べる**
    （例外の例外）。ウィジェットを通らない要求なので「すでに変化した」前提が無く、同じ値の
    要求でデスクトップの 反映 を点けないため: 文字の欄・国・地域は今の値と違うものだけを書き、
    キャラ行は組み直した行が今の行と同じなら publish しない（カードの入力欄・コンボが無変化なら
    emit しないのと同じ結果）

- **state が記録、ウィジェットは表示**。ステージ結果は `score.{n}.stages.{i}` が正本で、
  ボタンはそれを描画するだけ（`_SyncStageButtons`）。`setChecked()` は `toggled` しか
  出さず `clicked` は出ないので、この再描画はエコーループにならない
- Qt 依存が避けられないもの（統計の取得 = `StatsService.fetcher`、ローカライズ文字列など）は**呼び出し可能オブジェクトで
  注入**する（選手数は注入しない: state のスロット数。ADR-005 S3e-5）。core 側が Qt を import しないことが層の定義（`tests/test_core_qt_free.py`）
- **サービスは observer を購読しない**。購読すると所有ウィジェットと同じゴースト問題を
  core 側に持ち込むことになる。サービスは「呼ばれる」だけの存在にする
  - **狭い例外**（ADR-005 S4d）: 次をすべて満たす core サービスに限り、`state_updated` / `state_big_change` を購読してよい。
    (1) プロセスと同じ寿命のシングルトン（`bootstrap_core()` が作る）、(2) `Stop()` で購読をやめられる（テストが自分のインスタンスに
    差し替える間に止める）、(3) observer の中では state を読んで、注入された `later`（イベントループに戻ってから走る）で
    処理を予約するだけ。書かない・待たない（observer は `StateManager.lock` の中で走る。pitfalls #16 #17）。
    **実例は `StreamService` だけ**（配信タイトルの入力 = 大会名・ゲームを見る）。購読者の集合は
    `tests/test_bootstrap_split.py` の `STATE_SUBSCRIBERS` が pin しているので、増やすならここで判断してから足す

## 状態変更の通知（observer + Bridge）

通知の正本は Qt 非依存の observer チャンネル（`src/core/Observable.py` の
`ObserverChannel`）で、`StateManager.observers.<名前>` に 4 本ある。core 側は
`notify()` で発行する（8 箇所）。

| チャンネル | 引数 | 意味 |
|-----------|------|------|
| `state_big_change` | なし | 全面リロード。全ウィジェットのフル再描画をトリガーするため頻繁に発行しない |
| `state_updated` | payload dict（`revision` / `delta_index` / `delta`） | 差分のみ。WebSocket 経由で Web UI に配信される |
| `held_dirty_changed` | (prefix, bool) | held サブツリーに未反映の変更が出た(True)／反映された(False)。**通知は合図で、購読者は payload を信じず現在値（`HasPendingChanges`）を読む**（`Set` の True は lock の外で出るので、state の変化と順序が前後しうる）。False は `SaveState` が流し切ったところで、まだ未反映が無いときに出る（`CommitHeld` の中では出さない。保存ブロックの中の commit は積むだけで、キーはブロックが閉じるまで未反映のまま。ADR-005 S4u）。だから `CommitHeld` の False も lock の中で出る（pitfalls #17）。公開の失敗（`SaveState` の例外）の後は、次の `SaveState` まで False が出ない（もう一度の反映で再試行される） |
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

`state_updated` の `delta` の行（Socket.IO の `program_state_update`）:

- **クライアントは行を 1 つずつ適用する**（`web/src/stateDelta.js`）。Web サーバーが複数 revision の行を区切りなしで 1 メッセージに結合するので、まとめて並べ替えられない。それを成り立たせるのが `SaveState` の 2 つのオプションで、**どちらも外さない**: `DeepDiff(zip_ordered_iterables=True)`（リストは位置ごとに比べる。行は各位置の値と末尾の増減だけ。外すと値のリストが途中への挿入や、リスト全体についての `iterable_items_*` で届く）、`Delta(always_include_values=True)`（型の変換で作れる変更でも値を省かせない。省くと `""→{}` などで `unknown___` が値として届く）
- `iterable_item_removed` は「その位置の前でリストを終える」、`iterable_item_added` は位置への代入（新しいキーの 1 要素のリストはこの行で届き、親はリストとして作る）。`web/src/stateDelta.cases.json` が、実物の行（`tests/test_state_delta_cases.py`）と適用の結果（`web/src/stateDelta.test.js`）の両方を pin する。deepdiff を上げて行が変わったらここが落ちる
- `None` との間の変更は行に出ない（`exclude_types`）。state のリストや、開いているページに届けたい値に `None` を使わない
- OBS オーバーレイは `layout/include/globals.js` に別実装を持ち、`iterable_item_removed` を処理していない（既知。ADR-005 S4r の再レビューの節）

## DictHelper（`src/core/DictHelper.py`）

- `deep_get(d, keys)` / `deep_set(d, keys, value)` でネスト操作
- キーはドット区切り文字列: `"score.1.team.1"`
- StateManager 内部で使用。外部から直接呼ぶ場面は少ない

## Web サーバー（Flask + SocketIO）

- `AutonaWebServer.WebServer` は素の Python クラスで、`start()` が `threading.Thread`（daemon）で werkzeug の threaded サーバー（`make_server(threaded=True).serve_forever()`。旧 `socketio.run()` が threading モードで組むものと同じ）を回す（ADR-005 S3c。旧: QThread）。`stop(timeout)` が `shutdown()` で止める（旧 `QThread.terminate()` の強制終了は廃止）。起動失敗（bind 不能など）はコンストラクタの `on_start_failed(error: str)` コールバックにサーバースレッドから通知する（デスクトップは `gui_executor` で GUI スレッドへ渡してダイアログ表示）。ポート衝突の事前確認 `check_webserver_port()` は QMessageBox を出すのでデスクトップ専用
- **Socket.IO の delta（`program_state_update`）は 1 本の flusher スレッド（`AutonaWebServerDeltas`・daemon）が送る**（ADR-005 S3f-5。旧: バッチごとの `threading.Timer`）。`start()` がサーバースレッドより先に起こし、`stop()` がサーバーの停止の後に、開いているバッチをすぐ送らせて join する（残り時間、ただし最低 `DELTA_STOP_MIN_SEC`）
  - delta を受け付けるのは `start()` から `stop()` の間だけ。前（受け手がいない）と後（送るスレッドが無い）の delta は捨てる
  - `on_program_state_update` は `StateManager.lock` の中で走りうるので、キューに積んで notify するだけにする。送出は flusher が lock の外で行う。ここに待ちを足さない（agent-pitfalls #17）
  - `stop()` を `StateManager.lock` を持ったまま呼ばない（flusher の全量送信へのフォールバックが state を読むので、join が期限まで待つ）
  - 間隔（先頭 0・50 / 100 / 200 ms）と payload は外部契約。`tests/test_webserver_delta_debounce.py` が偽の時計（`WebServer._delta_clock`）で pin している
  - 例外は 1 つ: バッチの送信を待つもの（`_after_pending_deltas`。ゲームの切り替えの全量送信）があると、開いているバッチはすぐ送られる（窓を待たない。ADR-005 S3f-4）。`start()` のやり直しでも、待っていたものは捨てずに新しい flusher がすぐ走らせる
- **未反映の値の通知 `scoreboard_pending`（`{board, dirty}`。値は運ばない）も同じ flusher が送る**（ADR-005 S4u）。`__init__` が Bridge の `held_state_updated` / `held_dirty_changed` を `on_held_change` に繋ぐ。これも `StateManager.lock` の中で走るので、`_delta_cond` の中で積むだけ。`dirty` は送信時に（`_delta_cond` の外で、`StateManager.lock` を取らずに`PeekPendingChanges` で）読む。flusher は全クライアントの delta を送るので保存サイクルの後ろで止まらない。値を運ばない合図で、変われば次の通知が必ず来るので lock なしで足りる。読み手は「最後の通知より後に始めた `GET /scoreboards/{n}/pending`」だけを信じればよい。1 窓に 1 ボード 1 通、同じ送信の delta の後。間隔は通知自身のもの（`_held_notice_ts`）で、delta の `_delta_last_flush_ts` は動かさない。ただし窓は共有で、通知の窓が開いている間に来た delta はその窓に合流する: 先頭で出られたものは最大 50 ms 遅れ、自分の debounce を待つはずだったものは早まる。送信先は `_ws_trusted_rooms()`（`GET .../pending` と同じ権限）
- **追従の通知 `scoreboard_follow`（`{board}`。値は運ばない）も同じ flusher が送る**（ADR-005 S4v）。`SetFollowService.changes` を Bridge が `follow_changed` に変換し、`__init__` がそれを `on_follow_change` に繋ぐ（`scoreboard_pending` と同じ届き方。Qt 側がチャンネルを直接購読しないのは `tests/test_signal_lifetime.py` の規則）。ボードの操作の中で出るので `_delta_cond` の中で積むだけ。開いている窓があれば合流し、無ければすぐ出る（自分の間隔は持たない: 操作者の操作か読み込みが 1 つずつ起こすもので、1 回の送信に 1 ボード 1 通にまとまる）。同じ送信の delta と `scoreboard_pending` の後。送信先は全接続（`GET .../follow` が READ で、通知はボードの番号だけ）。読み手は通知の後に始めた `GET .../follow` を使う
- **対戦形式の通知 `battle_format`（`{}`。値は運ばない）も同じ flusher が送る**（ADR-005 S4z）。`AutonaActions.battleFormatChanges` を Bridge が `battle_format_changed` に変換し、`__init__` がそれを `on_battle_format_change` に繋ぐ（`scoreboard_follow` と同じ届き方・同じ規則: `_delta_cond` の中で積むだけ・自分の間隔は持たない・1 回の送信の間に何度切り替わっても 1 通）。同じ送信の delta・`scoreboard_pending`・`scoreboard_follow` の後。送信先は全接続（`GET /battle-format` が READ）。いまの形式への切り替えは `SetBattleFormat` が知らせないので出ない。読み手は通知の後に始めた `GET /battle-format` を使う
- Flask アプリと SocketIO インスタンスは static 変数
- ルートハンドラの行き先は **`src/AutonaWebOperations.py`（モジュール関数）の 1 つだけ**（ADR-005 S3c。S3e-9 で
  ボード依存の `WebServerActions` を削除して 1 本になった）。デスクトップとヘッドレスが同じ関数を通り、違いは
  シェルが `AutonaWebOperations` に登録するものだけ（`WebServer` 自身はシェルを知らない。コンストラクタは
  `on_start_failed` だけを取る）:
  - `boardHost`（ボードホスト。上の AutonaActions 節）
  - `loadGameByCodename`（ゲームのロード。デスクトップはアセットアダプタ、ヘッドレスは `src/headless.py` の
    カタログ直のローダー）
  - **シェル操作** `openSetSelection` / `renderThumbnail` / `startImageRender`（セット選択ウィンドウを開く `open_sets`・
    サムネイル `get_thumbnail`・オフスクリーンの画像の描画の開始。デスクトップのウィンドウだけが答える操作）。デスクトップは
    `bootstrap_gui()` がシェルのアダプタ `AutonaShellOperations`（`src/AutonaShellOperations.py`。ADR-005 S4y で
    スコアボード dock から移した。セット選択 / 配信台のウィンドウもこれが持つ）の `OpenSetSelection` / `RenderThumbnail` /
    `StartImageRender`（GUI スレッドで同期実行。`StartImageRender` は開始までで、描画の完了は待たない）を登録し、
    ヘッドレスは何も登録しない。`RenderThumbnail` はボードを構築しない（`ThumbnailService.DrawBoard` に同期の
    描き手 `DrawThumbnailNow` を渡す。X 投稿のクリップボードの画像も同じ入口）。`OpenSetSelection` は、SCORE が
    React のページのとき（ADR-005 S4ab-2）はホットキーと同じくページのダイアログを開き、`Prepare` を呼ばない（ダイアログからの
    読み込みは v1 の `:load-set` で、そのボードホストが準備する）。`n` が形式のボードと違う（1v1 での `/scoreboard2-open-set`）ときは
    何も開かずログだけ。答えはどちらも `"OK"`（ページへの経路の例外もログだけで `"OK"`）。退路の Qt モードでは Qt のウィンドウで、
    窓から読み込むセットが後のシードで消えないよう、ボードホストの `Prepare` で未構築のボードを先に作る。未登録なら
    `NotAvailable` → **503 `UNAVAILABLE`（`not available in headless`）**。legacy は errorhandler、Socket.IO は ack
    （`_ws_unavailable`）が同じ形で返し、v1 は `POST /thumbnail:preview` / `:generate`（ADR-005 S4o）と `POST /result:preview` / `:generate`（S4q）がこれを返す。
    ヘッドレスで 503 を返すのはこれらだけで、ほかのルート・イベントはすべて答える（`tests/test_web_operations.py` の
    `test_every_route_and_event_answers_headless_but_the_shell_operations` がルート台帳を歩いて pin）。
    **シェル操作を増やさない**: 新しい操作は core に置き、どちらのシェルでも動くようにする（`startImageRender` は S4o で
    足した例外で、Result も同じ 1 本を使う（`kind` が `"result"`。S4q）。理由は `docs/changes/architecture-modernization.md` の S4o 節）
- Web UI からの POST リクエスト → Web 操作 → core（StateManager 更新）→ WebSocket 配信の流れ
- 新しいエンドポイントの操作は core に置き、ルートからは `AutonaWebOperations` 経由で呼ぶ
  - 例外: **読み取り専用のカタログ系エンドポイントは Actions を経由しない**
    （ADR-001 2026-10-22 Addendum）。`GET /api/v1/layouts` はルートから Qt 非依存の
    `src/core/LayoutCatalog.py` を直接呼ぶ。state を読み書きせず、
    ページ（Board / Commentary / Top 8）が読むのと同じ関数を呼ぶこと自体が一本化なので、Web 操作に
    中継メソッドを足しても経路が増えるだけになる。state を書く操作はこの例外に入らない
- **公開面の判定は `src/core/WebAccess.py` が正本**（ADR-005 S3a）。サーバー側は `before_request`（`_guard_http_request`）と
  Socket.IO の `ws_on()` 登録（書き込みイベントを `_ws_guard` で包む）から問い合わせるだけ
  - **新しいルート / Socket.IO イベントは `ROUTE_ACCESS` / `EVENT_ACCESS` に分類を足す**（READ / WRITE / PLAYERDB / THIS_PC / DESIGNER / STATIC）。
    台帳テスト（`tests/test_webserver_routes.py`）が「全 endpoint・全イベントが分類済み」と分類規則（v1 の GET = READ、それ以外 = WRITE 等。例外は選手 DB の行を返す `GET /players`・`GET /players/entrants` で PLAYERDB（ADR-005 S4m）、未公開の編集を返す `GET /scoreboards/{n}/pending` で PLAYERDB（S4u）、反映前の値と選手の X ハンドルで組む `GET /scoreboards/{n}/post` で PLAYERDB（S4x）、絶対パスを返す `GET /layouts/selected/board/obs-path` で PLAYERDB、この PC のウィンドウを開く `POST /layouts/selected/board:reveal` で THIS_PC（S4s））を pin する。
    分類のない名前は WRITE 扱い（開かない側に倒れる）。GET でも状態を変える legacy ルートは WRITE（効果で分類する）
  - Socket.IO のハンドラは `socketio.on` ではなく `ws_on` で登録する（`socketio.on` で直接登録すると判定を素通りする）
  - WRITE / PLAYERDB: この PC（ループバックか、この PC の LAN IP。`GetLocalIPs()` を初回に引いて保持）からはトークン不要、
    LAN の他端末はトークン必須（`X-Autona-Token` / `Authorization: Bearer` / `?token=`。Socket.IO は接続時の `auth: {token}`）。
    トークンなしで通すのは次をすべて満たすときだけ（`WebAccess._AmbientRefusal`）:
    転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP`）が無い（ローカルのプロキシ・トンネルの中継は他端末扱い）、
    `Host` がこのサーバーの名前と待ち受けポート（DNS rebinding 対策）、`Sec-Fetch-Site` が無いか `same-origin` / `none`、
    `Origin` が無いか自分のオリジン（`null` は不可）。
    拒否は v1 のエラーエンベロープ（401 `UNAUTHENTICATED` / 403 `FORBIDDEN`。Socket.IO はイベントの ack）
  - THIS_PC: 上のトークンなしの判定を通った要求（`GRANT_AMBIENT`）だけを通す。ほかは**トークンがあっても** 403 `FORBIDDEN`（`WebAccess.THIS_PC_ONLY`。`_guard_http_request`）。配信 PC の画面にウィンドウを開く操作のためで（ADR-005 S4s。「エクスプローラで開く」）、シェル操作にはせず core に置く。OS の操作を足すなら、クライアントからパスを受けず、シェルを通さない（`shell=True` にしない）。実行ファイルは名前で探させない。Explorer は自前で引数を読みカンマで区切るので、`LayoutCatalog._ExplorerSelect` は list 形でなく引用符つきの文字列で渡す
  - トークンは `WebAccess.Token()` が初回に読み（無ければ発行）、以後メモリに持つ。変えるのは `RegenerateToken()` だけ。
    settings.json を読めなかった（`SettingsManager.loadFailed`）・値が壊れているときは発行せず `""`（LAN の書き込みは全部拒否）
  - Socket.IO は接続を全部受け入れて state を配る（OBS の file:// オーバーレイのため）。書き込み可かどうかは接続時（handshake の
    要求のヘッダ）に決めて `request.environ` に持つ。トークンなしで通れる接続はトークンを送ってきても ambient 扱いで、再発行で切れない。
    トークンで入った接続だけを世代で切る。選手 DB の broadcast は信頼済みの room にだけ送る
  - 静的配信は `StaticPath()` の許可リストだけ（`layout/`・`assets/`・`web/build/`・`out/`・`user_data/` の画像フォルダ・
    `tournament_banner.png`）。Flask の `static_folder` は無効。許可リスト外は 404 + 種類ごとに初回 warning
  - **`out/` への画像の出力も値を信じない**（`StateManager._ExportKind`）。state の文字列は運用者や start.gg が打った名前でありうるので、`./` のコピーは `StaticPath()` が許す（= すでに配信している）画像の拡張子（`EXPORT_IMAGE_EXTENSIONS`）だけ、ダウンロードは `https` + `EXPORT_IMAGE_HOSTS`（start.gg の画像ホスト）+ png / jpg だけ。ほかはただの文字として `.txt` に出す。新しい絵の置き場所を足すなら、`StaticPath()` の許可リストと `tests/test_state_manager_image_export.py` の固定に足す
  - Web UI の開発サーバー（`npm start`、localhost:3100）は別オリジンなので、書き込みには `?token=<token>` 付きで開く
- CORS は Flask-CORS で全オリジン許可のまま（読み取りは別オリジンからも読める）。**例外: `/api/designer/imports`** は Origin を `null` / figma.com に限定し、ペアリングコード（`X-Autona-Pairing` ヘッダ）が必須。
  `/api/designer/*` は Designer トークン（ループバック限定）とペアリングコードの独自ガードのままで、S3a の判定は通さない（DESIGNER）
- **デスクトップの共有ビュー（`AutonaWebPanelHost`）にはこのサーバーのページしか載せない**（ADR-005 S4e）。ページのスクリプトに
  クリップボードの書き込みを許しているため（`JavascriptCanAccessClipboard`。配信サムネの画像コピー）
  - React のパネルに外部へのリンクを置くなら `target="_blank"` にし、同じフレームで外へ遷移させない。ビューは新しいウィンドウを
    作らず、クリックからの（`isUserInitiated`）`http` / `https` の要求は既定のブラウザに渡す（`_PanelPage._OpenOutside`。ADR-005 S4r。X の投稿画面）。ほかは開かない。
    例外は Designer: クリックからの、ページと同じオリジンの `/web?view=designer` ちょうど（`_IsDesignerUrl`。パス・クエリ・フラグメント・userinfo まで見る）だけ、既定のブラウザへ渡さずホストが `AutonaDesignerWindow` を開く（ADR-005 S4s。トークンの発行と破棄は Qt 側のままで、ページにトークンを渡さない）。
    もう 1 つは SCORE ページの「ブラウザ版を開く」: クリックからの、ページと同じオリジンの `/scoreboard` / `/team` ちょうど（`_IsBrowserScoreboardUrl`。クエリ・フラグメント・userinfo が無いこと）だけ、ホストがクリックの時点で URL を組み直して既定のブラウザへ渡す（`BrowserScoreboardUrl`。Qt のボードのボタンと同じ形: Web サーバーに届くアドレスと、LAN 接続でトークンがあるときだけ `?token=`。ポートと LAN 接続は `WebServer.Listening()` = 実際に待ち受けている値で、サーバーが無いときだけ設定。ADR-005 S4aa）。ページはトークンを持たず、ログにはホストだけを出す。
    クリックなしのスクリプトは `JavascriptCanOpenWindows=False` で届かず、Qt が知らないスキームを OS に渡す経路は
    `DisallowUnknownUrlSchemes` で閉じている（シェル操作は増やしていない。`tests/test_web_panel_host.py` が pin）
  - 担保: `_PanelPage.acceptNavigationRequest` が、ページ自身が始めた別オリジンへのメインフレームの遷移（リンク・フォーム・
    スクリプト・リダイレクト）を拒否する。ホスト自身の読み込み（`load()`・破棄されたページの再読み込み）は通す。
    Qt は拒否した遷移を読み込みの失敗（`loadFinished(False)`）として出すが、文書は残っているので、ホストはそれで再読み込みしない
    （`refusedLast`。`tests/test_web_panel_host.py` が pin）

## Helpers

- `Helpers/` 配下はユーティリティ。各ファイルは単一責務
- `AutonaQtHelper.py` の `GuiExecutor` はワーカースレッドから GUI 操作する際の橋渡し
- `AutonaQtHelper.HeldBackText` は、state を描画する入力欄の「描画が残した文字は書き戻さない、打った文字は書く」判定
  （選手カード・ボード 1 / 2 のチーム名・クルーボードが共有。ADR-005 S3e-2）。打ちかけの間にその欄自身の値が
  別の所で書かれたときだけは、その書き込みが勝つ（S2d）。state を描画する入力欄を新しく作るときはこれを使う。
  「離れたら描画し直す欄」の印は持たないので、呼び出し側が別に持つ
- `AutonaCountryHelper` / `AutonaLocaleHelper` はシグナルを持つ。変更通知が必要な場面で使う
  （`AutonaCountryHelper` は ADR-005 S1c 以降、国モデル `countryModel` と `countriesAboutToReload` / `countriesUpdated` だけを持つ
  Qt アダプタ。国・地域のデータは `core/CountryCatalog` を読む）
- 悪語フィルタの正本は `core/BadWordFilter.py`（Trie ベース。ADR-005 S3e-1）。`BadWordFilter.instance` は `bootstrap_core()` が作り、
  プログラムのロケールと国 → 言語 / 大陸の表を `AutonaLocaleHelper` から callable で注入する（呼ぶたびに読む）。
  転送だけの互換層だった `Helpers/AutonaBadWordFilter` は ADR-005 S3e-3 で削除した（呼び出し側が無くなった。選手カードの読込は
  `PlayerService.ApplyData` が、実況は S3e-2 から `CommentaryService` が core の `BadWordFilter.instance` を呼ぶ）
- 選手 DB の正本は `core/PlayerDB.py`（`PlayerDB.instance`。ADR-005 S3e-1）。`database` は `lock`（RLock）で守り、外からは
  `Get` / `Contains` / `Items` / `GetPlayerFromTag` / `TournamentPlayers` で読む（dict を直接回さない。entrants のワーカーが書いている）。
  `Get` 系が返す選手 dict は生（GUI 側用。ロックの外で書き換えない）。プロセスの外へ出すもの（`/playerdb`・`ws_playerdb`）は
  `TournamentPlayers()` の深いコピーをシリアライズする。保存はロックの中で JSON 文字列にするだけで、書き込みはロックの外
  （保存専用のロックと世代番号で直列化。mkstemp → `os.replace` を 4 回試行し、だめなら旧実装と同じ上書き。`local_players.json` の
  バイトは旧実装と同じ）。変更は `changed`（ObserverChannel）で
  **ロックの外から**通知する。`AutonaPlayerDB` は補完モデルと `signals.db_updated` だけの Qt アダプタで、`bootstrap_core()` の
  `Attach()` が `changed` → `SetupModel`（GUI スレッドで `db_updated`。他スレッドからは積むだけで待たない: ADR-005 S3f-2）を中継する（process 寿命の static クラスなので
  `tests/test_signal_lifetime.py` の購読許可リストに入っている）。選手データの読み書きは `PlayerDB.instance` を呼び、
  `AutonaPlayerDB` にデータの操作を足さない
