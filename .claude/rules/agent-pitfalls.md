---
description: フェーズ1調査で発見した、エージェントが踏みやすい地雷
globs:
  - "src/**/*.py"
  - "tests/**/*.py"
---

# エージェント向け地雷回避ルール

実クラッシュ・実事故を出したものと、それを防ぐ規則。項番はコードコメントとほかの文書が `#9` `#17` のように参照するので変えない
（退役した項も番号を保つ）。経緯は git 履歴と台帳（`docs/changes/`）にある。

## 1. src/ の import は副作用ゼロ

- **`src.*` の import は副作用ゼロ**（QApplication 生成なし・シングルトン生成なし・ファイル読み書きなし）。テストからは `from src.XXX import YYY` の
  通常 import でよい
- **唯一の例外は `src/Autona.py`**: この import 自体が起動シーケンス（logger 構成 → `bootstrap_core()` → `bootstrap_gui()` → Window 定義）なので、
  テストや調査スクリプトから import しない。import するのは `main.py` の `from src import Autona` だけ
- 新しいモジュールは **module level で「作る／読む／書く」をしない**。生成は `Initialize()` / `__init__` / `bootstrap.py` 側へ置く
- pin: `tests/test_import_side_effects.py`（subprocess）。ここが落ちたら実装側を直し、テスト側を緩めない

## 2. シングルトンの生成順の正本は `src/bootstrap.py`

import 順序は生成順を決めない。生成は 2 段で、`src/Autona.py` とテストハーネス（`tests/conftest.py`）が両方をこの順で呼ぶ:

- `bootstrap_core()` — どのシェルにも要るもの（設定・state 層と Bridge・`AutonaActions`・カタログ・大会 provider・ゲームのローダー
  `GameLoader.instance` ほか）。**`QCoreApplication` だけで動く**（QtGui / QtWidgets のオブジェクトを作らない）。ヘッドレス起動はここで止まる
- `bootstrap_gui()` — デスクトップの Qt アダプタ（ホットキー・Updater・ボードの作成・シェル操作の登録）。先頭で `bootstrap_core()` を呼ぶ

規則:

- 順序を変えるときは `bootstrap.py` の `ORDER CONSTRAINT:` コメントを必ず読む（誰が誰に connect するか・`StateManager.Initialize()` の後で
  なければならないか等がその場に書いてある）。pin: `tests/test_bootstrap_split.py`（各段の手順を AST で、スキャンの後の順
  （provider の `SetGameFromProvider` → ローダーの `_RestoreSelectedGame`）を実際のスキャンで）
- **絶対に守る**: `GameAssetCatalog` を生成して `ResetGameState()` してから、それを読む `GameLoader`（`make_game_loader`。provider と
  `AssetPackService` の後）を作る。provider はローダーに connect しない（フック `selectGameFromStartGGId` は `bootstrap_core()` が入れ、
  スキャンの後の `SetGameFromProvider` はローダーの `afterScan`）
- ボードの作成 `AutonaActions.MakeBoards(panel=True)` とシェルのアダプタ `AutonaShellOperations` は `bootstrap_gui()` の最後、Window より前
  （`Window._SeedPanelState` と Web サーバーより前）。`MakeBoards` は起動 1 回だけ。生きているボードに `SeedBoard` を呼ばない（作ったボードを
  スケルトンで上書きする）
- 新しいシングルトンは、`QCoreApplication` で作れて全シェルが使うなら `bootstrap_core()`、QtGui / QtWidgets が要るかデスクトップだけが使う
  なら `bootstrap_gui()` に足す。モジュール末尾で `instance = XXX()` を書かない（#1 に違反する）
- Web 操作のシェルごとの違いは `AutonaWebOperations` への登録だけ: デスクトップはシェル操作 3 本（`openSetSelection` / `renderThumbnail` /
  `startImageRender`）を登録し、ヘッドレスは何も登録しない。ゲームのローダーは `bootstrap_core()` が両シェル共通で登録する
- ボードホストは両シェルとも `AutonaWebOperations` の `BoardHost`（呼び出し元のスレッドで 1 本の RLock の下）。ボードの操作の中で GUI
  スレッドを待たない。終了の開始からはメインスレッド以外の `Run` を 503 で断るので、終了中に書かなければならない後始末は
  `Run(op, atShutdown=True)`（実例: ゲームのローダーの `_Publish`）
