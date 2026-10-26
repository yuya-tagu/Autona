"""External control API (/api/v1) as the bundled Web UI uses it.

ADR-001 Addendum (2026-10-21) deprecates the legacy routes and makes moving
the Web UI (web/src) onto /api/v1 a precondition for removing them in v2.0.
Four operations it used had no v1 counterpart and were added for it:

- ``POST /api/v1/scoreboards/{n}/score:set``          (legacy ``POST /score``)
- ``PATCH /api/v1/scoreboards/{n}/teams/{t}/players/{p}``
                          (legacy ``POST /scoreboard<N>-update-team-<T>-<P>``)
- ``PATCH /api/v1/display-options``           (legacy ``POST /display-option``)
- ``GET /api/v1/countries/{code}/states``     (legacy ``GET /states``)

and ``PATCH /api/v1/scoreboards/{n}/set`` learned ``teamName`` (legacy
``-set?teamName=&team=``).

Most of the tests pin that a v1 call leaves the *same state* as its legacy
route: the legacy request runs on a freshly built board, the v1 request on
another freshly built board, and the resulting ``score.{n}`` subtrees are
compared. The Web UI's calls that moved onto routes v1 already had are
checked the same way, so the migration itself is pinned as
behavior-preserving.

Where v1 deliberately differs, the difference is pinned instead. Every v1
team parameter names the *state* team -- the column the operator (and the
Web UI) sees -- so score:set and the ``losers`` of PATCH set land on the same
side after a swap, where the legacy ``/score`` and ``-set?losers=`` flip with
it. And score:set / the player PATCH do not carry the legacy set-payload side
effects (``general.disable_scoreupdate`` zeroing the scores, ``match_en``
being blanked).

Both requests go through Flask's test client against the real route tables --
the legacy ``_build_web_app()`` factory plus the v1 blueprint registered the
way ``WebServer.__init__`` registers it -- so nothing between the URL and the
action is stubbed. Requests run on the test thread, which is the GUI thread,
so ``@gui_thread_sync`` executes inline.
"""
import copy
import json
import threading
import types
from unittest.mock import patch

import pytest

from src.core.SettingsManager import SettingsManager
# goldenGames / onLoadCount are fixtures: the S1a fixture games installed
# into the harness, and the count of onLoad emissions.
from tests.test_game_asset_manager_models import (
    GOLDEN, SCENARIOS, _load, _waitForLoader, goldenGames, onLoadCount)
from tests.test_scoreboard_operations import (
    _restoredDisplayOption, _setPlayerName)

_realSettingsGet = SettingsManager.Get


@pytest.fixture(scope="module")
def webApp():
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import _build_web_app

    app, _socketio = _build_web_app()
    app.register_blueprint(api_v1)
    return app


@pytest.fixture
def client(autonaApp, webApp):
    """A test client for the legacy and v1 routes. Both reach the same web
    operations (src/AutonaWebOperations.py) through what bootstrap_gui()
    registered on the real manager -- nothing to hand the server since
    ADR-005 S3e-9 (it used to take the board's actions object).
    """
    return webApp.test_client()


def _boardState(app, n):
    return copy.deepcopy(app.get(f"score.{n}"))


def _sameStateAsLegacy(app, n, legacy, v1, prepare=None, changes=True,
                       ignore=()):
    """Run `legacy` and `v1` on two fresh copies of board `n`; compare.

    `legacy` / `v1` issue the request(s) and return the last response;
    `prepare(board)` puts the board into the state both start from.
    `changes` asserts the request actually moved the board, so two no-ops
    cannot pass as "the same result". `ignore` names top-level keys of
    score.{n} left out of the comparison (a documented legacy side effect
    v1 does not have). Returns the resulting score.{n} of the v1 run.
    """
    results = []
    for run, isV1 in ((legacy, False), (v1, True)):
        board = app.board(n)
        if prepare is not None:
            prepare(board)
        app.settle()
        before = _boardState(app, n)
        response = run()
        assert response.status_code == 200, response.get_data(as_text=True)
        if isV1:
            assert response.get_json()["ok"] is True
        app.settle()
        after = _boardState(app, n)
        assert isinstance(after, dict) and after
        if changes:
            assert after != before, "the request left the board unchanged"
        results.append(after)

    legacyState, v1State = results
    strip = lambda state: {k: v for k, v in state.items() if k not in ignore}
    assert strip(v1State) == strip(legacyState)
    return v1State


def _scoreTwoOne(board):
    board.CommandScoreChange(0, 2)
    board.CommandScoreChange(1, 1)


def _assertRejected(response, code="INVALID_ARGUMENT", status=400):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == code


# ── score:set (legacy POST /score) ───────────────────────────────────────

@pytest.mark.parametrize("n", [1, 2])
def test_score_set_matches_legacy_post_score(autonaApp, client, n):
    # On a board that is not swapped. `match_en` is the legacy route's side
    # effect (it blanks it), pinned separately below.
    state = _sameStateAsLegacy(
        autonaApp, n,
        legacy=lambda: client.post("/score", data=json.dumps(
            {"scoreboard": n, "team1score": 2, "team2score": 1})),
        v1=lambda: client.post(
            f"/api/v1/scoreboards/{n}/score:set", json={"team1": 2, "team2": 1}),
        ignore=("match_en",),
    )
    assert state["team"]["1"]["score"] == 2
    assert state["team"]["2"]["score"] == 1


