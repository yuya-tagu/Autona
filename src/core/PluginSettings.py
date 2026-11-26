"""A plugin's non-secret settings (ADR-009 B.2 / B.3; lane L3):
``user_data/plugins/<id>/config.json`` (``user_data/plugins/_dev/<id>/``
for a dev folder's plugin, which shares nothing with an installed one).

What is kept
------------
config.json is one JSON object, setting key -> value (UTF-8, no BOM; read
as utf-8-sig), written whole through a temporary file. Only values that
fit their field are kept; a key the manifest no longer has is left as it
is (a dev manifest edited back keeps its values). A secret is never here:
it is PluginSecrets' (``secrets.bin``, DPAPI).

``Get`` answers every non-secret setting key of the manifest -- the saved
value when it still fits the field (the manifest may have changed since),
else the field's default, else None: request.json's ``settings``. An empty
text is "not entered": its default.

``Save`` is a partial update (the PATCH route's body): a key not in
``values`` keeps its saved value, None (or an empty text) clears it back to
its default. A file / folder value arrives as a pick id (pickFile /
pickFolder): ``resolvePick(fieldType, pickId)`` -- the host's -- turns it
into the absolute path, which is checked (absolute, a drive, no UNC, a file
of one of the field's extensions / a folder, there now) and stored. Every
value is checked first and nothing is written when one does not fit
(INVALID_REQUEST, naming the key, never the value).

``Missing`` is the required settings not set: config.json's (``Get``'s
None, or an empty text) and the secrets' (``PluginSecrets.Keys``)
together, in the manifest's order.

``CheckFieldValue`` is the one type check of a value field (settings here;
a job's params and a hook's saved values are the same rule).

Threads: one lock around each read-modify-write. Nothing is made, read or
written on import (pitfalls #1); no Qt.
"""

import json
import math
import ntpath
import os
import re
import sys
import threading
from typing import Any, Callable, Mapping, Optional, Tuple, Union

from loguru import logger

from . import ImageImport
from .PluginTypes import (FIELD_KEY_PATTERN, PLUGIN_ID_PATTERN,
                          TEXT_MAX_LENGTH, Field, FieldType, Manifest,
                          Refused, StartRefusal)

PLUGINS_DIR = "plugins"
# A dev folder's plugin keeps its state apart (ADR-009 D4).
DEV_DIR = "_dev"
CONFIG_FILE = "config.json"

_PLUGIN_ID = re.compile(PLUGIN_ID_PATTERN)
_FIELD_KEY = re.compile(FIELD_KEY_PATTERN)

_TEXT_TYPES = (FieldType.TEXT, FieldType.TEXTAREA)
_PATH_TYPES = (FieldType.FILE, FieldType.FOLDER)

# A pick id -> the absolute path it names, None when unknown.
PickResolver = Callable[[str, str], Optional[str]]


def UserDir(userDataDir: str, pluginId: str, *, dev: bool) -> str:
    """``<user_data>/plugins/<id>`` (``plugins/_dev/<id>`` when ``dev``).
    ValueError for an id that is not a plugin id (it names a folder)."""
    if not isinstance(pluginId, str) or not _PLUGIN_ID.fullmatch(pluginId):
        raise ValueError("not a plugin id")
    parts = [userDataDir, PLUGINS_DIR] + ([DEV_DIR] if dev else [])
    return os.path.join(*parts, pluginId)


def _MaxLength(field: Field) -> int:
    default = TEXT_MAX_LENGTH["textarea_default"
                              if field.type == FieldType.TEXTAREA
                              else "text_default"]
    limit = field.max_length if field.max_length is not None else default
    return min(int(limit), TEXT_MAX_LENGTH["cap"])


