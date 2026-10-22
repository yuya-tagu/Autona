---
description: 静的検証（pytest 全量 / Web UI の build・test）を一括実行し、結果を報告する
---

以下を順番に実行し、結果を報告してください。CI（`.github/workflows/ci.yml`）の python / web ジョブ相当です。

## 手順

1. **pytest 全量**（3〜4 分かかる。タイムアウトは 10 分）
   ```bash
   python -m pytest -q -ra -p no:cacheprovider --ignore=tests/live
   ```

2. **Web UI**（`web/` に変更がある場合のみ。`git status --porcelain web/` と `git diff --name-only HEAD web/` の両方で判定。未追跡の新規ファイルも含める）
   ```bash
   cd web && npm run build
   npm test -- --watchAll=false
   ```

3. **結果報告**（以下のフォーマット）:
   ```
   ## verify 結果
   - pytest: {N} passed / {N} failed / {N} skipped
   - Web UI build: 合格 / 不合格 / 対象外
   - Web UI test: 合格 / 不合格 / 対象外
   - 失敗: テスト名・エラー出力の該当箇所と推定原因（なければ「全通過」の一行）
   ```

## 注意
- 失敗しても後続を実行し、全体像を出す。**修正はしない**（原因の推定までにとどめる）
- 失敗は再実行で流さない。そのファイルだけ 1 回再実行した結果も併記し、再現するか・環境依存かを書く
- 実機確認（`python main.py` の起動確認）はオーナーが行う。exe ビルドは明示指示があるときだけメインが実行する。この手順では起動もビルドもしない
