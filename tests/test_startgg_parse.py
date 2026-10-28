"""Characterization tests for StartGGDataProvider parse functions.

These tests capture the current behavior of ParseMatchDataNewApi,
ParseMatchDataOldApi, and ProcessEntrantData so that refactoring
can be verified against known-good outputs.

Strategy: the provider's own dependencies (player DB, workers, qtpy, ...)
are stubbed via sys.modules while the module is exec'd, so the parse
functions can be exercised as pure data transforms. The stubs are removed
again immediately afterwards -- they are an isolation device for this module
only and must never be visible to another test module (src.* imports
normally these days).

Character and stage lookups are NOT stubbed: since ADR-005 S1b the provider
resolves start.gg ids through the Qt-free core GameAssetCatalog, and these
tests hand it a real one with the synthetic game of
tests/fixtures/game_assets (the S1a golden fixture) loaded. Its start.gg
tables: characters 1 Mario / 2 Luigi / 3 Bowser (4 Ryu exists only in the
other game, 5 in no pack at all), stages 311 Battlefield / 328 Final
Destination (codename "fd") / 484 Smashville.

Neither are country and region lookups (ADR-005 S1c): the provider reads the
Qt-free core CountryCatalog, and these tests hand it a real one loaded from
the shipped assets/ (country names, regions and the city -> region table).
"""
import os
import shutil
import sys
import types
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. The real deep_get (no Qt dependency) backs the stubbed dict helper, and
#    the real (Qt-free) game asset catalog is what the provider resolves
#    start.gg ids against
# ---------------------------------------------------------------------------
from src.core.CountryCatalog import CountryCatalog, state_flag_path
from src.core.DictHelper import deep_get
from src.core.GameAssetCatalog import GameAssetCatalog, GameAssetSnapshot
from src.core.TaskExecutor import TaskExecutor
from tests.conftest import _link_tree

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GAME_ASSETS = PROJECT_ROOT / "tests" / "fixtures" / "game_assets"


# ---------------------------------------------------------------------------
# 2. Build mock module tree so StartGGDataProvider can import
# ---------------------------------------------------------------------------
def _make_mock_module(name, attrs=None, is_package=False):
    mod = types.ModuleType(name)
    if is_package:
        mod.__path__ = []
        mod.__package__ = name
    else:
        mod.__package__ = name.rsplit(".", 1)[0] if "." in name else name
    if attrs:
        for k, v in attrs.items():
            setattr(mod, k, v)
    return mod


# --- AutonaLocaleHelper ---
class _MockAutonaLocaleHelper:
    phaseNames = {"group": "Pool {}"}
    matchNames = {
        "grand_final_reset": "Grand Final Reset",
        "grand_final": "Grand Final",
        "winners_final": "Winners Final",
        "winners_semi_final": "Winners Semi-Final",
        "winners_quarter_final": "Winners Quarter-Final",
        "losers_final": "Losers Final",
        "losers_semi_final": "Losers Semi-Final",
        "losers_quarter_final": "Losers Quarter-Final",
        "winners_round": "Winners Round {}",
        "losers_round": "Losers Round {}",
        "round": "Round {}",
    }


# --- AutonaStatusManager (lazily imported inside GetMatches) ---
class _MockStatusManager:
    @staticmethod
    def push(message, **kwargs):
        return object()

    @staticmethod
    def update(token, message=None, **kwargs):
        pass

    @staticmethod
    def pop(token):
        pass


# --- Minimal QObject mock ---
class _MockQObject:
    def __init__(self, *args, **kwargs):
        pass


# --- TournamentDataProvider base class ---
class _MockTournamentDataProvider(_MockQObject):
    def __init__(self, url=None, threadpool=None, tshTdp=None):
        super().__init__()
        self.name = ""
        self.url = url
        self.entrants = []
        self.tournamentData = {}
        self.threadpool = threadpool
        self.videogame = None
        self.tshTdp = tshTdp


# --- Pre-populate sys.modules ---
_original_modules = {}

def _setup_mock_modules():
    """Inject mock modules into sys.modules."""
    mocks = {
        "src": _make_mock_module("src", is_package=True),
        "src.Helpers": _make_mock_module("src.Helpers", is_package=True),
        "src.core": _make_mock_module("src.core", is_package=True),
        # The real class: the tests below set its `instance` to a catalog
        # loaded from the shipped assets.
        "src.core.CountryCatalog": _make_mock_module(
            "src.core.CountryCatalog",
            {"CountryCatalog": CountryCatalog},
        ),
        "src.core.DictHelper": _make_mock_module(
            "src.core.DictHelper",
            {"deep_get": deep_get},
        ),
        # The real (Qt-free) class: QueryRequests reads its `stopping`, and
        # the entrants fetch is submitted to it (ADR-005 S3f-2).
        "src.core.TaskExecutor": _make_mock_module(
            "src.core.TaskExecutor",
            {"TaskExecutor": TaskExecutor},
        ),
        # The real class: the tests below set its `instance` to a catalog
        # with the fixture game loaded.
        "src.core.GameAssetCatalog": _make_mock_module(
            "src.core.GameAssetCatalog",
            {"GameAssetCatalog": GameAssetCatalog},
        ),
        "src.Helpers.AutonaDirHelper": _make_mock_module(
            "src.Helpers.AutonaDirHelper",
            {"AutonaResolve": lambda x: x},
        ),
        "src.Helpers.AutonaQtHelper": _make_mock_module(
            "src.Helpers.AutonaQtHelper",
            {"gui_thread_sync": MagicMock()},
        ),
        "src.Helpers.AutonaLocaleHelper": _make_mock_module(
            "src.Helpers.AutonaLocaleHelper",
            {"AutonaLocaleHelper": _MockAutonaLocaleHelper},
        ),
        "src.Helpers.AutonaStatusManager": _make_mock_module(
            "src.Helpers.AutonaStatusManager",
            {"AutonaStatusManager": _MockStatusManager},
        ),
        "src.AutonaPlayerDB": _make_mock_module(
            "src.AutonaPlayerDB",
            {"AutonaPlayerDB": MagicMock()},
        ),
        # The entrants worker's DB writes (ADR-005 S3e-1); not exercised here.
        "src.core.PlayerDB": _make_mock_module(
            "src.core.PlayerDB",
            {"PlayerDB": MagicMock()},
        ),
        "src.Workers": _make_mock_module(
            "src.Workers",
            {"Worker": MagicMock()},
        ),
        "src.TournamentDataProvider": _make_mock_module(
            "src.TournamentDataProvider", is_package=True,
        ),
        "src.TournamentDataProvider.TournamentDataProvider": _make_mock_module(
            "src.TournamentDataProvider.TournamentDataProvider",
            {"TournamentDataProvider": _MockTournamentDataProvider},
        ),
        # qtpy stubs
        "qtpy": _make_mock_module("qtpy"),
        "qtpy.QtCore": _make_mock_module("qtpy.QtCore", {"QObject": _MockQObject}),
    }

    for name, mod in mocks.items():
        _original_modules[name] = sys.modules.get(name)
        sys.modules[name] = mod


def _teardown_mock_modules():
    for name, original in _original_modules.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    _original_modules.clear()


