# Autona (BST Scoreboard)

格闘ゲーム大会の配信支援ツール（スコアボード管理、統計情報表示など）。

## 参照マップ（詳細はここから）

CLAUDE.md は要点のみ。詳細は以下を**必要時に参照**する（常時ロードしない）。

| 入口 | 内容 |
|------|------|
| [.claude/README.md](.claude/README.md) | `.claude/` 全索引・記載ルール・Rules/Commands/Hooks 一覧 |
| [.claude/design/Design.md](.claude/design/Design.md) | **正本**: UI 実装方式・データモデル・画面・テーマ・ホットキー |
| [.claude/git-workflow.md](.claude/git-workflow.md) | Git 詳細（push/PR/ブランチ削除/マルチアカウント認証） |
| [.claude/known-smells.md](.claude/known-smells.md) / [.claude/manual-test-checklist.md](.claude/manual-test-checklist.md) | リファクタ候補 / 手動テスト手順 |
| [docs/README.md](docs/README.md) | 運用・変更管理（既存仕様・運用制約・変更影響・ADR・調査タスク） |

矛盾時の優先順位: **コードの実態 > `.claude/design/Design.md` > `CLAUDE.md` > その他 Markdown**。

---

## プロジェクト体制

個人開発プロジェクト。役割は 2 名体制。

| 役割 | 担当 | 責任範囲 |
|------|------|---------|
| **PdM 兼 経営者** | ユーザー（田口） | プロダクト方針・優先順位・UX 意思決定・リリース判断 |
| **エンジニアリード** | Claude | 設計・実装・検証・コード品質に対する最終責任 |

**モデル分担（Claude 側の内部体制）:**

| モデル | 役割 | 具体的なタスク |
|--------|------|--------------|
| **Fable**（メイン） | オーケストレータ・要件整理・レビュワー | タスク分解 / 設計判断 / PdM との要件のすり合わせ / サブエージェント成果物の最終レビュー / 自明な 1〜2 行修正（直接編集可） |
| **Opus**（`model: "opus"`） | 複雑な作業 | 複数ファイル横断の実装（新機能・リファクタ・migration+型+UI 連動）/ 設計判断を含む実装 / 原因不明バグ・RLS/認証まわりのデバッグ / ブラウザ検証（golden path 確認。computer use は Opus が最も得意でコスト 1/3）/ PR レビューの verify 役 |
| **Sonnet**（`model: "sonnet"`） | 簡単な作業 | パターン踏襲の単一〜少数ファイル実装（story 追加・文言/配置/色の UI 小修正）/ 機械的変更（rename・型追従・docs 同期）/ grep ベースの調査・影響範囲リストアップ |

- 実装・調査は Agent/Workflow のサブエージェントに委譲し、タスクの複雑さに応じて上表の `model` を指定する。Fable 自身は原則コードを直接書かない（自明な 1〜2 行修正を除く）

**Claude の責務:**
- コードの最終責任はエンジニアリードにある。PdM の指示を鵜呑みにせず、設計上の懸念があれば必ず提起する
- 実装前に不明点は質問する。実装後はテスト計画を立て、ユーザーに提示する
- 既存パターンから逸脱する判断は、理由を明示した上で実行する
- リファクタ・削除は影響範囲を調査してから行う。参照元にないコード＝不要ではない

**PdM からの依頼の受け方:**
- 曖昧な要件は具体化して確認してから着手する
- 複数案がある場合は最大 2 案を比較提示する（ルール: `c:/Users/ayuyt/Work/CLAUDE.md`）
- 「OK」「ビルドして」等の短い指示は、直前の文脈で確定した方針の実行として解釈する

---

## プロジェクト概要

