---
paths:
  - "src/**/*.py"
---

# Qt (PySide6) ウィジェット開発ルール

Qt binding は **PySide6（qtpy 経由、LGPL）** 固定。PyQt6 (GPL) はソース非公開配布と両立しないため使用禁止（build-and-release.md 参照）。import は `from qtpy...`、シグナルは `Signal` / `Slot`（`pyqtSignal` / `pyqtSlot` は書かない）。

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
state になる）。`Window._SeedPanelState()` はその起動時の呼び出し口で、SaveBlock で
1 サイクルに束ねる役目 + Qt 依存で core に置けない `SeedColorState()` のみを持つ。
グローバルシグナル接続は Window 側に寄せる。外部から構築を強制したい場合は
`EnsureXxxPanel()` アクセサを足す。

## STATE_VERSION

- `Window.STATE_VERSION` はナビ（パネル）の追加・削除・並び順変更時にインクリメントする
- `QMainWindow.saveState/restoreState` は使わない。サイドバーの選択 index と
  折りたたみ状態を `QSettings`（`sidebarIndex_v{N}` / `sidebarCollapsed_v{N}`）に保存する

## 入力ウィジェットの objectName は StateManager キーパス（後方互換に直結）

- `.ui` の入力ウィジェット（QLineEdit / QSpinBox / QComboBox / QCheckBox / QDateEdit 等）の
  objectName は、`findChildren()` 経由でそのまま StateManager のキーパスに使われる
  （例: `score.{N}.team.{T}.teamName`）。**objectName を変更/削除すると既存ユーザーの
  `program_state.json` との互換が壊れる**（state スキーマのマイグレーション層は無い。S-14）
- 変更が必要な場合は **先に StateManager のマイグレーション層を追加**してから、
  `tests/test_objectname_keypath_contract.py` の `EXPECTED` を更新する
- 保護対象 objectName ↔ キーパスの一覧は `.claude/known-smells.md` S-14 の対応表を参照

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
