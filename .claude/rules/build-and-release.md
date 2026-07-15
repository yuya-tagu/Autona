---
paths:
  - "dependencies/**"
  - "*.spec"
  - "scripts/**"
---

# ビルド・リリースルール

## PyInstaller（onedir 形式）

- spec ファイル: `dependencies/autona.spec`
- **onedir ビルド**: `EXE(..., exclude_binaries=True)` + `COLLECT` で、`dist/Autona/`
  フォルダに小さなランチャ `Autona.exe`（約 20 MB）と `_internal/`（Python ランタイム
  + Qt/WebEngine バイナリ）を生成する
  - 旧 onefile（単一 250 MB exe）から移行済み。理由は起動高速化（onefile は起動毎に
    約 250 MB を `%TEMP%` に展開し ~8〜11 秒のオーバーヘッド。onedir で展開が消え
    起動 ~17.5 秒 → ~3.5 秒）
- ビルド後、`dist/Autona/Autona.exe` と `dist/Autona/_internal/` を**両方**プロジェクト
  直下にコピーする（`Autona.exe` 単体だけでは起動しない。`_internal/` が必須）
- `_internal/` は `.gitignore` 済み（ビルド成果物）
- UPX は使わない（`upx=False`。ビルドマシンに upx 未インストールで元々 no-op）
- アイコン: `assets/icons/icon.ico`（`icon.png` から PIL で生成）

### コピー手順例

```bash
taskkill /IM Autona.exe /F 2>/dev/null   # 起動中なら終了
rm -rf ./_internal && cp -r dist/Autona/_internal ./_internal
cp dist/Autona/Autona.exe ./Autona.exe
```

## ビルド順序

1. Web UI (`cd web && npm run build`) を先にビルド
2. その後 exe をビルド（Web UI の build/ を同梱するため）

## 同梱物のサイズ・フォント・上流成果物

- **WebEngine 依存**: サムネイル生成（[src/thumbnail/ThumbnailRenderer.py](../../src/thumbnail/ThumbnailRenderer.py)）が
  QtWebEngine（`PySide6-Addons` に含まれる）を使用。`dependencies/requirements.txt` に含まれ `pip install -r` で自動導入される。
  `Qt6WebEngineCore.dll` が約 196 MB あるため、`_internal/` 全体が 600 MB 超になるのは正常
- **Qt binding は PySide6 固定（LGPL）**: PyQt6 (GPL v3) はソース非公開配布と両立しないため使用禁止。
  環境に PyQt6 が同居していると qtpy/qasync の自動検出が PyQt を優先して混在事故になるため、必ずアンインストールする
- **フォント**: LINE Seed JP（`assets/font/LINESeedJP/`、Google Fonts gstatic CDN から取得）
- **上流成果物の扱い**: `TSH.exe` / `TSH/` は上流 TournamentStreamHelper のビルド成果物。Autona 側では
  使用しないため残さない

## 翻訳コンパイル (.ts → .qm)

- ラッパー: `scripts\compile_translations.ps1`（引数なしで全言語、`-Lang ja` で単一言語）
- 実体: `dependencies/tools/lrelease.exe` + `Qt6Core.dll`（Qt 6 の Linguist ツール）
  - リポジトリに直接同梱している（約 11 MB）。PySide6 / PyQt6 のインストールに依存しない
  - PyInstaller ビルドには含まれない（spec の datas に含めていない）
- `.ts` を編集したら `.qm` を再コンパイルして commit する。ランタイムは `.qm` のみ読む

## リリース

- **リリース実行時は [.claude/release-runbook.md](../release-runbook.md) を上から順になぞる**（本書は背景知識、Runbook が実行手順）
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
  するのは assets / layout / user_data / web/build / LICENSE / Autona.exe / **`_internal/`** のみ）
- **assets / layout / user_data は git-tracked ファイルのみ同梱**（`git ls-files` ベース）。
  実行時にダウンロード・生成されるデータ（大会ロゴ `layout/logo.png`、デザイナー成果物
  `layout/custom/`、`rulesets.json`、ログ、`*.tmp`、アップロード画像、ゲーム画像等）は
  untracked / gitignored のため自動的に除外される。デフォルトは初回起動時に再生成される
  （ロゴ: `Window.__init__` が `assets/icons/icon.png` からシード）

