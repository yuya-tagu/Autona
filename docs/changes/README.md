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
| [commentary-rework-impact-analysis.md](commentary-rework-impact-analysis.md) | 実況・解説機能リワーク（2026-07）の破壊的変更・移行方針（スキーマ刷新 / OBS URL 差し替え） |
| [startup-architecture-redesign.md](startup-architecture-redesign.md) | 起動アーキテクチャ再設計の設計書（終着点 4 段パイプライン / 移行 5 ステップ / 各ステップの検証チェックリスト） |
| [headless-core-redesign.md](headless-core-redesign.md) | Headless Core 再設計（書き込み経路の Actions 集約）の設計書（ADR-004 の実行計画 / 移行 6 ステップ / T-18・T-19 の具体化） |
| [layout-custom-unification.md](layout-custom-unification.md) | layout/custom（Designer 製）を他 layout と同じ概念に統合する設計書（共通ランタイム 1 本化 / LayoutCatalog + `/api/v1/layouts` / 移行 5 ステップ） |
