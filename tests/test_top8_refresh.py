"""The Top 8 import from Start.gg (Top8Service.RefreshFromProvider, ADR-005 S3e-8).

The import used to be the TOP 8 panel's (its load button, the hotkey and the
web routes all reached AutonaTop8Widget.RefreshPhaseGroup, building the panel
first). golden.json was recorded through that panel on the last tree before
the move (cff44408), and every way in now has to write the same: core
directly, the panel's button, the hotkey with no panel, and -- in the
headless shell -- the legacy route, the Socket.IO event and /api/v1. What is
compared: the fetches, the countries looked up, every StateManager.Set with
its value's key order, the deltas, the subtree afterwards.

    # only on that tree; on this one it would record the core path
    AUTONA_RECORD_TOP8=1 python -m pytest tests/test_top8_refresh.py -k record

Also here: one import at a time across every way in, a failed or empty
fetch writing nothing, and the panel following the state (it renders the
import and its own edits, writes only the field edited, keeps what the
operator types unless the import wrote that very field).

Start.gg is a fake provider (the two reads the import makes); the country
lookup is a deterministic stand-in for CountryCatalog.BasicCountryInfo; the
characters are the harness game's (autonaApp.loadCharacters).
"""
import copy
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.core import Top8Model as top8
from tests.fixtures.write_recording import WriteRecorder, encode

RECORD = os.environ.get("AUTONA_RECORD_TOP8") == "1"
GOLDEN_PATH = (Path(__file__).resolve().parent / "fixtures" / "top8_refresh"
               / "golden.json")

GAME = "harness_game"


# ── Start.gg-shaped data (what GetTournamentPhaseGroup returns) ──────────

def _player(tag, prefix=None, country=None, mains=None):
    # ProcessEntrantData's shape; the import reads gamerTag / prefix /
    # country_code / mains.
    return {"prefix": prefix, "gamerTag": tag, "name": f"{tag} real",
            "country_code": country, "state_code": None,
            "mains": mains if mains is not None else {},
            "id": [len(tag), 1]}


ENTRANTS = [
    {"players": [_player("Alpha", "AA", "JP", {GAME: [["mario", 2]]})]},
    {"players": [_player("Bravo", None, "US", {GAME: [["Luigi", 0]]})]},
    {"players": [_player("Charlie", "CC", None,
                         {GAME: [["peach", 1], ["mario", 0]]})]},
    {"players": [_player("Delta", "", "FR", {"other_game": [["mario", 0]]})]},
    {"players": [_player("Echo", None, "ZZ", {GAME: [["nobody", 0]]})]},
    {},  # a seed Start.gg returned no entrant for
    {"players": [_player("Golf", "G", "JP", {GAME: []})]},
    {"name": "Team Hotel", "players": [
        _player("Hotel", "H", "US", {GAME: [["luigi", 3]]}),
        _player("Hotel2", None, "JP", {})]},
]


def _seedSlot(seed):
    return {"prereqType": "seed", "prereqId": seed, "prereqPlacement": None,
            "seedNum": seed}


def _fromSet(setId, placement, seedNum=None):
    return {"prereqType": "set", "prereqId": setId,
            "prereqPlacement": placement, "seedNum": seedNum}


def _set(setId, slots, score=(None, None), finished=False, identifier=None):
    return {"id": setId, "identifier": identifier or str(setId),
            "score": list(score), "finished": finished, "slots": slots}


def _group(results, reset=True, wiredLosers=True, entrants=ENTRANTS,
           seeds=None):
    """A Top 8 double elimination phase group, as the provider returns it.

    Seeds 1-4 start winners side (1v4, 2v3), 5-8 losers side (5v8, 6v7).
    Start.gg lists the losers quarter-finals the other way round from the
    fixed topology (the first one receives WSF1's loser), the losers
    semi-final and final with their slots swapped, so the import's wiring
    and orientation checks both have something to do. `results` maps a set
    id to (score, finished); `seeds` a set id to the seeds of its two
    entrants, which Start.gg fills in on a slot once its entrant is known
    (without them the import has only the bracket's own propagation).
    """
    def s(setId, slots):
        score, finished = results.get(setId, ((None, None), False))
        known = (seeds or {}).get(setId)
        if known is not None:
            slots = [dict(slot, seedNum=seed)
                     for slot, seed in zip(slots, known)]
        return _set(setId, slots, score, finished)

    loserOf = (lambda setId: _fromSet(setId, 2)) if wiredLosers else (
        lambda setId: {"prereqType": "bye", "prereqId": None,
                       "prereqPlacement": None, "seedNum": None})
    sets = {
        "1": [s(1001, [_seedSlot(1), _seedSlot(4)]),
              s(1002, [_seedSlot(2), _seedSlot(3)])],
        "2": [s(1003, [_fromSet(1001, 1), _fromSet(1002, 1)])],
        "3": [s(1004, [_fromSet(1003, 1), _fromSet(1008, 1)])],
        "-1": [s(1006, [_seedSlot(5), _seedSlot(8)]),
               s(1007, [_seedSlot(6), _seedSlot(7)])],
        "-2": [s(1009, [_fromSet(1007, 1), loserOf(1001)]),
               s(1010, [_fromSet(1006, 1), loserOf(1002)])],
        "-3": [s(1011, [_fromSet(1009, 1), _fromSet(1010, 1)])],
        "-4": [s(1008, [_fromSet(1003, 2), _fromSet(1011, 1)])],
    }
    if reset:
        sets["4"] = [s(1005, [_fromSet(1004, 1), _fromSet(1004, 2)])]
    return {"entrants": copy.deepcopy(entrants), "sets": sets,
            "progressionsIn": [], "progressionsOut": None,
            "seedMap": [1, 2, 3, 4, 5, 6, 7, 8]}


