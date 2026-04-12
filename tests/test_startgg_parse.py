"""Characterization tests for StartGGDataProvider parse functions.

These tests capture the current behavior of ParseMatchDataNewApi,
ParseMatchDataOldApi, and ProcessEntrantData so that refactoring
can be verified against known-good outputs.

Strategy: pre-populate sys.modules with mocks for all src.* dependencies
that would otherwise trigger the Qt/singleton init chain, then use
direct_import to load only StartGGDataProvider.
"""
import sys
import types
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Load AutonaDictHelper (no Qt dependency) for the real deep_get
# ---------------------------------------------------------------------------
from tests.conftest import direct_import, PROJECT_ROOT

_dict_helper = direct_import(
    "AutonaDictHelper_test", "src/Helpers/AutonaDictHelper.py"
)
deep_get = _dict_helper.deep_get


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
            {"invokeSlot": MagicMock(), "gui_thread_sync": MagicMock()},
        ),
        "src.Helpers.AutonaLocaleHelper": _make_mock_module(
            "src.Helpers.AutonaLocaleHelper",
            {"AutonaLocaleHelper": _MockAutonaLocaleHelper},
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
    sys.modules["src.TournamentDataProvider.StartGGDataProvider"] = _startgg_mod
    _spec.loader.exec_module(_startgg_mod)
    StartGGDataProvider = _startgg_mod.StartGGDataProvider
except Exception:
    _teardown_mock_modules()
    raise


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

    def test_stream_twitch(self):
        """Twitch stream URL construction."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["stream"] == "https://twitch.tv/btssmash"

    def test_station(self):
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["station"] == 1

    def test_is_online(self):
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        assert result["isOnline"] is False

    def test_pools_phase_name(self):
        """When groupCount > 1, phase name includes pool identifier."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_empty_slots())
        assert result["tournament_phase"] == "Pools - Pool B"
        assert result["isPools"] is True

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
        assert p1["pronoun"] == "He/Him"
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

    def test_entrant_state_from_city(self):
        """State lookup from city when state is null."""
        provider = _make_provider()
        result = provider.ParseMatchDataNewApi(_new_api_set_basic())
        p2 = result["entrants"][1][0]
        # P2: Japan, no state, city=Tokyo -> FindState returns "13"
        assert p2["state_code"] == "13"

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
        """When stream is None, stream field should be None."""
        provider = _make_provider()
        data = _new_api_set_basic()
        data["stream"] = None
        result = provider.ParseMatchDataNewApi(data)
        assert result["stream"] is None

    def test_returns_empty_dict_on_exception(self):
        """Parse errors should return {}."""
        provider = _make_provider()
        # Completely broken input
        result = provider.ParseMatchDataNewApi(None)
        assert result == {}


# ===========================================================================
# Tests: ParseMatchDataOldApi
# ===========================================================================

class TestParseMatchDataOldApi:

    def test_basic_scores(self):
        """Core fields: scores, bestOf, progressions."""
        provider = _make_provider()
        result = provider.ParseMatchDataOldApi(_old_api_response_basic())

        assert result["team1score"] == 3
        assert result["team2score"] == 2
        assert result["bestOf"] == 5
        assert result["winnerProgression"] == "Winners Final"
        assert result["loserProgression"] == "Losers Semi-Final"

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
