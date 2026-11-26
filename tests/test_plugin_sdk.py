"""The SDK sdk/autona_plugin.py (ADR-009 D10 P4, C.7; lane L12) against the
contract and Autona's own parts: its constants against
docs/plugins/contract_1.json, its pure functions against PluginRunner's,
its manifest check against PluginRegistry.Check, and its fake host against
the real PluginRunner -- the same fake worker
(tests/fixtures/fake_plugin_worker.py, every mode) and a small worker written
with the SDK's helpers run through both must end the same way with the same
events and warnings. The plugin folders sit under a folder with a non-ASCII
name.

The SDK is loaded from its file (it is not a package of the app: nothing in
src imports it, PyInstaller never sees it). The process tests are Windows
only (the host platform), as in tests/test_plugin_runner.py.
"""
import dataclasses
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.core import PluginJobs as J
from src.core import PluginRunner as R
from src.core import PluginTypes as T
from src.core.PluginRegistry import PluginRegistry
from tests.test_plugin_registry import CASES, _example, _one_job
from tests.test_plugin_registry import _field as _source_field
from tests.test_plugin_registry import _plugin as _registry_plugin

ROOT = Path(__file__).resolve().parents[1]
SDK_FILE = ROOT / "sdk" / "autona_plugin.py"
CONTRACT = json.loads((ROOT / "docs" / "plugins" / "contract_1.json")
                      .read_text(encoding="utf-8"))
FAKE = ROOT / "tests" / "fixtures" / "fake_plugin_worker.py"
WINDOWS = sys.platform == "win32"
windows = pytest.mark.skipif(not WINDOWS, reason="the host platform only")
HOST = "9.0.0"
LONG = 600.0
COOPERATIVE = 15.0
NON_ASCII = "プラグイン ü"