@pytest.mark.parametrize("n", [1, 2])
def test_score_set_writes_the_state_teams_on_a_swapped_board(
        autonaApp, client, n):
    """The Web UI's ± after a swap lands on the side it was pressed on.

    The Web UI shows score.{n}.team.1 on the left whatever the swap state
    (so does the desktop: SwapTeams exchanges the values, not the columns)
    and sends {team1: left, team2: right}. The legacy POST /score is a
    set-shaped write that runs team1 through DisplayTeam(), so after a swap
    a tap on the left score wrote the right one. score:set names state
    teams, like score:adjust.
    """
    board = autonaApp.board(n)
    board.CommandScoreChange(0, 3)   # team 1 = 3, team 2 = 0
    board.SwapTeams()                # now team 1 = 0, team 2 = 3
    autonaApp.settle()
    assert autonaApp.get(f"score.{n}.team.1.score") == 0
    assert autonaApp.get(f"score.{n}.team.2.score") == 3

    # The left (team 1) score's + : {team1: 0 + 1, team2: 3}. The legacy
    # route wrote this as team 1 = 3, team 2 = 1.
    response = client.post(
        f"/api/v1/scoreboards/{n}/score:set", json={"team1": 1, "team2": 3})
    assert response.status_code == 200
    autonaApp.settle()

    assert autonaApp.get(f"score.{n}.team.1.score") == 1
    assert autonaApp.get(f"score.{n}.team.2.score") == 3
    assert autonaApp.get(f"score.{n}.teamsSwapped") is True


def test_score_set_does_not_carry_the_set_payload_side_effects(
        autonaApp, client):
    """Neither of POST /score's set-load side effects reaches score:set.

    general.disable_scoreupdate is a provider setting (do not take scores
    from start.gg) -- on the legacy route it made a typed score zero both
    teams. And ChangeSetData writes match_en unconditionally, so every
    legacy score tap blanked the English round text.
    """
    autonaApp.board(1)
    autonaApp.state.Set("score.1.match_en", "Winners Final")
    autonaApp.settle()

    def settings(key, default=None):
        if key == "general.disable_scoreupdate":
            return True
        return _realSettingsGet(key, default)

    with patch.object(SettingsManager, "Get", side_effect=settings):
        response = client.post(
            "/api/v1/scoreboards/1/score:set", json={"team1": 3, "team2": 1})
    assert response.status_code == 200
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.score") == 3
    assert autonaApp.get("score.1.team.2.score") == 1
    assert autonaApp.get("score.1.match_en") == "Winners Final"


def test_score_set_clamps_and_leaves_an_unchanged_board_clean(autonaApp, client):
    autonaApp.board(1)
    autonaApp.state.CommitHeld("score.1")
    autonaApp.settle()

    # Re-asserting the current (zero) scores is not an edit: the 反映
    # button must stay off.
    client.post("/api/v1/scoreboards/1/score:set", json={"team1": 0, "team2": 0})
    autonaApp.settle()
    assert not autonaApp.state.HasPendingChanges("score.1")

    client.post(
        "/api/v1/scoreboards/1/score:set", json={"team1": 150, "team2": -4})
    autonaApp.settle()
    assert autonaApp.get("score.1.team.1.score") == 99
    assert autonaApp.get("score.1.team.2.score") == 0


@pytest.mark.parametrize("path, body", [
    ("/api/v1/scoreboards/1/score:set", {"team1": 2}),
    ("/api/v1/scoreboards/1/score:set", {"team2": 2}),
    ("/api/v1/scoreboards/1/score:set", {"team1": "two", "team2": 1}),
    ("/api/v1/scoreboards/3/score:set", {"team1": 2, "team2": 1}),
    ("/api/v1/scoreboards/4/score:set", {"team1": 2, "team2": 1}),
])
def test_score_set_rejects_bad_input_without_writing(
        autonaApp, client, path, body):
    autonaApp.board(1)

    _assertRejected(client.post(path, json=body))
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.score") == 0
    assert autonaApp.get("score.1.team.2.score") == 0


# ── player PATCH (legacy POST /scoreboard<N>-update-team-<T>-<P>) ─────────

_PLAYER = {
    "gamerTag": "Alpha",
    "prefix": "TM",
    "name": "Real Name",
    "twitter": "alpha_x",
    "custom_textbox": "note",
    # Never True here: it would write the harness player DB, which is
    # process-global and shared with the rest of the session.
    "savePlayerToDb": False,
}


@pytest.mark.parametrize("n, t", [(1, 1), (1, 2), (2, 1)])
def test_player_patch_matches_legacy_update_team(autonaApp, client, n, t):
    # `match_en` is the legacy route's side effect, pinned separately below.
    state = _sameStateAsLegacy(
        autonaApp, n,
        legacy=lambda: client.post(
            f"/scoreboard{n}-update-team-{t}-1", json=dict(_PLAYER)),
        v1=lambda: client.patch(
            f"/api/v1/scoreboards/{n}/teams/{t}/players/1", json=dict(_PLAYER)),
        ignore=("match_en",),
    )
    player = state["team"][str(t)]["player"]["1"]
    assert player["name"] == "Alpha"
    assert player["team"] == "TM"
    assert player["real_name"] == "Real Name"


def test_player_patch_addresses_the_state_team_on_a_swapped_board(
        autonaApp, client):
    # The legacy update-team action already named the state team directly;
    # the v1 route keeps that.
    state = _sameStateAsLegacy(
        autonaApp, 1,
        legacy=lambda: client.post(
            "/scoreboard1-update-team-1-1", json={"gamerTag": "Alpha"}),
        v1=lambda: client.patch(
            "/api/v1/scoreboards/1/teams/1/players/1", json={"gamerTag": "Alpha"}),
        prepare=lambda board: board.SwapTeams(),
        ignore=("match_en",),
    )
    assert state["team"]["1"]["player"]["1"]["name"] == "Alpha"


