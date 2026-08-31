# Headless Core 再設計（書き込み経路の Actions 集約）設計書

対応 ADR: [ADR-004](../decisions/ADR-004-headless-core-write-path.md)（本書が設計・移行の正本）。
Design.md との関係: §3.2.2 R-Sync-1 ⑤ と §8 T-18/T-19（M4）の実行計画。M4 の
「PR 前に追加 Design ドキュメントを書く」を満たすのが本書。

## 1. 目的 — PdM 方針の翻訳

PdM 方針（2026-08-28）「Qt ウィジェットが書き込み経路の主役という設計を根本から見直し、
軽く、他アプリに影響を及ぼさない堅牢なアプリケーションを目指す」を、検証可能な目標に落とす:

| 方針 | 目標（検証可能な形） |
|------|--------------------|
| **堅牢** | ① 書き込み経路を Actions 1 本に集約（ウィジェットの `StateManager.Set` 直接呼び出し 197 → 0）② core の Qt import ゼロをテストで恒久固定 ③ core が QApplication なしで単体テスト可能（過去バグ最多の「UI⇄State 同期」層にテストが書ける） |
| **軽く** | ① 未使用依存の削除（pynput 等）② WebEngine・重量パネルの遅延構築の維持・徹底 ③ core の起動が UI 構築に依存しなくなることで、起動残候補「TournamentInfo のヘッドレスサービス抽出」（[startup-architecture-redesign.md](startup-architecture-redesign.md) §3）が実行可能になる |
| **他アプリに影響を及ぼさない** | 下表「OS グローバル影響の現状評価」の維持・改善。新たな常駐・フック・ポートを増やさない |

### OS グローバル影響の現状評価（2026-08-28 調査）

| 項目 | 現状 | 評価・対応 |
|------|------|-----------|
| グローバルホットキー | Win32 `RegisterHotKey`。**pynput のような keyboard hook ではなく**、登録した 9 組み合わせ（全て `Ctrl+Shift+` 系）のみを予約。設定でオフ可・オフ時は OS 登録も解除・キー録音中は一時解放 | 設計として適切。維持。（swallow する組み合わせが 9 個ある事実は Settings に明示済み） |
| `pynput==1.7.7` | requirements / Pipfile に宣言されているが **src からの import ゼロ**（残留依存） | **削除**（Step 0）。「キーボード全フックを持つアプリ」という嫌疑ごと消す |
| 単一インスタンス mutex / QLocalServer IPC | ADR-003 D1 の仕様 | 維持 |
| TCP 5000 | 既定 `127.0.0.1`（loopback）。LAN 公開は opt-in・認証なし・CORS 全許可 | 既定は安全。**LAN 公開 + 認証なし**は本再設計の非目標だが、リモート運用を採る将来 ADR の前提条件として記録 |
| ファイル出力 | `./out/` 配下のみ | 維持 |
| 起動時外向き HTTP | frozen 時の更新チェックのみ | 維持 |

## 2. 現状の書き込み経路（事実）

```
[1] Qt UI 操作 ──────────────► StateManager.Set 直接（197 箇所 / 21 ファイル）
      支配的パターン: findChildren ループ + lambda + objectName() をキーパスに使用
[2] REST v1 (26 ルート) ──┐
[3] REST legacy + Socket.IO ┴─► WebServerActions（47 メソッド）
      （約 80 ルート）             └► 実態はリモコン: シグナル emit / ウィジェットメソッド /
                                      findChild+setValue の 3 系統。StateManager への書き込みゼロ
[4] グローバルホットキー (9 本) ──► ウィジェットのバウンドメソッド直結（Actions 迂回）
```

`StateManager.Set` 197 箇所の分類:

- **(a) UI 操作への直接反応** — 最多・最難関。objectName 動的キーパスと不可分（S-14）
- **(b) データロード結果の流し込み**（Start.gg 反映・セット読込・統計）— Qt 依存の必然性なし。
  移行が最も容易。`AutonaStatsUtil`（Qt 依存はシグナル定義のみ）が代表
