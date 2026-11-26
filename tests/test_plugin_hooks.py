"""The hooks' dispatcher (ADR-009 D5, appendix D; lane L9).

Driven without its thread: a fake clock, a fake registry and a fake start
(PluginJobs.Start), and ``Pump()`` for one round of the dispatcher. One test
runs the real thread.
"""
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.core import PluginHooks as hooks_module
from src.core.PluginHooks import PluginHooks
from src.core.PluginTypes import (
    HOOK_HISTORY_PER_HOOK, Badge, ErrorEvent, EndReason, Field, Hook,
    HookDisabledReason, HookEvent, HookOutcome, Job, JobState, Lane,
    Manifest, ManifestReport, Option, Permissions, PluginState,
    RegisteredPlugin, Refused, RunRecord, StartRefusal, TriggerKind)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT_ROOT / "docs" / "plugins" / "inputs"

PLUGIN = "example.webhook"
MATCH_KEY = "post_match/match_decided"
IMAGE_KEY = "post_image/result_image_saved"


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _manifest(pluginId=PLUGIN):
    match = Job(
        id="post_match", title="Post a match", lane="light",
        timeout_seconds=20, memory_mb=128, manual=False,
        params=(
            Field(key="match", type="match_record", label="Match",
                  required=True),
            Field(key="style", type="select", label="Style",
                  default="short", required=True,
                  options=(Option("short", "Short"),
                           Option("games", "Games"))),
        ),
        hooks=(Hook(on="match_decided", label="Post each match"),))
    image = Job(
        id="post_image", title="Post an image", lane="light",
        timeout_seconds=60, memory_mb=256,
        params=(
            Field(key="image", type="result_image", label="Image",
                  required=True),
            Field(key="message", type="textarea", label="Message",
                  default="", max_length=10),
            Field(key="count", type="number", label="Count", default=1,
                  min=0, max=5, step=1),
        ),
        hooks=(Hook(on="result_image_saved", label="Send each image"),))
    return Manifest(
        manifest=1, id=pluginId, version="1.0.0", name="Webhook",
        author="example", license="MIT", min_autona="2.6.0",
        protocol=(1,), platforms=("windows-x64",), entry=("bin/w.exe",),
        permissions=Permissions(network=(), files="inputs", spawn=False,
                                autona_api="none"),
        jobs=(match, image))


class _Registry:
    def __init__(self):
        self.plugins = {}
        self.unusable = set()

    def add(self, pluginId=PLUGIN, badge=Badge.MANUAL):
        manifest = _manifest(pluginId)
        self.plugins[pluginId] = RegisteredPlugin(
            id=pluginId, folder=f"C:\\plugins\\{pluginId}", badge=badge,
            report=ManifestReport(manifest), state=PluginState.READY)

    def Get(self, pluginId):
        return self.plugins.get(pluginId)

    def HookJobs(self, event):
        return [(pluginId, job)
                for pluginId, plugin in sorted(self.plugins.items())
                if pluginId not in self.unusable
                for job in plugin.report.manifest.jobs
                if any(hook.on == event for hook in job.hooks)]


class _Jobs:
    """PluginJobs.Start as the dispatcher sees it."""

    def __init__(self):
        self.calls = []
        self.busy = False
        # A refusal to answer (instead of a run), once.
        self.refuse = None
        self.started = threading.Event()

    def start(self, pluginId, jobId, trigger, params, settings, picks):
        if self.busy:
            return Refused(StartRefusal.BUSY, remaining_seconds=3)
        if self.refuse is not None:
            refused, self.refuse = self.refuse, None
            return Refused(refused)
        runId = f"20261126-000000-{len(self.calls):04d}"
        self.calls.append((pluginId, jobId, trigger, dict(params),
                           dict(settings), dict(picks), runId))
        self.started.set()
        return RunRecord(run_id=runId, plugin=pluginId, job=jobId,
                         trigger=trigger, lane=Lane.LIGHT,
                         state=JobState.RUNNING,
                         started_at="2026-11-26T00:00:00+09:00")


def _record(runId, state, *, reason=None, error=None, summary=None):
    return RunRecord(run_id=runId, plugin=PLUGIN, job="post_match",
                     trigger=None, lane=Lane.LIGHT, state=state,
                     started_at="2026-11-26T00:00:00+09:00", reason=reason,
                     error=error, summary=summary)


@pytest.fixture
def setup(tmp_path):
    registry = _Registry()
    registry.add()
    jobs = _Jobs()
    clock = _Clock()

    def make(**kwargs):
        kwargs.setdefault("busy", lambda: jobs.busy)
        kwargs.setdefault("settings", lambda pluginId: {"mention": "none"})
        kwargs.setdefault("clock", clock)
        return PluginHooks(str(tmp_path / "user_data"), registry,
                           jobs.start, **kwargs)

    return registry, jobs, clock, make, tmp_path


