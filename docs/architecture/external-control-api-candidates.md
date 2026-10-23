# External Control API Candidates

> 外部コントローラ（Companion / Stream Deck / その他薄いクライアント）から Autona を
> 安全に操作するための **操作 API 候補の棚卸し・分類・設計案**。
> **この段階で実装は確定しない。** 設計境界の決定は
> [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)、
> 影響整理は [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)、
> 調査タスクは [../tasks/investigate-external-control-api.md](../tasks/investigate-external-control-api.md)。

> ステータス凡例:
> - **Confirmed**: コードまたは既存ドキュメントから確認できた
> - **Inferred**: コード構造・命名から推測した（要検証）
> - **Unknown / TBD**: 現時点で確認できない

調査日: 2026-06-15 / 調査対象コミット: `design/external-control-api-investigation` 分岐元（`main` HEAD `61aa8736`）

---

## 1. 目的

- 今後 Autona を Companion / Stream Deck から操作可能にする。ただし **Companion 専用 /
  Stream Deck 専用の実装に直接寄せない**。
- まず「**Autona 内部の操作を外部から安全に呼び出すための External Control API（汎用操作 API）**」
  として整理する。外部コントローラはその API を叩く**薄いクライアント**として扱う。
- 本書のゴールは実装ではなく、**既存操作の棚卸しと API 化候補の分類・粒度・境界の提案**。

---

> **2026-10-21 更新**: 本書が棚卸しした旧ルートは ADR-001 Addendum により **deprecated（v2.0 で削除予定、それまで挙動不変）** と宣言済み。外部制御の正本は `/api/v1`。

## 2. 重要な前提（Confirmed）

外部 API を設計する上で土台になる、コードから確認した事実。

| 事項 | 内容 | 出典 |
|------|------|------|
| 既存制御面は既にある | Flask + Socket.IO の HTTP/WS 制御面が稼働中。HTTP ルートと Socket.IO イベントが**対で**定義され、同じ操作を両方で提供 | [src/AutonaWebServer.py](../../src/AutonaWebServer.py) |
| 実処理は1か所に集約 | 全ルートが `WebServer.actions`（`WebServerActions`）へ委譲。**API 化の自然な単一接続点** | [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py) |
| 状態変更は単一経路 | Actions は **Qt Signal を emit → 各ウィジェット → StateManager.Set** で状態変更。直接 dict 書き込みなし。多くが `@gui_thread_sync` で GUI スレッドに直列化 | 同上 / [src/StateManager.py](../../src/StateManager.py) |
| 既定ポート | `SettingsManager.Get("general.webserver_port", 5000)` = **5000**（設定で変更可） | [AutonaWebServer.py:71](../../src/AutonaWebServer.py) |
| バインド先 | `host = "0.0.0.0"` — **localhost 限定ではなく全 NIC で待ち受け** | [AutonaWebServer.py:70](../../src/AutonaWebServer.py) |
| 認証 | **なし**。CORS は `cors_allowed_origins='*'` / Flask-CORS 全許可 | [AutonaWebServer.py:47-56](../../src/AutonaWebServer.py) |
| Web UI ルート | `/`・`/scoreboard`・`/team`・`/web` が React UI（`web/build/index.html`）を配信。CLAUDE.md の記述と一致 | [AutonaWebServer.py:717-723](../../src/AutonaWebServer.py) |
| スコアボード番号 | ルートは `<scoreboardNumber>` を取る。現行は board1=1v1、board2=team、board3=crew を常時生成し、表示形式で切替。`/api/v1/` は board1/2 用操作を board3 へ投げる場合は拒否 | [src/AutonaScoreboardManager.py](../../src/AutonaScoreboardManager.py) / [src/AutonaApiV1.py](../../src/AutonaApiV1.py) |
| X / Bluesky 投稿 | **UI ボタン専用**。`PostToX` / `PostToBsky` はスコアボードウィジェット内。**HTTP/WS にもホットキーにも露出していない** | [AutonaScoreboardWidget.py:1114-1243](../../src/AutonaScoreboardWidget.py) |
| ホットキー | 9 アクション（後述）。Web ルートと操作が**重複**。Windows 専用（Win32 RegisterHotKey） | [src/AutonaHotkeys.py](../../src/AutonaHotkeys.py) |
| OBS 連携 | OBS-websocket 不使用。`out/` ファイル + ブラウザソース + WS デルタ配信。**Autona は OBS に接続しない**（OBS 未接続でも Autona 側操作は成立） | [docs/architecture/current-architecture.md](current-architecture.md) |

