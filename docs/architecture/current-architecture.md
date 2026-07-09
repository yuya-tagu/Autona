# Current Architecture

外部制御 API / Companion / Stream Deck / OBS 連携拡張を**安全に足すための変更影響マップ**。
アーキの「事実」（レイヤー構成・モジュール責務・データフロー・状態キーパス等）は
**ここでは再記述せず正本へリンクする**。本書は「変更時に触る／避ける構造」と
「既存の制御面」「実装済みの外部制御 API」「残課題」に絞る。

事実情報の参照先（正本・調査ログ）:

| 知りたいこと | 参照先 |
|-------------|--------|
| レイヤー構成・主要モジュールと責務・データフロー・状態キーパス概観 | [../../CLAUDE.md](../../CLAUDE.md)「アーキテクチャ概要」「状態キーパス」 |
| 実装仕様の正本（UI / データモデル / 画面 / テーマ / ホットキー） | [../../.claude/design/Design.md](../../.claude/design/Design.md) |
| import 関係・状態キー Read/Write・シングルトン初期化順の網羅事実 | [../../.claude/architecture/](../../.claude/architecture/)（2026-04 時点の凍結調査ログ） |
| エージェントが踏みやすい制約の一次情報 | [../../.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) |

> ステータス凡例: ✅ コード/CLAUDE.md 確認済 / 📄 既存ドキュメント由来 / ⚠️ 要確認 / `TBD`

## 状態に触れる経路（外部連携の接続点）

```
┌──────────────────────────────────────────────┐
│            main.py (QEventLoop + asyncio)      │
└───────────────────────┬────────────────────────┘
                        │
┌───────────────────────▼────────────────────────┐
│        Window (QMainWindow / Autona.py)         │
│        ドックウィジェット群のオーケストレーション   │
└──┬──────────┬───────────┬───────────┬──────────┘
   │          │           │           │
   ▼          ▼           ▼           ▼
StateManager TournamentData GameAsset  WebServer
(状態の中枢)  Provider       Manager   (Flask+SocketIO)
   │        (Start.gg)                  │
   ▼                                    ▼
out/ files (OBS用)            Web UI (React) / 外部HTTPクライアント
```

外部制御 API / Companion を足すとき関わる「状態に触れる経路」は次の3つ。
**新しい制御は必ずこのいずれかに同居 or 競合する**前提で設計する。

| 経路 | 入口 | 設計上の含意 |
|------|------|-------------|
| HTTP / WebSocket | [AutonaWebServer.py](../../src/AutonaWebServer.py)（QThread, Flask+SocketIO） | 既存の制御面（下記）。新 API はここに同居させるのが自然 |
| 状態書き込み | [StateManager.py](../../src/StateManager.py) `Set(keypath, value)` | **状態変更の単一経路。外部からの書き込みも必ずここを通す（バイパス禁止）** |
| 配信出力 | `out/` ファイル群 + WS デルタ配信 | OBS ブラウザソース／既存クライアントが直接依存。出力契約を壊さない |

## 既存 HTTP / WebSocket 制御面（重要）

> **新「外部制御 API」を設計する前に、既存の HTTP/WS 制御面が既に存在することを前提にする。**
> 新 API はこれと重複・競合・二重管理になりうる。

- HTTP ルートと Socket.IO イベントが**対で**定義される（同じ操作を両方で提供）✅
- 実処理は [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py) に委譲 ✅
- ⚠️ **HTTP ルートと WS イベントが二重定義**のため、片方だけ直すと不整合になる — 制御面拡張で要注意 ✅
- 外部制御 API は **`/api/v1/` として実装済み**。既存 Flask+SocketIO サーバーに Blueprint を登録し、
  既存ルートを壊さず `WebServerActions` を再利用する ✅
  - 実装: [src/AutonaApiV1.py](../../src/AutonaApiV1.py)
  - 登録: [src/AutonaWebServer.py](../../src/AutonaWebServer.py)
