# External Control API (`/api/v1/`) I/F 仕様

> **ステータス: 計画（未実装）。** 本書は ADR-001 で確定した方針に基づく**あるべき姿の I/F 仕様（正本）**。
> 実装が始まったら、実装と本書が乖離した場合は本書を優先し、差分は本書に追従させる。
> 既存の HTTP/WS 制御面の事実は変更しない（本 API は別名前空間として**追加**する）。

- 決定根拠: [../../docs/decisions/ADR-001-external-control-api-boundary.md](../../docs/decisions/ADR-001-external-control-api-boundary.md)（D1〜D8）
- 操作棚卸し・分類: [../../docs/architecture/external-control-api-candidates.md](../../docs/architecture/external-control-api-candidates.md)
- 同期/リビジョン規約の正本: [Design.md §3.2 同期経路の規約](Design.md)
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
- `data`: 読み取り系の本体。操作系では省略可。

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

### 2.8 多重実行ガード（D7・B-1）
- **重い操作**（`POST /tournament`・`POST /game`・`POST /bracket:refresh`）は**実行中フラグ**を持ち、
  実行中に同種要求が来たら **`409 CONFLICT`** を返す（**MUST**）。
- フラグはサーバープロセス内のスレッドセーフな状態（操作種別ごと or グローバル・`TBD`）。
- 既存コードに同等ガードが在るかは未確認（実装フェーズで確認し、無ければ本 API 層で実装）。

### 2.9 冪等性（D7・B-1）
- **相対操作**（`score:adjust`）は冪等でない。`Idempotency-Key` ヘッダを**任意対応（SHOULD）**:
  同一キーの再送は**重複適用せず**直前結果を返す。キー無しは従来通り（毎回適用）。
  - キーの保持はプロセス内の短命キャッシュ（直近 N 件 / 数十秒・`TBD`）。
- **絶対値設定**（`score:reset`・`PATCH set`・`:swap-teams`・`POST /ui/theme`）は本質的に冪等寄りのため必須ではない。

### 2.10 同時操作・順序（参考）
- UI / ホットキー / 既存 Web / 本 API はいずれも最終的に **GUI イベントループ上で直列処理**される（候補書 §9）。
  明示ロックは無く「到着順・最後勝ち」。本 API は割り込みを特別扱いしない（重い操作の多重起動だけ §2.8 で弾く）。

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
  "tournamentLoaded": true,
  "outLastWriteUnix": 1718000000
} }
```
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
| `POST /api/v1/ui/theme` | 操作ログ | `set_light_mode` | #12 |

### 7.2 確認フラグ（C-2 / D7）
- **破壊的操作**（`:clear`・`:reset-players`・`:reset-match`）は body に **`"confirm": true` 必須（MUST）**。
  無い/false なら **`409 CONFIRMATION_REQUIRED`**（状態は変更しない）。
- 「配信中」自動ブロックは**初回は設けない**（Autona に配信状態判定手段が無いため）。confirm のみで担保。
  → 配信状態判定手段が入ったら ADR-001 Revisit。

### 7.3 重い操作のペイロード
- `POST /api/v1/tournament`: `{ "url": "https://www.start.gg/tournament/.../event/..." }`
  - URL 正規化は既存 `load_tournament` に委譲。`url` 空/省略で大会クリア（既存挙動）。
  - 外部通信を伴う。`UPSTREAM_TIMEOUT` を返しうる。実行中は `409 CONFLICT`（§2.8）。
- `POST /api/v1/game`: `{ "codename": "ssbu", "modsActive": false }`
  - 未知 codename は `NOT_FOUND`。アセット再ロードで重い → 実行中フラグ。
- `POST /api/v1/bracket:refresh`: body なし。provider 不在で `NOT_READY`。実行中フラグ。
- `POST /api/v1/ui/theme`: `{ "value": true }`（true=light）。OBS 見た目に波及するため操作ログ必須。

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

## 10. テスト観点（実装時 / manual-test-checklist 連携）

- 既存 `/score`・`/scoreboard<N>-...` ルートと Web UI / OBS が従来通り動く（regression）。
- `score:adjust` の delta 符号・冪等キー再送で二重加算しない。
- 破壊的操作は confirm 無しで状態が変わらない。
- 重い操作の多重起動が 409 になる。
- 操作ログに書き込み操作が INFO で出る／読み取りは出ない。
- provider 不在時の `NOT_READY` / 外部タイムアウト時の `UPSTREAM_TIMEOUT`。

---

## 11. Companion / Stream Deck 連携時の注意（再掲・候補書 §11/§12）

- 外部コントローラは**本 API のクライアントに徹する**（状態ロジックを持たせない）。
- フィードバックは `GET /api/v1/state` + 既存 WS 購読（§4）。Companion のボタン設計に API を引きずらない。
- 物理ボタン誤爆対策として破壊的操作は confirm（§7.2）。連打・再送は冪等キー（§2.9）で吸収。

---

## 12. Open items（実装フェーズで確定）

- `revision` の正確な取得経路（StateManager のどの値を返すか）→ Design.md §3.2 と突合。
- `health.outLastWriteUnix` の取得方法（`out/` のどのファイルを基準にするか）。
- 多重実行フラグの粒度（操作種別ごと / グローバル）と保持実装。
- `Idempotency-Key` キャッシュの保持件数・TTL。
- `set_route`（PATCH set）の `players`/`characters` 同時指定時の適用順（既存挙動の確認）。
- `set_team_data`（選手データ更新）の payload スキーマ未確定のため**初回スコープ外**（確定後に追補）。
</content>