# Every set played, the grand final reset included. Scores are Start.gg's
# slot order.
COMPLETE = {
    1001: ((2, 1), True),    # seed 1 beats 4
    1002: ((0, 2), True),    # seed 3 beats 2
    1006: ((2, 0), True),    # seed 5 beats 8
    1007: ((1, 2), True),    # seed 7 beats 6
    1003: ((3, 1), True),    # WF: 1 beats 3
    1010: ((0, 2), True),    # LQF1: 2 beats 5
    1009: ((2, 1), True),    # LQF2: 7 beats 4
    1011: ((1, 2), True),    # LSF (slots swapped): 2 beats 7
    1008: ((3, 1), True),    # LF (slots swapped): 3 beats 2
    1004: ((1, 3), True),    # GF: 3 (losers side) beats 1 -> reset
    1005: ((3, 2), True),    # reset
}

# The seeds Start.gg reports on each slot of COMPLETE.
COMPLETE_SEEDS = {
    1003: [1, 3], 1004: [1, 3], 1005: [3, 1],
    1009: [7, 4], 1010: [5, 2], 1011: [7, 2], 1008: [3, 2],
}

IN_PROGRESS = {
    1001: ((2, 0), True),
    1002: ((1, 1), False),   # being played
    1006: ((0, 2), True),
    1003: ((None, None), False),
}

SMALL = {
    "entrants": copy.deepcopy(ENTRANTS[:4]),
    "sets": {
        "1": [_set(1, [_seedSlot(1), _seedSlot(4)], (2, 0), True),
              _set(2, [_seedSlot(2), _seedSlot(3)], (2, 0), True)],
        "2": [_set(3, [_fromSet(1, 1), _fromSet(2, 1)])],
        "3": [_set(4, [_fromSet(3, 1), _fromSet(6, 1)])],
        "-1": [_set(5, [_fromSet(1, 2), _fromSet(2, 2)])],
        "-2": [_set(6, [_fromSet(5, 1), _fromSet(3, 2)])],
    },
    "progressionsIn": [], "progressionsOut": None,
}


def _phases(*groups):
    return [{"id": 900 + index, "name": f"Phase {index}",
             "groups": [{"id": groupId, "name": f"Pool {groupId}",
                         "bracketType": bracketType}
                        for groupId, bracketType in phaseGroups]}
            for index, phaseGroups in enumerate(groups)]


TOP8_PHASES = _phases([(50, "DOUBLE_ELIMINATION")])

# label -> (phases, {group id: phase group}, game loaded)
SCENARIOS = {
    "complete with reset": (TOP8_PHASES, {50: _group(COMPLETE)}, True),
    "complete, seeds known": (
        TOP8_PHASES, {50: _group(COMPLETE, seeds=COMPLETE_SEEDS)}, True),
    "in progress": (TOP8_PHASES, {50: _group(IN_PROGRESS, reset=False)}, True),
    "losers unwired": (
        TOP8_PHASES, {50: _group(COMPLETE, wiredLosers=False)}, True),
    "last DE phase, first DE group": (
        _phases([(21, "DOUBLE_ELIMINATION"), (22, "DOUBLE_ELIMINATION")],
                [(31, "ROUND_ROBIN"), (32, "DOUBLE_ELIMINATION"),
                 (33, "DOUBLE_ELIMINATION")],
                []),
        {21: _group(IN_PROGRESS), 32: _group(COMPLETE),
         33: _group(IN_PROGRESS)}, True),
    "no game loaded": (TOP8_PHASES, {50: _group(COMPLETE)}, False),
    "too small": (TOP8_PHASES, {50: SMALL}, True),
    "empty phase group": (TOP8_PHASES, {50: {}}, True),
    "no double elimination phase": (
        _phases([(60, "ROUND_ROBIN")], [(61, "SWISS")]), {}, True),
    "no phases": ([], {}, True),
}


