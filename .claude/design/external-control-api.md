# External Control API (`/api/v1/`) I/F 仕様

> **ステータス: 実装済み。** 本書は ADR-001 で確定した方針に基づく I/F 仕様であり、
> 実装（[../../src/AutonaApiV1.py](../../src/AutonaApiV1.py)）の説明文書として扱う。実装と本書が乖離した場合は**実装を正**とし、本書を実装に追従させる。
> 既存の HTTP/WS 制御面の事実は変更しない（本 API は別名前空間として**追加**済み）。
> アプリ内導線: メニューバー「API設定」→ リファレンス・テスター（[../../src/AutonaApiInfoWindow.py](../../src/AutonaApiInfoWindow.py)）。

- 決定根拠: [../../docs/decisions/ADR-001-external-control-api-boundary.md](../../docs/decisions/ADR-001-external-control-api-boundary.md)（D1〜D8）
- 操作棚卸し・分類: [../../docs/architecture/external-control-api-candidates.md](../../docs/architecture/external-control-api-candidates.md)
- 同期/リビジョン規約の正本: [Design.md §3.2 同期経路の規約](Design.md)
- API 実装本体: [../../src/AutonaApiV1.py](../../src/AutonaApiV1.py)
- 既存ルート実装: [../../src/AutonaWebServer.py](../../src/AutonaWebServer.py) / [../../src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py)

> 凡例: **MUST**/**SHOULD**/**MAY**（RFC2119 相当）。`TBD` は実装フェーズで確定。

---

## 1. 目的・スコープ

- Autona の操作を外部コントローラ（Companion / Stream Deck / その他）から**安全に**呼び出すための汎用 API（ADR-001 D1）。
- 外部コントローラは**薄いクライアント**。状態ロジックを持たず本 API を叩くだけ（D2）。
- 本 API は既存 Flask+SocketIO サーバーに **`/api/v1/` 名前空間として薄く追加**し、内部は
  **既存 `WebServerActions` を再利用**する（D3）。新サーバー/プロセスは作らない。
- 状態変更は**必ず `WebServerActions` → Qt Signal → `StateManager` の単一経路**を通す（D4・不変条件）。
  StateManager をバイパスしない。GUI スレッド直列化（`@gui_thread_sync`）を尊重する。

### 1.1 初回スコープ（D6）
- 読み取り（§5）/ 安全操作（§6）/ 危険操作（§7・ガード必須）を**初回から一括提供**。
- 初回除外: `open-set`（GUI ウィンドウ起動）/ X・Bluesky 投稿（外部送信・機密）/ 選手 DB 全量（個人情報）/ crew 系（board3 運用未確定）。

---

## 2. 共通規約

### 2.1 ベース・バージョニング
- ベースパス: **`/api/v1`**。破壊的変更時は `/api/v2` を新設し、`/api/v1` は一定期間維持。
- 既存ルート（`/score`・`/scoreboard<N>-...` 等）とは**パス空間が衝突しない**（`/api/` 前缀）。既存契約は不変（D3）。

### 2.2 認証・ネットワーク（D5）
- **認証なし**。LAN 内運用を信頼境界とする前提を引き継ぐ。
- バインドは既存通り `0.0.0.0`、CORS は既存通り全許可。
- ⚠️ **無認証で破壊的操作まで叩ける**。緩和は confirm（§7）と操作ログ（§2.7）のみ。LAN 外運用が発生したら ADR-001 を Revisit。

### 2.3 Content-Type
- リクエストボディは `application/json`（**MUST**）。
- レスポンスは `application/json`（**MUST**。サムネ等のバイナリ返却は初回スコープ外）。

### 2.4 成功レスポンス・エンベロープ
すべての `/api/v1` 応答は次の形（**MUST**）:

```json
{ "ok": true, "data": { /* 任意・読み取りで使用 */ }, "revision": 1234 }
```

