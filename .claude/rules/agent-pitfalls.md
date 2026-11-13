---
description: フェーズ1調査で発見した、エージェントが踏みやすい地雷
globs:
  - "src/**/*.py"
  - "tests/**/*.py"
---

# エージェント向け地雷回避ルール

## 1. src/ の import は副作用ゼロ（2026-07-29 Step 3 完了により更新）

**旧ルール（`src` の import が QApplication + 全シングルトン生成を引く／テストは `direct_import()` を使う）は解消済み。** `src/__init__.py` は docstring だけの空 init になり、`direct_import()` は削除した。

新しい原則:

- **`src.*` の import は副作用ゼロ**（QApplication 生成なし・シングルトン生成なし・ファイル読み書きなし）。テストからは `from src.XXX import YYY` の**通常 import でよい**
- **唯一の例外は `src/Autona.py`**（アプリ組み立てモジュール）。この import 自体が起動シーケンス（logger 構成 → `bootstrap_core()` → `bootstrap_gui()` → Window 定義）なので、テストや調査スクリプトから import しない。`main.py` の `from src import Autona` だけがこれを行う
- 新しいモジュールを足すとき、**module level で「作る／読む／書く」をしない**。生成は `Initialize()` / `__init__` / `bootstrap.py` 側へ置く
- この原則は `tests/test_import_side_effects.py` が subprocess で pin している（`import src` 自体の inert 検査＋主要モジュール群の副作用検査）。ここが落ちたら実装側を直す。テスト側を緩めない

## 2. シングルトン初期化順序の正本は `src/bootstrap.py`（2026-10-25 ADR-005 S3c で 2 段に分割）

旧来は `Autona.py` の **import 順序**が生成順序を決めていたが、現在は `src/bootstrap.py` に**明示的な生成順として集約**されている。import 順序は順序を決めない。生成は 2 段:

- `bootstrap_core()` — どのシェルにも要るもの（設定・state 層と Bridge・`AutonaActions`・Qt 非依存カタログ・大会 provider・`TournamentInfoService` 等）。**`QCoreApplication` だけで動くこと**（QtGui / QtWidgets のオブジェクトを作らない）。ヘッドレス起動はここで止まる
- `bootstrap_gui()` — デスクトップの Qt アダプタ（ホットキー・ゲームアセット／国モデル・PlayerDB のモデル結線・Alert / Updater・スコアボード dock）。先頭で `bootstrap_core()` を呼ぶ。`src/Autona.py` が両方をこの順で呼ぶ。テストハーネス（`tests/conftest.py`）も両方呼ぶ

