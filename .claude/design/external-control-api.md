# External Control API (`/api/v1/`) I/F 仕様

> **ステータス: 実装済み。** 本書は ADR-001 で確定した方針に基づく I/F 仕様であり、
> 実装（[../../src/AutonaApiV1.py](../../src/AutonaApiV1.py)）の説明文書として扱う。実装と本書が乖離した場合は**実装を正**とし、本書を実装に追従させる。
> 既存の HTTP/WS 制御面の事実は変更しない（本 API は別名前空間として**追加**済み）。
> アプリ内導線: メニューバー「API設定」→ リファレンス・テスター（[../../src/AutonaApiInfoWindow.py](../../src/AutonaApiInfoWindow.py)）。

- 決定根拠: [../../docs/decisions/ADR-001-external-control-api-boundary.md](../../docs/decisions/ADR-001-external-control-api-boundary.md)（D1〜D8）
- 操作棚卸し・分類: [../../docs/architecture/external-control-api-candidates.md](../../docs/architecture/external-control-api-candidates.md)
- 同期/リビジョン規約の正本: [Design.md §3.2 同期経路の規約](Design.md)
- API 実装本体: [../../src/AutonaApiV1.py](../../src/AutonaApiV1.py)
- 既存ルート実装: [../../src/AutonaWebServer.py](../../src/AutonaWebServer.py) / [../../src/AutonaWebOperations.py](../../src/AutonaWebOperations.py)（両シェル共通の Web 操作。旧 `AutonaWebServerActions.py` は ADR-005 S3e-9 で削除）

> 凡例: **MUST**/**SHOULD**/**MAY**（RFC2119 相当）。`TBD` は実装フェーズで確定。

---

## 1. 目的・スコープ

- Autona の操作を外部コントローラ（Companion / Stream Deck / その他）から**安全に**呼び出すための汎用 API（ADR-001 D1）。
- 外部コントローラは**薄いクライアント**。状態ロジックを持たず本 API を叩くだけ（D2）。
- 本 API は既存 Flask+SocketIO サーバーに **`/api/v1/` 名前空間として薄く追加**し、内部は
  **既存ルートと同じ Web 操作（`AutonaWebOperations`）を再利用**する（D3。ADR-005 S3c までは `WebServerActions`）。
  新サーバー/プロセスは作らない。
- 状態変更は**必ず Web 操作 → core（`AutonaActions` / ドメインサービス）→ `StateManager` の単一経路**を通す
  （D4・不変条件。ADR-005 で Qt Signal の中継は無くなった）。StateManager をバイパスしない。デスクトップの
  ボードホストが GUI スレッドに直列化する操作（ADR-005 S3c）はそれを尊重する。

### 1.1 初回スコープ（D6）
- 読み取り（§5）/ 安全操作（§6）/ 危険操作（§7・ガード必須）を**初回から一括提供**。
- 初回除外: `open-set`（GUI ウィンドウ起動）/ X・Bluesky 投稿（外部送信・機密）/ 選手 DB 全量（個人情報）/ crew 系（board3 運用未確定）。

---

## 2. 共通規約

### 2.1 ベース・バージョニング
- ベースパス: **`/api/v1`**。破壊的変更時は `/api/v2` を新設し、`/api/v1` は一定期間維持。
- 既存ルート（`/score`・`/scoreboard<N>-...` 等）とは**パス空間が衝突しない**（`/api/` 前缀）。既存契約は不変（D3）。
- **正本は `/api/v1`。旧ルートは deprecated（v2.0 で削除予定。それまで挙動不変）**（ADR-001 Addendum 2026-10-21）。

### 2.2 認証・ネットワーク（D5 → ADR-005 S3a で改定・2026-10-25）
- バインドは既定 `127.0.0.1`。設定の「LAN 上の他のデバイスからのアクセスを許可」で `0.0.0.0`。
- **LAN の他端末からの書き込み（POST / PATCH）はアクセストークン必須**。読み取り（GET）はトークン不要のまま。
  同じ PC（同じ PC の Companion・ASS・curl）はトークン不要（今と同じ）。「同じ PC」は接続元がループバックか、この PC の LAN IP のどれか
  （LAN IP の URL を同じ PC で使っていても通る）。ただし転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP`）が付いた要求は、
  ローカルのリバースプロキシ・トンネルの中継とみなし、他の端末と同じ扱い（トークン必須）
  - トークンは `user_data/settings.json` の `general.webserver_token`（初回起動時に生成。API設定ウィンドウで表示・コピー・再発行）
  - 渡し方（どれか 1 つ）: `X-Autona-Token: <token>` / `Authorization: Bearer <token>` / クエリ `?token=<token>`（すでにクエリがあれば `&token=<token>`）
  - 無い・誤り → `401 UNAUTHENTICATED`（`WWW-Authenticate: Bearer realm="Autona"` 付き）
- **DNS rebinding 対策**（同じ PC にも適用）: トークンなしの書き込みは、`Host` がこのサーバーの名前
  （`127.0.0.1|localhost|[::1]|<この PC の LAN IP>:<待ち受けポート>`）のときだけ。それ以外の `Host` は `401 UNAUTHENTICATED`
  （`http://<PC 名>:5107` のようなホスト名も該当）
- **ブラウザ経由の CSRF 対策**（同じ PC にも適用）: トークンなしの書き込みで、`Sec-Fetch-Site` が付いていて `same-origin` / `none` 以外
  （`cross-site`・`same-site`）、または自分のオリジン（上と同じ名前とポート）以外の `Origin`（`null` を含む）が付いていたら
  `403 FORBIDDEN`。Companion・ASS・curl・`requests` はこれらのヘッダを付けないので影響しない。正しいトークン付きの要求は Host・CSRF の判定も免除
