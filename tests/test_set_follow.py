"""Set loading and following as core's (ADR-005 S3e-7): what changed on
purpose, what the headless shell can do now, and the late answers.

The desktop's behaviour is the golden's (tests/test_set_follow_flow.py,
recorded before the move). This file is what the golden cannot hold:

- the headless shell loads, follows and polls a set, and POST /score
  applies, through its own board host (they answered 503);
- `pull_stream` follows the streamer's channel (it emitted a signal nothing
  was connected to: ADR-005 S3e 論点 3); without a channel the legacy route
  answers "OK" and /api/v1 NOT_READY. Pulled again while the board follows
  the channel -- the Web's auto-update repeats it -- it keeps the set shown
  and its score unless the channel moved on; the first pull keeps the set
  shown when it is the channel's; a pull while the board follows a station
  or another channel follows this one;
- lookups answering in the other order leave the newer one's set;
- a set whose fetch failed is not recorded as loaded: the next pull, the
  set chosen again or the next tick loads it;
- with no tournament loaded a set load leaves the board as it is (legacy
  "OK", /api/v1 NOT_READY);
- the sets queued behind a followed station are kept in memory, never
  written;
- a one-player POST /score naming a slot the state does not have -- "0" and
  negative numbers included -- writes nothing (it wrote team 2 / the last
  player: the S3e-4 bug);
- an answer that arrives after the board moved on is dropped: a station
  lookup after a set loaded by hand, the cancel button or the board being
  forgotten, an update after the set was loaded again -- checked where the
  board's operations run, so a web load landing right after the check
  replaces the lookup's set;
- the board operations run where the shell runs them (the headless host's
  lock), and nothing waits for the GUI thread with the state's lock held;
- the poll's clock runs under a bare QCoreApplication, and stops for good
  when the app quits.

The provider is faked at its own interface and its jobs run one at a time
when the test says so (the golden's _FakeProvider and its `provider`
fixture's stepped task executor); nothing touches the network.
"""
import copy
import json
import threading
import time
from unittest.mock import patch

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaWebOperations import BoardHost
from tests.test_set_follow_flow import (  # noqa: F401 (fixtures)
    SETS, STREAM, _node, playerDB, provider, settings)

VIDEOGAME = 1386


# ── helpers ──────────────────────────────────────────────────────────────

def _state():
    from src.core.StateManager import StateManager
    return StateManager


def _jobs():
    """The stepped task executor the `provider` fixture runs the provider's
    jobs on (ADR-005 S3f-2; the adapter's thread pool until then)."""
    from src.core.TaskExecutor import TaskExecutor
    return TaskExecutor.instance


def _drain(app):
    """The main thread's queue, then the provider's oldest job, and again,
    until neither has anything left."""
    while True:
        for _ in range(3):
            app.settle()
        if not _jobs().run_next():
            break


def _runLast():
    """Run the newest queued provider job, ahead of the older ones (an
    answer overtaking another), on a thread of its own."""
    assert _jobs().run_next(newest=True), "no provider job is queued"


def _settle(app):
    for _ in range(3):
        app.settle()


def _failOnce(provider, monkeypatch, failing, failure):
    """Set `failing`'s next fetch fails: Start.gg raises ("error"), or its
    requests fail and it answers what it has, with no set ("no set")."""
    real = provider.GetMatch
    left = [1]

    def GetMatch(setId, progress_callback=None, cancel_event=None):
        if str(setId) == str(failing) and left[0]:
            left[0] = 0
            provider._record("GetMatch", setId)
            if failure == "error":
                raise RuntimeError("start.gg did not answer")
            return {"entrants": []}
        return real(setId, progress_callback, cancel_event)

    monkeypatch.setattr(provider, "GetMatch", GetMatch)


def _calls(fake, name):
    return [call[1:] for call in fake.calls if call[0] == name]


def _acks(ws, name):
    """What the server answered on event `name` (the state broadcasts that
    may come with it left out)."""
    return [event["args"] for event in ws.get_received()
            if event["name"] == name]


def _tick(board):
    from src.core.SetFollowService import SetFollowService
    SetFollowService.fetcher._timers[board].timeout.emit()


def _goHeadless(app, monkeypatch):
    """No board built and the headless host: what main.py --headless runs,
    with the player slots it seeds for boards 1/2."""
    from src.core import StateSchema as schema
    from src.core.PlayerService import SeedEmptyRoster
    app.dropAllBoards()
    with _state().SaveBlock():
        for number in (1, 2, 3):
            schema.SeedBoard(number)
        for number in (1, 2):
            SeedEmptyRoster(number)
    app.settle()
    host = BoardHost()
    monkeypatch.setattr(WebOps, "boardHost", host)
    return host


def _server(monkeypatch, headless):
    """The legacy routes, /api/v1 and Socket.IO as the app registers them;
    `headless` takes the desktop's shell operations away (ADR-005 S3e-9:
    the headless shell registers none)."""
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app

    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    if headless:
        monkeypatch.setattr(WebOps, "openSetSelection", None)
        monkeypatch.setattr(WebOps, "renderThumbnail", None)
    monkeypatch.setattr(WebServer, "socketio", socketio)
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=client)
    ws.get_received()
    return client, ws


@pytest.fixture
def headless(autonaApp, monkeypatch, settings, provider, playerDB):
    host = _goHeadless(autonaApp, monkeypatch)
    client, ws = _server(monkeypatch, headless=True)
    try:
        yield host, client, ws
    finally:
        for number in (1, 2, 3):
            _state().Unset(f"score.{number}")
        autonaApp.settle()


@pytest.fixture
def desktop(autonaApp, monkeypatch, settings, provider, playerDB):
    board = autonaApp.board(1)
    autonaApp.settle()
    client, ws = _server(monkeypatch, headless=False)
    return board, client, ws


def _v1(client, path, body=None):
    response = client.post(path, json=body or {})
    payload = response.get_json(silent=True) or {}
    return response.status_code, (payload.get("error") or {}).get("code")