> ⚠️ **セキュリティ上の含意（要 PdM 判断）**: 現状の制御面は `0.0.0.0` + 認証なし + CORS 全許可。
> 既に「LAN 内の誰でも全操作を叩ける」状態。外部 API を**新規追加するわけではなく、この前提を引き継ぐ**。
> 外部コントローラ追加を機に認証/バインド方針を見直すかは ADR-001 の論点。

---

## 3. 既存操作の棚卸し（HTTP / WS ルート全量）

`AutonaWebServer.py` の全ルートを列挙。R=読み取り / W=書き込み（状態変更）/ U=UI 起動（ウィンドウ・選択を開く）/ S=静的配信。
WS 列は対応する Socket.IO イベントの有無。

| # | HTTP ルート | 種別 | WS | Action 関数 | 操作の要点 |
|---|------------|------|----|-----------|-----------|
| 1 | `GET /program-state` | R | ✅ `program_state`/`program-state-update` | `program_state` | 全状態取得（フル） |
| 2 | `POST /score` | W | ✅ `score` | `post_score` | スコア一括設定（`reset_score:true` 付き） |
| 3 | `GET /scoreboard<N>-team<T>-scoreup` | W | ✅ `team_scoreup` | `team_scoreup` | チーム T のスコア +1 |
| 4 | `GET /scoreboard<N>-team<T>-scoredown` | W | ✅ `team_scoredown` | `team_scoredown` | チーム T のスコア -1 |
| 5 | `GET /scoreboard<N>-team<T>-color-<color>` | W | ✅ `team_color` | `team_color` | チームカラー設定 |
| 6 | `GET /scoreboard<N>-crew-member<T>-<M>-stockup` | W | ✅ `crew_member_stock` | `crew_member_stock(+1)` | クルー戦ストック +1（board3 想定） |
| 7 | `GET /scoreboard<N>-crew-member<T>-<M>-stockdown` | W | ✅（同上） | `crew_member_stock(-1)` | クルー戦ストック -1 |
| 8 | `GET /scoreboard<N>-crew-active<T>-<M>` | W | ✅ `crew_set_active` | `crew_set_active` | 出場中クルーメンバー設定（0=なし） |
| 9 | `GET /scoreboard<N>-get` | R | — | `get_scoreboard` | スコアボード N の状態取得 |
| 10 | `GET /scoreboard<N>-set` | W | ✅ `set` | `set_route` | best-of/phase/match/players/characters/losers をクエリで設定 |
| 11 | `POST /scoreboard<N>-update-team-<T>-<P>` | W | ✅ `update_team` | `set_team_data` | 選手データ更新 |
| 12 | ~~`POST /set-light-mode`~~ | — | ❌ 削除済み | — | テーマ切替機能ごと削除（ダーク固定、Design.md §5.4）。30s フリーズの原因 |
| 13 | `POST /update-commentary-<caster>` | W | ✅ `update_commentary` | `set_commentary_data` | 実況者データ更新 |
| 14 | `POST /update-game` | W | ✅ `update_game` | `set_game` | 選択ゲーム変更（**アセット再ロード=重い**） |
| 15 | `GET /games` | R | ✅ `games` | `get_games` | ゲーム一覧 |
| 16 | `GET /current-game` | R | ✅ `current_game` | `get_current_game` | 現在ゲーム |
| 17 | `GET /match-names` | R | ✅ `match_names` | `get_match_names` | match/phase 名候補 |
| 18 | `GET /characters` | R | ✅ `characters` | `get_characters` | キャラ一覧（ゲームアセットカタログを直接読む。GUI スレッドを経由しない。ADR-005 S1b） |
| 19 | `GET /variants` | R | ✅ `variants` | `get_variants` | バリアント一覧 |
| 20 | `GET /controllers` | R | — | — | ~~コントローラ一覧~~（機能ごと削除済み） |
| 21 | `GET /scoreboard<N>-swap-teams` | W | ✅ `swap_teams` | `swap_teams` | チーム左右入替 |
| 22 | `GET /scoreboard<N>-get-swap` | R | ✅ `get_swap` | `get_swap` | 入替状態取得 |
| 23 | `GET /scoreboard<N>-open-set` | U | ✅ `open_set` | `open_sets` | **セット選択ウィンドウを開く** |
| 24 | `GET /scoreboard<N>-pull-stream` | W/U | ✅ `pull_stream` | `pull_stream_set` | 配信セット取り込み |
| 25 | `GET /pull-user` | W/U | ✅ `pull_user` | `pull_user_set` | ユーザーセット取り込み |
| 26 | `GET /scoreboard<N>-stats-recent-sets` | W | ✅ `stats_recent_sets` | `stats_recent_sets` | 直近セット統計の再取得 |
| 27 | `GET /scoreboard<N>-stats-upset-factor` | W | ✅ `stats_upset_factor` | `stats_upset_factor` | アップセット計算 |
| 28 | `GET /scoreboard<N>-stats-last-sets-<player>` | W | ✅ `stats_last_sets` | `stats_last_sets` | 選手の直近成績 |
| 29 | `GET /scoreboard<N>-stats-history-sets-<player>` | W | ✅ `stats_history_sets` | `stats_history_sets` | 選手の通算成績 |
| 30 | `GET /scoreboard<N>-reset-scores` | W | ✅ `reset_scores` | `reset_scores` | スコアのみリセット |
| 31 | `GET /scoreboard<N>-reset-match` | W | ✅ `reset_match` | `reset_match` | スコア+best_of+ラウンド名/フェーズ/プール リセット（2026-10-21〜） |
| 32 | `GET /scoreboard<N>-reset-players` | W | ✅ `reset_players` | `reset_players` | 選手情報クリア |
| 33 | `GET /scoreboard<N>-clear-all` | W | ✅ `clear_all` | `clear_all` | **全消去（スコア/best_of/人数/キャラ/選手/ラウンド名・フェーズ・プール）** |
| 34 | `GET /scoreboard<N>-get-thumbnail-<fmt>` | R/W | — | `get_thumbnail` | サムネ生成して画像返却（**WebEngine=重い**） |
| 35 | `GET /get-sets` | R | ✅ `get_sets` | `get_sets` | 試合一覧（provider 無なら `[]`） |
| 36 | `GET /get-match-<setId>` | R | ✅ `get_match` | `get_match` | セット詳細 |
| 37 | `GET /get-comms` | R | ✅ `get_comms` | `get_comms` | 実況者状態 |
| 38 | `GET /scoreboard<N>-load-set` | W | ✅ `load_set` | `load_set` | set ID 指定でセットをロード |
| 39 | `GET /scoreboard<N>-get-set` | R | ✅ `get_set` | `get_set` | 最後に選択した set ID |
| 40 | `GET /playerdb` | R | ✅ `playerdb` | `get_playerdb` | ローカル選手 DB（**個人情報を含みうる**） |
| 41 | `GET /update-bracket` | W | ✅ `update_bracket` | `update_bracket` | ブラケット再取得（Start.gg） |
| 42 | `GET /scoreboard<N>-load-player-from-tag-<T>-<P>` | W | ✅ `load_player_from_tag` | `load_player_from_tag` | タグから選手ロード |
| 43 | `GET /load-commentator-from-tag-<caster>` | W | ✅ `load_commentator_from_tag` | `load_commentator_from_tag` | タグから実況者ロード |
| 44 | `GET /set-tournament` | W | ✅ `set_tournament` | `load_tournament` | **大会 URL 設定=コンテキスト総入替（重い）** |
| 45 | `GET /states` | R | ✅ `states` | `get_states` | 国の州一覧 |
| 46 | `/` `/scoreboard` `/team` `/web` | S | — | `web_index` | React UI 配信 |
| 47 | `GET/POST /<path:filename>` | S | — | `file_request` | 静的ファイル配信 |

