# リリース実行手順書（Runbook）

「新バージョンをリリースして」と依頼されたときに **上から順になぞる** 実行手順。
ビルド方式・アップデーター仕様などの背景知識は [rules/build-and-release.md](rules/build-and-release.md)、
Git 認証ダンスの詳細は [git-workflow.md](git-workflow.md) を参照。

## 大原則: 作業ディレクトリ（CWD）の取り扱い

**PowerShell / Bash ツールは呼び出し間で作業ディレクトリが持続する。**
過去のリリースで「`cd web` したまま次のステップを実行 → spec が見つからずビルド失敗」が発生した。

- **各ステップのコマンドは必ず `cd C:\Users\ayuyt\Work\30_Products\Autona` から始める**か、
  同一コマンド内で完結させる（例: `cd web; npm run build` は 1 呼び出しに閉じ、次の呼び出しは再度ルートから）
- ステップをまたいで CWD を仮定しない。「前のコマンドでルートにいたはず」を信用しない
- PowerShell 5.1 でネイティブ exe に `2>&1` を付けない（NativeCommandError で偽の失敗になる。stderr は付けなくても取得される）

## 前提条件チェック

- [ ] リリース対象の PR がすべてリモート `main` にマージ済み
- [ ] ローカル `main` が `origin/main` と一致（クリーンな作業ツリー）
- [ ] リリースに含まれる機能の実機確認が完了している（PdM 判断）

## 手順

### 1. ローカル main を最新化

```bash
# CWD: プロジェクトルート
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" fetch origin --prune
gh auth switch --user yuyat-highfield
git merge --ff-only origin/main
git status --short   # クリーンであること
```

### 2. バージョン番号を決めて更新

- 番号は **PdM に確認**する（機能追加ならマイナー、小修正ならパッチが目安）
- `assets/versions.json` の `"program"` を更新（アップデーターはこの値と release タグ `v{version}` を比較する）

```bash
# CWD: プロジェクトルート
git add assets/versions.json
git commit -m "chore(release): バージョンを {X.Y.Z} に更新 ..."   # 日本語・Co-Authored-By 付き
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" push origin main:chore/release-v{X.Y.Z}
gh auth switch --user yuyat-highfield
```

- PR 作成はユーザーが手動（URL・タイトル案・本文案を提示する）。**PR マージを待たずに手順 3 以降へ進んでよい**
  （ビルドはローカル main のバージョン更新コミットから行うため）

### 3. Web UI をビルド（exe より先。ビルド成果物が exe に同梱される）

```powershell
# 1 呼び出しに閉じる（次の呼び出しは CWD が web/ に残っている前提で書かない）
cd C:\Users\ayuyt\Work\30_Products\Autona\web; npm run build
```

### 4. exe をビルド（3〜5 分）

```powershell
cd C:\Users\ayuyt\Work\30_Products\Autona; pyinstaller dependencies/autona.spec --noconfirm
# 最後に "Build complete!" と exit 0 を確認
```

### 5. 成果物をプロジェクト直下へ配置

```powershell
cd C:\Users\ayuyt\Work\30_Products\Autona
try { Stop-Process -Name Autona -Force -ErrorAction Stop } catch {}   # 起動中なら終了（_internal がロックされるため）
Remove-Item -Recurse -Force ./_internal -ErrorAction SilentlyContinue
Copy-Item -Recurse dist/Autona/_internal ./_internal
Copy-Item -Force dist/Autona/Autona.exe ./Autona.exe
```

### 6. 同梱バージョンの検証（必須）

versions.json は exe（`_internal/assets/`）に埋め込まれる。**バージョン更新前のビルドを配布する事故**を防ぐ:

```powershell
Get-Content _internal/assets/versions.json -Raw   # "program" が新バージョンであること
```

違っていたら手順 2 のコミットが済んでいるか確認して手順 4 からやり直す。

### 7. release-windows.zip を生成

**事前チェック**: 法務ドキュメント一式（`LICENSE` / `TERMS.md` / `THIRD-PARTY-NOTICES.md` /
`licenses/`）が最新か確認する。特に依存を追加・更新した場合は
`licenses/python-packages.txt`（pip-licenses で生成）と `licenses/npm-packages.txt`
（license-checker で生成）の再生成が必要。**GPL ライセンスの依存が新たに入っていないこと**
（ソース非公開配布と非両立。ATTRIBUTIONS.md 参照）。

