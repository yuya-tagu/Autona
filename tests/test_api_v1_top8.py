"""The Top 8 page's /api/v1 routes (ADR-005 S4i).

What the React Top 8 page (web/src/Top8Page) writes through, added to
/api/v1:

- ``GET    /api/v1/countries``             the country combo's rows, the
                                           custom flags last
- ``PATCH  /api/v1/top8/players/<slot>``   name / sponsor / country code /
                                           character (en_name + skin, and
                                           the game it was picked for)
- ``PATCH  /api/v1/top8``                  the overlay's caption
- ``POST   /api/v1/top8/scores``           some matches' scores (a merge)
- ``DELETE /api/v1/top8/scores``           every score back to none

and the ones it shares: POST /bracket:refresh (tests/test_top8_refresh.py),
PATCH /colors/top8 and GET | PUT /layouts/selected/top8
(tests/test_api_v1_commentary.py).

Through Flask's test client against the real route table, on the
harness's top8.* (put back afterwards), with the country data loaded and
the harness game's characters: the contract (envelopes, 400 / 409, nothing
written on a refusal), that each write is core's -- Top8Service's values,
the player cards' for a country and a character -- and the access rules.
What the Qt panel's tests pinned of these writes (one field per edit, the
skin kept, the caption's default) is pinned here now.
"""
import copy

import pytest

from src import AutonaWebOperations as WebOps
from src.core import Top8Model as top8
from src.core import Top8Service
from src.core import WebAccess
from src.core.CountryCatalog import CountryCatalog, CountrySnapshot
from src.core.GameAssetCatalog import GameAssetCatalog
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from tests.fixtures.write_recording import WriteRecorder, encode
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_top8_refresh import _reset, bracket  # noqa: F401

CUSTOM_FLAG = "Custom Flag"


def _ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    return body


def _top8():
    return copy.deepcopy(SM.Get("top8"))


@pytest.fixture
def countryData(monkeypatch):
    """The country data loaded (the harness loads none), with one custom
    flag; the catalog's snapshot put back afterwards."""
    catalog = CountryCatalog.instance
    monkeypatch.setattr(catalog, "_current", catalog._current)
    flag = (CUSTOM_FLAG, {
        "name": CUSTOM_FLAG, "display_name": CUSTOM_FLAG,
        "en_name": CUSTOM_FLAG, "code": CUSTOM_FLAG,
        "asset": f"./user_data/additional_flag/{CUSTOM_FLAG}.png"})
    monkeypatch.setattr(CountryCatalog, "_ReadCustomFlags",
                        staticmethod(lambda: (flag,)))
    assert catalog.Load()
    return catalog


@pytest.fixture
def top8State(bracket):
    """top8.* at last session's bracket (tests/test_top8_refresh.py's
    baseline), put back afterwards."""
    _reset(bracket)
    return bracket


# ── GET /countries ───────────────────────────────────────────────────────

def test_the_countries_are_the_combos_rows_custom_flags_last(
        client, countryData):
    rows = _ok(client.get("/api/v1/countries"))["data"]
    snap = countryData.Current()

    assert rows == (
        [{**data, "label": text, "custom": False}
         for text, data in snap.countryRows]
        + [{**data, "label": text, "custom": True}
           for text, data in snap.customFlagRows])
    assert len(rows) > 100
    japan = next(row for row in rows if row["code"] == "JP")
    assert japan["asset"] == "./assets/country_flag/jp.png"
    assert japan["label"].endswith("(JP)") and japan["custom"] is False
    assert rows[-1]["code"] == CUSTOM_FLAG and rows[-1]["custom"] is True
    # A row's code is what the player write takes: the same value.
    for row in (japan, rows[-1]):
        assert Top8Service.CountryValue(row["code"]) == {
            key: value for key, value in row.items()
            if key not in ("label", "custom")}