class FakeProvider:
    """The tournament provider's two reads, recording what was asked."""

    url = "https://www.start.gg/tournament/harness/event/top-8"
    videogame = None

    def __init__(self, phases, groups):
        self._phases = phases
        self._groups = groups
        self.calls = []

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None):
        self.calls.append(["phases"])
        return copy.deepcopy(self._phases)

    def GetTournamentPhaseGroup(self, id, progress_callback=None,
                                cancel_event=None):
        self.calls.append(["group", id])
        return copy.deepcopy(self._groups.get(id, {}))


COUNTRIES = {"JP": "Japan", "US": "United States", "FR": "France"}


def _country(code):
    # The shape a country row has (name / code / asset), in a fixed order.
    name = COUNTRIES.get(code)
    if name is None:
        return {}
    return {"name": name, "en_name": name, "code": code,
            "asset": f"./assets/country_flag/{code.lower()}.png"}


def _baseline():
    """What top8.* holds before each import: last session's bracket."""
    state = top8.DefaultState()
    state["phaseLabel"] = "FINALS"
    for slot in top8.PLAYER_SLOTS:
        state["players"][slot]["name"] = f"Old {slot}"
    state["players"]["2"]["sponsor"] = "OLD"
    state["players"]["3"]["country"] = _country("FR")
    state["matches"]["wsf1"]["score"] = [1, 0]
    state["matches"]["wsf1"]["finished"] = True
    state["matches"] = top8.Recompute(state["matches"])
    return state


# ── harness ──────────────────────────────────────────────────────────────

def _state():
    from src.core.StateManager import StateManager
    return StateManager


@pytest.fixture
def countries(monkeypatch):
    """The country lookup the import makes, deterministic and recorded."""
    from src.core.CountryCatalog import CountryCatalog
    calls = []

    def basicCountryInfo(code):
        calls.append(code)
        return _country(code)

    monkeypatch.setattr(CountryCatalog.instance, "BasicCountryInfo",
                        basicCountryInfo)
    return calls


@pytest.fixture
def bracket(autonaApp):
    """top8.* restored afterwards, and no tournament provider left behind."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.DictHelper import deep_clone
    saved = deep_clone(_state().Get("top8"))
    provider = AutonaTournamentDataProvider.instance.provider
    try:
        yield autonaApp
    finally:
        _drain(autonaApp)
        AutonaTournamentDataProvider.instance.provider = provider
        if saved is None:
            _state().Unset("top8")
        else:
            _state().Set("top8", saved)
        autonaApp.settle()


def _drain(app):
    """Let the provider's jobs (the task executor's, ADR-005 S3f-2) and what
    they hand the GUI thread finish (a fetch, then the build, each delivered
    through queued signals)."""
    from qtpy.QtCore import QCoreApplication
    from qtpy.QtWidgets import QApplication
    from src.core.TaskExecutor import TaskExecutor
    executor = TaskExecutor.instance
    quiet = 0
    for _ in range(600):
        idle = executor.drain(0.02)
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents()
        quiet = quiet + 1 if idle else 0
        if quiet >= 8:
            break
    app.settle()


def _buildPanel(app):
    from src.AutonaTop8Widget import AutonaTop8Widget
    panel = AutonaTop8Widget()
    app.settle()
    return panel


def _dropPanel(app, panel):
    from qtpy.QtCore import QCoreApplication, QEvent
    from src.AutonaTop8Widget import AutonaTop8Widget
    if AutonaTop8Widget.instance is panel:
        AutonaTop8Widget.instance = None
    panel.setParent(None)
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(panel, QEvent.Type.DeferredDelete)
    app.settle()


def _useProvider(label):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    phases, groups, _game = SCENARIOS[label]
    provider = FakeProvider(phases, groups)
    AutonaTournamentDataProvider.instance.provider = provider
    return provider


def _reset(app):
    _state().Set("top8", _baseline())
    app.settle()


def _record(app, countries, provider, trigger):
    countries.clear()
    with WriteRecorder() as recorder:
        trigger()
        _drain(app)
    recorded = recorder.Result()
    recorded.update({
        "fetches": provider.calls,
        "countries": list(countries),
        "top8": encode(_state().Get("top8")),
    })
    return recorded


def _orderedLabels():
    # The scenario without a game first: the harness game, once installed,
    # stays for the rest of the test.
    return sorted(SCENARIOS, key=lambda label: SCENARIOS[label][2])


@pytest.mark.skipif(not RECORD, reason="recording only")
def test_record(bracket, countries):
    """Through the panel's load button, as the operator imports."""
    golden = {"baseline": encode(_baseline()), "scenarios": {}}
    for label in _orderedLabels():
        if SCENARIOS[label][2]:
            bracket.loadCharacters()
        _reset(bracket)
        provider = _useProvider(label)
        panel = _buildPanel(bracket)
        try:
            golden["scenarios"][label] = _record(
                bracket, countries, provider, panel.btLoadTop8.click)
        finally:
            _dropPanel(bracket, panel)
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")


