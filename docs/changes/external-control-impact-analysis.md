# External Control Impact Analysis

> **ステータス: 外部制御 API 実装後の影響分析。**
> 既存運用への影響と、今後の Companion / Stream Deck / OBS 連携時に守るべき制約を整理する。
> 設計境界は
> [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)、
> 事前調査は [../tasks/investigate-external-control-api.md](../tasks/investigate-external-control-api.md)。

> ステータス凡例: ✅ 確認済 / 📄 ドキュメント由来 / ⚠️ 要確認 / `TBD`

## Purpose

実装済みの `/api/v1/` と、今後の Companion / Stream Deck 連携・OBS 連携拡張によって
**既存の実運用を壊さないために検討すべき影響**を列挙する。

## Background

- Autona は既に実運用レベルで完成しており、配信現場が既存挙動に依存している ✅
- 既に **Flask + Socket.IO の HTTP/WS 制御面**が存在する（HTTP ルート **約 45**＋対応 Socket.IO イベント）✅
  → 外部制御は「ゼロからの新設」ではなく「既存制御面の上での拡張」になる
- 外部制御 API は `/api/v1/` として既存 Flask+SocketIO サーバーに実装済み ✅
- Autona 本体は OBS へ直接接続しない。通常の OBS 連携は `layout/` HTML + `out/` ファイル参照 📄

> 操作の完全棚卸し・API 化候補・分類・endpoint 案は
> [../architecture/external-control-api-candidates.md](../architecture/external-control-api-candidates.md) を正とする。
> 本書はそれを参照し、**既存運用への影響**に絞る。

## Implemented change

- 外部（Companion / Stream Deck / その他ツール）から Autona を制御する `/api/v1/` を追加済み
- 既存の HTTP/WS ルートは温存し、新 API は `WebServerActions` を再利用する薄いラッパーとして実装
- 書き込み操作ログ、破壊的操作の `confirm:true`、重い操作の `202 Accepted` + 多重起動ガードを実装

## Existing workflows affected

外部制御を入れると影響しうる**既存ワークフロー**:

| ワークフロー | 影響観点 |
|------------|---------|
| デスクトップ UI でのスコア操作 | 外部からの同時操作との競合・最後勝ち/順序 ⚠️ |
| グローバルホットキー | 外部トリガーとキー操作の二重発火・状態不整合 ⚠️ |
| 既存 Web UI リモート操作 | 同一ルートを外部も叩く場合の負荷・競合 ⚠️ |
| OBS への配信出力（`out/` + WS） | 制御頻度増による出力遅延/欠落リスク ⚠️ |
| Start.gg ロード〜セット反映 | 外部制御で割り込んだ場合の状態整合 ⚠️ |
| レイアウト保存/復元 | 外部から表示切替する場合の STATE_VERSION 互換 ⚠️ |

## Existing modules affected

| モジュール | 影響観点 |
|-----------|---------|
| [src/AutonaWebServer.py](../../src/AutonaWebServer.py) | 新ルート/イベント追加の主戦場。HTTP/WS 二重定義の整合 ✅ |
| [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py) | 実処理の委譲先。外部制御もここを経由させるか ✅ |
| [src/StateManager.py](../../src/StateManager.py) | 状態変更の単一経路。バイパス禁止・ロック尊重 ✅ |
| シングルトン群 | 新サービス起動位置と初期化順の影響 ✅ |
| [src/AutonaHotkeys.py](../../src/AutonaHotkeys.py) | ホットキーと外部トリガーの責務重複 ✅ |

## Operational risks

- **同時操作の競合**: 複数経路（UI / ホットキー / Web / 外部）からの書き込み順序・最後勝ち ⚠️
- **配信遅延/欠落**: 制御頻度増で WebSocket デルタ配信や `out/` 書き込みが詰まる ⚠️
- **ポート/ルート衝突**: 新サービスが既定ポート・既存ルートと競合 ⚠️
- **認証なしの制御面拡大**: localhost 前提が崩れると意図しない外部操作リスク ⚠️
- **スタッフ運用の複雑化**: 既存の最短操作経路が見えにくくなる（運用事故）⚠️
- **起動順依存**: 外部リスナの起動位置を誤るとシングルトン初期化が破綻 ✅

