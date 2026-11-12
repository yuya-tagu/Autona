"""The crew board's /api/v1 routes (ADR-005 S4w).

Everything the Qt crew board could do, for the React SCORE page (the Qt board
was left unchanged then, and went in ADR-005 S4ac-3):

- ``PATCH /api/v1/scoreboards/3/teams/{t}/members/{m}``              name, stock, active
- ``POST  /api/v1/scoreboards/3/teams/{t}/members/{m}/stock:adjust`` a relative stock change
- ``GET   /api/v1/scoreboards/3/post``                               the X post's text
- ``GET   /api/v1/match-names/candidates``                           what the combos offer

and what boards 1/2 already had, open to board 3: ``:swap-teams``,
``score:reset``, ``:clear``, ``:reset-players``, ``:reset-match``,
``teams/{t}/color`` and ``PATCH set``.

Through Flask's test client against the real route table, on both shells: the
route on the desktop and the route headless have to end up in the same state,
write by write (and, until S4ac-3, as the board's own inputs did).
"""
import json
import threading
import uuid
from unittest.mock import patch

import pytest

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
from tests.test_api_v1_score import (  # noqa: F401
    _cycles, _ok, lanServer, launchedBoards)
from tests.test_crew_operations import _golden, _setUpDesktop, _setUpHeadless
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


def _madeBoards():
    """The boards core has made (AutonaActions.BoardEnsured). Until ADR-005
    S4ac-3 the crew board's widgets were what these tests looked for: the
    desktop built them, and made a board core did not have again, for a
    write (the desktop board host's Prepare, gone with that host in
    ADR-005 S4ad)."""
    from src.core.Actions import AutonaActions
    return [number for number in AutonaActions.BOARDS
            if AutonaActions.instance.BoardEnsured(number)]


@pytest.fixture(params=["desktop", "headless"])
def crewShell(request, autonaApp, monkeypatch):
    """The crew board on the host each shell runs it on: on the desktop
    made in core as its launch makes it, with no widget (ADR-005 S4ac-2)."""
    if request.param == "desktop":
        autonaApp.ready(BOARD)
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
    """The desktop with no board in core: what a launch leaves when it
    failed (every board is made at launch since ADR-005 S4ac-1; this was the
    lazy board, not asked for yet, until then). What is refused is refused
    before anything is made or written."""
    autonaApp.dropAllBoards()
    assert _madeBoards() == []
    return autonaApp


# ── the route on the desktop, the route headless ─────────────────────────

# (what it is, the route): what the board's inputs wrote until ADR-005
# S4ac-3 removed them (the routes were checked against them until then)
STEPS = [
    ("the count", ("patch", f"{URL}/set", {"players": 5})),
    ("a name", ("patch", _member(1, 1), {"name": "A1"})),
    ("the other crew's name", ("patch", _member(2, 4), {"name": "B4"})),
    ("a stock", ("patch", _member(1, 1), {"stock": 1})),
    ("a stock down", ("post", _stock(2, 4), {"delta": -1})),
    ("a stock up", ("post", _stock(2, 4), {"delta": 7})),
    ("playing", ("patch", _member(1, 1), {"active": True})),
    ("someone else playing", ("patch", _member(1, 2), {"active": True})),
    ("nobody playing", ("patch", _member(1, 2), {"active": False})),
    ("the crew's name",
     ("patch", f"{URL}/set", {"teamName": {"team": 1, "value": "Alpha"}})),
    ("the colour", ("post", _color(2), {"color": "#123456"})),
    ("the phase", ("patch", f"{URL}/set", {"phase": "Pools"})),
    ("the pool", ("patch", f"{URL}/set", {"pool": "Group A"})),
    ("the match", ("patch", f"{URL}/set", {"match": "WF"})),
    ("swap", ("post", f"{URL}:swap-teams")),
    ("a stock, swapped", ("patch", _member(1, 4), {"stock": 8})),
    ("reset stocks", ("post", f"{URL}/score:reset")),
    ("an emptied name", ("patch", _member(1, 4), {"name": ""})),
    ("swap back", ("post", f"{URL}:swap-teams")),
    ("the count down", ("patch", f"{URL}/set", {"players": 1})),
]


def _play(app, act):
    played = []
    for label, route in STEPS:
        with WriteRecorder() as recorder:
            act(route)
            app.settle()
        played.append((label, recorder.Result(), encode(app.get("score.3"))))
    return played


