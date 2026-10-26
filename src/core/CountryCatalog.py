"""The country catalog: the Qt-free source of truth for countries and regions.

ADR-005 S1c. Everything the app knows about countries -- the standard list
with its localized names, each country's regions ("states"), the custom
flags a user dropped into ./user_data/additional_flag, and the city -> region
table the Start.gg auto-fill resolves against -- lives here as plain dicts.
The Qt side (`src/Helpers/AutonaCountryHelper.py`) only turns the rows into
the shared country QStandardItemModel; it no longer holds any of the data.

Snapshots
---------
A load builds a complete `CountrySnapshot` off to the side and swaps the
reference in one assignment (the GameAssetCatalog pattern). Nothing mutates
a published snapshot; the dicts inside are plain dicts by design, so that is
a convention, and every reader below that hands data to a caller returns a
copy -- the callers put it into the state, into a Start.gg payload or into
a Qt model, and the old helper's habit of handing out its own dicts is how
a region's `asset` ended up written back into the shared table (S1c).

A region's flag `asset` is resolved when a reader is asked for it, never
stored: whether ./assets/state_flag/... exists is a filesystem question, and
the answer is the same one the old per-country rebuild computed every time.

Locking rules
-------------
- `Load()` is the only writer, serialised by one `threading.Lock`. Readers
  take no lock: they read `self._current` once and work from that reference.
- The city -> region table is a separate file with its own lock, read once
  by `Load()` -- not on the first lookup, which can come from a player load
  under StateManager.lock (ADR-005 S3e-3). A lookup in a catalog nothing
  loaded still reads it then (a Start.gg worker and the GUI thread may get
  there at once).
- No observer channel and no callbacks: the Qt adapter's `countriesUpdated`
  signal is the notification.

Construction is side-effect free; bootstrap_core() creates the instance, and
the Qt adapter drives `Load()` (after the per-run locale is known).
"""
import os
import re
import threading
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

import orjson
from loguru import logger

from .DictHelper import deep_clone
from .TextHelper import remove_accents_lower

# The slim countries+states dataset (~2 MB) is what ships. The full
# countries+states+cities one (~46 MB) is read only when the slim one is
# absent, so an old install still boots; its city lists are not used (the
# city lookup reads CITY_TO_STATE_PATH).
COUNTRIES_PATH = "./assets/countries+states.json"
COUNTRIES_FULL_PATH = "./assets/countries+states+cities.json"
CITY_TO_STATE_PATH = "./assets/city_to_state.json"
CUSTOM_FLAG_DIR = "./user_data/additional_flag"

# Japanese municipality/prefecture suffixes stripped when looking a city
# string up, mirroring the aliases scripts/gen_city_to_state.py bakes into
# the table ("Shibuya-ku" -> "shibuya"). Kept here as well so a *state*
# string that carries the suffix still resolves ("Fukuoka-ken",
# "Kanagawa Prefecture"). The pattern must stay identical to the
# generator's SUFFIX_RE; tests/test_find_state.py pins that.
_STATE_SUFFIX_RE = re.compile(r"[\s-](shi|ku|machi|cho|gun|city|ken|prefecture)$")


def flag_emoji(iso2) -> Optional[str]:
    """The Unicode flag emoji of a 2-letter ISO code, by offsetting each
    letter into the regional indicator range (U+1F1E6..U+1F1FF). ~100ns per
    call vs ~40ms for `countryflag.getflag()`, which saved ~10s at startup
    across 250 countries."""
    if not iso2 or len(iso2) != 2 or not iso2.isalpha():
        return None
    offset = 0x1F1E6 - ord('A')
    a, b = iso2.upper()
    return chr(ord(a) + offset) + chr(ord(b) + offset)


def state_flag_path(country_code, state_code) -> Optional[str]:
    """./assets/state_flag/<country>/<state>.png, or None if it is not there.

    Windows will not open a file named CON.png, so that one region's flag
    ships as _CON.png."""
    path = (f'./assets/state_flag/{country_code}/'
            f'{"_CON" if state_code == "CON" else state_code}.png')
    if not os.path.exists(path):
        return None
    return path


