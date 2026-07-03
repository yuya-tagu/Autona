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
4. `self._navPanels` に `(panel, 'icon.svg')` を表示順で追加
   （`assets/icons/` のアイコン。`stack.addWidget` とナビ項目生成に使われる）
5. `Window.STATE_VERSION` をインクリメント

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

- `StateManager.signals` への `connect` は `__init__` 内で行う
- スロットでの例外は握りつぶさず `logger.debug` 以上でログに出す
