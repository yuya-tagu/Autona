# implement: 起動再設計 Step 3b — GameAssetManager / TournamentDataProvider の bootstrap 移行

作成: 2026-07-29 ／ 設計正本: [../changes/startup-architecture-redesign.md](../changes/startup-architecture-redesign.md) §2 Step 3 ／ 決定: [ADR-003](../decisions/ADR-003-startup-architecture.md) D2/D3 ／ 先行: [Step 3a](implement-startup-step3a-bootstrap-core.md)（PR #147）

## ゴール

移行リスト 3〜4 番目のペアを `src/bootstrap.py` へ移す。**このペアは agent-pitfalls #2 の
順序制約そのもの**（TournamentDataProvider.__init__ が GameAssetManager.instance.signals に
connect する）であり、暗黙の import 順序制約を bootstrap 上の明示的な生成順コメントに変換する。

1. **AutonaGameAssetManager** — モジュール末尾（[src/AutonaGameAssetManager.py](../../src/AutonaGameAssetManager.py):1449-1453）の
   `./user_data/games` 作成 + `instance` 生成を廃止
2. **AutonaTournamentDataProvider** — モジュール末尾（[src/AutonaTournamentDataProvider.py](../../src/AutonaTournamentDataProvider.py):418）の
   `instance` 生成を廃止

**挙動不変の移行**。生成順は現行の実効順（GAM → TDP）を固定する。

## 実装内容

### 1. `src/bootstrap.py` — bootstrap_core() に 2 ステップ追加

- 既存の StateManager → AutonaHotkeys の**後**に追加:
  1. GameAssetManager: `./user_data/games` 確保 + `instance` 生成
     （mkdir は `__init__` 先頭へ移すか bootstrap 側に置くか、既存コード構造に合う方を選び
     理由をコメントで明記）
  2. TournamentDataProvider: `instance` 生成。**直前コメントに順序制約を明記**
     （英語で: TDP.__init__ connects to GameAssetManager.instance.signals — 現
     agent-pitfalls #2 の内容をコードへ移す）
- いずれも `instance is None` ガード付き（Step 3a の Hotkeys と同じパターン）

### 2. 両モジュールの末尾副作用を削除

- 削除対象以外のモジュールレベルコード（定数・クラス定義）は無変更

### 3. `src/AutonaPlayerDB.py` — モジュールレベル connect の移設（スコープ拡張・2026-07-29 決定）

[src/AutonaPlayerDB.py](../../src/AutonaPlayerDB.py):355 の

```python
AutonaGameAssetManager.instance.signals.onLoad.connect(AutonaPlayerDB.SetupModel)
```

を削除し、`bootstrap_core()` の GameAssetManager 生成**直後**へ移す。

**背景（実装前確認で判明）:** `AutonaPlayerDB` は `AutonaTournamentDataProvider`
（9 行）と `TournamentDataProvider/StartGGDataProvider.py`（13 行）から import される。
GAM の import 時 instance 生成を廃止すると、この行が `AttributeError:
'NoneType' object has no attribute 'signals'` で落ち、**TDP の import 自体が失敗する**
（一時 cwd で再現確認済み）。

**スコープ拡張の根拠:**

- import 時の暗黙依存（「誰かが AutonaPlayerDB を import する前に GAM.instance が
  存在していること」）の典型例であり、Step 3 が解消しようとしている当のもの。
  これを残すと制約が「import 時生成」から「より見えにくい import 順序」へ移るだけになる
- `AutonaPlayerDB` は static クラスでインスタンス生成を伴わないため、
  「1 PR = 1〜2 シングルトン」の粒度は維持される
- 接続タイミングは GAM の初回 `LoadGames()`（`UiMounted()` 経由）より十分前であり挙動不変

### 4. `tests/test_import_side_effects.py` の対象拡張

- 検査対象に `src.AutonaGameAssetManager` / `src.AutonaPlayerDB` /
  `src.AutonaTournamentDataProvider` を追加
- assert 追加: 両 `instance is None`、一時 cwd に `./user_data/games` が**作られない**こと
- 既存の assert（QApplication 非生成 / ./out 非作成）は両モジュール import 後の状態で維持
- **フィクスチャ拡張が必須**: `AutonaResolve` は import 時の `Path.cwd()` 基準
  （[src/Helpers/AutonaDirHelper.py](../../src/Helpers/AutonaDirHelper.py)）であり、
  GAM → `AutonaLocaleHelper` 末尾の `LoadLanguages()` / `LoadCountryToLanguage()` と
  `StartGGDataProvider` 末尾の 18 個の query 読み込みが一時 cwd 相対でファイルを読む
  （前 2 者は失敗時に例外送出）。`src/i18n/mapping.json`・`assets/data_countries.json`・
  `src/TournamentDataProvider/StartGG*Query.txt` を一時 cwd へ事前コピーする
- 既知の残副作用（`AutonaCountryHelper` の import 時 instance 生成 /
  `SettingsManager` の `user_data/settings.json` 読み書き）はテスト内コメントで明記

## 実装前に必ず確認すること（1 つでも該当したら実装を止めて報告）

- [ ] GAM / TDP の `__init__`（および `__init__` から呼ぶメソッド）が、**未移行の
      import 時生成シングルトン**（AutonaAlertNotification / AutonaUpdater /
      AutonaCountryHelper / AutonaScoreboardManager / WebServer）の `instance` を参照していないか。
      参照があると bootstrap（全 import より前）への移動で None 参照になる
- [ ] `src/TournamentDataProvider/` 配下（StartGGDataProvider 等、TDP が import する連鎖）に
      モジュールレベルのシングルトン生成・`instance` 参照・Qt オブジェクト生成が無いか
- [ ] 両モジュールのモジュールレベルに、末尾の削除対象以外の副作用（ファイル I/O・
      ネットワーク・スレッド起動）が無いか
- [ ] `AutonaGameAssetManager.__init__` が QWidget を生成していないか（QObject なら
      QApplication 不要で副作用ゼロ検査が通る。QWidget があれば検査方法の再検討が必要）

## 制約・地雷

- [.claude/rules/agent-pitfalls.md](../../.claude/rules/agent-pitfalls.md) 全項目（特に #1 / #2 / #12 / #13）
- **並行セッションの作業中ファイルに絶対に触れない**: `src/Settings/AutonaSettingsWindow.py`、
  `tests/test_hotkeys_enabled.py`、`web/src/ScoreboardPage/CurrentSet.jsx`、`web/src/ScoreboardPage/Player.jsx`
- コード内コメントは英語。PySide6（qtpy 経由）のみ
- アプリ起動禁止（検証はユーザー実施）。py_compile + pytest まで
- コミット禁止（ユーザー指示があるまで）

## 検証（Claude 実施分）

- [ ] `python -m py_compile` 変更ファイル全通過
- [ ] `pytest tests/test_import_side_effects.py -v` 通過
- [ ] `pytest tests/ -q` 回帰なし

## 検証（ユーザー実施分・報告時に提示）

- [ ] `python main.py` 起動 → ゲーム選択・キャラアイコン表示（GameAssetManager）
- [ ] Start.gg URL 読み込み → セット取得（TournamentDataProvider）
- [ ] manual-test-checklist の該当項目

## スコープ外（後続 PR）

- AutonaAlertNotification / AutonaUpdater / AutonaCountryHelper / AutonaScoreboardManager /
  AutonaWebServer / SettingsManager の移行
- `src/__init__.py` の連鎖 import 廃止（Step 3 最終 PR）
- agent-pitfalls #2 の記述更新（全移行完了後の Step 5）