def test_the_routes_write_the_same_on_both_shells(
        autonaApp, client, monkeypatch):
    """Route by route, the desktop (the crew board made at launch) and the
    headless shell write the same. Until ADR-005 S4ac-3 both were checked
    against the Qt board's own inputs, which went with it; what the routes
    write against what the board wrote when it was recorded is
    test_the_routes_write_what_the_board_wrote's."""
    autonaApp.ready(BOARD)
    onTheDesktop = _play(autonaApp, lambda route: _sendOk(client, route))
    # Not two recordings of nothing: every step of the script did something.
    assert [label for label, recorded, _state in onTheDesktop
            if not recorded["sets"]] == []

    with monkeypatch.context() as headless:
        _goHeadless(autonaApp, headless)
        try:
            with_no_board = _play(
                autonaApp, lambda route: _sendOk(client, route))
        finally:
            _dropHeadlessBoards(autonaApp)
    assert with_no_board == onTheDesktop


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
    autonaApp.ready(1)
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
    """What a request is: a thread that is not the GUI's, the board's work
    done on it under the board host's lock (ADR-005 S4ad; the desktop's
    board host ran it on the GUI thread until then)."""
    autonaApp.ready(BOARD)
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
    # The clear, last of the writes, took the crews down to one member.
    assert autonaApp.get("score.3.team.1.memberNum") == 1
    assert autonaApp.get("score.3.team.1.player.1.name") == ""


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
    assert _madeBoards() == []


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
    autonaApp.ready(2)
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
    assert _madeBoards() == []


@pytest.mark.parametrize("text", ["-1", "1.5", "x"])
def test_a_member_number_that_is_not_one_has_no_route(noBoard, client, text):
    with WriteRecorder() as recorder:
        response = client.patch(_member(1, text), json={"name": "One"})
        noBoard.settle()
    assert response.status_code in (404, 405)
    assert recorder.sets == []
    assert _madeBoards() == []


@pytest.mark.parametrize("n", [0, 4])
def test_the_x_post_is_a_board_s(noBoard, client, n):
    # Boards 1/2 have one too since ADR-005 S4x
    # (tests/test_scoreboard_post.py).
    _assertRejected(client.get(f"/api/v1/scoreboards/{n}/post"))
    assert _madeBoards() == []


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
    """Lowering the count leaves the marker where it is, as Qt did, on a
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


# ── what a fresh board has ───────────────────────────────────────────────

@pytest.mark.parametrize("route", [
    ("post", f"{URL}/score:reset"),
    ("post", f"{URL}:reset-players", CONFIRM),
    ("post", f"{URL}:reset-match", CONFIRM),
    ("post", _stock(1, 1), {"delta": 0}),
    ("get", f"{URL}/post"),
    ("get", "/api/v1/match-names/candidates"),
], ids=lambda route: route[1].split("/api/v1")[1])
def test_what_means_nothing_to_a_fresh_board_does_not_build_it(
        launchedBoards, client, route):
    """Nothing written (the revision stays) on the boards as a launch makes
    them (ADR-005 S4ac-1) -- which have nothing to reset (the next test).
    Until ADR-005 S4ac-3 it ran on a board core did not have too, which the
    resets skipped (_crew_not_built, always false from then and removed in
    S4ad) rather than make it again with its widgets: nothing makes a board
    again since, and nothing builds."""
    revision = StateManager.revision
    with WriteRecorder() as recorder:
        _sendOk(client, route)
        launchedBoards.settle()
    assert recorder.sets == []
    assert StateManager.revision == revision


def test_a_fresh_board_really_has_nothing_to_reset(noBoard, client):
    """What the resets on a fresh board rest on (ADR-005 S4ac-1: the crew
    board is made at launch): the same resets on a fresh board change
    nothing and write nothing. The match reset blanks only the fields the
    board has (AutonaActions.ClearMatchInfo), and no seed writes a crew
    board's pool; it wrote one (pool = "") until the S4ac-1 review."""
    noBoard.ready(BOARD)
    fresh = json.loads(encode(noBoard.get("score.3")))
    assert "pool" not in fresh
    revision = StateManager.revision
    with WriteRecorder() as recorder:
        for route in (("post", f"{URL}/score:reset"),
                      ("post", f"{URL}:reset-players", CONFIRM),
                      ("post", f"{URL}:reset-match", CONFIRM)):
            _sendOk(client, route)
        noBoard.settle()
    assert recorder.sets == []
    assert StateManager.revision == revision
    assert json.loads(encode(noBoard.get("score.3"))) == fresh


