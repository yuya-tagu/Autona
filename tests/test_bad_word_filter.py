"""The core bad-word filter censors exactly as the Qt-side one did.

ADR-005 S3e-1 moved the filter from src/Helpers/AutonaBadWordFilter.py into
src/core/BadWordFilter.py: the program locale and the country -> languages /
continent tables, read from AutonaLocaleHelper before, are injected now.
tests/fixtures/bad_word_filter/golden.json holds what the old implementation
returned for a synthetic set of word lists over every language-selection
branch (see record_golden.py there); this replays it on the new class, wired
the way bootstrap_core() wires it -- AutonaLocaleHelper's country lookups and
a program locale read at each call.

Also pinned: the old module's name still censors (it forwards to
BadWordFilter.instance), and a filter built without the callables behaves as
the old one did before the locale was loaded ("en-US", no country
languages).
"""
import json
from pathlib import Path

import pytest
from loguru import logger

from src.core.BadWordFilter import BadWordFilter
from src.core.SettingsManager import SettingsManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads(
    (PROJECT_ROOT / "tests" / "fixtures" / "bad_word_filter" / "golden.json")
    .read_text(encoding="utf-8"))


@pytest.fixture
def country_tables(monkeypatch):
    """AutonaLocaleHelper's real country lookups, loaded from the shipped
    assets/data_countries.json (what bootstrap_core() injects). The tables
    are class attributes shared with the rest of the session, so they are
    put back afterwards."""
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    for name in ("countryToLanguage", "countryToContinent"):
        monkeypatch.setattr(AutonaLocaleHelper, name,
                            getattr(AutonaLocaleHelper, name))
    monkeypatch.chdir(PROJECT_ROOT)
    AutonaLocaleHelper.LoadCountryToLanguage()
    return (AutonaLocaleHelper.GetCountrySpokenLanguages,
            AutonaLocaleHelper.GetCountryContinent)


@pytest.fixture
def word_lists(tmp_path):
    """The golden's synthetic lists, written as the loader reads them."""
    ngword = tmp_path / "assets" / "ngword"
    ngword.mkdir(parents=True)
    for lang, words in GOLDEN["ngword"].items():
        (ngword / f"{lang}.txt").write_text("\n".join(words), encoding="utf-16")
    user_data = tmp_path / "user_data"
    user_data.mkdir()
    (user_data / "badword_whitelist.txt").write_text(
        "\n".join(GOLDEN["whitelist"]), encoding="utf-8")
    (user_data / "badword_blacklist.txt").write_text(
        "\n".join(GOLDEN["blacklist"]), encoding="utf-8")
    return ngword, user_data


@pytest.fixture
def log_sink():
    # The old log line formats a censored value with str.format, which only
    # runs while some sink accepts INFO (with none, loguru returns before
    # formatting). The app always has one; make sure the test does too.
    sink = logger.add(lambda message: None, level="DEBUG")
    yield
    logger.remove(sink)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(SettingsManager, "settings", {})


def _filter(word_lists, locale, country_tables=None):
    ngword, user_data = word_lists
    languages, continent = country_tables or (None, None)
    result = BadWordFilter(
        programLocale=None if locale is None else (lambda: locale["value"]),
        countryLanguages=languages, countryContinent=continent,
        ngwordDir=str(ngword), userDataDir=str(user_data))
    result.Initialize()
    return result


def _outcome(call):
    try:
        return call()
    except Exception as e:
        return {"raises": type(e).__name__}


def test_censoring_matches_the_old_filter(
        word_lists, country_tables, log_sink, enabled):
    locale = {"value": "en-US"}
    censor = _filter(word_lists, locale, country_tables)

    mismatches = []
    for program_locale, country, expected in GOLDEN["results"]:
        locale["value"] = program_locale
        for value, want in zip(GOLDEN["values"], expected):
            got = _outcome(lambda: censor.Censor(value, country))
            if got != want:
                mismatches.append((program_locale, country, value, want, got))

    assert not mismatches, (
        f"{len(mismatches)} results differ from the old filter, e.g.:\n  "
        + "\n  ".join(map(repr, mismatches[:10])))


def test_dict_values_none_and_the_setting_match_the_old_filter(
        word_lists, country_tables, log_sink, enabled, monkeypatch):
    locale = {"value": "en-US"}
    censor = _filter(word_lists, locale, country_tables)

    for program_locale, want in GOLDEN["dict_results"]:
        locale["value"] = program_locale
        value = json.loads(json.dumps(GOLDEN["dict_value"]))
        # Edited in place and returned, the country ignored (as before).
        assert censor.Censor(value, "JP") is value
        assert value == want

    locale["value"] = "en-US"
    assert censor.Censor(None, "US") == GOLDEN["none_result"]

    for settings, want in GOLDEN["disabled_results"]:
        monkeypatch.setattr(SettingsManager, "settings", settings)
        assert [censor.Censor(value, "US")
                for value in ("qcommon", "qenglish x")] == want


def test_the_word_lists_are_loaded_once_and_the_user_lists_created(
        word_lists, enabled):
    ngword, user_data = word_lists
    (user_data / "badword_whitelist.txt").unlink()
    (user_data / "badword_blacklist.txt").unlink()
    censor = BadWordFilter(ngwordDir=str(ngword), userDataDir=str(user_data))
    assert not censor.patterns

    censor.Initialize()
    assert set(censor.patterns) == set(GOLDEN["ngword"])
    assert (user_data / "badword_whitelist.txt").read_text() == ""
    assert (user_data / "badword_blacklist.txt").read_text() == ""
    patterns = dict(censor.patterns)
    censor.Initialize()  # idempotent
    assert dict(censor.patterns) == patterns


def test_without_the_callables_a_region_or_country_adds_nothing(
        word_lists, log_sink, enabled):
    # Defaults: program locale "en-US", no country languages, no continent.
    # "en-US" then tests plain "en" (no "en-us" file, no continent for
    # "-americas"), and a player's country adds no language -- the same
    # filters as the old one under the bare "en" locale with no country.
    censor = _filter(word_lists, None)
    want = next(outputs for program_locale, country, outputs
                in GOLDEN["results"]
                if program_locale == "en" and country is None)
    for country in (None, "JP", "BR"):
        assert [_outcome(lambda: censor.Censor(value, country))
                for value in GOLDEN["values"]] == want


def test_the_old_module_forwards_to_the_core_instance(
        word_lists, country_tables, log_sink, enabled, monkeypatch):
    from src.Helpers.AutonaBadWordFilter import AutonaBadWordFilter

    monkeypatch.setattr(BadWordFilter, "instance",
                        _filter(word_lists, {"value": "pt-BR"}, country_tables))
    want = next(outputs for program_locale, country, outputs
                in GOLDEN["results"]
                if program_locale == "pt-BR" and country == "MX")
    assert [_outcome(lambda: AutonaBadWordFilter.Censor(value, "MX"))
            for value in GOLDEN["values"]] == want
    assert AutonaBadWordFilter.CensorString("qblack") == "***"
    value = {"a": "qcommon", "b": {"c": "qcommon x"}}
    AutonaBadWordFilter.CensorDict(value)
    assert value == {"a": "***", "b": {"c": "*** x"}}
