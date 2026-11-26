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
V10: ``titles``; 0.4.0: ``h264``, ``run --codec h264``); a worker from before
says none, and only what needs the feature is refused (ADR-006 A6: the
titles' preview) or done another way (the download's codec:
ArchiveJobService._Arguments).

Its self-check (``CheckCommand``, ADR-006 A7): ``<command> version --check``
of a worker whose features name ``CHECK_FEATURE`` runs every part the frozen
worker bundles (yt-dlp and its JS solver, the OCR, OpenCV's video reader,
ffmpeg, deno) and prints the version line with ``"check": {"ok", "items":
[{"name", "ok", "detail"}]}``. The installer asks it of a worker it has
unpacked; a worker from before has no such feature and only its
``version`` is asked.

Its titles (``RunTitles``, ADR-006 A6): ``<command> titles <request>
--template <file> --format json`` makes a request's titles and descriptions
with no video work and prints one ``done`` line; run on the calling thread,
up to the time the caller gives (``TITLES_TIMEOUT_SECS`` by default). The
caller is handed the process as it starts (``started``), to kill it
(``StopTitles``) when a newer preview supersedes it.

How a job's run is started (``RunSpec``, ADR-009 D9): it is the built-in
plugin ``taguyan.vodsplit``'s run on the heavy lane, started by
core/PluginRunner.py like any plugin's -- a job object made first
(KILL_ON_JOB_CLOSE, the below-normal priority class as the job's limit, so
yt-dlp and ffmpeg are below normal too: the analysis must not take the CPU
from OBS), the process started suspended with no console window, put in
the job and only then resumed; stdout's JSON Lines read as events, the
cancel (the job's priority back to normal, ``cancel`` written, stdin
closed, the job ended past the grace), the end decided by contract_1.json's
table. A job object that cannot be made fails the launch. This module only
says what to start (``RunSpec``); ArchiveJobService starts it.

The probe, the self-check, the titles and the run all get Autona's
environment as PluginRunner.BuildEnvironment makes it for the built-in
(``_ChildEnvironment``): ADR-009 A.7's names removed (``AUTONA_*``,
``QT_API``), ``PYTHONIOENCODING=utf-8`` and
``PYINSTALLER_RESET_ENVIRONMENT=1`` added (a frozen Autona must not hand
its PyInstaller state to a frozen worker). The probe, the self-check and
the titles are short runs on the calling thread with no job object, at the
normal priority class, without a console window.

Qt-free; imports on every OS (the Windows API is loaded at first use).
Nothing is made at import.
"""
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from . import PluginTypes as T
from .PluginRunner import BuildEnvironment

ENV_COMMAND = "AUTONA_VODSPLIT_CMD"
# The distributed worker, under the folder Autona runs in (D5; installed and
# updated by core/ArchiveWorkerInstaller.py, ADR-006 A7).
WORKER_DIR = "vodsplit-worker"
WORKER_EXECUTABLE = "vodsplit-worker.exe"
DEFAULT_EXECUTABLE = os.path.join(WORKER_DIR, WORKER_EXECUTABLE)
# The sets.json format this Autona reads (vodsplit export.FORMAT).
SUPPORTED_FORMAT = 1
# Below the web client's 10 s request timeout: the archive page's status
# (GET /archive/status) waits for it.
PROBE_TIMEOUT_SECS = 8
# What a worker that can make titles alone says it does (vodsplit V10).
TITLES_FEATURE = "titles"
# What a worker with the self-check (``version --check``) says it does.
CHECK_FEATURE = "check"
# What a worker that downloads H.264 + AAC first (``run --codec h264``, its
# default from vodsplit 0.4.0) says it does; one from before takes ``open``
# (VP9 / AV1 + Opus, its default) or ``any``.
H264_FEATURE = "h264"
# How often a probe that can be stopped (``stop``) looks at it.
_STOP_POLL_SECS = 0.2
# The titles' run waits this long at most (ArchiveJobService gives it what
# is left of its own deadline, which the probe shares).
TITLES_TIMEOUT_SECS = 8

_WINDOWS = sys.platform == "win32"
_CREATE_NO_WINDOW = 0x08000000


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


def WorkerDir():
    """The distributed worker's folder (an absolute path; it may not be
    there): ``vodsplit-worker`` under the current directory, frozen or
    not."""
    return os.path.abspath(WORKER_DIR)


def UsesEnvCommand():
    """Whether ``AUTONA_VODSPLIT_CMD`` names the worker (the folder's is
    then not used)."""
    return bool(os.environ.get(ENV_COMMAND, "").strip())


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
    executable = os.path.join(WorkerDir(), WORKER_EXECUTABLE)
    if os.path.isfile(executable):
        return [executable], None
    return None, ("切り抜きワーカー（vodsplit-worker.exe）が入っていません。"
                  "デスクトップアプリのアーカイブ切り抜きのページから導入できます。")


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


class ProbeStopped(Exception):
    """A probe's `stop` said to stop: the worker's process was killed."""


class TitlesError(Exception):
    """``RunTitles`` got no titles: the Japanese message the page shows;
    `timeout` when the worker ran past the time."""

    def __init__(self, message, timeout=False):
        super().__init__(message)
        self.message = message
        self.timeout = timeout


def _ChildEnvironment():
    """The worker's environment: the built-in plugin's, as the runner makes
    it (no protocol, no secret, no API URL). It adds PYTHONIOENCODING (a
    Python worker -- the development command -- writes its stdio in UTF-8
    even before vodsplit reconfigures it: an early traceback) and
    PYINSTALLER_RESET_ENVIRONMENT (a frozen worker is another PyInstaller
    program, which must not take this one's _PYI_* state: its bootloader
    would read it as its own, the parent's _MEIPASS)."""
    return BuildEnvironment(os.environ, pluginDir=WorkerDir(), protocol=None,
                            secrets={}, apiUrl=None)


def _CreationFlags():
    return _CREATE_NO_WINDOW if _WINDOWS else 0


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
    where = (f"{ENV_COMMAND} のコマンド" if UsesEnvCommand()
             else WORKER_EXECUTABLE)
    return ProbeCommand(command, timeout, where)


def _Communicate(argv, timeout, stop=None):
    """Run `argv` (stdin closed, no window) to its end, up to `timeout`
    seconds -> its CompletedProcess (text). `stop()` (when given) is asked
    every _STOP_POLL_SECS while it runs. Raises OSError (not started),
    subprocess.TimeoutExpired or ProbeStopped (the process killed)."""
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        env=_ChildEnvironment(), creationflags=_CreationFlags())
    deadline = time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                stdout, stderr = process.communicate(
                    timeout=left if stop is None else min(left, _STOP_POLL_SECS))
            except subprocess.TimeoutExpired:
                if stop is not None and stop():
                    raise ProbeStopped() from None
                continue
            return subprocess.CompletedProcess(argv, process.returncode,
                                               stdout, stderr)
    except BaseException:
        process.kill()
        process.communicate()
        raise


