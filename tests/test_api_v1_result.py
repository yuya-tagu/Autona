"""The tournament result's /api/v1 routes (ADR-005 S4q).

Added to /api/v1 only (nothing existing changed):

- ``GET    /api/v1/result``                   the rows, the layouts, how the
  render and the import are going, whether this shell can draw
- ``GET    /api/v1/result/post``              the X post's text
- ``GET    /api/v1/result/preview``           the newest render, a PNG
- ``PATCH  /api/v1/result``                   the layout
- ``PATCH  /api/v1/result/players/<n>``       one row of the singles' layouts
- ``PATCH  /api/v1/result/teams/<n>``         one row of the team layouts
- ``POST   /api/v1/result:reorder``           rows in a new order
- ``POST   /api/v1/result:clear``             a kind's rows emptied (confirm)
- ``POST   /api/v1/result:loadStandings``     202; the result arrives in the state
- ``POST   /api/v1/result:saveToDb``          the rows into the player database
- ``POST   /api/v1/result:preview``           202; the result arrives in the state
- ``POST   /api/v1/result:generate``          202, 400 with nothing to draw, or
  409 while one is on its way

Through Flask's test client against the real route table, on a ResultService of
the test's own in a directory of its own (the `env` fixture of
tests/test_result_service.py, where the operations themselves are pinned): the
contract (envelopes, 400 / 404 / 409 / 413 / 503, unknown keys refused, nothing
written on a refusal), and the access rules.
"""
import copy

import pytest

from src import AutonaApiV1
from src import AutonaWebOperations as WebOps
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.AutonaWebOperations import NotAvailable, SHUTTING_DOWN_MESSAGE
from src.core import ResultService as results
from src.core import StateSchema as schema
from src.core import Top8Service
from src.core import WebAccess
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_result_service import (  # noqa: F401
    WAIT, _standing, env, executor)


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


def _rows():
    return copy.deepcopy((SM.Get(schema.RESULT_PLAYERS_ROOT),
                          SM.Get(schema.RESULT_TEAMS_ROOT)))


@pytest.fixture
def desktop(env, monkeypatch):
    """A shell with a renderer: what the service's `render` is given is the
    test's stand-in, not this registration, which only says it has one."""
    monkeypatch.setattr(WebOps, "startImageRender", lambda *_: None)
    return env


@pytest.fixture
def headless(env, monkeypatch):
    monkeypatch.setattr(WebOps, "startImageRender", None)
    return env


@pytest.fixture
def tournament(env, monkeypatch):
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        env.fetcher.provider)
    return env


# ── GET /result ──────────────────────────────────────────────────────────

def test_the_page_reads_everything_it_shows(desktop, client):
    SM.state = {"tournamentInfo": {"tournamentName": "Cup"},
                "game": {"codename": "tgame", "name": "Test Game"}}
    results.UpdatePlayer(1, {"name": "Alpha", "twitter": "@a",
                             "characters": [("Mario", 2, "tgame")]})
    results.UpdateTeam(2, {"name": "Reds", "members": [
        {"name": "A1", "twitter": "@a1", "character": ("Link", 1, None)}]})

    body = _ok(client.get("/api/v1/result"))
    data = body["data"]

    assert body["revision"] == SM.revision
    assert set(data) == {"layout", "layouts", "players", "teams", "sample",
                         "game", "render", "standings", "canRender"}
    assert data["layout"] == "horizontal"
    assert data["layouts"][0] == {"id": "horizontal",
                                  "name": "Horizontal (1920x1080)",
                                  "width": 1920, "height": 1080, "slots": 8,
                                  "team": False}
    assert {layout["id"]: (layout["width"], layout["slots"], layout["team"])
            for layout in data["layouts"]} == {
        "horizontal": (1920, 8, False), "horizontal_16": (1920, 16, False),
        "vertical": (960, 8, False), "vertical_geki": (960, 8, False),
        "team": (1920, 8, True), "team_split": (1920, 8, True)}
    assert len(data["players"]) == 16
    assert len(data["teams"]) == 8
    assert data["players"][0] == {
        "slot": 1, "rank": 1, "name": "Alpha", "sponsor": "", "twitter": "@a",
        "characters": [{"en_name": "Mario", "skin": 2}, None, None],
        "fromScoreboard": None}
    assert data["teams"][1] == {
        "slot": 2, "rank": 2, "name": "Reds", "members": [{
            "name": "A1", "twitter": "@a1",
            "character": {"en_name": "Link", "skin": 1},
            "fromScoreboard": None}]}
    assert data["sample"] is False
    assert data["game"] == {"codename": "tgame", "name": "Test Game"}
    assert data["render"] == schema.DefaultResultRender()
    assert data["standings"] == schema.DefaultResultStandings()
    assert data["canRender"] is True


