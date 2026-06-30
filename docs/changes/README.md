# docs/changes

**変更を入れる前**の影響分析・移行方針・互換性・リリース注意点を記録する。
「やる/やらない」の意思決定は `decisions/`（ADR）、実装手順は `tasks/` に分離する。
ここは「この変更を入れると何にどう響くか」を整理する場所。

## 置くもの

- 変更提案ごとの影響範囲（既存ワークフロー / モジュール / 運用）
- 互換性要件・移行方針・ロールバック方針・リリース時の注意点
- 未決事項（Open questions）と次アクション

## ファイルの単位

原則 **1 変更提案 1 ファイル**（`<change-name>-impact-analysis.md` 等）。
大きな変更は ADR（decisions/）と調査タスク（tasks/）に分岐させる。

## ファイル

| ファイル | 内容 |
|---------|------|
| [external-control-impact-analysis.md](external-control-impact-analysis.md) | 外部制御 API / Companion / Stream Deck 連携の影響分析（実装済み API の運用リスク含む） |
