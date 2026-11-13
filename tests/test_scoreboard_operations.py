"""Scoreboard operations, through core and the web operations.

These are the four things a caster does to a scoreboard during a stream --
change the score, swap sides, reset the score, clear the board -- driven
through the same entry points the hotkeys, the Web UI and the external
control API use. Every one of them had a shipped bug at some point (see
docs/changes/test-automation-strategy.md §2), and none of them had a test.

Nothing is stubbed: `autonaApp.ready(n)` makes the board in core as the
desktop makes it at launch, and `autonaApp.web()` is the web operations
module the HTTP routes delegate to (src/AutonaWebOperations.py). The Qt
SCORE's board widgets went in ADR-005 S4ac-3; where a test drove one of
them, it calls what that widget ran (the AutonaActions operation behind
its button, spinbox or field).

The assertions read StateManager, because StateManager is what OBS
overlays and the Web UI actually consume.
"""
import contextlib
import json
import threading
from unittest.mock import patch

import pytest

from src.core.SettingsManager import SettingsManager
from tests.conftest import (apply_set, clear_players,
                            load_player, tournamentLoaded)
from tests.fixtures.stats.spies import StatsTriggers
from tests.fixtures.write_recording import WriteRecorder


def _score(app, team, board=1):
    return app.get(f"score.{board}.team.{team}.score")


def _setPlayerName(board, team, name):
    """Seed a player tag on board number `board` the way a loaded set does:
    what the slot's card ran (load_player)."""
    load_player(board, team, {"gamerTag": name})


def _playerName(app, team, board=1):
    # The published key is the tag QLineEdit's objectName ("name"), which is
    # also what the overlays read.
    return app.get(f"score.{board}.team.{team}.player.1.name")


def _actions():
    from src.core.Actions import AutonaActions
    return AutonaActions.instance


def _typeTeamName(board, team, text):
    """A team name finished as the Qt board's field did (it went in ADR-005
    S4ac-3): the name, then its crest, each written only on a change and in
    its own save cycle. The Web's set_route writes the pair in one."""
    from src.core.SetLoadService import SetLoadService

    _actions().SetTeamName(board, team, text)
    SetLoadService(board).PublishTeamLogo(team, text)


@contextlib.contextmanager
def _restoredDisplayOption(key):
    """Put one display option back the way the test found it -- both halves.

    A display option deliberately lives in two places (SettingsManager is the
    persisted default a board re-reads when built, StateManager is what the
    overlays render), and this suite shares one process-global copy of each
    with every other test module. Restoring only the settings half left the
    *state* half flipped for whatever ran next.

    "Absent" is restored as absent, in both halves. A display option is
    normally missing from SettingsManager entirely -- boards read it with a
    default and never write it back -- so putting a literal True there is not
    the same state the test found, and a later board would publish that
    invented value.
    """
    from src.core.StateManager import StateManager

    path = f"display_options.{key}"
    missing = object()
    storedSetting = SettingsManager.Get(path, missing)
    storedState = StateManager.Get(path, missing)
    try:
        yield
    finally:
        if storedSetting is missing:
            SettingsManager.Unset(path)
        else:
            SettingsManager.Set(path, storedSetting)
        if storedState is missing:
            StateManager.Unset(path)
        else:
            StateManager.Set(path, storedState)


# ── score ────────────────────────────────────────────────────────────────

