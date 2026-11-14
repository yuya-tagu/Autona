"""The guides' /api/v1 routes (ADR-005 S-final SF-3a).

What the API / Companion / OBS guide windows read for themselves, for the
pages that replace them in SF-4a:

- ``GET /api/v1/api-catalog``  READ: core's ApiCatalog (the API window's
  reference rows and error codes)
- ``GET /api/v1/connection``   THIS_PC: the server's URLs and port, out/'s
  absolute path
- Socket.IO ``games_changed`` {}: the game list was scanned again

Not the access token (the API window shows and replaces it): a page of this
origin -- any layout/ page, in OBS or a browser -- passes as this PC's own
request, THIS_PC or not, so a THIS_PC route would hand it to whatever
layout is loaded. Its routes (SF-4a-1) are WebAccess.SHELL, behind a secret
the shell adds only to the shared view's main frame's fetches: pinned in
tests/test_web_access.py and tests/test_web_panel_host.py.

Who may call which is tests/test_web_access.py's (the guard: LAN, a foreign
page, a rebound host or a relayed request is 403, token or not); here the
answers, the API window reading the catalogue from core, and the shells
telling the web server about a scan.
"""
import ast
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.core import ApiCatalog, WebAccess
from src.core.SettingsManager import SettingsManager
from tests.test_web_access import (  # noqa: F401
    LAN_HOST, LAN_PEER, _client, apiWindow, server, settings)

CATALOG = "/api/v1/api-catalog"
CONNECTION = "/api/v1/connection"
SRC = Path(__file__).resolve().parents[1] / "src"


