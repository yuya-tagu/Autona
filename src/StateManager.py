import contextlib
import os
import sys

import orjson
import traceback
from urllib.parse import urlparse

# deepdiff (deephash.py) eagerly runs `try: import pandas` purely to support
# hashing pandas Series/DataFrame objects. Autona only diffs nested dicts of
# primitives, so that ~1.4s pandas import is pure startup dead weight. Pinning
# the sys.modules entry to None makes deepdiff's import fall through to its
# no-pandas path. setdefault() never clobbers a real pandas already imported
# elsewhere, so this is a no-op if anything legitimately needs pandas first.
# StateManager is the only deepdiff importer in src/, so this guard reliably
# runs before deephash loads. (numpy is left alone — its import is cheap.)
sys.modules.setdefault("pandas", None)

from qtpy.QtCore import QObject, Signal
from deepdiff import DeepDiff, Delta, extract
from functools import partial
import shutil
import threading
import requests
from PIL import Image
import time
from loguru import logger
from .Helpers.AutonaDictHelper import deep_get, deep_set, deep_unset, deep_clone
from .SettingsManager import SettingsManager

# Sentinel telling "key absent" apart from a stored None.
_MISSING = object()

# The direct-overwrite fallback below is warned about only once per session:
# whatever keeps the file locked usually keeps doing so, and this runs on
# every save.
_atomicWriteFallbackLogged = False

# Retry budget for the replace step (20 + 40 + 80 ms of sleep at worst).
_ATOMIC_REPLACE_ATTEMPTS = 4
_ATOMIC_REPLACE_BACKOFF = 0.02


def _atomic_write_bytes(path: str, data: bytes):
    """Publish `data` at `path` without ever exposing a half-written file.

    OBS overlays and the layout JS poll this file continuously, so a plain
    open('wb') lets a reader observe a truncated JSON mid-write. Writing a
    sibling temp file and os.replace()-ing it over the target makes the
    swap atomic from a reader's point of view. The temp must live in the
    same directory: rename is only atomic within one volume.

    On Windows the replace fails with a sharing violation while a reader
    holds the target open without FILE_SHARE_DELETE, so retry with a short
    backoff and then fall back to the old direct overwrite -- a possible
    torn read is better than dropping the write entirely.

    No fsync: this guards against cross-process torn reads, not power loss,
    and it sits on the hot save path.
    """
    global _atomicWriteFallbackLogged

    tmp = path + ".tmp"
    try:
        with open(tmp, 'wb', buffering=8192) as file:
            file.write(data)
    except Exception:
        _remove_quietly(tmp)
        raise

    delay = _ATOMIC_REPLACE_BACKOFF
    for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except OSError:  # PermissionError (sharing violation) included
            if attempt < _ATOMIC_REPLACE_ATTEMPTS - 1:
                time.sleep(delay)
                delay *= 2

    message = (f"Atomic replace of {path} kept failing (file locked by a "
               f"reader?); falling back to direct overwrite.")
    if not _atomicWriteFallbackLogged:
        _atomicWriteFallbackLogged = True
        logger.warning(message)
    else:
        logger.debug(message)

    try:
        with open(path, 'wb', buffering=8192) as file:
            file.write(data)
    finally:
        _remove_quietly(tmp)


