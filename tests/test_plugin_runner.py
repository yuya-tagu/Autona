"""PluginRunner (ADR-009 D6, appendix A / E; lane L1) against
docs/plugins/contract_1.json and the fake worker
(tests/fixtures/fake_plugin_worker.py): the end table in every
combination, the environment's subtraction, the events' rules, worker.log,
the cancel and the time limit, the job object (grandchildren end with it,
the priority limit reaches the descendants), describe's facts, Shutdown.

The process tests are Windows only (the host platform); the pure ones run
everywhere. Waits that end by the worker's own cooperation get a long
grace (a loaded machine must not make them fail); only the hang tests,
which need the kill, get a short one.
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.core import PluginRunner as R
from src.core import PluginTypes as T

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "plugins" / "contract_1.json")
                      .read_text(encoding="utf-8"))
FAKE = ROOT / "tests" / "fixtures" / "fake_plugin_worker.py"
WINDOWS = sys.platform == "win32"
windows = pytest.mark.skipif(not WINDOWS, reason="the host platform only")
OTHER_EXITS = (1, 3, 255, 3221225477, -9, None)
# How long a test waits for a process before it calls it stuck: a bound
# for failing, not a pace. The runs are BELOW_NORMAL, so a loaded machine
# (NORMAL work on every core) can hold them back for minutes.
LONG = 600.0


# ── EndState against the contract's table ──────────────────────────────

def _final(name):
    return None if name == "none" else T.EventType(name)


def _exits(name):
    return OTHER_EXITS if name == "other" else (int(name),)


@pytest.mark.parametrize("row", CONTRACT["end"]["table"],
                         ids=lambda row: f"{row['final']}-{row['exit']}")
def test_the_end_table_in_every_combination(row):
    for exitCode in _exits(row["exit"]):
        for cancel in (False, True):
            expected = row.get("after_cancel_request", row["state"]) \
                if cancel else row["state"]
            for memory in (False, True):
                state, reason = R.EndState(
                    _final(row["final"]), exitCode, cancelRequested=cancel,
                    timedOut=False, launchFailed=False, entryMissing=False,
                    memoryLimitHit=memory)
                assert state == expected, (row, exitCode, cancel)
                if state == "failed":
                    assert reason == ("memory_limit" if memory
                                      else "abnormal_exit")
                else:
                    assert reason is None


def test_the_end_table_covers_every_final_and_exit():
    rows = {(row["final"], row["exit"]) for row in CONTRACT["end"]["table"]}
    assert rows == {(final, exit_) for final in
                    ("done", "error", "cancelled", "none")
                    for exit_ in ("0", "2", "130", "other")}


@pytest.mark.parametrize("row", CONTRACT["end"]["table"],
                         ids=lambda row: f"{row['final']}-{row['exit']}")
def test_timed_out_and_launch_failure_come_before_the_table(row):
    for exitCode in _exits(row["exit"]):
        for cancel in (False, True):
            assert R.EndState(
                _final(row["final"]), exitCode, cancelRequested=cancel,
                timedOut=True, launchFailed=False, entryMissing=False,
                memoryLimitHit=True) == ("failed", "timed_out")
            for missing, reason in ((False, "launch_failed"),
                                    (True, "entry_missing")):
                assert R.EndState(
                    _final(row["final"]), exitCode, cancelRequested=cancel,
                    timedOut=True, launchFailed=True, entryMissing=missing,
                    memoryLimitHit=True) == ("failed", reason)


def test_the_checked_first_rows_and_reasons_are_the_contracts():
    first = CONTRACT["end"]["checked_first"]
    assert [row["when"] for row in first] == ["launch_failed", "timed_out"]
    assert {row["state"] for row in first} == {"failed"}
    assert set(CONTRACT["end"]["reasons"]) == {item.value
                                                for item in T.EndReason}


def test_the_runner_method_is_the_pure_function():
    runner = R.PluginRunner()
    assert runner.EndState(
        T.EventType.DONE, 0, cancelRequested=False, timedOut=False,
        launchFailed=False, entryMissing=False,
        memoryLimitHit=False) == ("done", None)


# ── the environment ────────────────────────────────────────────────────

PARENT = {
    "PATH": "C:\\bin", "SystemRoot": "C:\\Windows", "HOME": "h",
    "AUTONA_VODSPLIT_CMD": "python -m vodsplit", "AUTONA_PLUGIN_DEV": "d",
    "Autona_Secret_Other": "leak", "autona_instance_tag": "t",
    "QT_API": "pyside6", "qt_api": "pyqt", "pythonioencoding": "cp932",
    "AUTONAMOUS": "kept (no underscore)",
}


def test_the_environment_subtracts_case_insensitively():
    env = R.BuildEnvironment(PARENT, pluginDir="C:\\p", protocol=None,
                             secrets={}, apiUrl=None)
    assert env == {
        "PATH": "C:\\bin", "SystemRoot": "C:\\Windows", "HOME": "h",
        "AUTONAMOUS": "kept (no underscore)",
        "PYTHONIOENCODING": "utf-8", "PYINSTALLER_RESET_ENVIRONMENT": "1",
        "AUTONA_PLUGIN_DIR": "C:\\p"}


def test_describe_and_run_add_what_the_contract_says():
    added = {item["name"]: item["when"]
             for item in CONTRACT["environment"]["add"]}
    secrets = {"AUTONA_SECRET_WEBHOOK_URL": "https://x"}
    describe = R.BuildEnvironment({}, pluginDir="p", protocol=None,
                                  secrets=secrets, apiUrl="http://a")
    run = R.BuildEnvironment({}, pluginDir="p", protocol=1, secrets=secrets,
                             apiUrl="http://127.0.0.1:5107/api/v1")
    assert set(describe) == {name for name, when in added.items()
                             if "describe" in when}
    assert set(run) == {name.replace("<KEY>", "WEBHOOK_URL")
                        for name in added}
    assert run["AUTONA_PROTOCOL"] == "1"
    assert run["AUTONA_SECRET_WEBHOOK_URL"] == "https://x"
    without = R.BuildEnvironment({}, pluginDir="p", protocol=1, secrets={},
                                 apiUrl=None)
    assert "AUTONA_API_URL" not in without
    assert CONTRACT["environment"]["remove_prefixes"] == \
        list(T.ENV_REMOVE_PREFIXES)
    assert CONTRACT["environment"]["remove_names"] == list(T.ENV_REMOVE_NAMES)


def test_a_secret_name_must_be_a_secret_one():
    with pytest.raises(ValueError):
        R.BuildEnvironment({}, pluginDir="p", protocol=1,
                           secrets={"PATH": "x"}, apiUrl=None)


# ── the events' rules (pure) ───────────────────────────────────────────

def test_an_id_key_is_ignored_and_raw_keeps_everything():
    line = {"type": "log", "message": "m", "id": 7, "extra": [1]}
    event = R.ParseEvent(line)
    assert event == T.LogEvent(message="m")
    assert event.raw == line


def test_fields_that_do_not_fit_are_dropped_and_the_event_taken():
    assert R.ParseEvent({"type": "progress", "fraction": 7,
                         "overall": "half", "eta": -1}) == T.ProgressEvent()
    assert R.ParseEvent({"type": "stage", "stage": "send", "index": "two",
                         "count": True}) == T.StageEvent(stage="send")
    assert R.ParseEvent({"type": "progress", "stage": "s", "fraction": 0.5,
                         "overall": 1, "eta": None}) == T.ProgressEvent(
        stage="s", fraction=0.5, overall=1)
    assert R.ParseEvent({"type": "done", "summary": 5}) == T.DoneEvent()
    assert R.ParseEvent({"type": "error"}) == T.ErrorEvent(
        code="error", message="")


def test_texts_are_truncated_per_value_and_bad_codes_become_error():
    event = R.ParseEvent({"type": "done", "summary": {"ja": "あ" * 600,
                                                      "en": "e" * 600,
                                                      "fr": 3}})
    assert event.summary == {"ja": "あ" * 500, "en": "e" * 500}
    error = R.ParseEvent({"type": "error", "code": "Bad-Code!",
                          "message": "m" * 3000, "hint": "h" * 1500})
    assert (error.code, len(error.message), len(error.hint)) == \
        ("error", 2000, 1000)
    assert R.ParseEvent({"type": "error", "code": "x" * 64,
                         "message": "m"}).code == "x" * 64
    assert R.ParseEvent({"type": "error", "code": "x" * 65,
                         "message": "m"}).code == "error"


def test_lines_that_are_not_events_are_none():
    for obj in (None, [1], "s", {"message": "no type"}, {"type": 3},
                {"type": "telemetry"}):
        assert R.ParseEvent(obj) is None


def test_artifacts_keep_the_first_fifty_and_drop_the_unusable():
    items = [{"path": f"a{n}.txt", "kind": "text"} for n in range(60)]
    assert len(R.ParseEvent({"type": "done", "artifacts": items})
               .artifacts) == 50
    done = R.ParseEvent({"type": "done", "artifacts": [
        {"path": "a.txt", "kind": "text", "label": {"ja": "x" * 300}},
        {"path": "p.html", "kind": "html"}, {"kind": "text"},
        {"path": "", "kind": "file"}, "not an object"]})
    assert done.artifacts == (T.Artifact(path="a.txt", kind="text",
                                         label={"ja": "x" * 200}),)
    assert len(done.raw["artifacts"]) == 5


def test_the_line_splitter_holds_at_most_a_line():
    lines = R._Lines(limit=8)
    assert lines.Feed(b"ab\r\ncd") == [(b"ab", 2)]
    assert lines.Feed(b"efghijklmn\nxy") == [(b"cdefghijk", 12)]
    assert lines.Flush() == (b"xy", 2)
    assert lines.Flush() is None
    assert R._Lines(limit=3).Feed(b"abc\r\n") == [(b"abc", 3)]


def test_worker_log_stops_at_its_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "WORKER_LOG_MAX_BYTES", 200)
    note = "--- full ---\n"
    monkeypatch.setattr(R, "WORKER_LOG_FULL_NOTE", note)
    path = tmp_path / "worker.log"
    path.write_bytes(b"x" * 50 + b"\n")
    log = R._WorkerLog(str(path))
    for number in range(40):
        log.Write(f"line {number}")
    log.Close()
    data = path.read_text(encoding="utf-8")
    assert data.endswith(note) and data.count(note) == 1
    assert len(data.encode("utf-8")) <= 200


# ── the process (Windows) ──────────────────────────────────────────────

COOPERATIVE = 15.0  # a grace / timeout the worker never needs to reach


@pytest.fixture
def runner():
    made = R.PluginRunner(poll=0.02, cancelGrace=COOPERATIVE,
                          describeTimeout=COOPERATIVE)
    yield made
    made.Shutdown(5.0)


@pytest.fixture
def killer():
    """A short grace: for the hang tests, which wait for the kill."""
    made = R.PluginRunner(poll=0.02, cancelGrace=1.0)
    yield made
    made.Shutdown(5.0)


def _job_dir(tmp_path, params=None):
    job = tmp_path / "20261126-213000-a1b2"
    (job / "out").mkdir(parents=True)
    request = {
        "protocol": 1, "plugin": "example.fake", "job": "post",
        "run_id": job.name, "trigger": {"kind": "manual"}, "locale": "ja",
        "params": params or {}, "settings": {}, "inputs": {},
        "out_dir": str(job / "out"), "data_dir": str(tmp_path / "data"),
        "host": {"name": "Autona", "version": "2.6.0"}}
    (job / "request.json").write_text(json.dumps(request), encoding="utf-8")
    return job


def _spec(tmp_path, mode="ok", timeout=None, lane=T.Lane.HEAVY, memory=None,
          argv=None, parent=None, secrets=None, cwd=None, **fake):
    job = _job_dir(tmp_path)
    parent = dict(os.environ if parent is None else parent)
    parent.update({key: str(value) for key, value in fake.items()},
                  FAKE_PLUGIN_MODE=mode)
    env = R.BuildEnvironment(parent, pluginDir=str(FAKE.parent), protocol=1,
                             secrets=secrets or {}, apiUrl=None)
    return T.LaunchSpec(
        argv=tuple(argv or (sys.executable, str(FAKE), "run",
                            str(job / "request.json"))),
        cwd=str(cwd or job), env=env, lane=lane,
        log_path=str(job / "worker.log"), timeout_seconds=timeout,
        memory_mb=memory)


class _Collect:
    def __init__(self):
        self.events = []
        self.ends = []
        self.endThread = None

    def onEvent(self, event):
        self.events.append(event)

    def onEnd(self, outcome):
        self.endThread = threading.current_thread()
        self.ends.append(outcome)

    def types(self):
        return [event.type.value for event in self.events]


def _run(runner, spec, cancel=None):
    """Start, optionally Cancel once `cancel(collect)` says so, wait."""
    got = _Collect()
    handle = runner.Start(spec, got.onEvent, got.onEnd)
    if cancel is not None:
        deadline = time.monotonic() + LONG
        while not cancel(got) and time.monotonic() < deadline:
            time.sleep(0.02)
        handle.Cancel()
    outcome = handle.Wait(LONG)
    assert outcome is not None, "the run did not end"
    assert got.ends == [outcome]
    return outcome, got, Path(spec.log_path).read_text(encoding="utf-8")


def _started(got):
    return any(event.type == "stage" for event in got.events)


# Process state via the Windows API (no psutil in the requirements).

def _kernel32():
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetPriorityClass.argtypes = (wintypes.HANDLE,)
    kernel32.GetPriorityClass.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _priority(pid):
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(0x1000, False, pid)
    assert handle, f"process {pid} cannot be opened"
    try:
        return kernel32.GetPriorityClass(handle)
    finally:
        kernel32.CloseHandle(handle)


def _alive(pid):
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel32.CloseHandle(handle)


def _gone(pid, within=30.0):
    deadline = time.monotonic() + within
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _read_pid(path, within=LONG):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return int(text)
        except (OSError, ValueError):  # not there yet, or half written
            pass
        time.sleep(0.02)
    raise AssertionError("no child pid")


def _until(check, within=LONG):
    deadline = time.monotonic() + within
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


@windows
def test_ok_takes_every_event_and_ends_done(runner, tmp_path):
    outcome, got, log = _run(runner, _spec(tmp_path, "ok"))
    assert got.types() == ["stage", "progress", "log", "warning", "stage",
                           "progress", "done"]
    assert got.events[0] == T.StageEvent(
        stage="prepare", label={"ja": "準備", "en": "Prepare"}, index=1,
        count=2)
    assert (outcome.state, outcome.reason, outcome.exit_code) == \
        ("done", None, 0)
    assert outcome.final == got.events[-1]
    assert outcome.final_raw["summary"] == {"ja": "Webhook に送った",
                                            "en": "Posted"}
    assert [item.kind for item in outcome.final.artifacts] == \
        ["text", "image", "file"]
    assert not outcome.cancel_requested and not outcome.timed_out
    assert isinstance(outcome.peak_memory_bytes, int) and \
        outcome.peak_memory_bytes > 0
    assert "[stdout] fake plugin: starting (not a JSON line)" in log
    assert "fake plugin: stderr line" in log
    assert "--- exit 0 ---" in log
    assert got.endThread is not threading.current_thread()


@windows
def test_error_ends_error(runner, tmp_path):
    outcome, got, _ = _run(runner, _spec(tmp_path, "error"))
    assert (outcome.state, outcome.exit_code) == ("error", 2)
    assert outcome.final.code == "unsupported_host"
    assert outcome.final.hint == {"ja": "Discord か Slack の URL を入れてください",
                                  "en": "Use a Discord or Slack URL"}


@windows
def test_crash_ends_failed_with_its_traceback_in_the_log(runner, tmp_path):
    outcome, _, log = _run(runner, _spec(tmp_path, "crash"))
    assert (outcome.state, outcome.reason, outcome.exit_code,
            outcome.final) == ("failed", "abnormal_exit", 1, None)
    assert "RuntimeError: fake crash" in log


@windows
def test_slow_stops_on_the_cancel(runner, tmp_path):
    outcome, got, _ = _run(runner, _spec(tmp_path, "slow"), cancel=_started)
    assert (outcome.state, outcome.exit_code) == ("cancelled", 130)
    assert outcome.cancel_requested and got.types()[-1] == "cancelled"


@windows
def test_done_after_the_cancel_is_done_finished_before_it(runner, tmp_path):
    outcome, _, _ = _run(runner, _spec(tmp_path, "done_after_cancel"),
                         cancel=_started)
    assert (outcome.state, outcome.exit_code) == ("done", 0)
    assert outcome.cancel_requested and outcome.finished_before_cancel


@windows
def test_stdin_stays_open_while_it_runs(runner, tmp_path):
    outcome, _, _ = _run(runner, _spec(tmp_path, "stdin_open"))
    assert (outcome.state, outcome.final.summary) == \
        ("done", "stdin stayed open")


@windows
def test_hang_is_ended_with_its_job_after_the_grace_and_the_priority_is_raised_first(  # noqa: E501
        tmp_path):
    # A grace of 3 s: long enough for both processes' priorities to be
    # seen as normal before the kill, even on a loaded machine.
    runner = R.PluginRunner(poll=0.02, cancelGrace=3.0)
    childFile = tmp_path / "child.pid"
    spec = _spec(tmp_path, "hang", FAKE_PLUGIN_CHILD_PID=childFile)
    got = _Collect()
    handle = runner.Start(spec, got.onEvent, got.onEnd)
    child = _read_pid(childFile)
    deadline = time.monotonic() + LONG
    while not _started(got) and time.monotonic() < deadline:
        time.sleep(0.02)
    # Both the worker and the process it started run below normal: the
    # job's limit, set before the worker was resumed.
    assert _priority(handle.pid) == R.BELOW_NORMAL_PRIORITY_CLASS
    assert _priority(child) == R.BELOW_NORMAL_PRIORITY_CLASS
    began = time.monotonic()
    handle.Cancel()
    # The run's thread rewrites the job's limit: every process in it is
    # normal again, before the grace's kill. The kernel reaches the job's
    # processes one by one, so the two are polled together.
    assert _until(lambda: (_priority(handle.pid), _priority(child)) == (
        R.NORMAL_PRIORITY_CLASS, R.NORMAL_PRIORITY_CLASS))
    outcome = handle.Wait(LONG)
    runner.Shutdown(5.0)
    assert time.monotonic() - began >= 2.9  # the grace (3 s here)
    assert (outcome.state, outcome.exit_code) == ("cancelled",
                                                  R.KILLED_EXIT_CODE)
    assert outcome.final is None and outcome.cancel_requested
    assert _gone(child), "the grandchild outlived the job"
    assert R.HOST_STOPPED_NOTE in Path(spec.log_path).read_text(
        encoding="utf-8")


@windows
def test_a_grandchild_ends_with_the_job(runner, tmp_path):
    childFile = tmp_path / "grandchild.pid"
    outcome, _, _ = _run(runner, _spec(
        tmp_path, "grandchild", FAKE_PLUGIN_CHILD_PID=childFile))
    assert (outcome.state, outcome.exit_code) == ("done", 0)
    assert _gone(_read_pid(childFile)), "the grandchild outlived the job"


@windows
def test_past_its_time_limit_it_is_timed_out_and_cancelled(runner, tmp_path):
    outcome, got, _ = _run(runner, _spec(tmp_path, "timeout", timeout=0.5,
                                         FAKE_PLUGIN_DELAY=60))
    assert (outcome.state, outcome.reason) == ("failed", "timed_out")
    assert outcome.timed_out and not outcome.cancel_requested
    # Cut short of the worker's 60 s: it took the cancel line.
    assert outcome.exit_code == 130
    assert got.types()[-1] == "cancelled"


@windows
def test_a_hang_past_its_time_limit_is_killed_and_timed_out(killer,
                                                            tmp_path):
    runner = killer
    outcome, _, _ = _run(runner, _spec(tmp_path, "hang", timeout=0.3))
    assert (outcome.state, outcome.reason, outcome.exit_code) == \
        ("failed", "timed_out", R.KILLED_EXIT_CODE)


@windows
def test_done_in_time_is_not_timed_out(runner, tmp_path):
    outcome, _, _ = _run(runner, _spec(tmp_path, "timeout", timeout=20,
                                       FAKE_PLUGIN_DELAY=0.2))
    assert (outcome.state, outcome.timed_out) == ("done", False)


@windows
@pytest.mark.parametrize("final", ["done", "error", "cancelled", "none"])
def test_every_row_of_the_end_table_with_the_fake(runner, tmp_path, final):
    rows = {row["exit"]: row for row in CONTRACT["end"]["table"]
            if row["final"] == final}
    for name, exitCode in (("0", 0), ("2", 2), ("130", 130), ("other", 1)):
        folder = tmp_path / name
        folder.mkdir()
        outcome, _, _ = _run(runner, _spec(
            folder, "combo", FAKE_PLUGIN_FINAL=final,
            FAKE_PLUGIN_EXIT=exitCode))
        assert outcome.exit_code == exitCode
        assert outcome.state == rows[name]["state"], (final, name)
        assert (outcome.final.type.value if outcome.final else "none") == \
            final


@windows
def test_noise_goes_to_worker_log_and_the_events_are_still_taken(runner,
                                                                 tmp_path):
    outcome, got, log = _run(runner, _spec(tmp_path, "noise"))
    assert outcome.state == "done"
    assert got.types() == ["stage", "log", "progress", "stage", "warning",
                           "done"]
    reserved, progress, stage, warning, done = got.events[1:]
    assert reserved == T.LogEvent(message="reserved") and \
        reserved.raw["id"] == 7
    assert progress == T.ProgressEvent() and progress.raw["extra"] is True
    assert stage == T.StageEvent(stage="send") and \
        stage.raw["colour"] == "red"
    assert warning.message == "kept"
    assert done.summary == "after noise" and done.raw["unknown"] == {"a": 1}
    assert '[stdout] {"type": "telemetry", "value": 1}' in log
    assert '[stdout] {"message": "no type"}' in log
    assert "[stdout] [1, 2, 3]" in log
    assert '[stdout] "a string"' in log
    assert "over the 1048576-byte line limit" in log
    assert "\ufffd\ufffd not utf-8" in log
    assert len(log.encode("utf-8")) < 64 * 1024  # the long line's head only


@windows
def test_limits_are_kept(runner, tmp_path):
    outcome, got, _ = _run(runner, _spec(tmp_path, "limits"))
    warnings = [event for event in got.events if event.type == "warning"]
    logs = [event for event in got.events if event.type == "log"]
    assert len(warnings) == T.MAX_WARNINGS
    assert warnings[-1].message == f"warning {T.MAX_WARNINGS - 1}"
    assert len(logs[0].message) == T.LOG_MESSAGE_MAX_LENGTH
    assert len(outcome.final.summary) == T.SUMMARY_MAX_LENGTH
    assert len(outcome.final.artifacts) == T.MAX_ARTIFACTS
    assert len(outcome.final_raw["artifacts"]) == 60


@windows
def test_bad_code_becomes_error(runner, tmp_path):
    outcome, _, _ = _run(runner, _spec(tmp_path, "bad_code"))
    assert (outcome.state, outcome.final.code) == ("error", "error")


@windows
def test_artifacts_without_a_path_or_with_an_unknown_kind_are_dropped(
        runner, tmp_path):
    outcome, _, _ = _run(runner, _spec(tmp_path, "artifacts"))
    kinds = {item.kind for item in outcome.final.artifacts}
    assert "html" not in kinds and kinds >= {"text", "image", "file", "json"}
    assert all(item.path for item in outcome.final.artifacts)
    assert len(outcome.final_raw["artifacts"]) > \
        len(outcome.final.artifacts)


@windows
def test_the_run_gets_the_subtracted_environment_and_the_job_folder(
        runner, tmp_path):
    parent = dict(os.environ, AUTONA_VODSPLIT_CMD="x", autona_plugin_dev="y",
                  QT_API="pyside6", AUTONA_SECRET_OTHER="leak")
    spec = _spec(tmp_path, "echo", parent=parent,
                 secrets={"AUTONA_SECRET_WEBHOOK_URL": "https://hook"})
    outcome, _, _ = _run(runner, spec)
    assert outcome.state == "done"
    echo = json.loads((Path(spec.cwd) / "out" / "echo.json")
                      .read_text(encoding="utf-8"))
    names = {name.upper(): value for name, value in echo["env"].items()}
    assert os.path.normcase(echo["cwd"]) == os.path.normcase(spec.cwd)
    assert names["AUTONA_PLUGIN_DIR"] == str(FAKE.parent)
    assert names["AUTONA_PROTOCOL"] == "1"
    assert names["AUTONA_SECRET_WEBHOOK_URL"] == "https://hook"
    assert names["PYTHONIOENCODING"] == "utf-8"
    assert names["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    for gone in ("AUTONA_VODSPLIT_CMD", "AUTONA_PLUGIN_DEV", "QT_API",
                 "AUTONA_SECRET_OTHER"):
        assert gone not in names
    assert echo["argv"] == [str(Path(spec.cwd) / "request.json")]


@windows
def test_a_missing_entry_is_entry_missing_and_ends_on_the_runs_thread(
        runner, tmp_path):
    spec = _spec(tmp_path, argv=(str(tmp_path / "bin" / "missing.exe"),
                                 "run", "r.json"))
    outcome, got, log = _run(runner, spec)
    assert (outcome.state, outcome.reason, outcome.exit_code) == \
        ("failed", "entry_missing", None)
    assert got.endThread is not threading.current_thread()
    assert "could not start" in log


@windows
def test_a_launch_that_fails_otherwise_is_launch_failed(runner, tmp_path):
    outcome, _, _ = _run(runner, _spec(tmp_path,
                                       cwd=tmp_path / "no such folder"))
    assert (outcome.state, outcome.reason) == ("failed", "launch_failed")


@windows
def test_the_light_lanes_memory_limit_is_enforced_and_named(runner,
                                                            tmp_path):
    grow = ("import sys\nheld = []\n"
            "while True:\n    held.append(bytearray(1024 * 1024))\n")
    outcome, _, _ = _run(runner, _spec(
        tmp_path, lane=T.Lane.LIGHT, memory=64,
        argv=(sys.executable, "-c", grow)))
    assert outcome.state == "failed" and outcome.exit_code != 0
    assert outcome.reason == "memory_limit"
    # Windows counts the limit by its own accounting: the peak can pass it
    # by a little (measured: ~0.3%), never by the growth's next megabytes.
    assert outcome.peak_memory_bytes <= 64 * 1024 * 1024 * 1.05


@windows
def test_the_heavy_lane_has_no_memory_limit(runner, tmp_path):
    grow = "held = bytearray(96 * 1024 * 1024)\n"
    outcome, _, _ = _run(runner, _spec(
        tmp_path, lane=T.Lane.HEAVY, memory=64,
        argv=(sys.executable, "-c", grow)))
    assert (outcome.state, outcome.exit_code, outcome.reason) == \
        ("failed", 0, "abnormal_exit")  # no done line, exit 0
    assert outcome.peak_memory_bytes > 96 * 1024 * 1024


@windows
def test_wait_inside_on_end_returns_the_outcome(runner, tmp_path):
    seen = []
    holder = {}

    def onEnd(outcome):
        seen.append(holder["handle"].Wait(5))

    holder["handle"] = runner.Start(_spec(tmp_path, "ok"), lambda e: None,
                                    onEnd)
    outcome = holder["handle"].Wait(LONG)
    assert seen == [outcome]


@windows
def test_an_event_callback_that_raises_does_not_stop_the_run(runner,
                                                             tmp_path):
    def onEvent(event):
        raise RuntimeError("callback")

    ends = []
    handle = runner.Start(_spec(tmp_path, "ok"), onEvent, ends.append)
    assert handle.Wait(LONG).state == "done"


@windows
def test_shutdown_cancels_kills_the_rest_and_refuses_new_starts(tmp_path):
    runner = R.PluginRunner(poll=0.02, cancelGrace=60)
    slow = _Collect()
    hang = _Collect()
    first = runner.Start(_spec(tmp_path / "a", "slow"), slow.onEvent,
                         slow.onEnd)
    second = runner.Start(_spec(tmp_path / "b", "hang"), hang.onEvent,
                          hang.onEnd)
    deadline = time.monotonic() + LONG
    while not (_started(slow) and _started(hang)) and \
            time.monotonic() < deadline:
        time.sleep(0.02)
    began = time.monotonic()
    runner.Shutdown(3.0)  # slow stops by itself; hang needs the kill
    assert time.monotonic() - began < 3.0 + 2 * R.KILL_WAIT_SECONDS + 30
    assert first.Wait(5).state == "cancelled"
    # 130 when it read the cancel within the grace (a loaded machine may
    # hold it back past it: then it is killed, still cancelled).
    assert first.Wait(5).exit_code in (130, R.KILLED_EXIT_CODE)
    assert second.Wait(5).state == "cancelled"
    assert second.Wait(5).exit_code == R.KILLED_EXIT_CODE
    after = _Collect()
    late = runner.Start(_spec(tmp_path / "c", "ok"), after.onEvent,
                        after.onEnd)
    assert (late.Wait(10).state, late.Wait(10).reason) == \
        ("failed", "launch_failed")
    assert runner.Describe(str(tmp_path), [sys.executable, str(FAKE)],
                           dev=True) == T.DescribeRun(launched=False)


# ── describe ───────────────────────────────────────────────────────────

def _plugin(tmp_path):
    folder = tmp_path / "example.fake"
    folder.mkdir()
    (folder / "plugin.json").write_text(json.dumps(
        {"id": "example.fake", "version": "1.2.3"}), encoding="utf-8")
    return folder


def _describe(tmp_path, mode, timeout=COOPERATIVE):
    folder = _plugin(tmp_path)
    runner = R.PluginRunner(
        poll=0.02, describeTimeout=timeout,
        environ=lambda: dict(os.environ, FAKE_PLUGIN_DESCRIBE=mode,
                             AUTONA_PLUGIN_DIR="C:\\elsewhere"))
    return runner.Describe(str(folder), [sys.executable, str(FAKE)],
                           dev=True)


@windows
def test_describe_reports_the_answer(tmp_path):
    run = _describe(tmp_path, "ok")
    # The id and version are read from AUTONA_PLUGIN_DIR's plugin.json: the
    # describe environment points it at the plugin (the parent's dropped).
    assert run == T.DescribeRun(
        launched=True, exit_code=0, stdout_lines=1,
        objects=({"type": "describe", "protocol": [1], "id": "example.fake",
                  "version": "1.2.3"},))


@windows
@pytest.mark.parametrize("mode, lines, objects, exit_code", [
    ("noisy", 3, 2, 0), ("no_line", 0, 0, 0), ("not_json", 1, 0, 0),
    ("exit_1", 1, 1, 1), ("wrong_id", 1, 1, 0), ("protocol_2", 1, 1, 0)])
def test_describe_reports_facts_only(tmp_path, mode, lines, objects,
                                     exit_code):
    run = _describe(tmp_path, mode)
    assert (run.launched, run.timed_out, run.stdout_lines, len(run.objects),
            run.exit_code) == (True, False, lines, objects, exit_code)


@windows
def test_describe_past_its_time_is_timed_out(tmp_path):
    run = _describe(tmp_path, "hang", timeout=0.5)
    assert (run.launched, run.timed_out, run.exit_code) == (True, True, None)


@windows
def test_describe_of_a_missing_entry(tmp_path):
    folder = _plugin(tmp_path)
    runner = R.PluginRunner()
    assert runner.Describe(str(folder), ["bin/missing.exe"], dev=False) == \
        T.DescribeRun(launched=False, entry_missing=True)
    assert runner.Describe(str(folder), ["no-such-command-autona"],
                           dev=True) == \
        T.DescribeRun(launched=False, entry_missing=True)
    assert runner.Describe(str(folder), [], dev=True) == \
        T.DescribeRun(launched=False)


def test_entry_argv(tmp_path):
    folder = str(tmp_path)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    assert R.EntryArgv(folder, ["bin/w.exe", "-x"], dev=False) == \
        [os.path.normpath(os.path.join(folder, "bin/w.exe")), "-x"]
    assert R.EntryArgv(folder, ["python", "main.py"], dev=True) == \
        ["python", "main.py"]
    assert R.EntryArgv(folder, ["main.py"], dev=True) == \
        [os.path.join(folder, "main.py")]
    absolute = os.path.abspath(os.sep + "tools")
    assert R.EntryArgv(folder, [absolute], dev=True) == [absolute]
    with pytest.raises(ValueError):
        R.EntryArgv(folder, [], dev=False)


def test_nothing_is_made_at_construction():
    runner = R.PluginRunner(popen=lambda *a, **k: pytest.fail("started"))
    assert runner._runs == set() and runner._describes == set()
    runner.Shutdown(0)


# ── review fixes: launch failures never raise, the cancel's edges ──────

def _recording_popen():
    made = []

    def popen(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        made.append(proc)
        return proc

    return made, popen


def _ends_failed(runner, spec, reason="launch_failed"):
    got = _Collect()
    handle = runner.Start(spec, got.onEvent, got.onEnd)  # never raises
    outcome = handle.Wait(LONG)
    assert (outcome.state, outcome.reason) == ("failed", reason)
    assert got.ends == [outcome]
    assert got.endThread is not threading.current_thread()
    assert runner._runs == set()
    return outcome


@windows
def test_no_job_object_no_process(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise OSError("CreateJobObject failed")

    monkeypatch.setattr(R, "_JobObject", refuse)
    runner = R.PluginRunner(popen=lambda *a, **k: pytest.fail("started"))
    _ends_failed(runner, _spec(tmp_path))


@windows
@pytest.mark.parametrize("step", ["assign", "resume"])
def test_a_failed_assign_or_resume_leaves_nothing_suspended(
        monkeypatch, tmp_path, step):
    def fail(*args, **kwargs):
        raise OSError(f"{step} failed")

    if step == "assign":
        monkeypatch.setattr(R._JobObject, "Assign", fail)
    else:
        monkeypatch.setattr(R, "_Resume", fail)
    made, popen = _recording_popen()
    runner = R.PluginRunner(popen=popen)
    _ends_failed(runner, _spec(tmp_path, "hang"))
    assert len(made) == 1 and made[0].poll() is not None
    assert not _alive(made[0].pid)


@windows
def test_whatever_popen_raises_is_a_launch_failure(tmp_path):
    spec = _spec(tmp_path / "a")
    bad = T.LaunchSpec(argv=spec.argv, cwd=spec.cwd,
                       env=dict(spec.env, **{"A=B": "x"}), lane=spec.lane,
                       log_path=spec.log_path)
    _ends_failed(R.PluginRunner(), bad)  # ValueError from Popen
    for error in (TypeError("argv"), AttributeError("x"), ValueError("v")):
        def popen(*args, error=error, **kwargs):
            raise error

        folder = tmp_path / type(error).__name__
        _ends_failed(R.PluginRunner(popen=popen), _spec(folder))


@windows
def test_cancel_after_the_end_does_nothing(runner, tmp_path):
    handle = runner.Start(_spec(tmp_path, "ok"), lambda e: None,
                          lambda o: None)
    outcome = handle.Wait(LONG)
    handle.Cancel()
    assert not handle.cancelRequested and not outcome.cancel_requested
    failed = runner.Start(_spec(tmp_path / "b", argv=(
        str(tmp_path / "missing.exe"),)), lambda e: None, lambda o: None)
    failed.Cancel()
    assert not failed.cancelRequested
    assert not failed.Wait(LONG).cancel_requested


@windows
def test_after_the_users_cancel_the_time_limit_does_not_apply(tmp_path):
    # The clock jumps past the time limit once the cancel was sent, while
    # the worker still finishes its send; the grace is not reached. The
    # limit is far beyond what a starved start takes (a run is below
    # normal), so only the jump can pass it.
    offset = {"seconds": 0.0}
    runner = R.PluginRunner(
        poll=0.02, cancelGrace=LONG * 20,
        clock=lambda: time.monotonic() + offset["seconds"])
    finish = ("import json, sys, time\n"
              "print(json.dumps({'type': 'stage', 'stage': 's'}), "
              "flush=True)\n"
              "sys.stdin.readline()\n"
              "time.sleep(1.0)\n"
              "print(json.dumps({'type': 'done', 'summary': 'sent'}), "
              "flush=True)\n")
    got = _Collect()
    handle = runner.Start(_spec(tmp_path, timeout=LONG * 2, argv=(
        sys.executable, "-c", finish)), got.onEvent, got.onEnd)
    assert _until(lambda: _started(got), LONG)
    handle.Cancel()
    assert _until(lambda: handle.cancelSent, LONG)
    offset["seconds"] = LONG * 4
    outcome = handle.Wait(LONG)
    assert (outcome.state, outcome.timed_out) == ("done", False)
    assert outcome.cancel_requested and outcome.finished_before_cancel
    runner.Shutdown(5.0)


@windows
def test_a_last_line_without_lf_is_taken_and_empty_lines_not_logged(
        runner, tmp_path):
    script = ("import sys\nnl = chr(10)\n"
              "sys.stdout.write(nl + nl + '  ' + nl + '{\"type\": \"done\"}')"
              "\nsys.stdout.flush()\n")
    outcome, _, log = _run(runner, _spec(
        tmp_path, argv=(sys.executable, "-c", script)))
    assert (outcome.state, outcome.exit_code) == ("done", 0)
    assert "[stdout]" not in log


@windows
def test_a_single_refused_allocation_is_memory_limit(runner, tmp_path):
    outcome, _, _ = _run(runner, _spec(
        tmp_path, lane=T.Lane.LIGHT, memory=64,
        argv=(sys.executable, "-c", "held = bytearray(200 * 1024 * 1024)")))
    assert (outcome.state, outcome.reason) == ("failed", "memory_limit")


@windows
def test_wait_inside_on_event_refuses_to_wait_for_itself(runner, tmp_path):
    seen = []
    holder = {}

    def onEvent(event):
        if seen:
            return
        handle = holder["handle"]
        try:
            handle.Wait()
        except RuntimeError:
            seen.append("raised")
        seen.append(handle.Wait(0.1))

    holder["handle"] = runner.Start(_spec(tmp_path, "ok"), onEvent,
                                    lambda o: None)
    assert holder["handle"].Wait(LONG).state == "done"
    assert seen == ["raised", None]


@windows
def test_shutdown_while_a_run_is_starting_kills_it_once_started(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def popen(*args, **kwargs):
        entered.set()
        release.wait(LONG)
        return subprocess.Popen(*args, **kwargs)

    runner = R.PluginRunner(poll=0.02, cancelGrace=60, popen=popen)
    got = _Collect()
    holder = {}
    starter = threading.Thread(target=lambda: holder.setdefault(
        "handle", runner.Start(_spec(tmp_path, "hang"), got.onEvent,
                               got.onEnd)))
    starter.start()
    assert entered.wait(LONG)
    stopper = threading.Thread(target=runner.Shutdown, args=(10.0,))
    stopper.start()
    assert _until(lambda: runner._stopping, LONG)
    release.set()
    starter.join(LONG)
    stopper.join(LONG)
    assert not stopper.is_alive()
    outcome = holder["handle"].Wait(5)
    assert outcome.state == "cancelled" and outcome.cancel_requested
    assert outcome.exit_code == R.KILLED_EXIT_CODE


def test_secret_names_and_values_are_checked():
    for secrets in ({"AUTONA_SECRET_A": "x", "autona_secret_a": "y"},
                    {"AUTONA_SECRET_A": 3}, {"AUTONA_SECRET_A": "a\0b"},
                    {"AUTONA_SECRET_A=B": "x"}):
        with pytest.raises(ValueError):
            R.BuildEnvironment({}, pluginDir="p", protocol=1,
                               secrets=secrets, apiUrl=None)
    with pytest.raises(ValueError):
        R.BuildEnvironment({}, pluginDir="p\0", protocol=None, secrets={},
                           apiUrl=None)


@windows
def test_describe_keeps_the_answer_past_the_objects_limit(tmp_path):
    folder = _plugin(tmp_path)
    script = folder / "chatty.py"
    script.write_text(
        "import json\n"
        "for n in range(40):\n"
        "    print(json.dumps({'type': 'log', 'message': str(n)}))\n"
        "print(json.dumps({'type': 'describe', 'protocol': [1], "
        "'id': 'example.fake', 'version': '1.2.3'}))\n",
        encoding="utf-8")
    run = R.PluginRunner(describeTimeout=COOPERATIVE).Describe(
        str(folder), [sys.executable, str(script)], dev=True)
    assert run.stdout_lines == 41
    assert len(run.objects) == R.DESCRIBE_OBJECTS_MAX + 1
    assert run.objects[-1]["type"] == "describe"
