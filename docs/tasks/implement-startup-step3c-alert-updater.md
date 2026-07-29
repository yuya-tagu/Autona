# implement: 起動再設計 Step 3c — AlertNotification / Updater の bootstrap 移行

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 3 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) D2/D3 ／ 先行: [Step 3a](implement-startup-step3a-bootstrap-core.md)（PR #147）/ [Step 3b](implement-startup-step3b-gam-tdp.md)（PR #149）

## ゴール

移行リスト 5〜6 番目を `src/bootstrap.py` へ移す。**挙動不変の移行**（生成順は現行の実効順を固定）。

1. **AutonaAlertNotification** — モジュール末尾（[src/AutonaAlertNotification.py](../../src/AutonaAlertNotification.py):78-82）の
   `./user_data/alerts_red.json` 初期化 + `instance` 生成を廃止
2. **AutonaUpdater** — モジュール末尾（[src/AutonaUpdater.py](../../src/AutonaUpdater.py):902）の `instance` 生成を廃止

## 実装内容

### 1. `src/bootstrap.py` — bootstrap_core() に 2 ステップ追加

- 既存の TDP 生成の**後**に AlertNotification → Updater の順で追加（現行の Autona.py import 順
  167→168 行に一致）。いずれも `instance is None` ガード付き（既存パターン踏襲）
- 順序依存が無いことを確認済みの場合も、その旨を簡潔にコメントする（英語）

### 2. 両モジュールの末尾副作用を削除

- AlertNotification の `alerts_red.json` 初期化ガードは `__init__` 先頭へ移す
  （Step 3b の GAM `./user_data/games` と同じ「不変条件は依存するオブジェクト側に置く」パターン。
  `MarkNotificationRed()` がこのファイルの存在に依存）
- `CleanupUpdateLeftovers()` 等のモジュールレベル**関数定義**は無変更（明示呼び出しであり副作用ではない）

### 3. `tests/test_import_side_effects.py` の対象拡張

- 検査対象に `src.AutonaAlertNotification` / `src.AutonaUpdater` を追加
- assert 追加: 両 `instance is None`、一時 cwd に `./user_data/alerts_red.json` が**作られない**こと
- 既存 assert（QApplication 非生成 / `./out` / `./user_data/games` 非作成）は維持

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] 両 `__init__`（と、そこから呼ぶメソッド）が未移行シングルトン
      （AutonaCountryHelper / AutonaScoreboardManager / WebServer）の `instance` を参照していないか
- [ ] `src/` 全域に、モジュールレベル（import 時評価位置）で
      `AutonaAlertNotification.instance` / `AutonaUpdater.instance` を参照する箇所が無いか
      （Step 3b の PlayerDB:355 と同型の地雷の検出。AST 走査推奨）
- [ ] 両モジュールのモジュールレベルに、末尾の削除対象以外の副作用が無いか
      （AutonaUpdater は import が多いが定数のみのはず。`AutonaRemoteZip` 等の import 連鎖も確認）

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) 全項目（特に #1 / #12）
- **並行セッションの作業中ファイルに絶対に触れない**: `src/Settings/AutonaSettingsWindow.py`、
  `tests/test_hotkeys_enabled.py`、`web/src/ScoreboardPage/CurrentSet.jsx`、`web/src/ScoreboardPage/Player.jsx`、
  および `.claude/git-workflow.md`（別件の未コミット変更あり・コミット対象にも含めない）
- コード内コメントは英語。PySide6（qtpy 経由）のみ
- アプリ起動禁止（検証はユーザー実施）。py_compile + pytest まで
- コミット禁止（ユーザー指示があるまで）

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/test_import_side_effects.py -v` 通過
- [ ] `pytest tests/ -q` 回帰なし

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → 正常起動（AlertNotification は現在リモート取得無効のため起動可否のみ）
- [ ] メニューから「更新を確認」→ ダイアログ表示（AutonaUpdater。dev モードでは install 無効の説明が出る）

## スコープ外（後続 PR）

- AutonaCountryHelper / AutonaScoreboardManager / AutonaWebServer / SettingsManager /
  AutonaLocaleHelper / StartGGDataProvider の残副作用
- `src/__init__.py` の連鎖 import 廃止（Step 3 最終 PR）
