# Autona Web UI

## 日本語

Autona のスコアボード操作用 Web UI（React + MUI）。デスクトップアプリ内蔵の Web サーバー（既定ポート 5107）から配信され、ブラウザからスコアボードを操作できる。
`src/ScoreboardDesigner/` はデスクトップアプリ内の QWebEngine 専用で、通常のブラウザからは使わない。
フォルダ名 `web/` は上流（TournamentStreamHelper）由来の歴史的経緯で、実態は Autona 専用の UI。

### 開発コマンド

```bash
npm ci          # 依存関係のインストール
npm start       # 開発サーバー（http://localhost:3100。バックエンドは 5107 に接続）
npm test        # テスト（jest）
npm run build   # 本番ビルド → web/build/（デスクトップアプリが配信、PyInstaller ビルド前に実行）
```

## English

Web UI (React + MUI) for operating the Autona scoreboard. It is served by the desktop app's built-in web server (default port 5107).
`src/ScoreboardDesigner/` is intended only for the QWebEngine view inside the desktop app, not for regular browsers.
The `web/` folder name is historical (inherited from upstream TournamentStreamHelper).

### Commands

```bash
npm ci          # install dependencies
npm start       # dev server (http://localhost:3100, talks to the backend on 5107)
npm test        # run tests (jest)
npm run build   # production build -> web/build/ (served by the desktop app; run before the PyInstaller build)
```
