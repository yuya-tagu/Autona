"""The off-screen renderer and the real way through it (ADR-005 S4o).

These drive a real QWebEngineView (offscreen), because what is under test is
its order of events: the page has one document title to say RENDER_READY with,
so two renders at once would take each other's frames. The page stands in for
layout/thumbnail/thumbnail.html: it has the same contract (initThumbnail, the
title) and "paints" the colour it is given after the delay it is given.

On the offscreen platform grab() shows the widget's background, not the page
(the pixels are the real desktop's to check, and only there): the `renderer`
fixture's grab() answers with the colour the page last said it painted when
grab() is called. That is what a real grab would have caught, so a test sees
whose frame it got. The tests through the shell use the real grab().

What the renderer waits for before it grabs is pinned by its order, not its
pixels: RENDER_READY alone takes no picture, RENDER_PAINTED with the render's
number does, and a new page's first picture is not what the view showed
before its load (_showing() stands in for what the view shows).

Waits end as soon as the page has done what is waited for. Chromium runs a
hidden page's renderer at background priority, so on a busy machine a load of
even this small page takes many seconds: no test depends on how long anything
takes, and the watchdog's real 10 seconds are raised unless it is what is under
test (tests/test_layout_preview_lifecycle.py, same reasoning).
"""
import base64
import copy
import json
import shutil
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from qtpy.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, QUrl
from qtpy.QtGui import QColor, QImage, QPainter, QPixmap
from qtpy.QtWebEngineCore import QWebEngineLoadingInfo
from loguru import logger

from src import AutonaWebOperations as WebOps
from src.core import ThumbnailService as thumbnails
from src.core.DictHelper import deep_clone
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager as SM
from src.core.TaskExecutor import TaskExecutor
from src.core.ThumbnailService import ThumbnailService
from src.thumbnail.ThumbnailRenderer import ThumbnailRenderer
from tests.fixtures.thumbnail import scenarios as world
from tests.test_api_v1 import client, webApp  # noqa: F401

PROJECT_ROOT = Path(__file__).parent.parent
WAIT_SECS = 60.0
LONG_TIMEOUT_MS = int(WAIT_SECS * 1000 * 2)

RED = "#ff0000"
GREEN = "#00ff00"
BLUE = "#0000ff"

PAINTED = "PAINTED "

PAGE = """<!doctype html><html><body><script>
function initThumbnail(payload) {
  window.lastPayload = payload;
  const config = payload.config;
  if (config.hang) return;
  setTimeout(() => {
    document.title = "%s" + (config.color || "#808080");
    document.title = "RENDER_READY";
  }, config.delay || 0);
}
</script></body></html>
""" % PAINTED


def _waitUntil(predicate, timeout=WAIT_SECS):
    # A nested loop, not processEvents(): QWebEngine delivers its results
    # through the event loop, and nothing here runs inside a signal handler.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return predicate()


def _color(pixmap):
    return pixmap.toImage().pixelColor(960, 540).name()


def _template(templateId="type_a"):
    return {"template_id": templateId}


@pytest.fixture
def pages(autonaApp):
    """The stand-in page for both templates, in the runtime's layout/."""
    created = []
    for name in ("thumbnail", "thumbnail_type_b"):
        directory = autonaApp.dir / "layout" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "thumbnail.html").write_text(PAGE, encoding="utf-8")
        created.append(directory)
    yield
    for directory in created:
        shutil.rmtree(directory, ignore_errors=True)


def _destroy(renderer):
    renderer._watchdog.stop()
    renderer._host.close()
    # The page before the view it is shown in, and at loop level 0 a
    # DeferredDelete is only delivered on request (conftest's _dropBoard).
    renderer._view.setPage(None)
    for thing in (renderer._page, renderer._host, renderer):
        thing.deleteLater()
        QCoreApplication.sendPostedEvents(thing, QEvent.Type.DeferredDelete)


@pytest.fixture
def renderer(pages):
    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    painted = []

    def on_title(title):
        if title.startswith(PAINTED):
            painted.append(title[len(PAINTED):])

    def grab():
        pixmap = QPixmap(1920, 1080)
        pixmap.fill(QColor(painted[-1] if painted else "#000000"))
        return pixmap

    made._page.titleChanged.connect(on_title)
    made._view.grab = grab
    assert _waitUntil(lambda: made._page_loaded)
    yield made
    _destroy(made)


