"""The Board page's /api/v1 routes (ADR-005 S4s).

What the React Board page (web/src/BoardPage) reads and writes through:

- ``GET / PUT /api/v1/layouts/selected/board``      the design (settings.json's
  ``board.selected_design``, the key the Qt panel kept), from the scoreboards
  and the Designer projects together
- ``GET /api/v1/layouts/selected/board/obs-path``   the OBS files' paths
- ``POST /api/v1/layouts/selected/board:reveal``    show one in the file manager

Through Flask's test client against the real route tables. The scenarios of the
Qt panel's pulldown (tests/test_board_widget.py until it was removed) are kept
here: a design missing from one scan falls back on screen only, and the
operator's choice comes back with its folder. The file manager is never started:
a spy stands in for subprocess.Popen.
"""
import copy
import os
import shutil

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaWebServer import WebServer
from src.core import LayoutCatalog, WebAccess
from src.core.SettingsManager import SettingsManager
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_layout_catalog import builtin, project
from tests.test_web_access import LAN_HOST, LAN_HOST_2, LAN_PEER

SAVED_KEY = "board.selected_design"
DEFAULT = "scoreboard_crewbattle"
SELECTED = "/api/v1/layouts/selected/board"
OBS_PATH = f"{SELECTED}/obs-path"
REVEAL = f"{SELECTED}:reveal"
# What the page sends for the design it shows, the default here.
SHOW_DESIGN = {"target": "design", "designId": DEFAULT}


def _ok(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


@pytest.fixture
def designs(tmp_path, monkeypatch):
    """The two scoreboards, a Designer project and a commentary overlay, the
    winner popup's folder, and settings that are a copy, never saved."""
    root = tmp_path / "layout"
    root.mkdir()
    builtin(root, DEFAULT, {"kind": "scoreboard", "displayName": "Crew Battle",
                            "format": "クルーバトル", "game": "スマブラSP"})
    builtin(root, "scoreboard_simple", {"kind": "scoreboard",
                                        "displayName": "Simple"})
    project(root, "mine", {"name": "決勝", "meta": {"format": "<b>3v3</b>",
                                                   "game": "カスタム"}})
    builtin(root, "commentators", {"kind": "commentary", "displayName": "標準"})
    # No autona block and no legacy name: no selection lists it.
    builtin(root, LayoutCatalog.WINNER_POPUP_DIR)
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(root))
    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    SettingsManager.Unset(SAVED_KEY)
    return root


@pytest.fixture
def started(monkeypatch):
    """What the file manager was started with: a spy, so no window opens."""
    calls = []
    monkeypatch.setattr(LayoutCatalog.subprocess, "Popen",
                        lambda args, **kwargs: calls.append((args, kwargs)))
    return calls


def _explorer(path):
    # The whole command line: this and nothing else is what Explorer parses.
    explorer = os.path.join(r"C:\Windows", "explorer.exe")
    return f'"{explorer}" /select,"{path}"'


@pytest.fixture
def onWindows(monkeypatch):
    monkeypatch.setattr(LayoutCatalog.sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", r"C:\Windows")


@pytest.fixture
def lanServer(monkeypatch):
    """The token kept in a private settings dict, and the LAN names."""
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())


def _selectedId(client):
    return _ok(client.get(SELECTED))["data"]["id"]


# ── the selection ────────────────────────────────────────────────────────

def test_the_selection_lists_the_scoreboards_and_the_designer_projects(
        client, designs):
    # The catalog's own list for the page: built-in, then Designer, and the
    # selection accepts exactly those.
    listed = _ok(client.get("/api/v1/layouts?kind=scoreboard,designer"))["data"]
    assert [entry["id"] for entry in listed] == [
        DEFAULT, "scoreboard_simple", "custom/mine"]

    for entry in listed:
        body = _ok(client.put(SELECTED, json={"id": entry["id"]}))
        assert body["data"] == entry
        assert SettingsManager.Get(SAVED_KEY) == entry["id"]
        assert _selectedId(client) == entry["id"]


