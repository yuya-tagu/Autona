"""The Hivee tournament provider: a Hivee event read through its stream
overlay's JSON, read only (core/HiveeUrl.py has the URL and its token).

What the overlay hands out is what is read: the match on stream
(``current``: names, scores, seeds, X handles and avatars, the grand final's
losers flags, the characters of the set, the round's label, the station,
the program and its pool), the matches queued for the stream (``queue``:
the same without the X handles and the avatars -- and the seeds but a
bracket's) and the Top 8 of a double elimination program (``view=top8``).
Nothing is written back to Hivee. A seat not decided yet ("未定") and a
name Hivee hides ("（不明）") are no name.

How it fits what the app asks a provider for:

- the stream follow (core/SetFollowService.py): the event is one stream.
  ``GetStations`` answers one stream row and ``GetStreamMatchId`` the match
  on stream (else the first one queued) whatever the stream is called -- so
  the station dialog's row and the web's pull of the streamer's channel both
  follow it -- and ``GetMatch`` its set, in StartGGDataProvider's set shape
  (Hivee's match id, a uuid string, is the set id). The stream queue is
  ``streamQueue.hivee`` (a fixed key: the event's title is no state key).
- what is remembered of a match across reads, by its id (``_Memory``; per
  event, outside the provider, so a forced reload -- which builds the
  provider again -- keeps it; the newest ``_KEPT`` matches):
  - its sides: the overlay's own swap (Hivee's display-only "左右入れ替え")
    is undone within a match -- the sides a match was first read with stay
    its sides, or a swap in Hivee mid-set would put one player's score
    under the other's name on the next update. A side read with no name is
    filled in once its name comes.
  - its last content: a match that has left the stream and the queue (it
    ended) answers what was read of it last, so a board following it gets
    the score it ended on rather than nothing.
  - each player's characters and their seed, X handle and avatar, as last
    handed out: an update (the follow's tick: SetFollowService
    ._PlayerUpdate) writes a player's characters only when they changed
    since (``selection_changed``; writing them again would put back the
    skin the operator picked), and the seed, X handle and avatar when they
    were filled in since (``filled_in``: a queued match loaded first gets
    them once it is on stream). A value the overlay stops handing out (the
    match back in the queue) is kept. "Since the last read" is the
    provider's, not a board's: two boards following the same match, or a
    v1 GET /sets/{id} in between, see a change once between them.
- the game: Hivee is Super Smash Bros. Ultimate only, so the tournament's
  videogame is start.gg's SSBU id (``SSBU_STARTGG_ID``) and the adapter
  selects it the way it does a start.gg tournament's. The characters are
  Hivee's SSBU codes, mapped to the asset config's character keys
  (``HIVEE_TO_SSBU``; tests/test_hivee_provider.py keeps it whole), under
  the loaded game's codename when it is an SSBU game (a mod of it with its
  own codename included), else the first scanned SSBU game's.
- the Top 8 (core/Top8Service.py): ``GetTop8`` answers the overlay's eleven
  matches in Top8Model.FromBracket's shapes.
- what the overlay does not carry -- entrants, phases and their sets,
  standings, players' history -- answers empty: no phases, no sets, no
  standings, the whole player DB as the name candidates. The head-to-head
  and the placement history are start.gg's only (AutonaStatsUtil
  .IsStartGG); the last sets, asked of any provider, answer none here
  without a request (``GetLastSets``).

The token is a secret: no URL is logged, and a failed request is logged by
its kind alone (a requests exception's text carries the URL). The state and
the READ APIs show the URL masked (TournamentService.PublishedUrl).

Threads: the reads come from jobs of the task executor (the follow, the
Top 8, the tournament's data, the media), several at once, and ``GetMatch``
also from a web request's thread (the legacy /get-match, Socket.IO's
get_match, v1's GET /sets/{id}); the constructor and ``videogame`` are the
adapter's, on the main thread. Nothing here needs the GUI thread or holds a
lock while asking Hivee. The overlay is read once per view and kept for
``CACHE_SECS`` (a read that failed for ``FAILURE_SECS``, so a Hivee that is
down does not hold a job for every read), and reads that ask while a read
of the same view is waiting for Hivee wait for its answer rather than ask
again: the follow's 5-second tick -- the set, the stream queue, the
stream's current set, each board -- makes one request. Import side effects:
none (pitfalls #1).
"""
import re
import threading
import time
from collections import OrderedDict

import orjson
import requests
from loguru import logger

