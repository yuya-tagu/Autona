# Autona (BST Scoreboard)

格闘ゲーム大会の配信支援ツール（スコアボード管理、統計情報表示など）。
リポジトリ `yuya-tagu/Autona` / メインブランチ `main`。

- **デスクトップアプリ**: Python (PySide6、qtpy 経由) — `src/`。**PySide6 (LGPL) 必須・PyQt6 (GPL) 禁止**（MIT で配布するため、GPL の PyQt6 は使わず LGPL の PySide6 を使う。`main.py` 冒頭の `QT_API=pyside6` が正本）
- **Web UI**: React (MUI) — `web/src/`（フォルダ名は上流 TournamentStreamHelper フォーク由来の歴史的経緯。実態は汎用スコアボード Web UI。リネームは影響大のため保留）
- **ビルド**: PyInstaller（**onedir**）→ `dist/Autona/`。プロジェクト直下に `Autona.exe` + `_internal/` を配置して運用

## 参照マップ（詳細はここから）

CLAUDE.md は要点のみ。詳細は以下を**必要時に参照**する（常時ロードしない）。

| 入口 | 内容 |
|------|------|
| [.claude/README.md](.claude/README.md) | `.claude/` 全索引・記載ルール・Agents/Rules/Commands/Hooks 一覧 |
| [.claude/design/Design.md](.claude/design/Design.md) | **正本**: UI 実装方式・データモデル・画面・テーマ・ホットキー・コンポーネント構成 |
| [.claude/orchestration.md](.claude/orchestration.md) | **オーケストレータ運用**（委譲先の選び方・標準フロー・動作確認の分担・モデル運用・台帳と compact 区切り・worktree 並列の条件） |
| [.claude/git-workflow.md](.claude/git-workflow.md) | Git 詳細（push/PR/ブランチ削除/マルチアカウント認証） |
| [.claude/known-smells.md](.claude/known-smells.md) / [.claude/manual-test-checklist.md](.claude/manual-test-checklist.md) | リファクタ候補 / 手動テスト手順 |
| [docs/changes/test-automation-strategy.md](docs/changes/test-automation-strategy.md) | テスト自動化の層構成・実装順・既知バグ。**ウィジェットのテストを書く前に読む**（`autonaApp` fixture の使い方と制約） |
| [docs/README.md](docs/README.md) | 運用・変更管理（既存仕様・運用制約・変更影響・ADR・調査タスク） |

矛盾時の優先順位: **コードの実態 > `.claude/design/Design.md` > `CLAUDE.md` > その他 Markdown**。

## 体制と作業分担

個人開発プロジェクト。**オーナー／メンテナー**（人間）がプロダクト方針・優先順位・UX 意思決定・リリース判断を持ち、**エンジニアリード**（Claude Code）が設計・実装・検証・コード品質の最終責任を持つ。

- 実装・検証・レビューは [`.claude/agents/`](.claude/agents/) のサブエージェントへ委譲する（`impl` / `patch` / `verify` / `review`）。モデルとツールは各定義の frontmatter が正本。メインセッションはタスク分解・設計判断・要件のすり合わせ・成果物の検収に専念し、自明な 1〜2 行修正を除いて自分でコードを書かない。調査・横断検索も Explore に委譲し、メインは結論だけ受け取る（詳細は [.claude/orchestration.md](.claude/orchestration.md)）
- 課題を先延ばしにして docs やメモリを膨らませない。方針が決まっているものはその場で片付ける。次ターンへ送ってよいのは **オーナーの判断が要る論点**と、**先にマージが必要な依存**だけ
- 設計上の懸念は、指示に反していても提起する
- オーナーの「OK」「ビルドして」等の短い指示は、直前の文脈で確定した方針の実行として解釈する

## 開発原則

1. **壊れたものは根本原因を直す** — 表面的な回避策で塞がない。**症状の原因が既存設計そのものにある場合は、対症療法（キャッシュ後付け・個別パッチ）で済ませず設計を根本から見直す**。再設計の影響範囲とリスクは事前に調査し、リスクがリターンに見合わない場合は実装前に PdM へ判断を仰ぐ
2. **モーダル入れ子禁止** — `QSpinBox.valueChanged` 等のシグナル内から `QMessageBox.exec()` を呼ばない（過去に C++ クラッシュの事例あり）
3. **慣れない機能はゼロから書かない** — まず GitHub でオープンソースプロジェクトを検索してアプローチを比較してから手を動かす。ライセンスを確認する（GPL・PyQt 系のコードは取り込まない）

`src/**/*.py` を触るときの地雷は [.claude/rules/agent-pitfalls.md](.claude/rules/agent-pitfalls.md) に集約している（副作用ゼロ import / `bootstrap.py` の生成順 / モデル退避 / `processEvents()` 禁止など、実クラッシュを出したもの）。glob で自動ロードされる。

## ビルド

**実機の動作確認はメインセッションが隔離データの `python main.py` で行う**（2026-11-07 オーナー承認。手順と制限は [.claude/orchestration.md](.claude/orchestration.md)。開発者の作業ディレクトリでのアプリ本体（デスクトップ / exe / ヘッドレス）の起動は引き続き禁止。サブエージェントはアプリを起動せず、pytest と隔離ヘッドレスのテスト（スモーク・`npm run test:e2e`）までを担う。exe ビルドは 3〜5 分かかり反復速度を損なう。main.py と exe は同じコードを実行）。exe ビルドは「配布前の最終確認」「PyInstaller 固有挙動の検証」「凍結後の挙動を疑うバグ調査」のときだけ、かつユーザーから明示的に「ビルドして」「exe で確認」と言われた時のみ実行する。詳細（onedir/WebEngine/font/翻訳/配布）は [.claude/rules/build-and-release.md](.claude/rules/build-and-release.md)。