- 判定の正本は `src/core/WebAccess.py`（ルート・イベントの分類表 `ROUTE_ACCESS` / `EVENT_ACCESS`。分類のない endpoint は書き込み扱い）。
  legacy ルートも同じ判定を通り、拒否時だけ v1 と同じエラーエンベロープを返す（成功時の `"OK"` 等は不変）
- CORS（`Access-Control-Allow-Origin: *`）は残る。読み取りは別オリジンのページからも読める（OBS の file:// オーバーレイのため）
- ⚠️ トークンは平文 HTTP で流れ、全端末で 1 つを共有する。緩和は confirm（§7）・操作ログ（§2.7）・再発行。LAN 外運用が発生したら ADR-001 を Revisit。

### 2.3 Content-Type
- リクエストボディは `application/json`（**SHOULD**）。サーバーは `get_json(force=True)` で
  Content-Type が異なって/欠けていてもボディを JSON として解釈する（2026-07-16 変更。
  OBS Advanced Scene Switcher 等、ヘッダー設定が不確実なクライアントへの保険）。
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
  - `load_tournament` → `SettingsManager.Set("TOURNAMENT_URL")` ＋ シグナル emit（[`AutonaWebOperations.load_tournament`](../../src/AutonaWebOperations.py)）
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
| `UNAUTHENTICATED` | 401 | 書き込みにトークンが要るのに、無い・誤り（LAN の他端末・転送ヘッダ付きの中継・このサーバーの名前以外の `Host` 宛て。§2.2。ADR-005 S3a） |
| `FORBIDDEN` | 403 | トークンなしの書き込みが、別サイト・同一サイトの別オリジン・別オリジンのブラウザページから来た（§2.2 CSRF 対策） |
| `NOT_FOUND` | 404 | 存在しないリソース（未知の set ID 等） |
| `CONFIRMATION_REQUIRED` | 409 | 破壊的操作で `confirm:true` が無い（§7.2） |
| `CONFLICT` | 409 | 重い操作が実行中（多重起動ガード・§2.8） |
| `NOT_READY` | 409 | 前提未充足（大会/試合未ロードで provider 不在 等・§3） |
| `UPSTREAM_TIMEOUT` | 504 | Start.gg 等の外部依存がタイムアウト（agent-pitfalls #11） |
| `UNAVAILABLE` | 503 | この起動形態では使えない操作。ヘッドレス起動（`main.py --headless`）で、デスクトップの画面が要るシェル操作 2 本だけ（ADR-005 S3e-9）: legacy / Socket.IO のセット選択ウィンドウを開く操作（`scoreboard<n>-open-set`・`open_set`）とサムネイル（`scoreboard<n>-get-thumbnail-png|jpg`）。v1 にはもう無い（`:load-set`・`:pull-stream` は ADR-005 S3e-7 まで）が、コードは契約として残す。message は `not available in headless`。再試行しても変わらない。legacy ルートも同じ本文の 503 を返し、Socket.IO はイベントの ack で返す。セット読込（`:load-set`・legacy `load-set`・Socket.IO `load_set`）・配信中のセットの取り込み（`:pull-stream`・legacy `pull-stream`・Socket.IO `pull_stream`）・legacy `POST /score`（Socket.IO `score`）も ADR-005 S3e-7 から core の操作（`SetFollowService`）で、どちらの起動形態でも動く。クルーボード（board 3）の操作と実況の更新・タグからの実況者の読込は ADR-005 S3e-2 から core の操作で、どちらの起動形態でも動く（それまでは legacy / Socket.IO でもこれだった）。Top 8 の取り込み（`bracket:refresh`・legacy `/update-bracket`・Socket.IO `update_bracket`）も ADR-005 S3e-8 から core の操作（`Top8Service.RefreshFromProvider`）で、どちらの起動形態でも動く。選手の編集（`PATCH .../players/{p}`・legacy `update-team`・Socket.IO `update_team`）とタグからの選手の読込（legacy `load-player-from-tag`・Socket.IO `load_player_from_tag`）も ADR-005 S3e-4 から core の操作（`PlayerService.ApplyData`）で、どちらの起動形態でも、state にあるスロットに対して動く。統計（`stats:refresh`・legacy `/scoreboard<n>-stats-*`・Socket.IO `stats_*`）も ADR-005 S3e-5 から core の操作（`StatsService`）で、どちらの起動形態でも動く。ボード 1 / 2 の人数 / キャラ数（`PATCH set` の `players` / `characters`・legacy `scoreboard<n>-set`・Socket.IO、`:clear` の人数・キャラ数のリセット）も ADR-005 S3e-6 から core の操作（`AutonaActions.SetRoster`）で、どちらの起動形態でも動く（それまではヘッドレスでこれだった）。ボード 1 / 2 / 3 以外の人数 / キャラ数（legacy `scoreboard<n>-set`・Socket.IO `set`）は、起動形態によらずこの 503 で、何も書かずに断る（それまでデスクトップは 500）。同じボードへの set フィールド（phase / pool / match / best of / losers。legacy `scoreboard<n>-set`・`update-team`・Socket.IO）も ADR-005 S3e-7 から同じ 503（それまでデスクトップは 500）。v1 のボード 3 への書き込みは起動形態によらず `require_board_12` が 400 `INVALID_ARGUMENT` で断る（ADR-005 S3c） |
| `INTERNAL` | 500 | 想定外の内部例外 |

