# Start.gg API リファレンス

Autona が利用している Start.gg のエンドポイント・GraphQL クエリ・データフローを保守運用と機能追加のためにまとめたドキュメント。

> **対象コミット時点の情報**: 2026-04-26 / `claude/confident-wozniak-c22acb`
> 実装の主体は [src/TournamentDataProvider/StartGGDataProvider.py](src/TournamentDataProvider/StartGGDataProvider.py)（約 2,200 行）。

---

## 1. 概要

### 1.1 公式 / 非公式 API の使い分け

Autona は **Start.gg の公開 GraphQL API（developer key 必須）ではなく、Web フロントエンドが使う内部 API を利用している**。理由は token 不要で取得できる情報量が多く、`setTask`（キャラ・ステージ選択履歴）など公開 API では取れないデータが含まれるため。

| 種別 | エンドポイント | 認証 | 用途 |
|------|--------------|------|------|
| 内部 GraphQL | `https://www.start.gg/api/-/gql` | なし（Cookie/UA だけ） | ほぼ全てのデータ取得 |
| 旧 REST（v1） | `https://api.smash.gg/phase_group/{id}` | なし | フェーズグループ全体取得（`GetTournamentPhaseGroup` の seedMap 等） |
| 旧 REST（setTask） | `https://www.start.gg/api/-/gg_api./set/{setId};...;expand=["setTask"]` | なし | セットのタスク履歴（ストライク・キャラ選択の生データ） |

> **公開 GraphQL API（`api.start.gg/gql/alpha`）は使っていない**。トークン管理を避けるためと、`setTask` 相当の情報が取得できないため。

### 1.2 リクエストヘッダ

[StartGGDataProvider.py:55-60](src/TournamentDataProvider/StartGGDataProvider.py:55) の `QueryRequests` で全リクエスト共通に付与する。

```python
{
    "client-version": "20",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 ...)",
}
```

`User-Agent` を Web ブラウザ風にするのは Start.gg 側のレートリミット / 振り分け回避目的（過去に空 UA でブロックされた経緯）。

### 1.3 リトライ・タイムアウト

| 項目 | 値 | 設定箇所 |
|------|---|---------|
| リクエストタイムアウト | 20 秒 | `_request_timeout_secs = 20.0` ([StartGGDataProvider.py:41](src/TournamentDataProvider/StartGGDataProvider.py:41)) |
| リトライ回数 | 5 回 | [StartGGDataProvider.py:67](src/TournamentDataProvider/StartGGDataProvider.py:67) |
| バックオフ | 1s / 2s / 4s / 8s（指数） | [StartGGDataProvider.py:87](src/TournamentDataProvider/StartGGDataProvider.py:87) |
| 失敗時の戻り値 | `{}`（空 dict） | 例外を飲み込み呼び出し側を null-safe に書く前提 |

> **重要**: 重いクエリ（`TournamentPhaseGroupQuery` の seeds + sets）はレスポンスが 1 MB 超になると 20 秒で頻繁にタイムアウトする。フィールド追加時は [.claude/rules/agent-pitfalls.md:11](.claude/rules/agent-pitfalls.md) の「Start.gg GQL クエリの 20 秒タイムアウト」を必ず参照。

### 1.4 URL → eventSlug 変換

ユーザーが入力する URL は次の 2 形式を許容する（[AutonaTournamentDataProvider.py:141-143](src/AutonaTournamentDataProvider.py:141)）:

- `start.gg/tournament/{slug}/event[s]?/{eventSlug}` — 通常の URL
- `start.gg/admin/tournament/{slug}/brackets/{eventId}` — admin ページ URL

admin URL の場合は `TournamentSlugQuery` で eventId → eventSlug を解決する（[StartGGDataProvider.py:2147-2169](src/TournamentDataProvider/StartGGDataProvider.py:2147)）。
以降のクエリには `self.url.split("start.gg/")[1]` で抽出した `tournament/.../event/...` 形式の eventSlug を渡す。