def IsAbsoluteLocal(path: str) -> bool:
    """An absolute path on a drive of this PC: no UNC (``\\\\server``,
    ``//server``, ``\\\\?\\``), on Windows a drive letter."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    if path.startswith(("\\\\", "//")):
        return False
    if sys.platform == "win32":
        drive = ntpath.splitdrive(path)[0]
        return len(drive) == 2 and drive[1] == ":" and ntpath.isabs(path)
    return os.path.isabs(path)


def _Extension(path: str) -> str:
    return os.path.splitext(path)[1][1:].lower()


def _Number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _OnStep(field: Field, value) -> bool:
    if field.step is None or field.step <= 0:
        return True
    base = field.min if field.min is not None else 0
    ratio = (value - base) / field.step
    return abs(ratio - round(ratio)) <= 1e-9 * max(1.0, abs(ratio))


def CheckFieldValue(field: Field, value: Any, *,
                    resolvePick: Optional[PickResolver] = None,
                    stored: bool = False) -> Tuple[bool, Any]:
    """(True, the value to keep) or (False, why: a short reason without the
    value). ``value`` is not None. A file / folder value is a pick id
    resolved by ``resolvePick`` -- or, ``stored``, the path kept before
    (checked again without looking at the disk). A secret or a source type
    is never a value here."""
    kind = field.type
    if kind in _TEXT_TYPES:
        if not isinstance(value, str):
            return False, "not a string"
        if len(value) > _MaxLength(field):
            return False, "too long"
        return True, value
    if kind == FieldType.NUMBER:
        if not _Number(value):
            return False, "not a number"
        if field.min is not None and value < field.min:
            return False, "below min"
        if field.max is not None and value > field.max:
            return False, "above max"
        if not _OnStep(field, value):
            return False, "not on step"
        return True, value
    if kind == FieldType.BOOL:
        if not isinstance(value, bool):
            return False, "not a boolean"
        return True, value
    if kind == FieldType.SELECT:
        if not isinstance(value, str) or value not in {
                option.value for option in field.options}:
            return False, "not an option"
        return True, value
    if kind in _PATH_TYPES:
        if not isinstance(value, str) or not value:
            return False, "not a pick id"
        if stored:
            path = value
        else:
            if resolvePick is None:
                return False, "no picker"
            path = resolvePick(str(kind), value)
            if path is None:
                return False, "unknown pick id"
        if not IsAbsoluteLocal(path):
            return False, "not an absolute local path"
        path = os.path.normpath(path)
        if kind == FieldType.FILE:
            if _Extension(path) not in field.extensions:
                return False, "extension not allowed"
            if not stored and not os.path.isfile(path):
                return False, "not a file"
        elif not stored and not os.path.isdir(path):
            return False, "not a folder"
        return True, path
    if kind == FieldType.SECRET:
        return False, "a secret"
    return False, "not a value type"


def _Empty(field: Field, value) -> bool:
    return value is None or (field.type in _TEXT_TYPES and value == "")


class PluginSettings:
    """``userDataDir``: the user_data folder (made absolute now).
    ``secrets``: PluginSecrets (its ``Keys`` for Missing). ``resolvePick``:
    the host's pick ids -> absolute paths (pickFile / pickFolder)."""

    def __init__(self, userDataDir: str, secrets,
                 resolvePick: Optional[PickResolver] = None):
        self.userDataDir = os.path.abspath(userDataDir)
        self._secrets = secrets
        self._resolvePick = resolvePick
        self._lock = threading.Lock()

    # ── the file ────────────────────────────────────────────────────────

    def ConfigPath(self, pluginId: str, *, dev: bool) -> str:
        return os.path.join(UserDir(self.userDataDir, pluginId, dev=dev),
                            CONFIG_FILE)

    def _Read(self, path: str) -> dict:
        try:
            with open(path, encoding="utf-8-sig") as file:
                data = json.load(file)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            logger.warning(f"Plugin settings: {os.path.basename(path)} could "
                           f"not be read ({type(error).__name__})")
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _Values(manifest: Manifest):
        return [field for field in manifest.settings
                if field.type != FieldType.SECRET]

    # ── the interface ───────────────────────────────────────────────────

    def Get(self, pluginId: str, manifest: Manifest, *,
            dev: bool) -> Mapping[str, Any]:
        """Every non-secret setting key -> its saved value (when it still
        fits), else its default, else None."""
        with self._lock:
            saved = self._Read(self.ConfigPath(pluginId, dev=dev))
        return self._Resolve(manifest, saved)

    def _Resolve(self, manifest: Manifest, saved: Mapping) -> dict:
        answer = {}
        for field in self._Values(manifest):
            value = saved.get(field.key)
            if not _Empty(field, value):
                ok, value = CheckFieldValue(field, value, stored=True)
                if not ok:
                    value = None
            if _Empty(field, value):
                value = field.default
            answer[field.key] = value
        return answer

    def Save(self, pluginId: str, manifest: Manifest,
             values: Mapping[str, Any], *,
             dev: bool) -> Union[Mapping[str, Any], Refused]:
        """Check ``values`` (a partial update) against the manifest's
        non-secret settings and save them; INVALID_REQUEST when one does not
        fit or names no such setting. Returns what Get returns afterwards."""
        if not isinstance(values, Mapping):
            return Refused(StartRefusal.INVALID_REQUEST,
                           detail="settings: not an object")
        fields = {field.key: field for field in manifest.settings}
        checked = {}
        for key, value in values.items():
            field = fields.get(key) if isinstance(key, str) else None
            if field is None or not _FIELD_KEY.fullmatch(key):
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail=f"{key}: no such setting")
            if field.type == FieldType.SECRET:
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail=f"{key}: a secret is saved apart")
            if _Empty(field, value):
                checked[key] = None
                continue
            ok, kept = CheckFieldValue(field, value,
                                       resolvePick=self._resolvePick)
            if not ok:
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail=f"{key}: {kept}")
            checked[key] = kept
        path = self.ConfigPath(pluginId, dev=dev)
        with self._lock:
            saved = self._Read(path)
            for key, value in checked.items():
                if value is None:
                    saved.pop(key, None)
                else:
                    saved[key] = value
            data = json.dumps(saved, ensure_ascii=False, indent=2)
            ImageImport.WriteFile(path, data.encode("utf-8"))
        logger.info(f"Plugin settings: {pluginId} saved")
        return self._Resolve(manifest, saved)

    def Missing(self, pluginId: str, manifest: Manifest, *,
                dev: bool) -> Tuple[str, ...]:
        """The keys of required settings not set: config.json's and the
        secrets', in the manifest's order."""
        values = self.Get(pluginId, manifest, dev=dev)
        secretKeys = None
        missing = []
        for field in manifest.settings:
            if not field.required:
                continue
            if field.type == FieldType.SECRET:
                if secretKeys is None:
                    secretKeys = self._secrets.Keys(pluginId, dev=dev)
                if field.key not in secretKeys:
                    missing.append(field.key)
            elif _Empty(field, values.get(field.key)):
                missing.append(field.key)
        return tuple(missing)
