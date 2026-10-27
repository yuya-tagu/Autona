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
    今のビルド自身が残しうるものの起動ごとの掃除（`player_editor` など）は移行ではなく
    `StateSchema.SeedPanelState()` に置く
  - テスト: harness の `settle()` は `StatePersistence.Flush()` まで待つ。`StateManager.Initialize()` を
    呼ぶテストは tmp CWD で呼ぶ（そこで store のパスが決まる）

## StateSchema（キー定義とシードの正本）

`src/core/StateSchema.py`（headless core Step 2）。**state の「何があるか」の定義を
ウィジェットに書かない**。

- held exempt (`STATS_KEYS`) / 表示トグル (`DISPLAY_OPTIONS`) / 起動時シード
  (`SeedBoard` / `SeedPanelState`) はここが正本。ウィジェット側に同じ定義を置き直さない
- `SeedBoard(n)` は「埋める」ではなく **サブツリーを skeleton に戻す**。生きているボードに
  対して呼ばない（未構築ボードのプレースホルダと、遅延構築時の再シードが用途）
- **構築直後のボードの中身（選手スロット・クルーボード）のシードはドメインサービス側**（ADR-005 S3d / S3e-6）:
  `core/PlayerService.SeedEmpty()`（1 スロットを空のカードと同じ内容で）/ `SeedEmptyRoster(board)`
  （両チームの `DefaultPlayersPerTeam()` 人分）、`core/CrewBattleService.SeedDefaults()`（クルーボードの
  起動時の書き込み）。**選手スロットは両シェルとも core が書く**: ヘッドレスは `src/headless._SeedBoards()` の
  `SeedEmptyRoster`、デスクトップはボードの構築時の `AutonaActions.SetRoster`（足りないスロットを `SeedEmpty` で
  埋める。カードは何も書かずにスロットに従う）。クルーボードの起動時の中身はデスクトップではボードの
  ウィジェットが publish するので、`SeedDefaults` を使うのはヘッドレスだけ。形はデスクトップの空ボードと
  `tests/test_headless_seed.py` が照合する
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
  ならない**（保存済み state（`user_data/state.json`）の互換に加えて、eye メニューの表示トグルも
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
  `core/Bracket.py`（旧 `src/AutonaBracket`。ラウンド名は呼び出し側が渡す。ADR-005 S3e-8）/
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
  両面ミラー）/ `SetStreamUrl` / `SetStreamTitle` /
  `PublishResultPlayers`（パネルが組んだ dict をサブツリーごと publish する操作。同じ形だった `PublishCommentary` は
  ADR-005 S3e-2 で削除し、実況の書き込みは `core/CommentaryService.py` へ移った）/
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
  `AutonaWebOperations.get_swap` は state を読む。スワップ後のカード・スコア・チームカード・ステージボタンは
  held サイクルからの描画で追従する。統計の再取得は操作が出す（`SwapTeams` / `SwapPlayers` → `StatsService.TeamsChanged`。
  イベントループに戻ってから 1 回。ADR-005 S3e-5。それまではボードが held サイクルでフラグの反転を検知して出していた）。
  アップセット係数は操作が core 内で再計算する。
  入替サイクルではボードがゲームトラッカーにスコアの変化を伝えない（`_SyncScoreDisplays(followStages=False)`。
  記録は入替が鏡映済み）。**入力中の欄は入替・移動の前に確定する**（`AutonaScoreboardWidget.CommitFocusedEdit`
  がそのボード内のフォーカスを外し、Qt に `editingFinished` を出させる。欄の補完ポップアップが開いていれば
  先に閉じる（開いたままだと確定されない）。SWAP ボタン・上下移動・ホットキー・Web の全入口。
  旧実装は打ちかけの文字を選手と一緒に運んでいた）
- **クルーボード（board 3）は `CrewBattleService` が書き手**（Step 5 第 4 弾）。
  ただし *入口* のうち board 1/2 と語義が同じもの — チーム名（`SetTeamName`）・
  チームカラー（`SetTeamColor`）・match バーの 3 combo（`SetMatchField`）— は
  `AutonaActions` を呼ぶ。`AdjustScore` / `ResetScore` は board 3 では今も no-op で、
  クルーの操作（ストック・出場中のメンバー・人数・スワップ・ストックのリセット・クリア・セットデータ）は
  `CrewBattleService` の操作（ADR-005 S3e-2）。ボードの入力欄も Web（`AutonaWebOperations`。ボードホストの
  `Widget()` は使わない）も同じ操作を呼び、操作は state から読んで書く。ヘッドレスでも動く。
  ボードは state を描画するだけ（`AutonaCrewBattleWidget._RenderFromState`。Bridge の通知で 1 回にまとめて遅延描画し、
  入力中の欄は飛ばす。確定した欄は選手カードと同じ held-back の規則で、描画が残した古い文字を書き戻さない）。
  スワップのフラグ（`teamsSwapped`）は state に無く、`CrewBattleService._swapped`（ボード番号ごと）が持つ
  （`program_state.json` のキーを増やさないため。ボードの起動時の書き込みで消える）
