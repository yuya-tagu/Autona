"""Game asset packs: the upstream index, its download and the imports
(ADR-005 S-final SF-3b).

Autona does not bundle character / stage images (they are copyrighted by
their rights holders; user_data/games/README.md). They are fetched on the
operator's request from the public source TournamentStreamHelper uses --
joaorb64/StreamHelperAssets: its index (assets.json) lists each game's
packs, the binaries are split 7z files on the repository's latest Release
-- or imported from a game folder / ZIP the operator already has. Until
SF-3b all of it was the "Add game assets" window's own
(src/AutonaGameAssetGuideWindow.py: two QThreads, the copy and the ZIP
extraction on the GUI thread); here it is core's, so the React page that
replaces the window (SF-4b, /web/assets) can do it in either shell, and the
window calls this until then. v1 reads the list and the last job's state,
and since SF-4b-1 starts and cancels the jobs and takes the ZIP upload (see
Safety).

Only the packs the app uses are offered or installed (ADR-002):
``ALLOWED_PACKS``, the same rule for every title. A download fetches only
allowed packs and moves only their folders into place; an import leaves out
every other pack (a subfolder of the game with its own config.json).

The index
---------
``EnsureIndex(refresh)`` fetches assets.json on the calling thread when it
is not cached, older than ``INDEX_MAX_AGE_SECS`` or ``refresh`` is asked
(at most once every ``INDEX_MIN_REFRESH_SECS``, failed tries included);
callers that come while one fetch runs wait for it and share it.
``FetchIndex`` does the same as a job (the window: never on the GUI
thread). ``Packs`` is the list the page and the window show: every game of
the index with its allowed packs (size, version, installed version) and the
installed games the index does not have.

The jobs
--------
An install (``Install``), a ZIP import (``ImportZip``) or a folder import
(``ImportFolder``) is one job of the TaskExecutor, one at a time (a second
one is ``BUSY``, not queued): they all write user_data/games. Each takes the
next revision, which its start returns; ``Job()`` is the last one's state
(``{revision, status, operation, game, stage, pack, file, done, total,
imported, skipped, skippedEntries, skippedEntriesMore, failed, error,
message}``) and ``Cancel()`` asks it to stop. The footer shows it while it runs (StatusService ``assets_load`` with
the game, ``done`` / ``total`` in KiB for a download, in files for an
import; dedicated codes need the web's i18next words, SF-4b), and the job's
state is final before the footer drops it: a reader told by the ``status``
notice reads the outcome.

Everything is written to a staging folder under user_data/games
(``.autona-staging-*``: no base_files/config.json at its top, so a scan
skips it) and moved into place at the end. An import moves game by game:
an installed game is first moved aside (``.autona-replaced-<name>-*``,
outside the staging folder) and removed only once the new one is in; when
the new one cannot go in, the old one is put back, and when that fails
too the aside is kept, never removed, and named in the log and the
failure. An install's last step merges the extracted packs into the
game's folder file by file (a file there already is replaced, the others
stay -- what the window's extraction over the folder did): that merge is
not atomic, so a failure inside it leaves the files it had moved. Before
that last step, a job that is cancelled, stopped or fails leaves the games
as they were (an import keeps the games it had moved in before). A cancel
or the app's shutdown (TaskExecutor ``stopping``) is seen between the
downloaded chunks, the extracted ZIP entries and the copied files, and
after a pack's 7z extraction (py7zr cannot be stopped inside one). When
the next job starts, what a job left behind is cleaned: staging folders
are removed, and a game moved aside is put back when its place is empty
(the newest copy, when there are several), else kept and logged (it may
be the only copy). The games moved aside are looked at the same way when
each shell's first scan starts too (``RestoreReplaced``: a process that
died between a job's two moves would otherwise leave the game missing
from the list until the next job), but never while a job runs
(``_filesLock``). An installed copy whose replacement went in is removed
with a marker (``DISCARD_MARKER``) written first, so what cannot be
removed -- a file in use, a read-only file is tried again writable -- is
never put back and is removed again later. Every removal clears the
read-only attribute and tries again; what is left is logged.

Once a job has moved a game in, it re-scans the games once on its own
thread with ``scan``, the shell's scan (injected: the desktop adapter's,
the headless loader's -- SF-6 makes it the core GameLoader's), which tells
the game selector and the web server (``games_changed``) as the startup
scan does.

The upload
----------
The page cannot hand a path to the server (the browser does not give one),
so it sends the ZIP's bytes (v1 POST /assets/uploads; SF-4b-1).
``ReceiveUpload`` reads them on the web server's thread, a chunk at a time,
into ``UPLOADS_DIR`` -- user_data/.autona-uploads, not served (the games
folder is) -- after checking the free space, probes the file as an import
does and holds it: one upload at a time, a new one replacing it (and one
still arriving) at its start, under an id (``secrets.token_urlsafe``) for
``UPLOAD_EXPIRY_SECS``. ``ImportUpload`` takes it out and hands it to
``ImportZip(removeAfter=True)``, which removes it when the job ends (or
when the import is refused); a job started while it was probed (BUSY)
leaves it, and ``ImportUpload`` holds it again under its id, its expiry
unchanged. ``DropUpload`` removes it. Past its expiry it is no longer answered, and
is removed when the slot is next looked at (a newer upload, a lookup under
any id) or at the next start: no timer thread (the threads beside the
executor are a fixed list, tests/test_task_executor.py). A receive that fails
removes what it wrote. What a process left there when it ended is removed
by ``RemoveStaleUploads`` at each shell's first scan. Neither the path nor
the client's file name is answered or logged.

Safety
------
- An archive is read before anything is extracted: every name must be a
  relative path below the staging folder (no absolute path, drive, ``..``
  or ``:``), no symbolic link or junction, at most ``MAX_ENTRIES`` entries
  and ``MAX_UNPACKED_BYTES`` unpacked; a refused archive writes nothing. A
  ZIP's central directory is checked from its end records before zipfile
  reads it (``_CheckCentralDirectory``: entries, at most
  ``MAX_CENTRAL_DIRECTORY_BYTES``, inside the file), since zipfile reads it
  whole and makes an object of every entry first. A
  7z (the upstream packs) is refused for any name Windows cannot write
  too; a ZIP entry that stays inside but is no such name (a refused or
  control character, a trailing dot / space, a device name) is left out
  alone and listed (``skippedEntries``), as the window's extractall wrote
  the rest. Each target is checked again against the staging folder when
  it is written. Names are matched case-insensitively, as Windows does
  (``Base_Files/Config.json``, a pack ``LOGO`` with ``CONFIG.JSON``); two
  games whose names differ only in case are refused.
- A folder import copies no symbolic link or junction (a link to a file
  outside would be copied as that file).
- The free space is checked before a download, an extraction and a copy.
- Game folder names and the index's file names are checked
  (``SafeName``; the index's file names are plain names, no leading dot,
  none named as a pack's merged ``<pack>.7z``).
- Who may read what is the web layer's (src/core/WebAccess.py: the list is
  THIS_PC -- it may fetch from GitHub, which a LAN device or the preview
  listener must not set off; the index's error is a code, its text logged
  alone). The jobs, their state (its messages may name a path), the upload
  and the games folder's reveal are SHELL (SF-4b-1): THIS_PC also lets this
  origin's layout/ pages through, which could replace or remove installed
  games, download gigabytes or fill the disk; the secret is the shell's,
  handed only to the shared view's main frame, so headless has none of
  them. The API answers a refusal's code, never its message.
- A folder import is not in v1 (a page cannot name a folder; SF-5a's
  folder picker brings it back): the window's alone until then.

Lock order: this service's ``_lock`` guards its own fields only; nothing is
called with it held (no scan, no StatusService, no executor). The scan runs
on the job's thread with no lock of ours but ``_filesLock``, which a job
holds for its whole run (``_Run``) and ``RestoreReplaced`` takes without
waiting (it never runs a scan); it is never taken with ``_lock`` held.
An upload is received, probed and removed with no lock of ours (``_lock``
only to read and swap the slot).

Qt-free (tests/test_core_qt_free.py). Import side effects: none
(agent-pitfalls #1). ``AssetPackService.instance`` is built by
``bootstrap_core()``; the shells set its ``scan`` and call its
``RestoreReplaced`` and ``RemoveStaleUploads`` on their first scan job,
before scanning. The upload's expiry is read off ``time.monotonic()`` when
the slot is looked at (``_HeldUpload``); nothing runs when it passes.
"""
import hmac
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import requests
from loguru import logger

from .GameAssetCatalog import GAMES_DIR, GameAssetCatalog
from .StatusService import StatusService
from .TaskExecutor import TaskExecutor

# The upstream project and its public asset source (what TSH downloads
# from): the index lists the packs, the binaries are split 7z files on the
# latest Release.
SOURCE_URL = "https://github.com/joaorb64/TournamentStreamHelper"
INDEX_URL = ("https://raw.githubusercontent.com/joaorb64/StreamHelperAssets"
             "/main/assets.json")
DOWNLOAD_BASE = ("https://github.com/joaorb64/StreamHelperAssets/releases"
                 "/latest/download/")

# The only packs offered or installed (ADR-002): the functional scoreboard
# assets the app consumes. Logos, key / mural art, promotional renders,
# posters, comics, animations, audio, versus-screen art and every pack type
# upstream adds later are not -- publisher tournament guidelines restrict
# such material (e.g. Nintendo's, Q2/A2), and the index has no publisher to
# single titles out by, so the rule is the same for all of them.
ALLOWED_PACKS = frozenset(
    {"base_files", "full", "portrait", "stage_icon", "variant_icon"})
# Of those, the ones a download offers checked.
DEFAULT_PACKS = ("base_files", "full", "portrait", "stage_icon",
                 "variant_icon")

# requests' timeouts (connect, and each read).
INDEX_TIMEOUT_SECS = 30
DOWNLOAD_TIMEOUT_SECS = 60
# How long a fetched index is used, and how often a refresh may fetch.
INDEX_MAX_AGE_SECS = 600
INDEX_MIN_REFRESH_SECS = 5

# The limits (the window had none). The biggest game's allowed packs are
# about 820 MiB to download (umvc3, 2026-11); a game folder with every pack
# a few GiB.
MAX_DOWNLOAD_BYTES = 8 * 1024 ** 3
MAX_UNPACKED_BYTES = 8 * 1024 ** 3
MAX_ENTRIES = 200_000
# A ZIP's central directory is read whole by zipfile.ZipFile() (and each
# entry becomes an object) before _ZipPlan can count the entries: it is
# checked first, from the end records, against these (_CheckCentralDirectory).
MAX_CENTRAL_DIRECTORY_BYTES = 64 * 1024 * 1024
# Kept free on the disk on top of what a step writes.
DISK_MARGIN_BYTES = 64 * 1024 * 1024
# How many left-out ZIP entries a job lists by name (the rest are counted).
MAX_SKIPPED_ENTRIES = 100

