# External Control API (`/api/v1/`) I/F 仕様

> 実装 [../../src/AutonaApiV1.py](../../src/AutonaApiV1.py) の外部契約をまとめた文書。実装と本書が食い違ったら**実装が正**で、本書を直す。
> 旧ルート（`/score`・`/scoreboard<N>-...` など）は deprecated で挙動は変えない。本 API は別名前空間として追加したものである。
> アプリ内の導線: 「Help & Settings」→「API 設定」のリファレンス・テスター（React の `/web/api`、[../../web/src/GuidePages/ApiPage.tsx](../../web/src/GuidePages/ApiPage.tsx)）。

- 決定根拠: [ADR-001](../decisions/ADR-001-external-control-api-boundary.md)
- 操作の棚卸し: [external-control-api-candidates.md](external-control-api-candidates.md)
- 同期・revision の規約: [Design.md §3.2](Design.md)
- **エンドポイント一覧の正本**: [../../src/core/ApiCatalog.py](../../src/core/ApiCatalog.py)（人が手で叩く行の厳選表）、実行時は `GET /api/v1/api-catalog`。全ルートは `src/AutonaApiV1.py` の `@api_v1.route` と、台帳 `tests/test_webserver_routes.py` の `EXPECTED_API_V1_ROUTES`
- アクセス区分の正本: [../../src/core/WebAccess.py](../../src/core/WebAccess.py)（`ROUTE_ACCESS`）

---

## 1. 目的・スコープ

- Companion / Stream Deck などの外部コントローラが Autona を**安全に**操作するための汎用 API。外部コントローラは薄いクライアントで、状態ロジックを持たない。
- 既存の Flask + Socket.IO サーバーに `/api/v1/` 名前空間として載り、内部は Web 操作（`AutonaWebOperations`）を通る。新しいサーバーやプロセスは作らない。
- 状態の変更は必ず **Web 操作 → core（`AutonaActions` / ドメインサービス）→ `StateManager`** の 1 経路を通る。ボードの操作は両シェル（デスクトップ・ヘッドレス）とも、リクエストのスレッドで、ボードホストの 1 本のロックの下で 1 つずつ走る。
- 提供するのは読み取り・安全な書き込み・危険な書き込み（confirm 必須）・重い操作（202）。除外: X / Bluesky への投稿（外部送信）。選手 DB の全量取得は `PLAYERDB` 区分（§2.2）で LAN からはトークン必須。

---

## 2. 共通規約

### 2.1 ベース・バージョニング

- ベースパスは `/api/v1`。破壊的変更が要るときは `/api/v2` を新設し、`/api/v1` は一定期間維持する。
- 既存ルートとはパス空間が衝突しない（`/api/` 接頭辞）。

### 2.2 認証・アクセス区分

各ルートは `WebAccess.ROUTE_ACCESS` で区分される（分類のないルートは書き込み扱い）。一覧表（§5）には、既定でない区分だけを付ける。

| 区分 | 通すもの |
|------|---------|
| `READ` | 誰でも（v1 の GET の既定。CORS は全オリジン許可。OBS の file:// オーバーレイが読むため） |
| `WRITE` | この PC からはトークンなし。LAN の他端末は**トークン必須**（v1 の POST / PATCH / PUT / DELETE の既定） |
| `PLAYERDB` | `WRITE` と同じ扱いの読み取り。本名・X ハンドル・反映前の編集・絶対パスを返すルート |
| `THIS_PC` | この PC の要求だけ。LAN の他端末はトークンがあっても `403`。この PC のウィンドウを開く・この PC の設定を変える・接続情報を返すルート |
| `SHELL` | デスクトップの共有ビュー（アプリ自身のウィンドウ）の fetch だけ。トークンがあっても `403`。ヘッドレスは常に `403` |