> ⚠️ **二重定義の保守リスク（Confirmed）**: ほぼ全操作が HTTP と WS の両方に存在し、引数の取り方
> （クエリ vs JSON）や既定 scoreboardNumber（crew 系は `"3"`、他は `"1"`）が微妙に異なる。
> 外部 API を**第3の入口として増やすと三重管理**になる。→ ADR-001 の「既存拡張 vs アダプタ層」論点。

> **重要な発見**: X / Bluesky 投稿・サムネのクリップボードコピー等は **Web ルートに存在しない**
> （UI ボタンのみ）。「配信中によく使う操作」の一部は現状 API 化されていない。

### 3.1 ホットキー（Confirmed）

`AutonaHotkeys`（Windows 専用 Win32）。Web ルートと操作が重複する。

| アクション | 既定キー | 接続先 | 対応 Web ルート |
|-----------|---------|--------|----------------|
| `load_set` | Ctrl+Shift+O | `ScoreboardWidget.LoadSetClicked` | #23/#38 近傍 |
| `team1_score_up` | Ctrl+Shift+1 | ScoreboardWidget | #3 |
| `team1_score_down` | Ctrl+Shift+2 | ScoreboardWidget | #4 |
| `team2_score_up` | Ctrl+Shift+3 | ScoreboardWidget | #3 |
| `team2_score_down` | Ctrl+Shift+4 | ScoreboardWidget | #4 |
| `reset_scores` | Ctrl+Shift+R | `ScoreboardWidget.ResetScore` | #30 |
| `swap_teams` | Ctrl+Shift+S | `ScoreboardWidget.SwapTeams` | #21 |
| `refresh_phase_group` | Ctrl+Shift+P | `BracketWidget.PhaseGroupChanged` | #41 近傍 |
| `limit_export` | Ctrl+Shift+B | `BracketWidget` 限定エクスポート切替 | （Web ルートなし） |

