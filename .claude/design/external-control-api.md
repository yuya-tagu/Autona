# External Control API (`/api/v1/`) I/F 仕様

> **ステータス: 実装済み。** 本書は ADR-001 で確定した方針に基づく I/F 仕様であり、
> 実装（[../../src/AutonaApiV1.py](../../src/AutonaApiV1.py)）の説明文書として扱う。実装と本書が乖離した場合は**実装を正**とし、本書を実装に追従させる。
> 既存の HTTP/WS 制御面の事実は変更しない（本 API は別名前空間として**追加**済み）。
> アプリ内導線: 「Help & Settings」→「API設定」→ リファレンス・テスター（React の `/web/api`。[../../web/src/GuidePages/ApiPage.tsx](../../web/src/GuidePages/ApiPage.tsx)。ADR-005 S-final SF-4a-2 で Qt のウィンドウから共有ビューのページになった。ブラウザでも開けるが、トークンの表示と再発行はアプリの中だけ）。

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
  （D4・不変条件。ADR-005 で Qt Signal の中継は無くなった）。StateManager をバイパスしない。ボードの操作は
  ボードホストの 1 本のロックで 1 つずつ走り（両シェル同じ。ADR-005 S4ad まではデスクトップのボードホストが
  GUI スレッドに直列化していた。S3c）、本 API もそれを通る。

### 1.1 初回スコープ（D6）
- 読み取り（§5）/ 安全操作（§6）/ 危険操作（§7・ガード必須）を**初回から一括提供**。
- 初回除外: `open-set`（GUI ウィンドウ起動。ADR-005 S4ab-2 からは SCORE ページのダイアログ。v1 には今も無い）/ X・Bluesky 投稿（外部送信・機密）/ 選手 DB 全量（個人情報）。crew 系（当時は board3 運用未確定）は ADR-005 S4w で追加した（§2.12・§6.38・§6.39）。

---

## 2. 共通規約

### 2.1 ベース・バージョニング
- ベースパス: **`/api/v1`**。破壊的変更時は `/api/v2` を新設し、`/api/v1` は一定期間維持。
- 既存ルート（`/score`・`/scoreboard<N>-...` 等）とは**パス空間が衝突しない**（`/api/` 前缀）。既存契約は不変（D3）。
- **正本は `/api/v1`。旧ルートは deprecated（v2.0 で削除予定。それまで挙動不変）**（ADR-001 Addendum 2026-10-21）。

### 2.2 認証・ネットワーク（D5 → ADR-005 S3a で改定・2026-10-25）
- バインドは既定 `127.0.0.1`。設定の「LAN 上の他のデバイスからのアクセスを許可」で `0.0.0.0`。
- **LAN の他端末からの書き込み（POST / PATCH）はアクセストークン必須**。読み取り（GET）はトークン不要のまま。
  ただし選手 DB の読み取り（legacy `/playerdb`、v1 の `GET /players`・`GET /players/entrants`。本名を含む。ADR-005 S4m）と、DB の X ハンドルを含む大会リザルトの投稿文（`GET /result/post`。ADR-005 S4q）と、ユーザー名を含む OBS 用のファイルのパス（`GET /layouts/selected/board/obs-path`。ADR-005 S4s）と、操作者がまだ公開していない編集（`GET /scoreboards/{n}/pending`。本名を含みうる。ADR-005 S4u）と、ボードの X 投稿文（`GET /scoreboards/{n}/post`。反映前の値と選手の X ハンドルを含む。ADR-005 S4x）は書き込みと同じ扱い（`PLAYERDB` 区分）。
  同じ PC（同じ PC の Companion・ASS・curl）はトークン不要（今と同じ）。「同じ PC」は接続元がループバックか、この PC の LAN IP のどれか
  （LAN IP の URL を同じ PC で使っていても通る）。ただし転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP`）が付いた要求は、
  ローカルのリバースプロキシ・トンネルの中継とみなし、他の端末と同じ扱い（トークン必須）
  - トークンは `user_data/settings.json` の `general.webserver_token`（初回起動時に生成。アプリの API 設定のページ（`/web/api`）で表示・コピー・再発行）
  - 渡し方（どれか 1 つ）: `X-Autona-Token: <token>` / `Authorization: Bearer <token>` / クエリ `?token=<token>`（すでにクエリがあれば `&token=<token>`）
  - 無い・誤り → `401 UNAUTHENTICATED`（`WWW-Authenticate: Bearer realm="Autona"` 付き）
- **この PC のウィンドウを開く操作と、この PC の設定はこの PC からの要求だけ**（`THIS_PC` 区分。§6.35 のエクスプローラ（ADR-005 S4s）と、§6.45〜6.47 の待ち受け・言語・ホットキーの設定（ADR-005 S4ae）と、§5.25 の接続情報（ADR-005 S-final SF-3a）と、§5.27 のゲームアセットのパックの一覧（ADR-005 S-final SF-3b。ジョブの状態 §5.28 は SF-4b-1 で `SHELL` に移した））。トークンなしで通れる要求（下の DNS rebinding・CSRF の判定を含む）だけを通し、LAN の他端末・転送ヘッダ付きは**トークンの有無によらず** `403 FORBIDDEN`（トークンで開くものではないので 401 にしない）
  - `THIS_PC` は秘密を守らない: このサーバーと同じオリジンのページ（`layout/` のレイアウト。OBS のブラウザソース・直接開いたブラウザ）の要求もトークンなしで通るので、第三者製のレイアウトも読める。そのため**アクセストークンは `THIS_PC` のルートでは返さない**（下の `SHELL`）
  - 同じ理由で、**ゲームアセットの書き込みは `THIS_PC` にしない**（同じオリジンのレイアウトに、導入済みのゲームの置き換え・GB 単位のダウンロード・ディスクを埋めるアップロードをさせない）。ZIP のアップロードと破棄・ZIP の取り込み・インストール・キャンセル・ジョブの状態・ゲームのフォルダを開く操作は `SHELL`（下。ADR-005 S-final SF-4b-1。SF-3b では v1 に出していなかった）。**フォルダの取り込みは v1 に無い**（ページはこの PC のフォルダを名指せない。名指せるとこの PC のフォルダのコピーと有無の探査になる。SF-5a のフォルダ選択で戻す）
- **アプリ自身のウィンドウ（デスクトップの共有ビューの中の、本体のサーバーのオリジンの文書）からの要求だけ**（`SHELL` 区分。2026-11-14・ADR-005 S-final SF-4a-1）: §5.29 のアクセストークンの読み取りと §7 の `POST /access-token:regenerate`、ゲームアセットのジョブの状態（§5.28）・ZIP のアップロードと破棄（§6.48 / §6.49）・ジョブのキャンセル（§6.50）・ゲームのフォルダを開く（§6.51）・ZIP の取り込みとインストール（§7。ADR-005 S-final SF-4b-1）・シェル要求のチケット（§5.30。ADR-005 S-final SF-5a-1）。通すのは次の 3 つを**すべて**満たす要求だけで、ほかは**トークンの有無によらず** `403 FORBIDDEN`（`only the Autona app's own window may ask this`。1 つの答え。ログには何が欠けたかだけを出し、ヘッダの値は出さない）
  - `THIS_PC` と同じ、トークンなしで通れるこの PC の要求（トークンでは開かない。LAN の他端末は秘密があっても断る）
  - `Sec-Fetch-Site` がちょうど `same-origin`（`none`・無い・`same-site`・`cross-site` は断る。共有ビューの fetch は必ず送る）
  - `X-Autona-Shell` ヘッダがシェルの秘密と一致（定数時間の比較。空・128 文字超・ASCII 以外・前後の空白・大文字小文字の違いは不一致）。秘密はデスクトップのシェルが最初に必要になったときにプロセスで 1 回作り、メモリにだけ持つ（`settings.json` に入れない・ログに出さない）。付けるのは**共有ビューのページ自身のリクエストインターセプタ**（プロファイルのではない: Designer のビューが同じ既定プロファイルを使う）で、メインフレームが Web UI（本体のサーバーの `/web` の配下で `view=designer` でない）を出している間に、そのページの中の本体のサーバーのオリジン（`127.0.0.1:<待ち受けポート>`）の文書が出す、本体の `/api/v1/` への fetch / XHR に付ける。その文書はメインフレームと同じオリジンのフレームで、about:blank・srcdoc・blob: のフレームは作った文書のオリジンを継ぐので含まれる（だから Web UI はそこへ第三者の HTML を sandbox なしで入れない。sandbox のフレームは opaque origin で付かない）。Web UI のスクリプトが出す fetch / XHR には付き、`<script>`・画像の読み込みとナビゲーションには付かない。別オリジンのフレーム（プレビュー）・ほかのビュー・ほかのページの要求には付かない。付いた要求がほかへリダイレクトされたら止め、付けてはいけない要求が秘密を持っているか確かめられなければその要求を止める。ヘッドレスは秘密を作らないので、`SHELL` のルートは常に `403`
  - プレビュー用のリスナーは `SHELL` を出さない（READ だけ。下の項）。Socket.IO のイベントに `SHELL` は無い
  - 判定は `src/core/WebAccess.ShellRefusalReason`、付ける側は `src/AutonaWebPanelHost.ShellHeaderWanted`
  - 秘密とは別に、**シェル要求のチケット**（§5.30。ADR-005 S-final SF-5a-1）がある。秘密はシェルが共有ビューの fetch に付けるもの（ページの JS からは読めない）、チケットは `GET /shell-session` を読めたページだけが URL に入れられるもの。共有ビューのページがデスクトップ自身に頼むこと（最前面表示・更新の確認・決まった外部アドレス）は、クリックの中で開く `/web/shell-request?verb=…&request=…&ticket=…` の新しいウィンドウで、共有ビューはそれを開かずに検査して実行する（`src/AutonaWebPanelHost.py` の `ParseShellRequest`）。これは HTTP のルートではない（サーバーには届かない）
- **DNS rebinding 対策**（同じ PC にも適用）: トークンなしの書き込みは、`Host` がこのサーバーの名前
  （`127.0.0.1|localhost|[::1]|<この PC の LAN IP>:<待ち受けポート>`）のときだけ。それ以外の `Host` は `401 UNAUTHENTICATED`
  （`http://<PC 名>:5107` のようなホスト名も該当）
- **ブラウザ経由の CSRF 対策**（同じ PC にも適用）: トークンなしの書き込みで、`Sec-Fetch-Site` が付いていて `same-origin` / `none` 以外
  （`cross-site`・`same-site`）、または自分のオリジン（上と同じ名前とポート）以外の `Origin`（`null` を含む）が付いていたら
  `403 FORBIDDEN`。Companion・ASS・curl・`requests` はこれらのヘッダを付けないので影響しない。正しいトークン付きの要求は Host・CSRF の判定も免除
- 判定の正本は `src/core/WebAccess.py`（ルート・イベントの分類表 `ROUTE_ACCESS` / `EVENT_ACCESS`。分類のない endpoint は書き込み扱い）。
  legacy ルートも同じ判定を通り、拒否時だけ v1 と同じエラーエンベロープを返す（成功時の `"OK"` 等は不変）
- CORS（`Access-Control-Allow-Origin: *`）は残る。読み取りは別オリジンのページからも読める（OBS の file:// オーバーレイのため）
- **プレビュー用のリスナー**（2026-11-14・ADR-005 S-final SF-8a）: 同じバインド先の別のポート（OS が選ぶ。`GET /health` の `previewPort`、§5.1）で、ページのレイアウトのプレビューだけが読む。出すのはレイアウトが読むもの（`layout/`・`assets/`・`user_data/` の画像・`out/program_state.json`・v1 の READ・Socket.IO の読み取り）だけで、`WRITE` / `PLAYERDB` / `THIS_PC` / `SHELL` / `DESIGNER`・`/web`・legacy のルート・`out/` のほかのファイルは**トークンがあっても、この PC からでも** `403 FORBIDDEN`（Socket.IO は接続を受けて state を配り、書き込みイベントは同じ ack で断る。信頼済みの room に入れない）。判定は Authorize に入る前（そのポートのページを「自分のオリジン」にしないため）。そのポートのページ（オリジン `http://<ホスト>:<previewPort>`）からこのサーバー自身のポートへの書き込みも、自分のオリジンではない（`Sec-Fetch-Site: same-site`）ので上の CSRF 対策で `403`
- **別のページのフレームには出さない**（2026-11-14・SF-8a のレビュー）: このサーバー自身のポート（プレビュー用のリスナーではない方）は、`Sec-Fetch-Dest` がフレーム（`iframe` / `frame` / `embed` / `object` / `fencedframe`）で `Sec-Fetch-Site` が `same-origin` / `none` 以外（`same-site`・`cross-site`）の要求を、何を頼んだかによらず（`layout/`・`/web`・静的ファイル・v1・legacy）**トークンがあっても** `403 FORBIDDEN` で断る（判定は Authorize の前。拒否の応答自身は `Content-Security-Policy: sandbox; frame-ancestors 'none'`）。応答はすべて `Content-Security-Policy: frame-ancestors 'self'` を持つ。プレビューのレイアウトが自分のフレームや作ったフレームをこのポートへ送り、このサーバーのページとして動くのを止めるため。このサーバーのページが自分のページを枠に入れる（`same-origin`）・直接開く（OBS のブラウザソース・リンク。`Sec-Fetch-Dest: document`）・ヘッダを付けないクライアントは影響しない。`file://` などほかのページがこのサーバーのページを iframe に入れる使い方は出なくなる。規則は `src/core/WebAccess.FramedElsewhere`
- ⚠️ 上の CSRF 対策とこのフレームの判定は、`Sec-Fetch-*` を送るブラウザが前提。送らない古いブラウザでは、ほかのページからの no-cors の GET（legacy の GET の書き込み）がこの PC の要求として通りうる
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
  - ⚠️ **`revision` は `StateManager` の変更のみを追う**。`SettingsManager` のみに書く操作の
    設定値変更は `revision` に反映されない場合がある。クライアントは設定系の反映確認を `revision` だけに
    依存しない（**SHOULD**）。`POST /tournament` の URL は ADR-005 S4g レビュー（M1）から state の
    `tournamentInfo.url` にも出る（§2.4a・§5.7）。
- `data`: 読み取り系の本体。操作系では省略可。

### 2.4a 状態の二重書き込み（SettingsManager 併用・許容）
- D4 の不変条件「状態変更は `StateManager` 単一経路」は **`score.*` 等のランタイム状態**に適用される。
- 一部の Action は **`SettingsManager` にも書く**ことを**許容する**（PdM 確認済）:
  - 大会の設定・解除（`load_tournament` / `set_tournament` / `clear_tournament`）→ `core/TournamentService` が
    `SettingsManager.Set("TOURNAMENT_URL")` と、その写しの `tournamentInfo.url`（state。ADR-005 S4g レビュー M1）を書く
    （[`AutonaWebOperations`](../../src/AutonaWebOperations.py)）
- 本 API はこれらも**既存 Action へ委譲するだけ**（新たに Settings/State を直接書かない）。
  「StateManager をバイパスして state を直接書かない」原則は維持される。

### 2.5 エラーレスポンス・エンベロープ
失敗時（**MUST**）:

```json
{ "ok": false, "error": { "code": "INVALID_ARGUMENT", "message": "team must be 1 or 2" } }
```

- HTTP ステータスと `error.code` を**両方**返す。`message` は人間可読（i18n しない・ログ/デバッグ用）。
- 既存ルートの素朴な `"OK"`/文字列契約は**変えない**（新 API のみ構造化）。
- `error.reason`（任意。2026-11-14・ADR-005 S-final SF-4b-1）: `code` より細かい理由のコード（小文字のスネークケース）。今はゲームアセットのルート（§6.48〜§6.51・§7）だけが付ける: core（`AssetPackService`）の理由（`too_large` / `disk_full` / `busy` / `unknown_upload` / `index_unavailable` ほか）をそのまま入れ、`message` は理由ごとの固定の英文（`AutonaApiV1._ASSET_REFUSALS`）にする。core の文はこの PC のパスやアーカイブのエントリの名前を含みうるので、答えずにログにだけ出す（§2.7）。理由の無い拒否（引数の型など）とほかのルートは付けない。クライアントは無いことを想定して読む

```json
{ "ok": false, "error": { "code": "NOT_READY", "message": "not enough free space on this PC", "reason": "disk_full" } }
```

### 2.6 エラーコード一覧

| code | HTTP | 意味・用途 |
|------|------|-----------|
| `INVALID_ARGUMENT` | 400 | パラメータ不正（範囲外の team/scoreboard、型不一致 等） |
| `UNAUTHENTICATED` | 401 | 書き込みにトークンが要るのに、無い・誤り（LAN の他端末・転送ヘッダ付きの中継・このサーバーの名前以外の `Host` 宛て。§2.2。ADR-005 S3a） |
| `FORBIDDEN` | 403 | トークンなしの書き込みが、別サイト・同一サイトの別オリジン・別オリジンのブラウザページから来た（§2.2 CSRF 対策）。`THIS_PC` / `SHELL` のルートを LAN・転送ヘッダ付き・（`SHELL` は）アプリ自身のウィンドウ以外から頼んだ（トークンがあっても。§2.2） |
| `NOT_FOUND` | 404 | 存在しないリソース（未知の set ID 等） |
| `CONFIRMATION_REQUIRED` | 409 | 破壊的操作で `confirm:true` が無い（§7.2） |
| `CONFLICT` | 409 | 重い操作が実行中（多重起動ガード・§2.8）。選手 DB への保存（§6.26）では、mains のゲームが今のゲームと違う / もっと新しい保存に負けた。ゲームアセットのジョブ（§7）は別のジョブが走っている（`reason: "busy"`）。ZIP のアップロード（§6.48）は届いている最中に新しいアップロードに置き換えられた（`reason: "superseded"`） |
| `NOT_READY` | 409 | 前提未充足（大会/試合未ロードで provider 不在 等・§3）。ゲームアセット: ZIP のアップロード（§6.48）でこの PC の空き容量が足りない（`reason: "disk_full"`）・インストール（§7）で索引が読み込まれていない（`index_unavailable`） |
| `UPSTREAM_TIMEOUT` | 504 | Start.gg 等の外部依存がタイムアウト（agent-pitfalls #11） |
| `PAYLOAD_TOO_LARGE` | 413 | 本文が大きすぎる。上限を持つのは画像の取り込み（配信団体 §6.17・大会のアイコン / バナー §6.21。ADR-005 S4g）とゲームアセットの ZIP のアップロード（下）だけ。画像: `Content-Length` が上限を超えると本文を解析せずに読み捨ててから断り（読まずに返しても werkzeug の開発サーバーが答えの後に、次のデータが 10 ms 以内に届き続ける限り最大 10 GB まで読み捨てるので、ふつうに送るクライアントには 413 が届く。送信の極端に遅いクライアントには接続のリセットが届く余地がある。ルートは最後まで読んでから断る）、data URL の base64 / デコード後の大きさでも断る（2026-10-31 追加・ADR-005 S4e）。ZIP のアップロード（§6.48。ADR-005 S-final SF-4b-1）: 8 GiB（`MAX_UPLOAD_BYTES`）で、`Content-Length` が上限を超えるとルートは本文を読まずに断る（`reason: "too_large"`）。werkzeug の開発サーバーは答えた後、本文が届き続けるあいだ読み捨てる（最大 10 GB。ディスクには書かない。その間は要求のスレッドを 1 本占有する）ので、クライアントには 413 が届く。ページが送る前に大きさを確かめるのは無駄な転送を省くため。空き容量の不足は `413` ではなく `409 NOT_READY`（`reason: "disk_full"`）。ほかのルートに上限は無い（アプリ全体の `MAX_CONTENT_LENGTH` は設定しない。アップロードは要求ごとの `request.max_content_length`） |
| `UNAVAILABLE` | 503 | この起動形態では使えない操作。ヘッドレス起動（`main.py --headless`）で、デスクトップの画面が要るシェル操作だけ（ADR-005 S3e-9）: legacy / Socket.IO のセット選択ウィンドウを開く操作（`scoreboard<n>-open-set`・`open_set`）とサムネイル（`scoreboard<n>-get-thumbnail-png|jpg`）、v1 の試合サムネイルの描画（`POST /thumbnail:preview` / `:generate`。ADR-005 S4o。設定の読み書きと `GET /thumbnail/preview` は答える）。message は `not available in headless`。デスクトップでこれらに答えるのはシェルのアダプタ `AutonaShellOperations`（ADR-005 S4y でスコアボード dock から移した）: legacy のサムネイルはボードを構築しない（`ThumbnailService.DrawBoard` を同期で呼ぶ。未構築のボードを state のまま描き、シード・反映待ち・取り直しの停止を起こさない。クルーボードは 500 のまま、ボード 3 も構築しなくなった）。**ADR-005 S4ab-2 から、セット選択を開く操作は Qt の窓ではなくページのセット選択のダイアログを開く**（Qt の窓と退路の設定 `general.qt_scoreboard_panel` は S4ac-3 で削除。それまでの退路の Qt モードでは窓を開き、未構築のボードを先に構築していた）: メインウィンドウを前に出して（最小化からも戻す。外部からの依頼では Windows がタスクバーの点滅で止めることがある）SCORE に切り替え、ページはボード 1 / 2 で大会があるときだけダイアログを開く（無ければページを見せるだけ）。ボードは構築しない（ダイアログからの読み込みは v1 の `:load-set` が準備する）。`n` が今の形式のボードでないとき（1v1 での `/scoreboard2-open-set` など）は何も開かずログだけ。どちらでも答えは今までどおり 200 `OK`（Socket.IO は `open_set` に `OK`）で、ダイアログが開いたかは答えに出ない（窓のころから出なかった）。クルーボード（`/scoreboard3-open-set`）は 500 のまま。メインウィンドウができる前の依頼は何も開かずログだけ（S4ac-3。それまでは Qt の窓を開いた）。再試行しても変わらない。legacy ルートも同じ本文の 503 を返し、Socket.IO はイベントの ack で返す。セット読込（`:load-set`・legacy `load-set`・Socket.IO `load_set`）・配信中のセットの取り込み（`:pull-stream`・legacy `pull-stream`・Socket.IO `pull_stream`）・legacy `POST /score`（Socket.IO `score`）も ADR-005 S3e-7 から core の操作（`SetFollowService`）で、どちらの起動形態でも動く。クルーボード（board 3）の操作と実況の更新・タグからの実況者の読込は ADR-005 S3e-2 から core の操作で、どちらの起動形態でも動く（それまでは legacy / Socket.IO でもこれだった）。Top 8 の取り込み（`bracket:refresh`・legacy `/update-bracket`・Socket.IO `update_bracket`）も ADR-005 S3e-8 から core の操作（`Top8Service.RefreshFromProvider`）で、どちらの起動形態でも動く。選手の編集（`PATCH .../players/{p}`・legacy `update-team`・Socket.IO `update_team`）とタグからの選手の読込（legacy `load-player-from-tag`・Socket.IO `load_player_from_tag`）も ADR-005 S3e-4 から core の操作（`PlayerService.ApplyData`）で、どちらの起動形態でも、state にあるスロットに対して動く。統計（`stats:refresh`・legacy `/scoreboard<n>-stats-*`・Socket.IO `stats_*`）も ADR-005 S3e-5 から core の操作（`StatsService`）で、どちらの起動形態でも動く。ボード 1 / 2 の人数 / キャラ数（`PATCH set` の `players` / `characters`・legacy `scoreboard<n>-set`・Socket.IO、`:clear` の人数・キャラ数のリセット）も ADR-005 S3e-6 から core の操作（`AutonaActions.SetRoster`）で、どちらの起動形態でも動く（それまではヘッドレスでこれだった）。ボード 1 / 2 / 3 以外の人数 / キャラ数（legacy `scoreboard<n>-set`・Socket.IO `set`）は、起動形態によらずこの 503 で、何も書かずに断る（それまでデスクトップは 500）。同じボードへの set フィールド（phase / pool / match / best of / losers。legacy `scoreboard<n>-set`・`update-team`・Socket.IO）も ADR-005 S3e-7 から同じ 503（それまでデスクトップは 500）。v1 のボード 3 への書き込みは起動形態によらず `require_board_12` が 400 `INVALID_ARGUMENT` で断る（ADR-005 S3c） |
| `INTERNAL` | 500 | 想定外の内部例外 |

### 2.7 操作ログ（D7・A-1）
- **書き込み操作（§6・§7）は INFO で専用ロガーに記録（MUST）**: 時刻・送信元 IP（`request.remote_addr`）・
  メソッド・パス・主要パラメータ・結果（ok/error code）。
