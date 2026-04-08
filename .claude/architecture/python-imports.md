# Python Import Map

調査日: 2026-04-08
対象: `src/**/*.py` (62ファイル)
方法: `grep -n "^import\|^from"` による機械的抽出。標準ライブラリ・qtpy/PyQt6 は除外。

---

## ファイル別 import 一覧

### src/__init__.py
- 内部: `.TournamentStreamHelper`
- 外部: (なし)

### src/main.py
- 内部: (なし) — `import TournamentStreamHelper` (パッケージ外参照、上流残存)
- 外部: (なし)

### src/TournamentStreamHelper.py
- 内部: `.Helpers.TSHQtHelper`, `.Helpers.TSHDownloadHelper`, `.Helpers.TSHLocaleHelper`, `.Helpers.TSHDirHelper`, `.Helpers.TSHCountryHelper`, `.Helpers.TSHControllerHelper`, `.Settings.TSHSettingsWindow`, `.LayoutOptions.TSHLayoutOptionsWindow`, `.TSHHotkeys`, `.TSHPlayerListWidget`, `.TSHNotesWidget`, `.TSHCommentaryWidget`, `.TSHGameAssetManager`, `.TSHBracketWidget`, `.TSHTournamentInfoWidget`, `.TSHTournamentDataProvider`, `.TournamentDataProvider.StartGGDataProvider`, `.TSHAlertNotification`, `.TSHPlayerDB`, `.Workers`, `.StateManager`, `.SettingsManager`, `.TSHScoreboardManager`, `.TSHScoreboardStageWidget`, `.TSHStatsWidget`, `src.TSHAssetDownloader`, `src.TSHAboutWidget`, `src.TSHWebServer`, `. (ui_constants)`
- 外部: loguru, orjson, packaging.version, qdarktheme

### src/StateManager.py
- 内部: `.Helpers.TSHDictHelper`, `.SettingsManager`
- 外部: deepdiff, deepdiff.helper, loguru, orjson, PIL, requests

### src/SettingsManager.py
- 内部: `.Helpers.TSHDictHelper`
- 外部: orjson

### src/LayoutOptionsManager.py
- 内部: `.Helpers.TSHDictHelper`
- 外部: orjson

### src/ui_constants.py
- 内部: `.SettingsManager`
- 外部: (なし)

### src/Workers.py
- 内部: (なし)
- 外部: loguru

---

### src/Helpers/TSHAltTextHelper.py
- 内部: (なし)
- 外部: atproto

### src/Helpers/TSHBadWordFilter.py
- 内部: `..SettingsManager`, `.TSHLocaleHelper`
- 外部: loguru

### src/Helpers/TSHBskyHelper.py
- 内部: `.TSHAltTextHelper`, `.TSHLocaleHelper`, `src.SettingsManager`
- 外部: atproto, atproto_client

### src/Helpers/TSHControllerHelper.py
- 内部: `..SettingsManager`, `.TSHDownloadHelper`
- 外部: loguru, requests

### src/Helpers/TSHCountryHelper.py
- 内部: `..SettingsManager`, `..TournamentDataProvider`, `.TSHDictHelper`, `.TSHDirHelper`, `.TSHDownloadHelper`, `.TSHLocaleHelper`
- 外部: countryflag, loguru, orjson, requests, tqdm

### src/Helpers/TSHDictHelper.py
- 内部: (なし)
- 外部: msgpack

### src/Helpers/TSHDirHelper.py
- 内部: (なし)
- 外部: loguru

### src/Helpers/TSHDownloadHelper.py
- 内部: `..Workers`
- 外部: loguru, requests, tqdm

### src/Helpers/TSHLocaleHelper.py
- 内部: `.TSHDictHelper`, `.TSHDirHelper`, `src.SettingsManager`, `src.StateManager`
- 外部: loguru

### src/Helpers/TSHQtHelper.py
- 内部: (なし)
- 外部: loguru

### src/Helpers/TSHSponsorHelper.py
- 内部: `..StateManager`
- 外部: (なし)

