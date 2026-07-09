# Autona Design Document

> **本書の位置づけ**
> Autona の UI 実装方式・データモデル・画面仕様・デザインシステム・ホットキー仕様の「あるべき姿」を定義する正本。
> 実装と乖離する場合は本書が優先される。差異は別途リファクタ計画（[.claude/plans/ui-design-refactor.md](.claude/plans/ui-design-refactor.md)）で吸収する。
>
> 本書は Phase A（現状洗い出し）と Phase B（問題整理）の調査結果に基づく。
> 詳細な事実情報は `.claude/architecture/phase-a-b/ui-{pyqt,obs-layouts,image-renderers,web-react,styles-theme,problems}.md` を参照。

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
- `layout/scoreboard/` = OBS ブラウザソース用テンプレ（上流踏襲、Autona ではユーザーが OBS に貼って使う）

---

## 2. UI レイヤー定義

Autona は 4 つの UI レイヤーで構成される。各レイヤーは独立した実装言語・実装技術を持ち、`StateManager` を介してのみ連携する。

```
                         ┌──────────────────────────────┐
                         │     StateManager (Python)    │
                         │  単一の真実の源 / RLock 保護    │
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

**ドックウィジェット一覧** — 詳細は §4.1 参照。

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
| **R-React-8** | 楽観的スコアエコー（`useScoreSubmit.ts`）: スコア±はタップ即時にローカル表示を更新し、**絶対値**で `POST /score` する。①サーバーデルタの値がローカル送信値と一致したら pending 解除 ②連打は in-flight 中最新値のみ保持し応答後に 1 回再送（シリアライズ）③失敗時はサーバー値へ revert + Snackbar ④3 秒でタイムアウトし pending 解除（サーバーが正）。R-Sync-4（後勝ち）の範囲内のため revision 突き合わせ・ロールバックログは持たない |
| **R-React-9** | **即時反映モデル**: ScoreboardPage の全入力（選手情報・Phase/Match/Best Of・Losers・チーム名）は入力確定（blur / Enter / 選択）で即送信する。送信ボタンは置かない（デスクトップの `editingFinished` と同モデル）。サーバーエコーによるフォーム巻き戻りは「カード内にフォーカスがある間は表示フィールドを上書きしない」ガードで抑止する（`Player.jsx`） |
| **R-React-10** | フィールド表示トグルは **`display_options.show_*`（StateManager 経由で全クライアント共有）が正**。Web の Eye メニューは `POST /display-option`（Actions 層 `set_display_option` → 各ボードの eye アクション駆動）で書き込み、ローカルの visibility 状態は持たない。キーとデフォルトは `AutonaScoreboardWidget.elements` と `web/src/ScoreboardPage/displayOptions.js` で二重管理（変更時は両方更新） |

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
| **R-Img-1** | レンダラーは Singleton（`ThumbnailRenderer.instance()`, `ResultRenderer.instance()`）として実装 |
| **R-Img-2** | テンプレ選択は **プレビュー側**（プレビュー画像の直上）にコンボボックスを置く（#29 で確定済み） |
| **R-Img-3** | テンプレ追加時は `TEMPLATE_SUBDIRS`（Thumbnail）/ `LAYOUTS`（Result）への登録 + テンプレディレクトリ作成のみで完結する |
| **R-Img-4** | レンダリング完了は HTML 側で `document.title = "RENDER_READY"` に変更してシグナル化 |
| **R-Img-5** | Python から HTML への入力は `initThumbnail(payload)` / `initResult(payload)` の関数経由で受け渡す |
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
| Top 8 ブラケット編集 | ◎ | × | × | × |
| Top 8 ブラケット表示（配信） | × | × | ◎ | × |

◎ = 主担当 / △ = 任意 / × = 担当しない

#### 禁止事項

- **無秩序な機能重複は禁止**。両方で扱う場合は本書に「両方で扱う理由・正本となるレイヤー・競合時の優先順位」を明記する。例: トーナメント設定は PyQt 専有、スコア操作は両方（正本: StateManager、書き込みは AutonaWebServerActions に集約、PyQt と React の同時操作時は最後の書き込みが勝つ）。
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

**規約**:
- キーパスは **ドット区切り文字列**（例: `"score.1.team.1.player.1.gamerTag"`）
- 数値添字も**文字列として扱う**（`"1"` であって `1` ではない）
- `StateManager.state` への直接参照は **禁止**。必ず `Get` / `GetFullState()` 経由。

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
│   ├── losers                       W   (動的: QCheckBox.objectName)
│   └── player.{M}                   D
│       ├── name                     R
│       ├── gamerTag                 W
│       ├── team                     W   (所属チーム/スポンサー名)
│       ├── seed                     W R
│       ├── id                       W R
│       ├── city                     W R
│       ├── birthday                 W   (bool)
│       ├── custom_textbox           W
│       ├── online_avatar            W R
│       ├── avatar                   W
│       ├── mergedName               W
│       ├── mergedOnlyName           W
│       ├── character.{K}            W R   (dict: name, en_name, assets, skin, variant)
│       ├── mains                    W
│       ├── country_code             W
│       ├── state_code               W
│       └── sponsor_logo(s)          W D
├── best_of                          W R
├── best_of_short_text               W
├── best_of_text                     W
├── first_to / _short_text / _text   W
├── match                            W R
├── phase                            W R
├── top_n                            W R
├── stream_url                       W R
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

旧 `bracket.*` は Top 8 専用化（2026-07）で廃止。`AutonaTop8Widget` が起動時に
`StateManager.Unset("bracket")` で残留キーを掃除する。トポロジーと進出伝播の正本は
`src/AutonaTop8Model.py`（FGC 標準ダブルエリミ: Winners 4 名 + Losers 4 名）。

```
top8
├── phaseLabel                       W R   (表示ラベル。既定 "TOP 8")
├── players.{1..8}                   W R   (スロット位置固定: 1/2=WSF1, 3/4=WSF2,
│   │                                       5/6=LR1A, 7/8=LR1B)
│   ├── name / sponsor               W R
│   ├── country                      W R   ({code, asset} — AutonaCountryHelper 形式)
│   └── characters.{K}               W R   (charData: assets 込み)
└── matches.{wsf1|wsf2|wf|lr1a|lr1b|lqf1|lqf2|lsf|lf|gf|gf_reset}
    ├── name                         W R   (ラウンド表示名)
    ├── slots                        W R   ([int,int] 選手スロット 1..8 / 0=TBD。
    │                                       AutonaTop8Model.Recompute が導出)
    ├── score                        W R
    ├── finished                     W R
    └── active                       W R   (gf_reset のみ意味を持つ)
