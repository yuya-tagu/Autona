"""A plugin's ``secret`` settings (ADR-009 B.3, D6; lane L3).

Kept in ``user_data/plugins/<id>/secrets.bin`` (``plugins/_dev/<id>/`` for
a dev folder's plugin): one DPAPI blob (CryptProtectData, CurrentUser, no
UI, a fixed entropy) of the UTF-8 JSON object key -> value, written whole
through a temporary file. It keeps the values off the disk in plain text;
it does not keep them from another plugin: any process of the same user can
decrypt it (D6). Another user or another PC cannot -- a copied folder reads
as no secret (logged), and the user enters them again.

A value is a string with no line break and no other control character
(NUL, ``\\r`` / ``\\n``, tab, C1: Unicode category Cc), 1 to
SECRET_MAX_LENGTH characters. It leaves this module only through
``Environment`` (``AUTONA_SECRET_<KEY in upper case>`` -> value, for
PluginRunner.BuildEnvironment); the page sees ``Keys`` (set / not set).
Never in a log line, an exception's text, request.json or job.json: the
errors here name the key at most.

DPAPI is Windows' (ctypes on crypt32: no new dependency). The cipher can be
given (``protect`` / ``unprotect``: bytes -> bytes) -- a test's on another
platform; there the default raises OSError on use, never on import.

Threads: one lock around each read-modify-write. Nothing is made, read or
written on import (pitfalls #1); no Qt.
"""

import json
import os
import re
import sys
import threading
import unicodedata
from typing import Callable, Optional

from loguru import logger

from . import ImageImport
from .PluginSettings import UserDir
from .PluginTypes import ENV_SECRET_PREFIX, FIELD_KEY_PATTERN, \
    SECRET_MAX_LENGTH

SECRETS_FILE = "secrets.bin"

# Not a secret: it only keeps a blob of another program's DPAPI use from
# reading as ours.
_ENTROPY = b"Autona.PluginSecrets.1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

_FIELD_KEY = re.compile(FIELD_KEY_PATTERN)


class SecretValueError(ValueError):
    """A key or a value that does not fit. Its text never holds the
    value."""


