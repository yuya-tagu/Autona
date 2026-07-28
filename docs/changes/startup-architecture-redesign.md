# 起動アーキテクチャ再設計（単一インスタンス化 + 明示的ライフサイクル移行）

作成: 2026-07-28 ／ 根拠調査: 同日実施（コード静的解析 + `logs/autona.log` の過去起動タイムスタンプ実測）
意思決定: [ADR-003](../decisions/ADR-003-startup-architecture.md)

## 0. 目的と問題定義

「起動が遅い」「exe 連続起動で多重起動する」は同一の根本原因を持つ 2 症状である。

| 根本原因 | 症状への寄与 |
|---------|------------|
| 単一インスタンスガードが存在しない（リポジトリ全域で確認済み・ゼロ件） | 連打すると素直に 2 プロセス起動する |
| `import src` 完了までスプラッシュすら表示されない（QApplication 生成もスプラッシュも import 連鎖の**後**） | ダブルクリック後の「無反応区間」→ 連打の動機 |
| 起動処理が `Window.__init__`（[src/Autona.py](../../src/Autona.py) 206〜794 行）に全同期で集中。module import の副作用でシングルトンが生成され、**import 順序 = 初期化順序**になっている | ウィンドウ表示まで実測 22〜23 秒、操作可能まで 30 秒超（2026-07-27 のログ実測・dev モード） |

対症療法（並べ替え・個別遅延）ではなく、起動の仕組みを以下の終着点へ移行する。

**測定根拠の要点**（詳細な行番号付き調査結果は本文各所に記載）:

- QtWebEngine ビューを起動時に 6 個 eager 生成（うち 2 個は `__init__` 内で即レンダまで実行）
- 選手 DB 2937 件を起動時にフル処理（1 件ごとに INFO ログ → 直後に全件 `SaveDB()` 書き戻し → GUI スレッドで 2937 QStandardItem 生成）
- `LoadCountries()` が完全重複で 2 回実行（[src/Autona.py](../../src/Autona.py):265 と :738）
- `AutonaWebServer` はクラス定義時点で Flask + SocketIO 一式を構築（import 連鎖だけで実測 5 秒）
- 多重起動時: ポート衝突ダイアログ（`check_webserver_port`）の**デフォルトボタンが Yes** のため、状態を保持している正常な 1 プロセス目を誤 kill しやすい。`program_state.json` / `settings.json` / `local_players.json` はプロセス間排他なしの後勝ち上書き。`AutonaPlayerDB` の `multiprocessing.Lock` は独立起動プロセス間では共有されず実質無効

## 1. 終着点アーキテクチャ

### 1.1 設計原則

1. **Autona は意味的に単一インスタンスである。** ポート・`out/` 状態ファイル・グローバルホットキー・OBS オーバーレイという排他資源を占有する以上、「2 回目の起動 = 既存ウィンドウの前面化」が仕様（OBS や DAW と同じ挙動）。
2. **import は副作用ゼロ。** どの src モジュールを import してもクラス定義以外何も起きない。シングルトン生成・Qt オブジェクト生成・ファイル I/O・サーバー構築は一切 import 時に行わない。
3. **初期化順序はブートストラップコードが明示する。** import 文の並びではなく、composition root（`src/bootstrap.py`）が生成順を 1 箇所に記述する。順序制約（例: GameAssetManager → TournamentDataProvider）はコードとして読める・壊れたら壊れた場所でエラーになる。
4. **「起動 = 全機能の準備完了」を捨てる。** ゴールデンパス（起動 → スコアボード操作）に必要な最小コアだけを同期構築し、残りは初回表示時 or 表示後の非同期に回す。各パネルは「起動時に作られている」前提を持たない。

### 1.2 起動パイプライン（4 段）

```
[Stage 0] プロセスゲート        … named mutex による単一インスタンスガード
    ↓                            （2 個目は既存インスタンスへ前面化要求して即終了）
[Stage 1] 早期フィードバック    … QApplication 生成 + スプラッシュ表示
    ↓                            （`import src` より前。ダブルクリック後 1〜2 秒以内に必ず画面に出る）
[Stage 2] ブートストラップ      … src/bootstrap.py がシングルトンを明示順で生成
    ↓                            （import 副作用ゼロが前提。進捗をスプラッシュへ通知）
[Stage 3] 最小コア表示          … ウィンドウシェル + スコアボードのみ構築して show()
    ↓
[Stage 4] 遅延・非同期初期化    … 残りパネル = 初回表示時に構築（lazy）
                                  WebServer / 選手 DB / アセット / 更新チェック = 表示後に非同期
```

