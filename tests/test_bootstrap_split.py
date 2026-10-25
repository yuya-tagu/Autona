"""The two-stage composition root (ADR-005 S3c).

bootstrap_core() builds what every shell needs and must run under a bare
QCoreApplication -- the headless shell (main.py --headless) stops there.
bootstrap_gui() adds the desktop's Qt adapters on top. These tests pin:

- every step of each stage -- constructions, the provider / PlayerDB /
  hotkey wirings, the Initialize() loads, the Designer migration -- and
  their order, read from src/bootstrap.py itself (a reorder or a move to the
  other stage has to update the expectation here, next to the ORDER
  CONSTRAINT comment that justifies it);
- that a real bootstrap_core() under QCoreApplication builds no desktop
  singleton and leaves the provider without a game selector, and that
  in_gui_thread() answers for the QCoreApplication's thread;
- that bootstrap_gui() builds the adapters, wires the provider to the game
  asset adapter -- its slot on onLoadAssets running before the adapter's
  own startup restore, on a real emit -- registers the desktop's board host
  and game loader for the web operations, switches the player DB model on,
  and still drops the persisted `score` subtree;
- that the wiring both shells make (bootstrap.wire_tournament_data /
  wire_web_server) is what the window and src/headless.py both call, and
  that neither makes those connections itself;
- that the web server, now a plain thread, serves, stops and frees its port,
  and reports a failed start through its callback.

The boots run in a subprocess inside a throwaway working directory: both
stages are guarded against re-entry, and the session harness has already
used them up in this process (tests/conftest.py).
"""
import ast
import json
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKER = "<<<AUTONA_BOOT_PROBE>>>"


# ── The steps of each stage, read from the source ───────────────────────


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _statements(body):
    # The `if X.instance is None:` guards hold most steps; walk into them in
    # source order.
    for statement in body:
        if isinstance(statement, ast.If):
            yield from _statements(statement.body)
            yield from _statements(statement.orelse)
        else:
            yield statement


def _steps(function_name):
    """Every construction / wiring / load step of a stage, in source order.

    `X()` is a singleton construction (`X.instance = X(...)`), `a.b = c.d`
    an attribute wiring, `f(args)` a call (logging left out).
    """
    tree = ast.parse((PROJECT_ROOT / "src" / "bootstrap.py").read_text(
        encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name)
    steps = []
    for statement in _statements(function.body):
        if isinstance(statement, ast.Assign):
            target = statement.targets[0]
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr == "instance" and isinstance(target.value, ast.Name):
                steps.append(f"{target.value.id}()")
            elif isinstance(statement.value, ast.Call):
                args = ", ".join(
                    _dotted(arg) or "..." for arg in statement.value.args)
                steps.append(
                    f"{_dotted(target)} = "
                    f"{_dotted(statement.value.func)}({args})")
            else:
                steps.append(
                    f"{_dotted(target)} = {_dotted(statement.value)}")
        elif (isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)):
            name = _dotted(statement.value.func)
            if name is None or name.startswith("logger."):
                continue
            args = ", ".join(
                _dotted(arg) or "..." for arg in statement.value.args)
            steps.append(f"{name}({args})")
    return steps


def test_bootstrap_core_steps_and_their_order():
    # Everything here runs under a bare QCoreApplication (see the subprocess
    # test below). The bad-word filter is built right after the locale
    # tables its injected lookups read; the player DB and its db_updated
    # relay come before the provider whose entrants worker writes to it
    # (ADR-005 S3e-1). StartGG's query texts are loaded right above the
    # provider that owns them; the Designer migration is last (before
    # anything can serve or list designs, which the shells start afterwards).
    assert _steps("bootstrap_core") == [
        "SettingsManager.Initialize()",
        "LayoutOptionsManager.Initialize()",
        "AutonaLocaleHelper.Initialize()",
        "BadWordFilter()",
        "BadWordFilter.instance.Initialize()",
        "StateManager.Initialize()",
        "StateManagerBridge()",
        "AutonaActions()",
        "GameAssetCatalog()",
        "GameAssetCatalog.instance.ResetGameState()",
        "PlayerDB()",
        "AutonaPlayerDB.Attach()",
        "StartGGDataProvider.Initialize()",
        "AutonaTournamentDataProvider()",
        "TournamentInfoService()",
        "CountryCatalog()",
        "AutonaDesignerService.MigrateLegacyProjects()",
    ]