def test_the_post_has_its_own_route(desktop, client, env):
    SM.state = {"tournamentInfo": {"tournamentName": "Cup"}}
    results.UpdatePlayer(1, {"name": "Alpha"})
    env.database.records["Alpha"] = {"twitter": "@from_the_db"}

    body = _ok(client.get("/api/v1/result/post"))

    assert body["revision"] == SM.revision
    assert body["data"] == {"text": "CUP\n\n1/ Alpha @from_the_db"}


def test_the_page_says_no_handle_of_the_database(desktop, client, env):
    """GET /result is READ: a handle from the player database, whose route is
    guarded, was readable by anyone on the network."""
    results.UpdatePlayer(1, {"name": "Alpha"})
    results.UpdateTeam(1, {"name": "Reds", "members": [{"name": "A1"}]})
    env.database.records["Alpha"] = {"twitter": "@from_the_db"}
    env.database.records["A1"] = {"twitter": "@from_the_db"}

    assert "from_the_db" not in client.get("/api/v1/result").get_data(
        as_text=True)


def test_the_post_is_guarded_like_the_player_db():
    assert WebAccess.RouteAccess("api_v1.get_result_post") == WebAccess.PLAYERDB
    assert WebAccess.RouteAccess("api_v1.get_result") == WebAccess.READ


def test_a_path_typed_in_a_row_stays_a_name(desktop, client, env):
    """The probe: ./out is served, so that this put the settings there, with
    the web server's token."""
    (env.dir / "user_data" / "settings.json").write_text("{}", encoding="utf-8")

    _ok(client.patch("/api/v1/result/players/1",
                     json={"name": "./user_data/settings.json"}))
    SM.SaveState()
    if SM._export_thread is not None:
        SM._export_thread.join()

    out = env.dir / "out" / "result_players" / "1"
    assert (out / "name.txt").read_text(encoding="utf-8") \
        == "./user_data/settings.json"
    assert not (out / "name.json").exists()


def test_no_game_reads_as_null(desktop, client):
    assert _ok(client.get("/api/v1/result"))["data"]["game"] is None


def test_reading_writes_nothing(desktop, client, monkeypatch):
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))
    before = SM.revision

    _ok(client.get("/api/v1/result"))

    assert writes == []
    assert SM.revision == before


def test_the_headless_shell_cannot_draw_but_answers(headless, client):
    assert _ok(client.get("/api/v1/result"))["data"]["canRender"] is False


# ── PATCH /result ────────────────────────────────────────────────────────

def test_the_layout_comes_back_as_saved(desktop, client):
    body = _ok(client.patch("/api/v1/result", json={"layout": "vertical"}))

    assert body["data"] == {"layout": "vertical"}
    assert SettingsManager.settings[results.CONFIG_SETTING]["layout"] \
        == "vertical"


@pytest.mark.parametrize("body", [
    {},
    ["layout"],
    {"layout": "nope"},
    {"layout": 1},
    {"layout": None},
    {"unknown": 1},
    {"layout": "team", "unknown": 1},
    # A path would have the render read any local file.
    {"background_path": "C:/x.png"},
    {"main_icon_path": "C:/x.png"},
])
def test_a_bad_layout_is_refused(desktop, client, body):
    settings = copy.deepcopy(SettingsManager.settings)

    response = client.patch("/api/v1/result", json=body)

    _assertRejected(response, "INVALID_ARGUMENT", 400)
    assert SettingsManager.settings == settings


# ── PATCH /result/players/<n> ────────────────────────────────────────────

def test_a_player_is_written_with_the_values_core_builds(desktop, client):
    body = _ok(client.patch("/api/v1/result/players/3", json={
        "rank": 2, "name": " Alpha ", "sponsor": "SPN", "twitter": "@a",
        "characters": [{"en_name": "Mario", "skin": 2, "game": "tgame"}, None,
                       {"en_name": "Link"}]}))

    assert body["revision"] == SM.revision
    row = SM.Get("result_players.3")
    assert (row["rank"], row["name"], row["sponsor"], row["twitter"]) \
        == (2, "Alpha", "SPN", "@a")
    assert row["character"] == Top8Service.CharacterValue(
        "Mario", 2, desktop.catalog)
    # The empty field keeps its place.
    assert [value.get("en_name") for value in row["characters"]] \
        == ["Mario", None, "Link"]
    described = _ok(client.get("/api/v1/result"))["data"]["players"][2]
    assert described["characters"] == [{"en_name": "Mario", "skin": 2}, None,
                                       {"en_name": "Link", "skin": 0}]