- `revision`: 操作後の状態リビジョン。**操作系（書き込み）レスポンスに含める（MUST）**。
  クライアントは WS デルタ（`program_state_update` の `revision`/`delta_index`）と突合し、反映済みかを判定できる。
  → リビジョンの定義・採番は [Design.md §3.2](Design.md) に従う（既存 WS と同一ソース。実装時の取得経路は `TBD`）。
  - ⚠️ **`revision` は `StateManager` の変更のみを追う**。`SettingsManager` のみに書く操作
    （`POST /tournament` の URL 永続化など。§2.4a）の設定値変更は `revision` に
    反映されない場合がある。クライアントは設定系の反映確認を `revision` だけに依存しない（**SHOULD**）。
- `data`: 読み取り系の本体。操作系では省略可。

### 2.4a 状態の二重書き込み（SettingsManager 併用・許容）
- D4 の不変条件「状態変更は `StateManager` 単一経路」は **`score.*` 等のランタイム状態**に適用される。
- 一部の Action は **`SettingsManager` にも書く**ことを**許容する**（PdM 確認済）:
  - `load_tournament` → `SettingsManager.Set("TOURNAMENT_URL")` ＋ シグナル emit（[:447](../../src/AutonaWebServerActions.py)）
- 本 API はこれらも**既存 Action へ委譲するだけ**（新たに Settings/State を直接書かない）。
  「StateManager をバイパスして state を直接書かない」原則は維持される。

### 2.5 エラーレスポンス・エンベロープ
失敗時（**MUST**）:

```json
{ "ok": false, "error": { "code": "INVALID_ARGUMENT", "message": "team must be 1 or 2" } }
```

- HTTP ステータスと `error.code` を**両方**返す。`message` は人間可読（i18n しない・ログ/デバッグ用）。
- 既存ルートの素朴な `"OK"`/文字列契約は**変えない**（新 API のみ構造化）。

### 2.6 エラーコード一覧

| code | HTTP | 意味・用途 |
|------|------|-----------|
| `INVALID_ARGUMENT` | 400 | パラメータ不正（範囲外の team/scoreboard、型不一致 等） |
| `NOT_FOUND` | 404 | 存在しないリソース（未知の set ID 等） |
| `CONFIRMATION_REQUIRED` | 409 | 破壊的操作で `confirm:true` が無い（§7.2） |
| `CONFLICT` | 409 | 重い操作が実行中（多重起動ガード・§2.8） |
| `NOT_READY` | 409 | 前提未充足（大会/試合未ロードで provider 不在 等・§3） |
| `UPSTREAM_TIMEOUT` | 504 | Start.gg 等の外部依存がタイムアウト（agent-pitfalls #11） |
| `INTERNAL` | 500 | 想定外の内部例外 |

### 2.7 操作ログ（D7・A-1）
- **書き込み操作（§6・§7）は INFO で専用ロガーに記録（MUST）**: 時刻・送信元 IP（`request.remote_addr`）・
  メソッド・パス・主要パラメータ・結果（ok/error code）。
- 読み取り（§5）は**記録しない**（or DEBUG）。既存 `before_request`/`after_request`（DEBUG・パスのみ）とは別建て。
- ⚠️ 無認証のため操作主体は **IP 粒度まで**（D5 の帰結）。ボディに認証情報や PII を含めない（CLAUDE.md 機密ルール）。

### 2.8 重い操作の非同期受付（202・D7）
- **重い操作**（`POST /tournament`・`POST /game`・`POST /bracket:refresh`）は**同期完了を待たず
  `202 Accepted` を返す（MUST）**。理由: 委譲先が同期ブロックしうる（例: `set_game` は
  `@gui_thread_sync` ＋ `LoadGameAssets(async_mode=False)` で**アセット再ロード完了までブロック**、
  [AutonaWebServerActions.py:202](../../src/AutonaWebServerActions.py)）。同期で返すと HTTP がタイムアウトする。
- **完了確認はクライアントが WS 購読（§4）で行う**（`program_state_update` の `revision` 進行を観測）。
  202 レスポンスは受付のみを示し、`revision` は未確定（省略可）。
  ```json
  HTTP/1.1 202 Accepted
  { "ok": true, "data": { "accepted": true, "operation": "tournament.load" } }
  ```
- **多重起動ガード（B-1）**: 重い操作は**実行中フラグ**を持ち、実行中に同種要求が来たら
  **`409 CONFLICT`**（**MUST**）。202 で受け付けた処理が完了/失敗するまでフラグを保持する。
