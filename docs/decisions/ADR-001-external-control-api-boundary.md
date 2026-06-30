# ADR-001: External Control API Boundary

## Status

Accepted（2026-06-15・PdM 承認）

> 責務境界・初回スコープ・認証方針・誤操作防止の方針を確定。実装 I/F の詳細仕様と
> 実装チケットは次フェーズ（[../tasks/investigate-external-control-api.md](../tasks/investigate-external-control-api.md) の
> 「次に作成すべき実装タスク」）。確定の根拠調査は
> [../architecture/external-control-api-candidates.md](../architecture/external-control-api-candidates.md)、
> 影響整理は [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)。

## Context

Companion / Stream Deck 連携、外部制御 API、OBS 連携拡張を予定している。
決めるべきは「**外部からの制御をどの面（レイヤー）で受けるか**」という責務境界。

確認済みの前提（コード確認・2026-06-15 更新）:

- 既に **Flask + Socket.IO による HTTP/WS 制御面が存在**する
  （[src/AutonaWebServer.py](../../src/AutonaWebServer.py) /
  [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py)）。
  HTTP ルート **約 45**＋対応 Socket.IO イベント（スコア操作・Swap・Reset・統計・マスタ取得・
  セット/大会ロード等）。完全な棚卸しは
  [../architecture/external-control-api-candidates.md](../architecture/external-control-api-candidates.md) §3。
- 同じ操作が **HTTP ルートと Socket.IO イベントの両方**で二重定義されている（保守上、片方だけ直すと不整合）。
- 全ルートは **`WebServerActions` へ委譲**。状態変更は Qt Signal emit → widget → **`StateManager` 単一経路**
  （直接 dict 書き込みなし、多くが `@gui_thread_sync` で GUI スレッドに直列化）。
- **既定ポート = `5000`** / **Web UI ルート = `/`・`/scoreboard`・`/team`・`/web`**（確定）。
- **現状の制御面は `0.0.0.0` バインド + 認証なし + CORS 全許可**。既に LAN 内から全操作が可能。
- **X / Bluesky 投稿は UI ボタン専用**（Web/WS 未露出）。
- OBS-websocket は使わない方針（ROADMAP で検討外と明記）。Autona は OBS に接続しない。

残課題（実装フェーズで確定 / candidates §13 Open questions）:

- 重い操作の多重実行ガードが**既存コードに在るか**（無ければ新 API 層で実装）
- `set_team_data` の payload スキーマ（必須キー）
- Companion / Stream Deck 側が求めるプロトコル（HTTP / WS / その他）

## Decision

以下を確定する（2026-06-15・PdM 承認）。各論点の番号は調査時の整理に対応。

### D1（論点1）: 汎用 External Control API として切る
Companion 専用ではなく**汎用の External Control API**を採用する。Companion / Stream Deck は
この API を叩く**薄いクライアント**として扱い、API は外部コントローラのボタン設計に引きずられない。

### D2（論点2）: Stream Deck 連携も API 経由
Stream Deck 連携を直接実装せず、**API 経由**とする。Stream Deck プラグイン / Companion は
クライアントに徹し、状態ロジックを持たせない（多くの運用は Stream Deck → Companion → API の三段を許容）。

### D3（境界）: `/api/v1/` を既存サーバーに薄く追加し、`WebServerActions` を再利用（案 B'）
新サーバー/プロセスは増やさず、既存 Flask+SocketIO に **`/api/v1/` 名前空間を追加**する。
内部処理は**既存 `WebServerActions` を唯一の実処理として再利用**し、状態ロジックを二重化しない。
**既存ルート（`/score`・`/scoreboard<N>-...`）は契約を一切変えず温存**（既存クライアント/OBS 互換）。

### D4（論点5・不変条件）: StateManager 単一経路を厳守（SettingsManager 併用は許容）
外部 API も**必ず `WebServerActions` → Qt Signal → `StateManager` を通す**。
StateManager をバイパスして**ランタイム状態（`score.*` 等）を直接書かない**。
GUI スレッド直列化（`@gui_thread_sync`）を尊重する。
- ただし一部 Action（`load_tournament` 等）が **`SettingsManager` にも書く**ことは**許容**する
  （PdM 確認済）。本 API はこれらも既存 Action へ委譲するだけで、Settings/State を新たに直接書かない。
- 帰結: `SettingsManager` のみの変更は `revision`（StateManager 由来）に反映されない場合がある（spec §2.4/§2.4a）。

### D5（認証/バインド）: 現状維持（`0.0.0.0` + 認証なし + CORS 全許可）
LAN 内運用を前提に**既存の挙動を引き継ぐ**。新 API も認証を課さない。
※ リスクは Consequences に明記。将来 LAN 外運用・マルチオペレーターが発生したら Revisit。

