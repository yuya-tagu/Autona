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
  `PyQt6-WebEngine` を使用。`dependencies/requirements.txt` に含まれ `pip install -r` で自動導入される。
  `Qt6WebEngineCore.dll` が約 196 MB あるため、`_internal/` 全体が 600 MB 超になるのは正常
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

### onedir でのアップデート挙動（重要）

onedir のアップデーター（`ExtractUpdate`）は **「まず直接上書き、ロックされた
ファイルだけリネーム退避」**方式:

- 大多数（`assets/`・`layout/`・`web/`・`user_data/` と `_internal/` の大半）は
  ロックされていないので**そのまま上書き**（退避ファイルを作らない）
- 実行中はランチャ `Autona.exe` と `_internal/` のロード済み DLL
  （python3xx.dll, Qt6*.dll 等）がロックされ、上書きは共有違反で失敗する。Windows は
  実行イメージを `FILE_SHARE_DELETE` でマップするため**リネームは可能**なので、失敗した
  ファイルだけ `*.autona_old` に退避 → 新ファイル書込、とする（`_stash_existing`）
- 退避物は root と `_internal/` 内だけに収まる → 次回起動時に `CleanupUpdateLeftovers()`
  が一括削除（`Autona.py` 冒頭で呼ぶ。frozen ビルドのみ動作）
- **既知の制約（mmap ロック）**: `_internal/PyQt6/Qt6/resources/` の WebEngine リソース
  （`v8_context_snapshot.bin`・`*.pak`・`icudtl.dat` 等）は実行中プロセスが mmap で排他
  保持し、**上書きもリネームも不可**で置換できない。**同一 Qt バージョン間の更新では中身が
  同一なので無害**（古いまま残っても問題なし）。WebEngine をフル起動中（サムネイル描画）に
  閉じるとロック対象は更に広がる

#### Qt 跨ぎ更新の安全策（2段構え・実装済み）

「Qt が変わると上記リソースが置換できず WebEngine がクラッシュする」リスクを、
人間の記憶ではなく**仕組みで**防ぐ:

1. **Qt をピン留め**（`requirements.txt` で `PyQt6` / `PyQt6-Qt6` /
   `PyQt6-WebEngine` / `PyQt6-WebEngine-Qt6` を `==6.11.0`）。Qt は**意図的にピンを
   上げた時だけ**変わる。通常リリースは自動的に「同一 Qt」＝安全
2. **Qt フィンガープリント gate**（`AutonaUpdater._OnNewVersion`）。
   `publish_release.ps1` が release notes に `Qt-Build: <qVersion>` を自動付与し、
   updater は**ダウンロード前に**それと実行中の `qVersion()` を比較。**異なれば自動更新を
   拒否し「手動で再インストールしてください」を表示**（fail-safe）。マーカーが無い古い
   リリースは同一 Qt 扱いで許可（後方互換）

→ Qt を上げたい時は **`requirements.txt` のピンを 4 つ揃えて上げて再ビルド**するだけ。
   その release は gate により自動更新されず、ユーザーは手動再配布で受け取る。
   恒久的な「シームレスな Qt 跨ぎ自動更新」が必要になった場合のみ、別プロセス更新
   ヘルパー（ランチャに `--apply-update` モードを追加し、メイン終了後に全展開）を検討する
   ＝現状は未実装でよい

- **検証状況**: ローカル E2E（実 `ExtractUpdate` を起動中の onedir install に対して実行）で
  退避が数十件に収まり残骸ゼロ・再起動成功・版数伝播を確認。GitHub publish 経由の実地 E2E
  （0.9.0 → v1.0.0 検出 → DL → 更新 → 再起動）も完走済み

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