- **best_of は state 主導**（ADR-005 S2e）。`SetBestOf(board, n)`（board 1,2。board 3 は False で、
  クルーはスピンボックス → `CrewBattleService`）が 6 キーとステージ行を 1 `SaveBlock` で書く。
  0〜99 に丸め、値が変わらなければ何も書かない（旧 `valueChanged` と同じ。同じ値でゲームの記録は消えない）。
  ゲームのロードは `RepublishBestOf(board)`（文言を作り直し、同じ行数で行をリセット）。
  行はボードが構築時に `RegisterGameTracker(board, tracker)` で登録したトラッカーに、操作の block の中で
  `SetStageCount` させる（通知ハンドラの中で書かない = 再入しない。解除はボードの `destroyed` から。未構築ボードは
  キーだけ）。スピンボックスと行は held サイクルの `_SyncBestOf` が描画する（`_SyncSetFields` の後、
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
    ヘッドレスは 503。クルーボードの人数は S3e-2 から core）。カードは held サイクルで state のスロットに追従する
  - ヘッドレス（`BoardHost`。既定）: 呼び出し元のスレッドで core を呼ぶ。1 本の再入可能ロック（`RLock`）で 1 つずつ
    実行する（GUI スレッドに並ばないので、`clear_all` や v1 `PATCH set` の途中に別の要求が混ざらないように。
    ロックの中でメインスレッドを待たない・`StateManager.lock` を持ったまま取らない。待つもの（`savePlayerToDb` の選手 DB への
    書き込み: `PlayerDB.changed` → `AutonaPlayerDB.SetupModel` がメインスレッドで走る）は `Run` を抜けてから行う。S3e-4 レビュー）。ボードが無いので held にならず即時公開
    （反映ボタンが無いのでこれが正しい）。ウィジェットが要るもの（`Widget()`。ボード 1〜3 以外 = どのシェルにも無いボード。
    デスクトップはそこで 500）は `NotAvailable`（503）。ボード 1 / 2 の
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
  発火して書き戻し = エコーループ）。再入ガードの実例は
  `AutonaScoreboardWidget._SyncScoreDisplays`
