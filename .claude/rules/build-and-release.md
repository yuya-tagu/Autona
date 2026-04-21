---
paths:
  - "dependencies/**"
  - "*.spec"
  - "scripts/**"
---

# ビルド・リリースルール

## PyInstaller

- spec ファイル: `dependencies/autona.spec`
- `--distpath .` で実行し、`Autona.exe` をプロジェクト直下に生成する
- `dist/` フォルダの exe は使わない
- アイコン: `assets/icons/icon.ico`（`icon.png` から PIL で生成）

## ビルド順序

1. Web UI (`cd web && npm run build`) を先にビルド
2. その後 exe をビルド（Web UI の build/ を同梱するため）

## リリース

- `scripts/gen_release.ps1` でリリース ZIP を作成
- `web/build/` が ZIP に含まれるため、最新の Web UI ビルドが必要
