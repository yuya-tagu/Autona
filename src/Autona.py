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

# NOTE: the Windows AppUserModelID is set at the very top of main.py
# (before this module is imported), so the taskbar groups the app under
# our identity rather than the host python.exe.

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
    # Use the multi-resolution .ico (not .png): a single PNG renders in the
    # title bar but Windows fails to build a taskbar-sized HICON from it and
    # falls back to the host python.exe icon. The .ico embeds 16/32/48/256
    # so the taskbar button picks up the Autona mark.
    App.setWindowIcon(QIcon(UI.icon_path("icon.ico")))
except:
    logger.opt(exception=True).warning("Could not set window icon for QApplication.")


logger.critical("=== Autona IS STARTING ===")

logger.info("QApplication successfully initialized")

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
from .AutonaIntegrationGuideWindow import AutonaIntegrationGuideWindow
from .AutonaObsGuideWindow import AutonaObsGuideWindow
from .AutonaGameAssetGuideWindow import AutonaGameAssetGuideWindow
from .LayoutOptions.AutonaLayoutOptionsWindow import AutonaLayoutOptionsWindow
from .AutonaHotkeys import AutonaHotkeys
from .AutonaPlayerListWidget import AutonaPlayerListWidget
from .AutonaCommentaryWidget import AutonaCommentaryWidget
# from .AutonaTeamBattleWidget import AutonaTeamBattleWidget
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaTop8Widget import AutonaTop8Widget
from .AutonaTournamentInfoWidget import AutonaTournamentInfoWidget
from .AutonaBoardWidget import AutonaBoardWidget
from .AutonaSidebar import AutonaSidebar
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaAlertNotification import AutonaAlertNotification
from .AutonaUpdater import AutonaUpdater, CleanupUpdateLeftovers
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


# Single source for the corporate / commercial-use inquiry contact, used by
# the Help menu, the About dialog and (manually kept in sync) TERMS.md §9.
# Currently the official Discord; swap to the dedicated inquiry form URL once
# it exists — this constant is the only place in code to change.
DISCORD_URL = "https://discord.gg/yAFAmhaUqn"
BUSINESS_INQUIRY_URL = DISCORD_URL


class WindowSignals(QObject):
    StopTimer = Signal()
    DetectGame = Signal(int)
    SetupAutocomplete = Signal()
    UiMounted = Signal()
    GameChanged = Signal()