# ── one render at a time ─────────────────────────────────────────────────

def test_a_sync_render_during_another_gets_its_own_frame(renderer):
    """The reason for the tickets. renderSync used to start right over a
    running render, whose RENDER_READY then ended its wait, with its frame."""
    others = []
    renderer.renderAsync({}, {"color": RED, "delay": 200}, _template(),
                         lambda pixmap, error: others.append(_color(pixmap)))

    pixmap = renderer.renderSync({}, {"color": GREEN, "delay": 800},
                                 _template())

    assert others == [RED]
    assert _color(pixmap) == GREEN


def test_a_sync_render_during_a_panel_render_gets_its_own_frame(renderer):
    announced = []
    renderer.renderComplete.connect(lambda p: announced.append(_color(p)))
    renderer.render({}, {"color": RED, "delay": 200}, _template())

    pixmap = renderer.renderSync({}, {"color": GREEN, "delay": 800},
                                 _template())

    assert _color(pixmap) == GREEN
    assert announced == [RED, GREEN]


def test_an_async_render_during_a_sync_one_waits_for_it(renderer):
    results = []

    def ask_again():
        renderer.renderAsync({}, {"color": BLUE}, _template(),
                             lambda p, _e: results.append(_color(p)))

    QTimer.singleShot(0, ask_again)
    pixmap = renderer.renderSync({}, {"color": GREEN, "delay": 300},
                                 _template())

    assert _color(pixmap) == GREEN
    assert _waitUntil(lambda: results)
    assert results == [BLUE]


def test_the_panels_renders_stand_in_for_each_other(renderer):
    announced = []
    renderer.renderComplete.connect(lambda p: announced.append(_color(p)))

    renderer.render({}, {"color": RED, "delay": 300}, _template())
    renderer.render({}, {"color": GREEN}, _template())
    renderer.render({}, {"color": BLUE}, _template())

    assert _waitUntil(lambda: len(announced) == 2)
    assert announced == [RED, BLUE]


def test_renders_with_a_taker_all_run_in_order(renderer):
    results = []

    for color in (RED, GREEN, BLUE):
        renderer.renderAsync(
            {}, {"color": color}, _template(),
            lambda p, _e: results.append(_color(p)))

    assert _waitUntil(lambda: len(results) == 3)
    assert results == [RED, GREEN, BLUE]


def test_an_async_render_is_not_announced(renderer):
    # It would change the Qt panel's preview.
    announced = []
    renderer.renderComplete.connect(announced.append)
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((_color(p), e)))

    assert _waitUntil(lambda: results)
    assert results == [(GREEN, "")]
    assert announced == []


def test_a_different_template_loads_its_page_first(renderer):
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, _e: results.append(_color(p)))
    assert renderer._template_id == "type_b"

    assert _waitUntil(lambda: results)
    assert results == [GREEN]
    # And back, for a sync render.
    assert _color(renderer.renderSync({}, {"color": RED}, _template())) == RED
    assert renderer._template_id == "type_a"


def test_a_taker_that_raises_does_not_stop_the_next(renderer):
    results = []

    def fail(_pixmap, _error):
        raise RuntimeError("the taker failed")

    renderer.renderAsync({}, {"color": RED}, _template(), fail)
    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, _e: results.append(_color(p)))

    assert _waitUntil(lambda: results)
    assert results == [GREEN]


def test_what_the_page_is_given_survives_the_quoting(renderer):
    # A tag is the operator's or start.gg's text, and none of it is script.
    data = {"name": "a`b\\c'd\"e\nf${g}", "tag": "\u65e5\u672c\u8a9e",
            "separators": "line\u2028paragraph\u2029end",
            "close": "</script><script>document.title='RENDER_READY'</script>"}
    answers = []

    renderer.renderAsync(
        data, {"color": GREEN}, _template(),
        lambda _p, _e: renderer._page.runJavaScript(
            "JSON.stringify(window.lastPayload.data)", answers.append))

    assert _waitUntil(lambda: answers)
    assert json.loads(answers[0]) == data


def test_half_a_surrogate_pair_does_not_stop_the_render(renderer):
    # Half an emoji, from a tag cut at the wrong place. It reaches the page as
    # what JSON makes of it; the point is that the render goes on.
    data = {"name": "a\ud800b"}
    answers = []

    renderer.renderAsync(
        data, {"color": GREEN}, _template(),
        lambda _p, _e: renderer._page.runJavaScript(
            "window.lastPayload.data.name.charCodeAt(1)", answers.append))

    assert _waitUntil(lambda: answers)
    assert answers[0] in (0xD800, 0xFFFD)


