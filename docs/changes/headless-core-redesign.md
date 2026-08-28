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
│  QtBridge（observer → Qt シグナル変換。GUI スレッド境界もここ）│
│  WebServer（Flask/Socket.IO。既存の delta 配信・契約は不変）   │
└───────────────────────────────────────────────────────────────┘
```

設計上の要点:

- **エコーループ抑止を規約化する**: Actions が書いた state 変更を QtBridge 経由で受けた
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

### Step 0: 掃除・準備（リスク極小）

- `pynput` を requirements / Pipfile / licenses から削除（import ゼロの残留依存）
- デッドコード削除: `AutonaTeamBattleWidget`(423 行) + `AutonaTeamPlayerWidget`(658 行) +
  `AutonaTeamBattleModeEnum`(26 行)。唯一の生成元は import ごとコメントアウト済み。
  BUG-7 の `xfail` と関連テスト・agent-pitfalls #5 の記述も整理
- `WebServerActions` の `QThread` → `QObject` 化、未使用 `QThreadPool` 削除
- ドキュメントドリフト修正: Design.md の `set_light_mode` / `/api/v1/ui/theme` 残骸、
  commentary 12/8 混在、`.claude/README.md` の external-control-api「未実装」表記
- 検証: フルスイート + `test_webserver_routes.py`（台帳不変）

### Step 1: core 境界の確立（要石）

- `src/core/` パッケージ新設。`StateManager` のシグナル 4 本を Qt 非依存 observer
  （素の callback 登録）に置換し、既存シグナルと同名の Qt シグナルを `QtBridge` が提供
  （既存の接続元 20 箇所は QtBridge に付け替え、挙動不変）
- `tests/test_core_qt_free.py` 新設: core 配下の import が Qt を引かないことを
  subprocess で pin（`test_import_side_effects.py` 方式）
- `StateManager` / `SettingsManager` / `AutonaDictHelper` 等の Qt-free 資産を core へ移動
  （import パスは互換 re-export を 1 世代残す）
- 検証: フルスイート + 手動チェックリスト「起動/終了」節。delta 配信の回帰がないこと
  （React 側 revision 連番・OBS poll）

### Step 2: シード・スキーマの core 移動

- シード一本化: `AutonaScoreboardWidget.__init__` と `_SeedBoardState` の重複を解消し、
  core の `schema.SeedBoard()` に集約。`_SeedPanelState` も同様
- ウィジェット属性として存在するスキーマ定義（`STATS_KEYS`（held exempt）、
  `display_options` のキー集合 `elements`）を core の schema へ移動
- キーパスカタログの器を新設（この時点では既存キーの列挙のみ。静的化は Step 6）
- 検証: `test_objectname_keypath_contract.py` 不変 + held/commit 系テスト

### Step 3: AutonaActions 新設と「簡単な書き込み」の反転

- core に `AutonaActions` を新設。**state を直接書く実装**を、ロジックが自明な操作から
  実装する: スコア増減 / リセット / スワップ / チーム色 / `display_options` / best_of
- `WebServerActions` は該当メソッドを `AutonaActions` へ委譲する互換層に変える
  （経路 [2][3] が自動的に headless 経由になる）
- Qt ウィジェット側: 該当操作のハンドラを「Actions 呼び出し + state 通知での表示更新」に
  差し替え（エコーループ抑止規約を適用）
- 検証: v1 API の手動チェックリスト該当節 / 同一操作で delta 1 回 / スコア操作の
  既存テスト群
- ホットキー 9 本の接続先をウィジェットのバウンドメソッドから `AutonaActions` に付け替え
  （「パネル未構築でホットキーが死ぬ」の構造的解消）。`Autona.py` の
  `refresh_phase_group` はパネル構築を伴うため Window 側に残す

### Step 4: (b) データロード系の抽出

- `AutonaStatsUtil` を core のドメインサービス化（Qt 依存はシグナル定義のみ・全書き込みが
  (b) 型・テスト済みで最有力候補）
- `ChangeSetData`（140 行超の正規化ロジック）/ `NewSetSelected` / `SetData` /
  `UpdateData`（TournamentInfo）を core サービスへ抽出し、Actions から呼ぶ。
  ウィジェットは通知を受けて表示するだけにする
- これにより起動残候補「TournamentInfo のヘッドレスサービス抽出」
  （startup-architecture-redesign.md §3）が実行可能になる → 実施して eager パネルを解消
- `AutonaIndividualGameTracker` のステージトラッキングロジック（29 Set のうちドメイン部分）を
  core へ。UI トグル 16 箇所は Step 6 扱い
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

Step 0 → 1 → 2 → 3 は直列。Step 4 は Step 3 完了後に (b) 対象ごとに並行可。
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
- Step 1（observer 化）だけは接続元 20 箇所に触るため、リリース前に
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
