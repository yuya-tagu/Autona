"""The archive clipper's jobs (ADR-006 A1): core/ArchiveWorker.py (where the
worker is, its version, how it is started), core/ArchiveJobService.py (one
job at a time, its progress, its end state, job.json; A4: the review and the
export of the confirmed sets, core/ArchiveReview.py) and the v1 routes the
page calls (/api/v1/archive/*: the reads THIS_PC, the writes SHELL -- the
shared view's fetch with the shell's secret, as the asset jobs').

The worker is tests/fixtures/fake_vodsplit_worker.py through
AUTONA_VODSPLIT_CMD: vodsplit's CLI and JSON Lines, no video work (its
modes are its docstring's). Who may call the routes beyond these rules is
tests/test_web_access.py's (the guard walks every route; the SHELL table);
the ledger and the classification are tests/test_webserver_routes.py's.
"""
import contextlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from src.core import ArchiveJobService as archive
from src.core import ArchiveReview as review
from src.core import ArchiveTemplates as templates
from src.core import ArchiveWorker as worker
from src.core import WebAccess
from src.core.ArchiveJobService import ArchiveError, ArchiveJobService
from src.core.SettingsManager import SettingsManager
from tests.fixtures import fake_vodsplit_worker as fake_worker
from tests.test_web_access import (  # noqa: F401
    LAN_PEER, _client, _shell_headers, server, settings, shellSecret)

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_vodsplit_worker.py"
COMMAND = f'"{sys.executable}" "{FAKE}"'
SOURCE = "https://www.youtube.com/watch?v=fakevideo01"
WAIT = 30
RESULTS = {"event": "Fake Cup", "sets": [
    {"round": "Winners Final", "p1": "Kappa", "s1": 3, "p2": "Lambda",
     "s2": 1},
    {"round": "Grand Final", "p1": "Kappa", "s1": 2, "p2": "Mu", "s2": 3},
]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """The fake worker as AUTONA_VODSPLIT_CMD, in mode ``ok``; cwd tmp.

    It runs at the normal priority class, not the worker's below-normal
    one (pinned by test_the_worker_runs_below_normal_without_a_window):
    the parallel runner keeps every core busy at the normal priority, and
    Windows then runs a below-normal process only when a starved thread
    is boosted (seconds apart) -- a fake that needs a fraction of a second
    took more than 30 s to start, and its kill outlived Shutdown's wait."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(worker, "_BELOW_NORMAL_PRIORITY_CLASS", 0)
    monkeypatch.setenv(worker.ENV_COMMAND, COMMAND)
    monkeypatch.setenv("FAKE_VODSPLIT_MODE", "ok")
    for name in ("FAKE_VODSPLIT_FORMAT", "FAKE_VODSPLIT_VERSION_EXIT",
                 "FAKE_VODSPLIT_DELAY", "FAKE_VODSPLIT_ARGV",
                 "FAKE_VODSPLIT_CHILD_PID", "FAKE_VODSPLIT_NO_TITLES",
                 "FAKE_VODSPLIT_INPUTS", "FAKE_VODSPLIT_H264",
                 "PYINSTALLER_RESET_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def work(settings, tmp_path):  # noqa: F811
    """archive.work_dir set to a tmp folder (settings.json in memory)."""
    path = tmp_path / "work"
    SettingsManager.settings["archive"] = {"work_dir": str(path)}
    return path


@pytest.fixture
def service(fake, work, monkeypatch):
    made = ArchiveJobService()
    monkeypatch.setattr(ArchiveJobService, "instance", made)
    yield made
    made.Shutdown(5.0)


def _mode(monkeypatch, mode):
    monkeypatch.setenv("FAKE_VODSPLIT_MODE", mode)


def _until(predicate, what):
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _ended(service, jobId):
    """The job once it is not running any more."""
    return _until(lambda: (lambda job: job if job["state"] != "running"
                           else None)(service.Job(jobId)[0]),
                  f"job {jobId} to end")


def _running_with_progress(service, jobId, index=1):
    return _until(lambda: (lambda job: job if job["progress"]
                           and (job["progress"]["index"] or 0) >= index
                           else None)(service.Job(jobId)[0]),
                  f"job {jobId}'s progress")


def _disk(work, jobId):
    return json.loads((work / jobId / "job.json").read_text(encoding="utf-8"))


def _alive(pid):
    """Whether process `pid` is running (Windows: OpenProcess)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259     # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _kill(pid):
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x0001, False, pid)   # PROCESS_TERMINATE
    if handle:
        kernel32.TerminateProcess(handle, 1)
        kernel32.CloseHandle(handle)


# ── where the worker is and its version ─────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows' quoting")
@pytest.mark.parametrize("text, words", [
    ("python -m vodsplit", ["python", "-m", "vodsplit"]),
    ('"C:\\Program Files\\Python\\python.exe" -m vodsplit',
     ["C:\\Program Files\\Python\\python.exe", "-m", "vodsplit"]),
    ('C:\\py\\python.exe "D:\\My Tools\\cli.py"',
     ["C:\\py\\python.exe", "D:\\My Tools\\cli.py"]),
])
def test_the_command_is_split_like_a_windows_command_line(text, words):
    assert worker.SplitCommand(text) == words


def test_without_the_command_the_worker_is_the_folders_exe(fake, tmp_path):
    fake.delenv(worker.ENV_COMMAND)
    command, reason = worker.Locate()
    assert command is None and "vodsplit-worker.exe" in reason

    exe = tmp_path / "vodsplit-worker" / "vodsplit-worker.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    assert worker.Locate() == ([str(exe)], None)


def test_the_probe_reads_the_workers_version(fake):
    result = worker.Probe()
    assert result.available is True
    assert (result.version, result.format, result.reason) == (
        "0.0.0-fake", 1, None)
    assert result.command == tuple(worker.SplitCommand(COMMAND))


@pytest.mark.parametrize("env, words", [
    ({"FAKE_VODSPLIT_FORMAT": "2"}, "format 2"),
    ({"FAKE_VODSPLIT_VERSION_EXIT": "3"}, "終了コード 3"),
])
def test_a_worker_of_another_format_or_failing_is_unavailable(
        fake, env, words):
    for name, value in env.items():
        fake.setenv(name, value)
    result = worker.Probe()
    assert result.available is False and result.command is None
    assert words in result.reason


def test_a_command_that_cannot_start_is_unavailable(fake, tmp_path):
    fake.setenv(worker.ENV_COMMAND, f'"{tmp_path / "missing.exe"}" x')
    result = worker.Probe()
    assert result.available is False
    assert "起動できません" in result.reason
    assert worker.ENV_COMMAND in result.reason


def test_a_probe_that_can_be_stopped_kills_the_worker(fake):
    # The installer's probe of a worker it unpacked: Autona's end or a
    # cancel kills it at once (ADR-006 A7).
    fake.setenv("FAKE_VODSPLIT_VERSION_SLEEP", "30")
    began = time.monotonic()
    asked = []

    def stop():
        asked.append(1)
        return time.monotonic() - began > 0.3

    with pytest.raises(worker.ProbeStopped):
        worker.ProbeCommand(worker.SplitCommand(COMMAND), 60, stop=stop)
    assert time.monotonic() - began < 5 and asked
    # Without `stop` it is the plain probe.
    fake.delenv("FAKE_VODSPLIT_VERSION_SLEEP")
    assert worker.ProbeCommand(worker.SplitCommand(COMMAND), 30,
                               stop=lambda: False).available


def test_the_self_check_names_the_parts_that_do_not_work(fake):
    command = worker.SplitCommand(COMMAND)
    fake.setenv("FAKE_VODSPLIT_CHECK", "ok")
    probe = worker.Probe()
    assert worker.CHECK_FEATURE in probe.features
    assert worker.CheckCommand(command, 30) is None
    fake.setenv("FAKE_VODSPLIT_CHECK", "ffmpeg,deno")
    reason = worker.CheckCommand(command, 30)
    assert "ffmpeg" in reason and "deno" in reason and "yt_dlp" not in reason
    # A worker with no self-check answers no check: unreadable, not passed.
    fake.delenv("FAKE_VODSPLIT_CHECK")
    assert "読めません" in worker.CheckCommand(command, 30)


def test_the_probe_and_the_run_reset_pyinstallers_environment(fake,
                                                              monkeypatch):
    # A frozen Autona starting a frozen worker must not hand it its _PYI_*
    # state: both the probe and the run ask PyInstaller to reset it.
    given = []

    def popen(argv, **kwargs):
        given.append(("probe" if argv[-1] == "version" else "run",
                      kwargs["env"]))
        raise OSError("not started in this test")

    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    assert worker.Probe().available is False
    with pytest.raises(OSError):
        worker.Launch(["worker"], ["run", SOURCE])
    assert [(what, env["PYINSTALLER_RESET_ENVIRONMENT"],
             env["PYTHONIOENCODING"]) for what, env in given] == [
        ("probe", "1", "utf-8"), ("run", "1", "utf-8")]
    # Autona's own environment is not changed.
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' flags")
def test_the_worker_runs_below_normal_without_a_window(monkeypatch):
    # The analysis must not take the CPU from OBS (ADR-006 D1): the run is
    # below normal, the probe (a few lines) is not. No console window
    # either. (The fake fixture runs the fake at the normal class.)
    given = []

    def popen(argv, **kwargs):
        given.append(("probe" if argv[-1] == "version" else "run",
                      kwargs["creationflags"]))
        raise OSError("not started in this test")

    monkeypatch.setenv(worker.ENV_COMMAND, COMMAND)
    monkeypatch.setattr(worker.subprocess, "Popen", popen)
    assert worker.Probe().available is False
    with pytest.raises(OSError):
        worker.Launch(["worker"], ["run", SOURCE])
    assert given == [("probe", 0x08000000), ("run", 0x08000000 | 0x00004000)]


class _Kernel:
    """kernel32's two job object calls, recorded (with a pause in each, so
    two threads overlap in them)."""

    def __init__(self):
        self.calls = []
        self.closed = set()

    def TerminateJobObject(self, job, _code):
        assert job not in self.closed, "terminated a closed handle"
        time.sleep(0.01)
        self.calls.append(("terminate", job))
        return True

    def CloseHandle(self, job):
        assert job not in self.closed, "closed a handle twice"
        time.sleep(0.01)
        self.closed.add(job)
        self.calls.append(("close", job))
        return True


class _Proc:
    pid = 1

    def __init__(self):
        self.kills = 0

    def kill(self):
        self.kills += 1


def test_the_job_object_is_closed_once_and_never_terminated_closed(
        monkeypatch):
    kernel = _Kernel()
    monkeypatch.setattr(worker, "_kernel32", kernel)
    proc = _Proc()
    process = worker.WorkerProcess(proc, 77)
    failures = []

    def call(method):
        try:
            method()
        except AssertionError as error:     # pragma: no cover - the bug
            failures.append(error)

    threads = [threading.Thread(target=call, args=(method,))
               for _ in range(8)
               for method in (process.Close, process.Kill)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # _Kernel raises (into `failures`) on a second close or a terminate of
    # the closed handle.
    assert failures == []
    assert kernel.calls.count(("close", 77)) == 1
    assert kernel.calls[-1] == ("close", 77)
    assert process.hasJobObject is False
    assert proc.kills == 8

    # Once closed: a kill ends the worker alone, a close does nothing.
    calls = list(kernel.calls)
    process.Kill()
    process.Close()
    assert kernel.calls == calls
    assert proc.kills == 9


class _PriorityKernel:
    """kernel32's priority calls, recorded; the processes in `ended` have
    ended (OpenProcess fails)."""

    def __init__(self, ended=()):
        self.calls = []
        self.ended = set(ended)

    def SetPriorityClass(self, handle, priority):
        self.calls.append(("priority", handle, priority))
        return True

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("open", access, inherit, pid))
        return 0 if pid in self.ended else 1000 + pid

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return True


def test_raising_the_priority_takes_the_worker_and_its_jobs_processes(
        monkeypatch):
    # A stop is read from the worker's stdin; a below-normal worker on a
    # busy machine reads it only when the CPU is given to it (a stop took
    # 81 s under load). So the worker and every process in its job (yt-dlp,
    # ffmpeg) get the normal class first.
    kernel = _PriorityKernel(ended={43})
    monkeypatch.setattr(worker, "_WINDOWS", True)
    monkeypatch.setattr(worker, "_kernel32", kernel)
    monkeypatch.setattr(worker, "_JobProcessIds",
                        lambda job: [1, 42, 43, 44] if job == 77 else [])
    proc = _Proc()
    proc._handle = 500
    worker.WorkerProcess(proc, 77).RaisePriority()
    assert kernel.calls == [
        ("priority", 500, 0x20),                # the worker (pid 1) itself
        ("open", 0x0200, False, 42), ("priority", 1042, 0x20),
        ("close", 1042),
        ("open", 0x0200, False, 43),            # ended since: skipped
        ("open", 0x0200, False, 44), ("priority", 1044, 0x20),
        ("close", 1044)]

    # No job object: the worker alone. Not Windows: nothing.
    kernel.calls.clear()
    worker.WorkerProcess(proc, None).RaisePriority()
    assert kernel.calls == [("priority", 500, 0x20)]
    kernel.calls.clear()
    monkeypatch.setattr(worker, "_WINDOWS", False)
    worker.WorkerProcess(proc, 77).RaisePriority()
    assert kernel.calls == []

    # It never raises (a stop must still be asked).
    class Failing(_PriorityKernel):
        def SetPriorityClass(self, handle, priority):
            raise OSError("access denied")

    monkeypatch.setattr(worker, "_WINDOWS", True)
    monkeypatch.setattr(worker, "_kernel32", Failing())
    worker.WorkerProcess(proc, 77).RaisePriority()