### 2.7 操作ログ（D7・A-1）
- **書き込み操作（§6・§7）は INFO で専用ロガーに記録（MUST）**: 時刻・送信元 IP（`request.remote_addr`）・
  メソッド・パス・主要パラメータ・結果（ok/error code）。
- 読み取り（§5）は**記録しない**（or DEBUG）。既存 `before_request`/`after_request`（DEBUG・パスのみ）とは別建て。
- ⚠️ トークンは全端末で 1 つを共有するため、操作主体は **IP 粒度まで**（D5 の帰結。S3a 以降も同じ）。トークンはログに出さない（拒否のログもパスのみで、クエリ文字列を含めない）。ボディに認証情報や PII を含めない（CLAUDE.md 機密ルール）。
  - 選手データを運ぶ `PATCH .../players/{p}`（§6.8）は**ボディのキー名だけ**を記録し、値（タグ・本名・SNS）は記録しない。

### 2.8 重い操作の非同期受付（202・D7）
- **重い操作**（`POST /tournament`・`POST /game`・`POST /bracket:refresh`）は**同期完了を待たず
  `202 Accepted` を返す（MUST）**。理由: 委譲先が同期ブロックしうる（例: legacy の `set_game` は
  `LoadGameByCodename(async_mode=False)` で**アセット再ロード完了まで呼び出し元のスレッドをブロック**する、
  [`AutonaWebOperations.set_game`](../../src/AutonaWebOperations.py)。ADR-005 S3f-4 からは GUI スレッドではなく
  リクエストのスレッドが、アセットアダプタのロードのジョブを待つ）。同期で返すと HTTP がタイムアウトする。
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
- **絶対値設定**（`score:reset`・`score:set`・`PATCH set`・`PATCH .../players/{p}`・`PATCH /display-options`・`:swap-teams`）は本質的に冪等寄りのため必須ではない。

### 2.10 スレッド安全性（実行中フラグ・冪等キャッシュ）
- WebServer は `threading.Thread` 上の werkzeug threaded サーバー（ADR-005 S3c。旧 QThread）＋ Flask `async_mode='threading'` ＝ **複数リクエストスレッドが並行**しうる。
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
  - ゲームの切り替え（ロード）では、ロードの delta の後に全量（`program_state`、続いて `characters`）が配信される。全量の `revision` は中身と一致する（ADR-005 S3f-4）。そのため**ゲームの切り替えだけは、開いている delta の窓を待たずにすぐ送る**（通常は「先頭 0 ms・以後 50 / 100 / 200 ms の窓は届いた delta で動かない」。例外はこの 1 つ）
- Companion のボタンフィードバック（点灯・数値表示）はこの購読 + `GET /api/v1/state` で実現する（§11）。

---

## 5. 読み取りエンドポイント（GET・操作ログ対象外）

| メソッド・パス | 返却 `data` | 委譲先 Action | 既存ルート |
|---------------|------------|--------------|-----------|
| `GET /api/v1/state` | 全状態（フル） | `program_state` | #1 |
| `GET /api/v1/health` | §5.1 参照（新規） | 新規 | — |
| `GET /api/v1/scoreboards/{n}` | スコアボード n の状態（`swapped` 含む。`swapped` はボード 1/2 では `score.{n}.teamsSwapped` のライブ値（反映前でも入替を返す。ADR-005 S2c〜）、ボード 3 は `CrewBattleService` が持つフラグ（state には無い）。ADR-005 S3e-2 からヘッドレス起動でもボード 3 の `swapped` を返す（S3c〜S3e-1 のヘッドレスでは、クルーウィジェットが無いのでキーなしだった）） | `get_scoreboard`(+`get_swap`) | #9/#22 |
| `GET /api/v1/games` | ゲーム一覧 | `get_games` | #15 |
| `GET /api/v1/games/current` | 現在ゲーム | `get_current_game` | #16 |
| `GET /api/v1/match-names` | match/phase 名候補 | `get_match_names` | #17 |
| `GET /api/v1/characters` | キャラ一覧 | `get_characters` | #18 |
| `GET /api/v1/variants` | バリアント一覧 | `get_variants` | #19 |
| `GET /api/v1/sets` | 試合一覧（`?finished=true` で完了含む） | `get_sets` | #35 |
| `GET /api/v1/sets/{setId}` | セット詳細 | `get_match` | #36 |
| `GET /api/v1/commentary` | 実況者状態 | `get_comms` | #37 |
| `GET /api/v1/countries/{code}/states` | 国の州・地域一覧（未知の国は `404 NOT_FOUND`。2026-10-21 追加） | `get_states` | #45 |
| `GET /api/v1/layouts` | レイアウト一覧（§5.2。2026-10-22 追加） | なし（`core/LayoutCatalog.Layouts`） | — |

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

### 5.2 `GET /api/v1/layouts`（2026-10-22 追加）
`layout/` 配下の配信用レイアウト（旧来型テンプレート + Designer で作った `layout/custom/{slug}/`）の一覧。
Board / Commentary / Top 8 タブと Designer の「開く」一覧が使う**同じカタログ**（`src/core/LayoutCatalog.py`）を返す
（[../../docs/changes/layout-custom-unification.md](../../docs/changes/layout-custom-unification.md) Step 1）。

- `?kind=` で絞り込む。値は `scoreboard` / `designer` / `commentary` / `top8`。カンマ区切り（`?kind=scoreboard,designer`）・
  パラメータの繰り返しのどちらでも複数指定できる。省略・空なら全件。未知の kind は `400 INVALID_ARGUMENT`
