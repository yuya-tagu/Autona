"""The archive clipper's jobs (ADR-006 A1): one worker run on a VOD, its
progress and its outcome.

A job is one ``run`` of the worker (core/ArchiveWorker.py: vodsplit as a
child process) on a YouTube URL or a video file, with the tournament's
results when the operator has them. One runs at a time (a second start is
``busy``, not queued): the analysis takes the CPU, the downloads the disk.
The page (/web/app/archive) starts it, follows it once a second
(``Job``) and cancels or resumes it; this service is the only one that
starts or stops the worker.

Where it lives
--------------
Not in the StateManager (ADR-006 D6): the page's state is not broadcast and
not the overlays'. Each job is a folder of the work folder
(``archive.work_dir`` in settings.json; ``%USERPROFILE%\\Videos\\Autona`` by
default, never under user_data/: the videos are large and the operator's):

- ``<work>/<id>/job.json``: the job (its source, state, outcome, the last
  log lines and warnings), written whole (temp file + os.replace) when the
  state changes -- at the start and at the end, not on every progress line;
- ``<work>/<id>/results.json``: the results it was given, when it was;
- ``<work>/<id>/out/``: the worker's output (``sets.json`` -- A1 runs with
  ``--no-cut``, so no clips yet --, each set's background frame
  ``NN_<title>_bg.jpg`` (``--thumbs``, ADR-006 A3; sets.json's ``thumb``)
  and its analysis cache ``.cache``);
- ``<work>/<id>/worker.log``: the worker's stderr and what it printed on
  stdout that is not a JSON line, appended by this process alone;
- ``<work>/downloads/``: the downloaded videos, shared by the jobs (two
  jobs on one VOD download it once).

A job's id is ``YYYYMMDD-HHMMSS-xxxx`` (local time and four random
characters; ``JOB_ID_PATTERN``). Every id a request names is checked
against it, and the folder it names must resolve under the work folder
(``_JobDir``); anything else is ``not_found``.

The run
-------
The worker is started with ``run <source> --out <job>/out --download-dir
<work>/downloads [--results <job>/results.json] [--event=<name>] --no-cut
--thumbs --progress json --cancel-on-stdin``. The source is a URL or a file on a
drive of this PC (a share's path is refused unread: ``CheckSource``),
checked again on a resume. Two threads of this service read it
(``_Start``): one its stdout's JSON lines into the progress held in memory
(the latest stage and progress, the last ``LOG_LINES`` log lines, the
warnings, the final ``done`` / ``error`` / ``cancelled`` line), then ends
the job when the stdout closes (``_Finish``); the other its stderr into
worker.log. The end state:

- exit 0 with ``done``: ``done`` (with the sets and how many need review);
- exit 2 with ``error``: ``error`` (the worker's code, message and hint:
  what the operator can fix);
- exit 130 or ``cancelled``, or no final line after a cancel was asked:
  ``cancelled``;
- anything else: ``failed``, the message carrying the tail of worker.log.

A cancel writes ``cancel`` to the worker's stdin and closes it; the worker
stops at its next check and says ``cancelled``. ``Resume`` runs the same
command again: the worker picks up from its caches (the download, the
analysis); while it runs, ``Job`` answers no sets (out/sets.json is the
previous run's until the worker writes its own). ``Shutdown`` (the shells' close) cancels, waits, then ends the
worker and every process it started (the job object). A job a previous
Autona left ``running`` (it ended without its close: a crash, a kill) is
rewritten ``cancelled`` the first time the work folder is used.

What the page plays (ADR-006 A3, D7)
------------------------------------
The preview's video and a set's background frame are files this service
names from the job alone (``VideoFile`` / ``FrameFile``); a request names
the job (and the set's number), never a path. The video is the source
job.json names when it is a file of this PC (checked as a start checks it:
a share's path refused unread), else -- a URL -- the file the worker
downloaded for it. job.json does not name that one (the worker picks it:
``<id>.open.mp4``, or ``<id>.mp4`` when no VP9 / AV1 was offered); the
worker's out/sets.json does (``source``), and it is looked for by its name
in this work folder's downloads/ (the folder may have been moved since).
A frame is the ``thumb`` sets.json names for the set, in the job's out/.
The download and the frame must resolve under the work folder (links
followed); the operator's own file may be anywhere on this PC's drives.
Anything else is ``not_found``.

Threads and locks: the request threads and the two reader threads.
``_lock`` holds the run in memory, the probe's cache and the swept work
folders; ``_filesLock`` every read and write of a job.json (on Windows an
os.replace fails while another thread has the file open); taken in that
order. The version probe (up to ``ArchiveWorker.PROBE_TIMEOUT_SECS``) runs
under its own lock, never under ``_lock``; a request waits for one probe at
most (the web client's timeout is 10 s). Nothing here touches the
StateManager or waits for the GUI thread.

Qt-free. Nothing is made, read or written at import or in the constructor:
the work folder is read and swept at first use, the worker probed at the
first status asked.
"""
import datetime
import json
import os
import re
import secrets
import string
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger

from . import ArchiveWorker
from .SettingsManager import SettingsManager

WORK_DIR_KEY = "archive.work_dir"
JOB_ID_PATTERN = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9]{4}")
JOB_FILE = "job.json"
RESULTS_FILE = "results.json"
LOG_FILE = "worker.log"
OUT_DIR = "out"
SETS_FILE = "sets.json"
DOWNLOADS_DIR = "downloads"

STATES = ("running", "done", "error", "cancelled", "failed")
# What a job.json written by a run that never ended can say.
_UNFINISHED = ("running", "queued")

# How much of a run is kept: the log lines and warnings the page shows,
# and the tail of worker.log a failure carries.
LOG_LINES = 50
WARNING_LINES = 200
FAILURE_TAIL_LINES = 20
FAILURE_TAIL_CHARS = 2000

# An unavailable worker is asked again after this long (the operator may
# have put it in place since); an available one is not, until a refresh.
UNAVAILABLE_RECHECK_SECS = 30