- 実行失敗（外部タイムアウト等）は 202 後に起きるため**レスポンスでは返せない**。
  失敗はログ（§2.7）に残し、状態が進まないことで間接的に検知される（`revision` が動かない）。

### 2.9 冪等性（D7・B-1）
- **相対操作**（`score:adjust`）は冪等でない。`Idempotency-Key` ヘッダを**任意対応（SHOULD）**:
  同一キーの再送は**重複適用せず**直前結果を返す。キー無しは従来通り（毎回適用）。
  - キーの保持はプロセス内の短命キャッシュ（直近 N 件 / 数十秒・`TBD`）。
- **絶対値設定**（`score:reset`・`PATCH set`・`:swap-teams`）は本質的に冪等寄りのため必須ではない。

### 2.10 スレッド安全性（実行中フラグ・冪等キャッシュ）
- WebServer は QThread ＋ Flask `async_mode='threading'` ＝ **複数リクエストスレッドが並行**しうる。
- §2.8 の**実行中フラグ**と §2.9 の**冪等キャッシュ**は、本 API 層が持つ新規共有状態であり、
  **`threading.Lock` で保護する（MUST）**。粒度（操作種別ごと / グローバル）は実装フェーズで確定（§13）。
- これらは Qt のスレッド境界とは独立した「HTTP 受付層の状態」。`StateManager.lock`（RLock）とは別管理。

### 2.11 同時操作・順序（参考）
- UI / ホットキー / 既存 Web / 本 API はいずれも最終的に **GUI イベントループ上で直列処理**される（候補書 §9）。
  明示ロックは無く「到着順・最後勝ち」。本 API は割り込みを特別扱いしない（重い操作の多重起動だけ §2.8 で弾く）。

### 2.12 scoreboardNumber（パスパラメータ `{n}`）の規約
コード確認（[AutonaScoreboardManager.py:41-87](../../src/AutonaScoreboardManager.py)）に基づく。

- **有効値は `1`/`2`/`3` のみ**。3 ボードは同一 SCORE ドックに常時生成され、**表示は 1 つだけ切替**される。

  | n | 種類 | クラス | 状態キー |
  |---|------|--------|---------|
  | 1 | 1v1（シングルス・既定表示） | `AutonaScoreboardWidget` | `score.1` |
  | 2 | チーム | `AutonaScoreboardWidget` | `score.2` |
  | 3 | クルーバトル | `AutonaCrewBattleWidget`（**別クラス**） | `score.3` |

- **`{n}` は API 層で `{1,2,3}` を検証し、範囲外は `400 INVALID_ARGUMENT`（MUST）**。
  `GetScoreboard(n)` は未知番号で `None` を返すのみで、既存 Action はその後 `None.signals` で
  AttributeError になるため、**API 層で事前検証する**（NOT_FOUND ではなく INVALID_ARGUMENT 扱い）。
- **board 3（crew）は別クラス**。`score:adjust`（`CommandScoreChange`）等の board1/2 用操作は board3 に
  通用しない。crew 専用操作（ストック増減・active 設定）は**初回スコープ外**（§1.1）。
  → board1/2 用操作を board3 に投げた場合は `409 NOT_READY`（or `INVALID_ARGUMENT`）で**明示拒否**する（実装方針・§13）。
- **非表示ボードへの操作は配信に出ない**: OBS は表示中ボードのレイアウトを参照するため、裏ボードを
  操作しても画面は変わらない。誤対象を避けるため、`GET /api/v1/health`（§5.1）に**現在アクティブな
  ボード番号**を載せる（`activeBoard`）。
- 初回は **board 1（1v1）を主対象**として運用想定（board 2 も同操作で動くが、表示切替はメニュー操作前提）。

---

## 3. 前提条件（NOT_READY の扱い）

- 大会/試合に依存する読み取り（`/sets`・`/sets/{id}`）は provider 不在時、`data` を空（`[]`/`null`）で返す（既存挙動準拠）。
- provider 必須の操作（`bracket:refresh` 等）は provider 不在時 **`409 NOT_READY`**。
- スコア操作・set 情報設定は大会未ロードでも現在の盤面状態に作用する（試合未選択をエラーにしない）。

---

## 4. WebSocket 購読（既存を流用）