- 状態ではないので `revision` は付かない。読み取りなので操作ログにも出ない（§2.7）
- 並び順は旧来型（フォルダ名順）→ Designer プロジェクト（slug の大文字小文字無視順）。Board タブのプルダウンと同じ

```json
{ "ok": true, "data": [
  { "id": "scoreboard_simple", "kind": "scoreboard", "displayName": "Simple",
    "format": "1v1 / チーム", "game": "汎用（全ゲーム）", "entry": "index.html" },
  { "id": "custom/finals", "kind": "designer", "displayName": "Custom: 決勝",
    "format": "1v1", "game": "カスタム", "entry": "index.html" }
] }
```
- `id`: `layout/` からの相対パス（区切りは `/`）。配信 URL は `http://{host}:{port}/layout/{id}/{entry}`、
  OBS のローカルファイルは `layout/{id}/{entry}`
- `displayName` / `format` / `game`: 旧来型は各フォルダの `settings.json` の `autona` ブロック、Designer は
  `project.json` の `name` と `meta.format` / `meta.game`（未設定なら `"1v1"` / `"カスタム"`）。旧来型で宣言の無い
  `format` / `game` は `null`
- `displayName` の表記: Designer は `"Custom: " + name`。同じ `kind` の中で `displayName` が重複したエントリは、
  重複したもの全部が `"{displayName} ({id})"` になる（例: `"Simple (scoreboard_simple - コピー)"`）。重複が無ければそのまま。
  規約の正本は `layout/README.md`「レイアウトのメタデータ（`autona` ブロック）」
- ファイルシステムの絶対パス（カタログ内部の監視ディレクトリ等）は返さない（D5: LAN 公開時もパスを出さない）

---

## 6. 安全操作エンドポイント（書き込み・操作ログ対象 / confirm 不要）

**反映ゲート（2026-07-12 監査・明文化）**: ボード 1/2 への書き込み（§6.1〜6.5 および §7 の
クリア/リセット/セット読込）は、デスクトップ UI の操作と同一の Qt 経路を通って
`score.{n}` の held サブツリー（`StateManager.HoldSubtree`）に着地する。したがって
**アプリのスコアボード入力機能には即座に映るが、layout（OBS オーバーレイ・WS デルタ・
`program_state.json`・`out/` テキスト）へは §6.6 の `:apply`（反映）を送るまで公開されない**。
例外（デスクトップ UI と同一挙動）: ①ゲーム切替（`POST /game`、および大会/セット読込に伴う
自動ゲーム選択）は onLoad で held をベースライン公開する、②クルーバトル（board 3）は
held ゲート対象外で即時公開、③`tournamentInfo.*`（表示大会名等）は held 対象外で即時公開、
④統計キー `score.{n}.{last_sets,history_sets,recent_sets,upset_factor}` は
`HoldSubtree(exempt=...)` により held 対象外で即時公開（`stats:refresh` の結果は
反映を待たずに layout へ届く）。

**チーム番号の規約（2026-10-21 明文化）**: v1 のチーム番号（パスの `{t}`・body の `team`/`team1`/`team2`）は
すべて**状態側のチーム**（`score.{n}.team.{t}`。運用者と Web UI が見ている列）を指す。スワップ後も同じ列を指す。
旧ルートの `/score` と `-set?losers=` は set 形式の書き込みでスワップ時に反転するが、v1 はこれを持ち込まない。

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
- 委譲: `swap_teams` = #21 → ボードを構築してから `AutonaActions.SwapTeams`（core。ADR-005 S2c〜）
- 入れ替えるもの: 選手スロット全体（キャラのバリアント・mains・id・city・アバター・スポンサー画像を含む）・
  スコア・LOSERS・チーム名・チームロゴ・ゲームトラッカーの勝敗（鏡映）。**チームカラーは入れ替えない**（色は列のもの）。
  `score.{n}.teamsSwapped` を反転する（セット読込がどちらの列に第 1 エントラントを置くかの正本）。1 回の保存サイクル。
- `@gui_thread_sync` で同期実行（S2f まで。選手カードの入力確定が GUI スレッドで 2 書き込みに分かれるため、
  その間に入らないように）。応答時点で入替は完了している（旧実装は queued シグナルで、入替前に `OK` を返していた）。
  入力中の欄があれば先に確定する（フォーカスを外して Qt に確定させる。打ちかけの文字はその選手と一緒に動く。旧実装と同じ）。
- 未構築のボードは従来どおり構築してから入れ替える（未構築ボードは held 登録されていないので、構築前に入れ替えると
  即時公開されたうえ、構築時のシードで消える）。held 対象（反映待ち）。
- 旧ルート `/scoreboard{n}-swap-teams` / Socket.IO `swap_teams` も同じ Action。ボード 3 は旧ルートでは `CrewBattleService.SwapTeams`
  （ADR-005 S3e-2。それまではクルーウィジェット自身の入替。v1 は `require_board_12` で拒否）。

### 6.4 チームカラー `POST /api/v1/scoreboards/{n}/teams/{t}/color`
- body: `{ "color": "#FF0000" }`（`#` 付き hex）
- 委譲: `team_color` = #5
- エラー: `INVALID_ARGUMENT`（色形式不正）

### 6.5 セット情報設定 `PATCH /api/v1/scoreboards/{n}/set`
- body（部分更新・与えたキーのみ反映）:
  ```json
  { "bestOf": 5, "phase": "Top 8", "pool": "A1", "match": "Winners Finals",
    "players": 1, "characters": 1, "losers": { "team": 1, "value": true },
    "teamName": { "team": 1, "value": "Team Alpha" } }
  ```