---

## 2. クエリ一覧

GraphQL クエリは [src/TournamentDataProvider/](src/TournamentDataProvider/) に `.txt` ファイルで配置。`StartGGDataProvider.<QueryName>` クラス変数として起動時に読み込み（[StartGGDataProvider.py:2173-2195](src/TournamentDataProvider/StartGGDataProvider.py:2173)）。

| クエリ | ファイル | 主な引数 | 用途 |
|-------|---------|---------|------|
| `TournamentDataQuery` | [StartGGTournamentDataQuery.txt](src/TournamentDataProvider/StartGGTournamentDataQuery.txt) | `eventSlug` | 大会名・イベント名・参加者数・開催日・videogame 取得 |
| `TournamentIconQuery` / `TournamentBannerQuery` | （Python 内に直書き） | `eventSlug` | 大会アイコン / バナー画像 URL |
| `TournamentPhasesQuery` | [StartGGTournamentPhasesQuery.txt](src/TournamentDataProvider/StartGGTournamentPhasesQuery.txt) | `eventSlug` | フェーズ一覧（pool / bracket）取得 |
| `TournamentPhaseGroupQuery` | [StartGGTournamentPhaseGroupQuery.txt](src/TournamentDataProvider/StartGGTournamentPhaseGroupQuery.txt) | `id` | ブラケット 1 個分の seeds + sets 全取得（最大 500 seeds / 9999 sets） |
| `AnalysisPhaseGroupQuery` | [StartGGAnalysisPhaseGroupQuery.txt](src/TournamentDataProvider/StartGGAnalysisPhaseGroupQuery.txt) | `id` | 分析ドック用の軽量版（`user`/`images`/`authorizations` を除く） |
| `SetQuery` | [StartGGSetQuery.txt](src/TournamentDataProvider/StartGGSetQuery.txt) | `id` | 単一セット詳細（new API ルート） |
| `SetsQuery` (`EventMatchListQuery`) | [StartGGSetsQuery.txt](src/TournamentDataProvider/StartGGSetsQuery.txt) | `eventSlug`, `page`, `filters`, `perPage` | セット一覧（ステーション/ストリーム/フェーズ込み）。1 ページ 64 件 |
| `EntrantsQuery` (`EventEntrantsListQuery`) | [StartGGEntrantsQuery.txt](src/TournamentDataProvider/StartGGEntrantsQuery.txt) | `eventSlug`, `page` | 参加者一覧。1 ページ 64 件、ページネーション必須 |
| `StationsQuery` | [StartGGStationsQuery.txt](src/TournamentDataProvider/StartGGStationsQuery.txt) | `eventSlug` | 配信ステーション + ストリームキューの一覧 |
| `StationSetsQuery` | [StartGGStationSetsQuery.txt](src/TournamentDataProvider/StartGGStationSetsQuery.txt) | `eventSlug`, `filters` | ステーションに割り当てられたセット ID 列挙（最小フィールド） |
| `StreamQueueQuery` | [StartGGStreamQueueQuery.txt](src/TournamentDataProvider/StartGGStreamQueueQuery.txt) | `slug` | 配信キュー全体（次セットの選手情報込み） |
| `StreamSetsQuery` | [StartGGStreamSetsQuery.txt](src/TournamentDataProvider/StartGGStreamSetsQuery.txt) | `eventSlug` | 配信キューの最小フィールド版（先頭セット ID 取得用） |
| `FutureSetQuery` | [StartGGFutureSetQuery.txt](src/TournamentDataProvider/StartGGFutureSetQuery.txt) | `id` | 未開始セットの詳細（ステーション切替時の先読み） |
| `UserSetQuery` | [StartGGUserSetQuery.txt](src/TournamentDataProvider/StartGGUserSetQuery.txt) | `userSlug`, `filters` | 特定ユーザーの直近セット ID（実況者プロフィール連携） |
| `CompletedSetsQuery` | [StartGGCompletedSetsQuery.txt](src/TournamentDataProvider/StartGGCompletedSetsQuery.txt) | `eventSlug` | 直近 10 件の終了セット（state=3） |
| `LastSetsQuery` (`PlayerLastSetsQuery`) | [StartGGPlayerLastSetsQuery.txt](src/TournamentDataProvider/StartGGPlayerLastSetsQuery.txt) | `eventSlug`, `playerID` | 同イベント内の特定選手の直近 10 セット |
| `RecentSetsQuery` | [StartGGRecentSetsQuery.txt](src/TournamentDataProvider/StartGGRecentSetsQuery.txt) | `pid1`, `pid2`, `uid1`, `uid2`, `page`, `videogameId` | 2 選手の H2H（Head-to-Head）セット履歴 |
| `HistorySetsQuery` (`TournamentHistoryDataQuery`) | [StartGGPlayerTournamentHistoryQuery.txt](src/TournamentDataProvider/StartGGPlayerTournamentHistoryQuery.txt) | `playerID`, `gameID` | 選手の直近 10 大会成績 |
| `TournamentStandingsQuery` | [StartGGTournamentStandingsQuery.txt](src/TournamentDataProvider/StartGGTournamentStandingsQuery.txt) | `eventSlug`, `playerNumber` | 大会順位表 |
| `TournamentSlugQuery` | [StartGGTournamentSlugQuery.txt](src/TournamentDataProvider/StartGGTournamentSlugQuery.txt) | `id` | event ID → slug 解決（admin URL 入力時の前処理） |
| `FilteredSetsQuery` / `CompleteSetQuery` | [StartGGFilteredSetsQuery.txt](src/TournamentDataProvider/StartGGFilteredSetsQuery.txt) / [StartGGCompleteSetQuery.txt](src/TournamentDataProvider/StartGGCompleteSetQuery.txt) | （未使用） | 旧バージョンの残骸。削除候補 |

