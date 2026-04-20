# Autona (BST Scoreboard)

## プロジェクト体制

個人開発プロジェクト。役割は以下の 2 名体制。

| 役割 | 担当 | 責任範囲 |
|------|------|---------|
| **PdM 兼 経営者** | ユーザー（田口） | プロダクト方針・優先順位・UX 意思決定・リリース判断 |
| **エンジニアリード** | Claude | 設計・実装・検証・コード品質に対する最終責任 |

**Claude の責務:**
- コードの最終責任はエンジニアリードにある。PdM の指示を鵜呑みにせず、設計上の懸念があれば必ず提起する
- 実装前に不明点は質問し、実装後は検証まで完了させる（動かないコードを「できました」と報告しない）
- 既存パターンから逸脱する判断は、理由を明示した上で実行する
- リファクタ・削除は影響範囲を調査してから行う。参照元にないコード＝不要ではない

**PdM からの依頼の受け方:**
- 曖昧な要件は具体化して確認してから着手する
- 複数案がある場合は最大 2 案を比較提示する（ルール: `c:/Users/ayuyt/Work/CLAUDE.md`）
- 「OK」「ビルドして」等の短い指示は、直前の文脈で確定した方針の実行として解釈する

---

## プロジェクト概要

Autona のカスタムフォーク。
格闘ゲーム大会の配信支援ツール（スコアボード管理、ステージストライク、統計情報表示など）。

- **リポジトリ**: `yuya-tagu/Autona`
- **メインブランチ**: `main`
- **デスクトップアプリ**: Python (PyQt6) — `src/`
- **Web UI**: React (MUI) — `stage_strike_app/src/`
  - ※ フォルダ名は上流由来の歴史的経緯（元々は TournamentStreamHelper のフォーク）。実態はスコアボード等の汎用 Web UI。リネームは影響範囲が広いため保留
- **ビルド**: PyInstaller → `Autona.exe`（プロジェクト直下、約 236 MB）

---

## 開発原則

1. **既存パターンに従う** — 独自の抽象化やフレームワークを持ち込まない
2. **変更したら検証する** — 構文チェック・ビルド・起動・ログ確認を段階的に行う
3. **壊れたものは根本原因を直す** — 表面的な回避策ではなく原因を特定する
4. **不要なコードは残さない** — 削除した機能の関数・import は一緒に消す
5. **モーダル入れ子禁止** — QSpinBox.valueChanged 等のシグナル内から QMessageBox.exec() を呼ばない（過去に C++ クラッシュの事例あり）

---

## Git ルール

- `main` への直接 push 禁止
- `feature/{修正概要}` ブランチを作成し、PR 経由でマージ
- コミットメッセージ・PR 説明文は日本語
- コミット・プッシュは明示的に依頼されたときのみ

### 作業ディレクトリ（worktree 運用）

- **Claude Code の worktree 機能（`.claude/worktrees/*`）を使う**
- ルートリポジトリ `C:\Users\ayuyt\Work\30_Products\Autona` は常に `main`
  に揃えておく（作業用ブランチはチェックアウトしない）
- 理由:
  - ルートを `main` 固定にすることで、ユーザーがデバッグ（`python main.py` や
    exe の実行）する際に必ず「リリース済みの最新コード」を触れる
  - Claude の並行作業による意図しないファイル変更がルートへ波及しない
  - PR 作業中でもルート側で既存機能を検証できる
- ビルド成果物（`Autona.exe`、`stage_strike_app/build/`、`dist/` 等）は
  ルート側で運用する。worktree 内のビルド成果物は参考程度に留める

#### 王道フロー（Claude Code 標準機能に任せる）

1. **作成**: ルートから `claude --worktree <name>` で起動する。
   - `.claude/worktrees/<name>/` が作成され、ブランチ
     `worktree-<name>` が `origin/HEAD` から切られる
   - `<name>` は内容を表す短い英語（例: `bracket-fix`, `thumbnail-perf`）。
     timestamp や連番は不要（`git log --all` / `gh pr list` で時系列は追える）
2. **作業**: 通常通り編集・コミット・push する。ブランチ名はそのまま
   `worktree-<name>` で良い（PR 作成時にリネームしない）
3. **PR**: `gh pr create` でタイトルと本文を日本語で付ける。
   ブランチ名は検索のノイズにならないよう適度に短ければ何でも OK
4. **マージ後の掃除**: セッションを終了すれば Claude が自動判定する
   - 変更が全て push/マージ済 → worktree・ローカルブランチを自動削除
   - 未コミット変更や未 push コミットあり → 保持／削除を対話で選択
5. **手動 cleanup は原則不要**。`git worktree remove` / `git branch -D` は
   Claude が自動 cleanup できない特殊ケース（別プロセスが worktree 内の
   ファイルを掴んでいる等）にだけ使う

#### 特殊ケース