### アップデート挙動（外部ヘルパープロセス方式）

アップデートは **アプリ完全終了後に外部 PowerShell ヘルパーが展開・再起動する**方式
（[assets/apply_update.ps1](../../assets/apply_update.ps1)）。フロー:

1. アップデーターが `release-windows.zip` を `%TEMP%\autona_update\update.zip` に DL
2. `UpdateProcedure()`（[src/AutonaUpdater.py](../../src/AutonaUpdater.py)）が
   `assets/apply_update.ps1` を temp workdir にコピーし、通知ダイアログを出して
   ヘルパーを **detached**（`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP |
   CREATE_NO_WINDOW`）で起動 → `QApplication.exit()`
3. ヘルパーは **親 Autona プロセス（PID）の完全終了を待つ**（= 全ファイルのロック解放）。
   その後 zip を staging に展開（先頭 `Autona/` を strip・ZIP slip ガード）し、
   `robocopy /E`（**`/PURGE` なし**）で install ディレクトリへ反映。新 `Autona.exe` を
   起動して zip/staging/自分自身を後始末
4. 失敗時（親 PID 残存・展開例外・robocopy `exit >= 8`）は**旧 exe を再起動**し
   `assets/update_log.txt` にログを残す（ユーザーを「何も起動しない」状態にしない）

**ポイント:**
- アプリ終了後に動くため、ランチャ `Autona.exe`・ロード済み `_internal/*.dll`・
  mmap された WebEngine リソース（`v8_context_snapshot.bin`・`*.pak`・`icudtl.dat` 等）も
  **すべて置換可能**。旧来の in-place 上書き＋`.autona_old` リネーム退避は廃止
- `robocopy /E`（PURGE なし）なので、release zip に含まれないユーザー資産
  （`layout/game_images`・`user_data/` のアバター等）は**消えずに残る**。テンプレ
  layout は更新で上書きされる（期待動作）。旧来の `layout_backup_{time}` 退避は廃止
- `CleanupUpdateLeftovers()`（`Autona.py` 冒頭・frozen のみ）は temp workdir 残骸と、
  旧版から移行したユーザーの legacy `Autona_old.exe` / `*.autona_old` を掃除する
- **進行中アップデートの保護（2026-07-15 追加）**: ヘルパーは実行中
  `%TEMP%\autona_update\update_in_progress.lock` を排他モード（FileShare.None）で
  保持する。アプリはこのロックを検出すると (1) main.py の起動ゲートで
  「適用中」メッセージを出して起動を中止し、(2) `CleanupUpdateLeftovers()` は
  workdir 掃除をスキップする。ロック導入前は、インストール中に手動起動された
  アプリの起動時掃除が **robocopy 中の staging を削除して更新が失われる**事故が
  あった（v1.3.0 E2E、robocopy exit 11）。ヘルパー側も親 PID に加えて
  **同名プロセス全終了を待ち**、フォールバック再起動は既存インスタンスが
  いればスキップする（二重起動防止）。zip エントリ名は **`/` 区切り必須**
  （gen_release.ps1 が生成時に自己検証。`\` 区切りだとヘルパーが全エントリを
  スキップし無音で不発 = v1.3.0 で実際に発生）

#### Qt 跨ぎ更新も自動更新可

外部ヘルパーは WebEngine リソースを含む全ファイルを置換できるため、**Qt バージョンが
変わる更新も自動適用できる**（旧来の Qt フィンガープリント gate / 手動再インストール
誘導は撤廃済み）。`requirements.txt` の Qt ピンは従来どおり「意図的に上げた時だけ
変える」運用を維持してよいが、跨いでも自動更新は壊れない。`publish_release.ps1` の
`Qt-Build:` スタンプは updater が読まなくなった（残置で害なし）。

### ローカル公開（推奨・確実）

1. ローカルでビルド（Web UI → exe、`Autona.exe` + `_internal/` がプロジェクト直下にある状態）
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
