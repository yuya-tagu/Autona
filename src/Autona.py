#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from .Helpers import AutonaQtHelper
from .Helpers import AutonaCrashLog
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaDirHelper import AutonaResolve
import shutil
import qdarktheme
from . import ui_constants as UI
import json
import os
import sys
import qtpy
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from packaging.version import parse
from loguru import logger
from pathlib import Path

# The native crash log: the previous run's dump is kept as
# logs/tsh-crash.prev.log, and the log is closed (removed when empty) by
# an atexit hook, so the dump also covers the interpreter's exit join (Qt's
# own atexit cleanup may run after that hook and is not covered).
crashpath = Path('./logs/tsh-crash.log').resolve()
crashlog = AutonaCrashLog.open_crash_log(crashpath)

# NOTE: Qt.AA_ShareOpenGLContexts is set in main.py — it only takes effect
# before the QApplication is constructed, and that now happens there too.

if parse(qtpy.QT_VERSION).major == 6:
    QImageReader.setAllocationLimit(0)

# NOTE: the Windows AppUserModelID is set at the very top of main.py
# (before this module is imported), so the taskbar groups the app under
# our identity rather than the host python.exe.

# main.py creates the QApplication before importing this module (its IPC
# server needs one, and AA_ShareOpenGLContexts must be set before it; the
# splash is a native window that needs none). The fallback is NOT for
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

if sys.stdout != None:  # noqa: E711 -- tests/test_console_encoding_guard.py finds this block by this exact text
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
from src import autona_splash
autona_splash.attach_startup_logger(logger)

try:
    # Setting the icon for individual windows doesn't work on mac (and perhaps linux? unknown)
    # Use the multi-resolution .ico (not .png): a single PNG renders in the
    # title bar but Windows fails to build a taskbar-sized HICON from it and
    # falls back to the host python.exe icon. The .ico embeds 16/32/48/256
    # so the taskbar button picks up the Autona mark.
    App.setWindowIcon(QIcon(UI.icon_path("icon.ico")))
except Exception:
    logger.opt(exception=True).warning("Could not set window icon for QApplication.")


logger.critical("=== Autona IS STARTING ===")

logger.info("QApplication successfully initialized")

# Tint each window's Windows 11 title bar to the app palette (on focus).
from .Helpers.AutonaTitleBar import Install as _InstallTitleBarThemer
_titleBarThemer = _InstallTitleBarThemer(App)

from .autona_splash import (
    LoadingOverlay,
    active_splash,
    log_startup_phase,
)

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
from .AutonaHotkeys import AutonaHotkeys
from .core.GameLoader import GameLoader
from .core.ColorService import SeedColorState
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaUpdater import AutonaUpdater, CleanupUpdateLeftovers
from .core.PlayerDB import PlayerDB
from .core.StateManager import StateManager
from .core import StateSchema as schema
from .core.TournamentInfoService import TournamentInfoService
from .core.SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT
from .core.TaskExecutor import TaskExecutor
from .core.CountryCatalog import CountryCatalog
from .AutonaShellOperations import AutonaShellOperations
from .AutonaWebPageLifecycle import MarkWebServerReady
from .AutonaWebPanelHost import AutonaWebPanelHost
from . import AutonaWindowState as window_state
# autopep8: on


class WindowSignals(QObject):
    UiMounted = Signal()


def _LoadWebServer():
    """(WebServer, check_webserver_port), imported on first use.

    Read after the window is shown (_StartWebServer, under the spinner), not
    at the top of this module: importing it loads flask, flask_socketio
    (engineio, aiohttp), flask_cors and werkzeug, which would otherwise hold
    the window back before core-shown. A failed import raises here, where
    _StartWebServer shows it as a start failure."""
    from src.AutonaWebServer import WebServer, check_webserver_port
    return WebServer, check_webserver_port


# A first launch's window (no geometry saved): the width the AppShell's
# two-pane pages are laid out for beside its sidebar, within the screen.
DEFAULT_WINDOW_SIZE = (1096, 720)


