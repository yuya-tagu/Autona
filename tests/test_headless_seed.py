"""The headless shell's boards are the desktop's freshly built ones (ADR-005 S4t).

A new board's launch write is one core operation, AutonaActions.SeedFreshBoard:
the desktop runs it as it builds a board, ahead of the widgets, and the headless
shell for all three at launch (src/headless._SeedBoards).

What is compared is what each side *published* (the state Socket.IO and
out/program_state.json carry), values and key order both: the order is what
a fresh program_state.json is written in. Each board is compared whole. Until
S4t only the player slots and the crew board were: the board-level and
team-level keys of boards 1 and 2 were written by the board's widgets, so a
headless board had no colour, name, score or set length (ADR-005 S3d, decision
9 in docs/changes/architecture-modernization.md, which S4t resolves).
"""
import copy
import json
from unittest.mock import patch

import pytest

from src import headless
from src.core import StateSchema as schema
from src.core.Actions import AutonaActions
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager


def _published(number):
    with StateManager.lock:
        return copy.deepcopy(
            StateManager.lastSavedState.get("score", {}).get(str(number)))


def _players(board):
    return {team: value.get("player")
            for team, value in (board or {}).get("team", {}).items()}


def _same(left, right):
    return left == right and json.dumps(left) == json.dumps(right)


@pytest.fixture
def gameDefaults(autonaApp):
    """Set game.defaults for one test (the loaded game's defaults, which
    decide how many player cards a freshly built board gets)."""
    previous = StateManager.Get("game.defaults")

    def apply(defaults):
        StateManager.Set("game.defaults", defaults)
        autonaApp.settle()

    yield apply
    if previous is None:
        StateManager.Unset("game.defaults")
    else:
        StateManager.Set("game.defaults", previous)
    autonaApp.settle()


def _desktopThenHeadless(autonaApp):
    autonaApp.dropAllBoards()
    for number in (1, 2, 3):
        autonaApp.board(number)
    desktop = {number: _published(number) for number in (1, 2, 3)}
    # Dropping a board unsets its subtree and its 反映 hold, so what the
    # seed writes below is published at once, as in the headless shell.
    autonaApp.dropAllBoards()
    try:
        with StateManager.SaveBlock():
            headless._SeedBoards()
        autonaApp.settle()
        seeded = {number: _published(number) for number in (1, 2, 3)}
    finally:
        StateManager.Unset("score")
        autonaApp.settle()
    return desktop, seeded


@pytest.mark.parametrize("defaults, players", [
    (None, 1),                         # no game loaded: one card per team
    ({"players_per_team": 2}, 2),      # the game's default
    ({"players_per_team": "2"}, 2),    # a number, as text
    ({"players_per_team": None}, 1),   # a config.json with a null in it
    ({"players_per_team": "two"}, 1),  # ... or something that is no number
])
def test_the_headless_boards_publish_the_desktop_boards(
        autonaApp, gameDefaults, defaults, players):
    if defaults is not None:
        gameDefaults(defaults)

    desktop, seeded = _desktopThenHeadless(autonaApp)

    for number in (1, 2):
        expected = _players(desktop[number])
        # Not vacuous: the desktop board did publish its cards' slots.
        assert set(expected) == {"1", "2"}
        assert all(len(slots) == players for slots in expected.values())
        assert desktop[number]["team"]["1"]["color"]
        assert desktop[number]["best_of"] == 0
    assert set(desktop[3]["team"]["1"]["player"]) == {
        str(member) for member in range(1, 10)}
    for number in (1, 2, 3):
        assert _same(seeded[number], desktop[number]), number


def test_the_headless_seed_leaves_the_polling_setting_alone(autonaApp):
    """docs/operations/runtime-operations.md: on the headless shell the
    setting is the operator's. Building a desktop board turns it off."""
    written = []
    realSet = SettingsManager.Set

    def spy(key, value):
        written.append(key)
        return realSet(key, value)

    autonaApp.dropAllBoards()
    try:
        with patch.object(SettingsManager, "Set", side_effect=spy), \
                StateManager.SaveBlock():
            headless._SeedBoards()
        autonaApp.settle()
    finally:
        StateManager.Unset("score")
        autonaApp.settle()
    assert "general.disable_autoupdate" not in written


@pytest.mark.parametrize("value", [None, "two"])
def test_a_game_with_no_usable_player_count_still_gets_its_cards(
        autonaApp, gameDefaults, value):
    """To SetRoster, None means "keep": a new board has nothing to keep, and
    would have no card at all."""
    gameDefaults({"players_per_team": value})
    autonaApp.dropAllBoards()

    board = autonaApp.board(1)

    assert len(board.team1playerWidgets) == 1
    assert len(board.team2playerWidgets) == 1
    autonaApp.dropAllBoards()


def _settingsWith(values):
    real = SettingsManager.Get

    def get(key, default=None):
        return values[key] if key in values else real(key, default)
    return patch.object(SettingsManager, "Get", side_effect=get)


@pytest.fixture
def noBoards(autonaApp):
    """No board built and none held: what SeedFreshBoard writes is published
    at once, and the score subtree goes after the test."""
    autonaApp.dropAllBoards()
    yield autonaApp
    StateManager.Unset("score")
    autonaApp.settle()


@pytest.fixture
def displayOptions(autonaApp):
    """All eight in the state, as a launch restored them; put back after."""
    previous = copy.deepcopy(StateManager.Get("display_options"))
    restored = dict(schema.DISPLAY_OPTION_DEFAULTS)
    StateManager.Set("display_options", copy.deepcopy(restored))
    autonaApp.settle()
    yield restored
    if previous is None:
        StateManager.Unset("display_options")
    else:
        StateManager.Set("display_options", previous)
    autonaApp.settle()


