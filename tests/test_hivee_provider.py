"""The Hivee tournament provider (src/TournamentDataProvider/HiveeDataProvider
.py) and its URL (src/core/HiveeUrl.py): a Hivee event read through its
stream overlay's JSON, read only.

Pinned here, with Hivee's HTTP stood in for:

- the URL forms taken (the OBS page's, the API's), their normalisation to
  the API URL, plain http for this PC only, and TournamentService taking
  them as a tournament URL;
- the token never in a log line or an exception message: a failed request,
  a refused URL, the web operations' and the v1 op log's lines -- nor in the
  READ APIs' URLs (the state's is pinned in tests/test_api_v1_tournament.py);
- the character table: Hivee's SSBU codes, all of them, onto the asset
  config's keys (checked against Hivee's own list and the installed SSBU
  config when they are on this machine);
- the overlay's match as a set in StartGGDataProvider.GetMatch's shape, its
  sides kept within a match, what an update writes (the characters that
  changed, the fields filled in), a match that left the stream answering
  its last read -- remembered across a forced reload -- and on the board
  through core's set apply;
- one request per view at a time, and a failed read kept a while;
- the stream follow's lookups, the stream queue, the station row;
- the set ids: a Hivee match's uuid through the web's set lookups;
- the Top 8 cards onto Top8Model's fixed topology;
- what the overlay does not carry answering empty.
"""
import copy
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest
from loguru import logger

from src.core import HiveeUrl
from src.core import TournamentService as tournament
from src.TournamentDataProvider import HiveeDataProvider as hivee
from src.TournamentDataProvider.HiveeDataProvider import HiveeDataProvider
from tests.test_web_operations import server  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HIVEE_CHARACTERS = (PROJECT_ROOT.parent / "Hivee" / "src" / "lib" / "ssbu"
                    / "characters.ts")
SSBU_CONFIG = (PROJECT_ROOT / "user_data" / "games" / "ssbu" / "base_files"
               / "config.json")

EVENT = "0f8e2a4c-1b3d-4e5f-8a9b-0c1d2e3f4a5b"
PROGRAM = "11111111-2222-4333-8444-555555555555"
TOKEN = "0123456789abcdef" * 4
ORIGIN = "https://hivee.example.com"
OBS_URL = f"{ORIGIN}/overlay/{EVENT}/scoreboard?token={TOKEN}"
API_URL = f"{ORIGIN}/api/events/{EVENT}/overlay?token={TOKEN}"

# Hivee's SSBU codes as Hivee's characters.ts lists them (frozen: the table
# must cover exactly these).
HIVEE_CODES = [
    "mario", "donkey_kong", "link", "samus", "dark_samus", "yoshi", "kirby",
    "fox", "pikachu", "luigi", "ness", "captain_falcon", "jigglypuff",
    "peach", "daisy", "bowser", "ice_climbers", "sheik", "zelda", "dr_mario",
    "pichu", "falco", "marth", "lucina", "young_link", "ganondorf", "mewtwo",
    "roy", "chrom", "mr_game_and_watch", "meta_knight", "pit", "dark_pit",
    "zero_suit_samus", "wario", "snake", "ike", "pokemon_trainer",
    "diddy_kong", "lucas", "sonic", "king_dedede", "olimar", "lucario",
    "rob", "toon_link", "wolf", "villager", "mega_man", "wii_fit_trainer",
    "rosalina_and_luma", "little_mac", "greninja", "mii_brawler",
    "mii_swordfighter", "mii_gunner", "palutena", "pac_man", "robin",
    "shulk", "bowser_jr", "duck_hunt", "ryu", "ken", "cloud", "corrin",
    "bayonetta", "inkling", "ridley", "simon", "richter", "king_k_rool",
    "isabelle", "incineroar", "piranha_plant", "joker", "hero",
    "banjo_and_kazooie", "terry", "byleth", "min_min", "steve", "sephiroth",
    "pyra_mythra", "kazuya", "sora",
]


# ── the stand-in for Hivee ───────────────────────────────────────────────

class _Response:
    def __init__(self, status, payload=None, content=None):
        self.status_code = status
        self.content = content if content is not None else (
            orjson.dumps(payload) if payload is not None else b"")