def test_the_last_slot_can_be_written(desktop, client):
    _ok(client.patch("/api/v1/result/players/16", json={"name": "Alpha"}))

    assert SM.Get("result_players.16.name") == "Alpha"


@pytest.mark.parametrize("slot, body", [
    (0, {"name": "x"}),
    (17, {"name": "x"}),
    (1, {}),
    (1, ["name"]),
    (1, {"unknown": 1}),
    (1, {"name": "x", "unknown": 1}),
    (1, {"character": {"en_name": "Mario"}}),
    (1, {"name": 1}),
    (1, {"sponsor": None}),
    (1, {"twitter": ["@a"]}),
    (1, {"rank": "1"}),
    (1, {"rank": 0}),
    (1, {"rank": 1000}),
    (1, {"rank": True}),
    (1, {"rank": 1.5}),
    (1, {"characters": {"en_name": "Mario"}}),
    (1, {"characters": [None, None, None, None]}),
    (1, {"characters": ["Mario"]}),
    (1, {"characters": [{"skin": 0}]}),
    (1, {"characters": [{"en_name": ""}]}),
    (1, {"characters": [{"en_name": 1}]}),
    (1, {"characters": [{"en_name": "Mario", "skin": -1}]}),
    (1, {"characters": [{"en_name": "Mario", "skin": "0"}]}),
    (1, {"characters": [{"en_name": "Mario", "game": 1}]}),
    (1, {"characters": [{"en_name": "Mario", "extra": 1}]}),
    # The catalog's.
    (1, {"characters": [{"en_name": "Nobody"}]}),
    (1, {"characters": [{"en_name": "Mario", "skin": 99}]}),
])
def test_a_bad_player_is_refused_and_nothing_is_written(
        desktop, client, slot, body):
    results.UpdatePlayer(1, {"name": "Kept"})
    before = _rows()

    response = client.patch(f"/api/v1/result/players/{slot}", json=body)

    _assertRejected(response, "INVALID_ARGUMENT", 400)
    assert _rows() == before


def test_a_character_of_another_game_is_a_conflict(desktop, client):
    results.UpdatePlayer(1, {"name": "Kept"})
    before = _rows()

    response = client.patch("/api/v1/result/players/1", json={
        "name": "Changed",
        "characters": [{"en_name": "Mario", "game": "other"}]})

    _assertRejected(response, "CONFLICT", 409)
    assert _rows() == before


def test_a_players_body_is_limited(desktop, client):
    response = client.patch(
        "/api/v1/result/players/1",
        data=b"x" * (AutonaApiV1._RESULT_MAX_BODY + 1),
        content_type="application/json")

    _assertRejected(response, "PAYLOAD_TOO_LARGE", 413)
    assert SM.Get("result_players") is None


# ── PATCH /result/teams/<n> ──────────────────────────────────────────────

def test_a_team_is_written(desktop, client):
    body = _ok(client.patch("/api/v1/result/teams/8", json={
        "rank": 3, "name": "Reds", "members": [
            {"name": "A1", "twitter": "@a1",
             "character": {"en_name": "Link", "skin": 1, "game": "tgame"}},
            {"name": "B1"},
            {},
        ]}))

    assert body["revision"] == SM.revision
    row = SM.Get("result_teams.8")
    assert (row["rank"], row["name"]) == (3, "Reds")
    assert row["members"][0]["character"] == Top8Service.CharacterValue(
        "Link", 1, desktop.catalog)
    assert row["members"][1:] == [
        {"name": "B1", "twitter": "", "character": {}},
        {"name": "", "twitter": "", "character": {}}]