---

## 3. 機能別: API ↔ メソッド ↔ UI 対応

### 3.1 大会選択・初期ロード

```
ユーザーが URL 入力
  → AutonaTournamentDataProvider.SetTournament(url)
    → StartGGDataProvider(url) インスタンス生成
    → GetRealEventURL  …… admin URL なら TournamentSlugQuery
    → GetTournamentData …… TournamentDataQuery
    → GetTournamentPhases …… TournamentPhasesQuery
    → GetEntrants …… EventEntrantsListQuery（ページネーション）
```

| メソッド | クエリ | 戻り値の主要キー |
|---------|-------|----------------|
| `GetTournamentData` ([StartGGDataProvider.py:92](src/TournamentDataProvider/StartGGDataProvider.py:92)) | TournamentDataQuery | `tournamentName`, `eventName`, `numEntrants`, `address`, `shortLink`, `startAt`, `endAt`, `_videogame` |
| `GetIconURL` / `GetBannerURL` ([StartGGDataProvider.py:138](src/TournamentDataProvider/StartGGDataProvider.py:138)) | （Python 内インライン） | `images[0].url` |
| `GetTournamentPhases` ([StartGGDataProvider.py:210](src/TournamentDataProvider/StartGGDataProvider.py:210)) | TournamentPhasesQuery | `[{id, name, groups: [{id, name, bracketType}]}]` |
| `GetEntrants` / `GetEntrantsWorker` ([StartGGDataProvider.py:1487](src/TournamentDataProvider/StartGGDataProvider.py:1487), [:1870](src/TournamentDataProvider/StartGGDataProvider.py:1870)) | EventEntrantsListQuery | `AutonaPlayerDB` に登録 + `top_entrants` キャッシュ |

### 3.2 ブラケット表示

