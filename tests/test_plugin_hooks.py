"""The hooks' dispatcher (ADR-009 D5, appendix D; lane L9).

Driven without its thread: a fake clock, a fake registry and a fake start
(PluginJobs.Start), and ``Pump()`` for one round of the dispatcher. One test
runs the real thread.
"""
import dataclasses
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.core import PluginHooks as hooks_module
from src.core.PluginHooks import PluginHooks
from src.core.PluginTypes import (
    HOOK_HISTORY_PER_HOOK, HOOK_RATE_WINDOW_MAX_STARTS, Badge, ErrorEvent, EndReason, Field, Hook,
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
        # A refusal to answer (instead of a run), once: a StartRefusal or
        # a Refused.
        self.refuse = None
        # The state of the record it answers (CANCELLED: one cancelled
        # while being prepared).
        self.endState = JobState.RUNNING
        self.started = threading.Event()
        # The run ids started with sample=True.
        self.sampled = []

    def start(self, pluginId, jobId, trigger, params, settings, picks, *,
              sample=False):
        if self.busy:
            return Refused(StartRefusal.BUSY, remaining_seconds=3)
        if self.refuse is not None:
            refused, self.refuse = self.refuse, None
            return refused if isinstance(refused, Refused)                 else Refused(refused)
        runId = f"20261126-000000-{len(self.calls):04d}"
        self.calls.append((pluginId, jobId, trigger, dict(params),
                           dict(settings), dict(picks), runId))
        if sample:
            self.sampled.append(runId)
        self.started.set()
        return RunRecord(run_id=runId, plugin=pluginId, job=jobId,
                         trigger=trigger, lane=Lane.LIGHT,
                         state=self.endState,
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
    for values, detail in [({"nope": 1}, "nope: no such value"),
                           ({"message": "x" * 11}, "message: too long"),
                           ({"count": 1.5}, "count: not on step"),
                           ({"count": 9}, "count: above max"),
                           ({"count": True}, "count: not a number")]:
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
    # Marked a sample: PluginJobs keeps its folder like a manual run's.
    assert jobs.sampled == [record.run_id]
    dispatcher.RunEnded(PLUGIN, MATCH_KEY,
                        _record(record.run_id, JobState.FAILED,
                                reason=EndReason.ABNORMAL_EXIT))
    dispatcher.Pump()
    status = _status(dispatcher, MATCH_KEY)
    assert status.failures == 0 and status.history == ()
    # The stand-in golden image: a result_image_saved sample starts too.
    assert isinstance(dispatcher.TrySample(PLUGIN, IMAGE_KEY), RunRecord)
    assert jobs.calls[1][5]["image"].path == str(SAMPLES / "result_image.png")
    # Without the sample file it is unavailable.
    dispatcher._samplesDir = str(SAMPLES / "missing")
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


# ── hooks.json that cannot be taken in (review M1 / S4) ─────────────────

def _hooks_file(tmp_path, dev=False):
    root = tmp_path / "user_data" / "plugins"
    return (root / "_dev" if dev else root) / PLUGIN / "hooks.json"


@pytest.mark.parametrize("content", ["[]", '{"hooks": []}', "not json {"])
def test_a_damaged_hooks_json_is_moved_aside(setup, content):
    registry, jobs, clock, make, tmp_path = setup
    path = _hooks_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    dispatcher = make()
    dispatcher.Start()
    try:
        assert not any(s.enabled for s in dispatcher.Status(PLUGIN))
        [backup] = path.parent.glob("hooks.json.unreadable-*.bak")
        assert backup.read_text(encoding="utf-8") == content
        assert not path.exists()
        assert not isinstance(
            dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {}), Refused)
        assert IMAGE_KEY in json.loads(path.read_text(encoding="utf-8"))[
            "hooks"]
    finally:
        dispatcher.Shutdown()


def test_a_hook_entry_of_another_shape_is_left_out_alone(setup):
    registry, jobs, clock, make, tmp_path = setup
    path = _hooks_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": {
        IMAGE_KEY: {"enabled": True, "params": {"message": "hi"},
                    "history": 5, "failures": "x", "last_error": 3},
        MATCH_KEY: 7}}), encoding="utf-8")
    dispatcher = make()
    dispatcher.Refresh()
    image = _status(dispatcher, IMAGE_KEY)
    assert image.enabled and image.history == () and image.failures == 0
    assert not _status(dispatcher, MATCH_KEY).enabled
    assert dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    assert not list(path.parent.glob("*.bak"))


