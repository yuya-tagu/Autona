# implement: 起動再設計 Step 4b-2 — サイドバーのファクトリ構造 + プレビュー 4 パネルの遅延構築

作成: 2026-07-30 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 4-2 ／ 先行: 4a（PR #156）/ 4b-1（PR #157）/ 4c（PR #158）／ 調査: 2026-07-30 パネル台帳・外部参照マップ（本チケットに反映済み）

## ゴール

`Window.__init__` で eager 構築しているサイドバーパネルのうち、WebEngine プレビューを持つ
**Board / Top8 / Commentary / StatsPreview の 4 枚**を初回表示時構築へ移す。
4c 後の実測で window-init → CreateScoreboard の無音 〜7s の支配項がパネル構築と確定しており、
その大半がこの 4 枚（各 QWebEngineView + page load + フォーム構築）。

**eager のまま残す**: TournamentInfo / StreamSettings / Scoreboard / ThumbnailSettings /
ResultSettings / Statistics(+Analysis) / PlayerBrowser（Thumbnail / Result は Window が
`self.thumbnailSetting` / `self.resultSetting` を battle_format 切替で直接触るため。
レンダラは 4b-1 で遅延済み）。

## 実装方式

### 1. サイドバーのファクトリ構造（[src/Autona.py](../../src/Autona.py)）

- `_navSections` の各エントリを「構築済みウィジェット」または「ファクトリ」を持てる形にする。
  lazy パネルは stack に**軽量プレースホルダ QWidget** を先に積み、
  **nav index == stack page index の等式を維持**する（`navSelected → setCurrentIndex` 直結の
  現構造を壊さない）。ナビ項目の数・順序・ラベルは不変なので **STATE_VERSION は上げない**
- 初回にそのページが選択されたとき（`stack.currentChanged` 等）にファクトリを呼び、
  実パネルを構築 → **同じ index で** プレースホルダと差し替え（`removeWidget`/`insertWidget`）→
  以後は再構築しない（状態保持）。再入ガード必須
- ナビ項目ラベルは現在 `panel.windowTitle()` を使っている。lazy パネルは構築前に
  タイトルが取れないため、`_navSections` 側にラベル（翻訳済み文字列）を持たせる
- 現行の dockWidget 後処理ループ（minimumWidth / setTitleBarWidget / setFeatures、
  [src/Autona.py](../../src/Autona.py):485-497）を `_PreparePanel(dw)` ヘルパに抽出し、
  eager 分は従来通りループで、lazy 分は構築直後に適用する
- `setObjectName` / `setWindowTitle` / `setWindowIcon` もファクトリ内で従来値のまま設定する
  （objectName は StateManager キー・翻訳コンテキストに直結。**一字も変えない**）
- QSettings の `sidebarIndex_v{N}` 復元で lazy ページが初期選択になる場合は、
  その 1 枚だけ起動時に構築されてよい（挙動として自然。4c の readiness gate が
  プレビュー load を保護する）
- splash の進捗メッセージ（0.40 / 0.55 / 0.70 / 0.85）は構築が消えた分だけ再配分してよい
  （フェーズ名・既存 STARTUP 計測名は変更しない）

### 2. StateManager シードの移設（構築有無に関わらず起動時に必要）

以下 3 つは OBS オーバーレイ / Web UI が起動直後から読む state を整えるためのもの。
パネル `__init__` から**起動シーケンス側へ移設**する（英語コメントで移設元を明記）:

- Board: `tournamentInfo.themeColor` / `accentColor` シード
  （[src/AutonaBoardWidget.py](../../src/AutonaBoardWidget.py):156-163。SettingsManager 既定値から）
- Top8: `StateManager.Unset("bracket")` + `top8` の DefaultState / Recompute 正規化
  （[src/AutonaTop8Widget.py](../../src/AutonaTop8Widget.py):102-118。SaveBlock ごと）
- Commentary: `commentary` の `comms.NormalizeState` シード
  （[src/AutonaCommentaryWidget.py](../../src/AutonaCommentaryWidget.py):76-79。SaveBlock ごと）

移設先は `src/bootstrap.py` の `bootstrap_core()` 末尾（StateManager 初期化後）を第一候補と
するが、**シードは `StateManager.LoadState()`（program_state.json 復元）より後に走る必要がある**。
LoadState の実行タイミングを確認し、bootstrap 内で順序が保証できない場合は
`Window.__init__` 冒頭（パネル構築前）に置く。どちらにしても 1 箇所に集約し、
パネル側の `__init__` からは削除する（パネルはシード済み state を読むだけにする）。
正規化ロジック自体（`top8.DefaultState` / `comms.NormalizeState` 等）は移動しない。

