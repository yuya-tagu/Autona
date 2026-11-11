"""The headless shell's boards are the desktop's freshly built ones (ADR-005 S4t).

A new board's launch write is one core operation, AutonaActions.SeedFreshBoard.
Since ADR-005 S4y both shells run it through AutonaActions.EnsureBoard, which
makes a board once and -- the desktop's, `panel` -- also rewrites the display
options, turns polling off and puts a 1v1 / team board behind its 反映 button
(pinned at the end). Since S4ac-1 both make all three at launch through
AutonaActions.MakeBoards: the desktop with `panel` (bootstrap_gui()), the
headless shell without (src/headless._SeedBoards). The desktop made a board
only as it built its widgets until then (the widgets went in S4ac-3).

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
from tests.fixtures.write_recording import WriteRecorder


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
    # The desktop's launch (bootstrap_gui(), ADR-005 S4ac-1). The window
    # built each board's widgets on what it made, writing nothing, until
    # S4ac-3 removed them.
    AutonaActions.instance.MakeBoards(panel=True)
    autonaApp.settle()
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
    would have no player slot at all (the Qt board then had no card)."""
    gameDefaults({"players_per_team": value})
    autonaApp.dropAllBoards()

    autonaApp.ready(1)

    for team in (1, 2):
        assert list(StateManager.Get(f"score.1.team.{team}.player")) == ["1"]
        assert AutonaActions.RosterCount(1, team) == 1
    autonaApp.dropAllBoards()


def _settingsWith(values):
    real = SettingsManager.Get

    def get(key, default=None):
        return values[key] if key in values else real(key, default)
    return patch.object(SettingsManager, "Get", side_effect=get)


@pytest.fixture
def noBoards(autonaApp):
    """No board made and none held: what SeedFreshBoard writes is published
    at once, and the score subtree goes after the test -- the boards the test
    made first (core's flag, their holds), so no subtree is dropped from
    under a board still on it."""
    autonaApp.dropAllBoards()
    yield autonaApp
    autonaApp.dropAllBoards()
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
    """The desktop's making of a board (EnsureBoard with `panel`) does
    rewrite them, all eight. The crew board, which has no eye menu, does
    not."""
    against = {f"display_options.{key}": not value
               for key, value in displayOptions.items()}

    with _settingsWith(against):
        noBoards.ready(3)
        assert StateManager.Get("display_options") == displayOptions
        noBoards.ready(1)

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


# ── making a board: AutonaActions.EnsureBoard (ADR-005 S4y) ──────────────

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _held(board):
    with StateManager.lock:
        return f"score.{board}" in StateManager.heldPrefixes


@pytest.mark.parametrize("board", [1, 2, 3])
def test_a_board_is_made_once(noBoards, board):
    actions = AutonaActions.instance
    assert actions.BoardEnsured(board) is False

    assert actions.EnsureBoard(board) is True
    assert actions.BoardEnsured(board) is True
    StateManager.Set(f"score.{board}.team.1.teamName", "kept")

    assert actions.EnsureBoard(board) is False
    assert actions.EnsureBoard(board, panel=True) is False
    assert StateManager.Get(f"score.{board}.team.1.teamName") == "kept"
    assert not _held(board)


@pytest.mark.parametrize("board", [1, 2])
def test_the_desktops_board_is_held_after_its_seed(noBoards, board):
    """`panel`: one save cycle that publishes the seed (the hold comes last,
    or it would hold the seed back) and leaves the board behind 反映."""
    seen = []
    StateManager.observers.state_updated.subscribe(seen.append)
    try:
        assert AutonaActions.instance.EnsureBoard(board, panel=True) is True
    finally:
        StateManager.observers.state_updated.unsubscribe(seen.append)

    assert len(seen) == 1
    assert _held(board)
    assert _published(board)["team"]["1"]["color"]
    assert StateManager.HasPendingChanges(f"score.{board}") is False
    assert SettingsManager.Get("general.disable_autoupdate") is True


