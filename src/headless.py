"""The headless shell: ``python main.py --headless`` (ADR-005 S3c).

Autona without the desktop window: a QCoreApplication running the event
loop, the core state layer, the tournament provider and the web server.
OBS overlays and the browser UI (/web) work as they do beside the window,
and so does every operation that is core's (scores, 反映, colours, display
options, swap, the clears, best of, the match bar, team names, losers, the
tournament, the game). With no board, nothing is held: a write is published
at once -- there is no 反映 button to wait for. What needs a board or panel
widget answers 503 "not available in headless" (src/AutonaWebOperations.py).

What the window does at startup and this shell does too, in its own words:

- the per-run locale and the round names;
- the score subtree reset, the three boards as freshly built desktop boards
  publish them (skeleton, the empty player cards' slots, the crew board's
  launch write) and the panel seeds, in one save cycle (the window's
  ScoreboardManager / CreateScoreboard / the board widgets / _SeedPanelState);
- the country catalog, loaded directly (the window loads it through its Qt
  adapter);
- the tournament provider's wiring (bootstrap.wire_tournament_data, the
  window's too) and the restore of the tournament URL;
- the games: scanned and loaded through GameAssetCatalog directly -- no Qt
  models (HeadlessGames) -- with the provider's game re-applied after the
  scan and the last pick restored;
- the player DB, and the web server with the window's pushes
  (bootstrap.wire_web_server);
- the LAN exposure and the port check decided from settings.json and only
  logged: there is nobody to ask;
- a stop on Ctrl+C / Ctrl+Break, on the console's close button and on
  SIGTERM (where the platform delivers it), which lets ./out and
  user_data/state.json finish writing first.

Not here: the board-level reactions to a game load (the players per team a
game sets, the best of re-published in the new locale) -- they are a built
board's (S2g), and a headless run has none. So the boards keep the player
slots seeded at launch, before any game is restored: one per team.

Importing this module does nothing (agent-pitfalls #1): main.py calls run(),
and before its single-instance gate the launch checks (instance_tag,
refuse_frozen_headless: Autona.exe does not run headless).
"""
import faulthandler
import os
import re
import shutil
import signal
import sys
import threading
import traceback
from pathlib import Path

from loguru import logger
from qtpy.QtCore import QMetaObject, QObject, Qt, QTimer, Signal


# How long a console close waits for the shutdown before returning: Windows
# ends the process when the handler returns, and allows about five seconds.
_CLOSE_WAIT_SECONDS = 4.5

# Windows console control events (wincon.h).
_CTRL_CLOSE_EVENT = 2
_CTRL_LOGOFF_EVENT = 5
_CTRL_SHUTDOWN_EVENT = 6

_LOG_FORMAT = ("[{time:YYYY-MM-DD HH:mm:ss}] - {level} - "
               "{file}:{function}:{line} | {message}")

INSTANCE_TAG_VARIABLE = "AUTONA_INSTANCE_TAG"
_INSTANCE_TAG_PATTERN = re.compile(r"[A-Za-z0-9-]+")

FROZEN_REFUSAL = (
    "Autona.exe does not run headless (--headless).\n\n"
    "Run it from the source instead: python main.py --headless")

# MessageBoxW flags (winuser.h): MB_ICONINFORMATION | MB_SETFOREGROUND (and
# MB_OK, 0).
_REFUSAL_BOX_FLAGS = 0x40 | 0x10000


# ── Launch checks (main.py, before its single-instance gate) ─────────────

def instance_tag(environ, frozen):
    """The suffix main.py puts on its single-instance names, from
    AUTONA_INSTANCE_TAG in `environ`; "" for none.

    A test hook -- tests/test_headless_smoke.py starts a headless process
    beside an Autona the owner may have running -- so it is read only when
    running from source: a stray variable must never let a second copy of
    the installed app (`frozen`) past the gate. Letters, digits and hyphens
    only, as it becomes part of a mutex and a pipe name; anything else is
    ignored, with a warning.
    """
    if frozen:
        return ""
    value = environ.get(INSTANCE_TAG_VARIABLE)
    if not value:
        return ""
    if _INSTANCE_TAG_PATTERN.fullmatch(value):
        return value
    logger.warning(
        f"{INSTANCE_TAG_VARIABLE} ignored: only letters, digits and hyphens "
        f"are allowed (got {value!r})")
    return ""