def _load_sdk():
    spec = importlib.util.spec_from_file_location("autona_plugin_under_test", SDK_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ap = _load_sdk()


# ── the SDK's place ─────────────────────────────────────────────────────

def test_the_sdk_is_one_mit_file_of_the_standard_library_outside_the_app():
    source = SDK_FILE.read_text(encoding="utf-8")
    assert source.startswith("# MIT License")
    import ast
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    # Nothing in the app imports it, and the build does not bundle it.
    for path in [ROOT / "main.py", *(ROOT / "src").rglob("*.py")]:
        assert "autona_plugin" not in path.read_text(encoding="utf-8"), path
    spec = (ROOT / "packaging" / "autona.spec").read_text(encoding="utf-8")
    assert "autona_plugin" not in spec and "sdk/" not in spec and "sdk\\" not in spec
    # Python 3.8 (an author's interpreter may be old): the syntax parses.
    ast.parse(source, feature_version=(3, 8))


# ── the constants against the contract ──────────────────────────────────

def test_the_constants_are_the_contracts():
    c = CONTRACT
    assert (ap.PROTOCOL, ap.MANIFEST) == (c["versions"]["protocol"], c["versions"]["manifest"])
    assert list(ap.HOST_SPEAKS_PROTOCOLS) == c["versions"]["host_speaks_protocols"]
    assert list(ap.HOST_READS_MANIFESTS) == c["versions"]["host_reads_manifests"]
    assert ap.INPUT_FORMATS == c["versions"]["input_formats"]
    p = c["patterns"]
    for name, value in [("plugin_id", ap.PLUGIN_ID_PATTERN), ("job_id", ap.JOB_ID_PATTERN),
                        ("field_key", ap.FIELD_KEY_PATTERN), ("hook_key", ap.HOOK_KEY_PATTERN),
                        ("name", ap.NAME_PATTERN), ("platform", ap.PLATFORM_PATTERN),
                        ("version", ap.VERSION_PATTERN), ("run_id", ap.RUN_ID_PATTERN),
                        ("error_code", ap.ERROR_CODE_PATTERN),
                        ("file_extension", ap.FILE_EXTENSION_PATTERN),
                        ("github_repo", ap.GITHUB_REPO_PATTERN), ("locale", ap.LOCALE_PATTERN)]:
        assert value == p[name], name
    assert ap.PLUGIN_ID_MAX_LENGTH == p["plugin_id_max_length"]
    assert ap.RESERVED_ID_PREFIX == p["reserved_id_prefix"]
    assert ap.HOST_PLATFORM == p["host_platform"]
    assert ap.RESERVED_ENTRY_PREFIX == c["commands"]["entry"]["reserved_prefix"]
    assert ap.DESCRIBE_TIMEOUT_SECONDS == c["commands"]["describe"]["timeout_seconds"]
    assert list(ap.DESCRIBE_FAILURES) == c["commands"]["describe"]["unusable_when"]

    lim = c["limits"]
    for name in ("summary_max_length", "max_warnings", "max_artifacts", "line_max_bytes",
                 "worker_log_max_bytes", "log_message_max_length", "error_message_max_length",
                 "error_hint_max_length", "label_max_length", "hook_timeout_cap_seconds",
                 "hook_job_memory_mb_max", "describe_timeout_seconds", "cancel_grace_seconds",
                 "manifest_max_bytes", "icon_max_bytes", "name_max_length",
                 "description_max_length", "url_max_length", "select_max_options",
                 "secret_max_length", "installed_path_max_length"):
        assert getattr(ap, name.upper()) == lim[name], name
    assert ap.LIGHT_TIMEOUT_SECONDS == (lim["light_timeout_seconds"]["min"],
                                        lim["light_timeout_seconds"]["max"])
    assert ap.HEAVY_TIMEOUT_SECONDS == (lim["heavy_timeout_seconds"]["min"],
                                        lim["heavy_timeout_seconds"]["max"])
    assert ap.LIGHT_MEMORY_MB == (lim["light_memory_mb"]["min"], lim["light_memory_mb"]["max"])
    assert ap.TEXT_MAX_LENGTH == lim["text_max_length"]
    assert ap.CANCEL_GRACE_SECONDS == c["stdin"]["cancel_grace_seconds"]

    events = c["events"]
    assert list(ap.EVENT_TYPES) == [row["type"] for row in events["types"]]
    assert set(ap.TERMINAL_EVENTS) == {row["type"] for row in events["types"] if row["terminal"]}
    stage = next(row for row in events["types"] if row["type"] == "stage")
    assert ap.STAGE_MAX_LENGTH == stage["fields"]["stage"]["max_length"]
    assert ap.LINE_MAX_BYTES == events["line_max_bytes"]

    arts = c["artifacts"]
    assert list(ap.ARTIFACT_KINDS) == arts["fields"]["kind"]["values"]
    assert list(ap.ARTIFACT_REJECTIONS) == arts["rejected"]
    kinds = {row["kind"]: row for row in arts["kinds"]}
    assert ap.TEXT_ARTIFACT_MAX_BYTES == kinds["text"]["max_bytes"]
    assert ap.IMAGE_ARTIFACT_MAX_BYTES == kinds["image"]["max_bytes"]
    assert list(ap.IMAGE_ARTIFACT_EXTENSIONS) == kinds["image"]["extensions"]

    ft = c["field_types"]
    assert list(ap.VALUE_FIELD_TYPES) == [row["type"] for row in ft["value"]]
    assert list(ap.SOURCE_FIELD_TYPES) == [row["type"] for row in ft["source"]]
    assert list(ap.CONTEXT_ONLY_TYPES) == ft["context_only_types"]
    trig = c["triggers"]
    assert list(ap.TRIGGER_KINDS) == trig["kinds"]
    assert ap.ACTION_FILLS == {row["target"]: row["fills"] for row in trig["actions"]}
    assert ap.HOOK_FILLS == {row["on"]: row["fills"] for row in trig["hooks"]}

    env = c["environment"]
    assert list(ap.ENV_REMOVE_PREFIXES) == env["remove_prefixes"]
    assert list(ap.ENV_REMOVE_NAMES) == env["remove_names"]
    assert [ap.ENV_PYTHONIOENCODING, ap.ENV_PYINSTALLER_RESET, ap.ENV_PLUGIN_DIR,
            ap.ENV_PROTOCOL, ap.ENV_SECRET_PREFIX + "<KEY>", ap.ENV_API_URL] == \
        [row["name"] for row in env["add"]]

    end = c["end"]
    assert list(ap.JOB_STATES) == end["states"]
    assert list(ap.END_REASONS) == end["reasons"]
    codes = c["manifest_checks"]["codes"]
    assert list(ap.MANIFEST_CHECKS) == [(row["code"], row["scope"]) for row in codes]
    assert {row.get("settings_scope", "plugin") for row in codes
            if "settings_scope" in row} == {"plugin"}
    assert ap.MANIFEST_FILE == c["manifest_paths"]["file"]
    assert ap.REQUEST_FILE == c["request"]["file"]


def test_the_constants_are_the_engines():
    assert ap.MAX_WARNINGS == T.MAX_WARNINGS and ap.LINE_MAX_BYTES == T.LINE_MAX_BYTES
    assert ap.FALLBACK_ERROR_CODE == T.FALLBACK_ERROR_CODE
    assert set(ap.DESCRIBE_FAILURES) == {item.value for item in T.DescribeFailure}
    assert {code for code, _scope in ap.MANIFEST_CHECKS} == \
        {item.value for item in T.ManifestCheck}
    assert ap._MEMORY_LIMIT_REACHED == R.MEMORY_LIMIT_REACHED


# ── the pure functions against PluginRunner's ───────────────────────────

PARENT = {
    "PATH": "C:\\bin", "SystemRoot": "C:\\Windows", "AUTONA_VODSPLIT_CMD": "x",
    "Autona_Secret_Other": "leak", "QT_API": "pyside6", "qt_api": "pyqt",
    "pythonioencoding": "cp932", "AUTONAMOUS": "kept", "autona_plugin_dir": "old",
}


@pytest.mark.parametrize("protocol, secrets, api", [
    (None, {}, None), (1, {}, None),
    (1, {"AUTONA_SECRET_TOKEN": "t", "autona_secret_other": "o"}, "http://127.0.0.1:5107/api/v1"),
])
def test_the_environment_is_the_engines(protocol, secrets, api):
    assert ap.build_environment(PARENT, "C:\\p", protocol=protocol, secrets=secrets,
                                api_url=api) == \
        R.BuildEnvironment(PARENT, pluginDir="C:\\p", protocol=protocol, secrets=secrets,
                           apiUrl=api)


@pytest.mark.parametrize("secrets", [{"TOKEN": "x"}, {"AUTONA_SECRET_A": "1",
                                                      "autona_secret_a": "2"},
                                     {"AUTONA_SECRET_A": "nul\0"}])
def test_bad_secrets_are_refused_like_the_engine(secrets):
    with pytest.raises(ValueError):
        R.BuildEnvironment({}, pluginDir="p", protocol=1, secrets=secrets, apiUrl=None)
    with pytest.raises(ValueError):
        ap.build_environment({}, "p", protocol=1, secrets=secrets)


def test_the_end_is_the_engines_in_every_combination():
    finals = [None, *ap.TERMINAL_EVENTS]
    for final in finals:
        for exit_code in (0, 2, 130, 1, 3, 255, -9, None):
            for flags in range(32):
                cancel, timed, launch, missing, memory = (bool(flags & (1 << i))
                                                          for i in range(5))
                state, reason = R.EndState(
                    None if final is None else T.EventType(final), exit_code,
                    cancelRequested=cancel, timedOut=timed, launchFailed=launch,
                    entryMissing=missing, memoryLimitHit=memory)
                assert ap.end_state(final, exit_code, cancel, timed, launch, missing,
                                    memory) == (state.value, reason and reason.value)


def _engine_event(event):
    """A PluginRunner event as the SDK's dict (without raw)."""
    out = {"type": event.type.value}
    for item in dataclasses.fields(event):
        if item.name == "raw":
            continue
        value = getattr(event, item.name)
        if item.name == "artifacts":
            value = [{"path": a.path, "kind": a.kind.value, "label": a.label} for a in value]
        out[item.name] = value
    return out


def _sdk_event(event):
    return {key: value for key, value in event.items() if key != "raw"}


EVENT_OBJECTS = [
    {"type": "stage", "stage": "s" * 70, "label": {"ja": "準備", "en": "x" * 300, "x": 1},
     "index": 1, "count": 2},
    {"type": "stage", "index": "two", "count": 0, "colour": "red"},
    {"type": "stage", "stage": "a", "index": True},
    {"type": "progress", "fraction": 7, "overall": "half", "eta": -1, "extra": True},
    {"type": "progress", "stage": "p", "fraction": 0.5, "overall": 1, "eta": None},
    {"type": "progress", "fraction": float("nan"), "eta": float("inf")},
    {"type": "log", "message": "m" * 3000, "id": 7},
    {"type": "log"},
    {"type": "warning", "message": {"ja": "警告"}, "future_key": [1]},
    {"type": "done", "summary": "s" * 600, "artifacts": [
        {"path": f"a{i}.txt", "kind": "text", "label": "l" * 300} for i in range(60)]},
    {"type": "done", "artifacts": [{"path": "x", "kind": "html"}, {"kind": "text"},
                                   {"path": "", "kind": "text"}, "no", {"path": "i.png",
                                                                        "kind": "image"}]},
    {"type": "done", "artifacts": "none", "summary": 5},
    {"type": "error", "code": "Bad-Code!", "message": "e" * 2500, "hint": "h" * 1200},
    {"type": "error", "code": "ok_code", "message": {"en": "x"}, "hint": None},
    {"type": "error"},
    {"type": "cancelled", "extra": 1},
    {"type": "telemetry"}, {"message": "no type"}, {"type": 5}, [1, 2], "text", None,
]


@pytest.mark.parametrize("obj", EVENT_OBJECTS)
def test_an_event_is_taken_as_the_engine_takes_it(obj):
    engine = R.ParseEvent(obj)
    sdk = ap.parse_event(obj)
    if engine is None:
        assert sdk is None
        return
    assert _sdk_event(sdk) == _engine_event(engine)
    assert sdk["raw"] is obj


def test_the_entry_argv_is_the_engines(tmp_path):
    folder = str(tmp_path)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    absolute = os.path.abspath(os.sep + "tools")
    for entry, dev in [(["bin/w.exe", "-x"], False), (["python", "main.py"], True),
                       (["main.py"], True), (["main.py"], False), ([absolute], True),
                       (["sub\\w.exe"], True)]:
        assert ap.entry_argv(folder, entry, dev) == R.EntryArgv(folder, entry, dev=dev)
    for bad in ([], [""]):
        with pytest.raises(ValueError):
            ap.entry_argv(folder, bad, False)


# ── the manifest's check against PluginRegistry.Check ───────────────────

def _registry(tmp_path):
    return PluginRegistry(str(tmp_path / "app"), str(tmp_path / "ud"), HOST,
                          environ=lambda: {}, which=lambda _name: None)


def _problems(items):
    return sorted((p["code"], p["scope"], p["path"], p["job"]) for p in items)


def _registry_problems(report):
    return sorted((p.code.value, p.scope.value, p.path, p.job) for p in report.problems)


def _compare_check(tmp_path, manifest, dev=False, name=None, environ=None, source="manual"):
    """``source`` "manual" (a zip installed by hand) or None (not known)."""
    folder = _registry_plugin(tmp_path / NON_ASCII / "plugins", manifest, name=name)
    registry = PluginRegistry(
        str(tmp_path / "app"), str(tmp_path / "ud"), HOST, environ=lambda: {},
        which=(lambda _name: None) if environ == {} else shutil.which)
    report = registry.Check(json.loads(json.dumps(manifest)), str(folder),
                            None if source is None else T.InstallSource(source), dev=dev,
                            hostVersion=HOST)
    sdk = ap.check_manifest(json.loads(json.dumps(manifest)), str(folder), dev=dev,
                            host_version=HOST, environ=environ, source=source)
    assert _problems(sdk) == _registry_problems(report)
    return sdk


def test_the_example_passes_both_but_its_reserved_id(tmp_path):
    found = _compare_check(tmp_path, _example(), environ={})
    assert _problems(found) == [("id_reserved", "plugin", "id", None)]
    # Not knowing where it came from, the registry does not hold it.
    assert _compare_check(tmp_path / "unknown", _example(), environ={}, source=None) == []
    with pytest.raises(ValueError):
        ap.check_manifest(_example(), str(tmp_path), source="github")


@pytest.mark.parametrize("name, code, job, change", CASES, ids=[case[0] for case in CASES])
def test_every_broken_manifest_gets_the_registrys_problems(tmp_path, name, code, job,
                                                           change):
    manifest = _example()
    manifest["id"] = "example.webhook"
    change(manifest)
    found = _compare_check(tmp_path, manifest, environ={}, name="example.webhook")
    assert code in {p["code"] for p in found}


@pytest.mark.parametrize("job", [
    {"manual": False, "params": [_source_field("image", "result_image"),
                                 _source_field("match", "match_record")],
     "actions": [{"target": "result_image", "label": "a"}],
     "hooks": [{"on": "match_decided", "label": "h"}]},
    {"params": [_source_field("thumb", "thumbnail_image"), _source_field("now", "state"),
                _source_field("log", "match_log")],
     "actions": [{"target": "thumbnail_image", "label": "a"}]},
    {"params": [_source_field("now", "state")],
     "actions": [{"target": "result_image", "label": "a"}]},
    {"params": [_source_field("match", "match_record")]},
])
def test_the_fill_rules_are_the_registrys(tmp_path, job):
    _compare_check(tmp_path, _one_job(job), environ={})


@pytest.mark.parametrize("change", [
    lambda m: m.update(id="taguyan.webhook\n"),
    lambda m: m.update(manifest=1.0, protocol=[1.0]),
    lambda m: m["jobs"][0].update(timeout_seconds=60.5),
    lambda m: m["settings"][1].update(options="none"),
    lambda m: m.update(min_autona="1" * 5000 + ".0.0"),
    lambda m: m["jobs"][1]["params"].append({"key": "x", "type": ["text"], "label": "x",
                                             "required": True}),
    lambda m: m.update(id="con.tool"),
    lambda m: m.update(icon="../icon.png"),
    lambda m: m.update(icon="missing.png"),
    lambda m: m.update(entry=["bin/../../x.exe"]),
    lambda m: m.update(entry=["bin/webhook.txt"]),
    lambda m: m.pop("jobs"),
    lambda m: m.update(manifest=2),
], ids=["newline id", "integral floats", "half timeout", "bad options", "huge version",
        "list type", "device name", "icon outside", "icon missing", "entry up",
        "entry not exe", "no jobs", "manifest 2"])
def test_odd_manifests_get_the_registrys_problems(tmp_path, change):
    manifest = _example()
    change(manifest)
    _compare_check(tmp_path, manifest, environ={}, name="taguyan.webhook")
    _compare_check(tmp_path / "unknown", manifest, environ={}, name="taguyan.webhook",
                   source=None)


def test_the_folder_name_is_the_id_but_in_a_dev_folder(tmp_path):
    manifest = _example()
    found = _compare_check(tmp_path, manifest, name="other.name", environ={})
    assert "id_folder_mismatch" in {p["code"] for p in found}
    found = _compare_check(tmp_path / "dev", manifest, dev=True, name="other.name",
                           environ={})
    assert not {p["code"] for p in found} & {"id_folder_mismatch", "id_reserved"}


@pytest.mark.parametrize("entry", [["python", "main.py"], ["no-such-command-autona"],
                                   ["main.py"], ["bin/missing.exe"]])
def test_a_dev_entry_is_checked_as_the_registry_checks_it(tmp_path, entry):
    manifest = _example()
    manifest["entry"] = entry
    folder = tmp_path / NON_ASCII / "dev"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    if entry == ["main.py"]:
        (folder / "main.py").write_text("", encoding="utf-8")
    registry = PluginRegistry(str(tmp_path / "app"), str(tmp_path / "ud"), HOST)
    report = registry.Check(manifest, str(folder), None, dev=True, hostVersion=HOST)
    assert _problems(ap.check_manifest(manifest, str(folder), dev=True,
                                       host_version=HOST)) == _registry_problems(report)


@pytest.mark.parametrize("content", [b"", b"[", b"\xff\xfe{}", b"[1, 2]",
                                     b"{" + b" " * (256 * 1024) + b"}"],
                         ids=["empty", "cut", "not utf-8", "a list", "too big"])
def test_an_unreadable_manifest_is_unreadable_in_both(tmp_path, content):
    from src.core.PluginRegistry import ReadManifest
    folder = tmp_path / "a.b"
    folder.mkdir()
    (folder / "plugin.json").write_bytes(content)
    raw, problem = ReadManifest(str(folder))
    sdk_raw, sdk_problem = ap.read_manifest_file(str(folder))
    if problem is not None:
        assert sdk_problem["code"] == problem.code.value and sdk_raw is None
    else:
        report = _registry(tmp_path).Check(raw, str(folder), None, dev=True,
                                           hostVersion=HOST)
        assert _problems(ap.check_manifest(sdk_raw, str(folder), dev=True)) == \
            _registry_problems(report)


# ── the fake plugin ─────────────────────────────────────────────────────

def _path_with_python():
    """The parent environment with this interpreter first on PATH (the
    manifest's entry is ``python``)."""
    env = dict(os.environ)
    path_key = next((key for key in env if key.upper() == "PATH"), "PATH")
    env[path_key] = os.path.dirname(sys.executable) + os.pathsep + env.get(path_key, "")
    return env


FAKE_MANIFEST = {
    "manifest": 1, "id": "example.fake", "version": "1.2.3", "name": {"ja": "偽物", "en": "Fake"},
    "author": "taguyan", "license": "MIT", "min_autona": "2.6.0", "protocol": [1],
    "platforms": ["windows-x64"], "entry": ["python", "worker.py"],
    "permissions": {"network": [], "files": "inputs", "spawn": True, "autona_api": "none"},
    "settings": [{"key": "token", "type": "secret", "label": "token"},
                 {"key": "style", "type": "select", "label": "s", "default": "short",
                  "options": [{"value": "short", "label": "s"},
                              {"value": "long", "label": "l"}]}],
    "jobs": [
        {"id": "post", "title": "post", "lane": "heavy", "timeout_seconds": 600,
         "params": [{"key": "mode", "type": "text", "label": "mode", "default": "ok"}]},
        {"id": "light", "title": "light", "lane": "light", "timeout_seconds": 300,
         "memory_mb": 256,
         "params": [{"key": "mode", "type": "text", "label": "mode", "default": "ok"},
                    {"key": "image", "type": "result_image", "label": "image"},
                    {"key": "log", "type": "match_log", "label": "log"}],
         "actions": [{"target": "result_image", "label": "a"}],
         "hooks": [{"on": "result_image_saved", "label": "h"}]},
    ],
}


@pytest.fixture
def fake_plugin(tmp_path):
    if not (Path(sys.executable).parent / ("python.exe" if WINDOWS else "python")).exists():
        pytest.skip("the interpreter's folder has no 'python' to put on PATH")
    folder = tmp_path / NON_ASCII / "example.fake"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps(FAKE_MANIFEST, ensure_ascii=False),
                                        encoding="utf-8")
    shutil.copyfile(FAKE, folder / "worker.py")
    return folder


