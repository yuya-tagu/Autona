# シングルトン初期化順

調査日: 2026-04-08
方法: `grep -rn ".instance"` + 各ファイルの `__init__` 読解

---

## シングルトン一覧（8つ）

| クラス | ファイル | 初期化行 | パターン |
|--------|---------|---------|---------|
| AutonaHotkeys | `src/AutonaHotkeys.py:116` | `AutonaHotkeys.instance = AutonaHotkeys()` | モジュール末尾 |
| AutonaGameAssetManager | `src/AutonaGameAssetManager.py:1601-1602` | `if ... == None: AutonaGameAssetManager.instance = AutonaGameAssetManager()` | 条件付きモジュール末尾 |
| AutonaTournamentDataProvider | `src/AutonaTournamentDataProvider.py:398` | `AutonaTournamentDataProvider.instance = AutonaTournamentDataProvider()` | モジュール末尾 |
| AutonaAlertNotification | `src/AutonaAlertNotification.py:115` | `AutonaAlertNotification.instance = AutonaAlertNotification()` | モジュール末尾 |
| AutonaScoreboardManager | `src/AutonaScoreboardManager.py:101` | `AutonaScoreboardManager.instance = AutonaScoreboardManager()` | モジュール末尾 |
| AutonaAssetDownloader | `src/AutonaAssetDownloader.py:537` | `AutonaAssetDownloader.instance = AutonaAssetDownloader()` | モジュール末尾 |
| AutonaCountryHelper | `src/Helpers/AutonaCountryHelper.py:295` | `AutonaCountryHelper.instance = AutonaCountryHelper()` | モジュール末尾 |
| AutonaControllerHelper | `src/Helpers/AutonaControllerHelper.py:210` | `AutonaControllerHelper.instance = AutonaControllerHelper()` | モジュール末尾 |

※ StateManager, SettingsManager, AutonaPlayerDB は static クラス（インスタンス化しない）のためシングルトンに含めない

---

## 起動シーケンス

### Phase 0: エントリーポイント

```
main.py (プロジェクトルート)
  → import src                           # src/__init__.py
    → from .Autona import *  # src/Autona.py のトップレベル実行開始
```

### Phase 1: Autona.py のトップレベル（import 時に実行）

`src/Autona.py` の import 文が上から順に処理される。各 import 先モジュールの末尾でシングルトンが生成される。

```
line 42:  App = QApplication(sys.argv)       # QApplication 生成
line 43:  AutonaQtHelper.init_gui_executor()    # GUI スレッドヘルパー初期化

line 127: from .AutonaHotkeys import AutonaHotkeys
          → AutonaHotkeys.instance = AutonaHotkeys()                    [1] 依存: SettingsManager (static)

line 132: from .AutonaGameAssetManager import AutonaGameAssetManager
          → AutonaGameAssetManager.instance = AutonaGameAssetManager()  [2] 依存: StateManager (static)

line 135: from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
          → AutonaTournamentDataProvider.instance = AutonaTournamentDataProvider()  [3] 依存: AutonaGameAssetManager.instance ★

line 137: from .AutonaAlertNotification import AutonaAlertNotification
          → AutonaAlertNotification.instance = AutonaAlertNotification()  [4] 依存: なし

line 142: from .Helpers.AutonaCountryHelper import AutonaCountryHelper
          → AutonaCountryHelper.instance = AutonaCountryHelper()          [5] 依存: なし

line 143: from .Helpers.AutonaControllerHelper import AutonaControllerHelper
          → AutonaControllerHelper.instance = AutonaControllerHelper()    [6] 依存: なし

line 144: from .AutonaScoreboardManager import AutonaScoreboardManager
          → AutonaScoreboardManager.instance = AutonaScoreboardManager()  [7] 依存: StateManager (static)

line 145: from src.AutonaAssetDownloader import AutonaAssetDownloader
          → AutonaAssetDownloader.instance = AutonaAssetDownloader()      [8] 依存: なし
```

### Phase 2: Window.__init__（main.py の `src.Window(event_loop)` で実行）

```
line 322: StateManager.loop = loop
line 323: StateManager.BlockSaving()
line 325: AutonaLocaleHelper.LoadLocale()
line 375: AutonaControllerHelper.instance.init()         # コントローラーDB更新
line 376: AutonaCountryHelper.instance.UpdateCountriesFile()  # 国データ更新
line 437: self.scoreboard = AutonaScoreboardManager.instance  # ドックに組み込み
line 460: self.webserver = WebServer(...)              # Flask+SocketIO サーバー起動
```

### Phase 3: UiMounted（Window.__init__ の最終段階）