- ホットキーは `AutonaHotkeys.ConnectActions()` が `bootstrap_gui()` から 1 回だけ結線する（ADR-005 S3c でデスクトップ側の段へ移動。ヘッドレスにはホットキーが無い）。
  対象ボードは `AutonaActions.ActiveBoard()`（`SettingsManager.scoreboard.battle_format`）。
  `swap_teams` も S2c からここ（旧: 構築済みの全ボードに接続しており、形式切替後は両ボードが入れ替わった）。
  ただし `swap_teams` だけは、アクティブボードが未構築なら何もしない（旧実装どおり。スコアのホットキーは
  未構築でも効く。未構築ボードは held でないので、入れ替えると即時公開され、構築時のシードで消える）。
  ボードに残るのはパネル操作の `load_set` だけ（`refresh_phase_group` は ADR-005 S3e-8 から
  `ConnectActions()` が `Top8Service.RefreshFromProvider` に直結する。それまでは Window が Top 8 パネルを構築して呼んでいた）
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
| `src/core/StatsService.py` | なし（`StatsService(n)` を呼ぶたびに作る。取得の `fetcher`（`AutonaStatsUtil`。`bootstrap_core()` が登録）と要求番号はクラスの持ち物） | 統計のすべて（ADR-005 S3e-5）: いつ取り直すか（`PlayerChanged` = 選手の読込・クリア・カードの手入力の id / seed の変化。`PlayerService.Announce` が書き込みの保存ブロックの中で呼ぶ / `TeamsChanged` = スワップ・上下移動・クリア。`AutonaActions` が操作の後に呼び、`fetcher.Later` でイベントループに戻ってからボードごとに 1 回 / `Refresh` / `RefreshAll` = Web のルートと STATS パネル）・取得可否の判断・アップセット計算・`last_sets`/`history_sets`/`recent_sets`/`upset_factor` の書き込み。人数は state のスロット数（0 なら `DefaultPlayersPerTeam()`）。**遅れた答えは捨てる**: 要求ごとに（ボード, 統計, チーム）の番号を `StateManager.lock` の中で振り、答えはメインスレッドで、番号が最新かつスロットが要求したときの選手のままのときだけ書く。番号は 1 つのカウンタから出す（キーをまたいで重ならない）。取得の開始はロックの外。**取得の失敗は統計ごとにログに残して次へ進む**（`RefreshQuietly`。いくつも依頼する入口と Web のルートはこれを呼び、ルートは `"OK"` のまま。旧実装は統計ごとの Qt シグナルだった）。ボードの構築・シェルによらず動く |
| `src/core/SetFollowService.py` | なし（`SetFollowService(n)` を呼ぶたびに作る。何を追従しているか・ステーション・要求番号はボード番号ごとにクラスが持つ。取得と 5 秒の時計の `fetcher`（`AutonaSetFollowUtil`）とボードホストの引き方 `boardHost` は `bootstrap_core()` が登録） | セット読込と追従のすべて（ADR-005 S3e-7。それまではボードのウィジェット・2 つの QTimer・provider アダプタの Worker）: `LoadSet`（セット選択ウィンドウ・Web の `load_set`。クリア → 追従の記録 → 取得 → 答えを `UpdateSetData`。**大会が読み込まれていなければ何もしない**＝ボードを消さない）・`FollowStation`（ステーションウィンドウのステーション / 配信）・`PullStream`（Web の `pull_stream_set`。**追従中のチャンネルなら照会し直すだけ**で、セットが変わったときだけ読み直す。Web UI の自動更新スイッチが数秒ごとに送るので、毎回追従し直すとクリア → スコア 0 になる。追従を始める最初の 1 回も、最後に読んだセットと同じならクリアしない）・`StopFollowing`（タイマー行のキャンセル）・`Tick`（5 秒ごとの取り直し。`general.disable_autoupdate` が明示的に `false` のときだけ。デスクトップはボードの構築のたびに `true` にするが、設定画面の「Disable automatic set updating」を外すと次の構築まで取り直す）・`ApplySetPayload`（旧 `ChangeSetData`。取得したセットと `POST /score` の 1 保存サイクル）。**判定と書き込みはすべてボードホストの `Run` の中**（答えの照会番号・要求番号の判定もその先頭で行うので、判定の直後に別スレッドの Web 操作が割り込まない）。取得の答えはメインスレッドに届く（`fetcher` の `Signal(object)`。旧 Worker 自身のシグナルは GC で答えが消えることがあった）。**遅れた答えは捨てる**: 読んだセットでない答え（`IsStaleUpdate`）・後から全読込が始まった取り直し（要求番号）・手動 / Web の読込（読み込んだときだけ）・別のステーション・キャンセルの後に返った照会と、後から出した照会が先に答えた照会（照会番号。pull と tick で 1 列）・追従をやめた / 取り直したステーションの後続セット。**全読込の取得が失敗したら（セットの無い答え・例外・取得できない）、最後に読んだセットを忘れる**（ボードはクリア済みで何も出ていない。次の pull・照会・tick・同じセットの選び直しで読み直す）。ステーションの後続セットはメモリ（`_queues`）にだけ持ち、`score.{n}.station_queue` には書かない（`{}` のまま）。ロックの中で取得を待たない（pitfalls #17）。1 人分の `POST /score`（`team` / `player`）は state に無いスロット（"0"・負の数を含む）を書かずにログに残す |
| `src/core/GameTrackerService.py` | `AutonaIndividualGameTracker.service`（ボードが `AutonaActions.RegisterGameTracker` で登録する。ADR-005 S2e） | ステージ勝敗の記録（`score.{n}.stages.*`）・ステージ⇔スコアの相互整合・`writingScore` ガード・行の作り直し（`SetStageCount`。呼ぶのは `AutonaActions.SetBestOf` / `RepublishBestOf` だけで、`rowsVersion` が作り直しのたびに進み、ウィジェットの `RenderStageRows` がそれを見てボタンを作り直す）。存在しない行への `SetStageResult` / `SetStageAsset` は lock の中で断る（警告して False。別スレッドの `SetBestOf` で行が消えた後、作り直し前の古いボタンが押されても行を書き戻さない） |
| `src/core/SetLoadService.py` | `AutonaScoreboardWidget.setLoad`（`SetFollowService` も同じボードの 1 個を作って使う） | セット読込の正規化（ラウンド/フェーズ/プール・スコア・敗者フラグ・チーム名とロゴ・`bracket_type`・追従中セット）。最後に読んだセット `lastSetSelected` も持つ（state の外。ADR-005 S2d でボード属性から移動。S3e-7 からボード番号ごとにクラスが持つので、ボードのインスタンスでもヘッドレスでも同じ値を読む。クリアと `ClearSetTracking()` で忘れるので、同じセットを選び直すと再読込される。`get_set` と stale 判定が読む）。スワップ後の面の解決は `DisplayTeam()` が唯一の正本（`score.{n}.teamsSwapped` を読む。S2c までボード属性を callable で注入していた） |
| `src/core/TournamentInfoService.py` | `TournamentInfoService.instance`（`bootstrap_core()` 生成） | `tournamentInfo.*` の書き込み全部（provider ペイロードの適用・**EVENT タブ入力欄の `Set{Text,Number,Date}Field`**・表示大会名の 3 系統の上書き規則・`logoVersion`・起動時シード）。EVENT パネル不在でも動くことが遅延構築の前提。**両方向が同じ 3 操作を通る**（provider 側の `_ApplyKey` も入力側と同じメソッドを呼ぶ）。ただし `themeColor`/`accentColor` の 2 キーだけは **`core/ColorService.py` の持ち物**で本サービスを通らない（歴史的にこのプレフィックスなだけで、意味はカラー機能側）。**表示大会名の入力欄は SCORE パネルとクルーボードの 2 箇所にあり、どちらも `SetDisplayName` を呼ぶ**（クルー側は Step 5 第 4 弾で合流） |
| `src/core/PlayerService.py` | `AutonaBasePlayerWidget.player`（選手ウィジェット 1 枚につき 1 個。path に束縛）。加えて**一時インスタンス**: モジュール関数 `RepublishTeamMergedNames(board, team)` が、state にあるそのチームの各スロットの path で 1 個ずつ作って使い捨てる（カードを経由しない） | 選手スロット配下の書き込み全部（入力フィールド・seed/birthday・国/州・`mergedName`/`mergedOnlyName`・アバターとスポンサーロゴ・キャラスロット・`city`/`id`・スロット削除）。**選手の読込（`ApplyData(data, loadFromDB, clear, no_mains)`）・カードのクリア（`ClearSlot`）・DB への保存（`SavePlayerToDB` = state 側の `PrepareSaveToDB`（番号付きの記録を返す）+ DB 側の `SaveRecordToDB`。Web の操作は DB 側をボードホストの外で呼ぶ: `PlayerDB.changed` の受け手 `AutonaPlayerDB.SetupModel` がメインスレッドを待つため。外へ出したので、同じ DB キーにもっと新しい番号の記録が保存済みなら古い方を捨てる。ADR-005 S3e-4 レビュー）もここ**（ADR-005 S3e-3）: 読込は DB の記録とのマージ・悪語フィルタ（コピーに対して。呼び出し側の dict と DB の記録は書き換えない）・国 / 地域（code と `FindState`）・mains → キャラスロット・seed・合成名と画像を 1 保存サイクルで書き、旧カードの `SetData` と同じ Set 列を出す（`tests/test_player_apply.py` / `tests/test_player_service.py` が ae922c24 の録画と照合）。操作の前後で id / seed が変わったか（`SlotChange`。戻り値でもある）で、読込自身が統計を 1 回だけ依頼する（`Announce` → `StatsService.PlayerChanged`。ボードのスロットだけ。`SetSeed` / `SetPlayerId` は `announce=True` のときにカードの手入力として依頼する。ADR-005 S3e-5。それまでは呼び出し側のカード・ボード・ボードホストが依頼していた）。**クリアしない読込（Web の選手編集・legacy `update-team`）で、書き込み後のスポンサー + タグの組が前と違えば別の選手として扱い、読込が書かない id・city を空にする**（`_NamesAnotherPlayer`。ADR-005 S3e-4。同じ組なら残す。残すかは読込が実際に書いたかで決めるので、途中の例外でも前の選手の id が残らない。online_avatar・seed は残す: カードの手入力の `NameChanged` と同じで、LIVE-BUG-1「タグを直しても顔写真は消えない」を守る）。保存はウィジェットではなく state から組む。`AutonaActions.ClearPlayers` も各スロットの `ClearSlot` を呼ぶ。`BoardNumber()` が `score.{N}` と `player_editor.1` を見分ける。**`mergedName` / `mergedOnlyName` の ` [L]` は state のチームの `losers` から付ける**（`PublishMergedName(sponsor, tag)` が `TeamLosersKey()` を読む。ADR-005 S2b）。フラグの書き手は同じ SaveBlock で `RepublishTeamMergedNames` を呼び、チーム全員の名前を state の `team` / `name` から作り直す: `AutonaActions.SetTeamLosers`（LOSERS チェックボックス・v1 `PATCH set` の `AutonaWebOperations.set_team_losers`）と `SetLoadService.ApplyLosers`（セット読込・legacy `-set?losers=` の `ChangeSetData`。`DisplayTeam` で解いたチーム）。どちらもフラグが変わったときだけ。カードは名前確定で `_OnTextChanged`（state に書く）→ `NameChanged`（publish）の順に走るので、作り直しがどのスレッドから割り込んでも同じ名前になる。**国/州とキャラスロットはカードから識別子だけを受け取り、値はここで組む**（ADR-005 S1d）: `SetCountry(code)` / `SetRegion(countryCode, regionCode)` は `CountryCatalog.CountryByCode` / `State` から、`PublishCharacterSlots([CharacterSlot(en_name, skin 行, variant en_name, フォールバック名), ...])` は `GameAssetCatalog.CharacterRow` / `SkinRow` / `VariantRow` から（1 回の publish は `Current()` 1 回のスナップショットで解決）。入れ子のキー順は旧実装が Qt モデル経由で出していた QVariantMap 順を `DictHelper.qvariantmap_copy` で再現する（`program_state.json` と Socket.IO delta のバイト互換。`tests/test_player_slot_publish.py` と `tests/test_player_service.py` が移行前の golden と照合）。カタログが知らない識別子は、キャラなら `{"name": フォールバック名}`（手入力テキスト、または選ばれた行自身の `name`。コンボの「表示名 / en_name」ラベルは使わない）、国/州なら `{}`。カタログは `games=` / `countries=` で差し替え可能（既定は呼び出し時の `.instance`） |
| `src/core/CrewBattleService.py` | `AutonaCrewBattleWidget.crew`。Web（`AutonaWebOperations`）は呼び出しごとに 1 個作る | クルーボード（board 3）の書き込み全部（起動シード・メンバー名・ストック（`AdjustMemberStock` / `SetMemberStock`。0〜99）・人数（`SetMemberCount`。1〜9）・出場中のメンバー（`SelectActiveMember` / `ReleaseActiveMember`。1 チーム 1 人）・**クルー合計の算出**（名前が入った表示中メンバーの stock 合計 → `team.{t}.score`）・crew 版のセットデータ正規化（`ApplySetData`）・`best_of`（raw のみ・0〜99）・スワップ / ストックのリセット / クリア（`SwapTeams` / `ResetStocks` / `ClearCrew`。両チームを 1 サイクルで publish）・スワップのフラグ（`teamsSwapped`。state の外）。**操作は state を読んで書く**（ADR-005 S3e-2。旧実装はウィジェットの行を受け取っていた）。合計も state から算出する |
| `src/core/CommentaryService.py` | なし（モジュール関数） | `commentary.*` の書き込み全部（ADR-005 S3e-2）: 選手データでの 1 枠の上書き（`ApplyPlayerData`。Web と補完の選択。悪語フィルタ → 正規化 → 1 枠をマージしてルートごと 1 回 publish = 旧パネル経由と同じ Set）・タグからの読込（`LoadFromTag`。`PlayerDB`）・パネルの 1 欄の編集（`SetField`。値が変わったときだけ。表示は `MAX_DISPLAY` 人まで）・並べ替え（`Reorder`）。実況パネルは state を描画するだけ（入力中の欄は飛ばし、held-back の規則で古い文字を書き戻さない） |
| `src/core/Top8Service.py` | なし（モジュール関数） | `top8.*` の書き込み全部（パネルの 1 欄の編集 `SetPlayerText` / `SetPlayerCountry` / `SetPlayerCharacter`（その 1 キーだけを、変わったときだけ）・表示ラベルの既定値・反映（staged スコアのマージ + `Recompute`）・全リセット・Start.gg 取り込み）。**取り込みの入口は `RefreshFromProvider()` の 1 つ**（ADR-005 S3e-8。パネル・ホットキー・legacy / Socket.IO / v1 が同じ関数を呼び、ヘッドレスでも動く）: 大会の最後の phase のダブルエリミのグループ（`TargetPhaseGroup`）→ 取得 → `core/Bracket` → `Top8Model.FromBracket` → 選手を `GameAssetCatalog` / `CountryCatalog` で解決 → `ImportBracket`。取得は `bootstrap_core()` が注入する `fetcher`（provider の `RunWithProvider`。provider のワーカースレッドで走る）。実行中フラグは取り込み全体（取得〜書き込み）で 1 つ、全入口が共有し、実行中の要求は `BUSY` で捨てる（キューしない）。選手の解決と書き込みは `StateManager.lock` の中（ゲームの切り替えとの競合で前のゲームのキャラが残らない）。空・失敗の結果は何も書かない。**ゲームが別のゲームに変わったときのキャラの消去 `ForgetCharacters()`** も持つ（キャラがある枠だけ・1 保存サイクル。呼ぶのは `GameAssetCatalog.SelectGame` だけ）。Top 8 パネルは操作を依頼し、state を描画するだけ（実況パネルと同じ型）。トポロジ自体は Qt-free の `src/core/Top8Model.py` が正本 |
| `src/core/ColorService.py` | なし（モジュール関数） | 大会カラー（`tournamentInfo.themeColor`/`accentColor`）と機能別カラー（`colors.{feature}.*`）の読み・書き・起動シード。**`tournamentInfo` プレフィックスだがこの族は本サービスのもの**で、`TournamentInfoService` は触らない（歴史的なキー名） |
| `src/core/GameAssetCatalog.py` | `GameAssetCatalog.instance`（`bootstrap_core()` 生成。Qt 側は `AutonaGameAssetManager` が包む） | ゲームアセットの正本（スキャン・ロード・キャラ/スキン/ステージ/アセットパス・Start.gg ID 解決）と `game.*` の書き込み。ロード結果は不変スナップショットで差し替え、リーダーはロックを取らない。書き手のロック保持中に `run_sync` / Qt シグナル / observer を呼ばない（ADR-005 S1a）。**例外は `SelectGame` の `progress` コールバックだけ**でロック保持中に呼ばれるため、渡す側はブロックしない（queued で投げるだけ。アダプタは `AutonaStatusManager` を渡す）。observer から書き手を呼ばない（agent-pitfalls #16）。**`SelectGame` は、state の `game.codename` が空でなく新しい codename と違うとき、Top 8 のキャラを空にする**（writer lock を離した後・同じ `SaveBlock` の中で `Top8Service.ForgetCharacters()` を直接呼ぶ。observer は使わない。起動時の復元（`game` が空）・同じゲームの選び直しでは呼ばない。ADR-005 S3e-8）。同じ判定はスナップショットの `replaced`（置き換えた前のゲームの codename。それ以外と `ForgetSelection` の後は None）に残り、ボードの選手のキャラと mains はロード後にシェルが走らせる `AutonaActions.ApplyGameRoster(replaced)` が空にする。値はロードが返したスナップショットのもので、ローダーの `gameLoaded`（`Signal(object)`。デスクトップはアダプタ、ヘッドレスは `HeadlessGames`。`onLoad` / `gameChanged` の直前に出る）で `bootstrap.wire_game_roster` に渡る。後から `Current()` を読まない（キューされたスロットが走る頃には、次のロードのスナップショットになりうる。ADR-005 S3e-6 レビュー）。**Web / Start.gg のリーダーはカタログを直接読む**（ADR-005 S1b）: `AutonaWebOperations.get_games` / `get_characters` / `get_variants` は `*Payload()` を返すだけで `@gui_thread_sync` を持たない。`StartGGDataProvider` は `CharacterFromStartGGId` / `StageFromStartGGId` で解決し、1 つの結果（1 セットのパース・1 エントラント）を組む間は `Current()` を 1 回取って `snapshot=` で渡す（start.gg の ID → 名前の表 `_startggCharacters` はスナップショット外だが、ゲーム非依存なので実害なし）。ゲームの切り替えは codename で `LoadGameByCodename` を呼ぶ（スキャン上の位置を引いてから位置でロードしない。間の再スキャンで別ゲームになる）。終了を待つ側は `done=`（成否を問わず 1 回）を使い、onLoad（実際にロードされたときだけ）で待たない。新しいリーダーをアダプタの旧プロパティ（`games` / `selectedGame` / `characters` ほか。GUI 側の残りは S4 で消える）に繋がない。ペイロードの入れ子のキー順は旧モデル経由の QVariantMap 順を再現している（Socket.IO のバイト互換。`DictHelper.qvariantmap_copy`（S1d で core 共用に移動。選手スロットの値も使う）。str キーの dict と list だけ変換し、tuple と非 str キーの dict は PySide6 と同じく並べ替えない） |
| `src/core/CountryCatalog.py` | `CountryCatalog.instance`（`bootstrap_core()` 生成。Qt 側は `AutonaCountryHelper` が国モデルを作る） | 国・地域（states）・カスタム国旗・都市→地域表の正本（ADR-005 S1c）。`Load()` が唯一の書き手で不変スナップショットを差し替え、リーダーはロックを取らない。state は書かない。ロードは `Window.__init__` の `AutonaCountryHelper.instance.init()`（ヘッドレスは `src/headless.py` が `Load()` を直接呼ぶ。国名がロケール依存なので `LoadLocale()` の後。ロケールは callable 注入）。都市→地域表（`FindState`）も `Load()` が 1 回だけ読む（最初の照会は選手の読込から `StateManager.lock` の中で来うるため。ADR-005 S3e-3。`Load()` していないカタログでは最初の照会で読む）。**データを返すリーダーは必ずコピー**を返し、表に書き戻さない（旧 `LoadStates` と Start.gg の次セット解決が地域 dict に `asset` を書き込み、`get_states` の出力が「デスクトップがその国を表示したか」で揺れていた）。地域の `asset`（旗パス）は `States()` / `State()` が呼ばれた時点で解決する（地域ごとにディスクを見る。旗の要らない照合 = 選手の読込の地域コード合わせは、`StateManager.lock` の中なので `StateCodes()`（code / original_code だけ）を使う。ADR-005 S3e-3）。`AutonaWebOperations.get_states` は `States()` を返すだけで `@gui_thread_sync` を持たない。Start.gg は `CountryCodeFromEnName` / `FindState` / `State` / `BasicCountryInfo` で解決する。選手の国を code から引くのは `CountryByCode`（標準国 → カスタム国旗の順。国モデル走査と同じ答え。`PlayerService.CountryValue` が使う） |
| `src/core/SponsorHelper.py` | なし（静的メソッド） | `...player.{M}.sponsor_logo` / `sponsor_logos.{I}` の解決と書き込み。`PlayerService.PublishImages` から呼ばれる |