def _prepare(folder, tmp_path, job="post", mode="ok", environ=None, **options):
    params = dict(options.pop("params", {}), mode=mode)
    env = _path_with_python()
    env.update(environ or {})
    return ap.prepare_run(str(folder), job, params=params, environ=env,
                          work=str(tmp_path / NON_ASCII / "work"), **options)


def _spec(prepared):
    return T.LaunchSpec(argv=tuple(prepared.argv), cwd=prepared.cwd, env=prepared.env,
                        lane=T.Lane(prepared.lane), log_path=prepared.log_path,
                        timeout_seconds=prepared.timeout, memory_mb=prepared.memory_mb)


def _engine_run(prepared, cancel_after=None, grace=COOPERATIVE):
    runner = R.PluginRunner(poll=0.02, cancelGrace=grace)
    events, ends = [], []
    handle = runner.Start(_spec(prepared), events.append, ends.append)
    timer = None
    if cancel_after is not None:
        timer = threading.Timer(cancel_after, handle.Cancel)
        timer.start()
    outcome = handle.Wait(LONG)
    if timer is not None:
        timer.cancel()
    runner.Shutdown(5.0)
    assert outcome is not None and ends == [outcome]
    return outcome, events


def _timeless(events):
    """The events but progress: how many a cancel or a time limit lets
    through depends on the clock (and on how long the interpreter took to
    start)."""
    return [event for event in events if event["type"] != "progress"]


