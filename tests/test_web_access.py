"""The web server's public surface (ADR-005 S3a).

Three things are pinned here, through the real Flask app and Socket.IO
server that ``_build_web_app()`` builds (the handlers behind them are faked:
what is under test is who gets to them):

* static files: only the served folders (src/core/WebAccess.py
  STATIC_DIRECTORIES / STATIC_FILES), whatever the spelling of the path;
* the token: from another computer, writes and the player DB need it, in
  any of the three accepted places; reads never do;
* CSRF: without the token, a loopback write must come from this server's
  own pages -- not a cross-site one, not another origin, not ``null``.

The route / event classification itself is pinned against the ledger in
tests/test_webserver_routes.py.
"""

import json
import threading

import pytest
from loguru import logger

from src.core import WebAccess
from src.core.SettingsManager import SettingsManager

LAN_PEER = "192.168.1.20"
LAN_HOST = "192.168.1.5"   # this PC's LAN address in these tests
LAN_HOST_2 = "10.8.0.2"    # ... and a second one (a VPN adapter)


class FakeActions:
    """The web operations the routes below call, recorded.

    They are src/AutonaWebOperations.py's (the core-direct ones, ADR-005
    S3c); the fixture puts these in their place there.
    """

    def __init__(self):
        self.calls = []

    def program_state(self):
        return {"state": {"score": {}}, "revision": 0, "delta_index": 0}

    def team_scoreup(self, scoreboard, team):
        self.calls.append(("team_scoreup", str(scoreboard), str(team)))
        return "OK"

    def reset_scores(self, scoreboard):
        self.calls.append(("reset_scores", scoreboard))

    def get_games(self):
        return {"ssbu": {"name": "Smash"}}

    def get_playerdb(self):
        return {"Tag": {"gamerTag": "Tag", "name": "A Real Name"}}


@pytest.fixture
def settings(monkeypatch):
    """A private settings.json for the token (never the real user_data/),
    and WebAccess's in-memory copy of the token forgotten."""
    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    return SettingsManager


@pytest.fixture
def server(settings, monkeypatch, tmp_path):
    """The real app + Socket.IO server, the web operations it reaches
    faked, cwd = tmp_path."""
    from src import AutonaWebOperations
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app

    monkeypatch.chdir(tmp_path)
    actions = FakeActions()
    for name in ("program_state", "team_scoreup", "reset_scores",
                 "get_games", "get_playerdb"):
        monkeypatch.setattr(AutonaWebOperations, name, getattr(actions, name))
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())

    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    monkeypatch.setattr(WebServer, "socketio", socketio)

    class Server:
        pass

    s = Server()
    s.app, s.socketio, s.actions, s.root = app, socketio, actions, tmp_path
    return s


def _client(server, remote_addr="127.0.0.1"):
    # The test client addresses the server as http://localhost (port 80):
    # Host "localhost", SERVER_PORT "80". So "this server's origin" is
    # http://localhost / http://127.0.0.1 / http://<LAN_HOST> on port 80.
    client = server.app.test_client()
    client.environ_base["REMOTE_ADDR"] = remote_addr
    return client


@pytest.fixture
def warnings():
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    yield messages
    logger.remove(sink)


# ── Static files ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "layout/scoreboard_simple/index.html",
    "Layout/Scoreboard_Simple/index.html",
    "assets/icons/icon.png",
    "assets/data_countries.json",
    "out/program_state.json",
    "web/build/index.html",
    "web/build/static/js/main.js",
    "user_data/games/ssbu/base_files/logo.png",
    "user_data/team_logo/TEAM.png",
    "user_data/sponsor_logo/SPONSOR.png",
    "user_data/player_avatar/tag.png",
    "user_data/additional_flag/xx.png",
    "user_data/tournament_banner.png",
    "USER_DATA/Tournament_Banner.PNG",
    "./layout/x.js",
    "layout//x.js",
])
def test_the_served_folders_are_served(path):
    assert WebAccess.StaticPath(path) is not None


@pytest.mark.parametrize("path", [
    "user_data/settings.json",
    "User_Data/Settings.json",
    "user_data/local_players.json",
    "user_data/local_players.csv",
    "user_data/layout_options.json",
    "user_data/characters.json",
    "user_data/state.json",                  # the persisted state (S3b)
    "user_data/state.json.v2.20261025-120000.bak",
    "user_data/state.json.corrupt-20261025-120000.bak",
    "user_data/games",                       # the folder itself
    "user_data/games_extra/x.png",           # a prefix is not a folder
    "user_data/tournament_banner.png.bak",
    "layout/../user_data/settings.json",
    "layout/x/../../user_data/settings.json",
    "layout\\..\\user_data\\settings.json",
    "..\\user_data\\settings.json",
    "/user_data/settings.json",
    "\\user_data\\settings.json",
    "C:/Windows/win.ini",
    "layout/index.html::$DATA",
    "layout./x.js",                          # Windows would open layout/x.js
    "layout /x.js",
    "LAYOUT~1/x.js",
    "layout/x\x00.js",
    "main.py",
    "src/core/WebAccess.py",
    "logs/autona.log",
    "",
    ".",
    "..",
])
def test_everything_else_is_not(path):
    assert WebAccess.StaticPath(path) is None


def _write(root, relative, data=b"x"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_settings_and_the_player_db_are_not_served(server, warnings):
    _write(server.root, "user_data/settings.json", b'{"secret": 1}')
    _write(server.root, "user_data/local_players.json", b"{}")
    _write(server.root, "layout/scoreboard_simple/index.html", b"<html>")
    _write(server.root, "out/program_state.json", b"{}")
    _write(server.root, "assets/icons/icon.png", b"png")
    _write(server.root, "web/build/index.html", b"<html>")
    client = _client(server, LAN_PEER)

    for url in ("/layout/scoreboard_simple/index.html",
                "/out/program_state.json",
                "/assets/icons/icon.png",
                "/web/build/index.html",
                "/web"):
        response = client.get(url)
        assert response.status_code == 200, url
        response.close()

    for url in ("/user_data/settings.json",
                "/User_Data/Settings.json",
                "/user_data/local_players.json",
                "/layout/../user_data/settings.json",
                "/layout/%2e%2e/user_data/settings.json",
                "/./user_data/settings.json"):
        response = client.get(url)
        assert response.status_code == 404, url
        assert b"secret" not in response.data
    # The loopback is no exception: a page in a browser here is a loopback peer.
    assert _client(server).get("/user_data/settings.json").status_code == 404

    # One warning per path, however often it is asked for.
    refused = [m for m in warnings if "not serving" in m]
    assert sum("'user_data/settings.json'" in m for m in refused) == 1


def test_what_browsers_ask_for_on_their_own_is_not_a_warning(server, warnings):
    client = _client(server)
    for url in ("/favicon.ico", "/.well-known/appspecific/com.chrome.devtools.json",
                "/.Well-Known/security.txt"):
        assert client.get(url).status_code == 404
    assert not any("not serving" in m for m in warnings)
    client.get("/user_data/settings.json")
    assert any("not serving" in m for m in warnings)


def test_files_only_answer_get(server):
    _write(server.root, "layout/x.js")
    client = _client(server)
    assert client.get("/layout/x.js").status_code == 200
    # It used to take POST too (and read the file back).
    assert client.post("/layout/x.js").status_code == 405


def test_the_static_folder_is_gone(server):
    _write(server.root, "user_data/settings.json", b'{"secret": 1}')
    rules = {str(rule) for rule in server.app.url_map.iter_rules()}
    assert not any(rule.startswith("/./") for rule in rules)
    response = _client(server).get("/./user_data/settings.json")
    assert response.status_code == 404


# ── The token ────────────────────────────────────────────────────────────

def test_the_token_is_issued_once_and_kept_in_settings(settings):
    token = WebAccess.Token()
    assert settings.Get(WebAccess.TOKEN_SETTING) == token
    assert WebAccess.Token() == token
    # secrets.token_urlsafe(24): 32 URL-safe characters, unescaped in ?token=.
    assert len(token) == 32
    assert all(c.isalnum() or c in "-_" for c in token)


def test_regenerating_replaces_the_token(settings):
    old = WebAccess.Token()
    generation = WebAccess.TokenGeneration()
    new = WebAccess.RegenerateToken()
    assert new != old
    assert WebAccess.Token() == new
    assert WebAccess.TokenGeneration() == generation + 1
    assert not WebAccess.TokenMatches([old])
    assert WebAccess.TokenMatches([new])


def test_the_first_issue_is_logged(settings):
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        token = WebAccess.Token()
    finally:
        logger.remove(sink)
    assert any("first launch" in m for m in messages)
    assert not any(token in m for m in messages)


def test_the_token_is_kept_in_memory(settings):
    token = WebAccess.Token()
    generation = WebAccess.TokenGeneration()
    # settings.json edited, or emptied, while the app runs: the token in use
    # does not change (and nothing is issued or saved over it).
    settings.settings = {}
    assert WebAccess.Token() == token
    settings.settings = {"general": {"webserver_token": "another-long-token-1234"}}
    assert WebAccess.Token() == token
    assert WebAccess.TokenGeneration() == generation


def _unreadSettings(monkeypatch, path, content=None):
    """A launch that could not read settings.json, at `path`: `content`
    (bytes) in it, or a directory (cannot be opened: an OSError other than
    "not found"). Each save recorded."""
    from src.core import SettingsManager as settingsModule

    if content is None:
        path.mkdir()
    else:
        path.write_bytes(content)
    monkeypatch.setattr(settingsModule, "SETTINGS_PATH", str(path))
    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    monkeypatch.setattr(WebAccess, "_unknownWarned", False)
    saved = []
    monkeypatch.setattr(SettingsManager, "SaveSettings",
                        lambda: saved.append(json.loads(json.dumps(
                            SettingsManager.settings))))
    return saved


def test_an_unreadable_settings_file_issues_nothing(settings, monkeypatch,
                                                    tmp_path):
    # LoadSettings() could not read settings.json: it looks empty, but the
    # token in it is the one the user's devices hold. Asking for the token
    # reads nothing again (that is a write's) and writes nothing.
    saved = _unreadSettings(monkeypatch, tmp_path / "settings.json",
                            b'{"general": {"webserver_token": "kept-token-0123'
                            b'456789ab"}}')
    generation = WebAccess.TokenGeneration()
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        assert WebAccess.Token() == ""
        assert WebAccess.Token() == ""
    finally:
        logger.remove(sink)
    assert saved == []
    assert SettingsManager.loadFailed is True
    assert WebAccess.TokenGeneration() == generation
    assert not WebAccess.TokenMatches(["", "anything-at-all-123456"])
    # Asked on every request: said once.
    assert sum("access token is unknown" in m for m in messages) == 1


def test_the_token_is_known_once_a_write_has_read_settings_json_again(
        settings, monkeypatch, tmp_path):
    # The launch's failure was passing: the first write (any setting) reads
    # the file again, and the token in it -- the one the devices hold -- is
    # the token from then on. Not kept as "" from before.
    kept = "kept-token-0123456789ab"
    path = tmp_path / "settings.json"
    _unreadSettings(monkeypatch, path, json.dumps(
        {"general": {"webserver_token": kept}}).encode())
    generation = WebAccess.TokenGeneration()
    assert WebAccess.Token() == ""

    SettingsManager.Set("general.profanity_filter", False)

    assert WebAccess.Token() == kept
    assert WebAccess.TokenMatches([kept])
    assert WebAccess.TokenGeneration() == generation


def test_a_new_token_is_issued_once_a_write_moved_a_corrupt_file_aside(
        settings, monkeypatch, tmp_path):
    # The token in a corrupt file is lost with the rest of it (kept in the
    # .bak): the devices need a new one, issued and saved as at a first
    # launch.
    saved = _unreadSettings(monkeypatch, tmp_path / "settings.json",
                            b'{"general": {"webserver_tok')
    generation = WebAccess.TokenGeneration()
    assert WebAccess.Token() == ""

    SettingsManager.Set("general.profanity_filter", False)
    token = WebAccess.Token()

    assert len(token) == 32
    assert saved[-1]["general"]["webserver_token"] == token
    assert WebAccess.TokenGeneration() == generation + 1
    assert list(tmp_path.glob("settings.json.unreadable-*.bak"))


def test_no_token_is_regenerated_over_a_file_that_cannot_be_opened(
        settings, monkeypatch, tmp_path):
    # It could not be saved, and the next launch would read the old one: the
    # devices given it would be refused then.
    saved = _unreadSettings(monkeypatch, tmp_path / "settings.json")
    generation = WebAccess.TokenGeneration()

    assert WebAccess.RegenerateToken() == ""

    assert saved == [] and SettingsManager.settings == {}
    assert WebAccess.TokenGeneration() == generation
    assert WebAccess.Token() == ""


def test_without_a_usable_token_lan_writes_are_refused_until_regenerated(
        server, monkeypatch, tmp_path):
    # A corrupt settings.json: regenerating is the first write, which moves
    # it aside and saves the new token.
    _unreadSettings(monkeypatch, tmp_path / "settings.json", b"{broken")
    client = _client(server, LAN_PEER)
    assert client.post(V1_WRITE, headers={"X-Autona-Token": ""}).status_code == 401
    assert _client(server).post(V1_WRITE).status_code == 200   # this PC: as before
    token = WebAccess.RegenerateToken()
    assert token
    assert client.post(V1_WRITE, headers={"X-Autona-Token": token}).status_code == 200


@pytest.mark.parametrize("stored", ["", "short", 12345, "has spaces in it!!", "ü" * 20])
def test_a_malformed_stored_token_is_not_used_nor_replaced(settings, stored):
    settings.settings = {"general": {"webserver_token": stored}}
    assert WebAccess.Token() == ""
    assert settings.Get(WebAccess.TOKEN_SETTING) == stored
    assert not WebAccess.TokenMatches([str(stored)])
    # Regenerating (API Settings) is what replaces it.
    token = WebAccess.RegenerateToken()
    assert settings.Get(WebAccess.TOKEN_SETTING) == token
    assert WebAccess.Token() == token


def test_a_hand_set_token_of_the_right_shape_is_kept(settings):
    settings.settings = {"general": {"webserver_token": "my-own-long-token_1234"}}
    assert WebAccess.Token() == "my-own-long-token_1234"


def test_any_supplied_value_is_compared_without_raising(settings):
    WebAccess.Token()
    assert not WebAccess.TokenMatches(["ü" * 10, "x" * 10000, ""])
    assert not WebAccess.TokenMatches([])


def test_the_token_is_found_in_all_three_places():
    headers = {"Authorization": "Bearer a", "X-Autona-Token": "b"}
    assert WebAccess.SuppliedTokens(headers, {"token": "c"}, {"token": "d"}) == [
        "a", "b", "c", "d"]
    assert WebAccess.SuppliedTokens({"Authorization": "bearer  a "}, {}) == ["a"]
    assert WebAccess.SuppliedTokens({"Authorization": "Basic a"}, {}) == []
    assert WebAccess.SuppliedTokens({}, {}, "not-a-dict") == []


@pytest.mark.parametrize("addr, loopback", [
    ("127.0.0.1", True), ("127.0.0.2", True), ("::1", True),
    ("::ffff:127.0.0.1", True), (LAN_PEER, False), ("10.0.0.1", False),
    ("fe80::1%eth0", False), ("localhost", False), (None, False), ("", False),
])
def test_loopback(addr, loopback):
    assert WebAccess.IsLoopback(addr) is loopback


@pytest.mark.parametrize("origin, normalized", [
    ("http://127.0.0.1:5107", "http://127.0.0.1:5107"),
    ("HTTP://LocalHost:5107", "http://localhost:5107"),
    ("http://localhost", "http://localhost:80"),
    ("http://[::1]:5107", "http://[::1]:5107"),
    ("https://example.com", "https://example.com:443"),
    ("null", None), ("", None), ("file://", None), ("http://", None),
    ("http://x:notaport", None), (None, None),
])
def test_origins_are_compared_normalized(origin, normalized):
    assert WebAccess.NormalizeOrigin(origin) == normalized


@pytest.mark.parametrize("addr, local", [
    ("127.0.0.1", True), ("::1", True), (LAN_HOST, True), (LAN_HOST_2, True),
    (f"::ffff:{LAN_HOST}", True), (LAN_PEER, False), ("192.168.1.50", False),
    (None, False), ("", False), ("not-an-address", False),
])
def test_this_computer_is_the_loopback_and_its_own_lan_addresses(addr, local):
    assert WebAccess.IsThisComputer(addr, (LAN_HOST, LAN_HOST_2)) is local


@pytest.mark.parametrize("headers, forwarded", [
    ({}, False),
    ({"X-Forwarded-For": "203.0.113.9"}, True),
    ({"Forwarded": "for=203.0.113.9"}, True),
    ({"X-Real-IP": "203.0.113.9"}, True),
    ({"X-Forwarded-For": ""}, True),           # present is enough
])
def test_a_proxy_forwarding_header_is_noticed(headers, forwarded):
    assert WebAccess.IsForwarded(headers) is forwarded


def test_own_origins_are_this_servers_port_on_this_pcs_names():
    own = WebAccess.OwnOrigins("5107", (LAN_HOST, LAN_HOST_2))
    assert own == {
        "http://127.0.0.1:5107", "http://localhost:5107",
        "http://[::1]:5107", f"http://{LAN_HOST}:5107",
        f"http://{LAN_HOST_2}:5107"}


@pytest.mark.parametrize("origin", [
    "http://evil@127.0.0.1:5107", "http://127.0.0.1:5107/", "http://127.0.0.1:5107/x",
    "http://127.0.0.1:5107?x", "http://127.0.0.1:5107#x",
])
def test_an_origin_is_a_bare_scheme_host_and_port(origin):
    assert WebAccess.NormalizeOrigin(origin) is None


# ── HTTP: who may write ──────────────────────────────────────────────────

LEGACY_WRITE = "/scoreboard1-team1-scoreup"
V1_WRITE = "/api/v1/scoreboards/1/score:reset"


def _error(response):
    body = response.get_json()
    assert body["ok"] is False
    return body["error"]["code"]


def test_the_loopback_writes_without_a_token(server):
    client = _client(server)
    assert client.get(LEGACY_WRITE).status_code == 200
    assert client.post(V1_WRITE).status_code == 200
    assert client.get("/playerdb").status_code == 200
    assert server.actions.calls == [
        ("team_scoreup", "1", "1"), ("reset_scores", 1)]


def test_reads_need_nothing_from_the_lan(server):
    client = _client(server, LAN_PEER)
    assert client.get("/program-state").status_code == 200
    assert client.get("/api/v1/games").get_json()["data"] == {
        "ssbu": {"name": "Smash"}}


@pytest.mark.parametrize("method, url", [
    ("GET", LEGACY_WRITE), ("POST", V1_WRITE), ("GET", "/playerdb")])
def test_lan_writes_and_the_player_db_need_the_token(server, method, url):
    client = _client(server, LAN_PEER)
    response = client.open(url, method=method)
    assert response.status_code == 401
    assert _error(response) == "UNAUTHENTICATED"
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert b"A Real Name" not in response.data
    assert server.actions.calls == []


@pytest.mark.parametrize("how", ["bearer", "header", "query"])
def test_the_token_lets_a_lan_write_in(server, how):
    token = WebAccess.Token()
    client = _client(server, LAN_PEER)
    headers, query = {}, ""
    if how == "bearer":
        headers = {"Authorization": f"Bearer {token}"}
    elif how == "header":
        headers = {"X-Autona-Token": token}
    else:
        query = f"?token={token}"
    assert client.get(LEGACY_WRITE + query, headers=headers).status_code == 200
    assert client.post(V1_WRITE + query, headers=headers).status_code == 200
    playerdb = client.get("/playerdb" + query, headers=headers)
    assert playerdb.status_code == 200
    assert "Tag" in playerdb.get_json()
    assert server.actions.calls == [
        ("team_scoreup", "1", "1"), ("reset_scores", 1)]


def test_a_wrong_token_is_refused_and_never_logged(server, warnings):
    WebAccess.Token()
    client = _client(server, LAN_PEER)
    response = client.get(LEGACY_WRITE + "?token=WRONG-TOKEN-VALUE-123456")
    assert response.status_code == 401
    assert "invalid token" in response.get_json()["error"]["message"]
    assert server.actions.calls == []
    assert any("invalid_token" in m for m in warnings)
    assert not any("WRONG-TOKEN-VALUE" in m for m in warnings)


def test_a_regenerated_token_stops_working_at_once(server):
    old = WebAccess.Token()
    WebAccess.RegenerateToken()
    client = _client(server, LAN_PEER)
    assert client.post(V1_WRITE, headers={"X-Autona-Token": old}).status_code == 401


def test_a_preflight_is_not_refused(server):
    response = _client(server, LAN_PEER).options(V1_WRITE, headers={
        "Origin": "http://192.168.1.5:5107",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-autona-token",
    })
    assert response.status_code == 200
    assert server.actions.calls == []


def test_refusals_are_logged_once_per_kind(server, warnings):
    client = _client(server, LAN_PEER)
    for _ in range(5):
        client.get(LEGACY_WRITE)
    refused = [m for m in warnings if "refused GET /scoreboard1-team1-scoreup" in m]
    assert len(refused) == 1
    assert LAN_PEER in refused[0] and "token_required" in refused[0]


# ── HTTP: browser pages on this PC (CSRF) ────────────────────────────────

@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    # Another origin of the same site (a page on another port of this PC):
    # its no-cors GETs (<img src=...>) carry no Origin.
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "same-site", "Origin": "http://localhost"},
    {"Sec-Fetch-Site": "something-new"},
    {"Origin": "https://evil.example"},
    {"Origin": "null"},                       # sandboxed iframe / file:// page
    {"Origin": "http://localhost:3000"},      # another local server
    {"Origin": "http://127.0.0.1:5107"},      # right host, not this port (80)
    {"Origin": "http://evil.example:80"},
    {"Origin": "https://localhost"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "http://localhost"},
])
@pytest.mark.parametrize("method, url", [
    ("GET", LEGACY_WRITE), ("HEAD", LEGACY_WRITE), ("POST", V1_WRITE),
    ("GET", "/playerdb")])