class Window(QMainWindow):
    # Bump this when the navigation layout changes.
    # v9: dock objectNames switched from translated strings to stable English
    # literals; old saved layouts keyed by translated names are discarded.
    # v13: tabified docks + QMenuBar replaced by a left sidebar + QStackedWidget.
    # QMainWindow.saveState/restoreState is no longer used; the sidebar's
    # selected index and collapsed state are persisted instead.
    # v14: Statistics nav item moved to the bottom of the sidebar order.
    # v15: Result Settings and Commentary swapped in the sidebar order.
    # v16: Bracket panel replaced by the Top 8 panel (AutonaTop8Widget).
    STATE_VERSION = 16
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

        self.setWindowIcon(QIcon(UI.icon_path('icon.ico')))

        if not os.path.exists("out/"):
            os.mkdir("out/")

        if not os.path.exists("./user_data/games"):
            os.makedirs("./user_data/games")

        # layout/logo.png doubles as runtime-mutable state (loading a
        # tournament overwrites it with that tournament's logo), so it is
        # gitignored rather than tracked. Seed it from the Autona brand icon
        # on first run so the default tournament icon is Autona instead of a
        # missing/placeholder image. This is the single source of the default
        # (same asset SetDefaultIcon copies on "reset to default").
        if not os.path.exists("layout/logo.png"):
            try:
                shutil.copy("assets/icons/icon.png", "layout/logo.png")
            except Exception:
                logger.opt(exception=True).warning(
                    "Could not seed default tournament logo.")

        # Purge leftovers from a previous in-place update: the legacy
        # Autona_old.exe (onefile) plus the *.autona_old sidecars the onedir
        # updater leaves under _internal/. Safe to run now — nothing holds them.
        CleanupUpdateLeftovers()

        for weight in ('Regular', 'Bold', 'Thin'):
            QFontDatabase.addApplicationFont(
                f'./assets/font/LINESeedJP/LINESeedJP-{weight}.ttf')
        app_font = QFont(UI.FONT_FAMILY, UI.FONT_SM)
        QApplication.instance().setFont(app_font)

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

        self.programVersion = version
        self.setGeometry(300, 300, 800, 100)
        self.setWindowTitle("Autona v"+version)

        splash.set_message(splash_msgs[0], progress=0.25)

        # Each panel below is a QDockWidget subclass, but they are no longer
        # docked: their title bars are hidden and dock features disabled, so
        # they behave as plain panels. They are collected here and later added
        # as pages of a QStackedWidget driven by the left sidebar (see the
        # "Sidebar navigation" block further down).
        self.dockWidgets = []

        tournamentInfo = AutonaTournamentInfoWidget()
        tournamentInfo.setWindowIcon(QIcon(UI.icon_path('tournament.svg')))
        # objectName is the stable identity that saveState/restoreState uses to
        # match docks. It must NOT be translated: a translated objectName changes
        # with the UI language, so a layout saved in one language fails to restore
        # in another and the tab order scrambles. The visible tab label comes from
        # windowTitle (translated) instead.
        tournamentInfo.setObjectName("Tournament Info")
        tournamentInfo.setWindowTitle(
            QApplication.translate("app", "Tournament Info"))
        self.dockWidgets.append(tournamentInfo)

        # BOARD — pick scoreboard design, tune colors, OBS setup. Placed right
        # after EVENT (it absorbs EVENT's color pickers).
        board = AutonaBoardWidget()
        board.setWindowIcon(QIcon(UI.icon_path('board.svg')))
        board.setObjectName("Board")
        board.setWindowTitle(QApplication.translate("app", "Board"))
        self.dockWidgets.append(board)

        from .AutonaThumbnailSettingsWidget import AutonaThumbnailSettingsWidget
        thumbnailSetting = AutonaThumbnailSettingsWidget()
        thumbnailSetting.setWindowIcon(QIcon(UI.icon_path('thumbnail.svg')))
        thumbnailSetting.setObjectName("Thumbnail Settings")
        thumbnailSetting.setWindowTitle(
            QApplication.translate("app", "Thumbnail Settings"))
        self.dockWidgets.append(thumbnailSetting)

        from .AutonaResultSettingsWidget import AutonaResultSettingsWidget
        # Held so the "対戦形式" menu can switch the result layout to the team
        # standings variant when team mode is selected.
        self.resultSetting = resultSetting = AutonaResultSettingsWidget()
        resultSetting.setWindowIcon(QIcon(UI.icon_path('result.svg')))
        resultSetting.setObjectName("Result Settings")
        resultSetting.setWindowTitle(
            QApplication.translate("app", "Result Settings"))
        self.dockWidgets.append(resultSetting)

        top8 = AutonaTop8Widget()
        top8.setWindowIcon(QIcon(UI.icon_path('bracket.svg')))
        top8.setObjectName("Top8")
        top8.setWindowTitle(
            QApplication.translate("app", "Top 8 Display Settings"))
        self.dockWidgets.append(top8)
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
        self.scoreboard.setObjectName("Scoreboard")
        self.dockWidgets.append(self.scoreboard)
        AutonaScoreboardManager.instance.setWindowTitle(
            QApplication.translate("app", "Scoreboard"))
        splash.set_message(splash_msgs[1], progress=0.55)

        commentary = AutonaCommentaryWidget()
        commentary.setWindowIcon(QIcon(UI.icon_path('commentary.svg')))
        commentary.setObjectName("Commentary")
        commentary.setWindowTitle(
            QApplication.translate("app", "Commentary"))
        self.dockWidgets.append(commentary)

        # One-time migration prompt: the bind default changed from 0.0.0.0 to
        # 127.0.0.1, which would silently break LAN OBS setups
        # (docs/operations/runtime-operations.md). Ask once, persist the answer.
        if SettingsManager.Get("general.webserver_listen_lan", None) is None:
            allow = QMessageBox.question(
                self,
                QApplication.translate("portguard", "LAN access"),
                QApplication.translate(
                    "portguard",
                    "Autona's web server now listens only on this PC (127.0.0.1) "
                    "by default for security.\n\n"
                    "If you connect from other devices on your LAN (OBS on another PC, "
                    "smartphone remote control), enable LAN access now.\n\n"
                    "You can change this later under Settings → General."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes
            SettingsManager.Set("general.webserver_listen_lan", allow)

        self.webserver = WebServer(
            parent=self, commentaryWidget=commentary)
        self.webserver.signals.start_failed.connect(self.OnWebServerStartFailed)
        check_webserver_port(self.webserver.port, parent=self)
        self.webserver.start()
        self.signals.GameChanged.connect(self.webserver.ws_program_state)
        self.signals.GameChanged.connect(self.webserver.ws_get_characters)
        splash.set_message(splash_msgs[1], progress=0.70)

        # PLAYERS and ANLYSE are no longer top-level docks — they are built as
        # plain panels here and embedded as sub-tabs inside the STATS dock
        # ("選手一覧" / "この大会の試合一覧"). The NOTES dock was removed entirely.
        playerList = AutonaPlayerListWidget()
        self.analysisWidget = AutonaAnalysisWidget()

        self.statsWidget = AutonaStatsWidget(
            analysisPanel=self.analysisWidget,
            playerListPanel=playerList,
        )
        self.statsWidget.setWindowIcon(QIcon(UI.icon_path('stats.svg')))
        self.statsWidget.setObjectName("Statistics")
        self.statsWidget.setWindowTitle(
            QApplication.translate("app", "Statistics"))
        self.dockWidgets.append(self.statsWidget)

        splash.set_message(splash_msgs[1], progress=0.85)

        # Display order of the sidebar nav items and the matching
        # QStackedWidget pages: EVENT, SCORE, BOARD, VIDEO, RESULT,
        # COMMS, TOP8, STATS. Each entry is (panel widget, sidebar icon file).
        self._navPanels = [
            (tournamentInfo, 'tournament.svg'),
            (self.scoreboard, 'scoreboard.svg'),
            (board, 'board.svg'),
            (thumbnailSetting, 'thumbnail.svg'),
            (resultSetting, 'result.svg'),
            (commentary, 'commentary.svg'),
            (top8, 'bracket.svg'),
            (self.statsWidget, 'stats.svg'),
        ]

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

        # ── Sidebar navigation (replaces tabified docks + QMenuBar) ──
        # Central area: a fixed-width left sidebar beside a QStackedWidget that
        # holds every panel as a page. The sidebar's nav items drive the stack;
        # the operations header (tournament / game / format) and the footer
        # "Help & Settings" button host what used to be the menu bar.
        self.stack = QStackedWidget()
        central = QWidget()
        centralLayout = QHBoxLayout(central)
        centralLayout.setContentsMargins(0, 0, 0, 0)
        centralLayout.setSpacing(0)

        self.sidebar = AutonaSidebar()
        centralLayout.addWidget(self.sidebar)
        centralLayout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Floating button to re-open the sidebar once collapsed. It sits over
        # the top-left of the stacked content (Todoist-style).
        self.sidebarExpandButton = QToolButton(central)
        self.sidebarExpandButton.setObjectName("sidebarExpandBtn")
        self.sidebarExpandButton.setIcon(QIcon(UI.icon_path('menu.svg')))
        # 22px glyph in a 34px chip (4px QSS padding → 26px content box). The
        # #sidebarExpandBtn QSS rule cancels the shared button's 12px side
        # padding that would otherwise crush the icon.
        self.sidebarExpandButton.setIconSize(QSize(22, 22))
        self.sidebarExpandButton.setFixedSize(34, 34)
        self.sidebarExpandButton.setToolTip(
            QApplication.translate("app", "Show sidebar"))
        self.sidebarExpandButton.move(6, 6)
        self.sidebarExpandButton.clicked.connect(self.ShowSidebar)
        self.sidebarExpandButton.hide()

        self.sidebar.collapseRequested.connect(self.HideSidebar)
        self.sidebar.navSelected.connect(self.stack.setCurrentIndex)

        # Build the nav items + stack pages in display order. The nav label is
        # the panel's (translated) windowTitle.
        for panel, iconName in self._navPanels:
            self.stack.addWidget(panel)
            self.sidebar.addNavItem(
                QIcon(UI.icon_path(iconName)), panel.windowTitle())
        self.sidebar.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)

        # ── Help & Settings menu (sidebar footer) ──
        # Former Edit / API Settings / Help menus, merged into one dropdown.
        helpMenu = QMenu(self)

        self.settingsWindow = AutonaSettingsWindow(self)
        action = helpMenu.addAction(
            QApplication.translate("Settings", "Settings"))
        action.setIcon(QIcon(UI.icon_path('settings.svg')))
        action.triggered.connect(lambda: self.settingsWindow.show())

        action = helpMenu.addAction(
            QApplication.translate("app", "Always on top"))
        action.setCheckable(True)
        action.toggled.connect(self.ToggleAlwaysOnTop)

        action = helpMenu.addAction(
            QApplication.translate("app", "Migrate Layout"))
        action.triggered.connect(self.MigrateWindow)

        helpMenu.addSeparator()

        # Guidance for obtaining game assets (not bundled — see A2/copyright).
        # The window is built lazily in UiMounted below.
        self.gameAssetGuideWindow = AutonaGameAssetGuideWindow(self)
        action = helpMenu.addAction(
            QApplication.translate("app", "Add game assets"))
        action.triggered.connect(lambda: self.gameAssetGuideWindow.show())

        helpMenu.addSeparator()

        # Language sub-menus
        program_language_messagebox = generate_restart_messagebox(
            QApplication.translate("app", "Program language changed successfully."))
        languageSelect = helpMenu.addMenu(
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
        languageSelect = helpMenu.addMenu(
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
        languageSelect = helpMenu.addMenu(
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

        # ── Operations: battle format segment (1v1 / Team / Crew) ──
        # The sidebar header hosts the segmented control; choosing a format
        # switches the SCORE board (1v1 / team / crew) and the matching RESULT
        # layout. The choice is persisted and applied after the scoreboards
        # are created (see CreateScoreboard call below).
        self.sidebar.setFormat(self._resolveBattleFormat())
        self.sidebar.formatSelected.connect(self.SetBattleFormat)

        # ── Operations: tournament dropdown (Start.gg actions) ──
        # Hosted on the sidebar's "tournament" button (top of the header).
        startggMenu = QMenu(self)

        self.setTournamentAction = startggMenu.addAction(
            QApplication.translate("app", "Set tournament"))
        self.setTournamentAction.triggered.connect(
            lambda: AutonaTournamentDataProvider.instance.SetStartggEventSlug(self))

        startggMenu.addAction(
            QApplication.translate("app", "Unset tournament")).triggered.connect(
            lambda: AutonaTournamentDataProvider.instance.SetTournament(None))

        self.sidebar.tournamentButton.setMenu(startggMenu)
        self.sidebar.tournamentButton.setText(
            QApplication.translate("app", "Set tournament"))
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            self.UpdateTournamentButton)
        AutonaTournamentDataProvider.instance.signals.tournament_url_update.connect(
            lambda *_: self.UpdateTournamentButton())

        # ── Operations: game selector (sidebar header) ──
        # Same widget pattern as the tournament / Help & Settings pulldowns: a
        # QToolButton + QMenu (InstantPopup). The game list is a handful of local
        # user_data/games/ entries, so a menu (no type-to-filter) fits and keeps
        # all three sidebar pulldowns a single, identical implementation.
        self.gameSelect = QToolButton()
        self.gameSelect.setObjectName("sidebarGameBtn")
        self.gameSelect.setIcon(QIcon(UI.icon_path("gamepad.svg")))
        self.gameSelect.setIconSize(QSize(16, 16))
        self.gameSelect.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.gameSelect.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.gameSelect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gameSelect.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.gameMenu = QMenu(self)
        self.gameSelect.setMenu(self.gameMenu)
        self._gameActions = {}        # 1-based game index -> QAction
        self._currentGameIndex = 0    # 0 = no game selected
        AutonaGameAssetManager.instance.signals.onLoad.connect(self.SetGame)
        AutonaGameAssetManager.instance.signals.onLoadAssets.connect(self.ReloadGames)
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(self.SetGame)
        AutonaTournamentDataProvider.instance.signals.tournament_url_update.connect(
            self.Signal_GameChange)

        # Modded content — shown in the sidebar header only when the selected
        # game ships modded assets.
        self.moddedContentCheck = QCheckBox(
            QApplication.translate("app", "Modded content"))
        self.moddedContentCheck.setVisible(False)
        self.moddedContentCheck.setChecked(False)

        AutonaGameAssetManager.instance.signals.onLoad.connect(lambda x=None: [
            self.moddedContentCheck.setVisible(AutonaGameAssetManager.instance.has_modded_content),
            self.moddedContentCheck.setChecked(StateManager.Get("game").get("mods_active", False))
        ])
        self.moddedContentCheck.stateChanged.connect(lambda x=None: [
            AutonaGameAssetManager.instance.LoadGameAssets(
                self._currentGameIndex,
                mods_active=self.moddedContentCheck.isChecked(),
                mods_reload_mode=True)
        ])
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            lambda x=None: self.moddedContentCheck.setChecked(False))

        self.sidebar.setGameSelect(self.gameSelect, self.moddedContentCheck)

        # ── API Settings (Help & Settings submenu) ──
        # Entry point for the External Control API (/api/v1/) reference +
        # tester window. The window itself is built lazily in UiMounted below.
        helpMenu.addSeparator()
        self.apiInfoWindow = AutonaApiInfoWindow(self)
        apiMenu = helpMenu.addMenu(QApplication.translate("app", "API Settings"))
        action = apiMenu.addAction(
            QApplication.translate("api", "Open reference & tester"))
        action.triggered.connect(lambda: self.apiInfoWindow.show())

        # Connection guide for Bitfocus Companion (which also covers Stream
        # Deck). Built lazily in UiMounted below.
        apiMenu.addSeparator()
        self.companionGuideWindow = AutonaIntegrationGuideWindow(self)
        action = apiMenu.addAction(
            QApplication.translate("api", "Companion settings"))
        action.triggered.connect(lambda: self.companionGuideWindow.show())

        # Connection guide for OBS Studio + Advanced Scene Switcher (ASS):
        # (B) ASS drives /api/v1/ via curl, (D) ASS watches out/ files. Built
        # lazily in UiMounted below.
        self.obsGuideWindow = AutonaObsGuideWindow(self)
        action = apiMenu.addAction(
            QApplication.translate("api", "OBS / Advanced Scene Switcher"))
        action.triggered.connect(lambda: self.obsGuideWindow.show())

        # ── Help (updates + community) ──
        helpMenu.addSeparator()

        # Check for updates - the in-app updater pulls the latest release from
        # the public distribution repo (see AutonaUpdater.GITHUB_REPO).
        action = helpMenu.addAction(
            QApplication.translate("app", "Check for updates"))
        action.setIcon(QIcon(UI.icon_path('update_circle.svg')))
        action.triggered.connect(
            lambda x=None: AutonaUpdater.instance.CheckForUpdates(silent=False))

        action = helpMenu.addAction(
            QApplication.translate("app", "Join the Discord community"))
        action.triggered.connect(
            lambda x=None: QDesktopServices.openUrl(QUrl(DISCORD_URL)))

        # Corporate / commercial-use inquiries (TERMS.md §2/§9). Doubles as
        # the lead-in to HiveeWorks' event support services, so it must stay
        # visible even to users who never read the bundled documents.
        action = helpMenu.addAction(
            QApplication.translate("app", "Corporate use inquiries"))
        action.triggered.connect(
            lambda x=None: QDesktopServices.openUrl(QUrl(BUSINESS_INQUIRY_URL)))

        # About / license information. Serves double duty as the LGPL v3
        # "prominent notice" for Qt/PySide6 and the start.gg attribution
        # required by the start.gg APIs Terms of Use.
        action = helpMenu.addAction(
            QApplication.translate("app", "About Autona"))
        action.triggered.connect(lambda x=None: self.ShowAboutDialog())

        # Attach the assembled menu to the sidebar footer button.
        self.sidebar.helpButton.setMenu(helpMenu)
        self.sidebar.helpButton.setText(
            QApplication.translate("app", "Help & Settings"))

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

        # Restore the last-selected sidebar tab and collapsed state.
        savedIndex = self.qtSettings.value(
            f"sidebarIndex_v{self.STATE_VERSION}", 0, type=int)
        if 0 <= savedIndex < self.stack.count():
            self.sidebar.setCurrentIndex(savedIndex)
            self.stack.setCurrentIndex(savedIndex)
        if self.qtSettings.value(
                f"sidebarCollapsed_v{self.STATE_VERSION}", False, type=bool):
            self.HideSidebar()

        splash.set_message(splash_msgs[2], progress=1.0)
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
        self.companionGuideWindow.UiMounted()
        self.obsGuideWindow.UiMounted()
        self.gameAssetGuideWindow.UiMounted()
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
            f"color: {UI.palette()['text_muted']}; padding: 0 8px;")
        self._statusBar.addWidget(self._statusLabel, 1)
        AutonaStatusManager.signals.changed.connect(
            self._on_status_changed)

        StateManager.ReleaseSaving()

    @Slot(str)
    def _on_status_changed(self, message: str):
        # Runs on the GUI thread thanks to Qt's auto signal connection.
        self._statusLabel.setText(message)

    def _pickGame(self, idx):
        # User chose a game from the menu — mirror the former combo 'activated'
        # behavior: load the game's assets, then clear the modded toggle.
        self._currentGameIndex = idx
        AutonaGameAssetManager.instance.LoadGameAssets(idx)
        self.moddedContentCheck.setChecked(False)

    def SetGame(self, mods_active=False):
        # Reflect the currently selected game on the button: check its menu
        # action and show its name as the button label.
        selected = AutonaGameAssetManager.instance.selectedGame
        name = selected.get("name")
        codename = selected.get("codename")
        idx = next(
            (i for i, action in self._gameActions.items()
             if action.text() == name or action.text() == codename),
            None)
        self._currentGameIndex = idx if idx is not None else 0
        for i, action in self._gameActions.items():
            action.setChecked(i == idx)
        if idx is not None:
            self.gameSelect.setText(self._gameActions[idx].text())
            self.signals.GameChanged.emit()
        else:
            self.gameSelect.setText(
                QApplication.translate("app", "Select game title"))

    def Signal_GameChange(self, url):
        if url == "":
            self._currentGameIndex = 0
            for action in self._gameActions.values():
                action.setChecked(False)
            self.gameSelect.setText(
                QApplication.translate("app", "Select game title"))
            AutonaGameAssetManager.instance.selectedGame = {}

    def HideSidebar(self):
        # Collapse the sidebar entirely; a small floating button (top-left of
        # the stacked content) remains so the user can re-open it.
        self.sidebar.hide()
        self.sidebarExpandButton.show()
        self.sidebarExpandButton.raise_()

    def ShowSidebar(self):
        self.sidebar.show()
        self.sidebarExpandButton.hide()

    def UpdateTournamentButton(self, *args):
        # Reflect the loaded tournament name on the sidebar "tournament"
        # button, falling back to the generic "Set tournament" label.
        if not getattr(self, "sidebar", None):
            return
        name = StateManager.Get("tournamentInfo.tournamentName") or ""
        if isinstance(name, str) and name.strip():
            self.sidebar.tournamentButton.setText(name.strip())
        else:
            self.sidebar.tournamentButton.setText(
                QApplication.translate("app", "Set tournament"))

    def OnWebServerStartFailed(self, error: str):
        # Delivered via queued signal from the webserver QThread. Use show()
        # (non-blocking) instead of exec() per the no-nested-modal rule; keep
        # a reference so the box is not garbage-collected while visible.
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(QApplication.translate(
            "portguard", "Web server failed to start"))
        box.setText(QApplication.translate(
            "portguard",
            "The web server could not start on port {port}:\n\n{error}\n\n"
            "OBS browser sources and the Web UI will not work.\n"
            "Change the port under Settings → General → Webserver Port "
            "and restart Autona.")
            .replace("{port}", str(self.webserver.port))
            .replace("{error}", error))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.show()
        self._webserverErrorBox = box

    def closeEvent(self, event):
        logger.info("Shutting down...")
        self.qtSettings.setValue("geometry", self.saveGeometry())
        self.qtSettings.setValue(
            f"sidebarIndex_v{self.STATE_VERSION}", self.sidebar.currentIndex())
        self.qtSettings.setValue(
            f"sidebarCollapsed_v{self.STATE_VERSION}", self.sidebar.isHidden())

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
            self.gameMenu.clear()
            self._gameActions = {}
            # Name-only entries (game logos are intentionally omitted from the
            # sidebar selector), sorted by display name. Each action carries the
            # 1-based game index expected by GameAssetManager.LoadGameAssets.
            entries = [
                (game.get("name") or codename, i + 1)
                for i, (codename, game) in enumerate(
                    AutonaGameAssetManager.instance.games.items())
            ]
            for name, idx in sorted(entries, key=lambda e: e[0].lower()):
                action = self.gameMenu.addAction(name)
                action.setCheckable(True)
                action.triggered.connect(
                    lambda _checked=False, g=idx: self._pickGame(g))
                self._gameActions[idx] = action
            self.SetGame()

    def ToggleAlwaysOnTop(self, checked):
        if checked:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.show()

    def ShowAboutDialog(self):
        # Legal/attribution text is deliberately not translated: proper nouns
        # and license names must stay verbatim (TERMS.md is the JA source).
        box = QMessageBox(self)
        box.setWindowTitle(QApplication.translate("app", "About Autona"))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<b>Autona v{self.programVersion}</b><br>"
            "&copy; 2026 taguyan (HiveeWorks)<br><br>"
            "Based on <a href=\"https://github.com/joaorb64/TournamentStreamHelper\">"
            "TournamentStreamHelper</a> (MIT License) by João Ribeiro Bezerra.<br><br>"
            "Tournament data powered by <a href=\"https://www.start.gg/\">start.gg</a>. "
            "Autona is an unofficial tool and is not affiliated with or endorsed by "
            "Start.gg, Inc.<br><br>"
            "Built with Qt / <a href=\"https://pypi.org/project/PySide6/\">PySide6</a> "
            "under the GNU LGPL v3 (dynamically linked; the libraries in "
            "<code>_internal/</code> can be replaced by the user).<br><br>"
            "法人・事業としてのご利用は事前にお問い合わせください（TERMS.md 第2条・第9条）。"
            "導入支援・運用サポートもご案内できます / For corporate or business use, "
            f"please <a href=\"{BUSINESS_INQUIRY_URL}\">contact us</a> in advance.<br><br>"
            "License details: see <code>TERMS.md</code>, "
            "<code>THIRD-PARTY-NOTICES.md</code> and the <code>licenses/</code> "
            "folder next to the executable.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

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
        # Keep the sidebar segment in sync when the format is changed
        # programmatically (setChecked does not re-emit the click signal).
        if getattr(self, "sidebar", None) is not None:
            self.sidebar.setFormat(fmt)

    def LoadTheme(self):
        # The app is dark-only. Theme is applied once at startup; there is
        # no runtime light/dark toggle (it used to re-polish the entire
        # proxy-embedded bracket on the GUI thread and froze for 30s+).
        qdarktheme.setup_theme(
            "dark",
            custom_colors=UI.DARK_CUSTOM_COLORS,
            additional_qss=UI.global_qss())

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