def _folded(events):
    """The engine's events as RunResult.events keeps them (a run of progress
    events folded into its latest)."""
    folded = []
    for event in events:
        if event["type"] == "progress" and folded and folded[-1]["type"] == "progress":
            folded[-1] = event
        else:
            folded.append(event)
    return folded


def _plain(events, run_folder):
    """The events as JSON with the run's folder named <run> (an absolute
    artifact path holds it)."""
    text = json.dumps(events, ensure_ascii=False)
    return json.loads(text.replace(json.dumps(run_folder, ensure_ascii=False)[1:-1], "<run>"))


def _both(folder, tmp_path, job="post", mode="ok", cancel_after=None, grace=COOPERATIVE,
          timeless=False, **options):
    """The same run through the SDK's fake host and the real PluginRunner:
    the same end, events and warnings -> (sdk result, engine outcome, engine
    events)."""
    sdk = ap.execute(_prepare(folder, tmp_path, job, mode, **options),
                     cancel_after=cancel_after, grace=grace, poll=0.02)
    prepared = _prepare(folder, tmp_path, job, mode, **options)
    engine, events = _engine_run(prepared, cancel_after=cancel_after, grace=grace)
    assert (sdk.state, sdk.reason, sdk.exit_code) == (
        engine.state.value, engine.reason and engine.reason.value, engine.exit_code)
    assert (sdk.cancel_requested, sdk.timed_out, sdk.finished_before_cancel) == (
        engine.cancel_requested, engine.timed_out, engine.finished_before_cancel)
    mine = _plain([_sdk_event(event) for event in sdk.events], sdk.folder)
    theirs = _plain(_folded([_engine_event(event) for event in events]), prepared.cwd)
    if timeless:
        mine, theirs = _timeless(mine), _timeless(theirs)
    assert mine == theirs
    assert sdk.warnings == [event.message for event in events
                            if isinstance(event, T.WarningEvent)]
    if engine.final is None:
        assert sdk.final is None
    else:
        assert _plain(_sdk_event(sdk.final), sdk.folder) == _plain(
            _engine_event(engine.final), prepared.cwd)
    return sdk, engine, events


@windows
@pytest.mark.parametrize("mode, state", [
    ("ok", "done"), ("error", "error"), ("crash", "failed"), ("bad_code", "error"),
    ("noise", "done"), ("limits", "done"), ("stdin_open", "done"), ("artifacts", "done"),
])
def test_the_fake_worker_ends_the_same_through_both(fake_plugin, tmp_path, mode, state):
    sdk, _engine, _events = _both(fake_plugin, tmp_path, mode=mode)
    assert sdk.state == state


@windows
def test_noise_goes_to_worker_log_in_the_fake_host(fake_plugin, tmp_path):
    prepared = _prepare(fake_plugin, tmp_path, mode="noise")
    result = ap.execute(prepared)
    log = Path(prepared.log_path).read_text(encoding="utf-8")
    assert '[stdout] {"type": "telemetry", "value": 1}' in log
    assert "over the 1048576-byte line limit" in log
    assert "fake plugin: stderr line" in log and "--- exit 0 ---" in log
    assert [event["type"] for event in result.events][-1] == "done"


@windows
@pytest.mark.parametrize("final", ["done", "error", "cancelled", "none"])
@pytest.mark.parametrize("exit_code", [0, 2, 130, 1])
def test_every_row_of_the_end_table_through_both(fake_plugin, tmp_path, final, exit_code):
    _both(fake_plugin, tmp_path, mode="combo",
          environ={"FAKE_PLUGIN_FINAL": final, "FAKE_PLUGIN_EXIT": str(exit_code)})


@windows
def test_a_cancel_ends_slow_cancelled_through_both(fake_plugin, tmp_path):
    sdk, _e, _ev = _both(fake_plugin, tmp_path, mode="slow", cancel_after=1.0,
                         timeless=True)
    assert (sdk.state, sdk.exit_code, sdk.cancel_requested) == ("cancelled", 130, True)


@windows
def test_done_after_the_cancel_through_both(fake_plugin, tmp_path):
    sdk, _e, _ev = _both(fake_plugin, tmp_path, mode="done_after_cancel", cancel_after=1.0)
    assert (sdk.state, sdk.finished_before_cancel) == ("done", True)


@windows
def test_past_the_time_limit_through_both(fake_plugin, tmp_path):
    sdk, _e, _ev = _both(fake_plugin, tmp_path, mode="timeout", timeout=1.0,
                         environ={"FAKE_PLUGIN_DELAY": "8"}, timeless=True)
    assert (sdk.state, sdk.reason, sdk.timed_out) == ("failed", "timed_out", True)


@windows
def test_a_hang_is_killed_after_the_grace_through_both(fake_plugin, tmp_path):
    sdk, _e, _ev = _both(fake_plugin, tmp_path, mode="hang", timeout=1.0, grace=2.0)
    assert (sdk.state, sdk.reason) == ("failed", "timed_out")
    sdk, _e, _ev = _both(fake_plugin, tmp_path, mode="hang", cancel_after=1.0, grace=2.0)
    assert (sdk.state, sdk.cancel_requested) == ("cancelled", True)
    assert "--- stopped by the host ---" in Path(
        sdk.folder, "worker.log").read_text(encoding="utf-8")


def _alive(pid):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259
    finally:
        kernel32.CloseHandle(handle)


@windows
def test_a_grandchild_ends_with_the_job_in_the_fake_host_too(fake_plugin, tmp_path):
    pid_file = tmp_path / "child.pid"
    sdk, _e, _ev = _both(fake_plugin, tmp_path, mode="grandchild",
                         environ={"FAKE_PLUGIN_CHILD_PID": str(pid_file)})
    assert sdk.state == "done"
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 30
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


@windows
def test_the_artifacts_are_dropped_as_plugin_jobs_drops_them(fake_plugin, tmp_path):
    sdk, _engine, _events = _both(fake_plugin, tmp_path, mode="artifacts")
    # The SDK's checks of its own out_dir against PluginJobs.CheckArtifact
    # of the same files.
    expected = []
    sdk_out = os.path.join(sdk.folder, "out")
    for item in sdk.final["artifacts"]:
        why = J.CheckArtifact(sdk_out, T.Artifact(item["path"], T.ArtifactKind(item["kind"])))[1]
        if why is not None:
            expected.append((item["path"], why.value))
    assert sdk.rejected == expected
    assert {why for _path, why in sdk.rejected} >= {"outside_out_dir", "missing", "hard_link",
                                                     "bad_extension", "too_large"}


@windows
def test_the_request_and_environment_through_both(fake_plugin, tmp_path):
    sdk, engine, events = _both(fake_plugin, tmp_path, mode="echo",
                                secrets={"token": "s3cr3t"}, settings={"style": "long"})
    echo = json.loads(Path(sdk.folder, "out", "echo.json").read_text(encoding="utf-8"))
    request = echo["request"]
    fields = CONTRACT["request"]["fields"]
    assert list(request) == list(fields)
    assert request["settings"] == {"style": "long"} and request["params"] == {"mode": "echo"}
    assert request["trigger"] == {"kind": "manual"}
    assert "s3cr3t" not in Path(sdk.folder, "request.json").read_text(encoding="utf-8")
    env = {key.upper(): value for key, value in echo["env"].items()}
    assert env["AUTONA_SECRET_TOKEN"] == "s3cr3t" and env["AUTONA_PROTOCOL"] == "1"
    assert env["AUTONA_PLUGIN_DIR"] == str(fake_plugin)
    assert echo["cwd"] == sdk.folder
    request_bytes = Path(sdk.folder, "request.json").read_bytes()
    assert request_bytes.isascii() and not request_bytes.startswith(b"\xef\xbb\xbf")


