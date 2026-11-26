"""The plugin v1 routes (ADR-009 D4; lane L11): the handlers of every route
of ``docs/plugins/routes_v1.json``, registered onto the v1 blueprint by
``RegisterPluginRoutes(blueprint, deps)``.

Nothing is registered on import: the wiring calls RegisterPluginRoutes
once, before the blueprint is registered on the app (WebServer's
``_build_web_app``), with a ``PluginApiDeps`` it may fill later -- every
handler reads its parts per request, and a part not given yet is answered
503 ``unavailable``. The access classes (WebAccess.ROUTE_ACCESS: the list
and the icon THIS_PC, the rest SHELL) and the API catalog (none of these
is in it) are the wiring's, as routes_v1.json says.

Answers (routes_v1.json envelope): ``{"ok": true, "data": <answer>}``
(``data`` null when a route answers nothing); the icon, a run's log and an
artifact are sent raw (fixed Content-Type, ``X-Content-Type-Options:
nosniff``). A refusal is ``{"ok": false, "error": {"code", "message",
"reason"}}``: ``reason`` the refusal (contract_1.json refusals, the
parts' ``Refused``), ``code`` the v1 code of its status, ``message`` its
detail -- for busy the whole seconds left (rounded up) when known. Every
answer is no-store.

What the handlers check before a part is called (the parts check again):

- a body is a JSON object with the route's keys only; a value of the wrong
  type, an unknown key or an unknown pick id is ``invalid_request``;
- a setting's or a param's value: ``PluginSettings.CheckFieldValue`` (a
  file / folder value is a pick id, resolved by ``deps.resolvePick``);
  ``null`` in a settings body removes the saved value; a param left out
  (or null) is its default (PluginJobs);
- a run's inputs: a source key left out (or null) is not picked -- never
  filled with null -- (PluginJobs decides whether that is fine); a choice
  id is a ``PluginInputs.Choices`` id, an action's own file (its page's
  context) the file name in its folder or, for ``archive_export``, the
  archive job's id;
- a plugin, a run, a dev folder by its id (``not_found``).

Never answered: a path (a file / folder value is its name only, a dev
folder its name), a secret's value, the list's reasons. Logged (the v1
op log, write_op): the method, the path and the body's keys only -- never
a value (ADR-009 E.3).

The serving of an artifact re-checks it after opening (PluginJobs N1, the
TOCTOU of a worker swapping the file): the file is checked
(``PluginJobs.ArtifactFile``), opened, checked again, and its handle's
``fstat`` must be the checked path's file (``st_dev``, ``st_ino``), a
regular file with one link, within its kind's size.

Routes whose part is not in the contract yet (the installs, the listing,
the update check, the enable toggle, the removal's sizes) answer 503
``unavailable``: their stage's wiring gives them a part (ADR-009 D10).

No Qt; nothing is made, read or written on import (pitfalls #1).
"""

import dataclasses
import functools
import math
import os
import re
import stat
from typing import Any, Callable, Optional

from flask import Response, jsonify, request, send_file

from .AutonaApiV1 import (BODY_NONE, CONFLICT, INVALID_ARGUMENT, NOT_FOUND,
                          UNAVAILABLE, ApiError, api_handler, write_op)
from .core.PluginJobs import RecordJson
from .core.PluginSettings import CheckFieldValue
from .core.PluginTypes import (
    ACTION_FILLS, CONTEXT_ONLY_TYPES, DEV_FOLDER_ID_PATTERN, HOOK_KEY_PATTERN,
    ICON_MAX_BYTES, IMAGE_ARTIFACT_MAX_BYTES, MANUAL_PICK_TYPES,
    PLUGIN_ID_PATTERN, RUN_ID_PATTERN, SOURCE_FIELD_TYPES,
    TEXT_ARTIFACT_MAX_BYTES, VALUE_FIELD_TYPES, ActionTarget, ArtifactKind,
    Badge, FieldType, InputPick, PluginState, ProblemScope, Refused,
    RunRecord, StartRefusal, Trigger, TriggerKind)

V1_PREFIX = "/api/v1"
# How much of a run's worker.log the log route answers (its end).
LOG_TAIL_BYTES = 256 * 1024
# The removal's ``runs`` (contract C.6).
REMOVE_RUNS = ("delete", "keep_out", "keep")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# How much of a body's key a refusal repeats.
_SHOWN_KEY_LENGTH = 40

_PLUGIN_ID = re.compile(PLUGIN_ID_PATTERN)
_RUN_ID = re.compile(RUN_ID_PATTERN)
_DEV_FOLDER_ID = re.compile(DEV_FOLDER_ID_PATTERN)
_HOOK_KEY = re.compile(HOOK_KEY_PATTERN)
_INDEX = re.compile(r"^(0|[1-9][0-9]{0,5})$")
# An archive job's id as an action's context: one name, no separator
# (PluginInputs checks the job folder itself).
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# contract_1.json refusals: the reason's status and that status's v1 code.
_STATUS = {
    StartRefusal.INVALID_REQUEST: (INVALID_ARGUMENT, 400),
    StartRefusal.NOT_FOUND: (NOT_FOUND, 404),
    StartRefusal.UNAVAILABLE: (UNAVAILABLE, 503),
}
_CONFLICT = (CONFLICT, 409)