# ── when the picture is taken ────────────────────────────────────────────

def _evaluate(renderer, script):
    answers = []
    renderer._page.runJavaScript(script, answers.append)
    assert _waitUntil(lambda: answers)
    return answers[0]


def _setTitle(renderer, title):
    """As the page would; returns once the renderer has heard it, and what it
    put off to the event loop has run."""
    renderer._page.runJavaScript(f"document.title = {json.dumps(title)}")
    assert _waitUntil(lambda: renderer._page.title() == title)
    _waitUntil(lambda: False, timeout=0.2)


def _showing(renderer, color):
    """grab() as what the view shows: `color` until the test changes shown[0].
    Every grab is in `grabs`."""
    shown = [color]
    grabs = []

    def grab():
        grabs.append(shown[0])
        pixmap = QPixmap(1920, 1080)
        pixmap.fill(QColor(shown[0]))
        return pixmap

    renderer._view.grab = grab
    return shown, grabs


def _warnings():
    logged = []
    sink = logger.add(lambda message: logged.append(str(message)),
                      level="WARNING")
    return logged, sink


def test_ready_alone_does_not_take_the_picture(renderer):
    """RENDER_READY says the template's script is done, not that its frame was
    composited: grabbed then (100 ms on, until S4o's fix) it could be the
    frame before, after a change of template the other template's. The
    picture is taken when the page says it painted, under the render's
    number."""
    renderer.PAINT_WAIT_MS = LONG_TIMEOUT_MS
    # Says nothing: the test says it instead.
    renderer.PAINT_SCRIPT = "window.paintNumber = %d;"
    grab = renderer._view.grab
    grabs = []
    renderer._view.grab = lambda: grabs.append(1) or grab()
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((_color(p), e)))
    assert _waitUntil(lambda: renderer._active.ready)
    number = renderer._active.number
    assert _waitUntil(
        lambda: _evaluate(renderer, "window.paintNumber || 0") == number)
    _waitUntil(lambda: False, timeout=0.3)
    assert (results, grabs) == ([], [])

    # An earlier render's.
    _setTitle(renderer, f"RENDER_PAINTED {number - 1} painted")
    assert (results, grabs) == ([], [])

    _setTitle(renderer, f"RENDER_PAINTED {number} painted")
    assert _waitUntil(lambda: results)
    assert results == [(GREEN, "")]
    assert len(grabs) == 1


def test_a_painted_title_before_ready_is_not_believed(renderer):
    renderer.PAINT_WAIT_MS = LONG_TIMEOUT_MS
    results = []

    renderer.renderAsync({}, {"color": GREEN, "delay": 1000}, _template(),
                         lambda p, _e: results.append(_color(p)))
    assert renderer._active.started
    _setTitle(renderer, f"RENDER_PAINTED {renderer._active.number} painted")

    # Believed, the picture would be of the page before it drew GREEN.
    assert _waitUntil(lambda: results)
    assert results == [GREEN]


def test_a_page_whose_frames_stop_is_taken_after_the_wait(renderer):
    renderer.PAINT_WAIT_MS = 300
    _evaluate(renderer, "window.requestAnimationFrame = () => 0; 1")
    results = []
    logged, sink = _warnings()
    try:
        renderer.renderAsync({}, {"color": GREEN}, _template(),
                             lambda p, e: results.append((_color(p), e)))
        assert _waitUntil(lambda: results)
    finally:
        logger.remove(sink)

    assert results == [(GREEN, "")]
    assert any("did not say it painted (late)" in line for line in logged)


def test_a_wait_that_runs_out_after_its_render_is_not_the_next_ones(renderer):
    """The first render's wait would end the second before it drew."""
    renderer.PAINT_WAIT_MS = 400
    results = []

    renderer.renderAsync({}, {"color": RED}, _template(),
                         lambda p, _e: results.append(_color(p)))
    renderer.renderAsync({}, {"color": GREEN, "delay": 1000}, _template(),
                         lambda p, _e: results.append(_color(p)))

    assert _waitUntil(lambda: len(results) == 2)
    assert results == [RED, GREEN]