> Inferred: ホットキーで露出している操作 ≒ 「配信中に最頻で使う操作」。
> Companion / Stream Deck の初回ターゲットはこの集合と強く重なる。

---

## 4. API 化候補の分類（A〜E）

| 分類 | 定義 |
|------|------|
| **A** | 外部コントローラから**即時に呼べると有用**（安全・冪等寄り・高頻度） |
| **B** | 有用だが**誤操作対策・確認・ロックが必要**（破壊的・重い・コンテキスト総入替） |
| **C** | **読み取り専用 API として先に提供**すべき情報（状態/候補/接続性） |
| **D** | **API 化すべきでない / 初回対象外**（GUI 起動・UI 専用・未露出機能） |
| **E** | **既存仕様が不明**で追加確認が必要 |

### A. 即時に呼べると有用

| 操作 | 既存ルート | 根拠 |
|------|-----------|------|
| スコア +1 / -1（team1/2） | #3 #4 | 最頻・ホットキーにもある・低リスク |
| チーム左右入替 | #21 | ホットキーにある・低リスク |
| スコアのみリセット | #30 | 試合切替で多用・影響限定 |
| クルー戦ストック ±1 / 出場メンバー設定 | #6 #7 #8 | 競技性能上は A だが board3 限定（Inferred）→ 初回は対象ゲーム次第 |
| best-of / match / phase 設定 | #10 | 試合進行で頻用・値設定系 |

### B. 誤操作対策・確認・ロックが必要

| 操作 | 既存ルート | リスク要因 |
|------|-----------|-----------|
| 全消去 `clear-all` | #33 | 配信中に押すと盤面が消える（破壊的） |
| 選手クリア `reset-players` | #32 | 選手情報消失 |
| match リセット | #31 | best_of まで戻る |
| セットロード `load-set` / 配信セット取込 | #38 #24 #25 | 進行中の盤面を上書き |
| ゲーム変更 `update-game` | #14 | アセット再ロードで重い・全 UI 影響 |
| 大会 URL 設定 `set-tournament` | #44 | **コンテキスト総入替・Start.gg 通信・最重量** |
| ブラケット再取得 `update-bracket` | #41 | Start.gg 通信・タイムアウトしうる（agent-pitfalls #11） |
| ~~テーマ切替 `set-light-mode`~~ | #12 | **削除済み**。ダーク固定化により API 化対象外 |
| サムネ生成 | #34 | WebEngine で重い・連打で競合（agent-pitfalls #10） |

### C. 読み取り専用 API として先に提供

| 情報 | 既存ルート |
|------|-----------|
| 全状態 / スコアボード状態 / 入替状態 | #1 #9 #22 |
| 現在ゲーム / ゲーム一覧 / キャラ / バリアント / コントローラ | #16 #15 #18 #19 #20 |
| match・phase 名候補 | #17 |
| 試合一覧 / セット詳細 / 最終選択 set | #35 #36 #39 |
| 実況者状態 | #37 |
| 国の州一覧 | #45 |
| 接続性・OBS 出力健全性 | `GET /api/v1/health` 実装済み。`revision` / `currentGame` / `activeBoard` / `outLastWriteUnix` を返す |

> 選手 DB（#40）は読み取りだが**個人情報を含みうる**ため C ではなく B 寄りで扱う（CLAUDE.md 機密ルール）。

### D. API 化すべきでない / 初回対象外

| 操作 | 既存ルート | 理由 |
|------|-----------|------|
| セット選択ウィンドウを開く `open-set` | #23 | **GUI ウィンドウ起動**。ヘッドレス/外部操作に不向き |
| 静的ファイル配信 / UI 配信 | #46 #47 | API 操作ではない |
| X 投稿 / Bluesky 投稿 | （未露出） | 現状 UI 専用・外部送信を伴う・確認ダイアログ前提。初回は据え置き（E にも該当） |
| ホットキー設定変更 | （未露出） | 運用体系を壊しうる |

