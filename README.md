# Autona (BST Scoreboard)

格闘ゲーム大会の配信支援デスクトップアプリケーション。[TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper) のカスタムフォーク。

スコアボード管理、統計情報表示、配信オーバーレイ出力など、大会運営に必要な機能を統合的に提供する。

## 主な機能

- **スコアボード管理** — チーム/プレイヤー情報、スコア、キャラクター選択をリアルタイムで管理
- **配信オーバーレイ** — HTML ベースのアニメーション付きオーバーレイテンプレート（OBS 等で使用）
- **トーナメント連携** — Start.gg からブラケット・対戦表・参加者情報を自動取得
- **Web リモート操作** — ブラウザからスコアボードをリモート操作可能
- **統計表示** — H2H（直接対決）、Last Sets、Tournament History を表示
- **ローカル選手DB** — 選手のタグ・国籍・メインキャラなどをローカル管理
- **ホットキー** — グローバルキーボードショートカットでスコア操作
- **サムネイル生成** — 大会サムネイル画像の自動生成
- **多言語対応** — 9 言語（英語、日本語、ドイツ語、ポルトガル語、スペイン語、フランス語、中国語簡体/繁体、イタリア語）

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| デスクトップ GUI | Python 3.12 / PyQt6 / qasync |
| Web UI | React 19 / TypeScript / Redux Toolkit / MUI 7 |
| リアルタイム通信 | Flask-SocketIO / Socket.IO Client |
| 状態管理 | StateManager（独自）+ deepdiff によるデルタ同期 |
| ビルド | PyInstaller（exe） / react-scripts（Web UI） |
| データソース | Start.gg API |
| 配信オーバーレイ | HTML/CSS/JS（gsap, jquery） |

## セットアップ

### 前提条件

- Python 3.12
- Node.js（Web UI ビルド用）
- pip / pipenv

### インストール

```bash
# Python 依存パッケージ
python -m pipenv install -r dependencies/requirements.txt

# Web UI 依存パッケージ
cd web && npm install
```

### 実行

```bash
# 開発モード
python main.py

# Windows バッチ
Autona_bat.bat

# Linux/Mac
./Autona.sh
```

### ビルド

```bash
# 1. Web UI をビルド（先に実行）
cd web && npm run build

# 2. exe をビルド
python -m PyInstaller dependencies/autona.spec --distpath . --workpath build -y
```

- `Autona.exe` はプロジェクト直下に生成される（`dist/` ではない）
- Web UI のビルドを先に行わないと、exe に古い Web UI が同梱される

### リリース

```powershell
# Windows
scripts/gen_release.ps1
```

`release-windows.zip` に `Autona.exe`、`assets/`、`layout/`、`user_data/`、`web/build/` が含まれる。

## アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│                   main.py (Entry)                    │
│              QEventLoop + asyncio                    │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│             Window (QMainWindow)                     │
│     ドックウィジェット群のオーケストレーション            │
└──┬───────────┬───────────┬───────────┬───────────────┘
   │           │           │           │
   ▼           ▼           ▼           ▼
┌──────┐ ┌──────────┐ ┌────────┐ ┌──────────────┐
│State │ │Tournament│ │GameAsset│ │  WebServer   │
│Manager│ │DataProv. │ │Manager │ │ (Flask+SIO)  │
│      │ │(Start.gg)│ │        │ │              │
└──┬───┘ └────┬─────┘ └───┬────┘ └──────┬───────┘
   │          │            │             │
   │    ┌─────▼────────────▼─────┐       │
   │    │   Dock Widgets         │       │
   │    │ ・Scoreboard (×N)      │       │
   │    │ ・Commentary           │       │
   │    │ ・PlayerList           │       │
   │    │ ・Bracket              │       │
   │    │ ・Stats / Notes        │       │
   │    │ ・ThumbnailSettings    │       │
   │    └────────────────────────┘       │
   │                                     │
   ▼                                     ▼
