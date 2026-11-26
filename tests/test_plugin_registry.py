"""PluginRegistry (ADR-009 lane L2): the manifest's check, the B.4 fill
rules, the scan (installed, built-in, dev folders and their ids), the list's
state and the describe judgement. Everything in tmp_path; the injected
callables stand in for the other lanes."""

import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.core import ArchiveWorker
from src.core import PluginTypes as T
from src.core.PluginRegistry import (BUILTIN_ID, BUILTIN_VODSPLIT, DEV_ENV,
                                     DevFolderId, PluginRegistry, PluginUsable,
                                     ReadManifest, UsableJobs)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "docs" / "plugins" / "contract_1.json")
                      .read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "docs" / "plugins" / "examples"
                      / "webhook.plugin.json").read_text(encoding="utf-8"))
CHECKS = {row["code"]: row for row in CONTRACT["manifest_checks"]["codes"]}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
OFFICIAL = T.InstallSource("github", repo_id=1, name="yuya-tagu/autona-webhook")
HOST = "9.0.0"


def _example():
    return copy.deepcopy(EXAMPLE)


def _job(manifest, jobId):
    return next(job for job in manifest["jobs"] if job["id"] == jobId)


def _plugin(parent, manifest, name=None, exe=True, icon=True):
    """A plugin folder holding ``manifest`` (and its entry and icon)."""
    pluginId = manifest.get("id")
    folder = Path(parent) / (name or (pluginId if isinstance(pluginId, str)
                                      else "taguyan.webhook"))
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    entry = manifest.get("entry")
    if exe and isinstance(entry, list) and entry and isinstance(entry[0], str) \
            and not entry[0].startswith("@") and ".." not in entry[0] \
            and ":" not in entry[0]:
        target = folder / entry[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"MZ")
    if icon and isinstance(manifest.get("icon"), str) \
            and manifest["icon"].endswith(".png") and ".." not in manifest["icon"]:
        (folder / manifest["icon"]).write_bytes(PNG)
    return folder


def _registry(tmp_path, **kwargs):
    kwargs.setdefault("officialRepoIds", {1})
    kwargs.setdefault("installSource", lambda _id: OFFICIAL)
    kwargs.setdefault("environ", lambda: {})
    kwargs.setdefault("which", lambda _name: None)
    return PluginRegistry(str(tmp_path / "app"), str(tmp_path / "ud"), HOST,
                          **kwargs)


def _check(tmp_path, manifest, source=OFFICIAL, dev=False, **folder):
    folderPath = _plugin(tmp_path / "app" / "plugins", manifest, **folder)
    return _registry(tmp_path).Check(manifest, str(folderPath), source,
                                     dev=dev, hostVersion=HOST)


def _found(report):
    return {(p.code.value, p.scope.value, p.job) for p in report.problems}


# ── the check ───────────────────────────────────────────────────────────

def test_the_example_manifest_passes(tmp_path):
    report = _check(tmp_path, _example())
    assert report.problems == ()
    manifest = report.manifest
    assert manifest.id == "taguyan.webhook"
    assert [job.id for job in UsableJobs(report)] == ["post_image", "post_match"]
    assert manifest.settings[0].type == "secret"
    assert manifest.jobs[0].actions == (T.Action("result_image",
                                                 manifest.jobs[0].actions[0].label),)
    assert manifest.jobs[1].manual is False