- 本 API は**新しい WS チャネルを作らない**。状態フィードバックは**既存 Socket.IO をそのまま購読**する（D3）。
  - 接続時 / `program_state` 要求 → フル状態を受信
  - `program_state_update` → デルタ（`revision`/`delta_index` + `delta`）を受信（[AutonaWebServer.py](../../src/AutonaWebServer.py) の delta coalescing 準拠）
- Companion のボタンフィードバック（点灯・数値表示）はこの購読 + `GET /api/v1/state` で実現する（§11）。

---

## 5. 読み取りエンドポイント（GET・操作ログ対象外）

| メソッド・パス | 返却 `data` | 委譲先 Action | 既存ルート |
|---------------|------------|--------------|-----------|
| `GET /api/v1/state` | 全状態（フル） | `program_state` | #1 |
| `GET /api/v1/health` | §5.1 参照（新規） | 新規 | — |
| `GET /api/v1/scoreboards/{n}` | スコアボード n の状態（`swapped` 含む） | `get_scoreboard`(+`get_swap`) | #9/#22 |
| `GET /api/v1/games` | ゲーム一覧 | `get_games` | #15 |
| `GET /api/v1/games/current` | 現在ゲーム | `get_current_game` | #16 |
| `GET /api/v1/match-names` | match/phase 名候補 | `get_match_names` | #17 |
| `GET /api/v1/characters` | キャラ一覧 | `get_characters` | #18 |
| `GET /api/v1/variants` | バリアント一覧 | `get_variants` | #19 |
| `GET /api/v1/controllers` | コントローラ一覧 | `get_controllers` | #20 |
| `GET /api/v1/sets` | 試合一覧（`?finished=true` で完了含む） | `get_sets` | #35 |
| `GET /api/v1/sets/{setId}` | セット詳細 | `get_match` | #36 |
| `GET /api/v1/commentary` | 実況者状態 | `get_comms` | #37 |

> `data` の具体スキーマは委譲先 Action の現返却（candidates §3）に準拠。本 API はそれを `{"ok":true,"data":...}` で包むだけ。

### 5.1 `GET /api/v1/health`（新規）
死活・前提状態の軽量取得。Companion の監視・条件分岐用。

```json
{ "ok": true, "data": {
  "status": "ok",
  "version": "1.00",
  "revision": 1234,
  "currentGame": "ssbu",
  "activeBoard": 1,
  "tournamentLoaded": true,
  "outLastWriteUnix": 1718000000
} }
```
- `activeBoard`: 現在表示中のボード番号（1/2/3）。外部コントローラが「表示中ボードを操作」する判断材料（§2.12）。取得方法は `TBD`（`SetBattleFormat` の現在値をどこから読むか）。
- `outLastWriteUnix`: `out/` 配信ファイルの最終更新時刻（OBS 出力健全性の指標）。取得方法は `TBD`。
- `tournamentLoaded`: provider 有無。

---

## 6. 安全操作エンドポイント（書き込み・操作ログ対象 / confirm 不要）

### 6.1 スコア加算 `POST /api/v1/scoreboards/{n}/teams/{t}/score:adjust`
- パス: `n`=スコアボード番号, `t`=1|2
- body: `{ "delta": 1 }`（`-N`〜`+N` の整数。±1 が基本）
- 委譲: `team_scoreup`/`team_scoredown`（delta 符号で分岐、|delta| 回 emit）= #3/#4
- 冪等: `Idempotency-Key` 対応（§2.9）
- エラー: `INVALID_ARGUMENT`（t/delta 不正）

### 6.2 スコアリセット `POST /api/v1/scoreboards/{n}/score:reset`
- body: なし
- 委譲: `reset_scores` = #30

### 6.3 チーム入替 `POST /api/v1/scoreboards/{n}:swap-teams`
- body: なし
- 委譲: `swap_teams` = #21

### 6.4 チームカラー `POST /api/v1/scoreboards/{n}/teams/{t}/color`
- body: `{ "color": "#FF0000" }`（`#` 付き hex）
- 委譲: `team_color` = #5
- エラー: `INVALID_ARGUMENT`（色形式不正）

