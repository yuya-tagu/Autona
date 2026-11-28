"""The hooks wired as bootstrap_core() wires them (ADR-009 stage P7, I5):
the real dispatcher (PluginHooks) over the real runs (PluginJobs), the real
runner (PluginRunner), the real input sources (PluginInputs) and the fake
worker (tests/fixtures/fake_plugin_worker.py), its triggers and callbacks
through bootstrap's own functions (_plugin_run_ended, _plugin_lane_freed,
_result_image_saved).

The dispatcher is driven without its thread (``Pump`` with an injected
clock, as tests/test_plugin_hooks.py does): the 5 seconds of match_decided
are the clock's, the runs are real processes. The registry, settings and
secrets are fakes. Windows only (the runner's job object).
"""

import sys
import time
from pathlib import Path

import pytest

from src import bootstrap
from src.core import PluginRunner as R
from src.core.PluginHooks import PluginHooks
from src.core.PluginInputs import PluginInputs
from src.core.PluginJobs import PluginJobs
from src.core.PluginTypes import (
    HOOK_CONSECUTIVE_FAILURES_DISABLE, Badge, Field, Hook,
    HookDisabledReason, HookEvent, HookOutcome, Job, JobState, Lane,
    Manifest, ManifestReport, Permissions, PluginState, RegisteredPlugin)

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fixtures" / "fake_plugin_worker.py"
PLUGIN = "example.hooked"
MATCH_KEY = "post_match/match_decided"
IMAGE_KEY = "post_image/result_image_saved"
# A bound for failing, not a pace (the runs are BELOW_NORMAL).
LONG = 120.0

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the host platform only")


def _manifest():
    # A dev plugin: the absolute interpreter and worker are kept as given.
    mode = Field(key="mode", type="text", label="Mode", default="ok")
    return Manifest(
        manifest=1, id=PLUGIN, version="1.0.0", name="Hooked",
        author="someone", license="MIT", min_autona="2.0.0",
        protocol=(1,), platforms=("windows-x64",),
        entry=(sys.executable, str(FAKE)),
        permissions=Permissions(network=(), files="inputs", spawn=False,
                                autona_api="none"),
        jobs=(
            Job(id="post_match", title="Post a match", lane="light",
                timeout_seconds=30, manual=False,
                params=(Field(key="match", type="match_record",
                              label="Match", required=True), mode),
                hooks=(Hook(on="match_decided", label="Each match"),)),
            Job(id="post_image", title="Post an image", lane="light",
                timeout_seconds=30,
                params=(Field(key="image", type="result_image",
                              label="Image", required=True), mode),
                hooks=(Hook(on="result_image_saved", label="Each image"),)),
        ))


class FakeRegistry:
    def __init__(self, folder):
        self.plugin = RegisteredPlugin(
            id=PLUGIN, folder=str(folder), badge=Badge.DEV,
            report=ManifestReport(manifest=_manifest(), problems=()),
            state=PluginState.READY)

    def Get(self, pluginId):
        return self.plugin if pluginId == PLUGIN else None

    def HookJobs(self, event):
        return [(PLUGIN, job) for job in self.plugin.report.manifest.jobs
                if any(hook.on == event for hook in job.hooks)]


class FakeSettings:
    def Missing(self, pluginId, manifest, *, dev):
        return ()


class FakeSecrets:
    def Environment(self, pluginId, *, dev):
        return {}


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _until(condition, within=LONG):
    deadline = time.monotonic() + within
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


class _Env:
    def __init__(self, tmp):
        folder = tmp / "plugins" / PLUGIN
        folder.mkdir(parents=True)
        self.tmp = tmp
        self.out = tmp / "out"
        (self.out / "results").mkdir(parents=True)
        self.clock = _Clock()
        self.runner = R.PluginRunner(poll=0.02, cancelGrace=5.0)
        registry = FakeRegistry(folder)
        # The golden inputs, as bootstrap_core() gives both.
        samplesDir = str(ROOT / "docs" / "plugins" / "inputs")
        self.inputs = PluginInputs(
            outDir=str(self.out),
            matchLogDir=str(tmp / "user_data" / "match_log"),
            workDir=lambda: str(tmp / "work"), publishedState=lambda: {},
            flushMatchLog=lambda: True, openRecordDate=lambda: None,
            samplesDir=samplesDir)
        # The callbacks bootstrap_core() gives PluginJobs.
        self.jobs = PluginJobs(
            runner=self.runner, registry=registry, inputs=self.inputs,
            settings=FakeSettings(), secrets=FakeSecrets(),
            workDir=lambda: str(tmp / "work"),
            userDataDir=str(tmp / "user_data"), hostVersion="2.6.0",
            locale=lambda: "ja", apiUrl=lambda: None,
            onRunEnded=bootstrap._plugin_run_ended,
            onLaneFreed=bootstrap._plugin_lane_freed)
        self.hooks = PluginHooks(
            str(tmp / "user_data"), registry, self.jobs.Start,
            busy=lambda: self.jobs.BusyFor(Lane.LIGHT) is not None,
            settings=lambda pluginId: {}, clock=self.clock,
            samplesDir=samplesDir,
            userWaiting=lambda: self.jobs.UserWaiting(Lane.LIGHT))

    def Status(self, key):
        return next(s for s in self.hooks.Status(PLUGIN) if s.key == key)

    def Settled(self, key, count):
        """Pump until the hook's history has ``count`` run ends (the end
        is queued by onRunEnded from the runner's thread)."""
        def ended():
            self.hooks.Pump()
            return sum(1 for item in self.Status(key).history
                       if item.run_id is not None) >= count
        return _until(ended)