- 読み取り（§5）は**記録しない**（or DEBUG）。既存 `before_request`/`after_request`（DEBUG・パスのみ）とは別建て。
- ⚠️ トークンは全端末で 1 つを共有するため、操作主体は **IP 粒度まで**（D5 の帰結。S3a 以降も同じ）。トークンはログに出さない（拒否のログもパスのみで、クエリ文字列を含めない）。ボディに認証情報や PII を含めない（CLAUDE.md 機密ルール）。
  - 選手データを運ぶ `PATCH .../players/{p}`（§6.8）・`PATCH .../players/{p}/fields`（§6.41。ADR-005 S4z）・`PUT /players`（§6.26）・`POST .../players/{p}:load`（§6.27）・`POST /players:delete`（§7.2）は**ボディのキー名だけ**を記録し、値（タグ・本名・SNS）は記録しない。エラーの `message` にも値を入れない。
  - ゲームアセットのルート（ADR-005 S-final SF-4b-1）: ZIP のアップロード（§6.48）と破棄（§6.49）は本文を JSON として読まない（`write_op(body="none")`）ので、パラメータは受け取ったバイト数（`<N bytes>`）か `<no body>`。パスのアップロードの id は先頭 6 文字だけ（`/assets/uploads/abc123…`）。ファイル名（`X-Autona-File-Name`）はどのログにも出さない。`POST /assets:importZip` はボディのキー名だけ。断った答えの行には `error.reason` を ` (disk_full)` のように添え、core の理由の文（パスを含みうる）は別の INFO 行（`API v1: assets <操作> refused: <code>: <文>`）にだけ出す

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
  { "ok": true, "data": { "accepted": true, "operation": "tournament.load", "loadRevision": 4 } }
  ```
  - `POST` / `DELETE /tournament` だけは `loadRevision`（その要求の `tournamentInfo.load.revision`。202 の前に state に
    出ている）を返す。クライアントは state の `load.revision` がそれに達してから `status` を自分の要求の結果として読む（§5.7）
- **ゲームアセットのジョブ**（§7 の `POST /assets:importZip`・`POST /assets/packs:install`。ADR-005 S-final SF-4b-1）も 202 で、`data.job` にそのジョブの `revision` を返す（`{ "accepted": true, "operation": "importZip" | "install", "job": 4 }`）。完了は WS ではなく `GET /assets/job`（§5.28）で追う（`revision` が同じで `status` が `running` でなくなったら結果）。止めるのは `POST /assets/job:cancel`（§6.50）にその `revision` を渡す。1 度に 1 つで、走っている間の開始は `409 CONFLICT`（`reason: "busy"`）
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
コード確認（`AutonaActions.MakeBoards` / `EnsureBoard`）に基づく。

- **有効値は `1`/`2`/`3` のみ**。3 ボードは起動時に core が作り（`AutonaActions.MakeBoards`。ADR-005 S4ac-1）、SCORE ページは形式（`scoreboard.battle_format`）のボードを 1 つだけ描く。

  | n | 種類 | 書き手 | 状態キー |
  |---|------|--------|---------|
  | 1 | 1v1（シングルス・既定表示） | `AutonaActions` / `PlayerService` ほか | `score.1` |
  | 2 | チーム | `AutonaActions` / `PlayerService` ほか | `score.2` |
  | 3 | クルーバトル | `CrewBattleService`（**別の操作**） | `score.3` |

- **ボードの構築（2026-11-11・ADR-005 S4ac-3）**: Qt のボードのウィジェット（`AutonaScoreboardManager` → `AutonaScoreboardWidget` / `AutonaCrewBattleWidget`）は削除した。ボードは S4ac-1 から起動時に 3 つとも core にあるので、**デスクトップにも「未構築のボード」は無い**。本書の「未構築のボードを構築してから書く / 答える」「構築しない」「入力中の欄を先に確定する（フォーカスを外して Qt に確定させる）」は、それまでのデスクトップの遅延構築と Qt の欄の確定についての記述で、S4ac-3 からはどれも何もしない。断り方・書く値・held の扱いは変わらない
- **ボードの操作のスレッド（2026-11-12・ADR-005 S4ad）**: デスクトップのボードホスト `DesktopBoardHost`（操作を GUI スレッドで同期実行していた）と、何もしなくなっていたフック（`Prepare` / `Built` / `CommitEdit` / `DiscardTypedText` / `PlayerLoaded`）を削除した。**ボードの操作は両シェルとも呼び出し元（リクエスト）のスレッドで、ボードホスト（`AutonaWebOperations.boardHost`）の 1 本の再入可能ロックの下で走る**（ヘッドレスと同じ）。デスクトップのホットキー（スコア ±1・スコアのリセット・反映・スワップ。対象のボードもロックの中で読む）・サイドバーの形式の切り替え・ゲームのロード（`gameLoaded` の通知から保存サイクルを閉じるまで。ロスターとロードの反映を含む。Qt モデルの整理 PR4 まではモデルの作り直しから）も同じロックの下なので、`clear_all` や `PATCH set` の途中に別の要求・ホットキー・セットの追従が割り込まず、ロードの反映に Web の書き込みが混ざらない（S4ad まではどれも GUI スレッドで直列だった）。v1 の `:apply`（§6.6）はボードの操作ではなく、S4u からロックの外のまま。本書の「GUI スレッドで同期実行する」はそれまでのデスクトップの記述で、応答の時点で書き込みが済んでいることは変わらない。**終了の開始（ウィンドウを閉じてバックグラウンドの仕事を止めた後・イベントループの終了後）からは、リクエストのボードの操作は何も書かずに `503 UNAVAILABLE`（`the app is shutting down`）**（S4ad まではデスクトップのホストが GUI スレッドを待てなくなって返していた 503 を、終了のフラグで返す。両シェル同じ。ヘッドレスは S4ad の見直しから。§9）

- **`{n}` は API 層で `{1,2,3}` を検証し、範囲外は `400 INVALID_ARGUMENT`（MUST）**。
  `GetScoreboard(n)` は未知番号で `None` を返すのみで、既存 Action はその後 `None.signals` で
  AttributeError になるため、**API 層で事前検証する**（NOT_FOUND ではなく INVALID_ARGUMENT 扱い）。
- **board 3（crew）は別クラス**（書き手は `CrewBattleService`）。クルーボードでできることは ADR-005 S4w で v1 に揃った:
  - 同じ意味のものは同じルート: `:swap-teams`（§6.3）・`score:reset`（§6.2）・`teams/{t}/color`（§6.4）・`PATCH set`（§6.5）・`:clear` / `:reset-players` / `:reset-match`（§7.1）・`PATCH /tournament/info` の `displayTournamentName`（§6.19）・読みの `GET /scoreboards/3` と `/pending`（§5）
  - クルーだけのもの: メンバー（§6.38・§6.39）。**board 1 / 2 に投げると `400 INVALID_ARGUMENT`**。X 投稿文（§5.19）は ADR-005 S4x からボード 1 / 2 にもある。コンボの候補（§5.20）は `?board=` を取る（省略できる）
  - クルーに無いものは `400 INVALID_ARGUMENT` で**明示拒否**する: `score:adjust` / `score:set`（合計はストックから算出する。チームのスコアは無い）・`:apply`（held でない）・`stats:refresh`・選手のルート・`:load-set` / `:pull-stream` / `:follow` / `:stop-follow` / `GET follow`・`:reset-all`
  - **未構築のクルーボード**（デスクトップの遅延構築）: 書き込みは構築してから書く（未構築への書き込みは構築時のシードで消える）。`score:reset`・`:reset-players`・`:reset-match` と `stock:adjust` の `delta: 0` は、まっさらなボードに意味が無いので**構築せず何も書かずに `200`**。`GET /scoreboards/{n}/post`・`/match-names/candidates` も構築しない。`:clear`・`:swap-teams` は構築する（まっさらなボードを変える）。既存の `GET /scoreboards/3` と `/pending` は従来どおり構築する
- **非表示ボードへの操作は配信に出ない**: OBS は表示中ボードのレイアウトを参照するため、裏ボードを
  操作しても画面は変わらない。誤対象を避けるため、`GET /api/v1/health`（§5.1）に**現在アクティブな
  ボード番号**を載せる（`activeBoard`）。対戦形式そのものは `GET /api/v1/battle-format`（§5.21）で読み、
  `PUT /api/v1/battle-format`（§6.40）で切り替える（ADR-005 S4x。それまではデスクトップのサイドバーの操作だけ）。
- 初回は **board 1（1v1）を主対象**として運用想定（board 2 も同操作で動く）。

---

## 3. 前提条件（NOT_READY の扱い）

- 大会/試合に依存する読み取り（`/sets`・`/sets/{id}`）は provider 不在時、`data` を空（`[]`/`null`）で返す（既存挙動準拠）。
- provider 必須の操作（`bracket:refresh` 等）は provider 不在時 **`409 NOT_READY`**。
- スコア操作・set 情報設定は大会未ロードでも現在の盤面状態に作用する（試合未選択をエラーにしない）。
- **settings.json に保存してある値を読んで変える操作**は、起動時に `settings.json` を読めず、**保存の時点でもまだ開けない**とき **`409 NOT_READY`**（`settings.json could not be read: nothing was saved`。何も変えない。2026-11-11）: 配信チャンネルの追加・変更・削除（`POST` / `PUT` / `DELETE /stream/channels`）・配信サムネのオプション（`PUT /stream/thumbnail/options`）・試合サムネイルの設定（`PATCH /thumbnail`・`POST /thumbnail:reset`・`POST /thumbnail/background`）・大会リザルトのレイアウト（`PATCH /result`）と、設定ページの保存（§6.44〜6.47）。core が `SettingsUnreadable` を送出し、`write_op` が写す。読めなかった起動でも保存の前に読み直すので、今度は読めればその中身の上に変える（壊れていれば退避してから既定の上に）

---

## 4. WebSocket 購読（既存を流用）

- 本 API は**新しい WS チャネルを作らない**。状態フィードバックは**既存 Socket.IO をそのまま購読**する（D3）。例外は値を運ばない通知 4 つ（`scoreboard_pending`（§5.15。ADR-005 S4u）・`scoreboard_follow`（§5.17。ADR-005 S4v）・`battle_format`（§5.21。ADR-005 S4z）・`games_changed`（§5.26。ADR-005 S-final SF-3a））と、アプリの実行中の仕事を運ぶ `status`（§5.23。ADR-005 S-final SF-2。state ではない）だけ。公開値の配信は変わらない。
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
| `GET /api/v1/scoreboards/{n}` | スコアボード n の状態（`swapped` 含む。`swapped` はボード 1/2 では `score.{n}.teamsSwapped` のライブ値（反映前でも入替を返す。ADR-005 S2c〜）、ボード 3 も `score.3.teamsSwapped`（ADR-005 S4w。一度もスワップしていないボードにキーは無く、`swapped` は `false`。それまでは state の外のフラグ）。どちらの起動形態でもボード 3 の `swapped` を返す） | `get_scoreboard`(+`get_swap`) | #9/#22 |
| `GET /api/v1/scoreboards/{n}/pending` | 反映前のライブ値・保留中か・未反映があるか（§5.15。反映前の編集を含むので **LAN からはトークン必須**。2026-11-09 追加） | `get_pending` | — |
| `GET /api/v1/scoreboards/{n}/follow` | そのボードが追従している配信台・配信と取り直しの時計（§5.17。2026-11-09 追加） | `get_follow`（`core/SetFollowService.Snapshot`） | — |
| `GET /api/v1/games` | ゲーム一覧 | `get_games` | #15 |
| `GET /api/v1/games/current` | 現在ゲーム | `get_current_game` | #16 |
| `GET /api/v1/match-names` | match/phase の用語（キーつき・テンプレートつきの dict。Web のページはコンボの候補にこれを使わない） | `get_match_names` | #17 |
| `GET /api/v1/match-names/candidates` | コンボが出す候補（§5.20。2026-11-09 追加。`?board=` は 2026-11-10。ボードのコンボと Web のスコアボードページが使う） | `get_match_name_candidates`（`core/MatchNameCandidates.Candidates`） | — |
| `GET /api/v1/scoreboards/{n}/post` | ボードの X 投稿文（§5.19。ボード 3 は 2026-11-09、ボード 1 / 2 は 2026-11-10 追加。反映前の値と選手の X ハンドルを含むので **LAN からはトークン必須**） | `get_board_post`（ボード 1 / 2 は `AutonaActions.PostText`、ボード 3 は `CrewBattleService.PostText`） | — |
| `GET /api/v1/battle-format` | 対戦形式と、それが表示するボード（§5.21。2026-11-10 追加。変わると Socket.IO の `battle_format` が出る: S4z） | `get_battle_format`（`AutonaActions.ResolveBattleFormat`） | — |
| `GET /api/v1/settings` | 設定ページの設定とホットキー（§5.22。許可リストで、トークンは返さない。2026-11-11 追加。`defaults` は 2026-11-13） | `get_settings`（`core/AppSettings.Describe` + `core/HotkeyRules.Describe`） | — |
| `GET /api/v1/status` | アプリが実行中の仕事（デスクトップのフッターの源。§5.23。2026-11-14 追加。変わると Socket.IO の `status` が出る） | `get_status`（`core/StatusService.Snapshot`） | — |
| `GET /api/v1/connection` | Web サーバーの URL（LAN / ループバック）・ポート・`out/` の絶対パス（§5.25。**この PC からだけ**（`THIS_PC`）。2026-11-14 追加） | `get_connection`（`WebServer.Serving` / `LanOnAtRestart`） | — |
| `GET /api/v1/access-token` | アクセストークン（§5.29。**アプリ自身のウィンドウからだけ**（`SHELL`）。2026-11-14 追加） | `get_access_token`（`core/WebAccess.Token`） | — |
| `GET /api/v1/shell-session` | シェル要求のチケット（§5.30。**アプリ自身のウィンドウからだけ**（`SHELL`）。2026-11-14 追加） | `get_shell_session`（`core/WebAccess.IssueShellTicket`） | — |
| `GET /api/v1/api-catalog` | API 設定のページ（`/web/api`）のエンドポイント一覧とエラーコード（§5.24。2026-11-14 追加） | `get_api_catalog`（`core/ApiCatalog.Payload`） | — |
| `GET /api/v1/assets/packs` | ゲームアセットのパックの一覧（上流の索引・インストール済み・版。§5.27。**この PC からだけ**（`THIS_PC`）。2026-11-14 追加） | `get_asset_packs`（`core/AssetPackService.Packs`） | — |
| `GET /api/v1/assets/job` | ゲームアセットの取り込みのジョブの状態（§5.28。**アプリ自身のウィンドウからだけ**（`SHELL`。SF-3b の `THIS_PC` から SF-4b-1 で移した）。2026-11-14 追加） | `get_assets_job`（`core/AssetPackService.Job`） | — |
| `GET /api/v1/characters` | キャラ一覧 | `get_characters` | #18 |
| `GET /api/v1/variants` | バリアント一覧 | `get_variants` | #19 |
| `GET /api/v1/sets` | 試合一覧（`?finished=true` で完了含む） | `get_sets` | #35 |
| `GET /api/v1/sets/{setId}` | セット詳細 | `get_match` | #36 |
| `GET /api/v1/commentary` | 実況者状態 | `get_comms` | #37 |
| `GET /api/v1/countries/{code}/states` | 国の州・地域一覧（未知の国は `404 NOT_FOUND`。2026-10-21 追加） | `get_states` | #45 |
| `GET /api/v1/countries` | 国の一覧（カスタム国旗を含む。§5.8。2026-11-04 追加） | `get_countries`（`core/CountryCatalog.CountriesPayload`） | — |
| `GET /api/v1/layouts` | レイアウト一覧（§5.2。2026-10-22 追加） | なし（`core/LayoutCatalog.Layouts`） | — |
| `GET /api/v1/layouts/selected/{kind}` | パネルが表示・OBS に渡しているデザイン（§5.3。2026-10-31 追加） | `get_selected_layout`（`core/LayoutCatalog.SelectedLayout`） | — |
| `GET /api/v1/layouts/selected/board/obs-path` | OBS に指定するファイルの絶対パス（§5.14。ユーザー名を含むので **LAN からはトークン必須**。2026-11-09 追加） | `get_board_files`（`core/LayoutCatalog.BoardFiles`） | — |
| `GET /api/v1/commentary/options` | 実況の役割候補・アイコン・スロット・表示上限（§5.4。2026-10-31 追加） | `get_commentary_options`（`core/CommentaryModel` の定数） | — |
| `GET /api/v1/stream/channels` | 保存した配信チャンネル（§5.5。2026-10-31 追加） | `get_stream_channels`（`core/StreamService.Channels`） | — |
| `GET /api/v1/stream/thumbnail/options` | 配信サムネ生成のオプション（§5.6。2026-10-31 追加） | `get_stream_thumbnail_options`（`core/StreamService.ThumbnailOptions`） | — |
| `GET /api/v1/tournament` | 大会の URL と、設定 / 解除の進み具合（§5.7。2026-11-01 追加） | `get_tournament`（state の `tournamentInfo.url` / `load`。`core/TournamentService.Load`） | — |
| `GET /api/v1/tournament/phases` | セット選択のフェーズの行（§5.18。2026-11-09 追加） | `get_tournament_phases`（`core/SetSelectionService.Result`） | — |
| `GET /api/v1/tournament/sets` | セット選択のセットの行（§5.18。2026-11-09 追加。読みは取得しない。同期で取得する `GET /sets` とは別） | `get_tournament_sets`（同上） | — |
| `GET /api/v1/tournament/stations` | 配信台と配信の行（§5.18。2026-11-09 追加） | `get_tournament_stations`（同上） | — |
| `GET /api/v1/analysis` | この大会の試合一覧の行（§5.9。2026-11-04 追加） | `get_analysis`（`core/AnalysisService.Result`） | — |
| `GET /api/v1/stats/overlays` | 統計情報表示設定の種類の表・選択中・Companion マクロのリクエスト（§5.10。2026-11-05 追加） | `get_stats_overlays`（`core/StatsOverlays`） | — |
| `GET /api/v1/players` | 選手 DB の全件の行（§5.11。本名を含むので **LAN からはトークン必須**。2026-11-05 追加） | `get_players`（`core/PlayerDirectory.Registered`） | — |
| `GET /api/v1/players/entrants` | 大会の参加者の行（§5.11。同上。2026-11-05 追加） | `get_player_entrants`（`core/PlayerDirectory.Entrants`） | — |
| `GET /api/v1/thumbnail` | 試合サムネイルの設定・ゲーム・テンプレート・動画タイトル・描画できるか（§5.12。2026-11-07 追加） | `describe_thumbnail`（`core/ThumbnailService.Describe`） | — |
| `GET /api/v1/thumbnail/preview` | 最新の描画の PNG（§5.12。2026-11-07 追加） | `get_thumbnail_preview`（`core/ThumbnailService.PreviewImage`） | — |
| `GET /api/v1/result` | 大会リザルトのレイアウト・行・描画と取り込みの状態（§5.13。2026-11-09 追加） | `describe_result`（`core/ResultService.Describe`） | — |
| `GET /api/v1/result/post` | X の投稿文（§5.13。DB の X ハンドルを含むので **LAN からはトークン必須**。2026-11-09 追加） | `get_result_post`（`core/ResultService.PostText`） | — |
| `GET /api/v1/result/preview` | 最新の描画の PNG（§5.13。2026-11-09 追加） | `get_result_preview`（`core/ResultService.PreviewImage`） | — |

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
  "outLastWriteUnix": 1718000000,
  "previewPort": 52345
} }
```
- `previewPort`（2026-11-14 追加・ADR-005 S-final SF-8a）: レイアウトのプレビュー用の読み取り専用リスナーのポート（同じホスト・同じバインド先。OS が起動ごとに選ぶ）。待ち受けていない（サーバーが無い・bind できなかった）ときは `null`。ページのプレビューの iframe は `http://<このホスト>:<previewPort>/layout/...` を読み、`null` ならプレビューを出さない（このサーバーのオリジンに代えない）。そのリスナーが出すのは、レイアウトが読むもの（`layout/`・`assets/`・`user_data/` の画像・`out/program_state.json`・v1 の READ・Socket.IO の読み取り）だけで、ほかは**トークンがあっても** 403 `FORBIDDEN`（Socket.IO の書き込みイベントも同じ ack）。legacy の読み取りルートと `out/` のほかのファイルも出さない。規則は `src/core/WebAccess.PreviewServes`
- `activeBoard`: 現在表示中のボード番号（1/2/3）。外部コントローラが「表示中ボードを操作」する判断材料（§2.12）。`AutonaActions.ActiveBoard()`（`ResolveBattleFormat` の写像。§14.1）で、`GET /battle-format`（§5.21）の `board` と同じ値。
- `outLastWriteUnix`: `out/` 配信ファイルの最終更新時刻（OBS 出力健全性の指標）。取得方法は `TBD`。
- `tournamentLoaded`: provider 有無。

### 5.2 `GET /api/v1/layouts`（2026-10-22 追加）
`layout/` 配下の配信用レイアウト（旧来型テンプレート + Designer で作った `layout/custom/{slug}/`）の一覧。
Board / Commentary / Top 8 のページと Designer の「開く」一覧が使う**同じカタログ**（`src/core/LayoutCatalog.py`）を返す
（[../../docs/changes/layout-custom-unification.md](../../docs/changes/layout-custom-unification.md) Step 1）。

- `?kind=` で絞り込む。値は `scoreboard` / `designer` / `commentary` / `top8`。カンマ区切り（`?kind=scoreboard,designer`）・
  パラメータの繰り返しのどちらでも複数指定できる。省略・空なら全件。未知の kind は `400 INVALID_ARGUMENT`
- 状態ではないので `revision` は付かない。読み取りなので操作ログにも出ない（§2.7）
- 並び順は旧来型（フォルダ名順）→ Designer プロジェクト（slug の大文字小文字無視順）。Board のページのプルダウンと同じ

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
- ファイルシステムの絶対パスは返さない（D5: LAN 公開時もパスを出さない）。OBS に指定するパスは §5.14 に分けた

### 5.3 `GET /api/v1/layouts/selected/{kind}`（2026-10-31 追加・ADR-005 S4b）
パネルが選んでいるデザイン（プレビューと OBS URL に使うもの）を、§5.2 と同じ形のエントリ 1 件で返す。

- `kind` は選択を持つ種類だけ: 現在は `commentary`（`settings.json` の `commentary.selected_design`）と
  `top8`（`top8.selected_design`。2026-11-04 追加・ADR-005 S4h。Top 8 のページ（S4i。それまでは Qt のパネル）も同じ関数で読み書きする）と
  `board`（`board.selected_design`。2026-11-09 追加・ADR-005 S4s。§5.2 の種類ではなく選択の名前で、一覧は `scoreboard` と `designer` を合わせたもの。`?kind=board` は `400`）。
  正本は `core/LayoutCatalog.SELECTION_SETTINGS`。それ以外は `400 INVALID_ARGUMENT`
- 保存されている id がカタログに無い（削除された等）ときは、同じ順で代わりを返す: 既定（`commentators` /
  `bracket_top8` / `scoreboard_crewbattle`）→ 一覧の先頭。
  保存値は書き換えない。その種類のレイアウトが 1 つも無ければ `404 NOT_FOUND`
- 設定であって状態ではないので `revision` は付かない

### 5.4 `GET /api/v1/commentary/options`（2026-10-31 追加・ADR-005 S4b）
実況・解説ページの入力候補。core の定数（`core/CommentaryModel`）をそのまま配る（Web 側に複製しない）。

```json
{ "ok": true, "data": {
  "roles": ["実況", "解説", "選手", "主催", "MC", "スタッフ"],
  "icons": [{ "id": "mic", "label": "マイク" }, { "id": "megaphone", "label": "拡声器" }, { "id": "phone", "label": "電話" }],
  "slots": ["1", "2", "…", "12"],
  "maxDisplay": 4
} }
```
- `roles`: 役割の候補（自由入力も可）。`icons[].id` が `commentary.{N}.icon` に入る値（空文字 = アイコンなし）
- 状態ではないので `revision` は付かない

### 5.5 `GET /api/v1/stream/channels`（2026-10-31 追加・ADR-005 S4e）
配信設定ページの配信先コンボの中身。`settings.json` の `stream_channels`（形式は不変）。

```json
{ "ok": true, "data": { "channels": [
  { "name": "BST Stream", "url": "https://www.youtube.com/@B_ST_Stream/live" }
] } }
```
- 添字（0 始まり）が §6.16 の `{index}`。dict でないエントリは数えない（次の書き込みで落ちる）
- 設定なので `revision` は付かない。ほかのクライアントの変更は届かない（読み直す）

### 5.6 `GET /api/v1/stream/thumbnail/options`（2026-10-31 追加・ADR-005 S4e）
`{ "source": "banner" | "org", "style": "center" | "netflix", "overlayOrgIcon": bool }`（`settings.json` の
`stream_thumbnail_options`。壊れた値・無い値は既定 `banner` / `center` / `false`）。設定なので `revision` は付かない

### 5.7 `GET /api/v1/tournament`（2026-11-01 追加・ADR-005 S4g。2026-11-04 S4g レビューで state に移した）
大会の URL と、設定 / 解除の進み具合の読み取り。**同じものが state の `tournamentInfo.url` / `tournamentInfo.load` に出ている**
（Socket.IO の delta で届く）。大会情報ページはこれを読まずに state を描く（ポーリングしない）。

```json
{ "ok": true, "data": { "url": "https://www.start.gg/tournament/.../event/...",
  "loaded": true, "status": "loaded", "error": "", "revision": 3 }, "revision": 1234 }
```
- `url`: 最後に設定した大会の URL（`settings.json` の `TOURNAMENT_URL`。無ければ `""`）。state の `tournamentInfo.url` と同じ
- `loaded`: **provider があるか**（「読み込めた」ではない。読み込みに失敗した大会でも `true`。読み込めたかは `status` で見る。
  S4g レビュー L6）
- `status`: 最新の設定 / 解除の進み具合。`idle`（何も設定していない・解除した・起動直後）/ `resolving`（admin の URL からイベントを
  探している）/ `loading`（大会のデータを取得中）/ `loaded` / `error`
- `error`（`status` が `error` のときだけ）: `unresolved`（admin の URL にイベントが無かった。何も変えていない）/
  `load_failed`（データが来なかった: start.gg が答えない・そのイベントが無い・適用に失敗した・読み込みが例外を出した）
- `revision`（`data` の中）: **設定 / 解除のたびに 1 進む**（進み具合の遷移では進まない。state の `revision` とは別）。
  再起動しても戻らない（state に保存され、起動時のシードは `status` を `idle` に戻して `revision` を残す）
- `status` / `error` / `revision` は state の `tournamentInfo.load = {status, error, revision}` と同じ（`tournamentInfo.media` と同じ形）
- 自分の §7.3 `POST` / `DELETE /tournament` の結果を見分けるには、202 の `loadRevision` を使う。その要求の `load` は 202 の前に
  state に出ているので、state の `load.revision` が `loadRevision` 以上になった後の `status` がその要求（か、後から来た要求）の結果
  （`loadRevision` は後から来た要求の番号になることがある。そのときも state が出しているのはその新しい要求の結果）
- 封筒の `revision` は state の `revision`（§2.4）

---

### 5.8 `GET /api/v1/countries`（2026-11-04 追加・ADR-005 S4i）
国のコンボの行（Top 8 のページが国を選ぶ一覧）。プログラムのロケールの国の全件、その後ろにカスタム国旗
（`user_data/additional_flag`）。`CountryCatalog` の行のデータに `label`（コンボの表示文字列。国は
`表示名 / 英語名 (ISO2)`）と `custom`（カスタム国旗か）を足したもの。