- **(c) 初期シード** — `_SeedBoardState` / `_SeedPanelState` として既に半分ウィジェット外へ
  抽出済み。ただし `AutonaScoreboardWidget.__init__` と `_SeedBoardState` に重複あり

追い風となる既存資産:

- `StateManager`（785 行）: Qt 依存はシグナル 4 本（`state_big_change` / `state_updated` /
  `held_dirty_changed` / `held_state_updated`）のみ。held/commit（「反映」）二相機構は Qt 非依存
- Qt-free 資産 約 6,600 行: `StartGGDataProvider`(2291) / `AutonaApiV1`(733) /
  `AutonaDesignerService`(864) / `AutonaTop8Model`(341) / `AutonaBracket`(323) / Helpers 群
- v1 API は検証・冪等性・`_HeavyOpGuard`・op-log を備え、既に「ルーティング層 / Actions 層」の
  分離を達成。経路 [2][3] は Actions を唯一の下流として共有 = **差し替えの集約点が 1 つ**
- `WebServerActions` の `QThread` 継承・`QThreadPool` は完全未使用（`run()` なし・`start()` 呼び出しなし）

## 3. 終着点アーキテクチャ

```
┌─ clients ─────────────────────────────────────────────────────┐
│  Qt ウィジェット（表示 + Actions 呼び出しに限定）              │
│  React Web UI / 外部システム（REST v1 / legacy / Socket.IO）   │
│  グローバルホットキー                                          │
│  OBS オーバーレイ（read-only、現行どおり）                     │
└──────────────┬────────────────────────────────────────────────┘
               ▼ すべての書き込みはここを通る
┌─ core（Qt import ゼロ・テストで pin）────────────────────────┐
│  AutonaActions   ドメイン操作（スコア・セット・表示・統計…） │
│  domain services セットデータ正規化 / 統計集計 / ステージ     │
│                  トラッキング / クルー合計 / シード           │
│  schema          キーパスカタログ / held・exempt 定義 /       │
│                  マイグレーション層                           │
│  StateManager    状態保持 + delta + 保存 + held/commit        │
│                  （シグナル → 汎用 observer に置換）           │
└──────────────┬────────────────────────────────────────────────┘
               ▼ 通知（observer）
┌─ adapters ────────────────────────────────────────────────────┐
│  StateManagerBridge（observer → Qt シグナル変換。GUI スレッド境界もここ）│
│  WebServer（Flask/Socket.IO。既存の delta 配信・契約は不変）   │
└───────────────────────────────────────────────────────────────┘
```

設計上の要点:

- **エコーループ抑止を規約化する**: Actions が書いた state 変更を StateManagerBridge 経由で受けた
  ウィジェットが、表示更新の副作用（`setText` → `editingFinished` 等）で再度 Actions を
  呼ばないこと。プログラム的更新中は `QSignalBlocker` で囲む（agent-pitfalls #13b の
  既存パターンを標準とする）。各ステップの検証項目に「同一操作で delta が 1 回だけ出る」を含める
- **GUI スレッド境界はアダプタに集約**: 現在 `@gui_thread_sync` 19 箇所が担う
  「Flask スレッド → GUI スレッド同期ブロック」は、書き込みが core 化されるほど不要になる。
  残るのは Qt モデル読取り（`get_characters` 等）とウィンドウ操作（`open_sets` 等）のみ
- **外部契約は不変**: legacy REST / Socket.IO の台帳（44 ルート + 39 イベント、
  `test_webserver_routes.py` で pin）と v1 API の封筒仕様は変えない

## 4. 移行ステップ（リスク昇順・各ステップ独立リリース可能）

ADR-003 と同じ規律: 1 PR = 独立検証・revert 可能。粒度は「1 PR = 1 ステップ以下」とし、
Step 6 以降は 1 PR = 1 ウィジェット単位に分割する。

