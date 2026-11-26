"""The plugin v1 routes (ADR-009 D4, lane L11): src/AutonaPluginApi.py.

Every route of docs/plugins/routes_v1.json has a handler (the ledger and the
blueprint's rules held to each other), and each one answers its success and
its refusals (invalid_request 400, not_found 404, the 409s with busy's
seconds, unavailable 503) on a bare Flask app with the parts faked -- the
settings and the secrets are the real PluginSettings / PluginSecrets in
tmp_path (identity cipher), so CheckFieldValue and the secret rule are the
real ones. The access classes are the wiring's (WebAccess), not tested here.
"""

import json
import os
from pathlib import Path

import pytest
from flask import Blueprint, Flask
from loguru import logger

from src import AutonaPluginApi as api
from src.core.PluginSecrets import PluginSecrets
from src.core.PluginSettings import PluginSettings
from src.core.PluginTypes import (
    Action, Artifact, ArtifactKind, Badge, DescribeResult, DevFolder,
    ErrorEvent, Field, Hook, HookDisabledReason, HookHistoryEntry,
    HookOutcome, HookStatus, InputChoice, Job, JobState, Lane, ListEntry,
    Manifest, ManifestCheck, ManifestProblem, ManifestReport, Option,
    Permissions, PluginState, ProblemScope, Refused, RegisteredPlugin,
    RunRecord, StartRefusal, Trigger, TriggerKind)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = json.loads((ROOT / "docs" / "plugins" / "routes_v1.json")
                    .read_text(encoding="utf-8"))

PLUGIN = "example.tool"
DEV_PLUGIN = "me.sandbox"
RUN_ID = "20261126-120000-0abc"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SECRET_VALUE = "s3cr3t-value-never-shown"

TITLE = Field(key="title", type="text", label="Title", max_length=10)
COUNT = Field(key="count", type="number", label="Count", min=1, max=5,
              step=1, default=2)
DOC = Field(key="doc", type="file", label="Doc", extensions=("txt",))
LOG = Field(key="log", type="match_log", label="Log", required=True)
STATE = Field(key="st", type="state", label="State")
IMAGE = Field(key="img", type="result_image", label="Image")
EXPORT = Field(key="clips", type="archive_export", label="Clips")

RUN_JOB = Job(id="run", title={"ja": "実行", "en": "Run"}, lane="light",
              timeout_seconds=60, params=(TITLE, COUNT, DOC, LOG, STATE, IMAGE),
              actions=(Action(target="result_image", label="Send"),))
EXPORT_JOB = Job(id="upload", title="Upload", lane="light",
                 timeout_seconds=60, manual=False, params=(EXPORT,),
                 actions=(Action(target="archive_export", label="Upload"),))
HOOK_JOB = Job(id="post", title="Post", lane="light", timeout_seconds=30,
               manual=False, params=(IMAGE, DOC),
               hooks=(Hook(on="result_image_saved", label="On save"),))

NAME = Field(key="name", type="text", label={"ja": "名前", "en": "Name"})
MODE = Field(key="mode", type="select", label="Mode", default="a",
             options=(Option("a", "A"), Option("b", "B")))
OUTDIR = Field(key="outdir", type="folder", label="Folder")
TOKEN = Field(key="token", type="secret", label={"ja": "鍵", "en": "Key"},
              required=True)


def _manifest(pluginId, *, icon=None, jobs=(RUN_JOB, EXPORT_JOB, HOOK_JOB)):
    return Manifest(
        manifest=1, id=pluginId, version="1.0.0",
        name={"ja": "道具", "en": "Tool"}, author="someone", license="MIT",
        min_autona="2.6.0", protocol=(1,), platforms=("windows-x64",),
        entry=("bin/tool.exe",),
        permissions=Permissions(network=("example.com",), files="inputs",
                                spawn=False, autona_api="none", note="n"),
        jobs=jobs, description="Does things", homepage="https://example.com",
        icon=icon, settings=(NAME, MODE, OUTDIR, TOKEN))


def _record(state=JobState.DONE, artifacts=()):
    return RunRecord(run_id=RUN_ID, plugin=PLUGIN, job="run",
                     trigger=Trigger(TriggerKind.MANUAL), lane=Lane.LIGHT,
                     state=state, started_at="2026-11-26T12:00:00+09:00",
                     artifacts=tuple(artifacts), timeout_seconds=60)


class FakeRegistry:
    def __init__(self, plugins):
        self.plugins = {plugin.id: plugin for plugin in plugins}
        self.devMode = False
        self.devFolders = []
        self.added = []
        self.addAnswer = None
        self.removeAnswer = True
        self.reloaded = []
        self.scans = 0

    def Entries(self):
        return [ListEntry(id=p.id, name=p.report.manifest.name,
                          author=p.report.manifest.author,
                          version=p.report.manifest.version, state=p.state,
                          badge=p.badge, size_bytes=p.size_bytes,
                          has_icon=p.has_icon)
                for p in self.plugins.values()]

    def Get(self, pluginId):
        return self.plugins.get(pluginId)

    def Actions(self, target):
        return [(p.id, job) for p in self.plugins.values()
                for job in p.report.manifest.jobs
                if any(a.target == target for a in job.actions)]

    def DevFolders(self):
        return tuple(self.devFolders)

    def DevMode(self):
        return self.devMode

    def SetDevMode(self, on):
        self.devMode = on
        return None

    def AddDevFolder(self, path):
        self.added.append(path)
        return self.addAnswer or DevFolder("0123456789ab", path)

    def RemoveDevFolder(self, folderId):
        return self.removeAnswer

    def Reload(self, pluginId):
        self.reloaded.append(pluginId)
        return DescribeResult(None, protocol=1)

    def Scan(self):
        self.scans += 1


class FakeInputs:
    def Choices(self, kind):
        if kind == "match_log":
            return [InputChoice("20261126", "2026-11-26", detail="3 records"),
                    InputChoice("20261126/Cup", "2026-11-26 — Cup")]
        return []