def test_a_hooks_json_that_cannot_be_opened_is_not_written_over(
        setup, monkeypatch):
    registry, jobs, clock, make, tmp_path = setup
    _enabled(make, IMAGE_KEY, MATCH_KEY)
    path = _hooks_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    realOpen = open

    def locked(file, *args, **kwargs):
        if str(file) == str(path):
            raise PermissionError("held by another process")
        return realOpen(file, *args, **kwargs)

    monkeypatch.setattr(hooks_module, "open", locked, raising=False)
    dispatcher = make()
    dispatcher.Refresh()
    assert not dispatcher.Wanted(HookEvent.MATCH_DECIDED)
    refused = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, False, {})
    assert refused == Refused(StartRefusal.UNAVAILABLE,
                              detail="hooks.json unreadable")
    assert path.read_text(encoding="utf-8") == before
    assert not list(path.parent.glob("*.bak"))
    # Readable again: read on the next access, nothing lost.
    monkeypatch.undo()
    assert _status(dispatcher, MATCH_KEY).enabled
    assert dispatcher.Wanted(HookEvent.MATCH_DECIDED)


def test_a_read_again_that_fails_keeps_memory(setup):
    registry, jobs, clock, make, tmp_path = setup
    dispatcher = _enabled(make, IMAGE_KEY,
                          **{IMAGE_KEY: {"message": "hi"}})
    path = _hooks_file(tmp_path)
    path.write_text("not json {", encoding="utf-8")
    dispatcher.Refresh()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.enabled and status.params["message"] == "hi"
    assert dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    assert not list(path.parent.glob("*.bak"))
    # The next save writes what memory has.
    dispatcher.SetEnabled(PLUGIN, MATCH_KEY, True, {})
    saved = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    assert saved[IMAGE_KEY]["params"]["message"] == "hi"
    assert saved[MATCH_KEY]["enabled"] is True


def test_a_failed_write_keeps_memory_and_the_next_one_saves(
        setup, monkeypatch):
    registry, jobs, clock, make, tmp_path = setup
    dispatcher = make()

    def full(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(hooks_module.ImageImport, "WriteFile", full)
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {})
    assert status.enabled
    assert dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    path = _hooks_file(tmp_path)
    assert not path.exists()
    monkeypatch.undo()
    dispatcher.SetEnabled(PLUGIN, MATCH_KEY, True, {})
    saved = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    assert saved[IMAGE_KEY]["enabled"] and saved[MATCH_KEY]["enabled"]
    assert not list(path.parent.glob("*.tmp"))


def test_hooks_json_holds_no_setting(setup):
    registry, jobs, clock, make, tmp_path = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    _run_once(dispatcher, clock, 0)
    assert jobs.calls[0][4] == {"mention": "none"}
    text = _hooks_file(tmp_path).read_text(encoding="utf-8")
    assert "mention" not in text


def test_a_plugin_moved_between_dev_and_installed_shares_nothing(setup):
    registry, jobs, clock, make, tmp_path = setup
    registry.add(badge=Badge.DEV)
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    registry.add(badge=Badge.MANUAL)
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.enabled and not status.pending
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    dispatcher.SetEnabled(PLUGIN, MATCH_KEY, True, {})
    assert _hooks_file(tmp_path).is_file()
    dev = json.loads(_hooks_file(tmp_path, dev=True).read_text(
        encoding="utf-8"))["hooks"]
    assert MATCH_KEY not in dev