def _priority(pid):
    """Process `pid`'s priority class (Windows: GetPriorityClass)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetPriorityClass.argtypes = (wintypes.HANDLE,)
    kernel32.GetPriorityClass.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    assert handle, f"process {pid} cannot be opened"
    try:
        return kernel32.GetPriorityClass(handle)
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="the priority classes")
def test_the_priority_of_a_real_worker_and_its_child_is_raised(tmp_path):
    # A started worker is below normal, and so is what it starts (yt-dlp
    # and ffmpeg inherit the class); RaisePriority gives both the normal
    # class. The worker starts its child only once it was raised (a
    # below-normal process may not run for seconds under the parallel
    # runner), and starts it below normal explicitly.
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys\n"
        "sys.stdin.readline()\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)'], creationflags=0x08004000)\n"
        "print(child.pid, flush=True)\n"
        "sys.stdin.readline()\n", encoding="utf-8")
    process = worker.Launch([sys.executable, str(script)], [])
    lines = []
    reader = threading.Thread(
        target=lambda: lines.append(process.proc.stdout.readline()),
        daemon=True)
    try:
        assert process.hasJobObject
        assert _priority(process.pid) == 0x4000
        process.RaisePriority()
        assert _priority(process.pid) == 0x20

        reader.start()
        process.proc.stdin.write("go\n")
        process.proc.stdin.flush()
        reader.join(WAIT)
        assert lines and lines[0].strip(), "the child's pid was not printed"
        child = int(lines[0])
        assert _priority(child) == 0x4000
        process.RaisePriority()
        assert _priority(child) == 0x20
    finally:
        process.Kill()
        process.proc.wait(WAIT)
        process.Close()
        for stream in (process.proc.stdin, process.proc.stdout,
                       process.proc.stderr):
            with contextlib.suppress(OSError, ValueError):
                stream.close()


def test_the_status_says_why_there_is_no_worker(fake, work):
    fake.delenv(worker.ENV_COMMAND)
    status = ArchiveJobService().Status()
    assert status["available"] is False
    assert "vodsplit-worker" in status["reason"]
    assert status["work_dir"] == os.path.normpath(str(work))
    assert status["running_job"] is None


def test_an_unavailable_probe_is_asked_again_on_refresh(fake, work):
    asked = []

    def probe():
        asked.append(1)
        return worker.ProbeResult(False, None, None, "none", None)

    service = ArchiveJobService(probe=probe)
    service.Status()
    service.Status()
    assert len(asked) == 1        # cached for UNAVAILABLE_RECHECK_SECS
    service.Status(refresh=True)
    assert len(asked) == 2
    with pytest.raises(ArchiveError) as refused:
        service.Create(SOURCE)
    assert refused.value.code == archive.UNAVAILABLE
    assert len(asked) == 3        # a start asks once more first


def test_a_start_probes_the_worker_once_at_most(fake, work):
    # The web client gives a request 10 s and a probe may take 8: a start
    # (or a resume) never waits for two.
    asked = []

    def probe():
        asked.append(1)
        return worker.ProbeResult(False, None, None, "none", None)

    service = ArchiveJobService(probe=probe)
    with pytest.raises(ArchiveError) as refused:
        service.Create(SOURCE)          # nothing cached: one probe
    assert refused.value.code == archive.UNAVAILABLE
    assert len(asked) == 1

    jobId = "20261101-120000-ab12"
    (work / jobId).mkdir(parents=True)
    (work / jobId / "job.json").write_text(json.dumps({
        "id": jobId, "source": SOURCE, "state": "cancelled"}),
        encoding="utf-8")
    with pytest.raises(ArchiveError) as refused:
        service.Resume(jobId)           # an unusable one cached: one probe
    assert refused.value.code == archive.UNAVAILABLE
    assert len(asked) == 2


def test_a_start_takes_the_probe_another_request_was_waiting_for(fake, work):
    # A start that waited for the status' probe takes that answer rather
    # than probing again after it.
    entered, release = threading.Event(), threading.Event()
    asked = []

    def probe():
        asked.append(1)
        entered.set()
        assert release.wait(WAIT)
        return worker.ProbeResult(False, None, None, "none", None)

    service = ArchiveJobService(probe=probe)
    status = threading.Thread(target=service.Status)
    status.start()
    assert entered.wait(WAIT)
    refusals = []
    waiting = threading.Event()

    class Watched:
        """The probe lock, telling when the start reaches it (it read
        ``_probeCount`` before: the probe it waits for is its own)."""

        def __init__(self, lock):
            self.lock = lock

        def __enter__(self):
            if threading.current_thread() is not status:
                waiting.set()
            return self.lock.__enter__()

        def __exit__(self, *exc):
            return self.lock.__exit__(*exc)

    service._probeLock = Watched(service._probeLock)

    def create():
        try:
            service.Create(SOURCE)
        except ArchiveError as error:
            refusals.append(error.code)

    start = threading.Thread(target=create)
    start.start()
    assert waiting.wait(WAIT)           # the start waits for the probe
    release.set()
    status.join(WAIT)
    start.join(WAIT)
    assert refusals == [archive.UNAVAILABLE]
    assert len(asked) == 1


def test_nothing_is_read_or_probed_when_made(fake, work):
    asked = []
    ArchiveJobService(probe=lambda: asked.append(1))
    assert asked == [] and not work.exists()


# ── a job's run ──────────────────────────────────────────────────────────

def test_a_job_runs_to_done(service, work, tmp_path, fake):
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))

    job = service.Create(SOURCE)
    assert job["state"] == "running" and job["source"] == SOURCE
    assert archive.JOB_ID_PATTERN.fullmatch(job["id"])
    assert service.Status()["running_job"] == job["id"]
    assert _disk(work, job["id"])["state"] == "running"

    done = _ended(service, job["id"])
    jobDir = work / job["id"]
    assert done["state"] == "done"
    assert (done["sets"], done["needs_review"], done["exit_code"]) == (2, 1, 0)
    assert done["error"] is None and done["finished_at"]
    assert done["logs"] == ["fake log line"]
    assert done["warnings"] == ["fake warning"]
    assert done["has_results"] is False
    # --thumbs: vodsplit's export stage (the background frames) ends the
    # run, its last progress line the job's.
    assert {key: done["progress"][key] for key in (
        "stage", "label", "index", "count", "fraction")} == {
        "stage": "export", "label": "書き出し", "index": 5, "count": 5,
        "fraction": 1.0}
    assert done["progress"]["overall"] > 0.8
    assert [row["thumb"] for row in service.Job(job["id"])[1]["sets"]] == [
        "01_fake_bg.jpg", "02_fake_bg.jpg"]
    assert set(done) == {
        "id", "created_at", "source", "state", "sets", "needs_review",
        "finished_at", "progress", "logs", "warnings", "error", "exit_code",
        "has_results", "export", "thumbs", "analysis_cached"}
    assert done["export"] is None
    # The fake writes no analysis cache (out/.cache): a run again would
    # analyse from the start.
    assert done["analysis_cached"] is False
    # No thumbnail drawn (A6): none is listed.
    assert done["thumbs"] == {"state": None, "done": 0, "total": 0,
                              "failed": [], "files": [], "revision": 0}
    assert set(done["progress"]) == {
        "stage", "label", "index", "count", "fraction", "overall", "eta"}

    _job, sets = service.Job(job["id"])
    assert [row["no"] for row in sets["sets"]] == [1, 2]
    assert _disk(work, job["id"])["state"] == "done"
    assert service.Status()["running_job"] is None
    # The fake is a worker from before "h264": the download's codec is any.
    assert json.loads(argv.read_text(encoding="utf-8")) == [
        SOURCE, "--out", str(jobDir / "out"),
        "--download-dir", str(work / "downloads"), "--codec", "any",
        "--no-cut", "--thumbs", "--progress", "json", "--cancel-on-stdin"]
    log = (jobDir / "worker.log").read_text(encoding="utf-8")
    assert "[stdout] fake worker: starting (not a JSON line)" in log
    assert "fake worker: stderr line" in log
    assert service.Jobs() == [{
        "id": job["id"], "created_at": job["created_at"], "source": SOURCE,
        "state": "done", "sets": 2, "needs_review": 1}]


def test_the_results_and_the_event_are_handed_on(service, work, tmp_path,
                                                 fake):
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))
    video = tmp_path / "vod.mp4"
    video.write_bytes(b"")

    job = service.Create(str(video), RESULTS, "  雷影杯  ")
    done = _ended(service, job["id"])

    jobDir = work / job["id"]
    assert done["state"] == "done" and done["has_results"] is True
    assert json.loads((jobDir / "results.json").read_text(
        encoding="utf-8")) == RESULTS
    args = json.loads(argv.read_text(encoding="utf-8"))
    assert args[0] == str(video)
    assert args[args.index("--results") + 1] == str(jobDir / "results.json")
    assert "--event=雷影杯" in args and "--event" not in args
    # The codec as a URL's (a file's run downloads nothing).
    assert args[args.index("--codec") + 1] == "any"
    _job, sets = service.Job(job["id"])
    assert sets["sets"][0]["set"]["p1"] == "Kappa"
    assert sets["event"] == "雷影杯"


def test_an_event_name_starting_with_a_dash_is_the_value(service, tmp_path,
                                                         fake):
    # One word "--event=<name>": argparse reads "--event -x" as an option
    # with no value (vodsplit refused the run).
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))
    job = service.Create(SOURCE, None, "-x --no-cut")
    assert _ended(service, job["id"])["state"] == "done"
    args = json.loads(argv.read_text(encoding="utf-8"))
    assert "--event=-x --no-cut" in args
    assert service.Job(job["id"])[1]["event"] == "-x --no-cut"


@pytest.mark.parametrize("h264, codec", [(False, "any"), (True, "h264")])
def test_the_download_codec_follows_the_workers_features(
        service, work, tmp_path, fake, h264, codec):
    # H.264 + AAC from a worker whose features name "h264" (vodsplit 0.4.0:
    # the analysis decodes it about twice as fast, and a livestream's
    # archive may offer VP9 at 360p only); "any" from one before -- the
    # highest resolution, not its own default "open" -- which refuses
    # "h264" (the fake as argparse: the run would fail). Either is saved
    # as <id>.mp4, found by its name as the old <id>.open.mp4 was.
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))
    if h264:
        fake.setenv("FAKE_VODSPLIT_H264", "1")
    assert (worker.H264_FEATURE in service.Status()["features"]) is h264

    job = service.Create(SOURCE)
    assert _ended(service, job["id"])["state"] == "done"
    args = json.loads(argv.read_text(encoding="utf-8"))
    assert args[args.index("--codec") + 1] == codec
    download = work / "downloads" / "fakevideo01.mp4"
    assert service.Job(job["id"])[1]["source"] == str(download)
    assert service.VideoFile(job["id"]) == (str(download), "video/mp4")
    assert not (work / "downloads" / "fakevideo01.open.mp4").exists()

    # A resume asks the same of the worker it runs on.
    _mode(fake, "error")
    failed = service.Create(SOURCE)
    assert _ended(service, failed["id"])["state"] == "error"
    argv.unlink()
    _mode(fake, "ok")
    service.Resume(failed["id"])
    assert _ended(service, failed["id"])["state"] == "done"
    args = json.loads(argv.read_text(encoding="utf-8"))
    assert args[args.index("--codec") + 1] == codec


def test_an_error_the_operator_can_fix(service, work, fake):
    _mode(fake, "error")
    job = service.Create(SOURCE)
    done = _ended(service, job["id"])
    assert done["state"] == "error" and done["exit_code"] == 2
    assert done["error"] == {"code": "download",
                             "message": "動画をダウンロードできません。",
                             "hint": "URL を確かめてください。"}
    assert _disk(work, job["id"])["error"]["code"] == "download"


def test_a_crash_is_failed_with_the_logs_tail(service, fake):
    _mode(fake, "crash")
    job = service.Create(SOURCE)
    done = _ended(service, job["id"])
    assert done["state"] == "failed" and done["exit_code"] == 1
    assert done["error"]["code"] == "crashed"
    assert "終了コード 1" in done["error"]["message"]
    assert "RuntimeError: fake crash" in done["error"]["message"]


def test_a_cancel_stops_the_worker_and_the_job_resumes(service, work, fake):
    _mode(fake, "slow")
    job = service.Create(SOURCE)
    running = _until(
        lambda: (lambda job: job if (job["progress"] or {}).get("eta")
                 else None)(service.Job(job["id"])[0]), "a progress line")
    assert running["progress"]["eta"] == 12.5
    assert running["progress"]["label"] == "ダウンロード"

    asked = service.Cancel(job["id"])
    assert asked["state"] == "running"     # until the worker stops
    done = _ended(service, job["id"])
    assert done["state"] == "cancelled" and done["exit_code"] == 130
    assert _disk(work, job["id"])["state"] == "cancelled"
    with pytest.raises(ArchiveError) as refused:
        service.Cancel(job["id"])
    assert refused.value.code == archive.NOT_RUNNING

    _mode(fake, "ok")
    resumed = service.Resume(job["id"])
    assert resumed["id"] == job["id"] and resumed["state"] == "running"
    assert resumed["exit_code"] is None and resumed["finished_at"] is None
    assert _ended(service, job["id"])["state"] == "done"
    with pytest.raises(ArchiveError) as refused:
        service.Resume(job["id"])
    assert refused.value.code == archive.ALREADY_DONE


def test_one_job_at_a_time(service, work, fake):
    _mode(fake, "slow")
    job = service.Create(SOURCE)
    for refusal in (lambda: service.Create(SOURCE),
                    lambda: service.Resume(job["id"]),
                    lambda: service.SetWorkDir(str(work / "other"))):
        with pytest.raises(ArchiveError) as refused:
            refusal()
        assert refused.value.code == archive.BUSY
    service.Cancel(job["id"])
    assert _ended(service, job["id"])["state"] == "cancelled"


def test_the_shutdown_cancels_a_running_job(service, work, fake):
    _mode(fake, "slow")
    job = service.Create(SOURCE)
    _running_with_progress(service, job["id"])
    started = time.monotonic()
    service.Shutdown(5.0)
    assert time.monotonic() - started < 5.0
    assert _disk(work, job["id"])["state"] == "cancelled"
    with pytest.raises(ArchiveError) as refused:
        service.Create(SOURCE)
    assert refused.value.code == archive.STOPPING


class _RecordedStream:
    """A worker's stdin, recording what is done to it in `events`."""

    def __init__(self, stream, events):
        self.stream, self.events = stream, events

    def write(self, text):
        self.events.append(("write", text))
        return self.stream.write(text)

    def flush(self):
        return self.stream.flush()

    def close(self):
        self.events.append(("close",))
        return self.stream.close()


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_the_job_object_is_closed_before_the_job_is_let_go(service, fake):
    # The worker's swap (ADR-006 A7) is granted once no job is held: by
    # then nothing the job started may run -- even when its end failed.
    held = []
    close = worker.WorkerProcess.Close

    def recorded(process):
        held.append(service._run is not None)
        close(process)

    def broken(_run, _exitCode):
        raise RuntimeError("the end state failed in this test")

    fake.setattr(worker.WorkerProcess, "Close", recorded)
    fake.setattr(service, "_EndState", broken)
    service.Create(SOURCE)
    _until(lambda: len(held) >= 2 and service._run is None,
           "the job to be let go")
    assert held == [True, True]


@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
@pytest.mark.parametrize("kind", ["analysis", "export"])
def test_a_stop_raises_the_workers_priority_before_the_cancel_line(
        service, work, fake, tmp_path, stop, kind):
    events = []
    raise_priority = worker.WorkerProcess.RaisePriority

    def recorded(process):
        events.append(("raise",))
        raise_priority(process)

    fake.setattr(worker.WorkerProcess, "RaisePriority", recorded)
    _mode(fake, "slow")
    if kind == "export":
        _done(work, tmp_path)
        service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
        jobId = DONE_ID
        service.StartExport(jobId)
        _until(lambda: (service.Job(jobId)[0].get("export") or {}).get(
            "progress"), "the export's progress")
    else:
        jobId = service.Create(SOURCE)["id"]
        _running_with_progress(service, jobId)
    run = service._run
    run.worker.proc.stdin = _RecordedStream(run.worker.proc.stdin, events)
    if stop == "cancel":
        service.Cancel(jobId)
        _until(run.finished.is_set, "the worker to stop")
    else:
        service.Shutdown(5.0)
    # (The end closes the stream once more.)
    assert events[:3] == [("raise",), ("write", "cancel\n"), ("close",)]