```

##### `commentary` — 実況・解説

実況・解説機能の全面リワーク（2026-07）でスキーマ刷新。スロット固定 8 名・
欠番概念なし（旧「Unset 時に詰める」運用は廃止）。正規化・表示選択ルールの
正本は `src/AutonaCommentaryModel.py`。旧スキーマ（seed / country / character /
mains / custom_textbox 等）は起動時の `NormalizeState` でマイグレーションされる
（custom_textbox → note、その他は破棄）。状態は再起動をまたいで永続化される。

```
commentary
└── {1..8}                           W R   (スロット固定・常時 8 エントリ)
    ├── name                         W R   (ゲーマータグ)
    ├── team                         W R   (チーム / 所属)
    ├── real_name                    W R
    ├── twitter                      W R   (@ なし)
    ├── role                         W R   ("実況"|"解説"|"選手"|"主催"|"")
    ├── note                         W R   (自由コメント。例: 本年度ランキング2位)
    └── display                      W R   (int。0=非表示、1..4=オーバーレイ表示順。
                                            チェックした順に採番、左から表示)
```

##### `tournamentInfo`

```
tournamentInfo
├── tournamentName
├── themeColor / accentColor       ※既存テンプレ互換のため StateManager に展開する表示用キャッシュ。
│                                    永続化の正本ではない（正本は SettingsManager.general.theme_color / accent_color）。
│                                    名前から「大会別テーマ色」と誤読されないよう注意。
└── {動的キー}                     (Qt 入力フィールドの objectName + API 取得データ)
```

##### `ui` — UI 表示用キャッシュ

旧 `ui.light_mode` キャッシュはテーマ切替機能の削除に伴い廃止（§5.4、ダーク固定）。
現在 `ui` 配下に broadcast 用キーは無い。

##### `notes` / `team_battle` / `layout_options` / `provider_videogame` / `completed_sets` / `streamQueue` / `currentStream` / `result_players`

> 旧 `player_list.*`（PLAYERS 順位ロスター編集）は機能ごと廃止（layout/top_8 と共に削除、
> Top-8 は `top8.*` → `layout/bracket_top8/` に一本化）。起動時に
> `AutonaPlayerBrowserWidget.__init__` が残留キーを Unset する。

詳細は [.claude/architecture/phase-a-b/state-keys.md](.claude/architecture/phase-a-b/state-keys.md) 参照。

#### 3.1.3 動的キーパスの規約

以下のキーは Qt の `objectName()` から動的に決まる:

| 動的キー | 解決元 |
|--------|--------|
| `score.{N}.team.{T}.{QCheckBox.objectName}` | AutonaScoreboardWidget の team 列の QCheckBox |
| `score.{N}.team.{T}.player.{M}.{QLineEdit.objectName}` | AutonaScoreboardPlayerWidget の全 QLineEdit |
| `score.{N}.{QComboBox.objectName}` | AutonaScoreboardWidget の scoreColumn の QComboBox |
| `tournamentInfo.{Q*.objectName}` | AutonaTournamentInfoWidget のフィールド全般 |

**規約**:
- 既存の動的キーパスは **維持**する（`.ui` ファイルの `objectName` を変更するとキーパスが変わり、保存済み state と互換性が壊れるため安易に変えない。やむを得ず変える場合は `STATE_VERSION` をインクリメントする）。
- **新規追加は §3.1.2 に静的キーパスとして明記する方針**。動的キーパスの新規追加は、Qt Widget の単純な入力フィールド保存など、UI 構造と state 構造を一致させる合理性がある場合に限定する。

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
│ AutonaWebServerActions          │
│ （※将来的な書き込みハブ。       │
│   現行は React/OBS/外部のみ経由、 │
│   PyQt は直接 Set を暫定許可）   │
└────────────┬────────────────────┘
             │ StateManager.Set / Unset / SaveBlock
             ▼
┌─────────────────────────────────┐
│ StateManager                    │
│ - DeepDiff で delta 算出        │
│ - revision を単調増加で付与      │
│ - adaptive debounce (50/100/200ms)│
└──┬───────────┬──────────────────┘
   │           │
   │ delta     │ snapshot
   ▼           ▼
┌──────────┐ ┌────────────────────┐
│ Socket.IO│ │ ./out/program_state│
│ broadcast│ │ .json (degraded用) │
└──┬───────┘ └─────┬──────────────┘
   │               │
   ▼               ▼
PyQt UI    OBS HTML / React Web UI / WebEngine 画像生成
(直接接続)  (Socket.IO + revision 検証)
```

