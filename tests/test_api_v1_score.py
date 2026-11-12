"""The SCORE page's /api/v1 routes, first half (ADR-005 S4u).

What the React SCORE page will read and write through, added to /api/v1 with
the Qt panel still there:

- ``GET   /api/v1/scoreboards/{n}/pending``     the live values of a held board
- ``POST  .../teams/{t}/players/{p}:clear``     one slot
- ``POST  .../teams/{t}/players:swap``          a card's move up / down
- ``POST  /api/v1/scoreboards/{n}:reset-all``   the desktop's すべての入力値をリセット
- ``PATCH /api/v1/tournament/info``             + displayTournamentName

Through Flask's test client against the real route table, on both shells where
the operation is theirs alike: the desktop's real boards and the headless host.
Added only: the published view (GET /scoreboards/{n}, /state, program_state,
out/) keeps its meaning, which is pinned first (ADR-005 D4).

``GET /api/v1/stages`` and ``PATCH /api/v1/scoreboards/{n}/stages/{g}`` (one
game row) went with the game tracker in S4ac-4: their absence is pinned in
tests/test_web_panel_host.py.
"""
import contextlib
import copy
import json
import threading
from unittest.mock import patch

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaWebServer import WebServer
from src.core import StateSchema as schema
from src.core import WebAccess
from src.core.Actions import AutonaActions
from src.core.SetLoadService import SetLoadService
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from src.core.TournamentInfoService import TournamentInfoService
from tests.fixtures.write_recording import WriteRecorder
from tests.test_api_v1 import (  # noqa: F401
    _assertRejected, client, webApp)

from tests.test_web_access import LAN_HOST, LAN_HOST_2, LAN_PEER
from tests.test_web_operations import _goHeadless


def _ok(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


def _pending(client, n=1):
    return _ok(client.get(f"/api/v1/scoreboards/{n}/pending"))["data"]


def _published(client, n=1):
    return _ok(client.get(f"/api/v1/scoreboards/{n}"))["data"]


def _apply(client, n=1):
    _ok(client.post(f"/api/v1/scoreboards/{n}:apply"))


@pytest.fixture(params=["desktop", "headless"])
def shell(request, autonaApp, monkeypatch):
    """Boards 1 and 2 on the host each shell runs them on: on the desktop
    made in core as its launch makes them, with no widget (ADR-005 S4ac-2)."""
    if request.param == "desktop":
        autonaApp.ready(1)
        autonaApp.ready(2)
    else:
        _goHeadless(autonaApp, monkeypatch)
    try:
        yield request.param
    finally:
        if request.param == "headless":
            for number in (1, 2, 3):
                StateManager.Unset(f"score.{number}")
            autonaApp.settle()


@pytest.fixture
def desktopBoards(autonaApp):
    autonaApp.ready(1)
    autonaApp.ready(2)
    return autonaApp


@pytest.fixture
def launchedBoards(autonaApp):
    """The desktop as it launches (ADR-005 S4ac-1): every board made in core
    by AutonaActions.MakeBoards with `panel` -- boards 1 / 2 held, the crew
    board not. The other fixtures start from boards core does not have (the
    harness drops them between tests), which a launch only leaves when it
    failed. The harness's readyAll() (ADR-005 S4ac-2); the boards go after
    the test."""
    autonaApp.readyAll()
    return autonaApp


@pytest.fixture
def lanServer(monkeypatch):
    """The token in a private copy of the settings, and the LAN names."""
    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())


def _oneCycle(cycles):
    # A held board's cycle can publish the keys exempt from 反映 (the upset
    # factor a swap recalculates) beside its held update: still one cycle.
    assert len(cycles) == 1, cycles


