"""The archive clipper's results (ADR-006 A5): the tournament's sets a job is
given, with their characters, prepared before the job is made
(``POST /api/v1/archive/results:prepare``; the job's ``Create`` takes them
as they are, unchanged).

Three kinds (``Prepare``):

- ``startgg``: a start.gg event's completed sets, from its URL. The fetch
  is the caller's (``fetchEvent``: StartGGDataProvider.FetchEventResults --
  core does not reach the provider); a set comes with each game's
  characters as start.gg's report says them (start.gg character ids);
- ``matchlog``: the match log's records of one day (core/MatchLogService.py,
  ADR-006 A2), of one tournament when it is named;
- ``json``: the results the page read from a file.

Each answers vodsplit's results (``ArchiveJobService.CheckResults``) and a
summary (the sets, how many got their characters from where).

The game
--------
A start.gg event's characters are its videogame's: the scanned game of that
start.gg id; none when the scan has no such game (no character for any set:
the report and the mains would be another game's). Only an event with no
videogame takes the loaded game (``GameAssetCatalog.Current()``). A match
log record's characters are of the game it names (``game``: the game loaded
when it was written, MatchLogService); a record that names none (written
before records named their game) gives no character -- its own or the
mains': the loaded game now need not be the one played then. A record's
characters are taken for a start.gg set only when it names the event's
game. JSON results take the loaded game. A character the game does not
know -- a codename or a start.gg id it has no character for -- is left out;
the set stays.

Characters (``_Compose``)
-------------------------
Per side, the first source with a character the game knows, strongest
first: the match log's record of the set (``scoreboard``), start.gg's report
(``report``), the player's first main in the player database (``mains``:
the page says 推定). A record names a side's characters by its games (in
the order first played; the board's current one when no game names one).
A set's ``c1`` / ``c2`` are the display names (the program locale's), at
most ``MAX_SHOWN`` in that order, joined by "/"; ``games`` (one ``{c1, c2}``
per game, the display names or null) are the record's when it names any
known character, else the report's; ``char_source`` is the weakest source
a side took (so 推定 shows when either side is a guess). The codenames
behind ``c1`` / ``c2``, the game and the codenames' icons are kept in
``NAMESPACE`` (``{"autona": {"game", "c1": [...], "c2": [...], "icons":
{codename: file}}}``; a file is relative to the game's folder,
``GameAssetCatalog.GameCharacters``' "icon": the icon pack's file of the
lowest skin, named by the pack's prefix and postfix; a codename with no
icon file is not in ``icons``): vodsplit passes a set's other keys through
into sets.json, so the page's set list can show the icons of a job made
earlier. A set with no character has none of these keys.

A JSON set that already has characters (``c1``, ``c2``, ``games`` or
``char_source``) is the file's: left as it is (counted ``given``). A JSON
set's own ``NAMESPACE`` is dropped first (only Prepare writes it). JSON
results take no record (the file names no day to pick records by, D4).

Which record is which set
-------------------------
First by start.gg's set id: a record whose ``set_id`` is a set's id (a set
loaded from start.gg) is that set's -- when several are, the one with the
set's score (oriented), if only one has it. Such a record is that set's or
none's. Its sides are the set's by the names (the same pair, swapped or
not; else one name on its side; else as they are).

The sets and records left are matched by name: a record and a result set
are the same match when their players' names are the same, normalised
(``NormalizeName``), on either side (a record on swapped sides gives its
characters swapped). Within one pair of names: one set and one record are
matched as they are; otherwise a set and a record are matched when their
scores are the same (oriented) and that is the only such record for the set
and the only such set for the record. Anything else is no record. The match
log kind needs none of this: each set is made from its own record.

Which records (D4): by day and tournament name only. A match log day is the
file's (``<YYYYMMDD>.jsonl``, the day a record was first written), a
tournament the record's name as written (exact). A start.gg event's are the
records of its days (the local day of its start through the day after its
end -- a stream runs past midnight --, at most ``MAX_EVENT_DAYS``) whose
tournament name is start.gg's (NFKC, case and whitespace ignored; no
sponsor prefix is dropped), or whose ``set_id`` is one of the event's sets
(a set loaded from start.gg, whatever the display name was).

Reading the match log
---------------------
A line is a whole record; the last line of an ``id`` is the record (the
earlier ones are the same record before a change). A line that is not a
JSON object with a string ``id`` is skipped (a line a crash cut short, a
line the writer is still appending). A record is used when both names are
there (non-empty strings) and its score is two whole numbers >= 0, not 0-0
(a record whose games were all taken back); one left open (``closed``
false: a crash, or the match still on) is used as it was last written.
Records keep the order of their first line.

Qt-free; nothing is made or read on import. Every function reads the
catalog, the player database and the files on the calling thread. Only
``Days`` keeps anything: each day file's counts, by the file's (mtime,
size), so the page's list does not read every day again.
"""
import datetime
import json
import os
import re
import threading
import unicodedata

