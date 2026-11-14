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
  and game loader for the web operations, still drops the persisted `score` subtree, and makes the three boards in
  its place, boards 1 / 2 held (ADR-005 S4ac-1);
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
                # A constant (a flag) is shown as written.
                steps.append(
                    f"{_dotted(target)} = "
                    f"{_dotted(statement.value) or ast.unparse(statement.value)}")
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
    # The task executor (ADR-005 S3f-1) depends on nothing and comes before
    # everything that can hand it work.
    assert _steps("bootstrap_core") == [
        "SettingsManager.Initialize()",
        "AutonaLocaleHelper.Initialize()",
        "BadWordFilter()",
        "BadWordFilter.instance.Initialize()",
        "TaskExecutor()",
        "StateManager.Initialize()",
        "StateManagerBridge()",
        "AutonaActions()",
        "GameAssetCatalog()",
        "GameAssetCatalog.instance.ResetGameState()",
        "PlayerDB()",
        "AutonaPlayerDB.Attach()",
        "StartGGDataProvider.Initialize()",
        "AutonaTournamentDataProvider()",
        "Top8Service.fetcher"
        " = AutonaTournamentDataProvider.instance.RunWithProvider",
        # The match list (ADR-005 S4j): its fetch is the provider's jobs, as
        # the Top 8 import's; before anything can ask for one.
        "AnalysisService()",
        # The set selection's lists (ADR-005 S4v), the same way.
        "SetSelectionService()",
        "StatsService.fetcher = AutonaStatsUtil()",
        # The set follow (ADR-005 S3e-7): after the provider and the
        # statistics' stream queue relay; the board host is read when used.
        "SetFollowService.fetcher = AutonaSetFollowUtil()",
        "SetFollowService.boardHost = AutonaWebOperations.current_board_host",
        "TournamentInfoService()",
        # What the match bar's combos offer (ADR-005 S4x): fed by the
        # provider's two payloads, connected ahead of every board's.
        "MatchNameCandidates()",
        "AutonaTournamentDataProvider.instance.signals.tournament_data_updated"
        ".connect(MatchNameCandidates.instance.FollowTournamentData)",
        "AutonaTournamentDataProvider.instance.signals"
        ".tournament_phases_updated.connect("
        "MatchNameCandidates.instance.FollowPhases)",
        # The tournament's icon / banner and its set / clear (ADR-005 S4f):
        # after the executor, the game asset catalog and the provider they
        # drive; before anything can set a tournament (the shells' restore).
        "TournamentMediaService()",
        "TournamentService()",
        # The 配信設定 operations (ADR-005 S4d): after the state layer,
        # AutonaActions, the executor and the provider; started (following
        # the tournament name and game) before the shells' launch seed.
        "StreamService()",
        "StreamService.instance.Start()",
        # The match thumbnail (ADR-005 S4o): after what it reads and the
        # executor; it reads nothing here (it only removes what the last
        # session left of its own files).
        "ThumbnailService()",
        # The tournament result (ADR-005 S4q): the same, and the provider is
        # there for its standings' import.
        "ResultService()",
        "CountryCatalog()",
        # The settings page's settings (ADR-005 S4ae): they take what this
        # run starts with, before either shell reads it (the locale, the web
        # server).
        "AppSettings()",
        # The game asset packs (ADR-005 S-final SF-3b): after the executor
        # and the catalog; the shells set its scan before anything can
        # start a job.
        "AssetPackService()",
        "AutonaDesignerService.MigrateLegacyProjects()",
    ]