### Step 0: 掃除・準備（リスク極小）✅ 完了（2026-08-28, PR #188）

- `pynput` を requirements / Pipfile / licenses から削除（import ゼロの残留依存）
- デッドコード削除: `AutonaTeamBattleWidget`(423 行) + `AutonaTeamPlayerWidget`(658 行) +
  `AutonaTeamBattleModeEnum`(26 行)。唯一の生成元は import ごとコメントアウト済み。
  BUG-7 の `xfail` と関連テスト・agent-pitfalls #5 の記述も整理
- `WebServerActions` の `QThread` → `QObject` 化、未使用 `QThreadPool` 削除
- ドキュメントドリフト修正: Design.md の `set_light_mode` / `/api/v1/ui/theme` 残骸、
  commentary 12/8 混在、`.claude/README.md` の external-control-api「未実装」表記
- 検証: フルスイート + `test_webserver_routes.py`（台帳不変）

### Step 1: core 境界の確立（要石）✅ 完了（2026-08-28）

- `src/core/` パッケージ新設。`StateManager` のシグナル 4 本を Qt 非依存 observer
  （`src/core/Observable.py` の `ObserverChannel`。素の callback 登録）に置換し、
  既存シグナルと同名の Qt シグナルを `StateManagerBridge` が提供。
  **付け替えの実数: ウィジェット側 `connect` 13 箇所（6 ファイル）→ `StateManagerBridge.instance`、
  core 側 `emit` 8 箇所 → `observers.<名前>.notify()`**。挙動不変
- `tests/test_core_qt_free.py` 新設: core 配下の import が Qt を引かないことを
  subprocess で pin（`test_import_side_effects.py` 方式）。対象モジュールは
  `src/core` の `rglob` で動的列挙するため、新設 core モジュールの**間接的な** Qt 汚染も
  自動で pin される
- **移動したのは `StateManager` / `SettingsManager` の 2 つ**（import パスは互換
  re-export シムを 1 世代残す）。`AutonaDictHelper` 等の Qt-free Helpers は当面
  `src/Helpers` に残し、core から参照する形にした（移動は後続ステップで判断する。
  Helpers は Qt 側の利用者も多く、Step 1 の目的である「core 境界の確立」には不要）
- 検証: フルスイート + 手動チェックリスト「起動/終了」節。delta 配信の回帰がないこと
  （React 側 revision 連番・OBS poll）

### Step 2: シード・スキーマの core 移動 ✅ 完了（2026-08-28）

- `src/core/StateSchema.py` を新設。シード一本化: `AutonaScoreboardWidget.__init__` /
  `AutonaCrewBattleWidget.__init__` / `AutonaScoreboardManager._SeedBoardState` の 3 実装を
  `StateSchema.SeedBoard(n)` に集約（`_SeedBoardState` は削除）。`Window._SeedPanelState` の
  本体も `StateSchema.SeedPanelState()` へ移した
- ウィジェット属性として存在したスキーマ定義を schema へ移動: `STATS_KEYS`（held exempt）、
  `display_options` のキー集合（`AutonaScoreboardWidget.elements` と
  `WebServerActions._DISPLAY_OPTION_KEYS` の二重定義を `DISPLAY_OPTIONS` へ統合。
  ラベル文字列だけ Qt 側に残る）。付随して `stream.org*Version` の mtime 発行
  （`_publishAssetVersion` → `PublishAssetVersion`）とその素材パス、`player_editor` ルート、
  既定テーマ/アクセントカラーも schema 側へ
- キーパスカタログの器 `KEY_CATALOG` / `DYNAMIC_KEY_FAMILIES` を新設（列挙のみ。
  何も消費していない。静的化とマイグレーション層は Step 5）
- **唯一の挙動追加: `display_options.*` を `SeedPanelState()` でもシードする**。従来の
  書き手は 1v1 / チームボードの `__init__` だけで、保存フォーマットが crew ボード
  （`score.3`。eye メニューを持たない）の環境では state にキーが 1 つも出なかった。
  **欠損キーのみ**を `SettingsManager`（トグルの永続正本）から埋めるので、復元済みの
  `program_state.json` の値は上書きしない
