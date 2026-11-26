"""The plugin engine's process part (ADR-009 D6, appendix A / E.1; lane L1):
runs one plugin process and says how it ended. Implements
``PluginTypes.PluginRunner`` / ``RunHandle``; knows nothing of manifests,
lanes' occupancy or job folders (PluginJobs does) and judges no describe
answer (PluginRegistry does).

How a process is started (Windows): a job object is made first
(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_PRIORITY_CLASS --
BELOW_NORMAL for a run, NORMAL for ``describe`` -- and, on the light lane
only, JOB_OBJECT_LIMIT_JOB_MEMORY), then the process with
``CREATE_SUSPENDED | CREATE_NO_WINDOW`` and no shell, then it is put in the
job and only then resumed with ``NtResumeProcess`` (``Popen`` closes the
thread's handle, so ResumeThread cannot be used): nothing it starts can be
born outside the job. A job object that cannot be made or joined (a parent
job that forbids nesting, say) fails the launch, the process killed while
still suspended: a plugin is not run without it. Anything ``Popen`` raises
(OSError, a ValueError for a NUL or ``=`` in the environment, TypeError
...) is a launch failure too: ``Start`` never raises. Elsewhere (not a host
platform: tests only) the process gets a session of its own and a kill
ends its process group.

A run (``Start``) gets stdin, stdout and stderr as pipes:

- stdin stays open while it runs. A cancel (``RunHandle.Cancel``, the time
  limit, ``Shutdown``) only marks the run and wakes its thread; that thread
  then sets the job's priority class to NORMAL -- through the job's limit,
  so every process in it is raised at once with no pid listed (ADR-006's
  81 s stop) -- writes ``cancel\\n`` and closes stdin (stdin and the job's
  limit are touched by that thread only). A run still there
  CANCEL_GRACE_SECONDS later is ended with its job (worker.log says
  ``--- stopped by the host ---``). Past ``timeout_seconds`` the run is
  marked timed_out and cancelled the same way -- unless the user's cancel
  came first: from then on the time limit does not apply (the grace
  does), so a send that cannot be taken back may still end ``done``.
  ``Cancel`` after the run has ended does nothing (no cancel_requested).
- stdout is read on the run's thread (``_Pump``), without blocking (the
  pipe is peeked), so the same thread also keeps the time limit, the cancel
  grace and the process's end: once the process has ended its job is
  closed (what it left behind ends; ``PeakJobMemoryUsed`` is read just
  before) and the rest of stdout is read. Each line is one JSON object with
  a ``type`` (contract_1.json "events"): taken as a typed event with its
  ``raw``; fields that do not fit are dropped, texts truncated, an
  ``error.code`` off the pattern becomes ``error``, an ``id`` key is
  ignored. Unknown types, lines that are not JSON objects and lines over
  LINE_MAX_BYTES go to worker.log (empty lines are skipped); past
  MAX_WARNINGS warnings are dropped; the last terminal line wins.
- stderr is read on a second thread straight into worker.log. It is waited
  for STDERR_JOIN_SECONDS after the end; when a process outside the job
  still holds the pipe (none can be in it any more), the daemon reader is
  left blocked on it and its stream is not closed (closing it would wait
  for that read) -- it ends when the pipe does.

worker.log (``LaunchSpec.log_path``, appended to) holds at most
WORKER_LOG_MAX_BYTES; past it a note is written once and the rest is
dropped. Memory stays bounded: a line is buffered up to LINE_MAX_BYTES.

The light lane's memory limit (JOB_OBJECT_LIMIT_JOB_MEMORY) makes a commit
over it fail. A failed run is ``memory_limit`` when its PeakJobMemoryUsed
is MEMORY_LIMIT_REACHED of the limit or more: measured on Windows 11, a
growing process peaks a little over the limit, and a single refused
allocation is counted in the peak too (200 MB refused under 64 MB: a peak
of 217 MB). JobObjectLimitViolationInformation does not help: its
ViolationLimitFlags stay 0 for this hard limit (it reports notification
limits only).

``onEvent`` and ``onEnd`` are called on the run's thread, never inside
``Start`` (a launch failure too): a caller may hold its own lock while it
starts a run. ``onEnd`` comes once, after both readers; ``Wait`` returns
once ``onEnd`` has returned. On the run's own thread ``Wait`` returns the
outcome inside ``onEnd``; before it (inside ``onEvent``) ``Wait(None)``
raises RuntimeError (it would wait for itself) and ``Wait(t)`` returns
None at once. The one exception: when the run's thread cannot be started
at all (the interpreter refuses a thread), ``onEnd`` is called inside
``Start`` with a launch failure, after the process was killed.

``Shutdown`` cancels every run; a run whose launch finishes after it is
killed at once.

``Describe`` runs ``<entry> describe`` on the calling thread (stdin and
stderr DEVNULL, NORMAL priority, DESCRIBE_TIMEOUT_SECONDS) and reports
facts only (DescribeRun).

``EndState`` is contract_1.json "end" (launch failure, then timed_out, then
the table); ``BuildEnvironment`` its "environment" (case-insensitive
subtraction, then the added names). Both are pure.

The clock, the poll interval, the cancel grace, the describe timeout, the
parent environment and ``Popen`` are constructor arguments (the tests make
the waits short).

Qt-free; imports on every OS (the Windows API is loaded at first use).
Nothing is made, read or started at import or in the constructor.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from loguru import logger

from . import PluginTypes as T

_WINDOWS = sys.platform == "win32"

# How often the run's thread looks at the pipe, the clock and the process
# when stdout is quiet.
POLL_SECONDS = 0.05
# After the process has ended (and its job was closed), the rest of stdout
# is read for at most this long.
EXIT_DRAIN_SECONDS = 2.0
# The stderr reader is waited for this long once the process has ended.
STDERR_JOIN_SECONDS = 2.0
# After a kill (the cancel grace, Shutdown), how long Shutdown waits for a
# run's end to be written.
KILL_WAIT_SECONDS = 2.0
# DescribeRun.objects keeps the first ones.
DESCRIBE_OBJECTS_MAX = 32
# What of an over-long line goes to worker.log.
LONG_LINE_HEAD_BYTES = 1024
# A failed light run whose PeakJobMemoryUsed is this share of its limit or
# more reached it (the module's docstring: what was measured).
MEMORY_LIMIT_REACHED = 0.95
# The exit code a killed run gets (TerminateJobObject).
KILLED_EXIT_CODE = 1
HOST_STOPPED_NOTE = "--- stopped by the host ---"
WORKER_LOG_FULL_NOTE = (
    f"--- worker.log reached {T.WORKER_LOG_MAX_BYTES} bytes: the rest is "
    f"dropped ---\n")

# Windows
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020
_LIMIT_PRIORITY_CLASS = 0x00000020
_LIMIT_JOB_MEMORY = 0x00000200
_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_EXTENDED_LIMIT_INFORMATION = 9
_READ_CHUNK = 65536

_ERROR_CODE = re.compile(T.ERROR_CODE_PATTERN)


# ── the events (pure) ───────────────────────────────────────────────────

def _String(value, limit):
    return value[:limit] if isinstance(value, str) else None


def _Text(value, limit):
    """A text (a string, or locale -> string) cut to `limit` per value, or
    None when `value` is not one."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, Mapping):
        text = {key: item[:limit] for key, item in value.items()
                if isinstance(key, str) and isinstance(item, str)}
        return text or None
    return None