- 順序を変えるときは `bootstrap.py` の `ORDER CONSTRAINT:` コメントを必ず読む。制約の理由（誰が誰の `instance.signals` に connect するか、QApplication 必須か、`StateManager.Initialize()` 後でなければならないか等）がその場に書いてある。各段の手順（生成・結線・`Initialize()`・Designer の移行）とその順は `tests/test_bootstrap_split.py` が AST で、`onLoadAssets` のスロット順（provider → アダプタの `_RestoreSelectedGame`）は実際の emit で pin している
- **絶対に守る**: `GameAssetCatalog`（core）を生成して `ResetGameState()` してから、それを包む `AutonaGameAssetManager`（`bootstrap_gui()`）を作る（ADR-005 S1a）。`AutonaTournamentDataProvider` はもうアダプタに connect しない: ゲーム選択のフック `selectGameFromStartGGId` と `onLoadAssets` → `SetGameFromProvider` の結線はシェル側（`bootstrap_gui()`）が行う。ボードの作成 `AutonaActions.MakeBoards(panel=True)`（ADR-005 S4ac-1: `ResetBoards` → `ResetScoreRoot` と、3 ボードの `EnsureBoard`。ボード 1 / 2 は起動時から held。それまでは `ResetBoards` が Qt のスコアボードのマネージャの `__init__` にあり、ボードはウィジェットの構築で作っていた）とシェルのアダプタ `AutonaShellOperations`（ADR-005 S4y）は `bootstrap_gui()` の最後で、Window より前（`Window._SeedPanelState` / Web サーバーより前）。`MakeBoards` は起動 1 回だけ（作成済みのボードがあれば 2 回目は警告して何も書かずに False。ガードが無ければ held のボードのシードが保留される）。生きているボードに `SeedBoard` を呼ばない（作ったボードをスケルトンで上書きする。S4ac-1 まではボードのウィジェットの構築 `CreateScoreboard` が呼んでいた。Qt の SCORE とマネージャ `AutonaScoreboardManager` は S4ac-3 で削除）
- 新しいシングルトンは、QCoreApplication で作れて全シェルが使うなら `bootstrap_core()`、QtGui / QtWidgets が要るかデスクトップだけが使うなら `bootstrap_gui()` に追加する。モジュール末尾で `instance = XXX()` を書かない（#1 に違反する）
- Web 操作のシェルごとの違いは `AutonaWebOperations` への登録だけ（ADR-005 S3c / S3e-9）: デスクトップは `bootstrap_gui()` が `loadGameByCodename`・シェル操作 3 本（`openSetSelection` / `renderThumbnail` / `startImageRender`）を登録し、ヘッドレスは `src/headless.py` がローダーだけを登録する。ボードホストは両シェルとも `AutonaWebOperations` 自身の `BoardHost`（呼び出し元のスレッドで、1 本の再入可能ロックの下。ADR-005 S4ad で GUI スレッドで走らせていたデスクトップの `DesktopBoardHost` を削除した。ボードの操作の中で GUI スレッドを待たない。終了の開始からはメインスレッド以外の `Run` を 503 で断るので、終了中に書かなければならない後始末は `Run(op, atShutdown=True)`）。`WebServer` に操作を渡す経路は無い（`WebServerActions` は S3e-9 で削除）。シェル操作を増やさず、新しい Web 操作は core に置く（`startImageRender` は ADR-005 S4o で足した唯一の例外で、理由は `architecture-modernization.md` の S4o 節。オフスクリーンの描画はデスクトップにしか無く、Result も同じ 1 本を使う）。シェル操作を除き `@gui_thread_sync` を新しく足さない（残っているものの分類は `docs/changes/architecture-modernization.md` の S3e-9 節）

## 3. StateManager のキーパスはドット区切り文字列

- `StateManager.Set("score.1.team.1.player.1.name", value)` — 正しい
- `StateManager.Set("score/1/team/1", value)` — **間違い**
- 数値も文字列: `"1"` であって `1` ではない

## 4. `element.objectName()` をキーパスに使う旧パターン（解消済み・再導入禁止）

Qt の `objectName()` がそのまま StateManager のキーになる旧パターンは、Headless Core
Step 5（第 1〜3 弾、2026-09-01/02）で**全ウィジェット静的化完了**。`findChildren` で
拾ったウィジェットに名前を聞いてキーを組む書き方はもう存在しない。現在は
`src/core/StateSchema.py` の定数が正本で、ウィジェットはそれを **`findChild` の引数として**
使う（方向が逆になった）。

| 族 | 定数 | 書き手 |
|----|------|--------|
| `score.{N}.{phase,pool,match}` | `SCORE_MATCH_FIELDS` | `AutonaActions` |
| `score.{N}.team.{T}.{teamName,losers}` | `SCORE_TEAM_INPUT_KEYS` | `AutonaActions` |
| `score.{N}.team.{T}.player.{M}.*` | `PLAYER_INPUT_KEYS` | `core/PlayerService.py` |
| `tournamentInfo.*`（大会情報の入力欄。Qt の EVENT タブは ADR-005 S4g で React 化） | `TOURNAMENT_{TEXT,NUMBER,DATE}_FIELDS` | `core/TournamentInfoService.py` |

- **ADR-005 S4ac-3 で `src/layout/`（最後の `.ui`）と Qt の SCORE を削除し、このパターンの対象のウィジェットは残っていない**。
  schema の文字列は保存データ（`user_data/state.json`）のキーなので変えない（変えるなら `StateMigrations` に移行 step）
- 新しい入力欄（React のページ）のキーは `StateSchema` に定義してから使う（逆をやらない）。
  **`findChildren` ループで objectName からキーを組む書き方に戻さない**