class _Hivee:
    """requests.get as Hivee's overlay route answers it: `answers[view]`
    (a payload, a status, or an exception to raise). Records each call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}),
                           "timeout": timeout})
        answer = self.answers.get((params or {}).get("view"))
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return _Response(answer)
        return _Response(200, answer)


def _player(name, score=None, seed=None, characters=(), losers=False,
            x=None, avatar=None):
    return {"name": name, "score": score, "seed": seed, "xHandle": x,
            "avatarUrl": avatar, "avatarPaletteIndex": 1, "losers": losers,
            "characters": list(characters)}


def _match(matchId, a, b, label="Winners Semi Final", bracket="winners",
           station="台 3", program="SSBU シングルス", poolNumber=None):
    return {"matchId": matchId, "round": 3, "matchNumber": 1,
            "bracket": bracket, "pool": 1, "stationName": station,
            "programName": program, "poolNumber": poolNumber,
            "roundLabel": label, "playerA": a, "playerB": b}


CURRENT = _match(
    "m-current",
    _player("Alice", 2, 1, ["mario", "fox"], x="alice_x",
            avatar="https://img.example.com/a.png"),
    _player("Bob", 1, 4, ["luigi"]))
QUEUED = _match("m-next", _player("Carol", seed=2), _player("Dave", seed=3),
                label="Losers Quarter Final", bracket="losers")


def _scoreboard(current=CURRENT, queue=(QUEUED,)):
    return {"event": {"id": EVENT, "title": "Autona Cup",
                      "logoUrl": "https://img.example.com/logo.png"},
            "streamUrl": None, "colors": {"theme": None, "accent": None},
            "design": "universal", "current": copy.deepcopy(current),
            "queue": [dict(copy.deepcopy(m), position=i + 1)
                      for i, m in enumerate(queue)]}


@pytest.fixture(autouse=True)
def _noRoundNames(monkeypatch):
    """No round / phase names loaded, whichever test ran before: the labels
    as Hivee gives them (a test that translates sets its own)."""
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    monkeypatch.setattr(AutonaLocaleHelper, "matchNames", {})
    monkeypatch.setattr(AutonaLocaleHelper, "phaseNames", {})


@pytest.fixture(autouse=True)
def _freshMemory(monkeypatch):
    """No match remembered from another test (the memory outside the
    provider instances outlives them), and no character code warned about
    yet."""
    HiveeDataProvider.ForgetMemories()
    monkeypatch.setattr(hivee, "_unknownCodes", set())
    yield
    HiveeDataProvider.ForgetMemories()


@pytest.fixture
def http(monkeypatch):
    fake = _Hivee({"scoreboard": _scoreboard()})
    monkeypatch.setattr(hivee.requests, "get", fake.get)
    return fake


@pytest.fixture
def provider(http, monkeypatch):
    # No cache between the reads of a test unless it says so.
    monkeypatch.setattr(HiveeDataProvider, "CACHE_SECS", 0)
    monkeypatch.setattr(HiveeDataProvider, "FAILURE_SECS", 0)
    return HiveeDataProvider(API_URL, None)


@pytest.fixture
def logs():
    messages = []
    sink = logger.add(messages.append, format="{message}\n{exception}",
                      level="DEBUG")
    try:
        yield messages
    finally:
        logger.remove(sink)


# ── the URL ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, program", [
    (OBS_URL, ""),
    (f"{ORIGIN}/overlay/{EVENT}/top8?token={TOKEN}&program={PROGRAM}",
     PROGRAM),
    (f"{ORIGIN}/overlay/{EVENT}/scoreboard/?token={TOKEN}", ""),
    (API_URL, ""),
    (f"{API_URL}&view=top8&top8=1", ""),
    (f"  {ORIGIN}/api/events/{EVENT.upper()}/overlay/?token={TOKEN}  ", ""),
    (f"{ORIGIN}/overlay/{EVENT}/top8?token={TOKEN}&program=not-a-uuid", ""),
])
def test_the_obs_and_api_urls_are_taken(url, program):
    target = HiveeUrl.Parse(url)
    assert target == HiveeUrl.HiveeTarget(ORIGIN, EVENT, TOKEN, program)
    assert tournament.UrlKind(url) == tournament.HIVEE
    expected = API_URL + (f"&program={PROGRAM}" if program else "")
    assert HiveeUrl.Normalize(url) == expected
    assert tournament.NormalizeEventUrl(url) == expected


def test_a_local_hivee_is_taken_with_its_port():
    url = f"http://localhost:3000/overlay/{EVENT}/scoreboard?token={TOKEN}"
    assert HiveeUrl.Normalize(url) == (
        f"http://localhost:3000/api/events/{EVENT}/overlay?token={TOKEN}")


@pytest.mark.parametrize("host, taken", [
    ("localhost:3000", True),
    ("127.0.0.1:3000", True),
    ("[::1]:3000", True),
    ("hivee.localhost", True),
    ("LOCALHOST", True),
    ("hivee.example.com", False),
    ("192.168.1.20:3000", False),
    ("localhost.example.com", False),
])
def test_plain_http_is_taken_for_this_pc_only(host, taken):
    """Over plain http the token crosses the network in the clear: only a
    Hivee on this PC is read that way."""
    url = f"http://{host}/overlay/{EVENT}/scoreboard?token={TOKEN}"
    assert HiveeUrl.IsHiveeUrl(url) is taken
    assert (tournament.UrlKind(url) == tournament.HIVEE) is taken
    https = f"https://{host}/overlay/{EVENT}/scoreboard?token={TOKEN}"
    assert HiveeUrl.IsHiveeUrl(https)


@pytest.mark.parametrize("url", [
    f"{ORIGIN}/overlay/{EVENT}/scoreboard",                  # no token
    f"{ORIGIN}/overlay/{EVENT}/scoreboard?token=",
    f"{ORIGIN}/overlay/sample/scoreboard?token={TOKEN}",     # not an event
    f"{ORIGIN}/overlay/{EVENT}/other?token={TOKEN}",
    f"{ORIGIN}/events/{EVENT}?token={TOKEN}",
    f"ftp://hivee.example.com/overlay/{EVENT}/scoreboard?token={TOKEN}",
    f"{ORIGIN}/overlay/{EVENT}/scoreboard?token=a%20b",
    "https://www.start.gg/tournament/autona-cup/event/singles",
    "", None, 42,
])
def test_anything_else_is_not_a_hivee_url(url):
    assert HiveeUrl.Parse(url) is None
    assert HiveeUrl.Normalize(url) is None


def test_the_start_gg_forms_are_still_the_start_gg_ones():
    assert tournament.UrlKind(
        "https://www.start.gg/tournament/a/event/b") == tournament.EVENT
    assert tournament.UrlKind(
        "https://www.start.gg/admin/tournament/a/brackets/1") == \
        tournament.ADMIN


@pytest.mark.parametrize("text", [
    OBS_URL,
    repr({"url": API_URL, "force": True}),
    f"url={ORIGIN}%2Foverlay%3Ftoken%3D{TOKEN}%26view%3Dtop8",
    f"{API_URL}&view=top8 and again ?token={TOKEN}#x",
])
def test_the_token_is_masked_in_any_text(text):
    masked = HiveeUrl.Mask(text)
    assert TOKEN not in masked
    assert HiveeUrl.MASK in masked


def test_masking_leaves_what_has_no_token_as_it_is():
    url = "https://www.start.gg/tournament/a/event/b"
    assert HiveeUrl.Mask(url) == url
    assert HiveeUrl.Mask(None) == "None"


def test_the_masked_url_stands_for_the_saved_one_only():
    masked = HiveeUrl.Mask(API_URL)
    assert HiveeUrl.Unmask(masked, API_URL) == API_URL
    assert HiveeUrl.Unmask(f"  {masked} ", API_URL) == API_URL
    # Another event's masked URL is not this one's: it stays masked (and
    # is no Hivee URL, so it is refused as one).
    other = HiveeUrl.Mask(API_URL.replace(EVENT, PROGRAM))
    assert HiveeUrl.Unmask(other, API_URL) == other
    assert not HiveeUrl.IsHiveeUrl(other)
    # Nothing saved, or a saved URL with no token: as it is.
    assert HiveeUrl.Unmask(masked, "") == masked
    start = "https://www.start.gg/tournament/a/event/b"
    assert HiveeUrl.Unmask(start, start) == start
    assert HiveeUrl.Unmask(None, API_URL) is None


# ── the token is never logged ────────────────────────────────────────────

@pytest.mark.parametrize("answer", [
    ConnectionError(f"Max retries exceeded with url: {API_URL}"),
    404, 500,
])
def test_a_failed_read_logs_no_token_and_raises_none(provider, http, logs,
                                                     answer):
    http.answers["scoreboard"] = answer
    assert provider.GetMatch("m-current") == {}
    assert provider.GetTournamentData() == {}
    assert provider.GetStreamMatchId("any") is None
    assert provider.GetStreamQueue() == {}
    assert provider.GetStations() == []
    with pytest.raises(LookupError) as raised:
        provider.GetIconURL(strict=True)
    assert TOKEN not in str(raised.value)
    assert raised.value.__cause__ is None
    with pytest.raises(LookupError):
        provider.GetStations(strict=True)
    assert logs
    assert not [m for m in logs if TOKEN in m]


def test_an_answer_that_is_not_json_logs_no_token(provider, logs,
                                                  monkeypatch):
    monkeypatch.setattr(hivee.requests, "get", lambda *a, **kw: _Response(
        200, content=b"<html>"))
    assert provider.GetMatch("m-current") == {}
    assert [m for m in logs if "did not answer JSON" in m]
    assert not [m for m in logs if TOKEN in m]


def test_a_refused_url_is_logged_without_its_token(logs):
    service = tournament.TournamentService(provider=None, onMain=None)
    refused = f"{ORIGIN}/overlay/not-an-event/scoreboard?token={TOKEN}"
    assert service.SetTournament(refused) == tournament.INVALID_URL
    assert [m for m in logs if "refusing" in m]
    assert not [m for m in logs if TOKEN in m]


def test_the_web_operations_log_the_url_without_its_token(logs,
                                                          monkeypatch):
    from types import SimpleNamespace

    from src import AutonaWebOperations as WebOps
    from src.core.TournamentService import TournamentService

    asked = []
    monkeypatch.setattr(TournamentService, "instance", SimpleNamespace(
        SetTournament=lambda url, **kw: asked.append(url)))
    monkeypatch.setattr(TournamentService, "Load",
                        staticmethod(lambda: {"revision": 3}))
    WebOps.load_tournament(OBS_URL)
    WebOps.set_tournament(OBS_URL)
    assert asked == [OBS_URL, OBS_URL]
    assert len([m for m in logs if "Tournament URL requested" in m]) == 2
    assert not [m for m in logs if TOKEN in m]


def test_the_op_log_masks_the_token_before_the_cut():
    from flask import Flask

    from src import AutonaApiV1 as v1

    app = Flask(__name__)
    body = {"url": API_URL, "force": False, "fetchMedia": True}
    with app.test_request_context("/api/v1/tournament", method="POST",
                                  json=body):
        params = v1._safe_params()
    assert TOKEN not in params
    assert "token=***" in params
    # A token the 300-character cut would halve is masked whole.
    long = {"pad": "x" * 280, "url": API_URL}
    with app.test_request_context("/api/v1/tournament", method="POST",
                                  json=long):
        params = v1._safe_params()
    assert TOKEN[:8] not in params


def test_the_op_log_redacts_keys_and_still_masks_the_token():
    from flask import Flask

    from src import AutonaApiV1 as v1

    app = Flask(__name__)
    body = {"title": "SECRETTITLE", "url": API_URL}
    with app.test_request_context("/api/v1/stream", method="PUT", json=body):
        params = v1._safe_params(redact=("title",))
    assert "SECRETTITLE" not in params
    assert "<redacted>" in params
    assert TOKEN not in params
    assert "token=***" in params


# ── the characters ───────────────────────────────────────────────────────

def test_the_table_covers_every_hivee_code_once():
    assert sorted(hivee.HIVEE_TO_SSBU) == sorted(HIVEE_CODES)
    assert len(set(hivee.HIVEE_TO_SSBU.values())) == len(HIVEE_CODES)


@pytest.mark.skipif(not HIVEE_CHARACTERS.is_file(),
                    reason="Hivee's source is not next to this checkout")
def test_the_table_is_hivee_s_own_list():
    """Hivee's characters.ts: each fighter's code and nameEn. A fighter
    added there and not here (or renamed) fails this."""
    text = HIVEE_CHARACTERS.read_text(encoding="utf-8")
    pairs = re.findall(
        r"code:\s*'([a-z0-9_]+)',.*?nameEn:\s*'((?:[^'\\]|\\.)*)'", text,
        re.DOTALL)
    hivees = {code: name.replace("\\'", "'") for code, name in pairs}
    assert hivees == hivee.HIVEE_TO_SSBU


@pytest.mark.skipif(not SSBU_CONFIG.is_file(),
                    reason="the SSBU asset pack is not installed here")
def test_the_table_names_the_installed_config_s_characters():
    """Every name is a key of the SSBU config (what a main names a
    character by); the only config character with no Hivee code is
    Random."""
    config = orjson.loads(SSBU_CONFIG.read_bytes())
    keys = set(config["character_to_codename"])
    names = set(hivee.HIVEE_TO_SSBU.values())
    assert names - keys == set()
    assert keys - names == {"Random"}
    assert config["smashgg_game_id"] == hivee.SSBU_STARTGG_ID


def test_unknown_codes_are_left_out_of_the_mains(logs):
    assert hivee.Mains(["mario", "nope", "rob"], "ssbu") == {
        "ssbu": [["Mario", 0], ["R.O.B.", 0]]}
    assert hivee.Mains([], "ssbu") == {}
    assert hivee.Mains(None, "ssbu") == {}
    assert [m for m in logs if "unknown character" in m]


def test_an_unknown_code_is_warned_about_once(logs):
    """Every read of the match on stream carries its characters: a code
    the table lacks would log a line every 5 seconds."""
    for _ in range(3):
        hivee.Characters(["nope", "mario", "other"])
    warned = [m for m in logs if "unknown character" in m]
    assert len(warned) == 2
    assert any("'nope'" in m for m in warned)
    assert any("'other'" in m for m in warned)


def test_the_codename_is_the_loaded_ssbu_game_s(monkeypatch):
    """The mains and the Top 8's characters go under the loaded game's
    codename when it is SSBU (a mod of it with a codename of its own), else
    the first scanned SSBU game's, else "ssbu"."""
    from src.core.GameAssetCatalog import GameAssetCatalog

    def catalog(codename, game, first):
        return SimpleNamespace(
            Current=lambda: SimpleNamespace(codename=codename, game=game),
            GameFromStartGGId=lambda gameid: first)

    monkeypatch.setattr(GameAssetCatalog, "instance", catalog(
        "ssbu_mod", {"smashgg_game_id": 1386}, "ssbu"))
    assert HiveeDataProvider._Codename() == "ssbu_mod"
    monkeypatch.setattr(GameAssetCatalog, "instance", catalog(
        "ssbu_alt", {"smashgg_game_id": 4,
                     "alternate_versions": [{"smashgg_game_id": "1386"}]},
        "ssbu"))
    assert HiveeDataProvider._Codename() == "ssbu_alt"
    # Another game loaded: the first SSBU game scanned.
    monkeypatch.setattr(GameAssetCatalog, "instance", catalog(
        "sf6", {"smashgg_game_id": 43868}, "ssbu"))
    assert HiveeDataProvider._Codename() == "ssbu"
    monkeypatch.setattr(GameAssetCatalog, "instance", catalog(
        None, {}, None))
    assert HiveeDataProvider._Codename() == "ssbu"
    monkeypatch.setattr(GameAssetCatalog, "instance", None)
    assert HiveeDataProvider._Codename() == "ssbu"


