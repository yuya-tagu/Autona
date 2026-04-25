# Start.gg API 完全リファレンス

Start.gg が公式に提供している API（developer.start.gg）と、Autona が利用している内部 API の両方を含めた仕様書。
未使用 API を含めた全エントリポイント・型定義・制限事項を網羅し、機能追加時の判断材料にする。

> **対象**: 2026-04-26 時点の Start.gg 公開 GraphQL スキーマ
> **公式ドキュメント**: https://developer.start.gg/docs/intro
> **スキーマブラウザ**: https://smashgg-schema.netlify.app/
> **API Explorer**: https://developer.start.gg/explorer
> **Autona の使用状況**: 別ドキュメント [StartggAPI.md](StartggAPI.md) 参照

---

## 0. ドキュメント全体マップ

| ドキュメント | 内容 |
|------------|------|
| **本書** | Start.gg が公式提供する全 API の仕様（未使用含む） |
| [StartggAPI.md](StartggAPI.md) | Autona が現時点で実際に使用している API と内部実装の解説 |

---

## 1. API の種類

Start.gg が提供する API は実質 2 系統。Autona は両方を使い分けている。

| 系統 | エンドポイント | 認証 | 用途 | Autona 使用 |
|------|--------------|------|------|------------|
| **公開 GraphQL API** | `https://api.start.gg/gql/alpha` | **必須**（Bearer token） | 公開された安定版 API。SDK や 3rd party 連携の標準 | ❌ 未使用 |
| **内部 GraphQL API** | `https://www.start.gg/api/-/gql` | 不要（Cookie/UA） | Web フロントエンドが使う非公開 API。フィールドが豊富 | ✅ 主に使用 |
| **旧 REST v1** | `https://api.smash.gg/phase_group/{id}` | 不要 | レガシー REST。一部 phaseGroup 全体取得で残る | ✅ 一部使用 |
| **旧 REST setTask** | `https://www.start.gg/api/-/gg_api./set/{id};expand=["setTask"]` | 不要 | setTask（ストライク履歴）取得。GraphQL では取れない | ✅ 使用 |

### 1.1 公開 vs 内部の違い

| 観点 | 公開 GraphQL | 内部 GraphQL |
|-----|------------|-------------|
| トークン | 必須（`Authorization: Bearer ...`） | UA 偽装で OK |
| 公開フィールド数 | 限定的（このドキュメント記載のフィールドのみ） | 内部用フィールドも露出（`setTask`, `seedMap`, `wProgressingName` 等） |
| レートリミット | 60 秒で 80 req、リクエストあたり最大 1000 オブジェクト | 公式には未公開（実測 5 並列くらいまでは安全） |
| 安定性 | バージョンドキュメント化されたスキーマ | 予告なく変更される可能性 |
| トークン管理 | 1 年で失効、年次更新が必要 | 不要 |
| OAuth | 対応 | 対応外 |

**Autona が内部 API を選んだ理由**（[StartggAPI.md](StartggAPI.md) 1.1 節）:
- トークン管理を避けたい
- `setTask`（ステージストライク履歴）が公開 API では取れない
- `seedMap` や `wProgressingName` などフィールドが豊富

**公開 API への移行を検討すべき場面**:
- 大規模配布アプリで内部 API のスキーマ変更耐性が問題になる場合
- OAuth で個人データ（メールアドレス等）にアクセスする場合
- mutation（`reportBracketSet` 等）を使う場合 — 内部 API では認証越しでないと不可

---

## 2. 認証

### 2.1 公開 API トークン認証

#### 取得手順
1. https://www.start.gg/admin/profile/developer にアクセス
2. "Create new token" をクリック
3. 説明文を入力して保存
4. **生成直後にコピー**（再表示不可）
5. **1 年で失効** — 年次でローテーション必須

#### リクエスト形式
```http
POST https://api.start.gg/gql/alpha
Authorization: Bearer {token}
Content-Type: application/json

{"query": "...", "variables": {...}}
```

### 2.2 OAuth 2.0（Authorization Code Grant）

ユーザー個人データへのアクセス（メール等）が必要な場合のみ使用。

#### フロー
1. クライアントがリソースオーナー（ユーザー）を Start.gg の認可 URL にリダイレクト
2. ユーザーが Start.gg にログイン
3. ユーザーがスコープを承認
4. Start.gg が `redirect_uri` に認可コードを送信
5. アプリケーションが認可コードを `api.start.gg/oauth/refresh` 経由でアクセストークンに交換

#### リフレッシュ
アクセストークンに有効期限あり。`refresh_token` を使い `api.start.gg/oauth/refresh` に POST して再発行。

