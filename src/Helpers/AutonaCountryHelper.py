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

    @staticmethod
    def _iso2_to_flag_emoji(iso2: str) -> str | None:
        """Convert a 2-letter ISO country code to its Unicode flag emoji
        by offsetting each letter into the regional indicator range
        (U+1F1E6..U+1F1FF). ~100ns per call vs ~40ms for
        `countryflag.getflag()`, which saved ~10s at startup across
        250 countries."""
        if not iso2 or len(iso2) != 2 or not iso2.isalpha():
            return None
        offset = 0x1F1E6 - ord('A')
        a, b = iso2.upper()
        return chr(ord(a) + offset) + chr(ord(b) + offset)

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
        data["emoji"] = AutonaCountryHelper._iso2_to_flag_emoji(
            AutonaCountryHelper.countries[country_code]["code"]
        )
        return data

    def LoadCountries():
        try:
            # Prefer the slim countries+states JSON (~2 MB). If absent,
            # fall back to the full countries+states+cities JSON (~46 MB)
            # so old installs still boot. Cities-derived reverse lookup is
            # silently disabled when the slim file is used.
            slim_path = "./assets/countries+states.json"
            full_path = "./assets/countries+states+cities.json"
            if os.path.exists(slim_path):
                data_path = slim_path
            else:
                data_path = full_path
            f = open(data_path, 'r', encoding='utf-8')
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

            for country_code in AutonaCountryHelper.countries.keys():
                item = QStandardItem()
                item.setIcon(
                    QIcon(f'./assets/country_flag/{country_code.lower()}.png'))
                countryData = AutonaCountryHelper.GetBasicCountryInfo(
                    country_code)
                item.setData(countryData, Qt.ItemDataRole.UserRole)
                item.setData(
                    f'{AutonaCountryHelper.countries[country_code]["display_name"]} / {AutonaCountryHelper.countries[country_code]["en_name"]} ({country_code})', Qt.ItemDataRole.EditRole)
                AutonaCountryHelper.countryModel.appendRow(item)

            # Setup cities → states reverse lookup. Skipped when the
            # slim countries+states JSON is in use (no city data present);
            # AutonaCountryHelper.cities stays empty and city-based state
            # resolution silently returns None.
            for country in countries_json:
                for state in country.get("states") or []:
                    for c in state.get("cities") or []:
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