def test_bootstrap_gui_steps_and_their_order():
    # bootstrap_core() first. The relative order of the constructions is the
    # one they had inside the old single bootstrap_core(); the provider and
    # PlayerDB wirings follow the adapter they connect to (before its first
    # UiMounted() load), and the hotkeys are routed to
    # AutonaActions right after they exist. The web operations' desktop
    # registrations (the board host, the game loader, and the shell
    # operations -- ADR-005 S3e-9) come after the boards, the shell
    # operations (AutonaShellOperations: the scoreboard dock's until ADR-005
    # S4y) and the adapter they point at, and the game load's roster last,
    # run through the board host registered just above (ADR-005 S3e-6). The
    # dock (AutonaScoreboardManager) was built before the boards until
    # ADR-005 S4ac-3, which removed the Qt SCORE.
    assert _steps("bootstrap_gui") == [
        "bootstrap_core()",
        "AutonaHotkeys()",
        "AutonaHotkeys.ConnectActions()",
        # A save through the settings page re-registers the hotkeys, on the
        # GUI thread (ADR-005 S4ae; connected once, by its own flag); this
        # shell registers them (`applied`).
        "AutonaHotkeys.ConnectReload("
        "StateManagerBridge.instance.hotkeys_changed)",
        "HotkeyRules.applied = True",
        "AutonaGameAssetManager()",
        "AutonaTournamentDataProvider.instance.selectGameFromStartGGId"
        " = AutonaGameAssetManager.instance.SetGameFromStartGGId",
        "AutonaGameAssetManager.instance.signals.onLoadAssets.connect("
        "AutonaTournamentDataProvider.instance.SetGameFromProvider)",
        # The player DB's refresh (the mains normalised, the push) follows a
        # load; its Qt model (EnableModel, before this) went in the Qt model
        # clean-up's PR3.
        "AutonaGameAssetManager.instance.signals.onLoad.connect("
        "AutonaPlayerDB.SetupModel)",
        "AutonaUpdater()",
        # The desktop's boards, made at launch in core and boards 1 / 2 held
        # (ADR-005 S4ac-1; `panel=True` is pinned by the probe below): after
        # bootstrap_core(), before the Window.
        "AutonaActions.instance.MakeBoards()",
        "AutonaShellOperations()",
        # No board host of its own since ADR-005 S4ad (DesktopBoardHost ran
        # the board operations on the GUI thread): the web layer's one.
        "AutonaWebOperations.loadGameByCodename"
        " = AutonaGameAssetManager.instance.LoadGameByCodename",
        # A tournament newly loaded turns the mods off through the same
        # loader (ADR-005 S-final SF-5b-1).
        "AutonaWebOperations.turnModsOff"
        " = AutonaGameAssetManager.instance.TurnModsOff",
        "AutonaWebOperations.openSetSelection"
        " = AutonaShellOperations.instance.OpenSetSelection",
        "AutonaWebOperations.renderThumbnail"
        " = AutonaShellOperations.instance.RenderThumbnail",
        "AutonaWebOperations.startImageRender"
        " = AutonaShellOperations.instance.StartImageRender",
        # An asset pack's install / import re-scans with the adapter's scan
        # (onLoadAssets), on the import's job (ADR-005 S-final SF-3b).
        "AssetPackService.instance.scan"
        " = AutonaGameAssetManager.instance._Scan",
        "wire_game_roster(AutonaGameAssetManager.instance.signals.gameLoaded)",
    ]


# ── The wiring both shells make ─────────────────────────────────────────

# The connections that used to be the window's alone. Each is made by one
# shared function now, so the desktop and the headless shell cannot drift.
_SHARED_WIRING = {
    # Forgetting the selected game on a clear is core's
    # TournamentService.ClearTournament since ADR-005 S4f, for every clear --
    # no longer a slot of tournament_url_update.
    "wire_tournament_data": [
        "signals.tournament_data_updated.connect(apply)",
    ],
    "wire_web_server": [
        "game_changed.connect(webserver.ws_game_changed)",
        # The game list's notice (ADR-005 S-final SF-3a).
        "games_scanned.connect(webserver.on_games_change)",
        "AutonaPlayerDB.signals.db_updated.connect(webserver.ws_playerdb)",
    ],
}


def test_the_shared_wiring_steps():
    for name, steps in _SHARED_WIRING.items():
        assert _steps(name) == steps, name


