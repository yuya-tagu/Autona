"""The hooks' dispatcher (ADR-009 D5, appendix D; lane L9): implements
``PluginTypes.PluginHooks``.

A hook is a job's ``hooks[]`` entry, keyed ``<job id>/<on>``. It is off by
default; the user turns it on (SHELL, desktop only) with the values of the
job's value fields it runs with. Two triggers start it:

- ``match_decided``: the match log's observer tells ``MatchCandidate(id,
  line)`` / ``MatchWithdrawn(id)`` (MatchLogService, ADR-009 D.1). A record
  fires MATCH_DECIDED_HOLD_SECONDS after its *first* candidate unless it is
  withdrawn before; a candidate again for a record waiting replaces the line
  only (the wait keeps counting from the first). A withdrawal drops the
  wait without marking the record, so a later candidate waits again. A
  record that fired is marked and its candidates are ignored after (one
  firing per record). The line handed to the job is the latest candidate's.
- ``result_image_saved``: ``ResultImageSaved(path)`` from the result render
  lane's completion, one per image.

What happens to a trigger (D.3, D.4; contract_1.json hooks.order):

1. each enabled hook on the event gets the trigger in its *pending slot*
   (one per hook, not a queue): a trigger already there is replaced and the
   history says ``overwritten``. A trigger left there
   HOOK_PENDING_MAX_SECONDS is dropped (``discarded``);
2. the pending slots are started oldest first while the light lane is free
   (``busy``, then the start's own BUSY refusal: a manual / action run that
   took the lane keeps the hook pending, so the user's runs go first);
3. right before a start, the 60 s sliding window: HOOK_RATE_WINDOW_MAX_STARTS
   started in it already skips this one (``skipped_rate_limit``; not kept
   pending);
4. right after a start, the 1 h sliding window: more than
   HOOK_HOUR_WINDOW_MAX_STARTS in it disables the hook (``rate_limit``).

A run's end (``RunEnded``, from PluginJobs' onRunEnded): ``done`` sets the
failures back to 0; ``error`` and ``failed`` (timed_out included) add one,
and HOOK_CONSECUTIVE_FAILURES_DISABLE in a row disable the hook
(``failures``); ``cancelled`` counts nothing. A start refused for missing
settings is ``needs_settings`` in the history and counts nothing (D.5). An
auto-disable changes the hook's row only, never the plugin list's state.

What is kept per hook in ``user_data/plugins/<id>/hooks.json``
(``user_data/plugins/_dev/<id>/`` for a dev plugin): enabled, the values,
why it is off, the failures in a row, the last error and the newest
HOOK_HISTORY_PER_HOOK history entries (newest first). The windows' starts,
the pending slots and the match candidates are memory only.

When nothing runs (D.7): the headless shell (``headless`` at construction:
nothing is queued, nothing can be enabled), after ``Shutdown`` (the
dispatcher stops and what is pending is dropped), and an event no enabled
hook is on (``Wanted`` false; ``ResultImageSaved`` asks it itself, the match
log asks it once per decided record).

The dev plugins' 'try with the sample input' (D.6) is ``TrySample``: one
start with the golden input of ``samplesDir`` (docs/plugins/inputs/), not
counted in the windows nor the failures.

Threads and locks
-----------------
One dispatcher thread (``AutonaPluginHooks``, daemon, started by ``Start``).
``Wanted`` / ``MatchCandidate`` / ``MatchWithdrawn`` are called holding
``StateManager.lock`` (the match log's observer), and ``ResultImageSaved`` /
``LaneFreed`` / ``RunEnded`` from whatever thread: all of them only touch
the inbox and the enabled-events flags under ``_cond`` -- a leaf lock (taken
after StateManager.lock, never the other way round; no I/O, no other lock,
no wait inside it). ``Wanted`` answers from those flags.

The dispatcher thread (``Pump``) and the SHELL calls (``SetEnabled`` /
``Status`` / ``Forget`` / ``TrySample`` / ``Refresh``) hold ``_stateLock``
for the hooks' state, their files and the starts: ``_stateLock`` ->
``_cond``, never the other way round, and never StateManager.lock under it.
The injected ``start`` runs under ``_stateLock``; a run's end it reports
synchronously (a launch failure) only queues, so it cannot deadlock.

Injected (no plugin part is imported): the registry (``Get`` /
``HookJobs``), the start (``PluginJobs.Start``), whether the light lane is
full, the settings of a plugin (``PluginSettings.Get``), the clocks.
Nothing is made, read or written on import (pitfalls #1); no Qt.
"""

