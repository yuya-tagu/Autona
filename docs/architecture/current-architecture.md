# Current Architecture

外部制御 API / Companion / Stream Deck / OBS 連携拡張を検討するための
アーキテクチャ要約。網羅的な事実情報は
[../../.claude/architecture/](../../.claude/architecture/)、実装仕様の正本は
[../../.claude/design/Design.md](../../.claude/design/Design.md) を参照。

> ステータス凡例: ✅ コード/CLAUDE.md 確認済 / 📄 既存ドキュメント由来 / ⚠️ 要確認 / `TBD`

## Overview

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

- デスクトップ: Python 3.12 / PyQt6 / qasync ✅
- Web UI: React 19 / TypeScript / Redux Toolkit / MUI 7 / Socket.IO ✅
- 配信オーバーレイ: HTML/CSS/JS（gsap, jQuery）, `layout/` サブモジュール 📄

## Application layers

| レイヤー | 主体 | 役割 |
|---------|------|------|
| エントリ | [main.py](../../main.py) | QEventLoop + asyncio 起動、`sys.dont_write_bytecode=True` ✅ |
| ウィンドウ/UI | [src/Autona.py](../../src/Autona.py) | QMainWindow、メニュー、全ドック管理、レイアウト復元 ✅ |
| 状態 | [src/StateManager.py](../../src/StateManager.py) | nested dict 状態、deepdiff デルタ、ファイル出力 + WS 配信 ✅ |
| 設定 | [src/SettingsManager.py](../../src/SettingsManager.py) | ユーザー設定 JSON 永続化 ✅ |
| Web/配信 | [src/AutonaWebServer.py](../../src/AutonaWebServer.py) + `AutonaWebServerActions.py` | Flask + SocketIO、HTTP/WS ルート ✅ |
| データ取得 | [src/AutonaTournamentDataProvider.py](../../src/AutonaTournamentDataProvider.py) | Start.gg 等のオーケストレーション ✅ |
| アセット | [src/AutonaGameAssetManager.py](../../src/AutonaGameAssetManager.py) | ゲーム別キャラ/ステージ/アイコン ✅ |
| 入力 | [src/AutonaHotkeys.py](../../src/AutonaHotkeys.py) | pynput グローバルホットキー ✅ |

## Frontend modules

React Web UI（[web/src/](../../web/src/)）📄/⚠️:

- ページ: ScoreboardPage。ルートは CLAUDE.md では `/web`（既定）`/scoreboard`（board1）`/team`（board2）⚠️ 要再確認
- Redux Slices: `tshState` / `tshPlayers` / `tshCharacters` / `tshGames` / `tshCountries` / `websocketInfo` / `selectedScoreboard` / `visibility` ✅（CLAUDE.md）
- 通信: [web/src/websocketConnection.ts](../../web/src/websocketConnection.ts)（Socket.IO）+ デルタパッチ（Immer, 1000ms 間隔）✅
- ※ フォルダ名 `web` / `tsh*` は上流（TournamentStreamHelper）由来の歴史的経緯。リネーム保留 ✅
- ※ ステージストライク関連（`StageStrikePage` 等）は削除済み・存在しない ✅

## Backend modules

Flask + Socket.IO サーバー（[src/AutonaWebServer.py](../../src/AutonaWebServer.py)、QThread）✅:

- HTTP ルートと Socket.IO イベントが**対で**定義される（同じ操作を両方で提供）✅
- 実処理は [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py) に委譲 ✅
- 既存ルート群（抜粋・確認済）:
  - 状態: `GET /program-state`、`socketio 'program_state' / 'program-state-update'`
  - スコア: `/score`(POST)、`/scoreboard<N>-team<T>-scoreup|scoredown|color-<c>`
  - クルー: `/scoreboard<N>-crew-member<T>-<M>-stockup|stockdown`、`-crew-active<T>-<M>`
  - セット: `/scoreboard<N>-get|set|open-set|pull-stream`、`/get-sets`、`/get-match-<id>`
  - 操作: `swap-teams`、`reset-scores|match|players`、`clear-all`
  - 統計: `stats-recent-sets|upset-factor|last-sets-<p>|history-sets-<p>`
  - マスタ: `/games`、`/current-game`、`/match-names`、`/characters`、`/variants`、`/controllers`
  - サムネ: `/scoreboard<N>-get-thumbnail-<fmt>`
- ⚠️ **完全なルート一覧は未棚卸し**。外部制御 API 設計前に全列挙が必要（→ tasks 参照）

