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
格闘ゲーム大会の配信支援ツール（スコアボード管理、統計情報表示など）。

- **リポジトリ**: `yuya-tagu/Autona`
- **メインブランチ**: `main`
- **デスクトップアプリ**: Python (PyQt6) — `src/`
- **Web UI**: React (MUI) — `web/src/`
  - ※ フォルダ名は上流由来の歴史的経緯（元々は TournamentStreamHelper のフォーク）。実態はスコアボード等の汎用 Web UI。リネームは影響範囲が広いため保留
- **ビルド**: PyInstaller（**onedir**）→ `dist/Autona/`（ランチャ `Autona.exe` 約 20 MB + `_internal/`）。直下に `Autona.exe` + `_internal/` を配置して運用

---

## 開発原則

1. **既存パターンに従う** — 独自の抽象化やフレームワークを持ち込まない
2. **変更したら検証する** — 構文チェック・ビルド・起動・ログ確認を段階的に行う
3. **壊れたものは根本原因を直す** — 表面的な回避策ではなく原因を特定する
4. **不要なコードは残さない** — 削除した機能の関数・import は一緒に消す
5. **モーダル入れ子禁止** — QSpinBox.valueChanged 等のシグナル内から QMessageBox.exec() を呼ばない（過去に C++ クラッシュの事例あり）

---

## Claude Code ハーネスの挙動に関する注意

### ブラウザプレビュー通知（PostToolUse:Edit/Write hook）は多くの場合無視してよい

Edit / Write ツール実行後に以下のメッセージがハーネスから自動注入されることがある:

> PostToolUse:Edit hook additional context: No preview server is running. Before ending your turn, if this change is observable in a browser preview...

これは Claude_Preview MCP がファイル種別を判定せずに一律で出している通知で、
**Autona では以下のファイル編集時は「ブラウザプレビュー対象外」として無視してよい**:

- Python デスクトップアプリ本体（`src/**/*.py`・`main.py`）
- Qt UI 定義（`src/layout/**/*.ui`）
- Qt 翻訳（`src/i18n/**/*.ts`・`.qm`）
- ビルド・依存設定（`dependencies/**`・`scripts/**`・`*.spec`）
- ドキュメント（`*.md`・`.claude/**`）
- PyInstaller や Python ランタイムが実行するもの全般

**プレビュー検証が必要な編集**は Web UI 側だけ:

- `web/src/**/*.tsx`・`web/src/**/*.ts`・`web/src/**/*.css`
- `web/public/**`
- この場合のみ `preview_start` → `<verification_workflow>` を実行

ユーザーに「プレビューで確認して」等の明示指示があれば上記に関わらず従う。

---

## Git ルール

- **作業はローカル `main` 上で行う**（worktree もローカル作業ブランチも作らない）。ブランチはリモート push 時にだけ切る
- push 時は `git push origin main:<type>/<topic>`（種別ごとに `feat` / `fix` / `docs`）でリモート側にだけブランチを作り、PR 経由でマージする
- コミットメッセージ・PR 説明文は日本語
- コミット・プッシュは明示的に依頼されたときのみ

### 作業ブランチ運用

Hivee と同じ「ローカル `main` 上で作業し、push 時にだけリモート種別ブランチを切って PR 経由でマージ」する運用に統一する。

- リモート: `origin` = `https://github.com/yuya-tagu/Autona.git`（HTTPS, GitHub アカウント `yuya-tagu`）
- **作業はローカル `main` 上で行う**。worktree もローカル作業ブランチも作らない。ブランチはリモート push 時にだけ切る
- push 時は `git push origin main:<type>/<topic>`（種別ごとに `feat` / `fix` / `docs`）でリモート側にだけブランチを作る。**リモート `main` への直接 push はしない**（必ず PR 経由でマージ）
- ブランチ命名（`<topic>` 部分）は内容を表す短い英語（例: `feat/bracket-fix`、`docs/operational-docs-foundation`、`fix/thumbnail-perf`）。timestamp や連番は不要（`git log --all` / `gh pr list` で時系列は追える）
- ビルド成果物（`Autona.exe`、`web/build/`、`dist/` 等）はこのローカル `main` 上で運用する

