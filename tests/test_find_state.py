"""City/state resolution for Start.gg auto-fill.

Start.gg reports `location.state = null` for most Japanese users and puts
the region in `location.city` ("Tokyo", "Shibuya-ku", ...), so without a
city -> prefecture fallback the region field on a scoreboard stays empty.
The fallback used to exist, ran a linear scan per entrant over tables built
from the 46 MB countries+states+cities.json, and was removed for that cost
(7d1cd1a9).

It is back as a build-time table: scripts/gen_city_to_state.py precomputes
{country code: {normalized key: state iso2}} into assets/city_to_state.json,
and AutonaCountryHelper.FindState loads it lazily on first use for an O(1)
lookup. Two halves are covered here -- how the table is built, and how the
runtime reads it -- because a mismatch between the two normalizations
(generator-side vs remove_accents_lower) or between the two copies of the
suffix pattern would silently return nothing.

The thing this file is most careful about is the ambiguous city name.
"Kawasaki" is a city in six prefectures; resolving it to whichever one the
data happened to list first would put a confidently wrong region on a
broadcast, which is worse than the blank field. Those keys are dropped at
build time, and the tests below pin that they stay dropped.

The lazy load is what keeps the module import inert; that contract itself
belongs to tests/test_import_side_effects.py.
"""
import importlib.util
from pathlib import Path

import orjson
import pytest

from src.Helpers import AutonaCountryHelper as country_helper_module
from src.Helpers.AutonaCountryHelper import AutonaCountryHelper

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = PROJECT_ROOT / "assets" / "city_to_state.json"
FULL_DATASET_PATH = PROJECT_ROOT / "assets" / "countries+states+cities.json"


def _load_generator():
    """Import scripts/gen_city_to_state.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "gen_city_to_state", PROJECT_ROOT / "scripts" / "gen_city_to_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture
def cityToStateCache():
    """Restore AutonaCountryHelper's lazy cache around a test that clears it."""
    saved = AutonaCountryHelper._cityToState
    try:
        yield
    finally:
        AutonaCountryHelper._cityToState = saved


# ── the generator ────────────────────────────────────────────────────────

# A JP-shaped slice: real prefecture iso2 codes, a macron in a state name,
# padding in a city name, and city names that exercise both suffix forms
# ("-ku" and " Ku") as well as the ambiguity the real data is full of.
_JP_FIXTURE = {
    "iso2": "JP",
    "name": "Japan",
    "states": [
        {
            "iso2": "13",
            "name": "Tokyo",
            "cities": [{"name": "Shibuya-ku"}, {"name": "Adachi Ku"},
                       {"name": "Kita-ku"}, {"name": "Ōta-ku"}],
        },
        {
            "iso2": "27",
            "name": "Ōsaka",
            "cities": [{"name": "Osaka "}, {"name": "Kita-ku"},
                       {"name": "Ota"}],
        },
    ],
}

_US_FIXTURE = {
    "iso2": "US",
    "name": "United States",
    "states": [{"iso2": "CA", "name": "California",
                "cities": [{"name": "Los Angeles"}]}],
}

# iso2 is numeric, so LoadCountries derives the code from the name's
# initials -- and playerData["country_code"] carries that derived code, so
# the table has to be keyed the same way.
_NUMERIC_ISO2_FIXTURE = {
    "iso2": "01",
    "name": "Some Country",
    "states": [{"iso2": "X1", "name": "Someplace", "cities": []}],
}


def test_state_names_and_codes_are_keys():
    maps = gen.build_maps([_JP_FIXTURE])

    assert maps["JP"]["tokyo"] == "13"
    assert maps["JP"]["13"] == "13"
    # The macron in "Ōsaka" must be folded exactly like remove_accents_lower
    # folds the lookup input.
    assert maps["JP"]["osaka"] == "27"