#### 3.2.2 書き込み規約

| ルール | 内容 |
|------|------|
| **R-Sync-1** | 書き込み経路の規約: <br>① React Web UI / 外部システムからの書き込みは **必ず AutonaWebServerActions 経由**。<br>② OBS HTML は原則 **read-only**。例外的に書き込みを許可する場合も AutonaWebServerActions 経由とする。<br>③ PyQt Widget は現行互換のため `StateManager.Set` 直接呼び出しを **暫定許可**。<br>④ 新規の複合操作・正規化を伴う操作は Actions 層へ実装する。<br>⑤ 将来的には PyQt のドメイン状態書き込みも Actions 層へ移行する（§8 の移行プラン参照、将来名称は `AutonaActions` / `StateActions` のように WebServer 非依存名に寄せる）。 |
| **R-Sync-2** | バッチ書き込みは `with StateManager.SaveBlock():` を使う。`BlockSaving()` / `ReleaseSaving()` の手動ペアは避ける（例外時の漏れリスク） |
| **R-Sync-3** | 同一トランザクション内の複数 `Set` は SaveBlock で囲み、delta を 1 回にまとめる |
| **R-Sync-4** | 競合時の優先順位: **後勝ち**（最後の書き込みで上書き）。同時操作の検出・拒否は行わない |

#### 3.2.3 配信規約（broadcast）

