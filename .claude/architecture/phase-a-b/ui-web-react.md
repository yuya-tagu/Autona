# React Web UI レイヤー（Phase A 調査結果）

**Phase A の調査結果。事実情報のみ。判断・提案は別フェーズ。**

## 1. ページ構成

### 1.1 ルーティング定義

- 所在: `web/src/index.js`
- ライブラリ: React Router DOM v7.9.1
- ルート:
  - `GET /stage-strike-app` → `StageStrikePage`
  - `GET /scoreboard` → `ScoreboardPage`
  - `GET *` → `/stage-strike-app` へリダイレクト

### 1.2 ページ一覧

1. **ScoreboardPage** (`web/src/ScoreboardPage/index.tsx`) — スコアボード表示・操作
2. **StageStrikePage** (`web/src/StageStrikePage/index.jsx`) — ステージストライク

### 1.3 ScoreboardPage コンポーネント階層

```
ScoreboardPage
├── Header (AppBar): Game selector, Tournament URL loader, Auto-update toggle, Visibility menu
├── CurrentSet
│   ├── Player (x2): CharSelector, CountryStateSelector
│   └── Score display
├── UpcomingSets
└── Clear All Button (REST API)
```

### 1.4 StageStrikePage コンポーネント階層（Class component）

```
StageStrikePage
├── NoRulesetError (条件表示)
├── StageCard (複数)
├── StagePromptText
├── RpsDialog
├── PlayerWonButton
└── FooterControls
```

---

## 2. Redux ストア

### 2.1 Slices 一覧

| Slice | ファイル | initialState | 主要 Action | 用途 |
|-------|---------|-----------|-----------|------|
| `tshState` | `redux/tshState.ts` | `{ tshState: {}, stateDeltas: [], maxAppliedDeltaIdx: -1, initializing: true }` | `applySavedDeltas`, `addDeltas`, `overwrite`, `loadingNewData` | 全体状態 |
| `tshPlayers` | `redux/tshState.ts` | `{ players: {}, initializing: true }` | `overwrite` | プレイヤー DB |
| `tshCharacters` | `redux/tshState.ts` | `{ characters: {}, initializing: true }` | `overwrite` | キャラクター DB |
| `tshGames` | `redux/tshState.ts` | `{ value: {}, initializing: true }` | `overwrite` | ゲーム定義 DB |
| `tshCountries` | `redux/tshState.ts` | `{ value: {}, initializing: true }` | `overwrite` | 国データ |
| `websocketInfo` | `redux/tshState.ts` | `{ status: 'initial', errorMessage: null }` | `setStatus` | WebSocket 接続状態 |
| `visibility` | `redux/uiState.ts` | `{ twitter: true, seed: true, location: false, characters: false, pronouns: false, additional: true }` | `toggle` | スコアボード要素表示切替 |

### 2.2 ストア構成 (`web/src/redux/store.ts`)

```javascript
configureStore({
  reducer: combineReducers({ tshState, tshPlayers, tshCharacters, tshGames, tshCountries, websocketInfo, visibility }),
  devTools: { /* 大規模状態を省略表示 */ }
})
```

---

## 3. データ同期メカニズム

### 3.1 Socket.IO 接続初期化

- 所在: `web/src/websocketConnection.ts`（シングルトン）
- 接続: `io(\`ws://${window.location.hostname}:${BACKEND_PORT}/\`, { transports: ['websocket', 'webtransport'], timeout: 5000, reconnectionDelay: 500, reconnectionDelayMax: 1500 })`

### 3.2 イベントハンドラ

- 所在: `web/src/ScoreboardPage/websocketInit.ts`
- `ScoreboardPage` の `React.useEffect()` で初回マウント時に実行

### 3.3 受信イベント

| イベント | ペイロード | 処理 |
|---------|---------|------|
| `connect` | なし | playerdb, characters, games リクエスト + loadCountriesFile() |
| `program_state` | `{ state, delta_index }` | `tshStateSlice.actions.overwrite()` |
| `program_state_update` | `{ delta_index, delta }` | `tshStateSlice.actions.addDeltas()` → 1000ms ごと `applySavedDeltas()` |
| `playerdb` | `TSHPlayerDb` | `tshPlayersSlice.actions.overwrite()` |
| `characters` | `TSHCharacterDb` | en_name 並べ替え後 `tshCharactersSlice.actions.overwrite()` |
| `games` | `TSHGamesDb` | `tshGamesSlice.actions.overwrite()` |
| `countries` | `TSHCountryDb` | `tshCountriesSlice.actions.overwrite()` |
| `disconnect` | なし | `setStatus("disconnected")` → 自動再接続 |
| `error` | エラー | `setStatus("errored")` |