```json
{ "ok": true, "data": [
  { "code": "JP", "name": "Japan", "display_name": "日本", "en_name": "Japan", "latitude": 36.0, "longitude": 138.0,
    "asset": "./assets/country_flag/jp.png", "emoji": "🇯🇵", "label": "日本 / Japan (JP)", "custom": false },
  { "name": "My Flag", "display_name": "My Flag", "en_name": "My Flag", "code": "My Flag",
    "asset": "./user_data/additional_flag/My Flag.png", "label": "My Flag", "custom": true }
] }
```
- `code` が §6.22 の `country` に渡す値（カスタム国旗は名前）。同じ `code` が複数あれば先の行が使われる
- 国のデータの読み込み前は `409 NOT_READY`（§5 `countries/{code}/states` と同じ）。状態ではないので `revision` は付かない。
  Top 8 のページはこの 409 では止まらず、国の欄だけを無効にして注記と再読み込みボタンを出す

### 5.9 `GET /api/v1/analysis`（2026-11-04 追加・ADR-005 S4k）
この大会の試合一覧（統計情報ページの「この大会の試合一覧」タブ）。core の `AnalysisService` がメモリに持つ最新の結果で、
行は state に入らない（大会規模で数百〜千超。ADR-005 S4j 論点 2）。進み具合は state の `analysis`
（`{status, error, done, total, failed, revision}`）にあり、WS で届く。取得は §7.3 `POST /api/v1/analysis:refresh`。

```json
{ "ok": true, "revision": 1234, "data": {
  "status": "done",
  "url": "https://www.start.gg/tournament/.../event/...",
  "rows": [
    { "round": "Winners Final", "roundOrder": ["123", 3, 0],
      "p1": { "seed": 1, "name": "Alpha" }, "p2": { "seed": 4, "name": "Bravo" },
      "score": [3, 1], "upset": 0, "losersRun": 0, "seedOver": null }
  ]
} }
```
- `status` は `analysis.status`（`idle` / `running` / `done` / `error`）のいま読んだ値。`status`・`url`・`rows` は同じ時点の組
  （サーバーが state の lock の 1 回の保持の中で読む。core は行の差し替えと `analysis` の publish を同じ保持の中で行う）
- `rows` は最後に `done` で終わった取得の行（`error` で終わった取得は前の行を残す）。**今読み込んでいる大会の結果のときだけ**返し
  （`url` が今の大会の URL と一致するとき）、それ以外（未取得・大会を変えた後・大会なし）は `url: ""`・`rows: []`
- 行は構造で、表示の文言（「TBD」「(seed N)」「-」）はクライアントの仕事: `round`（ラウンド名。複数 group の大会は
  「フェーズ / グループ · 」の前置きつき）・`roundOrder`（Round 列の並べ替えキー `[group id, ラウンド, ラウンド内の位置]`）・
  `p1` / `p2` の `{seed, name}`（不明は `null` / `""`）・`score`（`[a, b]`。未完了は `null`）・`upset`（勝敗が付いていなければ `null`）・
  `losersRun`（無しは 0）・`seedOver`（無しは `null`）。並び順は core の順で、表示の既定の並びはクライアントが決める
- 状態の読み取りなので封筒に `revision` が付く。行の世代は `analysis.revision`（取得を受け付けるたび・大会を変えるたびに進む）。
  統計情報ページは `analysis.revision` / `status` が変わったときだけ読み直す

### 5.10 `GET /api/v1/stats/overlays`（2026-11-05 追加・ADR-005 S4l）
統計情報表示設定ページ（`/web/statsPreview`）の読み取り。種類の表は core の `StatsOverlays` の固定表で、選択中は
`settings.json` の `stats_preview.selected_type`（UI の設定で state ではない。Qt 版と同じキー・値）。

```json
{ "ok": true, "data": {
  "overlays": [
    { "key": "upset_factor", "id": "upset_factor", "entry": "index.html", "displayName": "Upset Factor" },
    { "key": "last_sets_p1", "id": "last_sets", "entry": "player1.html", "displayName": "Last Sets (Player 1)" }
  ],
  "selected": { "key": "upset_factor", "id": "upset_factor", "entry": "index.html", "displayName": "Upset Factor" },
  "refresh": { "method": "POST", "path": "/scoreboards/1/stats:refresh",
               "url": "http://127.0.0.1:5107/api/v1/scoreboards/1/stats:refresh" }
} }
```
- `overlays` は 7 種を表示順に。`key` が保存される値（フォルダ名を変えても変わらない）、`id` はレイアウトのフォルダ、
  `entry` は入口の html。`last_sets` / `tournament_history` は同じ `id` で `player1.html` / `player2.html` の 2 項目なので、
  選択は `id` ではなく `key` で行う。`displayName` は英語の原文（訳はクライアント）
- `selected` は保存値が表に無い（未保存・古い値）ときは先頭（Qt 版と同じ。保存値は書き換えない）
- `refresh` は Companion マクロの `stats:refresh` のリクエスト。`url` はサーバーが組む: **実際に待ち受けている**ポートと LAN 公開の有無（設定の変更は次の起動から効くので、
  設定値ではなくサーバーが bind したもの。起動していない環境では設定値）で、LAN 公開なら LAN の IP、でなければ `127.0.0.1`
  （ページの `location` からは LAN の IP が分からないため）。**トークンは含めない**。別 PC の Companion は
  ヘッダ `X-Autona-Token` が要る
- 設定なので `revision` は付かない

### 5.11 `GET /api/v1/players`・`GET /api/v1/players/entrants`（2026-11-05 追加・ADR-005 S4m）
選手 DB（`user_data/local_players.json`）を選手リストの行として読む。**本名を含むので `PLAYERDB` 区分**（READ ではない。`/playerdb` と同じく、LAN の他端末はトークン必須）。

```json
{ "ok": true, "data": { "game": "tgame", "players": [
  { "key": "SP Alpha", "prefix": "SP", "gamerTag": "Alpha", "name": "本名", "twitter": "@alpha",
    "country_code": "JP", "state_code": "13", "custom_textbox": "1 行目\n2 行目",
    "mains": [["Mario", 1, "EX"], ["Bowser", 0, ""]] }
] } }
```
- **`game` は行の `mains` のゲーム**（今の codename。未選択は `null`）。行と同じ 1 回のスナップショットから読む。ページは §6.26 の `mains` にこの値を添えて送り返す。**`state` の `game.codename` で代用しない**（`ForgetSelection` の後も state には前のゲームの codename が残り、未選択と照合して `409` になる）
- `/players` はキーの順（大文字小文字を無視）。`key` は DB のキー（スポンサー + タグ）で、§7.2 の削除が取る値
- `/players/entrants` は `{"game": ..., "entrants": [...]}`。上位から順（`core/StreamService.TopEntrants`。大会の切替で空になる）。各行は上の形に `seed`（未シードは `null`）を足したもの。
  タグの突き合わせは `PlayerDB.GetPlayerFromTag` と同じ（gamerTag だけ・大文字小文字を無視・最初の行）で、見つかれば DB の記録の値（`gamerTag` も DB の表記）。
  **DB に無い参加者はタグだけの行**（`key` は `null`、他の欄は空）。大会が無ければ `[]`
- **`key` と `seed` は読むだけ**で、§6.26 に送り返せない（未知のキーとして `400`）
- 欄はどれも文字列（無ければ `""`）。`custom_textbox` は実際の改行で返す（DB の中はエスケープされた 1 行）
- `mains` は**読み込み中のゲームのものだけ**、`[en_name, スキンの行, バリアントの en_name]`（ゲームが未選択なら `[]`）。古い行の JSON 文字列の mains や壊れた要素では落ちず、読めない要素は除く。
  スキンは DB の値のまま（スキンの無いキャラの `-1` も）。無い・数値でないものは 0、`-1` より小さいものは `-1`。**カタログとは照合しない**ので、今のゲームに無いキャラ（mods オフの modded など）・未知のバリアント・範囲外のスキンもそのまま出る。§6.26 はその選手が持っている要素をそのまま受け取るので無編集で往復でき、編集画面はそれらを壊さずに持ち、**新しく足す要素だけ** `GET /characters` の選択肢から作る
- `country_code` / `state_code` は**表示のための読み替え**で、§6.26 が受け取る値になっている。カタログが知らない国は `""`。地域は国の地域コードならそのまま、DB に普通に入っている自由文字列（start.gg の地名で解決できなかったもの）は `FindState` で解決し、できなければ `""`。国データの読み込み前は DB の値のまま。
  **行の `""` は DB が空だという意味ではない**（解決できない地名・読めないカスタム国旗でも `""`）ので、**ページはこの 2 つを、操作者が変更したときだけ §6.26 に送る**（送らなければ DB の値が保たれる）
- `key` が文字列でない行（タグの無い行）は出さない
- 選手 DB なので `revision` は付かない。再読込の契機は Socket.IO の `playerdb` の push と `game`・`tournamentInfo.load` の変化。push の時点で参加者はもう読める（取得ジョブが `SetTopEntrants` の後に通知する）

### 5.12 `GET /api/v1/thumbnail`・`GET /api/v1/thumbnail/preview`（2026-11-07 追加・ADR-005 S4o）
試合サムネイルのページ（React の `/web/thumbnail`。ADR-005 S4p）が読むもの。`settings.json` の `thumbnail_config` を既定値で埋めて返す（**何も書かない**）。設定なので `revision` は付かない。

```json
{ "ok": true, "data": {
  "settings": { "template_id": "type_a", "background_source": "default", "background_name": "",
    "display_phase": true, "use_team_names": true, "use_sponsors": true,
    "game": { "ssbu": { "flip_p1": false, "flip_p2": true, "smooth_scale": true, "zoom": 100,
      "align": { "horizontal": 50, "vertical": 40 } } } },
  "game": { "codename": "ssbu", "name": "Super Smash Bros. Ultimate" },
  "templates": [ { "id": "type_a", "name": "Standard" }, { "id": "type_b", "name": "Center Panel" } ],
  "videoTitle": "大会名 Grand Final | P1(キャラ) vs P2(キャラ) | SSBU",
  "canRender": true
} }
```
- `settings.background_source` は `background_source` の無い古い設定でも読める値（パスから判定。書かない）。`background_name` はカスタム背景のファイル名だけ（パスは出さない。操作者のファイルの場所を LAN に出さないため）
- `settings.game` は保存されたゲームと今のゲーム。古い読みの残骸（`game.None`）は出さない（ファイルからも消さない）
- `game` は `null`（未選択）か `{codename, name}`。`templates[].name` は翻訳済み
- `canRender` はこのシェルで描けるか（ヘッドレスは `false`）
- **`settings` の値は描画に使う値と同じ**。`thumbnail.js` の読みと違う値も、この読みに揃えて描画に渡す: 無いときの `use_team_names` / `flip_p2` は `true`（js は `false`）、不正な形の保存値（`use_team_names: 0`、`zoom: 150.0`、範囲外など）は既定値、`align` の保存値 `0` は既定の 50 / 40（js が `|| 50` / `|| 40` と読むのでそう描かれる。旧 Qt パネルが保存できた値）。v1 の描画に加えて、SCORE の生成・X 投稿と legacy の `/scoreboard<N>-get-thumbnail-<png|jpg>` も同じ読みで描く（`ThumbnailService.RenderPayload`。ADR-005 S4p。それまでの SCORE / legacy は保存値をそのまま渡していた）。描画は `settings.json` を書かない（`background_source` の無い古い設定は、`GET` と同じく移行した値で描き、保存は次の `PATCH` / アップロードで行われる）
- `GET /thumbnail/preview`: エンベロープではなく PNG の本体（`Cache-Control: no-store`）。**このセッションで最後に完了したボード 1 の描画**で、ボード 1 の生成（`:generate`）も更新する（生成の際にこの画像だけ書けなかったときは前の画像のまま）。**ボード 2 の生成はこの画像を書かない**（2026-11-10・ADR-005 S4z レビュー。プレビューはボード 1 の絵で、どの経路でも別のボードの絵で答えない。§7.3）。描画がまだなら `404 NOT_FOUND`（前回のセッションの画像は起動時に消す）。クライアントは `?v=<thumbnail.render.revision>` で URL を変える（値は見ない）。`tmp/` は静的配信しない

### 5.13 `GET /api/v1/result`・`GET /api/v1/result/post`・`GET /api/v1/result/preview`（2026-11-09 追加・ADR-005 S4q）
大会リザルトのページ（React の `/web/result`。ADR-005 S4r）が読むもの。**何も書かない**。

```json
{ "ok": true, "revision": 42, "data": {
  "layout": "horizontal",
  "layouts": [ { "id": "horizontal", "name": "Horizontal (1920x1080)", "width": 1920, "height": 1080, "slots": 8, "team": false } ],
  "players": [ { "slot": 1, "rank": 1, "name": "Alpha", "sponsor": "SPN", "twitter": "alpha",
    "characters": [ { "en_name": "Mario", "skin": 2 }, null, null ], "fromScoreboard": null } ],
  "teams": [ { "slot": 1, "rank": 1, "name": "Reds",
    "members": [ { "name": "Alpha", "twitter": "", "character": null, "fromScoreboard": "Mario" } ] } ],
  "sample": false,
  "game": { "codename": "ssbu", "name": "Super Smash Bros. Ultimate" },
  "render": { "status": "idle", "path": "", "error": "", "revision": 0 },
  "standings": { "status": "idle", "error": "", "revision": 0 },
  "canRender": true
} }
```
- `layout` は `settings.json` の `result_config.layout`。保存値が無いときは今の対戦形式から決める（Team / Crew は `team`、ほかは `horizontal`）。対戦形式を選ぶと追従する（`team_split` や `vertical` の手動選択は保つ）
- `players` は常に 16 行、`teams` は常に 8 行。`characters` は常に 3 要素で、`null` は空。読めない値・今のゲームのものでない値も `null`。**保存された別のゲームのキャラ**（再起動をまたいでゲームが変わった場合など）は state に残るが、読みでは無いものとして扱う（`null`・描かない・`fromScoreboard` の補完が効く・`sample` と `:loadStandings` の確認の判定でも入力に数えない）。絵のパスが `user_data/games/<codename>/` の下にあればそのゲームの値と見なし、絵が無ければ今のゲームの一覧にあるかで決める。ゲームが未選択のときは、その下の絵を持つ値を描かない
- `fromScoreboard`: キャラ欄が空の選手に、スコアボードの同名の選手のキャラを描くとき、そのキャラの名前（描画のときに解決し、state には書かない）。無ければ `null`
- `sample`: 今プレビューすると固定のサンプルを描くか（今のレイアウトの枠に描くものが無い）。このとき `:generate` は `400`
- `GET /result/post`: `{"text": "大会名\n..."}`。X の投稿文で、**順位の順**に上位 4 行まで（描くものがある行だけ）。X のハンドルは行の `twitter`、無ければ選手 DB から引くので、**`PLAYERDB` 区分**（LAN の他端末はトークン必須。`GET /result` は READ で、DB の値を出さない）。ページはこれを持っておいて X 投稿のクリックの中で読む
- `game` は `null`（未選択）か `{codename, name}`。`canRender` はこのシェルで描けるか（ヘッドレスは `false`）
- `GET /result/preview`: エンベロープではなく PNG の本体（`Cache-Control: no-store`）。**このセッションで最後に完了した描画**（`:preview` と `:generate`）。描画がまだなら `404 NOT_FOUND`（前回のセッションの画像は起動時に消す）。クライアントは `?v=<result_render.revision>` で URL を変える（値は見ない）

### 5.14 `GET /api/v1/layouts/selected/board/obs-path`（2026-11-09 追加・ADR-005 S4s）
Board のページが OBS の「ローカルファイル」に指定させるファイルの絶対パス。絶対パスはユーザー名を含むので §5.2 と分け、**`PLAYERDB` 区分**（LAN からはトークン必須）。

```json
{ "ok": true, "data": {
  "designId": "scoreboard_crewbattle",
  "design": "C:\\Autona\\layout\\scoreboard_crewbattle\\index.html",
  "winnerPopup": "C:\\Autona\\layout\\winner_popup\\index.html"
} }
```
- `designId`: 答えた時点で選択中のデザイン（§5.3 の `id`）。`design` はこのデザインのパスで、選択は読みの間に変わりうるので、クライアントは自分が表示しているデザインと照合する（違えば読み直す）
- `design`: そのファイル。デザインが 1 つも無ければ `designId` とも `null`
- `winnerPopup`: 勝者ポップアップのファイル。どの一覧にも載らない固定のフォルダ（`layout/winner_popup`）
- OS の区切り文字のまま。設定であって状態ではないので `revision` は付かない

### 5.15 `GET /api/v1/scoreboards/{n}/pending`（2026-11-09 追加・ADR-005 S4u）

反映前の値を描くフォーム（これから作る React の SCORE ページ）のための読み取り。`{n}` は 1 / 2 / 3（それ以外は `400`）。**公開値には影響しない**: `GET /scoreboards/{n}`・`GET /state`・Socket.IO の `program_state` / `program_state_update`・`out/` の意味は変わらず、反映まで未反映の値を含まない（ADR-005 D4）。

```json
{ "ok": true, "revision": 12, "data": { "held": true, "dirty": true, "board": { "team": { "1": { "score": 3 } }, "swapped": false } } }
```

- `held`: このボードが編集を反映まで保留するか。デスクトップのボード 1 / 2 で `true`（未構築なら構築してから答える）。クルーボードとヘッドレスは `false`（書き込みは即時公開）
- `dirty`: 未反映の変更があるか（`StateManager.HasPendingChanges`。反映ボタンの点灯と同じ値）。`held` が `false` なら常に `false`
- `board`: ライブ値。形は `GET /scoreboards/{n}` と同じ（`swapped` 含む）。反映後は両者が一致する
- `revision` と中身は 1 回のロックの中で読む。`revision` は公開済みのもの（`score:adjust` の応答と同じ）で、**未反映の編集では進まない**ので、未反映の値の新しさの判定には使えない。読み直しの合図は下の通知
- **`PLAYERDB` 区分（§2.2）**: **LAN からはトークン必須**（`401 UNAUTHENTICATED`）。反映前の編集は操作者がまだ公開していない値で、本名を含みうる。READ（CORS `*`）だと他のサイトのページからも読める
- 委譲: `get_pending`。デスクトップは未構築のボードを構築してから読む（`GET /scoreboards/{n}` と同じ。構築で held になる）

**通知 `scoreboard_pending`（Socket.IO）**: 未反映の値か `dirty` が変わると `{ "board": 1, "dirty": true }` を送る。**値は運ばない**。クライアントは通知のあとに始めた `GET .../pending` の答えを使う（それだけで足りる: 通知は変更の後に積まれるので、最後の通知より後に始めた読みは最後の変更を含む。値の別経路が無いので突き合わせが要らない）。

- `dirty` は**送信時**の値で、1 窓に 1 ボード 1 通にまとまる。先頭はすぐ、続く通知は 50 ms の窓（`program_state_update` と同じ flusher が、同じ送信の delta の後に送る。delta の間隔は通知で動かない。例外は、通知の窓が開いている間に届いた delta がその窓に合流する点で、先頭で出られたものは最大 50 ms 遅れ、自分の窓を待つはずだったものは早まる）
- 送信先は `GET .../pending` を読める接続（信頼済みの room）だけ。OBS オーバーレイなどトークンなしの LAN の接続には届かない
- 届くのはデスクトップのボード 1 / 2 の held サイクルからだけで、ヘッドレスでは出ない（`program_state_update` で足りる）。統計の除外キー（`STATS_KEYS`）は held 通知を出さず `program_state_update` で届く
- 接続の直後は通知が来ないので、最初に 1 回 `GET .../pending` を読む

### 5.16 `GET /api/v1/stages`（2026-11-09 追加・ADR-005 S4u。**2026-11-12 ADR-005 S4ac-4 で削除**）

ゲームトラッカー（ゲームごとの勝敗とステージの行）と一緒に削除した（オーナー承認 2026-11-11）。いまは静的配信の `404`（v1 のエンベロープではない）。節の番号は欠番として残す。

### 5.17 `GET /api/v1/scoreboards/{n}/follow`（2026-11-09 追加・ADR-005 S4v）

そのボードが今何を追従しているか（SCORE ページのタイマー行が出すもの。S4ac-3 までは Qt のスコアボードのタイマー行も）。`{n}` は 1 / 2（board 3 はセットを読まないので `400`）。読みではボードを構築しない（構築されていないボードは何も追従していない）。

```json
{ "ok": true, "revision": 12, "data": {
  "station": { "type": "station", "id": 77, "identifier": 4 },
  "poll": { "mode": "station", "identifier": 4, "secondsLeft": 3 },
  "pollingEnabled": true,
  "setId": "1001"
} }
```

- `station`: 追従している配信台（`"station"`）か配信（`"stream"`）。無ければ `null`。**取り直しが止まっていても残る**（デスクトップの既定は `general.disable_autoupdate` が true で、追従は 1 回の照会になる）。手でセットを読んでも残る。**大会を変えても残る**（既存の動作で、`poll` も同じ。タイマー行も止まらない）。`:stop-follow` で消える
- `poll`: 取り直しの時計が動いている間だけ。`mode` は `set` / `stream` / `station`（タイマー行のラベル）、`identifier` は配信台の番号か配信のチャンネル（`set` は `null`）、`secondsLeft` は次の取り直しまでの整数秒（タイマー行のカウントダウン）。取り直しは 5 秒ごと。止まっていれば `poll` は `null`
- `pollingEnabled`: セットを読むと取り直しが始まるか
- `setId`: 最後に読んだセット（文字列。無ければ `null`）。追従の読みで、未反映の編集の値ではない。**反映前の読み込みも見える**（読み始めた時点で変わる。出るのは id だけで、中身は `/pending` と反映の後の公開値）
- READ（§2.2）。legacy `/scoreboard{n}-get-set` が同じものを READ で答えている。**state には入らない**（追従は state の外の core の持ち物で、`program_state.json` のキーを増やさない）

**通知 `scoreboard_follow`（Socket.IO）**: `station` か `poll` が変わると `{ "board": 1 }` を送る。**値は運ばない**ので、通知のあとに始めた `GET .../follow` の答えを使う。接続の直後は通知が来ないので最初に 1 回読む。

- `program_state_update` と同じ flusher が、同じ送信の delta の後に送る。開いている窓があればそれに合流し、無ければすぐ出る。1 回の送信に 1 ボード 1 通で、delta の間隔は動かさない
- 送信先は全接続（読みが READ で、通知はボードの番号だけ）
- `secondsLeft` が減るだけでは通知しない（クライアントが数える）。配信台・配信の追従は取り直しのたびに時計が張り直されるので、取り直しごとに通知が来る。`set` の取り直しでは来ない
- 変わらなければ出さない: 止めるものが無い `:stop-follow`、同じ配信台の選び直しで忘れるセットも無いとき
- `setId` の変化では基本的に出さない（ボード自身の書き込みの `program_state_update` / `scoreboard_pending` で分かる）。**ボードに書かずに `null` に戻るときだけ出す**: 読み込みの失敗（ボードは消えたままで、同じセットをもう一度読める）と、`:follow` の読み直しで前のセットを忘れたとき
- React の SCORE ページのタイマー行（ADR-005 S4ab）は、開いたとき・そのボードの通知・再接続の後にこれを読み、`secondsLeft` から数え下げる。0 を過ぎたら（`set` の取り直しは通知しないので）読み直す（1 回の読みにつき 1 回）。`secondsLeft` が `null` の読みは前に出していた数を残す（Qt の行と同じ）

### 5.18 `GET /api/v1/tournament/phases`・`/sets`・`/stations`（2026-11-09 追加・ADR-005 S4v）

Qt のセット選択ウィンドウと配信台ウィンドウが出す一覧。core の `SetSelectionService` がメモリに持つ最新の結果で、行は state に入らない（§5.9 と同じ）。**読みは取得しない**（窓を開くだけではネットワークに行かない）。取得は §7.3 の `:refresh`。

```json
{ "ok": true, "revision": 12, "data": {
  "status": "done",
  "url": "https://www.start.gg/tournament/.../event/...",
  "fetchedAt": 1794214800,
  "query": { "phase": 11, "finished": false },
  "rows": [
    { "id": 1001, "stream": "channel", "station": "3", "phase": "Pools", "pool": "A",
      "round": "Winners Final",
      "p1": { "name": "ALPHA", "members": ["ALPHA"] },
      "p2": { "name": "Team", "members": ["BRAVO", "CHARLIE"] } }
  ]
} }
```

- `phases` の行: `{ "id", "name", "groups": [{ "id", "name", "bracketType" }] }`。`stations` の行: `{ "type": "station" | "stream", "id", "identifier", "stream" }`（`identifier` は配信台の番号か配信のチャンネル。`stream` は配信台に紐付く配信で、無ければ `""`）。`query` は `sets` だけで、**正規化した値**を返す: `phase` はフェーズの行の id（`"11"` で頼んでも `11`）か `"all"`（フェーズが分からないときの `null` も `"all"`）
- `status` は state の `set_selection.{phases|sets|stations}.status`（`idle` / `running` / `done` / `error`）のいま読んだ値。`status`・`url`・`fetchedAt`・`rows` は同じ時点の組（§5.9 と同じ 1 回の保持の中で読む）
- 進み具合は state の `set_selection.{phases,sets,stations} = {status, error, done, total, failed, revision}` で WS に届く。`revision` は取得を受け付けるたび・止めるたび・大会を変えるたびに進む。読み直すのは `revision` / `status` が変わったときでよい
  - `done` / `total` は `sets` のページ数（`total` 0 は「まだ分からない」= 窓の不定のバー）。`failed` は `done` の答えから欠けているもの（取れなかったページと読めなかったセットの数。窓は表示しないが、ページは出してよい）
  - `error` は `fetch_failed` だけ。**行が空の `done` は「一覧に何も無い」、`error` は「取得の失敗」**（窓はどちらも空の表になる）
