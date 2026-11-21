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

今コードを書く人が従う規則だけを置く。経緯は git 履歴と ADR（`docs/decisions/`）、各モジュールの詳細な挙動はその docstring が正本。

## StateManager

正本は `src/core/StateManager.py`（static クラス）。

- `StateManager.Get(key)` / `Set(key, value)`。キーはドット区切り（`score.1.team.1.player.1.gamerTag`。数値も文字列）。ネスト操作は
  `core/DictHelper.py` の `deep_get` / `deep_set`
- 複数キーを 1 サイクルにまとめるときだけ `with StateManager.SaveBlock():`（単一キーなら不要。空の `changedKeys` は早期 return するので
  包んでも全量 diff にならない。pin: `tests/test_state_manager_held.py`）
- `StateManager.lock`（RLock）で守る。state の dict を直接触らない。held 通知と `state_updated` のハンドラはこの lock の中で走るので、
  その中で別スレッドを待たない（pitfalls #17）
- **core（`src/core`）は Qt も `src/core` 外の `src` モジュールも import しない**（pin: `tests/test_core_qt_free.py`。AST と subprocess の
  `sys.modules`）。core が使う部品は core に置き、Qt・ロケールなどシェルにしか無いものは `bootstrap_core()` から callable で注入する。
  Qt シグナルはアダプタ `src/StateManagerBridge.py` が出す

### 永続化（`src/core/StatePersistence.py`）

- 保存の正本は `user_data/state.json`（`{"state_version": N, "state": {...}}`）。`out/program_state.json` は出力で、場所・形・
  `timestamp` / `revision`・書き込み順は外部契約。書き出しの間隔・読み込み・退避は docstring
- **ロック順は `StateManager.lock` → 保存側の lock の一方向**。`Schedule` は `StateManager.lock` の中で呼ばれるので I/O をしない。
  書き出しスレッドは `StateManager.lock` を取らず Qt に触らない
- 版は封筒にだけ持つ（state の木に `state_version` を入れない。Socket.IO と `program_state.json` に漏れる）
- 保存済み state のキー名・形を変えるときは `src/core/StateMigrations.py` に step を足して `CURRENT_VERSION` を上げる（前方一方向・
  出荷した step は凍結・冪等・`StateSchema` を import しない）。同じ版に残りうるものの起動ごとの掃除は `StateSchema.SeedPanelState()` に置く
- テスト: harness の `settle()` は `StatePersistence.Flush()` まで待つ。`StateManager.Initialize()` を呼ぶテストは tmp CWD で呼ぶ

### held（反映待ち）

- デスクトップのボード 1 / 2 は起動時から held（登録は `EnsureBoard(panel=True)` の `StateSchema.HoldBoard` だけ）。見せていない面への
  core 直結の書き込みも反映待ちになり、SCORE ページの反映で公開する。ヘッドレスは held にしない（反映ボタンが無い）
- **held 配下は「同値なら書かない」**（`StateManager.SetIfChanged`。同値でも `Set` すれば dirty になり反映ボタンが点く）。core 外から呼ばない
  - 例外: `PlayerService` の書き込みは無条件（理由はその docstring。`city` / `id` だけ guard する）
  - 例外の例外: v1 の編集 `PlayerService.Edit`（文字欄・国・地域・シード）/ `EditCharacter` は書く前に比べ、同じ値の要求で反映を
    点けない。シードは 0〜`SEED_MAX` の整数で、変われば `ChangeSince` の seedChanged で統計（アップセット係数）を依頼し、id・表示名は変えない
- held を「書いた分だけ」公開する操作は `StateManager.RecordHeldWrites()` + `CommitHeld(prefix, written)`（`ApplyChanges` は全部を出す）
- Qt のウィジェットが state を描くなら、プログラム的な更新を `QSignalBlocker` で囲む（書き戻しのエコーループ）

## StateSchema（キー定義とシードの正本）

`src/core/StateSchema.py`。**state に「何があるか」の定義をウィジェットにもページにも書かない。**

- `STATS_KEYS`（held exempt）・`DISPLAY_OPTIONS`・起動時シード（`SeedBoard` / `SeedPanelState`）はここが正本
- `SeedBoard(n)` はサブツリーを skeleton に戻す。生きているボードに呼ばない（呼ぶのは `AutonaActions.SeedFreshBoard` の先頭だけ）
- 新しい state キーを足したら `KEY_CATALOG` にも書く（テストで強制できない。**PR レビューで差分を確認する**）。サブツリーのルートは定数
  （`SCORE_ROOT` …）で `KEY_CATALOG` もこれで keying されている
- 静的なキー名（`SCORE_MATCH_FIELDS` / `SCORE_TEAM_INPUT_KEYS` / `PLAYER_INPUT_KEYS` / `TOURNAMENT_INPUT_KEYS`）の文字列は保存済み state の
  キーなので変えない（変えるなら移行 step）。pin: `tests/test_objectname_keypath_contract.py` の `EXPECTED`。`DYNAMIC_KEY_FAMILIES` は空のまま
  （pin: `tests/test_state_schema.py`）