def _Number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if (low is not None and value < low) or (high is not None
                                             and value > high):
        return None
    return value


def _Count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _Artifacts(items):
    """``done.artifacts``: the first MAX_ARTIFACTS items, those without a
    path or with an unknown kind dropped (PluginJobs reads the dropped ones
    from ``raw`` for its warnings, and checks the files)."""
    if not isinstance(items, list):
        return ()
    taken = []
    for item in items[:T.MAX_ARTIFACTS]:
        if not isinstance(item, Mapping):
            continue
        path, kind = item.get("path"), item.get("kind")
        if not isinstance(path, str) or not path or not isinstance(kind, str):
            continue
        try:
            kind = T.ArtifactKind(kind)
        except ValueError:
            continue
        taken.append(T.Artifact(path=path, kind=kind, label=_Text(
            item.get("label"), T.LABEL_MAX_LENGTH)))
    return tuple(taken)


def ParseEvent(obj: Any) -> Optional[T.WorkerEvent]:
    """The event of one stdout object (json.loads of the line), or None
    when it is not one (not an object, no ``type`` string, an unknown type:
    the line goes to worker.log). Fields that do not fit are dropped and
    texts truncated (contract_1.json events.invalid_field / over_limit);
    the object is kept as ``raw``, unknown keys and ``id`` included."""
    if not isinstance(obj, Mapping) or not isinstance(obj.get("type"), str):
        return None
    try:
        kind = T.EventType(obj["type"])
    except ValueError:
        return None
    if kind is T.EventType.STAGE:
        return T.StageEvent(
            stage=_String(obj.get("stage"), 64) or "",
            label=_Text(obj.get("label"), T.LABEL_MAX_LENGTH),
            index=_Count(obj.get("index")), count=_Count(obj.get("count")),
            raw=obj)
    if kind is T.EventType.PROGRESS:
        return T.ProgressEvent(
            stage=_String(obj.get("stage"), 64),
            fraction=_Number(obj.get("fraction"), 0, 1),
            overall=_Number(obj.get("overall"), 0, 1),
            eta=_Number(obj.get("eta"), 0), raw=obj)
    if kind is T.EventType.LOG:
        return T.LogEvent(message=_Text(
            obj.get("message"), T.LOG_MESSAGE_MAX_LENGTH) or "", raw=obj)
    if kind is T.EventType.WARNING:
        return T.WarningEvent(message=_Text(
            obj.get("message"), T.LOG_MESSAGE_MAX_LENGTH) or "", raw=obj)
    if kind is T.EventType.DONE:
        return T.DoneEvent(
            summary=_Text(obj.get("summary"), T.SUMMARY_MAX_LENGTH),
            artifacts=_Artifacts(obj.get("artifacts")), raw=obj)
    if kind is T.EventType.ERROR:
        code = obj.get("code")
        if not isinstance(code, str) or not _ERROR_CODE.fullmatch(code):
            code = T.FALLBACK_ERROR_CODE
        return T.ErrorEvent(
            code=code,
            message=_Text(obj.get("message"),
                          T.ERROR_MESSAGE_MAX_LENGTH) or "",
            hint=_Text(obj.get("hint"), T.ERROR_HINT_MAX_LENGTH), raw=obj)
    return T.CancelledEvent(raw=obj)