def _players(n):
    """Both teams' player slots of board `n` (a POST /score resets the
    scores besides)."""
    teams = _state().Get(f"score.{n}.team") or {}
    return copy.deepcopy({team: (teams[team] or {}).get("player")
                          for team in teams})


def _onWorker(fn):
    """`fn()` on another thread, the GUI thread pumped meanwhile (a desktop
    operation from a request thread marshals onto it)."""
    from qtpy.QtWidgets import QApplication

    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as error:  # handed back to the test
            box["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 30
    while thread.is_alive():
        assert time.monotonic() < deadline, "the operation did not finish"
        QApplication.processEvents()
        time.sleep(0.002)
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ── the headless shell loads, follows and applies ───────────────────────

def test_load_set_works_headless(autonaApp, headless, provider):
    """The legacy route, the Socket.IO event and /api/v1's load-set: the
    set is fetched and applied on the headless host (no 反映 there), the
    same set again is not, a clear forgets it."""
    host, client, ws = headless

    assert client.get("/scoreboard1-load-set?set=1001").get_data(
        as_text=True) == "OK"
    _drain(autonaApp)
    assert autonaApp.get("score.1.set_id") == "1001"
    assert autonaApp.get("score.1.auto_update") == "set"
    assert autonaApp.get("score.1.team.1.player.1.name") == "ALPHA"
    assert autonaApp.get("score.1.team.2.player.1.name") == "BRAVO"
    assert autonaApp.get("score.1.match") == "Round 1001"
    assert not _state().HasPendingChanges("score.1")
    assert client.get("/scoreboard1-get-set").get_data(as_text=True) == "1001"

    ws.emit("load_set", json.dumps({"scoreboardNumber": "1", "set": "1001"}))
    assert _acks(ws, "load_set") == [["OK"]]
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [["1001"]]

    assert _v1(client, "/api/v1/scoreboards/1:load-set",
               {"setId": 1002, "noMains": True}) == (200, None)
    _drain(autonaApp)
    assert autonaApp.get("score.1.set_id") == "1002"
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"

    assert client.get("/scoreboard1-reset-players").get_data(
        as_text=True) == "OK"
    assert client.get("/scoreboard1-get-set").get_data(as_text=True) == "0"
    client.get("/scoreboard1-load-set?set=1002")
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [["1001"], ["1002"], ["1002"]]
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"

    # Boards other than 1/2 load no set (500 / 400), as on the desktop.
    assert client.get("/scoreboard3-load-set?set=1001").status_code == 500
    assert _v1(client, "/api/v1/scoreboards/3:load-set",
               {"setId": 1}) == (400, "INVALID_ARGUMENT")


def test_a_team_set_grows_the_headless_roster(autonaApp, headless):
    host, client, _ = headless

    client.get("/scoreboard2-load-set?set=4001")
    _drain(autonaApp)

    assert autonaApp.get("score.2.team.1.player.2.name") == "BRAVO"
    assert autonaApp.get("score.2.team.2.player.2.name") == "DELTA"
    assert autonaApp.get("score.2.team.1.teamName") == "Team Left"


def test_headless_polling_follows_the_set_and_the_stream(
        autonaApp, headless, settings, provider):
    """Polling on: the shell's clock ticks under its event loop (driven by
    the test here), the update lands, the stream follow re-reads the
    channel's set, and the cancel stops it all."""
    from src.core.SetFollowService import SetFollowService

    host, client, _ = headless
    settings["general.disable_autoupdate"] = False
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)

    client.get("/scoreboard1-load-set?set=1001")
    _drain(autonaApp)
    timer = SetFollowService.fetcher._timers[1]
    assert timer.isActive()
    assert SetFollowService(1).Following() == ("set", None)
    assert autonaApp.get("score.1.team.1.score") == 0

    _tick(1)
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [["1001"], ["1001"]]
    assert autonaApp.get("score.1.team.1.score") == 1   # the update

    assert client.get("/scoreboard1-pull-stream").get_data(
        as_text=True) == "OK"
    _drain(autonaApp)
    assert _calls(provider, "GetStreamMatchId") == [[STREAM]]
    assert autonaApp.get("score.1.set_id") == 2001
    assert autonaApp.get("score.1.auto_update") == "stream"
    assert autonaApp.get("score.1.station") == STREAM
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    assert SetFollowService(1).Following() == ("stream", STREAM)

    # A clear while following the stream: the next tick reads the channel
    # again and loads its set again.
    client.get("/scoreboard1-reset-players")
    assert autonaApp.get("score.1.team.1.player.1.name") == ""
    _tick(1)
    _drain(autonaApp)
    assert _calls(provider, "GetStreamMatchId") == [[STREAM], [STREAM]]
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"

    SetFollowService(1).StopFollowing(forget=True)
    autonaApp.settle()
    assert not timer.isActive()
    assert autonaApp.get("score.1.set_id") is None
    before = len(provider.calls)
    _tick(1)
    _drain(autonaApp)
    assert len(provider.calls) == before


@pytest.mark.parametrize("answer", ["the set", "error", "no set"])
def test_a_tick_fetches_nothing_while_the_last_update_is_unanswered(
        autonaApp, headless, settings, provider, monkeypatch, answer):
    """With start.gg slow, a tick fetches nothing while the poll's previous
    update has not answered (ADR-005 S3f-2 review: each tick used to queue
    its fetches behind the ones still waiting, and the queue only grew).
    Whatever that update answers -- the set, an error, no set -- the next
    tick fetches again."""
    host, client, _ = headless
    settings["general.disable_autoupdate"] = False
    client.get("/scoreboard1-load-set?set=1001")
    _drain(autonaApp)
    if answer != "the set":
        _failOnce(provider, monkeypatch, "1001", answer)

    _tick(1)
    _settle(autonaApp)
    queued = _jobs().queued()
    assert queued >= 2   # the update and the stream queue
    _tick(1)
    _tick(1)
    _settle(autonaApp)
    assert _jobs().queued() == queued

    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [["1001"], ["1001"]]
    _tick(1)
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [["1001"], ["1001"], ["1001"]]
    assert autonaApp.get("score.1.set_id") == "1001"


