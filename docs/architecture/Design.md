# Autona Design Document

> **本書の位置づけ**
> Autona の構造・データモデル・同期規約・画面構成・デザインシステム・ホットキー・拡張の手順を定める正本。
> 本書は現在の設計だけを書く。経緯（いつ・どの工程で変えたか）は git 履歴と [../decisions/](../decisions/)（ADR）にある。
> 定数・タイムアウト・正規表現・エンドポイントごとの引数などコードが正本のものは写さず、コードの場所を示す。
> 矛盾したときの優先順位は「コードの実態 > 本書 > その他の Markdown」。

## 目次

1. [設計原則](#1-設計原則)
2. [レイヤー定義](#2-レイヤー定義)
3. [データモデルと同期](#3-データモデルと同期)
4. [画面構成](#4-画面構成)
5. [デザインシステム](#5-デザインシステム)
6. [ホットキー](#6-ホットキー)
7. [拡張ガイドライン](#7-拡張ガイドライン)

---

## 1. 設計原則

### 1.1 アプリの性格

Autona は格闘ゲーム大会の配信支援ツール。利用シーンは 2 つで、**明確に分離する**。

- **オペレーション**: スコア・選手・ゲームの情報を入力して配信へ即反映する（試合進行中）。一貫性・速度・誤入力防止が最優先。
- **クリエイティブ**: 配信画面・サムネイル・リザルト画像を大会ブランドに合わせて作る。自由度・表現力が最優先。

### 1.2 4 つの原則

1. **単一の真実の源**: 状態は `StateManager`（`src/core/StateManager.py`）に集約する。各レイヤーは state を読むか、書き込み API 経由で書く。レイヤー間で状態を直接共有しない。
2. **アプリ内部とクリエイティブの分離**: アプリ内部（デスクトップのシェルと React Web UI）は一貫したデザインシステムを持つ。クリエイティブ（OBS HTML レイアウト・画像生成テンプレート）はテンプレートごとに独自の配色・レイアウトを許す（§5.5）。互いの規約を持ち込まない。
3. **レイヤーごとの責務**: 各レイヤーは自分の仕事だけをする（§2.5）。
4. **拡張点の明示**: 新しいゲーム・レイアウト・ページなどを足すとき触るファイルは §7 に書く。書かれていない拡張点は、先に §7 を更新してから足す。

### 1.3 上流（TournamentStreamHelper）との関係

`web/`・`layout/` のディレクトリ名と構造は上流由来の歴史的経緯でリネームしない。本書では実態に即した名前で呼ぶ: `web/src/` = React Web UI、`layout/` = OBS ブラウザソース用テンプレートと画像生成テンプレート。

---

## 2. レイヤー定義

```
                    ┌────────────────────────────────────┐
                    │ StateManager (src/core, Qt 非依存)  │
                    │ 単一の真実の源・RLock・observers     │
                    └───────────────┬────────────────────┘
        書き込み                     │ delta / snapshot / observer 通知
 ┌────────────────────────┐         │
 │ AutonaActions + ドメイン │         ▼
 │ サービス (src/core)      │  ┌───────────────────────┐   ┌──────────────────────┐
 │ AutonaWebOperations      │  │ AutonaWebServer       │   │ StateManagerBridge   │
 │ (Web/外部の唯一の入口)   │  │ Flask + Socket.IO     │   │ (Qt アダプタ。同名の  │
 └───────────▲────────────┘  └───┬───────────┬──────┘   │ Qt シグナルを re-emit)│
             │                    │           │          └──────────┬───────────┘
   REST / Socket.IO               ▼           ▼                     ▼
   (React・外部連携)       React Web UI   OBS HTML レイアウト   デスクトップシェル
                           (全ページ)      (配信表示)          (Window + 共有ビュー)
                                          WebEngine 画像生成 ← state の snapshot
```

- **core**（`src/core/`）は Qt も `src/core` 外の `src` モジュールも import しない（`tests/test_core_qt_free.py` が pin）。Qt が要るものは `bootstrap_core()` から callable で注入する。
- **シェル**は 2 つ: デスクトップ（`python main.py`。`bootstrap_core()` + `bootstrap_gui()`）とヘッドレス（`--headless`。`src/headless.py`。`bootstrap_core()` までで QCoreApplication の上で動く）。ボードの state は両シェルとも core が起動時に 3 つ作る。生成順の正本は `src/bootstrap.py`。
- デスクトップのウィンドウの中身は、AppShell（React）を表示する**共有ビュー 1 枚**。Qt Widgets のパネルは無く、新規の Qt パネルは足さない（ADR-005）。

### 2.1 デスクトップシェル

目的: オペレーターがアプリを起動して使うウィンドウ。構成と起動順は §4.1。

| ルール | 内容 |
|------|------|
| **R-Shell-1** | 新しい UI は React のページとして作る。Qt Widgets は `Window`・共有ビューのホスト・Designer の窓・Updater のダイアログ・起動スプラッシュと、ネイティブのダイアログだけ（許可リスト `tests/test_web_panel_host.py` の `QT_WIDGET_CLASSES`）。 |
| **R-Shell-2** | Qt のウィジェットは state を書かない。書き込みは `AutonaActions` か core のドメインサービスを通す（§3.2.2）。 |
| **R-Shell-3** | Qt のオブジェクトが state の通知を受けるときは `StateManagerBridge.instance` の同名シグナルに `connect` する。observer を直接 `subscribe` しない（破棄済みのウィジェットが書き続ける）。 |
| **R-Shell-4** | `QSpinBox.valueChanged` 等のシグナルの中から `QMessageBox.exec()` や `processEvents()` を呼ばない。レイアウト解決後の処理は `QTimer.singleShot(0)` で defer する（`.claude/rules/agent-pitfalls.md` #9）。 |
| **R-Shell-5** | スタイルは `src/ui_constants.py` のトークン経由。色をハードコードしない（§5.1.6）。 |

### 2.2 React Web UI

目的: アプリの全ページ。デスクトップの共有ビューが AppShell として表示し、ブラウザ（スマホ・別 PC）からも開ける。

技術: React 19 + TypeScript、Redux Toolkit、MUI、Socket.IO クライアント + REST、i18next（日英）。バージョンの正本は `web/package.json`。

| ルール | 内容 |
|------|------|
| **R-React-1** | ページは `web/src/{PageName}/index.tsx`。ルートと登録は §7.5。 |
| **R-React-2** | サーバー同期対象のドメイン状態は Redux slice 経由で取る。フォーム入力中の一時値・開閉状態は `useState` でよい。 |
| **R-React-3** | 状態の変更は REST（`/api/v1`）か Socket.IO emit で AutonaWebServer 経由に限る。Redux 側を直接書き換えない。 |
| **R-React-4** | スタイルは MUI テーマ（`web/src/themes.js`）経由。`sx` は可、`style={{}}` のハードコードは避ける。 |
| **R-React-5** | フォーム要素は `size="small"` で統一する。 |
| **R-React-6** | delta は**到着駆動**で適用する（`program_state_update` 受信ハンドラ内で `applySavedDeltas`）。サーバー側で coalesce 済みなのでクライアントのバッチは不要で、setInterval は接続遷移中に残った delta の回収用の安全網だけ。rAF は使わない（モバイルのバックグラウンドで止まる）。 |
| **R-React-7** | テーマはダーク固定（§5.4）。 |
| **R-React-8** | スコア ± の楽観的エコー（`web/src/ScoreboardPage/useScoreSubmit.ts`）: タップ即時にローカル表示を更新し、絶対値で `score:set` を送る。サーバーの値が一致したら pending 解除、連打は in-flight 中最新値だけ保持、失敗でサーバー値へ戻して通知、タイムアウトでサーバーが正。競合は後勝ち（R-Sync-4）なので revision の突き合わせは持たない。 |
| **R-React-9** | 入力は確定（blur / Enter / 選択）で即送信し、送信ボタンは置かない。サーバーのエコーによる巻き戻りは「カード内にフォーカスがある間は表示欄を上書きしない」ガードで防ぐ。 |
| **R-React-10** | 表示トグルは `display_options.show_*`（state）が正本で、Web は `PATCH /api/v1/display-options` で書く。キーとデフォルトの正本は `src/core/StateSchema.py` の `DISPLAY_OPTIONS`。`web/src/ScoreboardPage/displayOptions.js` は言語が違う写しで、`tests/test_state_schema.py` が一致を検査する。 |

### 2.3 OBS HTML レイアウト

目的: OBS Studio などのブラウザソースとして読み込まれる配信画面。素の HTML / CSS / JS（jQuery・GSAP・lodash・Socket.IO は `layout/include/`）で、共通基盤は `layout/main.css` と `layout/include/globals.js`。

| ルール | 内容 |
|------|------|
| **R-OBS-1** | テンプレートのフォルダは `layout/{用途}_{バリアント}/`（§4.3）。 |
| **R-OBS-2** | 各テンプレートは `Start()` と `Update(event)` を実装し、`globals.js` の `tsh_update` を購読する。 |
| **R-OBS-3** | データ受信は Socket.IO が標準。ファイルポーリング（`out/program_state.json`）は degraded な代替。 |
| **R-OBS-3a** | `program_state_update` は `revision` を持つ。クライアントは前回の revision との連続性を検証する（規約は §3.2.3）。 |
| **R-OBS-3b** | reconnect 時と revision の不連続を検知した時、`program_state` の全量を取り直して再構築する。 |
| **R-OBS-4** | 配色・レイアウトはテンプレートごとに自由（§5.5）。`layout/main.css` の CSS 変数は上書きしてよい。 |
| **R-OBS-6** | テンプレートは状態を変更しない（read-only）。 |
| **R-OBS-7** | プレビュー画像は同じフォルダの `*_preview.png`。 |

### 2.4 WebEngine 画像生成

目的: サムネイル・リザルトを HTML テンプレートと Qt WebEngine（オフスクリーン）で描いて PNG / JPG にする。Python から JSON を渡す。テンプレートの契約はマニフェストの `contract` で決まり、契約 1 は `initThumbnail(payload)` / `initResult(payload)`、契約 2 は `layout/include/autona.js` の `Autona.onRender(payload => …)`（ADR-008 D7 R2）。

| ルール | 内容 |
|------|------|
| **R-Img-1** | レンダラーは `src/AutonaOffscreenRenderer.py` の土台のサブクラス（`src/thumbnail/ThumbnailRenderer.py` / `src/result/ResultPageRenderer.py`）。描画は 1 度に 1 つ（ページの完了の合図は title 1 つなので、同時に走ると互いの絵を取る）。入口は core の `renderAsync()`（結果はコールバック）と、サムネイルだけの `renderSync()`。段階ごとに期限があり、超えたらページを読み直す。 |
| **R-Img-2** | テンプレートの選択はプレビューの直上のコンボボックスに置く。 |
| **R-Img-3** | テンプレートの追加は `layout/` にテンプレートパッケージのフォルダを置くだけで完結する（§4.4.0）。 |
| **R-Img-4** | 契約 1 の HTML は描画完了で `document.title = "RENDER_READY"` にして合図する。契約 2 はレンダラーが `Autona._render(payload, serial)` を呼び、`autona.js` が `onRender` の関数・フォント・画像を待ってタイトルを `AUTONA_RENDER <serial> ok | failed` にする（`autona.js` が無ければ呼び出し側が `missing`）。`failed` / `missing` は絵を撮らず `RenderLane.TemplateError`（`template_error`）で終わり、ページが言ったエラー（`{kind, message, source, line, column}`。20 件まで）を `thumbnail.render.errors` / `result_render.errors` に載せる。ログには件数と kind だけ。`ok` でも撮る直前に `Autona._renderResult` を読み直し、描き終えた後のエラー（rAF・タイマー）があれば同じく失敗にする。タイムアウトでも捕まえたエラーがあれば `template_error`。レンダラーは前回 `missing`・契約の変更・フォルダのファイルの変更でページを読み直す。 |
| **R-Img-6** | 出力先は非 Preview 時 `./out/{種別}/`、Preview 時 `AutonaResolve("tmp/{種別}")`。ファイル名は §4.4.4。 |
| **R-Img-8** | 独自の配色・レイアウトを許す（§5.5）。 |
| **R-Img-9** | 描画は隔離する（ADR-007 D4 / T2）。ページはプレビュー用のリスナーのオリジン（`http://127.0.0.1:<WebServer.PreviewPort()>/layout/<フォルダ>/<入口>`）から読み、file:// では読まない。リスナーが無いときは描かずに `preview_unavailable`（`thumbnail.render.error` / `result_render.error`）で断る。ページはレンダラー専用の off-the-record のプロファイル（HTTP キャッシュなし・永続ストレージなし。ページの子にして、ページより後に消える）で動き、そのプロファイルのフィルタ（`_RenderRequestFilter`）がページ・フレーム・Worker・SharedWorker・Service Worker の要求のうち自分のオリジンと `data:` / `blob:` 以外を止める。既定のプロファイル（共有ビュー・デザインのプレビュー）とは BroadcastChannel・localStorage などを共有しない。権限は全部 deny・新しいウィンドウ無し・メインフレームは読み込んだ文書から動かない。**WebRTC の UDP（STUN / TURN）はフィルタを通らず止められない**。リスナーが出さない画像（アップロードした背景・アーカイブの `_bg.jpg`・旧版が保存したドライブのパス）は `core/RenderAssets` が描画中だけ id（ファイルの版ごと）で持ち、`/render-asset/<id>` で出す（プレビューのポート・この PC のループバックからだけ・描画が終われば 404）。ネットワークのパス（UNC）は GUI スレッドで見に行かず `""`。 |

### 2.5 責務の境界

| 概念 | デスクトップシェル | React | OBS HTML | 画像生成 |
|------|-------------------|-------|----------|---------|
| 状態の編集 | 依頼のみ（React 経由） | ◎ | × | × |
| 状態の運用表示 | ◎（React を表示） | ◎ | × | × |
| 配信画面への描画 | × | × | ◎ | × |
| 画像出力 | 描画の実行（Qt） | 依頼 | × | ◎ |
| 設定 UI・ホットキー受付 | ホットキーのみ | 設定 UI | × | × |

- **機能の重複は禁止**。両方で扱うなら、正本・競合時の優先順位を本書に書く（スコア操作は state が正本、書き込みは `AutonaWebOperations` に集約、同時操作は後勝ち）。
- OBS HTML から状態を書き換えない。
- 画像生成テンプレートを OBS のブラウザソースとして直接使わない（配信用と画像出力用の HTML は兼用しない）。

---

## 3. データモデルと同期

### 3.1 StateManager

#### 3.1.1 API 規約

| メソッド | 用途 |
|---------|------|
| `Set(key, value)` / `Get(key, default)` / `Unset(key)` | ドット区切りキーパスで読み書き（RLock 保護） |
| `with SaveBlock():` | 複数の書き込みを 1 保存サイクル（1 delta）にまとめる |
| `GetFullState()` | ロックを取るスナップショット（既定では held を除く） |
| `observers.<channel>.subscribe(cb)` | `state_big_change` / `state_updated` / `held_dirty_changed` / `held_state_updated` の購読（素の callback。Qt には Bridge 経由） |

- キーパスは**ドット区切りの文字列**で、数値の添字も文字列（`"score.1.team.1.player.1.name"`）。
- `StateManager.state` を直接参照しない。
- キー名は**保存データ**（`user_data/state.json`）のキーなので変えない。変えるなら `core/StateMigrations` に移行 step を足す（§3.3.5）。キーの定義は `src/core/StateSchema.py`（`KEY_CATALOG`）が正本で、**新しいキーは `StateSchema` に定義してから使う**。`objectName` からキーを組まない（`DYNAMIC_KEY_FAMILIES` は空。pin: `tests/test_state_schema.py`・`tests/test_objectname_keypath_contract.py`）。

#### 3.1.2 キーの構造

キーとシードの正本は `src/core/StateSchema.py`。ここでは構造と書き手だけを示す。値の形は書き手のモジュールの docstring が正本。

| ルート | 内容 | 書き手（core） | 永続 |
|-------|------|---------------|------|
| `score.{1,2,3}` | スコアボード。1 = 1v1、2 = チーム、3 = クルーバトル | ボード 1 / 2: `AutonaActions`・`PlayerService`・`SetLoadService`。ボード 3: `CrewBattleService` | 起動時に破棄 |
| `game` | 選択中のゲーム（`selected` で選ばれているかが分かる） | `GameAssetCatalog` | 起動時に破棄 |
| `tournamentInfo` | 大会の情報・大会カラー・アイコン / バナーの版・`media` / `load` の進み具合・`url` | `TournamentInfoService`・`TournamentMediaService`・`TournamentService` | ○ |
| `colors.{feature}` | 機能別カラーの上書き（明示設定がある feature だけ現れる） | `ColorService` | ○ |
| `stream` | 配信先・タイトル・団体素材の版・サムネ生成の状態 | `StreamService` | ○ |
| `thumbnail.render` / `result_*` | 画像生成の状態（`status` / `path` / `error` / `revision`）とリザルトの行（`result_players` / `result_teams`）・`result_layout` | `ThumbnailService`・`ResultService` | 行は ○、状態は起動で idle |
| `top8` | Top 8 の選手・試合 | `Top8Service`（トポロジーは `Top8Model`） | ○ |
| `commentary.{1..12}` | 実況者 12 スロット固定 | `CommentaryService`（形は `CommentaryModel`） | ○ |
| `analysis` / `set_selection` | 試合一覧・セット選択の取得の進み具合 | `AnalysisService`・`SetSelectionService` | 起動で idle |
| `display_options` | 表示トグル | `AutonaActions` | ○ |
| `provider_videogame` ほか | provider 由来 | `AutonaTournamentDataProvider`（`src/core` 外で state を書く唯一の例外） | |

`score.{N}` の主なキー:

```
score.{N}
├── team.{1|2}: teamName / score / color / logo / losers / player.{M}.{name, team(所属タグ), seed, country, state, id, city, character.{K}, ...}
├── best_of / match / phase / pool / top_n / set_id / station
├── teamsSwapped               スワップの正本（board1/2/3 共通。無ければ false）
├── last_sets / history_sets / recent_sets / upset_factor   統計（held の exempt）
└── board 3 のみ: team.{T}.memberNum / activeMember / player.{M}.{name, stock}
```

設計上の規則:

- **データ階層を混ぜない**: `tournamentInfo.*`（大会）・`stream.*`（配信）・`score.{N}.*`（試合）は別のライフサイクルを持つ。配信先を `score.{N}` に置くと、反映まで届かない・非表示ボードまで dirty になる・起動時の wipe で消える、の 3 つが同時に起きる。`stream.*` / `tournamentInfo.*` は非ゲート・ボード非依存・永続。
- **進み具合の形**: 長い取得・描画は state に `{status: idle|running|done|error, error, revision, ...}` を置く。`revision` は要求を受け付けるたびに増え、再起動でも戻らない。202 で返す `*Revision` と照合して自分の要求の結果を読む。
- **大きな行は state に入れない**: 試合一覧やセット選択の行は大会規模で数百〜千超になるので、サービスのメモリに大会 URL と組で持ち、`GET` で読む。
- **大会カラー・機能別カラー**: 値の正本は `SettingsManager`（`general.theme_color` / `accent_color`、`colors.{feature}.*`）で、state はレイアウト互換の表示用キャッシュ（§5.5.3）。
- **ボード 1 / 2 は held**（§3.2.3 の R-Sync-11）。ボード 3 と `score` 以外は即時公開。
- 値を作る側が解決する: 国は code、キャラは `en_name` + スキンの行で依頼を受け、core がカタログから値を組む（`PlayerService.BuildCountryValue` / `BuildCharacterValue`）。1 リクエストは 1 つのスナップショットで解決し、1 つでも断れば何も書かない。

### 3.2 同期経路

#### 3.2.1 全体図

```
ユーザー操作（React・外部連携・ホットキー）
        │
        ▼
AutonaWebOperations（Web/外部の唯一の入口。両シェル共通）
        │      ボードの操作は boardHost（1 本の RLock の下で 1 つずつ）
        ▼
AutonaActions / core のドメインサービス ──► StateManager.Set / Unset / SaveBlock
                                              │ DeepDiff で delta・revision を単調増加で付与
                  ┌───────────────────────────┼───────────────────────┐
                  ▼                           ▼                       ▼
          Socket.IO 配信              out/program_state.json      observers → Bridge → Qt
        （React・OBS）                (degraded・外部契約)
```

- 書き込みの入口は `AutonaWebOperations` 1 つ。デスクトップとヘッドレスの違いは、シェルが登録する操作だけ（デスクトップはシェル操作 3 本 `openSetSelection` / `renderThumbnail` / `startImageRender` を登録し、ヘッドレスは登録しないので 503 `UNAVAILABLE`）。
- ヘッドレスは held にしない（書き込みは即時公開）。ボードの操作は両シェルともリクエストのスレッドで走り、終了の開始からはメインスレッド以外の `Run` を 503 で断る。
- **共有ビューからのシェル要求**（デスクトップだけ・HTTP ルートではない）: 共有ビューのページがデスクトップに頼むこと（`alwaysOnTop` / `checkUpdates` / `openExternal` / `pickFolder` / `pickFile` / `openPluginLink`）は、`GET /api/v1/shell-session` のチケットを URL に入れた `/web/shell-request?verb=…` を開く形に限る。ホスト（`AutonaWebPanelHost`）が検査して `AutonaShellOperations.ShellRequest` を実行する。特権のチャネル（QWebChannel・注入スクリプト）は作らない。規則は `.claude/rules/state-and-webserver.md` の共有ビューの節。

#### 3.2.2 書き込み規約

| ルール | 内容 |
|------|------|
| **R-Sync-1** | ① React / 外部からの書き込みは必ず `AutonaWebOperations` 経由。② OBS HTML は read-only。③ Qt のウィジェットは `StateManager.Set` / `Unset` を呼ばない。④ 複合操作・正規化を伴う操作は Actions 層（`src/core/Actions.py`）か core のドメインサービスに置く。⑤ `src/core` 外の書き手は `AutonaTournamentDataProvider` の 1 つだけ（台帳: `tests/test_objectname_keypath_contract.py`）。⑥ state の Qt シグナルは `StateManagerBridge.instance` が出し、core に Qt シグナルを足さない。 |
| **R-Sync-1b** | `AutonaActions.instance` に移した操作（スコア・チームカラー・表示トグル・反映・match バー・チーム名 / LOSERS・best of・人数・スワップ・クリア・配信 URL / タイトル・対戦形式・ゲームのロードの commit ほか）はそこ以外が書かない。書き込みの実装が core にある操作でも、入口が Actions に一本化されていないものがある（クルーボード = `CrewBattleService`、実況 = `CommentaryService`、選手スロット = `PlayerService`、スコアの手入力 = `SetScore`）。`score.{n}.team.{t}.score` の core 内の書き手は `AutonaActions` と `SetLoadService.ApplyScores`。操作の一覧と規則は `.claude/rules/state-and-webserver.md`。 |
| **R-Sync-2** | バッチ書き込みは `with StateManager.SaveBlock():`。`BlockSaving()` / `ReleaseSaving()` の手動ペアは避ける（例外で漏れる）。 |
| **R-Sync-3** | 同一トランザクションの複数 `Set` は SaveBlock で囲み、delta を 1 回にまとめる。 |
| **R-Sync-4** | 競合は**後勝ち**。同時操作の検出・拒否はしない。 |

#### 3.2.3 配信規約（broadcast）

| ルール | 内容 |
|------|------|
| **R-Sync-5** | StateManager は変更ごとに `revision`（単調増加の整数）を付ける。 |
| **R-Sync-6** | `program_state_update` は `{revision, first_revision, delta}`。delta coalescing が複数の revision を 1 メッセージに結合するので、payload は連続区間 `[first_revision, revision]` を表す。`first_revision` が無い旧 payload は単一 revision と見なす。 |
| **R-Sync-6a** | `delta` の行は受信順に 1 行ずつ適用できる（結合の区切りはメッセージに無い）。リストは位置ごとに比べ、型の変更も値を省かない。成り立たせる `SaveState` のオプション（`DeepDiff(zip_ordered_iterables=True)` / `Delta(always_include_values=True)`）を外さない。pin: `web/src/stateDelta.cases.json`（pytest と jest が読む）。 |
| **R-Sync-7** | `program_state`（全量）は `{revision, state}`。 |
| **R-Sync-8** | クライアントは区間 `[first_revision, revision]` が「前回適用 + 1」を含むことを検証し、含まなければ `program_state` を再要求して全量で再構築する。 |
| **R-Sync-9** | reconnect 時は必ず全量を取り直す。 |
| **R-Sync-10** | adaptive debounce: delta が少ないほど短い窓（50 / 100 / 200 ms。大量なら `state_big_change` も通知）。**leading-edge flush**: 直近の flush から静かだったら最初の delta は即時送る。間隔と payload は外部契約（pin: `tests/test_webserver_delta_debounce.py`）。 |
| **R-Sync-11** | **遅延反映（held）**: デスクトップでは `score.1` / `score.2` が `StateManager.HoldSubtree` で保留登録される（`AutonaActions.MakeBoards(panel=True)`）。書き込みはライブ state に即適用される（`Get()` は常にライブ値）が、delta 配信・全量（`GetFullState` の既定）・`out/` からは除外され、SCORE ページの「反映」（`CommitHeld`）が 1 保存サイクルで一括公開する。ゲームのロードは自動コミットする。**exempt**（`HoldSubtree(prefix, exempt=[...])`）は即時公開: 統計キー `STATS_KEYS`（`last_sets` / `history_sets` / `recent_sets` / `upset_factor`）。画像生成は `GetFullState(include_pending=True)` でライブ値を描く。ボード 3・`score` 以外のサブツリー・ヘッドレスは held にならない。held 配下は「同値なら書かない」（`SetIfChanged`）。 |
| **R-Sync-12** | **値を運ばない通知**（state の外・未公開のものを React に知らせる合図）: `scoreboard_pending`・`scoreboard_follow`・`battle_format`・`games_changed`。読み手は通知の後に始めた GET の答えだけを使う。例外の `status`（アプリの実行中の仕事）は値を運ぶ。同じ送信の中の順は delta → pending → follow → battle_format → status → games_changed。詳細は [external-control-api.md](external-control-api.md) §4 と `.claude/rules/state-and-webserver.md`。 |

#### 3.2.4 スレッドと lock の不変条件

- `held_state_updated` / `held_dirty_changed` / `state_updated` のハンドラは `StateManager.lock` の中で走る。その中で別スレッドを待たない（`gui_thread_sync` / `run_sync` / `QDialog.exec()` / `processEvents()` をしない。pitfalls #17）。受け手は積むだけにする。
- ロック順は **catalog lock → `StateManager.lock`**。`StateManager.lock` 保持中の observer から `GameAssetCatalog` の writer を呼ばない（pitfalls #16）。保存側の lock は `StateManager.lock` の後（`StatePersistence`）。サービスの lock は自分のフィールドだけを守り、state・fetcher・`PlayerDB` は lock を離して呼ぶ。
- サービスは observer を購読しない。例外は「プロセスと同じ寿命のシングルトン・`Stop()` で解除できる・observer の中では予約するだけ」を満たす `StreamService` と `MatchLogService`（購読者の集合は `tests/test_bootstrap_split.py` の `STATE_SUBSCRIBERS` が pin）。
- ボードの操作は `boardHost.Run` の 1 本の RLock の下。ロックの中でメインスレッドを待たない。

#### 3.2.5 同期遅延の目安

| 経路 | 上限 |
|-----|------|
| StateManager → React（単発） | ほぼ 0 ms（leading-edge + 到着駆動）。バーストは debounce の上限 200 ms |
| StateManager → OBS（Socket.IO） | 200 ms。degraded poll はそれに poll 周期 |
| スコア ± の表示 | 0 ms（楽観的更新。R-React-8） |

held のボードは「反映」を押すまで配信自体が保留されるので、OBS / React への反映は押下が起点になる。

### 3.3 設定の永続化

#### 3.3.1 置き場所

保存先は種別ごとに一意に定める。

| 種別 | 保存先 | 所有 |
|------|------|------|
| 一般設定・ホットキー上書き・各種 `*_config`・`TOURNAMENT_URL` | `./user_data/settings.json` | `core/SettingsManager`（編集は React の設定ページと各ページ） |
| 前回の内容（state。`score` / `game` は起動時に破棄） | `./user_data/state.json`（`{"state_version": N, "state": {...}}`） | `core/StatePersistence`（間引いて自動保存・終了時に書き切る） |
| 選手 DB | `./user_data/local_players.json` | `core/PlayerDB` |
| ゲームアセット | `./user_data/games/{game}/{asset_type}/config.json` | `core/GameAssetCatalog`（スキャン・ロードは `core/GameLoader`） |
| 国・地域マスタ / カスタム国旗 | `./assets/countries+states.json`・`./assets/city_to_state.json`・`./user_data/additional_flag/*` | `core/CountryCatalog` |
| テンプレートの定義・マニフェスト | `./layout/{フォルダ}/settings.json` ほか | `core/LayoutCatalog` |
| アーカイブ切り抜き | 作業フォルダ（`archive.work_dir`）の `<job_id>/job.json` ほか / `./user_data/archive_templates/` / `./user_data/match_log/YYYYMMDD.jsonl` | `core/ArchiveJobService`・`ArchiveTemplates`・`MatchLogService`（state には載せない） |
| ウィンドウ位置 | QSettings（Windows レジストリ） | `Window` |
| 開いていたページ・サイドバーの折りたたみ | QSettings の `lastPanel`（ページの名前）/ `navCollapsed` | `src/AutonaWindowState.py`（Qt 非依存の純関数） |
| OBS テンプレートの設定 | `./layout/settings.json`（グローバル）・`./layout/{template}/settings.json`（ローカル） | レイアウトの実行時 |

`out/program_state.json` は永続化ではなく**出力**（OBS の file:// ポーリングと外部の契約。場所・形式・`timestamp` / `revision`・書き込み順は不変）。

#### 3.3.2 正本

- ユーザー設定の正本は `SettingsManager`。state に展開してよいが、それは**キャッシュで正本ではない**（`tournamentInfo.themeColor` / `colors.{feature}.themeColor` など）。
- API トークン・認証情報は `SettingsManager` に置き、state に置かない。
- ホットキーの有効値は `core/HotkeyRules.DEFAULT_KEYS` に `settings.json` の `hotkeys` を重ねた結果（`HotkeyRules.CurrentKeys()`）。
- OBS レイアウトの設定は `_.defaultsDeep(URL クエリ, ./settings.json, ../settings.json)`（クエリが最優先）。

#### 3.3.3 キーの命名

`SettingsManager` のキーはネームスペース付きで作り、トップレベルは増やさない。主な接頭辞: `general.*`（`theme_color` / `accent_color` は大会共通カラー）、`colors.{feature}.*`、`hotkeys.*`、`display_options.*`、`thumbnail_config.*`、`result_config.*`、`archive.*`、`designer.*`、`{board,commentary,top8}.selected_design`（`core/LayoutCatalog.SELECTION_SETTINGS`）、`stream_channels` / `stream_thumbnail_options`。読み書きは各サービスのモジュール（`ThumbnailService`・`ResultService`・`StreamService`・`ColorService`・`ArchiveJobService`・`AppSettings`）が持つ。

読めなかった起動の扱いは `.claude/rules/state-and-webserver.md` の SettingsManager の節（`loadFailed`・`SettingsUnreadable`・書き込みの 2 種類）。

#### 3.3.4 state の版と移行

- 保存済み state のキー名・形を変えるときは、`core/StateMigrations` に移行 step を足して `CURRENT_VERSION` を上げる（前方一方向・出荷した step は凍結・冪等）。版は封筒（`state.json`）にだけ持ち、state の木に入れない（Socket.IO と `out/` に漏れる）。同じ版に残りうるものの起動ごとの掃除は `StateSchema.SeedPanelState()`。
- 新しい版の `state.json` を古いビルドで開く・壊れた `state.json` は、`.bak` に退避して `out/program_state.json` から移行して起動する（上書きしない）。`out/program_state.json` のほうが新しいときもそちらから移行する。
- ウィンドウが覚えるのはページの名前だけなので、ナビの追加・削除・並べ替えで読み替えは要らない（§4.1）。

---

## 4. 画面構成

### 4.1 デスクトップのウィンドウ

`src/Autona.py` の `Window`（QMainWindow）の中身は**共有ビュー `AutonaWebPanelHost` 1 枚**。ホストは AppShell（`/web/app/<panel>`）を読み、サイドバー・ヘッダー（大会・ゲーム・MOD・対戦形式）・フッター（Help & Settings・実行中の仕事）は AppShell のもの。

- **ページの切り替え**: AppShell のナビは `history.replaceState`（遷移にならない）。ホストは `urlChanged` でページを読み（`AppPanelOf`）、ウィンドウは覚えるページを変わるたびに保存する（`AutonaWindowState`）。外から開くのは `Window.OpenPanel(名前)`。
- **起動**: `main.py` が起動直後にネイティブのスプラッシュを出し（`src/autona_splash.py`。Windows の layered window を専用スレッドが持つ。出せなければ no-op）、`show()` → ローディングのスピナー → 初回の描画の後に Web サーバー起動 → ホストがビューを建てて読み込み、最初の読み込みの終わりでスピナーを外す。順序とログ（`STARTUP phase=...`）は [ADR-003](../decisions/ADR-003-startup-architecture.md)。スピナーが外れない経路を作らない（外すのは最初の読み込みの終わり・Web サーバーの起動失敗・上限 `Window.SPINNER_CAP_MS`）。
- **失敗の表示**: Web サーバーの起動失敗はホストの案内文（`ShowStartFailure`）、読み込みの失敗は再試行の表示（`ShowLoadFailure`）。ダイアログは出さない。`QMessageBox` は初回起動の LAN の確認だけ。
- **ウィンドウが持つもの**: 常に最前面（Window の bool。保存しない）、ゲーム変更の通知は中継しない（`bootstrap.wire_web_server` が Bridge の `game_loaded` に繋ぐ）、大会の設定 / 解除はしない（AppShell と EVENT ページが v1 で `TournamentService` を呼ぶ）。

#### 4.1.1 ページ一覧

ページはすべて React（`web/src/<Page>/`）で、ナビの項目は `web/src/AppShell/navSections.json`。state の書き手とページ固有の仕様はそれぞれのサービスの docstring と ADR。

| ナビのキー | ページ | 責務 | 書き手 / 正本 |
|-----------|------|------|--------------|
| `event` | Tournament Info | 大会の設定・再取得・解除、大会情報、ゲーム選択、大会カラー、アイコン / バナー | `TournamentService`・`TournamentInfoService`・`TournamentMediaService`・`ColorService` |
| `stream` | Stream Settings | 配信先・タイトル・団体素材・配信サムネ | `StreamService` |
| `scoreboard` | Scoreboard | スコア入力・選手編集・match バー・反映・X 投稿・セット選択 / 配信台のダイアログ。形式は 1v1 / チーム / クルー | `AutonaActions`・`PlayerService`・`CrewBattleService`・`SetFollowService` |
| `board` | Board | 配信用スコアボードのデザイン選択・OBS 用ファイルの案内 | `LayoutCatalog` |
| `thumbnail` | Thumbnail Settings | 試合サムネイルの設定・プレビュー・作成 | `ThumbnailService` |
| `result` | Result Settings | 大会リザルトの行・レイアウト・プレビュー・作成・X 投稿 | `ResultService` |
| `top8` | Top 8 Display Settings | Top 8 の選手・スコア・Start.gg 取り込み・デザイン選択 | `Top8Service` |
| `commentary` | Commentary | 実況者 12 スロットの編集・デザイン選択 | `CommentaryService` |
| `statsPreview` | Stats Display Settings | 統計オーバーレイの選択・プレビュー・OBS 用 URL | `StatsOverlays`・`ColorService` |
| `stats` | Statistics | 統計の取得と 4 タブ（直近の対戦・戦績・大会履歴・この大会の試合一覧）。read-only | `StatsService`・`AnalysisService` |
| `playerBrowser` | Player List | 参加者と登録済み選手の一覧・編集（state に書かない） | `PlayerDirectory`・`PlayerDB` |
| `archive` | Archive Clipper | 配信アーカイブをセットごとに区切って書き出す（§4.2.5） | `ArchiveJobService` ほか |

Help & Settings から開く**メニューのページ**（サイドバーの項目ではない）: `settings`（一般・ネットワーク・言語・ホットキー）・`api`（リファレンスとテスター）・`companion`・`obs`・`about`・`assets`（ゲームアセットの追加）。ナビをハイライトせず、終了時は直前のページを覚える。

#### 4.1.2 ページの追加・削除

手順は §7.5。規約は次のとおり。

- **ページを開いていなくても成立させる**: 起動時に要る state のシード・正規化・廃止キーの掃除はページではなく `src/core/StateSchema.py`（`SeedPanelState()` / `SeedBoard()`）に置く。`Window._SeedPanelState()` はその呼び出し口で、1 つの SaveBlock に束ねる。新しいボードの中身は `AutonaActions.SeedFreshBoard()` が書き、両シェルとも起動時に 3 つ作る（`AutonaActions.MakeBoards()`。起動 1 回だけ。生きているボードに `SeedBoard` を呼ばない）。
- ページを消すときは、保存された `lastPanel` がその名前でも `RestorePanel` が `WEB_PANELS` に無い名前を既定（`event`）に倒す。

### 4.2 React Web UI

#### 4.2.1 ルート

`web/src/index.js` が React Router で定義する。

| ルート | 内容 |
|-------|------|
| `/`・`/web`・`/scoreboard` | ブラウザ向けの ScoreboardPage（board 1）。`/team` はチーム（board 2） |
| `/web/:panel` | `PanelShell`。枠なしでページを出す（ブラウザとプレビュー向け）。サーバーが返すのは `WebAccess.WEB_PANELS` の名前だけ（他は 404）。`?desktop=1` でブラウザ向けの部品を隠す |
| `/web/app/:panel` | `AppShell`。ページを枠（サイドバー・ヘッダー・フッター）の中に出す。デスクトップのウィンドウはこの 1 枚 |

#### 4.2.2 AppShell（`web/src/AppShell/`）

- 構成: サイドバー（`Header.tsx` = 大会・ゲーム・MOD・対戦形式、`Nav.tsx` = `navSections.json`、`Footer.tsx` = Help & Settings と実行中の仕事）+ ページ（`PanelContent`）。`PanelShell` と共通の準備は `usePanelShellSetup`。
- サイドバーが横にある間はページのブレークポイントをサイドバーの幅だけずらしたテーマ（`themes.js` の `themeBeside`）で描く。折りたたみは URL のクエリ `nav=collapsed` に replace で書き、デスクトップのホストが読んで QSettings に保存する。600px 未満はドロワー。
- ゲーム・大会・対戦形式の操作は v1 を呼ぶだけで、選んだ値は答えの後の読み直しまで保つ（後から押した方が勝つ）。大会の読み込みの失敗は `tournamentInfo.load` から revision ごとに 1 回通知する（`told=<revision>` を URL に持ち、ビューの読み直しで二重に出さない）。
- Help の分岐は `useShellAccess({withToken: false})`: 共有ビューではシェル要求（最前面・更新の確認・外部アドレス）、ブラウザ・ヘッドレスではそれらを出さない。外部アドレスは `src/AutonaExternalLinks.py` の表と `web/src/AppShell/externalLinks.ts` の写し。
- 文言は i18next の `app_*`（en / ja）。

#### 4.2.3 ページ共通の約束

- 共通部品は `web/src/` の各ページと共有コンポーネント（`NumberInput.tsx`・`CountrySelector.jsx`・`ScoreboardPage/TextField.jsx` ほか）を参照。
- 書き込みが `SHELL` 区分のページ（アーカイブ・ゲームアセット・トークン・プラグイン。プラグインはブラウザでも読めるのが一覧（`THIS_PC`）だけ＝この PC のブラウザ・ヘッドレスのみで、詳細・設定・実行・開発フォルダ・zip の導入・有効無効・削除は `SHELL`。導入は zip の生バイトを `POST /plugins/uploads` → `POST /plugins/installs` → 同意シート（`InstallSection.tsx`）→ `:consent` と進め、削除は `GET .../removal` で確かめて `:remove` を 1 秒ごとに追う（`PluginManage.tsx`）。組み込み・開発中の行には有効無効と削除を出さない）は `useShellAccess` で共有ビューかを確かめ、ブラウザ・ヘッドレスでは見るだけにして案内を出す。
- 共有ビューの Web UI に外部・第三者の HTML を sandbox なしで入れない（秘密とチケットが同じオリジンの子フレームに届く）。第三者のものは別オリジン（プレビュー用リスナー）か `allow-same-origin` なしの sandbox で出す。

#### 4.2.4 統計情報ページ

ボード 1 の統計を `POST /scoreboards/1/stats:refresh` で取得し、値は Socket.IO の `score.1.*` から TS で整形して表示する（`web/src/StatsPage/format.ts`）。選手名は反映済みの値（統計キーは held 対象外なので、反映までの間は新しい選手の統計に前の選手名が付いて見える）。「この大会の試合一覧」タブは `analysis.*` と `GET /api/v1/analysis` の行を描く（行の文言・並びは `analysisRows.ts`。`analysisRows.cases.json` を pytest と jest が読む）。

#### 4.2.5 アーカイブ切り抜き（`archive`。ADR-006）

配信アーカイブ（YouTube の URL か動画ファイル）をセットごとに区切るページ（`web/src/ArchivePage/`）。解析と書き出しは Autona のプロセスでは行わず、**ワーカー**（vodsplit。別リポジトリ SmashVodSplitter）を子プロセスで動かす。決定の根拠と詳細は [ADR-006](../decisions/ADR-006-archive-clipper-worker.md)。

- **core**（Qt 非依存。`bootstrap_core()` が作る）: `ArchiveWorker`（ワーカーの場所の解決・版の確認・起動するものの組み立て。形式 `format == 1` だけを受ける）、`ArchiveJobService`（ジョブ。同時に 1 つ）、`ArchiveReview`（レビューの形・検査）、`ArchiveResults`（結果データの用意とキャラの合成）、`ArchiveTemplates` / `ArchiveThumbnails`（タイトルのテンプレートとサムネイル）、`ArchiveWorkerInstaller`（ワーカーの導入・更新）、`MatchLogService`（ボード 1 の公開を追って試合の記録を書く）。
- **StateManager には載せない**: ページは `GET /api/v1/archive/jobs/<id>` を 1 秒ごとに読む。ファイルの置き場所は §3.3.1、v1 のルートは [external-control-api.md](external-control-api.md) §5.8。読み取りは `THIS_PC`、書き込みは `SHELL`。
- **ワーカーの場所**: 環境変数 `AUTONA_VODSPLIT_CMD`（開発用）→ 作業フォルダの `vodsplit-worker/`。ワーカーは Autona の zip に入れず、Autona-releases の `worker-v<版>` から導入する（アップデーターは触れない。pin: `tests/test_archive_worker_install.py`）。
- **ワーカーの起動**（ADR-009 D9）: 解析と書き出しの run は組み込みプラグイン `taguyan.vodsplit` として `PluginRunner` が起動する（ジョブオブジェクト・優先度・中止の猶予・終わりの状態の判定はランナーのもの）。実行中は重い枠を `PluginJobs` と共有し、プラグインの重い run が動いている間の開始は BUSY で断る。版の確認（probe）・自己チェック・タイトルのプレビューは `ArchiveWorker` が直接 subprocess で起動する。
- **ページ**: ジョブの一覧と、完了したジョブのレビュー画面（プレビュー・タイムライン・詳細）。YouTube のジョブは埋め込みプレーヤー、それ以外は `<video>`。YouTube の IFrame Player API の script は読まない（Google の script が共有ビューで動くのを避けるため。実装は `archiveEmbed.ts`）。編集（確定・IN / OUT・割り当て・削除）は共有ビューでだけ有効で、ページのローカルが正、600 ms で間引いて `review.json` 全体を PUT する。
- テスト: `tests/test_archive_*.py`（偽のワーカー `tests/fixtures/fake_vodsplit_worker.py`）、`web/src/ArchivePage/*.test.js`、`web/e2e/archive.spec.ts`。

### 4.3 OBS テンプレートの命名と構成

#### 4.3.1 ディレクトリ命名

既存テンプレートは互換のためリネームしない。新規は `layout/{用途}_{variant}/` で、`variant` は用途差分 → 配置差分 → 表現スタイル差分 → ブランド差分の優先順。scoreboard 系は `scoreboard_{placement}_{style}`。

| 用途プレフィックス | 内容 |
|----------------|------|
| `scoreboard*` | 配信中のスコアボード |
| `bracket_*` | Top 8 ブラケット（`top8.*` を購読） |
| `commentators` | 実況・解説者バー（`commentary` の `display` が ON のスロットを 1〜4 名） |
| `recent_sets` / `last_sets` / `h2h_record` / `upset_factor` / `tournament_history` | 統計オーバーレイ |
| `camborder_*` | カメラ枠 |
| `result*` / `thumbnail*` | 画像生成専用（§4.4） |

#### 4.3.2 ファイル構成

エントリ HTML（`index.html`）・CSS・JS（`Start()` / `Update(event)`）・`*_preview.png`（選択 UI に出すものは必須）・`settings.json`（ローカル設定とデザイン一覧のメタデータ）。

#### 4.3.3 デザイン一覧のメタデータ

BOARD / 実況 / TOP 8 のページのプルダウン・Designer の「開く」・`GET /api/v1/layouts` は、すべて `src/core/LayoutCatalog.py` の 1 つのカタログを読む。旧来型のテンプレートのメタデータは `settings.json` の `autona` ブロック（キーは `kind` / `displayName` / `format` / `game` / `entry` / `contract`。`contract` はオーバーレイ契約の番号で省略は 1、知らない番号は載せない（ADR-008 D1）。`LayoutCatalog` の docstring が正本）、Designer 製（`layout/custom/{slug}/`）は `project.json`。ブロックが無い・壊れたフォルダは旧来のフォルダ名の規約（`scoreboard*` / `commentators*` / `bracket_top8*`）で拾う。同じ kind で表示名が重なったら `{displayName} ({id})` にする。利用者向けの説明は `layout/README.md`。pin: `tests/test_layout_catalog.py`。

#### 4.3.4 共有リソース

`layout/include/`（`globals.js`・`assetUtils.js`・`socket.io.min.js`・`gsap`・`jquery`・`lodash`・`kuroshiro` / `kuromoji`・`color-thief`・フォント・`thumbnail_assets.js`・`result.js` / `result.css` / `result_team.css`）と `layout/main.css`（CSS 変数: `--font`・`--text-color`・`--bg-color`・`--p1-score-bg-color`・`--p2-score-bg-color`・`--theme-color`・`--accent-color` など。値は `layout/main.css` が正本で、`--theme-color` の既定は `StateSchema.DEFAULT_THEME_COLOR` と同値）。テンプレートは `:root` で上書きしてよい。`include/` のライブラリを上げるときは全テンプレートで動作確認する。

### 4.4 画像生成テンプレート

#### 4.4.0 テンプレートパッケージ

リザルト画像のレイアウトとサムネイルのテンプレートは、`layout/` 直下の 1 フォルダ = 1 **テンプレートパッケージ**。`core/LayoutCatalog.py` が `settings.json` の `autona` ブロック（マニフェスト）から見つける（コードに一覧の表は無い）。`ResultService` / `ThumbnailService` / レンダラー / `GET /api/v1/layouts?kind=result|thumbnail` / ページの選択肢はすべてこのカタログを読む。利用者向けの説明は `layout/README.md`。

| キー | 内容 |
|------|------|
| `kind`（必須） | `result` / `thumbnail` |
| `contract`（必須） | テンプレートの契約の番号（`LayoutCatalog.TEMPLATE_CONTRACTS`。知らない番号は載せない） |
| `size`（必須） | 出力の `[幅, 高さ]`（px の整数） |
| `rows` | result で必須: `players`（単独。16 行まで）/ `teams`（団体。8 行） |
| `template` | thumbnail で必須: 配置の数値の JSON（フォルダ内のファイル名） |
| `entry` / `displayName` / `author` / `license` | 任意 |

- **フォルダ名へのフォールバックは無い**。マニフェストが不正・入口や `template` のファイルが無いものは、そのフォルダだけ警告を出して載せない。
- **id**: 同梱 8 種は今までの id を変えない（凍結した表 `LayoutCatalog.BUNDLED_TEMPLATES`）。ほかはフォルダ名。同梱の id と同じ名前のフォルダ（大文字小文字は問わない）は載せない（同梱が勝つ）。保存値（`result_config.layout` / `thumbnail_config.template_id` / `result_layout`）の移行は無い。
- 保存された id のパッケージが無いときは既定で描き、保存値は消さない。
- パッケージが自分のフォルダの外で読んでよいのは `../include/`・`../../assets/`・payload が渡すパスだけ。`include/` のファイルを同梱しない。
- payload は契約 1 のまま（`tests/fixtures/{result,thumbnail}/golden.json` が写し）。契約 2 も同じ payload を `onRender` に渡す。pin: `tests/test_template_packages.py`。
- テンプレートのフォルダ（kind `thumbnail` / `result` のマニフェストを持つフォルダ。`LayoutCatalog.IsTemplateFolder`）のファイルは、両方のポートで CSP `WebAccess.TEMPLATE_CONTENT_POLICY`（自分のオリジンと `data:` / `blob:` だけ。Worker・フレーム・object・form は無し）と `no-cache` を付けて配る。ブラウザの iframe のプレビュー（§4.4.1）にはレンダラーの要求フィルタが無いため（フレーム自身の遷移と WebRTC は止められない）。pin: `tests/test_web_access.py`。
- 描画はプレビュー用のリスナーのオリジンで、ページが読めるのはそのリスナーが出すもの（`layout/`・`assets/`・state の画像・v1 の READ）と描画が持つ画像だけ（R-Img-9）。サービスが組む payload は変えず、レンダラーが描く直前に `RenderAssets.ForRender` で `config` の `background_path` / `main_icon_path` だけを読める形に直す（出すパスは相対のまま `m=<更新時刻>` を足す（アプリのディレクトリ `LayoutCatalog.AppDirectory()` で探す。静的ファイルもそこから配る）、ほかのファイルは `/render-asset/<id>`、無い・画像でない・ネットワークのパスは `""`）。pin: `tests/test_render_isolation.py`。

#### 4.4.1 サムネイル

同梱は `layout/thumbnail/`（`type_a`）と `layout/thumbnail_type_b/`（`type_b`）。どちらも契約 2。ファイルは `settings.json`（マニフェスト）・`thumbnail.html`（`../include/autona.js` を読み、`thumbnail.js` が `Autona.onRender(drawThumbnail)`）・`thumbnail.css`・`thumbnail.js`・テンプレート定義 JSON。選択は `thumbnail_config.template_id`（既定は `ThumbnailService.DEFAULT_TEMPLATE_ID`）。

ページのプレビュー: 選んだテンプレートが契約 2 なら、レンダラーに頼まず `GET /api/v1/thumbnail/payload`（PLAYERDB。`ThumbnailService.PreviewPayload`。未反映の編集を含む）で payload を受け、`DesignPane`（選択欄なし）の iframe（URL に `?autonaParent=<ページのオリジン>`）に `{type: "autona:render", serial, payload}` を送る。`autona.js` は 1 回ずつ描き、`autona:rendered` が届くまで確認の欄は「描画中…」。エラーは L3 の確認の欄（AI 用のコピー付き）。payload が 401 / 403 か契約 2 でなければレンダラーの画像に戻る。ヘッドレス・LAN のブラウザでも出る。`RenderAssets.ForPreview` は何も持たないので、リスナーが出さない画像（アップロードした背景など）は `""` になり `omitted` に名前が出る（ページが「プレビューには出ない」と言う）。生成（PNG）はオフスクリーン描画のまま。契約 1 のテンプレートは今までどおりレンダラーの画像でプレビューする。

新しいテンプレート（ADR-008 D7 R3）: ページの「新しいデザインを作る」（共有ビューの中だけ。L2 と同じ `NewDesignActions`・v1 の `POST /layouts:create` の kind `thumbnail`）が `layout/include/kit/thumbnail/` から `layout/thumbnail_<名前>/` を作り（マニフェストは契約 2 の形で、作った直後から一覧に出る）、一覧を読み直して選ぶ。payload のキーの表は `layout/include/thumbnail_contract.json`（`data` / `config` / `template`。表の `config` のキーは `ThumbnailService` が描画とプレビューに渡す config に毎回ある（ほかのキーもありうる）。表のキーは足すだけ。どちらも `tests/test_thumbnail_kit.py` が固定）で、キットの `CONTRACT.md` / `AI_PROMPT.md` / `sample_payload.json` は `scripts/gen_layout_kit.py` の生成物。テンプレートの選択の下に、一覧に出せなかった `thumbnail_*` のフォルダと理由（`GET /api/v1/layouts` の `unlisted` のうち kind `thumbnail`）を出す。作った後は一覧の読み直しが終わって一覧にあるときだけ選ぶ。

#### 4.4.2 リザルト

一覧の正本はカタログの result パッケージ（`ResultService.LayoutEntries()`）。枠の数は `SlotsFor()`、既定は `DEFAULT_LAYOUT`（単独）/ `DEFAULT_TEAM_LAYOUT`（団体）。同梱の名前は `core/CoreWords` の `result_layout_<id>`。ファイルは `settings.json`・`result.html`・`result.css`・`result.js`（同梱の多くは `../include/` を共有）。同梱の 6 種は契約 2（ADR-008 D7 R4。`../include/autona.js` を読み、共有の `include/result.js` の `drawResult` を各 `result.html` のインラインの `Autona.onRender(drawResult)` で登録する。`include/result.js` 自身は何も登録しない。geki は自分の `result.js` が登録する）。`include/result.js` は `autona.js` の無い契約 1 のページのために `initResult(payload)` も残す。団体のレイアウトは `placeTeams()`（`include/result.js`）がメンバーのファイターを配置する（`data.rows` が `teams` のとき。`rows` の無い payload では `layout` が `team` で始まるとき）。

ページのプレビュー（R4）: 選んだレイアウトが契約 2 なら、サムネイル（§4.4.1）と同じく `GET /api/v1/result/payload`（PLAYERDB。`ResultService.PreviewPayload`。`{data, config, layout}` を `RenderAssets.ForPreview` に通したものと、レイアウトの id・フォルダ・entry・contract・大きさ・`rows`）を `DesignPane` の iframe に描く。戻り方（401 / 403・契約 2 でない）・`omitted` の文言・ヘッドレスでも出ること・契約 1 は画像のプレビューのままもサムネイルと同じ。

新しいレイアウト（R4）: ページの「新しいデザインを作る」（共有ビューの中だけ。kind `result`）が `layout/include/kit/result/` から `layout/result_<名前>/` を作る（マニフェストは `rows: "players"`。チームにするなら `rows` を `"teams"` に変える）。payload のキーの表は `layout/include/result_contract.json`（`data` / `config` / `layout`。リザルトの `data` は `ResultService` が組み立てたものなので、表は閉じた集合: `BuildPayload` の作る payload は表のキーを型どおりに持ち、表に無いキーを持たない。`tests/test_result_kit.py`）。キットの `CONTRACT.md` / `AI_PROMPT.md` / `sample_payload.json`（個人 16 行）/ `sample_payload_teams.json`（チーム 8 行）は `scripts/gen_layout_kit.py` の生成物。レイアウトの選択の下に、一覧に出せなかった kind `result` のフォルダを出す。

#### 4.4.3 ペイロード

```javascript
async function initThumbnail({ data, config, template }) { ... }  // data: score/game/tournamentInfo の state スナップショット
async function initResult({ data, config, layout }) { ... }       // data: { tournamentName, eventName, startAt, numEntrants, players, rows }
// どちらも描画完了で document.title = "RENDER_READY"（契約 1）
Autona.onRender(async ({ data, config, template }) => { ... });  // 契約 2: 合図・待ち・例外の捕捉は autona.js
```

`template` は `{template_id, ...マニフェストの template が指す JSON}`、`layout` は現在のレイアウトの id。形の正本はサービスの `RenderPayload()` と golden。

#### 4.4.4 出力ファイル名

| 種別 | パス | ファイル名 |
|------|------|----------|
| サムネイル | `./out/thumbnails/` | `{tag1}-vs-{tag2}-{YYYYMMDDTHHmmssZ}.png`（YouTube のメタデータ `_title.txt` / `_desc.txt` を添える） |
| リザルト | `./out/results/` | `{tournament}-top{n}-{YYYY-MM-DD-HH-MM-SS}.png`（UTC） |
| プレビュー | `AutonaResolve("tmp/thumbnail")` / `tmp/result` | `preview.png` / `result_preview.png`（毎回上書き） |

識別子の特殊文字（`<>:"/\|?* ` 等）は除去する。

---

## 5. デザインシステム

アプリ内部（デスクトップのシェルと React）には統一されたデザインシステムを適用する。クリエイティブ領域（OBS HTML・画像生成テンプレート）には強制しない（§5.5）。

### 5.1 色トークン

#### 5.1.1 方針

- **機械可読の正本は `assets/design-tokens.json`**。デスクトップ側（`src/ui_constants.py`）と React 側（`web/src/themes.js`）が同一の役割・同一の値を持ち、契約テスト（`tests/test_design_tokens_contract.py` / `web/src/themes.test.js`）がズレを検出する。変更は json → 両コンシューマの順。
- 命名は言語の慣例に従う（`ui_constants`: `snake_case`、`themes.js`: `camelCase`）。テーマは**ダークのみ**。
- ニュートラルは単一の寒色相のクールグレー・ランプ。有彩トークン（brand・accent・gold・セマンティック）は暖色で、グレーに対して際立たせる。accent はスカーレット（純色寄りの赤）、brand（クリムゾン）はライブ / ステータス信号用。

#### 5.1.2 トークンの役割

`canvas` / `bg_primary`〜`bg_quaternary` / `border_subtle`・`default`・`strong` / `text_primary`・`secondary`・`muted`・`label`・`disabled` / `brand`・`brand_subtle` / `accent`・`accent_hover`・`accent_subtle` / `gold`・`gold_subtle` / `positive` / `negative` / `on_accent` / `btn_neutral`・`btn_neutral_hover` / `primary_btn_bg`・`primary_btn_text`・`primary_btn_hover`。値は `design-tokens.json`。

#### 5.1.4 セマンティック色

| セマンティック | 使用トークン |
|------|-----|
| 主アクション（フォーカス・選択） | `accent` / `accent_hover`（塗りのプライマリボタンは `primary_btn_*`） |
| ニュートラルアクション | `btn_neutral` / `btn_neutral_hover` |
| 強調・ライブ | `brand` |
| 成功・確定 | `positive` |
| エラー・危険操作 | `negative`（brand から分離。暗背景での可読性のため） |
| 警告・注目 | `gold` |

MUI のスロットとの対応（`themes.js`）: `error` = `negative`、`success` = `positive`、`warning` = `gold`、`info` = `text_secondary`。

#### 5.1.5 プレイヤー色

P1 / P2 は**テーマ色ではなく競技上の識別色**（`design-tokens.json` の `player_colors`）。React では `theme.palette.p1color` / `p2color`。チームカラーがユーザー指定ならそれが優先（`score.{N}.team.{T}.color`）。P2 背景の上に小さい白文字を置くときは影・縁取り・濃色文字で可読性を担保する。

#### 5.1.6 ハードコード禁止

新規コードでハードコードの hex を書かない。デスクトップは `ui_constants.palette()['key']` か対応するスタイル関数、React は `theme.palette.{slot}` か `theme.palette.autona.{tokenKey}`。

### 5.2 タイポグラフィ・寸法

- フォント: 主フォントは LINE Seed JP（フォールバックは `themes.js` / `ui_constants.py`）、等幅は JetBrains Mono。ウェイトは通常 400、ボタン・タブ・見出し 700。
- サイズ・スペーシング・角丸・ウィジェット高さ・アイコンサイズのトークン（`FONT_*`・`SPACE_*`・`RADIUS_*`・`BTN_*`・`ICON_*`）は `src/ui_constants.py` が正本。**同じトークン名が同等の視覚階層を表すことが仕様**で、実寸は各フレームワークの自然な密度に任せる（`pt` と `px` を一致させない）。React のフォーム要素は `size="small"`（R-React-5）、角丸は MUI の既定を使わず `RADIUS_*` の 3 段階を明示する。

### 5.4 テーマ（ダーク固定）

実行時のライト / ダーク切替は無い。理由: 切替は QApplication 全体のスタイルシートを再適用して全ウィジェットを再ポリッシュし、proxy 埋め込みの大きなウィジェットツリーで GUI スレッドが 30 秒以上固まったため。テーマ適用は起動時の 1 回だけ（`Window.__init__` → `LoadTheme()`）。React も `ThemeShell.tsx` が `darkTheme` を常用する。再導入するなら、この再ポリッシュの再発防止を先に設計する。

### 5.5 クリエイティブ領域

#### 5.5.1 適用しないルール

OBS レイアウト（`layout/scoreboard*` `bracket*` `recent_sets/` `commentators/` `camborder*` ほか）と画像生成テンプレート（`layout/thumbnail*` `result*`）には §5.1〜§5.4 を強制しない。配色・フォント・寸法は自由。共通基盤として残すのは `layout/main.css` の CSS 変数（上書き可）・`layout/include/`・`layout/fonts/`・`layout/icons/`。

#### 5.5.3 大会カラーと機能別カラー

大会ブランドに合わせた色は次の経路で OBS レイアウトに届く。

- `tournamentInfo.themeColor` / `accentColor`（大会共通。既存テンプレート互換の表示用キャッシュ）と、`colors.{feature}.themeColor` / `accentColor`（機能別の上書き。明示設定した feature だけ state に現れる）。正本は `SettingsManager`（§3.1.2）。
- **優先順位**: `colors.{feature}` → `tournamentInfo` → テンプレートの `:root` の既定。
- feature は `board` / `thumbnail` / `result` / `top8` / `commentary` / `stats`。サムネイルとリザルトは Python が payload の `config.theme_color` / `accent_color` に解決する（`ResultService` は `colors.result` → 大会共通で上書き）。
- **大会カラーの変更は「全レイアウトをこの色に揃える」操作**: `core/ColorService.SetTournamentColor(kind, color)` は永続化・state 展開に加えて、全 feature の同じ kind の上書きを削除する（SettingsManager・StateManager の両方）。kind 単位で、テーマ色を変えてもアクセントの上書きは残る。機能別カラーは、その後に個別編集した時点から再び独立する。「既定の色に戻す」（`PATCH /api/v1/colors/tournament` の `{"reset": true}`）も同じ。
- JS 側の読み方（Socket.IO 系レイアウトの共通形）: `const fc = (event.data.colors || {}).board || {}; const themeColor = fc.themeColor || ti.themeColor;`

| ルール | 内容 |
|------|------|
| **R-Brand-1** | 新規テンプレートは `tournamentInfo` / `colors.{feature}.*` を JS から直接読まず、`--theme-color` / `--accent-color` などの CSS 変数経由で使う（解決はエントリ JS が一度だけ行う）。 |
| **R-Brand-2** | テンプレート固有のブランド表現（大会公式の独自配色）は、テンプレートの `settings.json` か `:root` の上書きを優先してよい。 |
| **R-Brand-3** | テンプレートは state の特定キーに直接依存せず、CSS 変数 / DOM クラスを基本とする。 |

---

## 6. ホットキー

### 6.1 設計方針

| ルール | 内容 |
|------|------|
| **R-Hotkey-1** | OS グローバルで登録する（Windows の `RegisterHotKey`。他 OS は非対応）。 |
| **R-Hotkey-2** | デフォルトは `core/HotkeyRules.DEFAULT_KEYS` が正本（`AutonaHotkeys.keys` はそのコピー）。ユーザーの上書きは `settings.json` の `hotkeys.{action_key}` で、デフォルトの上にマージする。 |
| **R-Hotkey-3** | アクションの通知は `AutonaHotkeysSignals` の Signal。`hotkeys.hotkeys_enabled = false` でグローバルに無効化できる。 |
| **R-Hotkey-4** | デフォルトのモディファイアは `Ctrl+Shift+`（標準ショートカットとの衝突を避ける）。 |
| **R-Hotkey-5** | state を書くだけのアクションは `AutonaHotkeys.ConnectActions()` が `AutonaActions` / core のサービスに直結する（`bootstrap_gui()` から 1 回）。対象ボードは `AutonaActions.ActiveBoard()`。ボードのホットキーは GUI スレッドで `boardHost.Run` の中から core を呼び、対象ボードもその中で読む（`AutonaHotkeys._runOnActiveBoard`）。ウィジェットに接続を持つアクションは無い。 |

### 6.2 デフォルトの一覧

デフォルトの値の正本は `HotkeyRules.DEFAULT_KEYS`。アクションと受け手:

| アクションキー | デフォルト | 動作 | 受け手 |
|--------------|-----------|------|-------|
| `load_set` | `Ctrl+Shift+O` | アクティブボードのセット選択（SCORE ページのダイアログ）を開く | `AutonaShellOperations`（クルー形式・ウィンドウ前なら何もしない） |
| `team1_score_up` / `down` | `Ctrl+Shift+1` / `2` | チーム 1 のスコア ±1 | Actions |
| `team2_score_up` / `down` | `Ctrl+Shift+3` / `4` | チーム 2 のスコア ±1 | Actions |
| `reset_scores` | `Ctrl+Shift+R` | スコアリセット | Actions |
| `apply_changes` | `Ctrl+Shift+L` | 反映（保留中の編集を公開） | Actions |
| `swap_teams` | `Ctrl+Shift+S` | チーム入れ替え（クルー形式では何もしない） | Actions |
| `refresh_phase_group` | `Ctrl+Shift+P` | Top 8 を Start.gg から取り込み直す | `Top8Service` |

### 6.3 命名

- アクションキーは `snake_case` で動詞を含む（`team1_score_up`。状態名のみは不可）。Signal 名・設定キー・i18n の対応を保つ唯一の ID で、**一度公開したら変えない**。
- ショートカット文字列は `Ctrl+Shift+{key}`（推奨）/ `Alt+{key}`。キー名は `HotkeyRules.KEYS` の綴り。解析は `core/HotkeyRules.ParseShortcut()`。重複は（修飾キー, VK）で比べる（`HotkeyRules.Conflicts`）。

### 6.4 衝突回避

#### 6.4.1 OS・アプリ標準と被るキー

`Ctrl+S` / `O` / `P` / `R` / `Z` / `Y` / `C` / `V` / `X` / `F`、`Alt+F4` / `Alt+Tab`、`Win+*` は避ける。`Ctrl+Shift+` なら多くの衝突を避けられる。

#### 6.4.2 配信ソフトと被るキー

OBS の標準ホットキーと被ると配信中に意図しない操作が走るため、設定ページが警告を出す（`HotkeyRules.WarningFor` のコード `obs_likely`）。ほかの警告は `os_reserved` / `no_modifier` / `shift_only`。どれも保存は通す。重複の保存は 409 で、相手のアクション名を示す。

### 6.5 設定 UI

編集は React の設定ページのホットキー区画。**組み立て式**（Ctrl / Alt / Shift のチェックと `HotkeyRules.KEYS` からのキーの選択。保存は `Canonical` の綴り）で、押した組み合わせの記録は使わない（parser が読めない綴りを作るため）。個別・全体のリセットができる。React Web UI は OS グローバルのホットキーを持たない（Web のキー操作はページごとにローカルで束ねる）。

---

## 7. 拡張ガイドライン

触るファイルと、触らないものを示す。**ここに書かれていない拡張点は、先に本章を更新する。**

### 7.1 新ゲーム

`assets/games/{game}/base_files/config.json`（ゲーム定義）・`logo.png`・`{asset_type}/...`（キャラ・ステージ画像）。React・state のキー・SCORE ページは触らない（`game.*` と Socket.IO で自動追従）。OBS の見た目の個別調整は、汎用の `scoreboard_*` テンプレートを編集する（ゲーム名による分岐は持たない）。

### 7.2 新 OBS スコアボードレイアウト

`layout/{名前}/index.html`・`index.css`・`index.js`（`Start()` / `Update(event)`）・`*_preview.png`。命名は §4.3.1。Python・state・共通リソースは触らない（フォルダのスキャンで列挙される。一覧に載せるなら `settings.json` の `autona` ブロック。§4.3.3）。
新しく書くなら `globals.js` の直後に `layout/include/autona.js` を読み、`Autona.onUpdate(view, prev)` の整えた形（キーの表は `layout/include/overlay_contract.json`。足すだけの契約 1）を使う（ADR-008 D1。書き方は `layout/README.md`）。

### 7.3 新リザルト画像レイアウト / 7.4 新サムネイルテンプレート

テンプレートパッケージ（§4.4.0）のフォルダを置くだけで、ページの選択肢に出て描ける。

- リザルト: `layout/{名前}/settings.json`（`{"autona": {"kind": "result", "contract": 2, "size": [960, 1080], "rows": "players", ...}}`）・`result.html`（`../include/autona.js` を読む）・`result.css`・`result.js`（`Autona.onRender`）。`rows: "players"` は 16 行、`"teams"` は 8 行渡る。それより多い行は契約に無い（`SLOT_COUNTS`・state の行・payload の変更で、`contract` を上げる話）。契約 1（`initResult`）も読み続ける。リザルトのページの「新しいデザインを作る」でキットから作るのが早い（§4.4.2）。
- サムネイル: `layout/{名前}/settings.json`（`kind: "thumbnail"`・`contract: 2`・`template`）・`thumbnail.html`（`../include/autona.js` を読む）・`thumbnail.css`・`thumbnail.js`（`Autona.onRender`）・テンプレート定義 JSON。書き方は `layout/README.md`。サムネイルのページの「新しいデザインを作る」でキットから作るのが早い（§4.4.1）。
- 同梱として出すときは `git add` してリリースの zip に入れる（`validate_release_zip.ps1` は追跡済みのファイルだけを通す）。凍結した表と `CoreWords` は、id とフォルダ名が違う既存 8 種のためのもので足さない。
- 触らない: state・Python（`ResultService` / `ThumbnailService` / レンダラー）・React（選択肢は `GET /api/v1/layouts?kind=` から出る）。
- ページはプレビュー用のリスナーのオリジンで描かれる（R-Img-9）。読めるのはパッケージ・`../include/`・`../../assets/`・payload のパスだけで、外部（CDN）・file:・ほかのポートへの要求は届かない（WebRTC の UDP は除く）。ライブラリ・フォントはパッケージに入れる。
- **sandbox**: レンダラーは `QTWEBENGINE_DISABLE_SANDBOX` を設定しない（Chromium の sandbox のまま描く）。以前はレンダラーのモジュールが import 時に `=1` を設定していたが、QtWebEngine がこの変数を読むのは最初の初期化の 1 回だけで、アプリでは共有ビューがレンダラーより先に WebEngine を初期化するため効いていなかった（実測: 遅れて設定しても描画のプロセスに `--no-sandbox` は付かない）。外した今は、レンダラーが先に初期化した場合も sandbox が有効になるだけで、挙動は共有ビューと同じ（sandbox を外していたのは、レンダラーが先に WebEngine を立ち上げたプロセス＝テストだけ）。CI（`.github/workflows/ci.yml`）はヘッドレスの runner の都合で明示的に設定している（アプリの挙動ではない。テストは設定しなくても通る）。

### 7.5 新ページ

1. `web/src/{PageName}/`（新規）と `web/src/PanelShell/registry.tsx` の `PANELS` にコンポーネントを登録。
2. `src/core/WebAccess.py` の `WEB_PANELS` に名前を足す（サーバーが `/web/<名前>` と `/web/app/<名前>` で返す名前）。
3. サイドバーの項目なら `web/src/AppShell/navSections.json` に `{panel, icon, label}`（アイコンは `assets/icons/`、文言は `app_*` キーで en / ja）。`tests/test_app_shell.py` の `NAVIGATION`（ナビの凍結した写し）との照合を、今のナビの表に書き直すかを判断する。
4. Help & Settings から開くメニューのページなら `web/src/AppShell/navTable.ts` と `src/AutonaWindowState.py` の `MENU_PAGES` の両方に足す（`tests/test_app_shell.py` が一致を pin）。
5. 任意: 文言（`web/src/i18n/locales/{en,ja}.json`）、core のサービスと v1 のルート（`src/AutonaApiV1.py`・`src/core/WebAccess.py` の `ROUTE_ACCESS`。書き込みは core に置き、ページは v1 で依頼する）、state を持つなら `StateSchema` のシードと `KEY_CATALOG`。
6. 本書 §4.1.1 のページ一覧に追加する。`src/Autona.py` は触らない。ページを開いていなくても成立させるべき初期化はページに置かない（§4.1.2）。ブラウザ向けの独立ルートが要るなら `web/src/index.js` に追加する。

### 7.6 新 state キー

`src/core/StateSchema.py` に定数と `KEY_CATALOG` を足し（新しいキーは **PR レビューで差分を確認**する。テストでは強制できない）、書き込みは `AutonaActions` か core のドメインサービスに置く。OBS テンプレートは `Update()` で `data.score[N].xxx` を参照する。React の型は `web/src/redux/autonaState.ts`。`StateManager.py` と Web サーバーのルートは通常触らない。保存済みのキー名・形を変えるなら移行 step（§3.3.4）。

### 7.7 新ホットキー

1. `AutonaHotkeysSignals` に Signal を追加。
2. `core/HotkeyRules.DEFAULT_KEYS` にデフォルトを追加（表示順もこの順）。
3. 受け手を結ぶ: state を書くだけのアクションは `AutonaHotkeys.ConnectActions()`（§6.1 R-Hotkey-5）。
4. 設定ページの表示順 `HOTKEY_ACTIONS`（`web/src/SettingsPage/Cards.tsx`）と表示名 `settings_hotkey_action_<action>`（`web/src/i18n/locales/{en,ja}.json`）。
5. 本書 §6.2 の表に追加し、OBS・アプリ標準との衝突を確認する。

### 7.8 新言語

`src/i18n/Autona_{lang}.ts`（`compile_translations.ps1` で `.qm`。`.ts` は手で編集）と `src/core/CoreWords.py`（`LANGUAGES` と `WORDS` の各語）、`web/src/i18n/locales/{lang}.json`、`web/src/i18n/config.js`、`src/i18n/mapping.json` の `languages`（設定ページの選択肢の元）。

### 7.9 新外部サービス（tournament provider）

provider 実装（`src/TournamentDataProvider/` 配下、例: `HiveeDataProvider.py`）と `AutonaTournamentDataProvider.py` のアダプタ（`LoadTournament` / `UnloadTournament`）・URL 判定（`core/TournamentService.UrlKind`）に繋ぐ。外部 API のレスポンス構造を UI に漏らさず、state には Autona 共通のモデルに正規化して入れる。認証情報は `SettingsManager` に置き、state・ログ・READ の API に出さない（Hivee の URL のトークンは `HiveeUrl.Mask` / `TournamentService.PublishedUrl` で伏せる）。provider に紐づくメモリは、provider の差し替えの**直後に** `Forget()` で捨てる。provider の取得は strict（失敗と空を区別）で頼む。実例は `src/TournamentDataProvider/HiveeDataProvider.py` と `tests/test_hivee_provider.py`。

### 7.10 新統計指標

`src/core/StatsService.py` に算出・書き込み・取り直しのトリガー（`PlayerChanged` / `TeamsChanged` / `RefreshAll`）を足し、表示は `web/src/StatsPage/`。取得が要るなら `AutonaStatsUtil`（取得の輸送だけの Qt アダプタ）の fetcher に 1 メソッド足す。派生値は原則 read-only で、保存が要るときだけ state に置く（§3.1.2 の規則）。

### 7.11 新アセット種別

`user_data/games/{game}/{asset_type}/config.json` と `src/core/GameAssetCatalog.py`（解決とカタログのペイロード `*Payload()`）。state には asset id / 論理パスを保持し、実パスの解決はカタログに寄せる。テンプレートにファイルパスをハードコードしない。

### 7.12 新設定項目

`SettingsManager` のキーを §3.3.3 の命名で定義し、既定値は利用箇所で持つ。設定ページの項目なら `core/AppSettings`（表）と `GET` / `PATCH /api/v1/settings*`、React の `web/src/SettingsPage/`。トップレベルのキーは増やさない。state に展開するなら「キャッシュで正本ではない」と明記する。API トークン・アカウント情報は state に置かない。
