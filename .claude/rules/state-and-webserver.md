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
  対して呼ばない（用途は `AutonaActions.SeedFreshBoard` の先頭だけ。ADR-005 S4ac-1 までは
  `CreateScoreboard` が見せないボードのプレースホルダにも呼んでいた。起動時に作ったボードを消すので外した）
- **新しいボードの中身のシードは 1 つの操作 `AutonaActions.SeedFreshBoard(board)`**（ADR-005 S4t。どのスレッドでも・
  ボードの有無によらず動く）。1v1 / チームは `SeedBoard` → 既定のチーム色・空のチーム名・
  LOSERS → `SetRoster`（`DefaultPlayersPerTeam()` 人分。null / 数でないなら 1。足りないスロットを `PlayerService.SeedEmpty()` で埋める）→
  match バー → `best_of` 0 → スコア 0 を 1 サイクルで書き、クルーは `SeedBoard` + `CrewBattleService.SeedDefaults()`。
  **呼ぶのは両シェルで同じで、入口は `AutonaActions.EnsureBoard(board, panel=False)`**（ADR-005 S4y。ボードを 1 回だけ
  作る。2 回目と作っている最中の呼び出しは何もせず False。作ったかは `BoardEnsured(board)`（保存サイクルの書き込みの最後に、
  hold と同じ lock の保持の中で立つ）、忘れるのは `ResetBoards()`（`ResetScoreRoot` もここだけが呼ぶ）。作れなかった・
  描けなかったボードは `ForgetBoard(board)` がフラグを下ろして hold を外す（`StateManager.ReleaseSubtree`。保留中の値は公開する。
  呼ぶのは `EnsureBoard` の例外だけ。ADR-005 S4ac-3 まではデスクトップのウィジェットの構築の失敗でも、**その呼び出しが作ったボードだけ**を
  忘れていた（起動時に作ったボードは held のまま残す。S4ac-1））。
  `SeedFreshBoard` / `HoldBoard` / `ResetScoreRoot` / `ReleaseBoard` / `SeedBoard` / `ResetBoards` / `MakeBoards` の呼び出し元は
  `tests/test_headless_seed.py` が AST で pin）:
  **両シェルとも起動時に 3 つとも作る**（`AutonaActions.MakeBoards(panel)`。ADR-005 S4ac-1: 1 つの保存サイクルで `ResetBoards()` →
  `BOARDS` の各ボードに `EnsureBoard(n, panel)`。起動 1 回だけ。作成済みのボードが 1 つでもあれば警告して何も書かずに False。
  回数ではなくボードで決めるので、ハーネスがボードを落とした後は通る）。デスクトップは
  `bootstrap_gui()` が Window より前に `panel=True` で（ボード 1 / 2 は見せていない面も起動時から held。S4ac-3 までは
  スコアボード dock の直後で、形式のボードのウィジェットは後で `CreateScoreboard` → `EnsureScoreboard` が建てていた）、
  ヘッドレスは `src/headless._SeedBoards()` が `panel` なしで（held にしない: API の書き込みが反映待ちになるため）。
  S4ac-1 まではデスクトップがボードのウィジェットの構築のときに 1 つずつ作り、held もそのときだった。
  `tests/test_headless_seed.py` がボード 1 / 2 / 3 の全体をデスクトップと照合する（`MakeBoards(panel=True)` と `MakeBoards()` が
  表示トグルと取り直し以外で一致）。
  `panel=True`（デスクトップ）だけがする仕事は 3 つで、1 つの保存サイクルの中でシードの後に行う: 1v1 / チームの
  `schema.PublishDisplayOptions()`（全キーを設定から書き直す。ヘッドレスは `SeedPanelState` が欠けたキーを埋めるだけで、
  復元した値を変えない）と `SetFollowService.TurnOffPolling()`（`general.disable_autoupdate` の強制。起動のたびに（ボード 1 / 2 で 2 回。S4ac-1 までは
  ボード 1 / 2 の最初の構築のたび）。settings.json を読めなかった起動では SettingsManager が読み直してから書き、まだ開けなければ
  この起動の間だけメモリに持つ（下の SettingsManager の項。2026-11-11 まではここで書かずにいた）。ヘッドレスの取り直しの既定を変えない）と、最後に `schema.HoldBoard`（反映ボタン。シードより先に
  登録するとシードが公開されない。S4y まではボードの `__init__` がウィジェットの構築の後に登録していた）。
  ボードホストの `Built` / `Prepare` は ADR-005 S4ad で削除した（S4ac-3 からは常に真・何もしないだった。それまでデスクトップは `Built` でこのフラグを読み、
  `Prepare` は core に無いボード（起動時の作成の失敗・ハーネスが落としたボード）だけをウィジェットごと作り直していた。S4ac-1 の検収レビュー）
- `KEY_CATALOG` / `DYNAMIC_KEY_FAMILIES` は基本的に**列挙のみ**（マイグレーション層の
  アンカー）。新しい state キーを足したらここにも書く（テストで強制できないため
  ベストエフォート運用。**PR レビュー時に KEY_CATALOG との差分を確認する**）
- 例外は**静的化済みのキー名**: `SCORE_MATCH_FIELDS`（`phase`/`pool`/`match`）と
  `SCORE_TEAM_INPUT_KEYS`（`teamName`/`losers`）は Step 5 第 1 弾で、
  `PLAYER_INPUT_KEYS`（`team`/`name`/`real_name`/`twitter`/`custom_textbox`/
  `seed`/`birthday`/`country`/`state`）は第 2 弾で、
  `TOURNAMENT_INPUT_KEYS`（`tournamentName`/`eventName`/`address`/`numEntrants`/
  `startAt`）は第 3 弾で実際に消費されるようになった —
  `AutonaActions` / `PlayerService` / `TournamentInfoService` がこれらを書く。
  ADR-005 S4ac-3 までは Qt の `AutonaScoreboardWidget` / `AutonaScoreboardPlayerWidget` が `findChild` の
  **引数として**使っていた（objectName から作らない）。**文字列は保存済み state（`user_data/state.json`）のキーなので変えない**
  （.ui は S4ac-3 で削除した。`tests/test_objectname_keypath_contract.py` が当時の objectName の凍結した写し `EXPECTED` と
  schema の一致を pin する）。
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
- **settings.json を読めなかった起動**（開けない・JSON のオブジェクトでない）: **`SettingsManager.Initialize()` がその場で読み直す**（2026-11-11。値の読み手より前:
  ポート・言語・ホットキー・対戦形式は起動時に 1 回読まれる）。開けなければ 0.1 / 0.3 / 0.6 秒おきに読み直す（合計 1 秒弱。パースの失敗は待たない）。
  読めれば採用。壊れていれば `settings.json.unreadable-<日時>.bak` に `os.replace` で退避して既定（`{}`）を書く（両シェルとも起動時に退避する。退避先は
  `SettingsManager.movedAside`、`GET /api/v1/settings` の `movedAside` はファイル名だけ）。
  まだ開けなければ `settings` は `{}`・`loadFailed` は True のまま起動し、**書き込みのたびに SettingsManager が読み直す**（呼び手ごとに避けない）:
  読めれば採用し、開けない間の `Set` / `Unset` の変更をその上に順に戻してから書く。壊れていれば退避して既定（と開けない間の変更）の上に書く。
  まだ開けなければ `SetMany` / `PrepareWrite` は `SettingsUnreadable`（`OSError`）を送出して何も変えない（厳格）。`Set` / `Unset` はメモリだけを変え
  （この起動の間だけ有効・保存しない。warning）、変えたキーを記録する（同じキーとその下は後の変更が置き換える）。
  `loadFailed` は途中で False になりうるので、読めなかった間の判断をキャッシュしない（`WebAccess.Token()` は `""` を持たない）。
  読み込みは先頭の UTF-8 BOM を外してから（古いメモ帳の保存）。保存は BOM なし。`Unset` は無いキーなら保存せず、途中の空の親も作らない
- **settings の書き込みは 2 種類に分けて考える**（2026-11-11）:
  - **書く値を操作が決める**（利用者の選択・state のゲーム・固定の `True`）: `Set` / `Unset` だけでよい。`Set` が読み直し、まだ開けなければこの起動の間だけ持って、
    読めたら戻す。lock の外の比較で同じ値を書かないだけの呼び手（`LayoutCatalog.SelectLayout`・`AutonaActions.SetBattleFormat` の早期 return）もこちら:
    書かなかった値はファイルの値を上書きしない
  - **書く値か、書くかどうかを保存済みの値から決める**（変えて書く一覧・比べて書くもの・「無ければ既定を書く」）: `with SettingsManager.lock:` の最初の文で
    `SettingsManager.PrepareWrite()` を呼ぶ（呼ばないと、読み直す前の空の代わりから組んだ値で、採用したファイルのそのキーを上書きする）か、
    `loadFailed` の間は書かない（初回起動の LAN の確認。`src/Autona.py` の `_StartWebServer`。答えを空の代わりの「未決定」で決めない）。
    「あれば消す」は `Unset` 自身が今の中身で決めるので、呼び手は `Get` で確かめない（`ColorService._ClearFeatureColor`）
  - `PrepareWrite` の実例: `AppSettings` / `HotkeyRules`（`try` で `SettingsUnreadable` → Refusal `NOT_READY`）・`StreamService` のチャンネルと配信サムネのオプション・
    `ThumbnailService` の `Update` / `ImportBackground` / `Reset`・`ResultService.SetLayout`・`AutonaDesignerService` のペアリングコード。core はまだ開けなければ
    `SettingsUnreadable` を送出して何も変えず、v1 の `write_op` が 409 `NOT_READY` に写す（ほかの仕事の途中で呼ぶ操作は送出せず、捕まえて warning:
    `ResetOrgAssets` の source（画像の削除の後）・`FollowBattleFormat`（形式の切り替えの最後））。`PrepareWrite` が読み直した中身の保存に失敗したときも
    `SettingsUnreadable`
  - `tests/test_settings_manager_load_failed.py` が AST で「settings の lock の中で書く（同じモジュールの書く関数経由も含む）ブロックは最初の文が
    `PrepareWrite`」と、LAN の確認の条件に `loadFailed` があることを pin する（lock の外で読んだ値から決める形・別モジュールの書き手を lock の中から呼ぶ形は
    拾わない。台帳の残置）

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
  ロゴを 1 保存サイクルで入れ替え（S4ac-4 まではゲームトラッカーの勝敗も鏡映で）、`score.{n}.teamsSwapped` を
  反転。チームカラーは入れ替えない。board 3 は False を返し、呼び出し側が `CrewBattleService.SwapTeams` を
  呼ぶ（ADR-005 S3e-2。それまではクルーウィジェットに委ねていた））/ `SwapPlayers`（同じチームの 2 スロットを入れ替え。カードの上下移動）/
  **ADR-005 S2d で追加**: `ClearScore`（スコア 0・LOSERS False。match バーは残す = セット読込の前処理）/
  `ResetMatch`（`ClearScore` + `ClearMatchInfo`）/ `ClearPlayers(board, no_mains)`（両チームの全スロットを
  旧カードの `Clear()` と同じ内容で空にする。birthday・city・mains は残る）/ `ClearBoard`（すべての入力値をリセット =
  選手 + `ResetMatch` + チーム名・ロゴ）。いずれも board 1,2 だけで 1 保存サイクル、board 3 は False
- **クリアは state の書き換え**（S2d）。旧実装はボードのウィジェットを空に戻していた（カード 1 枚ごとに 1 サイクル）。
  操作の周りに残るのは、追跡中セットを忘れること（同じセットの再読込が効くように。ボードホストの `ClearPlayers`）。
  入力中の欄の確定（Qt の `AutonaScoreboardWidget._ClearingPlayers` の `CommitFocusedEdit`）は S4ac-3 で Qt の SCORE ごと無くなった。id が変わったチームの統計の取り直しは操作の仕事
  （`ClearPlayers` → `StatsService.TeamsChanged`。ADR-005 S3e-5。それまではボードが出していた）。
  Web の `clear_all` は `ClearBoard` ではなく（`ClearBoard` は v1 の `:reset-all` が呼ぶ。ADR-005 S4u）、旧順序（`ResetMatch` → best_of → 人数・キャラ数 → 選手）で呼ぶ
  （チーム名を残し、人数・キャラ数の変更が mains を決める順を保つ。ADR-005 S2 判断 3）
- **スワップは state の入れ替え**（S2c）。旧実装は選手カード間の値コピー（`SwapWith`、削除済み）で、
  キャラのバリアントと mains を落とし、ゲームトラッカー ON で勝敗の並びを崩していた（トラッカーは S4ac-4 で削除）。
  スロットは**キー単位**で入れ替える（両方にあるキーは各 dict 内の位置を保つ = `program_state.json` /
  Socket.IO delta のキー順を旧実装と一致させる。片側にしかないキーは `SLOT_DEFAULTS`、`id` / `city` は
  「None のために作らない」、seed は旧 `SetSeed` と同じく常に int で両側に書く）。チーム入替は両チームにある
  スロットだけを入れ替える（片側だけのスロットは動かさない）。キー順は 6a6f04cc で録画した全 dict の挿入順
  （`tests/fixtures/board_operations/key_order.json`）と照合する（`tests/test_board_operations.py`。
  値が意図して変わったステップも含む。S4ac-3 で Qt のボードではなく core の操作を録画どおりに駆動する形に書き直した）。
  **`teamsSwapped` の正本は state**（ボード属性は廃止）。`SetLoadService.DisplayTeam` と
  `AutonaWebOperations.get_swap` は state を読む。SCORE ページは `GET .../pending` と通知で追従する
  （S4ac-3 までは Qt のボードが held サイクルからの描画で追従していた）。統計の再取得は操作が出す（`SwapTeams` / `SwapPlayers` → `StatsService.TeamsChanged`。
  イベントループに戻ってから 1 回。ADR-005 S3e-5。それまではボードが held サイクルでフラグの反転を検知して出していた）。
  アップセット係数は操作が core 内で再計算する。
  入力中の欄を入替・移動の前に確定する仕事（`AutonaScoreboardWidget.CommitFocusedEdit`。ボードホストの `CommitEdit`）は
  Qt のボードにだけあり、S4ac-3 で無くなった（SCORE ページは欄を確定してから依頼する）。空になった `CommitEdit` は S4ad で削除した
- **クルーボード（board 3）は `CrewBattleService` が書き手**（Step 5 第 4 弾）。
  ただし *入口* のうち board 1/2 と語義が同じもの — チーム名（`SetTeamName`）・
  チームカラー（`SetTeamColor`）・match バーの 3 combo（`SetMatchField`）— は
  `AutonaActions` を呼ぶ。`AdjustScore` / `ResetScore` は board 3 では今も no-op で、
  クルーの操作（ストック・出場中のメンバー・人数・スワップ・ストックのリセット・クリア・セットデータ）は
  `CrewBattleService` の操作（ADR-005 S3e-2）。Web（`AutonaWebOperations`）が呼び、
  操作は state から読んで書く。ヘッドレスでも動く。
  Qt のクルーボード（`AutonaCrewBattleWidget`。入力欄が同じ操作を呼び、state を描画していた）は S4ac-3 で削除した。
  スワップのフラグは board 1/2 と同じ `score.3.teamsSwapped`（ADR-005 S4w。ページが delta で追従するため。それまでは state の外で通知が無かった。
  シードは書かず、無ければ false。`SeedFreshBoard` の `SeedBoard` と起動時の `ResetScoreRoot` で消える）。
  v1 は Qt のボードの入力欄と同じ操作を呼ぶ（ADR-005 S4w。`external-control-api.md` §2.12）: 1 メンバーの `UpdateMember`（名前・ストック・出場中を 1 回の lock と 1 保存サイクルで）・
  `AdjustMemberStock`、`AutonaWebOperations.crew_update_set`（`PATCH set`。legacy の `set_route` は通らない: チーム名を書かず値を丸める）・`crew_team_color`。
  **同じ値は書かない**（`_RepublishBoard` は変わらないボードを書かない）。書き込みはボードホストの `Run` の中（その前の `Prepare` は S4ac-3 から何もせず、S4ad で削除。それまではデスクトップで core に無いボードだけを作り直した）。
  リセット 3 本の `AutonaApiV1._crew_not_built`（`Built` が常に真なので S4ac-3 から常に偽）も S4ad で削除した。作成済みのまっさらなボードには何も書かない
  （`ClearMatchInfo` は match バーの欄をボードにあるものだけ空にする。クルーのシードに `pool` は無い。S4ac-1 の検収レビュー）。読み取りの X 投稿文は `PostText`（SCORE ページのボタンも同じ v1 を呼ぶ）。
  ボード 1 / 2 の X 投稿文は `AutonaActions.PostText(board)`（ADR-005 S4x。`(text, missing)` を lock の 1 回の保持で読む。
  SCORE ページのボタンは v1 でこれを読んで、`missing` があれば断り、無ければサムネイルを作ってから X を開く。Qt のボタンは S4ac-3 で削除）。
  v1 は `GET /scoreboards/{n}/post`（`get_board_post`。3 ボードとも）で、反映前の値と選手の X ハンドルを返すので PLAYERDB
