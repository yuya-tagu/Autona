"""Characterization tests for StartGGDataProvider parse functions.

These tests capture the current behavior of ParseMatchDataNewApi,
ParseMatchDataOldApi, and ProcessEntrantData so that refactoring
can be verified against known-good outputs.

Strategy: the provider's own dependencies (game assets, player DB, workers,
qtpy) are stubbed via sys.modules while the module is exec'd, so the parse
functions can be exercised as pure data transforms with deterministic
character/stage lookups. The stubs are removed again immediately afterwards
-- they are an isolation device for this module only and must never be
visible to another test module (src.* imports normally these days).
"""
import sys
import types
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. The real deep_get (no Qt dependency) backs the stubbed dict helper
# ---------------------------------------------------------------------------
from src.Helpers.AutonaDictHelper import deep_get

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


# --- AutonaCountryHelper ---
class _MockAutonaCountryHelper:
    countries = {
        "JP": {"code": "JP", "en_name": "Japan"},
        "US": {"code": "US", "en_name": "United States"},
    }

    @staticmethod
    def FindState(country_code, city):
        if country_code == "JP" and city == "Tokyo":
            return "13"  # Tokyo prefecture code
        return None


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


# --- AutonaGameAssetManager ---
class _MockGameAssetManagerInstance:
    selectedGame = {"codename": "ssbu"}

    @staticmethod
    def GetCharacterFromStartGGId(char_id):
        char_map = {
            1: ("mario", {"name": "Mario", "codename": "mario"}),
            2: ("fox", {"name": "Fox", "codename": "fox"}),
            3: ("pikachu", {"name": "Pikachu", "codename": "pikachu"}),
        }
        return char_map.get(char_id)

    @staticmethod
    def GetStageFromStartGGId(stage_id):
        stage_map = {
            100: (0, {"codename": "battlefield", "name": "Battlefield"}),
            101: (1, {"codename": "final_destination", "name": "Final Destination"}),
            102: (2, {"codename": "smashville", "name": "Smashville"}),
            103: (3, {"codename": "pokemon_stadium2", "name": "Pokemon Stadium 2"}),
            104: (4, {"codename": "kalos", "name": "Kalos"}),
        }
        return stage_map.get(stage_id)


class _MockAutonaGameAssetManager:
    instance = _MockGameAssetManagerInstance()


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
        "src.Helpers.AutonaCountryHelper": _make_mock_module(
            "src.Helpers.AutonaCountryHelper",
            {"AutonaCountryHelper": _MockAutonaCountryHelper},
        ),
        "src.Helpers.AutonaDictHelper": _make_mock_module(
            "src.Helpers.AutonaDictHelper",
            {"deep_get": deep_get},
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
        "src.AutonaGameAssetManager": _make_mock_module(
            "src.AutonaGameAssetManager",
            {"AutonaGameAssetManager": _MockAutonaGameAssetManager},
        ),
        "src.AutonaPlayerDB": _make_mock_module(
            "src.AutonaPlayerDB",
            {"AutonaPlayerDB": MagicMock()},
        ),
        "src.AutonaBracket": _make_mock_module(
            "src.AutonaBracket",
            {"is_power_of_two": lambda x: x & (x - 1) == 0 and x > 0},
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
                    "5002": [2],  # Fox
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
                            "selectionValue": 3,  # Pikachu
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
                "strikeStages": [100, 101, 102, 103, 104],
                "strikeList": {
                    "100": 5001,  # P1 strikes Battlefield
                    "103": 5002,  # P2 strikes Pokemon Stadium 2
                },
                "stageSelection": 102,  # Smashville selected
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
        """Country name -> code lookup from AutonaCountryHelper."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        p2 = result["entrants"][1][0]
        assert p2["country_code"] == "JP"

    def test_entrant_city_no_state_inference(self):
        """City-based state inference was removed (StartGGDataProvider.py
        ~843-847: linear scans were slow on large pools and unreliable for
        Japanese users). P2 (Japan, state null, city=Tokyo) gets no
        state_code, but the raw city is captured."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        p2 = result["entrants"][1][0]
        assert "state_code" not in p2
        assert p2["city"] == "Tokyo"

    def test_character_selection_main(self):
        """Character selection from recent sets produces startggMain and mains."""
        provider = _make_provider()
        data = _new_api_set_with_character_selections()
        result = provider.ParseMatchDataNewApi(data)
        p1 = result["entrants"][0][0]
        assert p1.get("startggMain") == 1  # Mario (most common)
        assert p1.get("mains") == "mario"

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
        assert entrants[0][0]["mains"] == ["mario", 0]
        # P2 picked Fox (id=2)
        assert entrants[1][0]["mains"] == ["fox", 0]

    def test_character_selection_from_report(self):
        """Characters from report task selections."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(
            _old_api_response_with_report_selections()
        )
        entrants = result["entrants"]
        # P1: Pikachu (id=3), P2: Mario (id=1)
        assert entrants[0][0]["mains"] == ["pikachu", 0]
        assert entrants[1][0]["mains"] == ["mario", 0]

    def test_stage_strike_data(self):
        """Stage strike state from setup_strike task."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(
            _old_api_response_with_stage_strike()
        )
        ss = result["stage_strike"]
        assert ss["selectedStage"] == "smashville"
        # P1 struck Battlefield, P2 struck Pokemon Stadium 2
        assert "battlefield" in ss["strikedBy"][0]
        assert "pokemon_stadium2" in ss["strikedBy"][1]
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
        assert "final_destination" in codenames
        assert "smashville" in codenames
        assert rs["useDSR"] is False
        assert rs["useMDSR"] is False

    def test_empty_char_fallback_to_characterIds(self):
        """When no selection data, fall back to entrantXCharacterIds."""
        resp = _old_api_response_basic()
        resp["entities"]["sets"]["entrant1CharacterIds"] = [2]  # Fox
        resp["entities"]["sets"]["entrant2CharacterIds"] = [3]  # Pikachu
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(resp)
        entrants = result["entrants"]
        assert entrants[0][0]["mains"] == ["fox", 0]
        assert entrants[1][0]["mains"] == ["pikachu", 0]


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
        # City-based state inference removed; with no direct state field
        # Start.gg gives us, state_code is not set.
        assert "state_code" not in result

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
                                "selectionValue": 2,  # Fox
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
        # Most common character = Fox (id=2)
        assert result["startggMains"][0][0] == 2
        # Mains should be resolved via GameAssetManager
        assert result["mains"] == {"ssbu": [["fox"]]}


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
