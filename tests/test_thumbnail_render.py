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
whose frame it got (and, there being no frame mark in it, the fixture does not
wait for one: FRAME_WAIT_MS 0). The tests through the shell use the real
grab().

What the renderer waits for before it grabs is pinned by its order, not its
pixels: RENDER_READY alone takes no picture, RENDER_PAINTED with the render's
number does, and the picture is a frame the view showed after the page's frame
mark (_markShowing() stands in for what the view shows).

Waits end as soon as the page has done what is waited for. Chromium runs a
hidden page's renderer at background priority, so on a busy machine a load of
even this small page takes many seconds: no test depends on how long anything
takes, and the watchdog's real 10 seconds are raised unless it is what is under
test (tests/test_layout_preview_lifecycle.py, same reasoning).
"""
import base64
import copy
import json
import os
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
from tests.conftest import remove_tree
from tests.fixtures import template_packages
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
def pages(autonaApp, previewListener):
    """The stand-in page for both templates, in the runtime's layout/, with the
    shipped manifest and template file (the catalog's thumbnail packages,
    ADR-007 T1)."""
    created = template_packages.Install(
        autonaApp.dir / "layout", template_packages.THUMBNAIL_FOLDERS,
        page=PAGE, contract=1)
    yield
    # The session's runtime: a folder left behind (a handle a Windows
    # scanner or the preview listener still has open) is not made again by
    # the next Install, so nothing removes it -- remove_tree waits, or fails
    # here.
    for directory in created:
        remove_tree(directory)


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
    # Its grab() never shows the page's frame mark: the first one after the
    # page said it painted is the picture.
    made.FRAME_WAIT_MS = 0
    painted = []

    def on_title(title):
        if title.startswith(PAINTED):
            painted.append(title[len(PAINTED):])

    def grab(rect=None):
        pixmap = QPixmap(1920, 1080)
        pixmap.fill(QColor(painted[-1] if painted else "#000000"))
        return pixmap if rect is None else pixmap.copy(rect)

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


def test_renders_with_a_taker_all_run_in_order(renderer):
    results = []

    for color in (RED, GREEN, BLUE):
        renderer.renderAsync(
            {}, {"color": color}, _template(),
            lambda p, _e: results.append(_color(p)))

    assert _waitUntil(lambda: len(results) == 3)
    assert results == [RED, GREEN, BLUE]


def test_the_qt_panels_way_in_is_gone():
    # ADR-005 S4p: render() and its renderComplete signal were the Qt panel's
    # only, and went with it. A render has a taker, always.
    assert not hasattr(ThumbnailRenderer, "render")
    assert not hasattr(ThumbnailRenderer, "renderComplete")


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


def _frame(width, height, color, mark=None, scale=1, rect=None, at=0):
    """A picture of the view: `color`, with the frame mark of render number
    `mark` (None: none) `at` pixels from its left edge (0: in its corner), at
    `scale` device pixels a pixel; only `rect` of it (the view's pixels) when
    given, as grab(rect) does."""
    pixmap = QPixmap(round(width * scale), round(height * scale))
    pixmap.fill(QColor(color))
    if mark is not None:
        painter = QPainter(pixmap)
        size = ThumbnailRenderer.MARK_SIZE
        for block, rgb in enumerate(ThumbnailRenderer._mark_colors(mark)):
            painter.fillRect(round((at + block * size) * scale), 0,
                             round(size * scale), round(size * scale),
                             QColor(*rgb))
        painter.end()
    if rect is not None:
        pixmap = pixmap.copy(round(rect.x() * scale), round(rect.y() * scale),
                             round(rect.width() * scale),
                             round(rect.height() * scale))
    return pixmap


def _markShowing(renderer, color):
    """grab() as what the view shows: shown["color"], and the frame mark of
    render shown["mark"] (None: none), until the test changes them. Every
    grab is in `grabs`, as (color, mark, whether of the whole view)."""
    shown = {"color": color, "mark": None}
    grabs = []

    def grab(rect=None):
        grabs.append((shown["color"], shown["mark"], rect is None))
        return _frame(1920, 1080, shown["color"], shown["mark"], rect=rect)

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
    # The whole view's grabs (not its corner's, looked at for the mark).
    renderer._view.grab = lambda rect=None: (
        rect is None and grabs.append(1)) or grab(rect)
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
    # No frame mark waited for (the fixture): the first grab.
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


def test_a_page_that_does_not_say_it_painted_is_taken_after_the_wait(
        renderer):
    """Its fonts never ready: the picture is looked for all the same."""
    renderer.PAINT_WAIT_MS = 300
    _evaluate(renderer, "Object.defineProperty(document, 'fonts', "
                        "{value: {ready: new Promise(() => {})}}); 1")
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
    assert "fonts=" in lines[0] and "images=" in lines[0]


# ── the frame the picture is ─────────────────────────────────────────────

MARKS_ON_PAGE = ("JSON.stringify([...document.querySelectorAll("
                 "'[data-autona-frame-mark]')].map("
                 "(mark) => mark.dataset.autonaFrameMark))")


def test_the_picture_is_a_frame_after_the_pages_mark_went(renderer):
    """With software compositing (a GPU-less CI runner) the view showed a
    frame from before the drawing for seconds after the page said it painted:
    its names, characters and squeezed text missing, or half the page white.
    Grabs that agreed, or a wait that ran out, took it. The page marks its
    corner once it has drawn; the picture is the first frame without the mark
    after one with it: it was drawn after the drawing."""
    renderer.FRAME_WAIT_MS = LONG_TIMEOUT_MS
    shown, grabs = _markShowing(renderer, RED)
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((_color(p), e)))
    assert _waitUntil(lambda: grabs)
    number = renderer._active.number
    # The view still shows a frame from before the drawing: not taken.
    _waitUntil(lambda: False, timeout=0.3)
    assert results == []
    assert json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == [str(number)] * 2

    # The frame with the drawing and the mark: the mark is taken away...
    shown.update(color=GREEN, mark=number)
    assert _waitUntil(
        lambda: json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == [])
    _waitUntil(lambda: False, timeout=0.3)
    assert results == []

    # ...and the frame after it is the picture.
    shown["mark"] = None
    assert _waitUntil(lambda: results)
    assert results == [(GREEN, "")]
    assert grabs[-1] == (GREEN, None, True)
    # Until then only the view's corner was grabbed.
    assert [whole for _c, _m, whole in grabs].count(True) == 1


def test_another_renders_mark_is_not_this_ones(renderer):
    renderer.FRAME_WAIT_MS = LONG_TIMEOUT_MS
    shown, grabs = _markShowing(renderer, RED)
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((_color(p), e)))
    assert _waitUntil(lambda: grabs)
    number = renderer._active.number
    shown["mark"] = number - 1
    _waitUntil(lambda: False, timeout=0.3)
    assert results == []
    assert json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == [str(number)] * 2

    shown.update(color=GREEN, mark=number)
    assert _waitUntil(
        lambda: json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == [])
    shown["mark"] = None
    assert _waitUntil(lambda: results)
    assert results == [(GREEN, "")]


def test_a_drawing_that_does_not_reach_the_view_is_taken_after_the_wait(
        renderer):
    """Qt's offscreen platform on a desktop composites no page: the mark is
    never seen. The picture is taken as it is, and said so; the mark goes."""
    renderer.FRAME_WAIT_MS = 300
    _shown, grabs = _markShowing(renderer, RED)
    results = []
    logged, sink = _warnings()
    try:
        renderer.renderAsync({}, {"color": GREEN}, _template(),
                             lambda p, e: results.append((_color(p), e)))
        assert _waitUntil(lambda: results)
    finally:
        logger.remove(sink)

    assert results == [(RED, "")]
    assert len(grabs) > 1
    assert any("did not reach the view" in line for line in logged)
    assert _waitUntil(
        lambda: json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == [])


def test_a_mark_that_does_not_go_is_taken_after_the_wait_without_it(
        renderer):
    """The frame with the drawing and the mark, the wait over: the picture,
    but not the mark (the picture's pixels under it in its place)."""
    renderer.FRAME_WAIT_MS = 300
    shown, _grabs = _markShowing(renderer, RED)
    results = []
    logged, sink = _warnings()
    try:
        renderer.renderAsync({}, {"color": GREEN}, _template(),
                             lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: renderer._active.painted)
        number = renderer._active.number
        shown.update(color=GREEN, mark=number)
        assert _waitUntil(lambda: results)
    finally:
        logger.remove(sink)

    pixmap, error = results[0]
    assert error == ""
    image = pixmap.toImage()
    assert not ThumbnailRenderer._marked(image, number, 1920)
    size = ThumbnailRenderer.MARK_SIZE
    assert {image.pixelColor(x, y).name() for x in range(2 * size)
            for y in range(size)} == {GREEN}
    assert any("frame mark did not go" in line for line in logged)


def test_a_mark_not_seen_is_not_in_the_picture_either(renderer):
    """Taken away when the wait ran out, it may still reach the view at the
    picture: not in the picture."""
    renderer.FRAME_WAIT_MS = 300
    shown, _grabs = _markShowing(renderer, BLUE)
    original = renderer._view.grab

    def grab(rect=None):
        # The corner never shows it; the picture does.
        if rect is None:
            shown["mark"] = renderer._active.number
        return original(rect)

    renderer._view.grab = grab
    results = []
    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
    assert _waitUntil(lambda: results)

    pixmap, error = results[0]
    assert error == ""
    assert not ThumbnailRenderer._marked(pixmap.toImage(), shown["mark"],
                                         1920)
    assert pixmap.toImage().pixelColor(1, 1).name() == BLUE


def test_the_wait_for_the_frame_is_what_the_stage_leaves(renderer):
    """A slow page's picture is taken before its stage's time is over, as
    it was before the mark (maybe from before its drawing), not failed: the
    wait for the frame is what the watchdog leaves, less the reserve."""
    renderer.STAGE_TIMEOUT_MS = 2500
    renderer.FRAME_RESERVE_MS = 1000
    renderer.FRAME_WAIT_MS = LONG_TIMEOUT_MS
    _markShowing(renderer, RED)
    results = []
    logged, sink = _warnings()
    try:
        renderer.renderAsync({}, {"color": GREEN}, _template(),
                             lambda p, e: results.append((_color(p), e)))
        assert _waitUntil(lambda: results)
    finally:
        logger.remove(sink)

    assert results == [(RED, "")]
    assert any("did not reach the view" in line for line in logged)


def test_the_wait_for_the_frame_is_one_for_both(renderer):
    renderer.FRAME_WAIT_MS = 6000
    renderer.FRAME_RESERVE_MS = 1500
    renderer._watchdog.start(60000)
    try:
        assert renderer._frame_wait_ms() == 6000
        renderer._watchdog.start(5000)
        assert 3000 < renderer._frame_wait_ms() <= 3500
        renderer._watchdog.start(1000)
        assert renderer._frame_wait_ms() == 0
    finally:
        renderer._watchdog.stop()
    assert renderer._frame_wait_ms() == 6000


def test_a_render_that_timed_out_while_waiting_for_its_frame_is_not_taken(
        renderer):
    renderer.FRAME_WAIT_MS = LONG_TIMEOUT_MS
    _shown, grabs = _markShowing(renderer, RED)
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p and _color(p), e)))
    assert _waitUntil(lambda: grabs)
    renderer._on_timeout()
    del grabs[:]

    _waitUntil(lambda: False, timeout=0.7)
    assert grabs == []
    assert results == [(None, "the render timed out")]