### E. 既存仕様が不明（要確認）

| 論点 | 不明点 |
|------|--------|
| scoreboardNumber の有効値 | `1`/`2`/`3`。board1=1v1、board2=team、board3=crew。board3 専用操作は `/api/v1/` 初回スコープ外 |
| `set_team_data` のスキーマ | `data` の正確な形（キー・必須項目）が未文書化 |
| 統計再取得系の副作用範囲 | #26〜#29 が Start.gg をどれだけ叩くか・結果の反映先 |
| X / Bluesky の API 化是非 | 外部送信・認証情報・確認 UI の扱い（機密ルール） |
| `assets/rulesets.json` | ステージストライク削除後の残置物。外部 API と無関係か |

---

## 5. 操作ごとの詳細（代表例）

各操作について「現在の UI 導線 / 関連コード / 状態 / 副作用 / 本番リスク / 価値 / 初回採否 / 推奨粒度」を記載。
全 47 ルートは §3 の表で網羅済み。ここでは設計判断に効く代表 7 例を掘り下げる。

### 5.1 スコア +1 / -1（A）

- **UI 導線**: スコアボードの +/- ボタン、ホットキー Ctrl+Shift+1〜4
- **関連コード**: `WebServerActions.team_scoreup/down` → `signals.CommandScoreChange.emit(idx, ±1)`（[AutonaWebServerActions.py:59](../../src/AutonaWebServerActions.py)）
- **状態**: `score.{N}.team.{T}.score`
- **副作用**: WS デルタ配信 → OBS/Web UI 反映。best_of 到達時の挙動は widget 側ロジック依存（Inferred）
- **本番リスク**: 低（誤爆しても ∓1 で復旧容易）
- **価値**: 最高（Companion/Stream Deck の主目的）
- **初回採否**: **含める**
- **推奨粒度**: `POST /api/v1/scoreboards/{n}/teams/{t}/score:increment`（delta 指定可）

### 5.2 全消去 `clear-all`（B）

- **UI 導線**: スコアボードのクリア操作
- **関連コード**: `clear_all`（`@gui_thread_sync`）→ `AutonaActions.ResetMatch`（スコア・LOSERS・match バー）+ best_of=0 + 人数/キャラ=1 + `CommandClearAll`（= `AutonaActions.ClearPlayers`。ADR-005 S2d）（[AutonaWebServerActions.py](../../src/AutonaWebServerActions.py)）。デスクトップの「すべての入力値をリセット」（`AutonaActions.ClearBoard`）とは意図して違い、人数・キャラ数を戻し、チーム名は人数を 1 に減らす場合だけ消える（ADR-005 S2 判断 3）
- **状態**: `score.{N}` 配下を広範に初期化
- **副作用**: 盤面全消去 → OBS に即反映
- **本番リスク**: **高**（配信中に押すと表示が空になる）
- **価値**: 中（試合間リセットに使うが頻度は低い）
- **初回採否**: **含めるが要ガード**（確認フラグ or 物理長押し相当）
- **推奨粒度**: `POST /api/v1/scoreboards/{n}:clear` に `confirm=true` 必須

### 5.3 大会 URL 設定 `set-tournament`（B）

- **UI 導線**: 「大会を選ぶ」/ Startgg メニュー
- **関連コード**: `load_tournament` → URL 正規化 → `SettingsManager.Set("TOURNAMENT_URL")` + `tournament_url_update.emit`（[AutonaWebServerActions.py:447](../../src/AutonaWebServerActions.py)）
- **状態**: 大会コンテキスト全体（bracket / sets / players）
- **副作用**: **Start.gg 大量通信**・全 UI 再構成。タイムアウトしうる
- **本番リスク**: **最高**（配信前の準備操作。本番中に誤発火すると盤面総入替）
- **価値**: 低〜中（配信前に1回。Companion から叩く必然性は薄い）
- **初回採否**: **除外**（読み取りで現在 URL を見せるに留める）
- **推奨粒度**: 初回は対象外。将来も confirm + 非配信時のみ

### 5.4 テーマ切替 `set-light-mode`（B）— ❌ 削除済み

> テーマ切替機能は削除済み（ダーク固定、[Design.md §5.4](../../.claude/design/Design.md)）。
> 切替時に `qdarktheme.setup_theme()` が proxy 埋め込みブラケットを全再ポリッシュし、
> GUI スレッドが 30s 以上フリーズしていたため。本候補は対象外。

