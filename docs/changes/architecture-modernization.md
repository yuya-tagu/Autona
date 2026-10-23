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

#### S2e best_of を state 主導へ（2026-10-24・PR 未作成）

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
