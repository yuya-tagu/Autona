# テスト自動化の層構成と `autonaApp` fixture

テストを書く人向けの現行の手引き。経緯（バグ発見ログ・導入時の Phase）は git 履歴にある（コミット d4311f81 の同名ファイル）。

## 層構成

| 層 | 手段 | 対象 |
|----|------|------|
| L1 純ロジック | pytest（Qt なし） | パース・モデル・ヘルパー・契約・core のサービス |
| L2 core 結合 | offscreen Qt + `bootstrap_core()`（`autonaApp` fixture） | ボードの state・v1 操作・core の書き込み経路 |
| L3 HTTP 契約 | Flask `test_client()` | `/api/v1`・legacy ルート・Socket.IO。ルート台帳は `tests/test_webserver_routes.py` |
| L4 起動スモーク | サブプロセスで実起動（`tests/test_headless_smoke.py`・`tests/test_headless_seed.py`） | 起動 → 出力 → HTTP 応答 → ログに ERROR ゼロ |
| L5 Web UI | Jest + RTL（`cd web && npm test`） | redux・各ページ・Designer |
| L6 ブラウザ E2E | Playwright（Chromium）+ `python main.py --headless` の実起動 | `/web` と OBS オーバーレイ（文言・DOM・state で確認。スクリーンショット比較はしない） |
| L-live | `tests/live/`（`AUTONA_LIVE_TESTS=1` でオプトイン） | Start.gg 実大会の API 形状。疎通不能・大会消失は skip、データ形状の変化は fail |

- L1〜L5 は `pytest` と `npm test` の 2 コマンドで自走できる。L6 は先に `npm run build` が要る
- 全量は並列ランナー `scripts/run_tests_parallel.py`（test ファイルを前回の実測時間で 4 つ前後に分け、各々を普通の pytest プロセスで走らせる。pytest-xdist は Qt が壊れるので使わない）。CI は PR で同じ並列ランナー、main への push では直列の `Python tests (serial)` も走る
- 小さな変更は影響範囲のテストだけを回す。`QT_QPA_PLATFORM=offscreen python -m pytest tests/test_xxx.py`

## L2: `autonaApp` fixture（`tests/conftest.py`）

session スコープで、使い捨て cwd を作って 1 回だけ起動する。

```
autonaApp (session)
├── 使い捨て cwd（tmp_path_factory）
│   ├── assets / src/i18n / src/TournamentDataProvider を junction（layout は空の実ディレクトリ）
│   └── user_data を合成（settings.json で hotkeys.enabled=false）
├── QApplication(offscreen) を 1 個
└── bootstrap_core() を 1 回
```

ジャンクションは管理者権限不要（`mklink /J`）。

### 制約と対処

| 制約 | 対処 |
|------|------|
| `bootstrap_core()` はプロセス内 1 回きり（再入ガード） | 1 プロセス = 1 ブート + 多数のテスト。テスト間の独立性は、使うボードをテストごとに作り直して担保する |
| `bootstrap_core()` は locale をロードしない（`Window.__init__` の担当） | ハーネスが `LoadLocale()` / `LoadRoundNames()` を呼ぶ |
| グローバルホットキーが実機と衝突する | 合成 settings で `hotkeys.enabled=false`。`AutonaHotkeys.UiMounted` は L2 では呼ばない |
| WebServer が既定ポート 5107 を掴む | L2 は WebServer を構築しない（L3 は `run()` を呼ばず `test_client()` のみ） |
| 実運用の `out/` `user_data/` `layout/` を汚染しうる | 全て使い捨て cwd。`tests/conftest.py` の監査フック（`_refuseRealWrites`）が、本物の `out/` `user_data/` `layout/` への書き込みをスレッドを問わず拒否してそのテストを落とす（見えるのはそのプロセスの Python からの書き込みだけ） |
| `SettingsManager` はプロセス共有 | ハーネスは `SettingsManager` を書き換えない（`hotkeys.*` を固定すると `test_hotkeys_*.py` の結果が変わる） |

運用上の注意: `autonaApp` の外（cwd がプロジェクトルート）で `SettingsManager.Set()` や選手 DB の保存系（`PlayerDB.instance`）を呼ぶと、**実運用の `user_data/settings.json` / `local_players.json` を上書きする**（API トークン・ポート・ホットキー設定が消える）。また `bootstrap_core()` 後のプロセスでは `SettingsManager` がハーネスの空の設定を指したままなので、ハーネス外のテストが `SettingsManager.Get()` の値に依存すると実行順で結果が変わる。

### ボードの用意