_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "webp": "image/webp"}
_CONTENT_TYPES = {
    ArtifactKind.TEXT: "text/plain; charset=utf-8",
    ArtifactKind.FILE: "application/octet-stream",
    ArtifactKind.JSON: "application/json",
}


@dataclasses.dataclass
class PluginApiDeps:
    """The parts the handlers use, read per request (the wiring may fill
    them after RegisterPluginRoutes). A part left None answers 503
    ``unavailable``.

    - ``registry``: PluginRegistry (plus ``DevMode()`` for GET dev);
    - ``settings`` / ``secrets`` / ``inputs`` / ``jobs`` / ``hooks``: the
      PluginTypes Protocols' parts (``hooks`` None headless);
    - ``installer``: PluginInstaller (its ``Remove``);
    - ``resolvePick(fieldType, pickId) -> Optional[str]``: the host's pick
      ids (pickFile / pickFolder) -> the absolute path, as PluginSettings';
    - ``revealFile(path) -> bool``: open the folder of a file in Explorer
      with it selected (the artifact's reveal);
    - ``workDir() -> Optional[str]``: the archive work folder now (an
      ``archive_export`` action's job folder is ``<work>/<job id>``)."""
    registry: Any = None
    settings: Any = None
    secrets: Any = None
    inputs: Any = None
    jobs: Any = None
    hooks: Any = None
    installer: Any = None
    resolvePick: Optional[Callable[[str, str], Optional[str]]] = None
    revealFile: Optional[Callable[[str], bool]] = None
    workDir: Optional[Callable[[], Optional[str]]] = None


# ── answers and refusals ────────────────────────────────────────────────

def _Ok(data=None, status=200):
    """The envelope with ``data`` always there (null when none)."""
    resp = jsonify({"ok": True, "data": data})
    resp.status_code = status
    return resp