def _translated(c, locale, default):
    """The country's name in `locale` (exact tag, then base language)."""
    if c.get("translations", {}):
        if locale.replace("_", "-") in c.get("translations", {}):
            return c.get("translations", {})[locale.replace("_", "-")]
        elif re.split("-|_", locale)[0] in c.get("translations", {}):
            return c.get("translations", {})[re.split("-|_", locale)[0]]
    return default


@dataclass(frozen=True)
class CountrySnapshot:
    """One load of the country data, immutable once published.

    `countryRows` / `customFlagRows` are the country combo's rows in display
    order as `(text, data)` pairs -- the blank first row and the "custom
    flags" separator the combo adds are not part of them.
    """
    # {iso2: {name, display_name, en_name, code, latitude, longitude,
    #         states: {region iso2: {name, code, original_code, latitude,
    #                                longitude}}}}
    countries: dict = field(default_factory=dict)
    # {iso2: the country row's data} -- `asset` (flag path) and `emoji` on
    # top of the country's own fields; what the model and the state carry.
    basicInfo: dict = field(default_factory=dict)
    countryRows: tuple = ()
    customFlagRows: tuple = ()
    # {en_name: code}, first country wins (the old linear scan's answer).
    codeByEnName: dict = field(default_factory=dict)
    # {code: row data}, first row wins -- standard countries, then custom
    # flags, the order the combo is searched in.
    rowByCode: dict = field(default_factory=dict)