def test_board_is_a_selection_and_not_a_kind_of_the_catalog(client, designs):
    # GET /layouts is as it was: the page asks for the two kinds.
    _assertRejected(client.get("/api/v1/layouts?kind=board"))
    assert "board" not in LayoutCatalog.KINDS


def test_with_nothing_kept_the_default_is_shown_and_nothing_written(
        client, designs):
    assert _selectedId(client) == DEFAULT
    assert SettingsManager.Get(SAVED_KEY) is None


@pytest.mark.parametrize("body", [
    {}, {"id": ""}, {"id": 5}, {"id": "gone"},
    {"id": "commentators"},             # another selection's
    {"id": LayoutCatalog.WINNER_POPUP_DIR},
])
def test_an_unknown_design_is_rejected_and_nothing_kept(client, designs, body):
    SettingsManager.Set(SAVED_KEY, "scoreboard_simple")

    _assertRejected(client.put(SELECTED, json=body))

    assert SettingsManager.Get(SAVED_KEY) == "scoreboard_simple"


def test_a_design_missing_from_a_scan_falls_back_on_screen_only(
        client, designs):
    # A project.json locked mid-save or broken: one read does not list it.
    # That read shows the default; what the operator chose stays kept, and comes
    # back on the next read that finds it.
    SettingsManager.Set(SAVED_KEY, "custom/mine")
    assert _selectedId(client) == "custom/mine"
    stub = designs / "custom" / "mine" / "project.json"
    saved = stub.read_text(encoding="utf-8")

    stub.write_text("{broken", encoding="utf-8")
    assert _selectedId(client) == DEFAULT
    assert SettingsManager.Get(SAVED_KEY) == "custom/mine"

    stub.write_text(saved, encoding="utf-8")
    assert _selectedId(client) == "custom/mine"
    assert SettingsManager.Get(SAVED_KEY) == "custom/mine"


def test_a_design_missing_at_startup_comes_back_when_it_is_listed(
        client, designs):
    SettingsManager.Set(SAVED_KEY, "custom/later")
    assert _selectedId(client) == DEFAULT
    assert SettingsManager.Get(SAVED_KEY) == "custom/later"

    project(designs, "later", {"name": "後から"})

    assert _selectedId(client) == "custom/later"


def test_only_the_operators_pick_is_kept(client, designs):
    # A fallback the operator then picks themselves is their choice.
    SettingsManager.Set(SAVED_KEY, "custom/gone")
    assert _selectedId(client) == DEFAULT

    _ok(client.put(SELECTED, json={"id": DEFAULT}))

    assert SettingsManager.Get(SAVED_KEY) == DEFAULT


def test_a_pick_of_what_is_kept_already_writes_nothing(client, designs,
                                                       monkeypatch):
    # The page sends every pick of the design shown: it cannot tell a fallback.
    SettingsManager.Set(SAVED_KEY, "scoreboard_simple")
    writes = []
    monkeypatch.setattr(SettingsManager, "Set",
                        lambda key, value: writes.append((key, value)))

    assert _ok(client.put(SELECTED, json={"id": "scoreboard_simple"}))[
        "data"]["id"] == "scoreboard_simple"

    assert writes == []


def test_without_the_default_the_first_listed_is_shown(client, designs):
    shutil.rmtree(designs / DEFAULT)
    assert _selectedId(client) == "scoreboard_simple"


def test_no_design_at_all_is_404_and_no_design_path(client, tmp_path,
                                                    monkeypatch):
    root = tmp_path / "empty"
    root.mkdir()
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(root))

    _assertRejected(client.get(SELECTED), "NOT_FOUND", 404)

    assert _ok(client.get(OBS_PATH))["data"] == {
        "designId": None,
        "design": None,
        "winnerPopup": str(root / LayoutCatalog.WINNER_POPUP_DIR / "index.html")}


