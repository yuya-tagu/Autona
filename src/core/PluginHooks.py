"""The hooks' dispatcher (ADR-009 D5, appendix D; lane L9): implements
``PluginTypes.PluginHooks``.

A hook is a job's ``hooks[]`` entry, keyed ``<job id>/<on>``. It is off by
default; the user turns it on (SHELL, desktop only) with the values of the
job's value fields it runs with. Two triggers start it:

- ``match_decided``: the match log's observer tells ``MatchCandidate(id,
  line)`` / ``MatchWithdrawn(id)`` (MatchLogService, ADR-009 D.1). A record
  fires MATCH_DECIDED_HOLD_SECONDS after its *first* candidate was queued
  unless it is withdrawn before; a candidate again for a record waiting
  replaces the line only (the wait keeps counting from the first). A
  withdrawal drops the wait without marking the record, so a later
  candidate waits again. A record that fired is marked (the newest
  FIRED_KEPT marks are kept) and its candidates are ignored after: one
  firing per record. The line handed to the job is the latest candidate's.
- ``result_image_saved``: ``ResultImageSaved(path)`` from the result render
  lane's completion, one per image.

What happens to a trigger (D.3, D.4; contract_1.json hooks.order), one
trigger at a time (two queued together do not overwrite each other while
the lane is free):

1. each enabled hook on the event gets the trigger in its *pending slot*
   (one per hook, not a queue): a trigger already there is replaced -- the
   history says ``overwritten`` and the slot takes the new trigger's time
   (its 10 minutes and its place among the oldest start again). A trigger
   left there HOOK_PENDING_MAX_SECONDS is dropped (``discarded``,
   ``expired``);
2. the pending slots are started oldest first while the light lane is free
   (``busy``, then the start's own BUSY refusal: a manual / action run that
   took the lane keeps the hook pending, so the user's runs go first);
3. right before a start, the saved values are checked against the job's
   fields again (a plugin update may have changed them): values that no
   longer fit -- or a start refused INVALID_REQUEST -- disable the hook
   (``reconsent``: its values have to be confirmed again, D.6);
4. then the 60 s sliding window: HOOK_RATE_WINDOW_MAX_STARTS started in it
   already skips this one (``skipped_rate_limit``; not kept pending);
5. right after a start, the 1 h sliding window: more than
   HOOK_HOUR_WINDOW_MAX_STARTS in it disables the hook (``rate_limit``).
   Enabling a hook again empties its windows.

A start refused for missing settings is ``needs_settings`` in the history
and counts nothing (D.5); one refused otherwise (blocked, disabled,
incompatible, unavailable ...) is ``discarded`` with the refusal's code and
counts nothing either.

A run's end (``RunEnded``, from PluginJobs' onRunEnded): ``done`` sets the
failures back to 0; ``error`` and ``failed`` (timed_out included) add one,
and HOOK_CONSECUTIVE_FAILURES_DISABLE in a row disable the hook
(``failures``); ``cancelled`` counts nothing, nor does a run failed because
Autona exited (the launch's sweep: not the plugin's failure). The last
error shown on the row is the run's error event, or one the host makes
from the failure's reason (code = the EndReason). An auto-disable changes
the hook's row only, never the plugin list's state.

What is kept per hook in ``user_data/plugins/<id>/hooks.json``
(``user_data/plugins/_dev/<id>/`` for a dev plugin, which shares nothing
with an installed one): enabled, the values, why it is off, the failures
in a row, the last error and the newest HOOK_HISTORY_PER_HOOK history
entries (newest first). Never a setting nor a secret. The windows' starts,
the pending slots and the match candidates are memory only.

A hooks.json that cannot be taken in: JSON of another shape is read hook
by hook (a hook entry that does not fit is left out, logged); one that is
not JSON or not an object is moved aside to
``hooks.json.unreadable-<time>.bak`` and the plugin starts with no hook
enabled (SettingsManager's way). One that cannot be opened now (an
antivirus or sync tool holding it) leaves the plugin *unreadable*: nothing
is saved over it, enabling answers UNAVAILABLE, and every access tries to
read it again. Reading again (``Refresh``) that fails keeps what memory
has.

When nothing runs (D.7): the headless shell (``headless`` at construction:
nothing is queued, nothing can be enabled), after ``Shutdown`` (the
dispatcher stops, what is pending is dropped, and every start checks
``stopping`` right before it), and an event no enabled hook is on
(``Wanted`` false; ``ResultImageSaved`` asks it itself, the match log asks
it once per decided record).

The dev plugins' 'try with the sample input' (D.6) is ``TrySample``: one
start with the golden input of ``samplesDir`` (docs/plugins/inputs/), not
counted in the windows nor the failures. There is no golden image, so a
result_image_saved sample needs ``result_image.png`` in ``samplesDir``.

Threads and locks
-----------------
One dispatcher thread (``AutonaPluginHooks``, daemon, started by ``Start``).

``Wanted`` / ``MatchCandidate`` / ``MatchWithdrawn`` are called holding
``StateManager.lock`` (the match log's observer), and ``ResultImageSaved``
/ ``LaneFreed`` / ``RunEnded`` from whatever thread: they only touch the
inbox and the enabled-events flags under ``_cond`` -- a leaf lock (no I/O,
no other lock, no wait inside it), so they may be called holding any lock,
including from inside the injected ``start``. ``Wanted`` answers from those
flags.

The dispatcher (``Pump``) and the SHELL calls (``SetEnabled`` / ``Status``
/ ``Forget`` / ``TrySample`` / ``Refresh``) hold ``_stateLock`` (not
re-entrant) for the hooks' state, their files and the starts. Under it they
call the registry (``Get`` / ``HookJobs``), ``busy()``, ``settings()`` and
``start`` -- PluginJobs.Start, which prepares the inputs (the state at the
trigger: StateManager.lock; the match log: MatchLogService.Flush) and
launches a process (seconds, with an antivirus scanning it; Status waits
meanwhile). So the order is ``_stateLock`` -> the registry's, PluginJobs',
StateManager.lock and the others, and the SHELL calls (and ``Shutdown``)
are never called holding StateManager.lock, the registry's lock (no
``Refresh`` from inside its Scan), PluginJobs' or the installer's. The
injected parts never call back into a SHELL call. ``Shutdown`` waits for
the thread and ``_stateLock`` at most ``timeout`` seconds.

Injected (no plugin part is imported): the registry, the start, whether
the light lane is full, the settings of a plugin, the pick resolver, the
clocks. Nothing is made, read or written on import (pitfalls #1); no Qt.
"""