- `rows` は最後に `done` で終わった取得の行（`error` は前の行を残す。`:cancel` の後も残る）。**今読み込んでいる大会の結果のときだけ**返す。それ以外は `url: ""`・`fetchedAt: null`・`rows: []`（`sets` は `query: null` も）
- 大会を変えると全リストが次の revision の `idle` になる。`phases` / `stations` の行は捨てる（窓も読み込みのたび・開くたびに取り直す）。**`sets` の行は URL と組で残り、同じ大会を読み直すと戻る**（窓の、全ボード共有のキャッシュと同じ。`query` と `fetchedAt` も戻るので「フェーズとチェックボックスの復元」と「N件（HH:MM 取得）」が描ける）。枠は 1 つで、別の大会の取得が終わると入れ替わる
- 行は決まったキーだけで、実名・SNS・キャラは含まない（だから READ。§2.2）。ゲームを変えても行は変わらない
- ページが窓と同じ表示にするための規則（窓の処理はどれもクライアント側の仕事）:
  - 列は Stream / Station / Phase / Pool / Match（`round`）/ Player 1 / Player 2。Player の文字は `p1.name`・`p2.name`。**`p1.members` が 2 人以上なら両方のチームに「 (A, B)」を足す**（判定は `p1` だけを見る。カッコは翻訳の punctuation）
  - 「完了したペアのみ」は `p1.name` と `p2.name` がどちらも空でない行
  - 検索は大文字小文字を無視した部分一致で、全列と `id`（打った文字のまま。前後の空白も含む）。列の並べ替えは文字列で（窓はヘッダを押すまで読んだ順。押すと大文字小文字を区別するコード単位の比較で、同じ値は読んだ順のまま）
  - 選択なしの OK / Enter は先頭の行を読む（行が無ければ何も読まない）。読むのは §7.1 の `:load-set`、走っている取得は `sets:cancel` で止める（窓は選択時に止める）
  - React の SCORE ページ（ADR-005 S4ab）はこの規則で描く。規則は `web/src/ScorePage/setRows.ts`、ケースの表は `web/src/ScorePage/setRows.cases.json`（jest と `tests/test_set_selection.py` の両方が読み、core の行・2 つの窓の表と照合する）

### 5.19 `GET /api/v1/scoreboards/{n}/post`（2026-11-09 追加・ADR-005 S4w。ボード 1 / 2 は 2026-11-10・S4x）

ボードの「Xに投稿する」が組む文。ボード 1 / 2 は `AutonaActions.PostText`、ボード 3 は `CrewBattleService.PostText` が正本で、ボードのボタンも同じものを呼ぶ（ボタンは URL もこの `text` から作る）。

```json
{ "ok": true, "revision": 12, "data": {
  "text": "Cup\n[Pools] - WF\n\nAlpha (A1 / A2)\nvs\nB1",
  "missing": []
} }
```

- `missing`: ボタンが投稿を断る理由を `team1` / `team2` / `round` のこの順で。`round` は `phase` も `match` も空（空白だけも空）。**空のときだけ投稿できる**。ページはクリックの中でこれを読み、`missing` が空なら開く（`https://twitter.com/intent/tweet?text=` + URL エンコードした `text`）。翻訳した文言（Team 1 player name / Team 2 player name / Round name）はページの仕事
  - ボード 1 / 2 の `team1` / `team2`: そのチームのスロットに、タグ（`name`。空白だけは空）のある選手がいない
  - ボード 3 の `team1` / `team2`: 表示中（`memberNum` まで）で名前のあるメンバーがいないクルー
- 文は `missing` があっても返す（いないチームは `???`。ボード 3 は「チーム名」か `???`）
- ボード 1 / 2 の文（ボタンの旧 `PostToX` と同じ）: 大会名・`[phase] - match`（片方だけならその片方）・空行・チーム 1 の選手・`vs`・チーム 2 の選手・空行・配信 URL（`stream.url` があれば `🔴STREAM: <url>`）。選手はタグのあるスロットを番号順に ` / ` でつなぎ、1 人は `タグ`・` @ハンドル`（`twitter` の `@` を外して付け直す）・`(キャラ/キャラ)`（`character` の `name` を番号順）・` [Seed:N]`（seed が 0 / 空でなければ）の順
- 大会名は state の値を文字列にしたもの（数で入っていても答える）。前後の空白は、どれもボタンの旧実装と同じ: 大会名は落とさない。ボード 1 / 2 は `phase` / `match` も落とさず（`round` の判定だけ落として見る）、タグとハンドルは落とす。ボード 3 は `phase` / `match` / チーム名 / メンバー名を落とす。ボード 1 / 2 は文字列でない値も文字列にして読む（旧ボタンは例外のダイアログで止まっていた）
- ボード 1 / 2 のボタンは投稿の前に試合サムネイルをクリップボードにコピーする。画像はこのルートに無い（文だけ）。ボード 3 には画像が無い
- **反映前の値**（ボタンが読んでいたもの。デスクトップのボード 1 / 2 は編集が反映を待つ）。だから `GET /scoreboards/{n}/pending` と同じ **`PLAYERDB` 区分**（LAN の他端末はトークン必須。§2.2）。ボード 3 の文は S4w では READ だったが、同じルートを区分で分けないので S4x から同じ扱い
- `{n}` は 1 / 2 / 3（ほかは `400`）。読みではボードを構築せず、何も書かない

### 5.20 `GET /api/v1/match-names/candidates`（2026-11-09 追加・ADR-005 S4w。`?board=` は 2026-11-10・S4x）

ボード `?board=N`（1 / 2 / 3）の 3 つの編集可能コンボが出す候補: `{ "phase": [...], "pool": [...], "match": [...] }`。コンボと同じ順で、先頭の空行は含まない（コンボは候補にない文字も打てる）。`core/MatchNameCandidates.Candidates(board)` が正本で、ボードのコンボ（1 / 2 / 3 とも）も同じものを読む。READ。ボードを構築せず、何も書かない。`board` がちょうど `1` / `2` / `3` でなければ `400`（` 1`・`+1`・`01`・全角の `１` も。2026-11-10 の S4x レビューまでは `int()` が通すものを受けていた）

- 用語の候補は `AutonaLocaleHelper.Get{Phase,Pool,Match}NameCandidates`（phase の用語・`group` の雛形の 4 プール・ラウンド名のラダー）。`GET /match-names` は用語そのまま（キーつき・テンプレートつき）で、`match` のラダー（`Winners TOP24` など）はそこに無い
- **ボード 1 / 2 は読み込んだ大会の名前を前に足す**: 複数グループのフェーズの実際のプール名（出てきた順・重複なし。グループが 1 つのフェーズは足さない）を `pool` の前に、参加人数の 1 回戦（`Winners TOP{n}` / `Losers TOP{n}`。用語 `winners_top_n` / `losers_top_n`。組めない雛形はその 1 つを足さない）を `match` の前に。用語がすでに出している名前は足さず、用語の位置のまま。大会を解除すると消える。provider の 2 つの通知（`tournament_data_updated` / `tournament_phases_updated`）を `bootstrap_core()` が core に渡すので、ヘッドレスでも同じ。§5.18 の `POST /tournament/phases:refresh` が取ったフェーズも、取得が `done` で終わったときにプール名になる（S4x レビュー。止めた取得・遅れた答え・失敗は渡さない。デスクトップのコンボはそれを知らされず、provider の通知で読み直すまで前のリストのまま）。ボード 3 と `board` なしは用語だけ
- **Web のスコアボードページ（`/scoreboard`。board 1 / 2 の `MatchInfoBar`）の Phase / Pool / Match もこれを読む**（2026-11-10）。`?board=` 付きで、コンボを開くたびに読み直す（大会を読み込んだ後に開けば大会の名前が出る。遅れて届いた古い答えは捨てる）。それまでは `GET /match-names` の dict を Autocomplete の候補に渡しており、欄をクリックするとページ全体が落ちていた。`GET /match-names` の形は外部の呼び手のために変えていない
- S4x より前の board 1 / 2 の Qt のコンボとの違い（いまは同じリストを描くだけ）: phase は `indicator` を含むキーを除く（同梱の用語には無い）。大会の名前は大会を読み込んだ後に構築したボードにも出る（旧: 構築済みのボードだけが通知で足していた）。前の大会の名前を選んでいた欄は、その名前を文字として残す（旧: 項目として残した）
- **壊れた用語でも答える**: 利用者の用語ファイル（`user_data/tournament_terms.json`。同梱の用語をキーごとに上書きする）でリストが組めないときは、そのリストを `[]` にする（雛形（`group` / `winners_top_n` / `losers_top_n` / `round`）が文字列でない・`.format` で埋められない（`{1}`・`{x}`・`{0.x}`・`{0[x]}`・閉じない `{`）、`phase` / `match` の用語が dict でない）。文字列でない用語は候補にしない

### 5.21 `GET /api/v1/battle-format`（2026-11-10 追加・ADR-005 S4x）

いまの対戦形式と、それが表示するボード: `{ "format": "singles" | "team" | "crew", "board": 1 | 2 | 3 }`。`AutonaActions.ResolveBattleFormat`（`scoreboard.battle_format`。無ければ旧 `scoreboard.team_mode`）が正本で、`board` は `GET /health` の `activeBoard` と同じ。設定なので `revision` は付かない。READ。切り替えは §6.40

**通知 `battle_format`（Socket.IO。2026-11-10 追加・ADR-005 S4z）**: 形式が変わると `{}` を送る。**値は運ばない**ので、通知のあとに始めた `GET /battle-format` の答えを使う。接続の直後は通知が来ないので最初に 1 回読む。

- どこで切り替えても出る: デスクトップのサイドバー・v1 の `PUT /battle-format`・ほかのクライアント。ヘッドレスでも出る（`AutonaActions.battleFormatChanges` → Bridge の `battle_format_changed` → `WebServer.on_battle_format_change`）
- **いまの形式への切り替え（`changed: false`）では出ない**（`SetBattleFormat` が何も知らせない）
- `program_state_update` と同じ flusher が、同じ送信の delta・`scoreboard_pending`・`scoreboard_follow` の後に送る。`scoreboard_follow` と同じく自分の間隔は持たない（開いている窓があれば合流し、無ければすぐ出る）。1 回の送信の間に何度切り替わっても 1 通で、delta の間隔は動かさない
- 送信先は全接続（読みが READ で、通知は値を運ばない）

### 5.22 `GET /api/v1/settings`（2026-11-11 追加・ADR-005 S4ae。`defaults` と `shift_only` は 2026-11-13・S4af）

設定ページ（`/web/settings`。ADR-005 S4af。それまでは Qt の設定ウィンドウ）の設定（一般・ネットワーク・言語）とホットキー。**返すキーは許可リスト**（`core/AppSettings.SETTINGS` と `core/HotkeyRules.DEFAULT_KEYS`）で、アクセストークン・ペアリングコード・その他の settings.json のキーは返さない。だから READ。設定なので `revision` は付かない。最上位に `loadFailed`（`settings.json` を読めなかった起動か。起動時に 1 秒弱読み直してもまだ開けなかったときだけ真で、値は既定を返す。どこかの保存がファイルを読み直す・壊れたファイルを退避すると false に戻る: §6.44）と `movedAside`（壊れた `settings.json` をこの起動で退避した先のファイル名。`settings.json.unreadable-<日時>.bak`。パスは返さない。退避していなければ `null`。起動時の退避も保存のときの退避も。設定ページの案内用）が付く

```json
{
  "loadFailed": false,
  "movedAside": null,
  "general": {
    "profanity_filter": true, "disable_autoupdate": true,
    "disable_scoreupdate": false, "disable_export": false,
    "disable_overwrite": false,
    "team_1_default_color": "#fe3636", "team_2_default_color": "#2e89ff"
  },
  "network": {
    "port": 5107, "listenLan": null,
    "listening": {"port": 5107, "lan": false},
    "restartPending": false
  },
  "language": {
    "program": "default", "gameAsset": "default", "fgTerm": "default",
    "choices": [{"code": "ja-JP", "name": "日本語", "englishName": "Japanese"}],
    "restartPending": false
  },
  "hotkeys": {
    "enabled": true,
    "keys": {"load_set": {"value": "Ctrl+Shift+O", "default": "Ctrl+Shift+O",
                          "valid": true, "warning": null}},
    "vocabulary": {"modifiers": ["Ctrl", "Alt", "Shift"],
                   "keys": ["A", "...", "Right"]},
    "applied": true
  },
  "defaults": {
    "general": {"profanity_filter": true, "disable_autoupdate": true, "...": "..."},
    "network": {"port": 5107, "listenLan": false},
    "language": {"program": "default", "gameAsset": "default", "fgTerm": "default"},
    "hotkeys": {"enabled": true}
  }
}
```

- `general`: settings.json の名前のまま。保存が無い・型が違うときは既定（読み手の既定と同じ。`disable_autoupdate` は true）
- `network.port`: 保存値。範囲外の保存値（旧ウィンドウは 99999 まで許した）もそのまま返す。`listenLan`: 保存値。**null は未決定**（デスクトップが次の起動で聞く。サーバーはこの PC だけで待ち受ける）。`listening`: サーバーが実際に待ち受けているポートと LAN（無ければ null）。`restartPending`: 保存値が `listening`（無ければ起動時の値）と違う。LAN は null を false として比べる
- `language`: `"default"` か `choices` の `code`。`restartPending`: 起動時の値と違う（言語は再起動で効く）
- `hotkeys.keys`: 全アクション。`value` は保存値（無ければ既定。文字列でない保存値は null）、`valid` は登録が読めるか（`""` = ショートカットなしは true。旧ウィンドウの記録が作った `Esc` / `PgUp` などは false で、登録されていない）、`warning` は `os_reserved` / `obs_likely` / `no_modifier`（修飾キーなしの 1 キー。F1〜F24 は除く）/ `shift_only`（修飾キーが Shift だけ。どのアプリで打った大文字・記号・Shift+矢印の選択も Autona に取られる。F1〜F24 は除く。2026-11-13）/ null（優先順は os_reserved → obs_likely → no_modifier / shift_only。警告は（修飾キー, VK）で引くので `Ctrl+Escape` も `os_reserved`）。文字列でない保存値でも落ちない。**オブジェクトのキーの順は保たれない**（JSON はソートされる）ので、表示順はクライアントが決める
- `hotkeys.vocabulary`: PATCH が受けるショートカットの部品（Ctrl / Alt / Shift と、キー 1 つ）。`applied`: このシェルが OS に登録するか（デスクトップ true、ヘッドレス false）
- `defaults`（2026-11-13）: 一般・ネットワーク・言語の各項目と、ホットキーの有効のスイッチの既定を、PATCH がそのまま受ける形で（`core/AppSettings.Defaults()` と `core/HotkeyRules.Defaults()` の `hotkeys.enabled`。設定ページの「デフォルト」が送る値）。`listenLan` の既定は false（保存が無い = null は「未決定」で、PATCH は真偽しか受けないため。設定ページは未決定をデフォルト扱いにし、「デフォルト」で false を書かない）。ショートカットの既定は `hotkeys.keys.<action>.default`

### 5.23 `GET /api/v1/status`（2026-11-14 追加・ADR-005 S-final SF-2）

アプリが実行中の長い仕事（start.gg の取得・ゲームアセットのロード・Top 8 / 大会リザルトの取り込み）。デスクトップのフッターと同じ源（`core/StatusService`）。state ではないので state の `revision` は付かない（`data.revision` はこの表の版）。READ（コードと件数と、ロード中のゲームの codename だけ）

```json
{ "ok": true, "data": {
  "revision": 12,
  "jobs": [
    {"id": 3, "code": "startgg_entrants", "args": {}, "done": 0, "total": 0},
    {"id": 4, "code": "assets_load", "args": {"codename": "ssbu"}, "done": 10, "total": 80}
  ]
} }
```

- `jobs`: 始まった順。**最後の 1 つがフッターに出す仕事**。何も走っていなければ `[]`。`total` 0 は「進み具合を測れない」（文言だけ）、それ以外は `total` のうち `done` が済んだ
- `revision`: 変わるたびに増える。プロセスごとに 0 から数え直す（再起動で戻る）
- **文言はコードと引数**で、読み手が自分の言語に訳す（英語の UI に日本語を出さないため）。サーバーは文を返さない。コードと引数（`core/StatusService.CODES` が正本。デスクトップの訳は `AutonaLocaleHelper.STATUS_TEXT` と `src/i18n/*.ts` の `status` コンテキスト、Web は i18next の `status_<code>`。表のずれは `tests/test_status_service.py` が検査）:

| `code` | `args` | 日本語 | English |
|--------|--------|--------|---------|
| `startgg_bracket` | — | Start.gg からブラケット情報を取得中... | Fetching bracket data from Start.gg... |
| `startgg_set` | — | Start.gg からセット情報を取得中... | Fetching set data from Start.gg... |
| `startgg_sets` | — | Start.gg からセット一覧を取得中... | Fetching the set list from Start.gg... |
| `startgg_sets_page` | `page`, `pages` | Start.gg からセット一覧を取得中... (page/pages) | Fetching the set list from Start.gg... (page/pages) |
| `startgg_sets_failed` | `failed`, `pages` | セット一覧の一部ページ取得に失敗しました (failed/pages) | Some pages of the set list could not be fetched (failed/pages) |
| `startgg_entrants` | — | Start.gg から参加者情報を取得中... | Fetching entrants from Start.gg... |
| `startgg_entrants_page` | `page`, `pages` | Start.gg から参加者情報を取得中... (page/pages) | Fetching entrants from Start.gg... (page/pages) |
| `assets_load` | `codename` | ゲームアセットを読み込み中... (codename) | Loading game assets... (codename) |
| `top8_import` | — | TOP 8 を構築中... | Building the TOP 8... |
| `result_import` | — | リザルトを取得中... | Fetching the results... |

  知らないコード（新しいアプリと古いクライアント）はコードそのものを出す

  `assets_load` はゲームアセットのジョブ（§5.28。ADR-005 S-final SF-3b）も使う: `codename` はダウンロードならゲームの code、取り込みなら取り込むゲーム名（複数は `, ` でつなぐ）。`done` / `total` はダウンロードなら KiB、取り込みならエントリ / ファイルの数（専用のコードは Web の訳語が要るので SF-4b で足す）

**通知 `status`（Socket.IO。2026-11-14 追加・ADR-005 S-final SF-2）**: 上と同じ `{revision, jobs}` を運ぶ（ほかの通知と違って**値を運ぶ**: 進み具合は短い間隔で変わり、通知のたびに全クライアントが GET すると無駄なため）。

- **接続の直後に 1 回**、その接続にだけ送る（`program_state` の後）。以後は変わるたびに全接続へ
- 値は**送る時点の最新**: 1 回の送信の間に何度変わっても 1 通。`program_state_update` と同じ flusher が、同じ送信の delta・`scoreboard_pending`・`scoreboard_follow`・`battle_format` の後に送る。`scoreboard_pending` と同じく自分の静かな間隔（50 ms）を持ち、続けて変わると窓ごとに 1 通（ロードの進み具合）。delta の間隔は動かさない
- GET の答えと通知のうち `revision` の大きい方が新しい。再接続の後は（再起動で 0 から数え直すので）次に届いたものをそのまま使う（接続の直後に届く）
- ヘッドレスでも出る（`StatusService.changes` → Bridge の `status_changed` → `WebServer.on_status_change`）。ヘッドレスのゲームのロードは進み具合を出さない（start.gg の取得と Top 8 / リザルトの取り込みは出る）

### 5.24 `GET /api/v1/api-catalog`（2026-11-14 追加・ADR-005 S-final SF-3a）

API 設定のページ（`/web/api`。SF-4a-2 までは Qt の API 設定ウィンドウ）が載せているエンドポイントの一覧とエラーコード。正本は `core/ApiCatalog.py`。READ。設定でも state でもないので `revision` は付かない

```json
{
  "endpoints": [
    {"id": "score_adjust", "method": "POST",
     "path": "/scoreboards/{n}/teams/{t}/score:adjust",
     "example": "/scoreboards/1/teams/1/score:adjust",
     "description": "Adjust score by delta", "body": {"delta": 1},
     "destructive": false, "logged": true, "confirm": false}
  ],
  "errorCodes": [{"code": "INVALID_ARGUMENT", "status": 400,
                  "description": "Bad parameter"}]
}
```

- `id`: その行のルートの Flask の endpoint 名（`api_v1.` を除いたもの）。ルートが変わらない限り変わらないので、ページは文言の翻訳をこれで引く（i18next の `guide_api_endpoint_<id>`、エラーコードは `guide_api_error_<code>`。en / ja の全行に訳があることは `tests/test_api_v1_guides.py` が検査する）。一覧の中で重複しない
- `path`: 表示用（`{n}` などの置き場所つき。クエリを含むことがある）。`example`: 置き場所を既定値（`{n}`→`1` など）にしたもの。ページのテスターに読み込むパスと curl が送るパス
- `description` / `errorCodes[].description`: 英語の文言（ページは訳が無い行だけこれを出す）。`body`: 例の body（無ければ `null`）。`destructive` / `logged` / `confirm`: ページの表の列（`destructive` の行のアクション（`:clear` など）は、テスターが送る前に確認する）
- 並びはページの表の順。ガイドのページ自身のルート（§5.24・§5.25）は載せていない。`SHELL` のルート（アクセストークンの §5.29・§7、ゲームアセットの §5.28・§6.48〜§6.51・§7、シェル要求のチケットの §5.30。アプリ自身のウィンドウからしか呼べない）も載せない（`core/ApiCatalog.py` の docstring）

### 5.25 `GET /api/v1/connection`（2026-11-14 追加・ADR-005 S-final SF-3a）

API / Companion / OBS のガイドのページ（`/web/api`・`/web/companion`・`/web/obs`。SF-4a-2 までは Qt のウィンドウ）が表示しているサーバーとファイルの場所。別の PC のページ（`403`）は、ページが届いた API の URL を出し、監視ファイルは `out/` からの相対で出す。**`THIS_PC` 区分**（`out/` の絶対パスはユーザー名を含む）。`Cache-Control: no-store`

```json
{
  "port": 5107, "lan": false, "lanAtRestart": false,
  "baseUrl": "http://127.0.0.1:5107/api/v1",
  "localBaseUrl": "http://127.0.0.1:5107/api/v1",
  "outDir": "C:\\Autona\\out", "pathSeparator": "\\"
}
```

- `port` / `lan`: `WebServer.Serving()`（待ち受けている値。サーバーが無いときだけ設定）。`lanAtRestart`: LAN 接続を有効で保存したが、走っているサーバーはこの PC だけ（再起動で効く。`WebServer.LanOnAtRestart()`。ページの「保存済み・再起動で有効」）
- `baseUrl`: 利用者に案内する API の URL（LAN 接続なら この PC の LAN IP、無ければループバック。API・Companion のページの値）。`localBaseUrl`: ループバックの URL（OBS のページの値。OBS は同じ PC で動く）
- `outDir`: Autona が出力する `out/` の絶対パス（Advanced Scene Switcher のファイル条件が監視するファイルはこの下。`out/score/1/match.txt` なら `outDir` + `pathSeparator` + `score` + … ）。`pathSeparator`: その OS の区切り文字
- 秘密は含まない（アクセストークンは返さない: §2.2 の `THIS_PC` の注記。トークンは §5.29 の `SHELL` のルートだけが返す）。ただし `THIS_PC` は同じオリジンのレイアウトからの要求も通すので、`outDir` はそれらにも読める（`GET /layouts/selected/board/obs-path` と同じ程度の露出）

### 5.26 通知 `games_changed`（Socket.IO。2026-11-14 追加・ADR-005 S-final SF-3a）

ゲームの一覧をスキャンし直すと（ゲームアセットの取り込みの後・起動時のスキャン。デスクトップはアセットアダプタの `onLoadAssets`、ヘッドレスはローダーの `gamesScanned`）`{}` を送る。**値は運ばない**ので、通知のあとに始めた `GET /games` の答えを使う。接続の直後は通知が来ないので最初に 1 回読む。

- `program_state_update` と同じ flusher が、同じ送信の delta・`scoreboard_pending`・`scoreboard_follow`・`battle_format` の後に送る。自分の間隔は持たず、1 回の送信の間に何度スキャンしても 1 通で、delta の間隔は動かさない
- 送信先は全接続（`GET /games` が READ）。既存の `games` イベント（一覧の要求と答え）は変えていない

### 5.27 `GET /api/v1/assets/packs`（2026-11-14 追加・ADR-005 S-final SF-3b）

「ゲームアセットを追加」のページ（`/web/assets`。ADR-005 S-final SF-4b-2 で Qt のウィンドウから置き換え）が出すパックの一覧。上流（`joaorb64/StreamHelperAssets` の `assets.json`）の索引と、`user_data/games` にインストール済みのゲーム。**`THIS_PC` 区分**（ローカルのパスは含まないが、読むと GitHub から索引を取りにいくことがあるので、LAN の他端末やプレビューのリスナーからは起こさせない）

```json
{ "ok": true, "data": {
  "source": {"page": "https://github.com/joaorb64/TournamentStreamHelper", "index": "https://raw.githubusercontent.com/joaorb64/StreamHelperAssets/main/assets.json"},
  "index": {"status": "ready", "error": null, "fetchedAt": 1763100000},
  "games": [
    {"code": "ssbu", "name": "Super Smash Bros. Ultimate", "available": true, "installed": true, "packs": [
      {"name": "full", "size": 123456789, "version": "2", "installedVersion": "1", "installed": true, "updateAvailable": true, "default": true}
    ]}
  ]
} }
```

