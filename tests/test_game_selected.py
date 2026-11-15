"""Whether a game is selected, and the mods a new tournament turns off, on
both shells (ADR-005 S-final SF-5b-1).

- `game.selected` (state): True from a load, False from a cleared
  tournament (core's TournamentService -> GameAssetCatalog.ForgetSelection),
  while the rest of `game` -- the last game's codename and name -- stays.
  Until SF-5b-1 nothing in the state said so: a client read `codename`,
  which a clear leaves. (The catalog's own half -- the write's place, its
  lock -- is tests/test_game_asset_catalog.py's.)
- A tournament newly loaded has the game the loader ends on loaded without
  its mods, through the loader each shell registers (AutonaWebOperations
  .turnModsOff -> its TurnModsOff, GameAssetCatalog.PlanModsOff): until
  SF-5b-1 the Qt window's mods checkbox did it on tournament_changed, the
  desktop only. A clear loads no game. (The service's own half -- when it
  asks -- is tests/test_tournament_service.py's; the plan's table,
  tests/test_game_asset_catalog.py's.)
- (The SF-5b-1 review) the requests around it: the game the tournament's
  data selects, asked for after it, keeps its reload (a newer request for
  the same game keeps the reload it supersedes); a game the operator asked
  for, waiting or running, is neither dropped nor followed by the previous
  game; a load turning the mods on, running, is followed by one turning
  them off.

Each check runs on the desktop's asset adapter and on the headless loader
(tests/test_asset_jobs.py's `shell` fixture), with the session's
TournamentService -- the loader as bootstrap_core() hands it -- driving a
stand-in provider adapter (no start.gg).
"""
import ast
import contextlib
import threading
from pathlib import Path

import pytest

from src.core.SettingsManager import SettingsManager
from src.core.TaskExecutor import TaskExecutor
from tests.conftest import drain_tasks
from tests.test_asset_jobs import (  # noqa: F401  (shell: fixture)
    WAIT, _holdTheLoader, _selections, shell)
from tests.test_game_asset_manager_load import goldenGames  # noqa: F401

EVENT_URL = "https://www.start.gg/tournament/autona-cup/event/singles"
ROOT = Path(__file__).resolve().parent.parent


class _Adapter:
    """The provider adapter, standing in: a load that loads (no start.gg),
    an unload, an announcement."""

    def LoadTournament(self, url, initialLoading=False, force=False):
        return True

    def UnloadTournament(self):
        pass

    def AnnounceUrl(self, url):
        pass

    def ResolveEventUrl(self, url):
        return url


@pytest.fixture
def tournaments(shell, autonaApp, monkeypatch):  # noqa: F811
    """The session's TournamentService over a stand-in adapter, the web
    operations' loader set to this shell's. TOURNAMENT_URL is handed
    back."""
    from src import AutonaWebOperations
    from src.core.TournamentService import TournamentService

    service = TournamentService.instance
    monkeypatch.setattr(service, "_provider", _Adapter())
    monkeypatch.setattr(AutonaWebOperations, "loadGameByCodename",
                        shell.games.LoadGameByCodename)
    monkeypatch.setattr(AutonaWebOperations, "turnModsOff",
                        shell.games.TurnModsOff)
    missing = object()
    stored = SettingsManager.Get("TOURNAMENT_URL", missing)
    try:
        yield service
    finally:
        drain_tasks()
        if stored is missing:
            SettingsManager.Unset("TOURNAMENT_URL")
        else:
            SettingsManager.Set("TOURNAMENT_URL", stored)
        autonaApp.settle()


@contextlib.contextmanager
def _busyLanes():
    """Every job lane of the session's executor busy until the block ends
    (or the yielded `release` is set): a load asked for meanwhile waits for
    the loader, whose job is queued."""
    lanes = threading.Barrier(TaskExecutor.JOB_WORKERS + 1)
    release = threading.Event()

    def busy():
        lanes.wait(WAIT)
        release.wait(WAIT)

    for _ in range(TaskExecutor.JOB_WORKERS):
        TaskExecutor.instance.submit(busy, task_name="busy")
    lanes.wait(WAIT)
    try:
        yield release
    finally:
        release.set()


