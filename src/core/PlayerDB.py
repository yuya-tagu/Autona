"""The local player database (user_data/local_players.json).

The data half of what used to be src/AutonaPlayerDB.py, moved here Qt-free
(ADR-005 S3e-1): the players by tag, loading (with the legacy CSV import),
merging, deleting, looking up and saving them, and the tags of the loaded
tournament's entrants. The Qt half -- the completer model and the
`db_updated` signal -- stays in src/AutonaPlayerDB.py, which subscribes to
`changed` here.

What changed in the move, and what did not:

- `database` is guarded by `lock` (an RLock). It used to be a bare class
  dict written from the GUI thread (the player card, the RESULT import, the
  player browser) and from the start.gg entrants worker at the same time.
  Readers outside this class go through Get / Contains / Items /
  GetPlayerFromTag / TournamentPlayers; the Qt adapter, which also rewrites
  mains in place while building its model, holds `lock` itself. What leaves
  the process (TournamentPlayers: the web server's /playerdb and its
  Socket.IO push) is a deep copy taken under the lock, so serialising it
  never races a writer.
- Changes are announced on `changed` (an ObserverChannel: several
  subscribers, synchronous, each one's exception isolated), always *outside*
  `lock`. The Qt adapter's subscriber marshals to the GUI thread and waits
  for it, and the model build there takes `lock`; announcing under the lock
  would deadlock a worker against the GUI thread. One announcement per
  LoadDB / AddPlayers (unless deferRefresh) / DeletePlayer, and one per
  explicit NotifyChanged() -- the points where the old code called
  SetupModel(), so db_updated fires as often as it did.
- SaveDB serialises under `lock` and writes outside it: a temp file next to
  the database, os.replace()d over it, so a crash mid-write leaves the
  previous file instead of a truncated one. Writes are serialised by their
  own lock and numbered, so an older snapshot never lands after a newer one.
  When the replace keeps failing (or the temp file cannot be made), it falls
  back to the old in-place overwrite rather than not saving. The bytes are
  the same as before: json.dumps(indent=2) written in text mode (os.linesep
  line ends), utf-8.
- Nothing else: the load-time normalisation, the save-time field filter and
  every merge rule are the old code's, pinned against what it produced by
  tests/test_player_db.py (tests/fixtures/player_db).

Import-inert like the rest of src/core: bootstrap_core() builds `instance`,
the shell calls LoadDB().
"""
import csv
import glob
import json
import os
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

    @staticmethod
    def PlayerTag(player):
        """DB key / model display tag for a player dict. Must stay the
        single source of truth so entrant tags collected during a
        tournament fetch match the keys AddPlayers writes."""
        return player.get("prefix")+" "+player.get("gamerTag") \
            if player.get("prefix") else player.get("gamerTag")

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
                csv_imported = self._Load(jsonExisted)
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
        except Exception:
            logger.error(traceback.format_exc())
            return
        if csv_imported:
            logger.info(
                "Persisting players migrated from the legacy CSV database")
            self.SaveDB()
        self.changed.notify()

    def _Load(self, jsonExisted):
        """Fill `database` from disk; True when the legacy CSV was imported.
        Caller holds `lock`."""
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
                        except:
                            player["mains"] = {}
                            logger.error(f"No mains found for: {tag}")

        if os.path.exists(self.legacyCsvPath):
            try:
                os.remove(self.legacyCsvPath)
            except Exception:
                logger.error(traceback.format_exc())

        with open(self.path, 'rt', encoding='utf-8') as jsonfile:
            player_list = orjson.loads(jsonfile.read())
            for player in player_list:
                tag = player.get("prefix")+" "+player.get("gamerTag") if player.get("prefix") else player.get("gamerTag")
                if tag not in self.database:
                    self.database[tag] = player

                    try:
                        player["mains"] = player.get("mains", "{}")
                    except:
                        player["mains"] = {}
                        logger.error(f"No mains found for: {tag}")

        # One line instead of one per player: a 3k-entry DB used to emit 3k
        # INFO records at every launch (written to two log sinks).
        logger.info(
            f"Loaded {len(self.database)} players from the local database")
        return csv_imported

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
                except:
                    logger.error(traceback.format_exc())
            self.database[tag] = player

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
        Failures are logged, never raised (as before)."""
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
        except OSError:
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
        matches `tag`, case-insensitively. The live dict, as Get; do not
        change it outside `lock`."""
        with self.lock:
            for player_db in self.database.values():
                if tag.lower() == player_db.get("gamerTag").lower():
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
        entrants when one is loaded, the whole DB otherwise (the same rule
        as the desktop name completers).

        For use outside the process (the web server's /playerdb and its
        Socket.IO push): a deep copy, players and all, taken under the lock,
        so serialising it cannot race the entrants worker or the model
        build, both of which change player dicts (and their mains lists) in
        place. Key order is kept, so the JSON is the same as the live
        dict's."""
        with self.lock:
            tags = self.tournamentTags
            return {tag: deep_clone(player)
                    for tag, player in self.database.items()
                    if not tags or tag in tags}
