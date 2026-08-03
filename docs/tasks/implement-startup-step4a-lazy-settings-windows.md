# implement: 起動再設計 Step 4a — 設定系 5 ウィンドウの lazy 化

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §1.2 Stage 4・§2 Step 4-1 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) ／ 先行: Step 3 完了（PR #147〜#155）

## ゴール

Step 4（遅延初期化）の第 1 弾。リスク最小の**設定系 5 ウィンドウ**を
「起動時に構築」から「初回アクセス時に構築」へ変える:

| self 属性 | クラス | 現生成位置（Autona.py） |
|-----------|--------|------------------------:|
| `settingsWindow` | AutonaSettingsWindow | 568 |
| `gameAssetGuideWindow` | AutonaGameAssetGuideWindow | 583 |
| `apiInfoWindow` | AutonaApiInfoWindow | 674 |
| `companionGuideWindow` | AutonaIntegrationGuideWindow | 683 |
| `obsGuideWindow` | AutonaObsGuideWindow | 691 |

**契約（設計書 §1.2 Stage 4）**: どのウィンドウも「任意のタイミングで初めて構築されても動く」。
2 回目以降の表示は再構築せず状態を保持する（従来と同じインスタンス再利用）。

## 実装方式

1. `Window.__init__` の 5 生成を `self._settingsWindow = None` 等の遅延スロットに置き換え、
   **lazy アクセサ**（例: `def _EnsureSettingsWindow(self) -> AutonaSettingsWindow`）を追加。
   初回呼び出しで構築 + 必要な初期化（従来 `__init__` 直後にしていた設定・connect があれば同じ順で）
2. 表示経路（メニューアクション等）の `self.settingsWindow.show()` 類を
   `self._EnsureSettingsWindow().show()` 類へ置換
3. 命名・実装パターンは 5 ウィンドウで統一する
4. **表示以外の参照経路を全て洗い出して同様に対応**（ホットキー・Web API・シグナル接続・
   `UiMounted` 連携等。特に `AutonaSettingsWindow` は AutonaHotkeys の `UiMounted()` 経路と
   関係する可能性があるため、生成タイミング変更で切れる connect が無いか精査）
5. import 文は**変えない**（モジュール import は Step 3 で副作用ゼロ化済み・ロード時間への
   寄与は小さい。本 PR は構築コストの遅延のみ）

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] 5 ウィンドウの `__init__` が「構築時に走ることに意味がある処理」
      （設定の読み込み反映・シグナル接続・タイマー開始等）を持つ場合、その処理が
      初回表示まで遅れても挙動が変わらないか。変わるものがあれば列挙して停止
- [ ] `src/AutonaApiInfoWindow.py` 内の自己参照以外に、Autona.py 外から
      5 属性（`settingsWindow` 等）へアクセスする箇所が無いか（grep + AST）
- [ ] `UiMounted()` シグナル/メソッドの発火経路に 5 ウィンドウが含まれていないか
      （含まれる場合、初回表示時に同等の初期化が走る設計にする）
- [ ] Web API（AutonaWebServerActions / AutonaApiV1）・ホットキーから 5 ウィンドウを
      参照する経路が無いか

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（#9 モーダル/シグナル・#12・#13）
- [.claude/rules/python-widgets.md](../../.claude/rules/python-widgets.md)（パネル/ウィンドウ規約）
- **触ってはいけないファイル**: `web/src/ScoreboardPage/CurrentSet.jsx`、
  `web/src/ScoreboardPage/Player.jsx`、`.claude/git-workflow.md`
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止
- STARTUP 計測ログ（autona_splash.log_startup_phase）は変更しない

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/ -q` 回帰なし
- [ ] grep: 旧属性名での直接アクセス残存ゼロ（アクセサ経由に統一）

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → 起動直後に 5 ウィンドウを**開かずに**通常操作（スコア・ホットキー・Web UI）
- [ ] 各ウィンドウをメニューから初回表示 → 内容・操作が従来通り（設定変更の反映含む）
- [ ] 各ウィンドウを閉じて再表示 → 状態が保持されている（再構築されていない）
- [ ] 設定ウィンドウ: ホットキー編集（キャプチャ・保存・反映）が従来通り

## スコープ外（後続 PR）

- WebEngine プレビュー系パネルの lazy 化（Step 4b）
- 選手 DB ワーカー化 / WebServer 表示後起動 / アクティブ board のみ構築（Step 4c〜）