def _enabled(make, *keys, **params):
    dispatcher = make()
    for key in keys:
        status = dispatcher.SetEnabled(PLUGIN, key, True, params.get(key, {}))
        assert not isinstance(status, Refused), status
    return dispatcher


def _status(dispatcher, key):
    return next(s for s in dispatcher.Status(PLUGIN) if s.key == key)


def _line(score):
    return {"id": "20261126-215230-1-a3f9", "score": score, "closed": False}


# ── match_decided: the 5 seconds ────────────────────────────────────────

def test_a_candidate_fires_after_five_seconds(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, MATCH_KEY)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.Pump()
    clock.t += 4.9
    dispatcher.Pump()
    assert jobs.calls == []
    clock.t += 0.1
    dispatcher.Pump()
    [(pluginId, jobId, trigger, params, settings, picks, _)] = jobs.calls
    assert (pluginId, jobId) == (PLUGIN, "post_match")
    assert trigger.kind == TriggerKind.HOOK
    assert trigger.event == HookEvent.MATCH_DECIDED
    assert trigger.hook == MATCH_KEY
    assert params == {"style": "short"}
    assert settings == {"mention": "none"}
    assert picks["match"].record == _line([3, 1])
    assert picks["match"].kind == "match_record"


def test_a_candidate_again_replaces_the_line_and_keeps_the_first_wait(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, MATCH_KEY)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.Pump()
    clock.t += 3
    dispatcher.MatchCandidate("rec-1", _line([3, 2]))
    dispatcher.Pump()
    clock.t += 2
    dispatcher.Pump()
    [call] = jobs.calls
    assert call[5]["match"].record == _line([3, 2])


def test_a_withdrawal_drops_the_wait_without_marking_the_record(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, MATCH_KEY)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.Pump()
    clock.t += 4
    dispatcher.MatchWithdrawn("rec-1")
    dispatcher.Pump()
    clock.t += 10
    dispatcher.Pump()
    assert jobs.calls == []
    # Decided again later: a new wait from that candidate.
    dispatcher.MatchCandidate("rec-1", _line([3, 0]))
    dispatcher.Pump()
    clock.t += 4
    dispatcher.Pump()
    assert jobs.calls == []
    clock.t += 1
    dispatcher.Pump()
    assert len(jobs.calls) == 1


def test_a_record_that_fired_ignores_its_candidates(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, MATCH_KEY)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.Pump()
    clock.t += 5
    dispatcher.Pump()
    dispatcher.MatchWithdrawn("rec-1")
    dispatcher.MatchCandidate("rec-1", _line([3, 2]))
    dispatcher.Pump()
    clock.t += 60
    dispatcher.Pump()
    assert len(jobs.calls) == 1


