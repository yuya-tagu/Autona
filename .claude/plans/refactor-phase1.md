# リファクタリング フェーズ1: Python読解と安全網

## 目的
Python側コードベースを「読める状態」にし、後続の並列リファクタを安全に進めるための地図と安全網を作る。本フェーズではコードの挙動を変更しない。**調査とドキュメント作成のみ**。

## 原則
- コード変更は禁止(調査・ドキュメント作成のみ)
- 成果物はすべて `.claude/architecture/` 配下に Markdown で配置
- 不明点は推測で埋めず「要確認」マークを付けて残す
- grep / view ベースの機械的調査を優先。LLM の記憶ではなく実コードを根拠にする

## Step 一覧

| Step | 成果物 | 内容 |
|---|---|---|
| 1 | `.claude/architecture/python-imports.md` | src/ 全ファイルのimport関係 |
| 1 | `.claude/architecture/singleton-init-order.md` | シングルトン初期化順 |
| 2 | `.claude/architecture/state-keys.md` | StateManager キーパスの read/write マップ |
| 3 | `.claude/architecture/python-hotspots.md` | 巨大ファイル特定 |
| 4 | `.claude/known-smells.md` | リファクタ候補リスト(燃料) |
| 5 | pytest 導入 + Characterization Test | known-smells.md で対象指定された関数のみ |
| 6 | `.claude/commands/verify.md` | ビルド + 起動 + テストの一括検証コマンド |
| 7 | `.claude/rules/agent-pitfalls.md` | 調査で発見した踏みやすい地雷のルール化 |
| 8 | `.claude/manual-test-checklist.md` | 自動化困難な動作確認手順 |

## 対象範囲
- `src/**/*.py` のみ
- `stage_strike_app/` は本フェーズでは触らない
- `dependencies/`, `scripts/` も触らない

## 完了条件
全 Step 完了時点で、Tagu が `.claude/architecture/` 配下のドキュメントだけを読んで src/ の全体構造を把握でき、known-smells.md を燃料にしてフェーズ2の夜間並列リファクタを開始できる状態になっていること。