> **OAuth 用途は限定的**。Autona のような閲覧専用ツールでは API トークンで十分。OAuth は「ユーザーに代わって `registerForTournament` する」「個人メアドを取得する」等のときに必要。

---

## 3. レートリミットと制限

### 3.1 公開 API のレートリミット

| 制限 | 値 | 違反時の挙動 |
|-----|---|-------------|
| **リクエスト頻度** | 60 秒平均で 80 req/min | `{"success": false, "message": "Rate limit exceeded - api-token"}` 返却 |
| **オブジェクト数** | 1 リクエストあたり最大 1000 オブジェクト（ネスト含む） | エラー応答 |
| **クエリ複雑度** | 公式数値非公開だが、深いネストや大量 perPage は拒否される | 同上 |

### 3.2 ページネーション上限

| エンティティ | perPage 上限 |
|-----------|------------|
| `tournaments` | 512 |
| `events` | デフォルト推奨 50 以下 |
| `entrants` / `participants` | 64〜500（実測） |
| `sets` | 64〜500（実測。`perPage: 9999` は内部 API のみ） |
| `seeds` | 500 |
| `recentStandings` | 20（最大）、デフォルト 3 |

> **オブジェクト数 1000 の計算**: `perPage: 50` × `nested 20 fields` で 1000 オブジェクト到達。深いネストではさらに早く上限に到達するため、フラットなクエリを心がける。

### 3.3 内部 API の事実上の制限

公式制限は未公開だが、Autona の運用知見:

- **タイムアウト 20 秒**: レスポンス 1 MB 超で頻発
- **並列上限 ~10**: 同時 10 並列以上だと 5xx が散発
- **キャッシュ**: 旧 REST `gg_api.` は積極的にキャッシュされる。`bustCache=true` 必須

---

## 4. 公開 API: トップレベル Query 一覧

`https://api.start.gg/gql/alpha` にアクセス可能な全エントリポイント。

| クエリ | 引数 | 戻り値 | 説明 |
|-------|------|--------|------|
| `currentUser` | なし | `User` | 認証済みユーザー本人（OAuth 必須） |
| `entrant` | `id: ID!` | `Entrant` | ID 指定でエントラント取得 |
| `event` | `id: ID`, `slug: String` | `Event` | ID または slug でイベント取得 |
| `league` | `id: ID`, `slug: String` | `League` | ID または slug でリーグ取得 |
| `leagues` | `query: LeagueQuery!` | `LeagueConnection` | リーグ一覧（フィルタ・ページネーション） |
| `participant` | `id: ID!`, `isAdmin: Boolean` | `Participant` | 参加者取得 |
| `phase` | `id: ID` | `Phase` | フェーズ取得 |
| `phaseGroup` | `id: ID` | `PhaseGroup` | フェーズグループ取得 |
| `player` | `id: ID!` | `Player` | プレイヤー取得 |
| `seed` | `id: ID` | `Seed` | シード取得 |
| `set` | `id: ID!` | `Set` | セット取得 |
| `shop` | `id: ID`, `slug: String` | `Shop` | ショップ情報 |
| `stream` | `id: ID!` | `Streams` | ストリーム取得 |
| `streamQueue` | `tournamentId: ID!`, `includePlayerStreams: Boolean` | `[StreamQueue]` | 大会のストリームキュー一覧 |
| `team` | `id: ID`, `slug: String`, `inviteCode: String` | `Team` | チーム取得 |
| `tournament` | `id: ID`, `slug: String` | `Tournament` | 大会取得 |
| `tournaments` | `query: TournamentQuery!` | `TournamentConnection` | 大会検索（位置・ゲーム・期間） |
| `user` | `id: ID`, `slug: String` | `User` | ユーザー取得 |
| `videogame` | `id: ID`, `slug: String` | `Videogame` | ゲームタイトル取得 |
| `videogames` | `query: VideogameQuery!` | `VideogameConnection` | ゲームタイトル検索 |

---

## 5. 公開 API: Mutation 一覧

書き込み操作。**ほとんどが大会管理者権限を必要とする**。Autona は読み取り専用ツールなので未使用。