#### Stage 0: プロセスゲート（`main.py`、`import src` より前）

- **検出**: Win32 named mutex（ctypes `CreateMutexW`、名前例 `taguyan.Autona.SingleInstance`）。
  - lockfile 方式より優れる点: プロセス死亡（クラッシュ含む）で OS が自動解放するため stale lock が原理的に発生しない。import コストもゼロ（ctypes は既に main.py で使用中）。
  - 既存の `_update_in_flight()` チェック（[main.py](../../main.py):42-77）と同じ「重い import の前の軽量ゲート」パターンに従い、同じ場所に置く。
- **前面化ハンドオフ**: 1 プロセス目が Stage 2 で `QLocalServer`（固定名）を立て、2 プロセス目は mutex 取得失敗時に `QLocalSocket` で "activate" を送って exit 0。1 プロセス目は受信で `showNormal()` + `raise_()` + `activateWindow()`。
  - レース窓（1 個目が mutex 取得済みだが QLocalServer 未起動）: 2 個目は短時間リトライ（例: 500ms × 3）後、接続不能でもそのまま exit 0（既に起動中なのは mutex で確定しているため何も出さない。`_update_in_flight()` と同じ「黙って退く」方針）。
- **アップデータとの整合**: 更新ヘルパー（apply_update.ps1）は旧プロセス終了後に再起動するため、mutex は解放済みで再起動を阻害しない。`_update_in_flight()` チェックは現状のまま mutex ガードの**前**に置く。

#### Stage 1: 早期フィードバック（`main.py`、`import src` より前）

- `QApplication` 生成とスプラッシュ表示を main.py に移す。import するのは `PySide6.QtWidgets` / `QtGui` / `QtCore` のみ（フル連鎖と比べ大幅に軽い）。
- 現行の `App = QApplication(sys.argv)`（[src/Autona.py](../../src/Autona.py):41 のモジュールレベル生成）は廃止し、src 側は `QApplication.instance()` を参照する。
- **既知の制約（Step 1 で検証必須）**: 後段で QWebEngineView を生成するため、QApplication 生成前に `Qt.AA_ShareOpenGLContexts` の設定（または QtWebEngine の事前初期化）が必要。フリーズ環境（PyInstaller）での挙動確認を検証項目に含める。
- スプラッシュは既存 `AutonaSplashScreen`（[src/AutonaSplash.py](../../src/AutonaSplash.py)）を src から独立させて main.py から使えるようにする（依存が `import src` を引きずらないこと）。

#### Stage 2: 明示的ブートストラップ（`src/bootstrap.py` — 新設）

- 全シングルトンの生成を 1 ファイルに集約する。現行のモジュールレベル生成（例: `AutonaGameAssetManager.py:1453` の `instance = AutonaGameAssetManager()`、`StateManager.py:581-587` の static 初期化、`AutonaWebServer.py:56-64` のクラス本体での Flask/SocketIO 構築）はすべて廃止し、明示的な生成呼び出しに置き換える。
- 順序制約はコメント付きでコードに明記する（例: 「GameAssetManager は TournamentDataProvider より先。後者の `__init__` が前者の signals に connect するため」— 現 [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) #2 の内容がコードに移る）。
- 各生成ステップからスプラッシュへ進捗を通知する。
- 移行完了後、`src/__init__.py` の import 副作用連鎖（`from .Autona import Window, App`）は廃止し、明示的なエントリ（例: `src.bootstrap.create_app()`）に置き換える。

#### Stage 3: 最小コア表示

- 構築するもの: ウィンドウシェル（メニューバー・サイドバー枠・ステータスバー）+ **アクティブな `battle_format` のスコアボード 1 面のみ** + StateManager/SettingsManager。
  - board1（1v1）/ board2（team）/ board3（crew）の全構築（現行・実測約 2 秒）は行わず、非アクティブな board は初回の形式切替時に構築する（2026-07-28 PdM 決定）。
- ここで `show()`。目標: ダブルクリックからウィンドウ表示まで **5 秒以内**（warm cache・frozen。2026-07-28 PdM 合意）。

