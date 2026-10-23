"""The country catalog against the pre-S1c recording (ADR-005 S1c).

`src/core/CountryCatalog.py` took the data half of AutonaCountryHelper: the
country table with its localized names, the regions, the custom flags and
the city -> region lookup. tests/fixtures/countries/golden.json was recorded
from the old helper, under three locale pairs, before the move (by
tests/fixtures/countries/record_golden.py, run against the pre-S1c tree):

- the country table, every country's row data (GetBasicCountryInfo) and the
  country model's rows (those are checked against the Qt adapter in
  tests/test_country_helper_model.py);
- what the legacy `get_states` returned for every country, twice: before any
  player widget had shown the country, and after the real LoadStates had
  run for it -- which wrote each region's flag `asset` into the helper's
  shared table, so the Web payload depended on what the desktop had shown.

The catalog never writes into its table; `States()` returns the regions
with `asset` resolved, i.e. the second recording, byte for byte. The first
one differs from it in exactly that trailing key, which is pinned too.

Everything here is Qt-free. The catalog reads cwd-relatively (as the old
helper did), so the tests run in a scratch directory with `assets` linked
in and a custom flag directory of their own -- never the developer's real
user_data.
"""
import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from src.core.CountryCatalog import CountryCatalog, flag_emoji
from tests.conftest import _link_tree

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads(
    (PROJECT_ROOT / "tests" / "fixtures" / "countries" / "golden.json")
    .read_text(encoding="utf-8"))


def digest(value):
    """What the recording stored: json.dumps as Socket.IO sends it."""
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()[:16]


def _withoutAsset(states):
    return {code: {key: value for key, value in state.items()
                   if key != "asset"}
            for code, state in states.items()}


@pytest.fixture(scope="module")
def countryDir(tmp_path_factory):
    """A working directory with the shipped assets and the fixture flags."""
    directory = tmp_path_factory.mktemp("country_catalog")
    _link_tree(directory / "assets", PROJECT_ROOT / "assets")
    flags = directory / "user_data" / "additional_flag"
    flags.mkdir(parents=True)
    for name in GOLDEN["customFlags"]:
        (flags / name).write_bytes(b"")
    try:
        yield directory
    finally:
        # The junction is removed by hand, never followed (see conftest).
        os.rmdir(directory / "assets")


@pytest.fixture
def inCountryDir(countryDir, monkeypatch):
    monkeypatch.chdir(countryDir)
    return countryDir


def _loaded(label):
    locale = GOLDEN["locales"][label]
    catalog = CountryCatalog(
        locales=lambda: (locale["program"], locale["export"]))
    assert catalog.Load() is True
    return catalog


# ── the recording ────────────────────────────────────────────────────────

@pytest.mark.parametrize("label", list(GOLDEN["scenarios"]))
def test_the_country_table_matches_the_recording(inCountryDir, label):
    golden = GOLDEN["scenarios"][label]
    catalog = _loaded(label)
    snap = catalog.Current()

    assert list(snap.countries) == golden["codes"]
    assert digest(snap.countries) == golden["countriesDigest"]
    assert {code: digest(catalog.BasicCountryInfo(code))
            for code in golden["codes"]} == golden["basicInfoDigests"]
    for code, info in golden["basicInfo"].items():
        assert json.dumps(catalog.BasicCountryInfo(code)) == json.dumps(info)
    assert catalog.BasicCountryInfo("XX-NOPE") == {}

    # The custom flag rows (the tail of the recorded model after its
    # separator), in listing order. The model handed the data back as a
    # QVariantMap, so only the content is comparable here.
    tail = golden["modelRowsTail"]
    assert tail[0][1] is None and tail[0][2] is False   # the separator
    assert [(text, data) for text, data in snap.customFlagRows] \
        == [(row[0], row[1]) for row in tail[1:]]
    # "ab.png" is shorter than three characters and is left out.
    assert "ab" not in [text for text, _data in snap.customFlagRows]


def test_states_match_the_recording_after_load_states(inCountryDir):
    """The legacy get_states payload, byte for byte as Socket.IO sends it,
    for every country -- in the form it had once the desktop had shown the
    country (every region with its flag `asset`)."""
    golden = GOLDEN["states"]
    catalog = _loaded("en")

    assert {code: digest(catalog.States(code))
            for code in golden["afterDigests"]} == golden["afterDigests"]
    assert json.dumps(catalog.States("JP")) == json.dumps(golden["afterJP"])
    # Windows will not open CON.png: that region's flag ships as _CON.png.
    assert catalog.States("GB")["CON"] == golden["afterGB_CON"]
    assert catalog.States("XX-NOPE") is golden["unknown"] is None


def test_the_only_difference_to_a_country_never_shown_is_the_asset(
        inCountryDir):
    """Before S1c a country no player widget had shown came back without
    `asset`. Dropping that one trailing key gives that recording back."""
    golden = GOLDEN["states"]
    catalog = _loaded("en")

    assert {code: digest(_withoutAsset(catalog.States(code)))
            for code in golden["pristineDigests"]} == golden["pristineDigests"]
    assert json.dumps(_withoutAsset(catalog.States("JP"))) \
        == json.dumps(golden["pristineJP"])
    for state in catalog.States("JP").values():
        assert list(state)[-1] == "asset"


# ── readers hand out copies ──────────────────────────────────────────────