# ---------------------------------------------------------------------------
# 3. Import StartGGDataProvider with mocked dependencies
#    We need proper package context for relative imports to resolve.
# ---------------------------------------------------------------------------
_setup_mock_modules()
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "src.TournamentDataProvider.StartGGDataProvider",
        PROJECT_ROOT / "src" / "TournamentDataProvider" / "StartGGDataProvider.py",
    )
    _startgg_mod = _ilu.module_from_spec(_spec)
    _startgg_mod.__package__ = "src.TournamentDataProvider"
    _PROVIDER_NAME = "src.TournamentDataProvider.StartGGDataProvider"
    _original_modules[_PROVIDER_NAME] = sys.modules.get(_PROVIDER_NAME)
    sys.modules[_PROVIDER_NAME] = _startgg_mod
    _spec.loader.exec_module(_startgg_mod)
    StartGGDataProvider = _startgg_mod.StartGGDataProvider
finally:
    # None of the stubs may outlive this module's import. Test modules
    # collected after this one import the real qtpy and the real src.*
    # modules, and would otherwise get these mocks handed to them. The
    # provider bound its dependencies (mock QObject, mock asset manager, ...)
    # at exec time, so dropping the sys.modules entries costs nothing here.
    # The one thing resolved later is the lazy
    # `from ..Helpers.AutonaStatusManager import AutonaStatusManager` inside
    # GetMatches(); it then loads the real module, which is Qt-only, needs no
    # QApplication and emits into the void with no receivers connected.
    _teardown_mock_modules()


# ---------------------------------------------------------------------------
# 4. Helper: create a provider instance without real network/Qt
# ---------------------------------------------------------------------------
def _make_provider(**kwargs):
    """Create a StartGGDataProvider instance with mock dependencies."""
    p = StartGGDataProvider.__new__(StartGGDataProvider)
    p.name = "StartGG"
    p.url = ""
    p.entrants = []
    p.tournamentData = {}
    p.threadpool = None
    p.videogame = None
    p.tshTdp = None
    p.player_seeds = kwargs.get("player_seeds", {})
    p.numEntrants = kwargs.get("numEntrants", 0)
    return p


# ---------------------------------------------------------------------------
# 5. The game asset catalog the provider resolves start.gg ids against
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _fixtureCatalog(tmp_path_factory):
    """A real GameAssetCatalog with the fixture game "tgame" loaded.

    Built through the production writers (scan, start.gg table, load) under
    the default locale, in a throwaway CWD: they read ./user_data and write
    ./out/css, and the load publishes `game` into the global StateManager,
    which is put back afterwards. Everything the provider asks it later is
    in memory, so the tests themselves need neither.
    """
    from src.core.DictHelper import deep_clone
    from src.core.StateManager import StateManager as SM

    work = tmp_path_factory.mktemp("startgg_catalog")
    (work / "out").mkdir()
    shutil.copytree(GAME_ASSETS / "games", work / "user_data" / "games")
    shutil.copy(GAME_ASSETS / "characters.json",
                work / "user_data" / "characters.json")
    previousDir = os.getcwd()
    with SM.lock:
        saved = (deep_clone(SM.state), deep_clone(SM.lastSavedState),
                 SM.revision, list(SM.changedKeys))
    os.chdir(work)
    try:
        catalog = GameAssetCatalog()
        catalog.ScanGames()
        catalog.ReloadStartGGCharacters()
        assert catalog.SelectGame("tgame") is not None
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state, SM.lastSavedState, SM.revision, SM.changedKeys = saved
        os.chdir(previousDir)
    return catalog


@pytest.fixture(autouse=True)
def _catalog(_fixtureCatalog, _fixtureCountries, monkeypatch):
    """Every test resolves against the fixture catalogs (put back after)."""
    monkeypatch.setattr(GameAssetCatalog, "instance", _fixtureCatalog)
    monkeypatch.setattr(CountryCatalog, "instance", _fixtureCountries)
    return _fixtureCatalog


@pytest.fixture(scope="module")
def _fixtureCountries(tmp_path_factory):
    """A real CountryCatalog loaded from the shipped country data.

    Loaded in a throwaway CWD with assets/ linked in, because a load also
    guarantees ./user_data/additional_flag. The city -> region table is
    read here as well (FindState reads it on first use); after that every
    answer the provider asks for is in memory.
    """
    work = tmp_path_factory.mktemp("startgg_countries")
    _link_tree(work / "assets", PROJECT_ROOT / "assets")
    previousDir = os.getcwd()
    os.chdir(work)
    try:
        catalog = CountryCatalog()
        assert catalog.Load() is True
        assert catalog.FindState("JP", "Tokyo") == "13"
    finally:
        os.chdir(previousDir)
        # The junction is removed by hand, never followed (see conftest).
        os.rmdir(work / "assets")
    return catalog


# ===========================================================================
# Test fixtures
# ===========================================================================

def _new_api_set_basic():
    """Minimal valid set for ParseMatchDataNewApi — 1v1 with player data."""
    return {
        "id": 12345,
        "entrant1Score": 2,
        "entrant2Score": 1,
        "fullRoundText": "Winners Final",
        "round": 3,
        "phaseGroup": {
            "phase": {
                "name": "Top 8",
                "bracketType": "DOUBLE_ELIMINATION",
                "groupCount": 1,
            },
            "displayIdentifier": "A",
        },
        "stream": {
            "streamName": "btssmash",
            "streamSource": "TWITCH",
        },
        "station": {"number": 1},
        "event": {"isOnline": False},
        "slots": [
            {
                "entrant": {
                    "name": "TSM | Tweek",
                    "initialSeedNum": 1,
                    "participants": [
                        {
                            "player": {
                                "id": 1001,
                                "prefix": "TSM",
                                "gamerTag": "Tweek",
                                "name": "Gavin",
                                "sets": {"nodes": []},
                            },
                            "user": {
                                "id": 2001,
                                "authorizations": [
                                    {"externalUsername": "tweaborned"}
                                ],
                                "genderPronoun": "He/Him",
                                "images": [
                                    {"url": "https://example.com/tweek.png"}
                                ],
                                "location": {
                                    "country": "United States",
                                    "state": "NJ",
                                    "city": "Hoboken",
                                },
                            },
                        }
                    ],
                }
            },
            {
                "entrant": {
                    "name": "MkLeo",
                    "initialSeedNum": 2,
                    "participants": [
                        {
                            "player": {
                                "id": 1002,
                                "prefix": None,
                                "gamerTag": "MkLeo",
                                "name": "Leonardo",
                                "sets": {"nodes": []},
                            },
                            "user": {
                                "id": 2002,
                                "authorizations": [],
                                "genderPronoun": None,
                                "images": [],
                                "location": {
                                    "country": "Japan",
                                    "state": None,
                                    "city": "Tokyo",
                                },
                            },
                        }
                    ],
                }
            },
        ],
    }


def _new_api_set_with_character_selections():
    """Set with character selection data from recent games."""
    base = _new_api_set_basic()
    # Add character selection data to player 1
    base["slots"][0]["entrant"]["participants"][0]["player"]["sets"] = {
        "nodes": [
            {
                "games": [
                    {
                        "selections": [
                            {
                                "selectionValue": 1,  # Mario
                                "entrant": {
                                    "participants": [
                                        {"player": {"id": 1001}}
                                    ]
                                },
                            }
                        ]
                    },
                    {
                        "selections": [
                            {
                                "selectionValue": 1,  # Mario again
                                "entrant": {
                                    "participants": [
                                        {"player": {"id": 1001}}
                                    ]
                                },
                            }
                        ]
                    },
                ]
            }
        ]
    }
    return base