@pytest.mark.parametrize("slot, body", [
    (0, {"name": "x"}),
    (9, {"name": "x"}),
    (1, {}),
    (1, {"unknown": 1}),
    (1, {"sponsor": "x"}),
    (1, {"name": 1}),
    (1, {"rank": 0}),
    (1, {"members": {}}),
    (1, {"members": [{}] * 6}),
    (1, {"members": ["A1"]}),
    (1, {"members": [{"unknown": 1}]}),
    (1, {"members": [{"name": 1}]}),
    (1, {"members": [{"twitter": 1}]}),
    (1, {"members": [{"character": "Mario"}]}),
    (1, {"members": [{"character": {"en_name": "Nobody"}}]}),
    (1, {"members": [{"character": {"en_name": "Mario", "skin": 99}}]}),
])
def test_a_bad_team_is_refused_and_nothing_is_written(
        desktop, client, slot, body):
    results.UpdateTeam(1, {"name": "Kept"})
    before = _rows()

    response = client.patch(f"/api/v1/result/teams/{slot}", json=body)

    _assertRejected(response, "INVALID_ARGUMENT", 400)
    assert _rows() == before


def test_a_members_character_of_another_game_is_a_conflict(desktop, client):
    results.UpdateTeam(1, {"name": "Kept"})
    before = _rows()

    response = client.patch("/api/v1/result/teams/1", json={"members": [
        {"character": {"en_name": "Mario", "game": "other"}}]})

    _assertRejected(response, "CONFLICT", 409)
    assert _rows() == before


def test_a_teams_body_is_limited(desktop, client):
    response = client.patch(
        "/api/v1/result/teams/1",
        data=b"x" * (AutonaApiV1._RESULT_MAX_BODY + 1),
        content_type="application/json")

    _assertRejected(response, "PAYLOAD_TOO_LARGE", 413)


# ── POST /result:reorder | :clear ────────────────────────────────────────

def test_reorder(desktop, client):
    results.UpdatePlayer(1, {"name": "A"})
    results.UpdatePlayer(2, {"name": "B"})

    body = _ok(client.post("/api/v1/result:reorder",
                           json={"kind": "players", "order": [2, 1]}))

    assert body["revision"] == SM.revision
    assert SM.Get("result_players.1.name") == "B"
    assert SM.Get("result_players.2.rank") == 2


@pytest.mark.parametrize("body", [
    {},
    {"kind": "players"},
    {"order": [1]},
    {"kind": "nobody", "order": [1]},
    {"kind": "players", "order": "12"},
    {"kind": "players", "order": [1, 1]},
    {"kind": "players", "order": [2]},
    {"kind": "players", "order": ["1"]},
    {"kind": "players", "order": [True]},
    {"kind": "players", "order": []},
    {"kind": "players", "order": list(range(1, 18))},
    {"kind": "teams", "order": list(range(1, 10))},
    {"kind": "players", "order": [1], "unknown": 1},
])
def test_a_bad_reorder_is_refused(desktop, client, body):
    results.UpdatePlayer(1, {"name": "A"})
    before = _rows()

    response = client.post("/api/v1/result:reorder", json=body)

    _assertRejected(response, "INVALID_ARGUMENT", 400)
    assert _rows() == before


def test_clear_needs_a_confirmation(desktop, client):
    results.UpdatePlayer(1, {"name": "A"})
    before = _rows()

    _assertRejected(client.post("/api/v1/result:clear",
                                json={"kind": "players"}),
                    "CONFIRMATION_REQUIRED", 409)

    assert _rows() == before


def test_clear_empties_that_kind_only(desktop, client):
    results.UpdatePlayer(1, {"name": "A"})
    results.UpdateTeam(1, {"name": "Reds"})

    _ok(client.post("/api/v1/result:clear",
                    json={"kind": "players", "confirm": True}))

    assert SM.Get("result_players.1.name") == ""
    assert SM.Get("result_teams.1.name") == "Reds"


@pytest.mark.parametrize("body", [
    {"confirm": True},
    {"kind": "nobody", "confirm": True},
    {"kind": "players", "confirm": True, "unknown": 1},
])
def test_a_bad_clear_is_refused(desktop, client, body):
    results.UpdatePlayer(1, {"name": "A"})
    before = _rows()

    response = client.post("/api/v1/result:clear", json=body)

    _assertRejected(response, "INVALID_ARGUMENT", 400)
    assert _rows() == before


# ── POST /result:loadStandings ───────────────────────────────────────────

def test_the_import_is_accepted_and_ends_in_the_state(tournament, client):
    tournament.fetcher.provider.answer = [_standing(1, "Alpha")]

    body = _ok(client.post("/api/v1/result:loadStandings", json={}),
               status=202)

    assert body["data"] == {"accepted": True,
                            "operation": "result.loadStandings",
                            "standingsRevision": 1}
    assert body["revision"] == SM.revision
    assert SM.Get("result_standings.status") == "running"

    tournament.fetcher.run()

    assert SM.Get("result_standings") == {"status": "done", "error": "",
                                          "revision": 1}
    assert SM.Get("result_players.1.name") == "Alpha"


