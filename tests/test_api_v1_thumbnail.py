"""The match thumbnail's /api/v1 routes (ADR-005 S4o).

Added to /api/v1 only (nothing existing changed):

- ``GET    /api/v1/thumbnail``             the settings, the game they are
  for, the templates, the video title, whether this shell can draw
- ``PATCH  /api/v1/thumbnail``             some of the settings
- ``POST   /api/v1/thumbnail:reset``       back to the defaults (confirm)
- ``POST   /api/v1/thumbnail/background``  a background, a base64 data URL
- ``POST   /api/v1/thumbnail:preview``     202; the result arrives in the state
- ``POST   /api/v1/thumbnail:generate``    202, or 409 while one is on its way
- ``GET    /api/v1/thumbnail/preview``     the newest render, a PNG

Through Flask's test client against the real route table, on a
ThumbnailService of the test's own in a directory of its own (the `env`
fixture of tests/test_thumbnail_service.py, where the operations themselves
are pinned): the contract (envelopes, 400 / 404 / 409 / 413 / 503, nothing
written on a refusal), and the access rules. And the legacy thumbnail route's
answers, which S4o leaves as they were.
"""
import base64
import copy
from io import BytesIO

import pytest
from PIL import Image

from src import AutonaWebOperations as WebOps
from src.AutonaWebOperations import NotAvailable, SHUTTING_DOWN_MESSAGE
from src.core import WebAccess
from src.core import ThumbnailService as thumbnails
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_thumbnail_service import (  # noqa: F401
    PLAYERS, WAIT, env, executor)


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


def _data_url(size=(64, 32), fmt="PNG", mime="image/png"):
    buffer = BytesIO()
    Image.new("RGB", size, (0, 200, 0)).save(buffer, fmt)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


@pytest.fixture
def desktop(env, monkeypatch):
    """A shell with a renderer: what ThumbnailService's `render` is given is
    the test's stand-in, not this registration, which only says it has one."""
    monkeypatch.setattr(WebOps, "startImageRender", lambda *_: None)
    return env


@pytest.fixture
def headless(env, monkeypatch):
    monkeypatch.setattr(WebOps, "startImageRender", None)
    return env


# ── GET /thumbnail ───────────────────────────────────────────────────────

def test_the_page_reads_everything_it_shows(desktop, client):
    SM.state = copy.deepcopy(PLAYERS)
    SM.state["game"]["abbreviation"] = "TG"
    SM.state["score"]["1"]["match_en"] = "Grand Final"

    data = _ok(client.get("/api/v1/thumbnail"))["data"]

    assert data == {
        "settings": desktop.service.Settings(),
        "game": {"codename": "tgame", "name": "Test Game"},
        "templates": [{"id": "type_a", "name": "Standard"},
                      {"id": "type_b", "name": "Center Panel"}],
        "videoTitle": "Big Cup Grand Final | SPN | Alpha(Mario) vs Bravo | TG",
        "canRender": True,
    }
    assert data["settings"]["game"]["tgame"]["zoom"] == 100


def test_no_game_reads_as_null(desktop, client):
    data = _ok(client.get("/api/v1/thumbnail"))["data"]

    assert data["game"] is None
    assert data["settings"]["game"] == {}
    assert data["videoTitle"] == ""


def test_reading_writes_nothing(desktop, client, monkeypatch):
    writes = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: writes.append(1))
    SettingsManager.settings = {"thumbnail_config": {
        "background_path": "C:/somewhere/mine.png"}}

    data = _ok(client.get("/api/v1/thumbnail"))["data"]

    assert data["settings"]["background_source"] == "custom"
    assert data["settings"]["background_name"] == "mine.png"
    assert "C:/somewhere" not in str(data)
    assert writes == []


def test_the_headless_shell_cannot_draw_but_answers(headless, client):
    assert _ok(client.get("/api/v1/thumbnail"))["data"]["canRender"] is False


# ── PATCH /thumbnail ─────────────────────────────────────────────────────

