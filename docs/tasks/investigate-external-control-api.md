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

---

## 調査結果（2026-06-15・コード読解のみ / コード変更なし）

> 出力方針: **Confirmed**（コード/既存ドキュメントで確認）/ **Inferred**（構造・命名から推測）/ **Unknown / TBD**（未確認）。

### 調査の目的
External Control API / Companion / Stream Deck 連携の設計前提として、既存の操作・制御面・状態経路を
**コードの一次情報から確定**し、API 化候補を分類する。実装はしない。

### 読んだファイル
- [src/AutonaWebServer.py](../../src/AutonaWebServer.py)（HTTP/WS ルート全量・ポート・バインド・CORS・delta coalescing）
- [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py)（全 Action の実処理・状態経路）
- [src/AutonaHotkeys.py](../../src/AutonaHotkeys.py)（9 ホットキーと接続先）
- [src/AutonaScoreboardWidget.py](../../src/AutonaScoreboardWidget.py)（`PostToX`/`PostToBsky`/`GenerateThumbnail`・ホットキー接続）
- [src/Autona.py](../../src/Autona.py)（シグナル配線・`UiMounted` 起動順）（抜粋 grep）
- 既存 docs（README / current-architecture / current-features / 旧 changes・ADR・task）

### 調査した既存機能
スコア操作 / チームカラー / クルー戦ストック / 入替 / 各種リセット・全消去 / セット&大会ロード /
ブラケット更新 / 統計再取得 / マスタ取得（ゲーム・キャラ・バリアント・コントローラ）/ サムネ生成 /
テーマ切替 / 実況者更新 / 選手 DB / ホットキー / X・Bluesky 投稿。

### 判明したこと（Confirmed）
- HTTP 約 45 ルート＋対応 Socket.IO（**二重定義**）。全量・分類は
  [../architecture/external-control-api-candidates.md](../architecture/external-control-api-candidates.md) §3。
- 全ルートは `WebServerActions` に集約 → Qt Signal → widget → **StateManager 単一経路**（直接書き込みなし）。
- 既定ポート `5000`、Web UI ルート `/`・`/scoreboard`・`/team`・`/web`。
- 制御面は **`0.0.0.0` + 認証なし + CORS 全許可**（LAN 内から全操作可能）。
- **X / Bluesky 投稿は UI ボタン専用**（API/ホットキー未露出）。
- ホットキー 9 種は Web ルートと操作が重複（= 最頻操作の指標）。

### 不明なこと（Unknown / TBD）
- `set_team_data` の payload スキーマ（必須キー）。
- scoreboardNumber の有効値・board2/3 の現行扱い。
- 重い操作の多重実行ガードの有無。
- 配信中/非配信中の判定手段。
- `assets/rulesets.json`（ステージストライク残置）の用途。

### PdM 判断（2026-06-15・解消済 → ADR-001 D1〜D8）
1. 認証/バインド: **現状維持**（`0.0.0.0`+無認証+CORS 全許可、LAN 内前提）= D5
2. 初回スコープ: **操作系も広く一括**（読み取り＋安全＋危険操作）= D6
3. 危険操作: **confirm 必須で初回から含める** ＋ 重い操作は実行中フラグ 409 = D6/D7
4. API 境界: **`/api/v1/` 薄いラッパー（既存 `WebServerActions` 再利用、案 B'）** = D3
5. API の性格: **汎用 External Control API**（Companion/Stream Deck は薄いクライアント）= D1/D2
6. 操作ログ: **書き込み操作のみ INFO・専用ロガー**（A-1）/ idempotency: **Idempotency-Key 任意対応**（B-1）= D7
7. X / Bluesky: 初回除外（将来 Revisit）

### 設計ドキュメント（作成済・2026-06-15）
- ✅ `/api/v1/` の **I/F 仕様**（endpoint・payload・response・error コード）を正本として作成:
  [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md)（計画/未実装・ADR-001 準拠）。

### 次に作成すべき実装タスク（ADR 確定済・ベースは candidates §8）
- `tasks/implement-external-control-api-readonly.md`:
  `GET /api/v1/state` + `/health` + 既存 WS 差分購読の踏襲（読み取り層）。
- `tasks/implement-external-control-api-safe-ops.md`:
  スコア±1 / swap / score:reset / set 情報設定（安全操作・既存 Actions の薄いラッパー、構造化レスポンス D8）。
- `tasks/implement-external-control-api-guarded-ops.md`:
  危険操作（clear-all/reset-players/reset-match=confirm 必須、set-tournament/update-game/update-bracket=実行中フラグ 409、
  load-set 系、set-light-mode）。**書き込み操作ログ（INFO）と Idempotency-Key 任意対応**を本チケットで実装（D7）。

### 実装フェーズで確定する残課題
- 重い操作の多重実行ガードが**既存コードに在るか**（無ければ新 API 層で実装）。
- `set_team_data` の payload スキーマ（必須キー）。
- Companion / Stream Deck が求めるプロトコル（HTTP / WS）の最終確認。
