---
paths:
  - "src/**/*.py"
---

# Qt (PySide6) 開発ルール

Qt binding は **PySide6（qtpy 経由、LGPL）** 固定。PyQt6 (GPL) は MIT 配布と両立しないため使用禁止（build-and-release.md 参照）。import は `from qtpy...`、シグナルは `Signal` / `Slot`（`pyqtSignal` / `pyqtSlot` は書かない）。

## ウィンドウとページ

**メインウィンドウ（`src/Autona.py` の `Window`）の中身は共有ビュー `AutonaWebPanelHost` 1 枚**。ホストは AppShell（`/web/app/<panel>`。
`web/src/AppShell/`）を読み、サイドバー・ヘッダー（大会・ゲーム・MOD・対戦形式）・フッター（Help & Settings・実行中の仕事）は AppShell のもの。
**新規の Qt パネル・Qt のウィジェットは足さない**（ADR-005。新しい UI は React で作る）。

`tests/test_web_panel_host.py` が pin していること（戻さない）:
- `src/` の QWidget のサブクラスは許可リスト `QT_WIDGET_CLASSES`（`Window` / `AutonaWebPanelHost` / `_PanelView` / `AutonaDesignerWindow` /
  `_UpdateDialog` / 起動スプラッシュの `LoadingOverlay`）の中だけ（基底は推移的に解決する）。足すなら許可リストを広げる前に React で作れないかを判断する
- `Autona.py` に `QStackedWidget` / `QToolButton` / `QStatusBar` / `QMenuBar` / `QMenu` を使わない。`QMessageBox` は初回起動の LAN の確認
  （`_StartWebServer`）だけ。Web サーバーの起動失敗はダイアログでなくホストの案内の文（`ShowStartFailure`）
- 消した Window のメソッド・属性（`GONE_WINDOW_MEMBERS`）を戻さない

### ページの登録

新しいページは React で書き、次を揃える（**`src/Autona.py` は触らない**）:

1. `src/core/WebAccess.py` の `WEB_PANELS` に名前を足す（サーバーが `/web/<名前>` と `/web/app/<名前>` で返す名前）
2. `web/src/PanelShell/registry.tsx`（`PANELS`）にページのコンポーネントを足す
3. サイドバーの項目なら `web/src/AppShell/navSections.json` の該当セクションに `{panel, icon, label}` を表示順で足す
   （アイコンは `assets/icons/`。文言は i18next の `app_*` キーで en / ja の両方）。`tests/test_app_shell.py` の `NAVIGATION` は今のナビの凍結した写しで、
   ナビと照合している。ナビを変えるなら、その照合を今のナビの表に書き直すかを判断してから直す
   （`AutonaWindowState.LEGACY_SIDEBAR_PANELS` はナビを変えても動かさない）
4. Help & Settings のメニューから開くページ（今は settings / api / companion / obs / about / assets）なら、`web/src/AppShell/navTable.ts` の
   `MENU_PAGES` と `src/AutonaWindowState.py` の `MENU_PAGES` の両方に足す（`tests/test_app_shell.py` が一致を pin）。メニューのページは
   ナビをハイライトせず、ウィンドウが次の起動で開き直さない（その前に開いていたページを覚える）

版を上げる手順は無い。ウィンドウが覚えるのはページの名前なので、項目の追加・削除・並べ替えで読み替えは要らない。

**state のシード実体は `src/core/StateSchema.py` に置く**（headless でも同じ state になる。`Window._SeedPanelState()` は呼び出し口だけ。
ボードは state-and-webserver.md の「ボードの作成とシード」）。

### ウィンドウの仕事

- **ページの切り替え**: AppShell のナビは `history.replaceState`（遷移にならない）。ホストは `urlChanged` でどのページかを読み（`AppPanelOf`）、
  `panelChanged(名前)` を出す。ウィンドウはそれで終了時に覚えるページを決める（`AutonaWindowState.RememberedPanel`）
- **外から開く**: `Window.OpenPanel(名前)`（ウィンドウを前に出して `ShowPanel`。ホストを返す。終了中は None）。呼ぶのはシェルのアダプタ
  `src/AutonaShellOperations.py` の `load_set` のホットキーと legacy の open-set だけで、返ったホストの `OpenScoreSets(board)` で SCORE ページの
  セット選択を開く
- **保存**: QSettings の `lastPanel` と `navCollapsed` を**変わるたびに**保存する（`Window._SaveWindowState`。クラッシュでも次の起動は最後の
  ページで開く）。読み書きは Qt-free の純関数 `src/AutonaWindowState.py`。`QMainWindow.saveState/restoreState` は使わない
- **起動の順**: `show()` → `STARTUP phase=core-shown` → 初回の描画の後に `_StartWebServer`（`call_when_painted`。イベントループから呼ばれるので
  中の `QMessageBox.question` は入れ子のモーダルにならない: pitfalls #9）→ `webserver-ready` → `panel-shown`（ADR-003）。pin: `tests/test_web_panel_host.py`
- **スピナーが外れない経路を作らない**: 外すのは最初の読み込みの終わり（失敗を含む）・Web サーバーの起動失敗（`OnWebServerStartFailed`）・
  上限（`Window.SPINNER_CAP_MS`）。イベントループから呼ばれる処理（`call_when_painted` のコールバック・`_BuildQueuedView`）で例外を素通しにしない
- **読み込みの失敗**: ホストが再試行の表示（`ShowLoadFailure`）で覆う。タイマーでの自動の再試行はしない（操作 1 回につき 1 回）
- シェル要求（最前面表示ほか）の実行は `AutonaShellOperations` の `ShellRequest` / `ShellState`（規則は state-and-webserver.md の共有ビューの節）。
  外部アドレスの表は `src/AutonaExternalLinks.py` に 1 つ（AppShell の `externalLinks.ts` が写し）
- グローバルシグナルの接続は Window 側に寄せ、繋ぐ先のシグナルをそのまま使う（例: `_StartWebServer` の `bootstrap.wire_web_server` が Web
  サーバーを Bridge の `game_loaded` / `games_scanned` に繋ぐ）。中継のシグナルを Window に足さない

## ウィジェットは state を書かない

- **`StateManager.Set` / `Unset` を Qt ウィジェットから呼ばない**。書き込みは `src/core/Actions.py` の `AutonaActions` か core のドメインサービスを
  通す。サブツリーのシード・破棄は `src/core/StateSchema.py`
- ウィジェットの役割は「操作を依頼する」「state を読んで描画する」の 2 つだけ。シグナルハンドラの中でレイアウトを同期で壊さない（破棄は `QTimer.singleShot(0)` でイベントループに戻してから。pitfalls #14）
- `src/core` 外で state を書いてよいのは `AutonaTournamentDataProvider`（`provider_videogame`）の 1 つだけ（pin:
  `tests/test_objectname_keypath_contract.py` の書き手台帳テスト）

## StateManager シグナル

state の通知の正本は Qt 非依存の observer（`src/core/Observable.py`）で、**Qt シグナルはアダプタ `src/StateManagerBridge.py` が出す**。
core 側に Qt シグナルを足さない（`tests/test_core_qt_free.py`）。

- Qt のオブジェクトは `StateManagerBridge.instance` の同名シグナル（`state_updated` ほか 4 本）に `__init__` 内で `connect` する
- **ウィジェットから `StateManager.observers...subscribe()` を直接呼ばない**（強参照で破棄時に解除されず、破棄済みウィジェットが state を書き戻し
  続けた: ゴーストボード）。pin: `tests/test_signal_lifetime.py`
- スロットでの例外は握りつぶさず `logger.debug` 以上でログに出す