### src/Helpers/TSHVersionHelper.py
- 内部: `.TSHDirHelper`
- 外部: loguru

---

### src/Settings/SettingsWidget.py
- 内部: `..SettingsManager`, `..TSHColorButton`
- 外部: (なし)

### src/Settings/TSHSettingsWindow.py
- 内部: `.SettingsWidget`, `..TSHHotkeys`, `..Helpers.TSHVersionHelper`
- 外部: (なし)

---

### src/LayoutOptions/LayoutOptionsWidget.py
- 内部: `..LayoutOptionsManager`, `..StateManager`, `..TSHColorButton`
- 外部: loguru

### src/LayoutOptions/TSHLayoutOptionsWindow.py
- 内部: `.LayoutOptionsWidget`
- 外部: (なし)

---

### src/TSHAboutWidget.py
- 内部: `.Helpers.TSHDirHelper`
- 外部: (なし)

### src/TSHAlertNotification.py
- 内部: (なし)
- 外部: loguru, requests

### src/TSHAssetDownloader.py
- 内部: `src.Helpers.TSHQtHelper`, `src.TSHGameAssetManager`, `src.Workers`
- 外部: loguru, orjson, py7zr, requests

### src/TSHBracket.py
- 内部: `.Helpers.TSHLocaleHelper`
- 外部: loguru

### src/TSHBracketView.py
- 内部: `.TSHBracket`, `.TSHPlayerList`
- 外部: loguru

### src/TSHBracketWidget.py
- 内部: `.Helpers.TSHCountryHelper`, `.Helpers.TSHDirHelper`, `.StateManager`, `.TSHGameAssetManager`, `.TSHPlayerDB`, `.TSHTournamentDataProvider`, `.TSHPlayerListSlotWidget`, `.TSHBracketView`, `.TSHPlayerList`, `.TSHBracket`, `.TSHHotkeys`
- 外部: loguru

### src/TSHColorButton.py
- 内部: (なし)
- 外部: (なし)

### src/TSHCommentaryWidget.py
- 内部: `.TSHScoreboardPlayerWidget`, `.TSHPlayerDB`, `.SettingsManager`, `.StateManager`, `.TSHGameAssetManager`, `. (ui_constants)`
- 外部: loguru

### src/TSHGameAssetManager.py
- 内部: `.StateManager`, `.Helpers.TSHLocaleHelper`, `.Workers`
- 外部: loguru, orjson, PIL, requests

### src/TSHHotkeys.py
- 内部: `.SettingsManager`, `.Helpers.TSHLocaleHelper`, `.Helpers.TSHDictHelper`
- 外部: loguru, pynput

### src/TSHIndividualGameTracker.py
- 内部: `.TSHGameAssetManager`, `.Helpers.TSHQtHelper`, `.Helpers.TSHVersionHelper`, `.SettingsManager`, `.StateManager`
- 外部: loguru

### src/TSHNotesWidget.py
- 内部: `.StateManager`
- 外部: loguru

### src/TSHPlayerDB.py
- 内部: `.Helpers.TSHDictHelper`, `.Helpers.TSHQtHelper`, `.TSHGameAssetManager`, `.SettingsManager`
- 外部: loguru, orjson

### src/TSHPlayerList.py
- 内部: `.StateManager`, `.TSHPlayerListSlotWidget`, `.TSHTournamentDataProvider`
- 外部: loguru

### src/TSHPlayerListSlotWidget.py
- 内部: `.TSHScoreboardPlayerWidget`, `.StateManager`
- 外部: loguru

### src/TSHPlayerListWidget.py
- 内部: `.Helpers.TSHCountryHelper`, `.StateManager`, `.TSHGameAssetManager`, `.TSHPlayerDB`, `.TSHPlayerList`, `.TSHPlayerListSlotWidget`, `.TSHScoreboardPlayerWidget`, `.TSHTournamentDataProvider`, `src.Helpers.TSHAltTextHelper`
- 外部: loguru