# ── the set ──────────────────────────────────────────────────────────────

def test_the_match_on_stream_is_a_set_in_the_start_gg_shape(provider):
    data = provider.GetMatch("m-current")
    entrants = data.pop("entrants")
    assert data == {
        "id": "m-current",
        "team1score": 2, "team2score": 1,
        "round_name": "Winners Semi Final",   # no round names loaded here
        "round_name_en": "Winners Semi-Final",
        "tournament_phase": "SSBU シングルス",
        "tournament_pool": "",
        "bracket_type": "DOUBLE_ELIMINATION",
        "p1_name": "Alice", "p2_name": "Bob",
        "p1_seed": 1, "p2_seed": 4,
        "stream": "", "station": "台 3",
        "isOnline": False, "isPools": False, "round": 3,
        "team1losers": False, "team2losers": False,
        "bestOf": None,
        "has_selection_data": True,
    }
    # The first read: the characters are new, the seed / X / avatar too.
    assert entrants == [
        [{"gamerTag": "Alice", "prefix": "", "twitter": "alice_x",
          "avatar": "https://img.example.com/a.png", "seed": 1,
          "selection_changed": True,
          "mains": {"ssbu": [["Mario", 0], ["Fox", 0]]},
          "filled_in": {"seed": 1, "twitter": "alice_x",
                        "avatar": "https://img.example.com/a.png"}}],
        [{"gamerTag": "Bob", "prefix": "", "twitter": "", "avatar": "",
          "seed": 4, "selection_changed": True,
          "mains": {"ssbu": [["Luigi", 0]]}, "filled_in": {"seed": 4}}],
    ]


