---
paths:
  - "packaging/**"
  - "requirements.txt"
  - "*.spec"
  - "scripts/**"
---

# ビルド・リリースルール

## PyInstaller（onedir 形式）

- spec ファイル: `packaging/autona.spec`
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
  QtWebEngine（`PySide6-Addons` に含まれる）を使用。`requirements.txt` に含まれ `pip install -r` で自動導入される。
  `_internal/` 全体は約 344 MiB（ADR-005 S-final SF-9 で未使用の Qt の同梱物を外した後。2026-11-16 のビルドの実測 344.4 MiB。
  SF-9 より前は 571 MiB）。うち `Qt6WebEngineCore.dll` が約 196 MB
- **Qt の同梱物の削減（ADR-005 S-final SF-9）**: PyInstaller の Qt のフックが集めるがアプリが開かないものを
  `packaging/autona.spec` が外す。外し方は 2 つ:
  - excludes（Python モジュール）: `PySide6.QtQuick` / `QtQml` / `QtQuickWidgets` / `QtPositioning` / `QtUiTools` /
    `QtDataVisualization` / `QtOpenGL` / `QtOpenGLWidgets`。WebEngine のリンク依存から QtQuick が hidden import になり、
    QtQml のフックが `qml/` 一式と QML プラグインとその DLL（Quick3D・3D・Graphs ほか）を集めていた。qtpy は QtOpenGL など
    3 つを try で試すだけ。**C++ の DLL（Qt6Quick・Qt6Qml・Qt6QmlMeta・Qt6QmlModels・Qt6QuickWidgets・Qt6OpenGL・
    Qt6WebChannel・Qt6Positioning・Qt6PrintSupport・Qt6Network）は外れない**（バイナリ依存解析が Qt6WebEngineCore.dll /
    Qt6WebEngineWidgets.dll の import table から集める）
  - `Analysis` の後の `a.datas` / `a.binaries` のフィルタ（宛先パス）: debug 版の WebEngine リソース（`*.debug.pak` /
    `v8_context_snapshot.debug.bin`）、Qt 自身の翻訳（`translations/*.qm`。アプリは `src/i18n/Autona_*.qm` だけを読む）、
    en-US / ja 以外の `qtwebengine_locales`（他の言語は en-US.pak にフォールバック）、Git for Windows の mingw64 から PATH 経由で
    混入する `libssl-3-x64.dll` / `libcrypto-3-x64.dll` と Qt の OpenSSL の TLS バックエンド `qopensslbackend.dll`（この 3 つは
    ファイル名で、どのフォルダにあっても外す。アプリの QtNetwork は `QLocalSocket` / `QLocalServer` だけで TLS を使わない。
    Python の `ssl` は `libssl-3.dll` / `libcrypto-3.dll` で、こちらは残す）
  - 外さないもの: `qtwebengine_devtools_resources.pak`・`Qt6Pdf.dll`・`opengl32sw.dll`・小さなプラグイン類（SF-9 で外した同梱物の削減の根拠。ADR-005 の「結果」節）
  - Qt モジュールを新しく import するとき: `tests/test_release_bundle.py` の `APP_QT_MODULES`（アプリが import する Qt モジュールの
    許可リスト。AST で pin）に足す前に、spec の excludes に入っていないこと・`validate_release_zip.ps1` の `$qtRequired` に要るものを足す
  - pin: `tests/test_release_bundle.py`（許可リスト・excludes を塞いだ状態での src / qtpy / qdarktheme の import・spec のフィルタと
    validator の禁止パターン / 必須リストの突き合わせ・validator が excludes のモジュールの `.pyd` そのものを禁止すること・外す OpenSSL が
    Python のものでないこと・検査 5 を合成の zip で実際に走らせること（Windows のみ））。ゲートは `validate_release_zip.ps1` の検査 5
- **Qt binding は PySide6 固定（LGPL）**: PyQt6 (GPL v3) は MIT 配布と両立しないため使用禁止。
  環境に PyQt6 が同居していると qtpy の自動検出が PyQt を優先して混在事故になるため、必ずアンインストールする
- **フォント**: LINE Seed JP（`assets/font/LINESeedJP/`、Google Fonts gstatic CDN から取得）
- **上流成果物の扱い**: `TSH.exe` / `TSH/` は上流 TournamentStreamHelper のビルド成果物。Autona 側では
  使用しないため残さない

## 翻訳コンパイル (.ts → .qm)