# ── replays: every way in writes what the panel's import wrote ───────────

def _golden(label):
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return golden["scenarios"][label]


def _assertGolden(recorded, label):
    expected = _golden(label)
    for key in ("fetches", "countries", "sets", "deltas", "bigChanges",
                "top8"):
        assert recorded[key] == expected[key], f"{label}: {key}"


def _replay(app, countries, label, trigger, gameLoaded):
    """One scenario through `trigger`, from the baseline. `gameLoaded`:
    whether the harness game was installed for an earlier scenario."""
    if SCENARIOS[label][2] and not gameLoaded:
        app.loadCharacters()
    _reset(app)
    provider = _useProvider(label)
    return _record(app, countries, provider, trigger)


def test_the_core_import_writes_what_the_panel_did(bracket, countries):
    """Top8Service.RefreshFromProvider, through the shell's fetcher (a
    provider job on the task executor), no panel built: the recording of the panel's
    import, scenario by scenario -- what was fetched, the countries looked
    up, every Set with its key order, the deltas, the subtree."""
    from src.core import Top8Service
    gameLoaded = False
    for label in _orderedLabels():
        answers = []
        recorded = _replay(
            bracket, countries, label,
            lambda: answers.append(Top8Service.RefreshFromProvider()),
            gameLoaded)
        gameLoaded = gameLoaded or SCENARIOS[label][2]
        assert answers == [Top8Service.STARTED], label
        assert not Top8Service.Refreshing(), label
        _assertGolden(recorded, label)


def test_the_panel_button_imports_through_core_and_shows_it(
        bracket, countries):
    """The load button, panel built: the same writes -- the panel renders
    the import and writes nothing of its own -- and it shows the result."""
    gameLoaded = False
    for label in _orderedLabels():
        if SCENARIOS[label][2] and not gameLoaded:
            bracket.loadCharacters()
            gameLoaded = True
        _reset(bracket)
        provider = _useProvider(label)
        panel = _buildPanel(bracket)
        try:
            recorded = _record(
                bracket, countries, provider, panel.btLoadTop8.click)
            _assertGolden(recorded, label)
            players = _state().Get("top8.players")
            assert {slot: w["name"].text()
                    for slot, w in panel.playerInputs.items()} \
                == {slot: players[slot]["name"] for slot in players}, label
            matches = _state().Get("top8.matches")
            assert {mid: [row["s1"].value(), row["s2"].value()]
                    for mid, row in panel.matchRows.items()} \
                == {mid: matches[mid]["score"] for mid in matches}, label
            assert panel.phaseLabelEdit.text() == "FINALS"
        finally:
            _dropPanel(bracket, panel)


ENTRY_LABEL = "complete, seeds known"


def test_the_hotkey_imports_without_the_panel(bracket, countries):
    from src.AutonaHotkeys import AutonaHotkeys
    from src.AutonaTop8Widget import AutonaTop8Widget
    recorded = _replay(bracket, countries, ENTRY_LABEL,
                       AutonaHotkeys.signals.refresh_phase_group.emit, False)
    _assertGolden(recorded, ENTRY_LABEL)
    # Not built to answer it (it used to be, by the window).
    assert AutonaTop8Widget.instance is None


# The web layer's fixtures: the real Flask app with no board-dependent
# actions (the headless WebServer), and the headless board host.
from tests.test_web_operations import headless, server  # noqa: E402,F401


def _client(server):
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    return client


def test_the_legacy_route_imports_headless(
        server, headless, bracket, countries):
    client = _client(server)
    answers = []

    def trigger():
        response = client.get("/update-bracket")
        answers.append((response.status_code,
                        response.get_data(as_text=True)))

    recorded = _replay(headless, countries, ENTRY_LABEL, trigger, False)
    assert answers == [(200, "OK")]
    _assertGolden(recorded, ENTRY_LABEL)


def test_the_socketio_event_imports_headless(
        server, headless, bracket, countries):
    app, socketio = server
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=flask_client)
    ws.get_received()

    recorded = _replay(headless, countries, ENTRY_LABEL,
                       lambda: ws.emit("update_bracket", "{}"), False)
    received = ws.get_received()
    assert [(m["name"], m["args"]) for m in received
            if m["name"] == "update_bracket"] == [("update_bracket", ["OK"])]
    _assertGolden(recorded, ENTRY_LABEL)


