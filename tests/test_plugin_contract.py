"""Plugin contract 1 (ADR-009 W0): the contract's files held to each other.

``docs/plugins/contract_1.json`` is the source of truth of Worker Protocol 1,
manifest 1 and the input formats; the parallel lanes (PluginRunner,
PluginRegistry, PluginJobs, PluginHooks, the SDK, the web page) build against
it alone. This module keeps its pieces from drifting apart:

- ``plugin.schema.json`` (the manifest's JSON Schema) states the contract's
  patterns, enums and limits, and ADR-009 B.1's example manifest
  (``docs/plugins/examples/webhook.plugin.json``) passes it -- checked by a
  small validator of the schema keywords the file uses (jsonschema is not a
  dependency); broken copies of the example fail it;
- ``src/core/PluginTypes.py`` (what running code reads: the docs are not
  shipped) has the contract's enums, patterns, limits, environment names
  and field names, and the parts already on main implement its Protocols
  (method names and parameters);
- the end table is ``ArchiveJobService._EndState``'s order;
- ``routes_v1.json`` names fixed routes, THIS_PC only for the list and the
  icon, endpoints that no route of another class has taken;
- the input sources' golden files are what ``MatchLogService`` writes and
  keys of ``StateSchema.KEY_CATALOG``;
- the fake worker (``tests/fixtures/fake_plugin_worker.py``), run in every
  mode, prints what the contract's events allow (or, in the modes made to
  break it, exactly the breaks the host must absorb) and ends as the end
  table says.

Holding the real engine and the SDK to the contract is the later lanes'.
"""

import dataclasses
import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from src.core import MatchLogService as match_log
from src.core import PluginTypes as T
from src.core import StateSchema, WebAccess

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "docs" / "plugins"
CONTRACT_PATH = PLUGINS / "contract_1.json"
SCHEMA_PATH = PLUGINS / "plugin.schema.json"
ROUTES_PATH = PLUGINS / "routes_v1.json"
EXAMPLE_PATH = PLUGINS / "examples" / "webhook.plugin.json"
INPUTS = PLUGINS / "inputs"
FAKE = ROOT / "tests" / "fixtures" / "fake_plugin_worker.py"


