"""The persisted state store: ``user_data/state.json`` (ADR-005 S3b).

Until S3b the state was persisted by accident. ``StateManager.LoadState()``
read back ``out/program_state.json``, the export the OBS overlays and
Advanced Scene Switcher read. That file belongs to its readers: its
location, its shape and the ``timestamp`` / ``revision`` stamps are an
external contract, ``out/`` is served over HTTP and is the folder an
operator clears or points OBS at, and it carries no version -- so the shape
of the state it held could never change (known-smells S-14).

This module is the store of record instead:

- **What**: the published state (the view program_state.json gets: held
  subtrees rewound to their last commit) without the export stamps, wrapped
  as ``{"state_version": N, "state": {...}}``. The version lives in the
  envelope, never in the tree (see ``StateMigrations``).
- **When**: ``StateManager.SaveState()`` hands every published snapshot to
  ``Schedule()``. One writer thread writes the newest one once the state has
  been quiet for ``WRITE_DELAY`` seconds -- but no later than
  ``MAX_WRITE_DELAY`` after the first unwritten change, so a steady stream of
  updates cannot postpone it forever. Independent of ``./out`` and of
  ``general.disable_export``.
- **Shutdown**: the writer is not a daemon, like the export writer, so the
  interpreter's shutdown join waits for it. Once the main thread is gone it
  stops waiting out the delay and writes at once.
- **Load**: ``Load()`` at launch reads ``state.json`` when it is there and
  usable. Otherwise it migrates ``out/program_state.json`` once (also the
  fallback for a corrupt store, or one written by a newer build -- both are
  set aside as time-stamped ``.bak`` files first, never overwritten). A
  ``program_state.json`` newer than ``state.json`` wins too: the export
  writes it moments after every save cycle, so after a crash it holds the
  last seconds the debounced store missed. A ``state.json`` that cannot be
  read or set aside is never overwritten: the store stays closed for the
  session.

Threading: ``Schedule()`` is called with ``StateManager.lock`` held and takes
only this module's own condition, briefly, with no I/O under it. The writer
never takes ``StateManager.lock`` and never touches Qt. Snapshots are not
copied: a snapshot SaveState() has published is never mutated afterwards
(the export writer relies on the same).

Only a process that loaded the store writes it (``Load()`` opens it), so a
script that imports StateManager and writes state without ``Initialize()``
cannot overwrite the operator's saved state with a partial one.

Import side effects: none (agent-pitfalls #1).
"""
import datetime
import os
import tempfile
import threading
import time
import traceback

import orjson
from loguru import logger

from . import StateMigrations

STATE_FILE = "./user_data/state.json"
# What a pre-S3b build persisted to, read once to migrate (and as the
# fallback when STATE_FILE cannot be used).
LEGACY_STATE_FILE = "./out/program_state.json"

VERSION_KEY = "state_version"
STATE_KEY = "state"

# Stamped into program_state.json by the export writer
# (StateManager._WriteExports) for the file-polling overlays. They describe
# the file, not the state, so they are kept out of the loaded state and out
# of the store.
EXPORT_STAMP_KEYS = ("timestamp", "revision")

# Write once the state has been quiet this long...
WRITE_DELAY = 1.0
# ...but never later than this after the first change not yet written.
MAX_WRITE_DELAY = 5.0
# How often a waiting writer looks for the main thread having exited.
_SHUTDOWN_POLL = 0.1

# Retry budget for the replace step, as in StateManager._atomic_write_bytes
# (a reader without FILE_SHARE_DELETE -- an antivirus scan, a backup tool --
# makes it fail for a moment on Windows).
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.02

# Attempts at reading state.json before an OSError (other than "missing")
# closes the store for the session, and the pause between them.
_READ_ATTEMPTS = 3
_READ_RETRY_DELAY = 0.1

# Temp files of a writer killed mid-write are removed at launch once older
# than this; a younger one could belong to a write still in progress.
_STALE_TEMP_AGE = 3600.0

_NEWER = "newer"


