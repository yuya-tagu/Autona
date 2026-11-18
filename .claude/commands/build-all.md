---
description: Web UI と exe を一括ビルドし、結果を報告する
---

Web UI と exe を順番にビルドし、成果物をプロジェクト直下へ配置して、結果を報告してください。
コマンドの正本は CLAUDE.md の「ビルド」と [.claude/rules/build-and-release.md](../rules/build-and-release.md)。

## 手順

1. Web UI ビルド
   ```bash
   cd web && npm run build
   ```
   - エラー・警告を確認

2. exe ビルド（onedir。出力先は `dist/Autona/`。3〜5 分かかる）
   ```bash
   cd .. && python -m PyInstaller dependencies/autona.spec --noconfirm
   ```
   - `Build complete!` と `dist/Autona/Autona.exe`・`dist/Autona/_internal/` を確認
   - `--distpath .` は付けない（onedir では直下に `Autona/` フォルダができるだけで、直下の `Autona.exe` と `_internal/` は古いまま残る）

3. 成果物をプロジェクト直下へ配置
   ```bash
   tasklist //FI "IMAGENAME eq Autona.exe"          # 起動中か確認
   rm -rf ./_internal && cp -r dist/Autona/_internal ./_internal
   cp dist/Autona/Autona.exe ./Autona.exe
   ```
   - Autona.exe が起動中なら、終了してよいかユーザーに確認してから `taskkill //IM Autona.exe //F`（配信中の可能性がある）
   - `_internal/` が無いと起動しないので、exe と必ず対で差し替える

4. 結果を簡潔に報告:
   - Web UI: 成功/失敗（ファイルサイズ、新しい警告）
   - exe: 成功/失敗（直下へ配置済みか）
   - ビルド対象のコミットと、未コミット変更が含まれている場合はその旨
