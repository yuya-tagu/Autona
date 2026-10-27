"""Set loading and following end to end on the desktop: what is fetched,
what is written and published, what the routes answer and what the board
shows, for every way a set is loaded or followed (ADR-005 S3e-7).

Recorded on the last tree before set loading moved into core (41e92c4c:
the board's NewSetSelected / UpdateSetData / ChangeSetData and its two
QTimers, the provider adapter's GetMatch / LoadStationSets workers,
WebServerActions' load_set / post_score) and replayed on the current one.
For every step of a scenario the recording holds

- the provider calls, in order (the tournament provider is faked at its
  own interface, `AutonaTournamentDataProvider.instance.provider`: the
  set fetches, the stream queue, the station / stream lookups, the future
  sets -- and the statistics' fetches, which a load asks for);
- every StateManager.Set / Unset, in order, with its value as
  program_state.json holds it (orjson: the key order is compared too);
- every published save cycle (the Socket.IO deltas, encoded as a client
  receives them) and the full reloads;
- the route / event answers;
- the local player database's writes;
- what the board shows: the auto-update row (shown, its label) and
  whether 反映 has something to publish;

and, after the scenario, each board's subtree (its key order included) and
the stream queue. "State key order and deltas, statistics requests, 反映
and the responses unchanged" is this file.

Record (on the pre-S3e-7 tree only):

    AUTONA_RECORD_SET_FOLLOW=1 python -m pytest tests/test_set_follow_flow.py

Determinism: the provider's thread pool runs one job at a time while a
scenario runs, so the jobs answer in the order they were started; the
5-second timer is pushed an hour away after every step and ticked by the
test (its `timeout`), never by the clock. Every scenario runs under the
locale the recording ran under, not the machine's (`recordedLocale`).

Not recorded here, because they change on purpose (each has its own test
on the current tree, tests/test_set_follow.py): `pull_stream` (it did
nothing: ADR-005 S3e 論点 3), a single-player `POST /score` addressing
team / player "0" (it wrote another slot: the S3e-4 bug), an answer that
arrives after the board moved on (single-threaded, none does), and a set
load with no tournament loaded (it cleared the board).

The sets queued behind a followed station: the recorded tree fetched them
from the station lookup's own pool thread, so their answer was posted back
to that thread -- which runs no event loop -- and never arrived;
`score.{n}.station_queue` stayed `{}`. The current tree's answer arrives
and is kept in memory, and the state is as it was: replayed exactly.
"""
import copy
import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import HARNESS_GAME
from tests.fixtures.write_recording import encode, encodeDelta

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "set_follow" / "golden.json"
RECORD = os.environ.get("AUTONA_RECORD_SET_FOLLOW") == "1"

VIDEOGAME = 1386
HOUR_MS = 3600 * 1000
STREAM = "harness_channel"


# ── the provider ─────────────────────────────────────────────────────────

def _player(tag, pid, seed, character="Mario", skin=0, **extra):
    # Keys out of order on purpose: whatever order a payload carries is
    # part of what is replayed.
    player = {"seed": seed, "gamerTag": tag, "prefix": "SP",
              "id": [pid, pid * 10], "twitter": f"{tag.lower()}_tw",
              "mains": {HARNESS_GAME: [[character, skin]],
                        "other_game": [["Zelda", 2]]},
              "country_code": "JP"}
    player.update(extra)
    return player


SETS = {
    1001: [[_player("ALPHA", 11, 1)], [_player("BRAVO", 22, 4, "Luigi")]],
    1002: [[_player("CHARLIE", 33, 2, "Peach", 1)],
           [_player("DELTA", 44, 3)]],
    1003: [[_player("ALPHA", 11, 1)], [_player("ECHO", 55, 6, "Peach")]],
    2001: [[_player("STREAMER", 66, 5)], [_player("BRAVO", 22, 4)]],
    3001: [[_player("FOXTROT", 77, 7)], [_player("GOLF", 88, 8, "Luigi")]],
    3002: [[_player("HOTEL", 99, 9, "Peach")], [_player("ALPHA", 11, 1)]],
    4001: [[_player("ALPHA", 11, 1), _player("BRAVO", 22, 4, "Luigi")],
           [_player("CHARLIE", 33, 2), _player("DELTA", 44, 3, "Peach")]],
}


