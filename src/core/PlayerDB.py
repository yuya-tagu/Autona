"""The local player database (user_data/local_players.json).

The data half of what used to be src/AutonaPlayerDB.py, moved here Qt-free
(ADR-005 S3e-1): the players by tag, loading (with the legacy CSV import),
merging, deleting, looking up and saving them, and the tags of the loaded
tournament's entrants. The Qt half -- the `db_updated` signal -- stays in
src/AutonaPlayerDB.py, which subscribes to `changed` here. (Its completer
model went in ADR-005 S4ad / the Qt model clean-up's PR3; the mains
normalisation its build did in place is NormaliseMains here.)

What changed in the move, and what did not:

- `database` is guarded by `lock` (an RLock). It used to be a bare class
  dict written from the GUI thread (the player card, the RESULT import, the
  player browser) and from the start.gg entrants worker at the same time.
  Readers outside this class go through Get / Contains / Items /
  GetPlayerFromTag / TournamentPlayers; NormaliseMains, which rewrites
  mains in place, holds `lock` for its whole walk. What leaves
  the process (TournamentPlayers: the web server's /playerdb and its
  Socket.IO push) is a deep copy taken under the lock, so serialising it
  never races a writer.
- Changes are announced on `changed` (an ObserverChannel: several
  subscribers, synchronous, each one's exception isolated), always *outside*
  `lock`. The Qt adapter's subscriber marshals to the GUI thread, and the
  refresh there (NormaliseMains) takes `lock`; until ADR-005 S3f-2 the
  subscriber also waited for it, and announcing under the lock deadlocked a
  worker against the GUI thread. A subscriber may still take the lock. One
  announcement per
  LoadDB / AddPlayers (unless deferRefresh) / DeletePlayer, and one per
  explicit NotifyChanged() -- the points where the old code called
  SetupModel(), so db_updated fires as often as it did.
- SaveDB serialises under `lock` and writes outside it: a temp file next to
  the database, os.replace()d over it, so a crash mid-write leaves the
  previous file instead of a truncated one. Writes are serialised by their
  own lock and numbered, so an older snapshot never lands after a newer one.
  When the replace keeps failing (or the temp file cannot be made), it falls
  back to the old in-place overwrite rather than not saving. When the disk
  is full (ENOSPC/EDQUOT) it does not overwrite: the previous file is kept
  and the next SaveDB tries again. The bytes are
  the same as before: json.dumps(indent=2) written in text mode (os.linesep
  line ends), utf-8.
- A database that could not be read is never saved over: a corrupt file is
  moved aside (local_players.json.unreadable-<time>.bak) before the DB starts
  empty, and one that cannot be opened stops SaveDB (`loadFailed`) until a
  later SaveDB can read it (the players it has are merged in, memory's win).
  The newest 3 local_players.json.skipped-*.bak copies are kept. A record that is no player is skipped, not the whole file.
- Nothing else: the load-time normalisation, the save-time field filter and
  every merge rule are the old code's, pinned against what it produced by
  tests/test_player_db.py (tests/fixtures/player_db).

Import-inert like the rest of src/core: bootstrap_core() builds `instance`,
the shell calls LoadDB().
"""
import csv
import datetime
import errno
import glob
import json
import os
import shutil
import tempfile
import threading
import time
import traceback

import orjson
from loguru import logger

from .DictHelper import deep_clone
from .Observable import ObserverChannel
from .SettingsManager import SettingsManager

# Relative to the process CWD, resolved at each read / write (as before).
DB_PATH = "./user_data/local_players.json"
LEGACY_CSV_PATH = "./user_data/local_players.csv"

# The replace step is tried this many times (so retried one time fewer) on
# Windows, where a reader holding the target open makes os.replace fail with
# a sharing violation: 20 + 40 + 80 ms of sleep at worst. Same budget as the
# state store's writer. After that, SaveDB overwrites in place (as the old
# code always did).
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.02

_DISK_FULL_ERRNOS = {errno.ENOSPC, getattr(errno, "EDQUOT", None)} - {None}


def _IsDiskFull(error):
    """True for "no space left" (ENOSPC / EDQUOT, or the Windows
    ERROR_HANDLE_DISK_FULL 39 / ERROR_DISK_FULL 112)."""
    return (error.errno in _DISK_FULL_ERRNOS
            or getattr(error, "winerror", None) in (39, 112))