def test_a_queued_match_is_a_set_too_and_an_unknown_one_is_none(provider):
    data = provider.GetMatch("m-next")
    assert (data["id"], data["p1_name"], data["p2_name"]) == (
        "m-next", "Carol", "Dave")
    assert data["team1score"] is None and data["team2score"] is None
    assert data["has_selection_data"] is False
    assert "mains" not in data["entrants"][0][0]
    assert provider.GetMatch("m-gone") == {}


def test_the_round_name_is_translated_with_start_gg_s_spelling(
        provider, monkeypatch):
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    monkeypatch.setattr(AutonaLocaleHelper, "matchNames", {
        "winners_semi_final": "勝者側準決勝",
        "losers_quarter_final": "敗者側準々決勝",
        "grand_final": "グランドファイナル"})
    assert provider.GetMatch("m-current")["round_name"] == "勝者側準決勝"
    assert provider.GetMatch("m-next")["round_name"] == "敗者側準々決勝"
    assert hivee.RoundName("Grand Final") == "グランドファイナル"
    # Single elimination's labels are Hivee's own (Japanese): as they are.
    assert hivee.RoundName("準決勝") == "準決勝"
    assert hivee.RoundNameEn("準決勝") == ""


@pytest.mark.parametrize("match, expected", [
    ({"bracket": "grand_final", "roundLabel": "Grand Final"},
     "DOUBLE_ELIMINATION"),
    ({"bracket": "winners", "roundLabel": "Winners Round 2"},
     "DOUBLE_ELIMINATION"),
    ({"bracket": "winners", "roundLabel": "準々決勝"}, "SINGLE_ELIMINATION"),
    ({"bracket": "winners", "roundLabel": "R2"}, "SINGLE_ELIMINATION"),
    ({"bracket": "round_robin", "roundLabel": "総当たり（リーグ戦）"},
     "ROUND_ROBIN"),
    ({"bracket": "matchmaking", "roundLabel": "マッチメイキング"}, ""),
])
def test_the_bracket_type_follows_the_bracket_and_label(match, expected):
    assert hivee.BracketType(match) == expected


def test_a_pool_is_named_when_the_program_has_pools(provider, http,
                                                    monkeypatch):
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    http.answers["scoreboard"] = _scoreboard(
        current=_match("m-rr", _player("A"), _player("B"),
                       label="総当たり（リーグ戦）", bracket="round_robin",
                       poolNumber=2))
    monkeypatch.setattr(AutonaLocaleHelper, "phaseNames",
                        {"group": "予選プール{0}"})
    data = provider.GetMatch("m-rr")
    assert data["tournament_pool"] == "予選プール2"
    assert data["isPools"] is True
    # The phase names not loaded: a plain name all the same.
    monkeypatch.setattr(AutonaLocaleHelper, "phaseNames", {})
    assert provider.GetMatch("m-rr")["tournament_pool"] == "Pool 2"


def test_the_grand_final_s_losers_flags_come_through(provider, http):
    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-gf", _player("Alice", 0), _player("Bob", 0, losers=True),
        label="Grand Final", bracket="grand_final"))
    data = provider.GetMatch("m-gf")
    assert (data["team1losers"], data["team2losers"]) == (False, True)


def test_hivee_s_swap_mid_set_does_not_swap_the_board(provider, http):
    """Hivee's 左右入れ替え swaps the overlay's current match. The sides a
    match was first read with stay: an update after the swap still puts
    Alice's score under Alice."""
    first = provider.GetMatch("m-current")
    swapped = copy.deepcopy(CURRENT)
    swapped["playerA"], swapped["playerB"] = (
        swapped["playerB"], swapped["playerA"])
    swapped["playerA"]["score"] = 2          # Bob took a game
    http.answers["scoreboard"] = _scoreboard(current=swapped)
    again = provider.GetMatch("m-current")
    assert (again["p1_name"], again["p2_name"]) == (
        first["p1_name"], first["p2_name"])
    assert (again["team1score"], again["team2score"]) == (2, 2)
    assert again["entrants"][0][0]["gamerTag"] == "Alice"


def test_reads_close_together_ask_hivee_once(http):
    provider = HiveeDataProvider(OBS_URL, None)
    provider.GetMatch("m-current")
    provider.GetStreamQueue()
    provider.GetStreamMatchId("whatever")
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"] == f"{ORIGIN}/api/events/{EVENT}/overlay"
    assert call["params"] == {"token": TOKEN, "view": "scoreboard"}
    assert call["timeout"]


def test_reads_of_a_view_under_way_wait_for_its_answer(monkeypatch):
    """One request per view at a time, with no lock held while it waits
    for Hivee: a read of the same view that comes meanwhile takes its
    answer (even with no cache), and a read of another view goes ahead."""
    release = threading.Event()
    asking = threading.Event()
    waiting = threading.Event()
    views = []

    def get(url, params=None, timeout=None):
        views.append(params["view"])
        if params["view"] == "scoreboard":
            asking.set()
            assert release.wait(5)
            return _Response(200, _scoreboard())
        return _Response(200, {"event": {}, "top8": None})

    class _Watched(hivee._Flight):
        # Tells when a read starts waiting on the flight.
        def __init__(self):
            super().__init__()
            done = self.done
            self.done = SimpleNamespace(
                wait=lambda timeout=None: waiting.set() or done.wait(timeout),
                set=done.set)

    monkeypatch.setattr(hivee.requests, "get", get)
    monkeypatch.setattr(hivee, "_Flight", _Watched)
    monkeypatch.setattr(HiveeDataProvider, "CACHE_SECS", 0)
    provider = HiveeDataProvider(API_URL, None)
    answers = []

    def read():
        answers.append(provider.GetStreamMatchId("x"))

    first = threading.Thread(target=read)
    first.start()
    assert asking.wait(5)
    second = threading.Thread(target=read)
    second.start()
    assert waiting.wait(5)
    # The Top 8's view is not held up by the scoreboard's request.
    assert provider.GetTop8() is None
    release.set()
    first.join(5)
    second.join(5)
    assert answers == [{"id": "m-current"}] * 2
    assert views == ["scoreboard", "top8"]
    assert provider._flights == {}