# ── shutdown during a dispatch (review M2) ──────────────────────────────

def test_shutdown_during_a_dispatch_keeps_its_timeout_and_no_start_follows(
        setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    took = []

    def busyThenClose():
        # The close comes while the dispatcher holds its state lock.
        began = time.monotonic()
        dispatcher.Shutdown(timeout=0.05)
        took.append(time.monotonic() - began)
        return False

    dispatcher._busy = busyThenClose
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    assert jobs.calls == []
    assert took and took[0] < 1.0


def test_a_sample_started_as_the_close_comes_is_not_started(setup):
    registry, jobs, clock, make, _ = setup
    registry.add(badge=Badge.DEV)
    dispatcher = make(samplesDir=str(SAMPLES))

    def settingsThenClose(pluginId):
        dispatcher.Shutdown(timeout=0.05)
        return {}

    dispatcher._settings = settingsThenClose
    assert dispatcher.TrySample(PLUGIN, MATCH_KEY).reason == (
        StartRefusal.UNAVAILABLE)
    assert jobs.calls == []


# ── one trigger at a time (review S1 / S6, N6, N7) ──────────────────────

def test_two_triggers_together_on_a_free_lane_lose_neither(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    realStart = jobs.start

    def fillsTheLane(*args):
        record = realStart(*args)
        jobs.busy = True
        return record

    dispatcher._start = fillsTheLane
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.ResultImageSaved("C:\\out\\results\\b.png")
    dispatcher.Pump()
    [call] = jobs.calls
    assert call[5]["image"].path == "C:\\out\\results\\a.png"
    status = _status(dispatcher, IMAGE_KEY)
    assert status.pending and status.history == ()
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert jobs.calls[1][5]["image"].path == "C:\\out\\results\\b.png"


def test_one_failing_item_does_not_lose_the_others(setup, monkeypatch):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY, MATCH_KEY)
    realTrigger = dispatcher._Trigger

    def broken(event, at, **kwargs):
        if event == HookEvent.RESULT_IMAGE_SAVED:
            raise RuntimeError("broken")
        return realTrigger(event, at, **kwargs)

    monkeypatch.setattr(dispatcher, "_Trigger", broken)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.MatchWithdrawn("rec-1")
    dispatcher.Pump()
    clock.t += 10
    dispatcher.Pump()
    # The withdrawal after the failing item was taken: nothing fired.
    assert jobs.calls == []


def test_a_start_that_raises_drops_that_trigger_only(setup):
    registry, jobs, clock, make, _ = setup
    registry.add("example.other")
    dispatcher = _enabled(make, IMAGE_KEY)
    dispatcher.SetEnabled("example.other", IMAGE_KEY, True, {})
    realStart = jobs.start

    def raisesForOne(pluginId, *args):
        if pluginId == PLUGIN:
            raise RuntimeError("broken start")
        return realStart(pluginId, *args)

    dispatcher._start = raisesForOne
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    assert [call[0] for call in jobs.calls] == ["example.other"]
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.pending and status.enabled
    assert status.history[0].reason == "start_failed"


def test_a_candidate_waits_from_when_it_was_queued(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, MATCH_KEY)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    # The dispatcher takes it 3 s late: the 5 s count from the queueing.
    clock.t += 3
    dispatcher.Pump()
    clock.t += 2
    dispatcher.Pump()
    assert len(jobs.calls) == 1


def test_the_fired_marks_kept_are_bounded(setup, monkeypatch):
    registry, jobs, clock, make, _ = setup
    monkeypatch.setattr(hooks_module, "FIRED_KEPT", 2)
    dispatcher = _enabled(make, MATCH_KEY)
    for n in range(3):
        dispatcher.MatchCandidate(f"rec-{n}", _line([3, n]))
        dispatcher.Pump()
        clock.t += 5
        dispatcher.Pump()
        clock.t += 10
    assert list(dispatcher._fired) == ["rec-1", "rec-2"]


# ── re-enabling, last error, saved values (review S2 / S3 / S5, N10) ────

def test_enabling_again_after_the_rate_limit_empties_the_windows(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(61):
        dispatcher.ResultImageSaved(f"C:\\out\\results\\{n}.png")
        dispatcher.Pump()
        clock.t += 11
    assert not _status(dispatcher, IMAGE_KEY).enabled
    dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {})
    for n in range(6):
        dispatcher.ResultImageSaved(f"C:\\out\\results\\again{n}.png")
        dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.enabled and len(jobs.calls) == 67


def test_a_failed_run_leaves_a_last_error_from_its_reason(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(5):
        dispatcher.RunEnded(PLUGIN, IMAGE_KEY, _record(
            f"r{n}", JobState.FAILED, reason=EndReason.TIMED_OUT))
    dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.disabled_reason == HookDisabledReason.FAILURES
    assert status.last_error.code == "timed_out"
    assert status.last_error.message["en"] == "Timed out"


def test_a_run_failed_because_autona_exited_counts_nothing(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(6):
        dispatcher.RunEnded(PLUGIN, IMAGE_KEY, _record(
            f"r{n}", JobState.FAILED, reason=EndReason.AUTONA_EXITED))
    dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.enabled and status.failures == 0
    assert status.last_error is None
    assert status.history[0].reason == "autona_exited"


def test_saved_values_that_no_longer_fit_disable_the_hook(setup):
    registry, jobs, clock, make, tmp_path = setup
    path = _hooks_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": {MATCH_KEY: {
        "enabled": True, "params": {"style": "long"}}}}), encoding="utf-8")
    dispatcher = make()
    dispatcher.Refresh()
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.Pump()
    clock.t += 5
    dispatcher.Pump()
    assert jobs.calls == []
    status = _status(dispatcher, MATCH_KEY)
    assert not status.enabled
    assert status.disabled_reason == HookDisabledReason.PARAMS_CHANGED
    assert status.history[0].reason == "invalid_params"
    assert not dispatcher.Wanted(HookEvent.MATCH_DECIDED)