- ラッパー: `scripts\compile_translations.ps1`（引数なしで全言語、`-Lang ja` で単一言語）
- 実体: PySide6 パッケージ同梱の `lrelease.exe`（`python` から PySide6 の場所を引く。リポジトリには置かない）
  - PyInstaller ビルドには含まれない（spec の datas に含めていない）
- `.ts` の更新: `scripts/update_translations.ps1`（同梱の `lupdate.exe -extensions py -locations none -no-obsolete main.py src`）
- `.ts` を更新したら `.qm` を再コンパイルして commit する。ランタイムは `.qm` のみ読む
- 新しい文言の訳は `update_translations.ps1` で `.ts` に取り込んだ後に手で埋める
- **`.ts` に置くのは Qt に残った UI の文言だけ**（ADR-005 S-final SF-7）: ネイティブのダイアログ（LAN の確認・ポートの競合・Web サーバーの起動失敗・フォルダの選択の題・共有ビューの再試行 / 失敗の表示）と Updater のダイアログ。文脈は `app` / `portguard` / `updater` / `webpanel` の 4 つ
  - 画面の文言は React のページの i18next（`web/src/i18n/locales/{en,ja}.json`）。core が返す文言（サムネイルのテンプレート名・リザルトのレイアウト名・プレビューの見本の大会名など）は `src/core/CoreWords.py` の ja / en の表で、言語は `program_language` に従う（`AutonaLocaleHelper.programLocale`。Qt の translate は注入しない）
  - 文言を足すとき: コードで `QCoreApplication.translate("<文脈>", "<原文>")` を**定数の引数**で呼び、`update_translations.ps1` で en / ja の両方の `.ts` に取り込み、訳を埋めて `.qm` を再コンパイルする。文言を消すと `.ts` からも消える
  - pin: `tests/test_web_panel_host.py` の `test_the_ts_files_hold_the_native_dialogs_words_alone`（`.ts` の中身 = src と `main.py` が引く Qt の訳の (文脈, 原文)、どれも訳が空でない、`<location>` を持たない。文脈ごとの呼び手のモジュールは `TS_CONTEXTS`）と `test_the_qm_files_are_compiled_from_the_ts_files`（`.qm` の再コンパイル漏れ。SF-7 で消した 66 件 `SF7_REMOVED_TS_MESSAGES` が訳されないことも）。呼び出しの検出は `tests/test_core_words.py` の `translationCalls`: 受け手が `QCoreApplication` / `QGuiApplication` / `QApplication` の `translate`・それを束ねた名前（`translate = QCoreApplication.translate`）・`QT_TRANSLATE_NOOP`・`self.tr`（文脈はクラス名）。文脈と原文は定数の文字列に限る（動的な文脈・translate を値として渡す注入は落ちる）。`<location>` は手で保守するとずれるので書かない

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
- **`scripts/validate_release_zip.ps1` が最終ゲート**: zip の区切り文字と
  「assets/layout/user_data = git 管理ファイルのみ」、保存済み state が入っていないこと、Qt の同梱物（検査 5。
  WebEngine とそのリンク先の Qt DLL などの必須ファイルがあること・spec が外したものが無いこと・`_internal` の合計サイズの上限
  `$internalMaxMiB`）、アーカイブ切り抜きのワーカー（`vodsplit-worker/`）が入っていないこと（検査 6。ADR-006 A7）を検証する。gen_release / publish_release の
  双方から自動で呼ばれ、手動 `gh release create` の前にも必ず単体実行する（Runbook 手順 8）。
  修復・手詰めした zip の publish は禁止（v1.3.0 差し替えアセットがこの経路でテスト大会
  アイコン等の開発時データを配布した）

### アーカイブ切り抜きのワーカー（別配布。ADR-006 A7）

- ワーカー `vodsplit-worker`（SmashVodSplitter の PyInstaller onedir。FFmpeg（LGPL のビルド。imageio-ffmpeg の GPL のビルドは入れない）・deno・yt-dlp を含む）は **Autona の zip にも
  spec にも入れない**。Autona-releases の `worker-v{X.Y.Z}` のリリース（**`--latest=false`**。アップデーターは `releases/latest` を読む）に
  zip とマニフェストを置き、利用者がアーカイブのページで導入すると core の `ArchiveWorkerInstaller` が Autona のフォルダの
  `vodsplit-worker/` に入れる。手順は Runbook の「ワーカーのリリース」