## Compatibility requirements

外部制御を入れても**維持すべき不変条件**（暫定・PdM 確認要）:

1. 既存 HTTP/WS ルートの**契約（パス・入出力）を壊さない**（既存クライアント/オーバーレイ互換）
2. 状態変更は**必ず StateManager 経由**（直接書き込み禁止）
3. デスクトップ UI / ホットキー / 既存 Web UI の**操作経路を温存**（置換しない）
4. `out/` 出力・WebSocket 配信の**リアルタイム性を劣化させない**
5. レイアウト保存データ（STATE_VERSION）の**後方互換を維持**

## Non-goals

この変更で**やらないこと**（ROADMAP / 方針より）:

- Autona 本体から OBS を直接制御する機能（OBS-websocket クライアント化等）📄
- Discord 連携 📄（検討外）
- YouTube / Twitch API 連携 📄（検討外）
- 認証/認可基盤の大規模新設（必要性が確定するまで）⚠️
- 既存ルートの破壊的リネーム/再設計

## 調査で判明した事実（2026-06-15 / コード確認済）

[external-control-api-candidates.md](../architecture/external-control-api-candidates.md) の調査で、以前の ⚠️ / `TBD` の一部が確定した。

- ✅ **完全なルート一覧**: HTTP 約 45 + 対応 Socket.IO。全量は candidates §3 の表。ほぼ全操作が **HTTP と WS の二重定義**。
- ✅ **既定ポート = `5000`**（`SettingsManager.Get("general.webserver_port", 5000)`、[AutonaWebServer.py:71](../../src/AutonaWebServer.py)）。設定で変更可。
- ✅ **Web UI ルート = `/`・`/scoreboard`・`/team`・`/web`**（[AutonaWebServer.py:717-723](../../src/AutonaWebServer.py)）。CLAUDE.md の記述と一致（FEATURES/README が旧記述）。
- ✅ **バインド = `0.0.0.0`・認証なし・CORS 全許可**。既に「LAN 内の誰でも全操作を叩ける」状態。
- ✅ **状態変更は単一経路**: 全 Action が Qt Signal emit → widget → `StateManager.Set`。直接 dict 書き込みなし。多くが `@gui_thread_sync` で **GUI スレッドに直列化**。
- ✅ **X / Bluesky 投稿は UI ボタン専用**（Web/WS 未露出・ホットキー無し）。
- 🔎 **同時操作**: UI/ホットキー/Web/外部はいずれも GUI イベントループ上で直列処理される見込み（Inferred）。明示的ロックは無く「到着順・最後勝ち」。重い操作中の割り込み挙動は要検証。

## 解消済みの論点（ADR-001 / 2026-06-15）

[../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) で確定:

- ✅ **認証/バインド**: 現状維持（`0.0.0.0`+無認証+CORS 全許可、LAN 内前提）= D5。
  → ⚠️ **無認証 LAN 上で破壊的操作まで叩ける**リスクを受容（緩和は confirm + 操作ログのみ）。
- ✅ **API 境界**: `/api/v1/` を既存サーバーに薄く追加・`WebServerActions` 再利用・既存ルート温存 = D3。
- ✅ **初回スコープ**: 操作系も広く一括（危険操作も confirm 付き）= D6。
- ✅ **誤操作防止**: 書き込み操作ログ INFO（A-1）/ 重い操作は実行中フラグ 409（B-1）/ 破壊的操作 confirm 必須（C-2）= D7。

## Open questions / Revisit items

- Companion / Stream Deck 側の具体プリセット・ボタン設計（API は HTTP、フィードバックは既存 Socket.IO を購読）`TBD`
- 配信中/非配信中を Autona が**判定する手段**はあるか（将来の配信中ガードの前提・Revisit 条件）`TBD`
- `set_team_data` の **payload スキーマ**（必須キー）`TBD`
- ステージストライク削除後に残る `assets/rulesets.json` の扱いは？ `TBD`

## Recommended next steps

1. `/api/v1/` の手動確認手順を運用チェックリストへ統合する
2. Companion / Stream Deck 用の実プリセットを作る段階で、必要な読み取り値・ボタン粒度を再確認する
3. LAN 外運用や複数オペレーター運用が出たら ADR-001 の認証/認可方針を Revisit する
