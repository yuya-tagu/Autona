"""The Event page's /api/v1 routes (ADR-005 S4g).

What the React Event page (web/src/EventPage) writes through, added to
/api/v1 (POST /tournament extended, its defaults unchanged):

- ``GET    /api/v1/tournament``                the URL, loaded, the status
                                               (the state's, review M1)
- ``POST   /api/v1/tournament``                + admin URLs, force, fetchMedia;
                                               the status published first
- ``DELETE /api/v1/tournament``                the clear (202)
- ``PATCH  /api/v1/tournament/info``           the five fields
- ``PUT    /api/v1/tournament/media/<icon|banner>`` an image, a base64 data URL
- ``DELETE /api/v1/tournament/media``          both back to Autona's own
- ``POST   /api/v1/tournament/media/fetch``    202; the result in the state
- ``PATCH  /api/v1/colors/tournament``         the tournament's colours

Through Flask's test client against the real route table, on a
TournamentMediaService of the test's own in a directory of its own (the
`env` fixture of tests/test_tournament_media_service.py, where the
operations themselves are pinned): the contract (envelopes, 400 / 409 /
413 / 500, nothing written on a refusal) and the access rules (this PC
without the token, another computer with it).
"""
import base64
import copy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src import AutonaApiV1
from src import AutonaWebOperations as WebOps
from src.core import ColorService
from src.core import ImageImport
from src.core import StateSchema as schema
from src.core import TournamentMediaService as media
from src.core import TournamentService as tournament
from src.core import WebAccess
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.TournamentInfoService import TournamentInfoService
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_tournament_media_service import (  # noqa: F401
    BANNER_PATH, BANNER_VERSION, ICON_PATH, LOGO_VERSION, WAIT, _Provider,
    _image_bytes, _serve, env, executor)

EVENT_URL = "https://www.start.gg/tournament/autona-cup/event/singles"
ADMIN_URL = "https://www.start.gg/admin/tournament/autona-cup/brackets/123"


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


def _data_url(size=(64, 32), fmt="PNG", mime="image/png"):
    encoded = base64.b64encode(_image_bytes(size, fmt=fmt)).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _info():
    return copy.deepcopy(SM.Get(schema.TOURNAMENT_INFO_ROOT, {}))


@pytest.fixture
def info(env, monkeypatch):
    """The five fields on the test's own tournamentInfo (env's), the
    service's 表示大会名 ledger handed back."""
    service = TournamentInfoService.instance
    monkeypatch.setattr(service, "_lastTournamentName",
                        service._lastTournamentName)
    return service


# ── PATCH /tournament/info ───────────────────────────────────────────────

def test_the_five_fields_are_one_publish(info, client):
    before = SM.revision

    body = _ok(client.patch("/api/v1/tournament/info", json={
        "tournamentName": "Autona Cup", "eventName": "Singles",
        "address": "Tokyo", "numEntrants": 64,
        "startAtDate": "2026-11-01"}))

    assert body["revision"] == SM.revision == before + 1
    assert SM.Get("tournamentInfo.tournamentName") == "Autona Cup"
    assert SM.Get("tournamentInfo.eventName") == "Singles"
    assert SM.Get("tournamentInfo.address") == "Tokyo"
    assert SM.Get("tournamentInfo.numEntrants") == 64
    # The day, and the PC's short date beside it (as the EVENT tab wrote).
    assert SM.Get("tournamentInfo.startAtDate") == "2026-11-01"
    assert SM.Get("tournamentInfo.startAt")
    # The display name follows the tournament name, as from the EVENT tab.
    assert SM.Get("tournamentInfo.displayTournamentName") == "Autona Cup"


def test_a_field_alone_is_written_alone(info, client):
    _ok(client.patch("/api/v1/tournament/info", json={"address": "Osaka"}))
    _ok(client.patch("/api/v1/tournament/info", json={"numEntrants": 0}))

    assert SM.Get("tournamentInfo.address") == "Osaka"
    assert SM.Get("tournamentInfo.numEntrants") == 0
    assert SM.Get("tournamentInfo.startAtDate", None) is None


def test_the_entrant_and_date_limits(info, client):
    # The entrants: any count start.gg reports (S4g review L7; the Qt tab
    # stopped at 9999). The days: the Qt tab's date edit's.
    _ok(client.patch("/api/v1/tournament/info", json={
        "numEntrants": AutonaApiV1.TOURNAMENT_ENTRANTS_MAX,
        "startAtDate": "2000-01-01"}))
    _ok(client.patch("/api/v1/tournament/info", json={
        "startAtDate": "2999-12-31"}))
    assert SM.Get("tournamentInfo.numEntrants") == 999999
    assert SM.Get("tournamentInfo.startAtDate") == "2999-12-31"