def _NoStore(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        resp = fn(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    return wrapper


def _Raise(reason: StartRefusal, detail: str = ""):
    code, status = _STATUS.get(reason, _CONFLICT)
    return ApiError(code, detail or reason.value, status,
                    reason=reason.value)


def _Refused(refused: Refused):
    """A part's Refused as the v1 error (busy: the seconds left)."""
    reason = StartRefusal(refused.reason)
    detail = refused.detail
    if reason is StartRefusal.BUSY and refused.remaining_seconds is not None:
        try:
            detail = str(max(0, math.ceil(refused.remaining_seconds)))
        except (OverflowError, ValueError):
            pass
    return _Raise(reason, detail)


def _Invalid(detail):
    return _Raise(StartRefusal.INVALID_REQUEST, detail)


def _NotFound(detail):
    return _Raise(StartRefusal.NOT_FOUND, detail)


def _Unavailable(detail):
    return _Raise(StartRefusal.UNAVAILABLE, detail)


def _Part(deps, name):
    part = getattr(deps, name)
    if part is None:
        raise _Unavailable(f"plugins: {name} is not ready")
    return part


def _Shown(key):
    return str(key)[:_SHOWN_KEY_LENGTH]


def _Body(allowed, required=()):
    """The JSON body as an object with only ``allowed`` keys (``required``
    among them); an empty body is {}."""
    data = request.get_json(force=True, silent=True)
    if data is None:
        if request.get_data():
            raise _Invalid("the body is not JSON")
        data = {}
    if not isinstance(data, dict):
        raise _Invalid("the body is not an object")
    unknown = sorted(str(key) for key in data if key not in allowed)
    if unknown:
        raise _Invalid(f"{_Shown(unknown[0])}: unknown key")
    for key in required:
        if key not in data:
            raise _Invalid(f"{key} is required")
    return data


# ── lookups ─────────────────────────────────────────────────────────────

def _Plugin(deps, pluginId):
    registry = _Part(deps, "registry")
    plugin = registry.Get(pluginId) \
        if isinstance(pluginId, str) and _PLUGIN_ID.fullmatch(pluginId) \
        else None
    if plugin is None:
        raise _NotFound("no such plugin")
    return plugin


def _Dev(plugin):
    return plugin.badge == Badge.DEV


def _Manifest(plugin):
    manifest = plugin.report.manifest
    if manifest is None:
        raise _Raise(StartRefusal.INCOMPATIBLE, "the manifest is unreadable")
    return manifest


def _RunId(runId):
    if not isinstance(runId, str) or not _RUN_ID.fullmatch(runId):
        raise _NotFound("no such run")
    return runId


def _Run(deps, pluginId, runId):
    _Plugin(deps, pluginId)
    record = _Part(deps, "jobs").Get(pluginId, _RunId(runId))
    if record is None:
        raise _NotFound("no such run")
    return record


def _Index(no):
    if not isinstance(no, str) or not _INDEX.fullmatch(no):
        raise _Invalid("no is an artifact's index")
    return int(no)


def _Name(path):
    """A path's last name to show (never the path): a drive root its
    drive."""
    if not isinstance(path, str) or not path:
        return None
    normal = os.path.normpath(path)
    return os.path.basename(normal) or os.path.splitdrive(normal)[0] or None


# ── the manifest's shapes (routes_v1.json types) ────────────────────────

def _TextOut(text):
    if text is None or isinstance(text, str):
        return text
    return dict(text)


def _FieldJson(field):
    out = {"key": field.key, "type": field.type,
           "label": _TextOut(field.label), "required": bool(field.required)}
    if field.default is not None:
        out["default"] = field.default
    if field.help is not None:
        out["help"] = _TextOut(field.help)
    for name in ("max_length", "min", "max", "step"):
        value = getattr(field, name)
        if value is not None:
            out[name] = value
    if field.options:
        out["options"] = [{"value": option.value,
                           "label": _TextOut(option.label)}
                          for option in field.options]
    if field.extensions:
        out["extensions"] = list(field.extensions)
    return out


def _JobJson(job):
    return {
        "id": job.id, "title": _TextOut(job.title), "lane": job.lane,
        "timeout_seconds": job.timeout_seconds, "manual": bool(job.manual),
        "params": [_FieldJson(field) for field in job.params],
        "actions": [{"target": action.target,
                     "label": _TextOut(action.label)}
                    for action in job.actions],
        "hooks": [{"on": hook.on, "label": _TextOut(hook.label)}
                  for hook in job.hooks],
    }


def _PermissionsJson(permissions):
    if permissions is None:
        return None
    out = {"network": list(permissions.network), "files": permissions.files,
           "spawn": bool(permissions.spawn),
           "autona_api": permissions.autona_api}
    if permissions.note is not None:
        out["note"] = _TextOut(permissions.note)
    return out


def _ValueShown(field, value):
    """SettingValue for a non-secret value: a file / folder by its name."""
    if field.type in (FieldType.FILE, FieldType.FOLDER):
        return {"kind": "pick", "name": _Name(value)}
    return {"kind": "value", "value": value}


def _EntryJson(entry):
    """contract_1.json plugin_list.fields: those keys and no others."""
    return {"id": entry.id, "name": _TextOut(entry.name),
            "author": entry.author, "version": entry.version,
            "state": PluginState(entry.state).value,
            "badge": Badge(entry.badge).value,
            "size_bytes": int(entry.size_bytes),
            "has_icon": bool(entry.has_icon)}


def _LabelIn(text, locale):
    if isinstance(text, str):
        return text
    if not text:
        return ""
    for name in (locale, "en", "ja"):
        if text.get(name):
            return text[name]
    return next(iter(text.values()))


def _Reason(deps, plugin):
    """The state's reason in words (PluginDetail.reason), null when the
    state has none. Host words; problems by their codes (no path)."""
    state = PluginState(plugin.state)
    manifest = plugin.report.manifest
    if state is PluginState.BLOCKED:
        return _TextOut(plugin.blocked_reason) or {
            "ja": "遮断されています。", "en": "Blocked."}
    if state is PluginState.INCOMPATIBLE:
        found = []
        for problem in plugin.report.problems:
            if problem.scope is ProblemScope.PLUGIN:
                where = f" ({problem.path})" if problem.path else ""
                what = f": {problem.detail}" if problem.detail else ""
                found.append(f"{problem.code.value}{where}{what}")
        describe = plugin.describe
        if describe is not None and describe.failure is not None:
            found.append(f"describe: {describe.failure.value}")
        if not found:
            found.append("no usable job")
        listed = "; ".join(found)
        return {"ja": f"この Autona では使えません（{listed}）",
                "en": f"Not usable in this Autona ({listed})"}
    if state is PluginState.DISABLED:
        return {"ja": "無効にしています。", "en": "Disabled."}
    if state is PluginState.NEEDS_SETTINGS and manifest is not None:
        missing = set(_Part(deps, "settings").Missing(
            plugin.id, manifest, dev=_Dev(plugin)))
        fields = [field for field in manifest.settings
                  if field.key in missing]
        ja = "、".join(_LabelIn(field.label, "ja") for field in fields)
        en = ", ".join(_LabelIn(field.label, "en") for field in fields)
        return {"ja": f"必須の設定が未入力です: {ja}",
                "en": f"Required settings are missing: {en}"}
    return None


# ── P2: the list, the icon, the detail ──────────────────────────────────

def get_plugins(deps):
    registry = _Part(deps, "registry")
    return _Ok([_EntryJson(entry) for entry in registry.Entries()])


def get_plugin_icon(deps, plugin_id):
    plugin = _Plugin(deps, plugin_id)
    manifest = plugin.report.manifest
    if not plugin.has_icon or manifest is None or not manifest.icon:
        raise _NotFound("no icon")
    folder = os.path.realpath(plugin.folder)
    path = os.path.realpath(os.path.join(folder, manifest.icon))
    try:
        inside = os.path.commonpath([os.path.normcase(folder),
                                     os.path.normcase(path)]) \
            == os.path.normcase(folder) and path != folder
    except ValueError:
        inside = False
    if not inside:
        raise _NotFound("no icon")
    try:
        with open(path, "rb") as file:
            data = file.read(ICON_MAX_BYTES + 1)
    except OSError:
        raise _NotFound("no icon") from None
    if len(data) > ICON_MAX_BYTES or not data.startswith(_PNG_SIGNATURE):
        raise _NotFound("no icon")
    resp = Response(data, mimetype="image/png")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def get_plugin(deps, plugin_id):
    plugin = _Plugin(deps, plugin_id)
    registry = deps.registry
    entry = next((row for row in registry.Entries()
                  if row.id == plugin.id), None)
    manifest = plugin.report.manifest
    data = _EntryJson(entry) if entry is not None else {
        "id": plugin.id,
        "name": _TextOut(manifest.name) if manifest else plugin.id,
        "author": manifest.author if manifest else "",
        "version": manifest.version if manifest else "",
        "state": PluginState(plugin.state).value,
        "badge": Badge(plugin.badge).value,
        "size_bytes": int(plugin.size_bytes),
        "has_icon": bool(plugin.has_icon)}
    # The detail's state is the plugin's now (Get), as its reason.
    data["state"] = PluginState(plugin.state).value
    values = {}
    if manifest is not None:
        dev = _Dev(plugin)
        saved = _Part(deps, "settings").Get(plugin.id, manifest, dev=dev)
        secretKeys = None
        for field in manifest.settings:
            if field.type == FieldType.SECRET:
                if secretKeys is None:
                    secretKeys = _Part(deps, "secrets").Keys(plugin.id,
                                                             dev=dev)
                values[field.key] = {"kind": "secret",
                                     "set": field.key in secretKeys}
            else:
                values[field.key] = _ValueShown(field, saved.get(field.key))
    if manifest is not None and manifest.description is not None:
        data["description"] = _TextOut(manifest.description)
    data.update({
        "reason": _Reason(deps, plugin),
        "homepage": bool(manifest and manifest.homepage),
        "funding": bool(manifest and manifest.funding),
        "permissions": _PermissionsJson(manifest.permissions
                                        if manifest else None),
        "settings": [_FieldJson(field)
                     for field in (manifest.settings if manifest else ())],
        "setting_values": values,
        "jobs": [_JobJson(job) for job in (manifest.jobs if manifest else ())],
    })
    return _Ok(data)


# ── P2: settings, reload, inputs ────────────────────────────────────────

def plugin_settings_update(deps, plugin_id):
    body = _Body(("values",))
    plugin = _Plugin(deps, plugin_id)
    values = body.get("values", {})
    if not isinstance(values, dict):
        raise _Invalid("values is an object")
    manifest = _Manifest(plugin)
    try:
        result = _Part(deps, "settings").Save(plugin.id, manifest, values,
                                              dev=_Dev(plugin))
    except OSError:
        raise _Unavailable("config.json not written") from None
    if isinstance(result, Refused):
        raise _Refused(result)
    return _Ok()


def plugin_reload(deps, plugin_id):
    plugin = _Plugin(deps, plugin_id)
    if not _Dev(plugin):
        raise _Invalid("not a dev plugin")
    deps.registry.Reload(plugin.id)
    return _Ok()


def get_plugin_input_choices(deps, kind):
    try:
        fieldType = FieldType(kind)
    except ValueError:
        fieldType = None
    if fieldType not in MANUAL_PICK_TYPES:
        raise _Invalid("not a source type a manual run picks")
    choices = _Part(deps, "inputs").Choices(fieldType)
    return _Ok([{"id": choice.id, "label": choice.label}
                for choice in choices])


# ── P2: runs ────────────────────────────────────────────────────────────

def _TriggerOf(raw):
    if not isinstance(raw, dict) or set(raw) - {"kind", "target"}:
        raise _Invalid("trigger is {kind, target}")
    kind, target = raw.get("kind"), raw.get("target")
    if kind == TriggerKind.MANUAL.value:
        if target is not None:
            raise _Invalid("a manual trigger has no target")
        return Trigger(TriggerKind.MANUAL)
    if kind == TriggerKind.ACTION.value:
        try:
            return Trigger(TriggerKind.ACTION, target=ActionTarget(target))
        except ValueError:
            raise _Invalid("not an action target") from None
    raise _Invalid("trigger kind is manual or action")


def _StateRefusal(plugin):
    """The plugin's state's refusal before a body is checked (PluginJobs'
    order: blocked, incompatible, disabled), else None."""
    state = PluginState(plugin.state)
    if state is PluginState.BLOCKED or plugin.blocked_reason is not None:
        return StartRefusal.BLOCKED
    if state is PluginState.INCOMPATIBLE or plugin.report.manifest is None:
        return StartRefusal.INCOMPATIBLE
    if state is PluginState.DISABLED or not plugin.enabled:
        return StartRefusal.DISABLED
    return None


def _Params(deps, job, raw):
    """The body's params checked (CheckFieldValue; pick ids resolved): a
    key left out or null is left out (its default)."""
    if not isinstance(raw, dict):
        raise _Invalid("params is an object")
    fields = {field.key: field for field in job.params
              if field.type in VALUE_FIELD_TYPES
              and field.type != FieldType.SECRET}
    checked = {}
    for key, value in raw.items():
        field = fields.get(key) if isinstance(key, str) else None
        if field is None:
            raise _Invalid(f"{_Shown(key)}: no such param")
        if value is None:
            continue
        good, kept = CheckFieldValue(field, value,
                                     resolvePick=deps.resolvePick)
        if not good:
            raise _Invalid(f"{key}: {kept}")
        checked[key] = kept
    return checked


def _Picks(deps, job, trigger, raw):
    """The body's inputs as InputPicks: a key left out or null is not
    picked (no pick, never a null one)."""
    if not isinstance(raw, dict):
        raise _Invalid("inputs is an object")
    sources = {field.key: FieldType(field.type) for field in job.params
               if field.type in SOURCE_FIELD_TYPES}
    fills = ACTION_FILLS[trigger.target] \
        if trigger.kind is TriggerKind.ACTION else None
    picks = {}
    for key, choice in raw.items():
        kind = sources.get(key) if isinstance(key, str) else None
        if kind is None:
            raise _Invalid(f"{_Shown(key)}: no such input")
        if choice is None:
            continue
        if not isinstance(choice, str) or not choice:
            raise _Invalid(f"{key}: a choice id is a string")
        if kind is FieldType.STATE:
            raise _Invalid(f"{key}: a state is never picked")
        if kind is fills and kind is FieldType.ARCHIVE_EXPORT:
            # The action's context: the archive job's id, its folder the
            # work folder's (PluginInputs checks it).
            if not _NAME.fullmatch(choice):
                raise _Invalid(f"{key}: not an archive job")
            work = deps.workDir() if deps.workDir is not None else None
            if not work:
                raise _Unavailable("the work folder is not known")
            picks[key] = InputPick(key, kind,
                                   path=os.path.join(work, choice))
        elif kind is fills or kind in MANUAL_PICK_TYPES:
            picks[key] = InputPick(key, kind, choice=choice)
        elif kind in CONTEXT_ONLY_TYPES:
            raise _Invalid(f"{key}: filled by its trigger only")
        else:
            raise _Invalid(f"{key}: not picked")
    return picks


def plugin_run_create(deps, plugin_id):
    body = _Body(("job", "trigger", "params", "inputs"), ("job", "trigger"))
    plugin = _Plugin(deps, plugin_id)
    jobs = _Part(deps, "jobs")
    jobId = body["job"]
    if not isinstance(jobId, str):
        raise _Invalid("job is a job id")
    trigger = _TriggerOf(body["trigger"])
    refusal = _StateRefusal(plugin)
    if refusal is not None:
        raise _Raise(refusal)
    manifest = plugin.report.manifest
    job = next((item for item in manifest.jobs if item.id == jobId), None)
    if job is None:
        raise _Invalid("unknown job")
    params = _Params(deps, job, body.get("params", {}))
    picks = _Picks(deps, job, trigger, body.get("inputs", {}))
    settings = _Part(deps, "settings").Get(plugin.id, manifest,
                                           dev=_Dev(plugin))
    result = jobs.Start(plugin.id, job.id, trigger, params, settings, picks)
    if isinstance(result, Refused):
        raise _Refused(result)
    return _Ok({"run_id": result.run_id}, status=201)


def get_plugin_runs(deps, plugin_id):
    plugin = _Plugin(deps, plugin_id)
    records = _Part(deps, "jobs").List(plugin.id)
    return _Ok([RecordJson(record) for record in records])


def get_plugin_run(deps, plugin_id, run_id):
    return _Ok(RecordJson(_Run(deps, plugin_id, run_id)))


def plugin_run_cancel(deps, plugin_id, run_id):
    record = _Run(deps, plugin_id, run_id)
    # A run that has ended is left as it is: the page reads its record.
    deps.jobs.Cancel(plugin_id, record.run_id)
    return _Ok()


def get_plugin_run_log(deps, plugin_id, run_id):
    record = _Run(deps, plugin_id, run_id)
    text = deps.jobs.LogTail(plugin_id, record.run_id, LOG_TAIL_BYTES)
    if text is None:
        raise _NotFound("no log")
    resp = Response(text, content_type="text/plain; charset=utf-8")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _ContentType(path, kind):
    if kind is ArtifactKind.IMAGE:
        return _IMAGE_TYPES.get(os.path.splitext(path)[1][1:].lower())
    return _CONTENT_TYPES.get(kind)


def _Identity(info):
    return info.st_dev, info.st_ino


def _OpenArtifact(jobs, pluginId, runId, index):
    """(the open file, kind) of an accepted artifact, re-checked after
    opening: the handle's fstat must be the checked path's file (st_dev,
    st_ino), a regular file with one link, within its kind's size (L8
    review N1: a worker swapping the file between the check and the
    open)."""
    found = jobs.ArtifactFile(pluginId, runId, index)
    if found is None:
        raise _NotFound("no such artifact")
    path, kind = found[0], ArtifactKind(found[1])
    try:
        handle = open(path, "rb")
    except OSError:
        raise _NotFound("the artifact cannot be opened") from None
    try:
        opened = os.fstat(handle.fileno())
        again = jobs.ArtifactFile(pluginId, runId, index)
        if again is None or ArtifactKind(again[1]) is not kind or \
                os.path.normcase(again[0]) != os.path.normcase(path):
            raise _NotFound("no such artifact")
        checked = os.stat(again[0])
        limit = {ArtifactKind.TEXT: TEXT_ARTIFACT_MAX_BYTES,
                 ArtifactKind.IMAGE: IMAGE_ARTIFACT_MAX_BYTES}.get(kind)
        if (_Identity(opened) != _Identity(checked)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1
                or (limit is not None and opened.st_size > limit)):
            raise _NotFound("the artifact changed")
    except OSError:
        handle.close()
        raise _NotFound("the artifact cannot be opened") from None
    except BaseException:
        handle.close()
        raise
    return handle, path, kind


def get_plugin_run_artifact(deps, plugin_id, run_id, no):
    index = _Index(no)
    record = _Run(deps, plugin_id, run_id)
    handle, path, kind = _OpenArtifact(deps.jobs, plugin_id, record.run_id,
                                       index)
    contentType = _ContentType(path, kind)
    if contentType is None:
        handle.close()
        raise _NotFound("no such artifact")
    resp = send_file(handle, mimetype=contentType, conditional=False)
    # As the kind says, exactly (send_file would add a second charset).
    resp.headers["Content-Type"] = contentType
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def plugin_run_artifact_reveal(deps, plugin_id, run_id, no):
    index = _Index(no)
    record = _Run(deps, plugin_id, run_id)
    found = deps.jobs.ArtifactFile(plugin_id, record.run_id, index)
    if found is None:
        raise _NotFound("no such artifact")
    if deps.revealFile is None or not deps.revealFile(found[0]):
        raise _Unavailable("the folder could not be shown")
    return _Ok()


# ── P2: the developer's ─────────────────────────────────────────────────

def get_plugin_dev(deps):
    registry = _Part(deps, "registry")
    folders = [{"id": folder.id, "name": _Name(folder.path) or folder.id}
               for folder in registry.DevFolders()]
    return _Ok({"enabled": bool(registry.DevMode()), "folders": folders})


def plugin_dev_update(deps):
    body = _Body(("enabled",), ("enabled",))
    if not isinstance(body["enabled"], bool):
        raise _Invalid("enabled is a boolean")
    refused = _Part(deps, "registry").SetDevMode(body["enabled"])
    if isinstance(refused, Refused):
        raise _Refused(refused)
    return _Ok()


def plugin_dev_folder_add(deps):
    body = _Body(("pick",), ("pick",))
    pick = body["pick"]
    registry = _Part(deps, "registry")
    if not isinstance(pick, str) or not pick:
        raise _Invalid("pick is a pickFolder id")
    path = deps.resolvePick(FieldType.FOLDER.value, pick) \
        if deps.resolvePick is not None else None
    if path is None:
        raise _Invalid("unknown pick id")
    result = registry.AddDevFolder(path)
    if isinstance(result, Refused):
        raise _Refused(result)
    return _Ok()


def plugin_dev_folder_remove(deps, folder_id):
    registry = _Part(deps, "registry")
    if not isinstance(folder_id, str) or not _DEV_FOLDER_ID.fullmatch(
            folder_id):
        raise _NotFound("no such dev folder")
    result = registry.RemoveDevFolder(folder_id)
    if isinstance(result, Refused):
        raise _Refused(result)
    if not result:
        raise _NotFound("no such dev folder")
    return _Ok()


# ── P3: actions and secrets ─────────────────────────────────────────────

def get_plugin_actions(deps):
    registry = _Part(deps, "registry")
    try:
        target = ActionTarget(request.args.get("target", ""))
    except ValueError:
        raise _Invalid("target is " + " | ".join(
            item.value for item in ActionTarget)) from None
    out = []
    for pluginId, job in registry.Actions(target):
        plugin = registry.Get(pluginId)
        manifest = plugin.report.manifest if plugin is not None else None
        out.append({"plugin": pluginId,
                    "name": _TextOut(manifest.name) if manifest
                    else pluginId,
                    "job": _JobJson(job)})
    return _Ok(out)


def _SecretField(plugin, key):
    manifest = _Manifest(plugin)
    field = next((item for item in manifest.settings if item.key == key),
                 None)
    if field is None or field.type != FieldType.SECRET:
        raise _Invalid(f"{_Shown(key)}: no such secret setting")
    return field


def plugin_secret_update(deps, plugin_id, key):
    body = _Body(("value",), ("value",))
    plugin = _Plugin(deps, plugin_id)
    _SecretField(plugin, key)
    secrets = _Part(deps, "secrets")
    try:
        secrets.Set(plugin.id, key, body["value"], dev=_Dev(plugin))
    except ValueError as error:
        # SecretValueError: its words never hold the value.
        raise _Invalid(str(error)) from None
    except OSError:
        raise _Unavailable("secrets.bin not written") from None
    return _Ok()


def plugin_secret_delete(deps, plugin_id, key):
    plugin = _Plugin(deps, plugin_id)
    _SecretField(plugin, key)
    try:
        _Part(deps, "secrets").Delete(plugin.id, key, dev=_Dev(plugin))
    except ValueError as error:
        raise _Invalid(str(error)) from None
    except OSError:
        raise _Unavailable("secrets.bin not written") from None
    return _Ok()


# ── P5a: removal ────────────────────────────────────────────────────────

# PluginInstaller.Remove's refusal codes (InstallError.code) -> ours.
_REMOVE_REFUSALS = {
    "running": StartRefusal.BUSY,
    "in_progress": StartRefusal.BUSY,
    "not_found": StartRefusal.NOT_FOUND,
    "builtin": StartRefusal.INVALID_REQUEST,
    "invalid_request": StartRefusal.INVALID_REQUEST,
}


def plugin_remove(deps, plugin_id):
    body = _Body(("data", "runs"), ("data", "runs"))
    if not isinstance(body["data"], bool):
        raise _Invalid("data is a boolean")
    if body["runs"] not in REMOVE_RUNS:
        raise _Invalid("runs is " + " | ".join(REMOVE_RUNS))
    plugin = _Plugin(deps, plugin_id)
    if plugin.badge in (Badge.BUILTIN, Badge.DEV):
        raise _Invalid("not an installed plugin")
    installer = _Part(deps, "installer")
    if _Part(deps, "jobs").Running(plugin.id):
        raise _Raise(StartRefusal.BUSY, "running")
    try:
        installer.Remove(plugin.id, data=body["data"], runs=body["runs"])
    except Exception as error:
        code = getattr(error, "code", None)
        if not isinstance(code, str):
            raise
        reason = _REMOVE_REFUSALS.get(code, StartRefusal.UNAVAILABLE)
        raise _Raise(reason, code) from None
    deps.registry.Scan()
    return _Ok()


# ── P7: hooks ───────────────────────────────────────────────────────────

def _HookJson(status, job):
    fields = {field.key: field for field in (job.params if job else ())}
    params = {}
    for key, value in dict(status.params).items():
        field = fields.get(key)
        params[key] = _ValueShown(field, value) if field is not None \
            else {"kind": "value", "value": value}
    error = status.last_error
    jobId, _slash, on = status.key.partition("/")
    return {
        "key": status.key, "job": jobId, "on": on,
        "enabled": bool(status.enabled), "params": params,
        "pending": bool(status.pending), "failures": int(status.failures),
        "disabled_reason": status.disabled_reason.value
        if status.disabled_reason is not None else None,
        "last_error": None if error is None else {
            "code": error.code, "message": _TextOut(error.message),
            "hint": _TextOut(error.hint)},
        "history": [{"at": item.at, "outcome": item.outcome.value,
                     "summary": _TextOut(item.summary),
                     "reason": item.reason, "run_id": item.run_id}
                    for item in status.history],
    }


def _JobOf(plugin, jobId):
    manifest = plugin.report.manifest
    if manifest is None:
        return None
    return next((job for job in manifest.jobs if job.id == jobId), None)


def get_plugin_hooks(deps, plugin_id):
    plugin = _Plugin(deps, plugin_id)
    hooks = _Part(deps, "hooks")
    return _Ok([_HookJson(status, _JobOf(plugin, status.key.partition("/")[0]))
                for status in hooks.Status(plugin.id)])


def _HookKey(job_id, on):
    key = f"{job_id}/{on}"
    if not _HOOK_KEY.fullmatch(key):
        raise _NotFound("no such hook")
    return key


def plugin_hook_update(deps, plugin_id, job_id, on):
    body = _Body(("enabled", "params"), ("enabled",))
    if not isinstance(body["enabled"], bool):
        raise _Invalid("enabled is a boolean")
    params = body.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise _Invalid("params is an object")
    plugin = _Plugin(deps, plugin_id)
    key = _HookKey(job_id, on)
    result = _Part(deps, "hooks").SetEnabled(plugin.id, key, body["enabled"],
                                             params)
    if isinstance(result, Refused):
        raise _Refused(result)
    return _Ok(_HookJson(result, _JobOf(plugin, job_id)))


def plugin_hook_sample(deps, plugin_id, job_id, on):
    plugin = _Plugin(deps, plugin_id)
    key = _HookKey(job_id, on)
    result = _Part(deps, "hooks").TrySample(plugin.id, key)
    if isinstance(result, Refused):
        raise _Refused(result)
    if not isinstance(result, RunRecord):
        raise _Unavailable("the sample did not start")
    return _Ok({"run_id": result.run_id}, status=201)


# ── stages whose part is not in the contract yet ────────────────────────

def _NotYet(deps, **_kwargs):
    raise _Unavailable("not available in this version")


# ── the table and the registration ──────────────────────────────────────

_READ = "read"
_WRITE = "write"
_UPLOAD = "upload"

# (method, path as routes_v1.json writes it, endpoint name, handler, how):
# one row per route of the ledger (tests/test_plugin_api.py holds them to
# it). The endpoint is ``<blueprint>.<name>`` (``api_v1.<name>``).
ROUTES = (
    ("GET", "/api/v1/plugins", "get_plugins", get_plugins, _READ),
    ("GET", "/api/v1/plugins/<plugin_id>/icon", "get_plugin_icon",
     get_plugin_icon, _READ),
    ("GET", "/api/v1/plugins/<plugin_id>", "get_plugin", get_plugin, _READ),
    ("PATCH", "/api/v1/plugins/<plugin_id>/settings",
     "plugin_settings_update", plugin_settings_update, _WRITE),
    ("POST", "/api/v1/plugins/<plugin_id>:reload", "plugin_reload",
     plugin_reload, _WRITE),
    ("GET", "/api/v1/plugins/inputs/<kind>", "get_plugin_input_choices",
     get_plugin_input_choices, _READ),
    ("POST", "/api/v1/plugins/<plugin_id>/runs", "plugin_run_create",
     plugin_run_create, _WRITE),
    ("GET", "/api/v1/plugins/<plugin_id>/runs", "get_plugin_runs",
     get_plugin_runs, _READ),
    ("GET", "/api/v1/plugins/<plugin_id>/runs/<run_id>", "get_plugin_run",
     get_plugin_run, _READ),
    ("POST", "/api/v1/plugins/<plugin_id>/runs/<run_id>:cancel",
     "plugin_run_cancel", plugin_run_cancel, _WRITE),
    ("GET", "/api/v1/plugins/<plugin_id>/runs/<run_id>/log",
     "get_plugin_run_log", get_plugin_run_log, _READ),
    ("GET", "/api/v1/plugins/<plugin_id>/runs/<run_id>/artifacts/<no>",
     "get_plugin_run_artifact", get_plugin_run_artifact, _READ),
    ("POST",
     "/api/v1/plugins/<plugin_id>/runs/<run_id>/artifacts/<no>:reveal",
     "plugin_run_artifact_reveal", plugin_run_artifact_reveal, _WRITE),
    ("GET", "/api/v1/plugins/dev", "get_plugin_dev", get_plugin_dev, _READ),
    ("PATCH", "/api/v1/plugins/dev", "plugin_dev_update", plugin_dev_update,
     _WRITE),
    ("POST", "/api/v1/plugins/dev/folders", "plugin_dev_folder_add",
     plugin_dev_folder_add, _WRITE),
    ("DELETE", "/api/v1/plugins/dev/folders/<folder_id>",
     "plugin_dev_folder_remove", plugin_dev_folder_remove, _WRITE),
    ("GET", "/api/v1/plugins/actions", "get_plugin_actions",
     get_plugin_actions, _READ),
    ("PUT", "/api/v1/plugins/<plugin_id>/secrets/<key>",
     "plugin_secret_update", plugin_secret_update, _WRITE),
    ("DELETE", "/api/v1/plugins/<plugin_id>/secrets/<key>",
     "plugin_secret_delete", plugin_secret_delete, _WRITE),
    ("POST", "/api/v1/plugins/uploads", "plugin_upload_zip", _NotYet,
     _UPLOAD),
    ("DELETE", "/api/v1/plugins/uploads/<upload_id>", "plugin_upload_drop",
     _NotYet, _WRITE),
    ("POST", "/api/v1/plugins/installs", "plugin_install_create", _NotYet,
     _WRITE),
    ("GET", "/api/v1/plugins/installs/<install_id>", "get_plugin_install",
     _NotYet, _READ),
    ("POST", "/api/v1/plugins/installs/<install_id>:consent",
     "plugin_install_consent", _NotYet, _WRITE),
    ("POST", "/api/v1/plugins/installs/<install_id>:cancel",
     "plugin_install_cancel", _NotYet, _WRITE),
    ("POST", "/api/v1/plugins/<plugin_id>:enable", "plugin_enable", _NotYet,
     _WRITE),
    ("POST", "/api/v1/plugins/<plugin_id>:disable", "plugin_disable",
     _NotYet, _WRITE),
    ("GET", "/api/v1/plugins/<plugin_id>/removal", "get_plugin_removal",
     _NotYet, _READ),
    ("POST", "/api/v1/plugins/<plugin_id>:remove", "plugin_remove",
     plugin_remove, _WRITE),
    ("GET", "/api/v1/plugins/listing", "get_plugin_listing", _NotYet, _READ),
    ("POST", "/api/v1/plugins:checkUpdates", "plugin_updates_check", _NotYet,
     _WRITE),
    ("GET", "/api/v1/plugins/<plugin_id>/hooks", "get_plugin_hooks",
     get_plugin_hooks, _READ),
    ("PUT", "/api/v1/plugins/<plugin_id>/hooks/<job_id>/<on>",
     "plugin_hook_update", plugin_hook_update, _WRITE),
    ("POST", "/api/v1/plugins/<plugin_id>/hooks/<job_id>/<on>:sample",
     "plugin_hook_sample", plugin_hook_sample, _WRITE),
)


def _View(deps, name, handler, how):
    bound = functools.partial(handler, deps)
    operation = "plugins." + name
    if how == _READ:
        view = api_handler(bound)
    elif how == _UPLOAD:
        # The zip's bytes are never read or logged here.
        view = write_op(operation, body=BODY_NONE)(bound)
    else:
        # Logged by key only: a body may hold a secret, a path's pick id
        # or a user's text (E.3).
        view = write_op(operation, log_keys_only=True)(bound)
    return _NoStore(view)


def RegisterPluginRoutes(blueprint, deps: PluginApiDeps) -> None:
    """Add every route of ROUTES to ``blueprint`` (the v1 blueprint, whose
    url_prefix is /api/v1), its handlers bound to ``deps``. Called once,
    before the blueprint is registered on the app."""
    for method, path, name, handler, how in ROUTES:
        if not path.startswith(V1_PREFIX):
            raise ValueError(f"not a v1 path: {path}")
        blueprint.add_url_rule(path[len(V1_PREFIX):], endpoint=name,
                               view_func=_View(deps, name, handler, how),
                               methods=[method])
