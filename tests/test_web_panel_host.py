"""The desktop's shared web view for the React panels (ADR-005 S4a).

AutonaWebPanelHost is one QStackedWidget page holding one QWebEngineView
for every panel moved to React. What has to hold:

* the view is not built until the host is on screen with a panel to show
  and the web server is ready (startup does not pay for it), and then from
  the event loop, not inside the show or the readiness call that allows it
  (S4g review M4: the Event page is the sidebar's default item, so at
  startup the host is shown by the main window's self.show(), ahead of
  STARTUP phase=core-shown, and the server is marked ready inside
  _StartWebServer(), ahead of phase=webserver-ready); no paint is waited
  for (S4g review L14: a window started minimized is not painted);
* it opens ``/web/<panel>?desktop=1&lng=<Qt language>`` once the web server
  is up, and showing another panel navigates the page's router instead of
  reloading the view;
* off screen it is frozen / discarded like the overlay previews (the same
  AutonaWebPageLifecycle parts), and on reveal it asks for the full state
  and shows the panel chosen meanwhile -- after a thaw and after the reload
  of a discarded page alike;
* a load that failed for good does not leave the view blank for the rest
  of the process: the next ShowPanel() or reveal loads the page again.

These drive a real QWebEngineView (offscreen) against a throwaway local
HTTP server, like tests/test_layout_preview_lifecycle.py. The served page
stands in for the Web UI's panel shell (``window.autonaPanelShell``,
web/src/PanelShell/hostApi.ts): it records the navigations and full-state
requests the host makes, and navigates the way the shell does
(``history.replaceState``, keeping the query). The real shell is covered by
the Web UI's own tests and by Playwright (web/e2e/panel-shell.spec.ts).
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from qtpy.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from src import AutonaWebPageLifecycle as WPL
from src.AutonaWebPanelHost import AutonaWebPanelHost, PanelPath, WebPanelPages
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from src.core import WebAccess

ACTIVE = QWebEnginePage.LifecycleState.Active
FROZEN = QWebEnginePage.LifecycleState.Frozen
DISCARDED = QWebEnginePage.LifecycleState.Discarded

# A per-document id tells a thaw / a router navigation (same document) from
# a reload (a new one).
PAGE = b"""<!doctype html><html><body><script>
window.__loadId = Math.random();
window.__navigations = [];
window.__refreshes = 0;
window.autonaPanelShell = {
  navigate: function (p) {
    var target = '/web/' + encodeURIComponent(p);
    if (location.pathname === target) { return; }
    window.__navigations.push(p);
    history.replaceState(null, '', target + location.search);
  },
  refresh: function () { window.__refreshes += 1; }
};
</script></body></html>"""


# How long a page gets for what a test waits for. It only decides how long a
# failing test takes: the wait ends as soon as the page has done it, and on a
# machine busy with other work a page's load takes many times what it does
# otherwise (see tests/test_layout_preview_lifecycle.py).
WAIT_SECS = 60.0


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


@pytest.fixture(scope="module")
def panelServer():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith("/favicon"):
                requests.append(self.path)
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
    yield server.server_address[1], requests
    server.shutdown()
    server.server_close()


class _Rig:
    """The host on page 0 of a stack inside its own top-level window, the
    stack showing another page (the host is not on screen yet)."""

    def __init__(self):
        self.window = QWidget()
        self.stack = QStackedWidget(self.window)
        QVBoxLayout(self.window).addWidget(self.stack)
        self.host = AutonaWebPanelHost()
        self.stack.addWidget(self.host)
        self.stack.addWidget(QLabel("another panel"))
        self.stack.setCurrentIndex(1)
        self.loads = []
        # Every load of the view, from its first: watched as the view is
        # built (from the event loop, see reveal()), before any can finish.
        build = self.host._EnsureView

        def watched():
            build()
            self.host.view.loadFinished.connect(self.loads.append)
        self.host._EnsureView = watched
        self.window.resize(640, 400)
        self.window.show()

    @property
    def page(self):
        return self.host.view.page()

    def reveal(self):
        self.stack.setCurrentIndex(0)
        # The view is built on the first reveal with a panel (the server is
        # ready here), from the event loop.
        if self.host.view is None and self.host._panel is not None:
            assert _waitUntil(lambda: self.host.view is not None),                 "the view was not built"

    def hide(self):
        self.stack.setCurrentIndex(1)

    def waitLoaded(self, count=1):
        assert _waitUntil(lambda: len(self.loads) >= count), "no load finished"
        assert self.loads[-1] is True

    def path(self):
        url = self.host.view.url()
        return url.path(), url.query()

    def js(self, expr):
        # Only on an Active page: a frozen one answers after it thaws. The
        # value is round-tripped through JSON (see the preview tests: PySide6
        # hands null, arrays and objects back as '').
        box = []
        self.page.runJavaScript(
            "(function(){try{var v=(" + expr + ");"
            "return JSON.stringify({value:v===undefined?null:v});}"
            "catch(e){return JSON.stringify({error:String(e)});}})()",
            0, box.append)
        assert _waitUntil(lambda: box), f"no reply to {expr!r}"
        reply = json.loads(box[0])
        assert "error" not in reply, f"{expr!r} threw {reply['error']}"
        return reply["value"]

    def state(self):
        return self.page.lifecycleState()


@pytest.fixture
def buildHost(qapp, panelServer, monkeypatch):
    port, requests = panelServer
    requests.clear()
    # The gate is open, the port lookup is answered without the settings
    # file (rig.usePort() points it elsewhere), the Qt language is
    # Japanese, and there is a second panel to switch to.
    monkeypatch.setattr(WPL, "_webServerReady", True)
    monkeypatch.setattr(WPL, "_pendingLoaders", [])
    monkeypatch.setattr(WPL, "_readyCallbacks", [])
    target = {"port": port}
    realGet = WPL.SettingsManager.Get
    monkeypatch.setattr(
        WPL.SettingsManager, "Get",
        lambda key, default=None: (target["port"]
                                   if key == "general.webserver_port"
                                   else realGet(key, default)))
    monkeypatch.setattr(AutonaLocaleHelper, "programLocale", "ja")
    monkeypatch.setattr(WebAccess, "WEB_PANELS",
                        frozenset({"shell-check", "second"}))
    rigs = []

    def build(discardAfterMs=60_000):
        monkeypatch.setattr(AutonaWebPanelHost, "DISCARD_AFTER_MS",
                            discardAfterMs)
        rig = _Rig()
        rig.requests = requests
        rig.servingPort = port
        rig.usePort = lambda p: target.__setitem__("port", p)
        rigs.append(rig)
        return rig

    yield build

    for rig in rigs:
        rig.window.close()
        rig.window.deleteLater()
        # At loop level 0 DeferredDelete is only delivered on request; aim
        # it at this window alone (see conftest's _dropBoard).
        QCoreApplication.sendPostedEvents(rig.window, QEvent.Type.DeferredDelete)


@pytest.fixture
def refusedPort():
    # A port bound but not listening: a connection to it is refused, and
    # no other socket (another test process) can take the port meanwhile,
    # as it could a port merely freed.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    yield sock.getsockname()[1]
    sock.close()


def _failedFirstLoad(buildHost, monkeypatch, refusedPort):
    # The host is shown while nothing listens on the port: the loader's
    # retries are used up and the view is left on an error page.
    monkeypatch.setattr(WPL.LocalPageLoader, "LOAD_RETRY_MS", 50)
    rig = buildHost()
    rig.usePort(refusedPort)
    rig.host.ShowPanel("shell-check")
    rig.reveal()
    attempts = 1 + WPL.LocalPageLoader.MAX_LOAD_RETRIES
    assert _waitUntil(lambda: len(rig.loads) >= attempts)
    assert not _waitUntil(lambda: len(rig.loads) > attempts, timeout=0.5),         "the host retried on its own past the loader's retries"
    assert rig.loads == [False] * attempts
    assert not rig.host._loader.IsLoading()
    assert rig.requests == []
    # The server is back.
    rig.usePort(rig.servingPort)
    return rig


def _shown(buildHost, **kwargs):
    rig = buildHost(**kwargs)
    rig.host.ShowPanel("shell-check")
    rig.reveal()
    rig.waitLoaded()
    return rig


def test_the_panel_url_carries_desktop_and_the_qt_language(monkeypatch):
    monkeypatch.setattr(AutonaLocaleHelper, "programLocale", "en-US")
    assert PanelPath("shell-check") == "/web/shell-check?desktop=1&lng=en-US"


def test_the_view_is_built_only_once_the_host_is_shown_with_a_panel(buildHost):
    rig = buildHost()
    # Constructed, a panel chosen, but not on screen: nothing built.
    rig.host.ShowPanel("shell-check")
    assert rig.host.view is None
    assert not _waitUntil(lambda: rig.requests, timeout=0.3)

    rig.stack.setCurrentIndex(0)

    # Not inside the show: from the event loop.
    assert rig.host.view is None
    assert _waitUntil(lambda: rig.host.view is not None)
    rig.waitLoaded()
    assert rig.requests == ["/web/shell-check?desktop=1&lng=ja"]
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")


def test_the_page_may_write_the_clipboard(buildHost):
    # The Stream page copies the made thumbnail with
    # navigator.clipboard.write (S4e). Off by default in Qt, and the write
    # then never settles (measured on PySide6 6.11 with a click on
    # 127.0.0.1: no answer either way; with the attribute, the image is on
    # the clipboard).
    rig = _shown(buildHost)
    assert rig.host.view.settings().testAttribute(
        QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard)


def test_the_page_does_not_leave_this_server(buildHost, refusedPort):
    # What the clipboard permission above rests on (S4e review): a
    # navigation the page starts to another origin -- script, a link -- is
    # refused, and the page stays as it was. Another port is another
    # origin (here nothing listens there: let through, the view would show
    # an error page).
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    elsewhere = f"http://127.0.0.1:{refusedPort}/elsewhere"

    rig.js(f"void (location.href = {json.dumps(elsewhere)})")
    rig.js("(function(u){var a=document.createElement('a');a.href=u;"
           f"document.body.appendChild(a);a.click();}})({json.dumps(elsewhere)})")

    _waitUntil(lambda: False, timeout=1.0)
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")
    assert rig.js("window.__loadId") == document
    # Qt reports a refused navigation as a failed load; the host knows the
    # page is still loaded, and does not reload it on the next reveal.
    assert rig.host._loaded
    rig.host.ShowPanel("shell-check")
    assert not _waitUntil(lambda: len(rig.requests) > 1, timeout=0.5)
    assert rig.js("window.__loadId") == document

    # Its own server: let through (a new document; the host then moves
    # its router back to the panel it shows).
    loads = len(rig.loads)
    rig.js("void (location.href = '/web/second?desktop=1&lng=ja')")
    assert _waitUntil(lambda: len(rig.loads) > loads)
    assert rig.loads[-1] is True
    assert rig.requests[-1] == "/web/second?desktop=1&lng=ja"
    assert rig.js("window.__loadId") != document
    assert not rig.host.view.page().refusedLast


def test_a_host_shown_without_a_panel_builds_nothing(buildHost):
    rig = buildHost()
    rig.reveal()

    assert not _waitUntil(lambda: rig.host.view is not None, timeout=0.3)
    rig.host.ShowPanel("shell-check")
    # On screen already: built from the event loop.
    assert rig.host.view is None
    assert _waitUntil(lambda: rig.host.view is not None)
    rig.waitLoaded()


def test_the_view_waits_for_the_web_server(buildHost, monkeypatch):
    """What STARTUP phase=core-shown and phase=webserver-ready are logged
    after (S4g review M4). At startup the window is shown with the host as
    its current page -- the Event page, the sidebar's default item -- before
    the web server is started: no view is built then, nor queued
    (core-shown follows self.show()). _StartWebServer() marks the server
    ready and logs webserver-ready once that returns: no view is built
    inside it either. It is built from the event loop afterwards, so
    Chromium's start-up holds up neither the window's first paint and the
    loading spinner nor the server's start (the page could not be shown
    before the server anyway)."""
    monkeypatch.setattr(WPL, "_webServerReady", False)
    rig = buildHost()
    rig.window.hide()
    rig.stack.setCurrentIndex(0)
    rig.host.ShowPanel("shell-check")

    rig.window.show()

    # core-shown: nothing built or queued -- however long it waits.
    assert rig.host.view is None and not rig.host._viewPending
    assert not _waitUntil(lambda: rig.host.view is not None, timeout=0.3)
    assert rig.requests == []

    WPL.MarkWebServerReady()

    # webserver-ready: still nothing built, the build queued.
    assert rig.host.view is None and rig.host._viewPending
    assert _waitUntil(lambda: rig.host.view is not None)
    rig.waitLoaded()
    assert rig.requests == ["/web/shell-check?desktop=1&lng=ja"]


def test_the_view_is_built_without_a_paint(buildHost, monkeypatch):
    """S4g review L14: the view waited for the host's first paint, and a
    window started minimized is never painted -- no view until the
    operator restored it. Nothing waits for a paint now (none is let
    through here)."""
    from qtpy.QtCore import QObject

    monkeypatch.setattr(WPL, "_webServerReady", False)
    rig = buildHost()
    rig.window.hide()

    class NoPaint(QObject):
        def eventFilter(self, obj, event):
            return event.type() == QEvent.Type.Paint
    rig.host.installEventFilter(NoPaint(rig.host))
    rig.stack.setCurrentIndex(0)
    rig.host.ShowPanel("shell-check")
    rig.window.showMinimized()

    WPL.MarkWebServerReady()

    assert _waitUntil(lambda: rig.host.view is not None)
    rig.waitLoaded()


def test_a_host_off_screen_when_the_server_is_ready_builds_on_its_show(
        buildHost, monkeypatch):
    monkeypatch.setattr(WPL, "_webServerReady", False)
    rig = buildHost()
    rig.reveal()
    rig.host.ShowPanel("shell-check")
    rig.hide()

    WPL.MarkWebServerReady()

    assert not _waitUntil(lambda: rig.host.view is not None, timeout=0.3)
    assert not rig.host._viewPending
    rig.reveal()
    rig.waitLoaded()


def test_a_host_hidden_before_the_queued_build_builds_on_its_next_show(
        buildHost):
    rig = buildHost()
    rig.host.ShowPanel("shell-check")
    # Shown: the build is queued; hidden before the event loop runs it.
    rig.stack.setCurrentIndex(0)
    assert rig.host.view is None and rig.host._viewPending
    rig.hide()

    assert not _waitUntil(lambda: rig.host.view is not None, timeout=0.3)
    assert not rig.host._viewPending
    assert not rig.host.isVisible()

    rig.reveal()

    rig.waitLoaded()
    assert rig.requests == ["/web/shell-check?desktop=1&lng=ja"]


def test_an_unknown_panel_is_refused(buildHost):
    rig = buildHost()
    rig.reveal()
    with pytest.raises(ValueError):
        rig.host.ShowPanel("not-a-panel")
    assert rig.host.view is None


def test_another_panel_is_navigated_to_without_a_reload(buildHost):
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")

    rig.host.ShowPanel("second")

    assert _waitUntil(lambda: rig.js("window.__navigations.join()") == "second")
    assert rig.path() == ("/web/second", "desktop=1&lng=ja")
    assert rig.js("window.__loadId") == document
    assert rig.loads == [True]
    assert rig.requests == ["/web/shell-check?desktop=1&lng=ja"]

    # Back, and the same panel twice: the page skips the second.
    rig.host.ShowPanel("shell-check")
    rig.host.ShowPanel("shell-check")
    assert _waitUntil(lambda: rig.path()[0] == "/web/shell-check")
    assert rig.js("window.__navigations.join()") == "second,shell-check"
    assert rig.js("window.__loadId") == document


def test_the_host_waits_for_the_web_server(buildHost, monkeypatch):
    monkeypatch.setattr(WPL, "_webServerReady", False)
    rig = buildHost()
    rig.host.ShowPanel("shell-check")
    rig.stack.setCurrentIndex(0)

    # Not even built behind the gate (S4g review M4; it used to be built
    # with its load parked): no request reaches the port.
    assert not _waitUntil(
        lambda: rig.host.view is not None or rig.requests, timeout=0.3)

    WPL.MarkWebServerReady()

    assert _waitUntil(lambda: rig.host.view is not None)
    rig.waitLoaded()
    assert rig.requests == ["/web/shell-check?desktop=1&lng=ja"]


def test_a_hidden_host_is_frozen_and_asks_for_the_full_state_on_reveal(
        buildHost):
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")

    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.reveal()
    assert rig.state() == ACTIVE
    assert _waitUntil(lambda: rig.js("window.__refreshes") > 0)
    # Thawed, not reloaded.
    assert rig.js("window.__loadId") == document
    assert rig.loads == [True]


def test_a_panel_chosen_while_frozen_is_shown_on_reveal(buildHost):
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.host.ShowPanel("second")
    rig.reveal()

    assert _waitUntil(lambda: rig.js("window.__navigations.join()") == "second")
    assert rig.path()[0] == "/web/second"
    assert rig.js("window.__loadId") == document


def test_a_discarded_host_reloads_on_reveal_with_the_panel_chosen_meanwhile(
        buildHost):
    rig = _shown(buildHost, discardAfterMs=200)
    document = rig.js("window.__loadId")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == DISCARDED)

    rig.host.ShowPanel("second")
    rig.reveal()

    assert rig.state() == ACTIVE
    rig.waitLoaded(count=2)
    # A new document (Qt reloaded the page it had), then the router is
    # told about the panel chosen while it was discarded.
    assert _waitUntil(lambda: rig.path()[0] == "/web/second")
    assert rig.js("window.__loadId") != document
    assert rig.js("window.__navigations.join()") == "second"
    assert rig.path()[1] == "desktop=1&lng=ja"


def test_a_failed_load_is_loaded_again_on_the_next_panel_shown(
        buildHost, monkeypatch, refusedPort):
    rig = _failedFirstLoad(buildHost, monkeypatch, refusedPort)
    failures = len(rig.loads)

    rig.host.ShowPanel("second")

    # Loaded again, straight on the panel asked for: there was no page
    # whose router could be told.
    rig.waitLoaded(count=failures + 1)
    assert rig.requests == ["/web/second?desktop=1&lng=ja"]
    assert rig.path() == ("/web/second", "desktop=1&lng=ja")
    assert rig.js("window.__navigations.join()") == ""

    # From then on it navigates as usual, without a reload.
    rig.host.ShowPanel("shell-check")
    assert _waitUntil(lambda: rig.path()[0] == "/web/shell-check")
    assert rig.requests == ["/web/second?desktop=1&lng=ja"]
    assert rig.loads == [False] * failures + [True]


def test_a_failed_load_is_loaded_again_when_the_host_comes_back(
        buildHost, monkeypatch, refusedPort):
    rig = _failedFirstLoad(buildHost, monkeypatch, refusedPort)
    failures = len(rig.loads)
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.reveal()

    rig.waitLoaded(count=failures + 1)
    assert rig.requests == ["/web/shell-check?desktop=1&lng=ja"]
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")


def test_a_refused_load_is_retried_then_given_up(qapp, monkeypatch,
                                                 refusedPort):
    # Nothing listens on the port: the loader tries MAX_LOAD_RETRIES more
    # times, then reports the failure once.
    monkeypatch.setattr(WPL, "_webServerReady", True)
    monkeypatch.setattr(WPL.LocalPageLoader, "LOAD_RETRY_MS", 50)
    realGet = WPL.SettingsManager.Get
    monkeypatch.setattr(
        WPL.SettingsManager, "Get",
        lambda key, default=None: (refusedPort
                                   if key == "general.webserver_port"
                                   else realGet(key, default)))
    view = QWebEngineView()
    try:
        # Shown, or this measures how Chromium treats a hidden page instead of
        # the retries: with every core busy (a full run beside a build) these
        # three loads took about 5 minutes hidden, and about 5 s shown.
        view.show()
        loader = WPL.LocalPageLoader(view, view)
        attempts, finished = [], []
        view.loadFinished.connect(attempts.append)
        loader.finished.connect(finished.append)

        loader.load("/web/shell-check")

        assert _waitUntil(lambda: finished)
        assert finished == [False]
        assert attempts == [False] * (1 + WPL.LocalPageLoader.MAX_LOAD_RETRIES)
        assert not loader.IsLoading()
    finally:
        view.deleteLater()
        QCoreApplication.sendPostedEvents(view, QEvent.Type.DeferredDelete)


# ── S4b: the editing menu, the Qt fallback, the Window's wiring ──────────

def test_the_right_click_menu_offers_editing_only():
    from src.AutonaWebPanelHost import ContextActions
    W = QWebEnginePage.WebAction

    assert ContextActions(editable=True, hasSelection=False) == [
        W.Undo, W.Redo, None, W.Cut, W.Copy, W.Paste, None, W.SelectAll]
    assert ContextActions(editable=True, hasSelection=True) == \
        ContextActions(editable=True, hasSelection=False)
    assert ContextActions(editable=False, hasSelection=True) == [W.Copy]
    assert ContextActions(editable=False, hasSelection=False) == []
    # None of the browser's: back / forward / reload / view source / save.
    offered = set(ContextActions(True, True) + ContextActions(False, True))
    assert not offered & {W.Back, W.Forward, W.Reload, W.ViewSource,
                          W.SavePage, W.InspectElement}


class _FakeRequest:
    def __init__(self, editable, selected):
        self._editable, self._selected = editable, selected

    def isContentEditable(self):
        return self._editable

    def selectedText(self):
        return self._selected


def _rightClick(view, monkeypatch, request):
    from qtpy.QtCore import QPoint
    from qtpy.QtGui import QContextMenuEvent
    from qtpy.QtWidgets import QMenu

    shown = []
    monkeypatch.setattr(view, "lastContextMenuRequest", lambda: request)
    monkeypatch.setattr(
        QMenu, "popup",
        lambda menu, *args: shown.append(list(menu.actions())))
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5),
                              view.mapToGlobal(QPoint(5, 5)))
    view.contextMenuEvent(event)
    assert event.isAccepted()
    return shown


def test_the_view_shows_the_editing_menu_for_the_clicked_context(
        buildHost, monkeypatch):
    rig = _shown(buildHost)
    view = rig.host.view
    W = QWebEnginePage.WebAction

    shown = _rightClick(view, monkeypatch, _FakeRequest(True, ""))

    assert len(shown) == 1
    entries = [None if action.isSeparator() else action
               for action in shown[0]]
    expected = [W.Undo, W.Redo, None, W.Cut, W.Copy, W.Paste, None,
                W.SelectAll]
    assert len(entries) == len(expected)
    for action, kind in zip(entries, expected):
        if kind is None:
            assert action is None
        else:
            # The page's own action: Chromium enables it for the context.
            assert action is view.pageAction(kind)

    shown = _rightClick(view, monkeypatch, _FakeRequest(False, "picked"))
    assert [[action is view.pageAction(W.Copy) for action in menu]
            for menu in shown] == [[True]]


@pytest.mark.parametrize("request_", [None, _FakeRequest(False, "")])
def test_no_menu_where_there_is_nothing_to_edit_or_copy(
        buildHost, monkeypatch, request_):
    rig = _shown(buildHost)

    assert _rightClick(rig.host.view, monkeypatch, request_) == []


# The Window (src/Autona.py) is read, not imported: that import is the
# app's startup (agent-pitfalls #1).
def _autonaSource():
    from tests.conftest import PROJECT_ROOT
    return (PROJECT_ROOT / "src" / "Autona.py").read_text(encoding="utf-8")


def _windowMethod(name):
    import ast

    source = _autonaSource()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"src/Autona.py: no method {name}")


def _calls(node):
    import ast
    return [(ast.unparse(call.func),
             [ast.unparse(arg) for arg in call.args])
            for call in ast.walk(node) if isinstance(call, ast.Call)]


def test_the_commentary_item_opens_the_react_page():
    import ast
    factory = _windowMethod("_CreateCommentaryPanel")
    calls = _calls(factory)

    # The React page is the only Commentary panel (the Qt one and its
    # settings switch were removed in ADR-005 S4c).
    assert ("self._WebPanel", ["'commentary'"]) in calls
    assert not [c for c, _ in calls if c == "UsesQtPanel"
                or c.startswith("AutonaCommentary")]

    # The sidebar item is still the lazy factory, keyed by the React
    # panel's name (_WebPanel finds the page by it).
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateCommentaryPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[-1]) == "'commentary'"
    assert "commentary" in WebAccess.WEB_PANELS


def test_the_event_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateEventPanel"))

    # The React page is the only 大会情報 panel (ADR-005 S4g).
    assert calls == [("self._WebPanel", ["'event'"])]

    # Still the lazy factory leading Setup (the sidebar's default item),
    # keyed by the React panel's name.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateEventPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'tournament.svg'"
    assert ast.unparse(entries[0].elts[-1]) == "'event'"
    assert "event" in WebAccess.WEB_PANELS


def test_the_top8_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateTop8Panel"))

    # The React page is the only Top 8 panel (ADR-005 S4i).
    assert calls == [("self._WebPanel", ["'top8'"])]

    # Still the lazy factory in Display, keyed by the React panel's name,
    # with its icon and label.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateTop8Panel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'bracket.svg'"
    assert "Top 8 Display Settings" in ast.unparse(entries[0].elts[2])
    assert ast.unparse(entries[0].elts[-1]) == "'top8'"
    assert "top8" in WebAccess.WEB_PANELS


def test_the_stats_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateStatsPanel"))

    # The React page is the only Statistics panel, the match list its
    # fourth tab (ADR-005 S4k).
    assert calls == [("self._WebPanel", ["'stats'"])]

    # Still the lazy factory in Data, keyed by the React panel's name,
    # with its icon and label.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateStatsPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'stats.svg'"
    assert "Statistics" in ast.unparse(entries[0].elts[2])
    assert ast.unparse(entries[0].elts[-1]) == "'stats'"
    assert "stats" in WebAccess.WEB_PANELS


def test_the_stats_preview_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateStatsPreviewPanel"))

    # The React page is the only Stats Display Settings panel (ADR-005 S4l).
    assert calls == [("self._WebPanel", ["'statsPreview'"])]

    # Still the lazy factory in Display, keyed by the React panel's name,
    # with its icon and label.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateStatsPreviewPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'stats.svg'"
    assert "Stats Display Settings" in ast.unparse(entries[0].elts[2])
    assert ast.unparse(entries[0].elts[-1]) == "'statsPreview'"
    assert "statsPreview" in WebAccess.WEB_PANELS


def test_the_player_list_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreatePlayerBrowserPanel"))

    # The React page is the only Player List (ADR-005 S4n).
    assert calls == [("self._WebPanel", ["'playerBrowser'"])]

    # Still the lazy factory in Data, keyed by the React panel's name, with
    # its icon and label.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreatePlayerBrowserPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'players.svg'"
    assert "Player List" in ast.unparse(entries[0].elts[2])
    assert ast.unparse(entries[0].elts[-1]) == "'playerBrowser'"
    assert "playerBrowser" in WebAccess.WEB_PANELS


def test_the_restored_page_is_built_before_the_window_is_shown():
    """STARTUP phase=core-shown is logged right after self.show(), with the
    restored page built before it (_EnsurePanelAt). For the Event page --
    the default item -- that page is the shared view's host, whose view is
    built from the event loop once the web server is ready, after
    phase=webserver-ready (test_the_view_waits_for_the_web_server): the
    server's readiness comes before that phase is logged."""
    tokens = "".join(_autonaSource().split())
    ensure = tokens.index("self._EnsurePanelAt(self.stack.currentIndex())")
    show = tokens.index("self.show()", ensure)
    shown = tokens.index('log_startup_phase("core-shown")', show)
    assert ensure < show < shown
    # And the overlay's dismissal (the queue drained) still gates the web
    # server, whose readiness releases the view's first load.
    assert tokens.index("self._loading_overlay.finish_when_ready()") < \
        tokens.index("self._loading_overlay.call_when_finished("
                     "self._StartWebServer)")
    start = _windowMethod("_StartWebServer")
    names = [call for call, _args in _calls(start)]
    assert names.index("MarkWebServerReady") < names.index(
        "log_startup_phase")