_CHUNK = 64 * 1024
# The footer's progress is updated at most this often.
_STATUS_INTERVAL_SECS = 0.2
STAGING_PREFIX = ".autona-staging-"
# An installed game moved aside while its replacement goes in:
# user_data/games/.autona-replaced-<name>-<random>/<name>.
REPLACED_PREFIX = ".autona-replaced-"
# In such a folder: its replacement went in, so the copy is not wanted and
# never put back (it may be partly removed already); written before the
# copy is removed and removed last, so what is left is removed again.
DISCARD_MARKER = ".autona-discard"
# The upload (SF-4b-1): the ZIP the shared view's page sends, held until it is
# imported, dropped or expires. Outside user_data/games, which is served to
# the LAN (WebAccess.STATIC_DIRECTORIES), and not a staging folder (a job's
# start removes those). One at a time: a new upload replaces the held one.
UPLOADS_DIR = "./user_data/.autona-uploads/"
UPLOAD_PREFIX = "upload-"
MAX_UPLOAD_BYTES = MAX_UNPACKED_BYTES
UPLOAD_EXPIRY_SECS = 15 * 60
UPLOAD_CHUNK = 1024 * 1024
# An upload's id: secrets.token_urlsafe(16), checked by shape before it is
# compared.
_UPLOAD_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
STATUS_CODE = "assets_load"
# A move's retries, as StatePersistence's (a reader without
# FILE_SHARE_DELETE -- an antivirus scan, an indexer -- makes os.replace fail
# for a moment on Windows).
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.02

# What a start answers (with the job's revision).
STARTED = "started"
BUSY = "busy"

# Job.status
IDLE = "idle"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

# Job.operation
INSTALL = "install"
IMPORT_ZIP = "importZip"
IMPORT_FOLDER = "importFolder"

# Job.stage
DOWNLOAD = "download"
EXTRACT = "extract"
COPY = "copy"
SCAN = "scan"

# IndexState().status
INDEX_IDLE = "idle"
INDEX_LOADING = "loading"
INDEX_READY = "ready"
INDEX_ERROR = "error"
# IndexState().error: a code, not the failure's text (which names the
# network, the proxy or the response; it goes to the log alone).
INDEX_FETCH_FAILED = "fetch_failed"

# AssetPackError.code (a refused start or probe) and Job.error (a job that
# could not finish).
UNKNOWN_GAME = "unknown_game"
UNKNOWN_PACK = "unknown_pack"
NO_PACKS = "no_packs"
INDEX_UNAVAILABLE = "index_unavailable"
BAD_INDEX = "bad_index"
NOT_A_ZIP = "not_a_zip"
NOT_A_FOLDER = "not_a_folder"
UNSAFE_ARCHIVE = "unsafe_archive"
TOO_LARGE = "too_large"
NO_GAME = "no_game"
BAD_NAME = "bad_name"
NEEDS_CONFIRMATION = "needs_confirmation"
STOPPED = "stopped"
DOWNLOAD_FAILED = "download_failed"
EXTRACT_FAILED = "extract_failed"
COPY_FAILED = "copy_failed"
DISK_FULL = "disk_full"
FAILED = "failed"
# The upload's: no upload held by that id (never sent, imported, dropped,
# replaced by a newer one or expired); one whose body did not arrive whole
# (the client went away or sent less); one a newer upload replaced while it
# arrived.
UNKNOWN_UPLOAD = "unknown_upload"
INCOMPLETE = "incomplete"
SUPERSEDED = "superseded"

# The index's file names: plain names, nothing a path or a URL could read
# more into (no leading dot: not "." nor "..").
_FILE_NAME = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]*$")
# Windows device names: refused as a path part, with or without extension.
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"COM{i}" for i in range(1, 10)] + [f"LPT{i}" for i in range(1, 10)])
_BAD_CHARS = frozenset('<>:"|?*\x00')


class AssetPackError(Exception):
    """A start or a probe refused: ``code`` (the constants above), and
    ``names`` for NEEDS_CONFIRMATION (the installed games it would
    replace). ``message`` is English, for the logs and the window; the API
    answers the code (``error.reason``) and its own fixed words, never the
    message (it may name a path or the archive's entries)."""

    def __init__(self, code, message, names=()):
        super().__init__(message)
        self.code = code
        self.message = message
        self.names = list(names)


class _Cancelled(Exception):
    pass


class _Stopped(Exception):
    pass


class _JobError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ── names and paths ──────────────────────────────────────────────────────

def SafeName(name):
    """Whether ``name`` can be one folder's name under user_data/games: one
    path part, no ``.`` / ``..``, no leading dot (the staging folders'), no
    character Windows refuses, no device name, no trailing dot or space."""
    if not isinstance(name, str) or not name or name.startswith("."):
        return False
    return _SafePart(name)


def _SafePart(part):
    if part in ("", ".", ".."):
        return False
    if any(ch in _BAD_CHARS or ord(ch) < 32 for ch in part):
        return False
    if "/" in part or "\\" in part:
        return False
    if part[-1] in ". ":
        return False
    if part.split(".")[0].upper() in _RESERVED:
        return False
    return True


def ArchivePath(name):
    """An archive entry's name as its path parts, or None when it would
    leave the folder it is extracted to: absolute, a drive, ``..``, a
    character Windows refuses (``:`` included: drives and alternate
    streams). ``\\`` counts as a separator (ZIPs made on Windows). A
    directory entry's trailing ``/`` is dropped; an empty name is None."""
    if not isinstance(name, str):
        return None
    normalised = name.replace("\\", "/")
    if normalised.startswith("/"):
        return None
    parts = normalised.split("/")
    if parts and parts[-1] == "":
        parts.pop()
    if not parts:
        return None
    if any(not _SafePart(part) for part in parts):
        return None
    return tuple(parts)


# _ZipEntryPath's verdicts.
_ENTRY_OK = "ok"
_ENTRY_SKIP = "skip"
_ENTRY_UNSAFE = "unsafe"


def _ZipEntryPath(name):
    """A ZIP entry's name as (path parts, verdict). UNSAFE (parts None) when
    it would leave the folder it is extracted to -- absolute, a drive,
    ``:`` (drives and alternate streams), ``..`` -- which refuses the whole
    ZIP. SKIP when it stays inside but a part is no name Windows can write
    (a refused or control character, a trailing dot / space, a device
    name): that entry alone is left out. ``\\`` counts as a separator; empty
    and ``.`` parts are dropped (``a//b``, ``./a``), so ``()`` is an entry
    naming the folder itself (nothing to write)."""
    if not isinstance(name, str):
        return None, _ENTRY_UNSAFE
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or ":" in normalised:
        return None, _ENTRY_UNSAFE
    parts = tuple(part for part in normalised.split("/")
                  if part not in ("", "."))
    if ".." in parts:
        return None, _ENTRY_UNSAFE
    if all(_SafePart(part) for part in parts):
        return parts, _ENTRY_OK
    return parts, _ENTRY_SKIP


def _Folded(parts):
    return tuple(part.casefold() for part in parts)


def _Within(root, path):
    root = os.path.normcase(os.path.abspath(root))
    path = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([root, path]) == root and path != root
    except ValueError:      # another drive
        return False


def _IsLink(path):
    if os.path.islink(path):
        return True
    isJunction = getattr(os.path, "isjunction", None)
    return bool(isJunction and isJunction(path))


def _GamesRoot():
    return os.path.abspath(GAMES_DIR)


def _UploadsRoot():
    return os.path.abspath(UPLOADS_DIR)


