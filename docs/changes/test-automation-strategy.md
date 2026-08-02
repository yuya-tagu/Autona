# テスト自動化戦略

作成日: 2026-08-02
状態: 戦略承認済み（2026-08-02）。**Phase 1 (PR-1) 実装済み** — §5 の進捗表を参照。
背景: 潜在バグの検出が手動テスト頼みになっており、`.claude/manual-test-checklist.md` の項目が増える一方で回帰検出が追いついていない。

---

## 1. 現状（実測）

| 層 | 件数 | 実行時間 | 対象 |
|----|------|---------|------|
| Python (pytest) | 212 件 / 20 ファイル | 28 秒 | ロジック・契約・更新系 |
| Web (Jest + RTL) | 21 件 / 6 ファイル | 47 秒 | ScoreboardDesigner / themes のみ |

いずれも全件グリーン。実行環境: Python 3.12.10 / pytest 9.0.3 / Node v24.13.0。
`pytest-cov` `pytest-qt` `pytest-mock` は未導入。

### カバーされている領域

`AutonaDictHelper` / `StateManager`（held・exempt セマンティクス）/ Start.gg パース /
`AutonaTop8Model` / `AutonaCommentaryModel` / `AutonaRemoteZip` / 更新系契約（apply_update・差分更新）/
ホットキー（suspend・enabled）/ `AutonaDesignerService` / デザイントークン /
import 副作用 / objectName キーパス契約 / WebServer ルート台帳 / GuiExecutor / SelectSetWindow キャッシュ。

### カバーされていない領域（＝バグ密集地帯）

| モジュール | 行数 | テスト |
|-----------|------|--------|
| `AutonaScoreboardWidget` | 1962 | ゼロ |
| `AutonaGameAssetManager` | 1501 | ゼロ |
| `AutonaScoreboardPlayerWidget` | 983 | ゼロ |
| `AutonaTeamPlayerWidget` | 637 | ゼロ |
| `AutonaApiV1` | 733 | ゼロ（26 ルート） |
| `AutonaCrewBattleWidget` | 657 | ゼロ |
| `AutonaTournamentInfoWidget` | 610 | ゼロ |
| `AutonaWebServerActions` | 587 | ルート名の台帳のみ・挙動は未検証 |
| Web `ScoreboardPage` / `redux` | 21 ファイル | ゼロ |

`.claude/known-smells.md` S-01 が「テスト網の無い中核 UI」と明記している通り、
**スコアボードとその周辺（＝アプリの中核機能）は完全に無防備**。

---

## 2. 過去のバグ分類（fix コミット約 40 件）

| 分類 | 件数目安 | 例 | 自動テスト適性 |
|------|---------|-----|--------------|
| **A. UI ⇄ StateManager の状態同期漏れ** | 12 | Swap の取りこぼし / `clear_all`・`reset_match` の best_of 参照ミス / 1 キャラ選手ロードで全スロット汚染 / commentary インデックスが詰まらない / stats の残留エントリ / シード情報なしでシード表示 / キャッシュ復元時にサイドバー未選択 | ◎ 最適 |
| **B. Qt スレッド / シグナル** | 6 | lambda 接続がワーカースレッドで実行 / `GuiExecutor.run_sync` 破損 / `Signal(dict)` の queued コピー / モデル差し替えで access violation | ○ 一部可能 |
| **C. 無音失敗** | 6 | X 投稿がサムネイル失敗で全体中断 / `DETACHED_PROCESS` で更新ヘルパー不発 / zip 区切り文字 / ヘルパー多重起動 | ◎（更新系は既にカバー済） |
| **D. 外部境界** | 4 | Start.gg パース / ポート衝突 / 日付 `undefined NaN` | ◎（Start.gg は既にカバー済） |
| **E. 表示・レイアウト** | 8 | 幅配分 / クルー名はみ出し / 文字影 / i18n | △ 目視が必要 |

**A が最多かつカバー率ゼロ**。ここを潰すのが最大の投資対効果。

---

## 3. 技術的前提の実証（2026-08-02 プローブ済み）

「中核 UI は自動テストできない」という前提が、この戦略の唯一のリスクだった。**検証して否定された。**

使い捨て cwd（`assets` / `layout` / `src/layout` / `src/i18n` / `src/TournamentDataProvider` を
ディレクトリジャンクション、`user_data` は合成）+ `QT_QPA_PLATFORM=offscreen` で:

