"""The web operations of both shells (src/AutonaWebOperations.py, ADR-005 S3c).

The routes call the same functions in the desktop app and in
``main.py --headless``; what differs is the board host the shell registered.
Pinned here:

- the S2 operations write the same state whichever host runs them -- the
  desktop's (the board built, the write held for 反映) and the headless one
  (no board, published at once);
- headless, what a widget answers -- the crew board's operations, the
  player / character counts, the board-dependent actions (WebServerActions)
  -- is refused with NotAvailable, and the server turns that into 503 in the
  /api/v1 error shape: legacy routes, Socket.IO acks, /api/v1 (UNAVAILABLE);
- headless, the S2 operations run one at a time (the host's re-entrant
  lock), so a composite one (clear_all) is not interleaved with another
  request.
"""
import json
import threading

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaWebOperations import BoardHost, NotAvailable

BOARD = 2   # the team board: every S2 field (team names included) applies

UNAVAILABLE_BODY = {"ok": False, "error": {
    "code": "UNAVAILABLE", "message": "not available in headless"}}


def _state():
    from src.core.StateManager import StateManager
    return StateManager


def _prepare(n):
    """The same starting point for both hosts, written through core."""
    from src.core.Actions import AutonaActions
    actions = AutonaActions.instance
    with _state().SaveBlock():
        actions.SetTeamName(n, 1, "Alpha")
        actions.SetTeamName(n, 2, "Bravo")
        actions.SetScore(n, 1, 2)
        actions.SetScore(n, 2, 1)
        # Both flags: a built board seeds team 1's as False, the headless
        # skeleton (the desktop's unbuilt boards' too) has no key yet.
        actions.SetTeamLosers(n, 1, False)
        actions.SetTeamLosers(n, 2, True)
        actions.SetMatchField(n, "phase", "Pools")
        actions.SetMatchField(n, "pool", "A")
        actions.SetMatchField(n, "match", "Winners Final")
        actions.SetBestOf(n, 5)


def _view(app, n):
    """What the operations below write: the teams and the match bar."""
    def get(key):
        return app.get(f"score.{n}.{key}")
    return {
        "teams": {team: {key: get(f"team.{team}.{key}")
                         for key in ("teamName", "score", "losers")}
                  for team in ("1", "2")},
        "match": {key: get(key)
                  for key in ("phase", "pool", "match", "match_en",
                              "best_of")},
        "teamsSwapped": bool(get("teamsSwapped")),
    }


def _goHeadless(app, monkeypatch):
    """No board built and the headless host: what main.py --headless runs.
    The skeleton is the one the headless shell seeds (StateSchema)."""
    from src.core import StateSchema as schema
    app.dropAllBoards()
    with _state().SaveBlock():
        for number in (1, 2, 3):
            schema.SeedBoard(number)
    app.settle()
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())


@pytest.fixture
def headless(autonaApp, monkeypatch):
    _goHeadless(autonaApp, monkeypatch)
    try:
        yield autonaApp
    finally:
        for number in (1, 2, 3):
            _state().Unset(f"score.{number}")
        autonaApp.settle()


# ── the same state from both hosts ───────────────────────────────────────

OPERATIONS = {
    "swap": lambda n: WebOps.swap_teams(n),
    "reset scores": lambda n: WebOps.reset_scores(n),
    "reset match": lambda n: WebOps.reset_match(n),
    "clear all": lambda n: WebOps.clear_all(n),
    "set fields": lambda n: WebOps.set_route(
        n, phase="Top 8", pool="", match="Grand Final", bestOf=3),
    "team name": lambda n: WebOps.set_route(n, team="2", teamName="Charlie"),
    "set losers": lambda n: WebOps.set_route(n, team="1", losers="true"),
    "team losers": lambda n: WebOps.set_team_losers(n, 1, True),
    "score up": lambda n: WebOps.team_scoreup(n, 1),
    "scores": lambda n: WebOps.set_scores(n, 3, 0),
}