def test_without_the_setting_a_load_does_not_poll(autonaApp, headless,
                                                  monkeypatch):
    """A shell that never wrote general.disable_autoupdate does not poll:
    there is no web operation to stop it. (The desktop's board forces it
    on.)"""
    from src.core.SetFollowService import SetFollowService
    from src.core.SettingsManager import SettingsManager

    host, client, _ = headless
    patched = SettingsManager.Get
    monkeypatch.setattr(SettingsManager, "Get", lambda key, default=None: (
        default if key == "general.disable_autoupdate"
        else patched(key, default)))

    assert SetFollowService.Polling() is False
    client.get("/scoreboard1-load-set?set=1001")
    _drain(autonaApp)

    assert SetFollowService(1).Following() is None
    timer = SetFollowService.fetcher._timers.get(1)
    assert timer is None or not timer.isActive()
    assert autonaApp.get("score.1.set_id") == "1001"


def test_post_score_works_headless(autonaApp, headless, playerDB):
    host, client, ws = headless
    _, saves = playerDB

    response = client.post("/score", data=json.dumps({
        "team1score": 2, "round_name": "Winners Final", "overwrite": True,
        "bestOf": 5, "entrants": copy.deepcopy(SETS[1001])}))
    assert response.get_data(as_text=True) == "OK"
    autonaApp.settle()
    assert autonaApp.get("score.1.match") == "Winners Final"
    assert autonaApp.get("score.1.team.1.score") == 2
    assert autonaApp.get("score.1.team.2.player.1.name") == "BRAVO"

    client.post("/score", data=json.dumps({
        "team": "2", "player": "1", "data": {
            "gamerTag": "SAVED", "prefix": "DB", "savePlayerToDb": True}}))
    autonaApp.settle()
    assert autonaApp.get("score.1.team.2.player.1.name") == "SAVED"
    assert len(saves) == 1

    ws.emit("score", json.dumps({"scoreboard": "3",
                                 "round_name": "Crew Final"}))
    assert _acks(ws, "score") == [["OK"]]
    assert autonaApp.get("score.3.match") == "Crew Final"
    assert client.post("/score", data=json.dumps(
        {"scoreboard": "4"})).status_code == 500


# ── pull_stream (ADR-005 S3e 論点 3) ─────────────────────────────────────

@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_pull_stream_without_a_channel(request, autonaApp, settings,
                                       provider, shell):
    _, client, ws = request.getfixturevalue(shell)
    settings["twitch_username"] = ""
    before = list(provider.calls)

    assert client.get("/scoreboard1-pull-stream").get_data(
        as_text=True) == "OK"
    ws.emit("pull_stream", json.dumps({"scoreboardNumber": "1"}))
    assert _acks(ws, "pull_stream") == [["OK"]]
    assert _v1(client, "/api/v1/scoreboards/1:pull-stream") == (
        409, "NOT_READY")
    _drain(autonaApp)

    assert provider.calls == before
    assert autonaApp.get("score.1.set_id") is None
    assert autonaApp.get("score.1.auto_update") is None