def test_a_bom_and_an_unknown_top_key_are_accepted(tmp_path):
    manifest = _example()
    manifest["future"] = {"x": 1}
    folder = _plugin(tmp_path, manifest)
    (folder / "plugin.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(manifest).encode("utf-8"))
    raw, problem = ReadManifest(str(folder))
    assert problem is None
    report = _registry(tmp_path).Check(raw, str(folder), OFFICIAL, dev=False,
                                       hostVersion=HOST)
    assert _found(report) == {("unknown_key", "warning", None)}
    assert PluginUsable(report)


def _settings(m, item):
    m["settings"].append(item)


# (name, code, job (None: the plugin's / a warning), change)
CASES = [
    # the schema's, as tests/test_plugin_contract.py breaks it
    ("no id", "invalid_field", None, lambda m: m.pop("id")),
    ("id with a capital", "invalid_field", None,
     lambda m: m.update(id="Taguyan.webhook")),
    ("manifest 2", "manifest_version_unknown", None,
     lambda m: m.update(manifest=2)),
    ("manifest a string", "invalid_field", None,
     lambda m: m.update(manifest="1")),
    ("version not X.Y.Z", "invalid_field", None, lambda m: m.update(version="1.0")),
    ("http homepage", "invalid_field", None,
     lambda m: m.update(homepage="http://example.com")),
    ("protocol without 1", "protocol_unsupported", None,
     lambda m: m.update(protocol=[2])),
    ("no windows-x64", "platform_unsupported", None,
     lambda m: m.update(platforms=["linux-x64"])),
    ("@ entry", "entry_reserved", None,
     lambda m: m.update(entry=["@python", "main.py"])),
    ("absolute entry", "invalid_field", None,
     lambda m: m.update(entry=["C:\\bin\\x.exe"])),
    ("entry going up", "invalid_field", None, lambda m: m.update(entry=["../x.exe"])),
    ("no permissions.files", "invalid_field", None,
     lambda m: m["permissions"].pop("files")),
    ("unknown files scope", "invalid_field", None,
     lambda m: m["permissions"].update(files="everything")),
    ("no jobs", "invalid_field", None, lambda m: m.update(jobs=[])),
    ("a job id twice", "invalid_field", None,
     lambda m: _job(m, "post_match").update(id="post_image")),
    ("source type in settings", "source_in_settings", None,
     lambda m: _settings(m, {"key": "img", "type": "result_image", "label": "i"})),
    ("select without options", "invalid_field", None,
     lambda m: m["settings"][1].pop("options")),
    ("file without extensions", "invalid_field", None,
     lambda m: _settings(m, {"key": "f", "type": "file", "label": "f"})),
    ("bad key", "invalid_field", None, lambda m: m["settings"][1].update(key="Mention")),
    ("secret with a default", "invalid_field", None,
     lambda m: m["settings"][0].update(default="https://x")),
    ("light timeout over 300", "timeout_out_of_range", "post_image",
     lambda m: _job(m, "post_image").update(timeout_seconds=301)),
    ("light without memory", "invalid_job_field", "post_image",
     lambda m: _job(m, "post_image").pop("memory_mb")),
    ("light memory under 64", "memory_out_of_range", "post_image",
     lambda m: _job(m, "post_image").update(memory_mb=32)),
    ("hook job memory over 512", "memory_out_of_range", "post_image",
     lambda m: _job(m, "post_image").update(memory_mb=600)),
    ("hook on a heavy job", "trigger_needs_light_lane", "post_match",
     lambda m: _job(m, "post_match").update(lane="heavy")),
    ("context type with manual true", "manual_with_context", "post_match",
     lambda m: _job(m, "post_match").update(manual=True)),
    ("context type without manual", "manual_with_context", "post_match",
     lambda m: _job(m, "post_match").pop("manual")),
    ("secret in params", "secret_outside_settings", "post_image",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "token", "type": "secret", "label": "t"})),
    ("text over the cap", "invalid_job_field", "post_image",
     lambda m: _job(m, "post_image")["params"][1].update(max_length=20001)),
    ("unknown lane", "invalid_job_field", "post_image",
     lambda m: _job(m, "post_image").update(lane="medium")),
    # what only the registry sees
    ("an unknown top-level key", "unknown_key", None, lambda m: m.update(extra=1)),
    ("a newer min_autona", "min_autona_newer", None,
     lambda m: m.update(min_autona="99.0.0")),
    ("a key twice in params", "key_duplicate", "post_image",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "message", "type": "text", "label": "x"})),
    ("a key twice in settings", "key_duplicate", None,
     lambda m: _settings(m, {"key": "mention", "type": "text", "label": "x"})),
    ("unknown field type in params", "field_type_unknown", "post_image",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "colour", "type": "color", "label": "c", "default": "#fff"})),
    ("unknown field type in settings", "field_type_unknown", None,
     lambda m: _settings(m, {"key": "colour", "type": "color", "label": "c"})),
    ("unknown hook event", "hook_event_unknown", "post_match",
     lambda m: _job(m, "post_match")["hooks"].append(
         {"on": "match_closed", "label": "x"})),
    ("unknown action target", "action_target_unknown", "post_image",
     lambda m: _job(m, "post_image")["actions"].append(
         {"target": "stream", "label": "x"})),
    ("a hook twice", "hook_duplicate", "post_match",
     lambda m: _job(m, "post_match")["hooks"].append(
         {"on": "match_decided", "label": "again"})),
    ("two fields of the filled type", "fill_count", "post_image",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "second", "type": "result_image", "label": "s"})),
    ("a required image the trigger cannot fill", "trigger_unfillable",
     "post_image",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "thumb", "type": "thumbnail_image", "label": "t",
          "required": True})),
    ("a required hook value without a default", "hook_param_needs_default",
     "post_match",
     lambda m: _job(m, "post_match")["params"].append(
         {"key": "note", "type": "text", "label": "n", "required": True})),
    ("a select default not among the options in settings", "default_invalid",
     None,
     lambda m: m["settings"][1].update(default="everyone")),
    ("a text default of another type", "default_invalid", "post_image",
     lambda m: _job(m, "post_image")["params"][1].update(default=5)),
    ("a number default out of range", "default_invalid", "post_image",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "n", "type": "number", "label": "n", "min": 0, "max": 3,
          "default": 9})),
    ("a job nobody can start", "job_unreachable", None,
     lambda m: _job(m, "post_match").update(hooks=[])),
]


@pytest.mark.parametrize("name, code, job, change", CASES,
                         ids=[case[0] for case in CASES])
def test_a_broken_manifest_gets_its_code_in_its_scope(tmp_path, name, code,
                                                      job, change):
    manifest = _example()
    change(manifest)
    report = _check(tmp_path, manifest)
    row = CHECKS[code]
    inSettings = "settings" in name
    scope = row.get("settings_scope", row["scope"]) if inSettings else row["scope"]
    assert (code, scope, job) in _found(report), _found(report)
    if scope == "plugin":
        assert not PluginUsable(report)
    elif scope == "job":
        # Only that job is unusable; the plugin and the other job are fine.
        assert PluginUsable(report), _found(report)
        others = [j["id"] for j in manifest["jobs"] if j["id"] != job]
        assert [j.id for j in UsableJobs(report)] == others
    else:
        assert PluginUsable(report) and len(UsableJobs(report)) == 2


def test_every_code_the_registry_owns_is_reached(tmp_path):
    """Every code is really produced: the table's, and the ones that need
    folders the table cannot make."""
    _winapi = pytest.importorskip("_winapi")
    reached = set()

    def run(report):
        reached.update(p.code.value for p in report.problems)

    for index, (_name, _code, _job, change) in enumerate(CASES):
        manifest = _example()
        change(manifest)
        run(_check(tmp_path / f"case{index}", manifest))
    run(T.ManifestReport(None, (ReadManifest(str(tmp_path / "nowhere"))[1],)))
    run(_check(tmp_path / "mismatch", _example(), name="other.name"))
    run(_check(tmp_path / "reserved", _example(), source=T.InstallSource("manual")))
    run(_check(tmp_path / "missing", _example(), exe=False))
    bad = _example()
    bad["icon"] = "icon.jpg"
    run(_check(tmp_path / "icon", bad))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "webhook.exe").write_bytes(b"MZ")
    folder = _plugin(tmp_path / "junction" / "app" / "plugins", _example(), exe=False)
    _winapi.CreateJunction(str(outside), str(folder / "bin"))
    run(_registry(tmp_path / "junction").Check(_example(), str(folder), OFFICIAL,
                                               dev=False, hostVersion=HOST))
    taken = tmp_path / "taken"
    _plugin(_app(taken), _example())
    clash = _dev(taken, "clash", "taguyan.webhook")
    registry = _registry(taken, environ=lambda: {DEV_ENV: str(clash)})
    registry.Scan()
    run(T.ManifestReport(None, registry.DevFolderProblems(DevFolderId(str(clash)))))

    owned = {row["code"] for row in CONTRACT["manifest_checks"]["codes"]
             if row["owner"].startswith("PluginRegistry.")}
    assert reached == owned
    assert owned == {member.value for member in T.ManifestCheck}


