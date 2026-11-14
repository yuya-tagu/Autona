"""The Qt adapter's game load, against the pre-S1a recording (ADR-005 S1a).

These tests load the golden fixture games through the load job every load
takes (ADR-005 S3f-4: the GUI thread's synchronous path it had until then
had no production caller left, and is gone) and check what the load
publishes (`game`) and the payloads of the web readers against the
recording. The readers walked the adapter's Qt models (on the GUI thread)
until S1b; they read the catalog now, on whatever thread asks, and the same
recording pins that their output did not move.

The file was test_game_asset_manager_models.py, which also read the
adapter's QStandardItemModels back row by row against the recording's
`models` section and pinned their retirement (agent-pitfalls #13). The
models went in the Qt model clean-up's PR4 (no combo was left to bind to
them); the recording keeps that section, which
tests/test_game_asset_catalog.py still compares the catalog's rows with.

The last block drives the load job (a TaskExecutor job since ADR-005 S3f-4;
a QThread until then) -- the path every pick in the UI takes -- and what
hangs off it: the footer status, the persisted pick and
its restore at startup, and forgetting the pick when the URL is cleared.
"""
import ast
import json
import threading
from pathlib import Path

import pytest

from tests.conftest import copy_trees, drain_tasks, remove_tree

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "game_assets"
GOLDEN = json.loads((FIXTURE / "golden.json").read_text(encoding="utf-8"))
GAMES = [p.name for p in (FIXTURE / "games").iterdir() if p.is_dir()]