from ..AutonaPlayerDB import AutonaPlayerDB
from ..core import HiveeUrl
from ..core import Top8Model as top8
from ..core.GameAssetCatalog import GameAssetCatalog
from ..core.TaskExecutor import TaskExecutor
from ..Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .StartGGDataProvider import StartGGDataProvider
from .TournamentDataProvider import TournamentDataProvider

# start.gg's videogame id of Super Smash Bros. Ultimate: what the asset
# config's smashgg_game_id names the game by (GameAssetCatalog
# .GameFromStartGGId).
SSBU_STARTGG_ID = 1386
# The game's codename when no scanned game has that id.
SSBU_CODENAME = "ssbu"

SCOREBOARD = "scoreboard"
TOP8 = "top8"

# Hivee's SSBU character codes (Hivee's src/lib/ssbu/characters.ts) -> the
# character's key in the asset config (character_to_codename: its en_name).
# Hivee's nameEn is the config's key for every fighter; the config's
# "Random" has no Hivee code.
HIVEE_TO_SSBU = {
    "mario": "Mario",
    "donkey_kong": "Donkey Kong",
    "link": "Link",
    "samus": "Samus",
    "dark_samus": "Dark Samus",
    "yoshi": "Yoshi",
    "kirby": "Kirby",
    "fox": "Fox",
    "pikachu": "Pikachu",
    "luigi": "Luigi",
    "ness": "Ness",
    "captain_falcon": "Captain Falcon",
    "jigglypuff": "Jigglypuff",
    "peach": "Peach",
    "daisy": "Daisy",
    "bowser": "Bowser",
    "ice_climbers": "Ice Climbers",
    "sheik": "Sheik",
    "zelda": "Zelda",
    "dr_mario": "Dr. Mario",
    "pichu": "Pichu",
    "falco": "Falco",
    "marth": "Marth",
    "lucina": "Lucina",
    "young_link": "Young Link",
    "ganondorf": "Ganondorf",
    "mewtwo": "Mewtwo",
    "roy": "Roy",
    "chrom": "Chrom",
    "mr_game_and_watch": "Mr. Game & Watch",
    "meta_knight": "Meta Knight",
    "pit": "Pit",
    "dark_pit": "Dark Pit",
    "zero_suit_samus": "Zero Suit Samus",
    "wario": "Wario",
    "snake": "Snake",
    "ike": "Ike",
    "pokemon_trainer": "Pokémon Trainer",
    "diddy_kong": "Diddy Kong",
    "lucas": "Lucas",
    "sonic": "Sonic",
    "king_dedede": "King Dedede",
    "olimar": "Olimar",
    "lucario": "Lucario",
    "rob": "R.O.B.",
    "toon_link": "Toon Link",
    "wolf": "Wolf",
    "villager": "Villager",
    "mega_man": "Mega Man",
    "wii_fit_trainer": "Wii Fit Trainer",
    "rosalina_and_luma": "Rosalina & Luma",
    "little_mac": "Little Mac",
    "greninja": "Greninja",
    "mii_brawler": "Mii Brawler",
    "mii_swordfighter": "Mii Swordfighter",
    "mii_gunner": "Mii Gunner",
    "palutena": "Palutena",
    "pac_man": "Pac-Man",
    "robin": "Robin",
    "shulk": "Shulk",
    "bowser_jr": "Bowser Jr.",
    "duck_hunt": "Duck Hunt",
    "ryu": "Ryu",
    "ken": "Ken",
    "cloud": "Cloud",
    "corrin": "Corrin",
    "bayonetta": "Bayonetta",
    "inkling": "Inkling",
    "ridley": "Ridley",
    "simon": "Simon",
    "richter": "Richter",
    "king_k_rool": "King K. Rool",
    "isabelle": "Isabelle",
    "incineroar": "Incineroar",
    "piranha_plant": "Piranha Plant",
    "joker": "Joker",
    "hero": "Hero",
    "banjo_and_kazooie": "Banjo & Kazooie",
    "terry": "Terry",
    "byleth": "Byleth",
    "min_min": "Min Min",
    "steve": "Steve",
    "sephiroth": "Sephiroth",
    "pyra_mythra": "Pyra / Mythra",
    "kazuya": "Kazuya",
    "sora": "Sora",
}