- pin: `StateSchema.DYNAMIC_KEY_FAMILIES` は**空**でなければならない
  （`tests/test_state_schema.py`）。`tests/test_objectname_keypath_contract.py` が
  schema 名と当時の .ui の objectName の凍結した写し `EXPECTED` の一致を pin する
  （.ui の解析と「該当ウィジェットに `objectName()` 呼び出しが無い」「該当ウィジェットが
  `StateManager.Set/Unset` を直接呼ばない」の AST の pin は、対象が消えたので S4ac-3 で外した）
- **2026-09-03（Step 5 完了）: ウィジェットは `StateManager.Set/Unset` を呼ばない。**
  `src/core` 外で呼んでよいのは `AutonaTournamentDataProvider` の 1 ファイルだけで
  （`game.*` は 2026-10-23 の ADR-005 S1a で `core/GameAssetCatalog.py` へ移った）、
  `tests/test_objectname_keypath_contract.py` の
  `test_the_state_writer_ledger_outside_core_is_the_documented_one` が**その集合そのもの**を、
  `test_no_qt_widget_writes_the_state` が「`Q*` を継承するクラスは書かない」を pin する。
  新しい書き込みは `AutonaActions` か core のドメインサービスに足す
  （台帳は `docs/changes/headless-core-redesign.md`）

## 5. （解消済み・退役）選手ウィジェットは無い

「選手ウィジェットが 2 実装あり、片方を直したらもう片方も直す」規約だったが、
**`AutonaTeamPlayerWidget` は 2026-08-28（Headless Core 再設計 Step 0）にデッドコードとして削除**し、
残った `AutonaScoreboardPlayerWidget` / `AutonaBasePlayerWidget` も **2026-11-11（ADR-005 S4ac-3）に
Qt の SCORE ごと削除**した。選手の画面は React の SCORE ページ（`web/src/ScorePage/`）、書き込みは
`core/PlayerService.py` だけ。この項は番号を保つために残している。

## 6. SaveBlock の対称性

`StateManager.BlockSaving()` と `StateManager.ReleaseSaving()` は必ずペアで呼ぶ。ブロック数がマイナスになると SaveState が暴走する。

- `with StateManager.SaveBlock():` を使うのが安全
- try/finally で囲む既存パターンもあるが、例外時に ReleaseSaving を漏らすリスクがある

## 7. 選手スロットの path は `score.{N}.team.{T}.player.{M}` だけ（2026-11-11 ADR-005 S4ac-3 で更新）

`PlayerService` を束縛する path は `score.{N}.team.{T}.player.{M}` だけ（Web の操作・セット読込・
`AutonaActions.ClearPlayers`・`RepublishTeamMergedNames` が組む）。S4ac-3 までは Qt の選手カード
（`AutonaScoreboardPlayerWidget.path`。構築元はスコアボードの `AutonaScoreboardWidget._AddCard` の 1 つだけ）も
同じ形で、カードは S4ac-3 で Qt の SCORE ごと削除した。

旧記述の `commentary.{N}` / `{base}.slot.{I}.player.{M}` / `player_editor.1` は**もう存在しない**
（実況者パネルは React 版のみで選手ウィジェットを持たず、選手リスト（PLAYERS ロスター）は廃止済み。
Qt の選手一覧と、カードを `player_editor.1` に貼って流用した編集ダイアログは S4n で削除。編集の下書きは
React ページのローカルにあり、state には出ない）。`player_editor` は、古い `state.json` の掃除のために
`RETIRED_ROOTS` に残っている。
構築元の数と形を pin していた `tests/test_player_widgets.py` は、カードと一緒に削除した
（選手の読込の pin は `tests/test_player_load.py` へ移した）。

- 別の形のパスを足すなら、`PlayerService.BoardNumber()`・v1 の選手ルートのスロットの存在検査を見直す。
  `score.*` 以外は想定していない
- 書き込みは `self.player`（`src/core/PlayerService.py`、path に束縛された
  1 インスタンス）が唯一の経路。ボード番号が要る処理は `PlayerService.BoardNumber()`
  を使う（`score.*` 以外のパスには `None` を返す。防御的なパース）