def _dataUrl(color):
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(
        buffer.getvalue()).decode("ascii")


def test_an_image_the_render_before_decoded_is_not_decoded_again(renderer):
    """Asking again for what is decoded repeats the work, every render (the
    template draws the same background and characters again). A new image,
    and one that did not decode, is decoded. The log has the page's account
    of the wait."""
    first, second, third = (_dataUrl(c) for c in ("red", "lime", "blue"))
    _evaluate(renderer, """(() => {
      window.decodes = [];
      const decode = HTMLImageElement.prototype.decode;
      HTMLImageElement.prototype.decode = function () {
        window.decodes.push(this.src.split("/").pop().slice(0, 40));
        return decode.call(this);
      };
      for (const src of [%s, "missing.png"]) {
        const image = document.createElement("img");
        image.src = src;
        document.body.appendChild(image);
      }
      const box = document.createElement("div");
      box.id = "box";
      box.style.backgroundImage = 'url("' + %s + '")';
      document.body.appendChild(box);
      return 1;
    })()""" % (json.dumps(first), json.dumps(second)))
    lines = []
    sink = logger.add(lambda message: lines.append(str(message)),
                      level="INFO",
                      filter=lambda record: "render complete" in
                      record["message"])

    def render():
        results = []
        renderer.renderAsync({}, {"color": GREEN}, _template(),
                             lambda p, e: results.append(e))
        assert _waitUntil(lambda: results)
        assert results == [""]
        decodes = json.loads(_evaluate(
            renderer, "JSON.stringify(window.decodes.splice(0))"))
        return sorted(decodes)

    def short(url):
        return url.split("/").pop()[:40]

    try:
        assert render() == sorted([short(first), short(second), "missing.png"])
        assert render() == ["missing.png"]
        _evaluate(renderer, "document.getElementById('box').style"
                  ".backgroundImage = 'url(\"' + %s + '\")'; 1"
                  % json.dumps(third))
        assert render() == sorted([short(third), "missing.png"])
    finally:
        logger.remove(sink)

    assert len(lines) == 3
    assert "images=" in lines[0] and "frames=" in lines[0]


def test_a_new_pages_first_picture_is_not_the_old_pages(renderer):
    """After a load the view can go on showing the page before it, after the
    new one said it painted: the picture would be the other template's."""
    renderer.FRESH_FRAME_WAIT_MS = LONG_TIMEOUT_MS
    shown, grabs = _showing(renderer, RED)
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, e: results.append((_color(p), e)))
    assert grabs == [RED]  # What the view showed before the load.

    # The page said it painted, and the view still shows the old one.
    assert _waitUntil(lambda: len(grabs) >= 3)
    assert results == []

    shown[0] = GREEN
    assert _waitUntil(lambda: results)
    assert results == [(GREEN, "")]
    assert renderer._before_load is None


def test_a_render_on_the_same_page_is_not_compared(renderer):
    """Nor waited on: the same picture again is the right picture."""
    renderer.FRESH_FRAME_WAIT_MS = LONG_TIMEOUT_MS
    _shown, grabs = _showing(renderer, RED)
    results = []

    # The page's first: unlike what the view showed before its load.
    renderer.renderAsync({}, {"color": RED}, _template(),
                         lambda p, _e: results.append(_color(p)))
    assert _waitUntil(lambda: results)
    del grabs[:]

    renderer.renderAsync({}, {"color": RED}, _template(),
                         lambda p, _e: results.append(_color(p)))

    assert _waitUntil(lambda: len(results) == 2)
    assert results == [RED, RED]
    assert grabs == [RED]


def test_a_new_page_that_looks_like_the_old_one_is_taken_after_the_wait(
        renderer):
    renderer.FRESH_FRAME_WAIT_MS = 300
    _shown, grabs = _showing(renderer, RED)
    results = []
    logged, sink = _warnings()
    try:
        renderer.renderAsync({}, {"color": RED}, _template("type_b"),
                             lambda p, e: results.append((_color(p), e)))
        assert _waitUntil(lambda: results)
    finally:
        logger.remove(sink)

    assert results == [(RED, "")]
    assert len(grabs) > 2
    assert any("still looks like the one before" in line for line in logged)


# ── the pages as shipped ─────────────────────────────────────────────────