def test_set_patch_losers_still_blanks_the_english_round_text(
        autonaApp, client):
    """An external behaviour kept on purpose.

    v1's losers used to go through set_route, i.e. a ChangeSetData payload,
    whose match-info writer blanks match_en for any payload without one.
    Since ADR-005 S2b it is AutonaActions.SetTeamLosers, and the route
    applies that match-info half itself (SetLoadService.ApplyMatchInfo, in
    the same save cycle) so the request still does what it did -- also when
    the flag does not change.
    """
    from src.core.StateManager import StateManager

    autonaApp.board(1)
    autonaApp.state.Set("score.1.match_en", "Winners Final")
    autonaApp.settle()

    cycles = []
    StateManager.observers.held_state_updated.subscribe(cycles.append)
    try:
        response = client.patch("/api/v1/scoreboards/1/set", json={
            "losers": {"team": 1, "value": True}})
        assert response.status_code == 200
        autonaApp.settle()
    finally:
        StateManager.observers.held_state_updated.unsubscribe(cycles.append)

    assert autonaApp.get("score.1.team.1.losers") is True
    assert autonaApp.get("score.1.match_en") == ""
    assert cycles.count("score.1") == 1

    autonaApp.state.Set("score.1.match_en", "Winners Final")
    autonaApp.settle()
    response = client.patch("/api/v1/scoreboards/1/set", json={
        "losers": {"team": 1, "value": True}})
    assert response.status_code == 200
    autonaApp.settle()

    assert autonaApp.get("score.1.match_en") == ""


def test_set_patch_losers_on_a_board_not_built_yet_still_waits_for_apply(
        autonaApp, client):
    """The old route built the board first (set_route -> GetScoreboard), and
    the build is what holds score.{n} for 反映. v1's losers still builds it,
    so the flag is pending rather than published at once."""
    from src.core.StateManager import StateManager

    autonaApp._dropBoard(2)
    assert 2 not in autonaApp.manager.scoreboards

    response = client.patch("/api/v1/scoreboards/2/set", json={
        "losers": {"team": 1, "value": True}})
    assert response.status_code == 200
    autonaApp.settle()

    assert 2 in autonaApp.manager.scoreboards
    assert autonaApp.get("score.2.team.1.losers") is True
    assert StateManager.HasPendingChanges("score.2") is True


def test_player_patch_keeps_the_english_round_text(autonaApp, client):
    """The legacy route ran a player edit through all of ChangeSetData, whose
    match-info writer blanks match_en for any payload without one. The v1
    route applies only the player half (AutonaWebOperations.update_player
    since ADR-005 S3e-4; the board's ApplyPlayerData before).
    """
    autonaApp.board(1)
    autonaApp.state.Set("score.1.match_en", "Winners Final")
    autonaApp.settle()

    response = client.patch(
        "/api/v1/scoreboards/1/teams/1/players/1", json={"gamerTag": "Alpha"})
    assert response.status_code == 200
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.player.1.name") == "Alpha"
    assert autonaApp.get("score.1.match_en") == "Winners Final"


def test_player_patch_answers_404_for_a_slot_the_team_does_not_have(
        autonaApp, client):
    """The roster is the state's slots (ADR-005 S3e-4; it was the board's
    cards, which publish exactly those): a player beyond it is 404, with
    nothing written -- the set payload's match_en included, which only the
    legacy route carries."""
    board = autonaApp.board(2)
    board.playerNumber.setValue(2)
    autonaApp.state.Set("score.2.match_en", "Winners Final")
    autonaApp.settle()
    before = _boardState(autonaApp, 2)

    for path in ("/api/v1/scoreboards/2/teams/1/players/3",
                 "/api/v1/scoreboards/2/teams/2/players/9"):
        _assertRejected(client.patch(path, json={"gamerTag": "Nobody"}),
                        "NOT_FOUND", 404)
    autonaApp.settle()
    assert _boardState(autonaApp, 2) == before

    response = client.patch("/api/v1/scoreboards/2/teams/2/players/2",
                            json={"gamerTag": "Second"})
    assert response.status_code == 200
    autonaApp.settle()
    assert autonaApp.get("score.2.team.2.player.2.name") == "Second"


def test_player_patch_on_a_board_not_built_yet_waits_for_apply(
        autonaApp, client):
    """As every board operation (ADR-005 S2c 判断 7): the board is built
    first -- which is what writes its slots -- so the edit is held for 反映
    rather than published at once and wiped by the build's seed."""
    autonaApp._dropBoard(1)
    assert 1 not in autonaApp.manager.scoreboards

    response = client.patch("/api/v1/scoreboards/1/teams/2/players/1",
                            json={"gamerTag": "Later"})
    assert response.status_code == 200, response.get_json()
    autonaApp.settle()

    assert 1 in autonaApp.manager.scoreboards
    assert autonaApp.get("score.1.team.2.player.1.name") == "Later"
    assert autonaApp.state.HasPendingChanges("score.1") is True
    card = autonaApp.manager.scoreboards[1].team2playerWidgets[0]
    from qtpy.QtWidgets import QLineEdit
    assert card.findChild(QLineEdit, "name").text() == "Later"