| メソッド | クエリ | 用途 |
|---------|-------|------|
| `GetTournamentPhaseGroup` ([StartGGDataProvider.py:248](src/TournamentDataProvider/StartGGDataProvider.py:248)) | TournamentPhaseGroupQuery + `https://api.smash.gg/phase_group/{id}` | メインのブラケットドック |
| `GetAnalysisPhaseGroup` ([StartGGDataProvider.py:386](src/TournamentDataProvider/StartGGDataProvider.py:386)) | AnalysisPhaseGroupQuery | 分析ドック専用（軽量版） |

**重要なデータ変形:**

- **seeds の連続化**: Start.gg は非連続な seedNum を返すことがあるため、`teams[seedNum-1]` がそのシードの位置に来るようプレースホルダーで埋める ([StartGGDataProvider.py:287-308](src/TournamentDataProvider/StartGGDataProvider.py:287))
- **preview ID のソート**: `preview_2004442_1_5` 形式は文字列ソートでは `1_11 < 1_2` になるため、末尾を整数化してソート ([StartGGDataProvider.py:317-324](src/TournamentDataProvider/StartGGDataProvider.py:317))
- **GF Reset の分離**: Start.gg は GF + GF Reset を同じ round に詰めて返すので、最終 round の 2 件目を `lastRound+1` に切り出す ([StartGGDataProvider.py:372-378](src/TournamentDataProvider/StartGGDataProvider.py:372))

### 3.3 セット情報の取得（スコアボード本体）

`GetMatch` は **2 つの API を並列に叩いて結果をマージ**する ([StartGGDataProvider.py:495-562](src/TournamentDataProvider/StartGGDataProvider.py:495))。

```
GetMatch(setId)
├─ _GetMatchTasks (旧 REST: gg_api./set/{setId};expand=["setTask"]) ── ParseMatchDataOldApi
│    └ ストライク履歴 / キャラ選択 / DSR/MDSR フラグ
└─ _GetMatchNewApi (GraphQL: SetQuery) ── ParseMatchDataNewApi
     └ プレイヤー情報 / フェーズ名 / ベストオブ / 配信元
```

`Worker.wait_for_all([fetchOld, fetchNew], 20)` で 20 秒待ち、両方の結果をマージ。
**preview ID の場合は旧 REST をスキップ**（`gg_api.` は preview ID 非対応）([StartGGDataProvider.py:565-566](src/TournamentDataProvider/StartGGDataProvider.py:565))。

#### 旧 REST `setTask` のデータ構造（重要）

`setTask` は **「ステージストライクや報告ごとのアクション履歴」を時系列で返す**。Autona はこれを後ろから走査して最新の状態を再構築する ([StartGGDataProvider.py:909-1125](src/TournamentDataProvider/StartGGDataProvider.py:909))。

| action | metadata 内 | 用途 |
|--------|------------|------|
| `setup_strike` / `setup_ban` / `setup_stage` / `setup_character` | `strikeStages`, `strikeList`, `stageSelection`, `banStages`, `banList`, `useDSR`, `useMDSR`, `turn` | ストライク状態 |
| `report` | `selections[].selectionType=character`, `winnerId`, `stageWins` | ゲーム結果報告 |

`charSelections` は entrant ID → character ID 配列の dict。

### 3.4 配信キュー / ステーション

| メソッド | クエリ | 説明 |
|---------|-------|------|
| `GetStations` ([StartGGDataProvider.py:660](src/TournamentDataProvider/StartGGDataProvider.py:660)) | StationsQuery | 物理ステーション + 配信ストリームを統合リスト化 |
| `GetStationMatchsId` ([StartGGDataProvider.py:1376](src/TournamentDataProvider/StartGGDataProvider.py:1376)) | StationSetsQuery | filter `state: [1,2,4,5,6]`（pending〜in-progress）。クライアント側で `station.id` でさらに絞る |
| `GetStreamMatchId` ([StartGGDataProvider.py:1338](src/TournamentDataProvider/StartGGDataProvider.py:1338)) | StreamSetsQuery | streamName 一致 + 同 event の最初のセットを返す |
| `GetStreamQueue` ([StartGGDataProvider.py:1285](src/TournamentDataProvider/StartGGDataProvider.py:1285)) | StreamQueueQuery | streamName ごとに `{1:..., 2:...}` 形式で返す（Web UI 表示用） |
| `GetFutureMatch` / `GetFutureMatchesList` ([StartGGDataProvider.py:2094](src/TournamentDataProvider/StartGGDataProvider.py:2094)) | FutureSetQuery | キュー内の各セットを並列で先読み |