- **ブランチ名を変えたい**: PR 作成前であれば `git branch -m` で自由に
  変えて良い。ただし変えた瞬間から Claude の自動 cleanup 対象から外れる
  （マージ済みかどうかを Claude が追跡できなくなるため）ので、
  変えたら `git worktree remove` / `git branch -D` を自分で叩く前提で
- **既に worktree 内にいる**: その worktree 内で作業を継続して差し支えない
- **orphan worktree の掃除**: 何かの事故で worktree/ブランチが残った場合は
  ルートで `git worktree prune` → `git fetch --prune` → `git branch -D` の順

---

## ビルド

### 開発サイクル中は `python main.py` を使う

**反復開発中の動作確認は必ず `python main.py` で行う**。PyInstaller による exe ビルドは 3〜5 分かかり、反復速度を著しく損なう。`python main.py` と exe は同じコードを実行するので、ほぼ全ての確認事項（UI・シグナル・StateManager・API 呼び出し等）は main.py で十分。

exe ビルドが本当に必要なケース:
- 配布前の最終確認
- PyInstaller 固有の挙動検証（hook 不足、モジュール収集漏れ、データファイル同梱等）
- 凍結後の挙動を疑う特定のバグ調査

ユーザーから明示的に「ビルドして」「exe で確認」と言われた時のみ PyInstaller を実行する。

### コマンド

```bash
# Web UI（先に実行）
cd stage_strike_app && npm run build

# exe（配布前のみ）
pyinstaller dependencies/autona.spec --noconfirm

# ビルド後のコピー
cp dist/Autona.exe ./Autona.exe

# 開発時の実行（基本これ）
python main.py
```

- `Autona.exe` はプロジェクト直下のものを使う（`dist/` ではない）
- **Autona.exe が起動中の場合は `taskkill /IM Autona.exe /F` で終了してからコピーしてよい**
- フォント: LINE Seed JP（`assets/font/LINESeedJP/`、Google Fonts gstatic CDN から取得）
- 翻訳コンパイル: `pyside6-lrelease src/i18n/Auto_ja.ts -qm src/i18n/Auto_ja.qm`（※ PySide6 は PyInstaller ビルド前にアンインストールすること。PyQt6 と共存不可）
- **WebEngine 依存**: サムネイル生成（[src/thumbnail/ThumbnailRenderer.py](src/thumbnail/ThumbnailRenderer.py)）が `PyQt6-WebEngine` を使用。`dependencies/requirements.txt` に含まれており、`pip install -r` で自動インストールされる。Qt6WebEngineCore.dll が約 196 MB あるため、Autona.exe が 230〜245 MB 程度になるのは正常
- **上流成果物の扱い**: `TSH.exe` / `TSH/` は上流 TournamentStreamHelper のビルド成果物。Autona 側では使用しないため残さない

---

## アーキテクチャ概要

### デスクトップアプリ (`src/`)

Python / PyQt6 のドックウィジェットベース。主要コンポーネント:

| コンポーネント | ファイル | 役割 |
|--------------|---------|------|
| **Window** | `Autona.py` | QMainWindow。QMenuBar（編集・Startgg・タイトル）+ 中央「大会を選ぶ」ボタン + 全ドックウィジェット管理。STATE_VERSION によるレイアウト復元 |
| **StateManager** | `StateManager.py` | グローバル状態（nested dict）。deepdiff でデルタ算出、ファイル出力 + WebSocket 配信 |
| **SettingsManager** | `SettingsManager.py` | ユーザー設定の永続化（JSON） |
| **AutonaScoreboardManager** | `AutonaScoreboardManager.py` | シングルトン。単一スコアボード (`scoreboardNumber = 1`) を dock に保持する薄いラッパー。`GetScoreboard(1)` で `AutonaScoreboardWidget` を取得 |
| **AutonaScoreboardWidget** | `AutonaScoreboardWidget.py` | スコアボード本体。チーム/選手/スコア/セット情報。`_applyScoreboardMode(singlePlayer)` で 1v1 ⇄ チーム UI を可逆切替 |
| **AutonaTournamentDataProvider** | `AutonaTournamentDataProvider.py` | シングルトン。Start.gg 等からデータ取得のオーケストレーション |
| **AutonaGameAssetManager** | `AutonaGameAssetManager.py` | シングルトン。ゲーム別キャラ/ステージ/アイコン管理 |
| **AutonaPlayerDB** | `AutonaPlayerDB.py` | ローカル選手DB（static クラス、`user_data/local_players.json`） |
| **AutonaWebServer** | `AutonaWebServer.py` | QThread。Flask + SocketIO で Web UI にリアルタイム配信 |
| **AutonaHotkeys** | `AutonaHotkeys.py` | シングルトン。pynput によるグローバルホットキー |