```
line 931: self.settingsWindow.UiMounted()
line 933: AutonaTournamentDataProvider.instance.UiMounted()  # SetTournament 復元等
line 934: AutonaGameAssetManager.instance.UiMounted()        # ゲームデータ読み込み開始
line 935: AutonaAlertNotification.instance.UiMounted()       # アラートチェック
line 936: AutonaAssetDownloader.instance.UiMounted()         # アイコン初期化
line 937: AutonaHotkeys.instance.UiMounted(self)             # pynput リスナー起動
```

---

## シングルトン間依存グラフ

```
AutonaHotkeys [1]
  └── SettingsManager (static) — LoadUserHotkeys で読み取り

AutonaGameAssetManager [2]
  └── StateManager (static) — __init__ で StateManager.Set("game", {})

AutonaTournamentDataProvider [3]  ★ 重要
  ├── StateManager (static)
  ├── SettingsManager (static)
  └── AutonaGameAssetManager.instance ★ — __init__ L47 で .signals.onLoadAssets.connect()

AutonaAlertNotification [4]
  └── (依存なし)

AutonaCountryHelper [5]
  └── (依存なし。__init__ は空。実質的な初期化は UpdateCountriesFile で行われる)

AutonaControllerHelper [6]
  └── (依存なし。__init__ は空。実質的な初期化は .init() で行われる)

AutonaScoreboardManager [7]
  └── StateManager (static) — __init__ で StateManager.Unset("score")

AutonaAssetDownloader [8]
  └── (依存なし)
```

**依存関係の要点:**
- `AutonaTournamentDataProvider` → `AutonaGameAssetManager.instance` が唯一のシングルトン間依存
- この依存は import 順で保証されている（line 132 → line 135）
- それ以外の依存は全て static クラス（StateManager, SettingsManager）への参照のみ

---

## 暗黙の初期化順序リスク

### 1. `AutonaTournamentDataProvider` ← `AutonaGameAssetManager.instance` 依存 ⚠️

**箇所**: `src/AutonaTournamentDataProvider.py:47`
```python
AutonaGameAssetManager.instance.signals.onLoadAssets.connect(self.SetGameFromProvider)
```
**リスク**: AutonaGameAssetManager の import (line 132) を AutonaTournamentDataProvider の import (line 135) より後に移動すると、`AutonaGameAssetManager.instance` が None で AttributeError。
**重大度**: 中（import 順を変えた場合のみ発火）

### 2. モジュールトップレベルでのシングルトン生成 ⚠️ 要確認

**箇所**: 全8シングルトンの `XXX.instance = XXX()` がモジュール末尾に記述
**リスク**: import 時に副作用（インスタンス生成）が実行される。テストでモジュールを個別に import すると意図しないシングルトン生成が走る可能性がある。
**重大度**: 低（現状動作に問題なし。pytest 導入時に要検討）

### 3. `from . import ui_constants as UI` のパッケージ経由 import ⚠️ 要確認

**箇所**: `src/AutonaCommentaryWidget.py`, `src/AutonaScoreboardPlayerWidget.py`, `src/AutonaScoreboardWidget.py`
**リスク**: `from .` が `src/__init__.py` を経由し、`from .Autona import *` が実行される。Autona.py のトップレベルで QApplication の生成（line 42）等が走るため、import 順によっては二重初期化の可能性がある。実際には Python のモジュールキャッシュにより問題にならないが、テスト環境では要注意。
**重大度**: 低（Python のモジュールキャッシュが保護）

### 4. AutonaGameAssetManager の条件付き初期化 ⚠️ 要確認

**箇所**: `src/AutonaGameAssetManager.py:1601-1602`
```python
if AutonaGameAssetManager.instance == None:
    AutonaGameAssetManager.instance = AutonaGameAssetManager()
```
**リスク**: 他のシングルトンは無条件で初期化されるが、AutonaGameAssetManager だけ `== None` チェック付き。これは `from . import *` による二重 import を防ぐためと推測されるが、明確なコメントがない。
**重大度**: 低（防御的コードとして機能している）

---

## 補足: static クラス（シングルトンではないがグローバル状態を持つ）

| クラス | ファイル | 初期化 |
|--------|---------|--------|
| StateManager | `src/StateManager.py` | クラス変数で状態保持。`import` 時にクラス定義が評価される |
| SettingsManager | `src/SettingsManager.py` | 同上 |
| LayoutOptionsManager | `src/LayoutOptionsManager.py` | 同上 |
| AutonaPlayerDB | `src/AutonaPlayerDB.py` | クラス変数 + static メソッド。`import` 時にクラス定義が評価される |
| AutonaLocaleHelper | `src/Helpers/AutonaLocaleHelper.py` | 同上 |