### 5.5 セット選択ウィンドウを開く `open-set`（D）

- **UI 導線**: セット選択ボタン
- **関連コード**: `open_sets` → `signals.SetSelection.emit()`（**GUI ウィンドウ表示**）
- **理由**: 外部コントローラから GUI ウィンドウを開いてもオペレータが PC 前で操作する必要があり、ボタン一発の価値が薄い。`load-set`（ID 指定）で代替可
- **初回採否**: **除外**

### 5.6 全状態取得 `program-state`（C）

- **UI 導線**: なし（Web UI / OBS が購読）
- **関連コード**: `program_state` → `StateManager.GetFullState()`
- **価値**: 高（Companion のボタン状態フィードバック＝点灯/数値表示に必須）
- **初回採否**: **含める（読み取り）**。差分は WS `program_state_update` を購読
- **推奨粒度**: `GET /api/v1/state`（フル）＋ WS 購読（既存を流用）

### 5.7 X 投稿 `PostToX`（D/E）

- **UI 導線**: スコアボードヘッダのボタンのみ（**Web/WS 未露出**）
- **関連コード**: `AutonaScoreboardWidget.PostToX`（[AutonaScoreboardWidget.py:1137](../../src/AutonaScoreboardWidget.py)）— サムネをクリップボードへ → `twitter.com/intent/tweet` をブラウザで開く
- **副作用**: **外部送信**・ブラウザ起動・クリップボード上書き
- **本番リスク**: 中（誤投稿）。Bluesky は App Password（機密）
- **初回採否**: **除外**（外部送信・確認 UI 前提・機密。需要が固まってから別途検討）

---

## 6. 読み取り API 候補 / 操作 API 候補のまとめ

### 読み取り API（先行提供推奨）
- `GET /api/v1/state`（= #1 のラップ）＋ WS 差分購読
- `GET /api/v1/scoreboards/{n}`（= #9）、`/swap`（= #22）
- ゲーム/キャラ/バリアント/コントローラ/match 名（= #15〜#20）
- 試合一覧 / セット詳細（= #35 #36 #39）
- **接続性・健全性**: `GET /api/v1/health` — webserver 稼働・現在ゲーム・表示中ボード・`out/` 最終更新時刻等。Companion の死活監視に有用（実装済み）

### 操作 API（初回スコープ）
- スコア ±1（team1/2）、swap、reset-scores、best-of/match/phase 設定
- （対象ゲーム次第で）crew ストック ±1 / active

### 操作 API（要ガード・初回は慎重 or 除外）
- clear-all / reset-players / reset-match（破壊的 → confirm 必須）
- load-set / pull-stream / pull-user（盤面上書き）
- update-game / set-tournament / update-bracket（重い・コンテキスト影響）
- set-light-mode（削除済み。再導入しない）

---

## 7. 推奨 API 境界・命名・endpoint 案

> いずれも**提案**。確定は ADR-001。初回は**過度に抽象化しない**。

### 7.1 境界方針（推奨: アダプタ層を薄く重ねる）

- 外部制御は **`WebServerActions` を唯一の実処理として再利用**する（状態変更ロジックを二重化しない）。
- 既存ルート（`/score`・`/scoreboard<N>-...`）は**契約を一切変えず温存**（既存クライアント/OBS 互換）。
- 新 API は **`/api/v1/` 名前空間に薄いラッパーとして追加**し、内部で既存 Actions を呼ぶ。
  → 既存ルートと三重管理になる懸念は「**Actions を単一の真実とする**」ことで緩和。
- **不変条件**: 状態変更は必ず `StateManager` 経由（バイパス禁止）。GUI スレッド直列化（`@gui_thread_sync`）を尊重。

### 7.2 命名方針

- リソース指向 + 動詞は明示アクション（REST 風 + `:action`）。
- 破壊的操作は必ず動詞で表現し、`confirm` を要求。
- バージョン固定 `/api/v1/`（既存ルートと衝突しない前缀）。

### 7.3 endpoint 案（抜粋）