from loguru import logger

from .ArchiveJobService import (
    FETCH_FAILED, INVALID, NOT_FOUND, ArchiveError, CheckResults)
from .GameAssetCatalog import GameAssetCatalog
from .MatchLogService import MATCH_LOG_DIR, MatchLogService
from .PlayerDB import PlayerDB

STARTGG = "startgg"
MATCHLOG = "matchlog"
JSON = "json"
KINDS = (STARTGG, MATCHLOG, JSON)

# Where a set's characters came from, strongest first (the order they are
# taken in, and `char_source`).
SCOREBOARD = "scoreboard"
REPORT = "report"
MAINS = "mains"
SOURCES = (SCOREBOARD, REPORT, MAINS)
# The summary's other counts: a JSON set's own characters, none at all.
GIVEN = "given"
NONE = "none"

# The key vodsplit passes through with a set: the game and the codenames
# behind c1 / c2 (the page's icons).
NAMESPACE = "autona"
# How many characters a side's c1 / c2 names.
MAX_SHOWN = 2
# The days a start.gg event's records are looked for in, at most.
MAX_EVENT_DAYS = 8

DATE_PATTERN = re.compile(r"[0-9]{8}")
LOG_FILE_PATTERN = re.compile(r"([0-9]{8})\.jsonl")

# A JSON set's own characters: any of these makes the set the file's.
_GIVEN_KEYS = ("c1", "c2", "games", "char_source")

# Days' counts of a day file: {(folder, date): ((mtime_ns, size), counts or
# None)}, under its own lock (a leaf: held for no I/O).
_dayCache = {}
_dayCacheLock = threading.Lock()

BAD_KIND = "結果データの種類は startgg / matchlog / json のどれかにしてください。"
BAD_URL = ("start.gg のイベント URL を解釈できません。"
           "https://www.start.gg/tournament/<大会>/event/<イベント> の形式で指定してください。")
NO_EVENT = "start.gg にそのイベントが見つかりません。イベントの URL（/event/... まで）を確かめてください。"
NO_STARTGG_SETS = "このイベントには結果が報告されたセットがありません。大会の終了後にもう一度取得してください。"
STARTGG_FAILED = "start.gg から結果を取得できませんでした。時間をおいてもう一度試してください。"
BAD_DATE = "記録の日付は YYYYMMDD の形で指定してください。"
BAD_TOURNAMENT = "大会名は文字列か null にしてください。"
NO_RECORDS = "その日の記録はありません。"
NO_TOURNAMENT_RECORDS = "その日のその大会の記録はありません。"


# ── names ────────────────────────────────────────────────────────────────

def _Fold(text):
    """NFKC (full-width letters and digits as half-width), case folded,
    whitespace removed."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def NormalizeName(name):
    """A player's name as names are compared: the sponsor prefix dropped
    (start.gg's "TEAM | name", the part after the last " | ", as vodsplit
    does; a full-width ｜ counts, after NFKC), then NFKC, case folded and
    every whitespace removed. Accents and kana are kept ("ガ" is not
    "カ"). "" for what is not a string."""
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKC", name)
    return _Fold(text.split(" | ")[-1])


# ── the match log ────────────────────────────────────────────────────────

def _LogDirectory(directory):
    if directory:
        return directory
    service = MatchLogService.instance
    return service.directory if service is not None \
        else os.path.abspath(MATCH_LOG_DIR)


def _Text(value):
    return value.strip() if isinstance(value, str) else ""


def _Score(value):
    """A record's score as (a, b), or None when it is not two whole numbers
    >= 0."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
               for v in value):
        return None
    return int(value[0]), int(value[1])


