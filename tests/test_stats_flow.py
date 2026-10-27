"""The board statistics end to end on the desktop: what is fetched, what is
written and what is published, for every way an operation asks for them
(ADR-005 S3e-5).

Recorded on the last tree before the statistics moved into core (9e8841d5:
the player cards' signals drove each board's AutonaStatsUtil, the web
routes emitted its trigger signals) and replayed on the current one. For
every step of a scenario the recording holds

- the fetches started, in order, with what they asked for (the provider's
  three entry points; a fetch is what reaches Start.gg);
- every write of a statistics key under ``score.{n}``, in order, with its
  value as program_state.json holds it (orjson: the key order is compared
  too);
- every published save cycle (the Socket.IO deltas, encoded as a client
  receives them) and the full reloads;
- the legacy / v1 route answers;

and, after the scenario, the key order of each board's subtree and its
statistics. "Fetch counts, publish timing, state keys and order unchanged"
is this file.

Record (on the pre-S3e-5 tree only):

    AUTONA_RECORD_STATS=1 python -m pytest tests/test_stats_flow.py

Start.gg is faked at the provider's entry points (GetRecentSets /
GetLastSets / GetPlayerHistoryStandings on the AutonaTournamentDataProvider
instance): nothing reaches the network, and a fetch is answered by the test,
from a thread of its own as a provider worker answers, when the step says
so -- the latest request of each statistic only: an earlier one still
pending is superseded and never answered. That a superseded or late answer
is dropped (it was written on that tree) is a change made on purpose, and
the current tree's own test (tests/test_stats_service.py,
tests/test_stats_util.py), not part of this recording.
"""
import json
import os
import re
import threading
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from qtpy.QtWidgets import QLineEdit, QMessageBox, QSpinBox

from tests.fixtures.write_recording import encode, encodeDelta

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "stats" / "golden.json"
RECORD = os.environ.get("AUTONA_RECORD_STATS") == "1"

VIDEOGAME = 1386
# Noon UTC: the same calendar day wherever the suite runs
# (UpdateHistorySets splits it with the local timezone).
EVENT_DATE = 1700049600

_STATS_KEY = re.compile(
    r"^score\.\d+\.(recent_sets|last_sets|history_sets|upset_factor)(\.|$)")
_STATS_LEAVES = ("recent_sets", "last_sets", "history_sets", "upset_factor")


class _FakeStartGG:
    """The provider's three statistics entry points, recorded and answered
    by the test. `provider.provider` is a stand-in for a loaded Start.gg
    tournament; with None, the entry points return before starting a
    worker, as the real ones do."""

    def __init__(self, provider):
        self.provider = provider
        self.calls = []
        self.pending = []
        self.clock = 1000
        self.step = None

    def install(self, monkeypatch):
        monkeypatch.setattr(self.provider, "provider", types.SimpleNamespace(
            name="StartGG", url=""))
        monkeypatch.setattr(self.provider, "GetRecentSets", self._recent)
        monkeypatch.setattr(self.provider, "GetLastSets", self._last)
        monkeypatch.setattr(self.provider, "GetPlayerHistoryStandings",
                            self._history)

    def _start(self, method, args, callback, **extra):
        args = json.loads(json.dumps(args))
        self.calls.append([method, args])
        self.pending.append(dict(
            method=method, args=args, callback=callback, step=self.step,
            number=len(self.calls), **extra))

    def _recent(self, callback, id1, id2, videogame):
        if self.provider.provider is None:
            return
        self.clock += 1
        self._start("recent_sets", [id1, id2, videogame], callback,
                    requestTime=self.clock)

    def _last(self, callback, playerId, playerNumber):
        if self.provider.provider is None:
            return
        self._start("last_sets", [playerId, playerNumber], callback)

    def _history(self, callback, playerId, playerNumber, gameType):
        if self.provider.provider is None:
            return
        self._start("history_sets", [playerId, playerNumber, gameType],
                    callback)

    @staticmethod
    def _payload(fetch):
        n = fetch["number"]
        if fetch["method"] == "recent_sets":
            # Keys out of order on purpose: the transport's ordering is
            # part of what is published.
            return {"sets": [{"tournament": f"T{n}", "id": f"S{n}",
                              "score": [2, n % 3], "winner": 0,
                              "timestamp": 5000 + n}],
                    "request_time": fetch["requestTime"]}
        if fetch["method"] == "last_sets":
            return {"playerNumber": fetch["args"][1], "last_sets": [{
                "round_name": f"R{n}", "phase_id": "A1",
                "player2_name": f"O{n}", "player1_name": f"P{n}",
                "player1_score": 2, "player2_score": n % 3,
                "phase_name": "Pools", "player1_seed": 1,
                "player2_seed": 8, "player1_team": "",
                "player2_team": "X"}]}
        return {"playerNumber": fetch["args"][1], "history_sets": [{
            "placement": n, "event_name": f"E{n}",
            "tournament_name": f"T{n}", "tournament_picture": None,
            "entrants": 64, "event_date": EVENT_DATE}]}

    @staticmethod
    def _asks(fetch):
        # What a fetch asks for: the head-to-head, or one team's statistic.
        if fetch["method"] == "recent_sets":
            return (fetch["method"],)
        return fetch["method"], fetch["args"][1]

    def answer(self, app, which=None):
        """Answer the pending fetches `which` picks (all by default), oldest
        first and one at a time, each from a thread of its own.

        Only the latest request for each statistic is answered; the ones a
        later request superseded are dropped unanswered. The tree this was
        recorded on wrote a superseded answer that arrived (the
        head-to-head of a set's first entrant with the previous set's
        second, say); the current one drops it, which is its own test."""
        latest = {}
        for fetch in self.pending:
            latest[self._asks(fetch)] = fetch
        for fetch in [f for f in self.pending if which is None or which(f)]:
            self.pending.remove(fetch)
            if latest[self._asks(fetch)] is not fetch:
                continue
            payload = self._payload(fetch)
            thread = threading.Thread(
                target=fetch["callback"].emit, args=(payload,))
            thread.start()
            thread.join()
            app.settle()