@pytest.mark.parametrize("scale", [1, 1.25, 1.75, 2])
def test_the_mark_is_found_at_the_pictures_scale(scale):
    """The view's picture is in device pixels (a screen at 175 %): the whole
    view, or its corner (grab(rect)), at any size."""
    found = ThumbnailRenderer._marked
    image = _frame(1920, 1080, RED, 7, scale).toImage()
    assert found(image, 7, 1920)
    # Another render's.
    assert not found(image, 6, 1920)
    assert not found(image, 8, 1920)
    assert not found(_frame(1920, 1080, RED, None, scale).toImage(), 7, 1920)
    corner = ThumbnailRenderer._mark_rect()
    assert found(_frame(960, 1080, BLUE, 3, scale, corner).toImage(), 3,
                 corner.width())
    # Only in its corner: the same blocks anywhere else are not it.
    assert not found(_frame(1920, 1080, RED, 7, scale, at=100).toImage(), 7,
                     1920)


MARK_GEOMETRY = """JSON.stringify([...document.querySelectorAll(
  '[data-autona-frame-mark]')].map((mark) => {
    const box = mark.getBoundingClientRect();
    return [mark.dataset.autonaFrameMark, box.left, box.top, box.width,
            box.height, getComputedStyle(mark).backgroundColor,
            getComputedStyle(mark).opacity];
  }))"""
