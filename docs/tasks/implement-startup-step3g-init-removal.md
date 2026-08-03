# implement: 起動再設計 Step 3g — src/__init__.py の連鎖 import 廃止（Step 3 最終）

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §1.2 Stage 2 末尾・§2 Step 3 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) D2 ／ 先行: Step 3a〜3f（PR #147/#149/#151/#152/#153/#154）

## ゴール

Step 3 の最終 PR。`src/__init__.py` の `from .Autona import Window, App` を廃止し、
**任意の src モジュールを import しても何も起きない**状態を完成させる。
agent-pitfalls #1（src import は全起動）を構造ごと消滅させ、テストの `direct_import()` 回避策を撤去する。

**設計判断（この方針で実装する）**: `src/Autona.py` は「アプリ組み立てモジュール」として、
import 時の logger 構成・`bootstrap_core()` 実行・Window 定義を維持する（import = 組み立て開始が仕様）。
副作用ゼロ原則の適用範囲は「Autona.py **以外**の全 src モジュール」であり、その旨を
`src/__init__.py` の docstring に明文化する。

## 実装内容

### 1. `src/__init__.py`

- `from .Autona import Window, App` を削除し、docstring のみに:
  - 「この package init は意図的に空。src.* の import は副作用ゼロが原則
    （唯一の例外はアプリ組み立てモジュール src/Autona.py で、その import が起動シーケンス）。
    シングルトン生成順は src/bootstrap.py が正」の趣旨を英語で

### 2. `main.py`

- `import src`（240 行）→ `from src import Autona`（重い import はここで走る。位置は変えない）
- `src.Window()` → `Autona.Window()` / `src.App` → `Autona.App`（258・261-262 行）
- 周辺コメント（17・26・45・162 行付近の「import src」への言及）を新しい実態に追従

### 3. テストの direct_import 撤去（13 ファイル）

- `src/__init__.py` が空になるため `from src.X import Y` の通常 import が安全になる。
  各テストを通常 import へ移行し、`tests/conftest.py` の `direct_import()` を削除
- **例外**: モジュールを「新品の状態」で import し直すこと自体が目的のテスト
  （`test_state_manager_held.py` の fresh-module ロード、`test_import_side_effects.py` の
  subprocess 検査）は目的に合う最小の仕組みを残してよい。ただし fake-package 細工が
  不要になった分は簡素化し、コメントを実態に更新する
- `test_import_side_effects.py` に **`import src` 自体が副作用ゼロ**である assert を追加
  （QApplication 非生成・ファイル非作成。__init__.py の再肥大化を恒久的に防ぐ回帰ガード）

### 4. `.claude/rules/agent-pitfalls.md` の #1 / #2 更新

- #1: 「src import は全起動を引き起こす」→ 解消済みであることと新原則
  （src.* は副作用ゼロ・例外は Autona.py・テストは通常 import でよい・direct_import は廃止）へ書き換え
- #2: 「シングルトン初期化順序」→ 順序の正本が `src/bootstrap.py` になったことへ書き換え
  （順序制約コメントの読み方・変更時の注意）
- 変更履歴として「2026-07-29 Step 3 完了により更新」を付記。他ドキュメント（Design.md 等）の
  追従は Step 5 のスコープなので触らない

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] `src.Window` / `src.App` / `from src import` を参照する箇所が main.py 以外に無いか
      （src/ 全域・tests/・scripts/・dependencies/autona.spec・.ps1）
- [ ] `src/result/__init__.py` / `src/thumbnail/__init__.py` 等サブパッケージの __init__ に
      副作用が無いか
- [ ] PyInstaller: autona.spec が `src` の import 構造に依存する記述（hiddenimports 等）を
      持っていないか。依存解析 subprocess は空 __init__ の import になるため、
      Autona.py:48-50 の QApplication フォールバックが不要化する可能性があるが、
      **フォールバックは削除しない**（exe ビルド検証は別途ユーザー承認時に実施するため、
      安全側に残す。コメントだけ実態に合わせて更新してよい）

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（#12。#1/#2 は本 PR で更新対象）
- **触ってはいけないファイル**: `web/src/ScoreboardPage/CurrentSet.jsx`、
  `web/src/ScoreboardPage/Player.jsx`、`.claude/git-workflow.md`
- コード内コメントは英語（agent-pitfalls は日本語ドキュメントなので日本語）。
  アプリ起動禁止（検証はユーザー実施）。コミット禁止

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/ -q` 全通過（direct_import 撤去後）
- [ ] subprocess で `import src` → QApplication 非生成・ファイル非作成を確認（新 assert）
- [ ] grep: リポジトリ全域に `direct_import` の残存参照ゼロ

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → golden path（スコアボード操作・Web UI・ホットキー）
- [ ] （次回リリース前）exe ビルド + 起動確認 — PyInstaller の依存解析経路が変わるため、
      Step 3 完了後の初回ビルドは要注意として Test Plan に明記

## スコープ外

- Design.md / current-architecture.md / CLAUDE.md 等の広範なドキュメント追従（Step 5）
- Step 4（遅延初期化）