def test_the_countries_wait_for_the_data(client, monkeypatch):
    monkeypatch.setattr(CountryCatalog.instance, "_current", CountrySnapshot())
    _assertRejected(client.get("/api/v1/countries"), "NOT_READY", 409)


# ── PATCH /top8/players/<slot> ───────────────────────────────────────────

def test_a_player_edit_writes_its_fields_alone_in_one_cycle(
        client, top8State, countryData):
    """Only the fields sent, in one save cycle; another writer's change to
    another player stays (the panel used to republish all eight)."""
    Top8Service.SetPlayerText("5", "name", "Newer")
    top8State.settle()

    with WriteRecorder() as recorder:
        body = _ok(client.patch("/api/v1/top8/players/1", json={
            "name": "  Typed ", "sponsor": "SP", "country": "JP"}))
        top8State.settle()

    assert body["revision"] == SM.revision
    assert [key for key, _ in recorder.sets] == [
        "top8.players.1.name", "top8.players.1.sponsor",
        "top8.players.1.country"]
    assert len(recorder.deltas) == 1
    assert SM.Get("top8.players.1.name") == "Typed"
    assert SM.Get("top8.players.1.sponsor") == "SP"
    # The player cards' value for the code, key order included.
    assert encode(SM.Get("top8.players.1.country")) \
        == encode(Top8Service.CountryValue("JP"))
    assert SM.Get("top8.players.5.name") == "Newer"

    # A custom flag is a code too; the same again writes nothing.
    _ok(client.patch("/api/v1/top8/players/2",
                     json={"country": CUSTOM_FLAG}))
    assert SM.Get("top8.players.2.country")["asset"] \
        == f"./user_data/additional_flag/{CUSTOM_FLAG}.png"
    with WriteRecorder() as recorder:
        _ok(client.patch("/api/v1/top8/players/2",
                         json={"country": CUSTOM_FLAG}))
    assert recorder.sets == []


@pytest.mark.parametrize("country", [None, ""])
def test_null_empties_a_country_and_a_character(
        client, top8State, countryData, country):
    top8State.loadCharacters()
    _ok(client.patch("/api/v1/top8/players/3", json={
        "character": {"en_name": "Mario", "skin": 1}}))
    assert SM.Get("top8.players.3.country")["code"] == "FR"   # the baseline
    assert SM.Get("top8.players.3.characters")

    _ok(client.patch("/api/v1/top8/players/3",
                     json={"country": country, "character": None}))
    assert SM.Get("top8.players.3.country") == {}
    assert SM.Get("top8.players.3.characters") == {}


def test_a_character_is_picked_by_en_name_and_skin_and_kept(
        client, top8State):
    """The character alone is its first skin, the skin its own row; the
    value is core's (the player cards' slot value, the skin's assets the
    overlay draws), and a later edit of another field keeps it."""
    top8State.loadCharacters()
    with WriteRecorder() as recorder:
        _ok(client.patch("/api/v1/top8/players/2",
                         json={"character": {"en_name": "Luigi"}}))
    assert recorder.sets == [[
        "top8.players.2.characters",
        encode({"1": Top8Service.CharacterValue("Luigi", 0)})]]

    _ok(client.patch("/api/v1/top8/players/2",
                     json={"character": {"en_name": "Luigi", "skin": 2}}))
    value = SM.Get("top8.players.2.characters.1")
    assert value == Top8Service.CharacterValue("Luigi", 2)
    skins = _ok(client.get("/api/v1/characters"))["data"]["Luigi"]["skins"]
    assert value["assets"] == skins[2]["assets"] and value["assets"]
    assert Top8Service.CharacterPick(value) == ("Luigi", 2)

    with WriteRecorder() as recorder:
        _ok(client.patch("/api/v1/top8/players/2",
                         json={"name": "Luigi main"}))
    assert [key for key, _ in recorder.sets] == ["top8.players.2.name"]
    assert SM.Get("top8.players.2.characters.1") == value


