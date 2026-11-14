"""Whether a game is selected, and the mods a new tournament turns off, on
both shells (ADR-005 S-final SF-5b-1).

- `game.selected` (state): True from a load, False from a cleared
  tournament (core's TournamentService -> GameAssetCatalog.ForgetSelection),
  while the rest of `game` -- the last game's codename and name -- stays.
  Until SF-5b-1 nothing in the state said so: a client read `codename`,
  which a clear leaves. (The catalog's own half -- the write's place, its
  lock -- is tests/test_game_asset_catalog.py's.)
- A tournament newly loaded asks for the selected game again with its mods
  off, through the loader each shell registers (AutonaWebOperations
  .loadGameByCodename): until SF-5b-1 the Qt window's mods checkbox did it
  on tournament_changed, the desktop only. A clear loads no game. (The
  service's own half -- when it asks -- is tests/test_tournament_service.py's.)

Each check runs on the desktop's asset adapter and on the headless loader
(tests/test_asset_jobs.py's `shell` fixture), with the session's
TournamentService -- the loader as bootstrap_core() hands it -- driving a
stand-in provider adapter (no start.gg).
"""
import pytest

from src.core.SettingsManager import SettingsManager
from tests.conftest import drain_tasks
from tests.test_asset_jobs import shell  # noqa: F401  (fixture)
from tests.test_game_asset_manager_load import goldenGames  # noqa: F401

EVENT_URL = "https://www.start.gg/tournament/autona-cup/event/singles"


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


def _load(shell, codename, mods=False):  # noqa: F811
    shell.Load(codename, mods_active=mods, mods_reload_mode=mods)
    drain_tasks()


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