def _Loads(text):
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


class _Lines:
    """Splits a byte stream into lines (LF, a CR before it dropped) with at
    most ``limit`` + 1 bytes of a line held: each line comes as (bytes,
    size), the bytes cut when ``size`` is over ``limit``."""

    def __init__(self, limit=T.LINE_MAX_BYTES):
        self._limit = limit
        self._buf = bytearray()
        self._dropped = 0

    def Feed(self, data):
        lines = []
        start = 0
        while True:
            end = data.find(b"\n", start)
            if end < 0:
                self._Keep(data[start:])
                return lines
            self._Keep(data[start:end])
            lines.append(self._Take())
            start = end + 1

    def Flush(self):
        """The last line when it had no LF, else None."""
        if not self._buf and not self._dropped:
            return None
        return self._Take()

    def _Keep(self, part):
        room = max(self._limit + 1 - len(self._buf), 0)  # + 1: a CR
        self._buf += part[:room]
        self._dropped += max(len(part) - room, 0)

    def _Take(self):
        line, dropped = bytes(self._buf), self._dropped
        self._buf.clear()
        self._dropped = 0
        if line.endswith(b"\r"):
            line = line[:-1]
        return line, len(line) + dropped


def _Decode(data):
    return data.decode("utf-8", "replace")


def _LongLine(where, line, size):
    head = _Decode(line[:LONG_LINE_HEAD_BYTES])
    return (f"{where}{head}... ({size} bytes: over the {T.LINE_MAX_BYTES}-"
            f"byte line limit)")


# ── the environment and the end (pure) ──────────────────────────────────

def _CheckEnvValue(what, value):
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"{what}: an environment value must be a string "
                         f"without NUL")


def BuildEnvironment(parent: Mapping[str, str], *, pluginDir: str,
                     protocol: Optional[int], secrets: Mapping[str, str],
                     apiUrl: Optional[str]) -> dict:
    """contract_1.json "environment": ``parent`` minus every name starting
    with ENV_REMOVE_PREFIXES and ENV_REMOVE_NAMES (matched case-insensitively,
    as Windows does), then PYTHONIOENCODING, PYINSTALLER_RESET_ENVIRONMENT,
    AUTONA_PLUGIN_DIR and -- for a run (``protocol`` not None) --
    AUTONA_PROTOCOL, ``secrets`` (already AUTONA_SECRET_<KEY> names) and
    AUTONA_API_URL when ``apiUrl``. A parent name equal to an added one in
    another case is dropped too (one name, one value). ValueError for a
    secret name that is not an AUTONA_SECRET_ one, two secret names equal
    but for case, and a name or value that is not a string or holds a NUL
    (or, in a name, ``=``). The parent's own entries are not checked: a bad
    one makes the launch fail (``Start`` never raises)."""
    _CheckEnvValue(T.ENV_PLUGIN_DIR, pluginDir)
    added = {T.ENV_PYTHONIOENCODING: "utf-8", T.ENV_PYINSTALLER_RESET: "1",
             T.ENV_PLUGIN_DIR: pluginDir}
    if protocol is not None:
        added[T.ENV_PROTOCOL] = str(protocol)
        seen = set()
        for name, value in secrets.items():
            if (not isinstance(name, str) or "=" in name or "\0" in name
                    or not name.upper().startswith(T.ENV_SECRET_PREFIX)):
                raise ValueError("a secret's environment name must start "
                                 f"with {T.ENV_SECRET_PREFIX} (no '=', NUL)")
            if name.upper() in seen:
                raise ValueError("two secrets' names differ only in case")
            seen.add(name.upper())
            _CheckEnvValue("a secret", value)
            added[name] = value
        if apiUrl:
            _CheckEnvValue(T.ENV_API_URL, apiUrl)
            added[T.ENV_API_URL] = apiUrl
    removedNames = {name.upper() for name in T.ENV_REMOVE_NAMES}
    addedNames = {name.upper() for name in added}
    env = {}
    for name, value in parent.items():
        upper = name.upper()
        if (upper in removedNames or upper in addedNames
                or upper.startswith(T.ENV_REMOVE_PREFIXES)):
            continue
        env[name] = value
    env.update(added)
    return env


def EndState(final: Optional[T.EventType], exitCode: Optional[int], *,
             cancelRequested: bool, timedOut: bool, launchFailed: bool,
             entryMissing: bool, memoryLimitHit: bool):
    """contract_1.json "end" -> (JobState, EndReason or None): a launch
    failure, then timed_out, then the table in ArchiveJobService._EndState's
    order. A FAILED row's reason is MEMORY_LIMIT when ``memoryLimitHit``,
    else ABNORMAL_EXIT."""
    if launchFailed:
        return T.JobState.FAILED, (T.EndReason.ENTRY_MISSING if entryMissing
                                   else T.EndReason.LAUNCH_FAILED)
    if timedOut:
        return T.JobState.FAILED, T.EndReason.TIMED_OUT
    if exitCode == 0 and final is T.EventType.DONE:
        return T.JobState.DONE, None
    if exitCode == 130 or final is T.EventType.CANCELLED:
        return T.JobState.CANCELLED, None
    if exitCode == 2 and final is T.EventType.ERROR:
        return T.JobState.ERROR, None
    if cancelRequested and final is None:
        return T.JobState.CANCELLED, None
    return T.JobState.FAILED, (T.EndReason.MEMORY_LIMIT if memoryLimitHit
                               else T.EndReason.ABNORMAL_EXIT)