def test_a_failed_read_is_kept_a_while(http, monkeypatch):
    """A Hivee that is down is asked once per FAILURE_SECS, not by every
    read (each would hold a job of the lane for up to the timeout)."""
    now = [100.0]
    monkeypatch.setattr(hivee, "time", SimpleNamespace(
        monotonic=lambda: now[0]))
    http.answers["scoreboard"] = 500
    provider = HiveeDataProvider(API_URL, None)
    assert provider.GetStreamMatchId("x") is None
    now[0] += provider.FAILURE_SECS - 0.1
    assert provider.GetMatch("m-current") == {}
    assert provider.GetStreamQueue() == {}
    assert len(http.calls) == 1
    http.answers["scoreboard"] = _scoreboard()
    now[0] += 0.2
    assert provider.GetStreamMatchId("x") == {"id": "m-current"}
    assert len(http.calls) == 2
    # An answer is kept for CACHE_SECS only.
    now[0] += provider.CACHE_SECS - 0.1
    provider.GetStreamQueue()
    assert len(http.calls) == 2
    now[0] += 0.2
    provider.GetStreamQueue()
    assert len(http.calls) == 3


# ── what is remembered of a match ────────────────────────────────────────

def test_a_match_that_left_the_stream_answers_its_last_read(provider, http):
    """A match that ended has left the stream and the queue: the board
    following it gets the score it was last read with, rather than nothing
    -- from a provider built again too (a forced reload)."""
    provider.GetMatch("m-current")
    ended = copy.deepcopy(CURRENT)
    ended["playerA"]["score"] = 3
    http.answers["scoreboard"] = _scoreboard(current=ended)
    assert provider.GetStreamQueue()          # read, not GetMatch'd
    http.answers["scoreboard"] = _scoreboard(current=None)
    data = provider.GetMatch("m-current")
    assert (data["id"], data["team1score"], data["team2score"]) == (
        "m-current", 3, 1)
    assert (data["p1_name"], data["p2_name"]) == ("Alice", "Bob")

    again = HiveeDataProvider(API_URL, None)
    assert again.GetMatch("M-CURRENT".lower())["team1score"] == 3
    # Another event's provider does not know it.
    assert HiveeDataProvider(API_URL.replace(EVENT, PROGRAM),
                             None).GetMatch("m-current") == {}
    # A read that failed answers nothing, not the memory.
    http.answers["scoreboard"] = 500
    assert provider.GetMatch("m-current") == {}


@pytest.mark.parametrize("noName", ["未定", "（不明）", None])
def test_a_side_read_with_no_name_is_filled_in(provider, http, noName):
    """An undecided seat ("未定") and a hidden name ("（不明）") are no
    name. The sides recorded then take the name once it comes, so Hivee's
    swap is undone from then on -- across a forced reload too."""
    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-new", _player("Alice"), _player(noName)))
    data = provider.GetMatch("m-new")
    assert (data["p1_name"], data["p2_name"]) == ("Alice", "")
    assert data["entrants"][1][0]["gamerTag"] == ""

    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-new", _player("Alice", 0), _player("Bob", 1)))
    provider.GetMatch("m-new")
    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-new", _player("Bob", 1), _player("Alice", 0)))
    data = HiveeDataProvider(API_URL, None).GetMatch("m-new")
    assert (data["p1_name"], data["p2_name"]) == ("Alice", "Bob")
    assert (data["team1score"], data["team2score"]) == (0, 1)


def test_hivee_s_swap_is_undone_with_one_name_known(provider, http):
    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-new", _player("Alice"), _player("未定")))
    provider.GetMatch("m-new")
    # Swapped in Hivee as the other seat was decided.
    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-new", _player("Bob", 1), _player("Alice", 0)))
    data = provider.GetMatch("m-new")
    assert (data["p1_name"], data["p2_name"]) == ("Alice", "Bob")
    assert (data["team1score"], data["team2score"]) == (0, 1)


def test_an_update_carries_what_changed_since_the_last_read(provider, http):
    """A queued match loaded first has no X handle, avatar or seed (Hivee
    hands them out for the match on stream only): they come as `filled_in`
    once it is on stream. The characters are `selection_changed` only when
    they changed, so an update leaves a picked skin alone."""
    queued = _match("m-1", _player("Carol", 0, characters=["mario"]),
                    _player("Dave", 0))
    http.answers["scoreboard"] = _scoreboard(current=None, queue=(queued,))
    first = provider.GetMatch("m-1")
    carol, dave = first["entrants"][0][0], first["entrants"][1][0]
    assert (carol["selection_changed"], dave["selection_changed"]) == (
        True, False)
    assert first["has_selection_data"] is True
    assert "filled_in" not in carol and "filled_in" not in dave

    again = provider.GetMatch("m-1")
    assert again["has_selection_data"] is False
    assert [team[0]["selection_changed"] for team in again["entrants"]] == [
        False, False]

    live = _match("m-1", _player("Carol", 1, 2, ["mario"], x="carol_x",
                                 avatar="https://img.example.com/c.png"),
                  _player("Dave", 0, 3, ["fox"]))
    http.answers["scoreboard"] = _scoreboard(current=live, queue=())
    data = provider.GetMatch("m-1")
    carol, dave = data["entrants"][0][0], data["entrants"][1][0]
    assert carol["filled_in"] == {
        "seed": 2, "twitter": "carol_x",
        "avatar": "https://img.example.com/c.png"}
    assert dave["filled_in"] == {"seed": 3}
    assert (carol["selection_changed"], dave["selection_changed"]) == (
        False, True)
    assert data["has_selection_data"] is True
    assert (data["p1_seed"], data["p2_seed"]) == (2, 3)

    # Back in the queue (Hivee no longer hands them out): kept.
    back = _match("m-1", _player("Carol", 1, None, ["mario"]),
                  _player("Dave", 0, None, ["fox"]))
    http.answers["scoreboard"] = _scoreboard(current=None, queue=(back,))
    data = provider.GetMatch("m-1")
    carol = data["entrants"][0][0]
    assert (carol["seed"], carol["twitter"], carol["avatar"]) == (
        2, "carol_x", "https://img.example.com/c.png")
    assert "filled_in" not in carol
    assert data["has_selection_data"] is False