@pytest.fixture
def env(tmp_path, monkeypatch):
    made = _Env(tmp_path)
    # What bootstrap's callbacks read: the desktop's dispatcher.
    monkeypatch.setattr(PluginHooks, "instance", made.hooks)
    yield made
    made.hooks.Shutdown(1.0)
    made.jobs.Shutdown(2.0)
    made.runner.Shutdown(2.0)


def test_match_decided_runs_the_hook_five_seconds_after_its_candidate(env):
    assert env.hooks.SetEnabled(PLUGIN, MATCH_KEY, True, {}).enabled
    assert env.hooks.Wanted(HookEvent.MATCH_DECIDED)
    line = {"id": "20261127-120000-1-a1b2", "score": [3, 1], "closed": False}
    env.hooks.MatchCandidate(line["id"], line)
    env.hooks.Pump()
    env.clock.t += 4.9
    env.hooks.Pump()
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert not (env.tmp / "work" / "plugins" / PLUGIN).exists()
    env.clock.t += 0.1
    env.hooks.Pump()
    assert env.Settled(MATCH_KEY, 1), "the hook's run did not end"
    status = env.Status(MATCH_KEY)
    assert [item.outcome for item in status.history] == [HookOutcome.DONE]
    assert status.failures == 0 and status.enabled
    # A hook's run that succeeded leaves no folder (D.6).
    assert _until(lambda: not any(
        (env.tmp / "work" / "plugins" / PLUGIN).glob("2*")), 10)
    # One firing per record: the same record again starts nothing.
    env.hooks.MatchCandidate(line["id"], line)
    env.clock.t += 10
    env.hooks.Pump()
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert len(env.Status(MATCH_KEY).history) == 1


def test_a_saved_result_image_runs_the_hook_with_that_image(env):
    env.hooks.SetEnabled(PLUGIN, IMAGE_KEY, True, {"mode": "error"})
    image = env.out / "results" / "top8.png"
    image.write_bytes(b"\x89PNG top8")
    # As ResultService tells it (bootstrap's saved callback).
    bootstrap._result_image_saved(str(image))
    env.hooks.Pump()
    assert env.Settled(IMAGE_KEY, 1)
    [item] = env.Status(IMAGE_KEY).history
    assert item.outcome == HookOutcome.ERROR
    # The failed run's folder is kept (D.6): the image the host placed, the
    # log the page reads through the runs' routes.
    assert env.jobs.Get(PLUGIN, item.run_id) is not None
    assert env.jobs.LogTail(PLUGIN, item.run_id, 4096) is not None
    placed = list((env.tmp / "work" / "plugins" / PLUGIN / item.run_id
                   / "inputs").glob("image.*"))
    assert [path.read_bytes() for path in placed] == [b"\x89PNG top8"]


def test_no_enabled_hook_queues_no_result_image(env):
    image = env.out / "results" / "top8.png"
    image.write_bytes(b"\x89PNG")
    bootstrap._result_image_saved(str(image))
    env.hooks.Pump()
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert all(not status.history for status in env.hooks.Status(PLUGIN))


def test_five_failures_in_a_row_disable_the_hook(env):
    env.hooks.SetEnabled(PLUGIN, IMAGE_KEY, True, {"mode": "error"})
    image = env.out / "results" / "top8.png"
    image.write_bytes(b"\x89PNG")
    for n in range(1, HOOK_CONSECUTIVE_FAILURES_DISABLE + 1):
        env.hooks.ResultImageSaved(str(image))
        env.hooks.Pump()
        assert env.Settled(IMAGE_KEY, n), f"run {n} did not end"
        env.clock.t += 30
    status = env.Status(IMAGE_KEY)
    assert not status.enabled
    assert status.disabled_reason == HookDisabledReason.FAILURES
    assert status.last_error is not None
    assert status.last_error.code == "unsupported_host"
    assert not env.hooks.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    # Only the newest failed run's folder is kept, readable by its run id.
    newest = next(item for item in status.history
                  if item.outcome in (HookOutcome.ERROR, HookOutcome.FAILED))
    assert env.jobs.Get(PLUGIN, newest.run_id) is not None
    kept = [path.name for path in
            (env.tmp / "work" / "plugins" / PLUGIN).glob("2*")]
    assert kept == [newest.run_id]
    # Another image starts nothing now.
    env.hooks.ResultImageSaved(str(image))
    env.hooks.Pump()
    assert env.jobs.BusyFor(Lane.LIGHT) is None


