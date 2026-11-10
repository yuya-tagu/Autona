"""The crew board's /api/v1 routes (ADR-005 S4w).

Everything the Qt crew board can do, for the React SCORE page, with the Qt
board still there and unchanged:

- ``PATCH /api/v1/scoreboards/3/teams/{t}/members/{m}``              name, stock, active
- ``POST  /api/v1/scoreboards/3/teams/{t}/members/{m}/stock:adjust`` a relative stock change
- ``GET   /api/v1/scoreboards/3/post``                               the X post's text
- ``GET   /api/v1/match-names/candidates``                           what the combos offer

and what boards 1/2 already had, open to board 3: ``:swap-teams``,
``score:reset``, ``:clear``, ``:reset-players``, ``:reset-match``,
``teams/{t}/color`` and ``PATCH set``.

Through Flask's test client against the real route table, on both shells: the
board's own inputs, the route on the desktop and the route headless have to
end up in the same state, write by write.
"""
import json
import threading
import urllib.parse
import uuid
from unittest.mock import patch

import pytest
from qtpy.QtWidgets import QApplication, QMessageBox

from src import AutonaWebOperations as WebOps
from src.core import StateSchema as schema
from src.core import WebAccess
from src.core.CrewBattleService import CrewBattleService
from src.core.StateManager import StateManager
from src.core.TournamentInfoService import TournamentInfoService
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from tests.fixtures.write_recording import WriteRecorder, encode
from tests.test_api_v1 import (  # noqa: F401
    _assertRejected, _requestOnWorkerThread, client, webApp)
from tests.test_api_v1_score import _cycles, _ok, lanServer  # noqa: F401
from tests.test_crew_board import _typeCombo
from tests.test_crew_operations import (
    _focus, _fromState, _golden, _nameMember, _onScreen, _row, _setUpDesktop,
    _setUpHeadless, _shown, _typeTeamName)
from tests.test_web_access import LAN_PEER
from tests.test_web_operations import _goHeadless

BOARD = 3
URL = f"/api/v1/scoreboards/{BOARD}"
CONFIRM = {"confirm": True}


def _member(team, member, board=BOARD):
    return f"/api/v1/scoreboards/{board}/teams/{team}/members/{member}"


def _stock(team, member, board=BOARD):
    return f"{_member(team, member, board)}/stock:adjust"


def _color(team):
    return f"{URL}/teams/{team}/color"


def _send(client, route, **kwargs):
    method, path, *body = route
    return getattr(client, method)(
        path, json=body[0] if body else None, **kwargs)


def _sendOk(client, route):
    return _ok(_send(client, route))


def _board(app):
    return app.manager.scoreboards.get(BOARD)


@pytest.fixture(params=["desktop", "headless"])
def crewShell(request, autonaApp, monkeypatch):
    """The crew board on the host each shell runs it on."""
    if request.param == "desktop":
        autonaApp.board(BOARD)
    else:
        _goHeadless(autonaApp, monkeypatch)
    try:
        yield request.param
    finally:
        if request.param == "headless":
            _dropHeadlessBoards(autonaApp)


def _dropHeadlessBoards(app):
    for number in (1, 2, 3):
        StateManager.Unset(f"score.{number}")
    app.settle()


@pytest.fixture
def noBoard(autonaApp):
    """The desktop with no board built: the lazy board, not asked for yet."""
    autonaApp.dropAllBoards()
    assert _board(autonaApp) is None
    return autonaApp


# ── the board's inputs, the route on the desktop, the route headless ─────

# (what it is, the board's input, the route)
STEPS = [
    ("the count",
     lambda b: b.playerNumber.setValue(5),
     ("patch", f"{URL}/set", {"players": 5})),
    ("a name",
     lambda b: _nameMember(b, 0, 1, "A1"),
     ("patch", _member(1, 1), {"name": "A1"})),
    ("the other crew's name",
     lambda b: _nameMember(b, 1, 4, "B4"),
     ("patch", _member(2, 4), {"name": "B4"})),
    ("a stock",
     lambda b: _row(b, 0, 1)["stock"].setValue(1),
     ("patch", _member(1, 1), {"stock": 1})),
    ("a stock down",
     lambda b: _row(b, 1, 4)["stock"].setValue(2),
     ("post", _stock(2, 4), {"delta": -1})),
    ("a stock up",
     lambda b: _row(b, 1, 4)["stock"].setValue(9),
     ("post", _stock(2, 4), {"delta": 7})),
    ("playing",
     lambda b: _row(b, 0, 1)["active"].setChecked(True),
     ("patch", _member(1, 1), {"active": True})),
    ("someone else playing",
     lambda b: _row(b, 0, 2)["active"].setChecked(True),
     ("patch", _member(1, 2), {"active": True})),
    ("nobody playing",
     lambda b: _row(b, 0, 2)["active"].setChecked(False),
     ("patch", _member(1, 2), {"active": False})),
    ("the crew's name",
     lambda b: _typeTeamName(b, 0, "Alpha"),
     ("patch", f"{URL}/set", {"teamName": {"team": 1, "value": "Alpha"}})),
    ("the colour",
     lambda b: b.colorButtons[1].setColor("#123456"),
     ("post", _color(2), {"color": "#123456"})),
    ("the phase",
     lambda b: _typeCombo(b.phaseCombo, "Pools"),
     ("patch", f"{URL}/set", {"phase": "Pools"})),
    ("the pool",
     lambda b: _typeCombo(b.poolCombo, "Group A"),
     ("patch", f"{URL}/set", {"pool": "Group A"})),
    ("the match",
     lambda b: _typeCombo(b.matchCombo, "WF"),
     ("patch", f"{URL}/set", {"match": "WF"})),
    ("swap",
     lambda b: b.btSwap.click(),
     ("post", f"{URL}:swap-teams")),
    ("a stock, swapped",
     lambda b: _row(b, 0, 4)["stock"].setValue(8),
     ("patch", _member(1, 4), {"stock": 8})),
    ("reset stocks",
     lambda b: b.btReset.click(),
     ("post", f"{URL}/score:reset")),
    ("an emptied name",
     lambda b: _nameMember(b, 0, 4, ""),
     ("patch", _member(1, 4), {"name": ""})),
    ("swap back",
     lambda b: b.btSwap.click(),
     ("post", f"{URL}:swap-teams")),
    ("the count down",
     lambda b: b.playerNumber.setValue(1),
     ("patch", f"{URL}/set", {"players": 1})),
]


def _play(app, act):
    played = []
    for label, input_, route in STEPS:
        with WriteRecorder() as recorder:
            act(input_, route)
            app.settle()
        played.append((label, recorder.Result(), encode(app.get("score.3"))))
    return played


def test_the_routes_write_what_the_board_s_inputs_write(
        autonaApp, client, monkeypatch):
    board = autonaApp.board(BOARD)
    byBoard = _play(autonaApp, lambda input_, _route: input_(board))
    # Not two recordings of nothing: every step of the script did something.
    assert [label for label, recorded, _state in byBoard
            if not recorded["sets"]] == []

    board = autonaApp.board(BOARD)

    def throughTheRoute(_input, route):
        _sendOk(client, route)
        # The board follows, and following writes nothing.
        with WriteRecorder() as following:
            autonaApp.settle()
        assert following.sets == []
        assert _shown(board) == _fromState(autonaApp)

    onTheDesktop = _play(autonaApp, throughTheRoute)
    assert onTheDesktop == byBoard

    with monkeypatch.context() as headless:
        _goHeadless(autonaApp, headless)
        try:
            with_no_board = _play(
                autonaApp, lambda _input, route: _sendOk(client, route))
        finally:
            _dropHeadlessBoards(autonaApp)
    assert with_no_board == byBoard