#### Stage 4: 遅延・非同期初期化

- **lazy 構築（初回表示時）**: WebEngine を持つプレビュー系パネル（Board プレビュー / Top8 / Commentary / StatsPreview / Thumbnail / Result）、設定系 5 ウィンドウ（Settings / ApiInfo / Companion / OBS / GameAssetGuide）、**非アクティブな battle_format の board**（初回の形式切替時に構築）。サイドバーのページ切替・メニューを「初回アクセスでファクトリを呼ぶ」構造にする。
- **表示後の非同期**: WebServer 起動（表示後数秒の遅延は配信運用上許容・2026-07-28 PdM 承認）、選手 DB 読み込み（ワーカーで読み込み → モデル構築は表示後の GUI バッチ）、ゲームアセット読み込み、Start.gg 再取得、更新チェック、ホットキー登録。
- **契約**: どのパネルも「任意のタイミングで初めて構築されても動く」こと。起動時構築を前提にした暗黙依存（他パネルのシグナルへの接続等）は bootstrap かパネル自身の初期化に明示する。

### 1.3 終着点で消滅する問題

- 多重起動（Stage 0 で構造的に不可能になる）
- ポート衝突ダイアログの誤 kill（主因の自己衝突が消える。他プロセスとの衝突検知は残すがデフォルトボタンは No に変更）
- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) #1（src import で全起動）・#2（import 順序がシングルトン順序を規定）— 構造ごと消滅し、テストも `direct_import()` 不要になる
- 「無反応区間」→ 連打の動機（Stage 1 で 1〜2 秒以内に必ず描画）

## 2. 移行ステップ

**一撃の作り直しはしない。** シングルトン初期化順序は本リポジトリ最大の地雷原（C++ クラッシュ実績複数: agent-pitfalls #2/#9/#13）のため、各ステップを独立リリース可能な単位に区切り、毎ステップで検証を挟む。ステップ順は「実害の大きい順 + リスクの小さい順」。

### Step 1: 単一インスタンスガード + 早期フィードバック（Stage 0 + 1 の実現）

**変更**: main.py に mutex ガード追加 / QApplication + スプラッシュを `import src` 前へ移動 / `Autona.py` の `App` 生成を `QApplication.instance()` 参照に変更 / 前面化用 QLocalServer 追加 / ポート衝突ダイアログのデフォルトを No に変更。

**触るファイル**: `main.py`、`src/Autona.py`（App 生成部・splash 呼び出し部）、`src/AutonaSplash.py`（src 非依存化）、`src/AutonaWebServer.py`（ダイアログのデフォルトボタンのみ）。

**リスク**: QApplication 生成位置の移動は全 Qt オブジェクトの前提に関わる。QtWebEngine の初期化順制約（AA_ShareOpenGLContexts）を dev / frozen 両方で要確認。

**検証**（ユーザー実施。Claude は構文チェック・レビューまで）:
- [ ] exe をダブルクリック連打（5 回）→ プロセスは 1 個、ウィンドウ 1 枚（タスクマネージャで確認）
- [ ] 起動済みの状態で exe を再度起動 → 既存ウィンドウが前面化し、2 個目は何も表示せず終了
- [ ] 最小化状態で 2 個目起動 → 復元されて前面化
- [ ] ダブルクリック後 1〜2 秒以内にスプラッシュが表示される（体感確認）
- [ ] 1 プロセス目を強制終了（taskkill）→ 直後に再起動できる（mutex の自動解放確認）
- [ ] 自動更新 E2E（旧版 → 新版）: ヘルパーによる再起動が阻害されない
- [ ] プレビュー系パネル（Board / サムネ / リザルト）が正常描画（WebEngine 初期化順の確認）
- [ ] `python main.py`（dev モード）でも上記が成立

### Step 2: 即効策（局所的な無駄の除去）