### src/TSHScoreboardManager.py
- 内部: `.TSHScoreboardWidget`, `.SettingsManager`, `.StateManager`
- 外部: loguru

### src/TSHScoreboardPlayerWidget.py
- 内部: `.Helpers.TSHCountryHelper`, `.Helpers.TSHSponsorHelper`, `.Helpers.TSHDirHelper`, `.Helpers.TSHBadWordFilter`, `.StateManager`, `.TSHGameAssetManager`, `.TSHPlayerDB`, `.Workers`, `. (ui_constants)`
- 外部: loguru

### src/TSHScoreboardStageWidget.py
- 内部: `.Helpers.TSHDictHelper`, `.Helpers.TSHDirHelper`, `.Helpers.TSHLocaleHelper` (src直指定), `.SettingsManager`, `.StateManager`, `.TSHGameAssetManager`, `src.TSHStageStrikeLogic`
- 外部: loguru, orjson, requests

### src/TSHScoreboardWidget.py
- 内部: `.Helpers.TSHDirHelper`, `.Helpers.TSHVersionHelper`, `.Helpers.TSHBskyHelper`, `.SettingsManager`, `.StateManager`, `.TSHHotkeys`, `.TSHIndividualGameTracker`, `.TSHPlayerDB`, `.TSHScoreboardPlayerWidget`, `.TSHSelectSetWindow`, `.TSHSelectStationWindow`, `.TSHStatsUtil`, `.TSHThumbnailSettingsWidget`, `.TSHTournamentDataProvider`, `.thumbnail.main_generate_thumbnail`, `src.TSHColorButton`, `. (ui_constants)`
- 外部: (なし — 標準ライブラリのみ)

### src/TSHSelectSetWindow.py
- 内部: `src.TSHTournamentDataProvider`
- 外部: loguru

### src/TSHSelectStationWindow.py
- 内部: `src.TSHTournamentDataProvider`
- 外部: loguru

### src/TSHStageStrikeLogic.py
- 内部: `.StateManager`
- 外部: loguru

### src/TSHStatsUtil.py
- 内部: `.StateManager`, `.SettingsManager`, `.TSHTournamentDataProvider`
- 外部: loguru, orjson

### src/TSHStatsWidget.py
- 内部: `.StateManager`
- 外部: loguru

### src/TSHTeamBattleModeEnum.py
- 内部: (なし)
- 外部: (なし)

### src/TSHTeamBattleWidget.py
- 内部: `.Helpers.TSHDirHelper`, `.Helpers.TSHSponsorHelper`, `.Helpers.TSHLocaleHelper`, `.StateManager`, `.TSHTeamBattleModeEnum`, `.TSHTeamPlayerWidget`
- 外部: loguru

### src/TSHTeamPlayerWidget.py
- 内部: `.Helpers.TSHCountryHelper`, `.Helpers.TSHSponsorHelper`, `.Helpers.TSHDirHelper`, `.Helpers.TSHBadWordFilter`, `.StateManager`, `.TSHGameAssetManager`, `.TSHPlayerDB`, `.TSHTeamBattleModeEnum`
- 外部: loguru

### src/TSHThumbnailSettingsWidget.py
- 内部: `.Helpers.TSHDictHelper`, `.Helpers.TSHDirHelper`, `.SettingsManager`, `.TSHGameAssetManager`, `.Workers`, `.thumbnail.main_generate_thumbnail`
- 外部: loguru

### src/TSHTournamentDataProvider.py
- 内部: `.SettingsManager`, `.StateManager`, `.TSHGameAssetManager`, `.TournamentDataProvider.TournamentDataProvider`, `.TournamentDataProvider.StartGGDataProvider`, `.Helpers.TSHVersionHelper`, `.Workers`
- 外部: loguru

### src/TSHTournamentInfoWidget.py
- 内部: `.StateManager`, `.TSHTournamentDataProvider`, `.SettingsManager`, `.Helpers.TSHLocaleHelper`, `.Helpers.TSHDirHelper`, `.TSHColorButton`
- 外部: loguru, requests

