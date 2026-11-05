"""The player database as the player list reads and writes it (ADR-005 S4m).

What the sidebar's player list shows -- the registered players and the loaded
tournament's entrants, each row with the current game's mains -- and the two
things it does to a record: save one, built from identifiers, and delete one.

**A save does not damage what was not edited.** The database holds what no
catalog vouches for: a character the game does not have now (mods off), the -1
of one with no skins, a free-form region (a start.gg location nothing
resolved), a country whose flag cannot be read. Save replaces the record, so:

- the mains: not sent, no game's change. Sent, they replace the loaded game's
  only, and name it (`game`, which the read gives: `ForgetSelection` leaves the
  last game's codename in the state). An entry the player already has -- the
  same character, skin and variant, as a row gives it -- is saved unchecked;
  only what is new is checked against the catalog;
- the country and the region: not sent, they stay as stored, whatever that is.
  "" clears. A region sent alone is checked against the stored country, and a
  country that changes takes the stored region with it (region codes repeat
  between countries, and it would name another place).

A row's `country_code` / `state_code` are what Save takes, read as the editor
reads a stored record (PlayerService.ApplyData): a region's own code stays, a
free-form one is resolved through the city table, and what is left is "" (a
code the catalog does not know shows no country). So a row's "" can hide a
stored value, and a page sends these two only when the operator changed them.
Until the catalog is loaded nothing can be told apart and the stored values are
given as they are.

The other texts are the record's: what is not sent is blank afterwards. A
sponsor or tag that changed is another record, which inherits nothing.

**Save writes the record, never the state.** It builds what
PlayerService.PrepareSaveToDB builds from a slot -- same keys, same order, same
values for the same pick (tests/test_player_directory.py compares the two) --
without a slot, and goes through the same SaveRecordToDB, so the order of the
saves and the other games' mains are one thing. One difference: a record with
no mains sent has an empty `mains`, where the slot's has none when no game is
loaded: AddPlayers replaces the whole mains of a record that has none, and the
operator's edit of a name would lose every game's characters.

The bad-word filter is not applied, as it is not to what an operator types into
a card. Nothing here logs a name: these are people's (the callers' op log has
the keys of a request only).

Module functions, as Top8Service / CommentaryService: no state of its own. The
catalogs default to the process's at call time; a test hands its own. Qt-free.
"""
from .CountryCatalog import CountryCatalog
from .GameAssetCatalog import GameAssetCatalog
from .PlayerDB import PlayerDB
from .PlayerService import NoteForDB, NoteFromDB, PlayerService, PreparedSave
from .StreamService import NO_SEED, StreamService

# What Save refuses, saving nothing.
NO_TAG = "no_tag"
UNKNOWN_COUNTRY = "unknown_country"
UNKNOWN_STATE = "unknown_state"
UNKNOWN_CHARACTER = "unknown_character"
UNKNOWN_SKIN = "unknown_skin"
UNKNOWN_VARIANT = "unknown_variant"
OTHER_GAME = "other_game"
SUPERSEDED = "superseded"


def _Games(games):
    return games if games is not None else GameAssetCatalog.instance


def _Countries(countries):
    return countries if countries is not None else CountryCatalog.instance


def _Codename(snapshot):
    return (snapshot.game or {}).get("codename")


# ── reading ──────────────────────────────────────────────────────────────

def _Text(value) -> str:
    """A stored text as a row gives it: "" for none. The file can hold
    anything, so whatever else it is, it is written out."""
    return "" if value is None else str(value)


def _SkinOf(entry) -> int:
    """A stored main's skin row, as stored: the -1 of a character with no
    skins too (none lower, which Save does not take). 0 for none, and for one
    that is not a number (as PlayerService._SkinNumber reads it)."""
    try:
        return max(int(entry[1]), -1) if len(entry) > 1 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _MainsRow(player, codename) -> list:
    """`codename`'s mains of a stored record, each [en_name, skin, variant];
    entries that name no character are left out."""
    entries = PlayerDB.ParseMains(player.get("mains")).get(codename) \
        if codename else None
    if not isinstance(entries, list):
        return []
    rows = []
    for entry in entries:
        if isinstance(entry, list) and entry \
                and isinstance(entry[0], str) and entry[0]:
            variant = entry[2] if len(entry) > 2 else ""
            rows.append([entry[0], _SkinOf(entry),
                         variant if isinstance(variant, str) else ""])
    return rows