# What the board wrote when it was recorded (tests/fixtures/crew_operations):
# the routes on top of the operations it was recorded through.
GOLDEN_ROUTES = [
    ("stock down", ("post", _stock(1, 1), {"delta": -1})),
    ("stock up, route strings", ("post", _stock(2, 1), {"delta": 1})),
    ("stock clamps at 99", ("post", _stock(1, 2), {"delta": 200})),
    ("stock clamps at 0", ("post", _stock(1, 2), {"delta": -5})),
    ("stock unchanged", ("post", _stock(2, 3), {"delta": 0})),
    ("stock of a hidden member", ("post", _stock(1, 7), {"delta": -1})),
    ("active", ("patch", _member(1, 2), {"active": True})),
    ("active again", ("patch", _member(2, 1), {"active": True})),
    ("active nobody", ("patch", _member(2, 1), {"active": False})),
    ("swap", ("post", f"{URL}:swap-teams")),
    ("reset scores", ("post", f"{URL}/score:reset")),
    ("reset match", ("post", f"{URL}:reset-match", CONFIRM)),
    ("reset players", ("post", f"{URL}:reset-players", CONFIRM)),
    ("clear all", ("post", f"{URL}:clear", CONFIRM)),
    ("set the same best of", ("patch", f"{URL}/set", {"bestOf": 5})),
    ("set players", ("patch", f"{URL}/set", {"players": 5})),
    ("set the same players", ("patch", f"{URL}/set", {"players": 3})),
]


@pytest.mark.parametrize("label,route", GOLDEN_ROUTES,
                         ids=[label for label, _route in GOLDEN_ROUTES])
def test_the_routes_write_what_the_board_wrote(
        autonaApp, client, crewShell, label, route):
    if crewShell == "desktop":
        _setUpDesktop(autonaApp)
    else:
        _setUpHeadless(autonaApp)
    with WriteRecorder() as recorder:
        _sendOk(client, route)
        autonaApp.settle()
    golden = _golden()[label]
    assert recorder.Result() == {key: golden[key]
                                 for key in ("sets", "deltas", "bigChanges")}
    assert encode(autonaApp.get("score.3")) == golden["board"]
    assert WebOps.get_swap(BOARD) == golden["swapped"]


def test_one_patch_of_the_set_is_what_the_legacy_route_ends_up_with(
        autonaApp, client, crewShell):
    # Not write by write: the legacy route has its own order. One cycle.
    if crewShell == "desktop":
        _setUpDesktop(autonaApp)
    else:
        _setUpHeadless(autonaApp)
    with _cycles() as cycles:
        _sendOk(client, ("patch", f"{URL}/set", {
            "bestOf": 3, "phase": "Top 8", "pool": "", "match": "GF"}))
        autonaApp.settle()
    assert cycles == [["published"]]
    assert encode(autonaApp.get("score.3")) == _golden()["set fields"]["board"]


# ── what a crew's numbers mean, spelled out ──────────────────────────────

def test_the_total_is_the_named_members_the_count_shows(
        autonaApp, client, crewShell):
    def total(team=1):
        return autonaApp.get(f"score.3.team.{team}.score")

    assert total() == 0

    _sendOk(client, ("patch", _member(1, 1), {"name": "One"}))
    assert total() == 3
    _sendOk(client, ("post", _stock(1, 1), {"delta": -1}))
    assert total() == 2
    assert autonaApp.get("score.3.team.1.player.1.stock") == 2

    # A member past the count: all nine rows exist, and it does not count yet.
    _sendOk(client, ("patch", _member(1, 7), {"name": "Seven", "stock": 5}))
    assert autonaApp.get("score.3.team.1.player.7.name") == "Seven"
    assert autonaApp.get("score.3.team.1.player.7.stock") == 5
    assert total() == 2

    _sendOk(client, ("patch", f"{URL}/set", {"players": 7}))
    assert total() == 2 + 5
    assert autonaApp.get("score.3.team.2.memberNum") == 7

    _sendOk(client, ("patch", _member(1, 1), {"name": ""}))
    assert total() == 5
    assert total(2) == 0


def test_a_stock_change_stays_within_what_the_spinbox_holds(
        autonaApp, client, crewShell):
    _sendOk(client, ("post", _stock(1, 1), {"delta": 1000}))
    assert autonaApp.get("score.3.team.1.player.1.stock") == 99
    _sendOk(client, ("post", _stock(1, 1), {"delta": -1000}))
    assert autonaApp.get("score.3.team.1.player.1.stock") == 0


def test_one_crew_has_one_member_playing(autonaApp, client, crewShell):
    def playing(team=1):
        return autonaApp.get(f"score.3.team.{team}.activeMember")

    _sendOk(client, ("patch", _member(1, 1), {"active": True}))
    assert playing() == "1"
    _sendOk(client, ("patch", _member(1, 2), {"active": True}))
    assert playing() == "2"
    assert playing(2) == "0"

    # Off for someone who is not marked leaves who is.
    _sendOk(client, ("patch", _member(1, 1), {"active": False}))
    assert playing() == "2"
    _sendOk(client, ("patch", _member(1, 2), {"active": False}))
    assert playing() == "0"


def test_a_crew_s_colour_goes_with_it_on_a_swap(autonaApp, client, crewShell):
    _sendOk(client, ("post", _color(1), {"color": "#111111"}))
    _sendOk(client, ("post", _color(2), {"color": "#222222"}))
    _sendOk(client, ("post", f"{URL}:swap-teams"))
    assert autonaApp.get("score.3.team.1.color") == "#222222"
    assert autonaApp.get("score.3.team.2.color") == "#111111"


def test_a_colour_is_kept_as_the_board_keeps_it(autonaApp, client, crewShell):
    _sendOk(client, ("post", _color(1), {"color": "#ABCDEF"}))
    assert autonaApp.get("score.3.team.1.color") == "#abcdef"
    _sendOk(client, ("post", _color(2), {"color": "#F0a"}))
    assert autonaApp.get("score.3.team.2.color") == "#ff00aa"


def test_boards_1_and_2_keep_their_colour_as_sent(autonaApp, client):
    # Not this step's to change. What is the crew's stays the crew's.
    autonaApp.board(1)
    _ok(client.post("/api/v1/scoreboards/1/teams/1/color",
                    json={"color": "#ABCDEF"}))
    autonaApp.settle()
    assert autonaApp.get("score.1.team.1.color") == "#ABCDEF"


# ── one thing at a time ──────────────────────────────────────────────────

def test_a_member_update_is_one_cycle(autonaApp, client, crewShell):
    with _cycles() as cycles:
        _sendOk(client, ("patch", _member(1, 1), {
            "name": "One", "stock": 2, "active": True}))
        autonaApp.settle()
    assert cycles == [["published"]]
    assert autonaApp.get("score.3.team.1.player.1.name") == "One"
    assert autonaApp.get("score.3.team.1.player.1.stock") == 2
    assert autonaApp.get("score.3.team.1.activeMember") == "1"
    assert autonaApp.get("score.3.team.1.score") == 2


def test_a_set_patch_is_one_cycle(autonaApp, client, crewShell):
    with _cycles() as cycles:
        _sendOk(client, ("patch", f"{URL}/set", {
            "phase": "P", "pool": "A", "match": "M", "bestOf": 3, "players": 4,
            "teamName": {"team": 2, "value": "Bravo"}}))
        autonaApp.settle()
    assert cycles == [["published"]]
    assert autonaApp.get("score.3.phase") == "P"
    assert autonaApp.get("score.3.pool") == "A"
    assert autonaApp.get("score.3.match") == "M"
    assert autonaApp.get("score.3.best_of") == 3
    assert autonaApp.get("score.3.team.1.memberNum") == 4
    assert autonaApp.get("score.3.team.2.teamName") == "Bravo"