def test_the_stream_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateStreamPanel"))

    # The React page is the only 配信設定 panel (ADR-005 S4e).
    assert calls == [("self._WebPanel", ["'stream'"])]

    # Still the lazy factory in Setup, right after the Event page, keyed by
    # the React panel's name.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateStreamPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[-1]) == "'stream'"
    assert "stream" in WebAccess.WEB_PANELS


def test_the_sidebar_items_are_the_ones_they_were():
    """The nav items' number and order (and so the saved sidebar index,
    QSettings sidebarIndex_v<STATE_VERSION>) did not change for the React
    panels (Stream S4e, Event S4g -- its key is the React page's name now
    --, Top 8 S4i, Statistics S4k and the Player List S4n): no STATE_VERSION
    bump (ADR-005 S4 方針 5, S4 Event 論点 1)."""
    import ast
    build = _windowMethod("_BuildUi")
    keys = [ast.unparse(node.elts[-1]) for node in ast.walk(build)
            if isinstance(node, ast.Tuple) and len(node.elts) == 4
            and ast.unparse(node.elts[1]).endswith(".svg'")]
    assert keys == [
        "'event'", "'stream'", "None", "'board'", "'thumbnail'",
        "'result'", "'top8'", "'commentary'", "'statsPreview'", "'stats'",
        "'playerBrowser'"]
    version = next(node for node in ast.walk(ast.parse(
        _autonaSource())) if isinstance(node, ast.Assign)
        and [ast.unparse(t) for t in node.targets] == ["STATE_VERSION"])
    assert ast.unparse(version.value) == "22"


