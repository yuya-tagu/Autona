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
  log lines and warnings), written whole (temp file, fsynced, + os.replace)
  when the state changes -- at the start and at the end, not on every progress line;
- ``<work>/<id>/results.json``: the results it was given, when it was (a
  reanalysis replaces them: below);
- ``<work>/<id>/out/``: the worker's output (``sets.json`` and
  ``review.md`` -- A1 runs with ``--no-cut``, so no clips --, each set's
  background frame ``NN_<title>_bg.jpg`` (``--thumbs``, ADR-006 A3;
  sets.json's ``thumb``), a matched set's title and description
  ``NN_<title>.txt``, and its analysis cache ``.cache``);
- ``<work>/<id>/worker.log``: the worker's stderr and what it printed on
  stdout that is not a JSON line, appended by this process alone;
- ``<work>/<id>/review.json``: the operator's review of the sets (ADR-006
  A4: confirmed, IN / OUT, which result; core/ArchiveReview.py), written
  whole when the page saves it;
- ``<work>/<id>/export_request.json`` and ``<work>/<id>/clips/``: the last
  export's request and what the worker cut (the clips, their text files and
  its ``export.json``);
- ``<work>/<id>/thumbs/<no>.png``: each confirmed set's thumbnail (ADR-006
  A6, below), with a copy beside its clip when that is certainly its clip,
  and ``thumbs/thumbs.json``: the result, range and colours each was drawn
  for and the name of that copy;
- ``<work>/<id>/templates/NN.json``: the title templates the last run or
  export was given (ADR-006 A6: a snapshot, written before it starts);
- ``<work>/downloads/``: the downloaded videos, shared by the jobs (two
  jobs on one VOD download it once).

A job's id is ``YYYYMMDD-HHMMSS-xxxx`` (local time and four random
characters; ``JOB_ID_PATTERN``). Every id a request names is checked
against it, and the folder it names must resolve under the work folder
(``_JobDir``); anything else is ``not_found``.

The run
-------
The worker is started with ``run <source> --out <job>/out --download-dir
<work>/downloads --codec h264|any [--results <job>/results.json]
[--event=<name>] --no-cut --thumbs [--template <job>/templates/NN.json ...]
--progress json --cancel-on-stdin`` (``h264`` when the worker's features
name it, else ``any``: ``_Arguments``; the title templates the worker can
take, as core/ArchiveTemplates.py's WorkerTemplates checks and orders them,
written
into the job's templates/ before job.json says it runs: the worker picks the
one for the event, ADR-006 A6). The source is a URL or a file on a
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

The reanalysis
--------------
``Reanalyze`` gives a job that is not running -- done, or ended any other
way -- results (a job made without them is split by the text on screen
alone) and runs its analysis again with Resume's command: the results are
written to its results.json (whole), so ``--results`` names them, and
job.json says it has results. The worker's analysis cache (out/.cache: the
decoded video and its OCR, keyed by the video's path, size and mtime, not
by the results) makes the run redo only the split and the output, while the
video it was made from is there (``Job``'s ``analysis_cached``; else the
analysis runs from the start). Before it starts, what the run makes again
from the results and what was made from the sets it replaces are removed
(``_ClearDerived``: the frames and title files of out/, the thumbnails);
the review stays and keeps what still fits the new sets
(ArchiveReview.Normalize: a set whose range moved loses its edits), the
clips of an export stay.

The export (ADR-006 A4)
-----------------------
A done job's confirmed sets are cut by the worker's ``export``: the same
run of the worker as the analysis (one at a time with it -- a start of
either while the other runs is ``busy`` --, the progress, the cancel, the
shutdown, below the normal priority), started with ``export
<job>/export_request.json --out <job>/clips [--template
<job>/templates/NN.json ...] --progress json --cancel-on-stdin``. The request is made from the job's out/sets.json and
review.json (ArchiveReview.ExportSets) and the video the preview plays
(``VideoFile``'s rule); the worker reads nothing else (the event and the
video's id from sets.json, the bracket URL and the date from results.json,
as ``run`` takes them). job.json keeps the job's analysis as it was and the
last export under ``export`` (its state, outcome, logs, warnings and the
counts of its ``done`` line); ``Job`` shows the clips of export.json
when no export runs (the worker rewrites the file while it runs). The
worker skips the clips it already cut from the same range, so running the
export again after a cancel or a failure goes on where it stopped.

What the page plays (ADR-006 A3, D7)
------------------------------------
The preview's video and a set's background frame are files this service
names from the job alone (``VideoFile`` / ``FrameFile``); a request names
the job (and the set's number), never a path. The video is the source
job.json names when it is a file of this PC (checked as a start checks it:
a share's path refused unread), else -- a URL -- the file the worker
downloaded for it. job.json does not name that one (the worker picks it:
``<id>.mp4`` for ``--codec h264`` / ``any``, ``<id>.open.mp4`` for a job a
worker ran with its old default ``open``); the worker's out/sets.json does
(``source``), and it is looked for by its name in this work folder's
downloads/ (the folder may have been moved since). The page plays a
YouTube job (its id known: sets.json's ``source_id``) in YouTube's embedded
player instead (ADR-006 D7); this file is what it plays for any other job.
A frame is the ``thumb`` sets.json names for the set, in the job's out/.
The download and the frame must resolve under the work folder (links
followed); the operator's own file may be anywhere on this PC's drives.
Anything else is ``not_found``.

The titles and the thumbnails (ADR-006 A6)
-------------------------------------------
The title templates are core/ArchiveTemplates.py's (this service answers
the v1 routes with its errors as ArchiveErrors). A run and an export are
given a snapshot of them: ArchiveTemplates.WorkerTemplates (read and
checked before _lock is taken) written into ``<job>/templates/`` -- the
worker reads what Autona checked, and a template deleted meanwhile does not
take a file from under it. ``PreviewTitles`` makes a done job's sets' titles
with a template the page has not saved: the worker's ``titles`` (vodsplit
V10, no video work) on the request of every reviewed set (confirmed or not;
not a removed one) whose range the worker takes (``ArchiveReview.MIN_CLIP``
at least: one shorter would refuse the whole request; none left is
NO_SETS), on the request's thread. The probe and the run end within
``TITLES_DEADLINE_SECS``
(below the page's wait). Not a run (not counted as the one run), but one at
a time: a newer preview kills the older one's worker, which answers
SUPERSEDED. A worker whose version names no ``titles`` is refused
(UNSUPPORTED) for this alone.

``StartThumbs`` draws a done job's confirmed sets that have a result, one at
a time, with the match thumbnail's renderer (ThumbnailService.SetPayload /
RenderSet: the desktop's off-screen view; headless the web operations
refuse first, 503) into ``<job>/thumbs/<no>.png``. One job's at a time
(``_thumbRun``; not one of the worker's runs: an export may run meanwhile).
A set's data is ArchiveThumbnails.SetData (its result, the event and the
colours its review gives its characters, ArchiveReview.Skins); its
background its frame (FrameFile's file, as a file URL) when
``archive.thumbnail_background`` is ``frame`` (the default) and it has one,
else the thumbnail settings' own (ThumbnailService.SetPayload: never the
tournament's colors or banner -- the archive's tournament is not the one
loaded now). Before the first render the run removes the thumbnails of the
sets it does not draw again and every PNG an earlier run put beside a clip
(thumbs.json names them). The payloads are built on the request's
thread, the first render started there; each render's ``done`` comes on the
main thread, the PNG is encoded and written in a job of the task executor,
and the next render is started from the main thread again
(ThumbnailService.Later: a job's thread may not wait for it) -- RenderLane's
way, without its lane. A thumbnail is copied beside its clip
(``<clip>.png`` in clips/) only when the clip is certainly the set as drawn:
no export of the job runs, clips/export.json's source is the job's video,
its clip of that number was exported and is there, and the
export_request.json it was cut from gave the set the same result and range
as the review does now. ``Job`` says how far it is (``thumbs``) and which
thumbnails still show their set as reviewed (``files``: thumbs.json drew it
for the result, range and colours the review gives it now -- a colour
changed is a thumbnail to draw again); ``ThumbFile`` serves one (as
FrameFile, PNG). The shutdown ends the run at its next set.

The worker's swap (ADR-006 A7)
------------------------------
core/ArchiveWorkerInstaller.py replaces the worker's folder with a newer
one. It asks this service first (``ReserveSwap``): granted only when
nothing of the worker runs -- no job's analysis or export, no titles'
preview, no version probe --, and until ``ReleaseSwap`` nothing of it
starts (a start, a resume, an export and a preview are BUSY, the status
says the worker is being replaced, without probing). The release drops the
cached probe, so the next status probes the new worker.

Threads and locks: the request threads, the two reader threads, and for
the thumbnails the main thread and the task executor's. ``_lock`` holds the
runs in memory, the probe's cache and the swept work folders;
``_filesLock`` every read and write of a job.json and a review.json (on
Windows an os.replace fails while another thread has the file open); taken
in that order. ArchiveTemplates' lock is never taken under them (the
templates are read before), ``_titlesLock`` (the preview that runs) alone.
The version probe (up to
``ArchiveWorker.PROBE_TIMEOUT_SECS``) runs under its own lock, never under
``_lock``; a request waits for one probe at most (the web client's timeout
is 10 s). ``_lock`` is never held across a render or the titles' run, nor
while ``Job`` reads which thumbnails there are.
Nothing here touches the StateManager or waits for the GUI thread.

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

from . import (ArchiveReview, ArchiveTemplates, ArchiveThumbnails,
               ArchiveWorker, ImageImport)
from .GameAssetCatalog import GameAssetCatalog
from .SettingsManager import SettingsManager
from .TaskExecutor import TaskExecutor
from .ThumbnailService import ThumbnailService

WORK_DIR_KEY = "archive.work_dir"
# Which background a set's thumbnail has (ADR-006 A6): its frame (the
# worker's _bg.jpg) or the thumbnail settings' own.
THUMBNAIL_BACKGROUND_KEY = "archive.thumbnail_background"
FRAME_BACKGROUND = "frame"
SETTINGS_BACKGROUND = "settings"
THUMBNAIL_BACKGROUNDS = (FRAME_BACKGROUND, SETTINGS_BACKGROUND)
SETTINGS_KEYS = frozenset({"work_dir", "thumbnail_background"})
JOB_ID_PATTERN = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9]{4}")
JOB_FILE = "job.json"
RESULTS_FILE = "results.json"
LOG_FILE = "worker.log"
OUT_DIR = "out"
SETS_FILE = "sets.json"
# The worker's analysis cache in out/ (vodsplit's default for --cache).
CACHE_DIR = ".cache"
DOWNLOADS_DIR = "downloads"
REVIEW_FILE = ArchiveReview.FILE
EXPORT_REQUEST_FILE = "export_request.json"
CLIPS_DIR = "clips"
EXPORT_FILE = "export.json"
THUMBS_DIR = "thumbs"
# What each thumbnail was drawn for and the copy beside its clip (A6).
THUMBS_FILE = "thumbs.json"
THUMBS_FORMAT = 1
# The title templates a run or an export is given (A6: a snapshot).
TEMPLATES_DIR = "templates"
# The titles' preview: the probe and the worker's run end within this (the
# page waits 15 s); the worker is not started with less than the least.
TITLES_DEADLINE_SECS = 12
TITLES_LEAST_SECS = 1.5
# How long a superseded preview's worker is waited for once killed.
_TITLES_KILL_WAIT_SECS = 2

