# Python Import Map

調査日: 2026-04-08
対象: `src/**/*.py` (62ファイル)
方法: `grep -n "^import\|^from"` による機械的抽出。標準ライブラリ・qtpy/PyQt6 は除外。

---

## ファイル別 import 一覧

### src/__init__.py
- 内部: `.Autona`
- 外部: (なし)

### src/main.py
- 内部: (なし) — `import Autona` (パッケージ外参照、上流残存)
- 外部: (なし)

### src/Autona.py
- 内部: `.Helpers.AutonaQtHelper`, `.Helpers.AutonaLocaleHelper`, `.Helpers.AutonaDirHelper`, `.Helpers.AutonaCountryHelper`, `.Helpers.AutonaControllerHelper`, `.Settings.AutonaSettingsWindow`, `.LayoutOptions.AutonaLayoutOptionsWindow`, `.AutonaHotkeys`, `.AutonaPlayerBrowserWidget`, `.AutonaNotesWidget`, `.AutonaCommentaryWidget`, `.AutonaGameAssetManager`, `.AutonaBracketWidget`, `.AutonaTournamentInfoWidget`, `.AutonaTournamentDataProvider`, `.TournamentDataProvider.StartGGDataProvider`, `.AutonaAlertNotification`, `.AutonaPlayerDB`, `.Workers`, `.StateManager`, `.SettingsManager`, `.AutonaScoreboardManager`, `.AutonaScoreboardStageWidget`, `.AutonaStatsWidget`, `src.AutonaAssetDownloader`, `src.AutonaAboutWidget`, `src.AutonaWebServer`, `. (ui_constants)`
- 外部: loguru, orjson, packaging.version, qdarktheme

### src/StateManager.py
- 内部: `.Helpers.AutonaDictHelper`, `.SettingsManager`
- 外部: deepdiff, deepdiff.helper, loguru, orjson, PIL, requests

### src/SettingsManager.py
- 内部: `.Helpers.AutonaDictHelper`
- 外部: orjson

### src/LayoutOptionsManager.py
- 内部: `.Helpers.AutonaDictHelper`
- 外部: orjson

### src/ui_constants.py
- 内部: `.SettingsManager`
- 外部: (なし)

### src/Workers.py
- 内部: (なし)
- 外部: loguru

---

### src/Helpers/AutonaBadWordFilter.py
- 内部: `..SettingsManager`, `.AutonaLocaleHelper`
- 外部: loguru

### src/Helpers/AutonaControllerHelper.py
- 内部: `..SettingsManager`
- 外部: loguru, requests

### src/Helpers/AutonaCountryHelper.py
- 内部: `..SettingsManager`, `..TournamentDataProvider`, `.AutonaDictHelper`, `.AutonaDirHelper`, `.AutonaLocaleHelper`
- 外部: countryflag, loguru, orjson, requests, tqdm

### src/Helpers/AutonaDictHelper.py
- 内部: (なし)
- 外部: msgpack

### src/Helpers/AutonaDirHelper.py
- 内部: (なし)
- 外部: loguru

### src/Helpers/AutonaLocaleHelper.py
- 内部: `.AutonaDictHelper`, `.AutonaDirHelper`, `src.SettingsManager`, `src.StateManager`
- 外部: loguru

### src/Helpers/AutonaQtHelper.py
- 内部: (なし)
- 外部: loguru

### src/Helpers/AutonaSponsorHelper.py
- 内部: `..StateManager`
- 外部: (なし)

### src/Helpers/AutonaVersionHelper.py
- 内部: `.AutonaDirHelper`
- 外部: loguru

---

### src/Settings/SettingsWidget.py
- 内部: `..SettingsManager`, `..AutonaColorButton`
- 外部: (なし)

### src/Settings/AutonaSettingsWindow.py
- 内部: `.SettingsWidget`, `..AutonaHotkeys`, `..Helpers.AutonaVersionHelper`
- 外部: (なし)

---

### src/LayoutOptions/LayoutOptionsWidget.py
- 内部: `..LayoutOptionsManager`, `..StateManager`, `..AutonaColorButton`
- 外部: loguru

### src/LayoutOptions/AutonaLayoutOptionsWindow.py
- 内部: `.LayoutOptionsWidget`
- 外部: (なし)

---

### src/AutonaAboutWidget.py
- 内部: `.Helpers.AutonaDirHelper`
- 外部: (なし)

### src/AutonaAlertNotification.py
- 内部: (なし)
- 外部: loguru, requests

### src/AutonaAssetDownloader.py
- 内部: `src.Helpers.AutonaQtHelper`, `src.AutonaGameAssetManager`, `src.Workers`
- 外部: loguru, orjson, py7zr, requests

### src/AutonaBracket.py
- 内部: `.Helpers.AutonaLocaleHelper`
- 外部: loguru

### src/AutonaBracketView.py
- 内部: `.AutonaBracket`, `.AutonaPlayerList`
- 外部: loguru