def test_score_up_and_down_reach_the_state(autonaApp):
    board = autonaApp.ready(1)
    actions = _actions()

    # The +1 / -1 (the Qt board's CommandScoreChange; 1-based teams here).
    actions.AdjustScore(board, 1, 1)
    actions.AdjustScore(board, 1, 1)
    actions.AdjustScore(board, 2, 1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 2
    assert _score(autonaApp, 2) == 1

    actions.AdjustScore(board, 1, -1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1


def test_score_never_goes_negative(autonaApp):
    board = autonaApp.ready(1)

    _actions().AdjustScore(board, 1, -1)
    autonaApp.settle()

    # The spinbox floor was the guard; without it the overlay would render a
    # "-1" that no operator can produce deliberately.
    assert _score(autonaApp, 1) == 0


def test_unknown_team_index_is_ignored(autonaApp):
    board = autonaApp.ready(1)
    _actions().AdjustScore(board, 1, 1)
    autonaApp.settle()

    # Reachable from the API/hotkey layer; must not raise or corrupt a board.
    _actions().AdjustScore(board, 6, 1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _score(autonaApp, 2) == 0


# ── best of ──────────────────────────────────────────────────────────────

def test_best_of_publishes_every_derived_field(autonaApp):
    board = autonaApp.ready(1)

    _actions().SetBestOf(board, 5)
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 5
    assert autonaApp.get("score.1.best_of_short_text") == "BO5"
    assert autonaApp.get("score.1.first_to") == 3
    assert autonaApp.get("score.1.first_to_short_text") == "FT3"
    # The long forms are localized, so the wording is not a contract -- but
    # the number is. A truthy check alone would pass on an unformatted
    # "Best of {}" template, which is precisely how this breaks.
    assert "5" in autonaApp.get("score.1.best_of_text")
    assert "3" in autonaApp.get("score.1.first_to_text")


def test_best_of_zero_blanks_the_long_texts(autonaApp):
    board = autonaApp.ready(1)
    _actions().SetBestOf(board, 3)
    autonaApp.settle()

    _actions().SetBestOf(board, 0)
    autonaApp.settle()

    # 0 means "not set" — the overlay shows nothing rather than "Best of 0".
    assert autonaApp.get("score.1.best_of_text") == ""
    assert autonaApp.get("score.1.first_to_text") == ""


# The set length is the state's since ADR-005 S2e: AutonaActions.SetBestOf
# writes it, from whichever entry point (the Qt board's spinbox rendered it
# from the held cycle until S4ac-3; the game tracker's rows followed it until
# S4ac-4).

def test_a_best_of_set_in_core_is_one_cycle(autonaApp):
    """The way the Web, /api/v1 and a set load set it: one save cycle."""
    board = autonaApp.ready(1)
    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        _actions().SetBestOf(board, 7)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.1"]
    assert autonaApp.get("score.1.best_of") == 7


def test_a_best_of_set_from_another_thread_reaches_the_board(autonaApp):
    """AutonaActions may be called from any thread: the length lands as
    from the GUI thread."""
    import threading

    board = autonaApp.ready(1)
    worker = threading.Thread(
        target=lambda: _actions().SetBestOf(board, 5))
    worker.start()
    worker.join()
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 5


def test_a_game_load_publishes_what_its_slots_wrote(autonaApp):
    """What an onLoad slot writes goes out with the load (a Qt player card's
    ReloadCharacters wrote what its rows picked as it rebound them, until
    ADR-005 S4ac-3): the commit, on gameLoaded with the roster since S4ad,
    only queues the boards inside the load's save cycle, and the cycle's
    close -- after every onLoad slot -- publishes what the load wrote by
    then, its slots' writes included (_Publish records what the GUI thread
    writes; another writer's pending edit waits for 反映:
    test_a_game_load_publishes_only_what_it_wrote). Were it published at
    the commit, the 反映 button would stay lit after a load nobody edited (a
    held write after the commit raises the flag and nothing lowers it)."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)

    def write():
        AutonaActions.instance.SetTeamName(1, 1, "written on onLoad")

    signals = AutonaGameAssetManager.instance.signals
    signals.onLoad.connect(write)
    try:
        autonaApp.publishGameLoad()
    finally:
        signals.onLoad.disconnect(write)

    assert autonaApp.state.HasPendingChanges("score.1") is False
    assert autonaApp.state.GetCommitted(
        "score.1.team.1.teamName") == "written on onLoad"


def test_a_game_load_that_wrote_the_board_leaves_nothing_pending(autonaApp):
    """The load's commit is queued inside its save cycle. The flag used to
    be lowered right there, with the keys still pending, so the board asked
    HasPendingChanges and kept the button lit; the drain that followed said
    nothing, and 反映 had nothing pending to lower it with (S4u). The False
    now comes from the drain: what the notifications last said is what
    HasPendingChanges says -- which is what lights the SCORE page's 反映
    (the Qt board's button, until ADR-005 S4ac-3)."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager

    autonaApp.ready(1)
    actions = _actions()
    autonaApp.state.CommitHeld("score.1")
    autonaApp.settle()
    assert autonaApp.state.HasPendingChanges("score.1") is False
    told = []

    def onDirty(prefix, dirty):
        if prefix == "score.1":
            told.append((dirty, autonaApp.state.HasPendingChanges(prefix)))

    def write():
        actions.SetTeamName(1, 1, "written on onLoad")

    signals = AutonaGameAssetManager.instance.signals
    channel = autonaApp.state.observers.held_dirty_changed
    signals.onLoad.connect(write)
    channel.subscribe(onDirty)
    try:
        autonaApp.publishGameLoad()
    finally:
        channel.unsubscribe(onDirty)
        signals.onLoad.disconnect(write)

    assert autonaApp.state.HasPendingChanges("score.1") is False
    assert told == [(True, True), (False, False)], (
        "each notification did not say what HasPendingChanges said")

    # And a real edit is still pending, and 反映 still publishes it.
    actions.SetTeamName(1, 1, "edited")
    assert autonaApp.state.HasPendingChanges("score.1") is True
    actions.ApplyChanges(1)
    autonaApp.settle()
    assert autonaApp.state.HasPendingChanges("score.1") is False


def test_after_a_game_load_the_clients_are_told_nothing_is_pending(
        autonaApp, monkeypatch):
    """The Socket.IO notice is on the same notifications (S4u): the load of
    the test above, through the Bridge as WebServer.__init__ connects it. The
    last thing a client hears is dirty: false, in whichever flushes the
    notices fell."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.AutonaWebServer import WebServer
    from src.core.Actions import AutonaActions
    from src.StateManagerBridge import StateManagerBridge

    autonaApp.ready(1)
    autonaApp.state.CommitHeld("score.1")
    autonaApp.settle()
    sent = []
    monkeypatch.setattr(WebServer, "_held_notices", set())
    monkeypatch.setattr(WebServer, "_delta_due", None)
    monkeypatch.setattr(WebServer, "_delta_running", True)
    monkeypatch.setattr(
        WebServer, "ws_emit",
        lambda event, *args, **kwargs: sent.append((event, args[0])))
    bridge = StateManagerBridge.instance

    def write():
        AutonaActions.instance.SetTeamName(1, 1, "written on onLoad")

    # A slot of this test's own: a server built earlier in the process has
    # WebServer.on_held_change connected too, and disconnecting that function
    # would not say whose connection goes.
    def told(prefix, dirty=None):
        WebServer.on_held_change(prefix, dirty)

    signals = AutonaGameAssetManager.instance.signals
    signals.onLoad.connect(write)
    bridge.held_state_updated.connect(told)
    bridge.held_dirty_changed.connect(told)
    try:
        autonaApp.publishGameLoad()
        WebServer._flush_pending_deltas()
        # This board's: any other held one the load rewrote has its own.
        notices = [payload for event, payload in sent
                   if event == "scoreboard_pending" and payload["board"] == 1]
        assert notices and notices[-1] == {"board": 1, "dirty": False}

        # An edit after it is told as one, and 反映 as the other.
        del sent[:]
        AutonaActions.instance.SetTeamName(1, 1, "edited")
        WebServer._flush_pending_deltas()
        assert sent[-1] == ("scoreboard_pending", {"board": 1, "dirty": True})
        AutonaActions.instance.ApplyChanges(1)
        WebServer._flush_pending_deltas()
        assert sent[-1] == ("scoreboard_pending", {"board": 1, "dirty": False})
    finally:
        bridge.held_state_updated.disconnect(told)
        bridge.held_dirty_changed.disconnect(told)
        signals.onLoad.disconnect(write)


def _onAnotherThread(write):
    """`write()` on a thread of its own, waited for (a request thread)."""
    errors = []

    def run():
        try:
            write()
        except BaseException as error:  # noqa: BLE001 -- re-raised below
            errors.append(error)

    worker = threading.Thread(target=run, name="a request")
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "the other thread's write did not finish"
    assert not errors, errors


def _loadWithOtherWriters(app, selecting=None, publishing=None):
    """A game load as the loader runs it, with another thread writing
    meanwhile; returns the deltas it published.

    The load's save cycle opened, as the loader opens it before it selects
    the game, and `selecting` run on another thread while it would select:
    a request's board operation, through the board host (whose lock nobody
    holds yet: the S4ad ledger's remainder 5, the window seen on the real
    window). Then the GUI thread's end (_Publish, under the board host's
    lock), with `publishing` run on another thread from a gameLoaded slot --
    after the wiring's roster and its commit, ahead of the cycle's close: a
    core-direct write (score:adjust), which takes no board lock."""
    from src.AutonaGameAssetManager import (AutonaGameAssetManager,
                                            _SaveCycle)
    from src.core.GameAssetCatalog import GameAssetCatalog

    manager = AutonaGameAssetManager.instance
    published = []

    def onDelta(payload):
        published.append(payload.get("delta"))

    def slot(_snapshot):
        if publishing is not None:
            _onAnotherThread(publishing)

    channel = app.state.observers.state_updated
    channel.subscribe(onDelta)
    manager.signals.gameLoaded.connect(slot)
    try:
        cycle = _SaveCycle()
        cycle.Open()
        try:
            if selecting is not None:
                _onAnotherThread(selecting)
        except BaseException:
            cycle.Release()
            raise
        manager._Publish(GameAssetCatalog.instance.Current(), cycle)
        app.settle()
    finally:
        manager.signals.gameLoaded.disconnect(slot)
        channel.unsubscribe(onDelta)
    return published


def test_a_game_load_publishes_only_what_it_wrote(autonaApp, monkeypatch):
    """Seen on the real window: a tag typed on the SCORE page while the game
    switched reached OBS without 反映 -- a half-typed one (E10 / E12 of 40
    writes against six switches). The load's commit published everything
    pending on the board when the load's cycle closed, the operator's edits
    with what the load wrote (the roster, the set length, onLoad's slots).
    It publishes what the load wrote now: an edit pending before the load,
    one a request made while the loader selected the game (a board
    operation: the board host's lock is only taken in _Publish) and one
    made while the load published (a core-direct write, which never takes
    it) all stay behind 反映, and 反映 publishes them."""
    from src import AutonaWebOperations as WebOps
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core import StateSchema as schema

    autonaApp.ready(1)
    state = autonaApp.state
    actions = _actions()
    tag = schema.PLAYER_TAG_KEY
    first = f"score.1.team.1.player.1.{tag}"
    second = f"score.1.team.2.player.1.{tag}"
    _setPlayerName(1, 1, "PUBLISHED")
    actions.ApplyChanges(1)
    autonaApp.settle()
    assert state.HasPendingChanges("score.1") is False
    secondBefore = state.GetCommitted(second)

    # Waiting behind 反映 when the load starts.
    assert WebOps.update_player_fields(1, 2, 1, {tag: "BEFORE"}) is None
    autonaApp.settle()

    realRoster = actions.ApplyGameRoster

    def roster(replaced):
        applied = realRoster(replaced)
        actions.SetTeamName(1, 1, "THE LOAD")
        return applied

    def onLoad():
        actions.SetTeamName(1, 2, "ON LOAD")

    monkeypatch.setattr(actions, "ApplyGameRoster", roster)
    signals = AutonaGameAssetManager.instance.signals
    signals.onLoad.connect(onLoad)
    try:
        deltas = _loadWithOtherWriters(
            autonaApp,
            selecting=lambda: WebOps.update_player_fields(
                1, 1, 1, {tag: "E10"}),
            publishing=lambda: actions.SetScore(1, 1, 3))
    finally:
        signals.onLoad.disconnect(onLoad)

    committed = state.GetCommitted
    # What the load wrote went out with it ...
    assert committed("score.1.team.1.teamName") == "THE LOAD"
    assert committed("score.1.team.2.teamName") == "ON LOAD"
    # ... and nothing of the operator's.
    assert committed(first) == "PUBLISHED"
    assert committed(second) == secondBefore
    assert committed("score.1.team.1.score") == 0
    published = json.dumps(deltas, default=repr)
    assert "E10" not in published and "BEFORE" not in published
    assert '"THE LOAD"' in published and '"ON LOAD"' in published
    assert state.HasPendingChanges("score.1") is True
    assert autonaApp.get(first) == "E10"

    actions.ApplyChanges(1)
    autonaApp.settle()
    assert state.HasPendingChanges("score.1") is False
    assert committed(first) == "E10"
    assert committed(second) == "BEFORE"
    assert committed("score.1.team.1.score") == 3


def test_a_load_s_write_over_a_pending_edit_waits_with_it(
        autonaApp, monkeypatch):
    """A key the load writes that another writer had pending -- before the
    load or while it ran -- stays behind 反映 with that edit: published, it
    would show what the load made of the edit (the set length's texts,
    derived again by a game load that changed the locale, from a BEST OF
    the operator had not applied). The load's other writes go out."""
    autonaApp.ready(1)
    state = autonaApp.state
    actions = _actions()
    actions.SetBestOf(1, 3)
    actions.ApplyChanges(1)
    autonaApp.settle()
    textBefore = state.GetCommitted("score.1.best_of_text")

    actions.SetBestOf(1, 5)                       # not applied
    autonaApp.settle()
    realRoster = actions.ApplyGameRoster

    def roster(replaced):
        applied = realRoster(replaced)
        actions.SetTeamName(1, 1, "THE LOAD")
        return applied

    monkeypatch.setattr(actions, "ApplyGameRoster", roster)
    monkeypatch.setattr(actions, "_formatMatchText",
                        lambda key, n: f"another locale {n}")
    _loadWithOtherWriters(autonaApp)

    committed = state.GetCommitted
    assert autonaApp.get("score.1.best_of_text") == "another locale 5"
    assert committed("score.1.best_of_text") == textBefore
    assert committed("score.1.best_of") == 3
    assert committed("score.1.team.1.teamName") == "THE LOAD"
    assert state.HasPendingChanges("score.1") is True

    actions.ApplyChanges(1)
    autonaApp.settle()
    assert committed("score.1.best_of_text") == "another locale 5"
    assert committed("score.1.best_of") == 5


def test_what_is_written_while_a_board_is_made_is_published(autonaApp):
    """A writer on another thread does not wait for a board being made, and
    ahead of the hold its write makes no held notification: it is published
    at once, and nothing is pending behind 反映 once the board is held.
    (The Qt board's build ended with a render that showed it, until ADR-005
    S4ac-3.)"""
    from src.core import StateSchema as schema

    hold = schema.HoldBoard

    def holdAfterAWrite(number):
        actions = _actions()
        actions.SetScore(number, 1, 3)
        actions.SetBestOf(number, 5)
        actions.SetMatchField(number, schema.MATCH_KEY, "Grand Final")
        hold(number)

    with patch.object(schema, "HoldBoard", side_effect=holdAfterAWrite):
        autonaApp.ready(1)

    committed = autonaApp.state.GetCommitted
    assert committed("score.1.team.1.score") == 3
    assert committed("score.1.best_of") == 5
    assert committed("score.1.match") == "Grand Final"
    assert autonaApp.state.HasPendingChanges("score.1") is False


def test_a_game_load_leaves_a_board_that_is_not_made_as_it_is(autonaApp):
    """A board not made (its launch failed; only a test's harness drops one
    since ADR-005 S4ac-1) is not held, and has no set length to start over:
    the skeleton stays what it is, live and published, and the commit
    (AutonaActions.CommitGameLoad names both boards; the wiring's since
    ADR-005 S4ad) finds nothing on it."""
    autonaApp.dropAllBoards()
    autonaApp.ready(1)
    autonaApp.state.Set("score.2", {})
    autonaApp.settle()

    try:
        autonaApp.publishGameLoad()

        assert autonaApp.get("score.2") == {}
        assert autonaApp.state.GetCommitted("score.2") == {}
        assert autonaApp.state.HasPendingChanges("score.2") is False
    finally:
        autonaApp.state.Unset("score.2")
        autonaApp.settle()


@pytest.mark.parametrize("number, turnsOff", [(1, True), (2, True), (3, False)])
def test_making_a_board_on_the_desktop_turns_the_polling_off(
        autonaApp, number, turnsOff):
    """The desktop's alone (SetFollowService.TurnOffPolling): polling left
    the board's UI, so a stale setting must not keep it running. Not in the
    seed the headless shell shares, whose polling is the operator's
    (tests/test_actions.py, tests/test_headless_seed.py)."""
    written = []
    realSet = SettingsManager.Set

    def spy(key, value):
        written.append(key)
        return realSet(key, value)

    with patch.object(SettingsManager, "Set", side_effect=spy):
        autonaApp.ready(number)

    assert ("general.disable_autoupdate" in written) is turnsOff


def test_a_failing_match_text_does_not_stop_what_sets_the_best_of(
        autonaApp, monkeypatch):
    """An empty matchNames (the locale tables not loaded) makes the Qt
    formatter raise. The set length is published with the English texts,
    and the callers that reach it directly since ADR-005 S2e go on: a set
    load applies its losers and players, the Web answers, a game load
    commits."""
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    from src.core.StateManager import StateManager

    board = autonaApp.ready(1)
    monkeypatch.setattr(AutonaLocaleHelper, "matchNames", {})

    apply_set(board, {
        "bestOf": 5, "team1losers": True, "overwrite": True,
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]]})
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 5
    assert autonaApp.get("score.1.best_of_text") == "Best of 5"
    assert autonaApp.get("score.1.team.1.losers") is True
    assert _playerName(autonaApp, 1) == "AAA"
    assert _playerName(autonaApp, 2) == "BBB"

    assert autonaApp.web().set_route(1, bestOf=7) == "OK"
    autonaApp.settle()
    assert autonaApp.get("score.1.first_to_text") == "First to 4"

    # The game load re-derives the text and goes on to commit it. 反映'd
    # first, with a text of another locale: a load publishes what it wrote
    # itself, and the set length chosen above would have waited for 反映
    # with the text made from it (test_a_load_s_write_over_a_pending_edit_
    # waits_with_it).
    autonaApp.state.Set("score.1.best_of_text", "Bo7")
    StateManager.CommitHeld("score.1")
    autonaApp.settle()

    autonaApp.publishGameLoad()

    assert StateManager.HasPendingChanges("score.1") is False
    assert StateManager.GetCommitted("score.1.best_of_text") == "Best of 7"
    assert autonaApp.state.saveBlocked == 0


def test_a_set_with_a_best_of_that_is_not_a_number_still_loads(autonaApp):
    """The one key is refused (a warning) and the rest of the set applies;
    the spinbox's TypeError used to abandon the losers and the players."""
    board = autonaApp.ready(1)
    _actions().SetBestOf(board, 3)
    autonaApp.settle()

    apply_set(board, {
        "bestOf": "five", "team1losers": True, "overwrite": True,
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]]})
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 3
    assert autonaApp.get("score.1.team.1.losers") is True
    assert _playerName(autonaApp, 1) == "AAA"
    assert _playerName(autonaApp, 2) == "BBB"