def test_both_shells_follow_a_game_load_with_the_same_roster():
    """A game load's roster (the game's players per team, the default
    characters per player) is AutonaActions.ApplyGameRoster, run through
    the board host -- with the set length and the commit (in a finally), in
    one save block (ADR-005 S4ad: the commit was the desktop asset adapter's,
    after onLoad, until then). It was each built board's onLoad handler until ADR-005
    S3e-6, so the headless shell's boards never followed a load. One
    function makes the connection: bootstrap_gui() for the desktop (the
    order: test_bootstrap_gui_steps_and_their_order) and the headless shell
    for its loader. The window does not make it again. (The Qt boards'
    onLoad handler only showed the state from S3e-6 and went with them in
    ADR-005 S4ac-3.)"""
    assert _steps("wire_game_roster") == ["game_loaded.connect(apply)"]
    tree = ast.parse((PROJECT_ROOT / "src" / "bootstrap.py").read_text(
        encoding="utf-8"))
    apply = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "apply"
                 and "ApplyGameRoster" in ast.unparse(node))
    assert "AutonaWebOperations.boardHost.Run(run)" in ast.unparse(apply)
    run = next(node for node in ast.walk(apply)
               if isinstance(node, ast.FunctionDef) and node.name == "run")
    # What the load writes, recorded around it, so that the commit publishes
    # that and leaves everyone else's edits behind 反映 (the fix of
    # 2026-11-12; its behaviour: test_scoreboard_operations'
    # test_a_game_load_publishes_only_what_it_wrote).
    recording, = run.body
    assert isinstance(recording, ast.With)
    assert [ast.unparse(item) for item in recording.items] == [
        "StateManager.RecordHeldWrites() as written"]
    block, = recording.body
    assert isinstance(block, ast.With)
    assert ast.unparse(block.items[0]) == "StateManager.SaveBlock()"
    # The commit in a finally: a roster that raises leaves nothing of the
    # load behind 反映 (the ADR-005 S4ad review; its behaviour:
    # test_scoreboard_operations' test_a_game_load_whose_roster_raises_
    # still_commits).
    guarded, = block.body
    assert isinstance(guarded, ast.Try) and not guarded.handlers
    assert [ast.unparse(statement) for statement in guarded.body] == [
        "AutonaActions.instance.ApplyGameRoster(replaced)",
        "AutonaActions.instance.RepublishBestOfs()"]
    assert [ast.unparse(statement) for statement in guarded.finalbody] == [
        "AutonaActions.instance.CommitGameLoad(written)"]
    # Nothing else commits an announced load: not the desktop's asset
    # adapter, whose one commit is the settle of a load it does not announce
    # (_SettleUnannounced: the shutdown came first; a failed Qt model
    # rebuild too, until the Qt model clean-up's PR4), of what that settle's
    # roster wrote.
    assert [call for call in _calls("src/AutonaGameAssetManager.py")
            if "CommitGameLoad" in call] == [
        "actions.CommitGameLoad(written)"]
    adapter = ast.parse((PROJECT_ROOT / "src" / "AutonaGameAssetManager.py")
                        .read_text(encoding="utf-8"))
    settle = next(node for node in ast.walk(adapter)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_SettleUnannounced")
    assert "actions.CommitGameLoad(written)" in ast.unparse(settle)
    for path in ("src/headless.py", "src/Autona.py"):
        assert not [call for call in _calls(path)
                    if "CommitGameLoad" in call], path
    counts = {shell: sum(call.startswith("wire_game_roster(")
                         for call in _calls(shell))
              for shell in ("src/bootstrap.py", "src/Autona.py",
                            "src/headless.py")}
    assert counts == {"src/bootstrap.py": 1, "src/Autona.py": 0,
                      "src/headless.py": 1}


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

def state_subscribers():
    """Who follows the state's publishes, by qualified name."""
    from src.core.StateManager import StateManager
    return {name: sorted(
                getattr(callback, "__qualname__", repr(callback))
                for callback in getattr(StateManager.observers, name)._callbacks)
            for name in ("state_updated", "state_big_change")}
'''


_CORE_SCRIPT = _PRELUDE + r'''
from qtpy.QtCore import QCoreApplication

app = QCoreApplication([])

from src.bootstrap import bootstrap_core
bootstrap_core()

from src import AutonaWebOperations
from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.AutonaHotkeys import AutonaHotkeys
from src.AutonaPlayerDB import AutonaPlayerDB
from src.AutonaShellOperations import AutonaShellOperations
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.AutonaUpdater import AutonaUpdater
from src.Helpers.AutonaQtHelper import in_gui_thread
from src.StateManagerBridge import StateManagerBridge
from src.core.Actions import AutonaActions
from src.core.AnalysisService import AnalysisService
from src.core.AppSettings import AppSettings
from src.core import HotkeyRules
from src.core.BadWordFilter import BadWordFilter
from src.core.CountryCatalog import CountryCatalog
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.PlayerDB import PlayerDB
from src.core.SetSelectionService import SetSelectionService
from src.core.StateManager import StateManager
from src.core.StreamService import StreamService
from src.core.TaskExecutor import TaskExecutor
from src.core.TournamentInfoService import TournamentInfoService
from src.Helpers import AutonaQtHelper
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

# The filter reads the program locale through the injected callable, at
# censor time: a locale loaded after the bootstrap is the one it sees.
AutonaLocaleHelper.programLocale = "ja"
filter_locale = BadWordFilter.instance._programLocale()

# The stream title is core's (ADR-005 S4d): with no window and no panel, a
# tournament name written to the state becomes stream.title once the event
# loop runs the refresh the observer queued (through the GUI executor, made
# before the bootstrap by both shells -- here after it, as the harness does).
AutonaQtHelper.init_gui_executor()
StateManager.Set("tournamentInfo.tournamentName", "Core Cup")
title_before_loop = StateManager.Get("stream.title", "")
app.processEvents()
stream_title = StateManager.Get("stream.title", "")

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
        "TaskExecutor": TaskExecutor.instance is not None,
        "StreamService": StreamService.instance is not None,
        "AnalysisService": AnalysisService.instance is not None,
        "SetSelectionService": SetSelectionService.instance is not None,
        "AppSettings": AppSettings.instance is not None,
    },
    "stream_title": [title_before_loop, stream_title],
    "state_subscribers": state_subscribers(),
    # Made, but no lane thread until the first submit.
    "executor_threads": [t.name for t in threading.enumerate()
                         if t.name.startswith(("AutonaJob", "AutonaFanout"))],
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
        "AutonaUpdater": AutonaUpdater.instance is not None,
        "AutonaShellOperations": AutonaShellOperations.instance is not None,
        # No shell registers hotkeys with the OS here (ADR-005 S4ae).
        "hotkeys_applied": HotkeyRules.applied,
    },
    "selector_registered":
        AutonaTournamentDataProvider.instance.selectGameFromStartGGId
        is not None,
    "board_host": type(AutonaWebOperations.boardHost).__name__,
    "game_loader": AutonaWebOperations.loadGameByCodename is not None,
    "mods_off": AutonaWebOperations.turnModsOff is not None,
    "shell_operations": [AutonaWebOperations.openSetSelection is not None,
                         AutonaWebOperations.renderThumbnail is not None,
                         AutonaWebOperations.startImageRender is not None],
    "in_gui_thread_main": in_gui_thread(),
    "in_gui_thread_worker": on_worker(in_gui_thread),
})
'''


# Everything that follows the state's publishes once the shells are built:
# the Qt adapter (every widget connects to it, never to the channels), and
# the one core service allowed to subscribe (ADR-005 S4d: process-long,
# with Stop(), and it only reads and queues inside the observer -- see
# .claude/rules/state-and-webserver.md). A new subscriber is a decision to
# make there, not here.
STATE_SUBSCRIBERS = {
    "state_updated": ["StateManagerBridge._onStateUpdated",
                      "StreamService._OnStatePublished"],
    "state_big_change": ["StateManagerBridge._onStateBigChange",
                         "StreamService._OnStatePublished"],
}


def test_bootstrap_core_runs_under_a_bare_qcoreapplication(tmp_path):
    result = _probe(tmp_path, _CORE_SCRIPT)

    assert result["app_type"] == "QCoreApplication"
    assert all(result["core"].values()), result["core"]
    assert not any(result["gui"].values()), result["gui"]
    assert result["executor_threads"] == []
    # Nothing is wired to the (absent) game asset adapter.
    assert result["selector_registered"] is False
    # The web operations' defaults: no board, no game loader (the headless
    # shell registers its own), no shell operation (the desktop's alone,
    # ADR-005 S3e-9).
    assert result["board_host"] == "BoardHost"
    assert result["game_loader"] is False
    assert result["mods_off"] is False
    assert result["shell_operations"] == [False, False, False]
    # The core bad-word filter got AutonaLocaleHelper's locale and country
    # tables (ADR-005 S3e-1), and the player DB its db_updated relay.
    assert result["bad_words_loaded"] is True
    assert result["filter_locale"] == "ja"
    assert result["filter_country_languages"] == ["ja"]
    assert result["filter_country_continent"] == "SA"
    assert result["player_db_relay"] is True
    # The stream title follows the tournament with no panel (ADR-005 S4d),
    # written from the event loop, never from inside the publish.
    assert result["stream_title"] == ["", "Core Cup"]
    assert result["state_subscribers"] == STATE_SUBSCRIBERS
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
# it: bootstrap_gui() must still drop it (AutonaActions.MakeBoards, ADR-005
# S4ac-1; the scoreboard dock's constructor until then).
StateManager.Set("score.1.team.1.player.1.name", "yesterday")
StateManager.Set("score.4.team.1.teamName", "stale board")
before = StateManager.Get("score")

bootstrap_gui()
bootstrap_gui()  # idempotent

from src import AutonaWebOperations
from src.AutonaGameAssetManager import AutonaGameAssetManager
from src.AutonaHotkeys import AutonaHotkeys
from src.AutonaShellOperations import AutonaShellOperations
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.Helpers.AutonaQtHelper import in_gui_thread
from src.core import HotkeyRules
from src.core.Actions import AutonaActions

provider = AutonaTournamentDataProvider.instance
adapter = AutonaGameAssetManager.instance

# The boards as the bootstrap left them, before anything below writes.
with StateManager.lock:
    launch_boards = {
        "score_after": StateManager.Get("score"),
        "published": StateManager.lastSavedState.get("score"),
        "held": sorted(StateManager.heldPrefixes),
        "pending": [StateManager.HasPendingChanges(f"score.{n}")
                    for n in (1, 2, 3)],
    }

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
    **launch_boards,
    "hotkeys": AutonaHotkeys.instance is not None,
    "actions_connected": AutonaHotkeys._actions_connected,
    "hotkeys_applied": HotkeyRules.applied,
    "slot_order": order,
    "board_host": type(AutonaWebOperations.boardHost).__name__,
    "game_loader": AutonaWebOperations.loadGameByCodename
        == adapter.LoadGameByCodename,
    "mods_off": AutonaWebOperations.turnModsOff == adapter.TurnModsOff,
    # The desktop's shell operations are AutonaShellOperations' (ADR-005 S4y:
    # the dock's until then). It holds no window since ADR-005 S4ac-3 (the
    # Qt set selection / station windows went with the Qt SCORE).
    "shell_operations": [
        AutonaWebOperations.openSetSelection
        == AutonaShellOperations.instance.OpenSetSelection,
        AutonaWebOperations.renderThumbnail
        == AutonaShellOperations.instance.RenderThumbnail,
        AutonaWebOperations.startImageRender
        == AutonaShellOperations.instance.StartImageRender],
    # Every board is made by the bootstrap (AutonaActions.MakeBoards, ADR-005
    # S4ac-1). None was until then: the window's CreateScoreboard made the
    # one it showed (AutonaActions.EnsureBoard, ADR-005 S4y).
    "boards_ensured": [AutonaActions.instance.BoardEnsured(n)
                       for n in (1, 2, 3)],
    "state_subscribers": state_subscribers(),
    "in_gui_thread_main": in_gui_thread(),
    "in_gui_thread_worker": on_worker(in_gui_thread),
})
'''