def test_the_legacy_player_routes_answer_as_before(autonaApp, client):
    """Their bodies (ADR-005 S3e-4 moved them, not their answers): update-team
    "OK" whatever it wrote, the tag lookup "OK" / "ERROR" / "No tag
    provided"."""
    from src.core.PlayerDB import PlayerDB

    autonaApp.board(1)
    assert client.post("/scoreboard1-update-team-1-1",
                       json={"gamerTag": "Alpha"}).get_data(
        as_text=True) == "OK"
    assert client.post("/scoreboard1-update-team-1-4",
                       json={"gamerTag": "Nowhere"}).get_data(
        as_text=True) == "OK"
    with patch.object(PlayerDB.instance, "GetPlayerFromTag",
                      side_effect=lambda tag: {"gamerTag": "Found"}
                      if tag == "found" else None):
        for path, answer in (
                ("/scoreboard1-load-player-from-tag-2-1?tag=found", "OK"),
                ("/scoreboard1-load-player-from-tag-2-1?tag=lost", "ERROR"),
                ("/scoreboard1-load-player-from-tag-2-1", "No tag provided")):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.get_data(as_text=True) == answer, path
    autonaApp.settle()

    assert autonaApp.get("score.1.team.1.player.1.name") == "Alpha"
    assert autonaApp.get("score.1.team.2.player.1.name") == "Found"


@pytest.mark.parametrize("path, body", [
    ("/api/v1/scoreboards/1/teams/1/players/0", {"gamerTag": "Alpha"}),
    ("/api/v1/scoreboards/1/teams/3/players/1", {"gamerTag": "Alpha"}),
    ("/api/v1/scoreboards/3/teams/1/players/1", {"gamerTag": "Alpha"}),
    ("/api/v1/scoreboards/1/teams/1/players/1", {"gamerTag": 5}),
    ("/api/v1/scoreboards/1/teams/1/players/1", {"mains": ["Mario"]}),
    # Fields outside the Web UI's set are refused rather than passed through
    # (`avatar` would make the server fetch an arbitrary URL).
    ("/api/v1/scoreboards/1/teams/1/players/1",
     {"gamerTag": "Alpha", "avatar": "http://example.invalid/a.png"}),
])
def test_player_patch_rejects_bad_input_without_writing(
        autonaApp, client, path, body):
    autonaApp.board(1)
    before = _boardState(autonaApp, 1)

    _assertRejected(client.patch(path, json=body))
    autonaApp.settle()

    assert _boardState(autonaApp, 1) == before


def test_player_patch_keeps_personal_data_out_of_the_op_log(autonaApp, client):
    from loguru import logger

    autonaApp.board(1)
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="INFO")
    try:
        response = client.patch(
            "/api/v1/scoreboards/1/teams/1/players/1",
            json={"gamerTag": "SecretTag", "name": "Secret Name"})
    finally:
        logger.remove(sink)

    assert response.status_code == 200
    opLog = [line for line in lines if "op=player.update" in line]
    assert len(opLog) == 1
    assert "gamerTag" in opLog[0]
    assert "SecretTag" not in opLog[0]
    assert "Secret Name" not in opLog[0]


# ── PATCH /display-options (legacy POST /display-option) ─────────────────

def test_display_options_patch_matches_legacy_display_option(
        autonaApp, client):
    autonaApp.board(1)
    results = []
    for request in (
        lambda: client.post("/display-option", data=json.dumps(
            {"option": "show_seed", "value": False})),
        lambda: client.patch(
            "/api/v1/display-options", json={"show_seed": False}),
    ):
        with _restoredDisplayOption("show_seed"):
            response = request()
            assert response.status_code == 200
            autonaApp.settle()
            results.append((autonaApp.get("display_options.show_seed"),
                            SettingsManager.Get("display_options.show_seed")))

    assert results[1] == results[0] == (False, False)


def test_display_options_patch_rejects_an_unknown_key_without_applying_any(
        autonaApp, client):
    autonaApp.board(1)
    with _restoredDisplayOption("show_seed"):
        before = autonaApp.get("display_options.show_seed")

        _assertRejected(client.patch(
            "/api/v1/display-options",
            json={"show_seed": not before, "show_nothing": True}))
        autonaApp.settle()

        assert autonaApp.get("display_options.show_seed") == before
        assert autonaApp.get("display_options.show_nothing") is None


# ── PATCH .../set teamName (legacy -set?teamName=&team=) ─────────────────

def test_set_patch_team_name_matches_legacy_query(autonaApp, client):
    state = _sameStateAsLegacy(
        autonaApp, 2,
        legacy=lambda: client.get("/scoreboard2-set?teamName=Alpha&team=1"),
        v1=lambda: client.patch(
            "/api/v1/scoreboards/2/set",
            json={"teamName": {"team": 1, "value": "Alpha"}}),
    )
    assert state["team"]["1"]["teamName"] == "Alpha"


def test_set_patch_team_name_and_losers_for_different_teams(autonaApp, client):
    # set_route takes one `team` for both; the v1 route splits the call.
    def legacy():
        client.get("/scoreboard2-set?losers=true&team=2")
        return client.get("/scoreboard2-set?teamName=Alpha&team=1")

    state = _sameStateAsLegacy(
        autonaApp, 2,
        legacy=legacy,
        v1=lambda: client.patch("/api/v1/scoreboards/2/set", json={
            "losers": {"team": 2, "value": True},
            "teamName": {"team": 1, "value": "Alpha"},
        }),
    )
    assert state["team"]["1"]["teamName"] == "Alpha"
    assert state["team"]["2"]["losers"] is True


