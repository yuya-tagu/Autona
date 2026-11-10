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
  when the app quits;
- v1's follow, stop and read (ADR-005 S4v) do what the station window and the
  timer row's cancel button do, on both shells; the read answers from a thread
  with no event dispatcher, where QTimer.remainingTime() crashes the process.

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
    with the boards it seeds."""
    from src import headless
    app.dropAllBoards()
    with _state().SaveBlock():
        headless._SeedBoards()
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
        monkeypatch.setattr(WebOps, "startImageRender", None)
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
    """Board 1 made as the desktop has it (held), with no widget (ADR-005
    S4ac-2), and the desktop's server: (1, client, ws)."""
    autonaApp.ready(1)
    client, ws = _server(monkeypatch, headless=False)
    return 1, client, ws


def _setFollow():
    """Board 1's `setFollow`, what the set and station windows and the timer
    row's cancel button call, with no widget: the service by number."""
    from src.core.SetFollowService import SetFollowService
    return SetFollowService(1)


def _lastSet():
    """Board 1's `setLoad.lastSetSelected`, kept by number."""
    from src.core.SetLoadService import SetLoadService
    return SetLoadService(1).lastSetSelected


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


def test_a_point_from_another_thread_is_not_followed_twice_by_a_set(
        autonaApp, headless, settings):
    """ApplySetPayload reads the score it follows from under the lock, so a
    +1 on another thread waits for it. With nothing in the way, the +1 lands
    between the read and the set's score: its row stays, and the set's follow
    adds its whole move from the score it read on top. Three rows for a score
    of 2."""
    from src.core.Actions import AutonaActions
    from src.core.SetFollowService import SetFollowService

    settings["general.disable_individual_game_tracker"] = False
    actions = AutonaActions.instance
    actions.SetBestOf(1, 5)
    tracker = actions.GameTracker(1)
    realScores = tracker.Scores
    other = threading.Thread(
        target=lambda: actions.AdjustScore(1, 1, 1), daemon=True)

    def scores():
        value = realScores()
        if other.ident is None:
            other.start()
            other.join(0.3)
        return value

    with patch.object(tracker, "Scores", side_effect=scores):
        SetFollowService(1).ApplySetPayload(
            {"bestOf": 5, "team1score": 2, "team2score": 0, "entrants": []})
        other.join(5)
    autonaApp.settle()

    assert not other.is_alive()
    assert autonaApp.get("score.1.team.1.score") == 3
    assert tracker.WonStages(0) == [0, 1, 2]


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


