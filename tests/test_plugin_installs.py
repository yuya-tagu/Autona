"""The installs as the v1 routes drive them (ADR-009 P5a, wiring I4):
src/core/PluginInstalls.py.

The first part fakes the installer, the registry, the runs and the hooks,
and runs the jobs by hand (an executor that queues them), so each state of
an install and a removal is seen. The last test is the whole path with the
real PluginInstaller, PluginRegistry, PluginJobs and PluginRunner and the
fake worker (tests/fixtures/fake_plugin_worker.py): an uploaded zip
installed, consented, run, disabled, enabled and removed (Windows only:
the job object).
"""

import concurrent.futures
import dataclasses
import io
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from src.core import PluginInstalls as installs_module
from src.core import PluginRunner as R
from src.core.PluginInstaller import InstallError, PluginInstaller
from src.core.PluginInstalls import (UPLOAD_EXPIRY_SECS, PluginInstalls,
                                     PluginInstallsError,
                                     ReadListingBlocklist)
from src.core.PluginJobs import PluginJobs
from src.core.PluginRegistry import PluginRegistry
from src.core.PluginTypes import (
    Badge, BlockEntry, HookDisabledReason, InstallSource, InstallState,
    JobState,
    ListEntry, PluginState, RemovalState, RunRecord, StartRefusal, Trigger,
    TriggerKind)

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fixtures" / "fake_plugin_worker.py"
PLUGIN = "example.tool"
MANUAL = Trigger(kind=TriggerKind.MANUAL)


# ── fakes ───────────────────────────────────────────────────────────────

class QueuedExecutor:
    """TaskExecutor's submit / stopping; the jobs run when ``RunAll`` says."""

    def __init__(self):
        self.stopping = threading.Event()
        self.queue = []
        self.names = []

    def submit(self, fn, *, task_name):
        future = concurrent.futures.Future()
        self.queue.append((fn, future))
        self.names.append(task_name)
        return future

    def RunAll(self):
        while self.queue:
            fn, future = self.queue.pop(0)
            future.set_running_or_notify_cancel()
            future.set_result(fn())


class SyncExecutor(QueuedExecutor):
    def submit(self, fn, *, task_name):
        future = super().submit(fn, task_name=task_name)
        self.RunAll()
        return future


@dataclasses.dataclass
class Pending:
    plugin_id: str = PLUGIN
    consent_digest: str = "d" * 64


@dataclasses.dataclass
class Record:
    sha256: str = "a" * 64
    source: InstallSource = InstallSource("manual")


class FakeInstaller:
    def __init__(self, folder):
        self.folder = folder
        self.calls = []
        self.prepareError = None
        self.commitError = None
        self.removeError = None
        self.enableError = None
        self.swept = 0
        self.enabled = {}
        self.sizes = (10, 2, 30, 12, "work")
        self.record = Record()
        self.blocked = {}
        self.uploads = 0

    def Sweep(self):
        self.calls.append("Sweep")
        return self.swept

    def UploadPath(self):
        self.uploads += 1
        os.makedirs(self.folder, exist_ok=True)
        return os.path.join(self.folder,
                            f".upload-{self.uploads:016x}.part")

    def PrepareFromZip(self, path):
        with open(path, "rb") as file:
            self.calls.append(("PrepareFromZip", file.read()))
        if self.prepareError is not None:
            raise self.prepareError
        return Pending()

    def Discard(self, pending):
        self.calls.append(("Discard", pending.plugin_id))
        return True

    def Commit(self, pending):
        self.calls.append(("Commit", pending.plugin_id))
        if self.commitError is not None:
            raise self.commitError

    def SetEnabled(self, pluginId, enabled):
        if self.enableError is not None:
            raise self.enableError
        self.calls.append(("SetEnabled", pluginId, enabled))

    def RemovalSizes(self, pluginId):
        self.calls.append(("RemovalSizes", pluginId))
        return self.sizes

    def Remove(self, pluginId, *, data, runs):
        self.calls.append(("Remove", pluginId, data, runs))
        if self.removeError is not None:
            raise self.removeError

    def ReadInstallRecord(self, pluginId):
        self.calls.append(("ReadInstallRecord", pluginId))
        return self.record

    def BlockedInstalled(self, versions, blocklist):
        return {pluginId: entry for pluginId, entry in self.blocked.items()
                if pluginId in versions}


@dataclasses.dataclass
class Plugin:
    id: str
    badge: Badge
    version: str = "1.0.0"


class FakeRegistry:
    def __init__(self):
        self.plugins = {PLUGIN: Plugin(PLUGIN, Badge.MANUAL),
                        "me.dev": Plugin("me.dev", Badge.DEV),
                        "taguyan.vodsplit": Plugin("taguyan.vodsplit",
                                                   Badge.BUILTIN)}
        self.scans = 0

    def Get(self, pluginId):
        return self.plugins.get(pluginId)

    def Entries(self):
        return [ListEntry(id=p.id, name=p.id, author="a", version=p.version,
                          state=PluginState.READY, badge=p.badge,
                          size_bytes=0, has_icon=False)
                for p in self.plugins.values()]

    def Scan(self):
        self.scans += 1


