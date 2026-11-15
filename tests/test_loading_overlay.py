"""The loading spinner over the main window (autona_splash.LoadingOverlay).

The window starts its web server once the window and the spinner are painted
(Window._StartWebServer through call_when_painted; ADR-005 S-final SF-5b-3):
the server's construction holds the GUI thread for a few seconds, so the
operator faces a painted window and spinner meanwhile, not an unpainted one.
What has to hold:

* the callback waits for the overlay's first paint, and runs after it from
  the event loop -- never inside the paint event (a modal dialog there would
  be a nested one: agent-pitfalls #9) nor synchronously in the call;
* a window that is never painted (started minimized) does not hold it back:
  past ``max_ms`` it runs anyway;
* it runs once, whichever comes first;
* registered once the overlay was painted already, or is gone (finish), it
  runs right away -- through the event loop;
* finish() takes the overlay away once, however often asked.

A real widget on the offscreen platform (tests/conftest.py), which paints a
shown widget as any platform does.
"""
import time

import pytest
from qtpy import shiboken
from qtpy.QtCore import QEventLoop, QTimer
from qtpy.QtWidgets import QWidget

from autona_splash import LoadingOverlay


def _waitUntil(predicate, timeout=10.0):
    # A nested loop, not processEvents(): the callbacks come through the
    # event loop, and nothing here runs inside a signal handler.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()
    return predicate()


class _Overlay(LoadingOverlay):
    """The overlay, telling whether its paint event is running."""

    def __init__(self, parent):
        super().__init__(parent)
        self.painting = False
        self.paints = 0

    def paintEvent(self, event):
        self.painting = True
        try:
            super().paintEvent(event)
        finally:
            self.painting = False
            self.paints += 1


@pytest.fixture
def window(qapp):
    windows = []

    def build(shown=True):
        widget = QWidget()
        widget.resize(320, 240)
        if shown:
            widget.show()
        windows.append(widget)
        return widget

    yield build

    for widget in windows:
        if shiboken.isValid(widget):
            widget.close()
            widget.deleteLater()


def _recorder(overlay):
    runs = []

    def callback():
        # What the window's _StartWebServer would find: the overlay painted
        # already, and its paint event not running.
        runs.append((overlay._painted_at is not None, overlay.painting))
    return runs, callback


def test_the_callback_waits_for_the_paint_and_runs_after_it(window):
    parent = window(shown=False)
    overlay = _Overlay(parent)
    overlay.start()
    runs, callback = _recorder(overlay)

    overlay.call_when_painted(callback, max_ms=10_000)

    # Not in the call, and not before the window is painted.
    assert runs == []
    assert not _waitUntil(lambda: runs, timeout=0.5)
    assert overlay.paints == 0

    parent.show()

    assert _waitUntil(lambda: runs)
    assert runs == [(True, False)]
    assert overlay.paints >= 1


def test_a_window_never_painted_runs_it_past_the_longest_wait(window):
    # Started minimized: no paint comes, the server is started anyway.
    parent = window(shown=False)
    overlay = _Overlay(parent)
    overlay.start()
    runs, callback = _recorder(overlay)
    started = time.monotonic()

    overlay.call_when_painted(callback, max_ms=200)

    assert _waitUntil(lambda: runs)
    assert time.monotonic() - started >= 0.15
    assert runs == [(False, False)]
    assert overlay.paints == 0


def test_it_runs_once_whichever_comes_first(window):
    # Painted first: the longest wait passing later runs nothing more.
    parent = window()
    overlay = _Overlay(parent)
    overlay.start()
    runs, callback = _recorder(overlay)

    overlay.call_when_painted(callback, max_ms=400)

    assert _waitUntil(lambda: runs)
    assert not _waitUntil(lambda: len(runs) > 1, timeout=0.8)
    assert runs == [(True, False)]

    # The longest wait first: the paint coming later runs nothing more.
    hidden = window(shown=False)
    late = _Overlay(hidden)
    late.start()
    lateRuns, lateCallback = _recorder(late)
    late.call_when_painted(lateCallback, max_ms=100)
    assert _waitUntil(lambda: lateRuns)
    hidden.show()
    assert _waitUntil(lambda: late.paints >= 1)
    assert not _waitUntil(lambda: len(lateRuns) > 1, timeout=0.3)
    assert lateRuns == [(False, False)]


def test_registered_once_painted_it_runs_through_the_event_loop(window):
    parent = window()
    overlay = _Overlay(parent)
    overlay.start()
    assert _waitUntil(lambda: overlay.paints >= 1)
    runs, callback = _recorder(overlay)

    overlay.call_when_painted(callback, max_ms=10_000)

    assert runs == []
    assert _waitUntil(lambda: runs)
    assert runs == [(True, False)]
    assert not _waitUntil(lambda: len(runs) > 1, timeout=0.3)


def test_registered_once_finished_it_runs_through_the_event_loop(window):
    # The spinner gone before (the web server's failure, the first page):
    # nothing to wait for.
    parent = window(shown=False)
    overlay = _Overlay(parent)
    overlay.start()
    overlay.finish()
    runs = []

    overlay.call_when_painted(lambda: runs.append(True), max_ms=10_000)

    assert runs == []
    assert _waitUntil(lambda: runs)
    assert not _waitUntil(lambda: len(runs) > 1, timeout=0.3)


def test_finish_takes_it_away_once(window):
    parent = window()
    overlay = LoadingOverlay(parent)
    overlay.start()
    assert overlay.isVisible()
    assert overlay._timer.isActive()

    overlay.finish()
    overlay.finish()

    assert not overlay._timer.isActive()
    assert overlay.isHidden()
    # Deleted from the event loop; the window's resizes no longer reach it.
    assert _waitUntil(lambda: not shiboken.isValid(overlay))
    parent.resize(400, 300)


def test_it_covers_the_window_as_it_is_resized(window):
    parent = window()
    overlay = LoadingOverlay(parent)
    overlay.start()
    assert overlay.size() == parent.size()

    parent.resize(500, 360)

    assert _waitUntil(lambda: overlay.size() == parent.size())
    overlay.finish()