class FakeJobs:
    def __init__(self):
        self.started = []
        self.answer = _record(JobState.RUNNING)
        self.records = {RUN_ID: _record()}
        self.cancelled = []
        self.artifacts = {}
        self.swap = None
        self.running = False
        self.log = "line 1\nline 2\n"

    def Start(self, pluginId, jobId, trigger, params, settings, picks):
        self.started.append((pluginId, jobId, trigger, params, settings,
                             picks))
        return self.answer

    def Get(self, pluginId, runId):
        return self.records.get(runId) if pluginId == PLUGIN else None

    def List(self, pluginId):
        return list(self.records.values())

    def Cancel(self, pluginId, runId):
        self.cancelled.append(runId)
        return True

    def LogTail(self, pluginId, runId, maxBytes):
        return self.log

    def ArtifactFile(self, pluginId, runId, index):
        found = self.artifacts.get(index)
        if found is not None and self.swap is not None:
            swap, self.swap = self.swap, None
            swap()
        return found

    def Running(self, pluginId):
        return self.running


class FakeHooks:
    def __init__(self):
        self.set = []
        self.answer = None
        self.sample = None
        self.refreshes = 0

    def Refresh(self):
        self.refreshes += 1

    def _Status(self, enabled=True, params=None,
                key="post/result_image_saved"):
        return HookStatus(
            plugin=PLUGIN, key=key, enabled=enabled,
            params=params or {"doc": os.path.join(
                os.path.abspath(os.sep), "Users", "someone", "secret",
                "a.txt")},
            pending=False, failures=1,
            disabled_reason=None if enabled else HookDisabledReason.USER,
            last_error=ErrorEvent(code="http", message="boom"),
            history=(HookHistoryEntry(at="2026-11-26T12:00:00+09:00",
                                      outcome=HookOutcome.ERROR,
                                      summary="failed", run_id=RUN_ID),))

    def Status(self, pluginId):
        return [self._Status()]

    def SetEnabled(self, pluginId, key, enabled, params):
        self.set.append((pluginId, key, enabled, params))
        return self.answer or self._Status(enabled)

    def TrySample(self, pluginId, key):
        return self.sample or _record(JobState.RUNNING)


class RemoveError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FakeInstaller:
    def __init__(self):
        self.removed = []
        self.error = None

    def Remove(self, pluginId, *, data, runs):
        if self.error is not None:
            raise RemoveError(self.error)
        self.removed.append((pluginId, data, runs))


@pytest.fixture
def env(tmp_path):
    folder = tmp_path / "plugins" / PLUGIN
    folder.mkdir(parents=True)
    (folder / "icon.png").write_bytes(PNG)
    devFolder = tmp_path / "dev" / "sandbox"
    devFolder.mkdir(parents=True)
    picked = tmp_path / "picked"
    picked.mkdir()
    (picked / "notes.txt").write_text("x", encoding="utf-8")
    picks = {"pick-folder": str(picked), "pick-file": str(picked / "notes.txt")}

    def plugin(pluginId, folderPath, badge, state=PluginState.READY,
               **kwargs):
        return RegisteredPlugin(
            id=pluginId, folder=str(folderPath), badge=badge,
            report=ManifestReport(_manifest(pluginId, icon="icon.png")),
            state=state, size_bytes=1024, has_icon=True, **kwargs)

    registry = FakeRegistry([
        plugin(PLUGIN, folder, Badge.MANUAL),
        plugin(DEV_PLUGIN, devFolder, Badge.DEV,
               state=PluginState.NEEDS_SETTINGS)])
    secrets = PluginSecrets(str(tmp_path / "user_data"),
                            protect=lambda b: b, unprotect=lambda b: b)
    resolved = []

    def resolvePick(kind, pickId):
        resolved.append((kind, pickId))
        return picks.get(pickId)

    settings = PluginSettings(str(tmp_path / "user_data"), secrets,
                              resolvePick=resolvePick)
    revealed = []
    deps = api.PluginApiDeps(
        registry=registry, settings=settings, secrets=secrets,
        inputs=FakeInputs(), jobs=FakeJobs(), hooks=FakeHooks(),
        installer=FakeInstaller(), resolvePick=resolvePick,
        revealFile=lambda path: revealed.append(path) or True,
        workDir=lambda: str(tmp_path / "work"))
    blueprint = Blueprint("api_v1", __name__, url_prefix="/api/v1")
    api.RegisterPluginRoutes(blueprint, deps)
    app = Flask(__name__)
    app.register_blueprint(blueprint)

    class Env:
        pass

    out = Env()
    out.tmp = tmp_path
    out.app = app
    out.client = app.test_client()
    out.deps = deps
    out.registry = registry
    out.jobs = deps.jobs
    out.hooks = deps.hooks
    out.installer = deps.installer
    out.secrets = secrets
    out.settings = settings
    out.picked = picked
    out.resolved = resolved
    out.revealed = revealed
    out.folder = folder
    return out


BASE = "/api/v1/plugins"


def _refused(resp, status, reason):
    assert resp.status_code == status, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["reason"] == reason
    assert body["error"]["code"] == {400: "INVALID_ARGUMENT",
                                     404: "NOT_FOUND", 409: "CONFLICT",
                                     503: "UNAVAILABLE"}[status]
    return body["error"]


def _data(resp, status=200):
    assert resp.status_code == status, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True and "data" in body
    return body["data"]


# ── the ledger ──────────────────────────────────────────────────────────

def test_every_route_of_the_ledger_has_a_handler_and_a_rule(env):
    ledger = {(route["method"], route["path"], route["endpoint"])
              for route in LEDGER["routes"]}
    table = {(method, path, "api_v1." + name)
             for method, path, name, _handler, _how in api.ROUTES}
    assert table == ledger
    assert len(api.ROUTES) == len(LEDGER["routes"]) == 35
    rules = {(rule.endpoint, method, rule.rule)
             for rule in env.app.url_map.iter_rules()
             for method in rule.methods - {"HEAD", "OPTIONS"}}
    for method, path, endpoint in ledger:
        assert (endpoint, method, path) in rules


def test_a_request_reaches_the_route_the_ledger_names(env):
    adapter = env.app.url_map.bind("localhost")
    for route in LEDGER["routes"]:
        path = (route["path"].replace("<plugin_id>", PLUGIN)
                .replace("<run_id>", RUN_ID).replace("<no>", "0")
                .replace("<kind>", "match_log")
                .replace("<folder_id>", "0123456789ab")
                .replace("<key>", "token").replace("<upload_id>", "u1")
                .replace("<install_id>", "i1").replace("<job_id>", "post")
                .replace("<on>", "result_image_saved"))
        endpoint, _args = adapter.match(path, method=route["method"])
        assert endpoint == route["endpoint"], route["path"]