# Hivee names a double elimination's rounds as start.gg does, but without
# the hyphen ("Winners Semi Final"); the round names are translated from
# start.gg's spelling (StartGGDataProvider.TranslateRoundName).
_UNHYPHENATED = re.compile(r" (Semi|Quarter) Final$")
# Hivee's single elimination round labels (決勝 / 準決勝 / 準々決勝 / R{n})
# and its round robin's.
_SINGLE_ELIMINATION = re.compile(r"^(決勝|準決勝|準々決勝|R\d+)$")
_ROUND_ROBIN_LABEL = "総当たり（リーグ戦）"

# What the overlay puts for a seat not decided yet and for a name it hides
# (Hivee's src/lib/overlay/payload.ts nameOf): no name.
_NO_NAMES = ("未定", "（不明）")

# The stream queue's one stream (streamQueue.<this>).
STREAM_NAME = "hivee"

# The matches remembered per event (the newest ones), and the events.
_KEPT = 32
_EVENTS_KEPT = 4

# The character codes the table does not know, warned about once each.
_unknownCodes = set()
_unknownLock = threading.Lock()


def Characters(codes):
    """Hivee's character codes as the asset config's keys, in order; codes
    the table does not know are left out (and logged, once a code)."""
    if not isinstance(codes, list):
        return []
    names = []
    for code in codes:
        name = HIVEE_TO_SSBU.get(code) if isinstance(code, str) else None
        if name is None:
            key = code if isinstance(code, str) else repr(code)
            with _unknownLock:
                first = key not in _unknownCodes
                _unknownCodes.add(key)
            if first:
                logger.warning(f"Hivee: unknown character code {code!r}")
            continue
        names.append(name)
    return names


def Mains(codes, codename):
    """A player's `mains` (PlayerService.ApplyData's dict form) for the
    characters `codes`: {codename: [[key, skin 0], ...]}; {} when none."""
    names = Characters(codes)
    if not names:
        return {}
    return {codename: [[name, 0] for name in names]}


def RoundName(label):
    """The board's round name for a Hivee round label: translated where it
    is start.gg's (the double elimination's), the label itself otherwise."""
    if not isinstance(label, str) or not label:
        return ""
    if not AutonaLocaleHelper.matchNames:
        # The round names are not loaded (a test, a shell that did not
        # load them): nothing to translate with.
        return label
    translated = StartGGDataProvider.TranslateRoundName(
        _UNHYPHENATED.sub(r" \1-Final", label))
    return translated or label


def RoundNameEn(label):
    """The English round text (match_en): the double elimination's label in
    start.gg's spelling; "" for one that is not English."""
    if not isinstance(label, str) or not label.isascii():
        return ""
    return _UNHYPHENATED.sub(r" \1-Final", label)


def BracketType(match):
    """start.gg's bracketType a Hivee match stands for, as far as its
    bracket and round label tell; "" when they do not."""
    bracket = match.get("bracket")
    label = match.get("roundLabel") or ""
    if bracket in ("losers", "grand_final") or label.startswith(
            ("Winners ", "Losers ", "Grand Final")):
        return "DOUBLE_ELIMINATION"
    if _SINGLE_ELIMINATION.match(label):
        return "SINGLE_ELIMINATION"
    if label == _ROUND_ROBIN_LABEL:
        return "ROUND_ROBIN"
    return ""


def PoolName(match):
    """The pool's name (tournament_pool) of a match in a program split in
    pools; "" otherwise."""
    number = match.get("poolNumber")
    if number is None:
        return ""
    template = AutonaLocaleHelper.phaseNames.get("group") \
        if isinstance(AutonaLocaleHelper.phaseNames, dict) else None
    try:
        return (template or "Pool {0}").format(number)
    except (IndexError, KeyError, ValueError):
        return f"Pool {number}"


def _Player(side):
    return side if isinstance(side, dict) else {}


def _Name(side):
    """A seat's name; "" when it has none (undecided, hidden)."""
    name = _Player(side).get("name")
    if not isinstance(name, str) or name in _NO_NAMES:
        return ""
    return name


def _Codes(side):
    """A seat's character codes as a tuple (what is compared across reads)."""
    codes = _Player(side).get("characters")
    return tuple(codes) if isinstance(codes, list) else ()


def _IsSsbu(game):
    """Whether a game config is SSBU: start.gg's SSBU id is its own or an
    alternate version's (GameAssetCatalog.GameFromStartGGId's test)."""
    if not isinstance(game, dict):
        return False
    ids = [game.get("smashgg_game_id")] + [
        alternate.get("smashgg_game_id")
        for alternate in game.get("alternate_versions") or []
        if isinstance(alternate, dict)]
    return str(SSBU_STARTGG_ID) in {str(i) for i in ids if i is not None}