def _ok(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body["data"]


# ── the API catalogue ────────────────────────────────────────────────────

def test_the_catalog_is_cores_rows(server):
    data = _ok(_client(server, LAN_PEER).get(CATALOG))

    assert data == json.loads(json.dumps(ApiCatalog.Payload()))
    assert [row["id"] for row in data["endpoints"]] == [
        entry.id for entry in ApiCatalog.ENDPOINTS]
    for row, entry in zip(data["endpoints"], ApiCatalog.ENDPOINTS):
        assert row == {
            "id": entry.id, "method": entry.method, "path": entry.path,
            "example": ApiCatalog.ConcretePath(entry.path),
            "description": entry.desc, "body": entry.body,
            "destructive": entry.destructive, "logged": entry.logged,
            "confirm": entry.confirm}
    assert data["errorCodes"] == [
        {"code": error.code, "status": error.status,
         "description": error.desc} for error in ApiCatalog.ERROR_CODES]
    assert all(isinstance(error["status"], int)
               for error in data["errorCodes"])


def test_each_rows_id_is_the_route_its_example_reaches(server):
    # The id is what a page keys a row's translation by: the route's Flask
    # endpoint, stable while the route is. One id, one row.
    routes = server.app.url_map.bind("localhost")
    ids = [entry.id for entry in ApiCatalog.ENDPOINTS]
    assert len(set(ids)) == len(ids)
    for entry in ApiCatalog.ENDPOINTS:
        # The query is not the route's.
        path = ApiCatalog.ConcretePath(entry.path).split("?")[0]
        endpoint, _args = routes.match(f"/api/v1{path}", method=entry.method)
        assert endpoint == f"api_v1.{entry.id}", entry


def test_the_catalog_lists_no_token_route():
    # The window's token row is its own; the token's routes are the shell's
    # (SF-4a-1), not for a person to call by hand.
    assert not [entry for entry in ApiCatalog.ENDPOINTS
                if "token" in entry.path or "token" in entry.id]


def test_a_payload_is_a_copy():
    # A request's answer is not the catalogue: changing it changes nothing.
    first = ApiCatalog.Payload()
    first["endpoints"][0]["description"] = "changed"
    bodies = [row for row in first["endpoints"] if row["body"]]
    bodies[0]["body"]["changed"] = True

    again = ApiCatalog.Payload()
    assert again["endpoints"][0]["description"] == ApiCatalog.ENDPOINTS[0].desc
    assert "changed" not in next(
        row for row in again["endpoints"]
        if row["id"] == bodies[0]["id"])["body"]


def test_the_api_window_reads_the_catalog_from_core(apiWindow):
    from qtpy.QtWidgets import QLabel

    from src import AutonaApiInfoWindow as windowModule

    for name in ("_ENDPOINTS", "_ERROR_CODES", "_PATH_DEFAULTS",
                 "_concrete_path"):
        assert not hasattr(windowModule, name), name

    table = apiWindow.refTable
    assert table.rowCount() == len(ApiCatalog.ENDPOINTS)
    for row, entry in enumerate(ApiCatalog.ENDPOINTS):
        assert [table.item(row, column).text() for column in range(5)] == [
            entry.method, entry.path, windowModule._tr(entry.desc),
            "✓" if entry.confirm else "", "✓" if entry.logged else ""]

    combo = apiWindow.presetCombo
    assert [combo.itemText(i) for i in range(1, combo.count())] == [
        f"{entry.method}  {entry.path}" for entry in ApiCatalog.ENDPOINTS]

    legend = "\n".join(
        f"{error.code}  ({error.status})  {windowModule._tr(error.desc)}"
        for error in ApiCatalog.ERROR_CODES)
    assert legend in [label.text() for label in apiWindow.findChildren(QLabel)]


def test_the_api_window_fills_its_tester_from_a_core_row(apiWindow):
    row = next(i for i, entry in enumerate(ApiCatalog.ENDPOINTS)
               if entry.method == "POST" and entry.body)
    entry = ApiCatalog.ENDPOINTS[row]

    apiWindow.presetCombo.setCurrentIndex(row + 1)   # after "—"
    apiWindow._applyPreset(row + 1)

    assert apiWindow.methodCombo.currentText() == "POST"
    assert apiWindow.pathField.text() == ApiCatalog.ConcretePath(entry.path)
    assert json.loads(apiWindow.bodyEdit.toPlainText()) == entry.body
    assert ApiCatalog.ConcretePath(entry.path) in apiWindow._buildCurl(entry)


# ── the connection ───────────────────────────────────────────────────────

@pytest.mark.parametrize("listening, stored, expected", [
    # No server: the settings (what its next start binds).
    (None, {"webserver_port": 6123},
     {"port": 6123, "lan": False, "lanAtRestart": False, "host": "127.0.0.1"}),
    (None, {"webserver_port": 6123, "webserver_listen_lan": True},
     {"port": 6123, "lan": True, "lanAtRestart": False, "host": LAN_HOST}),
    # A server: what it is bound with, a saved change at its next start.
    (("127.0.0.1", 5107, False),
     {"webserver_port": 6123, "webserver_listen_lan": True},
     {"port": 5107, "lan": False, "lanAtRestart": True, "host": "127.0.0.1"}),
    (("0.0.0.0", 5107, True), {},
     {"port": 5107, "lan": True, "lanAtRestart": False, "host": LAN_HOST}),
])
def test_the_connection_is_what_the_guides_show(
        server, monkeypatch, listening, stored, expected):
    from src import AutonaApiInfoWindow, AutonaIntegrationGuideWindow
    from src import AutonaObsGuideWindow
    from src.AutonaWebServer import WebServer
    from src.Helpers import AutonaNetworkHelper

    monkeypatch.setattr(AutonaNetworkHelper, "GetLocalIP", lambda: LAN_HOST)
    monkeypatch.setattr(WebServer, "_listening", listening)
    monkeypatch.setattr(SettingsManager, "settings", {"general": stored})

    response = _client(server).get(CONNECTION)
    data = _ok(response)

    port, host = expected["port"], expected["host"]
    assert data == {
        "port": port, "lan": expected["lan"],
        "lanAtRestart": expected["lanAtRestart"],
        "baseUrl": f"http://{host}:{port}/api/v1",
        "localBaseUrl": f"http://127.0.0.1:{port}/api/v1",
        "outDir": os.path.normpath(os.path.join(os.getcwd(), "out")),
        "pathSeparator": os.sep}
    assert response.headers["Cache-Control"] == "no-store"
    # What each window shows (their own readers, not built).
    assert data["baseUrl"] == AutonaApiInfoWindow.AutonaApiInfoWindow._baseUrl(
        None)
    assert data["baseUrl"] == (
        AutonaIntegrationGuideWindow.AutonaIntegrationGuideWindow._baseUrl(
            None))
    obs = AutonaObsGuideWindow.AutonaObsGuideWindow
    assert data["localBaseUrl"] == obs._baseUrl(None)
    assert data["outDir"] == obs._outDir(None)
    assert (data["lan"], data["lanAtRestart"]) == (
        WebServer.Serving()[1], WebServer.LanOnAtRestart())
    # Nothing secret in it.
    assert WebAccess.Token()
    assert WebAccess.Token().encode() not in response.data


def test_a_watch_file_is_out_dir_joined_with_the_separator(server):
    # What the OBS window's file table shows for out/score/1/match.txt.
    data = _ok(_client(server).get(CONNECTION))
    joined = data["pathSeparator"].join(
        [data["outDir"], "score", "1", "match.txt"])
    assert joined == os.path.normpath(os.path.abspath("out/score/1/match.txt"))


# ── the game list's notice ───────────────────────────────────────────────

def _wire_web_server_call(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "wire_web_server"]
    assert len(calls) == 1, path
    return [ast.unparse(argument) for argument in calls[0].args]


def test_both_shells_tell_the_web_server_a_scan():
    # The desktop's scan signal (the asset adapter's onLoadAssets, emitted
    # by its scan job after ScanGames) and the headless loader's
    # gamesScanned: bootstrap.wire_web_server connects either to
    # WebServer.on_games_change (tests/test_bootstrap_split.py).
    assert _wire_web_server_call(SRC / "Autona.py")[2] == (
        "AutonaGameAssetManager.instance.signals.onLoadAssets")
    assert _wire_web_server_call(SRC / "headless.py")[2] == (
        "games.signals.gamesScanned")


@pytest.mark.parametrize("shell", ["desktop", "headless"])
def test_a_scan_notice_arrives_on_the_main_thread(qapp, shell):
    # The scan job emits from its own thread. WebServer.on_games_change is a
    # staticmethod -- a plain function, no QObject to live in a thread -- so
    # Qt queues the call to the main thread, the headless shell's too. (It
    # only queues under _delta_cond, safe on any thread anyway.)
    if shell == "desktop":
        from src.AutonaGameAssetManager import AutonaGameAssetManagerSignals
        signals = AutonaGameAssetManagerSignals()   # created on the main thread
        signal = signals.onLoadAssets
    else:
        from src.headless import HeadlessSignals
        signals = HeadlessSignals()
        signal = signals.gamesScanned
    ran = []

    class Server:
        @staticmethod
        def on_games_change(*_args):
            ran.append(threading.current_thread())

    signal.connect(Server().on_games_change)   # as bootstrap.wire_web_server
    job = threading.Thread(target=signal.emit, name="scan job")
    job.start()
    job.join(30)
    assert not job.is_alive()
    assert ran == []        # not run on the scan job

    deadline = time.monotonic() + 30
    while not ran and time.monotonic() < deadline:
        qapp.processEvents()
    assert ran == [threading.main_thread()]


def test_the_headless_scan_tells_it_after_scanning(qapp, monkeypatch):
    from src import headless
    from src.Helpers import AutonaQtHelper

    order = []

    class Catalog:
        def ScanGames(self):
            order.append("scan")

    monkeypatch.setattr(AutonaQtHelper, "gui_executor",
                        lambda fn: order.append("then on the main thread"))
    games = headless.HeadlessGames(Catalog(), provider=None)
    games.signals.gamesScanned.connect(lambda: order.append("told"))

    games._Scan()

    assert order == ["scan", "told", "then on the main thread"]


def test_the_desktop_scan_emits_after_scanning():
    # The signal the desktop passes: emitted by the scan job right after
    # GameAssetCatalog.ScanGames (GET /games answers from that scan).
    tree = ast.parse((SRC / "AutonaGameAssetManager.py").read_text(
        encoding="utf-8"))
    scan = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_Scan")
    assert [ast.unparse(statement) for statement in scan.body] == [
        "self.catalog.ScanGames()", "self.signals.onLoadAssets.emit()"]
