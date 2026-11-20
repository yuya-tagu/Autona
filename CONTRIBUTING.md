# Contributing to Autona / コントリビューションガイド

[日本語](#日本語) | [English](#english)

---

## 日本語

Autona への貢献（バグ報告・機能提案・layout 投稿・コード・翻訳）を歓迎します。Issue・PR は日本語でも英語でも構いません。参加にあたっては [行動規範](CODE_OF_CONDUCT.md) に従ってください。質問は [Discord](https://discord.gg/yAFAmhaUqn) へ。

### 開発環境

- Windows 10 / 11、Python 3.12、Node.js 20 以降（手順は PowerShell 前提）

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt   # requirements.txt + 開発ツール
cd web; npm ci; cd ..
python main.py        # 開発時の起動（動作確認は基本これ）
```

exe のビルドは配布前の確認用です。通常の開発では不要です（手順は [README.md](README.md) 参照）。

### テスト

```powershell
python -m pytest                          # Python（tests/conftest.py が QT_QPA_PLATFORM=offscreen を既定にする）
python scripts/run_tests_parallel.py      # 同じ全量を複数プロセスで並列実行（-n 3 でワーカー数、追加の引数は各 pytest に渡る）
cd web; npm test                          # Web UI (jest)
cd web; npm run build; npm run test:e2e   # ブラウザ E2E（要 build。初回は npx playwright install chromium）
```

テストを書く前に [docs/changes/test-automation-strategy.md](docs/changes/test-automation-strategy.md) を読んでください。

### コードスタイル

```powershell
pip install -r requirements-dev.txt       # ruff・pytest・pre-commit（バージョン固定）
pre-commit install                        # 以後、コミット時に ruff check が走る
```

- **`ruff check` は CI で必須**です（リポジトリ全体が対象。設定は `pyproject.toml`）。手元では `ruff check .` で確認できます。
- 整形（`ruff format` / `cd web; npm run format:check`）は任意です。既存コードはまだ一括整形しておらず、CI では確認しません（公開前に一度まとめて整形し、その後に CI で確認する予定です）。新しく書く部分は `pyproject.toml` / `web/.prettierrc` の設定に寄せてもらえると助かります。
- Web UI の eslint は `cd web; npm run lint` で手動実行できます（CI の対象外。`npm run build` も同じルールで警告を出します）。

### ブランチ・PR の流れ

1. リポジトリを fork し、作業ブランチを切る
2. 変更をコミット（コミットメッセージは日本語・英語どちらでも可）
3. `main` に向けて PR を作成。テンプレート（Summary / Test plan）に沿って、確認した内容を書く
4. 1 つの PR では 1 つの目的に絞ると、レビューが早く進みます

### 翻訳の更新

画面の文言は Web UI の翻訳（`web/src/i18n/locales/`）にあります。サーバーが返す文言（サムネイルのテンプレート名・リザルトのレイアウト名など）は `src/core/CoreWords.py` の日英の表です。`src/i18n/*.ts` に残っているのは Qt のネイティブのダイアログと Updater の文言だけで、コード側で `QCoreApplication.translate("文脈", "原文")` を定数の引数で呼んだうえで、`scripts/update_translations.ps1`（PySide6 同梱の `lupdate.exe`）を実行して `.ts` に文言を取り込み、`Autona_en.ts` / `Autona_ja.ts` の訳を埋め、`scripts/compile_translations.ps1`（同梱の `lrelease.exe`）で `.qm` を生成します。

### 設計資料への入口

- [ARCHITECTURE.md](ARCHITECTURE.md) — コードベースの全体像（英語・1 ページ）
- [docs/architecture/Design.md](docs/architecture/Design.md) — 設計の正本（UI 実装方式・データモデル・コンポーネント構成）
- [docs/README.md](docs/README.md) — 運用・変更管理・ADR
- [docs/development/pitfalls.md](docs/development/pitfalls.md) — 実際にクラッシュを起こした地雷集。`src/` を触る前に読んでください

### layout の自作・投稿

OBS 用オーバーレイ（layout）は自作・共有できます。作り方は [layout/README.md](layout/README.md) を参照してください。投稿は Issue テンプレート「layout 投稿」から行えます。

### AI エージェントを使った貢献

Claude Code などの AI エージェントを使った貢献も歓迎します。その場合も [CLAUDE.md](CLAUDE.md) のルール（`.claude/rules/` を含む）に従い、生成物は提出者が内容を理解し、動作を確認したうえで提出してください。

---

## English

Contributions of all kinds are welcome: bug reports, feature requests, layouts, code, and translations. Issues and PRs may be written in Japanese or English. Please follow our [Code of Conduct](CODE_OF_CONDUCT.md). For questions, join our [Discord](https://discord.gg/yAFAmhaUqn).

### Development setup

- Windows 10 / 11, Python 3.12 and Node.js 20+ (the steps below assume PowerShell)

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd web; npm ci; cd ..
python main.py        # run in development mode
```

Building the exe is only for pre-release checks and is not needed for normal development (see [README.en.md](README.en.md)).

### Running tests

```powershell
python -m pytest                          # Python (tests/conftest.py defaults QT_QPA_PLATFORM to offscreen)
python scripts/run_tests_parallel.py      # the same full suite in parallel processes (-n 3 sets workers; extra args go to each pytest)
cd web; npm test                          # Web UI (jest)
cd web; npm run build; npm run test:e2e   # browser E2E (needs a build; first run: npx playwright install chromium)
```

Before writing tests, read [docs/changes/test-automation-strategy.md](docs/changes/test-automation-strategy.md) (Japanese).

### Code style

```powershell
pip install -r requirements-dev.txt       # ruff, pytest, pre-commit (pinned)
pre-commit install                        # from now on, commits run ruff check
```

- **`ruff check` is required in CI** (over the whole repository; settings in `pyproject.toml`). Run `ruff check .` locally.
- Formatting (`ruff format` / `cd web; npm run format:check`) is optional. The existing code has not been reformatted yet, so CI does not check it (we plan to reformat once before the public release and enable the check then). New code is welcome to follow the settings in `pyproject.toml` / `web/.prettierrc`.
- Web UI eslint can be run by hand with `cd web; npm run lint` (not in CI; `npm run build` reports the same rules as warnings).

### Branches and pull requests

1. Fork the repository and create a working branch.
2. Commit your changes (commit messages may be in Japanese or English).
3. Open a PR against `main`, following the template (Summary / Test plan) and describing what you verified.
4. Keep each PR focused on a single purpose; it makes review faster.

### Updating translations

The UI's words are the Web UI translations (`web/src/i18n/locales/`). Words the server returns (thumbnail template names, result layout names and the like) are the English/Japanese table in `src/core/CoreWords.py`. `src/i18n/*.ts` holds only the words of Qt's native dialogs and the updater: call `QCoreApplication.translate("context", "text")` with constant arguments in the code, run `scripts/update_translations.ps1` (PySide6's bundled `lupdate.exe`) to pull the words into the `.ts` files, fill in the translations in `Autona_en.ts` / `Autona_ja.ts`, then run `scripts/compile_translations.ps1` (the bundled `lrelease.exe`) to generate the `.qm` files.

### Design documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — one-page overview of the codebase (English)
- [docs/architecture/Design.md](docs/architecture/Design.md) — the source of truth for design (UI approach, data model, components)
- [docs/README.md](docs/README.md) — operations, change management, ADRs
- [docs/development/pitfalls.md](docs/development/pitfalls.md) — pitfalls that caused real crashes; read before touching `src/`

These are mostly written in Japanese; feel free to ask on Discord or in an Issue if something is unclear.

### Creating and submitting layouts

You can build and share your own OBS overlays (layouts). See [layout/README.md](layout/README.md), then submit via the "Layout submission" issue template.

### Contributing with AI agents

Contributions made with AI agents such as Claude Code are welcome. Please follow the rules in [CLAUDE.md](CLAUDE.md) (including `.claude/rules/`), and make sure you understand and have tested whatever the agent produced before submitting it.