def test_every_reveal_of_a_react_item_tells_the_host():
    calls = _calls(_windowMethod("_OnStackPageChanged"))
    assert ("self._EnsurePanelAt", ["index"]) in calls
    assert ("self._webPanelPages.Shown", ["index"]) in calls
    assert ("page.ShowPanel", ["panel"]) in calls
    # The host is told its panel before it is first shown.
    assert ("host.ShowPanel", ["panel"]) in _calls(_windowMethod("_WebPanel"))


def test_a_sidebar_choice_and_the_restore_go_through_the_nav_mapping():
    import ast
    build = _windowMethod("_BuildUi")
    connects = [(ast.unparse(call.func), [ast.unparse(a) for a in call.args])
                for call in ast.walk(build) if isinstance(call, ast.Call)
                and ast.unparse(call.func).endswith(".connect")]
    assert ("self.sidebar.navSelected.connect", ["self._OnNavSelected"]) \
        in connects
    assert not [c for c in connects
                if c == ("self.sidebar.navSelected.connect",
                         ["self.stack.setCurrentIndex"])]
    # The React items are registered with the mapping as they are added.
    assert ("self._webPanelPages.Add", ["index", "key"]) in _calls(build)
    # The restore of the saved sidebar index takes the same way as a click
    # (a saved React item lands on the shared view's page).
    assert ("self._OnNavSelected", ["savedIndex"]) in _calls(build)
    assert ("self.stack.setCurrentIndex", ["savedIndex"]) not in _calls(build)
    assert ("self._webPanelPages.StackIndex", ["nav"]) in _calls(
        _windowMethod("_OnNavSelected"))