| 操作 | 所要時間 | 結果 |
|------|---------|------|
| `bootstrap_core()` | 1.2 秒 | 全シングルトン生成完了 |
| `EnsureScoreboard(1)` (1v1) | 0.36 秒 | `AutonaScoreboardWidget` 構築成功 |
| `EnsureScoreboard(2)` (team) | 0.17 秒 | 同上 |
| `EnsureScoreboard(3)` (crew) | 0.04 秒 | `AutonaCrewBattleWidget` 構築成功 |
| `signals.CommandScoreChange.emit(0, 1)` | — | `StateManager.Get("score.1.team.1.score")` == 1 |
| `SwapTeams()` | — | チーム 1/2 のスコアが入れ替わる |
| `out/program_state.json` | — | 生成される |

**ジャンクションは管理者権限不要**（`mklink /J`）。セッションスコープの fixture で 1 回だけ張れば済む。

### プローブ 1 回目で検出したバグ 2 件

#### BUG-1: エクスポートスレッドがライブ state を反復して落ちる 🔴 ✅ PR-1 で修正済み

```
File "src/StateManager.py", line 584, in CreateFilesDict
    for k, i in di.items():
RuntimeError: dictionary changed size during iteration
```

原因: `SaveState()` は `state_snapshot` を `deep_clone` して渡す一方、
**`diff` はクローンしていない**（`src/StateManager.py:326`）。
`ExportText` の `values_changed` / `type_changes` 経路が使う `change.get("new_value")`
（同 541 行）は `StateManager.state` の**ライブ参照**であり、
これをバックグラウンドのエクスポートスレッドが反復する間に GUI スレッドが state を書き換えると衝突する。

影響: エクスポートスレッドが未捕捉例外で死ぬ → その回の `out/**` ファイル出力が途中で欠落する。
トレースバック自体は（凍結ビルドの stderr 差し替え経由で）`./logs/tsh-error.log` に ERROR として残るが、
**GUI には何も伝わらず、誰もそのログを見ない**ため、実質的には `./out` が黙って欠けるだけの無音失敗。
OBS オーバーレイが古い値のまま止まる。
`SaveState()` は次回 `_export_thread.join()` するがスレッドは既に死んでいるためハングもしない。

**修正**（PR-1）: `ExportText` が値をスナップショットからパス指定で読むように変更。
diff は「どのパスが変わったか」だけに使い、値の参照には使わない。
スナップショットは diff と同一ロック内で採られるため整合する。
回帰テストは `tests/test_state_manager_export_isolation.py`
（うち 2 件は修正前のコードで実際に失敗することを確認済み）。

#### BUG-2: `user_data/additional_flag` 不在で国旗が全滅（優先度低・未修正）

`AutonaCountryHelper.LoadCountries` が `os.listdir("./user_data/additional_flag")` で例外。
try で握られるため起動は継続するが、以降**国旗が 1 つも読めない**。
当該ディレクトリは git 管理下なので通常運用では起きないが、
ユーザーが `user_data` を掃除した場合に再現する。フォールバックを入れるべき。

### PR-1 実装中に追加検出した 3 件（いずれも未修正）

#### BUG-3: best_of ハンドラの例外で保存が恒久的に停止しうる 🟠

[src/AutonaScoreboardWidget.py:674](../../src/AutonaScoreboardWidget.py) の `bestOfSpin.valueChanged`
ハンドラは `StateManager.BlockSaving()` … `StateManager.ReleaseSaving()` を**リスト内包の要素として**
並べている。途中の `StateManager.Set` が例外を投げると `ReleaseSaving()` に到達せず、
`saveBlocked` カウンタが正の値のまま残り、**以降すべての SaveState が抑制される**
（= 配信中に state 出力が丸ごと止まる）。agent-pitfalls #6 の裏返し。

実際に harness 構築中に踏んだ: locale 未ロード時に `matchNames.get("best_of")` が None を返し
`.format()` で AttributeError → 後続の `first_to` 等が未書き込みのまま。
本番では `Window.__init__` が locale をロードするため現状は顕在化しないが、
ハンドラが例外を投げれば保存が死ぬ構造そのものが問題。
対処: `with StateManager.SaveBlock():` に置き換え、ラムダをメソッドへ抽出する。

#### BUG-4: crew ボードの `reset_match` が best_of を state からクリアしない 🟡

