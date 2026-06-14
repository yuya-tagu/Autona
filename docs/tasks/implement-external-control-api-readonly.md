# 実装チケット: External Control API — 読み取り層 + 共通基盤

> **チケット1/3。** External Control API（`/api/v1/`）の**共通基盤＋読み取りエンドポイント**を実装する。
> 後続の [safe-ops](implement-external-control-api-safe-ops.md) / [guarded-ops](implement-external-control-api-guarded-ops.md)
> はこのチケットで作る基盤（エンベロープ・エラー処理・n 検証・操作ログ）に乗る。**まずこれを完了させる。**

- 正本仕様: [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md)（§2 共通規約・§5 読み取り・§14 スパイク結果）
- 決定: [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)（D3/D4/D8）
- 既存実装: [../../src/AutonaWebServer.py](../../src/AutonaWebServer.py) / [../../src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py)

## ゴール

- `/api/v1/` 名前空間を**既存 Flask+SocketIO サーバーに薄く追加**し、内部は**既存 `WebServerActions` を再利用**する（ADR D3）。
- 構造化レスポンス・エラーコード・scoreboardNumber 検証・操作ログの**共通ヘルパー**を用意する。
- 読み取りエンドポイント（§5）を実装する。

## 前提・制約（厳守）

- **既存ルート（`/score`・`/scoreboard<N>-...`・Socket.IO イベント）を一切変更しない**（regression 禁止）。
- 状態変更は本チケットには無い（読み取りのみ）。ただし基盤は後続が使うため §2 規約に準拠させる。
- 認証なし・`0.0.0.0`・CORS 全許可は**現状維持**（ADR D5）。新たな認証は入れない。
- 既存ポート（`general.webserver_port`、既定 5000）を共有。新ポート・新プロセスを作らない。
- コード規約は [../../.claude/rules/state-and-webserver.md](../../.claude/rules/state-and-webserver.md) に従う。
- 動作確認は**手動のみ**（自動テストは作らない・ADR/spec §10）。`python main.py` で確認。

## 実装方針（推奨）

- **新モジュール `src/AutonaApiV1.py`** に Flask Blueprint `api_v1`（`url_prefix="/api/v1"`）を定義し、
  `WebServer.__init__`（[AutonaWebServer.py:59](../../src/AutonaWebServer.py) 付近）で `WebServer.app.register_blueprint(api_v1)` する。
  - 理由: `/api/v1` を 1 ファイルに隔離でき、既存のクラスメソッド・ルート定義に触れない。
  - Blueprint のハンドラからは `WebServer.actions`（= `WebServerActions`）を呼ぶ。状態ロジックを新規に書かない（D4）。
  - 別案: 既存 `WebServer` クラスにメソッドを足す（既存スタイル準拠だがファイルが膨らむ）。どちらでも可。
- Blueprint 登録は `socketio.run` の前（サーバー起動前）に行う。

### 共通ヘルパー（本チケットで作る・後続が使う）

`src/AutonaApiV1.py` に以下を実装:

1. **成功エンベロープ** `ok(data=None, revision=None, status=200)` → `{"ok": true, "data": ..., "revision": ...}`
   （`data`/`revision` は None なら省略可）。spec §2.4。
2. **エラーエンベロープ** `err(code, message, status)` → `{"ok": false, "error": {"code", "message"}}` + HTTP status。
   エラーコード定数は spec §2.6（`INVALID_ARGUMENT`/`NOT_FOUND`/`CONFIRMATION_REQUIRED`/`CONFLICT`/`NOT_READY`/`UPSTREAM_TIMEOUT`/`INTERNAL`）。
3. **例外ラッパ** デコレータ `api_handler`: ハンドラ全体を try/except し、未処理例外を `INTERNAL`(500) に変換しログ出力。
4. **scoreboardNumber 検証** `validate_scoreboard(n) -> int`: `{1,2,3}` 以外は `INVALID_ARGUMENT`(400) を投げる
   （spec §2.12・§14.2 の「最頻の失敗 = GetScoreboard(n)=None 後の AttributeError」を予防）。
5. **revision 取得** `current_revision()` → `StateManager.revision`（spec §14.1）。読み取り応答は `GetFullState()` の revision を使う。
6. **操作ログ用ロガー**（後続チケットが使う・本チケットでは定義のみ）: 書き込み操作を INFO で記録する専用ロガー
   （時刻・`request.remote_addr`・メソッド・パス・主要パラメータ・結果）。spec §2.7。読み取りはログしない。