### src/TSHWebServer.py
- 内部: `.StateManager`, `.TSHWebServerActions`, `.TSHScoreboardManager`, `.TSHCommentaryWidget`, `.SettingsManager`
- 外部: flask, flask_cors, flask_socketio, loguru, orjson

### src/TSHWebServerActions.py
- 内部: `.Helpers.TSHCountryHelper`, `.Helpers.TSHQtHelper`, `.Helpers.TSHControllerHelper`, `.Helpers.TSHLocaleHelper`, `.StateManager`, `.TSHStatsUtil`, `.SettingsManager`, `.TSHGameAssetManager`, `.TSHBracketView`, `.TSHBracketWidget`, `.TSHPlayerDB`, `.TSHScoreboardWidget`, `.TSHTournamentDataProvider`, `.TSHCommentaryWidget`, `.Workers`
- 外部: flask, loguru, orjson

---

### src/TournamentDataProvider/TournamentDataProvider.py
- 内部: (なし)
- 外部: loguru, orjson, requests

### src/TournamentDataProvider/StartGGDataProvider.py
- 内部: `..Helpers.TSHCountryHelper`, `..Helpers.TSHDictHelper`, `..Helpers.TSHDirHelper`, `..Helpers.TSHLocaleHelper`, `..Helpers.TSHQtHelper`, `..TSHBracket`, `..TSHGameAssetManager`, `..TSHPlayerDB`, `..Workers`, `.TournamentDataProvider`
- 外部: loguru, orjson, requests

---

### src/thumbnail/main_generate_thumbnail.py
- 内部: `src.TSHGameAssetManager`, `src.Helpers.TSHLocaleHelper`, `src.Helpers.TSHDictHelper`, `src.Helpers.TSHAltTextHelper`, `src.Helpers.TSHDirHelper`
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

### src/convert_database_3_to_4.py
- 内部: (なし)
- 外部: (なし) — スタンドアロンスクリプト

---

## 循環 import 候補

### 直接循環（A が B を import し、B が A を import）
検出されず (0件)

### 間接循環（`from . import *` → `__init__.py` → 全モジュール再 export 経由）

以下4ファイルが `from . import ui_constants as UI` や `from . import *` でパッケージルート (`src`) を import している。`src/__init__.py` が `from .TournamentStreamHelper import *` を実行するため、import 時に TournamentStreamHelper.py のトップレベルコードが走り、全ドックウィジェットが連鎖的にロードされる。

| ファイル | import 文 |
|---------|----------|
| `src/TSHCommentaryWidget.py` | `from . import ui_constants as UI` |
| `src/TSHScoreboardPlayerWidget.py` | `from . import ui_constants as UI` |
| `src/TSHScoreboardWidget.py` | `from . import ui_constants as UI` |
| `src/TournamentStreamHelper.py` | `from . import ui_constants as UI` |

**リスク**: `from . import ui_constants` は `src/__init__.py` を経由する。`__init__.py` の `from .TournamentStreamHelper import *` が先に実行されるため、TournamentStreamHelper.py 内の import 群が全て走る。実際の動作では Python のモジュールキャッシュにより無限ループにはならないが、import 順序に暗黙の依存が生まれている。

---

## 孤立ファイル

アプリ本体の import ツリーから到達不能なファイル:

| ファイル | 状態 | 備考 |
|---------|------|------|
| `src/convert_database_3_to_4.py` | 完全孤立 | DB マイグレーション用スタンドアロンスクリプト。どこからも参照なし |
| `src/i18n/gen_translation_files.py` | CI 専用 | `.github/workflows/build_app.yml` からのみ参照 |
| `src/i18n/gen_translation_binaries.py` | CI 専用 | 同上 |
| `src/i18n/gen_round_names_files.py` | CI 専用 | 同上 |
| `src/main.py` | 上流残存 | `import TournamentStreamHelper` — プロジェクトルートの `main.py` が実際のエントリーポイント |