@pytest.mark.skipif(sys.platform != "win32", reason="the job object")
def test_the_shutdown_kills_a_worker_that_does_not_stop(
        service, work, tmp_path, fake):
    _mode(fake, "hang")
    childPid = tmp_path / "child.pid"
    fake.setenv("FAKE_VODSPLIT_CHILD_PID", str(childPid))
    job = service.Create(SOURCE)
    _running_with_progress(service, job["id"], index=2)
    run = service._run
    assert run.worker.hasJobObject
    grandchild = int(childPid.read_text(encoding="utf-8"))
    try:
        assert _alive(run.worker.pid) and _alive(grandchild)

        started = time.monotonic()
        service.Shutdown(0.5)
        assert time.monotonic() - started < 0.5 + 3

        # Shutdown waits a bounded time for the end once it killed; under
        # load the kill and the reader's end can take longer than that.
        _until(lambda: run.worker.proc.poll() is not None,
               "the worker to end")
        _until(lambda: not _alive(grandchild), "the grandchild to end")
        assert run.finished.wait(WAIT)
        record = _disk(work, job["id"])
        assert record["state"] == "cancelled"
    finally:
        _kill(grandchild)


@pytest.mark.skipif(sys.platform != "win32", reason="the job object")
def test_a_grandchild_holding_the_stderr_does_not_hold_up_the_end(
        service, tmp_path, fake):
    # The worker exits while a process it started still holds its stderr:
    # the job object is closed first (ending that process), so the stderr
    # reader ends and closing the streams does not wait for it.
    _mode(fake, "orphan")
    # A join that waited for the grandchild would take this long; the
    # bound below stays far from it so a slow start under load does not
    # look like a held-up end.
    fake.setattr(archive, "_STDERR_JOIN_SECS", 120)
    childPid = tmp_path / "child.pid"
    fake.setenv("FAKE_VODSPLIT_CHILD_PID", str(childPid))
    started = time.monotonic()
    job = service.Create(SOURCE)
    try:
        done = _ended(service, job["id"])
        assert done["state"] == "done"
        assert time.monotonic() - started < 60
        grandchild = int(childPid.read_text(encoding="utf-8"))
        _until(lambda: not _alive(grandchild), "the grandchild to end")
    finally:
        if childPid.exists():
            _kill(int(childPid.read_text(encoding="utf-8")))


def test_a_job_left_running_by_a_previous_run_is_cancelled(
        fake, work, monkeypatch):
    jobId = "20261101-120000-ab12"
    (work / jobId).mkdir(parents=True)
    (work / jobId / "job.json").write_text(json.dumps({
        "id": jobId, "created_at": "2026-11-01T12:00:00+09:00",
        "source": SOURCE, "state": "running", "logs": []}),
        encoding="utf-8")
    service = ArchiveJobService()
    monkeypatch.setattr(ArchiveJobService, "instance", service)

    assert [job["state"] for job in service.Jobs()] == ["cancelled"]
    record = _disk(work, jobId)
    assert record["state"] == "cancelled"
    assert record["logs"] == [archive.STALE_NOTE]
    assert service.Job(jobId)[0]["logs"] == [archive.STALE_NOTE]

    assert service.Resume(jobId)["state"] == "running"
    assert _ended(service, jobId)["state"] == "done"
    service.Shutdown(5.0)


def test_the_jobs_are_listed_newest_first(fake, work):
    for jobId, state in (("20261101-120000-aaaa", "done"),
                         ("20261103-090000-cccc", "failed"),
                         ("20261102-080000-bbbb", "error")):
        (work / jobId).mkdir(parents=True)
        (work / jobId / "job.json").write_text(json.dumps(
            {"id": jobId, "created_at": "x", "source": SOURCE,
             "state": state, "sets": 3 if state == "done" else None}),
            encoding="utf-8")
    (work / "downloads").mkdir()
    (work / "not-a-job").mkdir()
    (work / "20261104-000000-dddd").mkdir()       # no job.json
    jobs = ArchiveJobService().Jobs()
    assert [job["id"] for job in jobs] == [
        "20261103-090000-cccc", "20261102-080000-bbbb",
        "20261101-120000-aaaa"]
    assert jobs[2]["sets"] == 3 and jobs[0]["sets"] is None


@pytest.mark.parametrize("jobId", [
    "", "x", "2026111-000000-abcd", "20261116-000000-ABCD",
    "20261116-000000-abcd\n", "../20261116-000000-abcd",
    "20261116-000000-abcde", None, 5,
])
def test_a_job_id_that_is_not_one_is_not_found(fake, work, jobId):
    service = ArchiveJobService()
    for call in (service.Job, service.Cancel, service.Resume):
        with pytest.raises(ArchiveError) as refused:
            call(jobId)
        assert refused.value.code == archive.NOT_FOUND


def test_an_unknown_job_is_not_found(fake, work):
    service = ArchiveJobService()
    for call in (service.Job, service.Cancel, service.Resume):
        with pytest.raises(ArchiveError) as refused:
            call("20261116-000000-zzzz")
        assert refused.value.code == archive.NOT_FOUND


def test_a_job_folder_that_leads_outside_the_work_folder_is_not_found(
        fake, work, tmp_path):
    jobId = "20261116-000000-link"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "job.json").write_text(json.dumps(
        {"id": jobId, "source": SOURCE, "state": "done"}), encoding="utf-8")
    work.mkdir()
    try:
        os.symlink(outside, work / jobId, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows without the symlink privilege: a junction needs none.
        try:
            import _winapi
            _winapi.CreateJunction(str(outside), str(work / jobId))
        except (ImportError, AttributeError, OSError):
            pytest.skip("no symlink or junction here")
    with pytest.raises(ArchiveError) as refused:
        ArchiveJobService().Job(jobId)
    assert refused.value.code == archive.NOT_FOUND


@pytest.mark.parametrize("source", [
    None, "", "   ", 5, "vod.mp4", "ftp://example.com/x",
    "https://", "https://example.com/\x00", "C:\\no\\such\\video.mp4",
    "x" * (archive.MAX_SOURCE + 1),
])
def test_a_source_that_is_not_a_url_or_a_file_is_refused(service, source):
    with pytest.raises(ArchiveError) as refused:
        service.Create(source)
    assert refused.value.code == archive.INVALID
    assert service.Jobs() == []


NETWORK_SOURCES = [
    "\\\\fileserver\\share\\vod.mp4",
    "//fileserver/share/vod.mp4",
    "\\/fileserver/share/vod.mp4",
    "\\\\?\\UNC\\fileserver\\share\\vod.mp4",
    "\\\\?\\C:\\vod.mp4",
    "\\\\.\\pipe\\vod",
    "  \\\\fileserver\\share\\vod.mp4  ",
]


@contextlib.contextmanager
def _no_file_system(monkeypatch, looked):
    """Within it, any look at the file system is recorded in `looked` and
    fails (Windows would connect to a share to answer it, with this user's
    credentials)."""

    def look(*args, **_kwargs):
        looked.append(args)
        raise OSError(f"the file system was asked: {args}")

    with monkeypatch.context() as patch:
        for name in ("isfile", "exists", "isdir", "realpath"):
            patch.setattr(archive.os.path, name, look)
        patch.setattr(archive.os, "stat", look)
        yield


@pytest.mark.parametrize("source", NETWORK_SOURCES)
def test_a_shares_path_is_refused_unread(fake, work, monkeypatch, source):
    looked = []
    with _no_file_system(monkeypatch, looked), \
            pytest.raises(ArchiveError) as refused:
        archive.CheckSource(source)
    assert refused.value.code == archive.INVALID
    assert "共有フォルダ" in refused.value.message
    assert looked == []


def _stored(work, jobId, **fields):
    (work / jobId).mkdir(parents=True, exist_ok=True)
    (work / jobId / "job.json").write_text(json.dumps(dict(
        {"id": jobId, "created_at": "x", "state": "cancelled"}, **fields)),
        encoding="utf-8")


@pytest.mark.parametrize("source", NETWORK_SOURCES + [
    "", "vod.mp4", "ftp://example.com/vod.mp4", "https://",
    "C:\\no\\such\\video.mp4"])
def test_a_resume_checks_the_stored_source_again(fake, work, source):
    # job.json is a file the operator may edit: a resume takes its source
    # through a start's checks before asking the worker.
    asked = []
    service = ArchiveJobService(probe=lambda: asked.append(1))
    jobId = "20261101-120000-ab12"
    _stored(work, jobId, source=source)
    with pytest.raises(ArchiveError) as refused:
        service.Resume(jobId)
    assert refused.value.code == archive.SOURCE_UNUSABLE
    assert refused.value.message.startswith(
        "このジョブの元動画を使えないため再開できません（")
    assert asked == []
    assert json.loads((work / jobId / "job.json").read_text(
        encoding="utf-8"))["state"] == "cancelled"


def test_a_resume_reads_a_share_unread(fake, work, monkeypatch):
    service = ArchiveJobService(probe=lambda: pytest.fail("probed"))
    jobId = "20261101-120000-ab12"
    _stored(work, jobId, source="\\\\fileserver\\share\\vod.mp4")
    record = service._DiskRecord(str(work / jobId), jobId)
    looked = []
    with _no_file_system(monkeypatch, looked), \
            pytest.raises(ArchiveError) as refused:
        service._ResumableSource(record)
    assert refused.value.code == archive.SOURCE_UNUSABLE
    assert looked == []


def test_a_resumed_job_shows_no_sets_while_it_runs(service, work, fake):
    # The previous run's out/sets.json stays on disk until the worker
    # writes its own at the end: not shown as the running job's.
    jobId = "20261101-120000-ab12"
    _stored(work, jobId, source=SOURCE)
    (work / jobId / "out").mkdir()
    (work / jobId / "out" / "sets.json").write_text(
        json.dumps({"sets": [{"no": 1}]}), encoding="utf-8")
    assert service.Job(jobId)[1] == {"sets": [{"no": 1}]}

    _mode(fake, "slow")
    service.Resume(jobId)
    _until(lambda: (service.Job(jobId)[0]["progress"] or {}).get("eta"),
           "a progress line")
    assert service.Job(jobId)[1] is None
    service.Cancel(jobId)
    assert _ended(service, jobId)["state"] == "cancelled"
    assert service.Job(jobId)[1] == {"sets": [{"no": 1}]}


@pytest.mark.parametrize("results", [
    [], "x", {}, {"sets": []}, {"sets": {}}, {"sets": ["x"]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": -1, "s2": 0}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": "1", "s2": 0}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": True, "s2": 0}]},
    # the worker's validate_set refuses these too
    {"sets": [{"p1": "", "p2": "B", "s1": 1, "s2": 0}]},
    {"sets": [{"p1": "A", "p2": "  ", "s1": 1, "s2": 0}]},
    {"sets": [{"p1": 1, "p2": "B", "s1": 1, "s2": 0}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "c1": 3}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "c2": []}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "char_source": 1}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "id": True}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "id": 1.5}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "games": "x"}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "games": ["x"]}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0,
               "games": [{"c1": 1}]}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0,
               "games": [{"c2": {}}]}]},
    {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, "round": 3}]},
])
def test_results_that_are_not_a_tournaments_sets_are_refused(
        service, results):
    with pytest.raises(ArchiveError) as refused:
        service.Create(SOURCE, results)
    assert refused.value.code == archive.INVALID


@pytest.mark.parametrize("item, says", [
    ({"p1": "", "p2": "B", "s1": -1, "s2": 0, "c1": 3}, "選手名（p1, p2）"),
    ({"p1": "A", "p2": "B", "s1": -1, "s2": 0, "c1": 3}, "スコア（s1, s2）"),
    ({"p1": "A", "p2": "B", "s1": 1, "s2": 0, "c1": 3, "round": 3}, "の c1 "),
])
def test_results_are_checked_in_the_workers_order(item, says):
    # vodsplit's validate_set: the names, the scores, then the optional
    # fields -- a set with several faults gets the worker's message.
    with pytest.raises(ArchiveError) as refused:
        archive.CheckResults({"sets": [item]})
    assert says in refused.value.message


@pytest.mark.parametrize("extra", [
    {}, {"round": None}, {"round": "Grand Final"},
    {"c1": None, "c2": None, "char_source": None, "id": None, "games": None},
    {"c1": "ホムラ/ヒカリ", "c2": "マリオ", "char_source": "report"},
    {"id": "abc"}, {"id": 12},
    {"games": []}, {"games": [{}, {"c1": None, "c2": "リンク"}]},
])
def test_optional_set_fields_of_the_right_type_are_accepted(
        service, extra):
    sets = {"sets": [{"p1": "A", "p2": "B", "s1": 1, "s2": 0, **extra}]}
    assert archive.CheckResults(sets) is sets


def test_the_work_folder_is_saved_absolute(service, tmp_path):
    target = tmp_path / "elsewhere" / "videos"
    service.SetWorkDir(f"  {target}  ")
    assert target.is_dir()
    assert SettingsManager.Get(archive.WORK_DIR_KEY) == str(target)
    assert service.WorkDir() == str(target)
    for value in ("", "   ", "relative\\dir", None, 3):
        with pytest.raises(ArchiveError) as refused:
            service.SetWorkDir(value)
        assert refused.value.code == archive.INVALID
    file = tmp_path / "a-file"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(ArchiveError):
        service.SetWorkDir(str(file))
    assert SettingsManager.Get(archive.WORK_DIR_KEY) == str(target)


def test_the_default_work_folder_is_the_users_videos(settings):  # noqa: F811
    assert ArchiveJobService().WorkDir() == os.path.join(
        str(Path.home()), "Videos", "Autona")


# ── the v1 routes ────────────────────────────────────────────────────────

BASE = "/api/v1/archive"