- シェル操作を増やさず、新しい Web 操作は core に置く（`startImageRender` だけが例外）。シェル操作を除き `@gui_thread_sync` を新しく足さない
  （残っているものの分類は `docs/changes/architecture-modernization.md` の S3e-9 節）

## 3. StateManager のキーパスはドット区切り文字列

- `StateManager.Set("score.1.team.1.player.1.name", value)` — 正しい。`"score/1/team/1"` は間違い
- 数値も文字列: `"1"` であって `1` ではない

## 4. （解消済み・再導入禁止）objectName をキーパスにしない

- キー名は `src/core/StateSchema.py` の定数が正本で、文字列は保存データ（`user_data/state.json`）のキー（変えるなら `StateMigrations` に移行 step）。
  新しい入力欄のキーは `StateSchema` に定義してから使う。objectName からキーを組まない（`DYNAMIC_KEY_FAMILIES` は空。pin:
  `tests/test_state_schema.py`・`tests/test_objectname_keypath_contract.py` の `EXPECTED`）
- `src/core` 外で `StateManager.Set` / `Unset` を呼んでよいのは `AutonaTournamentDataProvider` だけで、`Q*` を継承するクラスは state を書かない
  （pin: 同ファイルの `test_the_state_writer_ledger_outside_core_is_the_documented_one` / `test_no_qt_widget_writes_the_state`）。新しい書き込みは
  `AutonaActions` か core のドメインサービスに足す

## 5. （退役）選手ウィジェットは無い

選手の画面は React の SCORE ページ（`web/src/ScorePage/`）、書き込みは `core/PlayerService.py` だけ。番号を保つために残している。

## 6. SaveBlock の対称性

- `StateManager.BlockSaving()` と `StateManager.ReleaseSaving()` は必ずペアで呼ぶ。ブロック数がマイナスになると SaveState が暴走する
- `with StateManager.SaveBlock():` を使うのが安全（try/finally の手書きは例外時に ReleaseSaving を漏らしうる）。pin: `tests/test_save_block_symmetry.py`

## 7. 選手スロットの path は `score.{N}.team.{T}.player.{M}` だけ

- `PlayerService` を束縛する path はこの形だけ（Web の操作・セット読込・`AutonaActions.ClearPlayers`・`RepublishTeamMergedNames` が組む）。
  別の形を足すなら `PlayerService.BoardNumber()`（`score.*` 以外は `None`）と v1 の選手ルートのスロットの存在検査を見直す
- 書き込みは path に束縛した `PlayerService` が唯一の経路。読込・クリアも core（`ApplyData` / `ClearSlot`。pin: `tests/test_player_load.py`）
- 選手 DB への保存: Web の `savePlayerToDb` は `PrepareSaveToDB` + `SaveRecordToDB`（ウィジェットではなく state から組む）、選手一覧ページの
  保存・削除は `core/PlayerDirectory`
- 統計の依頼は `PlayerService.Announce(SlotChange)` がボードのスロットでだけ行う。`SetSeed` / `SetPlayerId` は依頼しない（要るなら呼び手が
  `Announce(SlotChange(...))` を呼ぶ）
- `player_editor` は古い `state.json` の掃除のために `RETIRED_ROOTS` に残っている

## 8. Autona.exe はプロジェクト直下

実行に使うのはプロジェクト直下の `Autona.exe` と `_internal/`（`dist/` ではない）。ビルドは `dist/Autona/` に出し、両方を直下へコピーする
（`_internal/` が無いと起動しない。手順は CLAUDE.md と build-and-release.md）。

## 9. Qt シグナルハンドラ内で `processEvents()` を呼ばない

`processEvents()` はイベントループを同期で再入させ、同じシグナルの再 dispatch や構築途中のウィジェットへの paint を起こして C++ レベルの
access violation になった。

- レイアウト解決後に何かしたいときは `QTimer.singleShot(0, fn)` で defer する
- 長時間走るハンドラには再入ガード（フラグを立てて `try/finally` で戻し、再入は warning を出して return）を置く
- `QSpinBox.valueChanged` 等のシグナル内からモーダル（`QMessageBox.exec()`）を呼ばない（CLAUDE.md の「モーダル入れ子禁止」と同根）

## 10. （実例なし・Qt の一般則）`QGraphicsScene.clear()` を連続で呼ばない

`QGraphicsProxyWidget` を大量に載せた scene で `clear()` と再構築を繰り返すと、proxy の非同期破棄と新規生成が競合して access violation を
起こした。loading 表示は scene 外のオーバーレイ（parent=QGraphicsView）にし、scene は触らない。