| 操作 | 案 | payload | 備考 |
|------|----|---------|------|
| 状態取得 | `GET /api/v1/state` | — | フル。差分は WS |
| 健全性 | `GET /api/v1/health` | — | 新規 |
| スコア加算 | `POST /api/v1/scoreboards/{n}/teams/{t}/score:adjust` | `{"delta": 1}` | ±1 |
| スコアリセット | `POST /api/v1/scoreboards/{n}/score:reset` | — | |
| 入替 | `POST /api/v1/scoreboards/{n}:swap-teams` | — | |
| セット情報設定 | `PATCH /api/v1/scoreboards/{n}/set` | `{"bestOf":5,"phase":"...","match":"..."}` | #10 相当 |
| 全消去 | `POST /api/v1/scoreboards/{n}:clear` | `{"confirm": true}` | B・要ガード |
| セットロード | `POST /api/v1/scoreboards/{n}:load-set` | `{"setId":"...","noMains":false}` | B |

### 7.4 response 案

```json
{ "ok": true, "data": { ... }, "revision": 1234 }
```
- 操作系は `{"ok": true, "revision": <最新リビジョン>}` を返し、クライアントが差分購読と突合できるようにする。
- `revision` は既存 WS デルタの `revision`/`delta_index`（[AutonaWebServer.py:126](../../src/AutonaWebServer.py)）と整合させる。

### 7.5 エラー設計

- HTTP ステータス + `{"ok": false, "error": {"code": "...", "message": "..."}}`。
- 代表コード（案）: `INVALID_ARGUMENT`（400）/ `NOT_READY`（409, 大会/試合未選択）/ `CONFLICT`（409, 重い操作の多重実行）/ `UPSTREAM_TIMEOUT`（504, Start.gg）/ `INTERNAL`（500）。
- 既存ルートは `"OK"` / 文字列エラーを返す素朴な契約（Confirmed）。**新 API のみ構造化**し、既存は変えない。

### 7.6 idempotency

- **絶対値設定（PATCH set / score:reset / swap）は自然に冪等寄り**。
- **相対操作（score:adjust ±1）は冪等でない** → 二重送信対策が要る。Companion はボタン連打や再送が起きうるため、`Idempotency-Key` ヘッダ受理を**推奨（初回は任意実装）**。
- 重い操作（set-tournament/update-game/update-bracket）は**実行中フラグで多重起動を弾く**（409）。

### 7.7 操作ログ

- 外部 API 経由の**書き込み操作はログ必須**（誰が/いつ/何を）。本番事故の追跡に効く。
- 既存は `before_request`/`after_request` が **DEBUG レベル**でパスのみ記録（[AutonaWebServer.py:73-84](../../src/AutonaWebServer.py)）。
  外部 API は **INFO レベルで操作内容を別ロガー**に出すことを推奨（読み取りはログしない or DEBUG）。

### 7.8 本番中の誤操作防止策

- 破壊的操作（clear/reset-players/reset-match）は `confirm=true` 必須。
- 重い/コンテキスト操作（set-tournament/update-game/update-bracket）は初回から提供済み。
  `202 Accepted` + 実行中フラグで多重起動を防ぐ。配信中/非配信中の自動判定は未実装。
- レート制限 / デバウンス（同一操作の連打吸収）。サムネ・統計の連打は既存でも競合注意（agent-pitfalls #10/#11）。

### 7.9 読み取り API と操作 API の分離

- パスで分離（`GET /api/v1/...` = 読み取り、`POST/PATCH ...:action` = 操作）。
- 将来認証を入れる場合、**読み取りと操作で権限スコープを分けられる**設計にしておく（初回は実装しないが境界だけ用意）。

---

## 8. 初回実装候補 / 除外候補

> **確定（ADR-001 D6・2026-06-15）**: 初回スコープは「操作系も広く一括（危険操作も confirm 付きで含める）」。
> いずれも既存 Actions の薄いラッパーとし、新規状態ロジックは書かない。

### 初回に含める — 読み取り
1. `GET /api/v1/state`（= #1。+ 既存 WS 差分購読の踏襲）
2. `GET /api/v1/health`（新規・死活/現在ゲーム/`out/` 最終更新）
3. 補助読み取り（現在ゲーム・match名候補・試合一覧・セット詳細 = #16 #17 #35 #36 #39）

### 初回に含める — 安全操作
4. スコア ±1（team1/2 = #3 #4）
5. swap-teams（= #21）
6. score:reset（スコアのみ = #30）
7. set 情報設定（best-of / match / phase = #10）

### 初回に含める — 危険操作（**D7 のガード必須**）
8. clear-all / reset-players / reset-match（= #33 #32 #31）→ **`confirm=true` 必須**
9. load-set / pull-stream / pull-user（= #38 #24 #25）→ 盤面上書き。confirm 推奨
10. set-tournament / update-game / update-bracket（= #44 #14 #41）→ **実行中フラグで 409**・重い
11. ~~set-light-mode（= #12）~~ → 削除済み。ダーク固定のため初回対象外。