def test_the_entrant_limit_is_the_pages():
    page = (Path(__file__).resolve().parents[1] / "web" / "src"
            / "EventPage" / "Cards.tsx").read_text(encoding="utf-8")
    assert (f"export const ENTRANTS_MAX = "
            f"{AutonaApiV1.TOURNAMENT_ENTRANTS_MAX};") in page


def test_a_day_this_pc_cannot_convert_is_rejected_without_writing(
        info, client, monkeypatch):
    """S4g review L4: TournamentInfoService.SetDate refused it, and the
    route answered 200 -- with the other fields of the request written."""
    _ok(client.patch("/api/v1/tournament/info", json={"address": "Kept"}))
    kept = _info()
    monkeypatch.setattr(TournamentInfoService, "_StartOfDay",
                        staticmethod(lambda day: None))

    _assertRejected(client.patch("/api/v1/tournament/info", json={
        "address": "Osaka", "startAtDate": "2026-11-01"}))

    assert _info() == kept
    # A day already published is no refusal (SetDate answers False too).
    monkeypatch.undo()
    _ok(client.patch("/api/v1/tournament/info",
                     json={"startAtDate": "2026-11-01"}))
    _ok(client.patch("/api/v1/tournament/info",
                     json={"startAtDate": "2026-11-01"}))


@pytest.mark.parametrize("body", [
    {},
    {"venue": "Tokyo"},
    {"startAt": "2026-11-01"},
    {"tournamentName": 5},
    {"address": None},
    {"numEntrants": -1},
    {"numEntrants": 1000000},
    {"numEntrants": "64"},
    {"numEntrants": 6.5},
    {"numEntrants": True},
    {"startAtDate": "1999-12-31"},
    {"startAtDate": "3000-01-01"},
    {"startAtDate": "2026-1-1"},
    {"startAtDate": "2026-02-30"},
    {"startAtDate": "２０２６-11-01"},
    {"startAtDate": None},
    {"startAtDate": ""},
    {"address": "Tokyo", "numEntrants": -1},
    ["address"],
])
def test_a_bad_info_write_is_rejected_without_writing(info, client, body):
    _ok(client.patch("/api/v1/tournament/info", json={"address": "Kept"}))
    kept = _info()

    _assertRejected(client.patch("/api/v1/tournament/info", json=body))

    assert _info() == kept


def test_the_v1_field_names_are_the_schemas():
    # TOURNAMENT_INPUT_KEYS names the fields this route takes since the
    # EVENT tab is gone (ADR-005 S4g): the raw day, not the formatted date.
    assert schema.TOURNAMENT_INPUT_KEYS == (
        "tournamentName", "eventName", "address", "numEntrants",
        "startAtDate")
    assert set(schema.TOURNAMENT_INPUT_KEYS) <= set(
        schema.TOURNAMENT_INFO_KEYS)


# ── GET / POST / DELETE /tournament ──────────────────────────────────────

