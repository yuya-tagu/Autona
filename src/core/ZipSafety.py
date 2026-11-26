"""A zip checked before it is unpacked, unpacked, and the unpacked folder
swapped in (ADR-009 C.1 / C.3 / D9): the parts an installer shares.

``CheckCentralDirectory`` comes first, on the file: zipfile.ZipFile()
reads the whole central directory into memory and makes an object of each
entry before anything can count them, so a zip whose end records list
more than ``maxEntries`` entries, or a directory larger than
``maxDirectoryBytes``, is refused from those records alone (read as
zipfile reads them, with struct; the same check as AssetPackService's
``_CheckCentralDirectory``, which stays its own until it moves here).

``Inspect`` then reads the opened zip's central directory and says what is
wrong with it -- nothing is written:

- a name that would leave the folder it is unpacked to: absolute, a drive,
  ``..``, a ``:`` (drives and alternate streams) -- UNSAFE_PATH;
- a part Windows cannot write: a refused or control character, a trailing
  dot or space, an empty or ``.`` part (``a//b``, ``./a``), an NTFS short
  name's ``~<digit>`` (``LONGFO~1``: it can name another entry) --
  BAD_NAME; a device name (``CON``, ``nul.txt``, ``COM1``) --
  RESERVED_NAME;
- with ``asciiOnly``, a name with a character outside printable ASCII --
  NOT_ASCII (a name not marked UTF-8 is decoded as cp437, so any byte
  over 0x7f lands here too);
- a symbolic link (a unix mode of S_IFLNK) or a Windows reparse point --
  LINK; an encrypted entry -- ENCRYPTED;
- two entries of one name (case-folded, ``\\`` read as ``/``), or a file
  that is also a folder of another entry -- DUPLICATE_NAME;
- more entries than ``maxEntries`` -- TOO_MANY_ENTRIES (nothing else is
  looked at); more declared bytes than ``maxExpandedBytes`` --
  EXPANDED_TOO_LARGE.

``\\`` counts as a separator: a zip made by ``Compress-Archive`` is read as
if it had ``/``. A folder entry's trailing ``/`` is dropped.

``PathLengths`` is the longest absolute file path and the longest folder
path the entries make under a folder (the caller holds them to Windows'
MAX_PATH: MAX_FILE_PATH for a file, MAX_FOLDER_PATH -- MAX_PATH - 12 --
for a folder, where long paths are not enabled).

``Extract`` writes the entries under a folder, ``strip`` leading parts
dropped (the ``<id>/`` of a plugin's zip), never over an existing file and
never outside the folder; a declared size is a ceiling (zipfile reads no
more than it; a shorter entry is merely shorter). What reading a damaged
or unsupported entry can raise is ``READ_ERRORS`` (zlib.error,
NotImplementedError for an unknown method such as deflate64, ...).

``Swap`` moves the current folder to ``.old`` and the new one in; a
``commit`` that raises after the move (the install record not written)
puts the old one back. ``CleanSwap`` is a launch's sweep of what a crash
left (``.part`` and ``.new`` removed, ``.old`` put back when the folder is
missing, else removed).

Logs name the folder's last part and an error's type and errno, never a
path (an OSError's text names the file: ADR-009 E.3).

Nothing is made, read or written on import (pitfalls #1); no Qt.
"""

import os
import re
import shutil
import stat
import struct
import time
import zipfile
import zlib
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from loguru import logger

try:
    import lzma
    _LZMA_ERRORS: tuple = (lzma.LZMAError,)
except ImportError:  # a Python built without lzma: zipfile has none either
    _LZMA_ERRORS = ()