### 6.5 セット情報設定 `PATCH /api/v1/scoreboards/{n}/set`
- body（部分更新・与えたキーのみ反映）:
  ```json
  { "bestOf": 5, "phase": "Top 8", "match": "Winners Finals",
    "players": 1, "characters": 1, "losers": { "team": 1, "value": true } }
  ```
- 委譲: `set_route`（既存はクエリ引数。本 API は JSON を同 Action にマップ）= #10
- エラー: `INVALID_ARGUMENT`（型不正）

---

## 7. 危険操作エンドポイント（書き込み・操作ログ対象 / ガード必須）

### 7.1 一覧

| メソッド・パス | ガード | 委譲先 Action | 既存 |
|---------------|--------|--------------|------|
| `POST /api/v1/scoreboards/{n}:clear` | confirm | `clear_all` | #33 |
| `POST /api/v1/scoreboards/{n}:reset-players` | confirm | `reset_players` | #32 |
| `POST /api/v1/scoreboards/{n}:reset-match` | confirm | `reset_match` | #31 |
| `POST /api/v1/scoreboards/{n}:load-set` | （confirm 推奨） | `load_set` | #38 |
| `POST /api/v1/scoreboards/{n}:pull-stream` | （confirm 推奨） | `pull_stream_set` | #24 |
| `POST /api/v1/scoreboards/{n}:pull-user` | （confirm 推奨） | `pull_user_set` | #25 |
| `POST /api/v1/tournament` | 実行中フラグ | `load_tournament` | #44 |
| `POST /api/v1/game` | 実行中フラグ | `set_game` | #14 |
| `POST /api/v1/bracket:refresh` | 実行中フラグ | `update_bracket` | #41 |

### 7.2 確認フラグ（C-2 / D7）
- **破壊的操作**（`:clear`・`:reset-players`・`:reset-match`）は body に **`"confirm": true` 必須（MUST）**。
  無い/false なら **`409 CONFIRMATION_REQUIRED`**（状態は変更しない）。
- 「配信中」自動ブロックは**初回は設けない**（Autona に配信状態判定手段が無いため）。confirm のみで担保。
  → 配信状態判定手段が入ったら ADR-001 Revisit。

### 7.3 重い操作のペイロード
> **重い操作（tournament / game / bracket:refresh）は `202 Accepted` で受け付け、完了は WS 購読で確認する（§2.8）。**
> 多重起動は `409 CONFLICT`。下記の `UPSTREAM_TIMEOUT`/`NOT_FOUND`/`NOT_READY` は**受付前に判定できる場合のみ**
> 同期で返し、受付後（202 以降）の失敗はログに残す（§2.8）。

- `POST /api/v1/tournament`: `{ "url": "https://www.start.gg/tournament/.../event/..." }`
  - URL 正規化は既存 `load_tournament` に委譲。`url` 空/省略で大会クリア（既存挙動）。
  - 外部通信を伴う。受付は 202、ロード結果は WS で観測。実行中は `409 CONFLICT`（§2.8）。
- `POST /api/v1/game`: `{ "codename": "ssbu", "modsActive": false }`
  - 未知 codename は受付前に判定できれば `404 NOT_FOUND`。アセット再ロードで重い → 202 + 実行中フラグ。
- `POST /api/v1/bracket:refresh`: body なし。provider 不在は受付前に `409 NOT_READY`。それ以外は 202 + 実行中フラグ。

> **注**: 旧 `POST /api/v1/ui/theme`（ライト/ダーク切替）は廃止。テーマはダーク固定（[Design.md §5.4](Design.md)）。

---

## 8. リクエスト/レスポンス例

スコア +1（冪等キー付き）:
```
POST /api/v1/scoreboards/1/teams/1/score:adjust
Idempotency-Key: 7f3a-...
Content-Type: application/json

{ "delta": 1 }
```
```json
HTTP/1.1 200 OK
{ "ok": true, "revision": 1042 }
```

confirm 無しの clear:
```
POST /api/v1/scoreboards/1:clear
{ }
```
```json
HTTP/1.1 409 Conflict
{ "ok": false, "error": { "code": "CONFIRMATION_REQUIRED",
  "message": "clear requires confirm:true" } }
```

重い操作の多重起動:
```json
HTTP/1.1 409 Conflict
{ "ok": false, "error": { "code": "CONFLICT",
  "message": "tournament load already in progress" } }
```

