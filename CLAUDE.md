# Autona (BST Scoreboard)

格闘ゲーム大会の配信支援ツール（スコアボード管理、統計情報表示など）。
リポジトリ `yuya-tagu/Autona` / メインブランチ `main`。

- **デスクトップアプリ**: Python (PySide6、qtpy 経由) — `src/`。**PySide6 (LGPL) 必須・PyQt6 (GPL) 禁止**（ソース非公開配布のライセンス要件。`main.py` 冒頭の `QT_API=pyside6` が正本）
- **Web UI**: React (MUI) — `web/src/`（フォルダ名は上流 TournamentStreamHelper フォーク由来の歴史的経緯。実態は汎用スコアボード Web UI。リネームは影響大のため保留）
- **ビルド**: PyInstaller（**onedir**）→ `dist/Autona/`。プロジェクト直下に `Autona.exe` + `_internal/` を配置して運用

## 参照マップ（詳細はここから）

CLAUDE.md は要点のみ。詳細は以下を**必要時に参照**する（常時ロードしない）。

| 入口 | 内容 |
|------|------|
| [.claude/README.md](.claude/README.md) | `.claude/` 全索引・記載ルール・Agents/Rules/Commands/Hooks 一覧 |
| [.claude/design/Design.md](.claude/design/Design.md) | **正本**: UI 実装方式・データモデル・画面・テーマ・ホットキー・コンポーネント構成 |
| [.claude/git-workflow.md](.claude/git-workflow.md) | Git 詳細（push/PR/ブランチ削除/マルチアカウント認証） |
| [.claude/known-smells.md](.claude/known-smells.md) / [.claude/manual-test-checklist.md](.claude/manual-test-checklist.md) | リファクタ候補 / 手動テスト手順 |
| [docs/changes/test-automation-strategy.md](docs/changes/test-automation-strategy.md) | テスト自動化の層構成・実装順・既知バグ。**ウィジェットのテストを書く前に読む**（`autonaApp` fixture の使い方と制約） |
| [docs/README.md](docs/README.md) | 運用・変更管理（既存仕様・運用制約・変更影響・ADR・調査タスク） |

矛盾時の優先順位: **コードの実態 > `.claude/design/Design.md` > `CLAUDE.md` > その他 Markdown**。

## 体制と作業分担

個人開発プロジェクト。**PdM 兼経営者**（ユーザー・田口）がプロダクト方針・優先順位・UX 意思決定・リリース判断を持ち、**エンジニアリード**（Claude）が設計・実装・検証・コード品質の最終責任を持つ。

- 実装・検証・レビューは [`.claude/agents/`](.claude/agents/) のサブエージェントへ委譲する（`impl` / `patch` / `verify` / `review`）。モデルとツールは各定義の frontmatter が正本。メインセッションはタスク分解・設計判断・要件のすり合わせ・成果物の検収に専念し、自明な 1〜2 行修正を除いて自分でコードを書かない
- 設計上の懸念は、指示に反していても提起する
- 「OK」「ビルドして」等の短い指示は、直前の文脈で確定した方針の実行として解釈する

## 開発原則

1. **壊れたものは根本原因を直す** — 表面的な回避策で塞がない。**症状の原因が既存設計そのものにある場合は、対症療法（キャッシュ後付け・個別パッチ）で済ませず設計を根本から見直す**。再設計の影響範囲とリスクは事前に調査し、リスクがリターンに見合わない場合は実装前に PdM へ判断を仰ぐ
2. **モーダル入れ子禁止** — `QSpinBox.valueChanged` 等のシグナル内から `QMessageBox.exec()` を呼ばない（過去に C++ クラッシュの事例あり）

`src/**/*.py` を触るときの地雷は [.claude/rules/agent-pitfalls.md](.claude/rules/agent-pitfalls.md) に集約している（副作用ゼロ import / `bootstrap.py` の生成順 / モデル退避 / `processEvents()` 禁止など、実クラッシュを出したもの）。glob で自動ロードされる。

