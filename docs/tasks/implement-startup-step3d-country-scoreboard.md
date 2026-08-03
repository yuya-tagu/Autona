# implement: 起動再設計 Step 3d — CountryHelper / ScoreboardManager の bootstrap 移行

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 3 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) D2/D3 ／ 先行: Step 3a（PR #147）/ 3b（PR #149）/ 3c（PR #151）

## ゴール

移行リスト 7〜8 番目を `src/bootstrap.py` へ移す。**挙動不変の移行**（生成順は現行の実効順
= Autona.py の import 順 173→175 を固定）。

1. **AutonaCountryHelper** — [src/Helpers/AutonaCountryHelper.py](../../src/Helpers/AutonaCountryHelper.py):227 の
   `instance` 生成を廃止（`__init__` は空。重い `LoadCountries()` は `init()` 側で従来通り）
2. **AutonaScoreboardManager** — [src/AutonaScoreboardManager.py](../../src/AutonaScoreboardManager.py):89 の
   `instance` 生成を廃止

## 重要な特性（3a〜3c との違い）

- **ScoreboardManager は QDockWidget（QWidget 系）**。生成には QApplication が必須。
  bootstrap_core() 実行時点で QApplication は存在する（main.py が `import src` 前に生成。
  PyInstaller 解析 subprocess でも Autona.py:48-50 のフォールバックが bootstrap より前）
  — この前提を bootstrap のコメントに英語で明記する
- `__init__` は `StateManager.Unset("score")` を呼ぶ → StateManager 初期化後であること（既存順で満たされる）
- board 群の構築は `CreateScoreboard()`（Window 側から呼ばれる）であり `__init__` では走らない

## 実装内容

### 1. `src/bootstrap.py` — bootstrap_core() に 2 ステップ追加

- Updater の**後**に CountryHelper → ScoreboardManager の順で追加（`instance is None` ガード付き・既存パターン踏襲）
- ScoreboardManager の直前コメントに「QWidget 系につき QApplication 必須」の前提を明記

### 2. 両モジュールの末尾副作用を削除

### 3. `tests/test_import_side_effects.py` の対象拡張

- 検査対象に `src.Helpers.AutonaCountryHelper` / `src.AutonaScoreboardManager` を追加
- assert: 両 `instance is None` + 既存 assert 維持
- **ScoreboardManager のポジティブコントロール（実生成）は行わない**（QWidget は QApplication
  なしで生成すると abort する。import 副作用ゼロの検査のみ）
- ScoreboardManager の import 連鎖（AutonaScoreboardWidget / AutonaCrewBattleWidget と
  その先）が一時 cwd で読む追加ファイルがあれば `_SEED_FILES` を拡張する

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] 両 `__init__` が未移行シングルトン（AutonaWebServer）や Window の `instance` を参照していないか
- [ ] **ScoreboardManager の import 連鎖全体**（AutonaScoreboardWidget / AutonaCrewBattleWidget /
      それらが import する全モジュール）に、import 時評価位置での (a) 未移行/移行済みシングルトン
      `instance` 参照、(b) QWidget 生成、(c) 例外を投げうるファイル I/O が無いか（AST 走査推奨。
      Step 3b の PlayerDB:355 と同型の地雷検出）
- [ ] `src/` 全域に import 時評価位置で `AutonaCountryHelper.instance` /
      `AutonaScoreboardManager.instance` を参照する箇所が無いか

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) 全項目（特に #1 / #12 / #13）
- **触ってはいけないファイル**: `web/src/ScoreboardPage/CurrentSet.jsx`、`web/src/ScoreboardPage/Player.jsx`、
  `.claude/git-workflow.md`（別件の未コミット変更）
- コード内コメントは英語。PySide6（qtpy 経由）のみ
- アプリ起動禁止（検証はユーザー実施）。py_compile + pytest まで。コミット禁止

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/test_import_side_effects.py -v` 通過
- [ ] `pytest tests/ -q` 回帰なし
- [ ] AST 走査で import 時評価位置からの両 instance 参照 0 件

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → スコアボード表示・スコア操作・battle_format 切替（1v1 → team → crew → 1v1）
- [ ] 選手編集で国・州の選択肢が出る（CountryHelper）

## スコープ外（後続 PR）

- AutonaWebServer（クラス本体の Flask/SocketIO 構築 → インスタンス化時へ移す大物）
- SettingsManager / AutonaLocaleHelper / StartGGDataProvider の残副作用
- `src/__init__.py` の連鎖 import 廃止（Step 3 最終 PR）