class _CountedLock:
    """StateManager.lock, counting how many times this thread let go of it
    altogether."""

    def __init__(self, real):
        self.real = real
        self.owner = threading.get_ident()
        self.depth = 0
        self.letGo = 0

    def acquire(self, *args, **kwargs):
        got = self.real.acquire(*args, **kwargs)
        if got and threading.get_ident() == self.owner:
            self.depth += 1
        return got

    def release(self):
        if threading.get_ident() == self.owner:
            self.depth -= 1
            if self.depth == 0:
                self.letGo += 1
        self.real.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.mark.parametrize("route", [
    ("patch", _member(1, 1), {"name": "One", "stock": 2, "active": True}),
    ("post", _stock(1, 1), {"delta": -1}),
    ("post", _color(1), {"color": "#123456"}),
    ("post", f"{URL}/score:reset"),
    ("post", f"{URL}:swap-teams"),
], ids=["member", "stock", "colour", "reset", "swap"])
def test_what_reads_and_writes_does_so_under_one_hold_of_the_lock(
        autonaApp, client, crewShell, route):
    _sendOk(client, ("patch", _member(1, 2), {"stock": 1}))
    autonaApp.settle()

    counted = _CountedLock(StateManager.lock)
    held = []
    set_ = StateManager.Set

    def spy(key, value=None, *args, **kwargs):
        held.append((counted.depth, counted.letGo))
        return set_(key, value, *args, **kwargs)

    with patch.object(StateManager, "lock", counted), \
            patch.object(StateManager, "Set", spy):
        _sendOk(client, route)
    autonaApp.settle()

    assert held, "the request wrote nothing"
    assert all(depth >= 1 for depth, _letGo in held), held
    assert len({letGo for _depth, letGo in held}) == 1, held


def test_the_x_post_is_read_under_one_hold_of_the_lock(autonaApp, crewShell):
    """A text with nobody's half-written swap in it: a crew read before and
    the other after."""
    counted = _CountedLock(StateManager.lock)
    held = []
    get = StateManager.Get

    def spy(key, *args, **kwargs):
        held.append((counted.depth, counted.letGo))
        return get(key, *args, **kwargs)

    with patch.object(StateManager, "lock", counted), \
            patch.object(StateManager, "Get", spy):
        CrewBattleService(BOARD).PostText()

    assert held
    assert all(depth >= 1 for depth, _letGo in held), held
    assert len({letGo for _depth, letGo in held}) == 1, held


def test_the_routes_answer_from_another_thread_on_the_desktop(
        autonaApp, client):
    """What a request is: a thread that is not the GUI's, with the board's
    work to be done on it."""
    autonaApp.dropAllBoards()
    answers = _requestOnWorkerThread(lambda: [_send(client, route) for route in (
        ("patch", _member(1, 1), {"name": "One", "active": True}),
        ("post", _stock(1, 1), {"delta": -1}),
        ("patch", f"{URL}/set", {"phase": "P", "players": 4}),
        ("post", _color(1), {"color": "#123456"}),
        ("post", f"{URL}:swap-teams"),
        ("post", f"{URL}/score:reset"),
        ("post", f"{URL}:reset-match", CONFIRM),
        ("post", f"{URL}:reset-players", CONFIRM),
        ("post", f"{URL}:clear", CONFIRM),
        ("get", f"{URL}/post"),
        ("get", "/api/v1/match-names/candidates"),
    )])
    autonaApp.settle()
    assert [answer.status_code for answer in answers] == [200] * len(answers)
    assert _board(autonaApp) is not None
    assert _shown(_board(autonaApp)) == _fromState(autonaApp)


# ── the same thing again ─────────────────────────────────────────────────

@pytest.mark.parametrize("route", [
    ("patch", _member(1, 1), {"name": "One"}),
    ("patch", _member(1, 1), {"name": ""}),
    ("patch", _member(1, 1), {"stock": 2}),
    ("patch", _member(1, 1), {"active": True}),
    ("patch", _member(1, 1), {"name": "One", "stock": 2, "active": True}),
    ("patch", f"{URL}/set", {"phase": "P"}),
    ("patch", f"{URL}/set", {"pool": "A"}),
    ("patch", f"{URL}/set", {"match": "M"}),
    ("patch", f"{URL}/set", {"bestOf": 3}),
    ("patch", f"{URL}/set", {"players": 4}),
    ("patch", f"{URL}/set", {"teamName": {"team": 1, "value": "Alpha"}}),
    ("patch", f"{URL}/set", {
        "phase": "P", "pool": "A", "match": "M", "bestOf": 3, "players": 4,
        "teamName": {"team": 1, "value": "Alpha"}}),
    ("post", _color(1), {"color": "#123456"}),
    ("post", f"{URL}/score:reset"),
    ("post", f"{URL}:reset-players", CONFIRM),
    ("post", f"{URL}:reset-match", CONFIRM),
    ("post", f"{URL}:clear", CONFIRM),
], ids=lambda route: f"{route[1].split('scoreboards/3')[1]} "
                     f"{json.dumps(route[2]) if len(route) > 2 else ''}")
def test_the_same_request_again_writes_nothing_and_tells_nobody(
        autonaApp, client, crewShell, route):
    _sendOk(client, ("patch", _member(1, 2), {
        "name": "Two", "stock": 1, "active": True}))
    _sendOk(client, route)
    autonaApp.settle()

    revision = StateManager.revision
    with WriteRecorder() as recorder, _cycles() as cycles:
        answer = _sendOk(client, route)
        autonaApp.settle()

    assert recorder.Result() == {"sets": [], "deltas": [], "bigChanges": 0}
    assert cycles == []
    assert StateManager.revision == revision
    assert answer["revision"] == revision


@pytest.mark.parametrize("route", [
    ("post", _stock(1, 1), {"delta": 0}),
    ("post", _stock(1, 3), {"delta": -1}),        # at 0 already
    ("post", _stock(1, 4), {"delta": 1}),         # at 99 already
    ("patch", _member(1, 1), {"active": False}),  # someone else is playing
    ("post", _color(1), {"color": "#ABCDEF"}),    # another spelling of it
], ids=["no change", "the floor", "the ceiling", "not the one playing",
        "the same colour"])
def test_what_changes_nothing_writes_nothing(
        autonaApp, client, crewShell, route):
    _sendOk(client, ("patch", _member(1, 2), {"active": True}))
    _sendOk(client, ("patch", _member(1, 3), {"stock": 0}))
    _sendOk(client, ("patch", _member(1, 4), {"stock": 99}))
    _sendOk(client, ("post", _color(1), {"color": "#abcdef"}))
    autonaApp.settle()

    revision = StateManager.revision
    with WriteRecorder() as recorder:
        _sendOk(client, route)
        autonaApp.settle()

    assert recorder.Result() == {"sets": [], "deltas": [], "bigChanges": 0}
    assert StateManager.revision == revision


def test_an_idempotency_key_takes_a_stock_once(autonaApp, client, crewShell):
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    route = ("post", _stock(1, 1), {"delta": -1})

    def stock():
        return autonaApp.get("score.3.team.1.player.1.stock")

    assert _send(client, route,
                 headers={"Idempotency-Key": first}).status_code == 200
    assert stock() == 2
    assert _send(client, route,
                 headers={"Idempotency-Key": first}).status_code == 200
    assert stock() == 2
    assert _send(client, route,
                 headers={"Idempotency-Key": second}).status_code == 200
    assert stock() == 1
    _sendOk(client, route)
    _sendOk(client, route)
    assert stock() == 0


# ── what is refused ──────────────────────────────────────────────────────

BAD_MEMBER_BODIES = [
    {},
    {"unknown": 1},
    {"name": "One", "unknown": 1},
    {"name": None},
    {"name": 1},
    {"name": True},
    {"name": ["One"]},
    {"name": {"value": "One"}},
    {"stock": None},
    {"stock": True},
    {"stock": False},
    {"stock": -1},
    {"stock": 100},
    {"stock": 1.5},
    {"stock": 2.0},
    {"stock": "2"},
    {"stock": ""},
    {"stock": [2]},
    {"active": None},
    {"active": 1},
    {"active": 0},
    {"active": "true"},
    {"active": []},
    # What is good in it is not written either.
    {"name": "One", "stock": -1},
    {"stock": 2, "active": "no"},
]

BAD_DELTAS = [
    {},
    {"delta": None},
    {"delta": True},
    {"delta": 1.5},
    {"delta": "1"},
    {"delta": ""},
    {"delta": [1]},
    {"delta": {"value": 1}},
]

