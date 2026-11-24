"""Template contract 2 (ADR-008 D7 R2): layout/include/autona.js's onRender.

A thumbnail / result template on contract 2 loads ../include/autona.js (and
no globals.js) and draws in ``Autona.onRender(listener)``. autona.js runs
here under node's ``vm`` in a page as far as it can tell -- a document (an
EventTarget with images, fonts and a title), the window's events, maybe a
parent frame -- and is asked to draw the way the app asks it:

- the off-screen renderer: ``Autona._render(payload, serial)``, after which
  the title says "AUTONA_RENDER <serial> ok | failed" and
  ``Autona._renderResult(serial)`` is its result as JSON;
- the Web UI's live preview, in a frame whose URL names its parent's
  origin (``?autonaParent=``): the parent posts exactly
  ``{type: "autona:render", serial, payload}``.

What is pinned: a listener that returns is a success, after the fonts are
in; an exception -- thrown, a rejected promise, the page's error event or
unhandled rejection during the render, one of the page's while it loaded --
is no success, and is the render's errors; an error after a render ended is
that render's (read again), not the next one's; no listener is no success;
one render at a time, the newest of those asked for meanwhile drawn next; a
frame takes a render only from its parent, from the origin its URL names,
of the one shape, with a serial above the last, and says when it is drawn;
a page that renders says it is ready (contract 2) and not that it lacks
globals.js. The renderer's side (AutonaOffscreenRenderer) is
tests/test_thumbnail_render.py's.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTONA = PROJECT_ROOT / "layout" / "include" / "autona.js"

# Runs scenarios, each in a fresh vm context. A scenario: `template` (the
# page's script, run after autona.js), `parent` ("frame": a parent window
# that records what it is posted), `fontsLate` (document.fonts.ready waits
# for __fontsDone()), `search` (the page's location.search), and `steps`,
# each run in the page in turn with a tick after it: {run: code}, {render:
# [payload, serial]} (Autona._render), {read: serial} (Autona._renderResult
# again), or {message: data, from: "parent" | "other", origin} (a message
# event; origin PARENT_ORIGIN when not given). argv: autona.js, the
# scenarios' JSON file.
_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [autonaPath, scenariosPath] = process.argv.slice(1);
const autonaSource = fs.readFileSync(autonaPath, "utf8");
const scenarios = JSON.parse(fs.readFileSync(scenariosPath, "utf8"));
const tick = () => new Promise((resolve) => setTimeout(resolve, 30));

function page(scenario) {
  const errors = [];
  const titles = [];
  const document = new EventTarget();
  document.readyState = "complete";
  document.images = [];
  document.querySelectorAll = () => [];
  let fontsDone = null;
  document.fonts = {ready: scenario.fontsLate
    ? new Promise((resolve) => { fontsDone = resolve; })
    : Promise.resolve()};
  let title = "";
  Object.defineProperty(document, "title", {
    get: () => title,
    set: (value) => { title = String(value); titles.push(title); },
  });
  const win = new EventTarget();
  const sandbox = {
    console: {log() {}, info() {}, debug() {},
              warn: (...args) => errors.push(["warn", args.map(String).join(" ")]),
              error: (...args) => errors.push(["error", args.map(String).join(" ")])},
    setTimeout, clearTimeout, URLSearchParams, EventTarget, Event, Promise,
    document,
    getComputedStyle: () => ({backgroundImage: "none"}),
    location: {search: scenario.search || "", protocol: "http:",
               host: "127.0.0.1:5108",
               pathname: "/layout/my_thumbnail/thumbnail.html"},
    addEventListener: win.addEventListener.bind(win),
    dispatchEvent: win.dispatchEvent.bind(win),
    __fontsDone: () => fontsDone && fontsDone(),
  };
  sandbox.window = sandbox;
  const posted = [];
  const parent = {postMessage: (message, target) => posted.push({message, target})};
  sandbox.parent = scenario.parent === "frame" ? parent : sandbox;
  vm.createContext(sandbox);
  vm.runInContext(autonaSource, sandbox, {filename: autonaPath});
  if (scenario.template) vm.runInContext(scenario.template, sandbox);
  return {sandbox, errors, titles, posted, parent};
}

async function run(scenario) {
  const p = page(scenario);
  await tick();
  const results = [];
  for (const step of scenario.steps || []) {
    if (step.run) vm.runInContext(step.run, p.sandbox);
    if (step.render) {
      p.sandbox.Autona._render(step.render[0], step.render[1]);
    }
    if (step.message !== undefined) {
      const event = new Event("message");
      // Made in the page's realm, as a posted message is (structured clone).
      event.data = vm.runInContext("(" + JSON.stringify(step.message) + ")",
                                   p.sandbox);
      event.source = step.from === "parent" ? p.parent : {};
      event.origin = step.origin || "http://127.0.0.1:5107";
      p.sandbox.dispatchEvent(event);
    }
    await tick();
    if (step.render || step.read) {
      const serial = step.render ? step.render[1] : step.read;
      const said = p.sandbox.Autona._renderResult(serial);
      results.push(said === null ? null : JSON.parse(said));
    }
  }
  return {
    titles: p.titles,
    results,
    errors: p.errors,
    posted: p.posted,
    keys: p.sandbox.Autona ? Object.keys(p.sandbox.Autona) : null,
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
        [NODE, "-e", _HARNESS, str(AUTONA), str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _error_event(message):
    return (f"const e = new Event('error'); e.message = {json.dumps(message)};"
            " e.filename = 'http://127.0.0.1:5108/layout/my_thumbnail/t.js';"
            " e.lineno = 12; e.colno = 3; dispatchEvent(e);")


PAYLOAD = {"data": {"name": "Alice"}, "config": {}, "template": {}}
# The page around the preview's frame, as its URL names it.
PARENT_ORIGIN = "http://127.0.0.1:5107"
FRAMED = "?autonaParent=" + PARENT_ORIGIN
DRAW = "Autona.onRender((p) => { window.__out = p.data.name; });"


@needs_node
def test_a_listener_that_returns_is_a_drawing(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "template": DRAW, "steps": [{"render": [PAYLOAD, 1]}]}])
    assert result["out"] == "Alice"
    assert result["titles"][-1] == "AUTONA_RENDER 1 ok"
    assert result["results"] == [
        {"serial": 1, "ok": True, "done": True, "errors": []}]
    assert result["errors"] == []


@needs_node
def test_the_render_waits_for_the_fonts(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "template": DRAW, "fontsLate": True,
        "steps": [{"render": [PAYLOAD, 1]},
                  {"run": "__fontsDone();"}]}])
    # Not done before the fonts were in; done after.
    assert result["results"] == [
        {"serial": 1, "ok": True, "done": False, "errors": []}]
    assert "AUTONA_RENDER 1 ok" in result["titles"]


@needs_node
@pytest.mark.parametrize("template, kind, message", [
    ("Autona.onRender(() => { throw new TypeError('boom'); });",
     "onRender", "TypeError: boom"),
    ("Autona.onRender(async () => { throw new RangeError('late'); });",
     "onRender", "RangeError: late"),
    ("Autona.onRender(() => Promise.reject(new Error('no')));",
     "onRender", "Error: no"),
    ("Autona.onRender(() => { " + _error_event("Uncaught oops") + " });",
     "error", "Uncaught oops"),
    ("Autona.onRender(() => { const e = new Event('unhandledrejection');"
     " e.reason = new Error('lost'); dispatchEvent(e); });",
     "unhandledrejection", "Error: lost"),
])
def test_an_exception_is_no_drawing(tmp_path, template, kind, message):
    [result] = run_scenarios(tmp_path, [{
        "template": template, "steps": [{"render": [PAYLOAD, 1]}]}])
    assert result["titles"][-1] == "AUTONA_RENDER 1 failed"
    [said] = result["results"]
    assert said["ok"] is False
    assert [(error["kind"], error["message"]) for error in said["errors"]] \
        == [(kind, message)]


@needs_node
def test_an_error_event_says_where(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "template": "Autona.onRender(() => { " + _error_event("x") + " });",
        "steps": [{"render": [PAYLOAD, 1]}]}])
    [error] = result["results"][0]["errors"]
    assert (error["source"], error["line"], error["column"]) == (
        "/layout/my_thumbnail/t.js", 12, 3)


@needs_node
def test_no_listener_is_no_drawing(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "template": "", "steps": [{"render": [PAYLOAD, 1]}]}])
    assert result["titles"][-1] == "AUTONA_RENDER 1 failed"
    errors = result["results"][0]["errors"]
    # What it said as the page loaded (neither globals.js nor onRender) and
    # what the render found.
    assert errors and all(error["kind"] == "autona" for error in errors)
    assert any("did not call Autona.onRender" in error["message"]
               for error in errors)


@needs_node
def test_an_error_of_the_loading_page_is_the_next_renders(tmp_path):
    # The page broke before it was asked to draw: that drawing is not one.
    # The one after it starts clean.
    [result] = run_scenarios(tmp_path, [{
        "template": DRAW + _error_event("Uncaught at load"),
        "steps": [{"render": [PAYLOAD, 1]}, {"render": [PAYLOAD, 2]}]}])
    first, second = result["results"]
    assert first["ok"] is False
    assert [error["message"] for error in first["errors"]] == [
        "Uncaught at load"]
    assert second == {"serial": 2, "ok": True, "done": True, "errors": []}
    assert result["titles"][-1] == "AUTONA_RENDER 2 ok"


@needs_node
@pytest.mark.parametrize("late, kind", [
    # A timer's uncaught error (the page's error event).
    ("setTimeout(() => { " + _error_event("Error: after") + " }, 5);",
     "error"),
    # A promise the listener did not await.
    ("setTimeout(() => { const e = new Event('unhandledrejection');"
     " e.reason = new Error('after'); dispatchEvent(e); }, 5);",
     "unhandledrejection"),
])
def test_an_error_after_a_render_is_that_renders_not_the_nexts(tmp_path,
                                                               late, kind):
    # Thrown once its render said it was done: read again, that render
    # failed; the next one did not.
    [result] = run_scenarios(tmp_path, [{
        "template": "Autona.onRender((p) => { if (p.data.name === 'Alice')"
                    " { " + late + " } });",
        "steps": [{"render": [PAYLOAD, 1]}, {"read": 1},
                  {"render": [dict(PAYLOAD, data={"name": "Bob"}), 2]}]}])
    first, again, second = result["results"]
    assert first["done"] is True
    assert again["ok"] is False
    assert [(item["kind"], item["message"]) for item in again["errors"]] \
        == [(kind, "Error: after")]
    assert second == {"serial": 2, "ok": True, "done": True, "errors": []}
    assert result["titles"][-1] == "AUTONA_RENDER 2 ok"


@needs_node
def test_one_render_at_a_time_and_the_newest_waiting_next(tmp_path):
    # Render 1 waits for __release(); 2 and 3 are asked for meanwhile: 3
    # takes 2's place, and is drawn once 1 is done.
    [result] = run_scenarios(tmp_path, [{
        "template": "window.__out = []; let release = null;"
                    " window.__release = () => release && release();"
                    " Autona.onRender(async (p) => {"
                    " window.__out.push('start ' + p.data.name);"
                    " if (p.data.name === '1') await new Promise((r) => {"
                    " release = r; });"
                    " window.__out.push('end ' + p.data.name); });",
        "search": FRAMED, "parent": "frame",
        "steps": [
            {"message": {"type": "autona:render", "serial": n,
                         "payload": dict(PAYLOAD, data={"name": str(n)})},
             "from": "parent"} for n in (1, 2, 3)
        ] + [{"run": "__release();"}]}])
    assert result["out"] == ["start 1", "end 1", "start 3", "end 3"]
    rendered = [posted["message"] for posted in result["posted"]
                if posted["message"]["type"] == "autona:rendered"]
    assert rendered == [
        {"type": "autona:rendered", "contract": 2, "serial": 1, "ok": True},
        {"type": "autona:rendered", "contract": 2, "serial": 3, "ok": True}]
    assert all(posted["target"] == PARENT_ORIGIN
               for posted in result["posted"])


@needs_node
def test_onrender_takes_one_function(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "template": """
          const said = [];
          try { Autona.onRender("draw"); } catch (e) { said.push(e.name); }
          Autona.onRender(() => {});
          try { Autona.onRender(() => {}); } catch (e) { said.push(e.name); }
          window.__out = said;
        """, "steps": []}])
    assert result["out"] == ["TypeError", "Error"]
    # The renderer's two are not the template's to see.
    assert result["keys"] == ["contract", "onUpdate", "onRender", "raw"]


@needs_node
def test_a_page_that_renders_is_ready_and_needs_no_globals(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "template": DRAW, "parent": "frame", "search": FRAMED,
        "steps": []}])
    assert [posted["message"]["type"] for posted in result["posted"]] == [
        "autona:ready"]
    assert result["posted"][0]["message"]["contract"] == 2
    assert result["errors"] == []


@needs_node
def test_a_frame_draws_what_its_parent_posts_and_nothing_else(tmp_path):
    render = {"type": "autona:render", "serial": 3, "payload": PAYLOAD}
    other = dict(PAYLOAD, data={"name": "Mallory"})
    [result] = run_scenarios(tmp_path, [{
        "template": "window.__out = []; Autona.onRender((p) => {"
                    " window.__out.push(p.data.name); });",
        "parent": "frame", "search": FRAMED,
        "steps": [
            # Not the parent's: ignored.
            {"message": dict(render, payload=other), "from": "other"},
            # The parent's, from another origin than its URL names: ignored.
            {"message": dict(render, payload=other), "from": "parent",
             "origin": "http://127.0.0.1:5108"},
            # Not the shape: ignored.
            {"message": dict(render, payload=other, extra=1),
             "from": "parent"},
            {"message": dict(render, serial=0, payload=other),
             "from": "parent"},
            {"message": dict(render, serial=1.5, payload=other),
             "from": "parent"},
            {"message": {"type": "autona:render", "serial": 4},
             "from": "parent"},
            {"message": render, "from": "parent"},
            # Not above the last: ignored.
            {"message": dict(render, payload=other), "from": "parent"},
            {"message": dict(render, serial=2, payload=other),
             "from": "parent"},
            {"message": dict(render, serial=4), "from": "parent"},
        ]}])
    assert result["out"] == ["Alice", "Alice"]


@needs_node
@pytest.mark.parametrize("search", ["", "?autonaParent=javascript:x",
                                    "?autonaParent=http://a/b"])
def test_a_frame_whose_url_names_no_parent_draws_nothing(tmp_path, search):
    [result] = run_scenarios(tmp_path, [{
        "template": "window.__out = []; Autona.onRender((p) => {"
                    " window.__out.push(p.data.name); });",
        "parent": "frame", "search": search,
        "steps": [{"message": {"type": "autona:render", "serial": 1,
                               "payload": PAYLOAD}, "from": "parent"}]}])
    assert result["out"] == []


@needs_node
def test_a_frame_is_told_a_renders_errors(tmp_path):
    [result] = run_scenarios(tmp_path, [{
        "template": "Autona.onRender(() => { throw new Error('bad'); });",
        "parent": "frame", "search": FRAMED,
        "steps": [{"message": {"type": "autona:render", "serial": 1,
                               "payload": PAYLOAD}, "from": "parent"}]}])
    errors = [posted["message"] for posted in result["posted"]
              if posted["message"]["type"] == "autona:error"]
    assert [(message["kind"], message["message"], message["contract"])
            for message in errors] == [("onRender", "Error: bad", 2)]
    assert result["posted"][-1]["message"] == {
        "type": "autona:rendered", "contract": 2, "serial": 1, "ok": False}
    # To the origin the frame's URL names, not to anyone.
    assert all(posted["target"] == PARENT_ORIGIN
               for posted in result["posted"])