**変更**（各項目は独立・いずれも局所）:
1. `LoadCountries()` 重複削除（[src/Autona.py](../../src/Autona.py):265 / :738 の片方）
2. 選手 DB: 1 件ごとの INFO ログ削除 + 読み込み直後の `SaveDB()` 廃止（[src/AutonaPlayerDB.py](../../src/AutonaPlayerDB.py):102 / :112）
3. `netstat` subprocess → `socket.bind` 試行に置換（[src/AutonaWebServer.py](../../src/AutonaWebServer.py):837 付近）
4. 表示後の固定 1.1 秒オーバーレイ（[src/Autona.py](../../src/Autona.py):729）を実処理完了ベースに変更 or 短縮
5. `CleanupUpdateLeftovers()` の毎起動フル走査を「更新直後マーカーがある時のみ」に変更（[src/AutonaUpdater.py](../../src/AutonaUpdater.py):354-370）
6. `dependencies/autona.spec` の `excludes` に未使用パッケージ（pandas / numpy / cryptography 等）を追加（配布 40 MB 超削減・コールド起動 I/O 減）

**検証**:
- [ ] 起動ログのタイムスタンプで before/after 比較（`=== Autona IS STARTING ===` 〜ウィンドウ表示）
- [ ] 選手 DB: 起動後に `local_players.json` の mtime が変わらないこと・選手一覧/補完が正常
- [ ] Web UI（`/scoreboard` 等）が従来通り配信されること
- [ ] excludes 追加後: exe ビルド → 起動 → 主要パネル巡回で ImportError が出ないこと（[.claude/manual-test-checklist.md](../../.claude/manual-test-checklist.md) のゴールデンパス）
- [ ] ビルドサイズ diff を記録

### Step 3: import 副作用の全廃（Stage 2 = composition root 移行）★本丸

**変更**: `src/bootstrap.py` 新設。モジュールレベルのシングルトン生成を 1 つずつ bootstrap へ移す。**1 PR = 1〜2 シングルトン**の粒度で進め、生成順は現行の import 順を初期値としてそのまま固定する（挙動不変の移行）。

移行対象（現行の生成順）: StateManager（static 初期化 → 明示 `init()`）→ AutonaHotkeys → AutonaGameAssetManager → AutonaTournamentDataProvider → AutonaAlertNotification → AutonaUpdater → AutonaCountryHelper → AutonaScoreboardManager → AutonaWebServer（クラス本体の Flask/SocketIO 構築をインスタンス化時に移動）。

最後に `src/__init__.py` の連鎖 import を廃止し、明示エントリに置換。

**リスク**: 各シングルトンの `__init__` が他シングルトンの `instance` を参照している暗黙依存が移行中に顕在化する（これ自体が本ステップの成果物: 依存が bootstrap 上で可視化される）。agent-pitfalls #13（共有モデルの retire パターン）に注意。

**検証**（1 PR ごと）:
- [ ] 起動 → [.claude/manual-test-checklist.md](../../.claude/manual-test-checklist.md) のうち当該シングルトンに関わる項目
- [ ] 自動テスト（新設・CI 可）: 各 src モジュールを subprocess で単独 import し、「QApplication が生成されない」「`instance` が None のまま」を assert する**副作用ゼロ検査**。移行済みモジュールから順に対象へ追加
- [ ] dev / frozen 両モードで起動確認（import 解決は PyInstaller で挙動が変わりうる）
- [ ] 全移行完了後: `tests/conftest.py` の `direct_import()` が不要になったことを確認し、agent-pitfalls #1/#2 を更新

### Step 4: 遅延初期化（Stage 3 + 4 の実現）

**変更**: サイドバー/メニューを「初回アクセス時にページを構築する」ファクトリ構造へ。リスクの小さい順に lazy 化する:
1. 設定系 5 ウィンドウ（Settings / ApiInfo / Companion / OBS / GameAssetGuide）— 既に `UiMounted()` 分離があり移行しやすい
2. WebEngine プレビュー系パネル（Top8 / Commentary / StatsPreview / Thumbnail / Result / Board プレビュー）— レンダラ生成・初回レンダも初回表示時へ
3. 選手 DB: ワーカー読み込み + 表示後モデル構築（`SetupModel` の GUI 占有を表示後へ）
4. WebServer 起動を `show()` 直後の非同期へ（表示後数秒の遅延は許容・2026-07-28 PdM 承認）
5. ゲームアセット: `characters.json` 4.1 MB のパースをワーカーへ（[src/AutonaGameAssetManager.py](../../src/AutonaGameAssetManager.py):729-736）
6. スコアボード: アクティブな battle_format の board のみ起動時構築とし、非アクティブ board は初回の形式切替時に構築（2026-07-28 PdM 決定）