def test_a_start_refused_invalid_disables_the_hook(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.refuse = StartRefusal.INVALID_REQUEST
    _run_once(dispatcher, clock, 0)
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.enabled and status.failures == 0
    assert status.disabled_reason == HookDisabledReason.PARAMS_CHANGED


def test_a_file_value_is_a_pick_id(setup, tmp_path):
    registry, jobs, clock, make, _ = setup
    picked = tmp_path / "logo.png"
    picked.write_bytes(b"png")
    plugin = registry.plugins[PLUGIN]
    manifest = plugin.report.manifest
    image = manifest.jobs[1]
    image = dataclasses.replace(image, params=image.params + (
        Field(key="logo", type="file", label="Logo", extensions=("png",)),))
    registry.plugins[PLUGIN] = dataclasses.replace(
        plugin, report=ManifestReport(dataclasses.replace(
            manifest, jobs=(manifest.jobs[0], image))))
    picks = {"pick-1": str(picked)}
    dispatcher = make(resolvePick=lambda kind, pickId: picks.get(pickId))
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True,
                                   {"logo": "pick-1"})
    assert status.params["logo"] == os.path.normpath(str(picked))
    assert dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {
        "logo": str(picked)}).detail == "logo: unknown pick id"
    # Kept as the path: the start checks it as stored.
    dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {"logo": "pick-1"})
    _run_once(dispatcher, clock, 0)
    assert jobs.calls[0][3]["logo"] == os.path.normpath(str(picked))