- **Autona の更新はワーカーに触れない**: `apply_update.ps1` の 2 段の robocopy のうち、`/MIR` は `_internal` だけで、ルートは `/E`（削除なし）。
  `CleanupUpdateLeftovers()` もワーカーのフォルダ（と導入の途中の `.part` / `.new` / `.old`）を消さない。
  `tests/test_archive_worker_install.py` が固定（ルートの段に `/MIR` / `/PURGE` / `/MOV` が無いこと・`Remove-Item` の対象・掃除の後もワーカーが残ること）。
  ルートの段を削除ありに変えるときは、ワーカーのフォルダを外す
- Autona の zip にワーカーを入れると、更新のたびに導入済みのワーカーを古いもので上書きする。`validate_release_zip.ps1` の検査 6 が止める
- NOTICES: Autona の `THIRD-PARTY-NOTICES.md` §9 はワーカーが別配布であることと構成物を書くだけで、ワーカーの表示とライセンス全文は
  ワーカーのフォルダの `THIRD-PARTY-NOTICES.md` / `licenses/`（SmashVodSplitter の `packaging/`）。FFmpeg のソースの tarball は
  ワーカーのリリースの 3 つ目のアセット（`build_worker.py` が `dist/` に置く）
- ワーカーのビルド（`build_worker.py`）は、ビルドした exe の自己診断（`version --check`。同梱の yt-dlp・OCR・OpenCV・FFmpeg・deno が動くか）が
  通らなければ zip を作らない。Autona の導入も入れ替えの前に同じ自己診断を通す（ワーカーの `features` に `check` があるとき）

### アップデート挙動（外部ヘルパープロセス方式）

アップデートは **アプリ完全終了後に外部 PowerShell ヘルパーが展開・再起動する**方式
（[assets/apply_update.ps1](../../assets/apply_update.ps1)）。フロー:

1. アップデーターがまず**差分ダウンロード**を試みる（2026-07-16 追加、v1.3.3 同梱予定）:
   zip の central directory（約1MB）を HTTP Range で取得し、各エントリの CRC32 を
   ローカル install と比較 → 変更分のみ Range 取得（実測: リリース間で全体の約6%、
   約20MB）→ staging を**アプリ側で事前構築**（未変更分は `copy2` でローカルから。
   mtime 保持により robocopy がスキップ）→ `staging_prebuilt.marker` を workdir に
   書く。コアは [src/Helpers/AutonaRemoteZip.py](../../src/Helpers/AutonaRemoteZip.py)
   （Qt 非依存・単体テスト可）。**あらゆる失敗は全量DLへフォールバック**
   （206 検証・ETag 固定・伸長後 CRC 検証・zip64/変更過半/TEMP 容量不足も同様）。
   構築中は `download_in_progress.lock` を保持し `CleanupUpdateLeftovers()` の
   workdir 掃除を抑止（main.py の起動ゲートはこのロックを見ない＝DL中の起動は正常）。
   マーカー名は Python/ps1 両側の契約（`tests/test_diff_update_contract.py` が pin）
1'. フォールバック時は従来どおり `release-windows.zip` 全量を
   `%TEMP%\autona_update\update.zip` に DL
2. `UpdateProcedure()`（[src/AutonaUpdater.py](../../src/AutonaUpdater.py)）が
   **ダウンロードした zip の中の** `assets/apply_update.ps1` を temp workdir に抽出し
   （2026-07-16 変更。インストール済みコピーは定義上1リリース古く、ヘルパー修正が
   「その修正を届ける更新」自身に効かない鶏と卵があった。zip に無い場合のみ
   インストール済みコピーへフォールバック）、通知ダイアログを出して
   ヘルパーを `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` で起動 →
   `QApplication.exit()`。**`DETACHED_PROCESS` は付けない**（付けると powershell.exe が
   スクリプト未実行のまま exit 0。v1.0.7→v1.1.0 の自動更新が無音で壊れた原因）
3. ヘルパーは即座に**インストール画面（WinForms・タイトル `Autona Updater`）を表示**し、
   **親 Autona プロセス（PID）の完全終了を待つ**（= 全ファイルのロック解放）。
   `staging_prebuilt.marker` があれば展開をスキップ（**マーカー検査は zip 存在検査より
   前**が必須 — 差分DLでは zip が存在しない）。無ければ zip を staging に展開
   （先頭 `Autona/` を strip・ZIP slip ガード）し、robocopy の 2 段で install ディレクトリへ反映する:
   `_internal` は `/MIR`（リリースが 100% 所有。zip から外れたファイルは消える）、残りは `/E`（**`/PURGE` なし**・
   `_internal` は `/XD`）。`tests/test_apply_update_robocopy.py` が pin。新 `Autona.exe` を
   起動して zip/staging/自分自身を後始末