def test_a_reset_of_a_1v1_board_still_runs_unbuilt(launchedBoards, client):
    # What boards 1/2 did: not the crew board's to skip for them. On the
    # boards a launch makes (held: the live value is what is read here).
    _ok(client.post("/api/v1/scoreboards/1/score:set",
                    json={"team1": 2, "team2": 1}))
    launchedBoards.settle()
    assert launchedBoards.get("score.1.team.1.score") == 2
    _ok(client.post("/api/v1/scoreboards/1/score:reset"))
    launchedBoards.settle()
    assert launchedBoards.get("score.1.team.1.score") == 0
    StateManager.Unset("score.1")
    launchedBoards.settle()


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
READS = [("get", "/api/v1/match-names/candidates"),
         ("get", f"/api/v1/match-names/candidates?board={BOARD}")]


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
    assert _madeBoards() == []

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
    assert _madeBoards() == []


@pytest.mark.parametrize("route", READS, ids=_routeIds(READS))
def test_a_read_is_open_to_anyone_who_can_reach_the_port(
        autonaApp, webApp, lanServer, route):
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    assert _send(peer, route).status_code == 200
    assert _send(peer, route, headers={
        "Origin": "https://evil.example"}).status_code == 200


@pytest.mark.parametrize("n", [1, 2, BOARD])
def test_the_x_post_needs_this_pc_or_the_token(
        autonaApp, webApp, lanServer, n):
    """Guarded as the pending values are (ADR-005 S4x): boards 1/2's post is
    built from the live values, 反映 or not, as the button reads them -- a
    real name typed in and not published yet among them. The crew board's
    was READ in S4w; one route, one rule."""
    url = f"/api/v1/scoreboards/{n}/post"
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    refused = peer.get(url)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"

    token = WebAccess.RegenerateToken()
    assert peer.get(url, headers={"X-Autona-Token": token}).status_code == 200
    assert webApp.test_client().get(url).status_code == 200


def test_the_routes_are_classified_as_what_they_do():
    assert WebAccess.RouteAccess("api_v1.crew_member_update") == WebAccess.WRITE
    assert WebAccess.RouteAccess(
        "api_v1.crew_member_stock_adjust") == WebAccess.WRITE
    assert WebAccess.RouteAccess(
        "api_v1.get_scoreboard_post") == WebAccess.PLAYERDB
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
    """The crew board's values are never held: each word of its post is in
    what /state answers (the route is guarded for boards 1/2's held values,
    test_the_x_post_needs_this_pc_or_the_token)."""
    _sendOk(client, ("patch", _member(1, 1), {"name": "Public One"}))
    _sendOk(client, ("patch", _member(2, 1), {"name": "Public Two"}))
    _sendOk(client, ("patch", f"{URL}/set", {
        "match": "Public Match", "phase": "Public Phase",
        "teamName": {"team": 1, "value": "Public Crew"}}))
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    token = {"X-Autona-Token": WebAccess.RegenerateToken()}

    post = _ok(peer.get(f"{URL}/post", headers=token))["data"]
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


def test_the_swap_flag_is_one_more_key_of_the_program_state(
        autonaApp, client):
    autonaApp.ready(BOARD)
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


# ── the row boards 1/2 share ─────────────────────────────────────────────

def test_the_display_name_field_follows_the_route_it_shares(
        autonaApp, client):
    """The 表示大会名 row is boards 1/2's too, so the route was already
    there: PATCH /tournament/info. (The Qt crew board's field followed it
    until ADR-005 S4ac-3; the React page reads the key.)"""
    autonaApp.ready(BOARD)
    key = TournamentInfoService.Key(schema.TOURNAMENT_DISPLAY_NAME_KEY)
    before = StateManager.Get(key, "") or ""
    try:
        _ok(client.patch("/api/v1/tournament/info",
                         json={"displayTournamentName": "Shown Cup"}))
        autonaApp.settle()
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
    assert _madeBoards() == []


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