- **対戦形式の切り替えは `AutonaActions.SetBattleFormat(fmt)`**（ADR-005 S4x。それまでは `Window.SetBattleFormat`）。
  サイドバーと v1 の `PUT /battle-format` がどちらも `AutonaWebOperations.set_battle_format`（ボードホストの `Run` の中）を通して呼ぶ（サイドバーは ADR-005 S4ad から。それまでは core を直接呼び、GUI スレッドで Web の操作と分かれていた）。
  いまの形式（`ResolveBattleFormat`）と同じなら何も書かず何も知らせずに False。変わるときは設定 2 つ
  （`scoreboard.battle_format` と旧 `team_mode`）→ `AutonaActions.battleFormatChanges`（クラスの `ObserverChannel`。
  Bridge が `battle_format_changed` に変換し、`Window._ShowBattleFormat` がサイドバーを合わせる（S4ac-3 までは Qt のボードの表示も。通知の値ではなく、その時点の形式 `_resolveBattleFormat()` を描く: v1 の切り替えの通知は queued で遅れて届き、その間にサイドバーで切り替わりうる。ADR-005 S4ad）。
  両シェルの `WebServer.on_battle_format_change` が Socket.IO の `battle_format` にする（ADR-005 S4z。React の SCORE ページが形式のボードを出すため。S4x は「設定なので通知しない」だった））→ `ResultService.FollowBattleFormat` の順。通知は切り替えたスレッドで出る（v1 はリクエストのスレッド、サイドバーは
  GUI スレッド。S4ad までデスクトップの v1 も GUI スレッドだった）。起動時に建てるボードのウィジェットは S4ac-3 で無くなった（それまで形式の決定は Window。S4y の範囲）
- **best_of は state 主導**（ADR-005 S2e）。`SetBestOf(board, n)`（board 1,2。board 3 は False で、
  クルーは `CrewBattleService`）が 6 キーを 1 `SaveBlock` で書く（ゲームトラッカーの行も同じブロックで作り直していた。
  ADR-005 S4ac-4 でトラッカーごと削除）。
  0〜99 に丸め、値が変わらなければ何も書かない（旧 `valueChanged` と同じ）。
  ゲームのロードは `RepublishBestOfs()`（シードされた全ボードで文言を作り直す。変わらなければ書かない。呼ぶのは
  `bootstrap.wire_game_roster` で、両シェルが同じ。ADR-005 S4t）。反映まで済ませるのは `CommitGameLoad(written)` で、
  ADR-005 S4ad から `bootstrap.wire_game_roster` がロスター・`RepublishBestOfs` と同じ保存ブロックで、ボードホストの `Run` の中で呼ぶ
  （両シェル同じ。ヘッドレスは held が無いので何も起きない。ロスターか `RepublishBestOfs` が例外を出しても `finally` で必ず呼ぶ）。
  ロードの commit は `CommitGameLoad(written)` で、**ロードが書いたキーだけ**を公開する（2026-11-12。`StateManager.RecordHeldWrites()` が
  そのスレッドの held の書き込みを記録し、`CommitHeld(prefix, written)` がそれだけを出す。デスクトップの `_Publish` が `gameLoaded` の emit からサイクルを
  閉じるまでを記録し、結線の記録はそれに合流する。Qt モデルの整理 PR4 まではモデルの作り直しから）。ロードの前からの未反映・ローダーが `SelectGame` している間のボードの操作・core 直結の
  書き込み（`score:adjust` など）は反映待ちのまま残り、ロードが書いたキーでもほかの書き手の未反映と重なる（同じ・祖先・子孫）ものは残る
  （例: 反映していない BEST OF から作り直した文言）。ただし操作が `StateManager.HeldWriteGroup(authoritative=True)` の中で書いた
  「誰の編集からも作っていない固定の値」（`SetRoster` が落とす選手枠・ゲーム切替の空のキャラと mains・空の選手枠のシード・空のチーム名）は、
  それより前のほかの書き手の未反映（同じキーか子孫。祖先は除く）を上書きしたものとして重なっても出し、上書きされた未反映は反映待ちから外す。
  held の書き込みは値が変わらなくても記録されるので、そういう値はライブがすでにその値でも、公開済み（`GetCommitted`）と違うか、そのキー以下に未反映（`StateManager.HasPendingAt`）があれば書く（`AutonaActions._SetDecided` / `_TrimPublished` / `_NothingToEmpty`。「同じなら書かない」で飛ばすと、反映していない編集が先にライブをその値にしたとき、ロードの commit が出さず公開済みの値が残る）。これはロードの記録の中（`StateManager.Recording()`）でだけ行い、ロードの外の `SetRoster` は今までどおり同じ値を書かない（反映が未反映を全部出すので書く理由が無く、書けば書き込みの列だけが変わる。`tests/test_set_follow_flow.py` の golden が見る）。
  `HeldWriteGroup` の中で書いたキーは 1 グループとして一緒に出るか一緒に残る（1 人の `character` と `mains`:
  `PlayerService.PublishCharacterSlots`）。判定はキーの位置とスレッドだけで、値の出どころは見ない。保存サイクルの中の commit は積むだけで、出すキーはサイクルを閉じるとき（`onLoad` の後）に
  決まるので、`onLoad` のスロットが書いたものも一緒に出る（それまではデスクトップのアダプタの `_Publish` が `onLoad` の後に呼んでいた。
  Qt のカードの作り直しが onLoad の中で書いていたため。カードは S4ac-3 で消えた）。デスクトップの `_Publish` は全体がボードホストの `Run` の中
  （S4ad のレビュー）。held を「書いた分だけ」公開する操作を新しく足すなら `RecordHeldWrites` + `CommitHeld(prefix, written)` を使う
  （`ApplyChanges` は全部を出す）。
  Qt のスピンボックスと行の描画（held サイクルの `_SyncBestOf`）は S4ac-3 で Qt の SCORE ごと削除した。
  入口はすべてこの操作: SCORE ページ（v1 `PATCH /scoreboards/{n}/match`）・セット読込（`SetLoadService.BestOfFromSetData`。
  0・空は運ばない）・Web `set_route` の bestOf（v1 `PATCH set` 含む）・`AutonaWebOperations._reset_match`・`ClearBoard`（最後に 0）
- **ローカライズ文字列は callable で注入**する。`best_of_text` / `first_to_text` は
  `AutonaLocaleHelper.FormatMatchText` を `bootstrap_core()` が `AutonaActions` に渡す
  （`TournamentInfoService.formatEventDate` と同じ形）。未注入時と、注入関数が例外を出したとき
  （ロケール未ロードの空の表・`.format()` が通らない独自用語）は英語既定にフォールバックする
  （後者は警告を出す。`SetBestOf` は入口から直接呼ばれるので、例外を上げるとセット読込の残りや
  ゲームのロードの `CommitHeld` が止まる。ADR-005 S2e）。int にできない best_of は警告して何も書かない
- `score.{n}.team.{t}.score` の書き手は **core 内に 2 系統**（`AutonaActions`、
  セット読込の `SetLoadService.ApplyScores`。3 系統目だったステージ集計の `GameTrackerService.PublishStageResults` は
  ADR-005 S4ac-4 でトラッカーごと削除。`AutonaActions` の書き手は `SetScore` / `AdjustScore` / `ResetScore` と、S2c からの `SwapTeams`、S2d からのクリア）。
  **スコア欄への数値の手入力**は v1 の `score:set` = `AutonaActions.SetScore`（ADR-005 S4x。Qt の `scoreDisplay1` / `scoreDisplay2` の
  `valueChanged` も同じ操作を呼んでいた（S4ac-3 で削除）。それまでは `GameTrackerService.UpdateScore` を呼んでおり、行の追従と 2 サイクルに分かれていた。
  `UpdateScore` は削除した。`tests/test_score_typing.py` が操作・デスクトップの route・ヘッドレスの route の書き込みを照合する）。
  **core の外から直接 `Set` する経路は残っていない**（`StageResultsToScore` の
  再 Set は検収レビューで削除済み。表示更新のみ）。
  スコアへのゲームの行の追従（ADR-005 S4t からスコアを書いた操作自身が core で `Scores()` → 書き込み →
  `FollowScores(before)`）は S4ac-4 でトラッカーごと無くなった。スコアの操作は追従の分のサイクルを持たない
- 呼び出し側は `AutonaWebOperations`（Web の操作。ADR-005 S3c。React の SCORE ページは v1 経由）・`AutonaHotkeys`・Qt のサイドバー（形式の切り替え）。
  Qt の SCORE のウィジェットは S4ac-3 で削除した。
  **どのスレッドから呼んでもよい**（Qt に触れないのが層の定義）。core 直結の Web 操作は呼び出し元
  （Flask / Socket.IO）のスレッドで core を呼び、`@gui_thread_sync` を持たない。
  S2 で core 化した操作（`swap_teams`・S2d のクリア `reset_match` / `reset_players` / `clear_all`・`get_set`・
  `get_swap`・`set_route` の phase / pool / match / losers / teamName / bestOf・`set_team_losers`・`reset_scores`）と、
  S3e-4 で移した選手の Web 操作（`update_player` / `set_team_data` / `load_player_from_tag`。`PlayerService.ApplyData`）と、
  S3e-5 で移した統計（`stats_*` 4 本・`refresh_stats`。`StatsService`）と、
  S3e-7 で移したセット読込と追従（`load_set` / `post_score` / `pull_stream_set`。`SetFollowService`）は
  どちらのシェルでも core を呼び、**その周りのボードの仕事はボードホスト（`AutonaWebOperations.boardHost`）が受け持つ**。
  **ボードホストは両シェルで同じ `BoardHost`**（ADR-005 S4ad。デスクトップがシェルとして登録するものは無い）:
  - 呼び出し元のスレッド（Flask / Socket.IO のリクエスト、ホットキー・サイドバー・セットの追従の答えと時計・ゲームのロード
    （デスクトップは `_Publish` 全体。結線のロスターと commit はその中）ならメインスレッド、終了中のロードの後始末ならローダーのスレッド）で
    core を呼ぶ。1 本の再入可能ロック（`RLock`）で 1 つずつ
    実行する（`clear_all` や v1 `PATCH set` の途中に別の要求・ホットキー・追従の書き込みが混ざらないように）。公平ではない
    （RLock は FIFO でないので、待っているメインスレッドをリクエストのスレッドが追い越しうる。操作は短いので受容。S4ad のレビュー）。
    **終了の開始からはメインスレッド以外のボードの操作を断る**（`Run` のいちばん外側の呼び出しで `gui_unavailable_reason()` を見て、
    何も書かずに `NotAvailable(SHUTTING_DOWN_MESSAGE)` = 503。INFO のログ。両シェル同じ。S4ad までデスクトップのホストが GUI スレッドを
    待てなくて返していた 503 を、終了のフラグで保つ）。断らないのはメインスレッドの操作・走っている操作の中の入れ子・終了中のロードの
    後始末（`Run(op, atShutdown=True)`: `_SettleUnannounced` / ヘッドレスの `_SettleStopped`）。QCoreApplication が無いときも断らない
    **ロックの中でメインスレッドを待たない・`StateManager.lock` を持ったまま取らない**（いちばん外側のロック）。待つもの
    （`savePlayerToDb` の選手 DB への書き込み。`PlayerDB.changed` → `AutonaPlayerDB.SetupModel` はメインスレッドで走る。
    ADR-005 S3f-2 から待たないが、外に出したまま）は `Run` を抜けてから行う（S3e-4 レビュー）。新しいボードの操作は
    `boardHost.Run` を通す（core 直結の 1 回の呼び出し（`score:adjust` など）は通さなくてよい: 1 つの操作は自分の保存ブロックと
    `StateManager.lock` で原子的）
  - ボード 1〜3 以外（どのシェルにも無いボード）は操作自身が両シェルとも `NotAvailable`（503）で断る
    （ADR-005 S4x でボードホストの `Widget()` を削除した。それまではそこで 503、デスクトップは 500）。
    選手のクリア（`ClearPlayers`: `AutonaActions.ClearPlayers` → 最後に読んだセットを忘れる）と最後に読んだセット（`LastSet`。
    `SetLoadService`）は ADR-005 S4x からホストの 1 つ（それまではボードの `CommandClearAll` と属性。`get_set` もボードを構築しない）。
    キャラ枠の無いボードが次の選手に渡すキャラ数の記憶も core（`AutonaActions.EmptyRosterCharacters`。Qt のボードの隠れたスピンボックス `charNumber` が描画していた。S4ac-3 で削除）。
    ボード 1 / 2 の人数 / キャラ数（`set_route` の `players` / `characters`・`clear_all`）とゲームのロードの既定人数も、
    S3e-6 から両シェルで同じ core の操作（`AutonaActions.SetRoster`。ゲームのロードは `bootstrap.wire_game_roster`）。統計は両シェルとも
    core が取り直す（ADR-005 S3e-5）
  - デスクトップのボード 1 / 2 は held（反映待ち）、ヘッドレスは即時公開（反映ボタンが無いのでこれが正しい）。違いはそこだけ
  - 経緯: S4ad まではデスクトップが `DesktopBoardHost`（`bootstrap_gui()` が登録）で操作を GUI スレッドで同期実行していた。
    理由は Qt の選手カードの入力確定（GUI スレッド上で「フィールド → 合成名」の 2 書き込みに分かれ、その間に割り込むと
    合成名が反対のスロットに残る。S3e-4 で見直して維持した）で、カードは S4ac-3 で無くなった。フック（`Prepare` / `Built` /
    `CommitEdit` / `DiscardTypedText` / `PlayerLoaded`）は S4ac-3 から何もせず、S4ad でデスクトップのホストと一緒に削除した。
    それまではデスクトップが core に無いボードを `EnsureScoreboard` でウィジェットごと作り直し、ウィジェットがあるときだけ
    欄の確定・コンボの打ちかけの文字の破棄をしていた（S4ac-1 までは未構築のボードを構築してから書いた: 未構築ボードは
    held でなく、書き込みが即時公開されたあと構築時のシードで消えたため）
- **SaveBlock は複数キーを 1 サイクルにまとめる時だけ**使う（単一キーなら不要）。
  かつては「空 `changedKeys` の SaveState = `include_paths=[]` = フィルタなし =
  全 state 差分」という地雷があったが、**Step 4a で `SaveState` 側が空 `changedKeys`
  を早期 return するようになった**ので、包んでも全量 diff にはならない
  （`tests/test_state_manager_held.py` が DeepDiff 呼び出し回数で pin）
- ウィジェット側は通知（`held_state_updated` / `state_updated`）で表示を更新する。
  プログラム的更新は必ず `QSignalBlocker` で囲む（囲まないと `valueChanged` 等が
  発火して書き戻し = エコーループ）。実例だった Qt の SCORE（`AutonaScoreboardWidget._SyncScoreDisplays` /
  再入ガードの `_RenderPlayerCards`）は S4ac-3 で削除した。state を描く Qt のウィジェットはほぼ残っていない
- ホットキーは `AutonaHotkeys.ConnectActions()` が `bootstrap_gui()` から 1 回だけ結線する（ADR-005 S3c でデスクトップ側の段へ移動。ヘッドレスにはホットキーが無い）。
  対象ボードは `AutonaActions.ActiveBoard()`（`SettingsManager.scoreboard.battle_format`）。
  `swap_teams` も S2c からここ（旧: 構築済みの全ボードに接続しており、形式切替後は両ボードが入れ替わった）。
  ボードのホットキー（スコア ±1・`reset_scores`・`apply_changes`（反映）・`swap_teams`）は GUI スレッドでボードホストの `Run` の中から
  core を呼び、対象ボード（`ActiveBoard()`）もその中で読む（`AutonaHotkeys._runOnActiveBoard`。ADR-005 S4ad でスワップ、そのレビューで
  残りも。Web の操作がリクエストのスレッドで走るようになったので、同じロックで直列にする。v1 の `:apply` はボードの操作ではなく、
  S4u からロックの外のまま）。未構築判定と入力中の欄の確定（`boardHost.Built` / `CommitEdit`。S4y でボードの
  ウィジェットから移し、S4ac-3 から素通り）は S4ad で削除した（S4ac-1 までは未構築のボードでは何もしなかった: 未構築ボードは
  held でないので、入れ替えると即時公開され、構築時のシードで消えた）。
  パネル操作の `load_set` も S4y から `ConnectActions()` が結線し、アクティブ
  ボード（1 / 2）のセット選択をシェルのアダプタ `AutonaShellOperations.ShowSetSelection` で開く（クルー形式なら何もしない。
  それまでは構築済みの 1v1 / チームの全ボードが自分のウィンドウを開いていた）。メインウィンドウを前に出して SCORE に切り替え、
  ページのダイアログを開く（ADR-005 S4ab-2。`Window.OpenScoreSets` → 共有ビューの `autonaPanelShell.openScoreSets`）。
  メインウィンドウができる前はログだけで何も開かない。Qt のセット選択ウィンドウと退路の設定 `general.qt_scoreboard_panel` は S4ac-3 で削除した。
  ページへの経路の例外はログだけで、ホットキーのハンドラに漏らさない。
  ボードに残るホットキーは無い（`refresh_phase_group` は ADR-005 S3e-8 から
  `ConnectActions()` が `Top8Service.RefreshFromProvider` に直結する。それまでは Window が Top 8 パネルを構築して呼んでいた）
