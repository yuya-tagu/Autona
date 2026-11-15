---
paths:
  - "src/**/*.py"
---

# Qt (PySide6) ウィジェット開発ルール

Qt binding は **PySide6（qtpy 経由、LGPL）** 固定。PyQt6 (GPL) は MIT 配布と両立しないため使用禁止（build-and-release.md 参照）。import は `from qtpy...`、シグナルは `Signal` / `Slot`（`pyqtSignal` / `pyqtSlot` は書かない）。

## ウィンドウとページ（ADR-005 S-final SF-5b-3）

**メインウィンドウ（`src/Autona.py` の `Window`）の中身は共有ビュー `AutonaWebPanelHost` 1 枚**。ホストは AppShell
（`/web/app/<panel>`。`web/src/AppShell/`）を読み、サイドバー・ヘッダー（大会・ゲーム・MOD・対戦形式）・フッター
（Help & Settings・実行中の仕事）は AppShell のもの。Qt のサイドバー（`AutonaSidebar`）・`QStackedWidget`・メニューバー・
ステータスバー・MOD のチェックボックス・大会ボタン・`_Create*Panel` ファクトリ・`WebPanelPages`・`Window.STATE_VERSION` は
SF-5b-3 で削除した。**新規の Qt パネル・Qt のウィジェットは足さない**（ADR-005。新しい UI は React で作る）。

`tests/test_web_panel_host.py` が pin していること（戻さない）:
- `src/` の QWidget のサブクラスは許可リスト（`Window` / `AutonaWebPanelHost` / `_PanelView` / `AutonaDesignerWindow` / `_UpdateDialog`）の中だけ
  （基底は推移的に解決する）。足すなら許可リストを広げる前に React で作れないかを判断する
- `Autona.py` に `QStackedWidget` / `QToolButton` / `QStatusBar` / `QMenuBar` / `QMenu` を使わない。`QMessageBox` は初回起動の LAN の確認
  （`_StartWebServer` の `QMessageBox.question`）だけ。Web サーバーの起動失敗はダイアログではなく、ホストの中身を案内の文に置き換える
  （`ShowStartFailure`）
- `src/AutonaSidebar.py` は無い・どこからも import しない。消した Window のメソッド・属性（`GONE_WINDOW_MEMBERS`）を戻さない

### ページの登録

新しいページは React で書き、次を揃える（**`src/Autona.py` は触らない**）:

1. `src/core/WebAccess.py` の `WEB_PANELS` に名前を足す（サーバーが `/web/<名前>` と `/web/app/<名前>` で返す名前）
2. `web/src/PanelShell/registry`（`PANELS`）にページのコンポーネントを足す
3. サイドバーの項目なら `web/src/AppShell/navSections.json` の該当セクションに `{panel, icon, label}` を表示順で足す
   （アイコンは `assets/icons/`。見出しと文言は i18next の `app_*` キーで en / ja の両方）。`tests/test_app_shell.py` の `NAVIGATION` は
   削除した Qt のサイドバーの凍結した写しで、今のナビと照合している。ナビを変えるなら、その照合を今のナビの表に書き直すかを判断してから直す
   （`AutonaWindowState.LEGACY_SIDEBAR_PANELS` は凍結した写しと照合し、ナビを変えても動かさない）
4. Help & Settings のメニューから開くページ（今は settings / api / companion / obs / about / assets）なら、`web/src/AppShell/navTable.ts` の
   `MENU_PAGES` と `src/AutonaWindowState.py` の `MENU_PAGES` の両方に足す（`tests/test_app_shell.py` が一致を pin）。メニューのページは
   ナビをハイライトせず、ウィンドウが次の起動で開き直さない（その前に開いていたページを覚える）

版を上げる手順は無い（`STATE_VERSION` は削除した）。ウィンドウが覚えるのはページの名前なので、項目の追加・削除・並べ替えで読み替えは要らない。

**state のシード実体は `src/core/StateSchema.py`（`SeedPanelState()` / `SeedBoard()`）に置く**（Qt 非依存なので headless でも同じ
state になる。ページを開いていなくても成り立つ）。`Window._SeedPanelState()` はその起動時の呼び出し口で、3 つのシード
（`StateSchema.SeedPanelState()` / `TournamentInfoService.Seed()` / `core.ColorService.SeedColorState()`）を 1 SaveBlock に束ねる役目だけを持つ。
新しいボードの中身（選手スロット・既定色・スコア・`best_of`・クルーボードまで）は、両シェルとも
`AutonaActions.SeedFreshBoard(board)` が書く（ADR-005 S4t。既定の人数・キャラ数は
`PlayerService.DefaultPlayersPerTeam()` / `DEFAULT_CHARACTERS_PER_PLAYER` が正本）。
ADR-005 S4y から両シェルとも入口は `AutonaActions.EnsureBoard(board, panel)`（1 回だけ作る。デスクトップの `panel=True`
は表示トグルの書き直し・取り直しの停止・反映ボタンの held 登録もここで行う）。S4ac-1 から両シェルとも**起動時に 3 つとも作る**
（`AutonaActions.MakeBoards`。デスクトップは `bootstrap_gui()` が Window より前に `panel=True` で、ヘッドレスは `_SeedBoards` が `panel` なしで）。