def test_bootstrap_gui_steps_and_their_order():
    # bootstrap_core() first. The relative order of the constructions is the
    # one they had inside the old single bootstrap_core(); the provider and
    # PlayerDB wirings follow the adapter they connect to (the model switched
    # on before the first SetupModel), and the hotkeys are routed to
    # AutonaActions right after they exist. The web operations' desktop
    # registrations come last, after the dock and the adapter they point at.
    assert _steps("bootstrap_gui") == [
        "bootstrap_core()",
        "AutonaHotkeys()",
        "AutonaHotkeys.ConnectActions()",
        "AutonaGameAssetManager()",
        "AutonaTournamentDataProvider.instance.selectGameFromStartGGId"
        " = AutonaGameAssetManager.instance.SetGameFromStartGGId",
        "AutonaGameAssetManager.instance.signals.onLoadAssets.connect("
        "AutonaTournamentDataProvider.instance.SetGameFromProvider)",
        "AutonaPlayerDB.EnableModel()",
        "AutonaGameAssetManager.instance.signals.onLoad.connect("
        "AutonaPlayerDB.SetupModel)",
        "AutonaAlertNotification()",
        "AutonaUpdater()",
        "AutonaCountryHelper()",
        "AutonaScoreboardManager()",
        "AutonaWebOperations.boardHost = AutonaWebOperations.DesktopBoardHost("
        "AutonaScoreboardManager.instance)",
        "AutonaWebOperations.loadGameByCodename"
        " = AutonaGameAssetManager.instance.LoadGameByCodename",
    ]


# ── The wiring both shells make ─────────────────────────────────────────

# The connections that used to be the window's alone. Each is made by one
# shared function now, so the desktop and the headless shell cannot drift.
_SHARED_WIRING = {
    "wire_tournament_data": [
        "signals.tournament_data_updated.connect(apply)",
        "signals.tournament_url_update.connect(forget_cleared)",
    ],
    "wire_web_server": [
        "game_changed.connect(webserver.ws_game_changed)",
        "AutonaPlayerDB.signals.db_updated.connect(webserver.ws_playerdb)",
    ],
}


def test_the_shared_wiring_steps():
    for name, steps in _SHARED_WIRING.items():
        assert _steps(name) == steps, name


def _calls(path):
    tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    return [ast.unparse(node) for node in ast.walk(tree)
            if isinstance(node, ast.Call)]


def test_both_shells_make_the_shared_wiring_and_only_through_it():
    for shell in ("src/Autona.py", "src/headless.py"):
        calls = _calls(shell)
        for name in _SHARED_WIRING:
            assert sum(call.startswith(f"{name}(") for call in calls) == 1, (
                f"{shell} must call {name}() exactly once")
        # What they connect is theirs only: no shell reaches the payload
        # writer, the forgetting or the web server's pushes on its own (the
        # window's readers of tournamentInfo.* stay its own business).
        for call in calls:
            for target in ("ApplyProviderPayload", "ApplyTournamentData",
                           "ForgetSelection", "ws_program_state",
                           "ws_get_characters", "ws_game_changed",
                           "ws_playerdb"):
                assert target not in call, f"{shell}: {call}"


# ── Real boots in a subprocess ──────────────────────────────────────────


@contextmanager
def _runtime(tmp_path):
    """A throwaway working directory shaped like the app's (see conftest)."""
    from tests.conftest import _LINKED_TREES, _RUNTIME_DIRS, _link_tree

    for relative in _LINKED_TREES:
        link = tmp_path / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        _link_tree(link, PROJECT_ROOT / relative)
    for relative in _RUNTIME_DIRS:
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    try:
        yield tmp_path
    finally:
        # Break the junctions before tmp cleanup can descend through them
        # into the real assets/ and src/ trees (see tests/conftest.py).
        for relative in reversed(_LINKED_TREES):
            link = tmp_path / relative
            try:
                if link.is_symlink():
                    os.unlink(link)
                else:
                    os.rmdir(link)
            except OSError:
                pass