@windows
def test_an_action_fills_its_input_and_the_log_at_the_trigger(fake_plugin, tmp_path):
    image = ROOT / "docs" / "plugins" / "inputs" / "result_image.png"
    prepared = _prepare(fake_plugin, tmp_path, job="light", mode="echo",
                        trigger="action:result_image", inputs={"image": str(image)})
    result = ap.execute(prepared)
    assert result.state == "done"
    request = prepared.request
    import hashlib
    assert request["trigger"] == {"kind": "action", "target": "result_image"}
    assert request["inputs"]["image"]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert request["inputs"]["image"]["path"].endswith(os.path.join("inputs", "image.png"))
    assert request["inputs"]["log"]["kind"] == "match_log"
    assert Path(request["inputs"]["log"]["path"]).read_bytes() == b""
    hook = _prepare(fake_plugin, tmp_path, job="light", trigger="hook:result_image_saved",
                    inputs={"image": str(image)})
    assert hook.timeout == ap.HOOK_TIMEOUT_CAP_SECONDS
    assert hook.request["trigger"] == {"kind": "hook", "event": "result_image_saved",
                                       "hook": "light/result_image_saved"}


@pytest.mark.parametrize("options, reason", [
    ({"job": "nope"}, "invalid_request"),
    ({"job": "light", "trigger": "action:result_image"}, "invalid_request"),
    ({"job": "light", "trigger": "action:thumbnail_image"}, "invalid_request"),
    ({"job": "post", "trigger": "hook:result_image_saved"}, "job_unusable"),
    ({"params": {"unknown": 1}}, "invalid_request"),
    ({"settings": {"token": "x"}}, "invalid_request"),
    ({"secrets": {"style": "x"}}, "invalid_request"),
])
def test_what_autona_would_not_start_is_refused(fake_plugin, tmp_path, options, reason):
    options = dict(options)
    job = options.pop("job", "post")
    params = options.pop("params", {})
    with pytest.raises(ap.Refused) as refused:
        ap.prepare_run(str(fake_plugin), job, params=params, environ=_path_with_python(),
                       work=str(tmp_path / "work"), **options)
    assert refused.value.reason == reason


def test_a_required_secret_missing_needs_settings(fake_plugin, tmp_path):
    manifest = json.loads(json.dumps(FAKE_MANIFEST))
    manifest["settings"][0]["required"] = True
    (fake_plugin / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ap.Refused) as refused:
        ap.prepare_run(str(fake_plugin), "post", environ=_path_with_python(),
                       work=str(tmp_path / "work"))
    assert (refused.value.reason, refused.value.detail) == ("needs_settings", "token")


@windows
def test_a_missing_entry_is_entry_missing_in_both(fake_plugin, tmp_path):
    prepared = _prepare(fake_plugin, tmp_path)
    prepared.argv = [str(fake_plugin / "bin" / "missing.exe"), "run", "x"]
    result = ap.execute(prepared)
    engine, _events = _engine_run(prepared)
    assert (result.state, result.reason) == ("failed", "entry_missing") == (
        engine.state.value, engine.reason.value)


# ── describe against PluginRunner.Describe and PluginRegistry.JudgeDescribe ──

@windows
@pytest.mark.parametrize("mode", ["ok", "noisy", "no_line", "not_json", "exit_1", "wrong_id",
                                  "wrong_version", "no_protocol", "protocol_2"])
def test_describe_is_judged_as_autona_judges_it(fake_plugin, mode):
    env = dict(_path_with_python(), FAKE_PLUGIN_DESCRIBE=mode,
               AUTONA_PLUGIN_DIR="C:\\elsewhere")
    entry = [sys.executable, str(fake_plugin / "worker.py")]
    runner = R.PluginRunner(poll=0.02, describeTimeout=COOPERATIVE, environ=lambda: env)
    run = runner.Describe(str(fake_plugin), entry, dev=True)
    facts = ap.describe_facts(str(fake_plugin), entry, True, env, COOPERATIVE)
    assert (facts["launched"], facts["entry_missing"], facts["timed_out"], facts["exit_code"],
            facts["stdout_lines"], tuple(facts["objects"])) == (
        run.launched, run.entry_missing, run.timed_out, run.exit_code, run.stdout_lines,
        run.objects)
    registry = PluginRegistry(str(fake_plugin), str(fake_plugin), HOST)
    manifest = registry.Check(FAKE_MANIFEST, str(fake_plugin), None, dev=True,
                              hostVersion=HOST).manifest
    judged = registry.JudgeDescribe(run, manifest)
    sdk = ap.judge_describe(facts, FAKE_MANIFEST)
    assert (sdk["failure"], sdk["protocol"], sdk["detail"]) == (
        judged.failure and judged.failure.value, judged.protocol, judged.detail)


@windows
def test_describe_past_its_time_and_of_a_missing_entry(fake_plugin):
    env = dict(_path_with_python(), FAKE_PLUGIN_DESCRIBE="hang")
    entry = [sys.executable, str(fake_plugin / "worker.py")]
    facts = ap.describe_facts(str(fake_plugin), entry, True, env, 0.5)
    assert (facts["launched"], facts["timed_out"], facts["exit_code"]) == (True, True, None)
    assert ap.judge_describe(facts, FAKE_MANIFEST)["failure"] == "timed_out"
    facts = ap.describe_facts(str(fake_plugin), ["bin/missing.exe"], False)
    assert (facts["launched"], facts["entry_missing"]) == (False, True)
    assert R.PluginRunner().Describe(str(fake_plugin), ["bin/missing.exe"], dev=False) == \
        T.DescribeRun(launched=False, entry_missing=True)


# ── check <folder> ──────────────────────────────────────────────────────

@windows
def test_check_passes_a_good_dev_folder_and_tries_a_non_ascii_one(fake_plugin):
    report = ap.check_folder(str(fake_plugin), dev=True, host_version=HOST,
                             environ=_path_with_python(), describe_timeout=COOPERATIVE)
    assert report["problems"] == [] and report["ok"], report
    assert report["describe"]["failure"] is None and report["describe"]["protocol"] == 1
    assert report["non_ascii"]["failure"] is None
    assert not report["non_ascii"]["folder"].isascii()
    assert report["longest_path"]["path"] == "plugins\\example.fake\\plugin.json"


def test_check_reports_an_installed_folder_with_a_script_entry(fake_plugin):
    report = ap.check_folder(str(fake_plugin), environ=_path_with_python())
    assert ("entry_missing", "plugin") in {(p["code"], p["scope"]) for p in report["problems"]}
    assert report["describe"] is None and not report["ok"]


def test_check_notes_long_paths_and_non_ascii_names(tmp_path):
    folder = tmp_path / "example.long"
    deep = folder / ("d" * 60) / ("e" * 40)
    deep.mkdir(parents=True)
    (deep / "x.bin").write_bytes(b"")
    (folder / "名前.txt").write_bytes(b"")
    measured = ap.longest_path(str(folder), "example.long")
    assert measured["length"] == len("plugins\\example.long\\") + 60 + 1 + 40 + 1 + 5
    assert measured["autona_dir_room"] == ap.INSTALLED_PATH_MAX_LENGTH - measured["length"] - 1
    assert ap.non_ascii_names(str(folder)) == ["名前.txt"]


# ── a worker written with the SDK's helpers ─────────────────────────────