def test_a_designer_projects_free_text_reaches_the_page_as_it_is(
        client, designs):
    # Drawn as text by the page, so nothing here cleans or escapes it.
    SettingsManager.Set(SAVED_KEY, "custom/mine")
    entry = _ok(client.get(SELECTED))["data"]
    assert entry["format"] == "<b>3v3</b>"
    assert entry["game"] == "カスタム"
    assert entry["displayName"] == "Custom: 決勝"


# ── the OBS files ────────────────────────────────────────────────────────

def test_the_obs_files_are_the_selected_designs_and_the_popups(client, designs):
    assert _ok(client.get(OBS_PATH)) == {"ok": True, "data": {
        "designId": DEFAULT,
        "design": str(designs / DEFAULT / "index.html"),
        "winnerPopup": str(designs / "winner_popup" / "index.html"),
    }}

    SettingsManager.Set(SAVED_KEY, "custom/mine")

    # The local path's own separators, the id's slash not among them.
    files = _ok(client.get(OBS_PATH))["data"]
    assert files["designId"] == "custom/mine"
    assert files["design"] == str(designs / "custom" / "mine" / "index.html")


def test_the_paths_follow_a_designs_own_entry(client, designs):
    builtin(designs, "scoreboard_other", {"kind": "scoreboard",
                                          "entry": "main.html"},
            entry="main.html")
    SettingsManager.Set(SAVED_KEY, "scoreboard_other")

    assert _ok(client.get(OBS_PATH))["data"]["design"].endswith(
        os.path.join("scoreboard_other", "main.html"))


def test_the_catalog_names_no_absolute_path(client, designs):
    # It is readable by anyone who reaches the port; a path names the account.
    text = client.get("/api/v1/layouts").get_data(as_text=True) \
        + client.get(SELECTED).get_data(as_text=True)
    assert str(designs.parent.name) not in text
    assert "index.html" in text


def test_the_paths_are_for_this_pc_or_the_token(webApp, autonaApp, designs,
                                                lanServer):
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    refused = peer.get(OBS_PATH)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert str(designs) not in refused.get_data(as_text=True)

    token = WebAccess.RegenerateToken()
    assert peer.get(OBS_PATH, headers={"X-Autona-Token": token}).status_code == 200
    assert webApp.test_client().get(OBS_PATH).status_code == 200
    # What the page lists from stays open, like the other designs'.
    assert peer.get(SELECTED).status_code == 200


# ── show in the file manager ─────────────────────────────────────────────

@pytest.mark.parametrize("target, parts", [
    ("design", (DEFAULT, "index.html")),
    ("winnerPopup", ("winner_popup", "index.html")),
])
@pytest.mark.parametrize("platform, expect", [
    ("win32", lambda path: _explorer(path)),
    ("darwin", lambda path: ["open", "-R", str(path)]),
    ("linux", lambda path: ["xdg-open", str(path.parent)]),
])
def test_the_file_is_shown_without_a_shell(
        client, designs, started, monkeypatch, target, parts, platform, expect):
    monkeypatch.setattr(LayoutCatalog.sys, "platform", platform)
    monkeypatch.setenv("WINDIR", r"C:\Windows")

    assert client.post(REVEAL, json={"target": target,
                                     "designId": DEFAULT}).status_code == 200

    # No shell=True among the keywords, or any other.
    assert started == [(expect(designs.joinpath(*parts)), {})]


@pytest.mark.parametrize("folder", ["plain", "a b", "a,b", "a b,c", "a;b&c"])
def test_the_path_is_always_quoted_for_explorer(client, tmp_path, started,
                                                monkeypatch, onWindows, folder):
    # Explorer splits at a comma, and subprocess would quote only for a space.
    root = tmp_path / folder / "layout"
    root.mkdir(parents=True)
    builtin(root, DEFAULT, {"kind": "scoreboard"})
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(root))

    assert client.post(REVEAL, json=SHOW_DESIGN).status_code == 200

    assert started[0][0] == _explorer(root / DEFAULT / "index.html")