def _node(setId, entrants=(1, 2), state=2):
    """A set as the station / stream lookups return it (the raw GraphQL
    node)."""
    return {"id": setId, "state": state, "event": {"id": 9},
            "slots": [{"entrant": {"id": entrants[0]}},
                      {"entrant": {"id": entrants[1]}}]}


class _FakeProvider:
    """The loaded tournament: answers from the tables above and records
    every call. Called on the adapter's worker threads."""

    name = "StartGG"
    url = "https://www.start.gg/tournament/t/event/e"

    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()
        self._fetched = {}
        self.stations = {}
        self.streams = {}
        self.streamQueue = {
            STREAM: {"1": {"id": 2001, "p1_name": "STREAMER",
                           "p2_name": "BRAVO"}},
            "another": {"1": {"id": 1002, "p1_name": "CHARLIE",
                              "p2_name": "DELTA"}}}

    def _record(self, *call):
        with self._lock:
            self.calls.append(json.loads(json.dumps(call, default=str)))

    def GetMatch(self, setId, progress_callback=None, cancel_event=None):
        self._record("GetMatch", setId)
        try:
            number = int(setId)
        except (TypeError, ValueError):
            return {}
        if number not in SETS:
            return {}
        with self._lock:
            fetched = self._fetched[number] = self._fetched.get(number, 0) + 1
        twoA = len(SETS[number][0]) > 1
        return {
            "round_name": f"Round {number}", "id": number,
            "tournament_phase": "Bracket",
            "round_name_en": f"Round {number} EN",
            "tournament_pool": "A" if number % 2 else "",
            "bestOf": 5 if number % 2 else 3,
            "team1score": min(fetched - 1, 2), "team2score": 0,
            "team1losers": False, "team2losers": number % 2 == 0,
            "isOnline": True, "has_selection_data": True,
            "bracket_type": "DOUBLE_ELIMINATION",
            "p1_name": "Team Left" if twoA else "ALPHA",
            "p2_name": "Team Right" if twoA else "BRAVO",
            "entrants": copy.deepcopy(SETS[number]),
        }

    def GetStreamQueue(self, progress_callback=None, cancel_event=None):
        self._record("GetStreamQueue")
        return copy.deepcopy(self.streamQueue)

    def GetStreamMatchId(self, streamName):
        self._record("GetStreamMatchId", streamName)
        return copy.deepcopy(self.streams.get(streamName))

    def GetStationMatchsId(self, stationId):
        self._record("GetStationMatchsId", stationId)
        return copy.deepcopy(self.stations.get(str(stationId), []))

    def GetFutureMatchesList(self, setsId, progress_callback=None,
                             cancel_event=None):
        self._record("GetFutureMatchesList", [s.get("id") for s in setsId])
        return {str(index): {"id": s.get("id"), "p1_name": f"P{s.get('id')}",
                             "p2_name": "Q"}
                for index, s in enumerate(setsId, start=1)}

    # The statistics a load asks for: recorded, never answered.
    def GetRecentSets(self, id1, id2, videogame, **kwargs):
        self._record("GetRecentSets", id1, id2, videogame)

    def GetLastSets(self, playerID, playerNumber, **kwargs):
        self._record("GetLastSets", playerID, playerNumber)

    def GetPlayerHistoryStandings(self, playerID, playerNumber, gameType,
                                  **kwargs):
        self._record("GetPlayerHistoryStandings", playerID, playerNumber,
                     gameType)


# ── driving the follow timer ────────────────────────────────────────────

