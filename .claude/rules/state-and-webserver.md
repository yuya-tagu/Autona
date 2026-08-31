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
