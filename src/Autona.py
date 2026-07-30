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

# NOTE: Qt.AA_ShareOpenGLContexts is set in main.py — it only takes effect
# before the QApplication is constructed, and that now happens there too.

if parse(qtpy.QT_VERSION).major == 6:
    QImageReader.setAllocationLimit(0)

# NOTE: the Windows AppUserModelID is set at the very top of main.py
# (before this module is imported), so the taskbar groups the app under
# our identity rather than the host python.exe.

# main.py creates the QApplication before importing this module, so that the
# splash can be painted while this import chain runs. The fallback is NOT for
# the app itself: PyInstaller's binary-dependency analysis imports modules in
# an isolated subprocess without main.py, and anything here that ends up
# creating a QWidget hard-aborts the process when no QApplication exists.
# Kept deliberately even though src/__init__.py no longer chains into this
# module (Step 3g) — the analysis still reaches it through main.py.
App = QApplication.instance()
if App is None:
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

        # loguru does not understand the stdlib `exc_info` kwarg; passing it
        # silently drops the traceback. Use logger.opt(exception=...) instead.
        logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(
            "Uncaught exception")

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

# loguru is only configured now, so the STARTUP marks recorded before this
# module was imported (gate / splash) have been buffered — replay them into
# the log file and route the later phases straight to loguru.
import autona_splash
autona_splash.attach_startup_logger(logger)

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

from autona_splash import (
    LoadingOverlay,
    active_splash,
    log_startup_phase,
    pick_splash_messages,
)

from contextlib import contextmanager
@contextmanager
def catchtime(msg = ''):
    from time import perf_counter
    start = perf_counter()
    yield lambda: perf_counter() - start
    logger.info(f'{msg} Time: {perf_counter() - start:.3f} seconds')

# Explicit construction of the core singletons (composition root). This must
# stay ABOVE the import block below: what is still built at import time there
# (SettingsManager, LocaleHelper, StartGGDataProvider ...) reads StateManager
# and the already-migrated instances, and the settings-window chain used to be
# what implicitly created
# AutonaHotkeys.instance. Running bootstrap_core() here keeps the effective
# initialization order identical to the old import-time side effects.
from .bootstrap import bootstrap_core
bootstrap_core()

# autopep8: off
from .Settings.AutonaSettingsWindow import AutonaSettingsWindow
from .AutonaApiInfoWindow import AutonaApiInfoWindow
from .AutonaIntegrationGuideWindow import AutonaIntegrationGuideWindow
from .AutonaObsGuideWindow import AutonaObsGuideWindow
from .AutonaGameAssetGuideWindow import AutonaGameAssetGuideWindow
from .LayoutOptions.AutonaLayoutOptionsWindow import AutonaLayoutOptionsWindow
from .AutonaHotkeys import AutonaHotkeys
from .AutonaCommentaryWidget import AutonaCommentaryWidget
# from .AutonaTeamBattleWidget import AutonaTeamBattleWidget
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaTop8Widget import AutonaTop8Widget
from .AutonaTournamentInfoWidget import AutonaTournamentInfoWidget
from .AutonaBoardWidget import (
    AutonaBoardWidget, DEFAULT_ACCENT_COLOR, DEFAULT_THEME_COLOR)
from .AutonaSidebar import AutonaSidebar
from . import AutonaCommentaryModel as comms
from . import AutonaTop8Model as top8
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaAlertNotification import AutonaAlertNotification
from .AutonaUpdater import AutonaUpdater, CleanupUpdateLeftovers
from .AutonaPlayerDB import AutonaPlayerDB
from .Workers import *
from .StateManager import StateManager
from .SettingsManager import SettingsManager
from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Helpers.AutonaStatusManager import AutonaStatusManager
from .AutonaScoreboardManager import AutonaScoreboardManager
from .AutonaStatsWidget import AutonaStatsWidget
from .AutonaStatsPreviewWidget import AutonaStatsPreviewWidget
from .AutonaAnalysisWidget import AutonaAnalysisWidget
from .AutonaPlayerBrowserWidget import AutonaPlayerBrowserWidget
from src.AutonaWebServer import WebServer, check_webserver_port
from .AutonaLayoutPreview import MarkWebServerReady
# autopep8: on


def remove_accents_lower(input_str):
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return u"".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()