# ── swap ─────────────────────────────────────────────────────────────────

def test_swap_exchanges_scores_players_and_flags(autonaApp):
    board = autonaApp.ready(1)
    actions = _actions()

    actions.AdjustScore(board, 1, 2)
    actions.AdjustScore(board, 2, 1)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    actions.SetTeamLosers(board, 2, True)
    autonaApp.settle()

    actions.SwapTeams(board)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _score(autonaApp, 2) == 2
    assert _playerName(autonaApp, 1) == "RIGHT"
    assert _playerName(autonaApp, 2) == "LEFT"
    assert autonaApp.get("score.1.team.1.losers") is True
    assert autonaApp.get("score.1.team.2.losers") is False


def test_swap_exchanges_team_names(autonaApp):
    board = autonaApp.ready(2)  # team board: the names are visible there

    _typeTeamName(board, 1, "Alpha")
    _typeTeamName(board, 2, "Bravo")
    autonaApp.settle()

    _actions().SwapTeams(board)
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == "Bravo"
    assert autonaApp.get("score.2.team.2.teamName") == "Alpha"


def test_swap_is_its_own_inverse(autonaApp):
    board = autonaApp.ready(1)

    _actions().AdjustScore(board, 1, 3)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    autonaApp.settle()

    _actions().SwapTeams(board)
    _actions().SwapTeams(board)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 3
    assert _score(autonaApp, 2) == 0
    assert _playerName(autonaApp, 1) == "LEFT"
    assert _playerName(autonaApp, 2) == "RIGHT"


def test_swap_publishes_the_swapped_flag(autonaApp):
    board = autonaApp.ready(1)

    _actions().SwapTeams(board)
    autonaApp.settle()
    assert autonaApp.get("score.1.teamsSwapped") is True

    _actions().SwapTeams(board)
    autonaApp.settle()
    assert autonaApp.get("score.1.teamsSwapped") is False


def test_a_swap_is_one_save_cycle(autonaApp):
    board = autonaApp.ready(1)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        _actions().SwapTeams(board)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.1"]
    assert _playerName(autonaApp, 1) == "RIGHT"
    assert _playerName(autonaApp, 2) == "LEFT"


# The swap is a core operation since ADR-005 S2c (AutonaActions.SwapTeams).
# The hotkey calls it for the active board, the Web UI for the board it
# names -- made, and held, at launch since S4ac-1.


_STATS_SIGNALS = StatsTriggers.NAMES


@contextlib.contextmanager
def _statsFetches(board):
    """How often each of the board's statistics triggers fired (named after
    the board's old AutonaStatsUtil signals; core's StatsService.Refresh
    since ADR-005 S3e-5)."""
    with StatsTriggers(board) as triggers:
        yield triggers.counts


@pytest.mark.parametrize("ids, fetches", [
    (([101, 1101], [303, 3303]), 1),    # other players on each side: once
    ((None, None), 0),                  # no ids, nothing to fetch for
])
def test_a_1v1_swap_refetches_the_stats_once(autonaApp, ids, fetches):
    """The swap exchanges the ids in the state with no card involved, so
    the operation asks for the statistics itself (StatsService.TeamsChanged;
    the board did, from _NoticeSwap, until ADR-005 S3e-5): once per swap,
    and only when it put other players on the sides. The upset factor is
    recalculated by the operation, not through the trigger."""
    from src.core.PlayerService import PlayerService

    board = autonaApp.ready(1)
    for team, value in zip((1, 2), ids):
        PlayerService(f"score.1.team.{team}.player.1").SetPlayerId(value)
    autonaApp.settle()

    with _statsFetches(board) as counts:
        _actions().SwapTeams(board)
        autonaApp.settle()

    assert counts == dict(dict.fromkeys(_STATS_SIGNALS, fetches),
                          UpsetFactorCalculation=0)

def test_the_swap_hotkey_swaps_the_active_board_only(autonaApp):
    """It used to be connected to every built board: after a singles/team
    switch one key press swapped the hidden board as well."""
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    autonaApp.ready(2)
    _setPlayerName(1, 1, "LEFT")
    _setPlayerName(1, 2, "RIGHT")
    autonaApp.settle()
    assert AutonaActions.instance.ActiveBoard() == 1

    AutonaHotkeys.signals.swap_teams.emit()
    autonaApp.settle()

    assert autonaApp.get("score.1.teamsSwapped") is True
    assert _playerName(autonaApp, 1) == "RIGHT"
    assert autonaApp.get("score.2.teamsSwapped") is None


# Each board hotkey, the operation it runs and that operation's arguments on
# the active board (1 in singles).
BOARD_HOTKEYS = {
    "team1_score_up": ("AdjustScore", (1, 1, 1)),
    "team1_score_down": ("AdjustScore", (1, 1, -1)),
    "team2_score_up": ("AdjustScore", (1, 2, 1)),
    "team2_score_down": ("AdjustScore", (1, 2, -1)),
    "reset_scores": ("ResetScore", (1,)),
    "apply_changes": ("ApplyChanges", (1,)),
    "swap_teams": ("SwapTeams", (1,)),
}


@pytest.mark.parametrize("hotkey", list(BOARD_HOTKEYS))
def test_a_board_hotkey_runs_under_the_board_host_s_lock(
        autonaApp, boardLock, hotkey):
    """ADR-005 S4ad: the board operations run on their callers' threads, so
    a board hotkey's (on the GUI thread) is one of them -- run by the board
    host, under its lock, the active board read there too -- or it would
    land between the calls of a web operation running on a request thread
    (clear_all's cycles, a PATCH set's), and act on a board a format switch
    replaced meanwhile. Until S4ad those ran on the GUI thread too, which
    kept them apart: the swap since S4ad, ±1 / reset / 反映 since its
    review. The swap asked the host whether the board was built (Built) and
    had it finish the field being typed in (CommitEdit; S4y) until then,
    hooks that answered as the headless host's once the Qt SCORE went
    (S4ac-3)."""
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    actions = AutonaActions.instance
    method, arguments = BOARD_HOTKEYS[hotkey]
    realActiveBoard = actions.ActiveBoard
    calls = []

    def activeBoard():
        calls.append(("active board", boardLock.held()))
        return realActiveBoard()

    def operation(*args):
        calls.append((method, args, boardLock.held()))

    with patch.object(actions, "ActiveBoard", side_effect=activeBoard), \
            patch.object(actions, method, side_effect=operation):
        getattr(AutonaHotkeys.signals, hotkey).emit()
        autonaApp.settle()
    assert calls == [("active board", True), (method, arguments, True)]


def test_a_game_load_commits_inside_its_cycle_under_the_board_host_s_lock(
        autonaApp, boardLock, monkeypatch):
    """ADR-005 S4ad: the load's commit (AutonaActions.CommitGameLoad) is the
    wiring's (bootstrap.wire_game_roster), in the save block of the roster
    and the set length, once, on gameLoaded, ahead of every onLoad slot --
    not the asset adapter's after onLoad, where it was while a Qt player
    card wrote its rows on onLoad (until S4ac-3). Inside the load's own
    cycle it only queues the board: what the load wrote is still pending
    after it and through onLoad, and goes out, with what onLoad's slots
    wrote, when _Publish closes the cycle. All of it under the board host's
    lock, from the announcement to the close (the review: a board operation
    of another thread landing before the close would go out with the commit,
    without 反映) -- a lock _Publish takes itself, and gives back when it
    returns. (The Qt models' rebuild was the first step under it until the
    Qt model clean-up's PR4; _Publish's entry and exit are recorded in its
    place.)"""
    from src.AutonaGameAssetManager import (AutonaGameAssetManager,
                                            _SaveCycle)
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    state = autonaApp.state
    actions = AutonaActions.instance
    manager = AutonaGameAssetManager.instance
    blocked = state.saveBlocked
    calls = []
    realCommit = actions.CommitGameLoad
    realRoster = actions.ApplyGameRoster
    realPublish = manager._Publish
    realRelease = _SaveCycle.Release

    def publish(*args):
        calls.append(("publish", boardLock.held()))
        try:
            return realPublish(*args)
        finally:
            calls.append(("published", boardLock.held()))

    def roster(replaced):
        # The host's lock is the outer one: never taken inside the state's
        # (core's RLock, which the test cannot wrap under its live holders,
        # so this one is asked).
        calls.append(("roster", boardLock.held(), state.lock._is_owned()))
        result = realRoster(replaced)
        state.Set("score.1.team.1.teamName", "THE LOAD")
        return result

    def commit(written):
        realCommit(written)
        # The load's cycle and the wiring's own block are open: queued.
        calls.append(("commit", boardLock.held(),
                      state.saveBlocked - blocked,
                      state.HasPendingChanges("score.1")))

    def onLoad():
        state.Set("score.1.team.2.teamName", "ON LOAD")
        calls.append(("onLoad", boardLock.held(),
                      state.HasPendingChanges("score.1")))

    def release(cycle):
        calls.append(("close", boardLock.held()))
        return realRelease(cycle)

    monkeypatch.setattr(manager, "_Publish", publish)
    monkeypatch.setattr(actions, "ApplyGameRoster", roster)
    monkeypatch.setattr(actions, "CommitGameLoad", commit)
    monkeypatch.setattr(_SaveCycle, "Release", release)
    signals = manager.signals
    signals.onLoad.connect(onLoad)
    try:
        autonaApp.publishGameLoad()
    finally:
        signals.onLoad.disconnect(onLoad)

    assert calls == [("publish", False), ("roster", True, False),
                     ("commit", True, 2, True), ("onLoad", True, True),
                     ("close", True), ("published", False)]
    # The close published both, with nothing left behind 反映.
    assert state.HasPendingChanges("score.1") is False
    assert state.GetCommitted("score.1.team.1.teamName") == "THE LOAD"
    assert state.GetCommitted("score.1.team.2.teamName") == "ON LOAD"