def test_foreign_pages_cannot_write_through_the_loopback(
        server, headers, method, url):
    response = _client(server).open(url, method=method, headers=headers)
    assert response.status_code == 403
    if method != "HEAD":
        assert _error(response) == "FORBIDDEN"
    assert server.actions.calls == []


@pytest.mark.parametrize("headers", [
    {},                                                      # curl / Companion
    {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
    {"Sec-Fetch-Site": "none"},                              # typed in the address bar
    {"Origin": "http://127.0.0.1"},
    {"Origin": "http://localhost:80"},
    {"Host": "127.0.0.1", "Origin": "http://127.0.0.1"},
    {"Host": "[::1]:80"},
])
def test_this_servers_own_pages_write_through_the_loopback(server, headers):
    assert _client(server).post(V1_WRITE, headers=headers).status_code == 200


# ── HTTP: this PC under its LAN address ──────────────────────────────────

@pytest.mark.parametrize("local", [LAN_HOST, LAN_HOST_2])
def test_this_pc_under_its_lan_address_needs_no_token(server, local):
    # A browser / Companion here opening the LAN-IP URL: the request comes
    # from this PC's own LAN address, to this server under that address.
    client = _client(server, local)
    for headers in ({"Host": local},
                    {"Host": local, "Origin": f"http://{local}",
                     "Sec-Fetch-Site": "same-origin"}):
        assert client.post(V1_WRITE, headers=headers).status_code == 200
    assert client.get(LEGACY_WRITE, headers={"Host": local}).status_code == 200
    assert client.get("/playerdb", headers={"Host": local}).status_code == 200


def test_this_pc_under_its_lan_address_is_held_to_the_same_checks(server):
    client = _client(server, LAN_HOST)
    cases = [
        ({"Host": LAN_HOST, "Origin": "https://evil.example"}, 403),
        ({"Host": LAN_HOST, "Sec-Fetch-Site": "cross-site"}, 403),
        ({"Host": LAN_HOST, "Sec-Fetch-Site": "same-site"}, 403),
        ({"Host": "evil.example"}, 401),                 # DNS rebinding
        ({"Host": LAN_HOST, "X-Forwarded-For": "203.0.113.9"}, 401),
    ]
    for headers, status in cases:
        assert client.post(V1_WRITE, headers=headers).status_code == status, headers
    assert server.actions.calls == []


def test_another_lan_address_is_not_this_pc(server):
    # Only the addresses looked up for this PC count; a peer stays a peer
    # even when it addresses the server correctly.
    response = _client(server, LAN_PEER).post(
        V1_WRITE, headers={"Host": LAN_HOST, "Origin": f"http://{LAN_HOST}"})
    assert response.status_code == 401


# ── HTTP: DNS rebinding and proxies ──────────────────────────────────────

REBOUND = [
    {"Host": "evil.example:5107"},
    {"Host": "evil.example"},          # the test server's port (80)
    # The rebound page's own request: same-origin under the attacker's name.
    {"Host": "evil.example", "Origin": "http://evil.example",
     "Sec-Fetch-Site": "same-origin"},
    {"Host": "localhost.:80"},
    {"Host": "127.0.0.1:5107"},        # a loopback name, not this port
]


@pytest.mark.parametrize("headers", REBOUND)
@pytest.mark.parametrize("method, url", [
    ("GET", LEGACY_WRITE), ("POST", V1_WRITE), ("GET", "/playerdb")])
def test_a_rebound_host_name_gets_no_loopback_exemption(
        server, headers, method, url):
    response = _client(server).open(url, method=method, headers=headers)
    assert response.status_code == 401
    assert _error(response) == "UNAUTHENTICATED"
    assert b"A Real Name" not in response.data
    assert server.actions.calls == []


def test_a_rebound_host_name_still_reads(server):
    response = _client(server).get(
        "/program-state", headers={"Host": "evil.example"})
    assert response.status_code == 200


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_a_request_through_a_local_proxy_is_not_from_this_pc(server, header):
    # A reverse proxy or tunnel on this PC relays other computers' requests
    # from the loopback; it says so with a forwarding header.
    value = "for=203.0.113.9" if header == "Forwarded" else "203.0.113.9"
    client = _client(server)
    response = client.post(V1_WRITE, headers={header: value})
    assert response.status_code == 401
    assert _error(response) == "UNAUTHENTICATED"
    assert client.get("/playerdb", headers={header: value}).status_code == 401
    assert server.actions.calls == []
    # With the token it is let in, like any other computer.
    token = WebAccess.Token()
    assert client.post(V1_WRITE, headers={
        header: value, "X-Autona-Token": token}).status_code == 200


def test_head_on_a_legacy_write_is_guarded_like_get(server):
    lan = _client(server, LAN_PEER)
    assert lan.head(LEGACY_WRITE).status_code == 401
    assert server.actions.calls == []
    token = WebAccess.Token()
    assert lan.head(LEGACY_WRITE, headers={"X-Autona-Token": token}).status_code == 200
    assert _client(server).head(LEGACY_WRITE).status_code == 200


def test_the_token_is_not_a_forgery_from_any_page(server):
    # A page that sends the token got it from the user (it is never sent on
    # its own), so neither Sec-Fetch-Site nor Origin is held against it --
    # the Web UI under another address of this PC, the user's own dashboard.
    token = WebAccess.Token()
    for remote in ("127.0.0.1", LAN_PEER):
        response = _client(server, remote).post(V1_WRITE, headers={
            "X-Autona-Token": token,
            "Sec-Fetch-Site": "cross-site",
            "Origin": "https://dashboard.example",
        })
        assert response.status_code == 200, remote


def test_reads_are_not_held_against_foreign_pages(server):
    response = _client(server).get("/program-state", headers={
        "Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"})
    assert response.status_code == 200


# ── React panels: /web/<panel> (ADR-005 S4a) ─────────────────────────────
# The desktop's embedded view (AutonaWebPanelHost) and a browser on another
# computer open the same page. The page is a file like /web; what it does
# afterwards goes through the API and is held to the rules above.

WEB_UI_PAGE = b"<html>the Web UI</html>"
# The URL the desktop's embedded view opens (AutonaWebPanelHost).
PANEL_PAGE = "/web/shell-check?desktop=1&lng=ja"


def _panel_page_headers(page="http://localhost" + PANEL_PAGE):
    # A fetch from that page, as Chromium sends it to the test server
    # (http://localhost, port 80).
    return {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin",
            "Referer": page}


def test_every_allowed_panel_is_the_web_ui_page(server):
    _write(server.root, "web/build/index.html", WEB_UI_PAGE)
    assert WebAccess.WEB_PANELS, "no React panel to serve"
    for panel in sorted(WebAccess.WEB_PANELS):
        for remote in ("127.0.0.1", LAN_PEER):
            response = _client(server, remote).get(f"/web/{panel}")
            assert response.status_code == 200, (panel, remote)
            assert response.data == WEB_UI_PAGE
            response.close()


def test_the_panel_page_needs_no_token_from_another_computer(server):
    # A page is a read: OBS-style, nothing is asked of whoever loads it, and
    # a token that comes along (?token=, from the API settings window's
    # "open in browser") is not refused either.
    _write(server.root, "web/build/index.html", WEB_UI_PAGE)
    lan = _client(server, LAN_PEER)
    for url in ("/web/shell-check", PANEL_PAGE,
                f"/web/shell-check?token={WebAccess.Token()}",
                "/web/shell-check?token=WRONG-TOKEN-VALUE-123456"):
        response = lan.get(url)
        assert response.status_code == 200, url
        assert response.data == WEB_UI_PAGE
        response.close()


@pytest.mark.parametrize("panel", [
    "unknown",
    "designer",          # the Designer is /web?view=designer, not a panel
    "build",             # /web/build is the Web UI's folder, not a page
    "Shell-Check",       # names are exact
    "shell-check.html",
    "settings.json",
])
def test_any_other_panel_name_is_not_found(server, panel):
    _write(server.root, "web/build/index.html", WEB_UI_PAGE)
    _write(server.root, "web/build/settings.json", b'{"secret": 1}')
    for remote in ("127.0.0.1", LAN_PEER):
        response = _client(server, remote).get(f"/web/{panel}")
        assert response.status_code == 404, (panel, remote)
        assert WEB_UI_PAGE not in response.data
        assert b"secret" not in response.data


def test_a_refused_panel_name_is_logged_like_a_refused_file(server, warnings):
    # As file_request logged these URLs before /web/<panel> took them: one
    # warning per name, however often it is asked for.
    _write(server.root, "web/build/index.html", WEB_UI_PAGE)
    client = _client(server, LAN_PEER)
    for _ in range(3):
        assert client.get("/web/unknown").status_code == 404
        assert client.get("/web/shell-check").status_code == 200

    refused = [m for m in warnings if "not serving" in m]
    assert sum("'web/unknown'" in m for m in refused) == 1
    assert not any("shell-check" in m for m in refused)


def test_the_web_ui_entry_and_the_designer_are_unchanged(server):
    _write(server.root, "web/build/index.html", WEB_UI_PAGE)
    _write(server.root, "web/build/static/js/main.js", b"js")
    client = _client(server)
    for url in ("/", "/web", "/scoreboard", "/team",
                "/web?view=designer&desktop=1&token=x"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.data == WEB_UI_PAGE
        response.close()
    # The page's own files are still the static route's.
    response = client.get("/web/build/static/js/main.js")
    assert response.status_code == 200
    assert response.data == b"js"
    response.close()


def test_the_panel_page_writes_from_this_pc_and_needs_the_token_from_another(
        server):
    headers = _panel_page_headers()
    # The desktop's embedded view: this PC, this server's own page.
    assert _client(server).post(V1_WRITE, headers=headers).status_code == 200
    assert _client(server).get(LEGACY_WRITE, headers=headers).status_code == 200
    # The same page opened on another computer: the token, as everywhere.
    lan = _client(server, LAN_PEER)
    lan_headers = _panel_page_headers(f"http://{LAN_HOST}{PANEL_PAGE}")
    lan_headers["Origin"] = f"http://{LAN_HOST}"
    lan_headers["Host"] = LAN_HOST
    response = lan.post(V1_WRITE, headers=lan_headers)
    assert response.status_code == 401
    assert _error(response) == "UNAUTHENTICATED"
    assert lan.get("/playerdb", headers=lan_headers).status_code == 401
    lan_headers["X-Autona-Token"] = WebAccess.Token()
    assert lan.post(V1_WRITE, headers=lan_headers).status_code == 200
    assert server.actions.calls == [
        ("reset_scores", 1), ("team_scoreup", "1", "1"), ("reset_scores", 1)]


def test_the_web_ui_routes_the_same_panels():
    # web/src/PanelShell/registry.tsx maps each panel name to its page; a
    # name the server serves but the Web UI does not route (or the other
    # way round) is a page that never works.
    import re
    from pathlib import Path

    registry = (Path(__file__).resolve().parent.parent
                / "web" / "src" / "PanelShell" / "registry.tsx").read_text(
                    encoding="utf-8")
    body = re.search(r"export const PANELS[^=]*=\s*\{(.*?)\n\};", registry,
                     re.S)
    assert body, "PANELS not found in registry.tsx"
    routed = set(re.findall(r'^\s*"([^"]+)"\s*:', body.group(1), re.M))
    assert routed == set(WebAccess.WEB_PANELS)


# ── Socket.IO ────────────────────────────────────────────────────────────

def _ws(server, remote_addr="127.0.0.1", headers=None, auth=None, query=None):
    flask_client = _client(server, remote_addr)
    return server.socketio.test_client(
        server.app, flask_test_client=flask_client, headers=headers,
        auth=auth, query_string=query)


def _names(ws):
    return [packet["name"] for packet in ws.get_received()]


def _scoreup(ws):
    return ws.emit("team_scoreup", json.dumps(
        {"scoreboardNumber": "1", "team": "1"}), callback=True)


def test_every_connection_receives_the_state(server):
    for ws in (_ws(server), _ws(server, LAN_PEER),
               _ws(server, headers={"Origin": "null"})):
        assert ws.is_connected()
        assert "program_state" in _names(ws)
        # And can ask again (a read).
        ws.emit("program_state", {})
        assert _names(ws) == ["program_state"]


def test_a_loopback_connection_writes(server):
    ws = _ws(server)
    ws.get_received()
    assert _scoreup(ws) == []
    assert server.actions.calls == [("team_scoreup", "1", "1")]
    assert _names(ws) == ["team_scoreup"]


def test_a_lan_connection_without_the_token_is_read_only(server, warnings):
    ws = _ws(server, LAN_PEER)
    ws.get_received()
    ack = _scoreup(ws)
    assert ack["ok"] is False
    assert ack["error"]["code"] == "UNAUTHENTICATED"
    assert server.actions.calls == []
    assert _names(ws) == []
    assert ws.emit("playerdb", {}, callback=True)["error"]["code"] == (
        "UNAUTHENTICATED")
    assert _names(ws) == []
    assert any("Socket.IO event 'team_scoreup'" in m for m in warnings)


@pytest.mark.parametrize("how", ["auth", "query", "header"])
def test_the_token_lets_a_lan_connection_write(server, how):
    token = WebAccess.Token()
    kwargs = {"auth": {"token": token}} if how == "auth" else (
        {"query": f"token={token}"} if how == "query"
        else {"headers": {"X-Autona-Token": token}})
    ws = _ws(server, LAN_PEER, **kwargs)
    assert _scoreup(ws) == []
    assert server.actions.calls == [("team_scoreup", "1", "1")]
    ws.get_received()
    ws.emit("playerdb", {})
    assert _names(ws) == ["playerdb"]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://absolute"},           # OBS's local-file browser source
    {"Sec-Fetch-Site": "cross-site"},
])
def test_a_foreign_page_connects_but_cannot_write(server, headers):
    ws = _ws(server, headers=headers)
    assert "program_state" in _names(ws)
    ack = _scoreup(ws)
    assert ack["error"]["code"] == "FORBIDDEN"
    assert server.actions.calls == []