**重要なパターン:**
- **シングルトン**: `*.instance` パターン（ScoreboardManager, GameAssetManager, TournamentDataProvider, Hotkeys, AlertNotification）
- **スレッドセーフ**: StateManager は `threading.RLock`、PlayerDB は `multiprocessing.Lock`
- **Qt Signal/Slot**: 各コンポーネントに `*Signals(QObject)` クラスが対応
- **Context Manager**: `StateManager.SaveBlock()` で一括更新時のファイル書き込み抑制
- **Worker スレッド**: `Workers.py` の `Worker(QRunnable)` でバックグラウンド処理

### Web UI (`stage_strike_app/src/`)

React 19 / Redux Toolkit / MUI。フォルダ名は上流由来の歴史的経緯。入力欄は全て `size="small"` で統一。

| ページ | パス | 主要コンポーネント |
|--------|------|-------------------|
| **ScoreboardPage** | `/scoreboard` | Header, CurrentSet, Team, Player, SetScore, CharSelector, UpcomingSets |
| **StageStrikePage** | `/stage-strike-app` | StageCard, RpsDialog, FooterControls, PlayerWonButton |

**データフロー:**
1. Socket.IO 接続 → `program_state` で全状態受信 → Redux `overwrite()`
2. 差分更新 → `program_state_update` でデルタ受信 → Redux `addDeltas()`
3. 1000ms 間隔で `applySavedDeltas()` により Immer でパッチ適用
4. ユーザー操作 → REST API (POST) → デスクトップアプリ → 状態変更 → WebSocket で全クライアントに配信

**Redux Slices:** `tshState`（トーナメント状態）、`tshPlayers`（選手DB）、`tshCharacters`（キャラDB）、`tshGames`（ゲーム一覧）、`tshCountries`（国一覧）、`websocketInfo`（接続状態）、`selectedScoreboard`（選択中タブ）、`visibility`（表示切替）

### 状態キーパス（StateManager）

```
root
├── score.{N}              # スコアボード N 番
│   ├── team.{1|2}         # チーム
│   │   ├── player.{N}     # 選手（gamerTag, name, seed, country_code, mains...）
│   │   └── score, color   # スコア・チームカラー
│   ├── best_of, match, phase
│   ├── stage_strike        # ステージストライク状態
│   └── last_sets, history_sets, recent_sets
├── game                    # 選択中ゲーム（characters, stages, variants）
├── bracket                 # ブラケット情報
├── commentary.{N}          # 実況者情報
├── tournamentInfo          # 大会メタデータ
└── streamQueue             # 配信キュー
```

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
| `plans/*.md` | フェーズ単位の作業計画 | 目的・原則・Step一覧・成果物・完了条件 | 1セッション分の具体指示（prompts に書く） |
| `prompts/*.md` | 1セッション分の指示書 | 対象範囲・手順・制約・報告フォーマット | フェーズ全体のロードマップ（plans に書く） |
| `architecture/*.md` | コードベース調査の成果物 | import 関係、状態キーパス、依存グラフ等の事実情報 | リファクタ方針・TODO（known-smells.md に書く） |

### Rules（パス別自動ルール）

| ファイル | 対象 | 概要 |
|---------|------|------|
| `rules/python-widgets.md` | `src/**/*.py` | PyQt6 ウィジェットの作法 |
| `rules/react-web-ui.md` | `stage_strike_app/src/**` | React/Redux の規約 |
| `rules/build-and-release.md` | `dependencies/**`, `scripts/**` | ビルド・リリース手順 |
| `rules/state-and-webserver.md` | `src/StateManager.py`, `src/AutonaWebServer*.py`, `src/Helpers/**` | 状態管理・Web サーバー・ヘルパーの作法 |
| `rules/agent-pitfalls.md` | `src/**/*.py`, `tests/**/*.py` | エージェントが踏みやすい地雷の回避ルール |

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
| `/verify` | テスト + ビルド + 起動確認の一括検証 |

### Plans（フェーズ単位の作業計画）

| ファイル | 概要 |
|---------|------|
| `plans/refactor-phase1.md` | Python 読解と安全網フェーズの計画 |

### Prompts（セッション指示書）

| ファイル | 用途 |
|---------|------|
| `prompts/step1.md` | Python import 関係 + シングルトン初期化順の調査 |

### Architecture（調査成果物）

| ファイル | 内容 |
|---------|------|
| `architecture/python-imports.md` | src/ の import 関係マップ |
| `architecture/singleton-init-order.md` | シングルトン初期化順と依存グラフ |
| `architecture/state-keys.md` | StateManager キーパスの read/write マップ |
| `architecture/python-hotspots.md` | 巨大ファイル・関数・コード重複の特定 |

※ 本ディレクトリのファイルは調査の事実情報のみを記録する。リファクタ方針や TODO は `.claude/known-smells.md` に分離する。

### その他

| ファイル | 役割 |
|---------|------|
| `.claude/known-smells.md` | リファクタ候補リスト。architecture/ の調査結果を元に作成し、フェーズ2の夜間タスク配布の燃料として使う |
| `.claude/manual-test-checklist.md` | 自動テストで守れない動作確認手順。朝の PR レビュー時になぞる |