### 3.4 State Delta パッチ適用 (`web/src/stateDelta.js`)

- 目的: Python DeepDiff の Delta オブジェクトを JS state に適用（Immer）
- 主要関数:
  - `applyDelta(data, delta)`: 単一 delta 適用（path: `["tshState", "score", "1", "team", "1", "score"]` 形式）
  - `applyDeltas(data, deltas[])`: 複数順序適用
  - `combineDeltas(deltas[])`: デバッグ用

### 3.5 同期ポーリング間隔

`web/src/ScoreboardPage/websocketInit.ts:82-87`:
```typescript
setInterval(() => {
  const state = tshStore.getState();
  if (state.websocketInfo.status === "connected" && state.tshState?.stateDeltas.length > 0) {
    tshStore.dispatch(tshStateSlice.actions.applySavedDeltas());
  }
}, 1000);
```

### 3.6 REST API ヘルパー

`web/src/env.js`:
```javascript
const BACKEND_PORT = process.env.NODE_ENV === 'production' ? window.location.port : 5000;
const BASE_URL = `http://${window.location.hostname}:${BACKEND_PORT}`;
```

ラッパー関数なし。各コンポーネントが直接 `fetch` を使用:
```javascript
fetch(`http://${window.location.hostname}:${BACKEND_PORT}/scoreboard${SCOREBOARD_ID}-clear-all`)
fetch(`http://${window.location.hostname}:${BACKEND_PORT}/set-tournament?url=${encodeURIComponent(tournamentUrl)}`)
```

---

## 4. UI ライブラリ・スタイリング

### 4.1 MUI バージョン

| パッケージ | バージョン | 役割 |
|----------|-----------|------|
| `@mui/material` | 7.3.2 | コンポーネント |
| `@mui/icons-material` | 7.3.2 | アイコン |
| `@mui/system` | 7.3.2 | Box, Stack, Grid |
| `@emotion/react` | 11.4.0 | CSS-in-JS |
| `@emotion/styled` | 11.14.1 | styled() helper |

### 4.2 テーマ定義 (`web/src/themes.js`)

**ダークモードトークン:**
```javascript
canvas: "#0A0A0A", bgPrimary: "#121212", bgSecondary: "#1A1A1A",
bgTertiary: "#252524", bgQuaternary: "#2F2F2D",
borderSubtle: "#252524", borderDefault: "#393936", borderStrong: "#55534E",
textPrimary: "#F0EAE0", textSecondary: "#B5ADA0", textMuted: "#85807A",
textLabel: "#A29A90", textDisabled: "#524F4A",
brand: "#DC143C", brandSubtle: "#2A1418",
accent: "#F5A545", accentHover: "#FFB960", accentSubtle: "#2F2820",
gold: "#F0C45A", goldSubtle: "#2E2A1E"
```
**ライトモード:** 同じキー、値反転。

**MUI 色スロット:**
| スロット | マッピング | 用途 |
|--------|-----------|------|
| primary | accent (オレンジ) | 主要ボタン、フォーカス |
| secondary | brand (クリムゾン) | 警告、ステータス |
| error | brand | エラー |
| warning | accent | 警告 |
| success | gold | 成功 |
| p1color | brand (赤) | プレイヤー1 |
| p2color | `#3D8BE8` (青) | プレイヤー2 |
| autona | トークン全体 | `theme.palette.autona` でアクセス |

**コンポーネント styleOverrides:**
- MuiButton: containedPrimary (orange bg + hover), outlined, text
- MuiOutlinedInput: bg + border、focus border on accent
- MuiTab: uppercase, bold, accent on selected
- MuiAppBar: bg primary, border bottom subtle
- MuiTooltip: bg quaternary, border, 11px

### 4.3 グローバル CSS

`index.css`:
```css
body {
  background-color: #0A0A0A;
  color: #F0EAE0;
  font-family: "LINE Seed JP", "Inter", "Noto Sans JP", -apple-system, ...
  -webkit-font-smoothing: antialiased;
}
code { font-family: "JetBrains Mono", ... }
```

`App.css` (StageStrikePage 専用):
- `.text`, `.container` (flex)
- `.stage-icon` (200x128 bg-cover), `.stage-container` (relative, border-radius 16px)
- `.stamp` (48x48 absolute center: stage-striked, stage-dsr, stage-selected, stage-gentlemans SVG)
- `.stage-name` (absolute bottom, white, 28px height)

### 4.4 スタイリング混在度