- **デスクトップのボード 1 / 2 は起動時から held**（ADR-005 S4ac-1。held 登録は `AutonaActions.EnsureBoard(panel=True)` の
  `StateSchema.HoldBoard` だけで、`bootstrap_gui()` の `MakeBoards(panel=True)` が起動時に呼ぶ。S4y まではボードの `__init__`、
  S4ac-1 まではボードのウィジェットの構築）。見せていない面への core 直結の書き込み（`score:adjust` / `score:set` /
  `score:reset` / チームカラー・スワップのホットキー）も反映待ちになり、React の SCORE ページの反映（もう一方のボードの
  未反映も出す）で公開する。ヘッドレスは held にしない（即時公開）。未反映が残る間は保存サイクルごとに `SaveState` が
  state 全体を複製する（実測 1 回約 0.27 ms。台帳 S4ac-1 の残置 #2。表示中のボードの未反映でも元から同じ）。
  起動時から held にする案（ADR-005 S2 判断 7）は S2c で一度撤回した（Web UI に反映ボタンが無く、デスクトップの反映は
  表示中のボードだけなので、形式と食い違う面への書き込みがどこにも出ないまま、構築時の `SeedBoard` で消えた）。React の
  反映ボタン（S4aa）の後に入れた（`docs/changes/architecture-modernization.md` の順序 6 の判断 2・S4ac-1 の節）。
  上の「未構築」の記述（`swap_teams` / `load_set` の判定・`Prepare`）は、S4ac-3 からどのシェルでも当たらず
  （`Built` は常に真、`Prepare` は何もしない。S4ac-3 まではデスクトップのボードの作成が失敗したときだけ当たった）、S4ad で判定ごと削除した

## ドメインサービス（core の per-board オブジェクト）

`AutonaActions` が「1 回の操作 = 1 メソッド」の入口なのに対し、状態を持つ／ボードごとに
インスタンスが要るドメインロジックは core のサービスに置く（headless core Step 4a）。
**Qt 側は薄いアダプタに徹する**（`AutonaStatsUtil` が実例。`AutonaIndividualGameTracker` も実例だったが S4ac-3 で Qt の SCORE ごと削除）。

