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

- `scripts/gen_release.ps1` でリリース ZIP（`release-windows.zip`）を作成
- `web/build/` が ZIP に含まれるため、最新の Web UI ビルドが必要

## 配布と自動アップデート

ソースリポ `yuya-tagu/Autona` は **private**。アプリ内アップデーター
（[src/AutonaUpdater.py](../../src/AutonaUpdater.py)）は **匿名** で GitHub
Releases API を叩くため、private リポの release は配布 exe から取得できない。
そこで **公開リリース専用リポジトリ `yuya-tagu/Autona-releases`（public）** に
release だけを publish し、アップデーターはそちらを参照する（`GITHUB_REPO`）。

- **release タグは `v{version}`**。`{version}` は `assets/versions.json` の
  `program`。アップデーターは `tag.lstrip("v")` を local version と比較する
- アップデートを「出す」には `versions.json` の `program` を上げてから publish する
  （同値なら「最新です」になる＝正常）
- 公開 ZIP に Python ソース（`src/*.py`）は含まれない（`gen_release.ps1` が同梱
  するのは assets / layout / user_data / web/build / LICENSE / Autona.exe のみ）

### ローカル公開（推奨・確実）

1. ローカルでビルド（Web UI → exe、`Autona.exe` がプロジェクト直下にある状態）
2. `scripts/gen_release.ps1` で `release-windows.zip` を作成
3. `gh` を `yuya-tagu` に切替（CLAUDE.md の複数アカウント手順参照）
4. `scripts/publish_release.ps1` を実行（`v{version}` タグで public リポへ publish）
5. `gh` を元アカウントに復元

### CI 公開（`.github/workflows/build_release.yml`）

- `workflow_dispatch` で起動。Web UI + exe をビルド → `gen_release.ps1` →
  public リポへ Release を publish する
- **要シークレット**: private リポの Actions secrets に `RELEASE_REPO_TOKEN`
  （`yuya-tagu/Autona-releases` への Contents:write 権限を持つ PAT）を登録
- **未検証**: CI 上での PyInstaller ビルドは未確認。`workflow_dispatch` で一度
  通し、生成された ZIP の動作を確認してから常用すること。確実なのはローカル公開
