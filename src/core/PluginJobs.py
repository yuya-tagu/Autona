"""Generic plugin runs (ADR-009 lane L8): PluginTypes.PluginJobs.

What it owns (ADR-009 D6, appendix A.3 / A.4 / A.8, D.6, E.2;
docs/plugins/contract_1.json request / job_record / refusals / limits):

- the two lanes (one heavy, one light). A request to a full lane is refused
  BUSY with the remaining time when known; there is no queue and no
  automatic retry. A hook's pending slot is PluginHooks', not this module's;
- a run's job folder ``<work>/plugins/<id>/<run_id>/`` with request.json
  (UTF-8, no BOM), job.json, worker.log (the runner appends to it),
  ``inputs/`` (removed when the run ends, kept for a dev plugin) and
  ``out/``;
- the artifacts' checks, at the end of a run and again when one is served
  (ArtifactFile): outside out_dir, a link or junction on the way, a hard
  link (st_nlink > 1), missing, not a file, the kind's size and extension.
  A dropped artifact leaves a host warning on the run, and so do the ones
  the runner dropped (past MAX_ARTIFACTS, an unknown kind: the difference
  between ``RunOutcome.final_raw`` and the typed ``final``);
- keeping the newest RUNS_KEPT_PER_PLUGIN manual / action runs per plugin.
  A hook's run folder is removed when it ends, except the last failed one
  of each hook (replaced by the next failure). Hook runs are not listed
  (List) and do not count; Get still answers the kept one (its log);
- the launch's Sweep (job.json still running -> FAILED / AUTONA_EXITED)
  and the shells' Shutdown (cancel both lanes, the grace, then kill).

Secrets: the PluginSecrets environment goes to the runner's
BuildEnvironment only. A secret setting key is dropped from ``settings``
before anything is written, and the environment is never written or
logged.

Threads: none of its own. A run's events and its end arrive on the
runner's threads (onEvent / onEnd; onEnd is never called inside
runner.Start). ``_lock`` is a leaf: it guards the lanes and the runs'
in-memory records only; no file, no runner / registry / inputs call and no
callback is made while holding it. ``_filesLock`` (also a leaf, never held
with ``_lock``) serialises job.json's reads and writes. The callbacks
(``onRunEnded(record)``, then ``onLaneFreed(lane)``) are called outside both
locks on the thread that ended the run -- or that gave a reserved lane back
when a start was refused after the lane was reserved.

``laneElsewhere`` (optional): another owner's run on a lane -- the built-in
vodsplit's on the heavy one, run by ArchiveJobService -- asked before a
lane is reserved. It is not atomic with the other owner's own check.

``main.log`` gets one line at a run's start and one at its end: the plugin
id, the job id, the run id, the lane and the end state. No value, path or
player name.

Nothing is made, read or written on import (pitfalls #1); no Qt
(tests/test_core_qt_free.py).
"""

import dataclasses
import datetime
import json
import os
import re
import secrets
import shutil
import stat
import string
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple, Union

from loguru import logger

from .PluginTypes import (
    ACTION_FILLS, CONTEXT_ONLY_TYPES, HOOK_FILLS, HOST_SPEAKS_PROTOCOLS,
    HOOK_TIMEOUT_CAP_SECONDS, IMAGE_ARTIFACT_EXTENSIONS,
    IMAGE_ARTIFACT_MAX_BYTES, INPUTS_DIR, JOB_FILE, LOG_FILE, MAX_ARTIFACTS,
    MAX_WARNINGS, OUT_DIR, PLUGIN_ID_PATTERN, REQUEST_FILE, RUN_ID_PATTERN,
    RUNS_KEPT_PER_PLUGIN, SOURCE_FIELD_TYPES, TEXT_ARTIFACT_MAX_BYTES,
    VALUE_FIELD_TYPES, ActionTarget, Artifact, ArtifactKind,
    ArtifactRejection, AutonaApiUse, Badge, DoneEvent, EndReason, ErrorEvent,
    FieldType, HookEvent, HostInfo, InputFile, InputPick, Job, JobState, Lane,
    LaunchSpec, LogEvent, Manifest, PluginState, ProblemScope, ProgressEvent,
    Refused, RunOutcome, RunRecord, StageEvent, StartRefusal, Text, Trigger,
    TriggerKind, WarningEvent)

_PLUGIN_ID = re.compile(PLUGIN_ID_PATTERN)
_RUN_ID = re.compile(RUN_ID_PATTERN)
_RUN_ID_CHARACTERS = string.ascii_lowercase + string.digits
_RUN_ID_ATTEMPTS = 10
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.05
# How much of a dropped artifact's path / kind a host warning repeats.
_WARNING_NAME_MAX = 200
_FAILED_HOOK_STATES = frozenset({JobState.FAILED, JobState.ERROR})
_PLUGINS_DIR = "plugins"
_DEV_DIR = "_dev"
_DATA_DIR = "data"

_REJECTION_TEXT = {
    ArtifactRejection.OUTSIDE_OUT_DIR: ("out フォルダの外", "outside out_dir"),
    ArtifactRejection.SYMLINK_OR_JUNCTION: ("リンク", "a link or junction"),
    ArtifactRejection.HARD_LINK: ("ハードリンク", "a hard link"),
    ArtifactRejection.MISSING: ("ファイルが無い", "missing"),
    ArtifactRejection.NOT_A_FILE: ("ファイルでない", "not a file"),
    ArtifactRejection.TOO_LARGE: ("大きすぎる", "too large"),
    ArtifactRejection.BAD_EXTENSION: ("使えない拡張子", "extension not allowed"),
    ArtifactRejection.UNKNOWN_KIND: ("知らない種類", "unknown kind"),
}


def _LocalNow():
    return datetime.datetime.now().astimezone()


def _RandomSuffix():
    return "".join(secrets.choice(_RUN_ID_CHARACTERS) for _ in range(4))


def _Iso(moment):
    return moment.isoformat(timespec="seconds")