| 呼び方 | 内容 |
|------|------|
| `autonaApp.ready(n)` / `readyAll()` | ボード `n` を落として `AutonaActions.EnsureBoard(n, panel=True)` / `MakeBoards(panel=True)` で作り直す（デスクトップの起動と同じ書き込み・held）。国のデータも読む |
| `autonaApp.dropAllBoards()` | ボードを全部落とす（held 登録の解除を `Unset` より先に行う。逆だと削除が delta にも `./out` にも出ない。pin: `tests/test_harness_isolation.py`） |
| `autonaApp.boardsNow()` | 今の 3 ボードの state |
| `autonaApp.settle()` | export スレッドと画像ダウンロードのスレッドを待つ（DL スレッドを起こすのは export スレッドなので、この順でないと取りこぼす。待ち手は `StateManager.JoinDownloads()`）。queued のスロットも走らせる |

- 使うボードは全部 `ready` する（前のテストのボードを読まないため）
- 選手の読込は `load_player(board, team, data, slot=1)`（core の `PlayerService.ApplyData`。DB とのマージなし）、セットの読込は `apply_set(board, data)`、タグ欄の手入力の再現は `edit_tag(path, text)`
- `deleteLater()` した C++ オブジェクトは、pytest が `exec()` を回さない（イベントループ階層が 0 のまま）ため `processEvents()` では回収されない。回収は `QCoreApplication.sendPostedEvents(obj, QEvent.Type.DeferredDelete)` で**対象を指定して**行う。receiver を `None`（全オブジェクト）にすると access violation で落ちる

### ゲームのキャラを使うテスト

ハーネスの `user_data/games` は空で、カタログにキャラが無いと「どのスロットに誰が入ったか」の assert が全て自明に通って空振りする。

- `autonaApp.loadCharacters(names=("Mario","Luigi","Peach"), skins=3)` が最小限の実ゲームパック（`harness_game`）を `user_data/games` に書き、本番と同じ `GameAssetCatalog.ScanGames()` / `SelectGame()` で読む。テスト後は `dropAllBoards()` がパックを削除して `ResetGameState()` で空に戻す
- 選択の後の処理（ロスター・commit・ゲームの保存・ロードの通知）は走らせない。ロードの通知に反応するものを検査するテストは、`loadCharacters()` の後に `publishGameLoad()` を呼ぶ（ロードの保存サイクルを開き、ボードホストのロックの下で `GameLoader.instance` の `_Publish` を呼んでからサイクルを閉じる。本物と同じ順）

### シグナルの接続

プロセス寿命のシグナルへ `lambda` で接続しない。lambda は receiver を持たないので C++ 破棄時に切断されず、破棄された object がゴーストとして state を書き戻す。bound method なら Qt が自動で切断する。pin: `tests/test_signal_lifetime.py`（AST による静的検査と、破棄後の接続数が基準に戻ることの検証。アプリ寿命と同じ receiver だけを理由付きの許可リストに置く）。

## L6: ブラウザ E2E

`cd web && npm run build && npm run test:e2e`。Windows 専用で、Python の依存（`dependencies/requirements.txt` + pytest）が要る。初回は `npx playwright install chromium`。

- global setup（`web/e2e/global-setup.ts`）がヘルパー `web/e2e/headless_server.py` を起動する。ヘルパーは L4 のスモークと同じ隔離（tmp の作業ディレクトリ・空きポート・`AUTONA_INSTANCE_TAG`）でヘッドレスを起動するので、開発者の `user_data/` `out/` `logs/` と起動中の Autona には触れない。停止は Ctrl+Break（起動した PID だけ）
- jest（`src/` だけ）と pytest（`tests/test_*.py` だけ）には拾われない
- CI は `ci.yml` の `e2e` ジョブ（windows-latest）。失敗時は HTML レポートと trace・ヘッドレスのコンソールログを artifact に上げる

## L4: 起動スモーク

`main.py` は単一インスタンスのミューテックスを持つので、実機で Autona が起動中だとテストが素通りする。L4 は `main.py` を使わず、サブプロセスで `QApplication` → `bootstrap_core()` → `bootstrap_gui()` → `Autona.Window()`（ヘッドレスは `bootstrap_core()` まで）を使い捨て cwd で組み立て、`STARTUP phase=` ログ・`out/program_state.json` の妥当性・指定ポートでの HTTP 応答・ログに `ERROR` / 未捕捉例外が無いことを検証する。

## L-live

`AUTONA_LIVE_TESTS=1` で Start.gg の実大会を実 API で読む（大会データ取得・全セットのパース・`GetMatch` → `ChangeSetData` → `score.*` の E2E・再起動キャッシュ・アバター画像の DL・サムネイル生成）。実データの仕様: `event.endAt` は未終了時にキー自体が返らない / 一覧クエリは `bestOf` をリクエストしていないので常に `None` / オフライン大会では `GetMatch` が `bestOf` を意図的に `None` に落とす。

## 運用ルール

1. バグ修正 PR にはリグレッションテストを添える。書けない修正は理由を PR 本文に書く
2. `docs/development/manual-test-checklist.md` の項目は、自動化できたら削除する（二重管理しない）
3. L2 のハーネスが壊れたら実装側を直す。テスト側を緩めない（`tests/test_import_side_effects.py` と同じ運用）
4. カバレッジの数値目標は置かない。基準は「バグが出た箇所には必ずテストがある」