@pytest.fixture
def shippedPages(autonaApp):
    """The templates as shipped in the runtime's layout/. Copies: a link there
    would have a cleanup that goes wrong delete the project's. layout/include/
    is 19 MB, of which the pages only want the two scripts."""
    created = []
    for name in ("thumbnail", "thumbnail_type_b"):
        directory = autonaApp.dir / "layout" / name
        assert not directory.exists()
        shutil.copytree(PROJECT_ROOT / "layout" / name, directory)
        created.append(directory)
    include = autonaApp.dir / "layout" / "include"
    assert not include.exists()
    include.mkdir()
    created.append(include)
    for script in ("jquery-3.7.1.min.js", "lodash.min.js"):
        shutil.copy(PROJECT_ROOT / "layout" / "include" / script, include)
    try:
        yield
    finally:
        for directory in created:
            shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.parametrize("templateId", ["type_a", "type_b"])
def test_a_shipped_page_draws_what_it_is_given(shippedPages, templateId):
    """The real thumbnail.js, on a payload with what is awkward in it. Not its
    pixels, which the offscreen platform does not show. Its catch says
    RENDER_READY as well, so the name is what says it got to the end."""
    template = json.loads(
        (PROJECT_ROOT / "assets" / "thumbnail_base" / "thumbnail_types"
         / f"{templateId}.json").read_text(encoding="utf-8"))
    template["template_id"] = templateId
    data = copy.deepcopy(world.WITH_PLAYERS)
    data["score"]["1"]["team"]["1"]["player"]["1"]["name"] \
        = "Al\u2028pha\ud800\"</script>${b}"
    config = {"scoreboardNumber": 1, "use_team_names": False,
              "use_sponsors": False,
              "game": {"tgame": {"zoom": 100, "flip_p2": True,
                                 "align": {"horizontal": 50, "vertical": 40}}}}
    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    made.FRESH_FRAME_WAIT_MS = 0  # See the `service` fixture.
    results = []
    names = []
    try:
        made.renderAsync(data, config, template,
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        pixmap, error = results[0]
        assert error == ""
        assert (pixmap.width(), pixmap.height()) == (1920, 1080)
        made._page.runJavaScript(
            "document.getElementById('p1name').textContent", names.append)
        assert _waitUntil(lambda: names)
    finally:
        _destroy(made)

    assert '"</script>${b}' in names[0]
    assert names[0].startswith("Al\u2028pha")


# ── a render that does not end ───────────────────────────────────────────

def test_a_hung_render_fails_and_the_next_one_works(renderer):
    results = []
    renderer.STAGE_TIMEOUT_MS = 500

    renderer.renderAsync({}, {"hang": True}, _template(),
                         lambda p, e: results.append((p, e)))

    assert _waitUntil(lambda: results)
    assert results == [(None, "the render timed out")]

    # Time enough from here on.
    renderer.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    pixmap = renderer.renderSync({}, {"color": GREEN}, _template())
    assert _color(pixmap) == GREEN


def test_a_late_ready_is_not_taken_for_the_next_render(renderer):
    """Its document is abandoned when it times out: the script still running
    in it cannot end the next render with the wrong frame."""
    results = []
    renderer.STAGE_TIMEOUT_MS = 100

    renderer.renderAsync({}, {"color": RED, "delay": 800}, _template(),
                         lambda p, e: results.append((p, e)))

    assert _waitUntil(lambda: results)
    assert results[0][0] is None

    renderer.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    pixmap = renderer.renderSync({}, {"color": GREEN, "delay": 1000},
                                 _template())
    assert _color(pixmap) == GREEN


def test_a_sync_render_that_hangs_answers_with_nothing(renderer):
    renderer.STAGE_TIMEOUT_MS = 500

    pixmap = renderer.renderSync({}, {"hang": True}, _template())

    assert pixmap.isNull()


def test_a_page_that_never_loads_fails_its_render(autonaApp):
    # No fixture page at all.
    layout = autonaApp.dir / "layout" / "thumbnail"
    assert not layout.exists()
    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = 500
    results = []
    try:
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results == [(None, "the render timed out")]
    finally:
        _destroy(made)


def test_the_next_render_loads_again_after_a_failed_load(autonaApp):
    # A page that is not there, and then is: nothing is on its way any more,
    # and waiting for it would fail every render from then on.
    layout = autonaApp.dir / "layout" / "thumbnail"
    assert not layout.exists()
    made = ThumbnailRenderer()
    made._view.grab = lambda: QPixmap(1920, 1080)
    results = []
    try:
        assert _waitUntil(lambda: made._load_query is None)
        assert not made._page_loaded

        layout.mkdir(parents=True)
        (layout / "thumbnail.html").write_text(PAGE, encoding="utf-8")
        made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append(e))

        assert _waitUntil(lambda: results)
        assert results == [""]
    finally:
        _destroy(made)
        shutil.rmtree(layout, ignore_errors=True)


