"""The guides' /api/v1 routes (ADR-005 S-final SF-3a).

What the API / Companion / OBS guide pages read (SF-4a-2: /web/api,
/web/companion, /web/obs; the Qt windows read it for themselves until then):

- ``GET /api/v1/api-catalog``  READ: core's ApiCatalog (the API page's
  reference rows and error codes)
- ``GET /api/v1/connection``   THIS_PC: the server's URLs and port, out/'s
  absolute path
- Socket.IO ``games_changed`` {}: the game list was scanned again

Not the access token (the API page shows and replaces it in the app): a
page of this origin -- any layout/ page, in OBS or a browser -- passes as
this PC's own request, THIS_PC or not, so a THIS_PC route would hand it to
whatever layout is loaded. Its routes (SF-4a-1) are WebAccess.SHELL, behind
a secret the shell adds only to the shared view's main frame's fetches:
pinned in tests/test_web_access.py and tests/test_web_panel_host.py.

Who may call which is tests/test_web_access.py's (the guard: LAN, a foreign
page, a rebound host or a relayed request is 403, token or not); here the
answers, the pages' own tables held to them (each catalogue row and error
code translated, each Companion / OBS example a v1 route), and the shells
telling the web server about a scan. The pages themselves are
web/src/GuidePages/GuidePages.test.js's.
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
    LAN_HOST, LAN_PEER, _client, server, settings)

CATALOG = "/api/v1/api-catalog"
CONNECTION = "/api/v1/connection"
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
WEB = ROOT / "web" / "src"
# The Companion and OBS guides' example requests (SF-4a-2).
EXAMPLES = WEB / "GuidePages" / "examples.json"
LOCALES = WEB / "i18n" / "locales"


def _locale(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


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


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_every_row_and_error_code_has_its_translation(lang):
    # The API page shows guide_api_endpoint_<id> / guide_api_error_<code>
    # (the catalogue's English only as a fallback); English is the
    # catalogue's own text, so the two cannot drift apart.
    strings = _locale(lang)
    for entry in ApiCatalog.ENDPOINTS:
        key = f"guide_api_endpoint_{entry.id}"
        assert strings.get(key), (lang, key)
        if lang == "en":
            assert strings[key] == entry.desc, key
    for error in ApiCatalog.ERROR_CODES:
        key = f"guide_api_error_{error.code}"
        assert strings.get(key), (lang, key)
        if lang == "en":
            assert strings[key] == error.desc, key


def _examples():
    return json.loads(EXAMPLES.read_text(encoding="utf-8"))


def test_the_examples_are_one_table_for_both_guides():
    rows = _examples()
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids)
    assert ids == [
        "team1_score_up", "team1_score_down", "team2_score_up",
        "team2_score_down", "reset_scores", "swap_teams", "apply_changes",
        "pull_stream", "stats_refresh", "bracket_refresh"]
    for row in rows:
        assert set(row) == {"id", "method", "path", "body", "icon",
                            "guides"}, row
        assert row["guides"] and set(row["guides"]) <= {"companion", "obs"}
        assert set(row["icon"]) == {"bg", "top", "main"}, row
    # The OBS guide never listed the statistics (the window's table).
    assert [row["id"] for row in rows if "obs" not in row["guides"]] == [
        "stats_refresh"]
    for lang in ("en", "ja"):
        strings = _locale(lang)
        for row in rows:
            assert strings.get(f"guide_example_{row['id']}"), (lang, row)


def test_every_example_is_a_v1_route(server):
    # What the Companion / OBS guides tell an operator to send: each one's
    # method and path reach a /api/v1 route (the URL map) that the
    # catalogue lists with the same method, needs no {"confirm": true} and
    # is a write the guard knows.
    routes = server.app.url_map.bind("localhost")
    for row in _examples():
        endpoint, _args = routes.match(f"/api/v1{row['path']}",
                                       method=row["method"])
        assert endpoint.startswith("api_v1."), row
        name = endpoint.split(".", 1)[1]
        entry = next((entry for entry in ApiCatalog.ENDPOINTS
                      if entry.id == name), None)
        assert entry is not None, row
        assert entry.method == row["method"]
        assert not entry.confirm, row
        assert WebAccess.RouteAccess(endpoint) == WebAccess.WRITE, row


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
    # What the windows showed until SF-4a-2 (GetWebServerHost and the port
    # being served, the loopback for OBS, ./out made absolute) is the fixed
    # expectation above.
    assert (data["lan"], data["lanAtRestart"]) == (
        WebServer.Serving()[1], WebServer.LanOnAtRestart())
    # Nothing secret in it.
    assert WebAccess.Token()
    assert WebAccess.Token().encode() not in response.data


def test_a_watch_file_is_out_dir_joined_with_the_separator(server):
    # What the OBS page's file table shows for out/score/1/match.txt
    # (web/src/GuidePages/format.ts's watchPath).
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


# ── The app's version (/api/v1/health; the About page shows it) ─────────

def _versions(directory, program):
    (directory / "assets").mkdir(parents=True)
    (directory / "assets" / "versions.json").write_text(
        json.dumps({"program": program}), encoding="utf-8")


@pytest.fixture
def version_dirs(monkeypatch, tmp_path):
    """A working directory and a bundle (sys._MEIPASS) of their own, the
    version read again."""
    from src import AutonaApiV1
    from src.Helpers import AutonaDirHelper

    work, bundle = tmp_path / "work", tmp_path / "bundle"
    work.mkdir()
    bundle.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(AutonaDirHelper, "current_dir", work)
    monkeypatch.setattr(AutonaApiV1, "_version_cache", None)
    return work, bundle


def test_a_release_reads_its_version_from_the_bundle(version_dirs,
                                                     monkeypatch):
    # The exe's folder has no assets/versions.json: it is under _internal/
    # (sys._MEIPASS). Read from the working directory, /health -- and the
    # About page -- said "v?".
    from src import AutonaApiV1
    from src.Helpers import AutonaDirHelper

    _work, bundle = version_dirs
    _versions(bundle, "9.8.7")
    monkeypatch.setattr(AutonaDirHelper, "is_in_bundle", True)
    monkeypatch.setattr("sys._MEIPASS", str(bundle), raising=False)

    assert AutonaApiV1._app_version() == "9.8.7"


def test_a_checkout_reads_its_version_from_the_working_directory(
        version_dirs, monkeypatch):
    from src import AutonaApiV1
    from src.Helpers import AutonaDirHelper

    work, _bundle = version_dirs
    _versions(work, "1.2.3")
    monkeypatch.setattr(AutonaDirHelper, "is_in_bundle", False)

    assert AutonaApiV1._app_version() == "1.2.3"


def test_no_versions_file_is_a_question_mark(version_dirs, monkeypatch):
    from src import AutonaApiV1
    from src.Helpers import AutonaDirHelper

    monkeypatch.setattr(AutonaDirHelper, "is_in_bundle", False)

    assert AutonaApiV1._app_version() == "?"