**状態を持たないサービスはモジュール関数でよい**（`Top8Service` / `ColorService` / `CommentaryService`）。
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
- Qt 依存が避けられないもの（統計の取得 = `StatsService.fetcher`、ローカライズ文字列など）は**呼び出し可能オブジェクトで
  注入**する（選手数は注入しない: state のスロット数。ADR-005 S3e-5）。core 側が Qt を import しないことが層の定義（`tests/test_core_qt_free.py`）
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

- `AutonaWebServer.WebServer` は素の Python クラスで、`start()` が `threading.Thread`（daemon）で werkzeug の threaded サーバー（`make_server(threaded=True).serve_forever()`。旧 `socketio.run()` が threading モードで組むものと同じ）を回す（ADR-005 S3c。旧: QThread）。`stop(timeout)` が `shutdown()` で止める（旧 `QThread.terminate()` の強制終了は廃止）。起動失敗（bind 不能など）はコンストラクタの `on_start_failed(error: str)` コールバックにサーバースレッドから通知する（デスクトップは `gui_executor` で GUI スレッドへ渡してダイアログ表示）。ポート衝突の事前確認 `check_webserver_port()` は QMessageBox を出すのでデスクトップ専用
- Flask アプリと SocketIO インスタンスは static 変数
- ルートハンドラの行き先は **`src/AutonaWebOperations.py`（モジュール関数）の 1 つだけ**（ADR-005 S3c。S3e-9 で
  ボード依存の `WebServerActions` を削除して 1 本になった）。デスクトップとヘッドレスが同じ関数を通り、違いは
  シェルが `AutonaWebOperations` に登録するものだけ（`WebServer` 自身はシェルを知らない。コンストラクタは
  `on_start_failed` だけを取る）:
  - `boardHost`（ボードホスト。上の AutonaActions 節）
  - `loadGameByCodename`（ゲームのロード。デスクトップはアセットアダプタ、ヘッドレスは `src/headless.py` の
    カタログ直のローダー）
  - **シェル操作** `openSetSelection` / `renderThumbnail`（セット選択ウィンドウを開く `open_sets`・サムネイル
    `get_thumbnail`。デスクトップのウィンドウだけが答える操作）。デスクトップは `bootstrap_gui()` がスコアボード dock の
    `OpenSetSelection` / `RenderThumbnail`（GUI スレッドで同期実行）を登録し、ヘッドレスは何も登録しない。未登録なら
    `NotAvailable` → **503 `UNAVAILABLE`（`not available in headless`）**。legacy は errorhandler、Socket.IO は ack
    （`_ws_unavailable`）が同じ形で返す（v1 にこの 2 本は無い。v1 の UNAVAILABLE は契約として残す）。
    ヘッドレスで 503 を返すのはこの 2 本だけで、ほかのルート・イベントはすべて答える（`tests/test_web_operations.py` の
    `test_every_route_and_event_answers_headless_but_the_shell_operations` がルート台帳を歩いて pin）。
    **シェル操作を増やさない**: 新しい操作は core に置き、どちらのシェルでも動くようにする