- **`colors.{feature}.*` のシード（`SeedColorState`）は AutonaFeatureColors に残した**:
  同じキー族の読み書き（`GetFeatureColors` / `SetFeatureColor` / `SetTournamentColor`）が
  同居しており、seed だけ core に出すと所有者が 2 つに割れる。カラーモジュール全体の
  core 移行は書き込み経路と一緒に Step 3/4 で行う。`Window._SeedPanelState` は
  `SaveBlock` 内で `StateSchema.SeedPanelState()` と `SeedColorState()` を呼ぶだけ
- 検証: フルスイート 417 passed / 19 skipped（Step 2 前 393 + 新規 24）。
  `test_objectname_keypath_contract.py` は不変で pass、held/commit 系も不変。
  `tests/test_state_schema.py` 新設（移動前の値をリテラルで pin・シードの挙動・
  `Window._SeedPanelState` からの結線と eye メニューのラベル一致を AST で pin）

### Step 3: AutonaActions 新設と「簡単な書き込み」の反転 ✅ 完了（2026-08-31）

- `src/core/Actions.py` に `AutonaActions` を新設（`bootstrap_core()` が生成）。
  **移行した操作**: スコア増減 / スコアリセット / チーム色 / `display_options` /
  反映（`CommitHeld`）。`WebServerActions` の該当メソッドは委譲になり、
  `set_display_option` / `apply_changes` からは `@gui_thread_sync` が外れた
  （`reset_scores` だけ残置 — クルーボード分岐がウィジェットを触るため）
- **スコープから外した 2 操作**（自明ではないと判明）:
  - **チームスワップ** — 選手データの入れ替えが `AutonaScoreboardPlayerWidget.SwapWith`
    （ウィジェット間コピー）で、`teamsSwapped` も `WebServerActions.get_swap` が
    ウィジェットから読む。選手サブツリーを core で表現できる Step 4/5 が前提
  - **best_of** — `best_of_text` / `first_to_text` が `AutonaLocaleHelper.matchNames`
    （Qt を import するモジュール）由来。同じハンドラが個別ゲームトラッカーの
    ステージ行も作り直す。ロケールテーブルの core 移動が前提
  - あわせて **クルーボード（board 3）のスコアリセット**も対象外。チーム合計が
    メンバーストックの派生値で、リセット = 全員ストック満タン復元というウィジェット側の意味
- **ホットキー**: `AutonaHotkeys.ConnectActions()`（`bootstrap_core()` から 1 回）が
  スコア ±4 本 / `reset_scores` / `apply_changes` を `AutonaActions` に直結。
  ボードは `AutonaActions.ActiveBoard()` = `SettingsManager.scoreboard.battle_format`
  で解決する。**挙動変化（意図した修正）**: 従来は「構築済みの全ボードが応答」だったため、
  singles↔team 切替後は非表示ボードのスコアまで動き、crew 起動時は 1 本も効かなかった。
  以降は常にアクティブな 1 面だけ。`load_set` / `swap_teams` はボード、
  `refresh_phase_group` は Window に残置
- **battle format の解決を core に一本化**: `AutonaActions.ResolveBattleFormat()` が
  `scoreboard.battle_format` → 旧 `scoreboard.team_mode`（bool）フォールバックまで含めて
  正本になり、`Window._resolveBattleFormat()` と `/api/v1/health` の `activeBoard`
  （`{"singles":1,...}` リテラルの 3 コピー目）はこれを呼ぶ形に統一。
  `battle_format` 導入前から team 運用のユーザーでも、ホットキーと `activeBoard` が
  表示中のボードに一致する。`FORMAT_BOARDS` は `MappingProxyType`
  （`AutonaScoreboardManager.FORMAT_BOARDS` がエイリアスするため共有 mutable を避ける）