| サービス | 所有者 | 責務 |
|---------|--------|------|
| `src/core/StatsService.py` | なし（`StatsService(n)` を呼ぶたびに作る。取得の `fetcher`（`AutonaStatsUtil`。`bootstrap_core()` が登録）と要求番号はクラスの持ち物） | 統計のすべて（ADR-005 S3e-5）: いつ取り直すか（`PlayerChanged` = 選手の読込・クリア・カードの手入力の id / seed の変化。`PlayerService.Announce` が書き込みの保存ブロックの中で呼ぶ / `TeamsChanged` = スワップ・上下移動・クリア。`AutonaActions` が操作の後に呼び、`fetcher.Later` でイベントループに戻ってからボードごとに 1 回 / `Refresh` / `RefreshAll` = Web のルート。統計情報ページ（React の `StatsPage`。ADR-005 S4k）の「統計情報を取得する」は v1 の `POST /api/v1/scoreboards/1/stats:refresh`）・取得可否の判断・アップセット計算・`last_sets`/`history_sets`/`recent_sets`/`upset_factor` の書き込み。人数は state のスロット数（0 なら `DefaultPlayersPerTeam()`）。**遅れた答えは捨てる**: 要求ごとに（ボード, 統計, チーム）の番号を `StateManager.lock` の中で振り、答えはメインスレッドで、番号が最新かつスロットが要求したときの選手のままのときだけ書く。番号は 1 つのカウンタから出す（キーをまたいで重ならない）。取得の開始はロックの外。**取得の失敗は統計ごとにログに残して次へ進む**（`RefreshQuietly`。いくつも依頼する入口と Web のルートはこれを呼び、ルートは `"OK"` のまま。旧実装は統計ごとの Qt シグナルだった）。ボードの構築・シェルによらず動く |
| `src/core/SetFollowService.py` | なし（`SetFollowService(n)` を呼ぶたびに作る。何を追従しているか・ステーション・要求番号はボード番号ごとにクラスが持つ。取得と 5 秒の時計の `fetcher`（`AutonaSetFollowUtil`）とボードホストの引き方 `boardHost` は `bootstrap_core()` が登録） | セット読込と追従のすべて（ADR-005 S3e-7。それまではボードのウィジェット・2 つの QTimer・provider アダプタの Worker）: `LoadSet`（Web の `load_set`。SCORE ページのセット選択のダイアログは v1 の `:load-set` で同じ操作。S4ac-3 まではセット選択ウィンドウも。クリア → 追従の記録 → 取得 → 答えを `UpdateSetData`。**大会が読み込まれていなければ何もしない**＝ボードを消さない）・`FollowStation`（v1 の `:follow` のステーション / 配信。S4ac-3 まではステーションウィンドウも）・`PullStream`（Web の `pull_stream_set`。**追従中のチャンネルなら照会し直すだけ**で、セットが変わったときだけ読み直す。Web UI の自動更新スイッチが数秒ごとに送るので、毎回追従し直すとクリア → スコア 0 になる。追従を始める最初の 1 回も、最後に読んだセットと同じならクリアしない）・`StopFollowing`（v1 の `:stop-follow`。S4ac-3 まではタイマー行のキャンセルも）・`Tick`（5 秒ごとの取り直し。`general.disable_autoupdate` が明示的に `false` のときだけ。デスクトップは起動のたびに `true` にする（`MakeBoards(panel=True)` → `TurnOffPolling`。ADR-005 S4ac-1。それまではボード 1 / 2 の構築のたび。settings.json を読めなかった起動では SettingsManager が読み直してから書き、まだ開けなければこの起動の間だけメモリに持って、読めたら戻す）ので、設定ページの「Disable automatic set updating」を外すと次の起動まで取り直す）・`ApplySetPayload`（旧 `ChangeSetData`。取得したセットと `POST /score` の 1 保存サイクル）。**判定と書き込みはすべてボードホストの `Run` の中**（答えの照会番号・要求番号の判定もその先頭で行うので、判定の直後に別スレッドの Web 操作が割り込まない）。取得の答えはメインスレッドに届く（`fetcher` の `Signal(object)`。旧 Worker 自身のシグナルは GC で答えが消えることがあった）。**遅れた答えは捨てる**: 読んだセットでない答え（`IsStaleUpdate`）・後から全読込が始まった取り直し（要求番号）・手動 / Web の読込（読み込んだときだけ）・別のステーション・キャンセルの後に返った照会と、後から出した照会が先に答えた照会（照会番号。pull と tick で 1 列）。**全読込の取得が失敗したら（セットの無い答え・例外・取得できない）、最後に読んだセットを忘れる**（ボードはクリア済みで何も出ていない。次の pull・照会・tick・同じセットの選び直しで読み直す）。ステーションは先頭のセットだけを読み、後続セットは取得しない（ADR-005 S3f-3。表示する箇所が無く、`score.{n}.station_queue` は `{}` のまま）。ロックの中で取得を待たない（pitfalls #17）。1 人分の `POST /score`（`team` / `player`）は state に無いスロット（"0"・負の数を含む）を書かずにログに残す。**何を追従しているかは state に入れない**（`tests/test_set_follow_flow.py` の golden が全ての書き込みを照合し、`program_state.json` のキーも増えるため）。読むのは `Snapshot()`（`{station, poll: {mode, identifier, secondsLeft}, pollingEnabled, setId}`。どのスレッドでも。v1 の `GET /scoreboards/{n}/follow`）、変わったことを知るのはクラスのチャンネル `changes`（`ObserverChannel`。引数はボード番号。ホストの中で出るので `StateManager.lock` の中でありうる。受け手は積むだけにする。`WebServer.on_follow_change` が `scoreboard_follow` にする。**変わったときだけ出す**: ステーションか取り直しの時計が変わったとき（止めるものが無いキャンセル・同じステーションの選び直しでは出ない）と、ボードに書かずに `setId` が null に戻ったとき（`_LoadFailed`・`_Follow` の読み直し）。`fetcher.FollowChanged` は受け手だった Qt のタイマー行（S4ac-3 で削除）が無くなったので S4ad で削除した）。`fetcher.RemainingMs` は次の tick の時刻（`time.monotonic`）から計算するので**どのスレッドからでも呼べる**（ADR-005 S4v。`QTimer.remainingTime()` はイベントディスパッチャの無いスレッドから呼ぶとプロセスが落ちる。実測）。その時刻は別スレッドの `Poll` がその場で記録する（ヘッドレスの読み込みはリクエストのスレッドで走り、タイマーの開始はメインスレッドへ queued なので、先に出る通知の読み手に `secondsLeft: null` を見せない）。v1 の入口は `:follow` / `:stop-follow`（`AutonaWebOperations.follow_station` / `stop_follow`。`FollowStation` / `StopFollowing(forget=True)` を呼ぶ（S4ac-3 まではステーションウィンドウの入口と同じ）。どちらもボードホストの `Run` の中（`:follow` の前の `Prepare` と、`:stop-follow` の「core に無いボード（`BoardHost.Built` が偽）なら作りも書きもしない」は、S4ac-3 から何もせず S4ad で削除した）。書き込みは `SetIfChanged` なので、何も追従していないボードには何も書かない（反映ボタンを点けない）。どちらもウィジェットを建てない） |
| `src/core/SetLoadService.py` | なし（`SetLoadService(n)` を呼ぶたびに作る。`SetFollowService`・ボードホスト・Web の操作が使う。S4ac-3 までは Qt のボードの `setLoad` も） | セット読込の正規化（ラウンド/フェーズ/プール・スコア・敗者フラグ・チーム名とロゴ・`bracket_type`・追従中セット）。最後に読んだセット `lastSetSelected` も持つ（state の外。ADR-005 S2d でボード属性から移動。S3e-7 からボード番号ごとにクラスが持つので、ボードのインスタンスでもヘッドレスでも同じ値を読む。クリアと `ClearSetTracking()` で忘れるので、同じセットを選び直すと再読込される。`get_set` と stale 判定が読む）。スワップ後の面の解決は `DisplayTeam()` が唯一の正本（`score.{n}.teamsSwapped` を読む。S2c までボード属性を callable で注入していた） |
| `src/core/TournamentInfoService.py` | `TournamentInfoService.instance`（`bootstrap_core()` 生成） | `tournamentInfo.*` の書き込み全部（provider ペイロードの適用・**大会情報ページの入力欄の `Set{Text,Number,Date}Field`**（`PATCH /api/v1/tournament/info`。日付は `SetDate`）・表示大会名の 3 系統の上書き規則・起動時シード（`logoVersion` / `bannerVersion` の publish と `media` の idle 戻し）。日付は整形済みの `startAt` と並べて `startAtDate`（`YYYY-MM-DD`）を同じ保存サイクルで公開し、日付欄は `DateOf` で描いて `SetDate(field, "YYYY-MM-DD")` で書く（その日の 0 時の unix 秒への変換は core。ADR-005 S4f）。ページを開いていなくても（ヘッドレスでも）動く。**両方向が同じ 3 操作を通る**（provider 側の `_ApplyKey` も入力側と同じメソッドを呼ぶ）。ただし `themeColor`/`accentColor` の 2 キーだけは **`core/ColorService.py` の持ち物**で本サービスを通らない（歴史的にこのプレフィックスなだけで、意味はカラー機能側）。**表示大会名の入力欄は React の SCORE ページにあり、v1 の `PATCH /api/v1/tournament/info`（`AutonaWebOperations.update_tournament_info`）が `SetDisplayName` を呼ぶ**（Qt の SCORE パネルとクルーボードの 2 箇所（クルー側は Step 5 第 4 弾で合流）も同じ操作を呼んでいた。S4ac-3 で削除） |
| `src/core/PlayerService.py` | なし（スロットの path に束縛した**一時インスタンス**を呼び出し側が作って使い捨てる: Web の操作・セット読込・`AutonaActions.ClearPlayers`・モジュール関数 `RepublishTeamMergedNames(board, team)`。S4ac-3 までは選手ウィジェット 1 枚につき 1 個の `AutonaBasePlayerWidget.player` もあった） | 選手スロット配下の書き込み全部（入力フィールド・seed/birthday・国/州・`mergedName`/`mergedOnlyName`・アバターとスポンサーロゴ・キャラスロット・`city`/`id`・スロット削除）。**選手の読込（`ApplyData(data, loadFromDB, clear, no_mains)`）・スロットのクリア（`ClearSlot`）・DB への保存（`SavePlayerToDB` = state 側の `PrepareSaveToDB`（番号付きの記録を返す）+ DB 側の `SaveRecordToDB`。Web の操作は DB 側をボードホストの外で呼ぶ: `PlayerDB.changed` の受け手 `AutonaPlayerDB.SetupModel` がメインスレッドを待っていたため（ADR-005 S3f-2 で待たなくなった）。外へ出したので、同じ DB キーにもっと新しい番号の記録が保存済みなら古い方を捨てる。ADR-005 S3e-4 レビュー）もここ**（ADR-005 S3e-3）: 読込は DB の記録とのマージ・悪語フィルタ（コピーに対して。呼び出し側の dict と DB の記録は書き換えない）・国 / 地域（code と `FindState`）・mains → キャラスロット・seed・合成名と画像を 1 保存サイクルで書き、旧カードの `SetData` と同じ Set 列を出す（`tests/test_player_apply.py` / `tests/test_player_service.py` が ae922c24 の録画と照合）。操作の前後で id / seed が変わったか（`SlotChange`。戻り値でもある）で、読込自身が統計を 1 回だけ依頼する（`Announce` → `StatsService.PlayerChanged`。ボードのスロットだけ。`SetSeed` / `SetPlayerId` は統計を依頼しない（Qt のカードの手入力のための `announce=` 引数は、S4ac-3 から呼び手が無く、Qt モデルの整理 PR1 で削除した。依頼するなら呼び手が明示的に `Announce(SlotChange(...))` を呼ぶ。テストの `edit_tag` が録画の再生でそうしている）。ADR-005 S3e-5。それまでは呼び出し側のカード・ボード・ボードホストが依頼していた）。**クリアしない読込（Web の選手編集・legacy `update-team`）で、書き込み後のスポンサー + タグの組が前と違えば別の選手として扱い、読込が書かない id・city を空にする**（`_NamesAnotherPlayer`。ADR-005 S3e-4。同じ組なら残す。残すかは読込が実際に書いたかで決めるので、途中の例外でも前の選手の id が残らない。online_avatar・seed は残す: Qt のカードの手入力の `NameChanged` と同じで、LIVE-BUG-1「タグを直しても顔写真は消えない」を守る）。保存はウィジェットではなく state から組む。**v1 の欄とキャラの行の編集もここ**（ADR-005 S4z）: `Edit(texts, country, region)` は選手の入力欄の確定（S4ac-3 までの Qt のカードの入力欄と同じ書き込み）（文字の欄・国・地域。スポンサー / タグが変われば合成名と画像を作り直して id を空にし、統計を依頼する。途中で例外が出ても書いたぶんについて同じことをする: `ApplyData` と同じ保証）、`EditCharacter(row, character, skin, variant, game)` はキャラ行の 3 つのコンボの選択（行は state のキャラ枠の数だけ。0 なら行は無い）。どちらも 1 保存サイクルで、断るときは何も書かずに理由のコードを返す。`AutonaActions.ClearPlayers` も各スロットの `ClearSlot` を呼ぶ。`BoardNumber()` は `score.{N}` の番号を返す（それ以外のパスは `None`）。**`mergedName` / `mergedOnlyName` の ` [L]` は state のチームの `losers` から付ける**（`PublishMergedName(sponsor, tag)` が `TeamLosersKey()` を読む。ADR-005 S2b）。フラグの書き手は同じ SaveBlock で `RepublishTeamMergedNames` を呼び、チーム全員の名前を state の `team` / `name` から作り直す: `AutonaActions.SetTeamLosers`（LOSERS チェックボックス・v1 `PATCH set` の `AutonaWebOperations.set_team_losers`）と `SetLoadService.ApplyLosers`（セット読込・legacy `-set?losers=` の `ChangeSetData`。`DisplayTeam` で解いたチーム）。どちらもフラグが変わったときだけ。Qt のカード（S4ac-3 で削除）は名前確定で `_OnTextChanged`（state に書く）→ `NameChanged`（publish）の順に走ったので、作り直しがどのスレッドから割り込んでも同じ名前になった。**国/州とキャラスロットは呼び出し側から識別子だけを受け取り、値はここで組む**（ADR-005 S1d）: `SetCountry(code)` / `SetRegion(countryCode, regionCode)` は `CountryCatalog.CountryByCode` / `State` から、`PublishCharacterSlots([CharacterSlot(en_name, skin 行, variant en_name, フォールバック名), ...])` は `GameAssetCatalog.CharacterRow` / `SkinRow` / `VariantRow` から（1 回の publish は `Current()` 1 回のスナップショットで解決）。入れ子のキー順は旧実装が Qt モデル経由で出していた QVariantMap 順を `DictHelper.qvariantmap_copy` で再現する（`program_state.json` と Socket.IO delta のバイト互換。`tests/test_player_slot_publish.py` と `tests/test_player_service.py` が移行前の golden と照合）。カタログが知らない識別子は、キャラなら `{"name": フォールバック名}`（手入力テキスト、または選ばれた行自身の `name`。コンボの「表示名 / en_name」ラベルは使わない）、国/州なら `{}`。カタログは `games=` / `countries=` で差し替え可能（既定は呼び出し時の `.instance`） |
| `src/core/CrewBattleService.py` | なし。Web（`AutonaWebOperations`）は呼び出しごとに 1 個作る（S4ac-3 までは Qt のクルーボードの `AutonaCrewBattleWidget.crew` もあった） | クルーボード（board 3）の書き込み全部（起動シード・メンバー名・ストック（`AdjustMemberStock` / `SetMemberStock`。0〜99）・人数（`SetMemberCount`。1〜9）・出場中のメンバー（`SelectActiveMember` / `ReleaseActiveMember`。1 チーム 1 人）・**クルー合計の算出**（名前が入った表示中メンバーの stock 合計 → `team.{t}.score`）・crew 版のセットデータ正規化（`ApplySetData`）・`best_of`（raw のみ・0〜99）・スワップ / ストックのリセット / クリア（`SwapTeams` / `ResetStocks` / `ClearCrew`。両チームを 1 サイクルで publish。変わらないボードは書かない）・スワップのフラグ（`score.3.teamsSwapped`。ADR-005 S4w）・1 メンバーの部分更新（`RenameMember` / `UpdateMember`。ADR-005 S4w）・X 投稿文（`PostText`。読むだけ。ADR-005 S4w）。**操作は state を読んで書く**（ADR-005 S3e-2。旧実装はウィジェットの行を受け取っていた）。合計も state から算出する |
| `src/core/CommentaryService.py` | なし（モジュール関数） | `commentary.*` の書き込み全部（ADR-005 S3e-2）: 選手データでの 1 枠の上書き（`ApplyPlayerData`。Web と補完の選択。悪語フィルタ → 正規化 → 1 枠をマージしてルートごと 1 回 publish = 旧パネル経由と同じ Set）・タグからの読込（`LoadFromTag`。`PlayerDB`）・パネルの 1 欄の編集（`SetField`。値が変わったときだけ。表示は `MAX_DISPLAY` 人まで）・並べ替え（`Reorder`）。実況パネルは Web 版（React の `CommentaryPage`）だけで、state を描画するだけ（書き手は core の本サービス。ADR-005 S4c で Qt 版を削除） |
| `src/core/Top8Service.py` | なし（モジュール関数） | `top8.*` の書き込み全部（ページの選手の編集 `UpdatePlayer`（ADR-005 S4i から React のページが `PATCH /api/v1/top8/players/<slot>` で呼ぶ。1 リクエストの欄を `StateManager.lock` の中でゲームのカタログの 1 つの snapshot と国の 1 回の参照で解決し、1 つでも断れば何も書かず、1 保存サイクルで書く。キャラに `game` が付いていれば読み込み中のゲームの codename と照合する）と、1 欄の `SetPlayerText` / `SetPlayerCountry` / `SetPlayerCharacter`（その 1 キーだけを、変わったときだけ）・表示ラベルの既定値・反映（staged スコアのマージ + `Recompute`）・全リセット・Start.gg 取り込み）。**国とキャラは識別子で受け取り、値はここで組む**（ADR-005 S4h）: `SetPlayerCountry(slot, code)` は `PlayerService.BuildCountryValue`（選手カードと同じ形・キー順。カスタム国旗も code）、`SetPlayerCharacter(slot, en_name, skin)` は `CharacterValue` = `PlayerService.BuildCharacterValue`（スキンの行が範囲外なら最初のスキン。オーバーレイが描く `assets` 込み）。カタログが知らない code / en_name は書かずに False。取り込み（`ResolveImportedPlayer`）のキャラは `ImportedCharacterValue`: 従来のキャラの config のコピーをキー・値・順序ともそのまま残し、`CharacterValue` にしかないキーを後ろに足す（追加だけ。`out/top8/...` のテキストと自作レイアウトのため。コンボに無いキャラ = mods オフの modded はコピーだけ）。state の値 → 選択（en_name, スキンの行）は `CharacterPick`（codename が一致する行を優先。codename の無い値は全キャラのスキンから assets で探す。S4h より前の 3 つの形も読む）。ページは同じ規則を TS に持つ（`web/src/Top8Page/characterPick.ts`。`characterPick.cases.json` を `tests/test_top8_service.py` と jest の両方が読み、食い違いを検出する。S4i）。**反映 `ApplyScores` は渡された試合だけを更新するマージ**（渡されなかった試合は今の値を保つ。lock の中で読んで書く。スコアは 0〜`SCORE_MAX`(99) の int 2 つの list だけ。1 つでも不正なら何も書かずに None。S4h）。**取り込みの入口は `RefreshFromProvider()` の 1 つ**（ADR-005 S3e-8。ページの「大会結果を読み込む」（`POST /api/v1/bracket:refresh`）・ホットキー・legacy / Socket.IO が同じ関数を呼び、ヘッドレスでも動く）: 大会の最後の phase のダブルエリミのグループ（`TargetPhaseGroup`）→ 取得 → `core/Bracket` → `Top8Model.FromBracket` → 選手を `GameAssetCatalog` / `CountryCatalog` で解決 → `ImportBracket`。取得は `bootstrap_core()` が注入する `fetcher`（provider の `RunWithProvider`。provider のワーカースレッドで走る）。実行中フラグは取り込み全体（取得〜書き込み）で 1 つ、全入口が共有し、実行中の要求は `BUSY` で捨てる（キューしない）。選手の解決と書き込みは `StateManager.lock` の中（ゲームの切り替えとの競合で前のゲームのキャラが残らない）。空・失敗の結果は何も書かない。**ゲームが別のゲームに変わったときのキャラの消去 `ForgetCharacters()`** も持つ（キャラがある枠だけ・1 保存サイクル。呼ぶのは `GameAssetCatalog.SelectGame` だけ）。Top 8 のページ（React。S4i）は操作を依頼し、state を描画するだけ（実況パネルと同じ型）。トポロジ自体は Qt-free の `src/core/Top8Model.py` が正本 |
| `src/core/ColorService.py` | なし（モジュール関数） | 大会カラー（`tournamentInfo.themeColor`/`accentColor`）と機能別カラー（`colors.{feature}.*`）の読み・書き・起動シード。**`tournamentInfo` プレフィックスだがこの族は本サービスのもの**で、`TournamentInfoService` は触らない（歴史的なキー名） |
| `src/core/GameAssetCatalog.py` | `GameAssetCatalog.instance`（`bootstrap_core()` 生成。Qt 側は `AutonaGameAssetManager` が包む） | ゲームアセットの正本（スキャン・ロード・キャラ/スキン/ステージ/アセットパス・Start.gg ID 解決）と `game.*` の書き込み。ロード結果は不変スナップショットで差し替え、リーダーはロックを取らない。書き手のロック保持中に `run_sync` / Qt シグナル / observer を呼ばない（ADR-005 S1a）。**例外は `SelectGame` の `progress` コールバックだけ**でロック保持中に呼ばれるため、渡す側はブロックしない（queued で投げるだけ。アダプタは core の `StatusService.Progress` を渡し、その通知の購読者は Bridge の `status_changed` だけ: フッターへは GUI スレッドに queued、Web サーバーは送信の予約を積むだけ。ADR-005 S-final SF-2）。observer から書き手を呼ばない（agent-pitfalls #16）。**`SelectGame` は、state の `game.codename` が空でなく新しい codename と違うとき、Top 8 と大会リザルトのキャラを空にする**（writer lock を離した後・同じ `SaveBlock` の中で `Top8Service.ForgetCharacters()` と `ResultService.ForgetCharacters()`（ADR-005 S4q）を直接呼ぶ。observer は使わない。起動時の復元（`game` が空）・同じゲームの選び直しでは呼ばない。ADR-005 S3e-8）。同じ判定はスナップショットの `replaced`（置き換えた前のゲームの codename。それ以外と `ForgetSelection` の後は None）に残り、ボードの選手のキャラと mains はロード後にシェルが走らせる `AutonaActions.ApplyGameRoster(replaced)` が空にする。値はロードが返したスナップショットのもので、ローダーの `gameLoaded`（`Signal(object)`。デスクトップはアダプタ、ヘッドレスは `HeadlessGames`。`onLoad` / `gameChanged` の直前に出る）で `bootstrap.wire_game_roster` に渡る。後から `Current()` を読まない（スロットが走る頃には、次のロードのスナップショットになりうる。ADR-005 S3e-6 レビュー）。**デスクトップのロードは `TaskExecutor` のジョブ 1 本（ローダー）**（ADR-005 S3f-4。旧 `AssetsLoaderThread`）: 要求は最新の 1 つだけを記録し（`_requestLock`）、ローダーが走っていなければ 1 本積む。ローダーは待っている最新の要求を、無くなるまで順に取る（要求ごとのジョブにしない: 立て続けの切り替えでジョブレーンを埋めない）。ローダーが保存ブロックを開いて `SelectGame` を呼び、`gameLoaded`・`onLoad` は `run_sync` で GUI スレッドに渡した `_Publish` の中で順に走る（ロスターの書き込みも同じ保存サイクル。`_Publish` 全体がボードホストの `Run` の中: ADR-005 S4ad のレビュー。Qt モデルの整理 PR4 までは、その前に Qt のモデル群の作り直しとアイコンのデコードの開始もあり、ローダーはデコードを待ってから終わっていた）。ブロックを閉じるのも `_Publish` の終わりの GUI スレッドなので、通常は公開も held 通知も GUI スレッドから direct に出る（pitfalls #17 の原子性を保つ）。ただしそのとき別スレッドの SaveBlock が開いていれば、最後に閉じたスレッドが公開する。**ヘッドレス（`src/headless.HeadlessGames`）も同じ設計のジョブ 1 本**（ADR-005 S3f-6。旧: 要求ごとの daemon スレッド）で、保存ブロックを `SelectGame` の後に閉じ、ロード後の処理（選択の保存・`gameLoaded` / `gameChanged`）を `run_sync` でメインスレッドに渡して待つ（非同期の要求も。見捨てられたら `_SettleStopped`）。**後から来た要求が勝つ**（世代番号。待っている要求は新しい要求に置き換わると選ばずに終わる。同期ロード（`set_game`）は、自分か後の要求の試行が終わってから戻る。`"OK"` は成否を問わず「試行が終わった」で、GUI スレッドで同期ロードしていた頃と同じ）。ローダーが要求を取った直後に終了中（`gui_unavailable_reason()`）なら何も書かずに `done` へ進む（両シェル）。選択の後に終了が来たら、ロスター（前のゲームのキャラの消去）・ボードの反映・選択の保存をローダーのスレッドで書き、state.json を 1 つのゲームに揃える（デスクトップは `_SettleUnannounced`、ヘッドレスは `_SettleStopped`）。（Qt モデルの整理 PR4 までは、デスクトップでモデルの作り直しが失敗したとき（`FAILED`）も `_Publish` の中で同じことをしていた。作り直しと一緒にその経路も無くなり、揃える経路は終了中の 1 つだけ）GUI スレッドからの同期ロードは RuntimeError。要求の Future は取り消せない（`cancel()` は False。空の codename は答えの済んだ `SKIPPED`）。`done()` は試行を終わらせたスレッド（ローダーのジョブレーン・置き換えた要求のスレッド・シャットダウンのスレッドなど）で走るので、短く済ませ、別のロードを頼まない（ジョブレーンからはローダーを起こせず、同期なら自己デッドロック）。ゲームが変わったときの全量の Socket.IO 送信（`ws_game_changed`）は、ロードの公開（`StateManager.WhenPublished`）とその delta の送信（`WebServer._after_pending_deltas`）の後に出す（revision の契約: R+1 の delta が先、全量の revision は中身と一致。開いている delta の窓はこのときだけ待たずに送る）。**Web / Start.gg のリーダーはカタログを直接読む**（ADR-005 S1b）: `AutonaWebOperations.get_games` / `get_characters` / `get_variants` は `*Payload()` を返すだけで `@gui_thread_sync` を持たない。`StartGGDataProvider` は `CharacterFromStartGGId` / `StageFromStartGGId` で解決し、1 つの結果（1 セットのパース・1 エントラント）を組む間は `Current()` を 1 回取って `snapshot=` で渡す（start.gg の ID → 名前の表 `_startggCharacters` はスナップショット外だが、ゲーム非依存なので実害なし）。ゲームの切り替えは codename で `LoadGameByCodename` を呼ぶ（スキャン上の位置を引いてから位置でロードしない。間の再スキャンで別ゲームになる）。終了を待つ側は `done=`（成否を問わず 1 回）を使い、onLoad（実際にロードされたときだけ）で待たない。新しいリーダーをアダプタのプロパティ（`games` / `selectedGame` / `has_modded_content`。`characters` / `skins` / `variants` / `colors` / `biggestCompletePack` と Qt のモデル群・`GetStockIcon`・`GetCharacterAssets` は Qt モデルの整理 PR4 で削除）に繋がない。ペイロードの入れ子のキー順は旧モデル経由の QVariantMap 順を再現している（Socket.IO のバイト互換。`DictHelper.qvariantmap_copy`（S1d で core 共用に移動。選手スロットの値も使う）。str キーの dict と list だけ変換し、tuple と非 str キーの dict は PySide6 と同じく並べ替えない） |
| `src/core/CountryCatalog.py` | `CountryCatalog.instance`（`bootstrap_core()` 生成。Qt のアダプタは無い） | 国・地域（states）・カスタム国旗・都市→地域表の正本（ADR-005 S1c）。`Load()` が唯一の書き手で不変スナップショットを差し替え、リーダーはロックを取らない。state は書かない。ロードは `Window.__init__` の `CountryCatalog.instance.Load()`（ヘッドレスは `src/headless.py` が同じ `Load()` を直接呼ぶ。国名がロケール依存なので `LoadLocale()` の後。ロケールは callable 注入）。都市→地域表（`FindState`）も `Load()` が 1 回だけ読む（最初の照会は選手の読込から `StateManager.lock` の中で来うるため。ADR-005 S3e-3。`Load()` していないカタログでは最初の照会で読む）。**データを返すリーダーは必ずコピー**を返し、表に書き戻さない（旧 `LoadStates` と Start.gg の次セット解決が地域 dict に `asset` を書き込み、`get_states` の出力が「デスクトップがその国を表示したか」で揺れていた）。地域の `asset`（旗パス）は `States()` / `State()` が呼ばれた時点で解決する（地域ごとにディスクを見る。旗の要らない照合 = 選手の読込の地域コード合わせは、`StateManager.lock` の中なので `StateCodes()`（code / original_code だけ）を使う。ADR-005 S3e-3）。`AutonaWebOperations.get_states` は `States()` を返すだけで `@gui_thread_sync` を持たない。Start.gg は `CountryCodeFromEnName` / `FindState` / `State` / `BasicCountryInfo` で解決する。選手の国を code から引くのは `CountryByCode`（標準国 → カスタム国旗の順。国モデル走査と同じ答え。`PlayerService.CountryValue` が使う） |
| `src/core/SponsorHelper.py` | なし（静的メソッド） | `...player.{M}.sponsor_logo` / `sponsor_logos.{I}` の解決と書き込み。`PlayerService.PublishImages` から呼ばれる |
| `src/core/TournamentMediaService.py` | `TournamentMediaService.instance`（`bootstrap_core()` 生成。今の provider の引き方 `currentProvider` は注入） | 大会アイコン / バナーのすべて（ADR-005 S4f）: ファイルからの取り込み `ImportMedia(kind, bytes)`（アイコンは長辺 512、バナーは長辺 1920 の PNG。検証・縮小・tmp → `os.replace` は配信団体素材と共通の `core/ImageImport.py`）・大会からの取得 `FetchMedia()`（`TaskExecutor` のジョブ 1 本、状態は `tournamentInfo.media.{status,error,revision}`。大会は URL（`provider.url`）で見分ける。実行中の要求は、同じ大会なら `BUSY`、別の大会なら `QUEUED`（走っている取得が終わったら同じジョブがその時点の大会で取り直す。大会の設定の `fetchMedia` が捨てられないように。S4f レビュー M1）。答えを待つ間に大会が変わったら書かずに `superseded`。照会の失敗（provider の `strict=True` の lookup が例外）は `fetch_failed` で、「メディアが無い」`no_media` と区別する）・既定に戻す `ResetMedia()`。`logoVersion` / `bannerVersion` を publish する。大会情報ページ（React の `EventPage`。ADR-005 S4g）は `/api/v1/tournament/media*` で依頼して state を描画するだけ |
| `src/core/TournamentService.py` | `TournamentService.instance`（`bootstrap_core()` 生成。provider のアダプタと、メインスレッドで走らせる `onMain`（GUI executor）は注入） | 大会の設定・解除（`TOURNAMENT_URL`）の唯一の経路（ADR-005 S4f）: `SetTournament(url, force, fetchMedia)`（event URL はメインスレッドで保存 → アダプタの `LoadTournament` → `AnnounceUrl` → 求められたら `FetchMedia`。admin / brackets の URL は `TaskExecutor` のジョブで event URL に解決してから（解決は QObject を作らない関数 `StartGGDataProvider.ResolveEventUrl`）。後から来た設定・解除が先に勝つ世代番号つき。それ以外の URL は `INVALID_URL` で何もしない）・`ClearTournament()`（URL を空 → **`GameAssetCatalog.ForgetSelection`** → `UnloadTournament`（その `tournament_changed` でゲームのコンボ・サイドバーが選択を読み直すので、忘れた後に出す）→ `AnnounceUrl("")`）・`Restore()`（起動時の読み直し）・`TournamentUrl()`（読み手）・`Load()`（最新の設定 / 解除の進み具合 `{status, error, revision}`。`idle` / `resolving` / `loading` / `loaded` / `error`（`unresolved` / `load_failed`。読み込みの例外も `load_failed`）。**state の `tournamentInfo.load` と、URL の写し `tournamentInfo.url` に publish する**（ADR-005 S4g レビュー M1。設定 / 解除は要求したスレッドで `StateManager.lock` → 自分の lock の順に取って次の revision を publish してから戻る。`GET /api/v1/tournament` と大会情報ページが読む）。読み込みの結果はシェルが `PayloadApplied` で伝える（`bootstrap.wire_tournament_data`）。今の provider でない取得の結果（遅れて届いた前の大会の結果）は provider のアダプタ（`AutonaTournamentDataProvider._onTournamentDataResult`）が捨てる（S4g レビュー M2）。ADR-005 S4g）。入口（サイドバーの URL ダイアログと解除の確認 `ConfirmClearTournament`・`AutonaWebOperations.load_tournament` / `clear_tournament`＝旧ルート / Socket.IO / `/api/v1` の `POST` / `DELETE /tournament`。大会情報ページはこの v1 を呼ぶ）はどれもここを呼ぶ。`tournament_url_update` は通知だけ（命令として emit しない） |
| `src/core/AnalysisService.py` | `AnalysisService.instance`（`bootstrap_core()` 生成。取得の `fetcher`（provider の `RunWithProvider`）・メインスレッドで走らせる `later`（GUI executor の `later`）・ラウンド名 `roundNames`（`AutonaLocaleHelper.matchNames`）は注入） | この大会の試合一覧のすべて（ADR-005 S4j。それまでは STATS パネルのサブタブ `AutonaAnalysisWidget`（S4k で削除）のメモリ）: `Refresh()`（`STARTED` / 実行中なら `BUSY`（キューしない）/ `NO_TOURNAMENT`）が、ジョブの中で毎回フェーズを取り直し（`GetTournamentPhases`）、id のある全 phase group（同じ id は 1 つ）を共有キューから**最大 2 本**（`PARALLEL_FETCHES`。S3f-2 判断 7。捨てた取得のジョブがまだ走っている間はその数だけ減らし、最低 1 本。試合一覧がジョブレーンを 2 本より多く使わない）で取る。provider には **strict で頼む**（`strict=True`: 要求の失敗を空の答えにせず例外にする。`TournamentMediaService` の lookup と同じ形）ので、`fetch_failed`（フェーズの要求が失敗・どの group の要求も失敗）と `no_phases`（phase group が無い）・sets の無い group を区別できる。2 本目はジョブから積めない（TaskExecutor の入れ子の禁止）ので、1 本目が `later` でメインスレッドに頼んで始める。どのジョブも別のジョブを待たない。最後の答えを記録したジョブが `core/Bracket` で行を組む（UpSet = 想定敗退ラウンドの差・Losers Run・SeedOver・複数 group のときのラウンドの前置き）。進み具合は state の `analysis`（`{status, error, done, total, failed, revision}`。取得のたびに次の revision を `Refresh` が戻る前に publish）に出し、**行は state に入れずメモリに持つ**（取得した大会の URL と組。`Result()` / `Rows()`。行を写さずにどの行かだけを返すのが `ResultRevision()`。error で終わった取得は前の行を残す）。行は構造（`round` / `roundOrder` / `p1`・`p2` の `{seed, name}` / `score`（未完了は None）/ `upset` / `losersRun` / `seedOver`）で、文言は表示側。`Forget()` は provider のアダプタの `LoadTournament` / `UnloadTournament` が **provider を差し替えた直後に**直接呼ぶ（observer は使わない。差し替えの前に呼ぶと、その間に別スレッドから来た `Refresh` が旧 provider で取得し、旧大会の行が新大会の結果になりうる）: 世代を進め、取得の残りを止め（遅れた答えは記録しない）、行を捨てて idle を次の revision で publish する。**ロック順は `StateManager.lock` → サービスの lock**。サービスの lock は自分のフィールドだけを守り、state の読み書き・fetcher・`later` はそれを離して呼ぶ。外からは v1 の `POST /api/v1/analysis:refresh`（`Refresh()`。202 / `BUSY` は 409 `CONFLICT` / `NO_TOURNAMENT` は 409 `NOT_READY`）と `GET /api/v1/analysis`（`{status, url, rows}`。`Result()` の URL が今の provider の URL と一致するときだけ行を返す）で使う（`AutonaWebOperations.refresh_analysis` / `get_analysis`。どちらも呼び出し元のスレッドで core を呼ぶ）。統計情報ページ（React の `StatsPage`。ADR-005 S4k）の「この大会の試合一覧」タブは、ボタンでこれを呼び、`analysis` の 1 行を描き、`analysis.revision` / `status` が変わったときだけ行を読み直すだけ |
| `src/core/SetSelectionService.py` | `SetSelectionService.instance`（`bootstrap_core()` 生成。取得の `fetcher`（provider の `RunWithProvider`）は注入） | セット選択の一覧のすべて（ADR-005 S4v。Qt のセット選択・配信台ウィンドウが出すものを、ウィンドウの無いシェルと SCORE ページのために）: `RefreshPhases()` / `RefreshStations()` / `RefreshSets(phase, finished)` / `CancelSets()`。どのセットを読むか・どの配信台を追従するかは `SetFollowService` の仕事で、ここは取って持つだけ。Qt の窓は provider のアダプタを通っていた（寄せなかった: アダプタのフェーズ取得はプール候補も使う。両入口が start.gg に同じ引数で頼み、同じ表を作ることを `tests/test_set_selection.py` が比較で pin していた）。窓は S4ac-3 で削除し、比較のテストも消した（窓が頼んでいた引数は `tests/test_set_selection.py` の parametrize に凍結し、窓の表は `web/src/ScorePage/setRows.cases.json` が持つ）。**リストごとに進み具合を state の `set_selection.{phases,sets,stations}`**（`{status, error, done, total, failed, revision}`。取得・止める・大会の変更のたびに次の revision を、呼び出しが戻る前に publish）、**行はメモリ**（大会の URL と組。`Result(kind)`。読むのは `AutonaWebOperations._get_selection_list` で、`StateManager.lock` の 1 回の保持の中で状態・行・今の URL を読み、URL が一致するときだけ行を渡す）。**失敗と空を区別する**（provider に `strict=True` で頼む。`GetMatches` は 1 ページ目がリトライ後も答えなければ例外で、`failed_pages(n)` が欠けたページ数を言う。`GetStations` は event が答えなければ例外）: `error` / `fetch_failed` は前の行を残し、`done` で行が空なら「何も無い」。取れなかったページと読めなかったセットは `failed`。**実行中の要求**: `phases` / `stations` は `BUSY`。`sets` は同じ `{phase, finished}` は `BUSY`、別の要求は走っている取得を止めて置き換える（S4ac-3 で削除した Qt の窓のコンボと同じ。止めた取得の答えは捨てる）。止めた取得（置き換え・`CancelSets`・`Forget` のどれでも）の要求がまだ答えず、そのリストのジョブが `MAX_SET_JOBS`（2）本残っている間は、どの要求も `BUSY`（ジョブレーンは 4 本で全ての大会の取得が共有）。ジョブから別のジョブを待たない（1 取得 1 ジョブ）。**遅れた答えは捨てる**: 取得は 1 回ごとのオブジェクトで、`_fetches[kind]` が自分でなくなった（置き換え・`CancelSets`・`Forget`）答えは何もしない。世代番号は持たない。`Forget()` は provider のアダプタの `LoadTournament` / `UnloadTournament` が **provider を差し替えた直後に**直接呼ぶ（`AnalysisService` と同じ。observer は使わない）: 全リストを次の revision の idle へ、`phases` / `stations` の行は捨て、**`sets` の行は URL と組で残す**（S4ac-3 で削除した Qt の窓の、全ボード共有・プロセス寿命のキャッシュと同じ。同じ大会を読み直すと戻る。枠は 1 つ）。**フェーズの解決**: フェーズの行があれば `None` は `PHASE_REQUIRED`、知らない id は `UNKNOWN_PHASE`、`"all"` は全グループ。無ければ大会全体で、id は `UNKNOWN_PHASE`。`query` は正規化した値（`"11"` は行の id の `11`、フェーズが無いときの `None` は `"all"`）で、同じ要求は 1 つ。解決と取得を current にするのは `_Start` の 1 回の lock の中（`plan`）: 間に `Forget` が入ると、次の大会に前の大会のグループを頼んでしまう。**行はホワイトリスト**（`PhaseRows` / `SetRow` / `StationRows`。タグとエントラント名だけなので READ で返せる。キャラ・実名を含まないのでゲームの変更で捨てない）。Qt の文言（ダブルスの「 (A, B)」・「完了したペアのみ」）は読む側の仕事で、規則は `external-control-api.md` §5.18。**ロック順は `StateManager.lock` → サービスの lock**。サービスの lock は自分のフィールドだけを守り、state の読み書き・fetcher はそれを離して呼ぶ。外からは v1 の `GET /tournament/{phases,sets,stations}` と `POST /tournament/{phases,stations,sets}:refresh`・`POST /tournament/sets:cancel` で使う（202 / `BUSY` は 409 `CONFLICT` / `NO_TOURNAMENT` は 409 `NOT_READY`）。`SeedPanelState` が起動のたびに idle に戻す（revision は続く） |
| `src/core/MatchNameCandidates.py` | `MatchNameCandidates.instance`（`bootstrap_core()` 生成。用語の候補 `generic`（`AutonaLocaleHelper.Get{Phase,Pool,Match}NameCandidates`）とラウンド名 `matchNames` は注入） | match バーの 3 つの編集可能コンボ（phase / pool / match）が出す候補（ADR-005 S4x。それまではボード 1 / 2 のウィジェットが provider の通知で足していた）: `Candidates(board)` が `{phase, pool, match}` を返す。ボード 1 / 2 は読み込んだ大会の名前（複数グループのフェーズのプール名・参加人数の `Winners / Losers TOP{n}`）を用語の前に足す（用語にある名前は用語の位置のまま）。ボード 3 と `None` は用語だけ。大会の名前は provider の `tournament_data_updated` / `tournament_phases_updated` を `bootstrap_core()` が `FollowTournamentData` / `FollowPhases` に繋いで持つ（読み手より先。state には入れない）。自分の lock は自分のフィールドだけを守る。v1 の `GET /match-names/candidates?board=` と Web の `MatchInfoBar` が同じリストを描く（Qt のボードのコンボ（1 / 2 / 3）も同じリストを描いていた。S4ac-3 で削除） |
| `src/core/StreamService.py` | `StreamService.instance`（`bootstrap_core()` が生成して `Start()`。バナー URL の照会・`later`（GUI executor の `later`）・出力の剪定は注入） | 配信設定のすべて（ADR-005 S4d）: 配信タイトルの自動生成（大会名・ゲーム略称は **state の observer** で見る＝下の例外。上位の参加者は provider が `SetTopEntrants` で渡す。生成したタイトルは毎回 `stream.titleAuto` に publish し、`stream.title` は `TitleIsAuto`（前回の自動値か空）のときだけ上書きする。クライアントの欄の打ちかけを送るかは `TypedTitleWins`。パネルが無くても・ヘッドレスでも動く）・チャンネル CRUD（`stream_channels`）・団体素材の取り込みとリセット（PIL で縮小・`stream.org*Version`）・配信サムネ生成（`TaskExecutor` のジョブ 1 本、実行中の要求は `BUSY`、結果は `stream.thumbnail`）・生成オプション（`stream_thumbnail_options`）。配信設定ページ（React の `StreamPage`、`/api/v1/stream*`。ADR-005 S4e で Qt 版を削除）は操作を依頼し、state を描画するだけ |
| `src/core/ResultService.py` | `ResultService.instance`（`bootstrap_core()` が `ThumbnailService` の後に生成。`later`・描画の開始 `render`・翻訳・ロケール・出力の剪定・`shuttingDown`・取得の `fetcher`（provider の `RunWithProvider`）・大会情報の取り直し `refreshTournament`（`RefreshTournamentData`）・今の provider の引き方 `currentProvider` は注入） | 大会リザルトのすべて（ADR-005 S4q）: 行の読み書き（単独 16 行の `result_players`・団体 8 行の `result_teams`。`state.json` に残る。**読みは `_Rows`**（保存された別のゲームのキャラを無いものとして読む。state は消さない。判定は絵のパスの `GAMES_DIR/<codename>/`）、**書きの元は `_StoredRows`**）。`UpdatePlayer` / `UpdateTeam` は 1 行 1 保存サイクルで、キャラは識別子（en_name + スキンの行 + 選んだゲーム）で受けて 1 つのスナップショットで全部を解決してから書く（`Refusal` のときは何も書かない）。`characters` / `members` はリストなら全置換、dict なら名指した位置（`"1"` から）・キーだけを変える（ページはこちら。別のゲームのキャラを読めないまま書き戻さない）。`Reorder` / `Clear` / `ImportStandings` は 1 保存サイクル、`ForgetCharacters()` は `SelectGame` が直接呼ぶ。ペイロード `BuildPayload()`（`tests/fixtures/result/golden.json` と一致。差分は `tests/test_result_service.py` の `DIFFERENCES`）: キャラ欄が空の選手はスコアボードの同名の選手のキャラで描く（描画のときに解決し、state に書かない）・名前が無ければプレビューだけ固定のサンプル・背景はサムネイルの読み（`ThumbnailService.EffectiveBackgroundPath`。バナーは同じパスで置き換わるので `?v=<bannerVersion>` を付ける）。レイアウトは `settings.json` の `result_config.layout`（`SetLayout` / `FollowBattleFormat`。後者は `AutonaActions.SetBattleFormat` が呼ぶ（ADR-005 S4x。それまでは `Window.SetBattleFormat`）。今のレイアウトの写しを state の `result_layout` に出す: 対戦形式の切り替えはページの操作ではなく、保存が無ければ設定にも書かれないので、ページはここで知る。S4r）。描画は `core/RenderLane.py`（結果は `result_render`。データの無い `Generate` は `NO_DATA`。`Generate` の受理時に `SaveToDb()`。プレビューは DB を書かない）。standings の取り込み `LoadStandings(confirmed)`（実行中の判定 → 確認の判定の順。**`refreshTournament(done)` が大会情報の取得とゲームの読み込みを連鎖で済ませてから** provider のワーカーのジョブで取る。待たない: job レーンからの submit は `NestedSubmitError`。入力があれば確認を求める。結果は `result_standings`。provider の `GetStandings` は失敗を例外にするので、失敗と空を区別できる。取得の間にゲームか provider が変わったら書かずに `changed`。行に入れられなければ `import_failed`）。投稿文 `PostText()` は順位の順で、DB の X ハンドルを含むので `GET /result/post`（`PLAYERDB`）だけが返す。**ロック順は `StateManager.lock` → レーンの lock → 取り込みの lock。`PlayerDB` はロックを持たずに呼ぶ**。ページは React の `ResultPage`（`/web/result`。S4r）で、操作を依頼して state を描画するだけ。旧 Qt パネルが `result_players` に残した団体の行は `SeedPanelState` が起動のたびに消す |
| `src/core/RenderLane.py` | なし（`ThumbnailService` / `ResultService` の基底クラス。ADR-005 S4q で `ThumbnailService` から切り出した） | オフスクリーン描画の予約と結果: `Preview()` / `Generate()` / `PreviewImage()`。規則は下の `ThumbnailService` の「描画のレーン」のとおり。サブクラスが `_ReadRender` / `_RunPayload` / `_OutputStem` と出力先の定数を与える |
| `src/core/ThumbnailService.py` | `ThumbnailService.instance`（`bootstrap_core()` が生成。`later`（GUI executor の `later`）・描画の開始 `render`（`AutonaWebOperations.start_image_render`）・翻訳・ロケールの名前・出力の剪定・終了中か `shuttingDown`（`gui_unavailable_reason`）は注入） | 試合サムネイルのすべて（ADR-005 S4o）: `thumbnail_config` の副作用のない読み `Settings()`（既定値を書かない。残骸の `game.None` は読み飛ばして消さない）・全部を検証してから 1 回だけ書く `Update()`（未知のキー・パス・範囲外・一覧に無いゲームは全拒否。`background_path` / `main_icon_path` は受けない。v1 の描画には `Settings()` と同じ読みを通す: `GET` の値 = 描いた絵）・`Reset()`・動画タイトル `VideoTitle()`・ペイロード `BuildPayload()`（生の出力。`tests/fixtures/thumbnail/golden.json` と一致）と、描画に渡す `RenderPayload()`（背景の移行（描くコピーにだけ当て、`settings.json` は書かない）+ `BuildPayload` + 読み済みの設定。v1 の run と、同期で描く `DrawBoard`（SCORE の X 投稿・legacy の get-thumbnail。ADR-005 S4y）が同じ `RenderPayload` を描く。ADR-005 S4p）・カスタム背景の取り込み `ImportBackground()`（`user_data/thumbnail_background_<内容のハッシュ>.*`。テンプレートにキャッシュバスターが無いので同じパスで置き換えない）・ファイル名と 30 世代。**描画のレーン**（ADR-005 S4q から本体は `core/RenderLane.py`）: `Preview()` / `Generate()`。1 度に 1 つで、実行中の preview は最後の 1 件にまとめ、generate は 1 件だけ予約でき（予約の preview を兼ねる。兼ねた generate が試合なしで終わったら preview を続けて描く）、それ以上は `BUSY`。ペイロードは呼び出し元のスレッド、描画は GUI スレッド（`render` で開始するだけで待たない。イベントループを入れ子にしない）、符号化とファイルは `TaskExecutor` のジョブ。結果は `thumbnail.render = {status, path, error, revision}`（`error` はコードだけ。次の run は `later` で GUI スレッドから。run の終わりは 1 回だけ受ける）。ロック順は `StateManager.lock` → レーンの lock。レンダラーの土台 `src/AutonaOffscreenRenderer.py`（ADR-005 S4q で `src/thumbnail/ThumbnailRenderer.py` から切り出した。`src/result/ResultPageRenderer.py` も同じサブクラス）は描画を 1 度に 1 つにする（チケット）ので、SCORE の同期経路（`renderSync`）と同時に走っても互いの絵を取らない。読み込みの完了は `loadingChanged` の URL の番号（`?load=N`）で照合し、古い読み込みの完了を取らない。絵は `RENDER_READY` では撮らず、レンダラーのスクリプトが描画の番号付きで出す `RENDER_PAINTED`（フォント・画像の decode・rAF 2 回の後。上限は Qt 側で 1 秒。同じ文書で直前の描画がデコードした画像は頼み直さない）を待って撮る。内訳は描画ごとに `Thumbnail render complete:` の INFO 行に出る。読み込んだばかりのページの最初の 1 枚は、読み込み前のビューと同じ画素の間は撮り直す（上限 1 秒。S4o 判断 22）。プレビューの PNG だけ圧縮を下げて速くエンコードする（`encode("PNG", fast=True)` → zlib レベル 1。可逆で `image/png` のまま）。`out/thumbnails/` と legacy は既定のエンコードのまま（判断 23） |
| `src/core/AppSettings.py` | `AppSettings.instance`（`bootstrap_core()` が生成。言語の一覧 `AutonaLocaleHelper.languages` と待ち受け `WebServer.Listening` は callable で注入） | 設定ページ（React の `SettingsPage`、`/web/settings`。ADR-005 S4af。それまでは Qt の設定ウィンドウ）の一般・ネットワーク・言語の表（`SETTINGS`）と、`Describe`（許可リスト。`loadFailed` と、壊れた settings.json の退避先のファイル名 `movedAside` と、各項目の既定を PATCH が受ける形で `Defaults()`（`defaults`。ページの「デフォルト」ボタンが送る。`listenLan` は false。S4af。ホットキーの有効のスイッチの既定 `defaults.hotkeys` は `AutonaWebOperations.get_settings` が `HotkeyRules.Defaults()` から足す）つき）・`UpdateGeneral` / `UpdateNetwork` / `UpdateLanguage`（全部を検証してから `SettingsManager.SetMany` で 1 回。比べる前に `SettingsManager.PrepareWrite()`。まだ開けなければ `NOT_READY` で書かない）・`restartPending`（ADR-005 S4ae）。ポートの範囲は `PORT_RANGE` |
| `src/core/HotkeyRules.py` | なし（モジュール関数と、変更のチャンネル `changes`・このシェルが OS に登録するかの `applied`。`applied` は `bootstrap_gui()` が `ConnectReload` の後で立てる） | ホットキーの既定（`DEFAULT_KEYS`）・parser（`ParseShortcut`）・重複（`Conflicts` / `FindDuplicates`。（修飾キー, VK）で比べる）・警告のコード（`os_reserved` / `obs_likely` / `no_modifier` / `shift_only`。最後の 2 つは F キーを除く。どれも保存は通す。`shift_only` は S4af）・語彙・`CurrentKeys()`（保存値と既定の合成の 1 か所）・`Describe` / `Update`（ADR-005 S4ae）・有効のスイッチの既定 `Defaults()`（S4af）。保存の通知は `SettingsManager.lock` の外で出し、受け手は Bridge の `hotkeys_changed` だけ |
| `src/core/AssetPackService.py` | `AssetPackService.instance`（`bootstrap_core()` 生成。シェルのスキャン `scan` はシェルが入れる: デスクトップはアセットアダプタの `_Scan`、ヘッドレスは `HeadlessGames._Scan`） | ゲームアセットのパックのすべて（ADR-005 S-final SF-3b。それまでは「ゲームアセットを追加」ウィンドウの QThread 2 本と GUI スレッドのコピー / 展開）: 上流の索引（`EnsureIndex` / `FetchIndex` / `Packs`。10 分キャッシュ・取り直しは 5 秒に 1 回・取得中の呼び出しはその結果を共有）と、3 つのジョブ（`Install` / `ImportZip` / `ImportFolder`。`TaskExecutor` のジョブ、**1 度に 1 つ**（2 つ目は `BUSY`）。状態は `Job()`（state に入れない）、フッターは `StatusService` の `assets_load`、`Cancel()` とシャットダウン（`stopping`）はチャンク・エントリ・ファイルの間で効く）。書くのは `user_data/games` の中の作業フォルダで、最後に置き換える（置き換えるゲームは `.autona-replaced-*` に退避し、戻せなかった退避は消さない。次のジョブの開始で、元の場所が空なら戻し、あれば残してログに出す）。ゲームを置いたジョブは自分のスレッドで `scan` を 1 回呼ぶ（ジョブレーンから別のジョブを積めないので同期）。アーカイブは展開の前に全エントリを検査する（`ArchivePath`・リンク・件数・展開後の大きさ・空き容量）。許可するパックは `ALLOWED_PACKS`（ADR-002）。自分の `_lock` は自分のフィールドだけを守り、それを持ったまま scan / StatusService / executor を呼ばない。置き換えに成功した後の退避はマーカー（`.autona-discard`）を先に置いてから消し（読み取り専用の属性は外して消し直す。消し切れなければ warning）、マーカーのある退避は戻さずに消し直す。同じゲームの退避が複数あれば、入れ物の mtime が最も新しいものを戻す。退避の復帰はジョブの開始に加えて、両シェルの最初の scan の前にも行う（`RestoreReplaced()`。走っているジョブを待たない: ジョブと `RestoreReplaced` は `_filesLock` で 1 つずつ）。窓の上書きの確認（`ImportConflicts`）は、戻せる退避のあるゲームもインストール済みとして数える。v1 は一覧 `GET /assets/packs` とジョブの状態 `GET /assets/job` だけで、どちらも THIS_PC（一覧は読むと GitHub から索引を取りにいくことがあり、`index.error` は固定のコード。ジョブの開始は、THIS_PC が同じオリジンの `layout/` のページも通すので、SF-4b でシェルの秘密の下に出す） |