def test_an_update_comes_back_as_saved(desktop, client):
    body = _ok(client.patch("/api/v1/thumbnail", json={
        "template_id": "type_b", "use_sponsors": False,
        "game": {"ssbu": {"zoom": 150, "align": {"vertical": 10}}}}))

    assert "revision" not in body or body["revision"] is None
    assert body["data"] == desktop.service.Settings()
    assert body["data"]["template_id"] == "type_b"
    config = SettingsManager.Get("thumbnail_config")
    assert config["game"]["ssbu"] == {"zoom": 150, "align": {"vertical": 10}}


def test_settings_are_not_state(desktop, client):
    before = SM.revision

    _ok(client.patch("/api/v1/thumbnail", json={"display_phase": False}))

    assert SM.revision == before


@pytest.mark.parametrize("body", [
    {},
    ["display_phase"],
    {"unknown": 1},
    {"template_id": "nope"},
    {"background_source": "nope"},
    {"background_path": "C:/x.png"},
    {"main_icon_path": "C:/x.png"},
    {"display_phase": 1},
    {"game": {"ssbu": {"zoom": 0}}},
    {"game": {"ssbu": {"zoom": 1001}}},
    {"game": {"ssbu": {"align": {"horizontal": 101}}}},
    # The template draws it as the default (and GET says so).
    {"game": {"ssbu": {"align": {"horizontal": 0}}}},
    {"game": {"ssbu": {"align": {"vertical": 0}}}},
    {"game": {"../evil": {"zoom": 100}}},
    {"game": {"a.b": {"zoom": 100}}},
    {"game": {"not_a_game": {"zoom": 100}}},
    {"display_phase": False, "zoom": 100},
])
def test_a_bad_update_is_rejected_without_writing(desktop, client, body):
    SettingsManager.settings = {"thumbnail_config": {"display_phase": True}}

    _assertRejected(client.patch("/api/v1/thumbnail", json=body))

    assert SettingsManager.settings == {
        "thumbnail_config": {"display_phase": True}}


def test_an_oversized_update_is_refused_unread(desktop, client):
    from src import AutonaApiV1

    response = client.patch(
        "/api/v1/thumbnail",
        data=b" " * (AutonaApiV1._THUMBNAIL_SETTINGS_MAX_BODY + 1),
        content_type="application/json")

    _assertRejected(response, "PAYLOAD_TOO_LARGE", 413)


def test_the_message_of_a_bad_update_says_what_is_taken(desktop, client):
    response = client.patch("/api/v1/thumbnail",
                            json={"game": {"not_a_game": {"zoom": 100}}})

    message = response.get_json()["error"]["message"]
    assert "of a game in the list" in message
    assert "zoom (1-1000)" in message
    assert "(1-100)" in message


# ── POST /thumbnail:reset ────────────────────────────────────────────────

def test_a_reset_needs_a_confirmation(desktop, client):
    kept = {"thumbnail_config": {"template_id": "type_b"}}
    SettingsManager.settings = copy.deepcopy(kept)

    _assertRejected(client.post("/api/v1/thumbnail:reset", json={}),
                    "CONFIRMATION_REQUIRED", 409)

    assert SettingsManager.settings == kept


def test_a_reset_comes_back_as_the_defaults(desktop, client):
    SettingsManager.settings = {"thumbnail_config": {"template_id": "type_b"}}

    data = _ok(client.post("/api/v1/thumbnail:reset",
                           json={"confirm": True}))["data"]

    assert data["template_id"] == "type_a"
    assert "thumbnail_config" not in SettingsManager.settings


# ── POST /thumbnail/background ───────────────────────────────────────────

def test_a_background_is_taken_in_and_selected(desktop, client):
    data = _ok(client.post("/api/v1/thumbnail/background",
                           json={"image": _data_url()}))["data"]

    assert data["background_source"] == "custom"
    assert data["background_name"].startswith("thumbnail_background_")
    config = SettingsManager.Get("thumbnail_config")
    assert config["background_path"].startswith("./user_data/")
    assert (desktop.dir / config["background_path"]).exists()