def test_the_desktops_board_turns_polling_off_and_the_headless_does_not(
        noBoards):
    written = []
    realSet = SettingsManager.Set

    def spy(key, value):
        written.append((key, value))
        return realSet(key, value)

    with patch.object(SettingsManager, "Set", side_effect=spy):
        AutonaActions.instance.EnsureBoard(1)
        AutonaActions.instance.EnsureBoard(3, panel=True)
        assert written == []
        AutonaActions.instance.EnsureBoard(2, panel=True)
    assert written == [("general.disable_autoupdate", True)]
    # The crew board is never held, with or without `panel`.
    assert not _held(3) and not _held(1) and _held(2)


@pytest.mark.parametrize("board", [0, 4, "x", None])
def test_there_is_no_such_board_to_make(noBoards, board):
    assert AutonaActions.instance.EnsureBoard(board, panel=True) is False
    assert AutonaActions.instance.BoardEnsured(board) is False
    assert StateManager.Get("score", None) in (None, {})


def test_a_board_that_failed_to_be_made_is_not_made(noBoards):
    actions = AutonaActions.instance
    with patch.object(actions, "SeedFreshBoard",
                      side_effect=RuntimeError("no seed")):
        with pytest.raises(RuntimeError):
            actions.EnsureBoard(1, panel=True)
    assert actions.BoardEnsured(1) is False
    assert not _held(1)
    assert StateManager.saveBlocked == 0

    assert actions.EnsureBoard(1) is True


def _failingAfterTheHold(where):
    """A patch that has EnsureBoard raise once the 反映 hold is in: in the
    registration itself, or in the publish that closes its save cycle."""
    if where == "hold":
        realHold = schema.HoldBoard

        def hold(number):
            realHold(number)
            raise RuntimeError("after the hold")
        return patch.object(schema, "HoldBoard", side_effect=hold)
    realSave = StateManager.SaveState
    calls = []

    def save():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("the publish failed")
        return realSave()
    return patch.object(StateManager, "SaveState", side_effect=save)


@pytest.mark.parametrize("where", ["hold", "publish"])
def test_a_board_that_failed_after_its_hold_is_neither_made_nor_held(
        noBoards, where):
    """Neither half stays (AutonaActions.ForgetBoard). Until the S4y review
    only the flag was dropped: the board stayed held with nothing to apply
    it, and the next attempt's seed was held back with its edits."""
    actions = AutonaActions.instance
    with _failingAfterTheHold(where), pytest.raises(RuntimeError):
        actions.EnsureBoard(1, panel=True)

    assert actions.BoardEnsured(1) is False
    assert not _held(1)
    assert StateManager.saveBlocked == 0
    StateManager.SaveState()  # what the failed publish left queued

    assert actions.EnsureBoard(1, panel=True) is True
    assert _held(1)
    assert _published(1)["team"]["1"]["color"]
    assert StateManager.HasPendingChanges("score.1") is False


def test_a_board_is_made_once_its_launch_write_is_in(noBoards):
    """Ready (BoardEnsured, a board host's Built) only at the end of the save
    cycle's writes, with its hold: a reader during the seed finds it neither
    ready nor held. A call that comes back in meanwhile is told no at once
    and makes nothing. It counted as ready from the start of the call until
    the S4y review."""
    actions = AutonaActions.instance
    realSeed = actions.SeedFreshBoard
    realHold = schema.HoldBoard
    seen = []

    def seed(board):
        if not seen:
            seen.append(None)  # the call coming back in below seeds as is
            seen[0] = ("seed", actions.BoardEnsured(board), _held(board),
                       actions.EnsureBoard(board, panel=True))
        return realSeed(board)

    def hold(board):
        seen.append(("hold", actions.BoardEnsured(board)))
        realHold(board)

    with patch.object(actions, "SeedFreshBoard", side_effect=seed), \
            patch.object(schema, "HoldBoard", side_effect=hold):
        assert actions.EnsureBoard(1, panel=True) is True

    assert seen == [("seed", False, False, False), ("hold", False)]
    assert actions.BoardEnsured(1) is True and _held(1)