import collections
import json
import math
import os
import threading
import time
from datetime import datetime
from types import MappingProxyType

from loguru import logger

from .PluginTypes import (
    HOOK_CONSECUTIVE_FAILURES_DISABLE, HOOK_FILLS, HOOK_HISTORY_PER_HOOK,
    HOOK_HOUR_WINDOW_MAX_STARTS, HOOK_HOUR_WINDOW_SECONDS,
    HOOK_PENDING_MAX_SECONDS, HOOK_RATE_WINDOW_MAX_STARTS,
    HOOK_RATE_WINDOW_SECONDS, MATCH_DECIDED_HOLD_SECONDS, SOURCE_FIELD_TYPES,
    TEXT_MAX_LENGTH, Badge, ErrorEvent, FieldType, HookDisabledReason,
    HookEvent, HookHistoryEntry, HookOutcome, HookStatus, InputPick,
    JobState, Refused, RunRecord, StartRefusal, Trigger, TriggerKind)

DISPATCHER_THREAD = "AutonaPluginHooks"
HOOKS_FILE = "hooks.json"
PLUGINS_DIR = "plugins"
DEV_DIR = "_dev"
# The golden input files of a sample run (docs/plugins/inputs/). There is
# no golden image: a result_image_saved sample needs this one in samplesDir.
SAMPLE_FILES = MappingProxyType({
    HookEvent.MATCH_DECIDED: "match_record.json",
    HookEvent.RESULT_IMAGE_SAVED: "result_image.png",
})

# History reasons of the host's own (besides an EndReason / error code /
# StartRefusal value).
REASON_EXPIRED = "expired"
REASON_UNUSABLE = "unusable"

_FAILURE_STATES = frozenset({JobState.ERROR, JobState.FAILED})


class _Hook:
    """One hook's state. Saved: enabled, params, disabledReason, failures,
    lastError, history. Memory: pending, starts."""

    __slots__ = ("enabled", "params", "disabledReason", "failures",
                 "lastError", "history", "pending", "starts")

    def __init__(self):
        self.enabled = False
        self.params = {}
        self.disabledReason = None
        self.failures = 0
        self.lastError = None
        self.history = []
        self.pending = None
        self.starts = collections.deque()


class _Pending:
    """A trigger waiting in a hook's slot: when it came (``at``, and
    ``seq`` for ties), the event and what fills the job's source field."""

    __slots__ = ("at", "seq", "event", "pick")

    def __init__(self, at, seq, event, pick):
        self.at = at
        self.seq = seq
        self.event = event
        self.pick = pick


class _Plugin:
    """A plugin's hooks as loaded from its hooks.json."""

    __slots__ = ("dev", "hooks")

    def __init__(self, dev):
        self.dev = dev
        self.hooks = {}


