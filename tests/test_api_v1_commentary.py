"""The Commentary page's /api/v1 routes (ADR-005 S4b).

What the React Commentary page (web/src/CommentaryPage) writes through,
added to /api/v1 only (nothing existing changed):

- ``PATCH /api/v1/commentary/<slot>``      caster fields (CommentaryService.SetField)
- ``POST  /api/v1/commentary/reorder``     the drag (CommentaryService.Reorder)
- ``POST  /api/v1/commentary/<slot>/player`` a name completer pick (ApplyPlayerData)
- ``GET   /api/v1/commentary/options``     the role candidates and icons (core's)
- ``GET / PUT /api/v1/layouts/selected/<kind>`` the design (settings.json's key)
- ``PATCH /api/v1/colors/<feature>``       one feature's colours (ColorService)

Through Flask's test client against the real route tables, on the harness's
real state: the contract (envelopes, 400 / 404 / 409, nothing written on a
refusal), the same writes as the Qt panel's (the core operations are
pinned against the golden recording in tests/test_commentary_service.py),
and the access rules (this PC without the token, another computer with it).
"""
import copy

import pytest

from src import AutonaWebOperations as WebOps
from src.core import CommentaryModel as comms
from src.core import WebAccess
from src.core.SettingsManager import SettingsManager
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_commentary_service import (  # noqa: F401
    _baseline, _slot, fakes, lineup)
from tests.test_layout_catalog import builtin


def _commentary(app):
    return copy.deepcopy(app.get("commentary"))


def _revision():
    from src.core.StateManager import StateManager
    return StateManager.revision