- 索引はこの要求のスレッドで取る（キャッシュが無い・10 分より古い・`?refresh=true`。取り直しは 5 秒に 1 回まで、失敗も数える。取得中に来た要求はその結果を使う）。取れなくても `200` で、`index.status` が `"error"`（`error` は固定のコード: `fetch_failed` = 応答が無い・HTTP のエラー、`bad_index` = JSON のオブジェクトでない。例外の文はログにだけ出す）。前に取れた索引があればそれを出し続ける。`index.status` は `idle` / `loading` / `ready` / `error`
- `games`: 索引のゲーム（`available: true`）と、索引に無いインストール済みのゲーム（`available: false`。`size` / `version` は null）。並びは `"name  (code)"` の大文字小文字を区別しない順（ウィンドウと同じ）
- `packs`: **アプリが使うパックだけ**（`base_files` / `full` / `portrait` / `stage_icon` / `variant_icon`。ADR-002）。`size` は落とすバイト数（分割ファイルの合計）、`installedVersion` はインストール済みのパックの `config.json` の `version`、`updateAvailable` は両方の版があって違うとき、`default` はダウンロードで最初からチェックされるもの

### 5.28 `GET /api/v1/assets/job`（2026-11-14 追加・ADR-005 S-final SF-3b）

最後のゲームアセットのジョブの状態（ジョブを始めるのは §7 の `POST /assets:importZip` / `POST /assets/packs:install`。ADR-005 S-final SF-4b-1）。**`SHELL` 区分**（§2.2。メッセージがこの PC のパスを含みうる。SF-3b では `THIS_PC` で、SF-4b-1 でジョブの開始と一緒にシェルの秘密の下に移した。読み手は共有ビューのページだけ）。`Cache-Control: no-store`

```json
{ "ok": true, "data": {
  "revision": 3, "status": "running", "operation": "install", "game": "ssbu",
  "stage": "download", "pack": "full", "file": "ssbu.full.7z.001",
  "done": 52428800, "total": 123456789,
  "imported": [], "skipped": [], "skippedEntries": [], "skippedEntriesMore": 0,
  "failed": [], "error": null, "message": null
} }
```

- `revision`: ジョブを始めるたびに増える（3 種類で 1 本）。プロセスごとに 0 から。最初のジョブの前は `revision: 0`・`status: "idle"`
- `status`: `idle` / `running` / `done` / `error` / `cancelled`。`operation`: `install` / `importZip` / `importFolder`。`stage`（走っている間）: `download` / `extract` / `copy` / `scan`
- `done` / `total`: ダウンロードはバイト、ZIP の取り込みはエントリの数、フォルダの取り込みはファイルの数
- `imported`: 置いたゲーム（インストールはそのゲーム）。`skipped`: インストール済みで置き換えなかったゲーム。`skippedEntries`: ZIP の取り込みで飛ばしたエントリの名前（フォルダの外を指さないが Windows で使えない名前。先頭 100 件。それを超えた数は `skippedEntriesMore`）。`failed`: `[{name, message}]`（ゲームごとの失敗。ほかのゲームは続ける）
- `error`（`status` が `error`）: `bad_index` / `download_failed` / `extract_failed` / `unsafe_archive` / `too_large` / `not_a_zip` / `disk_full` / `stopped`（終了中）/ `failed` ほか（`core/AssetPackService` の定数）。`message` は英語の理由。ZIP の取り込みは `zipfile` が読む前に中央ディレクトリを検査する（§6.48。件数・大きさ・ファイルの外を指す位置で `too_large` / `not_a_zip`）
- 読み方: `revision` が始めたときのものと同じで、`status` が `running` でなくなったら結果（`revision` が進んでいれば、そのジョブは終わっていて結果はもう読めない）。走っている間はフッター（§5.23 の `status` の `assets_load`）にも出る。終わったジョブは `status` の通知より先に結果になっている（通知のあとに読めば結果）

### 5.29 `GET /api/v1/access-token`（2026-11-14 追加・ADR-005 S-final SF-4a-1）

LAN の他端末（Companion・ASS・別 PC のブラウザ）が書き込みに使うアクセストークン（§2.2）。API 設定のページ（`/web/api`。SF-4a-2）がこれを読んで表示・コピーする（ページの state にだけ持ち、sessionStorage / localStorage・URL には入れない。ブラウザで開いたページは `403` を受けて「デスクトップアプリでこのページを開いてください」と案内する。テスターから呼んだ再発行は確認つきで、テスターの答えのトークンは伏せる）。**`SHELL` 区分**（§2.2: デスクトップの共有ビューが Web UI を出している間の、本体のサーバーのオリジンの文書（メインフレームと同じオリジンのフレーム）の fetch / XHR だけ。トークンでは開かない・LAN・同じオリジンのレイアウト・OBS・curl・ヘッドレスは `403 FORBIDDEN`）。`Cache-Control: no-store`

```json
{ "ok": true, "data": { "token": "…", "available": true } }
```

- `token`: `WebAccess.Token()`（初回に読み、無ければ発行してメモリに持つ）。settings.json を読めなかった・値が壊れているときは発行せず `""`、`available` は `false`（そのあいだ LAN の書き込みは全部 `401`）
- 再発行は §7 の `POST /api/v1/access-token:regenerate`
- `GET /api/v1/settings`（§5.22）・`GET /api/v1/connection`（§5.25）・API 一覧（§5.24。ガイドのページ自身のルートは載せない）はトークンを返さない

### 5.30 `GET /api/v1/shell-session`（2026-11-14 追加・ADR-005 S-final SF-5a-1）

共有ビューのページがデスクトップ自身に頼む「シェル要求」に入れるチケット。ページ（`web/src/PanelShell/hostApi.ts` の `loadShellSession`。`useShellAccess({withToken: false})` が開いたときに読む）がこれを読んでモジュールの中にだけ持ち（sessionStorage / localStorage・URL・BroadcastChannel には入れない）、クリックの中で `window.open("<このサーバーのオリジン>/web/shell-request?verb=…&request=<id>&ticket=<チケット>[&<引数>]", "_blank", "noopener")` を開く。共有ビュー（`_PanelPage._OpenOutside`）はそのウィンドウを作らず、次を**すべて**満たすときだけ動詞を実行して `window.autonaPanelShell.shellResult(id, 結果)` で答える。満たさないもの・シェル要求に見える URL（デコードしたパスに `shell-request` を含み、かつ本体のサーバーのオリジン（この PC の別名・LAN アドレスの同じポートも）か、クエリに `ticket` のキーがある（どのオリジン・スキームでも））は実行も既定のブラウザへの受け渡しもせず、ログには理由の語だけを出す（URL・チケットは出さない）。それ以外の、パスに `shell-request` を含むだけの外部のリンク（`https://github.com/x/shell-request-lib` など）は普通のリンクとして既定のブラウザへ渡す

- ユーザーのクリックから開いた（`isUserInitiated`）
- このサーバーのオリジン（`127.0.0.1:<待ち受けポート>`）・userinfo なし・パスがそのまま `/web/shell-request`・フラグメントなし
- クエリがちょうど `verb` / `request` / `ticket` と動詞の引数を 1 回ずつ（512 文字まで）。`request` は `[A-Za-z0-9_-]{1,64}`、引数は決まった値のどれか
- `ticket` がこのプロセスのチケットと一致（定数時間の比較）
- 頼んだページが共有ビューの Web UI（`/web` の配下で `view=designer` でない）

動詞（SF-5a-1）: `alwaysOnTop`（`on=0|1`。メニューの「常に最前面に表示」のチェックと同じ。結果に `alwaysOnTop`）・`checkUpdates`（メニューの「更新を確認」と同じく、結果はアップデータ自身のダイアログ。答えは始めたことだけ。アップデータの確認がまだ走っている間（起動時の確認・前に頼んだ確認）は何も始まらないので `busy`）・`openExternal`（`target=discord|inquiry`。決まった表 `src/AutonaExternalLinks.py` のキーだけで、URL は受け取らない）。結果は `{ "ok": true, … }` か `{ "ok": false, "error": "<code>" }`（`bad_argument` / `not_ready` / `busy` / `open_failed` / `unknown_verb` / `unavailable` / `failed`。`unknown_verb` はシェルが知らない動詞で、共有ビューの検査（`SHELL_VERBS`）を通った動詞では起きない）。ページは 10 秒待って答えが無ければ諦める。答えは頼んだ文書にだけ渡す（凍結中は解凍まで持ち、読み込み直し・破棄で捨てる。動詞ごとに最新の 1 つだけ）。デスクトップの状態（`{ "alwaysOnTop": … }`）は読み込みのたびと変わるたびに `window.autonaPanelShell.shellState(状態)` で知らせる

**`SHELL` 区分**（§2.2。トークンでは開かない・LAN・同じオリジンのレイアウト・OBS・curl・プレビューのリスナー・別のページのフレーム・ヘッドレスは `403 FORBIDDEN`）。`Cache-Control: no-store`。API 一覧（§5.24）には載せない

```json
{ "ok": true, "data": { "ticket": "…" } }
```

- `ticket`: `WebAccess.IssueShellTicket()`。このルートの最初の答えで 1 回作り、プロセスのあいだ同じ値をメモリにだけ持つ（`settings.json` に入れない・ログに出さない・import では作らない）。シェルの秘密（§2.2）ともアクセストークンとも別の値
- 断った要求ではチケットを作らない。チケットを作るのはこのルートだけ（`tests/test_web_access.py` が AST で pin）
- 同じオリジンのフレーム（about:blank・srcdoc・blob:）は秘密を受け取るのでこれも読める。だから Web UI は第三者の HTML を sandbox なしのフレームに入れない（sandbox のフレームは opaque origin で `403`。`.claude/rules/state-and-webserver.md`）

## 6. 安全操作エンドポイント（書き込み・操作ログ対象 / confirm 不要）

**反映ゲート（2026-07-12 監査・明文化）**: ボード 1/2 への書き込み（§6.1〜6.5・§6.37・§6.41〜6.43 および §7 の
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
**唯一の例外は §6.27 の `:load`**（ADR-005 S4m）で、`{t}` はセット側のチームを指し、スワップ時に反転する（選手リストの「P1 に設定」と legacy `load-player-from-tag` の意味のまま）。

### 6.1 スコア加算 `POST /api/v1/scoreboards/{n}/teams/{t}/score:adjust`
- パス: `n`=スコアボード番号, `t`=1|2
- body: `{ "delta": 1 }`（`-N`〜`+N` の整数。±1 が基本）
- 委譲: `team_scoreup`/`team_scoredown`（delta 符号で分岐、|delta| 回 emit）= #3/#4
- 冪等: `Idempotency-Key` 対応（§2.9）
- エラー: `INVALID_ARGUMENT`（t/delta 不正）

### 6.2 スコアリセット `POST /api/v1/scoreboards/{n}/score:reset`
- body: なし
- 委譲: `reset_scores` = #30
- board 3（ADR-005 S4w で開いた）: ボードの「Reset Stocks」と同じ `CrewBattleService.ResetStocks`。全メンバーを既定のストック（3）に戻し、出場中を無しにする。名前は残す。即時公開。同じことをもう一度送っても書かない。未構築のボードは構築せず何も書かない（§2.12）

### 6.3 チーム入替 `POST /api/v1/scoreboards/{n}:swap-teams`
- body: なし
- 委譲: `swap_teams` = #21 → `AutonaActions.SwapTeams`（core。ADR-005 S2c〜）
- 入れ替えるもの: 選手スロット全体（キャラのバリアント・mains・id・city・アバター・スポンサー画像を含む）・
  スコア・LOSERS・チーム名・チームロゴ。**チームカラーは入れ替えない**（色は列のもの）。
  `score.{n}.teamsSwapped` を反転する（セット読込がどちらの列に第 1 エントラントを置くかの正本）。1 回の保存サイクル。
- 両シェルともリクエストのスレッドで、ボードホストのロックの下で同期実行する（ADR-005 S4ad: §2.12。S2f〜S4ad の
  デスクトップは `@gui_thread_sync` で GUI スレッドで同期実行していた。選手カードの入力確定が GUI スレッドで
  2 書き込みに分かれ、その間に入らないように）。応答時点で入替は完了している（旧実装は queued シグナルで、入替前に `OK` を返していた）。
- held 対象（反映待ち）。
- 経緯（どれも今は何もしない）: S4ac-3 まではデスクトップが入力中の欄を先に確定していた（フォーカスを外して Qt に確定させ、
  打ちかけの文字はその選手と一緒に動いた。SCORE ページは欄を確定してから依頼する）。S4ac-1 までは未構築のボードを
  構築してから入れ替えていた（未構築ボードは held 登録されていないので、構築前に入れ替えると即時公開されたうえ、
  構築時のシードで消えた）。ボードは起動時に 3 つとも作るので、未構築のボードは無い（§2.12）。
- 旧ルート `/scoreboard{n}-swap-teams` / Socket.IO `swap_teams` も同じ Action。
- board 3（v1 は ADR-005 S4w で開いた）: `CrewBattleService.SwapTeams`。チーム名・**チームカラー**（ボード 1 / 2 と違い、色はクルーと一緒に動く。ボードのスワップが常にそうしてきた）・全メンバーの名前・ストック・出場中を入れ替え、`score.3.teamsSwapped` を反転する。1 回の保存サイクルで即時公開（held でない）。入力中の欄の確定は S4ac-3 まで（Qt のクルーボード）。フラグはボード 1 / 2 と同じキーなので delta で届く。一度もスワップしていないボードにキーは無い（`false` と読む）。クリアやリセットでは戻らない

### 6.4 チームカラー `POST /api/v1/scoreboards/{n}/teams/{t}/color`
- body: `{ "color": "#FF0000" }`（`#` 付き hex）
- 委譲: `team_color` = #5
- エラー: `INVALID_ARGUMENT`（色形式不正）
- board 3（ADR-005 S4w で開いた）: 色ボタンと同じ `SetTeamColor`。ボードの色の持ち方 `#rrggbb` に揃えて書く（`#F0A` → `#ff00aa`。ボード 1 / 2 は送られた綴りのまま）。今の色と同じなら書かない。未構築のボードは構築してから書く。既定の色に戻す（ボタンの右クリック）は `#fe0000` / `#0071bc` を送る

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
- **board 3（ADR-005 S4w で開いた）**: 受けるキーは `phase` / `pool` / `match`（文字列。**空文字でクリア**。コンボの確定と同じ `SetMatchField`）・`bestOf`（整数 0〜99）・`players`（整数 1〜9。`SetMemberCount`。両チームの `memberNum` と合計を書く）・`teamName`（`{team: 1|2, value: 文字列}`。名前欄と同じ `SetTeamName`）。全部を 1 保存サイクルで即時公開する。値はボードの入力欄が持てる範囲だけで、**丸めずに断る**（legacy の `set_route` は丸める）。`null`・bool・小数・文字列の数・未知のキー・`teamName` の中の `team` / `value` 以外のキー・`characters` / `losers`（クルーに無い）・空の body は `400` で、1 つでも不正なら何も書かない（ボードも構築しない）。同じ値は書かない。未構築のボードは構築してから書く。board 1 / 2 の `teamName` は中のほかのキーを読まない（従来どおり）

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
  （ADR-005 S3e-4。旧ルートの選手書き込みの部分だけ。S3e-3 までは Qt のボードの `ApplyPlayerData`）= #11。
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
- デスクトップは未構築のボードを先に構築し（held になる）、GUI スレッドで同期実行する（S4ac-3 から構築は無く、
  S4ad から両シェルともリクエストのスレッドでボードホストのロックの下: §2.12）。held 対象のため `revision` は
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

### 6.10 実況・解説者の欄 `PATCH /api/v1/commentary/{slot}`（2026-10-31 追加・ADR-005 S4b）
- `slot`: 1〜12（範囲外は `INVALID_ARGUMENT`）
- body（部分更新・1 つ以上）: `{ "name": "…", "team": "…", "twitter": "…", "role": "…", "icon": "mic", "note": "…", "display": true }`。
  `display` は真偽値、他は文字列。`icon` は空文字か §5.4 の `icons[].id`。未知キー・型違いは `INVALID_ARGUMENT`
- 委譲: `update_caster` → 欄ごとに `CommentaryService.SetField`（実況パネル（React 版）と同じ関数。1 つの保存サイクル）
- **全キーを先に検証**し、1 つでも不正なら何も書かない
- **表示の上限**: 他のスロットで既に 4 人（`maxDisplay`）が表示中のとき `display: true` は `409 CONFLICT`。
  同じリクエストの他の欄も書かない（判定と書き込みは `StateManager.lock` の中で行う）
- held 対象外のため即時公開され、`revision` は正確。値が変わらなければ公開されない
- 操作ログはキー名のみ（§2.7。名前・Twitter は個人情報）
- エラー: `INVALID_ARGUMENT`（slot・空 body・未知キー・型・icon）、`CONFLICT`（5 人目の表示）

### 6.11 実況・解説者の並べ替え `POST /api/v1/commentary/reorder`（2026-10-31 追加・ADR-005 S4b）
- body: `{ "order": ["3", "1", "2", "4", …] }` — スロット 1, 2, … の順に「そこへ移す元のスロット」。1〜12 を 1 回ずつ（数値でも可）
- 委譲: `reorder_casters` → `CommentaryService.Reorder`（デスクトップのカードのドラッグと同じ）。スロットの中身ごと入れ替わる
- エラー: `INVALID_ARGUMENT`（配列でない・真偽値を含む・1〜12 の並べ替えになっていない）

### 6.12 選手 DB からの実況者の読込 `POST /api/v1/commentary/{slot}/player`（2026-10-31 追加・ADR-005 S4b）
- body: Socket.IO `playerdb` の選手と同じ形のオブジェクト（名前補完で選んだ選手）。`gamerTag` / `prefix` / `twitter` /
  `custom_textbox` がそれぞれ `name` / `team` / `twitter` / `note` に入る（`CommentaryService.ApplyPlayerData`。悪語フィルタ込みで、
  旧 `load-commentator-from-tag-*` / `update-commentary-*` と同じ結果）。他のキーは無視し、`role` / `icon` / `display` は変えない
- 上の 4 キーのどれかが文字列であること（無ければ `INVALID_ARGUMENT`）。文字列・`null` 以外は `INVALID_ARGUMENT`
- 操作ログはキー名のみ（§2.7）

### 6.13 デザインの選択 `PUT /api/v1/layouts/selected/{kind}`（2026-10-31 追加・ADR-005 S4b）
- body: `{ "id": "commentators" }`（§5.2 の `id`。その `kind` の一覧にあるもの）
- 委譲: `select_layout` → `core/LayoutCatalog.SelectLayout`。`settings.json` の既存キー（`commentary.selected_design` /
  `top8.selected_design` / `board.selected_design`）に書くだけで形式は不変。すでに保存されている id なら書かない（表示中のデザインは代わりかもしれないので、Web UI はそれを選び直したときも送る）
- 応答の `data` は選んだエントリ（§5.3 と同じ形）。設定なので `revision` は付かない
- エラー: `INVALID_ARGUMENT`（`kind` が §5.3 以外・`id` が文字列でない / 一覧に無い）

### 6.14 機能別カラー `PATCH /api/v1/colors/{feature}`（2026-10-31 追加・ADR-005 S4b）
- `feature`: `core/ColorService.FEATURES` のいずれか（実況・解説は `commentary`）。未知は `INVALID_ARGUMENT`
- body: `{ "theme": "#112233", "accent": "#445566" }`（どちらか・両方。`#RGB` / `#RRGGBB`、大文字小文字どちらも）。
  **`#rrggbb`（小文字 6 桁）に揃えてから保存する**（Qt の色ダイアログ `QColor.name()` と同じ形。S4g レビュー L13）
- 委譲: `set_feature_colors` → 種類ごとに `ColorService.SetFeatureColor`（デスクトップの機能別カラーのカードと同じ。
  設定と `colors.{feature}.*` の両方に書く。1 つの保存サイクル）。オーバーレイは `colors.{feature}` を大会のカラーより優先する
- 「大会のカラーに合わせる」はクライアントが大会の 2 色を両方送る（Qt のカードと同じく明示的に書く）
- エラー: `INVALID_ARGUMENT`（feature・未知キー・空 body・色形式）

### 6.15 配信先・配信タイトル `PUT /api/v1/stream`（2026-10-31 追加・ADR-005 S4e）
- body: `{ "url": "https://...", "title": "..." }`（どちらか・両方。文字列）
- 委譲: `update_stream` → `AutonaActions.SetStreamUrl` / `SetStreamTitle`（同値なら書かない。1 つの保存サイクル）
- タイトルは送られたものを書く。打ちかけを自動生成のタイトルの上に送るかはクライアントが決める（`StreamService.TypedTitleWins`。
  配信設定ページは同じ規則の `web/src/StreamPage/titleRule.ts`）。空にすると、次に大会名・ゲーム・参加者が変わったときに自動生成に戻る
- エラー: `INVALID_ARGUMENT`（空 body・未知キー・文字列でない）

### 6.16 配信チャンネル `POST /api/v1/stream/channels`・`PUT` / `DELETE /api/v1/stream/channels/{index}`（2026-10-31 追加・ADR-005 S4e）
- `POST` body: `{ "name": "...", "url": "..." }`（両方とも空でない文字列。前後の空白は落とす）。末尾に足し、`data` は
  `{ "index": 足した添字, "channels": [...] }`。設定だけなので `revision` は付かない（配信先にするのはクライアントが §6.15 で送る）
- `PUT` body は `POST` と同じ。`DELETE` は body なし。`data` は `{ "channels": [...] }`。配信先（`stream.url`）が
  そのチャンネルの URL だったら、編集では新しい URL に、削除では空になる（core の規則）ので `revision` が付く
- 委譲: `add_stream_channel` / `update_stream_channel` / `delete_stream_channel` → `StreamService.AddChannel` / `UpdateChannel` / `DeleteChannel`
- エラー: `INVALID_ARGUMENT`（空・未知キー・文字列でない）、`NOT_FOUND`（その添字のチャンネルが無い）

### 6.17 配信団体の画像 `PUT /api/v1/stream/org/{kind}`・`DELETE /api/v1/stream/org`（2026-10-31 追加・ADR-005 S4e）
- `kind`: `icon`（`layout/stream_org_icon.png`。長辺 512）/ `thumbnail`（`layout/stream_org_thumbnail.png`。長辺 1920）
- `PUT` body: `{ "image": "data:image/png;base64,..." }`。ファイルをそのまま（縮小は core の PIL）。data URL の型は見ない
  （ブラウザがファイル名から付ける推測で、知らない拡張子なら `application/octet-stream`。S4e レビュー）。受け付けるのは
  core が読める画像（PNG / JPEG / MPO / BMP・5000 万画素まで）だけ。保存すると版（`stream.orgIconVersion` / `orgThumbVersion`）が publish されるので `revision` が付く
- 上限: 画像 20MB（20 × 1024 × 1024 バイト）。本文は base64（4/3 倍）+ 1KB まで。超えると `413 PAYLOAD_TOO_LARGE`
  （`Content-Length` で判定し、本文は解析せずに読み捨てる）。操作ログには本文のキーだけを残す
- `DELETE`: 両方の画像を消して版を 0 にする（未登録）。背景が団体サムネ（`source: org`）だったら大会バナーに戻すので、
  `data` は `{ "thumbnailOptions": {...} }`（§5.6 の形）
- 委譲: `import_stream_org_asset` / `reset_stream_org_assets` → `StreamService.ImportOrgAsset` / `ResetOrgAssets`
- エラー: `INVALID_ARGUMENT`（未知の `kind`・base64 の data URL でない・base64 でない・空・読めない画像）、`PAYLOAD_TOO_LARGE`、
  `INTERNAL`（保存できない。ほかのアプリがファイルを開いている など）

### 6.18 配信サムネ生成のオプション `PUT /api/v1/stream/thumbnail/options`（2026-10-31 追加・ADR-005 S4e）
- body: §5.6 のキーの一部（`source` / `style` / `overlayOrgIcon`）。`data` は保存した全キー（§5.6 の形）。設定なので `revision` は付かない
- 委譲: `set_stream_thumbnail_options` → `StreamService.SetThumbnailOptions`（値が変わらなければ `settings.json` を書かない）
- エラー: `INVALID_ARGUMENT`（空 body・未知キー・値）

### 6.19 大会の詳細 `PATCH /api/v1/tournament/info`（2026-11-01 追加・ADR-005 S4g）
- body: `{ "tournamentName": "...", "eventName": "...", "address": "...", "numEntrants": 64, "startAtDate": "2026-11-03" }` の一部
  （キーは `StateSchema.TOURNAMENT_INPUT_KEYS`）。文字列 3 つ・`numEntrants` は 0〜999999 の整数（bool は不可。start.gg が
  返す参加人数が入る範囲。大会情報ページの欄も同じ。S4g レビュー L7）・`startAtDate` は `YYYY-MM-DD`（半角数字。
  2000-01-01〜2999-12-31）
- 委譲: `update_tournament_info` → `TournamentInfoService.SetTextField` / `SetNumberField` / `SetDate`（1 つの保存サイクル）。
  開催日は `tournamentInfo.startAt`（PC の短い日付表記。今までどおり）と `startAtDate` に出る。日付は空にできない
- 次に大会を読み込むと、大会の値で上書きされる（今までどおり）
- `"displayTournamentName": "..."`（2026-11-09 追加・ADR-005 S4u）: SCORE パネルの表示大会名（`TournamentInfoService.SetDisplayName`）。`TOURNAMENT_INPUT_KEYS` ではない。`tournamentName` の変更は、現在値が空か直前の大会名と同じときだけ表示大会名に追従する（`FollowDisplayName`）ので、それ以外の値を書けば以後の `tournamentName` の変更では上書きされない。大会の選び直しでは上書きされる（`ApplyDisplayNameFromSelection`）。`tournamentName` と同時に送ると、body のキーの順によらず表示大会名の値が勝つ
- エラー: `INVALID_ARGUMENT`（空 body・未知キー・型・範囲。この PC が時刻にできない日（`startAtDate` を
  その日の 0 時の unix 秒にできない）も 400 で、**ほかの欄も書かない**。S4g レビュー L4）