def _data(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["ok"] is True
    return body["data"]


def _refusal(response, status, code, reason):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    error = response.get_json()["error"]
    assert (error["code"], error["reason"]) == (code, reason)
    return error["message"]


def _route_ended(client, jobId):
    return _until(
        lambda: (lambda job: job if job["state"] != "running" else None)(
            _data(client.get(f"{BASE}/jobs/{jobId}"))["job"]),
        f"job {jobId} to end")


class _Page:
    """The archive page in the shared view: its reads are plain fetches of
    this PC (THIS_PC), its writes carry the shell's secret (SHELL)."""

    def __init__(self, server, secret):  # noqa: F811
        self.client = _client(server)
        self.shell = _shell_headers(secret)

    def get(self, url):
        return self.client.get(url)

    def post(self, url, **kwargs):
        return self.client.post(url, headers=self.shell, **kwargs)

    def patch(self, url, **kwargs):
        return self.client.patch(url, headers=self.shell, **kwargs)

    def put(self, url, **kwargs):
        return self.client.put(url, headers=self.shell, **kwargs)


@pytest.fixture
def page(server, shellSecret):  # noqa: F811
    return _Page(server, shellSecret)


def test_the_page_runs_a_job_through_the_routes(page, service, work):
    status = _data(page.get(f"{BASE}/status"))
    assert status == {"available": True, "version": "0.0.0-fake",
                      "format": 1, "reason": None,
                      "work_dir": os.path.normpath(str(work)),
                      "running_job": None, "running_kind": None,
                      "features": ["titles"],
                      "thumbnail_background": archive.FRAME_BACKGROUND}

    created = _data(page.post(f"{BASE}/jobs", json={
        "source": SOURCE, "results": RESULTS, "event": None}), 201)
    jobId = created["job"]["id"]
    assert created["job"]["state"] == "running"
    assert created["job"]["has_results"] is True

    done = _route_ended(page, jobId)
    assert done["state"] == "done" and done["sets"] == 2
    detail = _data(page.get(f"{BASE}/jobs/{jobId}"))
    assert detail["sets"]["sets"][1]["needs_review"] is True
    assert _data(page.get(f"{BASE}/jobs")) == {"jobs": [{
        "id": jobId, "created_at": done["created_at"], "source": SOURCE,
        "state": "done", "sets": 2, "needs_review": 1}]}

    message = _refusal(page.post(f"{BASE}/jobs/{jobId}:resume"), 409,
                       "CONFLICT", archive.ALREADY_DONE)
    assert message == "このジョブは完了しています。"
    _refusal(page.post(f"{BASE}/jobs/{jobId}:cancel"), 409, "CONFLICT",
             archive.NOT_RUNNING)


def test_a_running_job_is_cancelled_and_resumed_through_the_routes(
        page, service, fake, tmp_path):
    _mode(fake, "slow")
    jobId = _data(page.post(f"{BASE}/jobs", json={"source": SOURCE}),
                  201)["job"]["id"]
    status = _data(page.get(f"{BASE}/status"))
    assert (status["running_job"], status["running_kind"]) == (
        jobId, archive.ANALYSIS)
    assert _data(page.get(f"{BASE}/jobs/{jobId}"))["sets"] is None
    _refusal(page.post(f"{BASE}/jobs", json={"source": SOURCE}), 409,
             "CONFLICT", archive.BUSY)
    _refusal(page.patch(f"{BASE}/settings",
                        json={"work_dir": str(tmp_path / "other")}),
             409, "CONFLICT", archive.BUSY)

    assert _data(page.post(f"{BASE}/jobs/{jobId}:cancel"))["job"][
        "state"] == "running"
    assert _route_ended(page, jobId)["state"] == "cancelled"

    _mode(fake, "ok")
    assert _data(page.post(f"{BASE}/jobs/{jobId}:resume"))["job"][
        "state"] == "running"
    assert _route_ended(page, jobId)["state"] == "done"


def test_the_routes_answer_cores_refusals(page, service, fake):
    message = _refusal(page.post(f"{BASE}/jobs", json={"source": ""}),
                       400, "INVALID_ARGUMENT", archive.INVALID)
    assert "URL" in message
    _refusal(page.post(f"{BASE}/jobs", json={
        "source": SOURCE, "results": {"sets": []}}), 400,
        "INVALID_ARGUMENT", archive.INVALID)
    _refusal(page.post(f"{BASE}/jobs", data="not json"), 400,
             "INVALID_ARGUMENT", archive.INVALID)
    for jobId in ("20261116-000000-zzzz", "nope", "20261116-000000-zzzz:x"):
        _refusal(page.get(f"{BASE}/jobs/{jobId}"), 404, "NOT_FOUND",
                 archive.NOT_FOUND)
    _refusal(page.post(f"{BASE}/jobs/20261116-000000-zzzz:cancel"), 404,
             "NOT_FOUND", archive.NOT_FOUND)
    _refusal(page.post(f"{BASE}/jobs/20261116-000000-zzzz:resume"), 404,
             "NOT_FOUND", archive.NOT_FOUND)
    for workDir in ("", "relative", None):
        _refusal(page.patch(f"{BASE}/settings", json={"work_dir": workDir}),
                 400, "INVALID_ARGUMENT", archive.INVALID)

    fake.delenv(worker.ENV_COMMAND)
    service._Forget()
    message = _refusal(page.post(f"{BASE}/jobs", json={"source": SOURCE}),
                       503, "UNAVAILABLE", archive.UNAVAILABLE)
    assert "vodsplit-worker" in message
    status = _data(page.get(f"{BASE}/status?refresh=true"))
    assert status["available"] is False and "vodsplit-worker" in \
        status["reason"]


def test_the_route_refuses_a_shares_path(page, service):
    for source in NETWORK_SOURCES:
        _refusal(page.post(f"{BASE}/jobs", json={"source": source}),
                 400, "INVALID_ARGUMENT", archive.INVALID)
    assert service.Jobs() == []


def test_the_resume_route_refuses_a_stored_source_it_cannot_use(
        page, service, work):
    jobId = "20261101-120000-ab12"
    _stored(work, jobId, source="\\\\fileserver\\share\\vod.mp4")
    message = _refusal(page.post(f"{BASE}/jobs/{jobId}:resume"), 409,
                       "CONFLICT", archive.SOURCE_UNUSABLE)
    assert "再開できません" in message
    assert _data(page.get(f"{BASE}/jobs/{jobId}"))["job"][
        "state"] == "cancelled"


def test_a_job_body_is_at_most_two_megabytes(page, service):
    big = {"source": SOURCE,
           "results": {"sets": [], "pad": "x" * (2 * 1024 * 1024)}}
    response = page.post(f"{BASE}/jobs", json=big)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert service.Jobs() == []


def test_the_work_folder_is_changed_through_the_route(page, service,
                                                      tmp_path):
    target = tmp_path / "new-work"
    status = _data(page.patch(
        f"{BASE}/settings", json={"work_dir": str(target)}))
    assert status["work_dir"] == str(target) and target.is_dir()
    assert SettingsManager.Get(archive.WORK_DIR_KEY) == str(target)


# ── the preview's files (ADR-006 A3, D7) ────────────────────────────────

def _link(target, link):
    """Folder `link` leading to folder `target` (a symlink, else a
    junction); skips the test where neither can be made."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, AttributeError, OSError):
            pytest.skip("no symlink or junction here")


def _sets(work, jobId, jobSource=SOURCE, **fields):
    """A finished job (its job.json's source `jobSource`) whose
    out/sets.json is `fields` -> its out/."""
    _stored(work, jobId, source=jobSource, state="done")
    out = work / jobId / "out"
    out.mkdir(exist_ok=True)
    (out / "sets.json").write_text(json.dumps(dict(
        {"format": 1, "sets": []}, **fields)), encoding="utf-8")
    return out


def _not_found(call, *args):
    with pytest.raises(ArchiveError) as refused:
        call(*args)
    assert refused.value.code == archive.NOT_FOUND


def test_the_page_plays_a_url_jobs_download_and_shows_its_frames(
        page, service, work):
    jobId = _data(page.post(f"{BASE}/jobs", json={"source": SOURCE}),
                  201)["job"]["id"]
    assert _route_ended(page, jobId)["state"] == "done"
    sets = _data(page.get(f"{BASE}/jobs/{jobId}"))["sets"]
    downloaded = work / "downloads" / "fakevideo01.mp4"
    assert sets["source"] == str(downloaded)
    assert [row["thumb"] for row in sets["sets"]] == [
        "01_fake_bg.jpg", "02_fake_bg.jpg"]

    video = page.get(f"{BASE}/jobs/{jobId}/video")
    assert video.status_code == 200
    assert video.data == downloaded.read_bytes()
    assert video.mimetype == "video/mp4"
    assert video.headers["Accept-Ranges"] == "bytes"
    assert video.headers["Cache-Control"] == "no-store"
    assert video.headers["X-Content-Type-Options"] == "nosniff"

    # The <video> seeks with ranges.
    size = len(fake_worker.VIDEO_BYTES)
    part = page.client.get(f"{BASE}/jobs/{jobId}/video",
                           headers={"Range": "bytes=256-511"})
    assert part.status_code == 206
    assert part.headers["Content-Range"] == f"bytes 256-511/{size}"
    assert part.data == fake_worker.VIDEO_BYTES[256:512]
    assert part.headers["Cache-Control"] == "no-store"
    tail = page.client.get(f"{BASE}/jobs/{jobId}/video",
                           headers={"Range": "bytes=-10"})
    assert tail.status_code == 206
    assert tail.data == fake_worker.VIDEO_BYTES[-10:]
    beyond = page.client.get(f"{BASE}/jobs/{jobId}/video",
                             headers={"Range": f"bytes={size}-"})
    assert beyond.status_code == 416
    assert beyond.headers["Content-Range"] == f"bytes */{size}"
    head = page.client.head(f"{BASE}/jobs/{jobId}/video")
    assert head.status_code == 200 and head.data == b""
    assert int(head.headers["Content-Length"]) == size

    for number in ("1", "2"):
        frame = page.get(f"{BASE}/jobs/{jobId}/frames/{number}")
        assert frame.status_code == 200
        assert frame.mimetype == "image/jpeg"
        assert frame.data == fake_worker.FRAME_BYTES
        assert frame.headers["Cache-Control"] == "no-store"
        assert frame.headers["X-Content-Type-Options"] == "nosniff"
    for number in ("3", "0", "01x", "-1", "1.0"):
        _refusal(page.get(f"{BASE}/jobs/{jobId}/frames/{number}"), 404,
                 "NOT_FOUND", archive.NOT_FOUND)


def test_a_local_source_is_served_where_job_json_names_it(
        page, service, work, tmp_path):
    # A file of this PC, outside the work folder: the operator picked it.
    video = tmp_path / "vods" / "match.webm"
    video.parent.mkdir()
    video.write_bytes(b"webm bytes")
    jobId = "20261116-100000-loca"
    _sets(work, jobId, jobSource=str(video), source=str(video))
    answer = page.get(f"{BASE}/jobs/{jobId}/video")
    assert answer.status_code == 200 and answer.data == b"webm bytes"
    assert answer.mimetype == "video/webm"
    for name, kind in (("a.MP4", "video/mp4"), ("a.m4v", "video/mp4"),
                       ("a.mkv", "video/x-matroska"),
                       ("a.mov", "video/quicktime")):
        (tmp_path / "vods" / name).write_bytes(b"v")
        _stored(work, jobId, source=str(tmp_path / "vods" / name),
                state="done")
        assert service.VideoFile(jobId)[1] == kind
    # A video the worker reads but a browser does not play (OBS' .flv / .ts
    # ...): there, but not previewable -- 415 with its own reason (the page
    # says the sets and the timeline still work), never "not found". HEAD
    # too: the page asks that way when the <video> fails.
    for name in ("match.ts", "match.FLV", "match.avi", "match.m2ts",
                 "match.wmv", "match.mpg"):
        other = tmp_path / "vods" / name
        other.write_bytes(b"x")
        _stored(work, jobId, source=str(other), state="done")
        with pytest.raises(ArchiveError) as refused:
            service.VideoFile(jobId)
        assert (refused.value.code, refused.value.message) == (
            archive.NOT_PREVIEWABLE, archive.VIDEO_NOT_PREVIEWABLE)
        assert _refusal(page.get(f"{BASE}/jobs/{jobId}/video"), 415,
                        "UNSUPPORTED_MEDIA_TYPE", archive.NOT_PREVIEWABLE
                        ) == archive.VIDEO_NOT_PREVIEWABLE
        assert page.client.head(f"{BASE}/jobs/{jobId}/video"
                                ).status_code == 415
        # Gone, it is not found as any other video.
        other.unlink()
        _not_found(service.VideoFile, jobId)
        _refusal(page.get(f"{BASE}/jobs/{jobId}/video"), 404, "NOT_FOUND",
                 archive.NOT_FOUND)
    # Not a video at all (anything the operator typed, the worker's own
    # partial download): not found, there or not.
    for name in ("notes.txt", "sets.json", "match", "match.mp4.part"):
        other = tmp_path / "vods" / name
        other.write_bytes(b"x")
        _stored(work, jobId, source=str(other), state="done")
        _not_found(service.VideoFile, jobId)
        _refusal(page.get(f"{BASE}/jobs/{jobId}/video"), 404, "NOT_FOUND",
                 archive.NOT_FOUND)
    # Gone since: not found.
    gone = tmp_path / "vods" / "gone.mp4"
    gone.write_bytes(b"v")
    _stored(work, jobId, source=str(gone), state="done")
    assert service.VideoFile(jobId)[1] == "video/mp4"
    gone.unlink()
    _refusal(page.get(f"{BASE}/jobs/{jobId}/video"), 404, "NOT_FOUND",
             archive.NOT_FOUND)


@pytest.mark.parametrize("source", NETWORK_SOURCES + [
    "\\\\fileserver\\share\\vod.flv", "//fileserver/share/vod.ts",
    "", "vod.mp4", "ftp://example.com/vod.mp4", "C:\\no\\such\\video.mp4",
    "C:\\no\\such\\video.flv"])
def test_a_source_a_start_would_refuse_is_not_served(
        page, service, work, monkeypatch, source):
    jobId = "20261116-100000-shar"
    _sets(work, jobId, jobSource=source, source=source)
    looked = []
    real = {name: getattr(archive.os.path, name)
            for name in ("isfile", "exists", "isdir", "realpath")}

    def guard(name):
        def look(path, *args, **kwargs):
            text = str(path).strip()
            if archive._IsNetworkPath(text) or "fileserver" in text:
                looked.append(path)
                raise OSError("a share was asked")
            return real[name](path, *args, **kwargs)
        return look

    with monkeypatch.context() as patch:
        for name in real:
            patch.setattr(archive.os.path, name, guard(name))
        _not_found(service.VideoFile, jobId)
        _refusal(page.get(f"{BASE}/jobs/{jobId}/video"), 404, "NOT_FOUND",
                 archive.NOT_FOUND)
    assert looked == []


def test_a_url_jobs_video_is_only_its_download_in_the_work_folder(
        fake, work, tmp_path):
    service = ArchiveJobService()
    jobId = "20261116-100000-down"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.open.mp4").write_bytes(b"not the work folder's")
    # No sets.json yet (the worker never ended): nothing to play.
    _stored(work, jobId, source=SOURCE, state="cancelled")
    _not_found(service.VideoFile, jobId)
    # sets.json names a file elsewhere: only its name is looked for, in
    # downloads/ -- not there.
    _sets(work, jobId, source=str(outside / "x.open.mp4"))
    _not_found(service.VideoFile, jobId)
    for name in (None, 5, "", "..", "downloads\\", "x.mp4:stream",
                 "C:\\x\\..", "a\x00.mp4"):
        _sets(work, jobId, source=name)
        _not_found(service.VideoFile, jobId)
    # A download that is not a video (yt-dlp's part file, a stray file) is
    # not found; a video of a type the browser does not play is there but
    # not previewable, as a local file's.
    (work / "downloads").mkdir()
    for name in ("x.open.mp4.part", "x.json"):
        (work / "downloads" / name).write_bytes(b"x")
        _sets(work, jobId, source=name)
        _not_found(service.VideoFile, jobId)
    (work / "downloads" / "x.flv").write_bytes(b"x")
    _sets(work, jobId, source="x.flv")
    with pytest.raises(ArchiveError) as refused:
        service.VideoFile(jobId)
    assert refused.value.code == archive.NOT_PREVIEWABLE
    (work / "downloads" / "x.open.mp4").write_bytes(b"v")
    _sets(work, jobId, source="x.open.mp4")
    assert service.VideoFile(jobId)[1] == "video/mp4"
    shutil.rmtree(work / "downloads")
    # A downloads/ that leads outside the work folder serves nothing.
    _link(outside, work / "downloads")
    _sets(work, jobId, source="D:\\moved\\x.open.mp4")
    assert (work / "downloads" / "x.open.mp4").is_file()
    _not_found(service.VideoFile, jobId)


@pytest.mark.parametrize("name", [
    "vid.mp4",          # --codec h264 / any
    "vid.open.mp4",     # a job an older worker ran with its default open
])
def test_the_download_is_found_after_the_work_folder_moved(fake, work, name):
    jobId = "20261116-100000-move"
    _sets(work, jobId, source=f"C:\\old\\work\\downloads\\{name}")
    (work / "downloads").mkdir()
    (work / "downloads" / name).write_bytes(b"v")
    path, kind = ArchiveJobService().VideoFile(jobId)
    assert path == str(work / "downloads" / name)
    assert kind == "video/mp4"


def test_a_frame_is_the_sets_thumb_in_the_jobs_out_folder(fake, work,
                                                         tmp_path):
    service = ArchiveJobService()
    jobId = "20261116-100000-fram"
    out = _sets(work, jobId, sets=[
        {"no": 1, "thumb": "01_a_bg.jpg"},
        {"no": 2, "thumb": "..\\..\\job.json"},
        {"no": 3, "thumb": "../../secret.jpg"},
        {"no": 4},
        {"no": True, "thumb": "01_a_bg.jpg"},
        {"no": 6, "thumb": 7},
        {"no": 7, "thumb": "07_b_bg.jpg"},
    ])
    (out / "01_a_bg.jpg").write_bytes(b"jpg")
    (tmp_path / "secret.jpg").write_bytes(b"outside")
    assert service.FrameFile(jobId, "1") == (str(out / "01_a_bg.jpg"),
                                             "image/jpeg")
    # 5 is a bool's "no"; 7 is named but not written (the worker stopped).
    for number in ("2", "3", "4", "5", "6", "7", "8", "", "x", "1 ", None,
                   1):
        _not_found(service.FrameFile, jobId, number)
    # An out/ that leads outside the work folder serves nothing.
    linked = "20261116-100000-fral"
    _stored(work, linked, source=SOURCE, state="done")
    elsewhere = tmp_path / "elsewhere-out"
    elsewhere.mkdir()
    (elsewhere / "sets.json").write_text(json.dumps(
        {"sets": [{"no": 1, "thumb": "01_a_bg.jpg"}]}), encoding="utf-8")
    (elsewhere / "01_a_bg.jpg").write_bytes(b"jpg")
    _link(elsewhere, work / linked / "out")
    _not_found(service.FrameFile, linked, "1")


def test_a_frame_is_never_another_jobs(fake, work):
    # An out/ leading to another job's out/ (inside the work folder): the
    # frame must resolve inside this job's own out/.
    service = ArchiveJobService()
    own = "20261116-100000-owna"
    out = _sets(work, own, sets=[{"no": 1, "thumb": "01_a_bg.jpg"}])
    (out / "01_a_bg.jpg").write_bytes(b"jpg")
    assert service.FrameFile(own, "1")[0] == str(out / "01_a_bg.jpg")
    other = "20261116-100000-othr"
    _stored(work, other, source=SOURCE, state="done")
    _link(out, work / other / "out")
    assert (work / other / "out" / "01_a_bg.jpg").is_file()
    _not_found(service.FrameFile, other, "1")


def test_a_running_jobs_files_are_not_served(page, service, fake, work):
    # As its sets (null while it runs): a resumed job's sets.json and
    # download are the previous run's until the worker ends.
    _mode(fake, "slow")
    jobId = _data(page.post(f"{BASE}/jobs", json={"source": SOURCE}),
                  201)["job"]["id"]
    out = work / jobId / "out"
    (work / "downloads").mkdir(exist_ok=True)
    download = work / "downloads" / "fakevideo01.open.mp4"
    download.write_bytes(b"v")
    (out / "sets.json").write_text(json.dumps(
        {"source": str(download),
         "sets": [{"no": 1, "thumb": "01_a_bg.jpg"}]}), encoding="utf-8")
    (out / "01_a_bg.jpg").write_bytes(b"jpg")
    _not_found(service.VideoFile, jobId)
    _not_found(service.FrameFile, jobId, "1")
    for url in (f"{BASE}/jobs/{jobId}/video",
                f"{BASE}/jobs/{jobId}/frames/1"):
        _refusal(page.get(url), 404, "NOT_FOUND", archive.NOT_FOUND)
    # Once it ended: both served (the slow fake writes no sets.json).
    _data(page.post(f"{BASE}/jobs/{jobId}:cancel"))
    assert _route_ended(page, jobId)["state"] == "cancelled"
    assert page.get(f"{BASE}/jobs/{jobId}/video").data == b"v"
    assert page.get(f"{BASE}/jobs/{jobId}/frames/1").data == b"jpg"


@pytest.mark.parametrize("error, status, code, reason", [
    (FileNotFoundError(2, "gone"), 404, "NOT_FOUND", archive.NOT_FOUND),
    (PermissionError(13, "held"), 409, "CONFLICT", archive.UNREADABLE),
    (OSError(22, "invalid"), 409, "CONFLICT", archive.UNREADABLE),
])
def test_a_file_that_cannot_be_opened_is_refused(page, service, work,
                                                 tmp_path, monkeypatch,
                                                 error, status, code,
                                                 reason):
    # Gone between core's look and send_file's open (deleted, moved), or
    # there but not to be opened (another app holds it, no permission):
    # core's words, never the path, never a 500.
    from src import AutonaApiV1

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    jobId = "20261116-100000-open"
    out = _sets(work, jobId, jobSource=str(video), source=str(video),
                sets=[{"no": 1, "thumb": "01_a_bg.jpg"}])
    (out / "01_a_bg.jpg").write_bytes(b"jpg")

    def refuse(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(AutonaApiV1, "send_file", refuse)
    gone = status == 404
    assert _refusal(page.get(f"{BASE}/jobs/{jobId}/video"), status, code,
                    reason) == (archive.VIDEO_MISSING if gone
                                else archive.FILE_UNREADABLE)
    assert _refusal(page.get(f"{BASE}/jobs/{jobId}/frames/1"), status,
                    code, reason) == (archive.FRAME_MISSING if gone
                                      else archive.FILE_UNREADABLE)


@pytest.mark.parametrize("jobId", [
    "x", "20261116-000000-ABCD", "20261116-000000-abcde",
    "20261116-000000-zzzz",
])
def test_an_unknown_or_malformed_jobs_files_are_not_found(page, service,
                                                          jobId):
    _not_found(service.VideoFile, jobId)
    _not_found(service.FrameFile, jobId, "1")
    for url in (f"{BASE}/jobs/{jobId}/video",
                f"{BASE}/jobs/{jobId}/frames/1"):
        _refusal(page.get(url), 404, "NOT_FOUND", archive.NOT_FOUND)


def test_a_job_folder_outside_the_work_folder_serves_no_file(fake, work,
                                                             tmp_path):
    jobId = "20261116-000000-link"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "job.json").write_text(json.dumps(
        {"id": jobId, "source": str(tmp_path / "v.mp4"), "state": "done"}),
        encoding="utf-8")
    (tmp_path / "v.mp4").write_bytes(b"v")
    work.mkdir()
    _link(outside, work / jobId)
    service = ArchiveJobService()
    _not_found(service.VideoFile, jobId)
    _not_found(service.FrameFile, jobId, "1")


# ── the review and the export (ADR-006 A4) ──────────────────────────────

KAPPA = {"round": "Winners Final", "p1": "Kappa", "s1": 3, "p2": "Lambda",
         "s2": 1}
MU = {"round": "Grand Final", "p1": "Kappa", "s1": 2, "p2": "Mu", "s2": 3}
UNUSED = {"round": "Losers Final", "p1": "Mu", "s1": 3, "p2": "Lambda",
          "s2": 0}
REVIEW_SETS = {
    "format": 1, "duration": 3600.0, "source_id": "fakevideo01",
    "event": "Fake Cup",
    "sets": [
        {"no": 1, "start": 120.0, "end": 720.0, "set": KAPPA,
         "game_starts": [130.0, 300.0]},
        {"no": 2, "start": 780.0, "end": 1380.0, "set": None,
         "needs_review": True, "game_starts": [790.0, "x"]},
        {"no": 3, "start": 1400.0, "end": 2000.0, "set": MU},
    ],
    "unused_sets": [UNUSED],
}


def test_a_review_keeps_what_the_operator_changed():
    checked = review.Check({"sets": {
        "1": {"confirmed": True},
        # The worker's own values are no change: left out.
        "2": {"confirmed": True, "start": 775.123, "end": 1380.0,
              "set": UNUSED},
        "3": {"confirmed": False, "set": MU},
    }}, REVIEW_SETS)
    assert checked == {"format": 1, "sets": {
        "1": {"confirmed": True},
        "2": {"confirmed": True, "start": 775.12, "set": UNUSED}}}
    stored = review.Stored(checked, REVIEW_SETS)
    assert stored == {"format": 1, "sets": {
        "1": {"base": [120.0, 720.0], "confirmed": True},
        "2": {"base": [780.0, 1380.0], "confirmed": True, "start": 775.12,
              "set": UNUSED}}}
    assert review.Normalize(json.loads(json.dumps(stored)),
                            REVIEW_SETS) == checked
    # A result taken from one set to another, the first given none.
    moved = review.Check({"sets": {"1": {"set": None}, "3": {"set": KAPPA}}},
                         REVIEW_SETS)
    assert moved["sets"] == {"1": {"set": None}, "3": {"set": KAPPA}}


@pytest.mark.parametrize("body, says", [
    (None, '"sets"'),
    ({"sets": []}, '"sets"'),
    ({"sets": {"9": {}}}, "セット 9 はこのジョブにありません"),
    ({"sets": {"01": {}}}, "セット 01 は"),
    ({"sets": {"0": {}}}, "セット 0 は"),
    ({"sets": {"1": True}}, "オブジェクトではありません"),
    ({"sets": {"1": {"base": [1, 2]}}}, "使えない項目があります: base"),
    ({"sets": {"1": {"confirmed": "yes"}}}, "confirmed は true か false"),
    ({"sets": {"1": {"start": "1:00"}}}, "start は秒数"),
    ({"sets": {"1": {"end": True}}}, "end は秒数"),
    ({"sets": {"1": {"start": float("nan")}}}, "start は秒数"),
    ({"sets": {"1": {"start": -1}}}, "1 秒以上"),
    ({"sets": {"1": {"start": 719.5}}}, "1 秒以上"),
    ({"sets": {"1": {"start": 800}}}, "1 秒以上"),
    ({"sets": {"3": {"start": 3600, "end": 3700}}}, "1 秒以上"),
    ({"sets": {"3": {"start": 3599.5, "end": 3700}}}, "1 秒以上"),
    # Less than 1 s before the video's end less END_MARGIN.
    ({"sets": {"3": {"start": 3599.0, "end": 3700}}}, "1 秒以上"),
    # 1.13 - 0.13 is a hair under 1 in floats: refused as the worker does.
    ({"sets": {"1": {"start": 0.13, "end": 1.13}}}, "1 秒以上"),
    ({"sets": {"1": {"set": {"p1": "Someone"}}}}, "このジョブの結果にありません"),
    ({"sets": {"1": {"set": "Kappa"}}}, "このジョブの結果にありません"),
    # The result set 1 keeps, given to set 2 too.
    ({"sets": {"2": {"set": KAPPA}}}, "同じ結果を 2 つのセット"),
    ({"sets": {"2": {"set": UNUSED}, "3": {"set": UNUSED}}},
     "割り当てています（#2・#3）"),
    ({"sets": {"1": {"removed": "yes"}}}, "removed は true か false"),
    ({"sets": {"1": {"removed": 1}}}, "removed は true か false"),
    ({"sets": {"1": {"removed": True, "note": "x"}}}, "使えない項目があります: note"),
])
def test_a_review_that_does_not_fit_the_sets_is_refused(body, says):
    with pytest.raises(review.ReviewError) as refused:
        review.Check(body, REVIEW_SETS)
    assert says in str(refused.value)


@pytest.mark.parametrize("start, end, fits", [
    # Two decimals do not make an exact length in floats: what the worker
    # checks (end - start < MIN_CLIP) is what is checked, and the page moves
    # the OUT a hundredth further when a rounded range falls short.
    (0.13, 1.13, False), (0.13, 1.14, True),
    (15.06, 16.06, False), (15.06, 16.07, True),
    (14.06, 15.06, True), (15.08, 16.08, False),
])
def test_a_range_is_checked_as_the_worker_checks_it(start, end, fits):
    body = {"sets": {"1": {"start": start, "end": end}}}
    if fits:
        assert review.Check(body, REVIEW_SETS)["sets"]["1"] == {
            "start": start, "end": end}
    else:
        with pytest.raises(review.ReviewError):
            review.Check(body, REVIEW_SETS)


@pytest.mark.parametrize("start, fits", [
    # 1 s up to the video's end (3600) less END_MARGIN (0.05).
    (3598.95, True), (3598.96, False), (3599.0, False)])
def test_a_range_at_the_videos_end_keeps_a_margin(start, fits):
    body = {"sets": {"3": {"start": start, "end": 3600.0}}}
    if fits:
        assert review.Check(body, REVIEW_SETS)["sets"]["3"]["start"] == start
    else:
        with pytest.raises(review.ReviewError):
            review.Check(body, REVIEW_SETS)


def test_results_are_the_same_whatever_their_numbers_look_like():
    # One comparison for every check (ResultKey): JSON with sorted keys and
    # whole numbers as integers, as the page's canonical makes it.
    assert review.ResultKey({"b": 1.0, "a": [2.0, True, None, 2.5]}) == (
        '{"a":[2,true,null,2.5],"b":1}')
    assert review.ResultKey(None) == "null"
    # KAPPA as the page sends it back (3.0 is 3) is KAPPA, held by set 1.
    with pytest.raises(review.ReviewError) as refused:
        review.Check({"sets": {"2": {"set": dict(KAPPA, s1=3.0)}}},
                     REVIEW_SETS)
    assert "同じ結果" in str(refused.value)
    # Set 1's own, written another way, is no change.
    assert review.Check({"sets": {"1": {"set": dict(KAPPA, s1=3.0)}}},
                        REVIEW_SETS)["sets"] == {}
    assert review.Normalize({"format": 1, "sets": {"1": {
        "base": [120.0, 720.0], "set": dict(KAPPA, s1=3.0)}}},
        REVIEW_SETS)["sets"] == {}
    twice = dict(REVIEW_SETS, unused_sets=[UNUSED, dict(UNUSED, s1=3.0)])
    assert review.Candidates(twice) == [KAPPA, MU, UNUSED]


def test_an_end_past_the_video_is_the_workers_to_cut():
    # vodsplit cuts an end past the video at its end (with a warning).
    assert review.Check({"sets": {"3": {"end": 4000}}}, REVIEW_SETS)[
        "sets"] == {"3": {"end": 4000}}


def test_reading_keeps_what_still_fits_the_sets():
    def read(sets, **entries):
        return review.Normalize({"format": 1, "sets": entries}, REVIEW_SETS)[
            "sets"] if sets is None else review.Normalize(sets, REVIEW_SETS)

    for broken in (None, [], "x", {"format": 2, "sets": {}},
                   {"format": 1, "sets": []}, {"sets": {}}):
        assert review.Normalize(broken, REVIEW_SETS) == {"format": 1,
                                                         "sets": {}}
    base1, base2 = [120.0, 720.0], [780.0, 1380.0]
    assert read(None, **{
        # A set that moved since (another sets.json): dropped whole.
        "1": {"base": [100.0, 720.0], "confirmed": True},
        # No base: not one this review wrote.
        "3": {"confirmed": True},
        "9": {"base": base1, "confirmed": True},
        "x": {"base": base1, "confirmed": True},
        # A range that no longer fits goes with its confirmation: an
        # export must not cut the worker's range in its place unasked.
        "2": {"base": base2, "confirmed": True, "start": 1379.9,
              "set": UNUSED, "note": "kept out"},
    }) == {"2": {"set": UNUSED}}
    # A result that is not the job's goes alone.
    assert read(None, **{"2": {"base": base2, "confirmed": True,
                               "start": 775.5, "set": {"p1": "gone"}}}) == {
        "2": {"confirmed": True, "start": 775.5}}
    # Two sets given one result: the review's assignment goes.
    assert read(None, **{"2": {"base": base2, "set": KAPPA}}) == {}
    assert read(None, **{"1": {"base": base1, "set": UNUSED},
                         "2": {"base": base2, "set": UNUSED,
                               "confirmed": True}}) == {
        "2": {"confirmed": True}}
    assert read(None, **{"1": {"base": base1, "set": None},
                         "3": {"base": [1400, 2000], "set": KAPPA}}) == {
        "1": {"set": None}, "3": {"set": KAPPA}}


def test_the_export_takes_the_confirmed_sets_as_reviewed():
    checked = review.Check({"sets": {
        "2": {"confirmed": True, "start": 770, "set": UNUSED},
        "1": {"confirmed": True, "set": None},
        "3": {"end": 2100},
    }}, REVIEW_SETS)
    assert review.ExportSets(checked, REVIEW_SETS) == [
        {"no": 1, "start": 120.0, "end": 720.0, "title": None,
         "description": None, "set": None, "game_starts": [130.0, 300.0]},
        {"no": 2, "start": 770, "end": 1380.0, "title": None,
         "description": None, "set": UNUSED, "game_starts": [790.0]},
    ]
    assert review.ExportSets(review.Empty(), REVIEW_SETS) == []


def test_a_removed_set_keeps_nothing_else_and_holds_no_result():
    # Set 1 removed (its confirmation, range and result go); its result is
    # free for set 2. Not removed is no change.
    checked = review.Check({"sets": {
        "1": {"removed": True, "confirmed": True, "start": 130.0,
              "set": None},
        "2": {"confirmed": True, "set": KAPPA},
        "3": {"removed": False},
    }}, REVIEW_SETS)
    assert checked == {"format": 1, "sets": {
        "1": {"removed": True}, "2": {"confirmed": True, "set": KAPPA}}}
    stored = review.Stored(checked, REVIEW_SETS)
    assert stored["sets"]["1"] == {"base": [120.0, 720.0], "removed": True}
    assert review.Normalize(json.loads(json.dumps(stored)),
                            REVIEW_SETS) == checked
    # Never exported, left out of the titles' preview.
    assert [item["no"] for item in review.ExportSets(checked, REVIEW_SETS)] \
        == [2]
    assert [item["no"] for item in review.ReviewedSets(checked, REVIEW_SETS)] \
        == [2, 3]
    assert review.Assigned(checked, REVIEW_SETS["sets"][0]) is None

    base1, base2 = [120.0, 720.0], [780.0, 1380.0]

    def read(**entries):
        return review.Normalize({"format": 1, "sets": entries},
                                REVIEW_SETS)["sets"]

    # Read back as {"removed": true} alone, whatever else the file holds.
    assert read(**{"1": {"base": base1, "removed": True, "confirmed": True,
                         "end": 700.0, "set": UNUSED}}) == {
        "1": {"removed": True}}
    # A set removed against another sets.json is back, with its result: the
    # review's assignment of that result goes.
    assert read(**{"1": {"base": [100.0, 720.0], "removed": True},
                   "2": {"base": base2, "set": KAPPA, "confirmed": True}}) \
        == {"2": {"confirmed": True}}
    assert read(**{"1": {"base": base1, "removed": True},
                   "2": {"base": base2, "set": KAPPA}}) == {
        "1": {"removed": True}, "2": {"set": KAPPA}}
    # Only true removes.
    assert read(**{"3": {"base": [1400.0, 2000.0], "removed": "yes",
                         "confirmed": True}}) == {"3": {"confirmed": True}}

    # What else a removed set's entry gives is not checked: its range is
    # never cut -- a block the worker found shorter than MIN_CLIP is removed
    # too.
    short = dict(REVIEW_SETS, sets=REVIEW_SETS["sets"] + [
        {"no": 4, "start": 2100.0, "end": 2100.5, "set": None}])
    assert review.Check({"sets": {"4": {"removed": True},
                                  "1": {"removed": True, "start": 719.5,
                                        "set": {"p1": "gone"}}}},
                        short)["sets"] == {"1": {"removed": True},
                                           "4": {"removed": True}}
    assert review.Normalize({"format": 1, "sets": {"4": {
        "base": [2100.0, 2100.5], "removed": True}}}, short)["sets"] == {
        "4": {"removed": True}}


DONE_ID = "20261116-120000-ab12"


def _done(work, tmp_path, sets=None, **record):
    """A finished job on a local video (vod.mp4) with `sets` as its
    out/sets.json and `record`'s fields in its job.json -> the video."""
    video = tmp_path / "vod.mp4"
    video.write_bytes(b"video")
    _sets(work, DONE_ID, jobSource=str(video),
          **dict(sets or REVIEW_SETS, source=str(video)))
    if record:
        _stored(work, DONE_ID, source=str(video), state="done", **record)
    return video


def _export_ended(service, jobId):
    return _until(lambda: (lambda export: export if export
                           and export["state"] != "running" else None)(
        service.Job(jobId)[0]["export"]), f"job {jobId}'s export to end")


def _refused(code, call, *args):
    with pytest.raises(ArchiveError) as refused:
        call(*args)
    assert refused.value.code == code
    return refused.value.message


def test_a_review_is_saved_whole_and_read_back(fake, work, tmp_path):
    service = ArchiveJobService()
    _done(work, tmp_path)
    assert service.Review(DONE_ID) == {"format": 1, "sets": {}}
    saved = service.SaveReview(DONE_ID, {"sets": {
        "1": {"confirmed": True}, "2": {"set": UNUSED}}})
    assert saved == {"format": 1, "sets": {
        "1": {"confirmed": True}, "2": {"set": UNUSED}}}
    assert json.loads((work / DONE_ID / "review.json").read_text(
        encoding="utf-8")) == {"format": 1, "sets": {
            "1": {"base": [120.0, 720.0], "confirmed": True},
            "2": {"base": [780.0, 1380.0], "set": UNUSED}}}
    assert service.Review(DONE_ID) == saved
    # A save replaces the whole review; nothing is left behind.
    assert service.SaveReview(DONE_ID, {"sets": {"3": {"confirmed": True}}})[
        "sets"] == {"3": {"confirmed": True}}
    assert service.Review(DONE_ID)["sets"] == {"3": {"confirmed": True}}
    assert sorted(p.name for p in (work / DONE_ID).iterdir()) == [
        "job.json", "out", "review.json"]
    # A refused save writes nothing.
    message = _refused(archive.INVALID, service.SaveReview, DONE_ID,
                       {"sets": {"1": {"start": 719.9}}})
    assert "1 秒以上" in message
    assert service.Review(DONE_ID)["sets"] == {"3": {"confirmed": True}}


def test_a_removed_set_is_saved_and_never_exported(fake, work, tmp_path):
    service = ArchiveJobService()
    _done(work, tmp_path)
    saved = service.SaveReview(DONE_ID, {"sets": {
        "1": {"removed": True, "confirmed": True}}})
    assert saved == {"format": 1, "sets": {"1": {"removed": True}}}
    assert json.loads((work / DONE_ID / "review.json").read_text(
        encoding="utf-8")) == {"format": 1, "sets": {
            "1": {"base": [120.0, 720.0], "removed": True}}}
    assert service.Review(DONE_ID) == saved
    # Nothing confirmed but a removed set: nothing to export.
    assert "確定したセットがありません" in _refused(
        archive.INVALID, service.StartExport, DONE_ID)


@pytest.mark.parametrize("content", [
    b"{not json", b"\xff\xfe", json.dumps({"format": 2, "sets": {
        "1": {"base": [120.0, 720.0], "confirmed": True}}}).encode(),
    json.dumps([1, 2]).encode()])
def test_a_broken_review_is_none_and_the_next_save_replaces_it(
        fake, work, tmp_path, content):
    service = ArchiveJobService()
    _done(work, tmp_path)
    (work / DONE_ID / "review.json").write_bytes(content)
    assert service.Review(DONE_ID) == {"format": 1, "sets": {}}
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    assert service.Review(DONE_ID)["sets"] == {"1": {"confirmed": True}}


def test_a_review_or_sets_that_cannot_be_read_is_refused_not_empty(
        fake, work, tmp_path):
    # A file that is there but cannot be read (another app holds it, no
    # permission) is not "no review": the page would show none and its next
    # save would write over the operator's review.
    service = ArchiveJobService()
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    reviewFile = work / DONE_ID / "review.json"
    kept = reviewFile.read_bytes()
    reviewFile.unlink()
    reviewFile.mkdir()                  # opening it fails, not as missing
    assert "review.json" in _refused(archive.UNREADABLE, service.Review,
                                     DONE_ID)
    _refused(archive.UNREADABLE, service.StartExport, DONE_ID)
    reviewFile.rmdir()
    reviewFile.write_bytes(kept)

    # A done job's sets.json: missing, unreadable or not JSON.
    setsFile = work / DONE_ID / "out" / "sets.json"
    sets = setsFile.read_bytes()
    for broken in ("missing", "unreadable", "not json"):
        setsFile.unlink()
        if broken == "unreadable":
            setsFile.mkdir()
        elif broken == "not json":
            setsFile.write_text("{x", encoding="utf-8")
        for call, args in ((service.Review, ()),
                           (service.SaveReview, ({"sets": {}},)),
                           (service.StartExport, ())):
            message = _refused(archive.UNREADABLE, call, DONE_ID, *args)
            assert "sets.json" in message, broken
        if setsFile.is_dir():
            setsFile.rmdir()
        setsFile.write_bytes(sets)
    assert service.Review(DONE_ID)["sets"] == {"1": {"confirmed": True}}
    assert reviewFile.read_bytes() == kept


def test_the_page_is_told_a_review_cannot_be_read(page, service, work,
                                                  tmp_path):
    _done(work, tmp_path)
    (work / DONE_ID / "review.json").mkdir()
    message = _refusal(page.get(f"{BASE}/jobs/{DONE_ID}/review"), 409,
                       "CONFLICT", archive.UNREADABLE)
    assert "読み直して" in message


def test_a_review_made_against_another_sets_json_is_dropped(fake, work,
                                                           tmp_path):
    service = ArchiveJobService()
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True},
                                          "2": {"confirmed": True}}})
    moved = dict(REVIEW_SETS, sets=[dict(REVIEW_SETS["sets"][0], end=730.0),
                                    REVIEW_SETS["sets"][1]])
    _done(work, tmp_path, moved)
    assert service.Review(DONE_ID)["sets"] == {"2": {"confirmed": True}}


