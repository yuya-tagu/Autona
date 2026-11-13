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
    # Windows consoles default to the legacy ANSI code page (e.g. cp932),
    # which cannot encode many characters that show up in ordinary log
    # messages (e.g. accented Latin letters in character names such as
    # "Pokémon Trainer"). Force UTF-8 with a safe fallback so a single
    # non-ASCII message can't raise UnicodeEncodeError and spam
    # "Logging error in Loguru Handler" for the rest of the run.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    config = {
        "handlers": [
            {"sink": sys.stdout, "format": fmt, "level": "DEBUG",
             "diagnose": False},
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

# diagnose=False on every sink: loguru's default writes the local variables'
# values into a traceback, and those can be a player's name.
logger.add(
    "./logs/autona.log",
    format="[{time:YYYY-MM-DD HH:mm:ss}] - {level} - {file}:{function}:{line} | {message}",
    encoding="utf-8",
    level="INFO",
    rotation="20 MB",
    retention=5,
    diagnose=False
)

logger.add(
    "./logs/tsh-error.log",
    format="[{time:YYYY-MM-DD HH:mm:ss}] - {level} - {file}:{function}:{line} | {message}",
    encoding="utf-8",
    level="ERROR",
    rotation="20 MB",
    retention=5,
    diagnose=False
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

# Tint each window's Windows 11 title bar to the app palette (on focus).
from .Helpers.AutonaTitleBar import Install as _InstallTitleBarThemer
_titleBarThemer = _InstallTitleBarThemer(App)

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
# bootstrap_gui() adds the desktop's Qt adapters (hotkeys, asset / country
# models, scoreboard dock ...) on top; the headless shell runs only the first
# (ADR-005 S3c).
from .bootstrap import (
    bootstrap_core, bootstrap_gui, wire_tournament_data, wire_web_server)
bootstrap_core()
bootstrap_gui()

# autopep8: off
from .AutonaApiInfoWindow import AutonaApiInfoWindow
from .AutonaIntegrationGuideWindow import AutonaIntegrationGuideWindow
from .AutonaObsGuideWindow import AutonaObsGuideWindow
from .AutonaGameAssetGuideWindow import AutonaGameAssetGuideWindow
from .AutonaHotkeys import AutonaHotkeys
from .AutonaGameAssetManager import AutonaGameAssetManager
from .core.ColorService import SeedColorState
from .AutonaSidebar import AutonaSidebar
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaUpdater import AutonaUpdater, CleanupUpdateLeftovers
from .core.PlayerDB import PlayerDB
from .core.StateManager import StateManager
from .StateManagerBridge import StateManagerBridge
from .core import StateSchema as schema
from .core.Actions import AutonaActions
from .core.TournamentInfoService import TournamentInfoService
from .core.SettingsManager import SettingsManager
from .core.TaskExecutor import TaskExecutor
from .core.CountryCatalog import CountryCatalog
from .core.StatusService import StatusService
from .AutonaShellOperations import AutonaShellOperations
from . import AutonaWebOperations
from src.AutonaWebServer import WebServer, check_webserver_port
from .AutonaWebPageLifecycle import MarkWebServerReady
from .AutonaWebPanelHost import AutonaWebPanelHost, WebPanelPages
from .core import WebAccess
# autopep8: on


# Single source for the support inquiry contact, used by the Help menu, the
# About dialog and (manually kept in sync) TERMS.md §4.
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
    # v16: Bracket panel replaced by the Top 8 panel (AutonaTop8Widget; a
    #      React page since ADR-005 S4i, in the same place: no bump).
    # v17: Player List panel added below Statistics. (A React page since
    #      ADR-005 S4n, in the same place: no bump.)
    # v18: Stream Settings panel inserted after Tournament Info; the stream
    #      destination / title / thumbnail moved into it from the Tournament
    #      Info and Scoreboard panels. (A React page since ADR-005 S4e, in
    #      the same place: no bump.)
    # v19: Sidebar grouped into categories (Setup / Display / Data) and
    #      reordered — Scoreboard leads, uncategorised.
    # v20: Commentary and Top 8 swapped in the sidebar order.
    # v21: New "Scoreboard" category between Setup and Display, holding the
    #      scoreboard (input) and board (design) panels.
    # v22: Stats Display Settings panel added to
    #      the Display category, after Commentary.
    #      (Tournament Info became a React page in ADR-005 S4g, Thumbnail
    #      Settings in S4p and Result Settings in S4r, each in the same place:
    #      no bump.)
    STATE_VERSION = 22
    signals = WindowSignals()

    def __init__(self):
        super().__init__()

        log_startup_phase("window-init")

        # Every state write made while the window is built is coalesced into
        # a single save. This used to be a bare BlockSaving() ... 600 lines
        # ... ReleaseSaving() pair: anything raising in between left the
        # counter positive and silenced SaveState for the rest of the process
        # (agent-pitfalls #6, the shape BUG-3 was). The body lives in a method
        # of its own purely so the context manager has something to wrap.
        with StateManager.SaveBlock():
            self._BuildUi()

    def _BuildUi(self):
        """Build the window: panels, sidebar, menus, hotkeys, status bar.

        Called from __init__ only, inside StateManager.SaveBlock(). Nothing
        here changed when it was split out -- same statements, same order.
        """
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

        self.saveMutex = QMutex()

        self.player_layouts = []

        self.allplayers = None
        self.local_players = None

        # Directly, as the headless shell does: the catalog is the whole
        # load now (no Qt country model to fill). After LoadLocale().
        CountryCatalog.instance.Load()
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

        # Every panel is a React page in the shared web view (ADR-005 S4; the
        # last Qt panel, SCORE, went in S4ac-3), a page of the QStackedWidget
        # driven by the left sidebar (see the "Sidebar navigation" block
        # further down). None is built here: the view is constructed the
        # first time a page is revealed (see the _Create*Panel factories and
        # _EnsurePanelAt). The Qt panels were QDockWidget subclasses, shown
        # undocked; the preview panels each owned a QWebEngineView, and five
        # (Stream / Thumbnail / Result / Statistics / Player List) together
        # added ~2.9s to the window-init -> core-shown gap for UI most
        # launches never open.

        # EVENT — lazy since headless core Step 4b: see _CreateEventPanel. It
        # was the last eager panel, and could only be one because it was the
        # sole writer of tournamentInfo.* — an operator who never opened it
        # would have had no tournament name in the state.
        # core/TournamentInfoService is that writer now, wired below, so the
        # page can be built on first reveal like every other. The React page
        # in the shared web view since ADR-005 S4g; being the sidebar's
        # default item, it is the page a fresh install starts on, so the
        # view's construction waits for the web server
        # (AutonaWebPanelHost._ScheduleView): after STARTUP
        # phase=webserver-ready, from the event loop.
        #
        # STREAM — broadcast-scoped settings (destination, title, stream org
        # assets, YouTube thumbnail). Sits right after EVENT: the tournament
        # metadata and the broadcast prep are both done before the event, in
        # that order. Deliberately separate from EVENT (tournament-scoped) and
        # SCORE (match-scoped) so each panel owns one level of the hierarchy.
        # Lazy: see _CreateStreamPanel.
        #
        # BOARD — pick scoreboard design, tune colors, OBS setup. Placed right
        # after EVENT (it absorbs EVENT's color pickers). Lazy: see
        # _CreateBoardPanel (a React page since ADR-005 S4s).
        #
        # THUMBNAIL / RESULT — lazy as well: see _CreateThumbnailPanel /
        # _CreateResultPanel (React pages since ADR-005 S4p / S4r).

        splash.set_message(splash_msgs[0], progress=0.40)

        # SCORE: the React page in the shared web view (ADR-005 S4aa), its
        # sidebar item a lazy factory (_CreateScoreboardPanel). Its boards are
        # core's, made at launch (AutonaActions.MakeBoards, bootstrap_gui();
        # S4ac-1). The Qt boards' dock (AutonaScoreboardManager), built here
        # until ADR-005 S4ac-3, and the setting that showed it instead
        # (general.qt_scoreboard_panel) are gone.
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

        # STATISTICS and PLAYER LIST (React pages since ADR-005 S4k / S4n) are
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
                (self._CreateEventPanel, 'tournament.svg',
                 QApplication.translate("app", "Tournament Info"), "event"),
                (self._CreateStreamPanel, 'megaphone.svg',
                 QApplication.translate("app", "Stream Settings"), "stream"),
            ]),
            (QApplication.translate("app", "ScoreboardCategory"), [
                (self._CreateScoreboardPanel, 'scoreboard.svg',
                 QApplication.translate("app", "Scoreboard"), "scoreboard"),
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
                (self._CreatePlayerBrowserPanel, 'players.svg',
                 QApplication.translate("app", "Player List"),
                 "playerBrowser"),
            ]),
        ]

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
        self.sidebar.navSelected.connect(self._OnNavSelected)

        # Build the nav items + stack pages in display order. Category
        # headings take no nav index, so nav index still equals stack page
        # index — including for lazy pages, which occupy their index with an
        # empty placeholder until they are first revealed. The one exception
        # is what a React item shows once the shared web view exists: that
        # view's page (WebPanelPages; see _OnNavSelected).
        self._lazyPanels = {}      # stack index -> (factory, key), unbuilt only
        self._panelIndexes = {}    # lazy key -> stack index
        # The React panels' shared web view (ADR-005 S4), built by the
        # factory of the first React panel opened, and which page each React
        # item shows (_OnStackPageChanged tells the host on every reveal).
        self._webPanelHost = None
        self._webPanelPages = WebPanelPages()
        self._buildingPanel = False  # suppresses currentChanged during a swap
        for categoryTitle, panels in self._navSections:
            if categoryTitle:
                self.sidebar.addNavCategory(categoryTitle)
            for page, iconName, label, key in panels:
                if callable(page):
                    index = self.stack.addWidget(QWidget())
                    self._lazyPanels[index] = (page, key)
                    self._panelIndexes[key] = index
                    if WebAccess.IsWebPanel(key):
                        self._webPanelPages.Add(index, key)
                else:
                    index = self.stack.addWidget(page)
                self.sidebar.addNavItem(QIcon(UI.icon_path(iconName)), label)
        # The Settings page (ADR-005 S4af): a React panel the Help & Settings
        # menu opens (OpenSettings), not a sidebar item. A lazy page after
        # the items', so nav index == stack index still holds for every item
        # and the saved sidebar index does not move (no STATE_VERSION bump).
        index = self.stack.addWidget(QWidget())
        self._lazyPanels[index] = (self._CreateSettingsPanel, "settings")
        self._panelIndexes["settings"] = index
        self._webPanelPages.Add(index, "settings")
        # The item the window reopens on while the Settings page shows (no
        # item is highlighted then): the one chosen before it.
        self._navBeforeSettings = 0
        # Connected after the pages exist so the initial addWidget() cannot
        # trigger a build before _lazyPanels is complete.
        self.stack.currentChanged.connect(self._OnStackPageChanged)
        self.sidebar.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)

        # ── Help & Settings menu (sidebar footer) ──
        # Former Edit / API Settings / Help menus, merged into one dropdown.
        helpMenu = QMenu(self)

        # Lazy dialog slots (startup Step 4a). The guide windows reachable
        # from this menu are no longer built during startup: each one is
        # constructed by its Ensure*Window() accessor on first open and kept
        # afterwards, so state survives close/reopen exactly as before.
        # Settings is a React page in the shared view since ADR-005 S4af
        # (OpenSettings); the Qt settings window was one of these.
        self._gameAssetGuideWindow = None
        self._apiInfoWindow = None
        self._companionGuideWindow = None
        self._obsGuideWindow = None

        action = helpMenu.addAction(
            QApplication.translate("Settings", "Settings"))
        action.setIcon(QIcon(UI.icon_path('settings.svg')))
        action.triggered.connect(lambda: self.OpenSettings())

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
        # the Settings page (Help & Settings → Settings → Language).

        # ── Operations: battle format segment (1v1 / Team / Crew) ──
        # The sidebar header hosts the segmented control; choosing a format
        # is core's switch (AutonaActions.SetBattleFormat, ADR-005 S4x),
        # through the web operation v1's PUT /battle-format runs
        # (AutonaWebOperations.set_battle_format): a board operation, under
        # the board host's lock, so a switch from here and one from v1's
        # request thread never interleave their check and their writes
        # (ADR-005 S4ad; both ran on this thread until then). It persists
        # the format and the RESULT layout follows. The segment follows the
        # switch's notice (_ShowBattleFormat), and the SCORE page follows it
        # on its own (the web server's `battle_format`; the Qt SCORE's board
        # did until ADR-005 S4ac-3).
        self.sidebar.setFormat(self._resolveBattleFormat())
        self.sidebar.formatSelected.connect(
            AutonaWebOperations.set_battle_format)
        StateManagerBridge.instance.battle_format_changed.connect(
            self._ShowBattleFormat)

        # ── Operations: tournament dropdown (Start.gg actions) ──
        # Hosted on the sidebar's "tournament" button (top of the header).
        startggMenu = QMenu(self)

        self.setTournamentAction = startggMenu.addAction(
            QApplication.translate("app", "Set tournament"))
        self.setTournamentAction.triggered.connect(
            lambda: AutonaTournamentDataProvider.instance.SetStartggEventSlug(self))

        # Asks first, and forgets the selected game like every other clear
        # (core/TournamentService.py, ADR-005 S4f owner's decision 2).
        startggMenu.addAction(
            QApplication.translate("app", "Unset tournament")).triggered.connect(
            lambda: AutonaTournamentDataProvider.ConfirmClearTournament(self))

        self.sidebar.tournamentButton.setMenu(startggMenu)
        # ORDER CONSTRAINT: the tournamentInfo.* writer must run before the
        # readers of that state. Connected here, ahead of the
        # UpdateTournamentButton hooks below, because the button reads
        # tournamentInfo.tournamentName out of the state — the EVENT panel
        # used to provide that ordering by being constructed earlier in this
        # method, and it is lazy now (headless core Step 4b; a React page
        # since ADR-005 S4g). The wiring is
        # the one the headless shell makes too (ADR-005 S3c): the payload
        # writer. (Forgetting the selected game on a clear is core's
        # TournamentService, ahead of the tournament_url_update that
        # Signal_GameChange below resets the game button on -- S4f.)
        wire_tournament_data(on_failure=self._ReportTournamentLoadFailure)
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            self.UpdateTournamentButton)
        AutonaTournamentDataProvider.instance.signals.tournament_url_update.connect(
            lambda *_: self.UpdateTournamentButton())
        # tournament_changed is emitted from SetTournament *before* the
        # Start.gg fetch resolves, so on its own it can never show a name that
        # only arrives with the data — follow the data signal as well.
        AutonaTournamentDataProvider.instance.signals.tournament_data_updated.connect(
            self.UpdateTournamentButton)
        # Seed from the restored state instead of hardcoding the placeholder
        # here: UpdateTournamentButton is the single owner of both the label
        # and the attention outline, so a cached tournament is already named
        # on the first paint.
        self.UpdateTournamentButton()

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

        # Support inquiries (TERMS.md §4). Not a license requirement: Autona is
        # MIT. Kept as the lead-in to the developer's event support services.
        action = helpMenu.addAction(
            QApplication.translate("app", "Event support inquiries"))
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

        # The shell operations' main window: what opens the SCORE page's set
        # selection for the load_set hotkey and the legacy open-set route
        # (ADR-005 S4ab-2). Until ADR-005 S4ac-3 the Qt set selection windows
        # were built here, parented to this window, and then the widgets of
        # the board the persisted battle format shows (CreateScoreboard /
        # SetBattleFormat); the boards are core's, made at launch.
        AutonaShellOperations.instance.UiMounted(self)

        self.ReloadGames()

        self.qtSettings = QSettings("taguyan", "Autona")

        if self.qtSettings.value("geometry"):
            self.restoreGeometry(self.qtSettings.value("geometry"))

        # Restore the last-selected sidebar tab and collapsed state.
        savedIndex = self.qtSettings.value(
            f"sidebarIndex_v{self.STATE_VERSION}", 0, type=int)
        if 0 <= savedIndex < self._panelIndexes["settings"]:
            # The saved index is the sidebar's (closeEvent: an item, never
            # the Settings page after them); a React item's page is the
            # shared view's, as on a click.
            self.sidebar.setCurrentIndex(savedIndex)
            self._OnNavSelected(savedIndex)
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
        # The main window may not take focus at launch; theme it explicitly.
        _titleBarThemer.Apply(self.windowHandle())
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
        # CountryCatalog.instance.Load() above); do not load them a second time.
        # NOTE: the settings / guide dialogs used to be constructed above and
        # UiMounted() here. They are built on first open instead (Step 4a) —
        # see the Ensure*Window() accessors.
        AutonaTournamentDataProvider.instance.UiMounted()
        AutonaGameAssetManager.instance.UiMounted()
        # Silent startup check runs only for the frozen distribution — in
        # dev mode (python main.py) we can't self-install, so skip the
        # noise of a "can't auto-install" dialog at every launch.
        if getattr(sys, "frozen", False):
            AutonaUpdater.instance.CheckForUpdates(silent=True)
        AutonaHotkeys.instance.UiMounted(self)
        # NOTE: Ctrl+Shift+P (refresh_phase_group) is routed to the core Top 8
        # import by AutonaHotkeys.ConnectActions() (bootstrap_gui(), ADR-005
        # S3e-8); the Top 8 page (React since S4i) renders what it wrote.
        # NOTE: AutonaPlayerDB.signals.db_updated -> webserver.ws_playerdb is
        # wired in _StartWebServer(); there is no server to push to yet here.
        PlayerDB.instance.LoadDB()

        # Footer status bar — surfaces the current long-running task
        # (Start.gg fetches, asset reloads, etc.) so the user can tell
        # the app is busy rather than frozen. Its source is core's
        # StatusService (ADR-005 S-final SF-2), the same one GET
        # /api/v1/status and Socket.IO's `status` give the web clients.
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
        StateManagerBridge.instance.status_changed.connect(
            self._on_status_changed)
        # A job already running (one pushed before the footer existed) is
        # shown now, not at its next change.
        self._on_status_changed()

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
        # Not while settings.json could not be read (even after the launch's
        # retries): "not decided yet" is then the stand-in's, and the answer
        # -- in effect for the run, put back over the file when it can be
        # read -- would replace the one it holds. That run listens on this PC
        # only (the default) and writes nothing; the next launch asks.
        if (not SettingsManager.loadFailed
                and SettingsManager.Get(
                    "general.webserver_listen_lan", None) is None):
            allow = QMessageBox.question(
                self,
                QApplication.translate("portguard", "LAN access"),
                QApplication.translate(
                    "portguard",
                    "Autona's web server now listens only on this PC (127.0.0.1) "
                    "by default for security.\n\n"
                    "If you connect from other devices on your LAN (OBS on another PC, "
                    "smartphone remote control), enable LAN access now.\n\n"
                    "You can change this later under Settings → Network."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes
            SettingsManager.Set("general.webserver_listen_lan", allow)
            # The dialog above ran a nested event loop; the window may have
            # been closed while it was up.
            if self._shuttingDown:
                return

        # The failure callback runs on the server thread; the dialog belongs
        # on the GUI thread, which is where the old queued start_failed
        # signal delivered it too. Nothing else to hand in: the desktop's
        # side of the web operations (the game loader, the shell
        # operations) was registered by bootstrap_gui() (ADR-005 S3e-9; the
        # board host too until S4ad).
        self.webserver = WebServer(
            on_start_failed=lambda error: AutonaQtHelper.gui_executor(
                lambda: self.OnWebServerStartFailed(error)))
        # Probe the very interface the server will bind (LAN vs loopback).
        check_webserver_port(
            self.webserver.port, parent=self, host=self.webserver.host_name)
        self.webserver.start()
        # GameChanged -> program_state + characters, db_updated -> the player
        # DB: the wiring the headless shell makes too. Here, together with
        # the server: at its old place in __init__ there is no webserver
        # instance to bind to. Nothing is lost by connecting after LoadDB()
        # — clients pull the player DB on demand (GET /playerdb, socket
        # 'playerdb') and none can be connected before the server is
        # listening anyway.
        wire_web_server(self.webserver, self.signals.GameChanged)

        # Release the embedded view that fetches its pages from this server
        # (the React panel host): it does not even build its view before this
        # point, and builds it from the event loop after this returns (ADR-005
        # S4g review M4).
        MarkWebServerReady()

        log_startup_phase("webserver-ready")

    # ── Panel state seeded before the panels (startup Step 4b-2) ─────────

    def _SeedPanelState(self):
        """Write the panel state the overlays / Web UI read from launch.

        The seed itself is StateSchema.SeedPanelState() — Qt-free, so the
        state a headless run starts from is the state a GUI launch starts
        from (headless core Step 2). What is left here is the save cycle the
        three seeds share. All three are core now: the per-feature color
        overrides were the last exception, and moved with the rest of their
        key family in Step 5's final instalment (core/ColorService.py).

        Called from __init__ before any panel is built, and necessarily after
        StateManager.LoadState() — which ran inside bootstrap_core(), i.e.
        while this module was imported — so what is normalised here is the
        restored state (user_data/state.json, ADR-005 S3b), not an empty
        state.
        """
        with StateManager.SaveBlock():
            schema.SeedPanelState()

            # tournamentInfo.*: the 表示大会名 default, the EVENT page's text
            # keys and the tournament icon's cache-bust version. Not part of
            # the schema seed because the display-name follow rule needs the
            # service's own "last seen tournament name" bookkeeping.
            TournamentInfoService.instance.Seed()

            # colors.{feature}.* — only the features the operator explicitly
            # set. An unset feature stays absent from the state so the layout
            # JS falls back to the tournament colors SeedPanelState wrote.
            SeedColorState()

    # ── Lazy sidebar panels (startup Step 4b-2 / 4b-3) ───────────────────
    # Every panel below is built the first time its page is revealed and
    # kept afterwards, so input values and selections survive switching
    # pages exactly as before. The first four each own a QWebEngineView; the
    # five added by Step 4b-3 are plain but heavy (large .ui files, editable
    # combos with completers), and together they filled the window-init ->
    # core-shown gap. Everything the panels used to seed into StateManager at
    # construction time now runs from _SeedPanelState() instead.

    def _CreateEventPanel(self):
        # ADR-005 S4g: the React page in the shared web view.
        return self._WebPanel("event")

    def _CreateScoreboardPanel(self):
        # ADR-005 S4aa: the React page in the shared web view. The way back
        # to the Qt panel (general.qt_scoreboard_panel) went with the Qt
        # boards in S4ac-3; an old settings.json may still carry the key,
        # which nothing reads.
        return self._WebPanel("scoreboard")

    def _CreateBoardPanel(self):
        # ADR-005 S4s: the React page in the shared web view.
        return self._WebPanel("board")

    def _CreateTop8Panel(self):
        # ADR-005 S4i: the React page in the shared web view.
        return self._WebPanel("top8")

    def _CreateCommentaryPanel(self):
        # ADR-005 S4b/S4c: the React page in the shared web view.
        return self._WebPanel("commentary")

    def _CreateStatsPreviewPanel(self):
        # ADR-005 S4l: the React page in the shared web view.
        return self._WebPanel("statsPreview")

    def _CreateStreamPanel(self):
        # ADR-005 S4e: the React page in the shared web view.
        return self._WebPanel("stream")

    def _CreateThumbnailPanel(self):
        # ADR-005 S4p: the React page in the shared web view.
        return self._WebPanel("thumbnail")

    def _CreateResultPanel(self):
        # ADR-005 S4r: the React page in the shared web view.
        return self._WebPanel("result")

    def _CreateStatsPanel(self):
        # ADR-005 S4k: the React page in the shared web view (the match list
        # is its fourth tab).
        return self._WebPanel("stats")

    def _CreatePlayerBrowserPanel(self):
        # ADR-005 S4n: the React page in the shared web view.
        return self._WebPanel("playerBrowser")

    def _CreateSettingsPanel(self):
        # ADR-005 S4af: the React page in the shared web view (not a sidebar
        # item: OpenSettings).
        return self._WebPanel("settings")

    def _WebPanel(self, panel):
        """The shared web view (AutonaWebPanelHost), built as the stack page
        of `panel`, the first React panel opened.

        Built once: once it is on the stack, _EnsurePanelAt adopts it
        (_AdoptWebPanelHost), and the other React items show this page from
        then on (WebPanelPages, _OnNavSelected; ADR-005 S4e). The guard
        below cannot fire through _EnsurePanelAt; it keeps a second host
        from ever being made.
        """
        if self._webPanelHost is not None:
            raise RuntimeError(
                f"the web panel host is already a page; {panel!r} would "
                "need a second one")
        host = AutonaWebPanelHost()
        host.setObjectName("WebPanels")
        # Before the page is shown: the view is built, on this panel's page,
        # when the host first is.
        host.ShowPanel(panel)
        return host

    def _AdoptWebPanelHost(self, index, host):
        """`host`, built by _WebPanel, is stack page `index` now: the React
        items show it from here on, and the other React items' placeholders
        leave _lazyPanels (never to be built: a second host).

        Only once the page is on the stack (S4e review): a build that fails
        before that leaves no host recorded and the other React items'
        placeholders lazy, so the next of them opened builds the host."""
        self._webPanelHost = host
        for other in self._webPanelPages.HostBuilt(index):
            self._lazyPanels.pop(other, None)

    def _OnNavSelected(self, nav):
        """Show sidebar item `nav`'s stack page: a sidebar click, and the
        QSettings tab restore at the end of __init__ (and the Settings page,
        a page of its own after the items': OpenSettings).

        Its own page, but for a React item once the shared web view exists:
        that view's page (WebPanelPages). Going from one React item to
        another leaves the stack where it is, and currentChanged only fires
        when the index moves, so the page change is handled here then.
        """
        index = self._webPanelPages.StackIndex(nav)
        if self.stack.currentIndex() == index:
            self._OnStackPageChanged(index)
        else:
            self.stack.setCurrentIndex(index)

    # ── SCORE's set selection asked for from outside (ADR-005 S4ab-2) ────
    # The load_set hotkey and the legacy open-set route (AutonaShellOperations)
    # open the SCORE page's dialog (the Qt window until ADR-005 S4ac-3, on
    # the way back to the Qt panel).

    def OpenScoreSets(self, board):
        """Bring the window to the front on SCORE and have its page open board
        `board`'s set selection dialog: what a click on the SCORE item does,
        then the request to the shared view, which hands it to the page once
        it can (AutonaWebPanelHost.OpenScoreSets: after the view's build, a
        load or a thaw). The page opens it as its own button does -- on the
        board it shows, with a tournament -- and else just shows itself.
        Nothing once the window is closing: a request then would show it
        again and build the host being torn down."""
        if self._shuttingDown:
            logger.info("The set selection was asked for while the window "
                        "closes: nothing opened")
            return
        self._BringToFront()
        nav = self._panelIndexes["scoreboard"]
        self.sidebar.setCurrentIndex(nav)
        self._OnNavSelected(nav)
        host = self._webPanelHost
        if host is None:
            # Its build failed (logged by _EnsurePanelAt): no page to ask.
            return
        host.OpenScoreSets(board)

    # ── The Settings page (ADR-005 S4af) ─────────────────────────────────
    # The Help & Settings menu's "Settings" and the API window's "Open
    # settings" (the Qt settings window, non-modal, until S4af).

    def OpenSettings(self):
        """Show the Settings page: a React panel in the shared view, as a
        sidebar item's, but not one -- no item is highlighted while it
        shows, and choosing one leaves it. The window comes to the front
        first: the API window asks from a window of its own. Nothing once
        the window is closing (a request then would build the host being
        torn down)."""
        if self._shuttingDown:
            logger.info("The settings page was asked for while the window "
                        "closes: nothing opened")
            return
        self._BringToFront()
        nav = self.sidebar.currentIndex()
        if nav >= 0:
            self._navBeforeSettings = nav
        self.sidebar.clearCurrent()
        self._OnNavSelected(self._panelIndexes["settings"])

    def _NavToRemember(self):
        """The sidebar item the window reopens on (closeEvent): the one
        highlighted, or, while the Settings page shows (none is), the one
        chosen before it."""
        nav = self.sidebar.currentIndex()
        return self._navBeforeSettings if nav < 0 else nav

    def _BringToFront(self):
        """Show the window in front of the others, restored if it was
        minimized and kept maximized if it was (QWidget.setWindowState's own
        recipe). Windows may still refuse a process that has not had the
        last input the foreground (an outside request), and flash its
        taskbar button instead."""
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def _OnStackPageChanged(self, index):
        # Covers both a sidebar choice (_OnNavSelected -> setCurrentIndex,
        # or called from there when the page stays) and a programmatic
        # switch. Ignored while a page swap of our own is in flight — see
        # _EnsurePanelAt.
        if self._buildingPanel:
            return
        page = self._EnsurePanelAt(index)
        panel = self._webPanelPages.Shown(index)
        if panel is not None and page is self._webPanelHost:
            # Every reveal: the host navigates its view to the panel chosen,
            # or reloads a view whose last load failed (S4a, decision 11).
            page.ShowPanel(panel)

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
            # removeWidget() on the visible page makes Qt pick another one and
            # emit currentChanged; _buildingPanel keeps that from building a
            # panel the user never asked for. insertWidget() at the same index
            # restores the original page order.
            placeholder = self.stack.widget(index)
            wasCurrent = self.stack.currentIndex() == index
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.stack.insertWidget(index, panel)
            if isinstance(panel, AutonaWebPanelHost):
                self._AdoptWebPanelHost(index, panel)
            if wasCurrent:
                self.stack.setCurrentIndex(index)
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

    # ── Lazy guide dialogs (startup Step 4a) ─────────────────────────────
    # Each of these four dialogs is only reachable from a menu entry, so
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

    @Slot()
    def _on_status_changed(self):
        # Runs on the GUI thread thanks to Qt's auto signal connection
        # (StateManagerBridge.status_changed, queued from a worker). The
        # notice carries no value: the job shown is the latest one, read
        # here -- the last pushed still running -- in the program's language
        # (AutonaLocaleHelper.StatusText). No job: an empty label.
        job = StatusService.Current()
        self._statusLabel.setText(AutonaLocaleHelper.StatusText(job))
        total = job["total"] if job else 0
        if total > 0:
            self._statusProgress.setRange(0, total)
            self._statusProgress.setValue(min(job["done"], total))
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
        # The game button only: forgetting the selected game itself is
        # core's TournamentService.ClearTournament, done before it announces
        # the empty URL this follows (both shells, ADR-005 S4f).
        if url == "":
            self._currentGameIndex = 0
            for action in self._gameActions.values():
                action.setChecked(False)
            self.gameSelect.setText(
                QApplication.translate("app", "Select game title"))
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

    def _ReportTournamentLoadFailure(self, data):
        """Tell the operator that a tournament payload did not load.

        The Qt half of core/TournamentInfoService: the writing is the
        service's (ApplyProviderPayload, connected by wire_tournament_data,
        which calls this when it failed), and this is the one thing it
        cannot do. Owned by the window rather than the EVENT page because
        the page may never be built (it is a React page since ADR-005 S4g,
        and also shows the failure: the state's tournamentInfo.load).
        """
        message = (
            QApplication.translate(
                "app", "The tournament URL could not be loaded.") + "\n"
            + QApplication.translate(
                "app",
                "Make sure that your tournament URL is correctly formatted "
                "and points to an existing event, and try again."))
        box = QMessageBox(self)
        box.setWindowIcon(QIcon(UI.icon_path('icon.png')))
        box.setWindowTitle(QApplication.translate("app", "Error"))
        box.setText(QApplication.translate("app", "Error"))
        box.setInformativeText(message)
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()

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
        # Marshalled here from the web server thread through gui_executor
        # (see _StartWebServer). Use show()
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
            "Change general.webserver_port in user_data/settings.json and "
            "restart Autona, or quit the application that is using the "
            "port.")
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
                self._NavToRemember())
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
        # The web server is stoppable since ADR-005 S3c: werkzeug's
        # shutdown() ends its accept loop, where QThread.terminate() used to
        # kill that thread (TerminateThread, which can catch it holding the
        # GIL or a CRT / interpreter lock and hang the exit). Requests run on
        # werkzeug's daemon request threads and are not drained, as before:
        # the server is local and the process is on its way out.
        try:
            # None when the window is closed before the deferred start ran.
            if self.webserver is not None:
                self.webserver.stop(10.0)  # 10 seconds grace period.
        except Exception:
            logger.opt(exception=True).error("Error stopping web server on shutdown")

        # The background tasks (ADR-005 S3f): after the web server, so no
        # request can queue one any more; before the interpreter's exit join
        # flushes ./out and user_data/state.json, so a task that finishes in
        # time has its writes in that flush. Queued tasks are cancelled at
        # once and a running one is told to stop; the wait is short so the
        # window still closes promptly.
        try:
            if TaskExecutor.instance is not None:
                if not TaskExecutor.instance.shutdown(1.0):
                    logger.warning(
                        "Background tasks were still running on shutdown")
        except Exception:
            logger.opt(exception=True).error(
                "Error stopping background tasks on shutdown")

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
        # and license names must stay verbatim.
        box = QMessageBox(self)
        box.setWindowTitle(QApplication.translate("app", "About Autona"))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<b>Autona v{self.programVersion}</b><br>"
            "&copy; 2026 taguyan and Autona contributors. Released under the "
            "MIT License.<br><br>"
            "Based on <a href=\"https://github.com/joaorb64/TournamentStreamHelper\">"
            "TournamentStreamHelper</a> (MIT License) by João Ribeiro Bezerra.<br><br>"
            "Tournament data powered by <a href=\"https://www.start.gg/\">start.gg</a>. "
            "Autona is an unofficial tool and is not affiliated with or endorsed by "
            "Start.gg, Inc.<br><br>"
            "Built with Qt / <a href=\"https://pypi.org/project/PySide6/\">PySide6</a> "
            "under the GNU LGPL v3 (dynamically linked; the libraries in "
            "<code>_internal/</code> can be replaced by the user).<br><br>"
            "Setup and operation support for events is available: "
            f"<a href=\"{BUSINESS_INQUIRY_URL}\">{BUSINESS_INQUIRY_EMAIL}</a>.<br><br>"
            "License details: see <code>LICENSE</code>, <code>TERMS.md</code>, "
            "<code>TRADEMARK.md</code>, "
            "<code>THIRD-PARTY-NOTICES.md</code> and the <code>licenses/</code> "
            "folder next to the executable.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    @staticmethod
    def _resolveBattleFormat() -> str:
        # Delegated to core so the window and the headless callers
        # (AutonaActions.ActiveBoard -> hotkeys, /api/v1/health) cannot answer
        # differently -- including the legacy `scoreboard.team_mode` fallback,
        # which the hotkeys used to miss (headless core Step 3).
        return AutonaActions.instance.ResolveBattleFormat()

    def _ShowBattleFormat(self, fmt=None):
        # What the window shows for a battle format just switched to
        # (StateManagerBridge.battle_format_changed): the sidebar segment,
        # kept in sync when the switch came from elsewhere (setChecked does
        # not re-emit the click signal). The format in force, not the
        # notice's: a switch from v1's request thread is queued here, one
        # from the sidebar is not, so a v1 switch followed at once by the
        # operator's arrives after it and would show the older format
        # (ADR-005 S4ad: v1's switch ran on this thread until then). The
        # SCORE page follows the switch on its own (the web server's
        # `battle_format`, which carries nothing either); the Qt SCORE's
        # board 1 (1v1), 2 (team) or 3 (crew) was shown here until ADR-005
        # S4ac-3. The switch itself -- the settings and the RESULT layout --
        # is core's (AutonaActions.SetBattleFormat, ADR-005 S4x); this was
        # the window's SetBattleFormat, which did all of it.
        if getattr(self, "sidebar", None) is not None:
            self.sidebar.setFormat(self._resolveBattleFormat())

    def LoadTheme(self):
        # The app is dark-only. Theme is applied once at startup; there is
        # no runtime light/dark toggle (it used to re-polish the entire
        # proxy-embedded bracket on the GUI thread and froze for 30s+).
        qdarktheme.setup_theme(
            "dark",
            custom_colors=UI.DARK_CUSTOM_COLORS,
            additional_qss=UI.global_qss())