class PluginHooks:

    def __init__(self, userDataDir, registry, start, *, busy=None,
                 settings=None, clock=None, wallClock=None, headless=False,
                 samplesDir=None):
        """`userDataDir`: the ``user_data`` folder (hooks.json lives under
        its ``plugins/``). `registry`: a PluginRegistry (``Get`` /
        ``HookJobs``). `start`: PluginJobs.Start. `busy()`: whether the
        light lane is full (None: only the start's BUSY refusal says so).
        `settings(pluginId)`: the plugin's settings for request.json
        (PluginSettings.Get; None: ``{}``). `clock()`: monotonic seconds;
        `wallClock()`: an aware datetime for the history. `headless`: never
        queue nor run. `samplesDir`: the golden inputs for TrySample."""
        self._userDataDir = userDataDir
        self._registry = registry
        self._start = start
        self._busy = busy
        self._settings = settings
        self._clock = clock or time.monotonic
        self._wallClock = wallClock or (lambda: datetime.now().astimezone())
        self._headless = bool(headless)
        self._samplesDir = samplesDir

        # The leaf lock: the inbox, the enabled-events flags, the next due
        # time, stopping.
        self._cond = threading.Condition()
        self._inbox = collections.deque()
        self._wanted = frozenset()
        self._due = None
        self._stopping = False
        self._thread = None

        # The hooks' state, their files and the starts.
        self._stateLock = threading.Lock()
        self._plugins = {}
        # recordId -> [first candidate's clock, latest line]
        self._candidates = {}
        self._fired = set()
        self._seq = 0
        # Sample runs' ids: their ends count nothing.
        self._samples = set()

    # ── lifetime ─────────────────────────────────────────────────────────

    def Start(self):
        """Read the enabled hooks and start the dispatcher thread (not
        headless). Not called holding StateManager.lock."""
        if self._headless:
            return
        self.Refresh()
        with self._cond:
            if self._thread is None and not self._stopping:
                self._StartThread()
        logger.info("plugin hooks: started")

    def Shutdown(self, timeout=2.0):
        """Stop the dispatcher and drop what is pending (the candidates
        too); no start after it. Not called holding StateManager.lock (it
        waits for the thread)."""
        with self._cond:
            if self._stopping:
                return
            self._stopping = True
            self._inbox.clear()
            self._wanted = frozenset()
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        with self._stateLock:
            self._candidates.clear()
            for plugin in self._plugins.values():
                for hook in plugin.hooks.values():
                    hook.pending = None
        if thread is not None:
            logger.info("plugin hooks: stopped")

    def Refresh(self):
        """Read hooks.json of every plugin with a usable hook job (launch,
        and after the registry's Scan: a dev folder added back brings its
        enabled hooks). Not called holding StateManager.lock."""
        if self._headless:
            return
        ids = set()
        for event in HookEvent:
            ids.update(pluginId for pluginId, _job
                       in self._registry.HookJobs(event))
        with self._stateLock:
            for pluginId in sorted(ids):
                self._Loaded(pluginId, reload=True)
            self._Recount()

    # ── the triggers (only queue) ────────────────────────────────────────

    def Wanted(self, event):
        """Whether any enabled hook is on `event`. Memory only: called
        holding StateManager.lock."""
        with self._cond:
            return (not self._headless and not self._stopping
                    and str(event) in self._wanted)

    def MatchCandidate(self, recordId, line):
        """match_decided's candidate. Holding StateManager.lock: only
        queues (a shallow copy of `line`)."""
        self._Queue(("candidate", str(recordId), dict(line)))

    def MatchWithdrawn(self, recordId):
        """The candidate no longer holds. Holding StateManager.lock: only
        queues."""
        self._Queue(("withdrawn", str(recordId)))

    def ResultImageSaved(self, path):
        """One result image saved to out/results: queued when a hook on
        result_image_saved is enabled (D.7)."""
        if self.Wanted(HookEvent.RESULT_IMAGE_SAVED):
            self._Queue(("image", str(path)))

    def LaneFreed(self):
        """The light lane is free: try the pending hooks."""
        self._Queue(("freed",))

    def RunEnded(self, pluginId, hookKey, record):
        """A hook's run ended (PluginJobs' onRunEnded): counted on the
        dispatcher thread."""
        self._Queue(("ended", pluginId, hookKey, record))

    def _Queue(self, item):
        with self._cond:
            if self._headless or self._stopping:
                return
            self._inbox.append(item)
            self._cond.notify_all()

    # ── the SHELL calls ──────────────────────────────────────────────────

    def SetEnabled(self, pluginId, hookKey, enabled, params):
        """Enable a hook with the values it runs with (every value field of
        the job: a missing one is its default; checked by type), or disable
        it (``params`` ignored; its saved values stay for the next enable).
        UNAVAILABLE headless or after Shutdown."""
        if self._headless or self._Stopping():
            return Refused(StartRefusal.UNAVAILABLE)
        with self._stateLock:
            plugin, job, hook = self._Find(pluginId, hookKey)
            if plugin is None:
                return Refused(StartRefusal.NOT_FOUND)
            if hook is None:
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail="unknown hook")
            state = self._Loaded(pluginId)
            entry = state.hooks.setdefault(hookKey, _Hook())
            if enabled:
                if not self._Usable(pluginId, job.id, hook.on):
                    return Refused(StartRefusal.JOB_UNUSABLE)
                values = _CheckParams(job, params or {})
                if isinstance(values, Refused):
                    return values
                entry.enabled = True
                entry.params = values
                entry.disabledReason = None
                entry.failures = 0
            else:
                entry.enabled = False
                entry.disabledReason = HookDisabledReason.USER
                entry.pending = None
            self._Save(pluginId)
            self._Recount()
            return self._StatusOf(pluginId, hookKey, entry)

    def Status(self, pluginId):
        """Every hook the plugin's manifest declares, in its order."""
        with self._stateLock:
            registered = self._registry.Get(pluginId)
            manifest = (registered.report.manifest
                        if registered is not None else None)
            if manifest is None:
                return ()
            state = self._Loaded(pluginId)
            rows = []
            for job in manifest.jobs:
                for hook in job.hooks:
                    key = f"{job.id}/{hook.on}"
                    rows.append(self._StatusOf(
                        pluginId, key, state.hooks.get(key) or _Hook()))
            return tuple(rows)

    def Forget(self, pluginId, reason):
        """Disable every hook of the plugin and drop its pending triggers
        (new install, reconsent, removal, block). A new install and a
        reconsent also put the values back to the defaults (D.6, D.7).
        Called before the plugin's user_data folder is removed."""
        reason = HookDisabledReason(reason)
        with self._stateLock:
            state = self._Loaded(pluginId)
            for entry in state.hooks.values():
                entry.enabled = False
                entry.disabledReason = reason
                entry.pending = None
                if reason in (HookDisabledReason.NEW_INSTALL,
                              HookDisabledReason.RECONSENT):
                    entry.params = {}
                    entry.failures = 0
            if state.hooks:
                self._Save(pluginId)
            self._Recount()

    def TrySample(self, pluginId, hookKey):
        """A dev plugin's 'try with the sample input' (D.6): one start with
        the golden input of the hook's event, with the saved values (the
        defaults when never saved). Counted in neither window nor the
        failures, enabled or not. INVALID_REQUEST for a plugin that is not
        a dev one; UNAVAILABLE without the sample file."""
        if self._headless or self._Stopping():
            return Refused(StartRefusal.UNAVAILABLE)
        with self._stateLock:
            plugin, job, hook = self._Find(pluginId, hookKey)
            if plugin is None:
                return Refused(StartRefusal.NOT_FOUND)
            if hook is None:
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail="unknown hook")
            if plugin.badge != Badge.DEV:
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail="not a dev plugin")
            if not self._Usable(pluginId, job.id, hook.on):
                return Refused(StartRefusal.JOB_UNUSABLE)
            event = HookEvent(hook.on)
            pick = self._SamplePick(job, event)
            if pick is None:
                return Refused(StartRefusal.UNAVAILABLE,
                               detail="no sample input")
            entry = self._Loaded(pluginId).hooks.get(hookKey) or _Hook()
            params = _CheckParams(job, entry.params)
            if isinstance(params, Refused):
                params = _CheckParams(job, {})
            result = self._start(
                pluginId, job.id,
                Trigger(TriggerKind.HOOK, event=event, hook=hookKey),
                params, self._SettingsOf(pluginId), {pick.key: pick})
            if isinstance(result, RunRecord):
                self._samples.add(result.run_id)
            return result

    # ── the dispatcher ───────────────────────────────────────────────────

    def Pump(self):
        """One round of the dispatcher (its thread calls it; tests call it
        with a fake clock): take the inbox, fire the candidates held long
        enough, drop the pending triggers waited too long, start the
        pending ones while the light lane is free."""
        with self._cond:
            if self._stopping:
                return
            items = list(self._inbox)
            self._inbox.clear()
        with self._stateLock:
            now = self._clock()
            for item in items:
                self._Take(item, now)
            for recordId, (firstAt, line) in sorted(
                    self._candidates.items(), key=lambda kv: kv[1][0]):
                if now - firstAt >= MATCH_DECIDED_HOLD_SECONDS:
                    del self._candidates[recordId]
                    self._fired.add(recordId)
                    self._Trigger(HookEvent.MATCH_DECIDED, now,
                                  record=line)
            self._Expire(now)
            self._StartPending(now)
            due = self._NextDue()
        with self._cond:
            self._due = due

    def _Take(self, item, now):
        kind = item[0]
        if kind == "candidate":
            _kind, recordId, line = item
            if recordId in self._fired:
                return
            waiting = self._candidates.get(recordId)
            if waiting is None:
                self._candidates[recordId] = [now, line]
            else:
                waiting[1] = line
        elif kind == "withdrawn":
            self._candidates.pop(item[1], None)
        elif kind == "image":
            self._Trigger(HookEvent.RESULT_IMAGE_SAVED, now, path=item[1])
        elif kind == "ended":
            _kind, pluginId, hookKey, record = item
            self._Ended(pluginId, hookKey, record)
        # "freed": the pending ones are tried after the inbox anyway.

    def _Trigger(self, event, now, *, record=None, path=None):
        """Put the trigger in the slot of each enabled hook on `event`."""
        fills = HOOK_FILLS[event]
        for pluginId, job in self._registry.HookJobs(event):
            key = f"{job.id}/{event}"
            entry = self._Loaded(pluginId).hooks.get(key)
            if entry is None or not entry.enabled:
                continue
            field = next((f for f in job.params if f.type == fills), None)
            if field is None:
                continue
            pick = InputPick(key=field.key, kind=fills, path=path,
                             record=record)
            if entry.pending is not None:
                self._Remember(pluginId, entry, HookOutcome.OVERWRITTEN)
                self._Save(pluginId)
            self._seq += 1
            entry.pending = _Pending(now, self._seq, event, pick)

    def _Expire(self, now):
        for pluginId, key, entry in self._Pendings():
            if now - entry.pending.at >= HOOK_PENDING_MAX_SECONDS:
                entry.pending = None
                self._Remember(pluginId, entry, HookOutcome.DISCARDED,
                               reason=REASON_EXPIRED)
                self._Save(pluginId)

    def _StartPending(self, now):
        """The pending triggers, oldest first, while the lane is free."""
        for pluginId, key, entry in self._Pendings():
            if self._busy is not None and self._busy():
                return
            pending = entry.pending
            job = self._UsableJob(pluginId, key, pending.event)
            if job is None:
                entry.pending = None
                self._Remember(pluginId, entry, HookOutcome.DISCARDED,
                               reason=REASON_UNUSABLE)
                self._Save(pluginId)
                continue
            _Prune(entry.starts, now)
            recent = sum(1 for at in entry.starts
                         if now - at < HOOK_RATE_WINDOW_SECONDS)
            if recent >= HOOK_RATE_WINDOW_MAX_STARTS:
                entry.pending = None
                self._Remember(pluginId, entry,
                               HookOutcome.SKIPPED_RATE_LIMIT)
                self._Save(pluginId)
                continue
            result = self._start(
                pluginId, job.id,
                Trigger(TriggerKind.HOOK, event=pending.event, hook=key),
                dict(entry.params), self._SettingsOf(pluginId),
                {pending.pick.key: pending.pick})
            if isinstance(result, Refused):
                if result.reason == StartRefusal.BUSY:
                    return
                entry.pending = None
                if result.reason == StartRefusal.NEEDS_SETTINGS:
                    self._Remember(pluginId, entry,
                                   HookOutcome.NEEDS_SETTINGS)
                else:
                    self._Remember(pluginId, entry, HookOutcome.DISCARDED,
                                   reason=str(result.reason))
                self._Save(pluginId)
                continue
            entry.pending = None
            entry.starts.append(now)
            if len(entry.starts) > HOOK_HOUR_WINDOW_MAX_STARTS:
                self._Disable(pluginId, entry,
                              HookDisabledReason.RATE_LIMIT)
                self._Save(pluginId)

    def _Ended(self, pluginId, hookKey, record):
        if record.run_id in self._samples:
            self._samples.discard(record.run_id)
            return
        state = self._plugins.get(pluginId)
        entry = state.hooks.get(hookKey) if state is not None else None
        if entry is None:
            return
        reason = None
        if record.state == JobState.DONE:
            entry.failures = 0
            outcome = HookOutcome.DONE
        elif record.state == JobState.CANCELLED:
            outcome = HookOutcome.CANCELLED
        elif record.state in _FAILURE_STATES:
            entry.failures += 1
            entry.lastError = record.error
            if record.state == JobState.ERROR:
                outcome = HookOutcome.ERROR
                reason = record.error.code if record.error else None
            else:
                outcome = HookOutcome.FAILED
                reason = str(record.reason) if record.reason else None
        else:
            return
        self._Remember(pluginId, entry, outcome, summary=record.summary,
                       reason=reason, runId=record.run_id)
        if (entry.enabled
                and entry.failures >= HOOK_CONSECUTIVE_FAILURES_DISABLE):
            self._Disable(pluginId, entry, HookDisabledReason.FAILURES)
        self._Save(pluginId)

    def _Disable(self, pluginId, entry, reason):
        entry.enabled = False
        entry.disabledReason = reason
        entry.pending = None
        self._Recount()
        logger.info(f"plugin hooks: a hook auto-disabled ({reason})")

    def _Pendings(self):
        """(plugin id, key, hook) of every pending slot, oldest first."""
        found = [(pluginId, key, entry)
                 for pluginId, state in self._plugins.items()
                 for key, entry in state.hooks.items()
                 if entry.pending is not None]
        found.sort(key=lambda row: (row[2].pending.at, row[2].pending.seq))
        return found

    def _NextDue(self):
        """The clock of the next candidate to fire or pending to drop."""
        dues = [firstAt + MATCH_DECIDED_HOLD_SECONDS
                for firstAt, _line in self._candidates.values()]
        dues += [entry.pending.at + HOOK_PENDING_MAX_SECONDS
                 for _id, _key, entry in self._Pendings()]
        return min(dues) if dues else None

    def _StartThread(self):
        """Caller holds self._cond."""
        self._thread = threading.Thread(
            target=self._Run, name=DISPATCHER_THREAD, daemon=True)
        self._thread.start()

    def _Run(self):
        while True:
            with self._cond:
                while not self._stopping and not self._inbox:
                    if self._due is None:
                        self._cond.wait()
                        continue
                    left = self._due - self._clock()
                    if left <= 0:
                        break
                    self._cond.wait(left)
                if self._stopping:
                    return
            try:
                self.Pump()
            except Exception as error:
                logger.warning(
                    f"plugin hooks: a dispatch failed ({type(error).__name__})")
                with self._cond:
                    # Not again at once: the next trigger or a second later.
                    self._due = self._clock() + 1.0

    # ── state and files (caller holds _stateLock) ────────────────────────

    def _Stopping(self):
        with self._cond:
            return self._stopping

    def _Find(self, pluginId, hookKey):
        """(registered plugin, job, hook) of a hook key; job and hook None
        when the manifest has no such hook; all None for no plugin."""
        registered = self._registry.Get(pluginId)
        manifest = (registered.report.manifest
                    if registered is not None else None)
        if manifest is None:
            return None, None, None
        jobId, _sep, on = str(hookKey).partition("/")
        for job in manifest.jobs:
            if job.id != jobId:
                continue
            for hook in job.hooks:
                if hook.on == on:
                    return registered, job, hook
        return registered, None, None

    def _Usable(self, pluginId, jobId, on):
        try:
            event = HookEvent(on)
        except ValueError:
            return False
        return any(p == pluginId and j.id == jobId
                   for p, j in self._registry.HookJobs(event))

    def _UsableJob(self, pluginId, hookKey, event):
        jobId = hookKey.partition("/")[0]
        return next((job for p, job in self._registry.HookJobs(event)
                     if p == pluginId and job.id == jobId), None)

    def _SettingsOf(self, pluginId):
        return dict(self._settings(pluginId)) if self._settings else {}

    def _SamplePick(self, job, event):
        fills = HOOK_FILLS[event]
        field = next((f for f in job.params if f.type == fills), None)
        if field is None or self._samplesDir is None:
            return None
        path = os.path.join(self._samplesDir, SAMPLE_FILES[event])
        if not os.path.isfile(path):
            return None
        if event == HookEvent.MATCH_DECIDED:
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
            except (OSError, ValueError):
                return None
            return InputPick(key=field.key, kind=fills, record=record)
        return InputPick(key=field.key, kind=fills, path=path)

    def _Path(self, pluginId, dev):
        parts = [self._userDataDir, PLUGINS_DIR]
        if dev:
            parts.append(DEV_DIR)
        return os.path.join(*parts, pluginId, HOOKS_FILE)

    def _Loaded(self, pluginId, reload=False):
        """The plugin's hooks, read from its hooks.json the first time.
        An unreadable file is no enabled hook (logged)."""
        state = self._plugins.get(pluginId)
        if state is not None and not reload:
            return state
        registered = self._registry.Get(pluginId)
        dev = registered is not None and registered.badge == Badge.DEV
        fresh = _Plugin(dev)
        path = self._Path(pluginId, dev)
        try:
            with open(path, encoding="utf-8-sig") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError) as error:
            logger.warning(
                f"plugin hooks: a hooks.json unreadable "
                f"({type(error).__name__})")
            raw = {}
        for key, saved in (raw.get("hooks") or {}).items():
            if isinstance(saved, dict):
                fresh.hooks[key] = _FromJson(saved)
        if state is not None:
            # A reload keeps what is memory only.
            for key, entry in state.hooks.items():
                if key in fresh.hooks:
                    fresh.hooks[key].pending = entry.pending
                    fresh.hooks[key].starts = entry.starts
        self._plugins[pluginId] = fresh
        return fresh

    def _Save(self, pluginId):
        """Write the plugin's hooks.json (temp file, then replace). A
        failure is logged; memory stays as it is."""
        state = self._plugins.get(pluginId)
        if state is None:
            return
        path = self._Path(pluginId, state.dev)
        payload = {"hooks": {key: _ToJson(entry)
                             for key, entry in state.hooks.items()}}
        temp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
            os.replace(temp, path)
        except OSError as error:
            logger.warning(
                f"plugin hooks: hooks.json not written "
                f"({type(error).__name__})")

    def _Remember(self, pluginId, entry, outcome, *, summary=None,
                  reason=None, runId=None):
        entry.history.insert(0, HookHistoryEntry(
            at=self._wallClock().isoformat(timespec="seconds"),
            outcome=outcome, summary=summary, reason=reason, run_id=runId))
        del entry.history[HOOK_HISTORY_PER_HOOK:]

    def _Recount(self):
        """The enabled-events flags Wanted answers from."""
        events = set()
        for state in self._plugins.values():
            for key, entry in state.hooks.items():
                if entry.enabled:
                    events.add(key.partition("/")[2])
        with self._cond:
            self._wanted = frozenset() if self._stopping else frozenset(
                events)

    def _StatusOf(self, pluginId, key, entry):
        return HookStatus(
            plugin=pluginId, key=key, enabled=entry.enabled,
            params=MappingProxyType(dict(entry.params)),
            pending=entry.pending is not None, failures=entry.failures,
            disabled_reason=entry.disabledReason, last_error=entry.lastError,
            history=tuple(entry.history))


