"""Off-screen lifecycle of the live layout preview (AutonaLayoutPreview).

Previews live for the whole process, so while one is not on screen -- its
panel is not the current stack page, or the main window is minimized -- its
page is frozen and, past ``DISCARD_AFTER_MS``, discarded. What has to hold
is the operator-facing promise: whenever the panel is shown again, the
preview is running and current.

These drive a real QWebEngineView (offscreen) against a throwaway local
HTTP server, because the behaviour under test is Qt's: page visibility on a
stack switch or a minimize, which transitions it accepts, and that it
returns a page to Active (reloading a discarded one) before reporting it
visible. The served page stands in for globals.js' Socket.IO client
(``window.tshSocket``), so the full-state request on reveal is observable
without the app's web server.

The real globals.js / Socket.IO round trip (deltas queued while frozen, a
connection dropped by the server's ping timeout during a long freeze) was
verified out of tree against a Flask-SocketIO server speaking the app's
program_state protocol; it needs the full layout include tree and a
websocket server, which this suite does not stand up.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from qtpy.QtWebEngineCore import QWebEnginePage
from qtpy.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from src import AutonaLayoutPreview as LP
from src.AutonaLayoutPreview import AutonaLayoutPreview

ACTIVE = QWebEnginePage.LifecycleState.Active
FROZEN = QWebEnginePage.LifecycleState.Frozen
DISCARDED = QWebEnginePage.LifecycleState.Discarded

# A per-document id tells a thaw (same document) from a reload (new one).
PAGE = b"""<!doctype html><html><body><script>
window.__loadId = Math.random();
window.__emits = [];
window.tshSocket = {connected: true,
                    emit: function (ev) { window.__emits.push(ev); }};
</script></body></html>"""


def _waitUntil(predicate, timeout=10.0):
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


@pytest.fixture(scope="module")
def layoutServer():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


class _Rig:
    """A preview on page 0 of a stack inside its own top-level window."""

    def __init__(self):
        self.host = QWidget()
        self.stack = QStackedWidget(self.host)
        QVBoxLayout(self.host).addWidget(self.stack)
        self.preview = AutonaLayoutPreview()
        self.stack.addWidget(self.preview)
        self.stack.addWidget(QLabel("another panel"))
        self.page = self.preview.view.page()
        self.loads = []
        self.preview.view.loadFinished.connect(self.loads.append)
        self.host.resize(640, 400)
        self.host.show()

    def load(self, layout="probe"):
        count = len(self.loads)
        self.preview.load(layout)
        assert _waitUntil(lambda: len(self.loads) > count), "load never finished"
        assert self.loads[-1] is True

    def hide(self):
        self.stack.setCurrentIndex(1)

    def reveal(self):
        self.stack.setCurrentIndex(0)

    def js(self, expr):
        # Only on an Active page: a frozen one answers after it thaws.
        # Scalars only -- a JS array comes back as an empty string.
        box = []
        self.page.runJavaScript(expr, 0, box.append)
        assert _waitUntil(lambda: box), f"no reply to {expr!r}"
        return box[0]

    def state(self):
        return self.page.lifecycleState()


@pytest.fixture
def buildPreview(qapp, layoutServer, monkeypatch):
    # Loads go straight to the throwaway server: the startup gate is open
    # and the port lookup is answered without touching the settings file.
    monkeypatch.setattr(LP, "_webServerReady", True)
    realGet = LP.SettingsManager.Get
    monkeypatch.setattr(
        LP.SettingsManager, "Get",
        lambda key, default=None: (layoutServer
                                   if key == "general.webserver_port"
                                   else realGet(key, default)))
    rigs = []

    def build(discardAfterMs=60_000):
        monkeypatch.setattr(AutonaLayoutPreview, "DISCARD_AFTER_MS",
                            discardAfterMs)
        rig = _Rig()
        rigs.append(rig)
        return rig

    yield build

    for rig in rigs:
        rig.host.close()
        rig.host.deleteLater()
        # At loop level 0 DeferredDelete is only delivered on request; aim
        # it at this window alone (see conftest's _dropBoard).
        QCoreApplication.sendPostedEvents(rig.host, QEvent.Type.DeferredDelete)


def test_a_visible_preview_stays_active(buildPreview):
    rig = buildPreview()
    rig.load()

    assert not _waitUntil(lambda: rig.state() != ACTIVE, timeout=0.5)


def test_a_hidden_preview_is_frozen_and_caught_up_when_shown(buildPreview):
    rig = buildPreview()
    rig.load()
    document = rig.js("window.__loadId")

    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.reveal()
    # Qt returns the page to Active before reporting it visible.
    assert rig.state() == ACTIVE
    # Thawed, not reloaded -- and it asked the server for the full state,
    # so whatever was broadcast while it was frozen cannot be missing.
    assert _waitUntil(lambda: rig.js("window.__emits.length") > 0)
    assert rig.js("window.__emits.join()") == "program_state"
    assert rig.js("window.__loadId") == document


def test_minimizing_the_window_freezes_the_preview(buildPreview):
    rig = buildPreview()
    rig.load()

    rig.host.showMinimized()
    # The view still claims to be visible; only the page knows better.
    # That is why the preview keys off the page's own visibility.
    assert rig.preview.view.isVisible()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.host.showNormal()
    assert rig.state() == ACTIVE
    assert _waitUntil(
        lambda: rig.js("window.__emits.join()") == "program_state")


def test_a_preview_hidden_past_the_delay_is_discarded_and_reloads_on_reveal(
        buildPreview):
    rig = buildPreview(discardAfterMs=200)
    rig.load()
    document = rig.js("window.__loadId")
    url = rig.page.url()

    rig.hide()
    assert _waitUntil(lambda: rig.state() == DISCARDED)

    loads = len(rig.loads)
    rig.reveal()
    assert rig.state() == ACTIVE
    assert _waitUntil(lambda: len(rig.loads) > loads), "no reload on reveal"
    assert rig.loads[-1] is True
    assert rig.page.url() == url
    assert rig.js("window.__loadId") != document


def test_a_preview_shown_again_before_the_delay_is_not_discarded(buildPreview):
    rig = buildPreview(discardAfterMs=800)
    rig.load()
    document = rig.js("window.__loadId")

    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.reveal()

    # Well past the delay, still the same document.
    assert not _waitUntil(lambda: rig.state() != ACTIVE, timeout=1.6)
    assert rig.js("window.__loadId") == document


def test_a_load_on_a_hidden_preview_navigates_then_freezes_again(buildPreview):
    # e.g. a design re-scan on the Board tab while another panel is shown.
    rig = buildPreview()
    rig.load("first")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.load("second")

    assert "/layout/second/" in rig.page.url().toString()
    assert _waitUntil(lambda: rig.state() == FROZEN)
