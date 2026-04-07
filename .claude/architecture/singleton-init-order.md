# シングルトン初期化順

調査日: 2026-04-08
方法: `grep -rn ".instance"` + 各ファイルの `__init__` 読解

---

## シングルトン一覧（8つ）

| クラス | ファイル | 初期化行 | パターン |
|--------|---------|---------|---------|
| TSHHotkeys | `src/TSHHotkeys.py:116` | `TSHHotkeys.instance = TSHHotkeys()` | モジュール末尾 |
| TSHGameAssetManager | `src/TSHGameAssetManager.py:1601-1602` | `if ... == None: TSHGameAssetManager.instance = TSHGameAssetManager()` | 条件付きモジュール末尾 |
| TSHTournamentDataProvider | `src/TSHTournamentDataProvider.py:398` | `TSHTournamentDataProvider.instance = TSHTournamentDataProvider()` | モジュール末尾 |
| TSHAlertNotification | `src/TSHAlertNotification.py:115` | `TSHAlertNotification.instance = TSHAlertNotification()` | モジュール末尾 |
| TSHScoreboardManager | `src/TSHScoreboardManager.py:101` | `TSHScoreboardManager.instance = TSHScoreboardManager()` | モジュール末尾 |
| TSHAssetDownloader | `src/TSHAssetDownloader.py:537` | `TSHAssetDownloader.instance = TSHAssetDownloader()` | モジュール末尾 |
| TSHCountryHelper | `src/Helpers/TSHCountryHelper.py:295` | `TSHCountryHelper.instance = TSHCountryHelper()` | モジュール末尾 |
| TSHControllerHelper | `src/Helpers/TSHControllerHelper.py:210` | `TSHControllerHelper.instance = TSHControllerHelper()` | モジュール末尾 |

※ StateManager, SettingsManager, TSHPlayerDB は static クラス（インスタンス化しない）のためシングルトンに含めない

---

## 起動シーケンス

### Phase 0: エントリーポイント

```
main.py (プロジェクトルート)
  → import src                           # src/__init__.py
    → from .TournamentStreamHelper import *  # src/TournamentStreamHelper.py のトップレベル実行開始
```

### Phase 1: TournamentStreamHelper.py のトップレベル（import 時に実行）

`src/TournamentStreamHelper.py` の import 文が上から順に処理される。各 import 先モジュールの末尾でシングルトンが生成される。

```
line 42:  App = QApplication(sys.argv)       # QApplication 生成
line 43:  TSHQtHelper.init_gui_executor()    # GUI スレッドヘルパー初期化

line 127: from .TSHHotkeys import TSHHotkeys
          → TSHHotkeys.instance = TSHHotkeys()                    [1] 依存: SettingsManager (static)

line 132: from .TSHGameAssetManager import TSHGameAssetManager
          → TSHGameAssetManager.instance = TSHGameAssetManager()  [2] 依存: StateManager (static)

line 135: from .TSHTournamentDataProvider import TSHTournamentDataProvider
          → TSHTournamentDataProvider.instance = TSHTournamentDataProvider()  [3] 依存: TSHGameAssetManager.instance ★

line 137: from .TSHAlertNotification import TSHAlertNotification
          → TSHAlertNotification.instance = TSHAlertNotification()  [4] 依存: なし

line 142: from .Helpers.TSHCountryHelper import TSHCountryHelper
          → TSHCountryHelper.instance = TSHCountryHelper()          [5] 依存: なし

line 143: from .Helpers.TSHControllerHelper import TSHControllerHelper
          → TSHControllerHelper.instance = TSHControllerHelper()    [6] 依存: なし

line 144: from .TSHScoreboardManager import TSHScoreboardManager
          → TSHScoreboardManager.instance = TSHScoreboardManager()  [7] 依存: StateManager (static)

line 145: from src.TSHAssetDownloader import TSHAssetDownloader
          → TSHAssetDownloader.instance = TSHAssetDownloader()      [8] 依存: なし
```

### Phase 2: Window.__init__（main.py の `src.Window(event_loop)` で実行）

```
line 322: StateManager.loop = loop
line 323: StateManager.BlockSaving()
line 325: TSHLocaleHelper.LoadLocale()
line 375: TSHControllerHelper.instance.init()         # コントローラーDB更新
line 376: TSHCountryHelper.instance.UpdateCountriesFile()  # 国データ更新
line 437: self.scoreboard = TSHScoreboardManager.instance  # ドックに組み込み
line 460: self.webserver = WebServer(...)              # Flask+SocketIO サーバー起動
```

### Phase 3: UiMounted（Window.__init__ の最終段階）