@pytest.mark.parametrize("content", [
    None, b"\xff\xfe not utf-8", b"{not json", b"x" * (T.MANIFEST_MAX_BYTES + 1)],
    ids=["missing", "not utf-8", "not json", "too large"])
def test_an_unreadable_manifest(tmp_path, content):
    folder = tmp_path / "a.b"
    folder.mkdir()
    if content is not None:
        (folder / "plugin.json").write_bytes(content)
    raw, problem = ReadManifest(str(folder))
    assert raw is None
    assert (problem.code, problem.scope) == (T.ManifestCheck.MANIFEST_UNREADABLE,
                                             T.ProblemScope.PLUGIN)


def test_a_manifest_that_is_not_an_object_is_unreadable(tmp_path):
    report = _registry(tmp_path).Check([1], str(tmp_path), None, dev=False,
                                       hostVersion=HOST)
    assert report.manifest is None
    assert _found(report) == {("manifest_unreadable", "plugin", None)}


def test_the_id_must_be_its_folders_name_but_in_a_dev_folder(tmp_path):
    report = _check(tmp_path, _example(), name="webhook-src")
    assert ("id_folder_mismatch", "plugin", None) in _found(report)
    folder = tmp_path / "app" / "plugins" / "webhook-src"
    dev = _registry(tmp_path).Check(_example(), str(folder), None, dev=True,
                                    hostVersion=HOST)
    assert dev.problems == ()


@pytest.mark.parametrize("source, dev, reserved", [
    (OFFICIAL, False, False),
    (None, False, False),                      # the built-in
    (T.InstallSource("manual"), False, True),
    (T.InstallSource("github", repo_id=2, name="someone/fork"), False, True),
    (T.InstallSource("dev"), True, False),
])
def test_taguyan_is_reserved_for_the_official_repositories(tmp_path, source,
                                                            dev, reserved):
    report = _check(tmp_path, _example(), source=source, dev=dev)
    assert (("id_reserved", "plugin", None) in _found(report)) is reserved


def test_another_author_needs_no_official_source(tmp_path):
    manifest = _example()
    manifest["id"] = "example.webhook-post"
    report = _check(tmp_path, manifest, source=T.InstallSource("manual"))
    assert report.problems == ()


def test_the_entry_must_exist_and_be_an_exe(tmp_path):
    assert ("entry_missing", "plugin", None) in _found(
        _check(tmp_path / "a", _example(), exe=False))
    manifest = _example()
    manifest["entry"] = ["bin/webhook.cmd"]
    assert ("entry_missing", "plugin", None) in _found(
        _check(tmp_path / "b", manifest))


def test_an_entry_through_a_junction_is_outside(tmp_path):
    _winapi = pytest.importorskip("_winapi")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "webhook.exe").write_bytes(b"MZ")
    folder = _plugin(tmp_path / "app" / "plugins", _example(), exe=False)
    _winapi.CreateJunction(str(outside), str(folder / "bin"))
    report = _registry(tmp_path).Check(_example(), str(folder), OFFICIAL,
                                       dev=False, hostVersion=HOST)
    assert ("entry_outside", "plugin", None) in _found(report)


def test_a_dev_entry_may_be_a_command_on_path(tmp_path):
    manifest = _example()
    manifest["id"] = "me.tool"
    manifest["entry"] = ["python", "main.py"]
    folder = _plugin(tmp_path, manifest, exe=False)
    found = PluginRegistry(str(tmp_path), str(tmp_path / "ud"), HOST,
                           which=lambda name: "C:\\py\\python.exe"
                           if name == "python" else None)
    assert found.Check(manifest, str(folder), None, dev=True,
                       hostVersion=HOST).problems == ()
    missing = PluginRegistry(str(tmp_path), str(tmp_path / "ud"), HOST,
                             which=lambda _name: None)
    assert ("entry_missing", "plugin", None) in _found(
        missing.Check(manifest, str(folder), None, dev=True, hostVersion=HOST))
    # Installed, a command on PATH is no entry.
    assert ("entry_missing", "plugin", None) in _found(
        found.Check(manifest, str(folder), None, dev=False, hostVersion=HOST))


@pytest.mark.parametrize("icon, content", [
    ("icon.png", b"GIF89a not a png"),
    ("icon.png", PNG + b"\0" * T.ICON_MAX_BYTES),
    ("icon.jpg", PNG),
    ("../icon.png", PNG),
    ("missing.png", None),
], ids=["not a png", "too large", "a jpg", "outside", "missing"])
def test_a_bad_icon_is_a_warning_only(tmp_path, icon, content):
    manifest = _example()
    manifest["icon"] = icon
    folder = _plugin(tmp_path / "app" / "plugins", manifest, icon=False)
    if content is not None and ".." not in icon:
        (folder / icon).write_bytes(content)
    report = _registry(tmp_path).Check(manifest, str(folder), OFFICIAL,
                                       dev=False, hostVersion=HOST)
    assert _found(report) == {("icon_invalid", "warning", None)}
    assert PluginUsable(report)


def test_unknown_names_take_down_their_job_only(tmp_path):
    manifest = _example()
    _job(manifest, "post_image")["params"].append(
        {"key": "colour", "type": "color", "label": "c"})
    _job(manifest, "post_match")["hooks"][0]["on"] = "match_closed"
    report = _check(tmp_path, manifest)
    assert PluginUsable(report)
    assert UsableJobs(report) == ()
    # A third job that knows only known names is still usable.
    manifest["jobs"].append({"id": "export", "title": "e", "lane": "heavy",
                             "timeout_seconds": 600,
                             "params": [{"key": "log", "type": "match_log",
                                         "label": "l", "required": True}]})
    report = _check(tmp_path / "again", manifest)
    assert [job.id for job in UsableJobs(report)] == ["export"]