def _Short(text):
    text = str(text)
    return text if len(text) <= _WARNING_NAME_MAX \
        else text[:_WARNING_NAME_MAX] + "…"


def _Inside(base, path):
    """Whether ``path`` is strictly below ``base`` (both normalised the
    same way by the caller)."""
    base = os.path.normcase(base)
    path = os.path.normcase(path)
    try:
        return os.path.commonpath([base, path]) == base and path != base
    except ValueError:
        return False


def _IsLink(path):
    return os.path.islink(path) or os.path.isjunction(path)


def _Remove(path):
    """Remove a folder this module made (a run folder or its inputs/).
    rmtree does not follow a link or junction inside it."""
    shutil.rmtree(path, ignore_errors=True)


def CheckArtifact(outDir: str, artifact: Artifact
                  ) -> Tuple[Optional[str], Optional[ArtifactRejection]]:
    """(the real path, None) of an acceptable artifact, else (None, why).
    ``outDir`` is the run's out folder as the host made it."""
    try:
        kind = ArtifactKind(artifact.kind)
    except ValueError:
        return None, ArtifactRejection.UNKNOWN_KIND
    rel = artifact.path
    if (not isinstance(rel, str) or not rel or "\x00" in rel
            or os.path.isabs(rel) or os.path.splitdrive(rel)[0]):
        return None, ArtifactRejection.OUTSIDE_OUT_DIR
    base = os.path.normpath(os.path.abspath(outDir))
    target = os.path.normpath(os.path.join(base, rel))
    if not _Inside(base, target):
        return None, ArtifactRejection.OUTSIDE_OUT_DIR
    # out/ itself and every step from it down: a worker can swap any of
    # them for a junction to a folder of the user's.
    current = base
    if _IsLink(current):
        return None, ArtifactRejection.SYMLINK_OR_JUNCTION
    for part in os.path.relpath(target, base).split(os.sep):
        current = os.path.join(current, part)
        if _IsLink(current):
            return None, ArtifactRejection.SYMLINK_OR_JUNCTION
    real = os.path.realpath(target)
    if not _Inside(os.path.realpath(base), real):
        return None, ArtifactRejection.OUTSIDE_OUT_DIR
    try:
        info = os.stat(target)
    except OSError:
        return None, ArtifactRejection.MISSING
    if not stat.S_ISREG(info.st_mode):
        return None, ArtifactRejection.NOT_A_FILE
    if info.st_nlink > 1:
        return None, ArtifactRejection.HARD_LINK
    if kind is ArtifactKind.IMAGE:
        extension = os.path.splitext(target)[1][1:].lower()
        if extension not in IMAGE_ARTIFACT_EXTENSIONS:
            return None, ArtifactRejection.BAD_EXTENSION
        if info.st_size > IMAGE_ARTIFACT_MAX_BYTES:
            return None, ArtifactRejection.TOO_LARGE
    elif kind is ArtifactKind.TEXT and info.st_size > TEXT_ARTIFACT_MAX_BYTES:
        return None, ArtifactRejection.TOO_LARGE
    return real, None


def _RejectionWarning(path, rejection):
    ja, en = _REJECTION_TEXT[rejection]
    name = _Short(path)
    return {"ja": f"成果物 {name} を捨てました（{ja}）",
            "en": f"Dropped the artifact {name} ({en})"}


def _RunnerDropWarnings(final, finalRaw):
    """Host warnings for the artifacts the runner dropped while reading the
    done line: an unknown kind (one each), and the rest (past MAX_ARTIFACTS
    or otherwise unreadable) as one count."""
    raw = finalRaw if isinstance(finalRaw, Mapping) else final.raw
    items = raw.get("artifacts") if isinstance(raw, Mapping) else None
    if not isinstance(items, list):
        return []
    known = {kind.value for kind in ArtifactKind}
    warnings = []
    unknown = 0
    for item in items:
        if (isinstance(item, Mapping) and isinstance(item.get("kind"), str)
                and item["kind"] not in known):
            unknown += 1
            path = item.get("path")
            warnings.append(_RejectionWarning(
                path if isinstance(path, str) else "?",
                ArtifactRejection.UNKNOWN_KIND))
    rest = len(items) - len(final.artifacts) - unknown
    if rest > 0:
        warnings.append({
            "ja": f"成果物 {rest} 件を捨てました（{MAX_ARTIFACTS} 件を超えた分か、"
                  f"読めない項目）",
            "en": f"Dropped {rest} artifact(s) (past {MAX_ARTIFACTS}, or "
                  f"unreadable)"})
    return warnings


# ── JSON shapes (request.json, job.json) ────────────────────────────────

def _TextJson(text):
    if text is None or isinstance(text, str):
        return text
    return dict(text)


def _TextFrom(raw):
    if isinstance(raw, str):
        return raw
    if (isinstance(raw, Mapping) and raw
            and all(isinstance(k, str) and isinstance(v, str)
                    for k, v in raw.items())):
        return dict(raw)
    return None


def _TriggerJson(trigger: Trigger):
    out = {"kind": TriggerKind(trigger.kind).value}
    if trigger.target is not None:
        out["target"] = ActionTarget(trigger.target).value
    if trigger.event is not None:
        out["event"] = HookEvent(trigger.event).value
    if trigger.hook is not None:
        out["hook"] = trigger.hook
    return out


def _TriggerFrom(raw):
    if not isinstance(raw, Mapping):
        return None
    try:
        kind = TriggerKind(raw.get("kind"))
        target = ActionTarget(raw["target"]) \
            if raw.get("target") is not None else None
        event = HookEvent(raw["event"]) \
            if raw.get("event") is not None else None
    except ValueError:
        return None
    hook = raw.get("hook")
    return Trigger(kind=kind, target=target, event=event,
                   hook=hook if isinstance(hook, str) else None)


def _InputJson(item: Optional[InputFile]):
    if item is None:
        return None
    out = {"kind": FieldType(item.kind).value, "format": item.format,
           "path": item.path}
    if item.sha256 is not None:
        out["sha256"] = item.sha256
    return out