| 方式 | 頻度 |
|------|------|
| MUI `sx` prop | 非常に多い |
| styled-components | 未検出 |
| makeStyles | 未検出 |
| インライン `style={{}}` | 少数 |
| グローバル CSS | 中程度（StageStrikePage） |

### 4.5 size="small" 統一ルール

CLAUDE.md に記載があるが、コード内で徹底されていない箇所が複数確認できる。MUI TextField 等でデフォルト使用のままの箇所あり。

---

## 5. 多言語化

### 5.1 i18n ライブラリ

i18next v25.5.2, i18next-browser-languagedetector v8.2.0

### 5.2 設定 (`web/src/i18n/config.js`)

```javascript
i18n.use(LanguageDetector).init({
  fallbackLng: ["en"],
  resources: { en, pt, es, ja, fr },
  detection: { order: ["querystring", "navigator"], lookupQuerystring: "lng" },
  ns: ["translations"], defaultNS: "translations",
  interpolation: { escapeValue: false },
});
```

### 5.3 対応言語

- 設定済み: en, pt, es, ja, fr
- ファイル存在するが未登録: it, zh-CN, zh-TW

### 5.4 切替方法

- 自動検出: `navigator.language`
- 手動: URL クエリ `?lng=ja`

---

## 6. ビルド・配信

### 6.1 ビルド

`web/package.json`:
```json
{ "scripts": { "start": "react-scripts start", "build": "react-scripts build", "test": "react-scripts test", "eject": "react-scripts eject" } }
```

出力: `web/build/`、`"homepage": "/web/build/"`

### 6.2 配信 (`src/AutonaWebServer.py:735-744`)

```python
@app.route('/')
@app.route('/scoreboard')
@app.route('/stage-strike-app')
def serve_app(): return send_from_directory(...)

@app.route('/<path:filename>', methods=['GET', 'POST'])
def serve_static(filename): return send_from_directory(...)
```

Flask `send_from_directory()` で `web/build/` 配信。SPA のため全ルートが index.html にフォールバック。

---

## 7. AutonaWebServer REST/Socket API 一覧

### 7.1 REST API ルート（主要）

| ルート | メソッド | 責務 | Web UI 利用 | OBS Layout 利用 |
|-------|---------|------|--------------|-----------------|
| `/program-state` | GET | 全体状態取得 | × | △ |
| `/ruleset` | GET | ステージストライクルール | StageStrikePage | × |
| `/stage_strike_*` | POST | ステージストライク操作（10 種） | StageStrikePage | △ |
| `/score` | POST | スコア設定 | (Socket経由) | × |
| `/scoreboard<N>-team<T>-scoreup/scoredown/color-<C>` | GET | スコア・色操作 | Header (color) | △ |
| `/scoreboard<N>-get/set` | GET/POST | スコアボード状態 | × / Socket | △ |
| `/games`, `/current-game`, `/match-names` | GET | ゲーム情報 | Header | △ |
| `/characters`, `/variants`, `/controllers` | GET | キャラ・バリアント | ScoreboardPage | × |
| `/scoreboard<N>-swap-teams/get-swap/open-set` | GET | チーム操作 | × | △ |
| `/scoreboard<N>-pull-stream` | GET | ストリーム取得 | Header | × |
| `/pull-user` | GET | プレイヤー情報取得 | × | × |
| `/scoreboard<N>-stats-recent-sets/upset-factor/last-sets/history-sets` | GET | 統計 | × | △ |
| `/scoreboard<N>-reset-scores/match/players/clear-all` | GET | リセット | Header (clear all) | △ |
| `/scoreboard<N>-get-thumbnail-<fmt>` | GET | サムネイル | × | △ |
| `/get-sets/get-match-<id>/get-comms` | GET | セット・マッチ情報 | × | × |
| `/scoreboard<N>-load-set/get-set` | GET | セット読み込み | UpcomingSets | △ |
| `/playerdb` | GET | プレイヤー DB | ScoreboardPage (socket) | × |
| `/update-bracket` | GET | ブラケット更新 | × | × |
| `/scoreboard<N>-load-player-from-tag-<T>-<P>` | GET | プレイヤー読み込み | Player (socket) | × |
| `/load-commentator-from-tag-<C>` | GET | コメンテーター | × | × |
| `/set-tournament` | GET | トーナメント設定 | Header | × |
| `/states` | GET | 保存状態一覧 | × | × |

### 7.2 Socket.IO イベント（主要）

