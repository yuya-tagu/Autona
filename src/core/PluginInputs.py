"""The input sources (ADR-009 B.3 / B.4 / B.5; lane L3): what a manual run
can pick for each, and the files the host places in a run's ``inputs/``
(contract_1.json field_types.source; the golden files of
docs/plugins/inputs/).

A source field ``key`` becomes ``inputs/<key><ext>`` and request.json's
``inputs[key]`` (``InputFile``: kind, format, the absolute path; an image's
sha256 too):

========================  =========  ========================================
type                      file       what
========================  =========  ========================================
``match_log``             .jsonl     the match log's lines (MatchLogService's,
                                     unchanged; a line that is not a JSON
                                     object -- one a crash cut -- is left
                                     out). Picked: one day, or one
                                     tournament of it. At the trigger: the
                                     file of the open record's start date
                                     (today's when none) after the writer's
                                     Flush; an empty file when it has none
``match_record``          .json      the trigger's record line (match_decided)
``state``                 .json      the published state now (never picked:
                                     the state at the press, manual too)
``result_image``          .png/.jpg  one of out/results (the newest
                                     RESULT_IMAGE_CHOICES), or the trigger's
``thumbnail_image``       .png/.jpg  one of out/thumbnails, or the trigger's
``archive_export``        .json      an archive job's export (below)
========================  =========  ========================================

A ``.jpeg`` is written as ``.jpg``. An image is copied byte for byte, and
its sha256 is that of those bytes.

The choices (``Choices``) name things by an id, never by a path (the page
never sends one): a match log choice is ``YYYYMMDD`` (the whole day) or
``YYYYMMDD/<tournament>`` (its records with that ``tournament``, exactly; ""
is the records with none), an image choice its file name in its folder. A
choice id that names nothing (any more) is a ValueError in ``Prepare``: the
caller's INVALID_REQUEST.

A trigger's own file (``InputPick.path``: an action's image, the export's
job folder) is taken only inside its own folder: an image inside
out/results or out/thumbnails (its type's), an export's job folder directly
in the work folder. Nothing is read from anywhere else, and ``inputsDir``
must be inside the work folder.

``archive_export`` (shape not settled by the contract: ADR-009 B.3 leaves
it to P3 and the YouTube ADR; this is the least one, ``format`` 1, to be
widened by adding fields)::

    {"job": "<the archive job id>",
     "clips_dir": "<absolute path of the job's clips folder>",
     "clips": [{"no": 1, "path": "<absolute path of the clip>",
                "file": "<its file name>", "title": "...",
                "start": 12.5, "end": 340.0}]}

taken from the job's ``clips/export.json`` (ArchiveJobService's
CLIPS_DIR / EXPORT_FILE): the clips it marks exported and not skipped whose
file is in the clips folder, in its order. The clips stay where they are.

Injected (nothing here reaches a service): the folders -- out/ (fixed),
the match log's (MatchLogService.directory), the work folder (a callable:
``archive.work_dir`` can change) -- and ``publishedState()`` (the published
state dict: ``StateManager.GetFullState()["state"]``), ``flushMatchLog()``
(``MatchLogService.Flush`` with a timeout; called outside StateManager.lock),
``openRecordDate()`` (the open record's start date, a ``datetime.date``, or
None) and ``today()`` (the local date).

Nothing is made, read or written on import (pitfalls #1); no Qt.
"""

import datetime
import hashlib
import json
import os
import re
from typing import Any, Callable, List, Mapping, Optional, Sequence

import orjson
from loguru import logger

from . import ImageImport
from .PluginTypes import (FIELD_KEY_PATTERN, INPUT_FORMATS,
                          MANUAL_PICK_TYPES, RESULT_IMAGE_CHOICES, FieldType,
                          InputChoice, InputFile, InputPick)

RESULTS_DIR = "results"
THUMBNAILS_DIR = "thumbnails"
# ArchiveJobService's (not imported: it brings the whole archive along;
# tests/test_plugin_inputs.py holds them equal).
CLIPS_DIR = "clips"
EXPORT_FILE = "export.json"
ARCHIVE_JOB_ID_PATTERN = r"[0-9]{8}-[0-9]{6}-[a-z0-9]{4}"

