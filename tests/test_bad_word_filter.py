"""The core bad-word filter censors exactly as the Qt-side one did.

ADR-005 S3e-1 moved the filter from src/Helpers/AutonaBadWordFilter.py into
src/core/BadWordFilter.py: the program locale and the country -> languages /
continent tables, read from AutonaLocaleHelper before, are injected now.
tests/fixtures/bad_word_filter/golden.json holds what the old implementation
returned for a synthetic set of word lists over every language-selection
branch (see record_golden.py there); this replays it on the new class, wired
the way bootstrap_core() wires it -- AutonaLocaleHelper's country lookups and
a program locale read at each call.

Also pinned: the shared instance's three entry points (the old module kept
forwarding to them until S3e-3 removed it, its callers gone), and a filter
built without the callables behaves as the old one did before the locale
was loaded ("en-US", no country languages).
"""
import json
from pathlib import Path

import pytest
from loguru import logger

from src.core.BadWordFilter import BadWordFilter, Trie
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
    # The app always has a log sink, and loguru only formats a message while
    # one accepts it. Keep a DEBUG one so a log line that formats a value
    # (the old one raised KeyError on "{x}") would fail these tests.
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


def test_the_shared_instance_censors_values_strings_and_dicts(
        word_lists, country_tables, log_sink, enabled, monkeypatch):
    # What the old module's three static methods forwarded to. Its callers
    # (the player card, the commentary panel) call core now (S3e-2 / S3e-3),
    # and the module itself is gone.
    monkeypatch.setattr(BadWordFilter, "instance",
                        _filter(word_lists, {"value": "pt-BR"}, country_tables))
    censor = BadWordFilter.instance
    want = next(outputs for program_locale, country, outputs
                in GOLDEN["results"]
                if program_locale == "pt-BR" and country == "MX")
    assert [_outcome(lambda: censor.Censor(value, "MX"))
            for value in GOLDEN["values"]] == want
    assert censor.CensorString("qblack") == "***"
    value = {"a": "qcommon", "b": {"c": "qcommon x"}}
    censor.CensorDict(value)
    assert value == {"a": "***", "b": {"c": "*** x"}}


def test_no_trie_is_kept_after_the_patterns_are_built(word_lists):
    censor = _filter(word_lists, None)
    # The regexes are compiled lazily, so make the filter build the patterns
    # of the selected lists first; otherwise the checks below prove nothing.
    censor.CensorString("qcommon x", "JP")
    assert censor._compiled, "no pattern was built"
    assert not hasattr(censor, "badWordTries")
    assert not any(isinstance(v, Trie) for v in vars(censor).values())


def test_a_list_is_compiled_only_when_a_censor_first_tests_it(
        word_lists, country_tables, log_sink, enabled):
    # Initialize() reads the words only; the regex of a list is built the
    # first time a CensorString tests it, so the lists no censor uses are
    # never compiled.
    censor = _filter(word_lists, {"value": "en"}, country_tables)
    assert set(censor.patterns) == set(GOLDEN["ngword"])
    assert censor._compiled == {}

    censor.CensorString("qcommon x", "JP")
    # "x" matches no list, so every list selected for an "en" program
    # language and a Japanese player is tested -- and only those.
    assert set(censor._compiled) == {"common", "en", "ja"}
    compiled = dict(censor._compiled)
    censor.CensorString("qcommon y", "JP")
    assert censor._compiled == compiled  # reused, not rebuilt


def test_concurrent_censors_compile_a_list_once(
        word_lists, log_sink, enabled, monkeypatch):
    import threading
    import time

    calls = []
    barrier = threading.Barrier(2)
    real_build = Trie.build_regex_pattern

    def counting_build(self):
        calls.append(threading.get_ident())
        # Widen the compile window so the other thread reaches _Pattern while
        # this one is still building; without the lock both would build.
        time.sleep(0.05)
        return real_build(self)

    monkeypatch.setattr(Trie, "build_regex_pattern", counting_build)
    censor = _filter(word_lists, None)
    results = [None, None]
    errors = []

    def run(index):
        try:
            barrier.wait(timeout=10)
            results[index] = censor.CensorString("qcommon x")
        except Exception as e:  # surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert results[0] == results[1] == "*** x"
    # One build per list tested ("common" and "en"), not one per thread.
    assert set(censor._compiled) == {"common", "en"}
    assert len(calls) == 2


@pytest.mark.parametrize("level", ["DEBUG", "INFO"])
def test_a_value_with_braces_is_censored_and_never_logged(
        word_lists, enabled, level):
    censor = _filter(word_lists, None)
    value = "{x} qcommon"
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)),
                      level=level)
    try:
        got = censor.Censor(value)
    finally:
        logger.remove(sink)
    assert "***" in got
    # At INFO the filter logs nothing at DEBUG, so this check is vacuous
    # there: that case only confirms no exception is raised.
    assert not any("{x}" in m or "qcommon" in m for m in messages)