def EntryArgv(pluginDir: str, entry: Sequence[str], *, dev: bool) -> list:
    """The manifest's ``entry`` as an argv to start: an installed plugin's
    first word is a path inside its folder (made absolute; Check has made
    sure it stays inside); a dev plugin's is a path in its folder when there
    is such a file or it names a path, else as given (a command on PATH,
    ``python``). ValueError for an empty entry."""
    if not entry or not isinstance(entry[0], str) or not entry[0]:
        raise ValueError("an empty entry")
    first, rest = entry[0], list(entry[1:])
    if os.path.isabs(first):
        return [first, *rest]
    inside = os.path.normpath(os.path.join(pluginDir, first))
    if (not dev or os.path.isfile(inside) or "/" in first
            or os.sep in first):
        return [inside, *rest]
    return [first, *rest]


# ── Windows ─────────────────────────────────────────────────────────────

_api = None
_apiLock = threading.Lock()


def _Api():
    """(kernel32, ntdll, ExtendedLimits) with the signatures used here,
    loaded once (``_apiLock``: two first runs at once set them once)."""
    with _apiLock:
        if _api is None:
            _LoadApi()
        return _api


def _LoadApi():
    global _api
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID,
                                          wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPDWORD)
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE,
                                                  wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE,
                                            wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.PeekNamedPipe.argtypes = (
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD)
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    ntdll.NtResumeProcess.restype = ctypes.c_long

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

    _api = (kernel32, ntdll, ExtendedLimits)


# Why a job object may be refused: Autona itself in a job that forbids
# nesting or breakaway (an older Windows, a launcher's job).
_JOB_HINT = ("the plugin is not run without its job object; Autona may be "
             "inside a job that does not allow a nested one")


def _WinError(what):
    import ctypes
    error = ctypes.get_last_error()
    return OSError(f"{what} failed (Windows error {error}): {_JOB_HINT}")


class _JobObject:
    """A job object with KILL_ON_JOB_CLOSE, a priority class limit and an
    optional job memory limit. Every method may run on any thread
    (``_lock``: the handle is not used once closed). Raises OSError when it
    cannot be made."""

    def __init__(self, priorityClass, memoryBytes=None):
        import ctypes

        kernel32, _, ExtendedLimits = _Api()
        self._lock = threading.Lock()
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise _WinError("CreateJobObject")
        self._limits = ExtendedLimits()
        flags = _LIMIT_KILL_ON_JOB_CLOSE | _LIMIT_PRIORITY_CLASS
        if memoryBytes:
            flags |= _LIMIT_JOB_MEMORY
            self._limits.JobMemoryLimit = memoryBytes
        self._limits.BasicLimitInformation.LimitFlags = flags
        self._limits.BasicLimitInformation.PriorityClass = priorityClass
        if not kernel32.SetInformationJobObject(
                self._handle, _EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(self._limits), ctypes.sizeof(self._limits)):
            error = _WinError("SetInformationJobObject")
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise error

    def Assign(self, proc):
        if not _Api()[0].AssignProcessToJobObject(self._handle,
                                                  int(proc._handle)):
            raise _WinError("AssignProcessToJobObject")

    def SetPriority(self, priorityClass):
        """Rewrite the job's priority class limit: every process in the job
        takes it. Never raises."""
        import ctypes

        with self._lock:
            if self._handle is None:
                return
            self._limits.BasicLimitInformation.PriorityClass = priorityClass
            if not _Api()[0].SetInformationJobObject(
                    self._handle, _EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(self._limits), ctypes.sizeof(self._limits)):
                logger.warning(f"Plugin run: the job's priority was not set "
                               f"(Windows error {ctypes.get_last_error()})")

    def PeakMemory(self):
        """PeakJobMemoryUsed, or None when it cannot be read."""
        import ctypes

        with self._lock:
            if self._handle is None:
                return None
            info = _Api()[2]()
            if not _Api()[0].QueryInformationJobObject(
                    self._handle, _EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info), ctypes.sizeof(info), None):
                return None
            return int(info.PeakJobMemoryUsed)

    def Terminate(self):
        with self._lock:
            if self._handle is not None:
                _Api()[0].TerminateJobObject(self._handle, KILLED_EXIT_CODE)

    def Close(self):
        """Close the handle: what is still in the job ends. Idempotent."""
        with self._lock:
            handle, self._handle = self._handle, None
            if handle is not None:
                _Api()[0].CloseHandle(handle)


def _Resume(proc):
    status = _Api()[1].NtResumeProcess(int(proc._handle))
    if status != 0:
        raise OSError(f"NtResumeProcess failed (status 0x{status & 0xffffffff:08x})")