- 委譲: `set_route`（既存はクエリ引数。本 API は JSON を同 Action にマップ）= #10
- `teamName`（2026-10-21 追加・Web UI 移行用）: 旧 `-set?teamName=&team=` 相当。`teamName` は別呼び出しで最後に適用する
  （他のキー → `losers` → `teamName`。旧クエリと同じ適用順）。
  チーム名は**状態側のチーム**（`team{t}column`）に書く。
- `losers` も**状態側のチーム**。`set_route` には渡さず `AutonaWebOperations.set_team_losers` → `AutonaActions.SetTeamLosers` を呼ぶ（ADR-005 S2b〜。
  それ以前は set 形式の `set_route` の losers をスワップ状態で補正して委譲していた）。フラグとそのチームの選手名の
  ` [L]`（`mergedName` / `mergedOnlyName`）を書く。旧経路（`ChangeSetData` ペイロード）の副作用は外部挙動として維持する:
  `match_en` を空にする（`SetLoadService.ApplyMatchInfo({})` をフラグと同じ保存サイクルで適用。フラグが変わらなくても空にする）、
  未構築のボードを構築する（構築で `score.{n}` が held 登録され、反映待ちになる）。
  適用順は旧クエリと同じ（他のキー → losers → `teamName`）。旧 `-set?losers=` はスワップ中に反対側へ着地する（旧ルートは不変）。
- 空の `phase` / `match` は「指定なし」扱いで反映されない（`SetLoadService.ApplyMatchInfo`・旧ルートと同じ）。
  `pool` は空文字でクリアされる。
- エラー: `INVALID_ARGUMENT`（型不正・team 範囲外・`teamName` が `{team,value}` でない／value が文字列でない）

### 6.6 反映（保留編集の公開） `POST /api/v1/scoreboards/{n}:apply`（2026-07-12 追加）
- body: なし
- 委譲: `apply_changes`（新規 Action・`@gui_thread_sync`）。デスクトップの
  反映ボタン / 反映ホットキー（Ctrl+Shift+L）と同一で、対象ボードの保留編集
  （`StateManager.CommitHeld("score.{n}")`）を一括公開する
- 冪等: 保留編集なしでの再送は no-op のため本質的に冪等
- エラー: `INVALID_ARGUMENT`（n 不正）

### 6.7 スコア一括設定 `POST /api/v1/scoreboards/{n}/score:set`（2026-10-21 追加）
- 旧 `POST /score` の v1 版（Web UI のスコア ± はこれで絶対値を送る。Design.md R-React-8）。
- body: `{ "team1": 2, "team2": 1 }`（**両方必須**・整数）。**状態側のチーム**（§6 冒頭）。
- 委譲: `set_scores` → `AutonaActions.SetScore` ×2（1 つの SaveBlock = 1 保存サイクル）。旧 `/score` の
  `post_score`（`ChangeSetData` に `reset_score` 付きで渡す set 形式の書き込み）は使わない。
- 値は 0〜99 にクランプ。現在値と同じ値は書かない（反映ボタンを点けない）。
- 旧 `/score` との違い（意図的）: スワップ中も反転しない／`general.disable_scoreupdate`（start.gg 由来のスコアを
  取り込まない設定）の影響を受けない／`match_en` を空にしない。スワップしていないボードでのスコアは旧ルートと同じ。
- held 対象（§6 冒頭の反映ゲート）。`revision` は最後に公開されたもの（`score:adjust` と同じ）。
- エラー: `INVALID_ARGUMENT`（n が 1/2 以外・team1/team2 欠落・整数でない）

### 6.8 選手データ更新 `PATCH /api/v1/scoreboards/{n}/teams/{t}/players/{p}`（2026-10-21 追加）
- 旧 `POST /scoreboard<N>-update-team-<T>-<P>` の v1 版（Web UI の選手カード）。
- body（部分更新）: 受け付けるキーは次のみ。それ以外は `INVALID_ARGUMENT`（任意 URL を取得させる
  `avatar` 等の内部キーを外部に開かないため）。
  ```json
  { "gamerTag": "Alpha", "prefix": "TM", "name": "本名", "twitter": "alpha_x",
    "custom_textbox": "", "country_code": "JP", "state_code": "13",
    "mains": { "ssbu": [["Mario", 0, ""]] }, "savePlayerToDb": false }
  ```
  - 文字列キーは `null` 可。**空・`null` の値は「変更なし」**（Action が空値を適用しない。旧ルートと同じ）。
  - `mains` はゲーム codename → `[en_name, skin, ""]` 配列のオブジェクト。`savePlayerToDb: true` で選手 DB にも保存。
  - 空ボディ `{}` は何もしない（200）。
- 委譲: `AutonaWebOperations.update_player` → `PlayerService.ApplyData(data, loadFromDB=True, clear=False)`
  （ADR-005 S3e-4。旧ルートの選手書き込みの部分だけ。S3e-3 までは `AutonaScoreboardWidget.ApplyPlayerData`）= #11。
  `t` は**状態側のチーム**。旧ルートと違い `match_en` を空にしない（セットのペイロードの `ApplyMatchInfo` を通らないため）。
  それ以外の結果は旧ルートと同じ。デスクトップ・ヘッドレスのどちらでも動く（同じ state になる）。