def test_the_web_page_gets_no_dock_chrome():
    import ast
    prepare = _windowMethod("_PreparePanel")
    guard = next(node for node in prepare.body if isinstance(node, ast.If))
    assert ast.unparse(guard.test) == "not isinstance(dw, QDockWidget)"
    assert isinstance(guard.body[-1], ast.Return)
    # Everything dock-only comes after the guard.
    after = prepare.body[prepare.body.index(guard) + 1:]
    assert any("setTitleBarWidget" in ast.unparse(node) for node in after)
    assert any("QDockWidget.widget" in ast.unparse(node) for node in after)


def test_the_web_page_is_not_kept_as_a_dock():
    """A lazily built page goes to self.dockWidgets only when it is a dock
    (S4b review): the React panels' host is a plain page."""
    import ast
    ensure = _windowMethod("_EnsurePanelAt")
    appends = [node for node in ast.walk(ensure)
               if isinstance(node, ast.If)
               and any("self.dockWidgets.append" in ast.unparse(child)
                       for child in node.body)]
    assert [ast.unparse(node.test) for node in appends] == [
        "isinstance(panel, QDockWidget)"]
    every = [call for call, _args in _calls(ensure)
             if call == "self.dockWidgets.append"]
    assert len(every) == 1


def test_the_qt_commentary_panel_and_its_switch_are_gone():
    # ADR-005 S4c: the React page is the only Commentary panel. Neither the
    # widget nor the `general.qt_commentary_panel` way back (a stale key in
    # a user's settings.json is simply never read) may come back in src/.
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not (src / "AutonaCommentaryWidget.py").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for path in src.rglob("*.py")
            if any(word in path.read_text(encoding="utf-8")
                   for word in ("AutonaCommentaryWidget",
                                "qt_commentary_panel", "UsesQtPanel",
                                "QT_FALLBACK_SETTINGS"))]
    assert left == []