┌──────────────┐              ┌──────────────────┐
│ out/ (files) │              │ Web UI (React)   │
│ program_state│              │ /web /scoreboard │
│ .json + .txt │              │ /team            │
└──────────────┘              └──────────────────┘
```

### 状態管理フロー

1. **StateManager** がアプリ全体の状態を nested dict で保持
2. 状態変更時に **deepdiff** でデルタを算出
3. デルタを **WebSocket** でブラウザ（Web UI）にプッシュ
4. 同時に `out/program_state.json` + 個別テキストファイルにエクスポート
5. 配信オーバーレイ（HTML）が `out/` のファイルを読み取って表示更新

### Web UI ルーティング

| パス | ページ | 用途 |
|------|--------|------|
| `/web`（既定） | ScoreboardPage | スコアボード 1 のリモート操作 |
| `/scoreboard` | ScoreboardPage | スコアボード 1 のリモート操作 |
| `/team` | ScoreboardPage | スコアボード 2（チーム）のリモート操作 |

## プロジェクト構造

```
Autona/
├── main.py                  # エントリーポイント
├── Autona.exe                  # ビルド済み実行ファイル
├── Autona_bat.bat              # Windows 起動スクリプト
├── Autona.sh                   # Linux/Mac 起動スクリプト
├── Pipfile                  # Python 依存定義
│
├── src/                     # Python ソースコード
│   ├── Autona.py                  # メインウィンドウ (QMainWindow)
│   ├── StateManager.py            # グローバル状態管理
│   ├── SettingsManager.py         # ユーザー設定
│   ├── AutonaScoreboardManager.py    # スコアボード管理
│   ├── AutonaScoreboardWidget.py     # スコアボード UI
│   ├── AutonaScoreboardPlayerWidget.py  # 選手情報ウィジェット
│   ├── AutonaCommentaryWidget.py     # 実況者パネル
│   ├── AutonaBracketWidget.py        # ブラケット表示
│   ├── AutonaPlayerListWidget.py     # 選手リスト
│   ├── AutonaStatsWidget.py          # 統計パネル
│   ├── AutonaTournamentDataProvider.py  # トーナメントデータ取得
│   ├── AutonaGameAssetManager.py     # ゲームアセット管理
│   ├── AutonaPlayerDB.py             # ローカル選手データベース
│   ├── AutonaWebServer.py            # Flask Web サーバー
│   ├── AutonaHotkeys.py              # グローバルホットキー
│   ├── ui_constants.py            # デザイントークン定義
│   ├── Helpers/                   # ユーティリティモジュール
│   ├── Settings/                  # 設定 UI
│   ├── LayoutOptions/             # レイアウトオプション UI
│   ├── TournamentDataProvider/    # データプロバイダー実装
│   └── i18n/                      # 翻訳ファイル・生成スクリプト
│
├── web/        # Web UI (React)
│   ├── src/
│   │   ├── index.js               # エントリーポイント
│   │   ├── themes.js              # MUI ダークテーマ定義
│   │   ├── env.js                 # バックエンドポート設定
│   │   ├── websocketConnection.ts # Socket.IO クライアント
│   │   ├── stateDelta.js          # デルタパッチ適用
│   │   ├── backendDataTypes.d.ts  # TypeScript 型定義
│   │   ├── redux/                 # Redux Store / Slices
│   │   ├── ScoreboardPage/        # スコアボードページ
│   │   └── i18n/                  # 多言語翻訳（9言語）
│   └── build/                     # ビルド成果物
│
├── layout/                  # 配信オーバーレイ（HTML テンプレート）
├── assets/                  # 国旗・アイコン等の静的アセット
├── user_data/               # ゲーム設定・選手データ（ゲーム画像は別途取得）
├── out/                     # 出力ファイル（オーバーレイ用）
├── dependencies/            # PyInstaller spec・requirements
├── scripts/                 # リリーススクリプト
├── build/                   # ビルド中間ファイル
├── test/                    # テスト（アセット検証）
├── logs/                    # ログファイル
└── .claude/                 # Claude Code 設定
```

## 対応ゲーム

`user_data/games/{game}/base_files/config.json` を配置したゲームを自動認識する
（例: Super Smash Bros. Ultimate）。設定ファイルにより他ゲームへ拡張可能。

> **ゲーム画像（キャラクター／ステージ）は同梱していません。** 各権利者に著作権が
> あるため、TournamentStreamHelper のアセットダウンローダーから取得して
> `user_data/games/` 配下に配置してください。詳細は
> [user_data/games/README.md](user_data/games/README.md) を参照。

## ライセンス

MIT License - 詳細は [LICENSE](LICENSE) を参照。

本リポジトリが利用するサードパーティ素材（フォント・国旗画像・ライブラリ等）の
出典・ライセンスは [ATTRIBUTIONS.md](ATTRIBUTIONS.md) に記載。

**Original Author**: [joaorb64](https://github.com/joaorb64/TournamentStreamHelper)
（本プロジェクトは TournamentStreamHelper のフォークです）
