"""A zip checked before it is unpacked, unpacked, and the unpacked folder
swapped in (ADR-009 C.1 / C.3 / D9): the parts an installer shares.

``Inspect`` reads a zip's central directory only and says what is wrong
with it -- nothing is written:

- a name that would leave the folder it is unpacked to: absolute, a drive,
  ``..``, a ``:`` (drives and alternate streams) -- UNSAFE_PATH;
- a part Windows cannot write: a refused or control character, a trailing
  dot or space, an empty or ``.`` part (``a//b``, ``./a``) -- BAD_NAME; a
  device name (``CON``, ``nul.txt``, ``COM1``) -- RESERVED_NAME;
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

``LongestPath`` is the longest absolute path the entries make under a
folder (Windows' MAX_PATH is checked by the caller against it).

``Extract`` writes the entries under a folder, ``strip`` leading parts
dropped (the ``<id>/`` of a plugin's zip), never over an existing file and
never outside the folder; a declared size is a ceiling (zipfile reads no
more than it, and a short entry fails its CRC).

``Swap`` moves the current folder to ``.old`` and the new one in; a
``commit`` that raises after the move (the install record not written)
puts the old one back. ``CleanSwap`` is a launch's sweep of what a crash
left (``.part`` and ``.new`` removed, ``.old`` put back when the folder is
missing, else removed).

Nothing is made, read or written on import (pitfalls #1); no Qt.
"""

import os
import shutil
import stat
import time
import zipfile
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from loguru import logger

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
        if part[-1] in ". ":
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


def LongestPath(root: str, entries: Iterable[ZipEntry], *,
                strip: int = 0) -> int:
    """The length of the longest absolute path ``entries`` make under
    ``root`` (``strip`` leading parts dropped); ``root``'s own when none."""
    root = os.path.abspath(root)
    longest = len(root)
    for entry in entries:
        rest = entry.parts[strip:]
        if rest:
            longest = max(longest, len(os.path.join(root, *rest)))
    return longest


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
            except OSError:
                logger.opt(exception=True).error(
                    "Swap: the new folder could not be moved back out")
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
                           f"({type(error).__name__}); removed at the next "
                           f"start")
    return moved


def _PutBack(old: str, current: str, sleep) -> None:
    try:
        Rename(old, current, sleep=sleep)
    except OSError:
        logger.opt(exception=True).error(
            "Swap: the old folder could not be put back; the next start "
            "puts it back")


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
                       f"not be removed ({type(error).__name__})")
    try:
        if os.path.isdir(new):
            RemoveTree(new)
            logger.info(f"Swap sweep: {name}: an unfinished install was "
                        f"removed")
    except OSError as error:
        logger.warning(f"Swap sweep: {name}: an unfinished install could "
                       f"not be removed ({type(error).__name__})")
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
                       f"be cleaned ({type(error).__name__})")