def test_bootstrap_gui_builds_the_adapters_and_resets_the_score_root(
        tmp_path):
    result = _probe(tmp_path, _GUI_SCRIPT)

    assert result["score_before"] is True
    # The restored subtree is gone, the stale board with it, and the three
    # boards are made in its place (ADR-005 S4ac-1): published as written,
    # boards 1 / 2 behind their 反映 button, nothing pending.
    score = result["score_after"]
    assert set(score) == {"1", "2", "3"}
    assert "yesterday" not in json.dumps(score)
    assert score["1"]["team"]["1"]["color"] and score["2"]["team"]["1"]["color"]
    assert set(score["3"]["team"]["1"]["player"]) == {
        str(member) for member in range(1, 10)}
    assert result["published"] == score
    assert [p for p in result["held"] if p.startswith("score.")] == [
        "score.1", "score.2"]
    assert result["pending"] == [False, False, False]
    assert result["hotkeys"] and result["actions_connected"]
    assert result["hotkeys_applied"] is True
    assert result["slot_order"] == ["provider:1386", "restore"]
    # The web layer's own board host, as headless (ADR-005 S4ad).
    assert result["board_host"] == "BoardHost"
    assert result["game_loader"] is True
    assert result["mods_off"] is True
    assert result["shell_operations"] == [True, True, True]
    assert result["boards_ensured"] == [True, True, True]
    assert result["state_subscribers"] == STATE_SUBSCRIBERS
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
import urllib.error
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
preview_port = None
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/health", timeout=2) as resp:
            data = json.loads(resp.read())["data"]
            health = (resp.status, data["status"])
            preview_port = data["previewPort"]
            break
    except Exception:
        time.sleep(0.1)

