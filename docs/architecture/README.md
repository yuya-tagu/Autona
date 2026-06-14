# docs/architecture

現在のアーキテクチャを**変更影響を判断できる粒度**で記録する。
import 関係や状態キーパスの網羅的な事実情報は `.claude/architecture/` が正本なので、
ここでは「外部制御 API / 連携を足すときに触れる/避けるべき構造」に焦点を当てる。

## 置くもの

- レイヤー構成・主要モジュールと責務・データ/状態の流れ・外部連携点
- 改変時に注意すべきアーキ制約（シングルトン初期化順、スレッド境界など）

## ファイル

| ファイル | 内容 |
|---------|------|
| [current-architecture.md](current-architecture.md) | レイヤー/モジュール/状態フロー/外部連携/改変注意点 |

## 正本・参照

- [../../.claude/design/Design.md](../../.claude/design/Design.md) — 実装仕様の正本
- [../../.claude/architecture/](../../.claude/architecture/) — import 関係・状態キーパス等の調査ログ
- [../../CLAUDE.md](../../CLAUDE.md) — アーキ概要