BAD_SET_BODIES = [
    {},
    {"unknown": 1},
    # What this board does not have.
    {"characters": 2},
    {"losers": {"team": 1, "value": True}},
    {"phase": "P", "characters": 2},
    {"phase": None},
    {"phase": 1},
    {"pool": True},
    {"pool": ["A"]},
    {"match": {"value": "M"}},
    {"bestOf": None},
    {"bestOf": True},
    {"bestOf": -1},
    {"bestOf": 100},
    {"bestOf": 1.5},
    {"bestOf": "3"},
    {"bestOf": [3]},
    {"players": None},
    {"players": True},
    {"players": 0},
    {"players": -1},
    {"players": 10},
    {"players": 1.5},
    {"players": "3"},
    {"teamName": None},
    {"teamName": "Alpha"},
    {"teamName": ["Alpha"]},
    {"teamName": {}},
    {"teamName": {"team": 1}},
    {"teamName": {"value": "Alpha"}},
    {"teamName": {"team": 0, "value": "Alpha"}},
    {"teamName": {"team": 3, "value": "Alpha"}},
    {"teamName": {"team": True, "value": "Alpha"}},
    {"teamName": {"team": "1", "value": "Alpha"}},
    {"teamName": {"team": 1, "value": None}},
    {"teamName": {"team": 1, "value": 1}},
    # Nothing else in the object either, as in the body.
    {"teamName": {"team": 1, "value": "Alpha", "extra": 1}},
    {"teamName": {"team": 1, "value": "Alpha", "Team": 2}},
    # What is good in it is not written either.
    {"phase": "P", "bestOf": -1},
    {"bestOf": 3, "players": 10},
]

BAD_COLORS = [{}, {"color": None}, {"color": 1}, {"color": "red"},
              {"color": "#12"}, {"color": "#1234567"}, {"color": ["#123456"]}]


def _refused(app, send, code="INVALID_ARGUMENT", status=400):
    """`send()` is refused, and nothing happened: nothing written, and the
    board not built for it."""
    with WriteRecorder() as recorder:
        response = send()
        app.settle()
    _assertRejected(response, code, status)
    assert recorder.sets == []
    assert _board(app) is None


def _ids(values):
    return [json.dumps(value) for value in values]


@pytest.mark.parametrize("body", BAD_MEMBER_BODIES, ids=_ids(BAD_MEMBER_BODIES))
def test_a_bad_member_update_is_refused(noBoard, client, body):
    _refused(noBoard, lambda: client.patch(_member(1, 1), json=body))


@pytest.mark.parametrize("body", BAD_DELTAS, ids=_ids(BAD_DELTAS))
def test_a_bad_stock_change_is_refused(noBoard, client, body):
    _refused(noBoard, lambda: client.post(_stock(1, 1), json=body))


@pytest.mark.parametrize("body", BAD_SET_BODIES, ids=_ids(BAD_SET_BODIES))
def test_a_bad_set_patch_is_refused(noBoard, client, body):
    _refused(noBoard, lambda: client.patch(f"{URL}/set", json=body))


def test_boards_1_and_2_still_read_only_what_they_know_of_a_team_name(
        autonaApp, client):
    # The strict reading is the crew board's; boards 1/2 keep theirs, which
    # does not look at what else the object holds.
    autonaApp.board(2)
    _ok(client.patch("/api/v1/scoreboards/2/set", json={
        "teamName": {"team": 1, "value": "Alpha", "extra": 1}}))
    autonaApp.settle()
    assert StateManager.Get("score.2.team.1.teamName") == "Alpha"


@pytest.mark.parametrize("body", BAD_COLORS, ids=_ids(BAD_COLORS))
def test_a_bad_colour_is_refused(noBoard, client, body):
    _refused(noBoard, lambda: client.post(_color(1), json=body))


@pytest.mark.parametrize("url", [
    _member(1, 1), _stock(1, 1), f"{URL}/set", _color(1)])
@pytest.mark.parametrize("data", ["[1]", '"text"', "1", "{broken"],
                         ids=["an array", "a string", "a number", "not JSON"])
def test_a_body_that_is_not_an_object_is_refused(noBoard, client, url, data):
    method = client.patch if url.endswith("/set") or url.endswith("/1") \
        else client.post
    _refused(noBoard, lambda: method(
        url, data=data, content_type="application/json"))


@pytest.mark.parametrize("name", [":clear", ":reset-players", ":reset-match"])
@pytest.mark.parametrize("body", [None, {}, {"confirm": False}],
                         ids=["no body", "no confirm", "confirm false"])
def test_what_clears_asks_for_a_confirm_on_the_crew_board_too(
        noBoard, client, name, body):
    _refused(noBoard, lambda: client.post(f"{URL}{name}", json=body),
             "CONFIRMATION_REQUIRED", 409)


@pytest.mark.parametrize("request_", [
    ("patch", {"name": "One"}, lambda t, m, n: _member(t, m, n)),
    ("post", {"delta": -1}, lambda t, m, n: _stock(t, m, n)),
], ids=["member", "stock"])
@pytest.mark.parametrize("n,t,m,code,status", [
    # The team and the member, on the crew board.
    (3, 0, 1, "INVALID_ARGUMENT", 400),
    (3, 3, 1, "INVALID_ARGUMENT", 400),
    (3, 1, 0, "INVALID_ARGUMENT", 400),
    # A number a member could be, and is not on any crew.
    (3, 1, 10, "NOT_FOUND", 404),
    (3, 2, 99, "NOT_FOUND", 404),
    # Boards with no crew: said so, and neither built nor written.
    (1, 1, 1, "INVALID_ARGUMENT", 400),
    (2, 1, 1, "INVALID_ARGUMENT", 400),
    # And none at all.
    (0, 1, 1, "INVALID_ARGUMENT", 400),
    (4, 1, 1, "INVALID_ARGUMENT", 400),
])
def test_a_member_that_is_not_there_is_refused(
        noBoard, client, request_, n, t, m, code, status):
    method, body, url = request_
    response = getattr(client, method)(url(t, m, n), json=body)
    _assertRejected(response, code, status)
    noBoard.settle()
    assert noBoard.manager.scoreboards == {}


@pytest.mark.parametrize("text", ["-1", "1.5", "x"])
def test_a_member_number_that_is_not_one_has_no_route(noBoard, client, text):
    with WriteRecorder() as recorder:
        response = client.patch(_member(1, text), json={"name": "One"})
        noBoard.settle()
    assert response.status_code in (404, 405)
    assert recorder.sets == []
    assert _board(noBoard) is None


@pytest.mark.parametrize("n", [0, 1, 2, 4])
def test_the_x_post_is_the_crew_board_s(noBoard, client, n):
    _assertRejected(client.get(f"/api/v1/scoreboards/{n}/post"))
    assert noBoard.manager.scoreboards == {}


@pytest.mark.parametrize("route", [
    ("post", f"{URL}/teams/1/score:adjust", {"delta": 1}),
    ("post", f"{URL}/score:set", {"team1": 1, "team2": 0}),
    ("post", f"{URL}:apply"),
    ("post", f"{URL}/stats:refresh"),
    ("patch", f"{URL}/teams/1/players/1", {"gamerTag": "One"}),
    ("post", f"{URL}:reset-all", CONFIRM),
    ("post", f"{URL}:load-set", {"setId": 1}),
    ("post", f"{URL}:pull-stream"),
    ("post", f"{URL}:follow", {"type": "stream", "identifier": "x"}),
    ("post", f"{URL}:stop-follow"),
], ids=lambda route: route[1].split("scoreboards/3")[1])
def test_what_the_crew_board_does_not_have_stays_refused(
        noBoard, client, route):
    """A score of its own (its total comes from the stocks), player cards,
    a set to load, statistics, a 反映 button."""
    _refused(noBoard, lambda: _send(client, route))


# ── only a member the board shows can be playing ─────────────────────────

def test_a_member_the_board_does_not_show_cannot_be_playing(
        autonaApp, client, crewShell):
    """Qt hides the rows past the count, radio button and all: one of them
    marked as playing is a state Qt cannot make. A name and a stock still go
    to any row, which keeps them as Qt does."""
    _sendOk(client, ("patch", f"{URL}/set", {"players": 5}))
    autonaApp.settle()
    with WriteRecorder() as recorder:
        for member, body in ((6, {"active": True}),
                             (9, {"active": True}),
                             (6, {"name": "A6", "stock": 1, "active": True})):
            _assertRejected(client.patch(_member(1, member), json=body))
        autonaApp.settle()
    assert recorder.sets == []
    assert autonaApp.get("score.3.team.1.activeMember") == "0"

    _sendOk(client, ("patch", _member(1, 5), {"active": True}))
    _sendOk(client, ("patch", _member(2, 9), {"name": "B9", "stock": 1}))
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.activeMember") == "5"
    assert autonaApp.get("score.3.team.2.player.9.name") == "B9"
    assert autonaApp.get("score.3.team.2.player.9.stock") == 1