- **選手の読込・クリアも core**（ADR-005 S3e-3）: `PlayerService.ApplyData` / `ClearSlot`
  （S4ac-3 まではカードの `SetData` / `Clear` もこれを呼んで描画するだけだった）
- **選手 DB への保存**: Web の `savePlayerToDb` は `PlayerService` の
  `PrepareSaveToDB` + `SaveRecordToDB`（**ウィジェットではなく state から**組む）、選手一覧ページの
  保存・削除は `core/PlayerDirectory`（v1 `PUT /players` / `POST /players:delete`。ADR-005 S4m）
- 統計の依頼は core（ADR-005 S3e-5）: `PlayerService.Announce(SlotChange)` が
  ボードのスロット（`score.*`）でだけ `StatsService.PlayerChanged` を呼ぶ（読込・クリアは
  自分の保存ブロックの中で。v1 の `Edit` も同じ。`SetSeed` / `SetPlayerId` は統計を依頼しない
  （Qt のカードの手入力のための `announce=` 引数は削除した。依頼するなら呼び手が明示的に `Announce(SlotChange(...))` を呼ぶ。テストの `edit_tag` が録画の再生でそうしている）。カードの統計シグナル（`playerId_changed` / `player_seed_changed` ほか）と `AnnounceChange` は
  削除済み。`SlotChange` は操作の前後の state の（id, seed）の比較

## 8. Autona.exe はプロジェクト直下

ビルド後の実行ファイルは `Autona.exe`（プロジェクトルート直下）。`dist/Autona.exe` ではない。
PyInstaller の `--distpath .` オプションで直下に出力している。

## 9. Qt シグナルハンドラ内で `processEvents()` を呼ばない

`QGuiApplication.processEvents()` はイベントループを同期で再入させるため、
シグナルハンドラ内で呼ぶと同じシグナルが再度 dispatch されたり、
構築途中のウィジェットに対して paint イベントが走ったりして C++ レベルの
access violation を起こす。

- widget レイアウト解決後に何かしたいときは `QTimer.singleShot(0, fn)` で defer
- 長時間走るハンドラには再入ガードを置く（旧 Top 8 パネルの `UpdatePhaseGroup` が実例だった。
  ADR-005 S3e-8 で取り込みが core の `Top8Service` に移り、パネルからは消えた）:
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
  （旧参考実装 `AutonaBracketView.loadingOverlay` は Top 8 専用化で削除済み）

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

## 13. 共有 QAbstractItemModel の再構築は「旧世代を retire」してから

PySide6 の `QSortFilterProxyModel.setSourceModel()` / `QComboBox.setModel()` /
`QCompleter.setModel()` は **ソースモデルへの Python 参照を保持しない**。
`self.model = QStandardItemModel()` のような再代入で旧モデルの最後の参照を
落とすと、プロキシ／ビュー／コンプリータがまだ旧モデルへの行マッピングや
永続インデックスを持ったまま C++ 側が破棄され、次のリセット処理で
access violation を起こす（2026-07-11 起動クラッシュ:
`AutonaPlayerDB.SetupModel` の `setSourceModel` で発生）。

- 再構築時は旧モデルを属性に退避して**最低 1 世代は生かす**:
  `AutonaPlayerDB._retiredModel` / `AutonaGameAssetManager._retiredModels` が実例
- 新モデルへの `setSourceModel()` / `setModel()` の付け替えが完了するまで
  旧モデルを削除しない。全消費者が onLoad 等の後続ハンドラで付け替える場合は
  ハンドラ完了まで（= 次回再構築まで）保持する
- 補足（2026-07-12 単体検証）: 現行 PySide6 では素の `setModel` /
  `setSourceModel` / `QCompleter.setModel` は keepReference を持ち、単純な
  差し替え単体は安全。ただし編集可能コンボ（オートコンプリータ／ポップアップ
  付き）やシグナル連鎖の深部での差し替えは保証外のため、退避パターンを常用する