- カラーのシード: `colors.{feature}.*` は `ColorService.SeedColorState()`、大会カラー 2 キーは `SeedPanelState()`。`Window._SeedPanelState()` が
  `SeedPanelState()` / `TournamentInfoService.Seed()` / `SeedColorState()` を 1 つの `SaveBlock` で呼ぶ（ColorService が StateSchema を読むため）

### ボードの作成とシード

- 新しいボードの中身は 1 つの操作 `AutonaActions.SeedFreshBoard(board)`（どのスレッドでも）。入口は `AutonaActions.EnsureBoard(board, panel)`
  （両シェル。1 回だけ作り、2 回目は何もせず False。`BoardEnsured` / `ResetBoards` / 例外時の `ForgetBoard` は docstring）
- **両シェルとも起動時に 3 つとも作る**: `AutonaActions.MakeBoards(panel)`（起動 1 回だけ）。デスクトップは `bootstrap_gui()` が Window より前に
  `panel=True`、ヘッドレスは `src/headless._SeedBoards()` が `panel` なし（held にしない）
- `panel=True` だけがすること: `PublishDisplayOptions()`・`SetFollowService.TurnOffPolling()`（起動のたびに `general.disable_autoupdate` を true）・
  最後に `HoldBoard`（シードより先に登録するとシードが公開されない）
- pin: `tests/test_headless_seed.py`（シード系の関数の呼び出し元を AST で。ボード 1〜3 の全体をデスクトップとヘッドレスで照合）

## SettingsManager（`settings.json`）

`src/core/SettingsManager.py`。`bootstrap_core()` が `Initialize()` を呼ぶ。詳細は各メソッド（`Set` / `SetMany` / `PrepareWrite` / `_Settle`）の docstring。

- **読めなかった起動**: `Initialize()` がその場で数回読み直す（ポート・言語・ホットキー・対戦形式は起動時に 1 回読まれる）。壊れていれば
  `settings.json.unreadable-<日時>.bak` へ退避して既定を書く（`movedAside`。v1 はファイル名だけ返す）。まだ開けなければ `loadFailed` が True の
  まま起動し、**書き込みのたびに SettingsManager が読み直す**（呼び手ごとに避けない）
  - 開けない間: `SetMany` / `PrepareWrite` は `SettingsUnreadable`（`OSError`）を送出して何も変えない。`Set` / `Unset` はメモリだけを変え、
    読めたらその上に順に戻してから書く
  - `loadFailed` は途中で False になりうるので、読めなかった間の判断をキャッシュしない（`WebAccess.Token()` は `""` を持たない）
  - 読むときは UTF-8 BOM を外す。保存は BOM なし
- **書き込みは 2 種類**:
  - 書く値を操作が決める（利用者の選択・state の値・固定値）: `Set` / `Unset` だけでよい
  - 書く値か書くかどうかを保存済みの値から決める: `with SettingsManager.lock:` の最初の文で `PrepareWrite()` を呼ぶ（呼ばないと空の代わりから
    組んだ値でファイルを上書きする）か、`loadFailed` の間は書かない（初回起動の LAN の確認 `_StartWebServer`）。「あれば消す」は `Unset` が
    自分で決める
  - v1 の `write_op` は `SettingsUnreadable` を 409 `NOT_READY` に写す。ほかの仕事の途中で呼ぶもの（`ResetOrgAssets`・`FollowBattleFormat`）は捕まえて warning
- pin: `tests/test_settings_manager_load_failed.py`

## AutonaActions（ドメイン操作の唯一の書き手）

`src/core/Actions.py`。**ここに移した操作は `AutonaActions.instance` 以外が書かない**。どのスレッドから呼んでもよい。

- 操作: スコア・チームカラー・表示トグル・反映 `ApplyChanges`・match バー・チーム名 / LOSERS・`SetBestOf`・人数 `SetRoster`・配信 URL / タイトル・
  スワップ・クリア・対戦形式・X 投稿文 `PostText`・ゲームのロード（`ApplyGameRoster` / `RepublishBestOfs` / `CommitGameLoad`）。board 1 / 2 専用の
  ものは board 3 で False を返し、クルーは `CrewBattleService`
- **クリアとスワップは state の書き換え**（ウィジェット間の値コピーにしない: バリアントと mains が落ちた）
  - Web の `clear_all` は `ResetMatch` → best_of → 人数・キャラ数 → 選手の順（チーム名を残し、人数が mains を決める順を保つ）。`ClearBoard` は v1 の `:reset-all`
  - スワップはスロットを**キー単位**で入れ替え、各 dict 内のキーの位置を保つ（delta のキー順。pin: `tests/test_board_operations.py` と
    `tests/fixtures/board_operations/key_order.json`）。`score.{n}.teamsSwapped` の正本は state
  - id が変わったチームの統計の取り直しは操作が出す（`StatsService.TeamsChanged`）
- `score.{n}.team.{t}.score` の書き手は `AutonaActions` と `SetLoadService.ApplyScores` だけ。手入力は v1 の `score:set` = `SetScore`
  （pin: `tests/test_score_typing.py`）
- 対戦形式 `SetBattleFormat`: 同じ形式なら何も書かず知らせない。変われば設定 → `battleFormatChanges`（Socket.IO の `battle_format`）→
  `ResultService.FollowBattleFormat`。通知は切り替えたスレッドで出る
