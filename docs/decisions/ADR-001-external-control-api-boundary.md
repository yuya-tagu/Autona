# ADR-001: External Control API Boundary

## Status

Proposed

> このADRは**プレースホルダー**。設計はまだ確定していない。確定前に必要な調査は
> [../tasks/investigate-external-control-api.md](../tasks/investigate-external-control-api.md)、
> 影響整理は [../changes/external-control-impact-analysis.md](../changes/external-control-impact-analysis.md)。

## Context

Companion / Stream Deck 連携、外部制御 API、OBS 連携拡張を予定している。
決めるべきは「**外部からの制御をどの面（レイヤー）で受けるか**」という責務境界。

確認済みの前提（コード確認・2026-06-14）:

- 既に **Flask + Socket.IO による HTTP/WS 制御面が存在**する
  （[src/AutonaWebServer.py](../../src/AutonaWebServer.py) /
  [src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py)）。
  スコア操作・Swap・Reset・統計取得・マスタ取得など約 30+ のルートがある。
- 同じ操作が **HTTP ルートと Socket.IO イベントの両方**で定義されている。
- すべての状態変更は **StateManager** を単一経路として通る。
- OBS-websocket は使わない方針（ROADMAP で検討外と明記）。

未確定（→ 調査タスク）:

- 既存ルートの完全な一覧・入出力契約・認証有無
- 既定ポートと Web UI ルーティングの実値
- 外部制御の認証/認可・スコープ要件
- Companion / Stream Deck 側が求めるプロトコル（HTTP / WS / その他）

## Decision

TBD

> 決定候補の例（未確定・優劣未評価）:
> - (A) 既存 Flask/SocketIO サーバーを拡張し、外部制御も同じ面で受ける
> - (B) 外部制御専用の薄いアダプタ層を新設し、内部は既存 Actions / StateManager に委譲する
> - (C) その他
>
> いずれにせよ「**StateManager をバイパスして状態を直接書かない**」を不変条件とする想定。

## Alternatives considered

TBD

## Consequences

TBD

## Revisit conditions

TBD

> 見直しトリガー候補: Companion/Stream Deck の要求仕様確定時、既存ルート棚卸し完了時、
> 認証要件が発生した時、マルチオペレーター運用を正式サポートする時。