board 3 の `_bestOfSpin` は「generic な WebServer アクションが AttributeError を出さないための
hidden compat widget」であり、値が state に配信されない。
`ChangeSetData({"bestOf": N})` は `score.3.best_of` を書くため、
**セット読込後に reset_match しても オーバーレイは古い BO を表示し続ける**（board 1/2 はクリアされる）。
`tests/test_scoreboard_operations.py` に `xfail(strict=True)` で追跡中。修正時にマーカーを外す。

#### BUG-5: `AutonaScoreboardWidget.closeEvent` が None タイマーで例外（優先度低）

`closeEvent` は `self.autoUpdateTimer.stop()` を無条件で呼ぶが、両タイマーは
`NewSetSelected` まで `None`。セットを一度も読み込まずにボードを閉じると AttributeError
（Qt がイベントハンドラ内例外を握るため無症状）。

### PR-1 レビューで追加検出（2026-08-02）

#### BUG-6: `AutonaStatsUtil.instance` が誰からも代入されず常に None 🟠 **[修正済み]**

`AutonaStatsUtil.instance` は上流 TSH のモジュール末尾シングルトン
（`TSHStatsUtil.instance = TSHStatsUtil()`）の残骸。フォーク初回コミット
（`c8a36577`）で ctor が per-board（`__init__(scoreboardNumber, scoreboard)`）に
変更された際に代入だけが消え、`AutonaWebServerActions.stats_recent_sets` /
`stats_upset_factor` の参照が取り残された。結果、
`/scoreboard{n}-stats-recent-sets` / `-stats-upset-factor` は
**フォーク初日から全リクエストで `AttributeError: 'NoneType'`**（かつ board 番号を無視）。

**修正（2026-08-02・別 PR `fix/stats-util-none-ref` で対応）**: 参照を正本パターン
`GetScoreboard(n).stats.signals...` に揃え、`instance` クラス属性は削除
（シングルトン再導入禁止を docstring に明記）。外部 API（URL・イベント名・戻り値）は不変。
pin: `tests/test_stats_util.py`。

同経路の派生 2 件も同時に修正:
- Socket.IO の `ws_stats_recent_sets` / `ws_stats_upset_factor` が 1 引数メソッドに
  `info.get("player")` を余分に渡していた TypeError（`stats_last_sets` からのコピペ痕）
- `AutonaStatsUtil` の `provider.name` 参照に provider(None) ガードが無く、
  大会未読込時に必ず AttributeError（`_IsStartGG()` ヘルパに集約して 3 箇所を修正）

#### PR-2 着手前の必須前提: 画像ダウンローダスレッドの待機機構

`StateManager.CreateFilesDict` は値が `http...png/jpg` の文字列だったとき
**daemon スレッドを起こして画像をダウンロードし、`./out/{path}.png` に書く**
（`src/StateManager.py` の `downloadImage`）。書き込み先は相対パスなので
**書き込み時点の CWD** で解決される。`_AutonaHarness.settle()` はエクスポートスレッドしか
join しないため、このスレッドは harness の CWD 復元後に走りうる。

**選手ウィジェット（avatar / キャラ画像の URL を state に流す層）のテストを書く前に、
ハーネス側へダウンローダの待機機構を入れること。** 入れずに PR-2 を進めると、
テスト実行が実プロジェクトの `./out` へ画像を書く事故になる。

#### 運用注意: ハーネス外から設定・選手 DB の保存系を呼ばない

`autonaApp` fixture の外（= CWD がプロジェクトルート）で `SettingsManager.Set()` や
`AutonaPlayerDB` の保存系を呼ぶと、**実運用の `user_data/settings.json` /
`user_data/local_players.json` を上書きする**。ユーザーの API トークン・ポート・
ホットキー設定が消える。

併せて、`bootstrap_core()` 後のプロセスでは `SettingsManager` が
**ハーネスの使い捨て `user_data`（空の設定）を指したまま**になる。
ハーネス外のテストが `SettingsManager.Get()` の戻り値に依存する場合、
実行順によって観測値が変わる。

#### 既知フレーク: フルスイートの access violation（本 PR 非起因）

フルスイート 13 回中 1 回の頻度で `tests/test_select_set_cache.py` 付近が
access violation でプロセスごと落ちる。Qt の teardown 系と見られるが再現条件は未特定。
落ちた場合は再実行する。

#### ハーネス実装上の地雷: `sendPostedEvents` は対象を絞る