class Window(QMainWindow):
    # The window is the shared view alone (ADR-005 S-final SF-5b-3): its one
    # widget is AutonaWebPanelHost, whose page -- the AppShell's
    # /web/app/<panel> -- draws the navigation, the operations header
    # (tournament, game, mods, battle format) and the Help & Settings footer
    # the Qt sidebar, its menus and the status bar did until then. What it
    # keeps between launches besides its geometry -- the page it reopens on,
    # whether the AppShell's sidebar is hidden -- is AutonaWindowState's (the
    # Qt sidebar's sidebarIndex_v22 / sidebarCollapsed_v22 read into it
    # once). There is no layout version to bump any more: the navigation is
    # the AppShell's (web/src/AppShell/navSections.json), and the window
    # keeps a page's name, not an index.
    signals = WindowSignals()

    # The longest the loading spinner covers the window waiting for its
    # first page (_spinnerCap), the operator's answers to the startup's
    # dialogs and the web server's construction not counted: past it the
    # spinner goes, with a warning, and the host offers to load the page
    # again (_OnSpinnerCapReached). A first load on a busy machine takes a
    # few seconds.
    SPINNER_CAP_MS = 20_000

    def __init__(self):
        super().__init__()

        log_startup_phase("window-init")

        # Every state write made while the window is built is coalesced into
        # a single save. This used to be a bare BlockSaving() ... 600 lines
        # ... ReleaseSaving() pair: anything raising in between left the
        # counter positive and silenced SaveState for the rest of the process
        # (pitfalls #6, the shape BUG-3 was). The body lives in a method
        # of its own purely so the context manager has something to wrap.
        with StateManager.SaveBlock():
            self._BuildUi()

    def _BuildUi(self):
        """Build the window: the shared view, its startup, the hotkeys.

        Called from __init__ only, inside StateManager.SaveBlock().
        """
        AutonaLocaleHelper.LoadLocale()
        AutonaLocaleHelper.LoadRoundNames()
        self.LoadTheme()

        self.signals = WindowSignals()

        # Created by main.py right after its single-instance gate, so it has
        # been on screen (and animating, on a thread of its own) since a
        # fraction of a second after launch. Finished once this is shown.
        splash = active_splash()

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

        # Directly, as the headless shell does: the catalog is the whole
        # load now (no Qt country model to fill). After LoadLocale().
        CountryCatalog.instance.Load()

        try:
            version = json.load(
                open(AutonaResolve('./assets/versions.json'), encoding='utf-8')).get("program", "?")
        except Exception:
            version = "?"

        self._SetDefaultGeometry()
        self.setWindowTitle("Autona v"+version)

        # State the overlays / Web UI read from launch, seeded before any
        # page exists (startup Step 4b-2 / 4b-3).
        self._SeedPanelState()

        # The web server is NOT built here (startup Step 4c). Constructing the
        # Flask/SocketIO app and binding the port dominated the window-init ->
        # core-shown gap, so the whole block is _StartWebServer(), which runs
        # from the event loop once the shown window is painted. The slot
        # exists from here on so every consumer can null-check it.
        self.webserver = None
        # Set by closeEvent so a deferred _StartWebServer() cannot spawn a
        # server thread that nothing will ever terminate.
        self._shuttingDown = False
        # "Always on top": the window's own state (SetAlwaysOnTop: the shell
        # request alwaysOnTop's, the AppShell's Help & Settings menu). Not
        # kept between launches, as the Qt menu's check was not.
        self._alwaysOnTop = False

        self.setMinimumWidth(480)

        # ── The shared view: the window's one widget ──
        # Every page is a React page of the AppShell (ADR-005 S4; the Qt
        # sidebar and its QStackedWidget, which held one placeholder page per
        # item and built the host on the first React item revealed, went in
        # S-final SF-5b-3). The host builds its view only once it is on
        # screen and the web server is ready (AutonaWebPanelHost.
        # _ScheduleView), so building it here costs a plain QWidget. Its
        # pages' shell requests are the shell adapter's to run (S-final
        # SF-5a-1: alwaysOnTop, checkUpdates, openExternal, pickFolder).
        host = AutonaWebPanelHost(shell=AutonaShellOperations.instance)
        host.setObjectName("WebPanels")
        self._webPanelHost = host
        self.setCentralWidget(host)
        # The page the window reopens on: the one shown, or the one before a
        # menu page (AutonaWindowState.RememberedPanel). Saved with whether
        # the AppShell's sidebar is hidden as soon as either changes
        # (_SaveWindowState) -- once the saved ones have been read, further
        # on (_windowStateRestored) -- and on close (closeEvent).
        self._panelToRemember = window_state.DEFAULT_PANEL
        self._windowStateRestored = False
        host.panelChanged.connect(self._OnPanelChanged)
        host.navCollapsedChanged.connect(self._OnNavCollapsedChanged)
        # The first load's end takes the loading spinner away
        # (_OnPageLoadFinished: STARTUP phase=panel-shown), and so does
        # its cap (SPINNER_CAP_MS), started with it further on.
        self._panelShown = False
        self._loading_overlay = None
        self._spinnerCap = QTimer(self)
        self._spinnerCap.setSingleShot(True)
        self._spinnerCap.setInterval(self.SPINNER_CAP_MS)
        self._spinnerCap.timeout.connect(self._OnSpinnerCapReached)
        host.pageLoadFinished.connect(self._OnPageLoadFinished)

        # The tournamentInfo.* writer, the wiring the headless shell makes
        # too (ADR-005 S3c). A payload that does not load is told by the
        # pages (ADR-005 S-final SF-5b-2: the AppShell's notice, the Event
        # page's card, both from the state's tournamentInfo.load); the
        # window's QMessageBox went in SF-5b-3. (Forgetting the selected
        # game on a clear is core's TournamentService: S4f.)
        wire_tournament_data()

        # The shell operations' main window: what opens the SCORE page's set
        # selection for the load_set hotkey and the legacy open-set route
        # (ADR-005 S4ab-2; OpenPanel) and answers the shell requests.
        AutonaShellOperations.instance.UiMounted(self)
        # The updater's dialogs ("up to date", the check's error, the new
        # version's) open over this window (ADR-005 S-final SF-5b-1): with
        # no parent they could open behind it while it is always on top.
        # The updater is bootstrap_gui()'s, built before this window; its
        # first check (the startup's silent one, further down) comes after
        # this.
        AutonaUpdater.instance.SetParent(self)

        self.qtSettings = QSettings("taguyan", "Autona")

        if self.qtSettings.value("geometry"):
            self.restoreGeometry(self.qtSettings.value("geometry"))

        # The page and the sidebar the window reopens on. Before the show:
        # the host's first load (once the web server is up) is on this page.
        # Read before anything is saved (the old keys are read into the new
        # ones here, only while those are absent): saving on each change
        # starts after this.
        host.SetNavCollapsed(
            window_state.RestoreNavCollapsed(self.qtSettings))
        host.ShowPanel(window_state.RestorePanel(self.qtSettings))
        self._windowStateRestored = True

        self.show()
        # Right after the show, so there is no gap between the two (it
        # returns within autona_splash.FINISH_JOIN_S whatever happens).
        splash.finish(self)
        # The main window may not take focus at launch; theme it explicitly.
        _titleBarThemer.Apply(self.windowHandle())
        log_startup_phase("core-shown")

        # Cover the window with a spinner until its page is there: the web
        # server is built and started first (_StartWebServer), and the host
        # builds its view and loads its page after that. The spinner goes
        # on the first load's end (_OnPageLoadFinished; a view that could
        # not be built is one), when the web server fails to start
        # (OnWebServerStartFailed), or past its cap whatever holds it
        # (_OnSpinnerCapReached).
        self._loading_overlay = LoadingOverlay(self)
        self._loading_overlay.start()
        self._spinnerCap.start()

        # The web server is built once the window and the spinner are
        # painted (Step 4c; ADR-005 S-final SF-5b-3). Building the
        # Flask/SocketIO app runs on the GUI thread and blocks it for a few
        # seconds -- the spinner stands still meanwhile -- so the operator
        # sees the window first. It used to wait for the spinner to go, and
        # the operator then faced the Qt panels; the window has nothing to
        # show before the server now, so the spinner stays up over it.
        #
        # call_when_painted() dispatches through a zero-delay timer, so this
        # runs from the event loop and not from inside a Qt signal handler:
        # the LAN opt-in and port-conflict QMessageBox exec() calls made below
        # are ordinary top-level modals (over the spinner) and do not violate
        # the no-nested-modal / no-reentrant-dispatch rule (pitfalls
        # #9). A window never painted (started minimized) starts it after a
        # short wait anyway.
        self._loading_overlay.call_when_painted(self._StartWebServer)

        # NOTE: countries are loaded once, early in __init__ (see
        # CountryCatalog.instance.Load() above); do not load them a second time.
        AutonaTournamentDataProvider.instance.UiMounted()
        # The game loader's startup: the start.gg character table, the first
        # scan and the last pick restored (core's GameLoader, built by
        # bootstrap_core(); the asset adapter's UiMounted called it until
        # ADR-005 S-final SF-6c).
        GameLoader.instance.Start()
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

        # What the app is busy with (Start.gg fetches, asset loads) is the
        # AppShell's footer since ADR-005 S-final SF-5b-3 (core's
        # StatusService through GET /api/v1/status and Socket.IO's
        # `status`); the Qt status bar that showed it went.

    def _SetDefaultGeometry(self):
        """A first launch's size and place (DEFAULT_WINDOW_SIZE, centred on
        the screen's free area and kept within it). A saved geometry
        replaces it (restoreGeometry, further on in _BuildUi). The Qt
        sidebar's contents used to make the window tall enough by
        themselves; the shared view asks for no size."""
        area = self.screen().availableGeometry()
        width = min(DEFAULT_WINDOW_SIZE[0], area.width())
        height = min(DEFAULT_WINDOW_SIZE[1], area.height())
        self.setGeometry(area.x() + (area.width() - width) // 2,
                         area.y() + (area.height() - height) // 2,
                         width, height)

    # ── Web server, started after the window is shown (startup Step 4c) ──

    def _StartWebServer(self):
        """Build and start the Flask/SocketIO server, off the startup path.

        Registered with LoadingOverlay.call_when_painted() at the end of
        _BuildUi, so it runs from the event loop once the window and its
        loading spinner are painted; the spinner stays up until the page has
        loaded (_OnPageLoadFinished). Everything below is the former
        __init__ block, in the same order.

        A server that could not be built or started (an exception here: the
        construction, the port guard, the start, the wiring) is shown as a
        start failure, as the server thread's bind failure is
        (OnWebServerStartFailed): nothing above this catches it, and the
        spinner would stay up over a page that never comes.

        The spinner's cap (SPINNER_CAP_MS) is held while this runs -- the
        operator's answers to the LAN question and the port guard, and the
        server's construction, which holds this thread -- and counts again
        from its end, whatever happens in it, while the spinner is up.
        """
        if self._shuttingDown or self.webserver is not None:
            return

        self._spinnerCap.stop()
        try:
            # One-time migration prompt: the bind default changed from
            # 0.0.0.0 to 127.0.0.1, which would silently break LAN OBS setups
            # (docs/operations/runtime-operations.md). Ask once, persist the
            # answer. Not while settings.json could not be read (even after
            # the launch's retries): "not decided yet" is then the
            # stand-in's, and the answer -- in effect for the run, put back
            # over the file when it can be read -- would replace the one it
            # holds. That run listens on this PC only (the default) and
            # writes nothing; the next launch asks.
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
                # The dialog above ran a nested event loop; the window may
                # have been closed while it was up.
                if self._shuttingDown:
                    return

            try:
                # The failure callback runs on the server thread; what it
                # shows belongs on the GUI thread, which is where the old
                # queued start_failed signal delivered it too. Nothing else
                # to hand in: the desktop's side of the web operations (the
                # game loader, the shell operations) was registered by
                # bootstrap_gui() (ADR-005 S3e-9; the board host too until
                # S4ad). The server's module is read here, after the LAN
                # question, so that neither the window nor the dialog waits
                # for the Flask stack (_LoadWebServer); a failed import is
                # this block's start failure too.
                WebServer, check_webserver_port = _LoadWebServer()
                self.webserver = WebServer(
                    on_start_failed=lambda error: AutonaQtHelper.gui_executor(
                        lambda: self.OnWebServerStartFailed(error)))
                # Probe the very interface the server will bind (LAN vs
                # loopback).
                check_webserver_port(
                    self.webserver.port, parent=self,
                    host=self.webserver.host_name)
                self.webserver.start()
                # A game load (the bridge's game_loaded: core's
                # GameLoader.loaded) -> program_state + characters, a scan
                # (games_scanned) -> games_changed, db_updated -> the player
                # DB: the wiring the headless shell makes too (ADR-005
                # S-final SF-6c; the asset adapter's onLoad / onLoadAssets
                # until then, and the window relayed onLoad through a
                # GameChanged of its own until SF-6b -- the Qt game
                # button's, until SF-5b-3, which also emitted it on every
                # tournament_changed).
                # Here, together with the server: at its old place in
                # __init__ there is no webserver instance to bind to.
                # Nothing is lost by connecting after LoadDB() — clients
                # pull the player DB on demand (GET /playerdb, socket
                # 'playerdb') and none can be connected before the server
                # is listening anyway.
                wire_web_server(self.webserver)
            except Exception as error:
                logger.opt(exception=True).error(
                    "The web server could not be started")
                self.OnWebServerStartFailed(f"{type(error).__name__}: {error}")
                return

            # Release the embedded view that fetches its pages from this
            # server (the React panel host): it does not even build its view
            # before this point, and builds it from the event loop after
            # this returns (ADR-005 S4g review M4).
            MarkWebServerReady()

            log_startup_phase("webserver-ready")
        finally:
            if self._loading_overlay is not None and not self._shuttingDown:
                self._spinnerCap.start()

    def _OnPageLoadFinished(self, ok):
        """The shared view's first load is over (AutonaWebPanelHost.
        pageLoadFinished): the loading spinner goes, and STARTUP
        phase=panel-shown marks the page there. A first load that failed for
        good (the loader's retries used up, or a view that could not be
        built) takes the spinner away too: the host shows its retry view
        then (AutonaWebPanelHost.ShowLoadFailure; the start failure's label
        for a view not built). Once; logged even after the spinner's cap
        took the spinner away."""
        if self._panelShown:
            return
        self._panelShown = True
        self._DismissLoadingOverlay()
        if ok:
            log_startup_phase("panel-shown")
        else:
            logger.warning("The window's first page did not load")

    def _OnSpinnerCapReached(self):
        """The spinner has covered the window SPINNER_CAP_MS (counted from
        the web server's start, or from the window's show before that)
        without the first page: whatever holds it -- a load that does not
        end, a path nothing foresaw -- the operator is not left facing it.
        It goes with a warning, and the host offers to load the page again
        (AutonaWebPanelHost.ShowLoadFailure; nothing when the page loaded
        meanwhile or never can). The first load's end, should it come, is
        still logged (_OnPageLoadFinished)."""
        if self._loading_overlay is None:
            return
        logger.warning(
            f"The window's first page did not show within "
            f"{self.SPINNER_CAP_MS // 1000} s: the loading spinner goes")
        self._DismissLoadingOverlay()
        self._webPanelHost.ShowLoadFailure()

    def _DismissLoadingOverlay(self):
        self._spinnerCap.stop()
        overlay, self._loading_overlay = self._loading_overlay, None
        if overlay is not None:
            overlay.finish()

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

    # ── The pages ─────────────────────────────────────────────────────────
    # The AppShell moves between its pages itself (its navigation and its
    # Help & Settings menu); the window follows which one shows
    # (_OnPanelChanged) and asks for one only from outside the page
    # (OpenPanel).

    def _OnPanelChanged(self, panel):
        """The host shows `panel` now (AutonaWebPanelHost.panelChanged): the
        page to reopen on, unless it is a menu page -- then the one before
        it stays (AutonaWindowState.RememberedPanel), as the Qt sidebar kept
        its item while a menu page showed. Saved at once (_SaveWindowState:
        nothing is written for a menu page, the page before it being saved
        already)."""
        self._panelToRemember = window_state.RememberedPanel(
            panel, self._panelToRemember)
        self._SaveWindowState()

    def _OnNavCollapsedChanged(self, collapsed):
        """The AppShell's sidebar was hidden or shown again
        (AutonaWebPanelHost.navCollapsedChanged): saved at once."""
        self._SaveWindowState()

    def _SaveWindowState(self):
        """Keep the page to reopen on and whether the AppShell's sidebar is
        hidden as soon as either changes, so a crash or a forced end --
        which skip closeEvent -- does not lose them: what differs from the
        saved values is written (AutonaWindowState.SaveWindowState) and
        flushed to disk (QSettings.sync). Nothing for the AppShell's other
        state in its URL (the tournament load failure it told of: the host
        tells no change for it). Nothing before the saved values -- the old
        keys among them -- have been read (_windowStateRestored), nor once
        the window closes (closeEvent saves them with the geometry)."""
        if not self._windowStateRestored or self._shuttingDown:
            return
        try:
            if window_state.SaveWindowState(
                    self.qtSettings, self._panelToRemember,
                    self._webPanelHost.NavCollapsed()):
                self.qtSettings.sync()
        except Exception:
            logger.opt(exception=True).error(
                "Error saving the page the window reopens on")

    def OpenPanel(self, name):
        """Bring the window to the front on page `name` (a React panel's:
        WebAccess.WEB_PANELS) -- what a request from outside the page asks
        for: the load_set hotkey and the legacy open-set route show SCORE
        and then have its page open the set selection (AutonaShellOperations,
        ADR-005 S4ab-2). Returns the host, to ask its page further
        (AutonaWebPanelHost.OpenScoreSets); None once the window is closing:
        a request then would show it again while it is torn down."""
        if self._shuttingDown:
            logger.info(f"The {name} page was asked for while the window "
                        "closes: nothing opened")
            return None
        self._BringToFront()
        self._webPanelHost.ShowPanel(name)
        return self._webPanelHost

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

    def OnWebServerStartFailed(self, error: str):
        """The web server could not start (marshalled here from the server
        thread through gui_executor, see _StartWebServer). The window's page
        can never load then, so the window says why in its place -- the
        host's local error view (AutonaWebPanelHost.ShowStartFailure: a
        label, no page) -- and the loading spinner goes. No dialog (ADR-005
        S-final SF-5b-3; a QMessageBox until then): the error view is what
        the window shows. Also for a server that could not be built or
        started at all (_StartWebServer's exception): its port is then the
        one it was to bind (WebServer.Serving), or the settings' one when
        the server's module could not even be imported (_LoadWebServer):
        nothing here may raise, the error view and the spinner's end must
        come whatever failed."""
        if self.webserver is not None:
            port = self.webserver.port
        else:
            try:
                port = _LoadWebServer()[0].Serving()[0]
            except Exception:
                port = SettingsManager.Get("general.webserver_port",
                                           DEFAULT_WEBSERVER_PORT)
        title = QApplication.translate(
            "portguard", "Web server failed to start")
        body = (QApplication.translate(
            "portguard",
            "The web server could not start on port {port}:\n\n{error}\n\n"
            "OBS browser sources and the Web UI will not work.\n"
            "Change general.webserver_port in user_data/settings.json and "
            "restart Autona, or quit the application that is using the "
            "port.")
            .replace("{port}", str(port))
            .replace("{error}", error))
        self._webPanelHost.ShowStartFailure(f"{title}\n\n{body}")
        self._DismissLoadingOverlay()

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
            window_state.SaveWindowState(
                self.qtSettings, self._panelToRemember,
                self._webPanelHost.NavCollapsed())
        except Exception:
            logger.opt(exception=True).error("Error saving window settings on shutdown")

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

        # The archive clipper's worker (ADR-006 A1): after the web server, so
        # no request can start or resume a job any more. A running job is
        # cancelled (resumable) and its worker -- with the yt-dlp / ffmpeg it
        # started -- ended if it has not stopped in 5 seconds. It is the
        # runner's run (ADR-009 D9), stopped through its own handle; its end
        # gives PluginJobs' heavy lane back. Before the runner's Shutdown
        # below, which then finds it ended.
        try:
            from .core.ArchiveJobService import ArchiveJobService
            if ArchiveJobService.instance is not None:
                ArchiveJobService.instance.Shutdown(5.0)
        except Exception:
            logger.opt(exception=True).error(
                "Error stopping the archive worker on shutdown")

        # The plugin runs (ADR-009 E.2), at the archive's place: after the
        # web server (no request can start one any more) and before the
        # tasks. Each run is cancelled and, past the 5 seconds' grace, its
        # job object ended (PluginJobs.Shutdown: its own runs only). Then the
        # runner refuses any launch and ends what is left -- a dev plugin's
        # describe on a request thread the server's stop does not drain;
        # the runs are already their owners' to have stopped (the archive's
        # above, PluginJobs'). The waits are one after another: at worst the
        # archive's 5 s, the runs' 5 s and the runner's wait for a killed
        # run to end (PluginRunner.KILL_WAIT_SECONDS, 2 s) -- 12 s, inside
        # the close watchdog's 60. Each in its own try, so a failure of the
        # runs' stop still has the runner refuse and end what is left.
        from .core.PluginJobs import PluginJobs
        from .core.PluginRunner import PluginRunner
        from .core.PluginTypes import SHUTDOWN_GRACE_SECONDS
        try:
            if PluginJobs.instance is not None:
                PluginJobs.instance.Shutdown(
                    float(SHUTDOWN_GRACE_SECONDS["desktop"]))
        except Exception:
            logger.opt(exception=True).error(
                "Error stopping the plugin runs on shutdown")
        try:
            if PluginRunner.instance is not None:
                PluginRunner.instance.Shutdown(0.0)
        except Exception:
            logger.opt(exception=True).error(
                "Error stopping the plugin runner on shutdown")

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

        # The match log (ADR-006 A2): after the web server and the tasks,
        # which can still publish a change it records. Closes the open
        # record and lets its writer finish (a daemon thread the
        # interpreter's exit would not wait for).
        try:
            from .core.MatchLogService import MatchLogService
            if MatchLogService.instance is not None:
                MatchLogService.instance.Shutdown(2.0)
        except Exception:
            logger.opt(exception=True).error(
                "Error closing the match log on shutdown")

        # --- Best-effort housekeeping: tmp/ ------------------------------
        # After the web server and the tasks have stopped (the order is
        # deliberate; the archive worker does not use tmp/): until then a render job can still be writing
        # tmp/thumbnail/preview.png or tmp/result/result_preview.png and a
        # request can be reading one, which made the removal stop half way
        # (WinError 32) or let a job recreate tmp/ right after it.
        try:
            tmpDir = AutonaResolve("tmp")
            if os.path.isdir(tmpDir):
                shutil.rmtree(tmpDir)
        except Exception:
            # Files under tmp/ can still be locked by the OS or by another
            # process (or a task that did not stop in time); leaving them
            # behind is harmless, blocking exit is not.
            logger.opt(exception=True).error("Error removing tmp directory on shutdown")

        try:
            # allWindows() returns QWindow, not QWidget: comparing against
            # `self` was always true. Compare with our own window handle.
            for window in QApplication.allWindows():
                if window is not self.windowHandle():
                    window.close()
        except Exception:
            logger.opt(exception=True).error("Error closing auxiliary windows on shutdown")

        super().closeEvent(event)

        # The crash log stays open until the atexit close_crash_log, after the
        # interpreter has joined the writer threads (Qt cleanup registered
        # earlier runs after it and is not covered). If that exit hangs, every
        # thread's stack is written there after 60 seconds; the normal exit
        # cancels this (AutonaCrashLog.close_crash_log).
        AutonaCrashLog.arm_exit_watchdog(crashlog, 60)

        # Quit explicitly instead of relying on quitOnLastWindowClosed:
        # off-screen helper widgets (thumbnail/result renderers) can keep the
        # last-window heuristic from ever firing, which leaves a zombie
        # process behind. Exiting must not depend on the cleanup above either.
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def SetAlwaysOnTop(self, on):
        """Set "Always on top" to `on`: the shell request alwaysOnTop's (the
        AppShell's Help & Settings menu; AutonaShellOperations, ADR-005
        S-final SF-5a-1). The window's flag -- Qt hides and shows the window
        again for it -- and the pages are told (the host's
        PublishShellState). Nothing when it is that already. The window's
        own bool is the state (the Qt menu's check was until SF-5b-3)."""
        on = bool(on)
        if on == self._alwaysOnTop:
            return
        self._alwaysOnTop = on
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()
        self._webPanelHost.PublishShellState()

    def IsAlwaysOnTop(self):
        """Whether "Always on top" is on."""
        return self._alwaysOnTop

    def LoadTheme(self):
        # The app is dark-only. Theme is applied once at startup; there is
        # no runtime light/dark toggle (it used to re-polish the entire
        # proxy-embedded bracket on the GUI thread and froze for 30s+).
        qdarktheme.setup_theme(
            "dark",
            custom_colors=UI.DARK_CUSTOM_COLORS,
            additional_qss=UI.global_qss())