def test_the_v1_refresh_imports_headless(
        server, headless, bracket, countries):
    """POST /api/v1/bracket:refresh in the headless shell (it answered 503
    until ADR-005 S3e-8): 202, and top8.* is written as the desktop did."""
    client = _client(server)
    answers = []

    def trigger():
        response = client.post("/api/v1/bracket:refresh", json={})
        answers.append((response.status_code, response.get_json()["data"]))

    recorded = _replay(headless, countries, ENTRY_LABEL, trigger, False)
    assert answers == [(202, {"accepted": True,
                              "operation": "bracket.refresh"})]
    _assertGolden(recorded, ENTRY_LABEL)


# ── one import at a time; what comes back short ──────────────────────────

class BlockingProvider(FakeProvider):
    """Holds the import in its first fetch until released."""

    def __init__(self, phases, groups):
        super().__init__(phases, groups)
        self.entered = threading.Event()
        self.release = threading.Event()

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None):
        self.entered.set()
        assert self.release.wait(10), "the test never released the import"
        return super().GetTournamentPhases()


def test_a_second_request_while_one_runs_is_dropped(
        server, headless, bracket, countries):
    from src.AutonaHotkeys import AutonaHotkeys
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core import Top8Service
    headless.loadCharacters()
    _reset(headless)
    phases, groups, _ = SCENARIOS[ENTRY_LABEL]
    provider = BlockingProvider(phases, groups)
    AutonaTournamentDataProvider.instance.provider = provider
    client = _client(server)
    done = []

    try:
        assert Top8Service.RefreshFromProvider(
            done=lambda: done.append(1)) == Top8Service.STARTED
        assert provider.entered.wait(10)
        assert Top8Service.Refreshing()

        # Every way in shares the flag.
        assert Top8Service.RefreshFromProvider() == Top8Service.BUSY
        response = client.post("/api/v1/bracket:refresh", json={})
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "CONFLICT"
        assert client.get("/update-bracket").get_data(as_text=True) == "OK"
        AutonaHotkeys.signals.refresh_phase_group.emit()
        assert done == []
    finally:
        provider.release.set()
        _drain(headless)

    assert done == [1]
    assert not Top8Service.Refreshing()
    assert provider.calls == [["phases"], ["group", 50]]   # one import
    assert _state().Get("top8") == json.loads(_golden(ENTRY_LABEL)["top8"])
    # Released: the next request imports again.
    assert Top8Service.RefreshFromProvider() == Top8Service.STARTED
    _drain(headless)
    assert provider.calls[2:] == [["phases"], ["group", 50]]


class FailingProvider(FakeProvider):
    def GetTournamentPhases(self, progress_callback=None, cancel_event=None):
        self.calls.append(["phases"])
        raise RuntimeError("start.gg is down")


class NoneProvider(FakeProvider):
    """What a provider gives back when a request failed and it did not
    turn the failure into an empty result."""

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None):
        self.calls.append(["phases"])
        return copy.deepcopy(self._phases) if self._phases else None

    def GetTournamentPhaseGroup(self, id, progress_callback=None,
                                cancel_event=None):
        self.calls.append(["group", id])
        return None


@pytest.mark.parametrize("make", [
    lambda: FailingProvider(TOP8_PHASES, {}),
    lambda: NoneProvider([], {}),
    lambda: NoneProvider(TOP8_PHASES, {}),
], ids=["raises", "no phases", "no group"])
def test_a_failed_fetch_writes_nothing_and_releases(bracket, make):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core import Top8Service
    _reset(bracket)
    AutonaTournamentDataProvider.instance.provider = make()
    done = []
    with WriteRecorder() as recorder:
        assert Top8Service.RefreshFromProvider(
            done=lambda: done.append(1)) == Top8Service.STARTED
        _drain(bracket)
    assert recorder.sets == []
    assert done == [1]
    assert not Top8Service.Refreshing()
    assert encode(_state().Get("top8")) == encode(_baseline())


@pytest.mark.parametrize("broken", ["push", "import"])
def test_a_failing_status_still_imports_and_releases(
        bracket, countries, monkeypatch, broken):
    """The shell's fetcher shows a footer status around the job. Showing it
    can fail before the job has even begun (the status manager's import,
    its push); the import must still run and the flag still come down --
    nothing else ever releases it, and every later request from every way
    in would answer BUSY for the rest of the session."""
    import sys
    from src.core import Top8Service
    if broken == "push":
        from src.Helpers.AutonaStatusManager import AutonaStatusManager

        def push(*args, **kwargs):
            raise RuntimeError("the footer is gone")

        monkeypatch.setattr(AutonaStatusManager, "push", staticmethod(push))
    else:
        # None in sys.modules: the fetcher's lazy import raises.
        monkeypatch.setitem(sys.modules, "src.Helpers.AutonaStatusManager",
                            None)
    _reset(bracket)
    provider = _useProvider(ENTRY_LABEL)
    done = []
    try:
        assert Top8Service.RefreshFromProvider(
            done=lambda: done.append(1)) == Top8Service.STARTED
        _drain(bracket)
        assert done == [1]
        assert not Top8Service.Refreshing()
        assert provider.calls == [["phases"], ["group", 50]]
        assert _state().Get("top8.players.1.name") != "Old 1"   # imported
        assert Top8Service.RefreshFromProvider() == Top8Service.STARTED
        _drain(bracket)
        assert not Top8Service.Refreshing()
    finally:
        # A stuck flag must not leak into the tests after this one.
        Top8Service._refreshing = False


