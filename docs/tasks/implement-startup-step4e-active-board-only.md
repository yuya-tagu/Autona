# implement: 起動再設計 Step 4e — アクティブ battle_format の board のみ起動時構築

作成: 2026-07-30 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 4-6（2026-07-28 PdM 決定）／ 先行: 4a〜4c・4b-2（PR #156-159）

## ゴール

`AutonaScoreboardManager.CreateScoreboard()`（[src/AutonaScoreboardManager.py](../../src/AutonaScoreboardManager.py):44-70）は
現在 board1（1v1）+ board2（team）+ board3（crew）の 3 面を常に構築する。
4b-2 後の実測で CreateScoreboard → core-shown が 〜4.2s の最大単一項。
これを**アクティブな battle_format の board 1 面のみ**の構築に変え、
非アクティブ board は**初回に必要になった時点**（形式切替・外部 API アクセス）で構築する。
削減見込み 〜2〜3s。

## 実装方式

### 1. AutonaScoreboardManager のファクトリ化

- 板ごとの構築を `EnsureScoreboard(number)`（idempotent・GUI スレッド前提）に切り出す。
  中身は現行 `CreateScoreboard` の該当ブロックをそのまま（`AutonaScoreboardWidget(1, singlePlayer=True)` /
  `AutonaScoreboardWidget(2, singlePlayer=False)` / `AutonaCrewBattleWidget(3)`、
  layout への addWidget、`self.scoreboards[n]` 登録、生成直後は hidden）。
  `SaveBlock` で囲む点も維持
- `CreateScoreboard()` は「アクティブ format の board 1 面を Ensure」に変更
  （format→番号の対応は既存 `SetBattleFormat` の `{"singles":1,"team":2,"crew":3}` と同一。
  アクティブ format は呼び出し元 `Window.__init__`:728 付近が `_resolveBattleFormat()` で
  解決して渡す形でも、Manager 側で SettingsManager を読む形でもよい。既存の呼び出し
  シーケンス（CreateScoreboard → SetBattleFormat）との整合を崩さないこと）
- `SetBattleFormat(fmt)` は表示切替の前に対象 board を `EnsureScoreboard` する
- `GetScoreboard(number)` は**未構築なら ensure 構築してから返す**方針に変更する。
  Web API（Flask スレッド）からも呼ばれるため、GUI スレッド外からの呼び出しは
  `GuiExecutor`（[src/Helpers/AutonaQtHelper.py](../../src/Helpers/AutonaQtHelper.py)）等で
  GUI スレッドへ同期ディスパッチして構築する（4b-2 の
  `WebServerActions._ensure_panel` と同じ考え方。二重構築ガード必須）。
  構築失敗時のみ現行どおり error ログ + None
  - これにより WebServerActions 約 40 箇所・AutonaApiV1・
    AutonaPlayerBrowserWidget:484・AutonaStatsWidget:186 の呼び出し元は**無変更**で済む

### 2. 未構築 board の state スケルトン先行シード

- board の `__init__`（[src/AutonaScoreboardWidget.py](../../src/AutonaScoreboardWidget.py):105-112）は
  `score.{N}` = {} + `last_sets.1/2` + `history_sets.1/2` + `station_queue` をシードする。
  未構築 board ではこれが走らず `score.2` / `score.3` キーが state から消えるため、
  **同じスケルトンを CreateScoreboard 時（SaveBlock 内）に未構築番号へも先行シード**し、
  Web UI / OBS オーバーレイが「キー不在」を踏まないようにする
- board が後から構築されるとき `__init__` が同じキーを再シードする（`score.N` = {} で
  上書き→ウィジェット既定値を書き込み）。これは現行の起動時と同一の書き込み列で
  あることを確認する

### 3. 触らないもの

- 各 board 内部（AutonaScoreboardWidget / AutonaCrewBattleWidget）の実装
- ホットキー配線（各 board が自分の `__init__` で connect する現行パターンのまま。
  未構築 board は接続されないが、score は board 別・非アクティブ board は不可視なので
  挙動同等。下記「実装前確認」で前提を検証すること）
- `scoreboard.battle_format` の永続化・`Window.SetBattleFormat` の
  resultSetting / thumbnailSetting / sidebar 連動

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] ホットキーの現行挙動: 複数 board が同じシグナル（team1_score_up 等）に connect して
      いる状態で、ハンドラに可視ガードがあるか。**無い場合（= 現状は不可視 board の score も
      同時に動く）**、lazy 化で「未構築 board はホットキーの影響を受けない」へ挙動が変わる。
      score が board 別である以上、実害の有無を評価して報告（実害ありなら停止）
- [ ] `AutonaScoreboardManager.instance.scoreboard` / `.teamScoreboard` / `.crewScoreboard`
      属性を直接読む箇所を全列挙し、未構築 None で壊れる箇所が無いこと
- [ ] `AutonaCrewBattleWidget.__init__` の副作用（state シード・シグナル接続）を棚卸しし、
      本チケットのスケルトンシードでカバーできない state キーが無いこと
- [ ] web/src（**読み取りのみ・変更禁止**）: `/team`（board 2）・crew 表示が
      `score.2` / `score.3` = スケルトンのみ（team/player 不在）のときに例外なく
      レンダリングできるか。null 耐性が無い場合は、その旨と「スケルトンに追加で
      必要な最小キー」を添えて報告（Python 側のシード追加で対処する。web/src は触らない）
- [ ] `GetScoreboard` の ensure 化で、Flask スレッド → GUI 同期ディスパッチ中に
      GUI スレッドが `GetScoreboard` を呼ぶ再入・デッドロック経路が無いこと
      （GuiExecutor の実装を読んで確認）

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（#3 キーパス・
  #4 objectName・#6 SaveBlock 対称性・#9 processEvents・#12 pycache）
- **触ってはいけないファイル**: `web/src/**`（読み取りは可）
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止
- STATE_VERSION は変更しない（ナビ構造に変更なし）

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/ -q` 回帰なし
- [ ] grep: `AutonaScoreboardWidget(` / `AutonaCrewBattleWidget(` の生成が
      `EnsureScoreboard` 経由のみになっていること

## 検証（ユーザー実施分・報告時に提示）

- [ ] STARTUP ログ: CreateScoreboard 区間の短縮（ログに「1 面のみ構築」相当が出る）
- [ ] battle_format 切替 1v1 → team → crew → 1v1 の一巡: 各 board が初回切替時に構築され、
      スコア・選手情報が形式ごとに保持される
- [ ] 未構築 board への Web API（例: 1v1 起動中に `/scoreboard2-*` 系）→ その場で構築され従来通り動作
- [ ] Web UI `/team` を board 2 未構築のまま開く → エラーなく表示（空状態）
- [ ] ホットキー（Ctrl+Shift+1 等）がアクティブ board に従来通り効く
- [ ] セット読み込み・選手読み込み・スコア操作の一連が従来通り

## スコープ外

- 4d（選手 DB ワーカー化）・eager 残存パネル（Thumbnail / Result / Stats / PlayerBrowser 等）の
  追加 lazy 化・Step 5（ドキュメント追従）