4. 失敗時（親 PID 残存・展開例外・robocopy `exit >= 8`）は**失敗ダイアログを出してから
   旧 exe を再起動**し `assets/update_log.txt` にログを残す（ユーザーを「何も起動しない」
   状態にせず、かつ「黙って旧版に戻った」とも誤解させない）

**ポイント:**
- アプリ終了後に動くため、ランチャ `Autona.exe`・ロード済み `_internal/*.dll`・
  mmap された WebEngine リソース（`v8_context_snapshot.bin`・`*.pak`・`icudtl.dat` 等）も
  **すべて置換可能**。旧来の in-place 上書き＋`.autona_old` リネーム退避は廃止
- `_internal` 以外は `robocopy /E`（PURGE なし）なので、release zip に含まれないユーザー資産
  （`layout/game_images`・`user_data/` のアバター等）は**消えずに残る**。テンプレ
  layout は更新で上書きされる（期待動作）。旧来の `layout_backup_{time}` 退避は廃止
- `_internal` は `/MIR` なので、PyInstaller の excludes や spec のフィルタで外したもの（v1.5.0 の numpy など、
  ADR-005 S-final SF-9 の Qt の同梱物）は更新で消える。PURGE なしだった頃は、旧 `_internal` に残った numpy が namespace package
  として import され、v1.4.1→v1.5.0 の自動更新後に起動できなくなった。**excludes / フィルタを変えたリリースは、Runbook 手順 9 の
  「旧版からの自動更新」で確かめる**（フル zip の起動では見えない）
- `CleanupUpdateLeftovers()`（`Autona.py` 冒頭・frozen のみ）は temp workdir 残骸と、
  旧版から移行したユーザーの legacy `Autona_old.exe` / `*.autona_old` を掃除する
- **インストール中の画面（2026-07-16 追加）**: ヘルパーはスクリプト全体を通じて
  インストール画面を表示し、展開は実ファイル数で進捗を出す。長時間ステップでは必ず
  `Update-UI`（`Application::DoEvents`）を回す（回さないと Windows が「応答なし」表示にする）。
  **`assets/apply_update.ps1` は UTF-8 **BOM 付き**必須** — BOM 無しだと PowerShell 5.1 が
  ANSI（日本語環境では cp932）で読み、画面の日本語が全部文字化けする。
  `tests/test_apply_update_encoding.py` が BOM とウィンドウタイトル契約を守る
- **ヘルパー多重起動の禁止（2026-07-16 追加）**: 2本のヘルパーは同じ workdir を共有する
  ため、後発の失敗時 `Invoke-Cleanup` が先発の robocopy 中の staging/zip を削除する
  （v1.3.0→v1.3.1 毎回失敗の確定原因。update_log 実測: #2 のフォールバック掃除 17:55:48
  → #1 の robocopy exit 11 17:56:42）。対策3層: (1) ヘルパーは排他ロックを取れなければ
  **何も掃除せず即 exit 0**、(2) `_UpdateDialog._StartDownload` はロック検出時に
  ダウンロード自体を拒否（DL先も共有 workdir のため）、(3) `UpdateProcedure()` 入口でも
  ロック検出時はヘルパーを spawn しない
- **進行中アップデートの保護（2026-07-15 追加 / 2026-07-16 改訂）**: ヘルパーは
  `%TEMP%\autona_update\update_in_progress.lock` を排他モード（FileShare.None）で
  保持する（2026-11-16 改訂: 保持するのは robocopy が終わるまで。exe を起動する前に必ず手放す。
  起動の後まで握っていたため、新しい exe がゲートで黙って終了し、再起動しなかった。v1.8.1→v2.0.0 の E2E で発覚。
  `tests/test_apply_update_relaunch.py` が順序を pin）。アプリはこのロックを検出すると (1) main.py の起動ゲートで
  **何も表示せず即 `sys.exit(0)`**（インストール画面を前面に出すだけ）し、
  (2) `CleanupUpdateLeftovers()` は workdir 掃除をスキップする。
  **ゲートでモーダルを出してはいけない**: そのプロセス自身が `Autona.exe` であり、
  実行中イメージは Windows が書き込みロックするため、ダイアログを読んでいる間
  robocopy が**ランチャを置換できず** `exit >= 8` → 旧 exe 復帰 =「勝手にロールバック
  された」に見える（ユーザー報告の根本原因）。ロック導入前は、インストール中に手動起動された
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