def _LocationReader(countries):
    """A function: stored record -> (country_code, state_code), as the module
    docstring says. One per read: a database has thousands of rows and only a
    few countries, and looking a country up copies its rows."""
    loaded = countries.IsLoaded()
    regions = {}

    def regionsOf(country):
        # None: no such country. {} for one without regions (a custom flag).
        if country not in regions:
            regions[country] = countries.StateCodes(country) or {} \
                if countries.CountryByCode(country) is not None else None
        return regions[country]

    def read(player):
        country = player.get("country_code")
        state = player.get("state_code")
        country = country if isinstance(country, str) else ""
        state = state if isinstance(state, str) else ""
        if not loaded:
            return country, state
        rows = regionsOf(country) if country else None
        if rows is None:
            return "", ""
        if state and state not in rows:
            # FindState's answer is the region's code. A free-form name is
            # looked up only here, once a region's own code did not match.
            state = countries.FindState(country, state) or ""
            state = state if state in rows else ""
        return country, state

    return read


def _Row(key, player, codename, readLocation) -> dict:
    country, state = readLocation(player)
    return {
        "key": key,
        "prefix": _Text(player.get("prefix")),
        "gamerTag": _Text(player.get("gamerTag")),
        "name": _Text(player.get("name")),
        "twitter": _Text(player.get("twitter")),
        "country_code": country,
        "state_code": state,
        "custom_textbox": NoteFromDB(_Text(player.get("custom_textbox"))),
        "mains": _MainsRow(player, codename),
    }


def _Stored(db) -> list:
    """(key, record) of the rows that can be shown: the file can hold a row
    with no tag, whose key is not a string. Caller holds `db.lock`."""
    return [(key, player) for key, player in db.Items()
            if isinstance(key, str) and isinstance(player, dict)]


def Registered(games=None, countries=None) -> dict:
    """{"game", "players"}: the codename the rows' mains are of (None with no
    game loaded), read once with them, and every player of the database as a
    row, in the order of their keys (ignoring case): {key, prefix, gamerTag,
    name, twitter, country_code, state_code, custom_textbox, mains}. `key` is
    what Delete takes, and `mains` the loaded game's, [] without one."""
    codename = _Codename(_Games(games).Current())
    readLocation = _LocationReader(_Countries(countries))
    db = PlayerDB.instance
    # The records are live dicts (PlayerDB.Get): read under the lock.
    with db.lock:
        rows = [_Row(key, player, codename, readLocation)
                for key, player in _Stored(db)]
    return {"game": codename,
            "players": sorted(rows, key=lambda row: row["key"].lower())}


def Entrants(games=None, countries=None) -> dict:
    """{"game", "entrants"}: `game` as Registered's, and the loaded
    tournament's entrants, best seed first (the order StreamService keeps them
    in; [] with no tournament), as rows: `seed` (None for an unseeded entrant),
    and the database's record for the tag -- the same fields as Registered. An
    entrant the database does not have is the tag alone, with `key` None and
    every other field empty.

    A tag is matched as PlayerDB.GetPlayerFromTag matches it (the gamer tag,
    ignoring case; the first row wins), through one index for the whole read:
    entrants times records would be millions of comparisons under the lock."""
    codename = _Codename(_Games(games).Current())
    stream = StreamService.instance
    entrants = stream.TopEntrants() if stream is not None else []
    if not entrants:
        return {"game": codename, "entrants": []}
    readLocation = _LocationReader(_Countries(countries))
    db = PlayerDB.instance
    rows = []
    with db.lock:
        byTag = {}
        for key, player in _Stored(db):
            tag = player.get("gamerTag")
            if isinstance(tag, str):
                byTag.setdefault(tag.lower(), (key, player))
        for entrant in entrants:
            tag = entrant.get("gamerTag") or ""
            seed = entrant.get("seed")
            found = byTag.get(tag.lower()) if tag else None
            if found is not None:
                row = _Row(found[0], found[1], codename, readLocation)
            else:
                row = _Row(None, {"gamerTag": tag}, codename, readLocation)
            row["seed"] = None if not seed or seed >= NO_SEED else seed
            rows.append(row)
    return {"game": codename, "entrants": rows}


# ── writing ──────────────────────────────────────────────────────────────