# A template's CSS that would move, hide or resize any div.
HOSTILE_CSS = """div, html > * { zoom: 2 !important; transform: scale(3) !important;
  scale: 2 !important; translate: 30px 30px !important;
  rotate: 45deg !important; max-width: 1px !important;
  max-height: 1px !important; min-width: 50px !important;
  min-height: 50px !important; clip-path: inset(50%) !important;
  mask: linear-gradient(transparent, transparent) !important;
  content-visibility: hidden !important; opacity: 0 !important;
  position: static !important; margin: 20px !important;
  border: 9px solid red !important; filter: invert(1) !important; }"""


@pytest.mark.parametrize("size", [4, 6])
def test_the_page_draws_the_mark_the_renderer_looks_for(renderer, monkeypatch,
                                                        size):
    """One definition (_mark_blocks): the page draws what _marked looks for,
    at its size and colours, whatever a template's CSS says of a div, and a
    renderer that says another MARK_SIZE draws that."""
    monkeypatch.setattr(ThumbnailRenderer, "MARK_SIZE", size)
    _evaluate(renderer, "(() => { const style = document.createElement("
                        "'style'); style.textContent = %s; "
                        "document.head.appendChild(style); return 1; })()"
              % json.dumps(HOSTILE_CSS))
    renderer._page.runJavaScript(renderer._paint_script(41))
    assert _waitUntil(lambda: renderer._page.title().startswith(
        "RENDER_PAINTED 41 "))

    marks = json.loads(_evaluate(renderer, MARK_GEOMETRY))
    assert marks == [
        ["41", block * size, 0, size, size, "rgb(%d, %d, %d)" % rgb, "1"]
        for block, rgb in enumerate(ThumbnailRenderer._mark_colors(41))]
    renderer._page.runJavaScript(renderer.UNMARK_SCRIPT % 41)
    assert _waitUntil(
        lambda: json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == [])


