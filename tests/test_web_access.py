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

import pytest
from loguru import logger

from src.core import WebAccess
from src.core.SettingsManager import SettingsManager

LAN_PEER = "192.168.1.20"
LAN_HOST = "192.168.1.5"   # this PC's LAN address in these tests
LAN_HOST_2 = "10.8.0.2"    # ... and a second one (a VPN adapter)


class FakeActions:
    """The WebServerActions calls the routes below make, recorded."""

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
    """The real app + Socket.IO server, the actions faked, cwd = tmp_path."""
    from src import AutonaApiV1
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app

    monkeypatch.chdir(tmp_path)
    actions = FakeActions()
    monkeypatch.setattr(WebServer, "actions", actions)
    monkeypatch.setattr(AutonaApiV1, "_actions", actions)
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


def test_an_unreadable_settings_file_issues_nothing(settings, monkeypatch):
    # LoadSettings() could not read settings.json: it looks empty, but the
    # token in it is the one the user's devices hold.
    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    writes = []
    monkeypatch.setattr(SettingsManager, "Set",
                        lambda key, value: writes.append(key))
    generation = WebAccess.TokenGeneration()
    assert WebAccess.Token() == ""
    assert WebAccess.Token() == ""
    assert writes == []
    assert WebAccess.TokenGeneration() == generation
    assert not WebAccess.TokenMatches(["", "anything-at-all-123456"])


def test_without_a_usable_token_lan_writes_are_refused_until_regenerated(
        server, monkeypatch):
    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    client = _client(server, LAN_PEER)
    assert client.post(V1_WRITE, headers={"X-Autona-Token": ""}).status_code == 401
    assert _client(server).post(V1_WRITE).status_code == 200   # this PC: as before
    token = WebAccess.RegenerateToken()
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

    def __init__(self, server, remote_addr="127.0.0.1", headers=None):
        self.client = _client(server, remote_addr)
        self.headers = dict(headers or {})
        opened = self.client.get(self.PATH, headers=self.headers)
        assert opened.status_code == 200, opened.data
        self.sid = json.loads(opened.get_data(as_text=True)[1:])["sid"]
        self.url = f"{self.PATH}&sid={self.sid}"
        self._ack = 0

    def send(self, packet):
        response = self.client.post(self.url, data=packet, headers=self.headers)
        assert response.get_data(as_text=True).lower() == "ok"

    def receive(self):
        body = self.client.get(self.url, headers=self.headers).get_data(as_text=True)
        return body.split("\x1e")

    def connect(self, auth=None):
        self.send("40" + (json.dumps(auth) if auth else ""))
        return self.receive()

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


# ── The API settings window's token row ──────────────────────────────────


@pytest.fixture
def apiWindow(qapp, settings, monkeypatch):
    from src.AutonaApiInfoWindow import AutonaApiInfoWindow
    from src.Helpers import AutonaNetworkHelper

    monkeypatch.setattr(AutonaNetworkHelper, "GetLocalIP", lambda: LAN_HOST)
    window = AutonaApiInfoWindow()
    window.UiMounted()
    try:
        yield window
    finally:
        window.deleteLater()


def test_the_api_window_shows_the_token_masked(apiWindow):
    from qtpy.QtWidgets import QLineEdit, QPushButton

    assert apiWindow.tokenField.text() == WebAccess.Token()
    assert apiWindow.tokenField.echoMode() == QLineEdit.Password
    show = next(b for b in apiWindow.findChildren(QPushButton)
                if b.isCheckable())
    show.setChecked(True)
    assert apiWindow.tokenField.echoMode() == QLineEdit.Normal
    show.setChecked(False)
    assert apiWindow.tokenField.echoMode() == QLineEdit.Password


def test_the_api_window_regenerates_only_when_confirmed(apiWindow,
                                                       monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    before = WebAccess.Token()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.No)
    apiWindow._regenerateToken()
    assert WebAccess.Token() == before
    assert apiWindow.tokenField.text() == before

    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.Yes)
    apiWindow._regenerateToken()
    assert WebAccess.Token() != before
    assert apiWindow.tokenField.text() == WebAccess.Token()


def test_the_copied_curl_carries_the_token_only_for_a_lan_write(apiWindow,
                                                               settings):
    write = ("POST", "/scoreboard/{n}/team/{t}/score:increment",
             "", None, False, True, False)
    read = ("GET", "/state", "", None, False, False, False)
    header = f"{WebAccess.TOKEN_HEADER}: {WebAccess.Token()}"

    assert header not in apiWindow._buildCurl(write)   # loopback: no token
    settings.settings["general"]["webserver_listen_lan"] = True
    assert header in apiWindow._buildCurl(write)
    assert LAN_HOST in apiWindow._buildCurl(write)
    assert header not in apiWindow._buildCurl(read)


@pytest.mark.parametrize("lan, carries", [(False, False), (True, True)])
def test_open_in_browser_carries_the_token_only_with_lan_access(
        qapp, settings, lan, carries):
    # "ブラウザ版を開く": with LAN access off the page is on this PC and needs
    # no token -- and one opened without it outlives a regenerate.
    from types import SimpleNamespace

    from src.AutonaScoreboardWidget import AutonaScoreboardWidget

    settings.settings = {"general": {"webserver_listen_lan": lan}}
    board = SimpleNamespace(webScoreboardUrl="http://127.0.0.1:5107/scoreboard")
    url = AutonaScoreboardWidget._WebScoreboardOpenUrl(board)
    token = WebAccess.Token()
    assert url == ("http://127.0.0.1:5107/scoreboard?token=" + token
                   if carries else "http://127.0.0.1:5107/scoreboard")


def test_open_in_browser_without_a_usable_token_carries_none(
        qapp, settings, monkeypatch):
    from types import SimpleNamespace

    from src.AutonaScoreboardWidget import AutonaScoreboardWidget

    monkeypatch.setattr(SettingsManager, "loadFailed", True)
    settings.settings = {"general": {"webserver_listen_lan": True}}
    board = SimpleNamespace(webScoreboardUrl="http://192.168.1.5:5107/scoreboard")
    assert AutonaScoreboardWidget._WebScoreboardOpenUrl(board) == (
        "http://192.168.1.5:5107/scoreboard")