def _Usable(record):
    """The record's normalised form, or None when it is not used (see the
    module docstring)."""
    p1, p2 = _Text(record.get("p1")), _Text(record.get("p2"))
    score = _Score(record.get("score"))
    if not p1 or not p2 or score is None or score == (0, 0):
        return None
    games = record.get("games")
    games = [game for game in games if isinstance(game, dict)] \
        if isinstance(games, list) else []
    setId = record.get("set_id")
    return {
        "id": record["id"],
        "tournament": _Text(record.get("tournament")),
        "set_id": None if setId is None or setId == "" else str(setId),
        "phase": _Text(record.get("phase")),
        "round": _Text(record.get("round")),
        "p1": p1, "p2": p2,
        # The game its characters are of; None for a record written before
        # records named it.
        "game": _Text(record.get("game")) or None,
        "c1": _Text(record.get("c1")), "c2": _Text(record.get("c2")),
        "score": score,
        "games": [(_Text(game.get("c1")), _Text(game.get("c2")))
                  for game in games],
    }


def ReadDay(date, directory=None):
    """The records of the match log's day `date` (``YYYYMMDD``): its file's
    records as the module docstring reads them, in the order of their first
    line. [] when there is no such file; an unreadable one is logged and
    [] too."""
    path = os.path.join(_LogDirectory(directory), f"{date}.jsonl")
    records = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as file:
            for line in file:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or not isinstance(
                        record.get("id"), str) or not record["id"]:
                    continue
                # Assigning keeps a key's first position: the last line's
                # content in the order the records began.
                records[record["id"]] = record
    except FileNotFoundError:
        return []
    except OSError as error:
        logger.warning(f"Archive: the match log {date}.jsonl could not be "
                       f"read ({type(error).__name__})")
        return []
    return [usable for usable in map(_Usable, records.values())
            if usable is not None]


def Days(directory=None):
    """The match log's days, newest first: [{"date": "YYYYMMDD", "records":
    n, "tournaments": [{"name", "records"}]}] -- the records Prepare would
    use, the tournaments in the order their first record began ("" for the
    records with no tournament name). A day with none is left out."""
    folder = _LogDirectory(directory)
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []
    except OSError as error:
        logger.warning(f"Archive: the match log folder could not be listed "
                       f"({type(error).__name__})")
        return []
    dates = sorted((match.group(1) for match in map(
        LOG_FILE_PATTERN.fullmatch, names) if match), reverse=True)
    days = []
    for date in dates:
        counts = _DayCounts(folder, date)
        if counts is None:
            continue
        days.append({
            "date": date,
            "records": sum(count for _name, count in counts),
            "tournaments": [{"name": name, "records": count}
                            for name, count in counts],
        })
    listed = set(dates)
    with _dayCacheLock:
        for key in [key for key in _dayCache
                    if key[0] == folder and key[1] not in listed]:
            del _dayCache[key]
    return days


def _DayCounts(folder, date):
    """[(tournament, records)] of a day file in the order their first
    record began, None when it has no record: the cached counts while the
    file's (mtime, size) is the same, else read again."""
    try:
        stat = os.stat(os.path.join(folder, f"{date}.jsonl"))
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    key = (folder, date)
    if stamp is not None:
        with _dayCacheLock:
            cached = _dayCache.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    records = ReadDay(date, folder)
    counts = {}
    for record in records:
        counts[record["tournament"]] = counts.get(
            record["tournament"], 0) + 1
    # A tuple of tuples: the cached value is shared, never changed.
    result = tuple(counts.items()) if records else None
    with _dayCacheLock:
        if stamp is None:
            _dayCache.pop(key, None)
        else:
            _dayCache[key] = (stamp, result)
    return result


def _CheckDate(date):
    if not isinstance(date, str) or not DATE_PATTERN.fullmatch(date):
        raise ArchiveError(INVALID, BAD_DATE)
    try:
        datetime.datetime.strptime(date, "%Y%m%d")
    except ValueError:
        raise ArchiveError(INVALID, BAD_DATE) from None
    return date