class FakeJobs:
    def __init__(self):
        self.calls = []
        self.running = set()
        self.endOnCancel = True
        # A run that ended but still writes its folder: Running, yet
        # nothing for CancelAll to ask (PluginJobs._finishing).
        self.finishing = False

    def Running(self, pluginId):
        return pluginId in self.running

    def Hold(self, pluginId):
        self.calls.append(("Hold", pluginId))
        return pluginId not in self.running

    def Unhold(self, pluginId):
        self.calls.append(("Unhold", pluginId))

    def CancelAll(self, pluginId):
        self.calls.append(("CancelAll", pluginId))
        count = 1 if pluginId in self.running and not self.finishing             else 0
        if self.endOnCancel and not self.finishing:
            self.running.discard(pluginId)
        return count


class FakeHooks:
    def __init__(self):
        self.refreshes = 0
        self.forgotten = []

    def Refresh(self):
        self.refreshes += 1

    def Forget(self, pluginId, reason):
        self.forgotten.append((pluginId, reason))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    class Env:
        pass

    e = Env()
    e.folder = tmp_path / "plugins"
    e.installer = FakeInstaller(str(e.folder))
    e.registry, e.jobs, e.hooks = FakeRegistry(), FakeJobs(), FakeHooks()
    e.executor = QueuedExecutor()
    e.clock = Clock()
    e.slept = []

    def sleep(seconds):
        e.slept.append(seconds)
        e.clock.now += seconds

    e.installs = PluginInstalls(e.installer, e.registry, jobs=e.jobs,
                                hooks=e.hooks, executor=e.executor,
                                clock=e.clock, sleep=sleep)
    e.installs.Sweep()
    return e


def _refused(call, reason):
    with pytest.raises(PluginInstallsError) as error:
        call()
    assert error.value.reason is reason, error.value.detail
    return error.value


def _upload(env, data=b"PK zip bytes"):
    uploadId, expires = env.installs.ReceiveUpload(io.BytesIO(data),
                                                   len(data))
    assert expires == UPLOAD_EXPIRY_SECS
    return uploadId


def _consent(env):
    status = env.installs.StartInstall(_upload(env))
    env.executor.RunAll()
    return status.install_id


# ── the launch ──────────────────────────────────────────────────────────

def test_nothing_is_received_before_the_launch_sweep(tmp_path):
    installer, registry = FakeInstaller(str(tmp_path)), FakeRegistry()
    installs = PluginInstalls(installer, registry, executor=QueuedExecutor())
    _refused(lambda: installs.ReceiveUpload(io.BytesIO(b"x"), 1),
             StartRefusal.UNAVAILABLE)
    _refused(lambda: installs.StartInstall("u"), StartRefusal.UNAVAILABLE)
    installs.Sweep()
    assert installer.calls == ["Sweep"] and registry.scans == 0
    assert installs.ReceiveUpload(io.BytesIO(b"x"), 1)[0]


def test_a_sweep_that_put_a_folder_back_scans(env):
    env.installer.swept = 1
    env.installs.Sweep()
    assert env.registry.scans == 1 and env.hooks.refreshes == 1


def test_nothing_is_started_while_shutting_down(env):
    env.executor.stopping.set()
    _refused(lambda: _upload(env), StartRefusal.UNAVAILABLE)
    _refused(lambda: env.installs.StartRemoval(PLUGIN, data=True,
                                               runs="keep"),
             StartRefusal.UNAVAILABLE)


# ── the upload ──────────────────────────────────────────────────────────

def test_an_upload_is_held_in_a_private_file_until_dropped(env):
    uploadId = _upload(env, b"zip!")
    files = os.listdir(env.folder)
    assert len(files) == 1 and files[0].startswith(".upload-")
    assert (env.folder / files[0]).read_bytes() == b"zip!"
    assert env.installs.DropUpload("not-it") is False
    assert env.installs.DropUpload(uploadId) is True
    assert os.listdir(env.folder) == []
    assert env.installs.DropUpload(uploadId) is False


def test_a_new_upload_replaces_the_one_held(env):
    first = _upload(env, b"one")
    second = _upload(env, b"two")
    assert len(os.listdir(env.folder)) == 1
    _refused(lambda: env.installs.StartInstall(first), StartRefusal.NOT_FOUND)
    env.installs.StartInstall(second)
    env.executor.RunAll()
    assert ("PrepareFromZip", b"two") in env.installer.calls


@pytest.mark.parametrize("length", [0, -1, True, "3",
                                    installs_module.ZIP_MAX_BYTES + 1])
def test_an_upload_of_no_length_or_over_the_limit_is_refused(env, length):
    _refused(lambda: env.installs.ReceiveUpload(io.BytesIO(b"x"), length),
             StartRefusal.INVALID_REQUEST)
    assert not env.folder.exists() or os.listdir(env.folder) == []


def test_an_incomplete_upload_leaves_nothing(env):
    _refused(lambda: env.installs.ReceiveUpload(io.BytesIO(b"abc"), 10),
             StartRefusal.INVALID_REQUEST)

    class Gone:
        def read(self, size):
            raise ConnectionError("the client went away")

    _refused(lambda: env.installs.ReceiveUpload(Gone(), 10),
             StartRefusal.INVALID_REQUEST)
    assert os.listdir(env.folder) == []