def test_readers_never_write_into_the_table(inCountryDir):
    """LoadStates and the Start.gg standings used to write `asset` into the
    shared region dicts and hand those dicts on into the state."""
    catalog = _loaded("en")
    snap = catalog.Current()
    before = digest(snap.countries)

    states = catalog.States("JP")
    states["13"]["name"] = "changed"
    states["13"]["extra"] = True
    one = catalog.State("JP", "13")
    one["name"] = "changed"
    info = catalog.BasicCountryInfo("JP")
    info["name"] = "changed"
    row = catalog.CountryByCode("JP")
    row["name"] = "changed"

    assert digest(snap.countries) == before
    assert "asset" not in snap.countries["JP"]["states"]["13"]
    assert catalog.States("JP")["13"]["name"] == "Tokyo"
    assert catalog.BasicCountryInfo("JP")["name"] == "Japan"
    assert catalog.CountryByCode("JP")["name"] == "Japan"


def test_state_resolves_one_region_or_nothing(inCountryDir):
    catalog = _loaded("en")

    assert catalog.State("JP", "13") == catalog.States("JP")["13"]
    assert list(catalog.State("JP", "13"))[-1] == "asset"
    # A raw Start.gg string the table has no key for, an empty code, an
    # unknown country.
    assert catalog.State("JP", "Tokyo") == {}
    assert catalog.State("JP", "") == {}
    assert catalog.State("XX-NOPE", "13") == {}


# ── lookups (the S1d readers) ────────────────────────────────────────────

def test_country_by_code_searches_countries_then_custom_flags(inCountryDir):
    catalog = _loaded("en")

    assert catalog.CountryByCode("JP") == catalog.BasicCountryInfo("JP")
    assert catalog.CountryByCode("Agender") == {
        "name": "Agender", "display_name": "Agender", "en_name": "Agender",
        "code": "Agender",
        "asset": "./user_data/additional_flag/Agender.png"}
    assert catalog.CountryByCode("XX-NOPE") is None
    assert catalog.CountryByCode(None) is None


def test_country_code_from_english_name(inCountryDir):
    catalog = _loaded("ja")

    # The English name, whatever the display locale.
    assert catalog.CountryCodeFromEnName("Japan") == "JP"
    assert catalog.CountryCodeFromEnName("United States") == "US"
    assert catalog.CountryCodeFromEnName("日本") is None
    assert catalog.CountryCodeFromEnName(None) is None


def test_flag_emoji():
    assert flag_emoji("JP") == "\U0001F1EF\U0001F1F5"
    assert flag_emoji("jp") == "\U0001F1EF\U0001F1F5"
    assert flag_emoji("J") is None
    assert flag_emoji("12") is None
    assert flag_emoji(None) is None


# ── loading ──────────────────────────────────────────────────────────────

def test_construction_reads_and_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    catalog = CountryCatalog()

    assert not catalog.IsLoaded()
    assert catalog.States("JP") is None
    assert catalog.BasicCountryInfo("JP") == {}
    assert os.listdir(tmp_path) == []


def test_a_failed_load_keeps_what_was_loaded(inCountryDir, tmp_path,
                                             monkeypatch):
    catalog = _loaded("en")
    snap = catalog.Current()

    monkeypatch.chdir(tmp_path)      # no dataset here
    assert catalog.Load() is False

    assert catalog.Current() is snap
    assert catalog.IsLoaded()
    # The custom flag directory is only guaranteed by a load that read the
    # standard data (as before).
    assert not (tmp_path / "user_data").exists()


def test_a_dataset_that_is_not_a_list_fails_the_load(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "countries+states.json").write_text("3")
    monkeypatch.chdir(tmp_path)
    catalog = CountryCatalog()

    assert catalog.Load() is False
    assert not catalog.IsLoaded()


def test_a_broken_country_entry_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "countries+states.json").write_text(json.dumps([
        {"iso2": "AA", "name": "Aland", "states": [
            {"iso2": "A1", "name": "One"}, {"name": "No code"}]},
        {"name": "No iso2"},
        # The regions fail half-way: the country stays, with what was read.
        {"iso2": "BB", "name": "Bland", "states": [
            {"iso2": "B1", "name": "One"}, None]},
    ]))
    monkeypatch.chdir(tmp_path)
    catalog = CountryCatalog()

    assert catalog.Load() is True

    assert list(catalog.Current().countries) == ["AA", "BB"]
    assert list(catalog.States("AA")) == ["A1"]
    assert list(catalog.States("BB")) == ["B1"]
    assert (tmp_path / "user_data" / "additional_flag").is_dir()


def test_a_reload_publishes_a_new_snapshot(inCountryDir):
    """Readers that took the old snapshot keep a consistent one."""
    catalog = _loaded("en")
    old = catalog.Current()

    assert catalog.Load() is True

    assert catalog.Current() is not old
    assert digest(catalog.Current().countries) == digest(old.countries)


def test_readers_on_other_threads_see_whole_loads(inCountryDir):
    """Load() swaps one reference; a reader on another thread sees either
    nothing yet or every country, never a half-built table."""
    catalog = CountryCatalog()
    seen = set()
    stop = threading.Event()

    def read():
        while not stop.is_set():
            seen.add(len(catalog.Current().countries))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        for _ in range(3):
            assert catalog.Load() is True
    finally:
        stop.set()
        reader.join(timeout=10)

    assert seen <= {0, len(GOLDEN["scenarios"]["en"]["codes"])}