def ProbeCommand(command, timeout=PROBE_TIMEOUT_SECS,
                 where=WORKER_EXECUTABLE, stop=None):
    """``Probe`` of the worker `command` (a list), wherever it is: the
    installer's check of a worker it has unpacked and not yet moved in
    (ADR-006 A7) is the same as the use's. `where` names it in the reason
    a start's failure gives. Raises nothing but ProbeStopped (`stop()`
    became true: Autona is ending or the install was cancelled)."""
    try:
        done = _Communicate([*command, "version"], timeout, stop)
    except subprocess.TimeoutExpired:
        return ProbeResult(False, None, None, (
            f"切り抜きワーカーが応答しません（{timeout} 秒待ちました）。"), None)
    except OSError as error:
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


def CheckCommand(command, timeout, stop=None):
    """The self-check of the worker `command` (``version --check``; ask it
    only of a worker whose features name CHECK_FEATURE) -> None when every
    part it bundles works, else why not (Japanese, naming the parts that do
    not; their details are logged). Raises nothing but ProbeStopped."""
    try:
        done = _Communicate([*command, "version", "--check"], timeout, stop)
    except subprocess.TimeoutExpired:
        return f"切り抜きワーカーの自己診断が終わりません（{timeout} 秒待ちました）。"
    except OSError as error:
        return (f"切り抜きワーカーの自己診断を始められません"
                f"（{error.strerror or type(error).__name__}）。")
    answer = _ReadVersion(done.stdout)
    check = answer.get("check") if answer is not None else None
    items = check.get("items") if isinstance(check, dict) else None
    if not isinstance(items, list):
        detail = (done.stderr or "").strip().splitlines()[-1:] or [""]
        logger.warning(f"Archive worker: the self-check gave no result (exit "
                       f"{done.returncode}): {detail[0][:300]}")
        return "切り抜きワーカーの自己診断の結果を読めません。"
    failed = [item for item in items
              if not isinstance(item, dict) or item.get("ok") is not True]
    if done.returncode == 0 and check.get("ok") is True and not failed:
        return None
    names = []
    for item in failed:
        name = item.get("name") if isinstance(item, dict) else None
        name = name if isinstance(name, str) and name else "?"
        names.append(name)
        detail = item.get("detail") if isinstance(item, dict) else None
        logger.warning(f"Archive worker: the self-check's {name} failed: "
                       f"{str(detail)[:300]}")
    if not names:
        logger.warning(f"Archive worker: the self-check failed (exit "
                       f"{done.returncode}) with every part working")
    return ("切り抜きワーカーに同梱のものが動きません（"
            + ("、".join(names) if names else f"終了コード {done.returncode}")
            + "）。")


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
            creationflags=_CreationFlags())
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


# The run ---------------------------------------------------------------

def RunSpec(command, args, logPath):
    """The PluginRunner launch of `command` + `args` (a job's analysis or
    export, ADR-009 D9: the built-in vodsplit on the heavy lane, no time
    limit), its stderr and non-event lines appended to `logPath`. The cwd
    is Autona's (the development command's ``python -m vodsplit`` finds the
    package there, as before); the environment is _ChildEnvironment's."""
    return T.LaunchSpec(argv=(*command, *args), cwd=os.getcwd(),
                        env=_ChildEnvironment(), lane=T.Lane.HEAVY,
                        log_path=logPath, timeout_seconds=None)