def _IsoDate(date):
    return f"{date[:4]}-{date[4:6]}-{date[6:]}"


# ── the game's characters ────────────────────────────────────────────────

class _Characters:
    """One game's characters as the results name them: codename -> display
    name, and the codename of an en_name (a main) or of a start.gg id."""

    def __init__(self, catalog, game):
        self.catalog = catalog
        self.game = game
        table = catalog.GameCharacters(game) if game else {}
        self.names = {}
        self.icons = {}
        self.byEnName = {}
        self.bySmashggName = {}
        for en_name, character in table.items():
            codename = character.get("codename")
            if not isinstance(codename, str) or not codename:
                continue
            self.names.setdefault(codename, character["display_name"])
            icon = character.get("icon")
            if isinstance(icon, str) and icon:
                self.icons.setdefault(codename, icon)
            self.byEnName.setdefault(en_name, codename)
            smashgg = character.get("smashgg_name")
            if isinstance(smashgg, str) and smashgg:
                self.bySmashggName.setdefault(smashgg, codename)

    def Known(self, codename):
        return isinstance(codename, str) and codename in self.names

    def FromStartGGId(self, smashgg_id):
        if smashgg_id is None or isinstance(smashgg_id, bool):
            return None
        name = self.catalog.StartGGCharacterName(smashgg_id)
        return self.bySmashggName.get(name) if name else None


def _LoadedGame(catalog):
    """The loaded game's codename, None when there is none."""
    return catalog.Current().codename or None


def _EventGame(catalog, videogame):
    """A start.gg event's game: its videogame's scanned game (None when the
    scan has none), the loaded game when the event names no videogame."""
    if videogame is None:
        return _LoadedGame(catalog)
    return catalog.GameFromStartGGId(videogame) or None


class _Mains:
    """The first main of a player, by normalised name, in the game: the
    player database's records by their gamerTag (the first record of a name
    wins)."""

    def __init__(self, database, characters):
        self.codenames = {}
        if database is None or not characters.game:
            return
        game = characters.game
        with database.lock:
            for _tag, player in database.Items():
                if not isinstance(player, dict):
                    continue
                key = NormalizeName(player.get("gamerTag"))
                if not key or key in self.codenames:
                    continue
                mains = PlayerDB.ParseMains(player.get("mains")).get(game)
                self.codenames[key] = self._First(mains, characters)

    @staticmethod
    def _First(mains, characters):
        """The codename of the first main (only the first: the list is what
        a player plays, not what they played here -- one guess, not two),
        or None."""
        if not isinstance(mains, list) or not mains:
            return None
        main = mains[0]
        en_name = main[0] if isinstance(main, (list, tuple)) and main \
            else main
        return characters.byEnName.get(en_name) \
            if isinstance(en_name, str) else None

    def Of(self, name):
        return self.codenames.get(NormalizeName(name))


# ── composing ────────────────────────────────────────────────────────────

def _Ordered(codenames):
    """Unique, in the order first seen, empty ones left out."""
    seen = []
    for codename in codenames:
        if codename and codename not in seen:
            seen.append(codename)
    return seen


def _RecordSides(record, swapped):
    """(games as (c1, c2) codenames, {side: [codenames]}) of a record,
    oriented to the set (`swapped`: the record's p1 is the set's p2)."""
    games = list(record["games"])
    current = (record["c1"], record["c2"])
    if swapped:
        games = [(c2, c1) for c1, c2 in games]
        current = current[::-1]
    sides = {}
    for side in (1, 2):
        played = _Ordered(game[side - 1] for game in games)
        sides[side] = played or _Ordered([current[side - 1]])
    return games, sides


def _ReportSides(games):
    sides = {}
    for side in (1, 2):
        sides[side] = _Ordered(game[side - 1] for game in games)
    return sides


