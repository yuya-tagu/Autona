"""The Stats Display Settings page's routes (ADR-005 S4l) and core's table.

What the React page (web/src/StatsPreviewPage) reads and writes through,
added to /api/v1 only:

- ``GET /api/v1/stats/overlays``            ``{overlays, selected, refresh}``
- ``PUT /api/v1/stats/overlays/selected``   ``{"key"}`` -> the chosen item

Core's ``StatsOverlays`` keeps the choice under the Qt panel's settings key
(``stats_preview.selected_type``), so a choice made before the move carries
over. Through Flask's test client on the real route table, with settings
that are a copy and never saved.
"""
import copy

import pytest

from src import AutonaWebOperations as WebOps
from src.core import StatsOverlays, WebAccess
from src.core.SettingsManager import SettingsManager
from tests.test_api_v1 import _assertRejected, webApp  # noqa: F401

KEYS = ["upset_factor", "h2h_record", "recent_sets", "last_sets_p1",
        "last_sets_p2", "tournament_history_p1", "tournament_history_p2"]


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    SettingsManager.Unset(StatsOverlays.SETTINGS_KEY)


@pytest.fixture
def client(webApp, settings):  # noqa: F811
    return webApp.test_client()


def _ok(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body["data"]


# ── core ─────────────────────────────────────────────────────────────────

def test_the_table_is_the_qt_panels(settings):
    items = StatsOverlays.Overlays()

    assert [item["key"] for item in items] == KEYS
    assert StatsOverlays.SETTINGS_KEY == "stats_preview.selected_type"
    # Two entries of one folder, told apart by their entry.
    last = [item for item in items if item["id"] == "last_sets"]
    assert [item["entry"] for item in last] == ["player1.html", "player2.html"]
    history = [item for item in items if item["id"] == "tournament_history"]
    assert [item["entry"] for item in history] == [
        "player1.html", "player2.html"]
    assert items[0] == {"key": "upset_factor", "id": "upset_factor",
                        "entry": "index.html", "displayName": "Upset Factor"}


def test_nothing_kept_or_a_key_not_in_the_table_selects_the_first(settings):
    assert StatsOverlays.Selected()["key"] == "upset_factor"

    SettingsManager.Set(StatsOverlays.SETTINGS_KEY, "gone")
    assert StatsOverlays.Selected()["key"] == "upset_factor"
    # The operator's value is not overwritten by the fallback.
    assert SettingsManager.Get(StatsOverlays.SETTINGS_KEY) == "gone"


def test_select_keeps_a_key_and_an_unknown_one_keeps_nothing(settings):
    item = StatsOverlays.Select("last_sets_p2")

    assert item["entry"] == "player2.html"
    assert SettingsManager.Get(StatsOverlays.SETTINGS_KEY) == "last_sets_p2"
    assert StatsOverlays.Selected()["key"] == "last_sets_p2"

    assert StatsOverlays.Select("gone") is None
    assert StatsOverlays.Select(None) is None
    assert SettingsManager.Get(StatsOverlays.SETTINGS_KEY) == "last_sets_p2"


# ── GET /stats/overlays ──────────────────────────────────────────────────

def test_the_read_gives_the_table_the_choice_and_the_request(
        client, monkeypatch):
    from src.Helpers import AutonaNetworkHelper

    monkeypatch.setattr(AutonaNetworkHelper, "GetLocalIP",
                        lambda: "192.168.0.5")
    # No server runs here: the settings.
    SettingsManager.Set("general.webserver_port", 6000)
    SettingsManager.Set("general.webserver_listen_lan", True)
    SettingsManager.Set(StatsOverlays.SETTINGS_KEY, "recent_sets")

    body = client.get("/api/v1/stats/overlays")
    data = _ok(body)

    assert "revision" not in body.get_json()      # settings, not state
    assert [item["key"] for item in data["overlays"]] == KEYS
    assert data["selected"]["key"] == "recent_sets"
    assert data["refresh"] == {
        "method": "POST", "path": "/scoreboards/1/stats:refresh",
        "url": "http://192.168.0.5:6000/api/v1/scoreboards/1/stats:refresh"}


def test_the_request_url_is_of_the_server_that_listens_not_the_settings(
        client, monkeypatch):
    from src.AutonaWebServer import WebServer
    from src.Helpers import AutonaNetworkHelper

    monkeypatch.setattr(AutonaNetworkHelper, "GetLocalIP",
                        lambda: "192.168.0.5")
    # Settings changed since the start: they take effect at the next one.
    SettingsManager.Set("general.webserver_port", 6000)
    SettingsManager.Set("general.webserver_listen_lan", True)
    monkeypatch.setattr(WebServer, "_listening", ("127.0.0.1", 5107, False))

    url = _ok(client.get("/api/v1/stats/overlays"))["refresh"]["url"]

    assert url == "http://127.0.0.1:5107/api/v1/scoreboards/1/stats:refresh"

    monkeypatch.setattr(WebServer, "_listening", ("0.0.0.0", 5108, True))
    url = _ok(client.get("/api/v1/stats/overlays"))["refresh"]["url"]
    assert url == "http://192.168.0.5:5108/api/v1/scoreboards/1/stats:refresh"


def test_the_request_url_carries_no_token(client):
    SettingsManager.Set("general.webserver_token", "SECRET-TOKEN-VALUE")

    text = client.get("/api/v1/stats/overlays").get_data(as_text=True)

    assert "SECRET-TOKEN-VALUE" not in text


# ── PUT /stats/overlays/selected ─────────────────────────────────────────

def test_the_choice_is_kept_under_the_panels_key(client):
    data = _ok(client.put("/api/v1/stats/overlays/selected",
                          json={"key": "tournament_history_p2"}))

    assert data == {"key": "tournament_history_p2",
                    "id": "tournament_history", "entry": "player2.html",
                    "displayName": "Tournament History (Player 2)"}
    assert SettingsManager.Get(StatsOverlays.SETTINGS_KEY) == \
        "tournament_history_p2"
    assert _ok(client.get("/api/v1/stats/overlays"))["selected"]["key"] == \
        "tournament_history_p2"


@pytest.mark.parametrize("body", [{}, {"key": ""}, {"key": 5},
                                  {"key": "gone"}, {"key": None}])
def test_an_unknown_key_is_rejected_and_nothing_kept(client, body):
    SettingsManager.Set(StatsOverlays.SETTINGS_KEY, "recent_sets")

    _assertRejected(client.put("/api/v1/stats/overlays/selected", json=body))

    assert SettingsManager.Get(StatsOverlays.SETTINGS_KEY) == "recent_sets"


# ── access ───────────────────────────────────────────────────────────────

def test_the_read_is_a_read_and_the_write_a_write():
    assert WebAccess.ROUTE_ACCESS["api_v1.get_stats_overlays"] == \
        WebAccess.READ
    assert WebAccess.ROUTE_ACCESS["api_v1.select_stats_overlay"] == \
        WebAccess.WRITE


def test_the_write_needs_the_token_from_another_computer(
        webApp, settings, monkeypatch):  # noqa: F811
    from src.AutonaWebServer import WebServer
    from tests.test_web_access import LAN_HOST, LAN_HOST_2, LAN_PEER

    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    calls = []
    monkeypatch.setattr(WebOps, "select_stats_overlay",
                        lambda key: calls.append(key) or {"key": key})
    path, body = "/api/v1/stats/overlays/selected", {"key": "recent_sets"}

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    refused = peer.put(path, json=body)
    assert refused.status_code == 401
    assert calls == []

    token = WebAccess.RegenerateToken()
    assert peer.put(path, json=body,
                    headers={"X-Autona-Token": token}).status_code == 200
    assert calls == ["recent_sets"]

    # The read needs none.
    assert peer.get("/api/v1/stats/overlays").status_code == 200


# ── the fixed table is pinned against what it points at ──────────────────

def test_every_overlay_is_a_layout_that_exists():
    from tests.conftest import PROJECT_ROOT

    for item in StatsOverlays.Overlays():
        path = PROJECT_ROOT / "layout" / item["id"] / item["entry"]
        assert path.is_file(), item["key"]


def test_the_companion_request_is_a_post_route_of_the_server(webApp):  # noqa: F811
    adapter = webApp.url_map.bind("localhost")
    endpoint, _args = adapter.match(
        "/api/v1" + WebOps.STATS_REFRESH_PATH, method="POST")
    assert endpoint == "api_v1.stats_refresh"
    assert WebOps.STATS_REFRESH_METHOD == "POST"


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_the_page_translates_every_overlay(lang):
    import json
    from tests.conftest import PROJECT_ROOT

    path = PROJECT_ROOT / "web" / "src" / "i18n" / "locales" / f"{lang}.json"
    table = json.loads(path.read_text(encoding="utf-8"))
    missing = [item["key"] for item in StatsOverlays.Overlays()
               if not table.get(f"statspreview_overlay_{item['key']}")]
    assert missing == []