## ビルド

**反復開発中の動作確認は必ず `python main.py` で行う**（exe ビルドは 3〜5 分かかり反復速度を損なう。main.py と exe は同じコードを実行）。exe ビルドは「配布前の最終確認」「PyInstaller 固有挙動の検証」「凍結後の挙動を疑うバグ調査」のときだけ、かつユーザーから明示的に「ビルドして」「exe で確認」と言われた時のみ実行する。詳細（onedir/WebEngine/font/翻訳/配布）は [.claude/rules/build-and-release.md](.claude/rules/build-and-release.md)。

```bash
python main.py                                        # 開発時の実行（基本これ）
cd web && npm run build                              # Web UI（exe ビルド前に実行）
pyinstaller dependencies/autona.spec --noconfirm     # exe（配布前のみ）→ dist/Autona/
rm -rf ./_internal && cp -r dist/Autona/_internal ./_internal   # 成果物を直下へ
cp dist/Autona/Autona.exe ./Autona.exe
```

- `Autona.exe` と `_internal/` はプロジェクト直下のものを使う（`dist/` ではない）。**`_internal/` が無いと起動しない**
- Autona.exe が起動中なら `taskkill /IM Autona.exe /F` で終了してからコピー

## アーキテクチャの入口

コンポーネント一覧・データフロー・Redux slices の詳細は [.claude/design/Design.md](.claude/design/Design.md)（正本）。状態キーパスは [.claude/architecture/phase-a-b/state-keys.md](.claude/architecture/phase-a-b/state-keys.md)。

読み始める場所:

- `src/bootstrap.py` — composition root。**シングルトン生成順の正本**（`bootstrap_core()`）
- `src/Autona.py` — QMainWindow。サイドバー + `QStackedWidget` で全パネル管理。**この import 自体が起動シーケンス**なので、テストや調査スクリプトから import しない
- `src/StateManager.py` — グローバル状態（nested dict）。deepdiff でデルタ算出、ファイル出力 + WebSocket 配信

**遅延構築**: 起動時に作るのは Tournament Info と Scoreboard のみ。他パネルと設定/ガイドウィンドウは初回アクセス時にファクトリで構築（`_Create*Panel` / `Ensure*Window`）。未構築でも必要な state のシードは `Window._SeedPanelState()` に集約。起動時間は `STARTUP phase=...` ログで計測する（詳細は [ADR-003](docs/decisions/ADR-003-startup-architecture.md)）。

## ハーネス挙動の注意

Edit/Write 後に出る「No preview server is running...（ブラウザプレビュー）」通知は、Claude_Preview MCP がファイル種別を問わず一律に出すもの。**ブラウザプレビュー検証が必要なのは Web UI 編集時のみ**（`web/src/**/*.{tsx,ts,css}`・`web/public/**` → `preview_start` → `<verification_workflow>`）。Python 本体・`.ui`・翻訳・ビルド設定・ドキュメント等の編集時は無視してよい。ユーザーの明示指示があればそれに従う。

## Git ルール（要点）

詳細フロー・ブランチ削除・複数行コミット構文は [.claude/git-workflow.md](.claude/git-workflow.md) を参照。

- **作業はローカル `main` 上で行う**（worktree も作業ブランチも作らない）。ブランチはリモート push 時にだけ切る
- push は `git push origin main:<type>/<topic>`（種別 `feat`/`fix`/`docs`）。**リモート `main` への直接 push 禁止**（必ず PR 経由）
- コミットメッセージ・PR 説明文は日本語。**コミット・push は明示的に依頼されたときのみ**
- **このマシン固有**: アクティブ gh アカウントが `yuyat-highfield` だと `yuya-tagu/Autona` へ push/fetch が `Repository not found` で失敗する。一回限りの手順で行う（push 後は必ず元に戻す）:

```bash
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" \
  push origin main:<type>/<topic>
gh auth switch --user yuyat-highfield   # 元のアクティブアカウントに復元
```
