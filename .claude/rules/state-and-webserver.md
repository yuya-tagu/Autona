---
paths:
  - "src/core/**"
  - "src/StateManager.py"
  - "src/StateManagerBridge.py"
  - "src/AutonaWebServer.py"
  - "src/AutonaWebServerActions.py"
  - "src/Helpers/**"
---

# 状態管理・Web サーバー開発ルール

## StateManager

**正本は `src/core/StateManager.py`**（headless core Step 1）。`src/StateManager.py` は
互換 re-export シムで、1 世代だけ残す。新規コードは `from .core.StateManager import StateManager`。

- StateManager は static クラス（インスタンス化しない）
- 状態アクセスは `StateManager.Get(key)` / `StateManager.Set(key, value)`
- キーパスはドット区切り: `score.1.team.1.player.1.gamerTag`
- 一括更新時は `StateManager.SaveBlock()` コンテキストマネージャーで囲む
  - ブロック内ではファイル出力が抑制され、ブロック終了時にまとめて反映される
- スレッドセーフ: `StateManager.lock`（RLock）を使用。直接 state dict を操作しない
- **core は Qt を import しない**（`tests/test_core_qt_free.py` が subprocess で pin）。
  Qt が要る処理はアダプタ（`src/StateManagerBridge.py` が実例）へ出す

## StateSchema（キー定義とシードの正本）

`src/core/StateSchema.py`（headless core Step 2）。**state の「何があるか」の定義を
ウィジェットに書かない**。

- held exempt (`STATS_KEYS`) / 表示トグル (`DISPLAY_OPTIONS`) / 起動時シード
  (`SeedBoard` / `SeedPanelState`) はここが正本。ウィジェット側に同じ定義を置き直さない
- `SeedBoard(n)` は「埋める」ではなく **サブツリーを skeleton に戻す**。生きているボードに
  対して呼ばない（未構築ボードのプレースホルダと、遅延構築時の再シードが用途）
- `KEY_CATALOG` / `DYNAMIC_KEY_FAMILIES` は**列挙のみで誰も消費していない**。Step 5 の
  キーパス静的化とマイグレーション層のアンカー。新しい state キーを足したらここにも書く
  （テストで強制できないためベストエフォート運用。**PR レビュー時に KEY_CATALOG との差分を
  確認する**）
- 例外: `colors.{feature}.*` のシードは `AutonaFeatureColors.SeedColorState()` に残っている
  （キー族の読み書きと同居。Step 3/4 で core へ移す）
- **core → src の逆依存は Qt-free モジュールのみ**: `StateSchema` は `src` 直下の
  `AutonaCommentaryModel` / `AutonaTop8Model`（いずれも Qt 非依存の state モデル。将来 core へ
  移す候補）を import してよい。Qt 依存モジュールの import は禁止で、
  `tests/test_core_qt_free.py` が subprocess で検出する

## AutonaActions（ドメイン操作の唯一の書き手）

`src/core/Actions.py`（headless core Step 3）。**移行済みの操作は
`AutonaActions.instance` 以外が書かない**。インスタンスは `bootstrap_core()` が作る。

- 移行済み: `AdjustScore` / `ResetScore`（board 1,2 のみ）/ `SetTeamColor` /
  `SetDisplayOption` / `ApplyChanges`（反映 = `CommitHeld`）
- 未移行（Qt 側が書き手のまま。理由は `Actions.py` の docstring）: チームスワップ /
  best_of / クルーボードのスコアリセット / セット読込の正規化（Step 4b）
- `score.{n}.team.{t}.score` の書き手は **core 内に 2 系統**（`AutonaActions` と、
  ステージ集計の `GameTrackerService.PublishStageResults` / `UpdateScore`）。
  Step 4a でどちらも core 内には入ったが、呼び出し口は別のまま。加えて**未移行の
  Qt 側経路**（セット読込 / `ClearScore` / `SwapTeams`）がスコアスピンボックスを
  駆動し、その `valueChanged` が `GameTrackerService.UpdateScore` を呼ぶ形で書く。
  **core の外から直接 `Set` する経路は残っていない**（`StageResultsToScore` の
  再 Set は検収レビューで削除済み。表示更新のみ）。両者は state 経由で
  のみ噛み合う。ステージ集計側は自分が書いている区間を
  `GameTrackerService.writingScore` で公開し（ウィジェットは property で委譲）、
  `_SyncScoreDisplays` はその間 `SyncStagesToScore` を呼ばない（呼ぶと 1 クリックで
  次のゲームまで勝ちになる）。完全一本化は Step 4b/5
- 呼び出し側は Qt ウィジェット・`AutonaWebServerActions`・`AutonaHotkeys` の 3 つ。
  **どのスレッドから呼んでもよい**（Qt に触れないのが層の定義）。委譲した
  WebServerActions メソッドからは `@gui_thread_sync` を外す
- **SaveBlock は複数キーを 1 サイクルにまとめる時だけ**使う（単一キーなら不要）。
  かつては「空 `changedKeys` の SaveState = `include_paths=[]` = フィルタなし =
  全 state 差分」という地雷があったが、**Step 4a で `SaveState` 側が空 `changedKeys`
  を早期 return するようになった**ので、包んでも全量 diff にはならない
  （`tests/test_state_manager_held.py` が DeepDiff 呼び出し回数で pin）