def _Dpapi(data: bytes, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData (CurrentUser). OSError when it
    fails or off Windows."""
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    import ctypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32),
                    ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptProtectData if protect \
        else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
                         ctypes.POINTER(Blob), ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_uint32,
                         ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    def blob(raw):
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(
            buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

    blobIn, bufferIn = blob(data)
    entropy, _entropyBuffer = blob(_ENTROPY)
    blobOut = Blob()
    try:
        ok = function(ctypes.byref(blobIn), None, ctypes.byref(entropy),
                      None, None, _CRYPTPROTECT_UI_FORBIDDEN,
                      ctypes.byref(blobOut))
    finally:
        # Clears this buffer's copy only: the bytes / str the caller holds
        # stay in memory until collected (Python cannot wipe them).
        ctypes.memset(bufferIn, 0, len(data))
    if not ok:
        code = ctypes.get_last_error()
        raise OSError(code, "DPAPI " + ("protect" if protect
                                        else "unprotect") + " failed")
    try:
        return ctypes.string_at(blobOut.pbData, blobOut.cbData)
    finally:
        if not protect:
            ctypes.memset(blobOut.pbData, 0, blobOut.cbData)
        kernel32.LocalFree(blobOut.pbData)


def Protect(data: bytes) -> bytes:
    return _Dpapi(data, True)


def Unprotect(data: bytes) -> bytes:
    return _Dpapi(data, False)


def EnvironmentName(key: str) -> str:
    """``AUTONA_SECRET_<KEY in upper case>``."""
    return ENV_SECRET_PREFIX + key.upper()


def CheckKey(key) -> str:
    if not isinstance(key, str) or not _FIELD_KEY.fullmatch(key):
        raise SecretValueError("not a setting key")
    return key


def CheckValue(value) -> str:
    """The B.3 rule: a string, no line break or other control character,
    1..SECRET_MAX_LENGTH."""
    if not isinstance(value, str):
        raise SecretValueError("a secret is a string")
    if not value:
        raise SecretValueError("a secret is not empty (clear it instead)")
    if len(value) > SECRET_MAX_LENGTH:
        raise SecretValueError(
            f"a secret is at most {SECRET_MAX_LENGTH} characters")
    if any(unicodedata.category(mark) == "Cc" for mark in value):
        raise SecretValueError(
            "a secret has no line break or control character")
    return value


class PluginSecrets:
    """``userDataDir``: the user_data folder (made absolute now).
    ``protect`` / ``unprotect``: the cipher (DPAPI by default)."""

    def __init__(self, userDataDir: str,
                 protect: Optional[Callable[[bytes], bytes]] = None,
                 unprotect: Optional[Callable[[bytes], bytes]] = None):
        self.userDataDir = os.path.abspath(userDataDir)
        self._protect = protect or Protect
        self._unprotect = unprotect or Unprotect
        self._lock = threading.Lock()
        # (id, dev) whose file could not be decrypted, warned once each.
        self._warned = set()

    def Path(self, pluginId: str, *, dev: bool) -> str:
        return os.path.join(UserDir(self.userDataDir, pluginId, dev=dev),
                            SECRETS_FILE)

    # ── the file (under the lock) ───────────────────────────────────────

    def _Read(self, pluginId: str, dev: bool, *,
              strict: bool = False) -> dict:
        """The secrets; {} when there is no file or it cannot be decrypted
        (another user's or PC's, damaged: as if none were set). A file that
        is there but cannot be read now (OSError: locked, a sharing
        violation) is {} for a read and an OSError when ``strict`` (a
        change: writing over it would drop the other keys)."""
        path = self.Path(pluginId, dev=dev)
        try:
            with open(path, "rb") as file:
                blob = file.read()
        except FileNotFoundError:
            return {}
        except OSError as error:
            if strict:
                raise
            logger.warning(f"Plugin secrets: {pluginId}'s could not be read "
                           f"({type(error).__name__})")
            return {}
        try:
            data = json.loads(self._unprotect(blob).decode("utf-8"))
        except Exception as error:
            if (pluginId, dev) not in self._warned:
                self._warned.add((pluginId, dev))
                logger.warning(f"Plugin secrets: {pluginId}'s could not be "
                               f"decrypted ({type(error).__name__})")
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items()
                if isinstance(key, str) and _FIELD_KEY.fullmatch(key)
                and isinstance(value, str) and value}

    def _Write(self, pluginId: str, dev: bool, secrets: dict) -> None:
        path = self.Path(pluginId, dev=dev)
        if not secrets:
            self._Remove(path)
        else:
            plain = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
            ImageImport.WriteFile(path, self._protect(plain))
        # A file written anew is warned about again if it ever fails.
        self._warned.discard((pluginId, dev))

    @staticmethod
    def _Remove(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    # ── the interface ───────────────────────────────────────────────────

    def Set(self, pluginId: str, key: str, value: str, *,
            dev: bool) -> None:
        """Save one secret. SecretValueError (a ValueError) when the key or
        the value does not fit; OSError, nothing written, when secrets.bin
        is there but cannot be read now, or cannot be written."""
        CheckKey(key)
        CheckValue(value)
        with self._lock:
            secrets = self._Read(pluginId, dev, strict=True)
            secrets[key] = value
            self._Write(pluginId, dev, secrets)
        logger.info(f"Plugin secrets: {pluginId}'s {key} set")

    def Delete(self, pluginId: str, key: str, *, dev: bool) -> None:
        """Clear one secret (nothing when it is not set). OSError, nothing
        written, as Set."""
        CheckKey(key)
        with self._lock:
            secrets = self._Read(pluginId, dev, strict=True)
            if secrets.pop(key, None) is None:
                return
            self._Write(pluginId, dev, secrets)
        logger.info(f"Plugin secrets: {pluginId}'s {key} cleared")

    def Keys(self, pluginId: str, *, dev: bool) -> frozenset:
        """The keys that are set."""
        with self._lock:
            return frozenset(self._Read(pluginId, dev))

    def Environment(self, pluginId: str, *, dev: bool) -> dict:
        """AUTONA_SECRET_<KEY> -> value, for BuildEnvironment: every key
        saved, one the manifest no longer has too -- the caller keeps those
        of the manifest's secret settings."""
        with self._lock:
            secrets = self._Read(pluginId, dev)
        return {EnvironmentName(key): value for key, value in secrets.items()}

    def Forget(self, pluginId: str, *, dev: bool) -> None:
        """Remove secrets.bin (removal, new install)."""
        with self._lock:
            self._Remove(self.Path(pluginId, dev=dev))
            self._warned.discard((pluginId, dev))
        logger.info(f"Plugin secrets: {pluginId}'s removed")