@pytest.mark.parametrize("body, message", [
    ({"country": "ZZ"}, "unknown country: ZZ"),
    ({"name": "Not written", "country": "ZZ"}, "unknown country: ZZ"),
    ({"character": {"en_name": "Nobody"}}, "no character Nobody"),
    ({"sponsor": "Not written",
      "character": {"en_name": "Luigi", "skin": 3}}, "no skin 3 for Luigi"),
], ids=["country", "country and name", "character", "skin and sponsor"])
def test_an_identifier_the_catalogs_do_not_have_writes_nothing(
        client, top8State, countryData, body, message):
    """Refused with nothing written, the other fields of the request
    included: a stray identifier never empties what is on air."""
    top8State.loadCharacters()
    before = _top8()
    with WriteRecorder() as recorder:
        response = client.patch("/api/v1/top8/players/4", json=body)
    _assertRejected(response)
    assert message in response.get_json()["error"]["message"]
    assert recorder.sets == []
    assert _top8() == before


@pytest.mark.parametrize("path, body", [
    ("/api/v1/top8/players/0", {"name": "X"}),
    ("/api/v1/top8/players/9", {"name": "X"}),
    ("/api/v1/top8/players/1", {}),
    ("/api/v1/top8/players/1", [{"name": "X"}]),
    ("/api/v1/top8/players/1", {"seed": 1}),
    ("/api/v1/top8/players/1", {"name": 5}),
    ("/api/v1/top8/players/1", {"sponsor": None}),
    ("/api/v1/top8/players/1", {"country": 81}),
    ("/api/v1/top8/players/1", {"country": {"code": "JP"}}),
    ("/api/v1/top8/players/1", {"character": "Mario"}),
    ("/api/v1/top8/players/1", {"character": {"en_name": ""}}),
    ("/api/v1/top8/players/1", {"character": {"skin": 1}}),
    ("/api/v1/top8/players/1",
     {"character": {"en_name": "Mario", "skin": -1}}),
    ("/api/v1/top8/players/1",
     {"character": {"en_name": "Mario", "skin": True}}),
    ("/api/v1/top8/players/1",
     {"character": {"en_name": "Mario", "skin": "1"}}),
    ("/api/v1/top8/players/1",
     {"character": {"en_name": "Mario", "variant": "x"}}),
    ("/api/v1/top8/players/1",
     {"character": {"en_name": "Mario", "game": 5}}),
])
def test_a_bad_player_write_is_rejected_without_writing(
        client, top8State, path, body):
    before = _top8()
    with WriteRecorder() as recorder:
        _assertRejected(client.patch(path, json=body))
    assert recorder.sets == []
    assert _top8() == before


def test_a_country_before_the_data_is_loaded_is_not_ready(
        client, top8State, monkeypatch):
    monkeypatch.setattr(CountryCatalog.instance, "_current", CountrySnapshot())
    _assertRejected(client.patch("/api/v1/top8/players/1",
                                 json={"country": "JP"}), "NOT_READY", 409)
    # Emptying needs no data.
    _ok(client.patch("/api/v1/top8/players/3", json={"country": None}))
    assert SM.Get("top8.players.3.country") == {}


def test_a_character_picked_for_another_game_writes_nothing(
        client, top8State):
    """The page sends the game whose list it picked on: a game switch it
    has not followed yet is a 409 CONFLICT, with nothing written (the
    other fields of the request included). The loaded game's codename --
    or none sent, as an external client may -- is written."""
    top8State.loadCharacters()
    loaded = GameAssetCatalog.instance.Current().codename
    assert loaded
    before = _top8()
    with WriteRecorder() as recorder:
        response = client.patch("/api/v1/top8/players/1", json={
            "name": "Not written",
            "character": {"en_name": "Mario", "skin": 1, "game": "other"}})
    _assertRejected(response, "CONFLICT", 409)
    assert recorder.sets == []
    assert _top8() == before

    _ok(client.patch("/api/v1/top8/players/1", json={
        "character": {"en_name": "Mario", "skin": 1, "game": loaded}}))
    assert SM.Get("top8.players.1.characters.1")         == Top8Service.CharacterValue("Mario", 1)
    _ok(client.patch("/api/v1/top8/players/1", json={
        "character": {"en_name": "Luigi"}}))
    assert Top8Service.CharacterPick(
        SM.Get("top8.players.1.characters.1")) == ("Luigi", 0)