def test_pull_stream_follows_the_channel_on_the_desktop(autonaApp, desktop,
                                                        provider):
    board, client, _ = desktop
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)

    assert client.get("/scoreboard1-pull-stream").get_data(
        as_text=True) == "OK"
    _drain(autonaApp)

    assert _calls(provider, "GetStreamMatchId") == [[STREAM]]
    assert _calls(provider, "GetMatch") == [[2001]]
    assert autonaApp.get("score.1.set_id") == 2001
    assert autonaApp.get("score.1.auto_update") == "stream"
    assert autonaApp.get("score.1.station") == STREAM
    # Held for 反映 like any set the board loads.
    assert _state().HasPendingChanges("score.1")
    assert board.setLoad.lastSetSelected == 2001

    provider.streams[STREAM] = _node(1002, (3, 4), state=1)
    assert _v1(client, "/api/v1/scoreboards/1:pull-stream") == (200, None)
    _drain(autonaApp)
    assert autonaApp.get("score.1.set_id") == 1002
    assert _v1(client, "/api/v1/scoreboards/3:pull-stream") == (
        400, "INVALID_ARGUMENT")
    assert client.get("/scoreboard3-pull-stream").status_code == 500


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_pulling_the_followed_stream_again_keeps_its_set(
        request, autonaApp, provider, shell):
    """The Web's auto-update switch pulls the stream every few seconds.
    Once the board follows the channel, a pull only looks its set up again:
    the same set is neither cleared nor fetched again -- the score the
    operator kept stays, and there is nothing new to apply -- even while
    the lookups are slow to answer. Another set is loaded."""
    from src.core.Actions import AutonaActions
    from src.core.SetFollowService import SetFollowService

    _, client, _ = request.getfixturevalue(shell)
    state = _state()
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)

    assert _v1(client, "/api/v1/scoreboards/1:pull-stream") == (200, None)
    _drain(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    AutonaActions.instance.SetScore(1, 1, 2)
    assert _v1(client, "/api/v1/scoreboards/1:apply") == (200, None)
    assert not state.HasPendingChanges("score.1")

    writes = []
    realSet = state.Set

    def spySet(key, value=None, *args, **kwargs):
        writes.append(key)
        return realSet(key, value, *args, **kwargs)

    with patch.object(state, "Set", spySet):
        for _ in range(3):
            assert client.get("/scoreboard1-pull-stream").get_data(
                as_text=True) == "OK"
            for _ in range(3):
                autonaApp.settle()
        # Three lookups not answered yet: the board shows the set all along.
        assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
        assert autonaApp.get("score.1.team.1.score") == 2
        assert _v1(client, "/api/v1/scoreboards/1:pull-stream") == (
            200, None)
        _drain(autonaApp)

    assert _calls(provider, "GetStreamMatchId") == [[STREAM]] * 5
    assert _calls(provider, "GetMatch") == [[2001]]
    assert [key for key in writes if key.startswith("score.1.")] == []
    assert autonaApp.get("score.1.team.1.score") == 2
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    assert autonaApp.get("score.1.set_id") == 2001
    assert not state.HasPendingChanges("score.1")
    assert SetFollowService(1).Station() == {"type": "stream",
                                             "identifier": STREAM}

    # The channel moved on: its set is loaded.
    provider.streams[STREAM] = _node(1002, (3, 4), state=1)
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [[2001], [1002]]
    assert autonaApp.get("score.1.set_id") == 1002
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"


def test_a_lookup_asked_for_before_the_board_is_forgotten_is_dropped(
        autonaApp, headless, provider):
    """ForgetBoard (the harness rebuilding a board) moves the numbers on:
    a lookup still in flight from before does not load its set."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)

    client.get("/scoreboard1-pull-stream")
    SetFollowService.ForgetBoard()
    _drain(autonaApp)

    assert _calls(provider, "GetStreamMatchId") == [[STREAM]]
    assert _calls(provider, "GetMatch") == []
    assert autonaApp.get("score.1.set_id") is None


@pytest.mark.parametrize("first", ["pull", "tick"])
def test_lookups_answering_in_the_other_order_keep_the_newer_set(
        autonaApp, headless, provider, settings, monkeypatch, first):
    """Two lookups of the channel in flight -- a pull's or a tick's, then a
    pull's -- answer in the other order: the newer one's set is loaded, and
    the older one, answering last with what the channel showed before,
    does not load its set over it (it did: the score and players went back
    to the previous set)."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    monkeypatch.setattr(SetFollowService, "POLL_MS", 3600 * 1000)
    settings["general.disable_autoupdate"] = first == "pull"
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"

    if first == "tick":
        _tick(1)
        _settle(autonaApp)
        # The tick's update and stream queue: its lookup is left.
        assert _jobs().run_next()
        _settle(autonaApp)
        assert _jobs().run_next()
        _settle(autonaApp)
    else:
        client.get("/scoreboard1-pull-stream")
        _settle(autonaApp)
    assert _jobs().queued() == 1      # the older lookup
    client.get("/scoreboard1-pull-stream")
    _settle(autonaApp)
    assert _jobs().queued() == 2      # and the newer one

    # The newer one answers first: the channel moved on to 1002.
    provider.streams[STREAM] = _node(1002, (3, 4), state=1)
    _runLast()
    _settle(autonaApp)
    _runLast()                          # 1002's fetch
    _settle(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"
    # Then the older one, with what the channel showed when it was asked.
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    _drain(autonaApp)

    assert _calls(provider, "GetStreamMatchId") == [[STREAM]] * 3
    assert _calls(provider, "GetMatch") == (
        [[2001], [2001], [1002]] if first == "tick" else [[2001], [1002]])
    assert autonaApp.get("score.1.set_id") == 1002
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"
    assert autonaApp.get("score.1.team.2.player.1.name") == "DELTA"
    SetFollowService(1).StopFollowing(forget=True)


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_the_first_pull_keeps_the_set_shown_when_it_is_the_channels(
        request, autonaApp, provider, shell):
    """The auto-update switch turned on over a set loaded by hand (from the
    Web: its id is a str, the lookup's an int): the board follows the
    channel from its first pull, and is not cleared for the set it shows
    -- nothing is written, the score stays. The channel's next set loads."""
    from src.core.Actions import AutonaActions
    from src.core.SetFollowService import SetFollowService

    _, client, _ = request.getfixturevalue(shell)
    state = _state()
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    client.get("/scoreboard1-load-set?set=2001")
    _drain(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    AutonaActions.instance.SetScore(1, 1, 2)
    assert _v1(client, "/api/v1/scoreboards/1:apply") == (200, None)

    writes = []
    realSet = state.Set

    def spySet(key, value=None, *args, **kwargs):
        writes.append(key)
        return realSet(key, value, *args, **kwargs)

    with patch.object(state, "Set", spySet):
        assert client.get("/scoreboard1-pull-stream").get_data(
            as_text=True) == "OK"
        _drain(autonaApp)

    assert _calls(provider, "GetStreamMatchId") == [[STREAM]]
    assert _calls(provider, "GetMatch") == [["2001"]]
    assert [key for key in writes if key.startswith("score.1.")] == []
    assert autonaApp.get("score.1.team.1.score") == 2
    assert not state.HasPendingChanges("score.1")
    assert SetFollowService(1).Station() == {"type": "stream",
                                             "identifier": STREAM}

    provider.streams[STREAM] = _node(1002, (3, 4), state=1)
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [["2001"], [1002]]
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"


def test_after_the_cancel_the_first_pull_loads_the_set_again(
        autonaApp, headless, provider):
    """What the first pull cannot keep (the ledger's owner check): the
    cancel button forgets the set shown -- `score.{n}.set_id` included, so
    choosing it again reloads it -- and nothing says the board still shows
    it. The next pull loads the channel's set again, the same set too."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)
    SetFollowService(1).StopFollowing(forget=True)
    autonaApp.settle()
    assert autonaApp.get("score.1.set_id") is None

    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [[2001], [2001]]
    assert autonaApp.get("score.1.set_id") == 2001


@pytest.mark.parametrize("streamSet", [2001, 3001])
def test_a_pull_while_following_a_station_follows_the_stream(
        autonaApp, desktop, provider, settings, monkeypatch, streamSet):
    """The board follows a station (polling); the Web pulls the stream: the
    board follows the stream from then on -- loading its set, or keeping
    the station's when the stream shows that one -- and the station's
    lookup the last tick left in flight loads nothing."""
    from src.core.SetFollowService import SetFollowService

    board, client, _ = desktop
    monkeypatch.setattr(SetFollowService, "POLL_MS", 3600 * 1000)
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    provider.streams[STREAM] = _node(streamSet, (5, 6), state=1)
    board.signals.StationSelected.emit(dict(_STATION))
    _drain(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") == "FOXTROT"
    # The station's next set is up: what its lookup would load.
    provider.stations["77"] = [_node(3002, (3, 4))]

    _tick(1)                            # the station's lookup, in flight
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)

    assert _calls(provider, "GetStationMatchsId") == [[77], [77]]
    assert SetFollowService(1).Station() == {"type": "stream",
                                             "identifier": STREAM}
    assert SetFollowService(1).Following() == ("stream", STREAM)
    # The load, the tick's update, and the stream's set if it is another.
    if streamSet == 3001:
        assert _calls(provider, "GetMatch") == [[3001], [3001]]
        assert autonaApp.get("score.1.team.1.player.1.name") == "FOXTROT"
    else:
        assert _calls(provider, "GetMatch") == [[3001], [3001], [2001]]
        assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    assert autonaApp.get("score.1.set_id") == streamSet
    board.StopAutonaUpdate(clear_variables=True)


def test_a_pull_while_following_another_channel_follows_this_one(
        autonaApp, headless, provider):
    """The board follows another channel (the station window's "stream"
    for another streamer): a pull follows the streamer's channel."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    provider.streams["another"] = _node(1002, (3, 4), state=1)
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    SetFollowService(1).FollowStation({"type": "stream",
                                       "identifier": "another"})
    _drain(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"

    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)

    assert _calls(provider, "GetStreamMatchId") == [["another"], [STREAM]]
    assert _calls(provider, "GetMatch") == [[1002], [2001]]
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    assert SetFollowService(1).Station() == {"type": "stream",
                                             "identifier": STREAM}


def test_a_new_channel_in_the_settings_is_followed(autonaApp, headless,
                                                   provider, settings):
    """`twitch_username` changed while the board follows the old channel:
    the next pull follows the new one and loads its set; a lookup of the
    old one still in flight loads nothing."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    provider.streams["another"] = _node(1002, (3, 4), state=1)
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)
    client.get("/scoreboard1-pull-stream")      # the old channel, in flight
    _settle(autonaApp)

    settings["twitch_username"] = "another"
    provider.streams[STREAM] = _node(1003, (7, 8), state=1)
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)

    assert _calls(provider, "GetStreamMatchId") == [
        [STREAM], [STREAM], ["another"]]
    assert _calls(provider, "GetMatch") == [[2001], [1002]]
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"
    assert SetFollowService(1).Station() == {"type": "stream",
                                             "identifier": "another"}


# ── a set whose fetch failed ────────────────────────────────────────────

@pytest.mark.parametrize("failure", ["error", "no set"])
@pytest.mark.parametrize("recover", ["pull", "chosen again", "tick"])
@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_set_whose_fetch_failed_loads_again(
        request, autonaApp, provider, settings, monkeypatch, shell, recover,
        failure):
    """The channel moved on to a set whose fetch fails (Start.gg raised, or
    answered no set): the board was cleared for it and shows none of it,
    and the set is not recorded as loaded -- the next pull, the set chosen
    again or the poll's next tick loads it. (It was recorded: the next
    pull and the same set chosen again skipped it as the set shown, an
    update found nothing to update, and the board stayed empty.)"""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = request.getfixturevalue(shell)
    monkeypatch.setattr(SetFollowService, "POLL_MS", 3600 * 1000)
    settings["general.disable_autoupdate"] = recover != "tick"
    provider.streams[STREAM] = _node(1002, (3, 4), state=1)
    _failOnce(provider, monkeypatch, 1002, failure)

    if recover == "pull":
        client.get("/scoreboard1-pull-stream")
    else:
        client.get("/scoreboard1-load-set?set=1002")
    _drain(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") in ("", None)
    assert str(autonaApp.get("score.1.set_id")) == "1002"
    assert client.get("/scoreboard1-get-set").get_data(as_text=True) == "0"

    if recover == "pull":
        client.get("/scoreboard1-pull-stream")
    elif recover == "chosen again":
        client.get("/scoreboard1-load-set?set=1002")
    else:
        _tick(1)
    _drain(autonaApp)

    assert [str(call[0]) for call in _calls(provider, "GetMatch")] == [
        "1002", "1002"]
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"
    assert autonaApp.get("score.1.team.2.player.1.name") == "DELTA"
    assert client.get("/scoreboard1-get-set").get_data(
        as_text=True) == "1002"
    SetFollowService(1).StopFollowing(forget=True)
    autonaApp.settle()


# ── no tournament loaded ────────────────────────────────────────────────

@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_with_no_tournament_a_load_leaves_the_board_as_it_is(
        request, autonaApp, provider, monkeypatch, shell):
    """Nothing could be fetched, so nothing is cleared (the desktop cleared
    the players and the score all the same): the legacy routes and
    Socket.IO answer "OK" as they always did, /api/v1 NOT_READY."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.SetFollowService import SetFollowService

    _, client, ws = request.getfixturevalue(shell)
    client.get("/scoreboard1-load-set?set=1001")
    _drain(autonaApp)
    assert autonaApp.get("score.1.team.1.player.1.name") == "ALPHA"
    players = _players(1)
    score = autonaApp.get("score.1.team.1.score")
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        None)
    calls = len(provider.calls)

    assert client.get("/scoreboard1-load-set?set=1002").get_data(
        as_text=True) == "OK"
    ws.emit("load_set", json.dumps({"scoreboardNumber": "1", "set": "1002"}))
    assert _acks(ws, "load_set") == [["OK"]]
    assert client.get("/scoreboard1-pull-stream").get_data(
        as_text=True) == "OK"
    assert _v1(client, "/api/v1/scoreboards/1:load-set",
               {"setId": 1002}) == (409, "NOT_READY")
    assert _v1(client, "/api/v1/scoreboards/1:pull-stream") == (
        409, "NOT_READY")
    _drain(autonaApp)

    assert len(provider.calls) == calls
    assert _players(1) == players
    assert autonaApp.get("score.1.team.1.score") == score
    assert autonaApp.get("score.1.set_id") == "1001"
    assert client.get("/scoreboard1-get-set").get_data(as_text=True) == "1001"
    assert SetFollowService(1).Station() is None


# ── a one-player POST /score names a slot the state has, or nothing ─────

@pytest.mark.parametrize("team,player", [
    ("0", "1"), ("1", "0"), ("-1", "1"), ("1", "-1"), ("3", "1"),
    ("1", "2"), ("x", "1")])
@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_one_player_payload_for_no_slot_writes_no_player(
        request, autonaApp, shell, team, player):
    _, client, _ = request.getfixturevalue(shell)
    before = _players(1)

    response = client.post("/score", data=json.dumps({
        "team": team, "player": player, "data": {"gamerTag": "NOPE"}}))
    autonaApp.settle()

    assert response.get_data(as_text=True) == "OK"
    assert _players(1) == before


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_one_player_payload_for_a_slot_writes_it(request, autonaApp,
                                                   shell):
    _, client, _ = request.getfixturevalue(shell)
    before = _players(1)

    client.post("/score", data=json.dumps({
        "team": "2", "player": "1", "data": {"gamerTag": "THERE"}}))
    autonaApp.settle()

    assert autonaApp.get("score.1.team.2.player.1.name") == "THERE"
    assert _players(1)["1"] == before["1"]


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_board_with_no_set_fields_is_not_asked_for(request, autonaApp,
                                                     shell):
    """A one-player payload or a set field for a board other than 1/2/3 is
    refused (503) before anything is written, and the board is not asked
    for: the desktop's host logged an unknown scoreboard first. A team name
    for it writes nothing, "OK" as before."""
    from loguru import logger

    _, client, ws = request.getfixturevalue(shell)
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="ERROR")
    try:
        response = client.post("/scoreboard4-update-team-1-1",
                               json={"gamerTag": "NOPE"})
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "UNAVAILABLE"
        ack = ws.emit("update_team", json.dumps({
            "scoreboardNumber": "4", "team": "1", "player": "1",
            "gamerTag": "NOPE"}), callback=True)
        assert ack["error"]["code"] == "UNAVAILABLE"
        assert client.get("/scoreboard4-set?match=Final").status_code == 503
        assert client.get("/scoreboard4-set?teamName=Them&team=1").get_data(
            as_text=True) == "OK"
        autonaApp.settle()
    finally:
        logger.remove(sink)

    assert not [line for line in lines if "Scoreboard Manager" in line], lines
    assert _state().Get("score.4") is None