def test_a_job_not_done_has_no_review(service, work, fake, tmp_path):
    _stored(work, DONE_ID, state="cancelled", source=SOURCE)
    assert service.Review(DONE_ID) == {"format": 1, "sets": {}}
    _refused(archive.NOT_DONE, service.SaveReview, DONE_ID, {"sets": {}})
    _refused(archive.NOT_FOUND, service.Review, "20261116-000000-zzzz")
    _refused(archive.NOT_FOUND, service.SaveReview, "nope", {"sets": {}})
    # While its analysis runs: no review, no save.
    _mode(fake, "slow")
    job = service.Create(SOURCE)
    assert service.Review(job["id"]) == {"format": 1, "sets": {}}
    _refused(archive.BUSY, service.SaveReview, job["id"], {"sets": {}})
    service.Cancel(job["id"])
    _ended(service, job["id"])


def test_an_export_cuts_the_confirmed_sets(service, work, fake, tmp_path):
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))
    video = _done(work, tmp_path)
    jobDir = work / DONE_ID
    (jobDir / "results.json").write_text(json.dumps(
        {"sets": [KAPPA], "bracket_url": "https://start.gg/x",
         "date": "2026-11-16"}), encoding="utf-8")
    service.SaveReview(DONE_ID, {"sets": {
        "1": {"confirmed": True},
        "2": {"confirmed": True, "start": 775.5, "end": 1390.25,
              "set": UNUSED},
        "3": {"set": None}}})

    started = service.StartExport(DONE_ID)
    assert started["state"] == "done"
    assert started["export"]["state"] == "running"
    assert started["export"]["files"] is None
    export = _export_ended(service, DONE_ID)

    assert json.loads(argv.read_text(encoding="utf-8")) == [
        str(jobDir / "export_request.json"), "--out", str(jobDir / "clips"),
        "--progress", "json", "--cancel-on-stdin"]
    assert json.loads((jobDir / "export_request.json").read_text(
        encoding="utf-8")) == {
        "source": str(video), "source_id": "fakevideo01",
        "event": "Fake Cup", "bracket_url": "https://start.gg/x",
        "date": "2026-11-16", "sets": [
            {"no": 1, "start": 120.0, "end": 720.0, "title": None,
             "description": None, "set": KAPPA,
             "game_starts": [130.0, 300.0]},
            {"no": 2, "start": 775.5, "end": 1390.25, "title": None,
             "description": None, "set": UNUSED, "game_starts": [790.0]}]}
    assert export["state"] == "done" and export["exit_code"] == 0
    assert (export["clips"], export["skipped"]) == (2, 0)
    assert export["out_dir"] == str(jobDir / "clips")
    assert export["error"] is None and export["started_at"] \
        and export["finished_at"]
    assert export["progress"]["stage"] == "export"
    assert export["files"] == [
        {"no": 1, "file": "01_Fake_Cup_Winners_Final_Kappa_vs_Lambda.mp4",
         "title": "Fake Cup Winners Final Kappa vs Lambda", "start": 120.0,
         "end": 720.0, "exported": True, "skipped": False},
        {"no": 2, "file": "02_Fake_Cup_Losers_Final_Mu_vs_Lambda.mp4",
         "title": "Fake Cup Losers Final Mu vs Lambda", "start": 775.5,
         "end": 1390.25, "exported": True, "skipped": False}]
    assert (jobDir / "clips" / export["files"][0]["file"]).read_bytes() \
        == fake_worker.CLIP_BYTES
    # The job is its analysis as it was, its export beside it.
    job, sets = service.Job(DONE_ID)
    assert job["state"] == "done" and sets["sets"][0]["no"] == 1
    disk = _disk(work, DONE_ID)
    assert disk["state"] == "done" and disk["export"]["state"] == "done"
    assert service.Status()["running_job"] is None
    assert "export start" in (jobDir / "worker.log").read_text(
        encoding="utf-8")

    # Again: the worker keeps what it cut from the same range.
    service.StartExport(DONE_ID)
    again = _export_ended(service, DONE_ID)
    assert (again["state"], again["clips"], again["skipped"]) == (
        "done", 2, 2)