def _ok(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


# ── GET /commentary/options ──────────────────────────────────────────────

def test_options_are_core_constants(client):
    body = _ok(client.get("/api/v1/commentary/options"))

    # Not state: no revision.
    assert body == {"ok": True, "data": {
        "roles": comms.ROLES,
        "icons": [{"id": stem, "label": label}
                  for stem, label in comms.ICONS.items()],
        "slots": comms.SLOTS,
        "maxDisplay": comms.MAX_DISPLAY,
    }}


# ── PATCH /commentary/<slot> ─────────────────────────────────────────────

def test_a_field_writes_that_key_only_normalised_as_the_panel(lineup, client):
    expected = _baseline()
    before = _revision()

    body = _ok(client.patch("/api/v1/commentary/2",
                            json={"twitter": "  @handle "}))
    lineup.settle()

    expected["2"]["twitter"] = "handle"
    assert _commentary(lineup) == expected
    assert body["revision"] == _revision() == before + 1


def test_several_fields_are_one_publish(lineup, client):
    before = _revision()

    _ok(client.patch("/api/v1/commentary/5", json={
        "name": " Five ", "team": "T5", "role": "実況", "icon": "megaphone",
        "note": "hi", "display": True}))
    lineup.settle()

    assert _commentary(lineup)["5"] == _slot(
        name="Five", team="T5", twitter="", role="実況", icon="megaphone",
        note="hi", display=1)
    assert _revision() == before + 1


def test_an_unchanged_field_publishes_nothing(lineup, client):
    before = _revision()

    body = _ok(client.patch("/api/v1/commentary/4", json={"name": "Same"}))
    lineup.settle()

    assert _commentary(lineup) == _baseline()
    assert body["revision"] == _revision() == before


def test_the_icon_can_be_cleared(lineup, client):
    _ok(client.patch("/api/v1/commentary/2", json={"icon": ""}))
    lineup.settle()

    assert _commentary(lineup)["2"]["icon"] == ""


def test_a_fifth_caster_is_refused_and_nothing_of_the_request_written(
        lineup, client):
    for slot in ("3", "5"):
        _ok(client.patch(f"/api/v1/commentary/{slot}", json={"display": True}))
    lineup.settle()
    shown = _commentary(lineup)
    assert sum(1 for slot in shown.values() if slot["display"]) == 4

    response = client.patch("/api/v1/commentary/6",
                            json={"name": "Sixth", "display": True})
    lineup.settle()

    _assertRejected(response, "CONFLICT", 409)
    assert "4" in response.get_json()["error"]["message"]
    assert _commentary(lineup) == shown
    # A shown caster stays shown, and hiding one makes room.
    _ok(client.patch("/api/v1/commentary/2", json={"display": True}))
    _ok(client.patch("/api/v1/commentary/3", json={"display": False}))
    _ok(client.patch("/api/v1/commentary/6", json={"display": True}))
    lineup.settle()
    assert _commentary(lineup)["6"]["display"] == 1


def test_showing_a_caster_already_shown_is_ok_at_the_limit(lineup, client):
    """The limit counts the others: a request that keeps a shown caster
    shown (the page sends the whole card's display) is not refused when
    four are shown, and its other fields are written."""
    for slot in ("3", "5"):
        _ok(client.patch(f"/api/v1/commentary/{slot}", json={"display": True}))
    lineup.settle()
    before = _revision()

    body = _ok(client.patch("/api/v1/commentary/2",
                            json={"display": True, "note": "still"}))
    lineup.settle()

    shown = _commentary(lineup)
    assert shown["2"]["display"] == 1 and shown["2"]["note"] == "still"
    assert sum(1 for slot in shown.values() if slot["display"]) == 4
    assert body["revision"] == _revision() == before + 1


def test_two_requests_showing_a_caster_at_once_keep_the_limit(
        lineup, monkeypatch):
    """The check and the writes are one hold of StateManager.lock: with a
    room for one, two requests (two pages, Flask threads) showing a caster
    at the same moment cannot both pass the check. The check is slowed so
    the second request arrives while the first is between its check and its
    write."""
    import threading
    import time

    from src.core import CommentaryService

    assert WebOps.update_caster("3", {"display": True}) is True
    lineup.settle()  # 3 of 4 shown

    check = CommentaryService.CanShow

    def slow_check(slot):
        answer = check(slot)
        time.sleep(0.2)
        return answer

    monkeypatch.setattr(CommentaryService, "CanShow", slow_check)
    start = threading.Barrier(2)
    answers = {}

    def show(slot):
        start.wait(5)
        answers[slot] = WebOps.update_caster(slot, {"display": True})

    workers = [threading.Thread(target=show, args=(slot,), daemon=True)
               for slot in ("5", "6")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    lineup.settle()

    assert sorted(answers.values()) == [False, True]
    shown = _commentary(lineup)
    assert sum(1 for slot in shown.values() if slot["display"]) == 4


@pytest.mark.parametrize("path, body", [
    ("/api/v1/commentary/0", {"name": "X"}),
    ("/api/v1/commentary/13", {"name": "X"}),
    ("/api/v1/commentary/1", {}),
    ("/api/v1/commentary/1", {"name": "X", "gamerTag": "X"}),
    ("/api/v1/commentary/1", {"name": 5}),
    ("/api/v1/commentary/1", {"name": "X", "note": None}),
    ("/api/v1/commentary/1", {"display": "true"}),
    ("/api/v1/commentary/1", {"display": 1}),
    ("/api/v1/commentary/1", {"name": "X", "icon": "マイク"}),
    ("/api/v1/commentary/1", {"icon": None}),
])
def test_bad_input_is_rejected_without_writing(lineup, client, path, body):
    _assertRejected(client.patch(path, json=body))
    lineup.settle()

    assert _commentary(lineup) == _baseline()


def test_a_body_that_is_not_an_object_is_rejected(lineup, client):
    _assertRejected(client.patch("/api/v1/commentary/1", json=["name"]))


def test_names_stay_out_of_the_op_log(lineup, client):
    from loguru import logger

    lines = []
    sink = logger.add(lambda message: lines.append(str(message)), level="INFO")
    try:
        _ok(client.patch("/api/v1/commentary/1",
                         json={"name": "SecretCaster", "twitter": "secret"}))
        _ok(client.post("/api/v1/commentary/1/player",
                        json={"gamerTag": "SecretTag", "name": "Real Name"}))
    finally:
        logger.remove(sink)

    opLog = [line for line in lines if "op=commentary." in line]
    assert len(opLog) == 2
    assert "name" in opLog[0] and "gamerTag" in opLog[1]
    for secret in ("SecretCaster", "secret'", "SecretTag", "Real Name"):
        assert not any(secret in line for line in opLog), secret


# ── POST /commentary/reorder ─────────────────────────────────────────────

def test_reorder_moves_the_casters_in_one_publish(lineup, client):
    order = ["12", "2"] + [slot for slot in comms.SLOTS
                           if slot not in ("12", "2")]
    before = _revision()

    _ok(client.post("/api/v1/commentary/reorder", json={"order": order}))
    lineup.settle()

    baseline = _baseline()
    assert _commentary(lineup) == {
        slot: baseline[source] for slot, source in zip(comms.SLOTS, order)}
    assert _revision() == before + 1


def test_reorder_takes_numbers_too(lineup, client):
    order = list(range(12, 0, -1))

    _ok(client.post("/api/v1/commentary/reorder", json={"order": order}))
    lineup.settle()

    assert _commentary(lineup)["1"]["name"] == "Twelve"


@pytest.mark.parametrize("body", [
    {},
    {"order": None},
    {"order": "1,2,3"},
    {"order": comms.SLOTS[:-1]},
    {"order": comms.SLOTS + ["13"]},
    {"order": ["1"] + comms.SLOTS[:-1]},
    {"order": [True] + comms.SLOTS[1:]},
    {"order": [None] + comms.SLOTS[1:]},
])
def test_a_bad_order_is_rejected_without_writing(lineup, client, body):
    _assertRejected(client.post("/api/v1/commentary/reorder", json=body))
    lineup.settle()

    assert _commentary(lineup) == _baseline()


# ── POST /commentary/<slot>/player ───────────────────────────────────────

def test_a_pick_writes_what_the_legacy_route_writes(lineup, client, fakes):
    pick = {"gamerTag": "qbad Ada", "prefix": "P", "twitter": "@a",
            "custom_textbox": "c", "name": "Real", "country_code": "JP",
            "mains": {"ssbu": [["Mario", 0]]}}

    from src.core.StateManager import StateManager

    WebOps.set_commentary_data("2", pick)
    lineup.settle()
    legacy = _commentary(lineup)
    StateManager.Set("commentary", _baseline())
    lineup.settle()

    _ok(client.post("/api/v1/commentary/2/player", json=pick))
    lineup.settle()

    assert _commentary(lineup) == legacy
    # name / team / twitter / note, censored; role, icon and display kept.
    assert legacy["2"] == _slot(
        name="*** Ada", team="P", twitter="a", role="解説", icon="mic",
        note="c", display=1)


@pytest.mark.parametrize("path, body", [
    ("/api/v1/commentary/0/player", {"gamerTag": "X"}),
    ("/api/v1/commentary/13/player", {"gamerTag": "X"}),
    ("/api/v1/commentary/1/player", {}),
    ("/api/v1/commentary/1/player", {"name": "only a real name"}),
    ("/api/v1/commentary/1/player", {"gamerTag": 5}),
    ("/api/v1/commentary/1/player", {"gamerTag": "X", "prefix": ["P"]}),
])
def test_a_bad_pick_is_rejected_without_writing(lineup, client, path, body):
    _assertRejected(client.post(path, json=body))
    lineup.settle()

    assert _commentary(lineup) == _baseline()


# ── GET / PUT /layouts/selected/<kind> ───────────────────────────────────

@pytest.fixture
def designs(tmp_path, monkeypatch):
    """Two commentary designs (and a scoreboard), and settings that are a
    copy, never saved."""
    from src.core import LayoutCatalog

    root = tmp_path / "layout"
    root.mkdir()
    builtin(root, "commentators", {"kind": "commentary", "displayName": "標準"})
    builtin(root, "commentators_wide",
            {"kind": "commentary", "displayName": "Wide"})
    builtin(root, "scoreboard_simple", {"kind": "scoreboard"})
    monkeypatch.setattr(LayoutCatalog, "LAYOUT_ROOT", str(root))
    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    SettingsManager.Unset("commentary.selected_design")
    return root


def _selected(client):
    return _ok(client.get("/api/v1/layouts/selected/commentary"))["data"]


def test_the_selection_is_the_panel_setting(client, designs):
    assert _selected(client)["id"] == "commentators"     # the default

    body = _ok(client.put("/api/v1/layouts/selected/commentary",
                          json={"id": "commentators_wide"}))

    # The catalog entry, and the panel's key in its own form (the id).
    assert body == {"ok": True, "data": {
        "id": "commentators_wide", "kind": "commentary", "displayName": "Wide",
        "format": None, "game": None, "entry": "index.html"}}
    assert SettingsManager.Get("commentary.selected_design") == \
        "commentators_wide"
    assert _selected(client)["id"] == "commentators_wide"


def test_a_selection_whose_design_is_gone_falls_back_and_is_kept(
        client, designs):
    SettingsManager.Set("commentary.selected_design", "gone")
    assert _selected(client)["id"] == "commentators"

    (designs / "commentators" / "index.html").unlink()
    assert _selected(client)["id"] == "commentators_wide"     # the first
    # The operator's choice is not overwritten by the fallback.
    assert SettingsManager.Get("commentary.selected_design") == "gone"


def test_no_design_installed_is_not_found(client, designs):
    for name in ("commentators", "commentators_wide"):
        (designs / name / "index.html").unlink()

    _assertRejected(client.get("/api/v1/layouts/selected/commentary"),
                    "NOT_FOUND", 404)


@pytest.mark.parametrize("body", [
    {}, {"id": ""}, {"id": 5}, {"id": "gone"},
    {"id": "scoreboard_simple"},          # another kind's
])
def test_an_unknown_design_is_rejected_and_nothing_kept(client, designs, body):
    SettingsManager.Set("commentary.selected_design", "commentators")

    _assertRejected(client.put("/api/v1/layouts/selected/commentary",
                               json=body))

    assert SettingsManager.Get("commentary.selected_design") == "commentators"


@pytest.mark.parametrize("kind", ["scoreboard", "top8", "designer", "bogus"])
def test_a_kind_without_a_selection_here_is_rejected(client, designs, kind):
    _assertRejected(client.get(f"/api/v1/layouts/selected/{kind}"))
    response = client.put(f"/api/v1/layouts/selected/{kind}",
                          json={"id": "scoreboard_simple"})
    _assertRejected(response)
    assert "commentary" in response.get_json()["error"]["message"]
    assert SettingsManager.Get("board.selected_design") is None or \
        SettingsManager.Get("board.selected_design") != "scoreboard_simple"


# ── PATCH /colors/<feature> ──────────────────────────────────────────────

@pytest.fixture
def colors(autonaApp, monkeypatch):
    """Settings that are a copy (never saved); the colors subtree restored."""
    monkeypatch.setattr(SettingsManager, "settings",
                        copy.deepcopy(SettingsManager.settings))
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    saved = copy.deepcopy(autonaApp.get("colors"))
    try:
        yield autonaApp
    finally:
        from src.core.StateManager import StateManager
        if saved is None:
            StateManager.Unset("colors")
        else:
            StateManager.Set("colors", saved)
        autonaApp.settle()


def test_a_feature_colour_is_kept_and_published(client, colors):
    body = _ok(client.patch("/api/v1/colors/commentary",
                            json={"theme": "#123456"}))
    colors.settle()

    assert SettingsManager.Get("colors.commentary.theme_color") == "#123456"
    assert colors.get("colors.commentary.themeColor") == "#123456"
    assert body["revision"] == _revision()


def test_both_colours_are_one_publish(client, colors):
    before = _revision()

    _ok(client.patch("/api/v1/colors/commentary",
                     json={"theme": "#111111", "accent": "#abc"}))
    colors.settle()

    assert colors.get("colors.commentary") == {
        "themeColor": "#111111", "accentColor": "#abc"}
    assert _revision() == before + 1


@pytest.mark.parametrize("path, body", [
    ("/api/v1/colors/bogus", {"theme": "#123456"}),
    ("/api/v1/colors/Commentary", {"theme": "#123456"}),
    ("/api/v1/colors/commentary", {}),
    ("/api/v1/colors/commentary", {"theme": "red"}),
    ("/api/v1/colors/commentary", {"theme": "#12345"}),
    ("/api/v1/colors/commentary", {"theme": None}),
    ("/api/v1/colors/commentary", {"theme": "#123456", "border": "#000"}),
    ("/api/v1/colors/commentary", {"theme": "#123456", "accent": "#zzz"}),
])
def test_a_bad_colour_is_rejected_without_writing(client, colors, path, body):
    before = copy.deepcopy(SettingsManager.settings)
    state = copy.deepcopy(colors.get("colors"))

    _assertRejected(client.patch(path, json=body))
    colors.settle()

    assert SettingsManager.settings == before
    assert colors.get("colors") == state


# ── access (ADR-005 S3a): this PC without the token, others with it ─────

_WRITES = [
    ("patch", "/api/v1/commentary/1", {"name": "X"}, "update_caster"),
    ("post", "/api/v1/commentary/reorder", {"order": comms.SLOTS},
     "reorder_casters"),
    ("post", "/api/v1/commentary/1/player", {"gamerTag": "X"},
     "apply_caster_player"),
    ("put", "/api/v1/layouts/selected/commentary", {"id": "commentators"},
     "select_layout"),
    ("patch", "/api/v1/colors/commentary", {"theme": "#123456"},
     "set_feature_colors"),
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
    for *_rest, name in _WRITES:
        monkeypatch.setattr(
            WebOps, name,
            lambda *args, _name=name: calls.append(_name) or {"id": "x"})
    return calls


@pytest.mark.parametrize("method, path, body, operation", _WRITES)
def test_the_writes_need_the_token_from_another_computer(
        webApp, lan, method, path, body, operation):
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    send = getattr(peer, method)

    refused = send(path, json=body)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert lan == []

    token = WebAccess.RegenerateToken()
    assert send(path, json=body, headers={"X-Autona-Token": token}
                ).status_code == 200
    assert lan == [operation]

    # This PC (the desktop's embedded view): no token.
    local = getattr(webApp.test_client(), method)
    assert local(path, json=body).status_code == 200
    assert lan == [operation, operation]


def test_the_reads_need_no_token(webApp, lan, monkeypatch):
    from tests.test_web_access import LAN_PEER

    monkeypatch.setattr(WebOps, "get_selected_layout",
                        lambda kind: {"id": "commentators"})
    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    for path in ("/api/v1/commentary/options",
                 "/api/v1/layouts/selected/commentary"):
        assert peer.get(path).status_code == 200, path