### 6.20 大会カラー `PATCH /api/v1/colors/tournament`（2026-11-01 追加・ADR-005 S4g）
- body: `{ "theme": "#112233", "accent": "#445566" }`（どちらか・両方。`#RGB` / `#RRGGBB`。§6.14 と同じく `#rrggbb` に揃えて
  保存する）、または `{ "reset": true }`（単独で。
  Autona の既定の 2 色 `#181B21` / `#f0a500`）。§6.14 の `{feature}` より先に一致する（`tournament` は機能名ではない）
- 委譲: `set_tournament_colors` → 種類ごとに `ColorService.SetTournamentColor`（`settings.json` の `general.*_color` と
  `tournamentInfo.themeColor` / `accentColor`。**同じ種類の機能別カラーの上書きを全部消す**。1 つの保存サイクル）
- `data` は `{ "theme": ..., "accent": ... }`（書いた後の 2 色）
- エラー: `INVALID_ARGUMENT`（空 body・未知キー・色形式・`reset` が true でない / ほかのキーと一緒）

### 6.21 大会のアイコン / バナー `PUT /api/v1/tournament/media/{kind}`・`DELETE /api/v1/tournament/media`（2026-11-01 追加・ADR-005 S4g）
- `kind`: `icon`（`layout/logo.png`。長辺 512）/ `banner`（`user_data/tournament_banner.png`。長辺 1920）。どちらも PNG で保存する
- `PUT` body・上限・操作ログ: §6.17 と同じ（data URL の画像。20MB・`413 PAYLOAD_TOO_LARGE`）。保存すると版
  （`tournamentInfo.logoVersion` / `bannerVersion` = ファイルの mtime）が publish されるので `revision` が付く
- `DELETE`: アイコンを Autona のアイコンに戻し、バナーを消す（バナーの版は 0 = 既定のバナー）。2 つの版を 1 つの保存サイクルで
- 委譲: `import_tournament_media` / `reset_tournament_media` → `TournamentMediaService.ImportMedia` / `ResetMedia`
- エラー: `INVALID_ARGUMENT`（未知の `kind`・data URL でない・読めない画像）、`PAYLOAD_TOO_LARGE`、`INTERNAL`（保存できない。
  ほかのアプリがファイルを開いている など）

### 6.22 Top 8 の選手 `PATCH /api/v1/top8/players/{slot}`（2026-11-04 追加・ADR-005 S4i）
- `slot`: 1〜8（範囲外は `INVALID_ARGUMENT`）
- body（部分更新・1 つ以上）: `{ "name": "…", "sponsor": "…", "country": "JP", "character": { "en_name": "Mario", "skin": 2, "game": "ssbu" } }`。
  `name` / `sponsor` は文字列。`country` は §5.8 の `code`（カスタム国旗は名前）か `null`（`""` も空にする）。
  `character` は `{ "en_name": 空でない文字列, "skin": 0 以上の整数（省略時 0。§5 `characters` の `skins` の行）,
  "game": そのキャラを選んだ一覧のゲームの codename（任意） }` か `null`。
  未知キー・型違い・`character` のほかのキーは `INVALID_ARGUMENT`
- 委譲: `update_top8_player` → `Top8Service.UpdatePlayer`（`StateManager.lock` の中で、ゲームのカタログの snapshot と国の参照を
  1 回ずつ取って全キーを解決し、1 つの保存サイクルで書く）。
  **値は core がカタログから組む**（選手カードと同じ形。ADR-005 S4h）。スキンの無いキャラは `skin` 0 で送る（値の `skin` は -1）
- **全キーを先に検証**し、1 つでも断ったら**ほかの欄も書かない**（打ち間違いで配信中の値を消さない）:
  - カタログに無い国コード・読み込み中のゲームに無いキャラ・そのキャラに無いスキン → `INVALID_ARGUMENT`
  - 国のデータの読み込み前に国を送る → `409 NOT_READY`
  - `game` が読み込み中のゲームの codename と違う（切替前のゲームの一覧で選んだ） → `409 CONFLICT`。
    Top 8 のページはキャラを送るとき必ず `game` を付け、この 409 でキャラの一覧を読み直す。省略時は照合しない
- held 対象外のため即時公開され、`revision` は正確。値が変わらなければ公開されない
- 操作ログはキー名のみ（§2.7。名前は個人情報）

### 6.23 Top 8 の表示ラベル `PATCH /api/v1/top8`（2026-11-04 追加・ADR-005 S4i）
- body: `{ "phaseLabel": "TOP 8 FINALS" }`（必須・文字列）。前後の空白を除き、空なら既定の `TOP 8`（`Top8Service.SetPhaseLabel`）
- `data` は `{ "phaseLabel": ... }`（公開した値）
- エラー: `INVALID_ARGUMENT`（`phaseLabel` が無い / 文字列でない・未知キー）

### 6.24 Top 8 のスコアの反映 `POST /api/v1/top8/scores`・全リセット `DELETE /api/v1/top8/scores`（2026-11-04 追加・ADR-005 S4i）
- `POST` body: `{ "wsf1": [2, 1], "lr1a": [0, 2] }` — 試合 id（`wsf1` `wsf2` `lr1a` `lr1b` `wf` `lqf1` `lqf2` `lsf` `lf` `gf` `gf_reset`）の
  一部と、それぞれ 0〜99 の整数 2 つ（bool は不可）。**マージ**: 送った試合だけを更新し（タイでなければ確定）、ほかの試合は今の
  スコア・結果を保つ。勝者の伝播（`Recompute`）も行う（`Top8Service.ApplyScores`。デスクトップの「反映」と同じ）。
  未知の試合・不正なスコアが 1 つでもあれば `INVALID_ARGUMENT` で何も書かない。空 body も `INVALID_ARGUMENT`
- `DELETE`: 全試合のスコアと確定を戻す（`Top8Service.ResetMatches`。「全リセット」）
- どちらも `data` は公開した `top8.matches`

### 6.25 統計オーバーレイの選択 `PUT /api/v1/stats/overlays/selected`（2026-11-05 追加・ADR-005 S4l）
- body: `{ "key": "last_sets_p2" }`（§5.10 の `overlays[].key`）
- 委譲: `select_stats_overlay` → `core/StatsOverlays.Select`。`settings.json` の `stats_preview.selected_type` に書くだけで形式は不変
- 応答の `data` は選んだ項目（§5.10 の `overlays[]` の 1 つ）。設定なので `revision` は付かない。操作ログは `stats.overlay.select`
- エラー: `INVALID_ARGUMENT`（`key` が文字列でない / 表に無い。何も保存しない）

### 6.26 選手 DB への保存 `PUT /api/v1/players`（2026-11-05 追加・ADR-005 S4m）
- 方針は**編集していない値を保存で壊さない**（2026-11-05 レビュー）。body:
  ```json
  { "prefix": "SP", "gamerTag": "Alpha", "name": "本名", "twitter": "@alpha",
    "custom_textbox": "1 行目\n2 行目", "country_code": "JP", "state_code": "13",
    "mains": [["Mario", 1, "EX"], ["Bowser", 0]], "game": "tgame" }
  ```
  - **文字列**: `null` も可（`""` と同じ）。`gamerTag` は必須（空白だけは不可）。未知のキー（§5.11 の行の `key`・`seed` を含む）は `INVALID_ARGUMENT`。
    1 行の欄（`gamerTag`・`prefix`・`name`・`twitter`・`country_code`・`state_code`）は前後の空白を取ってから検査し、途中に改行があれば `INVALID_ARGUMENT`、上限 256 文字。`custom_textbox` は改行可・空白はそのまま・上限 4096 文字。`prefix`・`name`・`twitter`・`custom_textbox` は**送らなければ空になる**（行が全部持っているので、ページは全部送る）
  - **`country_code` / `state_code` は送らなければ変えない**: キーが無ければ DB の値（解決できない地名・読めない国旗を含む）をそのまま保つ。**ページは変更したときだけ送る**（§5.11）。`""`（`null`）は明示のクリア。
    送る値はコード（`GET /countries`・`GET /countries/{code}/states`）。カスタム国旗も code。**自由文字列の地域は受け取らない**。地域だけ送ったら DB の国の地域として検査する。**国だけ変えて地域を送らなければ DB の地域は消す**（地域コードは国をまたいで重なるので、残すと別の場所を指す。同じ国を送っただけなら消さない）
  - **`mains` は送らなければどのゲームのものも変えない**。送るなら `game` が必須（無ければ `INVALID_ARGUMENT`）で、`[en_name, スキンの行, バリアントの en_name?]` のリスト（最大 100。ボードの 1 人あたりのキャラ数の上限。スキンは `-1` 以上）。`PATCH .../players/{p}` の `{ゲーム: [...]}` の形ではない。`[]` は今のゲームの mains を空にする。
    **その選手の DB の今のゲームの mains に同じ形（キャラ・スキン・バリアント）である要素は、カタログを見ずにそのまま保存する**（今のゲームに無いキャラ、スキン `-1` など。§5.11 の行のまま送れば往復する）。**新しく足す要素だけ検査する**。スキンの無いキャラはどのスキンでも受け、`-1` を保存する（カードと同じ）
  - **`game` は `mains` のゲームの codename**で、§5.11 の `game` をそのまま送る（未選択は `null`）。今のスナップショットの codename と違えば `409 CONFLICT`（`null` は未選択とだけ一致）。**ゲームが未選択で mains が空でないときも同じ**（入れる先が無い）。`mains` を送らない保存では見ない
- 委譲: `save_player` → `core/PlayerDirectory.Save`。**state には書かない**。全部送ったときの記録は `PlayerService.PrepareSaveToDB` がスロットから作るものと同じ（同じキー・同じ順・同じ値。`tests/test_player_directory.py` が golden で照合）で、`PlayerService.SaveRecordToDB` を通るので順序の保護を共有する
- 同じキー（スポンサー + タグ）の記録を**置き換える**（上の「送らなければ変えない」以外の欄は送った値になり、空の国・地域は鍵が無くなる。メモリだけのキーは落ちる）。**今のゲームの mains だけを置き換え、他のゲームの mains は残す**（DB の mains が古い行の JSON 文字列でも）。スポンサーかタグを変えた保存は別の選手として新規に作られ、**何も引き継がない**（国・地域・mains も。ページが要るものを送る）。元の記録は残る（Qt 版と同じ）。悪語フィルタは通さない（カードの手入力と同じ）
- 応答の `data`: `{ "key": "SP Alpha", "created": true }`（`created` は保存の前に DB にそのキーが無かったか。同時の `PUT` では両方 `true` になりうる）。選手 DB なので `revision` は付かない。操作ログは `player.save`（キー名のみ）
- エラー: `INVALID_ARGUMENT`（型・未知のキー・タグなし・改行・長さ・`mains` に `game` が無い・カタログに無い国 / 地域、新しく足す要素のキャラ / スキン / バリアント）、`CONFLICT`（上記の `game` / この選手のもっと新しい保存が先に済んだ）、`NOT_READY`（空でない国か地域を送ったのに国データが未読込。送らない保存は待たない）。いずれも何も保存しない

### 6.27 タグから選手を読み込む `POST /api/v1/scoreboards/{n}/teams/{t}/players/{p}:load`（2026-11-05 追加・ADR-005 S4m）
- パス: `n`=1|2（board 3 は `INVALID_ARGUMENT`）、`t`=1|2、`p`=1〜。body: `{ "tag": "Alpha" }`（gamerTag。スポンサーなし・大文字小文字を無視。最初の行）か `{ "key": "SP Alpha" }`
- **`key`**（2026-11-10 追加・ADR-005 S4z）: 選手 DB のキー（スポンサー + タグ。§5.11 の行の `key`。大会の参加者の行も同じ DB にある）で**完全一致**で読む。同じタグの選手が何人いても、選んだ 1 人を読める（`tag` は最初の行）。`key` があれば `tag` は見ない（`key` が空・文字列でなければ、`tag` があっても `400`）
- 委譲: `load_player_from_tag`（legacy `load-player-from-tag` と同じ操作。`PlayerService.ApplyData`。スロットを空にしてから読む）。ボード 1 / 2 の他の書き込みと同じく held
- **`{t}` はセット側のチーム**で、スワップ時は反対の state のチームに入る（§6 冒頭の規約の例外）
- 応答は `revision`。操作ログは `player.load`（キー名のみ）
- エラー: `INVALID_ARGUMENT`（番号・`tag` / `key` が空でない文字列でない）、`NOT_FOUND`（タグかキーが DB に無い（`no player with that tag` / `no player with that key`）/ そのチームにそのスロットが無い。state のスロットで判定するので両シェル同じ）

### 6.28 試合サムネイルの設定 `PATCH /api/v1/thumbnail`（2026-11-07 追加・ADR-005 S4o）
- body（どれか。空は `400`）: `template_id`（`type_a` | `type_b`）、`background_source`（`default` | `banner` | `custom`）、`display_phase` / `use_team_names` / `use_sponsors`（真偽）、`game: { <codename>: { flip_p1, flip_p2, smooth_scale（真偽）, zoom（1〜1000 の整数）, align: { horizontal, vertical }（1〜100 の整数） } }`。送らなかった値は変わらない（`game` と `align` は入れ子でマージ）。body は 64 KiB まで（超えたら `413`）
- `align` の `0` は受けない（テンプレートが既定の 50 / 40 として描くので、受けると送った値と絵が違う）
- **パスは受けない**（`background_path` / `main_icon_path` は `400`。描画に任意のローカルファイルを読ませないため。背景は §6.29）。codename は**今のゲーム一覧にあるものと state のゲームだけ**で、形としても `.` `/` `\` 制御文字・空・`None`・129 文字以上を断る。一覧を読む前は state のゲームだけ受ける。一覧に無いゲームの保存値は §5.12 で読めるが書けない
- 委譲: `update_thumbnail_settings` → `ThumbnailService.Update`（全部検証してから `settings.json` を 1 回だけ書く。1 つでも不正なら何も書かない。値が変わらなければ書かない）。応答 `data` は §5.12 の `settings`。state ではないので `revision` は付かない
- エラー: `INVALID_ARGUMENT`（型・範囲・未知のキー・一覧に無いゲーム）、`PAYLOAD_TOO_LARGE`（`413`）

### 6.29 試合サムネイルの背景 `POST /api/v1/thumbnail/background`（2026-11-07 追加・ADR-005 S4o）
- body: `{ "image": <base64 の data URL> }`（§6.17 と同じ検査・同じ上限。超えたら `413`）。PNG / JPEG / BMP を長辺 1920 に縮小して `user_data/thumbnail_background_<内容のハッシュ>.*` に保存し、**`background_source` を `custom` にする**（アップロードだけで効く）。前に取り込んだファイルは消す（操作者の任意のパスのファイルは消さない）
- 委譲: `import_thumbnail_background` → `ThumbnailService.ImportBackground`。応答 `data` は §5.12 の `settings`。操作ログはキー名のみ
- エラー: `INVALID_ARGUMENT`（読めない画像）、`INTERNAL`（保存の失敗）。いずれも設定を変えない

### 6.30 大会リザルトのレイアウト `PATCH /api/v1/result`（2026-11-09 追加・ADR-005 S4q）
- body: `{ "layout": "horizontal" | "horizontal_16" | "vertical" | "vertical_geki" | "team" | "team_split" }`。`settings.json` の `result_config.layout` に保存する。応答 `data` は `{ "layout": ... }`。state の `result_layout` が同じ値の写しで（S4r。対戦形式への追従でも変わる）、起動のたびに `""` へ戻る
- **パスは受けない**（`main_icon_path` / `background_path` は未知のキーとして `400`。描画に任意のローカルファイルを読ませないため）。背景はサムネイルの設定に従う（§5.12・§6.29）
- エラー: `INVALID_ARGUMENT`（未知のキー・空の body・未知のレイアウト）。body は 64 KiB まで（超えたら `413`。§6.31〜§6.34 と §7 の `result` も同じ）

### 6.31 大会リザルトの単独の行 `PATCH /api/v1/result/players/{n}`（2026-11-09 追加・ADR-005 S4q）
- `n` は 1〜16。body（どれか。空は `400`）: `rank`（1〜999 の整数）、`name` / `sponsor` / `twitter`（文字列。前後の空白は落とす。`""` で空にする）、`characters`（最大 3 要素で全体を置き換える。各要素は `{ "en_name": str, "skin": int（0 以上。省略は 0）, "game": str（省略可）}` か `null`（空。**位置を保つ**: `[A, null, B]` は 3 番目の欄に B を置く）。または変える位置だけのオブジェクト `{ "1".."3": 同じ形 }`（S4r。**名指さなかった欄は保存値のまま**で、別のゲームのキャラも残る）。リストは今のゲームで読めない欄も空で置き換える）
- 値は識別子で受け、core がカタログから組む（1 つのスナップショットで全部を解決してから書く）。**1 行で 1 保存サイクル**で、ほかの行に触らない（LAN の 2 端末の編集が競合しない）
- 委譲: `update_result_player` → `ResultService.UpdatePlayer`。応答は受付時点の `revision`。操作ログはキー名のみ
- エラー: `INVALID_ARGUMENT`（範囲外の `n`・未知のキー・型・今のゲームに無いキャラ / スキン）、`409 CONFLICT`（`game` が今のゲームでない。別のゲームで選んだキャラを書かない）。いずれも何も書かない

### 6.32 大会リザルトの団体の行 `PATCH /api/v1/result/teams/{n}`（2026-11-09 追加・ADR-005 S4q）
- `n` は 1〜8。body（どれか。空は `400`）: `rank`（1〜999 の整数）、`name`（文字列）、`members`（最大 5 要素で全体を置き換える。各要素は `{ "name": str, "twitter": str, "character": {§6.31 の形} | null }`。どのキーも省略可。空のメンバーも位置を保つ。または変える位置だけのオブジェクト `{ "1".."5": 同じ形 }`（S4r。**名指したキーだけ**を変え、ほかのキー・ほかのメンバーは保存値のまま。末尾の完全に空のメンバーは落とす））
- 委譲: `update_result_team` → `ResultService.UpdateTeam`。保存サイクル・応答・操作ログ・エラーは §6.31 と同じ

### 6.33 大会リザルトの並べ替え `POST /api/v1/result:reorder`（2026-11-09 追加・ADR-005 S4q）
- body: `{ "kind": "players" | "teams", "order": [2, 1, 3] }`。`order` は `1..長さ` を 1 回ずつ含む枠の番号（`kind` の枠数まで）で、先頭の `len(order)` 枠にその順で行を入れ、順位を 1 から振り直す。**それより後の行は変えない**（TOP 8 のレイアウトの並べ替えで 9〜16 位を崩さない）。1 保存サイクル
- 委譲: `reorder_result` → `ResultService` の `Reorder`。エラー: `INVALID_ARGUMENT`（未知のキー・`kind`・`order` の形）。何も書かない

### 6.34 大会リザルトの選手を DB へ `POST /api/v1/result:saveToDb`（2026-11-09 追加・ADR-005 S4q）
- body なし。今のレイアウトの種類の全行の選手を選手 DB に追記する（`overwrite=False`: 国・本名・ほかのゲームの mains は残る）。応答 `data` は `{ "saved": 人数 }`。選手 DB なので `revision` は付かない

### 6.35 エクスプローラで開く `POST /api/v1/layouts/selected/board:reveal`（2026-11-09 追加・ADR-005 S4s）
- body: `{ "target": "design", "designId": "<表示しているデザインの id>" }` か `{ "target": "winnerPopup" }`。§5.14 のファイルを、この PC のファイルマネージャで選択した状態で開く。**パスはカタログから解決し、クライアントからパスを受けない**。シェルを通さない（Windows は `"%WINDIR%\explorer.exe" /select,"<path>"` を文字列で起動する。Explorer はカンマで区切るのでパスは常に引用符で囲み、名前で探さない。NTFS のファイル名に `"` は入らず、入っていれば `404` で断る）
- **`THIS_PC` 区分（§2.2）**: この PC からの要求だけ。LAN はトークンがあっても `403 FORBIDDEN`（配信 PC の画面にウィンドウを開かせない）。ヘッドレスでも動く
- 委譲: `reveal_board_file` → `core/LayoutCatalog.RevealBoardFile`。state に触れないので `revision` は付かない
- エラー: `INVALID_ARGUMENT`（未知の `target`、`design` なのに `designId` が無い）、`CONFLICT` 409（`designId` が今の選択と違う: 操作者が見ていたファイルではない）、`NOT_FOUND`（ファイルが無い。デザインが無いときも。相対パスをログに残す）、`INTERNAL`（ファイルマネージャを起動できない）。どれも何も開かない
- 拒否のログには、この PC の要求と認められなかった元の理由（`forwarded` / `foreign_host` / `foreign_origin` など。トークンで通るはずだったものは `token`）を残す。応答は 1 つ

### 6.36 ゲームの行 `PATCH /api/v1/scoreboards/{n}/stages/{game}`（2026-11-09 追加・ADR-005 S4u。**2026-11-12 ADR-005 S4ac-4 で削除**）
- ゲームトラッカーと一緒に削除した（§5.16）。state の `score.{n}.stages` も無くなった（古い `state.json` に残っていても、起動時のボードの作成が `score` ごと作り直すので読まれない）。いまは `405`（同じパスの GET を静的配信が受けるため。v1 のエンベロープではない）。節の番号は欠番として残す

### 6.37 選手の上下移動 `POST /api/v1/scoreboards/{n}/teams/{t}/players:swap`（2026-11-09 追加・ADR-005 S4u）
- `{n}` は 1 / 2。`{t}` は状態側のチーム。body: `{ "first": 1, "second": 2 }`（1 以上の整数で別々の番号）
- カードの上下移動と同じ（`AutonaActions.SwapPlayers`）。入力中の欄を確定してから、スロットをキー単位で入れ替える。統計も取り直す。held 対象
- エラー: `INVALID_ARGUMENT`（型・範囲・同じ番号・board 3）、`NOT_FOUND` 404（チームにどちらかのスロットが無い。何も動かさない）

### 6.38 クルーのメンバー `PATCH /api/v1/scoreboards/3/teams/{t}/members/{m}`（2026-11-09 追加・ADR-005 S4w）
- `{n}` は 3 だけ（board 1 / 2 は `400`）。`{t}` は状態側のチーム（1 / 2。列のチームで、スワップで動くのはクルーの方）。`{m}` は 1〜9。**`memberNum` より後ろのメンバーも `name` / `stock` / `active: false` は受ける**（9 行は常にあり、`memberNum` は表示と合計の範囲を決めるだけ。下げたあとも値が残るのは Qt と同じ）。**`active: true` は表示中（`memberNum` まで）のメンバーだけ**（Qt は後ろの行をラジオごと隠すので、そこを出場中にはできない）。人数を下げると印は後ろの行に残る（Qt も同じ）ので、`active: false` で外せる。未構築のボードは構築したときの人数（シードの 3）で判定する。判定は書き込みの前の読みで、間に人数が下がったときは「印を付けてから人数を下げた」のと同じ state になる（2 つの書き込みは可換）
- body（部分更新。少なくとも 1 つ）:
  ```json
  { "name": "Player", "stock": 3, "active": true }
  ```
  - `name`: 文字列。空文字で空にする（そのメンバーのストックは合計から外れる）。`CrewBattleService.RenameMember`
  - `stock`: 整数 0〜99（ボードのスピンボックスの範囲）。丸めずに断る。`SetMemberStock`
  - `active`: `true` はこのメンバーが出場中（1 クルー 1 人なので前の人は外れる。`SelectActiveMember`）、`false` はこのメンバーが出場中でなくなる（**マークされているのが別の人なら何もしない**。`ReleaseActiveMember`。state に残るのは文字列で、出場中なしは `"0"`）
- 全部を `StateManager.lock` の 1 回の保持と 1 保存サイクルで書く（`UpdateMember`。合計 `team.{t}.score` も同じサイクル）。即時公開（held でない）。同じ値は書かない
- 未構築のボードは構築してから書く。値は操作ログにキーだけ記録する（選手の PATCH と同じ）
- エラー: `400 INVALID_ARGUMENT`（board 1 / 2・`{n}` 範囲外・`{t}` 範囲外・`{m}` が 0・JSON オブジェクトでない body・空の body・未知のキー・`null`・bool の `stock`・範囲外・小数・文字列の数・`true` / `false` でない `active`・`memberNum` より後ろのメンバーへの `active: true`。1 つでも不正なら何も書かず、ボードも構築しない）、`404 NOT_FOUND`（`{m}` が 10 以上）

### 6.39 クルーのストックの増減 `POST /api/v1/scoreboards/3/teams/{t}/members/{m}/stock:adjust`（2026-11-09 追加・ADR-005 S4w）
- パスは §6.38 と同じ。body: `{ "delta": -1 }`（整数）。0〜99 に収める（legacy の `crew_member_stock` と同じ `AdjustMemberStock`。`200` で丸める: 相対の操作は「端で止まる」のが正しいので、§6.38 の絶対値と違って断らない）
- 冪等: `Idempotency-Key` 対応（§2.9。`score:adjust` と同じ）
- 変わらなければ書かない。`delta: 0` は何もせず `200` で、ボードも構築しない。それ以外の未構築のボードは構築してから書く
- エラー: §6.38 と同じ番号の規則。`delta` が整数でない（bool・小数・文字列・`null`・無い）は `400`

### 6.40 対戦形式の切り替え `PUT /api/v1/battle-format`（2026-11-10 追加・ADR-005 S4x）
- body: `{ "format": "singles" | "team" | "crew" }`。デスクトップのサイドバーの切り替えと同じ操作（`AutonaActions.SetBattleFormat`）
- 返り値: `{ "format", "board", "changed" }`（§5.21 の形 + `changed`）と `revision`。**いまの形式と同じなら `changed: false` で何も書かない**（設定も state も・通知も。旧ルートは無く、サイドバーの同じ形式の選び直しも S4x から何もしない）
- 切り替えたとき: `scoreboard.battle_format` と旧 `scoreboard.team_mode`（チームなら true）を書き、シェルに知らせ（デスクトップはサイドバーの選択を合わせる。ADR-005 S4ad から切り替えはリクエストのスレッドで走り、サイドバーは GUI スレッドへ queued で届くので、応答の後になりうる（それまでは GUI スレッドで切り替えて応答の前に合わせていた）。SCORE ページは `battle_format` の通知で形式のボードを描き直す。S4ac-3 までは Qt のボードを未構築なら構築して表示していた）、大会リザルトのレイアウトが追従する（チームとクルーは団体のレイアウト、1v1 は単独のレイアウト。合っているレイアウトは手で選んだものを残す。`ResultService.FollowBattleFormat`。`result_layout` が state に出る）。ヘッドレスは表示するボードが無いので、設定とリザルトのレイアウトだけ
- 切り替えたときは Socket.IO の `battle_format`（`{}`）が全接続に出る（§5.21。ADR-005 S4z。S4x は「形式は設定なので通知は増やさない」としていたが、React の SCORE ページが他所での切り替えに追従するために足した）。読み手は通知の後に `GET /battle-format` か `GET /health` の `activeBoard` を読む
- エラー: `400 INVALID_ARGUMENT`（`format` が無い・文字列でない・3 つのどれでもない・JSON オブジェクトでない body。何も書かない）

