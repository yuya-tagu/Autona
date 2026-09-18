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
| import 関係・状態キー Read/Write の網羅事実 | [../../.claude/architecture/](../../.claude/architecture/)（2026-04 時点の凍結調査ログ） |
| シングルトン生成順の**正本** | [../../src/bootstrap.py](../../src/bootstrap.py) の `bootstrap_core()`（`ORDER CONSTRAINT:` コメントに理由を併記） |
| 起動パイプラインの設計・移行記録 | [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) / [../decisions/ADR-003-startup-architecture.md](../decisions/ADR-003-startup-architecture.md) |
| エージェントが踏みやすい制約の一次情報 | [../../.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) |

> ステータス凡例: ✅ コード/CLAUDE.md 確認済 / 📄 既存ドキュメント由来 / ⚠️ 要確認 / `TBD`

## 状態に触れる経路（外部連携の接続点）

```
┌──────────────────────────────────────────────┐
│  main.py（単一インスタンスゲート → splash →     │
│  import src → QEventLoop + asyncio）           │
└───────────────────────┬────────────────────────┘
                        │ bootstrap_core()（生成順の正本）
┌───────────────────────▼────────────────────────┐
│        Window (QMainWindow / Autona.py)         │
│   サイドバー + QStackedWidget のパネル群を管理   │
│   （大半は初回表示時に遅延構築）                  │
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

## 起動シーケンスと構築タイミング（外部制御を足すときの前提）

起動アーキテクチャ再設計（Step 1〜5・2026-07 完了）により、**「起動時に全 UI が存在する」前提は
成立しない**。外部制御 API / 連携を足すときは以下を前提に設計する ✅

| 段 | 何が起きるか | 変更時の含意 |
|----|------------|------------|
| ゲート | `main.py` の named mutex（`taguyan.Autona.SingleInstance`）で単一インスタンス化。2 個目は QLocalSocket で前面化要求して終了 | ポート・`out/`・ホットキーの多重占有は起こらない前提でよい |
| 早期表示 | QApplication + スプラッシュを `import src` より前に生成 | `src` を import する新モジュールは副作用ゼロを守る（`bootstrap.py` 参照） |
| ブートストラップ | `from src import Autona` → `bootstrap_core()` がシングルトンを明示順で生成 | 新サービスの生成は `bootstrap_core()` に追加する（モジュール末尾で作らない） |
| 最小コア表示 | `Window.__init__` は Tournament Info + アクティブ `battle_format` の board 1 面のみ構築して `show()` | **他パネル・他 board は未構築のまま起動する** |
| 表示後 | オーバーレイ消滅後に `Window._StartWebServer()` が Flask/SocketIO を構築・起動（`webserver-ready` フェーズ） | OBS ブラウザソースの取得開始は表示から数秒遅れる（ADR-003 で許容済み） |

未構築のまま外部から呼ばれる経路には、既に構築を強制する入口がある。**新しい制御を足すときは
同じ経路を使う**（パネル/board を直接参照して `None` を掘らない）:

- パネル: `WebServerActions._ensure_panel` → `Window.EnsureTop8Panel()` / `EnsureCommentaryPanel()`
- board: `AutonaScoreboardManager.GetScoreboard(N)`（GUI スレッドへ marshal して構築）
- state: パネル未構築でも読める初期値は `Window._SeedPanelState()`（theme/accent・`top8` 正規化・
  `commentary` 正規化・`stream.org*Version`）と `CreateScoreboard()`（`score.{N}` スケルトン）が書く

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
| 既定 Web サーバーポート | `SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)` = **5107**（2026-09-19 に 5000 から変更。ADR-001 Addendum 参照）。設定で変更可 ✅ | [../../src/AutonaWebServer.py](../../src/AutonaWebServer.py) |
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
3. **シングルトン生成順** — 新サービス（外部制御リスナ等）は [src/bootstrap.py](../../src/bootstrap.py) の `bootstrap_core()` に追加する。順序制約の理由は同ファイルの `ORDER CONSTRAINT:` コメント（agent-pitfalls #2）
4. **スレッド境界** — WebServer は QThread。外部 API も別スレッドなら StateManager のロックを尊重
5. **ポート/ルーティング** — `/api/v1/` と既存ルートの衝突回避、既定ポート `5107` の運用影響

> 具体的な影響整理は [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)、
> 設計境界の決定は [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) を参照。