def test_this_servers_own_page_writes(server):
    ws = _ws(server, headers={"Origin": "http://localhost"})
    assert _scoreup(ws) == []
    assert server.actions.calls == [("team_scoreup", "1", "1")]


def test_this_pcs_page_under_its_lan_address_writes(server):
    ws = _ws(server, LAN_HOST, headers={
        "Host": LAN_HOST, "Origin": f"http://{LAN_HOST}"})
    assert _scoreup(ws) == []
    assert server.actions.calls == [("team_scoreup", "1", "1")]


@pytest.mark.parametrize("remote, headers", [
    ("127.0.0.1", {"Host": "evil.example:5107"}),
    ("127.0.0.1", {"Host": "evil.example", "Origin": "http://evil.example",
                   "Sec-Fetch-Site": "same-origin"}),
    ("127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-site"}),
    (LAN_HOST, {"Host": "evil.example"}),
])
def test_a_rebound_or_relayed_connection_is_read_only(server, remote, headers):
    ws = _ws(server, remote, headers=headers)
    assert "program_state" in _names(ws)
    assert _scoreup(ws)["ok"] is False
    assert ws.emit("playerdb", {}, callback=True)["ok"] is False
    assert server.actions.calls == []


def test_a_regenerated_token_cuts_off_its_connections(server):
    token = WebAccess.Token()
    lan = _ws(server, LAN_PEER, auth={"token": token})
    local = _ws(server)
    WebAccess.RegenerateToken()
    assert _scoreup(lan)["error"]["code"] == "UNAUTHENTICATED"
    assert _scoreup(local) == []           # no token involved: unaffected
    assert server.actions.calls == [("team_scoreup", "1", "1")]


def test_this_pcs_own_page_with_the_token_outlives_a_regenerate(server):
    # Let in without the token (it qualifies), so regenerating the token it
    # also sent does not cut it off -- nor its player DB broadcast.
    from src.AutonaWebServer import WebServer

    token = WebAccess.Token()
    ws = _ws(server, auth={"token": token}, headers={"Origin": "http://localhost"})
    WebAccess.RegenerateToken()
    assert _scoreup(ws) == []
    assert server.actions.calls == [("team_scoreup", "1", "1")]
    ws.get_received()
    WebServer.ws_playerdb(None)
    assert _names(ws) == ["playerdb"]


def test_a_foreign_page_with_the_token_is_cut_off_by_a_regenerate(server):
    # The token was all that let it in.
    token = WebAccess.Token()
    ws = _ws(server, auth={"token": token},
             headers={"Origin": "https://dashboard.example"})
    assert _scoreup(ws) == []
    WebAccess.RegenerateToken()
    assert _scoreup(ws)["error"]["code"] == "UNAUTHENTICATED"


class _Polling:
    """A Socket.IO client over the real Engine.IO polling transport, through
    the Flask test client (the in-process test_client() above skips the
    transport, and with it the handshake request the server reads)."""

    PATH = "/socket.io/?EIO=4&transport=polling"

    def __init__(self, server, remote_addr="127.0.0.1", headers=None,
                 client=None):
        # `client`: another way in (the preview listener's, SF-8a).
        self.client = client or _client(server, remote_addr)
        self.headers = dict(headers or {})
        opened = self.client.get(self.PATH, headers=self.headers)
        assert opened.status_code == 200, opened.data
        self.sid = json.loads(opened.get_data(as_text=True)[1:])["sid"]
        self.url = f"{self.PATH}&sid={self.sid}"
        self._ack = 0
        # The Socket.IO session's id, once connected (its rooms are kept
        # under it).
        self.session = None

    def send(self, packet):
        response = self.client.post(self.url, data=packet, headers=self.headers)
        assert response.get_data(as_text=True).lower() == "ok"

    def receive(self):
        body = self.client.get(self.url, headers=self.headers).get_data(as_text=True)
        return body.split("\x1e")

    def connect(self, auth=None):
        self.send("40" + (json.dumps(auth) if auth else ""))
        packets = self.receive()
        for packet in packets:
            if packet.startswith('40{"sid"'):
                self.session = json.loads(packet[2:])["sid"]
        return packets

    def emit(self, event, data):
        self._ack += 1
        self.send(f"42{self._ack}" + json.dumps([event, data]))
        for packet in self.receive():
            if packet.startswith(f"43{self._ack}"):
                args = json.loads(packet[len(f"43{self._ack}"):])
                return args[0] if args else []   # a handler that returned None
        raise AssertionError("no ack")


@pytest.fixture
def polling(server, monkeypatch):
    # Handle each packet inside the request that carries it, so a poll
    # right after finds the answer queued (no background threads).
    monkeypatch.setattr(server.socketio.server, "async_handlers", False)
    monkeypatch.setattr(server.socketio.server.eio, "async_handlers", False)
    return server


SCOREUP = json.dumps({"scoreboardNumber": "1", "team": "1"})