def test_the_headless_seed_keeps_the_display_options_it_restored(
        noBoards, displayOptions):
    """A state.json and a settings.json that disagree: the headless shell
    fills in what is missing (StateSchema.SeedPanelState), and rewrites nothing.
    Its launches never did, and the toggles it serves write both."""
    against = {f"display_options.{key}": not value
               for key, value in displayOptions.items()}

    with _settingsWith(against), StateManager.SaveBlock():
        headless._SeedBoards()

    assert StateManager.Get("display_options") == displayOptions


def test_a_desktop_board_brings_the_display_options_back_to_the_settings(
        noBoards, displayOptions):
    """The desktop's board build does rewrite them, all eight. The crew board,
    which has no eye menu, does not."""
    against = {f"display_options.{key}": not value
               for key, value in displayOptions.items()}

    with _settingsWith(against):
        noBoards.board(3)
        assert StateManager.Get("display_options") == displayOptions
        noBoards.board(1)

    assert StateManager.Get("display_options") == {
        key: not value for key, value in displayOptions.items()}


@pytest.mark.parametrize("board", [1, 2])
def test_a_new_board_gets_the_default_team_colours(noBoards, board):
    with patch.object(SettingsManager, "Get",
                      side_effect=lambda key, default=None: default):
        assert AutonaActions.instance.SeedFreshBoard(board) is True

    assert StateManager.Get(f"score.{board}.team.1.color") == "#fe3636"
    assert StateManager.Get(f"score.{board}.team.2.color") == "#2e89ff"


def test_a_new_board_gets_the_team_colours_the_settings_name(noBoards):
    with _settingsWith({"general.team_1_default_color": "#111111",
                        "general.team_2_default_color": "#222222"}):
        AutonaActions.instance.SeedFreshBoard(1)

    assert StateManager.Get("score.1.team.1.color") == "#111111"
    assert StateManager.Get("score.1.team.2.color") == "#222222"


@pytest.mark.parametrize("board", [1, 2, 3])
def test_a_new_board_is_one_save_cycle(noBoards, board):
    seen = []
    StateManager.observers.state_updated.subscribe(seen.append)
    try:
        AutonaActions.instance.SeedFreshBoard(board)
    finally:
        StateManager.observers.state_updated.unsubscribe(seen.append)

    assert len(seen) == 1
    assert StateManager.saveBlocked == 0


def test_a_new_board_starts_over_from_what_was_there(noBoards):
    StateManager.Set("score.1.team.1.player.5", {"name": "left over"})
    StateManager.Set("score.1.team.1.score", 3)

    AutonaActions.instance.SeedFreshBoard(1)

    assert list(StateManager.Get("score.1.team.1.player")) == ["1"]
    assert StateManager.Get("score.1.team.1.score") == 0


def test_a_new_crew_board_forgets_the_swap(noBoards):
    """Its flag is score.3.teamsSwapped (ADR-005 S4w), and a new board does
    not keep it: not written by the seed, as on boards 1/2."""
    from src.core.CrewBattleService import CrewBattleService

    AutonaActions.instance.SeedFreshBoard(3)
    CrewBattleService(3).SwapTeams()
    assert CrewBattleService(3).teamsSwapped is True

    AutonaActions.instance.SeedFreshBoard(3)

    assert CrewBattleService(3).teamsSwapped is False
    assert "teamsSwapped" not in StateManager.Get("score.3")


@pytest.mark.parametrize("board", [0, 4, "x", None])
def test_there_is_no_such_new_board(noBoards, board):
    assert AutonaActions.instance.SeedFreshBoard(board) is False
    assert StateManager.Get("score", None) in (None, {})


def test_a_new_board_with_the_tracker_on_has_no_rows_yet(noBoards):
    with _settingsWith({"general.disable_individual_game_tracker": False}):
        AutonaActions.instance.SeedFreshBoard(1)

    assert StateManager.Get("score.1.stages") == {}


def test_the_roster_seed_is_written_after_the_score_reset(autonaApp):
    """_SeedBoards starts from ResetScoreRoot: a seed written before it
    would be dropped with the rest of the restored score."""
    autonaApp.dropAllBoards()
    try:
        StateManager.Set("score.1.team.1.player.5", {"name": "left over"})
        with StateManager.SaveBlock():
            headless._SeedBoards()
        autonaApp.settle()
        slots = _published(1)["team"]["1"]["player"]
    finally:
        StateManager.Unset("score")
        autonaApp.settle()
    assert list(slots) == ["1"]
    assert slots["1"]["character"]


def test_a_roster_takes_an_explicit_count(autonaApp):
    autonaApp.dropAllBoards()
    try:
        with StateManager.SaveBlock():
            StateManager.Set("score.2", {})
            assert AutonaActions.instance.SetRoster(2, 3, 1)
        autonaApp.settle()
        teams = _published(2)["team"]
    finally:
        StateManager.Unset("score")
        autonaApp.settle()
    for team in ("1", "2"):
        assert list(teams[team]["player"]) == ["1", "2", "3"]
        slot = teams[team]["player"]["1"]
        assert list(slot) == ["seed", "birthday", "country", "state",
                              "character", "mains"]
        assert slot["character"] == slot["mains"] == {
            1: {"name": "", "assets": {}, "skin": -1, "variant": {}}}