def test_an_unknown_type_in_settings_takes_down_the_plugin(tmp_path):
    manifest = _example()
    manifest["settings"].append({"key": "c", "type": "color", "label": "c"})
    report = _check(tmp_path, manifest)
    assert not PluginUsable(report)
    assert report.manifest is not None and report.manifest.name == EXAMPLE["name"]


# ── B.4: every way a job starts fills its context-only fields ───────────

def _one_job(job):
    manifest = _example()
    manifest["id"] = "example.b4"
    manifest["settings"] = []
    manifest["jobs"] = [dict({"id": "j", "title": "j", "lane": "light",
                              "timeout_seconds": 30, "memory_mb": 128}, **job)]
    return manifest


def _field(key, kind, required=True):
    return {"key": key, "type": kind, "label": key, "required": required}


@pytest.mark.parametrize("name, job, codes", [
    ("an action and a match hook in one job",
     {"manual": False, "params": [_field("image", "result_image"),
                                  _field("match", "match_record")],
      "actions": [{"target": "result_image", "label": "a"}],
      "hooks": [{"on": "match_decided", "label": "h"}]},
     {"trigger_unfillable"}),
    ("a match hook beside a required image",
     {"manual": False, "params": [_field("match", "match_record"),
                                  _field("image", "result_image")],
      "hooks": [{"on": "match_decided", "label": "h"}]},
     {"trigger_unfillable"}),
    ("a match hook beside an optional image",
     {"manual": False, "params": [_field("match", "match_record"),
                                  _field("image", "result_image", False)],
      "hooks": [{"on": "match_decided", "label": "h"}]},
     set()),
    ("a thumbnail action with the state at the trigger",
     {"params": [_field("thumb", "thumbnail_image"), _field("now", "state"),
                 _field("log", "match_log")],
      "actions": [{"target": "thumbnail_image", "label": "a"}]},
     set()),
    ("an archive export action with a match record",
     {"manual": False, "params": [_field("export", "archive_export"),
                                  _field("match", "match_record")],
      "actions": [{"target": "archive_export", "label": "a"}]},
     {"trigger_unfillable"}),
    ("an action without its type",
     {"params": [_field("now", "state")],
      "actions": [{"target": "result_image", "label": "a"}]},
     {"fill_count"}),
    ("a manual job with a context-only field",
     {"params": [_field("match", "match_record")]},
     {"manual_with_context"}),
    ("a manual job of picks",
     {"params": [_field("log", "match_log"), _field("image", "result_image"),
                 _field("thumb", "thumbnail_image", False), _field("now", "state")]},
     set()),
])
def test_every_start_fills_the_context(tmp_path, name, job, codes):
    report = _check(tmp_path, _one_job(job), source=T.InstallSource("manual"))
    assert {p.code.value for p in report.problems} == codes, name
    assert all(p.job == "j" for p in report.problems)


# ── describe ────────────────────────────────────────────────────────────

def _manifest(tmp_path):
    return _check(tmp_path, _example()).manifest


ANSWER = {"type": "describe", "protocol": [1], "id": "taguyan.webhook",
          "version": "1.0.0"}


@pytest.mark.parametrize("run, failure", [
    (T.DescribeRun(launched=False, entry_missing=True), "launch_failed"),
    (T.DescribeRun(launched=True, timed_out=True), "timed_out"),
    (T.DescribeRun(launched=True, exit_code=1, stdout_lines=1,
                   objects=(ANSWER,)), "nonzero_exit"),
    (T.DescribeRun(launched=True, exit_code=0), "no_line"),
    (T.DescribeRun(launched=True, exit_code=0, stdout_lines=2), "not_json"),
    (T.DescribeRun(launched=True, exit_code=0, stdout_lines=1,
                   objects=({"type": "log", "message": "hi"},)),
     "type_not_describe"),
    (T.DescribeRun(launched=True, exit_code=0, stdout_lines=1,
                   objects=(dict(ANSWER, protocol=[]),)), "protocol_missing"),
    (T.DescribeRun(launched=True, exit_code=0, stdout_lines=1,
                   objects=(dict(ANSWER, protocol=["1"]),)), "protocol_missing"),
    (T.DescribeRun(launched=True, exit_code=0, stdout_lines=1,
                   objects=(dict(ANSWER, id="other.id"),)), "id_mismatch"),
    (T.DescribeRun(launched=True, exit_code=0, stdout_lines=1,
                   objects=(dict(ANSWER, version="1.0.1"),)), "version_mismatch"),
    (T.DescribeRun(launched=True, exit_code=0, stdout_lines=1,
                   objects=(dict(ANSWER, protocol=[2, 3]),)), "no_common_protocol"),
])
def test_describe_is_judged_against_the_manifest(tmp_path, run, failure):
    result = _registry(tmp_path).JudgeDescribe(run, _manifest(tmp_path))
    assert result.failure == T.DescribeFailure(failure)
    assert result.protocol is None


def test_a_good_describe_names_the_protocol(tmp_path):
    run = T.DescribeRun(launched=True, exit_code=0, stdout_lines=3, objects=(
        {"type": "log", "message": "starting"}, dict(ANSWER, protocol=[1, 2])))
    result = _registry(tmp_path).JudgeDescribe(run, _manifest(tmp_path))
    assert result.failure is None and result.protocol == 1
    assert result.answer == T.DescribeAnswer((1, 2), "taguyan.webhook", "1.0.0")


# ── the scan ────────────────────────────────────────────────────────────

def _app(tmp_path):
    return tmp_path / "app" / "plugins"


