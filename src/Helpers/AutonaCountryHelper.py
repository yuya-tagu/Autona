import re
import threading
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
import os
import traceback
from pathlib import Path

from .AutonaDirHelper import AutonaResolve
from ..TournamentDataProvider import TournamentDataProvider
from .AutonaLocaleHelper import AutonaLocaleHelper
from ..core.TextHelper import remove_accents_lower
import orjson
from loguru import logger
# NOTE: `countryflag` / `country_converter` were dropped here. They were only
# used by the old getflag()-based flag lookup, which was replaced by the
# hand-rolled _iso2_to_flag_emoji() below. country_converter eagerly imports
# pandas, so these dead imports cost ~1.4s of startup for nothing.


class AutonaCountryHelperSignals(QObject):
    countriesUpdated = Signal()


# Japanese municipality/prefecture suffixes stripped when looking a city
# string up, mirroring the aliases scripts/gen_city_to_state.py bakes into
# the table ("Shibuya-ku" -> "shibuya"). Kept here as well so a *state*
# string that carries the suffix still resolves ("Fukuoka-ken",
# "Kanagawa Prefecture"). The pattern must stay identical to the
# generator's SUFFIX_RE; tests/test_find_state.py pins that.
_STATE_SUFFIX_RE = re.compile(r"[\s-](shi|ku|machi|cho|gun|city|ken|prefecture)$")

# Guards the one-time read in _LoadCityToState(). A Start.gg fetch runs on a
# worker thread while SetData runs on the GUI thread, so both can reach the
# first lookup at once and parse the same 2.5 MB file twice.
_cityToStateLock = threading.Lock()


class AutonaCountryHelper(QObject):
    instance: "AutonaCountryHelper" = None

    countries_json = {}
    countries = {}
    cities = {}
    countryModel = None
    signals = AutonaCountryHelperSignals()
    # {country code: {normalized name: state iso2}}, loaded lazily by
    # FindState() on first use. None means "not read yet"; an empty dict
    # means "read and unavailable", so a missing file is not retried on
    # every entrant. Never populated at import time (agent-pitfalls #1).
    _cityToState = None

    def __init__(self) -> None:
        super().__init__()

    def init(self):
        AutonaCountryHelper.LoadCountries()

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
                        city_name = remove_accents_lower(
                            c["name"])
                        if city_name not in AutonaCountryHelper.cities[country["iso2"]]:
                            AutonaCountryHelper.cities[country["iso2"]][city_name] = state["iso2"]

            AutonaCountryHelper.signals.countriesUpdated.emit()
        except:
            AutonaCountryHelper.countries_json = {}
            logger.error(traceback.format_exc())
            return

        # Custom flags (./user_data/additional_flag) are an optional add-on
        # on top of the standard country data loaded above. Isolated in its
        # own try/except and with the directory guaranteed via makedirs
        # (same pattern as AutonaGameAssetManager's ./user_data/games) so a
        # missing/unreadable directory -- e.g. a user who cleaned out
        # user_data (BUG-2) -- never rolls back the standard flags that are
        # already loaded and usable at this point.
        try:
            os.makedirs("./user_data/additional_flag", exist_ok=True)
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
            logger.error(traceback.format_exc())

    @staticmethod
    def _LoadCityToState():
        """Read the pre-computed city/state table, once, on first lookup.

        The table is generated by scripts/gen_city_to_state.py from
        countries+states+cities.json. Loading it lazily keeps the 46 MB
        source out of startup entirely -- and keeps this module's import
        side-effect free."""
        if AutonaCountryHelper._cityToState is not None:
            return AutonaCountryHelper._cityToState

        with _cityToStateLock:
            # Another thread may have finished the read while we waited.
            if AutonaCountryHelper._cityToState is not None:
                return AutonaCountryHelper._cityToState

            table = {}
            try:
                path = "./assets/city_to_state.json"
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        table = orjson.loads(f.read())
                    if not isinstance(table, dict):
                        # A truncated or hand-edited file must not take the
                        # whole set load down: SetData calls FindState with
                        # no exception handling of its own.
                        logger.error(
                            f"{path} is not an object; ignoring it")
                        table = {}
                else:
                    logger.warning(
                        f"{path} is missing; city-based state lookup disabled")
            except:
                logger.error(traceback.format_exc())
                table = {}

            AutonaCountryHelper._cityToState = table
            return table

    @staticmethod
    def FindState(country_code, location_string):
        """Resolve a Start.gg location string to a state iso2 code.

        `location_string` may be a state name/code or a city string
        ("Shibuya-ku, Tokyo"). Parts are tried from the most significant
        (rightmost) one, first verbatim and then with a Japanese
        municipality suffix stripped. Returns the state's iso2 -- the same
        value the state combo boxes carry as "original_code" -- or None."""
        if not country_code or not location_string:
            return None

        entries = AutonaCountryHelper._LoadCityToState().get(country_code)
        if not isinstance(entries, dict) or not entries:
            return None

        # " - " is Start.gg's own separator between city and region.
        split = str(location_string).replace(" - ", ",").split(",")

        for part in split[::-1]:
            part = remove_accents_lower(part.strip())
            if not part:
                continue

            state = entries.get(part)
            if state is None:
                state = entries.get(_STATE_SUFFIX_RE.sub("", part))

            # No logging here on purpose: this runs once per entrant, and
            # 7d1cd1a9 deliberately took the per-entrant DEBUG lines out of
            # the Start.gg path.
            if state is not None:
                return state

        return None

    def GetStates(country_code: str):
        """Returns the states for a country code, or None if the country cannot be found"""

        country_data = AutonaCountryHelper.countries.get(country_code)
        if country_data:
            return country_data.get("states") or {}

        return None