def test_a_polling_connection_from_this_pc_writes(polling):
    ws = _Polling(polling, headers={"Origin": "http://localhost"})
    packets = ws.connect()
    assert any(p.startswith('40{"sid"') for p in packets)
    assert any(p.startswith('42["program_state"') for p in packets)
    assert ws.emit("team_scoreup", SCOREUP) == []
    assert polling.actions.calls == [("team_scoreup", "1", "1")]


@pytest.mark.parametrize("remote, headers, code", [
    ("127.0.0.1", {"Host": "evil.example:5107"}, "UNAUTHENTICATED"),
    ("127.0.0.1", {"Host": "evil.example", "Origin": "http://evil.example",
                   "Sec-Fetch-Site": "same-origin"}, "UNAUTHENTICATED"),
    ("127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}, "UNAUTHENTICATED"),
    ("127.0.0.1", {"Origin": "https://evil.example"}, "FORBIDDEN"),
    (LAN_PEER, {}, "UNAUTHENTICATED"),
])
def test_a_polling_handshake_decides_what_the_connection_may_do(
        polling, remote, headers, code):
    # The verdict is taken from the handshake request, which carries the
    # Host / Origin / forwarding headers -- not from the later packets.
    ws = _Polling(polling, remote, headers)
    packets = ws.connect()
    assert any(p.startswith('42["program_state"') for p in packets)   # reads
    assert ws.emit("team_scoreup", SCOREUP)["error"]["code"] == code
    assert ws.emit("playerdb", {})["error"]["code"] == code
    assert polling.actions.calls == []


def test_a_polling_connection_with_the_token_writes_from_the_lan(polling):
    ws = _Polling(polling, LAN_PEER)
    ws.connect(auth={"token": WebAccess.Token()})
    assert ws.emit("team_scoreup", SCOREUP) == []
    assert polling.actions.calls == [("team_scoreup", "1", "1")]


def _guarded_events():
    return sorted(event for event, access in WebAccess.EVENT_ACCESS.items()
                  if access in WebAccess.GUARDED)


@pytest.mark.parametrize("event", _guarded_events())
def test_every_guarded_event_is_refused_on_a_read_only_connection(server, event):
    # Whatever the payload: the guard answers before the handler runs, so a
    # handler registered without it (socketio.on instead of ws_on) shows up
    # here as a missing or different ack.
    ws = _ws(server, LAN_PEER)
    ws.get_received()
    ack = ws.emit(event, json.dumps({"scoreboardNumber": "1"}), callback=True)
    assert ack == {"ok": False, "error": {
        "code": "UNAUTHENTICATED",
        "message": ("a token is required for this request from another "
                    "computer (see API Settings in Autona)")}}
    assert server.actions.calls == []
    assert _names(ws) == []


def test_socketio_handlers_are_registered_only_through_ws_on():
    # Structural half of the pin above: in the web server module, the one
    # place that calls socketio.on is ws_on's register().
    import ast
    import inspect

    from src import AutonaWebServer

    tree = ast.parse(inspect.getsource(AutonaWebServer))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing_functions(node):
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
        return names

    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr in ("on", "on_event", "event")
             and isinstance(node.func.value, (ast.Name, ast.Attribute))
             and (getattr(node.func.value, "id", None) == "socketio"
                  or getattr(node.func.value, "attr", None) == "socketio")]
    assert calls, "socketio.on is no longer called at all?"
    for call in calls:
        assert enclosing_functions(call)[:2] == ["register", "ws_on"], (
            f"line {call.lineno}: socketio.{call.func.attr}() outside ws_on")


def test_the_player_db_broadcast_reaches_only_trusted_connections(server):
    from src.AutonaWebServer import WebServer

    token = WebAccess.Token()
    local = _ws(server)
    with_token = _ws(server, LAN_PEER, auth={"token": token})
    without = _ws(server, LAN_PEER)
    foreign = _ws(server, headers={"Origin": "https://evil.example"})
    for ws in (local, with_token, without, foreign):
        ws.get_received()

    # What AutonaPlayerDB.signals.db_updated runs: outside any request.
    WebServer.ws_playerdb(None)

    assert _names(local) == ["playerdb"]
    assert _names(with_token) == ["playerdb"]
    assert _names(without) == []
    assert _names(foreign) == []

    # After a regenerate, the old token's connections stop receiving it.
    WebAccess.RegenerateToken()
    WebServer.ws_playerdb(None)
    assert _names(local) == ["playerdb"]
    assert _names(with_token) == []


def test_the_pending_notice_reaches_only_trusted_connections(server):
    # The same rooms as the GET it tells them to read (ADR-005 S4u): a client
    # that could not read it has no use for it, and when the board is edited
    # is the operator's own.
    from src.AutonaWebServer import WebServer

    token = WebAccess.Token()
    local = _ws(server)
    with_token = _ws(server, LAN_PEER, auth={"token": token})
    without = _ws(server, LAN_PEER)
    foreign = _ws(server, headers={"Origin": "https://evil.example"})
    for ws in (local, with_token, without, foreign):
        ws.get_received()

    # What the flusher runs: outside any request.
    WebServer._send_held_notices({1})

    for ws in (local, with_token):
        assert [(packet["name"], packet["args"]) for packet in ws.get_received()] == [
            ("scoreboard_pending", [{"board": 1, "dirty": False}])]
    assert _names(without) == []
    assert _names(foreign) == []

    # After a regenerate, the old token's connections stop receiving it.
    WebAccess.RegenerateToken()
    WebServer._send_held_notices({1})
    assert _names(local) == ["scoreboard_pending"]
    assert _names(with_token) == []


def test_the_follow_notice_reaches_whoever_can_read_it(server):
    # GET /scoreboards/{n}/follow is a READ (ADR-005 S4v), and the notice names
    # no more than a board: like the state's deltas, with no room.
    from src.AutonaWebServer import WebServer

    local = _ws(server)
    with_token = _ws(server, LAN_PEER, auth={"token": WebAccess.Token()})
    without = _ws(server, LAN_PEER)
    for ws in (local, with_token, without):
        ws.get_received()

    # What the flusher runs: outside any request.
    WebServer._send_follow_notices({1})

    for ws in (local, with_token, without):
        assert [(packet["name"], packet["args"]) for packet in ws.get_received()] == [
            ("scoreboard_follow", [{"board": 1}])]


def test_the_battle_format_notice_reaches_whoever_can_read_it(server):
    # GET /battle-format is a READ (ADR-005 S4x), and the notice carries
    # nothing (S4z): with no room, as the follow's.
    from src.AutonaWebServer import WebServer

    local = _ws(server)
    with_token = _ws(server, LAN_PEER, auth={"token": WebAccess.Token()})
    without = _ws(server, LAN_PEER)
    for ws in (local, with_token, without):
        ws.get_received()

    # What the flusher runs: outside any request.
    WebServer._send_battle_format_notice(True)
    WebServer._send_battle_format_notice(False)

    for ws in (local, with_token, without):
        assert [(packet["name"], packet["args"]) for packet in ws.get_received()] == [
            ("battle_format", [{}])]


def test_the_games_notice_reaches_whoever_can_read_it(server):
    # GET /games is a READ, and the notice carries nothing (ADR-005 S-final
    # SF-3a): with no room, as the battle format's.
    from src.AutonaWebServer import WebServer

    local = _ws(server)
    with_token = _ws(server, LAN_PEER, auth={"token": WebAccess.Token()})
    without = _ws(server, LAN_PEER)
    for ws in (local, with_token, without):
        ws.get_received()

    # What the flusher runs: outside any request.
    WebServer._send_games_notice(True)
    WebServer._send_games_notice(False)

    for ws in (local, with_token, without):
        assert [(packet["name"], packet["args"]) for packet in ws.get_received()] == [
            ("games_changed", [{}])]


# ── The preview listener (ADR-005 S-final SF-8a) ─────────────────────────
# The layout previews load from a second port, read-only: what a layout
# reads (layout/, assets/, the user_data pictures, out/program_state.json,
# the /api/v1 READ routes, the state over Socket.IO), nothing else, token or
# not. Its requests are refused before Authorize: its SERVER_PORT would make
# its own pages this server's (OwnOrigins).

PREVIEW_PORT = 52345
PREVIEW_ORIGIN = f"http://localhost:{PREVIEW_PORT}"
# The static samples: what the preview listener serves, and what it does not
# (served on this server's own port all the same).
PREVIEW_FILES = (
    "layout/scoreboard/index.html",
    "assets/icons/mic.svg",
    "user_data/games/ssbu/base_files/icon.png",
    "user_data/team_logo/team.png",
    "user_data/tournament_banner.png",
    "out/program_state.json",
)
NOT_PREVIEW_FILES = (
    "web/build/index.html",
    "out/score.txt",
    "out/thumbnails/thumb.png",
    "user_data/settings.json",
    "nowhere/at/all.txt",
)


class _PreviewClient:
    """Requests through PreviewGate, as the preview listener takes them
    (the real Flask app behind it, Socket.IO middleware included)."""

    def __init__(self, server, remote_addr="127.0.0.1"):
        from werkzeug.test import Client

        from src.AutonaWebServer import PreviewGate

        self._client = Client(PreviewGate(server.app))
        self._environ = {"REMOTE_ADDR": remote_addr}

    def open(self, *args, **kwargs):
        kwargs.setdefault("environ_base", dict(self._environ))
        return self._client.open(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.open(*args, method="GET", **kwargs)

    def post(self, *args, **kwargs):
        return self.open(*args, method="POST", **kwargs)


def _layout_reads(endpoint, method, filename):
    """What the preview listener serves, as the spec says it (not through
    WebAccess.PreviewServes)."""
    if method not in ("GET", "HEAD", "OPTIONS"):
        return False
    access = WebAccess.RouteAccess(endpoint)
    if access == WebAccess.STATIC:
        return filename in PREVIEW_FILES
    return endpoint.startswith("api_v1.") and access == WebAccess.READ


def _rule_requests(app):
    """(endpoint, method, path, filename) for every route and method of the
    app; a filename route once per static sample."""
    adapter = app.url_map.bind("localhost")
    for rule in app.url_map.iter_rules():
        filenames = ((PREVIEW_FILES + NOT_PREVIEW_FILES)
                     if "filename" in rule.arguments else (None,))
        for filename in filenames:
            values = {argument: "1" for argument in rule.arguments}
            if filename is not None:
                values["filename"] = filename
            for method in sorted(rule.methods):
                yield (rule.endpoint, method,
                       adapter.build(rule.endpoint, values, method=method),
                       filename)


def test_every_route_on_the_preview_listener_serves_only_a_layouts_reads(
        server):
    from flask import request

    from src.AutonaWebServer import PreviewGate, WebServer

    refusal = WebAccess.ErrorBody(WebAccess.PREVIEW_ONLY)
    served, refused = set(), set()
    for endpoint, method, path, filename in _rule_requests(server.app):
        with server.app.test_request_context(
                path, method=method,
                environ_overrides={PreviewGate.ENVIRON_KEY: True}):
            # Two routes of one URL (a GET and a DELETE) share its OPTIONS:
            # judged as the one Flask routes it to.
            routed = request.endpoint
            assert routed == endpoint or method == "OPTIONS", path
            response = WebServer._guard_http_request()
            if _layout_reads(routed, method, filename):
                assert response is None, (method, path)
                served.add(WebAccess.RouteAccess(routed))
            else:
                assert response is not None, (method, path)
                assert response.status_code == 403, (method, path)
                assert response.get_json() == refusal, (method, path)
                refused.add(WebAccess.RouteAccess(routed))
    # Every kind was seen: the reads served, and every guarded / Designer /
    # write kind refused (the static kind both ways).
    assert served == {WebAccess.READ, WebAccess.STATIC}
    assert refused == {WebAccess.READ, WebAccess.WRITE, WebAccess.PLAYERDB,
                       WebAccess.THIS_PC, WebAccess.SHELL, WebAccess.DESIGNER,
                       WebAccess.STATIC}


def test_every_row_of_the_route_ledger_is_judged_for_the_preview():
    # The rule on WebAccess's side, from the ledger itself: no second table.
    methods = ("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE")
    for endpoint, access in WebAccess.ROUTE_ACCESS.items():
        for method in methods:
            filenames = ((PREVIEW_FILES + NOT_PREVIEW_FILES + (None,))
                         if access == WebAccess.STATIC else (None,))
            for filename in filenames:
                expected = _layout_reads(endpoint, method, filename)
                assert WebAccess.PreviewServes(
                    endpoint, method, filename) is expected, (
                    endpoint, method, filename)
                if access in WebAccess.GUARDED or access == WebAccess.DESIGNER:
                    assert expected is False
    # An endpoint nobody classified, and no endpoint at all (404 / 405).
    assert WebAccess.PreviewServes("api_v1.no_such_route", "GET") is False
    assert WebAccess.PreviewServes(None, "GET") is False


def test_the_preview_listener_serves_what_a_layout_reads(server):
    for relative in PREVIEW_FILES + NOT_PREVIEW_FILES:
        _write(server.root, relative, b"a file")
    client = _PreviewClient(server)

    for relative in PREVIEW_FILES:
        response = client.get("/" + relative)
        assert response.status_code == 200, relative
        assert response.get_data() == b"a file"
    assert client.get("/api/v1/state").get_json()["ok"] is True
    assert client.get("/api/v1/games").get_json()["data"] == {
        "ssbu": {"name": "Smash"}}
    # The same from another computer: the reads need nothing there either.
    assert _PreviewClient(server, LAN_PEER).get(
        "/layout/scoreboard/index.html").status_code == 200


@pytest.mark.parametrize("headers", [
    {},
    # The preview's own page, as this server's own would be.
    {"Host": f"localhost:{PREVIEW_PORT}", "Origin": PREVIEW_ORIGIN,
     "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://localhost"},
    {"X-Autona-Token": "TOKEN"},
    {"Authorization": "Bearer TOKEN"},
])
@pytest.mark.parametrize("method, url", [
    ("GET", LEGACY_WRITE), ("POST", V1_WRITE), ("OPTIONS", V1_WRITE),
    ("GET", "/playerdb"), ("GET", "/api/v1/players"),
    ("GET", "/api/v1/connection"), ("GET", "/web"), ("GET", "/web/board"),
    ("GET", "/api/designer/projects"), ("POST", "/api/designer/imports"),
    # Reads too, outside a layout's: the legacy routes, the Web UI's files,
    # the rest of out/, an unmatched URL.
    ("GET", "/program-state"), ("GET", "/web/build/index.html"),
    ("GET", "/out/score.txt"), ("GET", "/no/such/route"),
])
def test_the_preview_listener_refuses_everything_else_token_or_not(
        server, headers, method, url):
    for relative in NOT_PREVIEW_FILES:
        _write(server.root, relative, b"a file")
    headers = {key: value.replace("TOKEN", WebAccess.Token())
               for key, value in headers.items()}
    response = _PreviewClient(server).open(url, method=method, headers=headers)
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.PREVIEW_ONLY)
    assert b"A Real Name" not in response.get_data()
    assert server.actions.calls == []
    # And with the token in the query.
    response = _PreviewClient(server).open(
        url, method=method, query_string={"token": WebAccess.Token()})
    assert response.status_code == 403
    assert server.actions.calls == []