`gen_release.ps1` は `$PSScriptRoot` 基準で CWD 非依存（ルートから `.\scripts\gen_release.ps1` で実行可）。
旧 staging `Autona/` と旧 zip はスクリプト冒頭で自動削除される（`Compress-Archive -Update` の
残留事故はスクリプト側で根絶済み）。zip は .NET ZipArchive で **エントリ名を `/` 区切り**で
生成し、末尾で `scripts/validate_release_zip.ps1` により自己検証する
（`OK: N entries, all '/' separators, runtime trees contain tracked files only` が出ること）。
検証内容は ① 区切り文字 ② **assets/layout/user_data に git 管理外ファイルが混入していないこと**
（テスト大会アイコン等の開発時データ遮断。v1.3.0 差し替えアセットで実際に混入）。
**`Compress-Archive` に戻してはならない**: PS 5.1 では `\` 区切りエントリになり、インストール済み
ヘルパー apply_update.ps1 が 1 ファイルも展開できず自動更新が無音で不発になる（v1.3.0 で実際に発生）。

```powershell
cd C:\Users\ayuyt\Work\30_Products\Autona
.\scripts\gen_release.ps1
Remove-Item -Recurse -Force ./Autona -ErrorAction SilentlyContinue   # staging 掃除
"ZIP: {0:N1} MB" -f ((Get-Item release-windows.zip).Length / 1MB)    # 310〜330 MB 前後が正常（WebEngine 同梱。大きく外れたら同梱物を確認）
```

### 8. リリースノートを作成して publish

日本語ノートを使う場合は `publish_release.ps1` の `-Notes` 引数ではなく
**UTF-8 のノートファイル + `gh release create --notes-file`** を使う（引数渡しは文字化けリスク）。
ノート末尾に `Qt-Build: {qVersion}` マーカーを付ける（`python -c "from qtpy.QtCore import qVersion; print(qVersion())"` で取得。現行 updater は読まないが慣例として残置）。

ノート構成: `Autona v{X.Y.Z}` / 新機能（ユーザー向け日本語） / アップデート時の注意（OBS URL 差し替え等の破壊的変更） / **v1.0.7・v1.1.0 向け手動更新の定型文**（[docs/operations/legacy-manual-update.md](../docs/operations/legacy-manual-update.md) の「運用ルール」から転記。該当世代の問い合わせが絶えるまで毎回載せる） / `Qt-Build:` マーカー。

**publish 直前に zip を必ず再検証する**（手動 `gh release create` は gen_release の自己検証を
経由しないため。過去に「修復した旧 zip」を publish して開発時データ＝テスト大会アイコンを
配布した事故がある。`publish_release.ps1` 経由ならこの検証は自動で走る）:

```powershell
cd C:\Users\ayuyt\Work\30_Products\Autona
.\scripts\validate_release_zip.ps1   # OK 行が出ること。throw したらその zip は publish 禁止
```

```bash
# CWD: プロジェクトルート。notes ファイルはスクラッチパッドに UTF-8 で作成しておく
gh auth switch --user yuya-tagu
gh release create v{X.Y.Z} release-windows.zip --repo yuya-tagu/Autona-releases --title v{X.Y.Z} --notes-file "{notes のフルパス}"
gh auth switch --user yuyat-highfield   # 復元を忘れない
```

- publish 先は **公開リポ `yuya-tagu/Autona-releases`**（ソースリポは private のため updater が読めない）
- 同名タグが既にあると失敗する（`gh release create` のエラーに従う）

### 9. 自動更新の E2E 確認（必須）

publish 直後に、**旧バージョンの exe からアプリ内アップデータで新リリースへ実際に更新できる**ことを確認する（実機確認はユーザーが行う）。

- 旧バージョン一式（`Autona.exe` + `_internal/` ほか）を退避コピーしておき、そこから起動 → 更新ダイアログ → 「ダウンロードしてインストール」を実行
- **アプリ終了後、自動で再起動されるまでアプリを手動起動しない**（展開＋robocopy に
  数分かかる。特に OneDrive 配下のインストール先は同期フックで大幅に遅い）。
  v1.2.x 以前の世代には進行中ロック保護が無く、途中起動すると起動時掃除が
  staging を削除して更新が失われる（v1.3.0 E2E で発生。v1.3.1+ は main.py の
  起動ゲートが「適用中」メッセージを出して保護する）
- 成功判定: **再起動後にバージョン表記が新版になっていること**（これが最終判定。ログの
  `update complete` だけでは不十分 — v1.3.0 では zip エントリ名が `\` 区切りだったため
  展開 0 件のまま robocopy が exit 0 で「成功」し、`update complete` が記録されつつ
  旧バージョンが再起動された）。加えて `assets/update_log.txt` の末尾に
  `waiting for pid → robocopy ok → update complete` が記録されていること
- `update_log.txt` にヘルパーのログ行が 1 行も増えていない場合、ヘルパー（apply_update.ps1）自体が起動していない（v1.1.0 で踏んだ DETACHED_PROCESS 事故のパターン）
- **差分ダウンロードの確認（v1.3.3 実装。効き始めは v1.3.3→v1.3.4 以降**、DL ロジックは
  インストール済み旧版側で走るため）: 差分経路が使われた場合、アプリログに
  `diff download ok: N changed + M reused entries, X of Y MB fetched`、
  `update_log.txt` に `prebuilt staging detected (diff download)` が出る。
  `diff download unavailable/failed ... falling back` が出ても**更新自体は全量DLで
  成功するのが正常**（差分は最適化。失敗モードを増やさないことが受け入れ条件）
- `staging has no Autona.exe after extraction` が記録されている場合、zip のレイアウト異常
  （エントリ名の区切り文字・ルートフォルダ名）。手順 7 の自己検証をすり抜けていないか確認する

背景: 更新時に動くアップデータは**インストール済みの旧バージョン側**のコード。この確認を怠ると、壊れたアップデータを持つ世代が積み重なり、全ユーザーに手動更新を強いることになる（v1.0.7→v1.1.0 で実際に発生）。

### 10. 後始末（ユーザー依頼・確認のうえ実施）

- [ ] `chore/release-v{X.Y.Z}` の PR をユーザーがマージ
- [ ] マージ後にローカル main を再同期（手順 1 と同じ）
- [ ] マージ済みリモートブランチを削除（[git-workflow.md](git-workflow.md) のブランチ削除フロー。**破壊的操作のためユーザーの明示依頼を得る**）
- [ ] リリース URL をユーザーに報告: `https://github.com/yuya-tagu/Autona-releases/releases/tag/v{X.Y.Z}`