class _Memory:
    """What is remembered of one event's matches (see the module
    docstring): {match id: record}, the newest _KEPT. A record's keys:
    "sides" (the two names, as first read and filled in), "match" (the last
    content read) and "players" ({name: what was handed out of them:
    "codes", and "seed" / "twitter" / "avatar" once known})."""

    def __init__(self):
        self.lock = threading.Lock()
        self.matches = OrderedDict()

    def Record(self, matchId):
        """Match `matchId`'s record, made when there is none. The caller
        holds the lock."""
        key = str(matchId).lower()
        record = self.matches.get(key)
        if record is None:
            record = self.matches[key] = {}
            while len(self.matches) > _KEPT:
                self.matches.popitem(last=False)
        else:
            self.matches.move_to_end(key)
        return record

    def Find(self, matchId):
        """Match `matchId`'s record, or None. The caller holds the lock."""
        return self.matches.get(str(matchId).lower())


class _Flight:
    """One read of a view asking Hivee: the reads that come meanwhile wait
    for its answer."""

    def __init__(self):
        self.done = threading.Event()
        self.payload = None


class HiveeDataProvider(TournamentDataProvider):
    _request_timeout_secs = 10.0
    # How long an overlay read answers the reads after it, and a read that
    # failed (nothing is asked of Hivee meanwhile).
    CACHE_SECS = 2.0
    FAILURE_SECS = 5.0

    # {(origin, event id): _Memory}: outside the instances, which a forced
    # reload replaces (see the module docstring).
    _memories = OrderedDict()
    _memoriesLock = threading.Lock()

    def __init__(self, url, tshTdp) -> None:
        super().__init__(url, tshTdp)
        self.name = "Hivee"
        self.target = HiveeUrl.Parse(url)
        # {view: (monotonic time, payload or None)}, and the reads asking
        # Hivee now ({view: _Flight}); the lock guards these two only.
        self._cache = {}
        self._flights = {}
        self._lock = threading.Lock()
        self._memory = self._MemoryOf(self.target)

    @classmethod
    def _MemoryOf(cls, target):
        if target is None:
            return _Memory()
        key = (target.origin, target.eventId)
        with cls._memoriesLock:
            memory = cls._memories.get(key)
            if memory is None:
                memory = cls._memories[key] = _Memory()
                while len(cls._memories) > _EVENTS_KEPT:
                    cls._memories.popitem(last=False)
            else:
                cls._memories.move_to_end(key)
            return memory

    @classmethod
    def ForgetMemories(cls):
        """Drop what is remembered of every event's matches (tests)."""
        with cls._memoriesLock:
            cls._memories.clear()

    # ── the overlay ──────────────────────────────────────────────────────

    def _Overlay(self, view):
        """The overlay's payload for `view` (a dict), or None when it could
        not be read. Logged without the URL. Hivee is asked by one read at
        a time per view, with no lock held: a read that comes meanwhile
        waits for that read's answer."""
        if self.target is None:
            logger.error("Hivee: the tournament URL is not a Hivee overlay "
                         "URL")
            return None
        with self._lock:
            cached = self._cache.get(view)
            if cached is not None:
                at, payload = cached
                kept = self.CACHE_SECS if payload is not None \
                    else self.FAILURE_SECS
                if time.monotonic() - at < kept:
                    return payload
            flight = self._flights.get(view)
            leading = flight is None
            if leading:
                flight = self._flights[view] = _Flight()
        if not leading:
            # A connect and a read can each take the whole timeout.
            if not flight.done.wait(2 * self._request_timeout_secs + 1):
                logger.warning(f"Hivee: the overlay ({view}) read under way "
                               "did not answer in time")
                return None
            return flight.payload
        payload = None
        try:
            payload = self._Request(view)
            if payload is not None and view == SCOREBOARD:
                self._Remember(payload)
        finally:
            with self._lock:
                self._cache[view] = (time.monotonic(), payload)
                self._flights.pop(view, None)
            flight.payload = payload
            flight.done.set()
        return payload

    def _Request(self, view):
        executor = TaskExecutor.instance
        if executor is not None and executor.stopping.is_set():
            return None
        params = {"token": self.target.token, "view": view}
        if view == TOP8 and self.target.program:
            params["program"] = self.target.program
        try:
            response = requests.get(
                f"{self.target.origin}/api/events/{self.target.eventId}"
                "/overlay",
                params=params, timeout=self._request_timeout_secs)
        except Exception as e:
            # Not the exception's text: it carries the URL and its token.
            logger.warning(f"Hivee: the overlay ({view}) could not be read "
                           f"({type(e).__name__})")
            return None
        if response.status_code == 404:
            logger.warning(
                f"Hivee: the overlay ({view}) answered 404 -- no such event, "
                "or the token is wrong or was reissued")
            return None
        if response.status_code != 200:
            logger.warning(f"Hivee: the overlay ({view}) answered "
                           f"{response.status_code}")
            return None
        try:
            payload = orjson.loads(response.content)
        except Exception as e:
            logger.warning(f"Hivee: the overlay ({view}) did not answer "
                           f"JSON ({type(e).__name__})")
            return None
        if not isinstance(payload, dict):
            logger.warning(f"Hivee: the overlay ({view}) did not answer an "
                           "object")
            return None
        return payload

    def _Remember(self, payload):
        """Each match of a scoreboard read as its last content."""
        with self._memory.lock:
            for match in self._Matches(payload):
                self._memory.Record(match.get("matchId"))["match"] = match

    @staticmethod
    def _Matches(payload):
        """The match on stream, then the ones queued, as they come."""
        matches = []
        current = payload.get("current")
        if isinstance(current, dict) and current.get("matchId"):
            matches.append(current)
        queue = payload.get("queue")
        if isinstance(queue, list):
            matches += [m for m in queue
                        if isinstance(m, dict) and m.get("matchId")]
        return matches

    def _StreamName(self, payload=None):
        """What the event's one stream is called on the station row: the
        event's title."""
        event = (payload or {}).get("event")
        title = event.get("title") if isinstance(event, dict) else None
        return title if isinstance(title, str) and title else "Hivee"

    @staticmethod
    def _Codename():
        """The codename the characters go under: the loaded game's when it
        is SSBU, else the first scanned SSBU game's, else "ssbu"."""
        catalog = GameAssetCatalog.instance
        if catalog is None:
            return SSBU_CODENAME
        snapshot = catalog.Current()
        if snapshot.codename and _IsSsbu(snapshot.game):
            return snapshot.codename
        return catalog.GameFromStartGGId(SSBU_STARTGG_ID) or SSBU_CODENAME

    @staticmethod
    def _Orient(record, match):
        """(player a, player b) of `match` on the sides it was first read
        with: a match whose names come back the other way round (Hivee's
        swap turned on or off since) is swapped back. A side first read
        with no name takes its name once it comes. The memory's lock is
        held."""
        a, b = _Player(match.get("playerA")), _Player(match.get("playerB"))
        names = (_Name(a), _Name(b))
        first = record.get("sides")
        if first is None:
            record["sides"] = names
            return a, b
        # A name known on one side that is now on the other.
        swapped = any(
            first[i] and first[i] == names[1 - i] and first[i] != names[i]
            for i in (0, 1))
        if swapped:
            a, b = b, a
            names = (names[1], names[0])
        record["sides"] = tuple(known or name
                                for known, name in zip(first, names))
        return a, b

    @staticmethod
    def _Handed(record, side):
        """What a seat's player is handed out as -- their seed, X handle and
        avatar, the ones known since when the overlay no longer gives them
        -- and what changed since the last read: whether their characters
        did, and the fields filled in. The memory's lock is held."""
        name = _Name(side)
        codes = _Codes(side)
        given = {"seed": side.get("seed"),
                 "twitter": side.get("xHandle") or "",
                 "avatar": side.get("avatarUrl") or ""}
        if not name:
            # No one to remember it of.
            return given, bool(codes), {}
        players = record.setdefault("players", {})
        known = players.get(name)
        changed = bool(codes) if known is None else codes != known["codes"]
        if known is None:
            known = players[name] = {}
        known["codes"] = codes
        filled = {}
        for key, value in given.items():
            if value in (None, ""):
                continue
            if known.get(key) != value:
                filled[key] = value
                known[key] = value
        handed = {key: known.get(key, value) for key, value in given.items()}
        return handed, changed, filled

    # ── the tournament ───────────────────────────────────────────────────

    def GetTournamentData(self, progress_callback=None, cancel_event=None):
        payload = self._Overlay(SCOREBOARD)
        if payload is None:
            return {}
        event = payload.get("event") if isinstance(
            payload.get("event"), dict) else {}
        self.videogame = SSBU_STARTGG_ID
        return {
            "_videogame": SSBU_STARTGG_ID,
            "tournamentName": event.get("title") or "",
        }

    def GetIconURL(self, strict=False):
        """The organising group's icon (the overlay's event.logoUrl); None
        when it has none. A read that failed: None, or LookupError when
        `strict` (TournamentMediaService tells the two apart)."""
        payload = self._Overlay(SCOREBOARD)
        if payload is None:
            if strict:
                raise LookupError("Hivee: the overlay could not be read") \
                    from None
            return None
        event = payload.get("event")
        logo = event.get("logoUrl") if isinstance(event, dict) else None
        return logo if isinstance(logo, str) and logo else None

    def GetBannerURL(self, strict=False):
        # Hivee has no banner.
        return None

    def GetEntrants(self, cancel_event=None):
        # The overlay lists no entrants: the whole player DB is the name
        # candidates (the tournament filter a start.gg tournament loaded
        # before set is dropped). Queued to the GUI thread, ahead of any
        # later tournament's.
        if cancel_event is not None and cancel_event.is_set():
            return
        AutonaPlayerDB.ClearTournamentPlayers()

    # ── the stream follow ────────────────────────────────────────────────

    def GetStations(self, progress_callback=None, cancel_event=None,
                    strict=False):
        """The event's one stream, as a stream row (the station dialog's).
        A read that failed: [] (LookupError when `strict`)."""
        payload = self._Overlay(SCOREBOARD)
        if payload is None:
            if strict:
                raise LookupError("Hivee: the overlay could not be read") \
                    from None
            return []
        name = self._StreamName(payload)
        return [{"type": "stream", "id": None, "identifier": name,
                 "stream": name}]

    def GetStreamMatchId(self, streamName):
        """The match on stream -- else the first one queued -- as a set node
        ({"id"}), whatever `streamName` is: the event is one stream. None
        when there is none (or the overlay could not be read)."""
        payload = self._Overlay(SCOREBOARD)
        if payload is None:
            return None
        matches = self._Matches(payload)
        if not matches:
            return None
        return {"id": matches[0].get("matchId")}

    def GetStationMatchsId(self, stationId):
        # No stations: the event is one stream.
        return []

    def GetMatch(self, setId, progress_callback=None, cancel_event=None):
        """The set of match `setId` -- on stream or queued, else as it was
        last read (a match that ended has left both) -- in
        StartGGDataProvider.GetMatch's shape; {} when the overlay could not
        be read or the match was never read."""
        payload = self._Overlay(SCOREBOARD)
        if payload is None:
            return {}
        wanted = str(setId).lower()
        for match in self._Matches(payload):
            if str(match.get("matchId")).lower() == wanted:
                return self.SetData(match)
        with self._memory.lock:
            record = self._memory.Find(setId)
            last = record.get("match") if record is not None else None
        if last is None:
            logger.info(f"Hivee: match {setId} is neither on stream nor "
                        "queued")
            return {}
        logger.debug(f"Hivee: match {setId} has left the stream; its last "
                     "read answers")
        return self.SetData(last)

    def SetData(self, match):
        """A Hivee match as a set (StartGGDataProvider.GetMatch's keys that
        the board reads), each player with what changed since the last read
        (see the module docstring)."""
        codename = self._Codename()
        label = match.get("roundLabel") or ""
        with self._memory.lock:
            record = self._memory.Record(match.get("matchId"))
            a, b = self._Orient(record, match)
            handed = [self._Handed(record, side) for side in (a, b)]

        def entrant(side, read):
            given, changed, filled = read
            player = {
                "gamerTag": _Name(side),
                "prefix": "",
                "twitter": given["twitter"],
                "avatar": given["avatar"],
                "seed": given["seed"],
                # What an update writes (SetFollowService._PlayerUpdate).
                "selection_changed": changed,
            }
            mains = Mains(side.get("characters"), codename)
            if mains:
                player["mains"] = mains
            if filled:
                player["filled_in"] = filled
            return player

        entrants = [[entrant(a, handed[0])], [entrant(b, handed[1])]]
        return {
            "id": match.get("matchId"),
            "team1score": a.get("score"),
            "team2score": b.get("score"),
            "round_name": RoundName(label),
            "round_name_en": RoundNameEn(label),
            "tournament_phase": match.get("programName") or "",
            "tournament_pool": PoolName(match),
            "bracket_type": BracketType(match),
            "p1_name": _Name(a),
            "p2_name": _Name(b),
            "p1_seed": handed[0][0]["seed"],
            "p2_seed": handed[1][0]["seed"],
            "stream": "",
            "station": match.get("stationName") or "",
            "isOnline": False,
            "isPools": match.get("poolNumber") is not None,
            "round": match.get("round"),
            "team1losers": bool(a.get("losers")),
            "team2losers": bool(b.get("losers")),
            "bestOf": None,
            "entrants": entrants,
            # An update writes characters only when a player's changed.
            "has_selection_data": handed[0][1] or handed[1][1],
        }

    def GetStreamQueue(self, streamName=None, progress_callback=None,
                       cancel_event=None):
        """The match on stream and the ones queued, as the stream queue
        (StartGGDataProvider.GetStreamQueue's shape) of STREAM_NAME; {}
        when the overlay could not be read."""
        payload = self._Overlay(SCOREBOARD)
        if payload is None:
            return {}
        queue = {}
        for index, match in enumerate(self._Matches(payload)):
            queue[str(index + 1)] = self._FutureSet(match)
        return {STREAM_NAME: queue}

    @staticmethod
    def _FutureSet(match):
        """A queued match as StartGGDataProvider.ProcessFutureSet's set."""
        teams = {}
        for index, key in enumerate(("playerA", "playerB")):
            side = _Player(match.get(key))
            name = _Name(side)
            teams[str(index + 1)] = {
                "teamName": "",
                "losers": bool(side.get("losers")),
                "seed": side.get("seed"),
                "player": {"1": {
                    "country": {},
                    "state": {},
                    "name": name,
                    "team": "",
                    "mergedName": name,
                    "real_name": "",
                    "online_avatar": side.get("avatarUrl") or "",
                    "twitter": side.get("xHandle") or "",
                }},
            }
        return {
            "id": match.get("matchId"),
            "match": RoundName(match.get("roundLabel") or ""),
            "phase": match.get("programName") or "",
            "pool": PoolName(match),
            "best_of": 0,
            "best_of_text": "",
            "state": None,
            "team": teams,
            "station": match.get("stationName") or "",
            "event": "",
            "isCurrentEvent": True,
        }

    # ── the Top 8 ────────────────────────────────────────────────────────

    def GetTop8(self):
        """The overlay's Top 8 as Top8Model.FromBracket answers one --
        (players, matches) -- or None when there is none to import (the
        overlay could not be read, or the program is not a double
        elimination Top 8)."""
        payload = self._Overlay(TOP8)
        if payload is None:
            return None
        data = payload.get("top8")
        if not isinstance(data, dict):
            logger.info("Hivee: the overlay has no Top 8")
            return None
        if data.get("status") != "ok":
            logger.info(f"Hivee: no Top 8 ({data.get('status')})")
            return None
        return Top8FromCards(data.get("cards"), self._Codename())

    # ── what the overlay does not carry ──────────────────────────────────

    def GetLastSets(self, playerID=None, playerNumber=None, callback=None,
                    progress_callback=None, cancel_event=None):
        """No last sets (the overlay has no player history): the answer is
        none, with no request, so the board's are emptied as for a start.gg
        player with none."""
        if callback is not None:
            callback.emit({"playerNumber": playerNumber, "last_sets": []})

    def GetMatches(self, getFinished=False, phaseGroupIds=None,
                   progress_callback=None, cancel_event=None, strict=False,
                   failed_pages=None):
        return []

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None,
                            strict=False):
        return []

    def GetTournamentPhaseGroup(self, id, progress_callback=None,
                                cancel_event=None):
        return {}

    def GetStandings(self, playerNumber, progress_callback=None,
                     cancel_event=None):
        return []