def test_a_preview_refusal_is_logged_once(server, warnings):
    client = _PreviewClient(server)
    client.get(LEGACY_WRITE)
    client.get(LEGACY_WRITE)
    logged = [m for m in warnings if "on the preview listener" in m]
    assert len(logged) == 1
    assert "preview_origin" in logged[0]


def test_a_preview_connection_reads_and_writes_nothing(polling):
    from src.AutonaWebServer import WebServer

    ws = _Polling(polling, client=_PreviewClient(polling),
                  headers={"Origin": PREVIEW_ORIGIN})
    packets = ws.connect(auth={"token": WebAccess.Token()})
    assert any(p.startswith('42["program_state"') for p in packets)   # reads
    assert ws.emit("program_state", {}) == []
    refusal = WebAccess.ErrorBody(WebAccess.PREVIEW_ONLY)
    for event in _guarded_events():
        assert ws.emit(event, json.dumps({"scoreboardNumber": "1"})) == (
            refusal), event
    assert polling.actions.calls == []
    # No trusted room: neither the player DB nor the pending notices.
    rooms = polling.socketio.server.manager.get_rooms(ws.session, "/")
    assert WebServer.WS_AMBIENT_ROOM not in rooms
    assert not any(str(room).startswith("autona:token:") for room in rooms)


def test_a_preview_connection_from_another_computer_is_read_only_too(polling):
    ws = _Polling(polling, LAN_PEER, client=_PreviewClient(polling, LAN_PEER))
    ws.connect(auth={"token": WebAccess.Token()})
    assert ws.emit("team_scoreup", SCOREUP) == WebAccess.ErrorBody(
        WebAccess.PREVIEW_ONLY)
    assert polling.actions.calls == []


@pytest.mark.parametrize("headers", [
    {"Origin": PREVIEW_ORIGIN},
    {"Origin": PREVIEW_ORIGIN, "Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "same-site"},     # a no-cors GET carries no Origin
])
@pytest.mark.parametrize("method, url", [
    ("GET", LEGACY_WRITE), ("POST", V1_WRITE), ("GET", "/playerdb"),
    ("GET", "/api/v1/connection")])
def test_a_preview_page_cannot_write_to_this_server(server, headers, method, url):
    # A layout's script on the preview origin asking this server's own port:
    # another origin of the same site, so no write without the token.
    response = _client(server).open(url, method=method, headers=headers)
    assert response.status_code == 403
    assert _error(response) == "FORBIDDEN"
    assert server.actions.calls == []
    # Its reads are what any page may read.
    assert _client(server).get(
        "/api/v1/state", headers=headers).status_code == 200


def test_a_preview_pages_connection_to_this_server_is_read_only(server):
    ws = _ws(server, headers={"Origin": PREVIEW_ORIGIN,
                              "Sec-Fetch-Site": "same-site"})
    assert "program_state" in _names(ws)
    assert _scoreup(ws)["error"]["code"] == "FORBIDDEN"
    assert server.actions.calls == []


def test_the_designer_guards_refuse_the_preview_listener(server, monkeypatch):
    from werkzeug.exceptions import Forbidden

    from src.AutonaWebServer import (AutonaDesignerService, PreviewGate,
                                     WebServer)

    # Even what the Designer's token would let in.
    monkeypatch.setattr(AutonaDesignerService, "is_authorized",
                        staticmethod(lambda token, remote: True))
    preview = {PreviewGate.ENVIRON_KEY: True}
    with server.app.test_request_context("/api/designer/projects",
                                         environ_overrides=preview):
        with pytest.raises(Forbidden):
            WebServer._authorize_designer_request()
    with server.app.test_request_context(
            "/api/designer/imports", method="POST", environ_overrides=preview,
            headers={"Origin": "https://www.figma.com"}):
        response, status = WebServer._authorize_designer_import_request()
        assert status == 403
    # The Socket.IO verdict and Authorize itself, asked directly.
    with server.app.test_request_context("/socket.io/",
                                         environ_overrides=preview):
        assert WebServer._authorize() == (None, WebAccess.PREVIEW_ONLY)


def test_a_client_cannot_mark_itself_as_the_preview(server):
    # The mark is the WSGI environ's, which a request's headers never set:
    # this server's own port keeps its own rules.
    from src.AutonaWebServer import PreviewGate

    header = PreviewGate.ENVIRON_KEY.replace(".", "-")
    response = _client(server).post(V1_WRITE, headers={header: "1"})
    assert response.status_code == 200
    assert server.actions.calls == [("reset_scores", 1)]


def test_the_preview_server_binds_a_port_the_os_picks(server):
    from src.AutonaWebServer import (PreviewGate, WebServer,
                                     WebServerRequestHandler,
                                     WebSocketHandover)

    made = []

    def make_server(host, port, app, threaded, request_handler):
        made.append((host, port, app, threaded, request_handler))
        return "the server"

    shell = type("Shell", (), {"host_name": "0.0.0.0", "app": server.app})()
    assert WebServer._MakePreviewServer(shell, make_server) == "the server"
    [(host, port, app, threaded, handler)] = made
    assert (host, port, threaded) == ("0.0.0.0", 0, True)
    # Served like this server's own listener (_MakeListener): the end of a
    # WebSocket is werkzeug's dropped connection, not a 500 on it.
    assert handler is WebServerRequestHandler
    assert isinstance(app, WebSocketHandover)
    assert isinstance(app.app, PreviewGate) and app.app.app is server.app


def test_no_preview_server_when_its_port_cannot_be_bound(server, warnings):
    import sys

    from src.AutonaWebServer import WebServer

    def make_server(*args, **kwargs):
        # werkzeug's bind failure: the OSError printed, then sys.exit(1).
        try:
            raise OSError(10048, "address in use")
        except OSError:
            sys.exit(1)

    shell = type("Shell", (), {"host_name": "127.0.0.1", "app": server.app})()
    assert WebServer._MakePreviewServer(shell, make_server) is None
    assert any("no preview listener" in m and "address in use" in m
               for m in warnings)


def test_health_names_the_preview_port(server, monkeypatch):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.AutonaWebServer import WebServer
    from src.core.Actions import AutonaActions

    # What else /health reads, with nothing built here.
    monkeypatch.setattr(AutonaTournamentDataProvider, "instance", type(
        "Provider", (), {"GetProvider": lambda self: None})())
    monkeypatch.setattr(AutonaActions, "instance", type(
        "Actions", (), {"ActiveBoard": lambda self: 1})())
    monkeypatch.setattr(WebServer, "_preview_port", None)
    assert _client(server).get("/api/v1/health").get_json()[
        "data"]["previewPort"] is None
    monkeypatch.setattr(WebServer, "_preview_port", PREVIEW_PORT)
    assert WebServer.PreviewPort() == PREVIEW_PORT
    assert _client(server).get("/api/v1/health").get_json()[
        "data"]["previewPort"] == PREVIEW_PORT
    # And the preview listener answers it: the pages ask either.
    assert _PreviewClient(server).get("/api/v1/health").get_json()[
        "data"]["previewPort"] == PREVIEW_PORT


# ── No frame of another page's (the SF-8a review) ────────────────────────
# A preview's script can navigate its frame -- or a frame it makes -- to this
# server's own port (the sandbox keeps neither), where it would be one of its
# pages. So this server's port shows nothing in a frame another page asked
# for: Sec-Fetch-Dest a frame, Sec-Fetch-Site neither same-origin nor none.
# Every response of this port also says frame-ancestors 'self'. The preview
# listener is not held to it: its pages are the shared view's frames.

FRAMED = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe",
          "Sec-Fetch-Site": "same-site"}
FETCH_MODES = ("navigate", "no-cors", "cors", "same-origin", None)
FETCH_DESTS = ("iframe", "frame", "embed", "object", "fencedframe",
               "document", "image", "script", "style", "empty", None)
FETCH_SITES = ("same-origin", "none", "same-site", "cross-site", None)
# What this server's port serves, the Web UI included.
FRAMED_TARGETS = (
    ("GET", "/layout/scoreboard/index.html"), ("GET", "/web"),
    ("GET", "/web/board"), ("GET", "/web/build/index.html"),
    ("GET", "/assets/icons/mic.svg"), ("GET", "/out/program_state.json"),
    ("GET", "/user_data/team_logo/team.png"), ("GET", "/api/v1/state"),
    ("GET", "/program-state"), ("GET", LEGACY_WRITE), ("POST", V1_WRITE),
    ("GET", "/playerdb"), ("GET", "/api/v1/connection"),
    ("GET", "/no/such/route"),
)


def _fetch_headers(mode, dest, site):
    names = ("Sec-Fetch-Mode", "Sec-Fetch-Dest", "Sec-Fetch-Site")
    return {name: value for name, value in zip(names, (mode, dest, site))
            if value is not None}


def _served_files(server):
    for relative in ("layout/scoreboard/index.html", "web/build/index.html",
                     "assets/icons/mic.svg", "out/program_state.json",
                     "user_data/team_logo/team.png"):
        _write(server.root, relative, b"a file")


def test_the_frame_rule_is_the_fetch_dest_and_site_only():
    # The table: refused exactly when the response would show in a frame
    # (whatever the mode: an <object> may be fetched no-cors) another page
    # asked for (same-site: the preview's port, a LAN page's; cross-site).
    for mode in FETCH_MODES:
        for dest in FETCH_DESTS:
            for site in FETCH_SITES:
                expected = (dest in ("iframe", "frame", "embed", "object",
                                     "fencedframe")
                            and site in ("same-site", "cross-site"))
                assert WebAccess.FramedElsewhere(dest, site) is expected, (
                    mode, dest, site)
    # Spelled any way a header may be.
    assert WebAccess.FramedElsewhere(" IFrame ", "Same-Site") is True
    assert WebAccess.FramedElsewhere("iframe", " SAME-ORIGIN ") is False


def test_this_servers_port_follows_the_frame_table(server):
    _served_files(server)
    client = _client(server)
    for mode in FETCH_MODES:
        for dest in FETCH_DESTS:
            for site in FETCH_SITES:
                response = client.get(
                    "/layout/scoreboard/index.html",
                    headers=_fetch_headers(mode, dest, site))
                if WebAccess.FramedElsewhere(dest, site):
                    assert response.status_code == 403, (mode, dest, site)
                    assert response.get_json() == WebAccess.ErrorBody(
                        WebAccess.FRAMED_ELSEWHERE)
                else:
                    assert response.status_code == 200, (mode, dest, site)
                    assert response.get_data() == b"a file"


@pytest.mark.parametrize("remote, host", [
    ("127.0.0.1", None),
    # The Web UI opened under this PC's LAN address (here, or a LAN
    # browser): its preview frame is that address at another port.
    (LAN_HOST, LAN_HOST), (LAN_PEER, LAN_HOST),
])
@pytest.mark.parametrize("extra", [
    {}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": PREVIEW_ORIGIN},
    {"X-Autona-Token": "TOKEN"},
])
def test_nothing_of_this_server_shows_in_another_pages_frame(
        server, remote, host, extra):
    _served_files(server)
    headers = dict(FRAMED, **extra)
    if host is not None:
        headers["Host"] = host
    headers = {key: value.replace("TOKEN", WebAccess.Token())
               for key, value in headers.items()}
    for method, url in FRAMED_TARGETS:
        response = _client(server, remote).open(url, method=method,
                                                headers=headers)
        assert response.status_code == 403, (method, url)
        assert response.get_json() == WebAccess.ErrorBody(
            WebAccess.FRAMED_ELSEWHERE), (method, url)
        # The refusal itself shows in no frame, and is no document of this
        # origin's: sandboxed.
        policies = response.headers.getlist("Content-Security-Policy")
        assert WebAccess.REFUSED_FRAME_POLICY in policies, (method, url)
        assert b"A Real Name" not in response.get_data()
    assert server.actions.calls == []


def test_every_route_refuses_another_pages_frame(server):
    from src.AutonaWebServer import WebServer

    refusal = WebAccess.ErrorBody(WebAccess.FRAMED_ELSEWHERE)
    kinds = set()
    for endpoint, method, path, filename in _rule_requests(server.app):
        with server.app.test_request_context(path, method=method,
                                             headers=FRAMED):
            response = WebServer._guard_http_request()
            assert response is not None, (method, path)
            assert response.status_code == 403, (method, path)
            assert response.get_json() == refusal, (method, path)
            kinds.add(WebAccess.RouteAccess(endpoint))
    assert kinds == {WebAccess.READ, WebAccess.WRITE, WebAccess.PLAYERDB,
                     WebAccess.THIS_PC, WebAccess.SHELL, WebAccess.DESIGNER,
                     WebAccess.STATIC}


