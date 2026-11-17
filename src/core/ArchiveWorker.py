"""The archive clipper's worker: where it is, which version it is and how it
is started (ADR-006 D1).

The clipping (download, analysis, segmentation, later the cut) is not done
in Autona's process. It is vodsplit (the SmashVodSplitter repository), run
as a child process: its CLI ``run`` prints one JSON object per line
(``--progress json``) and stops on a ``cancel`` line or the end of its
stdin (``--cancel-on-stdin``). This module only finds it, asks its version
and starts it; what a job is, is core/ArchiveJobService.py's.

Where it is (``Locate``), in this order:

- ``AUTONA_VODSPLIT_CMD``: a whole command line, for development
  (``python -m vodsplit``; the cwd and PYTHONPATH are Autona's, which the
  child inherits). Split like a Windows command line: double quotes group,
  backslashes are kept (``SplitCommand``);
- ``vodsplit-worker/vodsplit-worker.exe`` under the current directory (the
  folder Autona.exe runs in; the separately distributed worker, D5).

Its version (``Probe``): ``<command> version`` prints one JSON line
``{"version", "format", "features"?}``; only ``SUPPORTED_FORMAT`` is
accepted (the sets.json format this Autona reads). A worker that is missing,
cannot be started, does not answer in ``PROBE_TIMEOUT_SECS`` or answers
something else is unavailable, with the reason in Japanese (the page shows
it). ``features`` names what a worker of that format does beyond it (vodsplit
V10: ``titles``); a worker from before says none, and only what needs the
feature is refused (ADR-006 A6: the titles' preview).

Its titles (``RunTitles``, ADR-006 A6): ``<command> titles <request>
--template <file> --format json`` makes a request's titles and descriptions
with no video work and prints one ``done`` line; run on the calling thread,
up to the time the caller gives (``TITLES_TIMEOUT_SECS`` by default). The
caller is handed the process as it starts (``started``), to kill it
(``StopTitles``) when a newer preview supersedes it.

How it is started (``Launch``): stdin, stdout and stderr are pipes (stdout
is read as UTF-8 text by line: vodsplit reconfigures its stdout to UTF-8
and writes ``ensure_ascii=False``). On Windows it gets no console window
and the below-normal priority class (the analysis must not take the CPU
from OBS), and it is put in a job object with
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: closing the job's handle (``Close``),
or Autona's process ending for any reason, ends the worker with every
process it started (yt-dlp, ffmpeg). The job object is made after the
process starts, so a process the worker starts in that instant is not in
it (accepted: the worker's first work is reading its arguments). Elsewhere
there is no job object; ``Kill`` ends the worker only.

Before a stop is asked (the ``cancel`` line), ``RaisePriority`` gives the
worker and every process in its job the normal priority class again: a
below-normal process the CPU is not given to (a busy machine) reads its
stdin's cancel only when it runs, which made a stop take over a minute.

The probe and the run both get Autona's environment with
``PYTHONIOENCODING=utf-8`` and ``PYINSTALLER_RESET_ENVIRONMENT=1`` (a frozen
Autona must not hand its PyInstaller state to a frozen worker;
``_ChildEnvironment``).

Qt-free; imports on every OS (the Windows API is loaded at first use).
Nothing is made at import.
"""
import json
import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Optional

from loguru import logger

ENV_COMMAND = "AUTONA_VODSPLIT_CMD"
# The distributed worker, under the folder Autona runs in (D5).
DEFAULT_EXECUTABLE = os.path.join("vodsplit-worker", "vodsplit-worker.exe")
# The sets.json format this Autona reads (vodsplit export.FORMAT).
SUPPORTED_FORMAT = 1
# Below the web client's 10 s request timeout: the nav asks /archive/status at startup
PROBE_TIMEOUT_SECS = 8
# What a worker that can make titles alone says it does (vodsplit V10).
TITLES_FEATURE = "titles"
# The titles' run waits this long at most (ArchiveJobService gives it what
# is left of its own deadline, which the probe shares).
TITLES_TIMEOUT_SECS = 8

_WINDOWS = sys.platform == "win32"
_CREATE_NO_WINDOW = 0x08000000
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_NORMAL_PRIORITY_CLASS = 0x00000020
_PROCESS_SET_INFORMATION = 0x0200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
# The ids a job's process list is read with (the worker, yt-dlp, ffmpeg).
_JOB_PROCESS_IDS_MAX = 256
_ERROR_MORE_DATA = 234


