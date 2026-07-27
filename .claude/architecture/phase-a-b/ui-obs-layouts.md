# OBS 用 HTML レイアウトレイヤー（Phase A 調査結果）

**Phase A の調査結果。事実情報のみ。判断・提案は別フェーズ。**

## 1. テンプレートディレクトリ一覧

| ディレクトリ | 用途 | ファイル構成 |
|---|---|---|
| `layout/scoreboard/` | ゲーム別スコアボード | 16 種のゲーム別 HTML + index.css/js + preview 画像 |
| `layout/scoreboard_Universal/` | 汎用スコアボード | index.html/css/js + common.js + chip 表示用アイコン |
| `layout/scoreboard_simple/` | シンプル版 | index.html/css/js + preview 画像 |
| `layout/scoreboard_topleft/` | 左上固定 | index.html/css/js + index_portraits.html |
| `layout/bracket_top8/` | TOP8 ブラケット | index.html/css/js（テンプレート駆動） |
| `layout/bracket_top12/` | TOP12 | index.html/css/js |
| `layout/bracket_top16/` | TOP16 | index.html/css/js |
| `layout/bracket_ultimate/` | 大型ブラケット | index.html/css/js + index_online_avatars.html + SVG |
| `layout/result/` | リザルト水平 | result.html/css/js |
| `layout/result_top16/` | リザルト TOP16 | result.html/css（JS なし） |
| `layout/result_vertical/` | 縦リザルト | result.html/css |
| `layout/result_vertical_geki/` | 縦リザルト・激 | result.html/css/js（独立 JS 8.9KB） |
| `layout/recent_sets/` | 最近のセット | index.html/css/js |
| `layout/upset_factor/` | アップセットファクター（バッジ型） | index.html/css/js |
| `layout/h2h_record/` | 対戦成績サマリ + 直近対戦 | index.html/css/js + wifi.svg |
| `layout/last_sets/` | プレイヤー別・直近セット | player1.html / player2.html + css/js |
| `layout/commentators/` | コメンテーター名タグ | tag.html / tag_twitter.html / tag_name_twitter.html |
| `layout/camborder_Universal/` | カメラ枠 | index.html/css（JS なし） |
| `layout/thumbnail/` | サムネイル | thumbnail.html/css/js + thumbnail_assets.js |
| `layout/thumbnail_type_b/` | サムネイル B | thumbnail.html/css/js |
| `layout/tournament_history/` | プレイヤー経歴 | player1.html / player2.html + css/js |

**ゲーム別スコアボード (16 種):** arms, bblue, dbfz, fgc, gbvsr, kof15, mk1, multicam, nasb2, pocketbravery, pokken, sf6_offline, sf6_online, skullgirls, ssbultimate, strive, tekken8, uni2

`multicam.html` は独立 CSS/JS、他 15 種は `index.css` / `index.js` を共用（ゲーム名を `body class` で識別）。

---

## 2. テンプレート間の共通リソース

### 2.1 `layout/include/`（27MB）

| 分類 | 内容 | 用途 |
|---|---|---|
| フォント | NotoSans, OpenSans, Oswald, Roboto, RobotoCondensed, OtsutomeFont (7.4MB), Samurai (3.3MB) | CSS @font-face で参照 |
| JS ライブラリ | jQuery 3.6.0/3.7.1, gsap.min.js (71KB), he.js (99KB), lodash.min.js (72KB), kuroshiro*.min.js, jquery.waitforimages, color-thief | `globals.js LoadEverything()` で順次動的ロード |
| コア | globals.js (27KB), assetUtils.js (12KB) | 全 HTML テンプレートで必須 |
| Socket.IO | socket.io.min.js (45KB) + map (168KB) | リアルタイムデータ配信 |
| Kuromoji | kuromoji/ サブディレクトリ | 日本語テキスト処理 |

### 2.2 `layout/main.css`

参照: 全 HTML が `<link rel="stylesheet" href="../main.css" />`