def test_set_patch_players_and_losers_together_match_legacy_query(
        autonaApp, client):
    """A grown roster and the losers flag in one PATCH.

    The legacy query applies them in this order (set_route: players, then
    losers), and so does v1: set_route for the roster, then set_team_losers
    for the flag (ADR-005 S2b). The flag's rebuild of the team's names
    therefore sees the new slots on both routes, and leaves the same state.
    """
    from qtpy.QtWidgets import QCheckBox, QLineEdit

    def prepare(board):
        _setPlayerName(board, 1, "AAA")
        _setPlayerName(board, 2, "BBB")

    state = _sameStateAsLegacy(
        autonaApp, 2,
        legacy=lambda: client.get(
            "/scoreboard2-set?players=3&losers=true&team=1"),
        v1=lambda: client.patch("/api/v1/scoreboards/2/set", json={
            "players": 3, "losers": {"team": 1, "value": True}}),
        prepare=prepare,
    )

    for team in ("1", "2"):
        assert sorted(state["team"][team]["player"]) == ["1", "2", "3"]
    assert state["team"]["1"]["losers"] is True
    assert state["team"]["2"]["losers"] is False
    assert state["team"]["1"]["player"]["1"]["mergedName"] == "AAA [L]"
    assert state["team"]["1"]["player"]["1"]["mergedOnlyName"] == "AAA [L]"
    assert state["team"]["2"]["player"]["1"]["mergedName"] == "BBB"

    # The board shows what the state holds.
    board = autonaApp.manager.scoreboards[2]
    assert board.team1column.findChild(QCheckBox, "losers").isChecked()
    assert not board.team2column.findChild(QCheckBox, "losers").isChecked()
    for team, cards in (("1", board.team1playerWidgets),
                        ("2", board.team2playerWidgets)):
        assert len(cards) == 3
        for slot, card in enumerate(cards, start=1):
            assert card.findChild(QLineEdit, "name").text() == \
                (state["team"][team]["player"][str(slot)].get("name") or "")


@pytest.mark.parametrize("n", [1, 2])
@pytest.mark.parametrize("team", [1, 2])
def test_losers_lands_on_the_state_team_on_a_swapped_board(
        autonaApp, client, n, team):
    """The Web UI's Losers checkbox after a swap flags the side it is on.

    The checkbox sits in the column of score.{n}.team.{t} and sends that t.
    set_route's losers is set-shaped (SetLoadService.ApplyLosers runs it
    through DisplayTeam), so the legacy -set?losers= flagged the other team
    on a swapped board. v1 calls AutonaActions.SetTeamLosers with the state
    team (ADR-005 S2b; it used to flip the team back before set_route) --
    the flag and the " [L]" suffix on that team's player names both land on
    team t.
    """
    board = autonaApp.board(n)
    _setPlayerName(board, 1, "AAA")
    _setPlayerName(board, 2, "BBB")
    board.SwapTeams()
    autonaApp.settle()
    names = {t: autonaApp.get(f"score.{n}.team.{t}.player.1.name")
             for t in (1, 2)}
    assert names == {1: "BBB", 2: "AAA"}
    other = 3 - team

    response = client.patch(f"/api/v1/scoreboards/{n}/set", json={
        "losers": {"team": team, "value": True}})
    assert response.status_code == 200
    autonaApp.settle()

    assert autonaApp.get(f"score.{n}.team.{team}.losers") is True
    assert autonaApp.get(f"score.{n}.team.{other}.losers") is False
    assert autonaApp.get(
        f"score.{n}.team.{team}.player.1.mergedName") == f"{names[team]} [L]"
    assert autonaApp.get(
        f"score.{n}.team.{other}.player.1.mergedName") == names[other]


def test_team_name_lands_on_the_state_team_on_a_swapped_board(
        autonaApp, client):
    # set_route already wrote the literal column; nothing to correct.
    board = autonaApp.board(2)
    board.SwapTeams()
    autonaApp.settle()

    response = client.patch("/api/v1/scoreboards/2/set", json={
        "teamName": {"team": 1, "value": "Alpha"}})
    assert response.status_code == 200
    autonaApp.settle()

    assert autonaApp.get("score.2.team.1.teamName") == "Alpha"
    assert autonaApp.get("score.2.team.2.teamName") != "Alpha"


@pytest.mark.parametrize("body", [
    {"teamName": "Alpha"},
    {"teamName": {"team": 3, "value": "Alpha"}},
    {"teamName": {"team": 1, "value": 5}},
])
def test_set_patch_rejects_a_bad_team_name(autonaApp, client, body):
    autonaApp.board(2)

    _assertRejected(client.patch("/api/v1/scoreboards/2/set", json=body))


# ── the Web UI's other calls, moved onto routes v1 already had ───────────

_WEB_UI_CALLS = {
    # name: (legacy request, v1 request, prepare, changes the board)
    "swap-teams": (
        lambda c, n: c.get(f"/scoreboard{n}-swap-teams"),
        lambda c, n: c.post(f"/api/v1/scoreboards/{n}:swap-teams"),
        _scoreTwoOne, True),
    "reset-scores": (
        lambda c, n: c.get(f"/scoreboard{n}-reset-scores"),
        lambda c, n: c.post(f"/api/v1/scoreboards/{n}/score:reset"),
        _scoreTwoOne, True),
    "clear-all": (
        lambda c, n: c.get(f"/scoreboard{n}-clear-all"),
        lambda c, n: c.post(
            f"/api/v1/scoreboards/{n}:clear", json={"confirm": True}),
        _scoreTwoOne, True),
    "match bar": (
        lambda c, n: c.get(
            f"/scoreboard{n}-set?phase=Top+8&pool=A1"
            f"&match=Winners+Final&best-of=5"),
        lambda c, n: c.patch(f"/api/v1/scoreboards/{n}/set", json={
            "phase": "Top 8", "pool": "A1",
            "match": "Winners Final", "bestOf": 5}),
        None, True),
    # A blank phase is ignored by the action (an empty round/phase means
    # "not carried"), on both routes alike.
    "blank phase": (
        lambda c, n: c.get(f"/scoreboard{n}-set?phase="),
        lambda c, n: c.patch(
            f"/api/v1/scoreboards/{n}/set", json={"phase": ""}),
        None, False),
    "losers": (
        lambda c, n: c.get(f"/scoreboard{n}-set?losers=true&team=1"),
        lambda c, n: c.patch(f"/api/v1/scoreboards/{n}/set", json={
            "losers": {"team": 1, "value": True}}),
        None, True),
}