import collections
import json
import os
import threading
import time
from datetime import datetime
from types import MappingProxyType

from loguru import logger

from . import ImageImport
from .PluginSettings import CheckFieldValue
from .PluginTypes import (
    HOOK_CONSECUTIVE_FAILURES_DISABLE, HOOK_FILLS, HOOK_HISTORY_PER_HOOK,
    HOOK_HOUR_WINDOW_MAX_STARTS, HOOK_HOUR_WINDOW_SECONDS,
    HOOK_PENDING_MAX_SECONDS, HOOK_RATE_WINDOW_MAX_STARTS,
    HOOK_RATE_WINDOW_SECONDS, MATCH_DECIDED_HOLD_SECONDS, SOURCE_FIELD_TYPES,
    Badge, EndReason, ErrorEvent, FieldType, HookDisabledReason, HookEvent,
    HookHistoryEntry, HookOutcome, HookStatus, InputPick, JobState, Refused,
    RunRecord, StartRefusal, Trigger, TriggerKind)

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
# The newest records marked fired that are kept (one per match).
FIRED_KEPT = 1000

# History reasons of the host's own (besides an EndReason / error code /
# StartRefusal value).
REASON_EXPIRED = "expired"
REASON_UNUSABLE = "unusable"
REASON_INVALID_PARAMS = "invalid_params"
REASON_START_FAILED = "start_failed"

