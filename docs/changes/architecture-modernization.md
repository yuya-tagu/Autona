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
| **S3 ヘッドレス起動と永続化分離**（R3・R4） | `main.py --headless`（QApplication なしで core + WebServer）。正本を `user_data/state.json` へ（初回は `out/program_state.json` から移行）。`state_version` + 移行関数 1 本目。`WebServerActions` 廃止 | ヘッドレスで OBS オーバーレイ + `/web` が動く（Playwright E2E）。`@gui_thread_sync` の残りは thumbnail/result とウィンドウ操作のみ |
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