@pytest.mark.parametrize("name", list(OPERATIONS))
def test_both_hosts_write_the_same_state(autonaApp, monkeypatch, name):
    operation = OPERATIONS[name]

    # The desktop: the board built (held), the operation on the GUI thread.
    autonaApp.board(BOARD)
    _prepare(BOARD)
    autonaApp.settle()
    # 反映 first, so what is pending afterwards is the operation's.
    WebOps.apply_changes(BOARD)
    autonaApp.settle()
    assert not _state().HasPendingChanges(f"score.{BOARD}")
    operation(BOARD)
    autonaApp.settle()
    desktop = _view(autonaApp, BOARD)
    # Held for 反映 there, as every write to a built board is.
    assert _state().HasPendingChanges(f"score.{BOARD}")

    # Headless: no board, the write published at once.
    try:
        _goHeadless(autonaApp, monkeypatch)
        _prepare(BOARD)
        autonaApp.settle()
        operation(BOARD)
        autonaApp.settle()
        headless = _view(autonaApp, BOARD)
        assert not _state().HasPendingChanges(f"score.{BOARD}")
        assert _state().GetCommitted(f"score.{BOARD}.team.1.teamName") == (
            headless["teams"]["1"]["teamName"])
    finally:
        for number in (1, 2, 3):
            _state().Unset(f"score.{number}")
        autonaApp.settle()

    assert headless == desktop


# ── headless ─────────────────────────────────────────────────────────────

def test_a_headless_write_is_published_at_once(headless):
    assert WebOps.team_scoreup(1, 1) == "OK"
    headless.settle()

    assert _state().GetCommitted("score.1.team.1.score") == 1
    assert not _state().HasPendingChanges("score.1")
    assert WebOps.get_scoreboard(1)["team"]["1"]["score"] == 1


def test_headless_clears_the_players_without_a_board(headless):
    state = _state()
    with state.SaveBlock():
        state.Set("score.1.team.1.teamName", "Alpha")
        state.Set("score.1.team.1.player.1.name", "LEFT")
        state.Set("score.1.team.2.player.1.name", "RIGHT")
    headless.settle()

    assert WebOps.reset_players(1) == "OK"
    headless.settle()
    assert headless.get("score.1.team.1.player.1.name") == ""
    assert headless.get("score.1.team.2.player.1.name") == ""

    state.Set("score.1.team.1.player.1.name", "AGAIN")
    assert WebOps.clear_all(1) == "OK"
    headless.settle()
    assert headless.get("score.1.team.1.player.1.name") == ""
    # The legacy clear keeps the team names (ADR-005 S2 decision 3).
    assert headless.get("score.1.team.1.teamName") == "Alpha"


def test_headless_has_no_last_set(headless):
    assert WebOps.get_set(1) == "0"
    assert WebOps.get_swap(1) == "False"


# What only a widget answers: the crew board's own operations, the player /
# character counts. Refused before anything is written.
REFUSED = {
    "crew swap": lambda: WebOps.swap_teams(3),
    "crew swap flag": lambda: WebOps.get_swap(3),
    "crew reset scores": lambda: WebOps.reset_scores(3),
    "crew reset match": lambda: WebOps.reset_match(3),
    "crew reset players": lambda: WebOps.reset_players(3),
    "crew clear all": lambda: WebOps.clear_all(3),
    "crew set fields": lambda: WebOps.set_route(3, match="Grand Final"),
    "player count": lambda: WebOps.set_route(1, players=2, match="Final"),
    "character count": lambda: WebOps.set_route(1, characters=2),
}


@pytest.mark.parametrize("name", list(REFUSED))
def test_headless_refuses_what_needs_a_widget(headless, name):
    before = json.dumps(headless.get("score"), sort_keys=True)

    with pytest.raises(NotAvailable) as refused:
        REFUSED[name]()
    headless.settle()

    assert refused.value.message == "not available in headless"
    assert json.dumps(headless.get("score"), sort_keys=True) == before


def test_the_headless_host_runs_one_operation_at_a_time():
    # One lock: an operation another request's thread asks for waits for
    # the one running. Re-entrant: an operation may run another (v1's
    # PATCH set runs set_route inside its own task).
    host = BoardHost()
    inside = threading.Event()
    release = threading.Event()
    order = []

    def first():
        order.append("first")
        inside.set()
        release.wait(5)
        host.Run(lambda: order.append("first, nested"))
        order.append("first, done")

    running = threading.Thread(target=host.Run, args=(first,))
    running.start()
    assert inside.wait(5)
    waiting = threading.Thread(
        target=host.Run, args=(lambda: order.append("second"),))
    waiting.start()
    waiting.join(0.3)
    try:
        assert waiting.is_alive()
        assert order == ["first"]
    finally:
        release.set()
        running.join(5)
        waiting.join(5)

    assert not running.is_alive() and not waiting.is_alive()
    assert order == ["first", "first, nested", "first, done", "second"]