| Mutation | 引数 | 戻り値 | 用途 |
|---------|------|--------|------|
| `assignStation` | `setId: ID!`, `stationId: ID!` | `Set` | セットにステーション割当 |
| `assignStream` | `setId: ID!`, `streamId: ID!` | `Set` | セットにストリーム割当 |
| `deletePhase` | `phaseId: ID!` | `Boolean` | フェーズ削除 |
| `deleteStation` | `stationId: ID!` | `Boolean` | ステーション削除 |
| `deleteWave` | `waveId: ID!` | `Boolean` | ウェーブ削除 |
| `generateRegistrationToken` | `registration: TournamentRegistrationInput!`, `userId: ID!` | `String` | 登録トークン生成 |
| `markSetCalled` | `setId: ID!` | `Set` | セットを called 状態に |
| `markSetInProgress` | `setId: ID!` | `Set` | セットを in-progress に |
| `registerForTournament` | `registration: TournamentRegistrationInput`, `registrationToken: String` | `Participant` | 大会登録 |
| **`reportBracketSet`** | `setId: ID!`, `winnerId: ID`, `isDQ: Boolean`, `gameData: [BracketSetGameDataInput]` | `[Set]` | **セット結果報告（最重要）** |
| `resetSet` | `setId: ID!`, `resetDependentSets: Boolean` | `Set` | セットリセット |
| `resolveScheduleConflicts` | `tournamentId: ID!`, `options: ResolveConflictsOptions` | `[Seed]` | スケジュール競合解決 |
| `swapSeeds` | `phaseId: ID!`, `seed1Id: ID!`, `seed2Id: ID!` | `[Seed]` | シードスワップ |
| `updateBracketSet` | `setId: ID!`, `winnerId: ID`, `isDQ: Boolean`, `gameData` | `Set` | セット結果更新（勝者変更不可） |
| `updatePhaseGroups` | `groupConfigs: [PhaseGroupUpdateInput]!` | `[PhaseGroup]` | フェーズグループ更新 |
| `updatePhaseSeeding` | `phaseId: ID!`, `seedMapping: [UpdatePhaseSeedInfo]!`, `options` | `Phase` | シーディング更新 |
| `updateVodUrl` | `setId: ID!`, `vodUrl: String` | `Set` | VOD URL 設定 |
| `upsertPhase` | `phaseId: ID`, `eventId: ID`, `payload: PhaseUpsertInput!` | `Phase` | フェーズ作成/更新 |
| `upsertStation` | `stationId: ID`, `tournamentId: ID`, `fields: StationUpsertInput!` | `Stations` | ステーション作成/更新 |
| `upsertWave` | `waveId: ID`, `tournamentId: ID`, `fields: WaveUpsertInput!` | `Wave` | ウェーブ作成/更新 |

### 5.1 `reportBracketSet` の使い方（参考）

```graphql
mutation ReportSet($setId: ID!, $winnerId: ID, $gameData: [BracketSetGameDataInput]) {
    reportBracketSet(setId: $setId, winnerId: $winnerId, gameData: $gameData) {
        id
        state
        entrant1Score
        entrant2Score
    }
}
```

**ゲーム単位で詳細報告したい場合の `gameData` 例**:
```json
{
    "gameNum": 1,
    "winnerId": 12345,
    "stageId": 6,
    "selections": [
        {"entrantId": 12345, "characterId": 1271},
        {"entrantId": 67890, "characterId": 1278}
    ]
}
```

> **注意**: この mutation は当該セットの「報告者」権限が必要。OAuth or admin token 必須。

---

## 6. 主要オブジェクト型のフィールド一覧

### 6.1 `Tournament`

| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | 大会識別子 |
| `name` | `String` | 大会名 |
| `slug` | `String` | URL スラッグ |
| `shortSlug` | `String` | 短縮 slug |
| `startAt` / `endAt` | `Timestamp` | 開催期間（unix） |
| `createdAt` / `updatedAt` | `Timestamp` | 作成/更新時刻 |
| `numAttendees` | `Int` | 参加者数 |
| `venueAddress` / `venueName` | `String` | 会場 |
| `addrState` / `city` / `countryCode` / `postalCode` | `String` | 住所 |
| `lat` / `lng` | `Float` | 緯度経度 |
| `mapsPlaceId` | `String` | Google Maps Place ID |
| `currency` | `String` | 通貨 |
| `hashtag` | `String` | SNS ハッシュタグ |
| `timezone` | `String` | タイムゾーン |
| `state` | `Int` | 大会状態 |
| `tournamentType` | `Int` | 大会種別 |
| `isOnline` | `Boolean` | オンラインイベントを含むか |
| `hasOnlineEvents` / `hasOfflineEvents` | `Boolean` | 各種イベント有無 |
| `isRegistrationOpen` | `Boolean` | 登録受付中か |
| `registrationClosesAt` / `eventRegistrationClosesAt` / `teamCreationClosesAt` | `Timestamp` | 各種締切 |
| `rules` | `String` | ルール説明 |
| `primaryContact` / `primaryContactType` | `String` | 主要連絡先 |
| `publishing` | `JSON` | 発行設定 |
| `links` | `TournamentLinks` | 関連リンク |
| `images(type: String)` | `[Image]` | 画像（type=`profile`/`banner`） |
| `owner` | `User` | 主催者 |
| `admins(roles: [String])` | `[User]` | 管理者一覧 |
| `events(limit: Int, filter)` | `[Event]` | 大会内のイベント一覧 |
| `participants(query, isAdmin: Boolean)` | `ParticipantConnection` | 参加者一覧（ページネーション） |
| `teams(query)` | `TeamConnection` | チーム一覧 |
| `streams` | `[Streams]` | 配信ストリーム |
| `streamQueue` | `[StreamQueue]` | 配信キュー |
| `stations(page, perPage)` | `StationsConnection` | ステーション |
| `waves` | `[Wave]` | ウェーブ |
| `url(tab, relative)` | `String` | 大会 URL |

