---
description: テスト + ビルド + 起動確認を一括で実行し、結果を報告する
---

以下を順番に実行し、結果を報告してください。

## 手順

1. **pytest 実行**
   ```bash
   python -m pytest tests/ -v
   ```
   - 全テスト PASSED を確認
   - 失敗がある場合は詳細を報告して**中断**

2. **Web UI ビルド**
   ```bash
   cd stage_strike_app && npm run build
   ```
   - エラー・警告を確認

3. **exe ビルド**
   ```bash
   cd .. && python -m PyInstaller dependencies/tsh.spec --distpath . --workpath build -y
   ```
   - `TSH.exe` が生成されたことを確認

4. **起動確認**（手動テスト必要な場合のみ）
   ```bash
   python main.py
   ```
   - ウィンドウが表示されることを確認
   - **5秒以内にクラッシュしないこと**を確認
   - 手動で閉じる

5. **結果報告**（以下のフォーマット）:
   ```
   ## verify 結果
   - pytest: ✓ {N} passed / ✗ {N} failed
   - Web UI build: ✓ / ✗
   - exe build: ✓ / ✗ ({TSH.exe サイズ})
   - 起動確認: ✓ / ✗ / スキップ
   ```

## 注意
- Step 1 (pytest) が失敗した場合、後続ステップは実行しない
- exe ビルドは時間がかかる（1-2分）。不要な場合は `--skip-exe` と伝えてもらう
- 起動確認はGUIが必要なため、CI環境ではスキップ