def test_a_timeout_during_a_load_leaves_the_load_alone(renderer):
    """A page that takes longer than the timeout to load would never arrive if
    each render that gave up on it started it over: the next one gets it."""
    results = []
    loads = []
    load = renderer._view.load
    renderer._view.load = loads.append
    renderer.STAGE_TIMEOUT_MS = 100

    renderer.renderAsync({}, {"color": RED}, _template("type_b"),
                         lambda p, e: results.append((p, e)))

    assert _waitUntil(lambda: results)
    assert results == [(None, "the render timed out")]
    assert len(loads) == 1

    renderer.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, e: results.append((p, e)))
    assert len(loads) == 1

    load(loads[0])

    assert _waitUntil(lambda: len(results) == 2)
    assert _color(results[1][0]) == GREEN
    assert results[1][1] == ""


class _Ended:
    """What loadingChanged carries, for a load's end that is late."""

    def __init__(self, query, status=None):
        self._query = query
        self._status = status \
            or QWebEngineLoadingInfo.LoadStatus.LoadSucceededStatus

    def status(self):
        return self._status

    def url(self):
        url = QUrl("file:///thumbnail.html")
        url.setQuery(self._query)
        return url


def test_a_late_end_of_a_replaced_load_is_not_taken_for_the_new_one(renderer):
    """Its document is on its way out. Taken for the new one, the render would
    run its script there, and the new page would then start with no script."""
    replaced = renderer._load_query or f"load={renderer._load_number}"
    results = []
    loads = []
    load = renderer._view.load
    renderer._view.load = loads.append

    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, e: results.append((p, e)))
    assert renderer._load_query != replaced
    renderer._on_loading_changed(_Ended(replaced))

    assert renderer._page_loaded is False
    assert renderer._active.started is False
    # Nor does a failure end what it is not about.
    renderer._on_loading_changed(_Ended(
        replaced, QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus))
    assert renderer._load_query is not None

    load(loads[0])
    assert _waitUntil(lambda: results)
    assert _color(results[0][0]) == GREEN


# ── a render that cannot start ───────────────────────────────────────────

class _NotJson:
    pass


def test_a_render_that_cannot_start_fails_once_and_later(renderer):
    """Not by raising, on top of its ticket's watchdog ending it again."""
    results = []
    renderer.STAGE_TIMEOUT_MS = 300

    renderer.renderAsync({"x": _NotJson()}, {}, _template(),
                         lambda p, e: results.append((p, e)))

    # The caller has returned before it hears of it.
    assert results == []
    assert _waitUntil(lambda: results)
    assert results == [(None, "the render could not be started")]
    assert renderer._active is None
    assert not renderer._watchdog.isActive()

    # Past where its watchdog would have fired.
    _waitUntil(lambda: False, timeout=0.6)
    assert len(results) == 1

    renderer.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    assert _color(renderer.renderSync({}, {"color": GREEN}, _template())) \
        == GREEN


def test_a_render_that_cannot_start_once_its_page_is_there_fails_once(renderer):
    results = []

    renderer.renderAsync({"x": _NotJson()}, {}, _template("type_b"),
                         lambda p, e: results.append((p, e)))
    assert renderer._active.started is False

    assert _waitUntil(lambda: results)
    assert results == [(None, "the render could not be started")]
    assert renderer._active is None
    assert not renderer._watchdog.isActive()


def test_a_sync_render_that_cannot_start_answers_with_nothing(renderer):
    assert renderer.renderSync({"x": _NotJson()}, {}, _template()).isNull()
    assert renderer._active is None


def test_the_next_render_goes_on_after_one_that_cannot_start(renderer):
    results = []

    renderer.renderAsync({"x": _NotJson()}, {}, _template(),
                         lambda _p, e: results.append(e))
    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append(_color(p)))

    assert _waitUntil(lambda: len(results) == 2)
    assert results == ["the render could not be started", GREEN]


