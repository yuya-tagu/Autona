"""The headless shell's assembly (src/headless.py, ADR-005 S3c).

src/headless.Assemble() run for real in a subprocess -- a bare
QCoreApplication in a throwaway working directory, a game pack installed
there, the web server on a free port -- with the start.gg download (the
network) stubbed out. Pinned:

- the registrations the desktop makes in bootstrap_gui() have headless
  counterparts: the game selector the tournament provider loads its game
  through (S3c-1 review: a missing one means a tournament's game never
  loads, with only a warning in the log), the game loader the web
  operations use, the headless board host; no desktop adapter is built and
  the player DB builds no Qt model (QtGui);
- what the run makes and imports (ADR-005 S3f-6): no GUI application
  object, no widget, and none of the src modules that define a QWidget
  subclass (found by AST) -- in place of "no QtGui / QtWidgets", which
  qtpy rules out (importing it loads both);
- the games follow the desktop's startup: the scan, the provider's game
  re-applied after every scan (selector first), the last pick restored once
  -- loaded through the catalog, with the pick persisted and the web
  server's / player DB's listeners told;
- the shared wiring is live: a tournament payload is written, a cleared URL
  forgets the selected game;
- the shutdown stops the server, stops the task executor (running jobs told
  to stop, queued ones cancelled; ADR-005 S3f-1) and writes
  user_data/state.json.

And in this process: the stop handlers come before the assembly (a stop
asked for during it ends the run once assembled, with no loop), the
shutdown stops the server, then the task executor, then writes, and
removes the console handler last; main.py's
launch checks -- AUTONA_INSTANCE_TAG (read from source only; letters,
digits and hyphens) and Autona.exe refusing --headless before its
single-instance gate (simulated: sys.frozen set, the message box answered).

The real process -- main.py --headless, its single-instance gate and its
stop on Ctrl+Break -- is tests/test_headless_smoke.py.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_bootstrap_split import _PRELUDE, _free_port, _probe

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_ASSEMBLY_SCRIPT = _PRELUDE + r'''
import os
import time
import urllib.request

port = int(sys.argv[2])

# The settings a headless operator would have written, and a game pack
# (the harness's synthetic one, tests/conftest.py loadCharacters).
os.makedirs("user_data/games/probe_game/base_files/icon", exist_ok=True)
with open("user_data/settings.json", "w", encoding="utf-8") as f:
    json.dump({"general": {"webserver_port": port,
                           "webserver_listen_lan": False},
               "SELECTED_GAME_CODENAME": "probe_game"}, f)
with open("user_data/games/probe_game/base_files/config.json", "w",
          encoding="utf-8") as f:
    json.dump({"name": "Probe Game", "smashgg_game_id": 1386,
               "character_to_codename": {"Mario": {"codename": "mario"}}}, f)
with open("user_data/games/probe_game/base_files/icon/config.json", "w",
          encoding="utf-8") as f:
    json.dump({"type": ["icon"], "prefix": "", "postfix": "_"}, f)
from PIL import Image
Image.new("RGBA", (8, 8), (200, 30, 30, 255)).save(
    "user_data/games/probe_game/base_files/icon/mario_0.png")

from qtpy.QtCore import QCoreApplication
app = QCoreApplication([])

from src.core.GameAssetCatalog import GameAssetCatalog
GameAssetCatalog.DownloadStartGGCharacters = lambda self: None  # network

from src import headless
shell = headless.Assemble(app)

from src import AutonaWebOperations as WebOps
from src.AutonaPlayerDB import AutonaPlayerDB
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from src.core.StatePersistence import StatePersistence

provider = AutonaTournamentDataProvider.instance
games = shell.games
catalog = GameAssetCatalog.instance
events = []
games.signals.gameChanged.connect(lambda: events.append("gameChanged"))
AutonaPlayerDB.signals.db_updated.connect(lambda: events.append("db_updated"))

def built(module, name):
    # Looked up, not imported: the modules the run imports are pinned
    # (below), so the probe imports nothing the shell does not.
    cls = getattr(sys.modules.get(module), name, None)
    return cls is not None and cls.instance is not None

def pump(done, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if done():
            return True
        time.sleep(0.02)
    return False

result = {
    "app_type": type(QCoreApplication.instance()).__name__,
    "selector": provider.selectGameFromStartGGId
        == games.SelectGameFromStartGGId,
    "loader": WebOps.loadGameByCodename == games.LoadGameByCodename,
    "board_host": type(WebOps.boardHost).__name__,
    # The shell operations (ADR-005 S3e-9): the desktop's alone.
    "shell_operations": [WebOps.openSetSelection is not None,
                         WebOps.renderThumbnail is not None],
    "desktop": [name for module, name in (
        ("src.AutonaGameAssetManager", "AutonaGameAssetManager"),
        ("src.AutonaScoreboardManager", "AutonaScoreboardManager"),
        ("src.AutonaHotkeys", "AutonaHotkeys"),
        ("src.Helpers.AutonaCountryHelper", "AutonaCountryHelper"))
        if built(module, name)],
    "seeded": {key: isinstance(StateManager.Get(key), dict)
               for key in ("score.1", "score.2", "score.3",
                           "tournamentInfo", "commentary")},
}

# The startup scan, then the restore of the last pick: a real load.
result["restored"] = pump(
    lambda: StateManager.Get("game.codename") == "probe_game"
    and "db_updated" in events)
result["persisted"] = SettingsManager.Get("SELECTED_GAME_CODENAME")
result["events"] = events[:]
result["player_db_model"] = AutonaPlayerDB.model is not None

# Served by the thread.
with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/v1/health", timeout=5) as response:
    result["health"] = response.status

# Every later scan re-applies the provider's game through the selector;
# the last pick is restored on the first scan only.
order = []
games.LoadGameByCodename = (
    lambda codename, **kwargs: order.append(codename))
class FakeProvider:
    url = "https://www.start.gg/tournament/x/event/y"
    videogame = 1386
provider.provider = FakeProvider()
rescan = threading.Thread(target=games._Scan)
rescan.start()
rescan.join()
pump(lambda: order)
pump(lambda: False, timeout=0.3)
result["rescan_order"] = order[:]
provider.provider = None

# The shared wiring: the payload writer and the forgetting.
provider.signals.tournament_data_updated.emit({"tournamentName": "Probe Cup"})
result["tournament_name"] = StateManager.Get("tournamentInfo.tournamentName")
result["selected_before_clear"] = catalog.Current().codename
provider.signals.tournament_url_update.emit("")
result["selected_after_clear"] = catalog.Current().codename

# Jobs queued behind a full job lane when the shell stops (ADR-005 S3f-1):
# the running ones are told to stop, the queued ones never run.
from src.core.TaskExecutor import TaskExecutor
executor = TaskExecutor.instance
started = threading.Semaphore(0)
def blocking():
    started.release()
    executor.stopping.wait(30)
    return "stopped"
ran = []
running = [executor.submit(blocking, task_name=f"probe-running-{i}")
           for i in range(TaskExecutor.JOB_WORKERS)]
# Every lane thread busy before the next submits, so those are queued.
lane_full = all([started.acquire(timeout=10) for _ in running])
queued = [executor.submit(lambda: ran.append("queued"),
                          task_name=f"probe-{i}")
          for i in range(2)]

shell.Shutdown()
result["stopped"] = shell.stopped.is_set()
result["state_json"] = os.path.isfile(StatePersistence.Path() or "")
result["executor"] = {
    "lane_full": lane_full,
    "stopping": executor.stopping.is_set(),
    "running": [f.result(5) for f in running],
    "queued_ran": ran,
    "queued_cancelled": sum(f.cancelled() for f in queued),
    "after": executor.submit(lambda: None, task_name="late").cancelled(),
}

# What the whole run made and imported (ADR-005 S3f-6). qtpy loads
# PySide6.QtGui and PySide6.QtWidgets whatever the shell does, so those
# are looked up, not asked for.
import gc
qtgui = sys.modules.get("PySide6.QtGui")
qtwidgets = sys.modules.get("PySide6.QtWidgets")
result["qt"] = {
    "gui_application": qtgui is not None and isinstance(
        QCoreApplication.instance(), qtgui.QGuiApplication),
    "widgets": 0 if qtwidgets is None else sum(
        isinstance(o, qtwidgets.QWidget) for o in gc.get_objects()),
}
result["src_modules"] = sorted(
    name for name in sys.modules if name == "src" or name.startswith("src."))
report(result)
'''


def test_the_headless_shell_assembles_and_runs(tmp_path):
    result = _probe(tmp_path, _ASSEMBLY_SCRIPT, str(_free_port()))

    assert result["app_type"] == "QCoreApplication"
    # The registrations (S3c-1 review: the selector's must not go missing).
    assert result["selector"] is True
    assert result["loader"] is True
    assert result["board_host"] == "BoardHost"
    assert result["shell_operations"] == [False, False]
    assert result["desktop"] == []
    assert all(result["seeded"].values()), result["seeded"]

    # The games: scanned, the last pick restored and loaded via the catalog.
    assert result["restored"] is True, result
    assert result["persisted"] == "probe_game"
    assert "gameChanged" in result["events"]
    assert "db_updated" in result["events"]
    assert result["player_db_model"] is False

    assert result["health"] == 200
    # A rescan re-applies the provider's game, and only that.
    assert result["rescan_order"] == ["probe_game"]

    assert result["tournament_name"] == "Probe Cup"
    assert result["selected_before_clear"] == "probe_game"
    assert result["selected_after_clear"] is None

    assert result["stopped"] is True
    assert result["state_json"] is True
    # The shutdown stopped the task executor: the running jobs were told to
    # stop and finished, the queued ones were cancelled, and a later submit
    # is cancelled at once (ADR-005 S3f-1).
    executor = result["executor"]
    assert executor["lane_full"] is True
    assert executor["stopping"] is True
    assert executor["running"] == ["stopped"] * len(executor["running"])
    assert executor["queued_ran"] == []
    assert executor["queued_cancelled"] == 2
    assert executor["after"] is True

    # No GUI application object, no widget, no module of the desktop's
    # widgets (ADR-005 S3f-6).
    assert result["qt"] == {"gui_application": False, "widgets": 0}
    imported = set(result["src_modules"])
    assert "src.headless" in imported and "src.AutonaWebServer" in imported
    widgetModules = _widget_modules()
    assert imported.isdisjoint(widgetModules), sorted(
        imported & widgetModules)


def _widget_modules():
    """The src modules that define a QWidget subclass, by AST: a class
    whose base is one of PySide6.QtWidgets' widget classes, or (closed
    over) a src class that is one. A base is matched by its last name
    (`QDialog`, `QtWidgets.QDialog`, `AutonaBoardWidget`)."""
    from PySide6 import QtWidgets
    widgets = {name for name in dir(QtWidgets)
               if isinstance(getattr(QtWidgets, name), type)
               and issubclass(getattr(QtWidgets, name), QtWidgets.QWidget)}
    classes = []
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        parts = list(path.relative_to(PROJECT_ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {base.id if isinstance(base, ast.Name) else base.attr
                         for base in node.bases
                         if isinstance(base, (ast.Name, ast.Attribute))}
                classes.append((".".join(parts), node.name, bases))
    found = set()
    while True:
        more = {(module, name) for module, name, bases in classes
                if bases & widgets and (module, name) not in found}
        if not more:
            break
        found |= more
        widgets |= {name for _, name in more}
    modules = {module for module, _ in found}
    # The derivation finds what is known to be a widget's module.
    assert {"src.Autona", "src.AutonaScoreboardWidget",
            "src.AutonaScoreboardPlayerWidget", "src.AutonaSelectSetWindow",
            "src.AutonaCrewBattleWidget", "src.ui_layout"} <= modules, modules
    return modules


def _assemble_steps():
    """The calls and registrations of headless.Assemble, in source order."""
    tree = ast.parse((PROJECT_ROOT / "src" / "headless.py").read_text(
        encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "Assemble")
    steps = []
    for statement in function.body:
        if isinstance(statement, ast.Assign):
            steps.append(ast.unparse(statement.targets[0]) + " =")
        elif (isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)):
            steps.append(ast.unparse(statement.value.func) + "()")
    return steps


def test_the_registrations_come_before_anything_can_use_them():
    # The provider loads a tournament from UiMounted() (its game through the
    # selector), the scan's re-apply goes through the selector too, and the
    # web server takes requests once started: each registration must be in
    # place first.
    steps = _assemble_steps()
    for registration in ("provider.selectGameFromStartGGId =",
                         "AutonaWebOperations.loadGameByCodename =",
                         "wire_tournament_data()"):
        for user in ("provider.UiMounted()", "games.Start()",
                     "webserver.start()"):
            assert steps.index(registration) < steps.index(user), (
                registration, user, steps)
    assert steps.index("wire_web_server()") < steps.index("webserver.start()")


# ── stopping ─────────────────────────────────────────────────────────────

class _FakeMeta:
    """headless.QMetaObject, recording the queued quit."""
    calls = []

    @staticmethod
    def invokeMethod(target, name, connection):
        _FakeMeta.calls.append((target, name, connection))


def test_a_stop_request_quits_the_loop_queued_and_is_kept(monkeypatch):
    from qtpy.QtCore import Qt
    from src import headless
    monkeypatch.setattr(_FakeMeta, "calls", [])
    monkeypatch.setattr(headless, "QMetaObject", _FakeMeta)
    app = object()
    shell = headless.HeadlessShell(app)

    shell.RequestStop()

    # Queued (a direct quit() before the loop runs is dropped), and kept
    # for run() in case the loop is not running yet.
    assert _FakeMeta.calls == [
        (app, "quit", Qt.ConnectionType.QueuedConnection)]
    assert shell.stopRequested.is_set()


def _fake_run(monkeypatch, stop_while_assembling):
    from src import headless
    events = []
    monkeypatch.setattr(_FakeMeta, "calls", [])
    monkeypatch.setattr(headless, "QMetaObject", _FakeMeta)
    monkeypatch.setattr(headless, "_ConfigureLogging", lambda: None)
    monkeypatch.setattr(headless, "_InstallStopHandlers",
                        lambda shell: events.append("handlers"))

    def assemble(app, shell):
        events.append("assemble")
        if stop_while_assembling:
            shell.RequestStop()   # Ctrl+Break on a slow start
        return shell

    monkeypatch.setattr(headless, "Assemble", assemble)
    monkeypatch.setattr(headless.HeadlessShell, "Shutdown",
                        lambda self: events.append("shutdown"))

    class App:
        def exec(self):
            events.append("exec")
            return 7

    return headless.run(App()), events


def test_the_stop_handlers_come_before_the_assembly(monkeypatch):
    code, events = _fake_run(monkeypatch, stop_while_assembling=False)

    assert events == ["handlers", "assemble", "exec", "shutdown"]
    assert code == 7


def test_a_stop_asked_for_while_assembling_ends_without_a_loop(monkeypatch):
    code, events = _fake_run(monkeypatch, stop_while_assembling=True)

    # Through the same shutdown, once assembled: no loop is started.
    assert events == ["handlers", "assemble", "shutdown"]
    assert code == 0


class _FakeExecutor:
    """TaskExecutor.instance, recording its shutdown (the session's real
    one must stay usable for the tests after this one)."""

    def __init__(self, order, finished=True, raises=False):
        self.order = order
        self.finished = finished
        self.raises = raises

    def shutdown(self, timeout):
        self.order.append(("tasks", timeout))
        if self.raises:
            raise RuntimeError("probe")
        return self.finished


def _record_the_flush(monkeypatch, order):
    from src.core.StateManager import StateManager
    from src.core.StatePersistence import StatePersistence
    monkeypatch.setattr(StateManager, "JoinExports", staticmethod(
        lambda timeout=None: order.append("out") or True))
    monkeypatch.setattr(StatePersistence, "Flush", staticmethod(
        lambda timeout=None: order.append("state.json") or True))


def test_the_shutdown_writes_first_and_removes_the_console_handler_last(
        monkeypatch):
    from src import headless
    from src.core.TaskExecutor import TaskExecutor
    order = []
    _record_the_flush(monkeypatch, order)
    monkeypatch.setattr(TaskExecutor, "instance", _FakeExecutor(order))

    class Server:
        def stop(self, timeout):
            order.append("server")

    shell = headless.HeadlessShell(app=None, webserver=Server())
    shell._removeConsoleHandler = (
        lambda: order.append(("handler removed", shell.stopped.is_set())))

    shell.Shutdown()

    # The background tasks after the server (no request can queue one any
    # more) and before the flush (ADR-005 S3f-1), with a short wait: a
    # console close leaves about five seconds in all.
    assert order == ["server", ("tasks", 1.0), "out", "state.json",
                     ("handler removed", False)]
    assert shell.stopped.is_set()
    assert shell._removeConsoleHandler is None


def test_a_shutdown_before_the_web_server_was_made_still_writes(monkeypatch):
    # An assembly that failed early, or a stop before the server exists.
    from src import headless
    from src.core.TaskExecutor import TaskExecutor
    order = []
    _record_the_flush(monkeypatch, order)
    monkeypatch.setattr(TaskExecutor, "instance", _FakeExecutor(order))

    shell = headless.HeadlessShell(app=None)
    shell.Shutdown()

    assert order == [("tasks", 1.0), "out", "state.json"]
    assert shell.stopped.is_set()


@pytest.mark.parametrize("executor", [
    "none", "still running", "raises"])
def test_the_flush_runs_whatever_the_task_executor_does(monkeypatch, executor):
    # Not built (a bootstrap that failed before it), tasks that did not stop
    # in time, or an error: ./out and state.json are written all the same.
    from src import headless
    from src.core.TaskExecutor import TaskExecutor
    order = []
    _record_the_flush(monkeypatch, order)
    monkeypatch.setattr(TaskExecutor, "instance", {
        "none": None,
        "still running": _FakeExecutor(order, finished=False),
        "raises": _FakeExecutor(order, raises=True),
    }[executor])

    shell = headless.HeadlessShell(app=None)
    shell.Shutdown()

    assert order[-2:] == ["out", "state.json"]
    assert shell.stopped.is_set()


# ── the launch checks (main.py, before its single-instance gate) ─────────

@pytest.mark.parametrize("value", [
    "pytest-0123456789abcdef", "A", "a-b-C-9"])
def test_an_instance_tag_of_letters_digits_and_hyphens_is_used(value):
    from src import headless

    assert headless.instance_tag(
        {"AUTONA_INSTANCE_TAG": value}, frozen=False) == value


@pytest.mark.parametrize("value", [
    "two words", "a/b", "a\\b", "a.b", "a_b", "tag\n", "タグ", "a:b"])
def test_any_other_instance_tag_is_ignored_with_a_warning(value):
    from loguru import logger
    from src import headless
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)),
                      level="WARNING")
    try:
        tag = headless.instance_tag(
            {"AUTONA_INSTANCE_TAG": value}, frozen=False)
    finally:
        logger.remove(sink)

    assert tag == ""
    assert any("AUTONA_INSTANCE_TAG ignored" in message
               for message in messages)


def test_no_instance_tag_is_none():
    from src import headless

    assert headless.instance_tag({}, frozen=False) == ""
    assert headless.instance_tag({"AUTONA_INSTANCE_TAG": ""},
                                 frozen=False) == ""


def test_autona_exe_never_reads_the_instance_tag():
    from src import headless

    class Unreadable(dict):
        def get(self, *args):
            raise AssertionError("read in a frozen build")

        def __getitem__(self, key):
            raise AssertionError("read in a frozen build")

    assert headless.instance_tag(
        Unreadable(AUTONA_INSTANCE_TAG="pytest-x"), frozen=True) == ""


def test_the_frozen_refusal_says_to_run_from_source():
    from src import headless
    shown = []

    headless.refuse_frozen_headless(
        message_box=lambda *args: shown.append(args))

    (owner, text, title, flags), = shown
    assert owner is None
    assert title == "Autona"
    assert "Autona.exe" in text
    assert "python main.py --headless" in text


_FROZEN_LAUNCH_SCRIPT = r'''
import ctypes
import os
import runpy
import sys

root = sys.argv[1]
main = os.path.join(root, "main.py")
sys.path.insert(0, root)

# The message box Autona.exe would show, answered here.
def message_box(owner, text, title, flags):
    sys.stdout.write("MESSAGE_BOX " + title + " | "
                     + text.replace("\n", " ") + "\n")
    sys.stdout.flush()
    return 1
ctypes.windll.user32.MessageBoxW = message_box

# The single-instance gate (main.py _acquire_single_instance) takes its mutex
# through ctypes.WinDLL: reaching it is the failure, and must not go on to
# start an app. Only that caller -- loguru's dependencies load kernel32 too.
real_windll = ctypes.WinDLL
def guarded(name, *args, **kwargs):
    if sys._getframe(1).f_code.co_name == "_acquire_single_instance":
        sys.stdout.write("GATE_REACHED\n")
        sys.stdout.flush()
        os._exit(3)
    return real_windll(name, *args, **kwargs)
ctypes.WinDLL = guarded

sys.frozen = True   # what PyInstaller sets in Autona.exe
sys.argv = [main, "--headless"]
runpy.run_path(main, run_name="__main__")
'''


@pytest.mark.skipif(sys.platform != "win32",
                    reason="the refusal is a Windows message box")
def test_autona_exe_refuses_headless_before_the_gate(tmp_path):
    env = dict(os.environ)
    env.update({
        # The frozen update gate looks for the helper's lock in %TEMP%.
        "TEMP": str(tmp_path), "TMP": str(tmp_path),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    env.pop("AUTONA_INSTANCE_TAG", None)

    result = subprocess.run(
        [sys.executable, "-B", "-c", _FROZEN_LAUNCH_SCRIPT,
         str(PROJECT_ROOT)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "GATE_REACHED" not in result.stdout
    assert ("MESSAGE_BOX Autona | Autona.exe does not run headless"
            in result.stdout), result.stdout
    assert "python main.py --headless" in result.stdout
    # Nothing else started: no log, no state, no output files.
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "user_data").exists()
    assert not (tmp_path / "out").exists()