@contextlib.contextmanager
def _cycles():
    """The save cycles that told anyone anything, on either shell: one list
    per cycle of what it told. What one SaveState told is one cycle; a held
    write with no block open is told without a SaveState, and is one too."""
    cycles = []
    current = threading.local()
    save = StateManager.SaveState

    def countedSave():
        outer = getattr(current, "cycle", None)
        current.cycle = cycle = []
        try:
            save()
        finally:
            current.cycle = outer
            if cycle:
                cycles.append(cycle)

    def told(what):
        cycle = getattr(current, "cycle", None)
        if cycle is None:
            cycles.append([what])
        else:
            cycle.append(what)

    def published(changes):
        told("published")

    def held(prefix):
        told("held")

    StateManager.observers.state_updated.subscribe(published)
    StateManager.observers.held_state_updated.subscribe(held)
    try:
        with patch.object(StateManager, "SaveState", countedSave):
            yield cycles
    finally:
        StateManager.observers.state_updated.unsubscribe(published)
        StateManager.observers.held_state_updated.unsubscribe(held)


# ── GET /scoreboards/{n}/pending ─────────────────────────────────────────

def test_the_pending_values_stay_out_of_everything_published(
        autonaApp, client, desktopBoards):
    before = copy.deepcopy(_published(client))
    assert _pending(client) == {"held": True, "dirty": False,
                                "board": before}

    _ok(client.post("/api/v1/scoreboards/1/score:set",
                    json={"team1": 3, "team2": 1}))
    autonaApp.settle()

    pending = _pending(client)
    assert (pending["held"], pending["dirty"]) == (True, True)
    assert pending["board"]["team"]["1"]["score"] == 3
    assert pending["board"]["team"]["2"]["score"] == 1

    # Not the board's route, /state, program_state or out/: what OBS reads.
    assert _published(client) == before
    state = _ok(client.get("/api/v1/state"))["data"]
    assert state["score"]["1"]["team"]["1"]["score"] == before["team"]["1"]["score"]
    assert WebOps.program_state()["state"]["score"]["1"]["team"]["1"]["score"] \
        == before["team"]["1"]["score"]
    written = json.loads((autonaApp.dir / "out" / "program_state.json")
                         .read_text(encoding="utf-8"))
    assert written["score"]["1"]["team"]["1"]["score"] \
        == before["team"]["1"]["score"]

    _apply(client)
    autonaApp.settle()

    after = _pending(client)
    assert (after["held"], after["dirty"]) == (True, False)
    assert after["board"] == _published(client)
    assert _published(client)["team"]["1"]["score"] == 3
    assert WebOps.program_state()["state"]["score"]["1"]["team"]["1"]["score"] == 3


def test_the_pending_board_has_the_published_boards_shape_and_swap_flag(
        autonaApp, client, desktopBoards):
    _ok(client.post("/api/v1/scoreboards/1:swap-teams"))
    autonaApp.settle()

    # The swap flag is the live one on both routes, 反映 or not.
    assert _pending(client)["board"]["swapped"] is True
    assert _published(client)["swapped"] is True

    _apply(client)
    autonaApp.settle()
    assert _pending(client)["board"] == _published(client)


def test_the_board_and_the_revision_are_one_read(autonaApp, client,
                                                 monkeypatch):
    # Headless, where a write moves the revision. A write started between the
    # two reads waits for the lock they are under, so the answer has neither
    # its value nor its revision.
    _goHeadless(autonaApp, monkeypatch)
    key = "score.1.team.1.teamName"
    swapFlag = WebOps._swap_flag
    writer = threading.Thread(target=StateManager.Set,
                              args=(key, "IN THE MIDDLE"), daemon=True)

    def betweenTheReads(board):
        writer.start()
        writer.join(0.2)
        return swapFlag(board)

    monkeypatch.setattr(WebOps, "_swap_flag", betweenTheReads)
    try:
        before = StateManager.revision
        body = _ok(client.get("/api/v1/scoreboards/1/pending"))
        writer.join(30)
        assert not writer.is_alive()
        assert body["revision"] == before
        assert body["data"]["board"]["team"]["1"]["teamName"] != "IN THE MIDDLE"
        # And it was one that moves the revision.
        assert StateManager.revision > before
        assert StateManager.Get(key) == "IN THE MIDDLE"
    finally:
        for number in (1, 2, 3):
            StateManager.Unset(f"score.{number}")
        autonaApp.settle()


def test_the_crew_board_is_never_held(autonaApp, client):
    autonaApp.ready(3)
    assert _pending(client, 3) == {"held": False, "dirty": False,
                                   "board": _published(client, 3)}


