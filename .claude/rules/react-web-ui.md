---
paths:
  - "web/src/**"
---

# Web UI (React) 開発ルール

## 開発サーバー

- dev サーバーのポートは **3100 固定**（`web/.env` の `PORT=3100`）。他プロジェクトが 3000 を使うため CRA 既定から変更済み
- バックエンド（デスクトップアプリ）は従来どおり 5000（`web/src/env.js` の `BACKEND_PORT`）

## コンポーネント

- MUI (Material UI) を使用。独自の CSS フレームワークを追加しない
- `BACKEND_PORT` は `env.js` から import する
- fetch URL は `http://${window.location.hostname}:${BACKEND_PORT}/...` の形式で統一

## Redux

- 新しい state slice を追加したら `store.ts` にも登録する
- フィールド表示トグルは Redux ではなく `display_options.show_*`（同期 state）を正とする
  （Design.md §2.2 R-React-10）。**キーとデフォルトの定義の正本は `src/core/StateSchema.py`
  の `DISPLAY_OPTIONS`**。`ScoreboardPage/displayOptions.js` は JS から Python を import
  できないための写しで、`tests/test_state_schema.py` が両者の一致を検査する。キーを増減する
  ときは StateSchema 側を先に直す

## ビルド

- コード変更後は必ず `npm run build` でビルドが通ることを確認する
- `web/build/` がデスクトップアプリから配信される成果物
- ビルドせずにデスクトップアプリを起動すると古い Web UI が表示される

## 未使用コードの削除

- ボタンやUI要素を削除した場合、対応する関数・import も削除する
- 使われていない import は React の warning になりビルドサイズに影響する