def test_a_game_load_whose_roster_raises_still_commits(
        autonaApp, monkeypatch):
    """The wiring commits whatever the roster does (ADR-005 S4ad review:
    try / finally): a roster that wrote, then raised, leaves nothing of the
    load waiting behind 反映 once the cycle closes. The asset adapter's
    _Publish committed on every path until S4ad. The exception is the
    slot's: emit reports it and the load goes on (onLoad runs)."""
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    state = autonaApp.state
    actions = AutonaActions.instance
    loaded = []

    def roster(replaced):
        state.Set("score.1.team.1.teamName", "HALF A ROSTER")
        raise RuntimeError("the roster failed part-way")

    monkeypatch.setattr(actions, "ApplyGameRoster", roster)
    signals = AutonaGameAssetManager.instance.signals
    onLoad = lambda: loaded.append(state.HasPendingChanges("score.1"))
    signals.onLoad.connect(onLoad)
    try:
        autonaApp.publishGameLoad()
    finally:
        signals.onLoad.disconnect(onLoad)

    assert loaded == [True]
    assert state.HasPendingChanges("score.1") is False
    assert state.GetCommitted("score.1.team.1.teamName") == "HALF A ROSTER"


def test_the_load_set_hotkey_opens_the_active_boards_window_only(
        autonaApp, monkeypatch):
    """ADR-005 S4y: routed once for the process (AutonaHotkeys._onLoadSet)
    to the shell's window of the active board. Each built 1v1 / team board
    connected it to itself until then, so with both built one press opened
    both windows, the hidden board's too."""
    from src.AutonaHotkeys import AutonaHotkeys
    from src.AutonaShellOperations import AutonaShellOperations
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    autonaApp.ready(2)
    assert AutonaActions.instance.ActiveBoard() == 1
    opened = []
    monkeypatch.setattr(AutonaShellOperations.instance, "ShowSetSelection",
                        lambda n: opened.append(n))

    AutonaHotkeys.signals.load_set.emit()
    autonaApp.settle()
    assert opened == [1]

    with patch.object(AutonaActions.instance, "ActiveBoard", return_value=2):
        AutonaHotkeys.signals.load_set.emit()
        autonaApp.settle()
    assert opened == [1, 2]


def test_the_load_set_hotkey_opens_the_score_pages_dialog(
        autonaApp, monkeypatch):
    """ADR-005 S4ab-2: Ctrl+Shift+O has the main window show SCORE and its
    page open the active board's dialog (Window.OpenScoreSets). The way
    back to the Qt window (general.qt_scoreboard_panel) went in S4ac-3. The
    main window is stood in for (src/Autona.py cannot be imported)."""
    from types import SimpleNamespace

    from src.AutonaHotkeys import AutonaHotkeys
    from src.AutonaShellOperations import AutonaShellOperations
    from src.core.Actions import AutonaActions

    shell = AutonaShellOperations.instance
    autonaApp.ready(1)
    autonaApp.ready(2)
    asked = []
    monkeypatch.setattr(shell, "_parent",
                        SimpleNamespace(OpenScoreSets=asked.append))

    AutonaHotkeys.signals.load_set.emit()
    autonaApp.settle()
    with patch.object(AutonaActions.instance, "ActiveBoard", return_value=2):
        AutonaHotkeys.signals.load_set.emit()
        autonaApp.settle()

    assert asked == [1, 2]


def test_the_load_set_hotkey_on_the_score_page_does_not_raise(
        autonaApp, monkeypatch):
    """What asking the main window raises is logged, not raised out of the
    hotkey's handler (to sys.excepthook, PySide6's for a slot)."""
    import sys
    from types import SimpleNamespace

    from loguru import logger

    from src.AutonaHotkeys import AutonaHotkeys
    from src.AutonaShellOperations import AutonaShellOperations

    shell = AutonaShellOperations.instance
    autonaApp.ready(1)

    def fail(*args):
        raise RuntimeError("the window failed")

    monkeypatch.setattr(shell, "_parent", SimpleNamespace(OpenScoreSets=fail))
    hooked = []
    monkeypatch.setattr(sys, "excepthook",
                        lambda kind, error, trace: hooked.append(error))
    logged = []
    sink = logger.add(lambda message: logged.append(message.record),
                      level="ERROR")
    try:
        AutonaHotkeys.signals.load_set.emit()
        autonaApp.settle()
    finally:
        logger.remove(sink)

    assert hooked == []
    assert [str(record["exception"].value) for record in logged
            if record["exception"] is not None] == ["the window failed"]


def test_the_load_set_hotkey_does_nothing_without_a_set_window(
        autonaApp, monkeypatch):
    """The crew board has no set selection. Each built board answered for
    itself until ADR-005 S4y; an unbuilt board got none either until S4ac-3
    (every board is made at launch, and the host says so)."""
    from src.AutonaHotkeys import AutonaHotkeys
    from src.AutonaShellOperations import AutonaShellOperations
    from src.core.Actions import AutonaActions

    autonaApp.dropAllBoards()
    autonaApp.ready(1)
    opened = []
    monkeypatch.setattr(AutonaShellOperations.instance, "ShowSetSelection",
                        lambda n: opened.append(n))

    with patch.object(AutonaActions.instance, "ActiveBoard", return_value=3):
        AutonaHotkeys.signals.load_set.emit()
        autonaApp.settle()

    assert opened == []


def test_a_web_swap_is_done_when_it_answers(autonaApp):
    """The legacy route used to queue a signal into the board and answer
    "OK" before anything had moved; get_swap read the board's attribute."""
    autonaApp.ready(1)
    _setPlayerName(1, 1, "LEFT")
    _setPlayerName(1, 2, "RIGHT")
    autonaApp.settle()
    actions = autonaApp.web()

    assert actions.swap_teams("1") == "OK"
    assert actions.get_swap("1") == "True"
    assert _playerName(autonaApp, 1) == "RIGHT"

    assert actions.swap_teams(1) == "OK"
    assert actions.get_swap(1) == "False"


def test_a_web_swap_waits_for_apply_on_the_launched_board(autonaApp):
    """The board is made, and held, at launch (ADR-005 S4ac-1): a swap from
    the Web waits for 反映 there and survives every later route to the
    board. Until S4ac-3 the route built a board that had no widgets first
    (before S4ac-1 an unbuilt board was not held, and building it seeded
    its subtree afresh)."""
    from src.core.StateManager import StateManager

    autonaApp.ready(2)
    committed = StateManager.GetCommitted("score.2.teamsSwapped")
    actions = autonaApp.web()

    assert actions.swap_teams(2) == "OK"
    autonaApp.settle()

    assert autonaApp.get("score.2.teamsSwapped") is True
    assert StateManager.HasPendingChanges("score.2") is True
    assert StateManager.GetCommitted("score.2.teamsSwapped") == committed
    assert committed is not True

    assert actions.get_swap(2) == "True"
    actions.get_scoreboard(2)
    autonaApp.settle()
    assert autonaApp.get("score.2.teamsSwapped") is True


def test_reading_the_swap_flag_makes_no_board(autonaApp):
    """get_swap reads the flag from the state and writes nothing. The
    desktop built the board's widgets for it until ADR-005 S4ac-3 (before
    S2c it reached the flag through the board); nothing makes a board
    again since."""
    from src.core.Actions import AutonaActions
    from src.core.StateManager import StateManager

    autonaApp.dropAllBoards()
    StateManager.Unset("score.2")
    autonaApp.settle()
    try:
        with WriteRecorder() as recorder:
            assert autonaApp.web().get_swap(2) == "False"
            autonaApp.settle()
        assert recorder.sets == []
        assert not AutonaActions.instance.BoardEnsured(2)
    finally:
        autonaApp.dropAllBoards()
        StateManager.Unset("score.2")
        autonaApp.settle()


# ── reset score ──────────────────────────────────────────────────────────

def test_reset_score_zeroes_scores_and_keeps_players(autonaApp):
    board = autonaApp.ready(1)

    _actions().AdjustScore(board, 1, 2)
    _actions().AdjustScore(board, 2, 3)
    _setPlayerName(board, 1, "KEEP ME")
    autonaApp.settle()

    _actions().ResetScore(board)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 0
    assert _score(autonaApp, 2) == 0
    # Reset Score is the between-games button: the players stay on the board.
    assert _playerName(autonaApp, 1) == "KEEP ME"


# ── the web operations layer ─────────────────────────────────────────────
#
# clear_all / reset_match reached for the best_of spinbox, which the Qt 1v1
# and team boards reparented out of `scoreColumn` while built. Looking it
# up with scoreColumn.findChild() therefore returned None and raised
# AttributeError -- an HTTP 500 on /scoreboard<N>-clear-all and on the API's
# :clear / :reset-match (fixed in 019d85f9). These run through the real web
# operations (AutonaWebOperations; the web server's actions class until
# ADR-005 S3c / S3e-9) so that regression cannot come back unnoticed.

@pytest.mark.parametrize("boardNumber", [1, 2])
def test_reset_match_clears_score_and_best_of(autonaApp, boardNumber):
    from src.core.Actions import AutonaActions

    autonaApp.ready(boardNumber)
    actions = autonaApp.web()

    # What the +1 and BEST OF run (the Qt board's, until ADR-005 S4ac-3).
    AutonaActions.instance.AdjustScore(boardNumber, 1, 2)
    AutonaActions.instance.SetBestOf(boardNumber, 5)
    autonaApp.settle()

    actions.reset_match(boardNumber)
    autonaApp.settle()

    assert _score(autonaApp, 1, boardNumber) == 0
    assert _score(autonaApp, 2, boardNumber) == 0
    assert autonaApp.get(f"score.{boardNumber}.best_of") == 0