内容:
- グローバル CSS 変数: `--font`, `--text-color`, `--bg-color`, `--p1-score-bg-color` (#fe3636), `--p2-score-bg-color` (#2e89ff)
- 共通クラス: `.tsh_transcript`, `.tsh_character_container`, `.text`
- 代替テーマ枠（コメントアウト）: Dark Solid, Light Gradient

### 2.3 `layout/fonts/`

NotoSans (4 種), Saira (48 種), Red Hat Display (13 種), Red Hat Mono (9 種), Bungee, Kaisotai-Next-UP-B, OtsutomeFont, Roboto, RobotoCondensed, Samurai, OpenSans。**約 190 ファイル**。include/ と重複する例あり。

### 2.4 `layout/icons/`（13 SVG）

birthday, id, last_sets, location, mic, pronouns, recent_sets, replay, seeding, startgg, tournament, tournament_history, twitter

参照方法: CSS `background-image: url()` または HTML 内 include。

---

## 3. 各テンプレートの構造

### 3.1 スコアボード系

- ゲーム別 HTML は共通テンプレ（`<body class="ゲーム名">`）
- JS は `Start()` / `Update()` を実装
- データ参照: `data.score[scoreboardNumber].team[1|2].player[...]` を jQuery で DOM 更新
- アニメーション: GSAP (fade/slide/stagger)
- `scoreboard_Universal` は個別 HTML 不要、`common.js` で動的処理

### 3.2 ブラケット系

- `bracket_top8/12/16`: テンプレート駆動 (TEMPLATES オブジェクト)、4 列 × 2 行グリッド、Entrants 数で動的削減
- `bracket_ultimate`: 高機能版 (index.js 55KB)、`window.USE_ONLINE_PICTURE` フラグで `index_online_avatars.html` 切替

### 3.3 リザルト系（受信方式が違う）

- `result/`, `result_vertical/`, `result_vertical_geki/`: Python から `initResult(payload)` 直接呼び出し
  - payload = `{ data: { players, tournamentName, eventName, startAt, numEntrants }, config: { main_icon_path, background_path, accent_color } }`
  - SocketIO/ポーリング不使用
  - 完了時に `document.title = "RENDER_READY"`（Python 側で監視）
- `result_top16/`: CSS のみ（JS なし）

### 3.4 その他

- `recent_sets`: JSON poll / SocketIO
- `upset_factor`: `score.{N}.upset_factor` + 両選手名。両者が揃ったときだけ gsap autoAlpha で表示
- `h2h_record`: `score.{N}.recent_sets.sets` を集計（`winner == 0` が P1 勝ち）+ 直近 5 件リスト
- `commentators`: 3 バリアント
- `camborder_Universal`: フレーム表示（CSS のみ）
- `top_8`, `thumbnail*`: 静的画像生成（Canvas/SVG 操作）
- `tournament_history`: player1/player2 2 バリアント
- `last_sets`: player1/player2 2 バリアント（`window.PLAYER` で切替）。`score.{N}.last_sets.{PLAYER}` は
  `"1".."N"` キーの dict なので数値昇順ソートして最大 6 件表示

---

## 4. データ受信方式

### 4.1 ブラウザ初期化フロー

```
ブラウザ開始
  ↓ LoadEverything() [globals.js]
    → jQuery, gsap, lodash, socket.io 等を順次 <script> で動的ロード
  ↓ InitAll()
    → LoadSettings(): グローバル + ローカル settings.json をマージ
    → LoadKuroshiro(): 日本語処理初期化
    → window.location.protocol === 'file:' or 'absolute'?
      - YES: UpdateData() を 64ms 間隔でポーリング
      - NO: UpdateData_SocketIO()
        ├─ Socket.IO 接続試行 (websocket → webtransport → polling)
        ├─ 失敗時: UpdateData() にフォールバック
        └─ 成功時: 'program_state', 'program_state_update' イベント受信
```

### 4.2 取得対象: `program_state.json`

ルート: `../../out/program_state.json`（相対パス）

構造:
```
{
  timestamp, score: { "1": { team: { "1":{...}, "2":{...} } }, ... },
  phase, match
}
```

### 4.3 SocketIO イベント

| イベント | 内容 |
|---|---|
| `program_state` | 初回接続時 / manual request、フルデータ送信 |
| `program_state_update` | 差分（delta リスト + delta_index）、adaptive debounce 5ms〜200ms |

イベント処理: `tsh_update` CustomEvent 発行 → `Update(event)` callback。

### 4.4 Static HTML リザルト

SocketIO/ポーリング不使用。Python から `initResult(payload)` を直接呼び出し、HTML/CSS/JS は完全独立。

---

## 5. AutonaWebServer のルート対応

### 5.1 プログラム状態配信

| ルート | メソッド | 説明 |
|---|---|---|
| `/program-state` | GET | JSON 状態返却 |
| `program_state` (SocketIO) | emit | フルデータ配信（初接続時） |
| `program_state_update` (SocketIO) | emit | 差分更新（adaptive 5〜200ms） |

### 5.2 スコアボード制御

- `/scoreboard<N>-get` (GET)
- `/scoreboard<N>-set` (GET with query)
- `/scoreboard<N>-team<team>-scoreup/scoredown` (GET)
- `/scoreboard<N>-team<team>-color-<color>` (GET)
- `/scoreboard<N>-update-team-<team>-<player>` (POST JSON)
- `/scoreboard<N>-swap-teams`, `/scoreboard<N>-get-swap` (GET)
- `/scoreboard<N>-open-set`, `/scoreboard<N>-pull-stream` (GET)

### 5.3 ゲーム/設定情報

`/games`, `/current-game`, `/characters`, `/variants`, `/match-names`, `/ruleset` （GET + SocketIO）

### 5.4 静的ファイル配信

| 設定 | 値 |
|---|---|
| ルート | `/<path:filename>` |
| 実装 | Flask `send_from_directory(os.path.abspath('.'), filename)` |
| キャッシュ | max_age=86400（1 日） |
| MIME | 自動判定 (.js → text/javascript, .png → image/apng) |

アクセス例:
- `http://localhost:5000/layout/scoreboard/arms.html`
- `http://localhost:5000/layout/include/globals.js`
- `http://localhost:5000/layout/fonts/Saira/Saira-Bold.ttf`
- `http://localhost:5000/layout/main.css`

---

## 6. テンプレート追加時の作業範囲

新ゲームスコアボード追加例:

```
layout/scoreboard/newgame.html        ← 新規 HTML
  └─ ../include/globals.js            ← 既存（共用）
  └─ ../main.css                      ← 既存（共用）
  └─ ./index.css                      ← 既存（.newgame { } セレクタで分岐）
  └─ ./index.js                       ← 既存（必要に応じて分岐）
  └─ newgame_preview.png              ← 新規
```

**コード変更:**
1. HTML: `<body class="newgame">`
2. index.css: `.newgame { ... }` セレクタで分岐
3. index.js: 必要に応じてクラスセレクタで分岐
4. Preview 画像追加

### `settings.json` の役割

各テンプレ:
```json
{
  "assets": {"default": {"asset_key": "...", "custom_zoom": 1.0, "custom_center": [0.5, 0.5]}},
  "japanese_transcription": {"enabled": false, "to": "romaji", "mode": "normal", "romajiSystem": "nippon"},
  "automatic_theme": false
}
```

マージ優先順位: ローカル > グローバル > ハードコード。

### Python 側設定

新レイアウトディレクトリは自動スキャンで UI に追加される可能性。テンプレ選択画面の詳細は web UI 側実装に依存。

---

## 7. 主要 JavaScript 関数

### globals.js

| 関数 | 役割 |
|---|---|
| `LoadEverything()` | ライブラリ順次ロード開始点 |
| `InitAll()` | 初期化（設定読み込み、SocketIO 接続判定） |
| `UpdateData()` | program_state.json を AJAX fetch |
| `UpdateData_SocketIO()` | SocketIO 接続・イベントリスナー設定 |
| `LoadSettings()` | global/local settings.json マージロード |
| `Start()` | テンプレート側で実装（初回アニメ等） |
| `Update(event)` | テンプレート側で実装（データ更新時の DOM 更新） |
| `FitText(target)` | テキストを div に fit |
| `RegisterFit(element)` | ResizeObserver 登録 |

### テンプレート実装の標準パターン

```javascript
LoadEverything().then(() => {
  document.addEventListener("tsh_update", async (event) => { await Update(event); });
  document.addEventListener("tsh_init", () => { Start(); });
});

async function Start() { /* 初回アニメ */ }
async function Update(event) {
  const data = event.data;
  // jQuery で DOM 更新
}
```

---

## 8. フォント参照

CSS:
```css
@font-face {
  font-family: "NotoSans";
  src: url("../include/NotoSans-Regular.ttf") format("truetype");
}
```

実行時パス解決: Web Server の `/<path:filename>` で配信されるため HTML 内の相対パスは自動解決。

include/ と fonts/ にフォント重複あり（include/ が優先される推定）。

---

## 9. Preview 画像

- ファイル名規則: `*_preview.png`
- 配置: テンプレートディレクトリ直下
- 用途: テンプレート選択 UI（Python 側 or Web UI）
- サイズ: 約 74KB〜1.5MB

---

## 10. ディレクトリ構造図

```
layout/
├── main.css                          ← 全テンプレート共用 CSS
├── settings.json                     ← グローバル設定
├── logo.png
│
├── include/                          ← 共通ライブラリ
│   ├── globals.js                    ← 初期化・データ受信コア
│   ├── assetUtils.js
│   ├── socket.io.min.js, gsap.min.js, jquery-3.7.1.min.js, lodash.min.js
│   ├── kuroshiro*.min.js, color-thief.min.js
│   ├── *.ttf (フォント), kuromoji/ (辞書)
│
├── fonts/                            ← フォントファイル
├── icons/                            ← SVG アイコン（13 個）
├── scoreboard/, scoreboard_Universal/, scoreboard_simple/, scoreboard_topleft/
├── bracket_top8/, bracket_top12/, bracket_top16/, bracket_ultimate/
├── result/, result_top16/, result_vertical/, result_vertical_geki/
├── recent_sets/, commentators/, camborder_Universal/
├── upset_factor/, h2h_record/, last_sets/    ← 統計オーバーレイ
├── top_8/, thumbnail/, thumbnail_type_b/
└── tournament_history/
```