@pytest.mark.parametrize("n", [1, 2, 3])
def test_a_headless_board_is_published_as_it_is_written(
        autonaApp, client, monkeypatch, n):
    _goHeadless(autonaApp, monkeypatch)
    try:
        assert _pending(client, n) == {"held": False, "dirty": False,
                                       "board": _published(client, n)}
        if n != 3:
            _ok(client.post(f"/api/v1/scoreboards/{n}/score:set",
                            json={"team1": 2, "team2": 0}))
            autonaApp.settle()
            pending = _pending(client, n)
            assert (pending["held"], pending["dirty"]) == (False, False)
            assert pending["board"]["team"]["1"]["score"] == 2
            assert pending["board"] == _published(client, n)
    finally:
        for number in (1, 2, 3):
            StateManager.Unset(f"score.{number}")
        autonaApp.settle()


def test_a_board_that_does_not_exist_is_refused(autonaApp, client):
    _assertRejected(client.get("/api/v1/scoreboards/4/pending"))


def test_the_pending_values_need_this_pc_or_the_token(
        autonaApp, webApp, desktopBoards, lanServer):
    # Edits the operator has not published, and they can hold a real name.
    AutonaActions.instance.SetTeamName(1, 1, "NOT-PUBLISHED-YET")
    autonaApp.settle()
    url = "/api/v1/scoreboards/1/pending"
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    refused = peer.get(url)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert "NOT-PUBLISHED-YET" not in refused.get_data(as_text=True)

    token = WebAccess.RegenerateToken()
    answered = peer.get(url, headers={"X-Autona-Token": token})
    assert answered.status_code == 200
    assert "NOT-PUBLISHED-YET" in answered.get_data(as_text=True)
    assert webApp.test_client().get(url).status_code == 200

    # The published view stays open, and does not carry them.
    published = peer.get("/api/v1/scoreboards/1")
    assert published.status_code == 200
    assert "NOT-PUBLISHED-YET" not in published.get_data(as_text=True)


# ── the boards a launch made (ADR-005 S4ac-1) ────────────────────────────
# Every board is made in core at launch and boards 1 / 2 are held. What the
# page and the API write to a board SCORE does not show waits for 反映 like
# any edit.

def _live(n):
    return copy.deepcopy(StateManager.Get(f"score.{n}"))


def _publishedState(n):
    with StateManager.lock:
        return copy.deepcopy(
            StateManager.lastSavedState.get("score", {}).get(str(n)))


def _held(board):
    """A board without the keys exempt from the hold (StateSchema.STATS_KEYS):
    the swap recalculates `upset_factor`, published at once as on a shown
    board."""
    return {key: value for key, value in board.items()
            if key not in schema.STATS_KEYS}


UNSHOWN_WRITES = {
    "score:adjust": ("/api/v1/scoreboards/2/teams/1/score:adjust",
                     {"delta": 2}),
    "score:set": ("/api/v1/scoreboards/2/score:set", {"team1": 3, "team2": 1}),
    "color": ("/api/v1/scoreboards/2/teams/2/color", {"color": "#123456"}),
    "swap-teams": ("/api/v1/scoreboards/2:swap-teams", None),
}


@pytest.mark.parametrize("write", list(UNSHOWN_WRITES))
def test_a_write_to_the_board_the_window_does_not_show_waits_for_apply(
        launchedBoards, client, write):
    """Until S4ac-1 a write there built the board -- its seed, its hold and
    its widgets -- first. It is held from launch now: out of everything
    published until :apply."""
    app = launchedBoards
    before = _publishedState(2)
    assert before == _live(2)

    path, body = UNSHOWN_WRITES[write]
    _ok(client.post(path, json=body))
    app.settle()

    assert _pending(client, 2)["dirty"] is True
    assert _held(_live(2)) != _held(before)
    assert _held(_publishedState(2)) == _held(before)

    _apply(client, 2)
    app.settle()

    assert StateManager.HasPendingChanges("score.2") is False
    assert _publishedState(2) == _live(2)
    assert _held(_live(2)) != _held(before)