# ── late answers ─────────────────────────────────────────────────────────

_STATION = {"type": "station", "id": 77, "identifier": "4", "stream": ""}


def test_a_station_lookup_after_a_set_loaded_by_hand_is_dropped(
        autonaApp, desktop, provider):
    """The station window's station, then -- before its lookup answers --
    a set from the set window: the set stays, the station's is not loaded
    over it (it used to be)."""
    board, _, _ = desktop
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]

    board.signals.StationSelected.emit(dict(_STATION))
    board.NewSetSelected({"id": 1002, "auto_update": "set"})
    _drain(autonaApp)

    assert _calls(provider, "GetStationMatchsId") == [[77]]
    assert _calls(provider, "GetMatch") == [[1002]]
    assert _calls(provider, "GetFutureMatchesList") == []
    assert autonaApp.get("score.1.set_id") == 1002
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"


def test_a_station_lookup_after_the_cancel_button_is_dropped(
        autonaApp, desktop, provider):
    board, _, _ = desktop
    provider.stations["77"] = [_node(3001)]

    board.signals.StationSelected.emit(dict(_STATION))
    board.timerCancelBt.click()
    _drain(autonaApp)

    assert _calls(provider, "GetMatch") == []
    assert autonaApp.get("score.1.set_id") is None
    assert board.setLoad.lastSetSelected is None