> ステート定数（filters.state）:
> - 1=created, 2=active, 3=completed, 4=ready, 5=invalid, 6=called

### 3.5 統計・履歴

| メソッド | クエリ | UI |
|---------|-------|-----|
| `GetCompletedSets` ([StartGGDataProvider.py:1569](src/TournamentDataProvider/StartGGDataProvider.py:1569)) | CompletedSetsQuery（state=3, 直近 10） | 終了セット一覧 |
| `GetLastSets` ([StartGGDataProvider.py:1494](src/TournamentDataProvider/StartGGDataProvider.py:1494)) | PlayerLastSetsQuery | スコアボードの選手プロフィール |
| `GetRecentSets` / `GetRecentSetsWorker` ([StartGGDataProvider.py:1725](src/TournamentDataProvider/StartGGDataProvider.py:1725)) | RecentSetsQuery × **5 ページ × 2 方向 = 10 並列** | H2H 履歴。double-side で取得し ID で重複除去 |
| `GetPlayerHistoryStandings` ([StartGGDataProvider.py:1673](src/TournamentDataProvider/StartGGDataProvider.py:1673)) | TournamentHistoryDataQuery | 選手の過去成績 |
| `GetStandings` ([StartGGDataProvider.py:2053](src/TournamentDataProvider/StartGGDataProvider.py:2053)) | TournamentStandingsQuery | 大会順位表 |

### 3.6 ユーザー連携（実況者プロフィール）

`GetUserMatchId` ([StartGGDataProvider.py:1411](src/TournamentDataProvider/StartGGDataProvider.py:1411)) は `start.gg/user/{slug}` から:

1. 直近の active set（state 1/2/4/5/6）を `UserSetQuery` で取得
2. 該当なしなら filter 無しで再クエリ（直近の終了セット）
3. その set の event slug を抽出 → 大会自動切替（`SetTournament`）
4. プレイヤーが slot 2 にいる場合は `reverse=True` を立てる

---

## 4. データ変換のキモ

### 4.1 `ProcessEntrantData` ([StartGGDataProvider.py:1950](src/TournamentDataProvider/StartGGDataProvider.py:1950))

Start.gg の entrant → Autona の playerData に変換する中核。**全てのメソッドが最終的にこれを通る**。

| 出力キー | ソース | 備考 |
|---------|-------|------|
| `prefix` | `player.prefix` | スポンサータグ |
| `gamerTag` | `player.gamerTag` | ゲーマータグ |
| `name` | `player.name` | 本名 |
| `id` | `[player.id, user.id]` | 2 要素タプル。user.id が無ければ 0 |
| `twitter` | `user.authorizations[0].externalUsername` | TWITTER 認証のみ取得 |
| `pronoun` | `user.genderPronoun` | |
| `avatar` | `user.images[0].url` | profile タイプ |
| `country_code` | `user.location.country` を `CountryCatalog.CountryCodeFromEnName` で en_name → ISO コード変換 | |
| `state_code` | `user.location.state` を**そのまま使う** | city からの推測は削除済（[StartGGDataProvider.py:847-855](src/TournamentDataProvider/StartGGDataProvider.py:847)） |
| `city` | `user.location.city` | new API のみ |
| `mains` / `startggMains` | `player.sets.nodes[].games.selections` を集計 | `Counter` で出現頻度順。`GameAssetCatalog.CharacterFromStartGGId` でキャラコード解決（1 エントラント / 1 セットの解決は `Current()` を 1 回取った同じスナップショットで行う。ADR-005 S1b） |
| `seed` | `EntrantsQuery` の `initialSeedNum` から `player_seeds` キャッシュ参照 | クラス変数 `player_seeds = {playerId: seed}` |