# ── the Top 8 ────────────────────────────────────────────────────────────

def _Card(cards, matchId):
    card = cards.get(matchId)
    return card if isinstance(card, dict) else None


def _Seats(card):
    """The card's two seats: {"name", "characters", ...} or None each."""
    players = (card or {}).get("players")
    if not isinstance(players, list) or len(players) != 2:
        return [None, None]
    return [p if isinstance(p, dict) and _Name(p) else None for p in players]


def _Loser(card):
    """The name of the card's loser, or None while it is undecided."""
    winner = (card or {}).get("winner")
    if winner not in (0, 1):
        return None
    seat = _Seats(card)[1 - winner]
    return _Name(seat) if seat else None


def _Holds(card, name):
    return name is not None and any(
        seat is not None and _Name(seat) == name for seat in _Seats(card))


def _Aligned(cards):
    """Hivee's eleven cards on Top8Model's matches. Hivee orders its losers
    quarter finals by the losers semi final's seats and feeds lr1a -> lqf1,
    lr1b -> lqf2; Top8Model's fixed cross sends WSF1's loser to lqf2 and
    WSF2's to lqf1. The overlay does not say where a WSF loser goes: it is
    found by name, once a WSF is decided (Hivee's order otherwise)."""
    aligned = {mid: _Card(cards, mid) for mid in top8.MATCH_IDS}
    wsf1, wsf2 = _Loser(aligned["wsf1"]), _Loser(aligned["wsf2"])
    lqf1, lqf2 = aligned["lqf1"], aligned["lqf2"]
    swap = _Holds(lqf1, wsf1) or _Holds(lqf2, wsf2)
    if not swap and not (_Holds(lqf2, wsf1) or _Holds(lqf1, wsf2)):
        logger.info("Hivee: Top 8 losers' routing not known yet, "
                    "taking Hivee's order")
    if swap:
        aligned["lqf1"], aligned["lqf2"] = lqf2, lqf1
        aligned["lr1a"], aligned["lr1b"] = aligned["lr1b"], aligned["lr1a"]
    return aligned