class _Recorder:
    """Records each step: fetches, stats writes, deltas, route answers."""

    def __init__(self, app, fetches):
        self.app = app
        self.fetches = fetches
        self.steps = []
        self._writes = []
        self._deltas = []
        self._responses = []

    def __enter__(self):
        from src.core.StateManager import StateManager

        real = StateManager.Set
        realUnset = StateManager.Unset

        def spySet(key, value):
            if _STATS_KEY.match(key):
                self._writes.append(["set", key, encode(value)])
            return real(key, value)

        def spyUnset(key):
            if _STATS_KEY.match(key):
                self._writes.append(["unset", key])
            return realUnset(key)

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

    def respond(self, response):
        self._responses.append(response)

    def step(self, label, action):
        callsBefore = len(self.fetches.calls)
        self.fetches.step = label
        self._writes, self._deltas, self._responses = [], [], []
        action()
        self.app.settle()
        self.fetches.step = None
        self.steps.append({
            "step": label,
            "fetches": self.fetches.calls[callsBefore:],
            "writes": self._writes,
            "deltas": self._deltas,
            "responses": self._responses,
        })

    def boards(self, *numbers):
        result = {}
        for number in numbers:
            subtree = self.app.get(f"score.{number}") or {}
            result[str(number)] = {
                "keys": list(subtree),
                "stats": {leaf: encode(subtree[leaf])
                          for leaf in _STATS_LEAVES if leaf in subtree},
            }
        return result


# ── golden ───────────────────────────────────────────────────────────────

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
              for field in ("fetches", "writes", "deltas", "responses")
              if actual[field] != expected[field]]
    if differ:
        expected, actual = next(
            (expected, actual)
            for expected, actual in zip(expectedSteps, actualSteps)
            if expected != actual)
        pytest.fail(f"{name}: differs at\n  " + "\n  ".join(differ)
                    + f"\nfirst step recorded: {json.dumps(expected)[:3000]}"
                    + f"\nfirst step now: {json.dumps(actual)[:3000]}")
    assert recorded["boards"] == golden["boards"]


# ── fixtures and drivers ─────────────────────────────────────────────────