def test_an_upload_expires(env):
    uploadId = _upload(env)
    env.clock.now += UPLOAD_EXPIRY_SECS
    _refused(lambda: env.installs.StartInstall(uploadId),
             StartRefusal.NOT_FOUND)
    assert os.listdir(env.folder) == []


# ── an install ──────────────────────────────────────────────────────────

def test_an_install_prepares_waits_for_consent_and_commits(env):
    status = env.installs.StartInstall(_upload(env, b"zip"))
    assert status.state is InstallState.PREPARING and status.pending is None
    assert env.executor.names == ["plugin install: prepare"]
    # One install at a time.
    _refused(lambda: env.installs.StartInstall(_upload(env)),
             StartRefusal.BUSY)
    env.executor.RunAll()
    status = env.installs.Status(status.install_id)
    assert status.state is InstallState.CONSENT
    assert status.plugin_id == PLUGIN and status.pending == Pending()
    _refused(lambda: env.installs.Consent(status.install_id, "other"),
             StartRefusal.INVALID_REQUEST)
    answer = env.installs.Consent(status.install_id, "d" * 64)
    assert answer.state is InstallState.INSTALLING
    _refused(lambda: env.installs.Cancel(status.install_id),
             StartRefusal.BUSY)
    env.executor.RunAll()
    status = env.installs.Status(status.install_id)
    assert status.state is InstallState.DONE and status.failure is None
    assert ("Commit", PLUGIN) in env.installer.calls
    assert env.registry.scans == 1 and env.hooks.refreshes == 1
    # Done: a consent again is not for an install that waits.
    _refused(lambda: env.installs.Consent(status.install_id, "d" * 64),
             StartRefusal.INVALID_REQUEST)
    _refused(lambda: env.installs.Status("nope"), StartRefusal.NOT_FOUND)


def test_the_upload_file_is_removed_after_the_prepare(env):
    env.installs.StartInstall(_upload(env))
    env.installer.prepareError = InstallError("bad_zip", ("unsafe_path",))
    env.executor.RunAll()
    assert os.listdir(env.folder) == []


def test_a_prepare_that_fails_says_why(env):
    env.installer.prepareError = InstallError("bad_zip", ("unsafe_path",))
    installId = _consent(env)
    status = env.installs.Status(installId)
    assert status.state is InstallState.FAILED
    assert status.failure.code == "bad_zip"
    assert status.failure.reason is StartRefusal.INCOMPATIBLE
    assert status.failure.problems == ("unsafe_path",)
    env.installer.prepareError = RuntimeError("C:/Users/someone/secret")
    status = env.installs.Status(_consent(env))
    assert status.failure.code == "failed"
    assert status.failure.reason is StartRefusal.UNAVAILABLE
    assert status.failure.detail == "RuntimeError"


def test_a_consent_while_the_plugin_runs_is_busy(env):
    installId = _consent(env)
    env.jobs.running.add(PLUGIN)
    _refused(lambda: env.installs.Consent(installId, "d" * 64),
             StartRefusal.BUSY)
    assert env.installs.Status(installId).state is InstallState.CONSENT


def test_a_commit_refused_by_a_run_keeps_the_install_pending(env):
    installId = _consent(env)
    env.installer.commitError = InstallError("running")
    env.installs.Consent(installId, "d" * 64)
    env.executor.RunAll()
    status = env.installs.Status(installId)
    assert status.state is InstallState.CONSENT
    assert status.failure.reason is StartRefusal.BUSY
    assert env.registry.scans == 0
    env.installer.commitError = None
    env.installs.Consent(installId, "d" * 64)
    env.executor.RunAll()
    assert env.installs.Status(installId).state is InstallState.DONE


def test_a_commit_that_fails_ends_the_install(env):
    installId = _consent(env)
    env.installer.commitError = InstallError("describe_failed",
                                             ("bad_protocol",))
    env.installs.Consent(installId, "d" * 64)
    env.executor.RunAll()
    status = env.installs.Status(installId)
    assert status.state is InstallState.FAILED
    assert status.failure.reason is StartRefusal.INCOMPATIBLE


def test_a_declined_sheet_is_discarded(env):
    installId = _consent(env)
    status = env.installs.Cancel(installId)
    assert status.state is InstallState.CANCELLED
    assert env.installer.calls[-1] == ("Discard", PLUGIN)
    # An ended install is answered as it is.
    assert env.installs.Cancel(installId).state is InstallState.CANCELLED


def test_an_install_cancelled_while_preparing_is_discarded_by_its_job(env):
    status = env.installs.StartInstall(_upload(env))
    assert env.installs.Cancel(status.install_id).state \
        is InstallState.CANCELLED
    # Its job still runs: a new install waits for it.
    _refused(lambda: env.installs.StartInstall(_upload(env)),
             StartRefusal.BUSY)
    env.executor.RunAll()
    assert env.installer.calls[-1] == ("Discard", PLUGIN)
    assert env.installs.Status(status.install_id).state \
        is InstallState.CANCELLED
    env.installs.StartInstall(_upload(env))


