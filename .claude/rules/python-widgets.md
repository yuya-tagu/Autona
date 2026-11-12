---
paths:
  - "src/**/*.py"
---

# Qt (PySide6) ウィジェット開発ルール

Qt binding は **PySide6（qtpy 経由、LGPL）** 固定。PyQt6 (GPL) は MIT 配布と両立しないため使用禁止（build-and-release.md 参照）。import は `from qtpy...`、シグナルは `Signal` / `Slot`（`pyqtSignal` / `pyqtSlot` は書かない）。

## パネル（旧 Dock）

**ADR-005 S4ac-3 で最後の Qt パネル（SCORE）を削除し、サイドバーの項目はすべて共有ビュー
（`AutonaWebPanelHost`）の React のページになった**（`Autona.py` の `_Create*Panel` → `_WebPanel(名前)`）。
**新規の Qt パネルは足さない**（ADR-005。新しい UI は React で作る）。以下の手順は、ページを足すときの
ナビへの登録と、Qt のパネルがあったころの規約の記録。
左サイドバー（`AutonaSidebar`）＋中央 `QStackedWidget` のページとして表示する
（`Autona.py` の「Sidebar navigation」ブロック）。

- パネルを新規作成する場合、`__init__` 内で必ず `self.setWindowTitle()` を呼ぶこと
  - サイドバーのナビ項目ラベルは `panel.windowTitle()` を使う。空だとラベルが出ない
- `setObjectName()` は `Autona.py` 側で設定する（ウィジェット自身では設定しない）
- `objectName` は一度設定したら変更しない（入力欄の objectName は StateManager
  キーパスに直結していた。下記「入力ウィジェットの objectName」参照）

## パネル登録（Autona.py）

新しいパネルを追加する場合、以下を全て行う:

1. React のページを `WebAccess.WEB_PANELS` と `web/src/PanelShell/` に足し、`_Create*Panel()` ファクトリ
   （`return self._WebPanel("名前")`）を書く（下記「遅延構築」。Qt のパネルのインスタンス生成・`setObjectName` /
   `setWindowTitle` と `self.dockWidgets`・`_PreparePanel` は ADR-005 S4ac-3 で無くなった）
2. `self._navSections` の該当カテゴリに
   `(panel, 'icon.svg', ナビラベル, None)` を表示順で追加
   （`assets/icons/` のアイコン。`stack.addWidget` とナビ項目生成に使われる）
   - 構造はカテゴリ付き: `[(カテゴリ見出し or None, [(パネルまたはファクトリ, アイコン名, ナビラベル, キー or None), ...]), ...]`
     （見出しは `QApplication.translate("app", ...)`。`None` なら見出しなしで並ぶ）
   - 既存カテゴリ（Setup / ScoreboardCategory / Display / Data）に追加する場合は、
     該当セクションのパネルリストに表示順でタプルを挿入するだけでよい
   - 新カテゴリを作る場合は `(translate 済み見出し, [パネルリスト])` のタプルを追加する
   - カテゴリ見出しはナビ index を消費しないため、ナビ index = stack ページ index は維持される
3. `Window.STATE_VERSION` をインクリメント

パネルはすべて **遅延構築**（起動 Step 4b-2）: `_Create*Panel()` ファクトリを書いて
`(self._CreateXxxPanel, 'icon.svg', ナビラベル, 'キー')` を `_navSections` に
追加する。stack にはプレースホルダが入り、初回表示時に `_EnsurePanelAt` が
差し替える（nav index == stack index は維持）。パネル未構築でも動く必要がある
処理は `__init__` に置かない。**state のシード実体は `src/core/StateSchema.py`
（`SeedPanelState()` / `SeedBoard()`）に置く**（Qt 非依存なので headless でも同じ
state になる）。`Window._SeedPanelState()` はその起動時の呼び出し口で、3 つのシード
（`StateSchema.SeedPanelState()` / `TournamentInfoService.Seed()` /
`core.ColorService.SeedColorState()`）を 1 SaveBlock に束ねる役目だけを持つ。
新しいボードの中身（選手スロット・既定色・スコア・`best_of`・クルーボードまで）は、両シェルとも
`AutonaActions.SeedFreshBoard(board)` が書く（ADR-005 S4t。それまではボードのウィジェットが publish していた。既定の人数・キャラ数は
`PlayerService.DefaultPlayersPerTeam()` / `DEFAULT_CHARACTERS_PER_PLAYER` が正本で、ボードもこれを読む）。
ADR-005 S4y から両シェルとも入口は `AutonaActions.EnsureBoard(board, panel)`（1 回だけ作る。デスクトップの `panel=True`
は表示トグルの書き直し・取り直しの停止・反映ボタンの held 登録もここで行う）で、ボードのウィジェットは state を
書かず held 登録もしない。S4ac-1 から両シェルとも**起動時に 3 つとも作る**（`AutonaActions.MakeBoards`。デスクトップは
`bootstrap_gui()` が Window より前に `panel=True` で、ヘッドレスは `_SeedBoards` が `panel` なしで）。デスクトップの
ボードのウィジェット（`CreateScoreboard` / `EnsureScoreboard`）は S4ac-3 で Qt の SCORE ごと削除した。
サムネイルの同期描画・`load_set` ホットキーから SCORE のページのセット選択を開く経路はシェルのアダプタ
`src/AutonaShellOperations.py`（`bootstrap_gui()` が作り、Window が `UiMounted` で渡す）が持つ
（Qt のセット選択 / 配信台のウィンドウも S4ac-3 まではここにあった）。
グローバルシグナル接続は Window 側に寄せる。外部から構築を強制したい場合は
`EnsureXxxPanel()` アクセサを足す。

## STATE_VERSION

- `Window.STATE_VERSION` はナビ（パネル）の追加・削除・並び順変更時にインクリメントする
- `QMainWindow.saveState/restoreState` は使わない。サイドバーの選択 index と
  折りたたみ状態を `QSettings`（`sidebarIndex_v{N}` / `sidebarCollapsed_v{N}`）に保存する

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
  - どの行を表示するか（Qt モデルの走査・手入力）はウィジェット側に残る
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