def _Compose(item, characters, mains, record=None, swapped=False,
             report=None):
    """`item` (a result set) with its characters (the module docstring):
    from `record` (a match log record, `swapped` as matched), `report`
    (start.gg's games as (c1, c2) codenames, None when unknown) and
    `mains`. A record that names another game than `characters`' (or
    none) gives nothing. Returns the char_source counted (NONE when no
    character)."""
    if record is not None and (not record["game"]
                               or record["game"] != characters.game):
        record = None
    recordGames, recordSides = ([], {1: [], 2: []}) if record is None \
        else _RecordSides(record, swapped)
    reportGames = report or []
    reportSides = _ReportSides(reportGames)
    chosen = {}
    for side in (1, 2):
        name = item["p1"] if side == 1 else item["p2"]
        guess = mains.Of(name)
        for source, codenames in ((SCOREBOARD, recordSides[side]),
                                  (REPORT, reportSides[side]),
                                  (MAINS, [guess] if guess else [])):
            known = [c for c in codenames if characters.Known(c)]
            if known:
                chosen[side] = (source, known[:MAX_SHOWN])
                break
    if not chosen:
        return NONE

    def named(codename):
        return characters.names[codename] if characters.Known(codename) \
            else None

    for side, key in ((1, "c1"), (2, "c2")):
        if side in chosen:
            item[key] = "/".join(characters.names[c] for c in chosen[side][1])
    for games in (recordGames, reportGames):
        if any(characters.Known(c) for game in games for c in game):
            item["games"] = [{"c1": named(c1), "c2": named(c2)}
                             for c1, c2 in games]
            break
    source = max((source for source, _codenames in chosen.values()),
                 key=SOURCES.index)
    item["char_source"] = source
    shown = {side: list(chosen[side][1]) if side in chosen else []
             for side in (1, 2)}
    item[NAMESPACE] = {
        "game": characters.game,
        "c1": shown[1],
        "c2": shown[2],
        "icons": {codename: characters.icons[codename]
                  for codename in shown[1] + shown[2]
                  if codename in characters.icons},
    }
    return source


def _Pair(item):
    return NormalizeName(item["p1"]), NormalizeName(item["p2"])


def _Oriented(record, setPair):
    """Whether the record is on the set's sides swapped (None: another
    pair)."""
    recordPair = (NormalizeName(record["p1"]), NormalizeName(record["p2"]))
    if recordPair == setPair:
        return False
    if recordPair == setPair[::-1]:
        return True
    return None


def _Sides(record, setPair):
    """Whether a record matched by set id is on the set's sides swapped:
    by the pair, else by one name on its side, else not."""
    swapped = _Oriented(record, setPair)
    if swapped is not None:
        return swapped
    recordPair = (NormalizeName(record["p1"]), NormalizeName(record["p2"]))
    if recordPair[0] == setPair[1] or recordPair[1] == setPair[0]:
        return True
    return False


def _ScoreIs(record, swapped, item):
    return (record["score"][::-1] if swapped else record["score"]) \
        == (item["s1"], item["s2"])


def _MatchById(items, records):
    """{set index: (record, swapped)} by start.gg's set id, and the set
    ids the sets have (the module docstring)."""
    indexes = {}
    for index, item in enumerate(items):
        if item.get("id") is not None:
            indexes.setdefault(str(item["id"]), []).append(index)
    matched = {}
    for setId, found in indexes.items():
        candidates = [record for record in records
                      if record["set_id"] == setId]
        if len(found) != 1 or not candidates:
            continue
        index = found[0]
        pair = _Pair(items[index])
        sided = [(record, _Sides(record, pair)) for record in candidates]
        if len(sided) > 1:
            sided = [(record, swapped) for record, swapped in sided
                     if _ScoreIs(record, swapped, items[index])]
        if len(sided) == 1:
            matched[index] = sided[0]
    return matched, set(indexes)


def _MatchRecords(items, records):
    """{set index: (record, swapped)}: which record is which set (the
    module docstring)."""
    matched, setIds = _MatchById(items, records)
    # A record with one of the sets' ids is that set's or none's.
    records = [record for record in records if record["set_id"] not in setIds]
    groups = {}
    for index, item in enumerate(items):
        if index in matched:
            continue
        pair = _Pair(item)
        groups.setdefault(frozenset(pair), ([], []))[0].append(index)
    for record in records:
        pair = frozenset((NormalizeName(record["p1"]),
                          NormalizeName(record["p2"])))
        if pair in groups:
            groups[pair][1].append(record)
    for indexes, candidates in groups.values():
        if not candidates:
            continue
        oriented = {
            index: [(record, swapped) for record in candidates
                    for swapped in [_Oriented(record, _Pair(items[index]))]
                    if swapped is not None]
            for index in indexes}
        if len(indexes) == 1 and len(candidates) == 1:
            index = indexes[0]
            if oriented[index]:
                matched[index] = oriented[index][0]
            continue
        sameScore = {}
        for index in indexes:
            sameScore[index] = [
                (record, swapped) for record, swapped in oriented[index]
                if _ScoreIs(record, swapped, items[index])]
        for index in indexes:
            if len(sameScore[index]) != 1:
                continue
            record, swapped = sameScore[index][0]
            others = [other for other in indexes
                      if any(r is record for r, _s in sameScore[other])]
            if others == [index]:
                matched[index] = (record, swapped)
    return matched


