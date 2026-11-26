"""PluginJobs with the real PluginRunner (ADR-009 lanes L8 + L1) and the
fake worker (tests/fixtures/fake_plugin_worker.py): a run from Start to
its record, and the shells' close (PluginJobs.Shutdown: the cancel, the
grace, then RunHandle.Kill).

The registry, inputs, settings and secrets are fakes (test_plugin_jobs.py
covers PluginJobs itself, test_plugin_runner.py the runner). Windows only
(the job object).
"""

import os
import sys
import time
from pathlib import Path

import pytest

from src.core import PluginRunner as R
from src.core import PluginTypes as T
from src.core.PluginJobs import PluginJobs
from src.core.PluginTypes import (
    Badge, Field, Job, JobState, Lane, Manifest, ManifestReport, Permissions,
    PluginState, RegisteredPlugin, Trigger, TriggerKind)

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fixtures" / "fake_plugin_worker.py"
PLUGIN = "example.fake"
MANUAL = Trigger(kind=TriggerKind.MANUAL)
# A bound for failing, not a pace (the runs are BELOW_NORMAL).
LONG = 600.0

windows = pytest.mark.skipif(sys.platform != "win32",
                             reason="the host platform only")


def _Manifest():
    # A dev plugin: the absolute interpreter and worker are kept as given.
    return Manifest(
        manifest=1, id=PLUGIN, version="1.0.0", name="Fake",
        author="someone", license="MIT", min_autona="2.0.0",
        protocol=(1,), platforms=("windows-x64",),
        entry=(sys.executable, str(FAKE)),
        permissions=Permissions(network=(), files="inputs", spawn=False,
                                autona_api="none"),
        jobs=(Job(id="run", title="Run", lane="heavy", timeout_seconds=600,
                  params=(Field(key="mode", type="text", label="Mode",
                                default="ok"),)),))


class FakeRegistry:
    def __init__(self, folder):
        self.plugin = RegisteredPlugin(
            id=PLUGIN, folder=str(folder), badge=Badge.DEV,
            report=ManifestReport(manifest=_Manifest(), problems=()),
            state=PluginState.READY)

    def Get(self, pluginId):
        return self.plugin if pluginId == PLUGIN else None


class FakeInputs:
    def Prepare(self, key, kind, inputsDir, pick):
        raise AssertionError("the job has no source")


class FakeSettings:
    def Missing(self, pluginId, manifest, *, dev):
        return ()


class FakeSecrets:
    def Environment(self, pluginId, *, dev):
        return {}


def _until(condition, within=LONG):
    deadline = time.monotonic() + within
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def _gone(pid, within=30.0):
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def alive():
        handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel32.CloseHandle(handle)

    return _until(lambda: not alive(), within)


class _Env:
    def __init__(self, tmp, cancelGrace):
        folder = tmp / "plugins" / PLUGIN
        folder.mkdir(parents=True)
        self.tmp = tmp
        self.childFile = tmp / "child.pid"
        self.runner = R.PluginRunner(poll=0.02, cancelGrace=cancelGrace)
        self.ended = []
        self.freed = []
        self.jobs = PluginJobs(
            runner=self.runner, registry=FakeRegistry(folder),
            inputs=FakeInputs(), settings=FakeSettings(),
            secrets=FakeSecrets(), workDir=lambda: str(tmp / "work"),
            userDataDir=str(tmp / "user_data"), hostVersion="2.6.0",
            locale=lambda: "ja", apiUrl=lambda: None,
            onRunEnded=self.ended.append, onLaneFreed=self.freed.append,
            environ=lambda: {**os.environ,
                             "FAKE_PLUGIN_CHILD_PID": str(self.childFile)})

    def Start(self, mode):
        record = self.jobs.Start(PLUGIN, "run", MANUAL, {"mode": mode}, {},
                                 {})
        assert isinstance(record, T.RunRecord), record
        return record

    def Close(self):
        self.runner.Shutdown(5.0)


@pytest.fixture
def env(tmp_path):
    made = _Env(tmp_path, cancelGrace=15.0)
    yield made
    made.Close()


@windows
def test_a_run_goes_from_start_to_its_done_record(env):
    record = env.Start("ok")
    assert record.state is JobState.RUNNING
    assert _until(lambda: env.ended), "the run did not end"
    [ended] = env.ended
    assert ended.run_id == record.run_id
    assert ended.state is JobState.DONE and ended.exit_code == 0
    assert env.freed == [Lane.HEAVY]
    assert env.jobs.Get(PLUGIN, record.run_id).state is JobState.DONE
    assert env.jobs.BusyFor(Lane.HEAVY) is None


@windows
@pytest.mark.skipif(not hasattr(T.RunHandle, "Kill"),
                    reason="RunHandle.Kill is the contract patch's (#393)")
def test_shutdown_kills_what_its_grace_did_not_end(tmp_path):
    # The run never reads its cancel; the runner's own grace is far off,
    # so only Shutdown's Kill after its 1 s ends it (and its child).
    env = _Env(tmp_path, cancelGrace=LONG * 20)
    try:
        record = env.Start("hang")
        assert _until(lambda: env.childFile.is_file()
                      and env.childFile.read_text(encoding="utf-8"))
        child = int(env.childFile.read_text(encoding="utf-8"))
        env.jobs.Shutdown(1.0)
        assert _until(lambda: env.ended), "Shutdown did not end the run"
        [ended] = env.ended
        assert ended.run_id == record.run_id
        # Cancelled before the kill: the table's cancelled.
        assert ended.state is JobState.CANCELLED
        assert ended.exit_code == R.KILLED_EXIT_CODE
        assert _gone(child), "the child outlived the job"
        log = env.tmp / "work" / "plugins" / PLUGIN / record.run_id \
            / T.LOG_FILE
        assert R.HOST_STOPPED_NOTE in log.read_text(encoding="utf-8")
    finally:
        env.Close()