**そもそもモデルを差し替えない方が安全（2026-08-21 更新）**: retire パターンは
AV は防ぐが**リークは防がない**。`AutonaBasePlayerWidget.LoadStates`（S4ac-3 で削除）は
国変更のたびに新しい `QStandardItemModel` を張り替えており、**捨てたモデルが
解放されず蓄積**していた（セット読込 1 回につき選手 1 人あたりモデル 1 個・
`QStandardItem` 約 106 個。実測 300 反映で約 +100MB）。
対策は state コンボの **in-place refill**（`_EnsureStateModel()` で
生涯 1 回だけ `setModel`、以後は `clear()` + `appendRow()` で行を入れ替え。
`_retiredStateModel` は廃止）だった。選手ウィジェットと、同じ形の「1 モデルを使い回す」実装の
`AutonaSelectSetWindow` は ADR-005 S4ac-3 で Qt の SCORE ごと削除した。共有の国モデル `AutonaCountryHelper.countryModel`（#15）も
Qt モデルの整理 PR2 で削除した。今、この形の実例は無い（Qt の一般則として残す）。

- 保持機構は未特定（素の `QComboBox` 単体では旧モデルは解放される）。
  一般則として「コンボは渡された全モデルを保持する」と書かないこと。
  実アプリ構成でのみ再現する事象として扱い、**差し替えないことで機構に依存せず
  回避する**のが正解
- pin だった `tests/test_player_widgets.py`（`test_reloading_states_does_not_accumulate_models` ほか）は
  対象と一緒に削除した。リークした操作列は「ブランク国を経由する再構築」= `Clear()` → `SetData` で、
  国 A → 国 B の直接切替では再現しなかった。同じ形のコンボを Qt に書くことがあれば、テストは実際の読込の形で書く

## 13b. `QStandardItem.appendRows()` は追加した item の wrapper を解放しない（2026-08-21 実測）

親 item 経由の一括追加（`model.invisibleRootItem().appendRows(items)`）は、
追加した item を **root item の wrapper に登録したまま解放しない**。モデルを
使い回して `clear()` → 再充填する構成だと、`clear()` しても登録が残るため
**item だけが無限に増える**（モデル数は増えないので気づきにくい）。

- 安全: `model.appendRow(item)` のループ / `model.removeRows()` + `appendRow` /
  既存 item を `setData` で書き換える in-place 更新（いずれも実測で増加ゼロ）
- 危険: `invisibleRootItem().appendRows(rows)`（6 回リフィルで +318 item）
- ライブモデルへ 1 行ずつ追加するとビュー／コンプリータへの通知が行数ぶん出る。
  `with QSignalBlocker(combo):` で囲み、最後に必要なシグナルだけ 1 回 emit して
  旧 `setModel` 相当の発火に揃える（実装例だった `LoadStates` は S4ac-3 で削除。
  国モデル `AutonaCountryHelper` の再充填も `appendRow` のループだった。Qt モデルの整理 PR2 で削除）

## 14. QLayoutItem 系は PySide6 の追跡外 — 破棄前に `Shiboken.invalidate()` する（2026-08-03 フルスイートAV根治で確定）

`QLayoutItem` / `QWidgetItem` / `QSpacerItem` は QObject ではない。C++ 側で破棄されても PySide6 は検知できず、Python wrapper が Shiboken のバインディングマネージャに valid のまま残る。

