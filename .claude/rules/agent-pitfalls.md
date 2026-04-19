---
description: フェーズ1調査で発見した、エージェントが踏みやすい地雷
globs:
  - "src/**/*.py"
  - "tests/**/*.py"
---

# エージェント向け地雷回避ルール

## 1. src/ の import は全起動を引き起こす

`from src.XXX import ...` や `from .XXX import ...` は `src/__init__.py` → `Autona.py` → QApplication + 全シングルトン生成を連鎖的にトリガーする。

- テストで `src` パッケージ経由の import を使わない
- テストでは `tests/conftest.py` の `direct_import()` を使う
- `from . import ui_constants` は特に危険（3ファイルで使用中）

## 2. シングルトン初期化順序を壊さない

`Autona.py` の import 順序がシングルトンの生成順序を決定する。

**絶対に守る**: `AutonaGameAssetManager` の import は `AutonaTournamentDataProvider` より前。
理由: AutonaTournamentDataProvider.__init__ が `AutonaGameAssetManager.instance.signals` に connect する。

詳細: `.claude/architecture/singleton-init-order.md`

## 3. StateManager のキーパスはドット区切り文字列

- `StateManager.Set("score.1.team.1.player.1.name", value)` — 正しい
- `StateManager.Set("score/1/team/1", value)` — **間違い**
- 数値も文字列: `"1"` であって `1` ではない

## 4. `element.objectName()` をキーパスに使う既存パターン

`AutonaScoreboardWidget`, `AutonaTournamentInfoWidget` 等で Qt の `objectName()` がそのまま StateManager のキーに使われる。

- `.ui` ファイルの objectName を変更すると保存データとの互換性が壊れる
- 新しいウィジェットを追加する際も、objectName = StateManager キー であることを意識する

## 5. AutonaScoreboardPlayerWidget と AutonaTeamPlayerWidget は別クラス

見た目も構造もほぼ同じだが、**基底クラスを共有していない**。

- 片方を修正したら、もう片方にも同じ修正が必要か確認する
- 共通メソッド 19 個（SetData, SetCharactersPerPlayer, SwapWith 等）
- 詳細: `.claude/architecture/python-hotspots.md`

## 6. SaveBlock の対称性

`StateManager.BlockSaving()` と `StateManager.ReleaseSaving()` は必ずペアで呼ぶ。ブロック数がマイナスになると SaveState が暴走する。

- `with StateManager.SaveBlock():` を使うのが安全
- try/finally で囲む既存パターンもあるが、例外時に ReleaseSaving を漏らすリスクがある

## 7. `self.path` の動的構築

`AutonaScoreboardPlayerWidget.path` は呼び出し元によって異なる:
- スコアボード: `score.{N}.team.{T}.player.{M}`
- 実況者: `commentary.{N}`
- 選手リスト: `{base}.slot.{I}.player.{M}`

`self.path` を前提としたリファクタでは、全3パターンでの動作を確認すること。

## 8. Autona.exe はプロジェクト直下

ビルド後の実行ファイルは `Autona.exe`（プロジェクトルート直下）。`dist/Autona.exe` ではない。
PyInstaller の `--distpath .` オプションで直下に出力している。

## 9. Qt シグナルハンドラ内で `processEvents()` を呼ばない

`QGuiApplication.processEvents()` はイベントループを同期で再入させるため、
シグナルハンドラ内で呼ぶと同じシグナルが再度 dispatch されたり、
構築途中のウィジェットに対して paint イベントが走ったりして C++ レベルの
access violation を起こす。

- widget レイアウト解決後に何かしたいときは `QTimer.singleShot(0, fn)` で defer
- `UpdatePhaseGroup` のような長時間走るハンドラには再入ガードを置く:
  ```python
  if self._updatingPhaseGroup:
      logger.warning("re-entrant call, skipping")
      return
  self._updatingPhaseGroup = True
  try:
      ...
  finally:
      self._updatingPhaseGroup = False
  ```
- QSpinBox.valueChanged 等のシグナル内からモーダル (`QMessageBox.exec()`) を
  呼ばない（CLAUDE.md の「モーダル入れ子禁止」と同根）

## 10. `QGraphicsScene.clear()` を連続で呼ばない

scene に `QGraphicsProxyWidget` で大量ウィジェット (100+) が乗っているとき、
短時間に `clear()` → 再構築 → `clear()` → 再構築を繰り返すと、proxy の
非同期破棄と新規 widget 生成が競合して access violation を起こす。

- loading 状態を表示したいときは **scene 外の QLabel オーバーレイ**
  (parent=QGraphicsView 本体) を使い、scene は触らない
- 参考実装: `AutonaBracketView.loadingOverlay` と `ShowLoading()`

## 11. Start.gg GQL クエリの 20 秒タイムアウト

`StartGGDataProvider._request_timeout_secs = 20`。重いクエリ
（`TournamentPhaseGroupQuery` の seeds + sets 全取得など）に新フィールドを
追加するとすぐ timeout する。追加時は以下を意識:

- `user.images`, `authorizations`, `player.sets.games.selections` のような
  **ネスト深いフィールド**は bracket 表示に必須でなければ削る
- レスポンス 1MB を超えると危険域
- `QueryRequests` には指数バックオフ (1/2/4/8s) 組込済。5 回失敗で `{}`
  が返るので、呼び出し側は空データでも壊れないように null-safe に書く

## 12. Edit/Write ツール後は `__pycache__` が古い可能性

Claude Code の Edit/Write が Windows 上でソース mtime を更新しない場合があり、
Python が古い .pyc を使い続けて修正が反映されない。

- `main.py` 冒頭で `sys.dont_write_bytecode = True` を設定済み（根本対策）
- それでも疑わしい挙動があれば
  `find src -name "*.pyc" -delete` で .pyc を全消しして再実行