def test_the_qt_event_panel_is_gone():
    # ADR-005 S4g: the React page is the only 大会情報 panel. Neither the
    # widget, its .ui, nor the tournament color card it embedded may come
    # back in src/ (the .ts files included: their strings went with it).
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not (src / "AutonaTournamentInfoWidget.py").exists()
    assert not (src / "layout" / "AutonaTournamentInfo.ui").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui", "*.ts")
            for path in src.rglob(pattern)
            if any(word in path.read_text(encoding="utf-8")
                   for word in ("AutonaTournamentInfoWidget",
                                "AutonaTournamentInfo.ui",
                                "MakeTournamentColorCard"))]
    assert left == []


def test_the_qt_stream_panel_is_gone():
    # ADR-005 S4e: the React page is the only 配信設定 panel, with no way
    # back (論点 4). Neither the widget nor its .ui may come back in src/.
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not (src / "AutonaStreamSettingsWidget.py").exists()
    assert not (src / "layout" / "AutonaStreamSettings.ui").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui", "*.ts")
            for path in src.rglob(pattern)
            if any(word in path.read_text(encoding="utf-8")
                   for word in ("AutonaStreamSettingsWidget",
                                "AutonaStreamSettings.ui"))]
    assert left == []


def test_the_qt_top8_panel_is_gone():
    # ADR-005 S4i: the React page is the only Top 8 panel. The widget may
    # not come back in src/: no import, no construction (comments recording
    # where code came from still name it).
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not (src / "AutonaTop8Widget.py").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui", "*.ts")
            for path in src.rglob(pattern)
            if any(word in path.read_text(encoding="utf-8")
                   for word in ("import AutonaTop8Widget",
                                "AutonaTop8Widget(",
                                "AutonaTop8Widget.instance"))]
    assert left == []