- **別の選手の名前にした部分更新は id と city を空にする**（ADR-005 S3e-4）: 書き込み後のスポンサー + タグの組が書き込み前と
  違い、body が `id` / `city` を持ってこない（DB の記録も持っていない）とき。同じスポンサー + タグなら残る。
  デスクトップのカードで名前を手入力したときと同じ規則（Web UI は id を送らないので、名前を書き換えると前の選手の
  id・city と統計が残っていた）。id が変わるので、統計が取り直される（ヘッドレスでも。ADR-005 S3e-5 から。それまでは
  デスクトップだけ）。`online_avatar` と `seed` は残る（デスクトップの手入力と同じ。タグの typo の修正やスポンサーの追記でも
  別の選手と判定されるため。LIVE-BUG-1）。
- `savePlayerToDb` の選手 DB への書き込みは、state の書き込み（同じ保存サイクル）の後、応答の前に行う
  （ボードホストのロックの外。保存する中身は書き込み直後のスロット）。同じ選手への保存が前後して届いたときは、
  後に読んだ記録が残る（先に読んだ方は捨てる）。
- `p` が、そのチームの state にあるスロット（ボードのカードが出しているもの）を超える場合は 404 `NOT_FOUND`
  （何も書かない。ヘッドレスも同じ）。旧ルートはログに残して `"OK"` を返す（今も同じ）。
- デスクトップは未構築のボードを先に構築し（held になる）、GUI スレッドで同期実行する。held 対象のため `revision` は
  最後に公開されたもの。ヘッドレスはすぐ公開される。
- 操作ログはキー名のみ（§2.7）。
- エラー: `INVALID_ARGUMENT`（n が 1/2 以外・t が 1/2 以外・p < 1・未知キー・型不正）、`NOT_FOUND`（上記）

### 6.9 表示トグル `PATCH /api/v1/display-options`（2026-10-21 追加）
- 旧 `POST /display-option` の v1 版（Web UI の目アイコンメニュー）。ボード非依存のグローバル設定。
- body: `{ "show_seed": false, "show_social": true }`（1 つ以上・部分更新）。キーは
  `src/core/StateSchema.py` の `DISPLAY_OPTIONS`（`show_sponsor`/`show_name`/`show_seed`/`show_characters`/
  `show_social`/`show_real_name`/`show_location`/`show_additional`）。
- 委譲: `set_display_option`（旧ルートと同一 Action。SettingsManager + StateManager の両面に書く）をキーごとに呼ぶ。
- **全キーを先に検証**し、未知キーが 1 つでもあれば何も適用せず `INVALID_ARGUMENT`。
- held 対象外のため即時公開され、`revision` は正確。
- エラー: `INVALID_ARGUMENT`（未知キー）

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
| `POST /api/v1/bracket:refresh` | 実行中フラグ | `update_bracket`（`AutonaWebOperations` → `Top8Service.RefreshFromProvider`。ADR-005 S3e-8） | #41 |

- `:load-set` / `:pull-stream`（ADR-005 S3e-7 から core の `SetFollowService`。どちらの起動形態でも動く）: 取得は provider のワーカーで
  非同期で、応答は受付時点の `revision`。大会が読み込まれていなければ **409 `NOT_READY`**（`no tournament loaded`）で、ボードに何も
  書かない（legacy `load-set` / `pull-stream`・Socket.IO `load_set` / `pull_stream` は何もせず `OK`。それまでのデスクトップは選手と
  スコアを消してから、取得できずに終わっていた）
- `:pull-stream`: 設定 `twitch_username` のチャンネルが次に配信するセットを読み込み、そのチャンネルを追従する（セット選択ウィンドウの
  「配信」と同じ）。**すでにそのチャンネルを追従していれば、今のセットを照会し直すだけ**で、チャンネルのセットが変わったときだけ
  読み直す（同じセットのあいだはクリアも取得もしない。Web UI の「自動更新」スイッチが数秒ごとに送るため）。追従を始める最初の
  1 回も、ボードが最後に読んだセットがチャンネルのセットと同じならクリアしない（手で読んだセットの上で自動更新をオンにしたとき）。
  照会が逆順に返っても、後から出した照会の答えだけを使う。セットの取得に失敗した（Start.gg の失敗・セットの無い答え）ら、
  次の `:pull-stream`・同じセットの `:load-set` で読み直す。チャンネル未設定は
  **409 `NOT_READY`**（`twitch_username is not set`。legacy / Socket.IO は何もせず `OK`）。それまではボードのシグナルを出すだけで、
  何も接続されておらず何も起きなかった（ADR-005 S3e 論点 3）

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
  - 実行中フラグは `load_tournament` を GUI スレッドで走らせたタスクの終了時に解除する（2026-10-21 修正）。
    以前は `tournament_changed` を待っていたが、読込済みと同じ URL では早期 return でこれが出ず、
    最大 120 秒（ガードの TTL）409 を返し続けていた。
- `POST /api/v1/game`: `{ "codename": "ssbu", "modsActive": false }`
  - 未知 codename は受付前に判定できれば `404 NOT_FOUND`。アセット再ロードで重い → 202 + 実行中フラグ。
- `POST /api/v1/bracket:refresh`: body なし。provider 不在は受付前に `409 NOT_READY`。それ以外は 202 + 実行中フラグ。
  - 実行中フラグは core（`Top8Service`）の 1 つを、Top 8 パネル・ホットキー・legacy `/update-bracket`・Socket.IO `update_bracket` と共有する。
    取り込みの終了（取得・組み立て・書き込みが終わったとき。空の結果・失敗を含む）まで立ったまま。実行中は `409 CONFLICT`
    （legacy / Socket.IO は `"OK"` のまま何もしない）。ADR-005 S3e-8。それまではこのルート専用のフラグで、
    パネルに取得を頼んだ時点で下ろしていた（取得中の 2 回目も 202 になり、取得が 2 本走った）
  - 受付後に結果が空（決勝のダブルエリミのグループが無い・Start.gg の失敗）なら何も書かない（ログのみ）

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