def test_city_names_and_suffix_aliases_are_keys():
    maps = gen.build_maps([_JP_FIXTURE])

    assert maps["JP"]["shibuya-ku"] == "13"
    assert maps["JP"]["adachi ku"] == "13"
    # ...and the same cities without the municipality suffix.
    assert maps["JP"]["shibuya"] == "13"
    assert maps["JP"]["adachi"] == "13"


def test_padded_city_names_are_trimmed():
    """The dataset contains names like "Barisal " / "Osaka ". FindState
    strips each part before looking it up, so an untrimmed key could never
    be hit -- 57 of them were unreachable before normalize() stripped."""
    maps = gen.build_maps([_JP_FIXTURE])

    assert "osaka " not in maps["JP"]


def test_ambiguous_city_names_are_dropped_rather_than_guessed():
    """"Kita-ku" exists in both prefectures. Nothing in the data says which
    one a bare "Kita-ku" means, so the key is left out and the region stays
    blank -- a wrong region on a stream is worse than no region."""
    maps = gen.build_maps([_JP_FIXTURE])

    assert "kita-ku" not in maps["JP"]
    assert "kita" not in maps["JP"]


def test_a_city_name_and_another_citys_alias_are_weighed_together():
    """"Ota" is a city in 27 *and* what 13's "Ōta-ku" reduces to. Exact
    names get no precedence over aliases: both are equally plausible
    readings, so the key is dropped and only the full "ota-ku" resolves."""
    maps = gen.build_maps([_JP_FIXTURE])

    assert "ota" not in maps["JP"]
    assert maps["JP"]["ota-ku"] == "13"


def test_a_state_name_outranks_an_ambiguous_city_name():
    """State names and codes are never dropped: they are an unambiguous
    answer to "which region", whatever the cities are called."""
    fixture = {
        "iso2": "JP", "name": "Japan",
        "states": [
            {"iso2": "13", "name": "Tokyo",
             "cities": [{"name": "Osaka"}]},
            {"iso2": "27", "name": "Ōsaka",
             "cities": [{"name": "Osaka"}]},
        ],
    }

    assert gen.build_maps([fixture])["JP"]["osaka"] == "27"


def test_dropped_keys_are_reported():
    dropped = {}
    gen.build_maps([_JP_FIXTURE], dropped=dropped)

    assert "kita" in dropped["JP"]
    assert "ota" in dropped["JP"]


def test_japanese_prefecture_aliases_are_added_for_japan_only():
    maps = gen.build_maps([_JP_FIXTURE, _US_FIXTURE])

    assert maps["JP"]["東京"] == "13"
    assert maps["JP"]["東京都"] == "13"
    assert maps["JP"]["大阪"] == "27"
    assert maps["JP"]["大阪府"] == "27"
    # Not a state in this fixture, so no alias for it either.
    assert "北海道" not in maps["JP"]
    assert not [key for key in maps["US"] if not key.isascii()]


def test_country_key_uses_the_code_load_countries_derives():
    maps = gen.build_maps([_NUMERIC_ISO2_FIXTURE])

    assert "01" not in maps
    assert maps["SC"]["someplace"] == "X1"


def test_the_two_copies_of_the_suffix_pattern_agree():
    """The generator bakes suffix aliases into the table and the runtime
    strips the same suffixes off the lookup string. They are separate
    literals (the generator must not import src), and a drift between them
    fails silently as "no match" rather than as an error."""
    assert (gen.SUFFIX_RE.pattern
            == country_helper_module._STATE_SUFFIX_RE.pattern)


# ── the runtime lookup ───────────────────────────────────────────────────

def test_the_generated_table_ships():
    assert TABLE_PATH.is_file(), (
        "assets/city_to_state.json is missing -- run "
        "python scripts/gen_city_to_state.py")


@pytest.mark.slow
def test_the_shipped_table_matches_a_fresh_generation():
    """Regenerating from the source dataset must reproduce the shipped file
    byte for byte: it pins that the build is deterministic and that nobody
    changed build_maps without re-running the script."""
    if not FULL_DATASET_PATH.is_file():
        pytest.skip("assets/countries+states+cities.json is not present")

    data = orjson.loads(FULL_DATASET_PATH.read_bytes())
    assert orjson.dumps(gen.build_maps(data)) == TABLE_PATH.read_bytes(), (
        "assets/city_to_state.json is stale -- run "
        "python scripts/gen_city_to_state.py")