def test_a_marker_a_lowered_count_left_behind_can_be_taken_off(
        autonaApp, client, crewShell):
    """Lowering the count leaves the marker where it is, as Qt does, on a
    row no longer shown: `active: false` takes it off there, and
    `active: true` does not put it back."""
    for route in (("patch", f"{URL}/set", {"players": 5}),
                  ("patch", _member(1, 5), {"active": True}),
                  ("patch", f"{URL}/set", {"players": 3})):
        _sendOk(client, route)
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.activeMember") == "5"

    _assertRejected(client.patch(_member(1, 5), json={"active": True}))
    _sendOk(client, ("patch", _member(1, 5), {"active": False}))
    autonaApp.settle()
    assert autonaApp.get("score.3.team.1.activeMember") == "0"
    if crewShell == "desktop":
        assert _shown(_board(autonaApp)) == _fromState(autonaApp)


def test_a_board_not_built_has_the_members_building_it_shows(noBoard, client):
    """Before the board is built the count is its seed's (3), whatever its
    placeholder holds: building it writes the seed over it."""
    StateManager.Set("score.3.team.1.memberNum", 9)
    noBoard.settle()
    try:
        _refused(noBoard, lambda: client.patch(
            _member(1, 4), json={"active": True}))
        _sendOk(client, ("patch", _member(1, 3), {"active": True}))
        noBoard.settle()
        assert noBoard.get("score.3.team.1.memberNum") == 3
        assert noBoard.get("score.3.team.1.activeMember") == "3"
    finally:
        StateManager.Unset("score.3")
        noBoard.settle()


# ── the board that has not been built ────────────────────────────────────

@pytest.mark.parametrize("route,key,value", [
    (("patch", _member(1, 1), {"name": "One"}),
     "team.1.player.1.name", "One"),
    (("patch", _member(1, 1), {"stock": 7}), "team.1.player.1.stock", 7),
    (("patch", _member(2, 3), {"active": True}), "team.2.activeMember", "3"),
    (("post", _stock(1, 2), {"delta": -1}), "team.1.player.2.stock", 2),
    (("patch", f"{URL}/set", {"phase": "P"}), "phase", "P"),
    (("patch", f"{URL}/set", {"bestOf": 3}), "best_of", 3),
    (("patch", f"{URL}/set", {"players": 7}), "team.2.memberNum", 7),
    (("patch", f"{URL}/set", {"teamName": {"team": 2, "value": "Bravo"}}),
     "team.2.teamName", "Bravo"),
    (("post", _color(2), {"color": "#123456"}), "team.2.color", "#123456"),
    (("post", f"{URL}:swap-teams"), "teamsSwapped", True),
    (("post", f"{URL}:clear", CONFIRM), "team.1.memberNum", 1),
], ids=lambda value: value[1].split("scoreboards/3")[1]
    if isinstance(value, tuple) else None)
def test_a_write_builds_the_board_first_and_stays(
        noBoard, client, route, key, value):
    """A write to a board that is not built yet would be wiped by the seed
    that builds it later."""
    _sendOk(client, route)
    noBoard.settle()
    board = _board(noBoard)
    assert board is not None
    assert noBoard.get(f"score.3.{key}") == value
    assert _shown(board) == _fromState(noBoard)


@pytest.mark.parametrize("route", [
    ("post", f"{URL}/score:reset"),
    ("post", f"{URL}:reset-players", CONFIRM),
    ("post", f"{URL}:reset-match", CONFIRM),
    ("post", _stock(1, 1), {"delta": 0}),
    ("get", f"{URL}/post"),
    ("get", "/api/v1/match-names/candidates"),
], ids=lambda route: route[1].split("/api/v1")[1])
def test_what_means_nothing_to_a_fresh_board_does_not_build_it(
        noBoard, client, route):
    with WriteRecorder() as recorder:
        _sendOk(client, route)
        noBoard.settle()
    assert recorder.sets == []
    assert _board(noBoard) is None


def test_a_fresh_board_really_has_nothing_to_reset(noBoard, client):
    """What the skipped resets rest on: the same resets on the board once it
    is built change nothing, but for the one match field no seed writes, which
    the reset blanks (a key for "", as a board's first reset always did: no
    pool either way)."""
    noBoard.board(BOARD)
    fresh = noBoard.get("score.3")
    fresh = json.loads(encode(fresh))
    with WriteRecorder() as recorder:
        for route in (("post", f"{URL}/score:reset"),
                      ("post", f"{URL}:reset-players", CONFIRM),
                      ("post", f"{URL}:reset-match", CONFIRM)):
            _sendOk(client, route)
        noBoard.settle()
    assert recorder.sets == [["score.3.pool", '""']]
    assert json.loads(encode(noBoard.get("score.3"))) == {**fresh, "pool": ""}


def test_a_reset_of_a_1v1_board_still_runs_unbuilt(noBoard, client):
    # What boards 1/2 did: not the crew board's to skip for them.
    _ok(client.post("/api/v1/scoreboards/1/score:set",
                    json={"team1": 2, "team2": 1}))
    noBoard.settle()
    assert noBoard.get("score.1.team.1.score") == 2
    _ok(client.post("/api/v1/scoreboards/1/score:reset"))
    noBoard.settle()
    assert noBoard.get("score.1.team.1.score") == 0
    StateManager.Unset("score.1")
    noBoard.settle()


# ── who may ──────────────────────────────────────────────────────────────

WRITES = [
    ("patch", _member(1, 1), {"name": "Peer"}),
    ("post", _stock(1, 1), {"delta": -1}),
    ("patch", f"{URL}/set", {"phase": "Peer"}),
    ("post", _color(1), {"color": "#123456"}),
    ("post", f"{URL}:swap-teams"),
    ("post", f"{URL}/score:reset"),
    ("post", f"{URL}:clear", CONFIRM),
    ("post", f"{URL}:reset-players", CONFIRM),
    ("post", f"{URL}:reset-match", CONFIRM),
]
READS = [("get", f"{URL}/post"), ("get", "/api/v1/match-names/candidates")]


def _routeIds(routes):
    return [route[1].split("/api/v1")[1] for route in routes]


@pytest.mark.parametrize("route", WRITES, ids=_routeIds(WRITES))
def test_a_write_needs_this_pc_or_the_token(
        autonaApp, webApp, lanServer, route):
    autonaApp.dropAllBoards()
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    with WriteRecorder() as recorder:
        refused = _send(peer, route)
        autonaApp.settle()
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert recorder.sets == []
    assert _board(autonaApp) is None

    token = WebAccess.RegenerateToken()
    assert _send(peer, route,
                 headers={"X-Autona-Token": token}).status_code == 200


@pytest.mark.parametrize("route", WRITES, ids=_routeIds(WRITES))
def test_a_write_from_another_site_is_refused(
        autonaApp, webApp, lanServer, route):
    autonaApp.dropAllBoards()
    with WriteRecorder() as recorder:
        refused = _send(webApp.test_client(), route,
                        headers={"Origin": "https://evil.example"})
        autonaApp.settle()
    assert refused.status_code == 403
    assert refused.get_json()["error"]["code"] == "FORBIDDEN"
    assert recorder.sets == []
    assert _board(autonaApp) is None


@pytest.mark.parametrize("route", READS, ids=_routeIds(READS))
def test_a_read_is_open_to_anyone_who_can_reach_the_port(
        autonaApp, webApp, lanServer, route):
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    assert _send(peer, route).status_code == 200
    assert _send(peer, route, headers={
        "Origin": "https://evil.example"}).status_code == 200