def refuse_frozen_headless(message_box=None):
    """Tell whoever started ``Autona.exe --headless`` that the exe does not
    run headless (main.py exits right after, before its gate).

    The exe has no console, so a message box says it (`message_box`: the
    MessageBoxW signature; Windows' by default). Nothing else: the
    single-instance mutex is not taken, so the app double-clicked next
    starts as usual.
    """
    if sys.stderr is not None:
        try:
            sys.stderr.write(FROZEN_REFUSAL + "\n")
        except Exception:
            pass
    try:
        if message_box is None:
            import ctypes
            message_box = ctypes.windll.user32.MessageBoxW
        message_box(None, FROZEN_REFUSAL, "Autona", _REFUSAL_BOX_FLAGS)
    except Exception:
        pass


class HeadlessSignals(QObject):
    # A game was loaded (the headless counterpart of the window's
    # GameChanged): the web server pushes the state and the characters.
    gameChanged = Signal()


class HeadlessGames:
    """The games, through GameAssetCatalog directly (no Qt models).

    The headless counterpart of the asset adapter (AutonaGameAssetManager):
    the startup scan and start.gg character download, the load by codename
    (registered as AutonaWebOperations.loadGameByCodename, same signature),
    the selector the tournament provider loads its game through
    (selectGameFromStartGGId), the provider's game re-applied after the
    scan, and the last pick persisted and restored
    (SELECTED_GAME_CODENAME). A load runs on its own thread, one at a time;
    what follows it -- the pick persisted, `signals.gameChanged` -- runs on
    the main thread, where the adapter's onLoad handlers run.
    """

    def __init__(self, catalog, provider):
        self.catalog = catalog
        self.provider = provider
        self.signals = HeadlessSignals()
        self._lock = threading.Lock()
        self._pendingRestore = False

    # ── startup ──────────────────────────────────────────────────────────

    def Start(self):
        """The startup scan (and the start.gg character table refresh)."""
        threading.Thread(
            target=self.catalog.DownloadStartGGCharacters,
            name="AutonaCharactersDownload", daemon=True).start()
        self._pendingRestore = True
        threading.Thread(
            target=self._Scan, name="AutonaGameScan", daemon=True).start()

    def _Scan(self):
        try:
            self.catalog.ScanGames()
        except Exception:
            logger.error(traceback.format_exc())
            return
        from .Helpers import AutonaQtHelper
        AutonaQtHelper.gui_executor(self.OnScanned)

    def OnScanned(self):
        """After a scan, on the main thread, in the order the desktop's
        onLoadAssets slots run: the provider's game first (bootstrap_gui
        connects it first there), then the last pick -- on the first scan
        only, as the adapter's startup restore."""
        self.provider.SetGameFromProvider()
        if not self._pendingRestore:
            return
        self._pendingRestore = False

        from .core.SettingsManager import SettingsManager
        codename = SettingsManager.Get("SELECTED_GAME_CODENAME", "") or ""
        if not codename:
            return
        if codename not in self.catalog.GameCodenames():
            logger.info(
                f"Last used game '{codename}' is not installed anymore - "
                "skipping restore")
            return
        # Mods stay off on restore, as on the desktop.
        self.LoadGameByCodename(codename)

    # ── loading ──────────────────────────────────────────────────────────

    def SelectGameFromStartGGId(self, gameid):
        """The provider's selector: a start.gg videogame id -> the game."""
        codename = self.catalog.GameFromStartGGId(gameid)
        if codename:
            self.LoadGameByCodename(codename)

    def LoadGameByCodename(self, codename, async_mode=True, mods_active=False,
                           mods_reload_mode=False, done=None):
        """Load `codename`; on a thread of its own unless `async_mode` is off.

        `done()`, if given, is called once the attempt is over however it
        went (the adapter's contract: /api/v1 releases its in-progress flag
        there)."""
        if not codename:
            if done is not None:
                done()
            return
        if async_mode:
            threading.Thread(
                target=self._Load,
                args=(codename, mods_active, mods_reload_mode, done),
                name="AutonaGameLoader", daemon=True).start()
        else:
            self._Load(codename, mods_active, mods_reload_mode, done)

    def _Load(self, codename, modsActive, reload, done):
        from .core.StateManager import StateManager
        from .Helpers import AutonaQtHelper
        try:
            with self._lock:
                # The adapter's _Load without the Qt models: the start.gg
                # character table re-read, then the selection in one save
                # cycle. Skipped (None) for an unknown codename or the same
                # game again without reload.
                self.catalog.ReloadStartGGCharacters()
                with StateManager.SaveBlock():
                    snapshot = self.catalog.SelectGame(
                        codename, modsActive=modsActive, reload=reload)
                if snapshot is None:
                    return
            logger.info(f"Game loaded: {codename}")
            AutonaQtHelper.gui_executor(self._OnLoaded)
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            if done is not None:
                done()

    def _OnLoaded(self):
        # The pick persisted (the adapter's _PersistSelectedGame), then the
        # listeners: the player DB refresh and the web server's push.
        from .core.SettingsManager import SettingsManager
        from .core.StateManager import StateManager
        SettingsManager.Set(
            "SELECTED_GAME_CODENAME",
            StateManager.Get("game.codename", "") or "")
        self.signals.gameChanged.emit()