# What Inspect reports (the installer's codes: ADR-009 leaves them to it).
ZIP_UNREADABLE = "zip_unreadable"
TOO_MANY_ENTRIES = "too_many_entries"
EXPANDED_TOO_LARGE = "expanded_too_large"
UNSAFE_PATH = "unsafe_path"
BAD_NAME = "bad_name"
NOT_ASCII = "not_ascii"
RESERVED_NAME = "reserved_name"
LINK = "link"
ENCRYPTED = "encrypted"
DUPLICATE_NAME = "duplicate_name"
PATH_TOO_LONG = "path_too_long"
DIRECTORY_TOO_LARGE = "directory_too_large"

# What opening or reading a damaged / unsupported zip can raise besides
# OSError: zipfile's own, a broken deflate stream, an unknown or
# unsupported compression method, an encrypted entry, lzma's.
READ_ERRORS: tuple = (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError,
                      ValueError, zlib.error, NotImplementedError,
                      RuntimeError) + _LZMA_ERRORS

# Where a path is no longer creatable without long paths: a file, a folder
# (CreateDirectory leaves room for an 8.3 name: MAX_PATH - 12).
MAX_FILE_PATH = 259
MAX_FOLDER_PATH = 247

NEW_SUFFIX = ".new"
OLD_SUFFIX = ".old"
PART_SUFFIX = ".part"

_BAD_CHARS = frozenset('<>:"|?*')
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"COM{digit}" for digit in "0123456789"]
    + [f"LPT{digit}" for digit in "0123456789"]
    # The superscript digits Windows also reserves.
    + [f"COM{digit}" for digit in "¹²³"]
    + [f"LPT{digit}" for digit in "¹²³"])
# Windows' FILE_ATTRIBUTE_REPARSE_POINT in a DOS-made entry's attributes.
_SHORT_NAME = re.compile(r"~[0-9]")
_REPARSE_POINT = 0x400
_ENCRYPTED_FLAG = 0x1
_CHUNK = 1024 * 1024
_RENAME_ATTEMPTS = 5
_RENAME_BACKOFF = 0.2


@dataclass(frozen=True)
class ZipEntry:
    """One entry: its ZipInfo, its path parts (``\\`` read as ``/``) and
    whether it names a folder."""
    info: zipfile.ZipInfo
    parts: Tuple[str, ...]
    is_dir: bool


@dataclass(frozen=True)
class ZipPlan:
    """What Inspect found: the entries it could read (in the zip's order),
    their declared unpacked size, and the problems (empty when fine; each
    code once, in the order found)."""
    entries: Tuple[ZipEntry, ...]
    expanded_bytes: int
    problems: Tuple[str, ...]


class ZipStopped(Exception):
    """``Extract``'s ``step`` said to stop."""


def NameProblem(name: str, *, asciiOnly: bool = True
                ) -> Tuple[Optional[Tuple[str, ...]], Optional[str]]:
    """An entry's name as (its path parts, None), or (None, the problem).
    A folder's trailing ``/`` is dropped."""
    if not isinstance(name, str) or not name:
        return None, BAD_NAME
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or ":" in normalised:
        return None, UNSAFE_PATH
    if normalised.endswith("/"):
        normalised = normalised[:-1]
    parts = tuple(normalised.split("/"))
    if ".." in parts:
        return None, UNSAFE_PATH
    for part in parts:
        if part in ("", "."):
            return None, BAD_NAME
        if any(ch in _BAD_CHARS or ord(ch) < 32 for ch in part):
            return None, BAD_NAME
        if part[-1] in ". " or _SHORT_NAME.search(part):
            return None, BAD_NAME
        if part.split(".")[0].upper() in _RESERVED:
            return None, RESERVED_NAME
    if asciiOnly and any(ord(ch) > 0x7e for ch in normalised):
        return None, NOT_ASCII
    return parts, None


def _IsLink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        return True
    return info.create_system == 0 and bool(info.external_attr
                                            & _REPARSE_POINT)


def _Folded(parts: Sequence[str]) -> Tuple[str, ...]:
    return tuple(part.casefold() for part in parts)


