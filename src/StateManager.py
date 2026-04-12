import contextlib
import os

import orjson
import traceback

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

class StateManager:
    lastSavedState = {}
    state = {}
    saveBlocked = 0
    signals = StateManagerSignals()
    changedKeys = []
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
        logger.debug(
            "Initial Block - Current Blocking Status: " + str(StateManager.saveBlocked))

    def ReleaseSaving():
        StateManager.saveBlocked -= 1
        logger.debug(
            "Release Block - Current Blocking Status: " + str(StateManager.saveBlocked))
        if StateManager.saveBlocked == 0:
            StateManager.SaveState()

    def SaveState():
        if StateManager.saveBlocked == 0:
            with StateManager.lock:
                # Wait for any previous export before computing new diff
                if StateManager._export_thread is not None and StateManager._export_thread.is_alive():
                    StateManager._export_thread.join()

                diff = DeepDiff(
                    StateManager.lastSavedState,
                    StateManager.state,
                    exclude_types=[type(None)],
                    include_paths=list(set(StateManager.changedKeys)),
                    verbose_level=2, # Necessary to see values of added items.
                )
                delta = Delta(diff).to_flat_dicts()
                if len(delta) > 100:
                    StateManager.deltaIndex += 1
                    StateManager.signals.state_big_change.emit()
                elif len(delta) > 0:
                    try:
                        StateManager.deltaIndex += 1
                        StateManager.signals.state_updated.emit({
                            'delta_index': StateManager.deltaIndex,
                            'delta': Delta(diff).to_flat_dicts()
                        })
                    except TypeError:
                        logger.warning(f"Couldn't serialize diff. Changed Keys: {StateManager.changedKeys}")

                StateManager.changedKeys = []

                if len(diff) > 0:
                    # Snapshot state for thread-safe file I/O;
                    # update lastSavedState now so the next diff is correct
                    state_snapshot = deep_clone(StateManager.state)
                    old_state = StateManager.lastSavedState
                    StateManager.lastSavedState = state_snapshot

                    def _export(state_snap, old_snap, ref_diff):
                        with open("./out/program_state.json", 'wb', buffering=8192) as file:
                            state_snap["timestamp"] = time.time()
                            file.write(orjson.dumps(
                                state_snap, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_INDENT_2))

                        if not SettingsManager.Get("general.disable_export", False):
                            StateManager.ExportText(
                                old_snap, ref_diff, state_snap)

                    StateManager._export_thread = threading.Thread(
                        target=partial(_export,
                                       state_snap=state_snapshot,
                                       old_snap=old_state,
                                       ref_diff=diff))
                    StateManager._export_thread.start()

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

            final_key = "root"
            for k in key.split("."):
                final_key += f"['{k}']"

            StateManager.changedKeys.append(final_key)

            if StateManager.saveBlocked == 0:
                StateManager.SaveState()
                # StateManager.ExportText(oldState)

    def Unset(key: str):
        # import inspect
        # func = inspect.currentframe().f_back.f_code
        # fname = os.path.split(func.co_filename)[1]
        # logger.debug(f"{func.co_name}({fname}:{func.co_firstlineno}) Deleting {key}")

        with StateManager.lock:
            # StateManager.lastSavedState = deep_clone(StateManager.state)
            deep_unset(StateManager.state, key)

            final_key = "root"
            for k in key.split("."):
                final_key += f"['{k}']"
            StateManager.changedKeys.append(final_key)

            if StateManager.saveBlocked == 0:
                StateManager.SaveState()
                # StateManager.ExportText(oldState)

    def Get(key: str, default=None):
        return deep_get(StateManager.state, key, default)

    def GetFullState():
        """Return a snapshot of the full state dict with the current delta index.

        Thread-safe: acquires lock and returns a clone so callers cannot
        mutate the live state.
        """
        with StateManager.lock:
            return {
                'state': deep_clone(StateManager.state),
                'delta_index': StateManager.deltaIndex
            }

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