class HeadlessShell:
    """The running shell: what the stop handlers, the shutdown and the
    tests need. Made before the assembly (run() installs the stop handlers
    on it first); Assemble() fills in the games and the web server."""

    def __init__(self, app, games=None, webserver=None):
        self.app = app
        self.games = games
        self.webserver = webserver
        # Set by the first stop request. One that comes while the shell is
        # being assembled finds no event loop to quit; run() looks at this
        # once the assembly is done and does not start one.
        self.stopRequested = threading.Event()
        # Set once the shutdown has written everything (see Shutdown()).
        self.stopped = threading.Event()
        self._handlers = []
        # Removes the console control handler (Windows; see
        # _InstallStopHandlers). None when there is none.
        self._removeConsoleHandler = None

    def RequestStop(self):
        """Stop, from any thread: quit the event loop, or -- before there is
        one to quit -- leave the request for run()."""
        self.stopRequested.set()
        # Queued from every thread, the main one included (a direct quit()
        # before exec() is dropped): the loop quits as soon as it runs, if
        # the flag above was not seen first.
        QMetaObject.invokeMethod(
            self.app, "quit", Qt.ConnectionType.QueuedConnection)

    def Shutdown(self):
        """Stop serving, then let ./out and user_data/state.json finish."""
        from .core.StateManager import StateManager
        from .core.StatePersistence import StatePersistence
        logger.info("headless: stopping")
        if self.webserver is not None:
            try:
                # Short: a console close leaves about five seconds in all.
                self.webserver.stop(2.0)
            except Exception:
                logger.error(traceback.format_exc())
        if not StateManager.JoinExports(10):
            logger.warning("headless: the ./out export did not finish")
        if not StatePersistence.Flush(10):
            logger.warning("headless: user_data/state.json was not written")
        logger.info("headless: stopped")
        # Last, with nothing left to write: a console event is Windows'
        # default again from here (the process ends at once). A close the
        # handler is waiting on still returns through `stopped` below; the
        # callback itself stays referenced in _handlers.
        if self._removeConsoleHandler is not None:
            remove, self._removeConsoleHandler = (
                self._removeConsoleHandler, None)
            try:
                remove()
            except Exception:
                logger.opt(exception=True).warning(
                    "headless: could not remove the console handler")
        self.stopped.set()


