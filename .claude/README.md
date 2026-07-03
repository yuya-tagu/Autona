# `.claude/` 構成と運用ルール

技術的な詳細ルール・自動化は `.claude/` 配下に分離している。CLAUDE.md は要点とポインタのみを持ち、
詳細・索引は本ファイルに集約する。**CLAUDE.md や `.claude/` 配下のファイルを変更した場合、本ファイルの索引も最新化すること。**

## 記載ルール（どこに何を書くか）

| ファイル | 役割 | 書くべき内容 | 書かない内容 |
|---------|------|------------|------------|
| `CLAUDE.md` | マニフェスト・目次 | 開発原則、Git方針の要点、構成への入口 | 個別技術ルール、具体的なAPI名、詳細フロー |
| `.claude/README.md`（本書） | `.claude/` の索引 | 配下ファイルの一覧と役割 | 実装仕様そのもの |
| `.claude/git-workflow.md` | Git 詳細 | push/PR/ブランチ削除/認証ダンスの全手順 | プロジェクト全体の方針 |
| `rules/*.md` | パス別の技術ルール | 対象ファイル固有の作法・制約 | プロジェクト全体の方針 |
| `commands/*.md` | スラッシュコマンド | 手順・フォーマット定義 | 実行結果の記録 |
| `settings.json` | Hooks・権限設定 | 自動検証の定義 | ルールの説明（rules に書く） |
| `plans/*.md` | フェーズ単位の作業計画 | 目的・原則・Step一覧・成果物・完了条件 | 1セッション分の具体指示（prompts に書く） |
| `prompts/*.md` | 1セッション分の指示書 | 対象範囲・手順・制約・報告フォーマット | フェーズ全体のロードマップ（plans に書く） |
| `architecture/*.md` | コードベース調査の成果物 | import 関係、状態キーパス、依存グラフ等の事実情報 | リファクタ方針・TODO（known-smells.md に書く） |
| `design/*.md` | 正本ドキュメント | あるべき姿の仕様（UI / データモデル / 画面 / テーマ / ホットキー / 拡張ガイド / 移行プラン） | 個別の調査ログ（architecture/ に書く） |

## Rules（パス別自動ルール）

frontmatter の `paths:`/`globs:` で対象ファイル編集時のみ自動ロードされる（常時コスト無し）。

| ファイル | 対象 | 概要 |
|---------|------|------|
| `rules/python-widgets.md` | `src/**/*.py` | PyQt6 ウィジェットの作法 |
| `rules/react-web-ui.md` | `web/src/**` | React/Redux の規約 |
| `rules/build-and-release.md` | `dependencies/**`, `*.spec`, `scripts/**` | ビルド・リリース手順（onedir/WebEngine/翻訳/配布） |
| `rules/state-and-webserver.md` | `src/StateManager.py`, `src/AutonaWebServer*.py`, `src/Helpers/**` | 状態管理・Web サーバー・ヘルパーの作法 |
| `rules/agent-pitfalls.md` | `src/**/*.py`, `tests/**/*.py` | エージェントが踏みやすい地雷の回避ルール |

## Hooks（自動検証）

| トリガー | 内容 |
|---------|------|
| コミット前 | Web UI ビルド確認リマインド |
| ファイル編集後 | QDockWidget の必須設定チェック |

## Commands（スラッシュコマンド）

| コマンド | 用途 |
|---------|------|
| `/create-commit-message` | コミットメッセージ生成 |
| `/create-pr-description` | PR 説明文生成 |
| `/create-test-checklist` | テストチェックリスト生成 |
| `/review-pr` | 設計・保守性レビュー |
| `/build-all` | Web UI + exe 一括ビルド |
| `/verify` | テスト + ビルド + 起動確認の一括検証 |

## Plans（フェーズ単位の作業計画）

| ファイル | 概要 |
|---------|------|
| `plans/refactor-phase1.md` | Python 読解と安全網フェーズの計画 |
| `plans/ui-design-refactor.md` | UI 実装方式リファクタ計画（Design.md と対応） |

## Prompts（セッション指示書）

| ファイル | 用途 |
|---------|------|
| `prompts/step1.md` | Python import 関係 + シングルトン初期化順の調査 |

## Architecture（調査成果物）

| ファイル | 内容 |
|---------|------|
| `architecture/README.md` | 本ディレクトリの利用ルール（正本は `design/Design.md`、ここは調査ログ） |
| `architecture/phase-a-b/python-imports.md` | src/ の import 関係マップ |
| `architecture/phase-a-b/singleton-init-order.md` | シングルトン初期化順と依存グラフ |
| `architecture/phase-a-b/state-keys.md` | StateManager キーパスの read/write マップ |
| `architecture/phase-a-b/python-hotspots.md` | 巨大ファイル・関数・コード重複の特定 |
| `architecture/phase-a-b/ui-pyqt.md` | PyQt UI レイヤー（Phase A） |
| `architecture/phase-a-b/ui-obs-layouts.md` | OBS HTML レイアウト（Phase A） |
| `architecture/phase-a-b/ui-image-renderers.md` | WebEngine 画像生成（Phase A） |
| `architecture/phase-a-b/ui-web-react.md` | React Web UI（Phase A） |
| `architecture/phase-a-b/ui-styles-theme.md` | スタイル/テーマ定義の所在（Phase A） |
| `architecture/phase-a-b/ui-problems.md` | 重複/同期/設定/拡張範囲/テーマ統合 + OBS/ClickUp 比較（Phase B） |

※ 本ディレクトリのファイルは調査の事実情報のみを記録する。正本は `design/Design.md`。矛盾時は Design.md 優先。リファクタ方針や TODO は Design.md §8 と `known-smells.md` に分離する。

## Design（正本ドキュメント）

| ファイル | 内容 |
|---------|------|
| `design/Design.md` | UI 実装方式・データモデル・画面仕様・デザインシステム・ホットキー仕様の正本 |
| `design/external-control-api.md` | External Control API（`/api/v1/`）の I/F 仕様正本（計画/未実装。ADR-001 準拠） |

## その他

| ファイル | 役割 |
|---------|------|
| `git-workflow.md` | Git 詳細（push/PR/ブランチ削除/マルチアカウント認証）。CLAUDE.md の Git 要点の詳細版 |
| `release-runbook.md` | **リリース実行手順書**。「リリースして」と依頼されたら上から順になぞる（CWD 事故・アカウントダンス・zip 残留等の再発防止込み） |
| `known-smells.md` | リファクタ候補リスト。architecture/ の調査結果を元に作成し、フェーズ2の夜間タスク配布の燃料として使う |
| `manual-test-checklist.md` | 自動テストで守れない動作確認手順。朝の PR レビュー時になぞる |