def _remove_quietly(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


class StateManagerSignals(QObject):
    state_big_change = Signal()
    state_updated = Signal(dict)
    # Deferred publication (Apply button). held_dirty_changed fires when a
    # held subtree gains its first pending change (True) or is committed
    # (False). held_state_updated fires (coalesced per save cycle) whenever
    # live-but-unpublished data under a held prefix changes, so desktop
    # widgets that render from the live state can refresh without waiting
    # for the commit.
    held_dirty_changed = Signal(str, bool)
    held_state_updated = Signal(str)

class StateManager:
    lastSavedState = {}
    state = {}
    saveBlocked = 0
    signals = StateManagerSignals()
    changedKeys = []
    # Deferred publication ("反映" button). Writes under a held prefix apply
    # to the live state immediately (Get() and every in-process reader see
    # them) but are excluded from delta broadcast / program_state.json /
    # ./out text export until CommitHeld(prefix) publishes them in one shot.
    heldPrefixes = set()      # dot key-path prefixes, e.g. "score.1"
    heldKeys = {}             # prefix -> [bracket-form keys] pending commit
    # Sub-paths of a held prefix that publish immediately anyway (fetched
    # derived data such as stats, not operator edits waiting on 反映).
    # prefix -> [(dot path, bracket path)], both absolute.
    heldExempt = {}
    _commitPrefixes = set()   # prefixes queued to flush on the next SaveState
    _heldTouched = set()      # prefixes with held writes not yet notified
    # Monotonically increasing counter incremented on every state mutation
    # broadcast (delta or big change). Clients use it to detect dropped or
    # out-of-order events and request a full snapshot when discontinuous
    # (Design.md §3.2.3 R-Sync-5..9 / R-OBS-3a..b).
    revision = 0

    lock = threading.RLock()
    _export_thread = None
    _download_semaphore = threading.Semaphore(3)
    # In-flight image downloads started by CreateFilesDict. Guarded by its own
    # lock, NOT StateManager.lock: these are registered from the export thread,
    # while SaveState() joins that thread from the GUI thread *while holding*
    # StateManager.lock -- reusing it here would deadlock the pair.
    _download_threads = []
    _download_lock = threading.Lock()
    _initialized = False

    @staticmethod
    def Initialize():
        """Create ./out and load the persisted state.

        Used to run at import time; it is now called explicitly from
        src/bootstrap.py so that importing this module has no side effect.
        Idempotent: later calls are a no-op (re-running LoadState() would
        discard in-memory state that has not been exported yet).
        """
        if StateManager._initialized:
            return
        StateManager._initialized = True

        if not os.path.exists("./out"):
            os.makedirs("./out/")

        if not os.path.isfile("./out/program_state.json"):
            StateManager.SaveState()

        StateManager.LoadState()

    @staticmethod
    def _TrackDownload(thread):
        """Register a running image download so it can be waited on.

        The download writes to "./out/<path>.<ext>", a *relative* path that
        the OS resolves against the process CWD at write time -- not at the
        time the thread was started. Anything that moves the CWD (the widget
        test harness runs the whole app inside a throwaway directory) must be
        able to wait these out first, or a late thread lands its image in
        whatever directory happens to be current by then.

        Finished threads are dropped on every insert, so a long streaming
        session does not grow this list without bound.
        """
        with StateManager._download_lock:
            StateManager._download_threads = [
                t for t in StateManager._download_threads if t.is_alive()]
            StateManager._download_threads.append(thread)

    @staticmethod
    def JoinDownloads(timeout=None):
        """Wait for every in-flight image download; True if all finished.

        `timeout` is the total budget across all of them, not per thread.
        """
        with StateManager._download_lock:
            threads = list(StateManager._download_threads)

        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in threads:
            if deadline is None:
                thread.join()
            else:
                thread.join(max(0.0, deadline - time.monotonic()))
        return not any(t.is_alive() for t in threads)

    @contextlib.contextmanager
    def SaveBlock():
        StateManager.BlockSaving()
        try:
            yield
        finally:
            StateManager.ReleaseSaving()

    def BlockSaving():
        StateManager.saveBlocked += 1

    def ReleaseSaving():
        StateManager.saveBlocked -= 1
        if StateManager.saveBlocked == 0:
            StateManager.SaveState()

    @staticmethod
    def _bracketPath(key: str) -> str:
        # "score.1.best_of" -> "root['score']['1']['best_of']" (the key form
        # DeepDiff's include_paths expects; also used for changedKeys).
        out = "root"
        for k in key.split("."):
            out += f"['{k}']"
        return out

    @staticmethod
    def _isExemptKey(prefix: str, final_key: str) -> bool:
        # Same self-delimiting bracket comparison as _matchHeldPrefix: an
        # exempt path matches itself and everything below it.
        for _dot, bracket in StateManager.heldExempt.get(prefix, ()):
            if final_key == bracket or final_key.startswith(bracket + "["):
                return True
        return False

    @staticmethod
    def _matchHeldPrefix(final_key: str):
        # The bracket form is self-delimiting, so a prefix can never match
        # into a sibling key ("...['1']" does not prefix "...['10']...").
        for prefix in StateManager.heldPrefixes:
            bracket = StateManager._bracketPath(prefix)
            if final_key == bracket or final_key.startswith(bracket + "["):
                # Exempt sub-paths publish through the normal queue.
                if StateManager._isExemptKey(prefix, final_key):
                    return None
                return prefix
        return None

    @staticmethod
    def _revertHeldSubtrees(snapshot):
        # Rewind every dirty held subtree inside `snapshot` (a clone of the
        # live state) to its last committed value so pending edits never
        # reach the broadcast/export path.
        for prefix in StateManager.heldPrefixes:
            if not StateManager.heldKeys.get(prefix):
                continue
            # Exempt sub-paths are published as they are written, so their
            # live values must survive the rewind of the enclosing subtree.
            carried = []
            for dot, _bracket in StateManager.heldExempt.get(prefix, ()):
                value = deep_get(snapshot, dot, _MISSING)
                if value is not _MISSING:
                    carried.append((dot, value))
            committed = deep_get(StateManager.lastSavedState, prefix)
            if committed is None:
                deep_unset(snapshot, prefix)
            else:
                deep_set(snapshot, prefix, deep_clone(committed))
            for dot, value in carried:
                deep_set(snapshot, dot, value)

    @staticmethod
    def _notifyHeldTouched():
        touched = list(StateManager._heldTouched)
        StateManager._heldTouched.clear()
        for prefix in touched:
            StateManager.signals.held_state_updated.emit(prefix)

    @staticmethod
    def _trackChangedKey(final_key: str):
        """Route a mutated key to the held buffer or the publish queue.

        Caller must hold StateManager.lock. Returns the held prefix when
        this write made it dirty (caller emits held_dirty_changed outside
        the lock), otherwise None.
        """
        held_prefix = StateManager._matchHeldPrefix(final_key)
        if held_prefix is None:
            StateManager.changedKeys.append(final_key)
            if StateManager.saveBlocked == 0:
                StateManager.SaveState()
            return None

        was_dirty = bool(StateManager.heldKeys.get(held_prefix))
        StateManager.heldKeys.setdefault(held_prefix, []).append(final_key)
        StateManager._heldTouched.add(held_prefix)
        if StateManager.saveBlocked == 0:
            # No publish needed for a held write; just notify live viewers.
            StateManager._notifyHeldTouched()
        return None if was_dirty else held_prefix

    def HoldSubtree(prefix: str, exempt=None):
        """Defer publication of every key under `prefix` until CommitHeld().

        Writes keep landing in the live state; only the broadcast/export
        boundary is gated. Registered once per scoreboard widget.

        `exempt` lists prefix-relative dot paths (e.g. "last_sets") that opt
        out of the hold: they are fetched derived data rather than operator
        edits, so they publish as soon as they are written and are never
        rewound by the pending-edit mask.
        """
        with StateManager.lock:
            StateManager.heldPrefixes.add(prefix)
            entries = [(f"{prefix}.{sub}",
                        StateManager._bracketPath(f"{prefix}.{sub}"))
                       for sub in (exempt or [])]
            if entries:
                StateManager.heldExempt[prefix] = entries
            else:
                StateManager.heldExempt.pop(prefix, None)

    def HasPendingChanges(prefix: str) -> bool:
        with StateManager.lock:
            return bool(StateManager.heldKeys.get(prefix))

    def CommitHeld(prefix: str):
        """Publish everything pending under a held prefix in one save cycle."""
        with StateManager.lock:
            had_pending = bool(StateManager.heldKeys.get(prefix))
            StateManager._commitPrefixes.add(prefix)
            StateManager._heldTouched.discard(prefix)
            if StateManager.saveBlocked == 0:
                StateManager.SaveState()
            # else: _commitPrefixes stays queued; the SaveState fired by the
            # enclosing ReleaseSaving() drains it.
        if had_pending:
            StateManager.signals.held_dirty_changed.emit(prefix, False)

    def SaveState():
        if StateManager.saveBlocked == 0:
            with StateManager.lock:
                # Wait for any previous export before computing new diff
                if StateManager._export_thread is not None and StateManager._export_thread.is_alive():
                    StateManager._export_thread.join()

                # Prefixes queued by CommitHeld(): their pending keys join
                # this publish cycle. Anything held for a committing prefix
                # at this moment is included (the user asked to apply).
                for prefix in list(StateManager._commitPrefixes):
                    StateManager.changedKeys.extend(
                        StateManager.heldKeys.pop(prefix, []))
                StateManager._commitPrefixes.clear()

                # Publish view: the live state with still-dirty held subtrees
                # reverted to their last committed value. When nothing is
                # held-dirty this is the live state itself (no clone cost).
                publish_state = StateManager.state
                if any(StateManager.heldKeys.get(p)
                       for p in StateManager.heldPrefixes):
                    publish_state = deep_clone(StateManager.state)
                    StateManager._revertHeldSubtrees(publish_state)

                diff = DeepDiff(
                    StateManager.lastSavedState,
                    publish_state,
                    exclude_types=[type(None)],
                    include_paths=list(set(StateManager.changedKeys)),
                    verbose_level=2, # Necessary to see values of added items.
                )
                delta = Delta(diff).to_flat_dicts()
                if len(delta) > 100:
                    StateManager._bumpRevision()
                    StateManager.signals.state_big_change.emit()
                elif len(delta) > 0:
                    try:
                        StateManager._bumpRevision()
                        StateManager.signals.state_updated.emit({
                            # `revision` is the canonical name (Design.md §3.2.3).
                            # `delta_index` is kept as a legacy alias for older
                            # OBS templates / not-yet-rebuilt clients.
                            'revision': StateManager.revision,
                            'delta_index': StateManager.revision,
                            'delta': Delta(diff).to_flat_dicts()
                        })
                    except TypeError:
                        logger.warning(f"Couldn't serialize diff. Changed Keys: {StateManager.changedKeys}")

                StateManager.changedKeys = []

                if len(diff) > 0:
                    # Snapshot state for thread-safe file I/O;
                    # update lastSavedState now so the next diff is correct.
                    # publish_state already is a private clone when held
                    # subtrees were masked above.
                    state_snapshot = (
                        publish_state
                        if publish_state is not StateManager.state
                        else deep_clone(StateManager.state))
                    old_state = StateManager.lastSavedState
                    StateManager.lastSavedState = state_snapshot

                    revision_at_save = StateManager.revision

                    def _export(state_snap, old_snap, ref_diff):
                        state_snap["timestamp"] = time.time()
                        # Stamp revision so degraded poll clients (file
                        # mode) can validate continuity (Design.md §2.3
                        # R-OBS-3a, §8 T-12).
                        state_snap["revision"] = revision_at_save
                        # Drop OPT_INDENT_2 — OBS overlays / Web UI parse
                        # JSON regardless of whitespace, and indenting a
                        # multi-MB tournament state inflates file size
                        # 3-4x for nothing. Saves disk I/O bandwidth.
                        _atomic_write_bytes(
                            "./out/program_state.json",
                            orjson.dumps(state_snap,
                                         option=orjson.OPT_NON_STR_KEYS))

                        if not SettingsManager.Get("general.disable_export", False):
                            StateManager.ExportText(
                                old_snap, ref_diff, state_snap)

                    # Deliberately NOT a daemon: this thread only does local
                    # disk I/O (bounded, never blocks on the network), and
                    # letting shutdown join it guarantees program_state.json
                    # is never left truncated. Image downloads spawned below
                    # by ExportText are the network side and are daemons.
                    StateManager._export_thread = threading.Thread(
                        target=partial(_export,
                                       state_snap=state_snapshot,
                                       old_snap=old_state,
                                       ref_diff=diff))
                    StateManager._export_thread.start()

                # Coalesced notification for held writes (one per prefix per
                # save cycle, mirroring how SaveBlock coalesces deltas).
                StateManager._notifyHeldTouched()

    def LoadState():
        try:
            with open("./out/program_state.json", 'rb') as file:
                StateManager.state = orjson.loads(file.read())
                StateManager.signals.state_big_change.emit()
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(traceback.format_exc())
            StateManager.state = {}
            StateManager.signals.state_big_change.emit()
            StateManager.SaveState()

    def Set(key: str, value):
        # import inspect
        # func = inspect.currentframe().f_back.f_code
        # fname = os.path.split(func.co_filename)[1]
        # logger.debug(f"{func.co_name}({fname}:{func.co_firstlineno}) Setting {key} to {value}")
        with StateManager.lock:
            # StateManager.lastSavedState = deep_clone(StateManager.state)

            deep_set(StateManager.state, key, value)

            final_key = StateManager._bracketPath(key)

            dirty_prefix = StateManager._trackChangedKey(final_key)

        if dirty_prefix:
            StateManager.signals.held_dirty_changed.emit(dirty_prefix, True)

    def Unset(key: str):
        # import inspect
        # func = inspect.currentframe().f_back.f_code
        # fname = os.path.split(func.co_filename)[1]
        # logger.debug(f"{func.co_name}({fname}:{func.co_firstlineno}) Deleting {key}")

        with StateManager.lock:
            # StateManager.lastSavedState = deep_clone(StateManager.state)
            deep_unset(StateManager.state, key)

            final_key = StateManager._bracketPath(key)

            dirty_prefix = StateManager._trackChangedKey(final_key)

        if dirty_prefix:
            StateManager.signals.held_dirty_changed.emit(dirty_prefix, True)

    def Get(key: str, default=None):
        return deep_get(StateManager.state, key, default)

    def GetFullState(include_pending=False):
        """Return a snapshot of the full state dict with the current revision.

        Thread-safe: acquires lock and returns a clone so callers cannot
        mutate the live state. Both `revision` (canonical, Design.md §3.2.3)
        and `delta_index` (legacy alias) are included so older clients keep
        working.

        By default the snapshot is the *published* view: held subtrees with
        pending (not yet applied) edits are reverted to their last committed
        value, matching what the delta stream broadcasts. Pass
        include_pending=True for in-process renderers (thumbnail / result)
        that should see what the operator currently has in the form.
        """
        with StateManager.lock:
            snapshot = deep_clone(StateManager.state)
            if not include_pending:
                StateManager._revertHeldSubtrees(snapshot)
            return {
                'state': snapshot,
                'revision': StateManager.revision,
                'delta_index': StateManager.revision,
            }

    def GetCommitted(key: str, default=None):
        """Get() variant that serves the published view of a held subtree.

        When `key` lies inside a held prefix with pending edits, the last
        committed value is returned instead of the live one. Keys outside
        (or above) any held prefix fall back to the live state.

        Exempt sub-paths (HoldSubtree(exempt=...)) publish immediately, so
        they always resolve to the live value — both when read directly and
        when they sit inside a wider subtree read.
        """
        with StateManager.lock:
            for prefix in StateManager.heldPrefixes:
                if not StateManager.heldKeys.get(prefix):
                    continue
                if key != prefix and not key.startswith(prefix + "."):
                    continue

                exempt = StateManager.heldExempt.get(prefix, ())
                if any(key == dot or key.startswith(dot + ".")
                       for dot, _bracket in exempt):
                    return deep_get(StateManager.state, key, default)

                committed = deep_get(
                    StateManager.lastSavedState, key, default)
                # Overlay the live value of every exempt path nested under
                # `key`. Clone first: the committed dict is lastSavedState's
                # own storage and must not be mutated here.
                nested = [dot for dot, _bracket in exempt
                          if dot.startswith(key + ".")]
                if nested and isinstance(committed, dict):
                    committed = deep_clone(committed)
                    for dot in nested:
                        live = deep_get(StateManager.state, dot, _MISSING)
                        if live is not _MISSING:
                            deep_set(committed, dot[len(key) + 1:], live)
                return committed
            return deep_get(StateManager.state, key, default)

    @staticmethod
    def _bumpRevision():
        StateManager.revision += 1

    def ExportText(oldState, diff, stateSnapshot):
        # logger.info("ExportState")
        # logger.info(diff)
        #
        # THREADING: this runs on the export thread (SaveState spawns it), so
        # every container walked below has to be private to that thread.
        # `oldState` and `stateSnapshot` are, but `diff` is NOT: DeepDiff was
        # handed the live state, so its `new_value` entries are references
        # *into* StateManager.state. Walking one while the GUI thread wrote
        # to the same subtree killed this thread with "dictionary changed
        # size during iteration". The traceback does reach ./logs (the
        # frozen build routes stderr there), but nothing tells the GUI: the
        # visible symptom is ./out silently missing that batch of files --
        # an OBS overlay frozen on stale values.
        # Values are therefore read out of the snapshot by path; the diff is
        # used for its structure only. `stateSnapshot` is mandatory for the
        # same reason -- falling back to StateManager.state here would put
        # the live tree back under this thread's walk.
        snapshot = stateSnapshot

        mergedDiffs = list(diff.get("values_changed", {}).items())
        mergedDiffs.extend(list(diff.get("type_changes", {}).items()))

        # logger.info(mergedDiffs)

        for changeKey, change in mergedDiffs:
            # Remove "root[" from start and separate keys
            filename = "/".join(changeKey[5:].replace(
                "'", "").replace("]", "").replace("/", "_").split("["))

            # logger.info(filename)

            if change.get("new_type") == type(None):
                StateManager.RemoveFilesDict(
                    filename, extract(oldState, changeKey))
            else:
                try:
                    newValue = extract(snapshot, changeKey)
                except Exception:
                    # The snapshot is taken under the same lock as the diff,
                    # so a changed path must be present in it; getting here
                    # means the two disagree and something upstream is wrong.
                    # Skip the key rather than reaching for the diff's own
                    # `new_value`: that is a live reference into
                    # StateManager.state, and walking it is exactly the crash
                    # this whole path exists to avoid. warning, not debug --
                    # the frozen build's file sink only keeps INFO and above.
                    logger.warning(
                        f"{changeKey} missing from the export snapshot; "
                        "skipping it")
                    continue
                StateManager.CreateFilesDict(filename, newValue)

        removedKeys = diff.get("dictionary_item_removed", {})

        for key in removedKeys:
            item = extract(oldState, key)

            # Remove "root[" from start and separate keys
            filename = "/".join(key[5:].replace(
                "'", "").replace("]", "").replace("/", "_").split("["))

            # logger.info("Removed:", filename, item)

            StateManager.RemoveFilesDict(filename, item)

        addedKeys = diff.get("dictionary_item_added", {})

        for key in addedKeys:
            try:
                item = extract(snapshot, key)

                # Remove "root[" from start and separate keys
                path = "/".join(key[5:].replace(
                    "'", "").replace("]", "").replace("/", "_").split("["))
                # Remove "root[" from start and separate keys
                path = "/".join(key[5:].replace(
                    "'", "").replace("]", "").replace("/", "_").split("["))

                # logger.info("Added:", path, item)
                # logger.info("Added:", path, item)

                StateManager.CreateFilesDict(path, item)
            except Exception as e:
                logger.error(traceback.format_exc())

    def _FileExtension(value):
        """Extension of a local path or URL, ignoring any query string.

        start.gg hands avatars out as
        `https://.../image-<hash>.jpg?ehk=<cache key>`, so splitting the raw
        string on "." yields "jpg?ehk=..." -- which is neither an image
        extension (the URL fell through to the plain-text branch and the
        overlay got a .txt holding the URL) nor a legal Windows filename.
        Everything the export layer derives from the extension goes through
        here so the query string can never reach a filename again.
        """
        return urlparse(value).path.rsplit(".", 1)[-1].lower()

    def CreateFilesDict(path, di):
        pathdirs = "/".join(path.split("/")[0:-1])

        if not os.path.isdir("./out/"+pathdirs):
            os.makedirs("./out/"+pathdirs)

        if type(di) == dict:
            for k, i in di.items():
                StateManager.CreateFilesDict(
                    path+"/"+str(k).replace("/", "_"), i)
        else:
            # logger.info("try to add: ", path)
            if type(di) == str and di.startswith("./"):
                if os.path.exists(f"./out/{path}" + "." + di.rsplit(".", 1)[-1]):
                    try:
                        os.remove(f"./out/{path}" + "." +
                                  di.rsplit(".", 1)[-1])
                    except Exception as e:
                        logger.error(traceback.format_exc())
                if os.path.exists(di):
                    try:
                        shutil.copyfile(
                            os.path.abspath(di),
                            f"./out/{path}" + "." + di.rsplit(".", 1)[-1])
                    except Exception as e:
                        logger.error(traceback.format_exc())
            elif type(di) == str and di.startswith("http") and StateManager._FileExtension(di) in ("png", "jpg"):
                imageExtension = StateManager._FileExtension(di)
                downloadTarget = f"./out/{path}.{imageExtension}"
                try:
                    if os.path.exists(downloadTarget):
                        try:
                            os.remove(downloadTarget)
                        except Exception as e:
                            logger.error(traceback.format_exc())

                    def downloadImage(url, dlpath):
                        StateManager._download_semaphore.acquire()
                        try:
                            # (connect, read): without a timeout an
                            # unresponsive host blocks this thread forever.
                            # The read timeout applies per chunk of the
                            # streamed body, so 30s matches the streaming
                            # download budget used in AutonaUpdater.
                            r = requests.get(url, stream=True,
                                             timeout=(5, 30))
                            if r.status_code == 200:
                                with open(dlpath, 'wb') as f:
                                    r.raw.decode_content = True
                                    shutil.copyfileobj(r.raw, f)
                                    f.flush()
                            # imageExtension, not url.endswith(".jpg"): a
                            # cache-keyed URL ends in the query string.
                            if imageExtension == "jpg":
                                original = Image.open(dlpath)
                                original.save(dlpath.rsplit(
                                    ".", 1)[0]+".png", format="png")
                                os.remove(dlpath)
                        except Exception as e:
                            logger.error(traceback.format_exc())
                        finally:
                            StateManager._download_semaphore.release()

                    # daemon: a half-written overlay image is re-downloaded
                    # on the next export, so it must never keep the process
                    # alive at shutdown (threading._shutdown() joins every
                    # non-daemon thread).
                    t = threading.Thread(
                        target=downloadImage,
                        args=[di, downloadTarget],
                        daemon=True
                    )
                    t.start()
                    StateManager._TrackDownload(t)
                except Exception as e:
                    logger.error(traceback.format_exc())
            else:
                with open(f"./out/{path}.txt", 'w', encoding='utf-8') as file:
                    file.write(str(di))

    def RemoveFilesDict(path, di):
        pathdirs = "/".join(path.split("/")[0:-1])

        if type(di) == dict:
            for k, i in di.items():
                StateManager.RemoveFilesDict(
                    path+"/"+str(k).replace("/", "_"), i)
        else:
            if type(di) == str and (di.startswith("./") or di.startswith("http")):
                try:
                    if di.startswith("http"):
                        # The same derivation CreateFilesDict named the
                        # download with -- and a downloaded .jpg is stored
                        # converted, so what exists on disk is the .png twin.
                        extension = StateManager._FileExtension(di)
                        removeFiles = [f"./out/{path}.{extension}"]
                        if extension == "jpg":
                            removeFiles.append(f"./out/{path}.png")
                    else:
                        # Local files are copied under their own extension,
                        # original case included.
                        removeFiles = [f"./out/{path}."
                                       + di.rsplit(".", 1)[-1]]
                    for removeFile in removeFiles:
                        # logger.info("try to remove: ", removeFile)
                        if os.path.exists(removeFile):
                            os.remove(removeFile)
                except:
                    logger.error(traceback.format_exc())
            else:
                try:
                    removeFile = f"./out/{path}.txt"
                    # logger.info("try to remove: ", removeFile)
                    if os.path.exists(removeFile):
                        os.remove(removeFile)
                except:
                    logger.error(traceback.format_exc())

        try:
            # logger.info("Remove path", f"./out/{path}")
            if os.path.exists(f"./out/{path}"):
                shutil.rmtree(f"./out/{path}")
        except:
            logger.error(traceback.format_exc())