@pytest.fixture
def tournaments(autonaApp, monkeypatch):
    """A TournamentService of the test's own on a stand-in adapter, run in
    place (the GUI executor on this thread); TOURNAMENT_URL in a copy of
    the settings."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        None)
    calls = []

    class Adapter:
        def LoadTournament(self, url, initialLoading=False, force=False):
            calls.append(("load", url, force))
            return True

        def UnloadTournament(self):
            calls.append(("unload",))

        def AnnounceUrl(self, url):
            pass

        def ResolveEventUrl(self, url):
            return url

    service = tournament.TournamentService(provider=Adapter(),
                                           onMain=lambda fn: fn())
    monkeypatch.setattr(tournament.TournamentService, "instance", service)
    from src.core.GameAssetCatalog import GameAssetCatalog
    monkeypatch.setattr(GameAssetCatalog.instance, "ForgetSelection",
                        lambda: calls.append(("forget",)))
    # The published status starts as a fresh launch's; the session's is
    # handed back.
    published = {path: copy.deepcopy(SM.Get(path, None))
                 for path in (tournament.LOAD_PATH, tournament.URL_PATH)}
    SM.Set(tournament.LOAD_PATH,
           {"status": "idle", "error": "", "revision": 0})
    SM.Set(tournament.URL_PATH, "")
    yield SimpleNamespace(service=service, calls=calls)
    for path, value in published.items():
        if value is None:
            SM.Unset(path)
        else:
            SM.Set(path, value)


def test_the_status_is_read(tournaments, client):
    body = _ok(client.get("/api/v1/tournament"))
    assert body["data"] == {"url": "", "loaded": False, "status": "idle",
                            "error": "", "revision": 0}
    # The state's (review M1): with the state's revision.
    assert body["revision"] == SM.revision

    _ok(client.post("/api/v1/tournament", json={"url": EVENT_URL}), 202)
    data = _ok(client.get("/api/v1/tournament"))["data"]
    assert (data["url"], data["status"]) == (EVENT_URL, "loading")
    assert data["revision"] == 1
    assert SM.Get("tournamentInfo.url") == EVENT_URL
    assert SM.Get("tournamentInfo.load") == {
        "status": "loading", "error": "", "revision": 1}

    tournaments.service.PayloadApplied({"tournamentName": "Cup"}, True)
    assert _ok(client.get("/api/v1/tournament"))["data"]["status"] == (
        "loaded")


def test_the_status_is_published_before_the_202(tournaments, client,
                                               monkeypatch):
    """S4g review M1 / M3: the page used to poll an in-memory status that
    the GUI thread advanced later, so a read right after the 202 could
    answer the previous request's result. The request's status is in the
    state before the 202 now -- the GUI thread has not run the load yet --
    and the 202 names its revision."""
    queued = []
    monkeypatch.setattr(tournaments.service, "_onMain", queued.append)

    body = _ok(client.post("/api/v1/tournament", json={"url": EVENT_URL}),
               202)

    assert queued and tournaments.calls == []
    assert SM.Get("tournamentInfo.load") == {
        "status": "loading", "error": "", "revision": 1}
    assert body["data"]["loadRevision"] == 1
    assert body["revision"] == SM.revision

    body = _ok(client.delete("/api/v1/tournament"), 202)
    assert SM.Get("tournamentInfo.load") == {
        "status": "idle", "error": "", "revision": 2}
    assert body["data"]["loadRevision"] == 2
    while queued:
        queued.pop(0)()
    assert tournaments.calls == [("forget",), ("unload",)]


def test_the_status_says_a_tournament_is_loaded(tournaments, client,
                                                monkeypatch):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        SimpleNamespace(url=EVENT_URL))
    assert _ok(client.get("/api/v1/tournament"))["data"]["loaded"] is True


def test_the_load_defaults_are_what_the_route_always_did(tournaments,
                                                         client):
    body = _ok(client.post("/api/v1/tournament", json={"url": EVENT_URL}),
               202)
    assert body["data"] == {"accepted": True, "operation": "tournament.load",
                            "loadRevision": 1}
    assert tournaments.calls == [("load", EVENT_URL, False)]


def test_force_and_fetch_media_are_passed_on(tournaments, client,
                                             monkeypatch):
    sent = []
    monkeypatch.setattr(
        WebOps, "set_tournament",
        lambda url, **flags: sent.append((url, flags)) or 0)

    _ok(client.post("/api/v1/tournament", json={
        "url": EVENT_URL, "force": True, "fetchMedia": True}), 202)
    _ok(client.post("/api/v1/tournament", json={"url": EVENT_URL}), 202)

    assert sent == [
        (EVENT_URL, {"force": True, "fetchMedia": True}),
        (EVENT_URL, {"force": False, "fetchMedia": False})]


def test_an_admin_url_is_taken_now(tournaments, client, executor):
    """It answered 400 until S4g; it is resolved by a job (S4f)."""
    _ok(client.post("/api/v1/tournament", json={"url": ADMIN_URL}), 202)
    assert _ok(client.get("/api/v1/tournament"))["data"]["status"] == (
        "resolving")
    assert executor.drain(WAIT)
    data = _ok(client.get("/api/v1/tournament"))["data"]
    # Resolves to itself here: start.gg named no event. Nothing changed.
    assert (data["status"], data["error"]) == ("error", "unresolved")
    assert tournaments.calls == []


@pytest.mark.parametrize("body", [
    {"url": "https://example.com/tournament/a/event/b"},
    {"url": "https://www.start.gg/tournament/autona-cup"},
    {"url": 5},
    # Any false value used to clear the tournament (S4g review L3).
    {"url": False},
    {"url": 0},
    {"url": []},
    {"url": {}},
    {"url": EVENT_URL, "force": "true"},
    {"url": EVENT_URL, "fetchMedia": 1},
    {"url": EVENT_URL, "force": None},
])
def test_a_bad_load_is_rejected_and_changes_nothing(tournaments, client,
                                                    body):
    _assertRejected(client.post("/api/v1/tournament", json=body))
    assert tournaments.calls == []
    assert not AutonaApiV1._heavy._inflight


@pytest.mark.parametrize("body", [{"url": ""}, {"url": None}, {}])
def test_an_empty_url_clears(tournaments, client, body):
    _ok(client.post("/api/v1/tournament", json={"url": EVENT_URL}), 202)
    _ok(client.post("/api/v1/tournament", json=body), 202)
    assert tournaments.calls[-2:] == [("forget",), ("unload",)]
    assert SM.Get("tournamentInfo.url") == ""


def test_the_clear_is_accepted_and_forgets_the_game(tournaments, client):
    _ok(client.post("/api/v1/tournament", json={"url": EVENT_URL}), 202)

    body = _ok(client.delete("/api/v1/tournament"), 202)

    assert body["data"] == {"accepted": True,
                            "operation": "tournament.clear",
                            "loadRevision": 2}
    assert tournaments.calls[-2:] == [("forget",), ("unload",)]
    data = _ok(client.get("/api/v1/tournament"))["data"]
    assert (data["url"], data["status"]) == ("", "idle")


# ── PUT /tournament/media/<kind>, DELETE /tournament/media ──────────────

@pytest.mark.parametrize("kind, size, saved_size, path, version", [
    ("icon", (1024, 512), (512, 256), ICON_PATH, LOGO_VERSION),
    ("banner", (3840, 1080), (1920, 540), BANNER_PATH, BANNER_VERSION),
])
def test_an_image_is_scaled_saved_and_its_version_published(
        env, client, kind, size, saved_size, path, version):
    body = _ok(client.put(f"/api/v1/tournament/media/{kind}", json={
        "image": _data_url(size, fmt="JPEG", mime="image/jpeg")}))

    with Image.open(path) as saved:
        assert saved.format == "PNG"
        assert saved.size == saved_size
    assert SM.Get(version) > 0
    assert body["revision"] == SM.revision


@pytest.mark.parametrize("path, body", [
    ("/api/v1/tournament/media/logo", {"image": "png"}),
    ("/api/v1/tournament/media/Icon", {"image": "png"}),
    ("/api/v1/tournament/media/icon", {}),
    ("/api/v1/tournament/media/icon", {"image": 5}),
    ("/api/v1/tournament/media/icon", {"image": "data:image/png,iVBORw0="}),
    ("/api/v1/tournament/media/banner",
     {"image": "data:image/png;base64,R0lGODlhAQABAAAAACw="}),
    ("/api/v1/tournament/media/banner",
     {"image": "data:image/png;base64," + base64.b64encode(
         b"not an image").decode("ascii")}),
])
def test_a_bad_image_is_rejected_and_nothing_saved(env, client, path, body):
    before = _info()

    _assertRejected(client.put(path, json=body))

    assert _info() == before
    assert not Path(ICON_PATH).exists()
    assert not Path(BANNER_PATH).exists()


def test_an_image_that_cannot_be_saved_is_an_internal_error(
        env, client, monkeypatch):
    def held(path, data):
        raise PermissionError("held open")
    monkeypatch.setattr(ImageImport, "WriteFile", held)

    _assertRejected(client.put("/api/v1/tournament/media/icon", json={
        "image": _data_url()}), "INTERNAL", 500)

    assert SM.Get(LOGO_VERSION, None) is None


def test_the_upload_limit_is_the_stream_orgs(env, client):
    # The Content-Length says it: refused before the body is read.
    response = client.put(
        "/api/v1/tournament/media/banner", data=b'{"image": ""}',
        content_type="application/json",
        environ_overrides={
            "CONTENT_LENGTH": str(AutonaApiV1._ORG_IMAGE_MAX_BODY + 1)})

    _assertRejected(response, "PAYLOAD_TOO_LARGE", 413)
    assert not Path(BANNER_PATH).exists()


def test_the_image_stays_out_of_the_op_log(env, client):
    from loguru import logger

    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="INFO")
    image = _data_url()
    try:
        _ok(client.put("/api/v1/tournament/media/banner",
                       json={"image": image}))
    finally:
        logger.remove(sink)

    opLog = [line for line in lines
             if "op=tournament.media.import" in line]
    assert len(opLog) == 1
    assert "['image']" in opLog[0]
    assert image.partition(",")[2][:40] not in opLog[0]


def test_the_reset_puts_both_back(env, client):
    for kind in ("icon", "banner"):
        _ok(client.put(f"/api/v1/tournament/media/{kind}", json={
            "image": _data_url()}))

    body = _ok(client.delete("/api/v1/tournament/media"))

    assert body["revision"] == SM.revision
    # Autona's icon (the env's blue square), and no banner.
    with Image.open(ICON_PATH) as icon:
        assert icon.size == (64, 64)
    assert not Path(BANNER_PATH).exists()
    assert SM.Get(BANNER_VERSION) == 0


def test_a_reset_that_fails_is_an_internal_error(env, client):
    Path(media.DEFAULT_ICON_PATH).unlink()

    _assertRejected(client.delete("/api/v1/tournament/media"), "INTERNAL",
                    500)


# ── POST /tournament/media/fetch ────────────────────────────────────────

def test_no_tournament_has_nothing_to_fetch(env, client, executor):
    _assertRejected(client.post("/api/v1/tournament/media/fetch"),
                    "NOT_READY", 409)
    assert executor.queued() == 0


def test_a_fetch_is_accepted_then_arrives_in_the_state(
        env, client, executor, monkeypatch):
    provider = env.loaded["provider"] = _Provider(url=EVENT_URL)
    _serve(monkeypatch, {provider.icon: _image_bytes((64, 64)),
                         provider.banner: _image_bytes((320, 180))})

    body = _ok(client.post("/api/v1/tournament/media/fetch"), 202)
    assert body["data"] == {"accepted": True,
                            "operation": "tournament.media.fetch",
                            "queued": False}
    assert SM.Get(media.MEDIA_PATH)["status"] == media.RUNNING
    assert body["revision"] == SM.revision

    # The same tournament: one at a time.
    _assertRejected(client.post("/api/v1/tournament/media/fetch"),
                    "CONFLICT", 409)
    # Another one: queued behind it (S4f review M1).
    env.loaded["provider"] = _Provider(
        url="https://www.start.gg/tournament/other/event/doubles")
    queued = _ok(client.post("/api/v1/tournament/media/fetch"), 202)
    assert queued["data"]["queued"] is True

    env.loaded["provider"] = provider
    assert executor.drain(WAIT)
    assert SM.Get(media.MEDIA_PATH)["status"] == media.DONE
    assert Path(ICON_PATH).exists() and Path(BANNER_PATH).exists()


# ── PATCH /colors/tournament ─────────────────────────────────────────────

@pytest.fixture
def colors(autonaApp, monkeypatch):
    """The settings in a copy; the state's colours handed back."""
    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    kept = {root: copy.deepcopy(SM.Get(root, None))
            for root in ("colors", schema.TOURNAMENT_INFO_ROOT)}
    yield
    with SM.SaveBlock():
        for root, value in kept.items():
            if value is None:
                SM.Unset(root)
            else:
                SM.Set(root, value)
    autonaApp.settle()