def test_explorer_is_started_by_its_full_path(client, designs, started,
                                              monkeypatch, onWindows):
    # By name Windows would look in the working directory before system32.
    monkeypatch.setenv("WINDIR", r"D:\Win")

    assert client.post(REVEAL, json=SHOW_DESIGN).status_code == 200

    assert started[0][0].startswith('"' + os.path.join(r"D:\Win", "explorer.exe")
                                    + '" /select,"')


def test_no_windows_directory_is_a_500_and_explorer_not_looked_for_by_name(
        client, designs, started, monkeypatch, onWindows):
    monkeypatch.delenv("WINDIR")

    _assertRejected(client.post(REVEAL, json=SHOW_DESIGN), "INTERNAL", 500)

    assert started == []


def test_windows_cannot_name_a_file_with_a_double_quote(tmp_path):
    # What the quoting relies on, on the file system itself.
    if not LayoutCatalog.sys.platform.startswith("win"):
        pytest.skip("a Windows file system's rule")
    with pytest.raises(OSError):
        (tmp_path / 'a"b').write_text("x", encoding="utf-8")
    assert not os.path.isfile(str(tmp_path / 'a"b'))


def test_a_path_with_a_double_quote_is_refused_and_nothing_started(
        client, designs, started, monkeypatch, onWindows):
    # It cannot be there (above); were it there, it could close the quotes.
    path = str(designs / DEFAULT / 'in"dex.html')
    monkeypatch.setattr(LayoutCatalog, "BoardFiles", lambda root=None: {
        "designId": DEFAULT, "design": path, "winnerPopup": path})
    monkeypatch.setattr(LayoutCatalog.os.path, "isfile", lambda _path: True)

    _assertRejected(client.post(REVEAL, json=SHOW_DESIGN), "NOT_FOUND", 404)

    assert started == []


def test_a_design_no_longer_selected_is_a_conflict_and_nothing_started(
        client, designs, started):
    # Another client chose meanwhile: not the file the operator was looking at.
    response = client.post(REVEAL, json={"target": "design",
                                         "designId": "scoreboard_simple"})

    _assertRejected(response, "CONFLICT", 409)
    assert started == []


@pytest.mark.parametrize("extra", [{}, {"designId": ""}, {"designId": 5},
                                   {"designId": None}])
def test_the_design_target_has_to_name_the_design_shown(client, designs, started,
                                                        extra):
    _assertRejected(client.post(REVEAL, json={"target": "design", **extra}))
    assert started == []


def test_the_winner_popup_is_the_same_whatever_is_selected(client, designs,
                                                           started):
    for body in ({"target": "winnerPopup"},
                 {"target": "winnerPopup", "designId": "gone"}):
        assert client.post(REVEAL, json=body).status_code == 200

    assert len(started) == 2


@pytest.mark.parametrize("body", [
    {}, {"target": "other"}, {"target": 5}, {"target": ["design"]},
    {"target": "../../secret.txt"}, {"target": "C:\\Windows\\notepad.exe"},
])
def test_an_unknown_target_is_rejected_and_nothing_started(client, designs,
                                                           started, body):
    _assertRejected(client.post(REVEAL, json=body))
    assert started == []


def test_no_path_a_client_sends_is_taken(client, designs, started, onWindows):
    response = client.post(REVEAL, json={**SHOW_DESIGN,
                                         "path": "C:\\Windows\\notepad.exe"})

    assert response.status_code == 200
    assert started[0][0] == _explorer(designs / DEFAULT / "index.html")