- **リポジトリ**: `yuya-tagu/Autona` / **メインブランチ**: `main`
- **デスクトップアプリ**: Python (PySide6、qtpy 経由) — `src/`。**PySide6 (LGPL) 必須・PyQt6 (GPL) 禁止**（ソース非公開配布のライセンス要件。main.py 冒頭の `QT_API=pyside6` が正本）
- **Web UI**: React (MUI) — `web/src/`（フォルダ名は上流 TournamentStreamHelper フォーク由来の歴史的経緯。実態は汎用スコアボード Web UI。リネームは影響大のため保留）
- **ビルド**: PyInstaller（**onedir**）→ `dist/Autona/`（ランチャ `Autona.exe` 約 20 MB + `_internal/`）。直下に `Autona.exe` + `_internal/` を配置して運用

---

## 開発原則

1. **既存パターンに従う** — 独自の抽象化やフレームワークを持ち込まない
2. **変更したら検証する** — 構文チェック・ビルド・起動・ログ確認を段階的に行う
3. **壊れたものは根本原因を直す** — 表面的な回避策ではなく原因を特定する。**症状の原因が既存設計そのものにある場合は、対症療法（キャッシュ後付け・個別パッチ）で済ませず、設計を根本から見直してクリーンに作り直す**。再設計の影響範囲とリスクは事前に調査し、リスクがリターンに見合わない場合は実装前に PdM へ判断を仰ぐ
4. **不要なコードは残さない** — 削除した機能の関数・import は一緒に消す
5. **モーダル入れ子禁止** — QSpinBox.valueChanged 等のシグナル内から QMessageBox.exec() を呼ばない（過去に C++ クラッシュの事例あり）

---

## ハーネス挙動の注意

Edit/Write 後に出る「No preview server is running...（ブラウザプレビュー）」通知は、Claude_Preview MCP がファイル種別を問わず一律に出すもの。**ブラウザプレビュー検証が必要なのは Web UI 編集時のみ**（`web/src/**/*.{tsx,ts,css}`・`web/public/**` → `preview_start` → `<verification_workflow>`）。Python 本体・`.ui`・翻訳・ビルド設定・ドキュメント等の編集時は無視してよい。ユーザーの明示指示があればそれに従う。

---

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

---

## ビルド

**反復開発中の動作確認は必ず `python main.py` で行う**（exe ビルドは 3〜5 分かかり反復速度を損なう。main.py と exe は同じコードを実行）。exe ビルドは「配布前の最終確認」「PyInstaller 固有挙動の検証」「凍結後の挙動を疑うバグ調査」のときだけ、かつユーザーから明示的に「ビルドして」「exe で確認」と言われた時のみ実行する。詳細（onedir/WebEngine/font/翻訳/配布）は [.claude/rules/build-and-release.md](.claude/rules/build-and-release.md)。

```bash
cd web && npm run build                              # Web UI（先に実行）
pyinstaller dependencies/autona.spec --noconfirm     # exe（配布前のみ）→ dist/Autona/
rm -rf ./_internal && cp -r dist/Autona/_internal ./_internal   # 成果物を直下へ
cp dist/Autona/Autona.exe ./Autona.exe
python main.py                                        # 開発時の実行（基本これ）
```

- `Autona.exe` と `_internal/` はプロジェクト直下のものを使う（`dist/` ではない）。**`_internal/` が無いと起動しない**
- Autona.exe が起動中なら `taskkill /IM Autona.exe /F` で終了してからコピー

---

## アーキテクチャ概要

詳細は [.claude/design/Design.md](.claude/design/Design.md)（正本）、[docs/architecture/current-architecture.md](docs/architecture/current-architecture.md)、状態キーパスは [.claude/architecture/phase-a-b/state-keys.md](.claude/architecture/phase-a-b/state-keys.md)。

### デスクトップアプリ (`src/`) — 主要コンポーネント

Python / PySide6（qtpy 経由）。各パネルは `QDockWidget` サブクラスだがドックせず、左サイドバー + 中央 `QStackedWidget` のページとして表示する。