_FIELD_KEY = re.compile(FIELD_KEY_PATTERN)
_ARCHIVE_JOB_ID = re.compile(ARCHIVE_JOB_ID_PATTERN)
_DAY = re.compile(r"[0-9]{8}")
_LOG_FILE = re.compile(r"([0-9]{8})\.jsonl")
# An image's extension -> the one it is written with.
IMAGE_EXTENSIONS = {".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg"}
_IMAGE_TYPES = (FieldType.RESULT_IMAGE, FieldType.THUMBNAIL_IMAGE)
# What a choice with no tournament name shows after its date.
NO_TOURNAMENT_LABEL = "—"


class InputError(ValueError):
    """A pick that names nothing usable (the caller's INVALID_REQUEST). Its
    text holds no path."""


def _Inside(path: str, root: str) -> bool:
    try:
        path = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _IsoDay(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _CheckDay(day: str) -> str:
    if not _DAY.fullmatch(day):
        raise InputError("not a match log day")
    try:
        datetime.datetime.strptime(day, "%Y%m%d")
    except ValueError:
        raise InputError("not a match log day") from None
    return day


def _Write(path: str, data: bytes) -> None:
    ImageImport.WriteFile(path, data)


class PluginInputs:

    def __init__(self, *, outDir: str, matchLogDir: str,
                 workDir: Callable[[], str],
                 publishedState: Callable[[], Mapping[str, Any]],
                 flushMatchLog: Callable[[], Any],
                 openRecordDate: Callable[[], Optional[datetime.date]],
                 today: Optional[Callable[[], datetime.date]] = None):
        self.outDir = os.path.abspath(outDir)
        self.matchLogDir = os.path.abspath(matchLogDir)
        self._workDir = workDir
        self._publishedState = publishedState
        self._flushMatchLog = flushMatchLog
        self._openRecordDate = openRecordDate
        self._today = today or datetime.date.today

    # ── the folders ─────────────────────────────────────────────────────

    def ImageDir(self, kind: FieldType) -> str:
        return os.path.join(self.outDir, RESULTS_DIR
                            if kind == FieldType.RESULT_IMAGE
                            else THUMBNAILS_DIR)

    # ── the choices ─────────────────────────────────────────────────────

    def Choices(self, kind: FieldType) -> Sequence[InputChoice]:
        """The manual choices of a MANUAL_PICK_TYPES type, newest first
        (STATE has none). ValueError for another type."""
        if kind not in MANUAL_PICK_TYPES:
            raise ValueError("not a type a manual run picks")
        if kind == FieldType.STATE:
            return ()
        if kind == FieldType.MATCH_LOG:
            return self._LogChoices()
        limit = RESULT_IMAGE_CHOICES if kind == FieldType.RESULT_IMAGE \
            else None
        return self._ImageChoices(kind, limit)

    def _LogDays(self) -> List[str]:
        try:
            names = os.listdir(self.matchLogDir)
        except FileNotFoundError:
            return []
        except OSError as error:
            logger.warning(f"Plugin inputs: the match log folder could not "
                           f"be listed ({type(error).__name__})")
            return []
        days = []
        for name in names:
            match = _LOG_FILE.fullmatch(name)
            if match:
                try:
                    days.append(_CheckDay(match.group(1)))
                except InputError:
                    continue
        return sorted(days, reverse=True)

    def _ReadLog(self, day: str):
        """[(raw line, record)] of a day's file, the lines that are JSON
        objects, in order. None when there is no such file."""
        path = os.path.join(self.matchLogDir, f"{day}.jsonl")
        try:
            with open(path, "rb") as file:
                content = file.read()
        except FileNotFoundError:
            return None
        except OSError as error:
            logger.warning(f"Plugin inputs: the match log {day}.jsonl could "
                           f"not be read ({type(error).__name__})")
            return None
        lines = []
        for raw in content.decode("utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict):
                lines.append((raw, record))
        return lines

    @staticmethod
    def _Tournament(record: Mapping) -> str:
        value = record.get("tournament")
        return value.strip() if isinstance(value, str) else ""

    def _LogChoices(self) -> Sequence[InputChoice]:
        choices = []
        for day in self._LogDays():
            lines = self._ReadLog(day)
            if not lines:
                continue
            # tournament -> its record ids, in the order they began.
            records = {}
            for _raw, record in lines:
                records.setdefault(self._Tournament(record), set()).add(
                    record.get("id"))
            total = len({record.get("id") for _raw, record in lines})
            choices.append(InputChoice(id=day, label=_IsoDay(day),
                                       detail=str(total)))
            for name, ids in records.items():
                choices.append(InputChoice(
                    id=f"{day}/{name}",
                    label=f"{_IsoDay(day)} {name or NO_TOURNAMENT_LABEL}",
                    detail=str(len(ids))))
        return tuple(choices)

    def _ImageFiles(self, kind: FieldType):
        """[(mtime, name)] of the images of ``kind``'s folder, newest
        first."""
        folder = self.ImageDir(kind)
        try:
            entries = list(os.scandir(folder))
        except FileNotFoundError:
            return []
        except OSError as error:
            logger.warning(f"Plugin inputs: the {os.path.basename(folder)} "
                           f"folder could not be listed "
                           f"({type(error).__name__})")
            return []
        files = []
        for entry in entries:
            if entry.name.startswith(".") or os.path.splitext(
                    entry.name)[1].lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                files.append((entry.stat().st_mtime, entry.name))
            except OSError:
                continue
        return sorted(files, reverse=True)

    def _ImageChoices(self, kind, limit) -> Sequence[InputChoice]:
        files = self._ImageFiles(kind)
        if limit is not None:
            files = files[:limit]
        return tuple(InputChoice(
            id=name, label=name,
            detail=datetime.datetime.fromtimestamp(mtime).astimezone()
            .isoformat(timespec="seconds"))
            for mtime, name in files)

    # ── the files ───────────────────────────────────────────────────────

    def Prepare(self, key: str, kind: FieldType, inputsDir: str,
                pick: Optional[InputPick]) -> Optional[InputFile]:
        """Write ``<key><ext>`` into ``inputsDir`` from ``pick`` (or, with
        none, what the trigger fills: STATE and MATCH_LOG) and say what it
        wrote; None when nothing fills it. InputError (a ValueError) for a
        pick that names nothing usable; ValueError for a bad key, a pick of
        another field or type, an ``inputsDir`` outside the work folder;
        OSError when the file cannot be written."""
        if not isinstance(key, str) or not _FIELD_KEY.fullmatch(key):
            raise ValueError("not a field key")
        kind = FieldType(kind)
        if pick is not None and (pick.key != key or pick.kind != kind):
            raise ValueError("the pick is another field's")
        if not _Inside(inputsDir, self._workDir()):
            raise ValueError("the inputs folder is not in the work folder")
        if kind == FieldType.MATCH_LOG:
            return self._MatchLog(key, inputsDir, pick)
        if kind == FieldType.MATCH_RECORD:
            return self._MatchRecord(key, inputsDir, pick)
        if kind == FieldType.STATE:
            if pick is not None and (pick.choice or pick.path
                                     or pick.record is not None):
                raise InputError("a state is never picked")
            data = orjson.dumps(dict(self._publishedState()),
                                option=orjson.OPT_NON_STR_KEYS)
            return self._Place(key, kind, inputsDir, ".json", data)
        if kind in _IMAGE_TYPES:
            return self._Image(key, kind, inputsDir, pick)
        if kind == FieldType.ARCHIVE_EXPORT:
            return self._ArchiveExport(key, inputsDir, pick)
        raise ValueError("not a source type")

    def _Place(self, key, kind, inputsDir, extension, data,
               sha256=False) -> InputFile:
        path = os.path.join(os.path.abspath(inputsDir), key + extension)
        _Write(path, data)
        return InputFile(kind=kind, format=INPUT_FORMATS[kind], path=path,
                         sha256=hashlib.sha256(data).hexdigest()
                         if sha256 else None)

    def _MatchLog(self, key, inputsDir, pick) -> InputFile:
        if pick is not None and pick.choice is not None:
            day, slash, tournament = pick.choice.partition("/")
            _CheckDay(day)
            lines = self._ReadLog(day)
            if not lines:
                raise InputError("no such match log day")
            if slash:
                lines = [(raw, record) for raw, record in lines
                         if self._Tournament(record) == tournament]
                if not lines:
                    raise InputError("no such tournament on that day")
        elif pick is not None and (pick.path or pick.record is not None):
            raise InputError("a match log is picked by its day")
        else:
            # At the trigger: what the writer has, then the day's file.
            try:
                if self._flushMatchLog() is False:
                    logger.warning("Plugin inputs: the match log's writer "
                                   "had not finished")
            except Exception:
                logger.opt(exception=True).warning(
                    "Plugin inputs: the match log could not be flushed")
            day = (self._openRecordDate() or self._today()).strftime(
                "%Y%m%d")
            lines = self._ReadLog(day) or []
        data = "".join(raw + "\n" for raw, _record in lines).encode("utf-8")
        return self._Place(key, FieldType.MATCH_LOG, inputsDir, ".jsonl",
                           data)

    def _MatchRecord(self, key, inputsDir, pick) -> Optional[InputFile]:
        if pick is None or pick.record is None:
            if pick is not None and (pick.choice or pick.path):
                raise InputError("a match record comes from its trigger")
            return None
        if not isinstance(pick.record, Mapping):
            raise InputError("a match record is an object")
        data = json.dumps(dict(pick.record), ensure_ascii=False).encode(
            "utf-8")
        return self._Place(key, FieldType.MATCH_RECORD, inputsDir, ".json",
                           data)

    def _ImageSource(self, kind, pick) -> Optional[str]:
        folder = self.ImageDir(kind)
        if pick.choice is not None:
            name = pick.choice
            if (not isinstance(name, str) or not name
                    or name != os.path.basename(name) or "/" in name
                    or "\\" in name or name.startswith(".")):
                raise InputError("not an image choice")
            path = os.path.join(folder, name)
        elif pick.path is not None:
            path = os.path.abspath(pick.path)
        else:
            return None
        if os.path.splitext(path)[1].lower() not in IMAGE_EXTENSIONS:
            raise InputError("not a png / jpg")
        if os.path.islink(path) or not os.path.isfile(path) \
                or not _Inside(path, folder):
            raise InputError("no such image")
        return path

    def _Image(self, key, kind, inputsDir, pick) -> Optional[InputFile]:
        if pick is None:
            return None
        if pick.record is not None:
            raise InputError("an image is a file")
        source = self._ImageSource(kind, pick)
        if source is None:
            return None
        try:
            with open(source, "rb") as file:
                data = file.read()
        except OSError as error:
            raise InputError(f"the image could not be read "
                             f"({type(error).__name__})") from None
        extension = IMAGE_EXTENSIONS[os.path.splitext(source)[1].lower()]
        return self._Place(key, kind, inputsDir, extension, data,
                           sha256=True)

    def _ArchiveExport(self, key, inputsDir, pick) -> Optional[InputFile]:
        if pick is None or pick.path is None:
            if pick is not None and (pick.choice or pick.record is not None):
                raise InputError("an archive export is its job's folder")
            return None
        jobDir = os.path.abspath(pick.path)
        work = os.path.abspath(self._workDir())
        jobId = os.path.basename(jobDir)
        if (not _ARCHIVE_JOB_ID.fullmatch(jobId)
                or os.path.normcase(os.path.dirname(jobDir))
                != os.path.normcase(work)
                or os.path.islink(jobDir) or not os.path.isdir(jobDir)):
            raise InputError("not an archive job's folder")
        clipsDir = os.path.join(jobDir, CLIPS_DIR)
        try:
            with open(os.path.join(clipsDir, EXPORT_FILE),
                      encoding="utf-8") as file:
                exported = json.load(file)
        except FileNotFoundError:
            raise InputError("the job has no export") from None
        except (OSError, ValueError) as error:
            raise InputError(f"the export could not be read "
                             f"({type(error).__name__})") from None
        items = exported.get("clips") if isinstance(exported, dict) else None
        if not isinstance(items, list):
            raise InputError("the export lists no clips")
        clips = []
        for item in items:
            clip = self._Clip(clipsDir, item)
            if clip is not None:
                clips.append(clip)
        data = json.dumps({"job": jobId, "clips_dir": clipsDir,
                           "clips": clips},
                          ensure_ascii=False).encode("utf-8")
        return self._Place(key, FieldType.ARCHIVE_EXPORT, inputsDir, ".json",
                           data)

    @staticmethod
    def _Clip(clipsDir, item) -> Optional[dict]:
        """An exported clip of export.json, or None (not exported, skipped,
        not a plain file name in the clips folder, not there)."""
        if not isinstance(item, dict) or type(item.get("no")) is not int:
            return None
        if item.get("exported") is not True or item.get("skipped") is True:
            return None
        name = item.get("file")
        if (not isinstance(name, str) or not name
                or name != os.path.basename(name) or "/" in name
                or "\\" in name):
            return None
        path = os.path.join(clipsDir, name)
        if os.path.islink(path) or not os.path.isfile(path):
            return None

        def number(value):
            return value if isinstance(value, (int, float)) \
                and not isinstance(value, bool) else None

        title = item.get("title")
        return {"no": item["no"], "path": path, "file": name,
                "title": title if isinstance(title, str) else "",
                "start": number(item.get("start")),
                "end": number(item.get("end"))}