def test_the_scan_finds_the_builtin_and_the_installed(tmp_path):
    plugins = _app(tmp_path)
    _plugin(plugins, _example())
    _plugin(plugins, _example(), name="taguyan.webhook.new")
    _plugin(plugins, _example(), name="taguyan.webhook.old")
    (plugins / "stray.zip.part").write_bytes(b"x")
    other = _example()
    other["id"] = "example.misnamed"
    _plugin(plugins, other, name="example.folder")
    worker = tmp_path / "app" / ArchiveWorker.WORKER_DIR
    worker.mkdir()
    (worker / ArchiveWorker.WORKER_EXECUTABLE).write_bytes(b"M" * 100)

    registry = _registry(tmp_path, builtinVersion=lambda: "0.5.0",
                         installSource=lambda pid: OFFICIAL
                         if pid == "taguyan.webhook" else None)
    registry.Scan()
    entries = registry.Entries()
    assert [(e.id, e.badge, e.state) for e in entries] == [
        (BUILTIN_ID, T.Badge.BUILTIN, T.PluginState.READY),
        ("example.folder", T.Badge.MANUAL, T.PluginState.INCOMPATIBLE),
        ("taguyan.webhook", T.Badge.LISTED, T.PluginState.READY),
    ]
    builtin = entries[0]
    assert (builtin.version, builtin.size_bytes, builtin.author) == (
        "0.5.0", 100, "taguyan")
    assert entries[2].has_icon and entries[2].size_bytes > 0
    misnamed = registry.Get("example.folder")
    assert misnamed.report.manifest.id == "example.misnamed"
    assert ("id_folder_mismatch", "plugin", None) in _found(misnamed.report)


def test_the_builtin_points_at_the_archive_workers_folder(tmp_path):
    registry = _registry(tmp_path)
    registry.Scan()
    builtin = registry.Get(BUILTIN_ID)
    assert builtin.folder == str(tmp_path / "app" / ArchiveWorker.WORKER_DIR)
    manifest = builtin.report.manifest
    assert manifest.entry == (ArchiveWorker.WORKER_EXECUTABLE,)
    assert [(j.lane, j.manual) for j in manifest.jobs] == [("heavy", False)]
    assert BUILTIN_VODSPLIT.timeout_seconds is None
    assert BUILTIN_VODSPLIT.describe_args == ("version",)
    assert BUILTIN_VODSPLIT.run_args == ("--cancel-on-stdin",)
    # Never blocked or disabled, missing or not.
    other = _registry(tmp_path, blockedBy=lambda *_a: "bad",
                      isEnabled=lambda _id: False)
    other.Scan()
    assert other.Get(BUILTIN_ID).state == T.PluginState.READY


def test_a_dev_folder_is_named_by_its_path():
    path = os.path.abspath("C:/Somewhere/Plugin")
    expected = hashlib.sha256(os.path.normcase(path).encode("utf-8")).hexdigest()[:12]
    assert DevFolderId("C:/Somewhere/Plugin") == expected
    assert DevFolderId("c:\\somewhere\\plugin") == expected  # normcase


def _dev(tmp_path, name, pluginId):
    manifest = _example()
    manifest["id"] = pluginId
    return _plugin(tmp_path / "dev", manifest, name=name)


def test_dev_folders_come_from_the_env_and_the_toggled_list(tmp_path):
    one = _dev(tmp_path, "one-src", "me.one")
    two = _dev(tmp_path, "two-src", "me.two")
    three = _dev(tmp_path, "three-src", "me.three")
    env = {DEV_ENV: f"{one}; {two} ;"}
    registry = _registry(tmp_path, environ=lambda: env)
    registry.Scan()
    assert [(f.id, f.plugin_id, f.from_env) for f in registry.DevFolders()] == [
        (DevFolderId(str(one)), "me.one", True),
        (DevFolderId(str(two)), "me.two", True)]
    assert [e.badge for e in registry.Entries()][1:] == [T.Badge.DEV] * 2
    assert registry.Get("me.one").size_bytes == 0      # a source tree: not walked

    added = registry.AddDevFolder(str(three))
    assert added.id == DevFolderId(str(three))
    # The toggle is off: the list's folder waits.
    assert "me.three" not in [e.id for e in registry.Entries()]
    registry.SetDevMode(True)
    assert registry.DevMode()
    assert [e.id for e in registry.Entries()][1:] == ["me.three", "me.one", "me.two"]
    data = json.loads((tmp_path / "ud" / "plugins" / "dev.json")
                      .read_text(encoding="utf-8"))
    assert data == {"enabled": True, "folders": [str(three)]}

    assert registry.RemoveDevFolder(added.id) is True
    assert registry.RemoveDevFolder(added.id) is False
    # An env folder is not dev.json's to remove.
    assert registry.RemoveDevFolder(DevFolderId(str(one))) is False
    assert [e.id for e in registry.Entries()][1:] == ["me.one", "me.two"]


def test_a_dev_folder_with_an_installed_id_is_refused(tmp_path):
    _plugin(_app(tmp_path), _example())
    clash = _dev(tmp_path, "clash", "taguyan.webhook")
    registry = _registry(tmp_path, environ=lambda: {DEV_ENV: str(clash)})
    registry.Scan()
    assert [e.badge for e in registry.Entries()] == [T.Badge.BUILTIN, T.Badge.LISTED]
    folderId = DevFolderId(str(clash))
    assert ("id_taken", "plugin", None) in {
        (p.code.value, p.scope.value, p.job)
        for p in registry.DevFolderProblems(folderId)}
    assert registry.Get("taguyan.webhook").badge == T.Badge.LISTED

    listed = _registry(tmp_path)
    listed.Scan()
    refused = listed.AddDevFolder(str(clash))
    assert refused == T.Refused(T.StartRefusal.ID_TAKEN, detail="taguyan.webhook")
    assert not (tmp_path / "ud" / "plugins" / "dev.json").exists()


