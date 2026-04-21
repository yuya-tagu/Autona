---
description: PR の変更内容からテストチェックリストを生成する
---

現在の PR に対するテストチェックリストを以下の手順で生成してください。

1. `git diff main...HEAD` で全変更を把握する
2. 変更内容を分析し、影響範囲を特定する:
   - デスクトップアプリ (`src/*.py`)
   - Web UI (`web/src/`)
   - ビルド設定 (`dependencies/autona.spec`)
3. 以下のフォーマットでチェックリストを生成する:

```markdown
## テストチェックリスト

### ビルド
- [ ] Web UI ビルド: `cd web && npm run build`
- [ ] exe ビルド: `python -m PyInstaller dependencies/autona.spec --distpath . --workpath build -y`

### コア機能
- [ ] {変更した機能の動作確認}
- [ ] {変更した機能の動作確認}

### リグレッション
- [ ] 全タブが表示される（Scoreboard Manager, Stage, Commentary, Statistics 等）
- [ ] {変更に関連する既存機能が壊れていないこと}

### ログ確認
- [ ] `logs/tsh-error.log` にエラーが増えていない
```

ガイドライン:
- 重要な項目のみに絞る（コア機能 3-5 項目、リグレッション 1-3 項目）
- 具体的で実行可能な項目にする
- 壊れる可能性があるものにフォーカスする