def test_the_import_needs_a_tournament(env, client, monkeypatch):
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider", None)

    _assertRejected(client.post("/api/v1/result:loadStandings", json={}),
                    "NOT_READY", 409)

    assert env.fetcher.jobs == []


def test_a_tournament_cleared_at_the_last_moment_is_not_ready(
        tournament, client):
    tournament.fetcher.provider = None

    _assertRejected(client.post("/api/v1/result:loadStandings", json={}),
                    "NOT_READY", 409)


def test_the_import_over_rows_needs_a_confirmation(tournament, client):
    results.UpdatePlayer(1, {"name": "Kept"})

    _assertRejected(client.post("/api/v1/result:loadStandings", json={}),
                    "CONFIRMATION_REQUIRED", 409)
    assert tournament.fetcher.jobs == []

    _ok(client.post("/api/v1/result:loadStandings", json={"confirm": True}),
        status=202)
    assert len(tournament.fetcher.jobs) == 1


def test_a_second_import_is_a_conflict(tournament, client):
    _ok(client.post("/api/v1/result:loadStandings", json={}), status=202)

    _assertRejected(client.post("/api/v1/result:loadStandings", json={}),
                    "CONFLICT", 409)

    assert len(tournament.fetcher.jobs) == 1


def test_the_import_refuses_unknown_keys(tournament, client):
    _assertRejected(client.post("/api/v1/result:loadStandings",
                                json={"unknown": 1}),
                    "INVALID_ARGUMENT", 400)

    assert tournament.fetcher.jobs == []


def test_the_import_works_headless(headless, tournament, client):
    # Only the drawing is the desktop's.
    tournament.fetcher.provider.answer = [_standing(1, "Alpha")]

    _ok(client.post("/api/v1/result:loadStandings", json={}), status=202)
    tournament.fetcher.run()

    assert SM.Get("result_players.1.name") == "Alpha"


# ── POST /result:saveToDb ────────────────────────────────────────────────

def test_save_to_db_says_how_many(desktop, client):
    results.UpdatePlayer(1, {"name": "Alpha"})
    results.UpdatePlayer(2, {"sponsor": "no name"})

    body = _ok(client.post("/api/v1/result:saveToDb", json={}))

    assert body["data"] == {"saved": 1}
    assert desktop.database.given[0]["overwrite"] is False


# ── POST /result:preview | :generate ─────────────────────────────────────

def test_a_preview_is_accepted_and_ends_in_the_state(desktop, executor, client):
    body = _ok(client.post("/api/v1/result:preview", json={}), status=202)

    assert body["data"] == {"accepted": True, "operation": "result.preview",
                            "renderRevision": 1}
    assert body["revision"] == SM.revision
    assert SM.Get("result_render.status") == "running"
    assert desktop.renderer.asked[0].kind == "result"
    assert desktop.database.given == []


def test_previews_are_accepted_during_a_run(desktop, executor, client):
    client.post("/api/v1/result:preview", json={})

    body = _ok(client.post("/api/v1/result:preview", json={}), status=202)

    assert body["data"]["renderRevision"] == 2
    assert len(desktop.renderer.asked) == 1


def test_a_generate_is_accepted_and_a_second_refused(desktop, executor, client):
    results.UpdatePlayer(1, {"name": "Alpha"})

    body = _ok(client.post("/api/v1/result:generate", json={}), status=202)
    assert body["data"] == {"accepted": True, "operation": "result.generate",
                            "renderRevision": 1}

    _assertRejected(client.post("/api/v1/result:generate", json={}),
                    "CONFLICT", 409)
    assert SM.Get("result_render.revision") == 1
    assert len(desktop.database.given) == 1


def test_a_generate_without_data_is_a_400(desktop, executor, client):
    _assertRejected(client.post("/api/v1/result:generate", json={}),
                    "INVALID_ARGUMENT", 400)

    assert SM.Get("result_render") is None
    assert desktop.renderer.asked == []


@pytest.mark.parametrize("route", ["preview", "generate"])
def test_the_renders_are_the_desktops(headless, executor, client, route):
    results.UpdatePlayer(1, {"name": "Alpha"})

    response = client.post(f"/api/v1/result:{route}", json={})

    _assertRejected(response, "UNAVAILABLE", 503)
    assert response.get_json()["error"]["message"] \
        == "not available in headless"
    assert SM.Get("result_render") is None
    assert headless.renderer.asked == []
    assert headless.database.given == []