def test_the_station_queue_is_kept_in_memory_only(autonaApp, desktop,
                                                  provider):
    """The sets queued behind the station are fetched and kept for the
    follow; they are not written (the recorded tree lost them, and
    `score.{n}.station_queue` stays the `{}` it always was). A queue
    answering after the board stopped following the station is dropped,
    and so is the set its lookup loaded. Following the station again after
    the cancel fetches its queue again."""
    from src.core.SetFollowService import SetFollowService

    board, _, _ = desktop
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    provider.stations["78"] = [_node(1001), _node(1003)]
    queued = {"1": {"id": 3002, "p1_name": "P3002", "p2_name": "Q"}}

    board.signals.StationSelected.emit(dict(_STATION))
    _drain(autonaApp)
    assert SetFollowService._queues[1]["queued"] == queued
    assert autonaApp.get("score.1.station_queue") == {}
    assert autonaApp.get("score.1.set_id") == 3001

    # Station 78's lookup answers (asking for its queue and its set), then
    # the operator cancels before either answers.
    board.signals.StationSelected.emit(
        dict(_STATION, id=78, identifier="5"))
    for _ in range(3):
        autonaApp.settle()
    assert _jobs().run_next()            # station 78's lookup
    for _ in range(3):
        autonaApp.settle()
    board.timerCancelBt.click()
    _drain(autonaApp)

    assert _calls(provider, "GetFutureMatchesList") == [[[3002]], [[1003]]]
    assert _calls(provider, "GetMatch") == [[3001], [1001]]
    assert 1 not in SetFollowService._queues
    assert autonaApp.get("score.1.station_queue") == {}
    assert autonaApp.get("score.1.set_id") is None
    # Station 78's set cleared the board before the cancel; its players
    # never arrived.
    assert autonaApp.get("score.1.team.1.player.1.name") in ("", None)
    assert board.setLoad.lastSetSelected is None

    # Station 77 again, its queue as it was: asked for again.
    board.signals.StationSelected.emit(dict(_STATION))
    _drain(autonaApp)
    assert _calls(provider, "GetFutureMatchesList") == [
        [[3002]], [[1003]], [[3002]]]
    assert SetFollowService._queues[1]["queued"] == queued
    board.StopAutonaUpdate(clear_variables=True)