| ルール | 内容 |
|------|------|
| **R-Sync-5** | StateManager は変更ごとに **`revision`**（単調増加の整数）を付与する |
| **R-Sync-6** | `program_state_update` イベントには `{ revision, first_revision, delta }` を含める。サーバーの delta coalescing は複数の `state_updated` emit（= 複数 revision）を 1 メッセージへ結合するため、payload は連続区間 `[first_revision, revision]` を表す。`first_revision` 欠落時（旧 payload）はクライアントが `revision` 単一と見なす。同一 revision 内で複数 chunk に分けて送る必要がある場合は `{ revision, chunk_index, chunk_total, delta }` の形式で chunk 順序を保証する |
| **R-Sync-7** | `program_state` イベント（full snapshot）には `{ revision, state }` を含める |
| **R-Sync-8** | クライアントは受信 payload の区間 `[first_revision, revision]` が次に期待する revision（前回適用 +1）を含むことを検証する。含まなければ `program_state` を再リクエストして full snapshot で再構築する。（旧規約の「単一 revision の前回 +1 検証」は coalescing と両立せず、結合 payload をギャップと誤検知して全量再取得が多発していた） |
| **R-Sync-9** | reconnect 時はクライアントが必ず full snapshot を再取得する |
| **R-Sync-10** | adaptive debounce: <10 delta = 50ms / 10-99 = 100ms / 100+ = 200ms + `state_big_change` シグナル発火。**leading-edge flush**: 直近 flush から 50ms 以上静かな状態で届いた最初の delta は即時送出する（単発操作の体感遅延をゼロ化。バーストは従来どおり trailing debounce） |

#### 3.2.4 同期遅延の上限

ユーザー操作から各 UI に反映されるまでの遅延上限:

| 経路 | 上限 |
|-----|------|
| PyQt → StateManager | < 1ms |
| StateManager → OBS HTML（Socket.IO） | 200ms（debounce 上限） |
| StateManager → OBS HTML（degraded poll） | 200ms（debounce）+ 64ms（poll 周期） |
| StateManager → React Web UI | 単発操作: **~0ms**（leading-edge flush + 到着駆動適用）/ バースト: 200ms（debounce 上限） |
| StateManager → WebEngine 画像生成 | 0ms（prepare_data 時の snapshot） |
| React → StateManager | **目標値 ~50ms**（API/Socket → AutonaWebServerActions → Set）。実値はネットワーク依存で、無線・低速 LAN では大きくなる。スコア±は楽観的更新（R-React-8）により**表示は 0ms** |

**注**: 旧実装の React 側 100ms 固定バッチは到着駆動化により廃止（§2.2 R-React-6、2026-07 Web UI 刷新 Phase 3）。

#### 3.2.5 既知の不整合 → 規約上の扱い

Phase B-b で検出された問題は、本書を正本として以下のように扱う:

| 問題 | 規約上の扱い |
|-----|------------|
| `score.upset_factor`（番号なし）二重定義 | 番号なし版は **削除済**（§8 T-1 完了） |
| `commentary.{N}` インデックス欠番 | スロット固定 8 名化（2026-07 リワーク）で欠番概念ごと消滅（旧: Unset 時に詰める） |
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
| ドックウィジェット配置 | QSettings（`windowState_v{STATE_VERSION}`） | バイナリ | `Window` | 自動保存 |
| 選手 DB | `./user_data/local_players.json` | JSON 配列 | `AutonaPlayerDB` | PyQt の Player List + 直接 JSON 編集 |
| ルールセット（キャッシュ） | `./user_data/rulesets.json` | JSON | tournament provider | API 取得時に自動更新 |
| ゲームアセット定義 | `./user_data/games/{game}/{asset_type}/config.json` | JSON | `AutonaGameAssetManager` | アセット追加時に手動編集 |
| サムネテンプレ定義 | `./assets/thumbnail_base/thumbnail_types/{id}.json` | JSON | （定義のみ） | テンプレ追加時に手動作成 |
| OBS テンプレのグローバル設定 | `./layout/settings.json` | JSON | OBS layout runtime | テンプレ提供者または運用者が編集 |
| OBS テンプレのローカル設定 | `./layout/{template}/settings.json` | JSON | OBS layout runtime | テンプレ提供者または運用者が編集 |

#### 3.3.2 設定の正本（Single Source of Truth）