def test_resetting_the_boards_forgets_them(noBoards):
    actions = AutonaActions.instance
    actions.EnsureBoard(1)
    actions.EnsureBoard(3)

    actions.ResetBoards()

    assert [actions.BoardEnsured(n) for n in (1, 2, 3)] == [False] * 3
    assert StateManager.Get("score", None) in (None, {})


def test_the_headless_boards_are_made_and_not_held(noBoards):
    with StateManager.SaveBlock():
        headless._SeedBoards()
    noBoards.settle()

    assert [AutonaActions.instance.BoardEnsured(n) for n in (1, 2, 3)] \
        == [True] * 3
    assert not any(_held(n) for n in (1, 2, 3))


def test_the_desktop_board_host_only_runs_the_operation(noBoards):
    """DesktopBoardHost is Run alone since ADR-005 S4ac-3: every board is
    made at launch (S4ac-1), so Built says yes, and Prepare / CommitEdit /
    DiscardTypedText / PlayerLoaded have no widget to build or finish --
    they make, seed and write nothing, as the headless host's. Until S4ac-3
    Built was core's flag and Prepare made a board core did not have (only
    the harness's drop brings that about), then its widgets. The checks the
    hotkeys still make of Built go in S4ad."""
    from src import AutonaWebOperations as WebOps

    host = WebOps.boardHost
    assert isinstance(host, WebOps.DesktopBoardHost)
    assert [host.Built(n) for n in (1, 2, 3)] == [True] * 3

    host.Prepare(1)
    noBoards.settle()
    assert AutonaActions.instance.BoardEnsured(1) is False
    assert StateManager.Get("score.1") is None
    assert not _held(1)

    AutonaActions.instance.MakeBoards(panel=True)
    AutonaActions.instance.SetTeamName(2, 1, "kept")

    def everything():
        host.Prepare(2)
        host.CommitEdit(2)
        host.DiscardTypedText(2)
        host.PlayerLoaded(2, 1, 1, True)
    host.Run(everything)
    noBoards.settle()

    assert StateManager.Get("score.2.team.1.teamName") == "kept"
    assert StateManager.HasPendingChanges("score.2") is True


# ── the desktop's launch: AutonaActions.MakeBoards (ADR-005 S4ac-1) ──────


def test_the_desktop_launch_makes_every_board_and_holds_1_and_2(autonaApp):
    """What bootstrap_gui() left, recorded by the harness before it dropped
    the boards (tests/conftest.py): all three made, boards 1 / 2 behind
    their 反映 button and the crew board not, nothing pending -- their launch
    write is what was published, whole. Until S4ac-1 the bootstrap only
    reset the score subtree, and a board was made and held as the window
    built its widgets."""
    launch = autonaApp.launch

    assert launch["made"] == [True, True, True]
    assert launch["held"] == ["score.1", "score.2"]
    assert launch["pending"] == [False, False, False]
    assert launch["heldKeys"] == {}
    assert _same(launch["published"], launch["live"])
    published = launch["published"]
    assert set(published) == {"1", "2", "3"}
    for number in ("1", "2"):
        for team in ("1", "2"):
            assert published[number]["team"][team]["color"]
            assert list(published[number]["team"][team]["player"]) == ["1"]
        assert published[number]["best_of"] == 0
    assert set(published["3"]["team"]["1"]["player"]) == {
        str(member) for member in range(1, 10)}