> 危険操作は全て **書き込み操作ログ（INFO）** の対象（D7・A-1）。重い操作（10）は多重起動ガード（D7・B-1）。

### 初回から除外（据え置き）
- open-set（= #23・GUI ウィンドウ起動）→ load-set で代替
- X / Bluesky 投稿（未露出・外部送信・機密・確認 UI 前提）
- 選手 DB 全量取得（= #40・個人情報）
- crew 系（= #6〜#8）→ 対象ゲーム（board3）運用が固まってから（Inferred）

---

## 9. 状態管理上の注意

- **書き込みは必ず StateManager 経由**（Actions が emit する Qt Signal → widget → `StateManager.Set`）。外部 API も同じ経路を通す。
- **GUI スレッド直列化**: 多くの Action が `@gui_thread_sync`。外部 API も別スレッド（QThread）からこの仕組みを尊重して呼ぶ。直接 widget を触らない。
- **同時操作の競合（Confirmed の範囲 + Inferred）**: 手動 UI・ホットキー・Web・外部 API はいずれも最終的に GUI イベントループ上で直列処理される（Inferred: gui_thread_sync + signal emit のため）。結果は概ね「**処理順＝到着順、最後勝ち**」。明示的なロック/排他は無い → 重い操作中の割り込みは要検証（E）。
- **SaveBlock の対称性**（agent-pitfalls #6）: 一括更新を外部 API でまとめる場合 `with StateManager.SaveBlock():` を使う。

---

## 10. エラー設計上の注意

- 既存ルートは成功時 `"OK"`、失敗時も 200 で文字列を返すものが多い（Confirmed）。**外部クライアントは HTTP ステータスだけでは成否を判別できない** → 新 API は構造化レスポンス必須。
- `get_match` / `update_bracket` は provider 前提で、**大会未ロード時に例外/空**になりうる（Inferred）→ `NOT_READY` で返す設計。
- Start.gg 系（set-tournament/update-bracket/get-match）は **20 秒タイムアウト + 指数バックオフ**（agent-pitfalls #11）→ `UPSTREAM_TIMEOUT` を用意。

---

## 11. Companion 対応時の注意

- Companion は**ボタンのフィードバック（点灯・数値）**を多用する → 読み取り API + WS 差分が前提。`program-state` 購読で代替可。
- Companion の HTTP/WS Generic モジュールで叩ける形（シンプルな POST + JSON）に寄せる。ただし **Companion のボタン設計に API を引きずられない**（API は汎用、Companion 側で組み立てる）。
- 連打・再送が起きやすい → idempotency / デバウンス（§7.6/§7.8）。

## 12. Stream Deck 対応時の注意

- Stream Deck 単体プラグインを作る場合も、**プラグインは API クライアントに徹する**（状態ロジックを持たせない）。
- 多くの運用は **Stream Deck → Companion → Autona API** の三段になりうる。Autona 側は「外部は薄いクライアント」前提を崩さない。
- 物理ボタン故の誤爆（隣のボタン）を考え、破壊的操作はソフト側 confirm を持つ。

---

## 13. Open questions

- `set_team_data` の payload スキーマ（必須キー）は？（E / 要コード追読）
- scoreboardNumber の有効値と board2/board3 の現行扱いは？（E）
- 配信中/非配信中を Autona が判定する手段はあるか？（誤操作ガードの前提・`TBD`）
- 認証を入れるか／バインドを localhost に絞るか（現状 `0.0.0.0` + 認証なし）→ ADR-001
- 重い操作の多重実行を現状どう防いでいるか（実行中フラグの有無）→ コード追読（E）
- X / Bluesky を将来 API 化するか（外部送信・機密）→ ADR-001 / E
- `assets/rulesets.json`（ステージストライク残置）は外部 API と無関係か → 別件で整理

---

## 14. 関連ドキュメント

- **I/F 仕様（正本・計画）**: [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md)
- 影響分析: [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)
- 設計境界 ADR: [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)
- 調査タスク: [../tasks/investigate-external-control-api.md](../tasks/investigate-external-control-api.md)
- 現アーキ: [current-architecture.md](current-architecture.md)
- プロダクト視点: [../product/features.md](../product/features.md)
- 実装仕様の正本: [../../.claude/design/Design.md](../../.claude/design/Design.md)
- 地雷集: [../../.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)
</content>
</invoke>