def test_a_queue_answering_after_apply_leaves_nothing_to_apply(
        autonaApp, desktop, provider):
    """The station's queue is the follow's own: its answer arriving after
    the operator applied the station's set does not light 反映 again."""
    from src.core.SetFollowService import SetFollowService

    board, client, _ = desktop
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    answers = []

    def held(setIds, onResult):
        answers.append(onResult)
        return True

    with patch.object(SetFollowService.fetcher, "FutureSets", held):
        board.signals.StationSelected.emit(dict(_STATION))
        _drain(autonaApp)
    assert autonaApp.get("score.1.set_id") == 3001
    assert _v1(client, "/api/v1/scoreboards/1:apply") == (200, None)
    assert not _state().HasPendingChanges("score.1")

    answers[0]({"1": {"id": 3002}})     # the queue, late, on the main thread
    autonaApp.settle()

    assert not _state().HasPendingChanges("score.1")
    assert autonaApp.get("score.1.station_queue") == {}
    assert SetFollowService._queues[1]["queued"] == {"1": {"id": 3002}}
    board.StopAutonaUpdate(clear_variables=True)


def test_a_queue_answering_after_the_follow_stopped_is_dropped(
        autonaApp, desktop, provider, settings, monkeypatch):
    """The station's queue is kept from the fetch the board waits for: an
    older fetch answering after a newer one is dropped, and so is one
    answering after the poll stopped -- the station kept (it was written
    back, the station being the same)."""
    from src.core.SetFollowService import SetFollowService

    board, _, _ = desktop
    monkeypatch.setattr(SetFollowService, "POLL_MS", 3600 * 1000)
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    answers = []

    def held(setIds, onResult):
        answers.append(onResult)
        return True

    with patch.object(SetFollowService.fetcher, "FutureSets", held):
        board.signals.StationSelected.emit(dict(_STATION))
        _drain(autonaApp)
        _tick(1)                # its queue not there yet: asked for again
        _drain(autonaApp)
        assert len(answers) == 2
        answers[1]({"1": {"id": 3002, "p1_name": "NEWER"}})
        answers[0]({"1": {"id": 3002, "p1_name": "OLDER"}})
        assert SetFollowService._queues[1]["queued"] == {
            "1": {"id": 3002, "p1_name": "NEWER"}}

        # The queue moved: asked for again, then the poll stops.
        provider.stations["77"] = [_node(3001), _node(1003)]
        _tick(1)
        _drain(autonaApp)
        assert len(answers) == 3
        board.StopAutonaUpdate(clear_variables=False)
        autonaApp.settle()
        assert SetFollowService(1).Station() is not None
        answers[2]({"1": {"id": 1003}})

    assert 1 not in SetFollowService._queues
    assert autonaApp.get("score.1.station_queue") == {}
    board.StopAutonaUpdate(clear_variables=True)


