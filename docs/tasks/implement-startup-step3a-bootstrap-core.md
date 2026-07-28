# implement: 起動再設計 Step 3a — bootstrap.py 新設 + StateManager / AutonaHotkeys 移行

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §1.2 Stage 2・§2 Step 3 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) D2/D3

## ゴール

Step 3（import 副作用の全廃）の最初の PR。**1 PR = 1〜2 シングルトン**の粒度に従い、
composition root（`src/bootstrap.py`）を新設し、移行リスト先頭 2 件を移す:

1. **StateManager** — モジュール末尾の static 初期化（`./out` 作成 + `SaveState`/`LoadState`、
   [src/StateManager.py](../../src/StateManager.py):667-673）を明示 `Initialize()` へ
2. **AutonaHotkeys** — モジュールレベルの `AutonaHotkeys.instance = AutonaHotkeys()`
   （[src/AutonaHotkeys.py](../../src/AutonaHotkeys.py):186）を bootstrap での生成へ

**挙動不変の移行**である。生成順・生成タイミングは現行の実効順序を固定する。

## 実装内容

### 1. `src/bootstrap.py` 新設（composition root）

- `bootstrap_core()` 関数: 再入ガード付き（2 回目以降は no-op）。中身は順に:
  1. `from .StateManager import StateManager` → `StateManager.Initialize()`
  2. `from .AutonaHotkeys import AutonaHotkeys` → `AutonaHotkeys.instance = AutonaHotkeys()`
     （`instance is None` の場合のみ）
- モジュール docstring に「シングルトン生成順はこのファイルが正」であることと、
  順序制約は生成呼び出しの直前コメントに書くルールを英語で明記
- import は**関数内**で行う（bootstrap.py 自体の import が副作用連鎖を引かないように）
- スプラッシュ進捗通知は本 PR ではスコープ外（bootstrap が Stage 2 全体になった時点で追加）

### 2. `src/StateManager.py` — static 初期化の明示化

- 末尾 667-673 行（`os.makedirs("./out")` / 初回 `SaveState()` / `LoadState()`）を削除し、
  `Initialize()`（static メソッド、既存の `LoadState` 等と同じ流儀）に移す
- `_initialized` フラグで冪等化。2 回目以降は no-op
- 既存メソッド・シグナル・ロック等は一切変更しない

### 3. `src/AutonaHotkeys.py` — インスタンス生成の除去

- 末尾 186 行の `AutonaHotkeys.instance = AutonaHotkeys()` を削除
- クラス属性 `signals = AutonaHotkeysSignals()`（クラス定義時の QObject 生成）は**維持**する。
  ウィジェット群が `AutonaHotkeys.signals` をクラス属性として参照しており、
  plain QObject は QApplication なしで生成可能なため副作用ゼロ検査に抵触しない

### 4. `src/Autona.py` — bootstrap 呼び出しの挿入

- 一括 import ブロック（152 行 `from .Settings.AutonaSettingsWindow import ...` の直前）に:
  ```python
  from .bootstrap import bootstrap_core
  bootstrap_core()
  ```
- 挿入位置の根拠を英語コメントで明記: 現行では StateManager の import 時初期化が
  152 行の import 連鎖内で走り、AutonaHotkeys.instance が 158 行で生成される。
  その直前で明示実行することで実効初期化順を変えない
- 後続の import 時シングルトン（GameAssetManager / TournamentDataProvider /
  ScoreboardManager 等）は `__init__` で StateManager を参照するため、
  bootstrap がブロックより**前**にあることが順序制約

### 5. 副作用ゼロ検査の自動テスト新設（`tests/test_import_side_effects.py`）

設計書 Step 3 検証欄の「副作用ゼロ検査」。subprocess 方式:

- 親テストは `subprocess.run([sys.executable, "-c", <検査スクリプト>], cwd=<一時dir>)` で実行
- 検査スクリプト側: `types.ModuleType("src")` + `__path__ = [SRC_ROOT]` を `sys.modules` に
  登録して **`src/__init__.py` を実行せずに** `importlib.import_module("src.StateManager")` /
  `("src.AutonaHotkeys")` を import（`direct_import()` は相対 import を解決できないため不可）
- assert 内容:
  - `QtWidgets.QApplication.instance() is None`（import が QApplication を生成しない）
  - `AutonaHotkeys.instance is None`
  - StateManager: `Initialize()` 未呼び出しでは一時 cwd に `./out` が**作られない**こと
- 一時 cwd には `user_data/` を事前作成すること（未移行の SettingsManager が import 時に
  `user_data/settings.json` を読み書きするため。この既知の残副作用はテスト内コメントで明記）
- 既知の残副作用（SettingsManager / AutonaHotkeys の class-level QObject）を理由に
  テストを緩めすぎない。「QApplication 生成なし」「instance None」「./out なし」は厳格に

## 制約・地雷（必読）

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) 全項目。特に:
  - #1: テストで `import src` / `from src...` の通常 import をしない（上記 fake-package 方式）
  - #2: 生成順を変えない（本 PR は StateManager → AutonaHotkeys のみ・現行順固定）
  - #12: 編集後は `.pyc` 残留に注意
- PySide6 のみ（qtpy 経由）。コード内コメントは英語
- PyInstaller の依存解析 subprocess は `import src` で Autona.py 連鎖を実行する
  （main.py なし・QApplication フォールバックあり）。bootstrap_core() はその経路でも
  走るが、現行の import 副作用と同等なので追加リスクなし（変更しないこと）
- `src/thumbnail/main_generate_thumbnail.py` / `src/result/main_generate_result.py` は
  in-process import（標準 import 連鎖内）であり、standalone 実行経路はない — 対応不要
- **アプリを起動しての動作確認は行わない**（ユーザーが実施）。構文チェック + pytest まで

## 検証（Claude 実施分）

- [ ] 変更・新規ファイル全てで `python -m py_compile` 通過
- [ ] `pytest tests/test_import_side_effects.py` 通過
- [ ] 既存テストスイート（`pytest tests/`）に回帰なし

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → スコアボード操作・ホットキー動作・`out/program_state.json` 更新
- [ ] manual-test-checklist のうち StateManager / ホットキーに関わる項目

## スコープ外（後続 PR）

- SettingsManager の import 副作用（`LoadSettings()`、設計書の移行リストに追加要）
- GameAssetManager 以降のシングルトン移行
- `src/__init__.py` の連鎖 import 廃止（Step 3 最終 PR）