---

## 9. 実装ガイド（既存パターン遵守）

- **配置**: `/api/v1` ルート定義は既存 `AutonaWebServer.WebServer` に追加し、内部は `WebServer.actions.*`（既存 `WebServerActions`）を呼ぶ。
  状態ロジックを新規に書かない（D4）。エンベロープ整形は本 API 層の薄いヘルパーに集約。
- **GUI スレッド**: 委譲先 Action の多くは `@gui_thread_sync`。本 API も既存同様 QThread 上で呼べばよい（既存 Web ルートと同じ呼び方）。
- **既存ルートを壊さない**: 既存ハンドラ・Socket.IO イベントは変更しない（regression 禁止・current-features.md「must not regress」）。
- **二重定義に追従不要**: 本 API は HTTP のみ提供（WS フィードバックは §4 の既存購読で足りる）。Socket.IO 側に `/api/v1` 相当は作らない。
- **ポート**: 既存 `general.webserver_port`（既定 5000）を共有。新ポートは開かない。

---

## 10. テスト方針（手動・自動テストなし）

- **自動テストは作らない**（PdM 決定）。動作確認は**アプリ上で手動**で行う
  （`python main.py` 起動 → curl/HTTP クライアントで `/api/v1/...` を叩く → UI / `out/` / OBS で反映確認）。
- 手動確認項目は [../manual-test-checklist.md](../manual-test-checklist.md) の「外部制御 API」節になぞる:
  - 既存 `/score`・`/scoreboard<N>-...` ルートと Web UI / OBS が従来通り動く（regression）。
  - `score:adjust` の delta 符号・冪等キー再送で二重加算しない。
  - 破壊的操作は confirm 無しで状態が変わらない。
  - 重い操作が 202 で返り、完了が WS で観測でき、実行中の同種要求が 409 になる。
  - 操作ログに書き込み操作が INFO で出る／読み取りは出ない。
  - 範囲外 `{n}`（0 や 4）が 400 になる。board3 に board1/2 用操作で拒否される。

---

## 11. Companion / Stream Deck 連携時の注意（再掲・候補書 §11/§12）

- 外部コントローラは**本 API のクライアントに徹する**（状態ロジックを持たせない）。
- フィードバックは `GET /api/v1/state` + 既存 WS 購読（§4）。Companion のボタン設計に API を引きずらない。
- 物理ボタン誤爆対策として破壊的操作は confirm（§7.2）。連打・再送は冪等キー（§2.9）で吸収。

---

## 12. Open items（実装フェーズで確定）

> ✅ `revision`・`activeBoard`・`outLastWriteUnix` の取得経路は §14 スパイクで確定。

- 多重実行フラグ・冪等キャッシュの粒度（操作種別ごと / グローバル）と Lock 設計（§2.10）→ §14 に方針あり、実装で確定。
- `Idempotency-Key` キャッシュの保持件数・TTL。
- `set_route`（PATCH set）の `players`/`characters` 同時指定時の適用順（既存挙動の確認）。
- `set_team_data`（選手データ更新）の payload スキーマ未確定のため**初回スコープ外**（確定後に追補）。

## 13. 実装前に確認すること（pre-implementation・コーディング着手前）

実装チケットに入る前に、以下を**コード追読で確定**してから API ラッパを書く（PdM 決定 ③ に対応）。

1. **API 化対象 Action ごとの失敗モード棚卸し（必須）**: → §14.2 で完了。
2. **scoreboardNumber の検証実装（必須）**: `{1,2,3}` 検証と board3（別クラス）への不適合操作の拒否（§2.12）。
3. **重い操作の非同期化方式（必須）**: 202 で受け付け、実行は既存の signal emit / ワーカーに委ね、
   実行中フラグ（Lock 保護）で多重起動を弾く（§2.8/§2.10）。→ §14.3 に方式メモ。
4. **`revision` 取得経路の確定**: → §14.1 で完了。
5. **CORS プリフライト**: `PATCH` と `Idempotency-Key` ヘッダが既存 `CORS(app)` で通るか確認。→ §14.4。

---

## 14. 実装前スパイク結果（2026-06-15・コード追読）

### 14.1 取得経路の確定（revision / activeBoard / out）