def test_a_dev_folder_with_the_builtins_or_another_dev_id_is_refused(tmp_path):
    first = _dev(tmp_path, "first", "me.same")
    second = _dev(tmp_path, "second", "me.same")
    vodsplit = _dev(tmp_path, "vod", BUILTIN_ID)
    registry = _registry(tmp_path, environ=lambda: {
        DEV_ENV: ";".join(map(str, (first, second, vodsplit)))})
    registry.Scan()
    assert [(e.id, e.badge) for e in registry.Entries()] == [
        (BUILTIN_ID, T.Badge.BUILTIN), ("me.same", T.Badge.DEV)]
    assert registry.Get("me.same").folder == str(first)
    for folder in (second, vodsplit):
        codes = {p.code for p in registry.DevFolderProblems(DevFolderId(str(folder)))}
        assert T.ManifestCheck.ID_TAKEN in codes
    assert registry.AddDevFolder(str(tmp_path / "nowhere")).reason == \
        T.StartRefusal.INVALID_REQUEST


def test_a_dev_plugin_is_never_blocked(tmp_path):
    folder = _dev(tmp_path, "src", "me.tool")
    registry = _registry(tmp_path, environ=lambda: {DEV_ENV: str(folder)},
                         blockedBy=lambda *_a: "blocked everywhere")
    registry.Scan()
    assert registry.Get("me.tool").state == T.PluginState.READY


# ── the list's state ────────────────────────────────────────────────────

@pytest.mark.parametrize("conditions, state", [
    ({"blocked", "incompatible", "disabled", "running", "missing"}, "blocked"),
    ({"incompatible", "disabled", "running", "missing"}, "incompatible"),
    ({"disabled", "running", "missing"}, "disabled"),
    ({"running", "missing"}, "running"),
    ({"missing"}, "needs_settings"),
    (set(), "ready"),
])
def test_the_state_is_the_first_that_applies(tmp_path, conditions, state):
    manifest = _example()
    if "incompatible" in conditions:
        manifest["protocol"] = [2]
    _plugin(_app(tmp_path), manifest)

    # PluginSettings.Missing's shape: dev is keyword-only.
    def missing(pluginId, manifest, *, dev):
        return (("webhook_url",) if "missing" in conditions and dev is False
                and isinstance(manifest, T.Manifest) else ())

    registry = _registry(
        tmp_path,
        blockedBy=lambda pid, version: {"ja": "止めた"}
        if "blocked" in conditions and (pid, version) == ("taguyan.webhook", "1.0.0")
        else None,
        isEnabled=lambda _id: "disabled" not in conditions,
        isRunning=lambda _id: "running" in conditions,
        settingsMissing=missing)
    registry.Scan()
    plugin = registry.Get("taguyan.webhook")
    assert plugin.state == T.PluginState(state)
    assert plugin.enabled is ("disabled" not in conditions)
    assert (plugin.blocked_reason is not None) is ("blocked" in conditions)
    assert [e.state for e in registry.Entries()][-1] == T.PluginState(state)
    assert tuple(T.STATE_PRECEDENCE) == tuple(
        T.PluginState(s) for s in CONTRACT["plugin_list"]["state_precedence"])


def test_a_plugin_without_a_usable_job_is_incompatible(tmp_path):
    manifest = _example()
    for job in manifest["jobs"]:
        job["timeout_seconds"] = 9999
    _plugin(_app(tmp_path), manifest)
    registry = _registry(tmp_path)
    registry.Scan()
    assert registry.Get("taguyan.webhook").state == T.PluginState.INCOMPATIBLE


def test_a_list_row_has_the_contracts_fields_only(tmp_path):
    registry = _registry(tmp_path)
    registry.Scan()
    row = registry.Entries()[0]
    assert [f.name for f in dataclasses.fields(row)] == list(
        CONTRACT["plugin_list"]["fields"])


def test_a_failing_callable_does_not_break_the_list(tmp_path):
    _plugin(_app(tmp_path), _example())

    def broken(*_args):
        raise RuntimeError("boom")

    registry = _registry(tmp_path, isRunning=broken, blockedBy=broken)
    registry.Scan()
    assert registry.Get("taguyan.webhook").state == T.PluginState.READY


# ── actions and hooks ───────────────────────────────────────────────────

def test_actions_and_hooks_list_the_usable_jobs(tmp_path):
    _plugin(_app(tmp_path), _example())
    other = _example()
    other["id"] = "example.off"
    _plugin(_app(tmp_path), other)
    registry = _registry(tmp_path, isEnabled=lambda pid: pid != "example.off",
                         installSource=lambda pid: OFFICIAL)
    registry.Scan()
    assert [(pid, job.id) for pid, job in
            registry.Actions(T.ActionTarget.RESULT_IMAGE)] == [
        ("taguyan.webhook", "post_image")]
    assert registry.Actions(T.ActionTarget.THUMBNAIL_IMAGE) == []
    assert [(pid, job.id) for pid, job in
            registry.HookJobs(T.HookEvent.MATCH_DECIDED)] == [
        ("taguyan.webhook", "post_match")]
    assert [(pid, job.id) for pid, job in
            registry.HookJobs(T.HookEvent.RESULT_IMAGE_SAVED)] == [
        ("taguyan.webhook", "post_image")]


# ── reload (describe) ───────────────────────────────────────────────────

def test_reload_describes_a_dev_plugin(tmp_path):
    folder = _dev(tmp_path, "src", "me.tool")
    calls = []
    answers = {"version": "1.0.0"}

    # PluginRunner.Describe's shape: dev is keyword-only.
    def describe(pluginDir, entry, *, dev):
        calls.append((pluginDir, tuple(entry), dev))
        return T.DescribeRun(launched=True, exit_code=0, stdout_lines=1, objects=(
            dict(ANSWER, id="me.tool", version=answers["version"]),))

    _plugin(_app(tmp_path), _example())
    registry = _registry(tmp_path, environ=lambda: {DEV_ENV: str(folder)},
                         describe=describe)
    registry.Scan()
    assert calls == []                       # nothing starts at the scan
    assert registry.Reload("taguyan.webhook") is None   # not a dev plugin
    result = registry.Reload("me.tool")
    assert result.protocol == 1 and result.failure is None
    assert calls == [(str(folder), ("bin/webhook.exe",), True)]
    assert registry.Get("me.tool").describe == result

    answers["version"] = "2.0.0"
    result = registry.Reload("me.tool")
    assert result.failure == T.DescribeFailure.VERSION_MISMATCH
    assert registry.Get("me.tool").state == T.PluginState.INCOMPATIBLE
    assert registry.Reload("nobody") is None