SDK_WORKER = '''\
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autona_plugin as ap  # noqa: E402


def ok(request):
    ap.stage("prepare", {"ja": "準備", "en": "Prepare"}, 1, 2)
    ap.progress(0.5, 0.25, 1.5, stage="prepare")
    ap.log({"ja": "準備中 ü", "en": "Preparing"})
    ap.warning("画像を縮小しました")
    ap.stage("send", {"ja": "送信", "en": "Send"}, 2, 2)
    with open(request.out_path("message.txt"), "w", encoding="utf-8") as file:
        file.write(request.text({"ja": "送った", "en": "sent"}))
    print("to worker.log", file=sys.stderr)
    return ap.Done({"ja": "送った", "en": "Posted"},
                   [ap.artifact("message.txt", "text", {"ja": "本文", "en": "Message"})])


def fail(request):
    ap.stage("send")
    raise ap.PluginError("unsupported_host", {"ja": "送れません", "en": "Cannot post"},
                         {"ja": "URL を確かめて", "en": "Check the URL"})


def slow(request):
    ap.stage("wait")
    while True:
        ap.check_cancel()
        ap.progress(overall=0.5)
        ap.wait_cancel(0.05)


def boom(request):
    ap.stage("boom")
    raise RuntimeError("a bug")


def secret(request):
    return "secret set" if request.secret("token") == "s3cr3t" else "secret missing"


def leak(request):
    token = request.secret("token")
    ap.log("token " + token)
    raise RuntimeError("failed with " + token)


if __name__ == "__main__":
    ap.main({"post": lambda r: globals()[r.params["mode"]](r)})
'''


@pytest.fixture
def sdk_plugin(fake_plugin):
    (fake_plugin / "worker.py").write_text(SDK_WORKER, encoding="utf-8")
    shutil.copyfile(SDK_FILE, fake_plugin / "autona_plugin.py")
    return fake_plugin


@windows
@pytest.mark.parametrize("mode, state, cancel_after", [
    ("ok", "done", None), ("fail", "error", None), ("slow", "cancelled", 1.0),
    ("boom", "failed", None),
])
def test_a_worker_written_with_the_sdk_runs_in_the_real_engine(sdk_plugin, tmp_path, mode,
                                                                state, cancel_after):
    sdk, engine, events = _both(sdk_plugin, tmp_path, mode=mode, cancel_after=cancel_after,
                                timeless=True)
    assert engine.state.value == state
    if mode == "ok":
        assert [event.type.value for event in events] == [
            "stage", "progress", "log", "warning", "stage", "done"]
        assert events[2].message == {"ja": "準備中 ü", "en": "Preparing"}
        assert engine.final.artifacts == (T.Artifact("message.txt", T.ArtifactKind.TEXT,
                                                     {"ja": "本文", "en": "Message"}),)
        assert sdk.rejected == []
        assert Path(sdk.folder, "out", "message.txt").read_text(encoding="utf-8") == "送った"
        assert "to worker.log" in Path(sdk.folder, "worker.log").read_text(encoding="utf-8")
    if mode == "fail":
        assert engine.final.code == "unsupported_host" and engine.exit_code == 2
    if mode == "slow":
        assert engine.exit_code == 130
    if mode == "boom":
        assert engine.exit_code == 1
        assert "RuntimeError: a bug" in Path(sdk.folder, "worker.log").read_text(
            encoding="utf-8")


@windows
def test_an_sdk_workers_secret_and_describe(sdk_plugin, tmp_path):
    sdk, engine, _events = _both(sdk_plugin, tmp_path, mode="secret",
                                 secrets={"token": "s3cr3t"})
    assert engine.final.summary == "secret set"
    runner = R.PluginRunner(poll=0.02, describeTimeout=COOPERATIVE,
                            environ=_path_with_python)
    run = runner.Describe(str(sdk_plugin), [sys.executable, "worker.py"], dev=True)
    registry = PluginRegistry(str(sdk_plugin), str(sdk_plugin), HOST)
    manifest = registry.Check(FAKE_MANIFEST, str(sdk_plugin), None, dev=True,
                              hostVersion=HOST).manifest
    judged = registry.JudgeDescribe(run, manifest)
    assert (judged.failure, judged.protocol) == (None, 1)
    assert run.objects == ({"type": "describe", "protocol": [1], "id": "example.fake",
                            "version": "1.2.3"},)
    report = ap.check_folder(str(sdk_plugin), dev=True, environ=_path_with_python(),
                             describe_timeout=COOPERATIVE)
    assert report["ok"], report


@windows
def test_an_sdk_worker_masks_its_secret(sdk_plugin, tmp_path):
    secret = "hunter2-very-secret"
    sdk, engine, events = _both(sdk_plugin, tmp_path, mode="leak", secrets={"token": secret})
    assert (engine.state.value, engine.exit_code) == ("failed", 1)
    assert [event.message for event in events if isinstance(event, T.LogEvent)] == ["token ***"]
    log = Path(sdk.folder, "worker.log").read_text(encoding="utf-8")
    assert "RuntimeError: failed with ***" in log and secret not in log


# ── the command line ────────────────────────────────────────────────────

def _cli(args, cwd):
    return subprocess.run([sys.executable, str(SDK_FILE), *args], cwd=cwd,
                          env=_path_with_python(), capture_output=True, timeout=LONG,
                          encoding="utf-8", errors="replace")


@windows
def test_the_command_line_runs_checks_and_refuses(fake_plugin, tmp_path):
    params = tmp_path / "p.json"
    params.write_text(json.dumps({"mode": "error"}), encoding="utf-8")
    work = str(tmp_path / NON_ASCII / "cli")
    done = _cli(["run", str(fake_plugin), "post", "--json", "--work", work], tmp_path)
    assert done.returncode == 0, done.stderr
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    assert lines[-1]["end"]["state"] == "done"
    assert [line["type"] for line in lines[:-1]][-1] == "done"
    failed = _cli(["run", str(fake_plugin), "post", "--params", str(params), "--work", work],
                  tmp_path)
    assert failed.returncode == 1 and "end: error" in failed.stdout
    assert "unsupported_host" in failed.stdout
    refused = _cli(["run", str(fake_plugin), "nope", "--work", work], tmp_path)
    assert refused.returncode == 2 and "refused: invalid_request" in refused.stderr
    check = _cli(["check", str(fake_plugin), "--dev", "--json"], tmp_path)
    assert check.returncode == 0, check.stdout
    assert json.loads(check.stdout)["ok"] is True
    installed = _cli(["check", str(fake_plugin)], tmp_path)
    assert installed.returncode == 1 and "entry_missing" in installed.stdout


# ── the private copies against Autona's own ─────────────────────────────

def test_the_private_copies_are_the_registrys_and_the_schemas():
    from src.core import PluginRegistry as G
    assert ap._TOP_KEYS == G._TOP_KEYS
    assert (ap._FILES_SCOPES, ap._API_USES) == (G._FILES_SCOPES, G._API_USES)
    for name in ("_ENTRY_HEAD", "_HOST_NAME", "_HTTPS_URL"):
        assert getattr(ap, name).pattern == getattr(G, name).pattern, name
    schema = json.loads((ROOT / "docs" / "plugins" / "plugin.schema.json")
                        .read_text(encoding="utf-8"))
    assert set(schema["properties"]) == ap._TOP_KEYS
    permissions = schema["$defs"]["permissions"]["properties"]
    assert list(ap._FILES_SCOPES) == permissions["files"]["enum"]
    assert list(ap._API_USES) == permissions["autona_api"]["enum"]
    assert ap._HOST_NAME.pattern == permissions["network"]["items"]["pattern"]
    assert ap.PATH_ROOM_MIN == 40


