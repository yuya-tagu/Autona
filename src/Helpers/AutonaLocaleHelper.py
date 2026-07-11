import json
from qtpy import QtGui, QtWidgets, QtCore
from qtpy.QtCore import *
from qtpy.QtGui import *
import os
import traceback
from loguru import logger

from src.SettingsManager import SettingsManager
from src.StateManager import StateManager
from .AutonaDictHelper import deep_clone
from .AutonaDirHelper import AutonaResolve


class AutonaLocaleHelperSignals(QObject):
    localeChanged = Signal()


class AutonaLocaleHelper(QObject):
    exportLocale = "en-US"
    programLocale = "en-US"
    fgTermLocale = "en-US"
    matchNames = {}
    phaseNames = {}
    translator = None
    languages = []
    remapping = {}
    countryToLanguage = {}
    countryToContinent = {}

    # Double-elimination "TOP N" ladders. N is the number of players still
    # alive in the tournament when the round starts: winners rounds land on
    # the 3*2^n series, losers rounds additionally hit the 2^n series in
    # between. Sized for events up to ~10000 entrants — the actual first
    # round (e.g. "Winners TOP300") uses the real entrant count and is
    # inserted dynamically when a tournament is loaded.
    WINNERS_TOP_TOTALS = [3 * 2 ** n for n in range(3, 12)]  # 24..6144
    LOSERS_TOP_TOTALS = sorted(
        {2 ** n for n in range(4, 14)} | {3 * 2 ** n for n in range(2, 12)})  # 12..8192

    def LoadLocale():
        settingsProgramLocale = SettingsManager.Get("program_language", None)
        settingsExportLocale = SettingsManager.Get("game_asset_language", None)
        settingsFgTerm = SettingsManager.Get("fg_term_language", None)

        if settingsProgramLocale and settingsProgramLocale != "default":
            current_locale = [settingsProgramLocale]
        else:
            current_locale = QtCore.QLocale().uiLanguages()

        logger.info("OS locale: " + str(current_locale))

        oldTranslator = AutonaLocaleHelper.translator
        i18n_dir = AutonaResolve('src/i18n')

        if oldTranslator:
            QGuiApplication.instance().removeTranslator(oldTranslator)

        AutonaLocaleHelper.translator = QTranslator()
        localeFound = False
        for locale in current_locale:
            if localeFound:
                break
            for f in os.listdir(f"{i18n_dir}/"):
                if f.endswith(".qm"):
                    lang = f.split("_", 1)[1].split(".")[0]
                    if lang == locale:
                        AutonaLocaleHelper.translator.load(
                            QLocale(lang), f"{i18n_dir}/{f}")
                        AutonaLocaleHelper.programLocale = locale
                        localeFound = True
                        break
                    elif lang == locale.split("-")[0]:
                        AutonaLocaleHelper.translator.load(
                            QLocale(lang), f"{i18n_dir}/{f}")
                        AutonaLocaleHelper.programLocale = locale
                        localeFound = True
                        break

        QGuiApplication.instance().installTranslator(AutonaLocaleHelper.translator)

        if settingsExportLocale and settingsExportLocale != "default":
            AutonaLocaleHelper.exportLocale = settingsExportLocale
        else:
            AutonaLocaleHelper.exportLocale = current_locale[0]

        if settingsFgTerm and settingsFgTerm != "default":
            AutonaLocaleHelper.fgTermLocale = settingsFgTerm
        else:
            AutonaLocaleHelper.fgTermLocale = current_locale[0]

    def LoadLanguages():
        try:
            i18n_dir = AutonaResolve('src/i18n')
            languages_json = json.load(
                open(f"{i18n_dir}/mapping.json", 'rt', encoding='utf-8'))
            AutonaLocaleHelper.languages = languages_json.get("languages")
            AutonaLocaleHelper.remapping = languages_json.get("remapping")
        except Exception as e:
            raise Exception(f"Error loading languages") from e

    def LoadCountryToLanguage():
        try:
            languages_json = json.load(
                open("./assets/data_countries.json", 'rt', encoding='utf-8'))
            AutonaLocaleHelper.countryToLanguage = {ccode: cdata.get(
                "languages") for ccode, cdata in languages_json.items()}
            AutonaLocaleHelper.countryToContinent = {ccode: cdata.get(
                "continent") for ccode, cdata in languages_json.items()}
        except Exception as e:
            raise Exception(f"Error loading languages") from e

    def GetCountrySpokenLanguages(countryCode2: str):
        return AutonaLocaleHelper.countryToLanguage.get(countryCode2.upper(), [])

    def GetCountryContinent(countryCode2: str):
        return AutonaLocaleHelper.countryToContinent.get(countryCode2.upper(), "")

    def LoadRoundNames():
        # Load default round names and translation
        try:
            tterm_dir = AutonaResolve('./src/i18n/tournament_term')
            original_term_names: dict = json.load(
                open(f"{tterm_dir}/en.json", 'rt', encoding='utf-8'))
            term_names = deep_clone(original_term_names)

            for f in os.listdir(f"{tterm_dir}/"):
                if f.endswith(".json"):
                    lang = f.split(".")[0]

                    if lang == AutonaLocaleHelper.fgTermLocale:
                        # We found the exact language file
                        translatedRoundNames = json.load(
                            open(f"{tterm_dir}/{f}", 'rt', encoding='utf-8'))
                        term_names = original_term_names.copy()
                        term_names.update(translatedRoundNames)
                        break
                    elif lang == AutonaLocaleHelper.fgTermLocale.split("-")[0]:
                        # We found a more generic language file
                        # Good enough if we don't find a specific one
                        translatedRoundNames = json.load(
                            open(f"{tterm_dir}/{f}", 'rt', encoding='utf-8'))
                        term_names = original_term_names.copy()
                        term_names.update(translatedRoundNames)

            AutonaLocaleHelper.matchNames = term_names.get("match")
            AutonaLocaleHelper.phaseNames = term_names.get("phase")
        except:
            logger.error(traceback.format_exc())

        # Load user round names in a separate try/catch
        try:
            term_names: dict = json.load(
                open("./user_data/tournament_terms.json", 'rt', encoding='utf-8'))

            term_names["phase"] = {
                k: v for k, v in term_names.get("phase", {}).items() if v}
            term_names["match"] = {
                k: v for k, v in term_names.get("match", {}).items() if v}

            AutonaLocaleHelper.phaseNames.update(term_names["phase"])
            AutonaLocaleHelper.matchNames.update(term_names["match"])
        except:
            logger.warning("Custom Tournament Terms were not found and/or loaded.")

    def GetMatchNameCandidates():
        """Ordered match (round) name candidates for the editable combos.

        Order follows tournament prominence: winners rounds (named finals
        first, then the TOP ladder ascending), losers rounds likewise,
        grand finals, then generic round-robin rounds.
        """
        names = AutonaLocaleHelper.matchNames
        candidates = []

        def add(text):
            if text and text not in candidates:
                candidates.append(text)

        for key in ("winners_final", "winners_semi_final",
                    "winners_quarter_final"):
            add(names.get(key))
        winners_top = names.get("winners_top_n", "Winners TOP{0}")
        for n in AutonaLocaleHelper.WINNERS_TOP_TOTALS:
            add(winners_top.format(n))

        for key in ("losers_final", "losers_semi_final",
                    "losers_quarter_final", "losers_top8"):
            add(names.get(key))
        losers_top = names.get("losers_top_n", "Losers TOP{0}")
        for n in AutonaLocaleHelper.LOSERS_TOP_TOTALS:
            add(losers_top.format(n))

        add(names.get("grand_final"))
        add(names.get("grand_final_reset"))

        round_template = names.get("round", "")
        if "{0}" in round_template:
            for n in range(1, 6):
                add(round_template.format(n))

        return candidates

    def GetRemaps(language: str):
        for remap, langs in AutonaLocaleHelper.remapping.items():
            if language.replace('-', '_') in langs:
                logger.info("Loaded remap: " + str(remap))
                return remap
        return None


AutonaLocaleHelper.LoadLanguages()
AutonaLocaleHelper.LoadCountryToLanguage()
