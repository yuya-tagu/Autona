"""The profanity filter applied to player and commentator names.

Moved here from src/Helpers/AutonaBadWordFilter.py (ADR-005 S3e-1) so the
core can censor without Qt: the old module's only Qt dependency was
AutonaLocaleHelper, read for the program locale and the country -> spoken
languages / continent tables. Those arrive as callables instead
(`programLocale`, `countryLanguages`, `countryContinent`), injected by
bootstrap_core() from AutonaLocaleHelper -- which keeps owning both the
per-run locale (LoadLocale) and the assets/data_countries.json tables
(Initialize), so there is still one reader of that file. Without them the
filter behaves as the old one did before the locale was loaded: "en-US" and
no country languages.

The logic itself is unchanged, line for line: a regex per word list built
from a Trie, the user's white / black lists, the language selection
(program locale, then the player's country: language-country, else
language-americas for NA / SA, else the language alone, plus "common"), and
the per-word scan over the dividers. tests/test_bad_word_filter.py replays
what the old implementation produced (tests/fixtures/bad_word_filter).

Import-inert like the rest of src/core: the lists are read by Initialize(),
which bootstrap_core() calls once on BadWordFilter.instance.
"""
import os
import re
import traceback
from collections import defaultdict
from typing import Dict, Set, Tuple, Union

from loguru import logger

from .SettingsManager import SettingsManager
from .TextHelper import remove_accents_lower


class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end_of_word = False


class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end_of_word = True

    def build_regex_pattern(self):
        regex_strings = []
        self._build_regex_strings(self.root, '', regex_strings)
        pattern = '|'.join(regex_strings)
        return re.compile(pattern)

    def _build_regex_strings(self, node, prefix, regex_strings):
        if node.is_end_of_word:
            regex_strings.append(prefix)
        for char, child_node in node.children.items():
            self._build_regex_strings(child_node, prefix + char, regex_strings)