@pytest.mark.parametrize("n", [1, 2])
def test_stop_follow_on_a_launched_board_writes_nothing(
        launchedBoards, client, n):
    """The board follows nothing: the cancel has nothing to change, shown
    board or not (it skipped a board the desktop had not built until
    S4ac-1)."""
    app = launchedBoards
    revision = StateManager.revision

    with WriteRecorder() as recorder:
        _ok(client.post(f"/api/v1/scoreboards/{n}:stop-follow"))
        app.settle()

    assert recorder.sets == []
    assert StateManager.revision == revision
    assert StateManager.HasPendingChanges(f"score.{n}") is False


@pytest.mark.parametrize("n", [2, 3])
def test_reading_a_launched_board_writes_nothing(launchedBoards, client, n):
    revision = StateManager.revision

    assert _pending(client, n) == {"held": n != 3, "dirty": False,
                                   "board": _published(client, n)}
    swap = client.get(f"/scoreboard{n}-get-swap")
    assert (swap.status_code, swap.get_data(as_text=True)) == (200, "False")
    assert StateManager.revision == revision


# ── one slot, and moving a card ──────────────────────────────────────────

@pytest.fixture
def twoPlayers(shell, autonaApp, client):
    """Board 2 with two players a side, named."""
    _ok(client.patch("/api/v1/scoreboards/2/set", json={"players": 2}))
    for team, slot, tag in ((1, 1, "A1"), (1, 2, "A2"), (2, 1, "B1"),
                            (2, 2, "B2")):
        _ok(client.patch(f"/api/v1/scoreboards/2/teams/{team}/players/{slot}",
                         json={"gamerTag": tag}))
    autonaApp.settle()
    return shell


def _tag(team, slot, n=2):
    return StateManager.Get(f"score.{n}.team.{team}.player.{slot}.name")


def _slotUrl(team, slot, action, n=2):
    return f"/api/v1/scoreboards/{n}/teams/{team}/players/{slot}:{action}"


def _swapUrl(team, n=2):
    return f"/api/v1/scoreboards/{n}/teams/{team}/players:swap"


def test_a_slot_clear_empties_that_slot_only(autonaApp, client, twoPlayers):
    with _cycles() as told:
        _ok(client.post(_slotUrl(1, 1, "clear"), json={"confirm": True}))
        autonaApp.settle()

    _oneCycle(told)
    assert [_tag(1, 1), _tag(1, 2), _tag(2, 1), _tag(2, 2)] == [
        "", "A2", "B1", "B2"]


def test_a_slot_clear_needs_the_confirmation(autonaApp, client, twoPlayers):
    for body in ({}, {"confirm": False}):
        _assertRejected(client.post(_slotUrl(1, 1, "clear"), json=body),
                        "CONFIRMATION_REQUIRED", 409)
    assert _tag(1, 1) == "A1"


def test_a_slot_clear_refuses_what_is_not_a_slot(autonaApp, client, twoPlayers):
    confirm = {"confirm": True}
    _assertRejected(client.post(_slotUrl(1, 3, "clear"), json=confirm),
                    "NOT_FOUND", 404)
    _assertRejected(client.post(_slotUrl(1, 0, "clear"), json=confirm))
    _assertRejected(client.post(_slotUrl(3, 1, "clear"), json=confirm))
    _assertRejected(client.post(_slotUrl(1, 1, "clear", n=3), json=confirm))
    assert _tag(1, 1) == "A1"


def test_a_swap_exchanges_the_two_slots(autonaApp, client, twoPlayers):
    with _cycles() as told:
        _ok(client.post(_swapUrl(1), json={"first": 1, "second": 2}))
        autonaApp.settle()

    _oneCycle(told)
    assert [_tag(1, 1), _tag(1, 2), _tag(2, 1), _tag(2, 2)] == [
        "A2", "A1", "B1", "B2"]


@pytest.mark.parametrize("body", [
    {}, {"first": 1}, {"second": 2}, {"first": 1, "second": 1},
    {"first": 0, "second": 1}, {"first": 1, "second": -1},
    {"first": True, "second": 2}, {"first": "1", "second": 2},
    {"first": 1.5, "second": 2}, {"first": None, "second": 2},
])
def test_a_swap_takes_two_different_slot_numbers(
        autonaApp, client, twoPlayers, body):
    _assertRejected(client.post(_swapUrl(1), json=body))
    assert [_tag(1, 1), _tag(1, 2)] == ["A1", "A2"]