```bash
python main.py                                        # 開発時の実行（Claude は隔離データでのみ）
python main.py --headless                             # ウィンドウなし（state + Web サーバーのみ。exe は未対応）→ docs/operations/runtime-operations.md
cd web && npm run build                              # Web UI（exe ビルド前に実行）
cd web && npm run test:e2e                           # ブラウザ E2E（要 build。ヘッドレスを tmp で起動 → docs/changes/test-automation-strategy.md L6）
pyinstaller dependencies/autona.spec --noconfirm     # exe（配布前のみ）→ dist/Autona/
rm -rf ./_internal && cp -r dist/Autona/_internal ./_internal   # 成果物を直下へ
cp dist/Autona/Autona.exe ./Autona.exe
```

- `Autona.exe` と `_internal/` はプロジェクト直下のものを使う（`dist/` ではない）。**`_internal/` が無いと起動しない**
- Autona.exe が起動中なら `taskkill /IM Autona.exe /F` で終了してからコピー

## アーキテクチャの入口

コンポーネント一覧・データフロー・Redux slices の詳細は [.claude/design/Design.md](.claude/design/Design.md)（正本）。状態キーパスの正本は `src/core/StateSchema.py`（`KEY_CATALOG`）。

読み始める場所:

- `src/bootstrap.py` — composition root。**シングルトン生成順の正本**（全シェル共通の `bootstrap_core()` ＋ デスクトップの Qt アダプタを足す `bootstrap_gui()`）
- `src/Autona.py` — QMainWindow。サイドバー + `QStackedWidget` で全パネル管理。**この import 自体が起動シーケンス**なので、テストや調査スクリプトから import しない
- `src/core/StateManager.py` — グローバル状態（nested dict）。deepdiff でデルタ算出、ファイル出力 + WebSocket 配信。**core は Qt を import しない**（`tests/test_core_qt_free.py` が pin）。Qt シグナルはアダプタ `src/StateManagerBridge.py` が提供する。

**遅延構築**: サイドバーのパネルはすべて共有ビュー（`AutonaWebPanelHost`）の React のページで、設定/ガイドウィンドウと同じく初回アクセス時にファクトリで構築する（`_Create*Panel` / `Ensure*Window`）。SCORE も ADR-005 S4ac-3 で Qt 版を削除してこの形になった。ボードの state は core が起動時に 3 つとも作る（`AutonaActions.MakeBoards`、`bootstrap_gui()`）。**サイドバーの既定 index 0 = EVENT** なので、そこを最後に開いていた／新規インストールでは共有ビューが起動時に建つ。未構築でも必要な state のシードは `Window._SeedPanelState()` に集約。起動時間は `STARTUP phase=...` ログで計測する（詳細は [ADR-003](docs/decisions/ADR-003-startup-architecture.md)）。

## ハーネス挙動の注意

Edit/Write 後に出る「No preview server is running...（ブラウザプレビュー）」通知は、Claude_Preview MCP がファイル種別を問わず一律に出すもの。**ブラウザプレビュー検証が必要なのは Web UI 編集時のみ**（`web/src/**/*.{tsx,ts,css}`・`web/public/**` → `preview_start` → `<verification_workflow>`）。Python 本体・翻訳・ビルド設定・ドキュメント等の編集時は無視してよい。ユーザーの明示指示があればそれに従う。

## Git ルール（要点）

詳細フロー・ブランチ削除・複数行コミット構文は [.claude/git-workflow.md](.claude/git-workflow.md) を参照。

- **作業はローカル `main` 上で行う**（worktree も作業ブランチも作らない。例外はサブエージェントの worktree 並列のみ → [.claude/orchestration.md](.claude/orchestration.md)）。ブランチはリモート push 時にだけ切る
- push は `git push origin main:<type>/<topic>`（種別 `feat`/`fix`/`docs`/`chore`/`ci`）。**リモート `main` への直接 push 禁止**（必ず PR 経由）
- コミットメッセージ・PR 説明文は日本語。**コミット・push は明示的に依頼されたときのみ**
- PR を出したら、リスク区分「通常」「軽微」は `gh pr merge <PR番号> --merge --auto` を予約し、CI を待たずに報告して次へ進む（CI はバックグラウンドで見届け、`MERGED` 後にローカル `main` を同期）。「高」は予約せず CI 緑で「マージ可」と報告し、オーナーの確認後にマージする。区分とマージ方式（merge commit）の理由は [.claude/git-workflow.md](.claude/git-workflow.md)。グローバル CLAUDE.md の「マージはユーザーが行う」より優先する（2026-11-04 オーナー承認）
- **マシン固有の認証手順**（複数 GitHub アカウント環境での push / fetch 等）は `CLAUDE.local.md`（gitignore 済み）を参照

# Compact instructions

要約時は以下を優先して保持する:

- 未マージ PR / push 済みブランチと、その検収状態（OK / NG / 検証待ち）
- オーナーの判断待ち論点と、直近の設計判断 + その理由
- 作業中タスクの現在地と次アクション（どこまでやったか・次に何をするか）
- ワーキングツリーの状態（未コミット変更が何のためのものか）

以下は捨ててよい（正本がディスクにある・再取得可能）:

- テスト / build の生ログ（合否と失敗箇所の 1 行要約だけ残す）
- サブエージェントの報告原文（結論と検収結果だけ残す）
- 読んだファイルの本文（パスと要点だけ残す）

進捗の正本は台帳（[.claude/orchestration.md](.claude/orchestration.md) 参照）。コンテキストには台帳にまだ書いていない差分だけ残せばよい。