def test_a_v1_load_follows_the_set_as_the_set_window_does(
        autonaApp, desktop, settings, provider):
    """ADR-005 S4u's check. v1's :load-set and the set window reach the same
    load, auto_update "set": the same board and the same poll. What differs is
    the id's type alone (the window hands an int, the web a str), and
    SetFollowService compares them raw, as it always did: choosing in one the
    set loaded by the other loads it again."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = desktop
    settings["general.disable_autoupdate"] = False

    def followed():
        return (SetFollowService(1).Following(),
                SetFollowService.fetcher._timers[1].isActive(),
                autonaApp.get("score.1.auto_update"))

    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)
    assert followed() == (("set", None), True, "set")
    byWindow = copy.deepcopy(autonaApp.get("score.1"))

    autonaApp.ready(1)   # what it followed goes with it
    autonaApp.settle()
    provider._fetched.clear()    # the fake answers a point more each fetch
    assert SetFollowService(1).Following() is None
    assert _v1(client, "/api/v1/scoreboards/1:load-set",
               {"setId": 1001}) == (200, None)
    _drain(autonaApp)
    assert followed() == (("set", None), True, "set")
    byRoute = copy.deepcopy(autonaApp.get("score.1"))

    assert (byWindow.pop("set_id"), byRoute.pop("set_id")) == (1001, "1001")
    assert byRoute == byWindow

    # The same id, in the same type, again: nothing is fetched.
    fetched = len(_calls(provider, "GetMatch"))
    assert _v1(client, "/api/v1/scoreboards/1:load-set",
               {"setId": 1001}) == (200, None)
    _drain(autonaApp)
    assert len(_calls(provider, "GetMatch")) == fetched

    # The same set by the window's int: loaded again, cleared first.
    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
    _drain(autonaApp)
    assert len(_calls(provider, "GetMatch")) == fetched + 1
    assert autonaApp.get("score.1.set_id") == 1001


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
    # A board no shell has: 503 since the ADR-005 S4x review (500 until
    # then, raised as the crew board's sets are).
    response = client.post("/score", data=json.dumps({"scoreboard": "4"}))
    assert (response.status_code, response.get_json()["error"]) == (
        503, {"code": "UNAVAILABLE",
              "message": "scoreboard 4 does not exist"})


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
    _, client, _ = desktop
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
    assert _lastSet() == 2001

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

    _, client, _ = desktop
    monkeypatch.setattr(SetFollowService, "POLL_MS", 3600 * 1000)
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    provider.streams[STREAM] = _node(streamSet, (5, 6), state=1)
    _setFollow().FollowStation(dict(_STATION))
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
    _setFollow().StopFollowing(forget=True)


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
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]

    _setFollow().FollowStation(dict(_STATION))
    _setFollow().LoadSet({"id": 1002, "auto_update": "set"})
    _drain(autonaApp)

    assert _calls(provider, "GetStationMatchsId") == [[77]]
    assert _calls(provider, "GetMatch") == [[1002]]
    assert autonaApp.get("score.1.set_id") == 1002
    assert autonaApp.get("score.1.team.1.player.1.name") == "CHARLIE"


def test_a_station_lookup_after_the_cancel_button_is_dropped(
        autonaApp, desktop, provider):
    provider.stations["77"] = [_node(3001)]

    _setFollow().FollowStation(dict(_STATION))
    _setFollow().StopFollowing(forget=True)   # the cancel button
    _drain(autonaApp)

    assert _calls(provider, "GetMatch") == []
    assert autonaApp.get("score.1.set_id") is None
    assert _lastSet() is None


def test_following_a_station_fetches_its_current_set_only(
        autonaApp, desktop, provider, settings, monkeypatch):
    """Following a station looks its sets up and fetches the first one --
    on the follow and on every tick, the queue behind it moved or not. The
    sets queued behind it are not fetched (ADR-005 S3f-3): nothing showed
    them -- `score.{n}.station_queue` stays the `{}` it always was, the
    recorded tree's fetch never answered -- so they only added start.gg
    requests. Stopping the poll keeps the station."""
    from loguru import logger
    from src.core.SetFollowService import SetFollowService

    monkeypatch.setattr(SetFollowService, "POLL_MS", 3600 * 1000)
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    failed = []
    sink = logger.add(lambda message: failed.append(str(message)),
                      level="ERROR")
    try:
        _setFollow().FollowStation(dict(_STATION))
        _drain(autonaApp)
        provider.stations["77"] = [_node(3001), _node(1003)]
        _tick(1)
        _drain(autonaApp)
        _setFollow().StopFollowing(forget=False)
        autonaApp.settle()
    finally:
        logger.remove(sink)

    assert failed == []
    assert _calls(provider, "GetStationMatchsId") == [[77], [77]]
    assert _calls(provider, "GetMatch") == [[3001], [3001]]
    assert {call[0] for call in provider.calls} == {
        "GetStationMatchsId", "GetMatch", "GetStreamQueue", "GetLastSets",
        "GetPlayerHistoryStandings", "GetRecentSets"}
    assert autonaApp.get("score.1.station_queue") == {}
    assert autonaApp.get("score.1.set_id") == 3001
    assert SetFollowService(1).Station() is not None
    _setFollow().StopFollowing(forget=True)


def test_a_station_set_answering_after_the_cancel_is_dropped(
        autonaApp, desktop, provider):
    """Another station's lookup answers -- its set cleared the board and
    is asked for -- then the operator cancels before the set answers: the
    set is dropped, and nothing is followed."""
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    provider.stations["78"] = [_node(1001), _node(1003)]

    _setFollow().FollowStation(dict(_STATION))
    _drain(autonaApp)
    assert autonaApp.get("score.1.set_id") == 3001

    _setFollow().FollowStation(
        dict(_STATION, id=78, identifier="5"))
    for _ in range(3):
        autonaApp.settle()
    assert _jobs().run_next()            # station 78's lookup
    for _ in range(3):
        autonaApp.settle()
    _setFollow().StopFollowing(forget=True)   # the cancel button
    _drain(autonaApp)

    assert _calls(provider, "GetMatch") == [[3001], [1001]]
    assert autonaApp.get("score.1.station_queue") == {}
    assert autonaApp.get("score.1.set_id") is None
    # Station 78's set cleared the board before the cancel; its players
    # never arrived.
    assert autonaApp.get("score.1.team.1.player.1.name") in ("", None)
    assert _lastSet() is None


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

    _, client, _ = desktop
    settings["general.disable_autoupdate"] = False
    _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
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
        _setFollow().LoadSet({"id": 1001, "auto_update": "set"})
        _drain(autonaApp)

    assert _calls(provider, "GetMatch") == [[1001], [1001], [1001]]
    assert applied == [(1001, True)]
    # The fake counts its fetches: the reload's is the third.
    assert autonaApp.get("score.1.team.1.score") == 2
    assert autonaApp.get("score.1.team.1.player.1.name") == "ALPHA"
    _setFollow().StopFollowing(forget=True)


def test_a_loaded_set_moves_the_rows_with_no_board_at_all(
        autonaApp, headless, settings):
    """The tracker on, on the headless shell: the rows are core's, and the
    score reaches them as a +1 does (ADR-005 S4t; a board's held cycle did it,
    so a shell with no board had no rows). On the rows the set's length
    leaves, by the net move from the score the set found -- a re-read set
    with the same score moves nothing, its recorded games stay."""
    from src.core.Actions import AutonaActions
    from src.core.SetFollowService import SetFollowService

    settings["general.disable_individual_game_tracker"] = False
    tracker = AutonaActions.instance.GameTracker(1)
    service = SetFollowService(1)

    service.ApplySetPayload({"bestOf": 5, "team1score": 2, "team2score": 1,
                             "entrants": []})
    autonaApp.settle()
    assert tracker.StageCount() == 5
    assert tracker.WonStages(0) == [0, 1]
    assert tracker.WonStages(1) == [2]

    tracker.SetStageResult(0, 2)        # reorder by hand: T2, T1, T2 ...
    tracker.SetStageResult(1, 1)
    tracker.SetStageResult(2, 1)
    tracker.PublishStageResults()
    service.ApplySetPayload({"reset_score": True, "team1score": 2,
                             "team2score": 1, "entrants": []})
    autonaApp.settle()
    assert tracker.WonStages(0) == [1, 2], (
        "the reset inside the set read as a clear, and re-awarding reordered "
        "the rows")
    assert tracker.WonStages(1) == [0]

    service.ApplySetPayload({"team1score": 3, "team2score": 0,
                             "entrants": []})
    autonaApp.settle()
    assert tracker.WonStages(0) == [1, 2, 3]
    assert tracker.WonStages(1) == []


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

    _, client, _ = desktop
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
    _setFollow().StopFollowing(forget=True)

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
from src.core.Actions import AutonaActions
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
        AutonaActions.instance.SeedFreshBoard(number)
SetFollowService.POLL_MS = 100

answer = AutonaWebOperations.load_set(1, "1001")
# v1's read, on a request thread (no event dispatcher), with the clock running.
readByRequest = {}
QTimer.singleShot(700, lambda: readByRequest.update(
    on_worker(lambda: AutonaWebOperations.get_follow(1))))
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
    "readByRequest": readByRequest,
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
    # On a thread with no event dispatcher: QTimer.remainingTime() crashes the
    # process there (the probe's exit code above), and the seconds would be
    # None if the clock's next tick was not kept.
    assert result["readByRequest"] == {
        "station": None,
        "poll": {"mode": "set", "identifier": None, "secondsLeft": 0},
        "pollingEnabled": True, "setId": "1001"}


# ── following through v1 (ADR-005 S4v) ──────────────────────────────────

# What GET /tournament/stations answers, whole: a page posts the row.
_STATION_ROW = {"type": "station", "id": 77, "identifier": 4, "stream": ""}
_STREAM_ROW = {"type": "stream", "id": 90, "identifier": STREAM, "stream": ""}


def _follow(client, n=1):
    response = client.get(f"/api/v1/scoreboards/{n}/follow")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["data"]


def _boardSnapshot(autonaApp):
    """The board and what it follows, the clock's reading left out."""
    from src.core.SetFollowService import SetFollowService

    snapshot = SetFollowService(1).Snapshot()
    if snapshot["poll"] is not None:
        assert isinstance(snapshot["poll"].pop("secondsLeft"), int)
    return copy.deepcopy(autonaApp.get("score.1")), snapshot