def test_the_routes_are_classified_as_what_they_do():
    assert WebAccess.RouteAccess("api_v1.crew_member_update") == WebAccess.WRITE
    assert WebAccess.RouteAccess(
        "api_v1.crew_member_stock_adjust") == WebAccess.WRITE
    assert WebAccess.RouteAccess("api_v1.get_scoreboard_post") == WebAccess.READ
    assert WebAccess.RouteAccess(
        "api_v1.get_match_name_candidates") == WebAccess.READ


def test_a_member_s_name_stays_out_of_the_op_log(autonaApp, client, crewShell):
    # A person's name, as the player PATCH's is: the keys are enough.
    from loguru import logger

    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="INFO")
    try:
        _sendOk(client, ("patch", _member(1, 1),
                         {"name": "Secret Name", "stock": 2}))
    finally:
        logger.remove(sink)

    opLog = [line for line in lines if "op=crew.member.update" in line]
    assert len(opLog) == 1
    assert "name" in opLog[0] and "stock" in opLog[0]
    assert [line for line in lines if "Secret Name" in line] == []


def test_the_x_post_says_nothing_state_does_not(
        autonaApp, webApp, client, crewShell, lanServer):
    """READ, because each word of it is in what /state answers anyone."""
    _sendOk(client, ("patch", _member(1, 1), {"name": "Public One"}))
    _sendOk(client, ("patch", _member(2, 1), {"name": "Public Two"}))
    _sendOk(client, ("patch", f"{URL}/set", {
        "match": "Public Match", "phase": "Public Phase",
        "teamName": {"team": 1, "value": "Public Crew"}}))
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    post = _ok(peer.get(f"{URL}/post"))["data"]
    state = _ok(peer.get("/api/v1/state"))["data"]["score"]["3"]

    assert set(post) == {"text", "missing"}
    assert state["team"]["1"]["player"]["1"]["name"] in post["text"]
    assert state["team"]["2"]["player"]["1"]["name"] in post["text"]
    assert state["team"]["1"]["teamName"] in post["text"]
    assert state["phase"] in post["text"]
    assert state["match"] in post["text"]
    for line in post["text"].split("\n"):
        for word in line.replace("[", " ").replace("]", " ").replace(
                "(", " ").replace(")", " ").split():
            assert word in {"-", "/", "vs"} or word in json.dumps(state) \
                or word in json.dumps(StateManager.Get("tournamentInfo"))


# ── the swap flag ────────────────────────────────────────────────────────

def test_the_swap_flag_is_in_the_state_and_in_the_delta(
        autonaApp, client, crewShell):
    assert autonaApp.get("score.3.teamsSwapped") is None
    assert CrewBattleService(BOARD).teamsSwapped is False
    assert WebOps.get_swap(BOARD) == "False"

    with WriteRecorder() as recorder:
        _sendOk(client, ("post", f"{URL}:swap-teams"))
        autonaApp.settle()

    assert autonaApp.get("score.3.teamsSwapped") is True
    assert ["score.3.teamsSwapped", "true"] in recorder.sets
    assert [row["value"] for delta in recorder.deltas
            for row in json.loads(delta)
            if row["path"] == ["score", "3", "teamsSwapped"]] == [True]
    assert WebOps.get_swap(BOARD) == "True"
    published = _ok(client.get(URL))["data"]
    assert published["swapped"] is True
    assert published["teamsSwapped"] is True
    pending = _ok(client.get(f"{URL}/pending"))["data"]
    assert pending["board"]["swapped"] is True
    assert pending["held"] is False and pending["dirty"] is False

    with WriteRecorder() as recorder:
        _sendOk(client, ("post", f"{URL}:swap-teams"))
        autonaApp.settle()

    assert [row["value"] for delta in recorder.deltas
            for row in json.loads(delta)
            if row["path"] == ["score", "3", "teamsSwapped"]] == [False]
    assert WebOps.get_swap(BOARD) == "False"
    assert _ok(client.get(URL))["data"]["swapped"] is False


def test_the_swap_flag_is_what_the_board_reads(autonaApp, client):
    board = autonaApp.board(BOARD)
    assert board.teamsSwapped is False
    _sendOk(client, ("post", f"{URL}:swap-teams"))
    assert board.teamsSwapped is True
    board.btSwap.click()
    autonaApp.settle()
    assert autonaApp.get("score.3.teamsSwapped") is False
    assert _ok(client.get(URL))["data"]["swapped"] is False


def test_the_swap_flag_is_one_more_key_of_the_program_state(
        autonaApp, client):
    autonaApp.board(BOARD)
    autonaApp.settle()
    path = autonaApp.dir / "out" / "program_state.json"
    assert "teamsSwapped" not in json.loads(
        path.read_text(encoding="utf-8"))["score"]["3"]

    _sendOk(client, ("post", f"{URL}:swap-teams"))
    autonaApp.settle()

    after = json.loads(path.read_text(encoding="utf-8"))["score"]["3"]
    assert after["teamsSwapped"] is True


def test_the_swap_flag_is_a_key_the_catalog_knows():
    # The boards' {N}, so board 3 needed no change to the catalog.
    assert "{N}.teamsSwapped" in schema.KEY_CATALOG[schema.SCORE_ROOT]


def test_a_clear_or_a_reset_does_not_unswap(autonaApp, client, crewShell):
    _sendOk(client, ("post", f"{URL}:swap-teams"))
    for route in (("post", f"{URL}/score:reset"),
                  ("post", f"{URL}:reset-match", CONFIRM),
                  ("post", f"{URL}:reset-players", CONFIRM),
                  ("post", f"{URL}:clear", CONFIRM)):
        _sendOk(client, route)
        assert autonaApp.get("score.3.teamsSwapped") is True


# ── the Qt board follows ─────────────────────────────────────────────────

def test_a_name_being_typed_in_is_left_alone(autonaApp, client):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "Before")
    autonaApp.settle()
    edit = _row(board, 0, 1)["name"]
    other = board.teamNameEdits[0]

    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        _sendOk(client, ("patch", _member(1, 1), {"name": "From the page"}))
        autonaApp.settle()
        assert edit.text() == "Before"
        assert autonaApp.get("score.3.team.1.player.1.name") == "From the page"

        with WriteRecorder() as recorder:
            _focus(other, autonaApp)
        assert [key for key, _value in recorder.sets
                if key.endswith("player.1.name")] == []
        assert edit.text() == "From the page"
    assert autonaApp.get("score.3.team.1.player.1.name") == "From the page"


def test_a_swap_takes_what_is_being_typed_with_the_crew(autonaApp, client):
    board = autonaApp.board(BOARD)
    edit = _row(board, 0, 1)["name"]
    with _onScreen(board, autonaApp):
        _focus(edit, autonaApp)
        edit.setText("Typed")
        _sendOk(client, ("post", f"{URL}:swap-teams"))
        autonaApp.settle()
    assert autonaApp.get("score.3.team.2.player.1.name") == "Typed"
    assert autonaApp.get("score.3.team.1.player.1.name") == ""


def test_the_display_name_field_follows_the_route_it_shares(
        autonaApp, client):
    """The 表示大会名 row is boards 1/2's too, so the route was already
    there: PATCH /tournament/info."""
    board = autonaApp.board(BOARD)
    key = TournamentInfoService.Key(schema.TOURNAMENT_DISPLAY_NAME_KEY)
    before = StateManager.Get(key, "") or ""
    try:
        _ok(client.patch("/api/v1/tournament/info",
                         json={"displayTournamentName": "Shown Cup"}))
        autonaApp.settle()
        assert board.displayTournamentNameEdit.text() == "Shown Cup"
        assert StateManager.Get(key) == "Shown Cup"
    finally:
        TournamentInfoService.instance.SetDisplayName(before)
        autonaApp.settle()


# ── the X post ───────────────────────────────────────────────────────────

@pytest.fixture
def cup(autonaApp):
    key = "tournamentInfo.tournamentName"
    before = StateManager.Get(key)
    # Not through the service: it keeps the last name it followed in memory.
    StateManager.Set(key, "Cup")
    autonaApp.settle()
    try:
        yield
    finally:
        if before is None:
            StateManager.Unset(key)
        else:
            StateManager.Set(key, before)
        autonaApp.settle()


def _post(client):
    return _ok(client.get(f"{URL}/post"))["data"]