def _new_api_set_empty_slots():
    """Set with empty/null entrant slots."""
    return {
        "id": 99999,
        "entrant1Score": 0,
        "entrant2Score": 0,
        "fullRoundText": "Round 1",
        "round": 1,
        "phaseGroup": {
            "phase": {
                "name": "Pools",
                "bracketType": "DOUBLE_ELIMINATION",
                "groupCount": 4,
            },
            "displayIdentifier": "B",
        },
        "stream": None,
        "station": None,
        "event": {"isOnline": True},
        "slots": [
            {"entrant": None},
            {"entrant": None},
        ],
    }


def _new_api_set_matchmaking():
    """Set with MATCHMAKING bracket type — round name should be empty."""
    base = _new_api_set_basic()
    base["phaseGroup"]["phase"]["bracketType"] = "MATCHMAKING"
    return base


def _old_api_response_basic():
    """Minimal valid response for ParseMatchDataOldApi."""
    return {
        "entities": {
            "sets": {
                "entrant1Id": 5001,
                "entrant2Id": 5002,
                "entrant1Score": 3,
                "entrant2Score": 2,
                "entrant1CharacterIds": [],
                "entrant2CharacterIds": [],
                "bestOf": 5,
                "roundDivision": None,
                "isGF": False,
                "fullRoundText": "Winners Final",
                "wProgressingName": "Winners Final",
                "lProgressingName": "Losers Semi-Final",
            },
            "setTask": [],
        }
    }


def _old_api_response_with_char_selection():
    """Response with character selection from setup_character task."""
    resp = _old_api_response_basic()
    resp["entities"]["setTask"] = [
        {
            "action": "setup_character",
            "active": False,
            "metadata": {
                "charSelections": {
                    "5001": [1],  # Mario
                    "5002": [2],  # Luigi
                }
            },
        }
    ]
    return resp


def _old_api_response_with_report_selections():
    """Response with character selection from report task."""
    resp = _old_api_response_basic()
    resp["entities"]["setTask"] = [
        {
            "action": "report",
            "active": False,
            "metadata": {
                "report": {
                    "winnerId": 5001,
                    "selections": [
                        {
                            "selectionType": "character",
                            "entrantId": 5001,
                            "selectionValue": 3,  # Bowser
                        },
                        {
                            "selectionType": "character",
                            "entrantId": 5002,
                            "selectionValue": 1,  # Mario
                        },
                    ],
                }
            },
        }
    ]
    return resp


def _old_api_response_with_stage_strike():
    """Response with stage strike data."""
    resp = _old_api_response_basic()
    resp["entities"]["setTask"] = [
        {
            "action": "report",
            "active": False,
            "metadata": {
                "report": {
                    "winnerId": 5001,
                }
            },
        },
        {
            "action": "setup_strike",
            "active": False,
            "metadata": {
                "strikeStages": [311, 328, 484],
                "strikeList": {
                    "311": 5001,  # P1 strikes Battlefield
                    "328": 5002,  # P2 strikes Final Destination
                },
                "stageSelection": 484,  # Smashville selected
                "stageWins": {},
                "useDSR": False,
                "useMDSR": False,
            },
        },
    ]
    return resp


def _old_api_response_grand_final():
    """Grand Final set — losers flags should be set."""
    resp = _old_api_response_basic()
    resp["entities"]["sets"]["isGF"] = True
    resp["entities"]["sets"]["fullRoundText"] = "Grand Final"
    return resp


def _old_api_response_grand_final_reset():
    """Grand Final Reset — both losers flags should be True."""
    resp = _old_api_response_basic()
    resp["entities"]["sets"]["isGF"] = True
    resp["entities"]["sets"]["fullRoundText"] = "Grand Final Reset"
    return resp


# ===========================================================================
# Tests: ParseMatchDataNewApi
# ===========================================================================