# The end records, as zipfile (3.12) reads them.
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
_CENTRAL_LENGTHS_AT = 28
_MAX_COMMENT = (1 << 16) - 1
MAX_DIRECTORY_BYTES = 64 * 1024 * 1024


class _NotAZip(Exception):
    pass


def _EndRecord(f, size):
    """(entries, directory size, directory offset, where the record
    starts, ZIP64) found where zipfile finds them (zipfile._EndRecData /
    _EndRecData64, Python 3.12), or None. _NotAZip where zipfile refuses
    (split over disks, a ZIP64 locator with no room for its record)."""
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
        raise _NotAZip()
    if location < _LOCATOR_SIZE + _EOCD64_SIZE:
        raise _NotAZip()
    f.seek(location - _LOCATOR_SIZE - _EOCD64_SIZE)
    data = f.read(_EOCD64_SIZE)
    if len(data) != _EOCD64_SIZE:
        return entries, directorySize, directoryOffset, location, False
    (sig, _size, _made, _needed, _disk, _diskStart, _here, entries64,
     directorySize64, directoryOffset64) = struct.unpack(_EOCD64_FORMAT, data)
    if sig != _EOCD64:
        return entries, directorySize, directoryOffset, location, False
    return entries64, directorySize64, directoryOffset64, location, True


def CheckCentralDirectory(path: str, *, maxEntries: int,
                          maxDirectoryBytes: int = MAX_DIRECTORY_BYTES
                          ) -> Optional[str]:
    """The problem of the zip at ``path`` before zipfile.ZipFile() reads
    it, or None: TOO_MANY_ENTRIES when the end records list more than
    ``maxEntries`` entries or the directory, walked header by header as
    zipfile walks it (it stops at the directory's size, not at the stated
    count), holds more; DIRECTORY_TOO_LARGE over ``maxDirectoryBytes``;
    ZIP_UNREADABLE for no end record, a directory outside the file, a bad
    header, a file that cannot be read."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            record = _EndRecord(f, size)
            if record is None:
                return ZIP_UNREADABLE
            entries, directorySize, directoryOffset, location, zip64 = record
            if entries > maxEntries:
                return TOO_MANY_ENTRIES
            if directorySize > maxDirectoryBytes:
                return DIRECTORY_TOO_LARGE
            # Where zipfile reads the directory from: just before the end
            # records (the stated offset only says how much was prepended).
            start = location - directorySize - (
                _EOCD64_SIZE + _LOCATOR_SIZE if zip64 else 0)
            if start < 0 or directoryOffset > start:
                return ZIP_UNREADABLE
            f.seek(start)
            directory = f.read(directorySize)
    except (OSError, _NotAZip, struct.error):
        return ZIP_UNREADABLE
    at = count = 0
    while at < directorySize:
        if count >= maxEntries:
            return TOO_MANY_ENTRIES
        if (len(directory) - at < _CENTRAL_HEADER_SIZE
                or directory[at:at + 4] != _CENTRAL_HEADER):
            return ZIP_UNREADABLE
        nameLength, extraLength, commentLength = struct.unpack_from(
            "<3H", directory, at + _CENTRAL_LENGTHS_AT)
        at += _CENTRAL_HEADER_SIZE + nameLength + extraLength + commentLength
        count += 1
    return None


def Inspect(archive: zipfile.ZipFile, *, maxEntries: int,
            maxExpandedBytes: int, asciiOnly: bool = True) -> ZipPlan:
    """Check ``archive``'s entries (its central directory; nothing is
    unpacked or written)."""
    infos = archive.infolist()
    if len(infos) > maxEntries:
        return ZipPlan((), 0, (TOO_MANY_ENTRIES,))
    problems: List[str] = []

    def add(code):
        if code not in problems:
            problems.append(code)

    entries, files, folders, total = [], set(), set(), 0
    for info in infos:
        parts, problem = NameProblem(info.filename, asciiOnly=asciiOnly)
        if problem:
            add(problem)
            continue
        if _IsLink(info):
            add(LINK)
            continue
        if info.flag_bits & _ENCRYPTED_FLAG:
            add(ENCRYPTED)
            continue
        isDir = info.is_dir()
        key = _Folded(parts)
        if key in files or (key in folders and not isDir):
            add(DUPLICATE_NAME)
            continue
        if isDir:
            if key in folders:
                add(DUPLICATE_NAME)
                continue
            folders.add(key)
        else:
            files.add(key)
            total += info.file_size
        entries.append(ZipEntry(info, parts, isDir))
    # A file that another entry needs as its folder ("bin" and "bin/x").
    for entry in entries:
        key = _Folded(entry.parts)
        for depth in range(1, len(key)):
            if key[:depth] in files:
                add(DUPLICATE_NAME)
                break
    if total > maxExpandedBytes:
        add(EXPANDED_TOO_LARGE)
    return ZipPlan(tuple(entries), total, tuple(problems))


def PathLengths(root: str, entries: Iterable[ZipEntry], *,
                strip: int = 0) -> Tuple[int, int]:
    """(the longest file path, the longest folder path) ``entries`` make
    under ``root`` (absolute; ``strip`` leading parts dropped). ``root``
    itself is a folder; a file's parents are folders."""
    root = os.path.abspath(root)
    longestFile, longestFolder = 0, len(root)
    for entry in entries:
        rest = entry.parts[strip:]
        if not rest:
            continue
        folders = rest if entry.is_dir else rest[:-1]
        if folders:
            longestFolder = max(longestFolder,
                                len(os.path.join(root, *folders)))
        if not entry.is_dir:
            longestFile = max(longestFile, len(os.path.join(root, *rest)))
    return longestFile, longestFolder


