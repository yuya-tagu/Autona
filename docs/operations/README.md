# docs/operations

**実運用（本番配信）視点**の手順とリスクを記録する。コードの説明ではなく、
「現場で何をどの順に操作するか」「壊してはいけない操作は何か」を書く。

## 置くもの

- 起動順・配信前チェック・本番中の操作・障害対応・復旧手順
- スタッフ（非エンジニア）が触る前提で、シンプルさを保つべき操作

## ファイル

| ファイル | 内容 |
|---------|------|
| [runtime-operations.md](runtime-operations.md) | 起動〜本番〜復旧の運用手順と失敗モード |
| [legacy-manual-update.md](legacy-manual-update.md) | v1.0.7 / v1.1.0 世代の自動更新不能問題の告知文・手動更新手順・リリースノート定型文 |

## 関連

- [../../.claude/manual-test-checklist.md](../../.claude/manual-test-checklist.md) — 動作確認手順
- [../product/features.md](../product/features.md) — リグレッション禁止リスト