def RecordJson(record: RunRecord) -> dict:
    """job.json's object (contract_1.json job_record): those keys only."""
    stage = record.stage
    progress = record.progress
    error = record.error
    return {
        "run_id": record.run_id,
        "plugin": record.plugin,
        "job": record.job,
        "trigger": _TriggerJson(record.trigger),
        "lane": Lane(record.lane).value,
        "state": JobState(record.state).value,
        "reason": EndReason(record.reason).value
        if record.reason is not None else None,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "exit_code": record.exit_code,
        "stage": None if stage is None else {
            "stage": stage.stage, "label": _TextJson(stage.label),
            "index": stage.index, "count": stage.count},
        "progress": None if progress is None else {
            "stage": progress.stage, "fraction": progress.fraction,
            "overall": progress.overall, "eta": progress.eta},
        "log": _TextJson(record.log),
        "warnings": [_TextJson(text) for text in record.warnings],
        "summary": _TextJson(record.summary),
        "artifacts": [{"path": item.path,
                       "kind": ArtifactKind(item.kind).value,
                       "label": _TextJson(item.label)}
                      for item in record.artifacts],
        "error": None if error is None else {
            "code": error.code, "message": _TextJson(error.message),
            "hint": _TextJson(error.hint)},
        "cancel_requested": record.cancel_requested,
        "finished_before_cancel": record.finished_before_cancel,
        "timeout_seconds": record.timeout_seconds,
        "peak_memory_bytes": record.peak_memory_bytes,
    }


def _Int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) \
        else None


def _Num(value):
    return value if isinstance(value, (int, float)) \
        and not isinstance(value, bool) else None


def RecordFrom(raw: Any) -> Optional[RunRecord]:
    """A job.json object read back, None when it is not one."""
    if not isinstance(raw, Mapping):
        return None
    runId, plugin, job = raw.get("run_id"), raw.get("plugin"), raw.get("job")
    started = raw.get("started_at")
    trigger = _TriggerFrom(raw.get("trigger"))
    if (not isinstance(runId, str) or not _RUN_ID.fullmatch(runId)
            or not isinstance(plugin, str) or not isinstance(job, str)
            or not isinstance(started, str) or trigger is None):
        return None
    try:
        lane = Lane(raw.get("lane"))
        state = JobState(raw.get("state"))
        reason = EndReason(raw["reason"]) \
            if raw.get("reason") is not None else None
    except ValueError:
        return None
    stage = raw.get("stage")
    stage = StageEvent(stage=stage["stage"], label=_TextFrom(stage.get("label")),
                       index=_Int(stage.get("index")),
                       count=_Int(stage.get("count"))) \
        if isinstance(stage, Mapping) and isinstance(stage.get("stage"), str) \
        else None
    progress = raw.get("progress")
    progress = ProgressEvent(
        stage=progress.get("stage") if isinstance(progress.get("stage"), str)
        else None,
        fraction=_Num(progress.get("fraction")),
        overall=_Num(progress.get("overall")), eta=_Num(progress.get("eta"))) \
        if isinstance(progress, Mapping) else None
    artifacts = []
    for item in raw.get("artifacts") or ():
        if isinstance(item, Mapping) and isinstance(item.get("path"), str):
            try:
                artifacts.append(Artifact(path=item["path"],
                                          kind=ArtifactKind(item.get("kind")),
                                          label=_TextFrom(item.get("label"))))
            except ValueError:
                continue
    error = raw.get("error")
    error = ErrorEvent(code=error["code"],
                       message=_TextFrom(error.get("message")) or "",
                       hint=_TextFrom(error.get("hint"))) \
        if isinstance(error, Mapping) and isinstance(error.get("code"), str) \
        else None
    warnings = tuple(text for text in (_TextFrom(item) for item in
                                       raw.get("warnings") or ())
                     if text is not None) \
        if isinstance(raw.get("warnings"), list) else ()
    finished = raw.get("finished_at")
    return RunRecord(
        run_id=runId, plugin=plugin, job=job, trigger=trigger, lane=lane,
        state=state, started_at=started, reason=reason,
        finished_at=finished if isinstance(finished, str) else None,
        exit_code=_Int(raw.get("exit_code")), stage=stage, progress=progress,
        log=_TextFrom(raw.get("log")), warnings=warnings,
        summary=_TextFrom(raw.get("summary")), artifacts=tuple(artifacts),
        error=error, cancel_requested=raw.get("cancel_requested") is True,
        finished_before_cancel=raw.get("finished_before_cancel") is True,
        timeout_seconds=_Int(raw.get("timeout_seconds")),
        peak_memory_bytes=_Int(raw.get("peak_memory_bytes")))