pytest は `exec()` を回さないためイベントループ階層が 0 のままで、
`QApplication.processEvents()` は **DeferredDelete を消化しない**
（Qt は「投稿時のループ階層 < 現在の階層」でしか回収しない）。
そのため `deleteLater()` した board の C++ オブジェクトは生き続け、
グローバルシングルトンへの lambda 接続が残ったままゴーストとして state を書き戻す。
`QCoreApplication.sendPostedEvents(obj, QEvent.Type.DeferredDelete)` で明示回収する。

**ただし receiver を `None`（全オブジェクト）にしてはいけない。** レビュー対応中に実測した
ところ、`sendPostedEvents(None, DeferredDelete)` を `settle()` に置くとフルスイートが
2 回連続で access violation で落ちた（`WebServerActions.__init__` の `QThread` 生成中）。
破棄対象を board 1 個に限定した現行実装では 6 連続グリーン。回収は必ず対象を指定する。

#### 根治残件: プロダクション側の lambda 接続

上記ゴースト問題の根本原因は、ウィジェットがグローバルシングルトンのシグナルへ
**lambda で接続している**こと（例:
[src/AutonaScoreboardWidget.py:757](../../src/AutonaScoreboardWidget.py) の
`AutonaGameAssetManager.instance.signals.onLoad`）。
bound method なら PySide6 が receiver を weakref で追い、破棄時に自動切断するが、
lambda は receiver への参照を持たないため切断されない。
ハーネスは `sendPostedEvents` で C++ 破棄を強制して回避しているが、
**プロダクション側の lambda → bound method 化は別 PR**とする。

### ドキュメントの実態乖離

`.claude/architecture/phase-a-b/state-keys.md` は選手タグのキーを `gamerTag` と記載しているが、
実際に配信されるのは `name`（QLineEdit の objectName がそのままキーになる）。
`gamerTag` は `SetData` の**入力** dict のフィールド名であって state キーではない。

---

## 4. 戦略：4 層 + Web

| 層 | 手段 | 対象 | 目標時間 |
|----|------|------|---------|
| **L1 純ロジック** | pytest（Qt なし） | パース・モデル・ヘルパー・契約 | 現状維持（〜10 秒） |
| **L2 ウィジェット結合** ★新設 | offscreen Qt + `bootstrap_core()` | スコアボード / 選手 / crew / 大会情報 | 〜30 秒 |
| **L3 HTTP 契約** ★新設 | Flask `test_client()` | 44 routes + 38 events + 26 api/v1 | 〜15 秒 |
| **L4 起動スモーク** ★新設 | サブプロセスで実起動 | 起動 → 出力 → HTTP 応答 → ERROR ゼロ | 〜30 秒 |
| **L5 Web UI** | Jest + RTL（既存） | redux + ScoreboardPage | 〜60 秒 |

合計 3 分以内。1 コマンド（`pytest` と `npm test`）で Claude が自走実行できることを要件とする。

### L2 の設計（本戦略の肝）

`tests/conftest.py` に session スコープの `autonaApp` fixture を新設する。

```
autonaApp (session)
├── 使い捨て cwd を作成（tmp_path_factory）
│   ├── assets / layout / src/layout / src/i18n / src/TournamentDataProvider を junction
│   └── user_data を合成（settings.json で hotkeys.enabled=false、合成ゲーム 1 本）
├── QApplication(offscreen) を 1 個
└── bootstrap_core() を 1 回
```

**制約と対処:**

| 制約 | 対処 |
|------|------|
| `bootstrap_core()` はプロセス内 1 回きり（再入ガード） | L2 は「1 プロセス = 1 ブート + 多数のテスト」。テスト間の独立性は function スコープの `resetState` fixture（`StateManager.Unset("score")` → `EnsureScoreboard` 再構築）で担保 |
| グローバルホットキーを OS に登録すると実機と衝突 | `hotkeys.enabled=false` を合成 settings に。加えて `AutonaHotkeys.UiMounted` は L2 では呼ばない |
| WebServer がポート 5000 を掴む | L2 では WebServer を構築しない（L3 は `run()` を呼ばず `test_client()` のみ） |
| 実運用の `out/` `user_data/` を汚染しうる | 全て使い捨て cwd。**「テスト後にプロジェクト直下の `out/` が変化していないこと」を検証するガードテストを入れる** |
| pytest-xdist 並列化すると Qt が壊れる | 当面導入しない。3 分に収まる想定 |