# The last error of a failed run (no error event): code = the EndReason.
_FAILED_MESSAGES = MappingProxyType({
    EndReason.TIMED_OUT: {"ja": "制限時間を過ぎました", "en": "Timed out"},
    EndReason.MEMORY_LIMIT: {"ja": "メモリの上限に達しました",
                             "en": "Hit the memory limit"},
    EndReason.ABNORMAL_EXIT: {"ja": "異常終了しました",
                              "en": "Exited abnormally"},
    EndReason.LAUNCH_FAILED: {"ja": "起動できませんでした",
                              "en": "Could not be launched"},
    EndReason.ENTRY_MISSING: {"ja": "プログラムが見つかりません",
                              "en": "The program was not found"},
})
_FAILED_FALLBACK = {"ja": "失敗しました", "en": "Failed"}


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
    """A plugin's hooks as read from its hooks.json. ``unreadable``: the
    file is there but could not be opened; nothing is saved over it."""

    __slots__ = ("dev", "hooks", "unreadable")

    def __init__(self, dev):
        self.dev = dev
        self.hooks = {}
        self.unreadable = False


class PluginHooks:

    def __init__(self, userDataDir, registry, start, *, busy=None,
                 settings=None, resolvePick=None, clock=None,
                 wallClock=None, headless=False, samplesDir=None):
        """`userDataDir`: the ``user_data`` folder (hooks.json lives under
        its ``plugins/``). `registry`: a PluginRegistry (``Get`` /
        ``HookJobs``). `start`: PluginJobs.Start. `busy()`: whether the
        light lane is full (``jobs.BusyFor(Lane.LIGHT) is not None``; None:
        only the start's BUSY refusal says so). `settings(pluginId)`: the
        plugin's settings for request.json -- a wrapper of
        ``PluginSettings.Get(pluginId, manifest, dev=...)`` (None: ``{}``).
        `resolvePick(fieldType, pickId)`: the host's pick ids -> absolute
        paths, for file / folder values sent to SetEnabled (PluginSettings'
        PickResolver). `clock()`: monotonic seconds; `wallClock()`: an
        aware datetime for the history. `headless`: never queue nor run.
        `samplesDir`: the golden inputs for TrySample."""
        self._userDataDir = userDataDir
        self._registry = registry
        self._start = start
        self._busy = busy
        self._settings = settings
        self._resolvePick = resolvePick
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
        self._fired = collections.OrderedDict()
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
        too); no start after it. Waits at most `timeout` seconds for the
        thread and a dispatch in progress (a start already under way
        finishes; none follows it). Not called holding StateManager.lock."""
        deadline = time.monotonic() + timeout
        with self._cond:
            if self._stopping:
                return
            self._stopping = True
            self._inbox.clear()
            self._wanted = frozenset()
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        if self._stateLock.acquire(
                timeout=max(0.0, deadline - time.monotonic())):
            try:
                self._candidates.clear()
                for plugin in self._plugins.values():
                    for hook in plugin.hooks.values():
                        hook.pending = None
            finally:
                self._stateLock.release()
        else:
            # A dispatch still holds it; it starts nothing more (stopping).
            logger.warning("plugin hooks: a dispatch still running at close")
        if thread is not None:
            logger.info("plugin hooks: stopped")

    def Refresh(self):
        """Read hooks.json again for every plugin with a usable hook job
        (launch, and after the registry's Scan -- not from inside it: a dev
        folder added back brings its enabled hooks). A file that cannot be
        read keeps what memory has. Not called holding StateManager.lock
        nor the registry's lock."""
        if self._headless:
            return
        ids = set()
        for event in HookEvent:
            ids.update(pluginId for pluginId, _job
                       in self._registry.HookJobs(event))
        with self._stateLock:
            for pluginId in sorted(ids):
                try:
                    self._Loaded(pluginId, reload=True)
                except Exception as error:
                    logger.warning(f"plugin hooks: a plugin's hooks not read "
                                   f"({type(error).__name__})")
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
        """Queue `item` with its time (the clock is a plain function)."""
        with self._cond:
            if self._headless or self._stopping:
                return
            self._inbox.append((self._clock(), item))
            self._cond.notify_all()

    # ── the SHELL calls ──────────────────────────────────────────────────

    def SetEnabled(self, pluginId, hookKey, enabled, params):
        """Enable a hook with the values it runs with (every value field of
        the job: a missing or empty one is its default; checked by
        PluginSettings.CheckFieldValue, a file / folder as a pick id), or
        disable it (``params`` ignored; its saved values stay for the next
        enable). UNAVAILABLE headless, after Shutdown, or while its
        hooks.json cannot be read."""
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
            if state.unreadable:
                return Refused(StartRefusal.UNAVAILABLE,
                               detail="hooks.json unreadable")
            if enabled:
                if not self._Usable(pluginId, job.id, hook.on):
                    return Refused(StartRefusal.JOB_UNUSABLE)
                values = _CheckParams(job, params or {},
                                      resolvePick=self._resolvePick)
                if isinstance(values, Refused):
                    return values
                entry = state.hooks.setdefault(hookKey, _Hook())
                entry.enabled = True
                entry.params = values
                entry.disabledReason = None
                entry.failures = 0
                entry.starts.clear()
            else:
                entry = state.hooks.setdefault(hookKey, _Hook())
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
        reconsent also put the values back to the defaults and the failures
        to 0 (D.6, D.7). Called before the plugin's user_data folder is
        removed."""
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
        defaults when never saved or no longer fitting). Counted in neither
        window nor the failures, enabled or not. INVALID_REQUEST for a
        plugin that is not a dev one; UNAVAILABLE without the sample file,
        headless or after Shutdown."""
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
            params = _CheckParams(job, entry.params, stored=True)
            if isinstance(params, Refused):
                params = _CheckParams(job, {}, stored=True)
            if isinstance(params, Refused):
                return params
            settings = self._SettingsOf(pluginId)
            if self._Stopping():
                return Refused(StartRefusal.UNAVAILABLE)
            result = self._start(
                pluginId, job.id,
                Trigger(TriggerKind.HOOK, event=event, hook=hookKey),
                params, settings, {pick.key: pick})
            if isinstance(result, RunRecord):
                self._samples.add(result.run_id)
            return result

    # ── the dispatcher ───────────────────────────────────────────────────

    def Pump(self):
        """One round of the dispatcher (its thread calls it; tests call it
        with a fake clock): drop the pending triggers waited too long, take
        the inbox one item at a time (each trigger is started at once while
        the lane is free), fire the candidates held long enough, start the
        pending ones while the light lane is free. One item that fails is
        dropped and logged; the others go on."""
        with self._cond:
            if self._stopping:
                return
            items = list(self._inbox)
            self._inbox.clear()
        with self._stateLock:
            now = self._clock()
            self._Guarded(self._Expire, now)
            for at, item in items:
                self._Guarded(self._Take, item, at)
            for recordId, (firstAt, line) in sorted(
                    self._candidates.items(), key=lambda kv: kv[1][0]):
                if firstAt + MATCH_DECIDED_HOLD_SECONDS <= now:
                    del self._candidates[recordId]
                    self._MarkFired(recordId)
                    self._Guarded(self._TriggerAndStart,
                                  HookEvent.MATCH_DECIDED, now, line, None)
            self._Guarded(self._StartPending, now)
            due = self._NextDue()
        with self._cond:
            self._due = due

    @staticmethod
    def _Guarded(step, *args):
        try:
            step(*args)
        except Exception as error:
            logger.warning(f"plugin hooks: a dispatch step failed "
                           f"({type(error).__name__})")

    def _Take(self, item, at):
        kind = item[0]
        if kind == "candidate":
            _kind, recordId, line = item
            if recordId in self._fired:
                return
            waiting = self._candidates.get(recordId)
            if waiting is None:
                self._candidates[recordId] = [at, line]
            else:
                waiting[1] = line
        elif kind == "withdrawn":
            self._candidates.pop(item[1], None)
        elif kind == "image":
            self._TriggerAndStart(HookEvent.RESULT_IMAGE_SAVED, at, None,
                                  item[1])
        elif kind == "ended":
            _kind, pluginId, hookKey, record = item
            self._Ended(pluginId, hookKey, record)
        elif kind == "freed":
            self._StartPending(self._clock())

    def _MarkFired(self, recordId):
        self._fired[recordId] = True
        while len(self._fired) > FIRED_KEPT:
            self._fired.popitem(last=False)

    def _TriggerAndStart(self, event, at, record, path):
        self._Trigger(event, at, record=record, path=path)
        self._StartPending(self._clock())

    def _Trigger(self, event, at, *, record=None, path=None):
        """Put the trigger in the slot of each enabled hook on `event` (one
        plugin failing does not keep it from the others)."""
        for pluginId, job in self._registry.HookJobs(event):
            try:
                self._TriggerOne(pluginId, job, event, at, record, path)
            except Exception as error:
                logger.warning(f"plugin hooks: a trigger not taken "
                               f"({type(error).__name__})")

    def _TriggerOne(self, pluginId, job, event, at, record, path):
        fills = HOOK_FILLS[event]
        key = f"{job.id}/{event}"
        entry = self._Loaded(pluginId).hooks.get(key)
        if entry is None or not entry.enabled:
            return
        field = next((f for f in job.params if f.type == fills), None)
        if field is None:
            return
        pick = InputPick(key=field.key, kind=fills, path=path, record=record)
        if entry.pending is not None:
            self._Remember(entry, HookOutcome.OVERWRITTEN)
            self._Save(pluginId)
        self._seq += 1
        entry.pending = _Pending(at, self._seq, event, pick)

    def _Expire(self, now):
        for pluginId, key, entry in self._Pendings():
            if entry.pending.at + HOOK_PENDING_MAX_SECONDS <= now:
                entry.pending = None
                self._Remember(entry, HookOutcome.DISCARDED,
                               reason=REASON_EXPIRED)
                self._Save(pluginId)

    def _StartPending(self, now):
        """The pending triggers, oldest first, while the lane is free and
        not stopping."""
        for pluginId, key, entry in self._Pendings():
            if self._Stopping():
                return
            if self._busy is not None and self._busy():
                return
            if self._StartOne(pluginId, key, entry, now) is False:
                return

    def _StartOne(self, pluginId, key, entry, now):
        """Start one pending trigger, or drop it. False when the lane was
        full (stop trying)."""
        pending = entry.pending
        job = self._UsableJob(pluginId, key, pending.event)
        if job is None:
            entry.pending = None
            self._Remember(entry, HookOutcome.DISCARDED,
                           reason=REASON_UNUSABLE)
            self._Save(pluginId)
            return True
        params = _CheckParams(job, entry.params, stored=True)
        if isinstance(params, Refused):
            self._Disable(entry, HookDisabledReason.RECONSENT)
            self._Remember(entry, HookOutcome.DISCARDED,
                           reason=REASON_INVALID_PARAMS)
            self._Save(pluginId)
            return True
        _Prune(entry.starts, now)
        recent = sum(1 for at in entry.starts
                     if at + HOOK_RATE_WINDOW_SECONDS > now)
        if recent >= HOOK_RATE_WINDOW_MAX_STARTS:
            entry.pending = None
            self._Remember(entry, HookOutcome.SKIPPED_RATE_LIMIT)
            self._Save(pluginId)
            return True
        settings = self._SettingsOf(pluginId)
        if self._Stopping():
            return False
        try:
            result = self._start(
                pluginId, job.id,
                Trigger(TriggerKind.HOOK, event=pending.event, hook=key),
                params, settings, {pending.pick.key: pending.pick})
        except Exception as error:
            logger.warning(f"plugin hooks: a start failed "
                           f"({type(error).__name__})")
            entry.pending = None
            self._Remember(entry, HookOutcome.DISCARDED,
                           reason=REASON_START_FAILED)
            self._Save(pluginId)
            return True
        if isinstance(result, Refused):
            if result.reason == StartRefusal.BUSY:
                return False
            entry.pending = None
            if result.reason == StartRefusal.NEEDS_SETTINGS:
                self._Remember(entry, HookOutcome.NEEDS_SETTINGS)
            elif result.reason == StartRefusal.INVALID_REQUEST:
                self._Disable(entry, HookDisabledReason.RECONSENT)
                self._Remember(entry, HookOutcome.DISCARDED,
                               reason=REASON_INVALID_PARAMS)
            else:
                self._Remember(entry, HookOutcome.DISCARDED,
                               reason=str(result.reason))
            self._Save(pluginId)
            return True
        entry.pending = None
        entry.starts.append(now)
        if len(entry.starts) > HOOK_HOUR_WINDOW_MAX_STARTS:
            self._Disable(entry, HookDisabledReason.RATE_LIMIT)
            self._Save(pluginId)
        return True

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
        elif record.state == JobState.ERROR:
            entry.failures += 1
            entry.lastError = record.error
            outcome = HookOutcome.ERROR
            reason = record.error.code if record.error else None
        elif record.state == JobState.FAILED:
            outcome = HookOutcome.FAILED
            reason = str(record.reason) if record.reason else None
            # Autona exited under the run (the launch's sweep): not the
            # plugin's failure.
            if record.reason != EndReason.AUTONA_EXITED:
                entry.failures += 1
                entry.lastError = ErrorEvent(
                    code=reason or str(JobState.FAILED),
                    message=_FAILED_MESSAGES.get(record.reason,
                                                 _FAILED_FALLBACK))
        else:
            return
        self._Remember(entry, outcome, summary=record.summary,
                       reason=reason, runId=record.run_id)
        if (entry.enabled
                and entry.failures >= HOOK_CONSECUTIVE_FAILURES_DISABLE):
            self._Disable(entry, HookDisabledReason.FAILURES)
        self._Save(pluginId)

    def _Disable(self, entry, reason):
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
        """The clock of the next candidate to fire or pending to drop (the
        same sums Pump compares)."""
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
        """The plugin's hooks, read from its hooks.json the first time, when
        `reload`, while it is unreadable, and when the plugin moved between
        installed and dev (nothing carried over: they share nothing)."""
        state = self._plugins.get(pluginId)
        registered = self._registry.Get(pluginId)
        dev = registered is not None and registered.badge == Badge.DEV
        if state is not None and state.dev != dev:
            state = None
        if (state is not None and not reload and not state.unreadable):
            return state
        # Memory has nothing to keep yet: first read, or unreadable so far.
        fresh = self._Read(pluginId, dev,
                           first=state is None or state.unreadable)
        if fresh is None:
            # Read again and failed: memory stays as it is.
            return state
        if state is not None:
            for key, entry in state.hooks.items():
                if key in fresh.hooks:
                    fresh.hooks[key].pending = entry.pending
                    fresh.hooks[key].starts = entry.starts
        self._plugins[pluginId] = fresh
        self._Recount()
        return fresh

    def _Read(self, pluginId, dev, *, first):
        """A _Plugin from hooks.json: empty when there is none; read hook
        by hook. Not JSON / not an object: moved aside (first read) and
        empty. Cannot be opened: an unreadable _Plugin (first read). A read
        again that fails: None (keep memory)."""
        path = self._Path(pluginId, dev)
        fresh = _Plugin(dev)
        try:
            with open(path, "rb") as handle:
                content = handle.read()
        except FileNotFoundError:
            return fresh
        except OSError as error:
            logger.warning(f"plugin hooks: a hooks.json could not be read "
                           f"({type(error).__name__})")
            if not first:
                return None
            fresh.unreadable = True
            return fresh
        try:
            raw = json.loads(content.decode("utf-8-sig"))
            hooks = raw.get("hooks", {}) if isinstance(raw, dict) else None
            if not isinstance(hooks, dict):
                raise ValueError("not an object of hooks")
        except ValueError as error:
            logger.warning(f"plugin hooks: a hooks.json is damaged "
                           f"({type(error).__name__})")
            if not first:
                return None
            try:
                os.replace(path, _BackupPath(path))
            except OSError as moveError:
                logger.warning(f"plugin hooks: a damaged hooks.json not moved "
                               f"aside ({type(moveError).__name__})")
                fresh.unreadable = True
            return fresh
        for key, saved in hooks.items():
            try:
                fresh.hooks[str(key)] = _FromJson(saved)
            except Exception as error:
                logger.warning(f"plugin hooks: a hook entry left out "
                               f"({type(error).__name__})")
        return fresh

    def _Save(self, pluginId):
        """Write the plugin's hooks.json (a temporary file, then replace).
        Not over an unreadable one. A failure is logged; memory stays as it
        is and the next save writes it."""
        state = self._plugins.get(pluginId)
        if state is None:
            return
        if state.unreadable:
            logger.warning("plugin hooks: hooks.json unreadable, not saved")
            return
        path = self._Path(pluginId, state.dev)
        try:
            data = json.dumps({"hooks": {key: _ToJson(entry)
                                         for key, entry in state.hooks.items()}},
                              ensure_ascii=False, indent=1)
            ImageImport.WriteFile(path, data.encode("utf-8"))
        except (OSError, TypeError, ValueError) as error:
            logger.warning(
                f"plugin hooks: hooks.json not written "
                f"({type(error).__name__})")

    def _Remember(self, entry, outcome, *, summary=None, reason=None,
                  runId=None):
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
    while starts and starts[0] + HOOK_HOUR_WINDOW_SECONDS <= now:
        starts.popleft()