def test_reset_match_restores_full_stocks_on_the_crew_board(autonaApp):
    # "Did not raise" would also pass on a reset_match that did nothing: for
    # crew, the reset means every member back to full stocks and nobody
    # active (CrewBattleService.ResetStocks since ADR-005 S3e-2).
    from src.core.CrewBattleService import CrewBattleService

    DEFAULT_STOCK = CrewBattleService.DEFAULT_STOCK
    autonaApp.ready(3)
    # What the crew board's stock spinboxes and active buttons ran (its
    # `crew`; the Qt board went in ADR-005 S4ac-3).
    crew = CrewBattleService(3)

    crew.AdjustMemberStock(1, 1, -2)
    crew.AdjustMemberStock(2, 2, -1)
    crew.SelectActiveMember(1, 1)
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.player.1.stock") == DEFAULT_STOCK - 2
    assert autonaApp.get("score.3.team.2.player.2.stock") == DEFAULT_STOCK - 1
    assert autonaApp.get("score.3.team.1.activeMember") == "1"

    autonaApp.web().reset_match(3)
    autonaApp.settle()

    for team in (1, 2):
        # Every member row the board has (MAX_MEMBERS, the hidden ones too).
        for member in range(1, CrewBattleService.MAX_MEMBERS + 1):
            assert autonaApp.get(
                f"score.3.team.{team}.player.{member}.stock") == DEFAULT_STOCK
        assert autonaApp.get(f"score.3.team.{team}.activeMember") == "0"


def test_change_set_data_publishes_best_of_on_the_crew_board(autonaApp):
    """Setup guard for the reset test below, kept separate deliberately.

    If the crew's set data (CrewBattleService.ApplySetData; the board's
    ChangeSetData until ADR-005 S4x) ever stops writing score.3.best_of, the
    reset test's own
    precondition would silently disappear and it would keep passing on a
    never-written key. Pinned separately instead.
    """
    autonaApp.ready(3)

    apply_set(3, {"bestOf": 5})
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 5


def test_the_crew_board_is_made_with_a_best_of(autonaApp):
    # Overlays read score.3.best_of unconditionally, so the key must exist
    # from the start rather than only after the first set data.
    autonaApp.ready(3)
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 0


def test_reset_match_clears_best_of_on_the_crew_board(autonaApp):
    # BUG-4 regression: reset_match used to leave score.3.best_of at the value
    # ChangeSetData wrote, so overlays kept rendering the old BO.
    autonaApp.ready(3)

    apply_set(3, {"bestOf": 5})
    autonaApp.settle()

    autonaApp.web().reset_match(3)
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 0


def test_clear_all_clears_best_of_on_the_crew_board(autonaApp):
    autonaApp.ready(3)

    apply_set(3, {"bestOf": 5})
    autonaApp.settle()

    autonaApp.web().clear_all(3)
    autonaApp.settle()

    assert autonaApp.get("score.3.best_of") == 0


@pytest.mark.parametrize("boardNumber", [1, 2])
def test_clear_all_resets_scores_best_of_and_players(autonaApp, boardNumber):
    from src.core.Actions import AutonaActions

    autonaApp.ready(boardNumber)
    actions = autonaApp.web()

    # What the +1 and BEST OF run (the Qt board's, until ADR-005 S4ac-3).
    AutonaActions.instance.AdjustScore(boardNumber, 1, 2)
    AutonaActions.instance.SetBestOf(boardNumber, 5)
    _setPlayerName(boardNumber, 1, "GONE")
    autonaApp.settle()

    actions.clear_all(boardNumber)
    autonaApp.settle()

    assert _score(autonaApp, 1, boardNumber) == 0
    assert _score(autonaApp, 2, boardNumber) == 0
    assert autonaApp.get(f"score.{boardNumber}.best_of") == 0
    assert not _playerName(autonaApp, 1, boardNumber)


def test_reset_scores_action_leaves_players_alone(autonaApp):
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    actions = autonaApp.web()

    AutonaActions.instance.AdjustScore(1, 1, 2)   # the +1
    _setPlayerName(1, 1, "KEEP ME")
    autonaApp.settle()

    actions.reset_scores(1)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 0
    assert _playerName(autonaApp, 1) == "KEEP ME"


def test_the_score_up_and_down_actions_reach_the_state(autonaApp):
    autonaApp.ready(1)
    actions = autonaApp.web()

    actions.team_scoreup(1, 1)
    actions.team_scoreup(1, 2)
    actions.team_scoreup(1, 2)
    autonaApp.settle()

    assert _score(autonaApp, 1) == 1
    assert _score(autonaApp, 2) == 2

    actions.team_scoredown(1, 2)
    autonaApp.settle()

    assert _score(autonaApp, 2) == 1


def test_team_color_action_reaches_the_state(autonaApp):
    autonaApp.ready(1)

    autonaApp.web().team_color(1, 2, "#123456")
    autonaApp.settle()

    assert autonaApp.get("score.1.team.2.color") == "#123456"


# ── the inverted write path (headless core Step 3) ───────────────────────
#
# The score is written by AutonaActions and what shows it follows the state
# (the Qt board did until ADR-005 S4ac-3, the SCORE page since), so the
# thing that can go wrong is a reaction to a change writing it back (the
# echo loop the design forbids -- Design.md §3, docs/changes/
# headless-core-redesign.md §3).

def _heldUpdateSpy(prefix):
    """Collect held_state_updated notifications for one prefix.

    Returns (list, unsubscribe). Subscribing to the core channel directly is
    what test code and core code may do; a *widget* may not (it would never
    be unsubscribed -- tests/test_signal_lifetime.py enforces that).
    """
    from src.core.StateManager import StateManager

    seen = []

    def onHeld(notified):
        if notified == prefix:
            seen.append(notified)

    StateManager.observers.held_state_updated.subscribe(onHeld)
    return seen, lambda: StateManager.observers.held_state_updated.unsubscribe(
        onHeld)


def test_a_score_change_is_exactly_one_save_cycle(autonaApp):
    """Echo-loop check: nothing reacting to the change may write the score
    back."""
    board = autonaApp.ready(1)

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        _actions().AdjustScore(board, 1, 1)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _score(autonaApp, 1) == 1
    assert seen == ["score.1"], (
        f"a +1 produced {len(seen)} save cycles instead of 1: something "
        "reacted to the state change by writing it again")


def test_reset_score_is_exactly_one_save_cycle(autonaApp):
    board = autonaApp.ready(1)
    _actions().AdjustScore(board, 1, 2)
    _actions().AdjustScore(board, 2, 1)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        _actions().ResetScore(board)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert _score(autonaApp, 1) == 0 and _score(autonaApp, 2) == 0
    # Two keys, one cycle -- an overlay must never render a half-reset board.
    assert seen == ["score.1"]


def test_a_score_hotkey_works_with_no_board_built(autonaApp):
    """The structural fix: the hotkey no longer needs a panel to exist.

    Connected per board, Ctrl+Shift+1 did nothing at all when the operator's
    saved battle format was crew (board 3 never wired the score hotkeys), and
    scored *both* boards after a singles/team switch.
    """
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.Actions import AutonaActions

    autonaApp.dropAllBoards()
    autonaApp.settle()
    assert AutonaActions.instance.ActiveBoard() == 1
    assert autonaApp.get("score.1") is None

    AutonaHotkeys.signals.team1_score_up.emit()
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.score") == 1


def test_the_score_hotkey_is_routed_exactly_once(autonaApp):
    """ConnectActions() has to stay idempotent.

    `signals` is class-level and outlives every instance, so a second connect
    would leave each score hotkey applying its delta twice -- invisible until
    an operator's +1 came out as +2. bootstrap_gui() can run more than once
    in a process (this harness bootstraps its own runtime), which is what the
    guard inside ConnectActions is there for.
    """
    from src.AutonaHotkeys import AutonaHotkeys
    from src.core.StateManager import StateManager

    autonaApp.dropAllBoards()
    # dropAllBoards() only clears score.{n} for boards that were actually
    # built, so with none built it leaves whatever the previous test wrote.
    # The count below is the whole assertion, so start from nothing.
    StateManager.Unset("score.1")
    autonaApp.settle()
    assert autonaApp.get("score.1.team.1.score") is None

    # Back to un-routed, then route twice: the second call must do nothing.
    # DisconnectActions exists for exactly this -- there is no object whose
    # destruction would release a class-level connection.
    AutonaHotkeys.DisconnectActions()
    AutonaHotkeys.ConnectActions()
    AutonaHotkeys.ConnectActions()

    AutonaHotkeys.signals.team1_score_up.emit()
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.score") == 1, (
        "one hotkey press did not produce exactly one +1")


def test_the_sponsor_toggle_publishes_once(autonaApp):
    """A display option is toggled through Actions (the Web's eye menu; the
    Qt board's sponsor checkbox until ADR-005 S4ac-3).

    display_options.* is not held, so this is the operation where a delta is
    directly observable: exactly one revision per toggle. (The Qt board's
    mirrors -- the QAction, the checkbox, the inline sponsor field -- were
    checked not to produce a second one.)
    """
    from src.core.StateManager import StateManager

    autonaApp.ready(1)
    with _restoredDisplayOption("show_sponsor"):
        revision = StateManager.revision
        _actions().SetDisplayOption("show_sponsor", False)
        autonaApp.settle()

        assert autonaApp.get("display_options.show_sponsor") is False
        assert StateManager.revision == revision + 1, (
            "the sponsor toggle published more than once")


def test_a_remote_display_option_reaches_the_state(autonaApp):
    """POST /display-option no longer pokes each board's QAction.

    It writes the option once. Before Step 3 the route walked boards 1 and
    2 -- and *built* both of them as a side effect of a Web UI eye-menu
    click; the Qt boards mirrored it from the state notification until
    ADR-005 S4ac-3.
    """
    autonaApp.ready(1)
    with _restoredDisplayOption("show_seed"):
        autonaApp.web().set_display_option(
            {"option": "show_seed", "value": False})
        autonaApp.settle()

        assert autonaApp.get("display_options.show_seed") is False


def test_an_unknown_display_option_is_still_rejected(autonaApp):
    autonaApp.ready(1)
    result = autonaApp.web().set_display_option(
        {"option": "show_nothing", "value": True})

    assert result.startswith("ERROR")
    assert autonaApp.get("display_options.show_nothing") is None


# ── the board's own fields (headless core Step 5) ────────────────────────
#
# The Qt board's team card inputs and match bar combos used to be bound by
# findChildren loops that wrote score.{N}.team.{T}.{objectName} /
# score.{N}.{objectName}. They called AutonaActions from Step 5, against key
# names that live in StateSchema, and went in ADR-005 S4ac-3; the operations
# they called are what is checked here: the edit reaches the state, the
# " [L]" suffix comes with the flag, and nothing costs an extra save cycle.