MAX_SOURCE = 2048
MAX_EVENT = 200
MAX_WORK_DIR = 1024

# How long the reader waits for the stderr reader once the stdout closed.
_STDERR_JOIN_SECS = 5
# How long Shutdown waits for the end to be written after it killed.
_KILL_WAIT_SECS = 2

_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.05

_ID_CHARACTERS = string.ascii_lowercase + string.digits

# What the preview serves (VideoFile / FrameFile): a video a browser can
# play, of these extensions only, its type from this fixed table (not the
# registry's); a set's number in a URL; a frame is a JPEG.
_VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4",
                ".webm": "video/webm", ".mkv": "video/x-matroska",
                ".mov": "video/quicktime"}
# The videos a job may have been run on: vodsplit has no list of its own
# (api.run takes any file there is and opens it with cv2.VideoCapture, i.e.
# FFmpeg), so this is the common containers FFmpeg reads -- OBS records .flv
# / .ts too. One of them that is not in _VIDEO_TYPES is a video the preview
# cannot play (NOT_PREVIEWABLE); any other file is not a video (NOT_FOUND).
_VIDEO_SUFFIXES = frozenset(_VIDEO_TYPES) | {
    ".ts", ".m2ts", ".mts", ".flv", ".avi", ".wmv", ".mpg", ".mpeg",
    ".3gp", ".ogv"}
SET_NUMBER_PATTERN = re.compile(r"[0-9]{1,6}")
_FRAME_SUFFIXES = (".jpg", ".jpeg")
FRAME_TYPE = "image/jpeg"

STALE_NOTE = ("前回の Autona の終了時に実行中だったため、中断として扱いました。"
              "「再開」で続きから実行できます。")

# ArchiveError codes (the v1 routes answer them: AutonaApiV1).
INVALID = "invalid"
BUSY = "busy"
UNAVAILABLE = "unavailable"
STOPPING = "stopping"
NOT_FOUND = "not_found"
NOT_RUNNING = "not_running"
ALREADY_DONE = "done"
WORK_DIR_UNUSABLE = "work_dir"
# A resume whose job.json names a source a start would refuse (edited by
# hand, the video moved or deleted since).
SOURCE_UNUSABLE = "source"
# A preview file that is there but cannot be opened (another app holds it,
# no permission): v1 raises it when send_file's open fails (AutonaApiV1).
UNREADABLE = "unreadable"
# A job's video that is there but of a format the preview cannot play (a
# video of _VIDEO_SUFFIXES not in _VIDEO_TYPES): the sets and the timeline
# still work.
NOT_PREVIEWABLE = "not_previewable"

# The preview's refusals' words (VideoFile / FrameFile, and v1 when the
# file is gone by the time it opens it).
VIDEO_MISSING = "この切り抜きジョブの動画が見つかりません。"
VIDEO_NOT_PREVIEWABLE = ("この形式の動画は画面でプレビューできません。"
                         "セットの一覧・タイムラインは使えます。")
FRAME_MISSING = "このセットの画像はありません。"
FILE_UNREADABLE = ("ファイルを開けません。ほかのアプリが使っていないか、"
                   "読む権限があるかを確かめてください。")