class TestParseMatchDataNewApi:

    def test_basic_set_metadata(self):
        """Core fields: id, scores, round, phase, bracket type."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())

        assert result["id"] == 12345
        assert result["team1score"] == 2
        assert result["team2score"] == 1
        assert result["tournament_phase"] == "Top 8"
        assert result["bracket_type"] == "DOUBLE_ELIMINATION"
        assert result["round"] == 3

    def test_round_name_translation(self):
        """fullRoundText should be translated via AutonaLocaleHelper."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["round_name"] == "Winners Final"

    def test_player_names(self):
        """Entrant names extracted correctly."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["p1_name"] == "TSM | Tweek"
        assert result["p2_name"] == "MkLeo"

    def test_seeds(self):
        """Initial seed numbers."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["p1_seed"] == 1
        assert result["p2_seed"] == 2

    def test_stream_name(self):
        """stream stores the raw streamName (the https://twitch.tv/ URL
        construction was removed; callers build the URL if needed)."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["stream"] == "btssmash"

    def test_station(self):
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["station"] == 1

    def test_is_online(self):
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["isOnline"] is False

    def test_pools_phase_name(self):
        """When groupCount > 1, the pool name is a SEPARATE field
        (tournament_pool); tournament_phase stays the phase name only."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_empty_slots())
        assert result["tournament_phase"] == "Pools"
        assert result["tournament_pool"] == "Pool B"
        assert result["isPools"] is True

    def test_single_group_has_empty_pool(self):
        """Single-group phases (groupCount <= 1) carry an empty pool name so
        consumers can clear a previously loaded pool."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["tournament_pool"] == ""

    def test_matchmaking_round_name_empty(self):
        """MATCHMAKING bracket type => round_name is empty string."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_matchmaking())
        assert result["round_name"] == ""

    def test_empty_entrant_slots(self):
        """None entrants should produce empty strings for names."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_empty_slots())
        assert result["p1_name"] == ""
        assert result["p2_name"] == ""
        assert result["entrants"] == [[], []]

    def test_entrant_player_data(self):
        """Player details extracted from participants."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        entrants = result["entrants"]

        assert len(entrants) == 2
        assert len(entrants[0]) == 1  # 1 player on team 1
        p1 = entrants[0][0]
        assert p1["prefix"] == "TSM"
        assert p1["gamerTag"] == "Tweek"
        assert p1["name"] == "Gavin"

    def test_entrant_user_data(self):
        """User profile data: twitter, pronoun, avatar, location."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        p1 = result["entrants"][0][0]

        assert p1["twitter"] == "tweaborned"
        # pronoun was intentionally removed from the product; the parser no
        # longer extracts genderPronoun, so there is no "pronoun" field.
        assert p1["avatar"] == "https://example.com/tweek.png"
        assert p1["country_code"] == "US"
        assert p1["state_code"] == "NJ"
        assert p1["id"] == [1001, 2001]

    def test_entrant_country_lookup(self):
        """Country name -> code lookup from the country catalog."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        p2 = result["entrants"][1][0]
        assert p2["country_code"] == "JP"

    def test_entrant_city_infers_the_state(self):
        """City-based state inference is back, now backed by the
        pre-computed assets/city_to_state.json (an O(1) lookup instead of
        the linear scans that got it removed). P2 (Japan, state null,
        city=Tokyo) is the typical Japanese Start.gg user: without this the
        region field stays empty. The raw city is still captured."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        p2 = result["entrants"][1][0]
        assert p2["state_code"] == "13"  # Tokyo, as the state combo keys it
        assert p2["city"] == "Tokyo"

    def test_character_selection_main(self):
        """Character selection from recent sets produces startggMain and mains."""
        provider = _make_provider()
        data = _new_api_set_with_character_selections()
        result = provider.ParseMatchDataNewApi(data)
        p1 = result["entrants"][0][0]
        assert p1.get("startggMain") == 1  # Mario (most common)
        assert p1.get("mains") == "Mario"

    def test_player_seeds_from_provider(self):
        """player_seeds dict is checked, but p{N}_seed in setData takes priority."""
        provider = _make_provider(player_seeds={1001: 5, 1002: 10})
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        p1 = result["entrants"][0][0]
        p2 = result["entrants"][1][0]
        # setData has p1_seed=1, p2_seed=2 from initialSeedNum, which overrides player_seeds
        assert p1["seed"] == 1
        assert p2["seed"] == 2

    def test_player_seeds_without_initial_seed(self):
        """When no initialSeedNum, player_seeds dict is used."""
        provider = _make_provider(player_seeds={1001: 5, 1002: 10})
        data = _new_api_set_basic()
        # Remove initialSeedNum
        data["slots"][0]["entrant"]["initialSeedNum"] = None
        data["slots"][1]["entrant"]["initialSeedNum"] = None
        result = provider.ParseMatchDataNewApi(data)
        p1 = result["entrants"][0][0]
        p2 = result["entrants"][1][0]
        assert p1["seed"] == 5
        assert p2["seed"] == 10

    def test_null_stream(self):
        """When stream is None, stream field defaults to "" (not None)."""
        provider = _make_provider()
        data = _new_api_set_basic()
        data["stream"] = None
        result = provider.ParseMatchDataNewApi(data)
        assert result["stream"] == ""

    def test_returns_empty_dict_on_exception(self):
        """Parse errors should return {}."""
        provider = _make_provider()
        # Completely broken input
        result = provider.ParseMatchDataNewApi(None)
        assert result == {}


# ===========================================================================
# Tests: TranslateRoundName / RoundNumberToTopName (TOP-N round naming)
# ===========================================================================

class TestTranslateRoundNameTopN:

    def test_named_rounds_unaffected(self):
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Final", 300) == "Winners Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Grand Final", 300) == "Grand Final"

    def test_no_entrants_keeps_round_number(self):
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 2", 0) == "Winners Round 2"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 3", 0) == "Losers Round 3"

    def test_winners_first_round_uses_entrant_count(self):
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 1", 300) == "Winners TOP300"

    def test_winners_ladder_300_entrants(self):
        # 300 entrants: WR2 enters the 3*2^n ladder below 300 and halves.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 2", 300) == "Winners TOP192"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 3", 300) == "Winners TOP96"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 4", 300) == "Winners TOP48"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 5", 300) == "Winners TOP24"

    def test_winners_ladder_full_bracket(self):
        # Full 512 bracket: 6 numbered rounds ending at TOP24 before QF.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 2", 512) == "Winners TOP384"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 6", 512) == "Winners TOP24"

    def test_winners_deep_rounds_remap_to_named(self):
        # Alive counts 12/6/3 are the true finals regardless of how the
        # event is phased; they come back as the conventional names.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 6", 300) == "Winners Quarter-Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 7", 300) == "Winners Semi-Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 8", 300) == "Winners Final"

    def test_winners_ladder_exhausted_falls_back(self):
        # Beyond the ladder (values < 3) the plain notation returns.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 9", 300) == "Winners Round 9"

    def test_losers_ladder(self):
        # LR1 runs before the first elimination, so it is anchored at the
        # entrant count; the alternating 2^n / 3*2^n ladder follows.
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 1", 300) == "Losers TOP300"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 2", 300) == "Losers TOP256"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 3", 300) == "Losers TOP192"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 12", 300) == "Losers TOP8"
        # Below 8 alive the rungs land on the conventional names.
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 13", 300) == "Losers Quarter-Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 14", 300) == "Losers Semi-Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 15", 300) == "Losers Final"
        # Ladder exhausted -> fall back to the numbered notation.
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 16", 300) == "Losers Round 16"

    def test_losers_full_bracket_ends_at_top8(self):
        # Full 512 bracket: 13 numbered losers rounds ending at TOP8.
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 13", 512) == "Losers TOP8"

    def test_presplit_winners_skip_entrant_step(self):
        # Later phase entered pre-split (TOP64 = 32W/32L): the leading losers
        # round eliminates before winners round 1, so WR1 starts one rung
        # down the 3*2^n ladder instead of at TOP{entrants}.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 1", 64, presplit=True) == "Winners TOP48"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 2", 64, presplit=True) == "Winners TOP24"
        # TOP96 phase (32W/64L): winners also opens at 96/2 = 48.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Round 1", 96, presplit=True) == "Winners TOP48"

    def test_presplit_losers_unchanged(self):
        # The losers ladder is anchor-based either way: LR1 is the phase's
        # first elimination round with the full field still alive.
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 1", 64, presplit=True) == "Losers TOP64"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Round 2", 64, presplit=True) == "Losers TOP48"

    def test_named_rounds_convert_in_earlier_phases(self):
        """Start.gg names each phase bracket's deepest rounds WQF/WSF/...
        With the numeric round they convert to the event-wide alive count:
        an early phase's phase-local final becomes its honest TOP-N."""
        # Pool bracket's Winners Semi-Final, round 4 of a 315-entrant event.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Semi-Final", 315, roundNumber=4) == "Winners TOP48"
        # Pool bracket's Losers Quarter-Final, 6th losers round.
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Quarter-Final", 315, roundNumber=-6) == "Losers TOP64"

    def test_named_rounds_round_trip_in_final_phase(self):
        """True finals sit exactly on the remap values, so converting them
        returns the same conventional name."""
        # TOP8 phase (presplit, anchor 8): WSF is winners round 1.
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Semi-Final", 8, presplit=True,
            roundNumber=1) == "Winners Semi-Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Final", 8, presplit=True,
            roundNumber=2) == "Winners Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Losers Quarter-Final", 8, presplit=True,
            roundNumber=-2) == "Losers Quarter-Final"
        # TOP64 final phase: WQF is winners round 3 (8 left on that side).
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Quarter-Final", 64, presplit=True,
            roundNumber=3) == "Winners Quarter-Final"

    def test_named_rounds_without_round_number_keep_names(self):
        assert StartGGDataProvider.TranslateRoundName(
            "Winners Semi-Final", 315) == "Winners Semi-Final"

    def test_grand_final_never_converts(self):
        assert StartGGDataProvider.TranslateRoundName(
            "Grand Final", 315, roundNumber=3) == "Grand Final"
        assert StartGGDataProvider.TranslateRoundName(
            "Grand Final Reset", 315, roundNumber=4) == "Grand Final Reset"

    def test_generic_round_unaffected(self):
        assert StartGGDataProvider.TranslateRoundName(
            "Round 1", 300) == "Round 1"

    def test_metadata_uses_phase_num_seeds(self):
        """phase.numSeeds anchors the conversion (event fallback unused).
        Being smaller than the event, the phase is pre-split: WR1 opens on
        the ladder (TOP192) and WR2 is one halving further."""
        provider = _make_provider(numEntrants=1000)
        data = _new_api_set_basic()
        data["fullRoundText"] = "Winners Round 2"
        data["phaseGroup"]["phase"]["numSeeds"] = 300
        result = provider.ParseMatchDataNewApi(data)
        assert result["round_name"] == "Winners TOP96"

    def test_metadata_falls_back_to_event_entrants(self):
        provider = _make_provider(numEntrants=300)
        data = _new_api_set_basic()
        data["fullRoundText"] = "Winners Round 2"
        result = provider.ParseMatchDataNewApi(data)
        assert result["round_name"] == "Winners TOP192"

    def test_metadata_smaller_phase_is_presplit(self):
        """A phase smaller than the event (e.g. pools -> TOP64) enters
        pre-split, so its winners round 1 skips the TOP{entrants} step."""
        provider = _make_provider(numEntrants=315)
        data = _new_api_set_basic()
        data["fullRoundText"] = "Winners Round 1"
        data["phaseGroup"]["phase"]["numSeeds"] = 64
        result = provider.ParseMatchDataNewApi(data)
        assert result["round_name"] == "Winners TOP48"

    def test_metadata_full_size_phase_is_not_presplit(self):
        provider = _make_provider(numEntrants=300)
        data = _new_api_set_basic()
        data["fullRoundText"] = "Winners Round 1"
        data["phaseGroup"]["phase"]["numSeeds"] = 300
        result = provider.ParseMatchDataNewApi(data)
        assert result["round_name"] == "Winners TOP300"

    def test_metadata_non_double_elim_keeps_round_number(self):
        provider = _make_provider(numEntrants=300)
        data = _new_api_set_basic()
        data["fullRoundText"] = "Winners Round 2"
        data["phaseGroup"]["phase"]["bracketType"] = "SINGLE_ELIMINATION"
        result = provider.ParseMatchDataNewApi(data)
        assert result["round_name"] == "Winners Round 2"