def SplitCommand(text):
    """`text` as an argument list. On Windows, like a command line there:
    double quotes group and are removed, backslashes are kept
    (``"C:\\Program Files\\Python\\python.exe" -m vodsplit``). Elsewhere,
    POSIX shell words."""
    if not _WINDOWS:
        return shlex.split(text, posix=True)
    words = []
    for word in shlex.split(text, posix=False):
        if len(word) >= 2 and word[0] == word[-1] and word[0] in "\"'":
            word = word[1:-1]
        words.append(word)
    return words


def Locate():
    """(command, reason): the worker's command as a list, or None and why
    not (Japanese). ``AUTONA_VODSPLIT_CMD`` first, then the distributed
    executable under the current directory."""
    text = os.environ.get(ENV_COMMAND, "").strip()
    if text:
        try:
            command = SplitCommand(text)
        except ValueError as error:
            return None, (f"{ENV_COMMAND} を読めません（{error}）。")
        if not command:
            return None, f"{ENV_COMMAND} が空です。"
        return command, None
    executable = os.path.abspath(DEFAULT_EXECUTABLE)
    if os.path.isfile(executable):
        return [executable], None
    return None, ("切り抜きワーカーが見つかりません。Autona のフォルダの "
                  "vodsplit-worker フォルダに vodsplit-worker.exe を置いてください。")


@dataclass(frozen=True)
class ProbeResult:
    """What ``Probe`` found. ``command`` is set only when it is usable;
    ``features`` what its version line names (strings only)."""
    available: bool
    version: Optional[str]
    format: Optional[int]
    reason: Optional[str]
    command: Optional[tuple]
    features: tuple = ()


class TitlesError(Exception):
    """``RunTitles`` got no titles: the Japanese message the page shows;
    `timeout` when the worker ran past the time."""

    def __init__(self, message, timeout=False):
        super().__init__(message)
        self.message = message
        self.timeout = timeout


def _ChildEnvironment():
    env = dict(os.environ)
    # A Python worker (the development command) writes its stdio in UTF-8
    # even before vodsplit reconfigures it (an early traceback).
    env["PYTHONIOENCODING"] = "utf-8"
    # A frozen Autona starting a frozen worker: the worker is another
    # PyInstaller program, which must not take this one's _PYI_* state
    # (its bootloader would read it as its own: the parent's _MEIPASS).
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _CreationFlags(background):
    if not _WINDOWS:
        return 0
    flags = _CREATE_NO_WINDOW
    if background:
        flags |= _BELOW_NORMAL_PRIORITY_CLASS
    return flags