def _Summary(counts, game, records):
    sets = sum(counts.values())
    return {"sets": sets, "game": game, "records": records,
            "sources": {key: counts.get(key, 0)
                        for key in SOURCES + (GIVEN, NONE)}}


# ── the three kinds ──────────────────────────────────────────────────────

def _FromStartGG(body, fetchEvent, catalog, database, directory):
    url = body.get("url")
    if not isinstance(url, str) or "start.gg/" not in url.strip():
        raise ArchiveError(INVALID, BAD_URL)
    if fetchEvent is None:
        raise ArchiveError(FETCH_FAILED, STARTGG_FAILED)
    try:
        event = fetchEvent(url.strip())
    except ValueError:
        raise ArchiveError(INVALID, BAD_URL) from None
    except LookupError:
        raise ArchiveError(NOT_FOUND, NO_EVENT) from None
    except Exception as error:
        logger.warning(f"Archive: start.gg's results could not be fetched "
                       f"({type(error).__name__}: {error})")
        raise ArchiveError(FETCH_FAILED, STARTGG_FAILED) from None
    fetched = [item for item in event.get("sets") or []
               if isinstance(item, dict)]
    if not fetched:
        raise ArchiveError(NOT_FOUND, NO_STARTGG_SETS)
    characters = _Characters(catalog,
                             _EventGame(catalog, event.get("videogame")))
    mains = _Mains(database, characters)
    items, reports = [], []
    for found in fetched:
        item = {"round": found.get("round") or "", "p1": found["p1"],
                "s1": found["s1"], "p2": found["p2"], "s2": found["s2"]}
        if found.get("id") is not None:
            item["id"] = found["id"]
        items.append(item)
        reports.append([(characters.FromStartGGId(game.get("c1")),
                         characters.FromStartGGId(game.get("c2")))
                        for game in found.get("games") or []
                        if isinstance(game, dict)])
    records = _EventRecords(event, items, directory)
    matched = _MatchRecords(items, records)
    counts = {}
    for index, item in enumerate(items):
        record, swapped = matched.get(index, (None, False))
        source = _Compose(item, characters, mains, record=record,
                          swapped=swapped, report=reports[index])
        counts[source] = counts.get(source, 0) + 1
    results = {"event": event.get("tournament") or event.get("event") or "",
               "sets": items}
    if event.get("url"):
        results["bracket_url"] = event["url"]
    day = _LocalDay(event.get("startAt"))
    if day is not None:
        results["date"] = day.isoformat()
    return results, _Summary(counts, characters.game, len(records))


def _LocalDay(timestamp):
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        return None
    try:
        return datetime.datetime.fromtimestamp(timestamp).date()
    except (OverflowError, OSError, ValueError):
        return None


def _EventRecords(event, items, directory):
    """The match log's records of a start.gg event (the module
    docstring)."""
    first = _LocalDay(event.get("startAt"))
    if first is None:
        return []
    last = _LocalDay(event.get("endAt")) or first
    last = min(max(last, first) + datetime.timedelta(days=1),
               first + datetime.timedelta(days=MAX_EVENT_DAYS - 1))
    tournament = _Fold(event.get("tournament") or "") \
        if isinstance(event.get("tournament"), str) else ""
    setIds = {str(item["id"]) for item in items if item.get("id") is not None}
    folder = _LogDirectory(directory)
    records = []
    day = first
    while day <= last:
        for record in ReadDay(day.strftime("%Y%m%d"), folder):
            if (record["set_id"] is not None and record["set_id"] in setIds) \
                    or (tournament and _Fold(record["tournament"])
                        == tournament):
                records.append(record)
        day += datetime.timedelta(days=1)
    return records