def test_the_desktop_and_headless_launches_make_the_same_boards(
        noBoards, displayOptions):
    """MakeBoards with `panel` (the desktop) and without (the headless shell)
    write the same boards, values and key order, in one save cycle each.
    What only `panel` does: boards 1 / 2 held, the display options brought
    back to the settings, the polling turned off."""
    against = {f"display_options.{key}": not value
               for key, value in displayOptions.items()}
    written = []
    realSet = SettingsManager.Set

    def spy(key, value):
        written.append((key, value))
        return realSet(key, value)

    def launch(panel):
        del written[:]
        seen = []
        StateManager.observers.state_updated.subscribe(seen.append)
        try:
            with _settingsWith(against), \
                    patch.object(SettingsManager, "Set", side_effect=spy):
                AutonaActions.instance.MakeBoards(panel=panel)
        finally:
            StateManager.observers.state_updated.unsubscribe(seen.append)
        noBoards.settle()
        return {
            "cycles": len(seen),
            "boards": {number: _published(number) for number in (1, 2, 3)},
            "made": [AutonaActions.instance.BoardEnsured(number)
                     for number in (1, 2, 3)],
            "held": [_held(number) for number in (1, 2, 3)],
            "pending": [StateManager.HasPendingChanges(f"score.{number}")
                        for number in (1, 2, 3)],
            "options": copy.deepcopy(StateManager.Get("display_options")),
            "written": set(written),
        }

    desktop = launch(True)
    # Dropping the boards releases their holds, so the second launch's seed
    # is published at once, as in a launch of its own.
    noBoards.dropAllBoards()
    StateManager.Set("display_options", copy.deepcopy(displayOptions))
    noBoards.settle()
    headless = launch(False)

    for number in (1, 2, 3):
        assert desktop["boards"][number]["team"]["1"]["player"]
        assert _same(desktop["boards"][number], headless["boards"][number]), \
            number
    assert desktop["cycles"] == headless["cycles"] == 1
    assert desktop["made"] == headless["made"] == [True, True, True]
    assert desktop["held"] == [True, True, False]
    assert headless["held"] == [False, False, False]
    assert desktop["pending"] == headless["pending"] == [False] * 3
    assert desktop["options"] == {
        key: not value for key, value in displayOptions.items()}
    assert headless["options"] == displayOptions
    assert desktop["written"] == {("general.disable_autoupdate", True)}
    assert headless["written"] == set()


@pytest.mark.parametrize("panel", [True, False])
def test_the_boards_are_made_once(noBoards, panel):
    """A second MakeBoards while any board is made warns and writes nothing
    (ADR-005 S4ac-1 review): it would drop what was written since, and hold
    back the seed of a held board. Decided by the boards: once they are
    forgotten (the harness's drop), it makes them again."""
    actions = AutonaActions.instance
    assert actions.MakeBoards(panel=panel) is True
    actions.SetTeamName(2, 1, "kept")
    noBoards.settle()
    pending = StateManager.HasPendingChanges("score.2")

    with WriteRecorder() as recorder:
        assert actions.MakeBoards(panel=panel) is False
        noBoards.settle()

    assert recorder.sets == []
    assert StateManager.Get("score.2.team.1.teamName") == "kept"
    assert StateManager.HasPendingChanges("score.2") is pending is panel

    # One board made is enough to refuse.
    noBoards.dropAllBoards()
    actions.EnsureBoard(3)
    with WriteRecorder() as recorder:
        assert actions.MakeBoards(panel=panel) is False
    assert recorder.sets == []
    assert [actions.BoardEnsured(n) for n in (1, 2, 3)] == [False, False, True]

    noBoards.dropAllBoards()
    assert actions.MakeBoards(panel=panel) is True
    assert [actions.BoardEnsured(n) for n in (1, 2, 3)] == [True] * 3
    assert StateManager.Get("score.2.team.1.teamName") == ""


def _unreadSettings(monkeypatch, path):
    """A launch that could not read settings.json (`path`, never the
    harness's own): the defaults stand in, each save recorded."""
    from src.core import SettingsManager as settingsModule

    saved = []
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(path))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: saved.append(copy.deepcopy(
                            SettingsManager.settings)))
    monkeypatch.setattr(SettingsManager, "settings", {})
    return saved


