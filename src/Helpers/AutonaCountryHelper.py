import re
import sys
import unicodedata
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
import os
import traceback
from pathlib import Path

from .AutonaDirHelper import AutonaResolve
from .AutonaDictHelper import deep_get
from ..TournamentDataProvider import TournamentDataProvider
from .AutonaLocaleHelper import AutonaLocaleHelper
import orjson
from loguru import logger
import countryflag
from countryflag.core.exceptions import InvalidCountryError


class AutonaCountryHelperSignals(QObject):
    countriesUpdated = Signal()


class AutonaCountryHelper(QObject):
    instance: "AutonaCountryHelper" = None

    countries_json = {}
    countries = {}
    cities = {}
    countryModel = None
    signals = AutonaCountryHelperSignals()

    def __init__(self) -> None:
        super().__init__()

    def init(self):
        AutonaCountryHelper.LoadCountries()

    def remove_accents_lower(input_str):
        nfkd_form = unicodedata.normalize('NFKD', input_str)
        return u"".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()

    def GetBasicCountryInfo(country_code):
        if not country_code in AutonaCountryHelper.countries:
            return {}

        data = {
            "name": AutonaCountryHelper.countries[country_code]["name"],
            "display_name": AutonaCountryHelper.countries[country_code]["display_name"],
            "en_name": AutonaCountryHelper.countries[country_code]["en_name"],
            "code": AutonaCountryHelper.countries[country_code]["code"],
            "latitude": AutonaCountryHelper.countries[country_code]["latitude"],
            "longitude": AutonaCountryHelper.countries[country_code]["longitude"],
            "asset": f'./assets/country_flag/{country_code.lower()}.png',
        }
    
        try:
            data["emoji"] = countryflag.getflag(AutonaCountryHelper.countries[country_code]["code"])
        except InvalidCountryError:
            logger.warning(f'The following country could not be found in the countryflag library: {AutonaCountryHelper.countries[country_code]["code"]}')
            data["emoji"] = None
        
        return data

    def LoadCountries():
        try:
            f = open("./assets/countries+states+cities.json",
                     'r', encoding='utf-8')
            countries_json = orjson.loads(f.read())
            AutonaCountryHelper.countries_json = countries_json

            # Setup countries - states
            for c in countries_json:
                try:
                    # Load display name
                    display_name = c.get("name")

                    if c.get("translations", {}):
                        locale = AutonaLocaleHelper.programLocale
                        if locale.replace("_", "-") in c.get("translations", {}):
                            display_name = c.get("translations", {})[locale.replace(
                                "_", "-")]
                        elif re.split("-|_", locale)[0] in c.get("translations", {}):
                            display_name = c.get("translations", {})[re.split(
                                "-|_", locale)[0]]

                    # Load display name
                    export_name = c["name"]

                    if c.get("translations", {}):
                        locale = AutonaLocaleHelper.exportLocale
                        if locale.replace("_", "-") in c.get("translations", {}):
                            export_name = c.get("translations", {})[locale.replace(
                                "_", "-")]
                        elif re.split("-|_", locale)[0] in c.get("translations", {}):
                            export_name = c.get("translations", {})[re.split(
                                "-|_", locale)[0]]

                    ccode = c.get("iso2") if not c.get("iso2").isdigit() else "".join([
                        word[0] for word in re.split(r'\s+|-', c.get("name"))])

                    AutonaCountryHelper.countries[c["iso2"]] = {
                        "name": export_name,
                        "display_name": display_name,
                        "en_name": c.get("name"),
                        "code": ccode,
                        "latitude": c.get("latitude"),
                        "longitude": c.get("longitude"),
                        "states": {}
                    }

                    for s in c.get("states", []):
                        if s.get("iso2") is None:
                            continue

                        scode = s.get("iso2")

                        AutonaCountryHelper.countries[c["iso2"]]["states"][s["iso2"]] = {
                            "name": s.get("name"),
                            "code": scode,
                            "original_code": s.get("iso2"),
                            "latitude": s.get("latitude"),
                            "longitude": s.get("longitude"),
                        }
                except:
                    pass

            # Setup model
            AutonaCountryHelper.countryModel = QStandardItemModel()

            noCountry = QStandardItem()
            noCountry.setData({}, Qt.ItemDataRole.UserRole)
            AutonaCountryHelper.countryModel.appendRow(noCountry)

            for i, country_code in enumerate(AutonaCountryHelper.countries.keys()):
                item = QStandardItem()
                item.setIcon(
                    QIcon(f'./assets/country_flag/{country_code.lower()}.png'))
                countryData = AutonaCountryHelper.GetBasicCountryInfo(
                    country_code)
                item.setData(countryData, Qt.ItemDataRole.UserRole)
                item.setData(
                    f'{AutonaCountryHelper.countries[country_code]["display_name"]} / {AutonaCountryHelper.countries[country_code]["en_name"]} ({country_code})', Qt.ItemDataRole.EditRole)
                AutonaCountryHelper.countryModel.appendRow(item)

            # Setup cities - states for reverse search
            for country in countries_json:
                for state in country.get("states"):
                    for c in state["cities"]:
                        if country["iso2"] not in AutonaCountryHelper.cities:
                            AutonaCountryHelper.cities[country["iso2"]] = {}
                        city_name = AutonaCountryHelper.remove_accents_lower(
                            c["name"])
                        if city_name not in AutonaCountryHelper.cities[country["iso2"]]:
                            AutonaCountryHelper.cities[country["iso2"]][city_name] = state["iso2"]

            AutonaCountryHelper.signals.countriesUpdated.emit()

            AdditionalFlags = os.listdir("./user_data/additional_flag")

            AdditionalFlagsFiltered = []
            for flag in AdditionalFlags:
                filename = os.path.basename(flag)
                ext = filename.split(".")[-1]
                #  Remove flags with less than 3 characters
                if len(filename.removesuffix("."+ext)) >= 3:
                    AdditionalFlagsFiltered.append(flag)
            AdditionalFlags = AdditionalFlagsFiltered

            if AdditionalFlags:
                separator = QStandardItem()
                separator.setData("    " + QApplication.translate("app",
                                  "Custom Flags").upper() + "    ", Qt.ItemDataRole.EditRole)
                separator.setEnabled(False)
                separator.setSelectable(False)
                AutonaCountryHelper.countryModel.appendRow(separator)

            for flag in AdditionalFlags:
                item = QStandardItem()
                item.setIcon(QIcon(f"./user_data/additional_flag/{flag}"))
                item.setData({
                    "name": flag[:-4],
                    "display_name": flag[:-4],
                    "en_name": flag[:-4],
                    "code": flag[:-4],
                    "asset": f'./user_data/additional_flag/{flag}'
                }, Qt.ItemDataRole.UserRole)
                item.setData(flag[:-4], Qt.ItemDataRole.EditRole)
                AutonaCountryHelper.countryModel.appendRow(item)
        except:
            AutonaCountryHelper.countries_json = {}
            logger.error(traceback.format_exc())

    def FindState(countryCode, city):
        # State explicit?
        # Normalize parts of city string
        split = city.replace(" - ", ",").split(",")

        logger.debug(f"Finding State from city string [{city}]")

        for part in split[::-1]:
            part = part.strip()

            state = next(
                (st for st in AutonaCountryHelper.countries.get(countryCode, {}).get("states", {}).values(
                ) if AutonaCountryHelper.remove_accents_lower(st["code"]) == AutonaCountryHelper.remove_accents_lower(part)),
                None
            )
            if state is None:
                state = next(
                    (st for st in AutonaCountryHelper.countries.get(countryCode, {}).get("states", {}).values(
                    ) if AutonaCountryHelper.remove_accents_lower(st["name"]) == AutonaCountryHelper.remove_accents_lower(part)),
                    None
                )
            if state is not None:
                logger.debug(
                    f"State was explicit: [{city}] -> [{part}] = {state}")
                return state["original_code"]

        # No, so get by City
        for part in split[::-1]:
            part = part.strip()

            state = AutonaCountryHelper.cities.get(countryCode, {}).get(
                AutonaCountryHelper.remove_accents_lower(part), None)

            if state is not None:
                logger.debug(f"Got state from city name: [{city}] -> [{part}] = {state}")
                return state

        return None


    def GetStates(country_code: str):
        """Returns the states for a country code, or None if the country cannot be found"""

        country_data = AutonaCountryHelper.countries.get(country_code)
        if country_data:
            return country_data.get("states") or {}

        return None

    def GetCities(country_code: str, state_code: str):
        """Returns the cities for a country+state code, or None if the state can't be found"""

        states = AutonaCountryHelper.get_states(country_code)
        if not states:
            return None

        state = states.get(state_code, None)
        if not state:
            return None

        return state.cities


AutonaCountryHelper.instance = AutonaCountryHelper()