### 4.2 ラウンド名翻訳 `TranslateRoundName` ([StartGGDataProvider.py:714](src/TournamentDataProvider/StartGGDataProvider.py:714))

Start.gg の `fullRoundText`（"Winners Round 1" 等）を `AutonaLocaleHelper.matchNames` のロケール対応キー（"winners_round" 等）に変換する。

固定マッピング（grand_final 等）+ パターン一致（`Winners`/`Losers`/`Round` で始まる）+ フォールバック（原文返却）の 3 段階。

### 4.3 set の round キーの意味

`TournamentPhaseGroupQuery` の `set.round` は数値で次のように使われる:

- 正の整数 = Winners Round N
- 負の整数 = Losers Round N
- 最大正値 = Grand Final
- 最大正値 + 1 = Grand Final Reset（Autona が分離して挿入）

`slots[].prereqType` は `"set"`（前のセットの勝者/敗者）か `"seed"`（シード直接配置）。Bracket トポロジーは `prereqId` を辿って復元する。

### 4.4 set の new API / old API のマージ戦略

| キー | new (SetQuery) | old (gg_api/setTask) | 採用 |
|------|---------------|---------------------|------|
| `entrants` | participants 詳細あり | character ID のみ | new を使い、old の `mains` を上書き ([StartGGDataProvider.py:546-554](src/TournamentDataProvider/StartGGDataProvider.py:546)) |
| `bestOf` | なし | あり | old |
| `stage_strike` / `ruleset` / `strikedBy` | なし | あり | old のみ |
| `winnerProgression` / `loserProgression` | なし | `wProgressingName`/`lProgressingName` | old |
| `round_name` | `fullRoundText` を翻訳 | なし | new |
| `isOnline` | あり | なし | new |
| `bestOf` を `None` に上書き | — | — | new で `isOnline=False` だったら強制 None ([StartGGDataProvider.py:541-542](src/TournamentDataProvider/StartGGDataProvider.py:541)) |

---

## 5. パフォーマンス・落とし穴

### 5.1 ペイロードサイズ警報

`TournamentPhaseGroupQuery` のレスポンスは bracket が大きいと **1 MB を超える**。以下のフィールドはネスト深いため追加すると即タイムアウトの危険:

- `user.images`（profile 画像 URL）
- `user.authorizations`（Twitter 等の外部連携）
- `player.sets.games.selections`（キャラ選択履歴）

→ 追加が必要なら **AnalysisPhaseGroupQuery と TournamentPhaseGroupQuery を両方更新する**か、**別クエリで遅延ロード**する。

### 5.2 ページネーション

| クエリ | perPage | 注意 |
|-------|---------|------|
| EventEntrantsListQuery | 64 | 全ページ走査必須。`PlayerDB.instance.AddPlayers(deferRefresh=True)` で各ページ累積 → 最後に 1 回 `PlayerDB.instance.SaveDB()` → `AutonaPlayerDB.SetTournamentPlayers(entrant_tags)` → `PlayerDB.instance.NotifyChanged()`（補完モデルの作り直しと `db_updated`）([StartGGDataProvider.py:2057-2067](src/TournamentDataProvider/StartGGDataProvider.py:2057)。順序は `tests/test_player_db.py` が pin) |
| EventMatchListQuery (SetsQuery) | 64 | キャンセル可能（`cancel_event.is_set()` 監視） |
| TournamentPhaseGroupQuery seeds | 500 | 1 ページ固定。500 超 の bracket は未対応 |
| TournamentPhaseGroupQuery sets | 9999 | 1 ページ固定 |

### 5.3 セット並列取得