- best_of の入口はすべて `SetBestOf`（0〜99・変わらなければ書かない）。文言（`best_of_text` / `first_to_text`）は `bootstrap_core()` が
  `AutonaLocaleHelper.FormatMatchText` を注入し、未注入・例外は英語既定にフォールバックする（上げるとセット読込やロードの commit が止まる）
- **ゲームのロードの commit** `CommitGameLoad(written)` は**ロードが書いたキーだけ**を公開する（ローダーの `_Publish` がボードホストの中で、
  例外でも `finally` で）。ほかの書き手の未反映と重なるキーは残す。例外は `StateManager.HeldWriteGroup(authoritative=True)` で書いた固定の値、
  ロードの記録の中で同値でも書く条件は `_SetDecided` ほか。規則の詳細は `CommitGameLoad` / `HeldWriteGroup` / `CommitHeld` の docstring
  （pin: `tests/test_set_follow_flow.py` の golden）
- 入力中の欄の確定は SCORE ページの仕事（欄を確定してから操作を依頼する）

### ボードホストとスレッド

- ルートの行き先は `AutonaWebOperations`。core 直結の Web 操作は呼び出し元（Flask / Socket.IO）のスレッドで core を呼び、`@gui_thread_sync` を持たない
- **ボードホスト `AutonaWebOperations.boardHost` は両シェルで同じ `BoardHost`**: 呼び出し元のスレッドで、1 本の `RLock` の下で 1 つずつ実行する
  （`clear_all` や v1 `PATCH set` の途中に別の書き込みが混ざらない）。新しいボードの操作は `boardHost.Run` を通す（core 直結の 1 回の呼び出しは
  自分の保存ブロックで原子的なので不要）
- **ロックの中でメインスレッドを待たない・`StateManager.lock` を持ったまま取らない**（いちばん外側のロック）。待つもの（`savePlayerToDb` の
  DB 書き込み）は `Run` を抜けてから
- 終了の開始からはメインスレッド以外の `Run` を 503（`SHUTTING_DOWN_MESSAGE`）で断る。終了中に書かなければならない後始末は
  `Run(op, atShutdown=True)`（実例: ローダーの `_Publish`）。ボード 1〜3 以外は操作自身が `NotAvailable`（503）で断る
- デスクトップとヘッドレスの違いは held かどうかだけ

### ホットキー

- `AutonaHotkeys.ConnectActions()` を `bootstrap_gui()` が 1 回だけ結線する。対象ボードは `AutonaActions.ActiveBoard()`
- ボードのホットキーは GUI スレッドで `boardHost.Run` の中から core を呼び、対象ボードもその中で読む（`_runOnActiveBoard`）
- `load_set` は `AutonaShellOperations.ShowSetSelection` → `Window.OpenPanel("scoreboard")` → `OpenScoreSets`（SCORE ページのダイアログ。
  クルーなら何もしない）。経路の例外はログだけでハンドラに漏らさない。`refresh_phase_group` は `Top8Service.RefreshFromProvider` に直結

## ドメインサービス（core）

状態を持つ／ボードごとに要るドメインロジックは core のサービスに置き、Qt 側は注入される薄いアダプタ（`AutonaStatsUtil` /
`AutonaSetFollowUtil` など）に徹する。**各サービスのロック順・スレッド・遅れた答えの扱いはモジュールの docstring が正本**。

| サービス（`src/core/`） | 所有者 | 責務 |
|----------|--------|------|
| `StatsService` | なし（`StatsService(n)`） | 統計の取り直し・取得・アップセット・`last_sets` などの書き込み |
| `SetFollowService` / `SetLoadService` | なし（状態はボード番号ごとにクラス） | セットの読込と追従 / セット読込の正規化（best_of は `SetBestOf` に渡す。面の解決は `DisplayTeam()` だけ） |
| `PlayerService` | なし（path に束縛した一時インスタンス） | 選手スロット配下の書き込み全部（読込・クリア・DB 保存・v1 の `Edit` / `EditCharacter`） |
| `CrewBattleService` | なし | クルーボード（board 3）の書き込み全部（チーム名・色・match バーは `AutonaActions`） |
| `TournamentInfoService` | `.instance` | `tournamentInfo.*`（`themeColor` / `accentColor` は除く） |
| `ColorService` / `CommentaryService` / `Top8Service` | モジュール関数 | 大会・機能別カラー / `commentary.*` / `top8.*` |
| `GameAssetCatalog` / `GameLoader` | `.instance` | ゲームアセットの正本と `game.*` / スキャンとロード（ジョブ 1 本） |
| `CountryCatalog` | `.instance` | 国・地域・カスタム国旗・都市→地域表（state は書かない。リーダーはコピーを返す） |
| `TournamentService` / `TournamentMediaService` | `.instance` | 大会の設定・解除の唯一の経路 / 大会アイコンとバナー |
| `AnalysisService` / `SetSelectionService` | `.instance` | 試合一覧 / セット選択の一覧 |
| `MatchNameCandidates` | `.instance` | match バーの候補（state に入れない） |
| `StreamService` | `.instance` | 配信設定のすべて |
| `MatchLogService` | `.instance` | ボード 1 の公開を `user_data/match_log` に記録（ADR-006 A2） |
| `ThumbnailService` / `ResultService`（基底 `RenderLane`） | `.instance` | 試合サムネイル / 大会リザルト（テンプレート・レイアウトの一覧は `LayoutCatalog` のテンプレートパッケージ。コードに表を持たない。ADR-007 T1） |
| `AppSettings` / `HotkeyRules` | `.instance` / モジュール関数 | 設定ページの表 / ホットキーの規則 |
| `AssetPackService` | `.instance` | ゲームアセットのパックの導入・取り込み |
| `PlayerDirectory` | モジュール関数 | 選手リストの行と記録の保存・削除（state は書かない） |
| `LayoutKit` | モジュール関数 | 「新しいデザインを作る」（`layout/` の新しいフォルダだけに書く。作業フォルダで組んでから改名。ADR-008 D2） |