def _FromMatchLog(body, catalog, database, directory):
    date = _CheckDate(body.get("date"))
    tournament = body.get("tournament")
    if tournament is not None and not isinstance(tournament, str):
        raise ArchiveError(INVALID, BAD_TOURNAMENT)
    records = ReadDay(date, directory)
    if not records:
        raise ArchiveError(NOT_FOUND, NO_RECORDS)
    if tournament is not None:
        tournament = tournament.strip()
        records = [r for r in records if r["tournament"] == tournament]
        if not records:
            raise ArchiveError(NOT_FOUND, NO_TOURNAMENT_RECORDS)
    # Each record's characters are of its own game (none for a record that
    # names no game: not the loaded one's, nor the mains').
    byGame = {}
    items, counts = [], {}
    for record in records:
        if record["game"] not in byGame:
            characters = _Characters(catalog, record["game"])
            byGame[record["game"]] = (characters,
                                      _Mains(database, characters))
        characters, mains = byGame[record["game"]]
        item = {"round": record["round"] or record["phase"],
                "p1": record["p1"], "s1": record["score"][0],
                "p2": record["p2"], "s2": record["score"][1],
                "id": record["id"]}
        source = _Compose(item, characters, mains, record=record)
        counts[source] = counts.get(source, 0) + 1
        items.append(item)
    names = {record["tournament"] for record in records}
    results = {"event": tournament if tournament is not None
               else (names.pop() if len(names) == 1 else ""),
               "date": _IsoDate(date), "sets": items}
    # The summary's game: the records', when they name one and the same.
    games = set(byGame)
    return results, _Summary(counts, games.pop() if len(games) == 1 else None,
                             len(records))


def _FromJson(body, catalog, database):
    given = CheckResults(body.get("results"))
    if given is None:
        raise ArchiveError(INVALID, (
            "結果データの形式が違います（\"sets\" の配列がありません）。"))
    characters = _Characters(catalog, _LoadedGame(catalog))
    mains = _Mains(database, characters)
    items, counts = [], {}
    for found in given["sets"]:
        item = dict(found)
        # Only Prepare writes the namespace: a file's is not taken.
        item.pop(NAMESPACE, None)
        if any(item.get(key) is not None for key in _GIVEN_KEYS):
            source = GIVEN
        else:
            source = _Compose(item, characters, mains)
        counts[source] = counts.get(source, 0) + 1
        items.append(item)
    results = dict(given)
    results["sets"] = items
    return results, _Summary(counts, characters.game, 0)


def Prepare(body, fetchEvent=None, directory=None, catalog=None,
            database=None):
    """The results of `body` (``{"kind": "startgg", "url"}`` /
    ``{"kind": "matchlog", "date": "YYYYMMDD", "tournament": name or
    null}`` / ``{"kind": "json", "results"}``) with their characters:
    ``{"results": vodsplit's results, "summary": {"sets", "game",
    "records" (the match log's records looked at), "sources": {scoreboard,
    report, mains, given, none: the sets by where their characters came
    from}}}``. `fetchEvent(url)` fetches a start.gg event's completed sets
    (StartGGDataProvider.FetchEventResults' shape; ValueError: not an event
    URL, LookupError: no such event, anything else: start.gg did not
    answer). Raises ArchiveError (INVALID / NOT_FOUND / FETCH_FAILED)."""
    if not isinstance(body, dict) or body.get("kind") not in KINDS:
        raise ArchiveError(INVALID, BAD_KIND)
    catalog = catalog or GameAssetCatalog.instance
    database = database if database is not None else PlayerDB.instance
    kind = body["kind"]
    if kind == STARTGG:
        results, summary = _FromStartGG(body, fetchEvent, catalog, database,
                                        directory)
    elif kind == MATCHLOG:
        results, summary = _FromMatchLog(body, catalog, database, directory)
    else:
        results, summary = _FromJson(body, catalog, database)
    # What the job's Create checks, checked here: a result prepared is a
    # result the job takes.
    CheckResults(results)
    return {"results": results, "summary": summary}