> **重要**: 「外部制御 API」を新設する前に、この**既存 HTTP/WS 制御面が既に存在する**
> ことを前提にする。新 API はこれと重複・競合・二重管理になりうる。

## PyQt / desktop integration

- ドックウィジェットベース。QDockWidget は必須設定あり（`.claude` の hook がチェック）✅
- シングルトン `*.instance` パターン（ScoreboardManager / GameAssetManager / TournamentDataProvider / Hotkeys / AlertNotification）✅
- スレッド安全: StateManager は `threading.RLock`、PlayerDB は `multiprocessing.Lock` ✅
- Qt Signal/Slot で各コンポーネント連携。Worker(QRunnable) でバックグラウンド処理 ✅

## OBS integration

- **OBS-websocket は使わない方針**（ROADMAP で検討外と明記）📄
- 連携は (1) ブラウザソースで `layout/` の HTML を表示、(2) `out/` のテキスト/JSON をソース参照 📄
- 将来「シーン切替」を入れる場合も OBS 側の汎用 HTTP→scene プラグイン経由を想定（ROADMAP Tier5）📄

## State management

- StateManager が**唯一の状態の中枢**。すべての配信出力・Web UI はここから派生 ✅
- 変更フロー: `Set(keypath, value)` → deepdiff でデルタ → (a) `out/` ファイル出力、(b) WebSocket デルタ配信 ✅
- キーパスはドット区切り文字列、数値も文字列キー（`score.1.team.1.player.1.name`）✅
- 一括更新は `with StateManager.SaveBlock():` で書き込み抑制（BlockSaving/ReleaseSaving の対称性必須）✅
- 状態キーパス概観は [../../CLAUDE.md](../../CLAUDE.md) の「状態キーパス」節 / [.claude/architecture/phase-a-b/state-keys.md](../../.claude/architecture/phase-a-b/state-keys.md) ✅

## Data flow

1. Start.gg 等から取得（TournamentDataProvider）→ StateManager に書き込み
2. StateManager がデルタ算出 → `out/` 出力 + Socket.IO で全クライアント配信
3. Web UI: `program_state` で全状態受信 → Redux `overwrite()` → 以降 `program_state_update` デルタ
4. ユーザー操作（Web UI / 外部 HTTP クライアント）→ REST POST/WS → Actions → StateManager 変更 → 2 に戻る ✅

## External dependencies

- **Start.gg API**（GraphQL、20 秒タイムアウト、指数バックオフ 5 回）✅
- **X / BlueSky**（投稿、BlueSky は App Password 認証）📄
- **フォント**: LINE Seed JP（gstatic CDN 取得）✅
- **GitHub Releases**（自動アップデート、PyInstaller 版のみ）📄
- ⚠️ Companion / Stream Deck は**現状連携なし**（今回の拡張対象）

## Known architectural constraints

- **シングルトン初期化順序が固定**: `AutonaGameAssetManager` の import は
  `AutonaTournamentDataProvider` より前でなければならない ✅（agent-pitfalls #2）
- **`src` 経由 import は全起動を誘発**（QApplication + 全シングルトン生成）✅
- HTTP ルートと WS イベントが**二重定義**（片方だけ直すと不整合）✅ — 制御面拡張で要注意
- `objectName()` = StateManager キーの箇所があり、UI 変更が保存互換を壊しうる ✅
- Qt シグナルハンドラ内で `processEvents()` / モーダル / 連続 `QGraphicsScene.clear()` 禁止 ✅（agent-pitfalls #9,#10）
- Windows / PyInstaller 前提（ホットキー、自動更新、exe サイズ）✅

## Areas that require caution before modification

外部制御 API / 連携追加時に**先に影響調査が必要**な領域:

1. **AutonaWebServer / AutonaWebServerActions** — 既存制御面。新 API はここに同居 or 競合する
2. **StateManager** — 状態変更の単一経路。外部からの書き込みは必ずここを通す（バイパス禁止）
3. **シングルトン初期化順** — 新サービス（外部制御リスナ等）の起動位置を誤ると初期化破綻
4. **スレッド境界** — WebServer は QThread。外部 API も別スレッドなら StateManager のロックを尊重
5. **ポート/ルーティング** — 既定ポート・既存ルートと衝突しない設計（→ 値の正確な確認が前提）

> 具体的な影響整理は [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)、
> 設計境界の決定は [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) を参照。