def _ReadSome(stream):
    """(bytes, ended) of what the pipe `stream` holds now, without
    blocking: (b"", False) when nothing is there yet."""
    fd = stream.fileno()
    if _WINDOWS:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        available = wintypes.DWORD()
        if not _Api()[0].PeekNamedPipe(msvcrt.get_osfhandle(fd), None, 0,
                                       None, ctypes.byref(available), None):
            return b"", True  # broken pipe: every writer has closed it
        if not available.value:
            return b"", False
        data = os.read(fd, min(available.value, _READ_CHUNK))
        return data, not data
    import select

    ready, _, _ = select.select([fd], [], [], 0)
    if not ready:
        return b"", False
    data = os.read(fd, _READ_CHUNK)
    return data, not data


class _LaunchError(Exception):
    def __init__(self, message, entryMissing=False):
        super().__init__(message)
        self.entryMissing = entryMissing


# ── worker.log ──────────────────────────────────────────────────────────

class _WorkerLog:
    """A run's worker.log: appended to, WORKER_LOG_MAX_BYTES in all (what
    was there counts), then a note once and nothing more. Written from the
    two readers (``_lock``). Never raises."""

    def __init__(self, path):
        self._lock = threading.Lock()
        self._full = False
        try:
            self._file = open(path, "ab")
            self._written = self._file.tell()
        except OSError:
            logger.opt(exception=True).warning(
                "Plugin run: worker.log cannot be written")
            self._file = None
            self._written = 0

    def Write(self, text):
        data = (text.rstrip("\r\n") + "\n").encode("utf-8", "replace")
        with self._lock:
            if self._file is None or self._full:
                return
            room = (T.WORKER_LOG_MAX_BYTES - len(WORKER_LOG_FULL_NOTE)
                    - self._written)
            if len(data) > room:
                self._full = True
                data = WORKER_LOG_FULL_NOTE.encode("utf-8")
            try:
                self._file.write(data)
                self._file.flush()
                self._written += len(data)
            except (OSError, ValueError):
                pass

    def Close(self):
        with self._lock:
            file, self._file = self._file, None
        if file is not None:
            try:
                file.close()
            except OSError:
                pass


# ── a run ───────────────────────────────────────────────────────────────

class _Run:
    """One started process (``RunHandle``). The run's thread owns the
    process: stdin, the job's limit, the kill at the grace and the end.
    ``Cancel`` (any thread) only marks the run under ``_lock`` and wakes
    that thread."""

    def __init__(self, runner, spec, onEvent, onEnd):
        self._runner = runner
        self.spec = spec
        self.onEvent = onEvent
        self.onEnd = onEnd
        self.pid: Optional[int] = None
        self.proc = None
        self.job = None
        self.log = None
        self.launchError: Optional[_LaunchError] = None
        self.memoryLimit = (spec.memory_mb * 1024 * 1024
                            if spec.lane == T.Lane.LIGHT and spec.memory_mb
                            else None)
        self.deadline = None
        self.killAt = None
        self.killed = False
        self.exitedAt = None
        self.peak = None
        self.final = None
        self.warnings = 0
        self.cancelRequested = False
        self.cancelSent = False
        self.timedOut = False
        self.outcome: Optional[T.RunOutcome] = None
        self.stdoutThread = None
        self.stderrThread = None
        self.wake = threading.Event()
        self.finished = threading.Event()
        self._lock = threading.Lock()

    # RunHandle

    def Cancel(self) -> None:
        with self._lock:
            if (self.cancelRequested or self.exitedAt is not None
                    or self.launchError is not None
                    or self.finished.is_set()):
                return  # asked already, or ended: nothing to stop
            self.cancelRequested = True
        self.wake.set()

    def Wait(self, timeout: Optional[float] = None) -> Optional[T.RunOutcome]:
        if threading.current_thread() is self.stdoutThread:
            if self.outcome is not None:
                return self.outcome  # inside onEnd: the outcome is set
            if timeout is None:
                raise RuntimeError("Wait() on the run's own thread before "
                                   "its end would wait for itself")
            return None
        if not self.finished.wait(timeout):
            return None
        return self.outcome


# ── the runner ──────────────────────────────────────────────────────────