**選手リストの行と記録は `src/core/PlayerDirectory.py`**（なし。モジュール関数。ADR-005 S4m）: `Registered()` / `Entrants()` が選手 DB を `{game, players}` / `{game, entrants}` にする（行の mains のゲームの codename を同じスナップショットから添える。今のゲームの mains を防御的に読み、文字列の mains・壊れた要素・タグの無い行で落ちない。`country_code` / `state_code` は `Save` が受け取る値に読み替える）。`Save(fields)` は `PrepareSaveToDB` と同じ記録を組み（state は書かない。`tests/test_player_directory.py` が golden で照合）、`SaveRecordToDB` で保存する（順序の番号は `PlayerService.NextSaveSequence()` で共有）。**編集していない値を壊さない**: `mains` と `country_code` / `state_code` はキーが無ければ DB の値のまま（カタログに照らさない）、`mains` は `game` が今の codename と一致するときだけ受け、選手が今のゲームで既に持っている要素（同じキャラ・スキン・バリアント）は検証しない。文字列の mains の他のゲーム分は `SaveRecordToDB`（`PlayerDB.KeepStoredMains`）が守るので、カードの `savePlayerToDb` も同じ。`Delete(key)` は存在検査つき。読むのは `PlayerDB.lock` の中（生の dict なので）、保存はロックなしで呼ぶ。**番号（`_saveLock`）は `PlayerDB.lock` を取る前に取る**（順序は `_saveLock` → `PlayerDB.lock`）。選手の値をログに出さない（ログの sink は全部 `diagnose=False`。loguru の既定は traceback にローカル変数の値を書く。`tests/test_api_v1_players.py` が pin）。