@pytest.fixture
def goldenGames(autonaApp, monkeypatch):
    """The fixture games installed into the harness, under the recording's
    locale. Uninstalled afterwards, back to no game loaded."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.SettingsManager import SettingsManager
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

    locale = GOLDEN["locale"]
    monkeypatch.setattr(AutonaLocaleHelper, "programLocale", locale["program"])
    monkeypatch.setattr(AutonaLocaleHelper, "exportLocale", locale["export"])
    monkeypatch.setattr(AutonaLocaleHelper, "remapping", locale["remapping"])

    games = autonaApp.dir / "user_data" / "games"
    copy_trees([(FIXTURE / "games" / name, games / name) for name in GAMES])
    manager = AutonaGameAssetManager.instance
    catalog = GameAssetCatalog.instance
    catalog.ScanGames()
    # A real load ends in onLoad, which persists the pick.
    persisted = SettingsManager.Get("SELECTED_GAME_CODENAME", "")

    yield manager

    drain_tasks()
    for name in GAMES:
        remove_tree(games / name)
    catalog.ScanGames()
    catalog.ResetGameState()
    SettingsManager.Set("SELECTED_GAME_CODENAME", persisted)
    autonaApp.settle()


def _game(autonaApp):
    """The state's `game` as the recording has it: without `selected`,
    which a load says True (ADR-005 S-final SF-5b-1; checked here)."""
    game = dict(autonaApp.get("game"))
    assert game.pop("selected") is True
    return game


def _load(manager, codename, modsActive=False, reload=False):
    # The load job, run to the end with this (the GUI) thread pumped: it
    # marshals the load's announcement onto it.
    manager.LoadGameByCodename(codename, mods_active=modsActive,
                               mods_reload_mode=reload)
    drain_tasks()


SCENARIOS = {
    "tgame": ("tgame", False, False),
    "tgame_mods": ("tgame", True, True),
    "game2": ("game2", False, False),
}


@pytest.mark.parametrize("label", list(SCENARIOS))
def test_a_load_matches_the_recording(goldenGames, autonaApp, label):
    manager = goldenGames
    codename, modsActive, reload = SCENARIOS[label]
    golden = GOLDEN["scenarios"][label]

    _load(manager, codename, modsActive, reload)

    # (The adapter's Qt models were compared with golden["models"] here
    # until the Qt model clean-up's PR4 removed them.)
    assert _game(autonaApp) == golden["state"]

    # The Web readers (catalog-backed since S1b): what they send must not
    # move.
    actions = autonaApp.web()
    for name, actual in (("characters", actions.get_characters()),
                         ("variants", actions.get_variants()),
                         ("games", actions.get_games())):
        assert actual == golden["payloads"][name], name
        assert list(actual) == list(golden["payloads"][name]), name
        # Byte for byte as Socket.IO sends it (SocketioJson = json.dumps,
        # insertion order), nested key order included.
        assert json.dumps(actual) == json.dumps(golden["payloads"][name]), name


# ── the Web readers off the GUI thread (ADR-005 S1b) ────────────────────

# WebServerActions method -> golden payload name.
WEB_READERS = {
    "get_games": "games",
    "get_characters": "characters",
    "get_variants": "variants",
}


def test_the_web_readers_read_the_catalog_without_a_gui_hop():
    """S1's completion condition (docs/changes/architecture-modernization.md
    §4.4): get_games / get_characters / get_variants carry no
    @gui_thread_sync, and read the catalog rather than the Qt models."""
    # Web operations of both shells since ADR-005 S3c (module functions).
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "src" / "AutonaWebOperations.py").read_text(
        encoding="utf-8"))
    methods = {node.name: node for node in tree.body
               if isinstance(node, ast.FunctionDef)}

    for name in WEB_READERS:
        decorators = [ast.unparse(d) for d in methods[name].decorator_list]
        assert not any("gui_thread_sync" in d for d in decorators), \
            f"{name} hops to the GUI thread again: {decorators}"
        body = ast.unparse(methods[name])
        assert "GameAssetCatalog.instance" in body, name
        assert "AutonaGameAssetManager" not in body, name


def test_the_web_readers_answer_on_a_worker_thread(goldenGames, autonaApp):
    """The way the Flask / Socket.IO threads call them -- and with this (the
    GUI) thread NOT spinning the event loop meanwhile. A reader that still
    marshalled onto the GUI thread would wait in run_sync until the join
    below times out."""
    _load(goldenGames, "tgame")
    actions = autonaApp.web()
    results = {}

    def work():
        for name in WEB_READERS:
            results[name] = getattr(actions, name)()

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "a reader waited for the GUI thread"
    payloads = GOLDEN["scenarios"]["tgame"]["payloads"]
    for name, payload in WEB_READERS.items():
        assert json.dumps(results[name]) == json.dumps(payloads[payload]), name


# ── the threaded load ───────────────────────────────────────────────────

LOADER_TIMEOUT_SECS = 30


def _waitForLoader(manager=None):
    """Wait for the load job while pumping this (GUI) thread.

    The job marshals the load's announcement (gameLoaded / onLoad, and the
    close of its save cycle) onto the GUI thread (run_sync), so blocking on
    its Future here would deadlock it. drain_tasks also runs what the GUI
    thread is handed meanwhile (the v1 route's dispatch, a scan's
    onLoadAssets slots)."""
    drain_tasks(LOADER_TIMEOUT_SECS)


@pytest.fixture
def statusCalls(monkeypatch):
    """The footer's calls (core's StatusService, ADR-005 S-final SF-2),
    recorded and passed through, as (kind, token, words): a job's words as
    the desktop footer shows them (AutonaLocaleHelper.StatusText of the
    code and args it was given -- an unknown code shows as itself)."""
    from src.core.StatusService import StatusService
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

    calls = []
    realPush = StatusService.Push
    realUpdate = StatusService.Update
    realPop = StatusService.Pop

    def words(code, args):
        return AutonaLocaleHelper.StatusText({"code": code,
                                              "args": args or {}})

    def push(code, args=None, done=0, total=0):
        token = realPush(code, args, done, total)
        calls.append(("push", token, words(code, args)))
        return token

    def update(token, code, args=None, done=0, total=0):
        calls.append(("update", token, words(code, args)))
        realUpdate(token, code, args, done=done, total=total)

    def pop(token):
        calls.append(("pop", token, None))
        realPop(token)

    monkeypatch.setattr(StatusService, "Push", staticmethod(push))
    monkeypatch.setattr(StatusService, "Update", staticmethod(update))
    monkeypatch.setattr(StatusService, "Pop", staticmethod(pop))
    return calls


@pytest.fixture
def onLoadCount(goldenGames):
    """How many times signals.onLoad fired (from whichever thread)."""
    emitted = []

    def count():
        emitted.append(1)

    goldenGames.signals.onLoad.connect(count)
    yield emitted
    goldenGames.signals.onLoad.disconnect(count)


def _loaderStatus(calls, codename):
    return [token for kind, token, message in calls
            if kind == "push" and f"({codename})" in message]


def test_a_threaded_load_emits_on_load_once_and_clears_its_status(
        goldenGames, autonaApp, statusCalls, onLoadCount):
    from src.core.StatusService import StatusService

    manager = goldenGames
    manager.LoadGameByCodename("tgame", async_mode=True)
    _waitForLoader(manager)
    autonaApp.settle()

    assert len(onLoadCount) == 1
    assert _game(autonaApp) == GOLDEN["scenarios"]["tgame"]["state"]

    tokens = _loaderStatus(statusCalls, "tgame")
    assert len(tokens) == 1
    token = tokens[0]
    assert ("pop", token, None) in statusCalls
    pops = [i for i, call in enumerate(statusCalls) if call[:2] == ("pop", token)]
    updates = [i for i, call in enumerate(statusCalls)
               if call[:2] == ("update", token)]
    # The last progress tick lands before the pop, and the footer is clear.
    assert updates and max(updates) < pops[0]
    assert token not in StatusService._jobs
    # Its words are the load's code with the game (the footer and the web
    # translate them), never a sentence of the loader's own.
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    loading = AutonaLocaleHelper.StatusText(
        {"code": "assets_load", "args": {"codename": "tgame"}})
    said = [words for kind, tok, words in statusCalls
            if tok == token and kind != "pop"]
    assert said and all(words == loading for words in said)


def test_a_skipped_threaded_load_shows_no_status(
        goldenGames, autonaApp, statusCalls, onLoadCount):
    manager = goldenGames
    _load(manager, "tgame")
    statusCalls.clear()
    onLoadCount.clear()

    # Same game, no reload: the catalog skips it.
    manager.LoadGameByCodename("tgame", async_mode=True)
    _waitForLoader(manager)
    autonaApp.settle()

    assert onLoadCount == []
    # Nothing pushed; the loader's unconditional close pops None: a no-op.
    assert [call for call in statusCalls if call[0] != "pop"] == []
    assert all(token is None for _kind, token, _m in statusCalls)


def test_the_last_pick_is_persisted_and_restored(goldenGames, autonaApp):
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.SettingsManager import SettingsManager

    manager = goldenGames
    _load(manager, "game2")
    autonaApp.settle()
    # onLoad -> _PersistSelectedGame.
    assert SettingsManager.Get("SELECTED_GAME_CODENAME", "") == "game2"

    # Next launch: bootstrap clears the game, UiMounted arms the restore and
    # the scan's onLoadAssets runs it.
    GameAssetCatalog.instance.ResetGameState()
    autonaApp.settle()
    assert autonaApp.get("game") == {"selected": False}
    manager._pendingSelectedGameRestore = True
    manager._RestoreSelectedGame()
    _waitForLoader(manager)
    autonaApp.settle()

    assert autonaApp.get("game.codename") == "game2"
    assert manager.selectedGame["codename"] == "game2"
    assert SettingsManager.Get("SELECTED_GAME_CODENAME", "") == "game2"

    # Armed once: a later rescan ("Add game assets") keeps the user's pick.
    generation = manager._loadGeneration
    manager._RestoreSelectedGame()
    assert manager._loadGeneration == generation  # nothing asked for


def test_a_last_pick_that_is_no_longer_installed_is_not_restored(
        goldenGames, autonaApp):
    from src.core.SettingsManager import SettingsManager

    manager = goldenGames
    SettingsManager.Set("SELECTED_GAME_CODENAME", "uninstalled")
    generation = manager._loadGeneration

    manager._pendingSelectedGameRestore = True
    manager._RestoreSelectedGame()

    assert manager._loadGeneration == generation  # nothing asked for
    assert manager._pendingSelectedGameRestore is False
    assert not autonaApp.get("game.codename")


def test_clearing_the_tournament_url_forgets_the_pick(goldenGames):
    from src.core.GameAssetCatalog import GameAssetCatalog

    # Source-level half: the forgetting is core's
    # TournamentService.ClearTournament since ADR-005 S4f, for every clear
    # (it was a slot of tournament_url_update in bootstrap's wiring, which
    # only the web clears emitted; tests/test_tournament_service.py runs it),
    # and the window's Signal_GameChange only resets its game button.
    root = Path(__file__).resolve().parent.parent
    service = ast.parse((root / "src" / "core" / "TournamentService.py")
                        .read_text(encoding="utf-8"))
    clear = next(node for node in ast.walk(service)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "_ApplyClear")
    calls = {ast.unparse(node.func) for node in ast.walk(clear)
             if isinstance(node, ast.Call)}
    assert "GameAssetCatalog.instance.ForgetSelection" in calls, (
        "a cleared tournament no longer forgets the selected game; "
        "picking the same game again after clearing the URL would be skipped")
    bootstrap = (root / "src" / "bootstrap.py").read_text(encoding="utf-8")
    assert "forget_cleared" not in bootstrap, (
        "a second forgetting path is back; the clear is core's alone")
    tree = ast.parse((root / "src" / "Autona.py").read_text(encoding="utf-8"))
    handler = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "Signal_GameChange")
    assert "selectedGame" not in ast.unparse(handler), (
        "the adapter's selectedGame is a read-only view since S1a")

    # Runtime half: what that call does to the adapter.
    manager = goldenGames
    _load(manager, "tgame")
    generation = GameAssetCatalog.instance.Current().generation

    GameAssetCatalog.instance.ForgetSelection()

    assert manager.selectedGame == {}
    # Same assets: the snapshot keeps its generation (the Qt combos kept
    # their rows on it until the Qt model clean-up's PR4).
    assert GameAssetCatalog.instance.Current().generation == generation
    # ...and picking the same game again is a load, not a skip.
    loads = []

    def loaded():
        loads.append(1)

    manager.signals.onLoad.connect(loaded)
    try:
        _load(manager, "tgame")
    finally:
        manager.signals.onLoad.disconnect(loaded)
    assert manager.selectedGame["codename"] == "tgame"
    assert loads == [1]