def test_a_new_install_declines_the_sheet_waiting(env):
    first = _consent(env)
    second = env.installs.StartInstall(_upload(env))
    assert ("Discard", PLUGIN) in env.installer.calls
    _refused(lambda: env.installs.Status(first), StartRefusal.NOT_FOUND)
    assert env.installs.Status(second.install_id).state \
        is InstallState.PREPARING


def test_a_job_the_shutdown_cancelled_ends_the_install(env):
    status = env.installs.StartInstall(_upload(env))
    fn, future = env.executor.queue.pop()
    future.cancel()
    status = env.installs.Status(status.install_id)
    assert status.state is InstallState.FAILED
    assert status.failure.code == "stopped"
    assert os.listdir(env.folder) == []


# ── the enable ──────────────────────────────────────────────────────────

def test_a_disable_is_saved_then_its_runs_cancelled(env):
    env.jobs.running.add(PLUGIN)
    write = env.installer.SetEnabled

    def logged(pluginId, enabled):
        env.jobs.calls.append(("write", enabled))
        write(pluginId, enabled)

    env.installer.SetEnabled = logged
    env.installs.SetEnabled(PLUGIN, False)
    assert env.installer.calls[-1] == ("SetEnabled", PLUGIN, False)
    # Held around the write and the cancel: a start planned before the
    # write is refused at its lane (PluginJobs' hold epoch).
    assert env.jobs.calls == [("Hold", PLUGIN), ("write", False),
                              ("CancelAll", PLUGIN), ("Unhold", PLUGIN)]
    assert env.hooks.refreshes == 1
    env.jobs.calls.clear()
    env.installs.SetEnabled(PLUGIN, True)
    assert env.installer.calls[-1] == ("SetEnabled", PLUGIN, True)
    assert env.jobs.calls == [("write", True)]


def test_only_an_installed_plugin_is_enabled(env):
    _refused(lambda: env.installs.SetEnabled("me.dev", False),
             StartRefusal.INVALID_REQUEST)
    _refused(lambda: env.installs.SetEnabled("taguyan.vodsplit", False),
             StartRefusal.INVALID_REQUEST)
    _refused(lambda: env.installs.SetEnabled("no.such", False),
             StartRefusal.NOT_FOUND)
    env.installer.enableError = OSError(5, "denied", "C:/x/enabled.json")
    error = _refused(lambda: env.installs.SetEnabled(PLUGIN, False),
                     StartRefusal.UNAVAILABLE)
    assert "C:/" not in error.detail
    # Not written: nothing cancelled, the hold given back.
    assert env.jobs.calls == [("Hold", PLUGIN), ("Unhold", PLUGIN)]


# ── a removal ───────────────────────────────────────────────────────────

def test_what_a_removal_would_remove(env):
    status = env.installs.Removal(PLUGIN)
    assert status.state is RemovalState.IDLE
    assert (status.data_bytes, status.runs, status.runs_bytes,
            status.out_bytes, status.work_folder) == (10, 2, 30, 12, "work")
    _refused(lambda: env.installs.Removal("me.dev"),
             StartRefusal.INVALID_REQUEST)


def test_a_removal_holds_cancels_waits_removes_and_scans(env):
    env.jobs.running.add(PLUGIN)
    env.jobs.endOnCancel = False
    started = env.installs.StartRemoval(PLUGIN, data=True, runs="keep_out")
    assert started.state is RemovalState.REMOVING
    assert env.installs.Removal(PLUGIN).state is RemovalState.REMOVING
    _refused(lambda: env.installs.StartRemoval(PLUGIN, data=True,
                                               runs="keep"),
             StartRefusal.BUSY)
    fn, _future = env.executor.queue[0]

    def endSoon(seconds):
        env.jobs.running.discard(PLUGIN)

    env.installs._sleep = endSoon
    scan = env.registry.Scan
    env.registry.Scan = lambda: (env.jobs.calls.append(("Scan",)), scan())
    remove = env.installer.Remove
    env.installer.Remove = lambda pluginId, **kw: (
        env.jobs.calls.append(("Remove",)), remove(pluginId, **kw))
    env.executor.RunAll()
    # The scan is inside the hold: no start of it between its folder going
    # and the list knowing.
    assert env.jobs.calls == [("Hold", PLUGIN), ("CancelAll", PLUGIN),
                              ("Remove",), ("Scan",), ("Unhold", PLUGIN)]
    assert env.installer.calls[-1] == ("Remove", PLUGIN, True, "keep_out")
    assert env.registry.scans == 1 and env.hooks.refreshes == 1
    assert env.installs.Removal(PLUGIN).state is RemovalState.IDLE


def test_a_removal_waits_for_its_runs_up_to_a_bound(env):
    env.jobs.running.add(PLUGIN)
    env.jobs.endOnCancel = False
    env.installer.removeError = InstallError("running")
    env.installs.StartRemoval(PLUGIN, data=False, runs="keep")
    env.executor.RunAll()
    assert sum(env.slept) >= installs_module.REMOVAL_WAIT_SECS
    status = env.installs.Removal(PLUGIN)
    assert status.state is RemovalState.FAILED
    assert status.failure.reason is StartRefusal.BUSY
    assert ("Unhold", PLUGIN) in env.jobs.calls
    assert env.registry.scans == 0
    # A failed one can be asked again.
    env.installer.removeError = None
    env.jobs.running.clear()
    env.installs.StartRemoval(PLUGIN, data=False, runs="keep")
    env.executor.RunAll()
    assert env.installs.Removal(PLUGIN).state is RemovalState.IDLE