# ── the real way through: a request, the GUI thread, a job, the state ────

@pytest.fixture
def service(autonaApp, pages, tmp_path, monkeypatch):
    """The session's service and shell registration, with a renderer of the
    test's own (the singleton would outlive its stand-in page), the saved
    state and the output kept out of each other's way."""
    from src.AutonaScoreboardManager import AutonaScoreboardManager
    assert WebOps.startImageRender == AutonaScoreboardManager.instance\
        .StartImageRender

    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    # The offscreen grab is the same before a load and after it: a new page's
    # first picture would wait all of FRESH_FRAME_WAIT_MS for nothing.
    made.FRESH_FRAME_WAIT_MS = 0
    monkeypatch.setattr(ThumbnailRenderer, "_instance", made)
    settings = SettingsManager.settings
    SettingsManager.settings = {}
    with SM.lock:
        state = deep_clone(SM.state)
        saved = deep_clone(SM.lastSavedState)
    try:
        yield ThumbnailService.instance
    finally:
        assert TaskExecutor.instance.drain(WAIT_SECS)
        # What a test left running ends before its page goes.
        _waitUntil(lambda: _idle(ThumbnailService.instance))
        _destroy(made)
        SettingsManager.settings = settings
        with SM.lock:
            SM.state = state
            SM.lastSavedState = saved
        output = autonaApp.dir / "out" / "thumbnails"
        shutil.rmtree(output, ignore_errors=True)
        (autonaApp.dir / "tmp" / "thumbnail" / "preview.png").unlink(
            missing_ok=True)


def _idle(service):
    return service._running is None and service._next is None


def _match():
    SM.Set("game", {"codename": "tgame", "name": "T"})
    SM.Set("score.1.team.1.player.1.name", "Alpha")
    SM.Set("score.1.team.2.player.1.name", "Bravo")


def _rendered(revision):
    return (SM.Get(thumbnails.RENDER_PATH) or {}).get("revision", 0) \
        >= revision and SM.Get(thumbnails.RENDER_PATH)["status"] \
        not in ("idle", "running")


def test_a_preview_through_the_route_ends_in_a_png(service, client, autonaApp):
    response = client.post("/api/v1/thumbnail:preview", json={})

    assert response.status_code == 202, response.get_data(as_text=True)
    revision = response.get_json()["data"]["renderRevision"]
    assert _waitUntil(lambda: _rendered(revision))
    assert SM.Get(thumbnails.RENDER_PATH)["status"] == "done", \
        SM.Get(thumbnails.RENDER_PATH)

    image = client.get(f"/api/v1/thumbnail/preview?v={revision}")
    assert image.status_code == 200
    assert image.mimetype == "image/png"
    picture = Image.open(BytesIO(image.get_data()))
    assert picture.format == "PNG"
    assert picture.size == (1920, 1080)


def test_a_generate_through_the_route_saves_a_png_and_a_jpg(
        service, client, autonaApp):
    _match()

    response = client.post("/api/v1/thumbnail:generate", json={})

    assert response.status_code == 202, response.get_data(as_text=True)
    revision = response.get_json()["data"]["renderRevision"]
    assert _waitUntil(lambda: _rendered(revision))
    result = SM.Get(thumbnails.RENDER_PATH)
    assert result["status"] == "done", result
    assert result["path"].startswith("out/thumbnails/Alpha-vs-Bravo-")
    png = autonaApp.dir / result["path"]
    jpg = png.with_suffix(".jpg")
    assert Image.open(png).format == "PNG"
    assert Image.open(png).size == (1920, 1080)
    assert Image.open(jpg).format == "JPEG"
    assert Image.open(jpg).size == (1920, 1080)


def test_previews_while_one_runs_end_in_the_newest(service, client):
    first = client.post("/api/v1/thumbnail:preview", json={}).get_json()
    second = client.post("/api/v1/thumbnail:preview", json={}).get_json()
    third = client.post("/api/v1/thumbnail:preview", json={}).get_json()

    last = third["data"]["renderRevision"]
    assert second["data"]["renderRevision"] == last
    assert first["data"]["renderRevision"] == last - 1
    assert _waitUntil(lambda: _rendered(last))
    assert SM.Get(thumbnails.RENDER_PATH)["status"] == "done"
    assert SM.Get(thumbnails.RENDER_PATH)["revision"] == last