サービス共通の規則:

- 状態を持たないサービスはモジュール関数。`bootstrap_core()` が作るのはボードごとに要る／台帳を持つものだけ。Qt 依存は callable で注入する
- **識別子で受けて、値は core がカタログから組む**（国は code、キャラは en_name + スキンの行）。1 回の書き込みは `Current()` 1 回の
  スナップショットで解決し、1 つでも断れば何も書かない。入れ子のキー順は `DictHelper.qvariantmap_copy`（外部契約。pin:
  `tests/test_player_slot_publish.py` / `tests/test_player_service.py`）
- 長い取得は `TaskExecutor` のジョブ。ジョブから別のジョブを積まない・待たない（`NestedSubmitError`）。実行中の要求は `BUSY` で断るのが基本
  （例外は大会メディアの `QUEUED` と描画のレーンの予約。各 docstring）。executor 以外のスレッドは `tests/test_task_executor.py` の `THREADS_LEFT` に載せる
- 失敗と空を区別するときは provider に `strict=True` で頼む。進み具合は state の `{status, error, ..., revision}`（呼び出しが戻る前に次の
  revision を publish）、大きな行はメモリ（大会の URL と組で、読むときに照合）
- サービスの lock は自分のフィールドだけを守る。ロック順は `StateManager.lock` → サービスの lock で、state・fetcher・`later`・`PlayerDB` は
  lock を離して呼ぶ
- provider に紐づくメモリは、provider のアダプタが**差し替えた直後に** `Forget()` を直接呼んで捨てる（前に呼ぶと旧 provider の行が新大会の
  結果になりうる）。ゲームが変わったときは `GameAssetCatalog.SelectGame` が `Top8Service` / `ResultService` の `ForgetCharacters()` を直接呼び、
  ボードのキャラはローダーが `AutonaActions.ApplyGameRoster(replaced)` で空にする（`replaced` は引数で渡し、後から `Current()` を読まない）
- 選手の値・パスをログに出さない（ログの sink は全部 `diagnose=False`。pin: `tests/test_api_v1_players.py`）
- **サービスは observer を購読しない**（購読すると破棄済みの購読者が書き続けるゴースト問題を core に持ち込む）
  - **狭い例外**: (1) プロセスと同じ寿命のシングルトン、(2) `Stop()` で購読をやめられる、(3) observer の中では state を読んで処理を予約する
    だけ（`later` に積むか、自分の書き込みスレッドのキューに積む）を満たすものだけが `state_updated` / `state_big_change` を購読してよい。
    state を書かない・待たない・`GameAssetCatalog` の writer を呼ばない（pitfalls #16 #17）。書き込みスレッドを持つなら、キューの lock は
    末端・書き込みスレッドは `StateManager.lock` を取らない・書き込みを待つものを `StateManager.lock` を持ったまま呼ばない
  - 実例は `StreamService` と `MatchLogService`。購読者の集合は `tests/test_bootstrap_split.py` の `STATE_SUBSCRIBERS` が pin しているので、
    増やすならここで判断してから足す

サービス固有で docstring の外にも効く規則:

- `GameAssetCatalog`: ロック順は catalog lock → `StateManager.lock`（observer から writer を呼ばない: pitfalls #16）。「選んだか」は
  `game.selected` で読む。Web のリーダー（`get_games` ほか）は `*Payload()` を返すだけ。ゲームの切り替えは codename で `LoadGameByCodename`
  （位置でロードしない）。ロードの終わりを待つ側は `done=` を使い、`loaded` / `game_loaded` で待たない
  - writer lock を持ったまま `run_sync`・Qt シグナル・observer を呼ばない。その中で呼ばれるのは `SelectGame` の `progress` だけなので、
    渡す側はブロックしない（積むだけ）
  - `done()` は試行を終わらせたスレッド（ジョブレーンなど）で走る。短く済ませ、その中から別のロードを頼まない（同期なら自己デッドロック）。
    メインスレッドからの同期ロードは `RuntimeError`
- `CountryCatalog`: リーダーはコピーを返し、表に書き戻さない。`States()` / `State()` は地域ごとにディスクを見て旗を解決するので、
  `StateManager.lock` の中の照合（選手の読込の地域コード合わせ）は `StateCodes()` を使う