def test_a_partial_removal_scans_and_says_why(env):
    env.installer.removeError = InstallError("remove_failed")
    env.installs.StartRemoval(PLUGIN, data=True, runs="delete")
    env.executor.RunAll()
    status = env.installs.Removal(PLUGIN)
    assert status.state is RemovalState.FAILED
    assert status.failure.code == "remove_failed"
    assert status.failure.reason is StartRefusal.UNAVAILABLE
    assert env.registry.scans == 1


def test_no_removal_while_its_install_commits(env):
    installId = _consent(env)
    env.installs.Consent(installId, "d" * 64)
    _refused(lambda: env.installs.StartRemoval(PLUGIN, data=True,
                                               runs="keep"),
             StartRefusal.BUSY)


# ── the review's fixes: holds, one change at a time, the caches ────────

def test_a_commit_holds_the_plugin_through_the_scan(env):
    # The swap and the scan inside one hold: no start of the plugin reads
    # the old manifest once the new folder is in (PluginJobs' hold epoch
    # refuses a start planned before).
    installId = _consent(env)
    commit = env.installer.Commit
    env.installer.Commit = lambda pending: (
        env.jobs.calls.append(("Commit",)), commit(pending))
    scan = env.registry.Scan
    env.registry.Scan = lambda: (env.jobs.calls.append(("Scan",)), scan())
    refresh = env.hooks.Refresh
    env.hooks.Refresh = lambda: (env.jobs.calls.append(("Refresh",)),
                                 refresh())
    env.installs.Consent(installId, "d" * 64)
    env.executor.RunAll()
    assert env.jobs.calls == [("Hold", PLUGIN), ("Commit",), ("Scan",),
                              ("Refresh",), ("Unhold", PLUGIN)]
    assert env.installs.Status(installId).state is InstallState.DONE


def test_a_run_started_before_the_commits_hold_keeps_it_pending(env):
    installId = _consent(env)
    env.installs.Consent(installId, "d" * 64)
    env.jobs.running.add(PLUGIN)  # got its lane before the job held
    env.executor.RunAll()
    status = env.installs.Status(installId)
    assert status.state is InstallState.CONSENT
    assert status.failure.code == "running"
    assert ("Commit", PLUGIN) not in env.installer.calls
    assert env.jobs.calls[-1] == ("Unhold", PLUGIN)
    assert env.registry.scans == 0


def test_a_staged_manifest_changed_ends_the_install_incompatible(env):
    installId = _consent(env)
    env.installer.commitError = InstallError("manifest_changed")
    env.installs.Consent(installId, "d" * 64)
    env.executor.RunAll()
    status = env.installs.Status(installId)
    assert status.state is InstallState.FAILED
    assert status.failure.code == "manifest_changed"
    assert status.failure.reason is StartRefusal.INCOMPATIBLE


def test_a_removal_waits_for_a_run_writing_its_folder(env):
    # CancelAll asks nothing of a run that ended but still writes job.json
    # (0), yet it is Running: the removal waits all the same.
    env.jobs.running.add(PLUGIN)
    env.jobs.finishing = True
    removed = []
    remove = env.installer.Remove
    env.installer.Remove = lambda pluginId, **kw: (
        removed.append(env.jobs.Running(pluginId)), remove(pluginId, **kw))

    def written(seconds):
        env.slept.append(seconds)
        env.jobs.running.discard(PLUGIN)

    env.installs._sleep = written
    env.installs.StartRemoval(PLUGIN, data=True, runs="delete")
    env.executor.RunAll()
    assert env.slept and removed == [False]
    assert env.installs.Removal(PLUGIN).state is RemovalState.IDLE


def test_no_consent_while_the_plugin_is_being_removed(env):
    installId = _consent(env)
    env.installs.StartRemoval(PLUGIN, data=True, runs="keep")
    _refused(lambda: env.installs.Consent(installId, "d" * 64),
             StartRefusal.BUSY)
    assert env.installs.Status(installId).state is InstallState.CONSENT
    env.executor.RunAll()
    env.installs.Consent(installId, "d" * 64)
    env.executor.RunAll()
    assert env.installs.Status(installId).state is InstallState.DONE


def test_no_enable_while_the_plugin_is_removed_or_committed(env):
    env.installs.StartRemoval(PLUGIN, data=True, runs="keep")
    _refused(lambda: env.installs.SetEnabled(PLUGIN, False),
             StartRefusal.BUSY)
    env.executor.RunAll()
    installId = _consent(env)
    env.installs.Consent(installId, "d" * 64)
    _refused(lambda: env.installs.SetEnabled(PLUGIN, True),
             StartRefusal.BUSY)
    env.executor.RunAll()
    env.installs.SetEnabled(PLUGIN, True)
    assert env.installer.calls[-1] == ("SetEnabled", PLUGIN, True)