def test_without_a_tournament_nothing_starts(
        server, headless, bracket, monkeypatch):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core import Top8Service
    saved = AutonaTournamentDataProvider.instance.provider
    AutonaTournamentDataProvider.instance.provider = None
    try:
        client = _client(server)
        done = []

        assert Top8Service.RefreshFromProvider(
            done=lambda: done.append(1)) == Top8Service.NO_TOURNAMENT
        assert done == [1] and not Top8Service.Refreshing()
        response = client.post("/api/v1/bracket:refresh", json={})
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "NOT_READY"
        assert client.get("/update-bracket").get_data(as_text=True) == "OK"

        # No shell registered a fetcher: the same answer.
        _useProvider(ENTRY_LABEL)
        monkeypatch.setattr(Top8Service, "fetcher", None)
        assert Top8Service.RefreshFromProvider() == Top8Service.NO_TOURNAMENT
        assert not Top8Service.Refreshing()
    finally:
        AutonaTournamentDataProvider.instance.provider = saved


def test_the_target_is_the_last_double_elimination_phase():
    from src.core import Top8Service
    phases = SCENARIOS["last DE phase, first DE group"][0]
    assert Top8Service.TargetPhaseGroup(phases)["id"] == 32
    assert Top8Service.TargetPhaseGroup(TOP8_PHASES)["id"] == 50
    assert Top8Service.TargetPhaseGroup([]) is None
    assert Top8Service.TargetPhaseGroup(None) is None
    assert Top8Service.TargetPhaseGroup(
        _phases([(60, "ROUND_ROBIN")])) is None
    # A group without an id cannot be fetched.
    assert Top8Service.TargetPhaseGroup(
        [{"groups": [{"id": None, "bracketType": "DOUBLE_ELIMINATION"}]}]) \
        is None


# ── the panel follows the state ──────────────────────────────────────────

@pytest.fixture
def panel(bracket):
    _reset(bracket)
    panel = _buildPanel(bracket)
    try:
        yield panel
    finally:
        _dropPanel(bracket, panel)


def test_a_panel_edit_writes_only_its_field(bracket, panel):
    """A change the panel has not shown yet (its render is deferred) when
    the operator finishes an edit: the edit writes its own field, and the
    change stays (the panel used to republish all eight players from its
    inputs)."""
    from src.core import Top8Service
    Top8Service.SetPlayerText("5", "name", "Newer")
    assert panel.playerInputs["5"]["name"].text() == "Old 5"   # not yet

    edit = panel.playerInputs["1"]["name"]
    edit.setText("  Typed ")
    with WriteRecorder() as recorder:
        edit.editingFinished.emit()
        bracket.settle()

    assert recorder.sets == [["top8.players.1.name", '"Typed"']]
    assert _state().Get("top8.players.5.name") == "Newer"
    assert panel.playerInputs["5"]["name"].text() == "Newer"
    # The match rows name whoever the state puts there.
    assert panel.matchRows["wsf1"]["p1"].text() == "Typed"


def test_a_render_writes_nothing(bracket, panel):
    from src.core import Top8Service
    with WriteRecorder() as recorder:
        Top8Service.SetPlayerCountry("2", _country("JP"))
        bracket.settle()
        panel._onGameChanged()
        bracket.settle()
        _state().observers.state_big_change.notify()
        bracket.settle()
    assert [key for key, _ in recorder.sets] == ["top8.players.2.country"]
    assert _state().Get("top8.players.3.country") == _country("FR")