- ウィジェット側は通知（`held_state_updated` / `state_updated`）で表示を更新する。
  プログラム的更新は必ず `QSignalBlocker` で囲む（囲まないと `valueChanged` 等が
  発火して書き戻し = エコーループ）。再入ガードの実例は
  `AutonaScoreboardWidget._SyncScoreDisplays`
- ホットキーは `AutonaHotkeys.ConnectActions()` が `bootstrap_core()` から 1 回だけ結線する。
  対象ボードは `AutonaActions.ActiveBoard()`（`SettingsManager.scoreboard.battle_format`）

## ドメインサービス（core の per-board オブジェクト）

`AutonaActions` が「1 回の操作 = 1 メソッド」の入口なのに対し、状態を持つ／ボードごとに
インスタンスが要るドメインロジックは core のサービスに置く（headless core Step 4a）。
**Qt 側は薄いアダプタに徹する**（`AutonaStatsUtil` / `AutonaIndividualGameTracker` が実例）。

| サービス | 所有者 | 責務 |
|---------|--------|------|
| `src/core/StatsService.py` | `AutonaScoreboardWidget.stats.service` | 統計の取得可否判断・アップセット計算・`last_sets`/`history_sets`/`recent_sets`/`upset_factor` の書き込み |
| `src/core/GameTrackerService.py` | `AutonaIndividualGameTracker.service` | ステージ勝敗の記録（`score.{n}.stages.*`）・ステージ⇔スコアの相互整合・`writingScore` ガード |

- **state が記録、ウィジェットは表示**。ステージ結果は `score.{n}.stages.{i}` が正本で、
  ボタンはそれを描画するだけ（`_SyncStageButtons`）。`setChecked()` は `toggled` しか
  出さず `clicked` は出ないので、この再描画はエコーループにならない
- Qt 依存が避けられない値（選手数 = `team1playerWidgets` の長さ）は**呼び出し可能オブジェクトで
  注入**する。core 側が Qt を import しないことが層の定義（`tests/test_core_qt_free.py`）
- **サービスは observer を購読しない**。購読すると所有ウィジェットと同じゴースト問題を
  core 側に持ち込むことになる。サービスは「呼ばれる」だけの存在にする

## 状態変更の通知（observer + Bridge）

通知の正本は Qt 非依存の observer チャンネル（`src/core/Observable.py` の
`ObserverChannel`）で、`StateManager.observers.<名前>` に 4 本ある。core 側は
`notify()` で発行する（8 箇所）。

| チャンネル | 引数 | 意味 |
|-----------|------|------|
| `state_big_change` | なし | 全面リロード。全ウィジェットのフル再描画をトリガーするため頻繁に発行しない |
| `state_updated` | payload dict（`revision` / `delta_index` / `delta`） | 差分のみ。WebSocket 経由で Web UI に配信される |
| `held_dirty_changed` | (prefix, bool) | held サブツリーに未反映の変更が出た(True)／反映された(False) |
| `held_state_updated` | (prefix) | held 配下のライブ値が変わった（保存サイクル単位で合体） |

購読側の使い分け:

- **Qt ウィジェット / Qt を持つオブジェクトは `StateManagerBridge.instance` の同名
  シグナルに `connect` する**（現在 13 箇所）。observer を直接 `subscribe` してはいけない
  — チャンネルは callback を**強参照**し、bound method でも自動解除されないため、
  破棄済みウィジェットが state を書き続ける（ゴーストボード）。
  `tests/test_signal_lifetime.py` が違反を検出する
- **core / 非 Qt コードだけが `StateManager.observers.<名前>.subscribe(cb)` を使う**。
  寿命が短い購読者は自分で `unsubscribe()` する
- `ObserverChannel.notify()` は購読順・同期・**購読者ごとの例外隔離**
  （例外は logger.exception でログして次の購読者へ進む。書き込み側は落ちない）
- `StateManagerBridge` は process 内で 1 個だけ（`bootstrap_core()` が生成。
  2 個目の生成は `__init__` が RuntimeError で拒否する）

## AutonaDictHelper

- `deep_get(d, keys)` / `deep_set(d, keys, value)` でネスト操作
- キーは文字列リスト: `["score", "1", "team", "1"]`
- StateManager 内部で使用。外部から直接呼ぶ場面は少ない

## Web サーバー（Flask + SocketIO）

- `AutonaWebServer` は QThread として別スレッドで動作
- Flask アプリと SocketIO インスタンスは static 変数
- ルートハンドラは `AutonaWebServerActions` に集約
- Web UI からの POST リクエスト → Actions → StateManager 更新 → WebSocket 配信の流れ
- 新しいエンドポイントを追加する場合は `AutonaWebServerActions` に記述する
- CORS は Flask-CORS で全オリジン許可済み

## Helpers

- `Helpers/` 配下はユーティリティ。各ファイルは単一責務
- `AutonaQtHelper.py` の `GuiExecutor` はワーカースレッドから GUI 操作する際の橋渡し
- `AutonaCountryHelper` / `AutonaLocaleHelper` はシグナルを持つ。変更通知が必要な場面で使う
- `AutonaBadWordFilter` は Trie ベース。パフォーマンスに配慮されている
