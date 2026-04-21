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

## 翻訳コンパイル (.ts → .qm)

- ラッパー: `scripts\compile_translations.ps1`（引数なしで全言語、`-Lang ja` で単一言語）
- 実体: `dependencies/tools/lrelease.exe` + `Qt6Core.dll`（Qt 6 の Linguist ツール）
  - リポジトリに直接同梱している（約 11 MB）。PySide6 / PyQt6 のインストールに依存しない
  - PyInstaller ビルドには含まれない（spec の datas に含めていない）
- `.ts` を編集したら `.qm` を再コンパイルして commit する。ランタイムは `.qm` のみ読む

## リリース

- `scripts/gen_release.ps1` でリリース ZIP を作成
- `web/build/` が ZIP に含まれるため、最新の Web UI ビルドが必要
