# implement: 起動再設計 Step 3f — 静的データ読み込みの import 副作用廃止

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 3 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) D2/D3 ／ 先行: Step 3a〜3e（PR #147/#149/#151/#152/#153）

## ゴール

シングルトン移行（9 件・完了済み）の残りである **import 時の静的データ読み込み 4 系統**を
明示初期化に変える。いずれもインスタンス生成・Qt オブジェクト生成を伴わず、相互依存もない想定
（実装前確認で検証。依存が見つかったら停止して報告）。

| # | モジュール | 現行の import 副作用 |
|---|-----------|---------------------|
| 1 | [src/SettingsManager.py](../../src/SettingsManager.py) 末尾 | `settings.json` 初回作成 + `LoadSettings()` |
| 2 | [src/Helpers/AutonaLocaleHelper.py](../../src/Helpers/AutonaLocaleHelper.py) 末尾 | `LoadLanguages()` + `LoadCountryToLanguage()`（i18n/mapping.json 等の読み込み・失敗時例外） |
| 3 | [src/TournamentDataProvider/StartGGDataProvider.py](../../src/TournamentDataProvider/StartGGDataProvider.py) 末尾 | GQL クエリ 18 ファイルをクラス属性へ読み込み |
| 4 | [src/Helpers/AutonaBadWordFilter.py](../../src/Helpers/AutonaBadWordFilter.py) 末尾 | `assets/ngword/` 読み込み + `user_data/badword_*.txt` 作成 |

## 実装内容

### 1. 各モジュールに冪等な明示初期化を追加し、末尾の実行を削除

- 既存流儀（StateManager.Initialize / Step 3a）に合わせ、各クラスに static `Initialize()` を追加
  （`_initialized` フラグで冪等化）。末尾のモジュールレベル実行を削除
- StartGGDataProvider は 18 行の `readQueryFile` 群を `Initialize()` 内へ移す
  （`sggTdpDir = AutonaResolve(...)` の評価タイミングも `Initialize()` 内へ。
  AutonaResolve は import 時 cwd キャプチャ依存のため、挙動が変わらないことを確認して移すこと）
- SettingsManager.Initialize は `os.makedirs("./user_data", exist_ok=True)` を先頭に追加してよい
  （初回起動で user_data 不在だと現行は import 時に例外。防御的改善として報告に明記）

### 2. `src/bootstrap.py` — bootstrap_core() への組み込み

- **SettingsManager.Initialize() を bootstrap_core() の先頭**（StateManager より前）に置く
  （StateManager は SettingsManager を import し、Hotkeys.LoadUserHotkeys 等が設定を読むため）
- LocaleHelper / BadWordFilter は依存ゼロの静的データのため SettingsManager の直後にまとめる
- StartGGDataProvider.Initialize()（クエリ読み込み）は **TDP 生成の直前**に置く
- 各ステップに独立性 or 順序理由の英語コメント（既存パターン踏襲）

### 3. `tests/test_import_side_effects.py` の強化

- 検査対象に上記 4 モジュールを追加（fake-package 方式で既に import 連鎖に入っているものも
  明示対象として assert を追加）
- **これまで「既知の残副作用」として許容していた assert を厳格化**:
  一時 cwd に `user_data/settings.json` / `user_data/badword_*.txt` が**作られない**こと、
  StartGG クエリ属性が import 後 None（未ロード）であること
- seed フィクスチャの `user_data/` 事前作成・関連コメントは、不要になった分を整理

## 実装前に必ず確認すること（該当したら停止して報告）

- [ ] 4 モジュールのロード済みデータ（`SettingsManager.settings` / `AutonaLocaleHelper.languages`
      等のクラス属性）を **import 時評価位置**で読む箇所が `src/` 全域に無いか（AST 走査）
- [ ] bootstrap_core() より前に実行されるコード（main.py / autona_splash / Autona.py の
      bootstrap 呼び出しより上の行）が 4 モジュールのデータに触れていないか
- [ ] `src/thumbnail/main_generate_thumbnail.py` / `src/result/main_generate_result.py` 等、
      Window 経由以外の経路でこれらのデータを使うモジュールが「Initialize 済み」前提で
      壊れないか（すべて bootstrap 後の runtime 呼び出しであることを確認）
- [ ] LocaleHelper の翻訳ロード（Autona.py の QTranslator 系）が import 時データに依存していないか

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md)（特に #1 / #12）
- **触ってはいけないファイル**: `web/src/ScoreboardPage/CurrentSet.jsx`、
  `web/src/ScoreboardPage/Player.jsx`、`.claude/git-workflow.md`
- コード内コメントは英語。アプリ起動禁止（検証はユーザー実施）。コミット禁止

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/test_import_side_effects.py -v` / `pytest tests/test_webserver_routes.py -v` 通過
- [ ] `pytest tests/ -q` 回帰なし
- [ ] AST 走査で 4 モジュールのデータへの import 時評価位置参照 0 件

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → 設定値の反映（言語・ポート等）・言語切替表示
- [ ] Start.gg URL 読み込み → セット取得（クエリロード経路）
- [ ] 選手名入力での NG ワードフィルタ動作（タグ入力欄）

## スコープ外（後続 PR = Step 3 最終）

- `src/__init__.py` の連鎖 import 廃止 + `tests/conftest.py` の `direct_import()` 不要化確認 +
  agent-pitfalls #1/#2 の更新