| 項目 | 確定した取得経路 | 出典 |
|------|-----------------|------|
| `revision` | `StateManager.revision`（単調増加カウンタ、`_bumpRevision()` で +1）。読み取りは `GetFullState()` が `{'state','revision','delta_index'}` を返す。`GET /api/v1/state` はこれをそのまま使う | [StateManager.py:34,183-202](../../src/StateManager.py) |
| `activeBoard` | **`SettingsManager.Get("scoreboard.battle_format", "singles")`** を `{"singles":1,"team":2,"crew":3}` で写像。これが表示中ボードの真実源 | [Autona.py:952,968](../../src/Autona.py) / [AutonaScoreboardManager.py:69-75](../../src/AutonaScoreboardManager.py) |
| `outLastWriteUnix` | `os.path.getmtime("./out/program_state.json")`（毎 SaveState でエクスポートスレッドが書く）。または同ファイル内の `timestamp`/`revision` フィールド | [StateManager.py:104-109](../../src/StateManager.py) |

### 14.1a revision のタイミング（重要）
- **`@gui_thread_sync` の Action は同期実行**されるため、戻り時点で `StateManager.revision` は当該変更を反映済み
  → 操作レスポンスの `revision` は正確。
- **`@gui_thread_sync` でない Action は Qt シグナルを emit して即時 return**するため、戻り時点で revision が
  **まだ上がっていない**ことがある（下表「sync」列が ✕ のもの）。
- 方針: 操作レスポンスの `revision` は「応答生成時点の `StateManager.revision`」を**ベストエフォート**で返す。
  非同期 emit 系では当該操作を含まない場合がある旨を仕様（§2.4）どおり明記。**確定確認はクライアントが WS で行う**。

### 14.2 Action 失敗モード棚卸し（初回スコープ）

凡例: sync=`@gui_thread_sync` 有無。失敗モード→マップすべき §2.6 エラーコード。

| エンドポイント | 委譲 Action | sync | 主な失敗モード（コード根拠） | マップ |
|---------------|------------|:---:|------------------------------|--------|
| `GET /state` | `program_state` | ✕ | なし（`GetFullState`） | — |
| `GET /health` | （新規） | — | `out` 未生成時の getmtime → ファイル無ガード | 内部で握り潰し |
| `GET /scoreboards/{n}` | `get_scoreboard` | ✕ | n 不正→`GetScoreboard()`=None→`None.scoreboardNumber` で **AttributeError** | `INVALID_ARGUMENT`（n 事前検証） |
| `GET /scoreboards/{n}`(swap) | `get_swap` | ✕ | 同上（`None.teamsSwapped`） | `INVALID_ARGUMENT` |
| `GET /games`他マスタ | `get_games`等 | 一部✓ | アセット未ロードで空。致命例外は低 | — |
| `GET /sets` | `get_sets` | ✕ | provider null は **✓ガード済→[]**。ただし `provider.GetMatches()` は**同期 Start.gg 呼び**で例外/タイムアウト | `UPSTREAM_TIMEOUT`（要 try/except） |
| `GET /sets/{id}` | `get_match` | ✕ | **provider null 未チェック→AttributeError**／`int(setId)` 失敗→ValueError／同期 Start.gg 呼び | `NOT_READY`/`INVALID_ARGUMENT`/`UPSTREAM_TIMEOUT` |
| `POST .../score:adjust` | `team_scoreup/down` | ✕ | n/t 不正→`GetScoreboard()`=None→**AttributeError** | `INVALID_ARGUMENT` |
| `POST .../teams/{t}/color` | `team_color` | ✕ | n/t 不正→None→AttributeError／色形式未検証 | `INVALID_ARGUMENT` |
| `POST .../:swap-teams` | `swap_teams` | ✕ | n 不正→None→AttributeError | `INVALID_ARGUMENT` |
| `POST .../score:reset` | `reset_scores` | ✓ | n 不正→None→AttributeError | `INVALID_ARGUMENT` |
| `PATCH .../set` | `set_route` | ✓ | n 不正→None／`int(bestOf/players/characters)` 失敗→ValueError | `INVALID_ARGUMENT` |
| `POST .../:clear` | `clear_all` | ✓ | n 不正→None→AttributeError | `INVALID_ARGUMENT`（+ confirm 検証） |
| `POST .../:reset-players` | `reset_players` | ✓ | 同上 | `INVALID_ARGUMENT`/confirm |
| `POST .../:reset-match` | `reset_match` | ✓ | 同上 | `INVALID_ARGUMENT`/confirm |
| `POST .../:load-set` | `load_set` | ✓ | n 不正→None。set 非 str は `'0'` にフォールバック（既存） | `INVALID_ARGUMENT` |
| `POST .../:pull-stream/:pull-user` | `pull_stream_set`/`pull_user_set` | ✕ | n 不正→None→AttributeError | `INVALID_ARGUMENT` |
| `POST /tournament` | `load_tournament` | ✕ | url 検証は緩い（start.gg 以外でも Set される）／重い（非同期ロード） | 202 受付 |
| `POST /game` | `set_game` | ✓ | 未知 codename→**文字列 `"Could not find game X"` を返す**（非構造）／**同期アセット再ロードでブロック** | `NOT_FOUND`／202 化要（§14.3） |
| `POST /bracket:refresh` | `update_bracket` | ✓ | **`.provider` null 未チェック**／`GetTournamentPhases()[0]` **IndexError**／`.get("groups")[0]` 空で **TypeError/IndexError**／同期 Start.gg | `NOT_READY`/`UPSTREAM_TIMEOUT`（要堅牢化） |

