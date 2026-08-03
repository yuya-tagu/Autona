# implement: 起動再設計 Step 4b-3 — 残存 eager パネル 5 枚の遅延構築

作成: 2026-07-30 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 4-2 ／ 先行: 4b-2（PR #159）・4e（PR #160）／ 調査: 2026-07-30 実施（6 パネル台帳。要点は本チケットに反映済み）

## ゴール

eager 残存パネル（実測合計 〜2.9s）のうち、調査で低リスクと判定した 5 枚を
4b-2 のファクトリ構造で初回表示時構築へ移す:

1. **AutonaResultSettingsWidget**（最重量: editable + QCompleter 付き char combo 176 個
   + color combo 128 個を生成し、起動シーケンス末尾の `ReloadGames()` → `onLoad` で
   プロキシモデルを**もう 1 周**作り直している）
2. **AutonaThumbnailSettingsWidget**（.ui 39 widget）
3. **AutonaStreamSettingsWidget**（.ui 30 widget + PIL import も遅延化される）
4. **AutonaStatsWidget（AutonaAnalysisWidget 内包）**
5. **AutonaPlayerBrowserWidget**

**AutonaTournamentInfoWidget（page 0）は eager のまま据え置く**。
`tournamentInfo.*` の唯一の書き手で provider 側にリプレイ手段が無く、
`__init__` が空文字 `editingFinished.emit()` で state を上書きするため、
遅延構築は既存データを破壊する。lazy 化するならヘッドレスサービス抽出が前提
（別タスク。本チケットのスコープ外）。

## 実装方式（調査済みの作業リスト）

### 共通基盤（[src/Autona.py](../../src/Autona.py)）

1. `_navSections` の 5 エントリを `(self._CreateXxxPanel, 'icon.svg', ラベル, "キー")` に
   置換し、eager 生成ブロック（:355-433 のうち該当 5 枚）を各 `_CreateXxxPanel()`
   ファクトリへ移す（setObjectName / setWindowTitle / setWindowIcon は従来値のまま）
2. QSettings 復元ブロック（:754-761）の直後に
   `self._EnsurePanelAt(self.stack.currentIndex())` を追加する。
   **理由**: `currentChanged` は index が変わらないと emit されないため、
   復元先が現在 index と同じ lazy ページだと構築されず空白のままになる
   （page 0 は eager 据え置きだが、防御として必ず入れる）
3. STATE_VERSION はインクリメントしない（ナビの枚数・並び順不変）
4. PlayerBrowser のナビラベル: 現在 `playerBrowser.windowTitle()` を実行時に読んでいる
   唯一のパネル。`QApplication.translate("app", "Player List")` の明示に置換する
   （widget 側 :99 の setWindowTitle と同一文字列であることを確認）

### `_SeedPanelState()` への移設（パネル未構築でも起動時に必要）

5. Stream: `stream.orgIconVersion` / `stream.orgThumbVersion` の mtime publish
   （`AutonaStreamSettingsWidget._publishAssetVersion` は staticmethod なのでそのまま呼べる）
   と、旧キー `tournamentInfo.streamTitleEdit` / `tournamentInfo.streamUrl` の Unset
6. PlayerBrowser: 廃止キー掃除の `StateManager.Unset("player_list")` /
   `Unset(_EDITOR_PATH_BASE)`（定数は import するか値を確認して移す）
7. 移設した処理はパネル側 `__init__` から削除し、移設先を英語コメントで明記
   （4b-2 の既存 NOTE コメントのスタイルに合わせる）

### battle_format catch-up（Thumbnail / Result）

8. `_CreateThumbnailPanel()` / `_CreateResultPanel()` 内で
   `panel.SetBattleFormat(self._resolveBattleFormat())` を呼ぶ。
   Result は `SetBattleFormat` が layoutCombo 経由で `_onLayoutChanged` を撃つため、
   プレビュー生成が二重に走らない呼び出し順にし、順序の理由をコメントに書く