def test_the_run_argv_is_plugin_jobs(tmp_path):
    folder = tmp_path / NON_ASCII / "p"
    (folder / "bin").mkdir(parents=True)
    (folder / "main.py").write_text("", encoding="utf-8")
    (folder / "bin" / "w.exe").write_bytes(b"MZ")
    absolute = os.path.abspath(os.sep + "tools")
    for entry, dev in [(["bin/w.exe", "-x"], False), (["bin\\w.exe", "main.py"], False),
                       (["bin/../../x.exe"], False), ([absolute], False),
                       (["python", "main.py", "--flag"], True), (["main.py"], True),
                       (["python", "missing.py"], True), ([absolute, "main.py"], True),
                       (["bin/../../x.exe"], True)]:
        engine = J.PluginJobs._Argv(str(folder), entry, dev)
        sdk = ap._run_argv(str(folder), entry, dev)
        assert (None if sdk is None else tuple(sdk)) == engine, entry


def test_worker_log_stops_at_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "WORKER_LOG_MAX_BYTES", 300)
    log = ap._WorkerLog(str(tmp_path / "worker.log"))
    for index in range(50):
        log.write(f"line {index} " + "x" * 20)
    log.close()
    log.write("after the close")
    data = (tmp_path / "worker.log").read_bytes()
    assert len(data) <= 300 and data.endswith(log.note.encode("utf-8"))
    assert data.count(b"reached") == 1 and b"after the close" not in data


# ── the values against PluginSettings / PluginSecrets ───────────────────