# ── helpers ─────────────────────────────────────────────────────────────

def _Prune(starts, now):
    while starts and now - starts[0] >= HOOK_HOUR_WINDOW_SECONDS:
        starts.popleft()


def _ToJson(entry):
    error = entry.lastError
    return {
        "enabled": entry.enabled,
        "params": entry.params,
        "disabled_reason": (str(entry.disabledReason)
                            if entry.disabledReason else None),
        "failures": entry.failures,
        "last_error": (None if error is None else
                       {"code": error.code, "message": error.message,
                        "hint": error.hint}),
        "history": [{"at": h.at, "outcome": str(h.outcome),
                     "summary": h.summary, "reason": h.reason,
                     "run_id": h.run_id} for h in entry.history],
    }


def _FromJson(saved):
    entry = _Hook()
    entry.enabled = saved.get("enabled") is True
    params = saved.get("params")
    entry.params = dict(params) if isinstance(params, dict) else {}
    try:
        entry.disabledReason = (HookDisabledReason(saved["disabled_reason"])
                                if saved.get("disabled_reason") else None)
    except ValueError:
        entry.disabledReason = None
    failures = saved.get("failures")
    entry.failures = (failures if isinstance(failures, int)
                      and not isinstance(failures, bool) and failures > 0
                      else 0)
    error = saved.get("last_error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        entry.lastError = ErrorEvent(code=error["code"],
                                     message=error.get("message") or "",
                                     hint=error.get("hint"))
    for row in saved.get("history") or ():
        try:
            entry.history.append(HookHistoryEntry(
                at=str(row["at"]), outcome=HookOutcome(row["outcome"]),
                summary=row.get("summary"), reason=row.get("reason"),
                run_id=row.get("run_id")))
        except (KeyError, TypeError, ValueError):
            continue
    del entry.history[HOOK_HISTORY_PER_HOOK:]
    return entry