def _ReadVersion(stdout):
    """The {"version", "format"} object of `stdout` (the first line that is
    one), or None."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        if isinstance(answer, dict) and "format" in answer:
            return answer
    return None


def Probe(timeout=PROBE_TIMEOUT_SECS):
    """Find the worker and ask its version (``<command> version``), on the
    calling thread (it waits up to `timeout` seconds). Never raises."""
    command, reason = Locate()
    if command is None:
        return ProbeResult(False, None, None, reason, None)
    fromEnv = bool(os.environ.get(ENV_COMMAND, "").strip())
    try:
        done = subprocess.run(
            [*command, "version"], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=_ChildEnvironment(),
            creationflags=_CreationFlags(background=False))
    except subprocess.TimeoutExpired:
        return ProbeResult(False, None, None, (
            f"切り抜きワーカーが応答しません（{timeout} 秒待ちました）。"), None)
    except OSError as error:
        where = f"{ENV_COMMAND} のコマンド" if fromEnv else "vodsplit-worker.exe"
        return ProbeResult(False, None, None, (
            f"切り抜きワーカーを起動できません（{where}: {error}）。"), None)
    answer = _ReadVersion(done.stdout)
    if done.returncode != 0 or answer is None:
        detail = (done.stderr or "").strip().splitlines()[-1:] or [""]
        logger.warning(
            f"Archive worker: the version probe failed (exit "
            f"{done.returncode}): {detail[0][:300]}")
        if done.returncode != 0:
            return ProbeResult(False, None, None, (
                f"切り抜きワーカーの確認に失敗しました（終了コード "
                f"{done.returncode}）。"), None)
        return ProbeResult(False, None, None, (
            "切り抜きワーカーの応答を読めません（version の出力が想定と違います）。"),
            None)
    version = answer.get("version")
    version = version if isinstance(version, str) else None
    fmt = answer.get("format")
    if isinstance(fmt, bool) or not isinstance(fmt, int):
        return ProbeResult(False, version, None, (
            "切り抜きワーカーの応答を読めません（format がありません）。"), None)
    if fmt != SUPPORTED_FORMAT:
        return ProbeResult(False, version, fmt, (
            f"この切り抜きワーカーの形式（format {fmt}）には対応していません。"
            f"format {SUPPORTED_FORMAT} の版に入れ替えてください。"), None)
    features = answer.get("features")
    features = tuple(item for item in features if isinstance(item, str)) \
        if isinstance(features, list) else ()
    return ProbeResult(True, version, fmt, None, tuple(command), features)


def RunTitles(command, requestPath, templatePath, timeout=None,
              started=None):
    """The worker's ``titles`` of the export request at `requestPath` with
    the template at `templatePath`, on the calling thread (up to `timeout`
    seconds, TITLES_TIMEOUT_SECS by default) -> its ``done`` line (a dict:
    "template", "sets"). `started(process)` is called once the process runs
    (the caller may kill it: it then ends as a failure). Raises
    TitlesError."""
    timeout = TITLES_TIMEOUT_SECS if timeout is None else timeout
    try:
        process = subprocess.Popen(
            [*command, "titles", requestPath, "--template", templatePath,
             "--format", "json"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", env=_ChildEnvironment(),
            creationflags=_CreationFlags(background=False))
    except OSError as error:
        raise TitlesError(f"切り抜きワーカーを起動できません: {error}") from None
    try:
        if started is not None:
            started(process)
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TitlesError((
            f"切り抜きワーカーがタイトルを作り終えません（{timeout:g} 秒待ちました）。"),
            timeout=True) from None
    except BaseException:
        process.kill()
        process.communicate()
        raise
    done = subprocess.CompletedProcess(process.args, process.returncode,
                                       stdout, stderr)
    answer = failure = None
    for line in (done.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "done":
            answer = event
        elif isinstance(event, dict) and event.get("type") == "error":
            failure = event
    if done.returncode == 0 and answer is not None:
        return answer
    detail = (done.stderr or "").strip().splitlines()[-1:] or [""]
    logger.warning(f"Archive worker: titles failed (exit {done.returncode}): "
                   f"{detail[0][:300]}")
    message = failure.get("message") if isinstance(failure, dict) else None
    if isinstance(message, str) and message:
        raise TitlesError(f"タイトルを作れませんでした: {message}")
    raise TitlesError((
        f"切り抜きワーカーがタイトルを作れませんでした（終了コード {done.returncode}）。"))


def StopTitles(process, wait=0):
    """Kill a ``titles`` run (the process RunTitles handed to `started`; its
    thread then raises TitlesError), waiting up to `wait` seconds for it to
    end. Never raises."""
    try:
        process.kill()
    except OSError:
        return  # it has ended
    if wait:
        try:
            process.wait(wait)
        except subprocess.TimeoutExpired:
            logger.warning(f"Archive worker: a killed titles' run did not end "
                           f"in {wait}s")


# The Windows job object -------------------------------------------------

_kernel32 = None


def _Kernel32():
    """kernel32 with the signatures this module calls (loaded once)."""
    global _kernel32
    if _kernel32 is None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID,
                                              wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE,
                                                      wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE,
                                                wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
            wintypes.LPDWORD)
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = (wintypes.HANDLE,
                                              wintypes.DWORD)
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        _kernel32 = kernel32
    return _kernel32


def _ExtendedLimits():
    """A JOBOBJECT_EXTENDED_LIMIT_INFORMATION with KILL_ON_JOB_CLOSE."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount",
            "OtherOperationCount", "ReadTransferCount",
            "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    return limits


def _MakeJobObject(proc):
    """A job object holding `proc` that ends it (and what it starts) when
    its handle is closed; None when it could not be made (logged: the
    worker still runs, ``Kill`` then ends the worker alone)."""
    import ctypes

    kernel32 = _Kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        logger.warning(f"Archive worker: no job object (CreateJobObject "
                       f"error {ctypes.get_last_error()})")
        return None
    limits = _ExtendedLimits()
    if not kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits), ctypes.sizeof(limits)):
        logger.warning(f"Archive worker: no job object (SetInformation "
                       f"error {ctypes.get_last_error()})")
        kernel32.CloseHandle(job)
        return None
    if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
        logger.warning(f"Archive worker: no job object (AssignProcess "
                       f"error {ctypes.get_last_error()})")
        kernel32.CloseHandle(job)
        return None
    return job