- 挙動差分（記録）:
  - 未構築ボードを宛先にした Web/API 操作が **ボードを構築しなくなった**
    （`display_options` トグルは board 1/2 を構築していた）。その代わり、未構築ボードの
    state に書いた値は、そのボードが後から構築されると `SeedBoard` で消える
  - **不正なボード番号の扱いが 500 → 無視 + `"OK"` に変わった**。従来は
    `GetScoreboard(999)` が `None` を返し、その属性アクセスが `AttributeError` →
    HTTP 500 になっていた。委譲後の `AdjustScore` / `ResetScore` / `SetTeamColor` は
    board 番号を検証して `logger` に落とし、no-op で戻る（legacy ルートは `"OK"`）。
    v1 API 側は `require_board_12` が先に 400 を返すため影響なし。影響するのは
    legacy REST / Socket.IO で存在しないボード番号を投げた場合のみ
  - `set_display_option`（legacy）は Actions の戻り値をそのまま反映して
    `"ERROR: unknown display option ..."` / `"OK"` を返す。判定の正本は schema 一箇所

- **既知の残リスク・非効率（Step 4a で回収済み。以下は当時の記録）**:
  - **スコアの書き手はまだ 2 系統**。Actions（`AdjustScore` / `ResetScore`）と、
    スピンボックス主導経路（セット読込 / `ClearScore` / `SwapTeams` / ステージ集計 →
    `AutonaIndividualGameTracker.UpdateScore` ・ `StageResultsChanged`）。
    両者は state 経由でのみ噛み合うため、「どちらが書いたか」を
    `AutonaIndividualGameTracker.writingScore` で見分ける必要がある
    （立っている間 `_SyncScoreDisplays` は `SyncStagesToScore` を呼ばない。
    呼ぶとステージボタン 1 クリックで次のゲームまで勝ちになる）。
    `UpdateScore` の Get→Set は `AdjustScore` と同じく `StateManager.lock` で囲んだ。
    完全一本化は Step 4/5
  - **held のみを書く `SaveBlock` が全 state 差分を踏む**。held キーへの書き込みは
    `SaveState` を起こさない（`_notifyHeldTouched` だけ）ので、held だけを書いた
    `SaveBlock` は `changedKeys` が空のまま `ReleaseSaving()` → `SaveState()` に入り、
    `include_paths=[]` = フィルタなし = **全 state の DeepDiff** になる。
    実測（トラッカー ON・BO5・`+1` 1 回）で `DeepDiff(include_paths=[])` が 1 回発生。
    該当箇所は `AutonaIndividualGameTracker.IncrementScore` / `DecrementScore` に
    限らず、`StageResultsChanged` / `SwapStageResults` / `SetStageCount` /
    `AutonaScoreboardWidget.SwapTeams` / **`AutonaActions.ResetScore`** も同型。
    **Step 3 では code を触らず記録に留めた**: 個別に `SaveBlock` を外すと
    (1) held 通知が 1 回 → 3 回に増え、(2) 万一 hold 未登録のボードに対して呼ばれた
    場合は 1 サイクル → 3 サイクルの publish になる、という副作用が付く一方、
    原因は 2 箇所の `SaveBlock` ではなく `ReleaseSaving()` が空の `changedKeys` でも
    `SaveState()` を呼ぶという **StateManager 側のセマンティクス**にあるため。
    Step 4 で `SaveState` 側（空 `changedKeys` かつ `_commitPrefixes` 空なら diff を
    スキップ）として一括で回収する
    → **Step 4a で実施済み**。スコアの 2 系統も、スピンボックス主導側が
    `src/core/GameTrackerService.py` に移って **両方 core 内**になった
    （呼び出し口が 2 つ残っている状態。完全一本化は Step 4b/5）