def test_a_tournament_colour_clears_the_overrides_of_its_kind(colors,
                                                              client):
    ColorService.SetFeatureColor("board", ColorService.KIND_THEME, "#111111")
    ColorService.SetFeatureColor("board", ColorService.KIND_ACCENT,
                                 "#222222")
    before = SM.revision

    body = _ok(client.patch("/api/v1/colors/tournament",
                            json={"theme": "#123456"}))

    # One publish, the whole cascade (the Qt card's behaviour).
    assert body["revision"] == SM.revision == before + 1
    assert body["data"]["theme"] == "#123456"
    assert SettingsManager.Get("general.theme_color") == "#123456"
    assert SM.Get("tournamentInfo.themeColor") == "#123456"
    assert ColorService.GetFeatureColors("board")[0] == "#123456"
    assert ColorService.GetFeatureColors("board")[1] == "#222222"


def test_both_colours_are_one_publish(colors, client):
    before = SM.revision
    body = _ok(client.patch("/api/v1/colors/tournament", json={
        "theme": "#abcdef", "accent": "#fedcba"}))
    assert body["revision"] == SM.revision == before + 1
    assert body["data"] == {"theme": "#abcdef", "accent": "#fedcba"}


def test_a_colour_is_kept_as_rrggbb(colors, client):
    """S4g review L13: #RGB and capitals were kept as sent -- unlike the
    Qt dialog's QColor.name(), and what an <input type="color"> cannot
    show. The tournament's and a feature's alike."""
    body = _ok(client.patch("/api/v1/colors/tournament", json={
        "theme": "#ABC", "accent": "#A1B2C3"}))
    assert body["data"] == {"theme": "#aabbcc", "accent": "#a1b2c3"}
    assert SettingsManager.Get("general.theme_color") == "#aabbcc"
    assert SM.Get("tournamentInfo.accentColor") == "#a1b2c3"

    _ok(client.patch("/api/v1/colors/board", json={"theme": "#F0A"}))
    assert ColorService.GetFeatureColors("board")[0] == "#ff00aa"
    assert SM.Get(ColorService.FeatureColorKey(
        "board", ColorService.KIND_THEME)) == "#ff00aa"


