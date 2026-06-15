#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from .Helpers import AutonaQtHelper
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaDirHelper import AutonaResolve
import faulthandler
import shutil
import qdarktheme
from . import ui_constants as UI
import requests
import json
import orjson
import traceback
import time
import os
import unicodedata
import socket
import sys
import qtpy
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from packaging.version import parse
from loguru import logger
from pathlib import Path

crashpath = Path('./logs/tsh-crash.log').resolve()
Path.mkdir(crashpath.parent, exist_ok=True)
crashlog = crashpath.open(mode='w')
faulthandler.enable(crashlog)

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

if parse(qtpy.QT_VERSION).major == 6:
    QImageReader.setAllocationLimit(0)

App = QApplication(sys.argv)
AutonaQtHelper.init_gui_executor()  # guaranteed to be the main thread.

fmt = ("<green>{time:YYYY-MM-DD HH:mm:ss}</green> " +
       "| <level>{level}</level> | " +
       "<yellow>{file}</yellow>:<blue>{function}</blue>:<cyan>{line}</cyan> " +
       "- <level>{message}</level>")

if sys.stdout != None:
    config = {
        "handlers": [
            {"sink": sys.stdout, "format": fmt, "level": "DEBUG"},
        ],
    }
    logger.configure(**config)
else:
    # Handle all uncaught exceptions and forward to loguru
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.critical("Uncaught exception", exc_info=(
            exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception

    class LoggerWriter(object):
        def __init__(self, writer):
            self._writer = writer
            self._msg = ''

        def write(self, message):
            self._msg = self._msg + message
            while '\n' in self._msg:
                pos = self._msg.find('\n')
                self._writer(self._msg[:pos])
                self._msg = self._msg[pos+1:]

        def flush(self):
            if self._msg != '':
                self._writer(self._msg)
                self._msg = ''

    sys.stdout = LoggerWriter(logger.info)
    sys.stderr = LoggerWriter(logger.error)

logger.add(
    "./logs/autona.log",
    format="[{time:YYYY-MM-DD HH:mm:ss}] - {level} - {file}:{function}:{line} | {message}",
    encoding="utf-8",
    level="INFO",
    rotation="20 MB"
)

logger.add(
    "./logs/tsh-error.log",
    format="[{time:YYYY-MM-DD HH:mm:ss}] - {level} - {file}:{function}:{line} | {message}",
    encoding="utf-8",
    level="ERROR",
    rotation="20 MB"
)

try:
    # Setting the icon for individual windows doesn't work on mac (and perhaps linux? unknown)
    App.setWindowIcon(QIcon(UI.icon_path("icon.png")))
except:
    logger.opt(exception=True).warning("Could not set window icon for QApplication.")


logger.critical("=== Autona IS STARTING ===")

logger.info("QApplication successfully initialized")

# Install the ClickUp-style dock tab bar theme at the application level so
# it catches every dock tab bar Qt creates, including any that appear after
# dock widgets are rearranged at runtime.
from .AutonaDockTabTheme import install_dock_tab_theme
install_dock_tab_theme()


from .AutonaSplash import LoadingOverlay, create_splash, pick_splash_messages

from contextlib import contextmanager
@contextmanager
def catchtime(msg = ''):
    from time import perf_counter
    start = perf_counter()
    yield lambda: perf_counter() - start
    logger.info(f'{msg} Time: {perf_counter() - start:.3f} seconds')

# autopep8: off
from .Settings.AutonaSettingsWindow import AutonaSettingsWindow
from .AutonaApiInfoWindow import AutonaApiInfoWindow
from .LayoutOptions.AutonaLayoutOptionsWindow import AutonaLayoutOptionsWindow
from .AutonaHotkeys import AutonaHotkeys
from .AutonaPlayerListWidget import AutonaPlayerListWidget
from .AutonaNotesWidget import AutonaNotesWidget
from .AutonaCommentaryWidget import AutonaCommentaryWidget
# from .AutonaTeamBattleWidget import AutonaTeamBattleWidget
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaBracketWidget import AutonaBracketWidget
from .AutonaTournamentInfoWidget import AutonaTournamentInfoWidget
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .TournamentDataProvider.StartGGDataProvider import StartGGDataProvider
from .AutonaAlertNotification import AutonaAlertNotification
from .AutonaUpdater import AutonaUpdater
from .AutonaPlayerDB import AutonaPlayerDB
from .Workers import *
from .StateManager import StateManager
from .SettingsManager import SettingsManager
from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Helpers.AutonaControllerHelper import AutonaControllerHelper
from .Helpers.AutonaStatusManager import AutonaStatusManager
from .AutonaScoreboardManager import AutonaScoreboardManager
from .AutonaStatsWidget import AutonaStatsWidget
from .AutonaAnalysisWidget import AutonaAnalysisWidget
from src.AutonaWebServer import WebServer, check_webserver_port
# autopep8: on

def generate_restart_messagebox(main_txt):
    messagebox = QMessageBox()
    messagebox.setWindowTitle(QApplication.translate("app", "Warning"))
    messagebox.setText(
        main_txt + "\n" + QApplication.translate("app", "The program will now close."))
    messagebox.finished.connect(QApplication.exit)
    return (messagebox)


def remove_accents_lower(input_str):
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return u"".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()


class WindowSignals(QObject):
    StopTimer = Signal()
    DetectGame = Signal(int)
    SetupAutocomplete = Signal()
    UiMounted = Signal()
    GameChanged = Signal()
    # Fired after light_mode is committed via AutonaWebServerActions.
    # Window connects this to a slot that re-applies the qdarktheme
    # palette + UI.notify_theme_change() and syncs the menu checkbox.
    LightModeChanged = Signal(bool)


class Window(QMainWindow):
    # Bump this when dock widget layout changes (e.g. adding/removing tabs)
    STATE_VERSION = 8
    signals = WindowSignals()

    def __init__(self, loop=None):
        super().__init__()

        StateManager.loop = loop
        StateManager.BlockSaving()

        AutonaLocaleHelper.LoadLocale()
        AutonaLocaleHelper.LoadRoundNames()
        self.LoadTheme()

        self.signals = WindowSignals()

        splash = create_splash()
        splash_msgs = pick_splash_messages()
        splash.set_message(splash_msgs[0], progress=0.05)

        self.programState = {}
        self.savedProgramState = {}
        self.programStateDiff = {}

        self.setWindowIcon(QIcon(UI.icon_path('icon.png')))

        if not os.path.exists("out/"):
            os.mkdir("out/")

        if not os.path.exists("./user_data/games"):
            os.makedirs("./user_data/games")

        if os.path.exists("./Autona_old.exe"):
            os.remove("./Autona_old.exe")

        for weight in ('Regular', 'Bold', 'Thin'):
            QFontDatabase.addApplicationFont(
                f'./assets/font/LINESeedJP/LINESeedJP-{weight}.ttf')
        app_font = QFont(UI.FONT_FAMILY, UI.FONT_SM)
        QApplication.instance().setFont(app_font)

        self.font_small = QFont(UI.FONT_FAMILY_CONDENSED, UI.FONT_XS)
        QFontDatabase.addApplicationFont('./assets/font/RobotoCondensed.ttf')

        self.threadpool = QThreadPool()
        self.saveMutex = QMutex()

        self.player_layouts = []

        self.allplayers = None
        self.local_players = None

        AutonaControllerHelper.instance.init()
        AutonaCountryHelper.instance.init()
        splash.set_message(splash_msgs[0], progress=0.15)

        try:
            version = json.load(
                open(AutonaResolve('./assets/versions.json'), encoding='utf-8')).get("program", "?")
        except Exception as e:
            version = "?"

        self.setGeometry(300, 300, 800, 100)
        self.setWindowTitle("Autona v"+version)

        self.setDockOptions(
            QMainWindow.DockOption.AllowTabbedDocks)

        # Dock tabs are drawn as a ClickUp-style bar on the left side of
        # the dock area (see AutonaDockTabTheme for the paint path).
        self.setTabPosition(
            Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.West)

        # Empty central widget — collapsed to 0×0 so the dock area fills
        # the window directly below the menu bar. The former "大会を選ぶ"
        # button that used to live here has moved to the top of the
        # Tournament Info (EVENT) tab below.
        central = QWidget()
        central.setFixedSize(0, 0)
        self.setCentralWidget(central)

        # "Set tournament" button. Parented/placed into the Tournament Info
        # tab after it's created below. Default sizing matches the other
        # buttons in the EVENT tab (e.g. btLoadInfoFromTournament). The
        # "primary" object name picks up the accent color from the theme.
        self.setTournamentBt = QPushButton(
            QApplication.translate("app", "Set tournament"))
        self.setTournamentBt.setObjectName("primary")
        self.setTournamentBt.setIcon(QIcon(UI.icon_path('startgg.svg')))
        self.setTournamentBt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setTournamentBt.clicked.connect(
            lambda: AutonaTournamentDataProvider.instance.SetStartggEventSlug(self))

        splash.set_message(splash_msgs[0], progress=0.25)

        self.dockWidgets = []

        # The tab bar is ordered by addDockWidget() call order when widgets
        # auto-tabify into the same area. EVENT must be first so the tab
        # strip starts with Tournament Info.
        tournamentInfo = AutonaTournamentInfoWidget()
        tournamentInfo.setWindowIcon(QIcon(UI.icon_path('tournament.svg')))
        tournamentInfo.setObjectName(
            QApplication.translate("app", "Tournament Info"))
        # Place the "Set tournament" button at the very top of this tab.
        tournamentInfo.InsertHeaderWidget(self.setTournamentBt)
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, tournamentInfo)
        self.dockWidgets.append(tournamentInfo)

        if not SettingsManager.Get("general.disable_thumbnail_widget", False):
            from .AutonaThumbnailSettingsWidget import AutonaThumbnailSettingsWidget
            thumbnailSetting = AutonaThumbnailSettingsWidget()
            thumbnailSetting.setWindowIcon(QIcon(UI.icon_path('thumbnail.svg')))
            thumbnailSetting.setObjectName(
                QApplication.translate("app", "Thumbnail Settings"))
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea, thumbnailSetting)
            self.dockWidgets.append(thumbnailSetting)

        from .AutonaResultSettingsWidget import AutonaResultSettingsWidget
        # Held so the "対戦形式" menu can switch the result layout to the team
        # standings variant when team mode is selected.
        self.resultSetting = resultSetting = AutonaResultSettingsWidget()
        resultSetting.setWindowIcon(QIcon(UI.icon_path('result.svg')))
        resultSetting.setObjectName(
            QApplication.translate("app", "Result Settings"))
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, resultSetting)
        self.dockWidgets.append(resultSetting)

        bracket = AutonaBracketWidget()
        bracket.setWindowIcon(QIcon(UI.icon_path('bracket.svg')))
        bracket.setObjectName(
            QApplication.translate("app", "Bracket"))
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, bracket)
        self.dockWidgets.append(bracket)
        splash.set_message(splash_msgs[0], progress=0.40)

        # teamBattle = AutonaTeamBattleWidget()
        # teamBattle.setWindowIcon(QIcon(UI.icon_path('info.svg')))
        # teamBattle.setObjectName(
        #     QApplication.translate("app", "Crew/Team Battle"))
        # self.addDockWidget(
        #     Qt.DockWidgetArea.BottomDockWidgetArea, teamBattle)
        # self.dockWidgets.append(teamBattle)

        self.scoreboard = AutonaScoreboardManager.instance
        self.scoreboard.setWindowIcon(QIcon(UI.icon_path('scoreboard.svg')))
        self.scoreboard.setObjectName(
            QApplication.translate("app", "Scoreboard"))
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, self.scoreboard)
        self.dockWidgets.append(self.scoreboard)
        AutonaScoreboardManager.instance.setWindowTitle(
            QApplication.translate("app", "Scoreboard"))
        splash.set_message(splash_msgs[1], progress=0.55)

        commentary = AutonaCommentaryWidget()
        commentary.setWindowIcon(QIcon(UI.icon_path('commentary.svg')))
        commentary.setObjectName(QApplication.translate("app", "Commentary"))
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, commentary)
        self.dockWidgets.append(commentary)

        self.webserver = WebServer(
            parent=self, commentaryWidget=commentary)
        check_webserver_port(self.webserver.port, parent=self)
        self.webserver.start()
        self.signals.GameChanged.connect(self.webserver.ws_program_state)
        self.signals.GameChanged.connect(self.webserver.ws_get_characters)
        # Apply light/dark switches that originate from
        # AutonaWebServerActions.set_light_mode (PyQt menu or React toggle)
        # on the GUI thread. Direct connection because emit() is already
        # marshalled to GUI thread by the Action.
        self.signals.LightModeChanged.connect(self._applyLightMode)
        splash.set_message(splash_msgs[1], progress=0.70)

        playerList = AutonaPlayerListWidget()
        playerList.setWindowIcon(QIcon(UI.icon_path('players.svg')))
        playerList.setObjectName(QApplication.translate("app", "Player List"))
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, playerList)
        self.dockWidgets.append(playerList)

        notes = AutonaNotesWidget()
        notes.setWindowIcon(QIcon(UI.icon_path('notes.svg')))
        notes.setObjectName(QApplication.translate("app", "Additional Notes"))
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, notes)
        self.dockWidgets.append(notes)

        self.statsWidget = AutonaStatsWidget()
        self.statsWidget.setWindowIcon(QIcon(UI.icon_path('stats.svg')))
        self.statsWidget.setObjectName(QApplication.translate("app", "Statistics"))
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.statsWidget)
        self.dockWidgets.append(self.statsWidget)

        self.analysisWidget = AutonaAnalysisWidget()
        self.analysisWidget.setWindowIcon(QIcon(UI.icon_path('analysis.svg')))
        self.analysisWidget.setObjectName(QApplication.translate("app", "Analysis"))
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.analysisWidget)
        self.dockWidgets.append(self.analysisWidget)

        splash.set_message(splash_msgs[1], progress=0.85)

        # Tab order: EVENT, SCORE, THUMB, STATS, ANALYSIS, PLAYERS, COMMS,
        # RESULT, BRACKET, NOTES, RULES (RULES hidden by default).
        previous = tournamentInfo
        for nxt in (
            self.scoreboard,
            thumbnailSetting if not SettingsManager.Get(
                "general.disable_thumbnail_widget", False) else None,
            self.statsWidget,
            self.analysisWidget,
            playerList,
            commentary,
            resultSetting,
            bracket,
            notes,
        ):
            if nxt is None:
                continue
            self.tabifyDockWidget(previous, nxt)
            previous = nxt

        tournamentInfo.raise_()

        for dw in self.dockWidgets:
            dw.setMinimumWidth(0)
            # Call via class to avoid shadowing by instance attrs (e.g. AutonaCommentaryWidget.widget)
            inner = QDockWidget.widget(dw)
            if inner:
                inner.setMinimumWidth(0)
                inner.setSizePolicy(
                    QSizePolicy.Ignored, inner.sizePolicy().verticalPolicy())
            # Hide the gray title strip at the top of every dock tab.
            # The left sidebar already identifies the active tab, so the
            # redundant header just eats vertical space.
            dw.setTitleBarWidget(QWidget())
            dw.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)

        self.setMinimumWidth(480)

        # ── Menu Bar ──────────────────────────────────────────────
        mb = self.menuBar()

        # ── Edit menu (former hamburger menu) ──
        editMenu = mb.addMenu(QApplication.translate("app", "Edit"))

        action = editMenu.addAction(
            QApplication.translate("app", "Always on top"))
        action.setCheckable(True)
        action.toggled.connect(self.ToggleAlwaysOnTop)

        action = editMenu.addAction(
            QApplication.translate("app", "Light mode"))
        action.setCheckable(True)
        action.setChecked(SettingsManager.Get("light_mode", False))
        action.toggled.connect(self.ToggleLightMode)
        # Held so the Action-driven slot can sync the checkbox state when
        # the toggle originates from React or another remote client.
        self._lightModeAction = action

        editMenu.addSeparator()

        toggleWidgets = editMenu.addMenu(
            QApplication.translate("app", "Toggle widgets"))
        toggleWidgets.addAction(self.scoreboard.toggleViewAction())
        toggleWidgets.addAction(commentary.toggleViewAction())
        if not SettingsManager.Get("general.disable_thumbnail_widget", False):
            toggleWidgets.addAction(thumbnailSetting.toggleViewAction())
        toggleWidgets.addAction(resultSetting.toggleViewAction())
        toggleWidgets.addAction(tournamentInfo.toggleViewAction())
        toggleWidgets.addAction(playerList.toggleViewAction())
        toggleWidgets.addAction(bracket.toggleViewAction())
        toggleWidgets.addAction(notes.toggleViewAction())
        toggleWidgets.addAction(self.statsWidget.toggleViewAction())

        action = editMenu.addAction(
            QApplication.translate("app", "Migrate Layout"))
        action.triggered.connect(self.MigrateWindow)

        editMenu.addSeparator()

        # Language sub-menus
        program_language_messagebox = generate_restart_messagebox(
            QApplication.translate("app", "Program language changed successfully."))
        languageSelect = editMenu.addMenu(
            QApplication.translate("app", "Program Language"))
        languageSelectGroup = QActionGroup(languageSelect)
        languageSelectGroup.setExclusive(True)
        action = languageSelect.addAction(
            QApplication.translate("app", "System language"))
        languageSelectGroup.addAction(action)
        action.setCheckable(True)
        action.setChecked(True)
        action.triggered.connect(lambda x=None: [
            SettingsManager.Set("program_language", "default"),
            program_language_messagebox.exec()
        ])
        for code, language in AutonaLocaleHelper.languages.items():
            action = languageSelect.addAction(f"{language[0]} / {language[1]}")
            action.setCheckable(True)
            languageSelectGroup.addAction(action)
            action.triggered.connect(lambda x=None, c=code: [
                SettingsManager.Set("program_language", c),
                program_language_messagebox.exec()
            ])
            if SettingsManager.Get("program_language") == code:
                action.setChecked(True)

        game_asset_language_messagebox = generate_restart_messagebox(
            QApplication.translate("app", "Game Asset Language changed successfully."))
        languageSelect = editMenu.addMenu(
            QApplication.translate("app", "Game Asset Language"))
        languageSelectGroup = QActionGroup(languageSelect)
        languageSelectGroup.setExclusive(True)
        action = languageSelect.addAction(
            QApplication.translate("app", "Same as program language"))
        languageSelectGroup.addAction(action)
        action.setCheckable(True)
        action.setChecked(True)
        action.triggered.connect(lambda x=None: [
            SettingsManager.Set("game_asset_language", "default"),
            game_asset_language_messagebox.exec()
        ])
        for code, language in AutonaLocaleHelper.languages.items():
            action = languageSelect.addAction(f"{language[0]} / {language[1]}")
            action.setCheckable(True)
            languageSelectGroup.addAction(action)
            action.triggered.connect(lambda x=None, c=code: [
                SettingsManager.Set("game_asset_language", c),
                game_asset_language_messagebox.exec()
            ])
            if SettingsManager.Get("game_asset_language") == code:
                action.setChecked(True)

        fg_language_messagebox = generate_restart_messagebox(
            QApplication.translate("app", "Tournament term language changed successfully."))
        languageSelect = editMenu.addMenu(
            QApplication.translate("app", "Tournament term language"))
        languageSelectGroup = QActionGroup(languageSelect)
        languageSelectGroup.setExclusive(True)
        action = languageSelect.addAction(
            QApplication.translate("app", "Same as program language"))
        languageSelectGroup.addAction(action)
        action.setCheckable(True)
        action.setChecked(True)
        action.triggered.connect(lambda x=None: [
            SettingsManager.Set("fg_term_language", "default"),
            fg_language_messagebox.exec()
        ])
        for code, language in AutonaLocaleHelper.languages.items():
            action = languageSelect.addAction(f"{language[0]} / {language[1]}")
            action.setCheckable(True)
            languageSelectGroup.addAction(action)
            action.triggered.connect(lambda x=None, c=code: [
                SettingsManager.Set("fg_term_language", c),
                fg_language_messagebox.exec()
            ])
            if SettingsManager.Get("fg_term_language") == code:
                action.setChecked(True)

        editMenu.addSeparator()

        # Check for updates - promoted to a top-level Edit item for
        # discoverability. The in-app updater pulls the latest release from the
        # public distribution repo (see AutonaUpdater.GITHUB_REPO).
        action = editMenu.addAction(
            QApplication.translate("app", "Check for updates"))
        action.setIcon(QIcon(UI.icon_path('update_circle.svg')))
        action.triggered.connect(
            lambda x=None: AutonaUpdater.instance.CheckForUpdates(silent=False))

        editMenu.addSeparator()

        # Help sub-menu
        help_messagebox = QMessageBox()
        help_messagebox.setWindowTitle(
            QApplication.translate("app", "Warning"))
        help_messagebox.setText(QApplication.translate(
            "app", "A new window has been opened in your default webbrowser."))
        helpMenu = editMenu.addMenu(
            QApplication.translate("app", "Help"))
        action = helpMenu.addAction(
            QApplication.translate("app", "Open the Wiki"))
        wiki_url = "https://github.com/yuya-tagu/Autona/wiki"
        action.triggered.connect(lambda x=None: [
            QDesktopServices.openUrl(QUrl(wiki_url)),
            help_messagebox.exec()
        ])
        action = helpMenu.addAction(
            QApplication.translate("app", "Look for Help on the forum"))
        help_url = "https://github.com/yuya-tagu/Autona/discussions/categories/q-a"
        action.triggered.connect(lambda x=None: [
            QDesktopServices.openUrl(QUrl(help_url)),
            help_messagebox.exec()
        ])
        action = helpMenu.addAction(
            QApplication.translate("app", "Report a bug"))
        issues_url = "https://github.com/yuya-tagu/Autona/issues"
        action.triggered.connect(lambda x=None: [
            QDesktopServices.openUrl(QUrl(issues_url)),
            help_messagebox.exec()
        ])

        editMenu.addSeparator()

        self.settingsWindow = AutonaSettingsWindow(self)
        action = editMenu.addAction(
            QApplication.translate("Settings", "Settings"))
        action.setIcon(QIcon(UI.icon_path('settings.svg')))
        action.triggered.connect(lambda: self.settingsWindow.show())

        # ── Match format menu (1v1 / Team / Crewbattle) ──
        # Toggles which scoreboard the SCORE tab shows: board 1 (1v1),
        # board 2 (team) or board 3 (crewbattle). The choice is persisted and
        # applied after the scoreboards are created (see CreateScoreboard
        # call below).
        formatMenu = mb.addMenu(QApplication.translate("app", "Rules"))
        formatGroup = QActionGroup(formatMenu)
        formatGroup.setExclusive(True)
        battleFormat = self._resolveBattleFormat()

        self.singlesFormatAction = formatMenu.addAction("1 vs 1")
        self.singlesFormatAction.setCheckable(True)
        self.singlesFormatAction.setChecked(battleFormat == "singles")
        formatGroup.addAction(self.singlesFormatAction)
        self.singlesFormatAction.triggered.connect(
            lambda x=None: self.SetBattleFormat("singles"))

        self.teamFormatAction = formatMenu.addAction("Team")
        self.teamFormatAction.setCheckable(True)
        self.teamFormatAction.setChecked(battleFormat == "team")
        formatGroup.addAction(self.teamFormatAction)
        self.teamFormatAction.triggered.connect(
            lambda x=None: self.SetBattleFormat("team"))

        self.crewFormatAction = formatMenu.addAction("Crewbattle")
        self.crewFormatAction.setCheckable(True)
        self.crewFormatAction.setChecked(battleFormat == "crew")
        formatGroup.addAction(self.crewFormatAction)
        self.crewFormatAction.triggered.connect(
            lambda x=None: self.SetBattleFormat("crew"))

        # ── Startgg menu ──
        startggMenu = mb.addMenu(QApplication.translate("app", "Startgg"))

        self.setTournamentAction = startggMenu.addAction(
            QApplication.translate("app", "Set tournament"))
        self.setTournamentAction.triggered.connect(
            lambda: AutonaTournamentDataProvider.instance.SetStartggEventSlug(self))

        startggMenu.addAction(
            QApplication.translate("app", "Unset tournament")).triggered.connect(
            lambda: AutonaTournamentDataProvider.instance.SetTournament(None))

        startggMenu.addSeparator()

        if not SettingsManager.Get("general.hide_track_player", False):
            self.loadPlayerSetAction = startggMenu.addAction(
                QApplication.translate("app", "Load tournament and sets from StartGG user"))
            self.loadPlayerSetAction.setIcon(QIcon(UI.icon_path('startgg.svg')))
            self.loadPlayerSetAction.triggered.connect(self.LoadUserSetClicked)

            startggMenu.addAction(
                QApplication.translate("app", "Set StartGG user account")).triggered.connect(
                self.LoadUserSetOptionsClicked)

            AutonaTournamentDataProvider.instance.signals.user_updated.connect(
                self.UpdateUserSetButton)
            AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
                self.UpdateUserSetButton)
            self.UpdateUserSetButton()

            startggMenu.addSeparator()

        self.pullCompletedSetsAction = startggMenu.addAction(
            QApplication.translate("app", "Pull Latest Completed Sets from StartGG"))
        self.pullCompletedSetsAction.setIcon(QIcon(UI.icon_path('startgg.svg')))
        self.pullCompletedSetsAction.triggered.connect(
            AutonaTournamentDataProvider.instance.GetCompletedSets)

        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            self.UpdateLastSetsButton)
        AutonaTournamentDataProvider.instance.signals.completed_sets_updated.connect(
            self.LoadCompletedSetsClicked)
        self.UpdateLastSetsButton()

        # ── Title menu (game select) ──
        titleMenu = mb.addMenu(QApplication.translate("app", "Title"))

        self.gameSelect = QComboBox()
        self.gameSelect.setEditable(True)
        self.gameSelect.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.gameSelect.completer().setCompletionMode(QCompleter.PopupCompletion)
        proxyModel = QSortFilterProxyModel()
        proxyModel.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        proxyModel.setSourceModel(self.gameSelect.model())
        self.gameSelect.model().setParent(proxyModel)
        self.gameSelect.setModel(proxyModel)
        self.gameSelect.setFont(self.font_small)
        self.gameSelect.activated.connect(
            lambda x: AutonaGameAssetManager.instance.LoadGameAssets(self.gameSelect.currentData()))
        AutonaGameAssetManager.instance.signals.onLoad.connect(self.SetGame)
        AutonaGameAssetManager.instance.signals.onLoadAssets.connect(self.ReloadGames)
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(self.SetGame)
        AutonaTournamentDataProvider.instance.signals.tournament_url_update.connect(
            self.Signal_GameChange)

        gameSelectAction = QWidgetAction(titleMenu)
        gameSelectAction.setDefaultWidget(self.gameSelect)
        titleMenu.addAction(gameSelectAction)

        # Modded content
        self.moddedContentCheck = QCheckBox(
            QApplication.translate("app", "Modded content"))
        self.moddedContentCheck.setVisible(False)
        self.moddedContentCheck.setChecked(False)
        self.moddedContentAction = QWidgetAction(titleMenu)
        self.moddedContentAction.setDefaultWidget(self.moddedContentCheck)
        self.moddedContentAction.setVisible(False)
        titleMenu.addAction(self.moddedContentAction)

        AutonaGameAssetManager.instance.signals.onLoad.connect(lambda x=None: [
            self.moddedContentAction.setVisible(AutonaGameAssetManager.instance.has_modded_content),
            self.moddedContentCheck.setChecked(StateManager.Get("game").get("mods_active", False))
        ])
        self.moddedContentCheck.stateChanged.connect(lambda x=None: [
            AutonaGameAssetManager.instance.LoadGameAssets(
                self.gameSelect.currentData(),
                mods_active=self.moddedContentCheck.isChecked(),
                mods_reload_mode=True)
        ])
        self.gameSelect.activated.connect(
            lambda x=None: self.moddedContentCheck.setChecked(False))
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            lambda x=None: self.moddedContentCheck.setChecked(False))

        # ── API Settings menu ──
        # Entry point for the External Control API (/api/v1/) reference +
        # tester window. The window itself is built lazily in UiMounted below.
        self.apiInfoWindow = AutonaApiInfoWindow(self)
        apiMenu = mb.addMenu(QApplication.translate("app", "API Settings"))
        action = apiMenu.addAction(
            QApplication.translate("api", "Open reference & tester"))
        action.triggered.connect(lambda: self.apiInfoWindow.show())

        # Create all boards (1v1 + team + crew) into the SCORE dock, then apply
        # the persisted battle format so the right board + result layout are
        # shown on startup. The "Rules" menu switches between them at runtime.
        AutonaScoreboardManager.instance.CreateScoreboard(singlePlayer=True)
        battleFormat = self._resolveBattleFormat()
        AutonaScoreboardManager.instance.SetBattleFormat(battleFormat)
        self.resultSetting.SetBattleFormat(battleFormat)

        self.ReloadGames()

        self.qtSettings = QSettings("HiveeWorks", "Autona")

        if self.qtSettings.value("geometry"):
            self.restoreGeometry(self.qtSettings.value("geometry"))

        stateKey = f"windowState_v{self.STATE_VERSION}"
        if self.qtSettings.value(stateKey):
            self.restoreState(self.qtSettings.value(stateKey))

        splash.set_message(splash_msgs[2], progress=1.0)
        # Pre-configure dock tab bars so the first paint uses our styled
        # geometry instead of flashing Qt's default tab visuals.
        from .AutonaDockTabTheme import install_dock_tab_theme
        install_dock_tab_theme().prime(self)
        splash.finish(self)
        self.show()

        # Cover the main window with a spinner until Qt finishes its
        # first-pass layout + paint, so the user doesn't see half-drawn
        # widgets jump into place. The overlay dismisses itself after a
        # short delay — long enough for one or two event-loop passes.
        self._loading_overlay = LoadingOverlay(self)
        self._loading_overlay.start()
        QTimer.singleShot(1100, self._loading_overlay.finish)

        # Prevent tab labels from being elided ("…") — show full text so the
        # tab bar uses horizontal scrolling if it overflows.
        from qtpy.QtWidgets import QTabBar
        for bar in self.findChildren(QTabBar):
            bar.setElideMode(Qt.TextElideMode.ElideNone)
            bar.setUsesScrollButtons(True)

        AutonaCountryHelper.LoadCountries()
        self.settingsWindow.UiMounted()
        self.apiInfoWindow.UiMounted()
        # self.layoutOptions.UiMounted()
        AutonaTournamentDataProvider.instance.UiMounted()
        AutonaGameAssetManager.instance.UiMounted()
        AutonaAlertNotification.instance.UiMounted()
        # Silent startup check runs only for the frozen distribution — in
        # dev mode (python main.py) we can't self-install, so skip the
        # noise of a "can't auto-install" dialog at every launch.
        if getattr(sys, "frozen", False):
            AutonaUpdater.instance.CheckForUpdates(silent=True)
        AutonaHotkeys.instance.UiMounted(self)
        AutonaPlayerDB.signals.db_updated.connect(
            self.webserver.ws_playerdb
        )
        AutonaPlayerDB.LoadDB()

        # Footer status bar — surfaces the current long-running task
        # (Start.gg fetches, asset reloads, etc.) so the user can tell
        # the app is busy rather than frozen.
        self._statusBar = self.statusBar()
        self._statusBar.setSizeGripEnabled(False)
        self._statusLabel = QLabel("")
        self._statusLabel.setStyleSheet(
            "color: #888; padding: 0 8px;")
        self._statusBar.addWidget(self._statusLabel, 1)
        AutonaStatusManager.signals.changed.connect(
            self._on_status_changed)

        StateManager.ReleaseSaving()

        StateManager.Unset("completed_sets")

    @Slot(str)
    def _on_status_changed(self, message: str):
        # Runs on the GUI thread thanks to Qt's auto signal connection.
        self._statusLabel.setText(message)

    def SetGame(self, mods_active = False):
        index = next((i for i in range(self.gameSelect.model().rowCount()) if self.gameSelect.itemText(i) == AutonaGameAssetManager.instance.selectedGame.get(
            "name") or self.gameSelect.itemText(i) == AutonaGameAssetManager.instance.selectedGame.get("codename")), None)
        if index is not None:
            self.gameSelect.setCurrentIndex(index)
            self.signals.GameChanged.emit()

    def Signal_GameChange(self, url):
        if url == "":
            self.gameSelect.setCurrentIndex(0)
            AutonaGameAssetManager.instance.selectedGame = {}

    def UpdateUserSetButton(self):
        if not hasattr(self, 'loadPlayerSetAction'):
            return
        if SettingsManager.Get("StartGG_user"):
            self.loadPlayerSetAction.setText(
                QApplication.translate("app", "Load tournament and sets from StartGG user")+" "+QApplication.translate("punctuation", "(")+f"{SettingsManager.Get('StartGG_user')}"+QApplication.translate("punctuation", ")"))
            self.loadPlayerSetAction.setEnabled(True)
        else:
            self.loadPlayerSetAction.setText(
                QApplication.translate("app", "Load tournament and sets from StartGG user"))
            self.loadPlayerSetAction.setEnabled(False)
    
    def UpdateLastSetsButton(self):
        if AutonaTournamentDataProvider.instance and AutonaTournamentDataProvider.instance.provider and AutonaTournamentDataProvider.instance.provider.name == "StartGG":
            self.pullCompletedSetsAction.setEnabled(True)
        else:
            self.pullCompletedSetsAction.setEnabled(False)

    def LoadUserSetClicked(self):
        self.scoreboard.lastSetSelected = None
        if SettingsManager.Get("StartGG_user"):
            AutonaTournamentDataProvider.instance.provider = StartGGDataProvider(
                "start.gg/",
                AutonaTournamentDataProvider.instance.threadPool,
                AutonaTournamentDataProvider.instance
            )
            AutonaTournamentDataProvider.instance.LoadUserSet(
                self.scoreboard.GetScoreboard(1), SettingsManager.Get("StartGG_user"))
    
    def LoadCompletedSetsClicked(self, data):
        StateManager.Set("completed_sets", {index+1: set for index, set in enumerate(data)})

    def LoadUserSetOptionsClicked(self):
        AutonaTournamentDataProvider.instance.SetUserAccount(
            self.scoreboard, startgg=True)

    def closeEvent(self, event):
        logger.info("Shutting down...")
        self.qtSettings.setValue("geometry", self.saveGeometry())
        self.qtSettings.setValue(f"windowState_v{self.STATE_VERSION}", self.saveState())

        tmpDir = AutonaResolve("tmp")
        if os.path.isdir(tmpDir):
            shutil.rmtree(tmpDir)

        try:
            crashlog.close()
            if crashpath.stat().st_size == 0:
                crashpath.unlink()
        except:
            pass

        # For whatever reason, the webserver thread won't respond to the quit() signal nicely.
        # This is usually unsafe, but there's no easy way to terminate the flask server,
        # so we hard-kill its thread as we're shutting down to prevent exit-crashes.
        #
        # Given that this webserver is local in scope, it's reasonable to not do any
        # connection-draining process.
        try:
            web_socket_fd = os.environ.get('WERKZEUG_SOCKET_FD', None)
            if web_socket_fd:
                sock = socket.socket(fileno=int(web_socket_fd))
                sock.close()
        except Exception as e:
            logger.warning("Error closing web socket on shutdown", exc_info=True)

        self.webserver.terminate()
        self.webserver.wait(10000)  # 10 seconds grace period.

        for window in QApplication.allWindows():
            if window != self:
                window.close()

        super().closeEvent(event)

    def ReloadGames(self):
        logger.info("Reload games")
        with StateManager.SaveBlock():
            self.gameSelect.setModel(QStandardItemModel())
            self.gameSelect.addItem("", 0)
            for i, game in enumerate(AutonaGameAssetManager.instance.games.items()):
                logo_path = game[1].get("logo_path")
                if logo_path:
                    icon = QIcon(QPixmap(
                        QImage(logo_path).scaled(
                            64, 64,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        )
                    ))
                else:
                    icon = QIcon()
                if game[1].get("name"):
                    self.gameSelect.addItem(icon, game[1].get("name"), i+1)
                else:
                    self.gameSelect.addItem(icon, game[0], i+1)
            self.gameSelect.setIconSize(QSize(64, 64))
            self.gameSelect.setFixedHeight(32)
            view = QListView()
            view.setIconSize(QSize(64, 64))
            view.setStyleSheet("QListView::item { height: 32px; }")
            self.gameSelect.setView(view)
            self.gameSelect.model().sort(0)
            self.SetGame()

    def DetectGameFromId(self, id):
        def detect_smashgg_id_match(games, game, id):
            result = str(games[game].get("smashgg_game_id", "")) == str(id)
            if not result:
                alternates = games[game].get("alternate_versions")
                alternates_ids = []
                for alternate in alternates:
                    if alternate.get("smashgg_game_id"):
                        alternates_ids.append(str(alternate.get("smashgg_game_id")))
                result = str(id) in alternates_ids
            return(result)

        game = next(
            (i+1 for i, game in enumerate(self.games)
             if detect_smashgg_id_match(self.games,game,id)),
            None
        )

        if game is not None and self.gameSelect.currentIndex() != game:
            self.gameSelect.setCurrentIndex(game)
            self.LoadGameAssets(game)

    def ToggleAlwaysOnTop(self, checked):
        if checked:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.show()

    @staticmethod
    def _resolveBattleFormat() -> str:
        # `scoreboard.battle_format` (str) is the source of truth: "singles",
        # "team" or "crew". Fall back to the legacy `scoreboard.team_mode`
        # bool when the new key is absent so existing settings keep working.
        fmt = SettingsManager.Get("scoreboard.battle_format", None)
        if fmt in ("singles", "team", "crew"):
            return fmt
        return "team" if SettingsManager.Get("scoreboard.team_mode", False) else "singles"

    def SetBattleFormat(self, fmt):
        # Persist + apply the battle format chosen from the "Rules" menu.
        # Switches the SCORE tab between board 1 (1v1), board 2 (team) and
        # board 3 (crew), and the RESULT tab to the matching standings layout.
        # Accepts a bool for backward compatibility with older callers.
        if isinstance(fmt, bool):
            fmt = "team" if fmt else "singles"
        if fmt not in ("singles", "team", "crew"):
            fmt = "singles"
        # battle_format is the source of truth; mirror the legacy team_mode
        # bool so rolling back to an older build still restores 1v1/team.
        SettingsManager.Set("scoreboard.battle_format", fmt)
        SettingsManager.Set("scoreboard.team_mode", fmt == "team")
        AutonaScoreboardManager.instance.SetBattleFormat(fmt)
        if getattr(self, "resultSetting", None) is not None:
            self.resultSetting.SetBattleFormat(fmt)

    def ToggleLightMode(self, checked):
        # Route through the WebServer Action so PyQt and React go through
        # the same path (Design.md §5.4.2 / §8 T-10). The Action persists
        # via SettingsManager, mirrors into StateManager.ui.light_mode and
        # emits LightModeChanged back to this Window so the QSS reapply
        # happens on the GUI thread regardless of caller.
        actions = getattr(WebServer, 'actions', None)
        if actions is not None:
            actions.set_light_mode(checked)
        else:
            # WebServer not yet initialized (very early startup) — fall
            # back to a direct path so the menu still works.
            SettingsManager.Set("light_mode", bool(checked))
            self._applyLightMode(bool(checked))

    def _applyLightMode(self, value):
        # GUI-thread slot fired by AutonaWebServerActions.set_light_mode.
        # Re-applies qdarktheme + ui_constants notify_theme_change, and
        # syncs the menu checkbox in case the toggle originated remotely.
        self.LoadTheme()
        UI.notify_theme_change()
        action = getattr(self, '_lightModeAction', None)
        if action is not None and action.isChecked() != bool(value):
            # Signals are blocked so this doesn't loop back into
            # ToggleLightMode and re-trigger the Action.
            action.blockSignals(True)
            try:
                action.setChecked(bool(value))
            finally:
                action.blockSignals(False)

    def LoadTheme(self):
        light = bool(SettingsManager.Get("light_mode", False))
        if light:
            qdarktheme.setup_theme(
                "light",
                custom_colors=UI.LIGHT_CUSTOM_COLORS,
                additional_qss=UI.global_qss())
        else:
            qdarktheme.setup_theme(
                "dark",
                custom_colors=UI.DARK_CUSTOM_COLORS,
                additional_qss=UI.global_qss())

        # Broadcast cache for React / OBS HTML clients. Source-of-truth
        # remains SettingsManager.light_mode (Design.md §5.4.1); this
        # mirror exists so subscribers can react via the standard
        # state_updated channel (Design.md §5.4.2).
        StateManager.Set("ui.light_mode", light)

    def MigrateWindow(self):
        migrateWindow = QDialog(self)
        migrateWindow.setWindowTitle(
            QApplication.translate("app", "Migrate Scoreboard Layout"))
        migrateWindow.setMinimumWidth(800)
        vbox = QVBoxLayout()
        migrateWindow.setLayout(vbox)
        hbox = QHBoxLayout()
        label = QLabel(QApplication.translate("app", "File Path"))
        filePath = QLineEdit()
        fileExplorer = QPushButton(
            text=QApplication.translate("app", "Find File..."))
        hbox.addWidget(label)
        hbox.addWidget(filePath)
        hbox.addWidget(fileExplorer)
        vbox.addLayout(hbox)

        migrate = QPushButton(
            text=QApplication.translate("app", "Migrate Layout"))

        def open_dialog():
            fname, _ok = QFileDialog.getOpenFileName(
                migrateWindow,
                QApplication.translate("app", "Open Layout Javascript File"),
                os.getcwd(),
                QApplication.translate("app", "Javascript File") + "  (*.js)",
            )
            if fname:
                filePath.setText(str(fname))

        fileExplorer.clicked.connect(open_dialog)

        def MigrateLayout():
            data = None
            with open(filePath.text(), 'r') as file:
                data = file.read()

                data = data.replace("data.score.", "data.score[1].")
                data = data.replace("oldData.score.", "oldData.score[1].")
                data = data.replace(
                    "_.get(data, \"score.stage_strike.", "_.get(data, \"score.1.stage_strike.")
                data = data.replace(
                    "_.get(oldData, \"score.stage_strike.", "_.get(oldData, \"score.1.stage_strike.")
                data = data.replace(
                    "source: `score.team.${t + 1}`", "source: `score.1.team.${t + 1}`")
                data = data.replace(
                    "data.score[1].ruleset", "data.score.ruleset")

            with open(filePath.text(), 'w') as file:
                file.write(data)

            logger.info("Completed Layout Migration at: " + filePath.text())

            completeDialog = QDialog(migrateWindow)
            completeDialog.setWindowTitle(
                QApplication.translate("app", "Migration Complete"))
            completeDialog.setMinimumWidth(500)
            vbox2 = QVBoxLayout()
            completeDialog.setLayout(vbox2)
            completeText = QLabel(QApplication.translate(
                "app", "Layout Migration has completed!"))
            completeText.setAlignment(Qt.AlignmentFlag.AlignCenter)
            closeButton = QPushButton(
                text=QApplication.translate("app", "Close Window"))
            vbox2.addWidget(completeText)
            vbox2.addWidget(closeButton)
            closeButton.clicked.connect(completeDialog.close)
            completeDialog.show()

        migrate.clicked.connect(MigrateLayout)

        vbox.addWidget(migrate)

        migrateWindow.show()