class CountryCatalog:
    instance: "CountryCatalog" = None

    def __init__(self, locales: Callable = None):
        """`locales()` returns (program locale, export locale) at call time.
        Injected because the values live in AutonaLocaleHelper, which
        imports Qt (same pattern as GameAssetCatalog)."""
        self._locales = locales or (lambda: ("en-US", "en-US"))
        self._lock = threading.Lock()
        self._current = CountrySnapshot()
        # {country code: {normalized name: region iso2}}, read on the first
        # FindState(). None means "not read yet"; an empty dict means "read
        # and unavailable", so a missing file is not retried per entrant.
        self._cityToState = None
        self._cityToStateLock = threading.Lock()

    # ── readers (no lock: one reference read, then work from it) ─────────

    def Current(self) -> CountrySnapshot:
        """The published snapshot itself, not a copy: READ-ONLY. The dataclass
        is frozen but the dicts and row data inside it are not, and every
        reader in the process shares them without a lock -- modifying one
        changes what the Web, Start.gg and the country model see (the bug
        S1c removed). Callers that need to change what they read use the
        readers below, which return copies."""
        return self._current

    def IsLoaded(self) -> bool:
        """True once a load has published at least one country."""
        return bool(self._current.countries)

    def BasicCountryInfo(self, iso2) -> dict:
        """A copy of the country's row data (what a player's `country`
        holds), or {} for an unknown code."""
        info = self._current.basicInfo.get(iso2)
        if info is None:
            return {}
        return deep_clone(info)

    def CountryByCode(self, code) -> Optional[dict]:
        """A copy of the first combo row whose `code` matches -- a standard
        country or a custom flag -- or None. This is the lookup a player
        widget does by walking the country model."""
        row = self._current.rowByCode.get(code)
        if row is None:
            return None
        return deep_clone(row)

    def CountryCodeFromEnName(self, en_name) -> Optional[str]:
        """The code of the country whose English name is `en_name` (how a
        Start.gg location's country is mapped), or None."""
        return self._current.codeByEnName.get(en_name)

    def States(self, iso2) -> Optional[dict]:
        """The country's regions keyed by region iso2, each a new dict with
        its flag `asset` resolved now; {} for a country without regions and
        None for an unknown code. The legacy `get_states` payload."""
        country = self._current.countries.get(iso2)
        if not country:
            return None
        code = country.get("code")
        return {stateCode: self._WithAsset(code, stateCode, data)
                for stateCode, data in (country.get("states") or {}).items()}

    def StateCodes(self, iso2) -> Optional[dict]:
        """The country's regions as States() keys them, each a new dict of
        its `code` and `original_code` only: no flag `asset`, whose
        resolution is a filesystem check per region. For a caller under
        StateManager.lock -- a player load matching a region code
        (PlayerService.ApplyData, ADR-005 S3e-3 review). {} / None as
        States()."""
        country = self._current.countries.get(iso2)
        if not country:
            return None
        return {stateCode: {"code": data.get("code"),
                            "original_code": data.get("original_code")}
                for stateCode, data in (country.get("states") or {}).items()}

    def State(self, iso2, stateCode) -> dict:
        """One region as States() returns it, or {} when the country or the
        region is unknown (a raw Start.gg string the table has no key for)."""
        country = self._current.countries.get(iso2)
        if not country or not stateCode:
            return {}
        data = (country.get("states") or {}).get(stateCode)
        if not data:
            return {}
        return self._WithAsset(country.get("code"), stateCode, data)

    @staticmethod
    def _WithAsset(countryCode, stateCode, data):
        state = dict(data)
        state["asset"] = state_flag_path(countryCode, stateCode)
        return state

    def FindState(self, country_code, location_string) -> Optional[str]:
        """Resolve a Start.gg location string to a region iso2 code.

        `location_string` may be a region name/code or a city string
        ("Shibuya-ku, Tokyo"). Parts are tried from the most significant
        (rightmost) one, first verbatim and then with a Japanese
        municipality suffix stripped. Returns the region's iso2 -- the same
        value the state combo boxes carry as "original_code" -- or None."""
        if not country_code or not location_string:
            return None

        entries = self._LoadCityToState().get(country_code)
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

    def _LoadCityToState(self):
        """Read the pre-computed city/region table, once: from Load(), or
        on the first lookup in a catalog nothing loaded.

        The table is generated by scripts/gen_city_to_state.py from
        countries+states+cities.json, so the 46 MB source stays out of the
        runtime entirely; the table itself is ~2.6 MB."""
        if self._cityToState is not None:
            return self._cityToState

        with self._cityToStateLock:
            # Another thread may have finished the read while we waited.
            if self._cityToState is not None:
                return self._cityToState

            table = {}
            try:
                path = CITY_TO_STATE_PATH
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        table = orjson.loads(f.read())
                    if not isinstance(table, dict):
                        # A truncated or hand-edited file must not take the
                        # whole set load down: a player load
                        # (PlayerService.ApplyData) calls FindState with no
                        # exception handling of its own.
                        logger.error(
                            f"{path} is not an object; ignoring it")
                        table = {}
                else:
                    logger.warning(
                        f"{path} is missing; city-based state lookup disabled")
            except Exception:
                logger.error(traceback.format_exc())
                table = {}

            self._cityToState = table
            return table

    # ── writer ───────────────────────────────────────────────────────────

    def Load(self) -> bool:
        """Read the country data under the current locale and publish it.

        Returns False -- and keeps the previous snapshot -- when the dataset
        cannot be read at all. A country entry that does not parse is
        skipped, as before; the custom flags are optional and never fail the
        load.

        The city -> region table is read here too, up front (once: it is not
        localised, so a reload for a new locale keeps it). Its first lookup
        can come from a player load, which resolves a region under
        StateManager.lock (PlayerService.ApplyData, ADR-005 S3e-3) -- a read
        there held every other writer up."""
        self._LoadCityToState()
        with self._lock:
            programLocale, exportLocale = self._locales()
            try:
                # Prefer the slim countries+states JSON (~2 MB). If absent,
                # fall back to the full countries+states+cities JSON
                # (~46 MB) so old installs still boot.
                path = COUNTRIES_PATH if os.path.exists(COUNTRIES_PATH) \
                    else COUNTRIES_FULL_PATH
                with open(path, 'r', encoding='utf-8') as f:
                    countries_json = orjson.loads(f.read())
                countries = self._ParseCountries(
                    countries_json, programLocale, exportLocale)
                snapshot = self._Build(countries, self._ReadCustomFlags())
            except Exception:
                logger.error(traceback.format_exc())
                return False

            self._current = snapshot
            return True

    @staticmethod
    def _ParseCountries(countries_json, programLocale, exportLocale):
        countries = {}
        for c in countries_json:
            try:
                display_name = _translated(c, programLocale, c.get("name"))
                export_name = _translated(c, exportLocale, c["name"])

                ccode = c.get("iso2") if not c.get("iso2").isdigit() else "".join([
                    word[0] for word in re.split(r'\s+|-', c.get("name"))])

                # Published before its regions are filled in: a region list
                # that fails half-way keeps the country and the regions read
                # so far (the old loader's behaviour).
                entry = {
                    "name": export_name,
                    "display_name": display_name,
                    "en_name": c.get("name"),
                    "code": ccode,
                    "latitude": c.get("latitude"),
                    "longitude": c.get("longitude"),
                    "states": {}
                }
                countries[c["iso2"]] = entry

                for s in c.get("states", []):
                    if s.get("iso2") is None:
                        continue

                    entry["states"][s["iso2"]] = {
                        "name": s.get("name"),
                        "code": s.get("iso2"),
                        "original_code": s.get("iso2"),
                        "latitude": s.get("latitude"),
                        "longitude": s.get("longitude"),
                    }
            except Exception:
                pass
        return countries

    @staticmethod
    def _ReadCustomFlags():
        """The custom flag rows, in directory listing order.

        An optional add-on on top of the standard data: isolated so a
        missing or unreadable directory -- e.g. a user who cleaned out
        user_data (BUG-2) -- costs the custom flags only, and the directory
        is guaranteed the way ./user_data/games is."""
        rows = []
        try:
            os.makedirs(CUSTOM_FLAG_DIR, exist_ok=True)
            for flag in os.listdir(CUSTOM_FLAG_DIR):
                filename = os.path.basename(flag)
                ext = filename.split(".")[-1]
                # Flags whose name is shorter than 3 characters are skipped.
                if len(filename.removesuffix("." + ext)) < 3:
                    continue
                rows.append((flag[:-4], {
                    "name": flag[:-4],
                    "display_name": flag[:-4],
                    "en_name": flag[:-4],
                    "code": flag[:-4],
                    "asset": f'{CUSTOM_FLAG_DIR}/{flag}'
                }))
        except Exception:
            logger.error(traceback.format_exc())
            return ()
        return tuple(rows)

    @staticmethod
    def _Build(countries, customFlagRows):
        basicInfo = {}
        countryRows = []
        codeByEnName = {}
        rowByCode = {}
        for iso2, entry in countries.items():
            info = {
                "name": entry["name"],
                "display_name": entry["display_name"],
                "en_name": entry["en_name"],
                "code": entry["code"],
                "latitude": entry["latitude"],
                "longitude": entry["longitude"],
                "asset": f'./assets/country_flag/{iso2.lower()}.png',
            }
            info["emoji"] = flag_emoji(entry["code"])
            basicInfo[iso2] = info
            countryRows.append(
                (f'{entry["display_name"]} / {entry["en_name"]} ({iso2})', info))
            codeByEnName.setdefault(entry["en_name"], entry["code"])
            rowByCode.setdefault(info["code"], info)
        for _text, data in customFlagRows:
            rowByCode.setdefault(data["code"], data)
        return CountrySnapshot(
            countries=countries,
            basicInfo=basicInfo,
            countryRows=tuple(countryRows),
            customFlagRows=customFlagRows,
            codeByEnName=codeByEnName,
            rowByCode=rowByCode,
        )
