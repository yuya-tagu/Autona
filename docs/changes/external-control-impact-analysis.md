# External Control Impact Analysis

> ⚠️ **これは影響分析のプレースホルダー**。この段階で API 設計を確定しない。
> 既存運用への影響の「観点」を洗い出すことが目的。設計境界は
> [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)、
> 事前調査は [../tasks/investigate-external-control-api.md](../tasks/investigate-external-control-api.md)。

> ステータス凡例: ✅ 確認済 / 📄 ドキュメント由来 / ⚠️ 要確認 / `TBD`

## Purpose

Companion / Stream Deck 連携・外部制御 API・OBS 連携拡張を追加する前に、
**既存の実運用を壊さないために検討すべき影響**を網羅的に列挙する。

## Background

- Autona は既に実運用レベルで完成しており、配信現場が既存挙動に依存している ✅
- 既に **Flask + Socket.IO の HTTP/WS 制御面**が存在する（約 30+ ルート）✅
  → 外部制御は「ゼロからの新設」ではなく「既存制御面の上での拡張」になる
- OBS-websocket は不使用方針。OBS 連携は `layout/` HTML + `out/` ファイル参照 📄

## Proposed change

⚠️ 未確定。想定される変更の方向性のみ（設計確定は ADR-001 で行う）:

- 外部（Companion / Stream Deck / その他ツール）から Autona を制御する API/トリガー
- 既存の HTTP/WS ルートを使う案 / 専用アダプタ層を新設する案（→ ADR-001）

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

- OBS-websocket 連携 📄（検討外）
- Discord 連携 📄（検討外）
- YouTube / Twitch API 連携 📄（検討外）
- 認証/認可基盤の大規模新設（必要性が確定するまで）⚠️
- 既存ルートの破壊的リネーム/再設計

## Open questions

- 既存 HTTP/WS ルートの**完全な一覧と入出力契約**は？（未棚卸し）⚠️
- 既定ポートと Web UI ルーティングの**実値**は？（README/FEATURES と CLAUDE.md で記述差）⚠️
- Companion / Stream Deck が求める**プロトコル**は HTTP / WS / どちら？ `TBD`
- 外部制御に**認証**は必要か？（LAN 内のみ運用か）`TBD`
- 同時操作の競合解決ポリシー（最後勝ち / ロック / キュー）は？ `TBD`
- ステージストライク削除後に残る `assets/rulesets.json` の扱いは？ `TBD`

## Recommended next steps

1. [../tasks/investigate-external-control-api.md](../tasks/investigate-external-control-api.md) を実施し、
   既存制御面・ポート・ルーティングの実態を確定する
2. 調査結果で本ファイルの ⚠️ / `TBD` を埋める
3. [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md) で
   責務境界（既存拡張 vs 専用アダプタ）を決定する
4. 決定後、実装チケットを `tasks/` に切り出す（本ドキュメント作業の範囲外）