### 6.2 `Event`

大会内の 1 競技。例: 「スマブラ SP シングルス」「Tekken 7 ダブルス」。

| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `name` | `String` | イベント名 |
| `slug` | `String` | URL スラッグ |
| `tournament` | `Tournament` | 親大会 |
| `videogame` | `Videogame` | 対象ゲーム |
| `league` | `League` | 関連リーグ（あれば） |
| `numEntrants` | `Int` | 参加者数 |
| `entryFee` | `Float` | 参加費 |
| `entrantSizeMax` / `entrantSizeMin` | `Int` | 旧式（非推奨） |
| `teamRosterSize` | `TeamRosterSize` | チーム人数要件 |
| `teamNameAllowed` | `Boolean` | カスタムチーム名可否 |
| `teamManagementDeadline` | `Timestamp` | チーム管理期限 |
| `startAt` | `Timestamp` | 開始時刻 |
| `state` | `ActivityState` | 状態 |
| `type` | `Int` | イベント種別 |
| `competitionTier` | `Int` | 競技レベル |
| `isOnline` | `Boolean` | オンライン判定 |
| `hasTasks` | `Boolean` | プレイヤータスク有効 |
| `hasDecks` | `Boolean` | デッキ機能有効 |
| `deckSubmissionDeadline` | `Timestamp` | デッキ提出期限 |
| `checkInEnabled` / `checkInBuffer` / `checkInDuration` | `Boolean`/`Int` | チェックイン設定 |
| `useEventSeeds` | `Boolean` | イベント独自シード |
| `rulesetId` | `Int` | ルールセット |
| `rulesetSettings` | `JSON` | ルールセット詳細（非推奨） |
| `matchRulesMarkdown` / `rulesMarkdown` | `String` | ルール説明 |
| `prizingInfo` | `JSON` | 賞金 |
| `publishing` | `JSON` | 公開設定 |
| `phases(state, phaseId)` | `[Phase]` | フェーズ一覧 |
| `phaseGroups` | `[PhaseGroup]` | フェーズグループ一覧 |
| `entrants(query)` | `EntrantConnection` | 参加者ページネーション |
| `sets(page, perPage, sortType, filters)` | `SetConnection` | セット一覧 |
| `standings(query!)` | `StandingConnection` | 順位表 |
| `stations(query)` | `StationsConnection` | ステーション |
| `waves(phaseId)` | `[Wave]` | ウェーブ |
| `userEntrant(userId)` | `Entrant` | 特定ユーザーの参加情報 |
| `images(type)` | `[Image]` | |
| `createdAt` / `updatedAt` | `Timestamp` | |

### 6.3 `Phase` / `PhaseGroup`

**Phase**: イベント内の段階（"Round 1 Pools", "Top 8" 等）
**PhaseGroup**: Phase 内の個別プール（"Pool A1", "Pool B2" 等）

#### `Phase`
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `name` | `String` | フェーズ名 |
| `event` | `Event` | 親イベント |
| `bracketType` | `BracketType` | DOUBLE_ELIMINATION / SINGLE / SWISS / ROUND_ROBIN / MATCHMAKING 等 |
| `groupCount` | `Int` | フェーズグループ数（>1 ならプール） |
| `numSeeds` | `Int` | シード数 |
| `state` | `ActivityState` | 状態 |
| `phaseOrder` | `Int` | イベント内順序 |
| `isExhibition` | `Boolean` | エキシビションかどうか |
| `phaseGroups(query)` | `PhaseGroupConnection` | フェーズグループ |
| `seeds(query, eventId)` | `SeedConnection` | シード |
| `sets(page, perPage, sortType, filters)` | `SetConnection` | セット |
| `progressionsInData` | `[ProgressionData]` | 流入元 |
| `progressions` | `[Progression]` | 流出先 |
| `waves` | `[Wave]` | |

