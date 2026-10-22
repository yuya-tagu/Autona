"""Contract: the OBS runtime reads Designer projects exactly like model.ts.

``layout/designer_runtime/runtime.js`` migrates ``project.json`` at load time
(OBS reads the file without the app), and ``web/src/ScoreboardDesigner/
model.ts`` -- the owner of what a project means -- migrates it in the editor.
Both must read every supported schemaVersion into the same v3 project. The
fixtures in ``tests/fixtures/designer/`` are shared: ``model.test.js`` runs
them through model.ts, this module runs them through runtime.js under node.

Differences the contract tolerates (same rules in model.test.js): ids made
up during migration (the project id when the file has none, keyframe ids),
and a missing ``assetTransformApplied`` meaning false.

Cases the fixtures pin because the two once disagreed: a rotated layer
without ``animations`` (generated keyframes stay at rotation 0; keyframes
rotate on top of the layer's own rotation) and Game-History ``slots`` in a
v1/v2 file (their images belong in the asset manifest, as the Designer
saves it).

``invalid_v1.json`` lists inputs both must turn away, with the message they
give: a v1/v2 image or slot reference that is null or not a string. Deriving
the manifest skips those (model.ts once crashed there with a TypeError) and
validation rejects them, naming the layer or the slot.

``meta_overlay.json`` is laid over ``v3.json``: project.json's optional
``meta`` (format / game, for the Board tab). model.ts keeps it trimmed; the
runtime carries it through untouched and draws the same DOM with or without
it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME = PROJECT_ROOT / "layout" / "designer_runtime" / "runtime.js"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "designer"
FIXTURE_NAMES = ("v1.json", "v2.json", "v3.json")
INVALID_FIXTURE = "invalid_v1.json"
HOOK = "__autonaDesignerContractTest"

# Loads runtime.js as a page would, minus the page: the context is its own
# window, and fetch never settles, so nothing past loadProject() runs and no
# DOM is needed. argv: "opt-in" or "plain", runtime.js, fixture files. A
# fixture holding a list is a list of invalid inputs; each one's outcome is
# reported instead of a project.
_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [mode, runtimePath, ...fixtures] = process.argv.slice(1);
const fetched = [];
const sandbox = {console};
sandbox.window = sandbox;
sandbox.fetch = (url) => { fetched.push(url); return new Promise(() => {}); };
if (mode === "opt-in") sandbox.__autonaDesignerContractTest = {};
vm.createContext(sandbox);
const before = new Set(Object.keys(sandbox));
vm.runInContext(fs.readFileSync(runtimePath, "utf8"), sandbox,
                {filename: runtimePath});
const added = Object.keys(sandbox).filter((key) => !before.has(key));
const hook = sandbox.__autonaDesignerContractTest;
const results = {};
for (const file of fixtures) {
  const fixture = JSON.parse(fs.readFileSync(file, "utf8"));
  results[file] = Array.isArray(fixture)
    ? fixture.map((entry) => {
        try {
          hook.readProject(entry.project);
          return {accepted: true};
        } catch (error) {
          return {name: error.name, message: error.message};
        }
      })
    : hook.readProject(fixture);
}
process.stdout.write(JSON.stringify({
  fetched, added, hooked: Boolean(hook && hook.readProject), results}));
"""


# Renders a project with the real runtime in jsdom (borrowed from the web
# UI's test toolchain) and prints the composed DOM after one state update.
# argv: jsdom module, runtime.js, then project files to render one by one.
_RENDER_HARNESS = r"""
const fs = require("fs");
const [jsdomPath, runtimePath, ...projects] = process.argv.slice(1);
const {JSDOM} = require(jsdomPath);
const source = fs.readFileSync(runtimePath, "utf8");
const STATE = {
  score: {"1": {best_of: 5, phase: "Top 8", match: "Grand Final", team: {
    "1": {score: 2, player: {"1": {name: "Alpha",
      country: {asset: "assets/country_flag/jp.png"}}}},
    "2": {score: 1, player: {"1": {name: "Beta"}}},
  }}},
  tournamentInfo: {tournamentName: "Contract Cup"},
};

async function render(project) {
  const dom = new JSDOM(
    '<!doctype html><html><head></head><body>' +
    '<main id="scoreboard-composer"></main><div id="composer-error"></div>' +
    '</body></html>',
    {runScripts: "outside-only",
     url: "http://127.0.0.1/layout/custom/contract/index.html"});
  const w = dom.window;
  w.fetch = (url) => url === "./project.json"
    ? Promise.resolve({ok: true, json: () => Promise.resolve(project)})
    : Promise.reject(new Error("offline"));
  w.Start = null;
  w.Update = null;
  const loaded = new Promise((resolve) => {
    w.LoadEverything = () => { resolve(); return Promise.resolve(); };
  });
  w.eval(source);
  await Promise.race([loaded, new Promise((_, reject) => setTimeout(
    () => reject(new Error("runtime did not load: " +
      w.document.getElementById("composer-error").textContent)), 5000))]);
  await w.Update({data: STATE});
  return w.document.getElementById("scoreboard-composer").outerHTML;
}

(async () => {
  const rendered = [];
  for (const file of projects) {
    rendered.push(await render(JSON.parse(fs.readFileSync(file, "utf8"))));
  }
  process.stdout.write(JSON.stringify(rendered));
})().catch((error) => { console.error(error); process.exit(1); });
"""
JSDOM = PROJECT_ROOT / "web" / "node_modules" / "jsdom"


