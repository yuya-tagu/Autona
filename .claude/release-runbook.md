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
ただし **旧成果物の掃除が必須**: `Compress-Archive -Update` は既存 zip に追記更新するため、
古い zip が残っていると削除済みファイルが zip 内に残留する。staging ディレクトリ `Autona/` も同様。

```powershell
cd C:\Users\ayuyt\Work\30_Products\Autona
Remove-Item -Recurse -Force ./Autona -ErrorAction SilentlyContinue
Remove-Item -Force ./release-windows.zip -ErrorAction SilentlyContinue
.\scripts\gen_release.ps1
Remove-Item -Recurse -Force ./Autona -ErrorAction SilentlyContinue   # staging 掃除
"ZIP: {0:N1} MB" -f ((Get-Item release-windows.zip).Length / 1MB)    # 380 MB 前後が正常（WebEngine 同梱）
```

### 8. リリースノートを作成して publish

日本語ノートを使う場合は `publish_release.ps1` の `-Notes` 引数ではなく
**UTF-8 のノートファイル + `gh release create --notes-file`** を使う（引数渡しは文字化けリスク）。
ノート末尾に `Qt-Build: {qVersion}` マーカーを付ける（`python -c "from qtpy.QtCore import qVersion; print(qVersion())"` で取得。現行 updater は読まないが慣例として残置）。

ノート構成: `Autona v{X.Y.Z}` / 新機能（ユーザー向け日本語） / アップデート時の注意（OBS URL 差し替え等の破壊的変更） / **v1.0.7・v1.1.0 向け手動更新の定型文**（[docs/operations/legacy-manual-update.md](../docs/operations/legacy-manual-update.md) の「運用ルール」から転記。該当世代の問い合わせが絶えるまで毎回載せる） / `Qt-Build:` マーカー。

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
- 成功判定: 再起動後にバージョン表記が新版になり、`assets/update_log.txt` の末尾に `waiting for pid → robocopy ok → update complete` が記録されている
- `update_log.txt` にヘルパーのログ行が 1 行も増えていない場合、ヘルパー（apply_update.ps1）自体が起動していない（v1.1.0 で踏んだ DETACHED_PROCESS 事故のパターン）

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
| 旧ファイルが zip に残留 | `Compress-Archive -Update` の追記仕様 | zip / staging の事前削除（手順 7） |
| v1.0.7→v1.1.0 自動更新が無音で不発 | powershell.exe を `DETACHED_PROCESS` 付きで spawn するとエンジン起動前に exit 0 で即死（`CREATE_NO_WINDOW` は併用時無視） | AutonaUpdater の creationflags から DETACHED_PROCESS を除去 + 自動更新 E2E 確認（手順 9） |