### 6.41 選手の欄 `PATCH /api/v1/scoreboards/{n}/teams/{t}/players/{p}/fields`（2026-11-10 追加・ADR-005 S4z）
- デスクトップの選手カードの入力欄（タグ・スポンサー・本名・X・メモ・国・地域）に打った / 選んだのと同じ書き込み（`PlayerService.Edit`）。SCORE ページの欄の編集用。**§6.8 は選手データの読込**（DB の記録とのマージ・悪語フィルタ・空値は「変更なし」）で、こちらとは別
- パス: `n`=1|2、`t`=1|2（状態側のチーム）、`p`=1〜（state のスロット）
- body（1 つ以上。送ったキーだけを書く）: `{ "gamerTag": "Alpha", "prefix": "TM", "name": "本名", "twitter": "alpha_x", "custom_textbox": "1 行目\n2 行目", "country_code": "JP", "state_code": "13" }`
  - 値は文字列だけ（`null` は `400`）。**`""` はその欄を空にする**（§6.8 と違う）。`name` は本名（state の `real_name`）、`prefix` はスポンサー（`team`）、`gamerTag` はタグ（`name`）。メモの改行は `\n`
  - 文字の規則は §6.26 `PUT /players` と同じ（実装も同じ 1 つ: `_player_text_rule`。2026-11-10・S4z レビュー）: `gamerTag` / `prefix` / `name` / `twitter` は 1 行（改行（`\r` / `\n` / `\u2028` など）がどこにもない）で 256 文字まで、`custom_textbox` は改行を含めてよく 4096 文字まで。破れば `400`（メッセージに値は入れない）。**`PUT /players` と違い前後の空白は削らない**（カードの入力欄も削らないので、末尾の改行も 1 行の欄なら `400`）
  - `country_code`: 国のコード（カスタム国旗も。§5.8 の `code`）。`state_code`: 送った国の（送らなければ今の国の）地域のコード（`GET /countries/{code}/states` のキー）
- 書き込みはカードの入力欄と同じ:
  - 文字の欄は今の値と違うものだけを書く。**同じ値を送っても何も書かない**（反映ボタンを点けない）
  - スポンサーかタグが変わったら、表示名（`mergedName` / `mergedOnlyName`。LOSERS の ` [L]` 込み）と画像（スポンサーロゴ・アバター）を作り直し、`id` を空にする（統計が取り直される）。`online_avatar`・`city`・`seed` は残る（カードの手入力と同じ。LIVE-BUG-1）。比べるのは（スポンサー, タグ）の組
  - 今と違う国は、地域を空にしてから国を書く（コンボで選んだのと同じ順）。国と一緒にその国の地域を送ればその地域になる。別の国だけを送れば地域は空になる。同じ国・同じ地域は何も書かない
  - 悪語フィルタも選手 DB も通さない（運用者が打った文字）。1 回の lock と 1 保存サイクル
- held（§6 冒頭）。デスクトップは未構築のボードを先に構築する（**断る要求・同じ値の要求でも**構築する。構築は値の検査より前に走る。`PATCH players`（§6.8）と同じ前例。構築で held 登録される）。ヘッドレスはすぐ公開
- 応答は `revision`。操作ログは `player.fields`（キー名だけ。§2.7）
- エラー（いずれも何も書かない）: `400 INVALID_ARGUMENT`（番号・空の body・未知のキー・文字列でない値・文字の規則（改行・長さ）を破る値・国データに無い国・その国の地域でない地域（国の無いスロットの地域も））、`404 NOT_FOUND`（そのチームにそのスロットが無い）、`409 NOT_READY`（空でない国か地域を送ったのに国データが未読込。空にするだけ・国を送らない書き込みは待たない）

### 6.42 選手のキャラの行 `PATCH /api/v1/scoreboards/{n}/teams/{t}/players/{p}/characters/{row}`（2026-11-10 追加・ADR-005 S4z）
- カードのキャラ行の 3 つのコンボ（キャラ・スキン・バリアント）で選んだのと同じ書き込み（`PlayerService.EditCharacter`）
- パス: §6.41 と同じ。`row`=1〜（そのスロットのキャラ行。state のキャラ枠の数まで。**キャラ枠が 0 のスロット（キャラ数 0 の設定）には行が無く、どの `row` も `404`**。2026-11-10・S4z レビュー）
- body（`en_name` / `skin` / `variant` の 1 つ以上）: `{ "en_name": "Mario", "skin": 0, "variant": "EX", "game": "ssbu" }`
  - `en_name`: キャラの en_name（`GET /characters`）。`""` はキャラなし（ゲームが無くても書ける）
  - `skin`: スキンの行（0 から）。**送らずにキャラを変えると最初のスキン**（カードと同じ）。スキンの無いキャラは、送っても無し（`-1`。Top 8 と同じ）
  - `variant`: バリアントの en_name（`GET /variants`）。`""` / `null` は無し。**送らなければ今のバリアントのまま**（キャラを変えても。カードと同じ）
  - `game`: 選んだリストのゲームの codename（省略可）。読み込み中のゲームと違えば `409 CONFLICT`（リストを読んだ後にゲームが変わった。§6.22 と同じ）
- 今と同じ行なら何も書かない。違えば行の値を作り直して書く（他の行はそのまま）。解決は 1 回の lock の中で、ゲームの 1 つのスナップショットから
- held / ヘッドレス / 応答は §6.41 と同じ（デスクトップの未構築のボードは、断る要求・同じ値の要求でも構築する）。操作ログは `player.character`。エラーのメッセージはキャラ・スキン・バリアント・ゲームの名前を含むことがある（選手のデータではない。Top 8 §6.22 と同じ）
- エラー（いずれも何も書かない）: `400 INVALID_ARGUMENT`（番号・`row` < 1・未知のキー・型（`skin` は 0 以上の整数）・3 つのどれも無い・ゲームに無いキャラ / スキン / バリアント・ゲーム未読込でキャラを指定）、`404 NOT_FOUND`（そのスロット・その行が無い）、`409 CONFLICT`（`game` が読み込み中のゲームと違う）

### 6.43 match バー `PATCH /api/v1/scoreboards/{n}/match`（2026-11-10 追加・ADR-005 S4z）
- ボード 1 / 2 の match バー（フェーズ・プール・ラウンドの 3 つのコンボと BEST OF）を操作したのと同じ書き込み（`AutonaActions.SetMatchField` / `SetBestOf`）
- body（1 つ以上）: `{ "phase": "Top 8", "pool": "A1", "match": "Winners Final", "bestOf": 5 }`。文字列は `""` で空にする。`bestOf` は 0〜99 の整数で、0 も書く
- **§6.5 `PATCH set` との違い**: `set` はセットのペイロードとして読む（`phase` / `match` の `""` は運ばない・`pool` の `""` は空にする・`match_en` を空にする・`bestOf` の 0 は運ばない）。こちらはコンボの操作なので、**`""` は 3 つとも空にし、`match_en` に触れず、`bestOf` の 0 も書く**
- 同じ値は書かない。1 保存サイクル。held（§6 冒頭）・ヘッドレスはすぐ公開。デスクトップの未構築のボードは、断る要求・同じ値の要求でも構築する（§6.41 と同じ）
- 応答は `revision`。操作ログは `match.update`
- エラー: `400 INVALID_ARGUMENT`（`n` が 1 / 2 以外・空の body・未知のキー（`match_en` も）・文字列でない値・整数でないか範囲外の `bestOf`。何も書かない）

### 6.44 一般の設定 `PATCH /api/v1/settings/general`（2026-11-11 追加・ADR-005 S4ae）
- body: §5.22 の `general` のキーのうち 1 つ以上（部分更新）。真偽の項目は `true` / `false` だけ、色は `#rgb` / `#rrggbb`（`#rrggbb` の小文字で保存）
- **全キーを先に検証**し、1 つでも不正（未知のキー・型の違い）なら何も保存しない。今と同じ値は書かない。`qt_scoreboard_panel`（S4ac-3 で Qt の SCORE ごと削除した退路の設定）は未知のキーとして断る
- 応答: `data` は §5.22 の `general`（保存後）。`revision` は付かない
- WRITE（LAN はトークン必須）。ヘッドレスでも動く
- エラー: `INVALID_ARGUMENT`（空の body・未知のキー・型）、`NOT_READY` 409（`settings.json` を読めなかった起動で、**保存の時点でもまだ開けない**とき。何も保存しない: 書くと読めなかったファイルを置き換え、トークンも失う）
  - 読めなかった起動でも、保存の前に `settings.json` を読み直す（2026-11-11。`SettingsManager.PrepareWrite`）。今度は読めればその中身の上に保存し（同じ値かどうかもその中身と比べる）、中身が壊れていれば `settings.json.unreadable-<日時>.bak` に退避してから既定の上に保存する。どちらも 200 で、以後 `GET` の `loadFailed` は false。壊れたファイルはふつう起動時に退避されるので（`GET` の `movedAside`）、ここに来るのは起動時にまだ開けなかったときだけ

### 6.45 待ち受けの設定 `PATCH /api/v1/settings/network`（2026-11-11 追加・ADR-005 S4ae）
- body: `{ "port": 1..65535, "listenLan": true | false }` のどちらか 1 つ以上。`port` は整数（真偽・文字列・小数は不可）、`listenLan` は真偽（null は不可。未決定に戻す手段は無い）
- **再起動で効く**。応答の `data`（§5.22 の `network`）の `restartPending` で分かる。`port` だけの保存は未決定の `listenLan`（null）を保つ
- **`THIS_PC` 区分（§2.2）**: LAN はトークンがあっても `403 FORBIDDEN`
- エラー: `INVALID_ARGUMENT`、`NOT_READY` 409（§6.44 と同じ）

### 6.46 言語の設定 `PATCH /api/v1/settings/language`（2026-11-11 追加・ADR-005 S4ae）
- body: `{ "program", "gameAsset", "fgTerm" }` のうち 1 つ以上。値は `"default"` か §5.22 の `choices` の `code`
- **再起動で効く**（アプリは閉じない）。応答の `data` は §5.22 の `language`
- **`THIS_PC` 区分（§2.2）**
- エラー: `INVALID_ARGUMENT`、`NOT_READY` 409

### 6.47 ホットキーの設定 `PATCH /api/v1/settings/hotkeys`（2026-11-11 追加・ADR-005 S4ae）
- body: `{ "enabled": true | false, "keys": { "<action>": "<shortcut>" } }` のどちらか 1 つ以上（`{"keys": {}}` は何も保存しない）。ショートカットは Ctrl / Alt / Shift（各 1 回まで）と §5.22 の語彙のキー 1 つを `+` でつないだもの（大文字小文字・並び・空白は問わない。`Ctrl+Alt+Shift+Key` の順で保存）。修飾キーなしの 1 キー（F キーを除く）も保存できるが `no_modifier` の警告が付く。修飾キーが Shift だけ（F キーを除く）は `shift_only`（2026-11-13）。どちらも拒否しない。`""` はショートカットなし
- **全部を先に検証**し、1 つでも不正なら何も保存しない。今と同じショートカットは書かない
- **保存の後の集合で、要求したアクションのショートカットが別のアクションと同じキーなら `409 CONFLICT`**（メッセージに相手のアクション）。「同じキー」は（修飾キー, VK）で比べる（`Return` = `Enter`、`Esc` = `Escape`、並び順は無関係: `A+Ctrl` と `Ctrl+A` は重複。読めない値どうしは正規化した文字列で比べる）。保存していない既定値とも比べる。何も保存しない。1 つの要求で 2 つのアクションの入れ替えはできる
- OS / OBS と重なりがちなショートカットは保存し、`data.warnings`（`[{action, code: "os_reserved" | "obs_likely" | "no_modifier" | "shift_only"}]`）で知らせる。**何も書かなかったとき**（今と同じ値だけの要求）も `warnings` は返る
- 応答: `data` は §5.22 の `hotkeys` + `warnings`
- デスクトップは保存のあと、すぐ登録し直す（GUI スレッドで。`enabled: false` で全部外れる）。ヘッドレスは保存だけ（`applied: false`）
- **`THIS_PC` 区分（§2.2）**
- エラー: `INVALID_ARGUMENT`（未知のトップのキー・空・型・未知のアクション・語彙に無いキー・修飾キーの重複・キーが無い / 2 つ）、`CONFLICT` 409、`NOT_READY` 409

### 6.48 ゲームアセットの ZIP のアップロード `POST /api/v1/assets/uploads`（2026-11-14 追加・ADR-005 S-final SF-4b-1）
- 「ゲームアセットを追加」のページ（SF-4b-2）が ZIP を取り込む 1 段目（2 段目は §7 の `POST /assets:importZip`）。本文は **ZIP のバイトそのもの**（multipart ではない。`Content-Type` は見ない）
- `Content-Length` 必須（無い・chunked は `400 INVALID_ARGUMENT`）。上限は 8 GiB（`MAX_UPLOAD_BYTES` = 展開後の上限 `MAX_UNPACKED_BYTES`。超えると `413 PAYLOAD_TOO_LARGE`、`reason: "too_large"`）。どちらもルートは本文を読まずに答える。werkzeug の開発サーバーはその後、本文が届き続けるあいだ読み捨てる（最大 10 GB。ディスクには書かない。その間は要求のスレッドを占有する）ので、クライアントには `400` / `413` が届く。応答は `Connection: close`（werkzeug がどの応答にも付ける。断る答えにはルートも付ける）（§2.6）
- ヘッダ `X-Autona-File-Name`（任意）: ファイル名を UTF-8 のパーセントエンコードで（ヘッダは latin-1 しか運べない）。ZIP の最上位にゲームが直接入っているとき、そのゲームの名前になる（無ければそういう ZIP は `400`、`reason: "bad_name"`）。デコードできない・制御文字・255 文字超は `400`。ログには出さない
- サーバーは空き容量を先に確かめ（置き換える前のアップロードのファイルの大きさは空きとして数える。足りなければ読まずに `409 NOT_READY`、`reason: "disk_full"`。前のアップロードはそのとき残る）、配信しないフォルダ `user_data/.autona-uploads/` へ 1 MiB ずつ書く。届いたバイトが `Content-Length` に足りない・途中で切れた（`reason: "incomplete"`）・届いている最中に新しいアップロードに置き換えられた（`409 CONFLICT`、`reason: "superseded"`）・書けないときは、ファイルを消して断る（`Content-Length` を超えて送られた分は werkzeug が渡さないので、多いことはない）。書き終えたら ZIP を検査する: 中央ディレクトリの事前検査（`zipfile` が読む前に、終端レコード・ZIP64 のロケータとレコードだけを `struct` で読む。件数 200,000 超・ディレクトリ 64 MiB 超は `too_large`、ディレクトリがファイルの外を指す・ヘッダが壊れているのは `not_a_zip`）→ 全エントリの検査（パス・リンク・件数・展開後の大きさ）→ ゲームの検出。断るときはファイルを残さない
- 持つのは**1 つだけ**（新しいアップロードが前のものを置き換え、届いている最中のものも止める）で、**15 分**（`UPLOAD_EXPIRY_SECS`）で使えなくなる（`404 unknown_upload`）。期限切れのファイルは、次のアップロード・次の照会（取り込み・破棄。id を問わない）・次の起動で消える（タイマーのスレッドは持たない）。取り込み（§7）・破棄（§6.49）でも消える。起動時（最初のスキャンの前）に残っていたものは両シェルが消す（`RemoveStaleUploads`）
- 応答 `201`: `{ "upload": "<id>", "games": ["ssbu"], "conflicts": ["ssbu"], "expiresInSec": 900 }`。`upload` は推測できない id（`token_urlsafe(16)`）、`games` は取り込むゲーム、`conflicts` はそのうちインストール済みのもの（戻せる退避のあるゲームを含む）。**パスは返さない**
- **`SHELL` 区分**（§2.2）。`Cache-Control: no-store`（断る答えも）。API 一覧（§5.24）には載せない
- エラー: `INVALID_ARGUMENT` 400（`reason`: `not_a_zip` / `unsafe_archive` / `no_game` / `bad_name` / `incomplete`）、`PAYLOAD_TOO_LARGE` 413（`too_large`）、`NOT_READY` 409（`disk_full`）、`CONFLICT` 409（`superseded`）、`UNAVAILABLE` 503（`stopped`。終了中）、`INTERNAL` 500（`failed`。持つ前の予期しない失敗も 500 で、ファイルを残さない）

### 6.49 アップロードの破棄 `DELETE /api/v1/assets/uploads/{id}`（2026-11-14 追加・ADR-005 S-final SF-4b-1）
- 持っているアップロードがその id ならファイルを消す。応答は `{ "removed": true | false }`（取り込んだ・置き換わった・期限切れのものも `false` で、`404` にしない）。本文は読まない
- **`SHELL` 区分**・`no-store`

### 6.50 ゲームアセットのジョブのキャンセル `POST /api/v1/assets/job:cancel`（2026-11-14 追加・ADR-005 S-final SF-4b-1）
- body: `{ "revision": <ジョブの revision> }`（§7 の開始の `data.job` か §5.28 の `revision`。1 以上の整数でなければ `400`。bool も不可）
- そのジョブが走っていれば止めるよう頼み `{ "cancelled": true }`、走っていない（終わった・別のジョブが走っている）なら `{ "cancelled": false }`（別のジョブは止めない）。止まるのはジョブの次の区切り（チャンク・エントリ・ファイルの間）で、止まったかは §5.28 の `status: "cancelled"` で知る
- **`SHELL` 区分**・`no-store`

### 6.51 ゲームのフォルダを開く `POST /api/v1/assets/games-folder:reveal`（2026-11-14 追加・ADR-005 S-final SF-4b-1）
- `user_data/games` をこの PC のファイルマネージャで開く（無ければ作る）。**パスを受け取らない**（フォルダは core が決める）。Windows は `%WINDIR%\explorer.exe` を引用符つきの 1 つの引数で（シェルを通さない。§6.35 と同じ考え方）、macOS は `open`、ほかは `xdg-open`。開けなければ `500 INTERNAL`
- **`SHELL` 区分**（`THIS_PC` の §6.35 と違い、同じオリジンのレイアウトからは開かせない）・`no-store`

---

## 7. 危険操作エンドポイント（書き込み・操作ログ対象 / ガード必須）

### 7.1 一覧

| メソッド・パス | ガード | 委譲先 Action | 既存 |
|---------------|--------|--------------|------|
| `POST /api/v1/scoreboards/{n}:clear` | confirm | `clear_all` | #33 |
| `POST /api/v1/scoreboards/{n}:reset-players` | confirm | `reset_players` | #32 |
| `POST /api/v1/scoreboards/{n}:reset-match` | confirm | `reset_match` | #31 |
| `POST /api/v1/scoreboards/{n}:reset-all` | confirm | `reset_all`（`AutonaActions.ClearBoard`。2026-11-09 追加・ADR-005 S4u） | — |
| `POST /api/v1/scoreboards/{n}/teams/{t}/players/{p}:clear` | confirm | `clear_player`（`PlayerService.ClearSlot`。2026-11-09 追加・ADR-005 S4u） | — |
| `POST /api/v1/players:delete` | confirm | `delete_player`（`core/PlayerDirectory.Delete`。2026-11-05 追加・ADR-005 S4m） | — |
| `POST /api/v1/scoreboards/{n}:load-set` | （confirm 推奨） | `load_set` | #38 |
| `POST /api/v1/scoreboards/{n}:pull-stream` | （confirm 推奨） | `pull_stream_set` | #24 |
| `POST /api/v1/scoreboards/{n}:pull-user` | （confirm 推奨） | `pull_user_set` | #25 |
| `POST /api/v1/scoreboards/{n}:follow` | （confirm 推奨） | `follow_station`（`SetFollowService.FollowStation`。2026-11-09 追加・ADR-005 S4v） | — |
| `POST /api/v1/scoreboards/{n}:stop-follow` | なし | `stop_follow`（`SetFollowService.StopFollowing(forget=True)`。2026-11-09 追加・ADR-005 S4v） | — |
| `POST /api/v1/tournament` | 実行中フラグ | `set_tournament`（`TournamentService.SetTournament` / `ClearTournament`） | #44 |
| `DELETE /api/v1/tournament` | なし（すぐ終わる。後から来た設定 / 解除が勝つ） | `clear_tournament`（`TournamentService.ClearTournament`。2026-11-01 追加・ADR-005 S4g） | — |
| `POST /api/v1/tournament/media/fetch` | 実行中フラグ | `fetch_tournament_media`（`TournamentMediaService.FetchMedia`。2026-11-01 追加・ADR-005 S4g） | — |
| `POST /api/v1/game` | 実行中フラグ | `set_game` | #14 |
| `POST /api/v1/bracket:refresh` | 実行中フラグ | `update_bracket`（`AutonaWebOperations` → `Top8Service.RefreshFromProvider`。ADR-005 S3e-8） | #41 |
| `POST /api/v1/analysis:refresh` | 実行中フラグ | `refresh_analysis`（`AnalysisService.Refresh`。2026-11-04 追加・ADR-005 S4k） | — |
| `POST /api/v1/tournament/phases:refresh` | 実行中フラグ | `refresh_tournament_phases`（`SetSelectionService.RefreshPhases`。2026-11-09 追加・ADR-005 S4v） | — |
| `POST /api/v1/tournament/stations:refresh` | 実行中フラグ | `refresh_tournament_stations`（`SetSelectionService.RefreshStations`。2026-11-09 追加・ADR-005 S4v） | — |
| `POST /api/v1/tournament/sets:refresh` | 同じ要求の実行中フラグ（別の要求は置き換える） | `refresh_tournament_sets`（`SetSelectionService.RefreshSets`。2026-11-09 追加・ADR-005 S4v） | — |
| `POST /api/v1/tournament/sets:cancel` | なし | `cancel_tournament_sets`（`SetSelectionService.CancelSets`。2026-11-09 追加・ADR-005 S4v） | — |
| `POST /api/v1/stream/thumbnail` | 実行中フラグ | `generate_stream_thumbnail`（`StreamService.GenerateThumbnail`。2026-10-31 追加・ADR-005 S4e） | — |
| `POST /api/v1/thumbnail:reset` | confirm | `reset_thumbnail_settings`（`ThumbnailService.Reset`。2026-11-07 追加・ADR-005 S4o） | — |
| `POST /api/v1/thumbnail:preview` | なし（実行中の要求は最後の 1 件にまとめる） | `preview_thumbnail`（`ThumbnailService.Preview`。2026-11-07 追加・ADR-005 S4o） | — |
| `POST /api/v1/thumbnail:generate` | 実行中フラグ | `generate_thumbnail`（`ThumbnailService.Generate`。2026-11-07 追加・ADR-005 S4o。body の `board` は 2026-11-10・S4z） | — |
| `POST /api/v1/result:clear` | confirm | `clear_result`（`ResultService` の `Clear`。2026-11-09 追加・ADR-005 S4q） | — |
| `POST /api/v1/result:loadStandings` | 入力があれば confirm・実行中フラグ | `load_result_standings`（`ResultService.LoadStandings`。2026-11-09 追加・ADR-005 S4q） | — |
| `POST /api/v1/result:preview` | なし（実行中の要求は最後の 1 件にまとめる） | `preview_result`（`ResultService.Preview`。2026-11-09 追加・ADR-005 S4q） | — |
| `POST /api/v1/result:generate` | 実行中フラグ | `generate_result`（`ResultService.Generate`。2026-11-09 追加・ADR-005 S4q） | — |
| `POST /api/v1/access-token:regenerate` | confirm・`SHELL` 区分 | `regenerate_access_token`（`core/WebAccess.RegenerateToken`。2026-11-14 追加・ADR-005 S-final SF-4a-1） | — |
| `POST /api/v1/assets:importZip` | `overwrite` があれば confirm・1 度に 1 つ・`SHELL` 区分 | `import_asset_upload`（`AssetPackService.ImportUpload`。2026-11-14 追加・ADR-005 S-final SF-4b-1） | — |
| `POST /api/v1/assets/packs:install` | confirm・1 度に 1 つ・`SHELL` 区分 | `install_asset_packs`（`AssetPackService.Install(fetch=False)`。2026-11-14 追加・ADR-005 S-final SF-4b-1） | — |

