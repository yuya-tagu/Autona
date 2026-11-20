"""Overlay contract 1 (ADR-008 D1): layout/include/autona.js and its table.

``layout/include/overlay_contract.json`` is the table of the view autona.js
hands a layout: every key, its type, its value when the state holds none and
the state keys it comes from. This module holds the two to each other and to
the state:

- every source path of the table is a key of ``StateSchema.KEY_CATALOG``, or
  a field of one that holds a dict (DICT_VALUED), so renaming a state key
  fails here before it silently breaks a third party's overlay; the sample
  states of ``layout/include/sample/`` are held to the catalog the same way;
- autona.js runs under node's ``vm`` with the real ``globals.js`` (the
  transport, minus its library loading) on the samples and the edge cases of
  ``tests/fixtures/overlay``; what each case expects of the view
  (``tests/fixtures/overlay/views.json``: per case only the parts it is
  about) is what it gets, every view has exactly the table's keys and types,
  and the empty state gives every key the table's ``empty``;
- a delta (globals.js's own ``HandleTSHStateUpdateMessage``) calls onUpdate
  again, one the view does not show does not, and a layout that defines the
  global ``Update`` (as the shipped ones do) still gets onUpdate;
- ``?autonaSample=NAME`` draws the sample instead of the app's state.

views.json is written by hand-checked runs, not derived: when autona.js
changes what it hands out, views.json changes in the same diff, and contract
1 only allows keys to be added.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.core import StateSchema, Top8Model

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INCLUDE = PROJECT_ROOT / "layout" / "include"
GLOBALS = INCLUDE / "globals.js"
AUTONA = INCLUDE / "autona.js"
CONTRACT = INCLUDE / "overlay_contract.json"
SAMPLES = INCLUDE / "sample"
EMPTY_SAMPLE = "layout/include/sample/empty.json"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "overlay"
VIEWS = FIXTURES / "views.json"
GROUPS = ("board", "tournament", "commentary", "top8", "options")

TYPES = {"string": str, "number": (int, float), "boolean": bool,
         "array": list}
EMPTY_BY_TYPE = {"string": "", "number": None, "boolean": False, "array": []}

# KEY_CATALOG keys whose value is a dict the catalog does not enumerate:
# a path below one of them is a field of that dict (a country's asset, a
# character's assets), and is as much the state's as the key itself.
DICT_VALUED = frozenset({
    "score.{N}.recent_sets",
    "score.{N}.upset_factor",
    "score.{N}.last_sets.{T}.{I}",
    "score.{N}.history_sets.{T}.{I}",
    "score.{N}.station_queue",
    "score.{N}.team.{T}.player.{M}.country",
    "score.{N}.team.{T}.player.{M}.state",
    "score.{N}.team.{T}.player.{M}.character.{K}",
    "score.{N}.team.{T}.player.{M}.mains",
    "top8.players.{I}.country",
    "top8.players.{I}.characters.{K}",
})

# Runs scenarios, each in a fresh vm context that is a page as far as
# globals.js and autona.js can tell: a document (an EventTarget), the
# location, setTimeout, and stubs for what globals.js builds at the top
# level (ResizeObserver) or calls (io, gsap). LoadEverything is replaced by
# a stub that does what InitAll does for autona.js: calls
# UpdateData_SocketIO() and fires tsh_init. argv: globals.js, autona.js,
# the layout folder, the scenarios' JSON file.
_HARNESS = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const [globalsPath, autonaPath, layoutRoot, scenariosPath] = process.argv.slice(1);
const globalsSource = fs.readFileSync(globalsPath, "utf8");
const autonaSource = fs.readFileSync(autonaPath, "utf8");
const scenarios = JSON.parse(fs.readFileSync(scenariosPath, "utf8"));
const tick = () => new Promise((resolve) => setTimeout(resolve, 30));

function page(scenario) {
  const errors = [];
  const fetched = [];
  const document = new EventTarget();
  document.readyState = "complete";
  const sandbox = {
    console: {log() {}, info() {}, debug() {},
              warn: (...args) => errors.push(["warn", args.map(String).join(" ")]),
              error: (...args) => errors.push(["error", args.map(String).join(" ")])},
    setTimeout, clearTimeout, setInterval, clearInterval,
    URLSearchParams, EventTarget, Event, CustomEvent, document,
    location: {search: scenario.search || "", protocol: "http:",
               host: "127.0.0.1:5107",
               pathname: scenario.pathname || "/layout/my_overlay/index.html"},
    ResizeObserver: class { observe() {} },
    io: () => ({on() {}, emit() {}, io: {on() {}}}),
    gsap: {globalTimeline: {timeScale: () => 1}},
    fetch: async (url) => {
      fetched.push(url);
      const match = /^\/layout\/(.+)$/.exec(url);
      const file = match && path.join(layoutRoot, ...match[1].split("/"));
      if (!file || !fs.existsSync(file)) return {ok: false, json: async () => null};
      return {ok: true, json: async () => JSON.parse(fs.readFileSync(file, "utf8"))};
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(globalsSource, sandbox, {filename: globalsPath});
  sandbox.tsh_settings = Object.assign(
    {}, scenario.settings || {}, scenario.kind ? {autona: {kind: scenario.kind}} : {});
  let loads = 0;
  sandbox.LoadEverything = async () => {
    loads += 1;
    await sandbox.UpdateData_SocketIO();
    document.dispatchEvent(new CustomEvent("tsh_init"));
  };
  // A page's own transport: a full state as globals.js's program_state
  // handler takes it.
  vm.runInContext(`
    function __fullState(state, revision) {
      oldData = data;
      data = state;
      maxAppliedDeltaIdx = revision;
      const event = new CustomEvent("tsh_update");
      event.data = data;
      event.oldData = oldData;
      document.dispatchEvent(event);
    }`, sandbox);
  if (scenario.layout) vm.runInContext(scenario.layout, sandbox);
  vm.runInContext(autonaSource, sandbox, {filename: autonaPath});
  if (scenario.afterAutona) vm.runInContext(scenario.afterAutona, sandbox);
  const calls = [];
  if (sandbox.Autona) {
    sandbox.Autona.onUpdate((view, prev) => {
      calls.push({view: JSON.parse(JSON.stringify(view)),
                  prev: prev === null ? null : JSON.parse(JSON.stringify(prev)),
                  frozen: Object.isFrozen(view) && Object.isFrozen(view.board) &&
                    Object.isFrozen(view.board.p1.players) &&
                    Object.isFrozen(view.top8.matches.gf.p1.country) &&
                    (prev === null || Object.isFrozen(prev)),
                  rawIsState: sandbox.Autona.raw === sandbox.data});
    });
  }
  return {sandbox, errors, fetched, calls, loads: () => loads};
}

async function run(scenario) {
  const p = page(scenario);
  await tick();
  for (const step of scenario.steps || []) {
    if (step.state !== undefined) p.sandbox.__fullState(step.state, step.revision || 0);
    if (step.delta) p.sandbox.HandleTSHStateUpdateMessage(step.delta);
    if (step.run) vm.runInContext(step.run, p.sandbox);
    await tick();
  }
  return {
    calls: p.calls,
    errors: p.errors,
    fetched: p.fetched,
    loads: p.loads(),
    autona: p.sandbox.Autona
      ? {contract: p.sandbox.Autona.contract, keys: Object.keys(p.sandbox.Autona)}
      : null,
    out: p.sandbox.__out === undefined ? null : p.sandbox.__out,
  };
}

(async () => {
  const results = [];
  for (const scenario of scenarios) results.push(await run(scenario));
  process.stdout.write(JSON.stringify(results));
})().catch((error) => { console.error(error); process.exit(1); });
"""