class BadWordFilter:
    """The word lists and the censoring. One per process (`instance`)."""

    instance: "BadWordFilter" = None

    DIVIDERS = [" ", "_", ",", ".", "/", "-", "\\", "*"]

    def __init__(self, programLocale=None, countryLanguages=None,
                 countryContinent=None, ngwordDir="./assets/ngword/",
                 userDataDir="./user_data"):
        # Read at every CensorString, not here: the shell loads the locale
        # (LoadLocale) after bootstrap_core() built this object.
        self._programLocale = programLocale or (lambda: "en-US")
        self._countryLanguages = countryLanguages or (lambda code: [])
        self._countryContinent = countryContinent or (lambda code: "")
        # Relative by default and resolved when Initialize() reads them, as
        # the old module's paths were (against the process CWD).
        self._ngwordDir = ngwordDir
        self._userDataDir = userDataDir
        self.badWordTries = defaultdict(Trie)
        self.patterns = defaultdict(re.Pattern)
        self.whiteList = []
        self.blackList = []
        self._initialized = False

    def Initialize(self):
        """Build the bad-word tries and load the user white/black lists.

        Idempotent: the tries are cumulative, so re-running would only
        rebuild identical patterns. LoadWordList creates
        user_data/badword_*.txt on first run.
        """
        if self._initialized:
            return
        self._initialized = True

        # LoadWordList writes into ./user_data; SettingsManager.Initialize()
        # creates that directory, but do not depend on call order here.
        os.makedirs(self._userDataDir, exist_ok=True)

        self.LoadBadWordList()
        self.whiteList = self.LoadWordList("badword_whitelist.txt")
        self.blackList = self.LoadWordList("badword_blacklist.txt")

    def LoadBadWordList(self):
        langs = defaultdict(set)

        try:
            for f in os.listdir(self._ngwordDir):
                if not f.endswith(".txt"):
                    continue

                words = open(
                    os.path.join(self._ngwordDir, f), 'r',
                    encoding="utf-16").read().splitlines()

                newWords = set(words)
                langs[f.split(".")[0]] = newWords
        except:
            logger.error(traceback.format_exc())

        # The leetspeak expansion (a -> (a|4|@), ...) that used to sit here,
        # commented out upstream as too slow for a large bracket, was not
        # carried over; see src/Helpers/AutonaBadWordFilter.py in git history.

        for langkey, lang in langs.items():
            for word in lang:
                self.badWordTries[langkey].insert(word)

            self.patterns[langkey] = \
                self.badWordTries[langkey].build_regex_pattern()

    def CensorString(self, value: str, playerCountry: str = None):
        langTests = set(["common"])

        # Saving extra languages as [language, country]
        extraLanguages: Set[Tuple[str]] = set()

        userLocale = self._programLocale().lower()

        # Add program language
        if "-" in userLocale:
            extraLanguages.add(
                (userLocale.split("-")[0], userLocale.split("-")[-1]))
        else:
            extraLanguages.add((userLocale, None))

        # Add languages spoken in player's country
        if playerCountry is not None:
            langs = self._countryLanguages(playerCountry.upper())
            for lang in langs:
                extraLanguages.add((lang, playerCountry))

        # Process extra languages
        for lang, country in extraLanguages:
            lang = lang.lower()

            if country is not None:
                country = country.lower()

                # Test language-country
                canonical = f"{lang}-{country}"
                if canonical in self.patterns:
                    langTests.add(canonical)
                    continue

                # Test language-continent
                if country:
                    regionalLang = None
                    continent = self._countryContinent(country)

                    if continent in ["NA", "SA"]:
                        regionalLang = f"{lang}-americas"

                    if regionalLang and regionalLang in self.patterns:
                        langTests.add(regionalLang)
                        continue

            # Test language only
            if lang in self.patterns:
                langTests.add(lang)
                continue

        logger.info(f"AutonaBadWordFilter: using filters [{', '.join(langTests)}]")

        dividers = self.DIVIDERS
        testString = remove_accents_lower(value)
        stringStart = 0

        newString = ""

        for characterPos, character in enumerate(testString):
            if character in dividers:
                substring = testString[stringStart:characterPos]

                if len(substring) > 0:
                    matched = False

                    if substring in self.blackList:
                        newString += "***"
                        matched = True
                    elif substring in self.whiteList:
                        pass
                    else:
                        for lang in langTests:
                            if lang not in self.patterns:
                                continue

                            match = self.patterns[lang].match(
                                substring)

                            if match:
                                newString += "***"
                                matched = True
                                break
                    if not matched:
                        newString += value[stringStart:characterPos]

                newString += character
                stringStart = characterPos+1

        substring = testString[stringStart:]

        if len(substring) > 0:
            matched = False

            if substring in self.blackList:
                newString += "***"
                matched = True
            elif substring in self.whiteList:
                pass
            else:
                for lang in langTests:
                    if lang not in self.patterns:
                        continue

                    match = self.patterns[lang].match(
                        substring)

                    if match:
                        newString += "***"
                        matched = True
                        break
            if not matched:
                newString += value[stringStart:]

        if value != newString:
            # Kept as it was (S3e-1 moves, it does not fix): loguru formats
            # the message with str.format, so a censored value carrying a
            # named "{field}" raises KeyError here. Pinned by the golden.
            logger.info(value, "->", newString)

        return newString

    def CensorDict(self, dictionary: dict):
        for key, value in dictionary.items():
            if isinstance(value, dict):
                self.CensorDict(value)
            else:
                # Modify the leaf value
                if type(value) == str:
                    dictionary[key] = self.CensorString(value)

    def Censor(self, value: Union[Dict, str, None], countryCode2: str = None):
        if SettingsManager.Get("general.profanity_filter", True) != True:
            return value

        if value == None:
            return value

        if type(value) == str:
            value = self.CensorString(value, countryCode2)
        if isinstance(value, dict):
            self.CensorDict(value)

        return value

    def LoadWordList(self, file):
        path = os.path.join(self._userDataDir, file)
        try:
            if not os.path.exists(path):
                with open(path, 'w', encoding="utf-8") as f:
                    f.write("")
            words = open(path, 'r', encoding="utf-8").read().splitlines()
            return set([remove_accents_lower(w) for w in words if len(w) > 0])
        except:
            logger.error(traceback.format_exc())