def _followTimer(board):
    """The board's 5-second follow timer, or None. Pre-S3e-7 it was the
    board's own `autoUpdateTimer`; since then it is the set follow's clock
    (AutonaSetFollowUtil), one per board."""
    timer = getattr(board, "autoUpdateTimer", None)
    if timer is not None:
        return timer
    from src.core.SetFollowService import SetFollowService
    fetcher = SetFollowService.fetcher
    timers = getattr(fetcher, "_timers", None) or {}
    return timers.get(getattr(board, "scoreboardNumber", None))


def _tick(board):
    timer = _followTimer(board)
    if timer is not None:
        timer.timeout.emit()


def _holdTimer(board):
    # Never the clock's tick: an hour away, after every step.
    timer = _followTimer(board)
    if timer is not None and timer.isActive():
        timer.start(HOUR_MS)


# ── the recorder ────────────────────────────────────────────────────────

class _Recorder:

    def __init__(self, app, fake, saves, boards):
        self.app = app
        self.fake = fake
        self.saves = saves
        self.boards = boards
        self.steps = []
        self._writes = []
        self._deltas = []
        self._responses = []

    def __enter__(self):
        from src.core.StateManager import StateManager

        real = StateManager.Set
        realUnset = StateManager.Unset

        def spySet(key, value=None, *args, **kwargs):
            self._writes.append(["set", key, encode(value)])
            return real(key, value, *args, **kwargs)

        def spyUnset(key, *args, **kwargs):
            self._writes.append(["unset", key])
            return realUnset(key, *args, **kwargs)

        self._patches = [patch.object(StateManager, "Set", spySet),
                         patch.object(StateManager, "Unset", spyUnset)]
        for patcher in self._patches:
            patcher.start()
        self._subs = (
            (StateManager.observers.state_updated, self._onDelta),
            (StateManager.observers.state_big_change, self._onBig),
        )
        for channel, callback in self._subs:
            channel.subscribe(callback)
        return self

    def __exit__(self, *exc):
        for channel, callback in self._subs:
            channel.unsubscribe(callback)
        for patcher in reversed(self._patches):
            patcher.stop()
        return False

    def _onDelta(self, payload):
        self._deltas.append(encodeDelta(payload.get("delta")))

    def _onBig(self):
        self._deltas.append("BIG")

    def respond(self, *answer):
        self._responses.append(json.loads(json.dumps(answer, default=str)))

    def drain(self):
        """The main thread's queue until it is empty, then the provider's
        oldest job, and again, until neither has anything left (see
        _SteppedPool)."""
        from src.AutonaTournamentDataProvider import \
            AutonaTournamentDataProvider

        pool = AutonaTournamentDataProvider.instance.threadPool
        while True:
            # What one pass posts (a deferred refresh, a card torn down)
            # is run by the next.
            for _ in range(3):
                self.app.settle()
            if not pool.RunNext():
                break

    def step(self, label, action):
        from src.core.StateManager import StateManager

        callsBefore = len(self.fake.calls)
        savesBefore = len(self.saves)
        self._writes, self._deltas, self._responses = [], [], []
        action()
        self.drain()
        ui = {}
        for number, board in self.boards().items():
            _holdTimer(board)
            row = getattr(board, "timerLayout", None)
            ui[str(number)] = None if row is None else {
                "follow": not row.isHidden(),
                "label": board.labelAutonaUpdate.text()}
        self.steps.append({
            "step": label,
            "calls": self.fake.calls[callsBefore:],
            "writes": self._writes,
            "deltas": self._deltas,
            "responses": self._responses,
            "db": self.saves[savesBefore:],
            "ui": ui,
            "pending": {str(n): StateManager.HasPendingChanges(f"score.{n}")
                        for n in self.boards()},
        })

    def result(self):
        subtrees = {}
        for number in (1, 2, 3):
            subtree = self.app.get(f"score.{number}")
            subtrees[str(number)] = None if subtree is None else {
                "keys": list(subtree), "state": encode(subtree)}
        return {"steps": self.steps, "boards": subtrees,
                "streamQueue": encode(self.app.get("streamQueue")),
                "currentStream": encode(self.app.get("currentStream"))}


