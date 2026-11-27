"""Generic plugin runs (ADR-009 lane L8): PluginTypes.PluginJobs.

What it owns (ADR-009 D6, appendix A.3 / A.4 / A.8, D.6, E.2;
docs/plugins/contract_1.json request / job_record / refusals / limits):

- the two lanes (one heavy, one light). A request to a full lane is refused
  BUSY with the remaining time when known; there is no queue and no
  automatic retry. A hook's pending slot is PluginHooks', not this module's.
  PluginJobs owns both lanes: another owner (the built-in vodsplit, run by
  ArchiveJobService, on the heavy one) takes a lane with ``Claim(lane,
  owner)`` and gives it back with ``Release(lane, owner)``;
- the per-plugin hold of a swap, a removal, a disable or a block (``Hold`` /
  ``Unhold``, ADR-009 C.3): while held, a start of the plugin is refused
  UNAVAILABLE, and so is one whose plan was read before a hold (its
  manifest or state may be stale); Hold answers whether a run of it is
  going, under the lock a start reserves its lane with, so the installer's
  swap never meets a run it did not see.
  ``CancelAll`` asks every run of a plugin to stop (a disable, a removal);
- a run's job folder ``<work>/plugins/<id>/<run_id>/`` with request.json
  (UTF-8, no BOM), job.json, worker.log (the runner appends to it),
  ``inputs/`` (removed when the run ends, kept for a dev plugin) and
  ``out/``. The JSON files are written ASCII-escaped (still UTF-8): a
  worker's text may hold a lone surrogate, which UTF-8 cannot encode;
- the artifacts' checks, at the end of a run and again when one is served
  (ArtifactFile): outside out_dir, a link or junction on the way, a hard
  link (st_nlink > 1), missing, not a file, the kind's size and extension.
  A dropped artifact leaves a host warning on the run, and so do the ones
  the runner dropped (past MAX_ARTIFACTS, an unknown kind: the difference
  between ``RunOutcome.final_raw`` and the typed ``final``). A worker's
  texts are cut to the contract's display limits when kept;
- keeping the newest RUNS_KEPT_PER_PLUGIN manual / action runs per plugin.
  A hook's run folder is removed when it ends, except the last failed one
  of each hook (without inputs/ and out/: a hook run keeps no artifact,
  replaced by the next failure). Hook runs are not listed (List) and do
  not count; Get still answers the kept one (its log). A dev plugin's
  sample run (Start ``sample=True``: PluginHooks.TrySample) is a hook
  trigger kept like a manual run: its folder and artifacts stay, it counts
  towards RUNS_KEPT_PER_PLUGIN and Get answers it, but it is not listed
  (SAMPLE_KEY in job.json says which). A folder is removed
  job.json last, so one cut short is still found and removed again;
- the launch's Sweep (job.json still running -> FAILED / AUTONA_EXITED)
  and the shells' Shutdown (cancel this module's runs, wait the grace).
  The sweep leaves alone a run of this process (active, or started since
  PluginJobs was made: the sweep is a job, a run may start meanwhile) and
  a run of another Autona still alive on the same work folder: job.json
  carries its host's process (HOST_KEY, private: not contract_1.json's
  job_record, never read into a RunRecord or served) and the sweep asks
  whether that process is still the same one running.

Callbacks: ``onRunEnded(record)``, then ``onLaneFreed(lane)``, outside
both locks on the thread that ended the run (a runner thread), or that
released a claim. **Start never calls them**: a start refused after its
lane was reserved gives the lane back without onLaneFreed (a hook held
meanwhile waits for the next free lane), and a run cancelled while being
prepared is returned ended (CANCELLED) by Start and gets no onRunEnded.

Secrets: the PluginSecrets environment goes to the runner's
BuildEnvironment only. A secret setting key is dropped from ``settings``
before anything is written, and the environment is never written or
logged.

Threads: none of its own. A run's events and its end arrive on the
runner's threads (onEvent / onEnd; onEnd is never called inside
runner.Start). ``_lock`` is a leaf: it guards the lanes and the runs'
in-memory records only; no file, no runner / registry / inputs call and no
callback is made while holding it (Shutdown waits on its condition for a
run being launched to get its handle). ``_filesLock`` (also a leaf, never
held with ``_lock``) serialises job.json's reads and writes. A run that
ended stays in memory until its job.json is written (Get / List read it
there first).

``main.log`` gets one line at a run's start and one at its end: the plugin
id, the job id, the run id, the lane and the end state. No value, path or
player name; a failure logs its exception's type and errno only.

Nothing is made, read or written on import (pitfalls #1); no Qt
(tests/test_core_qt_free.py).
"""

import dataclasses
import datetime
import errno
import json
import math
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
    ACTION_FILLS, CONTEXT_ONLY_TYPES, ERROR_HINT_MAX_LENGTH,
    ERROR_MESSAGE_MAX_LENGTH, HOOK_FILLS, HOOK_TIMEOUT_CAP_SECONDS,
    HOST_SPEAKS_PROTOCOLS, IMAGE_ARTIFACT_EXTENSIONS,
    IMAGE_ARTIFACT_MAX_BYTES, INPUTS_DIR, JOB_FILE, LABEL_MAX_LENGTH,
    LOG_FILE, LOG_MESSAGE_MAX_LENGTH, MAX_ARTIFACTS, MAX_WARNINGS, OUT_DIR,
    PLUGIN_ID_PATTERN, REQUEST_FILE, RUN_ID_PATTERN, RUNS_KEPT_PER_PLUGIN,
    SOURCE_FIELD_TYPES, SUMMARY_MAX_LENGTH, TEXT_ARTIFACT_MAX_BYTES,
    VALUE_FIELD_TYPES, ActionTarget, Artifact, ArtifactKind,
    ArtifactRejection, AutonaApiUse, Badge, DoneEvent, EndReason, ErrorEvent,
    FieldType, HookEvent, HostInfo, InputFile, InputPick, Job, JobState, Lane,
    LaunchSpec, LogEvent, Manifest, PluginState, ProblemScope, ProgressEvent,
    Refused, RunOutcome, RunRecord, StageEvent, StartRefusal, Text, Trigger,
    TriggerKind, WarningEvent)