### D6（論点3+4・初回スコープ）: 操作系を広く一括提供（危険操作も confirm 付きで含める）
初回から以下をすべて含める。
- **読み取り**: `GET /api/v1/state` + `/health`（新規）+ 既存 WS 差分購読の踏襲（candidates §6）
- **安全操作**: スコア±1 / swap-teams / score:reset / set 情報設定（best-of・match・phase）
- **危険操作**: 破壊的（clear-all / reset-players / reset-match）と重い操作（set-tournament /
  update-game / update-bracket）も提供。ただし **D7 のガードを必須**とする。
  `set-light-mode` はテーマ切替機能ごと削除済みのため対象外。
- **初回も除外**: GUI ウィンドウ起動（open-set）/ X・Bluesky 投稿（外部送信・機密）/ 選手 DB 全量（個人情報）。

### D7（論点6・誤操作防止／ログ／ロック）
- **操作ログ（A-1）**: 外部 API の**書き込み操作のみ INFO で専用ロガーに記録**（時刻・送信元 IP・操作内容）。
  読み取りはログしない（or DEBUG）。既存の `before_request`（DEBUG・パスのみ）とは別建て。
  ※ 無認証のため「誰が」は IP 粒度まで（D5 の帰結）。
- **重い操作は非同期受付（202）**: set-tournament / update-game / update-bracket は同期完了を待たず
  **`202 Accepted` を返し、完了はクライアントが WS 購読で確認**する（PdM 決定）。
  理由: `set_game` 等は `@gui_thread_sync` + 同期アセット再ロードで HTTP をブロックしうるため。
- **多重実行ガード（B-1）**: 上記の重い操作は**実行中フラグで多重起動を 409 で弾く**。
  実行中フラグと冪等キャッシュは複数リクエストスレッド共有のため **`threading.Lock` で保護**する。
- **冪等性（B-1）**: 相対操作（score:adjust ±1 等）は `Idempotency-Key` ヘッダを**任意対応**
  （送られたら重複吸収、無ければ従来通り）。絶対値設定系（score:reset / set 設定 / swap）は本来冪等寄り。
- **破壊的操作の確認（C-2）**: clear-all / reset-players / reset-match は **`confirm=true` 必須**。
  「配信中」自動判定によるブロックは**初回は設けず**（Autona に配信状態判定手段が無いため）、confirm のみで担保。将来 Revisit。

### D8（I/F 形式）
- 操作系は構造化レスポンス `{"ok": true, "revision": <最新リビジョン>}`。失敗は
  `{"ok": false, "error": {"code": "...", "message": "..."}}` + 適切な HTTP ステータス（candidates §7.4/§7.5）。
  既存ルートの素朴な `"OK"`/文字列契約は**変えない**（新 API のみ構造化）。

## Alternatives considered

- **境界**: (A) 既存ルートを外部にそのまま使わせる → レスポンス非構造化で成否判定しにくい（candidates §10）。
  (C) 独立アダプタプロセス → 起動順・ポートが増え、シングルトン初期化の制約（agent-pitfalls #2）に抵触しやすい。
  → **(B') を採用**（D3）。
- **認証**: トークン認証を新 API のみに付与する案 / localhost バインドに絞る案も検討したが、
  LAN 上の別 PC（OBS 機・操作端末）からの利用を壊すため初回は見送り（D5）。
- **初回スコープ**: 読み取りのみ先行 / 安全操作のみ も検討したが、Companion で実用に足る操作面を
  早期に揃える方針を優先し、危険操作も**ガード付きで**含める（D6）。

## Consequences

**良い影響:**
- 外部コントローラは薄いクライアントで済み、Companion/Stream Deck/他ツールを同一 API で吸収できる。
- 既存ルート・OBS 出力・Web UI を壊さず追加できる（D3 の温存方針）。
- 状態ロジックが一本化される（D4）ため、手動 UI・ホットキー・Web・外部 API の挙動が一致する。

**受け入れるリスク・コスト:**
- ⚠️ **無認証 `0.0.0.0` 上で破壊的・重い操作まで叩ける**（D5+D6 の帰結）。同一 LAN の第三者・
  誤設定端末からの誤操作/不正操作リスクが残る。緩和は confirm（D7）と操作ログ（D7）に限られ、
  **アクセス制御そのものは無い**。LAN を信頼境界とみなせる運用に限定する前提。
- 既存 HTTP/WS と新 `/api/v1/` の**二系統**を持つことになる（既存は温存）。`WebServerActions` 再利用で
  ロジックの二重化は避けられるが、ルート定義の保守対象は増える。
- 操作ログ・実行中フラグ・idempotency の実装コストが初回に乗る（D7）。

## Revisit conditions

- LAN 外運用・マルチオペレーター・信頼できないネットワークでの利用が発生したとき
  → **認証/認可・バインド方針（D5）を再検討**（トークン / localhost 限定 / 操作スコープ分離）。
- 「配信中」状態を Autona が判定できる手段が入ったとき → **配信中の危険操作ガード（D7・C 系）を再検討**。
- Companion / Stream Deck の要求プロトコルが確定したとき → I/F 詳細（D8）を調整。
- X / Bluesky など外部送信操作を API 化する需要が固まったとき（初回除外を見直し）。
- 既存 HTTP/WS 二重定義の保守負荷が問題化したとき → 既存ルートの整理を別 ADR で検討。