### ウィンドウの仕事

- **ページの切り替え**: AppShell のナビは `history.replaceState`（遷移にならない）。ホストは `urlChanged` でどのページかを読み（`AppPanelOf`）、
  `panelChanged(名前)` を出す。ウィンドウはそれで終了時に覚えるページを決める（`AutonaWindowState.RememberedPanel`）
- **外から開く**: `Window.OpenPanel(名前)`（ウィンドウを前に出して `ShowPanel`。ホストを返す。終了中は None で何もしない）。呼ぶのは
  シェルのアダプタ `src/AutonaShellOperations.py`（`bootstrap_gui()` が作り、Window が `UiMounted` で渡す）の `load_set` のホットキーと legacy の
  open-set だけで、返ったホストの `OpenScoreSets(board)` で SCORE のページのセット選択を開く（ADR-005 S4ab-2）
- **保存**: QSettings の `lastPanel`（ページの名前）と `navCollapsed`（AppShell のサイドバーの折りたたみ。AppShell が URL の
  `nav=collapsed` に replace で書き、ホストが `urlChanged` で読んで `navCollapsedChanged` を出す）を、**変わるたびに**保存する
  （`Window._SaveWindowState`: `panelChanged` / `navCollapsedChanged` から。保存値と違うものだけ書いて `QSettings.sync()`。メニューのページ・
  URL の `told=` の変化では書かない。起動時の読み込み（旧キーの読み替え）より前と `_shuttingDown` の後は書かない）。終了時（`closeEvent`）にも
  ジオメトリと一緒に保存する。クラッシュ・強制終了で `closeEvent` が走らなくても、次の起動は最後のページで開く。起動時は `RestorePanel` / `RestoreNavCollapsed` で読み、
  ホストの最初の読み込みの URL（`PanelPath`）に入れる。読み書きは Qt-free の純関数 `src/AutonaWindowState.py`。旧キー
  （`sidebarIndex_v22` / `sidebarCollapsed_v22`）は 1 回だけ読み替えて消す（index は固定の表 `LEGACY_SIDEBAR_PANELS`）。
  `QMainWindow.saveState/restoreState` は使わない（ジオメトリは `saveGeometry` / `restoreGeometry`）
- **起動の順**: `show()` → `STARTUP phase=core-shown` → `LoadingOverlay` → 初回の描画の後に `_StartWebServer`（`call_when_painted`。
  イベントループから呼ばれるので、中の `QMessageBox.question` / `exec` は入れ子のモーダルにならない: pitfalls #9）→ `webserver-ready` →
  最初の読み込みの終わりでスピナーを外して `STARTUP phase=panel-shown`（ADR-003 の追記）。順は `tests/test_web_panel_host.py` が pin
- **スピナーが外れない経路を作らない**: スピナーを外すのは、最初の読み込みの終わり（失敗し切ったとき・ビューを建てられなかったときを含む）・
  Web サーバーの起動失敗（サーバースレッドの bind 失敗も、`_StartWebServer` の中の例外も `OnWebServerStartFailed`）・上限
  （`Window.SPINNER_CAP_MS` = 20 秒。LAN の確認やポートの確認のダイアログとサーバーの構築の間は止め、サーバーの準備ができてから数え直す。
  過ぎたら warning を出して外し、ホストの再試行の表示 `ShowLoadFailure` を出す）。イベントループから呼ばれる処理（`call_when_painted` の
  コールバック、ホストの `_BuildQueuedView`）で例外を素通しにしない（上で受けるものが無く、スピナーが残る）
- **読み込みの失敗**: 失敗し切った読み込み（ローダーの再試行の使い切り、文書の無い状態での拒否された遷移）のあとは、ホストが Chromium の
  エラーページを再試行の表示（`ShowLoadFailure`。QLabel と「再試行」の `QPushButton` をビューの上に重ねる。ビューは見えたままなので
  復帰時の読み直しもそのまま）で覆い、成功した読み込みで外す。タイマーでの自動の再試行はしない（操作 1 回につき 1 回）。ビューを
  建てられなかったときは起動失敗と同じ文（`ShowStartFailure`）にして、それ以上建てない