def test_the_reset_is_autonas_own_pair(colors, client):
    _ok(client.patch("/api/v1/colors/tournament", json={
        "theme": "#abcdef", "accent": "#fedcba"}))

    body = _ok(client.patch("/api/v1/colors/tournament",
                            json={"reset": True}))

    assert body["data"] == {"theme": schema.DEFAULT_THEME_COLOR,
                            "accent": schema.DEFAULT_ACCENT_COLOR}
    assert SM.Get("tournamentInfo.accentColor") == (
        schema.DEFAULT_ACCENT_COLOR)


@pytest.mark.parametrize("body", [
    {},
    {"reset": False},
    {"reset": "true"},
    {"reset": True, "theme": "#123456"},
    {"theme": "red"},
    {"accent": "#12345"},
    {"theme": "#123456", "border": "#123456"},
    ["theme"],
])
def test_a_bad_colour_write_is_rejected_without_writing(colors, client,
                                                        body):
    saved = copy.deepcopy(SettingsManager.settings)
    kept = _info()

    _assertRejected(client.patch("/api/v1/colors/tournament", json=body))

    assert SettingsManager.settings == saved
    assert _info() == kept


def test_the_feature_route_still_refuses_an_unknown_feature(colors, client):
    _assertRejected(client.patch("/api/v1/colors/everything",
                                 json={"theme": "#123456"}))