class ArchiveError(Exception):
    """A refusal: ``code`` (the constants above) and the Japanese
    ``message`` the page shows as it is."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def DefaultWorkDir():
    return os.path.join(str(Path.home()), "Videos", "Autona")


def _IsAbsolute(path):
    """An absolute path, with its drive on Windows (``C:\\...`` or a UNC
    share; ``\\Videos`` is relative to the current drive there)."""
    if not os.path.isabs(path):
        return False
    if os.name == "nt":
        return bool(os.path.splitdrive(path)[0])
    return True


def _HasControl(text):
    return any(ord(c) < 32 or ord(c) == 127 for c in text)


def _Now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _IsNetworkPath(path):
    """A path that starts with two separators: a share (``\\\\host\\share``,
    ``//host/share``) or a device path (``\\\\?\\UNC\\...``, ``\\\\?\\C:\\``,
    ``\\\\.\\pipe\\...``). Read from the text alone."""
    return len(path) >= 2 and path[0] in "\\/" and path[1] in "\\/"


def _FileName(value):
    """The last part of a path sets.json names (either separator), or None
    for what names no file in a folder (".", "..", a stream: ":")."""
    if not isinstance(value, str) or _HasControl(value):
        return None
    name = re.split(r"[\\/]", value)[-1]
    if name in ("", ".", "..") or ":" in name:
        return None
    return name


def _Under(base, path):
    """Whether `path` resolves (links followed) inside folder `base`."""
    root = os.path.realpath(base)
    real = os.path.realpath(path)
    try:
        return os.path.commonpath([root, real]) == root and real != root
    except ValueError:
        return False


def CheckSource(source):
    """`source` as the worker takes it: a ``http(s)://`` URL, or the full
    path of a video file on a drive of this PC. A share's or a device path
    is refused before anything looks at the file system (asking Windows
    whether \\\\host\\share\\x is a file connects to that host and sends it
    this user's credentials, NTLM). Raises ArchiveError(INVALID)."""
    if not isinstance(source, str) or not source.strip():
        raise ArchiveError(INVALID, "動画の URL か、動画ファイルのパスを入力してください。")
    source = source.strip()
    if len(source) > MAX_SOURCE or _HasControl(source):
        raise ArchiveError(INVALID, "動画の URL かパスとして読めません。")
    # vodsplit's own test (api.run): a URL starts like this, case and all.
    if re.match(r"https?://", source):
        try:
            host = urlsplit(source).hostname
        except ValueError:
            host = None
        if not host:
            raise ArchiveError(INVALID, "URL として読めません。")
        return source
    if _IsNetworkPath(source):
        raise ArchiveError(INVALID, (
            "ネットワーク上の共有フォルダ（\\\\サーバー\\共有）や \\\\ で始まるパスは使えません。"
            "動画をこの PC のドライブにコピーしてから、そのフルパスを入力してください。"))
    if not _IsAbsolute(source):
        raise ArchiveError(INVALID, (
            "YouTube の URL（https://...）か、動画ファイルのフルパスを入力してください。"))
    if not os.path.isfile(source):
        raise ArchiveError(INVALID, f"動画ファイルが見つかりません: {source}")
    return os.path.normpath(source)


def CheckResults(results):
    """`results` as the worker reads them (vodsplit results.validate's
    shape), or None. Raises ArchiveError(INVALID)."""
    if results is None:
        return None
    example = ('例: {"event": "大会名", "sets": [{"round": "Grand Final", '
               '"p1": "A", "s1": 3, "p2": "B", "s2": 2}]}')
    if not isinstance(results, dict) or not isinstance(
            results.get("sets"), list):
        raise ArchiveError(INVALID, (
            f"結果データの形式が違います（\"sets\" の配列がありません）。{example}"))
    if not results["sets"]:
        raise ArchiveError(INVALID, (
            "結果データにセットが1件もありません。結果なしで区切る場合は結果データの指定を外してください。"))
    for index, item in enumerate(results["sets"], 1):
        missing = [key for key in ("p1", "p2", "s1", "s2")
                   if not isinstance(item, dict) or key not in item]
        if missing:
            raise ArchiveError(INVALID, (
                f"結果データの {index} 件目のセットに {', '.join(missing)} がありません。"))
        # vodsplit's order (results.validate_set): the names, the scores,
        # then the optional fields -- the same set gets the same message.
        if not all(isinstance(item[key], str) and item[key].strip()
                   for key in ("p1", "p2")):
            raise ArchiveError(INVALID, (
                f"結果データの {index} 件目のセットの選手名（p1, p2）は空でない文字列にしてください。"))
        if not all(isinstance(item[key], int)
                   and not isinstance(item[key], bool) and item[key] >= 0
                   for key in ("s1", "s2")):
            raise ArchiveError(INVALID, (
                f"結果データの {index} 件目のセットのスコア（s1, s2）は 0 以上の整数にしてください。"))
        _CheckSetOptionals(item, f"結果データの {index} 件目のセット")
    return results


def _CheckSetOptionals(item, where):
    """The rest of vodsplit's results.validate_set (optional
    c1 / c2 / char_source / id / games, round), in its order. Same wording,
    so a set Autona accepts is never refused by the worker later. `round`
    is not rewritten here: the worker turns null into "" itself."""
    for key in ("c1", "c2", "char_source"):
        if item.get(key) is not None and not isinstance(item[key], str):
            raise ArchiveError(INVALID, (
                f"{where}の {key} は文字列か null にしてください。"))
    ident = item.get("id")
    if ident is not None and (isinstance(ident, bool)
                              or not isinstance(ident, (str, int))):
        raise ArchiveError(INVALID, f"{where}の id は文字列か整数にしてください。")
    games = item.get("games")
    if games is not None:
        if not isinstance(games, list) or not all(
                isinstance(game, dict) for game in games):
            raise ArchiveError(INVALID, (
                f"{where}の games はゲームごとのオブジェクトの配列にしてください。"
                '例: "games": [{"c1": "マリオ", "c2": "リンク"}, '
                '{"c1": "マリオ", "c2": "ピカチュウ"}]'))
        for number, game in enumerate(games, 1):
            for key in ("c1", "c2"):
                if game.get(key) is not None and not isinstance(game[key], str):
                    raise ArchiveError(INVALID, (
                        f"{where}の games の {number} 件目の {key} は"
                        "文字列か null にしてください。"))
    if item.get("round") is not None and not isinstance(item["round"], str):
        raise ArchiveError(INVALID, (
            f"{where}の round（ラウンド名）は文字列か null にしてください。"))


def CheckEvent(event):
    """The event name for the titles, or None. Raises ArchiveError."""
    if event is None:
        return None
    if not isinstance(event, str):
        raise ArchiveError(INVALID, "大会名は文字列にしてください。")
    event = event.strip()
    if not event:
        return None
    if len(event) > MAX_EVENT or _HasControl(event):
        raise ArchiveError(INVALID, f"大会名は {MAX_EVENT} 文字以内にしてください。")
    return event


def CheckWorkDir(value):
    """A work folder a setting can name: an absolute path (normalised).
    Raises ArchiveError(INVALID)."""
    if not isinstance(value, str) or not value.strip():
        raise ArchiveError(INVALID, "作業フォルダを入力してください。")
    value = value.strip()
    if len(value) > MAX_WORK_DIR or _HasControl(value):
        raise ArchiveError(INVALID, "作業フォルダのパスとして読めません。")
    if not _IsAbsolute(value):
        raise ArchiveError(INVALID, "作業フォルダはフルパス（例: D:\\Videos\\Autona）で入力してください。")
    return os.path.normpath(value)


def _Number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _Text(value):
    return value if isinstance(value, str) else None


def _Count(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _Normalized(raw, jobId):
    """A job.json as this service uses it (every field there, of its type),
    or None when it is not this job's."""
    if not isinstance(raw, dict) or raw.get("id") != jobId:
        return None
    state = raw.get("state")
    if state not in STATES and state not in _UNFINISHED:
        state = "failed"
    error = raw.get("error")
    if isinstance(error, dict):
        error = {"code": _Text(error.get("code")),
                 "message": _Text(error.get("message")),
                 "hint": _Text(error.get("hint"))}
    else:
        error = None
    progress = raw.get("progress")
    progress = dict(progress) if isinstance(progress, dict) else None

    def strings(key):
        value = raw.get(key)
        return ([item for item in value if isinstance(item, str)]
                if isinstance(value, list) else [])

    return {
        "id": jobId,
        "created_at": _Text(raw.get("created_at")) or "",
        "finished_at": _Text(raw.get("finished_at")),
        "source": _Text(raw.get("source")) or "",
        "event": _Text(raw.get("event")),
        "has_results": bool(raw.get("has_results")),
        "state": state,
        "exit_code": _Count(raw.get("exit_code")),
        "sets": _Count(raw.get("sets")),
        "needs_review": _Count(raw.get("needs_review")),
        "error": error,
        "warnings": strings("warnings")[-WARNING_LINES:],
        "logs": strings("logs")[-LOG_LINES:],
        "progress": progress,
        "worker_version": _Text(raw.get("worker_version")),
    }


def _Summary(record):
    return {"id": record["id"], "created_at": record["created_at"],
            "source": record["source"], "state": record["state"],
            "sets": record["sets"], "needs_review": record["needs_review"]}


def _View(record, progress=None, logs=None, warnings=None):
    """The Job of the v1 answers."""
    state = record["state"]
    if state not in STATES:
        state = "cancelled"
    view = _Summary(record)
    view.update(
        state=state,
        finished_at=record["finished_at"],
        progress=progress if progress is not None else record["progress"],
        logs=list(logs if logs is not None else record["logs"]),
        warnings=list(warnings if warnings is not None
                      else record["warnings"]),
        error=record["error"],
        exit_code=record["exit_code"],
        has_results=record["has_results"])
    return view


class _Run:
    """The job the worker runs now: its record and what the readers
    gathered since it started."""

    def __init__(self, record, jobDir, workDir):
        self.record = record
        self.jobDir = jobDir
        self.workDir = workDir
        self.worker = None
        self.progress = None
        self.logs = deque(maxlen=LOG_LINES)
        self.warnings = []
        self.final = None
        self.cancelRequested = False
        self.finished = threading.Event()
        self.logFile = None
        self.logLock = threading.Lock()
        self.stderrThread = None


class ArchiveJobService:
    instance = None

    def __init__(self, probe=None, launch=None):
        # The worker's probe and start (ArchiveWorker's; a test passes its
        # own).
        self._probe = probe or ArchiveWorker.Probe
        self._launch = launch or ArchiveWorker.Launch
        self._lock = threading.RLock()
        self._filesLock = threading.Lock()
        self._probeLock = threading.Lock()
        self._probed = None
        self._probedAt = 0.0
        # How many probes have ended (under _probeLock; read without it).
        self._probeCount = 0
        self._run = None
        self._swept = set()
        self._stopping = False

    # ── the work folder ────────────────────────────────────────────────

    def WorkDir(self):
        """The work folder settings.json names (an absolute path), else the
        default."""
        value = SettingsManager.Get(WORK_DIR_KEY)
        if isinstance(value, str) and value.strip() and _IsAbsolute(
                value.strip()):
            return os.path.normpath(value.strip())
        return DefaultWorkDir()

    def SetWorkDir(self, value):
        """Save the work folder (made when it is not there). Refused while
        a job runs: the job's folder is under the old one. Raises
        ArchiveError, or SettingsUnreadable (nothing saved)."""
        path = CheckWorkDir(value)
        with self._lock:
            if self._run is not None:
                raise ArchiveError(BUSY, "切り抜きの実行中は作業フォルダを変えられません。")
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as error:
                raise ArchiveError(INVALID, f"作業フォルダを作れません: {error}")
            if not os.path.isdir(path):
                raise ArchiveError(INVALID, "作業フォルダのパスにはファイルがあります。")
            SettingsManager.SetMany([(WORK_DIR_KEY, path)])
        logger.info("Archive: the work folder was changed")

    def _JobDir(self, jobId, workDir=None):
        """The folder of job `jobId` under the work folder. Raises
        ArchiveError(NOT_FOUND) for what is not a job id or would resolve
        outside it (not that it exists)."""
        if not isinstance(jobId, str) or not JOB_ID_PATTERN.fullmatch(jobId):
            raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        workDir = workDir or self.WorkDir()
        path = os.path.join(workDir, jobId)
        base = os.path.realpath(workDir)
        real = os.path.realpath(path)
        try:
            inside = os.path.commonpath([base, real]) == base and real != base
        except ValueError:
            inside = False
        if not inside:
            raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        return path

    # ── job.json ───────────────────────────────────────────────────────

    def _ReadRecord(self, jobDir, jobId):
        path = os.path.join(jobDir, JOB_FILE)
        with self._filesLock:
            try:
                with open(path, encoding="utf-8") as file:
                    raw = json.load(file)
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as error:
                logger.warning(f"Archive: {JOB_FILE} of {jobId} could not be "
                               f"read: {error}")
                return None
        return _Normalized(raw, jobId)

    def _WriteRecord(self, jobDir, record):
        """Write `record` to job.json whole (temp file + os.replace).
        Raises OSError."""
        data = json.dumps({"version": 1, **record}, ensure_ascii=False,
                          indent=2)
        path = os.path.join(jobDir, JOB_FILE)
        with self._filesLock:
            fd, tmp = tempfile.mkstemp(dir=jobDir, prefix="job.",
                                       suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(data)
                delay = _REPLACE_BACKOFF
                for attempt in range(_REPLACE_ATTEMPTS):
                    try:
                        os.replace(tmp, path)
                        break
                    except OSError:
                        if attempt == _REPLACE_ATTEMPTS - 1:
                            raise
                        time.sleep(delay)
                        delay *= 2
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise

    def _Sweep(self, workDir):
        """Rewrite the jobs a previous Autona left running as cancelled,
        once per work folder (under _lock)."""
        if workDir in self._swept:
            return
        self._swept.add(workDir)
        try:
            entries = list(os.scandir(workDir))
        except OSError:
            return
        current = self._run.record["id"] if self._run is not None else None
        for entry in entries:
            if (not JOB_ID_PATTERN.fullmatch(entry.name)
                    or entry.name == current or not entry.is_dir()):
                continue
            record = self._ReadRecord(entry.path, entry.name)
            if record is None or record["state"] not in _UNFINISHED:
                continue
            record["state"] = "cancelled"
            record["logs"] = (record["logs"] + [STALE_NOTE])[-LOG_LINES:]
            try:
                self._WriteRecord(entry.path, record)
                logger.info(f"Archive: job {entry.name} was left running by "
                            f"a previous run; marked cancelled")
            except OSError:
                logger.opt(exception=True).warning(
                    f"Archive: job {entry.name} could not be marked "
                    f"cancelled")

    # ── the worker's availability ──────────────────────────────────────

    def _Probe(self, refresh=False, since=None):
        """The worker's probe: the cached one, else (and when `refresh`) a
        new one -- unless a probe ended since the caller read
        ``_probeCount`` (`since`): one it waited for on another thread,
        which it takes as its own, so a request never waits for two (the
        web client gives it 10 s; a probe takes up to
        ArchiveWorker.PROBE_TIMEOUT_SECS). A count, not the clock: Windows'
        monotonic clock moves in 15 ms steps."""
        with self._probeLock:
            cached = self._probed
            if cached is not None and since is not None \
                    and self._probeCount > since:
                return cached
            if (not refresh and cached is not None
                    and (cached.available
                         or time.monotonic() - self._probedAt
                         < UNAVAILABLE_RECHECK_SECS)):
                return cached
            result = self._probe()
            if result.available:
                logger.info(f"Archive: worker {result.version} "
                            f"(format {result.format})")
            else:
                logger.info(f"Archive: no worker ({result.reason})")
            self._probed, self._probedAt = result, time.monotonic()
            self._probeCount += 1
            return result

    def _Available(self):
        """The probe of a worker that can be started, with one probe at
        most (a start must answer within the web client's 10 s): a cached
        usable one as it is, else one new probe -- the operator may have
        put the worker in place since. Raises ArchiveError(UNAVAILABLE)."""
        since = self._probeCount
        cached = self._probed
        probe = self._Probe(refresh=cached is None or not cached.available,
                            since=since)
        if not probe.available:
            raise ArchiveError(UNAVAILABLE, probe.reason or "切り抜きワーカーを使えません。")
        return probe

    def _Forget(self):
        """Drop the cached probe (a worker that could not be started). Not
        under _probeLock: it is called under _lock, which must not wait for
        a probe."""
        self._probed = None

    # ── what the page reads ────────────────────────────────────────────

    def Status(self, refresh=False):
        """{available, version, format, reason, work_dir, running_job}.
        Probes the worker on this thread the first time (and when
        `refresh`)."""
        probe = self._Probe(refresh=refresh, since=self._probeCount)
        with self._lock:
            running = self._run.record["id"] if self._run is not None else None
        return {"available": probe.available, "version": probe.version,
                "format": probe.format, "reason": probe.reason,
                "work_dir": self.WorkDir(), "running_job": running}

    def Jobs(self):
        """Every job of the work folder, newest first (JobSummary)."""
        workDir = self.WorkDir()
        with self._lock:
            self._Sweep(workDir)
            try:
                entries = list(os.scandir(workDir))
            except OSError:
                entries = []
            records = {}
            for entry in entries:
                if JOB_ID_PATTERN.fullmatch(entry.name) and entry.is_dir():
                    record = self._DiskRecord(entry.path, entry.name)
                    if record is not None:
                        records[entry.name] = record
            run = self._run
            if run is not None and run.workDir == workDir:
                records[run.record["id"]] = dict(run.record)
        return [_Summary(records[jobId])
                for jobId in sorted(records, reverse=True)]

    def Job(self, jobId):
        """(Job, the parsed out/sets.json or None). The sets are None while
        the job runs: a resumed job's sets.json is the previous run's until
        the worker writes its own at the end. Raises
        ArchiveError(NOT_FOUND)."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            view = self._RunView(jobId)
            if view is not None:
                return view, None
            record = self._DiskRecord(jobDir, jobId)
            if record is None:
                raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
            view = _View(record)
        return view, self._ReadSets(jobDir)

    def _DiskRecord(self, jobDir, jobId):
        """Job `jobId`'s job.json when it is not the running job (under
        _lock): one that says it is running is not (this process runs
        ``_run`` alone) and reads as cancelled."""
        record = self._ReadRecord(jobDir, jobId)
        if record is not None and record["state"] in _UNFINISHED:
            record["state"] = "cancelled"
        return record

    def _RunView(self, jobId):
        """The running job's view when it is `jobId` (under _lock)."""
        run = self._run
        if run is None or run.record["id"] != jobId:
            return None
        return _View(run.record,
                     progress=dict(run.progress) if run.progress else None,
                     logs=run.logs, warnings=run.warnings)

    # ── what the preview plays (ADR-006 A3, D7) ────────────────────────

    def _Served(self, jobId):
        """(work folder, job folder, job.json, running) of job `jobId` for
        the preview's files. Raises ArchiveError(NOT_FOUND)."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            run = self._run
            if run is not None and run.record["id"] == jobId:
                return workDir, jobDir, dict(run.record), True
        record = self._ReadRecord(jobDir, jobId)
        if record is None:
            raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        return workDir, jobDir, record, False

    def VideoFile(self, jobId):
        """(path, type) of the video job `jobId` was run on: the file of this
        PC its job.json names, or -- a URL -- the one the worker downloaded
        for it (out/sets.json's ``source``, looked for by its name in this
        work folder's downloads/). Raises ArchiveError(NOT_FOUND): no such
        job, a running one (as FrameFile: the page shows no sets then), a
        share's path (refused unread), no download (yet), a file outside the
        work folder, not a video (``_VIDEO_SUFFIXES``), or gone;
        ArchiveError(NOT_PREVIEWABLE): a video that is there but not one
        the preview plays (not of ``_VIDEO_TYPES``)."""
        missing = ArchiveError(NOT_FOUND, VIDEO_MISSING)
        workDir, jobDir, record, running = self._Served(jobId)
        if running:
            raise missing
        source = record["source"]
        if re.match(r"https?://", source):
            sets = self._ReadSets(jobDir) or {}
            name = _FileName(sets.get("source"))
            if name is None:
                raise missing
            path = os.path.join(workDir, DOWNLOADS_DIR, name)
            if not _Under(workDir, path):
                raise missing
        else:
            try:
                path = CheckSource(source)
            except ArchiveError:
                raise missing from None
        # CheckSource has refused a share's path before touching the disk.
        suffix = os.path.splitext(path)[1].lower()
        if suffix not in _VIDEO_SUFFIXES or not os.path.isfile(path):
            raise missing
        kind = _VIDEO_TYPES.get(suffix)
        if kind is None:
            raise ArchiveError(NOT_PREVIEWABLE, VIDEO_NOT_PREVIEWABLE)
        return path, kind

    def FrameFile(self, jobId, number):
        """(path, type) of set `number`'s background frame (out/sets.json's
        ``thumb``, a JPEG in the job's out/; the worker writes it with
        ``--thumbs``). None of a running job (its sets.json is the previous
        run's, as Job), nor one resolving outside the job's out/. Raises
        ArchiveError(NOT_FOUND)."""
        missing = ArchiveError(NOT_FOUND, FRAME_MISSING)
        if not isinstance(number, str) \
                or not SET_NUMBER_PATTERN.fullmatch(number):
            raise missing
        _workDir, jobDir, _record, running = self._Served(jobId)
        if running:
            raise missing
        sets = (self._ReadSets(jobDir) or {}).get("sets")
        row = next((item for item in sets if isinstance(item, dict)
                    and type(item.get("no")) is int
                    and item["no"] == int(number)), None) \
            if isinstance(sets, list) else None
        name = _FileName(row.get("thumb")) if row is not None else None
        if name is None or not name.lower().endswith(_FRAME_SUFFIXES):
            raise missing
        # Resolved (links followed) inside this job's out/, itself inside
        # the job's folder: not another job's file, not a download.
        out = os.path.join(jobDir, OUT_DIR)
        path = os.path.join(out, name)
        if (not _Under(jobDir, out) or not _Under(out, path)
                or not os.path.isfile(path)):
            raise missing
        return path, FRAME_TYPE

    @staticmethod
    def _ReadSets(jobDir):
        path = os.path.join(jobDir, OUT_DIR, SETS_FILE)
        try:
            with open(path, encoding="utf-8") as file:
                sets = json.load(file)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            logger.warning(f"Archive: {path} could not be read: {error}")
            return None
        return sets if isinstance(sets, dict) else None

    # ── starting, cancelling, resuming ─────────────────────────────────

    def Create(self, source, results=None, event=None):
        """Start a job on `source` -> its Job. Raises ArchiveError: INVALID
        (the request), UNAVAILABLE / STOPPING (no worker, the app is
        closing), BUSY (a job runs), WORK_DIR_UNUSABLE."""
        source = CheckSource(source)
        results = CheckResults(results)
        event = CheckEvent(event)
        probe = self._Available()
        with self._lock:
            self._Refuse()
            workDir = self.WorkDir()
            try:
                os.makedirs(workDir, exist_ok=True)
            except OSError as error:
                raise ArchiveError(WORK_DIR_UNUSABLE,
                                   f"作業フォルダを作れません: {error}")
            self._Sweep(workDir)
            jobId, jobDir = self._MakeJobDir(workDir)
            record = {
                "id": jobId, "created_at": _Now(), "finished_at": None,
                "source": source, "event": event,
                "has_results": results is not None, "state": "running",
                "exit_code": None, "sets": None, "needs_review": None,
                "error": None, "warnings": [], "logs": [], "progress": None,
                "worker_version": probe.version,
            }
            try:
                os.makedirs(os.path.join(jobDir, OUT_DIR), exist_ok=True)
                if results is not None:
                    with open(os.path.join(jobDir, RESULTS_FILE), "w",
                              encoding="utf-8") as file:
                        json.dump(results, file, ensure_ascii=False,
                                  indent=2)
                self._WriteRecord(jobDir, record)
            except OSError as error:
                raise ArchiveError(WORK_DIR_UNUSABLE,
                                   f"作業フォルダに書けません: {error}")
            logger.info(f"Archive: job {jobId} created")
            run = _Run(record, jobDir, workDir)
            self._Start(run, probe)
            return self._RunView(jobId)

    def Resume(self, jobId):
        """Run job `jobId` again (the worker resumes from its caches) ->
        its Job. Its job.json's source passes a start's checks again (the
        file is the operator's to edit). Raises ArchiveError: NOT_FOUND,
        ALREADY_DONE, BUSY, SOURCE_UNUSABLE, UNAVAILABLE / STOPPING."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            if self._RunView(jobId) is not None:
                raise ArchiveError(BUSY, "このジョブは実行中です。")
            record = self._DiskRecord(jobDir, jobId)
        if record is None:
            raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        if record["state"] == "done":
            raise ArchiveError(ALREADY_DONE, "このジョブは完了しています。")
        self._ResumableSource(record)
        probe = self._Available()
        with self._lock:
            self._Refuse()
            record = self._DiskRecord(jobDir, jobId)
            if record is None:
                raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
            if record["state"] == "done":
                raise ArchiveError(ALREADY_DONE, "このジョブは完了しています。")
            record["source"] = self._ResumableSource(record)
            record.update(state="running", finished_at=None, exit_code=None,
                          sets=None, needs_review=None, error=None,
                          warnings=[], logs=[], progress=None,
                          worker_version=probe.version)
            try:
                os.makedirs(os.path.join(jobDir, OUT_DIR), exist_ok=True)
                self._WriteRecord(jobDir, record)
            except OSError as error:
                raise ArchiveError(WORK_DIR_UNUSABLE,
                                   f"作業フォルダに書けません: {error}")
            logger.info(f"Archive: job {jobId} resumed")
            run = _Run(record, jobDir, workDir)
            self._Start(run, probe)
            return self._RunView(jobId)

    def Cancel(self, jobId):
        """Ask the running job `jobId` to stop -> its Job (still running:
        the worker ends it). Raises ArchiveError: NOT_FOUND, NOT_RUNNING."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            run = self._run
            if run is not None and run.record["id"] == jobId:
                self._AskCancel(run)
                logger.info(f"Archive: job {jobId} cancel asked")
                return self._RunView(jobId)
            if self._DiskRecord(jobDir, jobId) is None:
                raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        raise ArchiveError(NOT_RUNNING, "このジョブは実行中ではありません。")

    @staticmethod
    def _ResumableSource(record):
        """The source of job.json `record` as CheckSource reads it. Raises
        ArchiveError(SOURCE_UNUSABLE) with CheckSource's reason."""
        try:
            return CheckSource(record["source"])
        except ArchiveError as error:
            reason = error.message.rstrip("。")
            raise ArchiveError(SOURCE_UNUSABLE, (
                f"このジョブの元動画を使えないため再開できません（{reason}）。"
                "新しいジョブとして始め直してください。"))

    def _Refuse(self):
        """Refuse a start while the app closes or a job runs (under
        _lock)."""
        if self._stopping:
            raise ArchiveError(STOPPING, "Autona の終了中です。")
        if self._run is not None:
            raise ArchiveError(BUSY, (
                "別の切り抜きジョブが実行中です。終わるか中止してから始めてください。"))

    @staticmethod
    def _MakeJobDir(workDir):
        for _attempt in range(20):
            jobId = (time.strftime("%Y%m%d-%H%M%S") + "-"
                     + "".join(secrets.choice(_ID_CHARACTERS)
                               for _ in range(4)))
            jobDir = os.path.join(workDir, jobId)
            try:
                os.mkdir(jobDir)
            except FileExistsError:
                continue
            except OSError as error:
                raise ArchiveError(WORK_DIR_UNUSABLE,
                                   f"作業フォルダに書けません: {error}")
            return jobId, jobDir
        raise ArchiveError(WORK_DIR_UNUSABLE, "ジョブのフォルダを作れません。")

    def _Arguments(self, run):
        record = run.record
        arguments = ["run", record["source"],
                     "--out", os.path.join(run.jobDir, OUT_DIR),
                     "--download-dir", os.path.join(run.workDir,
                                                    DOWNLOADS_DIR)]
        results = os.path.join(run.jobDir, RESULTS_FILE)
        if os.path.isfile(results):
            arguments += ["--results", results]
        if record.get("event"):
            # One word: a name starting with "-" is the value, not an
            # option (argparse reads "--event -x" as a missing value).
            arguments.append(f"--event={record['event']}")
        # --thumbs: each set's background frame, the set list's and the
        # preview's picture (FrameFile; ADR-006 A3).
        return arguments + ["--no-cut", "--thumbs", "--progress", "json",
                            "--cancel-on-stdin"]

    def _Start(self, run, probe):
        """Start the worker for `run` and its two readers (under _lock).
        When it cannot be started the job ends failed and
        ArchiveError(UNAVAILABLE) is raised."""
        arguments = self._Arguments(run)
        try:
            run.logFile = open(os.path.join(run.jobDir, LOG_FILE), "a",
                               encoding="utf-8")
            run.logFile.write(f"--- {_Now()} start ---\n")
            run.logFile.flush()
        except OSError:
            logger.opt(exception=True).warning(
                f"Archive: {LOG_FILE} of {run.record['id']} cannot be written")
            run.logFile = None
        try:
            run.worker = self._launch(probe.command, arguments)
        except Exception as error:
            self._Forget()
            message = f"切り抜きワーカーを起動できません: {error}"
            run.record.update(state="failed", finished_at=_Now(), error={
                "code": "launch", "message": message, "hint": None})
            self._CloseLog(run)
            try:
                self._WriteRecord(run.jobDir, run.record)
            except OSError:
                logger.opt(exception=True).warning(
                    f"Archive: job {run.record['id']} could not be written")
            logger.warning(f"Archive: job {run.record['id']}: the worker "
                           f"could not be started: {error}")
            raise ArchiveError(UNAVAILABLE, message)
        logger.info(f"Archive: job {run.record['id']} running (pid "
                    f"{run.worker.pid})")
        self._run = run
        run.stderrThread = threading.Thread(
            target=self._ReadErrors, args=(run,), daemon=True,
            name="AutonaArchiveWorkerLog")
        run.stderrThread.start()
        threading.Thread(target=self._ReadOutput, args=(run,), daemon=True,
                         name="AutonaArchiveWorker").start()

    @staticmethod
    def _AskCancel(run):
        """Write ``cancel`` to the worker's stdin and close it (once)."""
        if run.cancelRequested:
            return
        run.cancelRequested = True
        stdin = run.worker.proc.stdin if run.worker is not None else None
        if stdin is None:
            return
        try:
            stdin.write("cancel\n")
            stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            stdin.close()
        except (OSError, ValueError):
            pass

    # ── the readers ────────────────────────────────────────────────────

    def _Log(self, run, line):
        with run.logLock:
            if run.logFile is None:
                return
            try:
                run.logFile.write(line if line.endswith("\n")
                                  else line + "\n")
                run.logFile.flush()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _CloseLog(run):
        with run.logLock:
            logFile, run.logFile = run.logFile, None
        if logFile is not None:
            try:
                logFile.close()
            except OSError:
                pass

    def _ReadErrors(self, run):
        try:
            for line in run.worker.proc.stderr:
                self._Log(run, line)
        except (OSError, ValueError):
            pass

    def _ReadOutput(self, run):
        try:
            for line in run.worker.proc.stdout:
                self._OnLine(run, line)
        except (OSError, ValueError):
            logger.opt(exception=True).warning(
                f"Archive: job {run.record['id']}: the worker's output "
                f"stopped")
        except Exception:
            logger.opt(exception=True).error(
                f"Archive: job {run.record['id']}: reading the worker failed")
        finally:
            self._Finish(run)

    def _OnLine(self, run, line):
        text = line.strip()
        if not text:
            return
        event = None
        if text.startswith("{"):
            try:
                event = json.loads(text)
            except ValueError:
                event = None
        if not isinstance(event, dict) or not isinstance(
                event.get("type"), str):
            self._Log(run, f"[stdout] {text}")
            return
        kind = event["type"]
        with self._lock:
            if kind == "stage":
                previous = run.progress or {}
                run.progress = {
                    "stage": _Text(event.get("stage")),
                    "label": _Text(event.get("label")),
                    "index": _Count(event.get("index")),
                    "count": _Count(event.get("count")),
                    "fraction": 0.0,
                    "overall": previous.get("overall") or 0.0,
                    "eta": None,
                }
            elif kind == "progress":
                progress = dict(run.progress or {
                    "stage": None, "label": None, "index": None,
                    "count": None, "fraction": 0.0, "overall": 0.0,
                    "eta": None})
                if _Text(event.get("stage")):
                    progress["stage"] = event["stage"]
                for key in ("fraction", "overall"):
                    value = _Number(event.get(key))
                    if value is not None:
                        progress[key] = value
                progress["eta"] = _Number(event.get("eta"))
                run.progress = progress
            elif kind == "log":
                message = _Text(event.get("message"))
                if message is not None:
                    run.logs.append(message)
            elif kind == "warning":
                message = _Text(event.get("message"))
                if message is not None and len(run.warnings) < WARNING_LINES:
                    run.warnings.append(message)
            elif kind in ("done", "error", "cancelled"):
                run.final = event
            else:
                self._Log(run, f"[stdout] {text}")

    def _EndState(self, run, exitCode):
        """(state, error) of a run that ended with `exitCode`."""
        final = run.final or {}
        kind = final.get("type")
        if exitCode == 0 and kind == "done":
            return "done", None
        if exitCode == 130 or kind == "cancelled":
            return "cancelled", None
        if exitCode == 2 and kind == "error":
            return "error", {"code": _Text(final.get("code")) or "error",
                             "message": _Text(final.get("message")) or "",
                             "hint": _Text(final.get("hint"))}
        if run.cancelRequested and kind is None:
            return "cancelled", None
        message = f"切り抜きワーカーが異常終了しました（終了コード {exitCode}）。"
        if kind == "error" and _Text(final.get("message")):
            message += f"\n{final['message']}"
        tail = self._LogTail(run)
        if tail:
            message += f"\n--- worker.log ---\n{tail}"
        code = (_Text(final.get("code")) if kind == "error" else None)
        return "failed", {
            "code": code or "crashed", "message": message,
            "hint": "「再開」で続きから実行できます。詳しくは作業フォルダの worker.log を見てください。"}

    @staticmethod
    def _LogTail(run):
        path = os.path.join(run.jobDir, LOG_FILE)
        try:
            with open(path, encoding="utf-8", errors="replace") as file:
                lines = deque(file, maxlen=FAILURE_TAIL_LINES)
        except OSError:
            return ""
        tail = "".join(lines).strip()
        return tail[-FAILURE_TAIL_CHARS:]

    def _Finish(self, run):
        """End the job once the worker's stdout closed: wait for it, write
        its end state, close what it held."""
        proc = run.worker.proc
        try:
            exitCode = proc.wait()
        except Exception:
            logger.opt(exception=True).error(
                f"Archive: job {run.record['id']}: waiting for the worker "
                f"failed")
            exitCode = None
        # The worker has ended: close its job object first, which ends what
        # it left behind (KILL_ON_JOB_CLOSE). A grandchild still holding
        # the inherited stderr would keep its reader blocked -- and closing
        # a stream while a thread is blocked reading it waits for that read.
        run.worker.Close()
        if run.stderrThread is not None:
            run.stderrThread.join(_STDERR_JOIN_SECS)
        self._Log(run, f"--- {_Now()} exit {exitCode} ---")
        self._CloseLog(run)
        try:
            state, error = self._EndState(run, exitCode)
            with self._lock:
                record = run.record
                record.update(
                    state=state, finished_at=_Now(), exit_code=exitCode,
                    error=error, logs=list(run.logs),
                    warnings=list(run.warnings),
                    progress=dict(run.progress) if run.progress else None)
                if state == "done":
                    final = run.final or {}
                    record.update(sets=_Count(final.get("sets")),
                                  needs_review=_Count(
                                      final.get("needs_review")))
                try:
                    self._WriteRecord(run.jobDir, record)
                except OSError:
                    logger.opt(exception=True).warning(
                        f"Archive: job {record['id']} could not be written")
                if self._run is run:
                    self._run = None
            logger.info(f"Archive: job {record['id']} {state} (exit "
                        f"{exitCode})")
        finally:
            with self._lock:
                if self._run is run:
                    self._run = None
            run.worker.Close()
            streams = [proc.stdin, proc.stdout]
            if run.stderrThread is not None and run.stderrThread.is_alive():
                # Still blocked reading (no job object ended the process
                # holding it): closing it would wait for that read. Left to
                # the daemon reader, which ends at the pipe's end.
                logger.warning(f"Archive: job {run.record['id']}: the "
                               f"worker's stderr is still open")
            else:
                streams.append(proc.stderr)
            for stream in streams:
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
            run.finished.set()

    # ── the shells' close ──────────────────────────────────────────────

    def Shutdown(self, timeout=5.0):
        """Refuse new jobs, cancel the running one and wait up to `timeout`
        seconds for it to end; then end the worker and every process it
        started. Its job.json is written cancelled (resumable)."""
        with self._lock:
            self._stopping = True
            run = self._run
            if run is None:
                return
            self._AskCancel(run)
        logger.info(f"Archive: stopping job {run.record['id']} for the "
                    f"shutdown")
        if not run.finished.wait(timeout):
            logger.warning(f"Archive: job {run.record['id']} did not stop in "
                           f"{timeout}s; ending the worker")
            run.worker.Kill()
            if not run.finished.wait(_KILL_WAIT_SECS):
                logger.warning(f"Archive: job {run.record['id']}: the end "
                               f"was not written")
        run.worker.Close()