class PluginRunner:
    """``PluginTypes.PluginRunner``. One per process (bootstrap_core makes
    it; nothing at construction)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 poll: float = POLL_SECONDS,
                 cancelGrace: float = T.CANCEL_GRACE_SECONDS,
                 describeTimeout: float = T.DESCRIBE_TIMEOUT_SECONDS,
                 environ: Optional[Callable[[], Mapping[str, str]]] = None,
                 popen: Callable[..., Any] = subprocess.Popen):
        self._clock = clock
        self._poll = poll
        self._cancelGrace = cancelGrace
        self._describeTimeout = describeTimeout
        self._environ = environ or (lambda: os.environ)
        self._popen = popen
        self._lock = threading.Lock()
        self._runs = set()
        self._describes = set()
        self._stopping = False

    # pure

    def BuildEnvironment(self, parent, *, pluginDir, protocol, secrets,
                         apiUrl) -> dict:
        return BuildEnvironment(parent, pluginDir=pluginDir,
                                protocol=protocol, secrets=secrets,
                                apiUrl=apiUrl)

    def EndState(self, final, exitCode, *, cancelRequested, timedOut,
                 launchFailed, entryMissing, memoryLimitHit):
        return EndState(final, exitCode, cancelRequested=cancelRequested,
                        timedOut=timedOut, launchFailed=launchFailed,
                        entryMissing=entryMissing,
                        memoryLimitHit=memoryLimitHit)

    # starting a process

    def _Spawn(self, argv, cwd, env, priorityClass, memoryBytes, stdin,
               stderr):
        """(Popen, job object or None) of `argv` started in its job and
        running. Raises _LaunchError, whatever went wrong, with nothing
        left running or suspended (a KeyboardInterrupt still propagates,
        after the cleanup)."""
        job = None
        if _WINDOWS:
            try:
                job = _JobObject(priorityClass, memoryBytes)
            except Exception as error:
                raise _LaunchError(str(error)) from None
            flags = {"creationflags": _CREATE_SUSPENDED | _CREATE_NO_WINDOW}
        else:
            flags = {"start_new_session": True}
        try:
            proc = self._popen(list(argv), cwd=cwd, env=dict(env),
                               stdin=stdin, stdout=subprocess.PIPE,
                               stderr=stderr, bufsize=0, **flags)
        except BaseException as error:
            if job is not None:
                job.Close()
            if not isinstance(error, Exception):
                raise
            text = (error.strerror if isinstance(error, OSError)
                    and error.strerror else str(error))
            raise _LaunchError(
                f"{type(error).__name__}: {text}",
                entryMissing=isinstance(error, FileNotFoundError)
                and os.path.isdir(cwd)) from None
        if job is not None:
            try:
                job.Assign(proc)
                _Resume(proc)
            except BaseException as error:
                self._Discard(proc, job)
                if not isinstance(error, Exception):
                    raise
                raise _LaunchError(str(error)) from None
        return proc, job

    @staticmethod
    def _Discard(proc, job):
        """Kill a process that was not let run (suspended, or not in its
        job) and close what it holds. Never raises."""
        try:
            proc.kill()
            proc.wait(KILL_WAIT_SECONDS)
        except Exception:
            pass
        if job is not None:
            job.Close()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    @staticmethod
    def _Kill(proc, job):
        """End `proc` and everything in its job now. Never raises."""
        try:
            if job is not None:
                job.Terminate()
            elif not _WINDOWS:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            pass

    # Start

    def Start(self, spec: T.LaunchSpec,
              onEvent: Callable[[T.WorkerEvent], None],
              onEnd: Callable[[T.RunOutcome], None]) -> _Run:
        run = _Run(self, spec, onEvent, onEnd)
        with self._lock:
            stopping = self._stopping
            if not stopping:
                self._runs.add(run)
        run.log = _WorkerLog(spec.log_path)
        if stopping:
            run.launchError = _LaunchError("the runner is shutting down")
        else:
            try:
                run.proc, run.job = self._Spawn(
                    spec.argv, spec.cwd, spec.env,
                    BELOW_NORMAL_PRIORITY_CLASS, run.memoryLimit,
                    subprocess.PIPE, subprocess.PIPE)
            except _LaunchError as error:
                run.launchError = error
            except Exception as error:  # a malformed spec, say
                run.launchError = _LaunchError(
                    f"{type(error).__name__}: {error}")
        if run.launchError is not None:
            run.log.Write(f"--- could not start: {run.launchError} ---")
        else:
            with self._lock:
                stopping = self._stopping
            with run._lock:
                run.pid = run.proc.pid
                if spec.timeout_seconds is not None:
                    run.deadline = self._clock() + spec.timeout_seconds
                if stopping:  # Shutdown came while it was starting
                    run.cancelRequested = True
                    run.killAt = self._clock()
        self._StartThreads(run)
        return run

    def _StartThreads(self, run):
        """The run's two readers: stdout with the run's end (or, when it
        did not start, only the end) and stderr into worker.log. When the
        interpreter refuses a thread, the run ends here (killed, a launch
        failure, onEnd on this thread: the module's docstring)."""
        try:
            if run.proc is not None:
                run.stderrThread = threading.Thread(
                    target=self._ReadStderr, args=(run,), daemon=True,
                    name="AutonaPluginStderr")
                run.stderrThread.start()
            run.stdoutThread = threading.Thread(
                target=self._ReadStdout, args=(run,), daemon=True,
                name="AutonaPluginRun")
            run.stdoutThread.start()
        except RuntimeError as error:
            logger.error(f"Plugin run: no thread for the run ({error})")
            if run.proc is not None:
                self._Kill(run.proc, run.job)
                try:
                    run.proc.wait(KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
                if run.job is not None:
                    run.job.Close()
                run.proc = None
                run.stderrThread = None
            run.launchError = _LaunchError(f"no thread: {error}")
            run.stdoutThread = threading.current_thread()
            self._Finish(run)

    def _SendCancel(self, run):
        """The cancel's steps, once, on the run's thread: NORMAL priority
        for the whole job, ``cancel\\n`` and close stdin, the kill after the
        grace (or sooner when already due)."""
        with run._lock:
            if run.cancelSent:
                return
            run.cancelSent = True
            due = self._clock() + self._cancelGrace
            run.killAt = due if run.killAt is None else min(run.killAt, due)
        if run.job is not None:
            run.job.SetPriority(NORMAL_PRIORITY_CLASS)
        stdin = run.proc.stdin
        if stdin is not None:
            try:
                stdin.write(b"cancel\n")
                stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                stdin.close()
            except (OSError, ValueError):
                pass

    def _HostKill(self, run, force=False):
        """End the run's job (the grace passed, Shutdown, a failed read),
        noting it once in worker.log. ``force`` kills again even when it
        was noted (the process may still be there)."""
        with run._lock:
            first = not run.killed
            run.killed = True
        if not first and not force:
            return
        if first:
            logger.info("Plugin run: ending its job (the host's stop)")
            run.log.Write(HOST_STOPPED_NOTE)
        self._Kill(run.proc, run.job)

    # the readers

    def _ReadStderr(self, run):
        lines = _Lines()
        fd = run.proc.stderr.fileno()
        try:
            while True:
                data = os.read(fd, _READ_CHUNK)
                if not data:
                    break
                for line, size in lines.Feed(data):
                    self._StderrLine(run, line, size)
        except (OSError, ValueError):
            pass
        tail = lines.Flush()
        if tail is not None:
            self._StderrLine(run, *tail)

    @staticmethod
    def _StderrLine(run, line, size):
        if size > T.LINE_MAX_BYTES:
            run.log.Write(_LongLine("", line, size))
        else:
            run.log.Write(_Decode(line))

    def _ReadStdout(self, run):
        try:
            if run.proc is not None:
                self._Pump(run)
        except Exception:
            logger.opt(exception=True).error("Plugin run: reading failed")
            if run.proc is not None and run.proc.poll() is None:
                self._Kill(run.proc, run.job)
        finally:
            self._Finish(run)

    def _Pump(self, run):
        """Read stdout without blocking while keeping the cancel, the time
        limit, the grace and the process's end, until stdout has ended (or
        EXIT_DRAIN_SECONDS after the end) and the process has ended."""
        lines = _Lines()
        stdoutOpen = True
        while True:
            data = b""
            if stdoutOpen:
                data, ended = _ReadSome(run.proc.stdout)
                for line, size in lines.Feed(data):
                    self._StdoutLine(run, line, size)
                if ended:
                    stdoutOpen = False
                    tail = lines.Flush()
                    if tail is not None:
                        self._StdoutLine(run, *tail)
            self._Tick(run)
            if run.exitedAt is not None:
                if not stdoutOpen:
                    return
                if self._clock() - run.exitedAt >= EXIT_DRAIN_SECONDS:
                    logger.warning("Plugin run: stdout still open after the "
                                   "process ended")
                    tail = lines.Flush()
                    if tail is not None:
                        self._StdoutLine(run, *tail)
                    return
            if not data:
                run.wake.wait(self._poll)
                run.wake.clear()

    def _Tick(self, run):
        """On the run's thread: the time limit (not after the user's
        cancel), the cancel's steps, the grace's kill, and the process's
        end (its job's peak memory read, then the job closed: what it left
        behind ends)."""
        now = self._clock()
        if run.proc.poll() is None:
            with run._lock:
                if (run.deadline is not None and not run.timedOut
                        and not run.cancelRequested and not run.cancelSent
                        and now >= run.deadline):
                    run.timedOut = True
                    logger.info("Plugin run: past its time limit, "
                                "cancelling")
                wanted = run.cancelRequested or run.timedOut
            if wanted and not run.cancelSent:
                self._SendCancel(run)
            if run.killAt is not None and now >= run.killAt:
                self._HostKill(run)
            return
        if run.exitedAt is None:
            with run._lock:
                run.exitedAt = now
            if run.job is not None:
                run.peak = run.job.PeakMemory()
                run.job.Close()

    def _StdoutLine(self, run, line, size):
        if size > T.LINE_MAX_BYTES:
            run.log.Write(_LongLine("[stdout] ", line, size))
            return
        text = _Decode(line).strip()
        if not text:
            return
        event = ParseEvent(_Loads(text))
        if event is None:
            run.log.Write(f"[stdout] {text}")
            return
        if isinstance(event, T.WarningEvent):
            run.warnings += 1
            if run.warnings > T.MAX_WARNINGS:
                return
        if event.type in T.TERMINAL_EVENTS:
            run.final = event
        try:
            run.onEvent(event)
        except Exception:
            logger.opt(exception=True).warning(
                "Plugin run: the event's callback failed")

    def _Finish(self, run):
        """The run's end: close what it held, decide, call onEnd, let
        Wait return."""
        proc = run.proc
        exitCode = None
        if proc is not None:
            if proc.poll() is None:  # the reading failed
                self._HostKill(run, force=True)
                try:
                    proc.wait(KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            exitCode = proc.returncode
            if run.job is not None:
                if run.peak is None:
                    run.peak = run.job.PeakMemory()
                run.job.Close()
            if run.stderrThread is not None:
                run.stderrThread.join(STDERR_JOIN_SECONDS)
            streams = [proc.stdin, proc.stdout]
            if run.stderrThread is None or not run.stderrThread.is_alive():
                streams.append(proc.stderr)  # else its reader still holds it
            for stream in streams:
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
            run.log.Write(f"--- exit {exitCode} ---")
        run.log.Close()
        error = run.launchError
        memoryHit = (run.memoryLimit is not None and run.peak is not None
                     and run.peak >= run.memoryLimit * MEMORY_LIMIT_REACHED)
        state, reason = EndState(
            run.final.type if run.final is not None else None, exitCode,
            cancelRequested=run.cancelRequested, timedOut=run.timedOut,
            launchFailed=error is not None,
            entryMissing=error is not None and error.entryMissing,
            memoryLimitHit=memoryHit)
        run.outcome = T.RunOutcome(
            state=state, reason=reason, exit_code=exitCode, final=run.final,
            final_raw=run.final.raw if run.final is not None else None,
            cancel_requested=run.cancelRequested, timed_out=run.timedOut,
            finished_before_cancel=(state is T.JobState.DONE
                                    and run.cancelRequested),
            peak_memory_bytes=run.peak)
        with self._lock:
            self._runs.discard(run)
        try:
            run.onEnd(run.outcome)
        except Exception:
            logger.opt(exception=True).warning(
                "Plugin run: the end's callback failed")
        finally:
            run.finished.set()

    # describe

    def Describe(self, pluginDir: str, entry: Sequence[str], *,
                 dev: bool) -> T.DescribeRun:
        try:
            argv = [*EntryArgv(pluginDir, entry, dev=dev), "describe"]
        except ValueError:
            return T.DescribeRun(launched=False)
        if not dev and not os.path.isfile(argv[0]):
            return T.DescribeRun(launched=False, entry_missing=True)
        with self._lock:
            if self._stopping:
                return T.DescribeRun(launched=False)
        try:
            env = BuildEnvironment(self._environ(), pluginDir=pluginDir,
                                   protocol=None, secrets={}, apiUrl=None)
            proc, job = self._Spawn(argv, pluginDir, env,
                                    NORMAL_PRIORITY_CLASS, None,
                                    subprocess.DEVNULL, subprocess.DEVNULL)
        except _LaunchError as error:
            return T.DescribeRun(launched=False,
                                 entry_missing=error.entryMissing)
        except ValueError:
            return T.DescribeRun(launched=False)
        held = (proc, job)
        with self._lock:
            self._describes.add(held)
            stopping = self._stopping
        if stopping:
            self._Kill(proc, job)
        try:
            return self._DescribeRead(proc, job)
        finally:
            with self._lock:
                self._describes.discard(held)
            if job is not None:
                job.Close()
            try:
                proc.stdout.close()
            except OSError:
                pass

    def _DescribeRead(self, proc, job):
        """Read describe's stdout on this thread: the non-empty lines
        counted, the JSON objects kept (the first DESCRIBE_OBJECTS_MAX, and
        the first ``type: describe`` one past them), the time limit kept
        with the injected clock and poll."""
        lines = _Lines()
        counted = {"lines": 0, "answer": False}
        objects = []

        def take(line, size):
            if size > T.LINE_MAX_BYTES:
                counted["lines"] += 1
                return
            text = _Decode(line).strip()
            if not text:
                return
            counted["lines"] += 1
            obj = _Loads(text)
            if not isinstance(obj, dict):
                return
            answer = obj.get("type") == "describe" and not counted["answer"]
            counted["answer"] = counted["answer"] or answer
            if len(objects) < DESCRIBE_OBJECTS_MAX or answer:
                objects.append(obj)

        deadline = self._clock() + self._describeTimeout
        timedOut = False
        stdoutOpen = True
        exitedAt = None
        while True:
            data = b""
            if stdoutOpen:
                data, ended = _ReadSome(proc.stdout)
                for line, size in lines.Feed(data):
                    take(line, size)
                if ended:
                    stdoutOpen = False
            now = self._clock()
            if proc.poll() is None:
                if now >= deadline and not timedOut:
                    timedOut = True
                    self._Kill(proc, job)
            elif exitedAt is None:
                exitedAt = now
                if job is not None:
                    job.Close()
            if exitedAt is not None and (
                    not stdoutOpen or now - exitedAt >= EXIT_DRAIN_SECONDS):
                break
            if not data:
                time.sleep(self._poll)
        tail = lines.Flush()
        if tail is not None:
            take(*tail)
        return T.DescribeRun(
            launched=True, timed_out=timedOut,
            exit_code=None if timedOut else proc.returncode,
            stdout_lines=counted["lines"], objects=tuple(objects))

    # the shells' close

    def _WaitAll(self, runs, seconds):
        """Wait (the injected clock and poll) until every run in `runs` has
        finished or `seconds` have passed -> those still going."""
        end = self._clock() + seconds
        while True:
            late = [run for run in runs if not run.finished.is_set()]
            if not late or self._clock() >= end:
                return late
            late[0].finished.wait(self._poll)

    def Shutdown(self, grace: float) -> None:
        """Refuse new runs and describes, cancel every run, wait up to
        ``grace`` seconds, then end the rest with their jobs (and every
        describe at once). A run still starting is killed once started
        (Start)."""
        with self._lock:
            self._stopping = True
            runs = list(self._runs)
            describes = list(self._describes)
        for proc, job in describes:
            self._Kill(proc, job)
        for run in runs:
            run.Cancel()
        late = self._WaitAll(runs, grace)
        for run in late:
            logger.warning("Plugin run: did not stop for the shutdown; "
                           "ending its job")
            if run.proc is not None:  # here: its thread may be stuck
                self._HostKill(run, force=True)  # in a callback
            run.wake.set()
        if self._WaitAll(late, KILL_WAIT_SECONDS):
            logger.warning("Plugin run: an end was not written")