def test_a_missing_file_is_404_and_logged_and_nothing_started(
        client, designs, started):
    from loguru import logger

    os.remove(designs / LayoutCatalog.WINNER_POPUP_DIR / "index.html")
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        response = client.post(REVEAL, json={"target": "winnerPopup"})
    finally:
        logger.remove(sink)

    _assertRejected(response, "NOT_FOUND", 404)
    assert started == []
    assert any("winnerPopup" in message for message in messages)
    # The account's name stays out of the log and the answer.
    assert not any(str(designs) in message for message in messages)
    assert str(designs) not in response.get_data(as_text=True)


def test_a_file_manager_that_cannot_start_is_a_500(client, designs, monkeypatch):
    def refuse(args, **kwargs):
        raise OSError("no explorer here")
    monkeypatch.setattr(LayoutCatalog.subprocess, "Popen", refuse)

    _assertRejected(client.post(REVEAL, json=SHOW_DESIGN), "INTERNAL", 500)


def test_another_computer_is_refused_even_with_the_token(webApp, autonaApp,
                                                         designs, started,
                                                         lanServer):
    # A window is not opened on the streaming PC's screen from another computer.
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    token = WebAccess.RegenerateToken()

    for headers in ({}, {"X-Autona-Token": token}):
        refused = peer.post(REVEAL, json=SHOW_DESIGN, headers=headers)
        assert refused.status_code == 403
        assert refused.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)

    assert started == []


def test_this_pc_with_a_foreign_page_is_refused_even_with_the_token(
        webApp, autonaApp, designs, started, lanServer):
    # The token lets a page of another site in where it may; not here.
    token = WebAccess.RegenerateToken()

    refused = webApp.test_client().post(
        REVEAL, json=SHOW_DESIGN,
        headers={"X-Autona-Token": token, "Origin": "https://evil.example",
                 "Sec-Fetch-Site": "cross-site"})

    assert refused.status_code == 403
    assert started == []


@pytest.mark.parametrize("headers, cause", [
    ({"X-Forwarded-For": "203.0.113.5"}, "forwarded"),
    ({"Host": "rebound.example:5107"}, "foreign_host"),
    ({"Origin": "null"}, "foreign_origin"),
    ({"Sec-Fetch-Site": "cross-site"}, "cross_site"),
])
@pytest.mark.parametrize("withToken", [False, True])
def test_only_this_pcs_own_request_gets_through_and_the_log_says_why_not(
        webApp, autonaApp, designs, started, lanServer, headers, cause,
        withToken):
    # Each way of not being this PC's own request. One answer to all of them,
    # token or not; the log keeps the reason for whoever looks into a refusal.
    from loguru import logger

    sent = dict(headers)
    if withToken:
        sent["X-Autona-Token"] = WebAccess.RegenerateToken()
    messages = []
    sink = logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    try:
        refused = webApp.test_client().post(REVEAL, json=SHOW_DESIGN, headers=sent)
    finally:
        logger.remove(sink)

    assert refused.status_code == 403
    assert refused.get_json() == WebAccess.ErrorBody(WebAccess.THIS_PC_ONLY)
    assert started == []
    told = [m for m in messages if "refused" in m and REVEAL in m]
    assert told and all("this_pc_only" in m for m in told)
    assert all(("(token)" if withToken else f"({cause})") in m for m in told)
    # The token never reaches the log.
    assert not withToken or not any(sent["X-Autona-Token"] in m for m in messages)


def test_this_pc_needs_no_token(webApp, autonaApp, designs, started, lanServer):
    response = webApp.test_client().post(REVEAL, json=SHOW_DESIGN)

    assert response.status_code == 200
    assert len(started) == 1


def test_the_operation_is_cores_and_needs_no_shell(monkeypatch):
    # It works in the headless shell too: no shell operation is registered for
    # it, the web operation goes straight to the catalog.
    seen = []
    monkeypatch.setattr(
        LayoutCatalog, "RevealBoardFile",
        lambda target, designId: seen.append((target, designId)) or True)

    assert WebOps.reveal_board_file("design", "custom/mine") is True
    assert seen == [("design", "custom/mine")]