- **常に最前面**: Window の bool（`SetAlwaysOnTop` / `IsAlwaysOnTop`。起動をまたいで保存しない）。シェル要求 `alwaysOnTop` から
  `AutonaShellOperations` が呼び、`SetAlwaysOnTop` が窓のフラグを変えてホストの `PublishShellState` でページに知らせる
- **共有ビューのページのシェル要求**（ADR-005 S-final SF-5a-1。`alwaysOnTop` / `checkUpdates` / `openExternal`、SF-5a-2 の `pickFolder`
  （`QFileDialog` のフォルダ選択。選んだフォルダは core の `AssetPackService.HoldFolderPick` が持ち、ページには id だけ））を実行するのも
  同じアダプタ（`ShellRequest` / `ShellState`。Window が `AutonaWebPanelHost(shell=AutonaShellOperations.instance)` と渡す）。
  外部アドレスの表は `src/AutonaExternalLinks.py` に 1 つ（AppShell の `externalLinks.ts` が同じアドレスの写し）
- グローバルシグナル接続は Window 側に寄せる（例: アセットのアダプタの `onLoad` → `WindowSignals.GameChanged`）

## 入力ウィジェットの objectName は StateManager キーパス（後方互換に直結）

- **ADR-005 S4ac-3 で `src/layout/`（最後の `.ui`）を削除し、この規約の対象の Qt ウィジェットは残っていない**。
  キー名は `src/core/StateSchema.py` の定数が正本のまま、`tests/test_objectname_keypath_contract.py` の
  `EXPECTED`（当時の .ui の objectName の凍結した写し）と一致していることを pin する。以下は規約の記録
- `.ui` の入力ウィジェット（QLineEdit / QSpinBox / QComboBox / QCheckBox / QDateEdit 等）の
  objectName は、そのまま StateManager のキーパスになっていた（例: `score.{N}.team.{T}.teamName`）。
  **キーを変更/削除すると既存ユーザーの保存済み state（`user_data/state.json`）との互換が壊れる**
  （移行 step を足さない限り。S-14）
- **方向は「schema → widget」**（Headless Core Step 5 で反転済み）。キー名は
  `src/core/StateSchema.py` の定数が正本で、ウィジェットはそれを `findChild` の
  **引数として**使う。`findChildren()` で拾ったウィジェットに `objectName()` を聞いて
  キーを組む書き方は全ウィジェットから消えており、**新しく書かない**
- 新しい入力欄を足すときは **先に `StateSchema` にキー定数を置き**、`.ui` の objectName を
  それに合わせる
- 変更が必要な場合は **先に `src/core/StateMigrations.py` に移行 step を足して `CURRENT_VERSION` を
  上げ**（ADR-005 S3b。旧キー → 新キー）、
  `StateSchema` の定数と `tests/test_objectname_keypath_contract.py` の `EXPECTED` を
  **同時に**更新する（片方だけ直すと `findChild` が解決できず無言で書き込みが止まる）
- 保護対象 objectName ↔ キーパスの一覧は `.claude/known-smells.md` S-14 の対応表を参照

## ウィジェットは state を書かない（Headless Core Step 5 完了）

- **`StateManager.Set` / `Unset` を Qt ウィジェットから呼ばない**。書き込みは
  `src/core/Actions.py` の `AutonaActions` か、core のドメインサービス
  （`PlayerService` / `SetLoadService` / `SetFollowService` / `CrewBattleService` / `TournamentInfoService` /
  `Top8Service` / `ColorService` / `StatsService` / `CommentaryService` /
  `ThumbnailService` / `ResultService`）を通す。
  サブツリーのシード・破棄は `src/core/StateSchema.py`