def _MainsRecord(picks, games, snapshot, stored):
    """The records of `picks`, each [en_name, skin row, variant?]: (entries,
    None) or (None, a refusal). One the player already has (`stored`: _MainsRow
    of the record) goes as it is; the others are checked against one snapshot
    of the game. A character with no skins takes any skin and stores -1, what
    its card's empty skin combo publishes."""
    has = {tuple(entry) for entry in stored}
    entries = []
    for pick in picks:
        name, skin = pick[0], pick[1]
        variant = (pick[2] if len(pick) > 2 else "") or ""
        if (name, skin, variant) in has:
            entries.append([name, skin, variant])
            continue
        if games.CharacterRow(name, snapshot=snapshot) is None:
            return None, UNKNOWN_CHARACTER
        skins = len(snapshot.skinRows.get(name, ()))
        if not skins:
            skin = -1
        elif not 0 <= skin < skins:
            return None, UNKNOWN_SKIN
        if variant and games.VariantRow(variant, snapshot) is None:
            return None, UNKNOWN_VARIANT
        entries.append([name, skin, variant])
    return entries, None


def _Location(fields, country, state, countries):
    """The record's (country_code, state_code, None) from what `fields` sends
    over the stored `country` and `state`, or (None, None, a refusal). What is
    not sent stays, uninspected (module docstring)."""
    if "country_code" in fields:
        sent = fields["country_code"] or ""
        if sent:
            row = countries.CountryByCode(sent)
            if row is None:
                return None, None, UNKNOWN_COUNTRY
            sent = row.get("code")
        if "state_code" not in fields and sent != (country or ""):
            state = None
        country = sent
    if "state_code" in fields:
        state = fields["state_code"] or ""
        if state:
            region = countries.State(country, state) \
                if country and isinstance(country, str) else None
            if not region:
                return None, None, UNKNOWN_STATE
            state = region.get("code")
    return country, state, None


def Save(fields, games=None, countries=None):
    """Save a player from `fields` (the caller has checked the types): the
    texts prefix, gamerTag, name, twitter, custom_textbox (with real line
    breaks), and -- each only if it is to change, the module docstring says
    what stays -- country_code, state_code, and `mains`, a list of [en_name,
    skin row, variant?] of the game `game` names, the loaded one or OTHER_GAME.
    So are mains with no game loaded: nothing to store them under.

    The record replaces the one of the same key (sponsor + tag): a text that
    is missing is blank afterwards. A tag that changed is another player, as it
    always was.

    {"key", "created"}, or what is refused (the constants above), saving
    nothing: SUPERSEDED when a save of this player read later was saved first.
    On the calling thread, with no lock held (SaveRecordToDB)."""
    games = _Games(games)
    countries = _Countries(countries)

    def text(name):
        value = fields.get(name)
        return "" if value is None else str(value)

    if not text("gamerTag").strip():
        return NO_TAG

    record = {
        "prefix": text("prefix"),
        "gamerTag": text("gamerTag"),
        "name": text("name"),
        "twitter": text("twitter"),
        "custom_textbox": NoteForDB(text("custom_textbox")),
        "mains": {},
    }
    snapshot = games.Current()
    codename = _Codename(snapshot)
    db = PlayerDB.instance
    key = PlayerDB.PlayerTag(record)
    # Numbered before what is kept is read, so that a save read after this one
    # wins over it. Outside the lock: the number's lock comes first
    # (SaveRecordToDB).
    sequence = PlayerService.NextSaveSequence()
    # The record is a live dict (PlayerDB.Get): read under the lock.
    with db.lock:
        created = not db.Contains(key)
        stored = db.Get(key)
        stored = stored if isinstance(stored, dict) else {}
        storedMains = _MainsRow(stored, codename)
        storedCountry = stored.get("country_code")
        storedState = stored.get("state_code")

    if "mains" in fields:
        picks = fields["mains"] or []
        # With no game selected there is nothing to store the picks under (the
        # tables of the last one stay: GameAssetCatalog.ForgetSelection).
        if fields.get("game") != codename or (picks and not codename):
            return OTHER_GAME
        entries, refused = _MainsRecord(picks, games, snapshot, storedMains)
        if refused is not None:
            return refused
        if codename:
            record["mains"] = {codename: entries}

    country, state, refused = _Location(
        fields, storedCountry, storedState, countries)
    if refused is not None:
        return refused
    if country:
        record["country_code"] = country
    if state:
        record["state_code"] = state

    if not PlayerService.SaveRecordToDB(PreparedSave(record, sequence)):
        return SUPERSEDED
    return {"key": key, "created": created}


def Delete(key) -> bool:
    """Delete the player stored under `key` (a row's). False when the database
    has no such key, saving and announcing nothing."""
    db = PlayerDB.instance
    if not db.Contains(key):
        return False
    db.DeletePlayer(key)
    return True