| イベント | 方向 | 責務 |
|---------|----|------|
| `connect` / `disconnect` / `error` | ← | 接続管理 |
| `program_state` | ← | フル配信 |
| `program_state_update` | ← | 差分配信 |
| `program-state-update` | → | 更新トリガー |
| `playerdb`, `characters`, `games`, `countries`, `ruleset` | ← | DB 配信 |
| `stage_strike_*` | ↔ | ステージストライク（8 種） |
| `score`, `team_scoreup/scoredown/color`, `set`, `update_team`, `update_commentary`, `update_game` | ↔ | 状態更新 |
| `swap_teams`, `get_swap`, `open_set`, `pull_stream`, `pull_user` | ↔ | チーム/プレイヤー操作 |
| `stats_*` | ↔ | 統計取得（4 種） |
| `reset_scores/match/players`, `clear_all` | ↔ | リセット |
| `load_set/get_set`, `load_player_from_tag`, `load_commentator_from_tag`, `set_tournament` | ↔ | データロード |
| `get_comms`, `get_sets`, `get_match`, `update_bracket`, `states` | ↔ | その他 |

---

## 8. 外部依存パッケージ

### 8.1 メイン依存

| パッケージ | バージョン | 役割 |
|----------|-----------|------|
| react | 19.1.1 | UI |
| react-dom | 19.1.1 | DOM |
| react-router-dom | 7.9.1 | ルーティング |
| @reduxjs/toolkit | 2.9.0 | 状態管理 |
| react-redux | 9.2.0 | Redux バインディング |
| socket.io-client | 4.8.1 | WebSocket |
| @mui/material | 7.3.2 | UI |
| @mui/icons-material | 7.3.2 | アイコン |
| i18next | 25.5.2 | i18n |
| i18next-browser-languagedetector | 8.2.0 | 言語検出 |
| immer | 10.1.3 | 不変性 |
| typescript | 5.9.3 | 型 |
| react-scripts | 5.0.1 | CRA |

### 8.2 devDependencies

@testing-library/{react,jest-dom}, @tsconfig/{create-react-app,recommended}, @types/{react,react-dom,node}, web-vitals

---

## 9. 独自ヘルパーコンポーネント

| コンポーネント | 役割 | 使用例 |
|--------------|------|-------|
| NumberInput.tsx | +/- ボタン付き数値入力 | スコアボード |
| CountrySelector.jsx | 国 Autocomplete (Redux 依存: tshCountries) | Player |
| CountryStateSelector.jsx | 国・州 チェーンセレクト | Player |
| GameIcon.jsx | ゲームアイコン表示 | Header |
| ScoreboardPage/TextField.jsx | カスタム MUI TextField | NumberInput |
| NoSleep.js | 画面スリープ防止 | import するだけで動作 |

---

## 10. 型定義 (`web/src/backendDataTypes.d.ts`)

```typescript
interface TSHCountryInfo { code, display_name, en_name }
interface TSHPlayerInfo { id?, city?, state?, country?, online_avatar?, name, real_name, seed, sponsor_logo?, twitter?, character }
interface TSHTeamInfo { score, teamName, player: Record<number, TSHPlayerInfo>, losers, color }
interface TSHScoreInfo { best_of, match, phase, set_id, team: Record<number, TSHTeamInfo> }
interface TSHCharacterSelection extends TSHCharacterBase { skin }
interface TSHCharacterDb { [codename]: TSHCharacterDbEntry }
type Delta = { path: string[], action: 'dictionary_item_removed' | string, value, type? }
```

---

## 11. ファイルツリー（簡略版）

```
web/src/
├── index.js, index.css, App.css
├── env.js, themes.js, backendDataTypes.d.ts
├── stateDelta.js, websocketConnection.ts, NoSleep.js
├── NumberInput.tsx, CountrySelector.jsx, CountryStateSelector.jsx, GameIcon.jsx
├── redux/
│   ├── store.ts
│   ├── tshState.ts (6 slices)
│   └── uiState.ts (visibility)
├── i18n/config.js + locales/{en,ja,pt,es,fr,it,zh-CN,zh-TW}.json
├── ScoreboardPage/
│   ├── index.tsx, index.jsx, websocketInit.ts
│   ├── Header.jsx, CurrentSet.jsx, UpcomingSets.jsx, Player.jsx
│   ├── CharSelector.jsx, Team.jsx, SetScore.jsx, Commentators.jsx
│   ├── TextField.jsx, Contexts.js, ProgramStateContext.js
└── StageStrikePage/
    ├── index.jsx, StageCard.jsx, StagePromptText.jsx
    ├── PlayerWonButton.jsx, RpsDialog.jsx, FooterControls.jsx
    └── NoRulesetError.jsx, postActions.js
```