## エンドポイント（読み取り・spec §5）

| メソッド・パス | 委譲 | 備考 |
|---------------|------|------|
| `GET /api/v1/state` | `actions.program_state()` | `GetFullState()` の `revision` を含めて返す |
| `GET /api/v1/health` | 新規（§5.1・下記） | — |
| `GET /api/v1/scoreboards/<int:n>` | `actions.get_scoreboard(n)`（+ `get_swap`） | n 検証必須 |
| `GET /api/v1/games` | `actions.get_games()` | |
| `GET /api/v1/games/current` | `actions.get_current_game()` | |
| `GET /api/v1/match-names` | `actions.get_match_names()` | |
| `GET /api/v1/characters` | `actions.get_characters()` | |
| `GET /api/v1/variants` | `actions.get_variants()` | |
| `GET /api/v1/controllers` | `actions.get_controllers()` | |
| `GET /api/v1/sets` | `actions.get_sets(request.args)` | **同期 Start.gg 呼び**→ try/except で `UPSTREAM_TIMEOUT`（§14.2） |
| `GET /api/v1/sets/<setId>` | `actions.get_match(setId)` | **provider null 未チェック**→`NOT_READY`、`int()` 失敗→`INVALID_ARGUMENT`、同期 Start.gg→`UPSTREAM_TIMEOUT`（§14.2） |
| `GET /api/v1/commentary` | `actions.get_comms()` | |

### `GET /api/v1/health`（新規・spec §5.1）

返却 `data`:
```json
{ "status": "ok", "version": "<app version>", "revision": 1234,
  "currentGame": "<codename>", "activeBoard": 1,
  "tournamentLoaded": true, "outLastWriteUnix": 1718000000 }
```
- `revision`: `StateManager.revision`。
- `currentGame`: `StateManager.Get("game")` 由来（codename 抽出）。
- `activeBoard`: `SettingsManager.Get("scoreboard.battle_format", "singles")` を `{"singles":1,"team":2,"crew":3}` で写像（spec §14.1）。
- `tournamentLoaded`: `AutonaTournamentDataProvider.instance.GetProvider() is not None`。
- `outLastWriteUnix`: `os.path.getmtime("./out/program_state.json")`（ファイル無は握り潰して `null`）。

## エラーマップ（読み取り・spec §14.2）

- 無効 `n` → `INVALID_ARGUMENT`(400)（`get_scoreboard`/将来の操作系で `GetScoreboard()`=None を防ぐ）。
- `get_match`: provider 不在 → `NOT_READY`(409)、`int(setId)` 失敗 → `INVALID_ARGUMENT`(400)。
- `get_sets`/`get_match` の Start.gg 例外・タイムアウト → `UPSTREAM_TIMEOUT`(504)。
- それ以外の未処理例外 → `INTERNAL`(500)。

## 触るファイル

- 新規: `src/AutonaApiV1.py`（Blueprint + ヘルパー）
- 変更: `src/AutonaWebServer.py`（Blueprint 登録の 1〜2 行のみ。既存ルートは触らない）
- 参照のみ: `AutonaWebServerActions.py` / `StateManager.py` / `SettingsManager.py` / `AutonaScoreboardManager.py` / `AutonaTournamentDataProvider.py`

## 受け入れ条件（手動確認・[../../.claude/manual-test-checklist.md](../../.claude/manual-test-checklist.md)「外部制御 API」節）

- [ ] 既存ルート・Web UI・OBS・Socket.IO が従来通り（regression なし）。
- [ ] `GET /api/v1/state` がフル状態 + `revision` を `{"ok":true,...}` で返す。
- [ ] `GET /api/v1/health` が `activeBoard`/`currentGame`/`revision`/`tournamentLoaded` を返す。
- [ ] `GET /api/v1/scoreboards/1` が board1 状態を返し、`GET /api/v1/scoreboards/4` が 400 `INVALID_ARGUMENT`。
- [ ] 大会未ロード時 `GET /api/v1/sets/123` が `NOT_READY`、`GET /api/v1/sets/abc` が `INVALID_ARGUMENT`。
- [ ] 別 PC（LAN 内）から `/api/v1/state` に到達できる。

## スコープ外

- 書き込み操作（safe-ops / guarded-ops チケット）。
- 認証・新ポート・WS 新チャネル（既存 WS を購読・spec §4）。
- 選手 DB 全量取得（個人情報・初回除外）。

## 依存

- なし（最初に着手するチケット）。後続2チケットは本チケットの基盤に依存。
</content>