def _BackupPath(path):
    """``hooks.json.unreadable-<time>.bak``, never an existing file
    (SettingsManager._BackupPath's naming)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{path}.unreadable-{stamp}"
    candidate = f"{base}.bak"
    suffix = 2
    while os.path.exists(candidate):
        candidate = f"{base}-{suffix}.bak"
        suffix += 1
    return candidate


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
    """A _Hook from its saved object; ValueError when it is not one. A part
    of another shape is left at its default."""
    if not isinstance(saved, dict):
        raise ValueError("not an object")
    entry = _Hook()
    entry.enabled = saved.get("enabled") is True
    params = saved.get("params")
    entry.params = dict(params) if isinstance(params, dict) else {}
    try:
        entry.disabledReason = (HookDisabledReason(saved["disabled_reason"])
                                if saved.get("disabled_reason") else None)
    except (TypeError, ValueError):
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
    history = saved.get("history")
    for row in history if isinstance(history, list) else ():
        try:
            entry.history.append(HookHistoryEntry(
                at=str(row["at"]), outcome=HookOutcome(row["outcome"]),
                summary=row.get("summary"), reason=row.get("reason"),
                run_id=row.get("run_id")))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    del entry.history[HOOK_HISTORY_PER_HOOK:]
    return entry


def _Empty(field, value):
    return value is None or (
        field.type in (FieldType.TEXT, FieldType.TEXTAREA) and value == "")


def _CheckParams(job, values, *, resolvePick=None, stored=False):
    """Every value field of `job` -> its checked value (a missing or empty
    one its default, else None), or INVALID_REQUEST naming the key. A file
    / folder value is a pick id resolved by `resolvePick`, or -- `stored`,
    a saved value -- the path kept before. Source fields are the
    trigger's, not values."""
    if not isinstance(values, dict) and not hasattr(values, "items"):
        return Refused(StartRefusal.INVALID_REQUEST,
                       detail="params: not an object")
    fields = [f for f in job.params if f.type not in SOURCE_FIELD_TYPES]
    known = {f.key for f in fields}
    unknown = sorted(str(key) for key in values if key not in known)
    if unknown:
        return Refused(StartRefusal.INVALID_REQUEST,
                       detail=f"{unknown[0][:40]}: no such value")
    checked = {}
    for f in fields:
        value = values.get(f.key)
        defaulted = _Empty(f, value)
        if defaulted:
            value = f.default
        if _Empty(f, value):
            if f.required:
                return Refused(StartRefusal.INVALID_REQUEST,
                               detail=f"{f.key}: required")
            checked[f.key] = value
            continue
        # A default is the manifest's, checked as a kept value.
        ok, kept = CheckFieldValue(f, value, resolvePick=resolvePick,
                                   stored=stored or defaulted)
        if not ok:
            return Refused(StartRefusal.INVALID_REQUEST,
                           detail=f"{f.key}: {kept}")
        checked[f.key] = kept
    return checked