def test_a_swap_with_a_slot_the_team_lacks_moves_nothing(
        autonaApp, client, twoPlayers):
    before = copy.deepcopy(StateManager.Get("score.2"))

    _assertRejected(client.post(_swapUrl(1), json={"first": 1, "second": 3}),
                    "NOT_FOUND", 404)
    _assertRejected(client.post(_swapUrl(1), json={"first": 3, "second": 2}),
                    "NOT_FOUND", 404)
    _assertRejected(client.post(_swapUrl(3), json={"first": 1, "second": 2}))
    _assertRejected(client.post(_swapUrl(1, n=3),
                                json={"first": 1, "second": 2}))

    assert StateManager.Get("score.2") == before


def test_with_the_teams_swapped_a_slot_is_the_states_team(autonaApp, client,
                                                          twoPlayers):
    _ok(client.post("/api/v1/scoreboards/2:swap-teams"))
    autonaApp.settle()
    assert StateManager.Get("score.2.teamsSwapped") is True
    assert [_tag(1, 1), _tag(1, 2), _tag(2, 1), _tag(2, 2)] == [
        "B1", "B2", "A1", "A2"]

    _ok(client.post(_swapUrl(1), json={"first": 1, "second": 2}))
    autonaApp.settle()
    assert [_tag(1, 1), _tag(1, 2), _tag(2, 1), _tag(2, 2)] == [
        "B2", "B1", "A1", "A2"]

    _ok(client.post(_slotUrl(2, 1, "clear"), json={"confirm": True}))
    autonaApp.settle()
    assert [_tag(1, 1), _tag(1, 2), _tag(2, 1), _tag(2, 2)] == [
        "B2", "B1", "", "A2"]


@pytest.mark.parametrize("url, body, after", [
    (_swapUrl(1), {"first": 1, "second": 2}, ["A2", "A1"]),
    (_slotUrl(1, 1, "clear"), {"confirm": True}, ["", "A2"]),
])
def test_on_a_held_board_a_slot_waits_for_the_apply(autonaApp, client, url,
                                                    body, after):
    autonaApp.ready(2)
    _ok(client.patch("/api/v1/scoreboards/2/set", json={"players": 2}))
    for slot, tag in ((1, "A1"), (2, "A2")):
        _ok(client.patch(f"/api/v1/scoreboards/2/teams/1/players/{slot}",
                         json={"gamerTag": tag}))
    _apply(client, 2)
    autonaApp.settle()

    def names(board):
        return [board["team"]["1"]["player"][slot]["name"]
                for slot in ("1", "2")]

    def everythingPublished():
        written = json.loads((autonaApp.dir / "out" / "program_state.json")
                             .read_text(encoding="utf-8"))
        return [names(_published(client, 2)),
                names(_ok(client.get("/api/v1/state"))["data"]["score"]["2"]),
                names(WebOps.program_state()["state"]["score"]["2"]),
                names(written["score"]["2"])]

    assert everythingPublished() == [["A1", "A2"]] * 4

    _ok(client.post(url, json=body))
    autonaApp.settle()

    pending = _pending(client, 2)
    assert (pending["held"], pending["dirty"]) == (True, True)
    assert names(pending["board"]) == after
    assert everythingPublished() == [["A1", "A2"]] * 4

    _apply(client, 2)
    autonaApp.settle()
    assert everythingPublished() == [after] * 4


# ── everything on the board ──────────────────────────────────────────────

def _scores(n=1):
    return (StateManager.Get(f"score.{n}.team.1.score"),
            StateManager.Get(f"score.{n}.team.2.score"))


def _filledBoard(client, autonaApp):
    _ok(client.patch("/api/v1/scoreboards/2/set", json={
        "players": 2, "characters": 2, "bestOf": 5, "phase": "Top 8",
        "match": "Grand Final"}))
    for team, name in ((1, "Alpha"), (2, "Bravo")):
        _ok(client.patch("/api/v1/scoreboards/2/set", json={
            "teamName": {"team": team, "value": name}}))
    _ok(client.patch("/api/v1/scoreboards/2/teams/1/players/1",
                     json={"gamerTag": "A1"}))
    _ok(client.post("/api/v1/scoreboards/2/score:set",
                    json={"team1": 2, "team2": 1}))
    autonaApp.settle()