- `QLayout.itemAt()` / `spacerItem()` が返す wrapper はレイアウトの Shiboken 子として保持される（参照を捨てても同じ wrapper が返る）。「一時変数だから大丈夫」は成り立たない
- 残った登録は地雷: 同アドレスに次の C++ オブジェクトが確保されると、そのコンストラクタ内で Qt が仮想 `metaObject()` を呼ぶ（Shiboken 登録前）ため古い wrapper が引き当てられ、`PySide::SignalManager::retrieveMetaObject` が NULL 参照して**無関係な場所で access violation**（実例: `QCompleter` 構築中。クラッシュ箇所は実行ごとに移動する）
- item が破棄される操作: ウィジェットの re-home（別レイアウトへ addWidget）/ レイアウト破棄 / `QLayout.removeWidget()`（内部で `delete takeAt(i)`）
- 対処: 破棄の前に対象レイアウト配下の item wrapper を `Shiboken.invalidate(item)` で無効化する。実装例だった `AutonaScoreboardPlayerWidget._releaseLayoutItemWrappers` は ADR-005 S4ac-3 で Qt の SCORE ごと削除した（git 履歴の f71a3b90 以前を参照）。サブレイアウトは QObject なので invalidate せず再帰
- 禁止パターン: `takeAt(i)` → `deleteLater()`（takeAt/removeItem は所有権を Python へ移し、deleteLater はそれを Qt に戻す → Qt が Shiboken 無断で解放）。`removeItem()` だけにして PySide に解放させる
- サブレイアウト探索は `itemAt()` 走査でなく `findChildren(QLayout, Qt.FindChildOption.FindDirectChildrenOnly)` / `indexOf()` を使う（走査が通りすがりに QWidgetItem を wrap してしまう）
- pin だった `tests/test_wrapper_lifetime.py` は実装例と一緒に削除した（規則自体は Qt の UI 一般に残る。レイアウトの item を破棄する Qt のコードを新しく書くなら pin も書く）

## 15. `QCompleter(model)` はモデルへの Python 参照を保持しない（PySide6 6.11 実測）

- コンストラクタ渡しは参照を保持せず、`QCompleter.setModel(model)` は保持する。共有モデルは `QCompleter()` + `setModel()` に分ける
- 関連（削除済み・実例なし）: 共有の国モデル `AutonaCountryHelper.countryModel`（初回ロードで 1 個作ったきり差し替えず、再ロードは `clear()` + `appendRow` で入れ替え、`countriesAboutToReload` / `countriesUpdated` で消費者が経路を止めて選び直す形）と、その消費者（選手ウィジェット・Top 8）は ADR-005 S4ac-3 / Qt モデルの整理 PR2 までに全部削除した。国・地域は Qt-free の `core/CountryCatalog` を直接読む。Qt の一般則（共有モデルは差し替えない・コンプリータは `QCompleter()` + `setModel()`）だけが残る

## 16. StateManager の observer から GameAssetCatalog の writer を呼ばない（ADR-005 S1a）

- StateManager の observer（Bridge の direct 接続ハンドラ含む）から `GameAssetCatalog` の writer（`SelectGame` / `ForgetSelection` / `ScanGames`）を呼ばない。catalog は「catalog lock → `StateManager.lock`」の順で取るので、`SM.lock` 保持中の observer から writer を呼ぶと順序が逆転してデッドロックする。必要なら `QTimer.singleShot(0, ...)` 等で observer の外へ出してから呼ぶ

## 17. held 通知と `state_updated` のハンドラは `StateManager.lock` の中で走る。その中で別スレッドを待たない（ADR-005 S3e-6 レビュー）

- `held_state_updated` / `held_dirty_changed` / `state_updated` は `StateManager.lock` の中で出る（`SaveState` の中。lock を持った呼び出し側の中で書いたときはその中）。GUI スレッドで書いた変更なら、Bridge 経由のウィジェットのハンドラも direct に、lock を持ったまま走る
- だからハンドラの中で別スレッドを待たない: `gui_thread_sync` / `run_sync` / `QDialog.exec()` / `processEvents()` をしない。待たれたスレッドが lock を取りに来ていればデッドロックになる（`exec` / `processEvents` は再入も起こす: #9）
- Qt のカードの構築（`AutonaScoreboardWidget._SyncPlayerCards`）は lock の中で行い、それを許容していた（同じ held サイクルの後続ハンドラが、カードがあることを前提にしていたため。S4ac-3 で Qt の SCORE ごと削除）。held の通知を lock の外へ出す案は採らなかった: ハンドラ全体が lock の中で走ることが暗黙の原子性になっていて（当時の例: ステージボタンの `PublishStageResults` の held ハンドラの途中に Flask スレッドの `score:adjust` が割り込まなかった。例の機能は ADR-005 S4ac-4 で削除したが、理由自体は残る）、外へ出すとその競合が入るため
- pin: `tests/test_roster_flow.py` の `test_no_thread_is_waited_on_inside_a_held_cycle`（S4ac-3 から core の held サイクルだけを見る）