def RevealGamesFolder():
    """Open user_data/games (made first when missing) in this PC's file
    manager: v1's games-folder:reveal, which takes no path. False when it
    is not shown (logged); a file manager that cannot be started raises
    OSError. As LayoutCatalog.RevealBoardFile: explorer by its full path
    (by name Windows looks in the working directory first), the path in
    quotes (explorer splits its command line at commas), no shell."""
    path = _GamesRoot()
    os.makedirs(path, exist_ok=True)
    if sys.platform.startswith("win"):
        # NTFS refuses a double quote in a name; one here could close the
        # quotes below.
        if '"' in path:
            logger.warning("Asset packs: the games folder is not shown")
            return False
        windows = os.environ.get("WINDIR")
        if not windows:
            raise OSError("WINDIR is not set")
        explorer = os.path.join(windows, "explorer.exe")
        subprocess.Popen(f'"{explorer}" "{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return True


def _Int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _FreeBytes(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _RequireSpace(path, needed):
    free = _FreeBytes(path)
    if free is not None and free < needed + DISK_MARGIN_BYTES:
        raise _JobError(
            DISK_FULL,
            f"not enough free space: {needed} bytes needed, {free} free")


# ── probing what an import holds ─────────────────────────────────────────

def _FolderGames(root):
    """(name, folder) of each game a folder holds: the folder itself when it
    has base_files/config.json, else its immediate subfolders that do (a
    folder may hold one game or several). Links are not followed."""
    if os.path.isfile(os.path.join(root, "base_files", "config.json")):
        return [(os.path.basename(os.path.normpath(root)), root)]
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        folder = os.path.join(root, name)
        if _IsLink(folder):
            continue
        if os.path.isfile(os.path.join(folder, "base_files", "config.json")):
            found.append((name, folder))
    return found


def _CheckGameNames(names):
    # Each a folder name under user_data/games, and no two the same folder
    # on Windows (``ssbu`` and ``SSBU``).
    seen = {}
    for name in names:
        if not SafeName(name):
            raise AssetPackError(
                BAD_NAME, f"{name!r} cannot be a game folder's name")
        other = seen.setdefault(name.casefold(), name)
        if other != name:
            raise AssetPackError(
                BAD_NAME, f"{other!r} and {name!r} differ only in case: "
                "they are one folder on Windows")


def ProbeFolder(path):
    """The games a folder holds, as (name, folder) pairs. AssetPackError
    NOT_A_FOLDER (no such folder), NO_GAME (none) or BAD_NAME (a game's
    folder name cannot be one under user_data/games, or two differ only in
    case)."""
    if not isinstance(path, str) or not path or not os.path.isdir(path):
        raise AssetPackError(NOT_A_FOLDER, "no such folder")
    games = _FolderGames(path)
    if not games:
        raise AssetPackError(
            NO_GAME, "no game found: a game folder must contain "
            "base_files/config.json")
    _CheckGameNames(name for name, _folder in games)
    return games


def _ZipPlan(z, nameHint):
    """What a ZIP holds, checked: {game name: its entries' path prefix}, the
    entries as (info, path parts, written) -- ``written`` False for one
    left out (_ZipEntryPath's SKIP) -- and the files it writes, case-folded.
    The game is the ZIP's top when base_files/config.json is there (named
    after the file: ``nameHint``), else each top folder that has one; names
    are matched case-insensitively, as Windows does. Raises AssetPackError
    UNSAFE_ARCHIVE / TOO_LARGE / NOT_A_ZIP / NO_GAME / BAD_NAME (two games
    whose names differ only in case included)."""
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise AssetPackError(
            TOO_LARGE, f"the ZIP has more than {MAX_ENTRIES} entries")
    entries = []
    unpacked = 0
    files = set()
    for info in infos:
        parts, verdict = _ZipEntryPath(info.filename)
        if verdict == _ENTRY_UNSAFE:
            raise AssetPackError(
                UNSAFE_ARCHIVE,
                "the ZIP has an entry that would be written outside its "
                "folder")
        if info.flag_bits & 0x1:
            raise AssetPackError(NOT_A_ZIP, "the ZIP is encrypted")
        if not parts:
            continue
        written = verdict == _ENTRY_OK
        entries.append((info, parts, written))
        if written and not info.is_dir():
            unpacked += info.file_size
            files.add(_Folded(parts))
    if unpacked > MAX_UNPACKED_BYTES:
        raise AssetPackError(
            TOO_LARGE,
            f"the ZIP unpacks to more than {MAX_UNPACKED_BYTES} bytes")
    games = {}
    if ("base_files", "config.json") in files:
        if not SafeName(nameHint or ""):
            raise AssetPackError(
                BAD_NAME, "the ZIP holds a game at its top; its file name "
                "cannot be a game folder's name")
        games[nameHint] = ()
    else:
        names = sorted({
            parts[0] for info, parts, written in entries
            if written and not info.is_dir() and len(parts) == 3
            and _Folded(parts[1:]) == ("base_files", "config.json")})
        _CheckGameNames(names)
        games = {name: (name,) for name in names}
    if not games:
        raise AssetPackError(
            NO_GAME, "no game found: a game folder must contain "
            "base_files/config.json")
    return games, entries, files


def ZipNameHint(fileName):
    """The game name a ZIP holding one game at its top is imported as: its
    file name without the extension (None for none)."""
    if not isinstance(fileName, str) or not fileName:
        return None
    base = fileName.replace("\\", "/").rsplit("/", 1)[-1]
    stem = os.path.splitext(base)[0]
    return stem or None


# The ZIP end records, as zipfile reads them (APPNOTE 4.3.14-4.3.16): the
# end of central directory record, the ZIP64 locator just before it and the
# ZIP64 end record just before that.
_EOCD = b"PK\x05\x06"
_EOCD_FORMAT = "<4s4H2LH"
_EOCD_SIZE = struct.calcsize(_EOCD_FORMAT)                      # 22
_LOCATOR = b"PK\x06\x07"
_LOCATOR_FORMAT = "<4sLQL"
_LOCATOR_SIZE = struct.calcsize(_LOCATOR_FORMAT)                # 20
_EOCD64 = b"PK\x06\x06"
_EOCD64_FORMAT = "<4sQ2H2L4Q"
_EOCD64_SIZE = struct.calcsize(_EOCD64_FORMAT)                  # 56
_CENTRAL_HEADER = b"PK\x01\x02"
_CENTRAL_HEADER_SIZE = 46
# The name, extra field and comment lengths in a central header.
_CENTRAL_LENGTHS_AT = 28
_MAX_COMMENT = (1 << 16) - 1


def _EndRecord(f, size):
    """(entries, directory size, directory offset, where the record starts,
    ZIP64) of a ZIP's end records, found where zipfile finds them
    (zipfile._EndRecData / _EndRecData64, Python 3.12): the record in the
    last 22 bytes when it has no comment, else the last signature in the
    last 65,557 bytes (ZIP_MAX_COMMENT, 65,535, and the record's 22); the
    ZIP64 record just before its locator. None when there is none;
    AssetPackError NOT_A_ZIP where zipfile refuses (a ZIP split over disks,
    a ZIP64 locator with no room for its record before it). The two are
    compared on crafted ZIPs in tests/test_asset_packs.py."""
    record = None
    if size >= _EOCD_SIZE:
        f.seek(size - _EOCD_SIZE)
        data = f.read(_EOCD_SIZE)
        if (len(data) == _EOCD_SIZE and data[:4] == _EOCD
                and data[-2:] == b"\x00\x00"):
            record = (data, size - _EOCD_SIZE)
    if record is None:
        start = max(size - _MAX_COMMENT - _EOCD_SIZE, 0)
        f.seek(start)
        data = f.read(_MAX_COMMENT + _EOCD_SIZE)
        at = data.rfind(_EOCD)
        if at < 0 or len(data) - at < _EOCD_SIZE:
            return None
        record = (data[at:at + _EOCD_SIZE], start + at)
    data, location = record
    (_sig, _disk, _diskStart, _here, entries, directorySize,
     directoryOffset, _comment) = struct.unpack(_EOCD_FORMAT, data)
    if location < _LOCATOR_SIZE:
        return entries, directorySize, directoryOffset, location, False
    f.seek(location - _LOCATOR_SIZE)
    data = f.read(_LOCATOR_SIZE)
    if len(data) != _LOCATOR_SIZE:
        return entries, directorySize, directoryOffset, location, False
    sig, diskNumber, _offset, disks = struct.unpack(_LOCATOR_FORMAT, data)
    if sig != _LOCATOR:
        return entries, directorySize, directoryOffset, location, False
    if diskNumber != 0 or disks > 1:
        raise AssetPackError(NOT_A_ZIP, "a ZIP split over several disks "
                             "cannot be read")
    if location < _LOCATOR_SIZE + _EOCD64_SIZE:
        # zipfile seeks before the file's start for the record: OSError,
        # which ZipFile() answers BadZipFile.
        raise AssetPackError(NOT_A_ZIP, "not a ZIP: a ZIP64 locator with no "
                             "room for its record")
    f.seek(location - _LOCATOR_SIZE - _EOCD64_SIZE)
    data = f.read(_EOCD64_SIZE)
    if len(data) != _EOCD64_SIZE:
        return entries, directorySize, directoryOffset, location, False
    (sig, _size, _made, _needed, _disk, _diskStart, _here, entries64,
     directorySize64, directoryOffset64) = struct.unpack(_EOCD64_FORMAT, data)
    if sig != _EOCD64:
        return entries, directorySize, directoryOffset, location, False
    return entries64, directorySize64, directoryOffset64, location, True


def _CheckCentralDirectory(path):
    """Refuse a ZIP before zipfile.ZipFile() reads it: that reads the whole
    central directory into memory and makes an object of each entry in it
    -- however many the end record says -- before _ZipPlan can count them.
    From the end records (read as zipfile reads them, with struct: no
    private zipfile API): AssetPackError TOO_LARGE when it lists more than
    MAX_ENTRIES entries or its directory is larger than
    MAX_CENTRAL_DIRECTORY_BYTES, NOT_A_ZIP when there is no end record or
    the directory lies outside the file. The directory (at most that size)
    is then walked header by header as zipfile walks it -- zipfile stops at
    the directory's size, not at the stated count -- and refused the same
    way for more entries or a header that is not one. No message names the
    path."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            record = _EndRecord(f, size)
            if record is None:
                raise AssetPackError(NOT_A_ZIP, "not a ZIP: no end of "
                                     "central directory record")
            entries, directorySize, directoryOffset, location, zip64 = record
            if entries > MAX_ENTRIES:
                raise AssetPackError(
                    TOO_LARGE, f"the ZIP lists more than {MAX_ENTRIES} "
                    "entries")
            if directorySize > MAX_CENTRAL_DIRECTORY_BYTES:
                raise AssetPackError(
                    TOO_LARGE, "the ZIP's central directory is larger than "
                    f"{MAX_CENTRAL_DIRECTORY_BYTES} bytes")
            # Where zipfile reads the directory from: just before the end
            # records (the stated offset only says how much was prepended).
            start = location - directorySize - (
                _EOCD64_SIZE + _LOCATOR_SIZE if zip64 else 0)
            if start < 0 or directoryOffset > start:
                raise AssetPackError(NOT_A_ZIP, "not a ZIP: its central "
                                     "directory lies outside the file")
            f.seek(start)
            directory = f.read(directorySize)
    except OSError as error:
        raise AssetPackError(
            NOT_A_ZIP, f"not a readable ZIP: {_ErrorText(error)}") from None
    at = 0
    count = 0
    while at < directorySize:
        if count >= MAX_ENTRIES:
            raise AssetPackError(
                TOO_LARGE, f"the ZIP has more than {MAX_ENTRIES} entries")
        if (len(directory) - at < _CENTRAL_HEADER_SIZE
                or directory[at:at + 4] != _CENTRAL_HEADER):
            raise AssetPackError(NOT_A_ZIP, "not a ZIP: a bad central "
                                 "directory header")
        nameLength, extraLength, commentLength = struct.unpack_from(
            "<3H", directory, at + _CENTRAL_LENGTHS_AT)
        at += _CENTRAL_HEADER_SIZE + nameLength + extraLength + commentLength
        count += 1


def _ErrorText(error):
    # An OSError's text names the file (its path: this PC's folders, the
    # user's name); its reason alone does not.
    if isinstance(error, OSError):
        return error.strerror or type(error).__name__
    return str(error)


def ProbeZip(path, nameHint=None):
    """The game names a ZIP holds, checked as an import checks it (see
    _ZipPlan; its central directory first, _CheckCentralDirectory).
    ``nameHint``: the name of a game at the ZIP's top (the file name without
    its extension when None). AssetPackError NOT_A_ZIP when it is not one.
    No message names the path."""
    if nameHint is None:
        nameHint = ZipNameHint(path)
    _CheckCentralDirectory(path)
    try:
        with zipfile.ZipFile(path) as z:
            games, _entries, _files = _ZipPlan(z, nameHint)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError,
            ValueError, NotImplementedError) as error:
        raise AssetPackError(
            NOT_A_ZIP, f"not a readable ZIP: {_ErrorText(error)}") from None
    return list(games)


def ImportConflicts(games):
    """Of ``games`` -- names, or (name, source folder) pairs -- the ones
    already installed under user_data/games, but for a source that is that
    folder itself (nothing to replace). A game a job moved aside and did
    not put back counts as installed: the job about to start puts it back
    first (RestoreReplaced), and then replaces it only if asked."""
    root = _GamesRoot()
    asides = {child.casefold()
              for container, marked in _Containers(root) if not marked
              for child in _Children(container)
              if _Restorable(container, child)}
    conflicts = []
    for game in games:
        name, source = (game, None) if isinstance(game, str) else game
        dest = os.path.join(root, name)
        if not os.path.exists(dest):
            if name.casefold() in asides:
                conflicts.append(name)
            continue
        if source is not None and (
                os.path.normcase(os.path.abspath(source))
                == os.path.normcase(dest)):
            continue
        conflicts.append(name)
    return conflicts


def _Overwrites(overwrite, name):
    if overwrite is True:
        return True
    if not overwrite:
        return False
    return name in overwrite


# ── the job ──────────────────────────────────────────────────────────────

class _Job:
    """One job's state; its fields under the service's lock."""

    def __init__(self, revision, operation, game):
        self.revision = revision
        self.operation = operation
        self.game = game
        self.status = RUNNING
        self.stage = None
        self.pack = None
        self.file = None
        self.done = 0
        self.total = 0
        self.imported = []
        self.skipped = []
        # The ZIP entries left out (names Windows cannot write): the first
        # MAX_SKIPPED_ENTRIES by name, the rest counted.
        self.skippedEntries = []
        self.skippedEntriesMore = 0
        self.failed = []
        self.error = None
        self.message = None
        self.cancel = threading.Event()
        self.footer = None

    def Snapshot(self):
        return {"revision": self.revision, "status": self.status,
                "operation": self.operation, "game": self.game,
                "stage": self.stage, "pack": self.pack, "file": self.file,
                "done": self.done, "total": self.total,
                "imported": list(self.imported),
                "skipped": list(self.skipped),
                "skippedEntries": list(self.skippedEntries),
                "skippedEntriesMore": self.skippedEntriesMore,
                "failed": [dict(entry) for entry in self.failed],
                "error": self.error, "message": self.message}


class _Footer:
    """The job's line in the footer (StatusService): pushed when the job
    starts, its progress updated at most every _STATUS_INTERVAL_SECS (and
    at the end of a stage), popped by Close()."""

    # The code and its args are written out at each report: the reports'
    # codes are checked against StatusService.CODES where they are made
    # (tests/test_status_service.py). "assets_load" is STATUS_CODE.

    def __init__(self, label):
        self.label = label
        self.token = StatusService.Push("assets_load", {"codename": label})
        self.at = 0.0

    def Progress(self, done, total, force=False):
        now = time.monotonic()
        if not force and now - self.at < _STATUS_INTERVAL_SECS:
            return
        self.at = now
        StatusService.Update(self.token, "assets_load",
                             {"codename": self.label}, done=done, total=total)

    def Close(self):
        token, self.token = self.token, None
        StatusService.Pop(token)


class AssetPackService:
    instance = None

    def __init__(self, scan=None):
        # The shell's scan of user_data/games, run on the job's thread once
        # a job has moved a game in (set by the shells; None: the catalog's
        # scan alone, which tells nobody -- tests).
        self.scan = scan
        self._lock = threading.Lock()
        # user_data/games is changed by one at a time: a job, for its whole
        # run (_Run), or RestoreReplaced, which does not wait for it. Never
        # taken with _lock held.
        self._filesLock = threading.Lock()
        self._job = None
        self._revision = 0
        # The index: the parsed assets.json (None until one was fetched),
        # when it was fetched and when a fetch was last tried
        # (time.monotonic()), its wall-clock time, the last failure's text
        # and whether a fetch runs. _indexFetch serialises the fetches.
        self._index = None
        self._indexAt = None
        self._indexTriedAt = None
        self._indexFetchedAt = None
        self._indexError = None
        self._indexLoading = False
        # How many fetches have finished: a caller that waited on
        # _indexFetch while one ran uses its result (not the clock: 15.6 ms
        # on Windows).
        self._indexTries = 0
        self._indexFetch = threading.Lock()
        # The upload (ReceiveUpload): the one held -- {"id", "path",
        # "games", "nameHint", "expiresAt"} -- or None; how many
        # uploads have started (a newer one replaces one still arriving);
        # the files being received and the ones an import took
        # (RemoveStaleUploads leaves them).
        self._upload = None
        self._uploadGeneration = 0
        self._receiving = set()
        self._taken = set()

    # ── the index ────────────────────────────────────────────────────────

    def IndexState(self):
        """{"status": idle | loading | ready | error, "error": the last
        failure's code (INDEX_FETCH_FAILED: no answer / an HTTP error;
        BAD_INDEX: not a JSON object) or None -- its text is logged, never
        returned --, "fetchedAt": the index's time (Unix seconds) or None}.
        "error" keeps an older index usable: Packs still lists it."""
        with self._lock:
            if self._indexLoading:
                status = INDEX_LOADING
            elif self._indexError is not None:
                status = INDEX_ERROR
            elif self._index is not None:
                status = INDEX_READY
            else:
                status = INDEX_IDLE
            return {"status": status, "error": self._indexError,
                    "fetchedAt": self._indexFetchedAt}

    def EnsureIndex(self, refresh=False):
        """Fetch assets.json on the calling thread when it is not cached, is
        older than INDEX_MAX_AGE_SECS, or ``refresh`` (at most once every
        INDEX_MIN_REFRESH_SECS, a failed try included). A caller that comes
        while a fetch runs waits for it and uses it. True when an index is
        cached afterwards. Never on the GUI thread (the network)."""
        with self._lock:
            seen = self._indexTries
        with self._indexFetch:
            with self._lock:
                if self._indexTries != seen:
                    # Fetched (or tried) while this caller waited.
                    return self._index is not None
                now = time.monotonic()
                tried = self._indexTriedAt
                recent = tried is not None and \
                    now - tried < INDEX_MIN_REFRESH_SECS
                fresh = self._index is not None and \
                    now - self._indexAt < INDEX_MAX_AGE_SECS
                if recent or (fresh and not refresh):
                    return self._index is not None
                self._indexLoading = True
                self._indexTriedAt = now
            data, error = None, None
            try:
                response = requests.get(INDEX_URL, timeout=INDEX_TIMEOUT_SECS)
                response.raise_for_status()
            except Exception as failure:
                error = INDEX_FETCH_FAILED
                logger.warning("Asset index fetch failed: "
                               f"{str(failure) or type(failure).__name__}")
            if error is None:
                try:
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError("the index is not a JSON object")
                except Exception as failure:
                    data, error = None, BAD_INDEX
                    logger.warning(
                        "Asset index fetch: not a usable index: "
                        f"{str(failure) or type(failure).__name__}")
            with self._lock:
                self._indexLoading = False
                self._indexTries += 1
                if error is None:
                    self._index = data
                    self._indexAt = time.monotonic()
                    self._indexFetchedAt = int(time.time())
                    self._indexError = None
                else:
                    self._indexError = error
                return self._index is not None

    def FetchIndex(self, refresh=False):
        """EnsureIndex as a job of the TaskExecutor (the window's: off the
        GUI thread). Its Future (cancelled when the app is shutting down);
        IndexState / Packs(fetch=False) read the result."""
        return TaskExecutor.instance.submit(
            self.EnsureIndex, refresh, task_name="asset index fetch")

    def Packs(self, fetch=True, refresh=False):
        """What can be installed and what is.

        {"source": {"page", "index"}, "index": IndexState(), "games":
        [{"code", "name", "available" (in
        the index), "installed" (scanned under user_data/games), "packs":
        [{"name", "size" (bytes to download; None when not in the index),
        "version", "installedVersion" (the installed pack's config.json;
        None when it has none), "installed", "updateAvailable", "default"
        (offered checked)}]}]}. Only allowed packs. The games are sorted
        as the window lists them ("name  (code)", case-insensitively).

        ``fetch``: EnsureIndex(refresh) first, on the calling thread (the
        network); off, the index as cached. No local path is in it."""
        if fetch:
            self.EnsureIndex(refresh)
        with self._lock:
            index = self._index or {}
        catalog = GameAssetCatalog.instance
        installed = catalog.GamesView() if catalog is not None else {}
        games = {}
        for code, info in index.items():
            if not isinstance(info, dict) or not isinstance(code, str):
                continue
            packs = info.get("assets")
            packs = packs if isinstance(packs, dict) else {}
            local = self._InstalledPacks(installed.get(code))
            rows = []
            for name in sorted(packs):
                pack = packs[name]
                if name not in ALLOWED_PACKS or not isinstance(pack, dict):
                    continue
                files = pack.get("files")
                files = files if isinstance(files, dict) else {}
                size = sum(_Int(entry.get("size")) for entry in files.values()
                           if isinstance(entry, dict))
                version = pack.get("version")
                rows.append(self._PackRow(
                    name, size, None if version is None else str(version),
                    local))
            games[code] = {"code": code, "name": str(info.get("name") or code),
                           "available": True, "installed": code in installed,
                           "packs": rows}
        for code, game in installed.items():
            if code in games:
                continue
            local = self._InstalledPacks(game)
            games[code] = {
                "code": code, "name": str(game.get("name") or code),
                "available": False, "installed": True,
                "packs": [self._PackRow(name, None, None, local)
                          for name in sorted(local)]}
        return {
            "source": {"page": SOURCE_URL, "index": INDEX_URL},
            "index": self.IndexState(),
            "games": sorted(
                games.values(),
                key=lambda game: f"{game['name']}  ({game['code']})".lower()),
        }

    @staticmethod
    def _InstalledPacks(game):
        # {allowed pack: its config.json's version or None} of a scanned game.
        if not game:
            return {}
        assets = game.get("assets") or {}
        local = {}
        for name, config in assets.items():
            if name in ALLOWED_PACKS:
                version = config.get("version") \
                    if isinstance(config, dict) else None
                local[name] = None if version is None else str(version)
        return local

    @staticmethod
    def _PackRow(name, size, version, local):
        installed = name in local
        installedVersion = local.get(name)
        return {"name": name, "size": size, "version": version,
                "installedVersion": installedVersion, "installed": installed,
                "updateAvailable": bool(
                    installed and version is not None
                    and installedVersion is not None
                    and version != installedVersion),
                "default": name in DEFAULT_PACKS}

    # ── starting, reading, cancelling a job ──────────────────────────────

    def Job(self):
        """The last job's state (see the module docstring); IDLE with
        revision 0 before the first."""
        with self._lock:
            if self._job is None:
                return _Job(0, None, None).Snapshot() | {"status": IDLE}
            return self._job.Snapshot()

    def Busy(self):
        with self._lock:
            return self._job is not None and self._job.status == RUNNING

    def Cancel(self, revision=None):
        """Ask the running job to stop (seen at its next step); False when
        none runs. ``revision``: only when it is that job (v1's job:cancel:
        the job the page shows, not one started since); None, whichever
        runs (the window)."""
        with self._lock:
            job = self._job
            if job is None or job.status != RUNNING:
                return False
            if revision is not None and job.revision != revision:
                return False
            job.cancel.set()
        logger.info(f"Asset {job.operation} #{job.revision}: cancel asked")
        return True

    def Install(self, code, packs, fetch=True):
        """Download the allowed ``packs`` of game ``code`` from the index (None:
        the DEFAULT_PACKS it has) and install them under
        user_data/games/<code>; (STARTED, revision)
        or (BUSY, the running job's revision). ``fetch``: the index is
        fetched first when it is not cached or too old (EnsureIndex, the
        calling thread: the network); off, the cached one is used however
        old (the window: the GUI thread). AssetPackError INDEX_UNAVAILABLE /
        UNKNOWN_GAME / BAD_NAME / NO_PACKS / UNKNOWN_PACK / STOPPED / FAILED.
        Everything is downloaded and extracted into a staging folder
        first; the last step merges it into user_data/games/<code> file by
        file (a file there already is replaced, the others stay -- what the
        window's extraction over the folder did). That merge is not atomic:
        a failure inside it leaves the files it had moved."""
        if not isinstance(code, str) or not code:
            raise AssetPackError(UNKNOWN_GAME, "game is required")
        if fetch:
            cached = self.EnsureIndex()
        else:
            with self._lock:
                cached = self._index is not None
        if not cached:
            raise AssetPackError(
                INDEX_UNAVAILABLE, "the asset index could not be fetched")
        with self._lock:
            info = (self._index or {}).get(code)
        if not isinstance(info, dict):
            raise AssetPackError(UNKNOWN_GAME, f"unknown game: {code}")
        if not SafeName(code):
            raise AssetPackError(BAD_NAME,
                                 f"{code!r} cannot be a game folder's name")
        available = info.get("assets")
        available = available if isinstance(available, dict) else {}
        if packs is None:
            # The ones a download offers checked, of those the game has.
            packs = [name for name in DEFAULT_PACKS
                     if isinstance(available.get(name), dict)]
        if not isinstance(packs, (list, tuple)) or not packs:
            raise AssetPackError(NO_PACKS, "select at least one pack")
        chosen = []
        for name in packs:
            if (not isinstance(name, str) or name not in ALLOWED_PACKS
                    or not isinstance(available.get(name), dict)):
                raise AssetPackError(
                    UNKNOWN_PACK, f"not an offered pack of {code}: {name!r}")
            if name not in chosen:
                chosen.append(name)
        plan = [(name, available[name]) for name in sorted(chosen)]
        return self._Start(INSTALL, code, code,
                           lambda job: self._RunInstall(job, code, plan))

    def ImportZip(self, path, overwrite=False, nameHint=None,
                  removeAfter=False):
        """Import the games a ZIP holds (checked first: ProbeZip) into
        user_data/games; (STARTED, revision) or (BUSY, ...). ``overwrite``:
        True, False or the names of the installed games to replace (the
        others are skipped). ``removeAfter``: the ZIP is the job's (an
        upload) and is removed when it ends, however it ends -- refused
        here included, BUSY excepted: the caller keeps it (ImportUpload
        holds it again). ImportUpload passes it (v1's POST
        /assets:importZip). AssetPackError NOT_A_ZIP / UNSAFE_ARCHIVE /
        TOO_LARGE / NO_GAME / BAD_NAME / STOPPED / FAILED."""
        if nameHint is None:
            nameHint = ZipNameHint(path)
        started = busy = False
        try:
            names = ProbeZip(path, nameHint)
            answer = self._Start(
                IMPORT_ZIP, None, ", ".join(names),
                lambda job: self._RunImportZip(
                    job, path, overwrite, nameHint, removeAfter),
                cleanup=(lambda: self._ReleaseUpload(path))
                if removeAfter else None)
            started = answer[0] == STARTED
            busy = answer[0] == BUSY
            return answer
        finally:
            if removeAfter and not started and not busy:
                self._ReleaseUpload(path)

    def ImportFolder(self, path, overwrite=False):
        """Copy the games a folder holds (ProbeFolder) into user_data/games;
        (STARTED, revision) or (BUSY, ...). ``overwrite`` as ImportZip's.
        AssetPackError NOT_A_FOLDER / NO_GAME / BAD_NAME / STOPPED /
        FAILED."""
        games = ProbeFolder(path)
        return self._Start(
            IMPORT_FOLDER, None, ", ".join(name for name, _f in games),
            lambda job: self._RunImportFolder(job, path, overwrite))

    def _Start(self, operation, game, label, run, cleanup=None):
        # ``cleanup``: what the job would have removed when it ended, run
        # here when it never runs (cancelled by the shutdown, or not
        # submitted). ImportZip's removeAfter -- SF-4b's upload -- is the
        # only one.
        executor = TaskExecutor.instance
        if executor.stopping.is_set():
            raise AssetPackError(STOPPED, "the app is shutting down")
        with self._lock:
            if self._job is not None and self._job.status == RUNNING:
                return BUSY, self._job.revision
            self._revision += 1
            job = _Job(self._revision, operation, game)
            self._job = job
        try:
            future = executor.submit(
                self._Run, job, label, run,
                task_name=f"asset {operation} #{job.revision}")
        except Exception as failure:
            # Not queued (NestedSubmitError from a job lane, ...): the job
            # is settled here, or it would stay RUNNING and every later
            # start BUSY.
            logger.opt(exception=True).error(
                f"Asset {operation} #{job.revision} could not be started")
            self._Finish(job, ERROR, FAILED,
                         f"the job could not be started: {failure}")
            if cleanup is not None:
                cleanup()
            raise AssetPackError(
                FAILED, f"the job could not be started: {failure}") from None

        def settleCancelled(done):
            # Cancelled by the shutdown before it ran (or submitted after
            # it): the job never runs, so it is settled here.
            if done.cancelled():
                self._Finish(job, ERROR, STOPPED, "the app is shutting down")
                if cleanup is not None:
                    cleanup()

        future.add_done_callback(settleCancelled)
        return STARTED, job.revision

    # ── the upload (SF-4b-1) ─────────────────────────────────────────────

    def ReceiveUpload(self, stream, length, fileName=None):
        """Receive a ZIP of ``length`` bytes from ``stream`` (the request's
        body: read UPLOAD_CHUNK at a time on the calling thread, the web
        server's) into UPLOADS_DIR, probe it (ProbeZip) and hold it: (its
        id, the games it holds, the installed ones among them --
        ImportConflicts). A new upload replaces the one held -- and one
        still arriving, which then fails SUPERSEDED -- at its start. The
        free space is checked before anything is written (the held upload's
        file, which this one replaces, counted as free), and the file is
        removed however the receive fails -- the installed games read
        (ImportConflicts) before it is held. The one held is removed when it
        is dropped (DropUpload) or replaced; past UPLOAD_EXPIRY_SECS it is
        no longer held, and removed when the slot is next looked at
        (_HeldUpload) or at the next start (RemoveStaleUploads);
        ImportUpload takes it before. ``fileName``: the name the client's
        file had -- it names a game at the ZIP's top (ZipNameHint) -- never
        logged. The path is never answered or logged. AssetPackError
        NOT_A_ZIP (empty) / TOO_LARGE (over MAX_UPLOAD_BYTES) / INCOMPLETE
        (less than ``length`` arrived, or the read failed) / SUPERSEDED (a
        newer upload replaced it) / DISK_FULL / STOPPED / FAILED, and
        ProbeZip's."""
        if not isinstance(length, int) or isinstance(length, bool) or \
                length <= 0:
            raise AssetPackError(NOT_A_ZIP, "the upload is empty")
        if length > MAX_UPLOAD_BYTES:
            raise AssetPackError(
                TOO_LARGE, f"an upload is at most {MAX_UPLOAD_BYTES} bytes")
        executor = TaskExecutor.instance
        if executor is not None and executor.stopping.is_set():
            raise AssetPackError(STOPPED, "the app is shutting down")
        folder = _UploadsRoot()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as error:
            raise AssetPackError(
                FAILED, "the upload folder could not be made: "
                f"{_ErrorText(error)}") from None
        with self._lock:
            replaced = self._upload
        # The held upload's file goes when this one starts: its room counts
        # (it is not removed first: a refusal here leaves it held).
        reclaim = 0
        if replaced is not None:
            try:
                reclaim = os.path.getsize(replaced["path"])
            except OSError:
                pass
        free = _FreeBytes(folder)
        if free is not None and free + reclaim < length + DISK_MARGIN_BYTES:
            raise AssetPackError(
                DISK_FULL, f"not enough free space: {length} bytes needed, "
                f"{free} free" + (f" and {reclaim} the upload it replaces "
                                  "frees" if reclaim else ""))
        path = os.path.join(
            folder, f"{UPLOAD_PREFIX}{secrets.token_hex(8)}.zip")
        with self._lock:
            previous, self._upload = self._upload, None
            self._uploadGeneration += 1
            generation = self._uploadGeneration
            # Before the file exists: RemoveStaleUploads leaves it.
            self._receiving.add(path)
        if previous is not None:
            logger.info("Asset upload: replaced by a newer one")
            _DropSlot(previous)
        held = False
        try:
            received = self._Receive(stream, length, path, generation)
            nameHint = ZipNameHint(fileName) or ""
            names = ProbeZip(path, nameHint)
            # Before it is held: a failure here removes the file, and
            # nothing after the hold can fail.
            conflicts = ImportConflicts(names)
            uploadId = secrets.token_urlsafe(16)
            with self._lock:
                if generation != self._uploadGeneration:
                    raise AssetPackError(SUPERSEDED,
                                         "a newer upload replaced it")
                self._upload = {
                    "id": uploadId, "path": path, "games": list(names),
                    "nameHint": nameHint,
                    "expiresAt": time.monotonic() + UPLOAD_EXPIRY_SECS}
                held = True
        finally:
            with self._lock:
                self._receiving.discard(path)
            if not held:
                _Remove(path)
        logger.info(f"Asset upload: received {received} bytes, "
                    f"{len(names)} game(s)")
        return uploadId, list(names), conflicts

    def _Receive(self, stream, length, path, generation):
        # The body into ``path``, which this creates: exactly ``length``
        # bytes, or AssetPackError. The bytes received. A read returns at
        # most what it was asked for -- werkzeug's request.stream is a
        # LimitedStream at the declared length: no more than ``length``
        # ever arrives.
        executor = TaskExecutor.instance
        received = 0
        try:
            with open(path, "xb") as out:
                while received < length:
                    if executor is not None and executor.stopping.is_set():
                        raise AssetPackError(STOPPED,
                                             "the app is shutting down")
                    with self._lock:
                        superseded = generation != self._uploadGeneration
                    if superseded:
                        raise AssetPackError(SUPERSEDED,
                                             "a newer upload replaced it")
                    want = min(UPLOAD_CHUNK, length - received)
                    try:
                        chunk = stream.read(want)
                    except Exception as error:
                        # The client went away (werkzeug's
                        # ClientDisconnected), the connection failed.
                        raise AssetPackError(
                            INCOMPLETE, f"the upload stopped after "
                            f"{received} of {length} bytes "
                            f"({type(error).__name__})") from None
                    if not chunk:
                        raise AssetPackError(
                            INCOMPLETE, f"the upload ended after {received} "
                            f"of {length} bytes")
                    out.write(chunk)
                    received += len(chunk)
        except OSError as error:
            full = getattr(error, "errno", None) == 28 or \
                getattr(error, "winerror", None) in (39, 112)
            raise AssetPackError(
                DISK_FULL if full else FAILED,
                f"the upload could not be written: {_ErrorText(error)}"
            ) from None
        return received

    def UploadGames(self, uploadId):
        """(games, the installed ones among them) of the upload held by
        that id. AssetPackError UNKNOWN_UPLOAD when none is (never sent,
        imported, dropped, replaced or expired)."""
        with self._lock:
            slot, expired = self._HeldUpload(uploadId)
        if expired is not None:
            _DropSlot(expired, "expired")
        if slot is None:
            raise AssetPackError(UNKNOWN_UPLOAD, "no such upload")
        return list(slot["games"]), ImportConflicts(slot["games"])

    def ImportUpload(self, uploadId, overwrite):
        """Import the upload held by that id (ImportZip, which owns the file
        from here: removed when the job ends, however it ends); (STARTED,
        revision) or (BUSY, the running job's revision) -- busy, the upload
        stays held under that id, its expiry unchanged: a job that started
        while ImportZip probed it as well (held again, _HoldAgain).
        ``overwrite`` as ImportZip's. AssetPackError UNKNOWN_UPLOAD, and
        ImportZip's (the upload is then removed)."""
        with self._lock:
            if self._job is not None and self._job.status == RUNNING:
                return BUSY, self._job.revision
            slot, expired = self._HeldUpload(uploadId)
            if slot is not None:
                self._upload = None
                self._taken.add(slot["path"])
                generation = self._uploadGeneration
        if expired is not None:
            _DropSlot(expired, "expired")
        if slot is None:
            raise AssetPackError(UNKNOWN_UPLOAD, "no such upload")
        logger.info("Asset upload: taken for an import")
        answer = self.ImportZip(slot["path"], overwrite, slot["nameHint"],
                                removeAfter=True)
        if answer[0] == BUSY:
            self._HoldAgain(slot, generation)
        return answer

    def _HoldAgain(self, slot, generation):
        # A taken upload whose import a job beat (BUSY): held again under its
        # id, its expiry unchanged -- unless a newer upload started since
        # (one at a time), which replaced it.
        with self._lock:
            self._taken.discard(slot["path"])
            again = self._upload is None and \
                generation == self._uploadGeneration
            if again:
                self._upload = slot
        if again:
            logger.info("Asset upload: held again (another job started "
                        "first)")
        else:
            _DropSlot(slot, "replaced by a newer one")

    def _ReleaseUpload(self, path):
        # An upload an import took, done with (its job ended or never ran,
        # or the import was refused): its file removed, and no longer one
        # RemoveStaleUploads leaves (what cannot be removed now, the next
        # start's removes).
        _Remove(path)
        with self._lock:
            self._taken.discard(path)

    def DropUpload(self, uploadId):
        """Remove the upload held by that id; whether one was."""
        with self._lock:
            slot, expired = self._HeldUpload(uploadId)
            if slot is not None:
                self._upload = None
        if expired is not None:
            _DropSlot(expired, "expired")
        if slot is None:
            return False
        logger.info("Asset upload: dropped")
        _DropSlot(slot)
        return True

    def _HeldUpload(self, uploadId):
        # With _lock held: (the slot held by that id, or None; the slot
        # held, taken out when it has expired -- whatever id was asked: no
        # timer removes it -- for the caller to remove outside the lock).
        slot = self._upload
        if slot is not None and time.monotonic() >= slot["expiresAt"]:
            self._upload = None
            return None, slot
        if slot is None or not isinstance(uploadId, str) or \
                not _UPLOAD_ID.match(uploadId) or \
                not hmac.compare_digest(slot["id"], uploadId):
            return None, None
        return slot, None

    def RemoveStaleUploads(self):
        """Remove what an earlier run left in UPLOADS_DIR (the process ended
        while an upload was held or arriving, or an import's ZIP could not
        be removed). Each shell calls it at the start of its first scan,
        beside RestoreReplaced. Leaves the upload held, the ones arriving
        and the ones an import took. How many files it removed; logs
        (counts, no names) and returns 0 on an unexpected failure."""
        try:
            folder = _UploadsRoot()
            try:
                names = os.listdir(folder)
            except FileNotFoundError:
                return 0
            # Listed first: a file listed was made before, so its receive
            # had registered it by now (ReceiveUpload: before the file).
            with self._lock:
                keep = set(self._receiving) | set(self._taken)
                if self._upload is not None:
                    keep.add(self._upload["path"])
            kept = {os.path.normcase(path) for path in keep}
            removed = left = 0
            for name in names:
                path = os.path.join(folder, name)
                if os.path.normcase(path) in kept or _IsLink(path) or \
                        not os.path.isfile(path):
                    continue
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    left += 1
            gone = {path for path in keep if not os.path.exists(path)}
            with self._lock:
                self._taken -= gone
            if removed or left:
                logger.info(f"Asset upload: removed {removed} file(s) an "
                            f"earlier run left" + (
                                f", {left} could not be removed"
                                if left else ""))
            return removed
        except Exception:
            logger.opt(exception=True).error(
                "Asset upload: the files an earlier run left could not be "
                "removed")
            return 0

    # ── the games moved aside ────────────────────────────────────────────

    def RestoreReplaced(self):
        """Put back the games a job moved aside and did not (_MoveIn): when
        user_data/games/<name> is missing, the newest copy goes back; one
        whose replacement went in (DISCARD_MARKER) is removed again
        instead. Each shell calls it at the start of its first scan -- a
        process that died between a job's two moves left an installed game
        missing until the next job, so the list lacked it -- and each job
        does at its start. Not while a job runs (that job did it at its
        start, and it moves games): False at once, without waiting.
        Whether a game was put back. File moves, on the calling thread (the
        shells': their scan job); logs and returns False on an unexpected
        failure, so the scan after it runs."""
        if not self._filesLock.acquire(blocking=False):
            return False
        try:
            return _RestoreReplaced()
        except Exception:
            logger.opt(exception=True).error(
                "Asset packs: the games moved aside could not be looked at")
            return False
        finally:
            self._filesLock.release()

    # ── running a job ────────────────────────────────────────────────────

    def _Run(self, job, label, run):
        # The job's files: one at a time with RestoreReplaced (a shell's
        # startup one may be running; it is short).
        with self._filesLock:
            self._RunJob(job, label, run)

    def _RunJob(self, job, label, run):
        footer = None
        status, error, message = DONE, None, None
        stopped = False
        restored = False
        try:
            # In the try: a footer that cannot be shown fails the job rather
            # than leave it RUNNING (every later start BUSY).
            footer = _Footer(label)
            job.footer = footer
            _RemoveStaleStaging()
            # A game put back is a game moved in: the scan below shows it.
            restored = _RestoreReplaced()
            run(job)
        except _Cancelled:
            status = CANCELLED
            logger.info(f"Asset {job.operation} #{job.revision}: cancelled")
        except _Stopped:
            status, error, message = ERROR, STOPPED, "the app is shutting down"
            stopped = True
            logger.info(f"Asset {job.operation} #{job.revision}: stopped by "
                        "the shutdown")
        except _JobError as failure:
            status, error, message = ERROR, failure.code, failure.message
            logger.warning(f"Asset {job.operation} #{job.revision} failed: "
                           f"{failure.code}: {failure.message}")
        except AssetPackError as failure:
            status, error, message = ERROR, failure.code, failure.message
            logger.warning(f"Asset {job.operation} #{job.revision} failed: "
                           f"{failure.code}: {failure.message}")
        except OSError as failure:
            full = getattr(failure, "errno", None) == 28 or \
                getattr(failure, "winerror", None) in (39, 112)
            status = ERROR
            error = DISK_FULL if full else FAILED
            message = str(failure)
            logger.opt(exception=True).error(
                f"Asset {job.operation} #{job.revision} failed")
        except Exception as failure:
            status, error, message = ERROR, FAILED, str(failure)
            logger.opt(exception=True).error(
                f"Asset {job.operation} #{job.revision} failed")
        try:
            with self._lock:
                imported = list(job.imported)
            if (imported or restored) and not stopped:
                self._Set(job, stage=SCAN)
                self._Scan()
        finally:
            # The outcome first, then the footer drops the job: a reader
            # told by the `status` notice reads the outcome.
            self._Finish(job, status, error, message)
            if footer is not None:
                try:
                    footer.Close()
                except Exception:
                    logger.opt(exception=True).error(
                        "Asset packs: the footer could not drop the job")

    def _Scan(self):
        scan = self.scan
        try:
            if scan is not None:
                scan()
            elif GameAssetCatalog.instance is not None:
                logger.warning("Asset packs: no shell scan set; the catalog "
                               "is scanned alone")
                GameAssetCatalog.instance.ScanGames()
        except Exception:
            logger.opt(exception=True).error(
                "Asset packs: the games could not be scanned again")

    def _Finish(self, job, status, error, message):
        with self._lock:
            if job.status != RUNNING:
                return
            job.status = status
            job.error = error
            job.message = message
            job.stage = None
        logger.info(f"Asset {job.operation} #{job.revision}: {status}"
                    + (f" ({error})" if error else ""))

    def _Set(self, job, **fields):
        with self._lock:
            for name, value in fields.items():
                setattr(job, name, value)

    def _Check(self, job):
        if TaskExecutor.instance.stopping.is_set():
            raise _Stopped()
        if job.cancel.is_set():
            raise _Cancelled()

    def _Progress(self, job, done, total, footerDone, footerTotal,
                  force=False):
        with self._lock:
            job.done = done
            job.total = total
        job.footer.Progress(footerDone, footerTotal, force)

    # ── the install ──────────────────────────────────────────────────────

    def _RunInstall(self, job, code, plan):
        files = []
        # The packs' merged archives are written next to the parts: a part
        # named as one would be overwritten by it.
        mergedNames = {f"{packName}.7z".casefold() for packName, _pack in plan}
        for packName, pack in plan:
            entries = pack.get("files")
            entries = entries if isinstance(entries, dict) else {}
            names = sorted(entries)
            if not names:
                raise _JobError(BAD_INDEX, f"{code} / {packName} has no file")
            for name in names:
                if not _FILE_NAME.match(name) or \
                        name.casefold() in mergedNames:
                    raise _JobError(
                        BAD_INDEX, f"{code} / {packName}: bad file name "
                        f"{name!r}")
                entry = entries[name]
                files.append((packName, name, _Int(
                    entry.get("size") if isinstance(entry, dict) else 0)))
        total = sum(size for _p, _n, size in files)
        if total > MAX_DOWNLOAD_BYTES:
            raise _JobError(TOO_LARGE, f"{total} bytes to download is over "
                            f"{MAX_DOWNLOAD_BYTES}")
        root = _GamesRoot()
        os.makedirs(root, exist_ok=True)
        _RequireSpace(root, total)
        staging = _NewStaging()
        try:
            parts = os.path.join(staging, "parts")
            extracted = os.path.join(staging, "extract")
            os.makedirs(parts)
            os.makedirs(extracted)
            totalKiB = max(1, total // 1024)
            self._Set(job, stage=DOWNLOAD)
            self._Progress(job, 0, total, 0, totalKiB, force=True)
            done = 0
            for packName, _pack in plan:
                downloaded = []
                for name in [n for p, n, _s in files if p == packName]:
                    self._Check(job)
                    self._Set(job, pack=packName, file=name)
                    target = os.path.join(parts, name)
                    done = self._Download(job, DOWNLOAD_BASE + name, target,
                                          done, total, totalKiB)
                    downloaded.append(target)
                # Split parts (.7z.001, .002, ...) concatenate in order.
                merged = os.path.join(parts, packName + ".7z")
                with open(merged, "wb") as out:
                    for part in downloaded:
                        with open(part, "rb") as source:
                            shutil.copyfileobj(source, out, _CHUNK)
                        _Remove(part)
                self._Check(job)
                self._Set(job, stage=EXTRACT, file=None)
                self._Progress(job, done, total, min(done // 1024, totalKiB),
                               totalKiB, force=True)
                _Extract7z(merged, extracted)
                _Remove(merged)
                self._Check(job)
                self._Set(job, stage=DOWNLOAD)
            self._Check(job)
            self._Set(job, stage=COPY, pack=None, file=None)
            dest = os.path.join(root, code)
            _MergeInto(extracted, dest)
            with self._lock:
                job.imported.append(code)
        finally:
            _RemoveStaging(staging)

    def _Download(self, job, url, target, done, total, totalKiB):
        try:
            with requests.get(url, stream=True, allow_redirects=True,
                              timeout=DOWNLOAD_TIMEOUT_SECS) as response:
                if response.status_code >= 400:
                    raise _JobError(
                        DOWNLOAD_FAILED,
                        f"HTTP {response.status_code} for "
                        f"{os.path.basename(target)}")
                with open(target, "wb") as out:
                    for chunk in response.iter_content(_CHUNK):
                        self._Check(job)
                        if not chunk:
                            continue
                        out.write(chunk)
                        done += len(chunk)
                        if done > MAX_DOWNLOAD_BYTES:
                            raise _JobError(
                                TOO_LARGE, "the download is over "
                                f"{MAX_DOWNLOAD_BYTES} bytes")
                        self._Progress(job, done, max(total, done),
                                       min(done // 1024, totalKiB), totalKiB)
        except requests.RequestException as failure:
            raise _JobError(DOWNLOAD_FAILED, str(failure)) from None
        return done

    # ── the imports ──────────────────────────────────────────────────────

    def _RunImportZip(self, job, path, overwrite, nameHint, removeAfter):
        staging = None
        try:
            root = _GamesRoot()
            os.makedirs(root, exist_ok=True)
            # Again here: the file may have changed since ImportZip probed it.
            _CheckCentralDirectory(path)
            try:
                z = zipfile.ZipFile(path)
            except (zipfile.BadZipFile, OSError, ValueError) as error:
                raise _JobError(NOT_A_ZIP,
                                f"not a readable ZIP: {_ErrorText(error)}")
            with z:
                games, entries, files = _ZipPlan(z, nameHint)
                staging = _NewStaging()
                extracted = os.path.join(staging, "extract")
                os.makedirs(extracted)
                wanted = {}
                for name, prefix in games.items():
                    if os.path.exists(os.path.join(root, name)) and \
                            not _Overwrites(overwrite, name):
                        with self._lock:
                            job.skipped.append(name)
                        continue
                    wanted[name] = prefix
                plan = []
                size = 0
                # An entry belongs to a game by its prefix, case-folded (one
                # folder on Windows).
                prefixes = [(name, prefix, _Folded(prefix))
                            for name, prefix in wanted.items()]
                for info, parts, written in entries:
                    folded = _Folded(parts)
                    for name, prefix, foldedPrefix in prefixes:
                        if folded[:len(prefix)] != foldedPrefix or \
                                len(parts) <= len(prefix):
                            continue
                        rel = parts[len(prefix):]
                        if _DisallowedPack(prefix, rel, files):
                            break
                        if not written:
                            self._SkippedEntry(job, info.filename)
                            break
                        plan.append((info, name, rel))
                        if not info.is_dir():
                            size += info.file_size
                        break
                with self._lock:
                    left = len(job.skippedEntries) + job.skippedEntriesMore
                if left:
                    logger.warning(
                        f"Asset importZip #{job.revision}: left out {left} "
                        "entries whose names Windows cannot write")
                _RequireSpace(root, size)
                self._Set(job, stage=EXTRACT)
                count = len(plan)
                self._Progress(job, 0, count, 0, count, force=True)
                for done, (info, name, rel) in enumerate(plan, 1):
                    self._Check(job)
                    target = os.path.join(extracted, name, *rel)
                    if not _Within(extracted, target):
                        raise _JobError(UNSAFE_ARCHIVE, "an entry would be "
                                        "written outside its folder")
                    if info.is_dir():
                        os.makedirs(target, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        try:
                            with z.open(info) as source, \
                                    open(target, "wb") as out:
                                while True:
                                    chunk = source.read(_CHUNK)
                                    if not chunk:
                                        break
                                    out.write(chunk)
                                    self._Check(job)
                        except (zipfile.BadZipFile, RuntimeError,
                                NotImplementedError, EOFError) as error:
                            raise _JobError(
                                NOT_A_ZIP, f"the ZIP could not be read: "
                                f"{error}")
                    self._Progress(job, done, count, done, count)
            self._Set(job, stage=COPY)
            for name in wanted:
                self._Check(job)
                staged = os.path.join(extracted, name)
                os.makedirs(staged, exist_ok=True)
                self._MoveIn(job, staged, os.path.join(root, name), name)
        finally:
            if staging is not None:
                _RemoveStaging(staging)
            if removeAfter:
                self._ReleaseUpload(path)

    def _RunImportFolder(self, job, path, overwrite):
        games = ProbeFolder(path)
        root = _GamesRoot()
        os.makedirs(root, exist_ok=True)
        wanted = []
        for name, folder in games:
            dest = os.path.join(root, name)
            if os.path.normcase(os.path.abspath(folder)) == \
                    os.path.normcase(dest):
                # The game's own folder: it is installed as it is.
                with self._lock:
                    job.imported.append(name)
                continue
            if os.path.exists(dest) and not _Overwrites(overwrite, name):
                with self._lock:
                    job.skipped.append(name)
                continue
            if os.path.normcase(os.path.abspath(folder)) == \
                    os.path.normcase(root) or _Within(folder, root):
                # The staging folder would be inside what is copied.
                self._Failed(job, name, "user_data/games is inside this "
                             "folder; it cannot be imported into itself")
                continue
            wanted.append((name, folder))
        # What the copy will copy (the same ignore): the progress' total and
        # the space it needs.
        count, size = 0, 0
        for _name, folder in wanted:
            ignore = _CopyIgnore(folder)
            for dirpath, dirnames, filenames in os.walk(folder):
                skipped = ignore(dirpath, dirnames + filenames)
                dirnames[:] = [d for d in dirnames if d not in skipped]
                for filename in filenames:
                    if filename in skipped:
                        continue
                    full = os.path.join(dirpath, filename)
                    count += 1
                    try:
                        size += os.path.getsize(full)
                    except OSError:
                        pass
        _RequireSpace(root, size)
        staging = _NewStaging()
        try:
            extracted = os.path.join(staging, "extract")
            os.makedirs(extracted)
            self._Set(job, stage=COPY)
            copied = [0]
            self._Progress(job, 0, count, 0, count, force=True)

            def copy(source, target):
                self._Check(job)
                shutil.copy2(source, target)
                copied[0] += 1
                self._Progress(job, copied[0], count, copied[0], count)

            for name, folder in wanted:
                self._Check(job)
                staged = os.path.join(extracted, name)
                try:
                    shutil.copytree(folder, staged, copy_function=copy,
                                    ignore=_CopyIgnore(folder))
                except shutil.Error as failure:
                    self._Failed(job, name, str(failure))
                    continue
                except OSError as failure:
                    self._Failed(job, name, str(failure))
                    continue
                self._MoveIn(job, staged, os.path.join(root, name), name)
        finally:
            _RemoveStaging(staging)

    def _Failed(self, job, name, message):
        logger.error(f"Game asset import failed for {name}: {message}")
        with self._lock:
            job.failed.append({"name": name, "message": message})

    def _SkippedEntry(self, job, name):
        with self._lock:
            if len(job.skippedEntries) < MAX_SKIPPED_ENTRIES:
                job.skippedEntries.append(name)
            else:
                job.skippedEntriesMore += 1

    def _MoveIn(self, job, staged, dest, name):
        """Put a staged game in place. An installed one is first moved
        aside -- to user_data/games/.autona-replaced-<name>-*/<name>, not
        into the staging folder, which the job removes -- and removed once
        the new one is in (_Discard: what cannot be removed then is marked,
        never put back, and removed again later). When the new one cannot
        go in, the old one is
        put back; when that fails too, the aside is kept (never removed
        here), named in the log and the failure, and put back by the next
        job when its place is still empty (_RestoreReplaced). A failure is
        the game's (job.failed), not the job's."""
        container = aside = None
        try:
            if os.path.lexists(dest):
                if _IsLink(dest) or not os.path.isdir(dest):
                    raise OSError(f"{name} is not a folder")
                container = tempfile.mkdtemp(
                    prefix=f"{REPLACED_PREFIX}{name}-",
                    dir=os.path.dirname(dest))
                aside = os.path.join(container, name)
                try:
                    _Replace(dest, aside)
                except OSError:
                    _RemoveEmpty(container)
                    container = aside = None
                    raise
            try:
                _Replace(staged, dest)
            except OSError as failure:
                if aside is None:
                    raise
                try:
                    _Replace(aside, dest)
                except OSError as restore:
                    logger.error(
                        f"Game asset import: {name} could not be put back "
                        f"({restore}); the installed folder is kept at "
                        f"{aside}")
                    self._Failed(
                        job, name, f"{failure}; the installed folder could "
                        f"not be put back and is kept at {aside}")
                    return
                _RemoveEmpty(container)
                raise
        except OSError as failure:
            self._Failed(job, name, str(failure))
            return
        with self._lock:
            job.imported.append(name)
        if container is not None:
            # The installed copy is not wanted any more: marked, then
            # removed; what cannot be is removed again later.
            _Discard(container)


# ── extraction and moves ─────────────────────────────────────────────────

def _DisallowedPack(prefix, rel, files):
    # rel[0] is a pack of the game (a subfolder with its own config.json)
    # that is not allowed: left out of the import (ADR-002). ``files``: the
    # ZIP's files, case-folded -- names compare as Windows compares them
    # (``LOGO/CONFIG.JSON`` is the logo pack).
    if len(rel) < 2:
        return False
    child = rel[0].casefold()
    return child not in ALLOWED_PACKS and \
        (_Folded(prefix) + (child, "config.json")) in files


def _CopyIgnore(top):
    """copytree's ignore: no link or junction anywhere, and at the game's
    top no pack that is not allowed (a subfolder with its own config.json;
    its name compared case-folded, as Windows compares it)."""
    topKey = os.path.normcase(os.path.abspath(top))

    def ignore(directory, names):
        skipped = set()
        atTop = os.path.normcase(os.path.abspath(directory)) == topKey
        for name in names:
            full = os.path.join(directory, name)
            if _IsLink(full):
                skipped.add(name)
            elif atTop and name.casefold() not in ALLOWED_PACKS and \
                    os.path.isdir(full) and \
                    os.path.isfile(os.path.join(full, "config.json")):
                skipped.add(name)
        return skipped

    return ignore


def _Extract7z(archive, target):
    """Extract a 7z into ``target`` once every entry is checked: a safe
    relative name, no link or junction, at most MAX_ENTRIES entries and
    MAX_UNPACKED_BYTES unpacked, and room on the disk."""
    import py7zr  # lazy: a heavy import, only when installing
    try:
        with py7zr.SevenZipFile(archive, "r") as z:
            entries = list(z.files)
            if len(entries) > MAX_ENTRIES:
                raise _JobError(TOO_LARGE, f"the archive has more than "
                                f"{MAX_ENTRIES} entries")
            unpacked = 0
            for entry in entries:
                if entry.is_symlink or entry.is_junction or entry.is_socket:
                    raise _JobError(UNSAFE_ARCHIVE,
                                    "the archive holds a link")
                if ArchivePath(entry.filename) is None:
                    raise _JobError(
                        UNSAFE_ARCHIVE, "the archive has an entry that "
                        "would be written outside its folder")
                if not entry.is_directory:
                    unpacked += _Int(entry.uncompressed)
            if unpacked > MAX_UNPACKED_BYTES:
                raise _JobError(TOO_LARGE, f"the archive unpacks to more "
                                f"than {MAX_UNPACKED_BYTES} bytes")
            _RequireSpace(target, unpacked)
            z.extractall(path=target)
    except _JobError:
        raise
    except OSError:
        raise
    except Exception as failure:
        raise _JobError(EXTRACT_FAILED,
                        f"the archive could not be extracted: {failure}")


def _MergeInto(extracted, dest):
    """Move the extracted allowed packs into the game's folder, file by
    file (a file there already is replaced; its other files stay, as an
    extraction over the folder left them). What else the archives held is
    left out (ADR-002).

    Upstream builds each pack's 7z from the pack's folder
    (StreamHelperAssets' gen_config.py: ``7z a <code>.<pack>.7z
    games/<code>/<pack>``), so its entries are ``<pack>/...``: this puts
    in place what the window's extraction into user_data/games/<code>/
    did until SF-3b (checked against the real sf3ts.variant_icon pack,
    tests/test_asset_packs.py)."""
    os.makedirs(dest, exist_ok=True)
    for top in sorted(os.listdir(extracted)):
        source = os.path.join(extracted, top)
        if top.casefold() not in ALLOWED_PACKS or \
                not os.path.isdir(source) or _IsLink(source):
            logger.info(f"Asset install: left out {top!r} (not an allowed "
                        "pack)")
            continue
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames[:] = [d for d in dirnames
                           if not _IsLink(os.path.join(dirpath, d))]
            rel = os.path.relpath(dirpath, extracted)
            outDir = os.path.join(dest, rel)
            if not _Within(dest, outDir):
                raise _JobError(UNSAFE_ARCHIVE, "an entry would be written "
                                "outside the game's folder")
            os.makedirs(outDir, exist_ok=True)
            for filename in filenames:
                os.replace(os.path.join(dirpath, filename),
                           os.path.join(outDir, filename))


def _NewStaging():
    root = _GamesRoot()
    os.makedirs(root, exist_ok=True)
    return tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root)


def _RemoveStaleStaging():
    """What a job left in user_data/games when the process died: removed
    when the next job starts (one job runs at a time, so none is in use).
    The games moved aside are not in it (_RestoreReplaced)."""
    root = _GamesRoot()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        if name.startswith(STAGING_PREFIX):
            _RemoveStaging(os.path.join(root, name))


def _RemoveStaging(staging):
    if not _RemoveTree(staging):
        logger.warning(f"Asset packs: the staging folder {staging} could not "
                       "be removed whole; the next job removes it again")


def _Containers(root):
    """The folders games were moved aside into (``.autona-replaced-*``, no
    link), by name: (path, marked) -- marked: DISCARD_MARKER is in it."""
    found = []
    for name in _Children(root):
        if not name.startswith(REPLACED_PREFIX):
            continue
        container = os.path.join(root, name)
        if _IsLink(container) or not os.path.isdir(container):
            continue
        found.append((container,
                      os.path.lexists(os.path.join(container, DISCARD_MARKER))))
    return found


def _Restorable(container, child):
    """Whether a job may put back what is at ``container``/``child``: a
    folder (no link) with a game's name."""
    aside = os.path.join(container, child)
    return SafeName(child) and not _IsLink(aside) and os.path.isdir(aside)


def _RestoreReplaced():
    """The games moved aside (``.autona-replaced-*/<name>``) that a job could
    not put back, or that it left when the process died, looked at when a
    shell's first scan or the next job starts (RestoreReplaced): the newest
    copy of a game -- the folder's time: the copy moved aside last was the
    one installed last -- is put back when user_data/games/<name> is
    missing; the others, and every copy of an installed game, are kept and
    logged -- never removed here, one may be the only copy. A folder with
    DISCARD_MARKER is removed again instead, never put back (its
    replacement went in, and it may be partly removed). Whether a game was
    put back."""
    root = _GamesRoot()
    copies = {}
    looked = []
    for container, marked in _Containers(root):
        if marked:
            _Discard(container)
            continue
        looked.append(container)
        try:
            movedAt = os.stat(container).st_mtime
        except OSError:
            continue
        for child in _Children(container):
            if not _Restorable(container, child):
                logger.warning("Asset packs: left as it is: "
                               f"{os.path.join(container, child)}")
                continue
            copies.setdefault(child.casefold(), []).append(
                (movedAt, container, child))
    restored = False
    for key in sorted(copies):
        newest, *older = sorted(copies[key], reverse=True)
        _movedAt, container, child = newest
        aside = os.path.join(container, child)
        dest = os.path.join(root, child)
        if os.path.lexists(dest):
            for _at, other, name in copies[key]:
                logger.warning(
                    f"Asset packs: an earlier job's copy of {name} is kept "
                    f"at {os.path.join(other, name)} ({name} is installed; "
                    "remove it by hand when it is not needed)")
            continue
        try:
            _Replace(aside, dest)
        except OSError as failure:
            logger.error(f"Asset packs: {child} could not be put back "
                         f"from {aside}: {failure}")
            continue
        restored = True
        logger.warning(f"Asset packs: put back {child}, which an earlier "
                       f"job had moved aside (from {aside})")
        for _at, other, name in older:
            logger.warning(
                f"Asset packs: an older copy of {name} is kept at "
                f"{os.path.join(other, name)} (a newer one was put back; "
                "remove it by hand when it is not needed)")
    for container in looked:
        _RemoveEmpty(container)
    return restored


def _Discard(container):
    """Remove a folder a game was moved aside into, once that copy is not
    wanted (its replacement went in). DISCARD_MARKER is written first and
    removed last, so what cannot be removed now -- a file in use, a crash
    on the way -- is never put back (it may be partly gone) and is removed
    again by the next RestoreReplaced. Whether it is gone."""
    marker = os.path.join(container, DISCARD_MARKER)
    try:
        with open(marker, "a", encoding="utf-8"):
            pass
    except OSError as failure:
        logger.warning(f"Asset packs: {container} could not be marked for "
                       f"removal: {failure}")
    whole = True
    for child in _Children(container):
        if child == DISCARD_MARKER:
            continue
        path = os.path.join(container, child)
        if os.path.isdir(path) and not _IsLink(path):
            whole = _RemoveTree(path) and whole
        else:
            _Remove(path)
            whole = not os.path.lexists(path) and whole
    if whole:
        _Remove(marker)
        _RemoveEmpty(container)
    if os.path.lexists(container):
        logger.warning(f"Asset packs: {container} could not be removed whole "
                       "(a file in use?); it is never put back, and is "
                       "removed again when the next job or the app starts")
        return False
    return True


def _RemoveTree(path):
    """shutil.rmtree that clears the read-only attribute of what it could
    not remove and tries that once more (a file copied from a read-only
    source keeps the attribute, and Windows refuses to delete it). Whether
    ``path`` is gone afterwards; what is left is the caller's to log. The
    handler is ``onexc`` from Python 3.12 (``onerror`` is deprecated there)
    and ``onerror`` before; both pass (function, path, failure)."""
    def again(function, target, _failure):
        # Only rmtree's own deletions are tried again (a failed os.scandir
        # or os.lstat leaves nothing to retry; what it hides stays and is
        # seen below).
        if function not in (os.unlink, os.remove, os.rmdir):
            return
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=again)
    else:
        shutil.rmtree(path, onerror=again)
    return not os.path.lexists(path)


def _Children(folder):
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


def _Replace(source, target):
    """os.replace with a few short retries (StatePersistence's: a reader
    without FILE_SHARE_DELETE makes it fail for a moment on Windows)."""
    delay = _REPLACE_BACKOFF
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _RemoveEmpty(folder):
    try:
        os.rmdir(folder)
    except OSError:
        pass


def _Remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _DropSlot(slot, why=None):
    # An upload no longer held: its file removed (what cannot be is removed
    # by the next start's RemoveStaleUploads). ``why``: logged first.
    if why:
        logger.info(f"Asset upload: {why}")
    _Remove(slot["path"])
    if os.path.exists(slot["path"]):
        logger.warning("Asset upload: its file could not be removed; the "
                       "next start removes it")