- 検証: フルスイート 445 passed / 19 skipped（Step 3 前 417 + 新規 28）。
  `tests/test_actions.py` 新設（QApplication なしで書き込み内容・held/即時の別・
  1 操作 = 1 サイクル・スレッド安全を pin）。ウィジェット側のエコーループ検査は
  `tests/test_scoreboard_operations.py`（1 操作あたり `held_state_updated` 1 回 /
  `display_options` は revision +1）。`test_webserver_routes` の台帳・
  `test_objectname_keypath_contract` は不変で pass

### Step 4a: (b) データロード系の抽出（統計・ステージ）+ 空 diff の回収 ✅ 完了（2026-08-31）

- **`SaveState` の空 `changedKeys` 全量 diff を回収**（Step 3 の既知の非効率）。
  commit 分をマージした後で `changedKeys` が空なら、diff・delta 配信・保存・
  export スレッド join をすべてスキップし、held 通知だけ出す。
  **単なる最適化ではなかった**: export スレッドが `lastSavedState` に
  `timestamp` / `revision` を書き込むため、フィルタなし diff は毎回この 2 キーの
  `dictionary_item_removed` を拾い、**delta 配信 + revision +1 + program_state.json
  再書き込み**を起こしていた（実測で確認）。held のみの `SaveBlock` 解放
  （トラッカー ON の `+1` / `SwapTeams` / ステージ操作 / `ResetScore` /
  `StationSetsLoaded`）と、pending の無いボードへの `CommitHeld` が該当。
  副作用として**フルスイートが 63 秒 → 47 秒**になった
  - 到達経路の棚卸し（全 5 箇所）: `ReleaseSaving` / `_trackChangedKey` /
    `CommitHeld` / `Initialize` / `LoadState` の例外分岐。**全量 diff に依存して
    delta を出していた正当な経路は無い**。`Initialize` と `LoadState` 例外分岐は
    どちらも `state` も `lastSavedState` も空の時点でしか走らない（`LoadState` の
    呼び出し元は `Initialize` のみ）ので、従来から diff は空だった
- **`AutonaStatsUtil` → `src/core/StatsService.py`**。判断と書き込み（取得の可否・
  必要な id・アップセット計算・公開する各サブツリーの形）はすべて core。Qt 側に
  残したのは**トランスポートだけ**: 取得トリガー 6 本（Flask スレッド → GUI スレッドの
  ホップ）と結果 3 本（start.gg ワーカー → GUI スレッド）、および
  `AutonaTournamentDataProvider`（Worker / QThreadPool は本ステップの対象外）。
  シグナルの in/out 分類は `AutonaStatsUtil` の docstring が正本
  - `playersPerTeam` は callable で注入する。統計はすべて 1v1 限定の指標だが、
    人数の正本はまだ Qt 側（`team1playerWidgets`）。state から
    `len(score.{n}.team.1.player)` を読む形にするのは選手サブツリー移行（Step 5）と一緒
- **`AutonaIndividualGameTracker` → `src/core/GameTrackerService.py`**。Step 3 が
  スコアに対して行った反転をステージ行にも適用した: **state が記録・ボタンは表示**
  （従来は `stageTeam1Check_{i}.isChecked()` が正本で state はミラー）。
  ウィジェットに残るのは行の生成・ステージコンボ・`stageResultsUpdate` シグナル、
  そして「クリック → サービス呼び出し 1 回 → state からボタンを再描画」の 3 手順のみ。
  `QAbstractButton.setChecked()` は `toggled` しか出さない（`clicked` は出ない）ので
  再描画がエコーループにならない
  - `writingScore` ガード・`StateManager.lock` RMW・`_SyncScoreDisplays` との
    連携の語義は不変（フラグの所有者がサービスに移り、ウィジェットは property で委譲）
  - 挙動差分（記録）: ステージボタン 1 クリックの held 通知が **6 回 → 4 回**に減った
    （フラグ 3 キーが個別 Set から 1 `SaveBlock` になったため。公開内容は同じ）。
    残る 4 回のうち 2 回は `AutonaScoreboardWidget.StageResultsToScore` が
    `PublishStageResults` の書いた 2 キーを同値で再 Set していたもので、**検収レビュー
    （S1）で削除し 2 回になった**。実測値: 修正前 4 / 修正後 2（新規 pin
    `test_a_stage_click_is_exactly_two_save_cycles`）、移行前 6 は旧コードの
    Set 回数（フラグ 3 + 集計 SaveBlock 1 + 再 Set 2）による
  - ステージコンボの `stages.{i}` 上書き時、結果フラグの復元元がボタンから state に
    変わった（両者一致が不変条件なので値は同じ）
  - 挙動差分（記録）: **pending の無い「反映」が完全な no-op になった**。従来は
    空 `changedKeys` の `SaveState` が全量 diff を走らせ、`lastSavedState` にだけ
    ある `timestamp` / `revision` の削除 delta を配信 + `program_state.json` を
    再書き込みしていた（= 中身が変わらないまま OBS/Web UI へ更新が飛ぶ）。
    R-Sync-11 に照らせば旧挙動の方が違反（保留分が無いのに配信していた）だが、
    **副作用として「反映」を OBS 側の強制リフレッシュ手段に使えていた**。
    これは使えなくなった。強制再出力が要る場面が出たら専用の操作を用意する