#### `PhaseGroup`
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `phase` | `Phase` | 親フェーズ |
| `displayIdentifier` | `String` | "A1" 等の表示用識別子 |
| `bracketType` | `BracketType` | |
| `bracketUrl` | `String` | ブラケット表示 URL |
| `numRounds` | `Int` | |
| `firstRoundTime` | `Timestamp` | 開始予定 |
| `startAt` | `Timestamp` | |
| `state` | `Int` | |
| `wave` | `Wave` | |
| `seedMap` | `JSON` | シード配置マップ（内部 API では取得可、公開 API では非推奨） |
| `seeds(query, eventId)` | `SeedConnection` | シード（最大 500） |
| `sets(page, perPage, sortType, filters)` | `SetConnection` | セット |
| `standings(query)` | `StandingConnection` | 順位表 |
| `progressionsOut` | `[Progression]` | 流出先 |
| `rounds` | `[Round]` | ラウンド情報 |
| `tiebreakOrder` | `JSON` | タイブレーク順 |

### 6.4 `Set`

| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `identifier` | `String` | プール内識別子（"F", "AT" 等） |
| `round` | `Int` | ラウンド番号（負数 = Losers） |
| `fullRoundText` | `String` | "Winners Round 1" 等 |
| `event` | `Event` | |
| `phaseGroup` | `PhaseGroup` | |
| `state` | `Int` | 1=created, 2=active, 3=completed, 4=ready, 5=invalid, 6=called |
| `startAt` / `startedAt` / `completedAt` | `Timestamp` | |
| `createdAt` / `updatedAt` | `Timestamp` | |
| `slots(includeByes: Boolean)` | `[SetSlot]` | 参加スロット（通常 2） |
| `displayScore(mainEntrantId: ID)` | `String` | スコア文字列 |
| `winnerId` | `Int` | 勝者 entrant ID |
| `wPlacement` / `lPlacement` | `Int` | 勝者/敗者の順位 |
| `winnerProgressionSeed` / `loserProgressionSeed` | `Seed` | 進出先シード |
| `entrant1Source` / `entrant2Source` | `SetEntrantSource` | エントラントソース |
| `setGamesType` | `Int` | best-of / total-games |
| `totalGames` | `Int` | 総ゲーム数 |
| `games` | `[Game]` | ゲーム一覧 |
| `game(orderNum: Int!)` | `Game` | 個別ゲーム |
| `station` | `Stations` | 物理ステーション |
| `stream` | `Streams` | 配信 |
| `vodUrl` | `String` | アーカイブ URL |
| `images(type)` | `[Image]` | |
| `hasPlaceholder` | `Boolean` | プレースホルダー含むか |
| `resetAffectedData` | `ResetAffectedData` | リセット時の影響範囲 |

### 6.5 `Entrant` / `Participant` / `Player` / `User`

これらは似ているので混同注意。階層は **User → Player → Participant → Entrant**。

#### `User` — Start.gg のアカウント
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `slug` | `String` | URL slug |
| `discriminator` | `String` | slug ハッシュ部 |
| `name` | `String` | 公開名 |
| `bio` | `String` | 自己紹介 |
| `birthday` | `String` | 公開誕生日 |
| `email` | `String` | **OAuth 必須** |
| `genderPronoun` | `String` | 性別代名詞 |
| `location` | `Address` | 公開地域 |
| `images(type)` | `[Image]` | |
| `authorizations(types: [SocialConnectionType])` | `[ProfileAuthorization]` | Twitter/Twitch/YouTube 等の連携 |
| `player` | `Player` | 関連プレイヤー |
| `events(query)` | `EventConnection` | 参加イベント |
| `tournaments(query)` | `TournamentConnection` | 関連大会 |
| `leagues(query)` | `LeagueConnection` | 関連リーグ |

#### `Player` — ゲーム上のプレイヤー（user に紐づく）
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `gamerTag` | `String` | ゲーマータグ |
| `prefix` | `String` | スポンサー接頭辞 |
| `user` | `User` | 関連ユーザー |
| `rankings(limit, videogameId)` | `[PlayerRank]` | 公開ランキング |
| `recentStandings(videogameId, limit)` | `[Standing]` | 直近成績（最大 20） |
| `recentSets(opponentId)` | `[Set]` | 直近セット（非推奨） |
| `sets(page, perPage, filters)` | `SetConnection` | セット履歴 |