def test_a_file_value_left_out_keeps_the_saved_path(setup, tmp_path):
    # The page shows a saved file by its name and sends a pick id only when
    # the user picks again: a file / folder key left out of an enable keeps
    # the path saved before (not the default); one given replaces it.
    registry, jobs, clock, make, _ = setup
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    first.write_bytes(b"png")
    second.write_bytes(b"png")
    plugin = registry.plugins[PLUGIN]
    manifest = plugin.report.manifest
    image = manifest.jobs[1]
    image = dataclasses.replace(image, params=image.params + (
        Field(key="logo", type="file", label="Logo", extensions=("png",)),))
    registry.plugins[PLUGIN] = dataclasses.replace(
        plugin, report=ManifestReport(dataclasses.replace(
            manifest, jobs=(manifest.jobs[0], image))))
    picks = {"pick-1": str(first), "pick-2": str(second)}
    dispatcher = make(resolvePick=lambda kind, pickId: picks.get(pickId))
    dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {"logo": "pick-1"})
    dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, False, {})
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {"count": 2})
    assert status.params["logo"] == os.path.normpath(str(first))
    assert status.params["count"] == 2
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {"logo": "pick-2"})
    assert status.params["logo"] == os.path.normpath(str(second))
    # An explicit null is not "left out": the default (none) again.
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {"logo": None})
    assert status.params["logo"] is None
    # The start runs with the values saved last.
    _run_once(dispatcher, clock, 0)
    assert jobs.calls[0][3]["logo"] is None


# ── Refresh, samples, unusable, Forget (review S8) ──────────────────────