def test_a_disabled_hook_gets_nothing(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = make()
    assert not dispatcher.Wanted(HookEvent.MATCH_DECIDED)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    clock.t += 5
    dispatcher.Pump()
    assert jobs.calls == []


# ── pending ─────────────────────────────────────────────────────────────

def test_a_full_lane_keeps_the_newest_trigger_and_says_overwritten(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    clock.t += 1
    dispatcher.ResultImageSaved("C:\\out\\results\\b.png")
    dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.pending
    assert [h.outcome for h in status.history] == [HookOutcome.OVERWRITTEN]
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.Pump()
    [call] = jobs.calls
    assert call[5]["image"].path == "C:\\out\\results\\b.png"
    assert not _status(dispatcher, IMAGE_KEY).pending


def test_a_trigger_pending_ten_minutes_is_discarded(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    clock.t += 599
    dispatcher.Pump()
    assert _status(dispatcher, IMAGE_KEY).pending
    clock.t += 1
    dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.pending
    assert status.history[0].outcome == HookOutcome.DISCARDED
    assert status.history[0].reason == "expired"
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert jobs.calls == []


def test_a_manual_run_that_took_the_freed_lane_goes_first(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    # The lane frees, a manual run takes it before the dispatcher's start:
    # the start is refused busy and the hook stays pending.
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert jobs.calls == []
    assert _status(dispatcher, IMAGE_KEY).pending
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert len(jobs.calls) == 1


def test_two_hooks_on_one_trigger_each_keep_their_slot(setup):
    registry, jobs, clock, make, _ = setup
    registry.add("example.other")
    dispatcher = _enabled(make, IMAGE_KEY)
    assert not isinstance(dispatcher.SetEnabled(
        "example.other", IMAGE_KEY, True, {}), Refused)
    started = []

    def oneAtATime(*args):
        if started:
            return Refused(StartRefusal.BUSY)
        started.append(args[0])
        return jobs.start(*args)

    dispatcher._start = oneAtATime
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    [first] = started
    [second] = {PLUGIN, "example.other"} - {first}
    assert dispatcher.Status(second)[1].pending
    # The first run ended: the lane is free again.
    started.clear()
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert started == [second]


# ── the windows ─────────────────────────────────────────────────────────

def test_the_sixty_second_window_skips_the_seventh_start(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(7):
        dispatcher.ResultImageSaved(f"C:\\out\\results\\{n}.png")
        dispatcher.Pump()
        clock.t += 1
    assert len(jobs.calls) == 6
    status = _status(dispatcher, IMAGE_KEY)
    assert status.history[0].outcome == HookOutcome.SKIPPED_RATE_LIMIT
    assert not status.pending
    assert status.enabled
    # The window slides: 60 s after the first start, one more fits.
    clock.t = 1000.0 + 60
    dispatcher.ResultImageSaved("C:\\out\\results\\late.png")
    dispatcher.Pump()
    assert len(jobs.calls) == 7


def test_more_than_sixty_starts_in_an_hour_disable_the_hook(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(61):
        assert dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
        dispatcher.ResultImageSaved(f"C:\\out\\results\\{n}.png")
        dispatcher.Pump()
        clock.t += 11
    assert len(jobs.calls) == 61
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.enabled
    assert status.disabled_reason == HookDisabledReason.RATE_LIMIT
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    # The plugin's list state is the registry's: untouched.
    assert registry.Get(PLUGIN).state == PluginState.READY


def test_sixty_starts_an_hour_keep_the_hook(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(61):
        dispatcher.ResultImageSaved(f"C:\\out\\results\\{n}.png")
        dispatcher.Pump()
        # 61 starts over more than an hour: never more than 60 in one.
        clock.t += 61
    assert _status(dispatcher, IMAGE_KEY).enabled


# ── failures ────────────────────────────────────────────────────────────

def _run_once(dispatcher, clock, n):
    dispatcher.ResultImageSaved(f"C:\\out\\results\\{n}.png")
    dispatcher.Pump()
    clock.t += 30


def test_five_failures_in_a_row_disable_the_hook(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    error = ErrorEvent(code="bad_url", message="The URL is wrong")
    ends = [
        _record("r0", JobState.FAILED, reason=EndReason.TIMED_OUT),
        _record("r1", JobState.ERROR, error=error),
        _record("r2", JobState.CANCELLED),
        _record("r3", JobState.FAILED, reason=EndReason.ABNORMAL_EXIT),
        _record("r4", JobState.FAILED, reason=EndReason.LAUNCH_FAILED),
    ]
    for record in ends:
        dispatcher.RunEnded(PLUGIN, IMAGE_KEY, record)
        dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.failures == 4 and status.enabled
    # Missing settings: not a failure.
    jobs.refuse = StartRefusal.NEEDS_SETTINGS
    _run_once(dispatcher, clock, 0)
    status = _status(dispatcher, IMAGE_KEY)
    assert status.history[0].outcome == HookOutcome.NEEDS_SETTINGS
    assert status.failures == 4 and status.enabled
    dispatcher.RunEnded(PLUGIN, IMAGE_KEY,
                        _record("r5", JobState.ERROR, error=error))
    dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.enabled
    assert status.disabled_reason == HookDisabledReason.FAILURES
    assert status.last_error == error
    assert status.history[0].outcome == HookOutcome.ERROR
    assert status.history[0].reason == "bad_url"
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)


def test_a_success_sets_the_failures_back_to_zero(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(4):
        dispatcher.RunEnded(PLUGIN, IMAGE_KEY, _record(
            f"f{n}", JobState.FAILED, reason=EndReason.ABNORMAL_EXIT))
    dispatcher.RunEnded(PLUGIN, IMAGE_KEY,
                        _record("ok", JobState.DONE, summary="Posted"))
    for n in range(4):
        dispatcher.RunEnded(PLUGIN, IMAGE_KEY, _record(
            f"g{n}", JobState.FAILED, reason=EndReason.TIMED_OUT))
    dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.enabled and status.failures == 4
    done = status.history[4]
    assert (done.outcome, done.summary, done.run_id) == (
        HookOutcome.DONE, "Posted", "ok")
    assert status.history[0].reason == "timed_out"


def test_the_history_keeps_the_newest_twenty(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(HOOK_HISTORY_PER_HOOK + 5):
        dispatcher.RunEnded(PLUGIN, IMAGE_KEY,
                            _record(f"r{n}", JobState.DONE))
    dispatcher.Pump()
    history = _status(dispatcher, IMAGE_KEY).history
    assert len(history) == HOOK_HISTORY_PER_HOOK
    assert history[0].run_id == f"r{HOOK_HISTORY_PER_HOOK + 4}"


def test_a_refused_start_other_than_busy_drops_the_trigger(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.refuse = StartRefusal.BLOCKED
    _run_once(dispatcher, clock, 0)
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.pending and status.failures == 0
    assert status.history[0].outcome == HookOutcome.DISCARDED
    assert status.history[0].reason == "blocked"


# ── enabling and the file ───────────────────────────────────────────────

def test_hooks_json_round_trips(setup):
    registry, jobs, clock, make, tmp_path = setup
    dispatcher = _enabled(make, IMAGE_KEY,
                          **{IMAGE_KEY: {"message": "hi", "count": 2}})
    error = ErrorEvent(code="bad_url", message={"ja": "違う", "en": "Wrong"},
                       hint="Check it")
    dispatcher.RunEnded(PLUGIN, IMAGE_KEY,
                        _record("r1", JobState.ERROR, error=error))
    dispatcher.Pump()
    before = dispatcher.Status(PLUGIN)
    path = tmp_path / "user_data" / "plugins" / PLUGIN / "hooks.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["hooks"][IMAGE_KEY]["params"] == {"message": "hi",
                                                   "count": 2}
    again = make()
    again.Refresh()
    assert again.Status(PLUGIN) == before
    assert again.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    assert not again.Wanted(HookEvent.MATCH_DECIDED)


def test_a_dev_plugin_keeps_its_hooks_apart(setup):
    registry, jobs, clock, make, tmp_path = setup
    registry.add(badge=Badge.DEV)
    _enabled(make, IMAGE_KEY)
    root = tmp_path / "user_data" / "plugins"
    assert (root / "_dev" / PLUGIN / "hooks.json").is_file()
    assert not (root / PLUGIN).exists()


def test_enabling_checks_the_values(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = make()
    for values, detail in [({"nope": 1}, "unknown key nope"),
                           ({"message": "x" * 11}, "message does not fit"),
                           ({"count": 1.5}, "count does not fit"),
                           ({"count": 9}, "count does not fit"),
                           ({"count": True}, "count does not fit")]:
        refused = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, values)
        assert refused == Refused(StartRefusal.INVALID_REQUEST,
                                  detail=detail)
    refused = dispatcher.SetEnabled(PLUGIN, MATCH_KEY, True,
                                    {"style": "long"})
    assert refused.reason == StartRefusal.INVALID_REQUEST
    assert dispatcher.SetEnabled(PLUGIN, "post_match/result_image_saved",
                                 True, {}).reason == (
        StartRefusal.INVALID_REQUEST)
    assert dispatcher.SetEnabled("no.plugin", IMAGE_KEY, True, {}).reason == (
        StartRefusal.NOT_FOUND)
    registry.unusable.add(PLUGIN)
    assert dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {}).reason == (
        StartRefusal.JOB_UNUSABLE)
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    registry.unusable.clear()
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {})
    assert dict(status.params) == {"message": "", "count": 1}


def test_disabling_drops_the_pending_trigger_and_keeps_the_values(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY, **{IMAGE_KEY: {"message": "hi"}})
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, False, {})
    assert not status.enabled and not status.pending
    assert status.disabled_reason == HookDisabledReason.USER
    assert status.params["message"] == "hi"
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)


def test_forget_disables_every_hook(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY, MATCH_KEY,
                          **{MATCH_KEY: {"style": "games"}})
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    dispatcher.Forget(PLUGIN, HookDisabledReason.NEW_INSTALL)
    for status in dispatcher.Status(PLUGIN):
        assert not status.enabled and not status.pending
        assert status.disabled_reason == HookDisabledReason.NEW_INSTALL
    assert dict(_status(dispatcher, MATCH_KEY).params) == {}
    assert not dispatcher.Wanted(HookEvent.MATCH_DECIDED)
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)


# ── when nothing runs ───────────────────────────────────────────────────

def test_shutdown_drops_what_is_pending_and_starts_nothing_after(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY, MATCH_KEY)
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.Pump()
    dispatcher.Shutdown()
    assert not any(s.pending for s in dispatcher.Status(PLUGIN))
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.ResultImageSaved("C:\\out\\results\\b.png")
    clock.t += 10
    dispatcher.Pump()
    assert jobs.calls == []
    assert dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {}).reason == (
        StartRefusal.UNAVAILABLE)


def test_the_headless_shell_runs_no_hook(setup):
    registry, jobs, clock, make, tmp_path = setup
    _enabled(make, IMAGE_KEY, MATCH_KEY)
    headless = make(headless=True)
    headless.Start()
    assert headless._thread is None
    assert not headless.Wanted(HookEvent.MATCH_DECIDED)
    assert not headless.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    headless.MatchCandidate("rec-1", _line([3, 1]))
    headless.ResultImageSaved("C:\\out\\results\\a.png")
    clock.t += 10
    headless.Pump()
    assert jobs.calls == []
    assert headless.SetEnabled(PLUGIN, IMAGE_KEY, True, {}).reason == (
        StartRefusal.UNAVAILABLE)
    assert headless.TrySample(PLUGIN, MATCH_KEY).reason == (
        StartRefusal.UNAVAILABLE)


def test_the_triggers_inside_the_state_lock_only_queue(setup, monkeypatch):
    """Wanted / MatchCandidate / MatchWithdrawn run holding
    StateManager.lock: they must neither do I/O nor wait on the
    dispatcher's own state lock (held here by a dispatch in progress)."""
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY, MATCH_KEY)

    def noIo(*args, **kwargs):
        raise AssertionError("I/O inside StateManager.lock")

    stateLock = threading.Lock()
    stateLock.acquire()
    dispatcher._stateLock = stateLock
    monkeypatch.setattr("builtins.open", noIo)
    for name in ("replace", "makedirs", "stat", "listdir"):
        monkeypatch.setattr(hooks_module.os, name, noIo)
    answers = []

    def observer():
        answers.append(dispatcher.Wanted(HookEvent.MATCH_DECIDED))
        dispatcher.MatchCandidate("rec-1", _line([3, 1]))
        dispatcher.MatchCandidate("rec-1", _line([3, 2]))
        dispatcher.MatchWithdrawn("rec-1")
        dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
        dispatcher.LaneFreed()
        dispatcher.RunEnded(PLUGIN, IMAGE_KEY, _record("r", JobState.DONE))
        answers.append("done")

    thread = threading.Thread(target=observer)
    thread.start()
    thread.join(2)
    stateLock.release()
    monkeypatch.undo()
    assert not thread.is_alive()
    assert answers == [True, "done"]
    assert len(dispatcher._inbox) == 6


# ── the sample run (D.6) ────────────────────────────────────────────────

def test_a_dev_plugin_tries_the_golden_input_uncounted(setup):
    registry, jobs, clock, make, _ = setup
    registry.add(badge=Badge.DEV)
    dispatcher = make(samplesDir=str(SAMPLES))
    record = dispatcher.TrySample(PLUGIN, MATCH_KEY)
    assert isinstance(record, RunRecord)
    [call] = jobs.calls
    golden = json.loads((SAMPLES / "match_record.json").read_text(
        encoding="utf-8"))
    assert call[5]["match"].record == golden
    assert call[3] == {"style": "short"}
    assert call[2].hook == MATCH_KEY
    dispatcher.RunEnded(PLUGIN, MATCH_KEY,
                        _record(record.run_id, JobState.FAILED,
                                reason=EndReason.ABNORMAL_EXIT))
    dispatcher.Pump()
    status = _status(dispatcher, MATCH_KEY)
    assert status.failures == 0 and status.history == ()
    # No golden image: a result_image_saved sample is unavailable.
    assert dispatcher.TrySample(PLUGIN, IMAGE_KEY).reason == (
        StartRefusal.UNAVAILABLE)


def test_only_a_dev_plugin_tries_the_sample(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = make(samplesDir=str(SAMPLES))
    assert dispatcher.TrySample(PLUGIN, MATCH_KEY).reason == (
        StartRefusal.INVALID_REQUEST)
    assert jobs.calls == []


# ── the thread ──────────────────────────────────────────────────────────

def test_the_dispatcher_thread_starts_a_trigger_and_stops(setup):
    registry, jobs, clock, make, _ = setup
    _enabled(make, IMAGE_KEY)
    dispatcher = make(clock=time.monotonic)
    dispatcher.Start()
    try:
        assert dispatcher._thread.name == "AutonaPluginHooks"
        assert dispatcher._thread.daemon
        dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
        assert jobs.started.wait(5)
    finally:
        dispatcher.Shutdown()
    assert not dispatcher._thread.is_alive()
    assert os.path.basename(jobs.calls[0][5]["image"].path) == "a.png"