- `SetFollowService`: 何を追従しているかは state に入れない（`tests/test_set_follow_flow.py` の golden が全書き込みを照合する）。読むのは
  `Snapshot()`、合図は `changes`。`fetcher.RemainingMs` はどのスレッドからでも呼べる（`QTimer.remainingTime()` はイベントディスパッチャの無い
  スレッドから呼ぶとプロセスが落ちた）
- `PlayerService`: LOSERS のフラグの書き手（`AutonaActions.SetTeamLosers`・`SetLoadService.ApplyLosers`）は、変わったときだけ同じ SaveBlock で
  `RepublishTeamMergedNames` を呼ぶ（` [L]` は state の `losers` から付く）。統計は読込が自分で 1 回だけ依頼し、`SetSeed` / `SetPlayerId` は依頼しない
- `Top8Service`: state → 選択の逆引き `CharacterPick` はページの `web/src/Top8Page/characterPick.ts` と同じ規則で、片方を変えたらもう片方も
  変える（`characterPick.cases.json` を `tests/test_top8_service.py` と jest が読む）
- `TournamentService`: 大会の設定・解除の入口はどれもここを呼ぶ。`tournament_url_update` は通知だけ（命令として emit しない）
- `PlayerDirectory`: 保存の番号は `PlayerService.NextSaveSequence()` と共有し、`_saveLock` → `PlayerDB.lock` の順で取る
- ゲームトラッカー（`score.{n}.stages`・`general.disable_individual_game_tracker`）は削除済みで戻さない（pin: `tests/test_web_panel_host.py` の
  `test_the_game_tracker_and_its_switch_are_gone`）

## 状態変更の通知（observer + Bridge）

通知の正本は `src/core/Observable.py` の `ObserverChannel`。`StateManager.observers.<名前>` に 4 本ある。

| チャンネル | 引数 | 意味 |
|-----------|------|------|
| `state_big_change` | なし | 全面リロード。頻繁に発行しない |
| `state_updated` | `{revision, delta_index, delta}` | 差分。WebSocket で配信される |
| `held_dirty_changed` | (prefix, bool) | 未反映が出た／無くなった。**合図なので payload を信じず `HasPendingChanges` を読む**（True は lock の外で出て前後しうる。False は `SaveState` が流し切ったときに lock の中で出る） |
| `held_state_updated` | (prefix) | held のライブ値が変わった（保存サイクル単位） |

- **Qt のオブジェクトは `StateManagerBridge.instance` の同名シグナルに `connect` する**。observer を直接 `subscribe` しない（強参照で自動解除
  されず、破棄済みウィジェットが state を書き続ける）。pin: `tests/test_signal_lifetime.py`
- core / 非 Qt コードだけが `subscribe(cb)` を使い、寿命が短ければ自分で `unsubscribe()` する。`notify()` は同期・購読者ごとの例外隔離
- `StateManagerBridge` はプロセスに 1 個（2 個目は RuntimeError）
- `hotkeys_changed`: `bootstrap_gui()` が `AutonaHotkeys.ConnectReload` で GUI スレッドの `ReloadHotkeys` に queued で 1 回だけ繋ぐ
  （`RegisterHotKey` は呼んだスレッドに付く）

`delta` の行（Socket.IO の `program_state_update`）:

- **クライアントは行を 1 つずつ適用する**（行の意味は `web/src/stateDelta.js`。サーバーが複数 revision の行を区切りなしで結合する）。それを成り立たせる
  `SaveState` のオプション `DeepDiff(zip_ordered_iterables=True)` と `Delta(always_include_values=True)` を**外さない**
- `web/src/stateDelta.cases.json` を `tests/test_state_delta_cases.py` と `web/src/stateDelta.test.js` の両方が読む（deepdiff を上げて行が
  変わったらここが落ちる）
- `None` との間の変更は行に出ない（`exclude_types`）。state のリストや、ページに届けたい値に `None` を使わない
- OBS オーバーレイの `layout/include/globals.js`（`applyDelta`）も同じ規則で行を適用する（`iterable_item_removed` はリストを縮める。`stateDelta.cases.json` が両方の照合の元）

## Web サーバー（Flask + SocketIO）

- `AutonaWebServer.WebServer` は素の Python クラス。`start()` が daemon スレッドで werkzeug の threaded サーバーを回し、`stop(timeout)` が
  `shutdown()` で止める。起動失敗は `on_start_failed(error)` をサーバースレッドから呼ぶ。`check_webserver_port()` はデスクトップ専用
- **werkzeug のサーバーは `WebServer._MakeListener` だけが作る**（`make_server` を直接呼ばない。`WebSocketHandover` と `WebServerRequestHandler`
  が WebSocket の終わりを切断として扱う。素の werkzeug は close フレームの後ろに 500 を書いていた）。pin: `tests/test_web_access.py` の
  `test_a_websocket_ends_with_nothing_after_its_close_frame`

### Socket.IO の送信（flusher）