#### `Participant` — 特定大会への参加情報
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `gamerTag` | `String` | この大会で使ったタグ |
| `prefix` | `String` | スポンサー |
| `player` | `Player` | プレイヤー |
| `user` | `User` | ユーザー |
| `verified` | `Boolean` | 認証済み参加 |
| `checkedIn` | `Boolean` | チェックイン済み |
| `checkedInAt` | `Timestamp` | チェックイン時刻 |
| `email` | `String` | **管理者のみ** |
| `contactInfo` | `ContactInfo` | **管理者のみ** |
| `connectedAccounts` | `JSON` | 連携アカウント |
| `requiredConnections` | `[ProfileAuthorization]` | 必須連携 |
| `entrants` | `[Entrant]` | 関連エントラント（複数イベント参加時） |
| `events` | `[Event]` | 登録イベント |
| `images(type)` | `[Image]` | |

#### `Entrant` — ブラケット上の単位（チーム or 個人）
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `name` | `String` | ブラケット表示名（個人ならゲーマータグ、チームならチーム名） |
| `event` | `Event` | |
| `initialSeedNum` | `Int` | 初期シード番号 |
| `isDisqualified` | `Boolean` | DQ |
| `participants` | `[Participant]` | 構成メンバー（1v1 なら 1 人） |
| `seeds` | `[Seed]` | シード履歴 |
| `standing` | `Standing` | 最終順位 |
| `paginatedSets(page, perPage, sortType, filters)` | `SetConnection` | このエントラントが参加したセット |
| `team` | `Team` | リンク済みチーム |
| `streams` | `[Streams]` | |
| `skill` | `Int` | |

### 6.6 `Seed` / `Standing` / `Game`

#### `Seed`
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `seedNum` | `Int` | シード番号 |
| `groupSeedNum` | `Int` | グループ内シード |
| `placement` | `Int` | 最終順位 |
| `isBye` | `Boolean` | バイか |
| `placeholderName` | `String` | プレースホルダー名 |
| `entrant` | `Entrant` | |
| `players` | `[Player]` | |
| `phase` / `phaseGroup` | | |
| `progressionSource` | `Progression` | 流入元 |
| `progressionSeedId` | `Int` | |
| `checkedInParticipants` | `JSON` | |
| `setRecordWithoutByes(phaseGroupId)` | `JSON` | バイ除外戦績 |
| `standings(containerType)` | `[Standing]` | |
| `updatedAt` | `Timestamp` | |

#### `Standing`
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `placement` | `Int` | 順位 |
| `isFinal` | `Boolean` | 最終順位か |
| `entrant` | `Entrant` | |
| `player` | `Player` | |
| `container` | `StandingContainer` | コンテキスト（イベント/フェーズ等） |
| `metadata` | `JSON` | |
| `setRecordWithoutByes` | `JSON` | |
| `stats` | `StandingStats` | 統計 |
| `totalPoints` | `Float` | リーグ等のポイント |
| `standing` | `Int` | （非推奨） |

#### `Game` — set 内の 1 ゲーム
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `orderNum` | `Int` | ゲーム番号 |
| `winnerId` | `Int` | 勝者 entrant ID |
| `entrant1Score` / `entrant2Score` | `Int` | スコア |
| `state` | `Int` | |
| `stage` | `Stage` | 使用ステージ |
| `selections` | `[GameSelection]` | キャラ選択 |
| `images(type)` | `[Image]` | |

### 6.7 `Videogame` / `League` / `Team`

#### `Videogame`
| フィールド | 型 | 説明 |
|-----------|----|------|
| `id` | `ID` | |
| `name` / `displayName` | `String` | |
| `slug` | `String` | |
| `characters` | `[Character]` | 全キャラ |
| `stages` | `[Stage]` | 全ステージ |
| `images(type)` | `[Image]` | |

#### `League`
リーグ（複数大会の連結）。Tournament とほぼ同じフィールド構成。
- `events`, `standings`, `tiers(EventTier[])`, `numUniquePlayers`, `entrantCount`, `videogames` などが固有
- `tiers` でリーグ内の階層分類

#### `Team`（インターフェース）
- 実装: `GlobalTeam`（プロチーム等）と `EventTeam`（大会内チーム）
- `id`, `name`, `discriminator`, `images`, `members(status)`

### 6.8 `Stations` / `Streams` / `StreamQueue`

