# TournamentStreamHelper (BST Scoreboard)

## プロジェクト概要

TournamentStreamHelper (TSH) のカスタムフォーク。
格闘ゲーム大会の配信支援ツール（スコアボード管理、ステージストライク、統計情報表示など）。

- **リポジトリ**: `yuya-tagu/BST-scoreboard`
- **メインブランチ**: `main`
- **デスクトップアプリ**: Python (PyQt6) — `src/`
- **Web UI**: React (MUI) — `stage_strike_app/src/`
  - ※ フォルダ名は上流由来の歴史的経緯。実態はスコアボード等の汎用 Web UI。リネームは影響範囲が広いため保留
- **ビルド**: PyInstaller → `TSH.exe`

---

## 開発原則

1. **既存パターンに従う** — 独自の抽象化やフレームワークを持ち込まない
2. **変更したら検証する** — ビルド・起動・ログ確認を毎回行う
3. **壊れたものは根本原因を直す** — 表面的な回避策ではなく原因を特定する
4. **不要なコードは残さない** — 削除した機能の関数・import は一緒に消す

---

## Git ルール

- `main` への直接 push 禁止
- `feature/{修正概要}` ブランチを作成し、PR 経由でマージ
- コミットメッセージ・PR 説明文は日本語
- コミット・プッシュは明示的に依頼されたときのみ

---

## ビルド

```bash
# Web UI（先に実行）
cd stage_strike_app && npm run build

# exe
python -m PyInstaller dependencies/tsh.spec --distpath . --workpath build -y

# 開発時の実行
python main.py
```

- `TSH.exe` はプロジェクト直下のものを使う（`dist/` ではない）

---

## `.claude/` 構成と運用ルール

技術的な詳細ルール・自動化は `.claude/` 配下に分離している。
**CLAUDE.md や `.claude/` 配下のファイルを変更した場合、このセクションの目次も最新化すること。**

### 記載ルール

| ファイル | 役割 | 書くべき内容 | 書かない内容 |
|---------|------|------------|------------|
| `CLAUDE.md` | マニフェスト・目次 | 開発原則、Git方針、構成一覧 | 個別技術ルール、具体的なAPI名 |
| `rules/*.md` | パス別の技術ルール | 対象ファイル固有の作法・制約 | プロジェクト全体の方針 |
| `commands/*.md` | スラッシュコマンド | 手順・フォーマット定義 | 実行結果の記録 |
| `settings.json` | Hooks・権限設定 | 自動検証の定義 | ルールの説明（rules に書く） |

### Rules（パス別自動ルール）

| ファイル | 対象 | 概要 |
|---------|------|------|
| `rules/python-widgets.md` | `src/**/*.py` | PyQt6 ウィジェットの作法 |
| `rules/react-web-ui.md` | `stage_strike_app/src/**` | React/Redux の規約 |
| `rules/build-and-release.md` | `dependencies/**`, `scripts/**` | ビルド・リリース手順 |

### Hooks（自動検証）

| トリガー | 内容 |
|---------|------|
| コミット前 | Web UI ビルド確認リマインド |
| ファイル編集後 | QDockWidget の必須設定チェック |

### Commands（スラッシュコマンド）

| コマンド | 用途 |
|---------|------|
| `/create-commit-message` | コミットメッセージ生成 |
| `/create-pr-description` | PR 説明文生成 |
| `/create-test-checklist` | テストチェックリスト生成 |
| `/review-pr` | 設計・保守性レビュー |
| `/build-all` | Web UI + exe 一括ビルド |