- delta（`program_state_update`）と下の通知は 1 本の flusher スレッド（`AutonaWebServerDeltas`）が送る。受け付けるのは `start()` から `stop()` の間だけ
- 受け手（`on_program_state_update` / `on_held_change` / `on_follow_change` / `on_battle_format_change` / `on_status_change` / `on_games_change`）は
  `StateManager.lock` や catalog の writer lock の中で走りうるので、`_delta_cond` の中で積むだけ。送出は flusher が lock の外で（pitfalls #17）。
  `stop()` を `StateManager.lock` を持ったまま呼ばない
- delta の間隔（先頭 0・50 / 100 / 200 ms）と payload は外部契約（pin: `tests/test_webserver_delta_debounce.py`）。バッチの送信を待つもの
  （`_after_pending_deltas`）があれば開いているバッチをすぐ送る
- 同じ送信の中の順は delta → `scoreboard_pending` → `scoreboard_follow` → `battle_format` → `status` → `games_changed`

| 通知 | payload | 出どころ（Bridge） | 自分の間隔 | 送信先 |
|------|---------|--------------------|-----------|--------|
| `scoreboard_pending` | `{board, dirty}` | `held_*` | あり（delta と窓を共有） | 信頼済み（`_ws_trusted_rooms()`） |
| `scoreboard_follow` | `{board}` | `follow_changed` | なし | 全接続 |
| `battle_format` | `{}` | `battle_format_changed` | なし | 全接続 |
| `status` | `{revision, jobs}`（値を運ぶ。文言はコード + 引数） | `status_changed` | あり（50 ms） | 全接続。`ws_connect` も 1 回 |
| `games_changed` | `{}` | `games_scanned`（`bootstrap.wire_web_server`） | なし | 全接続 |

- 値を運ばない通知の読み手は、最後の通知より後に始めた GET（`/scoreboards/{n}/pending`・`/follow`・`/battle-format`・`/games`）だけを信じる
- **ゲームの切り替えの全量送信**（`ws_game_changed`）は、ロードの公開（`StateManager.WhenPublished`）とその delta の送信の後に出す（R+1 の delta が
  先、全量の revision は中身と一致）。コールバックは `gui_executor.later` で main に戻してから積む（直接積むと前のロードの全量が次の delta を追い越した）

### ルートと Web の操作

- ルートハンドラの行き先は `src/AutonaWebOperations.py` の 1 つだけ。デスクトップとヘッドレスが同じ関数を通り、違いはシェルが登録するもの
  だけ: `boardHost`・`loadGameByCodename` / `turnModsOff`（`bootstrap_core()` が両シェル共通で登録）・シェル操作
- **シェル操作** `openSetSelection` / `renderThumbnail` / `startImageRender` はデスクトップだけが `AutonaShellOperations` を登録する。ヘッドレスは
  503 `UNAVAILABLE`（legacy・Socket.IO の `_ws_unavailable`・v1 の `/thumbnail:*` / `/result:*` が同じ形）で、ヘッドレスで 503 になるのはこれら
  だけ（pin: `tests/test_web_operations.py` の `test_every_route_and_event_answers_headless_but_the_shell_operations`）
- **シェル操作を増やさない**: 新しい操作は core に置き、どちらのシェルでも動かす（例外は `startImageRender` だけ。理由はサムネイルの描画がデスクトップの Qt に残るため）。
  `@gui_thread_sync` もシェル操作以外に足さない
- 新しいエンドポイントの操作は core に置いて `AutonaWebOperations` 経由で呼ぶ。例外: state を読み書きしない読み取り専用のカタログ
  （`GET /api/v1/layouts` → `core/LayoutCatalog.py`。kind `result` / `thumbnail` のテンプレートパッケージも。ADR-007 T1）はルートから
  直接呼ぶ（ADR-001 Addendum）。カタログは呼ばれたときに `layout/` を読むので、`StateManager.lock` の中で呼ばない（`ResultService` は
  レイアウトのエントリを lock の外で読んでから渡す: `LayoutEntries()` / `entries=`）。読むフォルダは `LayoutCatalog.Root()` の 1 つだけ
  （`bootstrap_core()` が `SetAppDirectory` でアプリのディレクトリ `AutonaDirHelper.current_dir` を渡す）。サービス・レンダラー・ルートで
  別のフォルダを組まない
- v1 の拒否の `error.reason`（`external-control-api.md` §2.5）: ゲームアセットのルートは `AutonaApiV1._ASSET_REFUSALS` が core のコードを入れ、
  core の文はログにだけ出す（パスを含みうる）。core にコードを足したら表にも足す（pin: `tests/test_api_v1_assets.py`）
- 本文を JSON として読まない書き込みは `write_op(..., body="none")`（操作ログが本文を読まない・`upload_id` はログで伏せる）。大きな本文を受ける
  ルートは `request.max_content_length` を要求ごとに設定し、`Content-Length` の無い要求と上限超えを本文を読まずに断る（アプリ全体の
  `MAX_CONTENT_LENGTH` は設定しない）

### 公開面の判定（`src/core/WebAccess.py`）

区分の意味と各ルートの区分は `external-control-api.md` §2 と `WebAccess` の docstring。

- サーバーは `before_request`（`_guard_http_request`）と `ws_on()`（書き込みイベントを `_ws_guard` で包む）から問い合わせるだけ。Socket.IO の
  ハンドラは `socketio.on` でなく `ws_on` で登録する（直接登録すると判定を素通りする）