9. 起動時の `self.resultSetting.SetBattleFormat(battleFormat)` /
   `self.thumbnailSetting.SetBattleFormat(battleFormat)`（:743-744）を削除（8 が代替）
10. `self.thumbnailSetting = None` / `self.resultSetting = None` を `__init__` 冒頭で
    初期化し、ファクトリ内で代入する。`Window.SetBattleFormat`（:1435-1438）の
    getattr ガードは None 値でも正しく skip することを確認（未構築時は skip →
    構築時に 8 の catch-up が現在 format を適用、で一貫する）

### Stats / PlayerBrowser

11. `AutonaStatsWidget` に `showEvent` を追加し `_updateDisplay()` を呼ぶ
    （現状 `__init__` から呼ばれておらず、構築直後に一度も描画されない穴がある。
    lazy 化と同時に塞ぐ）。Analysis はファクトリ内で先に生成して
    `AutonaStatsWidget(analysisPanel=...)` に渡す（従来と同じ）
12. `Window.statsWidget` / `Window.analysisWidget` 属性は外部参照が無いため、
    ファクトリ内ローカルで完結させてよい（残す場合は None 初期化 + ファクトリ代入）

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] 5 パネルそれぞれの `__init__` 副作用を再列挙し、上記台帳（state 書き込みは
      Stream の 4 件 + PlayerBrowser の Unset 2 件のみ・他 3 枚はゼロ）と差分が
      あれば報告
- [ ] Result の後発構築で `result_players` が「初回表示まで前回値のまま残る」ことに
      なる（現状は毎起動で空リセット）。`result_players` の消費者が
      main_generate_result / `_buildResultPostText` のみ（オーバーレイ・Web UI・API に
      読み手なし）であることを再確認
- [ ] `_RefreshStreamTitle` が構築時に現在 state + `provider.top_entrants` から
      再計算すること（= 取り逃した entrants_updated からの自己回復）を確認
- [ ] 5 枚の外部参照が `Window.thumbnailSetting` / `resultSetting` の
      SetBattleFormat 経路のみ（statsWidget / analysisWidget / playerBrowser は
      Autona.py 内のみ）であることを grep で再確認

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)
  （#4 objectName・#9 processEvents・#12 pycache・#13 モデル差し替え —
  特に Result の NoIconProxyModel 大量再バインド周りに触れる場合）
- **触ってはいけないファイル**: `web/src/**`（読み取りは可）
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止
- objectName / setWindowTitle / ナビラベルの文字列を一字も変えない

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/ -q` 回帰なし
- [ ] grep: 5 パネルの生成がファクトリ経由のみ・`self.statsWidget` 等の旧 eager 参照の
      残存ゼロ

## 検証（ユーザー実施分・報告時に提示）

- [ ] STARTUP ログ: window-init → core-shown の短縮（目標 〜2〜2.5s 減）と
      lazy 構築ログが起動時に出ないこと（前回タブが対象パネルの場合は 1 枚のみ）
- [ ] 5 パネルを開かずに: OBS オーバーレイ（thumbnail / result / scoreboard_topleft）と
      Web UI に大会名・ロゴ・配信先が正しく出る / `stream.*` が前回値を保持
- [ ] battle_format を切り替えてから Result / Thumbnail を初めて開く →
      レイアウト・チーム名トグルが現在の形式に一致
- [ ] Stats / PlayerBrowser 初回表示でデータが表示される（空のままにならない）
- [ ] 各パネル 2 回目以降の表示で入力値・選択が保持される
- [ ] lazy ページを選択した状態で終了 → 再起動でそのページが構築・表示される

## スコープ外

- TournamentInfo の lazy 化（ヘッドレスサービス抽出が前提。別タスク）
- 4d（選手 DB ワーカー化）・Step 5（ドキュメント追従）