@pytest.mark.parametrize("route", ["preview", "generate"])
def test_a_render_asked_for_at_shutdown_is_unavailable(
        desktop, executor, client, route):
    def refuse(_kind, _payload, _done):
        raise NotAvailable(SHUTTING_DOWN_MESSAGE)
    desktop.service._render = refuse
    desktop.service._shuttingDown = lambda: True
    results.UpdatePlayer(1, {"name": "Alpha"})

    response = client.post(f"/api/v1/result:{route}", json={})

    _assertRejected(response, "UNAVAILABLE", 503)
    assert response.get_json()["error"]["message"] == SHUTTING_DOWN_MESSAGE
    assert SM.Get("result_render.status") == "error"
    assert SM.Get("result_render.error") == results.CANCELLED


def test_the_session_service_is_wired(autonaApp, monkeypatch):
    # What bootstrap_core() injects.
    from src.Helpers import AutonaQtHelper
    from src.core.ResultService import ResultService

    service = ResultService.instance
    assert service._render == WebOps.start_image_render
    assert service._shuttingDown() is False
    monkeypatch.setattr(AutonaQtHelper, "gui_unavailable_reason",
                        lambda: "the event loop has exited")
    assert service._shuttingDown() is True
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        "the provider")
    assert service._currentProvider() == "the provider"
    assert service._fetcher == AutonaTournamentDataProvider.instance\
        .RunWithProvider


# ── GET /result/preview ──────────────────────────────────────────────────

def test_no_preview_is_a_404(desktop, client):
    _assertRejected(client.get("/api/v1/result/preview"), "NOT_FOUND", 404)


def test_the_preview_is_the_newest_render_as_a_png(desktop, executor, client):
    client.post("/api/v1/result:preview", json={})
    desktop.renderer.answer(b"first")
    executor.drain(WAIT)

    response = client.get("/api/v1/result/preview?v=1")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_data() == b"first.PNG"


def test_tmp_is_not_served_as_a_file(desktop, executor, client):
    client.post("/api/v1/result:preview", json={})
    desktop.renderer.answer()
    executor.drain(WAIT)
    assert desktop.service.PreviewImage() is not None

    assert WebAccess.StaticPath("tmp/result/result_preview.png") is None
    assert client.get("/tmp/result/result_preview.png").status_code == 404


# ── who may ──────────────────────────────────────────────────────────────

def test_the_writes_are_guarded_and_the_reads_are_not():
    access = WebAccess.ROUTE_ACCESS
    for name in ("result_update", "result_player_update", "result_team_update",
                 "result_reorder", "result_clear", "result_load_standings",
                 "result_save_to_db", "result_preview", "result_generate"):
        assert access[f"api_v1.{name}"] == WebAccess.WRITE, name
    for name in ("get_result", "get_result_preview"):
        assert access[f"api_v1.{name}"] == WebAccess.READ, name


@pytest.mark.parametrize("method, url, body", [
    ("PATCH", "/api/v1/result", {"layout": "team"}),
    ("GET", "/api/v1/result/post", None),
    ("PATCH", "/api/v1/result/players/1", {"name": "x"}),
    ("PATCH", "/api/v1/result/teams/1", {"name": "x"}),
    ("POST", "/api/v1/result:reorder", {"kind": "players", "order": [1]}),
    ("POST", "/api/v1/result:clear", {"kind": "players", "confirm": True}),
    ("POST", "/api/v1/result:loadStandings", {}),
    ("POST", "/api/v1/result:saveToDb", {}),
    ("POST", "/api/v1/result:preview", {}),
    ("POST", "/api/v1/result:generate", {}),
])
def test_another_computer_needs_the_token(desktop, executor, webApp, method,
                                          url, body):
    other = webApp.test_client()
    other.environ_base["REMOTE_ADDR"] = "192.0.2.10"
    settings = copy.deepcopy(SettingsManager.settings)
    before = _rows()

    response = other.open(url, method=method, json=body)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert SettingsManager.settings == settings
    assert _rows() == before
    assert desktop.renderer.asked == []


def test_a_players_text_is_not_written_to_the_log(desktop, client):
    from loguru import logger

    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="INFO")
    try:
        _ok(client.patch("/api/v1/result/players/1",
                         json={"name": "SECRET_NAME"}))
        _ok(client.patch("/api/v1/result/teams/1",
                         json={"name": "SECRET_NAME"}))
    finally:
        logger.remove(sink)

    assert not [line for line in lines if "SECRET_NAME" in line]