def test_a_headless_clear_is_not_interleaved_with_another_request(
        headless, monkeypatch):
    # clear_all is four steps; a swap asked for while it is between two of
    # them lands after the clear, not inside it.
    host = WebOps.boardHost
    midway = threading.Event()
    release = threading.Event()

    def resetCounts(board):   # the step between the reset and the players
        midway.set()
        release.wait(5)

    monkeypatch.setattr(host, "ResetCounts", resetCounts)
    state = _state()
    state.Set("score.1.team.1.player.1.name", "LEFT")
    headless.settle()

    clearing = threading.Thread(target=WebOps.clear_all, args=(1,))
    clearing.start()
    assert midway.wait(5)
    swapping = threading.Thread(target=WebOps.swap_teams, args=(1,))
    swapping.start()
    swapping.join(0.3)
    try:
        assert swapping.is_alive()
        assert not state.Get("score.1.teamsSwapped", False)
        assert state.Get("score.1.team.1.player.1.name") == "LEFT"
    finally:
        release.set()
        clearing.join(5)
        swapping.join(5)
    headless.settle()

    assert not clearing.is_alive() and not swapping.is_alive()
    assert state.Get("score.1.teamsSwapped") is True
    assert state.Get("score.1.team.1.player.1.name") == ""


def test_get_set_is_zero_headless_rather_than_refused(headless):
    # "0" is the answer for "no set loaded", and with no board none is --
    # the crew board included (the desktop's crew board has no set loader
    # and raises; headless there is nothing to ask).
    assert WebOps.get_set(2) == "0"
    assert WebOps.get_set(3) == "0"


# ── the 503 answers ──────────────────────────────────────────────────────

@pytest.fixture
def server(monkeypatch, tmp_path):
    """The real Flask app + Socket.IO server with no board-dependent actions
    (the headless shell's WebServer(actions=None)), cwd = tmp_path."""
    from src import AutonaApiV1
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app
    from src.core import WebAccess
    from src.core.SettingsManager import SettingsManager

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WebServer, "actions", None)
    monkeypatch.setattr(AutonaApiV1, "_actions", None)
    monkeypatch.setattr(AutonaApiV1, "_initialized", True)
    monkeypatch.setattr(WebServer, "_lan_hosts", ())
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())

    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    monkeypatch.setattr(WebServer, "socketio", socketio)
    return app, socketio


def test_a_legacy_route_answers_503(server):
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.get("/scoreboard1-open-set")

    assert response.status_code == 503
    assert response.get_json() == UNAVAILABLE_BODY


def test_a_socketio_event_acks_the_503_body(server):
    app, socketio = server
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=flask_client)
    ws.get_received()

    ack = ws.emit("open_set", json.dumps({"scoreboardNumber": "1"}),
                  callback=True)

    assert ack == UNAVAILABLE_BODY
    assert ws.get_received() == []


# What a web operation itself refuses headless (the board host's
# NotAvailable, not WebServer._board_actions()): the same 503.
REFUSED_ROUTES = [
    "/scoreboard3-swap-teams",
    "/scoreboard3-get-swap",
    "/scoreboard1-set?players=2",
]


@pytest.mark.parametrize("path", REFUSED_ROUTES)
def test_a_legacy_route_a_web_operation_refuses_answers_503(
        server, monkeypatch, path):
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.get(path)

    assert response.status_code == 503
    assert response.get_json() == UNAVAILABLE_BODY


@pytest.mark.parametrize("event,body", [
    ("swap_teams", {"scoreboardNumber": "3"}),
    ("set", {"scoreboardNumber": "1", "players": 2}),
])
def test_a_socketio_event_a_web_operation_refuses_acks_the_503_body(
        server, monkeypatch, event, body):
    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    app, socketio = server
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=flask_client)
    ws.get_received()

    ack = ws.emit(event, json.dumps(body), callback=True)

    assert ack == UNAVAILABLE_BODY
    assert ws.get_received() == []


@pytest.mark.parametrize("path,body", [
    ("/api/v1/bracket:refresh", None),
    ("/api/v1/scoreboards/1:load-set", {"setId": "1"}),
    ("/api/v1/scoreboards/1:pull-stream", None),
    ("/api/v1/scoreboards/1/stats:refresh", None),
])
def test_a_v1_operation_answers_unavailable(server, path, body):
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.post(path, json=body or {})

    assert response.status_code == 503, response.get_json()
    assert response.get_json()["ok"] is False
    assert response.get_json()["error"]["code"] == "UNAVAILABLE"


def test_a_v1_player_edit_answers_unavailable(server):
    app, _ = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    response = client.patch("/api/v1/scoreboards/1/teams/1/players/1",
                            json={"name": "Tag"})

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "UNAVAILABLE"


def test_the_board_actions_are_refused_without_a_shell_that_has_them(
        monkeypatch):
    from src.AutonaWebServer import WebServer
    monkeypatch.setattr(WebServer, "actions", None)

    with pytest.raises(NotAvailable):
        WebServer._board_actions()