def _load(shell, codename, mods=False):  # noqa: F811
    shell.Load(codename, mods_active=mods, mods_reload_mode=mods)
    drain_tasks()


def _waiting(shell):  # noqa: F811
    """The request waiting for the loader, as (codename, modsActive,
    reload); None when none waits."""
    waiting = shell.games._pendingLoad
    if waiting is None:
        return None
    return (waiting.codename, waiting.modsActive, waiting.reload)


def test_a_load_selects_the_game_and_a_clear_unselects_it(
        shell, tournaments, autonaApp):  # noqa: F811
    _load(shell, "tgame")
    autonaApp.settle()
    assert autonaApp.get("game.selected") is True

    tournaments.ClearTournament()
    drain_tasks()
    autonaApp.settle()

    assert autonaApp.get("game.selected") is False
    # The rest of `game` stays: why a reader cannot tell from `codename`.
    assert autonaApp.get("game.codename") == "tgame"
    assert shell.catalog.Current().codename is None

    # Picked again: a load, selected again.
    _load(shell, "tgame")
    autonaApp.settle()
    assert autonaApp.get("game.selected") is True


def test_a_new_tournament_reloads_the_game_with_its_mods_off(
        shell, tournaments, autonaApp):  # noqa: F811
    _load(shell, "tgame", mods=True)
    autonaApp.settle()
    assert autonaApp.get("game.mods_active") is True
    generation = shell.games._loadGeneration

    tournaments.SetTournament(EVENT_URL)
    drain_tasks()
    autonaApp.settle()

    # One request, the selected game, mods off -- loaded.
    assert shell.games._loadGeneration == generation + 1
    assert shell.catalog.Current().codename == "tgame"
    assert shell.catalog.Current().modsActive is False
    assert autonaApp.get("game.mods_active") is False
    assert autonaApp.get("game.selected") is True


def test_a_new_tournament_leaves_a_game_without_mods_alone(
        shell, tournaments, autonaApp):  # noqa: F811
    _load(shell, "tgame")
    generation = shell.games._loadGeneration

    tournaments.SetTournament(EVENT_URL)
    drain_tasks()

    assert shell.games._loadGeneration == generation


def test_a_clear_loads_no_game_even_with_its_mods_on(
        shell, tournaments, autonaApp):  # noqa: F811
    """The forgotten game is not loaded again (it would be selected
    again); the mods stay as the state says until a game is picked."""
    _load(shell, "tgame", mods=True)
    generation = shell.games._loadGeneration

    tournaments.ClearTournament()
    drain_tasks()
    autonaApp.settle()

    assert shell.games._loadGeneration == generation
    assert shell.catalog.Current().codename is None
    assert autonaApp.get("game.selected") is False
    assert autonaApp.get("game.mods_active") is True


# ── the requests around it (the SF-5b-1 review) ─────────────────────────


def test_the_tournaments_game_keeps_the_reload_that_turns_the_mods_off(
        shell, tournaments, autonaApp, monkeypatch):  # noqa: F811
    """The job lanes are busy, so the reload without the mods waits for the
    loader; the tournament's data then selects the same game (the
    provider's SetGameFromStartGGId: no reload, no mods), which supersedes
    it. It keeps the reload: until the review it was skipped as the same
    game, and the mods stayed on."""
    _load(shell, "tgame", mods=True)
    selections = _selections(shell.catalog, monkeypatch)
    monkeypatch.setattr(shell.catalog, "GameFromStartGGId",
                        lambda _gameid: "tgame")

    with _busyLanes():
        tournaments.SetTournament(EVENT_URL)
        assert _waiting(shell) == ("tgame", False, True)
        shell.FromStartGG(7)
        assert _waiting(shell) == ("tgame", False, True)
    drain_tasks()
    autonaApp.settle()
    assert selections == [("tgame", False, True)]
    assert shell.catalog.Current().modsActive is False
    assert autonaApp.get("game.mods_active") is False