**状態を持たないサービスはモジュール関数でよい**（`Top8Service` / `ColorService` / `CommentaryService` / `PlayerDirectory`）。
`bootstrap_core()` が作るのは、ボードごとに要る／台帳を持つもの（`SetLoadService` /
`TournamentInfoService` / `PlayerService` / `CrewBattleService` / `AutonaActions` / `StreamService` /
`TournamentMediaService` / `TournamentService` / `AnalysisService` / `SetSelectionService` / `ThumbnailService` / `ResultService` /
`MatchNameCandidates` / `AppSettings` / `AssetPackService`）だけ（`HotkeyRules` はモジュール関数で、生成するものを持たない）。

- **`SetLoadService` は best_of を直接書かない**。セットの bestOf は `BestOfFromSetData` で読んで
  `AutonaActions.SetBestOf` に渡し、読み込んだスコアは `team.{t}.score` を書く（ゲームトラッカーの行の追従は
  ADR-005 S4ac-4 でトラッカーごと削除）
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
    キャラ行は組み直した行が今の行と同じなら publish しない（Qt のカードの入力欄・コンボが無変化なら
    emit しなかったのと同じ結果）

- **state が記録、ウィジェットは表示**。ゲームトラッカーの記録 `score.{n}.stages.{i}` を描くだけだった
  Qt の SCORE のボタン（`_SyncStageButtons`。`setChecked()` は `toggled` しか出さず `clicked` は出ないので、
  再描画がエコーループにならなかった）は S4ac-3 で削除し、記録（core の `GameTrackerService`）・v1 の `stages`・
  設定 `general.disable_individual_game_tracker` も S4ac-4 で削除した（`tests/test_web_panel_host.py` の
  `test_the_game_tracker_and_its_switch_are_gone` が pin）
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
  シグナルに `connect` する**（S4ac-3 の時点で、この 4 つへの接続は `AutonaWebServer` の 4 箇所だけ）。observer を直接 `subscribe` してはいけない
  — チャンネルは callback を**強参照**し、bound method でも自動解除されないため、
  破棄済みウィジェットが state を書き続ける（ゴーストボード）。
  `tests/test_signal_lifetime.py` が違反を検出する
- **core / 非 Qt コードだけが `StateManager.observers.<名前>.subscribe(cb)` を使う**。
  寿命が短い購読者は自分で `unsubscribe()` する
- `ObserverChannel.notify()` は購読順・同期・**購読者ごとの例外隔離**
  （例外は logger.exception でログして次の購読者へ進む。書き込み側は落ちない）
- `StateManagerBridge` は process 内で 1 個だけ（`bootstrap_core()` が生成。
  2 個目の生成は `__init__` が RuntimeError で拒否する）
- `hotkeys_changed`（引数なし。ADR-005 S4ae）: core の `HotkeyRules.changes`（保存の後、`SettingsManager.lock` の外で 1 回）を Bridge が変換する。`bootstrap_gui()` が `AutonaHotkeys.ConnectReload(StateManagerBridge.instance.hotkeys_changed)` で、GUI スレッドにいる `AutonaHotkeys.ReloadHotkeys`（bound method。queued）に 1 回だけ繋ぐ（`_reload_connected` が二重を防ぐ）。Flask のスレッドからの保存でも OS への登録は GUI スレッドで走る（`RegisterHotKey` は呼んだスレッドに付くため）。ヘッドレスには受け手が無い

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

- `AutonaWebServer.WebServer` は素の Python クラスで、`start()` が `threading.Thread`（daemon）で werkzeug の threaded サーバー（`make_server(threaded=True).serve_forever()`。旧 `socketio.run()` が threading モードで組むものと同じ）を回す（ADR-005 S3c。旧: QThread）。
  - **werkzeug のサーバーは `WebServer._MakeListener` だけが作る**（本体もプレビュー用のリスナーも。SF-8a の後の修正）: app を `WebSocketHandover` で包み、`request_handler=WebServerRequestHandler`。threading モードの engineio は WebSocket を simple-websocket で werkzeug のソケット（`environ["werkzeug.socket"]`）の上に張り、終わると `start_response` を呼ばずに `[]` を返す（gunicorn にだけ `StopIteration` で知らせる）。素の werkzeug はそれを `write() before start_response` の AssertionError（切断のたびに error のログ）にし、WebSocket の close フレームの後ろに 500 の応答を書いていた（ブラウザの「Invalid frame header」。本体のポートでも SF-8a より前から）。`WebSocketHandover` は、WebSocket のアップグレードの要求で `start_response` が呼ばれずに app が戻ったら `ConnectionError`（werkzeug の切断扱い。Flask-Sock の werkzeug での扱いと同じ）を送出し、`WebServerRequestHandler.connection_dropped` が `close_connection` を立てて接続を閉じる（立てないと HTTP/1.1 のアップグレードの要求なので次の要求を待ち、クライアントに FIN が届かない）。`make_server` を直接呼ばない。pin は `tests/test_web_access.py` の `test_a_websocket_ends_with_nothing_after_its_close_frame`（両リスナー × クライアント / サーバーからの close。close フレームの後ろに何も無い・EOF・werkzeug の error のログ無し）`stop(timeout)` が `shutdown()` で止める（旧 `QThread.terminate()` の強制終了は廃止）。起動失敗（bind 不能など）はコンストラクタの `on_start_failed(error: str)` コールバックにサーバースレッドから通知する（デスクトップは `gui_executor` で GUI スレッドへ渡してダイアログ表示）。ポート衝突の事前確認 `check_webserver_port()` は QMessageBox を出すのでデスクトップ専用
- **Socket.IO の delta（`program_state_update`）は 1 本の flusher スレッド（`AutonaWebServerDeltas`・daemon）が送る**（ADR-005 S3f-5。旧: バッチごとの `threading.Timer`）。`start()` がサーバースレッドより先に起こし、`stop()` がサーバーの停止の後に、開いているバッチをすぐ送らせて join する（残り時間、ただし最低 `DELTA_STOP_MIN_SEC`）
  - delta を受け付けるのは `start()` から `stop()` の間だけ。前（受け手がいない）と後（送るスレッドが無い）の delta は捨てる
  - `on_program_state_update` は `StateManager.lock` の中で走りうるので、キューに積んで notify するだけにする。送出は flusher が lock の外で行う。ここに待ちを足さない（agent-pitfalls #17）
  - `stop()` を `StateManager.lock` を持ったまま呼ばない（flusher の全量送信へのフォールバックが state を読むので、join が期限まで待つ）
  - 間隔（先頭 0・50 / 100 / 200 ms）と payload は外部契約。`tests/test_webserver_delta_debounce.py` が偽の時計（`WebServer._delta_clock`）で pin している
  - 例外は 1 つ: バッチの送信を待つもの（`_after_pending_deltas`。ゲームの切り替えの全量送信）があると、開いているバッチはすぐ送られる（窓を待たない。ADR-005 S3f-4）。`start()` のやり直しでも、待っていたものは捨てずに新しい flusher がすぐ走らせる