- バインドは既定 `127.0.0.1`。設定で LAN 公開（`0.0.0.0`）にできる。
- **トークン**: `user_data/settings.json` の `general.webserver_token`（初回起動時に生成。API 設定ページで表示・再発行）。渡し方はどれか 1 つ: `X-Autona-Token: <token>` / `Authorization: Bearer <token>` / クエリ `?token=<token>`。無い・誤りは `401 UNAUTHENTICATED`。
- 「同じ PC」は接続元がループバックかこの PC の LAN IP であること。転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP`）が付いた要求は中継とみなし、他の端末と同じ扱い。
- **トークンなしの書き込みへの追加検査**（同じ PC にも適用。正しいトークン付きは免除）: `Host` がこのサーバーの名前（`127.0.0.1` / `localhost` / `[::1]` / この PC の LAN IP + 待ち受けポート）でなければ `401`。`Sec-Fetch-Site` が `cross-site` / `same-site`、または自分以外の `Origin` なら `403`（ブラウザ経由の CSRF 対策。Companion・curl は影響しない）。
- `THIS_PC` は同じオリジンの第三者製レイアウトも通る区分なので、**アクセストークンは `THIS_PC` のルートでは返さない**（`SHELL` だけ）。ゲームアセットの書き込みも `SHELL` で、フォルダの取り込みはシェルの選択 id でだけ名指す（パスを受け取るルートは無い）。
- `SHELL` の通過条件は、`THIS_PC` を満たし、`Sec-Fetch-Site: same-origin` で、`X-Autona-Shell` ヘッダがシェルの秘密と一致すること。判定は `WebAccess.ShellRefusalReason`、ヘッダを付けるのは `src/AutonaWebPanelHost.py`。秘密はメモリにだけ持ち、ログに出さない。
- **プレビュー用リスナー**: レイアウトのプレビューは別ポート（`GET /health` の `previewPort`）の読み取り専用リスナーが配る。出すのは `layout/`・`assets/`・`user_data/` の画像・`out/program_state.json`・v1 の `READ`・Socket.IO の読み取りだけで、ほかはトークンがあっても `403`。
- 別のページがフレームに入れて頼んだ要求（`Sec-Fetch-Dest` がフレームで `same-origin` / `none` 以外）は、本体のポートでは何を頼んでも `403`。応答はすべて `frame-ancestors 'self'`。規則は `WebAccess.FramedElsewhere`。
- ⚠️ `Sec-Fetch-*` を送らない古いブラウザでは、CSRF 対策とフレームの判定は効かない。⚠️ トークンは平文 HTTP で流れ、全端末で 1 つを共有する。緩和は confirm（§2.11）・操作ログ（§2.7）・再発行。

### 2.3 Content-Type

- リクエスト本文は JSON（SHOULD）。サーバーは Content-Type が違う・欠けていても本文を JSON として読む（OBS Advanced Scene Switcher のようにヘッダー設定が不確実なクライアントのため）。
- レスポンスは `application/json`。例外は画像・動画など、ルートが明記するバイナリ。

### 2.4 成功レスポンス

```json
{ "ok": true, "data": { }, "revision": 1234 }
```

- 封筒のキーは `ok` / `data` / `revision` が基本。例外は `GET /layouts` の `unlisted`（`data` の隣。§ルート一覧）。クライアントは知らないキーを無視する。
- `revision`: 操作後の状態 revision。書き込み系は必ず含める。クライアントは WebSocket の delta の `revision` と突き合わせて反映済みかを判定できる。
- ⚠️ `revision` は `StateManager` の変更だけを追う。`SettingsManager` だけに書く操作（設定ページの保存など）は revision に反映されないので、設定系の反映確認は revision だけに頼らない。
- 大会の設定・解除は `SettingsManager`（`TOURNAMENT_URL`）と state（`tournamentInfo.url`）の両方を `core/TournamentService` が書く。これは許容している二重書き込みで、API 層は既存の操作へ委譲するだけ。

### 2.5 エラーレスポンス

```json
{ "ok": false, "error": { "code": "INVALID_ARGUMENT", "message": "team must be 1 or 2", "reason": "optional_detail" } }
```

- HTTP ステータスと `error.code` を両方返す。`message` は人間向け（i18n しない）。
- `error.reason`（任意）: `code` より細かいスネークケースの理由。ゲームアセットのルート・新しいデザインの作成（`/layouts:create`・`/layouts/kit/{kind}/ai-prompt`）と、アーカイブ切り抜きの一部（`not_previewable` / `unreadable` など）が付ける。core の文はパスを含みうるのでログにだけ出す（`AutonaApiV1._ASSET_REFUSALS` と、新しいデザインの `AutonaApiV1._KIT_REFUSALS` が表）。クライアントは無いことを想定して読む。

### 2.6 エラーコード

| code | HTTP | 意味 |
|------|------|------|
| `INVALID_ARGUMENT` | 400 | パラメータ不正（範囲外の team / board・型・上限超え） |
| `UNAUTHENTICATED` | 401 | 書き込みにトークンが要るのに無い・誤り（LAN・転送ヘッダ付き・名前の違う `Host`） |
| `FORBIDDEN` | 403 | CSRF 対策・`THIS_PC` / `SHELL` の区分・プレビュー用リスナー・フレームの拒否 |
| `NOT_FOUND` | 404 | 存在しないリソース |
| `PAYLOAD_TOO_LARGE` | 413 | 本文の上限を超えた。上限を持つのは画像の取り込みとゲームアセットの ZIP（8 GiB）だけで、`Content-Length` が超えていれば本文を読まずに断る |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | ファイルはあるが返せない種類（アーカイブの動画のプレビュー不可の形式） |
| `CONFIRMATION_REQUIRED` | 409 | 破壊的操作で `confirm: true` が無い（§2.11） |
| `CONFLICT` | 409 | 重い操作が実行中（§2.8）。選手 DB の保存が新しい保存に負けた・ゲームアセットのジョブが走っている、なども |
| `NOT_READY` | 409 | 前提が足りない（大会未ロード・索引未取得・空き容量不足・`settings.json` を読めず保存できない。§3） |
| `INTERNAL` | 500 | 想定外の内部例外 |
| `UNAVAILABLE` | 503 | この起動形態・状態では使えない（ヘッドレスでデスクトップの画面が要る操作・終了の開始後） |
| `UPSTREAM_TIMEOUT` | 504 | Start.gg など外部依存のタイムアウト |

### 2.7 操作ログ

- 書き込み（POST / PATCH / PUT / DELETE）は INFO で専用ロガーに記録する: 時刻・送信元 IP・メソッド・パス・主要パラメータ・結果。読み取りは記録しない。
- トークンは全端末で 1 つを共有するので、操作主体は IP 粒度まで。トークンはログに出さない（拒否のログもクエリを含めない）。
- 選手データを運ぶルート（`players/{p}`・`/fields`・`PUT /players`・`:load`・`players:delete`）は**ボディのキー名だけ**を記録し、値（タグ・本名・SNS）もエラー `message` も出さない。ZIP のアップロードは本文を読まず、バイト数だけ。ファイル名・フォルダのパス・選択の id はログに出さない。

### 2.8 重い操作の非同期受付（202）

- 重い操作（`POST /tournament`・`POST /game`・`POST /bracket:refresh` と、`analysis:refresh`・`tournament/{phases,stations,sets}:refresh`・サムネイル / リザルトの描画・ゲームアセットのジョブなど）は完了を待たず `202 Accepted` を返す。`revision` は未確定。
  ```json
  { "ok": true, "data": { "accepted": true, "operation": "tournament.load", "loadRevision": 4 } }
  ```
- 完了は WebSocket（§4）で `program_state_update` の revision 進行と state の進み具合（`tournamentInfo.load` / `analysis` / `set_selection` / `thumbnail.render` / `result_render` など）で観測する。ゲームアセットのジョブだけは `GET /assets/job` を `revision` で追う。
- `POST` / `DELETE /tournament` は `loadRevision` を返す。state の `tournamentInfo.load.revision` がそれに達してから `status` を自分の要求の結果として読む。
- 同種の要求が実行中なら `409 CONFLICT`（実行中フラグ。`threading.Lock` で保護）。例外: `tournament/sets:refresh` は別の要求を置き換える。`thumbnail:preview` / `result:preview` は実行中の要求を最後の 1 件にまとめる。
- 失敗は 202 の後に起きるのでレスポンスでは返せない。ログに残り、state の `error` と revision が進まないことで分かる。

### 2.9 冪等性

- 相対操作（`score:adjust`）は冪等でない。`Idempotency-Key` ヘッダを付けると、同じキーの再送は重複適用せず直前の結果を返す。キー無しは毎回適用する。保持はプロセス内の 256 件・60 秒（`_IdempotencyCache`）。
- 絶対値を書く操作（`score:reset`・`score:set`・`PATCH set`・`PATCH .../players/{p}`・`PATCH /display-options`・`:swap-teams` ほか）は冪等寄りで、キーは不要。

### 2.10 scoreboardNumber（パスの `{n}`）

- 有効値は `1` / `2` / `3`。API 層で検証し、範囲外は `400 INVALID_ARGUMENT`。3 ボードは起動時に core が作る。

  | n | 種類 | 書き手 | state |
  |---|------|--------|-------|
  | 1 | 1v1 | `AutonaActions` / `PlayerService` ほか | `score.1` |
  | 2 | チーム | 同上 | `score.2` |
  | 3 | クルーバトル | `CrewBattleService`（別の操作系） | `score.3` |

- 画面が見せているボードは `GET /health` の `activeBoard`（`GET /battle-format` の `board` と同じ）。裏のボードを操作しても配信は変わらないので、外部コントローラは先にこれを確認する。形式は `PUT /battle-format` で切り替える。
- **ボード 3 の扱い**: メンバー（`.../members/{m}`・`stock:adjust`）はボード 3 専用で、ボード 1 / 2 に投げると `400`。`:swap-teams`・`score:reset`・`teams/{t}/color`・`PATCH set`・`:clear` / `:reset-players` / `:reset-match` は同じルートがボード 3 でも動く。クルーに無い操作（`score:adjust`・`score:set`・`:apply`・`stats:refresh`・選手のルート・`:load-set` / `:pull-stream` / `:follow` / `:stop-follow`・`:reset-all`）は `400` で明示的に拒否する。
- **反映ゲート（held）**: デスクトップではボード 1 / 2 の `score.{n}` が「反映」まで保留される。書き込みはライブ state とアプリの画面には即座に映るが、OBS・WebSocket の delta・`out/` へは `POST /scoreboards/{n}:apply` まで公開されない。例外で即時公開: ボード 3、`tournamentInfo.*`、統計キー（`last_sets` など）、ゲーム切り替え（ロードの自動コミット）。**ヘッドレスは held にしない**（書き込みは即時公開）。正本は Design.md §3.2 の R-Sync-11。
- **チーム番号**: v1 の `{t}` は常に state 側のチーム（`score.{n}.team.{t}`）を指し、スワップ後も同じ列を指す。唯一の例外は `players/{p}:load` で、`{t}` はセット側のチームを指しスワップで反転する。

### 2.11 確認フラグ（confirm）

- 破壊的な操作は本文に `"confirm": true` が必須。無い・false なら `409 CONFIRMATION_REQUIRED` で、状態は変えない。対象は §5 で「confirm」と付けたルート（`:clear`・`:reset-all`・`:reset-players`・`:reset-match`・`players/{p}:clear`・`players:delete`・`thumbnail:reset`・`result:clear`・`access-token:regenerate`・`assets/packs:install`、`overwrite` が空でない `assets:importZip` / `assets:importFolder`、入力があるときの `result:loadStandings`）。
- `:load-set` / `:pull-stream` / `:follow` はボードを上書きするが confirm は要らない（誤爆対策は外部コントローラ側で行う）。配信中かどうかの自動ブロックは無い。

---

## 3. 前提条件（NOT_READY）

- 大会に依存する読み取り（`/sets`・`/sets/{id}`）は provider が無ければ空（`[]` / `null`）で返す。
- provider が要る操作（`bracket:refresh`・`analysis:refresh`・`tournament/*:refresh`・`:load-set` / `:pull-stream` / `:follow`・`stats:refresh` の取得）は provider が無ければ `409 NOT_READY`（`no tournament loaded`）。`:pull-stream` は設定 `twitch_username` が無くても `NOT_READY`。
- スコア操作・set 情報の設定は、大会が未ロードでも今の盤面に作用する。
- **`settings.json` を読んで変える操作**（配信チャンネル・サムネイル / リザルトの設定・設定ページの保存・トークンの再発行）は、起動時に読めず保存の時点でも開けないとき `409 NOT_READY`（何も保存しない）。次の書き込みで読み直す。

---

## 4. WebSocket 購読

新しい WebSocket チャネルは作らない。状態のフィードバックは既存の Socket.IO を購読する（Companion のボタンのフィードバックは `GET /state` + この購読）。

- 接続時・`program_state` 要求 → 全量 `{revision, state}`
- `program_state_update` → `{revision, first_revision, delta}`（旧名 `delta_index` も付く）。複数の revision が結合されうるので、区間 `[first_revision, revision]` が「前回の revision + 1」を含むかを検査し、含まなければ全量を再取得する。reconnect でも全量を取り直す。規約と間隔は Design.md §3.2.3
- ゲームの切り替えは、delta の後に全量（`program_state`、続いて `characters`）が届く。全量の revision は中身と一致する
- 値を運ばない通知（読み手は通知の**後**に始めた GET の答えだけを使う）: `scoreboard_pending`（未反映の編集。`GET .../pending`）・`scoreboard_follow`（追従。`GET .../follow`）・`battle_format`（`GET /battle-format`）・`games_changed`（`GET /games`・`GET /assets/packs`）
- 値を運ぶ通知: `status`（`{revision, jobs}`。アプリの実行中の仕事。接続直後に 1 回届く。`GET /status` と同じ）
- 書き込み可かは接続時に決まる（LAN はトークン付きで接続する）。トークンを再発行すると、古いトークンで入った接続の書き込みイベントは `401` の ack で断られる。接続と state の配信は続く。

---

## 5. エンドポイント一覧

以下は分類ごとの索引。引数の詳細・本文の例は `GET /api/v1/api-catalog`（`ApiCatalog.py`）と `src/AutonaApiV1.py` の各ハンドラの docstring。「区分」欄は既定（読み取り = `READ`、書き込み = `WRITE`）以外だけ書く。パスは `/api/v1` の後ろ。

### 5.1 状態・接続

| メソッド | パス | 区分 | 説明 |
|---------|------|------|------|
| GET | `/state` | | 全状態と revision |
| GET | `/health` | | 死活・版・revision・`currentGame`・`activeBoard`・`tournamentLoaded`・`outLastWriteUnix`・`previewPort` |
| GET | `/status` | | アプリの実行中の仕事（`status` 通知と同じ） |
| GET | `/api-catalog` | | 手動操作向けのエンドポイント表とエラーコード |
| GET | `/connection` | THIS_PC | Web サーバーの URL（LAN / ループバック）・ポート・`out/` の絶対パス |
| GET | `/access-token` | SHELL | アクセストークン |
| POST | `/access-token:regenerate` | SHELL | トークンを再発行（confirm。古いトークンは即無効） |
| GET | `/shell-session` | SHELL | シェル要求のチケット（共有ビューのページがデスクトップに頼み事をするため。HTTP ルートでない `/web/shell-request` の検査用） |

### 5.2 スコアボード

`{n}` は §2.10、`{t}` = 1 / 2。

| メソッド | パス | 区分 | 説明 |
|---------|------|------|------|
| GET | `/scoreboards/{n}` | | ボードの状態（`swapped` を含む） |
| GET | `/scoreboards/{n}/pending` | PLAYERDB | 反映前のライブ値・held か・未反映があるか |
| GET | `/scoreboards/{n}/follow` | | 追従している配信台 / 配信と取り直しの時計 |
| GET | `/scoreboards/{n}/post` | PLAYERDB | ボードの X 投稿文と足りないもの |
| GET | `/battle-format` | | 対戦形式（`singles` / `team` / `crew`）と表示するボード |
| PUT | `/battle-format` | | 対戦形式を切り替える |
| POST | `/scoreboards/{n}/teams/{t}/score:adjust` | | スコアを `delta` だけ増減（`Idempotency-Key` 対応） |
| POST | `/scoreboards/{n}/score:set` | | 両チームのスコアを絶対値で設定 |
| POST | `/scoreboards/{n}/score:reset` | | スコアをリセット（ボード 3 はストックを既定に戻す） |
| POST | `/scoreboards/{n}:swap-teams` | | チーム入れ替え（選手・スコア・LOSERS・チーム名・ロゴ。色は動かさない） |
| POST | `/scoreboards/{n}:apply` | | 保留中の編集を公開（反映） |
| POST | `/scoreboards/{n}/teams/{t}/color` | | チームカラー |
| PATCH | `/scoreboards/{n}/set` | | best of・phase・pool・match・人数・キャラ数・LOSERS・チーム名の部分更新 |
| PATCH | `/scoreboards/{n}/match` | | match バーを入力欄と同じ規則で書く（`""` で空にする） |
| PATCH | `/scoreboards/{n}/teams/{t}/players/{p}` | | 選手データの部分更新 |
| PATCH | `/scoreboards/{n}/teams/{t}/players/{p}/fields` | | 選手の欄を入力欄と同じ規則で書く |
| PATCH | `/scoreboards/{n}/teams/{t}/players/{p}/characters/{row}` | | 選手のキャラの行 |
| POST | `/scoreboards/{n}/teams/{t}/players/{p}:load` | | 選手 DB から読み込む（`{t}` はセット側。§2.10） |
| POST | `/scoreboards/{n}/teams/{t}/players/{p}:clear` | | 選手 1 人を空にする（confirm） |
| POST | `/scoreboards/{n}/teams/{t}/players:swap` | | チーム内の 2 人を入れ替える |
| PATCH | `/scoreboards/3/teams/{t}/members/{m}` | | クルーのメンバー（名前・ストック・出場中）。ボード 3 のみ |
| POST | `/scoreboards/3/teams/{t}/members/{m}/stock:adjust` | | メンバーのストックを増減。ボード 3 のみ |
| POST | `/scoreboards/{n}:clear` | | 盤面をクリア（confirm。人数・キャラ数を既定に戻す） |
| POST | `/scoreboards/{n}:reset-all` | | 全入力をリセット（confirm。人数・キャラ数は残す。ボード 3 は 400） |
| POST | `/scoreboards/{n}:reset-players` | | 選手をリセット（confirm） |
| POST | `/scoreboards/{n}:reset-match` | | match をリセット（confirm） |
| POST | `/scoreboards/{n}/stats:refresh` | | 統計の取得を依頼（ボード 1 向け。取得は非同期） |
| PATCH | `/display-options` | | 表示トグル（`display_options.show_*`） |
| POST | `/scoreboards/{n}:load-set` | | セットをボードに読み込む（取得は非同期） |
| POST | `/scoreboards/{n}:pull-stream` | | `twitch_username` のチャンネルの次のセットを読み込み追従する |
| POST | `/scoreboards/{n}:follow` | | `tournament/stations` の行（配信台 / 配信）を追従する |
| POST | `/scoreboards/{n}:stop-follow` | | 追従を止める（盤面は残す） |

### 5.3 大会・ゲーム・試合データ

| メソッド | パス | 区分 | 説明 |
|---------|------|------|------|
| GET | `/tournament` | | 大会の URL と設定 / 解除の進み具合 |
| POST | `/tournament` | | 大会を設定（202。`url` が空なら解除。読み込み済みの URL は `force` で再取得、`fetchMedia` でアイコン / バナーも取得） |
| DELETE | `/tournament` | | 大会を解除 |
| PATCH | `/tournament/info` | | 大会名・イベント名・場所・参加人数・開催日など |
| PUT / DELETE | `/tournament/media/{kind}` / `/tournament/media` | | 大会アイコン / バナーの取り込み / 既定に戻す |
| POST | `/tournament/media/fetch` | | 大会からアイコン / バナーを取得（実行中フラグ） |
| PATCH | `/colors/tournament` | | 大会カラー（`{"reset": true}` で既定に戻す。全 feature の同じ kind の上書きを消す） |
| PATCH | `/colors/{feature}` | | 機能別カラー（board / thumbnail / result / top8 / commentary / stats） |
| GET | `/tournament/{phases,sets,stations}` | | セット選択の行（最後に取得したもの。読みは取得しない） |
| POST | `/tournament/{phases,sets,stations}:refresh` | | 取得を始める（202。`sets` は別の要求を置き換える） |
| POST | `/tournament/sets:cancel` | | セット一覧の取得を止める |
| GET | `/analysis` | | この大会の試合一覧の行 |
| POST | `/analysis:refresh` | | 試合一覧の取得（202） |
| GET | `/games` / `/games/current` | | ゲーム一覧 / 現在のゲーム（`selected` で選択済みか） |
| POST | `/game` | | ゲームを選ぶ（202。`codename`・`modsActive`） |
| GET | `/characters` / `/variants` | | キャラ一覧 / バリアント一覧 |
| GET | `/countries` / `/countries/{code}/states` | | 国の一覧（カスタム国旗を含む）/ 国の地域 |
| GET | `/sets` / `/sets/{setId}` | | start.gg への同期呼び出しでセット一覧 / 詳細（`setId` は start.gg の番号か Hivee の uuid） |
| GET | `/match-names` / `/match-names/candidates?board={n}` | | match・phase の用語 / コンボが出す候補 |
| POST | `/bracket:refresh` | | Top 8 を start.gg から取り込み直す（202） |

### 5.4 表示・デザイン・実況・配信

| メソッド | パス | 区分 | 説明 |
|---------|------|------|------|
| GET | `/layouts?kind=` | | デザイン一覧（`scoreboard` / `designer` / `commentary` / `top8` / `result` / `thumbnail`）。オーバーレイの項目は `settings.json` が `contract` を宣言していれば `contract` を持ち、フォルダ名の規約で一覧に出したが manifest に問題があった項目は `warnings: [{reason, detail}]`（`unlisted` と同じコード。`bad_settings` / `bad_block` / `unknown_kind` と、`entry` がファイル名でない `bad_entry`）を持つ（無ければキーごと無い）。`data` の隣に `unlisted`（ADR-008 D3）: 一覧に出さなかったフォルダと理由の配列 `{folder, kind, reason, detail}`。`kind` は分かれば種類・分からなければ `null`（どの `kind=` でも返る）。`reason` は `no_entry`（入口のファイルが無い）/ `bad_settings`（`settings.json` が JSON のオブジェクトでない）/ `bad_block`（`autona` ブロックがオブジェクトでない）/ `unknown_kind` / `unknown_contract` / `bad_template`（テンプレートの必須項目の不備）/ `bundled_id`（同梱テンプレートの id と同じ名前）/ `no_manifest`（ブロックもフォルダ名の接頭辞も無い。`settings.json` か `.html` があるフォルダだけ）/ `bad_entry`（`warnings` だけ）で、追加だけ。`detail` は英語の短い説明（ファイル名・受けなかった値・JSON のエラー。パスは含めない。200 文字まで）か `null`。予約フォルダ・Designer のプロジェクト（`custom/`）・作成中の作業フォルダ・同梱の非デザインのフォルダ・旧版で同梱して削除したフォルダ（自動更新は消さないので手元に残る）は出さない |
| GET / PUT | `/layouts/selected/{kind}` | | 選択中のデザイン（`commentary` / `top8` / `board`）の取得 / 選択 |
| GET | `/layouts/selected/board/obs-path` | PLAYERDB | OBS に指定するファイルの絶対パス |
| POST | `/layouts/selected/board:reveal` | THIS_PC | Board の OBS ファイルをエクスプローラで開く |
| POST | `/layouts:create` | SHELL | 新しいデザインを作る（ADR-008 D2。`kind` = `scoreboard` / `commentary` / `top8`・`name`（フォルダ名）・`displayName`（任意）。`layout/<接頭辞><name>/` を作成キットから作り、201 で一覧の項目を返す。拒否は `reason`: 400 `bad_kind` / `bad_name` / `bad_display_name`、409 `reserved` / `exists`、500 `kit_missing` / `write_failed` / `not_listed`） |
| GET | `/layouts/kit/{kind}/ai-prompt` | SHELL | 作成キットの `AI_PROMPT.md`（`{kind, text}`。デザイン一覧の「AI 用の説明をコピー」） |
| POST | `/layouts:reveal` | SHELL | 一覧にあるデザイン（`id`）のフォルダをエクスプローラで開く（無ければ 404） |
| GET | `/commentary` / `/commentary/options` | | 実況の状態 / 役割・アイコン・スロットの候補 |
| PATCH | `/commentary/{slot}` | | 実況者の欄（5 人目の表示は 409） |
| POST | `/commentary/reorder` | | 実況者の並べ替え |
| POST | `/commentary/{slot}/player` | | 選手 DB から実況者を読み込む |
| PATCH / POST | `/top8`・`/top8/players/{slot}`・`/top8/scores` | | Top 8 の表示ラベル / 選手 / スコアの反映（`DELETE /top8/scores` は全リセット） |
| GET | `/stats/overlays` | | 統計オーバーレイの種類・選択中・Companion マクロ |
| PUT | `/stats/overlays/selected` | | 統計オーバーレイの選択 |
| GET | `/stream/channels` / `/stream/thumbnail/options` | | 配信チャンネル / 配信サムネのオプション |
| PUT | `/stream` | | 配信先 URL・タイトル |
| POST / PUT / DELETE | `/stream/channels`・`/stream/channels/{index}` | | 配信チャンネルの追加・変更・削除 |
| PUT / DELETE | `/stream/org/{kind}` / `/stream/org` | | 配信団体の画像 / 全て削除 |
| PUT | `/stream/thumbnail/options` | | 配信サムネのオプション |
| POST | `/stream/thumbnail` | | 配信サムネを生成（実行中フラグ） |

### 5.5 画像生成（サムネイル・リザルト）

描画はデスクトップのレンダラーで行い、ヘッドレスでは `:preview` / `:generate` が `503 UNAVAILABLE`（設定の読み書きと `GET .../preview` は答える）。テンプレートの描画が例外で失敗したとき（契約 2）は `thumbnail.render` / `result_render` の `error` が `template_error` になり、`errors` にページが言ったエラー（`{kind, message, source, line, column}` の配列。20 件まで）が入る（ほかの結果では `[]`）。

| メソッド | パス | 区分 | 説明 |
|---------|------|------|------|
| GET | `/thumbnail` | | 試合サムネイルの設定・テンプレート・描画できるか |
| PATCH | `/thumbnail` | | 設定の部分更新 |
| POST | `/thumbnail/background` | | 背景画像の取り込み |
| POST | `/thumbnail:reset` | | 既定に戻す（confirm） |
| POST | `/thumbnail:preview` / `:generate` | | 描画（202。`:generate` は `board` = 1 / 2 を取れる） |
| GET | `/thumbnail/preview` | | 最新のプレビュー PNG |
| GET | `/thumbnail/payload` | PLAYERDB | 選んだテンプレートの payload（ページのブラウザでのプレビュー用。ADR-008 D7 R2）: `{templateId, folder, entry, contract, size, payload: {data, config, template}, omitted}`。未反映の編集を含む。描画しないのでヘッドレスでも答える。プレビュー用のサーバーが出さない画像（アップロードした背景など）は `""` にしてキーを `omitted` に入れる。テンプレートが読めなければ 404 |
| GET | `/result` | | リザルトのレイアウト・行・描画と取り込みの状態 |
| GET | `/result/post` | PLAYERDB | X の投稿文 |
| PATCH | `/result` | | レイアウトの選択 |
| PATCH | `/result/players/{n}` / `/result/teams/{n}` | | 単独 / 団体の行 |
| POST | `/result:reorder` | | 行の並べ替え |
| POST | `/result:clear` | | 今の種類の行を全部クリア（confirm） |
| POST | `/result:loadStandings` | | 大会結果の読み込み（入力があれば confirm。202） |
| POST | `/result:saveToDb` | | 選手を DB に保存 |
| POST | `/result:preview` / `:generate` | | 描画（202） |
| GET | `/result/preview` | | 最新のプレビュー PNG |

### 5.6 選手 DB

| メソッド | パス | 区分 | 説明 |
|---------|------|------|------|
| GET | `/players` | PLAYERDB | 選手 DB の全件の行 |
| GET | `/players/entrants` | PLAYERDB | 大会の参加者の行 |
| PUT | `/players` | | 選手 DB に保存（mains のゲームが違う・新しい保存に負けたら 409 `CONFLICT`） |
| POST | `/players:delete` | | 選手を削除（confirm。`key` は一覧の DB キー） |

### 5.7 設定・ゲームアセット

| メソッド | パス | 区分 | 説明 |
|---------|------|------|------|
| GET | `/settings` | | 設定ページの設定とホットキー（許可リスト。トークンは返さない） |
| PATCH | `/settings/general` | | 一般設定 |
| PATCH | `/settings/{network,language,hotkeys}` | THIS_PC | 待ち受け・言語・ホットキー（ネットワークと言語は再起動で反映。ホットキーの重複は 409） |
| GET | `/assets/packs` | THIS_PC | ゲームアセットのパックの一覧 |
| GET | `/assets/job` | SHELL | ゲームアセットのジョブの状態 |
| POST / DELETE | `/assets/uploads` / `/assets/uploads/{id}` | SHELL | ZIP のアップロード（本文そのまま） / 破棄 |
| POST | `/assets:importZip`・`/assets:importFolder` | SHELL | 取り込みのジョブを始める（202。`overwrite` があれば confirm） |
| POST | `/assets/packs:install` | SHELL | 索引のパックを落として入れる（202。常に confirm。索引が無ければ `NOT_READY`） |
| POST | `/assets/job:cancel` | SHELL | ジョブの中止（`revision` を渡す） |
| POST | `/assets/games-folder:reveal` | SHELL | ゲームのフォルダを開く |

### 5.8 アーカイブ切り抜き

ADR-006。ページ（`web/src/ArchivePage/`）だけが呼ぶので API 一覧には載せない。読み取りは `THIS_PC`、書き込みは `SHELL`（ヘッドレス・素のブラウザはジョブを作れない）。動画・フレームは実行中のジョブには返さず（404）、プレビューできない形式は 415（`reason: not_previewable`）。本文の上限は 2 MB。ジョブの状態は state に載せず、ページが 1 秒ごとに読む。

| メソッド | パス | 説明 |
|---------|------|------|
| GET / PATCH | `/archive/status` / `/archive/settings` | ワーカーの有無・版・作業フォルダ・実行中のジョブ / 作業フォルダ・サムネイル背景の設定 |
| GET / POST | `/archive/jobs` | ジョブの一覧（新しい順）/ 作成（201。400 → 503 → 409 の順に断る） |
| GET | `/archive/jobs/{id}` | 進捗と `out/sets.json` |
| POST | `/archive/jobs/{id}:cancel` / `:resume` / `:reanalyze` | 中止 / 再開 / 結果データを付けた区切り直し |
| GET | `/archive/jobs/{id}/video` / `/frames/{no}` | プレビュー動画（`Range` 対応）/ セットの背景フレーム |
| GET / PUT | `/archive/jobs/{id}/review` | レビューの編集（確定・IN / OUT・割り当て・削除） |
| POST | `/archive/jobs/{id}:export` | 確定分のクリップを書き出す |
| POST | `/archive/results:prepare` | 結果データを用意（start.gg / 試合の記録 / JSON。最大 150 秒かかる） |
| GET | `/archive/match-logs` / `/archive/characters` | 試合の記録の日付 / キャラのスキンとアイコン |
| GET / PUT / DELETE | `/archive/templates`・`/archive/templates/{id}` | タイトルのテンプレート |
| POST | `/archive/templates/{id}:duplicate` | テンプレートの複製 |
| POST | `/archive/jobs/{id}/titles:preview` / `/thumbs:generate` | タイトルのプレビュー / セットのサムネイル |
| GET | `/archive/jobs/{id}/thumbs/{no}` | セットのサムネイル |
| GET | `/archive/worker` | ワーカーの導入済みの版・配布中の最新・導入の進み具合 |
| POST | `/archive/worker:install` / `:cancel` | ワーカーの導入（202）/ 中止 |

---

## 6. 外部連携で気をつけること

- **ボード操作の順序**: 操作はボードホストの 1 本のロックで直列に走り、同時操作は後勝ち（検出も拒否もしない）。ボード 1 / 2 の書き込みを OBS に出すには、デスクトップでは `:apply` が要る（§2.10）。
- **非同期の完了確認**: 202 の操作は HTTP が返った時点では終わっていない。WebSocket の revision と state の進み具合（`load` / `render` / `analysis` など）で確かめる（§2.8）。
- **再送**: `score:adjust` は `Idempotency-Key` を付ける。物理ボタンの連打は破壊的操作の confirm（§2.11）と合わせて吸収する。
- **`revision` を設定の確認に使わない**（§2.4）。
- **LAN 越し**: 書き込み・`PLAYERDB` の読み取りはトークン必須（§2.2）。トークンを再発行したら、LAN の接続は新しいトークンで接続し直す。
- **大会の URL**: Hivee の大会 URL はトークンを `token=***` に伏せた形で読み取りに出る。送り返しは、保存した URL を伏せたものと一致するときだけ受け付ける。
- Companion / Stream Deck のボタン設計に API を引きずらない。外部コントローラは薄いクライアントに徹し、状態ロジックを持たせない。手動確認の項目は [../development/manual-test-checklist.md](../development/manual-test-checklist.md) の「外部制御 API」節。
