---
description: Web UI と exe を一括ビルドし、結果を報告する
---

Web UI と exe を順番にビルドし、結果を報告してください。

## 手順

1. Web UI ビルド
   ```bash
   cd web && npm run build
   ```
   - エラー・警告を確認

2. exe ビルド
   ```bash
   cd .. && python -m PyInstaller dependencies/autona.spec --distpath . --workpath build -y
   ```
   - ビルド成功を確認
   - `Autona.exe` が生成されたことを確認

3. 結果を簡潔に報告:
   - Web UI: 成功/失敗（ファイルサイズ）
   - exe: 成功/失敗