def test_the_same_team_name_again_leaves_nothing_pending(autonaApp):
    """Behaviour change, pinned deliberately (headless core Step 5).

    The Qt field's editingFinished fired on every focus-out, typed into or
    not, and the old handler wrote unconditionally -- so tabbing through the
    field marked the board dirty. The name and its crest are written only on
    a change since, the same way the set-load path has been since Step 4b.
    """
    from src.core.StateManager import StateManager

    board = autonaApp.ready(2)
    _typeTeamName(board, 1, "Alpha")
    StateManager.CommitHeld("score.2")
    autonaApp.settle()
    assert StateManager.HasPendingChanges("score.2") is False

    _typeTeamName(board, 1, "Alpha")
    autonaApp.settle()

    assert StateManager.HasPendingChanges("score.2") is False


def test_the_losers_flag_writes_the_flag_and_the_player_names(autonaApp):
    """The flag and the " [L]" the thumbnail generator renders, together.

    Both are AutonaActions.SetTeamLosers since ADR-005 S2b, in one save
    cycle. The suffix used to be the Qt LOSERS checkbox's second handler --
    every player card's SetLosers, connected per card -- so the set-load path
    needed its own copy (_ApplyLosersToPlayers) because it does not have
    this signal; now the two paths share the core half.
    """
    from src.core.StateManager import StateManager

    board = autonaApp.ready(1)
    _setPlayerName(board, 1, "AAA")
    autonaApp.settle()

    cycles = []
    StateManager.observers.held_state_updated.subscribe(cycles.append)
    try:
        _actions().SetTeamLosers(board, 1, True)
        autonaApp.settle()
    finally:
        StateManager.observers.held_state_updated.unsubscribe(cycles.append)

    assert autonaApp.get("score.1.team.1.losers") is True
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "AAA [L]"
    assert autonaApp.get("score.1.team.1.player.1.mergedOnlyName") == "AAA [L]"
    assert cycles.count("score.1") == 1

    _actions().SetTeamLosers(board, 1, False)
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.losers") is False
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "AAA"


def test_the_match_bar_reaches_the_state(autonaApp):
    from src.core import StateSchema as schema

    board = autonaApp.ready(1)

    for field, value in ((schema.PHASE_KEY, "Bracket"),
                         (schema.POOL_KEY, "Pool A"),
                         (schema.MATCH_KEY, "Winners Final")):
        _actions().SetMatchField(board, field, value)
    autonaApp.settle()

    assert autonaApp.get("score.1.phase") == "Bracket"
    assert autonaApp.get("score.1.pool") == "Pool A"
    assert autonaApp.get("score.1.match") == "Winners Final"


def test_a_match_bar_edit_is_exactly_one_save_cycle(autonaApp):
    """One field, one write. (The Qt combo's currentIndexChanged and its
    line edit's editingFinished were both wired to it, so picking a listed
    entry used to write the same value twice.)"""
    from src.core import StateSchema as schema

    board = autonaApp.ready(1)

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        _actions().SetMatchField(board, schema.MATCH_KEY, "Winners Final")
        autonaApp.settle()
    finally:
        unsubscribe()

    assert autonaApp.get("score.1.match") == "Winners Final"
    assert seen == ["score.1"], (
        f"one match-bar edit produced {len(seen)} save cycles instead of 1")


# ── the match bar: an operator's reset vs. a set load's pre-pass (S-17) ──
#
# Two clears that used to be one function. The reset entry points -- the
# すべての入力値をリセット button, and the web operations' reset_match /
# clear_all, which every legacy route, Socket.IO event and /api/v1
# :reset-match / :clear delegates to -- blank round name / phase / pool.
# The clear a set load (SetFollowService.LoadSet) runs before a new set
# arrives must not:
# SetLoadService.ApplyMatchInfo keeps the round for a payload that carries
# none, so a reset-style clear there would blank the round mid-stream.

_RESETS = {
    # すべての入力値をリセット (the Qt board's button until ADR-005 S4ac-3).
    "reset_all": lambda app, n: app.web().reset_all(n),
    "reset_match": lambda app, n: app.web().reset_match(n),
    "clear_all": lambda app, n: app.web().clear_all(n),
}


@pytest.mark.parametrize("reset", sorted(_RESETS))
@pytest.mark.parametrize("boardNumber", [1, 2])
def test_a_reset_clears_the_match_bar(autonaApp, boardNumber, reset):
    board = autonaApp.ready(boardNumber)
    apply_set(board, dict(_SET))
    autonaApp.settle()
    assert _matchFields(autonaApp, board) == (
        "Winners Final", "Bracket", "Pool A")

    _RESETS[reset](autonaApp, boardNumber)
    autonaApp.settle()

    prefix = f"score.{boardNumber}"
    assert _matchFields(autonaApp, board) == ("", "", "")
    # The English form of the same round name (the YouTube title reads it).
    assert autonaApp.get(f"{prefix}.match_en") == ""
    # ...and the half of the reset that already worked still does.
    assert (_score(autonaApp, 1, boardNumber),
            _score(autonaApp, 2, boardNumber)) == (0, 0)
    assert autonaApp.get(f"{prefix}.team.2.losers") is False


@pytest.fixture
def tournament():
    """A tournament loaded that answers nothing: a set loads only with one
    (SetFollowService, ADR-005 S3e-7 review). The tests here drive the
    load, and the answer by hand."""
    with tournamentLoaded():
        yield


def test_a_set_without_a_round_name_keeps_the_previous_one(autonaApp,
                                                           tournament):
    """The set-load pre-pass (SetFollowService.LoadSet -> ClearScore) is not a reset.

    A payload carrying no round_name / tournament_phase / tournament_pool
    leaves the match bar as the previous set had it.
    """
    from src.core.SetFollowService import SetFollowService

    board = autonaApp.ready(1)
    apply_set(board, dict(_SET))
    autonaApp.settle()

    SetFollowService(board).LoadSet({"id": "1001", "auto_update": "set"})
    autonaApp.settle()
    # The pre-pass alone must not have touched it either.
    assert autonaApp.get("score.1.match") == "Winners Final"

    SetFollowService(board).UpdateSetData(
        {"id": "1001", "team1score": 1, "team2score": 0})
    autonaApp.settle()

    assert _matchFields(autonaApp, board) == (
        "Winners Final", "Bracket", "Pool A")
    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (1, 0)


def test_a_set_with_a_round_name_overwrites_the_previous_one(autonaApp,
                                                             tournament):
    from src.core.SetFollowService import SetFollowService

    board = autonaApp.ready(1)
    apply_set(board, dict(_SET))
    autonaApp.settle()

    SetFollowService(board).LoadSet({"id": "1002", "auto_update": "set"})
    autonaApp.settle()
    SetFollowService(board).UpdateSetData({
        "id": "1002",
        "round_name": "Losers Final",
        "round_name_en": "Losers Final",
        "tournament_phase": "Top 8",
        "tournament_pool": "Pool B",
    })
    autonaApp.settle()

    assert autonaApp.get("score.1.match_en") == "Losers Final"
    assert _matchFields(autonaApp, board) == ("Losers Final", "Top 8", "Pool B")


def test_clearing_the_score_still_zeroes_the_score_and_the_losers_flags(
        autonaApp):
    # The half of ClearScore that does work (AutonaActions.ClearScore; the
    # Qt board drove its spinboxes and checkboxes to it until ADR-005 S2d).
    board = autonaApp.ready(1)
    apply_set(board, dict(_SET))
    autonaApp.settle()
    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (2, 1)
    assert autonaApp.get("score.1.team.2.losers") is True

    _actions().ClearScore(board)
    autonaApp.settle()

    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (0, 0)
    assert autonaApp.get("score.1.team.2.losers") is False


def test_a_best_of_change_is_exactly_one_save_cycle(autonaApp):
    """The six derived keys are one operation.

    AutonaActions.SetBestOf writes the six keys in one block, so the whole
    change is a single export (ADR-005 S2e; the spinbox's handler wrapped
    them before, with the game tracker's rows until S4ac-4).
    """
    board = autonaApp.ready(1)

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        _actions().SetBestOf(board, 5)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert autonaApp.get("score.1.best_of") == 5
    assert autonaApp.get("score.1.first_to_short_text") == "FT3"
    assert seen == ["score.1"], (
        f"a best-of change produced {len(seen)} save cycles instead of 1")


def test_the_display_tournament_name_publishes_once(autonaApp):
    """Not held (it is tournamentInfo.*), so the delta is directly visible.
    TournamentInfoService.SetDisplayName: what the Qt board's field ran
    until ADR-005 S4ac-3, and the SCORE page's route."""
    from src.core.StateManager import StateManager
    from src.core.TournamentInfoService import TournamentInfoService

    autonaApp.ready(1)
    stored = StateManager.Get("tournamentInfo.displayTournamentName")
    try:
        revision = StateManager.revision
        TournamentInfoService.instance.SetDisplayName("SPECIAL EDITION")
        autonaApp.settle()

        assert autonaApp.get(
            "tournamentInfo.displayTournamentName") == "SPECIAL EDITION"
        assert StateManager.revision == revision + 1, (
            "the display name published more than once")
    finally:
        StateManager.Set(
            "tournamentInfo.displayTournamentName", stored or "")


def test_making_a_board_on_the_desktop_republishes_every_display_option(
        autonaApp):
    """The loop the board used to carry its own copy of.

    An operator's toggles persist in SettingsManager, so building a board is
    what brings a program_state.json restored from an older session back in
    line with them. The definition is StateSchema's now.
    """
    from src.core import StateSchema as schema
    from src.core.StateManager import StateManager

    stored = StateManager.Get("display_options")
    try:
        StateManager.Unset("display_options")
        autonaApp.ready(1)
        autonaApp.settle()

        assert set(StateManager.Get("display_options") or {}) == set(
            schema.DISPLAY_OPTION_KEYS)
    finally:
        if stored is not None:
            StateManager.Set("display_options", stored)


# ── set loading (headless core Step 4b) ──────────────────────────────────
#
# The board no longer *is* the set data: core/SetLoadService writes it (the
# Qt board's widgets rendered it until ADR-005 S4ac-3). What is checked here
# beyond the service tests: the load as the app runs it reaches the state,
# in one cycle, and lands on the right side of a swapped board.

def _matchFields(app, board):
    """(match, phase, pool) of board number `board`, from the state."""
    prefix = f"score.{board}"
    return (app.get(f"{prefix}.match"), app.get(f"{prefix}.phase"),
            app.get(f"{prefix}.pool"))