def test_a_late_script_of_a_render_before_does_nothing(renderer):
    """Its waits ended after the next render's script began: it would take
    that render's mark away and put its own."""
    renderer._page.runJavaScript(renderer._paint_script(60))
    assert _waitUntil(lambda: renderer._page.title().startswith(
        "RENDER_PAINTED 60 "))
    renderer._page.runJavaScript(renderer._paint_script(59))
    _waitUntil(lambda: False, timeout=0.5)

    assert json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == ["60", "60"]
    assert renderer._page.title().startswith("RENDER_PAINTED 60 ")
    # Only its own mark is taken away by a render.
    renderer._page.runJavaScript(renderer.UNMARK_SCRIPT % 59)
    _waitUntil(lambda: False, timeout=0.2)
    assert json.loads(_evaluate(renderer, MARKS_ON_PAGE)) == ["60", "60"]


# ── the pages as shipped ─────────────────────────────────────────────────

@pytest.fixture
def shippedPages(autonaApp, previewListener):
    """The templates as shipped in the runtime's layout/. Copies: a link there
    would have a cleanup that goes wrong delete the project's. layout/include/
    is 19 MB, of which the pages only want the four scripts they share
    (ADR-007 D2: a package reads nothing outside its folder but include/)."""
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
    for script in ("jquery-3.7.1.min.js", "lodash.min.js",
                   "thumbnail_assets.js", "autona.js"):
        shutil.copy(PROJECT_ROOT / "layout" / "include" / script, include)
    try:
        yield
    finally:
        for directory in created:
            remove_tree(directory)  # See `pages`.


@pytest.mark.parametrize("templateId", ["type_a", "type_b"])
def test_a_shipped_page_draws_what_it_is_given(shippedPages, templateId):
    """The real thumbnail.js, on a payload with what is awkward in it. Not its
    pixels, which the offscreen platform does not show. On template contract
    2 (ADR-008 D7 R2) an exception in it is the render's error, so no error
    says it got to the end; the name says it drew the payload."""
    folder = template_packages.BUNDLED["thumbnail"][templateId]
    template = json.loads(
        (PROJECT_ROOT / "layout" / folder
         / template_packages.Manifest(folder)["template"])
        .read_text(encoding="utf-8"))
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
    made.FRAME_WAIT_MS = 0  # See the `service` fixture.
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


# ── template contract 2 (ADR-008 D7 R2) ─────────────────────────────────

# A contract 2 page: autona.js draws the payload in its onRender, which keeps
# the colour it drew (the offscreen platform's grab shows no page) or fails
# as the config says.
RENDER_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="../include/autona.js"></script></head><body><script>
Autona.onRender(async (payload) => {
  const config = payload.config;
  if (config.throw) throw new TypeError("drawing broke");
  if (config.reject) await Promise.reject(new RangeError("late"));
  // Not waited for: thrown once the drawing has returned.
  if (config.after) setTimeout(() => { throw new Error("after"); },
                               config.after);
  window.drawn = config.color;
});
</script></body></html>
"""
# One that forgot autona.js.
NO_AUTONA_PAGE = """<!doctype html><html><body><script>
window.Autona = undefined;
</script></body></html>
"""


@pytest.fixture
def renderPages(autonaApp, previewListener):
    """RENDER_PAGE for both templates, on contract 2, with autona.js in the
    runtime's layout/include/ (a copy)."""
    created = template_packages.Install(
        autonaApp.dir / "layout", template_packages.THUMBNAIL_FOLDERS,
        page=RENDER_PAGE, contract=2)
    include = autonaApp.dir / "layout" / "include"
    assert not include.exists()
    include.mkdir()
    created.append(include)
    shutil.copy(PROJECT_ROOT / "layout" / "include" / "autona.js", include)
    yield autonaApp.dir / "layout"
    for directory in created:
        remove_tree(directory)  # See `pages`.


