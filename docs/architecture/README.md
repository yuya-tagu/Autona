# docs/architecture

> **性格: current 追従の「変更影響マップ」（生きたドキュメント）。**
> 事実が変わったら直す。網羅的な生事実は持たず、正本へリンクする。

現在のアーキテクチャを**変更影響を判断できる粒度**で記録する。
状態キーパスの正本は `src/core/StateSchema.py`、シングルトン生成順の正本は `src/bootstrap.py` なので、
ここでは「外部制御 API / 連携を足すときに触れる/避けるべき構造」に焦点を当てる。
**アーキの事実そのものは再記述せず正本へリンクする**（二重管理を避ける）。

## 置くもの

- レイヤー構成・主要モジュールと責務・データ/状態の流れ・外部連携点
- 改変時に注意すべきアーキ制約（シングルトン初期化順、スレッド境界など）

## ファイル

| ファイル | 内容 |
|---------|------|
| [current-architecture.md](current-architecture.md) | レイヤー/モジュール/状態フロー/外部連携/改変注意点 |

## 正本・参照

- [../../.claude/design/Design.md](../../.claude/design/Design.md) — 実装仕様の正本
- [../../src/core/StateSchema.py](../../src/core/StateSchema.py) — 状態キーパスの正本
- [../../CLAUDE.md](../../CLAUDE.md) — アーキ概要