_SET = {
    "round_name": "Winners Final",
    "round_name_en": "Winners Final",
    "tournament_phase": "Bracket",
    "tournament_pool": "Pool A",
    "team1score": 2,
    "team2score": 1,
    "team1losers": False,
    "team2losers": True,
}


def test_a_loaded_set_reaches_the_state(autonaApp):
    autonaApp.ready(1)
    apply_set(1, dict(_SET))
    autonaApp.settle()

    assert autonaApp.get("score.1.match") == "Winners Final"
    assert autonaApp.get("score.1.match_en") == "Winners Final"
    assert autonaApp.get("score.1.phase") == "Bracket"
    assert autonaApp.get("score.1.pool") == "Pool A"
    assert (_score(autonaApp, 1), _score(autonaApp, 2)) == (2, 1)
    assert autonaApp.get("score.1.team.1.losers") is False
    assert autonaApp.get("score.1.team.2.losers") is True


def test_a_set_load_is_exactly_one_save_cycle(autonaApp):
    """Echo-loop check: nothing reacting to the set may write any of it
    back."""
    board = autonaApp.ready(1)

    seen, unsubscribe = _heldUpdateSpy("score.1")
    try:
        apply_set(board, dict(_SET))
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.1"], (
        f"loading a set produced {len(seen)} save cycles instead of 1: "
        "something reacted to the state change by writing it again")


def test_reloading_the_same_set_publishes_nothing(autonaApp):
    """The second load is all no-ops, so it must not mark the board dirty."""
    from src.core.StateManager import StateManager

    autonaApp.ready(1)
    apply_set(1, dict(_SET))
    StateManager.CommitHeld("score.1")
    autonaApp.settle()
    assert StateManager.HasPendingChanges("score.1") is False

    apply_set(1, dict(_SET))
    autonaApp.settle()

    assert StateManager.HasPendingChanges("score.1") is False, (
        "re-applying an identical set lit the 反映 button for edits nobody "
        "made")


def test_a_set_loaded_onto_a_swapped_board_lands_on_the_right_side(autonaApp):
    from src.core.Actions import AutonaActions

    autonaApp.ready(2)
    AutonaActions.instance.SwapTeams(2)   # what the board's SWAP runs
    autonaApp.settle()
    assert autonaApp.get("score.2.teamsSwapped") is True

    # Two players a side: the team-name half of a set only applies to a
    # multi-player entrant, as it always has.
    apply_set(2, dict(
        _SET,
        entrants=[[{"gamerTag": "A"}, {"gamerTag": "B"}],
                  [{"gamerTag": "C"}, {"gamerTag": "D"}]],
        p1_name="Alpha", p2_name="Bravo", overwrite=True))
    autonaApp.settle()

    # The set's first entrant is shown on the right after a swap, so its
    # score, losers flag, team name and players all belong to state team 2.
    assert (_score(autonaApp, 1, board=2), _score(autonaApp, 2, board=2)) == (1, 2)
    assert autonaApp.get("score.2.team.2.losers") is False
    assert autonaApp.get("score.2.team.1.losers") is True
    assert autonaApp.get("score.2.team.2.teamName") == "Alpha"
    assert autonaApp.get("score.2.team.1.teamName") == "Bravo"
    assert _playerName(autonaApp, 2, board=2) == "A"
    assert _playerName(autonaApp, 1, board=2) == "C"


def test_a_loaded_team_name_reaches_the_state(autonaApp):
    board = autonaApp.ready(2)
    apply_set(board, {
        "entrants": [[{"gamerTag": "A"}, {"gamerTag": "B"}],
                     [{"gamerTag": "C"}, {"gamerTag": "D"}]],
        "p1_name": "Alpha", "p2_name": "Bravo", "overwrite": True,
    })
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == "Alpha"
    assert autonaApp.get("score.2.team.2.teamName") == "Bravo"


def test_following_a_set_records_which_set_it_is(autonaApp, tournament):
    from src.core.SetFollowService import SetFollowService
    from src.core.SetLoadService import SetLoadService

    # The board's `setFollow` / `setLoad` are these, by board number.
    autonaApp.ready(1)
    SetFollowService(1).LoadSet({"id": "999", "auto_update": "set"})
    autonaApp.settle()

    assert autonaApp.get("score.1.set_id") == "999"
    assert autonaApp.get("score.1.auto_update") == "set"
    assert SetLoadService(1).lastSetSelected == "999"


def test_cancelling_the_follow_forgets_the_set(autonaApp, tournament):
    from src.core.SetFollowService import SetFollowService
    from src.core.SetLoadService import SetLoadService

    autonaApp.ready(1)
    SetFollowService(1).LoadSet({"id": "999", "auto_update": "set"})
    autonaApp.settle()

    # What the board's cancel button runs (StopAutonaUpdate).
    SetFollowService(1).StopFollowing(forget=True)
    autonaApp.settle()

    assert autonaApp.get("score.1.set_id") is None
    assert autonaApp.get("score.1.auto_update") is None
    assert SetLoadService(1).lastSetSelected is None


def test_a_stale_async_refresh_is_dropped(autonaApp):
    """An in-flight fetch answering after the operator moved on."""
    from src.core.SetFollowService import SetFollowService
    from src.core.SetLoadService import SetLoadService

    autonaApp.ready(1)
    SetLoadService(1).lastSetSelected = "999"
    apply_set(1, {"round_name": "Winners Final"})
    autonaApp.settle()

    SetFollowService(1).UpdateSetData({"id": "111",
                                   "round_name": "Losers Round 1",
                                   "entrants": []})
    autonaApp.settle()

    assert autonaApp.get("score.1.match") == "Winners Final"


def test_a_loaded_losers_flag_reaches_the_player_names(autonaApp):
    """The " [L]" suffix the thumbnail generator renders.

    The Qt checkbox was only rendered by a set load (_SyncSetFields set it
    with its signals blocked, because it was the render side and could not
    write), so the names could not come from its `toggled`:
    SetLoadService.ApplyLosers writes the flag and its team's names together
    (ADR-005 S2b). Before that the board carried the flag into each card by
    hand (_ApplyLosersToPlayers), and without it the state said "losers"
    while the exported name did not.
    """
    board = autonaApp.ready(1)
    apply_set(board, {
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team1losers": False, "team2losers": True, "overwrite": True,
    })
    autonaApp.settle()

    assert autonaApp.get("score.1.team.2.mergedName") is None  # team-level
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "BBB [L]"
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "AAA"
    # The carriers of the same fact must agree.
    assert autonaApp.get("score.1.team.2.losers") is True
    assert autonaApp.get("score.1.team.1.losers") is False


def test_a_loaded_losers_flag_follows_the_swap(autonaApp):
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    AutonaActions.instance.SwapTeams(1)   # what the board's SWAP runs
    autonaApp.settle()

    apply_set(1, {
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team1losers": True, "team2losers": False, "overwrite": True,
    })
    autonaApp.settle()

    # The set's first entrant is on the right after a swap, so its flag and
    # its player both belong to state team 2.
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "AAA [L]"
    assert autonaApp.get("score.1.team.2.losers") is True
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "BBB"
    assert autonaApp.get("score.1.team.1.losers") is False


def test_clearing_the_score_still_clears_the_losers_names(autonaApp):
    """ClearScore takes the flags and their " [L]" together
    (AutonaActions.ClearScore, ADR-005 S2d: SetTeamLosers, the same core
    half a set load uses through SetLoadService.ApplyLosers; the Qt board
    drove its checkboxes to it until then)."""
    board = autonaApp.ready(1)
    apply_set(board, {
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team2losers": True, "overwrite": True,
    })
    autonaApp.settle()
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "BBB [L]"

    _actions().ClearScore(board)
    autonaApp.settle()

    assert autonaApp.get("score.1.team.2.losers") is False
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "BBB"


def test_swapping_teams_carries_the_losers_names_across(autonaApp):
    """The flags and the names cross over together in the state
    (AutonaActions.SwapTeams): a name's " [L]" came from its team's flag,
    and the flag goes with it, so nothing is re-derived."""
    board = autonaApp.ready(1)
    apply_set(board, {
        "entrants": [[{"gamerTag": "AAA"}], [{"gamerTag": "BBB"}]],
        "team1losers": False, "team2losers": True, "overwrite": True,
    })
    autonaApp.settle()

    _actions().SwapTeams(board)
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.losers") is True
    assert autonaApp.get("score.1.team.2.losers") is False
    assert autonaApp.get("score.1.team.1.player.1.mergedName") == "BBB [L]"
    assert autonaApp.get("score.1.team.2.player.1.mergedName") == "AAA"


# ── clearing (ADR-005 S2d) ───────────────────────────────────────────────
#
# The clears are core operations (AutonaActions.ClearScore / ResetMatch /
# ClearPlayers / ClearBoard): one save cycle each, where the board used to
# drive its widgets back to empty -- a cycle per player card, per score
# spinbox and per losers box. What is checked here: one cycle, the set
# loaded last forgotten, the statistics fetched once, and the Web's clears
# on the board made at launch.

_CLEARS = {
    # The board host's clear (the board's CommandClearAll until ADR-005
    # S4x): what reset_players runs, without the Web around it.
    "ClearPlayers": lambda app, n: clear_players(n),
    # すべての入力値をリセット (the Qt board's button until ADR-005 S4ac-3).
    "reset_all": lambda app, n: app.web().reset_all(n),
    "reset_players": lambda app, n: app.web().reset_players(n),
    "clear_all": lambda app, n: app.web().clear_all(n),
}


@pytest.mark.parametrize("clear", ["ClearPlayers", "reset_all"])
def test_a_clear_is_one_save_cycle(autonaApp, clear):
    board = autonaApp.ready(2)
    _setPlayerName(board, 1, "LEFT")
    _setPlayerName(board, 2, "RIGHT")
    _actions().AdjustScore(board, 1, 2)
    autonaApp.settle()

    seen, unsubscribe = _heldUpdateSpy("score.2")
    try:
        _CLEARS[clear](autonaApp, board)
        autonaApp.settle()
    finally:
        unsubscribe()

    assert seen == ["score.2"]
    assert not _playerName(autonaApp, 1, board=2)
    assert not _playerName(autonaApp, 2, board=2)
    expectedScore = 0 if clear == "reset_all" else 2
    assert _score(autonaApp, 1, board=2) == expectedScore