- **配置**: `/api/v1` ルート定義は既存 `AutonaWebServer.WebServer` に Blueprint で追加し、内部は既存ルートと同じ
  `AutonaWebOperations` のモジュール関数を呼ぶ（ADR-005 S3c。それまでは `WebServer.actions.*` = `WebServerActions`。
  S3e-9 で削除し、`init_api_v1(actions)` も無くなった）。
  状態ロジックを新規に書かない（D4）。エンベロープ整形は本 API 層の薄いヘルパーに集約。
- **スレッド**: Web 操作はリクエストのスレッドから呼べばよい（既存 Web ルートと同じ呼び方）。GUI スレッドが要るもの
  （デスクトップのボードホスト・シェル操作）は操作の側が marshal する。`set_game` は marshal しない
  （ADR-005 S3f-4。ロードは TaskExecutor のジョブで走り、モデルの作り直しだけをジョブが GUI スレッドへ渡す）。
  終了中で GUI スレッドに渡せないときは、ボードホスト・シェル操作とも 503（`the app is shutting down`）で答える。
- **既存ルートを壊さない**: 既存ハンドラ・Socket.IO イベントは変更しない（regression 禁止・docs/product/features.md「リグレッション禁止リスト」）。
- **二重定義に追従不要**: 本 API は HTTP のみ提供（WS フィードバックは §4 の既存購読で足りる）。Socket.IO 側に `/api/v1` 相当は作らない。
- **ポート**: 既存 `general.webserver_port`（既定 5000）を共有。新ポートは開かない。

---

## 10. テスト方針（手動・自動テストなし）

> 例外（2026-10-21）: Web UI 移行で追加・拡張したルート（§5 `countries/{code}/states`・§6.5 `teamName`/`losers`・
> §6.7〜§6.9・§7.3 `tournament` の実行中フラグ）と、Web UI が置換した呼び出しは `tests/test_api_v1.py` が
> 自動で pin する（旧ルートと同じ状態になること、および §6 冒頭のチーム番号規約など意図的な差分）。
> Web UI 側が状態側のチーム番号を送ることは `web/src/ScoreboardPage/v1Requests.test.js` が pin する。
> §5.2 `layouts`（2026-10-22）はエンベロープと `?kind=` を `tests/test_api_v1.py`、カタログの発見規則を
> `tests/test_layout_catalog.py` が pin する。
> ルート一覧そのものは `tests/test_webserver_routes.py` の `EXPECTED_API_V1_ROUTES` が台帳。

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
- ~~`set_team_data`（選手データ更新）の payload スキーマ未確定のため初回スコープ外~~ → 2026-10-21 に
  Web UI が送るキーで確定し §6.8 として追加。

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
| `outLastWriteUnix` | `os.path.getmtime("./out/program_state.json")`（SaveState が積んだ job を export writer が書く。書き込み待ちが溜まった場合は最新スナップショットだけを 1 回書く）。または同ファイル内の `timestamp`/`revision` フィールド | [StateManager.py:104-109](../../src/StateManager.py) |

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
| `GET /scoreboards/{n}`(swap) | `get_swap` | ✕ | ボードを構築してから、ボード 1/2 は `score.{n}.teamsSwapped` を読む（ADR-005 S2c〜）。n 不正→None→`None.teamsSwapped` | `INVALID_ARGUMENT` |
| `GET /games`他マスタ | `get_games`等 | ✕ | アセット未ロードで空。致命例外は低。ADR-005 S1b からゲームアセットカタログを直接読む（GUI スレッドを経由しない） | — |
| `GET /sets` | `get_sets` | ✕ | provider null は **✓ガード済→[]**。ただし `provider.GetMatches()` は**同期 Start.gg 呼び**で例外/タイムアウト | `UPSTREAM_TIMEOUT`（要 try/except） |
| `GET /sets/{id}` | `get_match` | ✕ | **provider null 未チェック→AttributeError**／`int(setId)` 失敗→ValueError／同期 Start.gg 呼び | `NOT_READY`/`INVALID_ARGUMENT`/`UPSTREAM_TIMEOUT` |
| `POST .../score:adjust` | `team_scoreup/down` | ✕ | n/t 不正→`GetScoreboard()`=None→**AttributeError** | `INVALID_ARGUMENT` |
| `POST .../teams/{t}/color` | `team_color` | ✕ | n/t 不正→None→AttributeError／色形式未検証 | `INVALID_ARGUMENT` |
| `POST .../:swap-teams` | `swap_teams` | ✓ | ボードを構築してから、ボード 1/2 は `AutonaActions.SwapTeams`（ADR-005 S2c〜）。n 不正→None→AttributeError | `INVALID_ARGUMENT` |
| `POST .../score:reset` | `reset_scores` | ✓ | n 不正→None→AttributeError | `INVALID_ARGUMENT` |
| `PATCH .../set` | `set_route` | ✓ | n 不正→None／`int(bestOf/players/characters)` 失敗→ValueError | `INVALID_ARGUMENT` |
| `POST .../:clear` | `clear_all` | ✓ | n 不正→None→AttributeError | `INVALID_ARGUMENT`（+ confirm 検証） |
| `POST .../:reset-players` | `reset_players` | ✓ | 同上 | `INVALID_ARGUMENT`/confirm |
| `POST .../:reset-match` | `reset_match` | ✓ | 同上 | `INVALID_ARGUMENT`/confirm |
| `POST .../:load-set` | `load_set` | ✓ | n 不正→None。set 非 str は `'0'` にフォールバック（既存） | `INVALID_ARGUMENT` / `NOT_READY`（大会未読込。ADR-005 S3e-7） |
| `POST .../:pull-stream/:pull-user` | `pull_stream_set`/`pull_user_set` | ✕ | n 不正→None→AttributeError | `INVALID_ARGUMENT` → ADR-005 S3e-7 で `:pull-stream` は core の操作（大会未読込・チャンネル未設定は `NOT_READY`）。`:pull-user` は未実装のまま |
| `POST /tournament` | `load_tournament` | ✕ | url 検証は緩い（start.gg 以外でも Set される）／重い（非同期ロード） | 202 受付 |
| `POST /game` | `set_game` | ✓ | 未知 codename→**文字列 `"Could not find game X"` を返す**（非構造）／**同期アセット再ロードでブロック** | `NOT_FOUND`／202 化要（§14.3） |
| `POST /bracket:refresh` | `update_bracket` | ✓ | **`.provider` null 未チェック**／`GetTournamentPhases()[0]` **IndexError**／`.get("groups")[0]` 空で **TypeError/IndexError**／同期 Start.gg | `NOT_READY`/`UPSTREAM_TIMEOUT`（要堅牢化）→ ADR-005 S3e-8 で解消（provider の確認・空の phase / グループの扱いを core に置き、取得は provider のワーカーで非同期。失敗は何も書かずログ） |

