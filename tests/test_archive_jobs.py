"""The archive clipper's jobs (ADR-006 A1): core/ArchiveWorker.py (where the
worker is, its version, how it is started), core/ArchiveJobService.py (one
job at a time, its progress, its end state, job.json) and the v1 routes the
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
                 "FAKE_VODSPLIT_CHILD_PID", "PYINSTALLER_RESET_ENVIRONMENT"):
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


def test_the_probe_and_the_run_reset_pyinstallers_environment(fake,
                                                              monkeypatch):
    # A frozen Autona starting a frozen worker must not hand it its _PYI_*
    # state: both the probe and the run ask PyInstaller to reset it.
    given = []

    def run(*_args, **kwargs):
        given.append(("probe", kwargs["env"]))
        raise OSError("not started in this test")

    def popen(*_args, **kwargs):
        given.append(("run", kwargs["env"]))
        raise OSError("not started in this test")

    monkeypatch.setattr(worker.subprocess, "run", run)
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

    def run(*_args, **kwargs):
        given.append(("probe", kwargs["creationflags"]))
        raise OSError("not started in this test")

    def popen(*_args, **kwargs):
        given.append(("run", kwargs["creationflags"]))
        raise OSError("not started in this test")

    monkeypatch.setenv(worker.ENV_COMMAND, COMMAND)
    monkeypatch.setattr(worker.subprocess, "run", run)
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
        "has_results"}
    assert set(done["progress"]) == {
        "stage", "label", "index", "count", "fraction", "overall", "eta"}

    _job, sets = service.Job(job["id"])
    assert [row["no"] for row in sets["sets"]] == [1, 2]
    assert _disk(work, job["id"])["state"] == "done"
    assert service.Status()["running_job"] is None
    assert json.loads(argv.read_text(encoding="utf-8")) == [
        SOURCE, "--out", str(jobDir / "out"),
        "--download-dir", str(work / "downloads"),
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


@pytest.fixture
def page(server, shellSecret):  # noqa: F811
    return _Page(server, shellSecret)


def test_the_page_runs_a_job_through_the_routes(page, service, work):
    status = _data(page.get(f"{BASE}/status"))
    assert status == {"available": True, "version": "0.0.0-fake",
                      "format": 1, "reason": None,
                      "work_dir": os.path.normpath(str(work)),
                      "running_job": None}

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
    assert _data(page.get(f"{BASE}/status"))["running_job"] == jobId
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
    downloaded = work / "downloads" / "fakevideo01.open.mp4"
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


def test_the_download_is_found_after_the_work_folder_moved(fake, work):
    jobId = "20261116-100000-move"
    _sets(work, jobId, source="C:\\old\\work\\downloads\\vid.open.mp4")
    (work / "downloads").mkdir()
    (work / "downloads" / "vid.open.mp4").write_bytes(b"v")
    path, kind = ArchiveJobService().VideoFile(jobId)
    assert path == str(work / "downloads" / "vid.open.mp4")
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


READS = [
    ("get", f"{BASE}/status", None),
    ("get", f"{BASE}/jobs", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz/video", None),
    ("get", f"{BASE}/jobs/20261116-000000-zzzz/frames/1", None),
]
WRITES = [
    ("patch", f"{BASE}/settings", {"work_dir": "C:\\x"}),
    ("post", f"{BASE}/jobs", {"source": SOURCE}),
    ("post", f"{BASE}/jobs/20261116-000000-zzzz:cancel", None),
    ("post", f"{BASE}/jobs/20261116-000000-zzzz:resume", None),
]


def test_the_reads_are_this_pcs_and_the_writes_the_shells():
    endpoints = {"get_archive_status": WebAccess.THIS_PC,
                 "get_archive_jobs": WebAccess.THIS_PC,
                 "get_archive_job": WebAccess.THIS_PC,
                 "get_archive_video": WebAccess.THIS_PC,
                 "get_archive_frame": WebAccess.THIS_PC,
                 "archive_settings_update": WebAccess.SHELL,
                 "archive_job_create": WebAccess.SHELL,
                 "archive_job_cancel": WebAccess.SHELL,
                 "archive_job_resume": WebAccess.SHELL}
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