def test_an_export_needs_a_done_job_a_confirmed_set_and_its_video(
        work, fake, tmp_path):
    asked = []
    service = ArchiveJobService(probe=lambda: asked.append(1))
    video = _done(work, tmp_path)
    message = _refused(archive.INVALID, service.StartExport, DONE_ID)
    assert "確定したセットがありません" in message
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    video.unlink()
    message = _refused(archive.SOURCE_UNUSABLE, service.StartExport, DONE_ID)
    assert "元動画が見つからない" in message
    _stored(work, DONE_ID, state="error", source=str(video))
    _refused(archive.NOT_DONE, service.StartExport, DONE_ID)
    _refused(archive.NOT_FOUND, service.StartExport, "20261116-000000-zzzz")
    # Refused before the worker was asked about, and nothing was written.
    assert asked == []
    assert _disk(work, DONE_ID).get("export") is None
    assert not (work / DONE_ID / "export_request.json").exists()


def test_a_url_jobs_export_cuts_its_download(service, work, fake):
    job = service.Create(SOURCE)
    _ended(service, job["id"])
    service.SaveReview(job["id"], {"sets": {"1": {"confirmed": True}}})
    service.StartExport(job["id"])
    assert _export_ended(service, job["id"])["state"] == "done"
    request = json.loads((work / job["id"] / "export_request.json")
                         .read_text(encoding="utf-8"))
    assert request["source"] == str(work / "downloads" / "fakevideo01.mp4")
    assert [row["no"] for row in request["sets"]] == [1]