- **未反映の値の通知 `scoreboard_pending`（`{board, dirty}`。値は運ばない）も同じ flusher が送る**（ADR-005 S4u）。`__init__` が Bridge の `held_state_updated` / `held_dirty_changed` を `on_held_change` に繋ぐ。これも `StateManager.lock` の中で走るので、`_delta_cond` の中で積むだけ。`dirty` は送信時に（`_delta_cond` の外で、`StateManager.lock` を取らずに`PeekPendingChanges` で）読む。flusher は全クライアントの delta を送るので保存サイクルの後ろで止まらない。値を運ばない合図で、変われば次の通知が必ず来るので lock なしで足りる。読み手は「最後の通知より後に始めた `GET /scoreboards/{n}/pending`」だけを信じればよい。1 窓に 1 ボード 1 通、同じ送信の delta の後。間隔は通知自身のもの（`_held_notice_ts`）で、delta の `_delta_last_flush_ts` は動かさない。ただし窓は共有で、通知の窓が開いている間に来た delta はその窓に合流する: 先頭で出られたものは最大 50 ms 遅れ、自分の debounce を待つはずだったものは早まる。送信先は `_ws_trusted_rooms()`（`GET .../pending` と同じ権限）
- **追従の通知 `scoreboard_follow`（`{board}`。値は運ばない）も同じ flusher が送る**（ADR-005 S4v）。`SetFollowService.changes` を Bridge が `follow_changed` に変換し、`__init__` がそれを `on_follow_change` に繋ぐ（`scoreboard_pending` と同じ届き方。Qt 側がチャンネルを直接購読しないのは `tests/test_signal_lifetime.py` の規則）。ボードの操作の中で出るので `_delta_cond` の中で積むだけ。開いている窓があれば合流し、無ければすぐ出る（自分の間隔は持たない: 操作者の操作か読み込みが 1 つずつ起こすもので、1 回の送信に 1 ボード 1 通にまとまる）。同じ送信の delta と `scoreboard_pending` の後。送信先は全接続（`GET .../follow` が READ で、通知はボードの番号だけ）。読み手は通知の後に始めた `GET .../follow` を使う
- **対戦形式の通知 `battle_format`（`{}`。値は運ばない）も同じ flusher が送る**（ADR-005 S4z）。`AutonaActions.battleFormatChanges` を Bridge が `battle_format_changed` に変換し、`__init__` がそれを `on_battle_format_change` に繋ぐ（`scoreboard_follow` と同じ届き方・同じ規則: `_delta_cond` の中で積むだけ・自分の間隔は持たない・1 回の送信の間に何度切り替わっても 1 通）。同じ送信の delta・`scoreboard_pending`・`scoreboard_follow` の後。送信先は全接続（`GET /battle-format` が READ）。いまの形式への切り替えは `SetBattleFormat` が知らせないので出ない。読み手は通知の後に始めた `GET /battle-format` を使う
- **アプリの実行中の仕事 `status`（`{revision, jobs}`。値を運ぶ）も同じ flusher が送る**（ADR-005 S-final SF-2）。`core/StatusService.changes` を Bridge が `status_changed` に変換し、`__init__` がそれを `on_status_change` に繋ぐ（デスクトップのフッター `Window._on_status_changed` も同じシグナル）。`_delta_cond` の中で積むだけ（アセットのロードが `GameAssetCatalog` の writer lock の中から報告するので、ここでブロックしない）。値は送る時点の `StatusService.Snapshot()`。`scoreboard_pending` と同じく自分の静かな間隔（`_status_notice_ts`・50 ms）を持ち、続けて変わると窓ごとに 1 通。同じ送信の delta・`scoreboard_pending`・`scoreboard_follow`・`battle_format` の後。送信先は全接続（`GET /status` が READ）。加えて `ws_connect` が新しい接続に 1 回送る（`program_state` の後）。文言はコード + 引数（`StatusService.CODES`）で、読み手が訳す
- **ゲーム一覧の通知 `games_changed`（`{}`。値は運ばない）も同じ flusher が送る**（ADR-005 S-final SF-3a）。シェルのスキャンのシグナル（デスクトップはアセットアダプタの `onLoadAssets`、ヘッドレスはローダーの `gamesScanned`。どちらもスキャンのジョブから emit され、素の関数への接続なのでメインスレッドに queued で届く（`_delta_cond` で守られているのでどのスレッドでも安全））を `bootstrap.wire_web_server` が `on_games_change` に繋ぐ（`battle_format` と同じ規則: `_delta_cond` の中で積むだけ・自分の間隔は持たない・1 回の送信の間に何度スキャンしても 1 通）。同じ送信の delta・`scoreboard_pending`・`scoreboard_follow`・`battle_format` の後。送信先は全接続（`GET /games` が READ）。`games` は一覧の要求と答えのイベントで使っているので別の名前。読み手は通知の後に始めた `GET /games` を使う
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
    スコアボード dock から移した。セット選択 / 配信台のウィンドウも S4ac-3 で削除するまではこれが持っていた）の `OpenSetSelection` / `RenderThumbnail` /
    `StartImageRender`（GUI スレッドで同期実行。`StartImageRender` は開始までで、描画の完了は待たない）を登録し、
    ヘッドレスは何も登録しない。`RenderThumbnail` はボードを構築しない（`ThumbnailService.DrawBoard` に同期の
    描き手 `DrawThumbnailNow` を渡す。X 投稿のクリップボードの画像も同じ入口）。`OpenSetSelection` は
    ホットキーと同じくページのダイアログを開き（ADR-005 S4ab-2）、`Prepare` を呼ばない（ダイアログからの
    読み込みは v1 の `:load-set` で、そのボードホストが準備する）。`n` が形式のボードと違う（1v1 での `/scoreboard2-open-set`）ときは
    何も開かずログだけ。答えはどちらも `"OK"`（ページへの経路の例外もログだけで `"OK"`）。メインウィンドウができる前もログだけ。退路の Qt モード
    （Qt のウィンドウを開き、ボードホストの `Prepare` で未構築のボードを先に作っていた）は S4ac-3 で削除した。未登録なら
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
    台帳テスト（`tests/test_webserver_routes.py`）が「全 endpoint・全イベントが分類済み」と分類規則（v1 の GET = READ、それ以外 = WRITE 等。例外は選手 DB の行を返す `GET /players`・`GET /players/entrants` で PLAYERDB（ADR-005 S4m）、未公開の編集を返す `GET /scoreboards/{n}/pending` で PLAYERDB（S4u）、反映前の値と選手の X ハンドルで組む `GET /scoreboards/{n}/post` で PLAYERDB（S4x）、絶対パスを返す `GET /layouts/selected/board/obs-path` で PLAYERDB、この PC のウィンドウを開く `POST /layouts/selected/board:reveal` で THIS_PC（S4s）、`out/` の絶対パスを返す接続情報 `GET /connection` で THIS_PC（S-final SF-3a）、ゲームアセットのパックの一覧 `GET /assets/packs`（読むと GitHub から取りにいく）とジョブの状態 `GET /assets/job` で THIS_PC（S-final SF-3b））を pin する。
    分類のない名前は WRITE 扱い（開かない側に倒れる）。GET でも状態を変える legacy ルートは WRITE（効果で分類する）
  - Socket.IO のハンドラは `socketio.on` ではなく `ws_on` で登録する（`socketio.on` で直接登録すると判定を素通りする）
  - WRITE / PLAYERDB: この PC（ループバックか、この PC の LAN IP。`GetLocalIPs()` を初回に引いて保持）からはトークン不要、
    LAN の他端末はトークン必須（`X-Autona-Token` / `Authorization: Bearer` / `?token=`。Socket.IO は接続時の `auth: {token}`）。
    トークンなしで通すのは次をすべて満たすときだけ（`WebAccess._AmbientRefusal`）:
    転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP`）が無い（ローカルのプロキシ・トンネルの中継は他端末扱い）、
    `Host` がこのサーバーの名前と待ち受けポート（DNS rebinding 対策）、`Sec-Fetch-Site` が無いか `same-origin` / `none`、
    `Origin` が無いか自分のオリジン（`null` は不可）。
    拒否は v1 のエラーエンベロープ（401 `UNAUTHENTICATED` / 403 `FORBIDDEN`。Socket.IO はイベントの ack）
  - THIS_PC: 上のトークンなしの判定を通った要求（`GRANT_AMBIENT`）だけを通す。ほかは**トークンがあっても** 403 `FORBIDDEN`（`WebAccess.THIS_PC_ONLY`。`_guard_http_request`）。配信 PC の画面にウィンドウを開く操作のためで（ADR-005 S4s。「エクスプローラで開く」）、シェル操作にはせず core に置く。接続情報（`GET /connection`。`out/` の絶対パス）も THIS_PC（ADR-005 S-final SF-3a。応答は `Cache-Control: no-store`）。**THIS_PC は秘密を守れない**: 同じオリジンのページ（`layout/` のレイアウト。OBS のブラウザソースや直接開いたブラウザ）もトークンなしの判定を通るので、第三者製のレイアウトも読める（`obs-path` の PLAYERDB と同じ）。だからアクセストークンを v1 で配らない（SF-3a で外した。経路は SF-4a で、シェルが共有ビューのメインフレームにだけ渡す秘密を求める形にする。プレビューの iframe の分離（SF-8a）ではこれは閉じない）。OS の操作を足すなら、クライアントからパスを受けず、シェルを通さない（`shell=True` にしない）。実行ファイルは名前で探させない。Explorer は自前で引数を読みカンマで区切るので、`LayoutCatalog._ExplorerSelect` は list 形でなく引用符つきの文字列で渡す
  - トークンは `WebAccess.Token()` が初回に読み（無ければ発行）、以後メモリに持つ。変えるのは `RegenerateToken()` だけ。
    settings.json を読めなかった（`SettingsManager.loadFailed`）・値が壊れているときは発行せず `""`（LAN の書き込みは全部拒否）。
    読めなかった間の `""` は持たない: どこかの書き込みが読み直すと、次の `Token()` がファイルのトークンを読む（壊れたファイルを退避したなら発行する）。
    再発行（`_IssueToken`）は `SetMany` で、保存できなければ発行しない
  - Socket.IO は接続を全部受け入れて state を配る（OBS の file:// オーバーレイのため）。書き込み可かどうかは接続時（handshake の
    要求のヘッダ）に決めて `request.environ` に持つ。トークンなしで通れる接続はトークンを送ってきても ambient 扱いで、再発行で切れない。
    トークンで入った接続だけを世代で切る。選手 DB の broadcast は信頼済みの room にだけ送る
  - 静的配信は `StaticPath()` の許可リストだけ（`layout/`・`assets/`・`web/build/`・`out/`・`user_data/` の画像フォルダ・
    `tournament_banner.png`）。Flask の `static_folder` は無効。許可リスト外は 404 + 種類ごとに初回 warning
  - **`out/` への画像の出力も値を信じない**（`StateManager._ExportKind`）。state の文字列は運用者や start.gg が打った名前でありうるので、`./` のコピーは `StaticPath()` が許す（= すでに配信している）画像の拡張子（`EXPORT_IMAGE_EXTENSIONS`）だけ、ダウンロードは `https` + `EXPORT_IMAGE_HOSTS`（start.gg の画像ホスト）+ png / jpg だけ。ほかはただの文字として `.txt` に出す。新しい絵の置き場所を足すなら、`StaticPath()` の許可リストと `tests/test_state_manager_image_export.py` の固定に足す
  - Web UI の開発サーバー（`npm start`、localhost:3100）は別オリジンなので、書き込みには `?token=<token>` 付きで開く
- **このサーバーの URL・ポート・LAN 接続を言うものは全部 `WebServer.Serving()` を読む**（ADR-005 S4af）: 実際に待ち受けている値（`Listening()`）で、サーバーが無いときだけ設定（次の起動で bind する値）。保存したポート・LAN 接続は再起動で効くので、それまでは今の値を案内する。LAN の未決定（None）は、待ち受けの値でも設定でも False。読み手は共有ビューのブラウザ版の URL・統計表示設定ページの Companion の URL・`AutonaWebPageLifecycle.LocalUrl`・API / OBS / 連携のガイド・`GetWebServerHost`。LAN を有効で保存したが走っているサーバーはこの PC だけ（再起動で効く）かは `WebServer.LanOnAtRestart()`（ガイドが「保存済み・再起動で有効」と言う）。設定のポート・LAN を読むのはサーバー自身（bind と `Serving` のフォールバック）だけで、`Listening()` を呼ぶのは `Serving` と `bootstrap_core()`（`AppSettings` の注入）だけ（`tests/test_api_v1_settings.py` の `test_every_url_naming_the_server_reads_it_through_serving` が AST で pin。初回起動の LAN の確認（`src/Autona.py` の `_StartWebServer`）とヘッドレスのログ（`src/headless.py` の `Assemble`）は「未決定」を見るために LAN の設定を `Get(..., None)` で読むので、その 2 つの読みだけを除く）
- **プレビュー用のリスナー**（ADR-005 S-final SF-8a）: `_Serve` が本体の bind の後に 2 本目のサーバー（`_MakeListener(make_server, <本体と同じホスト>, 0, PreviewGate(app))`。本体と同じ作り方）を立てる（ポートは OS が選ぶ。スレッド `AutonaWebServerPreview`・daemon。`stop()` が本体と同時に shutdown して join する）。ポートは `WebServer.PreviewPort()`（v1 `/health` の `previewPort`）。bind できなければ warning でプレビュー無し（本体の起動は止めない。本体のオリジンに代えない）。本体の `serve_forever()` が例外で抜けたら、プレビューも shutdown・close して `PreviewPort()` を None に戻す（`_DropPreview`。`stop()` が始まっていれば `stop()` が閉じる。二重にしない）。`PreviewGate` は要求の WSGI environ に `autona.preview` を立てるだけ（クライアントはヘッダで environ のドット付きキーを作れない。ヘッダは `HTTP_*` になる）
  - **その要求は Authorize に入れない**: 素朴に通すと `OwnOrigins(SERVER_PORT)` がそのポートのページを自分のオリジンにして、トークンなしの書き込みが通る。`_guard_http_request` の先頭で `WebAccess.PreviewServes(endpoint, method, filename)` だけで決める（v1 の READ と、`PreviewStaticPath` の静的ファイル（`layout/`・`assets/`・`user_data/` の画像・`out/program_state.json`）の GET / HEAD / OPTIONS だけ）。ほかは全部（GUARDED・DESIGNER・`/web`・legacy の READ・`out/` のほかのファイル・404 になる URL・プリフライト）**トークンがあっても** `WebAccess.PREVIEW_ONLY`（403 `FORBIDDEN`）。`_authorize`・`_authorize_ws_connection`・Designer の 2 つのガードも先頭で断る。Socket.IO は接続を受けて state を配り、grant なしなので書き込みイベントは `_ws_guard` が `PREVIEW_ONLY` の ack で断る（信頼済みの room に入れない）
  - 第 2 の表は持たない: プレビューに出るかは `ROUTE_ACCESS` の分類から決まる（v1 の GET = READ はプレビューにも出る）。プレビューに出してはいけない読み取りを足すなら分類（PLAYERDB / THIS_PC）で扱う。`tests/test_web_access.py` が全ルート × 全メソッドを歩いて pin する
  - LAN: 本体と同じバインド先（LAN 接続なら `0.0.0.0`）。出すのは本体がトークンなしで LAN に出している読み取りの部分集合なので、露出は増えない。Windows のファイアウォールでポートを個別に開けている環境では、別 PC のブラウザのプレビューだけが届かないことがある
- **本体のポートは、別のページが頼んだフレームに何も出さない**（SF-8a のレビュー）: sandbox の `allow-scripts allow-same-origin` は自己ナビゲーション（`location.href`）も入れ子の iframe（本物のオリジンになる）も止めないので、プレビューのレイアウトのスクリプトが自分のフレームか作ったフレームを本体のポートへ送れば、そこで本体のページとして動く（共有ビューの DOM・トークンなしの書き込み）。だから:
  - `_guard_http_request` が（プレビューの判定の次、Authorize の前に）`WebAccess.FramedElsewhere(Sec-Fetch-Dest, Sec-Fetch-Site)` の要求を `WebAccess.FRAMED_ELSEWHERE`（403 `FORBIDDEN`）で断る: `Sec-Fetch-Dest` がフレーム（`iframe` / `frame` / `embed` / `object` / `fencedframe`）で、`Sec-Fetch-Site` が `same-origin` / `none` 以外（プレビューのポートも LAN のページのプレビューも same-site、ほかは cross-site）。mode は見ない（`<object>` / `<embed>` は no-cors でも本体のオリジンの文書になりうる）。対象は本体が出すすべて（`layout/`・`/web`・`web/build`・静的ファイル・v1・legacy・404 の URL）で、トークンがあっても断る。断った応答は `Content-Security-Policy: sandbox; frame-ancestors 'none'`（`REFUSED_FRAME_POLICY`）でどのフレームにも出ず、本体のオリジンの文書にもならない
  - 本体の応答はすべて `Content-Security-Policy: frame-ancestors 'self'`（`FRAME_ANCESTORS`。after_request で `add`）。入れ子のフレームは祖先にプレビューのオリジンが入るので、Sec-Fetch-* を送らないブラウザでも出ない。プレビュー用のリスナーの応答には付けない（共有ビューが枠に入れるページなので）
  - 通るもの: 本体のページが本体を枠に入れる（same-origin）・直接開く文書（OBS のブラウザソース・リンク。`Sec-Fetch-Dest: document`）・Sec-Fetch-* を送らないクライアント（Companion・curl）。`file://` のページが本体のページを iframe に入れる使い方は出なくなる
  - 前提: 「プレビューから本体へ書けない」「本体のポートをフレームに出さない」は `Sec-Fetch-Site` / `Sec-Fetch-Dest` を送るブラウザ（Chromium 系・Firefox・Safari の現行版）が前提。送らない古いブラウザでは no-cors の GET の書き込み（legacy の GET の書き込みルート）が ambient で通りうる（プレビューが同じオリジンだった SF-8a 以前より悪化はしない。`frame-ancestors` は効く）。共有ビューの QtWebEngine（Chromium）は送るので対象外
  - `tests/test_web_access.py` が mode × dest × site の表・全ルート・LAN のアドレス・応答のヘッダを、`web/e2e/preview-origin.spec.ts` がプレビューのフレームの自己ナビゲーションと入れ子の iframe を pin する