@pytest.fixture
def startgg(autonaApp, monkeypatch):
    """A Start.gg tournament stand-in plus the state the fetches read (the
    tournament's game, the bracket type), undone afterwards."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager

    fetches = _FakeStartGG(AutonaTournamentDataProvider.instance)
    fetches.install(monkeypatch)
    realGet = SettingsManager.Get
    monkeypatch.setattr(SettingsManager, "Get", lambda key, default=None: (
        "harness_channel" if key == "twitch_username"
        else realGet(key, default)))
    StateManager.Set("provider_videogame", {"id": VIDEOGAME, "name": "G"})
    # The rows of one delta follow the state's key order, and where
    # score.bracket_type sits among score.{n} depends on which earlier test
    # built (and so re-added) which board. Fixed here, as on a fresh start:
    # no board built, the bracket type ahead of every board's subtree.
    autonaApp.dropAllBoards()
    with StateManager.SaveBlock():
        boards = {n: StateManager.Get(f"score.{n}") for n in (1, 2, 3)}
        for n, value in boards.items():
            if value is not None:
                StateManager.Unset(f"score.{n}")
        StateManager.Unset("score.bracket_type")
        StateManager.Set("score.bracket_type", "DOUBLE_ELIMINATION")
        for n, value in boards.items():
            if value is not None:
                StateManager.Set(f"score.{n}", value)
    # The stream queue is not a board's: whatever an earlier test left
    # would decide what this one's write publishes. Both keys exist before
    # the recording starts: a save cycle that adds two new top-level keys
    # is published as one delta holding the whole state (a DeepDiff quirk,
    # recorded in the S3e-5 notes), whose content is every earlier test's.
    StateManager.Set("streamQueue", {})
    StateManager.Set("currentStream", "")
    autonaApp.settle()
    try:
        yield fetches
    finally:
        for key in ("provider_videogame", "score.bracket_type",
                    "streamQueue", "currentStream"):
            StateManager.Unset(key)
        autonaApp.settle()


@pytest.fixture
def playerDB(monkeypatch):
    """The local player database the tag lookups read ({DB tag: record});
    nothing is written to the harness's file."""
    from src.core.PlayerDB import PlayerDB
    instance = PlayerDB.instance
    players = {}
    monkeypatch.setattr(instance, "database", players)
    monkeypatch.setattr(instance, "AddPlayers", lambda *a, **k: None)
    monkeypatch.setattr(instance, "SaveDB", lambda: None)
    monkeypatch.setattr(instance, "NotifyChanged", lambda: None)
    return players


@pytest.fixture
def http(autonaApp, monkeypatch):
    """The legacy routes and /api/v1 through Flask's test client, as the
    app registers them. The request runs on the test thread (the GUI
    thread)."""
    from src import AutonaApiV1
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app

    app, _socketio = _build_web_app()
    app.register_blueprint(api_v1)
    if hasattr(autonaApp, "actions"):
        # The recording tree (before ADR-005 S3e-9) handed the server the
        # board's actions object; the current one has none to hand.
        actions = autonaApp.actions()
        monkeypatch.setattr(WebServer, "actions", actions, raising=False)
        monkeypatch.setattr(AutonaApiV1, "_actions", actions, raising=False)
    return app.test_client()


def _legacy(http, recorder, path):
    response = http.get(path)
    recorder.respond([path, response.status_code,
                      response.get_data(as_text=True)])


def _v1(http, recorder, path):
    response = http.post(path)
    body = response.get_json(silent=True) or {}
    recorder.respond([path, response.status_code, body.get("ok"),
                      (body.get("error") or {}).get("code")])


def _entrant(tag, pid, seed):
    return {"gamerTag": tag, "id": [pid, pid * 10], "seed": seed}


def _set(left, right, bracket="DOUBLE_ELIMINATION"):
    return {"overwrite": True, "bracket_type": bracket,
            "entrants": [left, right]}


def _editTag(card, text):
    edit = card.findChild(QLineEdit, "name")
    edit.setText(text)
    edit.editingFinished.emit()


def _statsPanelRefresh():
    from src.AutonaStatsWidget import AutonaStatsWidget
    # The button's slot; it reads nothing of the panel.
    AutonaStatsWidget._refresh(None)


def _streamQueue(tag):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    signals = AutonaTournamentDataProvider.instance.signals
    thread = threading.Thread(target=signals.stream_queue_loaded.emit, args=(
        {"stream": {"1": {"id": 9, "p1_name": tag, "p2_name": "Q2"}}},))
    thread.start()
    thread.join()


# ── scenarios ────────────────────────────────────────────────────────────