def test_an_export_and_an_analysis_run_one_at_a_time(
        service, work, fake, tmp_path):
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    _mode(fake, "slow")
    service.StartExport(DONE_ID)
    _until(lambda: (service.Job(DONE_ID)[0]["export"] or {}).get("progress"),
           "the export's progress")
    for refusal in (lambda: service.Create(SOURCE),
                    lambda: service.StartExport(DONE_ID),
                    lambda: service.Resume(DONE_ID),
                    lambda: service.SetWorkDir(str(work / "other"))):
        with pytest.raises(ArchiveError) as refused:
            refusal()
        assert refused.value.code == archive.BUSY
    assert "書き出し" in _refused(archive.BUSY, service.Create, SOURCE)
    # The sets, the video and the review go on while it exports: it only
    # reads them.
    job, sets = service.Job(DONE_ID)
    assert job["export"]["state"] == "running" and sets is not None
    assert service.VideoFile(DONE_ID)[0] == str(tmp_path / "vod.mp4")
    service.SaveReview(DONE_ID, {"sets": {"2": {"confirmed": True}}})

    asked = service.Cancel(DONE_ID)
    assert asked["export"]["state"] == "running"
    export = _export_ended(service, DONE_ID)
    assert export["state"] == "cancelled" and export["exit_code"] == 130
    assert _disk(work, DONE_ID)["export"]["state"] == "cancelled"
    _refused(archive.NOT_RUNNING, service.Cancel, DONE_ID)

    # An analysis now runs, and the export waits for it (in its words).
    job = service.Create(SOURCE)
    assert service.Status()["running_kind"] == archive.ANALYSIS
    message = _refused(archive.BUSY, service.StartExport, DONE_ID)
    assert message == ("切り抜きジョブの解析が実行中です。"
                       "終わるか中止してから書き出してください。")
    service.Cancel(job["id"])
    _ended(service, job["id"])


def test_a_cancelled_export_goes_on_where_it_stopped(
        service, work, fake, tmp_path):
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True},
                                          "3": {"confirmed": True}}})
    # Long between the clips: cancelled after the first, whatever the load.
    fake.setenv("FAKE_VODSPLIT_DELAY", "60")
    service.StartExport(DONE_ID)
    _until(lambda: ((service.Job(DONE_ID)[0]["export"] or {}).get(
        "progress") or {}).get("fraction") == 0.5, "the first clip")
    service.Cancel(DONE_ID)
    assert _export_ended(service, DONE_ID)["state"] == "cancelled"

    fake.setenv("FAKE_VODSPLIT_DELAY", "0")
    service.StartExport(DONE_ID)
    export = _export_ended(service, DONE_ID)
    assert (export["state"], export["clips"], export["skipped"]) == (
        "done", 2, 1)
    assert [(f["no"], f["skipped"]) for f in export["files"]] == [
        (1, True), (3, False)]


def test_the_shutdown_cancels_a_running_export(service, work, fake,
                                               tmp_path):
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    _mode(fake, "slow")
    service.StartExport(DONE_ID)
    _until(lambda: (service.Job(DONE_ID)[0]["export"] or {}).get("progress"),
           "the export's progress")
    started = time.monotonic()
    service.Shutdown(5.0)
    assert time.monotonic() - started < 5.0
    disk = _disk(work, DONE_ID)
    assert disk["state"] == "done" and disk["export"]["state"] == "cancelled"
    _refused(archive.STOPPING, service.StartExport, DONE_ID)


@pytest.mark.parametrize("mode, state, code, says", [
    ("crash", "failed", "crashed", "確定分を書き出し"),
    ("error", "error", "source", "移動・削除"),
    # The earlier clip open in a player: vodsplit cannot replace it.
    ("locked", "failed", "internal", "閉じてから"),
])
def test_an_export_that_fails_says_how_to_go_on(
        service, work, fake, tmp_path, mode, state, code, says):
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    _mode(fake, mode)
    service.StartExport(DONE_ID)
    export = _export_ended(service, DONE_ID)
    assert export["state"] == state and export["error"]["code"] == code
    assert says in (export["error"]["hint"] or "")
    assert service.Job(DONE_ID)[0]["state"] == "done"


def _vodsplit_export():
    """vodsplit's export module (the worker's own validate_edits):
    installed, else the SmashVodSplitter checkout beside this repository;
    None when there is neither (CI)."""
    try:
        from vodsplit import export
        return export
    except ImportError:
        pass
    checkout = Path(__file__).resolve().parents[2] / "SmashVodSplitter"
    if not (checkout / "vodsplit" / "export.py").is_file():
        return None
    sys.path.insert(0, str(checkout))
    try:
        from vodsplit import export
        return export
    except ImportError:
        return None
    finally:
        sys.path.remove(str(checkout))


def test_the_export_request_passes_the_workers_own_check(fake, work,
                                                        tmp_path):
    # The request Autona writes is what vodsplit's `export` reads: its
    # validate_edits takes it as it is (the shapes, the results, the ranges
    # at the boundary). Skipped where vodsplit is not there (CI).
    export = _vodsplit_export()
    if export is None:
        pytest.skip("vodsplit is not installed nor checked out beside")
    service = ArchiveJobService()
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {
        "1": {"confirmed": True, "start": 0.13, "end": 1.14},
        "2": {"confirmed": True, "set": UNUSED, "start": 775.5},
        "3": {"confirmed": True, "set": None, "start": 3598.95,
              "end": 3700}}})
    request = service._ExportRequest(
        service.WorkDir(), str(work / DONE_ID), _disk(work, DONE_ID))
    checked = export.validate_edits(json.loads(json.dumps(request)))
    assert [(row["no"], row["start"], row["end"], row["set"])
            for row in checked["sets"]] == [
        (1, 0.13, 1.14, KAPPA), (2, 775.5, 1380.0, UNUSED),
        (3, 3598.95, 3700, None)]
    assert checked["source"] == request["source"]
    assert export.MIN_CLIP == review.MIN_CLIP
    # What Autona refuses, vodsplit refuses.
    with pytest.raises(export.EditsError):
        export.validate_edits(dict(request, sets=[dict(
            request["sets"][0], start=0.13, end=1.13)]))


def test_an_export_left_running_by_a_previous_run_is_cancelled(
        fake, work, tmp_path):
    _done(work, tmp_path, export={"state": "running", "logs": ["clip 1"],
                                  "started_at": "x"})
    service = ArchiveJobService()
    export = service.Job(DONE_ID)[0]["export"]
    assert export["state"] == "cancelled"
    assert export["logs"] == ["clip 1", archive.STALE_EXPORT_NOTE]
    disk = _disk(work, DONE_ID)
    assert disk["state"] == "done" and disk["export"]["state"] == "cancelled"


def test_the_page_reviews_and_exports_through_the_routes(
        page, service, work, fake, tmp_path):
    _done(work, tmp_path)
    url = f"{BASE}/jobs/{DONE_ID}"
    assert _data(page.get(f"{url}/review")) == {
        "review": {"format": 1, "sets": {}}}
    _refusal(page.post(f"{url}:export"), 400, "INVALID_ARGUMENT",
             archive.INVALID)
    saved = _data(page.put(f"{url}/review", json={"sets": {
        "1": {"confirmed": True, "end": 700}, "2": {"set": UNUSED}}}))
    assert saved == {"review": {"format": 1, "sets": {
        "1": {"confirmed": True, "end": 700}, "2": {"set": UNUSED}}}}
    assert _data(page.get(f"{url}/review")) == saved
    message = _refusal(page.put(f"{url}/review", json={"sets": {
        "2": {"set": KAPPA}}}), 400, "INVALID_ARGUMENT", archive.INVALID)
    assert "同じ結果" in message
    _refusal(page.put(f"{url}/review", data="not json"), 400,
             "INVALID_ARGUMENT", archive.INVALID)

    _mode(fake, "slow")
    job = _data(page.post(f"{url}:export"))["job"]
    assert job["export"]["state"] == "running"
    status = _data(page.get(f"{BASE}/status"))
    assert (status["running_job"], status["running_kind"]) == (
        DONE_ID, archive.EXPORT)
    _refusal(page.post(f"{url}:export"), 409, "CONFLICT", archive.BUSY)
    _data(page.post(f"{url}:cancel"))
    assert _export_ended(service, DONE_ID)["state"] == "cancelled"

    _mode(fake, "ok")
    _data(page.post(f"{url}:export"))
    detail = _until(lambda: (lambda data: data if data["job"]["export"][
        "state"] != "running" else None)(_data(page.get(url))),
        "the export to end")
    assert detail["job"]["export"]["state"] == "done"
    assert [clip["no"] for clip in detail["job"]["export"]["files"]] == [1]
    assert detail["sets"]["sets"][0]["no"] == 1

    other = f"{BASE}/jobs/20261116-000000-zzzz"
    for response in (page.get(f"{other}/review"),
                     page.put(f"{other}/review", json={"sets": {}}),
                     page.post(f"{other}:export")):
        _refusal(response, 404, "NOT_FOUND", archive.NOT_FOUND)
    _stored(work, DONE_ID, state="cancelled", source=SOURCE)
    _refusal(page.put(f"{url}/review", json={"sets": {}}), 409, "CONFLICT",
             archive.NOT_DONE)
    _refusal(page.post(f"{url}:export"), 409, "CONFLICT", archive.NOT_DONE)


# ── the reanalysis: results given to a job already analysed ─────────────

# What a reanalysis removes from a job's folder (the run makes it again, or
# it was made from the sets it replaces) and what it leaves.
DERIVED = ["out/03_Old_Cup_A_vs_B_bg.jpg", "out/01_Old_Cup_A_vs_B.txt",
           "thumbs/1.png", "thumbs/thumbs.json"]
KEPT = ["out/sets.json", "out/.cache/p1.npy", "out/.cache/meta.json",
        "out/notes.md", "out/02_Cut.txt", "out/02_Cut.mp4",
        "clips/01_Old_Cup_A_vs_B.mp4", "clips/01_Old_Cup_A_vs_B.png",
        "clips/01_Old_Cup_A_vs_B.txt", "clips/export.json",
        "export_request.json", "review.json"]


def _derived(work, jobId):
    """DERIVED and KEPT in job `jobId`'s folder (the ones not there yet)."""
    for name in DERIVED + KEPT:
        path = work / jobId / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")


def _there(work, jobId, names):
    return [name for name in names if (work / jobId / name).exists()]


def test_a_done_job_is_analysed_again_with_results(service, work, fake,
                                                   tmp_path):
    # Made without results: the sets are split by the text on screen.
    argv = tmp_path / "argv.json"
    fake.setenv("FAKE_VODSPLIT_ARGV", str(argv))
    jobId = service.Create(SOURCE)["id"]
    assert _ended(service, jobId)["has_results"] is False
    jobDir = work / jobId
    (jobDir / "review.json").write_text(json.dumps({"format": 1, "sets": {
        "1": {"base": [120.0, 720.0], "confirmed": True},
        # Made against a set whose range the new split moves.
        "2": {"base": [700.0, 1380.0], "confirmed": True}}}),
        encoding="utf-8")
    _derived(work, jobId)
    service._thumbsLast[jobId] = {"state": "done", "done": 1, "total": 1,
                                  "failed": []}
    argv.unlink()

    job = service.Reanalyze(jobId, RESULTS)
    assert job["state"] == "running" and job["has_results"] is True
    done = _ended(service, jobId)
    assert done["state"] == "done" and done["has_results"] is True
    assert (done["sets"], done["error"]) == (2, None)
    assert _disk(work, jobId)["has_results"] is True
    assert json.loads((jobDir / "results.json").read_text(
        encoding="utf-8")) == RESULTS
    # Resume's command, --results naming them.
    assert json.loads(argv.read_text(encoding="utf-8")) == [
        SOURCE, "--out", str(jobDir / "out"),
        "--download-dir", str(work / "downloads"), "--codec", "any",
        "--results", str(jobDir / "results.json"),
        "--no-cut", "--thumbs", "--progress", "json", "--cancel-on-stdin"]
    sets = service.Job(jobId)[1]
    assert [row["set"]["p1"] for row in sets["sets"]] == ["Kappa", "Kappa"]
    assert _there(work, jobId, DERIVED) == []
    assert _there(work, jobId, KEPT) == KEPT
    # The run wrote its frames again.
    assert [row["thumb"] for row in sets["sets"]] == [
        "01_fake_bg.jpg", "02_fake_bg.jpg"]
    assert _there(work, jobId, ["out/01_fake_bg.jpg", "out/02_fake_bg.jpg"]) \
        == ["out/01_fake_bg.jpg", "out/02_fake_bg.jpg"]
    # The review keeps what still fits the new sets.
    assert service.Review(jobId)["sets"] == {"1": {"confirmed": True}}
    assert done["thumbs"]["state"] is None
    assert service.Jobs()[0]["state"] == "done"


def test_a_reanalysis_stopped_midway_resumes_with_its_results(
        service, work, fake, tmp_path):
    jobId = service.Create(SOURCE)["id"]
    _ended(service, jobId)
    # A set removed whose range the new split keeps.
    (work / jobId / "review.json").write_text(json.dumps({"format": 1,
        "sets": {"2": {"base": [780.0, 1380.0], "removed": True}}}),
        encoding="utf-8")
    _derived(work, jobId)
    frames = ["out/01_fake_bg.jpg", "out/02_fake_bg.jpg"]
    assert _there(work, jobId, frames) == frames

    _mode(fake, "slow")
    service.Reanalyze(jobId, RESULTS)
    _until(lambda: (service.Job(jobId)[0]["progress"] or {}).get("eta"),
           "a progress line")
    # Removed before the worker started; no sets while it runs.
    assert _there(work, jobId, DERIVED + frames) == []
    assert _there(work, jobId, KEPT) == KEPT
    assert service.Status()["running_job"] == jobId
    job, sets = service.Job(jobId)
    assert sets is None and job["analysis_cached"] is False
    assert service.Review(jobId) == {"format": 1, "sets": {}}
    _refused(archive.BUSY, service.SaveReview, jobId, {"sets": {}})
    service.Cancel(jobId)
    stopped = _ended(service, jobId)
    assert stopped["state"] == "cancelled" and stopped["has_results"] is True

    # The results are the job's now: a resume runs with them.
    _mode(fake, "ok")
    service.Resume(jobId)
    assert _ended(service, jobId)["state"] == "done"
    assert service.Job(jobId)[1]["sets"][0]["set"]["p1"] == "Kappa"
    # Still removed (its range did not move), holding no result: the one
    # the worker gave it now is free for another set.
    reviewed = service.Review(jobId)
    assert reviewed["sets"] == {"2": {"removed": True}}
    rows = service.Job(jobId)[1]["sets"]
    assert review.Assigned(reviewed, rows[1]) is None
    assert [item["no"] for item in review.ReviewedSets(
        reviewed, service.Job(jobId)[1])] == [1]


