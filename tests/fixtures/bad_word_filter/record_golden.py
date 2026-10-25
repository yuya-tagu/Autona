"""Record golden.json (next to this file) from the PRE-S3e-1 implementation.

ADR-005 S3e-1 moved the bad-word filter out of
src/Helpers/AutonaBadWordFilter.py (which read the program locale and the
country -> language / continent tables from AutonaLocaleHelper, i.e. Qt)
into the Qt-free src/core/BadWordFilter.py, which takes those as injected
callables. tests/test_bad_word_filter.py replays golden.json against the new
implementation; this is the script that recorded it.

It drives the OLD API -- the static `AutonaBadWordFilter.Censor` reading
`AutonaLocaleHelper.programLocale` -- so it cannot run against the current
tree. Run it against a copy of the last commit before S3e-1 (0f6c276d):

    mkdir ../autona-pre-s3e1
    git archive 0f6c276d src | tar -x -C ../autona-pre-s3e1
    python tests/fixtures/bad_word_filter/record_golden.py ../autona-pre-s3e1 \\
        tests/fixtures/bad_word_filter/golden.json

Arguments: <old checkout> <output json>. The old checkout's src/ is what gets
imported. The word lists are synthetic (NGWORD / WHITELIST / BLACKLIST
below: made-up tokens, so the fixture carries no real profanity), written
into a throwaway working directory as the old loader reads them (utf-16
ngword files, utf-8 user lists), next to a copy of this project's
assets/data_countries.json (the real country table: which languages a
player's country adds is part of what is pinned). The inputs are stored in
golden.json too, so the test rebuilds the same directory from it alone.

What the corpus covers: every branch of the language selection (program
locale with and without a region, language-country files such as zh-cn,
the "-americas" files through the NA / SA continents, a country adding its
spoken languages, an unknown or empty country), the white / black lists
(folded with remove_accents_lower), every divider, prefix matching, a word
with a regex metacharacter (the words are not escaped), an upper-case word
(never matches: only the tested text is lowered), a value whose folding
changes its length (the output slices the original by the folded
positions), dict values (no country), the setting turned off, None -- and
the old log line, which formats the censored value with str.format and so
raises for a value carrying a named "{field}" (recorded as such).
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

NGWORD = {
    "common": ["qcommon", "q.x"],
    "en": ["qenglish", "QUPPER"],
    "en-americas": ["qamerican"],
    "ja": ["ほげ"],
    "zh-cn": ["qchinese"],
    "pt": ["qportuguese"],
    "pt-americas": ["qbrazil"],
    "fr": ["qfrench"],
    "es": ["qspanish"],
    "de": ["qgerman"],
}
WHITELIST = ["QEnglishOK", "Ōk"]
BLACKLIST = ["QBlack", "zébra"]

LOCALES = ["en-US", "en", "ja", "pt-BR", "pt", "zh-CN", "fr-CA", "de-DE",
           "ko", "es-MX"]
COUNTRIES = [None, "US", "us", "BR", "MX", "JP", "CA", "CN", "FR", "XX", ""]
VALUES = [
    "", "clean", "qcommon", "QCommon", "qcommonfoo", "fooqcommon", "qax",
    "q.x", "qenglish", "QEnglish rocks", "qenglishok", "QEnglishOK",
    "qamerican", "ほげ", "ほげほげ", "ぼげ", "qchinese",
    "qportuguese qbrazil", "qfrench_qspanish,qgerman",
    "a/qenglish\\b*qcommon-c.d", "qblack", "Zébra", "zebra!", "QÉNGLISH",
    "ﬁ qenglish", "  qcommon  ", "qupper", "QUPPER", "ok", "Ōk",
    "{x} qcommon", "{} qcommon", "{x} clean",
]
DICT_LOCALES = ["en-US", "ja"]
DICT_VALUE = {
    "gamerTag": "qenglish",
    "nested": {"name": "qcommon x", "n": 3, "deeper": {"t": "ほげ"}},
    "list": ["qcommon"],
    "none": None,
    "clean": "fine",
}
DISABLED_SETTINGS = [{"general": {"profanity_filter": False}},
                     {"general": {"profanity_filter": "true"}}]


def _run(callable_):
    try:
        return callable_()
    except Exception as e:  # recorded: the old log line can raise
        return {"raises": type(e).__name__}


def main(old_checkout, output):
    old_checkout = Path(old_checkout).resolve()
    output = Path(output).resolve()
    os.environ.setdefault("QT_API", "pyside6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(old_checkout))

    work = Path(tempfile.mkdtemp(prefix="autona-badword-golden-"))
    try:
        (work / "assets" / "ngword").mkdir(parents=True)
        for lang, words in NGWORD.items():
            (work / "assets" / "ngword" / f"{lang}.txt").write_text(
                "\n".join(words), encoding="utf-16")
        shutil.copyfile(PROJECT_ROOT / "assets" / "data_countries.json",
                        work / "assets" / "data_countries.json")
        (work / "user_data").mkdir()
        (work / "user_data" / "badword_whitelist.txt").write_text(
            "\n".join(WHITELIST), encoding="utf-8")
        (work / "user_data" / "badword_blacklist.txt").write_text(
            "\n".join(BLACKLIST), encoding="utf-8")
        os.chdir(work)

        # Silenced, but not without a sink: with no handler at all loguru
        # returns before formatting, and the old log line's str.format (which
        # raises for a censored value carrying "{x}") would not run as it
        # does in the app.
        from loguru import logger
        logger.remove()
        logger.add(lambda message: None, level="DEBUG")

        from src.core.SettingsManager import SettingsManager
        from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
        from src.Helpers.AutonaBadWordFilter import AutonaBadWordFilter
        assert Path(sys.modules["src"].__file__).resolve().is_relative_to(
            old_checkout), "imported the current tree, not the old checkout"

        AutonaLocaleHelper.LoadCountryToLanguage()
        AutonaBadWordFilter.Initialize()

        results = []
        for locale in LOCALES:
            AutonaLocaleHelper.programLocale = locale
            for country in COUNTRIES:
                results.append([locale, country, [
                    _run(lambda: AutonaBadWordFilter.Censor(value, country))
                    for value in VALUES]])

        dict_results = []
        for locale in DICT_LOCALES:
            AutonaLocaleHelper.programLocale = locale
            value = json.loads(json.dumps(DICT_VALUE))
            returned = AutonaBadWordFilter.Censor(value, "JP")
            assert returned is value, "Censor(dict) no longer edits in place"
            dict_results.append([locale, returned])

        AutonaLocaleHelper.programLocale = "en-US"
        none_result = AutonaBadWordFilter.Censor(None, "US")
        disabled_results = []
        for settings in DISABLED_SETTINGS:
            SettingsManager.settings = settings
            disabled_results.append([settings, [
                AutonaBadWordFilter.Censor(value, "US")
                for value in ("qcommon", "qenglish x")]])
        SettingsManager.settings = {}

        golden = {
            "ngword": NGWORD,
            "whitelist": WHITELIST,
            "blacklist": BLACKLIST,
            "values": VALUES,
            "results": results,
            "dict_value": DICT_VALUE,
            "dict_results": dict_results,
            "none_result": none_result,
            "disabled_results": disabled_results,
        }
    finally:
        os.chdir(PROJECT_ROOT)
        shutil.rmtree(work, ignore_errors=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(golden, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {output}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