@pytest.mark.parametrize("n", [1, 2])
@pytest.mark.parametrize("call", sorted(_WEB_UI_CALLS))
def test_the_web_ui_calls_leave_the_same_state_on_v1(
        autonaApp, client, call, n):
    legacy, v1, prepare, changes = _WEB_UI_CALLS[call]
    _sameStateAsLegacy(
        autonaApp, n,
        legacy=lambda: legacy(client, n),
        v1=lambda: v1(client, n),
        prepare=prepare,
        changes=changes,
    )


# ── GET /games, /characters, /variants (ADR-005 S1b) ─────────────────────
# Read from the game asset catalog since S1b instead of the Qt models (and
# no longer on the GUI thread). Both routes must still answer what the
# pre-S1a recording holds (tests/fixtures/game_assets/golden.json).

@pytest.mark.parametrize("label", sorted(SCENARIOS))
def test_the_game_asset_reads_return_the_recorded_payloads(
        client, goldenGames, label):
    codename, modsActive, reload = SCENARIOS[label]
    _load(goldenGames, codename, modsActive, reload)
    payloads = GOLDEN["scenarios"][label]["payloads"]

    for name in ("games", "characters", "variants"):
        legacy = client.get(f"/{name}")
        v1 = client.get(f"/api/v1/{name}")

        assert legacy.status_code == v1.status_code == 200, name
        assert v1.get_json()["ok"] is True, name
        assert v1.get_json()["data"] == payloads[name], name
        assert legacy.get_json() == payloads[name], name


# ── POST /game (legacy POST /update-game) ────────────────────────────────
# Both load the game by codename. They used to look its position in the
# scan up and load by position, which the load re-read later, so a rescan
# in between loaded another game. And the v1 in-progress flag waited for
# onLoad, which a load that did not happen never emits.

@pytest.fixture
def gameFlag():
    """The v1 "game" in-progress flag, released whatever the test did."""
    from src import AutonaApiV1

    yield AutonaApiV1._heavy
    AutonaApiV1._heavy.end("game")


def _aGameIsAddedAfterTheFirstRead(m):
    """A rescan lands right after the request first reads the scan: every
    later read has one more game, in front."""
    from src.core.GameAssetCatalog import GameAssetCatalog

    real = GameAssetCatalog.GameCodenames
    reads = []

    def codenames(self):
        reads.append(1)
        names = real(self)
        return names if len(reads) == 1 else ["aaa_added"] + names

    m.setattr(GameAssetCatalog, "GameCodenames", codenames)


def test_game_set_loads_the_requested_game_despite_a_rescan(
        client, goldenGames, autonaApp, gameFlag, monkeypatch):
    _load(goldenGames, "tgame")

    with monkeypatch.context() as m:
        _aGameIsAddedAfterTheFirstRead(m)
        response = client.post("/api/v1/game", json={"codename": "game2"})
        assert response.status_code == 202, response.get_data(as_text=True)
        _waitForLoader(goldenGames)
    autonaApp.settle()

    assert autonaApp.get("game.codename") == "game2"
    assert not gameFlag._inflight


def test_update_game_loads_the_requested_game_despite_a_rescan(
        client, goldenGames, autonaApp, monkeypatch):
    _load(goldenGames, "tgame")

    with monkeypatch.context() as m:
        _aGameIsAddedAfterTheFirstRead(m)
        response = client.post("/update-game", json={"codename": "game2"})
    autonaApp.settle()

    assert response.get_data(as_text=True) == "OK"
    assert autonaApp.get("game.codename") == "game2"


def test_an_unknown_game_is_still_rejected(client, goldenGames, gameFlag):
    _assertRejected(
        client.post("/api/v1/game", json={"codename": "nope"}),
        "NOT_FOUND", 404)
    assert not gameFlag._inflight
    response = client.post("/update-game", json={"codename": "nope"})
    assert response.get_data(as_text=True) == "Could not find game nope"


@pytest.mark.parametrize("how", ["skipped", "failed"])
def test_a_game_load_that_does_not_happen_releases_the_flag(
        client, goldenGames, autonaApp, gameFlag, onLoadCount, monkeypatch,
        how):
    """No onLoad, yet no 409 for the guard's TTL either.

    skipped: the codename passed the request's check, then a rescan dropped
    it, so the catalog skips the load. failed: the load raised.
    """
    _load(goldenGames, "tgame")
    onLoadCount.clear()

    with monkeypatch.context() as m:
        if how == "skipped":
            from src.core.GameAssetCatalog import GameAssetCatalog

            real = GameAssetCatalog.GameCodenames
            m.setattr(GameAssetCatalog, "GameCodenames",
                      lambda self: real(self) + ["dropped"])
            codename = "dropped"
        else:
            def broken(*args, **kwargs):
                raise RuntimeError("the load failed")

            m.setattr(goldenGames, "_Load", broken)
            codename = "game2"
        response = client.post("/api/v1/game", json={"codename": codename})
        assert response.status_code == 202, response.get_data(as_text=True)
        _waitForLoader(goldenGames)

    assert onLoadCount == []
    assert not gameFlag._inflight
    assert autonaApp.get("game.codename") == "tgame"

    # The next request is accepted, not answered 409.
    response = client.post("/api/v1/game", json={"codename": "game2"})
    assert response.status_code == 202, response.get_data(as_text=True)
    _waitForLoader(goldenGames)
    autonaApp.settle()
    assert autonaApp.get("game.codename") == "game2"
    assert not gameFlag._inflight