#### 王道フロー

1. **最新化**: `git fetch origin && git reset --hard origin/main` でローカル `main` を最新にする
2. **作業**: ローカル `main` 上で通常通り編集・コミットする（メッセージは日本語）
3. **PR**: 「PRを出して」と依頼されたとき、Claude は コミット（ローカル `main`）→ `git push origin main:<type>/<topic>` で push → PR 文案作成 → PR 作成 URL 共有 までを実行する（`gh pr create` 本体はユーザーが手動。コミット・push は明示的に依頼されたときのみ）
4. **マージ後の同期**: PR マージ後は `git fetch origin && git reset --hard origin/main` でローカル `main` を再同期する
5. **マージ後のブランチ削除**: GitHub 側の自動削除は無効なので、マージした種別ブランチをリモートから削除する（後述「ブランチ削除フロー」）。push と同じく `yuya-tagu` 認証が必要

#### 注意

- ローカル作業ブランチを切らないため、未 push のコミットはローカル `main` に積まれる。push（手順3）→ PR → マージ → 再同期（手順4）→ ブランチ削除（手順5）まで一連で回す
- 1 つの作業ツリーで作業するため、複数トピックを並行させない。前トピックを push・PR 化してから次に着手する（未コミット変更を残さない。`git status` で確認 → コミットまたは `git stash`）

#### ブランチ削除フロー

ローカルには作業ブランチを作らないため、掃除対象はリモート（`origin`）の種別ブランチのみ。
`origin/main` と上流フォークの `upstream/*` は**絶対に削除しない**。削除は破壊的なので
**main にマージ済みのものだけ**を対象とし、未マージブランチは中身を確認してから個別に判断する。

**単発（マージ直後）**: PR をマージしたら、そのトピックブランチを削除する。

```bash
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" \
  push origin --delete <type>/<topic>
git fetch origin --prune
gh auth switch --user yuyat-highfield   # 元のアクティブアカウントに復元
```

**一括（定期掃除）**: マージ済みブランチがリモートに溜まったときに棚卸ししてから消す。

```bash
# 1. 分類して確認（削除はまだしない）
git fetch origin --prune
git branch -r --merged origin/main | sed 's/^ *//' \
  | grep '^origin/' | grep -vE 'origin/main$|origin/HEAD'   # 安全に削除可
git branch -r --no-merged origin/main | sed 's/^ *//' \
  | grep '^origin/' | grep -v 'origin/HEAD'                 # 未マージ → 中身を確認

# 2. マージ済みのみ一括削除（upstream/* は対象外）
branches=$(git branch -r --merged origin/main | sed 's/^ *//' \
  | grep '^origin/' | grep -vE 'origin/main$|origin/HEAD' | sed 's#^origin/##')
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" \
  push origin --delete $branches
git fetch origin --prune
gh auth switch --user yuyat-highfield
```

- 未マージブランチ（`--no-merged`）は `git log origin/main..origin/<branch>` で中身を確認し、
  陳腐化していれば個別に削除、活きていれば残す。一括削除には混ぜない
- 削除後は必ず `yuyat-highfield` に戻す（push 手順と同じ理由）

### コミット・push の実務（このマシン固有）

#### 複数 GitHub アカウントによる push 失敗（`Repository not found`）

このマシンには gh アカウントが 2 つ登録されており、アクティブが
`yuyat-highfield`（HighField 用）になっていることがある。`yuya-tagu/Autona`
はそのアカウントに書き込み権限がないため、アクティブのまま push すると
`remote: Repository not found`（404）で失敗する。SSH 既定鍵・HTTPS
credential manager のいずれも `yuyat-highfield` として認証されるのが原因。

push はグローバル設定を恒久変更しない一回限りの手順で行う:

```bash
gh auth switch --user yuya-tagu
git -c credential.helper= -c credential.helper="!gh auth git-credential" \
  push origin main:<type>/<topic>
gh auth switch --user yuyat-highfield   # 元のアクティブアカウントに復元
```