# The preview listener (ADR-005 S-final SF-8a): its own port, what a layout
# reads, and a write refused there even from this PC.
def preview_answer(path, method="GET"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{preview_port}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return [resp.status, None]
    except urllib.error.HTTPError as error:
        return [error.code, json.loads(error.read())["error"]["code"]]

preview_health = preview_answer("/api/v1/health")
preview_write = preview_answer("/api/v1/scoreboards/1/score:reset", "POST")
preview_threads = [t.name for t in threading.enumerate()
                   if t.name == "AutonaWebServerPreview"]

# The delta flusher runs beside the server (ADR-005 S3f-5).
flusher_while_serving = [t.name for t in threading.enumerate()
                         if t.name == "AutonaWebServerDeltas"]

# What a board's pending values did reaches the clients through the Bridge
# (ADR-005 S4u), both channels, from __init__.
from src.StateManagerBridge import StateManagerBridge
notices = []
real_emit = WebServer.ws_emit
WebServer.ws_emit = staticmethod(
    lambda event, *args, **kwargs: notices.append([event, args[0]]))
StateManagerBridge.instance.held_dirty_changed.emit("score.1", False)
StateManagerBridge.instance.held_state_updated.emit("score.2")
# What a board follows (ADR-005 S4v): core's channel, from __init__ too.
from src.core.SetFollowService import SetFollowService
SetFollowService.changes.notify(1)
# The battle format (ADR-005 S4z): core's channel, from __init__ too.
from src.core.Actions import AutonaActions
AutonaActions.battleFormatChanges.notify("team")
# What the app is busy with (ADR-005 S-final SF-2): core's channel, from
# __init__ too; the notice carries the status as it is when sent.
from src.core.StatusService import StatusService
status_token = StatusService.Push("top8_import")
status_snapshot = StatusService.Snapshot()
deadline = time.monotonic() + 10
while len(notices) < 5 and time.monotonic() < deadline:
    time.sleep(0.02)
WebServer.ws_emit = real_emit
StatusService.Pop(status_token)

started = time.monotonic()
stopped = server.stop(10.0)
stop_seconds = time.monotonic() - started

try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close()
    after_stop = "accepted"
except OSError:
    after_stop = "refused"
try:
    socket.create_connection(("127.0.0.1", preview_port), timeout=2).close()
    preview_after_stop = "accepted"
except OSError:
    preview_after_stop = "refused"
preview_port_after_stop = WebServer.PreviewPort()

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
    "preview_port": preview_port,
    "preview_health": preview_health,
    "preview_write": preview_write,
    "preview_threads": preview_threads,
    "preview_after_stop": preview_after_stop,
    "preview_port_after_stop": preview_port_after_stop,
    "flusher_while_serving": flusher_while_serving,
    "notices": notices,
    "status_snapshot": status_snapshot,
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
    port = _free_port()
    result = _probe(tmp_path, _WEBSERVER_SCRIPT, str(port))

    assert result["health"] == [200, "ok"]
    # The preview listener (ADR-005 S-final SF-8a): another port, read-only,
    # stopped with the server (its thread is in server_threads below).
    assert isinstance(result["preview_port"], int)
    assert result["preview_port"] != port
    assert result["preview_health"] == [200, None]
    assert result["preview_write"] == [403, "FORBIDDEN"]
    assert result["preview_threads"] == ["AutonaWebServerPreview"]
    assert result["preview_after_stop"] == "refused"
    assert result["preview_port_after_stop"] is None
    assert result["flusher_while_serving"] == ["AutonaWebServerDeltas"]
    assert result["notices"] == [
        ["scoreboard_pending", {"board": 1, "dirty": False}],
        ["scoreboard_pending", {"board": 2, "dirty": False}],
        ["scoreboard_follow", {"board": 1}],
        ["battle_format", {}],
        ["status", result["status_snapshot"]]]
    assert [job["code"] for job in result["status_snapshot"]["jobs"]] == [
        "top8_import"]
    # stop() joins the delta flusher too: no AutonaWebServer* thread is
    # left below (server_threads).
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