def test_every_answer_is_no_store(env):
    for resp in (env.client.get(BASE), env.client.get(f"{BASE}/nope.x"),
                 env.client.post(f"{BASE}/installs", json={}),
                 env.client.get(f"{BASE}/{PLUGIN}/icon")):
        assert resp.headers["Cache-Control"] == "no-store"


def test_a_part_not_given_yet_is_unavailable(env):
    env.deps.registry = None
    _refused(env.client.get(BASE), 503, "unavailable")


@pytest.mark.parametrize("method,path", [
    ("POST", "/uploads"), ("DELETE", "/uploads/u1"), ("POST", "/installs"),
    ("GET", "/installs/i1"), ("POST", "/installs/i1:consent"),
    ("POST", "/installs/i1:cancel"), ("POST", f"/{PLUGIN}:enable"),
    ("POST", f"/{PLUGIN}:disable"), ("GET", f"/{PLUGIN}/removal"),
    ("GET", "/listing"), ("POST", ":checkUpdates")])
def test_the_stages_without_a_part_answer_unavailable(env, method, path):
    url = BASE + path
    _refused(env.client.open(url, method=method), 503, "unavailable")


# ── P2: the list, the icon, the detail ──────────────────────────────────

def test_the_list_has_the_list_fields_only(env):
    rows = _data(env.client.get(BASE))
    assert [row["id"] for row in rows] == [PLUGIN, DEV_PLUGIN]
    assert set(rows[0]) == {"id", "name", "author", "version", "state",
                            "badge", "size_bytes", "has_icon"}
    assert rows[1]["state"] == "needs_settings" and rows[1]["badge"] == "dev"


def test_the_icon_is_a_checked_png(env):
    resp = env.client.get(f"{BASE}/{PLUGIN}/icon")
    assert resp.status_code == 200
    assert resp.data == PNG
    assert resp.headers["Content-Type"] == "image/png"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("content", [b"GIF89a" + b"\x00" * 10,
                                     PNG + b"\x00" * (256 * 1024)],
                         ids=["not-png", "too-large"])
def test_an_icon_that_is_not_a_small_png_is_not_found(env, content):
    (env.folder / "icon.png").write_bytes(content)
    _refused(env.client.get(f"{BASE}/{PLUGIN}/icon"), 404, "not_found")


def test_an_icon_outside_the_plugin_folder_is_not_found(env):
    outside = env.tmp / "outside.png"
    outside.write_bytes(PNG)
    plugin = env.registry.plugins[PLUGIN]
    env.registry.plugins[PLUGIN] = plugin.__class__(
        **{**plugin.__dict__, "report": ManifestReport(
            _manifest(PLUGIN, icon="../../outside.png"))})
    _refused(env.client.get(f"{BASE}/{PLUGIN}/icon"), 404, "not_found")
    _refused(env.client.get(f"{BASE}/nope.x/icon"), 404, "not_found")