## 11. Start.gg GQL クエリの 20 秒タイムアウト

- `StartGGDataProvider._request_timeout_secs = 20`。重いクエリ（`TournamentPhaseGroupQuery` の seeds + sets など）にフィールドを足すとすぐ
  timeout する。bracket 表示に必須でないネストの深いフィールド（`user.images`・`authorizations`・`player.sets.games.selections` など）は削る。
  レスポンス 1MB 超は危険域
- `QueryRequests` は指数バックオフ（1/2/4/8 秒）で、5 回失敗すると `{}` を返す。呼び出し側は空データでも壊れないように null-safe に書く。
  失敗と空を区別したい呼び手は provider の `strict=True` を使う

## 12. Edit/Write ツール後は `__pycache__` が古い可能性

Windows で Edit/Write がソースの mtime を更新しないことがあり、古い .pyc が使われ続けた。`main.py` 冒頭の `sys.dont_write_bytecode = True` が
根本対策。それでも疑わしければ `find src -name "*.pyc" -delete` で消して再実行する。

## 13. （実例なし・Qt の一般則）共有する Qt のモデルは差し替えない

PySide6 のプロキシ・ビュー・コンプリータはソースモデルへの Python 参照を保持しないことがあり、再代入で旧モデルの最後の参照を落として
起動時の access violation を出した（2026-07-11）。モデルは差し替えずに in-place で入れ替える（生涯 1 回だけ `setModel`、以後は `clear()` +
`appendRow()`）。どうしても差し替えるなら旧モデルを属性に退避して付け替えが終わるまで生かす（退避はリークを防がない）。

## 13b. （実例なし・Qt の一般則）`invisibleRootItem().appendRows()` は item を解放しない

使い回すモデルを `clear()` → 再充填する構成で、`invisibleRootItem().appendRows(rows)` は追加した item を解放せず増え続けた（実測）。
`model.appendRow(item)` のループか、既存 item の `setData` による in-place 更新を使う。

## 14. （実例なし・Qt の一般則）QLayoutItem は破棄前に `Shiboken.invalidate()` する

`QLayoutItem` / `QWidgetItem` / `QSpacerItem` は QObject でなく、C++ 側で破棄されても wrapper が残り、同じアドレスの次のオブジェクトの構築中に
無関係な場所で access violation を起こした。レイアウトの item を破棄する操作（re-home・レイアウト破棄・`removeWidget()`）の前に配下の item を
`Shiboken.invalidate(item)` する。`takeAt(i)` → `deleteLater()` をしない。サブレイアウトは `itemAt()` 走査でなく `findChildren(QLayout, ...)` で探す。

## 15. （実例なし・Qt の一般則）`QCompleter(model)` はモデルへの参照を保持しない

コンストラクタ渡しは保持せず、`QCompleter.setModel(model)` は保持する（PySide6 6.11 実測）。共有モデルは `QCompleter()` + `setModel()` に分ける。

## 16. StateManager の observer から GameAssetCatalog の writer を呼ばない

catalog は「catalog lock → `StateManager.lock`」の順で取る。`StateManager.lock` 保持中の observer（Bridge の direct 接続ハンドラ含む）から writer
（`SelectGame` / `ForgetSelection` / `ScanGames`）を呼ぶと順序が逆転してデッドロックする。必要なら `QTimer.singleShot(0, ...)` 等で observer の外へ出す。

## 17. held 通知と `state_updated` のハンドラは `StateManager.lock` の中で走る。その中で別スレッドを待たない

- `held_state_updated` / `held_dirty_changed` / `state_updated` は `StateManager.lock` の中で出る。GUI スレッドで書いた変更なら Bridge 経由の
  ハンドラも direct に lock を持ったまま走る
- だからハンドラの中で別スレッドを待たない: `gui_thread_sync` / `run_sync` / `QDialog.exec()` / `processEvents()` をしない（待たれたスレッドが
  lock を取りに来ていればデッドロック。`exec` / `processEvents` は再入も起こす: #9）。受け手は積むだけにする
- held の通知を lock の外へ出す案は採らない: ハンドラ全体が lock の中で走ることが暗黙の原子性になっていて、外へ出すと別スレッドの書き込みが
  割り込む
- pin: `tests/test_roster_flow.py` の `test_no_thread_is_waited_on_inside_a_held_cycle`