# ── access (ADR-005 S3a): this PC without the token, others with it ─────

_IMAGE = "data:image/png;base64,iVBORw0KGgo="
_WRITES = [
    ("patch", "/api/v1/tournament/info", {"address": "X"},
     "update_tournament_info", 200),
    ("delete", "/api/v1/tournament", None, "clear_tournament", 202),
    ("put", "/api/v1/tournament/media/icon", {"image": _IMAGE},
     "import_tournament_media", 200),
    ("delete", "/api/v1/tournament/media", None, "reset_tournament_media",
     200),
    ("post", "/api/v1/tournament/media/fetch", None,
     "fetch_tournament_media", 202),
    ("patch", "/api/v1/colors/tournament", {"theme": "#123456"},
     "set_tournament_colors", 200),
]


@pytest.fixture
def lan(monkeypatch, webApp):
    """The routes' operations recorded, the token private."""
    from src.AutonaWebServer import WebServer
    from tests.test_web_access import LAN_HOST, LAN_HOST_2

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    calls = []
    for _method, _path, _body, name, _status in _WRITES:
        monkeypatch.setattr(
            WebOps, name,
            lambda *args, _name=name: calls.append(_name) or media.STARTED)
    # What the colour route reads back.
    monkeypatch.setattr(ColorService, "GetTournamentColors",
                        lambda: ("#123456", "#654321"))
    return calls


def _send(client, method, path, body, **kwargs):
    if body is None:
        return getattr(client, method)(path, **kwargs)
    return getattr(client, method)(path, json=body, **kwargs)


@pytest.mark.parametrize("method, path, body, operation, status", _WRITES)
def test_the_writes_need_the_token_from_another_computer(
        autonaApp, webApp, lan, method, path, body, operation, status):
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    refused = _send(peer, method, path, body)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert lan == []

    token = WebAccess.RegenerateToken()
    assert _send(peer, method, path, body,
                 headers={"X-Autona-Token": token}).status_code == status
    assert lan == [operation]

    # This PC (the desktop's embedded view): no token.
    assert _send(webApp.test_client(), method, path, body
                 ).status_code == status
    assert lan == [operation, operation]


def test_the_read_needs_no_token(autonaApp, webApp, lan, monkeypatch):
    from tests.test_web_access import LAN_PEER

    monkeypatch.setattr(WebOps, "get_tournament", lambda: {"url": ""})
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    assert peer.get("/api/v1/tournament").status_code == 200
