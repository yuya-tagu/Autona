# Autona

格闘ゲームコミュニティ大会の配信を、**準備から本番・投稿まで一気通貫**で支える、日本発の配信ワークベンチ。

スコアボード管理・配信オーバーレイ・サムネ/リザルト画像生成・SNS 投稿までを 1 本に統合し、TO 兼配信オペの少人数運営を効率化する。日本語ファーストで、1v1・チーム・クルーバトルに対応する。

## なぜ Autona か

- **日本語ファースト** — UI・ゲーム用語・トーナメント用語の 3 系統を完全日本語化（ローマ字表示対応）
- **配信前後まで一気通貫** — 試合中のスコア管理に加え、サムネ/リザルト画像の自動生成と X へのワンクリック投稿
- **マルチフォーマット** — 1v1 / チーム / クルーバトル（最大 9v9・ストック管理）を再起動なしで切替
- **Web リモート操作** — ブラウザからの分業オペレーション
- **Start.gg 深度連携** — ブラケット・対戦表・参加者を自動取得

## 主な機能

- **スコアボード管理** — チーム/プレイヤー情報、スコア、キャラクター選択をリアルタイムで管理
- **配信オーバーレイ** — HTML ベースのアニメーション付きオーバーレイテンプレート（OBS 等で使用）
- **トーナメント連携** — Start.gg からブラケット・対戦表・参加者情報を自動取得
- **Web リモート操作** — ブラウザからスコアボードをリモート操作可能
- **統計表示** — H2H（直接対決）、Last Sets、Tournament History を表示
- **ローカル選手DB** — 選手のタグ・国籍・メインキャラなどをローカル管理
- **ホットキー** — グローバルキーボードショートカットでスコア操作
- **サムネイル生成** — 大会サムネイル画像の自動生成
- **日英対応** — 日本語・英語の 2 言語で UI を利用可能

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| デスクトップ GUI | Python 3.12 / PySide6 (qtpy) / qasync |
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
   │    │ ・PlayerBrowser        │       │
   │    │ ・Top8                 │       │
   │    │ ・Stats                │       │
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
│   ├── AutonaTop8Widget.py           # Top 8 ブラケット編集
│   ├── AutonaPlayerBrowserWidget.py  # 選手一覧（出場選手 / 登録済み選手）
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
│   │   └── i18n/                  # 翻訳（日英）
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
> あるため、アプリ内のアセット取得ガイドから取得し、`user_data/games/` 配下に配置して
> ください。詳細は [user_data/games/README.md](user_data/games/README.md) を参照。

## ライセンス

- 上流 [TournamentStreamHelper](https://github.com/joaorb64/TournamentStreamHelper) 由来の部分: MIT License
- Autona 固有の部分: © 2026 taguyan, All rights reserved

詳細は [LICENSE](LICENSE)、配布バイナリの利用条件は [TERMS.md](TERMS.md) を参照。

同梱する第三者ソフトウェアのライセンスは [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)
と `licenses/` に、素材（フォント・国旗画像等）の出典は [ATTRIBUTIONS.md](ATTRIBUTIONS.md)
に集約しています。Qt / PySide6 は LGPL v3 の下で動的リンクにより使用しています。