def test_a_trigger_while_the_lane_is_busy_waits_for_it_to_free(env):
    # A manual run holds the light lane; the hook is pending until the
    # run's end frees the lane (onLaneFreed -> LaneFreed, D.3).
    env.hooks.SetEnabled(PLUGIN, IMAGE_KEY, True, {})
    image = env.out / "results" / "top8.png"
    image.write_bytes(b"\x89PNG")
    from src.core.PluginTypes import InputPick, Trigger, TriggerKind
    manual = env.jobs.Start(
        PLUGIN, "post_image", Trigger(kind=TriggerKind.MANUAL),
        {"mode": "slow"}, {},
        {"image": InputPick("image", "result_image", choice="top8.png")})
    assert manual.run_id
    env.hooks.ResultImageSaved(str(image))
    env.hooks.Pump()
    assert env.Status(IMAGE_KEY).pending
    assert env.Status(IMAGE_KEY).history == ()
    assert env.jobs.Cancel(PLUGIN, manual.run_id)
    assert env.Settled(IMAGE_KEY, 1), "the pending hook never started"
    assert env.Status(IMAGE_KEY).history[0].outcome == HookOutcome.DONE


def test_a_sample_run_that_succeeded_stays_readable(env):
    # A dev plugin's 'try with the sample input' (D.6): uncounted, and its
    # folder kept like a manual run's, so GET runs/<id> and its log answer
    # after it ends (a hook run that succeeded would leave nothing).
    record = env.hooks.TrySample(PLUGIN, IMAGE_KEY)
    assert record.run_id

    def done():
        env.hooks.Pump()
        got = env.jobs.Get(PLUGIN, record.run_id)
        return got is not None and got.state is JobState.DONE
    assert _until(done), "the sample run did not end done"
    # Its end reached the dispatcher (onRunEnded) and was not counted.

    def told():
        env.hooks.Pump()
        return record.run_id not in env.hooks._samples
    assert _until(told, 10)
    status = env.Status(IMAGE_KEY)
    assert status.history == () and status.failures == 0
    assert env.jobs.Get(PLUGIN, record.run_id) is not None
    assert env.jobs.LogTail(PLUGIN, record.run_id, 4096) is not None
    folder = env.tmp / "work" / "plugins" / PLUGIN / record.run_id
    assert (folder / "job.json").is_file()
    # The keeping rules ran (the end's _Prune) and left it; not listed.
    assert record.run_id not in [run.run_id
                                 for run in env.jobs.List(PLUGIN)]


def test_a_result_image_gone_before_its_start_keeps_the_hook_on(env):
    # The real input check refuses it (INPUT_UNAVAILABLE): discarded, not
    # params_changed; the lane given back.
    env.hooks.SetEnabled(PLUGIN, IMAGE_KEY, True, {})
    bootstrap._result_image_saved(str(env.out / "results" / "gone.png"))
    env.hooks.Pump()
    status = env.Status(IMAGE_KEY)
    assert status.enabled and status.disabled_reason is None
    [item] = status.history
    assert item.outcome == HookOutcome.DISCARDED
    assert item.reason == "input_unavailable"
    assert env.jobs.BusyFor(Lane.LIGHT) is None


def test_a_request_refused_busy_goes_before_the_pending_hook(env):
    # D.3 over the real runs: the user pressed while the lane was held; when
    # it frees the hook waits the grace, and the request pressed again wins.
    from src.core.PluginTypes import InputPick, Trigger, TriggerKind
    from src.core.PluginHooks import USER_GRACE_SECONDS
    env.hooks.SetEnabled(PLUGIN, IMAGE_KEY, True, {})
    image = env.out / "results" / "top8.png"
    image.write_bytes(b"\x89PNG")

    def manual():
        return env.jobs.Start(
            PLUGIN, "post_image", Trigger(kind=TriggerKind.MANUAL),
            {"mode": "slow"}, {},
            {"image": InputPick("image", "result_image", choice="top8.png")})
    first = manual()
    env.hooks.ResultImageSaved(str(image))
    env.hooks.Pump()
    assert env.Status(IMAGE_KEY).pending
    assert manual().reason.value == "busy"
    assert env.jobs.Cancel(PLUGIN, first.run_id)
    assert _until(lambda: env.jobs.UserWaiting(Lane.LIGHT))
    env.hooks.Pump()
    assert env.jobs.BusyFor(Lane.LIGHT) is None
    assert env.Status(IMAGE_KEY).pending
    # Pressed again within the grace: the user's run takes the lane.
    again = manual()
    assert again.run_id
    env.clock.t += USER_GRACE_SECONDS
    env.hooks.Pump()
    assert env.Status(IMAGE_KEY).history == ()
    assert env.jobs.Cancel(PLUGIN, again.run_id)
    assert env.Settled(IMAGE_KEY, 1), "the pending hook never started"