**横断的な発見:**
1. **最頻の失敗は `GetScoreboard(n)`=None 後の AttributeError**。→ `{1,2,3}` 事前検証（§2.12）で大半を予防できる。
2. **`int()` パース**（bestOf/players/characters/setId/delta）は ValueError → API 層で型検証し `INVALID_ARGUMENT`。
3. **`get_sets`/`get_match`/`update_bracket` は同期 Start.gg 呼び**。読み取りでも 20 秒級の遅延・例外があり得る
   （agent-pitfalls #11）。→ 全て try/except で `UPSTREAM_TIMEOUT` に変換し、**読み取りでもタイムアウトしうる**旨を
   クライアントに通知。将来はキャッシュ/非同期化を検討（§12）。
4. **エラー文字列を返す Action**（`set_game`=`"Could not find game X"`、`load_player_from_tag`=`"ERROR"`、
   `load_commentator_from_tag`=`"ERROR : ..."`、DB に無いタグは `"ERROR"`。成功時は ADR-005 S3e-2 から `"OK"`（それまでは None）・範囲は 1..12）。初回スコープでは `set_game` のみ該当 → ラッパで判定し `NOT_FOUND` に。
5. **`update_bracket` は堅牢性が低い**（複数の未ガード添字/属性アクセス。ADR-005 S3e-8 で core の `Top8Service` 側を堅牢化した）。202 化に加え、受付前の `NOT_READY` 判定と
   全体 try/except を本 API 層で被せる。

### 14.3 重い操作の 202 化メモ
- `load_tournament`・（理想的には）`update_bracket` は内部でシグナル emit / worker 起動するため、
  **API 層は「実行中フラグを立てて即 202」**で素直に実現できる。
- （ADR-005 S3f-4 で更新: `set_game` はもう `@gui_thread_sync` を持たない。legacy ルートはリクエストのスレッドで
  ロードのジョブを待ち、後から来た別のゲームの要求に追い越されたときは、その新しいロードが終わってから `"OK"` を返す。
  以下は当時の記録）
- **`set_game` は `@gui_thread_sync` で同期ブロック**するのが課題。202 にするには API 層から
  **`@gui_thread_sync` を介さずに**起動する必要がある（例: `gui_thread_async` 系で投げっぱなしにする、
  または `LoadGameAssets(..., async_mode=True)` 相当の非同期経路を使う）。
  実装時に既存の非同期ロード経路（`AutonaGameAssetManager` の onLoad シグナル）を辿って確定する。
  → 実装済み（ADR-005 S1b で改訂）: `LoadGameByCodename(codename, async_mode=True, done=...)` を GUI executor で起動し、
  フラグはロード試行の終了時（成功・スキップ・例外のいずれでも）に呼ばれる `done` で解放する。onLoad は実際に
  ロードされたときしか来ないため、解放には使わない。
- 実行中フラグは操作キー（`tournament`/`game`）ごとに持ち、`threading.Lock` で保護（§2.10）。`bracket` のフラグは
  ADR-005 S3e-8 から core の `Top8Service` が持つ（全入口で共有し、取り込みの終了まで立つ）。

### 14.4 CORS 判断
- 既存は `CORS(app)`（flask_cors・全体適用）＋ `app.config['CORS_HEADERS']='Content-Type'`（[AutonaWebServer.py:47-56](../../src/AutonaWebServer.py)）。
- flask_cors の既定は**全オリジン・全メソッド許可＋プリフライト自動処理**で、`allow_headers` も既定で要求ヘッダを反映する。
  → **`PATCH` と `Idempotency-Key` は通る見込み**。`CORS_HEADERS` は `@cross_origin` 補助設定で制約にはならない。
- リスク低。実機で 1 回だけ OPTIONS プリフライトを確認すれば足りる（自動テストは作らない方針）。
</content>