def test_a_singles_board(autonaApp, startgg, playerDB, http):
    from src import AutonaWebOperations as WebOps

    board = autonaApp.board(1)
    autonaApp.settle()
    playerDB["SP ALPHA"] = {"gamerTag": "ALPHA", "prefix": "SP",
                           "id": [70, 700], "seed": 2}
    one = board.team1playerWidgets[0]
    two = board.team2playerWidgets[0]

    with _Recorder(autonaApp, startgg) as rec:
        rec.step("load set A", lambda: board.ChangeSetData(_set(
            [_entrant("AAA", 11, 1)], [_entrant("BBB", 22, 5)])))
        rec.step("answer set A", lambda: startgg.answer(autonaApp))
        rec.step("load set B", lambda: board.ChangeSetData(_set(
            [_entrant("CCC", 33, 2)], [_entrant("DDD", 44, 9)],
            bracket="SINGLE_ELIMINATION")))
        rec.step("load set C", lambda: board.ChangeSetData(_set(
            [_entrant("EEE", 55, 3)], [_entrant("FFF", 66, 4)])))
        rec.step("answer set C", lambda: startgg.answer(
            autonaApp, lambda f: f["step"] == "load set C"))
        rec.step("same set again", lambda: board.ChangeSetData(_set(
            [_entrant("EEE", 55, 3)], [_entrant("FFF", 66, 4)])))
        rec.step("card tag edit", lambda: _editTag(one, "ZZZ"))
        rec.step("card pick", lambda: one.SetData(
            _entrant("GGG", 77, 6), dontLoadFromDB=True))
        rec.step("card clear", two.Clear)
        rec.step("card seed spinbox",
                 lambda: one.findChild(QSpinBox, "seed").setValue(12))
        rec.step("card pick again", lambda: two.SetData(
            _entrant("HHH", 88, 1), dontLoadFromDB=True))
        rec.step("answer all", lambda: startgg.answer(autonaApp))
        rec.step("stats panel refresh", _statsPanelRefresh)
        rec.step("web update player (another)", lambda: WebOps.update_player(
            1, 2, 1, {"gamerTag": "III", "id": [99, 990], "seed": 7}))
        rec.step("web update player (same)", lambda: WebOps.update_player(
            1, 2, 1, {"gamerTag": "III", "name": "Real"}))
        rec.step("web set team data", lambda: WebOps.set_team_data(
            1, "1", "1", {"gamerTag": "JJJ", "id": [12, 120], "seed": 3}))
        rec.step("web load from tag",
                 lambda: WebOps.load_player_from_tag(1, "alpha", 2, 1))
        rec.step("answer web", lambda: startgg.answer(autonaApp))
        rec.step("web swap", lambda: WebOps.swap_teams(1))
        rec.step("board swap", board.SwapTeams)
        rec.step("hotkey-style swap", lambda: __import__(
            "src.core.Actions", fromlist=["AutonaActions"])
            .AutonaActions.instance.SwapTeams(1))
        rec.step("answer swaps", lambda: startgg.answer(autonaApp))
        rec.step("legacy recent", lambda: _legacy(
            http, rec, "/scoreboard1-stats-recent-sets"))
        rec.step("legacy upset", lambda: _legacy(
            http, rec, "/scoreboard1-stats-upset-factor"))
        for player in ("1", "2", "both", "x"):
            rec.step(f"legacy last {player}", lambda player=player: _legacy(
                http, rec, f"/scoreboard1-stats-last-sets-{player}"))
            rec.step(f"legacy history {player}",
                     lambda player=player: _legacy(
                         http, rec, f"/scoreboard1-stats-history-sets-{player}"))
        rec.step("v1 refresh", lambda: _v1(
            http, rec, "/api/v1/scoreboards/1/stats:refresh"))
        rec.step("v1 refresh crew board", lambda: _v1(
            http, rec, "/api/v1/scoreboards/3/stats:refresh"))
        rec.step("answer routes", lambda: startgg.answer(autonaApp))
        rec.step("board clear", board.CommandClearAll)
        rec.step("load set D", lambda: board.ChangeSetData(_set(
            [_entrant("KKK", 13, 2)], [_entrant("LLL", 14, 6)])))
        rec.step("web reset players", lambda: WebOps.reset_players(1))
        rec.step("load set E", lambda: board.ChangeSetData(_set(
            [_entrant("MMM", 15, 2)], [_entrant("NNN", 16, 6)])))
        rec.step("web clear all", lambda: WebOps.clear_all(1))
        rec.step("load set F", lambda: board.ChangeSetData(_set(
            [_entrant("OOO", 17, 2)], [_entrant("PPP", 18, 6)])))
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes):
            rec.step("reset all inputs", board.ResetAllInputs)
        rec.step("load set G", lambda: board.ChangeSetData(_set(
            [_entrant("QQQ", 19, 2)], [_entrant("RRR", 20, 6)])))
        rec.step("answer the rest", lambda: startgg.answer(
            autonaApp, lambda f: f["step"] == "load set G"))
        rec.step("stream queue", lambda: _streamQueue("singles"))

        def noTournament():
            startgg.provider.provider = None
        rec.step("tournament closed", noTournament)
        rec.step("legacy recent, no tournament", lambda: _legacy(
            http, rec, "/scoreboard1-stats-recent-sets"))
        rec.step("v1 refresh, no tournament", lambda: _v1(
            http, rec, "/api/v1/scoreboards/1/stats:refresh"))
        rec.step("load set H, no tournament", lambda: board.ChangeSetData(
            _set([_entrant("SSS", 21, 2)], [_entrant("TTT", 23, 6)])))

        recorded = {"steps": rec.steps, "boards": rec.boards(1)}

    _check("singles", recorded)