@pytest.mark.parametrize("headers", [
    {},                                                       # Companion, curl
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"},    # OBS, typed
    {"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "cross-site",
     "Sec-Fetch-Mode": "navigate"},                           # a link to it
    # Its own pages framing its own (the Designer's, a layout's).
    {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-origin",
     "Sec-Fetch-Mode": "navigate"},
    # What a page of another port may read (the preview's script).
    {"Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-site",
     "Sec-Fetch-Mode": "cors"},
    {"Sec-Fetch-Dest": "image", "Sec-Fetch-Site": "cross-site",
     "Sec-Fetch-Mode": "no-cors"},
])
def test_documents_reads_and_its_own_frames_are_served(server, headers):
    _served_files(server)
    for url in ("/layout/scoreboard/index.html", "/web/board",
                "/assets/icons/mic.svg", "/api/v1/state"):
        assert _client(server).get(url, headers=headers).status_code == 200, url


def test_every_response_of_this_server_says_frame_ancestors_self(server):
    _served_files(server)
    client = _client(server, LAN_PEER)
    for response in (client.get("/layout/scoreboard/index.html"),
                     client.get("/web/board"), client.get("/api/v1/state"),
                     client.get("/no/such/route"),
                     client.post(V1_WRITE),                    # a refusal
                     client.get("/user_data/settings.json")):  # not served
        assert WebAccess.FRAME_ANCESTORS in response.headers.getlist(
            "Content-Security-Policy"), response.request.path


def test_the_preview_listener_is_what_the_shared_view_frames(server):
    # Its frames load there from this server's page: same-site, a frame.
    _served_files(server)
    response = _PreviewClient(server).get("/layout/scoreboard/index.html",
                                          headers=FRAMED)
    assert response.status_code == 200
    assert response.get_data() == b"a file"
    assert "Content-Security-Policy" not in response.headers
    # Its own refusals are its own (PREVIEW_ONLY), framed or not.
    response = _PreviewClient(server).get("/web/board", headers=FRAMED)
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.PREVIEW_ONLY)


def test_a_framed_refusal_is_logged_once(server, warnings):
    _served_files(server)
    client = _client(server)
    client.get("/layout/scoreboard/index.html", headers=FRAMED)
    client.get("/web/board", headers=FRAMED)
    logged = [m for m in warnings if "in a frame" in m]
    assert len(logged) == 1
    assert "framed_elsewhere" in logged[0]
    assert "same-site" in logged[0]


# ── The preview listener's lifetime (the SF-8a review) ───────────────────
# _Serve with werkzeug's make_server faked: the preview listener goes when
# this server's own loop fails, is never made when this one cannot listen,
# and is closed with it when stop() came first.

class _FakeServer:
    def __init__(self, port, fail_after=None):
        self.server_port = port
        self.calls = []
        self.serving = threading.Event()
        self._stop = threading.Event()
        # Another server to wait for before failing (this server's loop).
        self._fail_after = fail_after

    def serve_forever(self):
        self.calls.append("serve_forever")
        self.serving.set()
        if self._fail_after is not None:
            assert self._fail_after.serving.wait(10)
            raise RuntimeError("the loop broke")
        self._stop.wait(10)

    def shutdown(self):
        self.calls.append("shutdown")
        self._stop.set()

    def server_close(self):
        self.calls.append("server_close")


def _serving_shell(monkeypatch, make_server):
    import werkzeug.serving

    from src.AutonaWebServer import WebServer

    monkeypatch.setattr(werkzeug.serving, "make_server", make_server)
    monkeypatch.setattr(WebServer, "_listening", None)
    monkeypatch.setattr(WebServer, "_preview_port", None)
    shell = WebServer.__new__(WebServer)
    shell.failures = []
    shell._on_start_failed = shell.failures.append
    shell._serve_lock = threading.Lock()
    shell._server = shell._thread = None
    shell._preview_server = shell._preview_thread = None
    shell._stopping = False
    shell.host_name, shell.port, shell.listen_lan = "127.0.0.1", 5107, False
    return shell


def test_a_failing_loop_takes_the_preview_listener_down(monkeypatch):
    from src.AutonaWebServer import WebServer

    preview = _FakeServer(PREVIEW_PORT)
    main = _FakeServer(5107, fail_after=preview)
    servers = [main, preview]
    shell = _serving_shell(monkeypatch, lambda *a, **k: servers.pop(0))

    shell._Serve()

    assert preview.calls == ["serve_forever", "shutdown", "server_close"]
    assert WebServer.PreviewPort() is None
    assert shell._preview_server is None and shell._preview_thread is None
    assert shell.failures and "the loop broke" in shell.failures[0]


def test_no_preview_listener_when_this_server_cannot_listen(monkeypatch):
    import sys

    from src.AutonaWebServer import WebServer

    made = []

    def make_server(host, port, app, **kwargs):
        made.append(port)
        try:
            raise OSError(10048, "address in use")
        except OSError:
            sys.exit(1)

    shell = _serving_shell(monkeypatch, make_server)
    shell._Serve()

    assert made == [5107]
    assert WebServer.PreviewPort() is None
    assert shell._preview_server is None
    assert shell.failures and "could not listen" in shell.failures[0]


def test_a_stop_during_the_start_closes_both_listeners(monkeypatch):
    from src.AutonaWebServer import WebServer

    main, preview = _FakeServer(5107), _FakeServer(PREVIEW_PORT)
    servers = [main, preview]
    shell = None

    def make_server(*args, **kwargs):
        if len(servers) == 1:
            shell._stopping = True    # stop() came while the preview binds
        return servers.pop(0)

    shell = _serving_shell(monkeypatch, make_server)
    shell._Serve()

    assert main.calls == ["server_close"]
    assert preview.calls == ["server_close"]
    assert shell._preview_server is None and shell._server is None
    assert WebServer.PreviewPort() is None
    assert shell.failures == []


def test_a_failing_loop_leaves_the_preview_listener_to_a_stop_begun(
        monkeypatch):
    # stop() took the listeners when it began: it closes them, not _Serve.
    preview = _FakeServer(PREVIEW_PORT)
    shell = _serving_shell(monkeypatch, None)
    shell._preview_server = preview
    shell._stopping = True

    shell._DropPreview(preview)

    assert preview.calls == []
    assert shell._preview_server is preview


# ── The WebSocket transport on both listeners (the SF-8a follow-up) ──────
# Real werkzeug servers, built as _Serve builds both (WebServer._MakeListener),
# and a raw WebSocket client (wsproto, simple-websocket's own), so what
# follows the close frame on the wire is seen. The end of a WebSocket --
# the client's close or the server's -- used to make werkzeug assert
# "write() before start_response" (an error in the log at every disconnect)
# and write a 500 response onto the WebSocket's connection after the close
# frame: a browser still reading failed the socket with "Invalid frame
# header". Both listeners had it, the main one long before SF-8a.

@pytest.fixture
def listeners(server):
    from werkzeug.serving import make_server

    from src.AutonaWebServer import PreviewGate, WebServer

    made = {
        "main": WebServer._MakeListener(
            make_server, "127.0.0.1", 0, server.app),
        "preview": WebServer._MakeListener(
            make_server, "127.0.0.1", 0, PreviewGate(server.app)),
    }
    threads = [threading.Thread(target=listener.serve_forever, daemon=True)
               for listener in made.values()]
    for thread in threads:
        thread.start()
    yield made
    for listener in made.values():
        listener.shutdown()
        listener.server_close()
    for thread in threads:
        thread.join(5)


class _WebSocketSession:
    """One Socket.IO connection over the WebSocket transport, read frame by
    frame: the texts received, what came after the close frame, and whether
    the server closed the connection (EOF) within the timeout."""

    TIMEOUT = 5

    def __init__(self, port):
        import socket

        from wsproto import ConnectionType, WSConnection
        from wsproto.events import Request

        self.sock = socket.create_connection(("127.0.0.1", port),
                                             timeout=self.TIMEOUT)
        self.ws = WSConnection(ConnectionType.CLIENT)
        self.texts = []
        self.after_close = []
        self.closed = False
        self.eof = False
        self.sock.sendall(self.ws.send(Request(
            host=f"127.0.0.1:{port}",
            target="/socket.io/?EIO=4&transport=websocket")))

    def read_until(self, done):
        import socket

        from wsproto.connection import ConnectionState
        from wsproto.events import CloseConnection, Ping, TextMessage

        while not done() and not self.eof:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                return
            if not data:
                self.eof = True
                return
            if self.closed:
                self.after_close.append(data)
                continue
            self.ws.receive_data(data)
            for event in self.ws.events():
                if self.closed:
                    # Parsed out of the same read as the close frame.
                    self.after_close.append(repr(event))
                elif isinstance(event, TextMessage):
                    self.texts.append(event.data)
                elif isinstance(event, Ping):
                    self.sock.sendall(self.ws.send(event.response()))
                elif isinstance(event, CloseConnection):
                    self.closed = True
                    # The server closed first: answer, as a browser does.
                    if self.ws.state is ConnectionState.REMOTE_CLOSING:
                        self.sock.sendall(self.ws.send(event.response()))

    def events(self, name):
        return [text for text in self.texts
                if text.startswith(f'42["{name}"')]

    def send(self, text):
        from wsproto.events import TextMessage

        self.sock.sendall(self.ws.send(TextMessage(text)))

    def close(self):
        from wsproto.events import CloseConnection

        self.sock.sendall(self.ws.send(CloseConnection(code=1000)))

    def engine_io_sid(self):
        [opened] = [text for text in self.texts if text.startswith("0")]
        return json.loads(opened[1:])["sid"]


@pytest.mark.parametrize("closer", ["client", "server"])
@pytest.mark.parametrize("which", ["main", "preview"])
def test_a_websocket_ends_with_nothing_after_its_close_frame(
        server, listeners, caplog, which, closer):
    import logging

    caplog.set_level(logging.ERROR, logger="werkzeug")
    session = _WebSocketSession(listeners[which].server_port)
    try:
        session.read_until(lambda: any(t.startswith("0")
                                       for t in session.texts))
        session.send("40")
        # The state, on connecting and on asking (a read event).
        session.read_until(lambda: session.events("program_state"))
        assert len(session.events("program_state")) == 1
        session.send('42["program_state",{}]')
        session.read_until(lambda: len(session.events("program_state")) == 2)
        assert len(session.events("program_state")) == 2

        if closer == "client":
            session.close()
        else:
            server.socketio.server.eio.disconnect(session.engine_io_sid())
        session.read_until(lambda: False)
    finally:
        session.sock.close()

    assert session.closed
    # Nothing after the close frame (it was "HTTP/1.1 500 INTERNAL SERVER
    # ERROR..."), and the server closed the connection rather than waiting
    # for another request on it.
    assert session.after_close == []
    assert session.eof
    werkzeug_errors = [record.getMessage() for record in caplog.records
                       if record.name == "werkzeug"
                       and record.levelno >= logging.ERROR]
    assert werkzeug_errors == []


# ── The guides' routes (ADR-005 S-final SF-3a) ───────────────────────────
# The connection is THIS_PC: this PC's own request only (loopback or its LAN
# address, its own Host, no forwarding, no foreign page), token or not -- it
# names this PC's folders. The catalogue is a READ. The access token is not
# THIS_PC (a same-origin layout/ page would pass as this PC's own request):
# its routes are SHELL (SF-4a-1, below).

CONNECTION_URL = "/api/v1/connection"
CATALOG_URL = "/api/v1/api-catalog"


def test_this_pc_reads_the_connection_with_no_token(server):
    response = _client(server).get(CONNECTION_URL)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["ok"] is True
    assert response.headers["Cache-Control"] == "no-store"
    # No secret in it.
    assert WebAccess.Token()
    assert WebAccess.Token().encode() not in response.data


@pytest.mark.parametrize("local", [LAN_HOST, LAN_HOST_2])
def test_this_pc_under_its_lan_address_reads_the_connection(server, local):
    response = _client(server, local).get(CONNECTION_URL,
                                          headers={"Host": local})
    assert response.status_code == 200, response.get_data(as_text=True)


@pytest.mark.parametrize("withToken", [False, True])
def test_another_computer_gets_no_connection_even_with_the_token(
        server, withToken):
    token = WebAccess.Token()
    headers = {WebAccess.TOKEN_HEADER: token} if withToken else {}

    response = _client(server, LAN_PEER).get(CONNECTION_URL, headers=headers)

    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)
    assert token.encode() not in response.data


@pytest.mark.parametrize("headers", [
    {"Origin": "null"},                       # sandboxed iframe / file:// page
    {"Origin": "https://evil.example"},
    {"Origin": "http://localhost:3000"},      # another local server
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Host": "evil.example"},                 # DNS rebinding
    {"Host": "127.0.0.1:9999"},               # this PC's name, another port
    {"X-Forwarded-For": "203.0.113.9"},       # a local proxy relaying
    {"Forwarded": "for=203.0.113.9"},
    {"X-Real-IP": "203.0.113.9"},
])
@pytest.mark.parametrize("withToken", [False, True])
def test_only_this_pcs_own_request_gets_the_connection(
        server, headers, withToken):
    token = WebAccess.Token()
    sent = dict(headers)
    if withToken:
        sent[WebAccess.TOKEN_HEADER] = token

    response = _client(server).get(CONNECTION_URL, headers=sent)

    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)
    assert token.encode() not in response.data


