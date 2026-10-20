# docs/tasks

調査タスク・実装計画・実装チケットを置く。調査・設計した結果を、ここに
**自己完結したチケット**として落とし込み、担当者がそのまま着手できる状態にする。
タスクの種類（調査 / 実装）に応じて担当・実施環境を割り当てる。

## タスクの種類

| 種類 | 目的 | 完了条件の例 |
|------|------|------------|
| 調査タスク（investigate-*） | 実装前に事実を確定する | 不明点が埋まり、設計判断の材料が揃う |
| 実装チケット（implement-*） | コードを変更する | 受け入れ条件を満たし検証が通る |

> 外部制御 API 関連の調査・実装チケットは完了済みの履歴として残す。
> 新規作業として再着手する場合は、実装済みの [../../src/AutonaApiV1.py](../../src/AutonaApiV1.py) と
> [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md) を正にして差分チケットを起こす。

## チケットの書き方（自己完結が原則）

実装者が**この 1 ファイルだけで着手できる**よう、以下を必ず含める:

- Goal / 前提 / 先に読むべきドキュメント
- 調査・変更対象（ファイルパス付き）
- 答えるべき問い or 受け入れ条件
- 成果物 / Out of scope / 次のタスク

## 開発運用との対応

- このリポジトリの実装ルールは [../../CLAUDE.md](../../CLAUDE.md) と
  [../../.claude/rules/](../../.claude/rules/) に従う（worktree 運用・検証・地雷回避）
- 設計正本は
  [../../.claude/design/Design.md](../../.claude/design/Design.md)

## ファイル

| ファイル | 種類 | 内容 |
|---------|------|------|
| [investigate-external-control-api.md](investigate-external-control-api.md) | 調査 | 外部制御 API 設計前の事実確定（調査結果・スパイク済） |
| [implement-external-control-api-readonly.md](implement-external-control-api-readonly.md) | 実装（完了済み履歴） | `/api/v1/` 共通基盤 + 読み取りエンドポイント |
| [implement-external-control-api-safe-ops.md](implement-external-control-api-safe-ops.md) | 実装（完了済み履歴） | 安全操作（score±1 / swap / reset / set 情報・confirm 不要） |
| [implement-external-control-api-guarded-ops.md](implement-external-control-api-guarded-ops.md) | 実装（完了済み履歴） | 危険操作（confirm 必須 + 重い操作 202 受付） |

> 外部制御 API は **readonly → safe-ops → guarded-ops** の順で実装済み。仕様正本は
> [../../.claude/design/external-control-api.md](../../.claude/design/external-control-api.md)、決定は
> [../decisions/ADR-001-external-control-api-boundary.md](../decisions/ADR-001-external-control-api-boundary.md)。