def test_reset_all_needs_the_confirmation(autonaApp, client, shell):
    _filledBoard(client, autonaApp)
    for body in ({}, {"confirm": False}):
        _assertRejected(client.post("/api/v1/scoreboards/2:reset-all", json=body),
                        "CONFIRMATION_REQUIRED", 409)
    assert StateManager.Get("score.2.team.1.teamName") == "Alpha"
    _assertRejected(client.post("/api/v1/scoreboards/3:reset-all",
                                json={"confirm": True}))


def test_reset_all_empties_the_values_and_keeps_the_counts(
        autonaApp, client, shell):
    _filledBoard(client, autonaApp)
    crest = autonaApp.dir / "user_data" / "team_logo" / "alpha.png"
    crest.parent.mkdir(parents=True, exist_ok=True)
    crest.write_bytes(b"")
    SetLoadService(2).lastSetSelected = "1001"
    try:
        _ok(client.patch("/api/v1/scoreboards/2/set", json={
            "teamName": {"team": 1, "value": "ALPHA"}}))
        autonaApp.settle()
        assert StateManager.Get("score.2.team.1.logo")

        with _cycles() as told:
            _ok(client.post("/api/v1/scoreboards/2:reset-all",
                            json={"confirm": True}))
            autonaApp.settle()
    finally:
        crest.unlink(missing_ok=True)

    _oneCycle(told)
    get = lambda key: StateManager.Get(f"score.2.{key}")
    assert (get("team.1.teamName"), get("team.2.teamName")) == ("", "")
    assert get("team.1.logo") is None
    assert get("team.1.player.1.name") == ""
    assert _scores(2) == (0, 0)
    assert get("best_of") == 0
    assert get("match") == ""
    assert get("phase") == ""
    # The counts stay: this is the difference from :clear besides the names.
    assert sorted(get("team.1.player")) == ["1", "2"]
    assert sorted(get("team.2.player")) == ["1", "2"]
    assert len(get("team.1.player.1.character")) == 2
    # The set loaded last is forgotten: loading it again loads it.
    assert SetLoadService(2).lastSetSelected is None


def test_reset_all_is_not_clear(autonaApp, client, shell):
    # :clear keeps the team names when two players a side stay... and puts the
    # counts back: what each route is for has to stay told apart by its name.
    _filledBoard(client, autonaApp)
    _ok(client.post("/api/v1/scoreboards/2:clear", json={"confirm": True}))
    autonaApp.settle()
    cleared = copy.deepcopy(StateManager.Get("score.2"))

    _filledBoard(client, autonaApp)
    _ok(client.post("/api/v1/scoreboards/2:reset-all", json={"confirm": True}))
    autonaApp.settle()
    resetAll = StateManager.Get("score.2")

    assert sorted(resetAll["team"]["1"]["player"]) == ["1", "2"]
    assert sorted(cleared["team"]["1"]["player"]) == ["1"]
    assert resetAll["team"]["1"]["teamName"] == ""


def test_reset_all_on_a_held_board_waits_for_the_apply(autonaApp, client):
    autonaApp.ready(2)
    _filledBoard(client, autonaApp)
    _apply(client, 2)
    autonaApp.settle()

    _ok(client.post("/api/v1/scoreboards/2:reset-all", json={"confirm": True}))
    autonaApp.settle()

    assert _pending(client, 2)["dirty"] is True
    assert _published(client, 2)["team"]["1"]["teamName"] == "Alpha"
    _apply(client, 2)
    autonaApp.settle()
    assert _published(client, 2)["team"]["1"]["teamName"] == ""


# ── the 表示大会名 ────────────────────────────────────────────────────────

INFO = "/api/v1/tournament/info"
DISPLAY = "tournamentInfo.displayTournamentName"