- 既存ルート群（抜粋・確認済）:
  - 状態: `GET /program-state`、`socketio 'program_state' / 'program-state-update'`
  - スコア: `/score`(POST)、`/scoreboard<N>-team<T>-scoreup|scoredown|color-<c>`
  - クルー: `/scoreboard<N>-crew-member<T>-<M>-stockup|stockdown`、`-crew-active<T>-<M>`
  - セット: `/scoreboard<N>-get|set|open-set|pull-stream`、`/get-sets`、`/get-match-<id>`
  - 操作: `swap-teams`、`reset-scores|match|players`、`clear-all`
  - 統計: `stats-recent-sets|upset-factor|last-sets-<p>|history-sets-<p>`
  - マスタ: `/games`、`/current-game`、`/match-names`、`/characters`、`/variants`
  - サムネ: `/scoreboard<N>-get-thumbnail-<fmt>`

## 確定済み事項 / 残課題

| 項目 | 現状 | 確認方法 |
|------|------|---------|
| 完全な HTTP/WS ルート一覧 | HTTP 約 45 + 対応 Socket.IO を棚卸し済み ✅ | [external-control-api-candidates.md](external-control-api-candidates.md) §3 |
| 外部制御 API | `/api/v1/` 実装済み ✅ | [../../src/AutonaApiV1.py](../../src/AutonaApiV1.py) |
| 既定 Web サーバーポート | `SettingsManager.Get("general.webserver_port", 5000)` = **5000**。設定で変更可 ✅ | [../../src/AutonaWebServer.py](../../src/AutonaWebServer.py) |
| Web UI ルート | `/`・`/web`・`/scoreboard`（board1）・`/team`（board2）✅ | [../../src/AutonaWebServer.py](../../src/AutonaWebServer.py) |
| 残課題 | LAN 外運用時の認証/認可、配信中判定による危険操作ガード、`set_team_data` の API 化要否 | ADR-001 Revisit 条件 / API spec Open items |

## OBS 連携の方針（変更時の制約）

- **OBS-websocket は使わない方針**（ROADMAP で検討外と明記）📄
- 連携は (1) ブラウザソースで `layout/`（Git サブモジュール）の HTML を表示、(2) `out/` のテキスト/JSON をソース参照 📄
- 将来「シーン切替」を入れる場合も OBS 側の汎用 HTTP→scene プラグイン経由を想定（ROADMAP Tier5）📄
- ⚠️ **2026-06-15: この方針は見直し検討中**。OBS Studio（obs-websocket v5）+ Advanced Scene Switcher を
  操作 API として採用する調査を [obs-scene-switcher-control.md](obs-scene-switcher-control.md) に整理した。
  採否・境界は ADR で確定する（同書 §6）。確定までは上記が現行方針。

## 外部依存（連携追加で関わるもの）

- **Start.gg API**（GraphQL、20 秒タイムアウト + 指数バックオフ）✅ — 重いクエリ追加で timeout しやすい（詳細は agent-pitfalls #11）
- **X / BlueSky**（投稿、BlueSky は App Password 認証）📄
- **GitHub Releases**（自動アップデート、PyInstaller 版のみ）📄
- ⚠️ **Companion / Stream Deck は現状連携なし**（今回の拡張対象）

## 改変前に影響調査が必要な領域

外部制御 API / 連携追加時に**先に影響調査が必要**な領域:

1. **AutonaWebServer / AutonaWebServerActions** — 既存制御面。新 API はここに同居 or 競合する
2. **StateManager** — 状態変更の単一経路。外部からの書き込みは必ずここを通す（バイパス禁止）
3. **シングルトン初期化順** — 新サービス（外部制御リスナ等）の起動位置を誤ると初期化破綻（制約は agent-pitfalls #2）
4. **スレッド境界** — WebServer は QThread。外部 API も別スレッドなら StateManager のロックを尊重
5. **ポート/ルーティング** — `/api/v1/` と既存ルートの衝突回避、既定ポート `5000` の運用影響

> 具体的な影響整理は [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)、
> 設計境界の決定は [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) を参照。