def test_the_detail_shows_settings_by_kind_and_never_a_path(env):
    env.settings.Save(PLUGIN, _manifest(PLUGIN),
                      {"name": "Mine", "outdir": "pick-folder"}, dev=False)
    env.secrets.Set(PLUGIN, "token", SECRET_VALUE, dev=False)
    resp = env.client.get(f"{BASE}/{PLUGIN}")
    detail = _data(resp)
    text = resp.get_data(as_text=True)
    assert str(env.picked) not in text and SECRET_VALUE not in text
    assert detail["setting_values"] == {
        "name": {"kind": "value", "value": "Mine"},
        "mode": {"kind": "value", "value": "a"},
        "outdir": {"kind": "pick", "name": "picked"},
        "token": {"kind": "secret", "set": True}}
    assert detail["reason"] is None
    assert detail["homepage"] is True and detail["funding"] is False
    assert detail["permissions"]["network"] == ["example.com"]
    assert [job["id"] for job in detail["jobs"]] == ["run", "upload", "post"]
    run = detail["jobs"][0]
    assert run["params"][1] == {"key": "count", "type": "number",
                                "label": "Count", "required": False,
                                "default": 2, "min": 1, "max": 5, "step": 1}
    assert run["actions"] == [{"target": "result_image", "label": "Send"}]
    assert detail["settings"][1]["options"] == [
        {"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
    assert detail["description"] == "Does things"


def test_the_detail_says_why_in_words(env):
    detail = _data(env.client.get(f"{BASE}/{DEV_PLUGIN}"))
    assert detail["reason"]["en"] == "Required settings are missing: Key"
    assert detail["setting_values"]["token"] == {"kind": "secret",
                                                 "set": False}
    plugin = env.registry.plugins[PLUGIN]
    env.registry.plugins[PLUGIN] = plugin.__class__(**{
        **plugin.__dict__, "state": PluginState.INCOMPATIBLE,
        "report": ManifestReport(_manifest(PLUGIN), problems=(
            ManifestProblem(ManifestCheck.MIN_AUTONA_NEWER,
                            ProblemScope.PLUGIN, "min_autona",
                            detail="9.0.0"),))})
    reason = _data(env.client.get(f"{BASE}/{PLUGIN}"))["reason"]
    assert "min_autona_newer (min_autona): 9.0.0" in reason["en"]
    env.registry.plugins[PLUGIN] = plugin.__class__(**{
        **plugin.__dict__, "state": PluginState.BLOCKED,
        "blocked_reason": {"ja": "危険", "en": "Bad"}})
    assert _data(env.client.get(f"{BASE}/{PLUGIN}"))["reason"] == {
        "ja": "危険", "en": "Bad"}


def test_an_unknown_plugin_is_not_found(env):
    _refused(env.client.get(f"{BASE}/nope.x"), 404, "not_found")
    _refused(env.client.get(f"{BASE}/NOT AN ID"), 404, "not_found")


# ── P2: settings ────────────────────────────────────────────────────────

def test_settings_are_saved_and_null_removes_one(env):
    url = f"{BASE}/{PLUGIN}/settings"
    assert _data(env.client.patch(url, json={"values": {
        "name": "Mine", "mode": "b", "outdir": "pick-folder"}})) is None
    manifest = _manifest(PLUGIN)
    saved = env.settings.Get(PLUGIN, manifest, dev=False)
    assert saved["mode"] == "b" and saved["outdir"] == str(env.picked)
    assert ("folder", "pick-folder") in env.resolved
    _data(env.client.patch(url, json={"values": {"mode": None}}))
    saved = env.settings.Get(PLUGIN, manifest, dev=False)
    assert saved["mode"] == "a" and saved["name"] == "Mine"
    # A key the body does not name stays as it is.
    _data(env.client.patch(url, json={}))
    assert env.settings.Get(PLUGIN, manifest, dev=False)["name"] == "Mine"


@pytest.mark.parametrize("body", [
    {"values": {"mode": "c"}}, {"values": {"name": 3}},
    {"values": {"token": "x"}}, {"values": {"nope": 1}},
    {"values": {"outdir": "unknown-pick"}}, {"values": []},
    {"values": {}, "other": 1}, ["values"]])
def test_a_setting_that_does_not_fit_is_invalid(env, body):
    _refused(env.client.patch(f"{BASE}/{PLUGIN}/settings", json=body), 400,
             "invalid_request")


def test_a_settings_body_that_is_not_json_is_invalid(env):
    _refused(env.client.patch(f"{BASE}/{PLUGIN}/settings", data=b"{nope",
                              content_type="application/json"),
             400, "invalid_request")
    _refused(env.client.patch(f"{BASE}/nope.x/settings", json={}), 404,
             "not_found")


def test_a_config_that_cannot_be_written_is_unavailable(env, monkeypatch):
    def fail(*_args, **_kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(env.settings, "Save", fail)
    error = _refused(env.client.patch(f"{BASE}/{PLUGIN}/settings",
                                      json={"values": {"name": "x"}}),
                     503, "unavailable")
    assert error["message"] == "config.json not written"


# ── P3: secrets ─────────────────────────────────────────────────────────

def test_a_secret_is_saved_and_cleared_and_never_answered_or_logged(env):
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="DEBUG")
    try:
        url = f"{BASE}/{PLUGIN}/secrets/token"
        resp = env.client.put(url, json={"value": SECRET_VALUE})
        assert _data(resp) is None
        assert SECRET_VALUE not in resp.get_data(as_text=True)
        assert env.secrets.Keys(PLUGIN, dev=False) == {"token"}
        _data(env.client.delete(url))
        assert env.secrets.Keys(PLUGIN, dev=False) == frozenset()
    finally:
        logger.remove(sink)
    assert lines and not any(SECRET_VALUE in line for line in lines)


@pytest.mark.parametrize("key,body", [
    ("token", {"value": "two\nlines"}), ("token", {"value": ""}),
    ("token", {"value": 3}), ("token", {}), ("name", {"value": "x"}),
    ("nope", {"value": "x"})])
def test_a_secret_that_does_not_fit_is_invalid(env, key, body):
    error = _refused(env.client.put(f"{BASE}/{PLUGIN}/secrets/{key}",
                                    json=body), 400, "invalid_request")
    assert "two" not in error["message"]


def test_a_dev_plugins_secret_is_its_own(env):
    _data(env.client.put(f"{BASE}/{DEV_PLUGIN}/secrets/token",
                         json={"value": SECRET_VALUE}))
    assert env.secrets.Keys(DEV_PLUGIN, dev=True) == {"token"}
    assert env.secrets.Keys(DEV_PLUGIN, dev=False) == frozenset()


# ── P2: runs ────────────────────────────────────────────────────────────

def _run(env, **body):
    body.setdefault("job", "run")
    body.setdefault("trigger", {"kind": "manual", "target": None})
    return env.client.post(f"{BASE}/{PLUGIN}/runs", json=body)


def test_a_run_starts_with_checked_params_and_only_the_picked_inputs(env):
    data = _data(_run(env, params={"title": "Hi", "count": 3,
                                   "doc": "pick-file"},
                      inputs={"log": "20261126/Cup", "img": None}), 201)
    assert data == {"run_id": RUN_ID}
    pluginId, jobId, trigger, params, settings, picks = env.jobs.started[-1]
    assert (pluginId, jobId, trigger.kind) == (PLUGIN, "run",
                                               TriggerKind.MANUAL)
    assert params == {"title": "Hi", "count": 3,
                      "doc": str(env.picked / "notes.txt")}
    # Left out or null: not picked (PluginJobs decides), never None.
    assert set(picks) == {"log"}
    assert picks["log"].choice == "20261126/Cup"
    assert "token" not in settings and settings["mode"] == "a"


def test_a_param_left_out_or_null_is_its_default(env):
    _data(_run(env, params={"count": None}, inputs={"log": "20261126"}), 201)
    assert env.jobs.started[-1][3] == {}


@pytest.mark.parametrize("body", [
    {"params": {"count": 9}}, {"params": {"count": "3"}},
    {"params": {"title": "x" * 11}}, {"params": {"nope": 1}},
    {"params": {"log": "20261126"}}, {"params": {"doc": "unknown-pick"}},
    {"params": []}, {"inputs": {"st": "x"}}, {"inputs": {"nope": "x"}},
    {"inputs": {"log": 3}}, {"inputs": []}, {"job": "nope"}, {"job": 3},
    {"trigger": {"kind": "hook", "target": None}},
    {"trigger": {"kind": "action", "target": "nope"}},
    {"trigger": {"kind": "manual", "target": "result_image"}},
    {"other": 1}])
def test_a_run_body_that_does_not_fit_is_invalid(env, body):
    _refused(_run(env, **body), 400, "invalid_request")
    assert env.jobs.started == []


def test_a_run_without_its_job_or_trigger_is_invalid(env):
    _refused(env.client.post(f"{BASE}/{PLUGIN}/runs", json={"job": "run"}),
             400, "invalid_request")


def test_an_action_fills_its_own_input_from_the_pages_context(env):
    _data(_run(env, trigger={"kind": "action", "target": "result_image"},
               inputs={"img": "result_1.png"}), 201)
    trigger, picks = env.jobs.started[-1][2], env.jobs.started[-1][5]
    assert trigger.target == "result_image"
    assert picks["img"].choice == "result_1.png"
    _data(_run(env, job="upload",
               trigger={"kind": "action", "target": "archive_export"},
               inputs={"clips": "20261126-120000-ab12"}), 201)
    pick = env.jobs.started[-1][5]["clips"]
    assert pick.path == os.path.join(str(env.tmp / "work"),
                                     "20261126-120000-ab12")
    _refused(_run(env, job="upload",
                  trigger={"kind": "action", "target": "archive_export"},
                  inputs={"clips": "../x"}), 400, "invalid_request")


def test_a_context_only_input_is_not_picked_by_hand(env):
    _refused(_run(env, job="upload", inputs={"clips": "x"}), 400,
             "invalid_request")


def test_busy_says_the_seconds_left(env):
    env.jobs.answer = Refused(StartRefusal.BUSY, remaining_seconds=41.2)
    error = _refused(_run(env, inputs={"log": "20261126"}), 409, "busy")
    assert error["message"] == "42"
    # Unknown: no message (the page words busy itself), whatever the detail.
    env.jobs.answer = Refused(StartRefusal.BUSY, detail="lane")
    assert _refused(_run(env, inputs={"log": "20261126"}), 409,
                    "busy")["message"] == ""


@pytest.mark.parametrize("reason", [
    StartRefusal.NEEDS_SETTINGS, StartRefusal.JOB_UNUSABLE,
    StartRefusal.INCOMPATIBLE])
def test_the_jobs_refusals_are_conflicts(env, reason):
    env.jobs.answer = Refused(reason, detail="token")
    error = _refused(_run(env, inputs={"log": "20261126"}), 409,
                     reason.value)
    assert error["message"] == "token"


def test_a_run_folder_not_written_is_unavailable(env):
    env.jobs.answer = Refused(StartRefusal.UNAVAILABLE, detail="shutting down")
    _refused(_run(env, inputs={"log": "20261126"}), 503, "unavailable")


@pytest.mark.parametrize("state,extra,reason", [
    (PluginState.DISABLED, {"enabled": False}, "disabled"),
    (PluginState.BLOCKED, {"blocked_reason": "bad"}, "blocked"),
    (PluginState.INCOMPATIBLE, {}, "incompatible")])
def test_the_plugins_state_refuses_before_the_body(env, state, extra, reason):
    plugin = env.registry.plugins[PLUGIN]
    env.registry.plugins[PLUGIN] = plugin.__class__(
        **{**plugin.__dict__, "state": state, **extra})
    _refused(_run(env, params={"count": 99}), 409, reason)
    assert env.jobs.started == []
    _refused(env.client.post(f"{BASE}/nope.x/runs",
                             json={"job": "run", "trigger": {
                                 "kind": "manual", "target": None}}),
             404, "not_found")


def test_the_runs_are_listed_and_read(env):
    runs = _data(env.client.get(f"{BASE}/{PLUGIN}/runs"))
    assert [run["run_id"] for run in runs] == [RUN_ID]
    run = _data(env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}"))
    assert run["state"] == "done" and run["trigger"] == {"kind": "manual"}
    assert set(run) >= {"warnings", "artifacts", "timeout_seconds"}
    _refused(env.client.get(f"{BASE}/{PLUGIN}/runs/20261126-120000-zzzz"),
             404, "not_found")
    _refused(env.client.get(f"{BASE}/{PLUGIN}/runs/not-a-run"), 404,
             "not_found")


def test_a_run_is_cancelled(env):
    _data(env.client.post(f"{BASE}/{PLUGIN}/runs/{RUN_ID}:cancel"))
    assert env.jobs.cancelled == [RUN_ID]
    _refused(env.client.post(
        f"{BASE}/{PLUGIN}/runs/20261126-120000-zzzz:cancel"), 404,
        "not_found")


def test_the_log_is_raw_text(env):
    resp = env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/log")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "line 1\nline 2\n"
    assert resp.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    env.jobs.log = None
    _refused(env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/log"), 404,
             "not_found")


# ── P2: artifacts ───────────────────────────────────────────────────────

def _artifacts(env):
    out = env.tmp / "work" / "out"
    out.mkdir(parents=True)
    (out / "a.txt").write_text("hello", encoding="utf-8")
    (out / "b.png").write_bytes(PNG)
    (out / "c.bin").write_bytes(b"\x00\x01")
    env.jobs.artifacts = {0: (str(out / "a.txt"), ArtifactKind.TEXT),
                          1: (str(out / "b.png"), ArtifactKind.IMAGE),
                          2: (str(out / "c.bin"), ArtifactKind.FILE)}
    env.jobs.records[RUN_ID] = _record(artifacts=(
        Artifact("a.txt", ArtifactKind.TEXT), Artifact("b.png",
                                                       ArtifactKind.IMAGE),
        Artifact("c.bin", ArtifactKind.FILE)))
    return out


def test_an_artifact_is_served_with_its_kinds_type(env):
    _artifacts(env)
    url = f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/"
    for no, content, ctype in (
            (0, b"hello", "text/plain; charset=utf-8"),
            (1, PNG, "image/png"),
            (2, b"\x00\x01", "application/octet-stream")):
        resp = env.client.get(url + str(no))
        assert resp.status_code == 200
        assert resp.data == content
        assert resp.headers["Content-Type"] == ctype
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        resp.close()
    _refused(env.client.get(url + "3"), 404, "not_found")
    _refused(env.client.get(url + "x"), 400, "invalid_request")
    _refused(env.client.get(url + "-1"), 400, "invalid_request")


def test_an_artifact_swapped_after_its_check_is_not_served(env,
                                                          monkeypatch):
    out = _artifacts(env)
    other = env.tmp / "other.txt"
    other.write_text("private", encoding="utf-8")
    checked = str(out / "a.txt")
    real_stat = os.stat
    opened = []
    real_open = open

    def spying_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        opened.append(handle)
        return handle

    def stat(path, *args, **kwargs):
        # The checked path names another file than the one opened (a
        # worker swapped it between the open and the check).
        if os.fspath(path) == checked:
            return real_stat(other)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(api, "open", spying_open, raising=False)
    monkeypatch.setattr(api.os, "stat", stat)
    resp = env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/0")
    monkeypatch.undo()
    assert _refused(resp, 404, "not_found")["message"] ==         "the artifact changed"
    assert opened and all(handle.closed for handle in opened)


def test_a_hard_linked_artifact_is_not_served(env):
    out = _artifacts(env)
    try:
        os.link(out / "a.txt", env.tmp / "link.txt")
    except OSError:
        pytest.skip("no hard links here")
    assert _refused(env.client.get(
        f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/0"), 404,
        "not_found")["message"] == "the artifact changed"


def test_an_artifacts_folder_is_revealed(env):
    out = _artifacts(env)
    _data(env.client.post(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/2"
                          ":reveal"))
    assert env.revealed == [str(out / "c.bin")]
    _refused(env.client.post(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/0"
                             ":reveal"), 400, "invalid_request")
    # Gone (or changed) by the second check: not shown.
    env.jobs.swap = lambda: env.jobs.artifacts.pop(2)
    _refused(env.client.post(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/2"
                             ":reveal"), 404, "not_found")
    assert env.revealed == [str(out / "c.bin")]
    _artifacts_again(env, out)
    _refused(env.client.post(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/9"
                             ":reveal"), 404, "not_found")
    env.deps.revealFile = None
    _refused(env.client.post(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/2"
                             ":reveal"), 503, "unavailable")


def _artifacts_again(env, out):
    env.jobs.artifacts[2] = (str(out / "c.bin"), ArtifactKind.FILE)


# ── P2: inputs, the developer's, reload ─────────────────────────────────

def test_the_input_choices_are_ids_and_labels(env):
    assert _data(env.client.get(f"{BASE}/inputs/match_log")) == [
        {"id": "20261126", "label": "2026-11-26"},
        {"id": "20261126/Cup", "label": "2026-11-26 — Cup"}]
    assert _data(env.client.get(f"{BASE}/inputs/state")) == []
    for kind in ("match_record", "archive_export", "nope"):
        _refused(env.client.get(f"{BASE}/inputs/{kind}"), 400,
                 "invalid_request")


def test_the_dev_state_names_folders_by_id_and_name(env):
    env.registry.devFolders = [DevFolder("0123456789ab",
                                         str(env.tmp / "dev" / "sandbox"),
                                         DEV_PLUGIN)]
    resp = env.client.get(f"{BASE}/dev")
    assert _data(resp) == {"enabled": False, "folders": [
        {"id": "0123456789ab", "name": "sandbox", "from_env": False}]}
    assert str(env.tmp) not in resp.get_data(as_text=True)
    _data(env.client.patch(f"{BASE}/dev", json={"enabled": True}))
    assert env.registry.devMode is True
    assert env.hooks.refreshes == 1
    for body in ({"enabled": "yes"}, {}, {"enabled": True, "x": 1}):
        _refused(env.client.patch(f"{BASE}/dev", json=body), 400,
                 "invalid_request")


def test_a_dev_folder_is_added_from_a_pick(env):
    _data(env.client.post(f"{BASE}/dev/folders", json={"pick": "pick-folder"}))
    assert env.registry.added == [str(env.picked)]
    assert env.hooks.refreshes == 1
    assert ("folder", "pick-folder") in env.resolved
    _refused(env.client.post(f"{BASE}/dev/folders",
                             json={"pick": "unknown-pick"}),
             400, "invalid_request")
    env.registry.addAnswer = Refused(StartRefusal.ID_TAKEN, detail=PLUGIN)
    _refused(env.client.post(f"{BASE}/dev/folders",
                             json={"pick": "pick-folder"}), 409, "id_taken")
    env.registry.addAnswer = Refused(StartRefusal.UNAVAILABLE,
                                     detail="dev.json not written")
    _refused(env.client.post(f"{BASE}/dev/folders",
                             json={"pick": "pick-folder"}),
             503, "unavailable")
    assert env.hooks.refreshes == 1


def test_a_dev_folder_is_removed_by_its_id(env):
    _data(env.client.delete(f"{BASE}/dev/folders/0123456789ab"))
    assert env.hooks.refreshes == 1
    env.registry.removeAnswer = False
    _refused(env.client.delete(f"{BASE}/dev/folders/0123456789ab"), 404,
             "not_found")
    _refused(env.client.delete(f"{BASE}/dev/folders/not-an-id"), 404,
             "not_found")
    assert env.hooks.refreshes == 1
    # Headless (no dispatcher): nothing to refresh.
    env.deps.hooks = None
    env.registry.removeAnswer = True
    _data(env.client.delete(f"{BASE}/dev/folders/0123456789ab"))


def test_only_a_dev_plugin_reloads(env):
    _data(env.client.post(f"{BASE}/{DEV_PLUGIN}:reload"))
    assert env.registry.reloaded == [DEV_PLUGIN]
    assert env.hooks.refreshes == 1
    _refused(env.client.post(f"{BASE}/{PLUGIN}:reload"), 400,
             "invalid_request")


def test_a_reload_that_describes_nothing_says_why(env):
    env.registry.Reload = lambda pluginId: None
    _refused(env.client.post(f"{BASE}/{DEV_PLUGIN}:reload"), 409,
             "incompatible")
    plugin = env.registry.plugins[DEV_PLUGIN]

    def gone(pluginId):
        del env.registry.plugins[DEV_PLUGIN]

    env.registry.Reload = gone
    _refused(env.client.post(f"{BASE}/{DEV_PLUGIN}:reload"), 404,
             "not_found")
    env.registry.plugins[DEV_PLUGIN] = plugin


# ── P3: actions ─────────────────────────────────────────────────────────

def test_the_actions_of_a_target_carry_their_forms(env):
    rows = _data(env.client.get(f"{BASE}/actions?target=archive_export"))
    assert [(row["plugin"], row["job"]["id"]) for row in rows] == [
        (PLUGIN, "upload"), (DEV_PLUGIN, "upload")]
    assert rows[0]["name"] == {"ja": "道具", "en": "Tool"}
    assert rows[0]["job"]["params"][0]["type"] == "archive_export"
    _refused(env.client.get(f"{BASE}/actions?target=nope"), 400,
             "invalid_request")


# ── P5a: removal ────────────────────────────────────────────────────────

def test_a_plugin_is_removed(env):
    url = f"{BASE}/{PLUGIN}:remove"
    _data(env.client.post(url, json={"data": True, "runs": "keep_out"}))
    assert env.installer.removed == [(PLUGIN, True, "keep_out")]
    assert env.registry.scans == 1 and env.hooks.refreshes == 1
    for body in ({"data": 1, "runs": "keep"}, {"data": True, "runs": "x"},
                 {"data": True}):
        _refused(env.client.post(url, json=body), 400, "invalid_request")
    _refused(env.client.post(f"{BASE}/{DEV_PLUGIN}:remove",
                             json={"data": True, "runs": "keep"}),
             400, "invalid_request")


def test_a_running_plugin_is_not_removed(env):
    env.jobs.running = True
    assert _refused(env.client.post(f"{BASE}/{PLUGIN}:remove",
                                    json={"data": False, "runs": "keep"}),
                    409, "busy")["message"] == ""
    assert env.installer.removed == []
    env.jobs.running = False
    env.installer.error = "running"
    assert _refused(env.client.post(f"{BASE}/{PLUGIN}:remove",
                                    json={"data": False, "runs": "keep"}),
                    409, "busy")["message"] == ""
    assert env.registry.scans == 0 and env.hooks.refreshes == 0
    env.jobs.running = False
    env.installer.error = "remove_failed"
    _refused(env.client.post(f"{BASE}/{PLUGIN}:remove",
                             json={"data": False, "runs": "keep"}),
             503, "unavailable")
    env.installer.error = "not_found"
    _refused(env.client.post(f"{BASE}/{PLUGIN}:remove",
                             json={"data": False, "runs": "keep"}),
             404, "not_found")


# ── P7: hooks ───────────────────────────────────────────────────────────

def test_a_hooks_value_of_a_field_the_job_no_longer_has_is_not_answered(
        env):
    path = os.path.join(os.path.abspath(os.sep), "Users", "someone",
                        "private", "old.txt")
    env.hooks.Status = lambda pluginId: [
        env.hooks._Status(params={"doc": path, "gone": path}),
        env.hooks._Status(params={"doc": path},
                          key="removed_job/result_image_saved")]
    resp = env.client.get(f"{BASE}/{PLUGIN}/hooks")
    rows = _data(resp)
    assert rows[0]["params"] == {"doc": {"kind": "pick", "name": "old.txt"}}
    assert rows[1]["params"] == {}
    assert "private" not in resp.get_data(as_text=True)


def test_the_hooks_show_their_values_by_kind(env):
    resp = env.client.get(f"{BASE}/{PLUGIN}/hooks")
    rows = _data(resp)
    assert "secret" not in resp.get_data(as_text=True)
    assert rows[0]["key"] == "post/result_image_saved"
    assert rows[0]["params"] == {"doc": {"kind": "pick", "name": "a.txt"}}
    assert rows[0]["history"][0]["run_id"] == RUN_ID
    assert rows[0]["last_error"] == {"code": "http", "message": "boom",
                                     "hint": None}


def test_a_hook_is_enabled_with_its_values(env):
    url = f"{BASE}/{PLUGIN}/hooks/post/result_image_saved"
    row = _data(env.client.put(url, json={"enabled": True,
                                          "params": {"doc": "pick-file"}}))
    assert row["enabled"] is True
    assert env.hooks.set[-1] == (PLUGIN, "post/result_image_saved", True,
                                 {"doc": "pick-file"})
    env.hooks.answer = Refused(StartRefusal.INVALID_REQUEST,
                               detail="doc: unknown pick id")
    _refused(env.client.put(url, json={"enabled": True}), 400,
             "invalid_request")
    _refused(env.client.put(url, json={"enabled": "on"}), 400,
             "invalid_request")
    _refused(env.client.put(f"{BASE}/{PLUGIN}/hooks/Bad/x",
                            json={"enabled": False}), 404, "not_found")


def test_a_hooks_sample_starts_a_run(env):
    url = f"{BASE}/{PLUGIN}/hooks/post/result_image_saved:sample"
    assert _data(env.client.post(url), 201) == {"run_id": RUN_ID}
    env.hooks.sample = Refused(StartRefusal.INVALID_REQUEST,
                               detail="not a dev plugin")
    _refused(env.client.post(url), 400, "invalid_request")


def test_hooks_are_unavailable_without_the_dispatcher(env):
    env.deps.hooks = None
    _refused(env.client.get(f"{BASE}/{PLUGIN}/hooks"), 503, "unavailable")


# ── the shapes against routes_v1.json types ─────────────────────────────

LIST_KEYS = {"id", "name", "author", "version", "state", "badge",
             "size_bytes", "has_icon"}
DETAIL_KEYS = LIST_KEYS | {"description", "reason", "homepage", "funding",
                           "permissions", "settings", "setting_values",
                           "jobs"}
JOB_KEYS = {"id", "title", "lane", "timeout_seconds", "manual", "params",
            "actions", "hooks"}
# routes_v1 DevState's folder {id, name}, plus from_env (the env's folders
# cannot be removed: the contract is to name it).
DEV_FOLDER_KEYS = {"id", "name", "from_env"}


def test_the_types_named_here_are_routes_v1s():
    types = LEDGER["types"]
    for key in DETAIL_KEYS - LIST_KEYS:
        assert key in types["PluginDetail"], key
    for key in JOB_KEYS:
        assert key in types["PluginDetail"], key
    for key in ("enabled", "folders", "id", "name"):
        assert key in types["DevState"], key
    assert types["RunStarted"] == "{run_id}"


def test_the_detail_and_its_jobs_have_the_types_keys(env):
    detail = _data(env.client.get(f"{BASE}/{PLUGIN}"))
    assert set(detail) == DETAIL_KEYS
    assert all(set(job) == JOB_KEYS for job in detail["jobs"])
    assert set(detail["permissions"]) == {"network", "files", "spawn",
                                          "autona_api", "note"}
    started = _data(_run(env, inputs={"log": "20261126"}), 201)
    assert set(started) == {"run_id"}
    env.registry.devFolders = [DevFolder("0123456789ab", str(env.tmp),
                                         from_env=True)]
    dev = _data(env.client.get(f"{BASE}/dev"))
    assert set(dev) == {"enabled", "folders"}
    assert set(dev["folders"][0]) == DEV_FOLDER_KEYS
    assert dev["folders"][0]["from_env"] is True


def test_a_plugin_whose_manifest_is_unreadable_has_a_whole_detail(env):
    env.registry.plugins["broken.one"] = RegisteredPlugin(
        id="broken.one", folder=str(env.tmp), badge=Badge.DEV,
        report=ManifestReport(None, problems=(ManifestProblem(
            ManifestCheck.MANIFEST_UNREADABLE, ProblemScope.PLUGIN,
            "plugin.json", detail="not JSON"),)),
        state=PluginState.INCOMPATIBLE)
    detail = _data(env.client.get(f"{BASE}/broken.one"))
    assert set(detail) == DETAIL_KEYS - {"description"}
    assert detail["permissions"] == {"network": [], "files": "inputs",
                                     "spawn": False, "autona_api": "none"}
    assert detail["name"] == "broken.one"
    assert detail["settings"] == [] and detail["jobs"] == []
    assert detail["setting_values"] == {}
    assert "manifest_unreadable" in detail["reason"]["en"]


def test_no_store_is_on_raw_bodies_refusals_and_errors(env, monkeypatch):
    _artifacts(env)
    answers = [
        env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/0"),
        env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/2"),
        env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/log"),
        env.client.patch(f"{BASE}/{PLUGIN}/settings",
                         json={"values": {"mode": "c"}}),
        env.client.post(f"{BASE}/{PLUGIN}/runs", json={"job": 3}),
    ]

    def fail():
        raise RuntimeError("boom")

    monkeypatch.setattr(env.registry, "Entries", fail)
    answers.append(env.client.get(BASE))
    for resp in answers:
        assert resp.headers["Cache-Control"] == "no-store"
        resp.close()
    assert answers[-1].status_code == 500


def test_a_parts_exception_tells_nothing(env, monkeypatch):
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="DEBUG")
    secret = os.path.join(os.path.abspath(os.sep), "Users", "someone",
                          "private-folder")

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"cannot open {secret}")

    try:
        monkeypatch.setattr(env.registry, "Entries", fail)
        resp = env.client.get(BASE)
        assert resp.status_code == 500
        assert resp.get_json()["error"]["message"] == "internal error"
        assert "private-folder" not in resp.get_data(as_text=True)
        monkeypatch.setattr(env.settings, "Save", fail)
        lines.clear()
        resp = env.client.patch(f"{BASE}/{PLUGIN}/settings",
                                json={"values": {"name": "x"}})
        error = _refused(resp, 503, "unavailable")
        assert "private-folder" not in json.dumps(error)
    finally:
        logger.remove(sink)
    assert any("RuntimeError" in line for line in lines)
    assert not any("private-folder" in line for line in lines)


def test_settings_values_and_pick_ids_are_not_logged(env):
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="DEBUG")
    try:
        _data(env.client.patch(f"{BASE}/{PLUGIN}/settings", json={
            "values": {"name": "Typed-By-The-User", "outdir": "pick-folder"}}))
        _data(env.client.post(f"{BASE}/dev/folders",
                              json={"pick": "pick-folder"}))
        _data(_run(env, params={"title": "Hello-Text", "doc": "pick-file"},
                   inputs={"log": "20261126/Cup"}), 201)
    finally:
        logger.remove(sink)
    assert lines
    for value in ("Typed-By-The-User", "pick-folder", "pick-file",
                  "Hello-Text", "Cup", str(env.picked)):
        assert not any(value in line for line in lines), value