def run_scenarios(tmp_path, scenarios):
    spec = tmp_path / "scenarios.json"
    spec.write_text(json.dumps(scenarios, ensure_ascii=False),
                    encoding="utf-8")
    result = subprocess.run(
        [NODE, "-e", _HARNESS, str(GLOBALS), str(AUTONA),
         str(PROJECT_ROOT / "layout"), str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def contract():
    return read_json(CONTRACT)


def view_cases():
    return read_json(VIEWS)["cases"]


def view_scenario(case):
    return {key: case[key] for key in ("search", "kind", "settings", "pathname")
            if key in case} | {
        "steps": [{"state": read_json(PROJECT_ROOT / case["state"])}]}


def concrete_keys(table):
    """The table's keys with {side} / {match} spelled out: key -> entry."""
    keys = {}
    for entry in table["keys"]:
        expanded = [entry["key"]]
        for placeholder, values in table["placeholders"].items():
            if any(placeholder in key for key in expanded):
                expanded = [key.replace(placeholder, value)
                            for key in expanded for value in values]
        for key in expanded:
            keys[key] = entry
    return keys


def view_paths(value, path=""):
    """(path, value) of every leaf and array in a view; an array's elements
    are under ``path + "[]"``."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from view_paths(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        yield path, value
        for child in value:
            yield from view_paths(child, path + "[]")
    else:
        yield path, value


def table_empty_paths():
    """What the empty state's view must be, flattened: every key outside an
    array at its ``empty``."""
    return {key: entry["empty"] for key, entry in concrete_keys(
        contract()).items() if "[]" not in key}


# ── the table and the samples against the state's catalog ────────────────
def catalog_paths():
    paths = set()
    for root, keys in StateSchema.KEY_CATALOG.items():
        paths.update(f"{root}.{key}" for key in keys)
    return paths


def test_the_dict_valued_keys_are_catalog_keys():
    assert DICT_VALUED <= catalog_paths()


def test_every_source_is_a_state_key_of_the_catalog():
    # A source names a KEY_CATALOG path, or a field of a dict one of
    # DICT_VALUED holds (country.asset, character.{K}.assets). Nothing below
    # a key that holds a plain value, nor below a root the catalog does not
    # enumerate.
    paths = catalog_paths()
    unknown = []
    for entry in contract()["keys"]:
        assert entry["source"], entry["key"]
        for source in entry["source"]:
            if source.startswith("url:"):
                assert source == "url:scoreboardNumber", entry["key"]
                continue
            if source in paths or any(
                    source.startswith(path + ".") for path in DICT_VALUED):
                continue
            unknown.append((entry["key"], source))
    assert unknown == []


def segments_match(pattern, path):
    return len(pattern) == len(path) and all(
        p == s or (p.startswith("{") and p.endswith("}"))
        for p, s in zip(pattern, path, strict=True))


def sample_leaves(value, path=()):
    """(path segments, value) of a state's leaves: scalars, lists and empty
    dicts."""
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from sample_leaves(child, (*path, key))
    else:
        yield path, value


@pytest.mark.parametrize("name", ["singles", "teams"])
def test_every_key_of_a_sample_is_a_state_key_of_the_catalog(name):
    # The samples stand in for the app's state: a key the state does not
    # have would show a layout something the app never sends.
    patterns = [tuple(path.split(".")) for path in catalog_paths()]
    dicts = [tuple(path.split(".")) for path in DICT_VALUED]
    unknown = []
    for path, value in sample_leaves(read_json(SAMPLES / f"{name}.json")):
        if any(segments_match(pattern, path) for pattern in patterns):
            continue
        if any(segments_match(pattern, path[:len(pattern)])
               for pattern in dicts if len(path) > len(pattern)):
            continue
        # An empty dict standing for a family not filled in (sponsor_logos,
        # a board's last_sets.1).
        if value == {} and any(segments_match(pattern[:len(path)], path)
                               for pattern in patterns
                               if len(pattern) > len(path)):
            continue
        unknown.append(".".join(path))
    assert unknown == []


def test_the_table_is_well_formed():
    table = contract()
    assert table["contract"] == 1
    assert table["description"]["ja"] and table["description"]["en"]
    assert table["placeholders"]["{match}"] == Top8Model.MATCH_IDS
    keys = [entry["key"] for entry in table["keys"]]
    assert len(keys) == len(set(keys))
    for entry in table["keys"]:
        assert set(entry) == {"key", "type", "empty", "source", "ja", "en"}
        assert re.fullmatch(r"[a-z][A-Za-z0-9]*(\[\])?(\.([a-z][A-Za-z0-9]*|"
                            r"\{side\}|\{match\})(\[\])?)*", entry["key"]), \
            entry["key"]
        assert entry["type"] in TYPES, entry["key"]
        assert entry["ja"].strip() and entry["en"].strip(), entry["key"]
        empty = entry["empty"]
        if entry["key"] == "board.number" or entry["key"].startswith(
                "options."):
            # Defaults, not empties: the URL's board and the display options.
            assert isinstance(empty, TYPES[entry["type"]]), entry["key"]
        else:
            assert empty == EMPTY_BY_TYPE[entry["type"]], entry["key"]
    # An array's elements are listed under it and nothing else.
    arrays = {entry["key"] for entry in table["keys"]
              if entry["type"] == "array"}
    for key in keys:
        for index in [m.start() for m in re.finditer(r"\[\]", key)]:
            assert key[:index] in arrays, key


def test_the_options_are_the_display_options_with_their_defaults():
    def camel(key):
        head, *rest = key.split("_")
        return head + "".join(part.title() for part in rest)

    options = {entry["key"]: (entry["empty"], entry["source"])
               for entry in contract()["keys"]
               if entry["key"].startswith("options.")}
    assert options == {
        f"options.{camel(key)}": (default, [f"display_options.{key}"])
        for key, default in StateSchema.DISPLAY_OPTION_DEFAULTS.items()}


def test_every_sample_is_a_case_and_each_expects_only_view_parts():
    cases = view_cases()
    named = {case["state"] for case in cases} | {EMPTY_SAMPLE}
    assert {f"layout/include/sample/{path.name}"
            for path in SAMPLES.glob("*.json")} <= named
    # The empty sample's view is the table's empties (below), not a copy.
    assert EMPTY_SAMPLE not in {case["state"] for case in cases}
    for case in cases:
        assert case["expect"] and set(case["expect"]) <= set(GROUPS), \
            case["name"]
    # The groups that do not depend on the board or the kind once per state.
    for state in {case["state"] for case in cases}:
        for group in ("commentary", "top8", "options"):
            assert sum(group in case["expect"] for case in cases
                       if case["state"] == state) == 1, (state, group)


# ── autona.js on the cases ───────────────────────────────────────────────
@pytest.fixture(scope="module")
def case_views(tmp_path_factory):
    """Every case's run, and the empty sample's: (name, result)."""
    if NODE is None:
        pytest.skip("node is not installed")
    cases = view_cases()
    scenarios = [view_scenario(case) for case in cases] + [
        {"steps": [{"state": read_json(PROJECT_ROOT / EMPTY_SAMPLE)}]}]
    results = run_scenarios(tmp_path_factory.mktemp("views"), scenarios)
    return list(zip([case["name"] for case in cases] + ["empty"], results,
                    strict=True))


def test_each_case_gets_what_it_expects(case_views):
    expected = {case["name"]: case["expect"] for case in view_cases()}
    for name, result in case_views:
        assert result["errors"] == [], name
        assert len(result["calls"]) == 1, name
        call = result["calls"][0]
        assert call["prev"] is None and call["rawIsState"] and call["frozen"]
        if name in expected:
            assert {group: call["view"][group]
                    for group in expected[name]} == expected[name], name


def test_every_view_has_exactly_the_tables_keys_and_types(case_views):
    table = contract()
    keys = concrete_keys(table)
    top = {key for key in keys if "[]" not in key}
    seen = set()
    for name, result in case_views:
        view = result["calls"][0]["view"]
        assert set(view) == set(GROUPS)
        for path, value in view_paths(view):
            assert path in keys, (name, path)
            entry = keys[path]
            if value != entry["empty"]:
                assert isinstance(value, TYPES[entry["type"]]) and not (
                    entry["type"] == "number" and isinstance(value, bool)), \
                    (name, path, value)
            seen.add(entry["key"])
        # A key outside any array is always there.
        assert top <= {path for path, _ in view_paths(view)}, name
    # ... and the cases between them show every key of the table.
    assert seen == {entry["key"] for entry in table["keys"]}


def test_the_empty_state_gives_every_key_its_empty_value(case_views):
    [result] = [result for name, result in case_views if name == "empty"]
    assert dict(view_paths(result["calls"][0]["view"])) == \
        table_empty_paths()


# ── autona.js's behaviour ────────────────────────────────────────────────
def singles():
    return read_json(SAMPLES / "singles.json")


@needs_node
def test_a_delta_calls_onupdate_and_one_the_view_does_not_show_does_not(
        tmp_path):
    [result] = run_scenarios(tmp_path, [{"kind": "scoreboard", "steps": [
        {"state": singles(), "revision": 4},
        {"delta": {"revision": 5, "delta": [
            {"path": ["score", "1", "team", "1", "score"],
             "action": "values_changed", "value": 3}]}},
        # Not in the view: no call.
        {"delta": {"revision": 6, "delta": [
            {"path": ["score", "1", "set_id"], "action": "values_changed",
             "value": "12345"}]}},
        {"delta": {"revision": 7, "delta": [
            {"path": ["score", "1", "team", "2", "player", "1", "name"],
             "action": "values_changed", "value": "Bravo!"}]}},
    ]}])
    assert result["errors"] == []
    scores = [(call["view"]["board"]["p1"]["score"],
               call["prev"] and call["prev"]["board"]["p1"]["score"],
               call["view"]["board"]["p2"]["name"])
              for call in result["calls"]]
    assert scores == [(2, None, "Bravo"), (3, 2, "Bravo"), (3, 3, "Bravo!")]
    assert all(call["rawIsState"] and call["frozen"]
               for call in result["calls"])


@needs_node
def test_a_layout_that_defines_update_still_gets_onupdate(tmp_path):
    # As a layout copied from the shipped ones would: Update replaced at the
    # top level, and globals.js's own listener (InitAll adds it) calling it.
    layout = """
      var __out = 0;
      Update = async (event) => { __out += 1; };
      document.addEventListener("tsh_update", UpdateWrapper);
    """
    [result] = run_scenarios(tmp_path, [{"layout": layout, "steps": [
        {"state": singles(), "revision": 1},
        {"delta": {"revision": 2, "delta": [
            {"path": ["score", "1", "phase"], "action": "values_changed",
             "value": "TOP 6"}]}},
    ]}])
    assert result["errors"] == []
    assert result["out"] == 2
    assert [call["view"]["board"]["phase"] for call in result["calls"]] == [
        "TOP 8", "TOP 6"]


@needs_node
def test_a_listener_added_after_the_first_update_gets_the_view(tmp_path):
    [result] = run_scenarios(tmp_path, [{"steps": [
        {"state": singles()},
        {"run": """
          Autona.onUpdate((view, prev) => {
            globalThis.__out = [view.board.p1.name, prev];
          });
        """},
    ]}])
    assert result["out"] == ["Alpha", None]


@needs_node
def test_the_function_onupdate_returns_takes_the_listener_off(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "afterAutona": """
          var __out = [];
          var off = Autona.onUpdate((view) => { __out.push(view.board.p1.score); });
        """,
        "steps": [
            {"state": singles(), "revision": 1},
            {"run": "off(); off();"},
            {"delta": {"revision": 2, "delta": [
                {"path": ["score", "1", "team", "1", "score"],
                 "action": "values_changed", "value": 3}]}},
        ]}])
    assert result["out"] == [2]
    # ... the others stay on.
    assert len(result["calls"]) == 2


@needs_node
def test_the_view_cannot_be_changed_by_a_listener(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "afterAutona": """
          var __out = null;
          Autona.onUpdate((view) => {
            view.board.p1.name = "changed";
            view.board.p1.players.push({});
          });
        """,
        "steps": [{"state": singles()}]}])
    # Strict mode code would have thrown on the first write (sloppy code is
    # silently ignored); the next listener sees the view as it was.
    assert result["calls"][0]["view"]["board"]["p1"]["name"] == "Alpha"
    assert len(result["calls"][0]["view"]["board"]["p1"]["players"]) == 1
    assert result["calls"][0]["frozen"]


@needs_node
def test_a_throwing_listener_is_reported_once_until_it_succeeds(tmp_path):
    def phase(revision, value):
        return {"delta": {"revision": revision, "delta": [
            {"path": ["score", "1", "phase"], "action": "values_changed",
             "value": value}]}}

    [result] = run_scenarios(tmp_path, [{
        "afterAutona": """
          Autona.onUpdate((view) => {
            if (view.board.phase !== "ok") throw new Error("layout bug");
          });
          Autona.onUpdate(async () => { throw new Error("async bug"); });
        """,
        "steps": [{"state": singles(), "revision": 1}, phase(2, "again"),
                  phase(3, "ok"), phase(4, "broken")]}])
    # The others are called every time.
    assert len(result["calls"]) == 4
    assert result["errors"] == [
        ["error", "Autona.onUpdate: Error: layout bug"],
        ["error", "Autona.onUpdate: Error: async bug"],
        # Succeeded on "ok", failing again: reported again.
        ["error", "Autona.onUpdate: Error: layout bug"],
    ]


@needs_node
def test_load_everything_runs_once_whoever_calls_it(tmp_path):
    results = run_scenarios(tmp_path, [
        # A layout written for autona.js does not call it: autona.js does.
        {"steps": []},
        # One copied from the shipped layouts does, after autona.js loaded.
        {"afterAutona": "LoadEverything(); LoadEverything();", "steps": []},
    ])
    assert [result["loads"] for result in results] == [1, 1]
    assert all(result["errors"] == [] for result in results)


@needs_node
def test_loaded_after_load_everything_it_warns_once_and_does_not_load_again(
        tmp_path):
    # The layout's script ran first: LoadEverything() had opened the socket.
    [result] = run_scenarios(tmp_path, [{
        "layout": "LoadEverything(); var tshSocket = {on() {}};",
        "steps": [{"run": "LoadEverything();"}, {"state": singles()}]}])
    assert result["loads"] == 1
    assert [level for level, _ in result["errors"]] == ["warn"]
    assert "right after globals.js" in result["errors"][0][1]
    assert len(result["calls"]) == 1


@needs_node
def test_the_sample_parameter_draws_the_sample_instead_of_the_state(
        tmp_path):
    [board] = [case for case in view_cases()
               if case["name"] == "singles: board 1"]
    results = run_scenarios(tmp_path, [
        {"search": "?autonaSample=singles", "kind": "scoreboard",
         "steps": [
             # What OBS does when it shows the source again: the sample, at
             # the same timestamp, changes nothing.
             {"run": "UpdateData();"},
         ]},
        {"search": "?autonaSample=../../out/program_state", "steps": []},
        {"search": "?autonaSample=missing", "steps": []},
    ])
    drawn, refused, missing = results
    assert drawn["errors"] == []
    assert drawn["fetched"] == ["/layout/include/sample/singles.json"]
    assert [call["view"]["board"] for call in drawn["calls"]] == [
        board["expect"]["board"]]

    assert refused["fetched"] == []
    assert refused["calls"] == []
    assert any("not a sample name" in text for _, text in refused["errors"])

    assert missing["fetched"] == ["/layout/include/sample/missing.json"]
    assert missing["calls"] == []
    assert any("no sample named missing" in text
               for _, text in missing["errors"])


@needs_node
def test_autona_exposes_the_contract_number_and_nothing_else(tmp_path):
    [result] = run_scenarios(tmp_path, [{"steps": []}])
    assert result["autona"] == {"contract": contract()["contract"],
                                "keys": ["contract", "onUpdate", "raw"]}


@needs_node
def test_without_globals_it_says_so_and_defines_nothing(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "layout": "LoadEverything = undefined;", "steps": []}])
    assert result["autona"] is None
    assert any("globals.js" in text for level, text in result["errors"]
               if level == "error")


# ── spot checks of views.json, written out so that a wrong expected value
# cannot slip in with a regenerated file ─────────────────────────────────
def expect(name):
    return next(case["expect"] for case in view_cases()
                if case["name"] == name)


def test_spot_checks_of_the_expected_views():
    singles_1 = expect("singles: board 1")
    p1 = singles_1["board"]["p1"]
    assert p1["name"] == "Alpha" and p1["twitter"] == "alpha_fgc"
    assert p1["country"]["flag"] == "/assets/country_flag/jp.png"
    assert p1["character"]["image"] == \
        "/assets/mock_data/mock_asset/full_character_1.svg"
    assert singles_1["board"]["p2"]["losers"] is True
    assert singles_1["board"]["station"] == "3"
    assert [(c["name"], c["iconName"]) for c in singles_1["commentary"]] == [
        ("Kilo", "mic"), ("Mike", "megaphone"), ("November", "phone")]
    assert singles_1["tournament"]["name"] == "Autona Cup"
    assert singles_1["tournament"]["themeColor"] == "#181B21"
    matches = singles_1["top8"]["matches"]
    assert matches["wsf2"]["winner"] == 2
    assert matches["wf"]["p2"]["name"] == "Delta"
    assert matches["gf"]["p1"]["slot"] is None
    assert matches["wf"]["p1"]["country"] == {
        "code": "JP", "name": "", "flag": "/assets/country_flag/jp.png"}

    # The commentary kind takes its own color, by its block or its folder.
    for name in ("singles: commentary kind",
                 "singles: kind from the folder name"):
        assert expect(name)["tournament"]["themeColor"] == "#0b3d91", name
    assert expect("singles: board 2")["board"]["p1"]["name"] == "Charlie"

    teams = expect("teams: board 1")
    assert [p["name"] for p in teams["board"]["p1"]["players"]] == [
        "Alpha", "Echo", "Hotel"]
    assert teams["board"]["p1"]["teamName"] == "Team Alpha"
    assert teams["options"]["showSponsor"] is False
    assert teams["board"]["p1"]["sponsor"] == "TEAM A"  # options not applied
    assert teams["tournament"]["logo"] == "/layout/logo.png?v=1763683200000"
    assert teams["board"]["p1"]["players"][0]["stock"] is None

    crew = expect("teams: crew board 3")["board"]
    assert crew["number"] == 3
    assert crew["bestOf"] is None                   # 0 in the state: not set
    assert [(p["name"], p["stock"], p["active"])
            for p in crew["p1"]["players"]] == [
        ("A1", 0, False), ("A2", 2, True), ("A3", 3, False)]
    assert crew["p1"]["score"] == 5

    partial = expect("partial: values missing or malformed")
    p1 = partial["board"]["p1"]
    assert partial["board"]["bestOf"] == 3
    assert p1["score"] == 2 and p1["losers"] is False and p1["seed"] is None
    assert p1["twitter"] == "solo"
    assert p1["sponsorLogo"] == "/user_data/sponsor_logo/Team%20A.png"
    assert [c["name"] for c in p1["characters"]] == ["Ken"]
    assert p1["character"]["icon"] == ""            # an absolute path
    # No full: the first pack by name that is not the icon.
    assert [i["pack"] for i in p1["character"]["images"]] == [
        "alpha_pack", "portrait"]
    assert p1["character"]["image"] == "/user_data/games/sf6/alpha_pack/ken.png"
    assert partial["board"]["p2"]["players"] == []
    assert partial["tournament"]["name"] == "Only Name"
    assert [c["name"] for c in partial["commentary"]] == ["Legacy"]
    assert partial["options"]["showSponsor"] is False
    assert partial["options"]["showName"] is True   # not a boolean: default
    gf = partial["top8"]["matches"]["gf"]
    assert (gf["p1"]["slot"], gf["p1"]["name"]) == (1, "P1")
    assert (gf["p2"]["slot"], gf["p2"]["name"]) == (None, "")  # slot 9

    # The layout's asset_key comes first.
    picked = expect("partial: the layout's asset_key")["board"]["p1"]
    assert picked["character"]["image"] == \
        "/user_data/games/sf6/portrait/ken_00.png"