def Within(root: str, path: str) -> bool:
    """Whether ``path`` is under ``root`` (not ``root`` itself)."""
    root = os.path.normcase(os.path.abspath(root))
    path = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([root, path]) == root and path != root
    except ValueError:
        return False


def Extract(archive: zipfile.ZipFile, entries: Iterable[ZipEntry],
            target: str, *, strip: int = 0,
            step: Optional[Callable[[], bool]] = None) -> int:
    """Write ``entries`` (Inspect's, without problems) under ``target``
    (made when missing), ``strip`` leading parts dropped; an entry with
    nothing left is the folder itself. Returns the bytes written. Raises
    OSError (an existing file, a disk), zipfile.BadZipFile / EOFError (a
    damaged entry), ValueError (a path outside ``target``), ZipStopped
    when ``step()`` is true (asked between chunks)."""
    os.makedirs(target, exist_ok=True)
    written = 0
    for entry in entries:
        if step is not None and step():
            raise ZipStopped()
        rest = entry.parts[strip:]
        if not rest:
            continue
        path = os.path.join(target, *rest)
        if not Within(target, path):
            raise ValueError("an entry leaves the folder")
        if entry.is_dir:
            os.makedirs(path, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with archive.open(entry.info) as source, open(path, "xb") as out:
            while True:
                chunk = source.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                if step is not None and step():
                    raise ZipStopped()
    return written


# ── the folders ─────────────────────────────────────────────────────────

def Why(error: BaseException) -> str:
    """An error for a log: its type and errno, never its text (an
    OSError's names the file)."""
    number = getattr(error, "errno", None)
    return type(error).__name__ + (f" errno {number}" if number else "")


def RemoveTree(path: str) -> None:
    """Remove folder ``path`` (read-only files included). Raises
    OSError."""
    def again(function, failed, _info):
        os.chmod(failed, stat.S_IWRITE)
        function(failed)
    shutil.rmtree(path, onerror=again)


def Rename(source: str, target: str, *,
           sleep: Callable[[float], None] = time.sleep) -> None:
    """os.rename, tried again a few times: on Windows an antivirus that
    reads a file just written holds it for a moment. Raises OSError."""
    delay = _RENAME_BACKOFF
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.rename(source, target)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            sleep(delay)
            delay *= 2


def Replace(source: str, target: str, *,
            sleep: Callable[[float], None] = time.sleep) -> None:
    """os.replace with Rename's retries (an antivirus holding the target).
    Raises OSError."""
    delay = _RENAME_BACKOFF
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            sleep(delay)
            delay *= 2


def Swap(new: str, current: str, old: str, *,
         commit: Optional[Callable[[], None]] = None,
         sleep: Callable[[float], None] = time.sleep) -> bool:
    """Move ``current`` (when there is one) to ``old`` and ``new`` to
    ``current``, then call ``commit``. When the move in fails, or
    ``commit`` raises, the old folder is put back (best effort, logged)
    and the error raised; ``new`` is removed when ``commit`` raised. The
    old folder is removed once all went well (left for CleanSwap when it
    cannot be). Returns whether there was a current folder."""
    if os.path.isdir(old):
        if os.path.lexists(current):
            RemoveTree(old)
        else:
            # A crash's, which CleanSwap could not put back: back first.
            Rename(old, current, sleep=sleep)
    moved = False
    if os.path.lexists(current):
        Rename(current, old, sleep=sleep)
        moved = True
    try:
        Rename(new, current, sleep=sleep)
    except OSError:
        if moved:
            _PutBack(old, current, sleep)
        raise
    if commit is not None:
        try:
            commit()
        except BaseException:
            try:
                Rename(current, new, sleep=sleep)
            except OSError as error:
                logger.error(f"Swap: the new folder could not be moved back "
                             f"out ({Why(error)})")
            else:
                if moved:
                    _PutBack(old, current, sleep)
                try:
                    RemoveTree(new)
                except OSError:
                    logger.warning("Swap: the new folder was left for the "
                                   "next start")
            raise
    if moved:
        try:
            RemoveTree(old)
        except OSError as error:
            logger.warning(f"Swap: the old folder could not be removed "
                           f"({Why(error)}); removed at the next "
                           f"start")
    return moved


def _PutBack(old: str, current: str, sleep) -> None:
    try:
        Rename(old, current, sleep=sleep)
    except OSError as error:
        logger.error(f"Swap: the old folder could not be put back "
                     f"({Why(error)}); the next start puts it back")


def CleanSwap(current: str, *, sleep: Callable[[float], None] = time.sleep
              ) -> None:
    """What a crash left around ``current``: ``<current>.part`` and
    ``.new`` removed, ``.old`` put back when ``current`` is missing, else
    removed. Each step on its own; logged, never raises."""
    part, new, old = (current + PART_SUFFIX, current + NEW_SUFFIX,
                      current + OLD_SUFFIX)
    name = os.path.basename(current)
    try:
        if os.path.isfile(part):
            os.remove(part)
            logger.info(f"Swap sweep: {name}: an unfinished download was "
                        f"removed")
    except OSError as error:
        logger.warning(f"Swap sweep: {name}: an unfinished download could "
                       f"not be removed ({Why(error)})")
    try:
        if os.path.isdir(new):
            RemoveTree(new)
            logger.info(f"Swap sweep: {name}: an unfinished install was "
                        f"removed")
    except OSError as error:
        logger.warning(f"Swap sweep: {name}: an unfinished install could "
                       f"not be removed ({Why(error)})")
    try:
        if os.path.isdir(old):
            if os.path.lexists(current):
                RemoveTree(old)
                logger.info(f"Swap sweep: {name}: the previous folder was "
                            f"removed")
            else:
                Rename(old, current, sleep=sleep)
                logger.warning(f"Swap sweep: {name}: the previous folder was "
                               f"put back (an install did not finish)")
    except OSError as error:
        logger.warning(f"Swap sweep: {name}: the previous folder could not "
                       f"be cleaned ({Why(error)})")