- **スコープから外した項目**（Step 4b = 次 PR）: セット読込の正規化
  （`ChangeSetData` / `NewSetSelected` / `SetData` / `UpdateData`）と
  TournamentInfo のヘッドレス化。これらは選手サブツリー（`SwapWith` / `SetData`）と
  `AutonaLocaleHelper` に依存し、1 PR の粒度を超える
- 検証: フルスイート **503 passed / 19 skipped**（Step 4a 前 453 + 新規 49 +
  検収レビューの pin 1）。**既存テストのアサーションは 1 つも書き換えていない** —
  トラッカー ON の回帰群（`test_scoreboard_operations.py`）・エコーループ検査・
  held/commit 系・`test_webserver_routes` の台帳・`test_objectname_keypath_contract`
  はすべて不変で pass（S1 の再 Set 削除後も同じ）。
  新規: `tests/test_stats_service.py`（21）/ `tests/test_game_tracker_service.py`（24）は
  QApplication なしで動く = 移行で得たもの。`tests/test_state_manager_held.py` に
  空 diff の pin を 4 本追加（DeepDiff の呼び出し回数と `include_paths` を spy で確認）
  - 検収レビューでの追加・修正: `test_a_stage_click_is_exactly_two_save_cycles`
    （S4 の pin）を追加。`test_game_tracker_service.py` の分離 fixture が
    `heldPrefixes` / `heldKeys` も退避・復元するようにし（N8）、
    `test_state_manager_held.py` の `include_paths` 検査を set 比較へ（N9。
    `SaveState` が `set(changedKeys)` で組むため list 比較は順序依存）
- 未実施（次 PR 以降で回収）: start.gg 実データでのセット読込（手動チェックリスト
  「トーナメントデータ」節）と `STARTUP phase=` ログでの計測。Step 4a は起動経路を
  変えていないため後者は Step 4b とまとめて測る

### Step 4b: セット読込の正規化と TournamentInfo のヘッドレス化

- `ChangeSetData`（140 行超の正規化ロジック）/ `NewSetSelected` / `SetData` /
  `UpdateData`（TournamentInfo）を core サービスへ抽出し、Actions から呼ぶ。
  ウィジェットは通知を受けて表示するだけにする
- これにより起動残候補「TournamentInfo のヘッドレスサービス抽出」
  （startup-architecture-redesign.md §3）が実行可能になる → 実施して eager パネルを解消
- `AutonaIndividualGameTracker` の UI トグル 16 箇所は Step 6 扱い（Step 4a でロジックは
  core に出たが、行の生成と表示は Qt 側に残っている）
