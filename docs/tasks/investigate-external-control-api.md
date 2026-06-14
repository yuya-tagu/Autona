# Investigate External Control API

> **これは調査タスク。実装はまだ行わない。** コード変更禁止。成果物は事実情報と
> 未決事項の整理のみ。設計判断は [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) で行う。

## Goal

外部制御 API / Companion / Stream Deck 連携を設計するために、**既存の制御面の実態を
確定**する。具体的には以下を一次情報（コード）から明らかにする:

- 既存 HTTP/WS ルートの完全な一覧と入出力契約
- 既定ポートと Web UI ルーティングの実値
- 状態変更が StateManager を単一経路として通っているかの確認
- 外部制御を足す場合の同時操作・認証・起動順の論点整理

## Preconditions

- 作業ブランチで実施（コードは読むのみ、変更しない）
- 実行確認が必要な場合は `python main.py`（CLAUDE.md のビルド方針に従う）
- 機密（Bluesky の App Password 等の認証情報）は読み込まない/出力しない

## Documents to read first

1. [../architecture/current-architecture.md](../architecture/current-architecture.md) — 全体像
2. [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md) — 影響観点
3. [../../CLAUDE.md](../../CLAUDE.md) — アーキ概要・状態キーパス
4. [../../.claude/design/Design.md](../../.claude/design/Design.md) — 実装仕様の正本
5. [../../.claude/architecture/phase-a-b/state-keys.md](../../.claude/architecture/phase-a-b/state-keys.md) — 状態キーパス read/write
6. [../../.claude/rules/state-and-webserver.md](../../.claude/rules/state-and-webserver.md) — 状態/Web サーバーの作法

## Investigation targets

1. **既存ルート棚卸し** — HTTP ルートと Socket.IO イベントを全列挙し、
   パス / メソッド / パラメータ / 返り値 / 対応する Action 関数を表にする
2. **HTTP と WS の対応関係** — どの操作が両方で提供され、どちらかにしかないかを明確化
3. **ポート/ルーティングの実値** — 既定ポート、Web UI の配信ルート（`/web` `/scoreboard` `/team` 等）の確定
4. **状態変更経路** — 各 Action が StateManager 経由で書いているか（バイパスの有無）
5. **起動シーケンス** — WebServer / シングルトンの起動順、外部リスナを足すなら適切な位置
6. **同時操作の現状挙動** — 複数経路からの同時書き込み時の順序・競合（最後勝ち等）

## Existing files to inspect

- [src/AutonaWebServer.py](../../src/AutonaWebServer.py) — ルート/イベント定義
- [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py) — 実処理
- [src/StateManager.py](../../src/StateManager.py) — 状態変更の中枢
- [src/SettingsManager.py](../../src/SettingsManager.py) — 既定ポート等の設定既定値
- [src/Autona.py](../../src/Autona.py) — 起動シーケンス・シングルトン初期化
- [web/src/websocketConnection.ts](../../web/src/websocketConnection.ts) — クライアント側の接続/イベント
- [src/AutonaHotkeys.py](../../src/AutonaHotkeys.py) — ホットキーの操作経路（重複論点）

## Questions to answer

- 既存ルートの完全一覧と契約は？（表形式で）
- 既定ポートと Web UI ルーティングの実値は？（README/FEATURES と CLAUDE.md の記述差を解消）
- すべての状態変更は StateManager を通っているか？通っていない箇所は？
- 外部制御を足すなら、既存サーバー拡張 / 専用アダプタ層のどちらが既存パターンに沿うか？
- 同時操作の競合は現状どう解決されているか（ロック/順序/最後勝ち）？
- 認証は現状あるか？localhost 以外からの接続を想定しているか？
- ステージストライク削除後に残る `assets/rulesets.json` は何に使われるか（無関係なら整理対象か）？

## Deliverables

- [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md) の
  ⚠️ / `TBD` を埋める（既存ルート一覧・ポート・ルーティング・同時操作挙動）
- 既存ルート一覧の表（HTTP/WS 対応付き）— architecture/ 配下に追記 or 新規ファイル
- ADR-001 の Context を事実で補強し、Decision の選択肢を評価できる状態にする

## Out of scope

- コードの変更・新ルート実装・API 設計の確定
- Companion / Stream Deck 側の実装
- 認証基盤の構築
- ステージストライク関連ファイルの削除（調査で要否を判断するに留める）

## Next task after investigation

1. [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) を
   `Accepted` まで進める（責務境界の決定）
2. 決定に基づき、実装チケット（`implement-*`）を `docs/tasks/` に切り出す
