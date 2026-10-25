"""The headless shell's boards are the desktop's freshly built ones (ADR-005 S3d).

The desktop builds a board's widgets and they publish its player slots: the
empty player cards of a 1v1 / team board, the crew board's launch write. The
headless shell builds no widget, so src/headless.py seeds the same thing
from core (PlayerService.SeedEmptyRoster / CrewBattleService.SeedDefaults).
Without it the score overlays drew nothing in the headless shell (they wait
for team 1's player slot) and the Web UI had no player subtree to show.

What is compared is what each side *published* (the state Socket.IO and
out/program_state.json carry), values and key order both: the order is what
a fresh program_state.json is written in. For boards 1 and 2 that is the
player subtree of each team -- the board-level keys (match bar, best of,
stages) and the team-level ones (colour, name, LOSERS, score) are the
board widget's and are not seeded. So the key order matches inside each
player subtree only: at the team level the headless board has `player`
first (and the other keys after it, as they are written), the desktop's
has it after colour / teamName / losers. Accepted with that scope (ADR-005
S3d, decision 9 in docs/changes/architecture-modernization.md). The crew
board is compared whole: its launch write is one core call on both sides.
"""
import copy
import json

import pytest

from src import headless
from src.core.PlayerService import SeedEmptyRoster
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
])
def test_the_headless_boards_publish_the_desktop_boards_player_slots(
        autonaApp, gameDefaults, defaults, players):
    if defaults is not None:
        gameDefaults(defaults)

    desktop, seeded = _desktopThenHeadless(autonaApp)

    for number in (1, 2):
        expected = _players(desktop[number])
        # Not vacuous: the desktop board did publish its cards' slots.
        assert set(expected) == {"1", "2"}
        assert all(len(slots) == players for slots in expected.values())
        assert _same(_players(seeded[number]), expected), number
    assert set(desktop[3]["team"]["1"]["player"]) == {
        str(member) for member in range(1, 10)}
    assert _same(seeded[3], desktop[3])


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


def test_the_roster_seed_takes_an_explicit_count(autonaApp):
    autonaApp.dropAllBoards()
    try:
        with StateManager.SaveBlock():
            assert SeedEmptyRoster(2, players=3, characters=1) == 3
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