def _Score(card):
    """A decided card's score [a, b]: its own when it says who won, else
    (a DQ, no score entered) 1-0 to the winner."""
    winner = card.get("winner")
    scores = card.get("scores")
    if isinstance(scores, list) and len(scores) == 2 and all(
            isinstance(s, int) and not isinstance(s, bool) for s in scores) \
            and scores[winner] > scores[1 - winner]:
        return list(scores)
    return [1, 0] if winner == 0 else [0, 1]


def Top8FromCards(cards, codename):
    """Hivee's Top 8 cards ({slot id: card or None}) as
    Top8Model.FromBracket's (players, matches): the 8 starting seats as the
    players (1/2 = WSF1, 3/4 = WSF2, 5/6 = LR1A, 7/8 = LR1B; each with the
    characters of their latest set that has some), and every decided card's
    score oriented to Top8Model's slots. None when `cards` is not a
    mapping. A player is known by their name (the overlay has no ids)."""
    if not isinstance(cards, dict):
        logger.warning("Hivee: the Top 8 has no cards")
        return None
    aligned = _Aligned(cards)

    def mains(name):
        # The latest match first.
        for matchId in reversed(top8.MATCH_IDS):
            for seat in _Seats(aligned[matchId]):
                if seat is not None and _Name(seat) == name:
                    found = Mains(seat.get("characters"), codename)
                    if found:
                        return found
        return {}

    players = {}
    slotOf = {}
    ambiguous = set()
    for matchId, slots in top8.INITIAL_SLOTS.items():
        for seat, slot in zip(_Seats(aligned[matchId]), slots):
            name = _Name(seat) if seat else ""
            players[str(slot)] = {"name": name, "sponsor": "",
                                  "country_code": None,
                                  "mains": mains(name) if name else {}}
            if not name:
                continue
            if name in slotOf:
                ambiguous.add(name)
            slotOf[name] = slot
    for name in ambiguous:
        logger.warning("Hivee: two Top 8 players share a name; their sets "
                       "are not scored")
        slotOf.pop(name, None)

    matches = top8.Recompute(
        {mid: top8.DefaultMatch(mid) for mid in top8.MATCH_IDS})
    for matchId in top8.MATCH_IDS:
        card = aligned[matchId]
        if card is None or card.get("winner") not in (0, 1):
            continue
        score = _Score(card)
        ours = matches[matchId]["slots"]
        theirs = [slotOf.get(_Name(seat), top8.TBD) if seat else top8.TBD
                  for seat in _Seats(card)]
        if top8.TBD in ours or top8.TBD in theirs:
            logger.warning(f"Top8: participants unresolved for {matchId} "
                           f"(ours={ours}, hivee={theirs}), skipping its "
                           "score")
            continue
        if theirs == ours[::-1]:
            score = score[::-1]
        elif theirs != ours:
            logger.warning(f"Top8: participant mismatch for {matchId} "
                           f"(ours={ours}, hivee={theirs}), skipping its "
                           "score")
            continue
        matches[matchId]["score"] = score
        matches[matchId]["finished"] = True
        matches = top8.Recompute(matches)
    return players, matches
