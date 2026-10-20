import re
import unicodedata
from typing import Union, Dict
from ..core.SettingsManager import SettingsManager
import json
from .AutonaLocaleHelper import AutonaLocaleHelper
import traceback
import os
from collections import defaultdict
from typing import List, Set, Tuple
from loguru import logger


def remove_accents_lower(input_str):
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return u"".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()


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


class AutonaBadWordFilter():
    badWordTries = defaultdict(Trie)
    patterns = defaultdict(re.Pattern)
    whiteList = []
    blackList = []
    _initialized = False

    def Initialize():
        """Build the bad-word tries and load the user white/black lists.

        Used to run at import time; it is now called explicitly from
        src/bootstrap.py so that importing this module has no side effect
        (LoadWordList creates ./user_data/badword_*.txt on first run).
        Idempotent: the tries are cumulative, so re-running would only
        rebuild identical patterns.
        """
        if AutonaBadWordFilter._initialized:
            return
        AutonaBadWordFilter._initialized = True

        # LoadWordList writes into ./user_data; SettingsManager.Initialize()
        # creates that directory, but do not depend on call order here.
        os.makedirs("./user_data", exist_ok=True)

        AutonaBadWordFilter.LoadBadWordList()
        AutonaBadWordFilter.whiteList = AutonaBadWordFilter.LoadWordList(
            "badword_whitelist.txt")
        AutonaBadWordFilter.blackList = AutonaBadWordFilter.LoadWordList(
            "badword_blacklist.txt")

    def LoadBadWordList():
        langs = defaultdict(set)

        try:
            for f in os.listdir("./assets/ngword/"):
                if not f.endswith(".txt"):
                    continue

                words = open(
                    f"./assets/ngword/{f}", 'r', encoding="utf-16").read().splitlines()

                newWords = set(words)
                langs[f.split(".")[0]] = newWords
        except:
            logger.error(traceback.format_exc())

        # Commenting this block until we have a better performing alternative
        # This is too time consuming in cases like loading a huge bracket

        # for langkey, lang in langs.items():
        #     for index, word in enumerate(lang):
        #         word = re.sub("a", "(a|4|\@)", word)

        #         # from i we generate L so that we know which ones we added ourselves
        #         word = re.sub("i", "(i|1|L|!)", word)
        #         word = re.sub("l", "(l|1|i|!)", word)
        #         # Then turn L into l
        #         # Otherwise, we'd have (i|(i|l)) all over the place
        #         word = re.sub("L", "l", word)

        #         # Same logic
        #         word = re.sub("u", "(u|V)", word)
        #         word = re.sub("v", "(v|u)", word)
        #         word = re.sub("V", "v", word)

        #         word = re.sub("o", "(o|0|\@)", word)
        #         word = re.sub("e", "(e|3)", word)
        #         word = re.sub("s", "(s|\$|5)", word)
        #         word = re.sub("t", "(t|7)", word)

        #         langs[langkey][index] = word

        for langkey, lang in langs.items():
            for word in lang:
                AutonaBadWordFilter.badWordTries[langkey].insert(word)

            AutonaBadWordFilter.patterns[langkey] =\
                AutonaBadWordFilter.badWordTries[langkey].build_regex_pattern()

    def CensorString(value: str, playerCountry: str = None):
        langTests = set(["common"])

        # Saving extra languages as [language, country]
        extraLanguages: Set[Tuple[str]] = set()

        userLocale = AutonaLocaleHelper.programLocale.lower()

        # Add program language
        if "-" in userLocale:
            extraLanguages.add(
                (userLocale.split("-")[0], userLocale.split("-")[-1]))
        else:
            extraLanguages.add((userLocale, None))

        # Add languages spoken in player's country
        if playerCountry is not None:
            langs = AutonaLocaleHelper.GetCountrySpokenLanguages(
                playerCountry.upper())
            for lang in langs:
                extraLanguages.add((lang, playerCountry))

        # Process extra languages
        for lang, country in extraLanguages:
            lang = lang.lower()

            if country is not None:
                country = country.lower()

                # Test language-country
                canonical = f"{lang}-{country}"
                if canonical in AutonaBadWordFilter.patterns:
                    langTests.add(canonical)
                    continue

                # Test language-continent
                if country:
                    regionalLang = None
                    continent = AutonaLocaleHelper.GetCountryContinent(country)

                    if continent in ["NA", "SA"]:
                        regionalLang = f"{lang}-americas"

                    if regionalLang and regionalLang in AutonaBadWordFilter.patterns:
                        langTests.add(regionalLang)
                        continue

            # Test language only
            if lang in AutonaBadWordFilter.patterns:
                langTests.add(lang)
                continue

        logger.info(f"AutonaBadWordFilter: using filters [{', '.join(langTests)}]")

        dividers = [" ", "_", ",", ".", "/", "-", "\\", "*"]
        testString = remove_accents_lower(value)
        stringStart = 0

        newString = ""

        for characterPos, character in enumerate(testString):
            if character in dividers:
                substring = testString[stringStart:characterPos]

                if len(substring) > 0:
                    matched = False

                    if substring in AutonaBadWordFilter.blackList:
                        newString += "***"
                        matched = True
                    elif substring in AutonaBadWordFilter.whiteList:
                        pass
                    else:
                        for lang in langTests:
                            if lang not in AutonaBadWordFilter.patterns:
                                continue

                            match = AutonaBadWordFilter.patterns[lang].match(
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

            if substring in AutonaBadWordFilter.blackList:
                newString += "***"
                matched = True
            elif substring in AutonaBadWordFilter.whiteList:
                pass
            else:
                for lang in langTests:
                    if lang not in AutonaBadWordFilter.patterns:
                        continue

                    match = AutonaBadWordFilter.patterns[lang].match(
                        substring)

                    if match:
                        newString += "***"
                        matched = True
                        break
            if not matched:
                newString += value[stringStart:]

        if value != newString:
            logger.info(value, "->", newString)

        return newString

    def CensorDict(dictionary: dict):
        for key, value in dictionary.items():
            if isinstance(value, dict):
                AutonaBadWordFilter.CensorDict(value)
            else:
                # Modify the leaf value
                if type(value) == str:
                    dictionary[key] = AutonaBadWordFilter.CensorString(value)

    def Censor(value: Union[Dict, str, None], countryCode2: str = None):
        if SettingsManager.Get("general.profanity_filter", True) != True:
            return value

        if value == None:
            return value

        if type(value) == str:
            value = AutonaBadWordFilter.CensorString(value, countryCode2)
        if isinstance(value, dict):
            AutonaBadWordFilter.CensorDict(value)

        return value

    def LoadWordList(file):
        try:
            if not os.path.exists(f"./user_data/{file}"):
                with open(f"./user_data/{file}", 'w', encoding="utf-8") as f:
                    f.write("")
            words = open(f"./user_data/{file}", 'r',
                         encoding="utf-8").read().splitlines()
            return set([remove_accents_lower(w) for w in words if len(w) > 0])
        except:
            logger.error(traceback.format_exc())