# How many local_players.json.skipped-*.bak copies are kept.
SKIPPED_COPIES_KEPT = 3


class _UnusableFile(Exception):
    """local_players.json was read but holds no player list (bad JSON, bad
    utf-8, or not a list at the top): it is moved aside, never saved over."""


class PlayerDB:
    instance: "PlayerDB" = None

    # The keys a player keeps in local_players.json; anything else (seed,
    # id, avatar, ...) lives in memory only and is dropped at save time.
    fieldnames = ["prefix", "gamerTag", "name", "twitter",
                  "country_code", "state_code", "mains", "custom_textbox"]

    def __init__(self, path=DB_PATH, legacyCsvPath=LEGACY_CSV_PATH):
        self.path = path
        self.legacyCsvPath = legacyCsvPath
        self.database = {}
        self.lock = threading.RLock()
        # DB tags (see PlayerTag) of the loaded tournament's entrants. Empty
        # = no tournament loaded -> the completers and /playerdb show the
        # whole DB. Replaced, never mutated in place, so a reader holding the
        # old set is never surprised.
        self.tournamentTags = set()
        self.changed = ObserverChannel("player_db.changed")
        # File writes happen outside `lock`; this serialises them. Lock
        # order: `lock` -> `_saveLock`, never the other way (nothing holding
        # `_saveLock` takes `lock`).
        self._saveLock = threading.Lock()
        # Snapshots are numbered under `lock` and written under `_saveLock`;
        # a write whose number is older than the one on disk is dropped.
        self._snapshotGeneration = 0
        self._writtenGeneration = 0
        # True when LoadDB could not read the file (locked by another
        # process, or corrupt and it could not be moved aside): SaveDB then
        # writes nothing for the rest of the session, so a DB that was never
        # read is not replaced by what this session added. Changes still
        # apply in memory.
        self.loadFailed = False
        self._warnedLoadFailed = False
        # True when loadFailed is only "could not open it": SaveDB then reads
        # the file again each time (_TryRecover). Not set when the file is
        # corrupt and could not be moved aside, nor after a partial load.
        self._reloadable = False

    @staticmethod
    def PlayerTag(player):
        """DB key / model display tag for a player dict. Must stay the
        single source of truth so entrant tags collected during a
        tournament fetch match the keys AddPlayers writes."""
        return player.get("prefix")+" "+player.get("gamerTag") \
            if player.get("prefix") else player.get("gamerTag")

    @staticmethod
    def ParseMains(mains) -> dict:
        """A record's `mains` as the per-game dict: an old row's is a JSON
        string, and a load gives a row without one the string "{}". {} for
        anything that does not make one."""
        if isinstance(mains, str):
            try:
                mains = json.loads(mains)
            except ValueError:
                return {}
        return mains if isinstance(mains, dict) else {}

    # -- loading -----------------------------------------------------------

    def LoadDB(self):
        try:
            self._RemoveStaleTemps()
            # The first run's empty file, written before taking `lock` (no
            # I/O under it); nothing else touches the DB this early.
            jsonExisted = os.path.exists(self.path)
            if not jsonExisted:
                self._Persist(self._NextGeneration(), json.dumps([]))
            with self.lock:
                before = dict(self.database)
                try:
                    csv_imported, skipped = self._Load(jsonExisted)
                except Exception:
                    # What a failed load had put in so far is dropped: a
                    # half-read DB must never reach SaveDB.
                    self.database.clear()
                    self.database.update(before)
                    raise
                # No unconditional write-back: everything _Load does is an
                # in-memory normalisation, so re-serialising the whole DB
                # (indent=2) at every launch only ever rewrote what was just
                # read. The one side effect it also had -- dropping keys
                # outside `fieldnames` from the file -- is a save-time concern
                # and still happens on the next real mutation (AddPlayers /
                # DeletePlayer / editor save); the in-memory copy keeps those
                # keys either way, so nothing observable changes within a
                # session.
                # The legacy CSV import is the exception: its rows exist
                # nowhere else yet, so that migration still has to be written
                # out (below, outside the lock).
        except _UnusableFile as problem:
            # Moved aside outside the lock (no I/O under it); not announced,
            # as a failed load never was.
            self._MoveAside(str(problem))
            return
        except FileNotFoundError:
            # Gone between the exists() check and the open, or the first
            # run's empty file could not be written: there is no file to
            # protect, so saving is allowed (it creates one).
            logger.warning(
                f"{os.path.basename(self.path)} was not there to read; "
                "starting an empty player database")
            return
        except OSError as error:
            # Locked by another process, unreadable: what is on disk is
            # unknown, so nothing is saved over it until it can be read.
            # SaveDB reads it again each time (_TryRecover). The error's
            # type only (its text carries the full path).
            self.loadFailed = True
            self._reloadable = True
            logger.warning(
                f"Could not open {os.path.basename(self.path)} "
                f"({type(error).__name__}); the player database will not be "
                "saved until it can be read")
            return
        except Exception:
            # Anything else (the legacy CSV import failing, say): the
            # database in memory is partial, and a partial database must
            # not be saved over the file. Not retried by SaveDB.
            self.loadFailed = True
            logger.error(traceback.format_exc())
            return
        if skipped:
            self._CopyAside(skipped)
        if csv_imported:
            logger.info(
                "Persisting players migrated from the legacy CSV database")
            self.SaveDB()
        self.changed.notify()

    def _Load(self, jsonExisted):
        """Fill `database` from disk: (True when the legacy CSV was imported,
        how many JSON records were skipped as no player). Caller holds
        `lock`, so this only reads and parses; moving or copying the file is
        LoadDB's, outside the lock. Raises OSError when the file cannot be
        read and _UnusableFile when what it holds is no player list."""
        # True once the legacy CSV import actually put rows in memory. That
        # import exists only in RAM until SaveDB() writes them out, so it is
        # the one path in LoadDB that still has to persist.
        csv_imported = False

        if (not jsonExisted) and os.path.exists(self.legacyCsvPath):
            logger.info("Importing from the previous version of the player database")
            with open(self.legacyCsvPath, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, quotechar='\'')
                for player in reader:
                    tag = player.get(
                        "prefix")+" "+player.get("gamerTag") if player.get("prefix") else player.get("gamerTag")
                    if tag not in self.database:
                        self.database[tag] = player
                        csv_imported = True
                        try:
                            player["mains"] = json.loads(
                                player.get("mains", "{}"))
                        except Exception:
                            player["mains"] = {}
                            logger.error("No mains found for a player")

        if os.path.exists(self.legacyCsvPath):
            try:
                os.remove(self.legacyCsvPath)
            except Exception:
                logger.error(traceback.format_exc())

        # OSError (cannot open / read) goes to the caller as it is; a read
        # that works but gives no player list is _UnusableFile.
        with open(self.path, 'rt', encoding='utf-8') as jsonfile:
            try:
                player_list = orjson.loads(jsonfile.read())
            except ValueError as error:
                # orjson.JSONDecodeError and UnicodeDecodeError are both
                # ValueErrors.
                raise _UnusableFile(type(error).__name__) from error
        if not isinstance(player_list, list):
            raise _UnusableFile("not a list")

        skipped = 0
        for player in player_list:
            # One bad record (null, no string gamerTag, a prefix that is no
            # string) used to fail the whole load; it is skipped instead.
            if not PlayerDB._IsRecord(player):
                skipped += 1
                continue
            tag = PlayerDB.PlayerTag(player)
            if tag not in self.database:
                self.database[tag] = player

                try:
                    player["mains"] = player.get("mains", "{}")
                except Exception:
                    player["mains"] = {}
                    logger.error("No mains found for a player")

        # One line instead of one per player: a 3k-entry DB used to emit 3k
        # INFO records at every launch (written to two log sinks).
        logger.info(
            f"Loaded {len(self.database)} players from the local database")
        return csv_imported, skipped

    @staticmethod
    def _IsRecord(player):
        """A JSON record PlayerTag can key: a dict with a string gamerTag,
        and a prefix that is a string when it is truthy."""
        if not isinstance(player, dict):
            return False
        if not isinstance(player.get("gamerTag"), str):
            return False
        prefix = player.get("prefix")
        return not prefix or isinstance(prefix, str)

    @staticmethod
    def _AsidePath(path, kind):
        """``<path>.<kind>-<time>.bak``, never an existing file (a second
        bad file must not replace the first one's copy). SettingsManager's
        naming."""
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{path}.{kind}-{stamp}"
        candidate = f"{base}.bak"
        suffix = 2
        while os.path.exists(candidate):
            candidate = f"{base}-{suffix}.bak"
            suffix += 1
        return candidate

    def _MoveAside(self, problem):
        """Move a corrupt database out of the way, so the next save starts a
        new file instead of replacing it. When it cannot be moved, nothing
        is saved this session (`loadFailed`). Called without `lock`."""
        name = os.path.basename(self.path)
        backup = PlayerDB._AsidePath(self.path, "unreadable")
        try:
            os.replace(self.path, backup)
        except OSError as error:
            self.loadFailed = True
            logger.warning(
                f"{name} is unusable ({problem}) and could not be moved "
                f"aside ({type(error).__name__}); the player database will "
                "not be saved this session")
            return False
        logger.warning(
            f"{name} is unusable ({problem}); moved it to "
            f"{os.path.basename(backup)} and started an empty player database")
        return True

    def _CopyAside(self, skipped):
        """Keep a copy of a database some records of which were skipped:
        the next save writes only the ones that were read. Called without
        `lock`; a failed copy is logged and the load goes on."""
        backup = PlayerDB._AsidePath(self.path, "skipped")
        try:
            shutil.copyfile(self.path, backup)
            self._PruneSkippedCopies()
        except OSError as error:
            logger.warning(
                f"Could not copy {os.path.basename(self.path)} aside "
                f"({type(error).__name__})")
            backup = None
        logger.warning(
            f"Skipped {skipped} unreadable player records"
            + (f"; the file was copied to {os.path.basename(backup)}"
               if backup else ""))

    def _PruneSkippedCopies(self):
        """Keep the newest SKIPPED_COPIES_KEPT `.skipped-*.bak` copies (they
        pile up on every launch that saves nothing). The `.unreadable-*.bak`
        ones are the user's data and are never touched. Called without
        `lock`; a copy that cannot be removed is left."""
        found = glob.glob(glob.escape(self.path) + ".skipped-*.bak")
        stamped = []
        for path in found:
            try:
                stamped.append((os.stat(path).st_mtime_ns, path))
            except OSError:
                pass
        stamped.sort()
        for _, path in stamped[:-SKIPPED_COPIES_KEPT]:
            try:
                os.remove(path)
            except OSError:
                logger.warning(f"Could not remove {os.path.basename(path)}")

    def _TryRecover(self):
        """Read the file again after a load that could not open it. When it
        reads, add the players the file has and memory does not (the same tag
        keeps memory's record) and clear `loadFailed`; True then. False while
        it still cannot be read (or is corrupt and cannot be moved aside).
        The read is done without `lock` (no I/O under it); the merge takes
        it. Called without `lock`."""
        if not self._reloadable:
            return False
        try:
            records, skipped = self._ReadRecords()
        except FileNotFoundError:
            records, skipped = [], 0  # nothing on disk to protect
        except _UnusableFile as problem:
            # Same as a corrupt file at load: moved aside, then saved over.
            if not self._MoveAside(str(problem)):
                return False
            records, skipped = [], 0
        except OSError as error:
            logger.debug(
                "Player database still cannot be read "
                f"({type(error).__name__})")
            return False
        with self.lock:
            for player in records:
                tag = PlayerDB.PlayerTag(player)
                if tag not in self.database:
                    self.database[tag] = player
                    player["mains"] = player.get("mains", "{}")
            self.loadFailed = False
            self._reloadable = False
            count = len(self.database)
        if skipped:
            self._CopyAside(skipped)
        logger.info(
            f"Read the local player database after all; {count} players now")
        return True

    def _ReadRecords(self):
        """(the records of the file that are players, how many were skipped).
        OSError when it cannot be read, _UnusableFile when it holds no list."""
        with open(self.path, 'rt', encoding='utf-8') as jsonfile:
            try:
                player_list = orjson.loads(jsonfile.read())
            except ValueError as error:
                raise _UnusableFile(type(error).__name__) from error
        if not isinstance(player_list, list):
            raise _UnusableFile("not a list")
        records = [p for p in player_list if PlayerDB._IsRecord(p)]
        return records, len(player_list) - len(records)

    # -- writing -----------------------------------------------------------

    def AddPlayers(self, players, overwrite=False, deferRefresh=False):
        """Merge `players` into the in-memory database.

        When `deferRefresh` is True, SaveDB() and the change announcement are
        skipped -- the caller is responsible for invoking SaveDB() and
        NotifyChanged() once after the final batch. This avoids
        re-serialising the full JSON DB and rebuilding the entire completer
        model on every page of a paginated fetch (9 pages x full O(N) rebuild
        would otherwise freeze the UI on large local databases).
        """
        logger.info(f"Adding players to DB: {len(players)}")
        with self.lock:
            for player in players:
                if player is not None:
                    self._AddPlayer(player, overwrite)
        if not deferRefresh:
            self.SaveDB()
            self.changed.notify()

    def _AddPlayer(self, player, overwrite):
        # Caller holds `lock`.
        tag = PlayerDB.PlayerTag(player)

        if not overwrite:
            if tag not in self.database:
                incomingMains = player.get("mains", {})
                for game in incomingMains:
                    for main in incomingMains[game]:
                        if len(main) == 1:
                            main.append(0)
                self.database[tag] = player
            else:
                dbMains = deep_clone(
                    self.database[tag].get("mains", {})
                )
                if isinstance(dbMains, str):
                    dbMains = json.loads(dbMains)
                incomingMains = player.get("mains", {})

                newMains = []
                for game in incomingMains:
                    for main in incomingMains[game]:
                        if len(main) == 1:
                            found = next(
                                (m for m in dbMains.get(game, []) if m[0] == main[0]), None)
                            if found:
                                main.append(found[1])
                            else:
                                main.append(0)
                        newMains.append(main)
                    dbMains[game] = newMains

                if SettingsManager.Get("general.disable_overwrite", False):
                    self.database[tag] = player | self.database[tag]
                else:
                    self.database[tag].update(player)
                self.database[tag]["mains"] = dbMains
        else:
            if self.database.get(tag) is not None and player.get("mains") is not None:
                try:
                    mains = self.database[tag].get("mains", {})
                    mains.update(player.get("mains", {}))
                    player["mains"] = mains
                except Exception:
                    logger.error(traceback.format_exc())
            self.database[tag] = player

    def KeepStoredMains(self, tag):
        """Make the stored record's `mains` the per-game dict, for a merge
        with AddPlayers(overwrite=True) to follow: the merge cannot update a
        string, so it logs that and replaces them, and the other games' mains
        are lost. AddPlayers stays as it is (tests/fixtures/player_db); a
        save of a player calls this first (PlayerService.SaveRecordToDB)."""
        with self.lock:
            stored = self.database.get(tag)
            if isinstance(stored, dict) and "mains" in stored \
                    and not isinstance(stored["mains"], dict):
                stored["mains"] = PlayerDB.ParseMains(stored["mains"])

    def NormaliseMains(self, codename):
        """Rewrite the `codename` game's mains of every player in place:
        each [character, skin] or [character, skin, variant] becomes
        [character, int skin, variant or ""] (S3c-2 decision 5).

        The desktop's Qt player model did this as a side effect of its build
        on every refresh (AutonaPlayerDB._BuildModel), until the Qt model
        clean-up's PR3 moved it here as it was; both shells call it now
        (AutonaPlayerDB.SetupModel: every change announcement and every game
        load). tests/test_player_db.py pins it against what the model build
        did, quirks included:

        - only a `mains` that is exactly a dict, and truthy, holding
          `codename` is looked at (a stored JSON string is not), and in it
          only `codename`'s list -- no other game's;
        - the filter reads `isinstance(main, list) and len(main) == 2 or
          len(main) == 3` (sic): anything 3 long is taken, a main with no
          len() fails the player before anything is rewritten, and a list
          of another length is skipped, not removed;
        - a skin int() cannot convert is 0, logged with its traceback;
          int() is what reads it (2.7 -> 2, True -> 1, " 3 " -> 3);
        - one player's error is logged (without the player's values) and stops that
          player only: what was rewritten before it in that player's list
          stays rewritten.

        No save and no change announcement: the next save writes the
        rewrite out (AddPlayers / DeletePlayer / a player save), and the
        caller tells the listeners (db_updated) whether anything changed or
        not, as the model build did. Under `lock` for the whole walk (the
        entrants worker merges into the same dicts); nothing in it waits.
        """
        with self.lock:
            for player in self.database.values():
                if player is not None:
                    try:
                        if player.get("mains") and type(player.get("mains")) is dict:
                            if codename in player.get("mains", {}).keys():
                                playerMains = player.get("mains")[codename]

                                if playerMains is not None and len(playerMains) > 0:
                                    # Meant: a list of size 2 [character,
                                    # skin] or 3 (+ variant). The precedence
                                    # is the model build's, kept.
                                    playerMains = [main for main in playerMains if isinstance(
                                        main, list) and (len(main) == 2) or (len(main) == 3)]

                                    # If the skin is invalid, default to 0
                                    for main in playerMains:
                                        while len(main) < 3:
                                            main.append("")
                                        skin = 0
                                        try:
                                            skin = int(main[1])
                                        except Exception:
                                            logger.error(
                                                f'Local DB error: a player has an invalid skin for character {main[0]}')
                                            logger.error(traceback.format_exc())
                                        main[1] = skin

                                        # If no variant, set to none
                                        if len(main) >= 3:
                                            variant = main[2]
                                        else:
                                            variant = ""
                                        main[2] = variant
                    except Exception:
                        logger.error(
                            'Error loading a player from local DB')
                        logger.error(traceback.format_exc())

    def DeletePlayer(self, tag):
        with self.lock:
            if tag in self.database:
                del self.database[tag]
        self.SaveDB()
        self.changed.notify()

    def NotifyChanged(self):
        """Announce a change made with AddPlayers(deferRefresh=True).

        Call it outside `lock`, after SaveDB(), once the batch is complete
        (the start.gg entrants fetch is the caller)."""
        self.changed.notify()

    def SaveDB(self):
        """Write the database to `path`, keeping only `fieldnames`.

        The snapshot (the JSON text) is taken under `lock`; the write is done
        outside it, under `_saveLock`, so a slow disk or a retried replace
        never blocks a reader or the entrants worker. Snapshots are numbered,
        so the file always ends at the newest one even when two saves race.
        Failures are logged, never raised (as before).

        Writes nothing while `loadFailed`: the file LoadDB could not read
        would be replaced by only what this session has in memory (the
        entrants worker saves on its own after every tournament fetch). A
        file that could not be opened is read again here each time
        (_TryRecover); once it reads, its players are merged in and the save
        goes ahead."""
        if self.loadFailed and not self._TryRecover():
            if not self._warnedLoadFailed:
                self._warnedLoadFailed = True
                logger.warning(
                    "Not saving the player database: it could not be read "
                    "at launch")
            else:
                logger.debug("Not saving the player database (unread)")
            return
        try:
            with self.lock:
                generation, text = self._Snapshot()
            self._Persist(generation, text)
        except Exception:
            logger.error(traceback.format_exc())

    def _NextGeneration(self):
        with self.lock:
            self._snapshotGeneration += 1
            return self._snapshotGeneration

    def _Snapshot(self):
        """(generation, JSON text) of the database as it is saved. Caller
        holds `lock`."""
        player_list = []

        for player in self.database.values():
            if player is not None:
                playerData = deep_clone(player)

                for key in list(playerData.keys()):
                    if key not in PlayerDB.fieldnames:
                        del playerData[key]

                player_list.append(playerData)

        return self._NextGeneration(), json.dumps(player_list, indent=2)

    def _Persist(self, generation, text):
        """Write `text` unless a newer snapshot is already on disk. Called
        without `lock`."""
        with self._saveLock:
            if generation < self._writtenGeneration:
                return
            self._WriteText(text)
            self._writtenGeneration = generation

    def _TempPrefix(self):
        # "local_players." for the default path: the temp files are
        # local_players.<random>.tmp, next to the database.
        return os.path.splitext(os.path.basename(self.path))[0] + "."

    def _WriteText(self, text):
        """Replace the file with `text`: atomically when the platform lets
        us, by overwriting it in place (the old way) when it does not."""
        try:
            if self._ReplaceAtomically(text):
                return
        except OSError as error:
            if _IsDiskFull(error):
                # Overwriting in place would truncate a good file and then
                # fail again for lack of space.
                logger.warning(
                    "Not saving the player database: the disk is full; the "
                    f"previous file is kept ({type(error).__name__})")
                return
            logger.warning(
                "Could not write the player database through a temp file:\n"
                + traceback.format_exc())
        logger.warning(
            "Overwriting the player database in place (the temp file could "
            "not replace it)")
        # Text mode with the default newline handling, as the old
        # open(path, 'wt', encoding='utf-8') was.
        with open(self.path, "wt", encoding="utf-8") as file:
            file.write(text)

    def _ReplaceAtomically(self, text):
        """Write a temp file next to the database and os.replace() it over
        the database. False when every replace attempt failed. The temp file
        never outlives this call."""
        directory = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(
            dir=directory, prefix=self._TempPrefix(), suffix=".tmp")
        try:
            # Text mode with the default newline handling, as the old
            # open(path, 'wt', encoding='utf-8') was: "\n" goes out as
            # os.linesep, so the file keeps its exact bytes.
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(text)
            delay = _REPLACE_BACKOFF
            for attempt in range(_REPLACE_ATTEMPTS):
                try:
                    os.replace(tmp, self.path)
                    return True
                except OSError:
                    if attempt == _REPLACE_ATTEMPTS - 1:
                        logger.warning(
                            f"Replacing {self.path} failed {_REPLACE_ATTEMPTS} "
                            "times:\n" + traceback.format_exc())
                        return False
                    time.sleep(delay)
                    delay *= 2
        finally:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass  # replaced: the temp file is the database now
            except OSError:
                logger.warning(f"Could not remove {tmp}")

    def _RemoveStaleTemps(self):
        """Delete temp files a crashed save left next to the database."""
        directory = os.path.dirname(self.path) or "."
        pattern = os.path.join(
            glob.escape(directory), glob.escape(self._TempPrefix()) + "*.tmp")
        with self._saveLock:
            for path in glob.glob(pattern):
                try:
                    os.remove(path)
                    logger.info(f"Removed a stale temp file: {path}")
                except OSError:
                    logger.warning(f"Could not remove a stale temp file: {path}")

    # -- reading -----------------------------------------------------------

    def Get(self, tag, default=None):
        """The player stored under `tag` -- the live dict, as before, for
        the GUI-side callers. Do not change it outside `lock`: the entrants
        worker merges into the same dict under it."""
        with self.lock:
            return self.database.get(tag, default)

    def Contains(self, tag):
        with self.lock:
            return tag in self.database

    def Items(self):
        """(tag, player) pairs, in insertion order: a list taken under the
        lock, so iterating it cannot race a writer. The player dicts are the
        live ones (as Get); do not change them outside `lock`."""
        with self.lock:
            return list(self.database.items())

    def GetPlayerFromTag(self, tag):
        """The first player whose gamerTag (not the DB tag: no prefix)
        matches `tag`, case-insensitively. A row that is no record, or with
        no gamerTag (or one that is not a string: the file can hold anything)
        matches nothing. The live dict, as Get; do not change it outside
        `lock`."""
        target = tag.lower()
        with self.lock:
            for player_db in self.database.values():
                gamerTag = player_db.get("gamerTag") \
                    if isinstance(player_db, dict) else None
                if isinstance(gamerTag, str) and target == gamerTag.lower():
                    return player_db
        return None

    # -- the loaded tournament ---------------------------------------------

    def SetTournamentTags(self, tags):
        with self.lock:
            self.tournamentTags = {tag for tag in tags if tag}

    def ClearTournamentTags(self):
        """Forget the entrants; False when there were none to forget."""
        with self.lock:
            if not self.tournamentTags:
                return False
            self.tournamentTags = set()
            return True

    def TournamentPlayers(self):
        """The players a client is shown: only the loaded tournament's
        entrants when one is loaded, the whole DB otherwise (the rule the
        desktop's Qt name completers followed, until they went with the Qt
        player cards: ADR-005 S4ac-3 / S4ad).

        For use outside the process (the web server's /playerdb and its
        Socket.IO push): a deep copy, players and all, taken under the lock,
        so serialising it cannot race the entrants worker or
        NormaliseMains, both of which change player dicts (and their mains
        lists) in place. Key order is kept, so the JSON is the same as the live
        dict's."""
        with self.lock:
            tags = self.tournamentTags
            return {tag: deep_clone(player)
                    for tag, player in self.database.items()
                    if not tags or tag in tags}