# ── review follow-ups ───────────────────────────────────────────────────

def test_the_injected_callables_have_the_protocols_shape():
    import inspect
    describe = inspect.signature(T.PluginRunner.Describe).parameters
    missing = inspect.signature(T.PluginSettings.Missing).parameters
    assert describe["dev"].kind is inspect.Parameter.KEYWORD_ONLY
    assert missing["dev"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("change", [
    # a type that is not hashable, on a job with a hook (the default check)
    lambda m: _job(m, "post_match")["params"].append(
        {"key": "x", "type": ["text"], "label": "x", "required": True}),
    lambda m: _job(m, "post_match")["params"].append(
        {"key": "y", "type": {"a": 1}, "label": "y", "required": True}),
    # past int()'s digit limit
    lambda m: m.update(min_autona="1" * 5000 + ".0.0"),
    lambda m: m.update(version="9" * 5000 + ".0.0"),
], ids=["list type", "dict type", "huge min_autona", "huge version"])
def test_an_odd_manifest_is_a_problem_not_an_exception(tmp_path, change):
    manifest = _example()
    change(manifest)
    report = _check(tmp_path, manifest)
    assert {p.code for p in report.problems} <= {
        T.ManifestCheck.INVALID_FIELD, T.ManifestCheck.INVALID_JOB_FIELD}
    assert report.problems


def test_a_manifest_nested_too_deep_is_unreadable(tmp_path):
    folder = tmp_path / "a.b"
    folder.mkdir()
    (folder / "plugin.json").write_text("[" * 100000 + "]" * 100000,
                                        encoding="utf-8")
    raw, problem = ReadManifest(str(folder))
    assert raw is None and problem.code == T.ManifestCheck.MANIFEST_UNREADABLE


def test_a_check_that_breaks_takes_down_its_folder_only(tmp_path, monkeypatch):
    _plugin(_app(tmp_path), _example())
    other = _example()
    other["id"] = "example.fine"
    _plugin(_app(tmp_path), other)
    registry = _registry(tmp_path, installSource=lambda _id: OFFICIAL)
    real = registry.Check

    def check(raw, folder, source, *, dev, hostVersion):
        if raw.get("id") == "taguyan.webhook":
            raise TypeError("a bug the manifest found")
        return real(raw, folder, source, dev=dev, hostVersion=hostVersion)

    monkeypatch.setattr(registry, "Check", check)
    registry.Scan()
    states = {e.id: e.state for e in registry.Entries()}
    assert states["example.fine"] == T.PluginState.READY
    assert states["taguyan.webhook"] == T.PluginState.INCOMPATIBLE
    broken = registry.Get("taguyan.webhook").report
    assert broken.manifest is None
    assert [(p.code, p.scope) for p in broken.problems] == [
        (T.ManifestCheck.MANIFEST_UNREADABLE, T.ProblemScope.PLUGIN)]


@pytest.mark.parametrize("change, path", [
    (lambda m: m.update(id="taguyan.webhook\n"), "id"),
    (lambda m: m.update(version="1.0.0\n"), "version"),
    (lambda m: m.update(min_autona="2.6.0\n"), "min_autona"),
    (lambda m: m.update(homepage="https://example.com\n"), "homepage"),
    (lambda m: m["settings"][1].update(key="mention\n"), "settings[1].key"),
], ids=["id", "version", "min_autona", "homepage", "key"])
def test_a_trailing_newline_does_not_pass_a_pattern(tmp_path, change, path):
    manifest = _example()
    change(manifest)
    report = _check(tmp_path, manifest, name="taguyan.webhook")
    assert (T.ManifestCheck.INVALID_FIELD, path) in {
        (p.code, p.path) for p in report.problems}


@pytest.mark.parametrize("pluginId, refused", [
    ("con.tool", True), ("me.nul", True), ("aux.x", True), ("prn.y", True),
    ("com1.x", True), ("me.lpt9", True), ("cons.tool", False),
    ("me.com10", False), ("me.console", False),
])
def test_a_windows_device_name_is_no_id(tmp_path, pluginId, refused):
    manifest = _example()
    manifest["id"] = pluginId
    report = _check(tmp_path, manifest, source=T.InstallSource("manual"))
    assert (("invalid_field", "plugin", None) in _found(report)) is refused
    assert (report.manifest is None) is refused


def test_integral_floats_are_integers(tmp_path):
    manifest = _example()
    manifest["manifest"] = 1.0
    manifest["protocol"] = [1.0]
    job = _job(manifest, "post_image")
    job["timeout_seconds"] = 60.0
    job["memory_mb"] = 256.0
    job["params"][1]["max_length"] = 1500.0
    report = _check(tmp_path, manifest)
    assert report.problems == ()
    built = report.manifest
    assert (built.manifest, built.protocol) == (1, (1,))
    assert type(built.manifest) is int and type(built.protocol[0]) is int
    first = built.jobs[0]
    assert (first.timeout_seconds, first.memory_mb) == (60, 256)
    assert type(first.timeout_seconds) is int and type(first.memory_mb) is int
    assert type(first.params[1].max_length) is int
    # 1.5 is not one.
    job["timeout_seconds"] = 60.5
    assert ("invalid_job_field", "job", "post_image") in _found(
        _check(tmp_path / "half", manifest))


def test_a_secrets_default_is_never_kept(tmp_path):
    manifest = _example()
    manifest["settings"][0]["default"] = "https://leaked.example"
    report = _check(tmp_path, manifest)
    assert report.manifest.settings[0].default is None


def test_bad_options_are_reported_once(tmp_path):
    manifest = _example()
    manifest["settings"][1]["options"] = "none"
    report = _check(tmp_path, manifest)
    assert [p.path for p in report.problems] == ["settings[1].options"]
    manifest = _example()
    manifest["settings"].append({"key": "f", "type": "file", "label": "f",
                                 "extensions": "png"})
    report = _check(tmp_path / "file", manifest)
    assert [p.path for p in report.problems] == ["settings[2].extensions"]


def test_a_describe_without_an_exit_code_says_so(tmp_path):
    result = _registry(tmp_path).JudgeDescribe(
        T.DescribeRun(launched=True, exit_code=None), _manifest(tmp_path))
    assert (result.failure, result.detail) == (T.DescribeFailure.NONZERO_EXIT,
                                               "no exit code")


def test_only_a_swaps_leftovers_are_skipped(tmp_path):
    plugins = _app(tmp_path)
    _plugin(plugins, _example(), name="taguyan.webhook.new")
    odd = _example()
    odd["id"] = "foo.new"          # a plugin whose id ends in .new
    _plugin(plugins, odd)
    registry = _registry(tmp_path, installSource=lambda _id: None)
    registry.Scan()
    assert [e.id for e in registry.Entries()] == [BUILTIN_ID, "foo.new"]


def test_the_builtin_has_no_time_limit(tmp_path):
    registry = _registry(tmp_path)
    registry.Scan()
    assert registry.Get(BUILTIN_ID).report.manifest.jobs[0].timeout_seconds is None


def test_a_listed_dev_folder_shows_while_the_toggle_is_off(tmp_path):
    folder = _dev(tmp_path, "src", "me.tool")
    registry = _registry(tmp_path)
    added = registry.AddDevFolder(str(folder))
    assert [(f.id, f.plugin_id, f.from_env) for f in registry.DevFolders()] == [
        (added.id, "me.tool", False)]
    assert registry.DevFolderActive(added.id) is False
    assert "me.tool" not in [e.id for e in registry.Entries()]
    registry.SetDevMode(True)
    assert registry.DevFolderActive(added.id) is True
    assert "me.tool" in [e.id for e in registry.Entries()]


def test_a_dev_json_that_cannot_be_written_is_refused(tmp_path, monkeypatch):
    from src.core import PluginRegistry as module
    folder = _dev(tmp_path, "src", "me.tool")
    registry = _registry(tmp_path)
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)
    calls = []

    def replace(source, target):
        calls.append(target)
        raise PermissionError("held open")

    monkeypatch.setattr(module.os, "replace", replace)
    refused = registry.AddDevFolder(str(folder))
    assert refused == T.Refused(T.StartRefusal.UNAVAILABLE,
                                detail="dev.json not written")
    assert len(calls) == 4                              # retried
    assert registry.SetDevMode(True).reason == T.StartRefusal.UNAVAILABLE
    assert list((tmp_path / "ud" / "plugins").iterdir()) == []   # no .tmp
    monkeypatch.undo()
    assert registry.SetDevMode(True) is None