def test_the_follow_s_update_writes_only_what_the_provider_marks():
    """SetFollowService._PlayerUpdate: start.gg's player (no marks) gets
    its mains whenever the set has a selection, as before; a marked one
    only when its characters changed, plus what was filled in."""
    from src.core.SetFollowService import SetFollowService

    update = SetFollowService._PlayerUpdate
    mains = {"ssbu": [["Mario", 0]]}
    startgg = {"gamerTag": "A", "mains": mains}
    assert update({"has_selection_data": True}, startgg, False) == {
        "mains": mains}
    assert update({"has_selection_data": False}, startgg, False) == {}
    assert update({"has_selection_data": True}, startgg, True) == {}
    same = dict(startgg, selection_changed=False)
    assert update({"has_selection_data": True}, same, False) == {}
    filled = dict(same, filled_in={"seed": 2, "twitter": "x"})
    assert update({"has_selection_data": True}, filled, False) == {
        "seed": 2, "twitter": "x"}
    changed = dict(filled, selection_changed=True)
    assert update({"has_selection_data": True}, changed, False) == {
        "seed": 2, "twitter": "x", "mains": mains}


# ── the set ids ──────────────────────────────────────────────────────────

def test_a_hivee_match_id_is_a_set_id_and_start_gg_s_stay_numbers():
    from src import AutonaWebOperations as WebOps

    assert WebOps.SetId("123") == 123
    assert WebOps.SetId(7) == 7
    assert WebOps.SetId(" 0F8E2A4C-1B3D-4E5F-8A9B-0C1D2E3F4A5B ") == EVENT
    for bad in ("abc", "", "12-34", None):
        with pytest.raises(ValueError):
            WebOps.SetId(bad)


def test_the_web_s_set_lookups_take_a_hivee_match_id(autonaApp, server,
                                                     monkeypatch):
    """/get-match-<id>, Socket.IO's get_match and v1's GET /sets/<id>:
    a uuid reaches the provider as it is, a number as an int."""
    import json

    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider

    asked = []
    monkeypatch.setattr(
        AutonaTournamentDataProvider.instance, "provider",
        SimpleNamespace(url=API_URL, GetMatch=lambda setId: asked.append(
            setId) or {"id": setId}))
    app, socketio = server
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = "127.0.0.1"

    assert client.get(f"/get-match-{EVENT}").get_json() == {"id": EVENT}
    body = client.get(f"/api/v1/sets/{EVENT.upper()}").get_json()
    assert body["data"] == {"id": EVENT}
    assert client.get("/api/v1/sets/42").get_json()["data"] == {"id": 42}
    refused = client.get("/api/v1/sets/not-a-set")
    assert refused.status_code == 400
    assert refused.get_json()["error"]["code"] == "INVALID_ARGUMENT"

    ws = socketio.test_client(app, flask_test_client=client)
    ws.get_received()
    ws.emit("get_match", json.dumps({"setId": EVENT}), callback=True)
    assert [(item["name"], item["args"]) for item in ws.get_received()] == [
        ("get_match", [{"id": EVENT}])]
    ws.disconnect()
    assert asked == [EVENT, EVENT, 42, EVENT]


# ── the token stays out of the READ APIs ─────────────────────────────────

def test_the_lists_show_the_tournament_url_masked(autonaApp, monkeypatch):
    """The match list's and the set selection's rows are matched to the
    loaded tournament by the provider's own URL, and shown with the token
    masked."""
    from src import AutonaWebOperations as WebOps
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.core.AnalysisService import AnalysisService
    from src.core.SetSelectionService import SetSelectionService

    monkeypatch.setattr(AutonaTournamentDataProvider.instance, "provider",
                        SimpleNamespace(url=API_URL))
    monkeypatch.setattr(AnalysisService, "instance", SimpleNamespace(
        Result=lambda: {"url": API_URL, "rows": ["row"], "revision": 1},
        ResultRef=lambda: (API_URL, ["row"], 1)))
    monkeypatch.setattr(SetSelectionService, "instance", SimpleNamespace(
        Result=lambda kind: {"url": API_URL, "rows": ["row"],
                             "fetchedAt": 1, "query": None}))
    masked = HiveeUrl.Mask(API_URL)
    analysis = WebOps.get_analysis()
    assert (analysis["url"], analysis["rows"]) == (masked, ["row"])
    sets = WebOps.get_tournament_phases()
    assert (sets["url"], sets["rows"]) == (masked, ["row"])
    assert TOKEN not in repr((analysis, sets))


# ── the last sets ────────────────────────────────────────────────────────

def test_the_last_sets_answer_none_without_a_request(provider, http):
    """Asked of any provider (StatsService._RefreshLastSets): none here,
    so the board's are emptied, and Hivee is not asked."""
    answered = []
    provider.GetLastSets(playerID=5, playerNumber="1",
                         callback=SimpleNamespace(emit=answered.append),
                         progress_callback=None, cancel_event=None)
    assert answered == [{"playerNumber": "1", "last_sets": []}]
    assert http.calls == []


# ── the stream follow ────────────────────────────────────────────────────

def test_the_stream_s_set_is_the_match_on_stream_whatever_its_name(
        provider, http):
    assert provider.GetStreamMatchId("twitch_channel") == {"id": "m-current"}
    http.answers["scoreboard"] = _scoreboard(current=None)
    assert provider.GetStreamMatchId("x") == {"id": "m-next"}
    http.answers["scoreboard"] = _scoreboard(current=None, queue=())
    assert provider.GetStreamMatchId("x") is None
    assert provider.GetStationMatchsId(1) == []


def test_the_event_is_one_stream_row(provider):
    assert provider.GetStations() == [{
        "type": "stream", "id": None, "identifier": "Autona Cup",
        "stream": "Autona Cup"}]
    assert provider.GetStations(strict=True) == provider.GetStations()


def test_the_stream_queue_is_the_match_on_stream_then_the_queue(provider):
    queue = provider.GetStreamQueue()
    # A fixed key: the event's title is no state key (streamQueue.<it>).
    assert list(queue) == ["hivee"]
    sets = queue["hivee"]
    assert list(sets) == ["1", "2"]
    assert sets["1"]["id"] == "m-current"
    assert sets["2"]["id"] == "m-next"
    assert sets["1"]["team"]["1"]["player"]["1"]["name"] == "Alice"
    assert sets["1"]["team"]["1"]["seed"] == 1
    assert sets["2"]["match"] == "Losers Quarter Final"
    assert set(sets["1"]) == {
        "id", "match", "phase", "pool", "best_of", "best_of_text", "state",
        "team", "station", "event", "isCurrentEvent"}


# ── the tournament ───────────────────────────────────────────────────────

def test_the_tournament_is_the_event_s_title_and_ssbu(provider):
    assert provider.videogame is None
    assert provider.GetTournamentData() == {
        "_videogame": hivee.SSBU_STARTGG_ID, "tournamentName": "Autona Cup"}
    assert provider.videogame == hivee.SSBU_STARTGG_ID
    assert provider.GetIconURL() == "https://img.example.com/logo.png"
    assert provider.GetBannerURL(strict=True) is None
    assert provider.name == "Hivee"


def test_what_the_overlay_does_not_carry_answers_empty(provider, http):
    failed = []
    assert provider.GetMatches(strict=True,
                               failed_pages=failed.append) == []
    assert failed == []
    assert provider.GetTournamentPhases(strict=True) == []
    assert provider.GetTournamentPhaseGroup(1) == {}
    assert provider.GetStandings(8) == []
    assert http.calls == []