# ===========================================================================
# Tests: ParseMatchDataOldApi
# ===========================================================================

class TestParseMatchDataOldApi:

    def test_basic_scores(self):
        """Core fields: scores and bestOf."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(_old_api_response_basic())

        assert result["team1score"] == 3
        assert result["team2score"] == 2
        assert result["bestOf"] == 5
        # Progression fields were removed together with the qualifier
        # round-name override (rounds now always use the TOP notation).
        assert "winnerProgression" not in result
        assert "loserProgression" not in result

    def test_no_stage_strike_empty(self):
        """No setTask => empty stage_strike and ruleset."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(_old_api_response_basic())
        assert result["stage_strike"] == {}
        assert result["ruleset"] == {}

    def test_not_grand_final(self):
        """Non-GF set should have both losers flags False."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(_old_api_response_basic())
        assert result["team1losers"] is False
        assert result["team2losers"] is False

    def test_grand_final_losers_flags(self):
        """Grand Final: team2 (from losers) should be True."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(_old_api_response_grand_final())
        assert result["team1losers"] is False
        assert result["team2losers"] is True

    def test_grand_final_reset_losers_flags(self):
        """Grand Final Reset: both teams came through losers."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(
            _old_api_response_grand_final_reset()
        )
        assert result["team1losers"] is True
        assert result["team2losers"] is True

    def test_character_selection_from_setup(self):
        """Characters from setup_character task metadata."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(
            _old_api_response_with_char_selection()
        )
        entrants = result["entrants"]
        # P1 picked Mario (id=1)
        assert entrants[0][0]["mains"] == ["Mario", 0]
        # P2 picked Luigi (id=2)
        assert entrants[1][0]["mains"] == ["Luigi", 0]

    def test_character_selection_from_report(self):
        """Characters from report task selections."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(
            _old_api_response_with_report_selections()
        )
        entrants = result["entrants"]
        # P1: Bowser (id=3), P2: Mario (id=1)
        assert entrants[0][0]["mains"] == ["Bowser", 0]
        assert entrants[1][0]["mains"] == ["Mario", 0]

    def test_stage_strike_data(self):
        """Stage strike state from setup_strike task."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(
            _old_api_response_with_stage_strike()
        )
        ss = result["stage_strike"]
        assert ss["selectedStage"] == "smashville"
        # P1 struck Battlefield, P2 struck Final Destination
        assert "battlefield" in ss["strikedBy"][0]
        assert "fd" in ss["strikedBy"][1]
        assert ss["currGame"] == 5  # 3 + 2

    def test_stage_strike_neutral_stages(self):
        """Ruleset neutral stages from stage strike data."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(
            _old_api_response_with_stage_strike()
        )
        rs = result["ruleset"]
        codenames = [s["codename"] for s in rs["neutralStages"]]
        assert "battlefield" in codenames
        assert "fd" in codenames
        assert "smashville" in codenames
        assert rs["useDSR"] is False
        assert rs["useMDSR"] is False

    def test_empty_char_fallback_to_characterIds(self):
        """When no selection data, fall back to entrantXCharacterIds."""
        resp = _old_api_response_basic()
        resp["entities"]["sets"]["entrant1CharacterIds"] = [2]  # Luigi
        resp["entities"]["sets"]["entrant2CharacterIds"] = [3]  # Bowser
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(resp)
        entrants = result["entrants"]
        assert entrants[0][0]["mains"] == ["Luigi", 0]
        assert entrants[1][0]["mains"] == ["Bowser", 0]


# ===========================================================================
# Tests: ProcessFutureSet (the next-set preview) -- player locations (S1c)
# ===========================================================================

def _future_player(result, team):
    return result["team"][str(team)]["player"]["1"]


class TestProcessFutureSetLocations:
    """Where a next-set preview's players come from. It used to hand out the
    country table's own region dict with `asset` written into it, so what
    the Web's get_states returned depended on which sets had been looked at
    (ADR-005 S1c)."""

    def test_country_and_region_come_with_their_flags(self):
        catalog = CountryCatalog.instance
        result = _make_provider().ProcessFutureSet(_new_api_set_basic(), "")
        p2 = _future_player(result, 2)

        assert p2["country"] == catalog.BasicCountryInfo("JP")
        assert p2["country"]["code"] == "JP" and p2["country"]["asset"]
        # City "Tokyo" resolves to the region the state combo keys as 13,
        # and the region's flag is resolved on the way out.
        assert p2["state"] == catalog.State("JP", "13")
        assert p2["state"]["original_code"] == "13"
        assert p2["state"]["asset"] == state_flag_path("JP", "13")

    def test_a_raw_region_the_table_does_not_know_is_empty(self):
        """ProcessEntrantData passes an unresolvable Start.gg state through
        verbatim; it is not a key of the region table, so the preview gets
        {} rather than a lookup error or the raw string."""
        _set = _new_api_set_basic()
        location = (_set["slots"][1]["entrant"]["participants"][0]
                    ["user"]["location"])
        location.update({"state": "Atlantis", "city": None})

        p2 = _future_player(_make_provider().ProcessFutureSet(_set, ""), 2)

        assert p2["state"] == {}
        assert p2["country"]["code"] == "JP"

    def test_the_answer_is_a_copy_of_the_catalog(self):
        snap = CountryCatalog.instance.Current()
        provider = _make_provider()
        p2 = _future_player(
            provider.ProcessFutureSet(_new_api_set_basic(), ""), 2)

        assert p2["country"] is not snap.basicInfo["JP"]
        assert p2["state"] is not snap.countries["JP"]["states"]["13"]
        # Nothing was written into the table on the way out...
        assert "asset" not in snap.countries["JP"]["states"]["13"]
        # ...and a caller editing its answer changes no later answer.
        p2["country"]["name"] = "CHANGED"
        p2["state"]["name"] = "CHANGED"
        p2["state"]["extra"] = 1
        again = _future_player(
            provider.ProcessFutureSet(_new_api_set_basic(), ""), 2)
        assert again["country"]["name"] != "CHANGED"
        assert again["state"]["name"] != "CHANGED"
        assert "extra" not in again["state"]


# ===========================================================================
# Tests: ProcessEntrantData
# ===========================================================================

class TestProcessEntrantData:

    def test_basic_player_data(self):
        """Player prefix, gamerTag, name extraction."""
        entrant = {
            "player": {
                "id": 1001,
                "prefix": "TSM",
                "gamerTag": "Tweek",
                "name": "Gavin",
                "sets": {"nodes": []},
            },
            "user": {
                "id": 2001,
                "authorizations": [],
                "genderPronoun": None,
                "images": [],
                "location": None,
            },
        }
        result = StartGGDataProvider.ProcessEntrantData(entrant)
        assert result["prefix"] == "TSM"
        assert result["gamerTag"] == "Tweek"
        assert result["name"] == "Gavin"
        assert result["id"] == [1001, 2001]

    def test_twitter_extraction(self):
        entrant = {
            "player": {"id": 1, "prefix": None, "gamerTag": "P1", "name": None},
            "user": {
                "id": 2,
                "authorizations": [{"externalUsername": "p1twitter"}],
                "images": [],
            },
        }
        result = StartGGDataProvider.ProcessEntrantData(entrant)
        assert result["twitter"] == "p1twitter"

    def test_country_and_state_lookup(self):
        entrant = {
            "player": {"id": 1, "prefix": None, "gamerTag": "P1", "name": None},
            "user": {
                "id": 2,
                "authorizations": [],
                "images": [],
                "location": {
                    "country": "Japan",
                    "state": None,
                    "city": "Tokyo",
                },
            },
        }
        result = StartGGDataProvider.ProcessEntrantData(entrant)
        assert result["country_code"] == "JP"
        # No direct state field, so the city falls back through
        # CountryCatalog.FindState and resolves to the prefecture iso2.
        assert result["state_code"] == "13"

    def test_no_user_gives_zero_id(self):
        """When user is None/falsy, id should be [player_id, 0]."""
        entrant = {
            "player": {"id": 1001, "prefix": None, "gamerTag": "P1", "name": None},
            "user": None,
        }
        result = StartGGDataProvider.ProcessEntrantData(entrant)
        assert result["id"] == [1001, 0]

    def test_mains_from_set_data(self):
        """Character mains from provided set data."""
        entrant = {
            "player": {
                "id": 1001,
                "prefix": None,
                "gamerTag": "P1",
                "name": None,
                "sets": {"nodes": []},
            },
            "user": {
                "id": 2001,
                "authorizations": [],
                "images": [],
            },
        }
        set_data = [
            {
                "games": [
                    {
                        "selections": [
                            {
                                "selectionValue": 2,  # Luigi
                                "entrant": {
                                    "participants": [
                                        {"player": {"id": 1001}}
                                    ]
                                },
                            }
                        ]
                    }
                ]
            }
        ]
        result = StartGGDataProvider.ProcessEntrantData(entrant, set_data)
        assert result.get("startggMains") is not None
        # Most common character = Luigi (id=2)
        assert result["startggMains"][0][0] == 2
        # Resolved through the game asset catalog, keyed by the loaded game
        assert result["mains"] == {"tgame": [["Luigi"]]}


# ===========================================================================
# Tests: _parse_sets_to_rounds (shared by GetTournamentPhaseGroup /
# GetAnalysisPhaseGroup — pure transform, no network)
# ===========================================================================

def _set_node(id, round, **kw):
    return {
        "id": id,
        "round": round,
        "identifier": kw.get("identifier", "A"),
        "entrant1Score": kw.get("e1", 0),
        "entrant2Score": kw.get("e2", 0),
        "state": kw.get("state", 1),
        "fullRoundText": kw.get("frt", "Round"),
        "slots": kw.get("slots", []),
    }


class TestParseSetsToRounds:

    def test_groups_by_round_key(self):
        sets = [_set_node("1", 1), _set_node("2", -1), _set_node("3", 1)]
        result = StartGGDataProvider._parse_sets_to_rounds(sets)
        assert set(result.keys()) == {"1", "-1"}
        assert len(result["1"]) == 2
        assert len(result["-1"]) == 1

    def test_entry_fields_and_slots(self):
        slot = {"prereqType": "set", "prereqId": "p1",
                "prereqPlacement": 2, "seed": {"seedNum": 5}}
        sets = [_set_node("99", 3, e1=2, e2=1, state=3, slots=[slot])]
        entry = StartGGDataProvider._parse_sets_to_rounds(sets)["3"][0]
        assert entry["id"] == "99"
        assert entry["identifier"] == "A"
        assert entry["score"] == [2, 1]
        assert entry["finished"] is True
        assert entry["slots"][0] == {
            "prereqType": "set", "prereqId": "p1",
            "prereqPlacement": 2, "seedNum": 5,
        }

    def test_full_round_text_flag(self):
        without = StartGGDataProvider._parse_sets_to_rounds(
            [_set_node("1", 1, frt="Winners Final")])
        assert "fullRoundText" not in without["1"][0]
        with_frt = StartGGDataProvider._parse_sets_to_rounds(
            [_set_node("1", 1, frt="Winners Final")],
            include_full_round_text=True)
        assert with_frt["1"][0]["fullRoundText"] == "Winners Final"

    def test_not_finished(self):
        result = StartGGDataProvider._parse_sets_to_rounds(
            [_set_node("1", 1, state=1)])
        assert result["1"][0]["finished"] is False

    def test_preview_sort_by_trailing_segment(self):
        # preview ids sort by the trailing numeric segment, not lexically
        sets = [
            _set_node("preview_1_1_2", 1),
            _set_node("preview_1_1_11", 1),
            _set_node("preview_1_1_1", 1),
        ]
        ids = [e["id"] for e in
               StartGGDataProvider._parse_sets_to_rounds(sets)["1"]]
        assert ids == ["preview_1_1_1", "preview_1_1_2", "preview_1_1_11"]

    def test_skips_null_round(self):
        result = StartGGDataProvider._parse_sets_to_rounds(
            [_set_node("1", 1), _set_node("2", None)])
        assert list(result.keys()) == ["1"]

    def test_empty_and_none_input(self):
        assert StartGGDataProvider._parse_sets_to_rounds([]) == {}
        assert StartGGDataProvider._parse_sets_to_rounds(None) == {}


# ===========================================================================
# Tests: _split_gf_reset (pure, in-place grand-final reset split)
# ===========================================================================

class TestSplitGfReset:

    def test_splits_when_no_progressions(self):
        finalSets = {"1": [{"id": "a"}], "2": [{"id": "gf"}, {"id": "reset"}]}
        StartGGDataProvider._split_gf_reset(finalSets, [])
        assert finalSets["2"] == [{"id": "gf"}]
        assert finalSets["3"] == [{"id": "reset"}]

    def test_no_split_when_progressions_present(self):
        finalSets = {"2": [{"id": "gf"}, {"id": "reset"}]}
        StartGGDataProvider._split_gf_reset(finalSets, [123])
        assert finalSets["2"] == [{"id": "gf"}, {"id": "reset"}]
        assert "3" not in finalSets

    def test_no_split_when_single_set(self):
        finalSets = {"2": [{"id": "gf"}]}
        StartGGDataProvider._split_gf_reset(finalSets, None)
        assert finalSets["2"] == [{"id": "gf"}]
        assert "3" not in finalSets

    def test_ignores_negative_rounds(self):
        # GF detection only looks at positive rounds; losers bracket untouched
        finalSets = {"-1": [{"id": "l1"}, {"id": "l2"}], "1": [{"id": "w"}]}
        StartGGDataProvider._split_gf_reset(finalSets, [])
        assert "2" not in finalSets
        assert finalSets["-1"] == [{"id": "l1"}, {"id": "l2"}]


# ===========================================================================
# Tests: GetMatches (parallel paginated set fetch)
# ===========================================================================

class TestGetMatchesPagination:

    def _paged_response(self, ids, totalPages):
        nodes = []
        for i in ids:
            node = _new_api_set_basic()
            node["id"] = i
            nodes.append(node)
        return {"data": {"event": {"sets": {
            "pageInfo": {"totalPages": totalPages},
            "nodes": nodes,
        }}}}

    def _provider(self):
        p = _make_provider()
        p.url = "https://www.start.gg/tournament/t/event/e"
        return p

    def test_pages_assembled_in_order_with_dedupe(self):
        """Pages land out of order (parallel fetch) but assemble by page
        number; a set drifting across a page boundary mid-fetch appears
        once."""
        provider = self._provider()
        pages = {
            1: self._paged_response([11, 12], 3),
            2: self._paged_response([13, 12], 3),  # 12 repeats (page shift)
            3: self._paged_response([14], 3),
        }
        provider.QueryRequests = lambda *a, **kw: pages[
            kw["jsonParams"]["variables"]["page"]]
        result = provider.GetMatches()
        assert [s["id"] for s in result] == [11, 12, 13, 14]

    def test_failed_page_gets_second_chance(self):
        """A page whose query exhausted its retries ({}) is re-fetched once
        after the burst instead of silently dropping its sets."""
        provider = self._provider()
        page2Calls = []

        def fake(*a, **kw):
            page = kw["jsonParams"]["variables"]["page"]
            if page == 2:
                page2Calls.append(True)
                if len(page2Calls) == 1:
                    return {}
                return self._paged_response([22], 2)
            return self._paged_response([21], 2)

        provider.QueryRequests = fake
        result = provider.GetMatches()
        assert sorted(s["id"] for s in result) == [21, 22]
        assert len(page2Calls) == 2

    def test_single_page_event(self):
        provider = self._provider()
        provider.QueryRequests = lambda *a, **kw: self._paged_response(
            [31], 1)
        result = provider.GetMatches()
        assert [s["id"] for s in result] == [31]

    def test_empty_event(self):
        provider = self._provider()
        provider.QueryRequests = lambda *a, **kw: self._paged_response([], 0)
        assert provider.GetMatches() == []


# ===========================================================================
# Tests: unknown character ids in the legacy half (LIVE-BUG-3)
# ===========================================================================

# Neither the fixture's start.gg table nor any installed pack knows this id,
# so it is an unknown character by construction.
_UNKNOWN_CHARACTER_ID = 9999


class TestOldApiUnknownCharacterIds:
    """A character the installed assets do not know must cost only itself.

    CharacterFromStartGGId returns None for any start.gg character id no
    installed game pack maps -- no pack installed at all, a modded roster, a
    character start.gg added since the pack was built.
    _build_entrants_old_api subscripted that None with [0], so one
    unrecognised id raised TypeError out of the whole legacy parse; the
    caller logs it and hands back {}, which silently costs the set its
    bestOf, stage_strike, striked stages, losers flags and currPlayer.
    Nothing about that failure mentions characters, and it only happens on a
    machine with no assets installed -- which is every fresh install.
    """

    def test_an_unknown_selection_keeps_the_rest_of_the_set(self):
        resp = _old_api_response_with_char_selection()
        resp["entities"]["setTask"][0]["metadata"]["charSelections"] = {
            "5001": [_UNKNOWN_CHARACTER_ID],
            "5002": [2],  # Luigi
        }
        provider = _make_provider()

        result = provider.ParseMatchDataOldApi(resp)

        # The fields the exception used to take down with it.
        assert result["bestOf"] == 5
        assert result["team1score"] == 3
        assert result["team2score"] == 2
        assert result["team1losers"] is False
        assert result["team2losers"] is False
        assert "currPlayer" in result
        # ...and the entrant that *was* recognised still has its character.
        assert result["entrants"][1][0]["mains"] == ["Luigi", 0]

    def test_the_unknown_character_is_skipped_not_faked(self):
        resp = _old_api_response_with_char_selection()
        resp["entities"]["setTask"][0]["metadata"]["charSelections"] = {
            "5001": [_UNKNOWN_CHARACTER_ID],
            "5002": [_UNKNOWN_CHARACTER_ID],
        }
        provider = _make_provider()

        result = provider.ParseMatchDataOldApi(resp)

        # No entry at all rather than one holding None: the player widget
        # reads mains[0] as a character name straight out of this.
        assert result["entrants"] == [[], []]

    def test_a_known_and_an_unknown_selection_for_one_entrant(self):
        resp = _old_api_response_with_char_selection()
        resp["entities"]["setTask"][0]["metadata"]["charSelections"] = {
            "5001": [1, _UNKNOWN_CHARACTER_ID, 3],  # Mario, ???, Bowser
            "5002": [2],
        }
        provider = _make_provider()

        result = provider.ParseMatchDataOldApi(resp)

        assert [e["mains"] for e in result["entrants"][0]] == [
            ["Mario", 0], ["Bowser", 0]]

    def test_an_unknown_id_in_the_characterIds_fallback(self):
        """The same guard on the fallback path, which had a bare except.

        There the TypeError was caught, so the set survived -- but the
        entrant ended up silently without mains and the log showed a
        traceback rather than the character id that was not recognised.
        """
        resp = _old_api_response_basic()
        resp["entities"]["sets"]["entrant1CharacterIds"] = [
            _UNKNOWN_CHARACTER_ID]
        resp["entities"]["sets"]["entrant2CharacterIds"] = [3]  # Bowser
        provider = _make_provider()

        result = provider.ParseMatchDataOldApi(resp)

        assert result["entrants"][0] == []
        assert result["entrants"][1][0]["mains"] == ["Bowser", 0]
        assert result["bestOf"] == 5


# Not a stage of the fixture game.
_UNKNOWN_STAGE_ID = 9999


class TestOldApiUnknownStageIds:
    """The same guard for the stages an entrant already won on.

    _scan_stage_strike_tasks subscripted StageFromStartGGId(...) with [1]
    for every stageWins id, so one stage the loaded game does not know took
    the legacy half down exactly as an unknown character did.
    """

    def test_an_unknown_stage_win_keeps_the_rest_of_the_set(self):
        resp = _old_api_response_with_stage_strike()
        resp["entities"]["setTask"][1]["metadata"]["stageWins"] = {
            "5001": [311, _UNKNOWN_STAGE_ID],  # Battlefield, ???
            "5002": [_UNKNOWN_STAGE_ID],
        }
        provider = _make_provider()

        result = provider.ParseMatchDataOldApi(resp)

        assert result["bestOf"] == 5
        assert result["team1losers"] is False
        assert "currPlayer" in result
        ss = result["stage_strike"]
        # Skipped, not faked: the known win stays, the unknown ones go.
        assert ss["stagesWon"] == [["battlefield"], []]
        assert ss["selectedStage"] == "smashville"
        assert ss["strikedBy"] == [["battlefield"], ["fd"]]
        assert [s["codename"] for s in result["ruleset"]["neutralStages"]] \
            == ["battlefield", "fd", "smashville"]


# ===========================================================================
# Tests: one parse, one game asset snapshot (ADR-005 S1b)
# ===========================================================================

@pytest.fixture
def loadLandsMidParse(_catalog, monkeypatch):
    """A game load lands right after the first start.gg id lookup.

    Both resolvers are wrapped so that every lookup, once answered, replaces
    the catalog's current snapshot with an empty one -- what a load of
    another game does to a parse running on a start.gg worker thread. A
    lookup that went back to Current() instead of the snapshot its parse
    took would come back None from then on.
    """
    for name in ("CharacterFromStartGGId", "StageFromStartGGId"):
        real = getattr(GameAssetCatalog, name)

        def swapping(self, smashgg_id, snapshot=None, _real=real):
            result = _real(self, smashgg_id, snapshot)
            self._current = GameAssetSnapshot()
            return result

        monkeypatch.setattr(GameAssetCatalog, name, swapping)
    # Registers the loaded snapshot for restoring at teardown: the fixture
    # catalog is shared by the whole module.
    monkeypatch.setattr(_catalog, "_current", _catalog.Current())


class TestOneSnapshotPerParse:

    def test_the_old_api_parse(self, loadLandsMidParse):
        resp = _old_api_response_with_stage_strike()
        resp["entities"]["setTask"][1]["metadata"]["charSelections"] = {
            "5001": [1],  # Mario
            "5002": [2],  # Luigi
        }

        result = _make_provider().ParseMatchDataOldApi(resp)

        ss = result["stage_strike"]
        assert ss["selectedStage"] == "smashville"
        assert ss["strikedBy"] == [["battlefield"], ["fd"]]
        assert [s["codename"] for s in result["ruleset"]["neutralStages"]] \
            == ["battlefield", "fd", "smashville"]
        assert [e["mains"] for team in result["entrants"] for e in team] \
            == [["Mario", 0], ["Luigi", 0]]

    def test_the_new_api_parse(self, loadLandsMidParse):
        data = _new_api_set_with_character_selections()
        data["slots"][1]["entrant"]["participants"][0]["player"]["sets"] = {
            "nodes": [{"games": [{"selections": [{
                "selectionValue": 2,  # Luigi
                "entrant": {"participants": [{"player": {"id": 1002}}]},
            }]}]}]}

        result = _make_provider().ParseMatchDataNewApi(data)

        assert [team[0].get("mains") for team in result["entrants"]] \
            == ["Mario", "Luigi"]

    def test_an_entrant(self, loadLandsMidParse):
        entrant = {
            "player": {"id": 1001, "prefix": None, "gamerTag": "P1",
                       "name": None},
            "user": {"id": 2001, "authorizations": [], "images": []},
        }
        selections = [
            {"selectionValue": value,
             "entrant": {"participants": [{"player": {"id": 1001}}]}}
            for value in (1, 1, 3)]  # Mario twice, Bowser once
        setData = [{"games": [{"selections": selections}]}]

        result = StartGGDataProvider.ProcessEntrantData(entrant, setData)

        # The game the mains are keyed under and the mains themselves come
        # from the same snapshot.
        assert result["mains"] == {"tgame": [["Mario"], ["Bowser"]]}


# ===========================================================================
# Tests: the head-to-head asks both players' events (ADR-005 S3e-5 review)
# ===========================================================================

class _PoolWorker:
    """src.Workers.Worker as GetRecentSets uses it, run by `_Pool` at
    once; wait_for_all records which workers it was asked to wait for."""
    waitedFor = []

    def __init__(self, fn, **kwargs):
        self.fn = fn
        self.kwargs = kwargs
        self.result = None
        self.completed = False

    def run(self):
        self.result = self.fn(progress_callback=None, cancel_event=None,
                              **self.kwargs)
        self.completed = True

    @staticmethod
    def wait_for_all(workers, timeout=None):
        _PoolWorker.waitedFor.append(list(workers))
        return all(worker.completed for worker in workers)


class TestRecentSets:

    def test_both_directions_are_waited_for_and_read(self, monkeypatch):
        """The workers of the first direction (the first player's events)
        were started but neither waited for nor read: the list was the last
        loop's, so the sets only that player's events held were missing. A
        set both directions found keeps the second's entry, as before."""
        monkeypatch.setattr(_startgg_mod, "Worker", _PoolWorker)
        monkeypatch.setattr(_PoolWorker, "waitedFor", [])
        found = {
            (False, 1): [{"id": "A", "timestamp": 300, "seen": "first"},
                         {"id": "B", "timestamp": 100, "seen": "first"}],
            (True, 1): [{"id": "B", "timestamp": 100, "seen": "second"},
                        {"id": "C", "timestamp": 200, "seen": "second"}],
        }
        asked = []

        def worker(id1, id2, page, videogame, inverted, progress_callback,
                   cancel_event):
            asked.append((id1, id2, page, inverted))
            return found.get((inverted, page), [])

        provider = _make_provider()
        provider.threadpool = types.SimpleNamespace(
            start=lambda worker: worker.run())
        provider.GetRecentSetsWorker = worker
        answers = []

        provider.GetRecentSets(
            [1, 10], [2, 20], 1386,
            types.SimpleNamespace(emit=answers.append), 7, None, None)

        assert answers == [{"request_time": 7, "sets": [
            {"id": "A", "timestamp": 300, "seen": "first"},
            {"id": "C", "timestamp": 200, "seen": "second"},
            {"id": "B", "timestamp": 100, "seen": "second"},
        ]}]
        assert [len(workers) for workers in _PoolWorker.waitedFor] == [10]
        assert sorted(asked) == sorted(
            [(["1", "10"], ["2", "20"], page, False) for page in range(1, 6)]
            + [(["2", "20"], ["1", "10"], page, True)
               for page in range(1, 6)])