- `gh auth git-credential` はアクティブアカウントのトークンを返すため、
  先に `yuya-tagu` へ switch しておく
- 手順 2 行目の最初の `-c credential.helper=`（空）で既存ヘルパー一覧を
  リセットし、既定の "manager"（yuyat-highfield を返す）を無効化してから
  gh のヘルパーだけを使わせる
- push 後は必ず `yuyat-highfield` に戻す（ユーザーの他作業のアクティブ
  アカウントを変えたままにしない）
- トークンの値はコマンドにも出力にも一切出さない（CLAUDE.md 機密ルール）

#### 複数行コミットメッセージは bash 構文で渡す

Bash ツールは PowerShell ではなく bash で動く。`@'...'@`（PowerShell
here-string）を使うとメッセージ先頭に `@` が混入するなど壊れる。複数行は
heredoc（`git commit -F - <<'EOF' ... EOF`）を使うか、メッセージをファイルに
書いて `git commit -F <file>` で渡し、コミット後に一時ファイルを削除する。

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
cd web && npm run build

# exe（配布前のみ）。onedir 形式 → dist/Autona/ に出力
pyinstaller dependencies/autona.spec --noconfirm

# ビルド後のコピー（ランチャ + _internal/ を両方）
rm -rf ./_internal && cp -r dist/Autona/_internal ./_internal
cp dist/Autona/Autona.exe ./Autona.exe

# 開発時の実行（基本これ）
python main.py
```

- **onedir 形式**: ビルド成果物は `dist/Autona/`（小さな `Autona.exe` ランチャ + `_internal/`）。
  起動高速化のため onefile から移行（onefile は起動毎に約 250 MB を `%TEMP%` 展開し ~8〜11 秒の
  オーバーヘッド。onedir で展開が消え起動 ~17.5 秒 → ~2.5 秒）。詳細は `.claude/rules/build-and-release.md`
- `Autona.exe` と `_internal/` はプロジェクト直下のものを使う（`dist/` ではない）。**`_internal/` が無いと起動しない**
- **Autona.exe が起動中の場合は `taskkill /IM Autona.exe /F` で終了してからコピーしてよい**
- フォント: LINE Seed JP（`assets/font/LINESeedJP/`、Google Fonts gstatic CDN から取得）
- 翻訳コンパイル: `.\scripts\compile_translations.ps1 -Lang ja`（`dependencies/tools/` に `lrelease.exe` + `Qt6Core.dll` を同梱済み。PySide6 のインストール不要）
- **WebEngine 依存**: サムネイル生成（[src/thumbnail/ThumbnailRenderer.py](src/thumbnail/ThumbnailRenderer.py)）が `PyQt6-WebEngine` を使用。`dependencies/requirements.txt` に含まれており、`pip install -r` で自動インストールされる。Qt6WebEngineCore.dll が約 196 MB あるため、`_internal/` 全体が 600 MB 超になるのは正常
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

### Web UI (`web/src/`)

React 19 / Redux Toolkit / MUI。フォルダ名は上流由来の歴史的経緯。入力欄は全て `size="small"` で統一。

| ページ | パス | 主要コンポーネント |
|--------|------|-------------------|
| **ScoreboardPage** | `/web`（既定）・`/scoreboard`（board1）・`/team`（board2） | Header, CurrentSet, Team, Player, SetScore, CharSelector, UpcomingSets |

※ ステージストライク機能（旧 `StageStrikePage` / `/stage-strike-app` / RULES ドック / `ruleset`・`stage_strike` 状態）は削除済み。

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
│   └── last_sets, history_sets, recent_sets
├── game                    # 選択中ゲーム（characters, stages, variants）
├── bracket                 # ブラケット情報
├── commentary.{N}          # 実況者情報
├── tournamentInfo          # 大会メタデータ
└── streamQueue             # 配信キュー
```

---

## 正本ドキュメント