def test_a_text_over_its_limit_is_not_served(env, monkeypatch):
    out = _artifacts(env)
    url = f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/0"
    monkeypatch.setattr(api, "TEXT_ARTIFACT_MAX_BYTES", 3)
    assert _refused(env.client.get(url), 404, "not_found")["message"] == \
        "the artifact changed"
    # Grown after fstat (fstat says 1 byte): read to the limit, refused.
    real_fstat = os.fstat

    def small(fd):
        info = real_fstat(fd)
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev,
                               info.st_nlink, info.st_uid, info.st_gid, 1,
                               info.st_atime, info.st_mtime, info.st_ctime))

    monkeypatch.setattr(api.os, "fstat", small)
    assert _refused(env.client.get(url), 404, "not_found")["message"] == \
        "the artifact changed"
    monkeypatch.undo()
    (out / "a.txt").write_text("hello", encoding="utf-8")


def test_a_file_artifact_is_sent_up_to_its_checked_size(env, monkeypatch):
    out = _artifacts(env)
    (out / "c.bin").write_bytes(b"0123456789")
    real_fstat = os.fstat

    def four(fd):
        info = real_fstat(fd)
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev,
                               info.st_nlink, info.st_uid, info.st_gid, 4,
                               info.st_atime, info.st_mtime, info.st_ctime))

    monkeypatch.setattr(api.os, "fstat", four)
    resp = env.client.get(f"{BASE}/{PLUGIN}/runs/{RUN_ID}/artifacts/2")
    assert resp.status_code == 200
    assert resp.data == b"0123"
    assert resp.headers["Content-Length"] == "4"
    resp.close()