def test_no_removal_or_commit_while_the_enable_is_written(env):
    installId = _consent(env)
    seen = []
    write = env.installer.SetEnabled

    def during(pluginId, enabled):
        for call in (lambda: env.installs.StartRemoval(PLUGIN, data=True,
                                                       runs="keep"),
                     lambda: env.installs.Consent(installId, "d" * 64)):
            seen.append(_refused(call, StartRefusal.BUSY).reason)
        write(pluginId, enabled)

    env.installer.SetEnabled = during
    env.installs.SetEnabled(PLUGIN, False)
    assert seen == [StartRefusal.BUSY, StartRefusal.BUSY]
    # Given back after the write.
    env.installs.Consent(installId, "d" * 64)


def test_a_removal_that_failed_after_the_plugin_left_is_still_answered(
        env):
    # A partial removal took the plugin's folder (it left the list) but
    # failed after: answered failed with the last sizes, asked again.
    remove = env.installer.Remove

    def partial(pluginId, **kw):
        remove(pluginId, **kw)
        del env.registry.plugins[PLUGIN]
        raise InstallError("remove_failed")

    env.installer.Remove = partial
    started = env.installs.StartRemoval(PLUGIN, data=True, runs="delete")
    # The answer carries the last-known sizes.
    assert (started.data_bytes, started.runs, started.work_folder) == \
        (10, 2, "work")
    env.executor.RunAll()
    assert env.registry.Get(PLUGIN) is None
    status = env.installs.Removal(PLUGIN)
    assert status.state is RemovalState.FAILED
    assert status.failure.code == "remove_failed"
    assert status.data_bytes == 10

    # Asked again: nothing left of it (NOT_FOUND) is a removal that ended.
    def gone(pluginId, **kw):
        raise InstallError("not_found")

    env.installer.Remove = gone
    env.installs.StartRemoval(PLUGIN, data=True, runs="delete")
    env.executor.RunAll()
    _refused(lambda: env.installs.Removal(PLUGIN), StartRefusal.NOT_FOUND)
    _refused(lambda: env.installs.StartRemoval(PLUGIN, data=True,
                                               runs="delete"),
             StartRefusal.NOT_FOUND)


def test_a_new_install_forgets_a_failed_removal(env):
    env.installer.removeError = InstallError("remove_failed")
    env.installs.StartRemoval(PLUGIN, data=True, runs="delete")
    env.executor.RunAll()
    assert env.installs.Removal(PLUGIN).state is RemovalState.FAILED
    installId = _consent(env)
    env.installs.Consent(installId, "d" * 64)
    env.executor.RunAll()
    status = env.installs.Removal(PLUGIN)
    assert status.state is RemovalState.IDLE and status.failure is None


def test_the_sizes_are_not_walked_while_it_is_removed(env):
    env.installs.StartRemoval(PLUGIN, data=True, runs="keep")
    walks = env.installer.calls.count(("RemovalSizes", PLUGIN))
    env.installer.sizes = (99, 9, 99, 99, "other")
    for _ in range(3):
        status = env.installs.Removal(PLUGIN)
        assert status.state is RemovalState.REMOVING
        assert status.data_bytes == 10
    assert env.installer.calls.count(("RemovalSizes", PLUGIN)) == walks
    env.executor.RunAll()
    assert env.installs.Removal(PLUGIN).data_bytes == 99


def test_the_blocked_check_reads_install_json_once_per_change(env):
    env.installs.SetBlocklist([dataclasses.replace(ROW, id="other.one")])

    def reads():
        return env.installer.calls.count(("ReadInstallRecord", PLUGIN))

    for _ in range(3):
        assert env.installs.BlockedReason(PLUGIN, "1.0.0") is None
    assert reads() == 1
    env.installs.Sweep()
    env.installs.BlockedReason(PLUGIN, "1.0.0")
    assert reads() == 2
    env.installs.Consent(_consent(env), "d" * 64)
    env.executor.RunAll()
    env.installs.BlockedReason(PLUGIN, "1.0.0")
    assert reads() == 3
    env.installs.StartRemoval(PLUGIN, data=True, runs="keep")
    env.executor.RunAll()
    env.installs.BlockedReason(PLUGIN, "1.0.0")
    assert reads() == 4


# ── the blocklist ───────────────────────────────────────────────────────

ROW = BlockEntry(reason={"ja": "危険", "en": "Unsafe"}, since="2026-11-01",
                 id=PLUGIN, versions="*")


def test_a_newly_blocked_plugin_is_stopped_and_its_hooks_forgotten(env):
    assert env.installs.BlockedReason(PLUGIN, "1.0.0") is None
    env.installer.blocked = {PLUGIN: ROW}
    env.installs.SetBlocklist([ROW])
    assert env.installs.Blocklist() == (ROW,)
    # As a disable: held around the cancel (the row is in effect first).
    assert env.jobs.calls == [("Hold", PLUGIN), ("CancelAll", PLUGIN),
                              ("Unhold", PLUGIN)]
    assert env.hooks.forgotten == [(PLUGIN, HookDisabledReason.BLOCKED)]
    assert env.hooks.refreshes == 1
    assert env.installs.BlockedReason(PLUGIN, "1.0.0") == ROW.reason
    assert env.installs.BlockedReason("other.one", "1.0.0") is None
    # Already blocked: nothing again.
    env.installs.SetBlocklist([ROW])
    assert len(env.hooks.forgotten) == 1