def test_refresh_keeps_the_pending_trigger_and_the_windows(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    for n in range(HOOK_RATE_WINDOW_MAX_STARTS):
        dispatcher.ResultImageSaved(f"C:\\out\\results\\{n}.png")
        dispatcher.Pump()
        clock.t += 1
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\held.png")
    dispatcher.Pump()
    dispatcher.Refresh()
    assert _status(dispatcher, IMAGE_KEY).pending
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.Pump()
    # Still 6 in the last 60 s: the held one is skipped.
    assert len(jobs.calls) == HOOK_RATE_WINDOW_MAX_STARTS
    assert _status(dispatcher, IMAGE_KEY).history[0].outcome == (
        HookOutcome.SKIPPED_RATE_LIMIT)


def test_samples_count_in_no_window(setup):
    registry, jobs, clock, make, _ = setup
    registry.add(badge=Badge.DEV)
    dispatcher = _enabled(make, MATCH_KEY)
    dispatcher._samplesDir = str(SAMPLES)
    for n in range(HOOK_RATE_WINDOW_MAX_STARTS):
        assert isinstance(dispatcher.TrySample(PLUGIN, MATCH_KEY), RunRecord)
    dispatcher.MatchCandidate("rec-1", _line([3, 1]))
    dispatcher.Pump()
    clock.t += 5
    dispatcher.Pump()
    assert len(jobs.calls) == HOOK_RATE_WINDOW_MAX_STARTS + 1


def test_a_pending_trigger_of_a_job_no_longer_usable_is_discarded(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    registry.unusable.add(PLUGIN)
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert jobs.calls == []
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.pending
    assert status.history[0].reason == "unusable"


@pytest.mark.parametrize("reason,keeps", [
    (HookDisabledReason.NEW_INSTALL, False),
    (HookDisabledReason.RECONSENT, False),
    (HookDisabledReason.BLOCKED, True),
    (HookDisabledReason.REMOVED, True),
    (HookDisabledReason.USER, True),
])
def test_forget_puts_the_values_back_only_for_a_new_consent(
        setup, reason, keeps):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, MATCH_KEY, **{MATCH_KEY: {"style": "games"}})
    dispatcher.RunEnded(PLUGIN, MATCH_KEY, _record(
        "r", JobState.FAILED, reason=EndReason.ABNORMAL_EXIT))
    dispatcher.Pump()
    dispatcher.Forget(PLUGIN, reason)
    status = _status(dispatcher, MATCH_KEY)
    assert not status.enabled and status.disabled_reason == reason
    assert dict(status.params) == ({"style": "games"} if keeps else {})
    assert status.failures == (1 if keeps else 0)
    # A new install is another plugin: no history, no last error.
    fresh = reason == HookDisabledReason.NEW_INSTALL
    assert (status.history == ()) == fresh
    assert (status.last_error is None) == fresh


# ── review fixes (I5) ───────────────────────────────────────────────────

def test_the_users_request_goes_first_when_the_lane_frees(setup):
    # D.3: a manual / action start was refused BUSY while the lane was
    # held; once it frees the pending hooks wait the grace, so the request
    # pressed again takes the lane.
    registry, jobs, clock, make, _ = setup
    waiting = {"user": False}
    dispatcher = _enabled(make, IMAGE_KEY)
    dispatcher._userWaiting = lambda: waiting["user"]
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    # The run ends; a manual start had been refused meanwhile.
    jobs.busy, waiting["user"] = False, True
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert jobs.calls == []
    assert dispatcher._NextDue() == clock.t + hooks_module.USER_GRACE_SECONDS
    clock.t += hooks_module.USER_GRACE_SECONDS - 0.1
    dispatcher.Pump()
    assert jobs.calls == []
    # The user pressed again: the lane is theirs, the hook stays pending.
    jobs.busy, waiting["user"] = True, False
    dispatcher.Pump()
    assert jobs.calls == [] and _status(dispatcher, IMAGE_KEY).pending
    # Their run ends with nobody refused: the hook starts at once.
    jobs.busy = False
    dispatcher.LaneFreed()
    dispatcher.Pump()
    assert len(jobs.calls) == 1


def test_the_grace_ends_and_the_hook_starts(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    dispatcher._userWaiting = lambda: True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    assert jobs.calls == []
    clock.t += hooks_module.USER_GRACE_SECONDS
    dispatcher.Pump()
    assert len(jobs.calls) == 1
    # Spent: a later trigger (the flag not reset by a fake lane) starts at
    # once.
    dispatcher.ResultImageSaved("C:\\out\\results\\b.png")
    clock.t += 1
    dispatcher.Pump()
    assert len(jobs.calls) == 2


def test_a_pending_trigger_due_to_expire_starts_when_the_lane_is_free(
        setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.busy = True
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    jobs.busy = False
    clock.t += hooks_module.HOOK_PENDING_MAX_SECONDS
    dispatcher.Pump()
    assert len(jobs.calls) == 1
    assert _status(dispatcher, IMAGE_KEY).history == ()


def test_an_input_that_cannot_be_filled_is_discarded_not_disabling(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    jobs.refuse = Refused(StartRefusal.INVALID_REQUEST,
                          detail=f"{hooks_module.INPUT_UNAVAILABLE}: image "
                                 "could not be filled")
    dispatcher.ResultImageSaved("C:\\out\\results\\gone.png")
    dispatcher.Pump()
    status = _status(dispatcher, IMAGE_KEY)
    assert status.enabled and status.disabled_reason is None
    assert status.failures == 0
    [item] = status.history
    assert item.outcome == HookOutcome.DISCARDED
    assert item.reason == "input_unavailable"


def test_forget_raises_when_it_cannot_save_and_refresh_keeps_it_off(
        setup, monkeypatch):
    registry, jobs, clock, make, tmp_path = setup
    dispatcher = _enabled(make, IMAGE_KEY)

    def full(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(hooks_module.ImageImport, "WriteFile", full)
    with pytest.raises(OSError):
        dispatcher.Forget(PLUGIN, HookDisabledReason.BLOCKED)
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)
    # The file still says enabled; reading again keeps memory's.
    dispatcher.Refresh()
    status = _status(dispatcher, IMAGE_KEY)
    assert not status.enabled
    assert status.disabled_reason == HookDisabledReason.BLOCKED
    # Writable again: the next read again saves it.
    monkeypatch.undo()
    dispatcher.Refresh()
    saved = json.loads(_hooks_file(tmp_path).read_text(encoding="utf-8"))
    assert saved["hooks"][IMAGE_KEY]["disabled_reason"] == "blocked"


def test_forget_raises_when_hooks_json_cannot_be_opened(setup, monkeypatch):
    registry, jobs, clock, make, tmp_path = setup
    _enabled(make, IMAGE_KEY)
    path = _hooks_file(tmp_path)
    realOpen = open

    def locked(file, *args, **kwargs):
        if str(file) == str(path):
            raise PermissionError("held by another process")
        return realOpen(file, *args, **kwargs)

    monkeypatch.setattr(hooks_module, "open", locked, raising=False)
    with pytest.raises(OSError):
        make().Forget(PLUGIN, HookDisabledReason.NEW_INSTALL)


def test_an_auto_disable_whose_save_failed_does_not_come_back(
        setup, monkeypatch):
    registry, jobs, clock, make, tmp_path = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    monkeypatch.setattr(hooks_module.ImageImport, "WriteFile",
                        lambda path, data: (_ for _ in ()).throw(
                            OSError("disk full")))
    jobs.refuse = StartRefusal.INVALID_REQUEST
    dispatcher.ResultImageSaved("C:\\out\\results\\a.png")
    dispatcher.Pump()
    assert (_status(dispatcher, IMAGE_KEY).disabled_reason
            == HookDisabledReason.PARAMS_CHANGED)
    dispatcher.Refresh()
    assert not _status(dispatcher, IMAGE_KEY).enabled
    assert not dispatcher.Wanted(HookEvent.RESULT_IMAGE_SAVED)


def test_a_new_install_keeps_its_reason_after_user_data_is_emptied(setup):
    registry, jobs, clock, make, tmp_path = setup
    dispatcher = _enabled(make, IMAGE_KEY, MATCH_KEY)
    dispatcher.Forget(PLUGIN, HookDisabledReason.NEW_INSTALL)
    # The installer empties user_data/plugins/<id>/, then refreshes.
    _hooks_file(tmp_path).unlink()
    dispatcher.Refresh()
    for status in dispatcher.Status(PLUGIN):
        assert not status.enabled
        assert status.disabled_reason == HookDisabledReason.NEW_INSTALL
        assert status.history == ()


def test_saving_the_values_of_an_enabled_hook_keeps_its_counts(setup):
    registry, jobs, clock, make, _ = setup
    dispatcher = _enabled(make, IMAGE_KEY)
    _run_once(dispatcher, clock, 0)
    dispatcher.RunEnded(PLUGIN, IMAGE_KEY,
                        _record(jobs.calls[0][6], JobState.ERROR,
                                error=ErrorEvent(code="x", message="m")))
    dispatcher.Pump()
    assert _status(dispatcher, IMAGE_KEY).failures == 1
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True,
                                   {"message": "hi"})
    assert status.failures == 1 and status.params["message"] == "hi"
    assert len(dispatcher._plugins[PLUGIN].hooks[IMAGE_KEY].starts) == 1
    # Off, then on again: a fresh start.
    dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, False, {})
    status = dispatcher.SetEnabled(PLUGIN, IMAGE_KEY, True, {})
    assert status.failures == 0
    assert len(dispatcher._plugins[PLUGIN].hooks[IMAGE_KEY].starts) == 0


def test_a_sample_cancelled_while_prepared_is_not_waited_for(setup):
    registry, jobs, clock, make, _ = setup
    registry.add(badge=Badge.DEV)
    dispatcher = make(samplesDir=str(SAMPLES))
    jobs.endState = JobState.CANCELLED
    record = dispatcher.TrySample(PLUGIN, MATCH_KEY)
    assert record.state == JobState.CANCELLED
    assert dispatcher._samples == set()