def test_a_character_and_its_skin_are_kept(bracket):
    """Picking a character writes its first skin, picking a skin writes
    that one, and a later render keeps the skin (the panel used to put the
    first skin back whenever it reloaded)."""
    from qtpy.QtCore import Qt
    from src.core import Top8Service
    bracket.loadCharacters()
    _reset(bracket)
    panel = _buildPanel(bracket)
    try:
        w = panel.playerInputs["2"]
        luigi = next(row for row in range(w["char"].count())
                     if (w["char"].itemData(row, Qt.ItemDataRole.UserRole)
                         or {}).get("en_name") == "Luigi")
        with WriteRecorder() as recorder:
            w["char"].setCurrentIndex(luigi)
            bracket.settle()
        assert [key for key, _ in recorder.sets] \
            == ["top8.players.2.characters"]
        assert w["color"].currentIndex() == 0

        with WriteRecorder() as recorder:
            w["color"].setCurrentIndex(2)
            bracket.settle()
        skin = w["color"].itemData(2, Qt.ItemDataRole.UserRole)
        assert recorder.sets == [
            ["top8.players.2.characters", encode({"1": skin})]]

        with WriteRecorder() as recorder:
            Top8Service.SetPlayerText("2", "name", "Luigi main")
            bracket.settle()
        assert [key for key, _ in recorder.sets] == ["top8.players.2.name"]
        assert w["char"].currentIndex() == luigi
        assert w["color"].currentIndex() == 2
    finally:
        _dropPanel(bracket, panel)


def test_staged_scores_survive_a_player_edit_and_apply(bracket, panel):
    from src.core import Top8Service
    row = panel.matchRows["wsf2"]
    row["s1"].setValue(2)
    Top8Service.SetPlayerText("3", "name", "Renamed")
    bracket.settle()
    assert row["s1"].value() == 2           # staged, not applied
    assert row["p1"].text() == "Renamed"

    panel.btApplyScores.click()
    bracket.settle()
    assert _state().Get("top8.matches.wsf2.score") == [2, 0]
    assert _state().Get("top8.matches.wsf2.finished") is True
    assert row["s1"].value() == 2


def test_a_cleared_caption_stands_for_the_default(bracket, panel):
    edit = panel.phaseLabelEdit
    assert edit.text() == "FINALS"
    edit.setText("")
    edit.editingFinished.emit()
    bracket.settle()
    assert _state().Get("top8.phaseLabel") == top8.DEFAULT_PHASE_LABEL
    assert edit.text() == ""
    # Another render leaves the emptied field as it is.
    from src.core import Top8Service
    Top8Service.SetPlayerText("1", "name", "Elsewhere")
    bracket.settle()
    assert edit.text() == ""


def test_a_new_panel_shows_the_default_caption_as_text(bracket):
    """As the panel always did: the caption the state holds is the field's
    text, the default "TOP 8" included (not left to the placeholder)."""
    from src.core import Top8Service
    _reset(bracket)
    Top8Service.SetPhaseLabel("")
    bracket.settle()
    panel = _buildPanel(bracket)
    try:
        assert panel.phaseLabelEdit.text() == top8.DEFAULT_PHASE_LABEL
        # Finishing it as shown is not an edit.
        with WriteRecorder() as recorder:
            panel.phaseLabelEdit.editingFinished.emit()
            bracket.settle()
        assert recorder.sets == []
    finally:
        _dropPanel(bracket, panel)


# Real focus, on the panel shown offscreen (as the commentary panel's
# tests do: tests/test_commentary_service.py).

@contextmanager
def _onScreen(widget, app):
    from qtpy.QtCore import QCoreApplication, QEvent
    from qtpy.QtWidgets import QWidget
    window = widget.window()
    window.show()
    window.activateWindow()
    app.settle()
    try:
        yield
    finally:
        standIn = QWidget()
        standIn.show()
        standIn.activateWindow()
        app.settle()
        window.hide()
        standIn.hide()
        standIn.deleteLater()
        QCoreApplication.sendPostedEvents(standIn, QEvent.Type.DeferredDelete)
        app.settle()


def _focus(widget, app):
    widget.setFocus()
    app.settle()
    assert widget.isVisible() and widget.hasFocus(), \
        "the harness could not focus the field"


def test_an_import_wins_over_typing_in_its_field(bracket, countries, panel):
    """The operator is typing in slot 1's name when an import writes it:
    the field is left alone while they are in it, finishing it does not
    write the typing over the import, and the field shows the import once
    left (HeldBackText)."""
    from qtpy.QtTest import QTest
    from src.core import Top8Service
    edit = panel.playerInputs["1"]["name"]
    other = panel.playerInputs["2"]["sponsor"]
    _useProvider("no game loaded")

    with _onScreen(panel, bracket):
        _focus(edit, bracket)
        QTest.keyClicks(edit, "x")
        assert Top8Service.RefreshFromProvider() == Top8Service.STARTED
        _drain(bracket)
        imported = _state().Get("top8.players.1.name")
        assert imported not in ("Old 1", "Old 1x")
        assert edit.text() == "Old 1x", "the field being edited was redrawn"

        with WriteRecorder() as recorder:
            _focus(other, bracket)
        assert recorder.sets == []
        assert edit.text() == imported