**横断的な発見:**
1. **最頻の失敗は `GetScoreboard(n)`=None 後の AttributeError**。→ `{1,2,3}` 事前検証（§2.12）で大半を予防できる。
2. **`int()` パース**（bestOf/players/characters/setId/delta）は ValueError → API 層で型検証し `INVALID_ARGUMENT`。
3. **`get_sets`/`get_match`/`update_bracket` は同期 Start.gg 呼び**。読み取りでも 20 秒級の遅延・例外があり得る
   （agent-pitfalls #11）。→ 全て try/except で `UPSTREAM_TIMEOUT` に変換し、**読み取りでもタイムアウトしうる**旨を
   クライアントに通知。将来はキャッシュ/非同期化を検討（§12）。
4. **エラー文字列を返す Action**（`set_game`=`"Could not find game X"`、`load_player_from_tag`=`"ERROR"`、
   `load_commentator_from_tag`=`"ERROR : ..."`）。初回スコープでは `set_game` のみ該当 → ラッパで判定し `NOT_FOUND` に。
5. **`update_bracket` は堅牢性が低い**（複数の未ガード添字/属性アクセス）。202 化に加え、受付前の `NOT_READY` 判定と
   全体 try/except を本 API 層で被せる。

### 14.3 重い操作の 202 化メモ
- `load_tournament`・（理想的には）`update_bracket` は内部でシグナル emit / worker 起動するため、
  **API 層は「実行中フラグを立てて即 202」**で素直に実現できる。
- **`set_game` は `@gui_thread_sync` で同期ブロック**するのが課題。202 にするには API 層から
  **`@gui_thread_sync` を介さずに**起動する必要がある（例: `gui_thread_async` 系で投げっぱなしにする、
  または `LoadGameAssets(..., async_mode=True)` 相当の非同期経路を使う）。
  実装時に既存の非同期ロード経路（`AutonaGameAssetManager` の onLoad シグナル）を辿って確定する。
- 実行中フラグは操作キー（`tournament`/`game`/`bracket`）ごとに持ち、`threading.Lock` で保護（§2.10）。

### 14.4 CORS 判断
- 既存は `CORS(app)`（flask_cors・全体適用）＋ `app.config['CORS_HEADERS']='Content-Type'`（[AutonaWebServer.py:47-56](../../src/AutonaWebServer.py)）。
- flask_cors の既定は**全オリジン・全メソッド許可＋プリフライト自動処理**で、`allow_headers` も既定で要求ヘッダを反映する。
  → **`PATCH` と `Idempotency-Key` は通る見込み**。`CORS_HEADERS` は `@cross_origin` 補助設定で制約にはならない。
- リスク低。実機で 1 回だけ OPTIONS プリフライトを確認すれば足りる（自動テストは作らない方針）。
</content>