def test_a_player_edit_reads_each_catalog_once(
        client, top8State, countryData, monkeypatch):
    """The identifiers checked are the ones written: one game snapshot
    and one country lookup per request, both taken under the state lock
    (review L1: a reload between the check and the write could not write
    another game's or another load's value)."""
    top8State.loadCharacters()
    games = GameAssetCatalog.instance
    snapshots = []
    real = games.Current

    def current():
        snapshots.append(real())
        return snapshots[-1]

    lookups = []
    realLookup = CountryCatalog.instance.CountryByCode

    def lookup(code):
        lookups.append(code)
        return realLookup(code)

    monkeypatch.setattr(games, "Current", current)
    monkeypatch.setattr(CountryCatalog.instance, "CountryByCode", lookup)

    _ok(client.patch("/api/v1/top8/players/6", json={
        "country": "JP", "character": {"en_name": "Peach", "skin": 2}}))

    assert len(snapshots) == 1
    assert lookups == ["JP"]
    assert SM.Get("top8.players.6.characters.1")         == Top8Service.CharacterValue("Peach", 2, games, snapshots[0])


# ── PATCH /top8 ──────────────────────────────────────────────────────────

def test_the_caption_is_written_trimmed_and_empty_is_the_default(
        client, top8State):
    body = _ok(client.patch("/api/v1/top8", json={"phaseLabel": " SEMIS "}))
    assert body["data"] == {"phaseLabel": "SEMIS"}
    assert SM.Get("top8.phaseLabel") == "SEMIS"

    body = _ok(client.patch("/api/v1/top8", json={"phaseLabel": ""}))
    assert body["data"] == {"phaseLabel": top8.DEFAULT_PHASE_LABEL}
    assert SM.Get("top8.phaseLabel") == top8.DEFAULT_PHASE_LABEL


@pytest.mark.parametrize("body", [
    {}, {"phaseLabel": 5}, {"phaseLabel": None},
    {"phaseLabel": "X", "players": {}}, {"label": "X"}])
def test_a_bad_caption_is_rejected_without_writing(client, top8State, body):
    with WriteRecorder() as recorder:
        _assertRejected(client.patch("/api/v1/top8", json=body))
    assert recorder.sets == []
    assert SM.Get("top8.phaseLabel") == "FINALS"


# ── POST / DELETE /top8/scores ───────────────────────────────────────────

def test_some_scores_are_merged_and_carried_on(client, top8State):
    """wsf1 applied, then wsf2 alone: wsf1 is kept (a merge), and the two
    winners meet in the winners' final."""
    _ok(client.post("/api/v1/top8/scores", json={"wsf1": [1, 0]}))
    with WriteRecorder() as recorder:
        body = _ok(client.post("/api/v1/top8/scores",
                               json={"wsf2": [2, 0]}))
    matches = SM.Get("top8.matches")
    assert [key for key, _ in recorder.sets] == ["top8.matches"]
    assert body["data"] == matches
    assert matches["wsf1"]["score"] == [1, 0] and matches["wsf1"]["finished"]
    assert matches["wsf2"]["score"] == [2, 0] and matches["wsf2"]["finished"]
    assert matches["wf"]["slots"] == [1, 3]

    # A tie is no result.
    _ok(client.post("/api/v1/top8/scores", json={"wsf2": [1, 1]}))
    assert SM.Get("top8.matches.wsf2.finished") is False
    assert SM.Get("top8.matches.wsf1.finished") is True