@pytest.mark.parametrize("method,path", [
    ("POST", f"/{DEV_PLUGIN}:reload"),
    ("POST", f"/{PLUGIN}/runs/{RUN_ID}:cancel"),
    ("POST", f"/{PLUGIN}/runs/{RUN_ID}/artifacts/2:reveal"),
    ("POST", f"/{PLUGIN}/hooks/post/result_image_saved:sample"),
    ("DELETE", f"/{PLUGIN}/secrets/token"),
    ("DELETE", "/dev/folders/0123456789ab")])
def test_a_route_without_a_body_refuses_a_key(env, method, path):
    _artifacts(env)
    _refused(env.client.open(BASE + path, method=method, json={"x": 1}),
             400, "invalid_request")


def test_registering_again_replaces_the_deps_only(env):
    blueprint = Blueprint("api_v1", __name__, url_prefix="/api/v1")
    first = api.PluginApiDeps(registry=env.registry)
    api.RegisterPluginRoutes(blueprint, first)
    second = api.PluginApiDeps()
    api.RegisterPluginRoutes(blueprint, second)
    app = Flask(__name__)
    app.register_blueprint(blueprint)
    rules = [rule for rule in app.url_map.iter_rules()
             if rule.endpoint.startswith("api_v1.")]
    assert len(rules) == len(api.ROUTES)
    _refused(app.test_client().get(BASE), 503, "unavailable")