@pytest.mark.parametrize("state", ["done", "error", "cancelled", "failed"])
def test_any_job_that_does_not_run_is_analysed_again(service, work, fake,
                                                     state):
    # Resume's refusal of a done job is not the reanalysis': the results
    # are what it runs for.
    jobId = "20261101-120000-ab12"
    _stored(work, jobId, source=SOURCE, state=state)
    assert service.Reanalyze(jobId, RESULTS)["state"] == "running"
    done = _ended(service, jobId)
    assert done["state"] == "done" and done["has_results"] is True
    assert service.Job(jobId)[1]["sets"][0]["set"]["p1"] == "Kappa"


def test_what_a_reanalysis_refuses_changes_nothing(service, work, fake,
                                                   tmp_path):
    jobId = service.Create(SOURCE)["id"]
    _ended(service, jobId)
    jobDir = work / jobId
    _derived(work, jobId)
    before = {name: (jobDir / name).read_bytes() for name in DERIVED + KEPT}
    record = (jobDir / "job.json").read_bytes()

    # Results as Create checks them; none at all is refused.
    for results, says in ((None, "結果データを指定"),
                          # Not Create's "leave the results out".
                          ({"sets": []}, "区切り直しに使う結果データにセットがありません"),
                          ({"sets": [{"p1": "A", "p2": "B", "s1": 1}]}, "s2"),
                          ("x", '"sets"')):
        assert says in _refused(archive.INVALID, service.Reanalyze, jobId,
                                results)
    for unknown in ("20261116-000000-zzzz", "nope"):
        _refused(archive.NOT_FOUND, service.Reanalyze, unknown, RESULTS)
    # Its thumbnails being drawn (from the frames it would remove).
    service._thumbRun = archive._ThumbRun(jobId, str(jobDir), str(work), [])
    assert "サムネイル" in _refused(archive.BUSY, service.Reanalyze, jobId,
                                RESULTS)
    service._thumbRun = None
    # While a job runs: another one.
    _mode(fake, "slow")
    other = service.Create(SOURCE)["id"]
    _refused(archive.BUSY, service.Reanalyze, jobId, RESULTS)
    _refused(archive.BUSY, service.Reanalyze, other, RESULTS)
    service.Cancel(other)
    _ended(service, other)
    # A source a start would refuse (job.json is the operator's to edit).
    stored = _disk(work, jobId)
    (jobDir / "job.json").write_text(json.dumps(
        dict(stored, source="\\\\fileserver\\share\\vod.mp4")),
        encoding="utf-8")
    assert _refused(archive.SOURCE_UNUSABLE, service.Reanalyze, jobId,
                    RESULTS).startswith(
        "このジョブの元動画を使えないため区切り直せません（")
    (jobDir / "job.json").write_bytes(record)

    assert not (jobDir / "results.json").exists()
    assert {name: (jobDir / name).read_bytes()
            for name in DERIVED + KEPT} == before
    assert _disk(work, jobId)["has_results"] is False

    # While its export runs.
    _done(work, tmp_path)
    service.SaveReview(DONE_ID, {"sets": {"1": {"confirmed": True}}})
    service.StartExport(DONE_ID)
    _refused(archive.BUSY, service.Reanalyze, DONE_ID, RESULTS)
    service.Cancel(DONE_ID)
    assert _export_ended(service, DONE_ID)["state"] == "cancelled"
    assert not (work / DONE_ID / "results.json").exists()


@pytest.mark.parametrize("failing", ["_WriteTemplates", "_WriteRecord"])
@pytest.mark.parametrize("had", [True, False])
def test_a_reanalysis_that_cannot_write_changes_nothing(
        service, work, fake, monkeypatch, failing, had):
    jobId = service.Create(SOURCE)["id"]
    _ended(service, jobId)
    jobDir = work / jobId
    _derived(work, jobId)
    if had:
        (jobDir / "results.json").write_text('{"sets": []}', encoding="utf-8")
    before = {name: (jobDir / name).read_bytes() for name in DERIVED + KEPT}
    record = (jobDir / "job.json").read_bytes()

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service, failing, broken)
    _refused(archive.WORK_DIR_UNUSABLE, service.Reanalyze, jobId, RESULTS)

    # results.json as it was, the derived files still there, no leftover.
    assert (jobDir / "results.json").exists() is had
    if had:
        assert (jobDir / "results.json").read_text(
            encoding="utf-8") == '{"sets": []}'
    assert not (jobDir / "results.json.new").exists()
    assert not list(jobDir.glob("*.tmp"))
    assert {name: (jobDir / name).read_bytes()
            for name in DERIVED + KEPT} == before
    assert (jobDir / "job.json").read_bytes() == record
    assert service.Job(jobId)[0]["state"] == "done"


def test_whether_the_analysis_cache_would_be_used_is_told(service, work,
                                                          fake, tmp_path):
    jobId = service.Create(SOURCE)["id"]
    _ended(service, jobId)

    def cached(job=jobId):
        return service.Job(job)[0]["analysis_cached"]

    assert cached() is False                    # no out/.cache
    (work / jobId / "out" / ".cache").mkdir()
    assert cached() is True
    # The download gone (another download would be another file: the
    # worker keys its cache on the video's path, size and mtime).
    download = work / "downloads" / "fakevideo01.mp4"
    moved = download.with_name("elsewhere.mp4")
    download.rename(moved)
    assert cached() is False
    moved.rename(download)
    assert cached() is True
    # A download of the open codecs is not what a run downloads now.
    setsFile = work / jobId / "out" / "sets.json"
    kept = setsFile.read_text(encoding="utf-8")
    opened = work / "downloads" / "fakevideo01.open.mp4"
    opened.write_bytes(b"video")
    setsFile.write_text(json.dumps(dict(json.loads(kept),
                                        source=str(opened))),
                        encoding="utf-8")
    assert cached() is False
    setsFile.write_text(kept, encoding="utf-8")

    # A local file: while it is where job.json names it.
    video = tmp_path / "vod.mp4"
    video.write_bytes(b"video")
    local = "20261101-120000-ab12"
    _stored(work, local, source=str(video), state="done")
    (work / local / "out" / ".cache").mkdir(parents=True)
    assert cached(local) is True
    video.unlink()
    assert cached(local) is False

    # Not while its analysis runs.
    _mode(fake, "slow")
    service.Reanalyze(jobId, RESULTS)
    assert cached() is False
    service.Cancel(jobId)
    _ended(service, jobId)
    assert cached() is True


def test_the_page_analyses_a_job_again_through_the_route(page, service,
                                                         work, fake):
    jobId = _data(page.post(f"{BASE}/jobs", json={"source": SOURCE}),
                  201)["job"]["id"]
    assert _route_ended(page, jobId)["has_results"] is False
    url = f"{BASE}/jobs/{jobId}:reanalyze"
    assert "結果データ" in _refusal(page.post(url, json={}), 400,
                                 "INVALID_ARGUMENT", archive.INVALID)
    _refusal(page.post(url, json={"results": {"sets": []}}), 400,
             "INVALID_ARGUMENT", archive.INVALID)
    _refusal(page.post(url, data="not json"), 400, "INVALID_ARGUMENT",
             archive.INVALID)
    _refusal(page.post(f"{BASE}/jobs/20261116-000000-zzzz:reanalyze",
                       json={"results": RESULTS}), 404, "NOT_FOUND",
             archive.NOT_FOUND)
    big = {"results": {"sets": [], "pad": "x" * (2 * 1024 * 1024)}}
    response = page.post(url, json=big)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    job = _data(page.post(url, json={"results": RESULTS}))["job"]
    assert job["state"] == "running" and job["has_results"] is True
    done = _route_ended(page, jobId)
    assert done["state"] == "done" and done["has_results"] is True
    detail = _data(page.get(f"{BASE}/jobs/{jobId}"))
    assert detail["sets"]["sets"][0]["set"]["p1"] == "Kappa"
    assert detail["job"]["analysis_cached"] is False

    _mode(fake, "slow")
    _data(page.post(url, json={"results": RESULTS}))
    _refusal(page.post(url, json={"results": RESULTS}), 409, "CONFLICT",
             archive.BUSY)
    _data(page.post(f"{BASE}/jobs/{jobId}:cancel"))
    assert _route_ended(page, jobId)["state"] == "cancelled"


READS = [
    ("get", f"{BASE}/status", None),
    ("get", f"{BASE}/jobs", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz/video", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz/frames/1", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz/review", None),
    # The match log's days (A5).
    ("get", f"{BASE}/match-logs", None),
    # The title templates and a set's thumbnail (A6).
    ("get", f"{BASE}/templates", None),
    ("get", f"{BASE}/templates/default", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz/thumbs/1", None),
]
WRITES = [
    ("patch", f"{BASE}/settings", {"work_dir": "C:\\x"}),
    ("post", f"{BASE}/jobs", {"source": SOURCE}),
    ("post", f"{BASE}/jobs/20261116-000000-zzzz:cancel", None),
    ("post", f"{BASE}/jobs/20261116-000000-zzzz:resume", None),
    # A job's analysis run again with results: it starts the worker.
    ("post", f"{BASE}/jobs/20261116-000000-zzzz:reanalyze",
     {"results": RESULTS}),
    ("put", f"{BASE}/jobs/20261116-000000-zzzz/review", {"sets": {}}),
    ("post", f"{BASE}/jobs/20261116-000000-zzzz:export", None),
    # The results' preparation (A5): it reaches start.gg for the job.
    ("post", f"{BASE}/results:prepare", {"kind": "json", "results": RESULTS}),
    # The title templates' writes (they go to user_data/), the titles'
    # preview (it starts the worker) and the thumbnails' drawing (A6).
    ("put", f"{BASE}/templates/default", {"template": {}}),
    ("delete", f"{BASE}/templates/default", None),
    ("post", f"{BASE}/templates/default:duplicate", None),
    ("post", f"{BASE}/jobs/20261116-000000-zzzz/titles:preview",
     {"template": {}}),
    ("post", f"{BASE}/jobs/20261116-000000-zzzz/thumbs:generate", None),
]


def test_the_reads_are_this_pcs_and_the_writes_the_shells():
    endpoints = {"get_archive_status": WebAccess.THIS_PC,
                 "get_archive_jobs": WebAccess.THIS_PC,
                 "get_archive_job": WebAccess.THIS_PC,
                 "get_archive_video": WebAccess.THIS_PC,
                 "get_archive_frame": WebAccess.THIS_PC,
                 "get_archive_review": WebAccess.THIS_PC,
                 "get_archive_match_logs": WebAccess.THIS_PC,
                 "get_archive_templates": WebAccess.THIS_PC,
                 "get_archive_template": WebAccess.THIS_PC,
                 "get_archive_thumb": WebAccess.THIS_PC,
                 "get_archive_worker": WebAccess.THIS_PC,
                 "archive_settings_update": WebAccess.SHELL,
                 "archive_job_create": WebAccess.SHELL,
                 "archive_job_cancel": WebAccess.SHELL,
                 "archive_job_resume": WebAccess.SHELL,
                 "archive_job_reanalyze": WebAccess.SHELL,
                 "archive_review_update": WebAccess.SHELL,
                 "archive_job_export": WebAccess.SHELL,
                 "archive_results_prepare": WebAccess.SHELL,
                 "archive_template_update": WebAccess.SHELL,
                 "archive_template_delete": WebAccess.SHELL,
                 "archive_template_duplicate": WebAccess.SHELL,
                 "archive_titles_preview": WebAccess.SHELL,
                 "archive_thumbs_generate": WebAccess.SHELL,
                 "archive_worker_install": WebAccess.SHELL,
                 "archive_worker_cancel": WebAccess.SHELL}
    assert {name: WebAccess.ROUTE_ACCESS[f"api_v1.{name}"]
            for name in endpoints} == endpoints


@pytest.mark.parametrize("method, url, body", READS + WRITES)
@pytest.mark.parametrize("withToken", [False, True])
def test_another_computer_cannot_use_the_archive_even_with_the_token(
        server, service, work, shellSecret, method, url, body,
        withToken):  # noqa: F811
    headers = _shell_headers(shellSecret)
    if withToken:
        headers[WebAccess.TOKEN_HEADER] = WebAccess.Token()
    response = getattr(_client(server, LAN_PEER), method)(
        url, json=body, headers=headers)
    assert response.status_code == 403
    refusal = (WebAccess.SHELL_ONLY if (method, url, body) in WRITES
               else WebAccess.THIS_PC_ONLY)
    assert response.get_json() == WebAccess.ErrorBody(refusal)
    assert service.Jobs() == [] and not work.exists()
    assert not os.path.exists(templates.DIRECTORY)


@pytest.mark.parametrize("method, url, body", WRITES)
@pytest.mark.parametrize("headers", [
    # This PC's own request without the shell's secret: a plain browser,
    # a layout/ page (in OBS too), the headless app's page.
    {"Sec-Fetch-Site": "same-origin"},
    {},
    # The secret, but not a fetch of this origin's own page.
    {"Sec-Fetch-Site": "none", WebAccess.SHELL_HEADER: None},
    {WebAccess.SHELL_HEADER: None},
    # Not the secret.
    {"Sec-Fetch-Site": "same-origin", WebAccess.SHELL_HEADER: "x" * 43},
])
@pytest.mark.parametrize("withToken", [False, True])
def test_only_the_shells_own_request_writes(
        server, service, work, shellSecret, method, url, body, headers,
        withToken):  # noqa: F811
    sent = {name: (shellSecret if value is None else value)
            for name, value in headers.items()}
    if withToken:
        sent[WebAccess.TOKEN_HEADER] = WebAccess.Token()
    response = getattr(_client(server), method)(url, json=body,
                                                headers=sent)
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.SHELL_ONLY)
    assert service.Jobs() == [] and not work.exists()
    assert not os.path.exists(templates.DIRECTORY)


@pytest.mark.parametrize("method, url, body", WRITES)
def test_a_shell_that_never_issued_the_secret_writes_nothing(
        server, service, work, monkeypatch, method, url, body):  # noqa: F811
    # Headless: nothing issues the secret, so no job starts (the page reads
    # the status and the jobs, but cannot start one).
    monkeypatch.setattr(WebAccess, "_shellSecret", None)
    response = getattr(_client(server), method)(
        url, json=body, headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 403
    assert response.get_json() == WebAccess.ErrorBody(WebAccess.SHELL_ONLY)
    assert service.Jobs() == [] and not work.exists()
    assert not os.path.exists(templates.DIRECTORY)


@pytest.mark.parametrize("method, url, body", READS)
def test_this_pcs_own_request_reads_without_the_secret(
        server, service, method, url, body):  # noqa: F811
    response = getattr(_client(server), method)(url)
    assert response.status_code in (200, 404)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("method, url, body", READS + WRITES)
def test_a_foreign_page_cannot_use_the_archive(
        server, service, work, shellSecret, method, url, body):  # noqa: F811
    response = getattr(_client(server), method)(
        url, json=body,
        headers=_shell_headers(shellSecret, Origin="https://evil.example"))
    assert response.status_code == 403
    assert service.Jobs() == []