def _name(client, team, member, name):
    _sendOk(client, ("patch", _member(team, member), {"name": name}))


def test_the_x_post_is_the_board_s_text(autonaApp, client, crewShell, cup):
    _name(client, 1, 1, "A1")
    _name(client, 1, 2, "A2")
    _name(client, 2, 1, "B1")
    _sendOk(client, ("patch", f"{URL}/set", {
        "phase": "Pools", "match": "WF",
        "teamName": {"team": 1, "value": "Alpha"}}))

    assert _post(client) == {
        "text": "Cup\n[Pools] - WF\n\nAlpha (A1 / A2)\nvs\nB1",
        "missing": []}

    _sendOk(client, ("patch", f"{URL}/set", {"phase": ""}))
    assert _post(client)["text"] == "Cup\nWF\n\nAlpha (A1 / A2)\nvs\nB1"

    _sendOk(client, ("patch", f"{URL}/set", {"phase": "Pools", "match": ""}))
    assert _post(client)["text"] == "Cup\n[Pools]\n\nAlpha (A1 / A2)\nvs\nB1"

    # A crew with a name and nobody in it can't be posted, and reads as itself.
    _name(client, 1, 1, "")
    _name(client, 1, 2, "   ")
    post = _post(client)
    assert post["missing"] == ["team1"]
    assert post["text"] == "Cup\n[Pools]\n\nAlpha\nvs\nB1"

    # Nobody in the crew and no name for it either.
    _sendOk(client, ("patch", f"{URL}/set", {
        "teamName": {"team": 1, "value": ""}}))
    assert _post(client)["text"] == "Cup\n[Pools]\n\n???\nvs\nB1"


@pytest.mark.parametrize("setUp,missing", [
    ((), ["team1", "team2", "round"]),
    ((("patch", _member(1, 1), {"name": "A1"}),), ["team2", "round"]),
    ((("patch", _member(2, 1), {"name": "B1"}),), ["team1", "round"]),
    ((("patch", _member(1, 1), {"name": "A1"}),
      ("patch", _member(2, 1), {"name": "B1"})), ["round"]),
    ((("patch", _member(1, 1), {"name": "A1"}),
      ("patch", _member(2, 1), {"name": "B1"}),
      ("patch", f"{URL}/set", {"phase": "P"})), []),
    ((("patch", _member(1, 1), {"name": "A1"}),
      ("patch", _member(2, 1), {"name": "B1"}),
      ("patch", f"{URL}/set", {"match": "M"})), []),
    # A member past the count is not in the crew battle.
    ((("patch", _member(1, 5), {"name": "A5"}),), ["team1", "team2", "round"]),
    ((("patch", f"{URL}/set", {"phase": "   "}),), ["team1", "team2", "round"]),
])
def test_what_the_x_post_is_missing(
        autonaApp, client, crewShell, setUp, missing):
    for route in setUp:
        _sendOk(client, route)
    assert _post(client)["missing"] == missing


def test_the_x_post_of_a_board_that_is_not_built_has_nothing(noBoard, client):
    assert _post(client)["missing"] == ["team1", "team2", "round"]
    assert _board(noBoard) is None


@pytest.mark.parametrize("name,line", [
    # A name the state holds as a number reads as one.
    (2024, "2024"),
    # Not stripped: what the board's post always took.
    ("  Cup  ", "  Cup  "),
])
def test_the_tournament_name_goes_in_as_it_reads(
        autonaApp, client, crewShell, name, line):
    key = "tournamentInfo.tournamentName"
    before = StateManager.Get(key)
    StateManager.Set(key, name)
    autonaApp.settle()
    try:
        _name(client, 1, 1, "A1")
        _name(client, 2, 1, "B1")
        _sendOk(client, ("patch", f"{URL}/set", {"phase": "P"}))
        assert _post(client) == {
            "text": f"{line}\n[P]\n\nA1\nvs\nB1", "missing": []}
    finally:
        if before is None:
            StateManager.Unset(key)
        else:
            StateManager.Set(key, before)
        autonaApp.settle()


def _clickPost(board):
    with patch("webbrowser.open") as opened:
        board.xpostBtn.click()
    return opened


def test_the_button_posts_what_the_route_answers(autonaApp, client, cup):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "A 1&")
    _nameMember(board, 1, 1, "日本語")
    _typeTeamName(board, 1, "Bravo")
    _typeCombo(board.phaseCombo, "Pools")
    _typeCombo(board.matchCombo, "WF")
    autonaApp.settle()

    opened = _clickPost(board)

    opened.assert_called_once()
    url = opened.call_args[0][0]
    prefix = "https://twitter.com/intent/tweet?text="
    assert url.startswith(prefix)
    assert urllib.parse.unquote(url[len(prefix):]) == _post(client)["text"]
    assert urllib.parse.unquote(url[len(prefix):]) \
        == "Cup\n[Pools] - WF\n\nA 1&\nvs\nBravo (日本語)"


def test_the_button_says_what_is_missing(autonaApp, client):
    board = autonaApp.board(BOARD)
    _nameMember(board, 0, 1, "A1")
    autonaApp.settle()

    shown = []
    with patch.object(QMessageBox, "exec",
                      lambda box: shown.append(box.informativeText())):
        opened = _clickPost(board)

    opened.assert_not_called()
    translate = QApplication.translate
    assert shown == ["\n".join((
        f"- {translate('app', 'Team 2 player name')}",
        f"- {translate('app', 'Round name')}"))]
    assert _post(client)["missing"] == ["team2", "round"]


# ── what the combos offer ────────────────────────────────────────────────

def test_the_candidates_are_what_the_combos_offer(autonaApp, client):
    board = autonaApp.board(BOARD)
    candidates = _ok(client.get("/api/v1/match-names/candidates"))["data"]
    assert set(candidates) == {"phase", "pool", "match"}

    for field, combo in board._matchCombos.items():
        items = [combo.itemText(row) for row in range(combo.count())]
        assert items[0] == ""
        assert items[1:] == candidates[field], field
        assert candidates[field], field
        assert not [name for name in candidates[field] if "{0}" in name]

    template = AutonaLocaleHelper.phaseNames["group"]
    assert candidates["pool"] == [template.format(letter) for letter in "ABCD"]
    assert template not in candidates["phase"]


def test_a_phase_list_without_terms_offers_none(autonaApp, client):
    with patch.object(AutonaLocaleHelper, "phaseNames", {}):
        candidates = _ok(client.get("/api/v1/match-names/candidates"))["data"]
    assert candidates["phase"] == []
    assert candidates["pool"] == []


def test_the_phase_terms_are_filtered_as_the_combo_filtered_them(autonaApp):
    terms = {"a": "One", "b": "", "c": "Two {0}", "d_indicator": "Three",
             "e": 5, "f": "One", "g": "Four"}
    with patch.object(AutonaLocaleHelper, "phaseNames", terms):
        assert AutonaLocaleHelper.GetPhaseNameCandidates() == ["One", "Four"]