def run_runtime(mode, *fixtures):
    result = subprocess.run(
        [NODE, "-e", _HARNESS, mode, str(RUNTIME), *map(str, fixtures)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def read_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def normalize(migrated, source, expected):
    project = json.loads(json.dumps(migrated))
    if isinstance(source.get("id"), str) and source["id"]:
        assert project["id"] == source["id"]
    else:
        assert isinstance(project.get("id"), str) and project["id"]
        project["id"] = expected["id"]
    for layer in project["layers"]:
        for track in ("intro", "update"):
            for frame in layer["animations"][track]["keyframes"]:
                frame.pop("id", None)
        for track in ("sceneEnter", "dataChange"):
            for frame in layer["events"][track]["visualKeyframes"]:
                frame.pop("id", None)
        layer.setdefault("assetTransformApplied", False)
    return project


@pytest.fixture(scope="module")
def runtime_results():
    paths = [FIXTURES / name for name in FIXTURE_NAMES]
    output = run_runtime("opt-in", *paths)
    return {Path(path).name: project
            for path, project in output["results"].items()}


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_runtime_reads_the_fixture_as_the_expected_v3(runtime_results, name):
    expected = read_fixture("expected_v3.json")
    migrated = runtime_results[name]
    assert normalize(migrated, read_fixture(name), expected) == expected


def test_runtime_rejects_invalid_input_like_model_ts():
    # model.test.js expects the same message from model.ts for each case.
    path = FIXTURES / INVALID_FIXTURE
    cases = read_fixture(INVALID_FIXTURE)
    outcomes = run_runtime("opt-in", path)["results"][str(path)]
    assert cases
    assert [(case["label"], outcome)
            for case, outcome in zip(cases, outcomes, strict=True)] == [
        (case["label"], {"name": "Error", "message": case["error"]})
        for case in cases]


@pytest.mark.skipif(not JSDOM.is_dir(),
                    reason="web/node_modules is not installed")
def test_slot_images_in_the_manifest_do_not_change_rendering(tmp_path):
    # runtime.js now lists Game-History slot images in the manifest it
    # derives for v1/v2 files, as model.ts does. Rendering must not notice:
    # the same file draws the same DOM whether the manifest carries the slot
    # images (current derivation) or only the image layers (the previous one).
    previous = read_fixture("v1.json")
    previous["assets"] = [
        {"id": f"legacy-image-{index}", "kind": "image", "path": path,
         "mediaType": "image/svg+xml" if path.endswith(".svg") else "image/png"}
        for index, path in enumerate(
            ["assets/frame.png", "assets/logo.svg", "assets/set-icon.svg"], 1)]
    previous_file = tmp_path / "previous.json"
    previous_file.write_text(json.dumps(previous), encoding="utf-8")
    result = subprocess.run(
        [NODE, "-e", _RENDER_HARNESS, str(JSDOM), str(RUNTIME),
         str(FIXTURES / "v1.json"), str(previous_file)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    with_slots, without_slots = json.loads(result.stdout)

    assert with_slots == without_slots
    # ... and the comparison did exercise the slots.
    assert with_slots.count("composer-game-slot ") == 5
    assert 'src="assets/game-1.svg"' in with_slots


def project_with_meta(tmp_path):
    """v3.json with meta_overlay.json laid over it, as a file; and the meta."""
    project = {**read_fixture("v3.json"), **read_fixture("meta_overlay.json")}
    path = tmp_path / "v3_meta.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path, project["meta"]


def test_runtime_carries_meta_through_untouched(tmp_path):
    # project.json's meta (format / game) is for the Board tab. The runtime
    # neither needs nor checks it: the project reads exactly as without it,
    # meta riding along as written (model.ts trims it on save; model.test.js
    # pins that against the same overlay).
    path, meta = project_with_meta(tmp_path)
    [migrated] = run_runtime("opt-in", path)["results"].values()
    expected = read_fixture("expected_v3.json")

    assert migrated.pop("meta") == meta
    assert normalize(migrated, read_fixture("v3.json"), expected) == expected


@pytest.mark.skipif(not JSDOM.is_dir(),
                    reason="web/node_modules is not installed")
def test_meta_does_not_change_rendering(tmp_path):
    path, meta = project_with_meta(tmp_path)
    result = subprocess.run(
        [NODE, "-e", _RENDER_HARNESS, str(JSDOM), str(RUNTIME),
         str(FIXTURES / "v3.json"), str(path)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    without_meta, with_meta = json.loads(result.stdout)

    assert with_meta == without_meta
    # ... nothing of it is drawn (the overlay's game is markup on purpose).
    assert meta["format"].strip() not in with_meta
    assert "スト6" not in with_meta
    # ... and the comparison did render the project with live data.
    assert "Alpha" in with_meta


def test_the_contract_hook_is_inert_unless_the_page_opts_in():
    plain = run_runtime("plain")
    # Without the opt-in the script runs exactly as before: it reaches
    # loadProject() and leaves no new global behind.
    assert plain["fetched"] == ["./project.json"]
    assert plain["added"] == []
    assert not plain["hooked"]

    opted_in = run_runtime("opt-in")
    assert opted_in["fetched"] == ["./project.json"]
    assert opted_in["added"] == []
    assert opted_in["hooked"]

    # ... and no shipped page opts in.
    layout = PROJECT_ROOT / "layout"
    mentions = sorted(
        path.relative_to(layout).as_posix()
        for path in layout.rglob("*")
        if path.is_file() and path.suffix in {".html", ".js"}
        and HOOK in path.read_text(encoding="utf-8", errors="replace"))
    assert mentions == ["designer_runtime/runtime.js"]