def test_a_row_without_a_reason_still_blocks(env):
    row = dataclasses.replace(ROW, reason={})
    env.installs.SetBlocklist([row])
    assert env.installs.BlockedReason(PLUGIN, "1.0.0") == {
        "ja": "遮断されています。", "en": "Blocked."}


def test_the_listing_copy_is_read_before_the_shipped_one(tmp_path):
    copy, shipped = tmp_path / "_listing.json", tmp_path / "plugins.json"
    row = {"id": PLUGIN, "versions": "*", "reason": "x",
           "since": "2026-11-01"}
    shipped.write_text(json.dumps({"blocked": [row]}), encoding="utf-8")
    assert ReadListingBlocklist([str(copy), str(shipped)])[0].id == PLUGIN
    copy.write_text("{broken", encoding="utf-8")
    assert len(ReadListingBlocklist([str(copy), str(shipped)])) == 1
    copy.write_text(json.dumps({"blocked": []}), encoding="utf-8")
    assert ReadListingBlocklist([str(copy), str(shipped)]) == ()
    assert ReadListingBlocklist([str(tmp_path / "none.json")]) == ()


def test_making_it_touches_no_file(tmp_path):
    PluginInstalls(FakeInstaller(str(tmp_path / "p")), FakeRegistry(),
                   executor=QueuedExecutor())
    assert os.listdir(tmp_path) == []


# ── the whole path, with the real parts and the fake worker ────────────

windows = pytest.mark.skipif(sys.platform != "win32",
                             reason="the host platform only")


class WorkerRunner(R.PluginRunner):
    """The real runner, its worker the fake one: the installed entry
    (bin/tool.exe in the zip) is run as ``python fake_plugin_worker.py``."""

    def Describe(self, pluginDir, entry, *, dev):
        return super().Describe(pluginDir, (sys.executable, str(FAKE)),
                                dev=True)

    def Start(self, spec, onEvent, onEnd):
        spec = dataclasses.replace(
            spec, argv=(sys.executable, str(FAKE), *spec.argv[1:]))
        return super().Start(spec, onEvent, onEnd)


class NoInputs:
    def Prepare(self, key, kind, inputsDir, pick):
        raise AssertionError("the job has no source")


class NoSettings:
    def Missing(self, pluginId, manifest, *, dev):
        return ()


class NoSecrets:
    def Environment(self, pluginId, *, dev):
        return {}

    def Forget(self, pluginId, *, dev):
        pass