def test_a_board_reset_takes_the_team_names_and_keeps_the_counts(autonaApp):
    """すべての入力値をリセット: values only. The Web's clear_all keeps the
    names instead (unless it shrinks the roster to one player) and resets
    the counts -- the two differ, as they always have (ADR-005 S2
    decision 3)."""
    board = autonaApp.ready(2)
    _actions().SetPlayersPerTeam(board, 2)
    autonaApp.settle()
    for team in (1, 2):
        _typeTeamName(board, team, f"Team {team}")
    autonaApp.settle()

    autonaApp.web().reset_all(board)
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == ""
    assert autonaApp.get("score.2.team.2.teamName") == ""
    assert len(autonaApp.get("score.2.team.1.player")) == 2
    assert len(autonaApp.get("score.2.team.2.player")) == 2


@pytest.mark.parametrize("clear", sorted(_CLEARS))
@pytest.mark.parametrize("ids, fetches", [
    (([101, 1101], [303, 3303]), 1),    # players gone from both sides: once
    ((None, None), 0),                  # no ids went, nothing to fetch for
])
def test_a_clear_refetches_the_stats_once(autonaApp, clear, ids, fetches):
    """A card used to ask for the statistics when its own Clear() dropped
    its id (one fetch per card); the clear asks once now (the board did
    until ADR-005 S3e-5, the operation since: AutonaActions.ClearPlayers),
    and the upset factor is recalculated by the operation."""
    from src.core.PlayerService import PlayerService

    board = autonaApp.ready(1)
    for team, value in zip((1, 2), ids):
        player = PlayerService(f"score.1.team.{team}.player.1")
        player.SetPlayerId(value)
        player.SetText("name", "SOMEONE")
    autonaApp.settle()

    with _statsFetches(board) as counts:
        _CLEARS[clear](autonaApp, board)
        autonaApp.settle()

    assert autonaApp.get("score.1.team.1.player.1.id") is None
    assert counts == dict(dict.fromkeys(_STATS_SIGNALS, fetches),
                          UpsetFactorCalculation=0)


@pytest.mark.parametrize("clear", sorted(_CLEARS))
def test_the_same_set_loads_again_after_a_clear(autonaApp, clear,
                                                tournament):
    """A set load skips the set the board already follows; a clear
    forgets it (it lives in SetLoadService now, not in the state). The
    fetch is the set follow's (SetFollowService.fetcher, ADR-005 S3e-7)."""
    from src.core.SetFollowService import SetFollowService

    board = autonaApp.ready(1)
    follow = SetFollowService(board)
    actions = autonaApp.web()
    with patch.object(SetFollowService.fetcher, "Match") as fetched:
        try:
            follow.LoadSet({"id": "999", "auto_update": "set"})
            follow.LoadSet({"id": "999", "auto_update": "set"})
            autonaApp.settle()
            assert fetched.call_count == 1
            assert actions.get_set(1) == "999"

            _CLEARS[clear](autonaApp, board)
            autonaApp.settle()
            assert actions.get_set(1) == "0"

            follow.LoadSet({"id": "999", "auto_update": "set"})
            autonaApp.settle()
            assert fetched.call_count == 2
        finally:
            follow.StopFollowing(forget=True)
            autonaApp.settle()


_WEB_CLEARS = {
    "reset_match": lambda actions: actions.reset_match(2),
    "reset_players": lambda actions: actions.reset_players(2),
    "clear_all": lambda actions: actions.clear_all(2),
    "set_route": lambda actions: actions.set_route(2, match="Grand Final"),
}


@pytest.mark.parametrize("route", sorted(_WEB_CLEARS))
def test_a_web_clear_reaches_the_launched_board(autonaApp, route):
    """The board is made, and held, at launch (ADR-005 S4ac-1): the route's
    write lands on it and waits for 反映. Until S4ac-3 the route built a
    board that had no widgets first, as GetScoreboard() did before S2d (an
    unbuilt board was not held, and building it seeded its subtree
    afresh)."""
    from src.core.StateManager import StateManager

    autonaApp.ready(2)
    actions = autonaApp.web()
    _WEB_CLEARS[route](actions)
    autonaApp.settle()

    state = autonaApp.get("score.2")
    if route in ("reset_match", "clear_all"):
        for team in ("1", "2"):
            assert state["team"][team]["score"] == 0
            assert state["team"][team]["losers"] is False
        assert (state["match"], state["phase"], state["pool"]) \
            == ("", "", "")
        assert state["best_of"] == 0
    if route in ("reset_players", "clear_all"):
        for team in ("1", "2"):
            for slot in state["team"][team]["player"].values():
                # A fresh slot has no text keys; the clear adds none.
                assert not slot.get("name") and not slot.get("team")
                assert slot["seed"] == 0
                assert slot["online_avatar"] is None
    if route == "clear_all":
        assert len(state["team"]["1"]["player"]) == 1
    if route == "set_route":
        assert autonaApp.get("score.2.match") == "Grand Final"
        assert StateManager.HasPendingChanges("score.2") is True
        assert StateManager.GetCommitted("score.2.match") \
            != "Grand Final"


def test_reading_the_set_loaded_last_builds_nothing(autonaApp):
    """get_set reads what core keeps by board (SetLoadService, ADR-005
    S3e-7), in both shells since ADR-005 S4x: the desktop's built the board
    first, for a value no board holds."""
    from src.core.Actions import AutonaActions
    from src.core.SetLoadService import SetLoadService
    from src.core.StateManager import StateManager

    autonaApp.dropAllBoards()
    StateManager.Unset("score.2")
    autonaApp.settle()
    actions = autonaApp.web()
    try:
        with WriteRecorder() as recorder:
            assert actions.get_set(2) == "0"
            SetLoadService(2).lastSetSelected = "777"
            assert actions.get_set(2) == "777"
            # The crew board loads no set.
            assert actions.get_set(3) == "0"
            autonaApp.settle()
        assert recorder.sets == []
        assert not any(AutonaActions.instance.BoardEnsured(number)
                       for number in AutonaActions.BOARDS)
    finally:
        SetLoadService(2).lastSetSelected = None
        autonaApp.settle()


def test_a_web_team_name_is_one_save_cycle_with_its_crest(autonaApp):
    """set_route's teamName= writes the literal column, as the operator
    typing into it: the name, and the crest that follows it."""
    # A crest for "Alpha" only (SetLoadService.PublishTeamLogo looks it up
    # by the lower-cased name).
    crest = autonaApp.dir / "user_data" / "team_logo" / "alpha.png"
    crest.parent.mkdir(parents=True, exist_ok=True)
    crest.write_bytes(b"")
    try:
        autonaApp.ready(2)

        seen, unsubscribe = _heldUpdateSpy("score.2")
        try:
            autonaApp.web().set_route(2, team="1", teamName="Alpha")
            autonaApp.settle()
            # The same name again writes nothing.
            autonaApp.web().set_route(2, team="1", teamName="Alpha")
            autonaApp.settle()
        finally:
            unsubscribe()

        assert seen == ["score.2"]
        assert autonaApp.get("score.2.team.1.teamName") == "Alpha"
        assert autonaApp.get("score.2.team.1.logo") \
            == "./user_data/team_logo/alpha.png"

        # A name without a crest takes the crest away, in the same cycle.
        seen, unsubscribe = _heldUpdateSpy("score.2")
        try:
            autonaApp.web().set_route(2, team="1", teamName="Bravo")
            autonaApp.settle()
        finally:
            unsubscribe()
        assert seen == ["score.2"]
        assert autonaApp.get("score.2.team.1.teamName") == "Bravo"
        assert autonaApp.get("score.2.team.1.logo") is None
    finally:
        crest.unlink(missing_ok=True)


def test_a_clear_keeps_the_city(autonaApp):
    """Known gap, recorded rather than fixed (ADR-005 S2 decision 5): no
    player card's Clear() ever emptied the city, so the clear does not
    either. A player loaded from start.gg leaves theirs behind."""
    from src.core.PlayerService import PlayerService

    autonaApp.ready(1)
    # The slot's card's `player`.
    player = PlayerService("score.1.team.1.player.1")
    player.SetCity("Shibuya")
    player.SetText("name", "SOMEONE")
    autonaApp.settle()

    clear_players(1)
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.player.1.name") == ""
    assert autonaApp.get("score.1.team.1.player.1.city") == "Shibuya"


# ── the roster (headless core Step 5, instalment 2) ──────────────────────
#
# The Qt player card's inputs were wired to core/PlayerService by name
# (StateSchema.PLAYER_INPUT_KEYS) from Step 5 and went in ADR-005 S4ac-3;
# the service is tested in tests/test_player_service.py. What is left here:
# the roster's own size changes still take the state with them.


def test_shrinking_the_roster_takes_the_player_slots_with_it(autonaApp):
    board = autonaApp.ready(2)
    _actions().SetPlayersPerTeam(board, 3)
    autonaApp.settle()
    for team in (1, 2):
        for slot in (1, 2, 3):
            load_player(board, team, {"gamerTag": f"T{team}P{slot}"}, slot)
    autonaApp.settle()
    assert autonaApp.get("score.2.team.1.player.3.name") == "T1P3"

    _actions().SetPlayersPerTeam(board, 1)
    autonaApp.settle()

    for team in (1, 2):
        remaining = autonaApp.get(f"score.2.team.{team}.player") or {}
        assert sorted(remaining) == ["1"], (
            f"team {team} kept {sorted(remaining)} after shrinking to one "
            "player; the surplus stays on the overlay")


def test_a_roster_that_grows_with_a_loaded_losers_flag_is_flagged(autonaApp):
    """The gap the player instalment closed, closed for good by S2b.

    A set that arrives with the flag set *and* more entrants than the board
    currently shows used to leave the new cards at losers=False -- they were
    connected to the LOSERS checkbox only from the moment they were created
    -- so their mergedName lost the " [L]" the thumbnail generator renders.
    The player instalment had the new cards read the flag as they were built
    (_AdoptTeamLosers); since ADR-005 S2b there is no per-card flag at all:
    a player's name is published with the flag it finds in the state, which
    SetLoadService.ApplyLosers has written before the entrants are poured in.
    """
    from src.core.Actions import AutonaActions

    board = autonaApp.ready(2)
    assert AutonaActions.RosterCount(board, 1) == 1

    apply_set(board, {
        "entrants": [[{"gamerTag": "A1"}, {"gamerTag": "A2"}],
                     [{"gamerTag": "B1"}, {"gamerTag": "B2"}]],
        "team1losers": True, "team2losers": False, "overwrite": True,
    })
    autonaApp.settle()

    assert AutonaActions.RosterCount(board, 1) == 2
    assert [autonaApp.get(f"score.2.team.1.player.{m}.mergedName")
            for m in (1, 2)] == ["A1 [L]", "A2 [L]"]
    # ...and the team that did not come out of losers stays unflagged.
    assert [autonaApp.get(f"score.2.team.2.player.{m}.mergedName")
            for m in (1, 2)] == ["B1", "B2"]

