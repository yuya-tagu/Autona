---
paths:
  - "stage_strike_app/src/**"
---

# Web UI (React) 開発ルール

## コンポーネント

- MUI (Material UI) を使用。独自の CSS フレームワークを追加しない
- `BACKEND_PORT` は `env.js` から import する
- fetch URL は `http://${window.location.hostname}:${BACKEND_PORT}/...` の形式で統一

## Redux

- `uiState.ts` の `initialState` デフォルト値を変更する場合は影響範囲を確認する
  - visibility トグルのデフォルト変更は全ユーザーの初回表示に影響する
- 新しい state slice を追加したら `store.ts` にも登録する

## ビルド

- コード変更後は必ず `npm run build` でビルドが通ることを確認する
- `stage_strike_app/build/` がデスクトップアプリから配信される成果物
- ビルドせずにデスクトップアプリを起動すると古い Web UI が表示される

## 未使用コードの削除

- ボタンやUI要素を削除した場合、対応する関数・import も削除する
- 使われていない import は React の warning になりビルドサイズに影響する