#### `Stations`
| フィールド | 型 |
|-----------|----|
| `id` | `ID` |
| `number` | `Int` |
| `prefix` | `String` |
| `identifier` | `Int` |
| `clusterNumber` / `clusterPrefix` | |
| `enabled` | `Boolean` |
| `state` | `Int` |
| `numSetups` | `Int` |
| `queue` | `JSON` |
| `queueDepth` | `Int` |
| `canAutoAssign` | `Boolean` |
| `stream` | `Streams` |
| `updatedAt` | `Timestamp` |

#### `Streams`
| フィールド | 型 |
|-----------|----|
| `id` | `ID` |
| `streamId` | `String` |
| `streamName` | `String` |
| `streamSource` | `StreamSource` (TWITCH / YOUTUBE / HITBOX 等) |
| `streamGame` | `String` |
| `streamLogo` | `String` |
| `streamStatus` | `String` |
| `streamType` / `streamTypeId` | `Int` |
| `shortName` | `String` |
| `enabled` / `isOnline` | `Boolean` |
| `followerCount` | `Int` |
| `numSetups` | `Int` |
| `parentStreamId` | `Int` |

#### `StreamQueue`
- `id` (String), `sets ([Set])`, `stream (Streams)`

---

## 7. Input 型

### 7.1 `SetFilters` — セット検索の必須入力

| フィールド | 型 | 説明 |
|-----------|----|------|
| `state` | `[Int]` | 1=created, 2=active, 3=completed, 4=ready, 5=invalid, 6=called |
| `entrantIds` / `participantIds` / `playerIds` | `[ID]` | 各種 ID で絞り込み |
| `phaseIds` / `phaseGroupIds` / `eventIds` / `tournamentIds` | `[ID]` | 上位構造で絞り込み |
| `stationIds` / `stationNumbers` | `[ID]` / `[Int]` | ステーションで絞り込み |
| `roundNumber` | `Int` | 特定ラウンドのみ |
| `entrantSize` | `Int` | 1v1 = 1 |
| `hasVod` | `Boolean` | VOD 付きのみ |
| `hideEmpty` | `Boolean` | エンプティセット除外 |
| `showByes` | `Boolean` | バイ含む |
| `isEventOnline` | `Boolean` | オンラインイベントのみ |
| `location` | `LocationFilter` | 地理絞り込み |
| `updatedAfter` | `Timestamp` | 差分取得用 |

### 7.2 `TournamentQuery` — 大会検索

| フィールド | 型 | 説明 |
|-----------|----|------|
| `page` | `Int` | ページ番号 |
| `perPage` | `Int` | 1 ページの件数（最大 512） |
| `sortBy` | `String` | レガシーソート |
| `sort` | `TournamentPaginationSort` | 新ソート |
| `filter` | `TournamentPageFilter` | フィルタ（位置・期間・videogame 等） |

### 7.3 `BracketSetGameDataInput` — 結果報告用

```graphql
input BracketSetGameDataInput {
    gameNum: Int!
    winnerId: ID
    stageId: ID
    selections: [BracketSetGameSelectionInput]
    score: Int
}
```

---

## 8. Autona 視点での「使うべき場所」マップ

| やりたいこと | 公開 API | 内部 API（Autona 現行） |
|------------|---------|----------------------|
| 大会基本情報取得 | `tournament(slug)` / `event(slug)` | `TournamentDataQuery` ✅ |
| 参加者一覧 | `event.entrants` | `EventEntrantsListQuery` ✅ |
| ブラケット構造 | `phaseGroup(id)` の seeds + sets | `TournamentPhaseGroupQuery` ✅ |
| セット詳細（new API） | `set(id)` | `SetQuery` ✅ |
| ステージストライク履歴 | **不可** | 旧 REST `setTask` ✅ |
| 配信キュー | `streamQueue(tournamentId)` | `StreamQueueQuery` ✅ |
| ステーション割当 | `tournament.stations` | `StationsQuery` ✅ |
| 選手 H2H | `player.sets(filters)` | `RecentSetsQuery` ✅ |
| 選手過去成績 | `player.recentStandings` | `TournamentHistoryDataQuery` ✅ |
| **セット結果報告** | `reportBracketSet` | **未対応**（OAuth 必須） |
| **シード変更** | `swapSeeds`, `updatePhaseSeeding` | 未対応 |
| **ステーション割当変更** | `assignStation`, `assignStream` | 未対応（必要なら大会管理権限が必要） |
| **VOD URL 設定** | `updateVodUrl` | 未対応 |

### 8.1 内部 API → 公開 API 移行時の注意