- **デザインのプレビュー（iframe。`web/src/CommentaryPage/DesignPane.tsx`。実況・Top 8・BOARD・統計表示設定のページ）はプレビュー用のリスナーから読む**（SF-8a）: `src` は `http://${location.hostname}:${previewPort}/layout/...`、`sandbox="allow-scripts allow-same-origin"`。実在の別オリジンなので、S4af で opaque origin（`sandbox="allow-scripts"` だけ）が壊したもの（レイアウトの JS の `cssRules` の SecurityError・canvas の `toDataURL`・`localStorage`。実機で `scoreboard_simple` の P1 側とフェーズ表示が出なかった）はそのまま動き、同じサーバーへの書き込みとページの中身への手出しは閉じる（台帳の S4s の直さず記録する点 9・S4af の判断 7）
  - `previewPort` が無ければ iframe を出さずに案内する（同じオリジンに代えない）。マウント時と、ページの Socket.IO の（再）接続のたびに `/health` を読み直す（アプリの再起動でポートが変わる・失敗した読みを取り直す。最後に頼んだ答えだけを使う）
  - iframe がキーボードのフォーカスを持ったら（親の window の `blur` と、ウィンドウが非アクティブなときのための 250 ms のポーリング）その iframe を `flushSync` で外して案内し、直前のフォーカスに戻す（別のデザイン・再読み込みで戻る。A → B → A と戻っても出る。ポートを見失って取り戻しただけでは戻らない）。`inert`・Permissions Policy・フォーカスの取り返しでは打鍵の横取りを止められなかった（SF-8 の spike）
  - Web 側に `message` のリスナーを足さない
  - 第三者のレイアウトで本体のポート（`localhost:5107` など）の絶対 URL や legacy の読み取りルートを使うものは、プレビューでだけ崩れうる（OBS では今までどおり）
- CORS は Flask-CORS で全オリジン許可のまま（読み取りは別オリジンからも読める）。**例外: `/api/designer/imports`** は Origin を `null` / figma.com に限定し、ペアリングコード（`X-Autona-Pairing` ヘッダ）が必須。
  `/api/designer/*` は Designer トークン（ループバック限定）とペアリングコードの独自ガードのままで、S3a の判定は通さない（DESIGNER）
- **デスクトップの共有ビュー（`AutonaWebPanelHost`）にはこのサーバーの Web UI（`/web` とその配下）しか載せない**（ADR-005 S4e。`/web` に絞ったのは S-final SF-8b のレビュー）。
  ページ自身のオリジンのスクリプトにクリップボードを許しているため（配信サムネ・サムネイル・リザルトの画像コピー、OBS の URL・タイトルのコピー、X 投稿の画像）。
  同じオリジンは `layout/`（第三者製のレイアウト）・`out/`・`assets/`・API も出すので、オリジンだけでは足りない
  - **クリップボードは設定で渡さない**（ADR-005 S-final SF-8b）: `JavascriptCanAccessClipboard` / `JavascriptCanPaste` は明示的に False。
    `_PanelPage._AnswerPermission` が `permissionRequested` に答え、Qt の `ClipboardReadWrite` を `ClipboardPermitted` が許すときだけ grant する:
    頼んだオリジンが本体のサーバーのオリジン（`_ServerUrl` = ホストが読む `LocalUrl` の `127.0.0.1` と待ち受けのポート）ちょうどで、
    ページの URL とホストが読み込んだ URL（`url()` / `requestedUrl()`。http(s) の文書を指すものすべて。about:blank・空は数えない。1 つは要る）とも
    scheme・host・port まで一致し、そのパスが `/web` の配下（`_InWebUi`）。ほかのオリジン（プレビューのフレーム・ループバックの別名やこの PC の LAN アドレスの本体のポート・
    ビューに落とされたリンクで来た別オリジンのページが自分のオリジンで頼むもの）、`/web` の外にいるページ、ほかの権限は全部 deny（warning を出す）。
    判定は QUrl の値だけを読む純関数（`_Origin` / `_Port` と同じ読み方。サーバーの URL は引数で渡す。QApplication も WebEngine も設定も要らない）。
    Qt はフレームの要求をそのフレームのオリジンで渡す（PySide6 6.11 で実測）が、どのフレームかは渡さないので、ページと同じオリジンの子フレームはページと同じに grant される
    （そういうフレームは下のフレームの遷移の規則で作れない）。SF-8b より前は設定で全フレームに渡していて、`allow` で委譲されたプレビューのレイアウトも読み書きできた
  - **`/web` の判定**（`_InWebUi`）: QUrl の `NormalizePathSegments` でドットのセグメントを解いたパスが `/web` か `/web/` で始まり（`/webx` は外）、デコードした後にドットのセグメントと
    バックスラッシュが残らない（QUrl が解かないエンコードされたスラッシュの `/web/..%2flayout`、Chromium がスラッシュと読む `\`）。Chromium は解いた URL を渡すが、それに頼らない
  - **grant を持ち越さない**: 既定プロファイルの永続化ポリシーを `AskEveryTime` にする（`_PanelPage.__init__`。ページが何か読み込む前）。
    Designer（`AutonaDesignerWindow`）は同じ既定プロファイルの `QWebEngineView` でこのサーバーの `/web` を出し、要求に答えない。
    Qt の既定（`StoreInMemory`）のままだと、共有ビューの grant が Designer の同じオリジンの要求にも黙って効く（実測）。漏れ先は Designer だけ:
    オフスクリーン描画（サムネ・リザルト）も既定プロファイルだが file:// のページ（別のオリジン）を読むので、もともと届かない。
    Designer はクリップボードを JS で使わないので、ハンドラは足していない（要求は答えられず、今までどおり止まる）。
    共有ビューを別のプロファイルに移すなら、そのプロファイルに同じポリシーを付ける
  - 残るもの: `ClipboardReadWrite` は読みと書きを分けないので、ページ自身のスクリプトは読むこともできる（パネルは読まない。
    テキスト欄の Ctrl+V・右クリックの貼り付けは編集コマンドで権限と関係ない）。クリックの中の `document.execCommand("copy")` は権限ではなく、
    どのフレームからも書ける（Web の標準。読めない）。`DesignPane` の iframe は `allow` を付けないので、プレビューの `navigator.clipboard` は
    Chromium の Permissions Policy が要求の前に断る（付けても上の判定で deny）
  - クリップボード以外の権限（通知・カメラなど）を使うページを足すなら `ClipboardPermitted` を広げる（今は全部 deny）。
    `tests/test_web_panel_host.py` が判定の表（オリジン・本体のサーバーのオリジン・パス・2 つの URL の組）・設定・ポリシー、実 QtWebEngine での grant・プレビューのフレームの拒否・
    別のビューに grant が届かないこと・ほかのウィンドウにフォーカスが移った後の画像の書き込み（X 投稿の形）・メインフレームが `/web` の外へも別オリジンの `/web` へも移れないことを pin
  - React のパネルに外部へのリンクを置くなら `target="_blank"` にし、同じフレームで外へ遷移させない。ビューは新しいウィンドウを
    作らず、クリックからの（`isUserInitiated`）`http` / `https` の要求は既定のブラウザに渡す（`_PanelPage._OpenOutside`。ADR-005 S4r。X の投稿画面）。ほかは開かない。
    例外は Designer: クリックからの、ページと同じオリジンの `/web?view=designer` ちょうど（`_IsDesignerUrl`。パス・クエリ・フラグメント・userinfo まで見る）だけ、既定のブラウザへ渡さずホストが `AutonaDesignerWindow` を開く（ADR-005 S4s。トークンの発行と破棄は Qt 側のままで、ページにトークンを渡さない）。
    もう 1 つは SCORE ページの「ブラウザ版を開く」: クリックからの、ページと同じオリジンの `/scoreboard` / `/team` ちょうど（`_IsBrowserScoreboardUrl`。クエリ・フラグメント・userinfo が無いこと）だけ、ホストがクリックの時点で URL を組み直して既定のブラウザへ渡す（`BrowserScoreboardUrl`。S4ac-3 で削除した Qt のボードのボタンと同じ形: Web サーバーに届くアドレスと、LAN 接続でトークンがあるときだけ `?token=`。ADR-005 S4aa）。ページはトークンを持たず、ログにはホストだけを出す。
    クリックなしのスクリプトは `JavascriptCanOpenWindows=False` で届かず、Qt が知らないスキームを OS に渡す経路は
    `DisallowUnknownUrlSchemes` で閉じている（シェル操作は増やしていない。`tests/test_web_panel_host.py` が pin）
  - 担保: `_PanelPage.acceptNavigationRequest` が、ページ自身が始めたメインフレームの遷移（リンク・フォーム・スクリプト・リダイレクト・
    ビューに落としたリンク）のうち、本体のサーバーのオリジン（`_ServerUrl`）で、ページのオリジン（`url()` か、最初の文書の前は `requestedUrl()`）の
    `/web` の配下（`_InWebUi`）でないものを拒否する（別オリジンは S4e から。同じオリジンの `layout/` ほかは SF-8b のレビューから）。
    ホスト自身の読み込み（`load()`・破棄されたページの再読み込み）はページのオリジンを問わず通すが（最初の読み込みには守るオリジンがまだ無い）、
    本体のサーバーのオリジンの `/web` の配下だけ（ホストが読むのは `LocalUrl(PanelPath)` だけ。ビューに落としたリンクも同じ種類の `Typed` で来うるので、
    別オリジンの `/web` は種類を問わず断る。SF-8b の 2 回目のレビュー）。
    パネルの切り替えは `history.replaceState` で遷移にならない。
    Qt は拒否した遷移を読み込みの失敗（`loadFinished(False)`）として出すが、文書は残っているので、ホストはそれで再読み込みしない
    （`refusedLast`。`tests/test_web_panel_host.py` が pin）
  - フレームの遷移は通すが、このサーバー自身のオリジン（http(s) のうち、ページのオリジンと、ループバック名・この PC の LAN アドレスの本体のポート =
    `WebAccess.OwnOrigins`。`about:` / `data:` は対象外）へのものは拒否する（SF-8a のレビュー。プレビューのフレームの自己ナビゲーション・レイアウトが作るフレーム）。
    Qt はどのフレームが頼んだかを渡さず、パネルのページは自分のオリジンをフレームに入れないので全部断る。`refusedLast` は立てない
    （フレームの拒否は `loadFinished(False)` を出さない。実測）。ブラウザの Web UI ではサーバー側の判定（上の `FramedElsewhere`）が担保

## Helpers

- `Helpers/` 配下はユーティリティ。各ファイルは単一責務
- `AutonaQtHelper.py` の `GuiExecutor` はワーカースレッドから GUI 操作する際の橋渡し
- `AutonaQtHelper.HeldBackText`（state を描画する入力欄の「描画が残した文字は書き戻さない、打った文字は書く」判定。選手カード・
  ボード 1 / 2 のチーム名・クルーボードが共有していた。ADR-005 S3e-2）は、S4ac-3 で Qt の SCORE が消えて呼び出し側が無くなり、
  S4ad で `tests/test_held_back_text.py` と一緒に削除した。新しい UI は React で作る（ADR-005）
- `AutonaLocaleHelper` はシグナルを持つ。変更通知が必要な場面で使う
  （`AutonaCountryHelper` は Qt モデルの整理 PR2 で削除した。国・地域のデータは `core/CountryCatalog` を読み、
  読み込みはシェルが `CountryCatalog.instance.Load()` を呼ぶ）
- 悪語フィルタの正本は `core/BadWordFilter.py`（Trie ベース。ADR-005 S3e-1）。`BadWordFilter.instance` は `bootstrap_core()` が作り、
  プログラムのロケールと国 → 言語 / 大陸の表を `AutonaLocaleHelper` から callable で注入する（呼ぶたびに読む）。
  転送だけの互換層だった `Helpers/AutonaBadWordFilter` は ADR-005 S3e-3 で削除した（呼び出し側が無くなった。選手の読込は
  `PlayerService.ApplyData` が、実況は S3e-2 から `CommentaryService` が core の `BadWordFilter.instance` を呼ぶ）
- 選手 DB の正本は `core/PlayerDB.py`（`PlayerDB.instance`。ADR-005 S3e-1）。`database` は `lock`（RLock）で守り、外からは
  `Get` / `Contains` / `Items` / `GetPlayerFromTag` / `TournamentPlayers` で読む（dict を直接回さない。entrants のワーカーが書いている）。
  `Get` 系が返す選手 dict は生（GUI 側用。ロックの外で書き換えない）。プロセスの外へ出すもの（`/playerdb`・`ws_playerdb`）は
  `TournamentPlayers()` の深いコピーをシリアライズする。保存はロックの中で JSON 文字列にするだけで、書き込みはロックの外
  （保存専用のロックと世代番号で直列化。mkstemp → `os.replace` を 4 回試行し、だめなら旧実装と同じ上書き。`local_players.json` の
  バイトは旧実装と同じ）。変更は `changed`（ObserverChannel）で
  **ロックの外から**通知する。`AutonaPlayerDB` は `signals.db_updated` だけの Qt アダプタ（QtCore だけ。選手の Qt モデル `model` は
  Qt モデルの整理 PR3 で、補完の proxy `completerModel` / `GetCompleterModel()` は受け手の Qt のカードと一緒に S4ad で削除）で、`bootstrap_core()` の
  `Attach()` が `changed` → `SetupModel` を中継する（process 寿命の static クラスなので `tests/test_signal_lifetime.py` の購読許可リストに入っている）。
  `SetupModel` は GUI スレッドで（他スレッドからは積むだけで待たない: ADR-005 S3f-2）、選んでいるゲーム（`GameAssetCatalog.instance.Current().game` の
  `codename`）の mains を `PlayerDB.NormaliseMains` でその場で正規化してから `db_updated` を出す。呼ばれるのは変更の通知と、ゲームのロードの後
  （デスクトップはアセットアダプタの `onLoad`、ヘッドレスはローダーの `gameChanged`）で、**両シェルで同じ**（PR3 まではデスクトップのモデルの構築の副作用で、
  ヘッドレスは正規化しなかった）
  - `NormaliseMains(codename)` は `PlayerDB.lock` の中で全選手を回し、そのゲームの `[キャラ, スキン, (バリアント)]` を `[キャラ, int スキン, バリアント or ""]` に揃える。
    モデルの構築の癖を残している（`isinstance(main, list) and len == 2 or len == 3` の優先順位・長さ 2 / 3 以外のリストは消さずに飛ばす・
    1 人の途中の例外はログに出してその選手だけ止める）。保存も通知もしない（次の保存で `local_players.json` に出る。`db_updated` は書き換えの有無によらず出る）。
    `tests/test_player_db.py` の `NORMALISE_CASES` と `tests/fixtures/player_db/normalised_golden.json`（モデルの構築から記録）が pin する
  - 選手データの読み書きは `PlayerDB.instance` を呼び、`AutonaPlayerDB` にデータの操作を足さない
