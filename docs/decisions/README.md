# docs/decisions

設計・運用上の**意思決定ログ（ADR: Architecture Decision Record）**。
「なぜその設計/制約にしたか」「採用しなかった案」「将来見直す条件」を残す。

## ADR の書き方

1 決定 1 ファイル。ファイル名は `ADR-NNN-<短い英語スラッグ>.md`。

```md
# ADR-NNN: タイトル

## Status
Proposed | Accepted | Superseded by ADR-XXX | Deprecated

## Context
背景・課題・制約

## Decision
何を決めたか

## Alternatives considered
検討して採用しなかった案と却下理由

## Consequences
良い影響・悪い影響・トレードオフ

## Revisit conditions
この決定を見直すべき条件・トリガー
```

## ステータス遷移

`Proposed` → 合意で `Accepted` → 後続 ADR が覆したら `Superseded by ADR-XXX`。
既存決定を変える場合は、元 ADR を直さず**新しい ADR を起こして supersede** する。

## ファイル

| ファイル | Status | 内容 |
|---------|--------|------|
| [ADR-001-external-control-api-boundary.md](ADR-001-external-control-api-boundary.md) | Proposed | 外部制御 API の責務境界 |