@pytest.mark.parametrize("body", [
    {},
    {"wsf1": [2, 0], "nope": [1, 0]},
    {"wsf1": [100, 0]},
    {"wsf1": [-1, 0]},
    {"wsf1": [1]},
    {"wsf1": [1, 0, 0]},
    {"wsf1": [True, 0]},
    {"wsf1": [1.5, 0]},
    {"wsf1": "2-0"},
    {"wsf2": [2, 0], "wsf1": None},
])
def test_a_bad_score_refuses_the_whole_request(client, top8State, body):
    before = _top8()
    with WriteRecorder() as recorder:
        _assertRejected(client.post("/api/v1/top8/scores", json=body))
    assert recorder.sets == []
    assert _top8() == before


def test_the_bounds_are_the_services(client, top8State):
    _ok(client.post("/api/v1/top8/scores",
                    json={"lr1a": [Top8Service.SCORE_MAX, 0]}))
    assert SM.Get("top8.matches.lr1a.score") == [Top8Service.SCORE_MAX, 0]


def test_the_reset_clears_every_score_and_result(client, top8State):
    body = _ok(client.delete("/api/v1/top8/scores"))
    expected = top8.Recompute({matchId: top8.DefaultMatch(matchId)
                               for matchId in top8.MATCH_IDS})
    assert body["data"] == expected
    assert SM.Get("top8.matches") == expected
    assert not any(match["finished"] for match in expected.values())
    # The players and the caption are not the scores.
    assert SM.Get("top8.players.1.name") == "Old 1"
    assert SM.Get("top8.phaseLabel") == "FINALS"


# ── access (ADR-005 S3a): this PC without the token, others with it ─────

_WRITES = [
    ("patch", "/api/v1/top8/players/1", {"name": "X"}, "update_top8_player",
     None),
    ("patch", "/api/v1/top8", {"phaseLabel": "X"}, "set_top8_phase_label",
     "X"),
    ("post", "/api/v1/top8/scores", {"wsf1": [1, 0]}, "apply_top8_scores",
     {}),
    ("delete", "/api/v1/top8/scores", None, "reset_top8_scores", {}),
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
    for _method, _path, _body, name, answer in _WRITES:
        monkeypatch.setattr(
            WebOps, name,
            lambda *args, _name=name, _answer=answer:
            calls.append(_name) or _answer)
    monkeypatch.setattr(WebOps, "get_countries", lambda: [])
    return calls


def _send(client, method, path, body, **kwargs):
    if body is None:
        return getattr(client, method)(path, **kwargs)
    return getattr(client, method)(path, json=body, **kwargs)


@pytest.mark.parametrize("method, path, body, operation, _answer", _WRITES)
def test_the_writes_need_the_token_from_another_computer(
        autonaApp, webApp, lan, method, path, body, operation, _answer):
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER

    refused = _send(peer, method, path, body)
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert lan == []

    token = WebAccess.RegenerateToken()
    assert _send(peer, method, path, body,
                 headers={"X-Autona-Token": token}).status_code == 200
    assert lan == [operation]

    # This PC (the desktop's embedded view): no token.
    assert _send(webApp.test_client(), method, path, body).status_code == 200
    assert lan == [operation, operation]


def test_the_country_list_needs_no_token(autonaApp, webApp, lan,
                                        countryData):
    from tests.test_web_access import LAN_PEER

    peer = webApp.test_client()
    peer.environ_base["REMOTE_ADDR"] = LAN_PEER
    assert peer.get("/api/v1/countries").status_code == 200


def test_the_player_write_logs_its_keys_only(client, top8State, monkeypatch):
    """A player's name is personal data: the op log has the keys."""
    from loguru import logger
    logged = []
    sink = logger.add(lambda message: logged.append(str(message)),
                      level="DEBUG")
    try:
        _ok(client.patch("/api/v1/top8/players/1",
                         json={"name": "Secret Name"}))
    finally:
        logger.remove(sink)
    assert not any("Secret Name" in line for line in logged)
    assert any("top8.player" in line for line in logged)