- **`.claude/design/Design.md`**
  - Autona の UI 実装方式・データモデル・画面仕様・デザインシステム・ホットキー仕様の正本。
  - UI / StateManager / React Web UI / OBS layout / 画像生成 / ホットキーに関する実装判断では、まず本書を参照する。
  - 実装と本書が乖離する場合は、本書を優先し、差分は `.claude/plans/ui-design-refactor.md` または Design.md §8 リファクタ移行プランにタスク化する。
  - `.claude/architecture/phase-a-b/` 配下の Phase A/B 調査ファイル群は、本書の根拠資料として扱う（矛盾時は Design.md を優先）。

---

## 運用・変更管理ドキュメント（`docs/`）

`.claude/` が「開発運用・実装仕様の正本」を担うのに対し、`docs/` は
**運用・変更管理の視点**（既存仕様・運用制約・変更影響・調査/実装タスク）を蓄積する。
Companion / Stream Deck 連携・外部制御 API・OBS 連携拡張などの今後の変更を、
既存運用を壊さずに進めるための情報源。調査・設計と実装を分担しても引き継げるよう、
タスクの種類に応じて担当・実施環境を割り当てる前提で運用する。

| ディレクトリ | 役割 |
|-------------|------|
| `docs/product/` | 既存機能・画面・ユーザー操作・利用シナリオ・リグレッション禁止リスト |
| `docs/architecture/` | 現アーキ・主要モジュール・責務分離・データ/状態の流れ・外部連携（変更影響を判断できる粒度） |
| `docs/operations/` | 実運用手順・起動順・障害/復旧・壊してはいけない操作 |
| `docs/decisions/` | ADR 形式の意思決定ログ（なぜその設計/制約か・不採用案・見直し条件） |
| `docs/changes/` | 変更提案ごとの影響範囲・互換性・移行方針・リリース注意点 |
| `docs/tasks/` | 担当者に渡す自己完結した調査タスク・実装チケット |

**`.claude/` との使い分け:**
- 実装仕様の「あるべき姿」は `.claude/design/Design.md`（正本）。`docs/` はそれを参照し、
  運用・変更時の注意点を書く（実装仕様を二重管理しない）。
- 矛盾時の優先順位は **コードの実態 > `.claude/design/Design.md` > `CLAUDE.md` > その他 Markdown**。
- 確認できない既存仕様は推測で書かず `TBD` とし、確認方法を併記する。
- 入口は [docs/README.md](docs/README.md)。`docs/` を増減した場合はこの目次も最新化する。

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
| `design/*.md` | 正本ドキュメント | あるべき姿の仕様（UI / データモデル / 画面 / テーマ / ホットキー / 拡張ガイド / 移行プラン） | 個別の調査ログ（architecture/ に書く） |

### Rules（パス別自動ルール）

| ファイル | 対象 | 概要 |
|---------|------|------|
| `rules/python-widgets.md` | `src/**/*.py` | PyQt6 ウィジェットの作法 |
| `rules/react-web-ui.md` | `web/src/**` | React/Redux の規約 |
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
| `plans/ui-design-refactor.md` | UI 実装方式リファクタ計画（Design.md と対応） |

### Prompts（セッション指示書）

| ファイル | 用途 |
|---------|------|
| `prompts/step1.md` | Python import 関係 + シングルトン初期化順の調査 |

### Architecture（調査成果物）

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

※ 本ディレクトリのファイルは調査の事実情報のみを記録する。正本は `.claude/design/Design.md`。矛盾時は Design.md 優先。リファクタ方針や TODO は Design.md §8 と `.claude/known-smells.md` に分離する。

### Design（正本ドキュメント）

| ファイル | 内容 |
|---------|------|
| `design/Design.md` | UI 実装方式・データモデル・画面仕様・デザインシステム・ホットキー仕様の正本 |
| `design/external-control-api.md` | External Control API（`/api/v1/`）の I/F 仕様正本（計画/未実装。ADR-001 準拠） |

### その他

| ファイル | 役割 |
|---------|------|
| `.claude/known-smells.md` | リファクタ候補リスト。architecture/ の調査結果を元に作成し、フェーズ2の夜間タスク配布の燃料として使う |
| `.claude/manual-test-checklist.md` | 自動テストで守れない動作確認手順。朝の PR レビュー時になぞる |