def test_a_dev_json_replace_that_fails_for_a_moment_is_retried(tmp_path,
                                                                monkeypatch):
    from src.core import PluginRegistry as module
    real = module.os.replace
    failures = [PermissionError("held"), PermissionError("held")]

    def replace(source, target):
        if failures:
            raise failures.pop()
        return real(source, target)

    monkeypatch.setattr(module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(module.os, "replace", replace)
    registry = _registry(tmp_path)
    assert registry.SetDevMode(True) is None
    assert registry.DevMode() is True


def test_a_broken_dev_json_is_moved_aside_before_a_write(tmp_path):
    folder = _dev(tmp_path, "src", "me.tool")
    listPath = tmp_path / "ud" / "plugins" / "dev.json"
    listPath.parent.mkdir(parents=True)
    listPath.write_text("{broken", encoding="utf-8")
    registry = _registry(tmp_path)
    registry.Scan()                                  # reading leaves it
    assert listPath.read_text(encoding="utf-8") == "{broken"
    registry.AddDevFolder(str(folder))
    aside = list(listPath.parent.glob("dev.json.unreadable-*.bak"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == "{broken"
    assert json.loads(listPath.read_text(encoding="utf-8"))["folders"] == [str(folder)]


def test_an_installed_folder_is_no_dev_folder(tmp_path):
    folder = _plugin(_app(tmp_path), _example())
    worker = tmp_path / "app" / ArchiveWorker.WORKER_DIR
    worker.mkdir()
    registry = _registry(tmp_path)
    registry.Scan()
    for path in (folder, worker, _app(tmp_path)):
        refused = registry.AddDevFolder(str(path))
        assert refused.reason == T.StartRefusal.INVALID_REQUEST, path


def test_an_installed_folder_with_the_builtins_id_is_shown_refused(tmp_path):
    vodsplit = _example()
    vodsplit["id"] = BUILTIN_ID
    folder = _plugin(_app(tmp_path), vodsplit)
    registry = _registry(tmp_path)
    registry.Scan()
    assert [e.badge for e in registry.Entries()] == [T.Badge.BUILTIN]
    assert [(path, {p.code for p in problems})
            for path, problems in registry.RefusedFolders()] == [
        (str(folder), {T.ManifestCheck.ID_TAKEN})]


def test_the_size_skips_junctions_and_is_kept(tmp_path, monkeypatch):
    _winapi = pytest.importorskip("_winapi")
    from src.core import PluginRegistry as module
    folder = _plugin(_app(tmp_path), _example())
    big = tmp_path / "big"
    big.mkdir()
    (big / "huge.bin").write_bytes(b"x" * 100000)
    _winapi.CreateJunction(str(big), str(folder / "linked"))
    registry = _registry(tmp_path)
    registry.Scan()
    size = registry.Get("taguyan.webhook").size_bytes
    assert 0 < size < 100000
    walks = []
    real = module._FolderSize
    monkeypatch.setattr(module, "_FolderSize",
                        lambda path: walks.append(path) or real(path))
    registry.Scan()
    assert walks == []                               # the same folders: kept
    (folder / "new.bin").write_bytes(b"y" * 10)
    registry.Scan()
    assert walks == [str(folder)]
    assert registry.Get("taguyan.webhook").size_bytes == size + 10