_PLUGIN_ID = re.compile(PLUGIN_ID_PATTERN)
_RUN_ID = re.compile(RUN_ID_PATTERN)
_RUN_ID_CHARACTERS = string.ascii_lowercase + string.digits
# The run id's first suffix character: a sequence within one second, in
# the order the ids sort (digits before letters), so the newest sorts last.
_SEQUENCE_CHARACTERS = string.digits + string.ascii_lowercase
_RUN_ID_ATTEMPTS = 10
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.05
# How much of a dropped artifact's path a host warning repeats, and how
# many unknown kinds are named one by one (the rest is a count).
_WARNING_NAME_MAX = 200
_UNKNOWN_KIND_NAMED = 10
_FAILED_HOOK_STATES = frozenset({JobState.FAILED, JobState.ERROR})
_PLUGINS_DIR = "plugins"
_DEV_DIR = "_dev"
_DATA_DIR = "data"
# job.json's private key: the host process that wrote it, {"pid": int,
# "started": int | null} (its creation time, so a reused pid is not taken
# for it). Not contract_1.json's job_record: RecordFrom never reads it and
# the routes never serve it; only the Sweep asks it.
HOST_KEY = "_host"
# job.json's private key of a sample run (true; absent otherwise): a hook
# trigger kept like a manual run (_Prune, the Sweep). Private like HOST_KEY.
SAMPLE_KEY = "_sample"
# OpenProcess's access for the creation time and the exit code, the error
# it gives for a process that is there but not ours to ask, and the exit
# code of one still running.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_STILL_ACTIVE = 259
_kernel32 = None

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
    return "".join(secrets.choice(_RUN_ID_CHARACTERS) for _ in range(3))


def _Iso(moment):
    return moment.isoformat(timespec="seconds")


def _Kernel32():
    """kernel32 with the calls the host's identity needs, made on first use
    (nothing on import), an instance of its own so its argtypes are no
    other module's."""
    global _kernel32
    if _kernel32 is None:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                         wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32 = kernel32
    return _kernel32


def _ProcessStarted(pid):
    """(alive, started) of process ``pid``: alive True / False, or None
    when it is there but cannot be asked (another user's: access denied);
    started its creation time (FILETIME ticks), None when not known. Off
    Windows, alive is asked with signal 0 and started is never known."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, None
        except OSError:
            return None, None
        return True, None
    import ctypes
    from ctypes import wintypes
    kernel32 = _Kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False,
                                  pid)
    if not handle:
        if ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
            return None, None
        return False, None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None, None
        if code.value != _STILL_ACTIVE:
            return False, None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *map(ctypes.byref, times)):
            return True, None
        created = times[0]
        return True, (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def ThisHost():
    """This process as job.json's HOST_KEY names it."""
    pid = os.getpid()
    try:
        _alive, started = _ProcessStarted(pid)
    except (OSError, AttributeError, ValueError):
        started = None
    return {"pid": pid, "started": started}


def HostAlive(host) -> bool:
    """Whether the host job.json's HOST_KEY names (a value read back, any
    shape) is still that process running: its pid alive and, when both are
    known, created at the same time (a pid reused by another process is
    not it). One that is there but cannot be asked counts as alive -- its
    run is left alone rather than marked failed under it. False for what
    is not a host."""
    if not isinstance(host, Mapping):
        return False
    pid, started = host.get("pid"), host.get("started")
    if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            or not (started is None or (isinstance(started, int)
                                        and not isinstance(started, bool)))):
        return False
    try:
        alive, now = _ProcessStarted(pid)
    except (OSError, AttributeError, ValueError, OverflowError):
        return True
    if alive is None:
        return True
    if not alive:
        return False
    return started is None or now is None or now == started


def _StartedSince(text, moment):
    """Whether a record's started_at (ISO 8601 with offset) is ``moment``
    or later; False for what cannot be read as one."""
    try:
        return datetime.datetime.fromisoformat(text) >= moment
    except (TypeError, ValueError):
        return False


def _Failure(error):
    """An exception for main.log: its type and errno, never its text (an
    OSError's carries the user's paths)."""
    number = getattr(error, "errno", None)
    name = type(error).__name__
    if isinstance(number, int):
        return f"{name} ({errno.errorcode.get(number, number)})"
    return name


def _Short(text):
    text = str(text)
    return text if len(text) <= _WARNING_NAME_MAX \
        else text[:_WARNING_NAME_MAX] + "…"


def _Clip(text, limit):
    """A text cut to ``limit`` characters (each value of a locale map)."""
    if text is None:
        return None
    if isinstance(text, str):
        return text[:limit]
    return {key: value[:limit] for key, value in text.items()}


def _Finite(value):
    return value if isinstance(value, (int, float)) \
        and not isinstance(value, bool) and math.isfinite(value) else None


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


def _Unlink(path):
    """Remove a link or junction itself (never what it points at)."""
    try:
        os.unlink(path)
    except OSError:
        try:
            os.rmdir(path)
        except OSError:
            pass


def _RemoveTree(path):
    """Remove a folder this module made (inputs/, out/). rmtree does not
    follow a link or junction inside it; one in its place is unlinked."""
    if _IsLink(path):
        _Unlink(path)
    else:
        shutil.rmtree(path, ignore_errors=True)