def test_the_qt_stats_panel_is_gone():
    # ADR-005 S4k: the React page is the only Statistics panel, the match
    # list included. Neither widget may come back in src/: no import, no
    # construction (comments recording where code came from still name
    # them), and no translation of theirs.
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    for name in ("AutonaStatsWidget", "AutonaAnalysisWidget"):
        assert not (src / f"{name}.py").exists()
        left = [str(path.relative_to(PROJECT_ROOT))
                for pattern in ("*.py", "*.ui", "*.ts")
                for path in src.rglob(pattern)
                if any(word in path.read_text(encoding="utf-8")
                       for word in (f"import {name}", f"{name}(",
                                    f"{name}.instance", f"{name}.py"))]
        assert left == [], name


def test_the_qt_player_list_is_gone():
    # ADR-005 S4n: the React page is the only Player List. The widget may not
    # come back in src/: no import, no construction (comments recording where
    # code came from still name it), and none of the translations only it and
    # the card's editor mode used. The player card has no editor mode left.
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    name = "AutonaPlayerBrowserWidget"
    assert not (src / f"{name}.py").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui", "*.ts")
            for path in src.rglob(pattern)
            if any(word in path.read_text(encoding="utf-8")
                   for word in (f"import {name}", f"{name}(",
                                f"{name}.instance", f"{name}.py"))]
    assert left == []

    for path in (src / "i18n").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        back = [source for source in (
                    "Tournament Entrants", "Registered Players", "Set as P1",
                    "Set as P2", "Search players", "Add new player",
                    "Edit player", "Delete selected player",
                    "Save new player", "Update player", "Delete player entry")
                if f"<source>{source}</source>" in text]
        assert back == [], path.name

    card = (src / "AutonaScoreboardPlayerWidget.py").read_text(encoding="utf-8")
    for word in ("save_bt", "delete_bt", "SetCharactersPerPlayer",
                 "ManageSavePlayerToDBText", "ManageDeletePlayerFromDBActive",
                 "DeletePlayerFromDB", 'path.startswith("score.")'):
        assert word not in card, word