# ── golden ───────────────────────────────────────────────────────────────

FIELDS = ("calls", "writes", "deltas", "responses", "db", "ui", "pending")


def _check(name, recorded):
    if RECORD:
        golden = {}
        if GOLDEN_PATH.exists():
            golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        golden[name] = recorded
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(golden, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))[name]
    expectedSteps = golden["steps"]
    actualSteps = recorded["steps"]
    assert [s["step"] for s in actualSteps] == \
        [s["step"] for s in expectedSteps]
    differ = [f"{expected['step']!r}: {field}"
              for expected, actual in zip(expectedSteps, actualSteps)
              for field in FIELDS if actual[field] != expected[field]]
    if differ:
        expected, actual = next(
            (expected, actual)
            for expected, actual in zip(expectedSteps, actualSteps)
            if expected != actual)
        pytest.fail(f"{name}: differs at\n  " + "\n  ".join(differ)
                    + f"\nfirst step recorded: {json.dumps(expected)[:4000]}"
                    + f"\nfirst step now: {json.dumps(actual)[:4000]}")
    for key in ("boards", "streamQueue", "currentStream"):
        assert recorded[key] == golden[key], key


# ── fixtures ─────────────────────────────────────────────────────────────

# The locale the golden was recorded under: the recording machine's first
# UI language, which LoadLocale() falls back to when the settings name none.
RECORDED_LOCALE = "ja-Jpan-JP"
LOCALE_SETTINGS = ("program_language", "game_asset_language",
                   "fg_term_language")


@pytest.fixture(autouse=True)
def recordedLocale(autonaApp):
    """Every scenario under the recording's locale, whatever the machine's.

    The harness loads the locale as the app does -- with no language in the
    settings, the OS's UI language -- and two things recorded here follow
    it: the follow row's label is translated ("自動更新" / "Auto update")
    and a loaded player's country carries its names in the program and
    export locales ("日本" / "Japan"). The golden was recorded on a Japanese
    Windows; an English runner replayed every step with the other label and
    every load with the other names. Pinned the way the harness loads it at
    start-up (LoadLocale, LoadRoundNames), with the countries reloaded under
    it (the first card built loads them under whatever locale is current);
    afterwards all three are loaded again under the harness's own settings.
    No board is built on either side: autonaApp drops them after every
    test, and `provider` drops them before this is undone.
    """
    from src.core.SettingsManager import SettingsManager
    from src.Helpers.AutonaCountryHelper import AutonaCountryHelper
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

    def load():
        AutonaLocaleHelper.LoadLocale()
        AutonaLocaleHelper.LoadRoundNames()
        AutonaCountryHelper.LoadCountries()
        autonaApp.settle()

    realGet = SettingsManager.Get
    with patch.object(SettingsManager, "Get", lambda key, default=None: (
            RECORDED_LOCALE if key in LOCALE_SETTINGS
            else realGet(key, default))):
        load()
    try:
        yield
    finally:
        load()


@pytest.fixture
def settings(monkeypatch):
    """The settings a set load reads, as the test sets them (nothing is
    written to the harness's file): polling off as the board leaves it
    (it forces general.disable_autoupdate on), overwriting on, the
    streamer's channel."""
    from src.core.SettingsManager import SettingsManager

    values = {"general.disable_autoupdate": True,
              "general.disable_overwrite": False,
              "twitch_username": STREAM}
    realGet = SettingsManager.Get
    monkeypatch.setattr(SettingsManager, "Get", lambda key, default=None: (
        values[key] if key in values else realGet(key, default)))
    return values