def test_the_x_post_takes_the_names_as_they_are(
        autonaApp, client, crewShell, cup):
    """What the Qt board's button posted, through the route the React page
    posts from (the button and its message box went in ADR-005 S4ac-3)."""
    _name(client, 1, 1, "A 1&")
    _name(client, 2, 1, "日本語")
    _sendOk(client, ("patch", f"{URL}/set", {
        "phase": "Pools", "match": "WF",
        "teamName": {"team": 2, "value": "Bravo"}}))
    assert _post(client) == {
        "text": "Cup\n[Pools] - WF\n\nA 1&\nvs\nBravo (日本語)",
        "missing": []}


# ── what the combos offer ────────────────────────────────────────────────

def test_the_candidates_are_what_the_combos_offer(autonaApp, client):
    """What the Qt board's combos offered under their blank row (they were
    checked against the route until ADR-005 S4ac-3): core's
    MatchNameCandidates, by board."""
    from src.core.MatchNameCandidates import MatchNameCandidates
    autonaApp.ready(BOARD)
    candidates = _ok(client.get("/api/v1/match-names/candidates"))["data"]
    assert set(candidates) == {"phase", "pool", "match"}
    assert candidates == MatchNameCandidates.instance.Candidates(BOARD)

    for field in ("phase", "pool", "match"):
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
    """The candidates are answered on broken terms, text only. (The Qt crew
    board built its combos from them, checked here until ADR-005 S4ac-3.)"""
    from src.core.MatchNameCandidates import MatchNameCandidates
    phaseTerms, matchTerms = _terms(phaseNames, matchNames)
    with phaseTerms, matchTerms:
        autonaApp.ready(BOARD)
        candidates = _ok(client.get("/api/v1/match-names/candidates"))["data"]
        assert candidates == MatchNameCandidates.instance.Candidates(BOARD)

    assert set(candidates) == {"phase", "pool", "match"}
    for field, names in candidates.items():
        assert all(isinstance(name, str) and name for name in names), field


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
    """What the Qt board's リセット button ran, twice in one turn of the
    event loop (the button went in ADR-005 S4ac-3): the second writes
    nothing."""
    autonaApp.ready(BOARD)
    crew = CrewBattleService(BOARD)
    assert crew.UpdateMember(1, 1, stock=1) is True
    autonaApp.settle()
    with _cycles() as cycles:
        crew.ResetStocks()
        crew.ResetStocks()
        autonaApp.settle()
    assert cycles == [["published"]]
    assert autonaApp.get("score.3.team.1.player.1.stock") == 3


# ── the catalogue a person reads ─────────────────────────────────────────

def test_the_api_window_lists_the_routes_and_each_goes_where_it_says(webApp):
    from src.AutonaApiInfoWindow import _ENDPOINTS, _PATH_DEFAULTS

    routes = webApp.url_map.bind("localhost")

    def endpoint(method, path):
        for token, value in _PATH_DEFAULTS.items():
            path = path.replace(token, value)
        # The query is not the route's: matched with it, a row lands on the
        # static files' catch-all ('/<path:filename>').
        return routes.match(f"/api/v1{path.split('?')[0]}", method=method)[0]

    listed = {endpoint(entry[0], entry[1]) for entry in _ENDPOINTS}
    assert {"api_v1.crew_member_update", "api_v1.crew_member_stock_adjust",
            "api_v1.get_scoreboard_post",
            "api_v1.get_match_name_candidates",
            # ADR-005 S4x.
            "api_v1.get_battle_format", "api_v1.battle_format_set"} <= listed


def test_the_api_window_s_examples_are_accepted(autonaApp, client):
    from src.AutonaApiInfoWindow import _ENDPOINTS, _PATH_DEFAULTS

    autonaApp.ready(BOARD)
    # The crew board's own rows (its X post is every board's since ADR-005
    # S4x: "/scoreboards/{n}/post", read on board 1 by the walk above).
    examples = [entry for entry in _ENDPOINTS
                if entry[1].startswith("/scoreboards/3/")]
    assert len(examples) >= 2
    for method, path, _desc, body, *_rest in examples:
        for token, value in _PATH_DEFAULTS.items():
            path = path.replace(token, value)
        response = getattr(client, method.lower())(
            f"/api/v1{path}", json=body)
        assert response.status_code == 200, (
            path, response.get_data(as_text=True))
