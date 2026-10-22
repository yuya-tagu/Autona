# Contributing to Autona / コントリビューションガイド

[日本語](#日本語) | [English](#english)

---

## 日本語

Autona への貢献（バグ報告・機能提案・layout 投稿・コード・翻訳）を歓迎します。Issue・PR は日本語でも英語でも構いません。参加にあたっては [行動規範](CODE_OF_CONDUCT.md) に従ってください。質問は [Discord](https://discord.gg/yAFAmhaUqn) へ。

### 開発環境

- Python 3.12 / Node.js 20 以降

```bash
pip install -r dependencies/requirements.txt
cd web && npm ci
python main.py        # 開発時の起動（動作確認は基本これ）
```

exe のビルドは配布前の確認用です。通常の開発では不要です（手順は [README.md](README.md) 参照）。

### テスト

```bash
QT_QPA_PLATFORM=offscreen python -m pytest   # Python
cd web && npm test                            # Web UI (jest)
```

ウィジェットのテストを書く前に [docs/changes/test-automation-strategy.md](docs/changes/test-automation-strategy.md) を読んでください。

### ブランチ・PR の流れ

1. リポジトリを fork し、作業ブランチを切る
2. 変更をコミット（コミットメッセージは日本語・英語どちらでも可）
3. `main` に向けて PR を作成。テンプレート（Summary / Test plan）に沿って、確認した内容を書く
4. 1 つの PR では 1 つの目的に絞ると、レビューが早く進みます

### 翻訳の更新

UI 文言は `.ts` ファイルを**手で編集**してから、`scripts/compile_translations.ps1` を実行して `.qm` を生成します。`pyside6-lupdate` は使えません。Web UI の翻訳は `web/src/i18n/` にあります。

### 設計資料への入口

- [.claude/design/Design.md](.claude/design/Design.md) — 設計の正本（UI 実装方式・データモデル・コンポーネント構成）
- [docs/README.md](docs/README.md) — 運用・変更管理・ADR
- [.claude/rules/agent-pitfalls.md](.claude/rules/agent-pitfalls.md) — 実際にクラッシュを起こした地雷集。`src/` を触る前に読んでください

### layout の自作・投稿

OBS 用オーバーレイ（layout）は自作・共有できます。作り方は [layout/README.md](layout/README.md) を参照してください。投稿は Issue テンプレート「layout 投稿」から行えます。

### AI エージェントを使った貢献

Claude Code などの AI エージェントを使った貢献も歓迎します。その場合も [CLAUDE.md](CLAUDE.md) のルール（`.claude/rules/` を含む）に従い、生成物は提出者が内容を理解し、動作を確認したうえで提出してください。

---

## English

Contributions of all kinds are welcome: bug reports, feature requests, layouts, code, and translations. Issues and PRs may be written in Japanese or English. Please follow our [Code of Conduct](CODE_OF_CONDUCT.md). For questions, join our [Discord](https://discord.gg/yAFAmhaUqn).

### Development setup

- Python 3.12 and Node.js 20+

```bash
pip install -r dependencies/requirements.txt
cd web && npm ci
python main.py        # run in development mode
```

Building the exe is only for pre-release checks and is not needed for normal development (see [README.en.md](README.en.md)).

### Running tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest   # Python
cd web && npm test                            # Web UI (jest)
```

Before writing widget tests, read [docs/changes/test-automation-strategy.md](docs/changes/test-automation-strategy.md) (Japanese).

### Branches and pull requests

1. Fork the repository and create a working branch.
2. Commit your changes (commit messages may be in Japanese or English).
3. Open a PR against `main`, following the template (Summary / Test plan) and describing what you verified.
4. Keep each PR focused on a single purpose; it makes review faster.

### Updating translations

Edit the `.ts` files **by hand**, then run `scripts/compile_translations.ps1` to generate the `.qm` files. `pyside6-lupdate` cannot be used. Web UI translations live in `web/src/i18n/`.

### Design documents

- [.claude/design/Design.md](.claude/design/Design.md) — the source of truth for design (UI approach, data model, components)
- [docs/README.md](docs/README.md) — operations, change management, ADRs
- [.claude/rules/agent-pitfalls.md](.claude/rules/agent-pitfalls.md) — pitfalls that caused real crashes; read before touching `src/`

These are mostly written in Japanese; feel free to ask on Discord or in an Issue if something is unclear.

### Creating and submitting layouts

You can build and share your own OBS overlays (layouts). See [layout/README.md](layout/README.md), then submit via the "Layout submission" issue template.

### Contributing with AI agents

Contributions made with AI agents such as Claude Code are welcome. Please follow the rules in [CLAUDE.md](CLAUDE.md) (including `.claude/rules/`), and make sure you understand and have tested whatever the agent produced before submitting it.