def test_all_47_prefectures_resolve_from_their_japanese_name():
    """The whole point of the JP alias table: a Japanese user whose
    Start.gg city field is written in Japanese still gets a region."""
    table = orjson.loads(TABLE_PATH.read_bytes())["JP"]

    for code, names in gen.JP_PREFECTURES.items():
        for name in names:
            assert table.get(name) == code, f"{name} did not resolve to {code}"

    assert len(gen.JP_PREFECTURES) == 47


def test_find_state_resolves_a_city_name():
    assert AutonaCountryHelper.FindState("JP", "Shibuya") == "13"


def test_find_state_resolves_a_composite_location_string():
    assert AutonaCountryHelper.FindState("JP", "Shibuya-ku, Tokyo") == "13"


def test_find_state_resolves_japanese_prefecture_names():
    assert AutonaCountryHelper.FindState("JP", "東京") == "13"
    assert AutonaCountryHelper.FindState("JP", "東京都") == "13"


def test_find_state_resolves_a_prefecture_written_in_romaji():
    assert AutonaCountryHelper.FindState("JP", "Osaka") == "27"


def test_find_state_strips_a_prefecture_suffix_off_the_lookup():
    """These forms are not keys in the table; the runtime pattern is what
    reduces them to one."""
    assert AutonaCountryHelper.FindState("JP", "Fukuoka-ken") == "40"
    assert AutonaCountryHelper.FindState("JP", "Kanagawa Prefecture") == "14"


def test_find_state_resolves_a_state_code_verbatim():
    assert AutonaCountryHelper.FindState("US", "CA") == "CA"


def test_find_state_leaves_ambiguous_city_names_unresolved():
    """The real-data counterpart of the build-time drop. "Kawasaki" is a
    city in six prefectures; the widely known one (Kanagawa) is only
    reachable by writing the name out."""
    assert AutonaCountryHelper.FindState("JP", "Kawasaki") is None
    assert AutonaCountryHelper.FindState("JP", "Ota") is None
    assert AutonaCountryHelper.FindState("JP", "Minato") is None
    assert AutonaCountryHelper.FindState("JP", "Kawasaki-shi") == "14"


def test_find_state_returns_none_for_an_unknown_place():
    assert AutonaCountryHelper.FindState("JP", "存在しない地名XYZ") is None


def test_find_state_tolerates_a_missing_country_code():
    """SetData and the Start.gg parser both call this before knowing whether
    the country resolved, so a None country must not raise."""
    assert AutonaCountryHelper.FindState(None, "Tokyo") is None
    assert AutonaCountryHelper.FindState("JP", None) is None
    assert AutonaCountryHelper.FindState("ZZ", "Tokyo") is None


def test_find_state_caches_a_missing_table_instead_of_retrying(
        tmp_path, monkeypatch, cityToStateCache):
    """The table is read cwd-relatively. When it is absent the helper must
    degrade to "no state" and remember that, rather than stat-ing the file
    once per entrant."""
    AutonaCountryHelper._cityToState = None
    monkeypatch.chdir(tmp_path)

    assert AutonaCountryHelper.FindState("JP", "Tokyo") is None
    assert AutonaCountryHelper._cityToState == {}


def test_find_state_survives_a_corrupt_table(
        tmp_path, monkeypatch, cityToStateCache):
    """SetData has no exception handling around FindState, so a truncated
    or hand-edited file must not take a whole set load down."""
    AutonaCountryHelper._cityToState = None
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "city_to_state.json").write_bytes(b'["not", "a map"]')
    monkeypatch.chdir(tmp_path)

    assert AutonaCountryHelper.FindState("JP", "Tokyo") is None
    assert AutonaCountryHelper._cityToState == {}