- ウィジェットの役割は「操作を依頼する」「state を読んで描画する」の 2 つだけ。
  **ウィジェット同士で値をコピーして操作を実現しない**（ADR-005 S2c で選手カードの `SwapWith` を削除し、
  スワップ / 上下移動は `AutonaActions.SwapTeams` / `SwapPlayers` が state を入れ替え、カードは
  `RenderFromState` で追従した。カードは S4ac-3 で Qt の SCORE ごと削除）。コピーは値の形を Qt に縛り（バリアント・mains が落ちた）、
  入口ごとに挙動がずれる。
  **コンボの選択は行の dict ではなく識別子で渡す**（キャラ / バリアントの `en_name`・
  スキンの行番号・国 / 地域のコード）。state に入れる値は core がカタログ
  （`GameAssetCatalog` / `CountryCatalog`）から組む（ADR-005 S1d。実例は選手スロットの
  `PlayerService.PublishCharacterSlots` / `SetCountry` / `SetRegion`。v1 の `Edit` / `EditCharacter` も同じ）。Qt モデルの UserRole を
  読み出してそのまま publish すると、値の形とキー順（QVariantMap 順 = 外部契約）が
  Qt に縛られ、Qt なしでは同じ state を作れなくなる
  - どの行を表示するか（Qt モデルの走査・手入力）はウィジェット側に残る（Qt モデルの整理 PR4 で最後の Qt のモデル
    （`AutonaGameAssetManager` のキャラ・スキン・バリアント・カラーのモデル）も削除し、今は該当する Qt のウィジェットが無い。
    React のページはカタログのペイロード（`GameAssetCatalog.*Payload()` / `CountryCatalog`）の行を描く）
  - **カードの数も state に従う**（ADR-005 S3e-6）: 人数・キャラ数は `AutonaActions.SetRoster` が
    スロットとキャラ枠を増減する。Qt のボードは held サイクルの中でカード・キャラ行を作り / 退役させ、
    破棄を `QTimer.singleShot(0)` でイベントループに戻してから pitfalls #14 の invalidate を通して行っていた
    （S4ac-3 で削除。シグナルハンドラの中でレイアウトを同期で壊さない、は Qt の UI 一般の規約として残る）
  - **選手データ dict の読込も同じ**（ADR-005 S3e-3）。読込は `PlayerService.ApplyData`
    （DB とのマージ・悪語フィルタ・国 / 地域 / キャラの解決は core）で、コンボの行を探して選ぶ・
    `editingFinished` を emit してハンドラに書かせる、という「フォームに入力して書かせる」読込を書かない
    （Qt のカードの `SetData` がその形だった。S4ac-3 で削除）
  - Top 8 は ADR-005 S4h から識別子で渡す（国は code、キャラは en_name + カラーの行。
    値は `Top8Service` が選手スロットと同じ組み立てで作り、state → 選択の逆引きは `Top8Service.CharacterPick`）。
    パネルは S4i で React のページ（`/web/top8`）になり、Qt 版は削除済み（ページの逆引きは同じ表で検査する
    `web/src/Top8Page/characterPick.ts`）。
    大会リザルトも S4r で React のページ（`/web/result`）になり、`ResultService` に識別子（en_name + スキンの行 + 選んだゲーム）で渡す。
    実況パネルは Web 版（React）だけになり（S4c）、アイコンの識別子（stem）を `CommentaryService.SetField` に渡す。
    新しく同じ形を書かない
- `src/core` 外で state を書いてよいのは `AutonaTournamentDataProvider`
  （`provider_videogame`）の 1 つだけ（`game.*` は ADR-005 S1a で
  `core/GameAssetCatalog.py` へ移動）。
  `tests/test_objectname_keypath_contract.py` の書き手台帳テストが集合ごと pin している

## StateManager シグナル

state の通知は Qt 非依存の observer（`StateManager.observers`、`src/core/Observable.py`）が
正本で、**Qt シグナルはアダプタ `src/StateManagerBridge.py` が提供する**（headless core Step 1）。
ウィジェットからは Bridge に繋ぐ。core 側に Qt シグナルを足さない
（`tests/test_core_qt_free.py` が落ちる）。

- `StateManagerBridge.instance.{state_big_change, state_updated,
  held_dirty_changed, held_state_updated}` への `connect` は `__init__` 内で行う
  （シグナル名・引数は旧 `StateManager` 上のシグナルと同一）
- **ウィジェットから `StateManager.observers...subscribe()` を直接呼ばない**。
  `ObserverChannel` は callback を**強参照**し、Qt の weak receiver に相当する仕組みが無い。
  bound method で登録しても破棄時に自動解除されないため、破棄済みウィジェットが
  シグナルに答え続けて state を書き戻す（ゴーストボード）。Qt シグナル経由なら
  C++ オブジェクト破棄時に Qt が接続を切るので、必ず Bridge に繋ぐ。
  `tests/test_signal_lifetime.py` がこの違反を検出する
- observer の直接購読は **core / 非 Qt コード専用**（core のテスト、将来の core サービス）。
  寿命がチャンネルより短い購読者は自分で `unsubscribe()` する責任を持つ
- スロットでの例外は握りつぶさず `logger.debug` 以上でログに出す