# The user's user_data/tournament_terms.json overrides the shipped terms key
# by key. Each case breaks one side of these, which give phase ["Pools"],
# pool ["GA" .. "GD"] and a match list with "GF" in it.
HEALTHY_PHASE = {"a": "Pools", "group": "G{0}"}
HEALTHY_MATCH = {"grand_final": "GF"}
# (what, phaseNames, matchNames, the lists it empties)
BROKEN_TERMS = [
    ("phase terms that are a list", ["Pools"], HEALTHY_MATCH,
     {"phase", "pool"}),
    ("phase terms that are text", "Pools", HEALTHY_MATCH, {"phase", "pool"}),
    ("no phase terms", None, HEALTHY_MATCH, {"phase", "pool"}),
    ("a group that is a number", {"a": "Pools", "group": 5}, HEALTHY_MATCH,
     {"pool"}),
    ("a group that is a list", {"a": "Pools", "group": ["G{0}"]},
     HEALTHY_MATCH, {"pool"}),
    ("a group with a second field", {"a": "Pools", "group": "G{0}{1}"},
     HEALTHY_MATCH, {"pool"}),
    ("a group with a named field", {"a": "Pools", "group": "G{0}{x}"},
     HEALTHY_MATCH, {"pool"}),
    ("a group with a lone brace", {"a": "Pools", "group": "G{0}{"},
     HEALTHY_MATCH, {"pool"}),
    ("a group with an attribute", {"a": "Pools", "group": "G{0}{0.x}"},
     HEALTHY_MATCH, {"pool"}),
    ("a group with a text index", {"a": "Pools", "group": "G{0}{0[x]}"},
     HEALTHY_MATCH, {"pool"}),
    ("match terms that are a list", HEALTHY_PHASE, ["GF"], {"match"}),
    ("no match terms", HEALTHY_PHASE, None, {"match"}),
    ("a winners ladder that is a number", HEALTHY_PHASE,
     {**HEALTHY_MATCH, "winners_top_n": 5}, {"match"}),
    ("a losers ladder that is null", HEALTHY_PHASE,
     {**HEALTHY_MATCH, "losers_top_n": None}, {"match"}),
    ("a losers ladder with a second field", HEALTHY_PHASE,
     {**HEALTHY_MATCH, "losers_top_n": "L{1}"}, {"match"}),
    ("a winners ladder with a named field", HEALTHY_PHASE,
     {**HEALTHY_MATCH, "winners_top_n": "W{0}{x}"}, {"match"}),
    ("rounds that are a number", HEALTHY_PHASE,
     {**HEALTHY_MATCH, "round": 5}, {"match"}),
    ("rounds with a lone brace", HEALTHY_PHASE,
     {**HEALTHY_MATCH, "round": "R{0}{"}, {"match"}),
]


def _terms(phaseNames, matchNames):
    return (patch.object(AutonaLocaleHelper, "phaseNames", phaseNames),
            patch.object(AutonaLocaleHelper, "matchNames", matchNames))


@pytest.mark.parametrize(
    "phaseNames,matchNames,emptied", [case[1:] for case in BROKEN_TERMS],
    ids=[case[0] for case in BROKEN_TERMS])
def test_a_term_that_breaks_a_list_empties_that_list(
        autonaApp, client, phaseNames, matchNames, emptied):
    phaseTerms, matchTerms = _terms(phaseNames, matchNames)
    with phaseTerms, matchTerms:
        candidates = _ok(client.get("/api/v1/match-names/candidates"))["data"]

    for field in emptied:
        assert candidates[field] == [], field
    if "phase" not in emptied:
        assert candidates["phase"] == ["Pools"]
    if "pool" not in emptied:
        assert candidates["pool"] == ["GA", "GB", "GC", "GD"]
    if "match" not in emptied:
        assert "GF" in candidates["match"]
        assert all(isinstance(name, str) for name in candidates["match"])


def test_a_round_name_that_is_not_text_is_no_candidate(autonaApp, client):
    # As a phase name that is not text is none: a combo cannot show it.
    phaseTerms, matchTerms = _terms(HEALTHY_PHASE, {
        "winners_final": 5, "losers_final": ["LF"], "grand_final": "GF"})
    with phaseTerms, matchTerms:
        match = _ok(client.get("/api/v1/match-names/candidates"))["data"][
            "match"]
    assert "GF" in match
    assert all(isinstance(name, str) for name in match)


@pytest.mark.parametrize("phaseNames,matchNames", [
    (None, None),
    ({"a": "Pools", "group": 5}, {**HEALTHY_MATCH, "winners_top_n": 5}),
    ({"a": "Pools", "group": "G{0}{1}"},
     {"winners_final": 5, "grand_final": "GF", "round": "R{0}{"}),
], ids=["no terms", "numbers", "fields and a lone brace"])
def test_the_crew_board_builds_on_broken_terms(
        autonaApp, client, phaseNames, matchNames):
    phaseTerms, matchTerms = _terms(phaseNames, matchNames)
    with phaseTerms, matchTerms:
        board = autonaApp.board(BOARD)
        candidates = _ok(client.get("/api/v1/match-names/candidates"))["data"]

    for field, combo in board._matchCombos.items():
        items = [combo.itemText(row) for row in range(combo.count())]
        assert items == [""] + candidates[field], field


# ── core, without a route ────────────────────────────────────────────────

def test_core_ignores_a_member_that_is_not_there(autonaApp, crewShell):
    crew = CrewBattleService(BOARD)
    with WriteRecorder() as recorder:
        for team, member in ((0, 1), (3, 1), (1, 0), (1, 10), ("x", 1)):
            assert crew.RenameMember(team, member, "Nobody") is False
            assert crew.UpdateMember(team, member, name="Nobody", stock=1,
                                     active=True) is False
            assert crew.UpdateMember(team, member, active=False) is False
        autonaApp.settle()
    assert recorder.sets == []


@pytest.mark.parametrize("member", [0, -5])
def test_core_leaves_whoever_is_playing_for_a_member_that_is_not_there(
        autonaApp, crewShell, member):
    """0 (or less) is "nobody" to SelectActiveMember: an UpdateMember that
    passed it on would take the marker off whoever is playing."""
    crew = CrewBattleService(BOARD)
    assert crew.UpdateMember(1, 2, active=True) is True
    autonaApp.settle()
    with WriteRecorder() as recorder:
        assert crew.UpdateMember(1, member, active=True) is False
        assert crew.UpdateMember(
            1, member, name="Nobody", stock=1, active=True) is False
        autonaApp.settle()
    assert recorder.sets == []
    assert crew.ActiveMember(1) == 2


def test_core_says_whether_it_changed_anything(autonaApp, crewShell):
    crew = CrewBattleService(BOARD)
    assert crew.RenameMember(1, 1, "One") is True
    assert crew.RenameMember(1, 1, "One") is False
    assert crew.UpdateMember(1, 1) is False
    assert crew.UpdateMember(1, 1, name="One", stock=3) is False
    assert crew.UpdateMember(1, 1, stock=2) is True
    assert crew.UpdateMember(1, 1, active=True) is True
    assert crew.UpdateMember(1, 1, active=True) is False
    assert crew.UpdateMember(1, 1, active=False) is True
    assert crew.UpdateMember(1, 1, active=False) is False
    # An empty name is a name to write (None is "leave it alone").
    assert crew.UpdateMember(1, 1, name="") is True
    assert StateManager.Get("score.3.team.1.player.1.name") == ""


def test_the_board_s_reset_button_twice_costs_one_cycle(autonaApp):
    board = autonaApp.board(BOARD)
    _row(board, 0, 1)["stock"].setValue(1)
    autonaApp.settle()
    with _cycles() as cycles:
        board.btReset.click()
        board.btReset.click()
        autonaApp.settle()
    assert cycles == [["published"]]
    assert autonaApp.get("score.3.team.1.player.1.stock") == 3
    assert _shown(board) == _fromState(autonaApp)


# ── the catalogue a person reads ─────────────────────────────────────────

def test_the_api_window_lists_the_routes_and_each_goes_where_it_says(webApp):
    from src.AutonaApiInfoWindow import _ENDPOINTS, _PATH_DEFAULTS

    routes = webApp.url_map.bind("localhost")

    def endpoint(method, path):
        for token, value in _PATH_DEFAULTS.items():
            path = path.replace(token, value)
        return routes.match(f"/api/v1{path}", method=method)[0]

    listed = {endpoint(entry[0], entry[1]) for entry in _ENDPOINTS}
    assert {"api_v1.crew_member_update", "api_v1.crew_member_stock_adjust",
            "api_v1.get_scoreboard_post",
            "api_v1.get_match_name_candidates"} <= listed


def test_the_api_window_s_examples_are_accepted(autonaApp, client):
    from src.AutonaApiInfoWindow import _ENDPOINTS, _PATH_DEFAULTS

    autonaApp.board(BOARD)
    examples = [entry for entry in _ENDPOINTS
                if entry[1].startswith("/scoreboards/3/")]
    assert len(examples) >= 3
    for method, path, _desc, body, *_rest in examples:
        for token, value in _PATH_DEFAULTS.items():
            path = path.replace(token, value)
        response = getattr(client, method.lower())(
            f"/api/v1{path}", json=body)
        assert response.status_code == 200, (
            path, response.get_data(as_text=True))