- **新しいルート / Socket.IO イベントは `ROUTE_ACCESS` / `EVENT_ACCESS` に分類を足す**（READ / WRITE / PLAYERDB / THIS_PC / SHELL / DESIGNER /
  STATIC）。v1 の GET = READ・それ以外 = WRITE、GET でも状態を変える legacy は WRITE、分類のない名前は WRITE 扱い。選手 DB の行・未公開の編集・
  X ハンドル・絶対パスを返す読み取りは PLAYERDB。pin: `tests/test_webserver_routes.py`
- WRITE / PLAYERDB は、この PC からはトークンなし、LAN の他端末はトークン必須。トークンなしで通す条件（転送ヘッダが無い・`Host` が自分・
  `Sec-Fetch-Site` と `Origin` が自分）は `_AmbientRefusal`
- THIS_PC はトークンなしの判定を通った要求だけ（トークンがあっても 403）。配信 PC の画面にウィンドウを開く操作・`out/` の絶対パスを返す
  接続情報・読むと外部（GitHub）へ取りにいく一覧がこの区分。**同じオリジンの `layout/`（第三者製）も通るので秘密を守れない**:
  アクセストークンを THIS_PC のルートで配らない。OS の操作を足すなら、クライアントからパスを受けず、`shell=True` にせず、実行ファイルを
  名前で探させない（Explorer には引用符つきの文字列で渡す: `LayoutCatalog._ExplorerSelect`）
- SHELL は THIS_PC に加えて `Sec-Fetch-Site: same-origin` とシェルの秘密のヘッダが要る（トークンがあっても 403。ログにヘッダの値を出さない）。
  第三者製のレイアウトに読ませてはいけない秘密を返すもの・この PC の構成を変えるもの（ゲームアセットのジョブ）だけを SHELL にする。
  Socket.IO のイベントには使わない。`no-store` を付け、API 一覧 `core/ApiCatalog` に載せない
  - 秘密（`IssueShellSecret()`）とチケット（`IssueShellTicket()`）はプロセスに 1 回メモリにだけ作る（settings.json に入れない・ログに出さない・
    import では作らない）。比較は `hmac.compare_digest`。作るのは `_ShellRequestInterceptor` と `AutonaWebOperations.get_shell_session` だけ
    （pin: `tests/test_web_panel_host.py` / `tests/test_web_access.py` の AST）。ヘッドレスは SHELL が常に 403
- トークンは `WebAccess.Token()` が初回に読みメモリに持つ。変えるのは `RegenerateToken()` だけ。settings.json を読めなかった・壊れているときは
  `""`（LAN の書き込みは全部拒否）
- Socket.IO は接続を全部受け入れて state を配る（OBS の file:// オーバーレイのため）。書き込み可かは接続時に決める。選手 DB の broadcast は
  信頼済みの room にだけ
- 静的配信は `StaticPath()` の許可リストだけ（Flask の `static_folder` は無効）。**`out/` への画像の出力も値を信じない**（`StateManager._ExportKind`:
  許可リストの拡張子・`https` + `EXPORT_IMAGE_HOSTS` だけ）。新しい絵の置き場所は `StaticPath()` と `tests/test_state_manager_image_export.py` に足す
- Web UI の開発サーバー（localhost:3100）は別オリジンなので、書き込みには `?token=` 付きで開く
- CORS は全オリジン許可。例外の `/api/designer/*` は独自ガード（Designer トークン・ペアリングコード）で、上の判定を通さない（DESIGNER）
- `/web/app/<panel>` は `/web/<panel>` と同じ endpoint `web_index` のもう 1 本のルール。新しい枠のルートも別の endpoint にせず同じ判定を通す
  （pin: `tests/test_webserver_routes.py` / `tests/test_web_access.py`）
- **このサーバーの URL・ポート・LAN 接続を言うものは全部 `WebServer.Serving()` を読む**（待ち受けている値。保存した値は再起動で効く）。
  `Listening()` を呼ぶのは `Serving` と `bootstrap_core()` だけ（pin: `tests/test_api_v1_settings.py` の
  `test_every_url_naming_the_server_reads_it_through_serving`）

### プレビュー用のリスナーとフレーム

- 本体の bind の後に 2 本目のサーバー（`_MakeListener(..., 0, PreviewGate(app))`）を立てる。ポートは `WebServer.PreviewPort()`（v1 `/health` の
  `previewPort`）。bind できなければプレビュー無し（本体のオリジンに代えない）
- **プレビューの要求は Authorize に入れない**（入れるとそのポートのページが自分のオリジンになりトークンなしで書ける）:
  `WebAccess.PreviewServes(...)` だけで決め（v1 の READ と `PreviewStaticPath` の静的ファイル）、ほかはトークンがあっても `PREVIEW_ONLY`。
  第 2 の表は持たない（出るかは `ROUTE_ACCESS` の分類で決まる。v1 の GET = READ はプレビューにも出るので、出してはいけない読み取りは
  PLAYERDB / THIS_PC に分類する）
