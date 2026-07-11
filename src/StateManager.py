import contextlib
import os
import sys

import orjson
import traceback

# deepdiff (deephash.py) eagerly runs `try: import pandas` purely to support
# hashing pandas Series/DataFrame objects. Autona only diffs nested dicts of
# primitives, so that ~1.4s pandas import is pure startup dead weight. Pinning
# the sys.modules entry to None makes deepdiff's import fall through to its
# no-pandas path. setdefault() never clobbers a real pandas already imported
# elsewhere, so this is a no-op if anything legitimately needs pandas first.
# StateManager is the only deepdiff importer in src/, so this guard reliably
# runs before deephash loads. (numpy is left alone — its import is cheap.)
sys.modules.setdefault("pandas", None)

from deepdiff.helper import DELTA_VIEW
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
    _commitPrefixes = set()   # prefixes queued to flush on the next SaveState
    _heldTouched = set()      # prefixes with held writes not yet notified
    # Monotonically increasing counter incremented on every state mutation
    # broadcast (delta or big change). Clients use it to detect dropped or
    # out-of-order events and request a full snapshot when discontinuous
    # (Design.md §3.2.3 R-Sync-5..9 / R-OBS-3a..b).
    revision = 0
    # Legacy alias kept for any in-process code that still references the
    # old name. Both attributes always carry the same value.
    deltaIndex = 0

    lock = threading.RLock()
    _export_thread = None
    loop = None
    _download_semaphore = threading.Semaphore(3)

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
    def _matchHeldPrefix(final_key: str):
        # The bracket form is self-delimiting, so a prefix can never match
        # into a sibling key ("...['1']" does not prefix "...['10']...").
        for prefix in StateManager.heldPrefixes:
            bracket = StateManager._bracketPath(prefix)
            if final_key == bracket or final_key.startswith(bracket + "["):
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
            committed = deep_get(StateManager.lastSavedState, prefix)
            if committed is None:
                deep_unset(snapshot, prefix)
            else:
                deep_set(snapshot, prefix, deep_clone(committed))

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

    def HoldSubtree(prefix: str):
        """Defer publication of every key under `prefix` until CommitHeld().

        Writes keep landing in the live state; only the broadcast/export
        boundary is gated. Registered once per scoreboard widget.
        """
        with StateManager.lock:
            StateManager.heldPrefixes.add(prefix)

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
                        with open("./out/program_state.json", 'wb', buffering=8192) as file:
                            state_snap["timestamp"] = time.time()
                            # Stamp revision so degraded poll clients (file
                            # mode) can validate continuity (Design.md §2.3
                            # R-OBS-3a, §8 T-12).
                            state_snap["revision"] = revision_at_save
                            # Drop OPT_INDENT_2 — OBS overlays / Web UI parse
                            # JSON regardless of whitespace, and indenting a
                            # multi-MB tournament state inflates file size
                            # 3-4x for nothing. Saves disk I/O bandwidth.
                            file.write(orjson.dumps(
                                state_snap, option=orjson.OPT_NON_STR_KEYS))

                        if not SettingsManager.Get("general.disable_export", False):
                            StateManager.ExportText(
                                old_snap, ref_diff, state_snap)

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
        """
        with StateManager.lock:
            for prefix in StateManager.heldPrefixes:
                if not StateManager.heldKeys.get(prefix):
                    continue
                if key == prefix or key.startswith(prefix + "."):
                    return deep_get(
                        StateManager.lastSavedState, key, default)
            return deep_get(StateManager.state, key, default)

    @staticmethod
    def _bumpRevision():
        # Keep `deltaIndex` legacy attr in sync with `revision`.
        StateManager.revision += 1
        StateManager.deltaIndex = StateManager.revision

    def ExportText(oldState, diff, stateSnapshot=None):
        # logger.info("ExportState")
        # logger.info(diff)

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
                StateManager.CreateFilesDict(
                    filename, change.get("new_value"))

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
                item = extract(stateSnapshot if stateSnapshot is not None else StateManager.state, key)

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
            elif type(di) == str and di.startswith("http") and (di.endswith(".png") or di.endswith(".jpg")):
                try:
                    if os.path.exists(f"./out/{path}" + "." + di.rsplit(".", 1)[-1]):
                        try:
                            os.remove(f"./out/{path}" +
                                      "." + di.rsplit(".", 1)[-1])
                        except Exception as e:
                            logger.error(traceback.format_exc())

                    def downloadImage(url, dlpath):
                        StateManager._download_semaphore.acquire()
                        try:
                            r = requests.get(url, stream=True)
                            if r.status_code == 200:
                                with open(dlpath, 'wb') as f:
                                    r.raw.decode_content = True
                                    shutil.copyfileobj(r.raw, f)
                                    f.flush()
                            if url.endswith(".jpg"):
                                original = Image.open(dlpath)
                                original.save(dlpath.rsplit(
                                    ".", 1)[0]+".png", format="png")
                                os.remove(dlpath)
                        except Exception as e:
                            logger.error(traceback.format_exc())
                        finally:
                            StateManager._download_semaphore.release()

                    t = threading.Thread(
                        target=downloadImage,
                        args=[
                            di,
                            f"./out/{path}" + "." + di.rsplit(".", 1)[-1]
                        ]
                    )
                    t.start()
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
                    removeFile = f"./out/{path}" + \
                        "." + di.rsplit(".", 1)[-1]
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


if not os.path.exists("./out"):
    os.makedirs("./out/")

if not os.path.isfile("./out/program_state.json"):
    StateManager.SaveState()

StateManager.LoadState()
