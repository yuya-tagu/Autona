# アーキテクチャ抜本見直し（現状棚卸し → レビュー → あるべき姿）

PdM 方針（2026-10-23）「Autona のアーキテクチャを現代的に抜本修正したい」を受けた検討書。
本書は (1) 現状の事実棚卸し、(2) 外部レビュー（Fable）、(3) あるべきアーキテクチャの再定義、を 1 本にまとめる。
決定事項は [ADR-005](../decisions/ADR-005-web-first-ui-thin-shell.md)（2026-10-23 Accepted）。移行の実施記録は本書 §5 に追記する。

前提となる先行再設計: [ADR-003 起動アーキ](../decisions/ADR-003-startup-architecture.md)（完了）/
[ADR-004 Headless Core](../decisions/ADR-004-headless-core-write-path.md)（Step 0〜5 完了・2026-09-03）。

---

## 1. 前提・制約（再設計で変わらないもの／変わり得るもの）

| 項目 | 現状 | 再設計での扱い |
|------|------|---------------|
| 利用者 | 1〜2 名運営の格ゲー大会（32〜256 名）。TO 兼配信オペレーター | 不変 |
| 配布 | Windows exe（PyInstaller onedir）+ 自動更新（`yuya-tagu/Autona-releases`）。配布中・自動更新ユーザーあり | 自動更新の互換（既存インストールが新版へ上がれること）は必須 |
| ライセンス | MIT（2026-10 移行）。PySide6 (LGPL) 必須・GPL 禁止 | 不変 |
| OSS 化 | 公開前に技術負債を潰す方針（2026-10-20 決定） | 本再設計はその一部として位置付ける |
| OBS 連携 | ブラウザソース（`layout/`）+ `out/` ファイル。obs-websocket は使わない | 外部契約（後述 §2.8）は維持 |
| チーム | オーナー 1 名 + Claude Code | 保守コストの低さを最優先 |

## 2. 現状棚卸し（2026-10-23 時点・コード実測）

### 2.1 規模

| 領域 | ファイル | 行数 |
|------|---------|------|
| `src/`（Python） | 99 | 38,476（うち `core/` 17 ファイル 5,279） |
| `main.py` | 1 | 302 |
| `web/src/`（React） | 66 | 約 13,500（Designer 9,344 / ScoreboardPage 2,606） |
| `layout/`（OBS オーバーレイ・素の HTML/JS） | 180 | 約 14,500（ベンダー除く） |
| `tests/` | 59 | 21,572 |

### 2.2 機能一覧（サイドバー構成順）

| 区分 | 機能 | 主ファイル | 出力 |
|------|------|-----------|------|
| Setup | 大会情報（EVENT）: Start.gg 取込・大会カラー | `AutonaTournamentInfoWidget` / `core/TournamentInfoService` | `tournamentInfo.*` |
| Setup | 配信設定: 配信先・タイトル生成・YouTube サムネ | `AutonaStreamSettingsWidget` | `stream.*` / `out/stream/` |
| Scoreboard | スコアボード入力: board1 1v1 / board2 チーム / board3 クルー。スコア・Bo・phase/pool/match・スワップ・リセット・セット選択・ステーション・反映（held）・X intent | `AutonaScoreboardWidget`(2,306) / `AutonaScoreboardPlayerWidget`(1,185) / `AutonaCrewBattleWidget` / core 各サービス | `score.{n}.*` |
| Scoreboard | BOARD: デザイン選択・ライブプレビュー・機能別カラー・Designer 起動 | `AutonaBoardWidget` / `AutonaLayoutPreview` / `AutonaDesignerService` | — |
| Display | サムネイル（動画サムネ 2 テンプレ） | `AutonaThumbnailSettingsWidget` / `thumbnail/ThumbnailRenderer`（オフスクリーン WebEngine） | `out/thumbnails/` |
| Display | リザルト画像（6 レイアウト）・コピー・保存・X | `AutonaResultSettingsWidget`(1,330) / `result/ResultRenderer` | `out/results/` |
| Display | TOP 8（固定 8 名 DE）・Start.gg 取込 | `AutonaTop8Widget` / `AutonaTop8Model` / `core/Top8Service` | `top8.*` |
| Display | 実況・解説（12 枠・表示最大 4） | `AutonaCommentaryWidget` / `AutonaCommentaryModel` | `commentary.*` |
| Display | 統計表示設定（統計オーバーレイ 5 種） | `AutonaStatsPreviewWidget` | `colors.stats` |
| Data | 統計（H2H / Last Sets / History / Analysis） | `AutonaStatsWidget` / `AutonaAnalysisWidget` / `core/StatsService` | `score.{n}.*_sets` |
| Data | 選手リスト・ローカル選手 DB | `AutonaPlayerBrowserWidget` / `AutonaPlayerDB` | `user_data/local_players.json` |
| 共通 | Settings（General / Language / Hotkeys）・ゲームアセット追加・API/Companion/OBS ガイド・アップデータ・アラート | `Settings/` / `AutonaGameAssetGuideWindow` / `AutonaUpdater`(899) | — |
| 共通 | グローバルホットキー 9 本（Win32 `RegisterHotKey`） | `AutonaHotkeys` | — |
| Web | リモート操作 UI（`/web`）・Designer（1920×1080 レイヤーエディタ、Figma 取込） | `web/src/ScoreboardPage` / `ScoreboardDesigner` | `layout/custom/{slug}/` |

> `docs/product/features.md` は陳腐化している（BlueSky・コントローラー検出・QDockWidget 自由配置など存在しない機能の記載、実況 8 枠→実際 12 枠、BOARD / Designer / Figma / 統計表示設定の記載漏れ）。

### 2.3 層構成

```
main.py（単一インスタンス mutex → QApplication → splash → import src.Autona → qasync loop）
  │
  ├─ src/Autona.py  QMainWindow（1,547 行）: サイドバー + QStackedWidget、パネル 10 個を遅延構築
  │     └─ Qt ウィジェット群（約 16,000 行・55 ファイルが Qt import）
  │
  ├─ src/bootstrap.py  bootstrap_core(): シングルトン 16 段の明示生成順
  │
  ├─ src/core/（Qt import ゼロ・テストで pin）
  │     StateManager(1,040)  nested dict + RLock + DeepDiff + held/commit + out/ 出力スレッド
  │     StateSchema(721)     KEY_CATALOG・シード
  │     Actions(652) + ドメインサービス 10 個（Player / SetLoad / Stats / CrewBattle / Top8 / Color / …）
  │
  ├─ adapters
  │     StateManagerBridge   observer → Qt Signal
  │     AutonaWebServer(1,212, QThread)  Flask + Flask-SocketIO（threading mode）
  │     AutonaWebServerActions(620)      Web → ウィジェット操作のリモコン
  │     AutonaApiV1(966)                 /api/v1 Blueprint（Qt-free）
  │
  ├─ providers / services（Qt 依存が混在）
  │     StartGGDataProvider(2,292, Qt-free) / AutonaTournamentDataProvider(416, QObject ファサード)
  │     AutonaGameAssetManager(1,501, QObject・Qt モデル構築と state publish が同居)
  │     AutonaUpdater(899) / AutonaDesignerService(877, Qt-free) / AutonaPlayerDB
  │
  └─ renderers  thumbnail/ result/（QWebEngine で HTML を描画して PNG 化）
```

- 被依存上位: `SettingsManager` 37 / `StateManager` 35 / `ui_constants` 22 / `StateSchema` 19
- `.instance` シングルトン 13 個。`AutonaGameAssetManager.instance` は 18 ファイル 112 箇所、`AutonaTournamentDataProvider.instance` は 15 ファイル 108 箇所。DI なし
- core → src 直下への逆依存あり（`AutonaCommentaryModel` / `AutonaTop8Model` / `Helpers/AutonaDictHelper`）

### 2.4 状態とデータフロー

```
[書き込み]                                         [読み出し]
Qt UI ──► AutonaActions / core サービス ─┐
REST v1 ─► WebServerActions ─► ┬ Actions ┤
legacy REST + Socket.IO ──────►│         ├─► StateManager ─┬─► out/program_state.json + out/<keypath>.txt（別スレッド）
ホットキー ─► Actions（6 本）    └ ウィジェット操作（GUI 同期）  ├─► Bridge(Qt Signal) ─► ウィジェット再描画
         └► ウィジェット直結（3 本）                        └─► WebServer debounce ─► Socket.IO program_state_update
                                                                    ├─► layout/ オーバーレイ（OBS）
                                                                    └─► web/ リモート UI（Redux）
```

- held/commit（「反映」）: `HoldSubtree` 配下の書き込みを溜め、`CommitHeld` で公開
- オーバーレイは Socket.IO で受信、`file://` 時は `out/program_state.json` を 64ms ポーリング
- 状態スキーマのマイグレーション層なし（`STATE_VERSION` は QSettings キーのみ）

### 2.5 書き込み経路の残課題（ADR-004 完了後もなお残るもの）

ADR-004 は「ウィジェットが `StateManager.Set` を直接呼ばない」を達成したが、**Web → ウィジェット遠隔操作**は残っている:

- `AutonaWebServerActions` に `GetScoreboard(` 42 回。シグナル emit / `setValue` / `findChild(QLineEdit).setText` + `editingFinished.emit()` 等で**ウィジェット経由で core に到達**する操作が残る（`clear_all` / `set_route` の players・characters・teamName / `swap_teams` / `load_set` / `reset_match`）
- これらは `@gui_thread_sync`（WebServerActions 21 + ApiV1 7）で Flask スレッドから GUI スレッドへ同期ブロック
- チームスワップの正本がウィジェット属性に残っている。`score.{n}.team.{t}.score` の書き手が core に 3 つ + スピンボックス経由
- ホットキー 3 本（load_set / swap_teams / refresh_phase_group）がウィジェット直結
- 外部入口が 3 系統: legacy REST 45 ルート（`.route(` 実数）+ Socket.IO 41 イベント（ほぼ 1:1 の二重定義、v2.0 で削除予定）+ `/api/v1` 31 ルート

### 2.6 並行処理

5 系統が併存: qasync `QEventLoop`（asyncio は実質未活用）/ `QThread` サブクラス（WebServer・Updater・AssetGuide・LayoutPreview）/ `QThreadPool + Worker(QRunnable)`（provider 系 24 箇所）/ `ThreadPoolExecutor`（Start.gg 並列フェッチ）/ `threading.Thread`・`Timer`（StateManager 出力・画像 DL・delta debounce）。
Actions は「どのスレッドから呼んでもよい」設計で、observer はスレッド同期通知のため、Flask スレッドから呼ぶと Bridge の Qt Signal が非 GUI スレッドから emit される（AutoConnection 任せ）。

### 2.7 永続化

`user_data/settings.json`（トップキー 17・旧値残留あり）/ `layout_options.json` / `local_players.json` + `.csv` / `characters.json` / `games/`（アセットパック）/ ロゴ・アバター類 / QSettings（サイドバー状態）/ `out/program_state.json`（起動時に LoadState）。
（注: S3b（2026-10-25）で state の正本は `user_data/state.json`（`state_version` 付き）へ移った。`out/program_state.json` は出力に戻り、起動時に読むのは移行のときだけ。下の S3b 節）

### 2.8 維持すべき外部契約

1. `out/` 配下: `program_state.json`（バッチ最後に書く＝ASS 監視の順序契約）・キーパス 1:1 の `.txt`・画像
2. OBS URL: `http://localhost:{port}/layout/{dir}/{entry}` / BOARD は**ローカルファイルパス**（file:// + ポーリング）
3. Socket.IO `program_state` / `program_state_update`（`revision` / `delta_index` / `first_revision` / `delta`）
4. `/api/v1` の全ルートとエラー封筒（legacy は v2.0 削除予定と告知済み）
5. 保存データ: `settings.json` / `local_players.json` / `layout/custom/{slug}/project.json`（schemaVersion 3）
6. レイアウト契約: `settings.json` の `autona` ブロック・`globals.js` のイベント・`thumbnail.html` / `result.html` 命名
7. アセットパック: TSH / StreamHelperAssets 互換
8. 自動更新: 既存インストールのアップデーター（`Autona-releases` 参照・zip 構造）

### 2.9 構造上の問題（観察）

| # | 問題 | 根拠 |
|---|------|------|
| P1 | **ドメインの一部がまだウィジェットに住む**（スワップ・セット読込起点・クリア） | §2.5 |
| P2 | **外部入口 3 系統の二重・三重定義** | legacy 45 + SIO 41 + v1 31 |
| P3 | **スレッドモデルが規約任せ**（5 系統併存、Actions のスレッド境界未定義） | §2.6 |
| P4 | **グローバルシングルトン結合**（`.instance` 100 箇所超 × 2、DI なし） | §2.3 |
| P5 | **God クラス**: ScoreboardWidget 2,306 / StartGG 2,292 / Autona.py 1,547 / GameAssetManager 1,501 / ResultSettings 1,330 | 行数 |
| P6 | **状態が型なし nested dict**。スキーマはキーパス定数のみで値の型・バリデーション・マイグレーションなし | StateManager / StateSchema |
| P7 | **3 つのフロントエンド技術が併存**: Qt Widgets（操作）/ React（Web 操作・Designer）/ jQuery+GSAP 素 HTML（オーバーレイ）。同じ画面（スコア入力）が Qt と React で二重実装 | §2.2 |
| P8 | **WebEngine の多用**（プレビュー 5 パネル + サムネ/リザルト描画 + Designer）。exe サイズ・メモリ・Qt ネイティブクラッシュ地雷（agent-pitfalls #9〜#15）の主因 | §2.3 |
| P9 | 書き込み規則の重複（`_SetIfChanged` 5 実装・`NoIconProxyModel` 2・`remove_accents_lower` 3） | grep |
| P10 | 依存管理の二重化（`requirements.txt` と `Pipfile`、不要な `asyncio` pip 依存） | dependencies/ |

---

## 3. 外部レビュー（Fable・2026-10-23）

Fable（claude-fable-5-1）に §1〜§2 とコードを渡し、結論を伏せて独立レビューさせた。要旨と、エンジニアリードによる裏取り結果。

### 3.1 指摘の要旨

**残すべき資産**: core の Qt-free 境界と pin テスト / `bootstrap.py` の明示生成順 / StateManager の出力パイプライン（held/commit・アトミック書き込み・単一ライタースレッド・revision/delta）/ `/api/v1`（冪等性・封筒・revision）/ React スコアボードは既に v1 + Socket.IO + 楽観更新で稼働 / **Designer は既に「React を QWebEngineView で包む」構成で稼働中**（Web-first UI の実証）。

**根本問題（棚卸し §2.9 の再整理）**:

| # | 問題 | 根拠 | 裏取り |
|---|------|------|--------|
| R1 | 選手スロット・スワップの正本がまだウィジェット | `AutonaBasePlayerWidget.SwapWith`（L341）が `findChildren` で値を吸い出してコピー。`teamsSwapped` はウィジェット属性。`Actions.py` docstring が「state は出力であって正本ではない」と明記 | ✅ |
| R2 | **Qt モデルがデータ層になっている**（R1 の上流） | `AutonaGameAssetManager` がキャラ・スキン・ステージを `QStandardItemModel` で保持し、`get_characters` は GUI スレッドで行を歩いて dict に戻す。問題は行数ではなく「データが Qt オブジェクトの中にある」こと | ✅ |
| R3 | **永続化と出力契約が同一ファイル** | `StateManager.LoadState` が `./out/program_state.json`（L207）を読む。スキーマ変更＝配信中ユーザーの状態破壊リスク。型付きスキーマ導入の最大の障害 | ✅ |
| R4 | 外部入口 3 系統 + GUI 同期ブロック | `@gui_thread_sync` 22 箇所が Flask スレッドから GUI を同期待ち。GUI が重いと API 応答が巻き添え | ✅（legacy ルート数を 57→45 に訂正済み） |
| R5 | スレッドモデルが規約任せ | observer は呼び出しスレッドで同期通知、qasync は実質未使用 | ✅ |
| R6 | 3 つの UI 技術と WebEngine 依存 | Qt Widgets 約 26k 行 / React 13.5k / jQuery オーバーレイ 14.5k。`_internal/` 579MB のうち大半が PySide6（WebEngine）。agent-pitfalls #9〜#15 は全て「ドメイン/データが Qt オブジェクトの寿命に乗っている」症状 | ✅（579MB 実測） |

### 3.2 選択肢比較（Fable）

| | (1) 現スタックで層を完成 | (2) Python core + Web UI + 薄い Qt シェル | (3) Tauri / Electron |
|---|---|---|---|
| 効果 | 低リスク | UI 技術 1 本化・SCORE 二重実装消滅・Qt 地雷 #13〜#15 が構造ごと消える・Tier 2（dashboard/複数オペ）が副産物 | 配布 100MB 台 |
| 残る問題 | 3 UI・WebEngine・26k 行の Qt は残る | WebEngine は当面残る（サイズ減らず）。IME/フォーカス挙動差。i18n 移行 | Python backend の書き直し or sidecar で 2 ランタイム保守 |
| 配布・自動更新 | 影響なし | **影響なし**（onedir・zip 構造不変） | exe/zip 構造が変わり鶏と卵問題が再来 |
| 1 人 + AI 保守 | ◎ | ○ | △ |

Fable の評価: **ADR-004 Alternative (B) は「スタック変更」と「UI 技術の統一」を混同して一括却下している。Python core と配布機構を残したまま UI だけ Web に寄せる (2) は (B) の却下理由（実地知識の再学習・配布互換）に当たらない。** (C) 別プロセス常駐の却下は妥当だが、core のヘッドレス起動は要求すべき。(3) は (2) 完了後に「シェルの差し替え」として再検討可能。

### 3.3 エンジニアリードの所見

Fable の結論 (2) に**同意**する。理由:

- ADR-004 で書き込みを core に寄せた結果、Qt ウィジェットに残った責務は「表示」と「R1/R2 の残骸」だけになった。表示だけなら既存の React（スコアボード・Designer）に寄せる方が、Qt ネイティブクラッシュ地雷（過去最大の保守コスト）を構造ごと捨てられる
- 配布・自動更新の互換が保たれる唯一の「抜本」案である（(3) は配布中ユーザーへのリスクが見合わない）
- 先行 3 段（R2→R1→R3 の解消）は (1)(2) どちらに着地しても必要な基盤なので、UI 全面移行の投資判断を先送りしたまま着手できる

補足・修正点:

- **IME リスクは S4 の最初のパネルで実機ゲートを置く**（日本語入力が多い Commentary を最初にして、オペレーター入力速度を確認してから続行判断）
- サムネ / リザルト描画は当面オフスクリーン WebEngine のまま（HTML テンプレ契約 `thumbnail.html` / `result.html` を変えない）。ブラウザ側 canvas 化は S-final の判断材料
- P9（重複実装）/ P10（依存二重管理）/ `features.md` 陳腐化は S0 の掃除として先に潰す

## 4. あるべきアーキテクチャ（再定義）

### 4.1 一言で

**「Python Headless Core + 単一 Web UI（React）+ 薄い Qt シェル」**。
core が状態とドメインの唯一の正本で、すべての UI（デスクトップ内 UI・リモート UI・Companion・ホットキー）は同じ `/api/v1` + Socket.IO を使うクライアントの一つにすぎない。

### 4.2 構成

```
┌─ Shell（PySide6・最小）──────────────────────────────────────┐
│ QMainWindow = QWebEngineView 1 枚                             │
│ 単一インスタンス / トレイ / Win32 ホットキー / ファイルダイアログ │
│ Updater 起動（現行ヘルパー・zip 構造のまま）                    │
└──────────────┬───────────────────────────────────────────────┘
               │ http://127.0.0.1:{port}   (+ QWebChannel: シェル専用操作のみ)
┌─ Web UI（React / TypeScript・1 本）──────────────────────────┐
│ /web: SCORE・TEAM・CREW・EVENT・STREAM・TOP8・COMMENTARY・     │
│       RESULT・THUMBNAIL・STATS・PLAYERS・SETTINGS・BOARD/Designer│
└──────────────┬───────────────────────────────────────────────┘
               │ Command: /api/v1          ▲ Event: Socket.IO delta
┌─ Core（Python・Qt import ゼロ・--headless で単独起動可）───────┐
│ Actions        コマンド入口・唯一の書き手                       │
│ Domain         Player / SetLoad / Crew / Top8 / Stats / Tracker │
│ Catalogs       GameAssetCatalog / PlayerDB / LayoutCatalog（純 dict）│
│ Providers      TournamentProvider（Protocol）← StartGG          │
│ State          型付きスキーマ + state_version + migrations      │
│ Persistence    user_data/state.json（正本）                      │
│ Exporters      out/*.txt + out/program_state.json（派生・契約不変）│
│ Tasks          ThreadPoolExecutor 1 本（Start.gg・画像 DL 等）    │
│ Renderers      thumbnail / result（当面オフスクリーン WebEngine）  │
└──────────────┬───────────────────────────────────────────────┘
               ▼
        layout/ オーバーレイ（OBS・素 HTML / Designer ランタイム。契約不変）
```

### 4.3 設計原則

| 領域 | 原則 |
|------|------|
| 正本 | 状態・ドメイン・カタログ（キャラ/スキン/ステージ/選手 DB）はすべて core の純 Python データ。**Qt モデルは表示用の派生物に限る**（最終的には不要） |
| 書き込み | `AutonaActions` のメソッド = コマンド。`WebServerActions` は廃止し、v1 ルートが Actions を直接呼ぶ。`@gui_thread_sync` ゼロが完了条件 |
| 入口 | `/api/v1` + Socket.IO の 1 系統。デスクトップ内 UI も同じ API を使う（特権経路を作らない）。legacy REST/SIO は v2.0 で削除 |
| 状態モデル | ワイヤ形式（nested dict / キーパス）は外部契約として維持し、その上に dataclass/TypedDict の型付きビューと `state_version` + 前方一方向マイグレーションを置く。JSON Schema から `web/src/backendDataTypes.d.ts` を生成し Python/TS の型ドリフトを止める |
| 永続化 | 正本 `user_data/state.json` と派生出力 `out/` を分離。`out/` の書き込み順契約は現行ロジックを移設するだけで変えない |
| 並行処理 | core は RLock で直列化・どのスレッドから呼んでもよい。長時間処理は core 内の Executor 1 本に統一し、`QThreadPool+Worker` / `threading.Timer` / qasync を廃止。Qt への配送は Bridge だけ |
| UI | 新規 Qt Widgets パネルは**今日から凍結**（追加しない）。新機能は React 側で作る |
| 拡張点 | `TournamentProvider` / アセットパック（ADR-002）/ レイアウト（`project.json`）の 3 点を Protocol として明文化。それ以外のプラグイン機構は作らない |
| テスト | ① core 単体（Qt なし）② API 契約（Flask test client）③ React（RTL）④ E2E: `--headless` 起動 + Playwright で `/web` とオーバーレイ。Qt 実アプリ fixture はシェルのスモークのみに縮退 |

### 4.4 移行ステップ（ストラングラー・1 PR = 独立リリース可能）

| 段 | 内容 | 完了条件 |
|----|------|---------|
| **S0 掃除** | `_SetIfChanged` 等の重複統合 / `Pipfile` 廃止・`asyncio` 依存削除 / core→src 逆依存（Top8Model・CommentaryModel・DictHelper）を core へ移設 / `features.md` を実態に同期 | 重複ゼロ・依存定義 1 本・core が src 直下を import しない（pin 追加） |
| **S1 アセットカタログの core 化**（R2） | `AutonaGameAssetManager` を `core/GameAssetCatalog`（純 dict + パス解決 + Start.gg ID 対応）と Qt アダプタ（dict→`QStandardItemModel` の一方向構築）に分割 | カタログが `test_core_qt_free` に載る。`get_characters` / `get_variants` / `get_games` から `@gui_thread_sync` が消える。`PlayerService` がキャラ/スキン/国を自分で解決できる |
| **S2 選手スロットの正本を state へ**（R1） | `SwapWith` / `ClearScore` / `ResetAllInputs` / `CommandClearAll` を `Actions.SwapTeams(board)` / `ClearBoard(board)` へ。選手カードは state から描画。残りのホットキー 3 本も Actions へ | `WebServerActions` の `GetScoreboard(` が board 1/2 でゼロ。`teamsSwapped` 属性削除。swap を Qt なしでテスト。「1 操作で delta 1 回」検証 |
| **S3 ヘッドレス起動と永続化分離**（R3・R4） | `main.py --headless`（QCoreApplication + イベントループ。QtGui なしで core + WebServer）。正本を `user_data/state.json` へ（初回は `out/program_state.json` から移行）。`state_version` + 移行関数 1 本目。Web の公開面を締める（静的配信の許可リスト + LAN トークン認証 + CSRF 対策）。`WebServerActions` を core 直結分だけ縮める（2026-10-25 改定。§5 S3 設計） | ヘッドレスで OBS オーバーレイと `/web` の表示、core 直結の操作が動く（スモーク + Playwright）。LAN の書き込みはトークン必須。正本は `user_data/state.json`。**`WebServerActions` 廃止と「`@gui_thread_sync` の残りは thumbnail/result とウィンドウ操作のみ」は S3e へ移動** |
| **S4〜 パネル単位の React 化**（R6） | `QStackedWidget` の各ページを `QWebEngineView(/web/<panel>)` に差し替え。順序: Commentary（**IME 実機ゲート**）→ Stream → Event → Top8 → Stats → PlayerDB → Result/Thumbnail（設定 UI のみ）→ Board → SCORE（Crew 含む）→ Settings | パネルごとに Qt 版削除 + 手動チェックリスト該当節通過 |
| **S-final シェル化** | Window = WebEngineView 1 枚 + トレイ + ホットキー。i18n を i18next に一本化。WebView2 化の要否を判断 | Qt Widgets パネル 0 |

**外部契約（§2.8）の守り方**: StateManager の export 部分は S3 でモジュール分離するだけでロジックを変えない（既存テストが pin）/ Socket.IO のイベント名・payload・revision は不変 / v1 は追加のみ / `settings.json`・`local_players.json`・`project.json` は読み替えのみで形式不変 / アセットパック不変 / **自動更新は onedir・zip 構造・ヘルパーに一切触らない**（S-final で WebView2 化する場合のみ例外とし、旧版からの実機更新テストを必須にする）。

### 4.5 やらないこと

Tauri / Electron 移行 / core の別プロセス常駐（ヘッドレス起動で代替）/ `layout/` オーバーレイの書き直し / obs-websocket / 新規 Qt Widgets パネルの追加。

### 4.6 オーナー判断が必要な論点

1. **UI の着地点を (2) Web-first にするか、(1) 層の完成で止めるか** — S0〜S3 は両案共通なので、S4 着手前までに決めればよい。S4 以降はパネル 11 枚 × 各 1〜3 週の投資
2. **長期のシェル技術**: QtWebEngine 継続（安全・約 580MB）か WebView2 化（軽量・Edge ランタイム依存・描画系の書き換え）か — S-final まで先送り可
3. **LAN 公開時のトークン認証**の導入時期: Web-first では UI = API になるため、S3 までに入れるか v2.0（legacy 削除）に合わせるか

推奨: 1 は (2)、2 は先送り、3 は S3 と同時（ヘッドレス起動で LAN 越しの操作が現実的になるため）。

---

## 5. 移行の実施記録

### S0 掃除 ✅ 完了（2026-10-23・PR #215 マージ済み）

- **core → src 逆依存ゼロ**: `Helpers/AutonaDictHelper` → `core/DictHelper`、`AutonaCommentaryModel` → `core/CommentaryModel`、`AutonaTop8Model` → `core/Top8Model` へ移設（互換 shim なし）。
  pin: `tests/test_core_qt_free.py::test_no_core_module_imports_src_outside_core`（AST・遅延 import 含む）+ subprocess 検査に「core import 後に `src.core` 外の `src.*` が sys.modules に無い」を追加
- **重複統合**: `_SetIfChanged` 4 実装（ロジック同一）→ `StateManager.SetIfChanged`（`StateSchema.PublishAssetVersion` の inline ガードも寄せた）。
  `NoIconProxyModel` 3 実装 → `Helpers/AutonaQtHelper`。`remove_accents_lower` 3 実装 → `core/TextHelper`（`Autona.py` 版はデッドコードで削除）。
  `test_objectname_keypath_contract` の書き手検出に `SetIfChanged` を追加（ウィジェットからの抜け道防止）
- **依存定義の一本化**: `requirements.txt` から `asyncio`（stdlib の非推奨 backport）を削除。`Pipfile*` は元々 `.gitignore` 対象のローカルファイルだったため git 上の変更はなし（ローカルから除去）
- **features.md を実態に同期**（存在しない機能の削除・実況 12 枠・サイドバー構成・ホットキー・BOARD/Designer/Figma/統計表示設定の追記・既定ポート 5107）
- 残置: `PlayerService` 内の inline ガード（unconditional writes が意図的な領域）

### S1a ゲームアセットカタログの抽出とアダプタ委譲（2026-10-23・PR #218）

- **`src/core/GameAssetCatalog.py` を新設**（Qt 非依存・import 副作用ゼロ。`test_core_qt_free` が自動で対象に含める）。ゲーム・キャラ・スキン・バリアント・カラー・ステージ・アセット索引・Start.gg ID 表を、不変の `GameAssetSnapshot`（純 dict）として持つ。ロードは別スナップショットを組んで参照を 1 回で差し替えるので、リーダーは `Current()` を 1 回取れば 2 ゲームが混ざらない。
  書き手（`ResetGameState` / `ScanGames` / `SelectGame` / `ForgetSelection` / `ReloadStartGGCharacters`）は `threading.Lock` 1 本で直列化。ロック保持中に `run_sync`・Qt シグナル・observer を呼ばない（`SaveBlock` はロックの外で開く。ロック順は catalog → `StateManager.lock`）。
  **例外は `SelectGame(progress=...)` のコールバックだけ**で、これはロック保持中に呼ばれる。渡す側はブロックしないこと（queued で投げるだけにする。アダプタは `AutonaStatusManager` の push / update を渡しており、これは queued シグナルを emit するだけ）。逆向きに、StateManager の observer（Bridge の direct 接続ハンドラ含む）から書き手を呼ぶとロック順が逆転してデッドロックする（agent-pitfalls #16）
- **`ScanGames` は壊れたパックを飛ばす**: `config.json` が読めない（JSON 不正・オブジェクトでない）ゲームはログを出して除外し、残りで索引を差し替える。ゲーム内のアセットパックの `config.json` が壊れている場合はそのパックだけ除外する（旧実装は例外でスキャン全体が止まり、索引が古いまま残っていた）
- **`game.*` の書き手が core へ移動**（キー・順序・回数は旧実装と同一。`StateSchema.GAME_KEYS` に `abbreviation` を追記）。`src/core` 外の state 書き手は `AutonaTournamentDataProvider` の 1 つだけになった（台帳テスト更新）
- **`AutonaGameAssetManager` は Qt アダプタに縮退**（クラス名・シグナル 2 本・モデル 6 個・`GetStockIcon`・アイコンワーカー・スレッド 3 種・選択ゲームの永続化は維持）。旧属性（`games` / `selectedGame` / `characters` ほか）はスナップショットへの読み取り専用プロパティ。モデルはスナップショットの行（並び順はカタログが Qt の UTF-16 比較を再現して確定）から作り直し、`.sort(0)` と `appendRows` は使わない。旧世代は 6 モデル（`stageModelWithBlank` を追加）まとめて 1 世代退避
- **空 codename（大会 URL のクリア = `LoadGameAssets(0)`）は何もしない**（PdM 決定 2026-10-23。旧実装は CSS を消して選択だけ `{"codename": ""}` に半端に切り替え、`KeyError` をログしていた）
- **golden テスト**: 抽出前の実装の出力（モデルの DisplayRole + UserRole・WebServerActions のペイロード・state・CSS・Start.gg 解決）を `tests/fixtures/game_assets/golden.json` に記録し、`tests/test_game_asset_catalog.py`（Qt なし）と `tests/test_game_asset_manager_models.py`（実モデル + 未変更の S1b リーダー）で一致を pin
- **golden 記録で見つかった既存バグ 2 件を修正**:
  - スキン 10 個以上のキャラで、アセット索引のファイル名リストを番号で上書きしていた（スキン 8・9 が消え、スキン 8 がスキン 0 の画像を返す）。番号に欠番があると `IndexError` でロード全体が中断していた
  - アセットの無いスキンが 1 つあると、そのキャラの全スキンのアイコンが消えていた（PIL で SVG の `cancel.svg` を開いて例外）。スキン単位で cancel アイコンへフォールバックするようにした
- 残置（S1b / S1c）: `WebServerActions` / `AutonaApiV1` / `StartGGDataProvider` のリーダーは旧プロパティ経由のまま（`@gui_thread_sync` の除去は S1b）。`AutonaCountryHelper` は S1c

### S1b Web / Start.gg リーダーのカタログ直読み（2026-10-23・PR #219）

- **`WebServerActions.get_games` / `get_characters` / `get_variants` はカタログの `GamesPayload()` / `CharactersPayload()` / `VariantsPayload()` を直接返す**。`get_characters` / `get_variants` から `@gui_thread_sync` を除去（`get_games` は元から無し）。Flask / Socket.IO のスレッドで答え、GUI スレッドを待たない。§4.4 の S1 完了条件「3 リーダーから `@gui_thread_sync` が消える」を満たす。
  各ペイロードはカタログが 1 回だけ取った参照（スキャン結果 / ロード済みスナップショット）から組む。旧 `get_games` はキーごとに `games` プロパティを読み直しており、その間に再スキャンが入ると `KeyError` になり得た
- **出力はバイト等価**: 旧リーダーはモデルの UserRole（QVariantMap）から dict を取り出していたため、入れ子の dict のキーが QMap 順（UTF-16 コード単位順）で並んでいた。S1a のカタログペイロードは挿入順のままで、Socket.IO（`SocketioJson` = `json.dumps`・挿入順）のバイトが変わるところだった（HTTP は Flask の `jsonify` がキーをソートするので元から不変）。
  `CharactersPayload` / `VariantsPayload` は `_qvariantmap_copy` で QMap 順を再現する。変換されるのは str キーの dict（QVariantMap）と list（QVariantList）だけで、str 以外のキーを持つ dict と tuple は PySide6 が Python オブジェクトのまま保持し中身を並べ替えないので、そのままコピーする（tuple は list になるが JSON は同じ。いずれも PySide6 6.11 の実測と JSON 一致を確認済み）。S1a の golden テストは `_norm` 比較で入れ子のキー順を見ていなかったので、`json.dumps` のバイト一致を追加した
- **Start.gg ID 解決はカタログ経由**: `StartGGDataProvider` は Qt アダプタ（`AutonaGameAssetManager`）を import しなくなり、`GameAssetCatalog.instance.CharacterFromStartGGId` / `StageFromStartGGId` で解決する。両リーダーに `snapshot=` 引数を追加し、1 つの結果を組む間は `Current()` を 1 回だけ取って渡す（旧 API パース 1 回・新 API パース 1 回・`ProcessEntrantData` の 1 エントラント）。パースの途中でゲームのロードが入っても 2 ゲームが混ざらない（旧 `ProcessEntrantData` は `selectedGame` の codename と mains を別々に読んでいた）。
  start.gg のキャラ ID → 名前の表（`_startggCharacters`）はスナップショットの外にあるが、ゲームに依存しない表なので生きた表を読んでも実害はない。呼び出し元の無くなったアダプタの `GetCharacterFromStartGGId` / `GetStageFromStartGGId` は削除
- **`AutonaApiV1.game_set` と `WebServerActions.set_game` は codename でロードする**（`LoadGameByCodename`）。旧実装は `GameCodenames()` 上の位置を引いてから位置でロードし、ロード側が一覧を読み直すので、間に再スキャンが入ると別のゲームを読み込み得た。存在しない codename の応答（v1 は 404 `NOT_FOUND`、legacy は `"Could not find game X"`）は不変。`set_game` の `@gui_thread_sync` は同期のモデル再構築のために残す。
  v1 の実行中フラグ `game` は、アダプタの `LoadGameByCodename(..., done=)` が**ロード試行の終了時（成功・カタログがスキップ・例外のいずれでも 1 回）**に呼ぶコールバックで解放する。旧実装は onLoad で解放しており、onLoad はロードが実際に起きたときしか来ないため、スキップ（再スキャンで codename が消えた）や例外では TTL（120 秒）まで 409 を返し続けた。UI から始めたロードの onLoad が API のフラグを早く解放する経路も消えた（`_connect_completion_signals` は削除）
- **テスト**: `test_game_asset_manager_models.py`（golden とのバイト一致・3 リーダーに `@gui_thread_sync` が無くカタログを読むことの AST pin・GUI スレッドがイベントループを回していない状態でワーカースレッドから呼べること）/ `test_api_v1.py`（legacy と v1 の 3 ルートが golden を返す・再スキャンが割り込んでも `POST /api/v1/game` と `/update-game` が指定のゲームを読み込む・ロードがスキップ / 失敗してもフラグが解放され次の要求が 202 になる・未知 codename の応答）/ `test_game_asset_catalog.py`（ペイロードのバイト一致・QMap 順の再現・tuple と非 str キー dict は並べ替えない）/ `test_startgg_parse.py`（アセットマネージャのスタブを廃止し、fixture ゲームを読み込んだ実カタログで解決。パース途中でスナップショットが差し替わっても 1 スナップショットで解決することを pin。stageWins の未知ステージ ID）
- 残置: GUI 側の読み手（各パネル・`AutonaPlayerDB.SetupModel`・`AutonaTournamentInfoWidget`・サムネイル / リザルト生成）は旧プロパティ経由のまま（S4 で消える。サムネイル / リザルトは S3 でも `@gui_thread_sync` 残置の予定）。`AutonaCountryHelper` は S1c
- **既存バグを修正**: 旧 API の `stageWins` に、読み込み中のゲームが知らないステージ ID が含まれるとき、`StageFromStartGGId(...)[1]` が `None` を添字して `TypeError` になり、そのセットの旧 API 部分（bestOf・stage_strike・losers・currPlayer）が丸ごと `{}` になっていた（キャラ ID の LIVE-BUG-3 と同型）。未知のステージは飛ばして warning を出す（飛ばすのは同じ関数の `strikeList` / `banList` と、warning はキャラの `mainFor` と同じ扱い）

### S1c 国・地域カタログの core 化（2026-10-23・PR #220）

- **`src/core/CountryCatalog.py` を新設**（Qt 非依存・import 副作用ゼロ。`test_core_qt_free` が自動で対象に含める）。国表（表示ロケールの表示名・書き出しロケールの名前・英語名・座標）・地域（states）・カスタム国旗（`./user_data/additional_flag`）・国コンボの行を、不変の `CountrySnapshot` として持つ。S1a と同じ型で、`Load()` が唯一の書き手（`threading.Lock` 1 本）、別スナップショットを組んで参照を 1 回で差し替える。リーダーはロックを取らず `_current` を 1 回読む。都市→地域表（`assets/city_to_state.json`）は従来どおり初回 `FindState` で遅延ロード（専用ロック）。observer もコールバックも持たない（通知はアダプタの `countriesUpdated`）。state は書かない。
  生成は `bootstrap_core()`（アダプタ `AutonaCountryHelper` の直前。ORDER CONSTRAINT を追記）。ロードは従来どおり `Window.__init__` の `AutonaCountryHelper.instance.init()` で、`LoadLocale()` の後（国名がロケール依存なので、ロケールは `GameAssetCatalog` と同じく callable で注入し、ロード時に読む）
- **リーダー API**（S1d で `PlayerService` が使う前提で用意）: `BasicCountryInfo(iso2)`（選手の `country` が持つ形）/ `States(iso2)` / `State(iso2, stateCode)` / `CountryByCode(code)`（標準国 → カスタム国旗の順に `code` で引く。選手ウィジェットが国モデルを走査して得る行と同じ答え）/ `CountryCodeFromEnName(en_name)` / `FindState(country, location)` / `IsLoaded()`。**データを返すリーダーはすべてコピー**を返す（呼び出し側は state・Start.gg のペイロード・Qt モデルにそのまま入れるため）
- **`AutonaCountryHelper` は Qt アダプタに縮退**: 残るのは国モデル `countryModel`・`countriesAboutToReload` / `countriesUpdated`・`LoadCountries()`（カタログをロードしてモデルを埋める）・`init()` だけ。データ API（`countries` / `GetBasicCountryInfo` / `GetStates` / `FindState` / `_LoadCityToState`）は削除し、呼び出し元（`AutonaBasePlayerWidget.LoadStates`・`AutonaScoreboardPlayerWidget.SetData`・`AutonaTop8Widget`・`StartGGDataProvider`・`WebServerActions`・`AutonaApiV1`）をカタログ直読みに追従させた（S1b と同じく、互換プロパティは置かない）。`countries_json`（パース済み JSON 全体の常駐）と `cities`（slim データでは常に空・読み手なし）は死にデータだったので持ち込まない
- **国モデルは生涯 1 個**: 初回の成功ロードで作り、再ロードは同じモデルを `clear()` + `appendRow` で入れ替える（#13b に従い行単位）。差し替えないので、agent-pitfalls #15 にあった「retire 世代なしの差し替え（#13 規約の唯一の例外）」は消えた。本番のロードは `Window.__init__` の 1 回だけで、選手カードのフォールバック（`LoadCountries` がモデル `None` を見たときにヘルパーを呼ぶ）も**生成**であって再ロードではないので、**再ロードは現状テストでしか起きない**。それでも再ロードの契約は決めた: ヘルパーは `clear()` の直前に `countriesAboutToReload` を出し、`countriesUpdated` は refill 後（途中で例外が出ても finally で）出す。国モデルの消費者は 2 つで、どちらも**再ロードで state を書かず、表示は state と一致したまま**にする — Top 8 は `_updating` を上げて入力→state 経路を止め、`_rebindCountryModels` で各スロットの国を `top8.players` から選び直してから下ろす（旧実装は blockSignals 下で全スロットを index 0 に戻していた。表示が state とずれる）／選手カードは国コンボのシグナルを止め、直前に表示していた国コードの行を選び直してから戻す（地域コンボはその国の行のままなので触らない。国が再ロード後のデータから消えていた場合だけ、空行への通常の変更として流して state を表示に揃える）。旧実装の選手カードは再ロードで国・地域とも `{}` を publish していた。`countriesUpdated` の発火はカスタム国旗まで入れた後に移した（旧: 標準国だけの時点で発火し、その後にカスタム国旗を追記していた。受け手は同じモデルを付け直すだけなので見え方は同じ）
- **選手カードの国まわりの結線を 1 回に**: 旧 `AutonaBasePlayerWidget.LoadCountries` は結線一式（`QCompleter` 生成・`country.currentIndexChanged → LoadStates`・地域コンボの設定）を行い、かつ `countriesUpdated` のスロットでもあったので、emit のたびに `LoadStates` の接続とコンプリータが 1 つずつ増えていた。結線は `_WireCountryState()`（`__init__` から 1 回）に分け、`LoadCountries` はモデルの付け替えだけ（同じモデルなら何もしない＝冪等。`UniqueConnection` は使わない）。コンプリータは #15 に従い `QCompleter()` + `setModel()`
- **共有 dict の破壊的変更をやめた**: 旧 `LoadStates` は、表示した国の地域 dict（`AutonaCountryHelper.countries` の中身）に地域旗のパス `asset` を書き込んでいた。旧 Start.gg の `ProcessFutureSet`（配信キュー / ステーションの次セット）も同じ dict に `asset` を書き込み、**その共有 dict 自体**を選手データに埋め込んで返していた（以後の書き込みが返した結果に波及し得た）。カタログは表に書き込まず、`States()` / `State()` が呼ばれた時点で `asset` を解決した新しい dict を返す（キャッシュしない。旧 `LoadStates` と同じく毎回 `os.path.exists`。JP 47 件で 1ms 未満）
- **`WebServerActions.get_states` から `@gui_thread_sync` を除去**し、`CountryCatalog.instance.States()` を直接返す（Flask / Socket.IO のスレッドで答える）。`AutonaApiV1` の未ロード判定（409 `NOT_READY`）も `IsLoaded()` に置き換え（意味は同じ「国が 1 つも無い」）。
  **出力について（設計判断）**: 旧出力は履歴依存だった — 上記の書き込みのせいで、デスクトップがその国を一度でも表示していれば各地域の末尾に `asset` が付き、表示していなければ付かない。新出力は**常に `asset` 付き**に固定し、旧の「表示済み」側と 250 か国すべてでバイト一致する（golden）。未表示の国に対しては末尾の `asset` キーが増える（これが唯一の差分で、`asset` を除くと旧の「未表示」側とバイト一致することも pin）。こちらに揃えた理由: Web が地域を引くのは選手の国 = ほぼ必ずデスクトップが表示済みの国で、旧出力の実運用側と一致する／Web UI（`CountryStateSelector`）は `asset` で地域旗を描き、無いときは `/assets/state_flag/{国}/{code}.png` に推測フォールバックしていた（`CON` = `_CON.png` を引けない）／「`asset` 無し」に揃えると表示済みの国でキーが消える後退になる。契約上はキーの追加のみ。
  旧経路は Qt モデルを経由せず生の dict を参照で返していたので、S1b のような QVariantMap 順の再現は不要（キー順は JSON の挿入順のまま）
- **golden テスト**: 移行前の実装の出力（国表・各国の行データ・国モデルの行・`get_states` の「未表示」「表示済み」両方・地域コンボの行。3 つのロケール組とカスタム国旗込み）を `tests/fixtures/countries/golden.json` に記録（大きい表は国ごとの `json.dumps` の sha256 先頭 16 桁）。`tests/test_country_catalog.py`（Qt なし: golden 一致・リーダーが表を書き換えない・ロード失敗で前のスナップショットを保つ・壊れた国エントリの扱い・別スレッドの読み手が半端な表を見ない）と `tests/test_country_helper_model.py`（国モデル行・再ロードで同じモデル・実 `LoadStates` が全 250 か国で golden 一致かつ表を書き換えない・実選手カードで国を表示しても `get_states` が変わらない・`get_states` の golden / `@gui_thread_sync` が無い AST pin / GUI スレッドがイベントループを回さない状態でのワーカースレッド応答 / 再ロードで Top 8・選手カードとも publish しない（Top 8 はパネルの実メソッドを借りた代役。パネル本体は QWebEngineView を持つのでハーネス外）/ `countriesUpdated` を何度出しても接続が増えない / モデル未生成時のフォールバック 2 経路（その間ライブボードの国・地域を publish しない）/ 再ロード通知が 2 回来ても選手カードのシグナルが止まったままにならない / Top 8 の `__init__` が代役と同じ 2 本を connect する AST pin）で pin。Start.gg の次セット（`ProcessFutureSet`）の国・地域は `test_startgg_parse.py` が pin（`asset` 解決・未知の生地域コード → `{}`・表の dict を返さない）。記録スクリプトは `tests/fixtures/countries/record_golden.py`（**S1c 前の実装に対して**走らせるもの。使い方は docstring）。国モデルの区切り行の文言はテスト環境の翻訳器に依存するので、その 1 行だけ実行時の翻訳と比較する
- **テストの追従**: `test_find_state.py`（カタログの `FindState` へ）/ `test_startgg_parse.py`（国のモックを廃止し、同梱データを読んだ実カタログで解決）/ `test_import_side_effects.py`（import 不活性 + 生成は何も読まず作らない・`Load()` が国データを読み `./user_data/additional_flag` を作る positive control）/ `test_country_helper_flags.py`・`test_player_widgets.py`（参照先の変更のみ）
- 残課題（国モデルの再ロード。本番では未発生）: 再ロード後のデータに国が見つからない場合の扱いが、選手カード（空行を publish して state を表示に揃える）と Top 8（`_selectCountry` に合わせて publish しない）で異なる
- 残課題（同上）: 見つかった国の `name` / `display_name` をロケール再ロードで state に publish し直すか（現状は publish しないので state には旧ロケールの名前が残る）は、ロケール再ロードを実装するときに仕様化する
- 残置: GUI 側の国モデル走査（`SetData` の `country_code` → 行番号、Top 8 の国コンボ）は Qt モデル経由のまま。S1d で state に入れる値の組み立て（`CountryByCode` / `State`）は `PlayerService` に移ったが、provider データ → 行の解決（国モデル走査・`FindState`）は選手カードに残っている（S2 で state 起点の描画に移す。S1d 節参照）。known-smells S-16（`LoadCountries` が `Window.__init__` で GUI 同期）は、データ半分が `CountryCatalog.Load()` に分かれたので Worker 化の素地はできたが未着手
- §4.4 の S1 完了条件の進捗: カタログが `test_core_qt_free` に載る（`GameAssetCatalog` + `CountryCatalog`）✅ / Web のカタログ系リーダーから `@gui_thread_sync` が消える（S1b の 3 本 + `get_states`）✅ / `PlayerService` がキャラ/スキン/国を自分で解決する → S1d

### S1d 選手カードのキャラ・国の値を PlayerService が組む（2026-10-23・PR #221）

- **選手カード（SCORE の選手スロット / PLAYERS エディタ `player_editor.1`）は識別子だけを渡す**。旧実装はコンボの選択行の UserRole（Qt モデルが持つ dict 全体）を読み出して `PlayerService.PublishCharacters` / `SetLocation` に渡していた。今は渡すのが「キャラの `en_name`・スキンの行番号・バリアントの `en_name`・フォールバック名」（`CharacterSlot`）と「国コード」「地域コード」だけで、state に入れる dict は `PlayerService` が `GameAssetCatalog` / `CountryCatalog` から組む。§4.4 の S1 完了条件「`PlayerService` がキャラ/スキン/国を自分で解決できる」を満たす（Qt なしで同じ値を作れることを golden で pin）
- **API**: `PlayerService`
  - `CharacterSlot`（frozen dataclass: `character` / `skin` / `variant` / `text`）
  - `PublishCharacterSlots(slots, includeMains=False)` / `CharacterValue(slot, snapshot=None)`
  - `SetCountry(code)` / `SetRegion(countryCode, regionCode)` / `CountryValue(code)` / `RegionValue(countryCode, regionCode)`

  `SetLocation` と `PublishCharacters` は削除した（呼び出し元は選手カードだけで、dict を受け取る入口を残すと Qt 側で値を組む経路が復活するため）。カタログはコンストラクタで差し替え可能（`games=` / `countries=`。既定は呼び出し時の `.instance`。テストが Qt なしで別カタログを渡すため）。`GameAssetCatalog` に行リーダー `CharacterRow(en_name)` / `SkinRow(en_name, row)` / `VariantRow(en_name)` を追加した（コンボの行データのコピーを返す。`snapshot=` 付き）
- **設計判断**:
  - **キャラとバリアントの識別子は `en_name`**（codename ではない）。キャラ表・スキン表のキーなので一意で、選手 DB の `mains` も Start.gg の解決結果も `en_name` で持っている
  - **スキンは「スキンコンボの行番号」**。state の `skin` は昔から行番号で（スキン番号ではない。番号に欠番があると両者はずれる）、`mains` も行番号を保存しているので、それをそのまま識別子にした
  - **バイト互換は PlayerService が持つ**。旧値は Qt モデル経由（QVariantMap）なので、入れ子の dict のキーがすべて UTF-16 コード単位順に並んでいた。`program_state.json`（orjson）も Socket.IO の delta（`json.dumps`）も挿入順で書き出すので、キー順は外部契約の一部。S1b の `_qvariantmap_copy` を `core/DictHelper.qvariantmap_copy` に移して共用し（利用者が 2 つになったため）、カタログのリーダーは挿入順の素のコピーを返す。`assets` / `skin` / `variant` の 3 キーは旧実装と同じく末尾に追加順で付く
  - **1 回の publish は 1 スナップショット**: `PublishCharacterSlots` は `Current()` を 1 回だけ取り、全スロットをそれで解決する（ゲームのロードが割り込んでもスロット 1 とスロット 3 が別ゲームにならない）
  - **カタログが知らない識別子**: キャラは `{"name": 名前}`（旧実装の「行が選ばれていない」と同じ形）。名前は、何も選ばれていなければ手入力テキスト、行が選ばれているのにカタログに無ければ**その行自身の `name`**（`CharacterSlot.text`。カードの `_CharacterSlot` が詰める）。後者はゲームのロード直後〜コンボの付け替えまでの一時値で、付け替え後の再 publish で収束する。当初はここにもコンボの `currentText()` を使っており、一時値の `name` に「表示名 / en_name」形式のラベルが出得た（review 指摘）。旧実装は旧モデルの行 dict 全体を出していたので、名前だけはそれと揃えた（`assets` などは空のまま）。国・地域は `{}`（下記の修正を参照）
  - **地域は `_loadedStatesCountryCode`（地域コンボの行を作った国）で解決する**。国コンボが今表示している国ではない。`SetData` の `Clear()` はシグナルを止めて国コンボを空にし、地域コンボの行には触らないので、その間は 2 つがずれるため
  - **どの行を表示するかは引き続きカード（Qt）が決める**。`SetData` の `country_code` → 国コンボの行、`mains` → キャラコンボの行、手入力テキストは従来どおり。カードを state から描画するのは S2 の範囲
  - **publish 回数・保存サイクル・SaveBlock は不変**。既存のキャラ選択時の 2 回 publish（ボード上で held 2 サイクル）も既存挙動として残した
- **golden テスト**: 移行前の実装（2f831e58）の出力を `tests/fixtures/player_slots/golden.json` に記録した。記録・再生は `tests/test_player_slot_publish.py`（記録手順は docstring。`AUTONA_RECORD_PLAYER_SLOTS=1`）。記録したもの:
  - fixture ゲーム 3 シナリオ（tgame / tgame + mods / game2）での、キャラ行 × スキン行（未選択含む）× バリアント行の全組み合わせと手入力テキスト
  - 国コンボの全行（標準国 + 区切り行 + カスタム国旗 3 種 + 空行）
  - 標準国すべての地域全行
  - ボードとエディタでの操作列（スロット数変更・mains 付き / 旧形式 mains / 自由記述の地域 / 国なし / カスタム国旗の `SetData`・各コンボの選択・スロット入れ替え・クリア）。`StateManager.Set` の呼び出しを順序どおり全部と、保存サイクル数を残している

  値は `program_state.json` と同じ `orjson.dumps(OPT_NON_STR_KEYS)` の文字列で、大きい表は sha256 先頭 16 桁。カード経由の再生（`test_player_slot_publish.py`）と、Qt なしで `PlayerService` だけを使った再生（`test_player_service.py`。golden の識別子から同じバイト列ができること）の両方で一致を確認している
- **golden 記録で見つかった既存バグを 2 件修正**した。golden との差分はこの 2 件だけで、テストの `_expectedLocations` / `_expectedOperations` に明示している:
  - **国なしの選手を `SetData` すると、前の選手の国が state に残っていた**。`Clear()` が国コンボをシグナルを止めて空にし、`country_code` 側の選択でそれを通知する前提だったため、`country_code` が無い（または未知の）場合は誰も通知しなかった。新しい選手の名前の下に前の選手の国旗が出て、カードには国が表示されない状態になっていた。さらに `state_code` が来ると、前の国の地域から探していた。黙って空にした場合だけ、`SetData` の同じ保存サイクル内で空行の index 変更を 1 回流すようにした（地域 `{}` → 国 `{}`）
  - **行データの無い行を選ぶと `null` を publish していた**: 国コンボの区切り行、国名の手入力（編集可能コンボで Enter → データ無しの行が挿入・選択される）、**地域名の手入力**（地域コンボも編集可能で同じ）。今は `{}`。型を既存の「国なし」「地域なし」の `{}`（空行・再構築直後に昔から publish している値）に揃えたもので、同じ意味の値が 2 つの型を持たなくなった。golden に入っているのは区切り行だけで、地域の手入力は golden に無いので専用テスト（`test_a_typed_region_publishes_no_region`）で pin した
- **テスト**:
  - `test_player_service.py`（Qt なし）: 識別子 → 値の規則（キー順・別キャラのスキンでの名前上書き・行なし / 未知のキャラ・1 スナップショット・未知のコード → `{}`・空値を飛ばさない）、ボード / エディタ両パス、`includeMains` が 1 保存サイクル、golden の Qt なし再生
  - `test_player_slot_publish.py`（実カード）: golden 再生・区切り行・地域の手入力
  - `test_player_widgets.py`: 国なし `SetData` の修正・カードが渡す識別子とフォールバック名（`_CharacterSlot`）
  - 両再生が使う世界（シナリオ表・カスタム国旗・ロケール）と符号化（`encode` / `digest`）は `tests/fixtures/player_slots/recording.py` に 1 本化（Qt なし）
  - `test_country_helper_model.py`: 国モデル再ロードで publish しないことの検査を `SetCountry` / `SetRegion` に追従
  - `test_game_asset_catalog.py`: `qvariantmap_copy` の import 先
- 残置（S1d の範囲外。どれも `PlayerService` の経路ではない）: Top 8（`AutonaTop8Widget` → `Top8Service`）・実況（Commentary）・リザルトの各パネルは、キャラ / 国の dict を今も自前の Qt コンボから組んでいる。S4 でパネルごと置き換わるときに、同じカタログ + `qvariantmap_copy` で組む形に寄せる。Start.gg のセット読込（`SetLoadService`）・Web / v1 の選手更新・選手 DB の補完・選手の入れ替えは、どれもカードの `SetData` → コンボ → 新 API を通るので、追加の変更は要らなかった
- 既知の端ケース（変更なし・記録のみ）:
  - カスタム国旗で拡張子だけが違う同名ファイル（`abc.png` / `abc.jpg`）は同じコードになるため、2 つ目の行を手で選んでも 1 つ目の旗が publish される（`CountryByCode` は先勝ち。旧実装では行自身のデータが出ていた。`SetData` の `country_code` 経由では旧実装も先勝ちだった）
  - 国コンボは編集可能（既定の InsertPolicy）なので、手入力の国名で Enter すると共有の国モデルに行が追加される（地域コンボで同じ挿入が起きることはテストで確認。国コンボはコード上の推定・実機未確認。publish は上記のとおり `{}`）
  - `GameAssetCatalog._CharacterAssets`: そのキャラのファイルが無いパックに `image_sizes` / `rescaling_factor` の定義があると、`charFiles[assetKey]` の `KeyError` でそのパックの残り（`unflippable` 等）が飛ばされ、ログが出る。直すとアセットの payload が変わるので触っていない
- 残課題（review で見送り・記録のみ）:
  - 地域の解決は、`LoadStates` が最後に記録する `_loadedStatesCountryCode` の更新タイミングに依存している（行の再構築中に出る空行の publish はまだ旧コードを見るが、空行なので国を要さない）。行の作成と国コードの記録が 1 か所にまとまっておらず、`LoadStates` の順序を変えるとずれ得る。S2（カードを state から描画）で解消する
  - `GameAssetCatalog` はどのインスタンスも共有 StateManager の `game.*` に書く（`ResetGameState` / `SelectGame`）。テストで 2 つ目のカタログを作ると本番相当の state に書き込む（現状は各テストの state スナップショット復元で隔離している）
  - `PlayerService` は `games=` / `countries=` が未指定だと、呼び出し時の `GameAssetCatalog.instance` / `CountryCatalog.instance` を暗黙に使う（`bootstrap_core()` 前や `instance` が `None` のままだと publish 時に `AttributeError`）。生成時の明示注入にするかは S2 以降で判断する
- §4.4 の **S1 完了条件はすべて満たした**:
  - カタログが `test_core_qt_free` に載る（`GameAssetCatalog` + `CountryCatalog`）✅
  - Web のカタログ系リーダーから `@gui_thread_sync` が消える（S1b の 3 本 + `get_states`）✅
  - `PlayerService` がキャラ/スキン/国を自分で解決する（S1d）✅
    - 注: 解決したのは「識別子 → state の値」まで。provider データ（`country_code` / `state_code` / `mains`）→ 識別子の変換（国モデル走査・`FindState`・`mains` → キャラ行）はカード側に残っており、S2 で state 起点の描画に移す

### S2 選手スロットの正本を state へ — 範囲と設計判断（2026-10-23）

設計案（棚卸し・目標形・PR 分割）を採用し、以下を決めた（オーナーから ADR-005 範囲内の設計判断を委任された判断）。

1. **範囲は S2a〜S2e**（S2a 描画 / S2b losers → mergedName を core へ / S2c swap を core へ / S2d clear 系を core へ / S2e best_of を state 主導へ）。**S2f（`SetData` の core 化）と S2g（人数・キャラ数を state 主導へ）は S2 から外し「S2 後の残り」とする**。§4.4 の完了条件「`WebServerActions` の `GetScoreboard(` が board 1/2 でゼロ」は「**swap・clear・best_of・set_route の該当箇所でゼロ**」に絞る。
   理由: 文字どおりの条件は、42 箇所のうち stats・ウィンドウ操作・サムネイル・provider 取得まで含み、S2 の内容（swap / clear を core へ、カードを state から描画）を大きく超える。S2f は悪語フィルタ（`AutonaLocaleHelper` 経由で Qt を import）と `AutonaPlayerDB`（Qt を import）の Qt 非依存部分の抽出が先に要る。S2g は「人数を state のキーで持つか」（今はスロット数で暗黙）というスキーマ判断を含み、カードの生成・破棄を state 起点にするため Qt ライフサイクルのリスクが最も大きい
   - S2 後も board 1/2 で残る `GetScoreboard(`: 選手データ（`post_score` / `set_team_data` / `update_player` / `load_player_from_tag`）→ S2f、人数・キャラ数（`set_route` の `players` / `characters`、`clear_all` の 2 行）→ S2g、stats（`stats_*` / `refresh_stats` の 11 箇所）、ウィンドウ操作（`open_sets` / `pull_stream_set`）、サムネイル（`get_thumbnail`。S3 でも残す）、provider 取得（`load_set`）。board 3（クルー）は条件の対象外
2. **ホットキー `load_set` / `refresh_phase_group` はシェル / パネルのコマンドに分類し直す**（Actions へ移さない）。前者はセット選択ウィンドウを開く操作、後者は Top 8 の provider 取得で、state 操作ではない。Actions へ移すのは `swap_teams` だけ（S2c）
3. **Web の `clear_all` とデスクトップの `ResetAllInputs` の挙動差は揃えない**（外部から見える挙動を変えない）。`ClearBoard` にチーム名を消すかどうかのオプションを持たせ、各入口は現在の挙動を維持する
4. **追跡中セット（`lastSetSelected`）は `SetLoadService` に置く**（state の外）。state の `set_id` を正本にすると、クリアで `set_id` が null になる外部差分が出るため
5. **既存バグの扱い**: 明らかなバグ（mains が選手と一緒に動かない / seed バッジが古いまま / tracker ON でステージ順が崩れる / swap で地域が落ちる＝golden で確認できた場合）は直してテストで pin し、golden 差分としてここに明記する。「Clear で city が消えない」は直さず記録のみ。バリアントのコンボ名衝突は内容次第（直すなら同様に pin）
6. **クルー（board 3）の `teamsSwapped` は対象外**（属性を残し、`get_swap(3)` は属性を読み続ける）
7. ~~**未構築ボードの `HoldSubtree` 登録を `bootstrap`（`SeedBoard` 直後）へ移すのは S2c で可**~~ → **撤回（S2c の review、2026-10-23）**。理由: Web UI には反映ボタンが無く（`web/src` に `:apply` の呼び出しが無い）、デスクトップの反映は表示中のボードだけに効く。起動時から held にすると、形式と食い違う面への書き込み（例: 1v1 表示中に Web の `/team` ページから board 2 へ `score:set` / reset / swap / カラー）が OBS にも Web にも出ないまま、そのボードを構築したときの `SeedBoard` で消える。さらに held が残り続け、保存サイクルごとに state 全量の複製が走る。未構築ボードへの core 直結の書き込みは従来どおり即時公開し、ボードを構築してから操作していた Web の swap / `get_swap` は構築を維持する（下記 S2c）
   - 当初の理由（今は登録がボードの `__init__` にしかなく、Web 操作が core 直行になると、未構築ボードへの操作が「構築して hold」から「hold なしで即 publish」に変わる）は、swap の Web 経路が構築を続けることで解消した
8. **swap 後のスロット内キー順の変化は golden で確認し、変わるなら挿入順を保つ実装を優先する**（外部契約の安全側）。swap の delta が 1 回になるのは意図した改善として扱う。
   実測（S2a の golden）: 現行の `SwapTeams` は既に 1 SaveBlock = held 1 サイクルで、反映時は delta が 100 行を超えるため `state_big_change` として送られている。設計案で書いた「複数サイクル → 1 回」は swap については誤りで、複数サイクルになっているのは losers（3）・選手のクリア（カード数）・`SetData`（カード数）・キャラ選択（2）などウィジェット主導の操作のほう

#### S2a 選手カードを state から描画する（2026-10-23・PR #222）

書き手は変えず、カードが自分のスロットを state から描画できるようにした（S2c / S2d で core が書いたスロットをカードが表示するための前提）。

- **`AutonaScoreboardPlayerWidget.RenderFromState()`** を新設した。描画対象は `StateSchema.PLAYER_INPUT_KEYS` に `character` を加えたもの（`RENDERED_KEYS`）。派生値（`mergedName` / `mergedOnlyName`・avatar・スポンサーロゴ・`mains`）と、カードに入力欄の無い値（`id` / `city` / `online_avatar`）は描画しない
  - **state を書かない**: 変える widget はすべて `QSignalBlocker` 下で変える（各 widget は今も `PlayerService` の書き込みに繋がっているため）
  - **比較は「そのカードが今 publish するなら出す値」で行う**: キャラは `PlayerService.CharacterValue(_CharacterSlot(...))` と state の値の一致。国・地域はコード（どちらの値も `CountryValue` / `RegionValue` がコードから組んだものなので、コードが一致すれば値も一致する）
  - **キャラの行は値から逆引きする**: 別キャラのスキン（Dr. Mario）では、スロットの `name` / `en_name` が選んだ行のものではなくなる。そこで `codename`（無ければ `en_name`）が一致する行を候補にし、`CharacterValue` が state の値と一致する候補を選ぶ。行の無い値（手入力・空行）は編集テキストとして出す
  - **地域の行は state の国コードで作る**。`LoadStates` から行の再構築部分を `_FillStateRows` に分けた。選択時の経路（`LoadStates`）は従来どおり再構築 → 空の地域を publish → `_loadedStatesCountryCode` を記録する。描画は publish せずに再構築して国コードを記録する
  - **フォーカス中の欄は飛ばす**（`_SyncSetFields` と同じ規則。コンボはドロップダウンが開いている間も編集中とみなす）。飛ばしたときは「描画済み」として記録しない。**再試行のきっかけは 2 つ**: (1) そのボードの次の held サイクル（どのカードやスコアへの書き込みでもよい）、(2) 飛ばした欄のすべてからフォーカスが外れたとき（`QApplication.focusChanged` → カードの `render_requested` → ボードが描画を予約する。描画自体は遅延・合体なので、フォーカス変更の中で widget を変えない。#9）
  - **フォーカスアウトで古い値を書き戻さない**（`_OnTextChanged`）。Qt 6（PySide6 6.11 で実測）は、前回の emit 以降にテキストが変わっていればフォーカスアウトで `editingFinished` を出す。描画のシグナル停止下の `setText` も「変わった」に数える。そこで line edit の確定時に次の 2 つは書かない
    - state が既に同じ値を持っている（描画済みの欄を通過しただけ、Enter を押しただけ）。比較はカード側で行い、`PlayerService` は今までどおり無条件に書く。キーが無いことと `""` は区別する（初めての確定で `""` のキーを作る既存挙動を変えないため。golden で確認済み）
    - 描画がフォーカス中で飛ばしたときに欄に出ていたテキスト（`_heldBackText`）と同じテキストで確定した。確定の仕方は問わない（フォーカスアウト・Enter・打って消して元に戻した場合も同じ）。state はその下で先に進んでいるので、書くと新しい値を古い値で上書きしてしまう。フォーカスアウト後の再描画が新しい値を出す。違うテキストならオペレーターの編集として書く（後勝ち）。判定を `isModified()`（打鍵したか）にしなかったのは、打って消して元に戻すと打鍵ありになり、古い値を書いてしまうため
    - Enter で確定した場合、欄にはフォーカスが残るので、新しい値が表示されるのはフォーカスが外れてから
    - メモ欄（`QPlainTextEdit`。打鍵ごとに publish する）も、state と同じ値なら書かない
  - **再現できない値では何も変えない**。行の決定を先に済ませ、決まらなければ widget に一切触らずに `False` を返す（次の描画で再試行）。国・地域では、国の行・地域の行（作り直すなら `CountryCatalog.State` で存在を確認）を先に決める。キャラでは、キャラの行・スキン行（スキンコンボが持つことになる行数の範囲内か）・バリアントの行を先に決め、その行から得られる識別子で `CharacterValue` を組んで state の値と一致するかを確かめる（`_CharacterPlan`）。以前は国を変え、地域の行を作り直した後で地域が見つからずに止まる、といった途中までの変更が残り得た
  - **キャラのスロットが state に無ければ空のスロットを出す**（スロットごと消えた選手、`character` が欠けた／短い場合）。空の行が publish する値（`{"name": "", "assets": {}, "skin": -1, "variant": {}}`）と同じ表示にする
  - **前回最後まで描画したスロットと同じなら何もしない**。カードが動くのは編集によってだけで、編集は必ず publish されるため。毎サイクル全カードのキャラ値を組み直さないための措置
  - **描画で名前欄を変えたら `lastExportedName` を更新する**。`NameChanged` はこれと比べて「別の選手になった」と判断し、その場合は画像を作り直し `id` を落とす。更新しないと、core が入れ替えたスロットの名前欄で、何も変えずにフォーカスを外しただけで `id` が消える
  - **seed のバッジは state の数値から出す**。スピンボックスは値をクランプするし、バッジを動かす `valueChanged` は描画中は止まっているため。スピンボックスの値も合わせる
  - 対象はボードのカード（`score.{n}`）だけで、PLAYERS エディタ（`player_editor.1`）は描画しない（`RenderFromState` 自身が `BoardNumber()` を見て何もしない）。キャラのスロット数は今も `charNumber` が決める（S2g）
- **`AutonaScoreboardWidget` は held の保存サイクルごとに描画を予約する**（`_SchedulePlayerRender`。`_onHeldStateUpdated` の先頭で呼ぶので、後に続く同期処理が例外を出しても予約は残る）。描画はイベントループに戻ってから 1 回にまとめて実行し（`QTimer.singleShot(0, self, ...)`。ボードが破棄されれば予約も消える）、描画そのものには再入ガードを付けた。描画するのは実行時点の `playerWidgets` なので、予約の後に人数が減って外されたカードは描画しない
  - **遅延が要件である理由**: 今の書き手はカードで、1 操作が複数サイクルにまたがる（国の選択は地域を先に publish し国が後。`SetData` / `Clear` は欄ごと）。その途中ではカードが state より先行しているのが正常で、そこで描画すると国コンボを旧国に戻し、続く国のハンドラがそれを publish してしまう。操作が戻った後なら両者は一致しているので、描画で変わるのは別の書き手が書いたものだけになる
- **golden テスト**: 移行前（bd0fa50b）の実装で、ボード（board 2・2 人 × 2 キャラ）への操作 14 ステップを `tests/fixtures/board_operations/golden.json` に記録した。ステップは、人数・キャラ数変更 / 両チームの `SetData`（Dr. Mario + バリアント・地域・seed・city・avatar・メモ）/ チーム名 / スコア / losers / swap / 選手の上下移動 / swap 戻し / キャラ選択 / `ResetMatch` / `CommandClearAll` / セット読込（`ChangeSetData`。人数が縮む）/ `ResetAllInputs`。記録・再生は `tests/test_player_card_render.py`（記録手順は docstring。`AUTONA_RECORD_BOARD_OPERATIONS=1`）
  - 各ステップで記録したもの: `score.2` の全体（キー順込みの digest と、`assets` を digest にした読める形）、held サイクル数、反映で送られたもの（`state_updated` の回数・`state_big_change` の回数・delta の digest）
  - golden との差分はゼロ（挙動不変）
- **不変条件の pin**（`tests/test_player_card_render.py`）
  - 14 ステップのそれぞれの後で、全カードの表示が state と一致すること。判定は描画コードを使わず、widget の値から直接求める
  - 描画が書き込みゼロ・保存サイクルゼロであること。全カードを state からずらした状態から描画し、`Set` / `Unset` / `SetIfChanged` の呼び出しとサイクルを数える。ずらし方は、テキスト・メモ・seed・birthday、地域だけ（国は同じ）または国、1 つ目のキャラ欄を手入力名、2 つ目のキャラ欄を別の行
  - 1 操作で held 1 サイクルであること。対象は swap・上下移動・swap 戻し・`ResetMatch`・セット読込・`ResetAllInputs`。これ以外のステップはウィジェット主導で欄ごとに publish しており、S2c / S2d で 1 サイクルにする
  - state 起点の書き込みがカードに届くこと（S2c の前提）。`PlayerService` で 1 ブロックに書いたスロット（Dr. Mario + EX・手入力キャラ・地域・seed）と、state 上で入れ替えた 2 スロットで確認した。どちらも書き込み 1 サイクル・描画 0 サイクルで、入れ替え後に名前欄で `editingFinished` を出しても何も書かない
  - フォーカス中の欄は描画しないこと。1 ターンに何サイクル来ても描画は 1 回であること
  - フォーカスまわり（オフスクリーンで実際にフォーカスを動かす。Qt がフォーカスアウトで `editingFinished` を出したことも各テストで確認する）: 描画済みの欄をフォーカスイン / アウトしても書き込み・サイクルともゼロ。**シナリオ A**（欄にフォーカスがある間に別経路で値が書かれ、打鍵せずに離れる）では、古い値は書かれず、フォーカスアウト後に新しい値が表示される。打って消して元のテキストに戻してから離れる / Enter を押す場合も書かない。違うテキストを打った場合はオペレーターの値が書かれる
  - 再現できない値（その国に無い地域・未知の国・未知のバリアント・キャラに無いスキン）では、コンボの選択・行数・地域の行の国・スキンコンボのモデルがどれも変わらず、`False` を返すこと
  - スロットごと消えた（`Unset` / `{}`）選手のカードが空で表示されること。PLAYERS エディタを描画しないこと。予約後に人数が減ったとき、外したカードを描画しないこと。`_SyncSetFields` が例外を出しても描画されること
  - 描画を無効化すると state 起点の 3 テストが落ちることを確認した（14 ステップの不変条件は、今の書き手のままなら描画なしでも成立する。S2a の描画は今の運用では何もしないのが正しい）。追加分も、それぞれ修正を外すと落ちることを確認した（確定時の比較なし / `isModified()` による判定 / フォーカスアウトの再描画なし / エディタの除外なし / 先に決めない旧描画 / 予約を末尾に戻す）
  - `tests/test_signal_lifetime.py` の `PROCESS_WIDE_SENDERS` にアプリケーションオブジェクト（`QApplication.instance()` ほか）を加えた。カードが繋ぐ `focusChanged` に lambda を繋ぐと検出される
- **テストの追従**: `test_country_helper_model.py` の代役（`_RegionCombo`）が `_FillStateRows` も借りるようにした
- **golden で見えた既存挙動**（S2c 以降の入力。S2a では触っていない）
  - swap と選手の上下移動で、**バリアントが失われる**（`EX` / `Alt` → なし）。`SwapWith` が、全スロットで共有している objectName `variants` を 1 キーで退避し、`findChild` で最初の 1 つにしか戻さないため
  - swap で地域は落ちない（JP/13・US/CA・GB/CON が保持される）
  - seed のバッジは、`SwapWith` がスピンボックスの内部 line edit 経由で同期させているので、これらのステップではずれなかった
  - city を持つ選手と持たない選手を swap すると、city を持っていた側のスロットに `city: null` が残る（キーは消えない。もう一方のスロットには city が移る）
  - `mains` は、人数・キャラ数の変更時の空の値のまま残る（`SetData` の mains は `mains` を publish しない）
- 残置 / リスク
  - 描画は state 起点の書き込みが入る S2c から実際に効く。それまで、本番では差分の無い比較だけが走る
  - **確定していない打鍵は描画で消え得る**: 編集可能コンボ（国・地域・キャラ・スキン・バリアント）に打ち込んで、Enter や候補の選択で確定しないまま離れた文字は publish されない。そのカードの描画が走ったとき（スロットのどこかが別経路で変わった、または前回の描画が欄を飛ばして完了していない）に、コンボの表示が state と食い違っていれば state の値で上書きされる。コンボが行を選んでいる状態では確定前の文字は比較に入らないので、上書きされるのはその欄の値が別経路で変わったときだけ。キャラ欄が手入力名の状態（行を選んでいない）では、コンボの文字そのものが比較対象なので、そのカードの描画が走れば上書きされる。フォーカスが外れたときの再描画（欄を飛ばした場合だけ予約する）で、その機会は増えた。ウィンドウの切り替え（alt-tab）も同じで、`focusChanged(old, None)` が来て飛ばした欄にフォーカスが無くなるので再描画が予約され、戻ったときにはコンボの未確定文字が state の値になっていることがある。line edit は打鍵すればフォーカスアウト（非アクティブ化も含む）で Qt が確定（`editingFinished`）するので該当しない
  - **飛ばし中の欄を読む既存操作は古い表示を拾う**: 欄を飛ばしている間（フォーカス中で、別経路の新しい値をまだ出していない間）は、widget を読む操作 `SwapWith`（読んだ値を相手のカードへ写して publish する）・`SavePlayerToDB`・`GetCurrentPlayerTag` が古いテキストを使う。また `SwapWith` が写し込む値が、たまたまその欄で飛ばしたときのテキストと同じだと、その確定は書かない側に入る。いずれも swap / clear を core に移して state を読むようにする S2c / S2d の検収観点にする
  - **再現できない値を持つスロットは、カードが古い表示のまま残る**（何も変えないことを選んだ結果）。そのボードの held サイクルごとに再試行し、そのたびに debug ログを出す
  - **seed の描画では upset_factor が再計算されない**。描画はスピンボックスをシグナル停止で変えるので、`player_seed_changed` → `UpsetFactorCalculation` が出ない。今の swap（`SwapWith`）はスピンボックス経由で再計算されているが、S2c で swap を core に移すとカード経由の再計算は無くなる。S2c で core 側（swap の操作がアップセットの再計算を呼ぶ）で手当てする
  - **`LoadSkinOptions` のモデルが増える**: キャラの行が変わると、描画もスキンコンボのモデルを差し替える。キャラ無し（空・手入力）のときは毎回新しい空の `QStandardItemModel` を作る（既存の選択経路と同じ）。描画が頻繁に走る S2c 以降は、空になるたびにモデルが 1 つ増える。#13 の「差し替えずに使い回す」（`_EnsureStateModel` と同じ形）に寄せるかを S2c で判断する。数は計測していない
  - **描画のコストは計測していない**。前回描画したスロットと同じならスキップするが、スロットが変わったカードは描画対象の全キーを複製し、キャラ欄ごとに `CharacterValue` を 1〜2 回組む。描画が実際に走り始める S2c で、セット読込や swap のときにかかる時間を測る
  - 選択時の地域の publish（`_OnLocationChanged`）は、今も `_loadedStatesCountryCode` で解いている。S1d の残課題のうち、描画側だけが解消した
  - 描画は GUI スレッドで `StateManager.lock` を短時間取る（スロットの描画対象キーを 1 回で複製する間だけ）。その後で `dataLock` を取るので、`dataLock` → `lock` の既存の順序とは入れ子にならない。line edit の確定時の比較は `StateManager.Get` で 1 キー読むだけ

#### S2b losers → mergedName を core へ（2026-10-23・PR #223）

LOSERS フラグと、そのチームの選手名に付く ` [L]`（`mergedName` / `mergedOnlyName`）を、フラグの書き手（core）が 1 保存サイクルで書くようにした。カードはフラグのコピーを持たない。

- **`PlayerService.PublishMergedName(sponsor, tag)`**: `losers` 引数を廃止。スロットのパスから `score.{n}.team.{t}.losers` を求めて state から読む（`TeamLosersKey()`。`player_editor.1` はチームが無いので付かない）。フラグの読みと 2 キーの書き込みは `StateManager.lock` 下。sponsor / tag は今も呼び出し側（カードのテキスト）から受け取る。名前の確定では、同じ値が先に state へ入っている（下記の接続順）
- **`PlayerService.RepublishTeamMergedNames(board, team)`**（モジュール関数）を新設した。state にあるそのチームの全スロットについて、スロット自身の `team` / `name` から `PublishMergedName` で名前を作り直す。名前の無いスロットも含む（旧 `SetLosers` が全カードで書いていたのと同じ）。1 SaveBlock・`StateManager.lock` 下
- **`AutonaActions.SetTeamLosers`**: フラグが変わったときだけ、同じ SaveBlock で `RepublishTeamMergedNames` を呼ぶ。旧経路（`toggled` → カードの `SetLosers`）と同じく「変化時のみ」で、同じ値の再送では何も書かない
- **`SetLoadService.ApplyLosers`**: 同じく、フラグが変わったチーム（`DisplayTeam` で解いた面）ごとに作り直す。`AutonaActions.instance.SetTeamLosers` を呼ばずに同じ組を持つのは、`SetLoadService` が Actions のシングルトンに依存していない（core 単体テストもインスタンス無しで動く）ため
- **削除**: カードの `losers` 属性と `SetLosers`、ボードの `_AdoptTeamLosers` / `_ApplyLosersToPlayers`、カードごとの `toggled.connect(p.SetLosers)`。LOSERS チェックボックスのハンドラは `SetTeamLosers` の 1 本だけになった
- **カードの接続順を変えた**（review 後）: タグ / スポンサー欄の `editingFinished` で、`_OnTextChanged`（`name` / `team` を state に書く）が `NameChanged`（`mergedName` を publish）より先に走るよう、`NameChanged` を `_WireInputs` の後で繋ぐ。旧順では、別スレッドから呼ばれた `SetTeamLosers` の作り直しが 2 つの間に入ると、state に残っている古い `name` に ` [L]` を付けた名前が最後に残った（カード自身の publish は先に済んでいる）。新順では、作り直しがどちらの側に入っても新しい名前になる
  - 反映サイクル数と golden（22 ステップ）は変わらない。変わるのは**手入力で埋めた新しいスロットのキー順**だけで、`name` が `mergedName` / `mergedOnlyName` の前に来る（`SetData` で埋めたスロットと同じ順になった）。値は同じ
  - S1d の書き込み列 golden（`test_player_slot_publish.py`）では、「clear」ステップ（ボード・エディタとも）で `name` の書き込みが名前の組と画像より前に来る（`Clear()` がタグ欄の `editingFinished` を通るため）。書き込みの集合と保存サイクル（1）は同じで、キーはそろっているので state のキー順と delta も同じ。`_expectedOperations` に意図した差分として足した
  - pin: `test_a_losers_flip_from_another_thread_mid_name_edit_keeps_the_new_name`（カードの publish の直後に実スレッドから `SetTeamLosers` を走らせる。旧順では `OLD [L]` が残って落ちることを確認した）
- **`/api/v1` `PATCH set` の `losers`**: `set_route` に渡してスワップ時にチーム番号を反転させる経路をやめ、状態側のチームで新設の `WebServerActions.set_team_losers(n, team, value)`（`@gui_thread_sync`）を呼ぶ。中身は `AutonaActions.SetTeamLosers` と、下記の旧経路の副作用。ルートはこれを呼ぶだけで、旧クエリと同じ適用順（他のキー → losers → `teamName`）を守るため 1 つの GUI タスク内で順に呼ぶ（`teamName` を別呼び出しにしているのもこの順のため）。legacy の `-set?losers=` は変えていない（set 形式・スワップで反転・`match_en` を空にする）
  - **旧経路の副作用は外部挙動として維持した**（S2 の方針）。旧経路は `ChangeSetData` ペイロードだったので、(1) `match_en` を空にしていた（`ApplyMatchInfo` は `round_name_en` の無いペイロードで空にする。フラグが変わらなくても空にする）、(2) 未構築のボードを構築していた（`set_route` / `get_swap` → `GetScoreboard`。構築で `score.{n}` が held 登録され、書き込みは反映待ちになる）。`set_team_losers` は、ボードを構築（`EnsureScoreboard`）し、そのボードの `setLoad.ApplyMatchInfo({})` と `SetTeamLosers` を 1 SaveBlock で適用する（旧経路と同じく held 1 サイクル）。当初はルート（`AutonaApiV1`）が `SetLoadService` をその場で組んでいたが、review で層の逸脱とされ、構築済みボードの `setLoad` を使う `WebServerActions` 側へ移した。(2) は decision 7（未構築ボードの held 登録を bootstrap へ）を実施すれば不要になる、としていたが、decision 7 は S2c で撤回したので (2) の構築は残る
  - losers だけの PATCH で `set_route` を呼ばなくなったが、上の 2 つ以外に `ChangeSetData` が losers だけのペイロードでしていたことは無い（`ApplyScores` は該当キーが無く何もしない。エントラント・単独選手の分岐にも入らず、`ApplyBracketType` の前に戻る）
  - pin: `test_set_patch_losers_still_blanks_the_english_round_text`（空にする・同じフラグの再送でも空にする・held 1 サイクル）、`test_set_patch_losers_on_a_board_not_built_yet_still_waits_for_apply`。legacy と同じ state になることを見る比較（3 テスト・4 ケース。`players`（人数増）と `losers` を 1 PATCH で送る `test_set_patch_players_and_losers_together_match_legacy_query` を含む）は `match_en` も含めて一致する。`set_team_losers` の構築と `ApplyMatchInfo` を外すと 6 ケースが落ち、ルートが losers を `players` より先に適用すると人数増のケースが落ちることを確認した。仕様書 `.claude/design/external-control-api.md` §6.5 を更新した
- **golden**: 移行前（76a740fb）で 8 ステップを追加し、記録し直した（既存の 14 ステップは bd0fa50b の記録と完全に一致）。追加したステップは、2 人に戻して両チームを `SetData` / 埋まったチームに losers / losers のチームで名前を手入力 / losers のチームで 3 人に増やす / 1 人に戻す / losers 付きのセット読込で人数が増える / losers 側があるまま swap / スワップ中に losers だけのセット読込。各ステップに、反映 delta をキー順を除いて比べる digest（`publishedRows`）も足した。golden は移行前の記録のまま残し、意図した差分を `S2B_CHANGES`（`tests/test_player_card_render.py`）に列挙して適用してから比較する（列挙していないフィールドはすべて一致を要求する）
  - **losers の操作が 1 サイクルになった**: 「losers」3 → 1、「埋まったチームに losers」4 → 1。state・反映 delta とも移行前と同じ
  - **losers のチームで人数を増やしたとき、新しい空のスロットに ` [L]` だけの名前を出さなくなった**（`mergedName` / `mergedOnlyName` = `" [L]"` が無くなる）。名前が入れば ` [L]` 付きで出る。losers でないチームの空のスロットが名前を持たないのと同じになった
  - **losers 付きのセット読込で増えたスロットは、キー順だけが変わる**: `mergedName` / `mergedOnlyName` が `name` の後ろに来る（旧: 生成直後に `" [L]"` を書いていたので前寄り）。値と反映 delta の行（キー順を除く）は同じ
  - **人数を減らして外したカードが、LOSERS の `toggled` に繋がったまま残っていた**（既存の穴）。外したカードは `deleteLater` 待ちの間も接続が生きていて、swap でチェックボックスが動くと、消えたスロット（`player.3`）を `mergedName` だけで作り直していた（「losers 側があるまま swap」「スワップ中に losers だけのセット読込」の差分）。テストハーネスは `deleteLater` を流さないので顕在化した。実機ではイベントループに戻れば消えるので、縮小と同じ周回内のトグルでしか起きない（移行前のコードで、`DeferredDelete` を流すと再現しないことを確認した）。S2b で接続そのものが無くなった
- **pin**
  - core（Qt なし）: `test_player_service.py`（フラグを state から読む・スロットごとに自分のチームのフラグ・エディタには付かない・チームの作り直しが全スロット / 1 サイクル / ロスターが無ければ何もしない）、`test_actions.py`（フラグと名前が held 1 サイクル・解除も同じ・再送では何も書かない）、`test_set_load_service.py`（スワップ中は表示側のチームに ` [L]`・delta 1 回・フラグが変わらなければ名前に触らない）
  - 1 操作で held 1 サイクル: `ONE_CYCLE_STEPS` に「losers」と追加ステップのうち 6 つを加えた。`test_the_losers_checkbox_writes_the_flag_and_the_player_names` でもチェックボックス 1 回 = held 1 サイクルを確認する
  - カード表示 == state: 22 ステップのそれぞれの後で、全カードと LOSERS チェックボックスが state と一致すること
  - 移行前のコード（76a740fb の `src`）に新しいテストを当てると、サイクル数・名前の各テストが落ちることを確認した（v1 の `match_en` と未構築ボードのテストは、旧経路の挙動をそのまま pin しているので移行前でも通る側）
- **テストの追従**: カードの `losers` 属性を見ていた 7 テスト（`test_scoreboard_operations.py` 6・`test_player_widgets.py` 1）を、state の確認に書き換えた
- 残置 / リスク
  - 名前の確定（`NameChanged`）はウィジェットのテキストで publish し、作り直しは state の `team` / `name` を使う。確定のたびに両者は一致する（同じ `editingFinished` で state に入る）。フォーカス中で未確定の打鍵は、旧実装（カードのテキストで作る）と違って作り直しに入らない。state と一致する側に寄った
  - `SetTeamLosers` は core なので GUI スレッド以外からも呼べる（今の呼び出しはすべて GUI スレッド: チェックボックス・`ChangeSetData`・v1 の `set_team_losers`）。カードの名前確定との競合は、上記の接続順の変更で解消した
  - ボード構築時の `losers.toggled.emit(False)` も `SetTeamLosers` を通るが、作り直す対象は無い。`__init__` の `SeedBoard` がサブツリーを skeleton に戻した後（`_WireTeamColumn`）で、選手カードを作る（`playerNumber.setValue(1)`）より前なので、state にそのチームのスロットが無い。skeleton にフラグのキーが無いので `False` の書き込みだけは起きる（旧経路と同じ）

#### S2c swap を core へ（2026-10-23・PR #224）

チームスワップと選手カードの上下移動を、カード間の値コピー（`SwapWith`）から state の入れ替え（`AutonaActions.SwapTeams` / `SwapPlayers`）にした。カードは S2a の描画で追従する。

- **`AutonaActions.SwapTeams(board)`**（board 1/2。board 3 は `False` を返し、呼び出し側がクルーウィジェット自身の入替に委ねる = 決定 6）: 1 SaveBlock・`StateManager.lock` 下で、両チームにある選手スロット・スコア・LOSERS・チーム名・ロゴ・ステージ記録（`GameTrackerService.SwapStageResults` で鏡映）を入れ替え、`score.{n}.teamsSwapped` を反転し、アップセット係数を計算し直す。**チームカラーは入れ替えない**（列の色。旧実装も入れ替えていない）
- **`AutonaActions.SwapPlayers(board, team, first, second)`**: 同じチームの 2 スロットを入れ替える（カードの上下移動）。同じスロット同士（先頭の上・末尾の下）は何もしない
- **入れ替えはキー単位**（スロットの dict ごと差し替えない）。理由: 両側にあるキーは各 dict 内の位置を保つので、`program_state.json` と Socket.IO delta のキー順が旧実装と一致する（決定 8・外部契約の安全側）。片側にしか無いキーも旧実装の出力に合わせた
  - 空欄に当たる既定値を入れる（`SLOT_DEFAULTS`。テキストは `""`、seed は `0`）。既定値の無いキー（birthday・国・キャラ・mains・スポンサー画像など）は有無ごと動く
  - テキスト欄・合成名・アバター 2 キー・seed は両側に必ず書く（`SLOT_ALWAYS`。旧実装が両カードで無条件に publish していた。seed は旧 `SetSeed` と同じく常に int（`SLOT_CONVERT`）
  - `id` / `city` は None のためにキーを作らない（`SLOT_NONE_ABSENT`。`PlayerService.SetCity` / `SetPlayerId` のガードと同じ）。S2a で記録した「city を持つ選手と持たない選手を swap すると、持っていた側に `city: null` が残る」は旧実装どおり
  - 新しいキーは旧実装の書き込み順（`SLOT_ORDER`）で末尾に足す。チーム側は `TEAM_SWAP_ORDER` の 4 キー（スコア・LOSERS・チーム名・ロゴ）だけ
  - **チーム入替は両チームにあるスロットだけ**を入れ替える。片側だけのスロット（カードの無いスロットを state が持っている場合）は動かさない（旧実装はカードを組にして入れ替えていた）
- **`teamsSwapped` の正本を state にした**: ボード属性と `AutonaActions.SetTeamsSwapped`（ミラーの publish）を削除。`SetLoadService.DisplayTeam` は `score.{n}.teamsSwapped` を読む（callable の注入をやめ `SetLoadService(scoreboardNumber)`）。`WebServerActions.get_swap` もボード 1/2 は state を読む（ボードの構築は従来どおり）。クルー（board 3）は属性のまま（決定 6）
- **入口**: SWAP ボタン → `board.SwapTeams()`、上下移動ボタン → `board._MovePlayer` → `SwapPlayers`。ホットキー `swap_teams` は `AutonaHotkeys.ConnectActions` が結線し、`ActiveBoard()` を入れ替える（決定 2。旧: 構築済みの全ボードが受けていて、形式を切り替えた後は両ボードが入れ替わった。クルー形式では何もしない）。アクティブボードが未構築なら何もしない（旧実装どおり。未構築ボードは held でないので、入れ替えると即時公開され、構築時のシードで消える）。Web（legacy REST / Socket.IO / v1 `:swap-teams`）は `WebServerActions.swap_teams` が**ボードを構築してから** `board.SwapTeams()` を呼ぶ（決定 7 の撤回により、構築は旧経路と同じく維持。構築前に入れ替えると即時公開されたうえ、構築時の `SeedBoard` で消える）
  - **Web の `swap_teams` は `@gui_thread_sync` を残した**（S2f まで）。理由: カードの入力確定は GUI スレッドで「フィールド → 合成名」の 2 書き込みに分かれている（`_OnTextChanged` → `NameChanged`）。別スレッドの swap がその間に入ると、合成名が反対のスロットに残る（S2b の losers と同じ形）。結果として、Web の swap は応答時点で完了している（旧: queued シグナルで、入替前に `OK` を返していた）
  - **入力中の欄は入替・移動の前に確定する**（`AutonaScoreboardWidget.CommitFocusedEdit`）。旧実装は欄の表示をそのまま読んでいたので、打ちかけのタグは選手と一緒に動いていた。新実装は state を入れ替え、描画はフォーカス中の欄を飛ばすので、何もしないと打ちかけの文字がカードに残り、フォーカスアウトで「入ってきた選手」に書かれる。そこで、そのボード内（詳細ダイアログ含む）にフォーカスがあればフォーカスを外し、Qt に確定（`editingFinished`）させてから入れ替える。オペレーターが別の場所をクリックしたときと同じ確定で、文字はその選手のスロットに入ってから動く。SWAP ボタン・上下移動・ホットキー（構築済みのアクティブボード）・Web の全入口で行う。欄の補完ポップアップ（タグの候補。フォーカスで開く）が開いていれば先に閉じる（開いたままフォーカスを外すと `editingFinished` が出ず、ポップアップも入ってきた選手の欄に残る。閉じても候補は選ばれない）。編集可能コンボの未確定の打鍵はフォーカスアウトで確定しない（S2a と同じ）
  - `get_scoreboard` の未構築ボード構築は残した（応答の形を変えない安全側）
- **カード・スコア・ステージは held サイクルから追従する**: カードは描画の予約（S2a）、スコアはスピンボックスの同期（`_SyncScoreDisplays`）。ステージボタンは、ボードの `_NoticeSwap`（held サイクルでフラグの反転を検知）が `individualGameTracker.RenderStageResults()` で描き直す（同点の swap ではスコアが動かず、`_SyncScoreDisplays` が描かないため）。トラッカーの使われていなかった `SwapStageResults`（ウィジェット側）は `RenderStageResults` に置き換えた
  - **入替サイクルでは、ボードがトラッカーにスコアの変化を伝えない**（`_SyncScoreDisplays(followStages=False)`。`_NoticeSwap` が入替を検知したサイクルだけ）。理由: 入替は記録の鏡映とスコアの交換を同じサイクルで済ませている。旧実装はそこで記録を付け直して並びを崩していた（T2 T1 T1 → T1 T2 T1。スコアは 1-2 なのに記録は 2-1）。当初は `GameTrackerService.SyncStagesToScore` に「集計と一致していれば書き換えない」ガードを置いたが、review で他の経路（`AdjustScore` / `ResetScore` / `ApplyScores` / `UpdateScore`）でも、記録とスコアがずれている状態（タイの解除後など）で挙動が変わると指摘されたため、swap 経路に限定した（`GameTrackerService` は HEAD のまま）
- **統計の再取得は操作ごとに 1 回**: カードは自分が id を変えたとき（`ExportPlayerId`）にしか取得しないので、ボードが出す。チーム入替は `_NoticeSwap` が、両チームにあるスロット対のどこかで id が違えば両チーム分。上下移動は `_MovePlayer` が、2 スロットの id が違えば動かしたチーム分。発火は `QTimer.singleShot(0)` でイベントループに戻ってから（#9）。入口が複数あるので、state から検知する
- **アップセット係数は操作が core 内で計算し直す**（`_UpdateUpsetFactor` → `StatsService.UpdateUpsetFactor`。人数は state のスロット数）。S2a の残置「seed の描画では upset_factor が再計算されない」への手当て。係数は held 対象外の統計キーなので即時公開される。Web の swap はボードを構築してから行うので、旧実装（カードの seed 経由で同じキーを書いていた）と同じく構築済みボードでの即時公開になる
- **`LoadSkinOptions` の空モデルを使い回す**（S2a の残置への判断）: キャラ無しのときに毎回新しい空の `QStandardItemModel` を作っていたのを、カードごとに 1 個（`_emptySkinModel`。カードを親にして初回だけ作る）にした。描画が実際に走る S2c 以降、空になるたびにモデルが増えるのを防ぐ（#13 の「差し替えずに使い回す」）
- **削除**: `AutonaBasePlayerWidget.SwapWith` と `_swapRestoreTail`（基底・スコアボードカード）、ボードの `SwapTeams` シグナル・`teamsSwapped` 属性・swap ホットキーの接続、`AutonaActions.SetTeamsSwapped`、トラッカーウィジェットの `SwapStageResults`
- **golden**
  - 既存の 22 ステップ（`golden.json`）は記録し直していない。意図した差分を `S2C_CHANGES` に列挙して適用してから比較する（S2b と同じ方式）。差分は 6 ステップ
    - 「swap teams」「move a player down」「swap teams back」「pick a character」「reset match」: 選手と一緒にバリアントと mains が動くぶん state が変わる（`travel`。各選手のバリアント・mains を「losers」ステップ時点のその選手のものに置き換えて期待値を作る。バリアントは同じキャラのままのときだけ）
    - 「clear players」: state は同じ。反映 delta に、swap で残るようになった 2 つのバリアントの削除が加わる
    - 保存サイクル数は全ステップで不変
  - **swap golden を追加した**（`swap_golden.json`、移行前の 6a6f04cc で 14 ステップを記録）: id・片側だけの city・選手ごとに違う mains・片チームだけのロゴ・トラッカー ON（best of 5、T2 T1 T1）・losers を揃えたボードで、swap / 下へ / 上へ / 先頭の上（何もしない）/ トラッカー ON で 1 点 / swap 戻し。各ステップで統計シグナルの発火数も記録した。意図した差分（`S2C_SWAP_CHANGES`）
    - mains とバリアントが選手と一緒に動く（`travel`）
    - ステージの並びが鏡映になる（swap 後 T1 T2 T2。旧は T1 T2 T1）。以降のステップもこの並びを引き継ぐ
    - 統計は操作ごとに 1 回: swap は 5 シグナル各 1（旧: Recent 6・LastSets / Standings 各 3）、移動は Recent と動かしたチームの 2 つが各 1（旧: 各 4 または 2）。`UpsetFactorCalculation` シグナルは 0（旧: 2〜4。core が直接計算する）
    - 「下へ」は反映 delta が変わる（mains が動く分）。「上へ」は反映が 100 行を超えて `state_big_change` になる（旧: delta）
    - 保存サイクル数（swap・移動とも 1）は不変。地域・ロゴ・city・id は旧実装と同じ state
  - **キー順の録画を追加した**（`key_order.json`。6a6f04cc で、上の 2 つのステップ列の各ステップ後に、ボードのサブツリーの全 dict のキーの挿入順を記録。`assets` / `variant` / `mains` の中身は値が意図して変わるので辿らず、親の dict での位置だけを見る）。state digest は値が変わったステップでは新実装自身のものに置き換えているのでキー順を守れない。このため、キー順はこの録画と全ステップで照合する。**36 ステップすべてで旧実装と一致**（片側にしか無いキーを足す順を含む）。`SLOT_ORDER` の 2 キーを入れ替えると落ちることを確認した。照合範囲はチームボード（board 2）の `score.2` サブツリーの state の挿入順だけ（1v1 ボードは録画していない。入替のコードは共通）。反映 delta の行順は、値が変わらないステップでだけ golden の `publishedRows` が見る
  - 意図した差分で golden に現れないもの: **birthday が選手と一緒に動く**（旧実装は隠しチェックボックスを読まず、カードに残っていた。golden の選手は全員 false）、**seed は常に int で両側に書く**（旧 `SetSeed` と同じ。golden の選手は全員 seed を持つ）。どちらも `test_actions.py` で pin
- **pin**
  - core（Qt なし。`test_actions.py`）: 両面の入替が held 1 サイクル / バリアントと mains が選手と動く / 片側だけのキー / None のために city・id を作らない / seed は常に int（片側に無ければ 0・文字列は数値に）/ birthday が選手と動く / 片チームだけのスロットは動かない / スロットのキー順 / 2 回で元に戻る / ステージの鏡映 / アップセット係数の再計算 / 選手のいないボード / クルーは `False` / 上下移動と、移動にならない引数（parametrize）
  - `test_set_load_service.py`: `DisplayTeam` が state のフラグを読む
  - ボード（`test_scoreboard_operations.py`）: SWAP 1 回 = held 1 サイクルでカードが追従 / 1v1 の swap で統計が 1 回（id が違うとき）・0 回（両方 id 無し）/ ホットキーはアクティブボードだけ / アクティブボードが未構築ならホットキーは何もしない / Web の swap は応答時点で完了 / Web の swap は未構築ボードを構築してから入れ替え、入替は反映待ちで、その後の `GetScoreboard` 経路でも残る / `get_swap` もボードを構築する / トラッカー ON の swap でステージの行とボタンが鏡映（parametrize）
  - カード（`test_player_widgets.py`）: 既存の swap テストを Actions 経由に移植（seed バッジの追従を追加）/ 先頭の上移動は何もしない / キャラ無しのカードは空のスキンモデルを 1 個だけ使う
  - `test_player_card_render.py`: 上記 2 つの golden とキー順の照合、各ステップ後のカード表示 == state、**入力中のタグが選手と一緒に動く**（ボタン・ホットキー・Web の 3 入口と上下移動。オフスクリーンで実際にフォーカスして打鍵する。確定を外すと 4 ケースとも落ちることを確認した）/ 補完ポップアップが開いたままのホットキー swap でも同じ（ポップアップを閉じる処理を外すと落ちることを確認した）
- **テストの追従**: `test_signal_lifetime.py`（ボードの swap シグナル・ホットキー接続が無くなった）、`test_objectname_keypath_contract.py`（docstring）
- 残置 / リスク
  - 入力中の欄の確定はフォーカスを外して行う。ホットキー・Web の swap では、オペレーターが打っていた欄からカーソルが外れる（旧実装はカーソルを残したまま、欄の表示を相手の値に書き換えていた）
  - 別の人が Web UI から swap すると、デスクトップで打っている途中の欄からもカーソルが外れ、打ちかけの文字はその時点で確定してその選手と一緒に動く（UX の変化。旧実装は欄の表示ごと入れ替え、カーソルは残っていた）
  - Web の `score:adjust`（別スレッド）と GUI の swap がほぼ同時に来て、ボードが両方の書き込みを 1 回の held 通知で受けると、入替を検知したサイクルは `followStages=False` で同期するので、+1 のステージ追従（トラッカー ON 時の勝敗行の追加）が落ちる。スコアは正しく、勝敗行が 1 つ足りない（以後の追従は差分なので自然には戻らない。ステージボタンで直せる）
  - Web の swap が GUI スレッドでの同期実行になったので、GUI が詰まっている間は Web の応答も待つ（他の `@gui_thread_sync` と同じ）
  - 統計の発火は、ボードが held サイクルで state から推定する。1 サイクル内で 2 回 swap される（フラグが元に戻る）と検知できないが、swap は 1 SaveBlock = 1 サイクルなので今の入口では起きない。ボードの無い swap（テストハーネスでホットキーを押した場合）では統計は出ない
  - 描画のコスト（S2a の残置）は計測していない。swap では全カードが描画対象になる
- 実機確認（オーナー）
  - 1v1・チームの両形式で、SWAP ボタン・ホットキー（Ctrl+Shift+S）・Web UI の swap: 選手名・キャラ（バリアント含む）・国 / 地域・seed バッジ・アバター・スコア・LOSERS・チーム名・ロゴが入れ替わり、チームカラーは残る。反映後に OBS のオーバーレイが入れ替わる
  - タグを打っている途中で SWAP / ホットキー / 上下移動: 打ちかけの文字がその選手と一緒に動き、入ってきた選手の名前を上書きしない
  - トラッカー ON で勝敗を付けてから swap / swap 戻し: ステージボタンの並びが鏡映になり、スコアと一致する
  - 選手カードの上下移動（キャラ・mains が選手と一緒に動く）
  - チーム形式を表示中にホットキーで swap したとき、1v1 ボードは入れ替わらない。クルー形式ではホットキーの swap が何もしない（クルーの swap はボタンと Web から）
  - 1v1 表示中に Web の `/team` ページから swap: チームボードが構築されて入れ替わり、形式をチームに切り替えると入替が残っている
  - Start.gg の選手で swap したとき、統計（直近の対戦・戦績）が 1 回だけ取り直される

#### S2d clear 系を core へ（2026-10-23・PR #225）

スコア・選手・ボード全体のクリアを、ボードのウィジェットを空に戻す実装から、state を直接書く core 操作にした。旧実装は、スコアのスピンボックスを 0 にして `valueChanged` にスコアを書かせ、LOSERS のチェックを外し、選手カードごとの `Clear()` で欄を 1 つずつ空にしてハンドラに publish させていた（カード 1 枚ごとに 1 保存サイクル）。新しい操作は 1 操作 = 1 SaveBlock で、カード・スピンボックス・チェックボックス・ステージボタンは held サイクルから追従する（S2a）。

- **`AutonaActions` に 4 操作**（board 1/2。board 3 は `False` を返し、呼び出し側がクルーウィジェットに委ねる = 決定 6）
  - `ClearScore(board)`: 両チームのスコアを 0、LOSERS を False にする（`SetTeamLosers` 経由で ` [L]` も外す）。変わる値だけを書く。match バーは残す（セット読込の前処理。S-17）
  - `ResetMatch(board)`: `ClearScore` + `ClearMatchInfo`（オペレーターのリセット）
  - `ClearPlayers(board, no_mains=False)`: 両チームの全スロットを、旧カードの `Clear()` が publish していた内容どおりに空にする（`_ClearSlot`）。seed が変わったスロットがあれば、アップセット係数を core 内で計算し直す
  - `ClearBoard(board)`: 「すべての入力値をリセット」。`ClearPlayers` + `ResetMatch` + チーム名 `""` + ロゴ None（変わるときだけ）
- **`_ClearSlot` は旧 `Clear()` の出力を再現する**。旧実装はウィジェットを空にしてハンドラに書かせていたので、何が書かれるかは表示中の値（S2a 以降はスロットの中身）で決まっていた
  - 常に書くもの: `online_avatar` = None、seed = int 0（旧 `SetSeed` の正規化）
  - 文字が入っていたときだけ `""` にするもの: テキスト 4 欄とメモ
  - スポンサーかタグに文字があったときだけ、空の名前で publish し直すもの: 合成名・画像・id。名前の無いスロットの id は残る
  - 国があれば地域 → 国の順で `{}`、地域だけなら地域だけ
  - キャラは、どれかの行が空でなければ全行を空にする
  - birthday・city・mains は残す
  - **`no_mains` の癖も再現した**: キャラが残るのはキャラ行が 1 のときだけ（2 行以上では、他の行のループが全行のコンボをリセットしていた）。0 行のときは国・地域も残る。`no_mains` でセットを追従すると、セットが届くまでこの state が publish される
- **判断 3（Web `clear_all` とデスクトップ `ResetAllInputs` の差）は揃えていない**。ただし「`ClearBoard` にチーム名のオプションを持たせる」形は採らなかった
  - 理由: Web の `clear_all` は「`ResetMatch` → best_of 0 → 人数 1・キャラ数 1 → 選手のクリア」の順で、この順が mains を決める（キャラ数の変更が `SetCharactersPerPlayer` で mains を publish し直す）。オプション付きの `ClearBoard` で選手のクリアを人数・キャラ数より前にすると、mains が変わる
  - 実装: `clear_all` は旧順序のまま、`AutonaActions.ResetMatch` と `board.CommandClearAll()`（= `ClearPlayers`）を呼ぶ。`ClearBoard` は `ResetAllInputs` 専用でオプションは無い
  - チーム名は旧実装どおり Web では残る（人数を 1 に減らす場合は、人数変更の処理で消える）
  - 人数・キャラ数のリセットは S2g まで、best_of は S2e まで、ボードのスピンボックスのまま
- **判断 4（`lastSetSelected`）**: ボード属性から `SetLoadService.lastSetSelected`（state の外）へ移した
  - クリア（`_ClearingPlayers`）と `ClearSetTracking()` で忘れるので、同じセットの再読込が効く
  - `NewSetSelected` は `BlockSaving` の中で `CommandClearAll` → `ClearScore` → `lastSetSelected = id` の順に実行する（クリアが忘れるので、記録はその後）
  - `UpdateSetData` の stale 判定、`LoadStationSets`、Web の `get_set` もここを読む
- **ボードに残る部分（`AutonaScoreboardWidget._ClearingPlayers`）**: 入力中の欄の確定（`CommitFocusedEdit`）→ クリア → 追跡セットを忘れる → id が変わったチームの統計を 1 回取り直す（`_RequestPlayerStats`。`QTimer.singleShot(0)`）
  - 全入口で通る: `CommandClearAll`・`ResetAllInputs`・Web の `reset_players` / `clear_all`・セット読込の前処理
  - 確定しないと、描画がフォーカス中の欄を飛ばすので打ちかけの文字がカードに残り、フォーカスアウトで空にしたスロットへ書かれる（S2c と同じ問題）
  - 打ちかけの文字が消えるのは**選手欄だけ**。match / phase / pool / チーム名 / スコア欄の入力中の文字は、旧実装と同じくフォーカスアウト時に確定する（回帰ではない）。`ResetAllInputs` だけは確定の後に `ClearBoard` がチーム名と match バーを空にするので、その 2 つも残らない
  - `ResetAllInputs` は確認ダイアログをデスクトップに残し、`ClearBoard` と best_of 0 を 1 SaveBlock で行う
  - ボードの `ClearScore()` / `ResetMatch()` は Actions を呼ぶだけになった
- **Web**: `reset_match` / `reset_players` / `clear_all` / `get_set` と、`set_route` の phase / pool / match / losers / teamName から `GetScoreboard(` を除いた
  - 未構築ボードは `EnsureScoreboard` で構築する（旧挙動を維持）。理由は S2c の swap と同じ: 未構築ボードは held でなく、書き込みは即時公開されたうえ、構築時の `SeedBoard` で消える。best_of / 人数 / キャラ数もまだボードのスピンボックス
  - いずれも `@gui_thread_sync`（S2f まで。カード入力の確定が「フィールド → 合成名」の 2 書き込みに分かれており、その間に割り込ませない）。`get_set` はボードを構築するので新たに付けた（旧は `GetScoreboard` が構築を GUI スレッドへ渡していた）
  - クルーボードは各ボードのメソッドに fallback する。`get_set(3)` は旧実装どおり AttributeError
  - `set_route` の phase / pool / match / losers は `SetLoadService.ApplyMatchInfo` + `ApplyLosers`（core）で適用する。旧 `ChangeSetData` と同じ挙動で、英語のラウンド名を空にし、losers は set 側のチーム（swap 後は反対の列）に付く
  - teamName は `SetTeamName` + `PublishTeamLogo` を 1 SaveBlock で書く（旧 2 サイクル）。クルーボードは `ChangeSetData` のまま
  - `set_route` の bestOf / players / characters と `_reset_best_of` はスピンボックスのまま（S2e / S2g）
- **Web / v1 のチーム名と入力中の欄（レビュー指摘）**: デスクトップでチーム名欄を入力中に Web / v1 から teamName を書くと、フォーカスアウト時の `editingFinished` が欄の文字で Web の名前を上書きしていた。選手カードと同じ held-back ガードを入れた
  - `_SyncSetFields` は、フォーカス中で描画を飛ばした時点の欄の文字を `_heldBackTeamName` に記録する
  - 確定（`_OnTeamNameFinished`）は、欄の文字がその記録と同じで state の値と違う（= その後オペレーターが何も打っていない）なら書かない。フォーカスが外れたら state から描画し直す（`_OnTeamNameFocusChanged` → `_SyncSetFields`）
  - Web の書き込みの後にオペレーターが打った場合は、打った文字が勝つ（後勝ち）
- **判断 5**: Clear で city が消えない問題は直していない（記録のみ。`test_a_clear_keeps_the_city` が現状を pin）
- **golden**
  - `clear_golden.json` を移行前の 2045c973 で記録した（35 ステップ）
    - 準備: 2 人 × 2 キャラ、トラッカー ON（BO5、T2 T1 T1）、両チームの id / seed、チーム名、match バー、losers
    - 対象: `ClearScore` / `ResetMatch` / Web `reset_match` / Web `set_route`（phase / pool / match・losers・teamName。swap 中を含む）/ `ClearPlayers`（`no_mains`、キャラ 1・2 行）/ Web `reset_players` / Web `clear_all` / `ResetAllInputs` / セット追従の前処理
    - 統計シグナルの発火数も記録した
    - `key_order.json` に「clear operations」節を追加した（既存の節は不変）
  - レビュー後に同じ 2045c973 で 2 節を追加録画した（`clear_golden.json` の `singles` / `prepass`、`key_order.json` の「singles clear operations」/「set load pre-pass」。既存の節は不変）
    - `singles`（1v1 ボード、25 ステップ）: 上と同じクリアを 1 人ずつで行う。チーム名なし。最後のセット追従は `no_mains=False`
    - `prepass`（チームボード、12 ステップ）: トラッカー ON で 3 ゲーム記録した状態のセット読込の前処理を、`no_mains=False` と `True` の両方で行う。追従中のセットを選び直すと前処理は飛ぶ（0 サイクル）
  - **全ステップで、state・キー順・反映 delta・カード表示が移行前と一致した**。意図した差分は保存サイクル数と統計の発火数だけで、`S2D_CLEAR_CHANGES` に列挙した
    - 選手のクリア: カード数ぶん → 1（4 → 1）
    - Web のチーム名: 2 → 1
    - トラッカー ON で 3 ゲーム記録した状態のスコアのクリア: `ClearScore` 6 → 4、`ResetMatch` 1 → 4、Web `reset_match` 2 → 5、Web `clear_all` 7 → 9
      - スコアは 1 サイクルで書き、ステージ行はボードの held サイクルから 1 行 1 サイクルで追従する（`ResetScore` / +1 と同じ既存の形）
      - トラッカー OFF（既定）ではどれも 1 サイクル
      - ボードは held なので、途中のサイクルは OBS / Web に出ない（反映 delta は同じ）
    - 統計: クリアごとに、id が変わったチームの分を 1 回取得する。旧はカードごとで Recent 3・LastSets P1 1 / P2 2・`UpsetFactorCalculation` 3、新は各 1・`UpsetFactorCalculation` 0
  - 既存の golden（`golden.json`）の「clear players」は 4 → 1 サイクル（`S2D_CHANGES`）
  - 追加の 2 節も全ステップで state・キー順・反映 delta・カード表示が一致した。差分は `S2D_SINGLES_CHANGES` / `S2D_PREPASS_CHANGES`
    - 1v1: 選手のクリア 2 → 1、Web `clear_all` 5 → 4。スコアのクリアはチームボードと同じ（6 → 4 / 1 → 4 / 2 → 5）。統計はクリアごとに 1 回（旧は Recent 2・`UpsetFactorCalculation` 2）
    - 行のある状態のセット読込の前処理: 1 → 4。旧はセット読込の block の中で、スピンボックスがステージ行も消していた。今は行がスコアに held サイクルで追従する（`ResetMatch` と同じ形）
- **pin**
  - core（`test_actions.py`）
    - `ClearScore` / `ResetMatch` / `ClearPlayers` / `ClearBoard` が held 1 サイクル
    - 何も変わらないクリアは反映を点灯させない
    - スロットの空け方: テキスト・合成名・画像・id、名前の無いスロットの id は残る、地域だけのスロット、seed は常に int、空のキャラは publish し直さない
    - birthday・city・mains は残る
    - `no_mains` の 5 通り（parametrize）
    - アップセット係数は seed が変わったときだけ計算し直す
    - `ClearBoard` はチーム名とロゴを消し、カラーを残す
    - クルーは 4 操作とも `False`
  - ボード / Web（`test_scoreboard_operations.py`）
    - `CommandClearAll` / `ResetAllInputs` が 1 サイクルで、カードが追従する
    - `ResetAllInputs` はチーム名を消し、人数を残す
    - 4 入口の統計が 1 回（id あり）/ 0 回（id なし）
    - 4 入口とも、クリア後に同じセットを再読込できる（`get_set` が `"0"` に戻る）
    - Web の 5 経路が未構築ボードを構築し、`set_route` の書き込みは反映待ちになる
    - Web のチーム名は 1 サイクルで、同じ名前は書かない
    - city が残る（記録）
    - 既存の `lastSetSelected` テストは `setLoad` を読むように追従させた
  - `test_player_card_render.py`
    - 上記 golden とキー順の照合
    - 各ステップ後のカード表示 == state
    - 入力中のタグがクリアされる（5 入口。セット読込の前処理を含む。オフスクリーンで実際にフォーカスして打鍵する）
    - カード自身の Clear（`clear_bt` = `AutonaBasePlayerWidget.Clear`）と `ClearPlayers` が、埋まったスロットに同じ state を出す（`no_mains` 両方 × キャラ 1・2 行。キー順も）
    - Web のチーム名: 入力中の欄に Web が書く → 欄から外れる → Web の名前（とロゴ）が残る。Web の後に打った文字は勝つ
  - ボード / Web の補強: Web のチーム名が 1 サイクルでロゴも書く、Web の 5 経路のクリア後の state
  - mutation で確認したこと: 次のどれを外しても該当テストが落ちる
    - 追跡セットを忘れる処理
    - 統計の取得
    - `CommitFocusedEdit`
    - `no_mains` の 2 つの条件
    - 「seed が変わったときだけ」の条件
    - 「名前があったときだけ」の条件
- 残置 / リスク
  - トラッカー ON のスコアのクリアは、ステージ行の数だけ保存サイクルが増える（上記）。追従を 1 サイクルにまとめる変更は `ResetScore` / `AdjustScore` / `ApplyScores` にも効くので、S2d では行わない（S2e の候補）→ **S2e で実施**（追従は両チーム分で 1 サイクル。下記 S2e の判断 6）
  - トラッカー ON で `ResetAllInputs` を行うと、`DecrementScore` の警告ログ（`Failed to decrement score`）がチームごとに出る。S2d で新たに出るのはここだけ。同じ block の best_of 0 で行が消えた後に、スコア 0 への追従が held サイクルで走るため。書き込みは無く、state は同じ
    - Web `clear_all` では出ない。`ResetMatch` の block を抜けた時点のスコア 0 への追従が行を消し、best_of 0 はその後になるため
    - 行の無い状態でスコアが下がるときの同じ警告（セット追従の前処理、1v1 の Web `clear_all`）は、移行前の 2045c973 でも出る
  - クリアの前に入力中の欄を確定するので、カーソルが欄から外れる（S2c と同じ UX の変化）。Web からのクリアでも、デスクトップで入力中の欄が確定される
  - セット読込の前処理（`NewSetSelected` → `_ClearingPlayers` → `CommitFocusedEdit`）でも、そのボードの入力中の欄からフォーカスが外れる（UX の変化）。station / stream の自動追従では、追従先のセットが切り替わるたびに起きる（5 秒ごとのポーリングで同じセットのままなら起きない）
  - 編集可能コンボ（キャラ・国）に打ちかけの文字があると、クリア後もその文字が残る可能性がある（未検証）。コンボの打鍵はフォーカスアウトで確定せず、描画もフォーカス中の欄を飛ばすため。直していない。S2f でカードの `Clear` を廃止するときに解消する予定
  - 性能面の注意: `ClearPlayers` は全スロットの書き込みとアップセット係数の再計算の間、`StateManager.lock` を保持する（旧はカードごとの書き込みで短く取っていた）。他スレッド（Web・書き出し）の待ちが少し長くなりうる（実測なし）
  - カード自身の Clear（`clear_bt`・`SetData` 内の `Clear`）はウィジェット主導のまま（S2f）
  - Web の `get_set` が GUI スレッドでの同期実行になった（Web UI は呼んでいない。外部マクロ向けの legacy 経路）
- 実機確認（オーナー）
  - 1v1・チームの両形式で、選手情報のクリア（Web の reset_players）・すべての入力値をリセット・Web の全クリア / マッチリセット: 選手名・キャラ・国 / 地域・seed バッジ・アバター・スコア・LOSERS・match バーが空になり、反映後に OBS が空になる
  - すべての入力値をリセットではチーム名が消える。Web の全クリアでは、人数が 2 人以上だったときだけ（人数を 1 に戻す処理で）消える。どちらもチームカラーは残る
  - タグを打っている途中で各クリア: 打ちかけの文字も消え、フォーカスを外しても戻らない（選手欄だけ。match / チーム名 / スコア欄の打ちかけはフォーカスアウトで確定する = 旧実装どおり）
  - チーム名欄を打っている途中に Web からチーム名を変え、欄からフォーカスを外す: Web の名前（とロゴ）が残る。そのあと欄に打てば打った文字になる
  - Start.gg の station / stream を追従中に別の欄を打っていて、次のセットに切り替わる: 欄からフォーカスが外れる（前処理の確定。UX の変化として許容できるか）
  - Start.gg のセットを追従中にクリアしてから同じセットを選び直すと、もう一度読み込まれる
  - トラッカー ON で勝敗を付けてからマッチリセット: ステージボタンが全部未報告に戻り、スコアが 0-0
  - Start.gg の選手でクリアしたとき、統計が 1 回だけ取り直される

#### S2e best_of を state 主導へ（2026-10-24・PR #226）

セットの長さ（BEST OF）の正本を、ボードのスピンボックスから state にした。旧実装では、スピンボックスの `valueChanged` が 1 SaveBlock で `AutonaActions.SetBestOf`（6 キー）→ `individualGameTracker.SetStageCount` を呼んでいた。ほかの入口（セット読込の `ChangeSetData`、Web の `set_route` / `reset_match` / `clear_all`、v1 の `PATCH set`、ゲームのロード、すべての入力値をリセット）は、どれもそのスピンボックスを動かしていた。新しい `AutonaActions.SetBestOf(board, n)` は 6 キーとステージ行を 1 SaveBlock で書く。スピンボックスとステージ行は held サイクルから state を描画する（S2a と同じ形）。

- **`AutonaActions` の操作**（board 1/2。board 3 は `False` を返す。クルーの best_of は今もクルーウィジェット → `CrewBattleService`）
  - `SetBestOf(board, value)`
    - 0〜99 に丸める（`BEST_OF_MIN` / `BEST_OF_MAX` = スピンボックスの範囲。旧実装でも Web の 150 はスピンボックスで 99 になっていた）。`"7"` は 7
    - int にできない値（`"seven"`・None・dict など）は警告を出して何も書かず `False` を返す（0 として読むと best_of をクリアしてしまう。`SetLoadService` の「警告して捨てる」に合わせた。レビュー指摘）
    - 値が変わらないときは何も書かず `False` を返す。旧 `valueChanged` も変化時だけ発火していたので、同じ値でステージ行がリセットされないのは旧挙動と同じ
    - 6 キーは旧と同じ順に `SetIfChanged` で書く。そのあと同じ block で、登録されたトラッカーの `SetStageCount(ResolveStageCount(value))` を呼ぶ
    - 文言の整形（注入された `formatMatchText`）が例外を出したら、警告を出して英語既定の文言（`DEFAULT_MATCH_TEXTS`）で書く（`_matchText`。レビュー指摘）。行の作り直しと他のキーはそのまま書くので、例外はどの入口にも伝わらない
  - `RepublishBestOf(board)`: ゲームのロード（`_onGameAssetsLoaded`）用。旧実装はここで `valueChanged` を再発火し、新しいゲーム・ロケールで文言を作り直したうえで、同じ行数でステージ行をリセットしていた。それを core で行う
  - `RegisterGameTracker(board, tracker)` / `UnregisterGameTracker(board, tracker)`
    - ボードは構築時（トラッカーのサービスを作った直後）に登録する
    - 外すのはボードの `destroyed` から（lambda は番号とサービスだけを捕まえ、ボード自身は捕まえない）。外すのは、登録がまだそのサービスのときだけ（作り直したボードの新しい登録は残す）
    - 未構築ボードへの `SetBestOf` はキーだけを書く。行はボードの構築が作る（構築時に `SetBestOf(n, 0)` → `_SyncBestOf()`。旧は `valueChanged.emit(0)`）
  - `ClearBoard` は最後に、同じ block で `SetBestOf(board, 0)` を行う。`ResetAllInputs` の「`ClearBoard` と best_of 0 を 1 SaveBlock」（S2d）はこれで満たす。`ResetMatch` は best_of を含まない（Web の 2 経路は、旧実装どおり自分のサイクルで best_of を 0 にする）
- **`GameTrackerService.rowsVersion`**: `SetStageCount` を呼ぶたびに（同じ行数でも）1 増える。ボードの `AutonaIndividualGameTracker.RenderStageRows()` は、版か行数が変わったときだけボタンを作り直す。何も書かない（旧 `SetStageCount` はウィジェットからサービスを書いていた）
- **ボード**
  - `_SyncBestOf()`: held サイクルで `_SyncSetFields` の後、`_SyncScoreDisplays` の前に呼ぶ
    - セット読込は best_of とスコアを 1 サイクルで書く。行を先に描画しておくと、スコアの変化を新しい行に追従させられる（旧実装でスピンボックスが書き込みの中で行を作り直していたのと同じ順）
    - スピンボックスは `QSignalBlocker` で描画する。オペレーターが入力中なら飛ばして、そのとき表示していた値を記録し（`_heldBackBestOf`）、`editingFinished` で `QTimer.singleShot(0)` から描画し直す（`_SyncSetFields` のフォーカス規則と同じ。Web の後にオペレーターが打った値は、`valueChanged` が先に書くので打った値が勝つ）
    - Return は Qt が今の値のまま `valueChanged` を出す（`AlwaysEmit`）。記録した値と同じ `valueChanged` は書かない（チーム名の `_OnTeamNameFinished` と同じ規則）。書くと、オペレーターが変えていない値が Web の新しい値を上書きする（レビューの追加テストで発見）。Return の後もフォーカスは残るので、保持は続く
    - ステージ行は、フォーカスに関係なく state に従う
  - `_onBestOfChanged` は `AutonaActions.SetBestOf` を呼ぶだけになった。ボードは `SetStageCount` を呼ばない
- **入口**
  - セット読込（`ChangeSetData`）: `SetLoadService.BestOfFromSetData(data)` → `SetBestOf`
    - 旧の `if data.get("bestOf")` をそのまま残した。0・空・None は運ばない
    - 数値の文字列（`"7"`）は数値にする。数値でない値は警告を出してそのキーだけを捨てる（`ApplyScores` と同じ規則。旧はスピンボックスの `setValue` が `TypeError` を出し、セット読込の残り（losers・選手）を捨てていた。golden の対象外）
  - Web `set_route` の bestOf: `_apply_set_fields({'bestOf': int(bestOf)})`。phase / pool / match と同じ経路（`ApplyMatchInfo` → `BestOfFromSetData` → `SetBestOf` → `ApplyLosers` を 1 SaveBlock）。英語のラウンド名が空になるのは旧 `ChangeSetData` と同じ。v1 の `PATCH set` もここを通る
  - Web `reset_match` / `clear_all` の `_reset_best_of`: board 1/2 は `SetBestOf(n, 0)`。クルーはスピンボックスのまま
  - 未構築ボードは、S2c / S2d と同じ理由で `EnsureScoreboard` で構築してから書く
- **判断**
  1. **行の作り直しは操作の block の中で行う**（通知ハンドラの中で書かない）。旧ルール（state-and-webserver.md）が state 先行にしなかった理由は「行の作り直し（= state の書き込み）が通知ハンドラに入り、再入と 2 サイクルになる」だった。トラッカーを `AutonaActions` に登録し、操作がそのトラッカーを呼ぶことで解消した。ウィジェットは描画だけを行う。1 サイクルは維持した
  2. **トラッカーは登録制**（操作のたびに `GameTrackerService` を作らない）。`writingScore` のガードと行の版はボードが読むインスタンスの状態なので、別インスタンスで書くとボードに伝わらない
  3. **変化時だけ書き、ゲームのロードは `RepublishBestOf`**: 旧 `valueChanged` の発火条件（変化時だけ）と、onLoad の再発火（必ず作り直す）の 2 つを、そのまま 2 つの操作に分けた
  4. **丸めは core**: スピンボックスのない入口（core を直接呼ぶ側）でも、同じ値になる
  5. **`ClearBoard` に best_of を含めた**: `ClearBoard` は `ResetAllInputs` 専用（S2d 判断 3）。旧は外側の SaveBlock で ClearBoard と best_of 0 をまとめていた
  6. **スコアへの行の追従を 1 サイクルにした**（S2d の残置）。`_SyncScoreDisplays` は、両チームの `SyncStagesToScore` を 1 つの SaveBlock の中で呼ぶ
     - 安全だと判断した理由:
       - 追従はもともと held ハンドラの中で行ごとに SaveBlock を作って書いていた。外側の block はそれを 1 回にまとめるだけ
       - block を抜けたときの通知は、旧の行ごとの通知と同じく `_syncingScore` のガードで止まる
       - 何も追従しなければ書かないので、サイクルは増えない（トラッカー OFF・+1 は不変）
       - held なので途中のサイクルは OBS / Web に出ない。held 通知の購読者はボードと統計パネルだけ
     - 効く操作: `ClearScore` / `ResetMatch` / `ResetScore`（3 ゲーム記録済み）、`ApplyScores`（セット読込のスコア）、`AdjustScore` の +2。スコアの書き込み 1 + 行の追従 1 = 2 サイクルになる
     - 行もスコアの操作の block で書く「1 サイクル」は行わなかった。スコアから行への追従を core のスコア操作（`AdjustScore` / `ResetScore` / `ClearScore` / `ApplyScores` / `SwapTeams`）へ移す必要があり、2 つの書き手の協調（`writingScore`・スワップの `followStages=False`・スコア欄の手入力の経路）の作り直しになるため。スコア欄の手入力を core へ移すときの候補
  7. **存在しない行への書き込みは core が断る**（レビュー指摘）。別スレッド（Web / API）の `SetBestOf` は行をすぐ作り直すが、ボタンの作り直しは held サイクルが GUI スレッドに届いてから。その間に消えた行のボタンを押すと、旧ガードなしでは `stages.5` が書き戻され、行の並びが崩れていた
     - `GameTrackerService.SetStageResult` / `SetStageAsset` は `StateManager.lock` の中で `0 <= index < StageCount()` を確かめ、範囲外なら警告を出して `False` を返す。「行数を決めるのは `SetStageCount` だけ」という不変条件を core が守る
     - ウィジェットの `_StageResultClicked` は、断られたクリックからスコアを集計しない（集計すると、作り直した行からスコアが 0 に戻る）
- **golden**
  - `best_of_golden.json` を移行前の 79da38bf で記録した。節は 3 つ
    - `operations`（チームボード、43 ステップ）/ `singles`（1v1、43 ステップ）
      - スピンボックスでの変更（同じ値・0・範囲外の 150 を含む）
      - トラッカー ON / OFF
      - ステージ結果を記録した状態での行数の変更
      - `ResetScore`、スコア付きのセット読込
      - Web `set_route` の bestOf（数値・文字列・0・範囲外）、v1 `PATCH set`（bestOf 単独・phase / match 付き・0）
      - bestOf 付き・同じ bestOf・bestOf 0 のセット読込、ゲームのロード
      - Web `reset_match` / `clear_all`、`ResetAllInputs` を 2 回、best_of の無いゲームのロード
    - `unbuilt`（2 ステップ）: 未構築のチームボードへの Web `set_route` bestOf と、未構築の 1v1 ボードへの v1 `PATCH set`。構築中と反映時の公開内容も記録した
    - `key_order.json` に「best of operations」/「singles best of operations」/「unbuilt best of operations」の 3 節を追加した（既存の節は不変）
    - 移行前のツリーで録画をそのまま再生し、一致することを確かめてから移行した
  - 各ステップの後に照合したこと: スピンボックスの値 == `best_of`、行数 == `stages` の数、各行のボタン == state の勝敗、行が無いときトラッカーが隠れる
  - **best_of の移行そのものは、全ステップで意図した差分ゼロ**（state・キー順・保存サイクル・反映 delta・統計の発火数・表示）。3 節とも同じ
  - 判断 6 による差分は保存サイクル数だけ（state・キー順・反映 delta は同じ）。テストでは S2d の差分に重ねて書いた（`_layered`）
    - `clear_golden.json`（`S2E_CLEAR_CHANGES`）
      - スコアのクリア: `ClearScore` 4 → 2、`ResetMatch` 4 → 2
      - Web: `reset_match` 5 → 3、`clear_all` 9 → 7（S2d 前の 7 に戻った）
      - スコア付きのセット読込: 4 → 2
      - +2 を含む準備ステップ: 6 → 5 / 12 → 11 / 20 → 19
    - 1v1（`S2E_SINGLES_CLEAR_CHANGES`）: 同じ差分。ただし Web `clear_all` は不変（4。行が無い状態のため）。+2 を含む準備ステップは 6 → 5 / 12 → 11
    - セット読込の前処理（`S2E_PREPASS_CHANGES`）: 4 → 2（2 通りとも）
    - `best_of_golden.json`（`S2E_CHANGES`。1v1 も同じ）: トラッカー ON の `ResetScore` 4 → 2、スコア付きのセット読込 4 → 2、Web `reset_match` 5 → 3、Web `clear_all` 8 → 6
    - `golden.json` / `swap_golden.json` は差分なし（+1 は行 1 つなので変わらない）
- **pin**
  - core（`test_actions.py`）
    - 同じ best_of は書き込みにならない
    - 丸め（150 → 99、-3 → 0、`"7"` → 7）
    - int にできない値（`"seven"`・None・`""`・dict・list）は何も書かない
    - 整形が例外を出す（空の表・`.format()` が通らない独自用語）と英語既定で書き、行も作り直す。`saveBlocked` は 0 に戻る
    - クルーは `False`
    - 登録された行が同じサイクルで作り直され、キー順も旧と同じ
    - トラッカー OFF では行が `{}`
    - 同じ best_of では記録済みのゲームが残る
    - `RepublishBestOf` は行を作り直し（版 +1、1 サイクル）、文言を作り直す
    - 未構築ボードはキーだけ
    - 登録の解除は新しいトラッカーを残す
    - `ClearBoard` は best_of 0 も行う
  - `test_game_tracker_service.py`
    - 作り直しのたびに版が進む（同じ行数でも）
    - 存在しない行（5・7・-1、縮めた後の旧最終行）への結果・ステージは書かない（判断 7）
  - `test_set_load_service.py`: `BestOfFromSetData`（数値・`"7"`・空白付き・負・範囲外はそのまま、0 / None / 空 / キーなし / dict でない入力は None、数値でない値は警告して None）
  - ボード（`test_scoreboard_operations.py`）
    - core の `SetBestOf` が 1 サイクルでスピンボックスと行に届く（別スレッドからも）
    - ゲームのロードで、同じ行数のまま行が作り直される
    - 破棄したボードは登録を外す
    - 3 ゲーム記録済みの `ResetScore` と +2 は 2 サイクル（判断 6）
    - 別スレッドの `SetBestOf` の後、作り直し前の古い行のボタンを押す: 行は増えず、スコアも動かない（判断 7）
    - `matchNames` が空でも、セット読込（losers と選手が入る）・`set_route` の bestOf（`"OK"`）・ゲームのロード（`CommitHeld` される）が最後まで進む
    - 数値でない bestOf のセット読込でも、losers と選手が入る
  - `test_player_card_render.py`
    - 上記 golden とキー順の照合
    - 入力中のスピンボックスに Web が書く: 欄から外れると Web の値が出る（オフスクリーンで実際にフォーカスして打鍵する）
    - 保持中に Return で確定する: フォーカスが残るので保持が続き、表示中の値は Web の値の上に書き戻されない。欄から外れると Web の値が出る
  - `test_save_block_symmetry.py`
    - SaveBlock の検査対象を `AutonaActions.SetBestOf` / `RepublishBestOf` に移した
    - 整形の失敗は例外にならなくなったので、block の中で実際に例外を出す検査（行の作り直しが例外を出す）を足した。例外は呼び出し側に届き、`saveBlocked` は 0 に戻る
- **テスト追従**
  - `test_clear_board_on_a_clear_board_writes_nothing`: 準備で best_of を 0 にした（合成ボードには best_of が無く、`ClearBoard` が 0 を書くため）
  - `test_clear_board_takes_the_team_names_too_in_one_cycle`: best_of 0 も確認するようにした
  - `test_a_tracker_row_is_rebound_to_the_new_stage_model`: ウィジェットの `SetStageCount` を削除したので、core の `SetBestOf` で行を作るようにした
- **S2 の完了条件**（判断 1 で絞った「swap・clear・best_of・set_route の該当箇所で `GetScoreboard(` ゼロ」）
  - clear（`reset_match` / `reset_players` / `clear_all` / `get_set`）・best_of（`_reset_best_of`）・`set_route` の phase / pool / match / losers / teamName / bestOf: **ゼロ**（`EnsureScoreboard` で構築して core を呼ぶ）
  - swap: `swap_teams` は `EnsureScoreboard` にした（`@gui_thread_sync` の中なので `GetScoreboard` と同じ動き。書き込みは `board.SwapTeams()` → `CommitFocusedEdit` → `AutonaActions.SwapTeams` で core）
    - **`get_swap` は `GetScoreboard` のまま残す**。理由: GUI スレッド外（Flask）から呼ばれ、`GetScoreboard` が構築を GUI スレッドへ渡す正しい形になっている。`EnsureScoreboard` は GUI スレッド専用なので、置き換えるには `@gui_thread_sync` を足すことになり、やっているのは state の読み取りだけなので、経路が変わるだけになる
  - board 1/2 で残る `GetScoreboard(`（26 箇所。コメント 2 行を除く）
    - 選手データ: `post_score` / `set_team_data` / `update_player` / `load_player_from_tag` → S2f
    - 人数・キャラ数: `set_route` の `players` / `characters` → S2g
    - stats: `stats_*` / `refresh_stats` の 11 箇所
    - ウィンドウ操作: `open_sets` / `pull_stream_set`
    - サムネイル: `get_thumbnail`
    - provider 取得: `load_set`
    - 構築して読むだけ: `get_scoreboard`（構築でサブツリー全体が書かれるため）
    - `get_swap`（上記）
    - board 3 だけの経路（条件の対象外）: `crew_member_stock` / `crew_set_active` / `reset_scores` の fallback
- 残置 / リスク
  - 登録の解除はボードの `destroyed` に頼る。届かずにボードが消えると、`SetBestOf` が state の行を作り直し続ける（そのボードは未構築に戻っており、行は次の構築の `SeedBoard` で消える）。作り直したボードは新しい登録で上書きする
  - 登録は `AutonaActions.instance` の中にあり、プロセス全体で共有される（テストではボードの破棄で外れることを pin した）
  - セット読込の数値でない bestOf は、例外ではなく警告になり、セット読込の残りが適用されるようになった（上記。旧の例外はバグとして扱った）
  - 文言の整形（`best_of_text` の注入関数）が例外を出すと、警告を出して英語既定の文言で書く。その間ずっと英語の BO 表記になる（ロケールの表が読み込まれた後の次のゲームのロード / best_of の変更で直る）
    - 旧はスピンボックスのスロット内の例外を PySide6 が握りつぶしていたので、呼び出し側は先へ進めていた。ただし 6 キーは途中で止まり、行も作り直されなかった
    - core 化した直後（レビュー前）は例外がそのまま上がり、セット読込の残り（losers・選手）・`set_route` の応答・ゲームのロードの `CommitHeld` が止まっていた。フォールバックで解消した
  - 性能の注意: 行の作り直し（`SetStageCount`）が `StateManager.lock` を持ったまま走るようになった（`SetBestOf` が lock の中で `_PublishBestOf` を呼ぶ）。スピンボックスからなら GUI スレッドで走るので、その間は Web・書き出しのスレッドが待つ（行は最大 99 × 3 キー。実測なし）
  - 既存リスクの頻度: `_SyncScoreDisplays` の SaveBlock（判断 6）で、held 通知のたびに SaveBlock が 1 つ増える。`saveBlocked` の増減はスレッド間で atomic でない（既存の問題）ので、競合の機会がその分増える（何も書かないときは SaveState が早期 return する）
  - クルーボード（board 3）の best_of はスピンボックス主導のまま
  - トラッカー ON の `ResetAllInputs` で出る `DecrementScore` の警告（S2d の残置）は変わらない
- 実機確認（オーナー）
  - BEST OF のスピンボックスを 3 → 5 → 0: トラッカー ON でステージ行が 3 → 5 → 消える。OFF では行が出ず、BO 表記だけが変わる
  - トラッカー ON で勝敗を付けてから BEST OF を変える: 行が新しい数で全部未報告に戻る。同じ値を打ち直しても、記録は消えない
  - Web / v1 から bestOf を変える: デスクトップのスピンボックスと行が追従し、反映後に OBS の BO 表記が変わる。スピンボックスを入力中に Web から変えると、欄から外れたときに Web の値が出る
  - ゲームを切り替える / 言語を変える: BO 表記がその言語になり、ステージ行は同じ数で未報告に戻る
  - Web のマッチリセット・全クリア・すべての入力値をリセット: BEST OF が 0 になり、行が消える
  - Start.gg のセットを読み込む（bestOf 付き）: BEST OF と行がセットの値になり、スコアの分だけ行に勝ちが付く
  - トラッカー ON で 2-1 からマッチリセット: 行が全部未報告に戻り、スコアが 0-0（判断 6 の対象）
  - クルーボードの BEST OF を Web から変える: 従来どおり変わる

### S3 ヘッドレス起動・永続化分離・LAN トークン認証 — 設計（2026-10-25・オーナー承認済み）

オーナー判断（2026-10-25）: LAN トークン認証は S3 と同時に入れる（§4.6-3 の推奨案）。下記の設計と完了条件の改定を承認。S3a〜S3d は自走で進め、S3e の前で止める。

#### 棚卸しで分かったこと（前提）

- **§4.4 の S3 完了条件「`WebServerActions` 廃止」は、S3 だけでは届かない**。50 メソッドのうち core 直結は 15 本。残り 26 本は、選手データ（S2f）、人数・キャラ数（S2g）、統計のトリガー、セット読込と station / stream の追従ポーリングがボードのウィジェットに残っていることに依存している。ヘッドレスではボードが無いので、これらは何も起きない
- **「QApplication なし」は QCoreApplication + イベントループで満たすのが現実解**。Bridge の delta 配送、`run_sync`、Worker の結果受け渡しはすべてイベントループ前提で、ループが無いと `run_sync` が永久に待つ。完全に Qt なしにするには Bridge・Worker・アセットアダプタ・PlayerDB・Provider の置き換えが要り、§4.3 の Executor 統一と同じ作業になる（S3 の外）
- **起動経路**: `src/Autona.py` の import が QApplication を作る。`bootstrap_core()` が QDockWidget（`AutonaScoreboardManager`）を作る。大会データの適用、`GameChanged` → Socket.IO 配信、URL・ゲームの復元は Window が配線している
- **セキュリティの既存問題**: 静的配信ルート（`/<path:filename>` と static_folder=`.`）が CWD 以下の全ファイルを返し、CORS は全許可。`user_data/settings.json`（Figma ペアリングコードを含む）や `local_players.json` が、LAN 公開時は LAN 内の誰からでも読める。ループバックでも、ブラウザで開いた任意のサイトから読める可能性がある。さらに legacy は GET で書き込め、v1 は `force=True` で text/plain の本文も受けるので、ブラウザ経由の CSRF が成立する
- **永続化の現状**: 起動時に `out/program_state.json` の全量を読み戻す。そのうえで、`score` は `ResetScoreRoot` が、`game` は `ResetGameState` が消し、`SeedPanelState` が正規化する。マイグレーション層は無い（実質的な移行は `RETIRED_ROOTS` / `STALE_KEYS` / `NormalizeState` の 3 つ）。`out/program_state.json` の場所・形式・`timestamp` / `revision` は、layout の file:// ポーリングと ASS の契約に縛られている
- **自動更新**: ルートは PURGE せずに上書きするので、git 管理外の `user_data/` は更新後も残る。ヘルパーには手を入れずに済む

#### 方針

S3 を 4 つの PR に分ける（各 PR は独立に revert 可能）。順序は、既存の穴を先に塞ぐ → 永続化 → ヘッドレス → E2E。

**S3a Web の公開面を締める（静的配信の許可リスト + LAN トークン認証 + ブラウザ CSRF 対策）**

- 静的配信を許可リスト化する
  - 返すもの: `layout/`、`assets/`、`web/build/`、`out/`、`user_data/` の画像系ディレクトリ（`games/`、`team_logo/`、`sponsor_logo/`、`player_avatar/`、`additional_flag/`、`tournament_banner.png`）
  - `user_data/*.json` などの設定・選手 DB は 404 にする
  - 許可リスト外の要求はログに出す（既存のカスタムレイアウトが参照していたら気づけるように）
- トークン
  - `secrets.token_urlsafe(24)` を `user_data/settings.json`（git 管理外。S3a 以降は HTTP から読めない）の `general.webserver_token` に保存する。初回起動時に生成する
  - API 設定ウィンドウに、表示・コピー・再発行を置く（LAN 無認証の赤い警告をここで差し替える）
- 判定（どの要求にトークンを求めるか）

  | 接続元 | 読み取り（静的配信・state・カタログ・Socket.IO の受信） | 書き込み（legacy の書き込み・v1 の POST/PATCH・Socket.IO の書き込みイベント）と選手 DB（`playerdb`） |
  |---|---|---|
  | ループバック | 不要（今と同じ） | 不要（今と同じ）。ただしブラウザからのクロスサイト要求は拒否（下記） |
  | LAN | 不要 | **トークン必須** |

  - 理由
    - 既存の OBS の URL（外部契約 §2.8）を壊さないため、ループバックと読み取りは免除する。2 PC 配信で、配信 PC の OBS が LAN 越しにレイアウトを読む構成も、そのまま動く
    - 書き込みと選手 DB だけを守れば、LAN 内の第三者による操作と個人データの読み出しは止まる
  - トークンの渡し方: `Authorization: Bearer <token>`、`X-Autona-Token` ヘッダ、`?token=` クエリのいずれでもよい。クエリを許すのは、legacy の GET URL を貼る Stream Deck と、Web UI の初回オープンのため
  - Socket.IO は接続時の `auth` でトークンを受け、接続ごとに「書き込み可」を持つ。書き込み可でない接続の書き込みイベントは拒否する
- ブラウザ経由の CSRF 対策（ループバック含む全接続）
  - 書き込みで `Sec-Fetch-Site: cross-site` の要求は拒否する
  - `Origin` が付いていて自分のオリジン（`http://127.0.0.1:{port}` / `localhost` / LAN IP）でない書き込みも拒否する
  - Stream Deck・Companion・curl・`requests` はこれらのヘッダを付けないので影響しない。OBS のブラウザソースは同一オリジンか file://（書き込まない）
- クライアントの追従
  - Web UI: `api.ts` と `websocketConnection.ts` の 2 箇所。初回の `?token=` を sessionStorage に保存してヘッダと `auth` で送る
  - 「ブラウザ版を開く」の URL にトークンを載せる
  - Companion ガイドと API 設定に、ヘッダを追加する手順を書く
  - アプリ内 API Tester はループバックなので変更不要
- Designer のトークン（ループバック限定・ウィンドウを閉じると失効）とペアリングコードは別の仕組みとして残す（用途が違う）
- **外部から見える変化**: LAN 越しの Companion / Stream Deck / ASS の書き込みにトークンが必要になる。リリースノートで告知し、API 設定にコピー手順を置く。無効化スイッチは作らない（LAN 公開をやめればループバックで今と同じに使える）

**S3b 永続化の分離（`user_data/state.json` + `state_version` + 移行関数 1 本目）**

- 正本を `user_data/state.json` にする
  - 中身は公開済みの state（held を巻き戻したもの = 今 `program_state.json` に書いている内容）から `timestamp` / `revision` を除き、`state_version` を足したもの
  - 書き込みはアトミック（SettingsManager と同じ mkstemp → os.replace）
  - 書き出しスレッドで、`out/` とは独立に間引いて書く（例: 最後の変更から 1 秒）。`disable_export` の影響を受けない。終了時は書き切る
- 起動時の読み込み
  - `state.json` があればそれを読む。無ければ `out/program_state.json` から移行する（`timestamp` / `revision` を落とす）
  - 起動時の `score` / `game` の消去と `SeedPanelState` の正規化は今と同じ（引き継ぐ範囲は変えない）
- `out/program_state.json` は、今と同じ場所・形式・書き込み順で書き続ける（外部契約）。export のロジックはモジュールを分けるだけで変えない
- `state_version` = 1。移行関数 1 本目（v0 → v1）は、既存の 3 つ（`RETIRED_ROOTS` / `STALE_KEYS` / commentary の `NormalizeState`）を取り込む
  - 前方一方向のみ。今より新しい版の `state.json` を読んだら、`state.json.v{N}.<日時>.bak` に退避して `out/program_state.json` から移行し、警告を出す（ダウングレードした場合）
    - 当初案は「空から始め」だった。S3b の実装時に移行に改めた（2026-10-25・採用）。理由: 運用者にとっては大会情報が消えない方が有利で、`program_state.json` は直前まで動いていた新しい版が書いたものなので、空より実際の状態に近い。移行関数は冪等なので、新しい形のデータに掛けても壊さない
  - 旧版に戻したユーザーは、旧版が `out/program_state.json` を読むので困らない
- `.gitignore` に `user_data/state.json` を足す（git 管理下に入ると、更新のたびに上書きされる）。zip 検証にも、配布物に含まれないことを足す
- S-14（スキーマ版の不在）はこれで解消扱いにする

**S3c ヘッドレス起動（`main.py --headless`）**

- QCoreApplication + `exec()` で動かす。QtGui（QIcon / QStandardItemModel / QTranslator）は使わない
  - Bridge・GuiExecutor・Worker・QThread・QTimer はそのまま動く
  - 単一インスタンスの IPC（QLocalServer）もそのまま使い、GUI 版とヘッドレス版を同時に起動させない（ポートと書き込み先が同じため）
- 組み立てを分ける
  - `bootstrap_core()` から `AutonaScoreboardManager` の生成を GUI 側へ出す
  - ヘッドレスの組み立て（`src/headless.py`）が担うもの
    - `ResetScoreRoot` + `SeedBoard(1..3)` + `SeedPanelState`
    - `CountryCatalog.Load()` の直接呼び出し
    - `LoadRoundNames`
    - 大会データの適用と `GameChanged` → Socket.IO の配線（Window から移す）
    - URL とゲームの復元
    - Web サーバーの起動
    - Ctrl+C / コンソールの閉じるボタン / SIGTERM での停止
  - ゲームのロードは、アダプタ（Qt モデル）を通さず `GameAssetCatalog` を直接使う
  - `in_gui_thread()` は QCoreApplication を見るように直す
- Web サーバーを `threading.Thread` に置き換える。失敗通知はコールバックにする。LAN 公開とポート衝突の確認は、ヘッドレスでは設定値で決めて、ログに出すだけにする
- **ヘッドレスで動く範囲（S3 の完了条件）**
  - OBS オーバーレイと `/web` の表示
  - core 直結の操作（スコア、反映、カラー、表示トグル、swap、クリア、best_of、match バー、チーム名、losers、大会情報、ゲームの切り替え）
  - ボードが無いので held にはならず、書き込みは即時公開される（ヘッドレスには反映ボタンが無いので、これが正しい）
  - ボードが要る操作（選手の編集・セット読込・追従・統計・人数の変更・Top 8 の取り込み・実況のパネル経由の更新）は、ヘッドレスでは 503（`not available in headless`）を返す
- `WebServerActions` を縮める
  - core 直結の 15 本と、S2 で core 化した操作は、v1 / legacy のルートから core を直接呼ぶ形に移す。GUI でもヘッドレスでも同じ経路を通る
  - 残る 26 本は「ボード依存」として台帳に列挙する
- 廃止は S3e（S2f / S2g / 統計・セット読込・ポーリングの core 化の後）へ送る（**§4.4 の完了条件を変える**）
- `pull_stream_set` は、どこにも接続されておらず常に空振りしている（v1 `:pull-stream` も同じ）。S3c で直すか外すかを判断する（推奨: 次のセットを引く core 操作として直す。ただし追従の core 化と同時なので S3e）

**S3d E2E**

- まず Python のスモークテストを 1 本作る。ヘッドレスを subprocess で起動し、health、Socket.IO 接続、`score:adjust`、delta の受信、トークンなしの LAN 書き込みの拒否、`state.json` の書き出しを確かめる。現状の API テストは GUI スレッドで inline 実行されるので、`run_sync` が止まる問題を検出できない。その防波堤にする
- Playwright（`web/` の devDependency）で `/web` と、オーバーレイ 1 枚の表示を確かめる。CI への組み込みは S3d の中で決める

#### §4.4 の S3 完了条件の改定（承認済み）

- 旧: ヘッドレスで OBS オーバーレイと `/web` が動く（Playwright E2E）。`@gui_thread_sync` の残りは thumbnail / result とウィンドウ操作のみ。`WebServerActions` 廃止
- 新: ヘッドレスで OBS オーバーレイと `/web` の表示、core 直結の操作が動く（スモーク + Playwright）。LAN の書き込みはトークン必須。正本は `user_data/state.json`（`state_version` 付き）
  - `WebServerActions` 廃止と「`@gui_thread_sync` の残りは thumbnail / result / ウィンドウ操作のみ」は S3e へ移す

#### 外部契約への影響

- 変わらないもの: `out/program_state.json` の場所・形式・書き込み順、Socket.IO のイベント名と payload、v1 の形（追加のみ）、settings.json の形式（キーの追加のみ）、自動更新（ヘルパー・zip 構造には触らない）
- 変わるもの: LAN 越しの書き込みにトークンが要る（S3a。オーナー判断済み）。CWD 以下の任意ファイルの配信を止める（S3a。許可リスト外を参照するカスタムレイアウトがあれば、壊れてログに出る）

#### S3a Web の公開面を締める（2026-10-25・PR #227）

Web サーバーの公開面を 3 つ締めた。1 つ目は静的配信で、許可リストに載ったファイルだけを返す。2 つ目は LAN の他端末で、書き込みと選手 DB にトークンを要求する。3 つ目はブラウザ経由の CSRF と DNS rebinding で、トークンを持たない書き込みは「この PC から・このサーバーの名前宛て・自分のページから」のときだけ受ける。どの要求に何を求めるかは、新しい core モジュール `src/core/WebAccess.py` が決める（Qt なし・Flask なし・import 副作用なし）。サーバーはそこに要求の情報を渡して答えを聞くだけにした。

- **実装**
  - `src/core/WebAccess.py`（新規）
    - 分類表
      - `ROUTE_ACCESS`: Flask の全 endpoint（legacy 43・designer 9・入口 / ファイル 2・v1 31）を `READ` / `WRITE` / `PLAYERDB` / `DESIGNER` / `STATIC` に分ける
      - `EVENT_ACCESS`: Socket.IO の全イベント（41）を同じクラスに分ける
      - 表に無い名前は `RouteAccess` / `EventAccess` が `WRITE` として返す。分類し忘れても、開く側ではなく守る側に倒れる
      - 判定の対象（`GUARDED`）は `WRITE` と `PLAYERDB`
    - 静的配信: `StaticPath(filename)` が、配信してよいパスを正規化して返す（配信しないものは None）
      - 許可するのは `STATIC_DIRECTORIES`（`layout/`・`assets/`・`web/build/`・`out/`・`user_data/{games,team_logo,sponsor_logo,player_avatar,additional_flag}/`）と `STATIC_FILES`（`user_data/tournament_banner.png`）
      - `\` は区切りとして読む
      - NUL・`:`（ドライブ名・NTFS ストリーム）・先頭の `/`・`..` の区間は、比較の前に拒否する
      - 大文字小文字は区別しない（Windows では `/User_Data/...` でも同じファイルが返るため）
    - トークン
      - `Token()`: 初回の使用時に settings.json から読み、以後はメモリに持つ。変えるのは `RegenerateToken()` だけ
      - 発行するのは、settings.json を読めたうえで `general.webserver_token` が本当に無いときだけ（初回起動・S3a 前からの更新）。`secrets.token_urlsafe(24)` を作って保存し、info ログを出す
      - 次の 2 つの場合は発行せず、使えるトークン無し（`""`）として扱う。警告を出し、LAN からの書き込みは再発行するまで全部拒否する
        - settings.json はあるが読めなかった（`SettingsManager.loadFailed`）。空に見えるだけで、利用者の機器が持っているトークンはその中にある
        - 保存値が `[A-Za-z0-9_-]{16,256}` に合わない
      - `RegenerateToken()`: 作り直す。旧トークンはその場で使えなくなる。`TokenGeneration()` は発行のたびに 1 つ進む
      - 受け取る場所は `Authorization: Bearer` / `X-Autona-Token` / `?token=` / Socket.IO の `auth.token`（`SuppliedTokens`）
      - 比較は bytes の `hmac.compare_digest` で行う。512 文字を超える値は比較しない
    - 判定: `Authorize(remoteAddr, tokens, origin, fetchSite, host, ownOrigins, localAddresses, forwarded)`
      - まず「トークンなしで通れるか」（`_AmbientRefusal`）を上から順に見る。どれかに当たったら拒否の理由とし、当たらなければ `GRANT_AMBIENT`
        1. 転送ヘッダ（`X-Forwarded-For` / `Forwarded` / `X-Real-IP`）がある、または接続元が「この PC」（`IsThisComputer`: ループバック、またはこの PC の LAN アドレスのどれか）でない → 401 `UNAUTHENTICATED`（`forwarded` / `token_required`）
        2. `Host` がこのサーバーの名前（`http://<Host>` が `OwnOrigins` に入る = ループバック名 3 つかこの PC の LAN アドレスで、待ち受けポート）でない → 401（`foreign_host`）
        3. `Sec-Fetch-Site` が付いていて `same-origin` / `none` 以外 → 403 `FORBIDDEN`（`cross_site` / `same_site` / `fetch_site`）
        4. `Origin` が付いていて自分のオリジンでない（`null` を含む） → 403（`foreign_origin`）
      - トークンなしで通れなければ、トークンが一致すれば `GRANT_TOKEN`。一致しなければ、上の拒否を返す（401 のときは `invalid_token` にする）
      - 拒否の本文は v1 のエラーエンベロープ（`ErrorBody`）
      - `NormalizeOrigin` は `scheme://host[:port]` だけを受ける。userinfo・パス・クエリ・フラグメントが付いていれば None（`evil@127.0.0.1:5107` を自分と読まない）
    - `RefusalLog`: 拒否の種類ごとに、最初の 1 回だけ warning を出す（以降は debug。上限 256 種）
  - `src/Helpers/AutonaNetworkHelper.py`: `GetLocalIPs()` を追加した。この PC の IPv4 アドレスを全部返す（`GetLocalIP()` の外向き 1 つと、ホスト名が引けるもの。別 NIC・VPN）
  - `src/core/SettingsManager.py`: `loadFailed` を追加した。settings.json を読めなかったとき True になる（読み込みの挙動は変えていない）
  - `src/AutonaWebServer.py`
    - HTTP
      - `Flask(static_folder=None)`。旧 `static_folder='.'` は `/./` の下で CWD 以下の全ファイルを返していた
      - `file_request` は `StaticPath` を通す。許可リスト外は `"File not found", 404` で、パスを warning に出す（`favicon.ico` と `.well-known/` はブラウザ・ツールが自分から取りに来るものなので debug）
      - `file_request` の POST を外した（GET のみ。HEAD は Flask が付ける）
      - `before_request` の `guard_request` → `_guard_http_request`
        - `GUARDED` の endpoint だけを判定する（HEAD も GET と同じく判定される）
        - OPTIONS（プリフライト）と、どのルートにも当たらない URL は素通しにする（Flask の 404 / 405 に任せる）
        - 401 には `WWW-Authenticate: Bearer realm="Autona"` を付ける
      - `_authorize` が渡すもの:
        - 自分のオリジンは `SERVER_PORT`（待ち受けポート）と `_lan_hosts`（`GetLocalIPs()` を初回に 1 回だけ引いて保持）から作る
        - `Host` は `request.headers` の値
        - 転送ヘッダの有無は `IsForwarded(request.headers)`
    - Socket.IO
      - 全ハンドラを `ws_on(event)` で登録する。`GUARDED` のイベントだけを `_ws_guard` で包む
      - `ws_connect` は最初に `_authorize_ws_connection(auth)` を呼ぶ
        - 接続はすべて受け入れ、これまでどおり state を送る
        - 判定は handshake の要求（polling なら最初の GET、websocket なら upgrade の GET）の Host / Origin / Sec-Fetch-Site / 転送ヘッダで行う
        - 判定の結果（`(GRANT_AMBIENT, None)` / `(GRANT_TOKEN, 世代)` / None + 拒否理由）を、その接続の `request.environ` に置く。python-socketio は接続の生存中この environ を保持するので、切断時の後始末は要らない
        - 書き込み可の接続は room に入れる（トークンなしは `autona:trusted`、トークンは `autona:token:{世代}`）
      - `_ws_guard`
        - 判定がまだ有効なら（`GrantStillValid`。トークンの接続は世代が同じ間だけ）ハンドラを呼ぶ
        - 無効なら、ハンドラを呼ばずにエラーエンベロープを ack として返す。再発行で切れた接続は `TOKEN_REPLACED`
    - `ws_playerdb`
      - Socket.IO で要求されたとき: 要求した接続だけに返す（`_ws_guard` を通った後）
      - DB の更新（`db_updated`、リクエストの外）: 信頼済みの room（`autona:trusted` と今の世代のトークン room）にだけ送る。旧実装は全接続に送っていた
    - `WebServer.__init__` で `WebAccess.Token()` を呼び、初回起動でトークンを作る
  - Web UI
    - `web/src/autonaToken.ts`（新規）
      - `captureTokenFromUrl()`: URL の `?token=` を sessionStorage（使えなければメモリ）に入れ、`history.replaceState` でアドレスバーから消す。`desktop=1` の URL（Designer）は触らない
      - `tokenHeaders()` / `withToken(headers)` / `socketAuth()`
    - `index.js`: 描画の前に `captureTokenFromUrl()` を呼ぶ
    - `api.ts`: ヘッダを `withToken(init?.headers)` で組む。`new Headers(...)` に `set` するので、呼び出し側のヘッダがオブジェクト・Headers・配列のどの形でも落ちない
    - `websocketConnection.ts`: `auth: (cb) => cb(socketAuth())`。再接続のたびに読み直す
  - デスクトップ
    - 「ブラウザ版を開く」（`AutonaScoreboardWidget._WebScoreboardOpenUrl`）: LAN アクセスが有効で、使えるトークンがあるときだけ、クリック時のトークンを `?token=` に載せて開く（URL を別の端末にコピーしても使えるように）。LAN アクセスが無効なら付けない（この PC のページなので要らない）。`webScoreboardUrl` 属性はトークンなしのまま
    - API 設定ウィンドウ（`AutonaApiInfoWindow`。Qt の既存ダイアログへの行の追加。新規パネルではない）
      - 接続グループに「アクセストークン」行を置く。既定は伏せ字で、表示・コピー・再発行ができる
      - 再発行はボタンのクリックから確認ダイアログを出す（入れ子のモーダルにならない）
      - 赤い警告「LAN では無認証」を、トークンの渡し方の説明に差し替えた
      - エラーコード表に `UNAUTHENTICATED` / `FORBIDDEN` を足した
      - LAN が有効なとき、「curl をコピー」の書き込みに `-H "X-Autona-Token: ..."` を付ける
      - アプリ内 API Tester は変えていない（127.0.0.1 宛てでヘッダなし = この PC からとして通る）
    - Companion ガイド（`AutonaIntegrationGuideWindow`）: 「別 PC の Companion はヘッダ `X-Autona-Token` を足す」手順を 1 つ足した
    - OBS ガイド（`AutonaObsGuideWindow`）: LAN 有効時の注記に「別 PC の OBS（ASS の HTTP アクション）は URL 末尾に `?token=`（すでに `?` があれば `&token=`）を付ける」を足した（ガイドの v1 リクエストは書き込みのため）
    - 翻訳: `.ts` を手で編集し（置き換え 2・追加 8、api コンテキスト）、`scripts/compile_translations.ps1` で `.qm` を作り直した
- **判断と理由**
  1. **許可リストは「HTTP で実際に読まれているもの」だけ**
     - オーバーレイが読むもの: `layout/`。file:// の場合は `out/program_state.json` を読む
     - Web UI が読むもの: `web/build/`。`assets/` のフォントとアイコン、`assets/data_countries.json` も読む
     - state の画像パスが指す先: `./assets/...`・`./user_data/games/...`、チーム / スポンサーのロゴ、アバター、カスタム国旗、大会バナー
     - `user_data/` のうち JSON（`settings.json`・`local_players.json`・`layout_options.json`・`characters.json`・`alerts_red.json`・`tournament_terms.json`）は、すべて Python がファイルとして読んでいる。HTTP の参照はコードにもレイアウトにも無いので、外した
     - 許可リスト外の要求には 404（ファイルが無いときと同じ）を返し、ログには残す（カスタムレイアウトが参照していれば気づける）
  2. **Flask の static_folder を無効にした**: `/./<path>` が許可リストの抜け道になるため。`file_request` を唯一のファイルのルートにした
  3. **`/<path:filename>` の POST を外した**: 上流 TSH フォークの最初のコミットから残っていたもので、ファイルを読み返すだけだった。POST の無いルート宛ての POST に 405 ではなく 404 を返す原因にもなっていた
  4. **「この PC」はループバックとこの PC の LAN アドレス**: この PC の LAN アドレスから来たパケットは、この PC が送ったもの。同じ PC の Companion・ブラウザが LAN の IP の URL を使っていても（LAN を有効にすると、アプリ自身が LAN の IP の URL を案内する）、今までどおりトークンなしで動く
  5. **転送ヘッダが付いた要求は「この PC」としない**: ローカルのリバースプロキシやトンネルは、他の端末の要求をループバックから中継する。転送ヘッダを付けないプロキシでは判定が外れるので、運用文書で注意を書いた（runtime-operations.md）
  6. **トークンなしの書き込みは `Host` がこのサーバーの名前のときだけ（DNS rebinding 対策）**: rebinding されたページは攻撃者のドメインのまま 127.0.0.1 に届き、そのページから見ると要求は same-origin で、`Origin` もそのドメインになる。`Host` は URL のホストからブラウザが付けるので、ページ側では偽れない。読み取りは承認済み設計どおり免除のまま
  7. **`Sec-Fetch-Site` は許可リスト（無い・`same-origin`・`none`）**: `same-site`（この PC の別ポートのページなど）も拒否する。理由は、同じ PC の別ポートのページからの no-cors GET（`<img src=".../scoreboard1-team1-scoreup">`）を防ぐため。この GET には `Origin` が付かない。正当な利用は影響を受けない（自分のページは `same-origin`、Companion などはヘッダを付けない）
  8. **正しいトークンは CSRF・Host の判定も免除する**: トークンは cookie ではないので、ブラウザが自分で付けることはない。トークンを送ってくるページは、利用者からそれを渡されている。だから偽造ではない（rebinding されたページはトークンを知らない）
  9. **`Origin: null` はトークンなしでは拒否する**
     - `null` を出すのは、file:// のページ、sandbox の iframe、data: の URL など、出どころを確かめられないページ
     - file:// のオーバーレイが送るのは `program_state`（読み取り）だけで、書き込みは送らない
     - Figma の取り込み（`/api/designer/imports`、`null` を受ける）は `DESIGNER` に分類しているので、この判定を通らない
  10. **自分のオリジンは待ち受けポート（`SERVER_PORT`）から作る**: `Host` ヘッダからは作らない（`Host` は判定 6 で照合する側）
  11. **Socket.IO は全接続を受け入れ、書き込み可かどうかを接続ごとに持つ**
      - 接続そのものを拒否すると、OBS の file:// オーバーレイや別 PC の OBS（トークンを持たない）が state を受け取れなくなる（外部契約 §2.8）
      - 判定は接続時に 1 回だけ行う。Socket.IO のイベントには `Origin` が付かないため、handshake で見るしかない
      - 拒否は ack で返す。Socket.IO には HTTP のステータスが無く、ack を受け取らないクライアントには何も起きない（旧と同じ）
  12. **トークンなしで通れる接続は、トークンを送ってきても `GRANT_AMBIENT` にする**: この PC の自分のページは、トークンの再発行で切れない。トークンで入った接続（別端末・別オリジン）だけが、世代で切れる
      - HTTP: 次の要求からすぐ拒否される
      - トークンで入った Socket.IO 接続: 次のイベントで拒否される（`TOKEN_REPLACED`）。選手 DB の broadcast も、世代ごとの room なので届かなくなる
  13. **トークンはメモリに持ち、黙って作り直さない**: settings.json を一時的に読めなかったときに作り直すと、新しいトークンが保存され、ファイル全体がほぼ空で上書きされる。利用者の機器はすべて使えなくなる
      - 読めなかったときと値が壊れているときは、使えるトークン無しで fail closed にした（LAN の書き込みを拒否する。この PC からは今までどおり）
      - API 設定の再発行で直る。読めなかっただけなら、再起動しても直る
  14. **legacy の GET でも、状態を変えるものは `WRITE`**（Stream Deck が URL を貼るため、メソッドではなく効果で分類した）
      - 例: `scoreboard1-team1-scoreup`・`-reset-*`・`-stats-*`・`load_set`・`set_tournament`
      - `get_thumbnail` もボードを描画して `out/` に画像を書くので `WRITE`
      - ボードを構築して読むだけのもの（`get_route` / `get_swap` / `get_set`、v1 の `get_scoreboard`）と、start.gg を引いて返すもの（`get_sets` / `get_match`）は `READ`（残置 / リスク参照）
  15. **「ブラウザ版を開く」のトークンは LAN アクセスが有効なときだけ**: 無効なら、この PC のページなので要らない。付けると、そのタブだけが再発行の影響を受ける形になる
  16. **Designer のトークン（`?desktop=1&token=...`）とペアリングコードは触らない**
      - 承認済み設計のとおり、別の仕組みとして残す
      - Web UI の取り込みは `desktop=1` の URL を飛ばす。`desktopApi.ts` はその `token` を毎回アドレスバーから読むので、消すと Designer が壊れる
  17. **拒否のログにはパスだけを書く**: クエリ文字列（`?token=`）は書かない。トークンの値はどこにも出さない
- **外部から見える変化**
  - LAN の他端末からの書き込み（v1 の POST / PATCH・legacy の書き込み URL・Socket.IO の書き込みイベント）と選手 DB（`/playerdb`・`playerdb` イベント）に、トークンが必要になる
    - 無い・誤り: HTTP は 401 `{"ok": false, "error": {"code": "UNAUTHENTICATED", ...}}`
    - Socket.IO: 同じ形の ack を返し、処理しない
  - この PC からは、ループバックの URL でも、この PC の LAN の IP の URL でも、今までどおりトークン不要
    - ただし、転送ヘッダの付いた中継（ローカルのリバースプロキシ・トンネル）は他の端末と同じ扱いになる
    - このサーバーの名前（127.0.0.1 / localhost / [::1] / この PC の LAN IP と待ち受けポート）以外の `Host` 宛ても、他の端末と同じ扱いになる（例: `http://<PC 名>:5107`）
  - トークンなしの書き込みは、ブラウザの別サイト・同一サイトの別オリジン・別オリジンのページからだと 403 `FORBIDDEN` になる
    - Companion・Stream Deck・ASS・curl・`requests` は、これらのヘッダを付けないので変わらない
    - Web UI の開発サーバー（`npm start`、localhost:3100）は別オリジンなので、書き込むには `?token=<token>` 付きで開く
  - 読み取りは変わらない（オーバーレイ・`/web`・state・カタログ・Socket.IO の受信。LAN からもトークン不要）
  - 許可リスト外のファイルは 404 になる
    - 例: `user_data/settings.json`・`local_players.json`、`src/`、`tmp/`、`logs/`
    - 拒否された要求は `webserver: not serving ...` の warning で出る
  - `file_request` 宛ての POST は 405 になる（旧はファイルを返していた）
  - legacy の書き込みが拒否されたときは JSON の本文を返す（成功時の `"OK"` などは変わらない）
  - 選手 DB の更新の broadcast は、書き込み可の接続にだけ届く（OBS や、トークンを持たない LAN のページには届かない）
  - `settings.json` に `general.webserver_token` が増える（キーの追加のみ）
- **pin**
  - `tests/test_web_access.py`（新規・254 件。実際の Flask アプリと Socket.IO サーバーを使い、Actions は偽物に差し替える）
    - `StaticPath`: 許可するもの、拒否するもの（`..`・絶対パス・`\`・ドライブ名・ストリーム・NUL・大文字小文字・`layout.` などの別名）
    - HTTP の静的配信
      - `settings.json` などは 404 で、warning は 1 回
      - `favicon.ico` / `.well-known/` は warning にしない
      - POST は 405。`/./` は無い
    - トークン
      - 初回だけ発行し、info ログを出す（値は出さない）
      - 再発行ができる
      - メモリに持つ（settings を空にしても書き換えても変わらない）
      - settings.json を読めなかったら何も発行・保存せず、何とも一致しない。LAN の書き込みは再発行まで 401
      - 壊れた保存値は使わず、作り直しもしない（再発行で直る）
      - 手で設定した正しい形の値はそのまま使う
      - 3 つの場所すべてで受け取る
    - アドレスとオリジン
      - ループバック / この PC（2 つの LAN アドレス）/ 転送ヘッダの判定
      - オリジンの正規化（userinfo・パスなどは None）と自分のオリジン
    - HTTP の書き込み
      - ループバックは書ける。LAN は読める。LAN の書き込みと選手 DB は 401 で、`WWW-Authenticate` が付く
      - Bearer・ヘッダ・クエリで通る
      - 誤ったトークンは 401 で、値はログに出ない
      - 再発行した直後から旧トークンは通らない
      - プリフライトは通る。拒否のログは種類ごとに 1 回
      - legacy の書き込みへの HEAD も、GET と同じく判定される
    - CSRF
      - 拒否（403）: cross-site、same-site、未知の値、別オリジン、`null`、localhost:3000、ポート違い、https。GET / HEAD / POST / playerdb のそれぞれで確かめる
      - 通す: ヘッダなし、same-origin、`none`、自分のオリジン、`Host` が `127.0.0.1` / `[::1]`
      - トークンがあれば免除。読み取りは止めない
    - この PC の LAN アドレス
      - 2 つとも、トークンなしで書ける（`Host` / `Origin` がその LAN アドレス）
      - 同じ判定が掛かる（別オリジン・cross-site・same-site は 403、rebinding・転送ヘッダは 401）
      - 他の LAN アドレスは「この PC」にならない
    - DNS rebinding: `Host` が `evil.example:5107` / `evil.example`（same-origin・同名の Origin 付きを含む）/ `localhost.` / ポート違いのとき、GET の書き込み・v1 の POST・`/playerdb` は 401。読み取りは通る
    - プロキシ: `X-Forwarded-For` / `Forwarded` / `X-Real-IP` の付いたループバックの要求は 401 で、トークンがあれば通る
    - Socket.IO（テストクライアント）
      - どの接続も `program_state` を受け取る。ループバックは書ける
      - この PC の LAN アドレスのページは書ける
      - LAN のトークンなしは読み取りだけになる（ack が `UNAUTHENTICATED`、選手 DB も拒否）
      - auth・クエリ・ヘッダのトークンで書ける
      - 別オリジン（`null`・`http://absolute` を含む）は接続できるが、書くと `FORBIDDEN`
      - rebinding の `Host`・転送ヘッダ・same-site の接続は読み取りだけ
      - 自分のオリジンは書ける
      - 再発行で切れるのは、トークンで入った接続だけ
        - ループバックで自分のページの接続は、トークンを送っていても切れない（選手 DB の broadcast も届き続ける）
        - 別オリジンでトークンを使っていた接続は切れる
      - 選手 DB の broadcast は信頼済みの room にだけ届き、再発行の後は旧トークンの接続に届かない
      - `GUARDED` の全イベント（27）が、読み取りだけの接続で同じ拒否の ack になる。ハンドラが `_ws_guard` なしで登録されていれば、ここで落ちる
      - AST: このモジュールで `socketio.on` を呼ぶのは `ws_on` の中だけ
    - Socket.IO（Engine.IO の polling で handshake から）
      - この PC からの接続は書ける。LAN からはトークン付きで書ける
      - 次の handshake では、書き込みと選手 DB が拒否される
        - rebinding の `Host`（same-origin 付きを含む）: 401
        - 転送ヘッダ: 401
        - LAN のトークンなし: 401
        - 別オリジン: 403
    - API 設定ウィンドウ（オフスクリーン）
      - トークンは伏せ字で、表示で見える
      - 再発行は確認で Yes のときだけ
      - curl のトークンは LAN の書き込みだけに付く
    - 「ブラウザ版を開く」の URL: LAN 有効のときだけトークンが付き、使えるトークンが無いときは付かない
  - `tests/test_webserver_routes.py`
    - 全 endpoint と全イベントが分類済み（台帳との集合一致）
    - 分類規則: v1 の GET は READ でそれ以外は WRITE、legacy の POST は WRITE、designer は DESIGNER、playerdb は PLAYERDB
    - 件数: legacy 43 / WRITE 29、イベントの WRITE 26、v1 の WRITE 18
    - 表に無い名前は守られる
    - 台帳の更新: `static` ルートを削除、`file_request` は GET のみ、`static_folder` は None、`before_request` に `guard_request` を追加、クラス属性に guard 4 つを追加
  - `tests/test_import_side_effects.py`: `src.core.WebAccess` を足した。import でトークンを読まない・発行しない
  - `web/src/autonaToken.test.js`（新規・12 件）
    - 取り込み・アドレスバーからの削除・再読込後も残る
    - トークンなし
    - Designer の URL は触らない
    - ストレージが使えない場合
    - api のヘッダ（あり / なし）
    - `withToken`: オブジェクト・Headers・配列のヘッダが落ちない。同名の古いヘッダは置き換える。トークンなしならそのまま
    - socket の auth 関数
- **残置 / リスク**
  - **読み取りは開いたまま**（承認済み設計）
    - LAN の誰でも、state（選手名・本名を含みうる `score.*`）とカタログを読める
    - CORS は `*` のままなので、この PC で開いた任意のサイトからも state を読める
    - DNS rebinding で読み取りを奪う経路も、読み取りについては残る（書き込みは `Host` の判定で止まる）
    - 読み取りも守るなら、`Host` の検査を読み取りにも広げ、CORS を絞る必要がある（OBS の file:// オーバーレイのため、慎重に行う）
  - **READ のルートに、状態を作る副作用がある**
    - 該当するもの
      - `get_route` / `get_swap` / `get_set`・v1 `get_scoreboard`: 未構築のボードを構築し、そのサブツリーを書く
      - `get_sets` / `get_match`: start.gg を引く
    - 受容した理由
      - どちらも利用者のデータ（スコア・選手・設定）を変えない。構築は、そのボードを開いたときと同じ初期化になる
      - 外部の OBS・Web UI が読み取りの経路として使っている
    - そのため分類方針（「効果で分類する」）の例外として READ に残した。LAN の誰でも、未構築ボードの構築と start.gg への問い合わせを起こせる
  - `designer_import_received` / `designer_project_saved` の通知は、今も全接続に broadcast される（中身は project 名と取り込み ID。S3a の範囲外）
  - **LAN からの legacy の GET 書き込みにトークンが要る**
    - 例: `scoreboard1-stats-*`・`-get-thumbnail`・`update_bracket`
    - 別 PC の Stream Deck が URL を貼っていたら、`?token=`（すでにクエリがあれば `&token=`）を足す必要がある（リリースノートで告知）
  - **この PC の LAN アドレスは、初回の判定で 1 回だけ調べて保持する**
    - 起動中に IP が変わる（DHCP の更新・Wi-Fi の切り替え）と、新しい IP は「この PC」にならない
    - そのため、新しい IP の URL を使う同じ PC の Companion・ページは、再起動まで 401 になる（トークンを付けていれば通る）
  - **この PC から、このサーバーの名前以外のホスト名で書いていた場合は 401 になる**
    - 例: `http://<PC 名>:5107`・mDNS 名・hosts に足した名前
    - rebinding と区別できないため。127.0.0.1 / localhost / LAN の IP に替えるか、トークンを付ける
  - 転送ヘッダを付けないプロキシ・トンネルを、この PC で LAN の外へ公開すると、その経路では認証が外れる（運用文書で注意。Autona 側では見分けられない）
  - トークンは平文の HTTP で流れ、全端末で 1 つを共有する。操作ログは今も IP 単位
  - トークンの再発行は、API 設定を開いている間しかできない（設定画面には出していない）
  - settings.json を読めなかった回の起動では、LAN の書き込みが全部拒否される
    - 再起動で読めれば元に戻る。再発行すれば、その場で新しいトークンになる
    - ただし、そのとき settings.json は空の内容で上書きされる。これは既存の SettingsManager の挙動で、どの設定の保存でも同じ
  - 古いブラウザ（`Sec-Fetch-*` が無い）の no-cors GET は、`Origin` も付かないので CSRF の判定をすり抜ける（`Host` の判定は掛かる）。OBS（CEF）・Chrome・Edge・Firefox・Safari の現行版は `Sec-Fetch-Site` を付ける
  - 既存の未翻訳: API 設定のエンドポイントの説明文・エラーコードの説明（例: 「Full state + revision」）は、HEAD の時点で `.ts` に無かった。今回は自分で足した文言だけを翻訳した
  - `AutonaStatsPreviewWidget` の「Companion マクロ」カードは、LAN の URL を表示するがヘッダの説明は無い（Companion ガイドへのボタンがあり、ガイドには手順を足した）
- **実機確認（オーナー）**
  - LAN オフ
    - この PC のブラウザで `/scoreboard` を開き、スコア・チーム名・反映を操作できる
    - 「ブラウザ版を開く」の URL に `?token=` が付かない
  - LAN オン・この PC
    - 「ブラウザ版を開く」（URL は LAN の IP で `?token=` 付き）で開いたページから操作できる。アドレスバーから `token` が消え、再読込しても操作できる
    - 同じ URL を `?token=` なしで開いても操作できる（この PC から）
    - 同じ PC の Companion が LAN の IP の URL のままでも、ヘッダなしで動く
  - LAN オン・別端末（スマホ）
    - トークンなしで `http://<IP>:5107/scoreboard` を開く: 表示は追従する。スコアを押しても変わらない（デスクトップのログに `refused` の warning が 1 回出る）
    - API 設定からコピーしたトークンを `?token=` に付けて開き直す: 操作できる
  - 別 PC の Companion: ヘッダ `X-Autona-Token` なしでは押しても変わらない。足すと動く
  - 別 PC の ASS: 書き込みの URL に `?token=`（クエリがある URL は `&token=`）を付けると動く
  - OBS のオーバーレイ（http://127.0.0.1 と、ローカルファイル（file://）の両方）が今までどおり更新される。別 PC の OBS（LAN の IP の URL）も更新される
  - Chrome と OBS（CEF）のブラウザソースで開いた Web UI で、選手名の補完（playerdb の WebSocket 受信）が効く。WebSocket の handshake に付く `Sec-Fetch-Site` が `same-origin` で通ることの確認
  - アプリ内のプレビュー（BOARD タブ・レイアウトのプレビュー）と Designer（保存・読込）が動く。Figma プラグインからの取り込みが届く
  - API 設定
    - トークンが伏せ字で出る。表示で見える。コピーできる
    - 再発行は確認が出て、No では変わらない
    - Tester の書き込み（`score:adjust` など）が LAN オン / オフどちらでも通る
  - 再発行の後
    - 旧トークンで開いていた別端末のページは操作できなくなり、新しいトークンで開き直すと戻る
    - この PC で開いていたページは、そのまま操作できる
  - ログ: `http://127.0.0.1:5107/user_data/settings.json` を開くと 404 になり、warning が 1 回出る
- **リリースノート案**
  > **LAN の他の端末からの操作にアクセストークンが必要になりました**
  > 「LAN 上の他のデバイスからのアクセスを許可」をオンにして、別の PC やスマホから Autona を操作している場合（Companion / Stream Deck / Advanced Scene Switcher / ブラウザ）、操作の要求にアクセストークンが必要になりました。トークンは「ヘルプ＆設定 → API設定 → リファレンス・テスターを開く」で表示・コピーできます。
  > - Companion などの HTTP 要求: ヘッダ `X-Autona-Token: <トークン>`（または `Authorization: Bearer <トークン>`）を追加するか、URL の末尾に `?token=<トークン>` を付けてください（URL にすでに `?` があるときは `&token=<トークン>`）
  > - ブラウザ版: 「ブラウザ版を開く」から開けば、トークンは自動で渡されます。別の端末では、URL の末尾に `?token=<トークン>` を付けて開いてください
  > - 表示（OBS のオーバーレイ・ブラウザ版の表示）にはトークンは不要です
  > - Autona を動かしている PC からの操作は、これまでどおりトークンなしで使えます（`127.0.0.1` の URL でも、その PC の LAN の IP の URL でも）。LAN アクセスがオフの場合も変わりません
  > - トークンが漏れたときは、同じ画面の「再発行」で作り直せます（古いトークンはすぐに使えなくなります）
  >
  > あわせて、Web サーバーが配信するファイルをオーバーレイ・ブラウザ版・画像に限定しました。`user_data/settings.json` や選手 DB はブラウザから読めなくなります。独自のレイアウトが `layout/` `assets/` `out/` と `user_data/` の画像フォルダ以外のファイルを読んでいた場合は表示されなくなり、ログに `not serving` が出ます。

#### S3b 永続化の分離（2026-10-25・PR #228）

state の正本を `out/program_state.json` から `user_data/state.json` に移した。`program_state.json` は OBS の file:// オーバーレイと ASS が読む出力で、場所・形式・`timestamp` / `revision` が外部契約に縛られ、版も持てなかった（S-14）。正本は版付きの `state.json` にし、`program_state.json` は今までどおり書き出すだけの出力に戻した（読むのは移行のときだけ）。保存は新しい core モジュール `src/core/StatePersistence.py`、版の移行は `src/core/StateMigrations.py` が持つ（どちらも Qt なし・import 副作用なし）。

- **実装**
  - `src/core/StateMigrations.py`（新規）
    - `CURRENT_VERSION = 1`。`Migrate(state, version)` が前方一方向に 1 段ずつ上げる（`_STEPS`）。int でない版・負の版・`CURRENT_VERSION` より新しい版は例外
    - v0（S3b 前の `program_state.json`）→ v1
      - `RETIRED_ROOTS`（`bracket` / `player_list` / `player_editor`）を落とす
      - `STALE_KEYS`（`tournamentInfo.streamTitleEdit` / `streamUrl`）を落とす
      - `commentary` があれば `CommentaryModel.NormalizeState` を掛ける。無ければ作らない（シードは `SeedPanelState` の仕事）
    - 出荷した移行関数は凍結する。名前は `StateSchema` の定数を読まず、書いた時点のコピーを持つ（定数が後で変わると、古いデータへの処理が黙って変わるため）。`StateSchema` を import しないのは循環を避けるためでもある（StateSchema → StateManager → StatePersistence → StateMigrations）
  - `src/core/StatePersistence.py`（新規）
    - 形式: `{"state_version": 1, "state": {...}}`。`state` は公開済みの state（held を巻き戻したもの = `program_state.json` と同じ内容）から `timestamp` / `revision` を除いたもの。orjson（`OPT_NON_STR_KEYS`、インデントなし）
    - 書き込み
      - `SaveState` が公開のたびに `Schedule(snapshot)` を呼ぶ（`lastSavedState` を差し替えた直後、export をキューに積む前）
      - 専用の書き出しスレッド（`AutonaStatePersist`、非 daemon）が、最後の変更から 1 秒（`WRITE_DELAY`）静かになったら最新の 1 つだけを書く。変更が続いても、最初の未保存の変更から 5 秒（`MAX_WRITE_DELAY`）で書く
      - mkstemp → `os.replace`。replace の失敗は 4 回まで待って再試行する。`program_state.json` と違い、直接上書きへのフォールバックは無い（このファイルをポーリングする読み手はいないので、途中まで書いたファイルより古いファイルの方がよい）
      - `out/` と `general.disable_export` からは独立（export の書き出しスレッドとは別）
    - 終了時: 非 daemon なので、インタプリタ終了時の join が待つ。メインスレッドが終わったら（`threading.main_thread().is_alive()` が False）、待ち時間を切り上げてすぐ書く（0.1 秒ごとに確認）
    - `Flush(timeout)`: 待ち時間を飛ばして、書き切るまで待つ。テストと harness の `settle()` 用で、アプリは終了時の join に任せる
    - 書き込みの失敗: error ログは連続する失敗の 1 回目だけ出す（以降は debug。成功すると戻る）。スレッドを起動できない（終了処理中など）ときは snapshot を残し、次の `Schedule` / `Flush` で書く
    - `Load()`: 起動時の読み込み（下表）。パスはこの時点の CWD で絶対パスに固定する。`Load()` していないプロセス（`Initialize()` を呼ばないスクリプト・テスト）では `Schedule` が何もしない
  - 起動時の分岐（`StatePersistence.Load`）

    | `user_data/state.json` | 動作 |
    |---|---|
    | 使える | その版から `Migrate` して読む。書き直さない |
    | 使えるが、`out/program_state.json` の方が新しい（mtime） | `program_state.json` を v0 から移行して読み、info ログに理由を出して**その場で `state.json` を書き直す**。`program_state.json` が読めない・オブジェクトでない・移行が例外を出すときは `state.json` のまま |
    | 無い | `out/program_state.json` を読み、`timestamp` / `revision` を落として v0 から移行し、**その場で `state.json` を書く**（以後は `out/` を消しても残る）。`program_state.json` は触らない |
    | 壊れている（JSON でない・オブジェクトでない・版が int でない・負の版・`state` が無い・移行が例外を出す） | `state.json.corrupt-YYYYmmdd-HHMMSS.bak` に移して warning。以後は「無い」と同じ（同じ秒に複数あれば `-2`, `-3`…）。**移せなければ触らず（証拠を残す）、このセッションは `state.json` を書かない**（error ログ） |
    | 新しい版（ダウングレード） | `state.json.v{N}.YYYYmmdd-HHMMSS.bak` に移して warning（既存の bak は上書きしない）。以後は「無い」と同じ。移せなければ触らず、このセッションは `state.json` を書かない（error ログ） |
    | 読めない（ロックなどの OSError） | 0.1 秒おきに 3 回まで読み直す。それでも読めなければ触らず、**このセッションは `state.json` を書かない**（error ログ）。読み込み元は `program_state.json` |

    - どちらのファイルも無ければ `{}`。`program_state.json` が壊れていれば error ログを出して `{}`（従来と同じ）
    - 起動時に、`user_data/` に残った書きかけの一時ファイル（`state.*.tmp`）のうち 1 時間以上前のものを消す（失敗しても無視して debug ログ）
  - `src/core/StateManager.py`
    - `LoadState()` は `StatePersistence.Load()` を呼ぶだけになった。どちらかのファイルがあれば `state_big_change` を出す（従来どおり。どちらも無ければ出さない）。`revision` はファイルから読まず 0 から始める（従来どおり）
    - 読み込んだ state に `timestamp` / `revision` は残らない。従来は前回の値が state の根に残り、`GetFullState` の全量 snapshot に乗っていた
    - `SaveState()` が公開の直後に `StatePersistence.Schedule(state_snapshot)` を呼ぶ
      - ロック順は `StateManager.lock` → 保存側の lock の一方向。保存側の lock の中では I/O をしない。書き出しスレッドは `StateManager.lock` を取らず、Qt に触らない
      - snapshot はコピーしない（公開後に変更されないのは、export の書き出しと同じ前提）
    - export（`_WriteExports`。text の出力と `program_state.json`）は変えていない
  - `src/core/StateSchema.py`: docstring とコメントのみ。「マイグレーション層は無い」の記述を StateMigrations へ向けた。`SeedPanelState` に重複して残す理由を書いた（判断 2）
  - `.gitignore`: `user_data/state.json` と `user_data/state.json.*.bak`（mkstemp の一時ファイルは既存の `*.tmp` で除外済み）
  - `scripts/validate_release_zip.ps1`: 4 つ目の検査。zip に `Autona/user_data/state.json` か `state.json.*.bak` があれば失敗する。git 管理ファイルの検査より前に置き、誤ってコミットされた場合でも止まる
  - `tests/conftest.py`: harness の `settle()` が `StatePersistence.Flush(30)` まで待つ
- **判断と理由**
  1. **ファイル形式は封筒（`{"state_version", "state"}`）にした**
     - 版を state の根に置くと、Socket.IO の payload と `program_state.json` に漏れる（外部契約）。読み込みと書き込みのたびに抜き差しするより、ファイルのメタデータとして分ける方が漏れようがない
     - 読み込んだ `state` の中に `timestamp` / `revision` があっても落とす（`program_state.json` を手で `state.json` に戻した場合）
  2. **`SeedPanelState` の 3 処理（`RETIRED_ROOTS`・`STALE_KEYS`・commentary の正規化）は残し、v0 → v1 と二重に掛ける**
     - `player_editor` は旧データだけのものではない。選手 DB エディタを開いている間は今のビルドも書くので、そのまま落ちたセッションは v1 の `state.json` に残す。v0 → v1 はもう走らないので、起動ごとの掃除でしか消えない
     - commentary の処理はシードを兼ねる（新規インストールで 12 枠を作る）。既存データの移行では作らない
     - 二重でも害がない。`NormalizeState` は冪等、無いルートの `Unset` は何も変えない（呼び出し側の SaveBlock の中なので保存サイクルも増えない）、`STALE_KEYS` はあるときだけ消す。`test_the_launch_cleanup_has_nothing_left_to_do_after_the_migration` が pin
     - 移行関数の側は凍結コピーを持つ。`StateSchema` の定数が今後変わっても、v0 データへの処理は変わらない（`test_the_v1_step_is_frozen` が、凍結値と、現時点で定数と一致していることを pin）
  3. **export のモジュール分離（StateExport.py）はしなかった**
     - 既存テスト（`test_state_manager_export_isolation.py` / `test_state_manager_image_export.py` / `test_state_manager_save_cost.py` ほか）が、`StateManager` の `_WriteExports` / `_atomic_write_bytes` / `ExportText` などをモジュールのパス経由で monkeypatch・呼び出ししている。分けると、テストの書き換えか再 export の shim が要る
     - 今回の目的（正本を分ける）には要らない。外部契約を pin しているテストを同じ PR で動かすのは、リスクの割に得るものが無い
     - export のコードは変えていないので、分離は後で単独の PR にできる
  4. **新しい版の `state.json` は退避し、`program_state.json` から移行する**（S3 設計の当初案「空から始め」を改め、設計節も更新した。採用済み）
     - 運用者にとっては大会情報が消えない方が有利。`program_state.json` は直前まで動いていた新しい版が書いたもので、空より実際の状態に近い
     - 移行関数は冪等（すでに移行先の形のデータを受けても壊さない）を規則にした（`StateMigrations` の docstring）。`program_state.json` は版を持たないので常に v0 として移行するが、中身は書いたビルドの形になっているため
     - 退避は日時付きの名前にして上書きしない。移せないときに上書きしない（`state.json` を閉じたままにする）のは、新しい版に戻したときにデータを残すため
  5. **間引きに上限（5 秒）を足した**。最後の変更から 1 秒だけだと、ゲームトラッカーの操作や大会データの取り込みのように変更が続く間、いつまでも書かれない
  6. **パスは `Initialize()`（`Load()`）の時点で絶対パスに固定する**
     - 書き込みは遅れて走るので、後で CWD が変わっても（テストの tmp CWD）別の場所に書かない
     - `Load()` していないプロセスは書かない。スクリプトから state を触ったときに、利用者の保存を部分的な state で上書きしない
  7. **壊れた `program_state.json` のときの `SaveState()` 呼び出しをやめた**。旧実装は `{}` にしてから `SaveState()` を呼んでいたが、`{}` と `lastSavedState`（`{}`）に差分は無く、何もしていなかった
  8. **fsync はしない**（settings.json と同じ）。電源断で `state.json` が失われても、次の起動は `program_state.json` から移行する
  9. **`program_state.json` の方が新しければ、そちらを採用する**（レビュー指摘で追加）
     - export は保存サイクルのたびにすぐ書き、`state.json` は 1〜5 秒待ってから書く。クラッシュ・強制終了では、その間の変更が `program_state.json` にだけ残る。間引きは変えずに、起動時に mtime で拾う
     - S3b 前の版に戻して使った後の復帰も、同じ規則で救える（旧版は `program_state.json` だけを書くので、そちらが新しくなる）
     - 正常終了の直後は、`state.json` の方が新しいか、同時刻なら `state.json` を使う。たまたま `program_state.json` が後になっても、中身は同じ snapshot で、移行は冪等なので結果は変わらない
  10. **読めない・退避できない `state.json` は上書きしない**（レビュー指摘で追加）。そのセッションは保存を止め、error ログを出す。中身が正しいかもしれないファイルや、壊れた原因の証拠を、部分的な保存で消さないため
- **外部から見える変化**
  - `user_data/state.json` ができる（初回の起動で `program_state.json` から作る）。以後、起動時に読むのはこちら
    - `out/` を消しても、OBS の出力先を変えても、前回の state（大会情報・配信設定・実況・Top 8・表示トグルなど）が戻る。`score` と `game` は従来どおり起動時に消える
  - Socket.IO の全量 snapshot（`program_state` イベント・v1 の `GET state`）の `state` から、前回のセッションの `timestamp` / `revision` が消える
    - payload の形（`revision` / `delta_index`）は変わらない
    - 読む側は無い。オーバーレイの `data.timestamp` は、file:// のポーリングで `program_state.json` を読むときだけ使う。そのファイルには今までどおり付く
  - `program_state.json` の根のキー順: `timestamp` / `revision` が常に最後になる。従来は 2 回目以降の起動で、起動後に足される `score` などより前に来ていた。JSON として読む側には影響しない
  - ダウングレード: 旧版は `program_state.json` を読むので困らない。旧版で使ってから戻すと、旧版が書いた `program_state.json` の方が新しいので、そちらから移行して続きになる
  - 手で `state.json` をバックアップから戻すときは、`out/program_state.json` も消す（コピーは更新時刻を保つので、`program_state.json` の方が新しいと見なされ、そちらが採用される）。`docs/operations/runtime-operations.md` に書いた
- **pin**
  - `tests/test_state_persistence.py`（新規・69 件）
    - 移行
      - v0 → v1 の 3 処理。commentary が無ければ作らない
      - 形の崩れたデータでも落ちない
      - 凍結値。全ての版に step がある。現行版の state は触らない
      - 範囲外の版・int でない版・dict でない state は例外
      - 移行の後に `SeedPanelState` の掃除が何もしない
      - v0 → v1 を v1 の形のデータに掛けても同値（冪等）
    - 読み込み
      - 使える store（書き直さない）。v0 の store
      - 初回の移行（すぐ書く・`program_state.json` は触らない）。どちらも無い
      - 壊れた store 9 種。壊れた store で `program_state.json` も無い。bak が衝突しない
      - 新しい版。同じ秒の bak も上書きしない。退避できない新しい版は書かない
      - `program_state.json` が読めない / オブジェクトでない
      - store の中の刻印を落とす
      - `program_state.json` の方が新しければ採用して書き直す（クラッシュ）。ダウングレードからの復帰。同時刻なら store。新しくても読めない / オブジェクトでない / 移行が例外なら store のまま
      - store の移行が例外 → 壊れた扱い。`program_state.json` の移行が例外 → `{}` で store を作らない
      - 壊れた store を退避できない → 書かない
      - 読み続けて失敗（OSError）→ 3 回試して書かない。1 回だけの失敗 → 読み直して通常どおり
      - 古い一時ファイルを消す（新しいもの・他の store のものは残す）。消せなくても起動は続く
    - 書き出し
      - 静かになるまで待つ。変更が続いても上限で書く。Flush は待たない
      - 封筒に刻印が無く、snapshot を変えない
      - アトミック（一時ファイルが残らない）。閉じた store は書かない
      - 失敗のログは 1 回で、その後回復する
      - daemon でない。スレッドを起動できないとき snapshot が残る
      - 諦めた Flush（タイムアウト・スレッド起動失敗）が要求を残さない（次の snapshot は間引かれる）
      - `_Run` の finally: 書き込み中に BaseException で落ちたら snapshot を戻し、次の Flush で書ける
    - StateManager
      - 公開のたびに store に届き、`program_state.json` の刻印は残る
      - `disable_export` でも store は書かれる
      - held の編集は、反映するまで store に入らない
    - 別プロセス
      - S3b 前のインストールからの起動: `state_big_change` 1 回・revision 0・state と `GetFullState` に刻印と退役キーが無い・store が v1・出力の revision 1 と timestamp・終了時に flush なしで最後の値が store にある
      - `WRITE_DELAY` 600 秒でも、終了時に書く
      - クラッシュ（`os._exit`）で store が書かれなくても、次の起動が `program_state.json` から最後の値を戻す
  - `tests/test_import_side_effects.py`: 2 モジュールを追加。import では store を開かず、スレッドを作らない。`Initialize()` でパスが決まり、書くまでファイルは無い
  - `tests/test_harness_isolation.py`: harness の store がリポジトリの `user_data/state.json` ではない（`--basetemp` に依存しない判定）
  - `tests/test_state_manager_held.py`: import 時の mkdtemp を終了時に消す（このプロセスの store がここに来ることが多い）
  - `tests/test_web_access.py`: `user_data/state.json` と `.bak` を配信しない
  - `tests/test_core_qt_free.py`: 2 モジュールを自動で対象にする（変更なし）
  - export の場所・形式・刻印・書き込み順は、既存の `test_state_manager_save_cost.py` ほかがそのまま pin する（変更なし）
- **残置 / リスク**
  - **fsync なし**: `os.replace` なので途中まで書いたファイルにはならないが、電源断で直前の内容に戻ることはある。壊れていれば `.corrupt` に退避して `program_state.json` から移行する。戻っただけなら、`program_state.json` の方が新しければそちらを使う
  - **クラッシュ・強制終了で失うのは、`program_state.json` にも出ていない分だけ**（export は保存サイクルのたびにすぐ書く）。`program_state.json` も消えていれば、`state.json` の時点（最大 5 秒前）に戻る
  - **mtime の比較に頼る**: 時計を戻した・`program_state.json` を手で触った・バックアップを戻したときは、新しい方の判定が運用者の意図とずれることがある（バックアップの戻し方は運用文書に書いた）
  - **`state.json` を読めない・退避できないとき、そのセッションは保存しない**（error ログ）。`program_state.json` の出力は続くので、次の起動はそちらの方が新しければそこから戻る
  - **終了時の `webserver.terminate()` と `_cond` / `_export_cond`**（2026-10-25 S3c-1 で訂正）
    - 当初は「`Schedule()` の中（`_cond` 保持中）で Web サーバーのスレッドが止められると、`_cond` が解放されず、書き出しスレッド（非 daemon）を待つ終了処理が止まる。`_export_cond` も同じ」と書いたが、この経路は実在しなかった。`terminate()` が止めていたのは accept ループのスレッド（`serve_forever()`）だけで、このスレッドは `_cond` も `_export_cond` も取らない。要求の処理（`SaveState` → `Schedule()` を呼びうる側）は werkzeug の daemon の要求スレッドで動いており、terminate の対象ではなかった
    - 実在したのは別の危険。`terminate()`（Windows の `TerminateThread`）は相手が何を持っているかを見ずに止めるので、accept ループが GIL や CRT / インタプリタ内部のロックを持った瞬間に止めると、プロセスが終了処理で固まりうる（通常は `select()` の待ちで GIL を放しているので、まれ）。S3c-1 で `terminate()` をやめ、`shutdown()` で止めるようにしたので、この危険は**解消**した
    - daemon の要求スレッドは今も止めずに終了する。インタプリタは、非 daemon スレッド（書き出しスレッド 2 本）の join が終わってから daemon スレッドを凍結するので、要求スレッドが `_cond` / `_export_cond` を持ったまま凍結されて書き出しを妨げることはない
  - 終了の遅れ: 書き込み待ちがあると、終了が最大 0.1 秒 + 書き込み 1 回ぶん遅れる
  - テストのプロセスでは、どれかのモジュールが `Initialize()` を呼んだ後は保存が有効になる（パスは tmp。harness の `settle()` が待つ）。`Initialize()` を呼ぶテストを足すときは tmp CWD で呼ぶ
  - 2 つ同時に起動すると、同じ `state.json` を書き合う。単一インスタンスの IPC で防いでいる前提は、従来の `program_state.json` と同じ
  - pytest 全量と live のテスト（start.gg など）は、実装の中では回していない（全量は別担当）
- **実機確認（オーナー）**
  - 更新後の初回の起動で `user_data/state.json` ができ、ログに `Migrated ... to ...` が出る
  - 再起動すると、大会情報・配信設定・実況・表示トグルが戻り、スコアは今までどおり空になっている
  - `out/` を消して起動しても、前回の state が戻る
  - アプリを閉じる直前（1 秒以内）に変えた値が、次の起動で戻る
  - 値を変えた直後にタスクマネージャーで Autona を強制終了しても、次の起動で戻る（ログに `is newer than` が出る）
  - `state.json` を壊して（例: 先頭に `x` を足す）起動すると、`state.json.corrupt-<日時>.bak` ができて warning が出て、`program_state.json` の内容で起動する
  - OBS のオーバーレイ（http と file://）と ASS が、今までどおり更新される
  - S3b 前の exe に戻しても起動し、前回の state が戻る（`program_state.json` を読む）。そこで値を変えてから新しい exe に戻すと、その値が残っている
- **リリースノート案**
  > **前回の内容の保存先が変わりました**
  > 大会情報・配信設定などの前回の内容を、`user_data/state.json` に保存するようになりました（これまでは `out/program_state.json` から読み戻していました）。更新後の初回の起動で自動的に引き継がれます。`out/` フォルダを消しても、前回の内容が戻ります。バックアップや別の PC への移行のときは、`user_data/state.json` もコピーしてください。
  > 開発者向け: `GET /api/v1/state` と Socket.IO の全量 snapshot（`program_state`）の `state` に、前回のセッションの `timestamp` / `revision` が残らなくなりました（`out/program_state.json` の `timestamp` / `revision` と、payload の `revision` は従来どおり）。

#### S3c-1 組み立ての 2 段化と Web サーバーのスレッド化（2026-10-25・PR #229）

S3c（ヘッドレス起動）を 2 つの PR に分けた（指示書の規模ルール）。S3c-1 はヘッドレスの前提になる土台で、GUI の挙動は変えない。組み立て（`src/bootstrap.py`）をどのシェルにも要る段と、デスクトップの Qt アダプタの段に分けた。Web サーバーは QThread をやめて、止められる普通のスレッドにした。`in_gui_thread()` は QCoreApplication を見る。`--headless`・`src/headless.py`・ルートの移行・503・スモークテストは S3c-2 で行う。

- **実装**
  - `src/bootstrap.py`
    - `bootstrap_core()`: どのシェルにも要るものだけを作る。QCoreApplication だけで動く（QtGui / QtWidgets のオブジェクトを作らない）。生成順は Settings → LayoutOptions → Locale / BadWord の `Initialize()` → StateManager → Bridge → Actions → GameAssetCatalog（+ `ResetGameState`）→ StartGG のクエリ → 大会 provider → TournamentInfoService → CountryCatalog → Designer の移行
    - `bootstrap_gui()`（新規）: 先頭で `bootstrap_core()` を呼び（冪等）、デスクトップの Qt アダプタを作る。順は Hotkeys（+ `ConnectActions`）→ `AutonaGameAssetManager` → provider の結線 → PlayerDB の `SetupModel` 結線 → AlertNotification → Updater → `AutonaCountryHelper` → `AutonaScoreboardManager`（`ResetScoreRoot`）。再入ガードは段ごと
    - 各ブロックの `ORDER CONSTRAINT:` を書き直した（新しい段の規則「QtGui / QtWidgets が要るか、デスクトップだけが使うなら `bootstrap_gui()`」も docstring に書いた）
  - `src/AutonaTournamentDataProvider.py`: Qt アダプタ（`AutonaGameAssetManager`）への依存を外した
    - `__init__` で `onLoadAssets` に connect していたのをやめ、`selectGameFromStartGGId`（start.gg の videogame ID でゲームを選ぶ関数）をシェルが登録する属性にした。未登録なら warning ログを出して何もしない
    - `bootstrap_gui()` が `selectGameFromStartGGId = adapter.SetGameFromStartGGId` を登録し、`onLoadAssets` → `SetGameFromProvider` を結線する
    - `SetTournament` の URL が空のときの `LoadGameAssets(0)` を削除した。2026-10-23 の PdM 判断で no-op になっていた（`test_an_empty_codename_keeps_what_is_loaded`）
  - `src/Autona.py`
    - `bootstrap_core()` の直後に `bootstrap_gui()` を呼ぶ
    - `_StartWebServer`: `WebServer(parent=self, on_start_failed=...)`。コールバックはサーバーのスレッドから呼ばれるので、`gui_executor` で GUI スレッドに渡してから `OnWebServerStartFailed`（非モーダルのダイアログ）を出す。旧 queued シグナルと同じ届き方
    - `closeEvent`: `terminate()` + `wait(10000)` を `webserver.stop(10.0)` に替えた。どこからも設定されていなかった `WERKZEUG_SOCKET_FD` を閉じるブロック（死にコード）と、それだけが使っていた `import socket` を削除した
  - `src/AutonaWebServer.py`
    - `WebServer` は QThread を継承しない素のクラスになった。`WebServerSignals`（`start_failed`）を削除し、コンストラクタの `on_start_failed(error: str)` コールバックに替えた
    - `start()`: daemon の `threading.Thread`（`AutonaWebServer`）で `make_server(host, port, app, threaded=True).serve_forever()` を回す。旧 `socketio.run()` が threading モードで組むもの（`Flask.run` → `run_simple` → `make_server`）と同じサーバーで、開発サーバー向けの付属物（バナー表示・`.env` 読み込み・リローダー用の `WERKZEUG_SERVER_FD`）だけを省いた。2 回目の呼び出しと `stop()` 後の呼び出しは何もしない
    - `stop(timeout)`: `shutdown()` で `serve_forever()` を抜けさせ、スレッドを join して socket を閉じる。止まったら True。`shutdown()` は補助スレッドで呼び、ループが固まっていても呼び出し側（ウィンドウの close）を timeout より長く止めない。処理中の接続は待たない（werkzeug の要求スレッドは daemon）
    - bind の失敗: werkzeug は OSError を stderr に出して `sys.exit(1)` する。これを捕まえ、元の OSError を添えた `could not listen on host:port: ...` でコールバックに通知する
  - `src/Helpers/AutonaQtHelper.py`: `in_gui_thread()` が `QCoreApplication.instance()` を見る。QtWidgets の import を外した
  - `tests/conftest.py`（harness）と `tests/live/restart_probe.py`: `bootstrap_gui()` も呼ぶ（ボード・パネルを作るため / `score` を落とすのはスコアボード dock なので）
  - コメントの追従: `src/AutonaHotkeys.py`（`ConnectActions` の冪等の理由）、`tests/test_scoreboard_operations.py`、`tests/test_import_side_effects.py`、`tests/test_designer_service.py`
- **判断と理由**
  1. **provider とアダプタの結線をシェルへ移した**
     - provider の `__init__` がアダプタの `signals` に connect していたため、「アダプタ → provider」が生成順の制約になり、アダプタ（QStandardItemModel・QIcon を持つ）がどのシェルにも要った。ヘッドレスはアダプタを作らない（ゲームのロードは `GameAssetCatalog` を直接使う。S3c-2）
     - `onLoadAssets` のスロットの順は変わらない。provider（`bootstrap_gui()`）→ Window の `ReloadGames`（`Window.__init__`）→ EVENT パネルのゲーム一覧（起動時に EVENT ページを建てたとき）→ アダプタ自身の `_RestoreSelectedGame`（アダプタの `UiMounted()` が、最初のスキャンの直前に connect）。provider がゲームを適用し直した後に、保存したゲームの復元が走るのも従来どおり。provider → `_RestoreSelectedGame` の順は、`tests/test_bootstrap_split.py` が実際の emit で確かめる
  2. **`bootstrap_gui()` へ移した 7 つの生成は、互いの相対順を保ち、core の生成の後ろにまとめただけにした**
     - core の生成（Actions 以降の GameAssetCatalog・StartGG・provider・TIS・CountryCatalog・Designer の移行）はどれも移した 7 つを参照しない。移した側は core の生成物だけを参照する
     - `AutonaScoreboardManager`（`ResetScoreRoot`）は Designer の移行より後になったが、Designer の移行は state を書かない。Window より前（= `score.*` の最初のシードより前）であることは変わらない
     - 段ごとの手順（生成・provider / PlayerDB / ホットキーの結線・`Initialize()`・Designer の移行）と、その順を `tests/test_bootstrap_split.py` が AST で pin する
  3. **Web サーバーは `socketio.run()` を呼ばず、werkzeug のサーバーを自分で作る**
     - `socketio.run()` は戻らず、止める手段が無い。旧実装は終了時に `QThread.terminate()`（Windows の `TerminateThread`）で accept ループのスレッドを殺していた。要求の処理は werkzeug の daemon の要求スレッドで動いていたので、殺されていたのは要求ではなく accept ループ
     - 危険は、そのスレッドが GIL や CRT / インタプリタ内部のロックを持った瞬間に止めると、プロセスが終了処理で固まりうること（通常は `select()` の待ちで GIL を放しているので、まれ）。`shutdown()` で止めるので、この危険は無くなった。S3b の残置に書いた `_cond` / `_export_cond` の経路は実在しなかった（S3b 節を訂正した）
     - 中身は `socketio.run()`（threading モード）が組むものと同じ。Socket.IO のミドルウェアは `app.wsgi_app` に巻かれており、WebSocket は simple-websocket がこのサーバー上で upgrade する
  4. **起動失敗の通知はコールバックにし、GUI スレッドへの受け渡しはシェルが行う**。Web サーバーを Qt に依存させないため（ヘッドレスはログに出すだけ）
  5. **daemon スレッドにした**。止め忘れ（クラッシュ・テスト）でプロセスが残らないように。正常終了ではどちらのシェルも `stop()` を呼ぶ
- **外部から見える変化**
  - 無い（GUI の起動順序・画面・Web の契約は変わらない）
  - 例外として、**bind に失敗したとき、「Web サーバーを起動できません」のダイアログが出るようになる**。旧実装では werkzeug の `sys.exit(1)`（SystemExit）が `except Exception` を素通りし、ダイアログは出ず Web サーバーだけが黙って止まっていた（`check_webserver_port` の事前確認はそのまま）
  - ポートを他のプロセスが握っているときは、`check_webserver_port` の衝突警告に続けて、bind が失敗すれば起動失敗のダイアログも出る（2 つ続けて出うる。Windows では SO_REUSEADDR で bind が通ることが多く、その場合は警告だけ）
  - 終了時の Web サーバーの停止が、強制終了から通常の停止になった（10 秒の猶予は同じ。通常は 0.5 秒以内）
- **pin**
  - `tests/test_bootstrap_split.py`（新規・6 件）
    - 段ごとの全手順とその順（AST）。生成に加えて、provider の結線（`selectGameFromStartGGId` と `onLoadAssets` → `SetGameFromProvider`）・PlayerDB の結線・`ConnectActions` が `bootstrap_gui()` のどこにあるか、`StartGGDataProvider.Initialize()` と Designer の移行が `bootstrap_core()` のどこにあるか。`bootstrap_gui()` の最初の手順が `bootstrap_core()`
    - 別プロセス・tmp CWD で、QCoreApplication の上で `bootstrap_core()` が通る。デスクトップのシングルトンを作らない。provider にゲームの選択関数が登録されていない。`in_gui_thread()` がメインで True・ワーカーで False
    - QApplication の上で `bootstrap_gui()` が前回の `score` を落とす。2 回呼んでも同じ。Hotkeys の `ConnectActions` 済み。登録されるのはアダプタの `SetGameFromStartGGId`
    - `onLoadAssets` のスロット順: アダプタの `UiMounted()` で実際に connect させてから emit し、provider（ゲームを登録された関数へ渡す）が `_RestoreSelectedGame` より先に走る
    - Web サーバーのスレッド: 空きポートで `/api/v1/health` が 200。`stop()` が 5 秒以内に True を返し、ポートが閉じる。`stop()` 後の `start()` は何もしない。bind できないアドレス（192.0.2.1）でコールバックが 1 回呼ばれる。サーバーのスレッドが残らない
  - `tests/test_gui_executor.py`: QApplication の上で `in_gui_thread()` がメインで True・ワーカーで False
- **残置 / リスク**
  - **ヘッドレス起動そのものは S3c-2**（`main.py --headless`、`src/headless.py`、`WebServerActions` の縮小と 503、PlayerDB の `SetupModel` と `LoadLocale` の QTranslator を QtGui なしで避けること、Socket.IO 経由のスモークテスト）
  - `check_webserver_port`（QMessageBox）と LAN 公開の初回確認は Window のまま。ヘッドレスでは設定値で決めてログに出す（S3c-2）
  - **S3c-2 の前提として片付けるもの**
    - `StartGGDataProvider` が entrants の取得の最後（2065 行目付近）で `AutonaPlayerDB.SetupModel()` を呼ぶ。QStandardItemModel / QIcon を作るので、ヘッドレスで大会を読み込むと QtGui が無くて落ちる
    - `WebServer.__init__` が `WebServerActions(scoreboard=AutonaScoreboardManager.instance)` を渡している。ヘッドレスでは None
    - `selectGameFromStartGGId` が未登録だと、大会のゲームは適用されない。黙って無視されないよう、ログを warning にした（S3c-1。ヘッドレスは S3c-2 で GameAssetCatalog を使う関数を登録する）
  - 停止時に処理中の接続（WebSocket を含む）は待たない。旧実装も待っていなかった
  - pytest 全量は回していない（全量は別担当）
- **実機確認（オーナー）**
  - 起動して、OBS のオーバーレイ（http）・`/web`・API 設定の API Tester が今までどおり動く
  - 閉じると、プロセスが残らずにすぐ終わる（タスクマネージャーで `python` / `Autona` が消える）。閉じる直前に変えた値が次の起動で戻る
  - 大会 URL を設定するとゲームが自動で切り替わる（provider → アダプタの結線）。ゲームを追加して再スキャンした後も、大会のゲームが選ばれたまま
  - ホットキー（スコア +/-、反映）が今までどおり効く
  - 他のプロセスが使っているポートを設定したときの警告ダイアログ（ポート衝突）が今までどおり出る

#### S3c-2 ヘッドレス起動（`main.py --headless`）（2026-10-26・PR #230）

S3c の後半。ウィンドウを出さずに state・大会データ・Web サーバーを動かす `python main.py --headless` を足した。Web の操作をデスクトップとヘッドレスで同じ関数に通し（`src/AutonaWebOperations.py`）、ボードやパネルのウィジェットが要る操作だけを `WebServerActions` に残した。ヘッドレスでは残りが 503 を返す。S3c-1 のレビューで出た前提 3 点（PlayerDB の Qt モデル・`WebServer.__init__` のスコアボード・ヘッドレスのゲーム選択関数）もここで片付けた。

- **実装**
  - `main.py`
    - `--headless`（`sys.argv` に含まれるか）。単一インスタンスの判定はデスクトップと共通（同時に起動させない）
    - ヘッドレスが 2 つ目の起動なら、エラー出力に `Autona is already running ...` を出して終了コード 1。デスクトップ版の 2 つ目は従来どおり既存ウィンドウを前面化して 0
    - IPC サーバー（`_start_ipc_server(on_activate)`）を分岐の手前に移し、前面化の処理をシェルが渡す形にした。ヘッドレスは「他の起動を断った」とログに出すだけ
    - ヘッドレスの分岐: QCoreApplication → IPC サーバー → `src.headless.run(App)` → その戻り値で `sys.exit`。スプラッシュ・QApplication・qasync・AppID ショートカットは通らない
    - `AUTONA_INSTANCE_TAG` 環境変数: mutex と IPC の名前に付ける接尾辞。スモークテストが、開発者の起動中の Autona と衝突せずに（2 つ目扱いにも、前面化の送信元にもならずに）ヘッドレスを起動するため。運用の機能ではない（コメントに明記）。ソースからの起動でだけ読み（`Autona.exe` は読まない）、英数字とハイフン以外を含む値は warning を出して無視する（`src/headless.py` の `instance_tag`）
    - `Autona.exe --headless`（frozen）は起動を拒否する。コンソールが無いのでメッセージボックス（`MessageBoxW`）で「exe はヘッドレス非対応、`python main.py --headless` を使う」と伝えて終了コード 1。単一インスタンスの判定（mutex）より前で終わる（`refuse_frozen_headless`）
  - `src/headless.py`（新規。import の副作用ゼロ）
    - `Assemble(app)`: GuiExecutor → `bootstrap_core()` → `LoadLocale` / `LoadRoundNames` → `out/`・`user_data/games`・`layout/logo.png` の用意（Window と同じ）→ `CountryCatalog.Load()` 直 → 1 保存サイクルで `ResetScoreRoot` + `SeedBoard(1..3)` + `SeedPanelState` + `TournamentInfoService.Seed` + `SeedColorState` → ゲームの選択関数とローダーの登録 → `wire_tournament_data` → `LoadDB` → `WebServer(actions=None)` + `wire_web_server` → 大会 URL の復元（`provider.UiMounted()`）→ ゲームのスキャン（`HeadlessGames.Start()`）→ LAN 公開の案内とポートの確認をログに → `webserver.start()`
    - `HeadlessGames`: `GameAssetCatalog` を直接使うゲームのローダー（Qt モデルなし）。アダプタ（`AutonaGameAssetManager`）と同じ順で動く: 起動時の start.gg キャラ表の更新とスキャン → スキャンのたびに provider のゲームを適用し直す（`SetGameFromProvider`）→ 初回だけ前回のゲーム（`SELECTED_GAME_CODENAME`）を復元。ロードは専用スレッドで 1 つずつ（`ReloadStartGGCharacters` → `SaveBlock` 内で `SelectGame`）、`done=` は成否を問わず 1 回。ロード後はメインスレッドで選択を保存し、`gameChanged` を出す（Web サーバーの push と PlayerDB の更新が受ける）
    - 登録: `provider.selectGameFromStartGGId = games.SelectGameFromStartGGId`、`AutonaWebOperations.loadGameByCodename = games.LoadGameByCodename`
    - Web サーバーが起動できなければ（bind 失敗）エラーをログに出して終了コード 1（`app.exit(1)`）
    - 停止: Windows は `SetConsoleCtrlHandler`（Ctrl+C・Ctrl+Break・閉じるボタン・ログオフ・シャットダウン）。閉じるボタン等は、ハンドラが戻るとプロセスが終わるため、停止処理の完了を最大 4.5 秒待ってから戻る。併せて Python の `SIGINT` / `SIGTERM` / `SIGBREAK` ハンドラと、それを動かすための 250 ms のタイマー
    - 停止ハンドラは組み立て（`Assemble`）の**前**に入れる。組み立て中に来た停止要求は `HeadlessShell.stopRequested` に残り、組み立てが終わったらイベントループを回さずに同じ停止処理へ進む（Windows の既定動作で組み立て途中のまま終わらない）。イベントループの終了は `QMetaObject.invokeMethod(app, "quit", QueuedConnection)`（メインスレッドからでも queued。ループ開始前の直接の `quit()` は捨てられるため）
    - `Shutdown()`: `webserver.stop(2.0)` → `StateManager.JoinExports(10)`（`out/`）→ `StatePersistence.Flush(10)`（`user_data/state.json`）→ コンソール制御ハンドラの登録解除（`SetConsoleCtrlHandler(routine, False)`）→ 完了イベント。Web サーバーを作る前の停止でも書き出しは行う
    - ログ: コンソール（INFO 以上。ヘッドレスでは運用者が見る唯一の画面のため）・`logs/autona.log`・`logs/tsh-error.log`・`logs/tsh-crash.log`（faulthandler）
  - `src/AutonaWebOperations.py`（新規）: Web の操作のうち、ボードのウィジェットが要らないもの（モジュール関数）
    - core 直結: `program_state` / `team_scoreup` / `team_scoredown` / `team_color` / `set_display_option` / `set_scores` / `apply_changes` / `get_games` / `get_current_game` / `get_match_names` / `get_characters` / `get_variants` / `get_comms` / `get_states` / `get_sets` / `get_match` / `get_playerdb` / `load_tournament` / `set_game` / `load_game`（新規。v1 `game` が使う）
    - S2 で core 化した操作: `set_route`（+ `_apply_set_fields`）/ `set_team_losers` / `swap_teams` / `get_swap` / `get_scoreboard` / `reset_scores` / `reset_match`（旧 `_reset_best_of` は `_reset_match` に統合）/ `reset_players` / `clear_all` / `get_set`
    - ボードの周りの仕事はシェルが登録する `boardHost` が受け持つ。`BoardHost`（既定 = ヘッドレス）: 呼び出し元のスレッドで、1 本の再入可能ロック（`RLock`）の下で 1 つずつ実行・何も構築しない・ウィジェットが要るもの（board 3 の操作・人数 / キャラ数）は `NotAvailable`。`DesktopBoardHost`（`bootstrap_gui()` が登録）: GUI スレッドで同期実行・未構築ボードを構築・入力中の欄の確定・board 3 はウィジェットへ・`CommandClearAll`・人数 / キャラ数を 1 に・最後に読んだセット
    - `NotAvailable`（message `not available in headless`）
  - `src/AutonaWebServerActions.py`: ボード依存の操作だけに縮めた（下の表）。werkzeug のアクセスログ抑制は `AutonaWebServer.py` へ移した（ヘッドレスはこのモジュールを import しないため）
  - `src/AutonaWebServer.py`
    - ルートと Socket.IO イベントは `WebOps.*` か `WebServer._board_actions().*` を呼ぶ。`_board_actions()` は `WebServer.actions` が None なら `NotAvailable`
    - 503: legacy は `@app.errorhandler(NotAvailable)`、Socket.IO は `ws_on` が全ハンドラを `_ws_unavailable` で包んで ack に本文を返す。本文は v1 のエラー形 `{"ok": false, "error": {"code": "UNAVAILABLE", "message": "not available in headless"}}`
    - `WebServer(on_start_failed=None, actions=None)`: `parent` 引数を削除し、ボード依存の操作はシェルが渡す（前提 2。サーバーはスコアボードを知らなくなった）
    - `ws_game_changed()`（新規）: `ws_program_state` + `ws_get_characters` をまとめたもの（`GameChanged` の受け口）
    - `log_webserver_port(port, host)`（新規）: `check_webserver_port` と同じ確認をして、ダイアログの代わりにログに出す（何も終了させない）
  - `src/AutonaApiV1.py`
    - `UNAVAILABLE`（503）を追加。`api_handler` / `write_op` が `NotAvailable` をこのコードに変える（操作ログにも残る）
    - `_actions_ref()`: `init_api_v1(None)` 後（= ヘッドレス）は `NotAvailable`、初期化前は従来どおり `NOT_READY`
    - core の操作は `WebOps` を呼ぶ。`PATCH set` は 1 つのタスクを `WebOps.boardHost.Run` で実行。`game` は `WebOps.load_game`。`bracket:refresh` は provider の確認より先にボード依存の確認をする
  - `src/bootstrap.py`
    - `bootstrap_gui()`: `AutonaPlayerDB.EnableModel()`（`SetupModel` の結線の前）。最後に `AutonaWebOperations.boardHost = DesktopBoardHost(AutonaScoreboardManager.instance)` と `loadGameByCodename = AutonaGameAssetManager.instance.LoadGameByCodename`
    - `wire_tournament_data(on_failure)`（新規）: `tournament_data_updated` → `TournamentInfoService.ApplyProviderPayload`（失敗時 `on_failure`）、`tournament_url_update("")` → `GameAssetCatalog.ForgetSelection`
    - `wire_web_server(webserver, game_changed)`（新規）: `game_changed` → `ws_game_changed`、`db_updated` → `ws_playerdb`
  - `src/Autona.py`: 上の 2 つの結線関数を呼ぶ（Window の `_OnTournamentDataUpdated` は失敗ダイアログだけの `_ReportTournamentLoadFailure` に、`Signal_GameChange` からは `ForgetSelection` を外した）。`WebServer(..., actions=WebServerActions(parent=self, scoreboard=...))`
  - `src/AutonaPlayerDB.py`（前提 1）: `modelEnabled`（既定 False）と `EnableModel()`。`SetupModel()` はモデルが無効なら `db_updated` を出すだけ。モデルの構築は `_BuildModel()` に分けた。start.gg の entrants 取得はヘッドレスでも DB を更新し、`SetupModel()`（GUI スレッドへ marshal）で QtGui を作らない
  - `src/core/TournamentInfoService.py`: `ApplyProviderPayload(data)`（ペイロードの適用 + 表示大会名の上書き。2 つのシェルが同じ順で呼ぶ）
  - `src/core/StateManager.py`: `JoinExports(timeout)`（それまでに積まれた `out/` の書き出しを待つ。閉じるボタンで外から終わらされるヘッドレスが使う）
  - `src/Helpers/AutonaLocaleHelper.py`: `LoadLocale` の翻訳の差し替えを `QCoreApplication` の static で行う（QTranslator は QtCore。ヘッドレスでも同じ経路）
  - `src/AutonaApiInfoWindow.py`: エラーコードの凡例に `UNAVAILABLE` を足した
  - コメント追従: `src/AutonaCrewBattleWidget.py`、`src/core/SetLoadService.py`
- **ヘッドレスで動くもの / 503 になるもの**

  | 区分 | 操作（legacy / Socket.IO / v1） | ヘッドレス |
  |---|---|---|
  | 表示 | OBS オーバーレイ・`/web`・`out/` の出力・Socket.IO の state / delta 配信・state / カタログ / 選手 DB の読み取り | 動く |
  | core 直結 | スコア ±・スコアの設定・スコアリセット（board 1/2）・反映・チームカラー・表示トグル・ゲームの一覧 / 切り替え・国 / 地域・セット一覧 / 詳細・大会 URL | 動く（即時公開） |
  | S2 の操作（board 1/2） | swap（`get_swap` 含む）・リセット / クリア（選手の中身を空にする）・best of・phase / pool / match・LOSERS・チーム名・`PATCH set`・`get_scoreboard`・`get_set`（常に `"0"`） | 動く（即時公開） |
  | ウィジェットが要る S2 の部分（legacy / Socket.IO） | クルーボード（board 3）の swap / `get_swap` / リセット / クリア / set フィールド、`set_route` の `players` / `characters`（人数・キャラ数） | 503（何も書かない） |
  | ボード依存（`WebServerActions`） | 選手の編集（`update-team`・`PATCH players`）・`POST /score`（と Socket.IO の `score`）・セット選択 / 読込 / 次のセット（`open-set`・`load-set`・`pull-stream`）・統計（各 `stats-*`・`stats:refresh`）・サムネイル・Top 8 の取り込み（`update-bracket`・`bracket:refresh`）・実況の更新・タグからの選手 / 実況者の読込（`load-player-from-tag`・`load-commentator-from-tag`）・クルーのストック / アクティブ | 503 |

  v1 のクルーボード（board 3）への書き込みは、起動形態によらず `require_board_12` が 400 `INVALID_ARGUMENT` で断る（v1 の初期範囲外。変更なし）。上の表の「クルーボードの 503」は legacy ルートと Socket.IO だけの話。v1 の `PATCH set` の `players` / `characters` はボード 1/2 宛てなので、ヘッドレスでは 503 `UNAVAILABLE` になる

- **`WebServerActions` に残したもの（ボード依存・S3e で廃止）**: `post_score` / `crew_member_stock` / `crew_set_active` / `set_team_data` / `update_player` / `set_commentary_data` / `open_sets` / `pull_stream_set` / `stats_recent_sets` / `stats_upset_factor` / `stats_last_sets` / `stats_history_sets` / `refresh_stats` / `get_thumbnail` / `update_bracket` / `load_set` / `load_player_from_tag` / `load_commentator_from_tag`（公開 18 本）と `_ensure_panel`。設計時の見積り「残る 26 本」はルート単位の数え方で、メソッドでは 18 本（同じ操作を受けるルート / イベントが複数ある）
- **判断と理由**
  1. **Web の操作は 2 つのシェルで同じ関数を通し、違いは 1 つのオブジェクト（ボードホスト）に閉じ込めた**
     - S2 の操作は core の呼び出し自体は同じで、デスクトップが周りに足すもの（GUI スレッド・未構築ボードの構築・入力中の欄の確定・クルーボード）だけが違う。操作ごとに `if headless` を書くと 2 つの経路がずれていくので、その差をホストの 6 メソッドに集めた
     - `tests/test_web_operations.py` が、10 種の操作で「デスクトップ（ボード構築・held）とヘッドレス（ボードなし・即時公開）が同じ値を書く」ことを確かめる
  2. **503 の本文は v1 のエラー形に揃えた**（legacy / Socket.IO / v1 で同じ）。S3a の拒否（401 / 403）と同じ形なので、クライアントは 1 つの形だけを見ればよい
  3. **`players_per_team` と `RepublishBestOf` はヘッドレスでは要らない**
     - デスクトップはゲームのロード時に、ボードがゲームの既定の人数を適用し、best of の文言を新しいロケールで出し直す（S2g / S2e。構築済みボードの反応）
     - ヘッドレスにはボードが無く、人数はボードのスピンボックスにしか無い（S2g で core 化するまで、ヘッドレスは人数を変えない）。best of の文言はゲームではなく起動時のロケールで決まり、ヘッドレス中にロケールは変わらない。行（`stages`）のリセットはトラッカー（ボードが登録する）の仕事で、ヘッドレスには行が無い
  4. **ヘッドレスの `clear_all` は人数・キャラ数を変えない**（ボードが無い。S2g）。チーム名は従来どおり残す
  5. **ヘッドレスの選手 DB は mains の正規化をしない**。デスクトップはモデルの構築のついでに、ロード中のゲームの mains を 3 要素に揃えて DB に書き戻している（QtGui の構築の中にある）。ヘッドレスでは `/playerdb` が保存されたままの mains を返す
  6. **`set_route` の `players` / `characters` はヘッドレスでは最初に断る**（他のフィールドを書く前に 503）。一部だけ書いて 503 を返すと、クライアントからは何が適用されたか分からないため
  7. **`get_set` はヘッドレスでは常に `"0"`**（読み込んだセットが無い = 旧実装の「無し」の答え）。クルーボードも同じ（デスクトップのクルーボードは `setLoad` が無く例外になるが、ヘッドレスでは聞く相手がいない）
  8. **LAN 公開とポートは設定値で決め、ログに出すだけにした**（設計どおり）。`general.webserver_listen_lan` が未設定ならこの PC のみで待ち受け、案内をログに出す。Web サーバーが起動できなければ終了コード 1 で終わる（待ち受けていないのに単一インスタンスのロックを握り続けるのを避ける）
  9. **ヘッドレスの 2 つ目の起動は終了コード 1**。デスクトップ版は 2 つ目を黙って前面化で済ませるが、ヘッドレスを起動したスクリプトやサービスには「起動しなかった」が伝わる必要がある
  10. **`load_tournament` の死にコード（結果を使っていない `QRegularExpression` のループ）を削除した**。core 直結の関数に移すにあたって QtCore への依存だけが残るため
  11. **大会 URL を消したときの `ForgetSelection` は共通の結線（`wire_tournament_data`）に移した**。Window の `Signal_GameChange` はゲームボタンの表示だけを戻す。接続順は Window の読み手より前のまま
  12. **ヘッドレスの S2 の操作は 1 本のロックで直列化した**（S3c-2 レビュー）。デスクトップは GUI スレッドに並べるので、`clear_all`（4 保存サイクル）や v1 の `PATCH set`（1 タスクで最大 3 呼び出し）の途中に別の要求のボード操作が入らない。ヘッドレスはリクエストのスレッドで直接走るので、ロックが無いと混ざる。`RLock` にしたのは、`PATCH set` のタスクがその中で `set_route` を呼び、`set_route` も `Run` を通るため。ロックを取るのは Web のリクエストのスレッドだけで、`StateManager.lock` を持ったまま取る経路も、ロックの中でメインスレッドを待つ経路も無い（ヘッドレスでは何もメインスレッドへ marshal しない）ので、常に一番外側のロックでデッドロックしない。core 直結の書き込み（`score:adjust`・色）はボード操作ではないので、両シェルとも従来どおり途中に入りうる
  13. **`Autona.exe --headless` は拒否し、mutex を取る前に終わる**（S3c-2 レビュー）。exe は GUI サブシステムでコンソールが無く、黙って起動するとログも停止手段も無いプロセスが mutex を握り、後からダブルクリックした GUI が「2 つ目の起動」として無言で終わる。メッセージボックスで伝えるだけにした
  14. **`AUTONA_INSTANCE_TAG` はソースからの起動でだけ読み、英数字とハイフンに限った**（S3c-2 レビュー）。配布版に環境変数が残っていても、インストール済みの Autona が 2 つ目を通すことがないように。値は mutex 名とパイプ名の一部になるので文字を絞る
  15. **停止ハンドラは組み立ての前に入れた**（S3c-2 レビュー）。組み立て中の Ctrl+Break を、Windows の既定動作（その場で終了）ではなく、組み立て後の同じ停止処理（書き出しを待つ）で受けるため
- **外部から見える変化**
  - `python main.py --headless` が使えるようになった（運用手順は [runtime-operations.md](../operations/runtime-operations.md) の「ヘッドレス起動」）
  - デスクトップ版の挙動・Web の契約は変わらない。v1 に `UNAVAILABLE`（503）が増えたが、デスクトップ版では返らない
  - Window の大会読込失敗のダイアログ・ゲームボタンのリセットは従来どおり
  - v1 の `GET /scoreboards/3` の `swapped` は、ヘッドレスでは返らない（キーが無い。デスクトップはクルーウィジェットのフラグを返す）。ボード 1/2 はどちらのシェルでも state の `score.{n}.teamsSwapped` を返す（受容。`external-control-api.md` §5）
- **pin**
  - `tests/test_web_operations.py`（新規・38 件）: 2 つのホストが同じ state を書く（10 操作。デスクトップ側は反映待ちになる）。ヘッドレスの書き込みが即時公開・選手のクリア・`get_set` が `"0"`・ウィジェットが要る操作（9 種）が `NotAvailable` で何も書かない。legacy ルートの 503・Socket.IO の ack・v1 の `UNAVAILABLE`（`bracket:refresh` / `:load-set` / `:pull-stream` / `stats:refresh` / `PATCH players`）・`_board_actions()`。Web 操作自身が投げる `NotAvailable`（クルーボードの swap / `get_swap`・人数）も legacy は 503、Socket.IO は ack。ヘッドレスのボードホストが 1 つずつ実行する（入れ子は再入できる）・`clear_all` の途中に来た swap は clear の後に走る（ロックを外すと両方落ちることを確認済み）
  - `tests/test_headless.py`（新規・22 件）: 別プロセスで `headless.Assemble()` を実際に動かす。ゲームの選択関数・ローダー・ボードホストの登録（**選択関数の登録漏れの pin**）。デスクトップのアダプタを作らない。PlayerDB のモデルを作らない。スキャン → 前回のゲームの復元 → カタログ経由のロード → 選択の保存 → `gameChanged` / `db_updated`。再スキャンで provider のゲームを適用し直す（復元は初回だけ）。大会ペイロードの書き込みと URL を消したときの `ForgetSelection`。停止で `state.json` が書かれる。`Assemble` の中で、登録と結線が `UiMounted` / スキャン / サーバー起動より前（AST）。停止まわり: 停止要求は queued の `quit` で、ループ前の要求も残る・停止ハンドラが組み立ての前・組み立て中の停止でループを回さずに停止処理へ・`Shutdown()` が書き出しの後でハンドラを解除する・Web サーバーを作る前の停止でも書き出す。起動時チェック: `AUTONA_INSTANCE_TAG` の検証（英数字とハイフン / それ以外は warning で無視 / frozen では読まない）・`Autona.exe --headless` の拒否（`sys.frozen` を立てて `main.py` を実行し、メッセージボックスを差し替えて確認。単一インスタンスの判定に届かず、`logs/`・`user_data/`・`out/` を作らない）
  - `tests/test_headless_smoke.py`（新規・1 件）: `python main.py --headless` を実際に起動（tmp CWD・空きポート・`AUTONA_INSTANCE_TAG`）。health・Socket.IO（polling）で state と delta・`score:adjust` の即時公開・503・転送ヘッダ付きの書き込みが 401・2 つ目の起動が終了コード 1・Ctrl+Break で終了コード 0・`state.json` と `program_state.json` に値が残る。プロセスは PID でのみ止める
  - `tests/test_bootstrap_split.py`: `bootstrap_gui()` の手順に `EnableModel`・ボードホスト・ローダーの登録を追加。共通の結線の手順と、Window とヘッドレスがどちらも 1 回ずつ呼び、自分では結線しないこと（AST）。core / GUI の各段のボードホスト・ローダー・PlayerDB のモデルの状態
  - `tests/test_import_side_effects.py`: `AutonaWebOperations` / `headless` の import が何も登録せず、`logs/` も作らない
  - 既存テストの追従: core に移った操作の呼び出しを `autonaApp.web()`（新規の harness アクセサ）へ（`test_scoreboard_operations` / `test_crew_board` / `test_player_card_render` / `test_country_helper_model` / `test_game_asset_manager_models`）。AST の pin を新しい場所へ（`test_tournament_info_panel` / `test_stats_util` / `test_country_helper_model` / `test_game_asset_manager_models`）。`test_web_access` の偽の操作を `AutonaWebOperations` に差し込む
- **残置 / リスク**
  - **exe でのヘッドレスは対象外**（PyInstaller の spec・自動更新は触っていない）。`Autona.exe --headless` は拒否する（判断 13）。exe で動かすには、GUI サブシステムの exe なのでコンソールが無く、ログも停止手段も別に考える必要がある
  - **ヘッドレスの `set_game`（legacy の `POST /update-game` と Socket.IO の `update_game`）はロードの間メインループを止める**（受容）。`@gui_thread_sync` でメインスレッドに渡し、同期でロードするのが旧ルートの契約（ロード完了後に `"OK"`）のため。その間は delta の配送と停止要求の処理が止まる。デスクトップもロードの間 GUI が固まる点では同じ。v1 の `POST /game` は非同期（202）なので止めない
  - **停止時、`run_sync` で GUI スレッドを待っているリクエストのスレッドは待ち続ける**（既存の構図。デスクトップも同じ）。イベントループが止まった後は誰も実行しないが、werkzeug のリクエストのスレッドは daemon なのでプロセスの終了は妨げない（そのリクエストには応答が返らない）
  - ヘッドレスのボードは skeleton（デスクトップの未構築ボードと同じ）。選手スロット・`losers` などのキーは最初の書き込みで現れる。オーバーレイは未定義を空として扱う前提 **→ S3d で訂正**: この前提は成り立っておらず（スコア系オーバーレイは選手スロットが無いと描画しない）、S3d でヘッドレスがデスクトップの空ボードと同じ選手スロットをシードするようにした
  - Web UI（`/web`）は 503 を専用に扱っていない（一般のエラーとして出る）。ボード依存の操作のボタンをヘッドレスで隠すのは S4 の UI 側の仕事
  - コンソールの閉じるボタンで止めたときの書き出しは、Windows の猶予（約 5 秒）の中で終わる前提（通常は 1 秒未満）。タスクマネージャーでの強制終了は書き出しを待たない
  - Windows の `SIGTERM`（`os.kill` / `taskkill /F`）は TerminateProcess で捕まえられない。サービス化するなら `taskkill`（/F なし）ではなく Ctrl+Break を送る仕組みが要る
  - pytest 全量は実装担当では回していない（レビュー時の全量は 1909 passed。レビュー修正後の全量は verify 担当）
- **実機確認（オーナー）**
  - デスクトップ版（`python main.py`）
    - 起動して OBS のオーバーレイ・`/web`・API Tester が今までどおり動く。Web からの swap / リセット / クリア / チーム名 / best of / LOSERS が今までどおり（反映待ちになる）
    - 大会 URL を設定するとゲームが切り替わる。URL を消してから同じゲームを選び直すとロードされる
    - 大会 URL の読込に失敗するとダイアログが出る
    - 閉じるとプロセスがすぐ終わる
  - ヘッドレス（`python main.py --headless`。デスクトップ版は閉じておく）
    - コンソールに `headless: serving http://127.0.0.1:5107/` が出て、OBS のオーバーレイと `/web` が表示される。前回のゲーム・大会情報が出ている
    - `/web` からスコア ± / swap / リセット / チーム名を操作するとオーバーレイがすぐ変わる
    - `/web` の選手編集・セット読込は失敗する（503）。アプリが落ちない
    - Ctrl+C で止まる。もう一度起動すると、大会情報と前回のゲームは残り、スコア（スコアボード）は消えている（デスクトップ版と同じ）。Ctrl+Break・コンソールの閉じるボタンでも同じ
    - （次に exe をビルドしたとき）`Autona.exe --headless` を実行するとメッセージボックスが出て起動せず、続けて普通にダブルクリックした `Autona.exe` は通常どおり起動する
    - ヘッドレス稼働中にもう 1 つ `python main.py --headless` を起動すると「already running」で終わる。デスクトップ版を起動すると何も出ずに終わる

#### S3d E2E（2026-10-26・PR #231）

S3 の最後。ヘッドレスの実プロセスに対して、Python のスモークの穴を埋め、ブラウザの E2E（Playwright・Chromium）を足した。E2E は `/web` とオーバーレイ 1 枚を開き、Web からのスコア +1 が state とオーバーレイに届くことを確かめる。**この E2E で、ヘッドレスの `/web` とスコア系オーバーレイが実際には使えないことが分かり、同じ S3d で直した**（下の「E2E で見つかった問題と修正」。`/web` のガードと、ヘッドレスが core でデスクトップの空ボードと同じ選手スロットをシードする）。

- **実装**
  - `tests/test_headless_smoke.py`: 設計の確認項目（health・Socket.IO 接続・`score:adjust`・delta・トークンなしの LAN 書き込みの拒否・`state.json`）のうち、拒否は HTTP の 1 件だけだった。Socket.IO でも拒否されること（中継された接続の書き込みイベント `team_scoreup` が ack で `UNAUTHENTICATED`、state は変わらない）と、初回起動で発行されたトークン（tmp の `settings.json`）を付けると同じ中継の書き込みが通ることを足した
  - `web/e2e/`（新規）
    - `headless_server.py`: ヘッドレスの起動・停止を受け持つヘルパー（テストではない。pytest は `tests/test_*.py` しか集めない）。スモークと同じ隔離を、スモークの関数をそのまま import して使う: `_runtime()`（tmp の作業ディレクトリ + junction）・`_free_port()`・`_launch()`（`AUTONA_INSTANCE_TAG`・`CREATE_NEW_PROCESS_GROUP`）・`_wait_until_serving()`・settings（ポートと `webserver_listen_lan: false`）。その上に、ブラウザが読む `web/build`・`layout/include`・`layout/scoreboard_simple` を junction で、`layout/main.css`・`layout/settings.json` をコピーで足す（読むだけ）
      - stdout に 1 行ずつ: `AUTONA_E2E_READY {url, pid, dir}` / `AUTONA_E2E_FAILED ...`（コンソールの末尾付き）/ `AUTONA_E2E_STOPPED {exit, killed}`。ヘルパー自身の終了コードは正常停止（exit 0・kill なし）のときだけ 0
      - READY の後は stdin の `stop` か EOF を待つ（呼び出し側が落ちて stdin が閉じても止まる）。止めるのは Ctrl+Break（起動したプロセスグループだけ）、60 秒で終わらなければその PID だけ kill。`poll()` と Ctrl+Break の間に終わっていた場合（送信が `OSError`）は、終了済みなら正常停止として扱い、まだ動いていれば kill する
      - 後始末: 足した junction を外し、`_runtime()` が自分の junction を外してから tmp を消す。junction が 1 つでも残っていたら tmp を消さない（`rmtree` が junction の先の `web/build`・`assets/` に降りないように）。ヘッドレスのコンソールログは `web/test-results/headless-console.txt` にコピーする
    - `global-setup.ts`: `web/build/index.html` が無ければ「先に `npm run build`」で止める。ヘルパーを起動して READY を待ち（150 秒）、URL を `AUTONA_E2E_BASE_URL` でテストに渡す。teardown（global setup の戻り値）は `stop` を送って 90 秒待つ。終わらなければ、ヘルパーと、ヘルパーが報告したヘッドレスの 2 つの PID だけを kill して失敗にする。終わっても、`AUTONA_E2E_STOPPED` が `{"exit": 0, "killed": false}` でない・ヘルパーの終了コードが 0 でないときは失敗にする（teardown の例外で Playwright の run は exit 1 になることを確認済み）。ヘルパーの stdin には `error` ハンドラを付け、閉じたパイプへの書き込み（EPIPE）で落ちない
    - `server.ts`: URL の組み立て・外部ホストへの要求の遮断（`/web` と オーバーレイが読む Google Fonts）・state の読み取り・ボード 1 を 0-0 にする（`score:set`。下の判断 4）
    - `web-and-overlay.spec.ts`（2 件）
      - (a) `/web` が開き（タイトル・Swap Teams・Clear All・+1 が 2 つ）、+1 を押すと state のスコアが `[1, 0]` になる
      - (b) `layout/scoreboard_simple` を開いて Socket.IO の接続を待ち、`/web` で +1 → オーバーレイの `.p1 .score` が `1`、`.p2 .score` が `0`
      - `/web` とオーバーレイの未捕捉の例外（`pageerror`）は afterEach で失敗にする（React は例外 1 つでページ全体を消し、オーバーレイは例外でそのイベントの描画を止めるので、原因をそのまま出す）
  - `web/playwright.config.ts`（新規）: `testDir: e2e`・Chromium のみ・workers 1（サーバーと state を共有）・retries 0・trace は失敗時だけ残す・HTML レポート・`locale: en-US`（Web UI はブラウザの言語で文言を選ぶ）
  - `web/package.json`: devDependency `@playwright/test` **1.62.1**（exact。2026-07-30 公開。最新の 1.64.1 は 2026-10-14 で 2 週間未満）、スクリプト `test:e2e`（`playwright test`）。`package-lock.json` の差分は `@playwright/test`・`playwright`・`playwright-core`・`fsevents`（darwin 用の optional）の追加だけ
  - `web/.gitignore`: `/test-results/`・`/playwright-report/`
  - `.github/workflows/ci.yml`: `e2e` ジョブ（windows-latest・20 分）。Python 3.12 + `requirements.txt` + pytest → Node 20 → `npm ci` → `npm run build`（`CI=false`）→ `npx playwright install chromium` → `npm run test:e2e`（`CI=true`）。失敗時は `web/playwright-report/` と `web/test-results/`（trace とヘッドレスのコンソールログ）を artifact に上げる（7 日）。既存の python / web ジョブは変えていない
  - `tests/` は `web/e2e/` を集めず、`react-scripts test`（jest の roots は `src/`）も `web/e2e/` を集めない（`--listTests` と `pytest --collect-only` で確認）。`tsconfig.json` の `include` は `./src` のままなので、`npm run build` の型検査にも入らない（Playwright が自分で TypeScript を変換する）
  - E2E で見つかった問題の修正（下の「E2E で見つかった問題と修正」）
    - `web/src/ScoreboardPage/Team.jsx`: `Object.keys(team.player ?? {})`。`ScoreboardPage/` で `team.player` を `Object.keys/entries/values` に渡すのはここだけだった（`CurrentSet.jsx` は直前の `if (!team?.player)` で、`ScoreControlBar.jsx` は `team.player || {}` で守られている）。`Team.test.js`（新規・1 件）: `player` の無いチームでも列（`1P`・チェックボックス）が描画される
    - `src/core/PlayerService.py`: `SeedEmpty()`（1 スロットを空のカードが publish するのと同じ内容で書く: seed 0・birthday False・country `{}`・state `{}`・キャラ 3 枠と mains。既存の setter をデスクトップのカードと同じ順で呼ぶので、キーと値の形・キー順が同じになる）、モジュール関数 `SeedEmptyRoster(board, players=None, characters=3)`（両チームの 1..players を書く。スロット順・チーム順はデスクトップの構築順）、`DefaultPlayersPerTeam()`（`game.defaults.players_per_team`、`game.defaults` が無ければ 1）と `DEFAULT_CHARACTERS_PER_PLAYER = 3`
    - `src/core/CrewBattleService.py`: `DefaultTeams()`（既定色 2 チーム・メンバー 9 人・名前空・ストック 3）と `SeedDefaults()`（`SeedBoardState(DefaultTeams(), DEFAULT_MEMBER_NUM, 0)` = クルーボードの起動時の書き込み）
    - `src/headless.py`: `_SeedBoards()`（`ResetScoreRoot` → `SeedBoard(1..3)` → `SeedEmptyRoster(1)` / `SeedEmptyRoster(2)` → `CrewBattleService(3).SeedDefaults()`）。`_SeedState()` の 1 つの `SaveBlock` の中で、パネルのシードの前に呼ぶ（= 1 保存サイクル）。モジュールの docstring も実態に合わせた
    - `src/AutonaScoreboardWidget.py`: `SetDefaultsFromAssets` の人数・キャラ数を core の `DefaultPlayersPerTeam()` / `DEFAULT_CHARACTERS_PER_PLAYER` から取る。構築時のキャラ数の `setValue(3)` 2 箇所（スピンボックス生成時と、選手カード生成前の再設定）も `DEFAULT_CHARACTERS_PER_PLAYER` にした（挙動は同じ。既定値の出どころを 1 つにした）
    - `tests/test_headless_seed.py`（新規・4 件）: デスクトップの空ボード（`autonaApp.board(1..3)` が publish した state）と `_SeedBoards()` の publish を比べる。board 1/2 は各チームの `player` サブツリー、board 3 はボード全体を、値とキー順（`json.dumps` の一致）で照合。ゲームなし（1 人）と `players_per_team: 2` の 2 通り。加えて、シードが `ResetScoreRoot` の後であること（復元された余分なスロットが残らない）と、人数・キャラ数の明示指定
    - `web/e2e/web-and-overlay.spec.ts`: (b) の `test.fail` を外し、普通に検査する
- **判断と理由**
  1. **起動・停止は Python のヘルパーにした（Playwright の `webServer` ではなく）**
     - スモークの隔離（junction の一覧・tmp の作り方・起動の仕方）を import で共有でき、TypeScript に同じ一覧を書き直さずに済む
     - 正規の停止（Ctrl+Break。書き出しを待って終了コード 0）は Node から送れない
     - `webServer` の停止は Windows では `taskkill /T /F` で、ヘルパーの後始末（junction を外す）が走らない。junction が残った tmp を誰かが再帰削除すると、リポジトリの `web/build`・`assets/` に降りる
     - stdin の EOF で止まるので、Playwright が途中で落ちてもヘッドレスが残らない
  2. **LAN 側の IP からの拒否は、実際の LAN では再現しない**: 1 台の PC では他端末の送信元 IP を作れない（自 PC の LAN IP 宛ては「この PC」として免除される）。実プロセスでは中継ヘッダ（`X-Forwarded-For`）で「他端末扱い」の同じ拒否の経路を通し、アドレスの判定そのものは `tests/test_web_access.py` が Flask の test client の `REMOTE_ADDR` で担保している（`test_lan_writes_and_the_player_db_need_the_token`・`test_another_lan_address_is_not_this_pc`・`test_this_pc_under_its_lan_address_needs_no_token`・`test_a_lan_connection_without_the_token_is_read_only`・`test_the_token_lets_a_lan_connection_write`）
  3. **オーバーレイは `scoreboard_simple`**（1v1 の素のスコアボード）。スコア系のオーバーレイ（`scoreboard_simple` / `scoreboard_Universal` / `scoreboard_team` / `scoreboard_topleft`）はどれもチーム 1 の選手スロットを前提にしていて、下の問題 2 は共通
  4. **各テストの前にボード 1 を API（`score:set`）で 0-0 にする**。ヘッドレスのシードは選手スロットまでで、スコアは書かない（デスクトップではスコアのスピンボックスが publish する。下の判断 9）。2 件のテストが 1 つのヘッドレスを共有するので、前のテストの +1 を持ち越さないためにも要る
  5. **(b) は普通に検査する**。修正前は `test.fail(条件)`（ボード 1 のチーム 1 に選手スロットが無い）で既知の失敗として固定していたが、シードを入れたので外した
  6. **外部への要求は遮断した**（Google Fonts）。オフラインでも CI のネットワークが遅くても結果が変わらないように
  7. **スクリーンショット比較はしない**（指示どおり）。文言・DOM・state だけを見る
  8. **CI は Windows**（配布と同じ OS。ヘルパーの Ctrl+Break も Windows のコンソールにしか無い）。Playwright のブラウザはキャッシュしない（毎回ダウンロード。必要になったら足す）
  9. **シードするのは「デスクトップの空ボードが publish する選手スロット」まで**（メインの判断。形の正本はデスクトップの空ボードの `score.N.team.T.player.M.*`）。board 1/2 のチーム単位のキー（color・teamName・losers・score）とボード単位のキー（match バー・best_of の文言・stages）はボードのウィジェットが publish するもので、シードしない（オーバーレイと `/web` が描画に要るのは選手スロット。チーム単位の値は最初の操作で書かれる）。クルーボード（board 3）はボード全体をシードする（デスクトップでも起動時の書き込みが core の 1 呼び出し = `CrewBattleService.SeedBoardState` なので、同じ呼び出しで同じ state になる）
  10. **3 面ともシードする**。デスクトップは表示した面だけを構築し、未構築の面は skeleton のまま（構築時にシードで上書きされる）。ヘッドレスは面を構築しないので、起動時に 3 面とも構築直後の形にする。人数・キャラ数は `ResetScoreRoot` の後・ゲームの復元の前に決まる（起動時の `game` は空なので 1 人 / 3 枠。デスクトップもボード構築時点では同じで、ゲームのロード後に人数を変えるのはボード側の反応 = S2g）
  11. **既定値の出どころは core の 1 箇所**。デスクトップの `SetDefaultsFromAssets` も `DefaultPlayersPerTeam()` / `DEFAULT_CHARACTERS_PER_PLAYER` を読むようにした（挙動は同じ。片方だけ既定値を変えて形がずれないように）。デスクトップの書き込み経路（カードが publish する）は変えていない。同等性は `tests/test_headless_seed.py` が pin する
- **E2E で見つかった問題と修正**
  1. **`/web` が真っ白になる**（`TypeError: Cannot convert undefined or null to object`）→ **修正済み**
     - 起きる条件: ボードに 2 チームあり、選手スロット（`team.{t}.player`）が無い。ヘッドレスでは（シード前は）スコアを 1 回書いた時点でこうなった
     - 原因: `web/src/ScoreboardPage/Team.jsx` の `Object.keys(team.player)`。React は例外 1 つでページ全体を消す
     - デスクトップでも起きうる: 未構築のボード（1v1 のときの board 2）に v1 の `score:set` を送ってから `/team` を開く。シードとは別に、Web UI 側でガードした
  2. **ヘッドレスではスコア系のオーバーレイが何も描画しない** → **修正済み**（ヘッドレスのシード）
     - 原因: オーバーレイはチーム 1 の選手スロットが無いと描画を始めない（`if (... || !sb.team["1"].player) return;`）。選手スロットを作るのはデスクトップの選手カードだけで、ヘッドレスのボードには無く、ヘッドレスで作る API も無い（`PATCH players` は 503、クリア系は既存のスロットしか触らない）
     - S3c-2 の「残置」にある「オーバーレイは未定義を空として扱う前提」は成り立っていなかった
  - 修正後、`npm run test:e2e` は 2 件とも通る。**S3 の完了条件「ヘッドレスで OBS オーバーレイと `/web` の表示、core 直結の操作が動く」を E2E で確認できた**（自動テストの範囲。実機は下の確認項目）
- **残置 / リスク**
  - **CI での実行は未確認**（手元の Windows でだけ回した）。CI ランナーのコンソールで Ctrl+Break が届くことは、python ジョブの `test_headless_smoke.py` と同じ前提
  - Windows 専用（ヘルパーは Windows 以外では `AUTONA_E2E_FAILED` で止まる）
  - ヘルパーは `tests/` の関数（`_runtime`・`_free_port`・`_link_tree`・`_LINKED_TREES`・`_launch`・`_wait_until_serving`）に依存する。名前を変えると E2E が起動しなくなる（`_launch` / `_wait_until_serving` の共有はスモークの docstring に書いた）。この import のため e2e ジョブにも pytest を入れている
  - ヘッドレスのコンソールに、ブラウザが WebSocket を閉じたときの werkzeug の `AssertionError: write() before start_response` が出る（既存のノイズ。テストの結果には影響しない）
  - ヘルパー自身が強制終了された場合（Playwright の teardown が 90 秒で諦めたとき以外）は、ヘッドレスが残りうる。teardown が諦めたときは報告済みの 2 つの PID を kill する
  - 同じくヘルパーが強制終了されると、junction 付きの `%TEMP%\autona-e2e-*` が残りうる（junction の先はリポジトリの `web/build`・`layout/`・`assets/` など）。**junction を辿るクリーナー（`rm -rf`・`shutil.rmtree`・エクスプローラーの削除など）で消すと、リポジトリ側の中身まで消える危険がある**。先に中の junction を `rmdir`（junction だけを外し、先は消さない）で外してから、残ったディレクトリを消す
  - teardown は正常停止でなければ run を失敗にする（ヘルパーの `AUTONA_E2E_STOPPED {"exit": 0, "killed": false}` と、ヘルパー自身の終了コード 0 の両方を確認。kill になった・終了コードが 0 以外なら失敗）
  - ヘッドレスの人数は起動時の 1 人 / チームのまま。2v2 のゲームを読んでもスロットは増えない（ゲームのロードへのボードの反応 = S2g はヘッドレスに無い。S3c-2 判断 3 の残置と同じ）。人数を変える Web の入口（ボードのスピンボックス相当）もヘッドレスでは 503
  - board 1/2 のチーム単位・ボード単位のキーはシードしないので、ヘッドレスの起動直後の state はデスクトップの空ボードより少ない（判断 9）。それを前提にしたオーバーレイは、最初の操作まで描画が欠ける。スコア系 4 枚はスコアが無ければ 0 と描く（`scoreboard_topleft` だけ `String(team.score)` で `undefined` と出ていたので、ほかの 3 枚と同じ `?? 0` にそろえた。デスクトップはボードがスコア 0 を publish するので元から出ない）
  - シードは起動時の 1 回だけ。ヘッドレスで `PATCH players` 相当がなく、クリア系は既存のスロットしか触らないので、スロットを消す経路も増やす経路も無い（シードで入ったスロットが残り続ける）
- **実機確認（オーナー）**
  - ヘッドレス（`python main.py --headless`）を起動し、OBS で `layout/scoreboard_simple` を開く → 起動直後から空のスコアボード（名前空・スコア 0-0）が描画される
  - その状態で `/web` を開く → 真っ白にならず、両チームの列（1P）とスコア操作が出る。+1 / −1 を押すとオーバーレイがすぐ変わる（S3c-2 の実機確認と同じ操作）
  - デスクトップ（`python main.py`）は挙動が変わらないこと: 起動直後のボードの選手カード数（ゲーム未選択で 1・2v2 のゲームで 2）とキャラ枠 3、スコアボードのオーバーレイの表示

### S3e `WebServerActions` の廃止（S2f / S2g・統計・セット読込・追従の core 化）— 設計（2026-10-26・オーナー承認済み）

オーナー判断（2026-10-26）: 下記の設計を承認。論点 1〜4 は推奨案どおり。S3e-1 から順に自走で進める（コミット・push・PR 作成まで）。

#### 棚卸しで分かったこと（前提）

- `WebServerActions` に残る 18 メソッド（S3c-2 判断の一覧）の依存は 6 つに分かれる
  - **選手データ**（`post_score` の entrants / `set_team_data` / `update_player` / `load_player_from_tag`）: カードの `SetData`（`AutonaScoreboardPlayerWidget.py:1366`）が、DB とのマージ、悪語フィルタ、国・キャラのコンボの行の走査、`editingFinished` 経由の書き込みを全部持っている（S2f）
  - **人数・キャラ数**: 正本はボードのスピンボックスで、スロット数として暗黙に表れる。書き手は 6 か所（構築・single モード・ゲームのロード・セットの entrants・`set_route`・`clear_all`）。統計は人数をウィジェットの数で、`Actions._UpdateUpsetFactor` は state のスロット数で数えており、2 系統が併存している（S2g）
  - **統計**: 判断と書き込みは `core/StatsService.py` にあるが、トリガーと結果の運び役（`AutonaStatsUtil` のシグナル）と出し手（カードの `instanceSignals`・ボード・STATS パネル）が Qt 側にある
  - **セット読込と追従**: `NewSetSelected`（`AutonaScoreboardWidget.py:2177`）が `QTimer` 2 本・ラベル・カードの `dataLock` を持つ。station / stream の追従は、**ワーカースレッドがボードのウィジェットを直接読んでいる**。`pull_stream_set` は接続先が無く、常に空振りしている
  - **実況と Top 8**: 遅延構築のパネルが書き手で、**state に追従しない**（構築時に 1 回 `_loadFromState` するだけ）。core が書いた後にパネルで編集すると、古い値で上書きされる
  - **シェルの操作**: `get_thumbnail`（QApplication + オフスクリーンの WebEngine）と `open_sets`（ウィンドウ）
- Qt に依存しない部品の抽出が先に要る
  - 悪語フィルタの Qt 依存は `AutonaLocaleHelper` の import 1 本だけ
  - `AutonaPlayerDB` はデータ（`database`・Load / Add / Get / Save）とモデル（QStandardItemModel）が同じクラスにある。`database` は entrants のワーカーからも書かれるのに、dict にロックが無い
- golden（`tests/fixtures/player_slots/golden.json` ほか）が、`SetData` を含む操作の Set 列と保存サイクルを固定している。core 版も同じ列を出す必要がある（キー順は外部契約）
- 既存バグ: `load_commentator_from_tag` は成功時に None を返す（legacy は 500、Socket.IO の ack は null）。範囲の確認が 1..8 で、実際の枠数 12（`CommentaryModel.SLOTS`）と合っていない

#### 方針

- S3e は、次の 9 つの PR に分ける。各 PR は単独でリリースできる
- 順序: 1 → 2 → 8 → 3 → 4 → 5 → 6 → 7 → 9
  - 依存の無いものと、リスクの低いものから進める
  - パネルを state に追従させる型は、実況・クルー・Top 8 で先に確立し、そのあと選手カード（S2f）に当てる

| 段 | 内容 | 完了条件 | リスク | 依存 |
|----|------|---------|-------|------|
| **S3e-1 Qt 非依存部品の抽出** | `core/BadWordFilter`（ロケールと国の言語表を注入）と `core/PlayerDB`（`database`・Load / Add / Delete / Get / Save・`tournamentTags`・変更通知の callback・dict のロック・保存のアトミック化）を作る。`AutonaPlayerDB` はモデルと `db_updated` だけの Qt アダプタにする | 両方とも `test_core_qt_free` の対象になる。`local_players.json` がバイト一致する。`db_updated` の発火回数が変わらない | 低〜中 | なし |
| **S3e-2 実況とクルーの core 化** | 実況: `CommentaryService.ApplyPlayerData` / `LoadFromTag`（1 枠ずつマージして publish）。クルー: `CrewBattleService.AdjustMemberStock` と、アクティブを 1 人に限る規則を core に移す。実況パネルとクルーのウィジェットを、Bridge 経由で state に追従させる。`set_commentary_data` / `load_commentator_from_tag` / `crew_*` を `AutonaWebOperations` へ移し、`_ensure_panel` を削除する。クルーボードの swap / reset / clear（`BoardHost.Widget` 経由で 503）も同じ追従の上で core 化する | ヘッドレスで動き、両シェルの state が一致する。パネルが未構築でも動く。パネルの編集が古い値で上書きしない。`load_commentator_from_tag` が `"OK"` を返し、範囲は 1..12 | 中 | 1 |
| **S3e-8 Top 8 取り込みの core 化** | `Top8Service.RefreshFromProvider()`（phases → 決勝グループの解決 → 取得 → `FromBracket` → `GameAssetCatalog` / `CountryCatalog` で選手を解決 → `ImportBracket`）。`Bracket` は core へ移す（ラウンド名は注入）。パネル・ホットキー・`update_bracket` が同じ関数を呼ぶ。v1 の 202 と実行中フラグは維持する | ヘッドレスで `bracket:refresh` が `top8.*` を書く。デスクトップの取り込み結果が golden で一致する | 中 | 1（2 と並行可） |
| **S3e-3 S2f: 選手データの core 化** | `PlayerService.ApplyData(data, loadFromDB, clear, no_mains)` を作る（`_ClearSlot` を流用、DB マージ → 悪語フィルタ → テキスト → id / avatar / city → 国 / 地域 → mains → seed → 合成名と画像。順序は golden に合わせる）。カードの `SetData` は、これを呼んで `RenderFromState` するだけにする。`SavePlayerToDB` を state から組む。id / seed が変わったかを戻り値で返す（統計のトリガー用）。キャラ数は、S3e-6 まではスロットの `character` の数で決める | golden の Set 列と保存サイクルが、core だけの再生とカード経由の再生の両方で一致する。カードに国 / キャラのモデル走査が残らない。`player_editor.1` の経路（pitfalls #7）が両方で動く | **高** | 1 |
| **S3e-4 選手系 Web 操作の移設** | `update_player` / `set_team_data` / `load_player_from_tag` を `AutonaWebOperations` + `boardHost` へ移す（スロットの範囲と `teamsSwapped` は state で判断）。`DesktopBoardHost` が GUI スレッドで実行する必要があるかを見直す | ヘッドレスで、既存のスロットに対して動く。デスクトップの応答の本文は変わらない | 中 | 3 |
| **S3e-5 統計の core 化** | `StatsService.Refresh(kind, team)` を入口にする。取得は `bootstrap_core()` が注入する fetcher（既存の provider と QThreadPool を使う）に任せる。トリガーは core 操作（id / seed の変化・`ApplyBracketType`・クリア）から出す。人数は state のスロット数に統一する。`AutonaStatsUtil` を縮小し、`stats_*` / `refresh_stats` を移す | ヘッドレスで state に結果が書かれる（Start.gg はモック）。取り直しの回数が今と同じ。古い結果は捨てる | 中 | 3 |
| **S3e-6 S2g: 人数・キャラ数を state 主導へ** | `SetRoster(board, players, characters)` を作る（スロットとキャラ枠の増減、1 人になるときのチーム名のクリアを、1 保存サイクルで行う）。入口 6 か所をすべてこれにする（ゲームのロード時の既定値は、両シェル共通の結線へ移す）。ボードは held サイクルの中でカードの数を state に合わせる。カードの破棄は `singleShot` に逃がし、pitfalls #14 の invalidate を通す | ヘッドレスで `set_route players` と 2v2 ゲームのロードによりスロットが増える。デスクトップの起動直後のカード数が変わらない。`test_wrapper_lifetime` / `test_signal_lifetime` が通る | **高** | 3 |
| **S3e-7 セット読込と追従の core 化** | ボードごとの追従サービスを Qt 非依存で作る。中身は `LoadSet`（`NewSetSelected` の論理）、5 秒ポーリング（タイマー 1 本）、station / stream の追従、`ApplySetPayload`（`ChangeSetData` の core 版）。ボードのラベルとカウントダウンは state を読むだけにする。`load_set` / `post_score` / `pull_stream_set` を移す | ヘッドレスで `:load-set` と追従が動き、停止・クリアで止まる。古いペイロードを捨てる。セット選択と station のウィンドウは今どおり | **高** | 3・5・6 |
| **S3e-9 シェル操作の登録と廃止** | `get_thumbnail` と `open_sets` を、シェルが登録する操作にする（デスクトップは `@gui_thread_sync`、ヘッドレスは 503）。`AutonaWebServerActions.py`・`_board_actions`・`init_api_v1(actions)` を削除する。残った `@gui_thread_sync` を棚卸しして台帳に記録する | `grep WebServerActions` が 0 件。ルート台帳と 503 のテストを更新する | 低〜中 | 全部 |

- 並行処理: S3e では Executor への統一（§4.3）はしない。core を呼ぶのは werkzeug のリクエスト・provider のワーカー・追従のタイマー・GUI のスレッドで、ボード操作は必ず `boardHost.Run` を通して直列化する（`clear_all` の途中に割り込ませない）
- デスクトップは、未構築のボードに書く前に構築する（`boardHost.Prepare`。構築時のシードで書き込みが消えるため。S2c 判断 7）
- 維持する既存の挙動
  - `set_team_data` / `post_score` の `match_en` 空化
  - `clear_all` でキャラ数を 1 にすること
  - Socket.IO の ack と legacy の本文の文字列

#### 論点と決定（2026-10-26 オーナー承認・すべて推奨案どおり）

1. **人数・キャラ数を state のキーで持つか** → 推奨: **キーを足さず、スロット数を正本にする**
   - オーバーレイ 4 種と Web UI は、既にスロット数とキャラのキー数を人数として読んでいる。キーを足すと正本が 2 つになり、食い違ったときに外部の表示が割れる
   - スロットが 0 の場合は `DefaultPlayersPerTeam()` で補う
2. **ヘッドレスでの thumbnail と `open_sets`** → 推奨: **シェルの操作として残し、ヘッドレスは 503 のまま**
   - 目標は「18 操作」から「16 操作 + デスクトップ専用のシェル操作 2 本」に改める
   - thumbnail は QApplication と WebEngine が必須（§4.2 で renderer の GUI 依存は認めている）
3. **`pull_stream_set` を直すか** → 推奨: **S3e-7 で直す**
   - `twitch_username` の配信キューから次のセットを読み、stream の追従を始める
   - 未設定のときは、legacy は `"OK"` のまま、v1 は 409 `NOT_READY`（エラーの追加）
   - 「何も起きない」が「次のセットが読まれる」に変わるので、リリースノートに書く
4. **Executor 1 本化を S3e に含めるか** → 推奨: **含めず、S3f に分ける**
   - ヘッドレスは QCoreApplication のイベントループで Worker の結果を受け取れるので、S3e の完了条件に Executor は要らない
   - 含めると、provider の入れ子 Worker・qasync・delta の `threading.Timer` まで範囲が広がり、S3e-7 のリスクと重なる

#### §4.4 の完了条件の改定（承認済み）

- 旧（S3 から移したもの）: `WebServerActions` を廃止する。`@gui_thread_sync` の残りは thumbnail / result とウィンドウ操作だけ
- 新: `WebServerActions` を廃止する。ヘッドレスで、シェル操作 2 本を除くすべての Web 操作が動く。`@gui_thread_sync` の残りは次の 3 種類だけ
  - シェルの操作（thumbnail / result / ウィンドウ）
  - Qt アダプタ内部のモデルの marshal（`AutonaPlayerDB` / `GameAssetManager`）
  - `set_game` の同期ロード
- 残りの 3 種類は S3f（Executor 1 本化）で棚卸しする

#### やらないこと

- Executor への統一と、QThreadPool / Worker / qasync / `threading.Timer` の廃止（S3f）
- パネルの React 化と、Web UI の 503 の専用表示（S4）
- thumbnail / result のヘッドレス描画
- legacy ルートの削除（v2.0）
- `local_players.json` の形式変更
- 自動更新の構造の変更

#### 規模の見積り

- 9 つの PR の合計で、+4,000 / −2,000 行程度
- 高リスクは S3e-3（S2f）・S3e-6（S2g）・S3e-7（セット読込と追従）の 3 つ。どれも Qt のライフサイクルか golden に触れる

#### S3e-1 Qt 非依存部品の抽出（2026-10-26・PR #233）

悪語フィルタと選手 DB のデータ側を core に移した。外から見える挙動（`local_players.json` のバイト・ヘッドレスの `/playerdb`・Socket.IO / v1 の応答・`db_updated` の回数・検閲結果）は変えていない。どちらも旧実装で録った golden で照合している。

- **実装**
  - `src/core/BadWordFilter.py`（新規）: `Helpers/AutonaBadWordFilter.py` の Trie・正規表現・言語の選び方・白黒リスト・`Censor` / `CensorString` / `CensorDict` をそのまま移した
    - インスタンス化した（`BadWordFilter.instance`）
    - プログラムのロケール（`programLocale`）、国 → 話者言語（`countryLanguages`）、国 → 大陸（`countryContinent`）をコンストラクタで callable として受け取る。呼ぶのは `CensorString` のたび
    - 単語リストの場所も引数にした。既定は従来どおり CWD 相対の `./assets/ngword/` と `./user_data`
    - `Initialize()` は冪等
  - `src/Helpers/AutonaBadWordFilter.py`: `BadWordFilter.instance` に転送するだけの互換層にした（`Censor` / `CensorString` / `CensorDict`）。呼び出し側（選手カード・実況）は変えていない。`AutonaLocaleHelper` を import しなくなった
  - `src/core/PlayerDB.py`（新規）: `AutonaPlayerDB` のデータ側を移した。対象は `database`・`fieldnames`・`PlayerTag`・`LoadDB`（旧 CSV の取り込みを含む）・`AddPlayers`（mains のマージ）・`DeletePlayer`・`GetPlayerFromTag`・`SaveDB`・`tournamentTags`。インスタンスは `PlayerDB.instance`
    - `database` を `lock`（RLock）で守る。外からは次で読む
      - `Get` / `Contains`
      - `Items`（リストのスナップショット）
      - `GetPlayerFromTag`
      - `TournamentPlayers`（Web に出す用。ロックの中で選手 dict ごと深いコピーを取る。`/playerdb` と `ws_playerdb` はこのコピーをシリアライズする）
      - `Get` / `GetPlayerFromTag` / `Items` が返す選手 dict は生のまま（GUI 側の呼び出し元用。旧実装どおり）。ロックの外で書き換えないことを docstring に書いた
    - 変更は `changed`（`ObserverChannel`）で通知する。複数の購読者に同期で渡し、購読者ごとに例外を隔離する
      - 通知は LoadDB / AddPlayers（`deferRefresh` のとき以外）/ DeletePlayer / `NotifyChanged()` で 1 回ずつ
      - 必ずロックの外で出す
    - `SaveDB` はアトミックにした
      - ロックの中ではシリアライズ（JSON の文字列化）だけを行い、書き込み・置き換え・再試行はロックの外で行う
      - 書き込みどうしは保存専用のロック（`_saveLock`）で直列化する。スナップショットに番号を振り、古いスナップショットが新しいものの後に書かれないようにした
      - 同じディレクトリに mkstemp で書いてから `os.replace` する。Windows の共有違反に備えて 4 回試行する（再試行 3 回。間隔は 20 / 40 / 80 ms）
      - 4 回とも置き換えに失敗したとき、または一時ファイルを作れないときは、旧実装と同じ上書き（truncate → write）にフォールバックする。旧実装で保存できていた環境で、保存できなくなることはない
      - 一時ファイルは成否にかかわらずその場で消す。`LoadDB` のときに、クラッシュで残った古い `local_players.*.tmp` を掃除する
      - テキストモードで書くので、バイトは旧実装の `open(path, 'wt')`（改行は `os.linesep`）と同じ。初回の `[]` も同じ経路で書く
    - `tournamentTags` の操作は `SetTournamentTags` / `ClearTournamentTags`（消すものが無ければ False を返す）
  - `src/AutonaPlayerDB.py`: Qt アダプタにした
    - 残したもの: `model`、`completerModel`（`_TournamentEntrantsProxy` は `PlayerDB.instance.tournamentTags` を読む）、`_retiredModel`、`modelEnabled`、`EnableModel()`、`SetupModel`、`_BuildModel`、`GetCompleterModel`、`SetTournamentPlayers`、`ClearTournamentPlayers`、`signals.db_updated`
    - `Attach()`（新規）が `SetupModel` を `PlayerDB.instance.changed` に購読させる（冪等）。`PlayerDB.instance` が差し替わっていたら、古い DB の購読を外してから新しい DB に付け替える
    - `_BuildModel` は `modelLock` → `db.lock` の順にロックを取る。本体（mains の正規化を含む）は旧実装のまま。`db_updated` の emit は両方のロックを放してから行う
  - `src/bootstrap.py`
    - `bootstrap_core()` で `BadWordFilter.instance` を作って `Initialize()` する。位置は旧 `AutonaBadWordFilter.Initialize()` と同じ（`AutonaLocaleHelper.Initialize()` の後）
    - `GameAssetCatalog.ResetGameState()` の後、StartGG の前で `PlayerDB.instance` を作り、`AutonaPlayerDB.Attach()` を呼ぶ
    - `bootstrap_gui()` の手順（`EnableModel()` と onLoad → `SetupModel`）は変えていない
    - `ORDER CONSTRAINT:` コメントを更新した
  - 呼び出し側のデータの操作を `PlayerDB.instance` へ向けた。モデルと `db_updated` の使い方はそのまま
    - StartGG: `PlayerTag`、`AddPlayers(deferRefresh=True)`、最後に `SaveDB()` → `AutonaPlayerDB.SetTournamentPlayers()` → `NotifyChanged()`
    - 選手カード: Get / AddPlayers(overwrite) / Contains / DeletePlayer
    - 実況: GetPlayerFromTag
    - 選手ブラウザ: GetPlayerFromTag / Items / Get / DeletePlayer
    - RESULT: AddPlayers / Get
    - スコアボード: UpdateSetData の DB マージ / GetPlayerFromTag
    - `AutonaWebOperations.get_playerdb`: `TournamentPlayers()`
    - `Autona.py` / `headless.py`: `LoadDB()`
- **判断と理由**
  1. **国の言語表は core でロードせず、注入にした**
     - `data_countries.json` は、両シェルで `bootstrap_core()` の `AutonaLocaleHelper.Initialize()` がすでに読んでいる。core でもう一度読むと、読み手が 2 つになる
     - 注入は `AutonaActions.formatMatchText`・`TournamentInfoService.formatEventDate`・カタログの `locales=` と同じ形
     - callable は検閲のたびに呼ぶので、`bootstrap_core()` の後に走る `LoadLocale()` の結果も反映される
     - `GetCountrySpokenLanguages` / `GetCountryContinent` を読むのはこのフィルタだけ。表ごと core へ移すのは、必要になった段でできる
  2. **中継（`AutonaPlayerDB.Attach()`）は `bootstrap_gui()` ではなく `bootstrap_core()` に置いた**
     - ヘッドレスでも `db_updated` → Web サーバーの `ws_playerdb` が要る。発火元は entrants の取得の終わり（`NotifyChanged`）・追加・削除
     - 中継が使うのは QtCore だけ（`gui_thread_sync` と Signal）。`StateManagerBridge` と同じ配置
     - `AutonaPlayerDB.py` を `tests/test_signal_lifetime.py` の購読許可リストに足した。process と同じ寿命の static クラスなので、強参照の購読が受け手より長く残ることはない
  3. **呼び出し側は `PlayerDB.instance` を直接呼ぶ。`AutonaPlayerDB.database` などの互換プロパティは作らない**
     - dict を直接読ませると、ロックを素通りする
     - 悪語フィルタは呼び出し側が多いので、範囲を広げないために互換層を残した
  4. **`db_updated` の回数は変えない**
     - 旧実装が `SetupModel()` を呼んでいた箇所（LoadDB / AddPlayers（defer 以外）/ DeletePlayer / entrants 取得の終わり）で、core が 1 回ずつ通知する
     - ゲームのロードは従来どおり `SetupModel` に直結している
     - emit はロックを放した後にした。スロットの中にモデルを作り直すものは無い（`modelLock` は再入できない）
  5. **通知はロックの外で出す**
     - アダプタの購読者は、ワーカーから呼ばれると GUI スレッドに渡して待つ。GUI スレッドのモデル構築は `db.lock` を取る。ロック中に通知すると、ワーカーと GUI スレッドが互いを待つ
     - ロック順は `modelLock` → `db.lock` → `SettingsManager.lock`（`_AddPlayer` が `disable_overwrite` を読む）
     - `SaveDB` もロックを持ったまま待たない。ロックの中はシリアライズだけで、書き込みは保存専用のロックで直列化する（ロック順は `db.lock` → `_saveLock` の一方向）
  6. **既存バグは直さず、golden で固定した**（範囲外）
     - 悪語フィルタのログ行（`logger.info(value, "->", newString)`）が、`{name}` を含む値で `KeyError` を出す
     - `AddPlayers` のマージで、`newMains` のリストが複数のゲームで共有される
     - overwrite での mains の更新は、DB 側の mains が文字列 `"{}"` だと失敗する（ログだけ）
     - 濁点・半濁点を含む語（例: 「ほげ」）は一致しない。`remove_accents_lower` で検査する文字列の側だけ濁点が落ちるため
     - `CensorDict` は list の中を検閲しない
  7. **mains の正規化はアダプタに残した**（S3c-2 判断 5 のまま。core に移すかは S3e-3 で決める）
- **エラー時だけの変化**
  - `AddPlayers` / `DeletePlayer` の中で `SetupModel` が例外を出したとき、旧実装では呼び出し元に例外が伝わった
  - 今は `ObserverChannel` がログに出して握る（保存は済んでいる）
- **pin**
  - `tests/test_bad_word_filter.py`（新規）
    - 旧実装で録った `tests/fixtures/bad_word_filter/golden.json` を、新しいクラスで再生する
      - 中身は、合成の単語リストで 10 ロケール × 11 国 × 33 値と、dict・None・設定オフの結果
      - 録り方は同じフォルダの `record_golden.py`
    - loguru のシンクを付けて、ログ行の `KeyError` も再現する
    - callable を渡さないときの既定値と、互換層の転送も見る
  - `tests/test_player_db.py`（新規）
    - 旧実装で録った `tests/fixtures/player_db/golden.json` で照合する
      - シナリオは 7 つ: ロード → 保存、追加の全分岐、削除、初回起動、旧 CSV の取り込みと無視、`GetPlayerFromTag`
      - 各ステップの後に、ファイルのバイト、CSV の有無、メモリ上の DB（ソートしない `json.dumps` で比べ、選手の並びと各選手のキー順も固定）、`TournamentPlayers()`（= `/playerdb` の中身）を見る
    - 通知: 回数、全購読者に届くこと、ロックの外で出ること、購読者の例外
    - 保存
      - 置き換えが成功するまで元のファイルは変わらない。一時ファイルは残らない
      - 一時的な失敗は再試行する。4 回とも失敗したとき・一時ファイルを作れないときは上書きで保存される
      - 書き込みはロックの外で行い、古いスナップショットは新しいものの後に書かれない
      - `LoadDB` が古い一時ファイルを消し、ほかのファイルは消さない
    - 読み手とワーカーの並行（`TournamentPlayers()` の JSON 化を含む）、大会での絞り込み、Web に出すコピーが深いこと
    - start.gg の entrants 取得の終わりが `SaveDB` → `SetTournamentPlayers` → `NotifyChanged` の順であること（`StartGGDataProvider.py` を AST で読む）
    - アダプタ経由
      - `db_updated` が操作ごとに 1 回、GUI スレッドで出る
      - ワーカーからの entrants 取得の形でも詰まらず、`db_updated` が 1 回出る
      - モデル構築が mains を正規化する
      - `Attach()` が差し替わった instance へ購読を移す
      - fixture は終わりに、セッションの DB への中継と `model` / `completerModel` / `_retiredModel` を元に戻す
  - `tests/test_core_qt_free.py`: 2 モジュールが core の検査対象に入っていることを pin する
  - `tests/test_import_side_effects.py`: 2 モジュールの import に副作用が無いこと。構築・`Initialize()`・`LoadDB()` の陽性対照も足した
  - `tests/test_bootstrap_split.py`
    - 手順の並び
    - 注入した callable がロケールと国の表を読むこと
    - 中継の購読が 1 本だけであること
- **残置 / リスク**
  - `Get()` / `GetPlayerFromTag()` / `Items()` は生の選手 dict を返す（GUI 側の呼び出し元用。旧実装どおり）。ロックの外で書き換えない約束は docstring だけで、強制はしていない
  - `TournamentPlayers()` は呼ぶたびに DB 全体（大会中は出場者分）を深いコピーする。`db_updated` ごとに 1 回で、3,000 人で約 9 ms（手元の計測。その間 DB のロックを持つ）
  - 上書きへのフォールバックは旧実装と同じく、書き込みの途中で落ちると壊れたファイルが残りうる（置き換えが 4 回とも失敗したときだけの経路）
  - `AutonaPlayerDB.modelLock` は `multiprocessing.Lock` のまま（既存）
- **実機確認（オーナー）**
  - デスクトップ
    - 選手カードで選手を保存 / 上書き保存 / 削除する → `user_data/local_players.json` に反映され、形式（インデント・並び・項目）が変わらない。名前欄の補完にも出る
    - 選手ブラウザ（PLAYERS）で一覧・検索・編集・削除ができる
    - ゲームを切り替えた後、補完のアイコン（メインキャラ）が出る
    - start.gg の大会を読み込む → 補完候補が出場者に絞られる。大会 URL を消すと全員に戻る
    - RESULT の取り込みで、選手が DB に入る
    - 悪語フィルタ（設定 ON）: 名前に NG ワードを入れると `***` になる（日本語 UI、海外選手の国）
  - ヘッドレス（`python main.py --headless`）
    - `/playerdb` の内容がデスクトップと同じ
    - start.gg の取り込み後、Web UI の選手候補が更新される

#### S3e-2 実況とクルーの core 化（2026-10-26・PR #234）

実況（`commentary.*`）とクルーボード（`score.3`）の書き込みを、パネル / ボードのウィジェットから core のサービスへ移した。Web の操作はどちらのシェルでも同じ core を呼び、パネルとボードは state を描画するだけになった。外から見える挙動（Socket.IO のイベント名と ack・legacy の本文・`program_state.json` のキー順・Socket.IO の delta）は、`load_commentator_from_tag` の修正（下記）を除いて変えていない。移す前の木（8aa8c33b）でウィジェット経由の Set 列・保存サイクル・delta・結果を録り、core 版と照合している。

- **実装**
  - `src/core/CommentaryService.py`（新規・モジュール関数）
    - `ApplyPlayerData(slot, data)`: PlayerDB の形の dict（Web の `update_commentary`・補完の選択・DB 引き）で 1 枠を上書きする。state から 12 枠を読み、文字列の値だけを悪語フィルタ（`BadWordFilter.instance`）→ 正規化（`FieldValue`。前後の空白を落とし、Twitter は先頭の `@` を落とす）して 1 枠にマージし、ルートごと 1 回 `Set` する（旧パネル経由と同じ Set）。role・icon・display は触らない
    - `LoadFromTag(slot, tag)`: `PlayerDB.instance.GetPlayerFromTag` → `ApplyPlayerData`。見つからなければ何も書かない
    - `SetField(slot, field, value)`: パネルの 1 欄の編集。その 1 キーだけを、値が変わったときだけ書く。表示は `MAX_DISPLAY`（4）人まで（超える ON は断る）
    - `Reorder(order)`: パネルのドラッグ。12 枠の並べ替えを 1 回の `Set` で書く
  - `src/core/CrewBattleService.py`: 操作を state から読むようにした（旧実装はウィジェットの行を受け取っていた）
    - 追加: `AdjustMemberStock` / `SetMemberStock`（0〜99）、`SelectActiveMember`（1 チーム 1 人。0 以下 = 不在）/ `ReleaseActiveMember`（印が付いているメンバーのときだけ外す）、`SetMemberCount`（1〜9。変わらなければ書かない）、`ApplyBestOf`（0〜99。変わらなければ書かない）/ `ApplySetData`、`SwapTeams` / `ResetStocks` / `ClearCrew`（両チームを読んで 1 サイクルで publish）、読み出し（`MemberNum` / `Stocks` / `ActiveMember` / `ReadTeam`）
    - `SetMemberName` / `SetMemberNum` のストック列は省略すると state から読む
    - スワップのフラグ（`teamsSwapped`）をクラス属性 `_swapped`（ボード番号ごと）に持つ。`SeedBoardState`（ボードの起動時の書き込み）で消す
  - `src/AutonaWebOperations.py`: `set_commentary_data` / `load_commentator_from_tag` / `crew_member_stock` / `crew_set_active` を追加した。クルーボードの swap / `get_swap` / reset / clear / `set_route`（人数・キャラ数・match バー・best of）は board 3 では `CrewBattleService` を呼ぶ（ボードホストの `Widget()` を使わない）
    - 実況はリクエストのスレッドで core を呼ぶ（パネルを構築しない）
    - クルーはボードホストの `Run` を通す（デスクトップは GUI スレッドで、未構築なら構築してから。swap・`reset_players`・`clear_all` は、ボード 1 / 2 と同じく入力中の欄を確定してから。`clear_all` はリセットより前に確定するので、match バーの打ちかけも一緒に消える）
  - `src/AutonaWebServer.py`: 上の 4 操作のルート / イベントを `WebOps` へ向けた
  - `src/AutonaWebServerActions.py`: 上の 4 メソッドと `_ensure_panel` を削除した（残りは公開 14 本）。`update_bracket` は `EnsureTop8Panel` を直接呼ぶ
  - `src/AutonaCommentaryWidget.py`: 書き込みを `CommentaryService` に向け、state を描画するようにした
    - 削除: `ChangeCommDataSignal` / `LoadCommFromTagSignal` / `SetData` / `LoadCommentatorFromTag` / `_syncToState`（入力欄からルートを組み直す全量 publish）/ `_loadFromState` / `_updating`
    - 入力欄の確定 → `SetField`（1 欄だけ）。アイコンは識別子（stem）だけを渡す。ドラッグ → `Reorder`。補完の選択 → `ApplyPlayerData`
    - 「配信に表示」の ON が上限で断られたら、チェックをシグナルを止めて state の値に戻してから、無効化する数を数える（`_refreshDisplayLocks`。断られたチェックを数えると、他の欄の無効化とこの欄の有効化を、state に無い数から決めてしまう）
    - `_RenderFromState`: Bridge の `state_updated`（`['commentary'` を含む delta）/ `state_big_change` で `singleShot(0)` に 1 回にまとめ、`QSignalBlocker` で描画する。入力中の欄は飛ばし、離れたら描画する（`focusChanged`）
  - `src/AutonaCrewBattleWidget.py`: 同じ形にした
    - 削除: `CommandMemberStock` / `SetActiveMember` / `CommandScoreChange` / `CommandTeamColor` / `ClearScore` / `ResetMatch` / `CommandClearAll` / `_flushAllState` / `_snapshotTeams` とシグナル、Web の汎用操作のためだけにあった隠しウィジェット（`_bestOfSpin` / `charNumber` / `scoreColumn`）
    - 入力（ストックのスピン・出場中のラジオ・人数のスピン・名前・チーム名・色・match バー）→ core の操作。SWAP / リセットのボタンも core
    - `_RenderFromState`: `['score', '3'` を含む delta と `state_big_change` で、上と同じ規則で描画する。表示大会名の欄は従来どおり
    - 人数が減って、打っている行が隠れるとき: 隠すと Qt が描画の中でその欄を確定する（`editingFinished`）。描画中の確定は書かない（`_Finished`。「描画は何も書かない」に例外を作らない）。打ちかけは捨て、同じ描画で隠れた欄に state の値を出す
  - `src/Autona.py`: `EnsureCommentaryPanel()` を削除した（呼び出し元が無くなった）
  - `src/core/Actions.py`: `PublishCommentary` を削除した（呼び出し元が無くなった）
  - `src/Helpers/AutonaQtHelper.py`: `HeldBackText`（新規）。入力中の欄に描画が残した文字を書き戻さない判定（判断 3）を 1 つにまとめ、選手カード・ボード 1 / 2 のチーム名・クルーボード・実況パネルが共有する（`Shown` / `LeftAlone` / `IsHeldBack` / `IsEdit`）
  - `src/AutonaScoreboardPlayerWidget.py`: 選手カードの既存の不具合（下記）を直した。`_heldBackText` を `HeldBackText` に置き換え、描画後の `lastExportedName` は「残した文字」の欄だけ欄の文字、それ以外は slot の値から組むようにした（打っていた文字は新しい名前なので、確定で合成名・画像を publish する）。名前の欄を飛ばした描画でも組み直す（残した文字の確定で合成名を publish しない）
  - `src/AutonaScoreboardWidget.py`: チーム名の欄の既存の不具合（下記）を直した。`_heldBackTeamName`（1 つの dict が「離れたら描画し直す欄」の印と残した文字を兼ねていた）を、印の `_teamNameSkipped`（チームの集合）と判定の `_teamNameHeldBack`（`HeldBackText`）に分けた
- **判断と理由**
  1. **Web の実況はルートごと無条件に 1 回 `Set` する**（「1 枠ずつマージ」= state から読んで 1 枠を変えて publish）。旧パネル経由の Set 列（変化なしでも 1 Set・delta なし）と一致させるため。`SetIfChanged` にすると Set 列が変わる
  2. **パネルの編集は 1 欄だけ書く**（`SetField`）。旧 `_syncToState` は確定のたびに全 12 枠を入力欄から組み直して publish しており、パネルがまだ描画していない変更（Web からの更新）を古い値で上書きしえた。Socket.IO の delta は葉の単位で出るので、パネルの編集の delta の形は変わらない（変化なしの確定は何も書かなくなった）
  3. **入力中の欄の扱いは選手カードの規則に揃え、1 点だけ直した**（S2a の `_OnTextChanged`）。描画は入力中の欄を飛ばし、そのとき欄に残った文字は、確定しても書き戻さない（state が先に進んでいるため）。打ち直した文字は書く。補完の選択だけは、選んだ名前を入力中の欄にも描画する（操作者自身の操作）。スピンボックス・ラジオ・チェックボックス・アイコンのコンボは常に描画する
     - 直した点: 残った文字として扱うのは次の 2 つだけにした。(a) **その欄が最後に表示した（または書いた）文字のまま**で、state が先に進んだ。(b) 打っている途中に、**その欄自身の値が別の所（Web・セット読込）で書かれた**（S2d のチーム名の規則: その書き込みが打ちかけに勝つ）。(b) はその書き込みを最初に見た描画だけが判定し、そのあと打った文字は書く。それ以外の打った文字は、描画が何回走っても確定で書く。S2a の選手カードと S2d のチーム名は、打っている途中の文字を無条件に残った文字として扱っていたため、打っている間に描画が 1 回でも走ると（同じボードのスコア +1 でも）、Enter / フォーカスアウトで打った文字が書かれずに消えていた（下の「既存の不具合」）。ボードと実況パネルは、関係する delta のたびに全体を描画するので、そのままだと Web の操作 1 回で打っていた名前が消える
     - (a) で残した文字のあとに打ち足した文字は、操作者の編集として書く。つまり外の書き込みが届いたときに欄に**いるだけ**（打っていない）だった場合、そのあと打ち足すと**打った側が勝ち**、外の書き込みは上書きされる（打ち足した文字は「欄が表示した文字のまま」ではないため。(b) で外の書き込みが勝つのは、打っている途中に届いた場合だけ）。レビューで確認し、この挙動のまま受け入れた
     - 判定は `HeldBackText`（`src/Helpers/AutonaQtHelper.py`）にまとめ、選手カード・ボード 1 / 2 のチーム名・クルーボード・実況パネルの 4 つが同じものを使う。(b) を残したのは、S2d の既存テスト（`test_a_web_team_name_survives_leaving_the_field`: 打ちかけの間に Web が書いたチーム名が、欄を離れても残る）と、直す前の選手カードの挙動を、その場合について変えないため。`isModified()` にしない理由は S2a と同じ（打って消して元に戻すと打鍵ありになる）。一度も表示していない欄は空を表示していたものとして扱う（新しいカードの入力欄は空で、state にキーが無い = 空と同じ）
  4. **`load_commentator_from_tag` を直した**（既存バグ。台帳の S3e 棚卸し参照）。成功は `"OK"`、DB に無いタグは `"ERROR"`（`load_player_from_tag` と同じ）、範囲は 1..12（旧 1..8）。番号でない値は旧実装どおり例外（legacy は 500）
  5. **クルーのスワップのフラグは state に入れない**。score.3 にキーを足すと `program_state.json` の中身が変わる（外部契約）。旧実装もウィジェットの属性で、再起動で消えた（同じ）。core がボード番号ごとに持ち、Web はウィジェット無しで読める。その結果、**ヘッドレスの v1 `GET /scoreboards/3` が `swapped` を返すようになった**（S3c〜S3e-1 はキーなし。デスクトップの形にそろった）
  6. **クルーの操作はボードホストを通す**。デスクトップでは GUI スレッドで同期実行し、応答の時点で書き込みは終わっている（旧実装は queued シグナルで、反映前に `"OK"` を返していた。実況も同じく、今は書いてから返す）。未構築のボードは構築してから書く（S2c 判断 7 と同じ理由）
  7. **board 3 以外への `crew_*` は旧実装どおり「構築して何もせず `"OK"`」**。番号の解釈もクルーボードのときだけ行う（旧実装はシグナルが無いボードでは `int()` しなかった）
  8. **アクティブの解除は「そのメンバーに印があるときだけ」**。描画が追いついていないラジオを外しても、state が先に進めた別のメンバーを消さない
  9. **ボードの隠しウィジェットを消した**。`_bestOfSpin` / `charNumber` / `scoreColumn` は、Web の汎用操作が board 3 でも属性を突けるようにするためだけにあった。Web の操作が board 3 を明示的に分けたので要らない
- **pin**
  - `tests/test_commentary_service.py`（新規）
    - golden（`tests/fixtures/commentary/golden.json`、16 シナリオ: 全項目・部分・空白と非文字列・12 枠目・検閲・変化なし・空・dict でない・範囲外 2・番号でない・DB 引き 5）を、パネル未構築・パネル構築済み・ヘッドレスのボードホストの 3 形態で再生する（Set 列・delta・big change・結果・検閲の呼び出し・ルート全体）。`load_*` の結果だけ修正後の値で照合する。パネルを構築したときは、パネルが何も書かず、表示が state と一致することも見る
    - 12 枠すべてに読めること、範囲外・DB に無いタグの応答
    - `SetField` の 1 キー書き込みと表示の上限、`Reorder`
    - パネル: core の書き込みに追従する（何も書かない）・`state_big_change` に追従する・未描画の変更があるときの編集が他の枠を戻さない・上限を超えた ON が戻る（描画を待たずにその場で戻り、無効化も state の数で決まる。何も書かない）・アイコンのコンボが stem の 1 キーだけを書く・ドラッグが 1 回の `Set` で並べ替える・入力中の欄を飛ばし、離れると新しい値が出る・打っている途中に別の枠が Web で変わっても、打った文字が Enter / フォーカスアウトで書かれる・名前の補完の選択が枠を埋め、入力中の欄にも保存した名前（補完の文字と違う検閲後の名前）が出て、離れても何も書かない（実フォーカス）
  - `tests/test_crew_operations.py`（新規）
    - golden（`tests/fixtures/crew_operations/golden.json`、33 シナリオ: ストック 9・アクティブ 8・swap・reset 3・clear・get_swap・`set_route` 10）を、デスクトップ（ボード構築済み）とヘッドレスのボードホスト（ボード無し・`_SeedBoards` 済み）の両方で再生する（Set 列・delta・結果・ボード全体・スワップのフラグ）
    - ヘッドレスでクルーの操作が動くこと
    - ボード: core の書き込みと Web の全体操作に追従する（何も書かない）・`state_big_change` に追従する・未描画の変更があるときのストックと名前の編集が他の行を戻さない・アクティブが 1 チーム 1 人で、追いついていないラジオの解除が別のメンバーを消さない・人数のスピン・入力中の名前を飛ばし、離れると新しい値が出る・打っている途中に別のメンバーのストックが Web で変わっても、打った名前が Enter / フォーカスアウトで書かれる・SWAP で打ちかけの名前が運ばれる（実フォーカス）
    - 空の行に打ちかけのまま Web の `reset_players` / `clear_all` → 打った文字が state にも欄にも残らない（直す前に失敗することを確認した）
    - 人数が減って打ちかけの行が隠れる → 描画が何も書かず、人数を戻すと state の値が出る（直す前に失敗することを確認した）
    - 判断 3 の (b): 打っている途中に同じメンバー名が別の所で書かれると、その書き込みが勝ち、そのあと打った文字は書かれる。match バーのコンボも、いるだけの欄に Web から書かれた値を、離れたときに書き戻さない
    - ヘッドレスで swap したフラグを、ハーネスのボード破棄が消す（`tests/conftest.py`）
  - `tests/test_held_back_text.py`（新規・Qt 非依存）: `HeldBackText` の規則そのもの（(a)・(a) のあとの打ち足し・(b)・(b) は最初の描画だけ・他の欄の描画中の打鍵・`Shown` で消えること・欄ごとに独立）
  - `tests/test_player_card_render.py`（選手カードの不具合の修正。直す前に失敗することを確認した）
    - 打っている途中にカードの描画が走っても、打った文字が Enter / フォーカスアウトで state に入り、欄に残り、合成名（`mergedName`）にも出る。描画のきっかけは Web のスコア +1 と、同じカードの別の欄の更新の 2 通り（実フォーカス）
    - 残した文字のあとに打ち足した文字は、もう 1 回描画が走っても書かれる
    - 残した選手名の横でスポンサーが描画されても、欄を離れたときに選手名も合成名も書かない（`lastExportedName` の変更で壊れていないこと）
    - 打っている途中に同じ選手名が別の所で書かれると、その書き込みが勝つ（確定しても何も書かない。後の描画が走っても同じ）。そのあと打った文字は書かれる
    - `player_editor.1`（agent-pitfalls #7）は描画されないので何も残さず、確定した文字はボードの描画があっても書かれる
    - チーム名: 打っている途中に Web からスコア +1 → Enter / フォーカスアウトで、打ったチーム名が state に入り、欄に残り、ロゴも付く
    - 既存の golden（`tests/fixtures/board_operations/*`・`tests/fixtures/player_slots/*`）と S2d のチーム名のテストは変更なしで通る
  - `tests/fixtures/write_recording.py`（新規）: 録画と再生で共用する Set / delta の記録（Qt 非依存）
  - `tests/conftest.py`: ボードの破棄（`_dropBoard`）でクルーのスワップのフラグ（`CrewBattleService._swapped`）も消す。ボードが未構築でも消す（ヘッドレスのボードホストで swap したテストが、次のテストにスワップ済みのボードを渡さない）。アプリ側では起動時の書き込み（`SeedBoardState`）だけが消せば足りる（score.3 を消すのは起動時の `ResetScoreRoot` だけで、その時点でフラグは空）ので、専用の関数は足していない
  - `tests/test_web_operations.py`: クルーの操作を 503 の一覧から外し、ヘッドレスでクルーと実況のルートが応答することを足した（503 の一覧は、ボード 1 / 2 の人数・キャラ数だけになった）
  - `tests/test_crew_board.py` / `tests/test_scoreboard_operations.py`: 削除したウィジェットのメソッドを `board.crew` の core 操作に置き換えた
  - `tests/test_actions.py`: `PublishCommentary` のテストを削除した
- **残置 / リスク**
  - スピンボックス（ストック・人数）とラジオ・チェックボックス・アイコンのコンボは、操作中でも描画で上書きされる（テキスト欄とクルーの match バーのコンボだけが入力中に飛ばされる）。数字を打ち込んでいる最中に Web が同じメンバーのストックを変えると、打っていた値が消える
  - 実況パネルとクルーボードは、関係する delta のたびに 1 回描画する（`singleShot(0)` でまとめる）。描画のたびに実況の 12 枠を deep copy する。`str(delta)` の判定はボード 1 / 2 と同じ
  - クルーのスワップのフラグは state の外なので、再起動で `False` に戻る（旧実装と同じ。中身はスワップされたまま）
  - `ws_crew_member_stock` などに `team` / `member` が無いと、旧実装どおり例外（ログ）で ack は返らない
  - 実況パネルを開いていないときの Web の更新は、パネルの構築を待たずに書く。構築時の描画で state が出る
  - クルーボードで人数が減って打ちかけの行が隠れると、打ちかけは書かれずに捨てられる（行が無くなるので確定しない扱い）
- **既存の不具合（修正済み）**
  - 選手カード（S2a〜）で選手名などを打っている途中に、そのカードの描画が走る（同じボードのスコア +1・同じカードの別の欄の更新など。Web / Stream Deck からでも）と、Enter / フォーカスアウトで打った文字が書かれず、欄も空（state の値）に戻っていた。配信中に起こりうるので S3e-2 で直した
  - 原因は判断 3 の「直した点」と同じ（`_RenderText` が入力中の欄の現在の文字を無条件に `_heldBackText` に入れていた）。判断 3 と同じ規則（`HeldBackText`）に置き換えた
  - もう 1 つ、描画がスポンサーなどを書き換えたときに `lastExportedName` を欄の文字（打っていた選手名を含む）で組んでいたため、打った選手名を確定しても合成名（OBS に出る名前）が publish されなかった。残した文字の欄以外は slot の値で組むようにした。逆に、打ちかけの選手名が別の所の書き込みで残った文字になったときは、確定で古い合成名を publish して id を落としていた（名前の欄を飛ばした描画では組み直していなかった）。これも直した
  - ボード 1 / 2 のチーム名の欄（S2d の `_heldBackTeamName`）も同じ規則だった。チーム名を打っている途中に Web からスコア +1 → Enter / フォーカスアウトで、打ったチーム名が書かれず、欄も空に戻っていた。印と判定を分け、判定を `HeldBackText` にした
- **リリースノート案**
  - `load-commentator-from-tag` が成功時に `OK` を返すようになった（従来は 500 エラー。Socket.IO は null）。9〜12 人目の実況者にも読み込める。選手 DB に無いタグは `ERROR`
  - スコアボードで選手名などを打っている途中に、Web / Stream Deck からスコアを変えたり同じ選手の別の欄が変わったりすると、打った文字が確定されずに消えていた不具合を直した
  - チーム戦のスコアボードでチーム名を打っている途中に、Web / Stream Deck からスコアを変えると、打ったチーム名が確定されずに消えていた不具合を直した
  - ヘッドレス起動（`--headless`）で、クルーボードの操作（ストック・出場中のメンバー・人数・Swap・リセット / クリア）と実況の更新・タグからの読込が使えるようになった（従来は 503）
- **実機確認（オーナー）**
  - デスクトップ・クルーボード
    - ストックの ± とスピンへの数字入力・出場中のラジオ（別のメンバーに付け替えると前の印が外れる）・人数のスピン・メンバー名・チーム名・色・Phase / Pool / Match・表示大会名が、今までどおり反映され、合計が合う
    - SWAP（名前を打ちかけのまま押すと、その名前が相手側に運ばれる）・ストックのリセット・X 投稿
    - Web / Stream Deck から `crew-member…-stockup/down`・`crew-active…`・`swap-teams`・`reset-scores`・`clear-all`・`set?players=` を送る → ボードの表示がすぐ追従し、OBS のクルーオーバーレイも変わる
    - メンバー名を打っている最中に Web から同じメンバーのストックを変える → 名前欄は打っている文字のまま、ストックと合計は変わる。欄を離れても打っていた文字が消えない
    - 空の行に名前を打ちかけのまま Web から `/scoreboard3-reset-players` / `/scoreboard3-clear-all` → 打った名前が消え、欄を離れても戻らない
    - 5 行目に名前を打ちかけのまま Web から `set?players=3` → 5 行目が隠れる。人数を 5 に戻すと 5 行目は空
  - デスクトップ・実況パネル
    - 各欄の入力・役割の補完・アイコン・「配信に表示」（4 人で残りが無効になる）・≡ のドラッグ並べ替え・名前の補完（選手 DB）が今までどおり反映される
    - 4 人を「配信に表示」にすると、5 人目以降のチェックが無効になり、表示中の 4 枚のカードだけが明るくなる。1 人外すと、残りのチェックが押せるようになる
    - パネルを開いたまま `/update-commentary-3` を送る → 3 番のカードがすぐ変わる。続けて 1 番の名前を編集しても、3 番が古い内容に戻らない
    - 3 番の名前欄を編集中に `/update-commentary-3` を送る → 欄は打っている文字のまま。欄を離れると送った名前が出る（打っていた文字は書き戻されない）
    - `/load-commentator-from-tag-12?tag=<DB にいるタグ>` → `OK` が返り、12 番に入る。DB に無いタグは `ERROR`
    - パネルを一度も開かずに `/update-commentary-1` を送る → OBS の実況オーバーレイが変わり、あとでパネルを開くとその内容が出る
  - デスクトップ・スコアボード（選手カード）
    - 選手名を打っている途中に Web / Stream Deck からスコア +1 → Enter → 打った名前が欄に残り、OBS の名前にも出る（フォーカスアウトで確定しても同じ）
    - 選手名の欄にいるだけ（打たない）のときに Web から名前が変わる → 欄は前の名前のまま。離れると新しい名前が出て、前の名前は書き戻されない（従来どおり）
    - 選手 DB エディタ（PLAYERS）で名前を打って確定すると、今までどおり反映される
    - チーム戦（2 人以上）でチーム名を打っている途中に Web / Stream Deck からスコア +1 → Enter → 打ったチーム名が欄に残り、OBS のチーム名（とロゴ）にも出る
  - ヘッドレス（`python main.py --headless`）
    - 上のクルーの Web 操作と `/update-commentary-<N>` が 503 にならず、オーバーレイが変わる

#### S3e-8 Top 8 取り込みの core 化（2026-10-26・PR #235）

Start.gg からの Top 8 の取り込みを、Top 8 パネルから core の `Top8Service` へ移した。パネルのボタン・ホットキー（`refresh_phase_group`）・legacy `/update-bracket`・Socket.IO `update_bracket`・v1 `bracket:refresh` が同じ関数を呼び、パネルを構築せず、ヘッドレスでも動く。パネルは state を描画するだけになった（S3e-2 の実況パネルと同じ型）。外部契約の形（legacy の本文 `"OK"`・Socket.IO の ack・v1 の 202 / 409 のエンベロープとエラーコード・`program_state.json` のキー順・Socket.IO の delta の形）は変えていない。変わったのは応答が出る条件（409 `CONFLICT` が出る期間など）と、ゲームの切り替えで Top 8 のキャラが空になること（下の「外から見える変化」）。移す前の木（cff44408）でパネルのボタン経由の取り込みを録り（Start.gg はモック）、core 版と照合している。

- **実装**
  - `src/core/Top8Service.py`
    - `RefreshFromProvider(done=None)`: 取り込みの唯一の入口。すぐに `STARTED`（provider のワーカーで走る）/ `BUSY`（実行中。この要求は捨てる）/ `NO_TOURNAMENT`（大会が無い・fetcher が無い）を返す。`done` は試行の終了時に 1 回（フラグを下ろした後。`BUSY` では呼ばない）
    - `fetcher`（モジュール属性。`fetcher(job, status)` → 始めたら True）: core は `src` を import しないので、provider への道はシェルが入れる。`bootstrap_core()` が `AutonaTournamentDataProvider.instance.RunWithProvider` を入れる
    - `ImportFromProvider(provider)`: phases を取得 → `TargetPhaseGroup`（最後の phase のダブルエリミのグループのうち最初の 1 つ。旧パネルの `_resolveTargetPhaseGroup` と同じ規則。id が無ければ None）→ `GetTournamentPhaseGroup` → `ImportPhaseGroup`
    - `ImportPhaseGroup(data, games=None, countries=None)`: `core/Bracket` → `Top8Model.FromBracket` → 選手ごとに `ResolveImportedPlayer`（国は `CountryCatalog.BasicCountryInfo`、キャラは `GameAssetCatalog.Current()` 1 回のスナップショットから、ゲームの mains の先頭を en_name か codename で引いてコピー）→ `ImportBracket`。解決と書き込みは `StateManager.lock` の中（判断 8 の競合対策）。dict でない・写せないグループは何も書かない
    - `ForgetCharacters()`: 8 枠のキャラ（スキンはキャラの値に含まれる）を空にする。キャラがある枠だけを、1 保存サイクルで書く。呼ぶのは `GameAssetCatalog.SelectGame` だけ（判断 8）
    - パネルの編集用に `SetPlayerText(slot, field, text)`（name / sponsor。前後の空白を落とす）/ `SetPlayerCountry` / `SetPlayerCharacter` を足した。どれもその 1 キーだけを、変わったときだけ書く（`StateManager.lock` の中で `SetIfChanged`）。`PublishPlayers`（8 枠の全量 publish）は削除した
  - `src/core/GameAssetCatalog.py`: `SelectGame` が、state の `game.codename` が空でなく新しい codename と違うときに、writer lock を離した後・自分の `SaveBlock` の中で `Top8Service.ForgetCharacters()` を呼ぶ（判断 8）
  - `src/core/Bracket.py`（`src/AutonaBracket.py` から移動）: ラウンド名（`matchNames`）を引数で受ける。読んでいた `AutonaLocaleHelper.matchNames` は呼び出し側（`AutonaAnalysisWidget`）が渡す。Top 8 の取り込みはラウンド名を使わない（渡さない）
  - `src/AutonaTournamentDataProvider.py`: `RunWithProvider(job, status)` を足した（今の provider を 1 回読み、無ければ False。provider のスレッドプールで `job(provider)` を走らせ、その間フッターに `status` を出す。フッターの表示に失敗しても job は必ず 1 回呼ぶ（判断 9）。例外はログ）。Top 8 専用だった `GetTournamentPhaseGroup` ラッパーと `tournament_phasegroup_updated` シグナルを削除した（受け手が無くなった）
  - `src/AutonaHotkeys.py`: `ConnectActions()` が `refresh_phase_group` を `Top8Service.RefreshFromProvider` に直結する
  - `src/Autona.py`: `EnsureTop8Panel()` と `RefreshTop8PhaseGroup()`、ホットキーの接続を削除した
  - `src/AutonaWebOperations.py`: `update_bracket(done=None)` を足した（`RefreshFromProvider` を呼んで答えを返す）
  - `src/AutonaWebServer.py`: `/update-bracket` と `update_bracket` イベントを `WebOps.update_bracket()` に向けた（本文と ack は旧実装どおり常に `"OK"`）
  - `src/AutonaApiV1.py`: `bracket:refresh` は provider が無ければ 409 `NOT_READY`、`BUSY` なら 409 `CONFLICT`、始まれば 202。`_HeavyOpGuard` の `bracket` キーは使わなくなった（`tournament` / `game` だけ）
  - `src/AutonaWebServerActions.py`: `update_bracket` を削除した（残りは公開 13 本）
  - `src/AutonaTop8Widget.py`: state を描画するパネルにした
    - 削除: phases のキャッシュ（`_phases` / `_loadQueuedOnPhases` / `UpdatePhases`）・`RefreshPhaseGroup` / `UpdatePhaseGroup`・組み立てのワーカー・`_resolveImportedPlayer`・`_loadFromState` / `_syncPlayersToState`（入力欄から 8 枠を組み直す全量 publish）・`_updating` / `_updatingPhaseGroup`・provider のシグナルの接続
    - 読み込みボタン → `RefreshFromProvider`。name / sponsor の確定 → `SetPlayerText`（`HeldBackText` の規則で、描画が残した文字は書き戻さない）。国・キャラ・スキン → `SetPlayerCountry` / `SetPlayerCharacter`。表示ラベル・反映・全リセットは従来どおり `Top8Service`
    - `_RenderFromState`: Bridge の `state_updated`（`['top8'` を含む delta）/ `state_big_change` で `singleShot(0)` に 1 回にまとめ、`QSignalBlocker` で描画する。入力中の欄（テキスト・国 / キャラのコンボ）は飛ばし、離れたら描画する（`focusChanged`）。試合のスコアのスピンは、試合（`top8.matches`）が変わったときだけ描画する（未反映のスコアを選手の編集で消さない）。キャラは、state のキャラがコンボの選択から組めるものと同じなら触らない（選んだスキンを保つ）
    - 表示ラベルの欄は state の値を文字で出す（既定の「TOP 8」も。旧実装どおり）。操作者が空にして確定した欄だけは空のまま（判断 10）
    - ゲームの切り替え（`_onGameChanged`）はコンボをシグナルを止めて空にし、描画を予約するだけ（何も書かない。キャラを空にするのは core。判断 8）。国の再ロード（`countriesAboutToReload`）の間は国の書き込みを止め、`countriesUpdated` で state から選び直す
- **判断と理由**
  1. **実行中フラグは取り込み 1 回に 1 つで、全入口が共有する**。取り込み全体（phases の取得・グループの取得・組み立て・書き込み）が終わるまで立ち、`finally` で下ろす。実行中の要求はキューせず捨てる（同じものを取りに行くだけなので）。旧 v1 はこのルート専用のフラグで、パネルに取得を頼んだ時点で下ろしていた（取得中に 2 回目を送ると 202 になり取得が 2 本走った。パネルのボタン・ホットキーにはガードが無かった）。TTL は持たない（Start.gg の取得は 1 回 20 秒・指数バックオフで有限に終わり、例外でも `finally` で下ろすため。旧 `_HeavyOpGuard` の TTL 120 秒は「完了の合図が来ない」場合の保険だった）
  2. **v1 の応答**: provider が無ければ受付前に 409 `NOT_READY`（従来どおり）、実行中は 409 `CONFLICT`、始まれば 202。legacy / Socket.IO は実行中・大会なしでも従来どおり `"OK"`（本文を変えない）
  3. **phases は毎回取得する**（1 回の取り込みで Start.gg に 2 リクエスト）。旧パネルは大会の読込時に届いた phases をキャッシュしていたため、読込後にできた phase（決勝の phase を後から作る大会）を拾えなかった。core はキャッシュを持たないことで、大会の切り替えや phase の追加で古い対象を引かない
  4. **Top 8 の取り込みは `tournament_phases_updated` を出さなくなった**。旧パネルは phases が未キャッシュのとき provider のラッパー経由で取得し、そのシグナルでスコアボードの pool 候補・セット選択・分析パネルにも phases を配っていた。これらは大会の読込時に同じシグナルを受け取っているので、取り込みのたびに配り直す必要は無い
  5. **パネルの編集は 1 欄だけ書く**（S3e-2 判断 2 と同じ）。旧 `_syncPlayersToState` は確定のたびに 8 枠を入力欄から組み直して `top8.players` を丸ごと `Set` しており、パネルがまだ描画していない変更（Web からの取り込み）を古い値で上書きしえた。Socket.IO の delta は葉の単位で出るので形は変わらない（変化なしの確定は何も書かなくなった）。Set のキーは `top8.players` から `top8.players.{N}.{field}` に変わる（`program_state.json` のキー順は既存のキーの上書きなので変わらない）
  6. **パネルは取り込みでもホットキーでも構築しない**（`EnsureTop8Panel` を削除）。core が state を書き、パネルは開いたときに state を描画する
  7. **`Bracket` のラウンド名は注入にした**。core は `AutonaLocaleHelper` を読めない。Top 8 の取り込みは `FromBracket` がトポロジだけを読むので名前は要らず、分析パネルだけが渡す
  8. **ゲームが別のゲームに変わったら、core が Top 8 のキャラを空にする**（2026-10-26 のレビューでのオーケストレータ判断）。理由: 別のゲームのキャラは意味を持たない。パネルを開いていたときの旧挙動とリザルトパネルの挙動に揃う。パネルの表示と state のずれ（コンボは空なのにオーバーレイに前のゲームのアイコンが残る）が根本から無くなる
     - 条件: state の `game.codename` が空でなく、新しい codename と違うときだけ。選択（catalog の `Current().codename`）ではなく state を見る（大会 URL を消すと選択は忘れるが `game` は残り、キャラはそのゲームのもの）。起動時は `ResetGameState` で `game` が空なので、前回のゲームの復元では消さない（`state.json` に保存した Top 8 は再起動後も残る）。同じゲームの選び直し（スキップ）・mods の再ロードでも消さない
     - 結線: `game.*` の唯一の書き手 `GameAssetCatalog.SelectGame` から呼ぶ。デスクトップのアセットアダプタとヘッドレスのローダーはどちらもここを通るので、両シェルで同じ挙動になる。切り替えと同じ 1 保存サイクルで、キャラがある枠だけを書く（delta が最小）。observer は使わない（pitfalls #16 も「サービスは observer を購読しない」も踏まない）。catalog の writer は呼ばない。呼ぶのは writer lock を離した後なので、ロックは catalog lock → `StateManager.lock` の一方向のまま（`_Publish` と同じ）。`Top8Service` が catalog を import しているので、catalog は関数の中で import する
     - 取り込みとの競合: 取り込みは選手の解決と書き込みを `StateManager.lock` の中で行う。切り替えはスナップショットを差し替えてから `ForgetCharacters`（同じロックを取る）を呼ぶので、取り込みが前のゲームで解決していれば、その書き込みの後で空になる。前のゲームのキャラが新しいゲームに残ることはない
  9. **`RunWithProvider` は、フッターの表示に失敗しても job を必ず 1 回呼ぶ**（レビュー指摘）。この PR の最初の形は、`AutonaStatusManager` の import / `push` が job の前の try の外にあり、そこで例外が出ると job が呼ばれず、Top 8 の実行中フラグが下りないまま、以後の要求がすべて `BUSY`（v1 は 409）になった。表示は表示にすぎないので、失敗はログだけにして job を呼ぶ
  10. **表示ラベルの欄は、state の値を文字で出す**（レビュー指摘。既定の「TOP 8」も文字で入れる旧実装に戻した）。この PR の最初の形は、state が既定値で欄が空なら空のまま（プレースホルダ）にしており、パネルを開いた直後の欄が空だった。操作者が欄を空にして確定したときだけは、旧実装どおり空のまま（プレースホルダで TOP 8）にする
- **外から見える変化**（形は同じで、出る条件が変わったもの）
  - v1 `bracket:refresh` の 409 `CONFLICT` が、取り込みが終わるまで（取得・組み立て・書き込み。Start.gg が遅いと数十秒）返る。旧実装はこのルート専用のフラグを、GUI スレッドがパネルに取得を頼んだ直後に下ろしていたので、409 が出るのは受付からその瞬間までだけで、取得中の 2 回目は 202 になって取得が 2 本走った。フラグを共有するので、パネル・ホットキー・legacy で始めた取り込みの最中も 409 になる
  - v1 の受付前の確認のあとで大会が外れていた場合は 409 `NOT_READY`（旧実装は 202 を返して何もしなかった）
  - ヘッドレス: v1 は 503 `UNAVAILABLE` → 202 / 409。legacy `/update-bracket` と Socket.IO `update_bracket` は 503 → `"OK"`
  - legacy / Socket.IO の `"OK"` は GUI スレッドを待たずに返る（旧実装は `@gui_thread_sync` で、パネルが取得を頼むまで待った）。取り込み中・大会なしでも `"OK"` で、何もしない。旧実装でパネルを構築できなかったときの直接取得（例外で 500 になりえた）は無くなった
  - パネルのボタンとホットキーは、取り込み中は何もしない（旧実装は押すたびに取得が走った）
  - ゲームを別のゲームに切り替えると、Top 8 の 8 枠のキャラ（スキン）が空になる。パネルを開いているか、どちらのシェルかによらない（旧実装はパネルを構築しているときだけ空になり、未構築なら前のゲームのキャラが残った。判断 8）
  - Top 8 の取り込みは `tournament_phases_updated` を出さない（判断 4）
- **pin**
  - `tests/test_top8_refresh.py`（新規）
    - golden（`tests/fixtures/top8_refresh/golden.json`、10 シナリオ: リセットありの完了・seed あり・進行中・敗者側未配線・最後の DE phase の最初の DE グループ・ゲーム未ロード・人数不足・空のグループ・DE phase なし・phases なし）を cff44408 のパネルのボタン経由で録画した（取得した phase / グループ・国の照会・Set 列・delta・big change・`top8` 全体）
    - 同じ golden を、core 直・パネルのボタン（取り込み後のパネルの表示が state と一致することも）・ホットキー（パネル未構築）・ヘッドレスの legacy `/update-bracket`・Socket.IO `update_bracket`・v1 `bracket:refresh` で再生する
    - 2 回目の要求: 取り込み中は `BUSY`、v1 は 409 `CONFLICT`、legacy は `"OK"`、ホットキーは無視され、取り込みは 1 回・`done` は 1 回だけ。終わればまた始められる
    - provider の例外・`None` を返す取得 → 何も書かずにフラグを下ろす。大会なし・fetcher なし → `NO_TOURNAMENT`、v1 は 409 `NOT_READY`、legacy は `"OK"`
    - `TargetPhaseGroup` の規則（最後の DE phase の最初の DE グループ・id なし・list でない）
    - パネル: 編集がその 1 欄だけを書く・描画（`_onGameChanged`・big change を含む）は何も書かない・キャラとスキンを保つ・未反映のスコアが選手の編集で消えずに反映される・表示ラベルを空にすると既定値の扱い（他の描画でも空のまま）・新しいパネルは既定の表示ラベルを文字で出す（直す前に失敗することを確認した）・実フォーカスで、入力中の欄にその欄自身の取り込みが勝ち、他の欄の描画では打った文字が残る
    - フッターの表示（`AutonaStatusManager` の import / `push`）が失敗しても、取り込みが走り、フラグが下り、次の要求が始まる（直す前に失敗することを確認した）
    - ゲームの切り替え: デスクトップのアセットアダプタとヘッドレスのローダーの両方で、起動時の復元・同じゲームの選び直し・再ロードでは残り、別のゲームで空になる（キャラがある枠だけ・切り替えと同じ 1 サイクル）。開いているパネルは空のキャラを表示し、自分では何も書かない
  - `tests/test_top8_service.py`: `PublishPlayers` のテストを、1 欄ずつの操作のテストに置き換えた（そのキーだけ・空白を落とす・変化なしは書かない・不明な枠 / 欄は何もしない・国・キャラ）。`ForgetCharacters`（キャラがある枠だけ・1 サイクル・何も無ければ書かない）
  - `tests/test_game_asset_catalog.py`: catalog 単体で判断 8 の規則（起動時の復元・同じゲーム・mods の再ロード・選択を忘れた後の同じゲーム・ロードしない codename では残る／別のゲーム・選択を忘れた後の別のゲームで空になる／切り替えと同じ 1 サイクルで、キャラがある枠の delta だけ）
  - `tests/test_signal_lifetime.py`: 破棄した Top 8 パネルに、予約済みの描画・delta・big change・`onLoad`・国の再ロードが届かない（接続数が元に戻り、描画は走らず、何も書かず、例外も出ない）
  - `tests/test_country_helper_model.py`: Top 8 の代役を実パネルに置き換えた（国の再ロードで Top 8 が何も書かず、コンボが state の国を指す）
  - `tests/test_bootstrap_split.py`: `bootstrap_core()` の手順に `Top8Service.fetcher = ...RunWithProvider` を足した（provider の生成の直後）
  - `tests/test_core_qt_free.py`: `src.core.Bracket` を core の部品として pin した
  - `tests/test_web_operations.py` / `tests/test_headless_smoke.py`: `bracket:refresh` を 503 の一覧から外した（ヘッドレスで provider が無ければ 409 `NOT_READY`）
- **残置 / リスク**
  - 取り込み中に大会を切り替えると、古い大会の結果が書かれる（取り込みは始めた時点の provider を使う。旧実装も同じ）
  - 1 回の取り込みで Start.gg へのリクエストが常に 2 本になった（phases を毎回取る。旧実装は phases をキャッシュ済みなら 1 本。判断 3）
  - ゲームを別のゲームに切り替えると Top 8 のキャラは空になる（判断 8）。最後に選んだゲームと大会のゲームが違うと、起動時に 2 回ロードされ（前回のゲーム → 大会のゲーム）、2 回目で空になる（規則どおり。大会を読み込むとゲームは大会のゲームになる）
  - 切り替えのスナップショットの差し替えから `ForgetCharacters` までの間（数 µs〜ms）に取り込みが新しいゲームで解決して書くと、そのキャラも空になる（ずれは残らないが、取り込んだキャラが消える。取り込み直せば入る）
  - キーが欠けた枠（壊れた保存データ）に 1 欄書くと、そのキーは dict の末尾に足される（旧実装は全量 publish で並びを作り直していた）。今のビルドが書く枠は常に全キーを持つ
  - パネルは `top8` の delta のたびに `top8` を deep copy して描画する（8 枠と試合 11 件で小さい）
  - スピン（試合のスコア）は、試合が変わると入力中でも描画で上書きされる（未反映のスコアは取り込み・反映・全リセットで消える。旧実装と同じ）
- **既存の不具合（報告のみ・未修正）**
  - `Bracket.UpdateBracket` は winners を losers より先に処理するので、Start.gg の slot に `seedNum` が無いと Grand Final のリセットの選手が解決されないことがある（実データは `seedNum` を持つので影響は小さい。配信事故にはならない）
  - スキンを選んだキャラは、パネルを作り直す（再起動を含む）とキャラのコンボが空になる（スキンのデータに `en_name` が無く、コンボの行に戻せない。表示だけで state とオーバーレイは正しい）
- **既存の不具合（修正済み）**
  - パネルで 1 欄を確定するたびに 8 枠を入力欄から publish していたため、パネルが描画していない Web からの取り込みを古い入力で上書きしえた（判断 5）
  - パネルの描画（`_loadFromState`）がスキンを毎回先頭に戻していた（今は state のスキンを選ぶ）
- **リリースノート案**
  - ヘッドレス起動（`--headless`）で、Top 8 の Start.gg からの取り込み（`/update-bracket`・API の `bracket:refresh`）が使えるようになった（従来は 503）
  - Top 8 の取り込み中に、もう一度取り込みを要求しても二重に取得しなくなった（API は 409 `CONFLICT`）
  - Top 8 の取り込みが、大会の読込後に作られた決勝の phase も拾うようになった
  - ゲームを切り替えると Top 8 のキャラは空になる（Top 8 パネルを開いていなくても。同じゲームを選び直したとき・再起動したときは残る）
- **実機確認（オーナー）**
  - デスクトップ・Top 8 パネル
    - 大会を読み込んで「Start.gg から読み込む」→ フッターに「TOP 8 を構築中...」が出て、8 枠・試合・OBS の Top 8 オーバーレイが埋まる
    - パネルを一度も開かずに Ctrl+Shift+P → オーバーレイが埋まり、あとでパネルを開くとその内容が出る
    - 選手名・スポンサー・国・キャラ・スキンの編集、試合スコアの入力 → 反映・全リセット・表示ラベル（空にすると TOP 8）が今までどおり
    - 選手名を打っている途中に Web から `/update-bracket` → 欄は打っている文字のまま。欄を離れると取り込んだ名前が出る（打っていた文字は書き戻されない）
    - スキンを選んだあと別の欄を編集・取り込み → スキンが先頭に戻らない
    - パネルを開くと、表示ラベルの欄に「TOP 8」（または設定した文字）が入っている
    - Top 8 にキャラが入った状態で、ゲームを別のゲームに切り替える → パネルを開いていてもいなくても、8 枠のキャラが空になり、OBS の Top 8 のアイコンも消える。同じゲームを選び直す・アプリを再起動する → キャラは残る
  - Web / Stream Deck: `/update-bracket`・Socket.IO `update_bracket` が `OK` を返して取り込む。v1 `POST /api/v1/bracket:refresh` が 202、取り込み中の 2 回目は 409 `CONFLICT`、大会なしは 409 `NOT_READY`
  - ヘッドレス（`python main.py --headless`）: 大会を読み込んで `/update-bracket` または `bracket:refresh` → `top8.*` が書かれ、OBS の Top 8 オーバーレイが変わる。`POST /api/v1/game` で別のゲームに切り替える → Top 8 のキャラが空になる

#### S3e-3 S2f: 選手データの core 化（2026-10-26・PR 未作成）

選手データの読込（カードの `SetData`）・カードのクリア・DB への保存を、選手カードから core の `PlayerService` へ移した。セットの entrants・Web の選手編集（`update_player` / `set_team_data`）・タグからの読込（`load_player_from_tag`）・補完の選択・PLAYERS エディタ・カードのクリアボタンが同じ操作を呼び、カードは操作を依頼して state を描画するだけになった。外部契約（`program_state.json` のキー順・delta、Socket.IO / legacy / v1 の応答、`local_players.json` の形式）は変えていない。移す前の木（ae922c24）でカード・ボードの入口から 11 シナリオを録り、カード経由と core だけの両方で照合している。レビューで、既存の不具合 3 件（読込で前の選手の id・city が残る・スポンサーだけの読込の例外・数値でないスキンの例外）を直し、都市→地域表の読込を `CountryCatalog.Load()` へ移し、互換層 `src/Helpers/AutonaBadWordFilter.py` を削除した（判断 10〜12）。2 回目のレビューで、読込が打ちかけのコンボの文字を残す・メモ欄が描画の残した文字を書き戻す・singles ⇔ チームのセット読込でアップセット係数が古いまま、の 3 件（どれもこの移行で入ったもの）を直した（判断 3・4・9）。Web の 3 入口の `WebServerActions` からの移設は S3e-4 の範囲で、今回は従来の経路のまま core を呼ぶ。

- **実装**
  - `src/core/PlayerService.py`
    - `ApplyData(data, loadFromDB=True, clear=True, no_mains=False)`: 旧カードの `SetData` が出していた書き込みを state だけから再現する。1 保存サイクル。順序は、クリア（`_Clear(forLoad=True)`）→ DB の記録 → `data`。後ろの 2 つはそれぞれ `_ApplyFields`（名前 3 欄 → avatar → id → city → twitter → メモ → 国 → 地域 → mains → seed）。最後に合成名と画像を出す（途中で例外が出ても `finally` で出す）。DB の記録は `PlayerDB.lock` の中でコピーし、そのロックを離してから `StateManager.lock` を取る（入れ子にしない）
    - 旧カードが書かなかったものは書かない。テキストは state と違うときだけ書く（旧カードの `setText` + `editingFinished` は同じ文字なら何も出さなかった）。国・地域・キャラも、旧カードがコンボを動かさなかったものは書かない（表示中の国と、地域の行がどの国のものかを `location` で追う）
    - 悪語フィルタは `data` と DB の記録の**コピー**にかける（旧実装は呼び出し元の dict と、メモリ上の DB の記録そのものを書き換えていた）
    - DB の照合は、タグがあるときだけ行う（スポンサーだけでは記録を引けない。旧実装は `PlayerDB.PlayerTag` が `TypeError` を出した）
    - mains のスキンが数値でなければ、最初のスキン（0）として読み、warning を出す（`_SkinNumber`。判断 7）
    - 地域の行は `CountryCatalog.StateCodes()` で引く（code と original_code だけ。`States()` は地域ごとに旗のファイルを確かめるので、`StateManager.lock` の中では使わない。判断 11）
    - `SlotChange(idChanged, seedChanged)` / `Identity()` / `ChangeSince()`: 操作の前後の (id, seed) を比べる。統計のトリガー用
    - `SlotFromValue(value)` / `_CharacterSlots()`: スロットの `character` の値から、カードが表示する選択（en_name・スキン行・バリアント）を逆引きする（`CharacterValue` の逆）。キャラ数は、S3e-6 までは `character` のキー数で決める（無ければ既定の 3）
    - `ClearSlot(no_mains)` / `_Clear()`: カードのクリアボタン（旧 `Clear()`）。`AutonaActions._ClearSlot` をここへ移し、`ClearPlayers` も各スロットの `ClearSlot` を呼ぶ。`forLoad` を足した。これは読込の先頭のクリアで、合成名・画像を出さず、国は state に残して隠すだけにする（後に国が来なければ空を出す）。id・city は、読込が持ってこなければ空にする（`keep`。判断 10）
    - `SavePlayerToDB()`: state から `PlayerDB.fieldnames` の形を組んで `AddPlayers(overwrite=True)` を呼び、キャラスロットを mains 込みで publish し直す（旧カードと同じ）
  - `src/AutonaScoreboardPlayerWidget.py`
    - `SetData` / `Clear`: 1 SaveBlock で `ApplyData` / `ClearSlot` → `AnnounceChange` → `_ShowOwnOperation` を行う。`_ShowOwnOperation` は、打ちかけのコンボの文字を捨て、入力中の欄も含めて描画する（エディタは Save / Update のラベルと Delete の有効化も更新する）。モデルの走査・`setText`・`editingFinished` の emit は無くなった
    - `SavePlayerToDB` は `self.player.SavePlayerToDB()` を呼ぶだけ。補完の選択は `_OnCompleterPick` → `SetData`
    - `AnnounceChange(SlotChange)`: 統計のシグナル（`playerId_changed` / `player1Id_changed` / `player2Id_changed` / `player_seed_changed`）を出す。旧実装は id・seed のウィジェットのハンドラが、値が動くたびに出していた
    - `RenderFromState` は `_RenderSlot(over=False)` を呼ぶ（ボードの held サイクル）。`over=True` はカード自身の操作の後で、入力中の欄も描画する（`_LeftAlone`）
    - `DiscardTypedText(notEditing=False)`: 編集可能なコンボ（国・地域・キャラ / スキン / バリアント）に、選択行と違う打ちかけの文字があれば、シグナルを止めて選択行の文字に戻す（何も書かない）。`notEditing=True` は、入力中のコンボを飛ばす（ボードの読込用。判断 9）
    - `_OnNoteChanged`: メモ欄も `HeldBackText` で判定する（描画が残した文字に戻る変更は書かない。判断 4）。描画は、入力中のメモ欄を飛ばすとき `LeftAlone` を記録する
    - `NameChanged` の一括読込ガードを削除した（読込が欄を経由しなくなったため）。`AutonaCountryHelper` / `AutonaBadWordFilter` の import も削除した
  - `src/AutonaBasePlayerWidget.py`: `dataLock`・`ExportPlayerCity`・ウィジェットの `Clear` を削除した
  - `src/AutonaScoreboardWidget.py`
    - `_ApplyPlayer(card, data, loadFromDB, clear, no_mains)`: カードの `player` の `ApplyData` を 1 SaveBlock で呼び、前後の比較で `card.AnnounceChange` を呼ぶ。クリアする読込の後は `card.DiscardTypedText(notEditing=True)` を呼ぶ（判断 9）。`SlotChange` を返す
      - 呼ぶのは `_ApplyEntrants`・`_WritePlayerSlot`・`LoadPlayerFromTag`。カードの `SetData` は経由しない
      - `_WritePlayerSlot` は、`savePlayerToDb` があれば続けて `card.player.SavePlayerToDB()` を呼ぶ
      - `playerNumber.setValue` は残した（S3e-6 の範囲）
      - `_ApplyEntrants` は、人数が singles ⇔ チームで変わり、どの選手の seed も動かなかったとき True を返し、`ChangeSetData` がアップセット係数を 1 回計算させる（`bracket_type` の変化と合わせて 1 回。判断 3）
    - `NewSetSelected` / `_ApplyEntrants` / `_WritePlayerSlot` から `dataLock` を削除した
    - `_ClearingPlayers`: クリアの後に、各カードの `DiscardTypedText()` を呼ぶ
  - `src/AutonaWebServerActions.py`: `update_player` のコメントだけ変えた（書き込みは core。GUI スレッドに渡すのは、ロスターの確認と統計のため）
  - `src/core/CountryCatalog.py`: `Load()` が都市→地域表も 1 回読む。旗を解かないリーダー `StateCodes()` を足した（判断 11）
  - `src/AutonaPlayerBrowserWidget.py`: エディタの読込の後の Save / Update ラベル・Delete の更新の重複を削除した（読込の `_ShowOwnOperation` が行う。新規登録のときだけ呼ぶ）
  - コメント・docs の追随: `AutonaSelectSetWindow.py`・`core/SetLoadService.py`・`core/CountryCatalog.py`・`.claude/known-smells.md`（S-01）・`docs/tasks/implement-startup-step3f-data-side-effects.md`
  - `src/Helpers/AutonaBadWordFilter.py` を削除した（判断 12）
- **判断と理由**
  1. **読込を state への 1 操作にし、カードはそれを描画する**（設計どおり）
     - 旧 `SetData` は、フォームに入力してハンドラに書かせる形だった。そのため Set 列がウィジェットの状態（どのコンボがどの行を出しているか）で決まっていた
     - `ApplyData` は「旧カードが何を書いたか」を state から再現し、書かなかったものは書かない
     - 録画との照合では、Set 列・保存サイクル・統計のトリガー・最終の state（キー順込み）・カードの表示・`local_players.json` のバイトが一致する。意図した差分は判断 2・3 と、エディタのクリアの順序（下の pin）だけ
  2. **同じキーへの連続した書き込みは 1 回にまとめた**
     - 旧カードは、コンボが 1 つ動くたびにキャラスロット全体を publish していた（3 行が動けば 3 回）
     - delta は前回の保存との差分なので、まとめても `program_state.json` と Socket.IO の delta は変わらない
     - 照合では、その区間だけ連続を 1 つにして比べる（`tests/test_player_slot_publish.py` も同じ）
  3. **統計は、正味の変化 1 回につき 1 回だけ取り直す**
     - 旧実装は、seed のスピンボックスが動くたびにアップセット係数を計算し直していた。読込の先頭のクリアで 0 になり、読込で元の値に戻る
     - そのため、同じ seed の選手を上書きすると（同じ選手の読み直し・セットの再読込）、変わっていない seed のためにカード 1 枚あたり 2 回計算していた
     - 今は操作の前後で比べる（`SlotChange`）。seed も id も変わらなければ何も出さない
     - ただし係数は singles のときだけ seed の差で、チームでは 0（`StatsService.IsSingles`）。旧実装は読込のたびに計算していたので、seed が同じでもセット読込で singles ⇔ チームが変われば計算し直されていた。今は、人数がそう変わり、どの seed も動かなかったセット読込で 1 回計算する（2 回目のレビュー指摘。それまでは前の組み合わせの係数が残った）。他の場面の回数は変えない
  4. **読込は、入力中の欄を上書きしない**（S3e-2 までに確立した HeldBackText の規則 (b)）
     - セットの entrants・Web の選手編集・タグからの読込は、カードから見れば「別の所からの書き込み」になった。カードは、他の書き込みと同じく held サイクルで描画する
     - 入力中の欄は描画で飛ばし、欄を離れると読込の値が出る。打っていた文字は書かない（その欄の値が別の所で書かれたので、その書き込みが勝つ）
     - 旧実装は、打っている欄の文字をカーソルの下で読込の値に置き換えていた
     - メモ欄（`custom_textbox`）も同じ規則にした（2 回目のレビュー指摘）。メモ欄はキーごとに書くので、描画が残した文字に戻る変更（選択した 1 文字を同じ文字で打ち直す等）で、読込の上に古いメモを書き戻していた。今はそれを書かず、欄を離れると読込の値が出る。打ち足した文字は書く
  5. **カード自身の操作（補完の選択・クリアボタン・エディタの読込）の後は、入力中の欄も含めて描画する**。補完は、操作者が入力中のタグ欄から選ぶので、選んだ選手がその欄にすぐ出なければならない。描画の後に欄を離れても何も書かない（欄の文字が state と同じため）
  6. **`dataLock` を削除した**。`SetData` とカードのハンドラを直列にするためのロックだった。使い手はすべて GUI スレッドにいて（Web の選手編集も `@gui_thread_sync` / queued シグナルで GUI スレッドに渡る）、読込は `StateManager.lock` の中の 1 操作になったので不要
  7. **mains の正規化はデスクトップのアダプタに残す**（委任時の論点。S3c-2 判断 5 のまま）
     - `AutonaPlayerDB._BuildModel` は、読み込んだゲームの mains をその場で `[キャラ, スキン（数値）, バリアント or ""]` に揃える。次の保存で、その形が `local_players.json` に出る
     - core に移すと、ヘッドレスの `local_players.json` と `/playerdb` の出力が変わる（外部契約）
     - 読込（`_ApplyMains`）は両方の綴り（長さ 2 / 3・数字の文字列のスキン）を同じに読むので、state はシェルによらず同じになる
     - 数値でないスキンは、読込（`_SkinNumber`）もアダプタと同じく 0 として読み、warning を出す（レビューで追加。それまでは、ヘッドレスでは DB の記録でも `ValueError` になり、キャラが書かれなかった）。これで読込の結果にシェル間の差は無い。違いは `local_players.json` に保存される綴りだけ（デスクトップは揃えた形、ヘッドレスは保存されていた形のまま）
  8. **エディタ（`player_editor.1`）は、自分の操作の後だけ描画する**
     - `RenderFromState` は `score.*` 以外では何もしない（エディタは held サイクルを持たない）。そのため `SetData` / `Clear` の後に `_ShowOwnOperation` で描画する
     - `SavePlayerToDB` は state から組むので、ダイアログを閉じるたびに `player_editor.*` を消す既存の処理（`done()` → `StateSchema.ClearPlayerEditor()`）が前提になる。accept / reject / ×のどれも `done()` を通ることを確認した。消さないと、次の「新規登録」に前の選手の欄が混ざる
  9. **S2d で未確認だった「コンボに打ちかけた文字が、クリアの後も残る」を確かめ、直した**
     - 描画は、行が違うコンボしか動かさない。そのため、空の行を出しているコンボに打った名前は、クリアの後も空のスロットの上に残っていた（直す前にテストが失敗することを確認した）
     - ボードのクリア（`_ClearingPlayers`）とカードのクリアボタンの後に、`DiscardTypedText()` で捨てる（何も書かない）
     - クリアする読込（セットの entrants・タグからの読込）の後も捨てる（2 回目のレビュー指摘）。旧カードの `SetData` は `Clear()` で全コンボを行に戻していたが、core の読込は描画に任せるので、行が正しいコンボの打ちかけの文字が残り、カードで別のコンボを選ぶと、その文字がキャラとして state に書かれ OBS に出た。ただし入力中のコンボは飛ばす（判断 4 と同じく、別の所からの書き込みは打っている文字を取らない）。クリアしない読込（Web の選手編集）は対象外
  10. **読込の先頭のクリアは、読込が持ってこない id・city も空にする**（レビューでのオーケストレータ判断。既存の不具合の修正）
     - 旧カードのクリアは名前のハンドラを止めて走るので、id・city を消さなかった。そのため id を持たない選手（ローカル DB からのタグ読込・補完の選択・start.gg のアカウントが無い entrant）を読むと、前の選手の id・city が残り、id で引く統計が前の選手のまま出続けた
     - 空にするのは、読込が持ってこないときだけ。持ってくるなら読込がそのまま上書きする（None を挟んで 2 回書かない）。同じ選手の読み直しは id も city も書かないので、統計を取り直さない。結果の state は「空にしてから読む」と同じで、この形にしたのは、録画の他のステップの Set 列を変えないため
     - id が変わる（None になる場合も含む）ので、統計が取り直される（`SlotChange`）
     - クリアしない読込（Web の選手編集 `update_player`）は対象外。部分更新なので id・city は残る（旧実装と同じ。下の「既存の不具合」）
  11. **都市→地域表は `CountryCatalog.Load()` で読む**（レビュー指摘）
     - 最初の `FindState` が選手の読込から来ると、表（約 2.6 MB）の初回の読込が `StateManager.lock` の中で走り、その間、他スレッドの書き込みと delta の送出が待った
     - 起動時の国データの読込（`Load()`。デスクトップは `AutonaCountryHelper.init()`、ヘッドレスは `src/headless.py`）で 1 回だけ読む（実測で約 20 ms）。ロケールで変わらないので、再ロードでは読み直さない。`Load()` していないカタログ（テスト）は、従来どおり最初の照会で読む。import の副作用はゼロのまま
     - 地域の照合は、旗のパスを解かない `StateCodes()` で行う（2 回目のレビュー指摘。`States()` は地域の数だけ `os.path.exists` を呼んでいた）
     - アバター・スポンサーロゴのディスクの確認は、`StateManager.lock` の中に残る（下の「残置」。受け入れ済み）
  12. **互換層 `src/Helpers/AutonaBadWordFilter.py` を削除した**（レビュー指摘）。`src` からの呼び出しが無くなったため。テストは core の `BadWordFilter.instance` に向け直し、`tests/test_import_side_effects.py` がこのモジュールの復活を検知する
- **外から見える変化**
  - 入力中の欄に読込（セットの読込・Web の選手編集・タグからの読込）が来ても、打っている文字はそのまま残る。欄を離れると読込の値が出て、打っていた文字は書かれない（判断 4。旧実装は、打っている欄の文字が読込の値に置き換わった）
  - 国・キャラのコンボに打ちかけた文字（行を選んでいないもの）は、クリアと、セット・タグからの読込で消える（入力中のコンボは残る。判断 9）
  - メモ欄に入力中に読込が来ても、離れると読込のメモが出る（打ち足した文字は書く。判断 4）
  - 同じ seed・同じ id の選手を上書きしても（同じ選手の読み直し・セットの再読込）、アップセット係数を計算し直さない（判断 3。値は同じなので state は変わらない）。singles ⇔ チームが変わるセット読込では 1 回計算する
  - 悪語フィルタが ON のとき、読み込んだ選手の伏せ字が `local_players.json` に保存されることが無くなった（下の「修正済み」）
  - id を持たない選手を別の選手の上に読み込むと（クリアあり）、id と city が空になり、統計が取り直される（判断 10。旧実装は前の選手の id・city・統計が残った）
  - スポンサーだけ（タグ無し）の選手データの読込が、例外にならずにスポンサーを入れる（DB の照合はしない）
  - mains に数値でないスキンがあっても例外にならず、最初のスキンで入る（warning のログ）
  - 起動時に都市→地域表を読む（約 20 ms。判断 11）
  - 上の不具合の修正で値が変わる場合を除き、`program_state.json`・Socket.IO の delta・legacy / v1 の応答・`local_players.json` のバイトは変わらない
- **pin**
  - `tests/test_player_apply.py`（新規）
    - golden（`tests/fixtures/player_apply/golden.json`）を、ae922c24 でカード・ボードの入口から録画した。記録したのは、結果・保存サイクル・統計のトリガー・Set 列・サブツリー全体とキー順・カードの表示・`local_players.json`
    - シナリオは 11（`tests/fixtures/player_apply/scenarios.py`）
      - DB とのマージ
      - 選手の上書き（id・seed・city の持ち越しを含む）
      - no_mains（キャラが 1 行のときと複数行のとき）
      - 国と地域（未知の国・未知の地域・自由記述の地域名・カスタム国旗）
      - キャラ（旧形式の mains・範囲外のスキン・未知のキャラ / バリアント・他のゲームの mains）
      - DB への保存
      - PLAYERS エディタ
      - 悪語フィルタ ON
      - タグからの読込
      - Web の選手編集（`savePlayerToDb` を含む）
      - セットの entrants
    - 同じ入口で再生して照合する。意図した差分は `scenarios.CHANGES` に置いた（2 ステップ）
      - 判断 3 の 1 ステップ（「a set's entrants」の 2。もう 1 ステップの「a player over another」の 1 は、下の録り直しに含めた）
      - エディタのクリアの Set の順序（1 ステップ）。ボードのクリアと同じ順になった。1 サイクルの中の既存キーの上書きなので観測できず、各キーの最終値で比べる
    - レビューの修正（判断 10、スポンサーだけの読込）で変わったステップは、S3e-3 の木で録り直した（golden の該当ステップに `rerecorded` の印。他のステップは ae922c24 の録画のまま）。変わったキーは次のとおり（どれもボード 1）
      - 「a player over another」（スロット 1-1）
        - 1（id 303・city なしの NEXT を、id 101・city Kyoto の TAG の上に）: `city` が "Kyoto" → null（Set 列に `city` = null が増える）。統計の `UpsetFactorCalculation` が 2 → 0（判断 3。それまで `CHANGES` で当てていたもの）
        - 2（id なしの THIRD）: `id` が 303 → null（Set 列に `id` = null が増える）。統計の `RecentSetsSignal` / `LastSetsP1Signal` / `PlayerHistoryStandingsP1Signal` が 0 → 1。`city` は前のステップから null
        - 3（クリアしない部分更新）: Set 列は同じ。state の `id` / `city` が null のまま（前のステップの続き）
        - 4（クリアしない・id 303・city Kyoto）: 最終の state は録画と同じ。Set 列に `id` = 303 と `city` = "Kyoto" が増え、統計の 3 種が 0 → 1
      - 「load with the database」の 2（スポンサー "ONLY" だけを、DB 照合付きで、id 202・国 US の LEG がいるスロット 2-1 に）
        - 結果: `TypeError` → 例外なし
        - `team`（スポンサー）に "ONLY" が書かれる。このスロットに無かったキーなので、dict の末尾に足される（keyOrder が変わる）
        - `mergedName` が "" → "ONLY | "、`id` が 202 → null、`country` が US → `{}`（旧実装は例外で「国なし」の公開が飛ばされ、表示は空なのに state に US が残っていた）
        - 統計の `RecentSetsSignal` / `LastSetsP2Signal` / `PlayerHistoryStandingsP2Signal` が 0 → 1
    - AST
      - カードの `SetData` / `Clear` / `SavePlayerToDB` / `_OnCompleterPick` / `_ShowOwnOperation` に、モデルの走査・値の設定（`setText` など）・`emit` が無い
      - カードが `AutonaCountryHelper` / `AutonaBadWordFilter` を import しない
      - ボードの 3 入口がカードの `SetData` を呼ばない
      - `dataLock` が残っていない
    - エディタの読込・保存・クリアが `score` を 1 つも書かず、ボードのカードの表示も変えない（pitfalls #7）
  - `tests/test_player_service.py`
    - 同じ golden のうち、ボードの操作を使わない 9 シナリオを `PlayerService` だけで（Qt なしで）再生する
    - `local_players.json` は、デスクトップの mains の正規化を両側に当てて比べる（判断 7）
    - `ApplyData` が、渡された dict と DB の記録を書き換えない
    - レビューの修正（どれも直す前に失敗することを確認した）
      - id・city を持たない選手の読込で、両方が空になり、`idChanged` になる
      - スポンサーだけの読込が例外にならず、DB の記録を引かない
      - dict 形式の mains の数値でないスキン（`"x"` / None / `""`）と、DB の記録の数値でないスキンが、スキン 0 で入り、warning が出る
    - id・city を持ってくる読込は、それぞれ 1 回だけ書き（None を挟まない）、同じ選手の読み直しは書かない。クリアしない読込は id・city を残す
  - `tests/test_find_state.py`: `Load()` が都市→地域表を読み、その後の照会はファイルに触れない（直す前に失敗することを確認した）
  - `tests/test_bad_word_filter.py`: 互換層の転送のテストを、core の `BadWordFilter.instance` の 3 つの入口のテストにした。`tests/test_import_side_effects.py`: 互換層の import を外し、互換層が戻ってきたら失敗する
  - `tests/test_player_card_render.py`
    - 入力中のタグ欄に Web の選手編集が来ても、打っている文字が残る。離れると読込の値が出て、何も書かず、id も残る
    - 補完の選択が、入力中の欄にすぐ出る
    - ボードのクリアとカードのクリアボタンが、コンボの打ちかけの文字を捨てる（直す前に失敗することを確認した）
    - 2 回目のレビューの修正（どれも直す前に失敗することを確認した）
      - 国とキャラ 2 行目に打って選ばずに離れ、セット読込・タグからの読込をすると、文字が消え、別のスキンを選んでも打った名前が state に入らない
      - 入力中の国のコンボは、読込の後も打っている文字が残る（詳細ダイアログを実際に表示してフォーカスする）
      - メモ欄に入力中に Web の選手編集が来る → 選択した 1 文字の打ち直しは何も書かない → 離れると読込のメモが出る → 打ち足すと書く
      - singles ⇔ チームのセット読込で、アップセット係数の計算が 1 回だけ出て値が合う。同じセットの読み直しでは出ない
  - `tests/test_country_catalog.py`: `StateCodes()` が `States()` の code / original_code と同じで、ディスクに触れず、コピーを返す
    - ボード操作の録画（`tests/fixtures/board_operations/golden.json`）のうち、判断 10 で変わったステップを `S3E3_CHANGES` に置いた（`_withChanges` に、state の値を差し替える `values` を足した）。どれもボード 1 で、id は関わらない（この録画の選手は id を持たない）
      - 「load a set」「load a losers set that grows the roster」（city の無い E1・L1 を、ALPHA（city Shibuya）がいたスロットに読む）: `team.1.player.1.city` が "Shibuya" → null。`stateDigest` と反映の delta も変わる
      - その後の「reset all inputs」（クリアは city を消さない）と、スワップ後の「swap teams with a losers side」「load the losers flags while swapped」（`team.2.player.1.city`）も null のまま
      - 「two players again」: state は録画と同じ。ALPHA を入れ直して city が戻る分だけ、反映の delta が変わる
      - cycles とキー順は録画のまま（`city` のキーは前からあった）
  - `tests/test_player_slot_publish.py`: 「set data」系のステップは、同じキーへの連続を 1 つにして比べる（判断 2）
- **残置 / リスク**
  - アバター・スポンサーロゴのディスクの確認が、`StateManager.lock` の中で走るようになった（旧実装は GUI スレッドで、state のロックの外だった）。都市→地域表の初回の読込は `Load()` へ移し、地域の照合は旗を確かめない `StateCodes()` にした（判断 11）。残るのは選手 1 人あたりアバター・ロゴの数回で、受け入れた
  - seed のスピンボックスの上限（100000）での丸めが無くなった。state にはそのまま入り、カードの表示は 100000 で止まる。実データでは起きない値
  - `update_player` / `set_team_data` / `load_player_from_tag` は、従来どおり `WebServerActions` 経由で GUI スレッドに渡る（S3e-4 の範囲）。`DesktopBoardHost` が GUI スレッドで実行する理由（手入力の確定がフィールド → 合成名の 2 書き込みに分かれる）も残っている
- **既存の不具合（報告のみ・未修正）**
  - **クリアしない読込で選手名を別の選手に書き換えても、id と city が残る（配信事故の可能性）**
    - 起きる場面: Web UI の選手欄の確定。Web UI は確定のたびに、id を含まない選手データを `PATCH .../players/{p}`（`update_player`）で送り、core はクリアせずに上書きする（部分更新）
    - デスクトップのカードは、名前の手入力で id を空にする（`NameChanged` → `ExportPlayerId(None)`）ので、2 つの入口で挙動が違う。旧実装も同じ
    - 直すなら、部分更新でスポンサー・タグが変わったときに id・city を空にする（Web の入口を移す S3e-4 で扱うのが自然）
  - 選手のクリア（カードのクリアボタン・ボードの選手のクリア・全リセット）は city を消さない。名前の無いスロットに前の選手の city が残る（旧実装も同じ。オーバーレイは city を読まないので、表示は変わらない。次の読込が city を持ってこなければ、今は空にする。配信事故にはならない）
  - `general.disable_overwrite` のときの `UpdateSetData` には 3 つの問題がある
    - DB との照合を、タグだけ（スポンサー抜き）で、各 entrant の 1 人目にしか行わない
    - 受け取ったペイロードを書き換える
    - 配信事故にはならない
- **既存の不具合（修正済み）**
  - 読込の先頭のクリアが id・city を消さず、id を持たない選手を読むと、前の選手の id・city と統計が残った（判断 10。配信事故につながるもの）
  - スポンサーだけでタグが無い選手データを DB 照合付きで読むと `TypeError` になり、クリアの後で読込が止まった（スロットは空になり、スポンサーは入らず、表示は空なのに state に前の選手の国が残った）。タグが無ければ照合しない
  - mains が dict 形式でスキンが数値でないと `ValueError` になり、キャラが書かれなかった（ヘッドレスでは DB の記録でも起きた）。最初のスキンとして読み、warning を出す
  - 悪語フィルタが、呼び出し元の dict とメモリ上の DB の記録を書き換えていた。悪語フィルタが ON のときに DB の選手を読み込むと、その記録の名前などが伏せ字になり、次の DB の保存（別の選手の保存・entrants の取り込み）で、伏せ字が `local_players.json` に書かれた
  - コンボに打ちかけた文字が、クリアの後も残っていた（判断 9。S2d の残置）
- **リリースノート案**
  - 選手名などを入力している途中にセットの読込や Web からの選手編集が来ても、入力中の文字が勝手に置き換わらなくなった（欄を離れると、読み込んだ内容が表示される）
  - クリアすると、国・キャラ欄に入力しかけていた文字も消えるようになった
  - 不適切語フィルタが有効なとき、伏せ字にした名前が選手 DB に保存されることがある問題を修正した
  - スコアボードに選手を読み込んだとき（選手 DB から読み込んだ選手・Start.gg のアカウントが無い選手など）、前の選手の戦績・直近の対戦が残って表示されることがある問題を修正した
- **実機確認（オーナー）**
  - デスクトップ・スコアボード
    - 選手名欄に打ち、補完候補から選ぶ → その場でスポンサー・タグ・国・キャラが埋まり、反映後に OBS にも出る
    - Start.gg のセットを読み込む → 両チームの選手・国・キャラが今までどおり入り、統計（直近の対戦・戦績・アップセット係数）が出る。同じセットを読み直しても表示は変わらない
    - 選手名欄に打っている途中に、Web UI からその選手を編集する（またはセットを読み込む）→ 欄は打っている文字のまま。欄を離れると読み込んだ名前が出て、打っていた文字は反映されない
    - 国かキャラのコンボに文字を打ち、候補を選ばずにカードのクリア・全クリアを押す → 文字が消える
    - カードのクリアボタン → 今までどおり空になる（誕生日は残る）
    - Start.gg のセットで選手を入れた後、同じ枠に選手 DB の選手を読み込む（補完で選ぶ、または `load_player_from_tag`）→ 統計（直近の対戦・戦績）が前の選手のまま残らない
  - PLAYERS（選手リスト）のエディタ
    - 「新規登録」で名前・国・キャラを入れて Save new player → 一覧に出る。閉じてからもう一度「新規登録」を開く → 空で、前の選手の欄が残っていない
    - 既存の選手を編集で開く → 欄が埋まっている。キャラを変えて Update player → `local_players.json` に反映され、スコアボードの選手は変わらない
    - エディタのクリア → スコアボードは変わらない
  - Web / Stream Deck
    - Web UI の選手編集（名前・国・キャラ、`savePlayerToDb` 付き）→ スコアボードと DB に入る
    - legacy / Socket.IO の `load_player_from_tag` → DB の選手が入る
  - 悪語フィルタ ON: 不適切語を含む選手を読み込む → 表示は伏せ字になる。別の選手を保存した後も、`local_players.json` のその選手の元の名前が変わっていない