def test_typing_survives_a_render_of_other_fields(bracket, panel):
    from qtpy.QtTest import QTest
    from src.core import Top8Service
    edit = panel.playerInputs["1"]["name"]
    other = panel.playerInputs["2"]["sponsor"]

    with _onScreen(panel, bracket):
        _focus(edit, bracket)
        QTest.keyClicks(edit, "y")
        Top8Service.SetPlayerText("4", "name", "Elsewhere")
        bracket.settle()
        assert panel.playerInputs["4"]["name"].text() == "Elsewhere"
        with WriteRecorder() as recorder:
            _focus(other, bracket)
        assert recorder.sets == [["top8.players.1.name", '"Old 1y"']]


# ── a game switch empties the characters, in either shell ────────────────

# The golden fixture games (tgame, game2) installed into the harness.
from tests.test_game_asset_manager_models import goldenGames  # noqa: E402,F401

SEEDED_CHARACTERS = {"3": {"1": {"en_name": "Somebody", "assets": {}}},
                     "8": {"1": {"en_name": "Someone else"}}}


def _seedCharacters():
    for slot, characters in SEEDED_CHARACTERS.items():
        _state().Set(f"top8.players.{slot}.characters", copy.deepcopy(characters))


def _top8Characters():
    players = _state().Get("top8.players") or {}
    return {slot: player["characters"] for slot, player in players.items()
            if player.get("characters")}


def _loader(shell, manager):
    """Load a game the way `shell` does: the desktop's asset adapter, or
    the headless shell's catalog-direct loader (src/headless.py)."""
    from qtpy.QtWidgets import QApplication
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.GameAssetCatalog import GameAssetCatalog
    if shell == "desktop":
        def load(codename, reload=False):
            manager.LoadGameByCodename(codename, async_mode=False,
                                       mods_reload_mode=reload)
            manager.threadpool.waitForDone()
            QApplication.processEvents()
        return load

    from src.headless import HeadlessGames
    games = HeadlessGames(GameAssetCatalog.instance,
                          AutonaTournamentDataProvider.instance)

    def load(codename, reload=False):
        games.LoadGameByCodename(codename, async_mode=False,
                                 mods_reload_mode=reload)
        QApplication.processEvents()        # what follows a load
    return load


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_game_switch_empties_the_characters(bracket, goldenGames, shell):
    """Through each shell's own loader -- the catalog's SelectGame is the
    one path they share: the first load of a session keeps the saved
    characters, the same game again keeps them, another game empties them
    (only the slots that had one, in the switch's own save cycle)."""
    from src.core.GameAssetCatalog import GameAssetCatalog
    load = _loader(shell, goldenGames)
    GameAssetCatalog.instance.ResetGameState()    # a session's start
    _reset(bracket)
    _seedCharacters()
    bracket.settle()

    load("tgame")                                 # the restore of the pick
    assert _top8Characters() == SEEDED_CHARACTERS
    load("tgame")                                 # picked again: skipped
    load("tgame", reload=True)                    # same game, reloaded
    assert _top8Characters() == SEEDED_CHARACTERS

    with WriteRecorder() as recorder:
        load("game2")
        bracket.settle()
    assert _state().Get("game.codename") == "game2"
    assert _top8Characters() == {}
    assert [key for key, _ in recorder.sets if key.startswith("top8")] \
        == ["top8.players.3.characters", "top8.players.8.characters"]
    # recorder.deltas: each save cycle's delta as a client receives it.
    cycles = [rows for rows in map(json.loads, recorder.deltas)
              if any(row["path"][0] == "top8" for row in rows)]
    assert len(cycles) == 1
    assert ["game", "codename"] in [row["path"][:2] for row in cycles[0]]
    assert _state().Get("top8.players.3.name") == "Old 3"


def test_the_panel_shows_the_emptied_characters(bracket, goldenGames):
    """A panel open across the switch: it shows the state (no character,
    no skins) and writes nothing of its own -- the emptying is core's."""
    from qtpy.QtCore import Qt
    from src.core.GameAssetCatalog import GameAssetCatalog
    load = _loader("desktop", goldenGames)
    GameAssetCatalog.instance.ResetGameState()
    _reset(bracket)
    load("tgame")
    panel = _buildPanel(bracket)
    try:
        w = panel.playerInputs["5"]
        row = next(row for row in range(w["char"].count())
                   if (w["char"].itemData(row, Qt.ItemDataRole.UserRole)
                       or {}).get("en_name"))
        w["char"].setCurrentIndex(row)
        bracket.settle()
        assert _top8Characters().keys() == {"5"}

        with WriteRecorder() as recorder:
            load("game2")
            bracket.settle()
        assert [key for key, _ in recorder.sets if key.startswith("top8")] \
            == ["top8.players.5.characters"]
        assert _top8Characters() == {}
        assert w["char"].currentIndex() == -1
        assert w["color"].count() == 0
    finally:
        _dropPanel(bracket, panel)
