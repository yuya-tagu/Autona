"""The files and folders the operator picked in the desktop's native
dialogs for plugins (ADR-009 D4; lane L10): the shell requests pickFile and
pickFolder (``purpose=plugin``), held here under an id.

The page never sees a path: the shell (AutonaShellOperations) answers the
page ``{id, name}`` (routes_v1.json pick_answer) and the page sends the id
back in a body -- a file / folder setting, a run's params, a hook's params,
a dev folder to add. ``Resolve(fieldType, pickId)`` turns it into the
absolute path again: it is the ``resolvePick`` PluginSettings, PluginHooks
and the plugin API take (PluginSettings.PickResolver). A pick of a file
never resolves for a folder field, nor the other way round.

How long, how many
------------------
A pick is held for PICK_EXPIRY_SECS from when it was made (read off the
injected monotonic clock when it is looked at) and resolves as often as it
is asked for within that time: a settings save and a run may both name it,
and a request retried after a failure names it again. It only names a path
the operator chose, so holding it longer costs nothing but memory; at most
MAX_PICKS are held, the oldest going first. Nothing is written: a pick goes
with the process.

A held path is an absolute path on a drive of this PC
(PluginSettings.IsAbsoluteLocal: no UNC). Whether it is still there, and a
file of the field's extensions, is the caller's to check when it is used
(PluginSettings.CheckFieldValue).

Threads: one lock around the table (the shell holds on the GUI thread, the
routes resolve on theirs). Nothing is made, read or written on import
(pitfalls #1); no Qt.
"""

import ntpath
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional, Tuple

from .PluginSettings import IsAbsoluteLocal
from .PluginTypes import FieldType

# A pick lives this long (a plugin's form may take a while to fill).
PICK_EXPIRY_SECS = 30 * 60
# At most this many are held; a newer one pushes out the oldest.
MAX_PICKS = 32

# The kinds a pick is of: a FieldType value.
KINDS = (FieldType.FILE.value, FieldType.FOLDER.value)

# pickFolder's argument ``purpose``: a game folder to import (the shell has
# AssetPackService probe and hold it) or a plugin's folder (a folder field, a
# dev folder to add: held here).
FOLDER_PURPOSES = ("assets", "plugin")

# pickFile's argument ``extensions``: a file field's extensions
# (FILE_EXTENSION_PATTERN each: lower case, no dot) joined by commas, at most
# FILE_EXTENSIONS_MAX of them -- so the request stays within what the shared
# view reads of a shell request's query.
FILE_EXTENSIONS_MAX = 20
EXTENSIONS_ARGUMENT_PATTERN = (
    r"[a-z0-9]{1,10}(?:,[a-z0-9]{1,10}){0,%d}" % (FILE_EXTENSIONS_MAX - 1))
_EXTENSIONS_ARGUMENT = re.compile(EXTENSIONS_ARGUMENT_PATTERN)


def ParseExtensions(text) -> Optional[Tuple[str, ...]]:
    """pickFile's ``extensions`` argument ("png,jpg") as its extensions, in
    order, each once; None when it is not one (EXTENSIONS_ARGUMENT_PATTERN:
    no dot, no wildcard, no upper case, no blank)."""
    if not isinstance(text, str) or not _EXTENSIONS_ARGUMENT.fullmatch(text):
        return None
    return tuple(dict.fromkeys(text.split(",")))


class ShellPicks:
    """See the module docstring. One per process (bootstrap: core makes it,
    the shell holds, the plugin routes resolve)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 newId: Callable[[], str] = lambda: secrets.token_urlsafe(16)):
        self._clock = clock
        self._newId = newId
        self._lock = threading.Lock()
        # id -> (kind, absolute path, expires at), oldest first.
        self._held = OrderedDict()

    def Hold(self, kind: str, path: str) -> str:
        """Hold ``path`` (the operator's pick, of ``kind``: "file" /
        "folder") and answer its id. ValueError for another kind, or a path
        that is not an absolute local one (a UNC path, a relative one)."""
        if kind not in KINDS:
            raise ValueError("not a kind of pick")
        if not IsAbsoluteLocal(path):
            raise ValueError("not an absolute local path")
        path = os.path.normpath(path)
        with self._lock:
            self._Expire()
            pickId = self._newId()
            self._held.pop(pickId, None)
            self._held[pickId] = (kind, path,
                                  self._clock() + PICK_EXPIRY_SECS)
            while len(self._held) > MAX_PICKS:
                self._held.popitem(last=False)
        return pickId

    def Resolve(self, fieldType: str, pickId: str) -> Optional[str]:
        """The absolute path pick ``pickId`` names, when it is a pick of
        ``fieldType`` ("file" / "folder") still held; else None (never
        picked, expired, pushed out, or the other kind)."""
        if not isinstance(pickId, str):
            return None
        with self._lock:
            self._Expire()
            held = self._held.get(pickId)
        if held is None or held[0] != str(fieldType):
            return None
        return held[1]

    def Count(self) -> int:
        """How many picks are held now."""
        with self._lock:
            self._Expire()
            return len(self._held)

    def _Expire(self):
        # Under the lock. Expiry follows the order they were held in.
        now = self._clock()
        while self._held:
            pickId, (_kind, _path, expiresAt) = next(iter(self._held.items()))
            if expiresAt > now:
                break
            del self._held[pickId]


def PickName(path: str) -> str:
    """What the page shows of a picked path: its last part (the file's or
    the folder's name), or the drive (``C:``) for a drive's root. Never
    more of the path."""
    path = os.path.normpath(path)
    name = ntpath.basename(path.rstrip("\\/"))
    if name:
        return name
    return ntpath.splitdrive(path)[0] or os.path.basename(path.rstrip("/"))