@pytest.mark.parametrize("url", [
    "/api/v1/settings/access-token",
    "/api/v1/settings/access-token:regenerate",
])
def test_the_access_token_is_not_under_the_settings(server, url):
    # Its routes are the shell's, /api/v1/access-token* (SF-4a-1): nothing
    # under /settings serves it.
    token = WebAccess.Token()
    client = _client(server)
    for response in (client.get(url), client.post(url, json={"confirm": True})):
        assert response.status_code in (404, 405)
        assert token.encode() not in response.data
    assert WebAccess.Token() == token


def test_the_api_catalog_is_read_from_anywhere(server):
    for client, headers in ((_client(server, LAN_PEER), {}),
                            (_client(server), {"Origin": "https://evil.example",
                                               "Sec-Fetch-Site": "cross-site"})):
        response = client.get(CATALOG_URL, headers=headers)
        assert response.status_code == 200
        assert response.get_json()["data"]["endpoints"]


# ── The game asset packs' routes (ADR-005 S-final SF-3b / SF-4b-1) ───────
# The list (the upstream index and what is installed, no path) is THIS_PC:
# its read may fetch the index from GitHub -- not another computer's to set
# off, token or not. The rest is SHELL (SF-4b-1; below, with the access
# token's): the last job's state (its messages may name this PC's paths),
# the ZIP upload, the jobs that write user_data/games, their cancel and the
# games folder's reveal -- THIS_PC also lets this origin's layout/ pages
# through. The folder import is not in v1 (a page cannot name a folder).

@pytest.fixture
def assetOps(server, monkeypatch):
    """The asset web operations faked; the names of those called."""
    from src import AutonaWebOperations
    from src.core import AssetPackService as assets

    calls = []

    def recorded(name, answer):
        def op(*args, **kwargs):
            calls.append(name)
            return answer
        return op

    for name, answer in (
            ("get_asset_packs", {"games": []}),
            ("get_assets_job", {"revision": 0, "status": "idle"}),
            ("upload_assets_zip", ("abcdefgh", ["aaa"], [])),
            ("asset_upload_games", (["aaa"], [])),
            ("drop_asset_upload", True),
            ("import_asset_upload", (assets.STARTED, 1)),
            ("install_asset_packs", (assets.STARTED, 1)),
            ("cancel_assets_job", True),
            ("reveal_games_folder", True)):
        monkeypatch.setattr(AutonaWebOperations, name, recorded(name, answer))
    return calls


ASSET_READS = [("/api/v1/assets/packs", "get_asset_packs", {"games": []})]


@pytest.mark.parametrize("url, op, answer", ASSET_READS)
def test_this_pc_reads_the_assets_with_no_token(
        server, assetOps, url, op, answer):
    response = _client(server).get(url)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"] == answer
    assert assetOps == [op]


@pytest.mark.parametrize("url, op, answer", ASSET_READS)
@pytest.mark.parametrize("withToken", [False, True])
def test_another_computer_cannot_read_the_assets_even_with_the_token(
        server, assetOps, withToken, url, op, answer):
    token = WebAccess.Token()
    headers = {WebAccess.TOKEN_HEADER: token} if withToken else {}
    response = _client(server, LAN_PEER).get(url, headers=headers)
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)
    assert assetOps == []


@pytest.mark.parametrize("url, op, answer", ASSET_READS)
@pytest.mark.parametrize("headers", [
    {"Origin": "null"},
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Host": "evil.example"},
    {"X-Forwarded-For": "203.0.113.9"},
])
def test_only_this_pcs_own_request_reads_the_assets(
        server, assetOps, headers, url, op, answer):
    sent = dict(headers, **{WebAccess.TOKEN_HEADER: WebAccess.Token()})
    response = _client(server).get(url, headers=sent)
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)
    assert assetOps == []


def test_the_folder_import_is_not_in_v1(server, assetOps):
    # A page cannot name a folder of this PC; SF-5a's picker brings it back.
    client = _client(server)
    assert client.post("/api/v1/assets:importFolder",
                       json={}).status_code in (404, 405)
    assert assetOps == []


# ── The access token's routes (ADR-005 S-final SF-4a-1) ──────────────────
# SHELL: this PC's own request (GRANT_AMBIENT) that is a fetch of this
# origin's own page (Sec-Fetch-Site same-origin exactly) and carries the
# secret the shell adds to the shared view's fetches (X-Autona-Shell). The
# token does not open it, a LAN device is refused even with the secret, and
# a shell that never issued one (headless) refuses them all. One answer to
# all: 403 SHELL_ONLY. Which requests the shell adds the secret to is
# tests/test_web_panel_host.py's.

TOKEN_URL = "/api/v1/access-token"
REGENERATE_URL = "/api/v1/access-token:regenerate"


@pytest.fixture
def shellSecret(monkeypatch):
    """The shell's secret, issued afresh for the test (and forgotten
    after)."""
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    return WebAccess.IssueShellSecret()


def _shell_headers(secret, **extra):
    # What the shared view's fetch carries: a same-origin fetch, the
    # interceptor's header.
    return dict({"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
                 "Sec-Fetch-Dest": "empty", WebAccess.SHELL_HEADER: secret},
                **extra)


def test_the_secret_is_made_once_kept_in_memory_and_not_at_import(
        settings, monkeypatch, tmp_path):
    import importlib
    import os
    import subprocess
    import sys
    from pathlib import Path

    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    first = WebAccess.IssueShellSecret()
    assert first == WebAccess.IssueShellSecret()
    assert len(first) == 43 and first.isascii()
    # Not in settings.json.
    assert first not in json.dumps(SettingsManager.settings)
    assert importlib.import_module("src.core.WebAccess")._shellSecret == first
    # A fresh process that imports it has none (the form of
    # tests/test_core_qt_free.py: the project on PYTHONPATH, run from an
    # empty directory, no bytecode written).
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    code = ("from src.core import WebAccess; "
            "print(WebAccess._shellSecret is None)")
    out = subprocess.run([sys.executable, "-B", "-c", code],
                         cwd=str(tmp_path), env=env, capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value, matches", [
    (None, False), ("", False), (0, False), (b"x", False),
    (" ", False), ("x" * 43, False),
    ("wrong", False),
    ("ü" * 43, False),       # non-ASCII (a latin-1 header value)
    ("x" * 129, False),      # over the bound
])
def test_only_the_secret_itself_matches(shellSecret, value, matches):
    assert WebAccess.ShellSecretMatches(value) is matches
    assert WebAccess.ShellSecretMatches(shellSecret) is True
    assert WebAccess.ShellSecretMatches(shellSecret + "x") is False
    assert WebAccess.ShellSecretMatches(shellSecret[:-1]) is False
    assert WebAccess.ShellSecretMatches(shellSecret + "ü") is False
    # Nothing trims or folds it: surrounding spaces and another case are
    # another value.
    for padded in (f" {shellSecret}", f"{shellSecret} ", f"\t{shellSecret}"):
        assert WebAccess.ShellSecretMatches(padded) is False


def test_the_secret_is_compared_case_sensitively(shellSecret, monkeypatch):
    # A secret with letters for certain (token_urlsafe may, rarely, draw
    # none in a stretch); the fixture puts the issued one back.
    monkeypatch.setattr(WebAccess, "_shellSecret", "aB" + shellSecret[2:])
    assert WebAccess.ShellSecretMatches("aB" + shellSecret[2:]) is True
    assert WebAccess.ShellSecretMatches("Ab" + shellSecret[2:]) is False
    assert WebAccess.ShellSecretMatches(
        ("aB" + shellSecret[2:]).swapcase()) is False


def test_nothing_matches_before_the_secret_is_issued(settings, monkeypatch):
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    for value in (None, "", "x" * 43, "anything"):
        assert WebAccess.ShellSecretMatches(value) is False


def test_the_shell_reads_the_token(server, shellSecret):
    token = WebAccess.Token()
    response = _client(server).get(TOKEN_URL,
                                   headers=_shell_headers(shellSecret))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"] == {"token": token, "available": True}
    assert response.headers["Cache-Control"] == "no-store"


def test_the_shell_under_this_pcs_lan_address_reads_the_token(
        server, shellSecret):
    response = _client(server, LAN_HOST).get(
        TOKEN_URL, headers=_shell_headers(shellSecret, Host=LAN_HOST))
    assert response.status_code == 200, response.get_json()


def test_no_usable_token_is_said_so(server, shellSecret, monkeypatch, tmp_path):
    # settings.json could not be read: there is none to show.
    _unreadSettings(monkeypatch, tmp_path / "unread.json")
    response = _client(server).get(TOKEN_URL,
                                   headers=_shell_headers(shellSecret))
    assert response.status_code == 200
    assert response.get_json()["data"] == {"token": "", "available": False}


def _refused_as_not_the_shell(response, token, secret):
    assert response.status_code == 403, response.get_data(as_text=True)
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.SHELL_ONLY)
    assert token.encode() not in response.data
    assert secret.encode() not in response.data


# What is not the shared view's own request: each refused, token or not.
NOT_THE_SHELLS = [
    # No secret, or not the one.
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin"}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin", "X-Autona-Shell": ""}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin",
                   "X-Autona-Shell": "x" * 43}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin",
                   "X-Autona-Shell": "ü" * 43}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin",
                   "X-Autona-Shell": "x" * 4096}),
    # The secret, but not a fetch of this origin's own page: a typed URL /
    # Companion / curl (none, absent), another port of this PC (same-site),
    # a page elsewhere.
    ("127.0.0.1", {"Sec-Fetch-Site": "none", "X-Autona-Shell": None}),
    ("127.0.0.1", {"X-Autona-Shell": None}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-site", "X-Autona-Shell": None}),
    ("127.0.0.1", {"Sec-Fetch-Site": "cross-site", "X-Autona-Shell": None}),
    # The secret and same-origin, but not this PC's own request: a foreign
    # page, a rebound host name, a relayed request, another computer.
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin", "X-Autona-Shell": None,
                   "Origin": "https://evil.example"}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin", "X-Autona-Shell": None,
                   "Host": "evil.example"}),
    ("127.0.0.1", {"Sec-Fetch-Site": "same-origin", "X-Autona-Shell": None,
                   "X-Forwarded-For": "203.0.113.9"}),
    (LAN_PEER, {"Sec-Fetch-Site": "same-origin", "X-Autona-Shell": None}),
    (LAN_PEER, {"X-Autona-Shell": None}),
]


@pytest.mark.parametrize("remote, headers", NOT_THE_SHELLS)
@pytest.mark.parametrize("withToken", [False, True])
@pytest.mark.parametrize("method, url", [("GET", TOKEN_URL),
                                         ("POST", REGENERATE_URL)])
def test_only_the_shells_own_request_reaches_the_token(
        server, shellSecret, warnings, remote, headers, withToken, method,
        url):
    token = WebAccess.Token()
    generation = WebAccess.TokenGeneration()
    sent = {name: (shellSecret if value is None else value)
            for name, value in headers.items()}
    if withToken:
        sent[WebAccess.TOKEN_HEADER] = token
    response = _client(server, remote).open(
        url, method=method, headers=sent, json={"confirm": True})
    _refused_as_not_the_shell(response, token, shellSecret)
    assert WebAccess.Token() == token
    assert WebAccess.TokenGeneration() == generation
    # The log names the refusal, never the secret or the token.
    assert any("shell_only" in m for m in warnings)
    assert not any(shellSecret in m or token in m for m in warnings)


@pytest.mark.parametrize("value", [None, "", "x" * 43])
def test_a_shell_that_never_issued_the_secret_refuses_them_all(
        server, monkeypatch, value):
    # Headless: nothing issues it, so no request reaches the token.
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    headers = {"Sec-Fetch-Site": "same-origin"}
    if value is not None:
        headers[WebAccess.SHELL_HEADER] = value
    token = WebAccess.Token()
    for method, url in (("GET", TOKEN_URL), ("POST", REGENERATE_URL)):
        response = _client(server).open(url, method=method, headers=headers,
                                        json={"confirm": True})
        assert response.status_code == 403
        assert response.get_json() == WebAccess.ErrorBody(WebAccess.SHELL_ONLY)
    assert WebAccess.Token() == token


# The game asset routes (SF-4b-1): the same rule. (method, URL, body, the
# operation it reaches, the status it answers the shell.)
ASSET_SHELL_ROUTES = [
    ("GET", "/api/v1/assets/job", None, "get_assets_job", 200),
    ("POST", "/api/v1/assets/uploads", b"PK\x05\x06" + b"\x00" * 18,
     "upload_assets_zip", 201),
    ("DELETE", "/api/v1/assets/uploads/abcdefgh", None, "drop_asset_upload",
     200),
    ("POST", "/api/v1/assets:importZip", {"upload": "abcdefgh"},
     "import_asset_upload", 202),
    ("POST", "/api/v1/assets/packs:install", {"game": "ssbu",
                                              "confirm": True},
     "install_asset_packs", 202),
    ("POST", "/api/v1/assets/job:cancel", {"revision": 1},
     "cancel_assets_job", 200),
    ("POST", "/api/v1/assets/games-folder:reveal", {}, "reveal_games_folder",
     200),
]


def _asset_request(client, method, url, body, headers):
    if isinstance(body, bytes):
        return client.open(url, method=method, headers=headers, data=body)
    return client.open(url, method=method, headers=headers, json=body)


@pytest.mark.parametrize("method, url, body, op, status", ASSET_SHELL_ROUTES)
def test_the_shell_reaches_the_asset_routes(server, shellSecret, assetOps,
                                            method, url, body, op, status):
    response = _asset_request(_client(server), method, url, body,
                              _shell_headers(shellSecret))
    assert response.status_code == status, response.get_json()
    assert op in assetOps
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("remote, headers", NOT_THE_SHELLS)
@pytest.mark.parametrize("withToken", [False, True])
@pytest.mark.parametrize("method, url, body, op, status", ASSET_SHELL_ROUTES)
def test_only_the_shells_own_request_reaches_the_asset_routes(
        server, shellSecret, assetOps, warnings, remote, headers, withToken,
        method, url, body, op, status):
    token = WebAccess.Token()
    sent = {name: (shellSecret if value is None else value)
            for name, value in headers.items()}
    if withToken:
        sent[WebAccess.TOKEN_HEADER] = token
    response = _asset_request(_client(server, remote), method, url, body,
                              sent)
    _refused_as_not_the_shell(response, token, shellSecret)
    assert assetOps == []
    assert any("shell_only" in m for m in warnings)
    assert not any(shellSecret in m or token in m for m in warnings)


