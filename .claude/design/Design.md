# Autona Design Document

> **本書の位置づけ**
> Autona の UI 実装方式・データモデル・画面仕様・デザインシステム・ホットキー仕様の「あるべき姿」を定義する正本。
> 実装と乖離する場合は本書が優先される。差異は実装側または本書の更新で吸収する。
>

---

## 目次

1. [設計原則](#1-設計原則)
2. [UI レイヤー定義](#2-ui-レイヤー定義)
3. [データモデル](#3-データモデル)
4. [画面仕様](#4-画面仕様)
5. [デザインシステム](#5-デザインシステム)
6. [ホットキー仕様](#6-ホットキー仕様)
7. [拡張ガイドライン](#7-拡張ガイドライン)
8. [リファクタ移行プラン](#8-リファクタ移行プラン)

---

## 1. 設計原則

### 1.1 アプリの性格

Autona は「格闘ゲーム大会の配信支援ツール」である。利用シーンは大きく 2 つ:

- **オペレーション**: スコア・選手情報・ゲーム情報を入力して即座に配信反映する（試合進行中）
- **クリエイティブ**: 配信画面・サムネイル・リザルト画像のビジュアルを大会ブランドに合わせて自由にカスタマイズする

この 2 つの性格は **明確に分離する**。前者は一貫性・速度・誤入力防止が最優先、後者は自由度・表現力が最優先。

### 1.2 4 つの設計原則

#### 原則 1: 単一の真実の源（Single Source of Truth）

すべての状態は **`StateManager`**（Python グローバル状態辞書）に集約する。各 UI レイヤーは `StateManager` を読むか、書き込み API 経由で書く。**UI レイヤー間で直接状態を共有してはならない**。

#### 原則 2: アプリ内部 vs クリエイティブの分離

| 領域 | 対象 | 設計方針 |
|------|------|---------|
| **アプリ内部** | PyQt デスクトップ UI（`src/`）/ React Web UI（`web/src/`） | 一貫したデザインシステム、共通の色トークン、命名統一 |
| **クリエイティブ** | OBS HTML レイアウト（`layout/scoreboard*` `bracket*` 等）/ 画像生成テンプレ（`layout/thumbnail*` `result*`） | テンプレートごとに独自配色・独自レイアウトを許容 |

クリエイティブ領域には「アプリ内部のデザインシステム」を強制しない。逆に、アプリ内部にクリエイティブの自由度を持ち込まない。

#### 原則 3: レイヤーごとの責務の明確化

各 UI レイヤーは「自分が扱う仕事だけ」をする:

| レイヤー | 責務 | 責務外（他レイヤーに任せる） |
|---------|------|---------------------------|
| PyQt デスクトップ UI | 入力・編集・運用操作・状態の正規化 | 配信向け表示・画像出力・リモート操作 |
| React Web UI | リモート（手元デバイス）からの操作 | 配信向け表示・画像出力・運用 UI 全般 |
| OBS HTML レイアウト | 配信画面への表示専用 | 状態の編集・永続化 |
| WebEngine 画像生成 | サムネイル/リザルト画像の出力専用 | リアルタイム配信・状態の編集 |

#### 原則 4: 拡張点の明示

新しいゲーム・新しいレイアウト・新しい機能を追加するとき、**触るファイルが何で、なぜそれだけで済むか**を本書 §7 の拡張ガイドラインに記載する。記載されていない拡張点はそもそも追加しない（リファクタが必要）。

### 1.3 既存上流（TournamentStreamHelper）との関係

`web/`、`layout/` のディレクトリ名・構造は上流由来の歴史的経緯。**ディレクトリ構造はリネームしない**（影響範囲が大きい）が、本書では実態に即した役割名で参照する。

例:
- `web/src/` = React Web UI（ScoreboardPage は手元操作用、上流の OBS overlay とは別物）
- `layout/scoreboard_*/` = OBS ブラウザソース用テンプレ（上流踏襲、Autona ではユーザーが OBS に貼って使う）

---

## 2. UI レイヤー定義

Autona は 4 つの UI レイヤーで構成される。各レイヤーは独立した実装言語・実装技術を持ち、`StateManager` を介してのみ連携する。

```
                         ┌──────────────────────────────┐
                         │  StateManager (src/core, Qt-free) │
                         │  単一の真実の源 / RLock 保護    │
                         │  通知は StateManager.observers  │
                         │  （素の callback、Qt 非依存）    │
                         └──────────────┬───────────────┘
                                        │
                         ┌──────────────┴───────────────┐
                         │ StateManagerBridge (Qt adapter)│
                         │ core の observer を購読し同名の │
                         │ Qt シグナル 4 本を re-emit      │
                         └──────────────┬───────────────┘
                                        │
                ┌───────────────────────┼───────────────────────┐
                │                       │                       │
        書き込み│ + signal              │ broadcast             │ snapshot
                │                       │                       │
                ▼                       ▼                       ▼
    ┌──────────────────┐   ┌──────────────────────┐  ┌──────────────────────┐
    │ 2.1 PyQt         │   │ AutonaWebServer      │  │ 2.4 WebEngine        │
    │   デスクトップ UI  │◄──┤ Flask + Socket.IO    │  │   画像生成           │
    │   (操作・編集)     │   │ REST + WS            │  │   (成果物出力)         │
    └──────────────────┘   └──┬───────────────┬───┘  └──────────────────────┘
                              │               │
                              ▼               ▼
                  ┌──────────────────┐  ┌──────────────────┐
                  │ 2.2 React Web UI │  │ 2.3 OBS HTML     │
                  │   (リモート操作)   │  │   レイアウト        │
                  └──────────────────┘  │   (配信表示)        │
                                        └──────────────────┘
```

### 2.1 PyQt デスクトップ UI

**目的**: オペレーターがアプリを起動して、状態を入力・編集・運用する画面。

**技術スタック**:
- PySide6（qtpy 抽象化レイヤー経由。QMainWindow + QDockWidget）。旧 PyQt6 から 2026-07 に移行（LGPL 対応）。本書の「PyQt UI」「R-PyQt-*」という呼称はレイヤー名・ルール ID として維持
- `.ui` ファイル（Qt Designer XML）+ Python 動的構築の併用
- `src/ui_constants.py` の中央集約スタイル + `setStyleSheet` での個別調整

**設計ルール**:

| ルール | 内容 |
|------|------|
| **R-PyQt-1** | UI の構築は `.ui` ファイル（Qt Designer）を優先し、複雑な動的構造のみ Python で組む |
| **R-PyQt-2** | 状態の編集は `StateManager.Set()` 直接呼び出しで反映し、`SaveBlock()` でバッチ化する |
| **R-PyQt-3** | スタイルは `ui_constants.py` の関数（`readonly_style()`, `score_display_style(scale)` 等）経由で適用し、インラインの `setStyleSheet` には色のハードコードを書かない |
| **R-PyQt-4** | 全ドックウィジェットは `objectName` を持ち、`STATE_VERSION` で管理する |
| **R-PyQt-5** | QSpinBox.valueChanged 等のシグナル内から QMessageBox.exec() を呼ばない（C++ クラッシュ事例あり） |
| **R-PyQt-6** | カスタム描画（paintEvent）は QPainter / QPainterPath のみ使用、QGraphicsScene.clear() の連続呼び出し禁止 |

**パネル一覧** — 詳細は §4.1 参照。

**使われない領域**:
- 配信画面の表示（OBS HTML が担当）
- リモート操作（React Web UI が担当）
- 画像生成の HTML レンダリング（WebEngine が担当）

### 2.2 React Web UI

**目的**: オペレーターが手元のスマホ・タブレット・別 PC からスコアボードを操作する画面。

**技術スタック**:
- React 19 + TypeScript 5
- Redux Toolkit（slice 単位で分割）
- MUI 7（Material UI）
- Socket.IO クライアント + REST API
- i18next（日英ローカライズ）

**設計ルール**:

| ルール | 内容 |
|------|------|
| **R-React-1** | ページは React Router で定義し、新規ページは `web/src/{PageName}/index.tsx` パターンで作る |
| **R-React-2** | サーバー同期対象のドメイン状態は Redux slice 経由で取得する。フォーム入力中の一時値・開閉状態などの transient UI state は `useState` を許容する |
| **R-React-3** | 状態の変更は REST API または Socket.IO emit で AutonaWebServer 経由でのみ行う（Redux 側で直接書き換えしない） |
| **R-React-4** | スタイルは MUI テーマ（`themes.js`）経由で適用、`sx` props は許容、`style={{}}` ハードコードは避ける |
| **R-React-5** | フォーム要素は `size="small"` で統一し、密度を高める |
| **R-React-6** | delta は**到着駆動**で即時適用する（`program_state_update` 受信ハンドラ内で `applySavedDeltas` を dispatch）。サーバー側で coalesce 済みのためクライアント側バッチは不要。**200ms の setInterval は安全網**（接続状態遷移中にキューへ残った delta の回収用）としてのみ残す。rAF は不採用（モバイルのバックグラウンドタブで停止するため） |
| **R-React-7** | テーマは**ダーク固定**。React Web UI は `ThemeShell.tsx` で `darkTheme` を常用し、StateManager / SettingsManager とのライト/ダーク同期は行わない |
| **R-React-8** | 楽観的スコアエコー（`useScoreSubmit.ts`）: スコア±はタップ即時にローカル表示を更新し、**絶対値**で `POST /api/v1/scoreboards/{n}/score:set` する（2026-10-21 に旧 `POST /score` から移行）。①サーバーデルタの値がローカル送信値と一致したら pending 解除 ②連打は in-flight 中最新値のみ保持し応答後に 1 回再送（シリアライズ）③失敗時はサーバー値へ revert + Snackbar ④3 秒でタイムアウトし pending 解除（サーバーが正）。R-Sync-4（後勝ち）の範囲内のため revision 突き合わせ・ロールバックログは持たない |
| **R-React-9** | **即時反映モデル**: ScoreboardPage の全入力（選手情報・Phase/Match/Best Of・Losers・チーム名）は入力確定（blur / Enter / 選択）で即送信する。送信ボタンは置かない（デスクトップの `editingFinished` と同モデル）。サーバーエコーによるフォーム巻き戻りは「カード内にフォーカスがある間は表示フィールドを上書きしない」ガードで抑止する（`Player.jsx`） |
| **R-React-10** | フィールド表示トグルは **`display_options.show_*`（StateManager 経由で全クライアント共有）が正**。Web の Eye メニューは `PATCH /api/v1/display-options`（2026-10-21 に旧 `POST /display-option` から移行。Actions 層 `set_display_option` → 各ボードの eye アクション駆動）で書き込み、ローカルの visibility 状態は持たない。キーとデフォルトの正本は `src/core/StateSchema.py` の `DISPLAY_OPTIONS`（デスクトップの eye アクションも `set_display_option` の受理キーもここを読む）。`web/src/ScoreboardPage/displayOptions.js` だけは言語が違うため写しが残り、`tests/test_state_schema.py` が両者の一致を検査する |

**ページ一覧** — 詳細は §4.2 参照。

**使われない領域**:
- 設定ダイアログ（PyQt に集約）
- ブラケット表示（必要時に追加検討）
- 画像生成の起動（PyQt から起動）

### 2.3 OBS HTML レイアウト

**目的**: 配信ソフト（OBS Studio など）の Browser Source として読み込まれ、配信画面に表示される。

**技術スタック**:
- 素の HTML / CSS / JS
- jQuery + GSAP + lodash + Socket.IO（`layout/include/`）
- `layout/main.css` を共通基盤として、各テンプレで上書き
- 共通初期化スクリプト `layout/include/globals.js`

**設計ルール**:

| ルール | 内容 |
|------|------|
| **R-OBS-1** | テンプレディレクトリは `layout/{用途}_{バリアント}/` の命名（例: `scoreboard_simple`, `result_vertical_geki`） |
| **R-OBS-2** | 各テンプレは `Start()` と `Update(event)` を実装。`globals.js` の `tsh_update` イベントを購読する |
| **R-OBS-3** | データ受信は Socket.IO を標準とする。旧 OBS / 古い CEF への第一級対応は不要。ファイルポーリングは degraded fallback として残す扱い |
| **R-OBS-3a** | 更新イベント（`program_state_update`）には `revision`（単調増加の整数）を含める。クライアントは受信ごとに前回 `revision` との差分（+1）を検証する |
| **R-OBS-3b** | reconnect 時、または `revision` の不連続（out-of-order / 欠落）を検知した時、クライアントは `program_state` の full snapshot を再取得して状態を再構築する |
| **R-OBS-4** | テンプレごとに独自配色・独自レイアウトを許容（クリエイティブ領域） |
| **R-OBS-5** | `layout/main.css` の CSS 変数（`--p1-score-bg-color` 等）はテンプレが上書きしてよい |
| **R-OBS-6** | テンプレ内で状態を変更しない（read-only）。書き込みが必要な特殊ケースは事前合意 |
| **R-OBS-7** | プレビュー画像は `*_preview.png` で同ディレクトリに置く |

**テンプレ分類** — 詳細は §4.3 参照。

**使われない領域**:
- 状態の編集
- 設定の永続化（テンプレ単位の `settings.json` のみ。実行時設定は `SettingsManager` 経由）

### 2.4 WebEngine 画像生成

**目的**: サムネイル・リザルト画像を HTML テンプレ + Qt WebEngine でレンダリングして PNG/JPG として出力する。

**技術スタック**:
- Qt WebEngine（QWebEngineView オフスクリーン）
- HTML テンプレ（`layout/thumbnail*`, `layout/result*`）
- Python から JS 経由で JSON ペイロード注入

**設計ルール**:

| ルール | 内容 |
|------|------|
| **R-Img-1** | レンダラーは Singleton（`ThumbnailRenderer.instance()`, `ResultPageRenderer.instance()`）として実装 |
| **R-Img-2** | テンプレ選択は **プレビュー側**（プレビュー画像の直上）にコンボボックスを置く（#29 で確定済み） |
| **R-Img-3** | テンプレ追加時は `TEMPLATE_SUBDIRS`（Thumbnail）/ `LAYOUT_SUBDIRS` ほか（Result。`core/ResultService.py`）への登録 + テンプレディレクトリ作成のみで完結する |
| **R-Img-4** | レンダリング完了は HTML 側で `document.title = "RENDER_READY"` に変更してシグナル化 |
| **R-Img-5** | Python から HTML への入力は `initThumbnail(payload)` / `initResult(payload)` の関数経由で受け渡す |
| **R-Img-5b** | 描画の土台は `src/AutonaOffscreenRenderer.py`（ADR-005 S4q で `ThumbnailRenderer` から切り出した。`ThumbnailRenderer` と `result/ResultPageRenderer` がサブクラスで、ビューはそれぞれ別）。描画は 1 度に 1 つ（チケット。ページの完了の合図は title 1 つなので、同時に走ると互いの絵を取る）。入口は `renderAsync()`（core。結果はコールバックだけ）と、サムネイルだけの `renderSync()`（SCORE・legacy。入れ子のイベントループ）。段階ごとに 10 秒で失敗し、ページを読み直す（ADR-005 S4o） |
| **R-Img-6** | 出力先は非 Preview 時 `./out/{種別}/`、Preview 時 `AutonaResolve("tmp/{種別}")/` |
| **R-Img-7** | 出力ファイル名は `{識別子}-{YYYYMMDDTHHmmssZ}.{ext}`（Windows ファイル名安全、UTC、`:` を含めない）、識別子の特殊文字は除去する |
| **R-Img-8** | クリエイティブ領域なので独自配色・独自レイアウトを許容（`--accent-color: #f0a500` など PyQt/React と異なる値を持ってよい） |

**テンプレ一覧** — 詳細は §4.4 参照。

**使われない領域**:
- リアルタイム配信（OBS HTML が担当）
- 状態の編集（PyQt の Settings Widget 経由）

### 2.5 各レイヤーの責務境界

#### マトリクス: 各概念をどのレイヤーが担うか

| 概念 | PyQt | React | OBS HTML | WebEngine 画像 |
|------|------|-------|---------|----------------|
| 状態の編集（書き込み） | ◎ | ◎ | × | × |
| 状態の運用表示（読み取り） | ◎ | ◎ | × | × |
| 配信画面への描画 | × | × | ◎ | × |
| 画像（PNG/JPG）出力 | × | × | × | ◎ |
| 設定 UI | ◎ | × | × | × |
| ホットキー受付 | ◎ | × | × | × |
| Top 8 ブラケット編集 | × | ◎ | × | × |
| Top 8 ブラケット表示（配信） | × | × | ◎ | × |

◎ = 主担当 / △ = 任意 / × = 担当しない

#### 禁止事項

- **無秩序な機能重複は禁止**。両方で扱う場合は本書に「両方で扱う理由・正本となるレイヤー・競合時の優先順位」を明記する。例: トーナメント設定は PyQt 専有、スコア操作は両方（正本: StateManager、Web からの書き込みは AutonaWebOperations に集約、PyQt と React の同時操作時は最後の書き込みが勝つ）。
- **OBS HTML から状態を書き換えない**。クリエイティブ領域は read-only。
- **WebEngine 画像生成テンプレを OBS Browser Source として直接使わない**。配信用は OBS HTML、画像出力用は WebEngine と分ける（同じ HTML を兼用しない）。

---

## 3. データモデル

### 3.1 StateManager キーパス完全仕様

#### 3.1.1 API 規約

| メソッド | 用途 | スレッドセーフ |
|---------|------|-------------|
| `StateManager.Set(key, value)` | 状態書き込み（ドット区切りキーパス） | Yes (RLock) |
| `StateManager.Get(key, default=None)` | 状態読み取り | Yes（経路: `deep_get`） |
| `StateManager.Unset(key)` | キー削除 | Yes (RLock) |
| `StateManager.SaveBlock()` | コンテキストマネージャ。バッチ書き込み中にファイル出力を抑制 | カウンタ式 |
| `StateManager.GetFullState()` | スナップショット取得（**ロック取得**版） | Yes |
| `StateManager.observers.<channel>.subscribe(cb)` | 変更通知の購読（`state_big_change` / `state_updated` / `held_dirty_changed` / `held_state_updated`）。Qt 非依存の素の callback 登録 | Yes |

**規約**:
- キーパスは **ドット区切り文字列**（例: `"score.1.team.1.player.1.gamerTag"`）
- 数値添字も**文字列として扱う**（`"1"` であって `1` ではない）
- `StateManager.state` への直接参照は **禁止**。必ず `Get` / `GetFullState()` 経由。
- モジュールは `src/core/StateManager.py`（`src/StateManager.py` は headless core Step 1 の互換シム・1 世代で削除予定）。Qt シグナルが必要な場合は `src/StateManagerBridge.py` の `StateManagerBridge.instance` に接続する（core 側に Qt シグナルを追加しない）

#### 3.1.2 キーパスツリー（正本）

凡例: **W** = Set / **R** = Get / **D** = Unset

##### `score.{N}` — スコアボード

`{N}` は `"1"` / `"2"` / `"3"`。board1=1v1、board2=team、board3=crew battle。
board3 は `AutonaCrewBattleWidget` であり、board1/2 用の通常スコア操作とは一部 API が異なる。

```
score.{N}
├── team.{1|2}
│   ├── teamName                     W R
│   ├── score                        W R
│   ├── color                        W
│   ├── logo                         W
│   ├── losers                       W R
│   └── player.{M}                   D
│       ├── name                     W R  (プレイヤー名 = ゲーマータグ)
│       ├── team                     W   (所属タグ。UI 表記は「所属タグ」に統一)
│       ├── real_name                W
│       ├── twitter                  W
│       ├── custom_textbox           W
│       ├── seed                     W R  (int。未シードは 0)
│       ├── birthday                 W   (bool)
│       ├── country                  W   (dict: code, name, display_name, en_name, asset)
│       ├── state                    W   (dict: code, original_code, name, asset)
│       ├── id                       W R
│       ├── city                     W R
│       ├── online_avatar            W R
│       ├── avatar                   W
│       ├── mergedName               W
│       ├── mergedOnlyName           W
│       ├── character.{K}            W R   (dict: name, en_name, assets, skin, variant)
│       ├── mains                    W
│       └── sponsor_logo(s)          W D
├── best_of                          W R
├── best_of_short_text               W
├── best_of_text                     W
├── first_to / _short_text / _text   W
├── match                            W R
├── phase                            W R
├── pool                             W R (フェーズと分離したプール名。表示は overlay 側で "phase - pool" 合成)
├── top_n                            W R
├── set_id                           W
├── auto_update                      W
├── station                          W
├── station_queue                    W
├── teamsSwapped                     W
├── stage_strike                     W D    (dict: currGame, strikedStages, gentlemans, canUndo, canRedo, ...)
├── stages.{I}                       W      (dict: t1_win, t2_win, tie)
├── last_sets.{1|2}.{I}              W R
├── history_sets.{1|2}.{I}           W R
├── recent_sets                      W R    (dict: sets, request_time)
└── upset_factor                     W R    ※番号なし版（`score.upset_factor`）は **削除済**（T-1 完了）
```

board3（crew）のみ、上記に加えてクルー名簿のキーを持つ。書き手は
`src/core/CrewBattleService.py`（board1/2 は一切書かない）:

```
score.3
├── teamsSwapped                     W R  (bool。board1/2 と同じキー。スワップするまで無い)
└── team.{1|2}
    ├── memberNum                    W R  (int。表示中のメンバー行数。両チーム共通)
    ├── activeMember                 W R  (str。出場中メンバーの 1 始まり index。"0" = 不在)
    ├── score                        W    (派生値: 名前が入った表示中メンバーの stock 合計)
    └── player.{M}
        ├── name                     W R  (board1/2 と同じキー = 同じ意味)
        └── stock                    W R  (int。残ストック)
```

クルーのスワップのフラグ（legacy の `get-swap`・v1 の `swapped`）は board1/2 と同じ `score.3.teamsSwapped`
（ADR-005 S4w。React のページが delta で追従するため。それまでは state の外で、スワップに通知が無かった）。
board1/2 と同じくシードは書かず、一度もスワップしていないボードにキーは無い（`false` と読む）。起動時の `ResetScoreRoot` で消える。
ボードの操作はボードの入力欄も Web も `CrewBattleService` を呼び、ボードは state を描画するだけ。
ページの操作は `/api/v1/scoreboards/3/...`（`external-control-api.md` §2.12）。

##### `game` — 選択中ゲーム情報

```
game
├── name, smashgg_id, codename, logo
├── defaults
│   ├── players_per_team
│   └── characters_per_player
├── mods_active, has_stages, has_variants, has_colors
└── has_modded_content
```

##### `top8` — Top 8 ブラケット

旧 `bracket.*` は Top 8 専用化（2026-07）で廃止。残留キーの掃除と `top8.*` の正規化は
`Window._SeedPanelState()` が起動時に行う（パネルが lazy 構築のため、旧 `AutonaTop8Widget.__init__`
から移動。§4.1.1）。トポロジーと進出伝播の正本は
`src/core/Top8Model.py`（FGC 標準ダブルエリミ: Winners 4 名 + Losers 4 名）、
**`top8.*` への書き込みの正本は `src/core/Top8Service.py`**（headless core Step 5 第 5 弾）。
Start.gg からの取り込み（大会の最後の phase のダブルエリミのグループ → `core/Bracket` → 選手の解決 →
`ImportBracket`）も `Top8Service.RefreshFromProvider()` が行い、ページの「大会結果を読み込む」
（`POST /api/v1/bracket:refresh`）・ホットキー・Web が同じ関数を呼ぶ（ADR-005 S3e-8。ヘッドレスでも動く）。
パネルは ADR-005 S4i から React のページ（`/web/top8`、`web/src/Top8Page/`）で、`/api/v1/top8*` に
操作を依頼し、state を描画するだけ（入力中の欄は飛ばす。スコアは「反映」まで画面の中だけで保つ）。
ゲームが別のゲームに変わると、`GameAssetCatalog.SelectGame` が切り替えと同じ保存サイクルで
`Top8Service.ForgetCharacters()` を呼び、`players.*.characters` を `{}` にする（キャラがある枠だけ。
起動時の前回ゲームの復元・同じゲームの選び直しでは消さない。両シェル共通）。
国とキャラは**識別子で受け取り core が値を組む**（ADR-005 S4h）: `SetPlayerCountry(slot, code)` /
`SetPlayerCharacter(slot, en_name, skin)`。ページの選手の書き込みは `UpdatePlayer(slot, fields)` で、1 リクエストの
欄を `StateManager.lock` の中でゲームのカタログの 1 つの snapshot と国の 1 回の参照で解決し、1 つでも断れば何も書かない
（キャラに付いた `game` が読み込み中のゲームの codename と違うときも。S4i）。値は選手カードと同じ組み立て（`PlayerService.BuildCountryValue` /
`BuildCharacterValue`）。Start.gg の取り込みのキャラは、従来どおりキャラの config のコピーに、パネルの値に
しかないキー（`assets` ほか）を後ろに足したもの（`ImportedCharacterValue`。既存のキーと値は変えない）。
state の値からパネルが示す選択への逆引きは `Top8Service.CharacterPick`（codename を優先。S4h より前の
3 つの形も読む）。ページは同じ規則を `web/src/Top8Page/characterPick.ts` に持ち、両者を 1 つの表
（`characterPick.cases.json`）で検査する（S4i）。反映 `ApplyScores` は渡された試合だけを更新するマージで、スコアは 0〜99 の int 2 つの
list だけを受け付ける（それ以外は何も書かずに None）。選択中のデザインは `settings.json` の
`top8.selected_design`（`LayoutCatalog.SELECTION_SETTINGS`。`GET|PUT /api/v1/layouts/selected/top8`）。

```
top8
├── phaseLabel                       W R   (表示ラベル。既定 "TOP 8")
├── players.{1..8}                   W R   (スロット位置固定: 1/2=WSF1, 3/4=WSF2,
│   │                                       5/6=LR1A, 7/8=LR1B)
│   ├── name / sponsor               W R
│   ├── country                      W R   (国の行: code / name / en_name / display_name / asset /
│   │                                       emoji / 座標。カスタム国旗も code で。パネルの値は選手
│   │                                       カードの country と同じ形・キー順。取り込みは
│   │                                       CountryCatalog.BasicCountryInfo の順)
│   └── characters.1                 W R   (パネルの選択: 選手カードのキャラ 1 枠と同じ形 name /
│                                           en_name / display_name / codename / modded / assets /
│                                           skin（スキンの行）/ variant（常に {}）。取り込み: キャラの
│                                           config のコピー（export_name / smashgg_name / locale /
│                                           skin_name ほか）の後ろに modded / name / assets / skin /
│                                           variant を足したもの。別キャラのスキンでは en_name が
│                                           違う（パネル: スキンのキャラ、取り込み: config のキャラ）。
│                                           オーバーレイは assets でアイコンを描く。別のゲームへの
│                                           切り替えで {} になる。S4h より前の値（スキンの行 {assets} /
│                                           キャラの行 / 取り込みの設定のコピー）が残っていることがある)
└── matches.{wsf1|wsf2|wf|lr1a|lr1b|lqf1|lqf2|lsf|lf|gf|gf_reset}
    ├── name                         W R   (ラウンド表示名)
    ├── slots                        W R   ([int,int] 選手スロット 1..8 / 0=TBD。
    │                                       core/Top8Model.Recompute が導出)
    ├── score                        W R
    ├── finished                     W R
    └── active                       W R   (gf_reset のみ意味を持つ)
```

##### `commentary` — 実況・解説

実況・解説機能の全面リワーク（2026-07）でスキーマ刷新。スロット固定 12 名・
欠番概念なし（旧「Unset 時に詰める」運用は廃止）。正規化・表示選択ルールの
正本は `src/core/CommentaryModel.py`。旧スキーマ（seed / country / character /
mains / custom_textbox 等）は `NormalizeState` でマイグレーションされる
（custom_textbox → note、その他は破棄）。呼び出しは保存済み state の v0 → v1 移行
（`core/StateMigrations`。§3.3.5）と、起動ごとの `Window._SeedPanelState()`（シードを兼ねる。
パネルが lazy 構築のため。§4.1.1）。状態は再起動をまたいで永続化される（`user_data/state.json`）。

```
commentary
└── {1..12}                          W R   (スロット固定・常時 12 エントリ)
    ├── name                         W R   (ゲーマータグ)
    ├── team                         W R   (チーム / 所属)
    ├── twitter                      W R   (@ なし)
    ├── role                         W R   (str 自由入力。補完候補は ROLES の 6 種:
    │                                       "実況"|"解説"|"選手"|"主催"|"MC"|"スタッフ")
    ├── icon                         W R   (str。""|"mic"|"megaphone"|"phone"。アイコン
    │                                       プルダウンで選択、オーバーレイで SVG バッジ表示。
    │                                       旧 role/roles のアイコンラベルは起動時に移行)
    ├── note                         W R   (自由コメント。例: 本年度ランキング2位)
    └── display                      W R   (int。0=非表示、1=表示。表示順はスロット順
                                            （パネルのカード順。≡ ドラッグで並び替え、
                                            ドロップでデータがスロット間を移動）。最大 4 名。
                                            旧チェック順採番値(1..4)は起動時に 1 へ移行)
```

##### `tournamentInfo`

```
tournamentInfo
├── tournamentName
├── themeColor / accentColor       ※**大会共通カラー**（大会情報ページ「大会カラー」カードで編集。`PATCH /api/v1/colors/tournament`）。
│                                    既存テンプレ互換のため StateManager に展開する表示用キャッシュで、
│                                    永続化の正本ではない（正本は SettingsManager.general.theme_color / accent_color）。
│                                    機能別に上書きしたい場合は下記 `colors.{feature}` を使う
│                                    （キー名は後方互換のため変更しない）。
├── displayTournamentName          ※編集 UI は SCORE タブ（と大会名の追従）。下記注記参照。
├── eventName / address / numEntrants / startAt
│                                  ※大会情報ページ（React `/web/event`）の入力欄。書き手は
│                                    `core/TournamentInfoService.py` のみ（入力・provider 両方向）。
│                                    ページは `PATCH /api/v1/tournament/info` で書く。その field 名は
│                                    `StateSchema.TOURNAMENT_INPUT_KEYS`（3 つの文字列・`numEntrants`・
│                                    `startAtDate`。ADR-005 S4g）。
│                                    `startAt` は短縮日付文字列（ロケール依存の整形済み）
├── startAtDate                    ※`startAt` の日（`YYYY-MM-DD`、PC のタイムゾーン。null と一緒に null）。
│                                    日付の入力欄（`<input type="date">`）はこれを描き、
│                                    `TournamentInfoService.SetDate` で書く
│                                    （その日の 0 時の unix 秒への変換は core。ADR-005 S4f）
├── endAt / eventStartAt / eventEndAt
│                                  ※タブに入力欄を持たない provider 由来の日時。短縮日付文字列で公開
├── logoVersion                    ※`./layout/logo.png` の mtime（OBS のキャッシュバスト用）
├── bannerVersion                  ※`./user_data/tournament_banner.png` の mtime（0 = 未登録。既定のバナーを使う）
├── media                          大会アイコン / バナーの取得の状態（起動のたびに idle へ戻る）
│   ├── status                     idle | running | done | error
│   ├── error                      error の理由（`no_media` / `fetch_failed` / `save_failed` /
│   │                                `superseded` / `cancelled`）
│   └── revision                   取得を始めるたびに 1 増える（予約された次の取得も 1 回と数える）
├── url                            最後に設定した大会の URL（`TOURNAMENT_URL` の写し。無ければ ""）
├── load                           大会の設定 / 解除の進み具合（`media` と同じ形。ADR-005 S4g レビュー M1）
│   ├── status                     idle | resolving | loading | loaded | error（起動のたびに idle へ戻る）
│   ├── error                      error の理由（`unresolved` / `load_failed`）
│   └── revision                   設定 / 解除のたびに 1 増える（進み具合では増えない。再起動でも戻らない）
└── {provider 由来}                (`shortLink` 等。ペイロードの残りをそのまま透過)
```

大会アイコン / バナーのファイル（`./layout/logo.png` は長辺 512、`./user_data/tournament_banner.png`
は長辺 1920 の PNG）と `logoVersion` / `bannerVersion` / `media` の書き手は
`core/TournamentMediaService.py`（取り込み・取得のジョブ・既定に戻す。ADR-005 S4f）。画像の検証・縮小・
原子的な書き込みは配信団体素材と共通の `core/ImageImport.py`。`bannerVersion` は state なので、React のページ（サムネイル /
リザルトのプレビュー）はそれを見て描き直す。大会の設定・解除（`TOURNAMENT_URL`）は
どの入口からでも `core/TournamentService.py` を通り、解除はゲーム選択も忘れる。`url` / `load` の書き手も
`TournamentService`（設定 / 解除の受け付け時に、要求したスレッドで publish する。`POST` / `DELETE /api/v1/tournament` の
202 はその後。起動時のシードは `StateSchema.SeedPanelState()`）。

##### `colors` — 機能別カラーの上書き

```
colors
└── {feature}                      board / thumbnail / result / top8 / commentary / stats
    ├── themeColor                 ※表示用キャッシュ（正本は SettingsManager.colors.{feature}.theme_color）
    └── accentColor                ※同上（正本は SettingsManager.colors.{feature}.accent_color）
```

- 各機能のページの「〜の色」カードで編集する（共通実装は Web の `FeatureColorCard`（`web/src/CommentaryPage/SideCards.tsx`）と `src/core/ColorService.py`）
- **明示設定がある feature のみ**が state に現れる（`Window._SeedPanelState()` → `SeedColorState()`）。
  未設定の feature はノードごと存在しないので、レイアウト JS 側の
  `colors.{feature}` → `tournamentInfo` フォールバックが自然に効く（§5.5.3）

##### `stream` — 配信単位の設定（配信設定ページ）

```
stream
├── url                            配信先 URL（X 投稿・リザルトが参照）
├── title                          配信タイトル
├── titleAuto                      自動生成が最後に作ったタイトル（`title` がこれか空なら自動のタイトル）
├── orgIconVersion                 layout/stream_org_icon.png の mtime（0 = 未登録）
├── orgThumbVersion                layout/stream_org_thumbnail.png の mtime（0 = 未登録）
└── thumbnail                      配信サムネ生成の状態（起動のたびに idle へ戻る）
    ├── status                     idle | running | done | error
    ├── path                       最後に生成できた画像（`out/stream/thumbnail_*.jpg`。error でも前回のまま）
    ├── error                      error の理由（`no_org_thumbnail` / `no_banner` / 例外の文言）
    └── revision                   生成を受け付けるたびに 1 増える
```

書き手はすべて core の `src/core/StreamService.py`（ADR-005 S4d。`url` / `title` は
`AutonaActions.SetStreamUrl` / `SetStreamTitle` 経由）。`title` は大会名・ゲーム略称（state の
observer）と上位の参加者（provider が `StreamService.SetTopEntrants` で渡す）から自動生成し、
生成のたびに `titleAuto` に publish する。`title` は、それが前回の自動値（`titleAuto`）か空のときだけ
上書きする（`TitleIsAuto`。手入力を守る）。`titleAuto` は state と一緒に永続化されるので、手入力の
タイトルは再起動しても残る（`titleAuto` の無い S4d 前の state では、`title` を自動の値として扱う）。
クライアントの入力欄は、打ちかけを送る前に `TypedTitleWins` で送るかを決める（打っている間に
届いた自動のタイトルには打ちかけが勝ち、別の書き手のタイトルには負ける。React の配信設定ページは同じ規則の
TypeScript 版 `web/src/StreamPage/titleRule.ts` を使い、両者は同じ表 `titleRule.cases.json` で照合する。
ADR-005 S4e）。ページが開かれていなくても・ヘッドレスでも動く。

**`thumbnail.*`（試合サムネイル。ADR-005 S4o）**

```
thumbnail
└── render                         サムネイル描画の状態（起動のたびに idle へ戻る。revision は続く）
    ├── status                     idle | running | done | error
    ├── path                       最後に生成できた PNG（`out/thumbnails/*.png`。プレビューと error では前回のまま）
    ├── error                      error の理由（`no_game` / `no_player` / `payload_failed` / `render_failed` / `save_failed` / `cancelled`）
    └── revision                   run ごとに 1 増える（`POST /thumbnail:preview` / `:generate` の 202 の `renderRevision`）
```

書き手は core の `src/core/ThumbnailService.py` だけ。`stream.thumbnail` と同じ形で、SCORE の同期の生成と legacy の
`get-thumbnail` は通らない（どちらも state を書かず、legacy は今までどおり画像のバイトを返す。内部では保存した PNG のパスを
`send_file` に渡す）。その 2 つ（と X 投稿のクリップボードの画像）は
ADR-005 S4y から `ThumbnailService.DrawBoard(board, draw)` を同期で呼ぶ（描き手はデスクトップのオフスクリーンの
`AutonaShellOperations.DrawThumbnailNow`。ボードのウィジェットを通らず、legacy はボードを構築しない）。
v1 の `POST /thumbnail:generate` は body の `board`（1 / 2。省略はボード 1）のボードを描き、そのボードの選手の名前で保存する
（ADR-005 S4z。React の SCORE ページの生成ボタン用。preview はサムネイルのページの絵なのでボード 1 のまま）。
プレビュー画像（`GET /thumbnail/preview`）はボード 1 の絵だけで、どの経路でも別のボードの絵で preview に答えない（S4z レビュー）:
ボード 1 の generate はプレビュー画像も書き、予約の preview を兼ねる（今までどおり）。ボード 2 の generate はプレビュー画像を
書かず、予約の preview を兼ねない（予約があれば generate の後に続けてボード 1 の preview を描く。`RenderLane._GenerateDrawsPreview`）。

**`result_players` / `result_teams` / `result_render` / `result_standings` / `result_layout`（大会リザルト。ADR-005 S4q / S4r）**

```
result_players
└── {1..16}                        単独のレイアウトの行
    ├── rank / name / sponsor / twitter
    ├── character                  1 体目（`Top8Service.CharacterValue` の形。空は {}）
    └── characters                 欄の並び（最大 3。空の欄は {} で位置を保つ。末尾の空は持たない）
result_teams
└── {1..8}                         団体のレイアウトの行
    ├── rank / name
    └── members                    最大 5: {name, twitter, character}
result_render                      描画の状態（thumbnail.render と同じ形）
├── status                         idle | running | done | error
├── path                           最後に作成できた PNG（`out/results/*.png`）
├── error                          no_data / payload_failed / render_failed / save_failed / cancelled
└── revision                       run ごとに 1 増える（`POST /result:preview` / `:generate` の 202 の `renderRevision`）
result_standings                   standings の取り込みの状態
├── status                         idle | running | done | error
├── error                          fetch_failed / no_standings / changed / import_failed
└── revision                       取り込みごとに 1 増える（`POST /result:loadStandings` の 202 の `standingsRevision`）
result_layout                      今のレイアウトの写し（文字列。正本は settings.json の result_config.layout。選ばれるか対戦形式に追従するまで ""）
```

行の正本は state で、`state.json` に残る（「すべてクリア」= `POST /result:clear` まで消えない）。書き手は core の
`src/core/ResultService.py` だけ（ADR-005 S4r で Qt パネルと `AutonaActions.PublishResultPlayers` を削除した）。パネルが
`result_players` に残した団体の行（`members` を持つ行）は、起動のたびの `StateSchema.SeedPanelState()` が消す。`result_render` と
`result_standings` は起動のたびに idle へ戻る（revision は続く）。行はシードしない（無い行は既定値で読む）。
`result_layout` はページがレイアウトの変化を知るための写しで、起動のたびに `""` へ戻り、`ResultService.SetLayout` /
`FollowBattleFormat` が書く（サイドバーの対戦形式の切り替えはページの操作ではなく、レイアウトが保存されていなければ設定にも書かれない）。
保存された別のゲームのキャラ（再起動をまたいでゲームが変わった場合など）は state に残り、`ResultService` の読みで無いものとして
扱う（描かない・スコアボードからの補完が効く・ページにも空で返す）。ゲームの切り替えで消す `ForgetCharacters` は今までどおり。

> **データ階層を混ぜないこと**: `tournamentInfo.*`（大会: 名前・日付・会場・大会アイコン/バナー）、
> `stream.*`（配信: 配信先・配信タイトル・配信団体素材）、`score.{N}.*`（試合: 選手・スコア・
> ラウンド）はそれぞれ別のライフサイクルを持つ。配信先は「大会ごとに変わるとは限らない配信
> チャンネル」なので `stream.*`。
>
> **`score.{N}` に置いてはいけない理由**: `score.{N}` は 反映（held subtree, §3.2.x）で
> ゲートされる。過去に配信先を `score.{N}.stream_url` に置いた結果、(1) 「反映」を押すまで
> Web UI / OBS に届かない、(2) 全ボードへミラーすると非表示側のボードまで dirty になる、
> (3) 起動時の score サブツリー wipe で消える、が同時に発生した。`stream.*` /
> `tournamentInfo.*` は非ゲート・ボード非依存・永続。
>
> `displayTournamentName` が `tournamentInfo` にあるのも同じ理由（編集 UI は SCORE タブだが
> 大会単位のデータ）。
>
> `orgIconVersion` / `orgThumbVersion` は `tournamentInfo.logoVersion` と同じキャッシュバスト
> 用 mtime。素材を読むオーバーレイが `/layout/stream_org_icon.png?v={version}` の形で参照する
> ための契約としてキーを残している（2026-10-31 時点で、同梱のオーバーレイにこれを読むものは無い）。

##### `analysis` — この大会の試合一覧の取得（ADR-005 S4j）

```
analysis                           最新の取得の進み具合（1 つの dict をまとめて書く。起動のたびに idle へ戻る）
├── status                         idle | running | done | error
├── error                          error の理由（`no_phases` = 大会に phase group が無い /
│                                    `fetch_failed` = フェーズの要求が失敗した・どの group の要求も失敗した。
│                                    要求が通って sets が空の group は「答えた」と数える）。それ以外は ""
├── done / total                   取得し終えた phase group の数 / 全数（フェーズの取得中は total 0）
├── failed                         行を作れなかった group の数（答え無し・sets 無し・ブラケットを組めない）
└── revision                       取得を受け付けるたび・結果を捨てるたびに 1 増える（再起動でも戻らない）
```

書き手は core の `src/core/AnalysisService.py` だけ。**行そのものは state に入れない**（大会規模で数百〜千超。
delta・`program_state.json`・`out/` が膨らむ）: サービスのメモリに、取得した大会の URL と組で持ち、
`AnalysisService.Result()` / `Rows()` で読む（`ResultRevision()` は行を写さずにどの行かだけを返す）。provider には
strict で頼む（`StartGGDataProvider.GetTournamentPhases` / `GetAnalysisPhaseGroup` の `strict=True`。要求の失敗を
空の答えにせず例外にする）。行は表示文字列ではなく構造（`round` と並べ替えキー `roundOrder`・
`p1` / `p2` の `{seed, name}`・`score`（未完了は null）・`upset` / `losersRun` / `seedOver`）で、
「TBD」「(seed N)」「-」は表示側の文言。大会を読み込み直す・解除すると、provider のアダプタ
（`LoadTournament` / `UnloadTournament`）が provider を差し替えた直後に `Forget()` を直接呼び、取得の残りと
行を捨てて idle にする（差し替えの前に捨てると、その間に別スレッドから来た取得が旧大会の行を新大会のものにしうる）。
起動時のシード（`StateSchema.SeedPanelState()`）は idle で、revision は保存された値を引き継ぐ。
外からは v1 の `POST /api/v1/analysis:refresh`（取得の開始。202 / 実行中は 409 `CONFLICT` / 大会なしは 409 `NOT_READY`）と
`GET /api/v1/analysis`（`{status, url, rows}`。今読み込んでいる大会の行だけ。ADR-005 S4k）で使う。統計情報ページの
「この大会の試合一覧」タブが読み手。

##### `set_selection` — セット選択の一覧の取得（ADR-005 S4v）

```
set_selection                      リストごとの進み具合（起動のたびに idle へ戻る）
├── phases                         大会のフェーズ
├── sets                           セットの一覧
└── stations                       配信台と配信
    （それぞれ 1 つの dict をまとめて書く）
    ├── status                     idle | running | done | error
    ├── error                      error の理由（`fetch_failed` のみ）。それ以外は ""
    ├── done / total               取得し終えたページ / 全ページ（`sets` のみ。total 0 はまだ分からない）
    ├── failed                     done の答えから欠けているもの（取れなかったページと読めなかったセットの数）
    └── revision                   取得を受け付けるたび・止めるたび・大会を変えるたびに 1 増える（再起動でも戻らない）
```

書き手は core の `src/core/SetSelectionService.py` だけ。**行そのものは state に入れない**（`analysis` と同じ理由）:
サービスのメモリに、取得した大会の URL と組で持ち、`SetSelectionService.Result(kind)` で読む。provider には strict で頼む
（`GetTournamentPhases` / `GetMatches` / `GetStations` の `strict=True`）ので、`error` は取得の失敗、行が空の `done` は
「一覧に何も無い」。行は表示文字列ではなく決まったキーだけの構造で、実名・SNS・キャラを含まない（`SetRow` ほかのホワイトリスト。
「 (A, B)」などの文言は表示側）。大会を読み込み直す・解除すると、provider のアダプタが provider を差し替えた直後に
`Forget()` を直接呼び、走っている取得を捨てて全リストを次の revision の idle にする。`phases` / `stations` の行は捨て、
`sets` の行は URL と組で残す（同じ大会を読み直すと戻る。セット選択ウィンドウのキャッシュと同じ）。
起動時のシード（`StateSchema.SeedPanelState()`）は idle で、revision は保存された値を引き継ぐ。
外からは v1 の `POST /api/v1/tournament/{phases,stations,sets}:refresh`（取得の開始。202 / 実行中は 409 `CONFLICT` /
大会なしは 409 `NOT_READY`）・`POST /api/v1/tournament/sets:cancel`・`GET /api/v1/tournament/{phases,sets,stations}` で使う
（`.claude/design/external-control-api.md` §5.18・§7.3）。Qt のセット選択・配信台ウィンドウはこの state を読まず、
provider のアダプタと窓自身のキャッシュを通る（一覧は共有しない: 窓で取得した一覧はダイアログに出ず、逆も出ない）。React の SCORE ページのセット選択・配信台のダイアログ（ADR-005 S4ab。`web/src/ScorePage/SetSelection.tsx`）は
この state を読む: 進み具合（`done` / `total`。total 0 は不定のバー）と状態の文言を描き、リストの `revision` / `status` が
変わったとき（running へ変わったときを除く）とダイアログを開いたときに GET を読み直す。行の文字列の規則（ダブルス・完了したペア）は
`web/src/ScorePage/setRows.ts` で、`setRows.cases.json` を jest と `tests/test_set_selection.py`（core と 2 つの窓）が読む。

##### `ui` — UI 表示用キャッシュ

旧 `ui.light_mode` キャッシュはテーマ切替機能の削除に伴い廃止（§5.4、ダーク固定）。
現在 `ui` 配下に broadcast 用キーは無い。

##### `notes` / `layout_options` / `provider_videogame` / `completed_sets` / `streamQueue` / `currentStream` / `result_players`

> `layout_options` は書き手も読み手も無い（オーバーレイにも Web UI にも読み手が無く、ADR-005 S4s で
> `SetLayoutOption` と `LayoutOptionsManager` を削除）。古い `state.json` に残っていれば残ったままで、
> `KEY_CATALOG` の名前も消していない（移行 step を足すほどの実害が無いため）。
>
> 旧 `player_list.*`（PLAYERS 順位ロスター編集）は機能ごと廃止（layout/top_8 と共に削除、
> Top-8 は `top8.*` → `layout/bracket_top8/` に一本化）。残留キー（`player_list` /
> `player_editor`）は起動時に `Window._SeedPanelState()` が Unset する。
>
> 旧 `team_battle.*`（チームバトル編集）は `AutonaTeamBattleWidget` / `AutonaTeamPlayerWidget`
> の削除（2026-08-28）に伴い廃止。クルーバトル（board 3）は別キー `score.3.*` に統合済みで
> `team_battle.*` を書き込むコードは存在しない（player_list と異なり自動 Unset 処理はなく、
> 既存 `program_state.json` に残留する場合がある）。

キー定義の正本は `src/core/StateSchema.py`（`KEY_CATALOG`）。

#### 3.1.3 動的キーパスの規約

かつては以下の 4 族が Qt の `objectName()` から動的に決まっていた。Headless Core
Step 5（T-19）で順に静的化し、**全族が完了**（`StateSchema.DYNAMIC_KEY_FAMILIES`
は空になった）:

| 族 | 状態 |
|----|------|
| `score.{N}.team.{T}.{QCheckBox/QLineEdit.objectName}` | ✅ 静的化（`StateSchema.SCORE_TEAM_INPUT_KEYS` = `teamName` / `losers`。書き手は `AutonaActions`） |
| `score.{N}.{QComboBox.objectName}` | ✅ 静的化（`StateSchema.SCORE_MATCH_FIELDS` = `phase` / `pool` / `match`。書き手は `AutonaActions`） |
| `score.{N}.team.{T}.player.{M}.{objectName}` | ✅ 静的化（`StateSchema.PLAYER_INPUT_KEYS`。書き手は `core/PlayerService.py`） |
| `tournamentInfo.{Q*.objectName}` | ✅ 静的化（`StateSchema.TOURNAMENT_{TEXT,NUMBER,DATE}_FIELDS` = `tournamentName` / `eventName` / `address` / `numEntrants` / `startAt`。書き手は `core/TournamentInfoService.py`。Qt の EVENT タブは ADR-005 S4g で React のページになり、`.ui` は削除済み） |

**規約**:
- **キー名は静的化後も不変**（`.ui` の `objectName` を変更するとキーパスが変わり、保存済み state と互換性が壊れる。静的化済みの族でも `StateSchema` の文字列と `objectName` が一致していなければ `findChild` が解決できず同じ結果になる）。やむを得ず変える場合は state のマイグレーション層が前提（known-smells S-14）。
- **新規追加は §3.1.2 に静的キーパスとして明記し、`StateSchema` に定数を置く**。動的キーパスの新規追加はしない。

### 3.2 同期経路の規約

#### 3.2.1 全体図

```
┌────────────────────────┐
│ ユーザー操作            │
│  - PyQt UI             │
│  - React Web UI        │
│  - REST/Socket from OBS│ (運用上は read-only)
└────────────┬───────────┘
             │
             ▼
┌─────────────────────────────────┐
│ AutonaActions (src/core, Qt-free)│
│  移行済み操作の唯一の書き手      │
│  (R-Sync-1b)                    │
│ AutonaWebOperations             │
│  Web/外部の唯一の入口            │
│  （両シェル共通。S3e-9）         │
│  シェルの違いは登録だけ:         │
│  ボードホスト・ゲームのローダー・│
│  シェル操作2本（ヘッドレス503）  │
└────────────┬────────────────────┘
             │ StateManager.Set / Unset / SaveBlock
             ▼
┌─────────────────────────────────┐
│ StateManager (src/core, Qt-free)│
│ - DeepDiff で delta 算出        │
│ - revision を単調増加で付与      │
│ - adaptive debounce (50/100/200ms)│
│ - 通知は StateManager.observers │
│   （素の callback、Qt 非依存）   │
└──┬───────────┬───────────┬──────┘
   │           │           │
   │ delta     │ snapshot  │ observer 通知
   ▼           ▼           ▼
┌──────────┐ ┌────────────┐ ┌───────────────────────┐
│ Socket.IO│ │./out/program│ │ StateManagerBridge     │
│ broadcast│ │_state.json  │ │ (Qt adapter, 同名シグナル│
│          │ │(degraded用) │ │  4本を re-emit)         │
└──┬───────┘ └─────┬──────┘ └──────────┬─────────────┘
   │               │                   │
   ▼               ▼                   ▼
OBS HTML / React Web UI / WebEngine 画像生成      PyQt UI
(Socket.IO + revision 検証)                      (Bridge 経由で接続)
```

**ヘッドレス起動**（`python main.py --headless`、ADR-005 S3c。`src/headless.py`）では、右下の PyQt UI が無い。
QCoreApplication の上で `bootstrap_core()` までを組み、大会データの適用・ゲームのロード（`GameAssetCatalog` 直。
Qt モデルなし）・Web サーバーを動かす。デスクトップと共通の結線は `bootstrap.wire_tournament_data` /
`wire_web_server`、Web 操作のボード周りの違いは `AutonaWebOperations.boardHost`（デスクトップは
`DesktopBoardHost`、ヘッドレスは既定の `BoardHost`）が吸収する。ボードが無いので held にならず、
書き込みは即時公開される。503 `UNAVAILABLE` を返すのは、デスクトップの画面が要るシェル操作（セット選択を
開く `open_sets`（React の SCORE ではページのダイアログ、退路では Qt の窓。S4ab-2）・サムネイル `get_thumbnail`・v1 の `POST /thumbnail:preview` / `:generate`。デスクトップは `bootstrap_gui()` が
`AutonaWebOperations.openSetSelection` / `renderThumbnail` / `startImageRender` に、シェルのアダプタ
`AutonaShellOperations`（S4y でスコアボード dock から移した）の同名の操作を登録する）だけで、ほかの Web 操作は
すべて動く（ADR-005 S3e-9・S4o）。

#### 3.2.2 書き込み規約

| ルール | 内容 |
|------|------|
| **R-Sync-1** | 書き込み経路の規約: <br>① React Web UI / 外部システムからの書き込みは **必ず Web 操作経由**（`AutonaWebOperations`。ADR-005 S3c。ボード依存の `WebServerActions` は S3e-9 で削除）。<br>② OBS HTML は原則 **read-only**。例外的に書き込みを許可する場合も Web 操作経由とする。<br>③ **PyQt Widget は `StateManager.Set` / `Unset` を直接呼ばない**（2026-09-03、headless core Step 5 完了で暫定許可を撤廃）。書き込みは `AutonaActions` か core のドメインサービスを通す。`tests/test_objectname_keypath_contract.py` の書き手台帳テストが `src/core` 外の書き手集合を pin する。<br>④ 新規の複合操作・正規化を伴う操作は Actions 層へ実装する。<br>⑤ PyQt のドメイン状態書き込みは `src/core/Actions.py` の **`AutonaActions`** と core のドメインサービスへ**移行済み**（ADR-004 D2 / [headless-core-redesign.md](../../docs/changes/headless-core-redesign.md) の「Step 5 完了時点の書き手台帳」）。`src/core` 外に残る書き手は `AutonaTournamentDataProvider`（`provider_videogame`）の 1 つだけで、Qt ウィジェットではない provider 層（`game.*` は 2026-10-23 の ADR-005 S1a で `src/core/GameAssetCatalog.py` へ移動）。<br>⑥ state 変更通知の Qt シグナルは `StateManagerBridge.instance` が提供する。core（`src/core/`）に Qt シグナルを追加しない。 |
| **R-Sync-1b** | **Actions へ移行済みの操作**: スコア増減 / スコアリセット / チーム色 / `display_options` トグル / 反映（`CommitHeld`）（Step 3）、match バー 3 combo / チーム名 / LOSERS / `teamsSwapped` / `best_of`（Step 5 第 1 弾）、チームの選手スロット数（第 2 弾）、レイアウトオプション / 配信 URL・タイトル（第 5 弾。同じ弾のリザルト順位は ADR-005 S4r で `core/ResultService` へ、実況スロットは ADR-005 S3e-2 で `core/CommentaryService` へ移った。下記）、**チームスワップ（`SwapTeams`）・選手カードの上下移動（`SwapPlayers`）**（ADR-005 S2c。選手スロット・スコア・LOSERS・チーム名・ロゴ・ステージ記録（鏡映）を 1 保存サイクルで入れ替え、`teamsSwapped` を反転。チームカラーは入れ替えない。正本は state で、ボード属性は廃止）、**クリア（`ClearScore` / `ResetMatch` / `ClearPlayers` / `ClearBoard`）**（ADR-005 S2d。スコア・LOSERS・match バー・選手スロット・チーム名をそれぞれ 1 保存サイクルで空にする。旧実装はウィジェットを空に戻し、カード 1 枚ごとに 1 サイクルだった）、**セットの長さ（`SetBestOf` / ゲームのロードの `RepublishBestOfs`）**（ADR-005 S2e。`best_of` の 6 キーとステージ行を 1 保存サイクルで書く。行は `AutonaActions` が持つボードごとのトラッカー（ADR-005 S4t で常時）に操作の中で作り直させ、BEST OF のスピンボックスと行は state を描画する。旧実装はスピンボックス主導だった）、**人数・キャラ数（`SetRoster` / ゲームのロードの `ApplyGameRoster`）**（ADR-005 S3e-6。選手スロットとキャラ枠の増減・1 人になるときのチーム名のクリアを 1 保存サイクルで書く。ボードは held サイクルの中でカードを state のスロットに合わせて作り / 退役させ、破棄はイベントループに戻ってから行う。旧実装は 2 つのスピンボックス（`playerNumber` / `charNumber`）とカードが書き、ヘッドレスでは変えられなかった）。**これらの操作の入口は `AutonaActions.instance` に一本化**され、Qt ウィジェット・REST v1・legacy REST / Socket.IO・グローバルホットキーはすべてそこを呼ぶ。ウィジェットは state 通知（`held_state_updated` / `state_updated`）を受けて表示だけを更新し、更新中は `QSignalBlocker` で囲んで書き戻さない（エコーループ抑止）。<br>**ステージ集計は core へ移行済み**（Step 4a）: `score.{n}.stages.*` の記録とステージ⇔スコアの相互整合は `src/core/GameTrackerService.py` が持ち、`AutonaIndividualGameTracker` は行の生成とボタン描画だけを行う（**state が記録・ボタンは表示**。行数は S2e から `AutonaActions.SetBestOf` が決め、ウィジェットはサービスの `rowsVersion` を見てボタンを作り直す）。トラッカーはボードの有無によらず `AutonaActions.GameTracker(board)` にあり、スコア → 行の追従はスコアを書いた操作が core で行う（`Scores()` + `FollowScores()`。ADR-005 S4t。`writingScore` のガードは不要になり削除した）。<br>**書き込みの実装はすべて core にある**（Step 5 完了）が、*入口* が Actions に一本化されていない操作は残る: クルーボードの操作（ストック・出場中のメンバー・人数・スワップ・リセット・クリア・`best_of`。チーム合計がメンバーストックの派生値なので Actions に載せず、ADR-005 S3e-2 からはボードの入力欄も Web も `CrewBattleService` の同じ操作を呼ぶ）/ **実況の 12 スロット**（`commentary.*`。ADR-005 S3e-2 から書き手は `core/CommentaryService` で、パネルの入力欄も Web の `set_commentary_data` / `load_commentator_from_tag` も同じ関数を呼び、パネルは state を描画するだけ）/ **選手スロットの読込・クリア・DB 保存**（`score.{n}.team.{t}.player.{m}.*`。ADR-005 S3e-3 から `core/PlayerService` の `ApplyData`（DB とのマージ・悪語フィルタ・国 / 地域 / キャラの解決まで）/ `ClearSlot` / `SavePlayerToDB` で、セットの entrants・Web の選手編集・タグからの読込・補完の選択・カードのクリアボタンが同じ操作を呼び、カードは state を描画するだけ。React の SCORE ページの欄の編集のために、カードの入力欄（文字の欄・国・地域）とキャラ行の 3 コンボと同じ書き込みを 1 回の lock と 1 保存サイクルで組む `Edit` / `EditCharacter` もある（v1 の `PATCH .../players/{p}/fields` / `.../characters/{row}`。ADR-005 S4z。Qt のカードはまだ欄ごとの操作を呼ぶ。同じ match バーの v1 は `PATCH /scoreboards/{n}/match` で、`SetMatchField` / `SetBestOf` を呼ぶ）/ **スコア欄への数値の手入力**（スピンボックスの `valueChanged` が v1 の `score:set` と同じ `AutonaActions.SetScore` を呼ぶ。ADR-005 S4x。それまでは `GameTrackerService.UpdateScore` を呼んでいた（削除済み）。`ClearScore` / `ResetAllInputs` は S2d で core 操作になった）。<br>**したがって `score.{n}.team.{t}.score` の書き手は core 内に 3 系統**（`AutonaActions`（`SetScore` / `AdjustScore` / `ResetScore`、S2c からの `SwapTeams`、S2d からのクリア）/ ステージ集計の `GameTrackerService` / セット読込の `SetLoadService`）で、「唯一の書き手」ではない。呼び出し口が Actions に一本化されていないためで、**core の外から直接 `Set` する経路は残っていない**。3 者は state 経由でのみ噛み合う。行の追従はスコアを書いた操作が行い（上記）、ステージ集計（`PublishStageResults`）は追従を呼ばないので、1 クリックで次のゲームまで勝ちになることは無い（ADR-005 S4t）。 |
| **R-Sync-2** | バッチ書き込みは `with StateManager.SaveBlock():` を使う。`BlockSaving()` / `ReleaseSaving()` の手動ペアは避ける（例外時の漏れリスク） |
| **R-Sync-3** | 同一トランザクション内の複数 `Set` は SaveBlock で囲み、delta を 1 回にまとめる |
| **R-Sync-4** | 競合時の優先順位: **後勝ち**（最後の書き込みで上書き）。同時操作の検出・拒否は行わない |

#### 3.2.3 配信規約（broadcast）

| ルール | 内容 |
|------|------|
| **R-Sync-5** | StateManager は変更ごとに **`revision`**（単調増加の整数）を付与する |
| **R-Sync-6** | `program_state_update` イベントには `{ revision, first_revision, delta }` を含める。サーバーの delta coalescing は複数の `state_updated` emit（= 複数 revision）を 1 メッセージへ結合するため、payload は連続区間 `[first_revision, revision]` を表す。`first_revision` 欠落時（旧 payload）はクライアントが `revision` 単一と見なす。同一 revision 内で複数 chunk に分けて送る必要がある場合は `{ revision, chunk_index, chunk_total, delta }` の形式で chunk 順序を保証する |
| **R-Sync-6a** | `delta` の行は受信順に 1 行ずつ適用できる（結合された revision の区切りはメッセージに無い）。リストは位置ごとに比べるので、行は各位置の値と末尾の増減だけで、型の変更も値を省かない（`SaveState` のオプションと `web/src/stateDelta.cases.json`。詳細は `.claude/rules/state-and-webserver.md`） |
| **R-Sync-7** | `program_state` イベント（full snapshot）には `{ revision, state }` を含める |
| **R-Sync-8** | クライアントは受信 payload の区間 `[first_revision, revision]` が次に期待する revision（前回適用 +1）を含むことを検証する。含まなければ `program_state` を再リクエストして full snapshot で再構築する。（旧規約の「単一 revision の前回 +1 検証」は coalescing と両立せず、結合 payload をギャップと誤検知して全量再取得が多発していた） |
| **R-Sync-9** | reconnect 時はクライアントが必ず full snapshot を再取得する |
| **R-Sync-10** | adaptive debounce: <10 delta = 50ms / 10-99 = 100ms / 100+ = 200ms + `state_big_change` 通知（core の observer 発火 → `StateManagerBridge` が同名 Qt シグナルを re-emit）。**leading-edge flush**: 直近 flush から 50ms 以上静かな状態で届いた最初の delta は即時送出する（単発操作の体感遅延をゼロ化。バーストは従来どおり trailing debounce） |
| **R-Sync-11** | **遅延反映（「反映」ボタン、2026-07 導入）**: `score.1` / `score.2`（1v1・チームボード）のサブツリーは `StateManager.HoldSubtree` で保留登録される。書き込みはライブ state に即適用（`Get()` は常にライブ値）だが、delta 配信・full snapshot（`GetFullState` 既定）・`program_state.json`・`./out` テキスト出力からは除外され、BEST OF 右の「反映」ボタン（`CommitHeld`）が保留分を 1 save サイクルで一括公開する。ゲーム（再）読込（`GameAssetManager.onLoad`）時は自動コミット。crew ボード（`score.3`）と `score` 以外のサブツリーは従来どおり即時配信。**除外サブキー（exempt、2026-07-27 追加）**: `HoldSubtree(prefix, exempt=[...])` で指定したサブパスは保留対象外となり、書き込み時点で即時公開される。現在の指定は `last_sets` / `history_sets` / `recent_sets` / `upset_factor`（`src/core/StateSchema.py` の `STATS_KEYS`）— 取得済みの派生データであり操作者の未確定編集ではないため、統計オーバーレイ（`layout/last_sets` 等）と統計プレビューが「反映」を待たずに更新される。保留中の編集がある状態でも exempt キーのライブ値は公開スナップショットに残る。サムネイル/リザルト生成は `GetFullState(include_pending=True)` でフォームのライブ値を描画する。デスクトップ内で保留中データを表示するウィジェットは `StateManagerBridge.instance.held_state_updated` シグナル（core 側は `StateManager.observers.held_state_updated` 通知が起点）で更新する |
| **R-Sync-12** | **値を運ばない通知**（state の外のものと未公開のものを React のページに知らせる合図）: `scoreboard_pending`（`{board, dirty}`。未反映の編集。ADR-005 S4u）・`scoreboard_follow`（`{board}`。追従。S4v）・`battle_format`（`{}`。対戦形式。S4z）。どれも `program_state_update` と同じ flusher が、同じ送信の delta の後にこの順で送る。クライアントは通知の後に始めた GET（`/scoreboards/{n}/pending`・`/follow`・`/battle-format`）の答えを使い、接続の直後は 1 回読む。規則の詳細は [external-control-api.md](external-control-api.md) §4・§5.15・§5.17・§5.21 と [.claude/rules/state-and-webserver.md](../rules/state-and-webserver.md) の Web サーバーの節 |

#### 3.2.4 同期遅延の上限

ユーザー操作から各 UI に反映されるまでの遅延上限:

| 経路 | 上限 |
|-----|------|
| PyQt → StateManager | < 1ms |
| StateManager → OBS HTML（Socket.IO） | 200ms（debounce 上限） |
| StateManager → OBS HTML（degraded poll） | 200ms（debounce）+ 64ms（poll 周期） |
| StateManager → React Web UI | 単発操作: **~0ms**（leading-edge flush + 到着駆動適用）/ バースト: 200ms（debounce 上限） |
| StateManager → WebEngine 画像生成 | 0ms（prepare_data 時の snapshot） |
| React → StateManager | **目標値 ~50ms**（API/Socket → AutonaWebOperations → AutonaActions → Set）。実値はネットワーク依存で、無線・低速 LAN では大きくなる。スコア±は楽観的更新（R-React-8）により**表示は 0ms** |

**注**: 旧実装の React 側 100ms 固定バッチは到着駆動化により廃止（§2.2 R-React-6、2026-07 Web UI 刷新 Phase 3）。

**注 2（遅延反映）**: 上表は「配信された変更」の伝搬遅延。`score.1` / `score.2` サブツリーへの編集は R-Sync-11 により「反映」ボタン押下まで配信自体が保留されるため、OBS / Web UI への反映はボタン押下が起点になる（押下後の遅延は上表どおり）。

#### 3.2.5 既知の不整合 → 規約上の扱い

Phase B-b で検出された問題は、本書を正本として以下のように扱う:

| 問題 | 規約上の扱い |
|-----|------------|
| `score.upset_factor`（番号なし）二重定義 | 番号なし版は **削除済**（§8 T-1 完了） |
| `commentary.{N}` インデックス欠番 | スロット固定 12 名化（2026-07 リワーク）で欠番概念ごと消滅（旧: Unset 時に詰める） |
| `StateManager.state` 直接参照 | `GetFullState()` に置き換え。直接参照は禁止（§3.1.1） |
| OBS HTML poll の順序保証なし | revision 検証で full snapshot 再取得に統一（R-Sync-8） |

### 3.3 設定永続化の規約

#### 3.3.1 設定の置き場所マトリクス

設定の種別ごとに **保存先を一意**に定める。

| 設定種別 | 保存先 | 形式 | 所有 | 編集経路 |
|---------|------|------|------|--------|
| 一般設定（言語、テーマ色、Web ポート、表示オプション、Bluesky アカウント、Twitch アカウント等） | `./user_data/settings.json` | JSON | `SettingsManager` | PyQt 設定ダイアログ |
| ホットキー（ユーザー上書き） | `./user_data/settings.json` の `hotkeys` キー | JSON | `AutonaHotkeys` | PyQt 設定ダイアログ |
| ホットキー（デフォルト定義） | `src/AutonaHotkeys.py` のクラス定数 | Python | `AutonaHotkeys` | コード変更のみ |
| ウィンドウ位置・サイズ | QSettings（Windows レジストリ） | バイナリ | `Window` | 自動保存 |
| サイドバー選択・折りたたみ | QSettings（`sidebarIndex_v{STATE_VERSION}` / `sidebarCollapsed_v{STATE_VERSION}`） | バイナリ | `Window` | 自動保存 |
| 選手 DB | `./user_data/local_players.json` | JSON 配列 | `core/PlayerDB`（Qt 側は `AutonaPlayerDB` が補完モデル化。ADR-005 S3e-1） | React の Player List（`/web/playerBrowser`。v1 `PUT /players`）+ 直接 JSON 編集 |
| 前回の内容（state。大会情報・配信設定・実況・Top 8・表示トグル等。`score` / `game` は起動時に破棄） | `./user_data/state.json`（`{"state_version": N, "state": {...}}`） | JSON | `core/StatePersistence`（版の移行は `core/StateMigrations`） | 自動保存（`StateManager.SaveState` の公開ごとに間引いて書く。最後の変更から 1 秒・最大 5 秒。終了時に書き切る。ADR-005 S3b） |
| ルールセット（キャッシュ） | `./user_data/rulesets.json` | JSON | tournament provider | API 取得時に自動更新 |
| ゲームアセット定義 | `./user_data/games/{game}/{asset_type}/config.json` | JSON | `core/GameAssetCatalog`（Qt 側は `AutonaGameAssetManager` がモデル化） | アセット追加時に手動編集 |
| 国・地域マスタ / カスタム国旗 | `./assets/countries+states.json`・`./assets/city_to_state.json`・`./user_data/additional_flag/*` | JSON / 画像 | `core/CountryCatalog`（Qt 側は `AutonaCountryHelper` が国モデル化） | 同梱データ（都市→地域表は `scripts/gen_city_to_state.py` で生成）。カスタム国旗は画像を置くだけ |
| サムネテンプレ定義 | `./assets/thumbnail_base/thumbnail_types/{id}.json` | JSON | （定義のみ） | テンプレ追加時に手動作成 |
| OBS テンプレのグローバル設定 | `./layout/settings.json` | JSON | OBS layout runtime | テンプレ提供者または運用者が編集 |
| OBS テンプレのローカル設定 | `./layout/{template}/settings.json` | JSON | OBS layout runtime | テンプレ提供者または運用者が編集 |

> `out/program_state.json` は永続化ではなく**出力**（OBS の file:// ポーリングと ASS の契約。場所・形式・`timestamp` / `revision`・書き込み順は不変）。S3b 以降、起動時に読むのは `user_data/state.json` が無い・使えない・それより新しいときの移行だけ（§3.3.5）。

#### 3.3.2 設定の正本（Single Source of Truth）

| カテゴリ | 正本 | キャッシュ可能な経路 |
|---------|-----|------------------|
| ユーザー設定全般 | `SettingsManager` (`./user_data/settings.json`) | StateManager に展開してよい（`tournamentInfo.themeColor` / `colors.{feature}.themeColor` 等） |
| ホットキー有効値 | `AutonaHotkeys.LoadUserHotkeys()` の結果（デフォルト + 上書きをマージ） | — |
| トーナメント URL（最後に開いた） | `SettingsManager.Get("TOURNAMENT_URL")` | メモリキャッシュ可 |
| ウィンドウ状態 | QSettings | — |

#### 3.3.3 マージ規則

##### ホットキー
```
有効なホットキー = AutonaHotkeys.keys（デフォルト） |> deep_clone |> update(SettingsManager.Get("hotkeys"))
```

##### OBS layout 設定
```
有効な settings = _.defaultsDeep(
    window.settings,                          // URL クエリパラメータ（最優先）
    fetch("./settings.json"),                 // テンプレローカル
    fetch("../settings.json")                 // グローバル（最低優先）
)
```

#### 3.3.4 設定キーの命名規約

`SettingsManager` のキーパスは以下のネームスペースに従う:

| プレフィックス | 用途 |
|--------------|------|
| `general.*` | アプリ全般（disable_thumbnail_widget, theme_color, accent_color, webserver_port 等）。`theme_color` / `accent_color` は**大会共通カラー** |
| `colors.{feature}.*` | 機能別カラーの上書き（`theme_color` / `accent_color`）。feature は board / thumbnail / result / top8 / commentary / stats。未設定なら大会共通カラーを継承（読み書き・シードは `src/core/ColorService.py`。カードは Web の `FeatureColorCard`） |
| `hotkeys.*` | ホットキー（hotkeys_enabled とアクションごとのキー） |
| `display_options.*` | 表示切替（プレイヤー欄のフィールド表示等） |
| `thumbnail_config.*` | サムネイル生成設定（template_id, background_source（default/banner/custom）, background_path（custom 時のユーザー画像）, use_sponsors 等）。読み書きは `src/core/ThumbnailService.py`（ADR-005 S4o。読みは既定値を書かない）。v1 で取り込んだ背景は `user_data/thumbnail_background_<ハッシュ>.*` |
| `result_config.*` | リザルト生成設定（layout, logo_path 等）。`layout` は `core/ResultService.py` が読み書きする（`PATCH /api/v1/result`）。`main_icon_path` / `background_path` は読むだけで、クライアントからは書けない。色は保持しない — `ResultService` が `colors.result` → 大会共通カラーで解決して payload の `theme_color` / `accent_color` を上書きする |
| `bsky_account.*` | Bluesky 連携 |
| `update.*` | アップデーター（skipped_version 等） |
| `designer.*` | Scoreboard Designer。`figma_pairing_code` は Figma プラグイン取込（`POST /api/designer/imports`）のペアリングコード（`XXXX-XXXX-XXXX`）。`AutonaDesignerService` が初回参照時に発行し、Designer の `Import` → `Figmaペアリングコード` で表示・再生成する。旧来の「取込は Origin `null` を許可するだけ」という穴はこれで解消（仕様・ロックの詳細は `docs/changes/layout-custom-unification.md` §7） |
| `{board,commentary,top8}.selected_design` | ページが選んでいるデザイン（レイアウトの id）。正本は `core/LayoutCatalog.SELECTION_SETTINGS`（`SelectedLayout` / `SelectLayout`。`GET|PUT /api/v1/layouts/selected/{kind}`。top8 は ADR-005 S4h から、board は S4s から。board の一覧は `scoreboard` と `designer` を合わせたもの） |
| `stream_channels` / `stream_thumbnail_options` | 配信設定（トップレベル）。チャンネル一覧 `[{name, url}]` と、配信サムネ生成のオプション `{source: banner\|org, style: center\|netflix, overlayOrgIcon: bool}`。読み書きは `src/core/StreamService.py`（後者は ADR-005 S4d で追加。前者に揃えてトップレベルに置いた） |
| その他トップレベル（`program_language`, `TOURNAMENT_URL` 等） | 個別 |

新規キーは原則ネームスペース付きで作成し、トップレベルは増やさない。

#### 3.3.5 STATE_VERSION とマイグレーション

- `sidebarIndex_v{N}` / `sidebarCollapsed_v{N}` のキー名は `Window.STATE_VERSION`（現在 22）に依存（`src/Autona.py`）。旧 `windowState_v{N}`（`QMainWindow.saveState`）はナビ刷新で不使用
- `STATE_VERSION` をインクリメントすると既存ユーザーのサイドバー選択状態が初期化される
- インクリメントが必要なケース:
  - パネル（ナビ項目）の追加・削除・並び順変更
  - `.ui` ファイルの `objectName` 変更（state 互換が壊れる時）
- 同 `STATE_VERSION` 内では既存 state との互換性を維持する
- **state のスキーマ版（`state_version`、ADR-005 S3b）は `Window.STATE_VERSION` とは別物**。`user_data/state.json` の封筒に入る版で、`src/core/StateMigrations.CURRENT_VERSION`（現在 2）が正本
  - 保存済み state のキー名・形を変えるときは、移行 step（前方一方向・出荷後は凍結）を足して `CURRENT_VERSION` を上げる。v0（S3b 前の `out/program_state.json`）→ v1 は `RETIRED_ROOTS` / `STALE_KEYS` / commentary の `NormalizeState`。v1 → v2 は `result_players` の団体の行（`members` を持つ行。枠 1〜8）を `result_teams` へ移す（ADR-005 S4q）
  - 版は state の木に入れない（Socket.IO の payload と `out/program_state.json` に漏れるため）
  - 移行 step は冪等（すでに移行先の形のデータを受けても壊さない）。`out/program_state.json` は版を持たないので常に v0 として移行されるが、中身は書いたビルドの形になっているため
  - 新しい版の `state.json` を古いビルドで開くと `state.json.v{N}.<日時>.bak` に退避し、`out/program_state.json` から移行して起動する。壊れた `state.json` は `state.json.corrupt-<日時>.bak` に退避して同じく移行する（どちらも上書きしない。退避できなければ触らず、その起動は保存しない）
  - `out/program_state.json` が `state.json` より新しい（mtime）ときもそちらから移行する（クラッシュで間引き中の変更を失わない・旧版を使った後の復帰）

---

## 4. 画面仕様

### 4.1 PyQt パネル一覧と責務

`Autona.py` の `Window`（QMainWindow、`STATE_VERSION = 22`）が以下のパネルを管理する。

**構築タイミング**: 起動時に構築されるのは `Scoreboard`（アクティブな `battle_format` の
board 1 面のみ。ADR-005 S4aa からは退路のときだけサイドバーのページで、React モードではページに載らない）だけで、**残りのパネルは初回表示時にファクトリで構築**される
（起動再設計 Step 4b + headless core Step 4b。§4.1.1）。`Tournament Info` も
2026-08-31 に遅延構築へ移行した — `tournamentInfo.*` の書き手が
`src/core/TournamentInfoService.py`（`bootstrap_core()` 生成）になり、パネル不在でも
大会データが state に入るようになったため。ADR-005 S4g で React のページになり、
先頭ページ（最後に EVENT を開いていた／新規インストールの起動）では共有ビューの
ホストが起動時に建つ。ビュー（QtWebEngine）の生成はホストの最初の描画の後
（`paintEvent` → `QTimer.singleShot(0)`）へ遅らせるので、ウィンドウが先に出る。
「デフォルト表示」列は QSettings に保存された最後の選択に依存し、初期値は先頭ページ。

| # | objectName | クラス | 主な責務 | 入力先 (Set) | 表示元 (Get) | デフォルト表示 |
|---|------------|-------|---------|------------|------------|--------------|
| 1 | `WebPanels`（共有ホスト。ナビの文言は `Tournament Info`、ナビのキーは `event`） | React の `EventPage`（`/web/event`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4g）。Qt 版（`AutonaTournamentInfoWidget` と `.ui`）は S4g で削除済み | 大会の設定・再取得・解除（確認つき）と状態の表示、大会名・イベント名・場所・参加人数・開催日の編集、ゲームの選択、大会カラー、大会アイコン / バナー（取り込み・大会から取得・既定に戻す）。**書き込みの正本は `src/core/TournamentInfoService.py`**（Start.gg ペイロード・表示大会名・日付）、アイコン / バナーは `src/core/TournamentMediaService.py`、大会の設定・解除は `src/core/TournamentService.py`（ADR-005 S4f）、カラーは `src/core/ColorService.py` で、ページは `/api/v1`（`tournament*`・`colors/tournament`・`game`）を呼んで state を描画するだけ | `tournamentInfo.*`（core 経由） | `tournamentInfo.*`（URL と状態の `url` / `load` を含む）、`game`、`GET /api/v1/games`（`game` の変化とページの再表示で読み直す） | ✓（最初に表示） |
| 1.5 | `WebPanels`（共有ホスト。ナビの文言は `Stream Settings`） | React の `StreamPage`（`/web/stream`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4e）。Qt 版（`AutonaStreamSettingsWidget`）は S4e で削除済み | 配信先・配信タイトル・配信団体アイコン / サムネイルの登録・配信サムネイル生成。**処理の正本は `src/core/StreamService.py`**（タイトルの自動生成・チャンネル CRUD・素材の取り込み・サムネ生成のジョブ。ADR-005 S4d）で、ページは `/api/v1/stream*` を呼んで state を描画するだけ（ファイル選択・ダイアログ・プレビュー・クリップボードはブラウザの部品）。`stream.url` / `title` の書き込みは `AutonaActions.SetStreamUrl` / `SetStreamTitle`（同値なら書かない） | `stream.*`（core 経由） | `stream.*` | — |
| 2 | `WebPanels`（共有ホスト。ナビの文言は `Thumbnail Settings`、ナビのキーは `thumbnail`） | React の `ThumbnailPage`（`/web/thumbnail`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4p）。Qt 版（`AutonaThumbnailSettingsWidget` と `.ui`）は S4p で削除済み | テンプレート・背景（既定 / 大会バナー / アップロードした画像）・表示する項目・今のゲームのキャラ画像の配置・機能別カラー・既定に戻す（確認つき）、実描画のプレビュー（表示中だけ・変更の 300 ms 後・表示に戻るたび）、サムネイルの作成（`out/thumbnails/`）と作成した画像のコピー、動画タイトル。**処理の正本は `src/core/ThumbnailService.py`**（ADR-005 S4o）で、ページは `/api/v1/thumbnail*`・`colors/thumbnail` を呼んで `thumbnail.render` を描画するだけ。SCORE の生成・X 投稿と legacy の get-thumbnail も同じ `RenderPayload()`（読み済みの設定）で描く | `thumbnail_config.*`（settings.json。core 経由） | `thumbnail.render`、ボード 1 の `score.1.*`、`tournamentInfo.*`、`game`、`colors.thumbnail.*`、`GET /api/v1/thumbnail` | ✓ |
| 3 | `WebPanels`（共有ホスト。ナビの文言は `Result Settings`、ナビのキーは `result`） | React の `ResultPage`（`/web/result`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4r）。Qt 版（`AutonaResultSettingsWidget` と `.ui`、`result/main_generate_result.py`、`result/ResultRenderer.py`）は S4r で削除済み | レイアウト選択（対戦形式に追従）、単独の行（順位・所属タグ・名前・X ハンドル・キャラとカラー 3 つ・スコアボードから補完の表示）と団体の行（順位・チーム名・メンバー 5 人）の編集、並べ替え（上下ボタン + ドラッグ）、大会結果の読み込み（確認つき）、すべてクリア（今の種類だけ・確認つき）、実描画のプレビュー（表示中だけ・変更の 300 ms 後・表示に戻るたび。サンプルの表示つき）、作成（`out/results/`）と作成した画像のコピー、1 クリックの X 投稿（作成 + クリップボード + 既定のブラウザで投稿画面）、選手を DB に保存、機能別カラー。**処理の正本は `src/core/ResultService.py`**（ADR-005 S4q）で、ページは `/api/v1/result*`・`colors/result` を呼んで state を描画するだけ | `result_players` / `result_teams`、`result_config.layout`（settings.json）（core 経由） | `GET /api/v1/result`・`GET /api/v1/result/post`、`result_render`、`result_standings`、`result_layout`、`score.*`（補完）、`tournamentInfo.*`、`game`、`colors.result.*` | ✓ |
| 4 | `WebPanels`（共有ホスト。ナビの文言は `Top 8 Display Settings`、ナビのキーは `top8`） | React の `Top8Page`（`/web/top8`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4i）。Qt 版（`AutonaTop8Widget`）は S4i で削除済み | Top 8 ダブルエリミの選手（名前・所属タグ・国・キャラとカラー）と表示ラベルの編集、試合スコア（「反映」まで画面の中だけで保つ・全リセットは確認つき）、Start.gg 読み込み、機能別カラー、デザイン選択・OBS 用 URL 案内・プレビュー。**書き込みの正本は `src/core/Top8Service.py`**（Start.gg の取り込みも。ADR-005 S3e-8）。国・キャラは識別子（code / en_name + スキンの行）で依頼し（ADR-005 S4h）、ページは `/api/v1`（`top8*`・`bracket:refresh`・`colors/top8`・`layouts/selected/top8`）を呼んで state を描画するだけ | `top8.*`（core 経由） | `top8.*`、`game`、`colors.top8` / `tournamentInfo.*`、`GET /api/v1/countries`、`GET /api/v1/characters`（`game` の変化とページの再表示で読み直す） | ✓ |
| 5 | `WebPanels`（共有ホスト。ナビの文言は `Scoreboard`、ナビのキーは `scoreboard`）。退路では `Scoreboard` | React の `ScorePage`（`/web/scoreboard`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4aa）。**退路**: 設定 `general.qt_scoreboard_panel`（一般タブのチェックボックス。既定オフ。項目を初めて開くときに読む = `Window._SettleWayBack`）が `true` のときだけ、Qt の `AutonaScoreboardManager` → `AutonaScoreboardWidget` / `AutonaCrewBattleWidget` の dock をこの項目のページにする（S4ac で Qt 版ごと削除予定）。dock とボードはどちらのモードでも起動時に建つ（held の登録が `EnsureBoard(panel=True)` にあるため。React モードでは dock はどのページにも載らず、Window の子として隠れている）。セット選択 / 配信台のウィンドウはシェルのアダプタ `AutonaShellOperations` の持ち物（ADR-005 S4y） | 形式のボード（board1=1v1、board2=team、board3=crew）のスコア入力・選手編集（欄・詳細・キャラ行・補完の読込・上下移動）・match バー・反映（held のボード）・X 投稿・ブラウザ版を開く。ボード 1 / 2 は大会があれば「大会の試合を選ぶ」でページのセット選択のダイアログ（フェーズ・取得・完了したセット / ペア・検索・並べ替え・進み具合。行を選ぶと `:load-set`）と、そこから配信台のダイアログ（`:follow`）を開き、追従中はタイマー行（自動更新のラベル・次の更新までの秒・キャンセル `:stop-follow`）を出す（ADR-005 S4ab。デスクトップに限らない）。**Ctrl+Shift+O と legacy の open-set（`/scoreboard<n>-open-set`・Socket.IO `open_set`）も、React モードではこのダイアログを開く**（ADR-005 S4ab-2）: シェルのアダプタ `AutonaShellOperations` が `Window.OpenScoreSets` を呼び、メインウィンドウを前に出して（最小化からも戻す）サイドバーを SCORE にし、共有ビューに `window.autonaPanelShell.openScoreSets(board)` を流す（ビューの構築・読み込み・凍結からの復帰の後に届け、パネル シェルが受け取るまで保留。1 つの文書には答えが来るまで 1 回だけ流す。先に別のパネルを開いたら・読み込みが失敗したら捨てる。終了処理中は何もしない）。ページは依頼の後に読んだ形式のボードを描いてから、そのボードで「大会の試合を選ぶ」が出る条件（ボード 1 / 2・大会あり）のときだけ開き、ほかはページを見せるだけ。legacy の `n` が形式のボードと違うとき（1v1 での `/scoreboard2-open-set`）は何もせずログだけで、答えは今までどおり `OK`。退路の Qt モードでは今までどおり Qt の窓。何も書かない。ページは `/api/v1`（`scoreboards/{n}/*`・`battle-format`・`match-names/candidates`・`tournament/info`・`tournament/{phases,sets,stations}`・`display-options`・`thumbnail:generate`）を呼び、held のボードは `GET .../pending`、ほかは state を描画するだけ | `score.{N}.*`（core 経由） | `score.{N}.*`、`GET .../pending`（held のボード）、`GET .../post`、`GET .../follow`（`scoreboard_follow` で読み直す）、`set_selection.*`、`tournamentInfo.*`、`display_options.*`、`game.*` | ✓ |
| 6 | `WebPanels`（共有ホスト。ナビの文言は `Commentary`） | React の `CommentaryPage`（`/web/commentary`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4b）。Qt 版と退路の設定は S4c で削除済み | 実況・解説者 12 スロットの編集（役割・コメント・表示トグル）、デザイン選択・OBS 用 URL 案内・プレビュー・機能別カラー。`commentary` の書き込みは `core/CommentaryService`（React 版は `/api/v1/commentary/*` 経由で、state を描画するだけ。ADR-005 S3e-2 / S4b / S4c） | `commentary.{1..12}.*` | `commentary.{1..12}.*`, `tournamentInfo.*`, `colors.commentary.*` | ✓ |
| 7 | `WebPanels`（共有ホスト。ナビの文言は `Statistics`、ナビのキーは `stats`） | React の `StatsPage`（`/web/stats`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4k）。Qt 版（`AutonaStatsWidget` と、そのサブタブの `AutonaAnalysisWidget`）は S4k で削除済み | ボード 1 の統計の取得（`POST /api/v1/scoreboards/1/stats:refresh`）と、アップセット係数・対戦カードの 1 行、4 タブ（直近の対戦 / 戦績 / 大会履歴 / この大会の試合一覧。下記 §4.1.0）。統計の書き手は core の `StatsService`、試合一覧は core の `AnalysisService`（ADR-005 S4j）で、ページは state を描画するだけ（read-only） | （なし。取得の依頼だけ） | `score.1.{recent_sets,last_sets,history_sets,upset_factor}`、反映済みの選手名、`analysis.*`、`GET /api/v1/analysis` | ✓ |
| 7.5 | `WebPanels`（共有ホスト。ナビの文言は `Stats Display Settings`、ナビのキーは `statsPreview`） | React の `StatsPreviewPage`（`/web/statsPreview`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4l）。Qt 版（`AutonaStatsPreviewWidget`）は S4l で削除済み | 統計オーバーレイ 7 種（`core/StatsOverlays` の固定表。`last_sets` / `tournament_history` は player1 / player2 の 2 項目）から 1 つを選び、プレビュー（オーバーレイ自体の縮小表示）・OBS 用 URL・機能別カラー（`stats`）・Companion マクロ（`stats:refresh` のメソッド・パス・URL）を出す。選択は `settings.json` の `stats_preview.selected_type`（`GET /api/v1/stats/overlays` / `PUT .../selected`）。state を書くのは色カード（`PATCH /api/v1/colors/stats`）だけ | `colors.stats.*` | `colors.stats.*`, `tournamentInfo.*` | ✓ |
| 8 | `WebPanels`（共有ホスト。ナビの文言は `Player List`、ナビのキーは `playerBrowser`） | React の `PlayerBrowserPage`（`/web/playerBrowser`。共有ビュー `AutonaWebPanelHost` に表示。ADR-005 S4n）。Qt 版（`AutonaPlayerBrowserWidget` と、選手カードの `player_editor` 向けの分岐）は S4n で削除済み | 選手一覧（2タブ）: トーナメント出場選手（`core/PlayerDirectory.Entrants`: `StreamService.TopEntrants` ＋ ローカルDB 突き合わせ、シード順、P1/P2 セット = v1 `:load`）／登録済み選手（`core/PlayerDirectory.Registered`。検索・ソート・100 行ごとのページ分割・新規登録・編集・削除 = v1 `PUT /players` / `POST /players:delete`）。編集の下書きはページのローカルで、保存のときだけ送る | なし（state に書かない。`player_editor` は古い `state.json` の掃除だけ残る） | `PlayerDB.instance`（core）, `StreamService.TopEntrants`（state 非依存）。再読込は Socket.IO `autonaPlayers.players`・`tournamentInfo.load`・`game` | ✓ |

> **NOTE タブ（`AutonaNotesWidget`）は廃止**。旧 `ANLYSE`（`AutonaAnalysisWidget`）は `Statistics` パネルのサブタブだったが、ADR-005 S4k で React の統計情報ページの 4 つ目のタブ「この大会の試合一覧」になり、Qt 版は削除済み。
> 旧 `PLAYERS`（`AutonaPlayerListWidget`、Top-8 順位ロスター編集）は機能ごと削除された。
> スコアボードの人数と「1選手あたりのキャラ数」は state が正本（スロット数とスロットの `character` の数。`AutonaActions.SetRoster`、ADR-005 S3e-6）。ボードの 2 つのスピンボックスは変更を依頼し、state を表示するだけ（キャラ数の初期値 3）。人数 0 のボードは次の選手のキャラ数を覚えておく（デスクトップは `charNumber`、ヘッドレスはボードホスト）。ゲームのロードが別のゲームへの切り替えなら、全スロットのキャラと mains を空にする（起動時の復元・同じゲームの再ロードでは空にしない。Top 8 と同じ規則）。シングルスのボードの LOSERS チェックボックスは、選手カードの下の専用の行に置く（カードと一緒に消えないように）。
> **スコアボードの選手行はコンパクト表示（2026-07、Web UI パリティ）**: Web `Player.jsx`（狭幅）と同配置 — スポンサー欄・プレイヤー名欄を**縦積み全幅**、その下に左寄せの「>>詳細」リンク（共通 QSS `QPushButton#linkButton` で細身表示）。本名・Twitter・国/地域・キャラクター・追加情報・シード表示・クリアは選手ごとのモーダルダイアログ（`AutonaScoreboardPlayerWidget._buildCompactDetailsUI`）に移設。ダイアログは同一ウィジェットインスタンスの再親化なので objectName キーパスは不変。選手 DB の編集は React の選手一覧（`/web/playerBrowser`、ADR-005 S4n）で行う。
> **目アイコン（フィールド表示トグル）はデスクトップから撤去（2026-07）**: 詳細ダイアログは**常時全項目表示**。`display_options.show_*` は Web UI / OBS オーバーレイ描画用の配信設定として存続し、非表示の QAction 群（`_eyeActions`）が `SetDisplayOption`（POST /display-option）と SettingsManager/StateManager 永続化を引き続き担う。デスクトップ側の表示連動が残るのは show_sponsor（インラインのスポンサー欄、「スポンサー名をスコアボードに表示しない」チェックボックス）のみ。「各プレイヤーの使用キャラクター数」スピンボックスも UI から撤去（`charNumber` は非表示のまま存続し、state のキャラ数を表示する。人数が 0 のボードでは新しい選手のキャラ数を決める。既定 3）。
> **スコア操作 UI（2026-07、Web `ScorePanel.jsx` パリティ）**: チームカードの下に全幅のスコアパネルカード（`scorePanelCard`）を置き、上から「+1｜+1」「スコア表示｜スコア表示（ゴールド・モノスペース、`QSpinBox[scoreDisplay="true"]` の共通 QSS）」「-1｜-1」「スコアリセット（全幅・通常色）」「⇄ チーム入れ替え（全幅）」を縦積み。スコア表示は矢印なし（±ボタンがインクリメント役）。
> **セクション構成（2026-07）**: 旧 topSectionFrame（Start.gg ロード行を収めた下段セクション）は廃止し、スコアボードは単一セクション。行順は「タイトル行 → Start.gg ロード行（auto-update タイマー含む） → フェーズ/ラウンド/BEST OF → スポンサー非表示チェック → 区切り線 → 共有グリッド（下記） → 下段設定（チームモードのみ）」。「すべての入力値をリセット」は Web の全クリア相当（選手・スコア・フェーズ/プール/ラウンド・BEST OF・チーム名。確認ダイアログあり、danger スタイル）。match バーを消すのはリセット系（すべての入力値をリセット / 全クリア / マッチリセット = `ResetMatch`）だけで、セット読込の前処理（`ClearScore`）は消さない（S-17）。
> **共有 2 列グリッド（`AutonaScoreboardWidget.columns`、2026-07）**: チームカード・スコアパネル・アクション行は**単一の QGridLayout**（列 stretch 1:1）に載せる。行0 = 1P カード｜2P カード、行1 = スコアパネル（2 列スパン）、行2 = 反映｜すべての入力値をリセット。**行ごとに独立した HBox を使わないこと** — 各行が自コンテンツから別々の「中央」を計算して縦軸がずれるのが過去の不揃いの根本原因（プローブ実測でグリッド共有はカード等幅・行間の軸一致を保証）。**スコア操作専用のサイズ/タイポグラフィは全廃**（旧 `SCORE_PANEL_CTRL_H` / `score_btn_style` / `score_display_style` トークンは削除済み）: 全コントロールはグローバル QSS の標準サイズ。スコア表示スピンボックスのみ「中央揃え・矢印なし」（±ボタンがインクリメント役のため）。
> **チームカード（2026-07、Web `Team.jsx` パリティ）**: 各チーム列は Paper 風カード（共通 QSS `QWidget#teamCard` / `QFrame#scorePanelCard`、`_styleTeamCard`）。ヘッダー行は敗者側チェックボックス（右）。シングルボードでは 1P/2P ラベルは**表示しない** — 列の識別はプレイヤー名欄のプレースホルダー「{0}Pプレイヤー名」（teamNumber ベース）が担う。チームボードのみ「TEAM N」ラベル＋チーム名欄を表示。詳細リンクの文言は「>>詳細」。

##### 4.1.0 統計情報ページのタブ（`web/src/StatsPage/`。ADR-005 S4k）

上部に「統計情報を取得する」（`POST /api/v1/scoreboards/1/stats:refresh`。応答を待つ間は押せない・成功は snackbar）と、
`Upset Factor: {係数} | P1 vs P2` の 1 行（係数は両選手の名前があるときだけ。無ければ `P1 vs P2`）。
値はすべて Socket.IO の `score.1.*` から TS で整形する（`format.ts`。日付はブラウザのローカル時刻、勝率は Python の `%.0f` と同じ偶数丸め）。
選手名は反映済みの値（OBS と同じ。統計キーは held 対象外なので、反映までの間は新しい選手の統計に前の選手名が付いて見える）。
選んだタブはモジュール変数に持つ（共有ビューでパネルを行き来しても残る。再読み込みで最初のタブ）。

| タブ | 表示元 |
|------|-------|
| 直近の対戦（Head-to-Head） | `score.1.recent_sets`（勝敗集計の 1 行と表。read-only） |
| 戦績（Last Sets） | `score.1.last_sets.{1,2}`（read-only） |
| 大会履歴（Tournament History） | `score.1.history_sets.{1,2}`（read-only） |
| この大会の試合一覧 | 取得・行の計算・大会切替での破棄は core の `AnalysisService`（ADR-005 S4j。全 phase group を同時 2 本で取得し、ヘッドレスでも動く）。ページは「start.gg からデータを取得」で `POST /api/v1/analysis:refresh` を呼び、`analysis.*`（進み具合）の 1 行と、`GET /api/v1/analysis` の行（タブを開いたときと、`analysis` が done / error / idle に変わったときだけ読む。描くのは今の `analysis` のために読んだ行だけで、取得中は直前に読んだ行を残し、idle では描かない）を描く。行の文言・既定の並び・絞り込みは `analysisRows.ts`（表 `analysisRows.cases.json` を pytest と jest の両方が読む）。並べ替えは `TableSortLabel` でクライアント側（read-only） |

#### 4.1.1 パネル（ナビ）の追加・削除規約

> **NOTE（ナビゲーション刷新 / STATE_VERSION 13）**: タブ化 Dock + `QMenuBar` は廃止。
> 各パネルは依然 `QDockWidget` サブクラスだが**ドックせず**、左サイドバー
> （`AutonaSidebar`）＋中央 `QStackedWidget` のページとして表示する。自前描画タブバー
> `AutonaDockTabTheme` は削除。運用操作（大会選択 / ゲーム選択 / 1v1·Team·Crew 形式）は
> サイドバー上部、設定・ヘルプは下部「Help & Settings」ボタンに集約。サイドバー地は
> ダークで、**選択中ナビのみブランド赤でハイライト**する（全面赤にはしない）。
> `QMainWindow.saveState/restoreState` は不使用。サイドバー選択 index と折りたたみ状態を
> `QSettings`（`sidebarIndex_v{N}` / `sidebarCollapsed_v{N}`）に保存する。

- 追加・削除時は `STATE_VERSION` を **必ずインクリメント**する
- **objectName は内部 ID として固定**する。i18n の対象は `setWindowTitle` などの**表示名のみ**で、objectName は不変
- objectName を変更する場合は `STATE_VERSION` インクリメントに加えて、必要に応じて**保存済み state / QSettings のマイグレーション処理を実装**する（state は `src/core/StateMigrations.py` に移行 step を足す。§3.3.5）
- `Autona.py` の `self._navSections` に `(panel, 'icon.svg')` を表示順で追加する
  （ナビ項目ラベルは `panel.windowTitle()`、アイコンは `assets/icons/`）
  - `_navSections` はカテゴリ付き構造 `[(カテゴリ見出し or None, [(パネル, アイコン名), ...]), ...]`。
    既存カテゴリ（Setup / ScoreboardCategory / Display / Data）に足す場合は該当セクションの
    パネルリストへ挿入し、新カテゴリを作る場合は `(translate 済み見出し, [パネルリスト])` を追加する
  - カテゴリ見出しはナビ index を消費しないため、ナビ index = `QStackedWidget` のページ index は保たれる

#### 4.1.2 ドック内の標準パターン
- `Autona.py` の `self._navSections`（カテゴリ単位のリスト）に
  `(panel, 'icon.svg', ナビラベル, None)` を表示順で追加する（アイコンは `assets/icons/`）
- **重いパネルは遅延構築**（起動再設計 Step 4b）: `panel` の代わりに `self._CreateXxxPanel`
  ファクトリと lazy キーを渡す（`(self._CreateXxxPanel, 'icon.svg', ラベル, 'キー')`）。stack には
  プレースホルダが入り、初回表示時に `_EnsurePanelAt` が差し替える（ナビ index == stack index を維持）。
  外部から構築を強制する経路（ホットキー・外部制御 API 等）には `EnsureXxxPanel()` アクセサを足す
- **パネル未構築でも成立させる**: 起動時に必要な state のシード・正規化・廃止キー掃除は
  パネルの `__init__` ではなく **`src/core/StateSchema.py`（`SeedPanelState()` / `SeedBoard()`）**
  に置く。`Window._SeedPanelState()` はその起動時の呼び出し口（SaveBlock で 1 サイクルに束ねる）で、
  Qt 依存で core に置けない `SeedColorState()` だけがここに残る。board スケルトンは
  `SeedBoard()` を `AutonaScoreboardManager.CreateScoreboard()` が未構築のボードに呼び、新しいボードの中身は
  `AutonaActions.SeedFreshBoard()` が両シェルで書く（デスクトップは `EnsureScoreboard` がウィジェットの構築の前に。ADR-005 S4t）。
  ボードを作る入口は `AutonaActions.EnsureBoard()`（S4y。シード・デスクトップだけの表示トグルの書き直し・取り直しの停止・
  反映ボタンの held 登録を 1 回だけ。ヘッドレスは `_SeedBoards` が `panel` なしで呼ぶ）
  グローバルシグナルへの接続も Window 側に置く
- 手順の詳細は [.claude/rules/python-widgets.md](../rules/python-widgets.md)「パネル登録」

#### 4.1.2 パネル内の標準パターン

| パターン | 内容 |
|---------|------|
| スコアボード番号 | board1=1v1、board2=team、board3=crew。画面表示は `scoreboard.battle_format`（`singles` / `team` / `crew`）で切り替える |
| 編集 → state 即時反映 | `editingFinished` / `valueChanged` 等で `StateManager.Set` を呼ぶ。バッチ操作は `SaveBlock()` で囲む |
| 設定ダイアログは別 QDialog | ドック自身は設定 UI を持たず、`AutonaSettingsWindow` に集約 |

### 4.2 React Web UI ページ仕様

`web/src/index.js` で React Router DOM v7 が以下を定義する。

| ルート | コンポーネント | 主目的 | 接続先 |
|-------|-------------|------|------|
| `/` | `ScoreboardPage` | スコア・選手・コメンテーターの操作（既定） | Socket.IO + REST |
| `/web` | `ScoreboardPage` | スコア・選手・コメンテーターの操作（board1） | Socket.IO + REST |
| `/scoreboard` | `ScoreboardPage` | スコア・選手・コメンテーターの操作（board1） | Socket.IO + REST |
| `/team` | `ScoreboardPage` | チーム用スコアボード操作（board2） | Socket.IO + REST |
| `/web/:panel` | `PanelShell`（`web/src/PanelShell/`） | React 化したデスクトップのパネル（ADR-005 S4）。サーバーが返すのは `WebAccess.WEB_PANELS` の名前だけ（他は 404）。`?desktop=1`（デスクトップの共有ビュー `AutonaWebPanelHost`）でブラウザ向けの部品を隠し、`?lng=` を i18next に渡す。確認用の `shell-check`（S4a）と実況・解説の `commentary`（S4b）と配信設定の `stream`（S4e）と大会情報の `event`（S4g）と Top 8 表示設定の `top8`（S4i）と統計情報の `stats`（S4k）と統計情報表示設定の `statsPreview`（S4l）と選手一覧の `playerBrowser`（S4n）とサムネイルの `thumbnail`（S4p）と大会リザルトの `result`（S4r）とスコアボード設定の `board`（S4s）とスコアボード入力の `scoreboard`（S4aa。ブラウザ版の `/scoreboard` とは別のページで、ブラウザ版は変えていない。退路の設定がある項目は、初めて開くまで共有ビューに加わらない）。サイドバーの React の項目は複数あっても共有ビューは 1 つで、各項目は自分のスタックページを持ち続け（ナビ index == スタック index）、最初に開いた項目のページにビューが建つ（`WebPanelPages`。S4e） | Socket.IO + REST |

#### 4.2.1 ScoreboardPage コンポーネント階層

```
ScoreboardPage (function component)
├── Header (AppBar)
│   ├── Game selector (Redux: tshGames)
│   ├── Tournament URL loader (REST: POST /api/v1/tournament)
│   ├── Auto-update toggle (REST: POST /api/v1/scoreboards/{N}:pull-stream)
│   └── Visibility menu (Redux: visibility)
├── CurrentSet
│   ├── Player ×2
│   │   ├── CharSelector (Redux: tshCharacters)
│   │   └── CountryStateSelector (Redux: tshCountries)
│   └── SetScore (NumberInput ×2 + match/phase/best_of)
├── UpcomingSets
└── Clear All Button (REST: POST /api/v1/scoreboards/{N}:clear)
```

#### 4.2.2 StageStrikePage（削除済み）

旧 `/stage-strike-app` / `StageStrikePage` は現行ビルドに存在しない。
Start.gg parser には互換データとして `stage_strike` / `ruleset` を読む処理が残るが、
React Web UI の操作画面・外部制御 API の対象ではない。

#### 4.2.3 React 共通コンポーネント

| コンポーネント | 役割 |
|-------------|------|
| `NumberInput.tsx` | +/- ボタン付き数値入力 |
| `CountrySelector.jsx` | 国 Autocomplete（Redux 依存: `tshCountries`） |
| `CountryStateSelector.jsx` | 国・州 チェーンセレクト |
| `GameIcon.jsx` | ゲームアイコン表示 |
| `ScoreboardPage/TextField.jsx` | カスタム MUI TextField（size 統一） |
| `NoSleep.js` | 画面スリープ防止（import で発動） |

### 4.3 OBS テンプレ命名規約

#### 4.3.1 ディレクトリ命名

##### 既存テンプレ
既存テンプレは互換維持のためリネームしない（例: `scoreboard_simple`, `scoreboard_topleft` などは命名軸が混在しているがそのまま残す）。

##### 新規追加時の命名軸（推奨）
```
layout/{用途}_{variant}/
```

`variant` は以下の優先順で命名する:

1. **表示対象・用途差分**（例: `top16`, `top8`, `vertical`, `horizontal`）
2. **配置差分**（例: `topleft`, `center`, `bottom`）
3. **表現スタイル差分**（例: `simple`, `geki`, `glass`）
4. **ブランド差分**（例: `evo`, `meijiyasuda`）

##### scoreboard 系の推奨パターン
新規追加分は `scoreboard_{placement}_{style}` に寄せる（例: `scoreboard_topleft_simple`）。既存の `scoreboard_simple` / `scoreboard_topleft` はリネームせず例外として残す。

#### 4.3.2 用途カテゴリ

| 用途プレフィックス | 内容 | 既存テンプレ例 |
|----------------|------|------------|
| `scoreboard*` | 配信中のスコアボード表示 | `scoreboard/`, `scoreboard_simple/`, `scoreboard_topleft/`, `scoreboard_Universal/` |
| `bracket_*` | トーナメントブラケット表示（Top 8 専用、`top8.*` を購読） | `bracket_top8/`（top12/top16/ultimate は Top 8 専用化で削除済み） |
| `result*` | リザルト画像（運用上 OBS には乗せない、画像生成専用） | §4.4 参照 |
| `recent_sets/` | 最近のセット表示 | 1 種のみ |
| `commentators/` | 実況・解説者バー（`commentary.{1..12}` の display=ON を 1〜4 名可変表示） | index.html 一本化（旧 tag*.html 3 バリアントは 2026-07 リワークで削除） |
| `camborder_*` | カメラ枠 | `camborder_Universal/` |
| `tournament_history/` | プレイヤー経歴 | player1.html / player2.html |
| `thumbnail*` | サムネイル画像生成専用 | §4.4 参照 |

#### 4.3.3 ファイル構成（標準）

各テンプレディレクトリは以下を含む:

| ファイル | 役割 | 必須? |
|---------|-----|------|
| `index.html` または `{template}.html` | エントリ HTML | ○ |
| `index.css` または `{template}.css` | テンプレ専用 CSS | ○ |
| `index.js` または `{template}.js` | `Start()` / `Update(event)` 実装 | ○（read-only テンプレでも空で配置） |
| `*_preview.png` | テンプレ選択 UI のプレビュー画像 | テンプレ選択 UI に表示するテンプレでは必須／直接 URL 指定のみで使う内部テンプレでは任意 |
| `settings.json` | テンプレ ローカル設定。アプリのデザイン一覧用の `autona` ブロックもここに置く（下記） | △（必要時。BOARD / 実況 / TOP 8 の一覧に載せるテンプレでは `autona` ブロックを置く） |

##### アプリのデザイン一覧のメタデータ（`settings.json` の `autona` ブロック）

BOARD / 実況 / TOP 8 のページのプルダウン・Designer の「開く」・`GET /api/v1/layouts` は、すべて
`src/core/LayoutCatalog.py`（Qt 非依存）の 1 つのカタログを読む。旧来型テンプレのメタデータは
`settings.json` の `autona` ブロック（ページ側の `globals.js` は読まない）、Designer 製
（`layout/custom/{slug}/`）は `project.json`。利用者向けの詳しい説明は `layout/README.md`
「アプリのデザイン一覧に載せる」。

| キー | 必須 | 内容 |
|------|------|------|
| `kind` | ○ | `scoreboard` / `commentary` / `top8`（`designer` は `layout/custom/` 専用で宣言不可）。不正・破損ブロックは警告ログを出してブロック無し扱い |
| `displayName` | | 一覧の表示名。省略時はフォルダ名から生成 |
| `format` / `game` | | BOARD のメタ欄（対戦形式・ゲーム）。省略時は `null`（画面は「—」） |
| `entry` | | 入口 HTML のファイル名（フォルダ内のみ）。省略・フォルダ外指定は `index.html` |

- **フォルダ名へのフォールバック**: ブロックが無い／壊れたフォルダは旧来の命名規約で拾う（`scoreboard*` → scoreboard、`commentators` / `commentators_*` → commentary、`bracket_top8` / `bracket_top8_*` → top8。基本フォルダは「標準」、バリアントは接尾辞を整形: `commentators_dark-gold` → `Dark Gold`）。`displayName` だけ省略したときも同規則、規約外の名前は `_` → 空白 + 単語先頭大文字。入口ファイルの無いフォルダ、`custom/` `designer_runtime/` `include/` `icons/` は載らない
- **Designer 製**: 表示名は `Custom: {project.json の name}`（空なら slug）。対戦形式・ゲームは `meta.format` / `meta.game`、未設定なら `1v1` / `カスタム`。Designer の「開く」一覧だけは接頭辞なしの `name` を出す（Designer の中なので区別が要らない）
  - `meta` は schemaVersion 3 の任意フィールド（バージョンは上げない）。Designer 左サイドバーの「対戦形式」「ゲーム」欄で編集し、`model.ts` の `validateProject()` が保存時に trim・空項目の除去（全部空なら `meta` ごと省略）・各 64 文字上限・文字列型を検査する。Python `_validate_project` は `meta` が dict か不在かだけを見る（サイズは `MAX_PROJECT_BYTES` で全体として制限）。`runtime.js` は読まない（`tests/test_designer_runtime_contract.py` が描画不変を pin）
  - Designer のプリセット（空 / 基本 1v1 / チーム / クルー、`web/src/ScoreboardDesigner/index.tsx` の `DESIGNER_PRESETS`）は Designer 内部の資産で、`layout/` の組み込みフォルダとは対応しない。空以外は `meta.format` も入れる
- **重複時の表記**: 同じ `kind` の中で表示名が重なったら、重なったもの全部を `{displayName} ({id})` にする（例: 複製フォルダ `scoreboard_simple - コピー` → `Simple (scoreboard_simple)` / `Simple (scoreboard_simple - コピー)`）。`kind` ごとに別のプルダウンなので、kind をまたぐ同名（実況と TOP 8 の「標準」）は変えない
- pin: `tests/test_layout_catalog.py`（出荷フォルダのブロックの値そのものは `test_the_shipped_layouts_declare_their_metadata_explicitly`）

#### 4.3.4 共有リソース（`layout/include/`）

すべてのテンプレが以下を参照できる:

| リソース | 用途 |
|---------|------|
| `globals.js` | `LoadEverything()`, `InitAll()`, `UpdateData()`, `UpdateData_SocketIO()`, `tsh_update` イベント発行 |
| `assetUtils.js` | アセット解決ヘルパー |
| `socket.io.min.js` | Socket.IO クライアント |
| `gsap.min.js` | アニメーション |
| `jquery-3.7.1.min.js` | DOM 操作 |
| `lodash.min.js` | ユーティリティ |
| `kuroshiro*.min.js` + `kuromoji/` | 日本語処理（読み仮名生成） |
| `color-thief.min.js` | 画像から代表色抽出 |
| 各種フォント | カスタムフォント定義用 |

`include/` 内のライブラリのバージョンを上げる時は、全テンプレで動作確認が必要。

#### 4.3.5 main.css の利用規約

`layout/main.css` は CSS 変数による「共通基盤」を提供する:

| CSS 変数 | デフォルト | 用途 |
|---------|---------|------|
| `--font` | `"LINE Seed JP", sans-serif` | 主フォント |
| `--text-color` | `#ffffff` | 主テキスト色 |
| `--bg-color` | `#121212` | 背景 |
| `--p1-score-bg-color` | `#fe3636` | P1 スコア背景 |
| `--p2-score-bg-color` | `#2e89ff` | P2 スコア背景 |
| `--p1-sponsor-color` / `--p2-sponsor-color` | `#ff7a6d` / `#29b6f6` | スポンサー色 |
| `--theme-color` | `#181B21` | テーマ色（正本 `src/core/StateSchema.py` の `DEFAULT_THEME_COLOR` と同値） |
| `--accent-color` | `#f0a500` | アクセント |
| `--bevel-light` / `--bevel-dark` | color-mix で派生 | 立体感 |
| `--tile-radius` / `--tile-gap` | `6px` / `5px` | タイル UI |
| `--border-radius` | `16px` | 角丸 |

各テンプレは `:root` でこれらを上書きしてよい（クリエイティブ領域の自由度）。

### 4.4 画像生成テンプレ命名規約

#### 4.4.1 サムネイル

##### ディレクトリ命名

```
layout/thumbnail/         ← 「Standard」テンプレ (template_id = "type_a")
layout/thumbnail_type_b/  ← 「Center Panel」テンプレ (template_id = "type_b")
layout/thumbnail_type_{x}/← 新規追加（任意の識別子）
```

`Standard` と `Center Panel` の 2 種類。両者とも Center Panel デザイン
（左右キャラ＋中央パネル）を出発点として共有し、今後それぞれ別デザインに
作り込んでいく。設定 UI の `templateSelect` で選択する。

##### `ThumbnailRenderer.TEMPLATE_SUBDIRS` の規約

```python
TEMPLATE_SUBDIRS = {
    "type_a": "thumbnail",
    "type_b": "thumbnail_type_b",
    # 新規追加: "type_c": "thumbnail_type_c",
}
DEFAULT_TEMPLATE_ID = "type_a"
```

`template_id` は `SettingsManager.thumbnail_config.template_id` に保存される（§3.3.4）。

##### ファイル構成

```
layout/thumbnail*/
├── thumbnail.html          ← entry（async function initThumbnail(payload) 必須）
├── thumbnail.css           ← スタイル
├── thumbnail.js            ← initThumbnail + RENDER_READY シグナル
└── thumbnail_assets.js     ← （任意）共通アセットヘルパー
```

##### テンプレ定義 JSON

```
assets/thumbnail_base/thumbnail_types/{template_id}.json
```

各テンプレ ID に対応する設定値（フォント・配置・色トーン等のテンプレ固有パラメータ）を定義。

##### 出力寸法

固定 **1920×1080**（透過 PNG 対応）

#### 4.4.2 リザルト

##### ディレクトリ命名

```
layout/result/                ← horizontal (デフォルト)
layout/result_top16/          ← horizontal_16
layout/result_vertical/       ← vertical
layout/result_vertical_geki/  ← vertical_geki
layout/result_team/           ← team / Crewbattle（各順位にチームの複数メンバーのメインファイターを配置）
layout/result_team_split/     ← team_split（1位2位を縦並びで左に大きく、3-8位を右に縦並び）
layout/result_{x}/            ← 新規追加
```

##### レイアウトの表の規約

表の正本は `core/ResultService.py` の `LAYOUT_SUBDIRS`（id → `layout/` のフォルダ）/ `LAYOUT_SIZES`（幅 × 高さ）/ `LAYOUT_NAMES`（`.ts` の訳の source）で、
枠の数は `SlotsFor()`、既定は `DEFAULT_LAYOUT`（単独）/ `DEFAULT_TEAM_LAYOUT`（団体）。レンダラー（`result/ResultPageRenderer.py`）はこの表を読む（ADR-005 S4r で表は 1 つになった）。
ページの選択肢は `GET /api/v1/result` の `layouts` を描く（§7.3）。

##### team レイアウト（チーム / Crewbattle）

- 横向き Top-8 グリッド（`result/result.css`）を土台に、各順位枠を「チームカード」として描画する。`result/result.js` の `placeTeams()` が `payload.layout === "team"` のとき呼ばれ、枠ごとにチームのメンバー（2-5 人）のメインファイターを横並びに配置し、各ファイターの右下に選手名を反時計回り 90°回転（下→上読み）で重ね、枠下にチーム名プレートを置く。選手名は全角 5-10 文字を縦長に収める想定で、ファイター下部に暗いフェードを敷いて視認性を確保する。styling は `result_team/result.css`（base を `@import` してチーム枠を上書き）。
- データは `result_teams[N]` に `members: [{ name, twitter, character }, ...]` を持つ（行の `name` はチーム名。ADR-005 S4q で `result_players` から分けた）。`PATCH /api/v1/result/teams/{n}` で手入力でき、Start.gg のチーム standings からの自動取得にも対応する（ロスター全員 + mains）。React のページ（`/web/result`）では団体の行（順位・チーム名・最大 5 メンバー）がこれを編集する（S4r）。
- 旧 team レイアウト（チーム名 + ロスター文字列のみのテキストプレート）はファイター版に置き換え済み。
- `team_split` は `team` のバリアント。`result_team_split/result.css` が `result_team/result.css` を `@import` して **グリッド配置のみ** 差し替える（1位2位を左カラムの縦並び、3-8位を右カラムの縦並び）。メンバー横並び・回転名・下部フェード等の演出は `team` から継承。レンダラーは `placeTeams()` を共有（`layout` が `team` で始まれば team系扱い）。

##### ファイル構成

```
layout/result*/
├── result.html              ← entry（async function initResult(payload) 必須）
├── result.css               ← スタイル
└── result.js                ← initResult + RENDER_READY シグナル（CSS-only テンプレでは省略可）
```

##### 出力寸法

`LAYOUTS` で定義する。横版は 1920×1080、縦版は 960×1080 が標準。

#### 4.4.3 ペイロード規約

##### Thumbnail ペイロード

```javascript
async function initThumbnail({ data, config, template }) {
  // data:     score, game, tournamentInfo を含む state スナップショット
  // config:   thumbnail_config（display_phase, use_sponsors 等）
  // template: { template_id, ...assets/thumbnail_base/thumbnail_types/{id}.json の内容 }
  // 描画完了後に document.title = "RENDER_READY"
}
```

##### Result ペイロード

```javascript
async function initResult({ data, config, layout }) {
  // data:   { tournamentName, eventName, startAt, numEntrants, players: [...] }
  //   players[N] = { rank, name, sponsor, character, characters: [...],
  //                  members: [{ name, character }, ...] }
  //                ※ members は team レイアウト専用（name = チーム名）
  // config: { main_icon_path, background_path, accent_color, ... }
  // layout: 現在のレイアウト名（"horizontal" / "team" 等）。"team" のとき
  //         placeTeams() でメンバーのファイターを配置、それ以外は placePlayers()
  // 描画完了後に document.title = "RENDER_READY"
}
```

#### 4.4.4 出力ファイル名

| 種別 | パス | ファイル名 |
|------|------|----------|
| Thumbnail final | `./out/thumbnails/` | `{tag1}-vs-{tag2}-{YYYYMMDDTHHmmssZ}.png` |
| Thumbnail final | 同上 | `{tag1}-vs-{tag2}-{YYYYMMDDTHHmmssZ}_title.txt` / `_desc.txt`（YouTube メタデータ。**現状は常時生成**。将来は `thumbnail_config.generate_metadata = true` の設定で制御する。設定化は §8 の移行タスクで対応） |
| Thumbnail preview | `AutonaResolve("tmp/thumbnail")` | `preview.png`（v1 の `:preview` / `:generate` が毎回上書き。`GET /api/v1/thumbnail/preview` が配信。Qt 版の `template.png` は S4p で無くなった） |
| Result final | `./out/results/` | `{tournament}-top{n}-{YYYY-MM-DD-HH-MM-SS}.png`（UTC。大会名が空のときは `result-top{n}-...`） |
| Result preview | `AutonaResolve("tmp/result")` | `result_preview.png`（v1 の `:preview` / `:generate` が毎回上書き。`GET /api/v1/result/preview` が配信） |

特殊文字（`<>:"/\|?* ` 等）は識別子から除去する（R-Img-7）。

---

## 5. デザインシステム

「アプリ内部」（PyQt + React）には統一されたデザインシステムを適用する。「クリエイティブ領域」（OBS HTML / 画像生成テンプレ）には強制しない（§5.5）。

### 5.1 統合色トークン

#### 5.1.1 設計方針

- PyQt（`src/ui_constants.py`）と React（`web/src/themes.js`）で**同一の役割・同一の値**を持つ
- **機械可読の正本は `assets/design-tokens.json`**。両側の契約テスト（`tests/test_design_tokens_contract.py` / `web/src/themes.test.js`）が値のズレを検出する。変更時は json → 両コンシューマの順で更新する
- 命名規則は言語慣例に従い、PyQt: `snake_case`、React: `camelCase`（プログラム的に変換可能）
- テーマは DARK のみ（LIGHT は撤回済み、§5.1.3 / §5.4 参照）
- accent 色は **スカーレット（純色寄りの赤）** を採用。色相 ~2° はブランドクリムゾン（~348°）とタブバーのグラデーション橙端の中間で、グラデーションの色相レンジ内に収まりつつ brand と差別化される。旧クリムゾン `#8A1A2C` は暗背景上の選択タブ／メニュー文字でコントラストが低かったため明度を上げた（DARK `#C33732`、暗背景でコントラスト比 ~3.2。ベタ塗りの primary ボタンが暗背景で主張し過ぎないよう LIGHT より深めに設定 / LIGHT は明背景・白文字ボタン両対応のため `#CE3A33`）。brand（クリムゾン純色 `#DC143C`/`#C41232`）はライブ／ステータス信号用として据え置き

#### 5.1.2 色トークン正本（DARK）

ニュートラルは単一の寒色相（H~218°、S~6-9%）のクールグレー・ランプ。明度は旧 warm-neutral 値と同等でコントラスト比は不変。有彩トークン（brand/accent/gold/セマンティック）は暖色のまま、寒色グレーに対して際立たせる。

| 役割 | 値 | PyQt キー | React キー |
|------|-----|----------|----------|
| キャンバス | `#0A0B0D` | `canvas` | `canvas` |
| 主背景 | `#111317` | `bg_primary` | `bgPrimary` |
| 副背景 | `#191B20` | `bg_secondary` | `bgSecondary` |
| 三段背景 | `#23262C` | `bg_tertiary` | `bgTertiary` |
| 四段背景 | `#2D3138` | `bg_quaternary` | `bgQuaternary` |
| 淡い枠線 | `#23262C` | `border_subtle` | `borderSubtle` |
| 標準枠線 | `#383C44` | `border_default` | `borderDefault` |
| 強い枠線 | `#53575F` | `border_strong` | `borderStrong` |
| 主テキスト | `#E9ECF1` | `text_primary` | `textPrimary` |
| 副テキスト | `#AFB3BB` | `text_secondary` | `textSecondary` |
| 淡いテキスト | `#81858E` | `text_muted` | `textMuted` |
| ラベル | `#9CA0A9` | `text_label` | `textLabel` |
| 無効テキスト | `#4F525A` | `text_disabled` | `textDisabled` |
| ブランド色 | `#DC143C` | `brand` | `brand` |
| ブランド淡色 | `#2A1418` | `brand_subtle` | `brandSubtle` |
| **アクセント色（スカーレット）** | **`#C33732`** | **`accent`** | **`accent`** |
| アクセントホバー | `#D8453E` | `accent_hover` | `accentHover` |
| アクセント淡色 | `#3A1413` | `accent_subtle` | `accentSubtle` |
| ゴールド | `#F0C45A` | `gold` | `gold` |
| ゴールド淡色 | `#2E2A1E` | `gold_subtle` | `goldSubtle` |
| ポジティブ | `#6EC77A` | `positive` | `positive` |
| ネガティブ | `#F04E54` | `negative`（明るいアラート赤。danger ボタンはアウトライン式で暗背景での可読性確保のため brand から分離） | `negative` |
| オンアクセント | `#FFFFFF` | `on_accent` | `onAccent` |
| ニュートラルボタン | `#3B3F47` | `btn_neutral` | `btnNeutral` |
| ニュートラルボタン ホバー | `#494D56` | `btn_neutral_hover` | `btnNeutralHover` |
| プライマリボタン背景 | `#EFF1F5` | `primary_btn_bg` | `primaryBtnBg` |
| プライマリボタン文字 | `#0E0F12` | `primary_btn_text` | `primaryBtnText` |
| プライマリボタン ホバー | `#DBDDE3` | `primary_btn_hover` | `primaryBtnHover` |

#### 5.1.3 色トークン正本（LIGHT）— ❌ 撤回

テーマ切替の撤回（§5.4、T-8〜T-10）に伴い LIGHT パレットも廃止した。PyQt（`ui_constants.py`）は削除済み、React（`themes.js`）の未使用 `LIGHT_TOKENS` も 2026-07 の Web UI 刷新 Phase 1 で削除。`assets/design-tokens.json` にも DARK のみ収録する。

#### 5.1.4 セマンティック色

役割色は以下の用途に対応する:

| セマンティック | 用途 | 使用トークン |
|------|------|-----|
| 主アクション | プライマリボタン、フォーカスリング、選択ハイライト | `accent` / `accent_hover`（塗りのプライマリボタンは `primary_btn_*`） |
| ニュートラルアクション | セカンダリボタン（グレーのチップ状塗り） | `btn_neutral` / `btn_neutral_hover` |
| 強調・ライブ | LIVE バッジ、ステータス、アラート | `brand` |
| 成功・確定 | チェック完了、ポジティブ通知 | `positive` |
| エラー | 入力エラー、削除確認、危険操作 | `negative`（brand から分離済み） |
| 警告・注目 | 未確認、注意、特別ハイライト | `gold` |
| ニュートラル | デフォルトテキスト・背景・枠線 | `text_*`, `bg_*`, `border_*` |

**React MUI セマンティックスロット対応**（`themes.js`）: `error` = `negative`、`success` = `positive`、`warning` = `gold`、`info` = `text_secondary`。`MuiButton` は containedPrimary = `primary_btn_*`、outlined = `btn_neutral*`（デスクトップのニュートラルボタンと同じ塗りチップ）。

#### 5.1.5 プレイヤー色（特殊）

スコアボードの P1 / P2 は固定の意味を持つ:

| 色 | 値 | 用途 |
|----|------|------|
| P1 | `#DC143C`（= brand） | プレイヤー1 のスコア背景・名前枠 |
| P2 | `#3D8BE8` | プレイヤー2 のスコア背景・名前枠 |

P1/P2 は **テーマ色ではなく競技上の識別色**（`design-tokens.json` の `player_colors`）。React 側は `theme.palette.p1color` / `p2color` でアクセス。PyQt 側は `AutonaColorButton` が動的に `setStyleSheet` で適用。**チームカラーがユーザー指定された場合はその値が優先**される（`score.{N}.team.{T}.color`）。

**可読性ルール**: P2 背景（`#3D8BE8`）上に小さい白文字を置く場合は、テキストシャドウ・縁取り・濃色テキストなどで可読性を担保する。LIGHT モードで P2 背景上に淡色テキストを置く場合も同様。

#### 5.1.6 ハードコード禁止

**新規コードでハードコード hex を直接書かない**。色を使う時は必ずトークン参照経由:
- PyQt: `ui_constants.palette()['key']` または対応するスタイル関数
- React: `theme.palette.{semanticSlot}` または `theme.palette.autona.{tokenKey}`

既存のハードコード色（PyQt 12+ 箇所、React App.css 数箇所）はリファクタタスク（§8）で順次置換する。

### 5.2 タイポグラフィ

#### 5.2.1 フォントファミリー

| 用途 | フォント | PyQt キー | React 設定 |
|------|---------|----------|----------|
| 主フォント（UI 全般） | LINE Seed JP | `FONT_FAMILY` | `theme.typography.fontFamily` 第 1 位 |
| 等幅フォント（コード・スコア表示） | JetBrains Mono | `FONT_FAMILY_MONO` | `<code>` 要素の font-family |
| Condensed（横長表示用） | Roboto Condensed | `FONT_FAMILY_CONDENSED` | （現状未使用、必要時に追加） |

フォントスタックのフォールバック順:
- PyQt: `"LINE Seed JP" → 既定 sans-serif`（OS 依存）
- React: `"LINE Seed JP" → "Inter" → "Noto Sans JP" → -apple-system, BlinkMacSystemFont, ...`

#### 5.2.2 フォントサイズ

| トークン | PyQt | React 推奨用法 | 用途 |
|--------|------|------------|------|
| FONT_XS | 8pt | `theme.typography.caption` | キャプション・補足 |
| FONT_SM | 10pt | （MUI 既定 fontSize より小さめ） | 本文基底・タイトルラベル・サブテキスト |
| FONT_MD | 11pt | `theme.typography.body2` 相当 | 本文・ラベル |
| FONT_LG | 13pt | `theme.typography.body1`、`theme.typography.fontSize: 13` | 主本文 |
| FONT_XL | 16pt | `theme.typography.h6` 相当 | 見出し |

**規約**: PyQt の `pt` と React の `px` は**完全一致させない**。同一トークン名が**同等の視覚階層**を表すことを仕様とし、実寸は各 UI フレームワークの自然な密度を優先する（React 側を `14.7px` のような変則値に寄せない）。

#### 5.2.3 フォントウェイト

| 用途 | ウェイト |
|------|--------|
| 通常テキスト | 400（Regular） |
| ボタン・タブ | 700（Bold） |
| 見出し | 700 |
| Caption | 400 |

React 側のボタンは `theme.components.MuiButton.styleOverrides.root.fontWeight = 700` + `letterSpacing: '0.04em'` + `textTransform: 'none'` で統一。

### 5.3 寸法トークン

#### 5.3.1 スペーシング

| トークン | 値 | PyQt | React 用法 |
|--------|-----|------|----------|
| SPACE_XS | 2px | `SPACE_XS` | `sx={{ p: 0.25 }}` 相当 |
| SPACE_SM | 4px | `SPACE_SM` | `sx={{ p: 0.5 }}` |
| SPACE_MD | 8px | `SPACE_MD` | `sx={{ p: 1 }}` |
| SPACE_LG | 12px | `SPACE_LG` | `sx={{ p: 1.5 }}` |
| SPACE_XL | 16px | `SPACE_XL` | `sx={{ p: 2 }}` |

React の MUI `theme.spacing(1) = 8px` を基準として、`sx` の倍率は上記対応で読み替える。

#### 5.3.2 ボーダー半径

| トークン | 値 | PyQt | React 用法 |
|--------|-----|------|----------|
| RADIUS_SM | 3px | `RADIUS_SM` | `sx={{ borderRadius: '3px' }}` |
| RADIUS_MD | 5px | `RADIUS_MD` | `sx={{ borderRadius: '5px' }}` |
| RADIUS_LG | 8px | `RADIUS_LG` | `sx={{ borderRadius: '8px' }}` |

MUI のデフォルト `theme.shape.borderRadius = 4` は使わず、上記 3 段階を明示的に使う。

#### 5.3.3 ウィジェット高さ

| トークン | 値 | 用途 |
|--------|-----|------|
| _WIDGET_H | 26px | 標準入力ウィジェット高（QLineEdit, QComboBox, QSpinBox 等） |
| BTN_SM | 24px | 小ボタン |
| BTN_MD | 28px | 標準ボタン |
| BTN_LG | 36px | 強調ボタン |

React の MUI 側は `size="small"` で密度を揃える（§2.2 R-React-5）。

#### 5.3.4 アイコンサイズ

| トークン | 値 | 用途 |
|--------|-----|------|
| ICON_SM | 24px | インライン |
| ICON_MD | 32px | ボタン内 |
| ICON_LG | 64px | 強調・大型表示 |

### 5.4 テーマ（ダーク固定）

**実行時のライト/ダーク切替機能は削除した（2026-06-28、ダーク固定）。**

理由: テーマ切替は `Autona.LoadTheme()` → `qdarktheme.setup_theme()` を呼び、これが
QApplication 全体に `setStyleSheet` + `setPalette` を再適用して**全ウィジェットを再ポリッシュ**する。
旧ブラケットビュー（`AutonaBracketView`、Top 8 専用化で削除済み）は全試合のウィジェット
ツリーを単一の `QGraphicsProxyWidget` に埋め込んでおり、proxy 埋め込みウィジェットの
再ポリッシュは病的に遅く、大規模ブラケット読込時に GUI スレッドが **30 秒以上フリーズ**
していた。切替トリガー自体を無くすことで恒久的に解消する。

#### 5.4.1 現在の仕様

- テーマは**ダーク固定**。`ui_constants.palette()` / `global_qss()` は常に `DARK` を返す。
- テーマ適用は**起動時の 1 回のみ**（`Window.__init__` → `LoadTheme()` → `qdarktheme.setup_theme("dark", ...)`）。実行時に再適用するパスは存在しない。
- React Web UI も**ダーク固定**（`ThemeShell.tsx` が `darkTheme` を常用）。バックエンドは `ui.light_mode` を broadcast しない。

#### 5.4.2 削除済みの要素（参照用）

以下は切替機能と共に削除した。再導入する場合は上記フリーズの再発防止（proxy 埋め込み
ブラケットを一時退避する等）を設計に織り込むこと:

- PyQt: `WindowSignals.LightModeChanged`、`Window.ToggleLightMode` / `_applyLightMode`、"Light mode" メニュー、`ui_constants` の `LIGHT` パレット / `_is_light_mode` / `on_theme_change` / `notify_theme_change` / `_theme_listeners`
- Web サーバー / API: `AutonaWebServerActions.set_light_mode`、`/set-light-mode` REST + socketio、`/api/v1/ui/theme`
- 状態 / 設定: `StateManager.ui.light_mode` broadcast キャッシュ、`SettingsManager.light_mode`（旧値が残っても無視される）
- React: `ThemeShell` の `ui.light_mode` 購読 / localStorage キャッシュ、`themes.js` の `lightTheme` export

### 5.5 クリエイティブ領域の独立性

#### 5.5.1 適用しないルール

OBS HTML レイアウト（`layout/scoreboard*` `bracket*` `recent_sets/` `commentators/` `camborder*` `tournament_history/`）と画像生成テンプレ（`layout/thumbnail*` `result*`）には **§5.1〜§5.4 を強制しない**。

具体的には:
- 配色は自由（OBS の `--accent-color: #f0a500` などアプリ内部とは異なる値で OK）
- フォントも自由（テンプレ別に `@font-face` で独自フォント追加可）
- 寸法トークンの適用義務なし
- ダーク/ライト切替の追従義務なし

#### 5.5.2 共通基盤として残す部分

ただし以下は「実装の共通化のため」残す:

| 項目 | 内容 |
|------|------|
| `layout/main.css` の CSS 変数 | デフォルト値として提供。テンプレが自由に上書きしてよい |
| `layout/include/` のライブラリ | jQuery / GSAP / Socket.IO / kuroshiro 等は共通利用 |
| `layout/fonts/` のフォント | アプリで提供するフォントセット。テンプレが好きに参照 |
| `layout/icons/` の SVG | アプリ提供のアイコン。テンプレが好きに参照 |

#### 5.5.3 大会別カスタマイズ / 機能別カラー

「大会ブランドに合わせたテーマカラー」を OBS テンプレに反映する仕組みは以下の経路で実現する:
- `tournamentInfo.themeColor` / `accentColor`（大会共通）を CSS 変数経由で OBS HTML に注入（既存テンプレ互換）
- 値の正本は `SettingsManager.general.theme_color / accent_color`（§3.1.2 注釈、§3.3.2）
- 機能ごとに色を変えたい場合は `colors.{feature}` で上書きする（正本は `SettingsManager.colors.{feature}.*`）

##### 優先順位

```
colors.{feature}.themeColor / accentColor   （機能別の上書き。設定された feature にのみ存在）
  ↓ 無ければ
tournamentInfo.themeColor / accentColor     （大会共通カラー）
  ↓ 無ければ
テンプレ CSS の :root デフォルト値
```

##### 大会カラー変更時の挙動（機能別上書きのクリア）

大会カラーの変更は「全レイアウトをこの色に揃える」操作として扱う。
`core/ColorService.SetTournamentColor(kind, color)` は永続化・state 展開に加えて、
**全 feature の同じ kind の上書きを削除**する（SettingsManager / StateManager 両方）。

- クリアは **kind 単位**。テーマ色を変えてもアクセントの機能別上書きは残る
- 上書きが消えると各レイアウトのフォールバックが働き、新しい大会カラーが即座に全レイアウトへ反映される
- 大会情報ページの「既定の色に戻す」（`PATCH /api/v1/colors/tournament` の `{"reset": true}`）も `SetTournamentColor` 経由なので同挙動
- 機能別カラーは、その後に個別編集した時点から再び独立する
- 構築済みパネルのカラーボタン表示は、カードの showEvent リフレッシュ（`_ColorCardRefresher`）で追従する

| feature | 適用先 | 設定パネル |
|---------|--------|-----------|
| `board` | `scoreboard_*` / `camborder_Universal` | スコアボード設定 |
| `thumbnail` | `thumbnail` / `thumbnail_type_b`（Python が payload の `config.theme_color` / `accent_color` に解決） | 対戦動画サムネイル |
| `result` | `result` / `result_vertical_geki`（同上） | 大会リザルト |
| `top8` | `bracket_top8*` | TOP8 |
| `commentary` | `commentators*` | 実況・解説 |
| `stats` | `upset_factor` / `h2h_record` / `recent_sets` / `last_sets` / `tournament_history` | 統計情報表示 |

JS 側の読み方（SocketIO 系レイアウトの共通形）:

```js
const ti = event.data.tournamentInfo || {};
const fc = (event.data.colors || {}).board || {};
const themeColor = fc.themeColor || ti.themeColor;
```

##### 新規テンプレでの利用ルール

| ルール | 内容 |
|------|------|
| **R-Brand-1** | 新規 OBS / 画像生成テンプレでは `tournamentInfo.themeColor` / `accentColor` / `colors.{feature}.*` を **JS から直接読まない**。`--theme-color` / `--accent-color` などの CSS 変数経由でのみ利用する（上記の解決処理は各レイアウトのエントリ JS が一度だけ行う） |
| **R-Brand-2** | テンプレ固有のブランド表現（例: 大会公式の独自配色）がある場合は、テンプレの `settings.json` または `:root` 上書きを優先してよい |
| **R-Brand-3** | テンプレが state の特定キーに直接依存する設計は避ける。CSS 変数 / DOM クラス経由を基本とする |

---

## 6. ホットキー仕様

### 6.1 設計方針

| ルール | 内容 |
|------|------|
| **R-Hotkey-1** | ホットキーは **OS グローバル**で登録する（Windows: `RegisterHotKey` / 他 OS は当面非対応） |
| **R-Hotkey-2** | デフォルトキー定義は `src/AutonaHotkeys.py` の `keys` クラス変数に**ハードコード**で持つ |
| **R-Hotkey-3** | ユーザーカスタムは `SettingsManager.hotkeys.{action_key}` に保存。デフォルトに `update()` でマージする |
| **R-Hotkey-4** | アクション通知は `AutonaHotkeysSignals` の Signal 経由（PyQt の signal/slot） |
| **R-Hotkey-5** | デフォルトのモディファイアは **`Ctrl+Shift+`** プレフィックス（標準ショートカット `Ctrl+S`/`Ctrl+O`/`Ctrl+P`/`Ctrl+R` の食い合いを避けるため） |
| **R-Hotkey-6** | `SettingsManager.hotkeys.hotkeys_enabled = false` でグローバル無効化できる |
| **R-Hotkey-7** | **state を書くだけのアクションは `AutonaHotkeys.ConnectActions()` が `AutonaActions` に直結する**（`bootstrap_gui()` から 1 回・プロセス寿命。ADR-005 S3c までは `bootstrap_core()`）。対象ボードは `AutonaActions.ActiveBoard()`（= `SettingsManager.scoreboard.battle_format`）で解決するため、パネル未構築でも動き、作用するのは常に 1 面だけ。ウィジェット側に接続を持つアクションは無い（`load_set` は ADR-005 S4y から `ConnectActions()` が結線し、アクティブボード（1 / 2）のセット選択をシェルのアダプタ `AutonaShellOperations` で開く。React の SCORE ではメインウィンドウを前に出して SCORE に切り替え、ページのセット選択のダイアログを開く（ADR-005 S4ab-2）。退路の Qt モードでは Qt のセット選択ウィンドウ。クルー形式・未構築なら何もしない。それまでは構築済みの 1v1 / チームの全ボードが自分のウィンドウを開いていた。`refresh_phase_group` は ADR-005 S3e-8 から `ConnectActions()` が `Top8Service.RefreshFromProvider` に直結する。それまでは Window が Top 8 パネルを構築して呼んでいた。headless core Step 3 / ADR-004 D2。`swap_teams` は ADR-005 S2c で Actions 直結へ移った。それまでは構築済みの全ボードが受けており、形式切替後は両ボードが入れ替わった。クルー形式ではスワップのホットキーは何もしない。アクティブボードが未構築のときも、旧実装どおり何もしない） |

### 6.2 デフォルトホットキー一覧

| アクションキー | デフォルト | Signal | 動作 | 受け手 |
|--------------|-----------|--------|------|-------|
| `load_set` | `Ctrl+Shift+O` | `AutonaHotkeys.signals.load_set` | アクティブボードのセット選択を開く（React の SCORE: ウィンドウを前に出して SCORE のページのダイアログ。S4ab-2。退路の Qt: Qt のウィンドウ） | シェル（`AutonaShellOperations`。S4y） |
| `team1_score_up` | `Ctrl+Shift+1` | `team1_score_up` | チーム 1 スコア +1 | Actions |
| `team1_score_down` | `Ctrl+Shift+2` | `team1_score_down` | チーム 1 スコア -1 | Actions |
| `team2_score_up` | `Ctrl+Shift+3` | `team2_score_up` | チーム 2 スコア +1 | Actions |
| `team2_score_down` | `Ctrl+Shift+4` | `team2_score_down` | チーム 2 スコア -1 | Actions |
| `reset_scores` | `Ctrl+Shift+R` | `reset_scores` | スコアリセット | Actions |
| `apply_changes` | `Ctrl+Shift+L` | `apply_changes` | 反映（保留中の編集を配信へ公開） | Actions |
| `swap_teams` | `Ctrl+Shift+S` | `swap_teams` | チーム入れ替え | Actions（構築済みのアクティブボード。S2c〜） |
| `refresh_phase_group` | `Ctrl+Shift+P` | `refresh_phase_group` | Top 8 を Start.gg から取り込み直す（大会の最後の phase のダブルエリミのグループ） | Top8Service（S3e-8〜。パネル未構築でも動く） |

### 6.3 命名規約

#### 6.3.1 アクションキー（内部 ID）

- **snake_case**（例: `team1_score_up`, `refresh_phase_group`）
- 動詞を含む（`load_set`、`reset_scores` 等）。状態名のみは禁止（× `score_up`、○ `team1_score_up`）
- アクションキーは Signal 名・SettingsManager キー・i18n 表示名の対応関係を保つ唯一の ID
- 一度公開したキー名は変更しない（ユーザー設定との互換が壊れる）

#### 6.3.2 ショートカット文字列

- 形式: `Ctrl+Shift+{key}`（推奨）/ `Alt+{key}` / `{key}` 単体（避ける）
- キー名は Qt の標準名に従う（`F1`〜`F12`、`A`〜`Z`、`0`〜`9`、`Enter`、`Space` 等）
- 変換は `AutonaHotkeys._parse_shortcut()` が行う

### 6.4 衝突回避ガイドライン

新規ホットキーを追加するときは以下の衝突を避ける:

#### 6.4.1 OS / アプリ標準と被るキー

| 避けるキー | 一般的な動作 |
|----------|---------|
| `Ctrl+S` | 保存 |
| `Ctrl+O` | 開く |
| `Ctrl+P` | 印刷 |
| `Ctrl+R` | リロード |
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Ctrl+C` / `Ctrl+V` / `Ctrl+X` | コピー・貼付・切取 |
| `Ctrl+F` | 検索 |
| `Alt+F4` | アプリ終了 |
| `Alt+Tab` | ウィンドウ切替 |
| `Win+*` | Windows OS 予約 |

`Ctrl+Shift+` プレフィックスを使えば多くの衝突を避けられる。

#### 6.4.2 配信ソフトと被るキー

OBS Studio のデフォルトホットキー（録画開始・シーン切替・ミュート等）と被ると、配信中に意図しない操作が走る。**OBS のホットキー設定とユーザー側で確認**できるよう、設定ダイアログに警告メッセージを表示する（実装タスク。§8）。

#### 6.4.3 アプリ内既存キーと被るキー

既存のホットキー一覧（§6.2）を表示しながら新規割り当てを編集できる UI が望ましい。

### 6.5 設定 UI 規約

| ルール | 内容 |
|------|------|
| **R-Hotkey-7** | ホットキー設定は PyQt の `AutonaSettingsWindow` または専用ダイアログで編集する |
| **R-Hotkey-8** | 編集 UI は「キャプチャモード」（実際に押した組み合わせを記録）と「文字列入力」の両対応が望ましい |
| **R-Hotkey-9** | 重複設定（同じキーが複数アクションに割り当て）は保存時に検出して警告 |
| **R-Hotkey-10** | リセットボタンで個別アクション・全体それぞれデフォルトに戻せる |

### 6.6 React Web UI とホットキー

React Web UI 側はオペレーターが手元デバイスから操作する用途であり、**OS グローバルホットキーは持たない**。Web UI 内のキー操作（例: スペースキーでスコア +1）が必要な場合は、ページ単位でローカルにバインドする（Web UI 専有のキーバインドは本仕様の対象外）。

### 6.7 ホットキー追加時のチェックリスト

新規ホットキーを追加する場合、以下をすべて満たす:

| ステップ | 内容 |
|---------|------|
| 1 | `AutonaHotkeysSignals` に Signal 追加 |
| 2 | `AutonaHotkeys.keys` 辞書にデフォルト追加 |
| 3 | 受け取り側 Widget で `AutonaHotkeys.signals.{action_key}.connect(self.{handler})` |
| 4 | 設定ダイアログのアクション一覧に追加（i18n 表示名込み） |
| 5 | §6.2 の表に追加（本書を更新） |
| 6 | OBS / アプリ標準との衝突確認 |

---

## 7. 拡張ガイドライン

各拡張シナリオで「触るファイル」「やること」「やらないこと」を明示する。**本章に記載されていない拡張点は禁止**（§1.2 原則 4）。新しい拡張パターンが必要な場合、まず本章を更新する。

### 7.1 新ゲームの追加

例: 新しい格闘ゲーム（仮: `streetfighter7`）を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `assets/games/streetfighter7/base_files/config.json` | ゲーム定義（キャラ・ステージ等） |
| 2 | `assets/games/streetfighter7/base_files/logo.png` | ゲームロゴ |
| 3 | `assets/games/streetfighter7/{asset_type}/...` | キャラ画像、ステージ画像など |

ゲーム別の OBS 上書き HTML/CSS（旧 `layout/scoreboard/{game}.html`）は削除済み。
新ゲーム追加時に OBS 側の見た目を個別調整したい場合は §7.2 の汎用
`scoreboard_*/` テンプレ側（各デザインの `index.html` / `index.css`）を編集する。
現行テンプレは `asset_key` でゲームのキャラ画像を差し込む汎用設計であり、
旧方式のようなゲーム名 CSS セレクタによる分岐は持たない。

#### 触らない

- React Web UI（Socket.IO で `tshGames` slice に自動配信される）
- StateManager のキー定義（動的）
- PyQt の AutonaScoreboardWidget（`game.codename` で動的に切り替わる）

### 7.2 新 OBS スコアボードレイアウトの追加

例: `scoreboard_glass_topright/` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `layout/scoreboard_glass_topright/index.html` | テンプレ HTML（`globals.js`, `main.css` 参照） |
| 2 | `layout/scoreboard_glass_topright/index.css` | レイアウト専用 CSS |
| 3 | `layout/scoreboard_glass_topright/index.js` | `Start()` / `Update(event)` 実装 |
| 4 | `layout/scoreboard_glass_topright/scoreboard_preview.png` | 選択 UI 用プレビュー画像（テンプレ選択 UI に表示する場合） |

#### 触らない

- Python 側（自動スキャンで列挙される）
- StateManager
- 共通リソース（`layout/include/`、`layout/main.css`）

#### 命名

§4.3.1 の規約に従う。新規は `layout/{用途}_{variant}/` を基本とし、scoreboard 系は `scoreboard_{placement}_{style}` を推奨。

### 7.3 新リザルト画像レイアウトの追加

例: TOP32 縦リザルト `result_vertical_top32` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `src/core/ResultService.py` の `LAYOUT_SUBDIRS` / `LAYOUT_SIZES` / `LAYOUT_NAMES` | `"vertical_top32"` を追加（`result_vertical_top32`・960 × 1080・表示名） |
| 2 | `layout/result_vertical_top32/result.html` | 新規 HTML（`async function initResult(payload)` 実装） |
| 3 | `layout/result_vertical_top32/result.css` | 新規 CSS |
| 4 | `layout/result_vertical_top32/result.js` | 新規 JS（CSS-only テンプレなら省略可） |
| 5 | `src/core/ResultService.py` の `SlotsFor()` と `SLOT_COUNTS` | TOP32 → 32 行の対応を追加（行の数はページも `layouts[].slots` で追う） |
| 6 | `src/i18n/Autona_*.ts`（手動 + `compile_translations.ps1`）と `web/src/i18n/locales/{en,ja}.json` の `result_layout_<id>` | 表示名（ページの文言が無ければ `layouts[].name`） |

#### 触らない

- StateManager
- 既存 result テンプレ
- React Web UI

### 7.4 新サムネイルテンプレの追加

例: `type_c` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `src/thumbnail/ThumbnailRenderer.py` の `TEMPLATE_SUBDIRS` | `"type_c": "thumbnail_type_c"` を追加 |
| 2 | `layout/thumbnail_type_c/thumbnail.html` | 新規 HTML（`async function initThumbnail(payload)` 実装） |
| 3 | `layout/thumbnail_type_c/thumbnail.css` | 新規 CSS |
| 4 | `layout/thumbnail_type_c/thumbnail.js` | 新規 JS |
| 5 | `assets/thumbnail_base/thumbnail_types/type_c.json` | テンプレ固有設定（フォント・配置・色トーン等） |
| 6 | `src/core/ThumbnailService.py` の `TEMPLATE_IDS` / `TEMPLATE_NAMES` | `"type_c"` と `"type_c": "Display Name"` を追加（`PATCH /api/v1/thumbnail` が受ける id と、`GET` の `templates` の名前） |
| 7 | `web/src/i18n/locales/{en,ja}.json` の `thumbnail_template_type_c` | ページの表示名（無ければ `GET` の名前 = 6 の `TEMPLATE_NAMES` を "app" で訳したものを出す。ADR-005 S4p） |

#### 触らない

- StateManager

### 7.5 新パネルの追加

例: 試合タイマー `AutonaMatchTimerWidget` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `src/AutonaMatchTimerWidget.py` | 新ウィジェットクラス（`QDockWidget` 継承、`__init__` で `setWindowTitle()` 必須） |
| 2 | `src/layout/AutonaMatchTimer.ui` | UI 定義（必要に応じて） |
| 3 | `src/Autona.py` | import 追加、`_navSections` への登録（eager ならインスタンス生成 + `setObjectName` + `dockWidgets` 追加、重いパネルなら `_CreateXxxPanel` ファクトリ）、`STATE_VERSION` インクリメント |
| 4 | `Design.md` §4.1 の表 | パネル一覧に追加 |

#### 触るファイル（任意）

| # | ファイル | 内容 |
|---|---------|------|
| 5 | `src/ui_constants.py` | ウィジェット固有のスタイル関数追加（必要時） |
| 6 | `src/StateManager.py` | キー初期化（必要時。動的キーなら不要） |

#### 注意

- `setObjectName()` は **Autona.py 側で設定**する（ウィジェット自身では設定しない、`.claude/rules/python-widgets.md`）
- `STATE_VERSION` インクリメント忘れで既存ユーザーが新ウィジェットを見られなくなる
- `signals` への `connect` は `__init__` 内で行う
- 遅延構築にする場合、パネル未構築でも成立させるべき初期化は `__init__` に置かない（§4.1.1）。
  state のシード・正規化は `src/core/StateSchema.py` の `SeedPanelState()` / `SeedBoard()` に、
  グローバルシグナル接続は Window 側に置く（`Window._SeedPanelState()` は前者の呼び出し口）

### 7.6 新 State キーパスの追加

例: `score.{N}.referee` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `Design.md` §3.1.2 のキーパスツリー | 新規キーを **静的キーパスとして明記**（§3.1.3 規約） |
| 2 | 書き込み元の Widget（PyQt） | `StateManager.Set("score.{N}.referee", value)` を呼ぶ |

#### 触るファイル（任意）

| # | ファイル | 内容 |
|---|---------|------|
| 3 | `web/src/redux/tshState.ts` | 必要なら型定義拡張（slice は自動追従） |
| 4 | OBS HTML テンプレ | `Update()` 内で `data.score[N].referee` を参照 |

#### 触らない

- `StateManager.py`（キーパスは動的、追加コードは不要）
- AutonaWebServer のルート定義（必要な場合のみ追加）

### 7.7 新ホットキーの追加

§6.7 のチェックリストに従う。

### 7.8 新言語の追加

例: 韓国語 `ko` を追加する。

#### 触るファイル

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `src/i18n/ko/...` | PyQt 側翻訳ファイル + `.qm` 生成 |
| 2 | `web/src/i18n/locales/ko.json` | React 側翻訳ファイル |
| 3 | `web/src/i18n/config.js` | `resources` に `ko` を追加、`i18n.languages` リストに追加 |
| 4 | PyQt `AutonaSettingsWindow` の言語選択 | 選択肢に「한국어」を追加 |

### 7.9 新 React ページの追加

例: `/coach-mode` を追加する。

#### 触るファイル

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `web/src/CoachModePage/index.tsx` | 新ページコンポーネント |
| 2 | `web/src/index.js` | React Router の routes に追加 |
| 3 | 必要なら `web/src/redux/...` | 新 slice |
| 4 | 必要なら `src/AutonaWebServer.py` | 新 REST/Socket ルート |

### 7.10 新外部 API / Tournament Provider 連携の追加

例: 新しい大会管理サービス `ExampleProvider` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | provider 実装ファイル（`src/Helpers/AutonaExampleProviderDataProvider.py` 等） | 大会、フェーズ、フェーズグループ、セット、選手取得 API を実装 |
| 2 | `AutonaTournamentDataProvider.py` 関連 | provider 切替・共通インターフェースへの接続 |
| 3 | StateManager キーパス（§3.1.2） | `top8.*`, `score.{N}.*` への展開仕様を確認 |
| 4 | SettingsManager | API token / endpoint / provider 選択の保存が必要なら追加（§3.3.4 命名規約） |
| 5 | PyQt 設定 UI | provider 選択や認証情報入力が必要なら追加 |

#### 注意

- 外部 API のレスポンス構造を UI に直接漏らさない
- StateManager には Autona 共通モデルに正規化して入れる
- API 認証情報は **StateManager ではなく SettingsManager 側に保存**する（§3.3.2）

### 7.11 新統計指標 / 分析項目の追加

例: `character_win_rate` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `src/core/StatsService.py` | 算出ロジック・state 書き込み・取り直しのトリガー（`PlayerChanged` / `TeamsChanged` / `RefreshAll`）を追加（headless core Step 4a で算出と書き込み、ADR-005 S3e-5 でトリガーと取得の判断が core へ移行。`AutonaStatsUtil` は取得の輸送だけの Qt アダプタ = `StatsService.fetcher`。取得が要るなら fetcher に 1 メソッド足す） |
| 2 | `web/src/StatsPage/`（統計情報ページ。ADR-005 S4k） | 表示 UI を追加（値は Socket.IO の `score.1.*` から `format.ts` で整形。新しい Qt Widgets パネルは作らない: ADR-005） |
| 3 | `Design.md` §3.1.2 | State に保存する場合のみキーパスを追記 |

#### 注意

- 派生値は **原則 read-only**。保存が必要な場合のみ StateManager に置く
- 元データと派生データを混同しない
- 計算コストが高い場合は `state_big_change` 等のイベント単位で再計算する

### 7.12 新スポンサー / アセット種別の追加

例: `coach_logo` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `user_data/games/{game}/{asset_type}/config.json` | アセット定義を追加 |
| 2 | `src/core/GameAssetCatalog.py` | 新 asset_type の解決が必要なら追加（コンボに出すなら `AutonaGameAssetManager` のモデル構築も） |
| 3 | State キーパス（§3.1.2） | State に保持する場合は静的キーパスとして追記 |
| 4 | 利用 UI / テンプレ | PyQt / OBS / 画像生成側で参照を追加 |

#### 注意

- ファイルパスはテンプレに**直接ハードコードしない**
- State には可能な限り **asset id / logical path** を保持し、実パス解決は AssetManager 側に寄せる

### 7.13 新設定項目の追加

例: `thumbnail_config.generate_metadata` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `Design.md` §3.3.4 | SettingsManager キー命名規約に従ってキーを定義 |
| 2 | SettingsManager 利用箇所 | default 値を定義 |
| 3 | PyQt 設定 UI または対象 Widget | 編集 UI を追加 |
| 4 | 利用側処理 | 設定値を読んで挙動を切り替える |

#### 注意

- **新規トップレベルキーは原則追加しない**（§3.3.4）
- StateManager に展開する場合は「**キャッシュであり正本ではない**」と明記する
- API token / アカウント情報は StateManager に置かない

---

## 8. リファクタ移行プラン

本書を正本としたとき、現状実装との差分を埋めるためのタスクを優先度別に列挙する。各タスクは独立してマージ可能な単位に分解する。

### 8.1 優先度: 高（仕様違反の解消）

| ID | タスク | 関連 § | 影響範囲 | 状態 |
|----|------|------|--------|------|
| **T-1** | `score.upset_factor`（番号なし）の二重定義を削除し、`score.{N}.upset_factor` のみに統一 | §3.1.2, §3.2.5 | `src/AutonaStatsUtil.py:176-179` 周辺 | ✅ 完了（M1a） |
| **T-2** | `commentary.{N}` Unset 時にインデックスを詰める実装に変更 | §3.1.2, §3.2.5 | `AutonaCommentaryWidget.py`（当時。S4c で削除） | ✅ 完了（M1a）→ 2026-07 リワークでスロット固定 12 名化により概念ごと消滅 |
| **T-3** | `StateManager.state` 直接参照を `GetFullState()`（ロック取得版）に置き換え | §3.1.1, §3.2.5 | `src/AutonaWebServerActions.py:41`, `src/result/main_generate_result.py`（ADR-005 S4r で削除）, `src/thumbnail/main_generate_thumbnail.py` | ✅ 完了（M1a） |
| **T-4** | `StateManager.SaveState` に `revision` を単調増加で付与、`program_state` / `program_state_update` イベントに含める | §3.2.3, §3.2.5 | `src/StateManager.py`, `src/AutonaWebServer.py`, `out/program_state.json` への埋め込み | ✅ 完了（M1b） |
| **T-5** | クライアント（OBS / React）に `revision` 連続性検証 + 不連続時の full snapshot 再取得を実装 | §3.2.3 | `layout/include/globals.js`, `web/src/redux/tshState.ts`, `web/src/ScoreboardPage/websocketInit.ts` | ✅ 完了（M1b） |
| **T-6** | React の `applySavedDeltas` バッチ間隔を 1000ms → **既定 100ms / 上限 200ms** に変更 | §2.2 R-React-6, §3.2.4 | `web/src/ScoreboardPage/websocketInit.ts:82-87` | ✅ 完了（M1a） |
| **T-7** | accent 色を `#F5A545`（オレンジ）→ `#8A1A2C`（クリムゾン）に統合 | §5.1 | `web/src/themes.js` の DARK/LIGHT TOKENS、`web/public/manifest.json`、`web/public/index.html` | ✅ 完了（M1a） |

### 8.2 優先度: 中（設計負債の解消）

| ID | タスク | 関連 § | 影響範囲 | 状態 |
|----|------|------|--------|------|
| **T-8** | ~~React Web UI のテーマ切替~~ | §5.4 | — | ❌ 撤回（2026-06-28）。切替が 30s フリーズの原因のため機能ごと削除しダーク固定化（§5.4） |
| **T-9** | ~~`ui.light_mode` broadcast 機構~~ | §5.4 | — | ❌ 撤回（2026-06-28、§5.4） |
| **T-10** | ~~テーマ切替の PyQt⇄React 同期~~ | §5.4 | — | ❌ 撤回（2026-06-28、§5.4） |
| **T-11** | `BlockSaving()` / `ReleaseSaving()` の手動ペアを `with StateManager.SaveBlock():` に置き換え | §3.2.2 R-Sync-2 | `AutonaScoreboardManager` / `StatsUtil` / `BasePlayerWidget` / `PlayerListSlotWidget` / `PlayerList` / `PlayerListWidget` / `ScoreboardWidget` の小〜中規模ペア。既に try/finally で守られている長大処理と lambda タプル式 / 巨大 __init__ は意図的にスキップ | ✅ 完了（M3） |
| **T-12** | OBS HTML poll モード（`UpdateData()` 64ms ポーリング）の degraded 化と revision 検証実装 | §2.3 R-OBS-3, §3.2.3 | `layout/include/globals.js` | ✅ 完了（M1b） |
| **T-13** | React `size="small"` を全フォーム要素に徹底（指摘事項） | §2.2 R-React-5 | `CountrySelector` / `CountryStateSelector` / `NumberInput` / `Header` / `CurrentSet` / `CharSelector` / `UpcomingSets` / `Team` / `index.tsx` | ✅ 完了（M3） |
| **T-14** | ハードコード hex 色をトークン参照に置換（PyQt 12+ 箇所、React App.css 数箇所） | §5.1.6 | `src/AutonaBracketView.py` / `src/AutonaBracketWidget.py` を `UI.palette()` 参照化、`web/src/index.css` 固定色を CssBaseline override に委譲、`web/src/ScoreboardPage/Header.jsx` 内 rgba を `theme.palette.divider` / `text.disabled` に | ✅ 完了（M3） |
| **T-15** | `AutonaAnalysisWidget` の仕様反映済み確認と回帰テスト追加 | §4.1 | 試合一覧は core の `AnalysisService`（ADR-005 S4j）と React のページ（S4k）に移り、Qt 版は削除。golden（`tests/fixtures/analysis_rows/golden.json`）・`tests/test_analysis_rows.py` / `test_analysis_service.py` / `test_api_v1_analysis.py`・jest の `analysisRows.test.js` が回帰を pin | ✅ 完了（ADR-005 S4j / S4k） |
| **T-16** | サムネイルメタデータ（`_title.txt` / `_desc.txt`）を `thumbnail_config.generate_metadata` の設定で制御化 | §4.4.4 | `src/thumbnail/main_generate_thumbnail.py:save_result`, `AutonaThumbnailSettingsWidget` + `AutonaThumbnailSettings.ui` に `generateMetadataCheck` を追加（default true） | ✅ 完了（M3） |
| **T-17** | OBS テンプレ設定ダイアログに「OBS Studio 標準ホットキーとの衝突警告」を追加 | §6.4.2 | `src/Helpers/AutonaHotkeyValidator.py` を新設し、`Settings/SettingsWidget.py` の hotkey ハンドラで Autona 内重複は error / OS・OBS 想定衝突は warning ダイアログを出す | ✅ 完了（M3） |

### 8.3 優先度: 低（長期的な構造改善）

| ID | タスク | 関連 § | 影響範囲 |
|----|------|------|--------|
| **T-18** | **PyQt 書き込みの Actions 層移行**: PyQt Widget の `StateManager.Set` 直接呼び出しを Actions 層に移行する。WebServer 非依存名 `AutonaActions` / `StateActions` への改名も検討。Step 1（core 境界確立・Qt-free pin）完了 2026-08-28 | §3.2.2 R-Sync-1 | 大規模リファクタ。`src/AutonaWebServerActions.py` の独立化 + 全 PyQt Widget の書き込み経路差し替え |
| **T-19** | 動的キーパス（`objectName` 依存キー）の段階的な静的化。新規追加分から §3.1.2 に明記する運用 → ✅ **完了**（Headless Core Step 5 第 1〜3 弾、2026-09-01/02。`StateSchema.DYNAMIC_KEY_FAMILIES` は空）。以降は「新規キーは `StateSchema` に定数を置く」運用のみ | §3.1.3 | 新規キー追加時のレビューで運用 |
| **T-20** | OBS テンプレの命名軸統一: 新規追加分から `scoreboard_{placement}_{style}` に寄せる | §4.3.1 | 新規追加時のレビューで運用 |
| **T-21** | フォント定義の整理: PyQt の `FONT_FAMILY_CONDENSED`（Roboto Condensed）を React にも追加するか、削除するか決定 | §5.2.1 | `web/src/themes.js`、`src/ui_constants.py` |
| **T-22** | `danger` / `negative` トークンを `brand` から分離するか検討（エラー・削除確認・危険操作とブランド強調が同色になる懸念の解消） | §5.1.4 | `src/ui_constants.py` 側は分離済み（`negative #F04E54`）。`web/src/themes.js` も Web UI 刷新 Phase 1（2026-07）で `error = negative` に反映 → ✅ 完了 |
| **T-23** | React MUI theme の `shape.borderRadius` を `RADIUS_MD = 5px` に合わせる（MUI コンポーネントが参照するデフォルトを統合トークンに揃える） | §5.3.2 | `web/src/themes.js` → ✅ 完了（Web UI 刷新 Phase 1、2026-07） |

### 8.4 マイルストーン

| マイルストーン | 含むタスク | 完了条件 |
|------------|-----------|--------|
| **M1a: Quick compliance fixes** | T-1, T-2, T-3, T-6, T-7 | 個別に独立 PR でマージ可能。upset_factor 統一・commentary 欠番解消・StateManager.state 直接参照置換・React batch 100ms 化・accent 色統合が完了する |
| **M1b: Sync reliability baseline** | T-4, T-5, T-12 | StateManager / AutonaWebServer / OBS globals.js / React websocketInit にまたがる同期基盤の改修。revision 付与・連続性検証・full snapshot 再取得・degraded poll 整理がまとめて完了し、React の遅延が 300ms 以内に収まる |
| **M2: テーマ統合（中期）** | T-8〜T-10 | ❌ 撤回（2026-06-28）。切替が 30s フリーズの原因のため機能削除・ダーク固定化（§5.4） |
| **M3: 設計負債の解消（中期）** | T-11〜T-17 | ハードコード色・遅延・size 不統一が解消される |
| **M4: 構造改革（長期）** | T-18〜T-23 | Actions 層が独立、動的キーパスが減少、命名軸が統一される。`negative`/`brand` 分離、MUI `shape.borderRadius` 統合判断 |

### 8.5 リファクタの進め方

| ルール | 内容 |
|------|------|
| 1 | 各タスクは独立 PR でマージ可能な単位に分割 |
| 2 | M1 は同一スプリントでまとめて実施するのが望ましい（互いに依存するため） |
| 3 | M2/M3 は順序自由 |
| 4 | M4 は別途設計フェーズが必要（PR 前に追加 Design ドキュメントを書く） |
| 5 | 各タスク完了時に本書（Design.md）の該当箇所と現実装の乖離が解消されていることを確認する |

### 8.6 今後のデザインシステム改善（Future UX Improvement Themes）

今後、OBS Studio / ClickUp などの成熟した制作・業務管理ツールを参考に、Autona の UI/UX 改善を別フェーズで検討する。

主な検討対象:
- OBS Studio 的なドック配置・プレビュー・操作パネルの整理
- ClickUp 的な情報密度、サイドバー、状態表示、タスク/設定の階層設計
- 配信中オペレーションにおける視認性・誤操作防止・ショートカット導線
- 初心者向け onboarding / setup flow
- 大会運営者向けの作業フロー最適化

ただし、本書（Design.md）の責務境界・StateManager 規約・テーマトークン・拡張規約を前提とし、UI/UX 改善は別ブランチで実施する。

---