# ── GET /countries/{code}/states (legacy GET /states?countryCode=) ───────

def test_country_states_return_the_legacy_payload(autonaApp, client):
    legacy = client.get("/states?countryCode=JP")
    v1 = client.get("/api/v1/countries/JP/states")

    assert legacy.status_code == v1.status_code == 200
    assert v1.get_json()["ok"] is True
    assert v1.get_json()["data"] == legacy.get_json()
    assert v1.get_json()["data"], "JP should have states in the bundled data"


def test_an_unknown_country_is_not_found(autonaApp, client):
    _assertRejected(
        client.get("/api/v1/countries/XX-NOPE/states"), "NOT_FOUND", 404)


# ── POST /tournament (the Web UI's legacy /set-tournament) ───────────────

_EVENT_URL = "https://www.start.gg/tournament/autona-test/event/singles"


@pytest.fixture
def loadedTournament(autonaApp, monkeypatch):
    """A tournament provider already on _EVENT_URL, with no network behind it.

    SetTournamentSignal compares the requested URL with provider.url and
    returns early when they match, so a stand-in is all a same-URL reload
    touches. TOURNAMENT_URL (which load_tournament persists) is put back.
    """
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    monkeypatch.setattr(
        AutonaTournamentDataProvider.instance, "provider",
        types.SimpleNamespace(url=_EVENT_URL))
    missing = object()
    stored = SettingsManager.Get("TOURNAMENT_URL", missing)
    yield
    if stored is missing:
        SettingsManager.Unset("TOURNAMENT_URL")
    else:
        SettingsManager.Set("TOURNAMENT_URL", stored)


def test_reloading_the_loaded_tournament_releases_the_in_progress_flag(
        client, loadedTournament):
    """Re-sending the loaded URL used to hold the flag for the full TTL.

    The flag waited for tournament_changed, which SetTournamentSignal's
    same-URL early return never emits, so the Web UI's Load button answered
    409 for two minutes after a reload. It is released when the GUI-thread
    task that runs load_tournament finishes, whichever way it went.
    """
    from src import AutonaApiV1

    for _ in range(2):
        response = client.post("/api/v1/tournament", json={"url": _EVENT_URL})
        assert response.status_code == 202, response.get_data(as_text=True)
        assert not AutonaApiV1._heavy._inflight

    assert SettingsManager.Get("TOURNAMENT_URL") == _EVENT_URL


def test_a_tournament_load_in_flight_still_answers_409(client, loadedTournament):
    from src import AutonaApiV1

    assert AutonaApiV1._heavy.try_begin("tournament")
    try:
        _assertRejected(
            client.post("/api/v1/tournament", json={"url": _EVENT_URL}),
            "CONFLICT", 409)
    finally:
        AutonaApiV1._heavy.end("tournament")


def test_match_names_and_sets_return_the_legacy_payload(autonaApp, client):
    # No tournament is loaded in the harness, so /sets is the empty list on
    # both -- what is checked is the envelope the Web UI now unwraps.
    for legacyPath, v1Path in (
        ("/match-names", "/api/v1/match-names"),
        ("/get-sets", "/api/v1/sets"),
        ("/get-sets?getFinished=true", "/api/v1/sets?finished=true"),
    ):
        legacy = client.get(legacyPath)
        v1 = client.get(v1Path)
        assert legacy.status_code == v1.status_code == 200
        assert v1.get_json()["ok"] is True
        assert v1.get_json()["data"] == legacy.get_json()


# ── Flask-thread -> GUI-thread path ──────────────────────────────────────
# The tests above run requests on the GUI thread, where gui_thread_sync and
# gui_executor collapse into direct calls. These issue the request from a
# worker thread (as the real Flask server does) while the test thread spins
# the Qt event loop, so the queued hand-off and the blocking wait really run.

def _waitUntil(predicate, timeout=10.0):
    """Spin the Qt event loop on this (GUI) thread until `predicate()` holds.

    pytest-qt is not installed, so this stands in for qtbot.waitUntil.
    """
    import time
    from qtpy.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out spinning the event loop"
        QApplication.processEvents()
        time.sleep(0.005)


def _requestOnWorkerThread(requests):
    """Run `requests()` on a thread; spin the event loop until it returns."""
    outcome = {"done": False, "responses": None, "error": None}

    def work():
        try:
            outcome["responses"] = requests()
        except BaseException as e:  # surfaced on the test thread below
            outcome["error"] = e
        finally:
            outcome["done"] = True

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    _waitUntil(lambda: outcome["done"])
    thread.join(timeout=10)
    assert not thread.is_alive()
    if outcome["error"] is not None:
        raise outcome["error"]
    return outcome["responses"]