@pytest.mark.parametrize("body", [
    {},
    {"image": 5},
    {"image": "not a data url"},
    {"image": "data:image/png;base64,"},
    {"image": "data:image/png;base64,!!!"},
    {"image": "data:image/png;base64,bm90IGFuIGltYWdl"},
])
def test_a_bad_background_is_rejected_without_writing(desktop, client, body):
    _assertRejected(client.post("/api/v1/thumbnail/background", json=body))

    assert SettingsManager.Get("thumbnail_config") is None
    assert list((desktop.dir / "user_data").glob("thumbnail_background_*")) \
        == []


def test_a_background_that_cannot_be_saved_is_an_internal_error(
        desktop, client, monkeypatch):
    monkeypatch.setattr(WebOps, "import_thumbnail_background",
                        lambda _data: thumbnails.SAVE_FAILED)

    _assertRejected(client.post("/api/v1/thumbnail/background",
                                json={"image": _data_url()}),
                    "INTERNAL", 500)


def test_an_oversized_upload_is_refused_unread(desktop, client):
    from src import AutonaApiV1

    response = client.post(
        "/api/v1/thumbnail/background",
        data=b"x" * (AutonaApiV1._ORG_IMAGE_MAX_BODY + 1),
        content_type="application/json")

    _assertRejected(response, "PAYLOAD_TOO_LARGE", 413)


# ── POST /thumbnail:preview | :generate ──────────────────────────────────

def test_a_preview_is_accepted_and_ends_in_the_state(desktop, executor, client):
    SM.state = copy.deepcopy(PLAYERS)

    body = _ok(client.post("/api/v1/thumbnail:preview", json={}), status=202)

    assert body["data"] == {"accepted": True,
                            "operation": "thumbnail.preview",
                            "renderRevision": 1}
    assert body["revision"] == SM.revision
    assert SM.Get("thumbnail.render.status") == "running"
    assert SM.Get("thumbnail.render.revision") == 1


def test_previews_are_accepted_during_a_run(desktop, executor, client):
    SM.state = copy.deepcopy(PLAYERS)
    client.post("/api/v1/thumbnail:preview", json={})

    body = _ok(client.post("/api/v1/thumbnail:preview", json={}), status=202)

    assert body["data"]["renderRevision"] == 2
    assert len(desktop.renderer.asked) == 1


def test_a_generate_is_accepted_and_a_second_refused(desktop, executor, client):
    SM.state = copy.deepcopy(PLAYERS)

    body = _ok(client.post("/api/v1/thumbnail:generate", json={}), status=202)
    assert body["data"] == {"accepted": True,
                            "operation": "thumbnail.generate",
                            "renderRevision": 1}

    _assertRejected(client.post("/api/v1/thumbnail:generate", json={}),
                    "CONFLICT", 409)
    assert SM.Get("thumbnail.render.revision") == 1


@pytest.mark.parametrize("route", ["preview", "generate"])
def test_the_renders_are_the_desktops(headless, executor, client, route):
    response = client.post(f"/api/v1/thumbnail:{route}", json={})

    _assertRejected(response, "UNAVAILABLE", 503)
    assert response.get_json()["error"]["message"] \
        == "not available in headless"
    assert SM.Get("thumbnail.render") is None
    assert headless.renderer.asked == []


@pytest.mark.parametrize("route", ["preview", "generate"])
def test_a_render_asked_for_at_shutdown_is_unavailable(
        desktop, executor, client, route):
    desktop.renderer.refuse = NotAvailable(SHUTTING_DOWN_MESSAGE)
    desktop.service._shuttingDown = lambda: True
    SM.state = copy.deepcopy(PLAYERS)

    response = client.post(f"/api/v1/thumbnail:{route}", json={})

    _assertRejected(response, "UNAVAILABLE", 503)
    assert response.get_json()["error"]["message"] == SHUTTING_DOWN_MESSAGE
    assert SM.Get("thumbnail.render.status") == "error"
    assert SM.Get("thumbnail.render.error") == thumbnails.CANCELLED


def test_the_session_service_knows_a_shutdown_from_the_gui_thread(
        autonaApp, monkeypatch):
    # What bootstrap_core() injects is what run_sync gives up on.
    from src.core.ThumbnailService import ThumbnailService
    from src.Helpers import AutonaQtHelper

    service = ThumbnailService.instance
    assert service._shuttingDown() is False
    monkeypatch.setattr(AutonaQtHelper, "gui_unavailable_reason",
                        lambda: "the event loop has exited")
    assert service._shuttingDown() is True