- `_GetMatchTasks` + `_GetMatchNewApi` は QThreadPool で並列実行
- `GetRecentSetsWorker` は 5 ページ × 2 方向 = **10 ワーカー並列**
- `GetFutureMatchesList` はキュー内のセット数だけ並列で `FutureSetQuery` を発行

→ 多くの並列リクエストを発行する箇所では Start.gg 側のレート制限に注意。

### 5.4 キャッシュ無効化

`_GetMatchTasks` の旧 REST URL は明示的にキャッシュ無効化を指定:

```
;bustCache=true;expand=["setTask"];fetchMostRecentCached=true
+ params: {"extensions": {"cacheControl": {"version": 1, "noCache": True}}, ...}
```

これを外すと**ステージストライクの結果が古いまま固まる**ことがある。

---

## 6. 拡張する際のチェックリスト

新しい Start.gg データを取りたいときの手順:

1. **既存クエリで取れないか確認** — 同じ data type を取るクエリが複数あるので、フィールドを追加するだけで済むことが多い
2. **クエリを `.txt` に追加** — `src/TournamentDataProvider/StartGG{Name}Query.txt`
3. **`StartGGDataProvider` クラス変数として宣言** — クラス上部の `XxxQuery = None` の列に追加
4. **末尾の `readQueryFile` に登録** — [StartGGDataProvider.py:2173-2195](src/TournamentDataProvider/StartGGDataProvider.py:2173)
5. **メソッドを実装** — `QueryRequests` でリクエスト → `deep_get` で安全にパース → 例外は `logger.error(traceback.format_exc())` で握る
6. **`AutonaTournamentDataProvider` 経由で公開** — `Worker(self.provider.XxxMethod)` でスレッドプール起動 → result シグナルで UI に通知
7. **`TournamentDataProvider` 基底クラスに pass メソッドを追加** — 別プロバイダ実装時の備え（[TournamentDataProvider.py](src/TournamentDataProvider/TournamentDataProvider.py)）
8. **重いクエリは AnalysisPhaseGroupQuery と並行更新** — 軽量版の存在意義を保つ
9. **テストデータでパース検証** — [tests/test_startgg_parse.py](tests/test_startgg_parse.py) に追加

### よく踏む地雷

| 症状 | 原因 | 対策 |
|------|------|------|
| 20 秒タイムアウトで `{}` が返る | レスポンス 1 MB 超 | フィールド削減 or 別クエリに分割 |
| ブラケットの slot.{N} が選手とずれる | seedNum を position として使った | `seeds[seedNum-1]` 方式でプレースホルダー埋め |
| GF Reset が GF と同じ列に表示 | round 分離処理を忘れた | [StartGGDataProvider.py:372-378](src/TournamentDataProvider/StartGGDataProvider.py:372) パターンをコピー |
| `mains` が空 | ゲーム未選択（`GameAssetCatalog.Current().game` が空）/ キャラコード未対応 | `GameAssetCatalog.CharacterFromStartGGId` の戻り値を null チェック |
| `country_code` が空 | Start.gg 側が国名英語表記でない | `CountryCatalog` の国表の en_name と完全一致しないとマッチしない |

---

## 7. 関連ドキュメント

- [StartggAPI_full.md](StartggAPI_full.md) — Start.gg が公式提供する全 API（公開 GraphQL / OAuth / 全 Mutation 含む、Autona 未使用も網羅）
- [.claude/architecture/state-keys.md](.claude/architecture/state-keys.md) — Start.gg 取得結果が StateManager のどこに書かれるか
- [.claude/architecture/python-imports.md](.claude/architecture/python-imports.md) — `StartGGDataProvider` の依存関係
- [.claude/rules/agent-pitfalls.md](.claude/rules/agent-pitfalls.md) — 「Start.gg GQL クエリの 20 秒タイムアウト」セクション
- [.claude/rules/state-and-webserver.md](.claude/rules/state-and-webserver.md) — StateManager と Web Server の作法