VALUE_FIELDS = [
    {"key": "t", "type": "text", "label": "t"},
    {"key": "ta", "type": "textarea", "label": "ta", "max_length": 5},
    {"key": "n", "type": "number", "label": "n", "min": 1, "max": 10, "step": 0.5},
    {"key": "m", "type": "number", "label": "m"},
    {"key": "b", "type": "bool", "label": "b"},
    {"key": "s", "type": "select", "label": "s",
     "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
    {"key": "f", "type": "file", "label": "f", "extensions": ["png", "txt"]},
    {"key": "d", "type": "folder", "label": "d"},
    {"key": "k", "type": "secret", "label": "k"},
]


def test_a_value_is_checked_as_plugin_settings_checks_it(tmp_path):
    from src.core.PluginSettings import CheckFieldValue, _Empty
    manifest = _example()
    manifest["settings"] = VALUE_FIELDS
    folder = _registry_plugin(tmp_path / NON_ASCII, manifest)
    report = PluginRegistry(str(tmp_path / "app"), str(tmp_path / "ud"), HOST).Check(
        manifest, str(folder), None, dev=True, hostVersion=HOST)
    fields = {field.key: field for field in report.manifest.settings}
    (tmp_path / "real.png").write_bytes(b"")
    (tmp_path / "real.TXT").write_bytes(b"")
    paths = [str(tmp_path / "real.png"), str(tmp_path / "real.TXT"),
             str(tmp_path / "missing.png"), str(tmp_path), "real.png",
             "\\\\server\\share\\x.png", "//server/x.png", str(tmp_path / "a\x00.png")]
    values = ["", "x", "x" * 201, "x" * 6, 0, 1, 1.5, 1.25, 10, 10.5, -1, float("nan"),
              float("inf"), 10 ** 400, True, False, "a", "c", [], {}, *paths]
    for raw in VALUE_FIELDS:
        field = fields[raw["key"]]
        for value in values:
            for stored in (False, True):
                engine = CheckFieldValue(field, value, resolvePick=lambda _kind, pick: pick,
                                         stored=stored)
                assert ap.check_field_value(raw, value, stored=stored) == engine, (
                    raw["key"], value, stored)
        for value in ("", None, 0, False, "x"):
            assert ap.is_empty_value(raw, value) == _Empty(field, value), (raw["key"], value)


def test_a_secret_value_is_checked_as_plugin_secrets_checks_it():
    from src.core.PluginSecrets import CheckValue, SecretValueError
    for value in ("", "a", "a" * 4096, "a" * 4097, "a\nb", "a\tb", "a\x85b", "ü ok", 5, None):
        try:
            CheckValue(value)
            expected = None
        except SecretValueError as problem:
            expected = str(problem)
        assert ap.check_secret_value(value) == expected, value


# ── names a zip could not hold (Autona's ZipSafety.NameProblem) ─────────

@pytest.mark.parametrize("name, code", [
    ("a/b.txt", None), ("a\\b.txt", None), ("dir/", None), ("console.txt", None),
    ("/abs", "unsafe_path"), ("C:/x", "unsafe_path"), ("a/../b", "unsafe_path"),
    ("file:stream", "unsafe_path"),
    ("", "bad_name"), ("a//b", "bad_name"), ("./a", "bad_name"), ("a<b", "bad_name"),
    ("tab\there", "bad_name"), ("trail.", "bad_name"), ("trail ", "bad_name"),
    ("LONGFO~1.txt", "bad_name"),
    ("CON", "reserved_name"), ("nul.txt", "reserved_name"), ("dir/com1.log", "reserved_name"),
    ("COM0", "reserved_name"), ("LPT¹", "reserved_name"), ("conin$", "reserved_name"),
    ("名前.txt", "not_ascii"),
])
def test_a_zip_name_is_judged_as_zip_safety_judges_it(name, code):
    assert ap.zip_name_problem(name) == code
    if code == "not_ascii":
        assert ap.zip_name_problem(name, ascii_only=False) is None


# ── check <folder>: an installed folder against a dev one ───────────────

def _installed_folder(tmp_path, plugin_id="example.fake", manifest=None):
    manifest = dict(json.loads(json.dumps(manifest or FAKE_MANIFEST)), id=plugin_id,
                    entry=["bin/w.exe"])
    folder = tmp_path / NON_ASCII / plugin_id
    (folder / "bin").mkdir(parents=True)
    (folder / "bin" / "w.exe").write_bytes(b"MZ")
    (folder / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                        encoding="utf-8")
    return folder


@pytest.fixture
def described(monkeypatch):
    """describe answered without starting anything; the folders it was
    asked in (the copy's names too)."""
    seen = []

    def facts(folder, entry, dev, environ=None, timeout=None, poll=0.05):
        seen.append((folder, sorted(os.listdir(folder))))
        manifest = json.loads(Path(folder, "plugin.json").read_text(encoding="utf-8"))
        return {"launched": True, "entry_missing": False, "timed_out": False, "exit_code": 0,
                "stdout_lines": 1, "seconds": 0.01,
                "objects": [{"type": "describe", "protocol": [1], "id": manifest["id"],
                             "version": manifest["version"]}]}

    monkeypatch.setattr(ap, "describe_facts", facts)
    return seen


def _check(folder, **options):
    return ap.check_folder(str(folder), host_version=HOST, environ={}, **options)


def test_an_installed_check_fails_on_names_a_dev_one_notes_them(tmp_path, described):
    folder = _installed_folder(tmp_path)
    assert _check(folder)["ok"] and _check(folder, dev=True)["ok"]
    (folder / "名前.txt").write_bytes(b"")
    (folder / "a~1.txt").write_bytes(b"")
    (folder / ".venv" / "名前").mkdir(parents=True)
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x~1.pyc").write_bytes(b"")
    installed, dev = _check(folder), _check(folder, dev=True)
    for report in (installed, dev):
        assert report["non_ascii_names"] == ["名前.txt"]
        assert report["bad_names"] == [{"path": "a~1.txt", "code": "bad_name"}]
        assert any("名前.txt" in note for note in report["notes"])
        assert any("a~1.txt" in note for note in report["notes"])
    assert not installed["ok"] and dev["ok"]


def test_an_installed_check_fails_on_a_long_path_a_dev_one_notes_it(tmp_path, described,
                                                                      monkeypatch):
    monkeypatch.setattr(ap, "INSTALLED_PATH_MAX_LENGTH", 90)
    folder = _installed_folder(tmp_path)
    (folder / ("d" * 30)).mkdir()
    (folder / ("d" * 30) / "x.bin").write_bytes(b"")
    installed, dev = _check(folder), _check(folder, dev=True)
    assert installed["longest_path"]["autona_dir_room"] == 90 - (21 + 30 + 1 + 5) - 1 < 40
    assert not installed["ok"] and dev["ok"]
    assert any("user_data" in note for note in dev["notes"])


def test_a_reserved_id_is_noted_unless_installed_by_hand(tmp_path, described):
    folder = _installed_folder(tmp_path, "taguyan.fake")
    for report in (_check(folder), _check(folder, dev=True),
                   _check(folder, dev=True, source="manual")):
        assert report["ok"] and report["problems"] == []
        assert any("taguyan." in note for note in report["notes"])
    manual = _check(folder, source="manual")
    assert ("id_reserved", "plugin") in {(p["code"], p["scope"]) for p in manual["problems"]}
    assert not manual["ok"]


def test_a_failed_copy_is_a_note_and_a_dev_copy_leaves_dot_folders(tmp_path, described,
                                                                   monkeypatch):
    manifest = json.loads(json.dumps(FAKE_MANIFEST))
    folder = _installed_folder(tmp_path, manifest=manifest)
    manifest["entry"] = [".tools/w.exe"]
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / ".tools").mkdir()
    (folder / ".tools" / "w.exe").write_bytes(b"MZ")
    (folder / ".git").mkdir()
    (folder / ".git" / "HEAD").write_bytes(b"")
    described.clear()
    report = _check(folder, dev=True)
    assert report["ok"] and report["non_ascii"]["failure"] is None
    copy_names = described[1][1]
    assert ".tools" in copy_names and ".git" not in copy_names and "bin" in copy_names

    def broken(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(ap.shutil, "copytree", broken)
    report = _check(folder, dev=True)
    assert report["ok"] and report["non_ascii"] is None
    assert any("not tried" in note and "disk full" in note for note in report["notes"])


# ── run <folder> <job>: values, secrets, the job folder ─────────────────

@pytest.mark.parametrize("options", [
    {"params": {"mode": 5}}, {"params": {"mode": "x" * 201}},
    {"settings": {"style": "huge"}}, {"secrets": {"token": "a\nb"}},
    {"secrets": {"token": ""}}, {"secrets": {"token": "x" * 4097}},
], ids=["not a string", "too long", "not an option", "a line break", "empty", "too long secret"])
def test_a_value_autona_would_refuse_is_refused(fake_plugin, tmp_path, options):
    options = dict(options)
    with pytest.raises(ap.Refused) as refused:
        ap.prepare_run(str(fake_plugin), "post", params=options.pop("params", None),
                       environ=_path_with_python(), work=str(tmp_path / "work"), **options)
    assert refused.value.reason == "invalid_request"


def test_a_failed_preparation_leaves_no_job_folder(fake_plugin, tmp_path):
    work = tmp_path / "work"
    with pytest.raises(OSError):
        ap.prepare_run(str(fake_plugin), "light", trigger="action:result_image",
                       inputs={"image": str(tmp_path / "missing.png")},
                       environ=_path_with_python(), work=str(work))
    assert [path.name for path in work.iterdir()] == ["data"]


# ── the run's loop: a slow on_event, a failing one, an interrupt ────────

SPAM = '''\
import sys
line = '{"type": "progress", "overall": 0.5}\\n' * 200
while True:
    sys.stdout.write(line)
    sys.stdout.flush()
'''


@windows
@pytest.mark.parametrize("how", ["timeout", "cancel"])
def test_a_slow_on_event_does_not_hold_up_the_time_limit_or_the_cancel(tmp_path, how):
    folder = tmp_path / NON_ASCII
    folder.mkdir()
    (folder / "spam.py").write_text(SPAM, encoding="utf-8")
    prepared = ap.Prepared([sys.executable, str(folder / "spam.py")], str(folder),
                           dict(os.environ), "heavy", 1.0 if how == "timeout" else None, None,
                           str(folder / "worker.log"))
    seen = [0]

    def slow(_event):
        seen[0] += 1
        time.sleep(0.001)

    started = time.monotonic()
    result = ap.execute(prepared, cancel_after=1.0 if how == "cancel" else None, grace=1.0,
                        on_event=slow, poll=0.02)
    assert time.monotonic() - started < 15
    assert seen[0] > 0
    if how == "timeout":
        assert (result.state, result.reason, result.timed_out) == ("failed", "timed_out", True)
    else:
        assert (result.state, result.cancel_requested) == ("cancelled", True)
    assert [event["type"] for event in result.events] == ["progress"]


def test_the_kept_events_fold_progress_and_stop_at_the_cap(monkeypatch):
    monkeypatch.setattr(ap, "RESULT_EVENTS_MAX", 3)
    result = ap.RunResult()
    kinds = ["progress", "progress", "log", "progress", "progress", "warning", "log", "done"]
    for index, kind in enumerate(kinds):
        result.keep({"type": kind, "n": index})
    assert [(event["type"], event["n"]) for event in result.events] == [
        ("progress", 1), ("log", 2), ("progress", 4)]
    assert result.events_dropped == 3 and result.as_dict()["events_dropped"] == 3


class _Stop(BaseException):
    pass


@windows
def test_a_failing_on_event_is_logged_and_an_interrupt_ends_the_worker(fake_plugin, tmp_path):
    def failing(_event):
        raise RuntimeError("the host's bug")

    prepared = _prepare(fake_plugin, tmp_path)
    result = ap.execute(prepared, on_event=failing)
    assert result.state == "done"
    log = Path(prepared.log_path).read_text(encoding="utf-8")
    assert log.count("on_event raised RuntimeError: the host's bug") == 1

    def interrupt(_event):
        raise _Stop()

    prepared = _prepare(fake_plugin, tmp_path, mode="slow")
    with pytest.raises(_Stop):
        ap.execute(prepared, on_event=interrupt)
    # The worker (its cwd is the job folder) and worker.log are gone.
    assert "--- exit" in Path(prepared.log_path).read_text(encoding="utf-8")
    shutil.rmtree(prepared.folder)


# ── the worker's helpers in this process ────────────────────────────────

def test_the_worker_api_and_its_per_run_state(tmp_path, monkeypatch, capsys):
    assert all(hasattr(ap, name) for name in ap.__all__)
    assert not {"done", "error", "cancelled"} & set(dir(ap))
    assert not hasattr(ap.Request, "cancelled") and not hasattr(ap.Request, "check_cancel")
    (tmp_path / "plugin.json").write_text(json.dumps({"id": "a.b", "version": "1.0.0"}),
                                          encoding="utf-8")
    monkeypatch.setenv("AUTONA_PLUGIN_DIR", str(tmp_path))
    ap._terminal["type"] = "done"
    ap._cancel.set()
    assert ap.run_worker({}, ["describe"]) == 0
    assert ap._terminal["type"] is None and not ap.cancel_requested()
    assert json.loads(capsys.readouterr().out) == {"type": "describe", "protocol": [1],
                                                   "id": "a.b", "version": "1.0.0"}


def test_nan_and_infinity_never_reach_an_event(capsys):
    ap.progress(float("nan"), float("inf"), 2.5)
    ap.emit({"type": "log", "message": {"en": "x"}, "extra": [float("-inf")]})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [{"type": "progress", "eta": 2.5},
                     {"type": "log", "message": {"en": "x"}, "extra": [None]}]


def test_a_secrets_value_is_masked_in_the_events(monkeypatch, capsys):
    monkeypatch.setenv("AUTONA_SECRET_TOKEN", "hunter2-long")
    monkeypatch.setenv("AUTONA_SECRET_SHORT", "short")
    ap.log({"en": "token hunter2-long, short"})
    assert json.loads(capsys.readouterr().out)["message"] == {"en": "token ***, short"}
    assert ap._mask("a hunter2-long b") == "a *** b"


@windows
def test_a_junction_is_a_link_without_isjunction(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    for link in (tmp_path / "link", out / "j"):
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                       capture_output=True)
    assert ap._is_link(str(tmp_path / "link")) and not ap._is_link(str(target))
    assert ap.check_artifact(str(out), "j/a.txt", "text") == (None, "symlink_or_junction")
    monkeypatch.delattr(os.path, "isjunction", raising=False)
    assert ap._is_link(str(tmp_path / "link")) and not ap._is_link(str(target))
    assert ap.check_artifact(str(out), "j/a.txt", "text") == (None, "symlink_or_junction")