def test_the_legacy_way_is_not_disturbed_by_the_new_one(service, client):
    from src.thumbnail import main_generate_thumbnail as legacy

    _match()
    response = client.post("/api/v1/thumbnail:preview", json={})
    assert response.status_code == 202

    path = legacy.generate(SettingsManager)

    assert path.startswith("./out/thumbnails/Alpha-vs-Bravo-")
    assert Path(path).exists()
    assert Path(path).with_suffix(".jpg").exists()
    revision = response.get_json()["data"]["renderRevision"]
    assert _waitUntil(lambda: _rendered(revision))
    assert SM.Get(thumbnails.RENDER_PATH)["status"] == "done"


def test_the_encoder_gives_what_a_pixmap_saves(autonaApp, tmp_path):
    """The image a job encodes is byte for byte the file QPixmap.save()
    wrote: the legacy path's files and the new path's do not differ."""
    from src.AutonaScoreboardManager import _EncodeImage, _ImageEncoder

    pixmap = QPixmap(64, 36)
    pixmap.fill(QColor(10, 200, 30))
    png = tmp_path / "x.png"
    jpg = tmp_path / "x.jpg"
    pixmap.save(str(png))
    pixmap.save(str(jpg))

    image = pixmap.toImage()
    assert _EncodeImage(image, "PNG") == png.read_bytes()
    assert _EncodeImage(image, "JPG") == jpg.read_bytes()
    # And the encoder a render hands on, unless asked for the fast PNG.
    encode = _ImageEncoder(image)
    assert encode("PNG") == png.read_bytes()
    assert encode("JPG") == jpg.read_bytes()


def _ZlibLevelClass(png):
    """The FLEVEL of a PNG's image data: zlib's 0 (levels 0-1), 1 (2-5),
    2 (6, the default) or 3 (7-9)."""
    offset = 8
    while offset < len(png):
        length = int.from_bytes(png[offset:offset + 4], "big")
        if png[offset + 4:offset + 8] == b"IDAT":
            return png[offset + 9] >> 6
        offset += 12 + length
    raise AssertionError("a PNG without image data")


def test_the_preview_png_is_the_same_picture_compressed_less(autonaApp):
    """ADR-005 S4o judgment 23: `fast` is the preview's PNG -- a PNG still
    (the preview route answers image/png), the same pixels, at zlib level 1
    instead of the default 6. A JPG is the same either way."""
    from src.AutonaScoreboardManager import _EncodeImage, _ImageEncoder

    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    for k in range(30):
        painter.setPen(QColor.fromHsv((k * 37) % 360, 200, 220))
        painter.drawEllipse(10 + k * 4, 10 + k * 2, 200 - k * 5, 150 - k * 4)
    painter.end()
    encode = _ImageEncoder(image)

    fast = encode("PNG", fast=True)
    default = encode("PNG")

    assert fast.startswith(b"\x89PNG\r\n\x1a\n")
    assert QImage.fromData(fast).convertToFormat(image.format()) == image
    assert QImage.fromData(default).convertToFormat(image.format()) == image
    # Level 1, not 0 (stored: as big as the pixels).
    assert _ZlibLevelClass(fast) == 0
    assert len(fast) < 320 * 180 * 3 // 2
    assert _ZlibLevelClass(default) == 2
    assert default == _EncodeImage(image, "PNG")
    assert encode("JPG", fast=True) == encode("JPG") \
        == _EncodeImage(image, "JPG")


def test_the_shell_refuses_a_kind_it_does_not_know(autonaApp):
    from src.AutonaScoreboardManager import AutonaScoreboardManager

    with pytest.raises(ValueError):
        AutonaScoreboardManager.instance.StartImageRender(
            "poster", ({}, {}, {}), lambda *_: None)


def test_the_shell_hands_on_a_failed_render(pages, monkeypatch):
    from src.AutonaScoreboardManager import AutonaScoreboardManager

    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = 500
    monkeypatch.setattr(ThumbnailRenderer, "_instance", made)
    answers = []
    try:
        assert _waitUntil(lambda: made._page_loaded)
        AutonaScoreboardManager.instance.StartImageRender(
            "thumbnail", ({}, {"hang": True}, _template()),
            lambda encode, error: answers.append((encode, error)))
        assert _waitUntil(lambda: answers)
    finally:
        _destroy(made)

    assert answers == [(None, "the render timed out")]