@pytest.fixture
def info(autonaApp, monkeypatch):
    """tournamentInfo and the service's ledger handed back."""
    service = TournamentInfoService.instance
    before = copy.deepcopy(StateManager.Get("tournamentInfo"))
    monkeypatch.setattr(service, "_lastTournamentName",
                        service._lastTournamentName)
    yield service
    if before is None:
        StateManager.Unset("tournamentInfo")
    else:
        StateManager.Set("tournamentInfo", before)
    autonaApp.settle()


def test_the_display_name_reaches_the_state(autonaApp, client, info):
    _ok(client.patch(INFO, json={"displayTournamentName": "Cup"}))
    autonaApp.settle()

    assert StateManager.Get(DISPLAY) == "Cup"

    _ok(client.patch(INFO, json={"displayTournamentName": ""}))
    autonaApp.settle()
    assert StateManager.Get(DISPLAY) == ""


@pytest.mark.parametrize("value", [1, True, None, [], {}])
def test_the_display_name_is_a_string(autonaApp, client, info, value):
    before = StateManager.Get(DISPLAY)
    _assertRejected(client.patch(INFO, json={"displayTournamentName": value}))
    assert StateManager.Get(DISPLAY) == before


def test_a_typed_display_name_stops_following_the_tournament_name(
        autonaApp, client, info):
    _ok(client.patch(INFO, json={"tournamentName": "One"}))
    assert StateManager.Get(DISPLAY) == "One"

    _ok(client.patch(INFO, json={"displayTournamentName": "Mine"}))
    _ok(client.patch(INFO, json={"tournamentName": "Two"}))

    assert StateManager.Get("tournamentInfo.tournamentName") == "Two"
    assert StateManager.Get(DISPLAY) == "Mine"


@pytest.mark.parametrize("body", [
    {"tournamentName": "One", "displayTournamentName": "Mine"},
    {"displayTournamentName": "Mine", "tournamentName": "One"},
])
def test_sent_with_the_tournament_name_the_display_name_wins(
        autonaApp, client, info, body):
    # Whichever key the JSON has first: the name follows, and this is typed.
    # (The stream title follows the name in a cycle of its own, later:
    # StreamService. Not settled inside.)
    with _cycles() as told:
        _ok(client.patch(INFO, json=body))

    _oneCycle(told)
    autonaApp.settle()
    assert StateManager.Get("tournamentInfo.tournamentName") == "One"
    assert StateManager.Get(DISPLAY) == "Mine"


# ── the counts: PATCH set has them already ───────────────────────────────

def test_the_counts_are_the_set_routes(autonaApp, client, shell):
    _ok(client.patch("/api/v1/scoreboards/2/set",
                     json={"players": 3, "characters": 2}))
    autonaApp.settle()
    assert sorted(StateManager.Get("score.2.team.1.player")) == ["1", "2", "3"]
    assert len(StateManager.Get("score.2.team.2.player.3.character")) == 2


# ── v1 teamName and the crest (S4u's check) ──────────────────────────────

def test_a_team_name_brings_its_crest_and_the_same_name_again_does_not_look(
        autonaApp, client, shell):
    crest = autonaApp.dir / "user_data" / "team_logo" / "alpha.png"
    crest.parent.mkdir(parents=True, exist_ok=True)

    def name(team, value):
        _ok(client.patch("/api/v1/scoreboards/2/set", json={
            "teamName": {"team": team, "value": value}}))
        autonaApp.settle()

    logo = "score.2.team.1.logo"
    try:
        crest.write_bytes(b"")
        name(1, "Alpha")
        assert StateManager.Get(logo) == "./user_data/team_logo/alpha.png"
        # Another name takes it away, in the state team named here.
        name(1, "Bravo")
        assert StateManager.Get(logo) is None
        # A crest added for the name it already has. The same name again is
        # no change, so it is not looked up: the Qt field finishing the same
        # text did look (AutonaScoreboardWidget._OnTeamNameFinished, gone
        # with the Qt SCORE in ADR-005 S4ac-3). Pinned as it is: the route is
        # what every client of it has seen.
        crest.rename(crest.with_name("bravo.png"))
        name(1, "Bravo")
        assert StateManager.Get(logo) is None
    finally:
        for path in (crest, crest.with_name("bravo.png")):
            path.unlink(missing_ok=True)