def _RemoveRun(folder):
    """Remove a run folder, job.json last: one cut short keeps its job.json
    and is found (and removed) again by the next keeping pass."""
    if _IsLink(folder):
        _Unlink(folder)
        return
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        if entry.name == JOB_FILE:
            continue
        if entry.is_symlink() or entry.is_junction():
            _Unlink(entry.path)
        elif entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path, ignore_errors=True)
        else:
            try:
                os.remove(entry.path)
            except OSError:
                pass
    try:
        os.remove(os.path.join(folder, JOB_FILE))
    except FileNotFoundError:
        pass
    except OSError:
        return
    try:
        os.rmdir(folder)
    except OSError:
        pass


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
    except (OSError, ValueError):
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
    done line: the first unknown kinds by name, then the rest (more
    unknown kinds, past MAX_ARTIFACTS or unreadable) as one count."""
    raw = finalRaw if isinstance(finalRaw, Mapping) else final.raw
    items = raw.get("artifacts") if isinstance(raw, Mapping) else None
    if not isinstance(items, list):
        return []
    known = {kind.value for kind in ArtifactKind}
    warnings = []
    named = 0
    for item in items:
        if named == _UNKNOWN_KIND_NAMED:
            break
        if (isinstance(item, Mapping) and isinstance(item.get("kind"), str)
                and item["kind"] not in known):
            named += 1
            path = item.get("path")
            warnings.append(_RejectionWarning(
                path if isinstance(path, str) else "?",
                ArtifactRejection.UNKNOWN_KIND))
    rest = len(items) - len(final.artifacts) - named
    if rest > 0:
        warnings.append({
            "ja": f"成果物 {rest} 件を捨てました（{MAX_ARTIFACTS} 件を超えた分・"
                  f"知らない種類・読めない項目）",
            "en": f"Dropped {rest} artifact(s) (past {MAX_ARTIFACTS}, of an "
                  f"unknown kind, or unreadable)"})
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


def _NormalTrigger(trigger: Trigger) -> Trigger:
    """The trigger with the keys of its kind only (raises ValueError /
    AttributeError for what is not one)."""
    kind = TriggerKind(trigger.kind)
    if kind is TriggerKind.MANUAL:
        return Trigger(kind=kind)
    if kind is TriggerKind.ACTION:
        return Trigger(kind=kind, target=ActionTarget(trigger.target))
    if not isinstance(trigger.hook, str):
        raise ValueError("a hook trigger without its key")
    return Trigger(kind=kind, event=HookEvent(trigger.event),
                   hook=trigger.hook)


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
        return _NormalTrigger(Trigger(
            kind=raw.get("kind"), target=raw.get("target"),
            event=raw.get("event"), hook=raw.get("hook")))
    except (AttributeError, ValueError):
        return None


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


def _Str(value):
    return value if isinstance(value, str) else None


def _List(value):
    return value if isinstance(value, list) else []


def _StageFrom(raw):
    if not isinstance(raw, Mapping) or not isinstance(raw.get("stage"), str):
        return None
    return StageEvent(stage=raw["stage"], label=_TextFrom(raw.get("label")),
                      index=_Int(raw.get("index")),
                      count=_Int(raw.get("count")))


def _ProgressFrom(raw):
    if not isinstance(raw, Mapping):
        return None
    return ProgressEvent(stage=_Str(raw.get("stage")),
                         fraction=_Finite(raw.get("fraction")),
                         overall=_Finite(raw.get("overall")),
                         eta=_Finite(raw.get("eta")))


def _ArtifactsFrom(raw):
    out = []
    for item in _List(raw):
        if not isinstance(item, Mapping) or not isinstance(item.get("path"),
                                                           str):
            continue
        try:
            kind = ArtifactKind(item.get("kind"))
        except ValueError:
            continue
        out.append(Artifact(path=item["path"], kind=kind,
                            label=_TextFrom(item.get("label"))))
    return tuple(out)


def _ErrorFrom(raw):
    if not isinstance(raw, Mapping) or not isinstance(raw.get("code"), str):
        return None
    return ErrorEvent(code=raw["code"],
                      message=_TextFrom(raw.get("message")) or "",
                      hint=_TextFrom(raw.get("hint")))


def RecordFrom(raw: Any) -> Optional[RunRecord]:
    """A job.json object read back, None when it is not one. Every part is
    type-checked (the folder is the plugin's to write)."""
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
    except (TypeError, ValueError):
        return None
    warnings = tuple(text for text in map(_TextFrom,
                                          _List(raw.get("warnings")))
                     if text is not None)
    return RunRecord(
        run_id=runId, plugin=plugin, job=job, trigger=trigger, lane=lane,
        state=state, started_at=started, reason=reason,
        finished_at=_Str(raw.get("finished_at")),
        exit_code=_Int(raw.get("exit_code")),
        stage=_StageFrom(raw.get("stage")),
        progress=_ProgressFrom(raw.get("progress")),
        log=_TextFrom(raw.get("log")), warnings=warnings,
        summary=_TextFrom(raw.get("summary")),
        artifacts=_ArtifactsFrom(raw.get("artifacts")),
        error=_ErrorFrom(raw.get("error")),
        cancel_requested=raw.get("cancel_requested") is True,
        finished_before_cancel=raw.get("finished_before_cancel") is True,
        timeout_seconds=_Int(raw.get("timeout_seconds")),
        peak_memory_bytes=_Int(raw.get("peak_memory_bytes")))


def _WriteJson(path, content):
    """Write ``content`` as JSON whole: ASCII-escaped (so valid UTF-8 with
    no BOM whatever the strings hold), temp file, fsynced, os.replace
    (tried again a few times: on Windows a replace fails while a reader
    has the file open). Raises OSError, TypeError / ValueError for what is
    not JSON."""
    data = json.dumps(content, ensure_ascii=True, allow_nan=False, indent=2)
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
    # None: no time limit (a built-in job's only).
    timeout: Optional[int]
    argv: Tuple[str, ...]
    params: dict
    settings: dict
    sources: Tuple[Tuple[str, FieldType, bool], ...]
    picks: Mapping[str, InputPick]


@dataclass
class _Claim:
    """A lane held by another owner (Claim)."""
    owner: str


class _Run:
    """A run holding a lane (reserved, launching, then launched): its
    record's changing fields, guarded by PluginJobs._lock."""

    def __init__(self, plan: _Plan, started: datetime.datetime):
        self.plan = plan
        self.pluginId = plan.pluginId
        self.lane = plan.lane
        self.started = started
        self.runId: Optional[str] = None
        self.folder: Optional[str] = None
        self.handle = None
        self.launching = False
        self.ended = False
        self.cancelRequested = False
        self.state = JobState.RUNNING
        self.reason: Optional[EndReason] = None
        self.finishedAt: Optional[str] = None
        self.exitCode: Optional[int] = None
        self.stage: Optional[StageEvent] = None
        self.progress: Optional[ProgressEvent] = None
        self.progressAt: Optional[datetime.datetime] = None
        self.log: Optional[Text] = None
        self.warnings: list = []
        self.workerWarnings = 0
        self.summary: Optional[Text] = None
        self.artifacts: Tuple[Artifact, ...] = ()
        self.error: Optional[ErrorEvent] = None
        self.finishedBeforeCancel = False
        self.peak: Optional[int] = None
        # A sample run (Start sample=True): kept like a manual run.
        self.sample = False

    @property
    def hook(self):
        """Whether its folder goes as a hook run's (a sample run's stays)."""
        return (TriggerKind(self.plan.trigger.kind) is TriggerKind.HOOK
                and not self.sample)

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


def _Unavailable(detail):
    return Refused(reason=StartRefusal.UNAVAILABLE, detail=detail)


# ── the service ─────────────────────────────────────────────────────────

class PluginJobs:
    """PluginTypes.PluginJobs (plus Claim / Release). Every collaborator is
    injected:

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
    - ``randomSuffix()``: the run id's last three characters ([a-z0-9]);
    - ``host``: this process as job.json's HOST_KEY names it (ThisHost);
    - ``hostAlive(host)``: whether a host read back from job.json is still
      running (HostAlive): the Sweep leaves its runs alone.
    """

    # The one bootstrap_core() makes (ADR-009 E.1); None until then.
    instance = None

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
                 host: Optional[Mapping[str, Any]] = None,
                 hostAlive: Callable[[Any], bool] = HostAlive):
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
        self._host = dict(host) if host is not None else ThisHost()
        self._hostAlive = hostAlive
        # A run started at this second or later is this process's (the
        # sweep's job may come after a start; started_at has seconds).
        self._madeAt = clock().replace(microsecond=0)
        self._lock = threading.Lock()
        self._launched = threading.Condition(self._lock)
        self._filesLock = threading.Lock()
        self._lanes: dict = {Lane.HEAVY: None, Lane.LIGHT: None}
        # Plugin id -> how many holds keep its starts refused (a swap, a
        # removal, a disable, a block: Hold / Unhold), and how many holds it
        # has had (a start planned before one is refused at its lane).
        self._held: dict = {}
        self._holdEpochs: dict = {}
        # Runs that ended and whose job.json is not written yet.
        self._finishing: dict = {}
        self._closing = False
        self._idStamp = None
        self._idSequence = 0

    # ── paths ──────────────────────────────────────────────────────────

    def _PluginWork(self, pluginId, make=False):
        """The plugin's work folder, None when it (or plugins/) is a link
        or junction, or resolves outside the work folder."""
        workDir = self._workDir()
        root = os.path.join(workDir, _PLUGINS_DIR)
        path = os.path.join(root, pluginId)
        if make:
            os.makedirs(path, exist_ok=True)
        if _IsLink(root) or _IsLink(path):
            return None
        if os.path.lexists(path) and not _Inside(
                os.path.realpath(workDir), os.path.realpath(path)):
            return None
        return path

    def _RunFolder(self, pluginId, runId):
        """The run's folder on disk (not that it exists), None for what is
        not an id or would resolve outside the plugin's work folder."""
        if (not isinstance(pluginId, str) or not _PLUGIN_ID.fullmatch(pluginId)
                or not isinstance(runId, str) or not _RUN_ID.fullmatch(runId)):
            return None
        base = self._PluginWork(pluginId)
        if base is None:
            return None
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
            except (OSError, ValueError) as error:
                logger.warning(f"Plugins: {JOB_FILE} of run "
                               f"{os.path.basename(folder)} could not be "
                               f"read: {_Failure(error)}")
                return None
        try:
            return RecordFrom(raw)
        except Exception as error:
            logger.warning(f"Plugins: {JOB_FILE} of run "
                           f"{os.path.basename(folder)} is not a record: "
                           f"{_Failure(error)}")
            return None

    def _WriteRecord(self, folder, record, sample=False):
        content = RecordJson(record)
        content[HOST_KEY] = self._host
        if sample:
            content[SAMPLE_KEY] = True
        with self._filesLock:
            _WriteJson(os.path.join(folder, JOB_FILE), content)

    def _RecordKey(self, folder, key):
        """job.json's private ``key`` as written (any shape), None when
        there is none or the file cannot be read."""
        with self._filesLock:
            try:
                with open(os.path.join(folder, JOB_FILE),
                          encoding="utf-8") as file:
                    raw = json.load(file)
            except (OSError, ValueError):
                return None
        return raw.get(key) if isinstance(raw, Mapping) else None

    def _RecordHost(self, folder):
        """job.json's HOST_KEY as written (any shape), None when there is
        none or the file cannot be read."""
        return self._RecordKey(folder, HOST_KEY)

    def _HookRun(self, folder, record):
        """Whether a run read back is a hook run kept as one (a sample run
        is kept like a manual run)."""
        return (record.trigger.kind is TriggerKind.HOOK
                and self._RecordKey(folder, SAMPLE_KEY) is not True)

    # ── the lanes ──────────────────────────────────────────────────────

    def _Remaining(self, run):
        """Seconds until ``run`` likely ends: the worker's eta (counted down
        since it came) or its time limit, whichever is sooner; None when
        neither is known."""
        now = self._clock()
        candidates = []
        if run.progress is not None and run.progress.eta is not None:
            since = (now - run.progressAt).total_seconds()
            candidates.append(run.progress.eta - since)
        if run.plan.timeout is not None:
            elapsed = (now - run.started).total_seconds()
            candidates.append(run.plan.timeout - elapsed)
        return max(0.0, min(candidates)) if candidates else None

    def _BusyLocked(self, lane):
        slot = self._lanes[lane]
        if slot is None:
            return None
        remaining = self._Remaining(slot) if isinstance(slot, _Run) else None
        return Refused(reason=StartRefusal.BUSY, remaining_seconds=remaining)

    def BusyFor(self, lane: Lane) -> Optional[Refused]:
        lane = Lane(lane)
        with self._lock:
            return self._BusyLocked(lane)

    def Claim(self, lane: Lane, owner: str) -> bool:
        """Take ``lane`` for another owner (the built-in vodsplit on the
        heavy one): True when it was free. Refused while shutting down."""
        lane = Lane(lane)
        with self._lock:
            if self._closing or self._lanes[lane] is not None:
                return False
            self._lanes[lane] = _Claim(owner)
            return True

    def Release(self, lane: Lane, owner: str) -> None:
        """Give back ``owner``'s claim of ``lane`` (nothing when it holds
        none); onLaneFreed on this thread."""
        lane = Lane(lane)
        with self._lock:
            slot = self._lanes[lane]
            freed = isinstance(slot, _Claim) and slot.owner == owner
            if freed:
                self._lanes[lane] = None
        if freed:
            self._Call(self._onLaneFreed, lane)

    def Running(self, pluginId: str) -> bool:
        with self._lock:
            return self._RunningLocked(pluginId)

    def _RunningLocked(self, pluginId):
        """A run of the plugin holds a lane, or one that ended is still
        writing its folder (job.json, inputs/: _Settle, after the lane is
        freed) -- a removal must not take the folder under it. Under
        _lock."""
        return any(
            (isinstance(slot, _Run) and slot.pluginId == pluginId)
            or (isinstance(slot, _Claim) and slot.owner == pluginId)
            for slot in self._lanes.values()) or any(
            key[0] == pluginId for key in self._finishing)

    def Hold(self, pluginId: str) -> bool:
        """Refuse the plugin's starts (UNAVAILABLE) until Unhold, for a swap
        or a removal (ADR-009 C.3): True when no run of it is Running.
        Counted, and given back by Unhold whatever it answered. Taken under
        the lock that reserves a lane, so a start either got its lane
        before (False here) or sees the hold."""
        with self._lock:
            self._held[pluginId] = self._held.get(pluginId, 0) + 1
            self._holdEpochs[pluginId] = self._holdEpochs.get(pluginId, 0) + 1
            return not self._RunningLocked(pluginId)

    def Unhold(self, pluginId: str) -> None:
        with self._lock:
            count = self._held.get(pluginId, 0) - 1
            if count > 0:
                self._held[pluginId] = count
            else:
                self._held.pop(pluginId, None)

    def CancelAll(self, pluginId: str) -> int:
        """Cancel every run of the plugin holding a lane (a disable, a
        removal, a block): how many were asked. A claim is its owner's."""
        # As Cancel, for each: one being prepared (no id yet) is not
        # launched, one being launched is cancelled once it has its handle.
        with self._lock:
            runs = [run for run in self._Runs()
                    if run.pluginId == pluginId and not run.ended]
            for run in runs:
                run.cancelRequested = True
            handles = [run.handle for run in runs if run.handle is not None]
        for handle in handles:
            handle.Cancel()
        return len(runs)

    def _Runs(self):
        """This module's runs holding a lane (under _lock)."""
        return [slot for slot in self._lanes.values()
                if isinstance(slot, _Run)]

    def _Active(self, pluginId, runId):
        """The in-memory run: holding a lane or finishing (under _lock)."""
        run = self._finishing.get((pluginId, runId))
        if run is not None:
            return run
        for run in self._Runs():
            if run.pluginId == pluginId and run.runId == runId:
                return run
        return None

    def _ActiveIds(self, finishing=True):
        """The runs holding a lane, and (``finishing``) the ended ones whose
        job.json is being written. The keeping pass leaves the latter out:
        it runs after the write, and a job.json still saying running is
        never removed anyway."""
        with self._lock:
            ids = {(run.pluginId, run.runId) for run in self._Runs()
                   if run.runId is not None}
            return ids | set(self._finishing) if finishing else ids

    # ── the start's checks ─────────────────────────────────────────────

    def _Plan(self, pluginId, jobId, trigger, params, settings, picks):
        """The checked start, or raises _Refuse. Calls the registry (no
        lock held)."""
        plugin = self._registry.Get(pluginId) \
            if isinstance(pluginId, str) and _PLUGIN_ID.fullmatch(pluginId) \
            else None
        if plugin is None:
            raise _Refusal(StartRefusal.NOT_FOUND)
        if Badge(plugin.badge) is Badge.BUILTIN:
            # The built-in vodsplit runs through ArchiveJobService.
            raise _Refusal(StartRefusal.JOB_UNUSABLE, "built-in")
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
        dev = Badge(plugin.badge) is Badge.DEV
        argv = self._Argv(plugin.folder, manifest.entry, dev)
        if argv is None:
            raise _Refusal(StartRefusal.INCOMPATIBLE, "entry outside")

        try:
            trigger = _NormalTrigger(trigger)
        except (AttributeError, ValueError):
            raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown trigger")
        fills = self._TriggerFills(job, lane, trigger)
        values = {}
        valueKeys = {field.key for field, kind in fields
                     if kind in VALUE_FIELD_TYPES
                     and kind is not FieldType.SECRET}
        if set(params or {}) - valueKeys:
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
            kind = getattr(pick, "kind", None)
            if (sourceKinds.get(key) is None or getattr(pick, "key", None) != key
                    or kind not in FieldType
                    or FieldType(kind) is not sourceKinds[key]
                    or FieldType(kind) is FieldType.STATE):
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
        if trigger.kind is TriggerKind.HOOK:
            timeout = (HOOK_TIMEOUT_CAP_SECONDS if timeout is None
                       else min(timeout, HOOK_TIMEOUT_CAP_SECONDS))
        return _Plan(pluginId=pluginId, job=job, manifest=manifest,
                     folder=plugin.folder, dev=dev, protocol=protocol,
                     lane=lane, trigger=trigger, timeout=timeout, argv=argv,
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

    @staticmethod
    def _Argv(pluginFolder, entry, dev):
        """The manifest's entry with its file resolved to an absolute path
        in the plugin folder (cwd is the job folder); None when an installed
        entry's file resolves outside it. A dev entry's first word may be a
        command found on PATH (left as is); its later words are resolved
        when they name a file in the plugin folder."""
        folder = os.path.abspath(pluginFolder)
        resolved = []
        for index, word in enumerate(entry):
            candidate = os.path.normpath(os.path.join(folder, word))
            inFolder = not os.path.isabs(word) and _Inside(folder, candidate)
            if index == 0 and not dev:
                if not inFolder:
                    return None
                resolved.append(candidate)
            elif dev and inFolder and os.path.isfile(candidate):
                resolved.append(candidate)
            else:
                resolved.append(word)
        return tuple(resolved)

    @staticmethod
    def _TriggerFills(job, lane, trigger):
        """The FieldType a (normalised) trigger fills, None for manual; or
        raises _Refuse."""
        if trigger.kind is TriggerKind.MANUAL:
            if not job.manual:
                raise _Refusal(StartRefusal.INVALID_REQUEST,
                               "the job is not started by hand")
            return None
        if lane is not Lane.LIGHT:
            raise _Refusal(StartRefusal.JOB_UNUSABLE)
        if trigger.kind is TriggerKind.ACTION:
            if trigger.target.value not in {action.target
                                            for action in job.actions}:
                raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown action")
            return ACTION_FILLS[trigger.target]
        if (trigger.event.value not in {hook.on for hook in job.hooks}
                or trigger.hook != f"{job.id}/{trigger.event.value}"):
            raise _Refusal(StartRefusal.INVALID_REQUEST, "unknown hook")
        return HOOK_FILLS[trigger.event]

    # ── Start ──────────────────────────────────────────────────────────

    def Start(self, pluginId: str, jobId: str, trigger: Trigger,
              params: Mapping[str, Any], settings: Mapping[str, Any],
              picks: Mapping[str, InputPick], *,
              sample: bool = False) -> Union[RunRecord, Refused]:
        """Start a run or say why not. Never calls onRunEnded / onLaneFreed
        (see the module): what fails before the launch is in the return
        value only. ``sample``: a hook trigger's sample run (TrySample),
        kept like a manual run (nothing for another trigger)."""
        with self._lock:
            if self._closing:
                return _Unavailable("shutting down")
            # What the plan reads (the registry's manifest and state: the
            # enable, the blocklist) is read outside the lock; a hold taken
            # since (a swap, a removal, a disable, a block) refuses the
            # start at its lane, so no run starts from a stale plan.
            epoch = self._holdEpochs.get(pluginId, 0)
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
        run = _Run(plan, self._clock())
        run.sample = bool(sample) and TriggerKind(plan.trigger.kind) \
            is TriggerKind.HOOK
        with self._lock:
            if self._closing:
                return _Unavailable("shutting down")
            if self._held.get(pluginId) \
                    or self._holdEpochs.get(pluginId, 0) != epoch:
                # A swap, a removal, a disable or a block of it is going,
                # or went since the plan was read (Hold).
                return _Unavailable("the plugin is being installed or "
                                    "removed")
            busy = self._BusyLocked(plan.lane)
            if busy is not None:
                return busy
            self._lanes[plan.lane] = run

        try:
            spec = self._Prepare(run)
        except _Refuse as refusal:
            self._GiveBack(run)
            return refusal.refused
        except Exception as error:
            logger.warning(f"Plugins: a run of {pluginId}/{jobId} could not "
                           f"be prepared: {_Failure(error)}")
            self._GiveBack(run)
            return _Unavailable("the run could not be prepared")

        with self._lock:
            closing = self._closing
            cancelled = run.cancelRequested
            run.launching = not closing and not cancelled
        if closing:
            self._GiveBack(run)
            return _Unavailable("shutting down")
        if cancelled:
            return self._Finish(run, RunOutcome(state=JobState.CANCELLED,
                                                cancel_requested=True),
                                callbacks=False)

        logger.info(f"Plugins: run {run.runId} of {pluginId}/{jobId} "
                    f"started ({plan.lane.value}, {plan.trigger.kind.value})")
        try:
            handle = self._runner.Start(
                spec, lambda event: self._OnEvent(run, event),
                lambda outcome: self._Finish(run, outcome))
        except Exception as error:
            logger.warning(f"Plugins: run {run.runId} could not be launched: "
                           f"{_Failure(error)}")
            with self._launched:
                run.launching = False
                self._launched.notify_all()
            self._GiveBack(run)
            return _Unavailable("the run could not be launched")
        with self._launched:
            run.handle = handle
            run.launching = False
            self._launched.notify_all()
            cancel = run.cancelRequested and not run.ended
            record = run.Record()
        if cancel:
            handle.Cancel()
        return record

    def _GiveBack(self, run):
        """A reserved lane back after a refused start (the folder made so
        far removed). No onLaneFreed: Start calls no callback."""
        with self._lock:
            if self._lanes[run.lane] is run:
                self._lanes[run.lane] = None
            run.ended = True
        if run.folder is not None:
            _RemoveRun(run.folder)

    def _NewRunFolder(self, run):
        pluginWork = self._PluginWork(run.pluginId, make=True)
        if pluginWork is None:
            raise OSError(errno.EPERM, "the plugin's work folder is a link")
        for attempt in range(_RUN_ID_ATTEMPTS):
            stamp = self._clock().strftime("%Y%m%d-%H%M%S")
            with self._lock:
                if stamp != self._idStamp:
                    self._idStamp, self._idSequence = stamp, 0
                sequence = _SEQUENCE_CHARACTERS[
                    min(self._idSequence, len(_SEQUENCE_CHARACTERS) - 1)]
                self._idSequence += 1
            runId = f"{stamp}-{sequence}{self._randomSuffix()}"
            if not _RUN_ID.fullmatch(runId):
                raise ValueError("the run id suffix is not [a-z0-9]{3}")
            folder = os.path.abspath(os.path.join(pluginWork, runId))
            try:
                os.mkdir(folder)
            except FileExistsError:
                continue
            return runId, folder
        raise FileExistsError(errno.EEXIST, "no free run id")

    def _Prepare(self, run) -> LaunchSpec:
        """The run's folder, inputs, request.json and job.json, then the
        launch. Raises _Refuse, or anything a collaborator raises."""
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

        manual = plan.trigger.kind is TriggerKind.MANUAL
        inputs = {}
        for key, kind, required in plan.sources:
            pick = plan.picks.get(key)
            if manual and pick is None and kind is not FieldType.STATE:
                # Not picked by hand: null (only STATE is the press's).
                inputs[key] = None
                continue
            try:
                item = self._inputs.Prepare(key, kind, inputsDir, pick)
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
        self._WriteRecord(folder, record, run.sample)

        manifest = plan.manifest
        apiUrl = self._apiUrl() if manifest.permissions.autona_api in (
            AutonaApiUse.READ.value, AutonaApiUse.WRITE.value) else None
        env = self._runner.BuildEnvironment(
            self._environ(), pluginDir=plan.folder, protocol=plan.protocol,
            secrets=self._secrets.Environment(plan.pluginId, dev=plan.dev),
            apiUrl=apiUrl)
        return LaunchSpec(
            argv=plan.argv + ("run", requestPath), cwd=folder, env=env,
            lane=plan.lane, log_path=os.path.join(folder, LOG_FILE),
            timeout_seconds=plan.timeout, memory_mb=plan.job.memory_mb)

    # ── a run's events and end (the runner's threads) ──────────────────

    def _OnEvent(self, run, event):
        now = self._clock()
        with self._lock:
            if run.ended:
                return
            if isinstance(event, StageEvent):
                run.stage = dataclasses.replace(
                    event, label=_Clip(event.label, LABEL_MAX_LENGTH))
                run.progress = None
            elif isinstance(event, ProgressEvent):
                run.progress = dataclasses.replace(
                    event, fraction=_Finite(event.fraction),
                    overall=_Finite(event.overall), eta=_Finite(event.eta))
                run.progressAt = now
            elif isinstance(event, LogEvent):
                run.log = _Clip(event.message, LOG_MESSAGE_MAX_LENGTH)
            elif isinstance(event, WarningEvent):
                if run.workerWarnings < MAX_WARNINGS:
                    run.workerWarnings += 1
                    run.warnings.append(
                        _Clip(event.message, LOG_MESSAGE_MAX_LENGTH))

    def _Checked(self, run, final, finalRaw):
        """(accepted artifacts, host warnings) of a done line. A hook run
        keeps none (its folder goes)."""
        if not isinstance(final, DoneEvent) or run.hook:
            return (), []
        accepted = []
        warnings = []
        outDir = os.path.join(run.folder, OUT_DIR)
        for artifact in final.artifacts:
            try:
                real, rejection = CheckArtifact(outDir, artifact)
            except Exception as error:
                logger.warning(f"Plugins: an artifact of run {run.runId} "
                               f"could not be checked: {_Failure(error)}")
                rejection = ArtifactRejection.MISSING
            if rejection is None:
                accepted.append(dataclasses.replace(
                    artifact, label=_Clip(artifact.label, LABEL_MAX_LENGTH)))
            else:
                warnings.append(_RejectionWarning(artifact.path, rejection))
        try:
            warnings.extend(_RunnerDropWarnings(final, finalRaw))
        except Exception as error:
            logger.warning(f"Plugins: run {run.runId}'s dropped artifacts "
                           f"could not be counted: {_Failure(error)}")
        return tuple(accepted), warnings

    def _Finish(self, run, outcome: RunOutcome, callbacks=True):
        """A run's end (the runner's onEnd, or a run cancelled before its
        launch with ``callbacks`` False): the record, job.json, the
        folders, then the callbacks -- always, whatever failed before."""
        final = outcome.final
        try:
            accepted, hostWarnings = self._Checked(run, final,
                                                   outcome.final_raw)
        except Exception as error:
            logger.warning(f"Plugins: run {run.runId}'s artifacts could not "
                           f"be checked: {_Failure(error)}")
            accepted, hostWarnings = (), []

        with self._lock:
            if run.ended:
                return run.Record()
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
                run.summary = _Clip(final.summary, SUMMARY_MAX_LENGTH)
                run.artifacts = accepted
            elif isinstance(final, ErrorEvent):
                run.error = dataclasses.replace(
                    final, message=_Clip(final.message,
                                         ERROR_MESSAGE_MAX_LENGTH),
                    hint=_Clip(final.hint, ERROR_HINT_MAX_LENGTH))
            run.warnings.extend(hostWarnings)
            if self._lanes[run.lane] is run:
                self._lanes[run.lane] = None
            key = (run.pluginId, run.runId)
            self._finishing[key] = run
            record = run.Record()

        try:
            self._Settle(run, record)
        finally:
            with self._lock:
                self._finishing.pop(key, None)
            logger.info(f"Plugins: run {record.run_id} of "
                        f"{record.plugin}/{record.job} ended "
                        f"{record.state.value}"
                        + (f" ({record.reason.value})" if record.reason
                           else ""))
            if callbacks:
                self._Call(self._onRunEnded, record)
                self._Call(self._onLaneFreed, run.lane)
        return record

    def _Settle(self, run, record):
        """An ended run's files: job.json, inputs/, a hook run's folder, the
        keeping rules. Each step's failure is logged and the next tried."""
        folder = run.folder
        steps = [("write job.json",
                  lambda: self._WriteRecord(folder, record, run.sample))]
        if not run.plan.dev:
            steps.append(("remove inputs", lambda: _RemoveTree(
                os.path.join(folder, INPUTS_DIR))))
        if run.hook:
            if record.state in _FAILED_HOOK_STATES:
                steps.append(("remove out", lambda: _RemoveTree(
                    os.path.join(folder, OUT_DIR))))
            else:
                steps.append(("remove the folder",
                              lambda: _RemoveRun(folder)))
        steps.append(("keep", lambda: self._Prune(os.path.dirname(folder))))
        for name, step in steps:
            try:
                step()
            except Exception as error:
                logger.warning(f"Plugins: run {record.run_id} could not "
                               f"{name}: {_Failure(error)}")

    @staticmethod
    def _Call(callback, argument):
        try:
            callback(argument)
        except Exception as error:
            logger.warning(f"Plugins: a run callback failed: "
                           f"{_Failure(error)}")

    # ── keeping ────────────────────────────────────────────────────────

    def _Scan(self, pluginWork):
        """(records, links): (folder, record) of every readable run folder
        of a plugin, and the run-id entries that are links or junctions."""
        try:
            entries = list(os.scandir(pluginWork))
        except OSError:
            return [], []
        records, links = [], []
        for entry in entries:
            if not _RUN_ID.fullmatch(entry.name):
                continue
            if entry.is_symlink() or entry.is_junction():
                links.append(entry.path)
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            record = self._ReadRecord(entry.path)
            if record is not None and record.run_id == entry.name:
                records.append((entry.path, record))
        return records, links

    def _Prune(self, pluginWork):
        """Keep the newest RUNS_KEPT_PER_PLUGIN manual / action / sample
        runs (a running one counts and stays) and, per hook, the newest
        failed run only. A folder whose job.json says running or cannot be
        read is never removed; a link or junction named like a run is
        unlinked."""
        active = self._ActiveIds(finishing=False)
        plugin = os.path.basename(pluginWork)
        records, links = self._Scan(pluginWork)
        for link in links:
            _Unlink(link)
        kept = []
        failedByHook = {}
        for folder, record in records:
            hook = self._HookRun(folder, record)
            if (plugin, record.run_id) in active:
                if not hook:
                    kept.append((record.run_id, folder, False))
                continue
            ended = record.state is not JobState.RUNNING
            if hook:
                if not ended:
                    continue
                if record.state in _FAILED_HOOK_STATES:
                    failedByHook.setdefault(record.trigger.hook, []).append(
                        (record.run_id, folder))
                else:
                    _RemoveRun(folder)
                continue
            kept.append((record.run_id, folder, ended))
        kept.sort(reverse=True)
        for runId, folder, ended in kept[RUNS_KEPT_PER_PLUGIN:]:
            if ended:
                _RemoveRun(folder)
        for runs in failedByHook.values():
            runs.sort(reverse=True)
            for runId, folder in runs[1:]:
                _RemoveRun(folder)

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
        in the hook's history; the kept failed one answers Get, and so does
        a sample run, by the run_id its start answered)."""
        if not isinstance(pluginId, str) or not _PLUGIN_ID.fullmatch(pluginId):
            return []
        with self._lock:
            runs = list(self._Runs()) + list(self._finishing.values())
            records = {run.runId: run.Record() for run in runs
                       if run.pluginId == pluginId and run.runId is not None}
        pluginWork = self._PluginWork(pluginId)
        if pluginWork is not None:
            for folder, record in self._Scan(pluginWork)[0]:
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
        """Ask a run to stop. One still being prepared is not launched (it
        ends CANCELLED); one being launched is cancelled once it has its
        handle."""
        with self._lock:
            run = self._Active(pluginId, runId)
            if run is None or run.ended:
                return False
            run.cancelRequested = True
            handle = run.handle
        if handle is not None:
            handle.Cancel()
        return True

    def Sweep(self, stopping: Callable[[], bool] = lambda: False) -> None:
        """At launch: every generic run job.json still calls running ->
        FAILED / AUTONA_EXITED, but a run of this process (active, or
        started since this object was made) and a run of another host still
        alive (HOST_KEY, hostAlive); then the keeping rules per plugin.
        ``stopping()`` is asked before each plugin: True ends the sweep
        there (the app is closing; the next launch sweeps the rest)."""
        root = os.path.join(self._workDir(), _PLUGINS_DIR)
        if _IsLink(root):
            return
        try:
            plugins = [entry.name for entry in os.scandir(root)
                       if _PLUGIN_ID.fullmatch(entry.name)
                       and not entry.is_symlink() and not entry.is_junction()
                       and entry.is_dir(follow_symlinks=False)]
        except OSError:
            return
        active = self._ActiveIds()
        finishedAt = _Iso(self._clock())
        for pluginId in plugins:
            if stopping():
                logger.info("Plugins: the sweep stopped: closing")
                return
            try:
                self._SweepPlugin(pluginId, active, finishedAt)
            except Exception as error:
                logger.warning(f"Plugins: the runs of {pluginId} could not "
                               f"be swept: {_Failure(error)}")

    def _SweepPlugin(self, pluginId, active, finishedAt):
        pluginWork = self._PluginWork(pluginId)
        if pluginWork is None:
            return
        registered = self._registry.Get(pluginId)
        dev = registered is not None and Badge(registered.badge) is Badge.DEV
        for folder, record in self._Scan(pluginWork)[0]:
            if (record.state is not JobState.RUNNING
                    or (pluginId, record.run_id) in active
                    or _StartedSince(record.started_at, self._madeAt)):
                continue
            if self._hostAlive(self._RecordHost(folder)):
                logger.info(f"Plugins: run {record.run_id} of {pluginId} "
                            "is another Autona's, still running; left alone")
                continue
            swept = dataclasses.replace(
                record, state=JobState.FAILED,
                reason=EndReason.AUTONA_EXITED, finished_at=finishedAt)
            hook = self._HookRun(folder, record)
            sample = record.trigger.kind is TriggerKind.HOOK and not hook
            if hook:
                swept = dataclasses.replace(swept, artifacts=())
            try:
                self._WriteRecord(folder, swept, sample)
            except Exception as error:
                logger.warning(f"Plugins: run {record.run_id} could not be "
                               f"marked failed: {_Failure(error)}")
                continue
            if not dev:
                _RemoveTree(os.path.join(folder, INPUTS_DIR))
            if hook:
                _RemoveTree(os.path.join(folder, OUT_DIR))
            logger.info(f"Plugins: run {record.run_id} of {pluginId} was "
                        f"left running; marked failed")
        self._Prune(pluginWork)

    def Shutdown(self, grace: float) -> None:
        """Refuse new starts and claims, cancel this module's runs (waiting
        for one being launched to get its handle), wait up to ``grace``
        seconds for them, then kill the ones still running, each by its own
        handle (RunHandle.Kill): the runner's Shutdown would stop runs that
        are not this module's (vodsplit's). A claimed lane is its owner's."""
        deadline = time.monotonic() + max(0.0, grace)
        with self._launched:
            self._closing = True
            runs = self._Runs()
            for run in runs:
                run.cancelRequested = True
            while any(run.launching and run.handle is None for run in runs):
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._launched.wait(left)
            handles = [run.handle for run in runs if run.handle is not None]
        for handle in handles:
            handle.Cancel()
        left = [handle for handle in handles
                if handle.Wait(max(0.0, deadline - time.monotonic())) is None]
        for handle in left:
            handle.Kill()
        if left:
            logger.warning(f"Plugins: {len(left)} run(s) still running after "
                           f"the shutdown grace: killed")