```
line 931: self.settingsWindow.UiMounted()
line 933: TSHTournamentDataProvider.instance.UiMounted()  # SetTournament 復元等
line 934: TSHGameAssetManager.instance.UiMounted()        # ゲームデータ読み込み開始
line 935: TSHAlertNotification.instance.UiMounted()       # アラートチェック
line 936: TSHAssetDownloader.instance.UiMounted()         # アイコン初期化
line 937: TSHHotkeys.instance.UiMounted(self)             # pynput リスナー起動
```

---

## シングルトン間依存グラフ

```
TSHHotkeys [1]
  └── SettingsManager (static) — LoadUserHotkeys で読み取り

TSHGameAssetManager [2]
  └── StateManager (static) — __init__ で StateManager.Set("game", {})

TSHTournamentDataProvider [3]  ★ 重要
  ├── StateManager (static)
  ├── SettingsManager (static)
  └── TSHGameAssetManager.instance ★ — __init__ L47 で .signals.onLoadAssets.connect()

TSHAlertNotification [4]
  └── (依存なし)

TSHCountryHelper [5]
  └── (依存なし。__init__ は空。実質的な初期化は UpdateCountriesFile で行われる)

TSHControllerHelper [6]
  └── (依存なし。__init__ は空。実質的な初期化は .init() で行われる)

TSHScoreboardManager [7]
  └── StateManager (static) — __init__ で StateManager.Unset("score")

TSHAssetDownloader [8]
  └── (依存なし)
```

**依存関係の要点:**
- `TSHTournamentDataProvider` → `TSHGameAssetManager.instance` が唯一のシングルトン間依存
- この依存は import 順で保証されている（line 132 → line 135）
- それ以外の依存は全て static クラス（StateManager, SettingsManager）への参照のみ

---

## 暗黙の初期化順序リスク

### 1. `TSHTournamentDataProvider` ← `TSHGameAssetManager.instance` 依存 ⚠️

**箇所**: `src/TSHTournamentDataProvider.py:47`
```python
TSHGameAssetManager.instance.signals.onLoadAssets.connect(self.SetGameFromProvider)
```
**リスク**: TSHGameAssetManager の import (line 132) を TSHTournamentDataProvider の import (line 135) より後に移動すると、`TSHGameAssetManager.instance` が None で AttributeError。
**重大度**: 中（import 順を変えた場合のみ発火）

### 2. モジュールトップレベルでのシングルトン生成 ⚠️ 要確認

**箇所**: 全8シングルトンの `XXX.instance = XXX()` がモジュール末尾に記述
**リスク**: import 時に副作用（インスタンス生成）が実行される。テストでモジュールを個別に import すると意図しないシングルトン生成が走る可能性がある。
**重大度**: 低（現状動作に問題なし。pytest 導入時に要検討）

### 3. `from . import ui_constants as UI` のパッケージ経由 import ⚠️ 要確認

**箇所**: `src/TSHCommentaryWidget.py`, `src/TSHScoreboardPlayerWidget.py`, `src/TSHScoreboardWidget.py`
**リスク**: `from .` が `src/__init__.py` を経由し、`from .TournamentStreamHelper import *` が実行される。TournamentStreamHelper.py のトップレベルで QApplication の生成（line 42）等が走るため、import 順によっては二重初期化の可能性がある。実際には Python のモジュールキャッシュにより問題にならないが、テスト環境では要注意。
**重大度**: 低（Python のモジュールキャッシュが保護）

### 4. TSHGameAssetManager の条件付き初期化 ⚠️ 要確認

**箇所**: `src/TSHGameAssetManager.py:1601-1602`
```python
if TSHGameAssetManager.instance == None:
    TSHGameAssetManager.instance = TSHGameAssetManager()
```
**リスク**: 他のシングルトンは無条件で初期化されるが、TSHGameAssetManager だけ `== None` チェック付き。これは `from . import *` による二重 import を防ぐためと推測されるが、明確なコメントがない。
**重大度**: 低（防御的コードとして機能している）

---

## 補足: static クラス（シングルトンではないがグローバル状態を持つ）

| クラス | ファイル | 初期化 |
|--------|---------|--------|
| StateManager | `src/StateManager.py` | クラス変数で状態保持。`import` 時にクラス定義が評価される |
| SettingsManager | `src/SettingsManager.py` | 同上 |
| LayoutOptionsManager | `src/LayoutOptionsManager.py` | 同上 |
| TSHPlayerDB | `src/TSHPlayerDB.py` | クラス変数 + static メソッド。`import` 時にクラス定義が評価される |
| TSHLocaleHelper | `src/Helpers/TSHLocaleHelper.py` | 同上 |