`AutonaScoreboardPlayerWidget` と `AutonaTeamPlayerWidget` は基底クラスを共有しない別実装
（agent-pitfalls #5）。**同一のテストを両クラスに `parametrize` で流す**ことで、
「片方だけ直して片方を忘れる」既知の事故パターンを構造的に潰す。

### L4 の設計

`main.py` は単一インスタンスミューテックスを持つため、実機で Autona が起動中だとテストが素通りする。
L4 は `main.py` を使わず、サブプロセス内で `QApplication` → `bootstrap_core()` → `Autona.Window()` を
使い捨て cwd で組み立て、以下を検証する:

- `STARTUP phase=` ログが所定の段階まで出る
- `out/program_state.json` が生成され妥当な JSON である
- WebServer が指定ポート（5000 ではない）で応答する
- ログに `ERROR` / 未捕捉例外が出ていない ← **BUG-1 はここで恒久的に捕まる**

---

## 5. 実装順（PR 単位）

### Phase 1: 土台 ✅ 完了（2026-08-02）
- **PR-1** L2 ハーネス（`tests/conftest.py` の `autonaApp` fixture + 汚染ガード
  `tests/test_harness_isolation.py`）
  + `tests/test_scoreboard_operations.py`: スコア加減算 / Swap / Reset Score / Clear All
  → 過去バグ「Swap の取りこぼし」「`clear_all`・`reset_match` の best_of 参照」を pin
  + BUG-1 修正 + `tests/test_state_manager_export_isolation.py`

  **実績**: Python 212 件 / 28 秒 → **248 件 + xfail 1 / 22 秒**（第三者レビュー反映後）。
  ハーネス実装で判明した設計上の注意点:
  - board 破棄は `deleteLater()` だけでは足りない（§3「ハーネス実装上の地雷」参照）
  - `_dropBoard()` は **held 登録の解除を `Unset` より先に**行う。逆順だと Unset の
    変更キーが held バッファに積まれてそのまま破棄され、削除が delta にも `./out` にも
    出ない（`tests/test_harness_isolation.py` の
    `test_dropping_a_board_removes_its_exported_files` が pin）
  - `bootstrap_core()` はプロセス内 1 回きり（再入ガード）なので board は**テストごとに再構築**する。
    リセット経路そのものがテスト対象なので、リセットで独立性を担保すると循環する
  - `AutonaScoreboardWidget.__init__` は `HoldSubtree("score.{N}")` を登録する。
    StateManager に解除 API がないため、board 破棄時に harness 側で登録を掃除している
    （放置すると `test_state_manager_held.py` が実行順に依存して落ちる）
  - harness は `SettingsManager` を**書き換えない**。プロセス共有のため
    `hotkeys.*` を固定すると `test_hotkeys_*.py` の観測結果が変わる
  - `bootstrap_core()` は locale をロードしない（`Window.__init__` の担当）。
    harness も `LoadLocale()` / `LoadRoundNames()` を呼ぶ必要がある（BUG-3 参照）

### Phase 2: バグ密集地帯（分類 A）
- **PR-2** 選手ウィジェット（1v1 / team を parametrize で同時検証）
  `SetData` / キャラスロット / mains / seed 表示
- **PR-3** best_of / players_per_team / battle_format 切替
- **PR-4** crew battle（ストック・アクティブ選手・勝敗遷移）

### Phase 3: 境界
- **PR-5** L3 HTTP 契約（既存 44 routes の主要動作 + api/v1 の冪等性・confirm 必須・409）
  → `.claude/manual-test-checklist.md` の「外部制御 API」節を自動化に置換
- **PR-6** L4 起動スモーク

### Phase 4: Web
- **PR-7** redux slice + `ScoreboardPage` 主要コンポーネント

### 並行
- `pytest-cov` を導入し `src/` のカバレッジを可視化する。
  **数値目標は置かない**。基準は「バグが出た箇所には必ずテストがある」。

---

## 6. 運用ルール（この戦略の維持条件）

1. **バグ修正 PR にはリグレッションテストを必ず添える。** テストを書けない修正は、
   書けない理由を PR 本文に明記する
2. `.claude/manual-test-checklist.md` の項目は、自動化できたら**削除する**（二重管理しない）
3. L2 のハーネスが壊れたら実装側を直す。テスト側を緩めない
   （`tests/test_import_side_effects.py` と同じ運用）