- Web UI からの POST リクエスト → Web 操作 → core（StateManager 更新）→ WebSocket 配信の流れ
- 新しいエンドポイントの操作は core に置き、ルートからは `AutonaWebOperations` 経由で呼ぶ
  - 例外: **読み取り専用のカタログ系エンドポイントは Actions を経由しない**
    （ADR-001 2026-10-22 Addendum）。`GET /api/v1/layouts` はルートから Qt 非依存の
    `src/core/LayoutCatalog.py` を直接呼ぶ。state を読み書きせず、パネル（Board /
    Commentary / Top 8）と同じ関数を呼ぶこと自体が一本化なので、Web 操作に
    中継メソッドを足しても経路が増えるだけになる。state を書く操作はこの例外に入らない
- **公開面の判定は `src/core/WebAccess.py` が正本**（ADR-005 S3a）。サーバー側は `before_request`（`_guard_http_request`）と
  Socket.IO の `ws_on()` 登録（書き込みイベントを `_ws_guard` で包む）から問い合わせるだけ
  - **新しいルート / Socket.IO イベントは `ROUTE_ACCESS` / `EVENT_ACCESS` に分類を足す**（READ / WRITE / PLAYERDB / DESIGNER / STATIC）。
    台帳テスト（`tests/test_webserver_routes.py`）が「全 endpoint・全イベントが分類済み」と分類規則（v1 の GET = READ、それ以外 = WRITE 等）を pin する。
    分類のない名前は WRITE 扱い（開かない側に倒れる）。GET でも状態を変える legacy ルートは WRITE（効果で分類する）
  - Socket.IO のハンドラは `socketio.on` ではなく `ws_on` で登録する（`socketio.on` で直接登録すると判定を素通りする）
  - WRITE / PLAYERDB: この PC（ループバックか、この PC の LAN IP。`GetLocalIPs()` を初回に引いて保持）からはトークン不要、
    LAN の他端末はトークン必須（`X-Autona-Token` / `Authorization: Bearer` / `?token=`。Socket.IO は接続時の `auth: {token}`）。
    トークンなしで通すのは次をすべて満たすときだけ（`WebAccess._AmbientRefusal`）:
    転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP`）が無い（ローカルのプロキシ・トンネルの中継は他端末扱い）、
    `Host` がこのサーバーの名前と待ち受けポート（DNS rebinding 対策）、`Sec-Fetch-Site` が無いか `same-origin` / `none`、
    `Origin` が無いか自分のオリジン（`null` は不可）。
    拒否は v1 のエラーエンベロープ（401 `UNAUTHENTICATED` / 403 `FORBIDDEN`。Socket.IO はイベントの ack）
  - トークンは `WebAccess.Token()` が初回に読み（無ければ発行）、以後メモリに持つ。変えるのは `RegenerateToken()` だけ。
    settings.json を読めなかった（`SettingsManager.loadFailed`）・値が壊れているときは発行せず `""`（LAN の書き込みは全部拒否）
  - Socket.IO は接続を全部受け入れて state を配る（OBS の file:// オーバーレイのため）。書き込み可かどうかは接続時（handshake の
    要求のヘッダ）に決めて `request.environ` に持つ。トークンなしで通れる接続はトークンを送ってきても ambient 扱いで、再発行で切れない。
    トークンで入った接続だけを世代で切る。選手 DB の broadcast は信頼済みの room にだけ送る
  - 静的配信は `StaticPath()` の許可リストだけ（`layout/`・`assets/`・`web/build/`・`out/`・`user_data/` の画像フォルダ・
    `tournament_banner.png`）。Flask の `static_folder` は無効。許可リスト外は 404 + 種類ごとに初回 warning
  - Web UI の開発サーバー（`npm start`、localhost:3100）は別オリジンなので、書き込みには `?token=<token>` 付きで開く
- CORS は Flask-CORS で全オリジン許可のまま（読み取りは別オリジンからも読める）。**例外: `/api/designer/imports`** は Origin を `null` / figma.com に限定し、ペアリングコード（`X-Autona-Pairing` ヘッダ）が必須。
  `/api/designer/*` は Designer トークン（ループバック限定）とペアリングコードの独自ガードのままで、S3a の判定は通さない（DESIGNER）

## Helpers

- `Helpers/` 配下はユーティリティ。各ファイルは単一責務
- `AutonaQtHelper.py` の `GuiExecutor` はワーカースレッドから GUI 操作する際の橋渡し
- `AutonaQtHelper.HeldBackText` は、state を描画する入力欄の「描画が残した文字は書き戻さない、打った文字は書く」判定
  （選手カード・ボード 1 / 2 のチーム名・クルーボード・実況パネルが共有。ADR-005 S3e-2）。打ちかけの間にその欄自身の値が
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
  `Attach()` が `changed` → `SetupModel`（GUI スレッドで `db_updated`）を中継する（process 寿命の static クラスなので
  `tests/test_signal_lifetime.py` の購読許可リストに入っている）。選手データの読み書きは `PlayerDB.instance` を呼び、
  `AutonaPlayerDB` にデータの操作を足さない