## 過去に踏んだ地雷（再発防止）

| 事故 | 原因 | 対策（本書への反映箇所） |
|------|------|------------------------|
| exe ビルドが「spec not found」で失敗 | 手順 3 の `cd web` が次の呼び出しに持続 | 大原則 + 各手順の `cd` 徹底 |
| versions.json が消えた | 旧 gen_release.ps1 をルートから実行し `..` が親ディレクトリに解決 | スクリプト側で修正済み（`$PSScriptRoot` 基準）。手順 7 に経緯を記載 |
| ネイティブコマンドが偽エラー | PS 5.1 の `2>&1` が stderr を ErrorRecord 化 | 大原則に記載 |
| push が `Repository not found` | アクティブ gh アカウントが `yuyat-highfield` | 全 push/fetch/publish でアカウントダンス（手順 1/2/8） |
| 日本語リリースノートの文字化け | `-Notes` 引数渡し | `--notes-file`（UTF-8）に統一（手順 8） |
| 旧ファイルが zip に残留 | `Compress-Archive -Update` の追記仕様 | gen_release.ps1 冒頭で zip / staging を自動削除（手順 7） |
| v1.0.7→v1.1.0 自動更新が無音で不発 | powershell.exe を `DETACHED_PROCESS` 付きで spawn するとエンジン起動前に exit 0 で即死（`CREATE_NO_WINDOW` は併用時無視） | AutonaUpdater の creationflags から DETACHED_PROCESS を除去 + 自動更新 E2E 確認（手順 9） |
| v1.2.x→v1.3.0 自動更新が無音で不発（「update complete」ログ付きで旧版のまま） | PS 5.1 の `Compress-Archive` が zip エントリ名を `\` 区切りで書くため、`/` 前提のヘルパーが全エントリをスキップ → 空 staging を robocopy が exit 0 で「成功」 | gen_release.ps1 を .NET ZipArchive + `/` 区切り明示 + 自己検証に変更。apply_update.ps1 に区切り正規化と「staging に exe 不在なら fallback」ガードを追加（手順 7・9） |
| 配布物にテスト大会アイコン等の開発時データが残存 | v1.3.0 の区切り文字事故対応で「旧 zip を修復して差し替え」たため、生成時の git 管理ファイル限定ガード（c80fc937）を迂回。旧 zip には一括コピー時代の `layout/logo.png`（テスト大会アイコン）・`layout/custom/`・`assets/rulesets.json` が同梱されていた | `scripts/validate_release_zip.ps1` を新設し、生成時（gen_release）・publish 時（publish_release / 手動 gh の手順 8）の両方で「assets/layout/user_data は git 管理ファイルのみ」を機械検証。**zip は必ず再生成する。修復・手詰めした zip を publish しない** |
| gen_release.ps1 が新規 PowerShell セッションで型エラー | `[ZipArchiveMode]` / `[CompressionLevel]` リテラルの解決に必要な `System.IO.Compression` アセンブリを Add-Type しておらず、PS 5.1 は遷移ロードしない | gen_release.ps1 で両アセンブリを明示 Add-Type（2026-07-16 修正） |
| v1.4.1→v1.5.0 自動更新後に起動不能（`AttributeError: module 'numpy' has no attribute 'array'`。フル zip 展開は正常） | v1.5.0 で PyInstaller excludes に numpy 等を追加したが、ヘルパーの robocopy `/E`（パージなし）が旧 `_internal` のバイナリ残骸ディレクトリを残す → Python が namespace package として import 成功 → deepdiff の `except ImportError` フォールバックが不発 | apply_update.ps1 の robocopy を 2 段化: `_internal` は `/MIR`（リリース 100% 所有・ユーザー資産は install ルート直下のみが前提）、残りは従来どおり `/E` パージなし。`tests/test_apply_update_robocopy.py` が構成を pin（2026-07-30・PR #164）。**教訓: excludes へパッケージを追加したら、必ず「旧版からの自動更新」E2E で確認する（フル zip 起動確認では検出できない）** |