def _ConfigureLogging():
    """The desktop's sinks: the console (INFO here, as this is the
    operator's only view), logs/autona.log and logs/tsh-error.log, and the
    crash log."""
    crashpath = Path('./logs/tsh-crash.log').resolve()
    crashpath.parent.mkdir(exist_ok=True)
    faulthandler.enable(crashpath.open(mode='w'))

    handlers = []
    if sys.stdout is not None:
        # The legacy console code page cannot encode every log message (see
        # src/Autona.py).
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(
                    encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass
        handlers.append({
            "sink": sys.stdout, "level": "INFO",
            "format": ("<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                       "<level>{level}</level> | <level>{message}</level>")})
    logger.configure(handlers=handlers)
    logger.add("./logs/autona.log", format=_LOG_FORMAT, encoding="utf-8",
               level="INFO", rotation="20 MB", retention=5)
    logger.add("./logs/tsh-error.log", format=_LOG_FORMAT, encoding="utf-8",
               level="ERROR", rotation="20 MB", retention=5)


def _SeedBoards():
    """The boards as the desktop's freshly built ones publish them.

    The score subtree reset (the scoreboard dock's construction) and every
    board's skeleton come first, as on the desktop. Then what a built board's
    widgets add to it (ADR-005 S3d): the player slots its empty cards
    publish -- the score overlays draw nothing until team 1 has one -- and
    the crew board's launch write. The desktop builds a board only when it
    is shown; this shell builds none and serves all three, so all three get
    the seed. tests/test_headless_seed.py pins it against desktop boards.
    """
    from .core import StateSchema as schema
    from .core.CrewBattleService import CrewBattleService
    from .core.PlayerService import SeedEmptyRoster
    schema.ResetScoreRoot()
    for number in (1, 2, 3):
        schema.SeedBoard(number)
    for number in (1, 2):
        SeedEmptyRoster(number)
    CrewBattleService(3).SeedDefaults()


def _SeedState():
    """The state a desktop launch starts from, in one save cycle: the boards
    (_SeedBoards) and the panel seeds (Window._SeedPanelState)."""
    from .core import StateSchema as schema
    from .core.ColorService import SeedColorState
    from .core.StateManager import StateManager
    from .core.TournamentInfoService import TournamentInfoService
    with StateManager.SaveBlock():
        _SeedBoards()
        schema.SeedPanelState()
        TournamentInfoService.instance.Seed()
        SeedColorState()


def _PrepareFiles():
    # What the window creates on first run (Window._BuildUi).
    os.makedirs("out", exist_ok=True)
    os.makedirs("./user_data/games", exist_ok=True)
    if not os.path.exists("layout/logo.png") and os.path.isdir("layout"):
        try:
            shutil.copy("assets/icons/icon.png", "layout/logo.png")
        except Exception:
            logger.opt(exception=True).warning(
                "Could not seed default tournament logo.")


def _OnTournamentLoadFailed(data):
    logger.warning(
        "The tournament URL could not be loaded. Make sure that it is "
        "correctly formatted and points to an existing event.")


def Assemble(app, shell=None):
    """Build the headless shell on `app` (a QCoreApplication); start serving.

    Everything run() does before the event loop, into `shell` (a new
    HeadlessShell if None). Returns the shell.
    """
    if shell is None:
        shell = HeadlessShell(app)
    from .Helpers import AutonaQtHelper
    from .bootstrap import (
        bootstrap_core, wire_tournament_data, wire_web_server)

    # The main thread, before anything can marshal to it.
    if AutonaQtHelper.gui_executor is None:
        AutonaQtHelper.init_gui_executor()
    bootstrap_core()

    from . import AutonaWebOperations
    from .AutonaPlayerDB import AutonaPlayerDB
    from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from .AutonaWebServer import WebServer, log_webserver_port
    from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    from .core.CountryCatalog import CountryCatalog
    from .core.GameAssetCatalog import GameAssetCatalog
    from .core.SettingsManager import SettingsManager

    AutonaLocaleHelper.LoadLocale()
    AutonaLocaleHelper.LoadRoundNames()
    _PrepareFiles()
    # Directly: the window loads it through its Qt adapter (flag icons and
    # the country model), which this shell has no use for.
    CountryCatalog.instance.Load()
    _SeedState()

    provider = AutonaTournamentDataProvider.instance
    games = HeadlessGames(GameAssetCatalog.instance, provider)
    shell.games = games
    # ORDER CONSTRAINT: both before the provider can load a tournament
    # (UiMounted below) and before the web server takes a request. The
    # selector is what a tournament's start.gg videogame id loads through;
    # without it the provider logs that the game was not applied.
    provider.selectGameFromStartGGId = games.SelectGameFromStartGGId
    AutonaWebOperations.loadGameByCodename = games.LoadGameByCodename
    # The player DB follows a game load, as it follows the adapter's onLoad
    # on the desktop.
    games.signals.gameChanged.connect(AutonaPlayerDB.SetupModel)

    wire_tournament_data(on_failure=_OnTournamentLoadFailed)

    AutonaPlayerDB.LoadDB()

    def onStartFailed(error):
        # Serving is all this shell does: a process that cannot is ended
        # (exit code 1) rather than left holding the single-instance lock.
        # Called on the server thread; the exit is the event loop's.
        logger.error(f"The web server could not start: {error}")
        AutonaQtHelper.gui_executor(lambda: app.exit(1))

    webserver = WebServer(on_start_failed=onStartFailed)
    shell.webserver = webserver
    wire_web_server(webserver, games.signals.gameChanged)

    # The tournament URL and the games, restored as the window restores
    # them (its UiMounted calls).
    provider.UiMounted()
    games.Start()

    # Decided from settings.json, never asked (the desktop asks once).
    if SettingsManager.Get("general.webserver_listen_lan", None) is None:
        logger.info(
            "general.webserver_listen_lan is not set in "
            "user_data/settings.json: listening on this PC only. Set it to "
            "true to accept connections from the LAN (writes from another "
            "computer need the access token, general.webserver_token).")
    log_webserver_port(webserver.port, host=webserver.host_name)
    webserver.start()
    logger.info(
        f"headless: serving http://{webserver.host_name}:{webserver.port}/ "
        "(Ctrl+C to stop)")
    return shell


def _InstallStopHandlers(shell):
    """Ctrl+C, Ctrl+Break, the console's close button and SIGTERM -> stop.

    Windows delivers the console events to a handler of their own thread
    (SetConsoleCtrlHandler); for a close, the process is ended as soon as
    that handler returns, so it waits for the shutdown first. Elsewhere,
    and as a fallback, the Python signal handlers, with a timer that lets
    them run while the event loop is in Qt.

    Installed before the assembly (run()): a stop asked for while the shell
    is being built is kept (HeadlessShell.RequestStop) and honoured once it
    is built, through the same shutdown, instead of Windows' default --
    ending the process half-built. Shutdown() removes the console handler.
    """
    def onSignal(signum, frame):
        logger.info(f"headless: signal {signum}, stopping")
        shell.RequestStop()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            try:
                signal.signal(signum, onSignal)
            except (ValueError, OSError):
                pass
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(250)
    shell._handlers.append(heartbeat)

    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        kernel32.SetConsoleCtrlHandler.argtypes = [
            HandlerRoutine, wintypes.BOOL]
        kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL

        def onConsoleEvent(event):
            logger.info(f"headless: console event {event}, stopping")
            shell.RequestStop()
            if event in (_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT,
                         _CTRL_SHUTDOWN_EVENT):
                shell.stopped.wait(_CLOSE_WAIT_SECONDS)
            return True

        routine = HandlerRoutine(onConsoleEvent)
        if not kernel32.SetConsoleCtrlHandler(routine, True):
            logger.warning(
                "headless: could not install the console handler "
                f"(error {ctypes.get_last_error()})")
            return
        # The callback must outlive the registration -- and the removal: a
        # close the handler is waiting on is still running it then.
        shell._handlers.append(routine)
        shell._removeConsoleHandler = (
            lambda: kernel32.SetConsoleCtrlHandler(routine, False))
    except Exception:
        logger.opt(exception=True).warning(
            "headless: could not install the console handler")


def run(app) -> int:
    """Run the headless shell on `app` until it is stopped; the exit code.

    `app` is the QCoreApplication main.py built for the single-instance
    gate (its IPC server needs one before this runs).
    """
    _ConfigureLogging()
    logger.critical("=== Autona IS STARTING (headless) ===")
    shell = HeadlessShell(app)
    # ORDER CONSTRAINT: before the assembly, so a stop asked for during it
    # goes through the shutdown below (see _InstallStopHandlers).
    _InstallStopHandlers(shell)
    try:
        Assemble(app, shell)
        if shell.stopRequested.is_set():
            # Asked during the assembly, before there was a loop to quit.
            logger.info("headless: a stop was asked for while starting")
            code = 0
        else:
            code = app.exec()
    finally:
        shell.Shutdown()
    return code