def StripExportStamps(state):
    """`state` without the export stamps; the same dict when it has none."""
    if not any(key in state for key in EXPORT_STAMP_KEYS):
        return state
    # dict() copies in one C-level step, so a concurrent write to `state`
    # cannot land between iteration steps.
    stripped = dict(state)
    for key in EXPORT_STAMP_KEYS:
        stripped.pop(key, None)
    return stripped


class StatePersistence:
    # Absolute path of state.json, or None while the store is not open:
    # before Load(), or when a newer build's store could not be set aside
    # (it is then left alone for the whole session).
    _path = None

    # Guards everything below. Never held during I/O, never held while
    # taking StateManager.lock.
    _cond = threading.Condition(threading.Lock())
    _pending = None          # newest snapshot not yet written
    _pendingSince = 0.0      # monotonic time of the first unwritten change
    _lastChange = 0.0        # monotonic time of the newest Schedule()
    _writing = False         # the writer is writing (outside _cond)
    _flushRequested = False  # Flush() is waiting: skip the delay
    _running = False         # a writer thread exists
    _thread = None           # the most recently started writer
    _failureLogged = False   # a failed write was logged at error level

    @staticmethod
    def Path():
        """The open store's absolute path, or None when it is not open."""
        return StatePersistence._path

    # ── Load ─────────────────────────────────────────────────────────────

    @staticmethod
    def Load(statePath=STATE_FILE, legacyPath=LEGACY_STATE_FILE):
        """Read the state this launch starts from, and open the store.

        Returns ``(state, found)``. `state` is at
        ``StateMigrations.CURRENT_VERSION`` and has no export stamps.
        `found` is False only when neither file exists; StateManager then
        skips state_big_change, as it always did for a missing
        program_state.json.

        - state.json usable: its state, migrated from its version -- unless
          out/program_state.json is newer (see below).
        - state.json usable but out/program_state.json newer and readable:
          program_state.json, migrated from v0, and state.json rewritten
          from it right away. The export writes program_state.json within
          moments of every save cycle while the store waits out its delay,
          so after a crash or a forced exit it holds the last few seconds
          the store missed. It is also newer after a session of a build
          that does not write the store (a downgrade and back).
        - state.json missing: out/program_state.json migrated from v0, and
          state.json written from it right away -- from then on it is the
          store, whatever happens to ./out.
        - state.json corrupt: moved to ``state.json.corrupt-<time>.bak``,
          then as if missing.
        - state.json from a newer build: moved to
          ``state.json.v<N>.<time>.bak``, then as if missing.
        - state.json unreadable (an OSError other than "missing", after a
          few attempts), or corrupt / newer and impossible to move aside:
          left untouched, and the store stays closed for this session --
          nothing overwrites it. The state comes from program_state.json.
        - Neither readable: ``{}``.
        """
        cls = StatePersistence
        statePath = os.path.abspath(statePath)
        legacyPath = os.path.abspath(legacyPath)
        cls._RemoveStaleTemps(statePath)
        found = False
        writable = True
        state = None

        raw, storeMtime, readError = cls._ReadStore(statePath)
        if readError is not None:
            # Locked or unreadable, but not known to be bad: never replaced.
            found = True
            writable = False
            logger.error(
                f"Could not read {statePath} ({readError}). It is left "
                f"untouched and this session does not save {statePath}; "
                f"starting from {legacyPath}.")

        if raw is not None:
            found = True
            stored, version, problem = cls._ParseStore(raw)
            if problem is None:
                try:
                    state = StateMigrations.Migrate(stored, version)
                except Exception:
                    logger.error(traceback.format_exc())
                    problem = f"migration from state_version {version} failed"
            if problem == _NEWER:
                backup = cls._BackupPath(statePath, f"v{version}.")
                if cls._SetAside(statePath, backup):
                    logger.warning(
                        f"{statePath} was written by a newer Autona "
                        f"(state_version {version}; this build reads up to "
                        f"{StateMigrations.CURRENT_VERSION}). Moved it to "
                        f"{backup} and started from {legacyPath} instead.")
                else:
                    writable = False
                    logger.error(
                        f"{statePath} was written by a newer Autona "
                        f"(state_version {version}) and could not be moved "
                        f"aside. It is left untouched and this session does "
                        f"not save {statePath}; starting from {legacyPath}.")
            elif problem is not None:
                backup = cls._BackupPath(statePath, "corrupt-")
                if cls._SetAside(statePath, backup):
                    logger.warning(
                        f"{statePath} is unusable ({problem}); moved it to "
                        f"{backup} and started from {legacyPath} instead.")
                else:
                    # Kept as the evidence of what went wrong.
                    writable = False
                    logger.error(
                        f"{statePath} is unusable ({problem}) and could not "
                        f"be moved aside. It is left untouched and this "
                        f"session does not save {statePath}; starting from "
                        f"{legacyPath}.")

        migrated = False
        if state is not None:
            newer = cls._NewerLegacy(legacyPath, storeMtime, statePath)
            if newer is not None:
                state = newer
                migrated = True
            else:
                # Never written by this module, but a hand-restored copy of
                # program_state.json would carry them.
                state = StripExportStamps(state)
        else:
            legacy, legacyFound = cls._ReadLegacy(legacyPath)
            found = found or legacyFound
            if legacy is not None:
                try:
                    state = StateMigrations.Migrate(
                        StripExportStamps(legacy), 0)
                    migrated = True
                except Exception:
                    logger.error(traceback.format_exc())
            if state is None:
                state = {}

        with cls._cond:
            cls._path = statePath if writable else None

        if migrated and writable:
            try:
                cls._WriteStore(statePath, state)
                logger.info(
                    f"Migrated {legacyPath} to {statePath} (state_version "
                    f"{StateMigrations.CURRENT_VERSION})")
            except Exception:
                # The first published save cycle writes it again.
                cls._LogWriteFailure(statePath)
        return state, found

    @staticmethod
    def _ReadStore(statePath):
        """(body, mtime_ns, error) of state.json.

        A missing file is ``(None, None, None)``. Any other OSError is
        retried a few times (an antivirus scan or a backup tool can hold the
        file for a moment) and then returned as `error`.
        """
        error = None
        for attempt in range(_READ_ATTEMPTS):
            try:
                with open(statePath, "rb") as file:
                    return (file.read(), os.fstat(file.fileno()).st_mtime_ns,
                            None)
            except FileNotFoundError:
                return None, None, None
            except OSError as caught:
                error = caught
                if attempt < _READ_ATTEMPTS - 1:
                    time.sleep(_READ_RETRY_DELAY)
        return None, None, error

    @staticmethod
    def _NewerLegacy(legacyPath, storeMtime, statePath):
        """program_state.json's state, at v1, when it is newer than the store.

        None when it is not newer, missing, or unusable -- the store is then
        kept as it is.
        """
        try:
            legacyMtime = os.stat(legacyPath).st_mtime_ns
        except OSError:
            return None
        if storeMtime is None or legacyMtime <= storeMtime:
            return None
        try:
            with open(legacyPath, "rb") as file:
                legacy = orjson.loads(file.read())
        except Exception as error:
            logger.warning(
                f"{legacyPath} is newer than {statePath} but unreadable "
                f"({error}); keeping {statePath}.")
            return None
        if not isinstance(legacy, dict):
            logger.warning(
                f"{legacyPath} is newer than {statePath} but does not hold "
                f"a state object; keeping {statePath}.")
            return None
        try:
            state = StateMigrations.Migrate(StripExportStamps(legacy), 0)
        except Exception:
            logger.error(traceback.format_exc())
            return None
        logger.info(
            f"{legacyPath} is newer than {statePath} (the last session ended "
            f"before the store caught up, or a build without the store ran "
            f"since); starting from {legacyPath}.")
        return state

    @staticmethod
    def _RemoveStaleTemps(statePath):
        """Delete temp files a killed writer left next to the store.

        Only ones older than _STALE_TEMP_AGE: a younger one could still be
        being written. Best effort -- nothing here may stop a launch.
        """
        directory = os.path.dirname(statePath)
        prefix = os.path.basename(statePath).split(".", 1)[0] + "."
        cutoff = time.time() - _STALE_TEMP_AGE
        try:
            names = os.listdir(directory)
        except OSError:
            logger.opt(exception=True).debug(
                f"Could not list {directory} for stale temp files")
            return
        for name in names:
            if not (name.startswith(prefix) and name.endswith(".tmp")):
                continue
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    logger.debug(f"Removed the stale temp file {path}")
            except OSError:
                logger.opt(exception=True).debug(
                    f"Could not remove the stale temp file {path}")

    @staticmethod
    def _ParseStore(raw):
        """(state, version, problem) of a state.json body.

        `problem` is None for a usable store, _NEWER for one from a newer
        build (`version` set), else a description of what is wrong.
        """
        try:
            document = orjson.loads(raw)
        except orjson.JSONDecodeError as error:
            return None, None, f"not JSON: {error}"
        if not isinstance(document, dict):
            return None, None, "not a JSON object"
        version = document.get(VERSION_KEY)
        if not isinstance(version, int) or isinstance(version, bool):
            return None, None, f"no integer {VERSION_KEY}"
        if version > StateMigrations.CURRENT_VERSION:
            return None, version, _NEWER
        if version < 0:
            return None, None, f"negative {VERSION_KEY} {version}"
        state = document.get(STATE_KEY)
        if not isinstance(state, dict):
            return None, None, f"no {STATE_KEY} object"
        return state, version, None

    @staticmethod
    def _ReadLegacy(legacyPath):
        """(state or None, found) of out/program_state.json."""
        try:
            with open(legacyPath, "rb") as file:
                legacy = orjson.loads(file.read())
        except FileNotFoundError:
            return None, False
        except Exception:
            logger.error(traceback.format_exc())
            return None, True
        if not isinstance(legacy, dict):
            logger.error(
                f"{legacyPath} does not hold a state object; starting from "
                f"an empty state")
            return None, True
        return legacy, True

    @staticmethod
    def _BackupPath(statePath, label):
        """``<statePath>.<label><time>.bak``, never an existing file.

        The time keeps every set-aside copy: a second corrupt store, or a
        second downgrade, must not replace the evidence of the first.
        """
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{statePath}.{label}{stamp}"
        candidate = f"{base}.bak"
        suffix = 2
        while os.path.exists(candidate):
            candidate = f"{base}-{suffix}.bak"
            suffix += 1
        return candidate

    @staticmethod
    def _SetAside(path, backup):
        """Move `path` to `backup` (replacing it). True on success."""
        try:
            os.replace(path, backup)
            return True
        except OSError:
            logger.error(traceback.format_exc())
            return False

    # ── Write ────────────────────────────────────────────────────────────

    @staticmethod
    def Schedule(snapshot):
        """Queue a published snapshot; the writer stores the newest one.

        Called by StateManager.SaveState() with StateManager.lock held, so
        this never waits for I/O: it records the snapshot and, when no
        writer is running, starts one. A no-op while the store is closed.
        """
        cls = StatePersistence
        with cls._cond:
            if cls._path is None:
                return
            now = time.monotonic()
            if cls._pending is None:
                cls._pendingSince = now
            cls._pending = snapshot
            cls._lastChange = now
            if not cls._running:
                cls._StartWriter()

    @staticmethod
    def Flush(timeout=None):
        """Write what is pending now, without the delay, and wait for it.

        True once nothing is pending or being written; False on timeout or
        if no writer could be started. For tests and anything that must see
        the file current; the app itself relies on the shutdown join.
        """
        cls = StatePersistence
        deadline = None if timeout is None else time.monotonic() + timeout
        with cls._cond:
            while cls._pending is not None or cls._writing:
                if cls._pending is not None:
                    # A request the writer consumes with the snapshot it
                    # takes; set again for each snapshot still pending.
                    cls._flushRequested = True
                    if not cls._running and not cls._StartWriter():
                        # Withdrawn: left set, it would make the next
                        # writer skip the delay for a Flush long gone.
                        cls._flushRequested = False
                        return False
                    cls._cond.notify_all()
                if deadline is None:
                    cls._cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        cls._flushRequested = False
                        return False
                    cls._cond.wait(remaining)
            return True

    @staticmethod
    def _StartWriter():
        """Start a writer. Caller holds _cond. True if it started."""
        cls = StatePersistence
        thread = threading.Thread(target=cls._Run, name="AutonaStatePersist")
        cls._running = True
        try:
            thread.start()
        except Exception:
            # e.g. "can't create new thread at interpreter shutdown". The
            # snapshot stays pending; the next Schedule() or Flush() starts
            # a writer for it. Not raised: the save cycle that called us
            # still has its export to queue.
            cls._running = False
            logger.opt(exception=True).debug(
                "state.json writer could not start; the next save retries")
            return False
        # The new writer cannot look at this before it is assigned: its
        # first step takes _cond, which is still held here.
        cls._thread = thread
        return True

    @staticmethod
    def _Run():
        """Writer body: wait out the delay, write the newest snapshot, repeat.

        Deliberately NOT a daemon (same reasoning as the export writer):
        it does bounded local disk I/O only, and the interpreter's shutdown
        join of non-daemon threads is what gets the last state into the
        store when the app exits.
        """
        cls = StatePersistence
        me = threading.current_thread()
        inFlight = None
        try:
            while True:
                with cls._cond:
                    while True:
                        if cls._pending is None:
                            cls._running = False
                            cls._flushRequested = False
                            cls._cond.notify_all()
                            return
                        now = time.monotonic()
                        due = min(cls._lastChange + WRITE_DELAY,
                                  cls._pendingSince + MAX_WRITE_DELAY)
                        if (cls._flushRequested or now >= due
                                or not threading.main_thread().is_alive()):
                            break
                        cls._cond.wait(min(due - now, _SHUTDOWN_POLL))
                    inFlight, path = cls._pending, cls._path
                    cls._pending = None
                    cls._writing = True
                    # Consumed: a later snapshot waits out the delay again
                    # unless a Flush still waiting asks for it too.
                    cls._flushRequested = False
                try:
                    if path is not None:
                        cls._WriteStore(path, inFlight)
                except Exception:
                    cls._LogWriteFailure(path)
                inFlight = None
                with cls._cond:
                    cls._writing = False
                    cls._cond.notify_all()
        finally:
            # Still flagged as ours only if a BaseException escaped. Put the
            # snapshot that was being written back (unless a newer one is
            # already waiting) and release the flag, so the next Schedule()
            # or Flush() starts a writer that stores it.
            with cls._cond:
                if cls._running and cls._thread is me:
                    if inFlight is not None and cls._pending is None:
                        cls._pending = inFlight
                        cls._pendingSince = time.monotonic()
                    cls._running = False
                    cls._writing = False
                    cls._flushRequested = False
                    cls._cond.notify_all()

    @staticmethod
    def _WriteStore(path, state):
        """Write `state` to `path` atomically (temp file + os.replace).

        No direct-overwrite fallback, unlike program_state.json: nobody
        polls this file, so there is no torn read to trade against, and a
        stale store is better than a truncated one. No fsync, like
        settings.json; a store lost to a power cut falls back to
        program_state.json at the next launch.
        """
        data = orjson.dumps(
            {VERSION_KEY: StateMigrations.CURRENT_VERSION,
             STATE_KEY: StripExportStamps(state)},
            option=orjson.OPT_NON_STR_KEYS)
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(path), prefix="state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            delay = _REPLACE_BACKOFF
            for attempt in range(_REPLACE_ATTEMPTS):
                try:
                    os.replace(tmp, path)
                    break
                except OSError:
                    if attempt == _REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(delay)
                    delay *= 2
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        StatePersistence._failureLogged = False

    @staticmethod
    def _LogWriteFailure(path):
        # Once at error level per run of failures: whatever keeps the file
        # from being written usually keeps doing so, and this runs on every
        # write. The next success re-arms it.
        message = f"Could not write {path}:\n{traceback.format_exc()}"
        if not StatePersistence._failureLogged:
            StatePersistence._failureLogged = True
            logger.error(message)
        else:
            logger.debug(message)