def _CheckParams(job, values):
    """Every value field of `job` -> its checked value (a missing one its
    default, else None), or INVALID_REQUEST. Source fields are the
    trigger's, not values."""
    fields = [f for f in job.params if f.type not in SOURCE_FIELD_TYPES]
    known = {f.key for f in fields}
    unknown = sorted(set(values) - known)
    if unknown:
        return Refused(StartRefusal.INVALID_REQUEST,
                       detail=f"unknown key {unknown[0]}")
    checked = {}
    for f in fields:
        value = values.get(f.key, f.default)
        if value is None:
            if f.required:
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail=f"{f.key} is required")
            checked[f.key] = None
            continue
        if not _ValueFits(f, value):
            return Refused(StartRefusal.INVALID_REQUEST,
                           detail=f"{f.key} does not fit")
        checked[f.key] = value
    return checked


def _ValueFits(field, value):
    """A minimal check of one value against its field (ADR-009 B.3).
    PluginSettings' check replaces it once lane L3 is in."""
    kind = field.type
    if kind in (FieldType.TEXT, FieldType.TEXTAREA):
        default = TEXT_MAX_LENGTH[f"{kind}_default"]
        limit = min(field.max_length or default, TEXT_MAX_LENGTH["cap"])
        return isinstance(value, str) and len(value) <= limit
    if kind == FieldType.NUMBER:
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            return False
        if field.min is not None and value < field.min:
            return False
        if field.max is not None and value > field.max:
            return False
        return not (field.step == 1 and value != int(value))
    if kind == FieldType.BOOL:
        return isinstance(value, bool)
    if kind == FieldType.SELECT:
        return (isinstance(value, str)
                and any(o.value == value for o in field.options))
    if kind in (FieldType.FILE, FieldType.FOLDER):
        if (not isinstance(value, str) or not os.path.isabs(value)
                or value.startswith(("\\\\", "//"))):
            return False
        if kind == FieldType.FILE:
            extension = os.path.splitext(value)[1].lstrip(".").lower()
            return extension in field.extensions
        return True
    # secret (settings only) and unknown types: not a hook value.
    return False