- **board 3 の `:clear` / `:reset-players` / `:reset-match`**（ADR-005 S4w で開いた。Qt のクルーボード（S4ac-3 で削除）にボタンは無く、legacy と同じ操作）: `:reset-match` は全メンバーを既定のストックに戻して出場中を無しにし（名前は残す）、`phase` / `pool` / `match` と `best_of` を空にする（match バーの欄はボードにあるものだけ。クルーボードのシードに `pool` は無く、`PATCH set` で書くまで足さない。ADR-005 S4ac-1 の検収レビュー。それまでは `pool: ""` を足していた）。`:reset-players` は全メンバーの行を空にする（名前も。チーム名・色・人数は残す。入力中の欄を先に確定する）。`:clear` は `:reset-match` + 人数 1 + `:reset-players`（チーム名・色は残る）。即時公開で、同じことをもう一度送っても書かない。スワップのフラグは戻らない。未構築のボードの扱いは §2.12
- `:reset-all`: デスクトップの「すべての入力値をリセット」と同じ（`AutonaActions.ClearBoard`。1 保存サイクル）。選手・スコア・match バー・チーム名とロゴ・best of を空にし、**人数・キャラ数は残す**。入力中の欄を先に確定し、最後に読んだセットを忘れる（同じセットを読み直せる）。**`:clear` とは別の動作**: `:clear` はチーム名を残し（人数が 1 人になるときを除く）、人数・キャラ数を既定に戻す。board 3 は `400`。held 対象
- `players/{p}:clear`: カードのクリアボタンと同じ（`PlayerService.ClearSlot`。birthday・city・mains は残る）。`{t}` は状態側のチーム。入力中の欄を先に確定する。スロットが無ければ `404 NOT_FOUND`。board 3 は `400`。held 対象
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
- `:follow`（ADR-005 S4v）: body は §5.18 の `stations` の行（`{ "type": "station", "id": 77, "identifier": 4 }` か `{ "type": "stream", "identifier": "channel" }`。ほかのキーは無視するので行をそのまま送れる）。配信台ウィンドウで行を選ぶのと同じ `SetFollowService.FollowStation`: その配信台 / 配信の今のセットを照会し、ボードが何を出していても読み直す。取得は provider のワーカーで非同期で、応答は受付時点の `revision`。ポーリングが有効ならそのあと取り直しが続く。何を追従しているかは §5.17
  - 今のセットが無いときはボードをそのままにする。照会の失敗も同じ扱いで区別しない（窓も表示しない）。一覧とは照合しない（`:pull-stream` と同じく、言われたものを追従する）
  - `{n}` は 1 / 2。デスクトップは未構築のボードを構築してから書く（`:load-set` と同じ）。held 対象
  - エラー: `400 INVALID_ARGUMENT`（`type` が `station` / `stream` でない・`station` に `id` が無い・`stream` の `identifier` が空でない文字列でない・`id` / `identifier` が正の整数でも空でない文字列でもない（bool も不可）・board 3）、`409 NOT_READY`（`no tournament loaded`。何も書かない）
- `:stop-follow`（ADR-005 S4v）: body なし。タイマー行のキャンセルボタンと同じ `StopFollowing(forget=True)`: 取り直しの時計を止め、配信台と追従中のセットを忘れる（同じセットをもう一度読める）。**ボードに出ている選手とスコアは残す**。大会が無くても答える（`200`）。board 3 は `400`
  - 止めるものが無ければ何も書かない（書くのは変わるキーだけなので、反映ボタンを点けない。`scoreboard_follow` も出ない）。追従中のセットを忘れる書き込みは held 対象
  - デスクトップの未構築のボードは**構築せず、何も書かずに `200`**（読み込みが必ず構築するので、未構築のボードに止める追従は無い。構築するとシードで state が消え、ポーリングの設定も切れる）
- `access-token:regenerate`（ADR-005 S-final SF-4a-1）: API 設定のページ（`/web/api`）の「再発行」（確認つき）が呼ぶ `WebAccess.RegenerateToken()`。body は `{ "confirm": true }`。**`SHELL` 区分**（§2.2。アプリ自身のウィンドウからだけ。トークンでは開かない）
  - 応答: `data` は §5.29 と同じ `{ "token": "<新しいトークン>", "available": true }`、`Cache-Control: no-store`
  - 古いトークンはすぐ使えなくなる: LAN の HTTP の書き込みは `401`、古いトークンで入った Socket.IO の接続の書き込みイベントは `401 UNAUTHENTICATED`（`the token was regenerated; reconnect with the new one`）の ack で断る（接続と state の配信は続く。新しいトークンで接続し直す。この PC のトークンなしの接続は影響しない）
  - settings.json を読めず保存できないときは発行せず **`409 NOT_READY`**（`settings.json could not be read: no new token was issued`。今のトークンのまま）
  - 操作ログは `access-token.regenerate`（body のキー名だけ。トークンは出さない）
- `assets:importZip`（ADR-005 S-final SF-4b-1）: §6.48 のアップロードを取り込むジョブを始める（ページの「ZIP から取り込む」が呼ぶ `ImportZip`）。body: `{ "upload": "<id>", "overwrite": ["<置き換えるゲーム>"], "confirm": true }`。`overwrite` はアップロードの `conflicts` のうち置き換えるもの（無い・空なら、インストール済みのゲームは置き換えずに §5.28 の `skipped`）。`conflicts` に無い名前は `400`。`overwrite` が空でないときだけ `confirm: true` が要る（無ければ `409 CONFIRMATION_REQUIRED`）。**`SHELL` 区分**・`no-store`
  - 応答 `202`: `{ "accepted": true, "operation": "importZip", "job": <revision> }`（§2.8。結果は §5.28）。アップロードはジョブが取り出し、ジョブの終わり方によらずジョブが消す（同じ id でもう一度は取り込めない）。取り込みが始まらずに断られたとき（取り込みの前の検査で `not_a_zip` など）も消える。ジョブは書き込む前にもう一度、中央ディレクトリとエントリを検査する
  - エラー: `404 NOT_FOUND`（`reason: "unknown_upload"`: 取り込んだ・破棄した・置き換わった・期限切れ）、`409 CONFLICT`（`busy`。別のジョブが走っている。アップロードは同じ id で残る。取り込みの前の検査の間に別のジョブが始まったときも同じで、期限は延びない。その間に新しいアップロードが始まっていれば、それが置き換えたものとして消える）、`503 UNAVAILABLE`（`stopped`）
  - 操作ログはボディのキー名だけ（`assets.importZip`）
- `assets/packs:install`（ADR-005 S-final SF-4b-1）: 上流の索引のゲームのパックを落として入れるジョブを始める（ページの「ダウンロードして読み込む」が呼ぶ `AssetPackService.Install`）。body: `{ "game": "<code>", "packs": ["full"], "confirm": true }`。`packs` が無い・null なら既定のパック（§5.27 の `default`）。**常に confirm**（ゲームのファイルを置き換え、GB 単位を落とすため）。**`SHELL` 区分**・`no-store`
  - 索引はキャッシュにあるものだけを使い、この要求では取りにいかない。無ければ `409 NOT_READY`（`reason: "index_unavailable"`。先に §5.27 を読む）
  - 応答 `202`: `{ "accepted": true, "operation": "install", "job": <revision> }`
  - エラー: `400 INVALID_ARGUMENT`（`game` が空でない文字列でない・`packs` が文字列のリストでない・`reason` `unknown_pack` / `no_packs`）、`404 NOT_FOUND`（`unknown_game`）、`409 CONFLICT`（`busy`）、`409 NOT_READY`（`index_unavailable`）、`500 INTERNAL`（`bad_index`）

### 7.2 確認フラグ（C-2 / D7）
- **破壊的操作**（`:clear`・`:reset-all`・`:reset-players`・`:reset-match`・`players/{p}:clear`・`POST /players:delete`・`POST /access-token:regenerate`・`POST /assets/packs:install`・`overwrite` が空でない `POST /assets:importZip`）は body に **`"confirm": true` 必須（MUST）**。
  無い/false なら **`409 CONFIRMATION_REQUIRED`**（状態は変更しない）。
- `POST /players:delete`: body は `{ "key": "SP Alpha", "confirm": true }`（`key` は §5.11 の行の DB キー。タグだけでは一致しない）。文字列でない `key` は `INVALID_ARGUMENT`、DB に無い `key` は `404 NOT_FOUND`（保存も通知もしない）。選手 DB なので応答に `revision` は付かない。操作ログは `player.delete`（キー名のみ）
- 「配信中」自動ブロックは**初回は設けない**（Autona に配信状態判定手段が無いため）。confirm のみで担保。
  → 配信状態判定手段が入ったら ADR-001 Revisit。

### 7.3 重い操作のペイロード
> **重い操作（tournament / game / bracket:refresh）は `202 Accepted` で受け付け、完了は WS 購読で確認する（§2.8）。**
> 多重起動は `409 CONFLICT`。下記の `UPSTREAM_TIMEOUT`/`NOT_FOUND`/`NOT_READY` は**受付前に判定できる場合のみ**
> 同期で返し、受付後（202 以降）の失敗はログに残す（§2.8）。

- `POST /api/v1/tournament`: `{ "url": "https://www.start.gg/tournament/.../event/...", "force": false, "fetchMedia": false }`
  - URL 正規化は `set_tournament`（core の `TournamentService.SetTournament`）に委譲。`url` が `""`・`null`・省略で大会クリア
    （既存挙動）。**`url` が文字列でも `null` でもなければ 400**（`false` / `0` / `[]` / `{}` も。以前は偽の値がすべて解除になった。
    S4g レビュー L3）。
  - ADR-005 S4g から、管理画面のブラケットの URL（`start.gg/admin/tournament/<大会>/brackets/<id>`）も受け付ける（ジョブで
    イベントの URL に解決してから読み込む）。それ以外の URL は受付前に `400 INVALID_ARGUMENT`（何も変えない）。
  - `force`（bool。既定 false）: 読み込み済みと同じ URL でも取得し直す。`fetchMedia`（bool。既定 false）: 読み込めたら
    大会のアイコン / バナーも取得する（下の `media/fetch` と同じジョブ）。既定値は今までの挙動。bool でなければ 400。
  - 外部通信を伴う。受付は 202（`data: {"accepted": true, "operation": "tournament.load", "loadRevision": n}`）。
    要求の `tournamentInfo.load`（`resolving` / `loading`、解除なら `idle`）と `tournamentInfo.url` は **202 の前に** state に出る。
    結果は WS（`tournamentInfo.load` / `tournamentInfo.*`）と §5.7 で観測する（`loadRevision` の使い方は §5.7）。
    実行中は `409 CONFLICT`（§2.8）。
  - 実行中フラグは `load_tournament` を GUI スレッドで走らせたタスクの終了時に解除する（2026-10-21 修正）。
    以前は `tournament_changed` を待っていたが、読込済みと同じ URL では早期 return でこれが出ず、
    最大 120 秒（ガードの TTL）409 を返し続けていた。
- `DELETE /api/v1/tournament`: body なし。大会を解除する（URL を空・`tournamentInfo` を空・選択中のゲームも外す。
  `POST` の空の `url` と同じ）。GUI executor で `POST` の読み込みと来た順に走り、`202`（`data: {"accepted": true, "operation":
  "tournament.clear", "loadRevision": n}`。`load` は 202 の前に `idle` で出る）。解除は確認ダイアログの後に送るのがクライアントの責任（大会情報ページ・サイドバー）
- `POST /api/v1/tournament/media/fetch`: body なし。読み込み中の大会のアイコン / バナーを start.gg から取る `TaskExecutor` の
  ジョブを積み、`202`（`data: {"accepted": true, "operation": "tournament.media.fetch", "queued": bool}`）。進捗と結果は
  state の `tournamentInfo.media = {status: running → done | error, error, revision}`（error は `no_media` / `fetch_failed` /
  `save_failed` / `superseded` / `cancelled`）。同じ大会の取得が実行中なら `409 CONFLICT`。**別の大会**の取得が実行中なら
  `queued: true` で、その取得が終わった後にその時点の大会で取り直す。大会が読み込まれていなければ `409 NOT_READY`
- `POST /api/v1/game`: `{ "codename": "ssbu", "modsActive": false }`
  - 未知 codename は受付前に判定できれば `404 NOT_FOUND`。アセット再ロードで重い → 202 + 実行中フラグ。
- `POST /api/v1/bracket:refresh`: body なし。provider 不在は受付前に `409 NOT_READY`。それ以外は 202 + 実行中フラグ。
  - 実行中フラグは core（`Top8Service`）の 1 つを、Top 8 パネル・ホットキー・legacy `/update-bracket`・Socket.IO `update_bracket` と共有する。
    取り込みの終了（取得・組み立て・書き込みが終わったとき。空の結果・失敗を含む）まで立ったまま。実行中は `409 CONFLICT`
    （legacy / Socket.IO は `"OK"` のまま何もしない）。ADR-005 S3e-8。それまではこのルート専用のフラグで、
    パネルに取得を頼んだ時点で下ろしていた（取得中の 2 回目も 202 になり、取得が 2 本走った）
  - 受付後に結果が空（決勝のダブルエリミのグループが無い・Start.gg の失敗）なら何も書かない（ログのみ）
- `POST /api/v1/analysis:refresh`（2026-11-04 追加・ADR-005 S4k）: body なし。読み込み中の大会の全 phase group の試合を
  start.gg から取る（core の `AnalysisService`。取得は provider のワーカーで最大 2 本）。受付は
  `202`（`data: {"accepted": true, "operation": "analysis.refresh"}`）で、`analysis` の次の revision の `running` は
  **202 の前に** state に出る。進み具合と結果は state の `analysis = {status: running → done | error, error, done, total,
  failed, revision}`（error は `no_phases` / `fetch_failed`）で観測し、行は §5.9 で読む。実行中は `409 CONFLICT`（キューしない。
  実行中フラグは core の 1 つ）。大会が読み込まれていなければ `409 NOT_READY`（何も書かない）。旧 Qt パネルのボタンと同じ操作で、
  どちらの起動形態でも動く
- `POST /api/v1/tournament/phases:refresh` / `/tournament/stations:refresh`（2026-11-09 追加・ADR-005 S4v）: body なし。読み込み中の大会のフェーズ / 配信台と配信を start.gg から取る（core の `SetSelectionService`。provider のワーカーのジョブ 1 本）。受付は `202`（`data: {"accepted": true, "operation": "tournament.phases.refresh" | "tournament.stations.refresh"}`）で、そのリストの次の revision の `running` は **202 の前に** state に出る。進み具合と結果は state の `set_selection.{phases|stations}`、行は §5.18。実行中は `409 CONFLICT`（キューしない）。大会を変えて止まった取得の要求がまだ答えず、そのリストのジョブが 2 本残っている間も `409 CONFLICT`。大会が読み込まれていなければ `409 NOT_READY`（何も書かない）
- `POST /api/v1/tournament/sets:refresh`（2026-11-09 追加・ADR-005 S4v）: `{ "phase": 11 | "11" | "all" | null, "finished": false }`。セット選択の「最新の試合情報を取得」。`finished` は「完了したセットを表示」で、省略は false。受付は上と同じ `202`（`"operation": "tournament.sets.refresh"`）で、`done` / `total` にページが進む。窓と同じ条件で start.gg に頼む（`phase` のグループに絞る。`"all"` は全フェーズのグループ）
  - **フェーズが分かっているとき**（§5.18 の `phases` に行があるとき）: `phase` が `null` か省略なら `400 INVALID_ARGUMENT`（窓の「フェーズを選択してください」。大きな大会で全ページを取らないため。全部なら `"all"`）。その大会に無い id は `404 NOT_FOUND`
  - **フェーズが分からないとき**（未取得か、フェーズの無い大会）: 大会全体を取る。`null` と `"all"` は同じ要求で、id は `404 NOT_FOUND`
  - **同じ `phase` / `finished` の取得が実行中なら `409 CONFLICT`**（`"11"` と `11` も同じ要求）。別の要求は走っている取得を止めて置き換える（窓のコンボとチェックボックスと同じ）。止めた取得の答えは捨てる。ただし止めた取得（置き換え・`:cancel`・大会の変更のどれでも）の要求がまだ答えず、ジョブが 2 本残っている間は、どの要求も `409 CONFLICT`（ジョブレーンは全ての大会の取得で共有）。大会が読み込まれていなければ `409 NOT_READY`
  - `phase` が正の整数でも空でない文字列でもない（bool も不可）・`finished` が bool でないときは `400 INVALID_ARGUMENT`
- `POST /api/v1/tournament/sets:cancel`（2026-11-09 追加・ADR-005 S4v）: body なし。走っている `sets` の取得を止める（窓がセットを選んだときと同じ）。`200` で `data: {"cancelled": bool}`（走っていたか）。止めたら `set_selection.sets` が次の revision の `idle` になり、行は残る。止めた取得の要求が答えるまでジョブは残り、`:refresh` の 2 本に数える
- `POST /api/v1/stream/thumbnail`: body なし。保存済みのオプション（§5.6）で配信サムネを 1 枚作る `TaskExecutor` のジョブを積み、
  `202`（`data: {"accepted": true, "operation": "stream.thumbnail"}`）。実行中フラグは core（`StreamService`）の 1 つで、
  実行中は `409 CONFLICT`。進捗と結果は state の `stream.thumbnail = {status: running → done | error, path, error, revision}`
  （`path` は `out/stream/thumbnail_*.jpg` で、そのまま静的配信の URL パス。`revision` は受け付けるたびに 1 進む）
- `POST /api/v1/thumbnail:reset`: `{"confirm": true}` 必須（無ければ `409 CONFIRMATION_REQUIRED`）。全ゲームの設定と取り込んだ背景を消して既定に戻す。応答 `data` は §5.12 の `settings`
- `POST /api/v1/thumbnail:preview` / `:generate`: 保存済みの設定で試合サムネイルを描く。preview は body なしで**ボード 1**（サムネイルのページの絵）。generate は body の `board`（`1` / `2`。省略・JSON オブジェクトでない body はボード 1 で、それまでの呼び方のまま。2026-11-10 追加・ADR-005 S4z）のボードを描き、そのボードの選手の名前でファイルを保存する。`board` が 1 / 2 でない（整数でない・bool も）ときは `400 INVALID_ARGUMENT`（何も描かない。ヘッドレスでも 503 より先）。`202`（`data: {"accepted": true, "operation": "thumbnail.preview" | "thumbnail.generate", "renderRevision": n}`）で、`running` は 202 の前に state に出る。
  進み具合と結果は state の `thumbnail.render = {status: running → done | error, path, error, revision}`（error は `no_game` / `no_player` / `payload_failed`（テンプレートや設定が読めない）/ `render_failed`（描けない・描画を始められない）/ `save_failed` / `cancelled`（終了中）のコードだけで、詳細はログ）。`renderRevision` はその要求を描く run の revision で、クライアントはこれで自分の結果を見分ける。
  描画は 1 度に 1 つ。**実行中の preview は次の 1 回にまとめる**（ペイロードは run の開始時に組むので最新の設定が出る）。**実行中の generate か予約済みの generate があるときの generate は `409 CONFLICT`**、プレビュー中の generate は次の run に予約する（ボード 1 の generate は予約の preview を兼ねる。preview の依頼には generate の `renderRevision` を返す）。preview を兼ねた generate が試合なし（`no_game` / `no_player`）で終わったときは、続けて preview の run（次の revision）でプレースホルダを描く（ADR-005 S4p）。
  **ボード 2 の generate は preview を兼ねない**（2026-11-10・ADR-005 S4z レビュー。どの経路でも preview の依頼に別のボードの絵で答えない）: 予約の preview があれば、generate の run の後に続けてボード 1 の preview の run（generate の次の revision）を描く。generate より先に予約した preview の依頼に返した `renderRevision` は generate の run の番号になるので、その preview は次の revision の run が答える（preview を兼ねた generate が試合なしで終わったときと同じ流れ）。ボード 2 の generate を予約した後の preview の依頼には、その後の run の revision（generate の revision + 1）を返す。
  generate は `out/thumbnails/{P1}-vs-{P2}-{UTC}.png` と `.jpg` を保存し（30 世代）、`path` は PNG の相対パス（`out/thumbnails/...`。静的配信の URL パス）。preview は `path` を変えず、§5.12 の画像だけを更新する。ボード 1 の generate も §5.12 の画像を書く。**ボード 2 の generate は §5.12 の画像を書かない**（ボード 1 の絵のまま）。生成の `done` はそこまでで、§5.12 の画像だけ書けなくても `done`（警告ログ）。試合が揃っていなければ（`out/program_state.json` が無い・壊れているときも）、preview はプレースホルダを描き、generate は `no_game` / `no_player` で error。
  オフスクリーンの描画はデスクトップだけなので、**ヘッドレスは `503 UNAVAILABLE`**（終了中も同じ `503`）
- `POST /api/v1/result:clear`: `{"kind": "players" | "teams", "confirm": true}`（`confirm` が無ければ `409 CONFIRMATION_REQUIRED`）。その種類の全行を空にする。もう一方の種類は残る。1 保存サイクル
- `POST /api/v1/result:loadStandings`: body は空か `{"confirm": true}`。**大会情報を取り直し、それが指すゲームの読み込みが済んでから**、今のレイアウトの枠数ぶんの standings を start.gg から取り、そのレイアウトの種類の行に入れる（取り直しの失敗はログだけで standings へ進む）。provider のワーカーのジョブで、`202`（`data: {"accepted": true, "operation": "result.loadStandings", "standingsRevision": n}`）。`running` は 202 の前に state に出る（ジョブが先に終わっていればその結果が残る）。
  進み具合と結果は state の `result_standings = {status: running → done | error, error, revision}`。error は `fetch_failed`（start.gg が答えない・読めない答え）/ `no_standings`（答えたが standings が無い）/ `changed`（standings の取得の間にゲームが変わった、または取り直しか取得の間に大会が差し替わった。取り込み自身の取り直しによるゲームの切り替えでは出ない）/ `import_failed`（答えは来たが行に入れられなかった）で、`done` 以外は行を書かない。
  **実行中は `409 CONFLICT`**（キューしない。確認より先に判定するので、`confirm` の有無に関わらずこれが返る）。**行に入力があるときは `confirm` が要る**（無ければ `409 CONFIRMATION_REQUIRED`）。大会が読み込まれていなければ `409 NOT_READY`。これらは何も変えない。どちらの起動形態でも動く
- `POST /api/v1/result:preview` / `:generate`: body なし。今のレイアウトで大会リザルトを描く。`202`（`data: {"accepted": true, "operation": "result.preview" | "result.generate", "renderRevision": n}`）で、`running` は 202 の前に state に出る。進み具合と結果は state の `result_render = {status: running → done | error, path, error, revision}`。レーンの規則（1 度に 1 つ・preview のまとめ・generate の予約・`renderRevision` の意味・error のコード）は `:thumbnail` と同じで、`no_game` / `no_player` は無く、予約の間にデータが無くなった generate の `no_data` がある。
  generate は `out/results/{大会名}-top{8|16}-{UTC}.png` を保存し（30 世代。PNG のみ）、`path` はその相対パス。大会名が空なら `result-top{8|16}-{UTC}.png`。**受理の時点で行の選手を選手 DB に追記する**（§6.34 と同じ。失敗はログだけで画像は作る）。preview は DB を書かない。
  **データの無い generate は `400 INVALID_ARGUMENT`**、実行中か予約済みの generate があるときは `409 CONFLICT`。preview はデータが無ければ固定のサンプルを描く（§5.13 の `sample`）。背景はサムネイルの設定（`background_source`）に従う。ヘッドレスと終了中は `503 UNAVAILABLE`

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
  （シェル操作）は操作の側が marshal する。ボードの操作は marshal しない（ADR-005 S4ad。リクエストのスレッドで
  ボードホストのロックの下。それまではデスクトップのボードホストが GUI スレッドへ marshal していた）。`set_game` も marshal しない
  （ADR-005 S3f-4。ロードは TaskExecutor のジョブで走り、ロードの通知（`gameLoaded` / `onLoad`。Qt モデルの整理 PR4 まではモデルの作り直しも）だけをジョブが GUI スレッドへ渡す）。
  終了中で GUI スレッドに渡せないときは、シェル操作が 503（`the app is shutting down`）で答える。ボードの操作も
  終了の開始からは同じ 503 で、何も書かない（ボードホストが `gui_unavailable_reason()` を見て断る。メインスレッドの
  操作と、終了中のロードの後始末（`atShutdown`）は断らない。S4ad まではデスクトップのホストの marshal が返していた。§2.12）。
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
> §5.8 `countries` と §6.22〜6.24 の Top 8（2026-11-04・ADR-005 S4i）は `tests/test_api_v1_top8.py` が pin する。
> §5.9 `analysis` と §7.3 `analysis:refresh`（2026-11-04・ADR-005 S4k）は `tests/test_api_v1_analysis.py` が pin し、
> 行の全シナリオを `tests/test_analysis_rows.py` が golden と照合する。
> §5.10 `stats/overlays` と §6.25 `stats/overlays/selected`（2026-11-05・ADR-005 S4l）は `tests/test_api_v1_stats_overlays.py` が pin する。
> §5.11 `players*`・§6.26・§6.27・§7.2 `players:delete`（2026-11-05・ADR-005 S4m）は `tests/test_api_v1_players.py` が pin し、行と記録（`PrepareSaveToDB` との golden）は `tests/test_player_directory.py` が pin する。

- **自動テストは作らない**（PdM 決定）。動作確認は**アプリ上で手動**で行う
  （`python main.py` 起動 → curl/HTTP クライアントで `/api/v1/...` を叩く → UI / `out/` / OBS で反映確認）。
- 手動確認項目は [../manual-test-checklist.md](../manual-test-checklist.md) の「外部制御 API」節になぞる:
  - 既存 `/score`・`/scoreboard<N>-...` ルートと Web UI / OBS が従来通り動く（regression）。
  - `score:adjust` の delta 符号・冪等キー再送で二重加算しない。
  - 破壊的操作は confirm 無しで状態が変わらない。
  - 重い操作が 202 で返り、完了が WS で観測でき、実行中の同種要求が 409 になる。
  - 操作ログに書き込み操作が INFO で出る／読み取りは出ない。
  - 範囲外 `{n}`（0 や 4）が 400 になる。board3 に board1/2 専用の操作（§2.12）で拒否される。

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
| `activeBoard` | **`SettingsManager.Get("scoreboard.battle_format", "singles")`** を `{"singles":1,"team":2,"crew":3}` で写像。これが表示中ボードの真実源 | `AutonaActions.ActiveBoard` / `ResolveBattleFormat`（[core/Actions.py](../../src/core/Actions.py)） |
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
| `GET /scoreboards/{n}`(swap) | `get_swap` | ✕ | ボードを構築してから `score.{n}.teamsSwapped` を読む（ADR-005 S2c〜。ボード 3 は S4w〜）。n 不正→None→`None.teamsSwapped` | `INVALID_ARGUMENT` |
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