def _JobProcessIds(job):
    """The ids of the processes in job object `job` (at most
    _JOB_PROCESS_IDS_MAX); [] when they cannot be read (logged)."""
    import ctypes
    from ctypes import wintypes

    class ProcessIdList(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                    ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * _JOB_PROCESS_IDS_MAX)]

    ids = ProcessIdList()
    if not _Kernel32().QueryInformationJobObject(
            job, _JOB_OBJECT_BASIC_PROCESS_ID_LIST, ctypes.byref(ids),
            ctypes.sizeof(ids), None):
        error = ctypes.get_last_error()
        if error != _ERROR_MORE_DATA:  # more data: the first ones are read
            logger.warning(f"Archive worker: the job's processes cannot be "
                           f"read (QueryInformationJobObject error {error})")
            return []
    count = min(ids.NumberOfProcessIdsInList, _JOB_PROCESS_IDS_MAX)
    return [int(pid) for pid in ids.ProcessIdList[:count]]


class WorkerProcess:
    """A started worker: its Popen and, on Windows, its job object.

    ``Kill`` and ``Close`` may run at once on two threads (the shells' close
    and the reader ending the job): ``_lock`` keeps the handle from being
    closed twice or terminated once closed."""

    def __init__(self, proc, job):
        self.proc = proc
        self._job = job
        self._lock = threading.Lock()

    @property
    def pid(self):
        return self.proc.pid

    @property
    def hasJobObject(self):
        return self._job is not None

    def RaisePriority(self):
        """Give the worker and every process in its job (yt-dlp, ffmpeg)
        the normal priority class: called before a stop is asked, which a
        below-normal process reads only when the CPU is given to it.
        Windows only; never raises."""
        if not _WINDOWS:
            return
        import ctypes

        with self._lock:
            try:
                kernel32 = _Kernel32()
                if not kernel32.SetPriorityClass(int(self.proc._handle),
                                                 _NORMAL_PRIORITY_CLASS):
                    logger.warning(f"Archive worker: SetPriorityClass error "
                                   f"{ctypes.get_last_error()}")
                if self._job is None:
                    return
                for pid in _JobProcessIds(self._job):
                    if pid == self.proc.pid:
                        continue
                    handle = kernel32.OpenProcess(_PROCESS_SET_INFORMATION,
                                                  False, pid)
                    if not handle:  # it has ended since
                        continue
                    try:
                        kernel32.SetPriorityClass(handle,
                                                  _NORMAL_PRIORITY_CLASS)
                    finally:
                        kernel32.CloseHandle(handle)
            except Exception:
                logger.opt(exception=True).warning(
                    "Archive worker: raising the priority failed")

    def Kill(self):
        """End the worker now, with every process it started when there is
        a job object (else the worker alone). Never raises."""
        with self._lock:
            if self._job is not None:
                try:
                    _Kernel32().TerminateJobObject(self._job, 1)
                except OSError:
                    logger.opt(exception=True).warning(
                        "Archive worker: TerminateJobObject failed")
        try:
            self.proc.kill()
        except OSError:
            pass

    def Close(self):
        """Close the job object's handle: what is still in it ends
        (KILL_ON_JOB_CLOSE). Called once the worker has ended, which also
        ends a process it left behind. Idempotent; never raises."""
        with self._lock:
            job, self._job = self._job, None
            if job is not None:
                try:
                    _Kernel32().CloseHandle(job)
                except OSError:
                    logger.opt(exception=True).warning(
                        "Archive worker: closing the job object failed")


def Launch(command, args):
    """Start `command` + `args` with stdin, stdout and stderr as pipes
    (UTF-8 text, by line), in the background on Windows (no window, the
    below-normal priority class, a job object). Raises OSError when it
    cannot be started."""
    proc = subprocess.Popen(
        [*command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", bufsize=1, env=_ChildEnvironment(),
        creationflags=_CreationFlags(background=True))
    job = None
    if _WINDOWS:
        try:
            job = _MakeJobObject(proc)
        except Exception:
            logger.opt(exception=True).warning(
                "Archive worker: no job object")
    return WorkerProcess(proc, job)