class _SteppedPool:
    """The adapter's thread pool, run by the test: a job is queued, and
    `RunNext` runs the oldest one on a thread of its own (the answers cross
    to the main thread as a worker's do) once the main thread has nothing
    left to do. One schedule, the same on every run.

    Two things made the real pool's runs differ from each other:

    - the order of a job's answer against what the main thread was doing
      meanwhile (a statistics refresh the clear queued, say, landed before
      or after the stream queue's answer);
    - on the tree this was recorded on, a fetch answered through its own
      Worker's signals (`worker.signals.result` -> a lambda): once the pool
      had run and dropped the job, the Worker -- and its signals object --
      could be collected before the main thread delivered the queued
      answer, which was then lost (the set load did nothing). Every job is
      kept here until the scenario ends, so every answer arrives and the
      recording is what the code meant to do. The current tree answers
      through an object that lives as long as the app
      (AutonaSetFollowUtil) and does not depend on it.
    """

    def __init__(self, pool):
        self.pool = pool
        self.queue = []
        self.kept = []
        self._lock = threading.Lock()

    def start(self, runnable, *args):
        with self._lock:
            self.queue.append(runnable)
            self.kept.append(runnable)

    def RunNext(self):
        """Run the oldest queued job; False when there is none."""
        with self._lock:
            if not self.queue:
                return False
            job = self.queue.pop(0)
        thread = threading.Thread(
            target=job.run if hasattr(job, "run") else job)
        thread.start()
        thread.join(30)
        assert not thread.is_alive(), "a provider job did not finish"
        return True

    def waitForDone(self, msecs=-1):
        while self.RunNext():
            pass
        return True

    def __getattr__(self, name):
        return getattr(self.pool, name)