- **本体のポートは別のページが頼んだフレームに何も出さない**（`WebAccess.FramedElsewhere` を `FRAMED_ELSEWHERE` で断り、応答はすべて
  `frame-ancestors 'self'`）。プレビューのスクリプトが自分のフレームを本体のポートへ送れば本体のページとして動くため。pin:
  `tests/test_web_access.py` と `web/e2e/preview-origin.spec.ts`
- **デザインのプレビュー（`web/src/CommentaryPage/DesignPane.tsx`）はプレビュー用のリスナーから読む**（`sandbox="allow-scripts allow-same-origin"`）。
  `previewPort` が無ければ iframe を出さない（同じオリジンに代えない）。iframe がフォーカスを持ったら外す。Web 側に `message` のリスナーを足さない

### 共有ビュー（`AutonaWebPanelHost`）

規則の詳細は `src/AutonaWebPanelHost.py` の docstring（module と `ClipboardPermitted` / `ShellHeaderWanted` / `ParseShellRequest` ほか）と
`external-control-api.md` §5.1（`/shell-session`）。

- **このサーバーの Web UI（`/web` とその配下）しか載せない**（ページのスクリプトにクリップボードを許していて、同じオリジンは第三者製の
  `layout/` も出すため）。遷移の担保は `_PanelPage.acceptNavigationRequest`（拒否した遷移の `loadFinished(False)` で再読み込みしない: `refusedLast`）
- **クリップボードは設定で渡さない**。`ClipboardPermitted` が本体のオリジンの `/web` のページにだけ grant し、ほかの権限・オリジンは deny。
  既定プロファイルの永続化ポリシーは `AskEveryTime`（Designer が同じプロファイルなので、grant が黙って効いた）
- **シェルの秘密を付けるのはページ自身のインターセプタ**（プロファイルに付けない: Designer にも付く）。条件は `ShellHeaderWanted`、合わない要求が
  秘密を持っていたら `block(True)`。インターセプタは UI スレッドで走るので待たない
- **共有ビューの Web UI では、外部・第三者の HTML を sandbox なしの srcdoc / about:blank / innerHTML（`dangerouslySetInnerHTML`）に入れない**:
  同じオリジンの子フレームの fetch にも秘密が付き、チケットも読めるので、そのスクリプトが SHELL のルートとシェル要求を使える。第三者のものは
  別オリジン（プレビュー用のリスナー）か sandbox（`allow-same-origin` なし）のフレームに出し、チケットを渡さない（実 QtWebEngine で pin）
- 外部へのリンクは `target="_blank"`（ホストがクリックからの http(s) を既定のブラウザに渡す）。例外は Designer（`/web?view=designer` →
  `AutonaDesignerWindow`）と「ブラウザ版を開く」（`/scoreboard` / `/team` → ホストが URL を組み直す）で、どちらもページにトークンを渡さない
- **シェル要求**（`alwaysOnTop` / `checkUpdates` / `openExternal` / `pickFolder`）は特権の経路（QWebChannel・注入スクリプト）を作らず、クリックの中で
  `/web/shell-request?verb=…&request=<id>&ticket=…` を開く形だけ（`web/src/PanelShell/hostApi.ts` の `shellRequest`）
  - ホストはちょうどの形（`ParseShellRequest`）だけを受け、ほかのシェル要求に見える URL（`IsShellRequestShaped`）はブラウザへ渡さずに捨てる。
    ログに URL・クエリ・チケットを出さない
  - 実行は `QTimer.singleShot(0)` でイベントループに出てから（pitfalls #9）。答えは頼んだ文書にだけ、スクリプトに入れる値は `json.dumps`
    したものだけ。外部アドレスは `src/AutonaExternalLinks.py` の表のキーだけを受け、URL は受けない
  - 新しい動詞は `SHELL_VERBS`・`AutonaShellOperations.ShellRequest`・hostApi.ts の `ShellVerb` と `SHELL_ANSWER_TIMEOUTS_MS` を揃える
    （pin: `tests/test_web_panel_host.py`）
  - `pickFolder` は引数なし（ページはフォルダを名指せない）。`QFileDialog` を使うのはシェルの `_PickFolder` だけ（AST で pin）。選んだフォルダは
    `AssetPackService.HoldFolderPick` が持ち、ページには id だけ（パスは答えにもログにも入れない）。検査はジョブレーン、答えは GUI スレッドに戻ってから

## Helpers

- `Helpers/` 配下はユーティリティ（単一責務）。`AutonaQtHelper.GuiExecutor` はワーカースレッドから GUI 操作する橋渡し
- 悪語フィルタの正本は `core/BadWordFilter.py`（ロケールと国の表は `AutonaLocaleHelper` から callable で注入）
- 選手 DB の正本は `core/PlayerDB.py`（ロックと通知の規則は docstring）。外からは `Get` / `Contains` / `Items` / `GetPlayerFromTag` /
  `TournamentPlayers` で読み、dict を直接回さない（entrants のワーカーが書いている）。プロセスの外へ出すものは `TournamentPlayers()` の深いコピー
  - `src/AutonaPlayerDB.py` は `db_updated` だけの Qt アダプタ（`Attach()` が `changed` → `SetupModel`。`SetupModel` は GUI スレッドで mains を
    `NormaliseMains` で正規化してから出す。pin: `tests/test_player_db.py`）。データの操作を足さない