def test_polling_is_turned_off_for_the_run_over_settings_it_cannot_open(
        noBoards, monkeypatch, tmp_path):
    """settings.json could not be read at launch and still cannot be opened
    (a directory: an OSError other than "not found"): the desktop's launch
    write (SetFollowService.TurnOffPolling) saves nothing -- a save would
    replace the unread file with this one key. SettingsManager makes it in
    memory only, for the run, and keeps it to put back over the file once a
    write can read it. The boards are made and held as ever."""
    from src.core.SetFollowService import SetFollowService

    unopenable = tmp_path / "settings.json"
    unopenable.mkdir()
    saved = _unreadSettings(monkeypatch, unopenable)

    SetFollowService.TurnOffPolling()
    assert AutonaActions.instance.MakeBoards(panel=True) is True

    assert saved == []
    assert SettingsManager.settings == {"general": {"disable_autoupdate": True}}
    assert SettingsManager._unsaved == [("general.disable_autoupdate", True)]
    assert SettingsManager.loadFailed is True
    assert [_held(n) for n in (1, 2, 3)] == [True, True, False]
    assert SetFollowService.Polling() is False


def test_the_launch_write_reads_settings_json_again_and_keeps_it(
        noBoards, monkeypatch, tmp_path):
    """The launch's read failed only for a moment (a file held by an
    antivirus or sync tool): TurnOffPolling, the desktop's first write of a
    launch, reads settings.json again and saves its key over what it holds
    -- the port, the token and the rest are back for the rest of the
    launch."""
    from src.core.SetFollowService import SetFollowService

    stored = {"general": {"webserver_port": 6000,
                          "webserver_token": "kept-token-0123456789ab",
                          "disable_autoupdate": False}}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    saved = _unreadSettings(monkeypatch, path)

    assert AutonaActions.instance.MakeBoards(panel=True) is True

    stored["general"]["disable_autoupdate"] = True
    # Once per board 1 / 2 (each EnsureBoard with `panel`).
    assert saved == [stored, stored]
    assert SettingsManager.loadFailed is False
    assert SettingsManager.Get("general.webserver_port") == 6000
    assert SetFollowService.Polling() is False


def _callers(name):
    """(file, function) of every call to `name` in src/."""
    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and (
                        getattr(node.func, "attr", None) == name
                        or getattr(node.func, "id", None) == name):
                    found.add((path.relative_to(SRC).as_posix(),
                               function.name))
    return found


def test_only_core_holds_a_board_or_drops_the_score_root():
    """The hold and the score root's reset are core's board life (ADR-005
    S4y): the board widget's constructor held its board, the dock's reset
    the root and the headless shell's too, until then."""
    assert _callers("HoldBoard") == {("core/Actions.py", "EnsureBoard")}
    assert _callers("ResetScoreRoot") == {("core/Actions.py", "ResetBoards")}
    assert _callers("SeedFreshBoard") == {("core/Actions.py", "EnsureBoard")}
    # Every board at launch, in both shells (ADR-005 S4ac-1): the score
    # root's reset only there, and the skeleton only as a new board's start
    # (CreateScoreboard wrote it over the boards it did not show until then).
    assert _callers("MakeBoards") == {("bootstrap.py", "bootstrap_gui"),
                                      ("headless.py", "_SeedBoards")}
    assert _callers("ResetBoards") == {("core/Actions.py", "MakeBoards")}
    assert _callers("SeedBoard") == {("core/Actions.py", "SeedFreshBoard")}
    # And the hold's release, for a board that failed (the review of S4y).
    assert _callers("ReleaseBoard") == {("core/Actions.py", "ForgetBoard")}
    assert _callers("ReleaseSubtree") == {
        ("core/StateSchema.py", "ReleaseBoard")}