@pytest.mark.parametrize("row, lookup, asked, setId, identifier", [
    (_STATION_ROW, "GetStationMatchsId", [77], 3001, 4),
    (_STREAM_ROW, "GetStreamMatchId", [STREAM], 2001, STREAM)])
def test_a_v1_follow_does_what_the_station_window_does(
        autonaApp, desktop, settings, provider, row, lookup, asked, setId,
        identifier):
    """The window's row and v1's reach the same FollowStation: the same
    lookup, the same board, the same clock. A row of the list posted as it is
    follows."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = desktop
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)

    _setFollow().FollowStation(dict(row))
    _drain(autonaApp)
    byWindow = _boardSnapshot(autonaApp)
    assert _calls(provider, lookup) == [asked]
    assert byWindow[0]["set_id"] == setId
    assert byWindow[1] == {
        "station": {"type": row["type"], "id": row["id"],
                    "identifier": identifier},
        "poll": {"mode": row["type"], "identifier": identifier},
        "pollingEnabled": True, "setId": str(setId)}

    autonaApp.ready(1)   # what it followed goes with it
    autonaApp.settle()
    provider._fetched.clear()    # the fake answers a point more each fetch
    provider.calls.clear()
    assert SetFollowService(1).Station() is None
    assert _v1(client, "/api/v1/scoreboards/1:follow", row) == (200, None)
    _drain(autonaApp)

    assert _calls(provider, lookup) == [asked]
    assert SetFollowService.fetcher._timers[1].isActive()
    assert _boardSnapshot(autonaApp) == byWindow


def test_a_v1_stop_does_what_the_cancel_button_does(
        autonaApp, desktop, settings, provider):
    from src.core.SetFollowService import SetFollowService

    _, client, _ = desktop
    board = autonaApp.board(1)   # its cancel button is what v1 is held to
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001)]

    _setFollow().FollowStation(dict(_STATION_ROW))
    _drain(autonaApp)
    assert SetFollowService(1).Following() == ("station", 4)
    board.timerCancelBt.click()
    _drain(autonaApp)
    byButton = _boardSnapshot(autonaApp)
    assert byButton[1] == {"station": None, "poll": None,
                           "pollingEnabled": True, "setId": None}

    board = autonaApp.board(1)
    autonaApp.settle()
    provider._fetched.clear()
    _setFollow().FollowStation(dict(_STATION_ROW))
    _drain(autonaApp)
    assert SetFollowService(1).Following() == ("station", 4)
    assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
    _drain(autonaApp)

    assert _boardSnapshot(autonaApp) == byButton
    assert not SetFollowService.fetcher._timers[1].isActive()
    before = len(provider.calls)
    _tick(1)
    _drain(autonaApp)
    assert len(provider.calls) == before


def test_a_station_is_followed_and_stopped_headless(
        autonaApp, headless, settings, provider):
    _, client, _ = headless
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001)]
    assert _follow(client) == {"station": None, "poll": None,
                               "pollingEnabled": True, "setId": None}

    assert _v1(client, "/api/v1/scoreboards/1:follow",
               _STATION_ROW) == (200, None)
    _drain(autonaApp)

    assert autonaApp.get("score.1.set_id") == 3001
    assert autonaApp.get("score.1.auto_update") == "station"
    read = _follow(client)
    assert isinstance(read["poll"].pop("secondsLeft"), int)
    assert read == {
        "station": {"type": "station", "id": 77, "identifier": 4},
        "poll": {"mode": "station", "identifier": 4},
        "pollingEnabled": True, "setId": "3001"}

    assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
    _drain(autonaApp)
    assert _follow(client) == {"station": None, "poll": None,
                               "pollingEnabled": True, "setId": None}


def test_what_a_board_follows_comes_apart_in_three(
        autonaApp, headless, settings, provider):
    """The station stays with polling off (the desktop's default: a follow is
    one lookup) and through a set loaded by hand; what is polled is the poll."""
    _, client, _ = headless
    provider.stations["77"] = [_node(3001)]
    station = {"type": "station", "id": 77, "identifier": 4}

    settings["general.disable_autoupdate"] = True
    assert _v1(client, "/api/v1/scoreboards/1:follow",
               _STATION_ROW) == (200, None)
    _drain(autonaApp)
    assert _follow(client) == {"station": station, "poll": None,
                               "pollingEnabled": False, "setId": "3001"}
    assert _calls(provider, "GetStationMatchsId") == [[77]]

    settings["general.disable_autoupdate"] = False
    assert _v1(client, "/api/v1/scoreboards/1:load-set",
               {"setId": 1001}) == (200, None)
    _drain(autonaApp)
    read = _follow(client)
    assert read["poll"].pop("secondsLeft") >= 0
    assert read == {"station": station,
                    "poll": {"mode": "set", "identifier": None},
                    "pollingEnabled": True, "setId": "1001"}


def test_the_seconds_left_run_down_and_a_tick_brings_them_back(
        autonaApp, headless, settings, provider, monkeypatch):
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    monkeypatch.setattr(SetFollowService, "POLL_MS", 3600 * 1000)
    settings["general.disable_autoupdate"] = False
    client.get("/scoreboard1-load-set?set=1001")
    _drain(autonaApp)
    fetcher = SetFollowService.fetcher

    def left():
        return _follow(client)["poll"]["secondsLeft"]

    assert 3590 <= left() <= 3600
    fetcher._nextTicks[1] -= 1000       # a thousand seconds on
    assert 2590 <= left() <= 2600
    fetcher._nextTicks[1] -= 3000       # the tick is late: none left, not -400
    assert left() == 0
    _tick(1)
    _drain(autonaApp)
    assert 3590 <= left() <= 3600

    assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
    autonaApp.settle()
    assert fetcher.RemainingMs(1) is None
    assert _follow(client)["poll"] is None


def test_what_a_board_follows_changing_is_told(
        autonaApp, headless, settings, provider):
    """What the Socket.IO notice follows. A set's tick tells nothing: the
    seconds running down are the reader's. A station's tick looks its set up
    and starts the poll again, whose clock starts again: that is told."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001)]
    heard = []
    tell = heard.append
    SetFollowService.changes.subscribe(tell)
    try:
        assert _v1(client, "/api/v1/scoreboards/2:follow",
                   _STATION_ROW) == (200, None)
        assert heard == [2]             # told on the follow, before the answer
        _drain(autonaApp)
        assert heard == [2, 2]          # and when its load starts the poll

        heard.clear()
        _tick(2)
        _drain(autonaApp)
        assert heard == [2]

        heard.clear()
        assert _v1(client, "/api/v1/scoreboards/2:stop-follow") == (200, None)
        assert heard == [2]

        # Nothing left to stop: nothing changed, nothing told.
        heard.clear()
        assert _v1(client, "/api/v1/scoreboards/2:stop-follow") == (200, None)
        assert heard == []

        assert _v1(client, "/api/v1/scoreboards/2:load-set",
                   {"setId": 1001}) == (200, None)
        _drain(autonaApp)
        assert heard == [2]             # the load's poll

        heard.clear()
        _tick(2)
        _drain(autonaApp)
        assert heard == []
    finally:
        SetFollowService.changes.unsubscribe(tell)