# Single source for the corporate / commercial-use inquiry contact, used by
# the Help menu, the About dialog and (manually kept in sync) TERMS.md §9.
DISCORD_URL = "https://discord.gg/yAFAmhaUqn"
BUSINESS_INQUIRY_EMAIL = "taguyangames@gmail.com"
BUSINESS_INQUIRY_URL = f"mailto:{BUSINESS_INQUIRY_EMAIL}"


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
    # v17: Player List panel (AutonaPlayerBrowserWidget) added below Statistics.
    # v18: Stream Settings panel (AutonaStreamSettingsWidget) inserted after
    #      Tournament Info; the stream destination / title / thumbnail moved
    #      into it from the Tournament Info and Scoreboard panels.
    # v19: Sidebar grouped into categories (Setup / Display / Data) and
    #      reordered — Scoreboard leads, uncategorised.
    # v20: Commentary and Top 8 swapped in the sidebar order.
    # v21: New "Scoreboard" category between Setup and Display, holding the
    #      scoreboard (input) and board (design) panels.
    # v22: Stats Display Settings panel (AutonaStatsPreviewWidget) added to
    #      the Display category, after Commentary.
    STATE_VERSION = 22
    signals = WindowSignals()

    def __init__(self):
        super().__init__()

        log_startup_phase("window-init")

        StateManager.BlockSaving()

        AutonaLocaleHelper.LoadLocale()
        AutonaLocaleHelper.LoadRoundNames()
        self.LoadTheme()

        self.signals = WindowSignals()

        # Created by main.py before this module was imported, so it has been
        # on screen since a second or two after launch.
        splash = active_splash()
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

        # State the overlays / Web UI read from launch, seeded before any
        # panel exists (startup Step 4b-2 / 4b-3).
        self._SeedPanelState()

        # Each panel below is a QDockWidget subclass, but they are no longer
        # docked: their title bars are hidden and dock features disabled, so
        # they behave as plain panels. They are collected here and later added
        # as pages of a QStackedWidget driven by the left sidebar (see the
        # "Sidebar navigation" block further down).
        #
        # Only Tournament Info and the Scoreboard are built here. Every other
        # panel is constructed the first time its page is revealed (see the
        # _Create*Panel factories and _EnsurePanelAt): the preview panels
        # (Board / Top 8 / Commentary / Stats Display) each own a
        # QWebEngineView, and the remaining five (Stream / Thumbnail / Result
        # / Statistics / Player List) together added ~2.9s to the window-init
        # -> core-shown gap for UI most launches never open.
        self.dockWidgets = []

        # Filled by _CreateThumbnailPanel / _CreateResultPanel. Declared here
        # because SetBattleFormat reaches for both on every format switch and
        # has to skip whichever panel has not been revealed yet; the factory
        # then catches that panel up to the current format as it builds.
        self.thumbnailSetting = None
        self.resultSetting = None

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

        # STREAM — broadcast-scoped settings (destination, title, stream org
        # assets, YouTube thumbnail). Sits right after EVENT: the tournament
        # metadata and the broadcast prep are both done before the event, in
        # that order. Deliberately separate from EVENT (tournament-scoped) and
        # SCORE (match-scoped) so each panel owns one level of the hierarchy.
        # Lazy: see _CreateStreamPanel.
        #
        # BOARD — pick scoreboard design, tune colors, OBS setup. Placed right
        # after EVENT (it absorbs EVENT's color pickers). Lazy: see
        # _CreateBoardPanel.
        #
        # THUMBNAIL / RESULT — lazy as well: see _CreateThumbnailPanel /
        # _CreateResultPanel.

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

        # The web server is NOT built here (startup Step 4c). Constructing the
        # Flask/SocketIO app and binding the port dominated the window-init ->
        # core-shown gap, and nothing the user sees at startup depends on it,
        # so the whole block moved to _StartWebServer() which runs off the
        # event loop right after show(). The slot exists from here on so every
        # consumer can null-check it.
        self.webserver = None
        # Set by closeEvent so a deferred _StartWebServer() cannot spawn a
        # server thread that nothing will ever terminate.
        self._shuttingDown = False
        splash.set_message(splash_msgs[1], progress=0.70)

        # STATISTICS (with the absorbed ANLYSE sub-tab) and PLAYER LIST are
        # lazy too: see _CreateStatsPanel / _CreatePlayerBrowserPanel.

        splash.set_message(splash_msgs[1], progress=0.85)

        # Display order of the sidebar nav items and the matching
        # QStackedWidget pages, grouped into categories. Each section is
        # (category title or None, [(page, sidebar icon file, nav label,
        # lazy key), ...]); a None title emits the items with no heading
        # above them.
        #
        # `page` is either an already-built panel or a zero-argument factory.
        # A factory marks a LAZY page (startup Step 4b-2): the stack gets a
        # placeholder now and the real panel is built the first time that
        # page is revealed (_EnsurePanelAt), keeping nav index == stack page
        # index either way. The nav label cannot be read from
        # panel.windowTitle() before the panel exists, so every entry carries
        # it explicitly; the strings are the same translate() calls the
        # panels' setWindowTitle uses. `key` names a lazy page for the
        # Ensure* accessors and the build log; it is None for eager pages.
        #
        # The categories follow the operator's timeline — configure the
        # event and the broadcast up front, run the scoreboard while the
        # tournament runs, set up how things look, then read the data back
        # out. NOTE: the "ScoreboardCategory" source string is a deliberate
        # disambiguation — plain "Scoreboard" is already the scoreboard
        # panel's title (ja: スコアボード入力).
        self._navSections = [
            (QApplication.translate("app", "Setup"), [
                (tournamentInfo, 'tournament.svg',
                 QApplication.translate("app", "Tournament Info"), None),
                (self._CreateStreamPanel, 'megaphone.svg',
                 QApplication.translate("app", "Stream Settings"), "stream"),
            ]),
            (QApplication.translate("app", "ScoreboardCategory"), [
                (self.scoreboard, 'scoreboard.svg',
                 QApplication.translate("app", "Scoreboard"), None),
                (self._CreateBoardPanel, 'board.svg',
                 QApplication.translate("app", "Board"), "board"),
            ]),
            (QApplication.translate("app", "Display"), [
                (self._CreateThumbnailPanel, 'thumbnail.svg',
                 QApplication.translate("app", "Thumbnail Settings"),
                 "thumbnail"),
                (self._CreateResultPanel, 'result.svg',
                 QApplication.translate("app", "Result Settings"), "result"),
                (self._CreateTop8Panel, 'bracket.svg',
                 QApplication.translate("app", "Top 8 Display Settings"),
                 "top8"),
                (self._CreateCommentaryPanel, 'commentary.svg',
                 QApplication.translate("app", "Commentary"), "commentary"),
                (self._CreateStatsPreviewPanel, 'stats.svg',
                 QApplication.translate("app", "Stats Display Settings"),
                 "statsPreview"),
            ]),
            (QApplication.translate("app", "Data"), [
                (self._CreateStatsPanel, 'stats.svg',
                 QApplication.translate("app", "Statistics"), "stats"),
                # The only panel whose title is set by the widget itself, so
                # the label cannot be read back off the instance while the
                # panel is still unbuilt. Spelled out here with the exact
                # same source string AutonaPlayerBrowserWidget.__init__ uses.
                (self._CreatePlayerBrowserPanel, 'players.svg',
                 QApplication.translate("app", "Player List"),
                 "playerBrowser"),
            ]),
        ]

        for dw in self.dockWidgets:
            self._PreparePanel(dw)

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

        # Build the nav items + stack pages in display order. Category
        # headings take no nav index, so nav index still equals stack page
        # index — including for lazy pages, which occupy their index with an
        # empty placeholder until they are first revealed.
        self._lazyPanels = {}      # stack index -> (factory, key), unbuilt only
        self._panelIndexes = {}    # lazy key -> stack index
        self._buildingPanel = False  # suppresses currentChanged during a swap
        for categoryTitle, panels in self._navSections:
            if categoryTitle:
                self.sidebar.addNavCategory(categoryTitle)
            for page, iconName, label, key in panels:
                if callable(page):
                    index = self.stack.addWidget(QWidget())
                    self._lazyPanels[index] = (page, key)
                    self._panelIndexes[key] = index
                else:
                    index = self.stack.addWidget(page)
                self.sidebar.addNavItem(QIcon(UI.icon_path(iconName)), label)
        # Connected after the pages exist so the initial addWidget() cannot
        # trigger a build before _lazyPanels is complete.
        self.stack.currentChanged.connect(self._OnStackPageChanged)
        self.sidebar.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)

        # ── Help & Settings menu (sidebar footer) ──
        # Former Edit / API Settings / Help menus, merged into one dropdown.
        helpMenu = QMenu(self)

        # Lazy dialog slots (startup Step 4a). The settings / guide windows
        # reachable from this menu are no longer built during startup: each
        # one is constructed by its Ensure*Window() accessor on first open and
        # kept afterwards, so state survives close/reopen exactly as before.
        self._settingsWindow = None
        self._gameAssetGuideWindow = None
        self._apiInfoWindow = None
        self._companionGuideWindow = None
        self._obsGuideWindow = None

        action = helpMenu.addAction(
            QApplication.translate("Settings", "Settings"))
        action.setIcon(QIcon(UI.icon_path('settings.svg')))
        action.triggered.connect(lambda: self.EnsureSettingsWindow().show())

        action = helpMenu.addAction(
            QApplication.translate("app", "Always on top"))
        action.setCheckable(True)
        action.toggled.connect(self.ToggleAlwaysOnTop)

        helpMenu.addSeparator()

        # Guidance for obtaining game assets (not bundled — see A2/copyright).
        # Built on first open (see EnsureGameAssetGuideWindow).
        action = helpMenu.addAction(
            QApplication.translate("app", "Add game assets"))
        action.triggered.connect(
            lambda: self.EnsureGameAssetGuideWindow().show())

        # Program / game-asset / tournament-term language selectors moved to
        # the Settings window (Help & Settings → Settings → Language).

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
        # Lit until a tournament is loaded (tournament_changed clears it via
        # UpdateTournamentButton below).
        self._SetPulldownAttention(self.sidebar.tournamentButton, True)
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
        # tester window. The window itself is built on first open (see
        # EnsureApiInfoWindow).
        helpMenu.addSeparator()
        apiMenu = helpMenu.addMenu(QApplication.translate("app", "API Settings"))
        action = apiMenu.addAction(
            QApplication.translate("api", "Open reference & tester"))
        action.triggered.connect(lambda: self.EnsureApiInfoWindow().show())

        # Connection guide for Bitfocus Companion (which also covers Stream
        # Deck). Built on first open.
        apiMenu.addSeparator()
        action = apiMenu.addAction(
            QApplication.translate("api", "Companion settings"))
        action.triggered.connect(
            lambda: self.EnsureCompanionGuideWindow().show())

        # Connection guide for OBS Studio + Advanced Scene Switcher (ASS):
        # (B) ASS drives /api/v1/ via curl, (D) ASS watches out/ files. Built
        # on first open.
        action = apiMenu.addAction(
            QApplication.translate("api", "OBS / Advanced Scene Switcher"))
        action.triggered.connect(lambda: self.EnsureObsGuideWindow().show())

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
        # the lead-in to the developer's event support services, so it must
        # stay visible even to users who never read the bundled documents.
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

        # Build only the board the persisted battle format shows (startup
        # Step 4e — the other boards are built the first time they are needed)
        # and apply that format so the board + result layout match. The
        # "Rules" menu switches between them at runtime.
        # The Result / Thumbnail panels used to be told the format here; they
        # are lazy now, so each factory applies the current format itself
        # (startup Step 4b-3).
        battleFormat = self._resolveBattleFormat()
        AutonaScoreboardManager.instance.CreateScoreboard(battleFormat)
        AutonaScoreboardManager.instance.SetBattleFormat(battleFormat)

        self.ReloadGames()

        self.qtSettings = QSettings("taguyan", "Autona")

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

        # currentChanged only fires when the index actually moves, so a
        # restore that lands back on the page the stack already shows would
        # leave a lazy panel unbuilt and the page blank. Build whatever page
        # ends up current, restored or not.
        self._EnsurePanelAt(self.stack.currentIndex())

        splash.set_message(splash_msgs[2], progress=1.0)
        splash.finish(self)
        self.show()
        log_startup_phase("core-shown")

        # Cover the main window with a spinner until Qt finishes its
        # first-pass layout + paint, so the user doesn't see half-drawn
        # widgets jump into place. Dismissal is driven by the event queue
        # draining (the first pass being done), not by a fixed 1.1 s delay
        # that was pure guesswork in both directions.
        self._loading_overlay = LoadingOverlay(self)
        self._loading_overlay.start()
        self._loading_overlay.finish_when_ready()

        # Start the web server only once the overlay above has dismissed
        # itself (Step 4c). Building the Flask/SocketIO app runs on the GUI
        # thread and blocks it for a few seconds, so the order matters for
        # perceived responsiveness: window painted -> overlay gone -> the app
        # is briefly interactive -> the server is built. Starting it any
        # earlier would only move the freeze behind a stalled spinner, which
        # improves the core-shown number without improving the experience.
        #
        # call_when_finished() dispatches through a zero-delay timer, so this
        # runs from the event loop and not from inside a Qt signal handler:
        # the LAN opt-in and port-conflict QMessageBox exec() calls made below
        # are ordinary top-level modals and do not violate the
        # no-nested-modal / no-reentrant-dispatch rule (agent-pitfalls #9).
        self._loading_overlay.call_when_finished(self._StartWebServer)

        # Prevent tab labels from being elided ("…") — show full text so the
        # tab bar uses horizontal scrolling if it overflows.
        self._ApplyTabBarPolicy(self)

        # NOTE: countries are loaded once, early in __init__ (see
        # AutonaCountryHelper.instance.init() above). The second, identical
        # LoadCountries() that used to sit here re-parsed the ~2 MB JSON and
        # rebuilt the 250-row countryModel from scratch after every widget had
        # already installed the first model — pure duplicate work, and a model
        # swap that agent-pitfalls #13 warns about. Removed.
        # NOTE: the settings / guide dialogs used to be constructed above and
        # UiMounted() here. They are built on first open instead (Step 4a) —
        # see the Ensure*Window() accessors.
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
        # Ctrl+Shift+P used to be connected inside AutonaTop8Widget.__init__,
        # which made the hotkey silently dead until that panel existed. The
        # panel is lazy now (Step 4b-2), so the connection lives here — the
        # single owner — and builds the panel on demand.
        AutonaHotkeys.signals.refresh_phase_group.connect(
            self.RefreshTop8PhaseGroup)
        # NOTE: AutonaPlayerDB.signals.db_updated -> webserver.ws_playerdb is
        # wired in _StartWebServer(); there is no server to push to yet here.
        AutonaPlayerDB.LoadDB()

        # Footer status bar — surfaces the current long-running task
        # (Start.gg fetches, asset reloads, etc.) so the user can tell
        # the app is busy rather than frozen.
        self._statusBar = self.statusBar()
        self._statusBar.setSizeGripEnabled(False)
        # Right margin so the progress bar doesn't touch the window edge.
        # (QSS margin-right on the bar itself is ignored for background
        # painting; the status bar's contents margin is honored.)
        self._statusBar.setContentsMargins(0, 0, 8, 0)
        self._statusLabel = QLabel("")
        self._statusLabel.setStyleSheet(
            f"color: {UI.palette()['text_muted']}; padding: 0 8px;")
        self._statusBar.addWidget(self._statusLabel, 1)
        # Red progress bar filling left to right for multi-step operations
        # (paginated Start.gg fetches, asset scans). Hidden when the active
        # status reports no measurable progress.
        self._statusProgress = QProgressBar()
        self._statusProgress.setTextVisible(False)
        self._statusProgress.setFixedSize(160, 6)
        # padding: 0 is required — qdarktheme ships
        # `QStatusBar QWidget { padding: 3px }`, which would otherwise eat
        # the whole 6px height and leave the chunk a zero-height rect
        # (bar renders as an empty strip, no red fill).
        self._statusProgress.setStyleSheet(
            "QProgressBar {{ background: {bg}; border: none; padding: 0px;"
            " border-radius: 3px; }}"
            "QProgressBar::chunk {{ background-color: {chunk};"
            " border-radius: 3px; }}".format(
                bg=UI.palette()["bg_tertiary"],
                chunk=UI.palette()["brand"]))
        self._statusProgress.setVisible(False)
        self._statusBar.addWidget(self._statusProgress)
        AutonaStatusManager.signals.changed.connect(
            self._on_status_changed)

        StateManager.ReleaseSaving()

    # ── Web server, started after the window is shown (startup Step 4c) ──

    def _StartWebServer(self):
        """Build and start the Flask/SocketIO server, off the startup path.

        Registered with LoadingOverlay.call_when_finished() at the end of
        __init__, so it runs from the event loop once the window is visible
        AND the loading overlay is gone. Everything below is the former
        __init__ block, in the same order.
        """
        if self._shuttingDown or self.webserver is not None:
            return

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
            # The dialog above ran a nested event loop; the window may have
            # been closed while it was up.
            if self._shuttingDown:
                return

        self.webserver = WebServer(parent=self)
        self.webserver.signals.start_failed.connect(self.OnWebServerStartFailed)
        # Probe the very interface the server will bind (LAN vs loopback).
        check_webserver_port(
            self.webserver.port, parent=self, host=self.webserver.host_name)
        self.webserver.start()
        self.signals.GameChanged.connect(self.webserver.ws_program_state)
        self.signals.GameChanged.connect(self.webserver.ws_get_characters)
        # Moved here together with the server: at its old place in __init__
        # there is no webserver instance to bind to. Nothing is lost by
        # connecting after LoadDB() — clients pull the player DB on demand
        # (GET /playerdb, socket 'playerdb') and none can be connected before
        # the server is listening anyway.
        AutonaPlayerDB.signals.db_updated.connect(self.webserver.ws_playerdb)

        # Release the overlay previews (Board / Commentary / Top 8 / Stats),
        # which fetch their HTML from this server: a panel restored as the
        # startup page issues its first load() before this point.
        MarkWebServerReady()

        log_startup_phase("webserver-ready")

    # ── Panel state seeded before the panels (startup Step 4b-2) ─────────

    def _SeedPanelState(self):
        """Write the panel state the overlays / Web UI read from launch.

        These seeds used to run inside the Board / Top 8 / Commentary /
        Stream / Player List constructors. Those panels are built on first
        reveal now, so the seeds moved here: they have to run at every launch
        whether or not the operator ever opens the panel (an OBS overlay
        reads the theme colors and the commentary slots straight away).
        Called from __init__ before any panel is built, and necessarily after
        StateManager.LoadState() — which ran inside bootstrap_core(), i.e.
        while this module was imported — so what is normalised here is the
        restored program_state.json, not an empty state.
        """
        # Local imports: both modules are only needed for the constants /
        # helper below and for their own lazy factories, so nothing here
        # justifies pulling them into the module-level import block.
        from .AutonaStreamSettingsWidget import (
            AutonaStreamSettingsWidget, ORG_ICON_PATH, ORG_THUMB_PATH)
        from .AutonaPlayerBrowserWidget import _EDITOR_PATH_BASE

        with StateManager.SaveBlock():
            # Moved from AutonaBoardWidget.__init__: overlays receive
            # theme/accent before the user touches the color pickers.
            StateManager.Set(
                "tournamentInfo.themeColor",
                SettingsManager.Get("general.theme_color", DEFAULT_THEME_COLOR))
            StateManager.Set(
                "tournamentInfo.accentColor",
                SettingsManager.Get(
                    "general.accent_color", DEFAULT_ACCENT_COLOR))

            # Moved from AutonaTop8Widget.__init__. The legacy bracket
            # feature is gone; LoadState() restores its keys from
            # out/program_state.json, so purge them once here.
            StateManager.Unset("bracket")
            state = StateManager.Get("top8")
            if not isinstance(state, dict) or not state.get("matches"):
                StateManager.Set("top8", top8.DefaultState())
            else:
                # Normalise a restored state: slots are always derived.
                StateManager.Set(
                    "top8.matches",
                    top8.Recompute(state.get("matches") or {}))
                players = state.get("players") or {}
                for slot in top8.PLAYER_SLOTS:
                    if slot not in players:
                        StateManager.Set(
                            f"top8.players.{slot}", top8.DefaultPlayer())

            # Moved from AutonaCommentaryWidget.__init__: normalises
            # whatever LoadState restored, including the legacy pre-rework
            # schema.
            StateManager.Set(
                "commentary",
                comms.NormalizeState(StateManager.Get("commentary")))

            # Moved from AutonaStreamSettingsWidget.__init__. The org icon /
            # thumbnail are served out of ./layout/ and OBS overlays
            # cache-bust them with these mtimes, so the versions have to be
            # current from launch whether or not the panel is ever opened.
            # The panel republishes them itself after a load / reset.
            AutonaStreamSettingsWidget._publishAssetVersion(
                "stream.orgIconVersion", ORG_ICON_PATH)
            AutonaStreamSettingsWidget._publishAssetVersion(
                "stream.orgThumbVersion", ORG_THUMB_PATH)

            # Also from AutonaStreamSettingsWidget.__init__: residual keys
            # from the pre-配信設定 layouts, where the stream title and the
            # destination lived on the EVENT tab. Nothing reads them; drop
            # them so an upgraded program_state.json stops broadcasting them.
            # Guarded because Unset queues a changed key unconditionally and
            # a fresh install would otherwise fire two no-op save cycles.
            for stale in ("tournamentInfo.streamTitleEdit",
                          "tournamentInfo.streamUrl"):
                if StateManager.Get(stale, None) is not None:
                    StateManager.Unset(stale)

            # Moved from AutonaPlayerBrowserWidget.__init__: state left
            # behind by removed features / crashed sessions. player_list.*
            # was written by the retired PLAYERS roster editor and
            # player_editor.* by the editor dialog, and LoadState would
            # otherwise restore both forever.
            StateManager.Unset("player_list")
            StateManager.Unset(_EDITOR_PATH_BASE)

    # ── Lazy sidebar panels (startup Step 4b-2 / 4b-3) ───────────────────
    # Every panel below is built the first time its page is revealed and
    # kept afterwards, so input values and selections survive switching
    # pages exactly as before. The first four each own a QWebEngineView; the
    # five added by Step 4b-3 are plain but heavy (large .ui files, editable
    # combos with completers), and together they filled the window-init ->
    # core-shown gap. Everything the panels used to seed into StateManager at
    # construction time now runs from _SeedPanelState() instead.

    def _CreateBoardPanel(self):
        board = AutonaBoardWidget()
        board.setWindowIcon(QIcon(UI.icon_path('board.svg')))
        board.setObjectName("Board")
        board.setWindowTitle(QApplication.translate("app", "Board"))
        return board

    def _CreateTop8Panel(self):
        top8Panel = AutonaTop8Widget()
        top8Panel.setWindowIcon(QIcon(UI.icon_path('bracket.svg')))
        top8Panel.setObjectName("Top8")
        top8Panel.setWindowTitle(
            QApplication.translate("app", "Top 8 Display Settings"))
        return top8Panel

    def _CreateCommentaryPanel(self):
        commentary = AutonaCommentaryWidget()
        commentary.setWindowIcon(QIcon(UI.icon_path('commentary.svg')))
        commentary.setObjectName("Commentary")
        commentary.setWindowTitle(QApplication.translate("app", "Commentary"))
        return commentary

    def _CreateStatsPreviewPanel(self):
        # Preview + OBS URL for the statistics overlays (layout/upset_factor,
        # h2h_record, recent_sets, last_sets, tournament_history).
        statsPreview = AutonaStatsPreviewWidget()
        statsPreview.setWindowIcon(QIcon(UI.icon_path('stats.svg')))
        statsPreview.setObjectName("statsPreviewSettings")
        statsPreview.setWindowTitle(
            QApplication.translate("app", "Stats Display Settings"))
        return statsPreview

    def _CreateStreamPanel(self):
        from .AutonaStreamSettingsWidget import AutonaStreamSettingsWidget
        streamSetting = AutonaStreamSettingsWidget()
        streamSetting.setWindowIcon(QIcon(UI.icon_path('megaphone.svg')))
        streamSetting.setObjectName("Stream Settings")
        streamSetting.setWindowTitle(
            QApplication.translate("app", "Stream Settings"))
        return streamSetting

    def _CreateThumbnailPanel(self):
        from .AutonaThumbnailSettingsWidget import AutonaThumbnailSettingsWidget
        # Held so the battle-format switch can hide the team-name toggle in 1v1.
        self.thumbnailSetting = thumbnailSetting = AutonaThumbnailSettingsWidget()
        thumbnailSetting.setWindowIcon(QIcon(UI.icon_path('thumbnail.svg')))
        thumbnailSetting.setObjectName("Thumbnail Settings")
        thumbnailSetting.setWindowTitle(
            QApplication.translate("app", "Thumbnail Settings"))
        # Catch up on the format switches that happened while this panel did
        # not exist — SetBattleFormat skips an unbuilt panel. Cheap here: the
        # thumbnail's SetBattleFormat only toggles the team-name checkbox's
        # visibility, it does not touch the preview.
        thumbnailSetting.SetBattleFormat(self._resolveBattleFormat())
        return thumbnailSetting

    def _CreateResultPanel(self):
        from .AutonaResultSettingsWidget import AutonaResultSettingsWidget
        # Held so the "対戦形式" menu can switch the result layout to the team
        # standings variant when team mode is selected.
        self.resultSetting = resultSetting = AutonaResultSettingsWidget()
        resultSetting.setWindowIcon(QIcon(UI.icon_path('result.svg')))
        resultSetting.setObjectName("Result Settings")
        resultSetting.setWindowTitle(
            QApplication.translate("app", "Result Settings"))
        # Same catch-up as the thumbnail panel, but the order matters here.
        # SetBattleFormat moves layoutCombo, which fires _onLayoutChanged ->
        # GeneratePreview(True). Doing it now — inside the factory, i.e.
        # before _EnsurePanelAt puts the panel on the stack and Qt shows it —
        # means the panel's renderer is still unbuilt (showEvent builds it),
        # so that render call returns immediately. The single real render is
        # then the one showEvent schedules, already on the right layout.
        # Applying the format after the reveal would instead render the
        # stale layout first and re-render straight after.
        resultSetting.SetBattleFormat(self._resolveBattleFormat())
        return resultSetting

    def _CreateStatsPanel(self):
        # ANLYSE is not a top-level panel — it is built here and embedded as
        # a sub-tab inside the STATS panel ("この大会の試合一覧"). The NOTES
        # dock was removed entirely. Neither widget is referenced outside
        # this factory, so both stay local to it.
        analysisWidget = AutonaAnalysisWidget()
        statsWidget = AutonaStatsWidget(analysisPanel=analysisWidget)
        statsWidget.setWindowIcon(QIcon(UI.icon_path('stats.svg')))
        statsWidget.setObjectName("Statistics")
        statsWidget.setWindowTitle(
            QApplication.translate("app", "Statistics"))
        return statsWidget

    def _CreatePlayerBrowserPanel(self):
        # Sets its own windowTitle (translate("app", "Player List")); the nav
        # label in _navSections repeats that source string because the panel
        # does not exist when the sidebar is built.
        playerBrowser = AutonaPlayerBrowserWidget()
        playerBrowser.setWindowIcon(QIcon(UI.icon_path('players.svg')))
        playerBrowser.setObjectName("Players")
        return playerBrowser

    def _PreparePanel(self, dw):
        """Panel post-processing that used to be the loop over dockWidgets.

        Applied to the eager panels in one pass from __init__ and to a lazy
        panel right after its factory returns.
        """
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

    def _OnStackPageChanged(self, index):
        # Covers both a sidebar click (navSelected -> setCurrentIndex) and a
        # programmatic switch (the QSettings tab restore at the end of
        # __init__). Ignored while a page swap of our own is in flight — see
        # _EnsurePanelAt.
        if self._buildingPanel:
            return
        self._EnsurePanelAt(index)

    def _EnsurePanelAt(self, index):
        """Return the panel on stack page `index`, building it if lazy.

        The placeholder added at startup is replaced in place, so nav index
        == stack page index still holds and no other page moves.
        """
        entry = self._lazyPanels.get(index)
        if entry is None:
            # Already built, or never lazy to begin with.
            return self.stack.widget(index)
        factory, key = entry
        # Drop the factory first: a re-entrant call (widget construction can
        # dispatch events) must not start a second build, and a factory that
        # raises must not be retried on every page switch.
        del self._lazyPanels[index]

        panel = None
        self._buildingPanel = True
        try:
            panel = factory()
            self._PreparePanel(panel)
            # removeWidget() on the visible page makes Qt pick another one and
            # emit currentChanged; _buildingPanel keeps that from building a
            # panel the user never asked for. insertWidget() at the same index
            # restores the original page order.
            placeholder = self.stack.widget(index)
            wasCurrent = self.stack.currentIndex() == index
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.stack.insertWidget(index, panel)
            if wasCurrent:
                self.stack.setCurrentIndex(index)
            self.dockWidgets.append(panel)
            # Startup applies the tab-bar policy to every child that exists at
            # that point; panels born later need the same treatment.
            self._ApplyTabBarPolicy(panel)
            logger.debug(f"Built lazy panel '{key}' (page {index})")
        except Exception:
            logger.opt(exception=True).error(
                f"Failed to build lazy panel '{key}' (page {index})")
            return None
        finally:
            self._buildingPanel = False

        # A page switch that arrived while the swap was in flight was
        # ignored above; honour it now.
        current = self.stack.currentIndex()
        if current != index and current in self._lazyPanels:
            self._EnsurePanelAt(current)
        return panel

    def EnsureTop8Panel(self):
        """Build the Top 8 panel if it has not been revealed yet.

        Entry point for the consumers that drive the panel without the user
        having opened it: the Ctrl+Shift+P hotkey and /bracket:refresh. The
        class instance is what is returned (None if the build failed), so a
        caller never gets handed the placeholder widget.
        """
        self._EnsurePanelAt(self._panelIndexes["top8"])
        return AutonaTop8Widget.instance

    def EnsureCommentaryPanel(self):
        """Build the Commentary panel if it has not been revealed yet.

        Entry point for the external control API (/update-commentary-N,
        /load-commentator-from-tag-N), which drives the panel's signals.
        """
        self._EnsurePanelAt(self._panelIndexes["commentary"])
        return AutonaCommentaryWidget.instance

    def RefreshTop8PhaseGroup(self):
        """Ctrl+Shift+P: reload the Top 8 from Start.gg, building it first."""
        panel = self.EnsureTop8Panel()
        if panel is not None:
            panel.RefreshPhaseGroup()

    # ── Lazy settings / guide dialogs (startup Step 4a) ──────────────────
    # Each of these five dialogs is only reachable from a menu entry, so
    # building them during startup charged every launch for UI nobody had
    # asked for. They are constructed on first access and reused from then
    # on, which keeps the previous behaviour of a single long-lived instance
    # (state survives close/reopen).

    def _EnsureLazyWindow(self, attr, factory):
        """Build the dialog held in `attr` on first access; return it.

        The slot is filled *before* UiMounted() runs so that a re-entrant
        call during the build (nested event dispatch) reuses the same object
        instead of constructing a second dialog (agent-pitfalls #9).
        """
        window = getattr(self, attr)
        if window is None:
            window = factory(self)
            setattr(self, attr, window)
            window.UiMounted()
            # Startup applies the tab-bar policy to every child that exists
            # at that point; dialogs born later need the same treatment.
            self._ApplyTabBarPolicy(window)
        return window

    def EnsureSettingsWindow(self):
        return self._EnsureLazyWindow("_settingsWindow", AutonaSettingsWindow)

    def EnsureGameAssetGuideWindow(self):
        return self._EnsureLazyWindow(
            "_gameAssetGuideWindow", AutonaGameAssetGuideWindow)

    def EnsureApiInfoWindow(self):
        return self._EnsureLazyWindow("_apiInfoWindow", AutonaApiInfoWindow)

    def EnsureCompanionGuideWindow(self):
        return self._EnsureLazyWindow(
            "_companionGuideWindow", AutonaIntegrationGuideWindow)

    def EnsureObsGuideWindow(self):
        return self._EnsureLazyWindow("_obsGuideWindow", AutonaObsGuideWindow)

    def _ApplyTabBarPolicy(self, root):
        """Show full tab labels ("…" elision off) and scroll on overflow."""
        for bar in root.findChildren(QTabBar):
            bar.setElideMode(Qt.TextElideMode.ElideNone)
            bar.setUsesScrollButtons(True)

    @Slot(str, int, int)
    def _on_status_changed(self, message: str, progress: int, total: int):
        # Runs on the GUI thread thanks to Qt's auto signal connection.
        self._statusLabel.setText(message)
        if total > 0:
            self._statusProgress.setRange(0, total)
            self._statusProgress.setValue(min(progress, total))
            self._statusProgress.setVisible(True)
        else:
            self._statusProgress.setVisible(False)

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
        self._SetPulldownAttention(self.gameSelect, idx is None)
        if idx is not None:
            self.gameSelect.setText(self._gameActions[idx].text())
            self.signals.GameChanged.emit()
        else:
            self.gameSelect.setText(
                QApplication.translate("app", "Select game title"))

    def _SetPulldownAttention(self, button, on: bool):
        # Gold outline (QSS [attention="true"] rule in AutonaSidebar) nudging
        # the operator to complete a required pick (tournament / game).
        # Repolish so the attribute selector re-evaluates.
        if button.property("attention") == on:
            return
        button.setProperty("attention", on)
        button.style().unpolish(button)
        button.style().polish(button)

    def Signal_GameChange(self, url):
        if url == "":
            self._currentGameIndex = 0
            for action in self._gameActions.values():
                action.setChecked(False)
            self.gameSelect.setText(
                QApplication.translate("app", "Select game title"))
            AutonaGameAssetManager.instance.selectedGame = {}
            self._SetPulldownAttention(self.gameSelect, True)

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
        hasTournament = isinstance(name, str) and bool(name.strip())
        if hasTournament:
            self.sidebar.tournamentButton.setText(name.strip())
        else:
            self.sidebar.tournamentButton.setText(
                QApplication.translate("app", "Set tournament"))
        self._SetPulldownAttention(
            self.sidebar.tournamentButton, not hasTournament)

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
        # Shutdown must never depend on housekeeping succeeding. Every step
        # below is isolated so that one failure cannot abort the rest of the
        # sequence: an uncaught exception here used to leave the process alive
        # (e.g. shutil.rmtree hitting a locked tmp file with WinError 32),
        # because the later teardown never ran.
        logger.info("Shutting down...")
        # Blocks a still-pending _StartWebServer() (Step 4c defers it to the
        # event loop) from spawning a thread after the teardown below has
        # already run, which would leave the process alive.
        self._shuttingDown = True

        # --- Best-effort housekeeping ------------------------------------
        try:
            self.qtSettings.setValue("geometry", self.saveGeometry())
            self.qtSettings.setValue(
                f"sidebarIndex_v{self.STATE_VERSION}",
                self.sidebar.currentIndex())
            self.qtSettings.setValue(
                f"sidebarCollapsed_v{self.STATE_VERSION}",
                self.sidebar.isHidden())
        except Exception:
            logger.opt(exception=True).error("Error saving window settings on shutdown")

        try:
            tmpDir = AutonaResolve("tmp")
            if os.path.isdir(tmpDir):
                shutil.rmtree(tmpDir)
        except Exception:
            # Files under tmp/ can still be locked by the OS or by another
            # process; leaving them behind is harmless, blocking exit is not.
            logger.opt(exception=True).error("Error removing tmp directory on shutdown")

        try:
            crashlog.close()
            if crashpath.stat().st_size == 0:
                crashpath.unlink()
        except Exception:
            pass

        # --- Mandatory teardown ------------------------------------------
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
        except Exception:
            logger.opt(exception=True).warning("Error closing web socket on shutdown")

        try:
            # None when the window is closed before the deferred start ran.
            if self.webserver is not None:
                self.webserver.terminate()
                self.webserver.wait(10000)  # 10 seconds grace period.
        except Exception:
            logger.opt(exception=True).error("Error terminating web server on shutdown")

        try:
            # allWindows() returns QWindow, not QWidget: comparing against
            # `self` was always true. Compare with our own window handle.
            for window in QApplication.allWindows():
                if window is not self.windowHandle():
                    window.close()
        except Exception:
            logger.opt(exception=True).error("Error closing auxiliary windows on shutdown")

        super().closeEvent(event)

        # Quit explicitly instead of relying on quitOnLastWindowClosed:
        # off-screen helper widgets (thumbnail/result renderers) can keep the
        # last-window heuristic from ever firing, which leaves a zombie
        # process behind. Exiting must not depend on the cleanup above either.
        app = QApplication.instance()
        if app is not None:
            app.quit()

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
            # Shortcut to the same "Add game assets" guide as Help & Settings,
            # so assets can be added right where a game is picked.
            self.gameMenu.addSeparator()
            self.gameMenu.addAction(
                QApplication.translate("app", "Add game assets")).triggered.connect(
                lambda: self.EnsureGameAssetGuideWindow().show())
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
            "&copy; 2026 taguyan<br><br>"
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
            f"please contact <a href=\"{BUSINESS_INQUIRY_URL}\">"
            f"{BUSINESS_INQUIRY_EMAIL}</a> in advance.<br><br>"
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
        if getattr(self, "thumbnailSetting", None) is not None:
            self.thumbnailSetting.SetBattleFormat(fmt)
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