def test_a_load_that_loads_nothing_leaves_a_lookup_to_answer(
        autonaApp, headless, provider, monkeypatch):
    """Only a load that loads decides what the board follows: the set shown
    chosen again, or a load with no tournament to fetch it from, leaves the
    lookup in flight to load the channel's next set (both dropped it)."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    adapter = AutonaTournamentDataProvider.instance
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    client.get("/scoreboard1-pull-stream")
    _drain(autonaApp)

    # The set shown, chosen again (as the set window hands it: an int).
    provider.streams[STREAM] = _node(1002, (3, 4), state=1)
    client.get("/scoreboard1-pull-stream")
    SetFollowService(1).LoadSet({"id": 2001, "auto_update": "set"})
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [[2001], [1002]]
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"

    # A set chosen with no tournament loaded.
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    client.get("/scoreboard1-pull-stream")
    _settle(autonaApp)
    monkeypatch.setattr(adapter, "provider", None)
    client.get("/scoreboard1-load-set?set=1003")
    monkeypatch.setattr(adapter, "provider", provider)
    _drain(autonaApp)
    assert _calls(provider, "GetMatch") == [[2001], [1002], [2001]]
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    SetFollowService(1).StopFollowing(forget=True)


def test_an_update_answering_after_the_set_was_loaded_again_is_dropped(
        autonaApp, desktop, settings, provider):
    """Polling: a tick's update is in flight when the operator clears the
    board and loads the same set again. The update must not land on the
    fresh load (the set is the one the board shows, so only the request
    order tells them apart)."""
    from src.core.SetFollowService import SetFollowService

    board, client, _ = desktop
    settings["general.disable_autoupdate"] = False
    board.NewSetSelected({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)

    # What lands: the update is dropped where it is checked, in the host.
    applied = []
    real = SetFollowService.ApplySetPayload

    def spy(self, data):
        applied.append((data.get("id"), data.get("overwrite")))
        return real(self, data)

    with patch.object(SetFollowService, "ApplySetPayload", spy):
        _tick(1)                                  # an update, in flight
        client.get("/scoreboard1-reset-players")
        board.NewSetSelected({"id": 1001, "auto_update": "set"})
        _drain(autonaApp)

    assert _calls(provider, "GetMatch") == [[1001], [1001], [1001]]
    assert applied == [(1001, True)]
    # The fake counts its fetches: the reload's is the third.
    assert autonaApp.get("score.1.team.1.score") == 2
    assert autonaApp.get("score.1.team.1.player.1.name") == "ALPHA"
    board.StopAutonaUpdate(clear_variables=True)


def test_answers_apply_on_the_headless_host(autonaApp, headless,
                                            monkeypatch):
    """A fetched set is applied where the headless shell runs its board
    operations -- one at a time with the web's (the host's lock)."""
    from src.core.SetFollowService import SetFollowService

    host, client, _ = headless
    held = []
    real = SetFollowService.ApplySetPayload

    def spy(self, data):
        held.append(host._lock._is_owned())
        return real(self, data)

    monkeypatch.setattr(SetFollowService, "ApplySetPayload", spy)
    client.get("/scoreboard1-load-set?set=1001")
    client.post("/score", data=json.dumps({"round_name": "Grand Final"}))
    _drain(autonaApp)

    assert held == [True, True]


def test_a_web_load_right_after_a_lookup_is_checked_replaces_it(
        autonaApp, headless, provider):
    """The stream lookup's answer is checked where the board's operations
    run, and loads its set there: a web load-set arriving on a request
    thread right after the check waits for that load, then replaces it.
    (Checked outside, the web's set loaded first and the stream's landed
    over it.)"""
    from src.core.SetFollowService import SetFollowService

    host, client, _ = headless
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    client.get("/scoreboard1-pull-stream")
    for _ in range(3):
        autonaApp.settle()
    assert _jobs().run_next()            # the lookup; its answer is queued

    real = SetFollowService._TakeLookup
    web = {}

    def checked(self, number):
        taken = real(self, number)
        if "thread" not in web:
            web["thread"] = threading.Thread(
                target=lambda: web.setdefault(
                    "answer", WebOps.load_set(1, "1002")))
            web["thread"].start()
            web["thread"].join(0.5)
            web["waited"] = web["thread"].is_alive()
        return taken

    with patch.object(SetFollowService, "_TakeLookup", checked):
        for _ in range(3):
            autonaApp.settle()          # the answer: checked, 2001 loaded
    web["thread"].join(10)
    assert not web["thread"].is_alive()
    _drain(autonaApp)

    assert web["waited"] is True
    assert web["answer"] == "OK"
    assert _calls(provider, "GetMatch") == [[2001], ["1002"]]
    assert autonaApp.get("score.1.set_id") == "1002"
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"


def test_no_thread_waits_for_the_gui_with_the_state_lock(
        autonaApp, desktop, settings, provider):
    """The desktop's operations from a request thread marshal onto the GUI
    thread (DesktopBoardHost.Run) and wait for it; none of that happens
    with StateManager.lock held (agent-pitfalls #17) -- on the request
    thread, or on the GUI thread, where the answers and the clock's tick
    run (a run_sync there runs at once)."""
    from src.Helpers import AutonaQtHelper

    board, client, _ = desktop
    settings["general.disable_autoupdate"] = False
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    executor = AutonaQtHelper.gui_executor
    runSync = executor.run_sync
    waits = []

    def counted(fn):
        waits.append((threading.current_thread() is threading.main_thread(),
                      _state().lock._is_owned()))
        return runSync(fn)

    with patch.object(executor, "run_sync", counted):
        _onWorker(lambda: WebOps.load_set(1, "1001"))
        _drain(autonaApp)
        _onWorker(lambda: WebOps.post_score(json.dumps({
            "team": "1", "player": "1", "data": {"gamerTag": "W"}})))
        _onWorker(lambda: WebOps.pull_stream_set(1))
        _drain(autonaApp)
        _tick(1)
        _drain(autonaApp)
    assert autonaApp.get("score.1.set_id") == 2001
    assert autonaApp.get("score.1.team.1.player.1.name") == "STREAMER"
    board.StopAutonaUpdate(clear_variables=True)

    assert [wait for wait in waits if not wait[0]], (
        "no operation marshalled onto the GUI thread", waits)
    assert not [wait for wait in waits if wait[1]], waits


# ── the clock under a bare QCoreApplication ─────────────────────────────

_CLOCK_SCRIPT = r'''
from qtpy.QtCore import QCoreApplication, QTimer

app = QCoreApplication([])

from src.bootstrap import bootstrap_core
bootstrap_core()

from src import AutonaWebOperations
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.core import StateSchema as schema
from src.core.PlayerService import SeedEmptyRoster
from src.core.SetFollowService import SetFollowService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager

calls = []

class Provider:
    name = "StartGG"

    def GetMatch(self, setId, progress_callback=None, cancel_event=None):
        calls.append(["GetMatch", setId])
        return {"id": setId, "round_name": "Round %d" % len(calls),
                "entrants": [[{"gamerTag": "ALPHA"}], [{"gamerTag": "BRAVO"}]],
                "overwrite": True}

    def GetStreamQueue(self, progress_callback=None, cancel_event=None):
        calls.append(["GetStreamQueue"])
        return {}

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

realGet = SettingsManager.Get
SettingsManager.Get = staticmethod(lambda key, default=None: (
    False if key == "general.disable_autoupdate" else realGet(key, default)))
AutonaTournamentDataProvider.instance.provider = Provider()
with StateManager.SaveBlock():
    for number in (1, 2, 3):
        schema.SeedBoard(number)
    SeedEmptyRoster(1)
SetFollowService.POLL_MS = 100

answer = AutonaWebOperations.load_set(1, "1001")
QTimer.singleShot(1500, app.quit)
app.exec()
# app.quit() emitted aboutToQuit: the clock stopped, and a poll asked for
# while the app winds down does not start it again.
fetcher = SetFollowService.fetcher
stoppedOnQuit = not fetcher._timers[1].isActive()
fetcher.Poll(1)
stillStopped = not fetcher._timers[1].isActive()
SetFollowService(1).StopFollowing()
from src.core.TaskExecutor import TaskExecutor
TaskExecutor.instance.drain(5)
report({
    "answer": answer,
    "fetches": len([c for c in calls if c[0] == "GetMatch"]),
    "set_id": StateManager.Get("score.1.set_id"),
    "name": StateManager.Get("score.1.team.1.player.1.name"),
    "match": StateManager.Get("score.1.match"),
    "stoppedOnQuit": stoppedOnQuit,
    "stillStopped": stillStopped,
})
'''


def test_the_poll_runs_under_a_bare_qcoreapplication(tmp_path):
    """The headless shell's set load and poll: the fetches answer and the
    clock ticks under QCoreApplication's event loop, and stops when the app
    quits."""
    from tests.test_bootstrap_split import _PRELUDE, _probe

    result = _probe(tmp_path, _PRELUDE + _CLOCK_SCRIPT)

    assert result["answer"] == "OK"
    assert result["set_id"] == "1001"
    assert result["name"] == "ALPHA"
    # The load's fetch and at least a few ticks' updates.
    assert result["fetches"] >= 4, result
    assert result["match"].startswith("Round "), result
    assert result["stoppedOnQuit"] is True
    assert result["stillStopped"] is True