| Autona 利用フィールド | 公開 API での代替 | 注意 |
|-------------------|----------------|------|
| `seedMap` (PhaseGroup) | なし（非推奨化された） | seed の `groupSeedNum` から再構築 |
| `wProgressingName` / `lProgressingName` (Set, 旧 REST) | `set.winnerProgressionSeed.entrant.name` 等 | 公開 API では Seed 経由で同等情報取得可 |
| `setTask` (旧 REST) | **代替なし** | ストライク履歴は公開 API では不可。BST 用途には致命的なので内部 API を継続 |
| `event.tournament.streamQueue` | `streamQueue(tournamentId)` | ID 解決の追加クエリ要 |
| `entrants(perPage: 64)` | 同じ | 上限は同等 |

---

## 9. クエリ設計のベストプラクティス

### 9.1 オブジェクト数 1000 制限の見積もり

```graphql
event(slug: $s) {              # 1
    entrants(query: {perPage: 50}) {
        nodes {                # 50
            participants {     # 50 × 1〜5 = 50〜250
                player { ... } # 50〜250
                user { ... }   # 50〜250
            }
        }
    }
}
```
→ 1v1 イベントで `perPage: 50` なら ~250 オブジェクト（安全）。チームイベントで `perPage: 50` × 4 人 = 1000 到達。

**目安**:
- フラットなクエリは `perPage: 100` まで OK
- ネスト 2 段なら `perPage: 50`
- ネスト 3 段以上は `perPage: 25` 以下

### 9.2 Connection 型のページネーション

`xxxConnection` 型は全て同じ構造:
```graphql
{
    pageInfo {
        page
        total
        perPage
        totalPages
        sortBy
    }
    nodes { ... }
}
```

### 9.3 差分取得

更新があったセットだけ取りたい場合:
```graphql
event(slug: $s) {
    sets(page: 1, perPage: 50, filters: {updatedAfter: $lastSync}) {
        nodes { id state ... }
    }
}
```

→ ポーリング間隔と組み合わせて API 呼び出し量を抑える設計に有効。

### 9.4 タイムアウト回避

- 1 クエリで「全部」を取らず、`tournament` → `event` → `entrants` → `phaseGroup` を**段階的に取得**
- 重いネスト（`paginatedSets.games.selections`）は別クエリに分離
- preview ID（`preview_xxx_yyy_zzz`）は API リクエスト不可。クライアント側で扱う

---

## 10. 関連ドキュメント

| 文書 | 内容 |
|-----|------|
| [StartggAPI.md](StartggAPI.md) | Autona の現実装と内部 API の使い方 |
| https://developer.start.gg/docs/intro | 公式入口 |
| https://smashgg-schema.netlify.app/ | スキーマブラウザ（型定義） |
| https://developer.start.gg/explorer | GraphiQL Explorer（要ログイン） |
| https://developer.start.gg/docs/rate-limits | レート制限の正式仕様 |
| [.claude/architecture/state-keys.md](.claude/architecture/state-keys.md) | 取得結果の StateManager マッピング |
| [.claude/rules/agent-pitfalls.md](.claude/rules/agent-pitfalls.md) | Start.gg 関連の地雷 |

---

## 11. 用語集

| 用語 | 意味 |
|-----|------|
| **Tournament** | 大会全体 |
| **Event** | 大会内の競技種目（Smash SP シングルス等） |
| **Phase** | イベント内の段階（プール/トップ 8 等） |
| **PhaseGroup** | Phase 内の個別ブラケット（A1, B2 等） |
| **Wave** | 同時並行で進む組分け（朝の部/夜の部等） |
| **Set** | 試合 1 マッチ（best of N の単位） |
| **Game** | Set 内の 1 ゲーム |
| **Entrant** | ブラケット上の参加単位（個人 or チーム） |
| **Participant** | 特定大会への参加情報（Entrant のメンバー） |
| **Player** | ゲームプロファイル（user に紐づく） |
| **User** | Start.gg アカウント本体 |
| **Seed** | シード位置（連続でない場合あり） |
| **Standing** | 最終順位 |
| **Station** | 物理セットアップ（モニター + 台） |
| **Stream** | 配信ストリーム（Twitch/YouTube 等） |
| **StreamQueue** | 配信に乗る予定のセット列 |
| **Progression** | フェーズ間の進出経路 |
| **bracketType** | DOUBLE_ELIMINATION / SINGLE_ELIMINATION / ROUND_ROBIN / SWISS / MATCHMAKING / EXHIBITION |
| **state** (Set) | 1=created, 2=active, 3=completed, 4=ready, 5=invalid, 6=called |
| **state** (ActivityState) | CREATED / ACTIVE / COMPLETED / READY / INVALID 等 |