def test_the_entrants_are_the_whole_player_db(provider, monkeypatch):
    import threading

    from src.AutonaPlayerDB import AutonaPlayerDB
    cleared = []
    monkeypatch.setattr(AutonaPlayerDB, "ClearTournamentPlayers",
                        staticmethod(lambda: cleared.append(1)))
    provider.GetEntrants(cancel_event=threading.Event())
    assert cleared == [1]
    stopped = threading.Event()
    stopped.set()
    provider.GetEntrants(cancel_event=stopped)
    assert cleared == [1]


def test_the_adapter_loads_a_hivee_url_with_this_provider(autonaApp,
                                                          monkeypatch):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    adapter = AutonaTournamentDataProvider()
    for name in ("GetTournamentData", "GetTournamentPhases",
                 "_StartEntrants", "_ForgetAnalysis", "_ForgetSetSelection"):
        monkeypatch.setattr(adapter, name, lambda *a, **kw: None)
    assert adapter.LoadTournament(API_URL) is True
    assert isinstance(adapter.provider, HiveeDataProvider)
    assert adapter.provider.target.token == TOKEN

    selected = []
    adapter.selectGameFromStartGGId = selected.append
    adapter.SetGameFromProvider()
    assert selected == []                  # no data yet: no game
    adapter.provider.videogame = hivee.SSBU_STARTGG_ID
    adapter.SetGameFromProvider()
    assert selected == [hivee.SSBU_STARTGG_ID]


# ── on the board ─────────────────────────────────────────────────────────

def test_the_set_lands_on_the_board_through_the_core(autonaApp, provider,
                                                     http, monkeypatch):
    """GetMatch's set through core's set apply (SetFollowService
    .ApplySetPayload, a full load): the names, the scores, the round, the
    characters of the set on the loaded game."""
    from tests.conftest import HARNESS_GAME, apply_set

    autonaApp.loadCharacters()
    board = autonaApp.ready(1)
    monkeypatch.setattr(HiveeDataProvider, "_Codename",
                        staticmethod(lambda: HARNESS_GAME))
    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-gf", _player("Alice", 2, 1, ["peach"]),
        _player("Bob", 1, 2, ["luigi", "mario"], losers=True),
        label="Grand Final", bracket="grand_final"))
    data = provider.GetMatch("m-gf")
    data.update({"overwrite": True, "no_mains": False})
    apply_set(board, data)
    autonaApp.settle()

    team = f"score.{board}.team"
    assert autonaApp.get(f"{team}.1.player.1.name") == "Alice"
    assert autonaApp.get(f"{team}.2.player.1.name") == "Bob"
    assert autonaApp.get(f"{team}.1.score") == 2
    assert autonaApp.get(f"{team}.2.score") == 1
    assert autonaApp.get(f"{team}.2.losers") is True
    assert autonaApp.get(f"{team}.1.player.1.seed") == 1
    assert autonaApp.get(f"score.{board}.match") == \
        hivee.RoundName("Grand Final")
    assert autonaApp.get(f"score.{board}.match_en") == "Grand Final"
    alice = autonaApp.get(f"{team}.1.player.1.character") or {}
    bob = autonaApp.get(f"{team}.2.player.1.character") or {}
    assert [c.get("name") for c in alice.values()][:1] == ["Peach"]
    assert [c.get("name") for c in bob.values()][:1] == ["Luigi"]


def _firstSkin(autonaApp, slot):
    # The character slots are keyed by int in the state.
    rows = list((autonaApp.get(f"{slot}.character") or {}).values())
    return rows[0].get("skin") if rows else None


def test_an_update_fills_the_board_in_and_keeps_a_picked_skin(
        autonaApp, provider, http, monkeypatch):
    """The follow's update (overwrite False) of a match loaded from the
    queue: once on stream, the seed, X handle and avatar land on the board;
    a player whose characters did not change keeps the skin the operator
    picked, and one who picked a character gets it."""
    from src.core.PlayerService import PlayerService
    from tests.conftest import HARNESS_GAME, apply_set

    autonaApp.loadCharacters()
    board = autonaApp.ready(1)
    monkeypatch.setattr(HiveeDataProvider, "_Codename",
                        staticmethod(lambda: HARNESS_GAME))
    carol = f"score.{board}.team.1.player.1"
    dave = f"score.{board}.team.2.player.1"

    http.answers["scoreboard"] = _scoreboard(current=None, queue=(_match(
        "m-q", _player("Carol", 0, characters=["peach"]),
        _player("Dave", 0)),))
    data = provider.GetMatch("m-q")
    data.update({"overwrite": True, "no_mains": False})
    apply_set(board, data)
    autonaApp.settle()
    assert not autonaApp.get(f"{carol}.seed")
    assert not autonaApp.get(f"{carol}.twitter")
    assert PlayerService(carol).EditCharacter(1, skin=2) is None
    autonaApp.settle()
    assert _firstSkin(autonaApp, carol) == 2

    http.answers["scoreboard"] = _scoreboard(current=_match(
        "m-q", _player("Carol", 1, 2, ["peach"], x="carol_x",
                       avatar="https://img.example.com/c.png"),
        _player("Dave", 0, 3, ["luigi"])), queue=())
    data = provider.GetMatch("m-q")
    data.update({"overwrite": False, "no_mains": False})
    apply_set(board, data)
    autonaApp.settle()

    assert autonaApp.get(f"score.{board}.team.1.score") == 1
    assert autonaApp.get(f"{carol}.seed") == 2
    assert autonaApp.get(f"{carol}.twitter") == "carol_x"
    assert autonaApp.get(f"{carol}.online_avatar") == \
        "https://img.example.com/c.png"
    assert _firstSkin(autonaApp, carol) == 2
    assert autonaApp.get(f"{carol}.name") == "Carol"
    assert autonaApp.get(f"{dave}.seed") == 3
    picked = autonaApp.get(f"{dave}.character") or {}
    assert [c.get("name") for c in picked.values()][:1] == ["Luigi"]

    # The next update has nothing new: the skin still stays.
    data = provider.GetMatch("m-q")
    assert data["has_selection_data"] is False
    data.update({"overwrite": False, "no_mains": False})
    apply_set(board, data)
    autonaApp.settle()
    assert _firstSkin(autonaApp, carol) == 2


# ── the Top 8 ────────────────────────────────────────────────────────────

def _seat(name, characters=(), dq=False):
    return {"name": name, "dq": dq, "characters": list(characters)}


def _card(label, a, b, scores=(None, None), winner=None):
    return {"label": label, "players": [a, b], "scores": list(scores),
            "winner": winner, "winnerToSeat": None}