def _plugin_zip_bytes():
    raw = {
        "manifest": 1, "id": PLUGIN, "version": "1.0.0", "name": "Tool",
        "author": "example", "license": "MIT", "min_autona": "2.6.0",
        "protocol": [1], "platforms": ["windows-x64"],
        "entry": ["bin/tool.exe"],
        "permissions": {"network": [], "files": "inputs", "spawn": False,
                        "autona_api": "none"},
        "jobs": [{"id": "run", "title": "Run", "lane": "heavy",
                  "timeout_seconds": 600,
                  "params": [{"key": "mode", "type": "text",
                              "label": "Mode", "default": "ok"}]}],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{PLUGIN}/plugin.json", json.dumps(raw))
        archive.writestr(f"{PLUGIN}/bin/tool.exe", b"MZ" + b"0" * 64)
        archive.writestr(f"{PLUGIN}/LICENSE", b"MIT")
    return buffer.getvalue()


def _until(condition, within=120.0):
    deadline = time.monotonic() + within
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


class RealParts:
    """The real installer, registry, runs and runner (the fake worker)."""

    def __init__(self, tmp_path):
        self.app, self.user, self.work = tmp_path / "Autona", \
            tmp_path / "user_data", tmp_path / "work"
        self.app.mkdir()
        app, user, work = self.app, self.user, self.work
        self.runner = runner = WorkerRunner(poll=0.02, cancelGrace=5.0)
        parts = {}
        self.registry = registry = PluginRegistry(
            str(app), str(user), "2.6.0", describe=runner.Describe,
            isRunning=lambda pluginId: parts["jobs"].Running(pluginId),
            isEnabled=lambda pluginId: parts["installer"].IsEnabled(
                pluginId),
            installSource=lambda pluginId: getattr(
                parts["installer"].ReadInstallRecord(pluginId), "source",
                None))
        self.jobs = jobs = PluginJobs(
            runner=runner, registry=registry, inputs=NoInputs(),
            settings=NoSettings(), secrets=NoSecrets(),
            workDir=lambda: str(work), userDataDir=str(user),
            hostVersion="2.6.0", locale=lambda: "ja", apiUrl=lambda: None,
            onRunEnded=lambda record: None, onLaneFreed=lambda lane: None,
            environ=lambda: dict(os.environ))
        self.installer = installer = PluginInstaller(
            str(app), str(user), registry=registry, runner=runner,
            hostVersion="2.6.0", secrets=NoSecrets(), jobs=jobs,
            workDir=lambda: str(work))
        parts.update(jobs=jobs, installer=installer)
        self.hooks = FakeHooks()
        self.installs = PluginInstalls(installer, registry, jobs=jobs,
                                       hooks=self.hooks,
                                       executor=SyncExecutor())
        registry.Scan()
        self.installs.Sweep()

    def Install(self):
        installs = self.installs
        data = _plugin_zip_bytes()
        uploadId, _expires = installs.ReceiveUpload(io.BytesIO(data),
                                                    len(data))
        status = installs.Status(installs.StartInstall(uploadId).install_id)
        assert status.state is InstallState.CONSENT, status.failure
        status = installs.Consent(status.install_id,
                                  status.pending.consent_digest)
        status = installs.Status(status.install_id)
        assert status.state is InstallState.DONE, status.failure
        return status

    def StartSlow(self):
        record = self.jobs.Start(PLUGIN, "run", MANUAL, {"mode": "slow"},
                                 {}, {})
        assert isinstance(record, RunRecord), record
        assert self.jobs.Running(PLUGIN)
        return record


@windows
def test_a_disable_or_a_removal_stops_a_running_run(tmp_path):
    real = RealParts(tmp_path)
    jobs, installs, registry = real.jobs, real.installs, real.registry
    try:
        real.Install()

        # Disabled while it runs: the run is cancelled and ends.
        record = real.StartSlow()
        installs.SetEnabled(PLUGIN, False)
        assert registry.Get(PLUGIN).state is PluginState.DISABLED
        assert jobs.Get(PLUGIN, record.run_id).cancel_requested
        assert _until(lambda: not jobs.Running(PLUGIN))
        assert jobs.Get(PLUGIN, record.run_id).state is JobState.CANCELLED
        assert not isinstance(
            jobs.Start(PLUGIN, "run", MANUAL, {"mode": "slow"}, {}, {}),
            RunRecord)

        # Removed while it runs: cancel -> wait -> removed (the job runs
        # inline here: SyncExecutor).
        installs.SetEnabled(PLUGIN, True)
        record = real.StartSlow()
        started = installs.StartRemoval(PLUGIN, data=True, runs="delete")
        assert started.runs >= 1
        assert not jobs.Running(PLUGIN)
        assert registry.Get(PLUGIN) is None
        assert not (real.app / "plugins" / PLUGIN).exists()
        assert not (real.user / "plugins" / PLUGIN).exists()
        assert not (real.work / "plugins" / PLUGIN).exists()
        _refused(lambda: installs.Removal(PLUGIN), StartRefusal.NOT_FOUND)
        assert jobs.Hold(PLUGIN) is True  # the hold was given back
        jobs.Unhold(PLUGIN)
    finally:
        real.runner.Shutdown(5.0)


@windows
def test_install_consent_run_disable_enable_remove(tmp_path):
    real = RealParts(tmp_path)
    app, user, work = real.app, real.user, real.work
    runner, registry, jobs = real.runner, real.registry, real.jobs
    installs, hooks = real.installs, real.hooks
    try:
        # The upload, the sheet, the consent.
        data = _plugin_zip_bytes()
        uploadId, _expires = installs.ReceiveUpload(io.BytesIO(data),
                                                    len(data))
        status = installs.StartInstall(uploadId)
        status = installs.Status(status.install_id)
        assert status.state is InstallState.CONSENT, status.failure
        assert status.pending.plugin_id == PLUGIN
        status = installs.Consent(status.install_id,
                                  status.pending.consent_digest)
        status = installs.Status(status.install_id)
        assert status.state is InstallState.DONE, status.failure
        plugin = registry.Get(PLUGIN)
        assert plugin.badge is Badge.MANUAL
        assert plugin.state is PluginState.READY
        assert hooks.refreshes == 1
        assert not [name for name in os.listdir(app / "plugins")
                    if name.startswith(".upload-")]

        # A run of the installed plugin, to its end.
        record = jobs.Start(PLUGIN, "run", MANUAL, {"mode": "ok"}, {}, {})
        assert isinstance(record, RunRecord), record
        assert _until(lambda: jobs.Get(PLUGIN, record.run_id).state
                      is not JobState.RUNNING)
        assert jobs.Get(PLUGIN, record.run_id).state is JobState.DONE
        assert _until(lambda: not jobs.Running(PLUGIN))

        # Disabled: listed so, and nothing starts.
        installs.SetEnabled(PLUGIN, False)
        assert registry.Get(PLUGIN).state is PluginState.DISABLED
        refused = jobs.Start(PLUGIN, "run", MANUAL, {"mode": "ok"}, {}, {})
        assert not isinstance(refused, RunRecord)
        installs.SetEnabled(PLUGIN, True)
        assert registry.Get(PLUGIN).state is PluginState.READY

        # The removal: its folder, its user_data and its runs gone.
        removal = installs.Removal(PLUGIN)
        assert removal.runs == 1
        installs.StartRemoval(PLUGIN, data=True, runs="delete")
        assert registry.Get(PLUGIN) is None
        assert not (app / "plugins" / PLUGIN).exists()
        assert not (user / "plugins" / PLUGIN).exists()
        # The removal waited for the run's folder to be written (Running
        # covers job.json's write after the lane is freed).
        assert not (work / "plugins" / PLUGIN).exists()
        _refused(lambda: installs.Removal(PLUGIN), StartRefusal.NOT_FOUND)
        assert jobs.Hold(PLUGIN) is True  # the hold was given back
        jobs.Unhold(PLUGIN)
    finally:
        runner.Shutdown(5.0)