| カテゴリ | 正本 | キャッシュ可能な経路 |
|---------|-----|------------------|
| ユーザー設定全般 | `SettingsManager` (`./user_data/settings.json`) | StateManager に展開してよい（`tournamentInfo.themeColor` 等） |
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
| `general.*` | アプリ全般（disable_thumbnail_widget, theme_color, accent_color, webserver_port 等） |
| `hotkeys.*` | ホットキー（hotkeys_enabled とアクションごとのキー） |
| `display_options.*` | 表示切替（プレイヤー欄のフィールド表示等） |
| `thumbnail_config.*` | サムネイル生成設定（template_id, background_path, use_sponsors 等） |
| `result_config.*` | リザルト生成設定（layout, accent_color, logo_path 等） |
| `bsky_account.*` | Bluesky 連携 |
| `update.*` | アップデーター（skipped_version 等） |
| その他トップレベル（`program_language`, `TOURNAMENT_URL` 等） | 個別 |

新規キーは原則ネームスペース付きで作成し、トップレベルは増やさない。

#### 3.3.5 STATE_VERSION とマイグレーション

- `windowState_v{N}` のキー名は `STATE_VERSION = 8` に依存（[Autona.py:183](src/Autona.py#L183)）
- `STATE_VERSION` をインクリメントすると既存ユーザーのドック配置が初期化される
- インクリメントが必要なケース:
  - ドックウィジェットの追加・削除
  - `.ui` ファイルの `objectName` 変更（state 互換が壊れる時）
- 同 `STATE_VERSION` 内では既存 state との互換性を維持する

---

## 4. 画面仕様

### 4.1 PyQt ドックウィジェット一覧と責務

`Autona.py` の `Window`（QMainWindow、`STATE_VERSION = 12`）が以下のドックを管理する。

| # | objectName | クラス | 主な責務 | 入力先 (Set) | 表示元 (Get) | デフォルト表示 |
|---|------------|-------|---------|------------|------------|--------------|
| 1 | `Tournament Info` | `AutonaTournamentInfoWidget` | 大会名・日付・URL・テーマ色など大会メタデータの編集 | `tournamentInfo.*` | `tournamentInfo.*` | ✓（最初に表示） |
| 2 | `Thumbnail Settings` | `AutonaThumbnailSettingsWidget` | サムネイルテンプレ選択・背景指定・プレビュー・PNG/JPG 保存・クリップボードコピー | `thumbnail_config.*`（SettingsManager） | `score.*`, `tournamentInfo.*`, `thumbnail_config.*` | ✓（条件付き） |
| 3 | `Result Settings` | `AutonaResultSettingsWidget` | リザルトレイアウト選択・選手入力・プレビュー・PNG/JPG 保存・クリップボードコピー | `result_config.*`, `result_players` | `tournamentInfo.*`, `result_players` | ✓ |
| 4 | `Top8` | `AutonaTop8Widget` | Top 8 ダブルエリミの選手・試合スコア編集、Start.gg 読み込み、OBS 用 URL 案内・プレビュー | `top8.*` | `top8.*`, `tournamentInfo.*` | ✓ |
| 5 | `Scoreboard` | `AutonaScoreboardManager` → `AutonaScoreboardWidget` / `AutonaCrewBattleWidget` | board1=1v1、board2=team、board3=crew のスコア入力・選手編集・セット読み込み | `score.{N}.*` | `score.{N}.*`, `game.*` | ✓ |
| 6 | `Commentary` | `AutonaCommentaryWidget` | 実況・解説者 8 スロットの編集（役割・コメント・表示トグル）、OBS 用 URL 案内・プレビュー | `commentary.{1..8}.*` | `commentary.{1..8}.*`, `tournamentInfo.*` | ✓ |
| 7 | `Statistics` | `AutonaStatsWidget` | 内部 `QTabWidget` で複数ビューを集約（下記 §4.1.0） | （read-only） | `score.{N}.recent_sets`, `last_sets`, `upset_factor` ほか | ✓ |
| 8 | `Players` | `AutonaPlayerBrowserWidget` | 選手一覧（2タブ）: トーナメント出場選手（provider.top_entrants ＋ ローカルDB 突き合わせ、シード順、P1/P2 セット）／登録済み選手（ローカル選手DB の閲覧・検索・新規登録・編集・削除） | `player_editor.1.*`（編集ダイアログの一時パス、close で Unset） | `AutonaPlayerDB.database`, `provider.top_entrants`（state 非依存） | ✓ |

> **NOTE タブ（`AutonaNotesWidget`）は廃止**。`ANLYSE`（`AutonaAnalysisWidget`）は**トップレベルのドックではなく**、`Statistics` ドック内の `QTabWidget` のサブタブとして埋め込まれている（`QDockWidget` → `QWidget` パネルに降格）。
> 旧 `PLAYERS`（`AutonaPlayerListWidget`、Top-8 順位ロスター編集）は機能ごと削除された。
> スコアボードの「1選手あたりのキャラ数」スピンボックスはスコアボード自身が所有する（初期値 3）。

##### 4.1.0 Statistics ドック内タブ（`AutonaStatsWidget.tabs`）

| サブタブ | 担当パネル | 入力先 / 表示元 |
|---------|-----------|---------------|
| Head-to-Head (Recent Sets) | （`AutonaStatsWidget` 内蔵） | `score.{N}.recent_sets` ほか（read-only） |
| Last Sets | （内蔵） | `score.{N}.last_sets`（read-only） |
| Tournament History | （内蔵） | `score.{N}.history_sets`（read-only） |
| この大会の試合一覧 | `AutonaAnalysisWidget` パネル | Start.gg 全フェーズグループの自前フェッチのみ（state 非依存、read-only）。TournamentDataProvider のシグナル購読 |

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
- objectName を変更する場合は `STATE_VERSION` インクリメントに加えて、必要に応じて**保存済み state / QSettings のマイグレーション処理を実装**する
- `Autona.py` の `self._navPanels` に `(panel, 'icon.svg')` を表示順で追加する
  （ナビ項目ラベルは `panel.windowTitle()`、アイコンは `assets/icons/`）

#### 4.1.2 ドック内の標準パターン

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

#### 4.2.1 ScoreboardPage コンポーネント階層

```
ScoreboardPage (function component)
├── Header (AppBar)
│   ├── Game selector (Redux: tshGames)
│   ├── Tournament URL loader (REST: /set-tournament)
│   ├── Auto-update toggle (REST: /scoreboard{N}-pull-stream)
│   └── Visibility menu (Redux: visibility)
├── CurrentSet
│   ├── Player ×2
│   │   ├── CharSelector (Redux: tshCharacters)
│   │   └── CountryStateSelector (Redux: tshCountries)
│   └── SetScore (NumberInput ×2 + match/phase/best_of)
├── UpcomingSets
└── Clear All Button (REST: /scoreboard{N}-clear-all)
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
| `commentators/` | 実況・解説者バー（`commentary.{1..8}` の display=ON を 1〜4 名可変表示） | index.html 一本化（旧 tag*.html 3 バリアントは 2026-07 リワークで削除） |
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
| `settings.json` | テンプレ ローカル設定 | △（必要時） |

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
| `--theme-color` | `#1D1B18` | テーマ色 |
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

##### `ResultRenderer.LAYOUTS` の規約

```python
LAYOUTS = {
    "horizontal":     ("result",              1920, 1080),
    "horizontal_16":  ("result_top16",        1920, 1080),
    "vertical":       ("result_vertical",      960, 1080),
    "vertical_geki":  ("result_vertical_geki", 960, 1080),
    "team":           ("result_team",         1920, 1080),
    "team_split":     ("result_team_split",   1920, 1080),
    # 新規追加例: "vertical_custom": ("result_vertical_custom", 960, 1080),
}
DEFAULT_LAYOUT = "horizontal"
```

レイアウト名は `AutonaResultSettingsWidget` の layoutCombo の currentData に対応する（§7.3）。

##### team レイアウト（チーム / Crewbattle）

- 横向き Top-8 グリッド（`result/result.css`）を土台に、各順位枠を「チームカード」として描画する。`result/result.js` の `placeTeams()` が `payload.layout === "team"` のとき呼ばれ、枠ごとにチームのメンバー（2-5 人）のメインファイターを横並びに配置し、各ファイターの右下に選手名を反時計回り 90°回転（下→上読み）で重ね、枠下にチーム名プレートを置く。選手名は全角 5-10 文字を縦長に収める想定で、ファイター下部に暗いフェードを敷いて視認性を確保する。styling は `result_team/result.css`（base を `@import` してチーム枠を上書き）。
- データは `result_players[N]` に `members: [{ name, character }, ...]` を持つ（`name` はチーム名）。`AutonaResultSettingsWidget` の Teams グループ（順位 × 最大5メンバー）で手入力でき、Start.gg のチーム standings からの自動取得にも対応する（ロスター全員 + mains）。
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
| Thumbnail preview | `AutonaResolve("tmp/thumbnail")` | `template.png`（毎回上書き） |
| Result final | `./out/results/` | `{tournament}-top{n}-{YYYYMMDDTHHmmssZ}.png` |
| Result preview | `AutonaResolve("tmp/result")` | `preview.png`（毎回上書き） |

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

#### 5.5.3 大会別カスタマイズ

「大会ブランドに合わせたテーマカラー」を OBS テンプレに反映する仕組みは以下の経路で実現する:
- `tournamentInfo.themeColor` / `accentColor` を CSS 変数経由で OBS HTML に注入（既存テンプレ互換）
- 値の正本は `SettingsManager.general.theme_color / accent_color`（§3.1.2 注釈、§3.3.2）

##### 新規テンプレでの利用ルール

| ルール | 内容 |
|------|------|
| **R-Brand-1** | 新規 OBS / 画像生成テンプレでは `tournamentInfo.themeColor` / `accentColor` を **JS から直接読まない**。`--theme-color` / `--accent-color` などの CSS 変数経由でのみ利用する |
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

### 6.2 デフォルトホットキー一覧

| アクションキー | デフォルト | Signal | 動作 |
|--------------|-----------|--------|------|
| `load_set` | `Ctrl+Shift+O` | `AutonaHotkeys.signals.load_set` | 次のセットを読み込み |
| `team1_score_up` | `Ctrl+Shift+1` | `team1_score_up` | チーム 1 スコア +1 |
| `team1_score_down` | `Ctrl+Shift+2` | `team1_score_down` | チーム 1 スコア -1 |
| `team2_score_up` | `Ctrl+Shift+3` | `team2_score_up` | チーム 2 スコア +1 |
| `team2_score_down` | `Ctrl+Shift+4` | `team2_score_down` | チーム 2 スコア -1 |
| `reset_scores` | `Ctrl+Shift+R` | `reset_scores` | スコアリセット |
| `swap_teams` | `Ctrl+Shift+S` | `swap_teams` | チーム入れ替え |
| `refresh_phase_group` | `Ctrl+Shift+P` | `refresh_phase_group` | Top 8 の選択中フェーズグループを Start.gg から再取得 |

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

#### 触るファイル（推奨）

| # | ファイル | 内容 |
|---|---------|------|
| 4 | `layout/scoreboard/streetfighter7.html` | 既存共通テンプレを参照する HTML（`<body class="streetfighter7">`） |
| 5 | `layout/scoreboard/index.css` | ゲーム名セレクタ（`.streetfighter7 { ... }`）で配色・配置の差分を追加 |

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
| 1 | `src/result/ResultRenderer.py` の `LAYOUTS` | `"vertical_top32": ("result_vertical_top32", 960, 1080)` を追加 |
| 2 | `layout/result_vertical_top32/result.html` | 新規 HTML（`async function initResult(payload)` 実装） |
| 3 | `layout/result_vertical_top32/result.css` | 新規 CSS |
| 4 | `layout/result_vertical_top32/result.js` | 新規 JS（CSS-only テンプレなら省略可） |
| 5 | `src/AutonaResultSettingsWidget.py` の `layoutCombo` | `addItem("Vertical TOP32", "vertical_top32")` を追加 |
| 6 | `_max_players_for_layout()` | TOP32 → 32 人の対応行を追加 |

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
| 6 | `src/AutonaThumbnailSettingsWidget.py` の `_template_options` | `("type_c", "Display Name")` を追加 |

#### 触らない

- StateManager
- React Web UI

### 7.5 新ドックウィジェットの追加

例: 試合タイマー `AutonaMatchTimerWidget` を追加する。

#### 触るファイル（必須）

| # | ファイル | 内容 |
|---|---------|------|
| 1 | `src/AutonaMatchTimerWidget.py` | 新ウィジェットクラス（`QDockWidget` 継承、`__init__` で `setWindowTitle()` 必須） |
| 2 | `src/layout/AutonaMatchTimer.ui` | UI 定義（必要に応じて） |
| 3 | `src/Autona.py` | import 追加、インスタンス生成、`setObjectName`、`addDockWidget`、`tabifyDockWidget`、`STATE_VERSION` インクリメント |
| 4 | `src/AutonaDockTabTheme.py` の `TAB_SHORT_LABELS` | サイドバー用 6 文字以内の短縮ラベル |
| 5 | `Design.md` §4.1 の表 | ドック一覧に追加 |

#### 触るファイル（任意）

| # | ファイル | 内容 |
|---|---------|------|
| 6 | `src/ui_constants.py` | ウィジェット固有のスタイル関数追加（必要時） |
| 7 | `src/StateManager.py` | キー初期化（必要時。動的キーなら不要） |

#### 注意

- `setObjectName()` は **Autona.py 側で設定**する（ウィジェット自身では設定しない、`.claude/rules/python-widgets.md`）
- `STATE_VERSION` インクリメント忘れで既存ユーザーが新ウィジェットを見られなくなる
- `signals` への `connect` は `__init__` 内で行う

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
| 1 | 統計算出ユーティリティ（`src/AutonaStatsUtil.py` 等） | 算出ロジックを追加 |
| 2 | `AutonaStatsWidget` または `AutonaAnalysisWidget` | 表示 UI を追加 |
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
| 2 | `AutonaGameAssetManager` | 新 asset_type の解決が必要なら追加 |
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
| **T-2** | `commentary.{N}` Unset 時にインデックスを詰める実装に変更 | §3.1.2, §3.2.5 | `src/AutonaCommentaryWidget.py`（当時） | ✅ 完了（M1a）→ 2026-07 リワークでスロット固定 8 名化により概念ごと消滅 |
| **T-3** | `StateManager.state` 直接参照を `GetFullState()`（ロック取得版）に置き換え | §3.1.1, §3.2.5 | `src/AutonaWebServerActions.py:41`, `src/result/main_generate_result.py`, `src/thumbnail/main_generate_thumbnail.py` | ✅ 完了（M1a） |
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
| **T-15** | `AutonaAnalysisWidget` の仕様反映済み確認と回帰テスト追加 | §4.1 | `src/AutonaAnalysisWidget.py` のテスト整備 | 未着手 |
| **T-16** | サムネイルメタデータ（`_title.txt` / `_desc.txt`）を `thumbnail_config.generate_metadata` の設定で制御化 | §4.4.4 | `src/thumbnail/main_generate_thumbnail.py:save_result`, `AutonaThumbnailSettingsWidget` + `AutonaThumbnailSettings.ui` に `generateMetadataCheck` を追加（default true） | ✅ 完了（M3） |
| **T-17** | OBS テンプレ設定ダイアログに「OBS Studio 標準ホットキーとの衝突警告」を追加 | §6.4.2 | `src/Helpers/AutonaHotkeyValidator.py` を新設し、`Settings/SettingsWidget.py` の hotkey ハンドラで Autona 内重複は error / OS・OBS 想定衝突は warning ダイアログを出す | ✅ 完了（M3） |

### 8.3 優先度: 低（長期的な構造改善）

| ID | タスク | 関連 § | 影響範囲 |
|----|------|------|--------|
| **T-18** | **PyQt 書き込みの Actions 層移行**: PyQt Widget の `StateManager.Set` 直接呼び出しを Actions 層に移行する。WebServer 非依存名 `AutonaActions` / `StateActions` への改名も検討 | §3.2.2 R-Sync-1 | 大規模リファクタ。`src/AutonaWebServerActions.py` の独立化 + 全 PyQt Widget の書き込み経路差し替え |
| **T-19** | 動的キーパス（`objectName` 依存キー）の段階的な静的化。新規追加分から §3.1.2 に明記する運用 | §3.1.3 | 新規キー追加時のレビューで運用 |
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
