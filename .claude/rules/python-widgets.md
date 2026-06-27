---
paths:
  - "src/**/*.py"
---

# PyQt6 ウィジェット開発ルール

## QDockWidget

- `QDockWidget` を新規作成する場合、`__init__` 内で必ず `self.setWindowTitle()` を呼ぶこと
  - これがないとタブのテキストが空になり、ユーザーからタブが見えない
- `setObjectName()` は `Autona.py` 側で設定する（ウィジェット自身では設定しない）
- `objectName` は一度設定したら変更しない（`restoreState` がこの名前でウィジェットを識別する）

## ウィジェット登録（Autona.py）

新しいドックウィジェットを追加する場合、以下を全て行う:

1. import を追加
2. インスタンス生成・`setObjectName` 設定
3. `self.addDockWidget()` で追加
4. `self.dockWidgets` リストに追加
5. `self.tabifyDockWidget(self.scoreboard, ...)` でタブ化
6. `Window.STATE_VERSION` をインクリメント

## STATE_VERSION

- `Window.STATE_VERSION` はドックウィジェットのレイアウト変更時にインクリメントする
- タブの追加・削除・並び順変更が該当する
- インクリメントしないと、古い保存状態が復元されて新ウィジェットが非表示になる

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