def _load(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


CONTRACT = _load(CONTRACT_PATH)
SCHEMA = _load(SCHEMA_PATH)
ROUTES = _load(ROUTES_PATH)


def _values(enum):
    return [member.value for member in enum]


# ── a validator of the JSON Schema keywords plugin.schema.json uses ─────

_KNOWN_KEYWORDS = {
    "$schema", "$id", "$ref", "$defs", "title", "description", "type",
    "enum", "const", "pattern", "minLength", "maxLength", "minimum",
    "maximum", "exclusiveMinimum", "minItems", "maxItems", "uniqueItems",
    "items", "prefixItems", "contains", "required", "properties",
    "additionalProperties", "propertyNames", "minProperties", "allOf",
    "oneOf", "anyOf", "not", "if", "then", "else"}


def _is_type(value, kind):
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "null":
        return value is None
    raise AssertionError(f"unknown type {kind}")


def _resolve(ref):
    assert ref.startswith("#/"), ref
    node = SCHEMA
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _errors(value, schema, path="$"):
    """The ways `value` breaks `schema` (empty when it passes)."""
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: not allowed"]
    unknown = set(schema) - _KNOWN_KEYWORDS
    assert not unknown, f"the validator does not know {unknown}"
    found = []
    if "$ref" in schema:
        found += _errors(value, _resolve(schema["$ref"]), path)
    if "type" in schema and not _is_type(value, schema["type"]):
        return found + [f"{path}: not {schema['type']}"]
    if "const" in schema and value != schema["const"]:
        found.append(f"{path}: not {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            found.append(f"{path}: {value!r} !~ {schema['pattern']}")
        if len(value) < schema.get("minLength", 0):
            found.append(f"{path}: too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            found.append(f"{path}: too long")
    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{path}: < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            found.append(f"{path}: > {schema['maximum']}")
        if ("exclusiveMinimum" in schema
                and value <= schema["exclusiveMinimum"]):
            found.append(f"{path}: <= {schema['exclusiveMinimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            found.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            found.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            seen = [json.dumps(item, sort_keys=True) for item in value]
            if len(seen) != len(set(seen)):
                found.append(f"{path}: items not unique")
        prefix = schema.get("prefixItems", [])
        for index, item in enumerate(value):
            sub = prefix[index] if index < len(prefix) else schema.get("items")
            if sub is not None:
                found += _errors(item, sub, f"{path}[{index}]")
        if "contains" in schema and not any(
                not _errors(item, schema["contains"]) for item in value):
            found.append(f"{path}: contains nothing matching")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                found.append(f"{path}: {name} missing")
        if len(value) < schema.get("minProperties", 0):
            found.append(f"{path}: too few properties")
        properties = schema.get("properties", {})
        for name, item in value.items():
            if name in properties:
                found += _errors(item, properties[name], f"{path}.{name}")
            elif "additionalProperties" in schema:
                found += _errors(item, schema["additionalProperties"],
                                 f"{path}.{name}")
            if "propertyNames" in schema:
                found += _errors(name, schema["propertyNames"],
                                 f"{path}<{name}>")
    for sub in schema.get("allOf", []):
        found += _errors(value, sub, path)
    if "anyOf" in schema and all(_errors(value, sub)
                                 for sub in schema["anyOf"]):
        found.append(f"{path}: matches no anyOf")
    if "oneOf" in schema:
        passing = [sub for sub in schema["oneOf"] if not _errors(value, sub)]
        if len(passing) != 1:
            found.append(f"{path}: matches {len(passing)} of oneOf")
    if "not" in schema and not _errors(value, schema["not"]):
        found.append(f"{path}: matches not")
    if "if" in schema:
        branch = "then" if not _errors(value, schema["if"]) else "else"
        if branch in schema:
            found += _errors(value, schema[branch], path)
    return found


def _example():
    return _load(EXAMPLE_PATH)


# ── the schema and the example manifest ────────────────────────────────

def test_the_example_manifest_passes_the_schema():
    assert _errors(_example(), SCHEMA) == []


def _job(manifest, job_id):
    return next(job for job in manifest["jobs"] if job["id"] == job_id)


def _plugin_errors(manifest):
    """The plugin-scoped check (manifest_checks.schema_scopes): the top
    level and settings, every job left out."""
    top = dict(SCHEMA, properties=dict(SCHEMA["properties"]))
    top["properties"]["jobs"] = {"type": "array", "minItems": 1,
                                 "items": {"type": "object"}}
    return _errors(manifest, top)


def _job_errors(job):
    """The job-scoped check: one job against #/$defs/job."""
    return _errors(job, SCHEMA["$defs"]["job"])


CHECKS = {row["code"]: row for row in CONTRACT["manifest_checks"]["codes"]}


@pytest.mark.parametrize("name, code, change", [
    ("no id", "invalid_field", lambda m: m.pop("id")),
    ("id with a capital", "invalid_field",
     lambda m: m.update(id="Taguyan.webhook")),
    ("id without a dot", "invalid_field", lambda m: m.update(id="webhook")),
    ("manifest 2", "manifest_version_unknown",
     lambda m: m.update(manifest=2)),
    ("version not X.Y.Z", "invalid_field", lambda m: m.update(version="1.0")),
    ("http homepage", "invalid_field",
     lambda m: m.update(homepage="http://example.com")),
    ("protocol without 1", "protocol_unsupported",
     lambda m: m.update(protocol=[2])),
    ("no windows-x64", "platform_unsupported",
     lambda m: m.update(platforms=["linux-x64"])),
    ("@ entry", "entry_reserved",
     lambda m: m.update(entry=["@python", "main.py"])),
    ("absolute entry", "invalid_field",
     lambda m: m.update(entry=["C:\\bin\\x.exe"])),
    ("entry going up", "invalid_field",
     lambda m: m.update(entry=["../x.exe"])),
    ("no permissions.files", "invalid_field",
     lambda m: m["permissions"].pop("files")),
    ("unknown files scope", "invalid_field",
     lambda m: m["permissions"].update(files="everything")),
    ("no jobs", "invalid_field", lambda m: m.update(jobs=[])),
    ("source type in settings", "source_in_settings",
     lambda m: m["settings"].append(
         {"key": "img", "type": "result_image", "label": "i"})),
    ("select without options", "invalid_field",
     lambda m: m["settings"][1].pop("options")),
    ("file without extensions", "invalid_field",
     lambda m: m["settings"].append(
         {"key": "f", "type": "file", "label": "f"})),
    ("bad key", "invalid_field",
     lambda m: m["settings"][1].update(key="Mention")),
    ("secret with a default", "invalid_field",
     lambda m: m["settings"][0].update(default="https://x")),
    ("light timeout over 300", "timeout_out_of_range",
     lambda m: _job(m, "post_image").update(timeout_seconds=301)),
    ("light without memory", "invalid_job_field",
     lambda m: _job(m, "post_image").pop("memory_mb")),
    ("light memory under 64", "memory_out_of_range",
     lambda m: _job(m, "post_image").update(memory_mb=32)),
    ("hook job memory over 512", "memory_out_of_range",
     lambda m: _job(m, "post_image").update(memory_mb=600)),
    ("hook on a heavy job", "trigger_needs_light_lane",
     lambda m: _job(m, "post_match").update(lane="heavy")),
    ("context type with manual true", "manual_with_context",
     lambda m: _job(m, "post_match").update(manual=True)),
    ("context type without manual", "manual_with_context",
     lambda m: _job(m, "post_match").pop("manual")),
    ("secret in params", "secret_outside_settings",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "token", "type": "secret", "label": "t"})),
    ("text over the cap", "invalid_job_field",
     lambda m: _job(m, "post_image")["params"][1].update(max_length=20001)),
])
def test_a_broken_manifest_fails_in_its_codes_scope(name, code, change):
    manifest = _example()
    change(manifest)
    assert _errors(manifest, SCHEMA), name
    row = CHECKS[code]
    jobs = manifest["jobs"] if isinstance(manifest.get("jobs"), list) else []
    failing = [job["id"] for job in jobs if _job_errors(job)]
    if row["scope"] == "plugin":
        assert row["schema"] in ("top", "settings"), code
        assert _plugin_errors(manifest), name
        # The jobs themselves still pass: the plugin is what is unusable.
        assert failing == [], name
    else:
        assert (row["scope"], row["schema"]) == ("job", "job"), code
        # The top level passes: only the one job is unusable.
        assert _plugin_errors(manifest) == [], name
        assert len(failing) == 1, name


@pytest.mark.parametrize("name, change", [
    ("unknown field type in params",
     lambda m: _job(m, "post_image")["params"].append(
         {"key": "colour", "type": "color", "label": "c", "default": "#fff"})),
    ("unknown field type in settings",
     lambda m: m["settings"].append(
         {"key": "colour", "type": "color", "label": "c"})),
    ("unknown hook event",
     lambda m: _job(m, "post_match")["hooks"].append(
         {"on": "match_closed", "label": "x"})),
    ("unknown action target",
     lambda m: _job(m, "post_image")["actions"].append(
         {"target": "stream", "label": "x"})),
    ("another platform beside windows-x64",
     lambda m: m.update(platforms=["windows-x64", "linux-x64"])),
    ("a long label",
     lambda m: _job(m, "post_image")["params"][1].update(label="l" * 300)),
    ("a long help",
     lambda m: _job(m, "post_image")["params"][1].update(help="h" * 3000)),
    ("a long author", lambda m: m.update(author="a" * 200)),
    ("a long select value",
     lambda m: m["settings"][1]["options"][0].update(value="v" * 200)),
    ("a heavy job with a large memory",
     lambda m: m["jobs"].append({"id": "big", "title": "b", "lane": "heavy",
                                 "timeout_seconds": 3600,
                                 "memory_mb": 100000})),
    ("an icon outside the folder (a warning, not the schema's)",
     lambda m: m.update(icon="../icon.jpg")),
])
def test_open_names_and_display_limits_pass_the_schema(name, change):
    # D8: a name this host does not know makes the job (the plugin, in
    # settings) unusable through the Registry, never the manifest invalid.
    manifest = _example()
    change(manifest)
    assert _errors(manifest, SCHEMA) == [], name


def test_the_check_codes_name_their_scope_schema_and_owner():
    scopes = CONTRACT["manifest_checks"]["schema_scopes"]
    assert {key: scopes[key] for key in ("top", "settings", "job")} == {
        "top": "plugin", "settings": "plugin", "job": "job"}
    owners = {"PluginRegistry.Check", "PluginRegistry.Scan"}
    for row in CONTRACT["manifest_checks"]["codes"]:
        assert row["owner"] in owners, row["code"]
        assert row["schema"] in (None, "top", "settings", "job"), row["code"]
        if row["schema"] is not None:
            assert row["scope"] == scopes[row["schema"]], row["code"]
        if "settings_scope" in row:
            assert row["scope"] == "job", row["code"]
            assert row["settings_scope"] == "plugin", row["code"]
    for code in ("field_type_unknown", "hook_event_unknown",
                 "action_target_unknown"):
        assert CHECKS[code]["schema"] is None, code
    assert CHECKS["id_taken"]["owner"] == "PluginRegistry.Scan"


def _branches(field):
    """{known type: its then} of #/$defs/field's per-type rules."""
    found = {}
    for rule in field["allOf"]:
        kind = rule["if"]["properties"]["type"]
        for name in kind.get("enum", [kind.get("const")]):
            found[name] = rule["then"]
    return found


def test_the_schema_states_the_contract():
    props = SCHEMA["properties"]
    defs = SCHEMA["$defs"]
    patterns = CONTRACT["patterns"]
    limits = CONTRACT["limits"]
    fields = CONTRACT["field_types"]
    assert SCHEMA["$id"].endswith("/docs/plugins/plugin.schema.json")
    assert props["manifest"]["const"] == CONTRACT["versions"]["manifest"]
    assert props["id"]["pattern"] == patterns["plugin_id"]
    assert props["id"]["maxLength"] == patterns["plugin_id_max_length"]
    assert defs["version"]["pattern"] == patterns["version"]
    assert props["protocol"]["contains"]["const"] \
        == CONTRACT["versions"]["protocol"]
    assert props["update"]["properties"]["github"]["pattern"] \
        == patterns["github_repo"]
    assert defs["entry_head"]["pattern"].startswith("^(?!@)")
    assert props["platforms"]["items"]["pattern"] == patterns["platform"]
    assert props["platforms"]["contains"]["const"] \
        == patterns["host_platform"]
    assert defs["https_url"]["maxLength"] == limits["url_max_length"]
    assert defs["text80"]["oneOf"][0]["maxLength"] \
        == limits["name_max_length"]
    assert defs["text500"]["oneOf"][0]["maxLength"] \
        == limits["description_max_length"]
    assert defs["locale_map"]["propertyNames"]["pattern"] \
        == patterns["locale"]
    # The host's display limits truncate; the schema does not hold them.
    for key in limits["display_truncation"]:
        assert key in limits, key
    assert "maxLength" not in props["author"]
    assert "maxLength" not in defs["text"]["oneOf"][0]
    assert "maxLength" not in defs["option"]["properties"]["value"]

    # Open names: a field type, a hook's on, an action's target.
    assert defs["name"]["pattern"] == patterns["name"]
    field = defs["field"]
    assert field["properties"]["type"] == {"$ref": "#/$defs/name"}
    assert defs["action"]["properties"]["target"] == {"$ref": "#/$defs/name"}
    assert defs["hook"]["properties"]["on"] == {"$ref": "#/$defs/name"}

    job = defs["job"]
    assert job["properties"]["id"]["pattern"] == patterns["job_id"]
    assert field["properties"]["key"]["pattern"] == patterns["field_key"]
    assert job["properties"]["lane"]["enum"] == sorted(
        CONTRACT["limits"]["lanes"], reverse=True)
    assert job["properties"]["timeout_seconds"]["minimum"] \
        == limits["heavy_timeout_seconds"]["min"]
    assert job["properties"]["timeout_seconds"]["maximum"] \
        == limits["heavy_timeout_seconds"]["max"]
    # heavy's memory_mb is recorded only: no bound outside the light rule.
    assert job["properties"]["memory_mb"] == {"type": "integer"}
    light, hooks, actions, context = job["allOf"]
    assert light["then"]["properties"]["timeout_seconds"]["maximum"] \
        == limits["light_timeout_seconds"]["max"]
    assert light["then"]["properties"]["memory_mb"] == {
        "minimum": limits["light_memory_mb"]["min"],
        "maximum": limits["light_memory_mb"]["max"]}
    assert hooks["then"]["properties"]["memory_mb"]["maximum"] \
        == limits["hook_job_memory_mb_max"]
    for rule in (hooks, actions):
        assert rule["then"]["properties"]["lane"]["const"] \
            == CONTRACT["lanes"]["actions_and_hooks_need_lane"]
    assert sorted(context["if"]["properties"]["params"]["contains"]
                  ["properties"]["type"]["enum"]) \
        == sorted(fields["context_only_types"])

    # Every known value type has its rule; the source types are only
    # named (the settings refuse them, by the contract's list).
    value_types = [row["type"] for row in fields["value"]]
    source_types = [row["type"] for row in fields["source"]]
    branches = _branches(field)
    assert sorted(branches) == sorted(value_types)
    assert defs["setting_field"]["not"]["properties"]["type"]["enum"] \
        == source_types
    assert defs["param_field"]["not"]["properties"]["type"]["const"] \
        == "secret"
    assert sorted(fields["settings_types"]) == sorted(value_types)
    assert sorted(fields["params_types"]) == sorted(
        [t for t in value_types if t != "secret"] + source_types)
    assert branches["select"]["properties"]["options"]["maxItems"] \
        == limits["select_max_options"]
    assert branches["file"]["properties"]["extensions"]["items"]["pattern"] \
        == patterns["file_extension"]
    for kind in ("text", "textarea"):
        assert branches[kind]["properties"]["max_length"]["maximum"] \
            == limits["text_max_length"]["cap"]
    assert branches["secret"] == {"not": {"required": ["default"]}}

    permissions = defs["permissions"]["properties"]
    assert permissions["files"]["enum"] == _values(T.FilesScope)
    assert permissions["autona_api"]["enum"] == _values(T.AutonaApiUse)
    assert permissions["note"] == {"$ref": "#/$defs/text"}


def test_every_field_type_in_the_contract_has_one_row():
    fields = CONTRACT["field_types"]
    names = [row["type"] for row in fields["value"] + fields["source"]]
    assert len(names) == len(set(names))
    assert set(fields["manual_pick_types"]) <= {
        row["type"] for row in fields["source"]}
    for row in fields["source"]:
        assert row["format"] == CONTRACT["versions"]["input_formats"][
            row["type"]]
        assert row["context_only"] == (
            row["type"] in fields["context_only_types"])
        assert (row["manual"] is not None) == (
            row["type"] in fields["manual_pick_types"])
    fills = {row["fills"] for row in CONTRACT["triggers"]["actions"]
             + CONTRACT["triggers"]["hooks"]}
    # Every context-only type has a trigger that fills it.
    assert set(fields["context_only_types"]) <= fills


# ── PluginTypes against the contract ───────────────────────────────────

def test_the_enums_are_the_contracts():
    fields = CONTRACT["field_types"]
    plugin_list = CONTRACT["plugin_list"]
    assert _values(T.Lane) == sorted(CONTRACT["limits"]["lanes"],
                                     reverse=True)
    assert _values(T.FieldType) == [row["type"] for row in
                                    fields["value"] + fields["source"]]
    assert {t.value for t in T.VALUE_FIELD_TYPES} \
        == {row["type"] for row in fields["value"]}
    assert {t.value for t in T.SOURCE_FIELD_TYPES} \
        == {row["type"] for row in fields["source"]}
    assert {t.value for t in T.CONTEXT_ONLY_TYPES} \
        == set(fields["context_only_types"])
    assert {t.value for t in T.MANUAL_PICK_TYPES} \
        == set(fields["manual_pick_types"])
    assert {t.value for t in T.DESKTOP_ONLY_TYPES} \
        == {row["type"] for row in fields["source"] if row["desktop_only"]}
    assert {t.value for t in T.AT_TRIGGER_TYPES} \
        == {row["type"] for row in fields["source"] if row["at_trigger"]}
    assert {t.value: n for t, n in T.INPUT_FORMATS.items()} \
        == CONTRACT["versions"]["input_formats"]
    assert _values(T.TriggerKind) == CONTRACT["triggers"]["kinds"]
    assert _values(T.ActionTarget) == [
        row["target"] for row in CONTRACT["triggers"]["actions"]]
    assert _values(T.HookEvent) == [
        row["on"] for row in CONTRACT["triggers"]["hooks"]]
    assert {k.value: v.value for k, v in T.ACTION_FILLS.items()} == {
        row["target"]: row["fills"] for row in CONTRACT["triggers"]["actions"]}
    assert {k.value: v.value for k, v in T.HOOK_FILLS.items()} == {
        row["on"]: row["fills"] for row in CONTRACT["triggers"]["hooks"]}
    events = CONTRACT["events"]["types"]
    assert _values(T.EventType) == [row["type"] for row in events]
    assert {t.value for t in T.TERMINAL_EVENTS} \
        == {row["type"] for row in events if row["terminal"]}
    assert _values(T.ArtifactKind) \
        == CONTRACT["artifacts"]["fields"]["kind"]["values"]
    assert _values(T.ArtifactKind) == [
        row["kind"] for row in CONTRACT["artifacts"]["kinds"]]
    assert _values(T.ArtifactRejection) == CONTRACT["artifacts"]["rejected"]
    assert _values(T.JobState) == CONTRACT["job_states"]
    assert set(CONTRACT["end"]["states"]) == set(CONTRACT["job_states"]) - {
        "running"}
    assert _values(T.EndReason) == CONTRACT["end"]["reasons"]
    assert _values(T.DescribeFailure) \
        == CONTRACT["commands"]["describe"]["unusable_when"]
    assert _values(T.PluginState) == plugin_list["plugin_states"]
    assert [s.value for s in T.STATE_PRECEDENCE] \
        == plugin_list["state_precedence"]
    assert sorted(plugin_list["state_precedence"]) \
        == sorted(plugin_list["plugin_states"])
    assert _values(T.Badge) == plugin_list["badges"]
    assert _values(T.StartRefusal) == [
        row["reason"] for row in CONTRACT["refusals"]["codes"]]
    assert all(row["status"] in (400, 404, 409, 503)
               for row in CONTRACT["refusals"]["codes"])
    checks = CONTRACT["manifest_checks"]["codes"]
    assert _values(T.ManifestCheck) == [row["code"] for row in checks]
    assert {row["scope"] for row in checks} <= set(_values(T.ProblemScope))
    assert _values(T.HookOutcome) == CONTRACT["hooks"]["history_outcomes"]
    assert _values(T.HookDisabledReason) \
        == CONTRACT["hooks"]["disabled_reasons"]
    assert _values(T.InstallSourceKind) \
        == CONTRACT["install"]["source_kinds"]
    assert _values(T.ZipProblem) == CONTRACT["install"]["zip_problems"]


def test_the_shared_constants_and_the_secret_free_repr():
    # One set of official repository ids, shared by the registry (and the
    # installer): a taguyan. id comes only from these.
    assert isinstance(T.OFFICIAL_REPO_IDS, frozenset)
    assert all(isinstance(n, int) for n in T.OFFICIAL_REPO_IDS)
    from src.core import PluginRegistry
    assert PluginRegistry.OFFICIAL_REPO_IDS is T.OFFICIAL_REPO_IDS
    # The launch's environment holds the secrets: never in a repr.
    env = next(f for f in dataclasses.fields(T.LaunchSpec) if f.name == "env")
    assert env.repr is False
    spec = T.LaunchSpec(argv=("w.exe",), cwd="c", env={"AUTONA_SECRET_X": "s3"},
                        lane=T.Lane.LIGHT, log_path="l")
    assert "s3" not in repr(spec)
    # A built-in job has no time limit (job_record's timeout_seconds null).
    assert T.Job(id="run", title="t", lane="heavy",
                 timeout_seconds=None).timeout_seconds is None


def _params(function):
    import inspect
    return list(inspect.signature(function).parameters.values())[1:]


# (Protocol, the module and class implementing it) for the parts on main.
IMPLEMENTED = [
    ("PluginJobs", "PluginJobs"),
    ("PluginRegistry", "PluginRegistry"),
    ("PluginHooks", "PluginHooks"),
    ("PluginSettings", "PluginSettings"),
    ("PluginSecrets", "PluginSecrets"),
    ("PluginInputs", "PluginInputs"),
]


@pytest.mark.parametrize("protocol, module", IMPLEMENTED)
def test_the_parts_implement_their_protocols(protocol, module):
    """Every method of the Protocol, with its parameters by name and kind
    in order; an implementation's extra parameters have defaults."""
    import importlib
    import inspect
    cls = getattr(importlib.import_module(f"src.core.{module}"), module)
    proto = getattr(T, protocol)
    methods = [name for name, value in vars(proto).items()
               if callable(value) and name[:1].isupper()]
    assert methods, protocol
    for name in methods:
        assert hasattr(cls, name), f"{module}.{name}"
        wanted = _params(getattr(proto, name))
        have = _params(getattr(cls, name))
        assert [(p.name, p.kind) for p in have[:len(wanted)]] \
            == [(p.name, p.kind) for p in wanted], f"{module}.{name}"
        assert all(p.default is not inspect.Parameter.empty
                   or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                   for p in have[len(wanted):]), f"{module}.{name}"
    # Pump is the dispatcher's test hook, not the contract's.
    assert not hasattr(T.PluginHooks, "Pump")


def test_the_protocols_name_what_the_lanes_share():
    assert {"Claim", "Release"} <= set(vars(T.PluginJobs))
    assert "Kill" in vars(T.RunHandle)
    assert {"Refresh", "TrySample"} <= set(vars(T.PluginHooks))
    assert {"DevFolderActive", "RefusedFolders"} <= set(vars(T.PluginRegistry))
    import inspect
    check = inspect.signature(T.PluginRegistry.Check).parameters
    assert check["expectedId"].kind is inspect.Parameter.KEYWORD_ONLY
    assert {"RepoName", "RepoId", "LatestDescriptor", "Download"} \
        <= set(vars(T.ReleaseNetwork))


def test_the_patterns_versions_and_environment_are_the_contracts():
    patterns = CONTRACT["patterns"]
    assert T.PROTOCOL_VERSION == CONTRACT["versions"]["protocol"]
    assert T.MANIFEST_VERSION == CONTRACT["versions"]["manifest"]
    assert list(T.HOST_SPEAKS_PROTOCOLS) \
        == CONTRACT["versions"]["host_speaks_protocols"]
    assert list(T.HOST_READS_MANIFESTS) \
        == CONTRACT["versions"]["host_reads_manifests"]
    for name, key in [
            ("PLUGIN_ID_PATTERN", "plugin_id"),
            ("PLUGIN_ID_MAX_LENGTH", "plugin_id_max_length"),
            ("RESERVED_ID_PREFIX", "reserved_id_prefix"),
            ("JOB_ID_PATTERN", "job_id"),
            ("FIELD_KEY_PATTERN", "field_key"),
            ("HOOK_KEY_PATTERN", "hook_key"),
            ("VERSION_PATTERN", "version"),
            ("RUN_ID_PATTERN", "run_id"),
            ("ERROR_CODE_PATTERN", "error_code"),
            ("MATCH_RECORD_ID_PATTERN", "match_record_id"),
            ("SHA256_PATTERN", "sha256"),
            ("FILE_EXTENSION_PATTERN", "file_extension"),
            ("NAME_PATTERN", "name"),
            ("PLATFORM_PATTERN", "platform"),
            ("HOST_PLATFORM", "host_platform"),
            ("DEV_FOLDER_ID_PATTERN", "dev_folder_id")]:
        assert getattr(T, name) == patterns[key], name
    assert T.RESERVED_ENTRY_PREFIX \
        == CONTRACT["commands"]["entry"]["reserved_prefix"]
    assert T.FALLBACK_ERROR_CODE == next(
        row for row in CONTRACT["events"]["types"]
        if row["type"] == "error")["fields"]["code"]["invalid"]
    # The run id is the archive's job id.
    from src.core.ArchiveJobService import JOB_ID_PATTERN
    assert T.RUN_ID_PATTERN == "^" + JOB_ID_PATTERN.pattern + "$"
    # The hook key is the job id, a slash and an event.
    assert T.HOOK_KEY_PATTERN == (T.JOB_ID_PATTERN[:-1] + "/"
                                  + T.JOB_ID_PATTERN[1:])

    environment = CONTRACT["environment"]
    assert list(T.ENV_REMOVE_PREFIXES) == environment["remove_prefixes"]
    assert list(T.ENV_REMOVE_NAMES) == environment["remove_names"]
    added = [row["name"] for row in environment["add"]]
    assert added == [T.ENV_PYTHONIOENCODING, T.ENV_PYINSTALLER_RESET,
                     T.ENV_PLUGIN_DIR, T.ENV_PROTOCOL,
                     T.ENV_SECRET_PREFIX + "<KEY>", T.ENV_API_URL]
    # What Autona adds for itself is removed (main.py sets QT_API).
    assert 'os.environ["QT_API"]' in (ROOT / "main.py").read_text(
        encoding="utf-8")
    # The archive worker's environment adds the same two (ArchiveWorker).
    worker = (ROOT / "src" / "core" / "ArchiveWorker.py").read_text(
        encoding="utf-8")
    for name in (T.ENV_PYTHONIOENCODING, T.ENV_PYINSTALLER_RESET):
        assert f'env["{name}"]' in worker


def test_the_limits_are_the_contracts():
    limits = CONTRACT["limits"]
    pairs = {
        "SUMMARY_MAX_LENGTH": "summary_max_length",
        "MAX_WARNINGS": "max_warnings",
        "MAX_ARTIFACTS": "max_artifacts",
        "LINE_MAX_BYTES": "line_max_bytes",
        "WORKER_LOG_MAX_BYTES": "worker_log_max_bytes",
        "LOG_MESSAGE_MAX_LENGTH": "log_message_max_length",
        "ERROR_MESSAGE_MAX_LENGTH": "error_message_max_length",
        "ERROR_HINT_MAX_LENGTH": "error_hint_max_length",
        "LABEL_MAX_LENGTH": "label_max_length",
        "HOOK_TIMEOUT_CAP_SECONDS": "hook_timeout_cap_seconds",
        "HOOK_JOB_MEMORY_MB_MAX": "hook_job_memory_mb_max",
        "DESCRIBE_TIMEOUT_SECONDS": "describe_timeout_seconds",
        "CANCEL_GRACE_SECONDS": "cancel_grace_seconds",
        "SHUTDOWN_GRACE_SECONDS": "shutdown_grace_seconds",
        "MANIFEST_MAX_BYTES": "manifest_max_bytes",
        "ICON_MAX_BYTES": "icon_max_bytes",
        "NAME_MAX_LENGTH": "name_max_length",
        "DESCRIPTION_MAX_LENGTH": "description_max_length",
        "AUTHOR_MAX_LENGTH": "author_max_length",
        "URL_MAX_LENGTH": "url_max_length",
        "SELECT_MAX_OPTIONS": "select_max_options",
        "TEXT_MAX_LENGTH": "text_max_length",
        "SECRET_MAX_LENGTH": "secret_max_length",
        "RUNS_KEPT_PER_PLUGIN": "runs_kept_per_plugin",
        "HOOK_HISTORY_PER_HOOK": "hook_history_per_hook",
        "HOOK_PENDING_PER_HOOK": "hook_pending_per_hook",
        "HOOK_PENDING_MAX_SECONDS": "hook_pending_max_seconds",
        "HOOK_RATE_WINDOW_SECONDS": "hook_rate_window_seconds",
        "HOOK_RATE_WINDOW_MAX_STARTS": "hook_rate_window_max_starts",
        "HOOK_HOUR_WINDOW_SECONDS": "hook_hour_window_seconds",
        "HOOK_HOUR_WINDOW_MAX_STARTS": "hook_hour_window_max_starts",
        "HOOK_CONSECUTIVE_FAILURES_DISABLE":
            "hook_consecutive_failures_disable",
        "MATCH_DECIDED_HOLD_SECONDS": "match_decided_hold_seconds",
        "RESULT_IMAGE_CHOICES": "result_image_choices",
        "RELEASE_DESCRIPTOR_MAX_BYTES": "release_descriptor_max_bytes",
        "ZIP_MAX_BYTES": "zip_max_bytes",
        "ZIP_EXPANDED_MAX_BYTES": "zip_expanded_max_bytes",
        "ZIP_MAX_ENTRIES": "zip_max_entries",
        "INSTALLED_PATH_MAX_LENGTH": "installed_path_max_length",
        "BLOCKLIST_REFRESH_SECONDS": "blocklist_refresh_seconds",
        "UPDATE_CHECK_MIN_INTERVAL_SECONDS":
            "update_check_min_interval_seconds",
    }
    for name, key in pairs.items():
        assert getattr(T, name) == limits[key], name
    for name, key in [("LIGHT_TIMEOUT_SECONDS", "light_timeout_seconds"),
                      ("HEAVY_TIMEOUT_SECONDS", "heavy_timeout_seconds"),
                      ("LIGHT_MEMORY_MB", "light_memory_mb")]:
        assert getattr(T, name) == (limits[key]["min"], limits[key]["max"])
    assert T.CANCEL_GRACE_SECONDS == CONTRACT["stdin"]["cancel_grace_seconds"]
    assert T.DESCRIBE_TIMEOUT_SECONDS \
        == CONTRACT["commands"]["describe"]["timeout_seconds"]
    assert T.MAX_ARTIFACTS == CONTRACT["artifacts"]["max_count"]
    kinds = {row["kind"]: row for row in CONTRACT["artifacts"]["kinds"]}
    assert T.TEXT_ARTIFACT_MAX_BYTES == kinds["text"]["max_bytes"]
    assert T.IMAGE_ARTIFACT_MAX_BYTES == kinds["image"]["max_bytes"]
    assert list(T.IMAGE_ARTIFACT_EXTENSIONS) == kinds["image"]["extensions"]
    events = {row["type"]: row["fields"] for row in
              CONTRACT["events"]["types"]}
    assert events["done"]["summary"]["max_length"] == T.SUMMARY_MAX_LENGTH
    assert events["done"]["artifacts"]["max_items"] == T.MAX_ARTIFACTS
    assert events["error"]["code"]["pattern"] == T.ERROR_CODE_PATTERN
    assert events["log"]["message"]["max_length"] == T.LOG_MESSAGE_MAX_LENGTH
    assert events["error"]["hint"]["max_length"] == T.ERROR_HINT_MAX_LENGTH
    assert CONTRACT["events"]["line_max_bytes"] == T.LINE_MAX_BYTES


def _names(cls):
    return [f.name for f in dataclasses.fields(cls)]


def test_the_dataclasses_are_spelled_like_the_contract():
    assert set(_names(T.RunRequest)) == set(CONTRACT["request"]["fields"])
    trigger = CONTRACT["request"]["fields"]["trigger"]["fields"]
    assert _names(T.Trigger) == list(trigger)
    item = CONTRACT["request"]["fields"]["inputs"]["item"]
    assert _names(T.InputFile) == list(item)
    host = CONTRACT["request"]["fields"]["host"]["fields"]
    assert set(_names(T.HostInfo)) == set(host)
    assert set(_names(T.RunRecord)) == set(CONTRACT["job_record"]["fields"])
    assert _names(T.ListEntry) == list(CONTRACT["plugin_list"]["fields"])
    assert _names(T.Artifact) == list(CONTRACT["artifacts"]["fields"])
    answer = CONTRACT["commands"]["describe"]["answer"]
    assert set(_names(T.DescribeAnswer)) | {"type"} == set(answer)
    assert T.DescribeAnswer.type == answer["type"]["value"]
    by_type = {row["type"]: row["fields"]
               for row in CONTRACT["events"]["types"]}
    classes = [T.StageEvent, T.ProgressEvent, T.LogEvent, T.WarningEvent,
               T.DoneEvent, T.ErrorEvent, T.CancelledEvent]
    assert [cls.type.value for cls in classes] == list(by_type)
    for cls in classes:
        # raw (the whole line) is the host's, not a protocol key.
        assert _names(cls)[-1] == T.RAW_FIELD, cls.__name__
        assert _names(cls)[:-1] == list(by_type[cls.type.value]), \
            cls.__name__
    assert T.RAW_FIELD not in {name for spec in by_type.values()
                               for name in spec}
    assert "final_raw" in _names(T.RunOutcome)
    assert _names(T.DescribeRun) \
        == CONTRACT["commands"]["describe"]["runner_reports"]
    assert "id" in _names(T.DevFolder)
    paths = CONTRACT["manifest_paths"]
    assert set(_names(T.InstallRecord)) == set(paths["install_fields"])
    assert _names(T.InstallSource) == ["kind", "repo_id", "name"]
    assert list(paths["dev_list_fields"]) == ["enabled", "folders"]

    props = SCHEMA["properties"]
    assert set(_names(T.Manifest)) == set(props)
    assert set(_names(T.Job)) == set(SCHEMA["$defs"]["job"]["properties"])
    assert set(_names(T.Permissions)) \
        == set(SCHEMA["$defs"]["permissions"]["properties"])
    assert set(_names(T.Action)) \
        == set(SCHEMA["$defs"]["action"]["properties"])
    assert set(_names(T.Hook)) == set(SCHEMA["$defs"]["hook"]["properties"])
    assert set(_names(T.Option)) \
        == set(SCHEMA["$defs"]["option"]["properties"])
    field = SCHEMA["$defs"]["field"]
    field_keys = set(field["properties"])
    for then in _branches(field).values():
        field_keys |= set(then.get("properties", {}))
    assert set(_names(T.Field)) == field_keys
    assert set(_names(T.Field)) >= set(CONTRACT["field_types"]["common"])
    job_files = CONTRACT["manifest_paths"]["job_files"]
    assert [T.REQUEST_FILE, T.JOB_FILE, T.LOG_FILE, T.INPUTS_DIR + "/",
            T.OUT_DIR + "/"] == job_files
    assert T.MANIFEST_FILE == CONTRACT["manifest_paths"]["file"]


# ── the end table ──────────────────────────────────────────────────────

def _bucket(exit_code):
    return str(exit_code) if exit_code in (0, 2, 130) else "other"


def _end(final, exit_code, cancel_requested=False):
    """The contract's end table: (state, reason) of a run that was
    launched and did not time out."""
    for row in CONTRACT["end"]["table"]:
        if row["final"] == (final or "none") \
                and row["exit"] == _bucket(exit_code):
            state = (row.get("after_cancel_request", row["state"])
                     if cancel_requested else row["state"])
            return state
    raise AssertionError((final, exit_code))


def test_the_end_table_is_complete():
    rows = {(row["final"], row["exit"]) for row in CONTRACT["end"]["table"]}
    assert rows == {(final, code)
                    for final in ("done", "error", "cancelled", "none")
                    for code in ("0", "2", "130", "other")}
    assert len(CONTRACT["end"]["table"]) == 16
    assert {row["state"] for row in CONTRACT["end"]["table"]} \
        <= set(CONTRACT["end"]["states"])
    first = CONTRACT["end"]["checked_first"]
    assert [row["when"] for row in first] == ["launch_failed", "timed_out"]
    assert all(row["state"] == "failed" for row in first)
    assert {row["reason"] for row in first} \
        | set(CONTRACT["end"]["failed_reasons"]) \
        | {"entry_missing"} == set(CONTRACT["end"]["reasons"])
    assert CONTRACT["exit_codes"] == {
        "0": "success", "2": "user_fixable_failure", "130": "cancelled",
        "other": "abnormal"}


class _Run:
    kind = "analysis"
    jobDir = ""

    def __init__(self, final, cancel_requested):
        self.final = {"type": final} if final else None
        self.cancelRequested = cancel_requested


class _Service:
    @staticmethod
    def _LogTail(_run):
        return ""


@pytest.mark.parametrize("cancel_requested", [False, True])
def test_the_end_table_is_the_archive_workers_order(cancel_requested):
    from src.core.ArchiveJobService import ArchiveJobService
    for final in ("done", "error", "cancelled", None):
        for code in (0, 2, 130, 1, -1):
            state, _error = ArchiveJobService._EndState(
                _Service(), _Run(final, cancel_requested), code)
            assert state == _end(final, code, cancel_requested), (
                final, code, cancel_requested)


# ── routes ─────────────────────────────────────────────────────────────

def test_the_routes_are_fixed_and_classed():
    routes = ROUTES["routes"]
    assert ROUTES["access_classes"] == [WebAccess.THIS_PC, WebAccess.SHELL]
    endpoints = [route["endpoint"] for route in routes]
    assert len(endpoints) == len(set(endpoints))
    keys = [(route["method"], route["path"]) for route in routes]
    assert len(keys) == len(set(keys))
    this_pc = [(route["method"], route["path"]) for route in routes
               if route["access"] == WebAccess.THIS_PC]
    assert this_pc == [("GET", "/api/v1/plugins"),
                       ("GET", "/api/v1/plugins/<plugin_id>/icon")]
    plugin_id = re.compile(CONTRACT["patterns"]["plugin_id"])
    for route in routes:
        assert route["access"] in (WebAccess.THIS_PC, WebAccess.SHELL)
        assert route["stage"] in ROUTES["stages"]
        assert route["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE")
        assert route["endpoint"].startswith("api_v1.")
        # Not taken by a route of another class (the lane registers it in
        # ROUTE_ACCESS with this class).
        assert WebAccess.ROUTE_ACCESS.get(
            route["endpoint"], route["access"]) == route["access"]
        path = route["path"]
        assert path == "/api/v1/plugins" or path.startswith(
            ("/api/v1/plugins/", "/api/v1/plugins:"))
        segments = path[len("/api/v1/plugins/"):].split("/")
        if path.startswith("/api/v1/plugins/"):
            head = segments[0].split(":")[0]
            # A fixed word never reads as a plugin id (ids hold a dot).
            assert head == "<plugin_id>" or not plugin_id.match(head)
        for name in re.findall(r"<([^>]+)>", path):
            assert re.fullmatch(r"[a-z_]+", name)
        assert route["purpose"]
    assert {route["stage"] for route in routes} == set(ROUTES["stages"])
    assert [verb["verb"] for verb in ROUTES["shell_verbs"]] \
        == ["pickFile", "openPluginLink"]


def test_the_routes_bodies_and_answers_name_known_shapes():
    types = ROUTES["types"]
    assert set(ROUTES["envelope"]) == {"ok", "error", "body_note"}
    for route in ROUTES["routes"]:
        for key in ("body", "answer"):
            shape = route.get(key)
            if shape is None:
                continue
            if key == "answer" and shape.startswith("raw: "):
                # Not the envelope: a GET of a file's bytes.
                assert route["method"] == "GET", route["endpoint"]
                continue
            assert shape.removesuffix("[]") in types, (route["endpoint"], shape)
        if "body" in route:
            assert route["method"] in ("POST", "PUT", "PATCH"), route["endpoint"]
    # Every P2 route and the secrets' routes say what they answer (the page
    # and the v1 handlers are built against these).
    for route in ROUTES["routes"]:
        if route["stage"] == "P2" or "/secrets/" in route["path"]:
            assert "answer" in route, route["endpoint"]
    # The plugin list answers its contract fields.
    assert types["ListEntry"] == "contract_1.json plugin_list.fields"


# ── the input sources' golden files ────────────────────────────────────

def _log_lines():
    with open(INPUTS / "match_log.jsonl", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def _record_line(record):
    """What MatchLogService writes for `record` (a golden line): its own
    _Record, filled as the observer fills it."""
    # Fields _Shown gains later (best_of ...) stay None: the line's keys
    # are what this golden pins, not _Shown's.
    values = dict.fromkeys(match_log._Shown._fields)
    values.update(
        setId=record["set_id"] or "", swapped=False,
        game=record["game"] or "", tournament=record["tournament"],
        phase=record["phase"], round=record["round"], p1=record["p1"],
        p2=record["p2"], c1=record["c1"], c2=record["c2"],
        score=tuple(record["score"]))
    shown = match_log._Shown(**values)
    made = match_log._Record(shown, shown.setId)
    made.id = record["id"]
    made.startedAt = datetime.fromisoformat(record["started_at"])
    made.updatedAt = datetime.fromisoformat(record["updated_at"])
    made.games = [dict(game) for game in record["games"]]
    return made.Line(record["closed"])


def test_the_match_log_golden_is_what_the_match_log_writes():
    lines = _log_lines()
    assert lines
    for line in lines:
        made = _record_line(line)
        assert line == made
        # The same keys in the same order (the line is json.dumps of it).
        assert list(line) == list(made)
        assert re.fullmatch(T.MATCH_RECORD_ID_PATTERN, line["id"])
        assert line["board"] == int(match_log.BOARD)
        assert isinstance(line["closed"], bool)
        assert line["set_id"] is None or isinstance(line["set_id"], str)
        assert line["game"] is None or isinstance(line["game"], str)
        assert all(isinstance(line[key], str) for key in (
            "tournament", "phase", "round", "p1", "p2", "c1", "c2"))
        assert len(line["score"]) == 2 and all(
            isinstance(n, int) for n in line["score"])
        assert all(list(game) == ["c1", "c2", "at"]
                   for game in line["games"])
        # A record is written once it has a game: its first write's time
        # (started_at, and the id) is a game's end or later.
        assert len(line["games"]) >= 1
        assert line["games"][0]["at"] >= line["started_at"]
        stamp = datetime.fromisoformat(line["started_at"]).strftime(
            "%Y%m%d-%H%M%S")
        assert line["id"].startswith(stamp + "-")
    assert any(line["closed"] for line in lines)
    assert any(line["set_id"] is None for line in lines)


def test_every_hook_has_a_golden_input_for_its_sample_run():
    rows = {row["type"]: row for row in CONTRACT["field_types"]["source"]}
    for row in rows.values():
        if "golden" in row:
            assert (PLUGINS / row["golden"]).is_file(), row["golden"]
    from src.core import PluginHooks
    for event, kind in T.HOOK_FILLS.items():
        golden = rows[kind.value].get("golden")
        assert golden, kind
        # The dispatcher's sample run reads the same file.
        assert PluginHooks.SAMPLE_FILES[event] == Path(golden).name
    image = (INPUTS / "result_image.png").read_bytes()
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    # A stand-in: small, of an accepted image extension.
    assert len(image) <= 4096
    assert "png" in T.IMAGE_ARTIFACT_EXTENSIONS


def test_the_match_record_golden_is_a_decided_line_of_the_log():
    record = _load(INPUTS / "match_record.json")
    assert record == _record_line(record)
    assert record["closed"] is False
    assert record in _log_lines()
    # Decided: a best-of-5's majority.
    assert max(record["score"]) >= 5 // 2 + 1


# KEY_CATALOG keys whose value is a dict the catalog does not enumerate.
DICT_VALUED = {"score.{N}.team.{T}.player.{M}.country",
               "score.{N}.team.{T}.player.{M}.state",
               "score.{N}.team.{T}.player.{M}.character.{K}"}


def _catalog():
    keys = []
    for root, entries in StateSchema.KEY_CATALOG.items():
        keys += [f"{root}.{entry}" for entry in entries]
    return keys


def _matches(pattern, segments):
    parts = pattern.split(".")
    if len(parts) > len(segments):
        return False
    for part, segment in zip(parts, segments):
        if not (part == segment or re.fullmatch(r"\{[A-Za-z]+\}", part)):
            return False
    return len(parts) == len(segments) or pattern in DICT_VALUED


def _leaves(node, prefix=()):
    if isinstance(node, dict) and node:
        for key, value in node.items():
            yield from _leaves(value, prefix + (key,))
    else:
        yield prefix


def test_the_state_golden_is_made_of_catalog_keys():
    state = _load(INPUTS / "state.json")
    catalog = _catalog()
    for leaf in _leaves(state):
        assert any(_matches(key, list(leaf)) for key in catalog), \
            ".".join(leaf)
    # The same match as the record golden.
    record = _load(INPUTS / "match_record.json")
    board = state["score"]["1"]
    assert board["team"]["1"]["player"]["1"]["name"] == record["p1"]
    assert board["team"]["2"]["player"]["1"]["name"] == record["p2"]
    assert [board["team"]["1"]["score"], board["team"]["2"]["score"]] \
        == record["score"]
    assert board["phase"] == record["phase"]
    assert board["match"] == record["round"]
    assert board["set_id"] == record["set_id"]
    assert state["tournamentInfo"]["displayTournamentName"] \
        == record["tournament"]
    assert state["game"]["codename"] == record["game"]
    assert board["first_to"] == board["best_of"] // 2 + 1


# ── the fake worker ────────────────────────────────────────────────────

EVENTS = {row["type"]: row for row in CONTRACT["events"]["types"]}


def _text_ok(value, limit):
    if isinstance(value, str):
        return len(value) <= limit
    return (isinstance(value, dict) and value
            and all(isinstance(text, str) and len(text) <= limit
                    for text in value.values()))


def _field_ok(value, spec):
    if value is None:
        return bool(spec.get("nullable"))
    kind = spec["type"]
    if kind == "text":
        return _text_ok(value, spec.get("max_length", 10 ** 9))
    if kind == "string":
        if not isinstance(value, str):
            return False
        if "pattern" in spec and not re.search(spec["pattern"], value):
            return False
        return len(value) <= spec.get("max_length", 10 ** 9)
    if kind in ("integer", "number"):
        if not _is_type(value, kind):
            return False
        return (spec.get("minimum", float("-inf")) <= value
                <= spec.get("maximum", float("inf")))
    if kind == "array<artifact>":
        return isinstance(value, list)
    raise AssertionError(kind)


def _artifact_ok(item):
    fields = CONTRACT["artifacts"]["fields"]
    return (isinstance(item, dict) and isinstance(item.get("path"), str)
            and item.get("kind") in fields["kind"]["values"]
            and (item.get("label") is None
                 or _text_ok(item["label"], fields["label"]["max_length"])))


class _Read:
    """A run's stdout as the contract reads it."""

    def __init__(self, stdout):
        self.events = []
        self.log = []
        self.bad = []
        limit = CONTRACT["events"]["line_max_bytes"]
        for line in stdout.split(b"\n"):
            if not line.strip():
                continue
            if len(line) > limit:
                self.log.append("too long")
                continue
            text = line.decode("utf-8", errors="replace")
            try:
                event = json.loads(text)
            except ValueError:
                self.log.append(text)
                continue
            if not isinstance(event, dict) or event.get("type") not in EVENTS:
                self.log.append(text)
                continue
            self.events.append(event)
            spec = EVENTS[event["type"]]["fields"]
            for name, field_spec in spec.items():
                if name not in event:
                    if field_spec["required"]:
                        self.bad.append((event["type"], name, "missing"))
                    continue
                if not _field_ok(event[name], field_spec):
                    self.bad.append((event["type"], name, event[name]))
            for item in event.get("artifacts") or []:
                if not _artifact_ok(item):
                    self.bad.append(("artifact", item))
            # An ignored key (id) is not a break: the line is taken.
            unknown = (set(event) - set(spec) - {"type"}
                       - set(CONTRACT["events"]["ignored_keys"]))
            if unknown:
                self.bad.append((event["type"], "unknown", sorted(unknown)))

    @property
    def final(self):
        terminals = [event for event in self.events
                     if EVENTS[event["type"]]["terminal"]]
        return terminals[-1] if terminals else None


def _env(**extra):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("FAKE_PLUGIN_")}
    env.update({key: str(value) for key, value in extra.items()})
    return env


def _request(tmp_path, params=None, **fields):
    job_dir = tmp_path / "20261126-213000-a1b2"
    (job_dir / "out").mkdir(parents=True, exist_ok=True)
    request = {
        "protocol": 1, "plugin": "example.fake", "job": "post",
        "run_id": job_dir.name,
        "trigger": {"kind": "manual"}, "locale": "ja",
        "params": params or {}, "settings": {}, "inputs": {},
        "out_dir": str(job_dir / "out"),
        "data_dir": str(tmp_path / "data"),
        "host": {"name": "Autona", "version": "2.6.0"},
    }
    request.update(fields)
    path = job_dir / "request.json"
    path.write_text(json.dumps(request, ensure_ascii=False),
                    encoding="utf-8")
    return path


def _start(tmp_path, mode, **env):
    request = _request(tmp_path)
    return subprocess.Popen(
        [sys.executable, str(FAKE), "run", str(request)],
        cwd=str(request.parent), env=_env(FAKE_PLUGIN_MODE=mode, **env),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)


def _run(tmp_path, mode, stdin=None, **env):
    """Run a mode to its end: (exit code, _Read, stderr). stdin stays open
    unless `stdin` is given (written, then closed)."""
    process = _start(tmp_path, mode, **env)
    if stdin is None:
        stdout, stderr = _wait_open(process)
    else:
        stdout, stderr = process.communicate(stdin, timeout=20)
    return process.returncode, _Read(stdout), stderr


def _wait_open(process, timeout=20):
    """Read to the end with stdin kept open (the host's normal run)."""
    import threading
    chunks = {}

    def read(name, stream):
        chunks[name] = stream.read()

    readers = [threading.Thread(target=read, args=(name, stream))
               for name, stream in (("out", process.stdout),
                                    ("err", process.stderr))]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout)
    finally:
        process.stdin.close()
    for reader in readers:
        reader.join(5)
    return chunks.get("out", b""), chunks.get("err", b"")


class _Stdout:
    """A process's stdout read by one thread only (no second reader races
    it): `going()` waits for the first event line (the stage), `all()`
    joins the thread once the process has ended and returns every line."""

    def __init__(self, process):
        import threading
        self._lines = []
        self._going = threading.Event()
        self._thread = threading.Thread(
            target=self._read, args=(process.stdout,), daemon=True)
        self._thread.start()

    def _read(self, stream):
        for line in stream:
            self._lines.append(line)
            if line.startswith(b"{"):
                self._going.set()
        self._going.set()

    def going(self, timeout=10):
        assert self._going.wait(timeout), "the fake worker printed nothing"

    def all(self, timeout=10):
        self._thread.join(timeout)
        assert not self._thread.is_alive(), "stdout did not close"
        return b"".join(self._lines)


def _kill(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _assert_valid(read):
    assert read.bad == [], read.bad
    assert read.events and read.events[0]["type"] == "stage"


def test_ok_prints_every_kind_of_event_and_ends_done(tmp_path):
    code, read, stderr = _run(tmp_path, "ok")
    _assert_valid(read)
    assert [event["type"] for event in read.events] == [
        "stage", "progress", "log", "warning", "stage", "progress", "done"]
    assert _end(read.final["type"], code) == "done"
    # The two lines for worker.log.
    assert read.log and b"stderr line" in stderr
    out = tmp_path / "20261126-213000-a1b2" / "out"
    for item in read.final["artifacts"]:
        assert (out / item["path"]).is_file()
    image = out / "result.png"
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_error_ends_error(tmp_path):
    code, read, _ = _run(tmp_path, "error")
    _assert_valid(read)
    assert read.final["type"] == "error" and code == 2
    assert _end("error", code) == "error"


def test_crash_ends_failed(tmp_path):
    code, read, stderr = _run(tmp_path, "crash")
    _assert_valid(read)
    assert read.final is None and code == 1
    assert b"Traceback" in stderr
    assert _end(None, code) == "failed"


@pytest.mark.parametrize("stdin", [b"cancel\n", b""])
def test_slow_stops_on_cancel_or_eof(tmp_path, stdin):
    process = _start(tmp_path, "slow")
    stdout = _Stdout(process)
    stdout.going()
    process.stdin.write(stdin)
    process.stdin.close()
    process.wait(10)
    read = _Read(stdout.all())
    _assert_valid(read)
    assert read.final == {"type": "cancelled"}
    assert process.returncode == 130
    assert _end("cancelled", 130) == "cancelled"


def test_slow_ignores_other_stdin_lines(tmp_path):
    process = _start(tmp_path, "slow")
    _Stdout(process).going()
    process.stdin.write(b"hello\n")
    process.stdin.flush()
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(0.4)
    process.stdin.write(b"cancel\n")
    process.stdin.close()
    assert process.wait(10) == 130


def test_hang_answers_no_cancel_and_its_child_lives(tmp_path):
    child_file = tmp_path / "child.pid"
    process = _start(tmp_path, "hang", FAKE_PLUGIN_CHILD_PID=child_file)
    try:
        _Stdout(process).going()
        process.stdin.write(b"cancel\n")
        process.stdin.close()
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(0.5)
        assert int(child_file.read_text(encoding="utf-8")) > 0
    finally:
        process.kill()
        process.wait(10)
        if child_file.exists():
            _kill(int(child_file.read_text(encoding="utf-8")))
    # Killed with no terminal line: failed, or cancelled after a cancel.
    assert _end(None, process.returncode) == "failed"
    assert _end(None, process.returncode, cancel_requested=True) \
        == "cancelled"


def test_grandchild_ends_done_while_its_child_runs(tmp_path):
    child_file = tmp_path / "child.pid"
    try:
        code, read, _ = _run(tmp_path, "grandchild",
                             FAKE_PLUGIN_CHILD_PID=child_file)
        _assert_valid(read)
        assert _end(read.final["type"], code) == "done"
        assert int(child_file.read_text(encoding="utf-8")) > 0
    finally:
        if child_file.exists():
            _kill(int(child_file.read_text(encoding="utf-8")))


def test_done_after_cancel_ends_done(tmp_path):
    process = _start(tmp_path, "done_after_cancel")
    stdout = _Stdout(process)
    stdout.going()
    process.stdin.write(b"cancel\n")
    process.stdin.close()
    process.wait(10)
    read = _Read(stdout.all())
    _assert_valid(read)
    assert read.final["type"] == "done" and process.returncode == 0
    # The cancel was asked: still done (finished before the cancel).
    assert _end("done", 0, cancel_requested=True) == "done"


def test_timeout_finishes_late_or_stops_on_cancel(tmp_path):
    code, read, _ = _run(tmp_path / "late", "timeout", FAKE_PLUGIN_DELAY=0.2)
    _assert_valid(read)
    assert read.final["type"] == "done" and code == 0
    process = _start(tmp_path / "cut", "timeout", FAKE_PLUGIN_DELAY=30)
    _Stdout(process).going()
    process.stdin.write(b"cancel\n")
    process.stdin.close()
    assert process.wait(10) == 130


@pytest.mark.parametrize("final", ["done", "error", "cancelled", "none"])
def test_combo_reaches_every_row_of_the_end_table(tmp_path, final):
    for exit_code in (0, 2, 130, 1):
        code, read, _ = _run(tmp_path / str(exit_code), "combo",
                             FAKE_PLUGIN_FINAL=final,
                             FAKE_PLUGIN_EXIT=exit_code)
        _assert_valid(read)
        assert code == exit_code
        assert (read.final or {}).get("type", "none") == final
        assert _end(read.final and read.final["type"], code) in \
            CONTRACT["end"]["states"]


def test_noise_is_what_the_host_sends_to_worker_log(tmp_path):
    code, read, _ = _run(tmp_path, "noise")
    assert code == 0 and read.final["type"] == "done"
    # Unknown type, no type, not an object (2), the long line, the invalid
    # bytes -- plus the leading "not JSON" line.
    assert len(read.log) == 7
    # A line with the ignored id key is taken as its type says.
    assert {"type": "log", "message": "reserved", "id": 7} in read.events
    # The events kept carry exactly the breaks the host drops.
    assert ("progress", "fraction", 7) in read.bad
    assert ("progress", "overall", "half") in read.bad
    assert ("progress", "eta", -1) in read.bad
    assert ("stage", "index", "two") in read.bad
    assert {entry[1] for entry in read.bad if entry[1] == "unknown"}


def test_limits_go_past_every_limit(tmp_path):
    code, read, _ = _run(tmp_path, "limits")
    assert code == 0
    warnings = [e for e in read.events if e["type"] == "warning"]
    assert len(warnings) > T.MAX_WARNINGS
    assert len(read.final["artifacts"]) > T.MAX_ARTIFACTS
    assert ("done", "summary", "s" * 600) in read.bad
    assert ("log", "message", "m" * 3000) in read.bad


def test_bad_code_breaks_the_error_code_pattern(tmp_path):
    code, read, _ = _run(tmp_path, "bad_code")
    assert code == 2
    assert read.bad == [("error", "code", "Bad-Code!")]


def test_artifacts_holds_good_and_bad_ones(tmp_path):
    code, read, _ = _run(tmp_path, "artifacts")
    assert code == 0
    out = tmp_path / "20261126-213000-a1b2" / "out"
    entries = read.final["artifacts"]
    good = [e for e in entries if e.get("label")]
    assert {e["kind"] for e in good} == set(_values(T.ArtifactKind))
    assert all((out / e["path"]).is_file() for e in good)
    paths = [e.get("path") for e in entries]
    assert "../outside.txt" in paths and "missing.txt" in paths
    assert any(path and os.path.isabs(path) for path in paths)
    assert ("artifact", {"path": "page.html", "kind": "html"}) in read.bad
    assert ("artifact", {"kind": "text"}) in read.bad
    assert (out / "big.txt").stat().st_size > T.TEXT_ARTIFACT_MAX_BYTES


def test_stdin_open_tells_open_closed_and_cancel(tmp_path):
    code, read, _ = _run(tmp_path / "open", "stdin_open")
    assert code == 0 and read.final["type"] == "done"
    code, read, _ = _run(tmp_path / "closed", "stdin_open", stdin=b"")
    assert code == 2 and read.final["code"] == "stdin_closed"
    code, read, _ = _run(tmp_path / "cancel", "stdin_open", stdin=b"cancel\n")
    assert code == 130 and read.final == {"type": "cancelled"}


def test_echo_writes_what_it_was_given(tmp_path):
    code, read, _ = _run(tmp_path, "echo", AUTONA_SECRET_TOKEN="s3cret",
                         AUTONA_PLUGIN_DIR=str(tmp_path))
    _assert_valid(read)
    assert code == 0
    out = tmp_path / "20261126-213000-a1b2" / "out"
    echo = json.loads((out / "echo.json").read_text(encoding="utf-8"))
    assert echo["argv"][0].endswith("request.json")
    assert Path(echo["cwd"]) == out.parent
    assert echo["request"]["run_id"] == out.parent.name
    assert set(echo["request"]) == set(CONTRACT["request"]["fields"])
    names = {key.upper() for key in echo["env"]}
    assert "AUTONA_SECRET_TOKEN" in names and "AUTONA_PLUGIN_DIR" in names


def test_the_mode_can_come_from_the_request(tmp_path):
    request = _request(tmp_path, params={"mode": "error"})
    result = subprocess.run(
        [sys.executable, str(FAKE), "run", str(request)],
        env=_env(FAKE_PLUGIN_MODE="ok"), input=b"", capture_output=True,
        timeout=20)
    assert result.returncode == 2
    assert _Read(result.stdout).final["type"] == "error"


# ── describe ───────────────────────────────────────────────────────────

def _describe(tmp_path, mode="ok", manifest=None):
    folder = tmp_path / "plugin"
    folder.mkdir(exist_ok=True)
    if manifest is not None:
        # utf-8-sig: a BOM from Notepad is accepted.
        (folder / "plugin.json").write_text(json.dumps(manifest),
                                            encoding="utf-8-sig")
    result = subprocess.run(
        [sys.executable, str(FAKE), "describe"], cwd=str(folder),
        env=_env(FAKE_PLUGIN_DESCRIBE=mode, AUTONA_PLUGIN_DIR=folder),
        stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    answers = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("type") == "describe":
            answers.append(value)
    return result.returncode, answers


def _answer_problems(answer, manifest):
    spec = CONTRACT["commands"]["describe"]["answer"]
    problems = [name for name, item in spec.items()
                if item["required"] and name not in answer]
    if "protocol" in answer and not set(answer["protocol"]) & set(
            CONTRACT["versions"]["host_speaks_protocols"]):
        problems.append("no_common_protocol")
    for name in ("id", "version"):
        if answer.get(name) != manifest[name]:
            problems.append(f"{name}_mismatch")
    return problems


def test_describe_answers_the_manifests_id_and_version(tmp_path):
    manifest = _example()
    code, answers = _describe(tmp_path, manifest=manifest)
    assert code == 0 and len(answers) == 1
    assert _answer_problems(answers[0], manifest) == []
    assert answers[0]["id"] == "taguyan.webhook"


@pytest.mark.parametrize("mode, problem", [
    ("wrong_id", "id_mismatch"),
    ("wrong_version", "version_mismatch"),
    ("no_protocol", "protocol"),
    ("protocol_2", "no_common_protocol"),
])
def test_describe_breaks_as_asked(tmp_path, mode, problem):
    manifest = _example()
    code, answers = _describe(tmp_path, mode, manifest)
    assert code == 0 and len(answers) == 1
    assert _answer_problems(answers[0], manifest) == [problem]


@pytest.mark.parametrize("mode, code, count", [
    ("not_json", 0, 0), ("no_line", 0, 0), ("noisy", 0, 1), ("exit_1", 1, 1),
])
def test_describe_lines_and_exit(tmp_path, mode, code, count):
    got, answers = _describe(tmp_path, mode, _example())
    assert (got, len(answers)) == (code, count)


def test_describe_without_a_manifest_uses_its_defaults(tmp_path):
    code, answers = _describe(tmp_path)
    assert code == 0
    assert answers == [{"type": "describe", "protocol": [1],
                        "id": "example.fake", "version": "1.0.0"}]
    assert re.fullmatch(T.PLUGIN_ID_PATTERN, answers[0]["id"])