@pytest.fixture
def provider(autonaApp, monkeypatch):
    """The fake tournament on the real adapter, its pool one job at a time,
    and the state the loads read; undone afterwards."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.StateManager import StateManager

    adapter = AutonaTournamentDataProvider.instance
    fake = _FakeProvider()
    adapter.threadPool.waitForDone(10000)
    monkeypatch.setattr(adapter, "threadPool",
                        _SteppedPool(adapter.threadPool))
    monkeypatch.setattr(adapter, "provider", fake)

    # The same starting point whatever ran before: no board built, the
    # bracket type ahead of every board's subtree, both stream queue keys
    # present (a cycle adding two new top-level keys publishes the whole
    # state: the S3e-5 notes' DeepDiff quirk).
    autonaApp.dropAllBoards()
    with StateManager.SaveBlock():
        for n in (1, 2, 3):
            StateManager.Unset(f"score.{n}")
        StateManager.Unset("score.bracket_type")
        StateManager.Set("score.bracket_type", "DOUBLE_ELIMINATION")
    StateManager.Set("provider_videogame", {"id": VIDEOGAME, "name": "G"})
    StateManager.Set("streamQueue", {})
    StateManager.Set("currentStream", "")
    autonaApp.loadCharacters()
    autonaApp.settle()
    try:
        yield fake
    finally:
        for board in list(autonaApp.manager.scoreboards.values()):
            timer = _followTimer(board)
            if timer is not None:
                timer.stop()
        adapter.threadPool.waitForDone()
        autonaApp.settle()
        autonaApp.dropAllBoards()
        for key in ("provider_videogame", "score.bracket_type",
                    "streamQueue", "currentStream"):
            StateManager.Unset(key)
        autonaApp.settle()


@pytest.fixture
def playerDB(monkeypatch):
    """The local player database: what the loads read ({DB tag: record})
    and what they save (the returned list, recorded per step)."""
    from src.core.PlayerDB import PlayerDB
    instance = PlayerDB.instance
    players = {}
    saves = []
    monkeypatch.setattr(instance, "database", players)
    monkeypatch.setattr(
        instance, "AddPlayers",
        lambda records, overwrite=False, deferRefresh=False: saves.append(
            json.loads(json.dumps([records, overwrite], default=str))))
    monkeypatch.setattr(instance, "SaveDB", lambda: None)
    monkeypatch.setattr(instance, "NotifyChanged", lambda: None)
    return players, saves


@pytest.fixture
def web(autonaApp, monkeypatch):
    """The legacy routes, /api/v1 and Socket.IO, as the app registers them;
    requests run on the test thread (the GUI thread)."""
    from src import AutonaApiV1
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app

    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    actions = autonaApp.actions()
    monkeypatch.setattr(WebServer, "actions", actions)
    monkeypatch.setattr(AutonaApiV1, "_actions", actions)
    monkeypatch.setattr(AutonaApiV1, "_initialized", True)
    monkeypatch.setattr(WebServer, "socketio", socketio)
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=client)
    ws.get_received()
    return client, ws


def _get(rec, client, path):
    response = client.get(path)
    rec.respond(path, response.status_code, response.get_data(as_text=True))


def _post(rec, client, path, body):
    response = client.post(path, data=body)
    rec.respond(path, response.status_code, response.get_data(as_text=True))


def _v1(rec, client, path, body=None):
    response = client.post(path, json=body or {})
    payload = response.get_json(silent=True) or {}
    rec.respond(path, response.status_code, payload.get("ok"),
                (payload.get("error") or {}).get("code"))


def _event(rec, ws, event, message):
    ws.emit(event, json.dumps(message))
    rec.respond(event, [[e["name"], e["args"]] for e in ws.get_received()])


# ── scenarios ────────────────────────────────────────────────────────────

def test_loading_sets_from_the_web(autonaApp, settings, provider, playerDB,
                                   web):
    """Polling off (as the board leaves it): the legacy route, the
    Socket.IO event and /api/v1's load-set, the same set again, a clear in
    between, the database merge of general.disable_overwrite, the team
    board, and the boards that have no set to load."""
    client, ws = web
    players, saves = playerDB
    autonaApp.board(1)
    autonaApp.board(2)
    autonaApp.board(3)
    autonaApp.settle()
    built = autonaApp.manager.scoreboards

    with _Recorder(autonaApp, provider, saves, lambda: dict(built)) as rec:
        rec.step("legacy load-set 1001", lambda: _get(
            rec, client, "/scoreboard1-load-set?set=1001"))
        rec.step("the same set again (Socket.IO)", lambda: _event(
            rec, ws, "load_set", {"scoreboardNumber": "1", "set": "1001"}))
        rec.step("v1 load-set 1002 without mains", lambda: _v1(
            rec, client, "/api/v1/scoreboards/1:load-set",
            {"setId": 1002, "noMains": True}))
        rec.step("legacy load-set without a set", lambda: _get(
            rec, client, "/scoreboard1-load-set"))
        rec.step("get-set", lambda: _get(rec, client, "/scoreboard1-get-set"))
        rec.step("legacy reset-players", lambda: _get(
            rec, client, "/scoreboard1-reset-players"))
        rec.step("get-set after the clear", lambda: _get(
            rec, client, "/scoreboard1-get-set"))
        rec.step("1002 again, legacy no-mains", lambda: _get(
            rec, client, "/scoreboard1-load-set?set=1002&no-mains"))

        def merged():
            settings["general.disable_overwrite"] = True
            players["SP ECHO"] = {"gamerTag": "ECHO", "prefix": "SP",
                                  "twitter": "echo_db", "pronoun": "they",
                                  "id": [55, 550]}
            _get(rec, client, "/scoreboard1-load-set?set=1003")
        rec.step("disable_overwrite: 1003 merged with the database", merged)

        def overwriting():
            settings["general.disable_overwrite"] = False
            _event(rec, ws, "load_set", {"scoreboardNumber": "1",
                                         "set": 1001, "no-mains": True})
        rec.step("Socket.IO load_set with a number", overwriting)
        rec.step("apply", lambda: _v1(
            rec, client, "/api/v1/scoreboards/1:apply"))
        rec.step("team board 4001", lambda: _get(
            rec, client, "/scoreboard2-load-set?set=4001"))
        rec.step("crew board", lambda: (
            _get(rec, client, "/scoreboard3-load-set?set=1001"),
            _v1(rec, client, "/api/v1/scoreboards/3:load-set",
                {"setId": 1001})))
        rec.step("a board that is not one", lambda: _get(
            rec, client, "/scoreboard4-load-set?set=1001"))
        rec.step("v1 without a set id", lambda: _v1(
            rec, client, "/api/v1/scoreboards/1:load-set", {}))
    _check("web loads", rec.result())


def test_following_a_set_a_station_and_a_stream(autonaApp, settings,
                                                provider, playerDB, web):
    """Polling on: the set window's set, the ticks, a clear while
    following, the station window's station (the queue behind it, the
    station moving on, emptying), the stream, a set loaded while following
    the stream, the cancel button."""
    client, ws = web
    _, saves = playerDB
    settings["general.disable_autoupdate"] = False
    board = autonaApp.board(1)
    autonaApp.settle()
    built = autonaApp.manager.scoreboards
    provider.stations["77"] = [_node(3001), _node(3002, (3, 4))]
    provider.streams[STREAM] = _node(2001, (5, 6), state=1)
    station = {"type": "station", "id": 77, "identifier": "4",
               "stream": ""}
    stream = {"type": "stream", "id": 5, "identifier": STREAM}

    with _Recorder(autonaApp, provider, saves, lambda: dict(built)) as rec:
        rec.step("set window: 1001", lambda: board.signals.NewSetSelected.emit(
            {"id": 1001, "p1_name": "ALPHA", "p2_name": "BRAVO",
             "auto_update": "set"}))
        rec.step("tick", lambda: _tick(board))
        rec.step("tick again", lambda: _tick(board))
        rec.step("web reset-players while following", lambda: _get(
            rec, client, "/scoreboard1-reset-players"))
        rec.step("tick after the clear", lambda: _tick(board))
        rec.step("station window: station 4",
                 lambda: board.signals.StationSelected.emit(
                     copy.deepcopy(station)))
        rec.step("tick on the station", lambda: _tick(board))
        rec.step("web clear-all while following the station", lambda: _get(
            rec, client, "/scoreboard1-clear-all"))
        rec.step("tick after the clear: the station's set again",
                 lambda: _tick(board))

        def movedOn():
            provider.stations["77"] = [_node(3002, (3, 4)), _node(1003)]
            _tick(board)
        rec.step("the station moves on", movedOn)

        def emptied():
            provider.stations["77"] = []
            _tick(board)
        rec.step("the station empties", emptied)
        rec.step("stream window: the channel",
                 lambda: board.signals.StationSelected.emit(
                     copy.deepcopy(stream)))
        rec.step("tick on the stream", lambda: _tick(board))
        rec.step("set window while following the stream",
                 lambda: board.signals.NewSetSelected.emit(
                     {"id": 1002, "auto_update": "set"}))
        rec.step("tick on the set", lambda: _tick(board))
        rec.step("cancel", lambda: board.timerCancelBt.click())
        rec.step("tick after cancelling", lambda: _tick(board))
        rec.step("get-set", lambda: _get(rec, client, "/scoreboard1-get-set"))
        rec.step("web load-set while polling", lambda: _get(
            rec, client, "/scoreboard1-load-set?set=1003"))
        rec.step("tick on the web's set", lambda: _tick(board))
        rec.step("cancel again", lambda: board.timerCancelBt.click())
    _check("following", rec.result())


def test_post_score(autonaApp, settings, provider, playerDB, web):
    """POST /score and the Socket.IO score event: a set's entrants, one
    player (saved to the database), a payload with neither, the bracket
    type, the team and crew boards, and what each answers."""
    client, ws = web
    _, saves = playerDB
    autonaApp.board(1)
    autonaApp.board(2)
    autonaApp.board(3)
    autonaApp.settle()
    built = autonaApp.manager.scoreboards

    def score(body):
        return json.dumps(body)

    with _Recorder(autonaApp, provider, saves, lambda: dict(built)) as rec:
        rec.step("entrants", lambda: _post(rec, client, "/score", score({
            "team1score": 2, "round_name": "Winners Final",
            "overwrite": True, "bestOf": 5, "tournament_phase": "Top 8",
            "bracket_type": "SINGLE_ELIMINATION",
            "entrants": copy.deepcopy(SETS[1001])})))
        rec.step("mains only", lambda: _post(rec, client, "/score", score({
            "scoreboard": "1", "has_selection_data": True,
            "entrants": copy.deepcopy(SETS[1002])})))
        rec.step("entrants without mains", lambda: _post(
            rec, client, "/score", score({
                "scoreboard": 1, "overwrite": True, "no_mains": True,
                "team2score": 1, "entrants": copy.deepcopy(SETS[1003])})))
        rec.step("one player, saved", lambda: _post(
            rec, client, "/score", score({
                "team": "2", "player": "1", "data": {
                    "gamerTag": "SAVED", "prefix": "DB", "twitter": "s_tw",
                    "id": [5, 50], "savePlayerToDb": True,
                    "mains": {HARNESS_GAME: [["Peach", 2]]}}})))
        rec.step("one player and a bracket type", lambda: _post(
            rec, client, "/score", score({
                "team": 1, "player": 1, "data": {"gamerTag": "ONLY"},
                "bracket_type": "DOUBLE_ELIMINATION", "team1score": 1})))
        rec.step("a team the board does not have", lambda: _post(
            rec, client, "/score", score({
                "team": "3", "player": "1", "data": {"gamerTag": "NOPE"},
                "round_name": "Losers Final"})))
        rec.step("a player the team does not have", lambda: _post(
            rec, client, "/score", score({
                "team": "1", "player": "2", "data": {"gamerTag": "NOPE"}})))
        rec.step("neither entrants nor a player", lambda: _post(
            rec, client, "/score", score({
                "round_name": "Grand Final", "bracket_type": "ROUND_ROBIN",
                "team1losers": True})))
        rec.step("a board number that is not one", lambda: _post(
            rec, client, "/score", score({
                "scoreboard": "abc", "team2score": 2})))
        rec.step("no entrant at all", lambda: _post(
            rec, client, "/score", score({"overwrite": True,
                                          "entrants": [[], []]})))
        rec.step("entrants again after none", lambda: _post(
            rec, client, "/score", score({
                "overwrite": True, "entrants": copy.deepcopy(SETS[1001])})))
        rec.step("Socket.IO score", lambda: _event(rec, ws, "score", {
            "scoreboard": "1", "team1score": 3, "round_name": "From WS"}))
        rec.step("team board", lambda: _post(rec, client, "/score", score({
            "scoreboard": "2", "overwrite": True,
            "bracket_type": "DOUBLE_ELIMINATION",
            "p1_name": "Team Left", "p2_name": "Team Right",
            "entrants": copy.deepcopy(SETS[4001])})))
        rec.step("crew board", lambda: _post(rec, client, "/score", score({
            "scoreboard": "3", "round_name": "Crew Final", "bestOf": 3,
            "team1score": 2, "entrants": copy.deepcopy(SETS[1001])})))
        rec.step("a board that is not one", lambda: _post(
            rec, client, "/score", score({"scoreboard": "4"})))
        rec.step("a null board", lambda: _post(
            rec, client, "/score", score({"scoreboard": None})))
        rec.step("not JSON", lambda: _post(rec, client, "/score", "{nope"))
    _check("post_score", rec.result())