def _WriteJson(path, content):
    """Write ``content`` as JSON (UTF-8, no BOM) whole: temp file, fsynced,
    os.replace (tried again a few times: on Windows a replace fails while
    a reader has the file open). Raises OSError, TypeError / ValueError
    for what is not JSON."""
    data = json.dumps(content, ensure_ascii=False, indent=2)
    folder, name = os.path.split(path)
    fd, tmp = tempfile.mkstemp(dir=folder,
                               prefix=os.path.splitext(name)[0] + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        delay = _REPLACE_BACKOFF
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                break
            except OSError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ── a run in memory ─────────────────────────────────────────────────────

@dataclass
class _Plan:
    """What a start checked: the parts its folder and launch need."""
    pluginId: str
    job: Job
    manifest: Manifest
    folder: str
    dev: bool
    protocol: int
    lane: Lane
    trigger: Trigger
    timeout: int
    params: dict
    settings: dict
    sources: Tuple[Tuple[str, FieldType, bool], ...]
    picks: Mapping[str, InputPick]


class _Run:
    """A run holding a lane (reserved, then launched): its record's
    changing fields, guarded by PluginJobs._lock."""

    def __init__(self, plan: _Plan, started: datetime.datetime):
        self.plan = plan
        self.pluginId = plan.pluginId
        self.lane = plan.lane
        self.started = started
        self.runId: Optional[str] = None
        self.folder: Optional[str] = None
        self.handle = None
        self.ended = False
        self.cancelRequested = False
        self.state = JobState.RUNNING
        self.reason: Optional[EndReason] = None
        self.finishedAt: Optional[str] = None
        self.exitCode: Optional[int] = None
        self.stage: Optional[StageEvent] = None
        self.progress: Optional[ProgressEvent] = None
        self.log: Optional[Text] = None
        self.warnings: list = []
        self.workerWarnings = 0
        self.summary: Optional[Text] = None
        self.artifacts: Tuple[Artifact, ...] = ()
        self.error: Optional[ErrorEvent] = None
        self.finishedBeforeCancel = False
        self.peak: Optional[int] = None

    def Record(self) -> RunRecord:
        return RunRecord(
            run_id=self.runId or "", plugin=self.pluginId,
            job=self.plan.job.id, trigger=self.plan.trigger, lane=self.lane,
            state=self.state, started_at=_Iso(self.started),
            reason=self.reason, finished_at=self.finishedAt,
            exit_code=self.exitCode, stage=self.stage, progress=self.progress,
            log=self.log, warnings=tuple(self.warnings), summary=self.summary,
            artifacts=self.artifacts, error=self.error,
            cancel_requested=self.cancelRequested,
            finished_before_cancel=self.finishedBeforeCancel,
            timeout_seconds=self.plan.timeout, peak_memory_bytes=self.peak)


class _Refuse(Exception):
    def __init__(self, refused: Refused):
        super().__init__(refused.reason.value)
        self.refused = refused


def _Refusal(reason, detail=""):
    return _Refuse(Refused(reason=reason, detail=detail))


# ── the service ─────────────────────────────────────────────────────────

class PluginJobs:
    """PluginTypes.PluginJobs. Every collaborator is injected:

    - ``runner`` (PluginRunner), ``registry`` (PluginRegistry), ``inputs``
      (PluginInputs), ``settings`` (PluginSettings: Missing),
      ``secrets`` (PluginSecrets: Environment);
    - ``workDir()``: the work folder (archive.work_dir), read per start;
    - ``userDataDir``: the user_data folder (data_dir is
      ``plugins/<id>/data`` or ``plugins/_dev/<id>/data`` below it);
    - ``hostVersion``: Autona's version (request.json ``host``);
    - ``locale()``: the display language (``ja`` / anything else -> ``en``);
    - ``apiUrl()``: the v1 base URL or None (AUTONA_API_URL, only for a
      plugin declaring autona_api read / write);
    - ``onRunEnded(record)`` / ``onLaneFreed(lane)``: see the module;
    - ``clock()``: an aware local datetime (run ids, times, remaining);
    - ``environ()``: the parent environment for BuildEnvironment;
    - ``randomSuffix()``: the run id's four characters;
    - ``laneElsewhere(lane)``: another owner's BUSY on ``lane``, or None.
    """

    def __init__(self, *, runner, registry, inputs, settings, secrets,
                 workDir: Callable[[], str], userDataDir: str,
                 hostVersion: str, locale: Callable[[], str],
                 apiUrl: Callable[[], Optional[str]],
                 onRunEnded: Callable[[RunRecord], None],
                 onLaneFreed: Callable[[Lane], None],
                 clock: Callable[[], datetime.datetime] = _LocalNow,
                 environ: Callable[[], Mapping[str, str]] =
                 lambda: dict(os.environ),
                 randomSuffix: Callable[[], str] = _RandomSuffix,
                 laneElsewhere: Optional[
                     Callable[[Lane], Optional[Refused]]] = None):
        self._runner = runner
        self._registry = registry
        self._inputs = inputs
        self._settings = settings
        self._secrets = secrets
        self._workDir = workDir
        self._userDataDir = userDataDir
        self._hostVersion = hostVersion
        self._locale = locale
        self._apiUrl = apiUrl
        self._onRunEnded = onRunEnded
        self._onLaneFreed = onLaneFreed
        self._clock = clock
        self._environ = environ
        self._randomSuffix = randomSuffix
        self._laneElsewhere = laneElsewhere
        self._lock = threading.Lock()
        self._filesLock = threading.Lock()
        self._lanes: dict = {Lane.HEAVY: None, Lane.LIGHT: None}
        self._closing = False

    # ── paths ──────────────────────────────────────────────────────────

    def _PluginWork(self, pluginId, workDir=None):
        return os.path.join(workDir or self._workDir(), _PLUGINS_DIR,
                            pluginId)

    def _RunFolder(self, pluginId, runId):
        """The run's folder on disk (not that it exists), None for what is
        not an id or would resolve outside the plugin's work folder."""
        if (not isinstance(pluginId, str) or not _PLUGIN_ID.fullmatch(pluginId)
                or not isinstance(runId, str) or not _RUN_ID.fullmatch(runId)):
            return None
        base = self._PluginWork(pluginId)
        path = os.path.join(base, runId)
        if not _Inside(os.path.realpath(base), os.path.realpath(path)):
            return None
        return path

    def _DataDir(self, pluginId, dev):
        parts = [self._userDataDir, _PLUGINS_DIR]
        if dev:
            parts.append(_DEV_DIR)
        return os.path.abspath(os.path.join(*parts, pluginId, _DATA_DIR))

    # ── job.json ───────────────────────────────────────────────────────

    def _ReadRecord(self, folder):
        path = os.path.join(folder, JOB_FILE)
        with self._filesLock:
            try:
                with open(path, encoding="utf-8") as file:
                    raw = json.load(file)
            except FileNotFoundError:
                return None
            except (OSError, ValueError):
                logger.warning(f"Plugins: {JOB_FILE} of run "
                               f"{os.path.basename(folder)} could not be read")
                return None
        return RecordFrom(raw)

    def _WriteRecord(self, folder, record):
        with self._filesLock:
            _WriteJson(os.path.join(folder, JOB_FILE), RecordJson(record))

    # ── the lanes ──────────────────────────────────────────────────────

    def _Remaining(self, run):
        """Seconds until ``run`` likely ends: the worker's eta when it gave
        one, else until its time limit; None when neither is known."""
        if run.progress is not None and run.progress.eta is not None:
            return max(0.0, float(run.progress.eta))
        if run.plan.timeout is None:
            return None
        elapsed = (self._clock() - run.started).total_seconds()
        return max(0.0, run.plan.timeout - elapsed)

    def _BusyLocked(self, lane):
        run = self._lanes[lane]
        if run is None:
            return None
        return Refused(reason=StartRefusal.BUSY,
                       remaining_seconds=self._Remaining(run))

    def BusyFor(self, lane: Lane) -> Optional[Refused]:
        lane = Lane(lane)
        with self._lock:
            busy = self._BusyLocked(lane)
        if busy is None and self._laneElsewhere is not None:
            busy = self._laneElsewhere(lane)
        return busy

    def Running(self, pluginId: str) -> bool:
        with self._lock:
            return any(run is not None and run.pluginId == pluginId
                       for run in self._lanes.values())

    def _Active(self, pluginId, runId):
        """The in-memory run (under _lock)."""
        for run in self._lanes.values():
            if (run is not None and run.pluginId == pluginId
                    and run.runId == runId):
                return run
        return None

    def _ActiveIds(self):
        with self._lock:
            return {(run.pluginId, run.runId)
                    for run in self._lanes.values()
                    if run is not None and run.runId is not None}

    # ── the start's checks ─────────────────────────────────────────────

    def _Plan(self, pluginId, jobId, trigger, params, settings, picks):
        """The checked start, or raises _Refuse. Calls the registry (no
        lock held)."""
        plugin = self._registry.Get(pluginId) \
            if isinstance(pluginId, str) and _PLUGIN_ID.fullmatch(pluginId) \
            else None
        if plugin is None:
            raise _Refusal(StartRefusal.NOT_FOUND)
        state = PluginState(plugin.state)
        if state is PluginState.BLOCKED or plugin.blocked_reason is not None:
            raise _Refusal(StartRefusal.BLOCKED)
        manifest = plugin.report.manifest
        if (state is PluginState.INCOMPATIBLE or manifest is None
                or any(problem.scope is ProblemScope.PLUGIN
                       for problem in plugin.report.problems)):
            raise _Refusal(StartRefusal.INCOMPATIBLE)
        if state is PluginState.DISABLED or not plugin.enabled:
            raise _Refusal(StartRefusal.DISABLED)
        job = next((item for item in manifest.jobs if item.id == jobId), None)
        if job is None:
            raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown job")
        if any(problem.scope is ProblemScope.JOB and problem.job == jobId
               for problem in plugin.report.problems):
            raise _Refusal(StartRefusal.JOB_UNUSABLE)
        try:
            lane = Lane(job.lane)
            fields = [(field, FieldType(field.type)) for field in job.params]
            settingFields = [(field, FieldType(field.type))
                             for field in manifest.settings]
        except ValueError:
            raise _Refusal(StartRefusal.JOB_UNUSABLE)
        protocol = self._Protocol(plugin, manifest)
        if protocol is None:
            raise _Refusal(StartRefusal.INCOMPATIBLE)

        fills = self._CheckTrigger(job, lane, trigger)
        values = {}
        valueKeys = {field.key for field, kind in fields
                     if kind in VALUE_FIELD_TYPES and kind is not FieldType.SECRET}
        unknown = set(params or {}) - valueKeys
        if unknown:
            raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown param")
        for field, kind in fields:
            if field.key not in valueKeys:
                continue
            value = (params or {}).get(field.key)
            if value is None:
                value = field.default
            if value is None and field.required:
                raise _Refusal(StartRefusal.INVALID_REQUEST,
                               f"{field.key} is required")
            values[field.key] = value
        # Never a secret: a secret key the caller passed is dropped here.
        settingValues = {}
        for field, kind in settingFields:
            if kind is FieldType.SECRET:
                continue
            value = (settings or {}).get(field.key)
            settingValues[field.key] = field.default if value is None \
                else value

        sources = tuple((field.key, kind, field.required)
                        for field, kind in fields
                        if kind in SOURCE_FIELD_TYPES)
        sourceKinds = {key: kind for key, kind, _ in sources}
        picks = dict(picks or {})
        for key, pick in picks.items():
            if (sourceKinds.get(key) is None
                    or getattr(pick, "kind", None) not in FieldType
                    or FieldType(pick.kind) is not sourceKinds[key]):
                raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown input")
        for key, kind, required in sources:
            needed = (kind is fills) if fills is not None \
                else (required and kind is not FieldType.STATE)
            if needed and key not in picks:
                raise _Refusal(StartRefusal.INVALID_REQUEST,
                               f"{key} is not picked")
            if fills is None and kind in CONTEXT_ONLY_TYPES:
                raise _Refusal(StartRefusal.INVALID_REQUEST,
                               "a context-only input in a manual run")

        timeout = job.timeout_seconds
        if TriggerKind(trigger.kind) is TriggerKind.HOOK:
            timeout = min(timeout, HOOK_TIMEOUT_CAP_SECONDS)
        return _Plan(pluginId=pluginId, job=job, manifest=manifest,
                     folder=plugin.folder,
                     dev=Badge(plugin.badge) is Badge.DEV, protocol=protocol,
                     lane=lane, trigger=trigger, timeout=timeout,
                     params=values, settings=settingValues, sources=sources,
                     picks=picks)

    @staticmethod
    def _Protocol(plugin, manifest):
        """describe's protocol when the registry has it, else the newest in
        common with the host (describe's result is not cached)."""
        if plugin.describe is not None and plugin.describe.protocol:
            return plugin.describe.protocol
        common = set(manifest.protocol) & set(HOST_SPEAKS_PROTOCOLS)
        return max(common) if common else None

    @classmethod
    def _CheckTrigger(cls, job, lane, trigger):
        """The FieldType the trigger fills (None for manual), or raises."""
        try:
            return cls._TriggerFills(job, lane, trigger)
        except (AttributeError, ValueError):
            raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown trigger")

    @staticmethod
    def _TriggerFills(job, lane, trigger):
        kind = TriggerKind(trigger.kind)
        if kind is TriggerKind.MANUAL:
            if not job.manual:
                raise _Refusal(StartRefusal.INVALID_REQUEST,
                               "the job is not started by hand")
            return None
        if lane is not Lane.LIGHT:
            raise _Refusal(StartRefusal.JOB_UNUSABLE)
        if kind is TriggerKind.ACTION:
            if (trigger.target is None
                    or ActionTarget(trigger.target).value
                    not in {action.target for action in job.actions}):
                raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown action")
            return ACTION_FILLS[ActionTarget(trigger.target)]
        if (trigger.event is None
                or HookEvent(trigger.event).value
                not in {hook.on for hook in job.hooks}
                or trigger.hook != f"{job.id}/{HookEvent(trigger.event).value}"):
            raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown hook")
        return HOOK_FILLS[HookEvent(trigger.event)]

    # ── Start ──────────────────────────────────────────────────────────

    def Start(self, pluginId: str, jobId: str, trigger: Trigger,
              params: Mapping[str, Any], settings: Mapping[str, Any],
              picks: Mapping[str, InputPick]) -> Union[RunRecord, Refused]:
        with self._lock:
            if self._closing:
                return Refused(reason=StartRefusal.UNAVAILABLE,
                               detail="shutting down")
        try:
            plan = self._Plan(pluginId, jobId, trigger, params, settings,
                              picks)
        except _Refuse as refusal:
            return refusal.refused
        missing = self._settings.Missing(pluginId, plan.manifest,
                                         dev=plan.dev)
        if missing:
            return Refused(reason=StartRefusal.NEEDS_SETTINGS,
                           detail=", ".join(missing))
        if self._laneElsewhere is not None:
            elsewhere = self._laneElsewhere(plan.lane)
            if elsewhere is not None:
                return elsewhere
        run = _Run(plan, self._clock())
        with self._lock:
            if self._closing:
                return Refused(reason=StartRefusal.UNAVAILABLE,
                               detail="shutting down")
            busy = self._BusyLocked(plan.lane)
            if busy is not None:
                return busy
            self._lanes[plan.lane] = run

        try:
            spec = self._Prepare(run)
        except _Refuse as refusal:
            self._GiveBack(run)
            return refusal.refused
        except OSError:
            logger.opt(exception=True).warning(
                f"Plugins: the job folder of {pluginId}/{jobId} could not be "
                f"written")
            self._GiveBack(run)
            return Refused(reason=StartRefusal.UNAVAILABLE,
                           detail="the job folder could not be written")
        with self._lock:
            closing = self._closing
        if closing:
            self._GiveBack(run)
            return Refused(reason=StartRefusal.UNAVAILABLE,
                           detail="shutting down")

        logger.info(f"Plugins: run {run.runId} of {pluginId}/{jobId} "
                    f"started ({plan.lane.value}, "
                    f"{TriggerKind(trigger.kind).value})")
        try:
            handle = self._runner.Start(
                spec, lambda event: self._OnEvent(run, event),
                lambda outcome: self._OnEnd(run, outcome))
        except Exception:
            logger.opt(exception=True).warning(
                f"Plugins: run {run.runId} could not be launched")
            self._OnEnd(run, RunOutcome(state=JobState.FAILED,
                                        reason=EndReason.LAUNCH_FAILED))
            with self._lock:
                return run.Record()
        with self._lock:
            run.handle = handle
            cancel = run.cancelRequested and not run.ended
            record = run.Record()
        if cancel:
            handle.Cancel()
        return record

    def _GiveBack(self, run):
        """A reserved lane back after a refused start (the folder made so
        far removed)."""
        with self._lock:
            if self._lanes[run.lane] is run:
                self._lanes[run.lane] = None
            run.ended = True
        if run.folder is not None:
            _Remove(run.folder)
        self._Call(self._onLaneFreed, run.lane)

    def _NewRunFolder(self, run):
        pluginWork = self._PluginWork(run.pluginId)
        os.makedirs(pluginWork, exist_ok=True)
        stamp = self._clock().strftime("%Y%m%d-%H%M%S")
        for attempt in range(_RUN_ID_ATTEMPTS):
            runId = f"{stamp}-{self._randomSuffix()}"
            if not _RUN_ID.fullmatch(runId):
                raise ValueError("the run id suffix is not [a-z0-9]{4}")
            folder = os.path.abspath(os.path.join(pluginWork, runId))
            try:
                os.mkdir(folder)
            except FileExistsError:
                continue
            return runId, folder
        raise FileExistsError("no free run id")

    def _Prepare(self, run) -> LaunchSpec:
        """The run's folder, inputs, request.json and job.json, then the
        launch. Raises _Refuse or OSError."""
        plan = run.plan
        runId, folder = self._NewRunFolder(run)
        with self._lock:
            run.runId = runId
            run.folder = folder
        inputsDir = os.path.join(folder, INPUTS_DIR)
        outDir = os.path.join(folder, OUT_DIR)
        os.mkdir(inputsDir)
        os.mkdir(outDir)
        dataDir = self._DataDir(plan.pluginId, plan.dev)
        os.makedirs(dataDir, exist_ok=True)

        inputs = {}
        for key, kind, required in plan.sources:
            try:
                item = self._inputs.Prepare(key, kind, inputsDir,
                                            plan.picks.get(key))
            except (LookupError, ValueError, TypeError):
                raise _Refusal(StartRefusal.INVALID_REQUEST,
                               f"{key} could not be filled")
            if item is None and required:
                raise _Refusal(StartRefusal.INVALID_REQUEST,
                               f"{key} is not filled")
            inputs[key] = item

        locale = self._locale() or ""
        request = {
            "protocol": plan.protocol,
            "plugin": plan.pluginId,
            "job": plan.job.id,
            "run_id": runId,
            "trigger": _TriggerJson(plan.trigger),
            "locale": "ja" if locale.lower().startswith("ja") else "en",
            "params": plan.params,
            "settings": plan.settings,
            "inputs": {key: _InputJson(item) for key, item in inputs.items()},
            "out_dir": outDir,
            "data_dir": dataDir,
            "host": {"name": HostInfo.name, "version": self._hostVersion},
        }
        requestPath = os.path.join(folder, REQUEST_FILE)
        try:
            _WriteJson(requestPath, request)
        except (TypeError, ValueError):
            raise _Refusal(StartRefusal.INVALID_REQUEST, "a value is not JSON")
        with self._lock:
            record = run.Record()
        self._WriteRecord(folder, record)

        manifest = plan.manifest
        apiUrl = self._apiUrl() if manifest.permissions.autona_api in (
            AutonaApiUse.READ.value, AutonaApiUse.WRITE.value) else None
        env = self._runner.BuildEnvironment(
            self._environ(), pluginDir=plan.folder, protocol=plan.protocol,
            secrets=self._secrets.Environment(plan.pluginId, dev=plan.dev),
            apiUrl=apiUrl)
        return LaunchSpec(
            argv=self._Argv(plan) + ("run", requestPath), cwd=folder,
            env=env, lane=plan.lane, log_path=os.path.join(folder, LOG_FILE),
            timeout_seconds=plan.timeout, memory_mb=plan.job.memory_mb)

    @staticmethod
    def _Argv(plan):
        """The manifest's entry with its file resolved to an absolute path
        in the plugin folder (cwd is the job folder). A dev entry's first
        word may be a command found on PATH (left as is); its later words
        are resolved when they name a file in the plugin folder."""
        folder = os.path.abspath(plan.folder)
        entry = list(plan.manifest.entry)
        resolved = []
        for index, word in enumerate(entry):
            candidate = os.path.normpath(os.path.join(folder, word))
            inFolder = not os.path.isabs(word) and _Inside(folder, candidate)
            if index == 0 and not plan.dev:
                resolved.append(candidate)
            elif plan.dev and inFolder and os.path.isfile(candidate):
                resolved.append(candidate)
            else:
                resolved.append(word)
        return tuple(resolved)

    # ── a run's events and end (the runner's threads) ──────────────────

    def _OnEvent(self, run, event):
        with self._lock:
            if run.ended:
                return
            if isinstance(event, StageEvent):
                run.stage = event
                run.progress = None
            elif isinstance(event, ProgressEvent):
                run.progress = event
            elif isinstance(event, LogEvent):
                run.log = event.message
            elif isinstance(event, WarningEvent):
                if run.workerWarnings < MAX_WARNINGS:
                    run.workerWarnings += 1
                    run.warnings.append(event.message)

    def _OnEnd(self, run, outcome: RunOutcome):
        final = outcome.final
        accepted = []
        hostWarnings = []
        if isinstance(final, DoneEvent):
            outDir = os.path.join(run.folder, OUT_DIR)
            for artifact in final.artifacts:
                real, rejection = CheckArtifact(outDir, artifact)
                if rejection is None:
                    accepted.append(artifact)
                else:
                    hostWarnings.append(
                        _RejectionWarning(artifact.path, rejection))
            hostWarnings.extend(_RunnerDropWarnings(final, outcome.final_raw))

        with self._lock:
            if run.ended:
                return
            run.ended = True
            run.state = JobState(outcome.state)
            run.reason = outcome.reason
            run.exitCode = outcome.exit_code
            run.finishedAt = _Iso(self._clock())
            run.cancelRequested = run.cancelRequested \
                or outcome.cancel_requested
            run.finishedBeforeCancel = outcome.finished_before_cancel
            run.peak = outcome.peak_memory_bytes
            if isinstance(final, DoneEvent):
                run.summary = final.summary
                run.artifacts = tuple(accepted)
            elif isinstance(final, ErrorEvent):
                run.error = final
            run.warnings.extend(hostWarnings)
            if self._lanes[run.lane] is run:
                self._lanes[run.lane] = None
            record = run.Record()

        plan = run.plan
        try:
            self._WriteRecord(run.folder, record)
        except (OSError, TypeError, ValueError):
            logger.opt(exception=True).warning(
                f"Plugins: {JOB_FILE} of run {record.run_id} could not be "
                f"written")
        if not plan.dev:
            _Remove(os.path.join(run.folder, INPUTS_DIR))
        if TriggerKind(plan.trigger.kind) is TriggerKind.HOOK \
                and record.state not in _FAILED_HOOK_STATES:
            _Remove(run.folder)
        self._Prune(os.path.dirname(run.folder))
        logger.info(f"Plugins: run {record.run_id} of "
                    f"{record.plugin}/{record.job} ended {record.state.value}"
                    + (f" ({record.reason.value})" if record.reason else ""))
        self._Call(self._onRunEnded, record)
        self._Call(self._onLaneFreed, run.lane)

    @staticmethod
    def _Call(callback, argument):
        try:
            callback(argument)
        except Exception:
            logger.opt(exception=True).warning(
                "Plugins: a run callback failed")

    # ── keeping ────────────────────────────────────────────────────────

    def _Records(self, pluginWork):
        """(folder, record) of every readable run folder of a plugin."""
        try:
            entries = list(os.scandir(pluginWork))
        except OSError:
            return []
        out = []
        for entry in entries:
            if not _RUN_ID.fullmatch(entry.name) or not entry.is_dir(
                    follow_symlinks=False):
                continue
            record = self._ReadRecord(entry.path)
            if record is not None and record.run_id == entry.name:
                out.append((entry.path, record))
        return out

    def _Prune(self, pluginWork):
        """Keep the newest RUNS_KEPT_PER_PLUGIN manual / action runs (a
        running one counts and stays) and, per hook, the newest failed run
        only. A folder whose job.json says running or cannot be read is
        never removed."""
        active = self._ActiveIds()
        plugin = os.path.basename(pluginWork)
        kept = []
        failedByHook = {}
        for folder, record in self._Records(pluginWork):
            if (plugin, record.run_id) in active:
                if record.trigger.kind is not TriggerKind.HOOK:
                    kept.append((record.run_id, folder, False))
                continue
            ended = record.state is not JobState.RUNNING
            if record.trigger.kind is TriggerKind.HOOK:
                if not ended:
                    continue
                if record.state in _FAILED_HOOK_STATES:
                    failedByHook.setdefault(record.trigger.hook, []).append(
                        (record.run_id, folder))
                else:
                    _Remove(folder)
                continue
            kept.append((record.run_id, folder, ended))
        kept.sort(reverse=True)
        for runId, folder, ended in kept[RUNS_KEPT_PER_PLUGIN:]:
            if ended:
                _Remove(folder)
        for runs in failedByHook.values():
            runs.sort(reverse=True)
            for runId, folder in runs[1:]:
                _Remove(folder)

    # ── reading ────────────────────────────────────────────────────────

    def Get(self, pluginId: str, runId: str) -> Optional[RunRecord]:
        with self._lock:
            run = self._Active(pluginId, runId)
            if run is not None:
                return run.Record()
        folder = self._RunFolder(pluginId, runId)
        if folder is None:
            return None
        record = self._ReadRecord(folder)
        if record is None or record.plugin != pluginId \
                or record.run_id != runId:
            return None
        return record

    def List(self, pluginId: str) -> Sequence[RunRecord]:
        """The plugin's manual / action runs, newest first (hook runs are
        in the hook's history; the kept failed one answers Get)."""
        if not isinstance(pluginId, str) or not _PLUGIN_ID.fullmatch(pluginId):
            return []
        with self._lock:
            active = {run.runId: run.Record() for run in self._lanes.values()
                      if run is not None and run.pluginId == pluginId
                      and run.runId is not None}
        records = dict(active)
        for folder, record in self._Records(self._PluginWork(pluginId)):
            if record.run_id not in records and record.plugin == pluginId:
                records[record.run_id] = record
        return [record for runId, record in
                sorted(records.items(), reverse=True)
                if record.trigger.kind is not TriggerKind.HOOK]

    def _Folder(self, pluginId, runId):
        """The run's folder: the in-memory run's (it may be in an earlier
        work folder), else the current work folder's."""
        with self._lock:
            run = self._Active(pluginId, runId)
            if run is not None:
                return run.folder
        return self._RunFolder(pluginId, runId)

    def LogTail(self, pluginId: str, runId: str,
                maxBytes: int) -> Optional[str]:
        if self.Get(pluginId, runId) is None:
            return None
        folder = self._Folder(pluginId, runId)
        if folder is None:
            return None
        path = os.path.join(folder, LOG_FILE)
        try:
            with open(path, "rb") as file:
                size = file.seek(0, os.SEEK_END)
                start = max(0, size - max(0, int(maxBytes)))
                file.seek(start)
                data = file.read()
        except FileNotFoundError:
            return ""
        except OSError:
            return None
        if start > 0:
            # Cut at the first line break: the first line is a partial one.
            newline = data.find(b"\n")
            if newline != -1:
                data = data[newline + 1:]
        return data.decode("utf-8", errors="replace")

    def ArtifactFile(self, pluginId: str, runId: str,
                     index: int) -> Optional[Tuple[str, ArtifactKind]]:
        record = self.Get(pluginId, runId)
        if (record is None or not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(record.artifacts)):
            return None
        folder = self._Folder(pluginId, runId)
        if folder is None:
            return None
        artifact = record.artifacts[index]
        real, rejection = CheckArtifact(os.path.join(folder, OUT_DIR),
                                        artifact)
        if rejection is not None:
            return None
        return real, ArtifactKind(artifact.kind)

    # ── control ────────────────────────────────────────────────────────

    def Cancel(self, pluginId: str, runId: str) -> bool:
        with self._lock:
            run = self._Active(pluginId, runId)
            if run is None or run.ended:
                return False
            run.cancelRequested = True
            handle = run.handle
        if handle is not None:
            handle.Cancel()
        return True

    def Sweep(self) -> None:
        """At launch: every generic run job.json still calls running (and
        that is not a run of this process) -> FAILED / AUTONA_EXITED; then
        the keeping rules per plugin."""
        root = os.path.join(self._workDir(), _PLUGINS_DIR)
        try:
            plugins = [entry for entry in os.scandir(root)
                       if _PLUGIN_ID.fullmatch(entry.name)
                       and entry.is_dir(follow_symlinks=False)]
        except OSError:
            return
        active = self._ActiveIds()
        finishedAt = _Iso(self._clock())
        for plugin in plugins:
            registered = self._registry.Get(plugin.name)
            dev = registered is not None \
                and Badge(registered.badge) is Badge.DEV
            for folder, record in self._Records(plugin.path):
                if (record.state is not JobState.RUNNING
                        or (plugin.name, record.run_id) in active):
                    continue
                swept = dataclasses.replace(
                    record, state=JobState.FAILED,
                    reason=EndReason.AUTONA_EXITED, finished_at=finishedAt)
                try:
                    self._WriteRecord(folder, swept)
                except OSError:
                    logger.opt(exception=True).warning(
                        f"Plugins: run {record.run_id} could not be marked "
                        f"failed")
                    continue
                if not dev:
                    _Remove(os.path.join(folder, INPUTS_DIR))
                logger.info(f"Plugins: run {record.run_id} of {plugin.name} "
                            f"was left running; marked failed")
            self._Prune(plugin.path)

    def Shutdown(self, grace: float) -> None:
        """Refuse new starts, cancel both lanes, wait up to ``grace``
        seconds, then have the runner kill what is left."""
        with self._lock:
            self._closing = True
            runs = [run for run in self._lanes.values() if run is not None]
            for run in runs:
                run.cancelRequested = True
            handles = [run.handle for run in runs if run.handle is not None]
        for handle in handles:
            handle.Cancel()
        deadline = time.monotonic() + max(0.0, grace)
        left = False
        for handle in handles:
            if handle.Wait(max(0.0, deadline - time.monotonic())) is None:
                left = True
        if left:
            self._runner.Shutdown(0.0)