### src/AutonaBracketWidget.py
- 内部: `.Helpers.AutonaCountryHelper`, `.Helpers.AutonaDirHelper`, `.StateManager`, `.AutonaGameAssetManager`, `.AutonaPlayerDB`, `.AutonaTournamentDataProvider`, `.AutonaPlayerListSlotWidget`, `.AutonaBracketView`, `.AutonaPlayerList`, `.AutonaBracket`, `.AutonaHotkeys`
- 外部: loguru

### src/AutonaColorButton.py
- 内部: (なし)
- 外部: (なし)

### src/AutonaCommentaryWidget.py
- 内部: `.AutonaScoreboardPlayerWidget`, `.AutonaPlayerDB`, `.SettingsManager`, `.StateManager`, `.AutonaGameAssetManager`, `. (ui_constants)`
- 外部: loguru

### src/AutonaGameAssetManager.py
- 内部: `.StateManager`, `.Helpers.AutonaLocaleHelper`, `.Workers`
- 外部: loguru, orjson, PIL, requests

### src/AutonaHotkeys.py
- 内部: `.SettingsManager`, `.Helpers.AutonaLocaleHelper`, `.Helpers.AutonaDictHelper`
- 外部: loguru, pynput

### src/AutonaIndividualGameTracker.py
- 内部: `.AutonaGameAssetManager`, `.Helpers.AutonaQtHelper`, `.Helpers.AutonaVersionHelper`, `.SettingsManager`, `.StateManager`
- 外部: loguru

### src/AutonaNotesWidget.py
- 内部: `.StateManager`
- 外部: loguru

### src/AutonaPlayerDB.py
- 内部: `.Helpers.AutonaDictHelper`, `.Helpers.AutonaQtHelper`, `.AutonaGameAssetManager`, `.SettingsManager`
- 外部: loguru, orjson

### src/AutonaPlayerBrowserWidget.py
- 内部: `.StateManager`, `.AutonaPlayerDB`, `.AutonaGameAssetManager`, `.AutonaTournamentDataProvider`, `.AutonaScoreboardPlayerWidget`, `. (ui_constants)`（`.AutonaScoreboardManager` はメソッド内遅延 import）
- 外部: loguru

### src/AutonaScoreboardManager.py
- 内部: `.AutonaScoreboardWidget`, `.SettingsManager`, `.StateManager`
- 外部: loguru

### src/AutonaScoreboardPlayerWidget.py
- 内部: `.Helpers.AutonaCountryHelper`, `.Helpers.AutonaSponsorHelper`, `.Helpers.AutonaDirHelper`, `.Helpers.AutonaBadWordFilter`, `.StateManager`, `.AutonaGameAssetManager`, `.AutonaPlayerDB`, `.Workers`, `. (ui_constants)`
- 外部: loguru

### src/AutonaScoreboardStageWidget.py
- 内部: `.Helpers.AutonaDictHelper`, `.Helpers.AutonaDirHelper`, `.Helpers.AutonaLocaleHelper` (src直指定), `.SettingsManager`, `.StateManager`, `.AutonaGameAssetManager`, `src.AutonaStageStrikeLogic`
- 外部: loguru, orjson, requests

### src/AutonaScoreboardWidget.py
- 内部: `.Helpers.AutonaDirHelper`, `.Helpers.AutonaVersionHelper`, `.SettingsManager`, `.StateManager`, `.AutonaHotkeys`, `.AutonaIndividualGameTracker`, `.AutonaPlayerDB`, `.AutonaScoreboardPlayerWidget`, `.AutonaSelectSetWindow`, `.AutonaSelectStationWindow`, `.AutonaStatsUtil`, `.AutonaThumbnailSettingsWidget`, `.AutonaTournamentDataProvider`, `.thumbnail.main_generate_thumbnail`, `src.AutonaColorButton`, `. (ui_constants)`
- 外部: (なし — 標準ライブラリのみ)

### src/AutonaSelectSetWindow.py
- 内部: `src.AutonaTournamentDataProvider`
- 外部: loguru

### src/AutonaSelectStationWindow.py
- 内部: `src.AutonaTournamentDataProvider`
- 外部: loguru

### src/AutonaStageStrikeLogic.py
- 内部: `.StateManager`
- 外部: loguru

### src/AutonaStatsUtil.py
- 内部: `.StateManager`, `.SettingsManager`, `.AutonaTournamentDataProvider`
- 外部: loguru, orjson

### src/AutonaStatsWidget.py
- 内部: `.StateManager`
- 外部: loguru

### src/AutonaTeamBattleModeEnum.py
- 内部: (なし)
- 外部: (なし)

### src/AutonaTeamBattleWidget.py
- 内部: `.Helpers.AutonaDirHelper`, `.Helpers.AutonaSponsorHelper`, `.Helpers.AutonaLocaleHelper`, `.StateManager`, `.AutonaTeamBattleModeEnum`, `.AutonaTeamPlayerWidget`
- 外部: loguru

### src/AutonaTeamPlayerWidget.py
- 内部: `.Helpers.AutonaCountryHelper`, `.Helpers.AutonaSponsorHelper`, `.Helpers.AutonaDirHelper`, `.Helpers.AutonaBadWordFilter`, `.StateManager`, `.AutonaGameAssetManager`, `.AutonaPlayerDB`, `.AutonaTeamBattleModeEnum`
- 外部: loguru