| コンポーネント | ファイル | 役割 |
|--------------|---------|------|
| **bootstrap** | `bootstrap.py` | composition root。シングルトン生成順の**正本**（`bootstrap_core()`）。`src.*` の import は副作用ゼロ |
| **Window** | `Autona.py` | QMainWindow。サイドバー + QStackedWidget で全パネル管理。起動時構築は Tournament Info と Scoreboard のみで、他パネル・設定ウィンドウは初回表示時に構築 |
| **StateManager** | `StateManager.py` | グローバル状態（nested dict）。deepdiff でデルタ算出、ファイル出力 + WebSocket 配信 |
| **SettingsManager** | `SettingsManager.py` | ユーザー設定の永続化（JSON） |
| **AutonaScoreboardManager** | `AutonaScoreboardManager.py` | シングルトン。board1=1v1、board2=team、board3=crew を `scoreboard.battle_format` で表示切替。起動時はアクティブ形式の 1 面のみ構築し、他は形式切替 / `GetScoreboard()` で随時構築 |
| **AutonaScoreboardWidget** | `AutonaScoreboardWidget.py` | 1v1 / team スコアボード本体。通常スコア操作・選手編集・セット読み込みを担当 |
| **AutonaTournamentDataProvider** | `AutonaTournamentDataProvider.py` | シングルトン。Start.gg 等からのデータ取得オーケストレーション |
| **AutonaGameAssetManager** | `AutonaGameAssetManager.py` | シングルトン。ゲーム別キャラ/ステージ/アイコン管理 |
| **AutonaPlayerDB** | `AutonaPlayerDB.py` | ローカル選手DB（static クラス、`user_data/local_players.json`） |
| **AutonaWebServer** | `AutonaWebServer.py` | QThread。Flask + SocketIO で Web UI にリアルタイム配信。構築・起動はウィンドウ表示後（`Window._StartWebServer()`） |
| **AutonaHotkeys** | `AutonaHotkeys.py` | シングルトン。pynput によるグローバルホットキー |

**起動シーケンス**（4 段。詳細は [docs/changes/startup-architecture-redesign.md](docs/changes/startup-architecture-redesign.md) / [ADR-003](docs/decisions/ADR-003-startup-architecture.md)）:
`main.py` の単一インスタンスゲート → QApplication + スプラッシュ → `from src import Autona`（`bootstrap_core()` でシングルトン生成）→ `Window.__init__`（最小コアのみ構築）→ `show()` → WebServer を非同期起動。所要時間は `STARTUP phase=...` ログ（gate / splash / import-src / window-init / core-shown / webserver-ready）で計測する。

**重要なパターン:**
- **シングルトン**: `*.instance`（ScoreboardManager, GameAssetManager, TournamentDataProvider, Hotkeys, AlertNotification）。生成は `bootstrap.py` のみで行い、モジュール末尾では作らない
- **遅延構築**: サイドバーのパネルと設定/ガイドウィンドウは初回アクセス時にファクトリで構築（`_Create*Panel` / `Ensure*Window`）。未構築でも必要な state のシードは `Window._SeedPanelState()` に集約
- **スレッドセーフ**: StateManager は `threading.RLock`、PlayerDB は `multiprocessing.Lock`
- **Qt Signal/Slot**: 各コンポーネントに `*Signals(QObject)` クラスが対応
- **Context Manager**: `StateManager.SaveBlock()` で一括更新時のファイル書き込み抑制
- **Worker スレッド**: `Workers.py` の `Worker(QRunnable)` でバックグラウンド処理

### Web UI (`web/src/`)

React 19 / Redux Toolkit / MUI。入力欄は全て `size="small"` で統一。ページは ScoreboardPage（`/web` 既定・`/scoreboard` board1・`/team` board2）。データフロー（Socket.IO 受信 → Redux、REST POST → デスクトップ → WebSocket 配信）と Redux slices の詳細は [.claude/design/Design.md](.claude/design/Design.md) を参照。