def test_the_qt_stats_preview_panel_is_gone():
    # ADR-005 S4l: the React page is the only Stats Display Settings panel.
    # The widget may not come back in src/: no import, no construction
    # (comments recording where code came from still name it), and none of
    # the translations only it used (the overlays' names are the page's).
    from src.core import StatsOverlays
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    name = "AutonaStatsPreviewWidget"
    assert not (src / f"{name}.py").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui", "*.ts")
            for path in src.rglob(pattern)
            if any(word in path.read_text(encoding="utf-8")
                   for word in (f"import {name}", f"{name}(",
                                f"{name}.instance", f"{name}.py"))]
    assert left == []

    for path in (src / "i18n").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        back = [item["displayName"] for item in StatsOverlays.Overlays()
                if f"<source>{item['displayName']}</source>" in text]
        assert back == [], path.name


# ── several React items, one host page (ADR-005 S4e) ────────────────────

def test_a_react_item_shows_its_own_page_until_the_host_is_built():
    pages = WebPanelPages()
    pages.Add(1, "stream")
    pages.Add(7, "commentary")

    # A Qt item: its own page, and the choice of panel stays.
    assert pages.StackIndex(3) == 3
    assert pages.selected is None
    # No host yet: the React item's own page (where the host is built).
    assert pages.StackIndex(7) == 7
    assert pages.selected == "commentary"
    assert pages.Shown(7) is None

    assert pages.HostBuilt(7) == [1]
    assert pages.Shown(7) == "commentary"
    # Every React item shows the host's page from then on.
    assert pages.StackIndex(1) == 7
    assert pages.selected == "stream"
    assert pages.Shown(7) == "stream"
    assert pages.StackIndex(3) == 3
    assert pages.selected == "stream"
    assert pages.Shown(3) is None
    assert pages.StackIndex(7) == 7
    assert pages.Shown(7) == "commentary"


def test_the_host_page_shows_its_own_panel_before_any_choice():
    pages = WebPanelPages()
    pages.Add(1, "stream")
    pages.HostBuilt(1)
    assert pages.Shown(1) == "stream"


def test_only_react_panels_are_mapped():
    with pytest.raises(ValueError):
        WebPanelPages().Add(1, "result")


class _WindowRig:
    """The Window's real page-switching methods (read out of src/Autona.py,
    which cannot be imported: agent-pitfalls #1) on a stack built the way
    _BuildUi builds it, with a few sidebar items: five React ones and two
    Qt ones (the eager scoreboard, the lazy Result Settings -- the lazy Top 8
    until it became a React page, ADR-005 S4i, then the lazy Statistics
    until S4k), in the real order (Qt items between them). Off screen, so the host builds no view; what
    it was asked to show is its _panel."""

    METHODS = ("_OnNavSelected", "_OnStackPageChanged", "_EnsurePanelAt",
               "_WebPanel", "_AdoptWebPanelHost", "_PreparePanel",
               "_CreateEventPanel", "_CreateStreamPanel",
               "_CreateTop8Panel", "_CreateCommentaryPanel",
               "_CreateStatsPanel")

    def __init__(self):
        import ast

        from loguru import logger
        from qtpy.QtWidgets import QDockWidget, QSizePolicy

        tree = ast.parse(_autonaSource())
        window = ast.parse("class Window:\n    pass\n").body[0]
        window.body = [node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name in self.METHODS]
        assert sorted(node.name for node in window.body) == sorted(
            self.METHODS)
        window.body.append(ast.parse(
            "def _ApplyTabBarPolicy(self, panel):\n    pass\n").body[0])
        namespace = {"QDockWidget": QDockWidget, "QSizePolicy": QSizePolicy,
                     "QWidget": QWidget, "logger": logger,
                     "AutonaWebPanelHost": AutonaWebPanelHost}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[window], type_ignores=[])), "src/Autona.py", "exec"),
            namespace)
        self.window = w = namespace["Window"]()
        self.built = []

        def qtPanel(name):
            def factory():
                self.built.append(name)
                return QLabel(name)
            return factory

        w.stack = QStackedWidget()
        w.dockWidgets = []
        w._lazyPanels = {}
        w._panelIndexes = {}
        w._webPanelHost = None
        w._webPanelPages = WebPanelPages()
        w._buildingPanel = False
        # As _BuildUi: event, stream, scoreboard (eager), ..., result, top8,
        # commentary, ..., stats. (Result is after the scoreboard in the
        # sidebar; last here so the indexes below stay one per kind of item.)
        for page, key in ((w._CreateEventPanel, "event"),
                          (w._CreateStreamPanel, "stream"),
                          (QLabel("scoreboard"), None),
                          (w._CreateTop8Panel, "top8"),
                          (w._CreateCommentaryPanel, "commentary"),
                          (w._CreateStatsPanel, "stats"),
                          (qtPanel("result"), "result")):
            if callable(page):
                index = w.stack.addWidget(QWidget())
                w._lazyPanels[index] = (page, key)
                w._panelIndexes[key] = index
                if WebAccess.IsWebPanel(key):
                    w._webPanelPages.Add(index, key)
            else:
                w.stack.addWidget(page)
        w.stack.currentChanged.connect(w._OnStackPageChanged)
        self.placeholders = [w.stack.widget(index)
                             for index in range(w.stack.count())]

    def choose(self, nav):
        # What a sidebar click does (navSelected -> _OnNavSelected).
        self.window._OnNavSelected(nav)
        return self.window.stack.currentIndex()

    def hosts(self):
        return [index for index in range(self.window.stack.count())
                if isinstance(self.window.stack.widget(index),
                              AutonaWebPanelHost)]