# What a run of the worker is: a job's analysis (``run``) or the export of
# its confirmed sets (``export``, ADR-006 A4).
ANALYSIS = "analysis"
EXPORT = "export"

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
# A set's thumbnail (ADR-006 A6): thumbs/<no>.png.
_THUMB_NAME = re.compile(r"([0-9]{1,6})\.png")
# A copy beside a clip: the clip's name (NN_<title>) with .png.
_BESIDE_NAME = re.compile(r"[0-9]{2,6}_.+\.png")
# What a run writes into out/ for a set besides sets.json (vodsplit
# export.write_outputs: NN_<title>, "unmatched" for a set with no title): its
# background frame, and a matched set's title and description.
_OUT_FRAME_NAME = re.compile(r"[0-9]{2,6}_.+_bg\.jpg")
_OUT_TEXT_NAME = re.compile(r"([0-9]{2,6}_.+)\.txt")
# A download of the open codecs (vodsplit's name): a run downloads <id>.mp4.
_OPEN_DOWNLOAD_SUFFIX = ".open.mp4"
THUMB_TYPE = "image/png"
# What a run of the thumbnails ended as (Job's ``thumbs.state``; "running"
# while it runs).
THUMBS_RUNNING = "running"
THUMBS_DONE = "done"
THUMBS_CANCELLED = "cancelled"
THUMBS_FAILED = "failed"

STALE_NOTE = ("前回の Autona の終了時に実行中だったため、中断として扱いました。"
              "「再開」で続きから実行できます。")
STALE_EXPORT_NOTE = ("前回の Autona の終了時に書き出し中だったため、中断として扱いました。"
                     "もう一度「確定分を書き出し」で続きから書き出せます。")

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
# A review or an export of a job whose analysis is not done (no sets to
# review, or a sets.json of an earlier run).
NOT_DONE = "not_done"
# The results' preparation (core/ArchiveResults.py, ADR-006 A5): start.gg
# did not answer (or answered an error) for an event's sets.
FETCH_FAILED = "fetch_failed"
# The titles' preview (ADR-006 A6): a worker whose version names no
# ``titles`` (from before vodsplit V10); one that failed to make them.
UNSUPPORTED = "unsupported"
WORKER_FAILED = "worker_failed"
# A preview with no set the worker takes (none of MIN_CLIP), and one a newer
# preview replaced (its worker killed).
NO_SETS = "no_sets"
SUPERSEDED = "superseded"
# A title template that could not be written (user_data/).
SAVE_FAILED = ArchiveTemplates.SAVE_FAILED

# While core/ArchiveWorkerInstaller.py replaces the worker's folder
# (ReserveSwap .. ReleaseSwap, ADR-006 A7).
SWAPPING = "切り抜きワーカーを入れ替えているところです。終わってからもう一度押してください。"
SWAP_BUSY_JOB = ("切り抜きジョブの実行中は切り抜きワーカーを入れ替えられません。"
                 "終わるか中止してから更新してください。")
SWAP_BUSY_PROBE = "切り抜きワーカーを確認しているところです。少し待ってからもう一度押してください。"
SWAP_BUSY_TITLES = ("タイトルのプレビューを作っているところです。"
                    "終わってからもう一度押してください。")

# The preview's refusals' words (VideoFile / FrameFile, and v1 when the
# file is gone by the time it opens it).
VIDEO_MISSING = "この切り抜きジョブの動画が見つかりません。"
VIDEO_NOT_PREVIEWABLE = ("この形式の動画は画面でプレビューできません。"
                         "セットの一覧・タイムラインは使えます。")
FRAME_MISSING = "このセットの画像はありません。"
# An export that failed on a file it could not write (PermissionError).
EXPORT_LOCKED_HINT = ("書き出し先のクリップ（前回の書き出し）を動画プレイヤーなどで開いていると"
                      "上書きできません。閉じてから、もう一度「確定分を書き出し」を押してください。")
FILE_UNREADABLE = ("ファイルを開けません。ほかのアプリが使っていないか、"
                   "読む権限があるかを確かめてください。")
THUMB_MISSING = "このセットのサムネイルはありません。"
TITLES_UNSUPPORTED = ("この版の切り抜きワーカーではタイトルのプレビューは使えません。"
                      "テンプレートの保存と書き出しは使えます。")
TITLES_NO_SETS = ("プレビューできるセットがありません"
                  f"（区間が {ArchiveReview.MIN_CLIP:g} 秒以上のセットがありません）。")
TITLES_SUPERSEDED = "新しいプレビューの要求に置き換えました。"
TITLES_OUT_OF_TIME = ("切り抜きワーカーの確認に時間がかかったため、タイトルを作れませんでした。"
                      "もう一度試してください。")


class ArchiveError(Exception):
    """A refusal: ``code`` (the constants above) and the Japanese
    ``message`` the page shows as it is. ``transient``: what refused it
    ends by itself in a few seconds (ReserveSwap's probe and titles'
    preview: the installer waits and asks again)."""

    def __init__(self, code, message, transient=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


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


def _State(value):
    return value if value in STATES or value in _UNFINISHED else "failed"


def _Error(value):
    if not isinstance(value, dict):
        return None
    return {"code": _Text(value.get("code")),
            "message": _Text(value.get("message")),
            "hint": _Text(value.get("hint"))}


def _Strings(value):
    return ([item for item in value if isinstance(item, str)]
            if isinstance(value, list) else [])


def _NormalizedExport(raw):
    """job.json's ``export`` (the job's last export, ADR-006 A4) as this
    service uses it, or None when there was none."""
    if not isinstance(raw, dict):
        return None
    progress = raw.get("progress")
    return {
        "state": _State(raw.get("state")),
        "started_at": _Text(raw.get("started_at")),
        "finished_at": _Text(raw.get("finished_at")),
        "exit_code": _Count(raw.get("exit_code")),
        "error": _Error(raw.get("error")),
        "warnings": _Strings(raw.get("warnings"))[-WARNING_LINES:],
        "logs": _Strings(raw.get("logs"))[-LOG_LINES:],
        "progress": dict(progress) if isinstance(progress, dict) else None,
        # The worker's done line: the clips of this export, the ones it
        # skipped (already cut from the same range).
        "clips": _Count(raw.get("clips")),
        "skipped": _Count(raw.get("skipped")),
    }


def _Normalized(raw, jobId):
    """A job.json as this service uses it (every field there, of its type),
    or None when it is not this job's."""
    if not isinstance(raw, dict) or raw.get("id") != jobId:
        return None
    progress = raw.get("progress")
    progress = dict(progress) if isinstance(progress, dict) else None
    return {
        "id": jobId,
        "created_at": _Text(raw.get("created_at")) or "",
        "finished_at": _Text(raw.get("finished_at")),
        "source": _Text(raw.get("source")) or "",
        "event": _Text(raw.get("event")),
        "has_results": bool(raw.get("has_results")),
        "state": _State(raw.get("state")),
        "exit_code": _Count(raw.get("exit_code")),
        "sets": _Count(raw.get("sets")),
        "needs_review": _Count(raw.get("needs_review")),
        "error": _Error(raw.get("error")),
        "warnings": _Strings(raw.get("warnings"))[-WARNING_LINES:],
        "logs": _Strings(raw.get("logs"))[-LOG_LINES:],
        "progress": progress,
        "worker_version": _Text(raw.get("worker_version")),
        "export": _NormalizedExport(raw.get("export")),
    }


def _Summary(record):
    return {"id": record["id"], "created_at": record["created_at"],
            "source": record["source"], "state": record["state"],
            "sets": record["sets"], "needs_review": record["needs_review"]}


def _View(record, progress=None, logs=None, warnings=None, export=None):
    """The Job of the v1 answers (`export`: the view of its last export,
    ``_ExportView``)."""
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
        has_results=record["has_results"],
        export=export)
    return view


def _Clip(item):
    """A clip of the worker's export.json as the page shows it, or None."""
    if not isinstance(item, dict) or type(item.get("no")) is not int:
        return None
    name = _FileName(item.get("file"))
    if name is None:
        return None
    return {"no": item["no"], "file": name, "title": _Text(item.get("title")),
            "start": _Number(item.get("start")),
            "end": _Number(item.get("end")),
            "exported": item.get("exported") is True,
            "skipped": item.get("skipped") is True}


class _Run:
    """The job the worker runs now: its record and what the readers
    gathered since it started. `kind`: ANALYSIS (the job's ``run``) or
    EXPORT (the export of its confirmed sets: its end goes to the record's
    ``export``)."""

    def __init__(self, record, jobDir, workDir, kind=ANALYSIS):
        self.record = record
        self.jobDir = jobDir
        self.workDir = workDir
        self.kind = kind
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


class _ThumbRun:
    """A job's thumbnails being drawn (StartThumbs), one set at a time:
    `items` are (no, payload, the set as the review has it now: ExportSets'
    with its ``skins``)."""

    def __init__(self, jobId, jobDir, workDir, items):
        self.jobId = jobId
        self.jobDir = jobDir
        self.workDir = workDir
        self.items = items
        self.next = 0
        self.done = 0
        self.failed = []
        self.ended = False


def _ErrorText(error):
    # An OSError's text names the file (a path of this PC, with the user's
    # name); its reason alone does not. Other errors keep their message
    # (a JSON error names no path and its position is what to investigate).
    if isinstance(error, OSError):
        return error.strerror or type(error).__name__
    return str(error)