def _contract2Renderer():
    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    made.FRAME_WAIT_MS = 0  # See the `service` fixture.
    return made


def _drawn(made):
    said = []
    made._page.runJavaScript("window.drawn || null", said.append)
    assert _waitUntil(lambda: said)
    return said[0]


def test_a_contract_2_page_draws_in_onrender(renderPages):
    made = _contract2Renderer()
    results = []
    try:
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        pixmap, error = results[0]
        assert error == ""
        assert (pixmap.width(), pixmap.height()) == (1920, 1080)
        assert _drawn(made) == GREEN
    finally:
        _destroy(made)


@pytest.mark.parametrize("config, message", [
    ({"throw": True}, "TypeError: drawing broke"),
    ({"reject": True}, "RangeError: late"),
])
def test_a_contract_2_exception_is_no_picture_and_says_why(
        renderPages, config, message):
    made = _contract2Renderer()
    results = []
    try:
        made.renderAsync({}, config, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        pixmap, error = results[0]
        assert pixmap is None
        assert error == thumbnails.TEMPLATE_ERROR
        assert [(item["kind"], item["message"]) for item in error.errors] \
            == [("onRender", message)]
        # The entry page (with the renderer's ?load=), whose script threw.
        assert "/layout/thumbnail/thumbnail.html" in error.errors[0]["source"]
        assert error.errors[0]["line"] > 0

        # The next one draws: the page is not broken for good.
        results.clear()
        made.renderAsync({}, {"color": BLUE}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results[0][1] == ""
        assert _drawn(made) == BLUE
    finally:
        _destroy(made)


@pytest.mark.parametrize("after", [1, 40])
def test_a_contract_2_error_after_the_drawing_is_its_render_s(
        renderPages, after):
    """An error the page throws after its listener returned (here a timer:
    during the waits, or after it said "ok" -- the renderer reads again
    before it gives the picture) fails that render; the next one, which
    throws nothing, draws: the error is not carried into it."""
    made = _contract2Renderer()
    # The picture comes after its frame has reached the view: here, where
    # the frame mark is never seen, after the whole wait for it.
    made.FRAME_WAIT_MS = 300
    results = []
    try:
        made.renderAsync({}, {"color": GREEN, "after": after}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        pixmap, error = results[0]
        assert pixmap is None and error == thumbnails.TEMPLATE_ERROR
        assert [item["message"] for item in error.errors] == [
            "Uncaught Error: after"]

        results.clear()
        made.renderAsync({}, {"color": BLUE}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results[0][1] == ""
        assert _drawn(made) == BLUE
    finally:
        _destroy(made)


EDITED_PAGE = RENDER_PAGE.replace("window.drawn = config.color;",
                                  'window.drawn = "edited";')


def test_an_edited_template_is_loaded_again_before_its_next_render(
        renderPages):
    """The page loaded is not drawn again once its folder's files changed:
    the live preview draws the edited files, so Generate must too."""
    made = _contract2Renderer()
    results = []
    try:
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert _drawn(made) == GREEN
        loads = made._load_number

        page = renderPages / "thumbnail" / "thumbnail.html"
        page.write_text(EDITED_PAGE, encoding="utf-8")
        stamp = page.stat().st_mtime_ns + 5_000_000_000
        os.utime(page, ns=(stamp, stamp))
        results.clear()
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results[0][1] == ""
        assert made._load_number == loads + 1
        assert _drawn(made) == "edited"

        # Unchanged: not loaded again.
        results.clear()
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert made._load_number == loads + 1
    finally:
        _destroy(made)


CONTRACT_1_PAGE = """<!doctype html><html><body><script>
function initThumbnail(payload) {
  window.drawn = "contract 1";
  document.title = "RENDER_READY";
}
</script></body></html>
"""


def test_a_template_moved_to_contract_2_is_loaded_again(renderPages):
    """Loaded as contract 1, then moved to 2 (its page and its manifest):
    the next render loads it again and draws, not "no autona.js" for good."""
    folder = renderPages / "thumbnail"
    (folder / "settings.json").write_text(
        template_packages.Settings("thumbnail", contract=1), encoding="utf-8")
    (folder / "thumbnail.html").write_text(CONTRACT_1_PAGE, encoding="utf-8")
    made = _contract2Renderer()
    results = []
    try:
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results[0][1] == ""
        assert _drawn(made) == "contract 1"

        (folder / "settings.json").write_text(
            template_packages.Settings("thumbnail", contract=2),
            encoding="utf-8")
        (folder / "thumbnail.html").write_text(RENDER_PAGE, encoding="utf-8")
        results.clear()
        made.renderAsync({}, {"color": BLUE}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results[0][1] == ""
        assert _drawn(made) == BLUE
    finally:
        _destroy(made)


def test_a_page_found_without_autona_js_is_loaded_again(renderPages):
    """"missing" marks the page: once autona.js is on it (the same files'
    stamp aside), the next render loads it again and draws."""
    page = renderPages / "thumbnail" / "thumbnail.html"
    page.write_text(NO_AUTONA_PAGE, encoding="utf-8")
    made = _contract2Renderer()
    results = []
    try:
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results[0][1] == thumbnails.TEMPLATE_ERROR
        loads = made._load_number
        assert made._page_stale

        results.clear()
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert made._load_number == loads + 1
    finally:
        _destroy(made)


HANGING_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="../include/autona.js"></script></head><body><script>
Autona.onRender(() => {
  setTimeout(() => { throw new TypeError("stuck"); }, 0);
  return new Promise(() => {});
});
</script></body></html>
"""


def test_a_contract_2_timeout_says_the_errors_its_page_caught(renderPages):
    (renderPages / "thumbnail" / "thumbnail.html").write_text(
        HANGING_PAGE, encoding="utf-8")
    made = _contract2Renderer()
    made.STAGE_TIMEOUT_MS = 1500
    results = []
    try:
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        pixmap, error = results[0]
        assert pixmap is None and error == thumbnails.TEMPLATE_ERROR
        assert [item["message"] for item in error.errors] == [
            "Uncaught TypeError: stuck"]
    finally:
        _destroy(made)


def test_a_contract_2_page_without_autona_js_says_so(renderPages):
    (renderPages / "thumbnail" / "thumbnail.html").write_text(
        NO_AUTONA_PAGE, encoding="utf-8")
    made = _contract2Renderer()
    results = []
    try:
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        pixmap, error = results[0]
        assert pixmap is None and error == thumbnails.TEMPLATE_ERROR
        [item] = error.errors
        assert item["kind"] == "autona" and "autona.js" in item["message"]
    finally:
        _destroy(made)


THROWING_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="../include/autona.js"></script></head><body><script>
Autona.onRender(() => { throw new TypeError("drawing broke"); });
</script></body></html>
"""


def test_a_contract_2_template_error_reaches_the_state(
        renderPages, autonaApp, client, monkeypatch):
    """POST :preview through the service and the shell's renderer:
    thumbnail.render is the error, with the template's errors."""
    (renderPages / "thumbnail" / "thumbnail.html").write_text(
        THROWING_PAGE, encoding="utf-8")
    made = _contract2Renderer()
    monkeypatch.setattr(ThumbnailRenderer, "_instance", made)
    settings = SettingsManager.settings
    SettingsManager.settings = {}
    with SM.lock:
        state = deep_clone(SM.state)
        saved = deep_clone(SM.lastSavedState)
    try:
        response = client.post("/api/v1/thumbnail:preview", json={})
        assert response.status_code == 202, response.get_data(as_text=True)
        revision = response.get_json()["data"]["renderRevision"]
        assert _waitUntil(lambda: _rendered(revision))
        render = SM.Get(thumbnails.RENDER_PATH)
        assert render["status"] == "error", render
        assert render["error"] == thumbnails.TEMPLATE_ERROR
        assert [(item["kind"], item["message"])
                for item in render["errors"]] == [
            ("onRender", "TypeError: drawing broke")]
        assert set(render["errors"][0]) == {
            "kind", "message", "source", "line", "column"}
    finally:
        assert TaskExecutor.instance.drain(WAIT_SECS)
        _waitUntil(lambda: _idle(ThumbnailService.instance))
        _destroy(made)
        SettingsManager.settings = settings
        with SM.lock:
            SM.state = state
            SM.lastSavedState = saved


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


def test_a_template_the_catalog_never_listed_cannot_start(autonaApp):
    # No package at all (ADR-007 T1): there is no page to look for, so nothing
    # is loaded at construction and the render fails at once, not a timeout
    # later. A page listed and then gone is loaded and fails its load
    # (test_a_failed_load_the_next_render_took_over_is_tried_again).
    layout = autonaApp.dir / "layout" / "thumbnail"
    assert not layout.exists()
    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    results = []
    try:
        assert made._load_query is None and made._load_number == 0
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append((p, e)))
        assert _waitUntil(lambda: results)
        assert results == [(None, "the render could not be started")]
    finally:
        _destroy(made)


def test_the_next_render_loads_again_after_a_failed_load(
        autonaApp, previewListener):
    # A package that is not there, and then is: nothing is on its way any
    # more, and waiting for it would fail every render from then on.
    layout = autonaApp.dir / "layout" / "thumbnail"
    assert not layout.exists()
    made = ThumbnailRenderer()
    made._view.grab = lambda rect=None: QPixmap(1920, 1080)
    results = []
    try:
        assert _waitUntil(lambda: made._load_query is None)
        assert not made._page_loaded

        template_packages.Install(autonaApp.dir / "layout", ("thumbnail",),
                                  page=PAGE, contract=1)
        made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
        made.renderAsync({}, {"color": GREEN}, _template(),
                         lambda p, e: results.append(e))

        assert _waitUntil(lambda: results)
        assert results == [""]
    finally:
        _destroy(made)
        remove_tree(layout)  # See `pages`.


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


def test_a_failed_load_the_next_render_took_over_is_tried_again(
        renderer, autonaApp):
    """A render that timed out leaves its page loading for the next one. When
    that load then fails, the next one has not tried yet: waiting for its own
    watchdog instead, it failed a whole timeout later, the page there all along
    (tests/test_result_render.py's page that never loads, 3 CI runs in 30)."""
    page = autonaApp.dir / "layout" / "thumbnail_type_b" / "thumbnail.html"
    page.unlink()
    results = []
    loads = []
    load = renderer._view.load
    renderer._view.load = loads.append
    renderer.STAGE_TIMEOUT_MS = 100

    renderer.renderAsync({}, {"color": RED}, _template("type_b"),
                         lambda p, e: results.append((p, e)))
    assert _waitUntil(lambda: results)
    assert results == [(None, "the render timed out")]

    renderer.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, e: results.append((p, e)))
    assert len(loads) == 1

    # The first render's load ends, and its page was not there.
    load(loads[0])
    assert _waitUntil(lambda: len(loads) == 2)

    page.write_text(PAGE, encoding="utf-8")
    load(loads[1])
    assert _waitUntil(lambda: len(results) == 2)
    assert results[1][1] == ""
    assert _color(results[1][0]) == GREEN


def test_a_renders_own_failed_load_is_left_to_its_watchdog(renderer, autonaApp):
    """Tried again at once, a page that is not there would be loaded over and
    over until the timeout. The next render tries it
    (test_the_next_render_loads_again_after_a_failed_load)."""
    (autonaApp.dir / "layout" / "thumbnail_type_b" / "thumbnail.html").unlink()
    results = []
    loads = []
    load = renderer._view.load
    renderer._view.load = loads.append
    statuses = []
    renderer._page.loadingChanged.connect(
        lambda info: statuses.append(info.status()))

    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, e: results.append((p, e)))
    assert len(loads) == 1
    load(loads[0])

    # Connected after the renderer: heard after it.
    assert _waitUntil(lambda: QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus
                      in statuses)
    # Past where a try again would have begun (the next pass of the loop).
    _waitUntil(lambda: len(loads) > 1, timeout=0.5)
    assert len(loads) == 1
    assert results == []
    assert renderer._active is not None and renderer._watchdog.isActive()


def test_a_page_is_loaded_from_the_preview_listener(renderer, previewListener):
    """The package's entry page on the preview listener's origin (ADR-007
    D4), never file://: /layout/<folder>/<entry>, with the load's number."""
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, e: results.append((p, e)))

    assert _waitUntil(lambda: results)
    assert results[0][1] == ""
    assert _color(results[0][0]) == GREEN
    url = renderer._view.url()
    assert (url.scheme(), url.host(), url.port()) \
        == ("http", "127.0.0.1", previewListener)
    assert url.path() == "/layout/thumbnail_type_b/thumbnail.html"
    assert url.query() == renderer._load_query or url.query().startswith(
        "load=")


def test_a_page_is_looked_up_where_the_app_started(
        renderer, autonaApp, tmp_path, monkeypatch):
    """Not in the process's current directory, which nothing of the app's
    changes and still was, for a moment, another (in test runs, near a first
    WebEngine page): the page looked up then was the shipped one (in the
    directory pytest started in) or none (in site-packages/PySide6). The
    preview listener serves the catalog's folder (LayoutCatalog.
    AppDirectory), not the working directory (ADR-007 D4)."""
    elsewhere = tmp_path / "elsewhere"
    other = elsewhere / "layout" / "thumbnail_type_b" / "thumbnail.html"
    other.parent.mkdir(parents=True)
    other.write_text(PAGE.replace('(config.color || "#808080")', f'"{BLUE}"'),
                     encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    results = []

    renderer.renderAsync({}, {"color": GREEN}, _template("type_b"),
                         lambda p, e: results.append((p, e)))

    assert _waitUntil(lambda: results)
    assert results[0][1] == ""
    assert _color(results[0][0]) == GREEN
    assert renderer._view.url().path() \
        == "/layout/thumbnail_type_b/thumbnail.html"


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
    from src.AutonaShellOperations import AutonaShellOperations
    assert WebOps.startImageRender == AutonaShellOperations.instance\
        .StartImageRender

    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = LONG_TIMEOUT_MS
    # The offscreen grab never shows the page's frame mark: each picture
    # would wait all of FRAME_WAIT_MS for nothing.
    made.FRAME_WAIT_MS = 0
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
        remove_tree(output)
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


def test_the_shells_synchronous_thumbnail_draws_a_board_with_none_built(
        service, autonaApp):
    """ADR-005 S4y: the legacy route's and the X post's thumbnail is core's
    ThumbnailService.DrawBoard with the desktop's synchronous draw
    (AutonaShellOperations.DrawThumbnail), the real renderer: a PNG and a
    JPG at broadcast size, with no board made or asked for (no board widget
    built, until ADR-005 S4ac-3 removed them)."""
    from src.AutonaShellOperations import AutonaShellOperations

    autonaApp.dropAllBoards()
    _match()
    published = copy.deepcopy(SM.Get(thumbnails.RENDER_PATH))

    path = AutonaShellOperations.instance.DrawThumbnail(1)

    assert path.startswith("./out/thumbnails/Alpha-vs-Bravo-")
    assert Image.open(path).format == "PNG"
    assert Image.open(path).size == (1920, 1080)
    jpg = Path(path).with_suffix(".jpg")
    assert Image.open(jpg).format == "JPEG"
    assert Image.open(jpg).size == (1920, 1080)
    from src.core.Actions import AutonaActions
    assert not any(AutonaActions.instance.BoardEnsured(n) for n in (1, 2, 3))
    # Not a run of the lane: nothing published.
    assert SM.Get(thumbnails.RENDER_PATH) == published


def test_the_encoder_gives_what_a_pixmap_saves(autonaApp, tmp_path):
    """The image a job encodes is byte for byte the file QPixmap.save()
    wrote: the legacy path's files and the new path's do not differ."""
    from src.AutonaShellOperations import _EncodeImage, _ImageEncoder

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
    from src.AutonaShellOperations import _EncodeImage, _ImageEncoder

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
    from src.AutonaShellOperations import AutonaShellOperations

    with pytest.raises(ValueError):
        AutonaShellOperations.instance.StartImageRender(
            "poster", ({}, {}, {}), lambda *_: None)


def test_the_shell_hands_on_a_failed_render(pages, monkeypatch):
    from src.AutonaShellOperations import AutonaShellOperations

    made = ThumbnailRenderer()
    made.STAGE_TIMEOUT_MS = 500
    monkeypatch.setattr(ThumbnailRenderer, "_instance", made)
    answers = []
    try:
        assert _waitUntil(lambda: made._page_loaded)
        AutonaShellOperations.instance.StartImageRender(
            "thumbnail", ({}, {"hang": True}, _template()),
            lambda encode, error: answers.append((encode, error)))
        assert _waitUntil(lambda: answers)
    finally:
        _destroy(made)

    assert answers == [(None, "the render timed out")]


def test_a_synchronous_draw_says_a_template_s_first_error(
        autonaApp, monkeypatch):
    """The legacy routes' synchronous draw (DrawThumbnailNow): a template's
    error is in what it raises -- its first message -- and not in the log
    (a template's message may hold a player's name)."""
    from src import AutonaShellOperations as shell
    from src.core.RenderLane import TemplateError

    class Failing:
        syncError = TemplateError([
            {"kind": "onRender", "message": "TypeError: Alice broke"},
            {"kind": "error", "message": "second"}])

        def renderSync(self, data, config, template):
            return QPixmap()

    monkeypatch.setattr(ThumbnailRenderer, "instance",
                        classmethod(lambda cls: Failing()))
    logged = []
    sink = logger.add(lambda message: logged.append(str(message)))
    try:
        with pytest.raises(shell.ThumbnailNotDrawn) as raised:
            shell.DrawThumbnailNow(({}, {}, {}))
    finally:
        logger.remove(sink)
    assert raised.value.code == thumbnails.TEMPLATE_ERROR
    assert str(raised.value) == ("Thumbnail rendering failed: template_error:"
                                 " TypeError: Alice broke")
    assert not any("Alice" in line for line in logged)