### 3. 外部参照の null 安全化・接続の付け替え

- **Commentary ↔ WebServerActions**: 現在はコンストラクタ注入
  （[src/AutonaWebServerActions.py](../../src/AutonaWebServerActions.py):34、
  `set_commentary_data`:222 / `load_commentator_from_tag`:518 が
  `self.commentaryWidget.*Signal.emit`）。注入をやめ、
  **`AutonaCommentaryWidget.instance` の遅延解決**に変える。パネル未構築で API が来た場合は
  GUI スレッドでパネルを ensure 構築してから emit する（`gui_thread_sync` 経由。
  Window に lazy 構築を頼む経路が必要なら、ファクトリ差し替え関数を Window から
  Actions へ渡すのではなく、`update_bracket`:445 と同様の instance 参照 + ensure で統一）。
  `WebServer.__init__` / `_StartWebServer` の `commentaryWidget=` 引数は削除する
  （[src/Autona.py](../../src/Autona.py):881、[src/AutonaWebServer.py](../../src/AutonaWebServer.py):703-716）
- **Top8 ホットキー**: `AutonaHotkeys.signals.refresh_phase_group` の connect が
  Top8 `__init__`:197 にあるため、未構築だと Ctrl+Shift+P が無反応になる。
  接続点を Window 側へ 1 本化（ensure 構築 → `RefreshPhaseGroup()` のハンドラ）し、
  Top8 `__init__` の connect は削除する（二重発火防止）
- **Web API `/bracket:refresh`**（`update_bracket`:438-452）は既に
  `AutonaTop8Widget.instance is None` フォールバックを持つ。ensure 構築に寄せるか
  現状維持かは挙動が変わらない方を選び、選択理由をコメントに残す
- `self.dockWidgets` / `_navSections` を走査する他の箇所（retranslate・クローズ処理等）を
  grep で全列挙し、lazy 未構築でも壊れないことを確認する

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] `StateManager.LoadState()` の呼び出しタイミングを特定し、シード移設先が
      「LoadState より後・パネル構築より前」を満たすこと（満たせない場合は案を添えて停止）
- [ ] 4 パネルそれぞれの `__init__` の副作用を再列挙（シグナル接続・instance 代入・
      state 書き込み・ファイル/ネットワーク I/O）し、本チケットの台帳と差分があれば報告
- [ ] `AutonaCommentaryWidget.instance` / `AutonaTop8Widget.instance` を読む全箇所を列挙し、
      未構築 None で壊れる箇所が上記以外に無いこと
- [ ] GameChanged / tournament_changed 等のグローバルシグナルを 4 パネルが `__init__` で
      connect している場合、初回表示時構築でも表示時点の state から自己回復すること
      （購読漏らしたイベントのデータ欠落が残るなら対応案を添えて報告）

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（#4 objectName・
  #9 processEvents / シグナル内モーダル・#10 scene clear・#12 pycache・#13 モデル差し替え）
- [.claude/rules/python-widgets.md](../../.claude/rules/python-widgets.md)（STATE_VERSION の
  増分条件 = ナビの追加・削除・並び替え。**今回は該当しない**）
- **触ってはいけないファイル**: `web/src/**`（読み取りは可）
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/ -q` 回帰なし（test_webserver_routes / test_import_side_effects 含む）
- [ ] grep: 4 パネルの起動時構築（`AutonaBoardWidget()` 等）が `Window.__init__` 直下から消え、
      ファクトリ経由のみになっていること

## 検証（ユーザー実施分・報告時に提示）

- [ ] STARTUP ログ: window-init → core-shown の短縮（目標 〜7s 減）
- [ ] 起動直後、4 パネルを開かずに: スコア操作 / OBS オーバーレイの theme・accent 色反映 /
      Web UI 取得 / Ctrl+Shift+P（Top8 が自動構築されリロード）/
      commentary API（Companion 等から）→ すべてエラーなし
- [ ] 各パネル初回表示: 内容・操作性が従来通り（プレビュー描画・フォーム値の state 復元）
- [ ] 2 回目以降の表示で再構築されない（入力値・選択が保持される）
- [ ] 前回終了時に lazy パネルを選択していた状態で再起動 → そのパネルが表示され正常動作
- [ ] battle_format 切替（1v1 → team → crew）が従来通り

## スコープ外

- 4d（選手 DB ワーカー化）/ 4e（アクティブ battle_format の board のみ構築）
- 表示後アイドルでのプリウォーム（体感悪化時の追加案として保留）