# ── GET /thumbnail/preview ───────────────────────────────────────────────

def test_no_preview_is_a_404(desktop, client):
    _assertRejected(client.get("/api/v1/thumbnail/preview"), "NOT_FOUND", 404)


def test_the_preview_is_the_newest_render_as_a_png(desktop, executor, client):
    SM.state = copy.deepcopy(PLAYERS)
    client.post("/api/v1/thumbnail:preview", json={})
    desktop.renderer.answer(b"first")
    executor.drain(WAIT)

    response = client.get("/api/v1/thumbnail/preview?v=1")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_data() == b"first.PNG"

    client.post("/api/v1/thumbnail:generate", json={})
    desktop.renderer.answer(b"second")
    executor.drain(WAIT)

    assert client.get("/api/v1/thumbnail/preview?v=2").get_data() \
        == b"second.PNG"


def test_tmp_is_not_served_as_a_file(desktop, executor, client):
    # GET /thumbnail/preview is the one way to it.
    SM.state = copy.deepcopy(PLAYERS)
    client.post("/api/v1/thumbnail:preview", json={})
    desktop.renderer.answer()
    executor.drain(WAIT)
    assert desktop.service.PreviewImage() is not None

    assert WebAccess.StaticPath("tmp/thumbnail/preview.png") is None
    assert client.get("/tmp/thumbnail/preview.png").status_code == 404


# ── who may ──────────────────────────────────────────────────────────────

def test_the_writes_are_guarded_and_the_reads_are_not():
    access = WebAccess.ROUTE_ACCESS
    for name in ("thumbnail_update", "thumbnail_reset", "thumbnail_background",
                 "thumbnail_preview", "thumbnail_generate"):
        assert access[f"api_v1.{name}"] == WebAccess.WRITE, name
    for name in ("get_thumbnail", "get_thumbnail_preview"):
        assert access[f"api_v1.{name}"] == WebAccess.READ, name


@pytest.mark.parametrize("method, url, body", [
    ("PATCH", "/api/v1/thumbnail", {"display_phase": False}),
    ("POST", "/api/v1/thumbnail:reset", {"confirm": True}),
    ("POST", "/api/v1/thumbnail/background", {"image": "data:x;base64,AA=="}),
    ("POST", "/api/v1/thumbnail:preview", {}),
    ("POST", "/api/v1/thumbnail:generate", {}),
])
def test_another_computer_needs_the_token(desktop, executor, webApp, method,
                                          url, body):
    from src.AutonaWebServer import WebServer  # noqa: F401
    other = webApp.test_client()
    other.environ_base["REMOTE_ADDR"] = "192.0.2.10"
    settings = copy.deepcopy(SettingsManager.settings)

    response = other.open(url, method=method, json=body)

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert SettingsManager.settings == settings
    assert desktop.renderer.asked == []


# ── the legacy route (an external contract S4o does not touch) ───────────

@pytest.mark.parametrize("extension, mimetype", [
    ("png", "image/png"), ("jpg", "image/jpg")])
def test_the_legacy_thumbnail_route_answers_as_it_did(
        env, client, monkeypatch, extension, mimetype):
    image = env.dir / f"x.{extension}"
    image.write_bytes(b"an image")
    asked = []
    monkeypatch.setattr(WebOps, "renderThumbnail",
                        lambda n, f: asked.append((n, f)) or str(image))

    response = client.get(f"/scoreboard2-get-thumbnail-{extension}")

    assert response.status_code == 200
    assert response.mimetype == mimetype
    assert response.get_data() == b"an image"
    response.close()
    assert asked == [("2", extension)]


def test_the_legacy_thumbnail_route_does_not_go_through_the_new_lane(
        env, client, monkeypatch):
    monkeypatch.setattr(WebOps, "renderThumbnail", lambda n, f: None)

    response = client.get("/scoreboard1-get-thumbnail-png")

    assert response.status_code == 200
    assert b"An error has occured" in response.get_data()
    assert SM.Get("thumbnail.render") is None