def _probe(tmp_path, script, *args, timeout=180):
    env = dict(os.environ)
    env.update({
        "QT_API": "pyside6",
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    with _runtime(tmp_path) as cwd:
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(PROJECT_ROOT), *args],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    lines = [line for line in result.stdout.splitlines()
             if line.startswith(MARKER)]
    assert result.returncode == 0 and lines, (
        f"probe failed (exit {result.returncode})\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}")
    return json.loads(lines[-1][len(MARKER):])


_PRELUDE = r'''
import json
import sys
import threading

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
MARKER = "<<<AUTONA_BOOT_PROBE>>>"

def report(result):
    sys.stdout.write(MARKER + json.dumps(result) + "\n")
    sys.stdout.flush()

def on_worker(fn):
    box = {}
    thread = threading.Thread(target=lambda: box.update(value=fn()))
    thread.start()
    thread.join()
    return box["value"]
'''


_CORE_SCRIPT = _PRELUDE + r'''
from qtpy.QtCore import QCoreApplication

app = QCoreApplication([])

from src.bootstrap import bootstrap_core
bootstrap_core()

from src import AutonaWebOperations
from src.AutonaAlertNotification import AutonaAlertNotification
from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.AutonaHotkeys import AutonaHotkeys
from src.AutonaPlayerDB import AutonaPlayerDB
from src.AutonaScoreboardManager import AutonaScoreboardManager
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.AutonaUpdater import AutonaUpdater
from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
from src.Helpers.AutonaQtHelper import in_gui_thread
from src.StateManagerBridge import StateManagerBridge
from src.core.Actions import AutonaActions
from src.core.BadWordFilter import BadWordFilter
from src.core.CountryCatalog import CountryCatalog
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.PlayerDB import PlayerDB
from src.core.TournamentInfoService import TournamentInfoService
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

# The filter reads the program locale through the injected callable, at
# censor time: a locale loaded after the bootstrap is the one it sees.
AutonaLocaleHelper.programLocale = "ja"
filter_locale = BadWordFilter.instance._programLocale()

report({
    "app_type": type(QCoreApplication.instance()).__name__,
    "core": {
        "StateManagerBridge": StateManagerBridge.instance is not None,
        "AutonaActions": AutonaActions.instance is not None,
        "BadWordFilter": BadWordFilter.instance is not None,
        "GameAssetCatalog": GameAssetCatalog.instance is not None,
        "PlayerDB": PlayerDB.instance is not None,
        "AutonaTournamentDataProvider":
            AutonaTournamentDataProvider.instance is not None,
        "TournamentInfoService": TournamentInfoService.instance is not None,
        "CountryCatalog": CountryCatalog.instance is not None,
    },
    "bad_words_loaded": bool(BadWordFilter.instance.patterns),
    "filter_locale": filter_locale,
    "filter_country_languages":
        BadWordFilter.instance._countryLanguages("JP"),
    "filter_country_continent":
        BadWordFilter.instance._countryContinent("BR"),
    # The relay is the core stage's: the headless shell's web push needs it.
    "player_db_relay": AutonaPlayerDB._attachedTo is PlayerDB.instance
        and PlayerDB.instance.changed.subscriber_count() == 1,
    "gui": {
        "AutonaHotkeys": AutonaHotkeys.instance is not None,
        "AutonaGameAssetManager": AutonaGameAssetManager.instance is not None,
        "AutonaAlertNotification":
            AutonaAlertNotification.instance is not None,
        "AutonaUpdater": AutonaUpdater.instance is not None,
        "AutonaCountryHelper": AutonaCountryHelper.instance is not None,
        "AutonaScoreboardManager":
            AutonaScoreboardManager.instance is not None,
    },
    "selector_registered":
        AutonaTournamentDataProvider.instance.selectGameFromStartGGId
        is not None,
    "board_host": type(AutonaWebOperations.boardHost).__name__,
    "game_loader": AutonaWebOperations.loadGameByCodename is not None,
    "player_db_model": AutonaPlayerDB.modelEnabled,
    "in_gui_thread_main": in_gui_thread(),
    "in_gui_thread_worker": on_worker(in_gui_thread),
})
'''


def test_bootstrap_core_runs_under_a_bare_qcoreapplication(tmp_path):
    result = _probe(tmp_path, _CORE_SCRIPT)

    assert result["app_type"] == "QCoreApplication"
    assert all(result["core"].values()), result["core"]
    assert not any(result["gui"].values()), result["gui"]
    # Nothing is wired to the (absent) game asset adapter.
    assert result["selector_registered"] is False
    # The web operations' defaults: no board, no game loader (the headless
    # shell registers its own), and no player DB model (QtGui).
    assert result["board_host"] == "BoardHost"
    assert result["game_loader"] is False
    assert result["player_db_model"] is False
    # The core bad-word filter got AutonaLocaleHelper's locale and country
    # tables (ADR-005 S3e-1), and the player DB its db_updated relay.
    assert result["bad_words_loaded"] is True
    assert result["filter_locale"] == "ja"
    assert result["filter_country_languages"] == ["ja"]
    assert result["filter_country_continent"] == "SA"
    assert result["player_db_relay"] is True
    # The QCoreApplication's thread is "the GUI thread" for GuiExecutor's
    # purposes: the headless shell's event loop runs there.
    assert result["in_gui_thread_main"] is True
    assert result["in_gui_thread_worker"] is False


_GUI_SCRIPT = _PRELUDE + r'''
from qtpy.QtWidgets import QApplication

app = QApplication([])

from src.bootstrap import bootstrap_core, bootstrap_gui
bootstrap_core()

from src.core.StateManager import StateManager
# A score subtree left over from the previous run, as LoadState() restores
# it: bootstrap_gui() (AutonaScoreboardManager) must still drop it.
StateManager.Set("score.1.team.1.player.1.name", "yesterday")
StateManager.Set("score.4.team.1.teamName", "stale board")
before = StateManager.Get("score")

bootstrap_gui()
bootstrap_gui()  # idempotent

from src import AutonaWebOperations
from src.AutonaPlayerDB import AutonaPlayerDB
from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.AutonaHotkeys import AutonaHotkeys
from src.AutonaScoreboardManager import AutonaScoreboardManager
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
from src.Helpers.AutonaQtHelper import in_gui_thread

provider = AutonaTournamentDataProvider.instance
adapter = AutonaGameAssetManager.instance

# The provider re-applies its game on every scan (onLoadAssets), through
# the selector the shell registered -- and it does so before the adapter's
# own startup restore, which the adapter connects in UiMounted(), right
# before the first scan. Both slots are the real connections; only what
# they end up calling is recorded.
order = []
provider.selectGameFromStartGGId = (
    lambda videogame: order.append(f"provider:{videogame}"))
restore = adapter._RestoreSelectedGame
def recordRestore():
    order.append("restore")
    restore()
adapter._RestoreSelectedGame = recordRestore
adapter.DownloadStartGGCharacters = lambda: None  # network
adapter.LoadGames = lambda: None  # the scan; the emit below stands in for it
adapter.UiMounted()

class FakeProvider:
    url = "https://www.start.gg/tournament/x/event/y"
    videogame = 1386

provider.provider = FakeProvider()
adapter.signals.onLoadAssets.emit()
provider.provider = None

report({
    "score_before": before is not None,
    "score_after": StateManager.Get("score"),
    "hotkeys": AutonaHotkeys.instance is not None,
    "actions_connected": AutonaHotkeys._actions_connected,
    "country_helper": AutonaCountryHelper.instance is not None,
    "scoreboard": AutonaScoreboardManager.instance is not None,
    "slot_order": order,
    "board_host": type(AutonaWebOperations.boardHost).__name__,
    "board_host_manager": AutonaWebOperations.boardHost.manager
        is AutonaScoreboardManager.instance,
    "game_loader": AutonaWebOperations.loadGameByCodename
        == adapter.LoadGameByCodename,
    "player_db_model": AutonaPlayerDB.modelEnabled,
    "in_gui_thread_main": in_gui_thread(),
    "in_gui_thread_worker": on_worker(in_gui_thread),
})
'''


def test_bootstrap_gui_builds_the_adapters_and_resets_the_score_root(
        tmp_path):
    result = _probe(tmp_path, _GUI_SCRIPT)

    assert result["score_before"] is True
    assert result["score_after"] in (None, {})
    assert result["hotkeys"] and result["actions_connected"]
    assert result["country_helper"] and result["scoreboard"]
    assert result["slot_order"] == ["provider:1386", "restore"]
    assert result["board_host"] == "DesktopBoardHost"
    assert result["board_host_manager"] is True
    assert result["game_loader"] is True
    assert result["player_db_model"] is True
    assert result["in_gui_thread_main"] is True
    assert result["in_gui_thread_worker"] is False


def test_bootstrap_gui_registers_the_adapter_as_the_game_selector(tmp_path):
    script = _PRELUDE + r'''
from qtpy.QtWidgets import QApplication
app = QApplication([])
from src.bootstrap import bootstrap_gui
bootstrap_gui()  # runs bootstrap_core() itself
from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
selector = AutonaTournamentDataProvider.instance.selectGameFromStartGGId
report({
    "is_adapter_method": selector == (
        AutonaGameAssetManager.instance.SetGameFromStartGGId),
})
'''
    assert _probe(tmp_path, script) == {"is_adapter_method": True}


# ── The web server thread ───────────────────────────────────────────────


_WEBSERVER_SCRIPT = _PRELUDE + r'''
import socket
import time
import urllib.request

port = int(sys.argv[2])

from qtpy.QtWidgets import QApplication
app = QApplication([])

from src.bootstrap import bootstrap_core, bootstrap_gui
bootstrap_core()
bootstrap_gui()
from src.Helpers import AutonaQtHelper
AutonaQtHelper.init_gui_executor()

from src.core.SettingsManager import SettingsManager
SettingsManager.Set("general.webserver_port", port)
SettingsManager.Set("general.webserver_listen_lan", False)

from src.AutonaWebServer import WebServer

failures = []
server = WebServer(on_start_failed=failures.append)
server.start()

health = None
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/health", timeout=2) as resp:
            health = (resp.status, json.loads(resp.read())["data"]["status"])
            break
    except Exception:
        time.sleep(0.1)

started = time.monotonic()
stopped = server.stop(10.0)
stop_seconds = time.monotonic() - started

try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close()
    after_stop = "accepted"
except OSError:
    after_stop = "refused"

server.start()  # no restart after stop()
time.sleep(0.3)
try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close()
    after_restart = "accepted"
except OSError:
    after_restart = "refused"

# 192.0.2.1 (TEST-NET-1) is never a local address: the bind fails.
failing = WebServer(on_start_failed=failures.append)
failing.host_name = "192.0.2.1"
failing.start()
deadline = time.monotonic() + 15
while not failures and time.monotonic() < deadline:
    time.sleep(0.05)
failing_thread_gone = failing.stop(5.0)

report({
    "health": health,
    "stopped": stopped,
    "stop_seconds": stop_seconds,
    "after_stop": after_stop,
    "after_restart": after_restart,
    "failures": failures,
    "failing_thread_gone": failing_thread_gone,
    "server_threads": [t.name for t in threading.enumerate()
                       if t.name.startswith("AutonaWebServer")],
})
'''


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_web_server_thread_serves_stops_and_reports_a_failed_start(
        tmp_path):
    result = _probe(tmp_path, _WEBSERVER_SCRIPT, str(_free_port()))

    assert result["health"] == [200, "ok"]
    assert result["stopped"] is True
    # werkzeug's serve_forever polls every 0.5 s; anything near the 10 s
    # grace period means shutdown() did not reach the loop.
    assert result["stop_seconds"] < 5
    assert result["after_stop"] == "refused"
    assert result["after_restart"] == "refused"
    assert len(result["failures"]) == 1
    assert "could not listen on 192.0.2.1" in result["failures"][0]
    assert result["failing_thread_gone"] is True
    assert result["server_threads"] == []