def test_a_team_board(autonaApp, startgg, http):
    from src import AutonaWebOperations as WebOps

    autonaApp.board(1)
    board = autonaApp.board(2)
    autonaApp.settle()

    with _Recorder(autonaApp, startgg) as rec:
        rec.step("load a 2v2 set", lambda: board.ChangeSetData(_set(
            [_entrant("A1", 31, 1), _entrant("A2", 32, 1)],
            [_entrant("B1", 41, 4), _entrant("B2", 42, 4)])))
        rec.step("move a player down", lambda: board._MovePlayer(
            board.team1playerWidgets[0], 1))
        rec.step("move it back up", lambda: board._MovePlayer(
            board.team1playerWidgets[1], -1))
        rec.step("web swap", lambda: WebOps.swap_teams(2))
        rec.step("board clear", board.CommandClearAll)
        rec.step("load a 1v1 set", lambda: board.ChangeSetData(_set(
            [_entrant("S1", 51, 1)], [_entrant("S2", 52, 8)])))
        rec.step("answer", lambda: startgg.answer(autonaApp))
        rec.step("load a 2v2 set again", lambda: board.ChangeSetData(_set(
            [_entrant("A1", 31, 1), _entrant("A2", 32, 1)],
            [_entrant("B1", 41, 4), _entrant("B2", 42, 4)])))
        rec.step("load the same 2v2 roster without seeds",
                 lambda: board.ChangeSetData(_set(
                     [{"gamerTag": "A1", "id": [31, 310]},
                      {"gamerTag": "A2", "id": [32, 320]}],
                     [{"gamerTag": "B1", "id": [41, 410]},
                      {"gamerTag": "B2", "id": [42, 420]}])))
        rec.step("legacy recent", lambda: _legacy(
            http, rec, "/scoreboard2-stats-recent-sets"))
        rec.step("legacy last both", lambda: _legacy(
            http, rec, "/scoreboard2-stats-last-sets-both"))
        rec.step("legacy history both", lambda: _legacy(
            http, rec, "/scoreboard2-stats-history-sets-both"))
        rec.step("legacy upset", lambda: _legacy(
            http, rec, "/scoreboard2-stats-upset-factor"))
        rec.step("v1 refresh", lambda: _v1(
            http, rec, "/api/v1/scoreboards/2/stats:refresh"))
        rec.step("stream queue, two boards", lambda: _streamQueue("team"))

        recorded = {"steps": rec.steps, "boards": rec.boards(1, 2)}

    _check("team", recorded)


def test_routes_reach_a_board_that_is_not_built(autonaApp, startgg, http):
    """The legacy routes and v1 build board 2 first, as they always did."""
    autonaApp.board(1)
    autonaApp.settle()

    with _Recorder(autonaApp, startgg) as rec:
        rec.step("legacy recent on board 2", lambda: _legacy(
            http, rec, "/scoreboard2-stats-recent-sets"))
        rec.step("legacy last 1 on board 2", lambda: _legacy(
            http, rec, "/scoreboard2-stats-last-sets-1"))
        recorded = {"steps": [dict(s, deltas=[d for d in s["deltas"]
                                              if d != "BIG"])
                              for s in rec.steps],
                    "boards": rec.boards(2)}
        built = 2 in autonaApp.manager.scoreboards

    assert built
    _check("unbuilt board", recorded)