def test_a_follow_is_told_when_it_changes_what_a_reader_has(
        autonaApp, headless, settings, provider):
    """Polling off, so that no poll's notice is in the way. The station chosen
    again looks its set up afresh: the set is forgotten with nothing written to
    the board, which would have told it. With no set to forget, it changes
    nothing."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    settings["general.disable_autoupdate"] = True
    provider.stations["77"] = [_node(3001)]
    heard = []

    def tell(board):
        heard.append((board, SetFollowService(board).Snapshot()["setId"]))

    SetFollowService.changes.subscribe(tell)
    try:
        assert _v1(client, "/api/v1/scoreboards/1:follow",
                   _STATION_ROW) == (200, None)
        assert heard == [(1, None)]     # another station
        _drain(autonaApp)
        assert _follow(client)["setId"] == "3001"

        heard.clear()
        assert _v1(client, "/api/v1/scoreboards/1:follow",
                   _STATION_ROW) == (200, None)
        assert heard == [(1, None)]     # the same one, its set forgotten

        provider.stations["77"] = []
        _drain(autonaApp)
        assert _follow(client)["setId"] is None

        heard.clear()
        assert _v1(client, "/api/v1/scoreboards/1:follow",
                   _STATION_ROW) == (200, None)
        _drain(autonaApp)
        assert heard == []              # the same one, nothing to forget

        assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
        assert heard == [(1, None)]     # a station, with no poll to stop
    finally:
        SetFollowService.changes.unsubscribe(tell)


@pytest.mark.parametrize("failure", ["error", "no set"])
def test_a_load_that_failed_is_told(
        autonaApp, headless, settings, provider, monkeypatch, failure):
    """`setId` goes back to null with no write to the board, whose own updates
    tell every other change of it."""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    settings["general.disable_autoupdate"] = True
    _failOnce(provider, monkeypatch, 1001, failure)
    assert _v1(client, "/api/v1/scoreboards/1:load-set",
               {"setId": 1001}) == (200, None)
    assert _follow(client)["setId"] == "1001"
    heard = []

    def tell(board):
        heard.append((board, SetFollowService(board).Snapshot()["setId"]))

    SetFollowService.changes.subscribe(tell)
    try:
        _drain(autonaApp)
    finally:
        SetFollowService.changes.unsubscribe(tell)

    assert heard == [(1, None)]
    assert _follow(client)["setId"] is None


def test_a_follow_and_its_ticks_wait_for_the_apply(
        autonaApp, desktop, settings, provider):
    """The held board's point, and what a follow could most easily break: the
    set a follow loads, and the one a tick moves it to, reach the operator's
    board and nothing OBS reads until 反映. Looked at where they are published
    (the committed view, the board's route, program_state, the deltas): the
    live state moves whether or not the hold works."""
    from src.core import StateSchema as schema
    from src.core.StateManager import StateManager

    _, client, _ = desktop
    settings["general.disable_autoupdate"] = False
    provider.stations["77"] = [_node(3001)]

    def withoutStats(board):
        # The statistics publish at once (HoldBoard's exempt keys).
        return {key: value for key, value in copy.deepcopy(board).items()
                if key not in schema.STATS_KEYS}

    def published():
        route = client.get("/api/v1/scoreboards/1").get_json()["data"]
        return (withoutStats(StateManager.GetCommitted("score.1")),
                withoutStats(route),
                withoutStats(WebOps.program_state()["state"]["score"]["1"]))

    rows = []

    def told(payload):
        rows.extend(
            row for row in payload["delta"]
            if [str(part) for part in row["path"][:2]] == ["score", "1"]
            and str(row["path"][2]) not in schema.STATS_KEYS)

    before = published()
    StateManager.observers.state_updated.subscribe(told)
    try:
        assert _v1(client, "/api/v1/scoreboards/1:follow",
                   _STATION_ROW) == (200, None)
        _drain(autonaApp)
        assert autonaApp.get("score.1.set_id") == 3001
        assert StateManager.HasPendingChanges("score.1")
        assert published() == before
        assert rows == []

        provider.stations["77"] = [_node(3002, (3, 4))]
        _tick(1)
        _drain(autonaApp)
        assert autonaApp.get("score.1.set_id") == 3002
        assert StateManager.HasPendingChanges("score.1")
        assert published() == before
        assert rows == []

        assert _v1(client, "/api/v1/scoreboards/1:apply") == (200, None)
        autonaApp.settle()
        # What was held all along is what 反映 sends, here too.
        assert not StateManager.HasPendingChanges("score.1")
        assert StateManager.GetCommitted("score.1.set_id") == 3002
        assert WebOps.program_state()["state"]["score"]["1"]["set_id"] == 3002
        assert any(str(row["path"][2]) == "set_id" for row in rows), rows
    finally:
        StateManager.observers.state_updated.unsubscribe(told)


def test_a_cancel_with_nothing_to_cancel_does_not_light_the_apply_button(
        autonaApp, desktop, settings, provider):
    """Under a held prefix an identical write is not a write (the shared
    SetIfChanged rule): it marked the board dirty, with no difference between
    what 反映 would send and what is out. A cancel that does change something
    still waits for 反映."""
    from src.core.SetFollowService import SetFollowService
    from src.core.StateManager import StateManager

    _, client, _ = desktop
    heard = []
    tell = heard.append
    assert not StateManager.HasPendingChanges("score.1")
    SetFollowService.changes.subscribe(tell)
    try:
        assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
        autonaApp.settle()
        assert not StateManager.HasPendingChanges("score.1")
        assert heard == []

        settings["general.disable_autoupdate"] = False
        assert _v1(client, "/api/v1/scoreboards/1:load-set",
                   {"setId": 1001}) == (200, None)
        _drain(autonaApp)
        assert _v1(client, "/api/v1/scoreboards/1:apply") == (200, None)
        autonaApp.settle()
        assert not StateManager.HasPendingChanges("score.1")
        assert str(StateManager.GetCommitted("score.1.set_id")) == "1001"

        heard.clear()
        assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
        autonaApp.settle()
        assert heard == [1]
        assert StateManager.HasPendingChanges("score.1")
        assert autonaApp.get("score.1.set_id") is None
        assert str(StateManager.GetCommitted("score.1.set_id")) == "1001"

        assert _v1(client, "/api/v1/scoreboards/1:apply") == (200, None)
        autonaApp.settle()
        heard.clear()
        assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
        autonaApp.settle()
        assert not StateManager.HasPendingChanges("score.1")
        assert heard == []
    finally:
        SetFollowService.changes.unsubscribe(tell)


def test_a_poll_asked_for_on_a_request_thread_has_its_seconds_at_once(
        autonaApp, headless, settings, provider):
    """Headless, a load runs on the request's thread and the clock is the main
    thread's: the notice goes out before the main thread can start the timer,
    and a reader answering it read `secondsLeft: null`. (The main thread does
    nothing here until the thread has been joined.)"""
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    settings["general.disable_autoupdate"] = False
    fetcher = SetFollowService.fetcher
    service = SetFollowService(1)
    heard = []

    def tell(board):
        heard.append(SetFollowService(board).Snapshot()["poll"])

    def onThread(fn):
        errors = []

        def run():
            try:
                fn()
            except BaseException as error:  # handed back to the test
                errors.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(10)
        assert not thread.is_alive() and errors == []

    SetFollowService.changes.subscribe(tell)
    try:
        onThread(lambda: WebOps.load_set(1, "1001"))
    finally:
        SetFollowService.changes.unsubscribe(tell)

    timer = fetcher._timers.get(1)
    assert timer is None or not timer.isActive()    # not started yet
    assert len(heard) == 1
    assert 0 <= heard[0].pop("secondsLeft") <= 5
    assert heard == [{"mode": "set", "identifier": None}]
    left = service.Snapshot()["poll"]["secondsLeft"]
    assert isinstance(left, int) and 0 <= left <= 5

    _drain(autonaApp)
    assert fetcher._timers[1].isActive()
    assert isinstance(service.Snapshot()["poll"]["secondsLeft"], int)

    onThread(lambda: WebOps.stop_follow(1))
    assert service.Snapshot()["poll"] is None
    autonaApp.settle()
    assert not fetcher._timers[1].isActive()
    assert fetcher.RemainingMs(1) is None


def test_reading_builds_no_board_and_a_follow_builds_it(
        autonaApp, desktop, provider):
    """As every board operation does (BoardHost.Prepare): a write to a board
    not built is lost when it is. A read has nothing to lose."""
    _, client, _ = desktop
    provider.stations["77"] = [_node(3001)]
    # The follow builds board 2's widgets on purpose: the desktop host's
    # Prepare for a board core does not have is what this checks.
    autonaApp.allowWidgets()

    autonaApp._dropBoard(2)
    assert _follow(client, 2)["station"] is None
    assert autonaApp.manager.scoreboards.get(2) is None

    assert _v1(client, "/api/v1/scoreboards/2:follow",
               _STATION_ROW) == (200, None)
    assert autonaApp.manager.scoreboards.get(2) is not None
    _drain(autonaApp)
    assert autonaApp.get("score.2.set_id") == 3001


def test_stopping_builds_no_board_and_writes_nothing_to_one_not_built(
        autonaApp, desktop, settings, provider):
    """A load always builds its board, so one not built has nothing to stop.
    Building it for this seeded it (losing what the state had) and turned the
    polling setting off."""
    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager

    _, client, _ = desktop
    autonaApp._dropBoard(2)
    # What a board not built yet can have: the saved state's, from the last run.
    StateManager.Set("score.2.set_id", 1234)
    autonaApp.settle()
    before = copy.deepcopy(StateManager.Get("score.2"))
    writes = []
    settingsWritten = []
    real = StateManager.Set

    def recorded(key, value):
        writes.append(key)
        return real(key, value)

    with patch.object(StateManager, "Set", recorded), \
            patch.object(SettingsManager, "Set", lambda key, value:
                         settingsWritten.append(key)):
        assert _v1(client, "/api/v1/scoreboards/2:stop-follow") == (200, None)
    autonaApp.settle()

    assert autonaApp.manager.scoreboards.get(2) is None
    assert writes == []
    assert settingsWritten == []
    assert StateManager.Get("score.2") == before


def test_a_built_board_is_there_for_the_host_only_once_it_is(
        autonaApp, desktop):
    host = WebOps.current_board_host()
    autonaApp.allowWidgets()    # Prepare builds the widgets: what is checked
    autonaApp._dropBoard(2)
    assert host.Built(2) is False
    host.Prepare(2)
    assert host.Built(2) is True
    assert BoardHost().Built(2) is True     # with no widget, always


@pytest.mark.parametrize("body", [
    {},
    {"type": "set", "id": 77},
    {"type": True, "id": 77},
    {"type": ["station"], "id": 77},
    {"id": 77},
    {"type": "station"},
    {"type": "station", "id": None},
    {"type": "station", "id": True},
    {"type": "station", "id": False},
    {"type": "station", "id": 1.5},
    {"type": "station", "id": ""},
    {"type": "station", "id": 0},
    {"type": "station", "id": -1},
    {"type": "station", "id": [77]},
    {"type": "station", "id": {"id": 77}},
    {"type": "station", "id": 77, "identifier": True},
    {"type": "station", "id": 77, "identifier": ""},
    {"type": "station", "id": 77, "identifier": 1.5},
    {"type": "stream"},
    {"type": "stream", "identifier": None},
    {"type": "stream", "identifier": 5},
    {"type": "stream", "identifier": ""},
    {"type": "stream", "identifier": True},
    {"type": "stream", "identifier": STREAM, "id": True},
])
def test_what_a_follow_refuses(autonaApp, headless, provider, body):
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    assert _v1(client, "/api/v1/scoreboards/1:follow",
               body) == (400, "INVALID_ARGUMENT")
    _drain(autonaApp)
    assert provider.calls == []
    assert SetFollowService(1).Station() is None


@pytest.mark.parametrize("method, path, body", [
    ("get", "/api/v1/scoreboards/3/follow", None),
    ("post", "/api/v1/scoreboards/3:follow", _STATION_ROW),
    ("post", "/api/v1/scoreboards/3:stop-follow", {}),
    ("get", "/api/v1/scoreboards/4/follow", None),
    ("post", "/api/v1/scoreboards/0:follow", _STATION_ROW),
    ("post", "/api/v1/scoreboards/4:stop-follow", {}),
])
def test_only_boards_1_and_2_follow(autonaApp, headless, provider, method,
                                    path, body):
    """The crew board loads no set; there is no other board."""
    _, client, _ = headless
    response = getattr(client, method)(path, json=body) \
        if method == "post" else client.get(path)
    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["error"]["code"] == "INVALID_ARGUMENT"
    _drain(autonaApp)
    assert provider.calls == []


def test_stopping_refuses_the_crew_board_by_itself(autonaApp, headless):
    # The route refuses it first. Headless every board counts as built, so with
    # no check of its own this would write the followed set on the crew board.
    from src.core.StateManager import StateManager

    before = copy.deepcopy(StateManager.Get("score.3"))
    with pytest.raises(ValueError):
        WebOps.stop_follow(3)
    autonaApp.settle()
    assert StateManager.Get("score.3") == before


def test_with_no_tournament_nothing_is_followed(
        autonaApp, headless, provider, monkeypatch):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.SetFollowService import SetFollowService

    _, client, _ = headless
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        None)
    assert _v1(client, "/api/v1/scoreboards/1:follow",
               _STATION_ROW) == (409, "NOT_READY")
    assert SetFollowService(1).Station() is None
    # Stopping needs no tournament: the board may still follow the one unloaded.
    assert _v1(client, "/api/v1/scoreboards/1:stop-follow") == (200, None)