@pytest.mark.parametrize("mods", [False, True])
def test_a_game_asked_for_and_waiting_is_loaded_without_the_mods(
        shell, tournaments, autonaApp, monkeypatch, mods):  # noqa: F811
    """The operator asked for another game (the GAME menu; or POST /game
    with its mods on), which waits for the loader when the tournament is
    set: it stays the request -- without the mods -- and the previous game
    is not loaded again, before it or after it."""
    _load(shell, "tgame", mods=True)
    selections = _selections(shell.catalog, monkeypatch)
    with _busyLanes():
        shell.Load("game2", mods_active=mods, mods_reload_mode=mods)
        generation = shell.games._loadGeneration

        tournaments.SetTournament(EVENT_URL)
        assert _waiting(shell) == ("game2", False, mods)
        assert shell.games._loadGeneration == generation
    drain_tasks()
    autonaApp.settle()
    assert selections == [("game2", False, mods)]
    assert shell.catalog.Current().codename == "game2"
    assert autonaApp.get("game.mods_active") is False


def test_a_game_being_loaded_is_not_followed_by_the_previous_one(
        shell, tournaments, autonaApp, monkeypatch):  # noqa: F811
    """The operator's game is loading (before it selects) when the
    tournament is set: nothing is asked -- it loads without the mods it was
    asked for without -- and the previous game is not loaded after it."""
    _load(shell, "tgame", mods=True)
    selections = _selections(shell.catalog, monkeypatch)
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    try:
        shell.Load("game2")
        assert entered.wait(WAIT)
        generation = shell.games._loadGeneration

        tournaments.SetTournament(EVENT_URL)
        assert _waiting(shell) is None
        assert shell.games._loadGeneration == generation
    finally:
        release.set()
    drain_tasks()
    autonaApp.settle()
    assert selections == [("game2", False, False)]
    assert shell.catalog.Current().codename == "game2"
    assert autonaApp.get("game.mods_active") is False


def test_a_load_turning_the_mods_on_is_followed_by_one_turning_them_off(
        shell, tournaments, autonaApp, monkeypatch):  # noqa: F811
    """The mods checkbox was ticked -- the game again with its mods, still
    loading -- when the tournament is set: the catalog says they are off
    still, but the game the loader ends on has them on, so it is asked for
    again without them, after."""
    _load(shell, "tgame")
    selections = _selections(shell.catalog, monkeypatch)
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    try:
        shell.Load("tgame", mods_active=True, mods_reload_mode=True)
        assert entered.wait(WAIT)

        tournaments.SetTournament(EVENT_URL)
        assert _waiting(shell) == ("tgame", False, True)
    finally:
        release.set()
    drain_tasks()
    autonaApp.settle()
    assert selections == [("tgame", True, True), ("tgame", False, True)]
    assert shell.catalog.Current().modsActive is False
    assert autonaApp.get("game.mods_active") is False


# ── the window's mods checkbox (gone in SF-5b-3) ────────────────────────
#
# The Qt window's mods checkbox (_ShowModdedContent drawing it from a load,
# _pickGame unchecking it on a pick from the GAME menu, _ToggleMods asking
# for the load on a click) went with the Qt sidebar in ADR-005 S-final
# SF-5b-3: the AppShell's header has the switch (web/src/AppShell/
# Header.tsx, PUT /api/v1/game with the mods; its tests are
# web/src/AppShell/AppShell.test.js's). Read out of src/Autona.py, which
# cannot be imported (agent-pitfalls #1).

def test_the_window_has_no_mods_checkbox_any_more():
    tree = ast.parse((ROOT / "src" / "Autona.py").read_text(
        encoding="utf-8"))
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    for gone in ("_pickGame", "_ToggleMods", "_ShowModdedContent"):
        assert gone not in names, gone
    attributes = {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
    assert "moddedContentCheck" not in attributes