- **注意（不変条件）**: セット読込で `stagesWon` を `score.{n}.stages` へ流し込む際は、
  「state の `stages` の行数 == ウィジェットの `stage_widget_list` の長さ」を
  `GameTrackerService.SetStageCount()` だけが保証している点に注意する
  （`StageCount()` の docstring 参照）。行を直接足す経路を作ると、描画されない行や
  クリック位置のズレが出る
- 検証: start.gg 実データでのセット読込（手動チェックリスト「トーナメントデータ」節）+
  統計系テスト + `STARTUP phase=` ログで core-shown 短縮を計測

### Step 5: (a) UI 直接反応の移行 + キーパス静的化（最難関・最後）

- ウィジェット単位に PR を分割: Scoreboard → Player/BasePlayer → TournamentInfo →
  CrewBattle → その他（LayoutOptions / StreamSettings / FeatureColors / Sponsor）
- `findChildren` ループ + `objectName()` 動的キーの各バインディングを、
  キーパスカタログ参照 + Actions 呼び出しに置換（T-19）
- 保存データ互換のマイグレーション層を schema に実装（旧キー → 新キーの読み替え。
  互換が守れないケースが出たら ADR-004 Revisit conditions に従い `STATE_VERSION` 一括変換へ）
- 完了条件: ウィジェットからの `StateManager.Set` 直接呼び出し 0。
  `test_objectname_keypath_contract.py` は役目を終え、schema カタログの契約テストに置換
- 検証: ウィジェットごとに手動チェックリスト該当節 + 旧バージョンの保存データ読込テスト

### ステップ間の依存

Step 0 → 1 → 2 → 3 は直列。Step 4 は Step 3 完了後に (b) 対象ごとに並行可
（実際には 4a = 統計・ステージ・空 diff / 4b = セット読込・TournamentInfo に分割した。
4b は選手サブツリーに触るため 4a と同一 PR に収まらない）。
Step 5 は Step 2（カタログの器）と Step 3（Actions 基盤）の完了が前提。
**移行専念で連続実施する**（2026-08-28 PdM 決定）: 新旧経路の併存期間とエコーループ抑止の
運用負荷を最小化するため、移行完了まで機能開発を挟まない。独立リリース可能な粒度
（1 PR = 1 ステップ以下）は維持し、リリース自体は各ステップ完了時に行ってよい。

## 5. 検証方法（全ステップ共通）

- フルスイート（現状 349 passed / 31 秒）+ 契約テスト群
  （webserver 台帳 / objectname keypath（Step 5 まで）/ design tokens / diff update）
- `tests/test_core_qt_free.py`（Step 1 以降）: core の Qt 汚染を即検出
- エコーループ検査: 代表操作（スコア増減・名前入力・セット読込）で
  `StateManager.revision` の増分が期待回数と一致すること
- 手動チェックリストは各ステップで該当節のみ実施（全節なぞりは release 時のみ）
- 性能: `STARTUP phase=` ログ（起動）と「セット反映 300 回」系の既存リークテスト
  （v1.7.1 で修正した劣化の再発防止）

## 6. ロールバック方針

- 各ステップは独立 PR・独立リリース。問題時は当該 PR revert で戻る
- Step 1（observer 化）だけは通知経路の全点（`connect` 13 + `emit` 8）に触るため、リリース前に
  dev / frozen / OBS 実機（delta 配信）の 3 点を必ず通す
- Step 5 のマイグレーション層は「旧キーの読み替え」方式のため、revert しても
  旧バージョンで保存されたデータは読める（前方互換を壊す変更を入れない）

## 7. 決定済み事項と残る判断

**本書で決定済み（ADR-004 準拠 + 2026-08-28 PdM 決定）**:

- 層構成 / 移行順序 / 非目標（スタック・外部契約・OBS 方式・認証は変えない）/
  エコーループ抑止規約
- **配分: 移行専念**。新旧経路の併存期間を最小化するため、移行完了まで機能開発を挟まず
  Step 0〜5 を連続実施する
- legacy REST 約 80 ルートは本再設計では触らない（契約不変）。縮退は React 側が
  v1 API へ寄った将来に別提案として起票
