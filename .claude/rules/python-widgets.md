---
paths:
  - "src/**/*.py"
---

# Qt (PySide6) ウィジェット開発ルール

Qt binding は **PySide6（qtpy 経由、LGPL）** 固定。PyQt6 (GPL) は MIT 配布と両立しないため使用禁止（build-and-release.md 参照）。import は `from qtpy...`、シグナルは `Signal` / `Slot`（`pyqtSignal` / `pyqtSlot` は書かない）。

## パネル（旧 Dock）

各機能パネルは依然 `QDockWidget` サブクラスだが、**もうドックしない**。
タイトルバーを隠し、左サイドバー（`AutonaSidebar`）＋中央 `QStackedWidget` の
ページとして表示する（`Autona.py` の「Sidebar navigation」ブロック）。

- パネルを新規作成する場合、`__init__` 内で必ず `self.setWindowTitle()` を呼ぶこと
  - サイドバーのナビ項目ラベルは `panel.windowTitle()` を使う。空だとラベルが出ない
- `setObjectName()` は `Autona.py` 側で設定する（ウィジェット自身では設定しない）
- `objectName` は一度設定したら変更しない（入力欄の objectName は StateManager
  キーパスに直結。下記「入力ウィジェットの objectName」参照）

## パネル登録（Autona.py）

新しいパネルを追加する場合、以下を全て行う:

1. import を追加
2. インスタンス生成・`setObjectName` / `setWindowTitle` 設定
3. `self.dockWidgets` リストに追加（後処理ループでタイトルバー非表示等を一括適用）
4. `self._navSections` に `(panel, 'icon.svg')` を表示順で追加
3. `self.dockWidgets` リストに追加（`_PreparePanel` でタイトルバー非表示等を一括適用）
4. `self._navSections` の該当カテゴリに
   `(panel, 'icon.svg', ナビラベル, None)` を表示順で追加
   （`assets/icons/` のアイコン。`stack.addWidget` とナビ項目生成に使われる）
   - 構造はカテゴリ付き: `[(カテゴリ見出し or None, [(パネル, アイコン名), ...]), ...]`
     （見出しは `QApplication.translate("app", ...)`。`None` なら見出しなしで並ぶ）
   - 既存カテゴリ（Setup / ScoreboardCategory / Display / Data）に追加する場合は、
     該当セクションのパネルリストに表示順でタプルを挿入するだけでよい
   - 新カテゴリを作る場合は `(translate 済み見出し, [パネルリスト])` のタプルを追加する
   - カテゴリ見出しはナビ index を消費しないため、ナビ index = stack ページ index は維持される
5. `Window.STATE_VERSION` をインクリメント

WebEngine プレビュー等で重いパネルは **遅延構築**にする（起動 Step 4b-2）:
2〜3 を行わず、`_Create*Panel()` ファクトリを書いて
`(self._CreateXxxPanel, 'icon.svg', ナビラベル, 'キー')` を `_navSections` に
追加する。stack にはプレースホルダが入り、初回表示時に `_EnsurePanelAt` が
差し替える（nav index == stack index は維持）。パネル未構築でも動く必要がある
処理は `__init__` に置かない。**state のシード実体は `src/core/StateSchema.py`
（`SeedPanelState()` / `SeedBoard()`）に置く**（Qt 非依存なので headless でも同じ
state になる）。`Window._SeedPanelState()` はその起動時の呼び出し口で、3 つのシード
（`StateSchema.SeedPanelState()` / `TournamentInfoService.Seed()` /
`core.ColorService.SeedColorState()`）を 1 SaveBlock に束ねる役目だけを持つ。
グローバルシグナル接続は Window 側に寄せる。外部から構築を強制したい場合は
`EnsureXxxPanel()` アクセサを足す。

## STATE_VERSION

- `Window.STATE_VERSION` はナビ（パネル）の追加・削除・並び順変更時にインクリメントする
- `QMainWindow.saveState/restoreState` は使わない。サイドバーの選択 index と
  折りたたみ状態を `QSettings`（`sidebarIndex_v{N}` / `sidebarCollapsed_v{N}`）に保存する

## 入力ウィジェットの objectName は StateManager キーパス（後方互換に直結）

- `.ui` の入力ウィジェット（QLineEdit / QSpinBox / QComboBox / QCheckBox / QDateEdit 等）の
  objectName は、そのまま StateManager のキーパスになる（例: `score.{N}.team.{T}.teamName`）。
  **objectName を変更/削除すると既存ユーザーの `program_state.json` との互換が壊れる**
  （state スキーマのマイグレーション層は無い。S-14）
- **方向は「schema → widget」**（Headless Core Step 5 で反転済み）。キー名は
  `src/core/StateSchema.py` の定数が正本で、ウィジェットはそれを `findChild` の
  **引数として**使う。`findChildren()` で拾ったウィジェットに `objectName()` を聞いて
  キーを組む書き方は全ウィジェットから消えており、**新しく書かない**
  （`tests/test_objectname_keypath_contract.py` が AST で検知する）
- 新しい入力欄を足すときは **先に `StateSchema` にキー定数を置き**、`.ui` の objectName を
  それに合わせる
- 変更が必要な場合は **先に StateManager のマイグレーション層を追加**してから、
  `StateSchema` の定数と `tests/test_objectname_keypath_contract.py` の `EXPECTED` を
  **同時に**更新する（片方だけ直すと `findChild` が解決できず無言で書き込みが止まる）
- 保護対象 objectName ↔ キーパスの一覧は `.claude/known-smells.md` S-14 の対応表を参照

## ウィジェットは state を書かない（Headless Core Step 5 完了）

- **`StateManager.Set` / `Unset` を Qt ウィジェットから呼ばない**。書き込みは
  `src/core/Actions.py` の `AutonaActions` か、core のドメインサービス
  （`PlayerService` / `SetLoadService` / `CrewBattleService` / `TournamentInfoService` /
  `Top8Service` / `ColorService` / `StatsService` / `GameTrackerService`）を通す。
  サブツリーのシード・破棄は `src/core/StateSchema.py`
- ウィジェットの役割は「値を組み立てて操作を依頼する」「state を読んで描画する」の 2 つだけ。
  Qt モデル（国コンボ・キャラスロット・スキン行）から値を解決する処理は**ウィジェット側に残す**
  のが正しい（core は Qt を import しない）
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