def test_set_patch_with_nested_gui_sync_does_not_deadlock_off_thread(
        autonaApp, client):
    """losers + teamName from a worker thread: set_update nests gui_thread_sync."""
    autonaApp.board(2)
    autonaApp.settle()

    response, = _requestOnWorkerThread(lambda: [client.patch(
        "/api/v1/scoreboards/2/set", json={
            "losers": {"team": 2, "value": True},
            "teamName": {"team": 1, "value": "Alpha"},
        })])

    assert response.status_code == 200, response.get_data(as_text=True)
    autonaApp.settle()
    assert autonaApp.get("score.2.team.1.teamName") == "Alpha"
    assert autonaApp.get("score.2.team.2.losers") is True


def test_tournament_reload_releases_the_flag_asynchronously_off_thread(
        client, loadedTournament):
    from src import AutonaApiV1

    def post():
        return [client.post("/api/v1/tournament", json={"url": _EVENT_URL})]

    statuses = []
    for _ in range(2):
        response, = _requestOnWorkerThread(post)
        statuses.append(response.status_code)
        # The flag is released by the queued GUI task, not before the 202 is
        # returned, so it clears only while this thread spins the event loop.
        _waitUntil(lambda: not AutonaApiV1._heavy._inflight)

    # A 409 on the second POST would mean the first one never released it.
    assert statuses == [202, 202]
    assert SettingsManager.Get("TOURNAMENT_URL") == _EVENT_URL


# ── GET /layouts (layout catalog) ────────────────────────────────────────
# docs/changes/layout-custom-unification.md Step 1. A read of the layout
# catalog the Board / Commentary / Top 8 tabs and the Designer list from
# (src/core/LayoutCatalog.py; its discovery rules are pinned in
# tests/test_layout_catalog.py). Here: the envelope, the ?kind= filter and
# its rejection, against a throwaway layout tree.

@pytest.fixture
def layoutTree(tmp_path, monkeypatch):
    from src.core import LayoutCatalog
    from tests.test_layout_catalog import builtin, project

    root = tmp_path / "layout"
    root.mkdir()
    builtin(root, "scoreboard_simple", {
        "kind": "scoreboard", "displayName": "Simple",
        "format": "1v1 / チーム", "game": "汎用（全ゲーム）"})
    builtin(root, "commentators", {"kind": "commentary", "displayName": "標準"})
    builtin(root, "bracket_top8", {"kind": "top8", "displayName": "標準"})
    project(root, "finals", {"name": "決勝", "layers": []})
    # The catalog resolves its default root on every call, so the route
    # reads this tree whatever the working directory is.
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(root))
    return root


def _layoutIds(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return [entry["id"] for entry in body["data"]]


def test_layouts_lists_the_catalog(webApp, layoutTree):
    from src.core import LayoutCatalog

    response = webApp.test_client().get("/api/v1/layouts")

    assert response.status_code == 200
    body = response.get_json()
    # Not state: no revision rides along (spec §5).
    assert body == {"ok": True, "data": LayoutCatalog.Layouts()}
    assert body["data"][-1] == {
        "id": "custom/finals", "kind": "designer",
        "displayName": "Custom: 決勝",
        "format": "1v1", "game": "カスタム", "entry": "index.html"}


@pytest.mark.parametrize("query, expected", [
    ("?kind=scoreboard", ["scoreboard_simple"]),
    ("?kind=designer", ["custom/finals"]),
    ("?kind=commentary", ["commentators"]),
    ("?kind=top8", ["bracket_top8"]),
    ("?kind=scoreboard,designer", ["scoreboard_simple", "custom/finals"]),
    ("?kind=scoreboard&kind=designer", ["scoreboard_simple", "custom/finals"]),
    ("?kind=%20top8%20,", ["bracket_top8"]),
    ("?kind=", ["bracket_top8", "commentators", "scoreboard_simple",
                "custom/finals"]),
])
def test_layouts_filters_by_kind(webApp, layoutTree, query, expected):
    assert _layoutIds(
        webApp.test_client().get(f"/api/v1/layouts{query}")) == expected


@pytest.mark.parametrize("query", ["?kind=bogus", "?kind=scoreboard,Designer"])
def test_layouts_rejects_an_unknown_kind(webApp, layoutTree, query):
    response = webApp.test_client().get(f"/api/v1/layouts{query}")
    _assertRejected(response)
    message = response.get_json()["error"]["message"]
    assert "scoreboard, designer, commentary, top8" in message


def test_layouts_reads_the_shipped_tree_with_the_panel_labels(
        webApp, monkeypatch):
    # The real layout/ folder through the route: the Board tab's labels, in
    # the Board tab's order. Only the shipped folders are checked -- a
    # developer's local layout/ may hold more -- and a local copy sharing a
    # shipped name makes the catalog append the id (pinned in
    # tests/test_layout_catalog.py), so that form is accepted too.
    from pathlib import Path
    from src.core import LayoutCatalog

    shipped = [("scoreboard_Universal", "Universal"),
               ("scoreboard_crewbattle", "Crew Battle"),
               ("scoreboard_simple", "Simple"),
               ("scoreboard_team", "Team"),
               ("scoreboard_topleft", "Top Left")]
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(
        Path(__file__).resolve().parent.parent / "layout"))
    body = webApp.test_client().get(
        "/api/v1/layouts?kind=scoreboard").get_json()
    names = dict(shipped)
    listed = [(entry["id"], entry["displayName"]) for entry in body["data"]
              if entry["id"] in names]
    assert [layoutId for layoutId, _ in listed] == [
        layoutId for layoutId, _ in shipped]
    for layoutId, displayName in listed:
        assert displayName in (
            names[layoutId], f"{names[layoutId]} ({layoutId})"), layoutId