**リスク**: 未構築パネルへの参照（ホットキー・Web API・StateManager 経由の間接参照）が null 参照になる箇所の洗い出しが必要。lazy 化 1 対象ごとに参照元を grep で確認してから実施。

**検証**（対象ごと）:
- [ ] 起動直後に当該パネルを**開かずに**関連機能を操作（ホットキー・Web API・Companion）→ エラーなし
- [ ] 当該パネルを初回表示 → 従来と同じ内容・操作性（manual-test-checklist の該当項目）
- [ ] 2 回目以降の表示が再構築されない（状態が保持される）こと
- [ ] WebServer 遅延後: OBS ブラウザソースが表示後数秒以内に取得成功すること
- [ ] battle_format 切替: 1v1 → team → crew → 1v1 と一巡し、各 board が初回切替時に正しく構築され、スコア・選手情報が形式ごとに保持されること
- [ ] 未構築 board への外部参照（Web API / ホットキー / StateManager キー `score.2.*` `score.3.*`）が形式切替前でもエラーにならないこと
- [ ] 起動フェーズログで目標値達成を確認（§3）

### Step 5: 後始末・ドキュメント追従

- ポート衝突検知の残置分の整理（自己衝突が消えた前提でメッセージ・挙動を見直し）
- [.claude/design/Design.md](../../.claude/design/Design.md)・[docs/architecture/current-architecture.md](../architecture/current-architecture.md)・[.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)・CLAUDE.md の起動記述を実態へ更新
- 検証: ドキュメント間の矛盾チェック（優先順位: コードの実態が正）

## 3. 計測基盤（全ステップ共通の検証手段）

Step 1 の中で、起動フェーズごとの経過時間を 1 行ずつ INFO ログに出す軽量マーカーを入れる:

```
STARTUP phase=gate elapsed=0.02s
STARTUP phase=splash elapsed=0.8s
STARTUP phase=bootstrap elapsed=3.1s
STARTUP phase=core-shown elapsed=4.9s
STARTUP phase=hydrated elapsed=12.0s
```

- 各ステップの before/after はこのログの diff で判定する（体感ではなく数値で回帰検知）
- 成功基準（**確定・2026-07-28 PdM 合意**): warm cache・frozen で `core-shown ≤ 5s`、スプラッシュ表示 ≤ 2s、ダブルクリック連打でプロセス 1 個

### Step 1 完了時点の基準値（frozen exe・実機、2026-07-29 記録）

| phase | 2026-07-28 23:44 | 2026-07-29 01:04（ビルド直後 = cold 寄り） |
|-------|-----------------:|-------------------------------------------:|
| gate | 0.09s | 0.16s |
| splash | 2.31s | 3.12s |
| import-src | 6.58s | 16.53s |
| window-init | 6.59s | 16.56s |
| core-shown | **27.58s** | **34.49s** |

- 最大の塊は **window-init → core-shown の 18〜21 秒**（`Window.__init__` の一枚岩）→ Step 2/4 の主対象
- splash はほぼ目標圏（2.3〜3.1s。QtWidgets DLL ロードが支配的で、cold cache で伸びる）
- import-src は cache 状態で 4〜13 秒振れる → Step 2 の excludes 削減と Step 3 の import 副作用全廃の対象

## 4. 決定事項（2026-07-28 PdM 回答済み・旧 Open questions）

1. **2 個目起動時の挙動**: 前面化のみ（通知・トースト等は出さない）
2. **WebServer の起動タイミング**: 表示後の非同期起動で良い。OBS ブラウザソースの取得開始が数秒遅れることは配信運用上問題ない
3. **最小コアの範囲**: アクティブな `battle_format` の board のみ。非アクティブ board は初回の形式切替時に構築
4. **目標起動時間**: `core-shown ≤ 5s`（warm cache・frozen）で合意

## 5. ロールバック方針

- 各ステップは独立 PR・独立リリースのため、問題発生時は当該 PR の revert で戻せる
- Step 1（QApplication 移動）だけは revert 影響が広いため、リリース前に dev / frozen / 自動更新 E2E の 3 点を必ず通す
- Step 3 は 1 PR = 1〜2 シングルトンの粒度を厳守し、クラッシュ発生時に二分探索可能な履歴を保つ