### src/AutonaThumbnailSettingsWidget.py
- 内部: `.Helpers.AutonaDictHelper`, `.Helpers.AutonaDirHelper`, `.SettingsManager`, `.AutonaGameAssetManager`, `.Workers`, `.thumbnail.main_generate_thumbnail`
- 外部: loguru

### src/AutonaTournamentDataProvider.py
- 内部: `.SettingsManager`, `.StateManager`, `.AutonaGameAssetManager`, `.TournamentDataProvider.TournamentDataProvider`, `.TournamentDataProvider.StartGGDataProvider`, `.Helpers.AutonaVersionHelper`, `.Workers`
- 外部: loguru

### src/AutonaTournamentInfoWidget.py
- 内部: `.StateManager`, `.AutonaTournamentDataProvider`, `.SettingsManager`, `.Helpers.AutonaLocaleHelper`, `.Helpers.AutonaDirHelper`, `.AutonaColorButton`
- 外部: loguru, requests

### src/AutonaWebServer.py
- 内部: `.StateManager`, `.AutonaWebServerActions`, `.AutonaScoreboardManager`, `.AutonaCommentaryWidget`, `.SettingsManager`
- 外部: flask, flask_cors, flask_socketio, loguru, orjson

### src/AutonaWebServerActions.py
- 内部: `.Helpers.AutonaCountryHelper`, `.Helpers.AutonaQtHelper`, `.Helpers.AutonaControllerHelper`, `.Helpers.AutonaLocaleHelper`, `.StateManager`, `.AutonaStatsUtil`, `.SettingsManager`, `.AutonaGameAssetManager`, `.AutonaBracketView`, `.AutonaBracketWidget`, `.AutonaPlayerDB`, `.AutonaScoreboardWidget`, `.AutonaTournamentDataProvider`, `.AutonaCommentaryWidget`, `.Workers`
- 外部: flask, loguru, orjson

---

### src/TournamentDataProvider/TournamentDataProvider.py
- 内部: (なし)
- 外部: loguru, orjson, requests

### src/TournamentDataProvider/StartGGDataProvider.py
- 内部: `..Helpers.AutonaCountryHelper`, `..Helpers.AutonaDictHelper`, `..Helpers.AutonaDirHelper`, `..Helpers.AutonaLocaleHelper`, `..Helpers.AutonaQtHelper`, `..AutonaBracket`, `..AutonaGameAssetManager`, `..AutonaPlayerDB`, `..Workers`, `.TournamentDataProvider`
- 外部: loguru, orjson, requests

---

### src/thumbnail/main_generate_thumbnail.py
- 内部: `src.AutonaGameAssetManager`, `src.Helpers.AutonaLocaleHelper`, `src.Helpers.AutonaDictHelper`, `src.Helpers.AutonaDirHelper`
- 外部: loguru

---

### src/i18n/gen_translation_files.py
- 内部: (なし)
- 外部: (なし) — スタンドアロンスクリプト

### src/i18n/gen_translation_binaries.py
- 内部: (なし)
- 外部: (なし) — スタンドアロンスクリプト

### src/i18n/gen_round_names_files.py
- 内部: (なし)
- 外部: (なし) — スタンドアロンスクリプト

---

## 循環 import 候補

### 直接循環（A が B を import し、B が A を import）
検出されず (0件)

### 間接循環（`from . import *` → `__init__.py` → 全モジュール再 export 経由）

以下4ファイルが `from . import ui_constants as UI` や `from . import *` でパッケージルート (`src`) を import している。`src/__init__.py` が `from .Autona import *` を実行するため、import 時に Autona.py のトップレベルコードが走り、全ドックウィジェットが連鎖的にロードされる。

| ファイル | import 文 |
|---------|----------|
| `src/AutonaCommentaryWidget.py` | `from . import ui_constants as UI` |
| `src/AutonaScoreboardPlayerWidget.py` | `from . import ui_constants as UI` |
| `src/AutonaScoreboardWidget.py` | `from . import ui_constants as UI` |
| `src/Autona.py` | `from . import ui_constants as UI` |

**リスク**: `from . import ui_constants` は `src/__init__.py` を経由する。`__init__.py` の `from .Autona import *` が先に実行されるため、Autona.py 内の import 群が全て走る。実際の動作では Python のモジュールキャッシュにより無限ループにはならないが、import 順序に暗黙の依存が生まれている。

---

## 孤立ファイル

アプリ本体の import ツリーから到達不能なファイル:

| ファイル | 状態 | 備考 |
|---------|------|------|
| `src/i18n/gen_translation_files.py` | CI 専用 | `.github/workflows/build_app.yml` からのみ参照 |
| `src/i18n/gen_translation_binaries.py` | CI 専用 | 同上 |
| `src/i18n/gen_round_names_files.py` | CI 専用 | 同上 |
| `src/main.py` | 上流残存 | `import Autona` — プロジェクトルートの `main.py` が実際のエントリーポイント |