@pytest.mark.parametrize("value", [None, "", "x" * 43])
def test_a_shell_that_never_issued_the_secret_refuses_the_asset_routes(
        server, assetOps, monkeypatch, value):
    # Headless: none of them answers.
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    headers = {"Sec-Fetch-Site": "same-origin"}
    if value is not None:
        headers[WebAccess.SHELL_HEADER] = value
    for method, url, body, _op, _status in ASSET_SHELL_ROUTES:
        response = _asset_request(_client(server), method, url, body, headers)
        assert response.status_code == 403, url
        assert response.get_json() == WebAccess.ErrorBody(WebAccess.SHELL_ONLY)
    assert assetOps == []


@pytest.mark.parametrize("site, refused", [
    ("same-origin", False), ("Same-Origin", False), (" same-origin ", False),
    ("none", True), (None, True), ("same-site", True), ("cross-site", True),
    ("", True), ("same-origin, none", True),
])
def test_the_shell_rule_reads_the_fetch_site_exactly(shellSecret, site,
                                                     refused):
    because = WebAccess.ShellRefusalReason(WebAccess.GRANT_AMBIENT, None,
                                           site, shellSecret)
    assert (because == "fetch_site") is refused
    assert (because is None) is not refused


def test_the_shell_rule_is_not_opened_by_the_token(shellSecret):
    tokenRefusal = WebAccess.Refusal(401, "UNAUTHENTICATED", "m", "token_required")
    assert WebAccess.ShellRefusalReason(
        WebAccess.GRANT_TOKEN, None, "same-origin", shellSecret) == "token"
    assert WebAccess.ShellRefusalReason(
        None, tokenRefusal, "same-origin", shellSecret) == "token_required"
    assert WebAccess.ShellRefusalReason(
        WebAccess.GRANT_AMBIENT, None, "same-origin", None) == "no_secret"
    assert WebAccess.ShellRefusalReason(
        WebAccess.GRANT_AMBIENT, None, "same-origin", "nope") == "secret"


def test_regenerating_needs_confirm(server, shellSecret):
    token = WebAccess.Token()
    generation = WebAccess.TokenGeneration()
    client = _client(server)
    for body in ({}, {"confirm": False}):
        response = client.post(REGENERATE_URL, json=body,
                               headers=_shell_headers(shellSecret))
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "CONFIRMATION_REQUIRED"
        assert token.encode() not in response.data
    assert WebAccess.Token() == token
    assert WebAccess.TokenGeneration() == generation


def test_regenerating_replaces_the_token_and_cuts_off_its_connections(
        server, shellSecret):
    old = WebAccess.Token()
    generation = WebAccess.TokenGeneration()
    lan = _ws(server, LAN_PEER, auth={"token": old})
    assert _scoreup(lan) == []
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        response = _client(server).post(
            REGENERATE_URL, json={"confirm": True},
            headers=_shell_headers(shellSecret, Origin="http://localhost"))
    finally:
        logger.remove(sink)
    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    new = response.get_json()["data"]["token"]
    assert response.get_json()["data"]["available"] is True
    assert new and new != old and new == WebAccess.Token()
    assert WebAccess.TokenGeneration() == generation + 1
    # The old token stops at once: a LAN write, and the connection it let in
    # at its next event.
    lanClient = _client(server, LAN_PEER)
    assert lanClient.post(V1_WRITE, headers={"X-Autona-Token": old}
                          ).status_code == 401
    assert lanClient.post(V1_WRITE, headers={"X-Autona-Token": new}
                          ).status_code == 200
    assert _scoreup(lan) == WebAccess.ErrorBody(WebAccess.TOKEN_REPLACED)
    # The op log keeps the body's keys only: no token, no secret.
    oplog = [m for m in messages if "access-token.regenerate" in m]
    assert oplog and "-> 200" in oplog[-1] and "['confirm']" in oplog[-1]
    assert not any(old in m or new in m or shellSecret in m for m in messages)


def test_no_new_token_while_settings_json_cannot_be_opened(
        server, shellSecret, monkeypatch, tmp_path):
    # It could not be saved: none is issued, and the answer says so -- the
    # old one (none to use here) is still the one.
    saved = _unreadSettings(monkeypatch, tmp_path / "unread.json")
    generation = WebAccess.TokenGeneration()
    response = _client(server).post(REGENERATE_URL, json={"confirm": True},
                                    headers=_shell_headers(shellSecret))
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "NOT_READY"
    assert WebAccess.TokenGeneration() == generation
    assert saved == []


def test_the_preview_listener_never_reaches_the_token(server, shellSecret):
    # PreviewServes serves READ alone: SHELL is refused there before any of
    # the above, secret or not.
    for method in ("GET", "HEAD", "OPTIONS", "POST"):
        for endpoint in ("api_v1.get_access_token",
                         "api_v1.access_token_regenerate"):
            assert WebAccess.PreviewServes(endpoint, method) is False
    response = _PreviewClient(server).get(
        TOKEN_URL, headers=_shell_headers(shellSecret))
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.PREVIEW_ONLY)


def test_the_token_is_not_shown_in_another_pages_frame(server, shellSecret):
    response = _client(server).get(TOKEN_URL, headers=dict(
        _shell_headers(shellSecret), **FRAMED))
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(
        WebAccess.FRAMED_ELSEWHERE)


# ── The shell request's ticket (ADR-005 S-final SF-5a-1) ─────────────────
# GET /api/v1/shell-session: SHELL like the token's routes (the same
# refusals, the same 403), answering the ticket a shell request carries
# (src/AutonaWebPanelHost.py's ParseShellRequest checks it). The ticket is
# not the secret: the secret is what the shell adds to every fetch of the
# shared view, the ticket is what only a page that read this route can put
# in a URL. Made on the first answer, the same for the process, in memory.

SESSION_URL = "/api/v1/shell-session"


@pytest.fixture
def noTicket(monkeypatch):
    """No ticket issued yet (and the test's forgotten after)."""
    monkeypatch.setattr(WebAccess, "_shellTicket", None)


def test_the_shell_reads_the_ticket(server, shellSecret, noTicket):
    response = _client(server).get(SESSION_URL,
                                   headers=_shell_headers(shellSecret))
    assert response.status_code == 200, response.get_json()
    ticket = response.get_json()["data"]["ticket"]
    assert response.get_json()["data"] == {"ticket": ticket}
    assert response.headers["Cache-Control"] == "no-store"
    # Not the secret, not the token; the same on the next answer.
    assert ticket not in (shellSecret, WebAccess.Token())
    assert len(ticket) == 43 and ticket.isascii()
    again = _client(server).get(SESSION_URL,
                                headers=_shell_headers(shellSecret))
    assert again.get_json()["data"]["ticket"] == ticket
    assert WebAccess.ShellTicketMatches(ticket) is True


def test_the_ticket_is_made_on_the_first_answer_only(
        server, shellSecret, noTicket, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    # Nothing issued: nothing matches, not even an empty value.
    assert WebAccess._shellTicket is None
    for value in (None, "", "x" * 43):
        assert WebAccess.ShellTicketMatches(value) is False
    # A refused request issues none.
    _client(server).get(SESSION_URL, headers={"Sec-Fetch-Site": "same-origin"})
    assert WebAccess._shellTicket is None
    _client(server).get(SESSION_URL, headers=_shell_headers(shellSecret))
    assert WebAccess._shellTicket is not None
    # Not in settings.json.
    assert WebAccess._shellTicket not in json.dumps(SettingsManager.settings)
    # A fresh process that imports it has none (as the secret's test).
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    code = ("from src.core import WebAccess; "
            "print(WebAccess._shellTicket is None)")
    out = subprocess.run([sys.executable, "-B", "-c", code],
                         cwd=str(tmp_path), env=env, capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value, matches", [
    (None, False), ("", False), (0, False), (b"x", False),
    (" ", False), ("x" * 43, False),
    ("wrong", False),
    ("\u00fc" * 43, False),  # non-ASCII
    ("x" * 129, False),       # over the bound
])
def test_only_the_ticket_itself_matches(noTicket, value, matches):
    ticket = WebAccess.IssueShellTicket()
    assert WebAccess.ShellTicketMatches(value) is matches
    assert WebAccess.ShellTicketMatches(ticket) is True
    for other in (ticket + "x", ticket[:-1], ticket + "\u00fc", f" {ticket}",
                  f"{ticket} ", ticket.swapcase()):
        assert WebAccess.ShellTicketMatches(other) is False


def test_the_secret_is_not_the_ticket(shellSecret, noTicket):
    WebAccess.IssueShellTicket()
    assert WebAccess.ShellTicketMatches(shellSecret) is False
    assert WebAccess.ShellSecretMatches(WebAccess._shellTicket) is False


@pytest.mark.parametrize("remote, headers", NOT_THE_SHELLS)
@pytest.mark.parametrize("withToken", [False, True])
def test_only_the_shells_own_request_reaches_the_ticket(
        server, shellSecret, noTicket, warnings, remote, headers, withToken):
    token = WebAccess.Token()
    sent = {name: (shellSecret if value is None else value)
            for name, value in headers.items()}
    if withToken:
        sent[WebAccess.TOKEN_HEADER] = token
    response = _client(server, remote).get(SESSION_URL, headers=sent)
    _refused_as_not_the_shell(response, token, shellSecret)
    # None issued by a refused request.
    assert WebAccess._shellTicket is None
    assert any("shell_only" in m for m in warnings)
    assert not any(shellSecret in m or token in m for m in warnings)


@pytest.mark.parametrize("value", [None, "", "x" * 43])
def test_a_shell_without_the_secret_issues_no_ticket(
        server, monkeypatch, noTicket, value):
    # Headless: no secret, so the ticket is never issued.
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    headers = {"Sec-Fetch-Site": "same-origin"}
    if value is not None:
        headers[WebAccess.SHELL_HEADER] = value
    response = _client(server).get(SESSION_URL, headers=headers)
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.SHELL_ONLY)
    assert WebAccess._shellTicket is None


def test_the_preview_listener_never_reaches_the_ticket(
        server, shellSecret, noTicket):
    for method in ("GET", "HEAD", "OPTIONS", "POST"):
        assert WebAccess.PreviewServes(
            "api_v1.get_shell_session", method) is False
    response = _PreviewClient(server).get(
        SESSION_URL, headers=_shell_headers(shellSecret))
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.PREVIEW_ONLY)
    assert WebAccess._shellTicket is None


def test_the_ticket_is_not_shown_in_another_pages_frame(
        server, shellSecret, noTicket):
    response = _client(server).get(SESSION_URL, headers=dict(
        _shell_headers(shellSecret), **FRAMED))
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(
        WebAccess.FRAMED_ELSEWHERE)
    assert WebAccess._shellTicket is None


def test_only_the_shell_session_route_issues_the_ticket():
    # The ticket leaves the process through that one answer: the one caller
    # of IssueShellTicket in src/ is AutonaWebOperations.get_shell_session.
    import ast
    from pathlib import Path

    callers = []
    root = Path(__file__).resolve().parents[1] / "src"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "IssueShellTicket"):
                    callers.append(
                        (path.relative_to(root).as_posix(), function.name))
    assert callers == [("AutonaWebOperations.py", "get_shell_session")]


# The API settings window's token row (masked, shown, regenerated only when
# confirmed) and its copied curl (the token only for a LAN write) are the
# API page's since ADR-005 S-final SF-4a-2: web/src/GuidePages/
# GuidePages.test.js.


def _browserScoreboard(monkeypatch):
    """The URL "ブラウザ版を開く" opens (the SCORE page's, built by the
    desktop at the click: AutonaWebPanelHost.BrowserScoreboardUrl; the Qt
    board's button built the same until ADR-005 S4ac-3), with no server
    running: the settings' port and LAN access, LAN_HOST as this PC's
    address."""
    from src import AutonaWebPanelHost as HostModule
    from src.AutonaWebServer import WebServer

    monkeypatch.setattr(WebServer, "_listening", None)
    monkeypatch.setattr(HostModule, "GetLocalIP", lambda: LAN_HOST)
    return HostModule.BrowserScoreboardUrl("/scoreboard")


@pytest.mark.parametrize("lan, carries", [(False, False), (True, True)])
def test_open_in_browser_carries_the_token_only_with_lan_access(
        qapp, settings, monkeypatch, lan, carries):
    # "ブラウザ版を開く": with LAN access off the page is on this PC and needs
    # no token -- and one opened without it outlives a regenerate.
    settings.settings = {"general": {"webserver_listen_lan": lan}}
    url = _browserScoreboard(monkeypatch)
    token = WebAccess.Token()
    base = f"http://{LAN_HOST if lan else '127.0.0.1'}:5107/scoreboard"
    assert url == (base + "?token=" + token if carries else base)


def test_open_in_browser_without_a_usable_token_carries_none(
        qapp, settings, monkeypatch, tmp_path):
    # A private settings.json that still cannot be opened: with loadFailed
    # the next write reads the file again, which must never be the real
    # user_data/ one.
    saved = _unreadSettings(monkeypatch, tmp_path / "settings.json")
    settings.settings = {"general": {"webserver_listen_lan": True}}
    assert _browserScoreboard(monkeypatch) == (
        f"http://{LAN_HOST}:5107/scoreboard")
    assert saved == []