def _SamePath(left, right):
    """Whether two paths name one file (links followed, case as the OS
    compares it)."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return os.path.normcase(os.path.realpath(left)) \
        == os.path.normcase(os.path.realpath(right))


def _ReadJson(path):
    """The JSON object at `path`, or None."""
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _Numbered(items, no):
    """The item of list `items` whose ``no`` is `no`, or None."""
    if not isinstance(items, list):
        return None
    return next((item for item in items if isinstance(item, dict)
                 and type(item.get("no")) is int and item["no"] == no), None)


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
        # The thumbnails being drawn (one job's at a time), how the last run
        # of each job ended, and a number that moves with every thumbnail
        # written (the page's cache buster).
        self._thumbRun = None
        self._thumbsLast = {}
        self._thumbRevision = 0
        # The titles' preview that runs (one at a time, A6): its number and
        # its worker's process (under _titlesLock).
        self._titlesLock = threading.Lock()
        self._titlesTicket = 0
        self._titlesProcess = None
        # Whether the newest preview is between its claim and its end (its
        # worker may be starting: _titlesProcess is set once it runs).
        self._titlesBusy = False
        # Whether core/ArchiveWorkerInstaller.py is replacing the worker's
        # folder (ReserveSwap .. ReleaseSwap; set under _lock and
        # _titlesLock, read under either or _probeLock), and how many
        # swaps have ended (a start compares it with what it read before
        # its probe: _Swapped).
        self._swapping = False
        self._swaps = 0

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
        """Save the work folder: UpdateSettings' ``work_dir`` alone."""
        self.UpdateSettings({"work_dir": value})

    def _PrepareWorkDir(self, path):
        """Make work folder `path` (CheckWorkDir's) when it is not there,
        under _lock. Refused while a job runs: the job's folder is under the
        old one. Raises ArchiveError."""
        if self._run is not None:
            raise ArchiveError(BUSY, "切り抜きの実行中は作業フォルダを変えられません。")
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as error:
            raise ArchiveError(INVALID, f"作業フォルダを作れません: {error}")
        if not os.path.isdir(path):
            raise ArchiveError(INVALID, "作業フォルダのパスにはファイルがあります。")

    def ThumbnailBackground(self):
        """What a set's thumbnail is drawn on (FRAME_BACKGROUND, the
        default, or SETTINGS_BACKGROUND)."""
        value = SettingsManager.Get(THUMBNAIL_BACKGROUND_KEY)
        return value if value in THUMBNAIL_BACKGROUNDS else FRAME_BACKGROUND

    def UpdateSettings(self, body):
        """Save the page's settings, `body` {"work_dir"?,
        "thumbnail_background"?} (one at least, nothing else): both checked,
        then saved in one write (neither when one is refused or the write
        fails). The work folder is made when it is not there, and refused
        while a job runs. Raises ArchiveError, or SettingsUnreadable."""
        if not isinstance(body, dict) or not body \
                or not set(body) <= SETTINGS_KEYS:
            raise ArchiveError(INVALID, (
                "変える設定（work_dir / thumbnail_background）を指定してください。"))
        background = body.get("thumbnail_background")
        if "thumbnail_background" in body \
                and background not in THUMBNAIL_BACKGROUNDS:
            raise ArchiveError(INVALID, "サムネイルの背景の選び方が違います。")
        path = CheckWorkDir(body["work_dir"]) if "work_dir" in body else None
        changes = []
        with self._lock:
            if path is not None:
                self._PrepareWorkDir(path)
                changes.append((WORK_DIR_KEY, path))
            if "thumbnail_background" in body:
                changes.append((THUMBNAIL_BACKGROUND_KEY, background))
            SettingsManager.SetMany(changes)
        if path is not None:
            logger.info("Archive: the work folder was changed")
        if "thumbnail_background" in body:
            logger.info(f"Archive: the thumbnails' background is "
                        f"{background}")

    # ── the title templates (ADR-006 A6) ───────────────────────────────

    @staticmethod
    def _Templates(call, *args):
        """ArchiveTemplates' `call`, its refusal as an ArchiveError."""
        try:
            return call(*args)
        except ArchiveTemplates.TemplateError as error:
            raise ArchiveError(error.code, error.message) from None

    def Templates(self):
        return self._Templates(ArchiveTemplates.List)

    def Template(self, templateId):
        return self._Templates(ArchiveTemplates.Get, templateId)

    def SaveTemplate(self, templateId, template):
        return self._Templates(ArchiveTemplates.Save, templateId, template)

    def DuplicateTemplate(self, templateId):
        return self._Templates(ArchiveTemplates.Duplicate, templateId)

    def DeleteTemplate(self, templateId):
        return self._Templates(ArchiveTemplates.Delete, templateId)

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
                               f"read: {_ErrorText(error)}")
                return None
        return _Normalized(raw, jobId)

    def _WriteRecord(self, jobDir, record):
        """Write `record` to job.json whole (temp file, fsynced, + os.replace).
        Raises OSError."""
        self._WriteFile(jobDir, JOB_FILE, {"version": 1, **record})

    def _WriteFile(self, jobDir, name, content):
        """Write `content` as JSON to the job's file `name` whole (temp
        file, fsynced, + os.replace). Raises OSError."""
        data = json.dumps(content, ensure_ascii=False, indent=2)
        path = os.path.join(jobDir, name)
        with self._filesLock:
            fd, tmp = tempfile.mkstemp(
                dir=jobDir, prefix=os.path.splitext(name)[0] + ".",
                suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(data)
                    # On disk before the replace: if the rename reached the
                    # disk first, a power cut would leave the file empty or
                    # cut short (review.json holds the user's hand edits and
                    # has no other copy).
                    file.flush()
                    os.fsync(file.fileno())
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
            if record is None:
                continue
            export = record["export"]
            stale = record["state"] in _UNFINISHED
            staleExport = export is not None \
                and export["state"] in _UNFINISHED
            if not stale and not staleExport:
                continue
            if stale:
                record["state"] = "cancelled"
                record["logs"] = (record["logs"] + [STALE_NOTE])[-LOG_LINES:]
            if staleExport:
                export["state"] = "cancelled"
                export["logs"] = (export["logs"]
                                  + [STALE_EXPORT_NOTE])[-LOG_LINES:]
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
            if self._swapping:
                # Not run and not cached: the folder is being replaced.
                return ArchiveWorker.ProbeResult(False, None, None, SWAPPING,
                                                 None)
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
        put the worker in place since. Raises ArchiveError(UNAVAILABLE), or
        BUSY while the worker's folder is replaced."""
        if self._swapping:
            raise ArchiveError(BUSY, SWAPPING)
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

    def CachedProbe(self):
        """The last probe (ArchiveWorker.ProbeResult) without probing, or
        None (none yet, or dropped). ArchiveWorkerInstaller reads the
        installed version with it."""
        return self._probed

    # ── the worker's swap (ADR-006 A7) ─────────────────────────────────

    def ReserveSwap(self):
        """Hold the worker still for ArchiveWorkerInstaller's swap of its
        folder: granted when no job's analysis or export, no titles'
        preview and no probe runs; until ReleaseSwap none starts. Raises
        ArchiveError: STOPPING, BUSY (transient: a probe or a titles'
        preview runs)."""
        with self._lock:
            if self._stopping:
                raise ArchiveError(STOPPING, "Autona の終了中です。")
            if self._swapping:
                raise ArchiveError(BUSY, SWAPPING)
            if self._run is not None:
                raise ArchiveError(BUSY, SWAP_BUSY_JOB)
            # Not waited for: a probe takes up to 8 s and _lock must not
            # wait for one (the probe never takes _lock).
            if not self._probeLock.acquire(blocking=False):
                raise ArchiveError(BUSY, SWAP_BUSY_PROBE, transient=True)
            try:
                with self._titlesLock:
                    if self._titlesBusy or self._titlesProcess is not None:
                        raise ArchiveError(BUSY, SWAP_BUSY_TITLES,
                                           transient=True)
                    self._swapping = True
            finally:
                self._probeLock.release()
        logger.info("Archive: the worker is held for its swap")

    def ReleaseSwap(self):
        """End ReserveSwap's hold. The cached probe is dropped: the next
        status probes the worker now in the folder."""
        with self._lock:
            with self._titlesLock:
                self._swapping = False
                self._swaps += 1
            self._Forget()
        logger.info("Archive: the worker's swap is over")

    def _Swapped(self, swaps, again):
        """Whether a swap ended since the caller read ``_swaps`` (`swaps`)
        before its probe, which may then name the worker that was there
        (under _lock or _titlesLock): True the first time (the caller
        probes again), ArchiveError(BUSY) when it is `again`."""
        if self._swaps == swaps:
            return False
        if again:
            raise ArchiveError(BUSY, SWAPPING)
        logger.info("Archive: the worker was replaced since its probe; "
                    "probed again")
        return True

    def SwapBusy(self):
        """Why a swap would be refused now (ReserveSwap's words), or None:
        the installer's check before it downloads. Takes no hold."""
        with self._lock:
            if self._stopping:
                return "Autona の終了中です。"
            if self._swapping:
                return SWAPPING
            if self._run is not None:
                return SWAP_BUSY_JOB
        return None

    # ── what the page reads ────────────────────────────────────────────

    def Status(self, refresh=False):
        """{available, version, format, features (what the worker says it
        does beyond its format: "titles", ADR-006 A6), reason, work_dir,
        thumbnail_background, running_job, running_kind (ANALYSIS / EXPORT,
        or None)}. Probes the worker on this thread the first time (and when
        `refresh`)."""
        probe = self._Probe(refresh=refresh, since=self._probeCount)
        with self._lock:
            run = self._run
            running = run.record["id"] if run is not None else None
            kind = run.kind if run is not None else None
        return {"available": probe.available, "version": probe.version,
                "format": probe.format, "features": list(probe.features),
                "reason": probe.reason, "work_dir": self.WorkDir(),
                "thumbnail_background": self.ThumbnailBackground(),
                "running_job": running, "running_kind": kind}

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
        the job's analysis runs: a resumed job's sets.json is the previous
        run's until the worker writes its own at the end (an export does not
        touch it). The Job has ``thumbs`` too (ADR-006 A6: ``_ThumbsState``
        and ``files``, ``_ThumbFiles``: read outside _lock) and
        ``analysis_cached`` (``_AnalysisCached``; False while its analysis
        runs). Raises ArchiveError(NOT_FOUND)."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            view = self._RunView(jobId)
            analysing = view is not None and self._run.kind == ANALYSIS
            if view is None:
                record = self._DiskRecord(jobDir, jobId)
                if record is None:
                    raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
                view = _View(record, export=self._ExportView(record, jobDir))
            view["thumbs"] = self._ThumbsState(jobId)
        sets = None if analysing else self._ReadSets(jobDir)
        view["thumbs"]["files"] = self._ThumbFiles(jobDir, jobId, sets)
        view["analysis_cached"] = not analysing and self._AnalysisCached(
            workDir, jobDir, view, sets)
        return view, sets

    def _AnalysisCached(self, workDir, jobDir, record, sets):
        """Whether running job `record`'s analysis again (a reanalysis, a
        resume) would reuse the worker's cache: its out/.cache is there and
        so is the video it was made from -- by VideoFile's rule, with
        sets.json `sets` (None: no download is known), and for a URL not a
        download of the open codecs (a run downloads <id>.mp4 now:
        _Arguments). The worker keys the cache on the video's path, size and
        mtime: a video moved or downloaded again is analysed from the
        start. Read from the disk alone (outside _lock)."""
        if not os.path.isdir(os.path.join(jobDir, OUT_DIR, CACHE_DIR)):
            return False
        try:
            path = self._VideoPath(workDir, jobDir, record, sets or {})
        except ArchiveError:
            return False
        if re.match(r"https?://", record["source"]) \
                and path.lower().endswith(_OPEN_DOWNLOAD_SUFFIX):
            return False
        return os.path.isfile(path)

    def _DiskRecord(self, jobDir, jobId):
        """Job `jobId`'s job.json when it is not the running job (under
        _lock): one that says it (or its export) is running is not (this
        process runs ``_run`` alone) and reads as cancelled."""
        record = self._ReadRecord(jobDir, jobId)
        if record is not None:
            if record["state"] in _UNFINISHED:
                record["state"] = "cancelled"
            export = record["export"]
            if export is not None and export["state"] in _UNFINISHED:
                export["state"] = "cancelled"
        return record

    def _CurrentRecord(self, jobDir, jobId):
        """(job `jobId`'s record, the kind of its run or None) under _lock:
        the running one's, else its job.json. Raises
        ArchiveError(NOT_FOUND)."""
        run = self._run
        if run is not None and run.record["id"] == jobId:
            return dict(run.record), run.kind
        record = self._DiskRecord(jobDir, jobId)
        if record is None:
            raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        return record, None

    def _RunView(self, jobId):
        """The running job's view when it is `jobId` (under _lock): an
        analysis' progress is the job's, an export's its ``export``'s."""
        run = self._run
        if run is None or run.record["id"] != jobId:
            return None
        if run.kind == EXPORT:
            return _View(run.record,
                         export=self._ExportView(run.record, run.jobDir, run))
        return _View(run.record,
                     progress=dict(run.progress) if run.progress else None,
                     logs=run.logs, warnings=run.warnings,
                     export=self._ExportView(run.record, run.jobDir))

    def _ExportView(self, record, jobDir, run=None):
        """The view of job `record`'s last export, or None: its record, the
        clips folder (``out_dir``) and the clips of the folder's
        export.json (``files``; None while `run` -- the export running --
        rewrites it: on Windows an open file cannot be replaced)."""
        export = record.get("export")
        if export is None:
            return None
        view = dict(export, out_dir=os.path.join(jobDir, CLIPS_DIR))
        if run is not None:
            view.update(progress=dict(run.progress) if run.progress else None,
                        logs=list(run.logs), warnings=list(run.warnings),
                        files=None)
        else:
            view["files"] = self._ReadClips(jobDir)
        return view

    @staticmethod
    def _ReadClips(jobDir):
        """The clips the job's clips/export.json lists, or None."""
        path = os.path.join(jobDir, CLIPS_DIR, EXPORT_FILE)
        try:
            with open(path, encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            logger.warning(
                f"Archive: {CLIPS_DIR}/{EXPORT_FILE} of "
                f"{os.path.basename(jobDir)} could not be read: "
                f"{_ErrorText(error)}")
            return None
        clips = data.get("clips") if isinstance(data, dict) else None
        if not isinstance(clips, list):
            return None
        return [clip for clip in map(_Clip, clips) if clip is not None]

    # ── what the preview plays (ADR-006 A3, D7) ────────────────────────

    def _Served(self, jobId):
        """(work folder, job folder, job.json, running) of job `jobId` for
        the preview's files: `running` while its analysis runs (an export
        only reads them). Raises ArchiveError(NOT_FOUND)."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            run = self._run
            if run is not None and run.record["id"] == jobId:
                return (workDir, jobDir, dict(run.record),
                        run.kind == ANALYSIS)
        record = self._ReadRecord(jobDir, jobId)
        if record is None:
            raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        return workDir, jobDir, record, False

    def _VideoPath(self, workDir, jobDir, record, sets=None):
        """The video job `record` was run on, by VideoFile's rule (not
        checked to be there or to be a video): the file of this PC its
        job.json names, or the download sets.json (`sets`, read when None)
        names in this work folder's downloads/. Raises
        ArchiveError(NOT_FOUND, VIDEO_MISSING)."""
        missing = ArchiveError(NOT_FOUND, VIDEO_MISSING)
        source = record["source"]
        if re.match(r"https?://", source):
            if sets is None:
                sets = self._ReadSets(jobDir)
            name = _FileName((sets or {}).get("source"))
            if name is None:
                raise missing
            path = os.path.join(workDir, DOWNLOADS_DIR, name)
            if not _Under(workDir, path):
                raise missing
            return path
        try:
            return CheckSource(source)
        except ArchiveError:
            raise missing from None

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
        path = self._VideoPath(workDir, jobDir, record)
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
        path = self._FramePath(jobDir, self._ReadSets(jobDir), int(number))
        if path is None:
            raise missing
        return path, FRAME_TYPE

    @staticmethod
    def _FramePath(jobDir, sets, number):
        """The file of set `number`'s frame sets.json (`sets`) names in the
        job's out/ (FrameFile's checks), or None."""
        row = _Numbered((sets or {}).get("sets"), number)
        name = _FileName(row.get("thumb")) if row is not None else None
        if name is None or not name.lower().endswith(_FRAME_SUFFIXES):
            return None
        # Resolved (links followed) inside this job's out/, itself inside
        # the job's folder: not another job's file, not a download.
        out = os.path.join(jobDir, OUT_DIR)
        path = os.path.join(out, name)
        if (not _Under(jobDir, out) or not _Under(out, path)
                or not os.path.isfile(path)):
            return None
        return path

    def ThumbFile(self, jobId, number):
        """(path, type) of set `number`'s thumbnail (thumbs/<no>.png, ADR-006
        A6), as FrameFile serves a frame: none while the job's analysis
        runs, nor one resolving outside the job's thumbs/. Raises
        ArchiveError(NOT_FOUND)."""
        missing = ArchiveError(NOT_FOUND, THUMB_MISSING)
        if not isinstance(number, str) \
                or not SET_NUMBER_PATTERN.fullmatch(number):
            raise missing
        _workDir, jobDir, _record, running = self._Served(jobId)
        if running:
            raise missing
        folder = os.path.join(jobDir, THUMBS_DIR)
        path = os.path.join(folder, f"{int(number)}.png")
        if (not _Under(jobDir, folder) or not _Under(folder, path)
                or not os.path.isfile(path)):
            raise missing
        return path, THUMB_TYPE

    @staticmethod
    def _ReadSets(jobDir):
        path = os.path.join(jobDir, OUT_DIR, SETS_FILE)
        try:
            with open(path, encoding="utf-8") as file:
                sets = json.load(file)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            logger.warning(
                f"Archive: {OUT_DIR}/{SETS_FILE} of "
                f"{os.path.basename(jobDir)} could not be read: "
                f"{_ErrorText(error)}")
            return None
        return sets if isinstance(sets, dict) else None

    @staticmethod
    def _ReadResults(jobDir):
        """The results the job was given (results.json), or None."""
        try:
            with open(os.path.join(jobDir, RESULTS_FILE),
                      encoding="utf-8") as file:
                results = json.load(file)
        except (OSError, ValueError):
            return None
        return results if isinstance(results, dict) else None

    # ── the review and the export (ADR-006 A4) ─────────────────────────

    @staticmethod
    def _DoneSets(jobDir, jobId):
        """The out/sets.json of a done job, for its review and its export.
        Raises ArchiveError(UNREADABLE) when it cannot be read: a done job
        has one, and the review is made against it (an empty one in its
        place would let a save write over the operator's review)."""
        path = os.path.join(jobDir, OUT_DIR, SETS_FILE)
        try:
            with open(path, encoding="utf-8") as file:
                sets = json.load(file)
        except (OSError, ValueError) as error:
            logger.warning(f"Archive: {SETS_FILE} of {jobId} could not be "
                           f"read: {_ErrorText(error)}")
            sets = None
        if not isinstance(sets, dict):
            raise ArchiveError(UNREADABLE, (
                "このジョブのセットの一覧（out/sets.json）が読めません。"
                "ほかのアプリが使っていないか確かめて、ページを読み直してください。"))
        return sets

    def _ReadReview(self, jobDir, jobId, sets):
        """The job's review.json as it applies to `sets`
        (ArchiveReview.Normalize): none when there is none or it is not a
        review of this format (not JSON, another shape: the next save
        writes over it). Raises ArchiveError(UNREADABLE) when it is there
        but cannot be read (another app holds it, no permission): an empty
        review in its place would let the page's next save write over it."""
        path = os.path.join(jobDir, REVIEW_FILE)
        with self._filesLock:
            try:
                with open(path, encoding="utf-8") as file:
                    raw = json.load(file)
            except FileNotFoundError:
                return ArchiveReview.Empty()
            except ValueError as error:     # not JSON, not UTF-8
                logger.warning(f"Archive: {REVIEW_FILE} of {jobId} is not "
                               f"JSON ({error}); ignored")
                return ArchiveReview.Empty()
            except OSError as error:
                logger.warning(f"Archive: {REVIEW_FILE} of {jobId} could not "
                               f"be read: {_ErrorText(error)}")
                raise ArchiveError(UNREADABLE, (
                    "このジョブのレビュー（review.json）が読めません。"
                    "ほかのアプリが使っていないか確かめて、ページを読み直してください。"))
        if not isinstance(raw, dict) \
                or raw.get("format") != ArchiveReview.FORMAT:
            logger.warning(f"Archive: {REVIEW_FILE} of {jobId} is not of "
                           f"format {ArchiveReview.FORMAT}; ignored")
        return ArchiveReview.Normalize(raw, sets)

    def Review(self, jobId):
        """Job `jobId`'s review ({"format", "sets": {no: entry}}: what of
        its review.json still fits its out/sets.json). Empty for a job whose
        analysis is not done or runs (no sets to review). Raises
        ArchiveError: NOT_FOUND, UNREADABLE (its review.json or sets.json is
        there but cannot be read)."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            record, kind = self._CurrentRecord(jobDir, jobId)
        if kind == ANALYSIS or record["state"] != "done":
            return ArchiveReview.Empty()
        return self._ReadReview(jobDir, jobId, self._DoneSets(jobDir, jobId))

    def SaveReview(self, jobId, review):
        """Replace job `jobId`'s review with `review` ({"sets": {no:
        {"confirmed"?, "start"?, "end"?, "set"?, "removed"?}}},
        ArchiveReview.Check) ->
        the review saved. Refused while the job's analysis runs and for one
        whose analysis is not done; taken while it exports (the export runs
        on the request it started with). Raises ArchiveError: NOT_FOUND,
        BUSY, NOT_DONE, UNREADABLE (its sets.json), INVALID,
        WORK_DIR_UNUSABLE."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            record, kind = self._CurrentRecord(jobDir, jobId)
            if kind == ANALYSIS:
                raise ArchiveError(BUSY, "このジョブは解析の実行中です。")
            if record["state"] != "done":
                raise ArchiveError(NOT_DONE, "解析が完了したジョブだけ編集できます。")
            sets = self._DoneSets(jobDir, jobId)
            try:
                checked = ArchiveReview.Check(review, sets)
            except ArchiveReview.ReviewError as error:
                raise ArchiveError(INVALID, str(error))
            try:
                self._WriteFile(jobDir, REVIEW_FILE,
                                ArchiveReview.Stored(checked, sets))
            except OSError as error:
                raise ArchiveError(WORK_DIR_UNUSABLE,
                                   f"作業フォルダに書けません: {error}")
        return checked

    def _ExportRequest(self, workDir, jobDir, record):
        """The export request (vodsplit export.validate_edits) of job
        `record`: its confirmed sets as reviewed, the video it was run on,
        and what ``run`` gave the titles (sets.json's event and video id,
        results.json's bracket URL and date). Raises ArchiveError: NOT_DONE,
        UNREADABLE (its sets.json or review.json), INVALID (nothing
        confirmed), SOURCE_UNUSABLE (the video is gone)."""
        if record["state"] != "done":
            raise ArchiveError(NOT_DONE, "解析が完了したジョブだけ書き出せます。")
        sets = self._DoneSets(jobDir, record["id"])
        review = self._ReadReview(jobDir, record["id"], sets)
        chosen = ArchiveReview.ExportSets(review, sets)
        if not chosen:
            raise ArchiveError(INVALID, (
                "確定したセットがありません。書き出すセットを確定してください。"))
        try:
            path = self._VideoPath(workDir, jobDir, record, sets)
        except ArchiveError:
            path = None
        if path is None or not os.path.isfile(path):
            raise ArchiveError(SOURCE_UNUSABLE, (
                "元動画が見つからないため書き出せません。"
                "動画を移動・削除していないか確かめてください。"))
        results = self._ReadResults(jobDir) or {}
        return {"source": path,
                "source_id": _Text(sets.get("source_id")),
                "event": _Text(sets.get("event")),
                "bracket_url": _Text(results.get("bracket_url")),
                "date": _Text(results.get("date")),
                "sets": chosen}

    def StartExport(self, jobId):
        """Cut job `jobId`'s confirmed sets with the worker's ``export``
        into <job>/clips/ -> its Job (its ``export`` running). Raises
        ArchiveError: NOT_FOUND, BUSY (a job or an export runs), NOT_DONE,
        INVALID (nothing confirmed), SOURCE_UNUSABLE, UNAVAILABLE /
        STOPPING, WORK_DIR_UNUSABLE."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            record, kind = self._CurrentRecord(jobDir, jobId)
            if kind is not None:
                self._Refuse(export=True)
        # The request's refusals come before the probe (up to 8 s).
        self._ExportRequest(workDir, jobDir, record)
        for again in (False, True):
            swaps = self._swaps
            probe = self._Available()
            templates = ArchiveTemplates.WorkerTemplates()
            with self._lock:
                if self._Swapped(swaps, again):
                    continue
                self._Refuse(export=True)
                record = self._DiskRecord(jobDir, jobId)
                if record is None:
                    raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
                request = self._ExportRequest(workDir, jobDir, record)
                record["export"] = {
                    "state": "running", "started_at": _Now(), "finished_at": None,
                    "exit_code": None, "error": None, "warnings": [], "logs": [],
                    "progress": None, "clips": None, "skipped": None}
                run = _Run(record, jobDir, workDir, kind=EXPORT)
                try:
                    os.makedirs(os.path.join(jobDir, CLIPS_DIR), exist_ok=True)
                    self._WriteFile(jobDir, EXPORT_REQUEST_FILE, request)
                    # Before job.json says it runs: what fails here leaves no
                    # export that runs without a worker.
                    arguments = self._Arguments(
                        run, self._WriteTemplates(jobDir, templates))
                    self._WriteRecord(jobDir, record)
                except OSError as error:
                    raise ArchiveError(WORK_DIR_UNUSABLE,
                                       f"作業フォルダに書けません: {error}")
                logger.info(f"Archive: job {jobId} export started "
                            f"({len(request['sets'])} sets)")
                self._Start(run, probe, arguments)
                return self._RunView(jobId)

    # ── the titles' preview (ADR-006 A6) ───────────────────────────────

    def PreviewTitles(self, jobId, template):
        """The titles `template` (a template the page has, saved or not:
        ArchiveTemplates.Check) makes for job `jobId`'s sets as reviewed,
        every one (confirmed or not) -> {"template": its name, "sets": [{no,
        title, description, chapters}]} (title / description None for a set
        with no result; chapters: how many chapter lines the description
        got, 0 when YouTube would ignore them). Only the sets whose range
        the worker takes (``_TitlesRequest``). The worker's ``titles`` on
        this thread, the probe and the run within TITLES_DEADLINE_SECS; one
        at a time (a newer preview kills this one's worker). Raises
        ArchiveError: INVALID (the template), NOT_FOUND, STOPPING, BUSY (its
        analysis runs), NOT_DONE, UNREADABLE, NO_SETS, UNAVAILABLE (no
        worker, or no time left: it ran past it, or the probe took it),
        UNSUPPORTED (a worker from before ``titles``), SUPERSEDED,
        WORKER_FAILED."""
        began = time.monotonic()
        try:
            checked = ArchiveTemplates.Check(template)
        except ArchiveTemplates.TemplateError as error:
            raise ArchiveError(INVALID, error.message) from None
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            if self._stopping:
                raise ArchiveError(STOPPING, "Autona の終了中です。")
            self._Sweep(workDir)
            record, kind = self._CurrentRecord(jobDir, jobId)
        if kind == ANALYSIS:
            raise ArchiveError(BUSY, "このジョブは解析の実行中です。")
        if record["state"] != "done":
            raise ArchiveError(NOT_DONE, "解析が完了したジョブだけプレビューできます。")
        request = self._TitlesRequest(jobDir, jobId)
        # The worker selects by match: this one is the template asked for.
        chosen = dict(checked, match=None)
        for again in (False, True):
            swaps = self._swaps
            probe = self._Available()
            if ArchiveWorker.TITLES_FEATURE not in probe.features:
                raise ArchiveError(UNSUPPORTED, TITLES_UNSUPPORTED)
            # What is left of the deadline once the probe (up to 8 s) is
            # done: the page gives up at 15 s.
            left = TITLES_DEADLINE_SECS - (time.monotonic() - began)
            if left < TITLES_LEAST_SECS:
                raise ArchiveError(UNAVAILABLE, TITLES_OUT_OF_TIME)
            timeout = round(min(ArchiveWorker.TITLES_TIMEOUT_SECS, left), 1)
            ticket = self._ClaimTitles(swaps, again)
            if ticket is not None:
                break
        try:
            answer = self._RunTitles(probe, request, chosen, ticket, timeout)
        finally:
            with self._titlesLock:
                if ticket == self._titlesTicket:
                    self._titlesProcess = None
                    self._titlesBusy = False
        rows = []
        for row in answer.get("sets") if isinstance(
                answer.get("sets"), list) else []:
            if not isinstance(row, dict) or type(row.get("no")) is not int:
                continue
            rows.append({"no": row["no"], "title": _Text(row.get("title")),
                         "description": _Text(row.get("description")),
                         "chapters": _Count(row.get("chapters")) or 0})
        return {"template": checked["name"], "sets": rows}

    def _TitlesRequest(self, jobDir, jobId):
        """The titles' request of done job `jobId`: its sets as reviewed
        (ArchiveReview.ReviewedSets: not the removed ones), only those whose
        range the worker takes -- vodsplit's validate_edits refuses the
        whole request for one shorter than MIN_CLIP (the review keeps
        sets.json's own range, which may be) --, with what ``run`` gave the
        titles. Raises
        ArchiveError: UNREADABLE, NO_SETS (none left)."""
        sets = self._DoneSets(jobDir, jobId)
        review = self._ReadReview(jobDir, jobId, sets)
        rows = [item for item in ArchiveReview.ReviewedSets(review, sets)
                if item["end"] - item["start"] >= ArchiveReview.MIN_CLIP]
        if not rows:
            raise ArchiveError(NO_SETS, TITLES_NO_SETS)
        results = self._ReadResults(jobDir) or {}
        return {"source": None,
                "source_id": _Text(sets.get("source_id")),
                "event": _Text(sets.get("event")),
                "bracket_url": _Text(results.get("bracket_url")),
                "date": _Text(results.get("date")),
                "sets": rows}

    def _ClaimTitles(self, swaps, again):
        """Make this preview the one that runs: the older one's worker is
        killed (its request answers SUPERSEDED) and waited for a moment, so
        two do not run at once -> this preview's ticket, or None when a
        swap ended since its probe (`swaps`, `again`: _Swapped). Raises
        ArchiveError: STOPPING, BUSY (the worker's folder is replaced)."""
        with self._titlesLock:
            if self._stopping:
                raise ArchiveError(STOPPING, "Autona の終了中です。")
            if self._swapping:
                raise ArchiveError(BUSY, SWAPPING)
            if self._Swapped(swaps, again):
                return None
            self._titlesTicket += 1
            ticket = self._titlesTicket
            older, self._titlesProcess = self._titlesProcess, None
            self._titlesBusy = True
        if older is not None:
            logger.info("Archive: a newer titles' preview replaces the "
                        "running one")
            ArchiveWorker.StopTitles(older, _TITLES_KILL_WAIT_SECS)
        return ticket

    def _RunTitles(self, probe, request, template, ticket, timeout):
        """The worker's ``titles`` of `request` with `template` (written to
        a temporary folder) for the preview of `ticket` -> its done line.
        Raises ArchiveError."""
        def started(process):
            with self._titlesLock:
                if ticket == self._titlesTicket:
                    self._titlesProcess = process
                    return
            # A newer preview, or the shutdown, came first.
            ArchiveWorker.StopTitles(process)

        with tempfile.TemporaryDirectory(prefix="autona-titles-") as folder:
            requestPath = os.path.join(folder, "request.json")
            templatePath = os.path.join(folder, "template.json")
            try:
                for path, content in ((requestPath, request),
                                      (templatePath, template)):
                    with open(path, "w", encoding="utf-8") as file:
                        json.dump(content, file, ensure_ascii=False)
            except OSError as error:
                raise ArchiveError(WORKER_FAILED, (
                    f"タイトルのプレビューを用意できません: {error}")) from None
            try:
                return ArchiveWorker.RunTitles(
                    probe.command, requestPath, templatePath,
                    timeout=timeout, started=started)
            except ArchiveWorker.TitlesError as error:
                with self._titlesLock:
                    replaced = ticket != self._titlesTicket
                if replaced and self._stopping:
                    raise ArchiveError(STOPPING, "Autona の終了中です。") \
                        from None
                if replaced:
                    raise ArchiveError(SUPERSEDED, TITLES_SUPERSEDED) \
                        from None
                raise ArchiveError(UNAVAILABLE if error.timeout
                                   else WORKER_FAILED, error.message) from None

    # ── the thumbnails (ADR-006 A6) ────────────────────────────────────

    def _RefuseThumbs(self, kind):
        """Refuse thumbnails while the app closes, other thumbnails are
        drawn or the job's analysis runs (`kind`: _CurrentRecord's; under
        _lock)."""
        if self._stopping:
            raise ArchiveError(STOPPING, "Autona の終了中です。")
        if self._thumbRun is not None:
            raise ArchiveError(BUSY, (
                "サムネイルを作っているところです。終わってからもう一度押してください。"))
        if kind == ANALYSIS:
            raise ArchiveError(BUSY, "このジョブは解析の実行中です。")

    def StartThumbs(self, jobId):
        """Draw job `jobId`'s confirmed sets' thumbnails (the ones with a
        result) one at a time into <job>/thumbs/<no>.png -> its Job (its
        ``thumbs`` running). Raises ArchiveError: NOT_FOUND, STOPPING, BUSY,
        NOT_DONE, UNREADABLE, INVALID (no confirmed set with a result),
        UNAVAILABLE (the thumbnail's template cannot be read),
        WORK_DIR_UNUSABLE; and what the shell's renderer raises when the
        first render cannot start (NotAvailable: the app is closing)."""
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            record, kind = self._CurrentRecord(jobDir, jobId)
            self._RefuseThumbs(kind)
        if record["state"] != "done":
            raise ArchiveError(NOT_DONE, "解析が完了したジョブだけサムネイルを作れます。")
        sets = self._DoneSets(jobDir, jobId)
        review = self._ReadReview(jobDir, jobId, sets)
        chosen = [item for item in ArchiveReview.ExportSets(review, sets)
                  if item["set"] is not None]
        if not chosen:
            raise ArchiveError(INVALID, (
                "結果のある確定したセットがありません。セットを確定してください。"))
        thumbnails = ThumbnailService.instance
        if thumbnails is None:
            raise ArchiveError(UNAVAILABLE, "サムネイルを作れません。")
        useFrame = self.ThumbnailBackground() == FRAME_BACKGROUND
        event = _Text(sets.get("event"))
        date = _Text((self._ReadResults(jobDir) or {}).get("date"))
        cache = {}
        items = []
        try:
            # Read once per run; SetPayload copies it for each set.
            base = thumbnails.SetBase()
            for item in chosen:
                frame = self._FramePath(jobDir, sets, item["no"]) \
                    if useFrame else None
                # The colours: the review's, never the export request's.
                skins = ArchiveReview.Skins(review, item["no"])
                data = ArchiveThumbnails.SetData(
                    item["set"], event, GameAssetCatalog.instance,
                    date=date, cache=cache, skins=skins)
                # A file URL: the frame's name may hold "#" or spaces.
                background = Path(os.path.realpath(frame)).as_uri() \
                    if frame is not None else None
                items.append((item["no"],
                              thumbnails.SetPayload(data, background, base=base),
                              dict(item, skins=skins)))
        except (OSError, ValueError) as error:
            logger.warning(f"Archive: job {jobId}'s thumbnails could not be "
                           f"prepared: {_ErrorText(error)}")
            raise ArchiveError(UNAVAILABLE, (
                "サムネイルのテンプレートを読めません。")) from None
        try:
            os.makedirs(os.path.join(jobDir, THUMBS_DIR), exist_ok=True)
        except OSError as error:
            raise ArchiveError(WORK_DIR_UNUSABLE,
                               f"作業フォルダに書けません: {error}") from None
        run = _ThumbRun(jobId, jobDir, workDir, items)
        with self._lock:
            current, kind = self._CurrentRecord(jobDir, jobId)
            self._RefuseThumbs(kind)
            # Looked at again: a reanalysis may have begun since the first.
            if current["state"] != "done":
                raise ArchiveError(NOT_DONE,
                                   "解析が完了したジョブだけサムネイルを作れます。")
            self._thumbRun = run
            self._thumbsLast.pop(jobId, None)
        logger.info(f"Archive: job {jobId} thumbnails started "
                    f"({len(items)} sets)")
        self._ClearThumbs(run)
        self._NextThumb(run, first=True)
        return self.Job(jobId)[0]

    def _NextThumb(self, run, first=False):
        """Start drawing `run`'s next set, or end it when there is none (or
        the app closes). The first on the request's thread (what the
        renderer raises goes to it), the next ones on the main thread."""
        with self._lock:
            if run.ended:
                return
            if self._stopping or run.next >= len(run.items):
                self._EndThumbs(run, THUMBS_CANCELLED if self._stopping
                                else THUMBS_DONE)
                return
            no, payload, _item = run.items[run.next]
            run.next += 1
        # Taken once, by whichever comes first of the renderer's answer and
        # its raising (as RenderLane's run).
        answer = threading.Lock()

        def done(encode, error):
            if answer.acquire(blocking=False):
                self._ThumbRendered(run, no, encode, error)

        try:
            ThumbnailService.instance.RenderSet(payload, done)
        except BaseException:
            if answer.acquire(blocking=False):
                with self._lock:
                    run.failed.append(no)
                    self._EndThumbs(run, THUMBS_CANCELLED if self._stopping
                                    else THUMBS_FAILED)
            if first:
                raise
            logger.opt(exception=True).warning(
                f"Archive: job {run.jobId}: a thumbnail render could not "
                f"start")

    def _ThumbRendered(self, run, no, encode, error):
        """The renderer drew set `no` (on the main thread): the PNG is a
        job's to encode and write."""
        if encode is None:
            logger.warning(f"Archive: job {run.jobId}: the thumbnail of set "
                           f"{no} could not be drawn: {error}")
            self._ThumbSaved(run, no, False)
            return
        try:
            executor = TaskExecutor.instance
            future = executor.submit(
                self._SaveThumb, run, no, encode, executor.stopping,
                task_name="archive thumbnail save",
                then=lambda saved: self._ThumbSaved(run, no, saved),
                on_error=lambda failure: self._ThumbFailed(run, no, failure))
        except Exception as failure:
            self._ThumbFailed(run, no, failure)
            return
        # A job the shutdown cancels runs neither `then` nor `on_error`.
        future.add_done_callback(
            lambda finished: finished.cancelled() and self._ThumbSaved(
                run, no, None))

    def _SaveThumb(self, run, no, encode, stopping):
        """The job: True once thumbs/<no>.png is written, False when nothing
        was drawn, None when the executor stops first. Raises OSError."""
        if stopping.is_set():
            return None
        png = encode("PNG")
        if not png:
            return False
        ImageImport.WriteFile(
            os.path.join(run.jobDir, THUMBS_DIR, f"{no}.png"), png)
        item = next(entry for number, _payload, entry in run.items
                    if number == no)
        target = self._ThumbBesideClip(run, item)
        if target is not None:
            try:
                ImageImport.WriteFile(target, png)
            except OSError:
                logger.opt(exception=True).warning(
                    f"Archive: job {run.jobId}: the thumbnail of set {no} "
                    f"could not be copied beside its clip")
                target = None
        # What it was drawn for (Job shows it while the review still gives
        # the set this result, range and colours) and the copy (the next run
        # removes it: a later export may name the clip otherwise). No
        # "skins" is every character in skin 0 (an entry written before
        # there were colours reads so too).
        drawn = self._ReadThumbsIndex(run.jobDir)
        drawn[str(no)] = {
            "start": item["start"], "end": item["end"], "set": item["set"],
            **({"skins": item["skins"]} if item.get("skins") else {}),
            "beside": os.path.basename(target) if target else None}
        self._WriteThumbsIndex(run.jobDir, drawn)
        return True

    def _ThumbBesideClip(self, run, item):
        """Where set `item`'s thumbnail goes beside its clip
        (``<clip>.png`` in clips/), or None unless that clip is certainly
        the set as drawn: no export of the job runs, clips/export.json's
        source is the job's video, its clip of that number was exported and
        is there (``NN_<title>.mp4``), and export_request.json gave the set
        the result and range the review gives it now."""
        with self._lock:
            current = self._run
            if current is not None and current.kind == EXPORT \
                    and current.record["id"] == run.jobId:
                return None
        no = item["no"]
        clips = os.path.join(run.jobDir, CLIPS_DIR)
        listed = _ReadJson(os.path.join(clips, EXPORT_FILE))
        record = self._ReadRecord(run.jobDir, run.jobId)
        if listed is None or record is None:
            return None
        try:
            video = self._VideoPath(run.workDir, run.jobDir, record)
        except ArchiveError:
            return None
        if not _SamePath(listed.get("source"), video):
            return None
        clip = _Numbered(listed.get("clips"), no)
        name = _FileName(clip.get("file")) if clip is not None else None
        if clip is None or clip.get("exported") is not True or name is None \
                or not re.fullmatch(re.escape(f"{no:02d}") + r"_.+\.mp4",
                                    name):
            return None
        path = os.path.join(clips, name)
        if not _Under(run.jobDir, clips) or not _Under(clips, path) \
                or not os.path.isfile(path):
            return None
        asked = _Numbered((_ReadJson(os.path.join(
            run.jobDir, EXPORT_REQUEST_FILE)) or {}).get("sets"), no)
        if asked is None or asked.get("start") != item["start"] \
                or asked.get("end") != item["end"] \
                or ArchiveReview.ResultKey(asked.get("set")) \
                != ArchiveReview.ResultKey(item["set"]):
            return None
        return os.path.splitext(path)[0] + ".png"

    def _ThumbFailed(self, run, no, failure):
        logger.opt(exception=failure).warning(
            f"Archive: job {run.jobId}: the thumbnail of set {no} could not "
            f"be saved")
        self._ThumbSaved(run, no, False)

    def _ThumbSaved(self, run, no, saved):
        """Set `no` is done (`saved`: True written, False failed, None the
        executor stopped): the next one is started from the main thread, or
        the run ends."""
        with self._lock:
            if run.ended:
                return
            if saved:
                run.done += 1
                self._thumbRevision += 1
            elif saved is False:
                run.failed.append(no)
            if saved is None or self._stopping:
                self._EndThumbs(run, THUMBS_CANCELLED)
                return
            if run.next >= len(run.items):
                self._EndThumbs(run, THUMBS_DONE)
                return
        try:
            ThumbnailService.instance.Later(lambda: self._NextThumb(run))
        except Exception:
            logger.opt(exception=True).warning(
                f"Archive: job {run.jobId}: the next thumbnail could not be "
                f"queued")
            with self._lock:
                self._EndThumbs(run, THUMBS_FAILED)

    def _EndThumbs(self, run, state):
        """End `run` as `state` (under _lock)."""
        run.ended = True
        if self._thumbRun is run:
            self._thumbRun = None
        self._thumbsLast[run.jobId] = {
            "state": state, "done": run.done, "total": len(run.items),
            "failed": sorted(run.failed)}
        logger.info(f"Archive: job {run.jobId} thumbnails {state} "
                    f"({run.done} of {len(run.items)} written, "
                    f"{len(run.failed)} failed)")

    def _ThumbsState(self, jobId):
        """How far job `jobId`'s thumbnails are (under _lock): {"state"
        (THUMBS_RUNNING while they are drawn, how this process' last run of
        them ended, or None), "done", "total", "failed" (the sets'
        numbers), "revision" (moves with every thumbnail written)}. Job
        adds "files" (``_ThumbFiles``)."""
        run = self._thumbRun
        if run is not None and run.jobId == jobId:
            view = {"state": THUMBS_RUNNING, "done": run.done,
                    "total": len(run.items), "failed": sorted(run.failed)}
        else:
            view = dict(self._thumbsLast.get(jobId) or {
                "state": None, "done": 0, "total": 0, "failed": []})
        view["revision"] = self._thumbRevision
        return view

    def _ThumbFiles(self, jobDir, jobId, sets):
        """The numbers of the sets whose thumbnail (thumbs/<no>.png) still
        shows them as reviewed: thumbs.json drew it for the result, the
        range and the colours (none: all skin 0) the review gives the set
        now. [] without `sets` (the job's analysis runs, or no sets.json) or
        a review that cannot be read."""
        drawn = self._ReadThumbsIndex(jobDir) if sets is not None else {}
        if not drawn:
            return []
        try:
            review = self._ReadReview(jobDir, jobId, sets)
        except ArchiveError:
            return []
        folder = os.path.join(jobDir, THUMBS_DIR)
        files = []
        for item in ArchiveReview.ReviewedSets(review, sets):
            entry = drawn.get(str(item["no"]))
            if entry is not None and entry.get("start") == item["start"] \
                    and entry.get("end") == item["end"] \
                    and ArchiveReview.ResultKey(entry.get("set")) \
                    == ArchiveReview.ResultKey(item["set"]) \
                    and (entry.get("skins") or {}) \
                    == ArchiveReview.Skins(review, item["no"]) \
                    and os.path.isfile(os.path.join(folder,
                                                    f"{item['no']}.png")):
                files.append(item["no"])
        return sorted(files)

    def _ReadThumbsIndex(self, jobDir):
        """thumbs/thumbs.json's {no: {"start", "end", "set", "skins"?,
        "beside"}}, or {} (none, or not of THUMBS_FORMAT)."""
        with self._filesLock:
            data = _ReadJson(os.path.join(jobDir, THUMBS_DIR, THUMBS_FILE))
        drawn = data.get("sets") if data is not None \
            and data.get("format") == THUMBS_FORMAT else None
        if not isinstance(drawn, dict):
            return {}
        return {key: entry for key, entry in drawn.items()
                if SET_NUMBER_PATTERN.fullmatch(key)
                and isinstance(entry, dict)}

    def _WriteThumbsIndex(self, jobDir, drawn):
        """Write thumbs/thumbs.json whole. Raises OSError."""
        self._WriteFile(os.path.join(jobDir, THUMBS_DIR), THUMBS_FILE,
                        {"format": THUMBS_FORMAT, "sets": drawn})

    def _ClearThumbs(self, run):
        """Before `run`'s first render: remove the thumbnails of the sets it
        does not draw again (no longer confirmed, or without a result) and
        every PNG an earlier run put beside a clip (thumbs.json names them:
        an export since may have named the clip otherwise); what
        thumbs.json says of the sets drawn again stays until each is. Never
        raises: what cannot be removed is logged (Job shows no thumbnail
        thumbs.json does not name)."""
        drawn = self._ReadThumbsIndex(run.jobDir)
        clips = os.path.join(run.jobDir, CLIPS_DIR)
        for entry in drawn.values():
            name = _FileName(entry.get("beside"))
            if name is None or not _BESIDE_NAME.fullmatch(name):
                continue
            path = os.path.join(clips, name)
            if _Under(run.jobDir, clips) and _Under(clips, path) \
                    and os.path.isfile(path):
                self._RemoveThumb(run, path)
        numbers = {no for no, _payload, _item in run.items}
        folder = os.path.join(run.jobDir, THUMBS_DIR)
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        for name in names:
            found = _THUMB_NAME.fullmatch(name)
            if found and int(found.group(1)) not in numbers:
                self._RemoveThumb(run, os.path.join(folder, name))
        kept = {key: dict(entry, beside=None) for key, entry in drawn.items()
                if int(key) in numbers}
        try:
            self._WriteThumbsIndex(run.jobDir, kept)
        except OSError:
            logger.opt(exception=True).warning(
                f"Archive: job {run.jobId}: {THUMBS_FILE} could not be "
                f"written")

    @staticmethod
    def _RemoveThumb(run, path):
        try:
            os.remove(path)
        except OSError:
            logger.opt(exception=True).warning(
                f"Archive: job {run.jobId}: an old thumbnail could not be "
                f"removed ({os.path.basename(path)})")

    # ── starting, cancelling, resuming ─────────────────────────────────

    def Create(self, source, results=None, event=None):
        """Start a job on `source` -> its Job. Raises ArchiveError: INVALID
        (the request), UNAVAILABLE / STOPPING (no worker, the app is
        closing), BUSY (a job runs), WORK_DIR_UNUSABLE."""
        source = CheckSource(source)
        results = CheckResults(results)
        event = CheckEvent(event)
        for again in (False, True):
            swaps = self._swaps
            probe = self._Available()
            templates = ArchiveTemplates.WorkerTemplates()
            with self._lock:
                if self._Swapped(swaps, again):
                    continue
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
                    "worker_version": probe.version, "export": None,
                }
                run = _Run(record, jobDir, workDir)
                try:
                    os.makedirs(os.path.join(jobDir, OUT_DIR), exist_ok=True)
                    if results is not None:
                        self._WriteFile(jobDir, RESULTS_FILE, results)
                    # Before job.json says it runs: what fails here leaves no
                    # job that runs without a worker.
                    arguments = self._Arguments(
                        run, self._WriteTemplates(jobDir, templates),
                        probe.features)
                    self._WriteRecord(jobDir, record)
                except OSError as error:
                    raise ArchiveError(WORK_DIR_UNUSABLE,
                                       f"作業フォルダに書けません: {error}")
                logger.info(f"Archive: job {jobId} created")
                self._Start(run, probe, arguments)
                return self._RunView(jobId)

    def Resume(self, jobId):
        """Run job `jobId` again (the worker resumes from its caches) ->
        its Job. Its job.json's source passes a start's checks again (the
        file is the operator's to edit). Raises ArchiveError: NOT_FOUND,
        ALREADY_DONE, BUSY, SOURCE_UNUSABLE, UNAVAILABLE / STOPPING,
        WORK_DIR_UNUSABLE."""
        return self._RunAgain(jobId)

    def Reanalyze(self, jobId, results):
        """Run job `jobId`'s analysis again with `results` (as Create takes
        them: CheckResults; none is refused) -> its Job. Any job that is not
        running (done or ended otherwise; not while its thumbnails are
        drawn): its results.json replaced, what the run makes again removed
        (_ClearDerived), then Resume's command -- the worker reuses its
        analysis cache while the video is there, and splits and names the
        sets by the results. Raises ArchiveError: INVALID, NOT_FOUND, BUSY,
        SOURCE_UNUSABLE, UNAVAILABLE / STOPPING, WORK_DIR_UNUSABLE."""
        if isinstance(results, dict) and results.get("sets") == []:
            raise ArchiveError(INVALID, (
                "区切り直しに使う結果データにセットがありません。1 件以上のセットを含む結果を指定してください。"))
        results = CheckResults(results)
        if results is None:
            raise ArchiveError(INVALID, "区切り直しに使う結果データを指定してください。")
        return self._RunAgain(jobId, results)

    def _RunAgain(self, jobId, results=None):
        """Run job `jobId`'s analysis again with the command _Arguments
        makes -> its Job: a resume (`results` None) or a reanalysis (its
        results.json replaced by `results`, and _ClearDerived, once nothing
        refuses it). Its job.json's source passes a start's checks again."""
        reanalysis = results is not None
        workDir = self.WorkDir()
        jobDir = self._JobDir(jobId, workDir)
        with self._lock:
            self._Sweep(workDir)
            record = self._Rerunnable(jobDir, jobId, reanalysis)
        self._ResumableSource(record, reanalysis)
        for again in (False, True):
            swaps = self._swaps
            probe = self._Available()
            templates = ArchiveTemplates.WorkerTemplates()
            with self._lock:
                if self._Swapped(swaps, again):
                    continue
                self._Refuse()
                record = self._Rerunnable(jobDir, jobId, reanalysis)
                record["source"] = self._ResumableSource(record, reanalysis)
                record.update(state="running", finished_at=None, exit_code=None,
                              sets=None, needs_review=None, error=None,
                              warnings=[], logs=[], progress=None,
                              worker_version=probe.version)
                if reanalysis:
                    record["has_results"] = True
                run = _Run(record, jobDir, workDir)
                pending = os.path.join(jobDir, RESULTS_FILE + ".new")
                final = os.path.join(jobDir, RESULTS_FILE)
                previous = None
                swapped = False
                try:
                    os.makedirs(os.path.join(jobDir, OUT_DIR), exist_ok=True)
                    if reanalysis:
                        # Written aside: results.json is replaced only once
                        # what can fail before job.json is written has not.
                        self._WriteFile(jobDir, RESULTS_FILE + ".new",
                                        results)
                    # Before job.json says it runs: what fails here leaves no
                    # job that runs without a worker.
                    templateFiles = self._WriteTemplates(jobDir, templates)
                    if reanalysis:
                        try:
                            with open(final, "rb") as file:
                                previous = file.read()
                        except FileNotFoundError:
                            previous = None
                        os.replace(pending, final)
                        swapped = True
                    # After the replace: --results names the file.
                    arguments = self._Arguments(run, templateFiles,
                                                probe.features)
                    self._WriteRecord(jobDir, record)
                except OSError as error:
                    self._DiscardResultsSwap(jobDir, pending, final,
                                             previous, swapped)
                    raise ArchiveError(WORK_DIR_UNUSABLE,
                                       f"作業フォルダに書けません: {error}")
                if reanalysis:
                    # job.json says it runs: what the new sets replace goes.
                    self._ClearDerived(jobDir, jobId)
                if reanalysis:
                    logger.info(f"Archive: job {jobId} analysed again with "
                                f"results ({len(results['sets'])} sets)")
                else:
                    logger.info(f"Archive: job {jobId} resumed")
                self._Start(run, probe, arguments)
                return self._RunView(jobId)

    def _DiscardResultsSwap(self, jobDir, pending, final, previous, swapped):
        """A reanalysis that failed before job.json was written: remove the
        results written aside and, when results.json was already replaced,
        put the earlier one back (or remove it when there was none). Never
        raises."""
        try:
            if os.path.exists(pending):
                os.remove(pending)
            if swapped:
                if previous is None:
                    os.remove(final)
                else:
                    with open(final, "wb") as file:
                        file.write(previous)
        except OSError:
            logger.opt(exception=True).warning(
                f"Archive: {RESULTS_FILE} could not be restored")

    def _Rerunnable(self, jobDir, jobId, reanalysis):
        """Job `jobId`'s job.json when its analysis may run again (under
        _lock): not while it runs (its analysis or its export); a resume's
        not done; a reanalysis' not while its thumbnails are drawn (from the
        sets and frames it replaces). Raises ArchiveError: BUSY, NOT_FOUND,
        ALREADY_DONE."""
        if self._RunView(jobId) is not None:
            raise ArchiveError(BUSY, "このジョブは実行中です。")
        record = self._DiskRecord(jobDir, jobId)
        if record is None:
            raise ArchiveError(NOT_FOUND, "その切り抜きジョブはありません。")
        if not reanalysis and record["state"] == "done":
            raise ArchiveError(ALREADY_DONE, "このジョブは完了しています。")
        thumbs = self._thumbRun
        if reanalysis and thumbs is not None and thumbs.jobId == jobId:
            raise ArchiveError(BUSY, (
                "このジョブのサムネイルを作っているところです。終わってからもう一度押してください。"))
        return record

    def _ClearDerived(self, jobDir, jobId):
        """Before job `jobId`'s analysis runs again with other results
        (Reanalyze; under _lock): remove what the run makes again and what
        was made from the sets it replaces -- out/'s frames (NN_<title>_bg
        .jpg: a set's title is in the name, so a renamed set's would stay)
        and title files (NN_<title>.txt; not one beside a clip of its name,
        which a run with --no-cut never cuts: not Autona's), the thumbnails
        (thumbs/<no>.png, drawn for the sets replaced; thumbs.json keeps
        only which clip each had a copy beside: the next drawing removes
        those copies) and how this process' last drawing of them ended.
        Left: out/sets.json
        and review.md (the worker replaces them; until then sets.json names
        the download), out/.cache (what makes the run quick), the download,
        review.json (ArchiveReview.Normalize keeps what still fits the new
        sets), the export (clips/, its request) and the copies of the
        thumbnails beside its clips (they show those clips), and anything
        else. Never raises: what cannot be removed is logged."""
        out = os.path.join(jobDir, OUT_DIR)
        try:
            names = os.listdir(out) if _Under(jobDir, out) else []
        except OSError:
            names = []
        for name in names:
            text = _OUT_TEXT_NAME.fullmatch(name)
            if _OUT_FRAME_NAME.fullmatch(name) or (
                    text and not os.path.exists(
                        os.path.join(out, text.group(1) + ".mp4"))):
                self._RemoveDerived(jobId, os.path.join(out, name))
        folder = os.path.join(jobDir, THUMBS_DIR)
        try:
            names = os.listdir(folder) if _Under(jobDir, folder) else []
        except OSError:
            names = []
        for name in names:
            if _THUMB_NAME.fullmatch(name):
                self._RemoveDerived(jobId, os.path.join(folder, name))
        if THUMBS_FILE in names:
            # What the drawn thumbnails were drawn for goes (Job shows no
            # thumbnail: no start / end / set to match); the copies beside
            # clips stay named, for the next drawing to remove them.
            besides = {key: {"beside": entry["beside"]}
                       for key, entry in self._ReadThumbsIndex(jobDir).items()
                       if entry.get("beside")}
            if besides:
                try:
                    self._WriteThumbsIndex(jobDir, besides)
                except OSError:
                    logger.warning(
                        f"Archive: job {jobId}: {THUMBS_FILE} could not be "
                        f"rewritten before its reanalysis")
            else:
                # As _ReadThumbsIndex reads it: on Windows a file another
                # thread has open cannot be removed.
                with self._filesLock:
                    self._RemoveDerived(jobId,
                                        os.path.join(folder, THUMBS_FILE))
        self._thumbsLast.pop(jobId, None)

    @staticmethod
    def _RemoveDerived(jobId, path):
        """Remove `path`; a failure is logged by its folder alone (a file of
        out/ is named after a set's players)."""
        try:
            os.remove(path)
        except OSError as error:
            logger.warning(
                f"Archive: job {jobId}: a file of its "
                f"{os.path.basename(os.path.dirname(path))}/ could not be "
                f"removed before its reanalysis ({type(error).__name__}, "
                f"errno {error.errno})")

    def Cancel(self, jobId):
        """Ask the running job `jobId` -- its analysis or its export -- to
        stop -> its Job (still running: the worker ends it). Raises
        ArchiveError: NOT_FOUND, NOT_RUNNING."""
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
    def _ResumableSource(record, reanalysis=False):
        """The source of job.json `record` as CheckSource reads it. Raises
        ArchiveError(SOURCE_UNUSABLE) with CheckSource's reason (`reanalysis`:
        worded for Reanalyze)."""
        try:
            return CheckSource(record["source"])
        except ArchiveError as error:
            reason = error.message.rstrip("。")
            what = "区切り直せません" if reanalysis else "再開できません"
            raise ArchiveError(SOURCE_UNUSABLE, (
                f"このジョブの元動画を使えないため{what}（{reason}）。"
                "新しいジョブとして始め直してください。"))

    def _Refuse(self, export=False):
        """Refuse a start (`export`: an export's) while the app closes, the
        worker's folder is replaced or a job runs its analysis or its
        export (under _lock)."""
        if self._stopping:
            raise ArchiveError(STOPPING, "Autona の終了中です。")
        if self._swapping:
            raise ArchiveError(BUSY, SWAPPING)
        if self._run is not None and self._run.kind == EXPORT:
            raise ArchiveError(BUSY, (
                "書き出しの実行中です。終わるか中止してから始めてください。"))
        if self._run is not None and export:
            raise ArchiveError(BUSY, (
                "切り抜きジョブの解析が実行中です。終わるか中止してから書き出してください。"))
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

    def _WriteTemplates(self, jobDir, templates):
        """Write `templates` (ArchiveTemplates.WorkerTemplates') into the
        job's templates/ as 01.json, 02.json ... in their order, what an
        earlier run left there removed first -> their paths (A6: the worker
        reads this snapshot, not user_data/). Raises OSError."""
        folder = os.path.join(jobDir, TEMPLATES_DIR)
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if name.endswith(".json"):
                    os.remove(os.path.join(folder, name))
        elif templates:
            os.makedirs(folder)
        paths = []
        for number, template in enumerate(templates, 1):
            name = f"{number:02d}.json"
            self._WriteFile(folder, name, template)
            paths.append(os.path.join(folder, name))
        return paths

    @staticmethod
    def _Arguments(run, templateFiles, features=()):
        """The worker's arguments for `run` with the templates' snapshot
        `templateFiles` (_WriteTemplates') and, for an analysis, the
        `features` of the worker that runs it (its probe's)."""
        # In ArchiveTemplates' order: the worker takes the first whose match
        # is in the event name (ADR-006 A6).
        templates = [word for path in templateFiles
                     for word in ("--template", path)]
        if run.kind == EXPORT:
            # The request names the video and the sets; clips/ has no
            # sets.json (the worker refuses a run's output folder).
            return ["export", os.path.join(run.jobDir, EXPORT_REQUEST_FILE),
                    "--out", os.path.join(run.jobDir, CLIPS_DIR),
                    *templates, "--progress", "json", "--cancel-on-stdin"]
        record = run.record
        # The download's codec: H.264 + AAC (the analysis decodes it about
        # twice as fast, and a livestream's archive may offer VP9 at 360p
        # only); a worker from before ``h264`` gets ``any`` -- the highest
        # resolution whatever the codec -- not its own default ``open``.
        # The preview of a YouTube job is the embedded player (ADR-006 D7),
        # not this file. A local file's run ignores it.
        codec = "h264" if ArchiveWorker.H264_FEATURE in features else "any"
        arguments = ["run", record["source"],
                     "--out", os.path.join(run.jobDir, OUT_DIR),
                     "--download-dir", os.path.join(run.workDir,
                                                    DOWNLOADS_DIR),
                     "--codec", codec]
        results = os.path.join(run.jobDir, RESULTS_FILE)
        if os.path.isfile(results):
            arguments += ["--results", results]
        if record.get("event"):
            # One word: a name starting with "-" is the value, not an
            # option (argparse reads "--event -x" as a missing value).
            arguments.append(f"--event={record['event']}")
        # --thumbs: each set's background frame, the set list's and the
        # preview's picture (FrameFile; ADR-006 A3).
        return arguments + ["--no-cut", "--thumbs", *templates, "--progress",
                            "json", "--cancel-on-stdin"]

    def _Start(self, run, probe, arguments):
        """Start the worker for `run` with `arguments` (_Arguments', made
        before job.json said it runs) and its two readers (under _lock).
        When it cannot be started the job ends failed and
        ArchiveError(UNAVAILABLE) is raised."""
        try:
            run.logFile = open(os.path.join(run.jobDir, LOG_FILE), "a",
                               encoding="utf-8")
            what = "export start" if run.kind == EXPORT else "start"
            run.logFile.write(f"--- {_Now()} {what} ---\n")
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
            self._Settle(run, state="failed", finished_at=_Now(), error={
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
    def _Settle(run, **end):
        """Write a run's end (its state, outcome, logs ...) where its kind
        keeps it: the job's own fields for an analysis, its ``export`` for
        an export."""
        (run.record["export"] if run.kind == EXPORT else run.record).update(
            end)

    @staticmethod
    def _AskCancel(run):
        """Write ``cancel`` to the worker's stdin and close it (once), after
        giving the worker its normal priority back (a below-normal worker on
        a busy machine reads the line only when the CPU is given to it)."""
        if run.cancelRequested:
            return
        run.cancelRequested = True
        if run.worker is None:
            return
        run.worker.RaisePriority()
        stdin = run.worker.proc.stdin
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
        if run.kind != EXPORT:
            hint = ("「再開」で続きから実行できます。詳しくは作業フォルダの worker.log を見てください。")
        elif kind == "error" and "PermissionError" in (
                _Text(final.get("message")) or ""):
            # vodsplit's internal error for a clip it cannot replace: the
            # earlier export's file, open in a player (Windows' sharing).
            hint = EXPORT_LOCKED_HINT
        else:
            hint = ("もう一度「確定分を書き出し」を押すと、書き出し済みのクリップを飛ばして続きから書き出します。"
                    "詳しくは作業フォルダの worker.log を見てください。")
        return "failed", {"code": code or "crashed", "message": message,
                          "hint": hint}

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
                end = dict(
                    state=state, finished_at=_Now(), exit_code=exitCode,
                    error=error, logs=list(run.logs),
                    warnings=list(run.warnings),
                    progress=dict(run.progress) if run.progress else None)
                if state == "done":
                    final = run.final or {}
                    if run.kind == EXPORT:
                        end.update(clips=_Count(final.get("clips")),
                                   skipped=_Count(final.get("skipped")))
                    else:
                        end.update(sets=_Count(final.get("sets")),
                                   needs_review=_Count(
                                       final.get("needs_review")))
                self._Settle(run, **end)
                try:
                    self._WriteRecord(run.jobDir, record)
                except OSError:
                    logger.opt(exception=True).warning(
                        f"Archive: job {record['id']} could not be written")
                if self._run is run:
                    self._run = None
            what = "export " if run.kind == EXPORT else ""
            logger.info(f"Archive: job {record['id']} {what}{state} (exit "
                        f"{exitCode})")
        finally:
            # Closed before the run is let go: a swap (ReserveSwap) is
            # granted once _run is None, and must find nothing of the job
            # left running.
            run.worker.Close()
            with self._lock:
                if self._run is run:
                    self._run = None
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
        """Refuse new jobs, cancel the running one (an analysis or an
        export) and wait up to `timeout` seconds for it to end; then end the
        worker and every process it started. Its job.json is written
        cancelled (resumable; an export goes on with the next one). A
        titles' preview's worker is killed (its request answers
        STOPPING)."""
        with self._lock:
            self._stopping = True
            run = self._run
            if run is not None:
                self._AskCancel(run)
        with self._titlesLock:
            self._titlesTicket += 1
            preview, self._titlesProcess = self._titlesProcess, None
        if preview is not None:
            ArchiveWorker.StopTitles(preview)
        if run is None:
            return
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