EVENT, STREAM, SCOREBOARD, TOP8, COMMENTARY, STATS, RESULT = range(7)


def test_every_react_item_shows_the_one_host(qapp):
    rig = _WindowRig()
    w = rig.window

    # Stream first: the host is built on its page.
    assert rig.choose(STREAM) == STREAM
    assert rig.hosts() == [STREAM]
    host = w._webPanelHost
    assert host._panel == "stream"
    # The other React items' placeholders are never to be built (a second
    # host).
    assert COMMENTARY not in w._lazyPanels
    assert EVENT not in w._lazyPanels
    assert TOP8 not in w._lazyPanels
    assert STATS not in w._lazyPanels

    # Commentary: the same page, told the other panel (the stack did not
    # move, so this is not currentChanged's).
    assert rig.choose(COMMENTARY) == STREAM
    assert host._panel == "commentary"
    assert rig.hosts() == [STREAM]
    assert w.stack.widget(COMMENTARY) is rig.placeholders[COMMENTARY]

    # Away and back: each React item brings its own panel back.
    assert rig.choose(SCOREBOARD) == SCOREBOARD
    assert rig.choose(STREAM) == STREAM
    assert host._panel == "stream"
    assert rig.choose(SCOREBOARD) == SCOREBOARD
    assert rig.choose(COMMENTARY) == STREAM
    assert host._panel == "commentary"
    assert w._webPanelHost is host and rig.hosts() == [STREAM]
    # The Event page too (S4g): the same page, its own panel.
    assert rig.choose(EVENT) == STREAM
    assert host._panel == "event"
    assert w.stack.widget(EVENT) is rig.placeholders[EVENT]
    # The Top 8 page too (S4i).
    assert rig.choose(TOP8) == STREAM
    assert host._panel == "top8"
    assert w.stack.widget(TOP8) is rig.placeholders[TOP8]
    # The Statistics page too (S4k).
    assert rig.choose(STATS) == STREAM
    assert host._panel == "stats"
    assert w.stack.widget(STATS) is rig.placeholders[STATS]
    # The Qt items were built as before, the eager one never.
    assert rig.choose(RESULT) == RESULT
    assert rig.built == ["result"]
    assert w.dockWidgets == []


def test_the_host_is_built_where_the_first_react_item_is_opened(qapp):
    rig = _WindowRig()
    w = rig.window

    assert rig.choose(COMMENTARY) == COMMENTARY
    assert rig.hosts() == [COMMENTARY]
    assert w._webPanelHost._panel == "commentary"
    assert STREAM not in w._lazyPanels

    assert rig.choose(STREAM) == COMMENTARY
    assert w._webPanelHost._panel == "stream"
    assert w.stack.widget(STREAM) is rig.placeholders[STREAM]


def test_choosing_the_shown_react_item_again_tells_the_host_again(
        qapp, monkeypatch):
    # A reveal is how a view whose load failed is loaded again (S4a
    # decision 11): a second click on the item shown is one.
    rig = _WindowRig()
    rig.choose(STREAM)
    asked = []
    monkeypatch.setattr(rig.window._webPanelHost, "ShowPanel", asked.append)

    rig.choose(STREAM)
    rig.choose(COMMENTARY)
    rig.choose(COMMENTARY)

    assert asked == ["stream", "commentary", "commentary"]


def test_a_restored_react_item_builds_the_host_on_its_page(qapp):
    # The restore at startup: the sidebar's saved index, through the same
    # _OnNavSelected (the stack starts on page 0).
    rig = _WindowRig()
    assert rig.window.stack.currentIndex() == 0

    assert rig.choose(COMMENTARY) == COMMENTARY
    assert rig.window._webPanelHost._panel == "commentary"
    assert rig.built == []
    # Restored on page 0, the Event page -- the default, so a fresh
    # install's start (the stack is there already): built all the same.
    rig = _WindowRig()
    assert rig.choose(EVENT) == EVENT
    assert rig.hosts() == [EVENT]
    assert rig.window._webPanelHost._panel == "event"
    assert STREAM not in rig.window._lazyPanels
    assert rig.built == []


def test_a_host_that_fails_to_be_built_is_built_by_the_next_react_item(qapp):
    # A failure between the factory and the stack (S4e review): nothing is
    # recorded, so the next React item opened builds the host -- the host
    # used to be recorded, and the other items' placeholders dropped, before
    # the page was on the stack, which left no React panel to open.
    rig = _WindowRig()
    w = rig.window

    def failing(panel):
        raise RuntimeError("injected")

    w._PreparePanel = failing
    assert rig.choose(STREAM) == STREAM
    assert w._webPanelHost is None
    assert w._webPanelPages.hostIndex is None
    assert COMMENTARY in w._lazyPanels
    assert rig.hosts() == []

    del w._PreparePanel
    assert rig.choose(COMMENTARY) == COMMENTARY
    assert rig.hosts() == [COMMENTARY]
    assert w._webPanelHost._panel == "commentary"
    # The item whose build failed shows the host from then on.
    assert rig.choose(STREAM) == COMMENTARY
    assert w._webPanelHost._panel == "stream"
    assert rig.hosts() == [COMMENTARY]