def _cards(swap):
    """A Top 8 halfway through: WSF1 A beats B, WSF2 D beats C, LR1 E beats
    F and H beats G, the losers' side up to the losers final played (D wins
    it), the grand final to come. `swap`: Hivee's lqf1 holds WSF1's loser
    (Top8Model's lqf2), else WSF2's."""
    A, B, C, D = (_seat("A", ["mario"]), _seat("B"), _seat("C"),
                  _seat("D", ["fox"]))
    E, F, G, H = _seat("E"), _seat("F"), _seat("G"), _seat("H", ["kirby"])
    cards = {
        "wsf1": _card("Winners Semi Final", A, B, (3, 1), 0),
        "wsf2": _card("Winners Semi Final", C, D, (2, 3), 1),
        "wf": _card("Winners Final", _seat("A", ["luigi"]), D, (3, 0), 0),
        "gf": _card("Grand Final", _seat("A"), _seat("D")),
        "gf_reset": None,
        "lsf": _card("Losers Semi Final", B, C, (2, 1), 0),
        "lf": _card("Losers Final", D, B, (3, 1), 0),
    }
    if swap:
        cards.update({
            "lr1a": _card("Losers Round 1", E, F, (2, 0), 0),
            "lqf1": _card("Losers Quarter Final", B, E, (2, 0), 0),
            "lr1b": _card("Losers Round 1", G, H, (1, 2), 1),
            "lqf2": _card("Losers Quarter Final", C, H, (2, 1), 0),
        })
    else:
        cards.update({
            "lr1a": _card("Losers Round 1", G, H, (1, 2), 1),
            "lqf1": _card("Losers Quarter Final", H, C, (1, 2), 1),
            "lr1b": _card("Losers Round 1", E, F, (2, 0), 0),
            "lqf2": _card("Losers Quarter Final", E, B, (0, 2), 1),
        })
    return cards


@pytest.mark.parametrize("swap", [True, False], ids=["swapped", "as-is"])
def test_the_top8_cards_land_on_the_fixed_topology(swap):
    players, matches = hivee.Top8FromCards(_cards(swap), "ssbu")
    names = {slot: p["name"] for slot, p in players.items()}
    # G/H play LR1A (into Top8Model's lqf1, with WSF2's loser), E/F LR1B.
    assert names == {"1": "A", "2": "B", "3": "C", "4": "D",
                     "5": "G", "6": "H", "7": "E", "8": "F"}
    # The latest set with characters wins (A played Luigi in the WF).
    assert players["1"]["mains"] == {"ssbu": [["Luigi", 0]]}
    assert players["6"]["mains"] == {"ssbu": [["Kirby", 0]]}
    assert players["2"]["mains"] == {}
    assert players["1"]["country_code"] is None

    def result(mid):
        m = matches[mid]
        return m["slots"], m["score"], m["finished"]

    assert result("wsf1") == ([1, 2], [3, 1], True)
    assert result("wsf2") == ([3, 4], [2, 3], True)
    assert result("lr1a") == ([5, 6], [1, 2], True)
    assert result("lr1b") == ([7, 8], [2, 0], True)
    assert result("lqf1") == ([6, 3], [1, 2], True)
    assert result("lqf2") == ([7, 2], [0, 2], True)
    assert result("lsf") == ([3, 2], [1, 2], True)
    assert result("wf") == ([1, 4], [3, 0], True)
    assert result("lf") == ([2, 4], [1, 3], True)
    assert result("gf") == ([1, 4], [0, 0], False)
    assert matches["gf_reset"]["active"] is False


def test_a_dq_is_a_one_nil_to_the_winner():
    cards = _cards(True)
    cards["wsf2"] = _card("Winners Semi Final", _seat("C", dq=True),
                          _seat("D"), (None, None), 1)
    _players, matches = hivee.Top8FromCards(cards, "ssbu")
    assert matches["wsf2"]["score"] == [0, 1]
    assert matches["wsf2"]["finished"] is True


def test_before_the_wsf_hivee_s_order_is_taken(logs):
    cards = _cards(False)
    for mid in ("wsf1", "wsf2"):
        cards[mid] = dict(cards[mid], winner=None, scores=[None, None])
    players, matches = hivee.Top8FromCards(cards, "ssbu")
    assert [players[s]["name"] for s in ("5", "6", "7", "8")] == [
        "G", "H", "E", "F"]
    assert matches["wsf1"]["finished"] is False
    assert [m for m in logs if "routing not known" in m]


def test_get_top8_reads_the_top8_view_with_the_program(http, monkeypatch):
    http.answers["top8"] = {"event": {}, "top8": {
        "status": "ok", "programName": "SSBU", "cards": _cards(True)}}
    url = f"{ORIGIN}/overlay/{EVENT}/top8?token={TOKEN}&program={PROGRAM}"
    provider = HiveeDataProvider(HiveeUrl.Normalize(url), None)
    players, matches = provider.GetTop8()
    assert players["1"]["name"] == "A"
    assert http.calls[-1]["params"] == {
        "token": TOKEN, "view": "top8", "program": PROGRAM}


@pytest.mark.parametrize("top8_", [
    {"status": "not_double_elim", "programName": "SSBU"},
    {"status": "no_program", "programName": None},
    None,
])
def test_no_top8_to_import_is_none(http, top8_):
    http.answers["top8"] = {"event": {}, "top8": top8_}
    assert HiveeDataProvider(API_URL, None).GetTop8() is None


def test_the_top8_import_takes_the_provider_s_own_top8(monkeypatch):
    from src.core import Top8Service
    imported = []
    monkeypatch.setattr(Top8Service, "ImportExtracted",
                        lambda players, matches: imported.append(
                            (players, matches)) or True)
    extracted = ({"1": {"name": "A"}}, {"wf": {}})

    class _Provider:
        def GetTop8(self):
            return extracted

        def GetTournamentPhases(self):
            raise AssertionError("not asked for phases")

    assert Top8Service.ImportFromProvider(_Provider()) is True
    assert imported == [extracted]

    class _Nothing(_Provider):
        def GetTop8(self):
            return None

    assert Top8Service.ImportFromProvider(_Nothing()) is False
    assert imported == [extracted]


def test_the_top8_lands_in_the_state_through_the_import(autonaApp,
                                                        monkeypatch):
    """GetTop8's answer through Top8Service's import (the players resolved
    on the loaded game, then ImportBracket)."""
    from src.core import Top8Service
    from src.core.StateManager import StateManager
    from tests.conftest import HARNESS_GAME

    autonaApp.loadCharacters()
    saved = copy.deepcopy(StateManager.Get("top8"))
    try:
        players, matches = hivee.Top8FromCards(_cards(True), HARNESS_GAME)
        assert Top8Service.ImportExtracted(players, matches) is True
        autonaApp.settle()
        assert StateManager.Get("top8.players.1.name") == "A"
        assert StateManager.Get(
            "top8.players.1.characters.1.en_name") == "Luigi"
        assert StateManager.Get("top8.matches.lf.score") == [1, 3]
        assert StateManager.Get("top8.matches.gf.slots") == [1, 4]
    finally:
        if saved is not None:
            StateManager.Set("top8", saved)
        autonaApp.settle()
