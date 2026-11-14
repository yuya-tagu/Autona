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
* off screen it is frozen / discarded (AutonaWebPageLifecycle), and on reveal
  it asks for the full state
  and shows the panel chosen meanwhile -- after a thaw and after the reload
  of a discarded page alike;
* a load that failed for good does not leave the view blank for the rest
  of the process: the next ShowPanel() or reveal loads the page again.

These drive a real QWebEngineView (offscreen) against a throwaway local
HTTP server, because the behaviour under test is Qt's. The served page
stands in for the Web UI's panel shell (``window.autonaPanelShell``,
web/src/PanelShell/hostApi.ts): it records the navigations, full-state
requests and SCORE set selections (S4ab-2, with the path the page was on
then) the host makes, and navigates the way the shell does
(``history.replaceState``, keeping the query). The real shell is covered by
the Web UI's own tests and by Playwright (web/e2e/panel-shell.spec.ts).
"""
import ast
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, Qt, QTimer, QUrl
from qtpy.QtTest import QTest
from qtpy.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import QDialog, QLabel, QStackedWidget, QVBoxLayout, QWidget

from src import AutonaWebPageLifecycle as WPL
from src import AutonaWebPanelHost as HostModule
from src.AutonaWebPanelHost import AutonaWebPanelHost, PanelPath, WebPanelPages
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from src.core import WebAccess
from src.core.SettingsManager import SettingsManager

ACTIVE = QWebEnginePage.LifecycleState.Active
FROZEN = QWebEnginePage.LifecycleState.Frozen
DISCARDED = QWebEnginePage.LifecycleState.Discarded

# A per-document id tells a thaw / a router navigation (same document) from
# a reload (a new one).
PAGE = b"""<!doctype html><html><body><script>
window.__loadId = Math.random();
window.__navigations = [];
window.__refreshes = 0;
window.__scoreSets = [];
window.autonaPanelShell = {
  navigate: function (p) {
    var target = '/web/' + encodeURIComponent(p);
    if (location.pathname === target) { return; }
    window.__navigations.push(p);
    history.replaceState(null, '', target + location.search);
  },
  refresh: function () { window.__refreshes += 1; },
  openScoreSets: function (b) {
    window.__scoreSets.push(b + '@' + location.pathname);
  }
};
</script></body></html>"""


# How long a page gets for what a test waits for. It only decides how long a
# failing test takes: the wait ends as soon as the page has done it, and on a
# machine busy with other work a page's load takes many times what it does
# otherwise: Chromium runs a hidden page's renderer at background priority, and
# under the parallel test runner one load of this small page took 5-12 s.
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
        # value is round-tripped through JSON: PySide6 hands null, arrays and
        # objects back as ''.
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
    # Japanese, and there is a second panel to switch to -- and SCORE, whose
    # set selection the host hands to its page (S4ab-2).
    monkeypatch.setattr(WPL, "_webServerReady", True)
    monkeypatch.setattr(WPL, "_pendingLoaders", [])
    monkeypatch.setattr(WPL, "_readyCallbacks", [])
    target = {"port": port}
    realGet = SettingsManager.Get
    monkeypatch.setattr(
        SettingsManager, "Get",
        lambda key, default=None: (target["port"]
                                   if key == "general.webserver_port"
                                   else realGet(key, default)))
    monkeypatch.setattr(AutonaLocaleHelper, "programLocale", "ja")
    monkeypatch.setattr(WebAccess, "WEB_PANELS",
                        frozenset({"shell-check", "second", "scoreboard"}))
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


def _failedFirstLoad(buildHost, monkeypatch, refusedPort, panel="shell-check",
                     scoreSets=None):
    # The host is shown while nothing listens on the port: the loader's
    # retries are used up and the view is left on an error page. `scoreSets`:
    # SCORE's set selection asked for before the view is built.
    monkeypatch.setattr(WPL.LocalPageLoader, "LOAD_RETRY_MS", 50)
    rig = buildHost()
    rig.usePort(refusedPort)
    rig.host.ShowPanel(panel)
    if scoreSets is not None:
        rig.host.OpenScoreSets(scoreSets)
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


# ── no frame of this server's own origin (ADR-005 S-final SF-8a review) ──
# The design previews load from the preview listener (another port); a
# layout's script there may navigate its frame -- or a frame it makes -- to
# this server's own port, where it would be one of its pages. The view lets
# no frame onto that origin (Qt does not say which frame asks; no page of the
# panels frames it). The server refuses it too (tests/test_web_access.py).

# Stands in for a layout on the preview listener: it navigates itself to
# ?go=, or frames ?nest=.
FRAME_PAGE = b"""<!doctype html><html><body><script>
var q = new URLSearchParams(location.search);
if (q.get('go')) { location.href = q.get('go'); }
if (q.get('nest')) {
  var f = document.createElement('iframe');
  f.src = q.get('nest');
  document.body.appendChild(f);
}
</script></body></html>"""


@pytest.fixture(scope="module")
def frameServer():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith("/favicon"):
                requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(FRAME_PAGE)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(FRAME_PAGE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], requests
    server.shutdown()
    server.server_close()


def _frame(rig, url):
    rig.js("(function(u){var f=document.createElement('iframe');f.src=u;"
           f"document.body.appendChild(f);return 1;}})({json.dumps(url)})")


def test_no_frame_goes_to_this_servers_own_origin(buildHost, monkeypatch):
    monkeypatch.setattr(HostModule, "_lanHosts", ("192.0.2.10",))
    rig = _shown(buildHost)
    port = rig.servingPort
    N = QWebEnginePage.NavigationType
    kinds = (N.NavigationTypeLinkClicked, N.NavigationTypeOther,
             N.NavigationTypeRedirect, N.NavigationTypeTyped,
             N.NavigationTypeReload)

    for url in (f"http://127.0.0.1:{port}/layout/scoreboard/index.html",
                f"http://localhost:{port}/web/board",
                f"http://x@127.0.0.1:{port}/layout/a.html",
                f"http://192.0.2.10:{port}/layout/a.html"):   # this PC's LAN
        for kind in kinds:
            assert rig.page.acceptNavigationRequest(
                QUrl(url), kind, False) is False, (url, kind)
    # The main frame's document is untouched: no refusal of its own.
    assert not rig.page.refusedLast

    for url in (f"http://127.0.0.1:{port + 1}/layout/a.html",  # the previews'
                f"http://192.0.2.10:{port + 1}/layout/a.html",
                "https://example.com/widget", "about:blank", "about:srcdoc",
                "data:text/html,a"):
        assert rig.page.acceptNavigationRequest(
            QUrl(url), N.NavigationTypeLinkClicked, False) is True, url
    # Only http(s) is the server's: an about: frame of a page that is itself
    # still about:blank is not "the page's own origin".
    for url in ("about:blank", "about:srcdoc", "data:text/html,a"):
        assert HostModule._IsServerOrigin(
            QUrl(url), (QUrl("about:blank"), QUrl(url))) is False, url
    # The main frame keeps its rule: its own origin goes through.
    assert rig.page.acceptNavigationRequest(
        QUrl(f"http://127.0.0.1:{port}/web/second"),
        N.NavigationTypeLinkClicked, True) is True


def test_a_frame_sent_to_this_servers_own_origin_does_not_get_there(
        buildHost, frameServer, caplog):
    # Chromium's own navigations of a frame: itself (location.href) and one
    # it makes (<iframe src>). The server's port never sees them.
    rig = _shown(buildHost)
    port = rig.servingPort
    framePort, frameRequests = frameServer
    frameRequests.clear()
    loads = list(rig.loads)
    elsewhere = f"http://127.0.0.1:{framePort}/frame?"

    # Control: a navigation of the frame on its own origin goes through.
    _frame(rig, elsewhere + "go=" + quote(
        f"http://127.0.0.1:{framePort}/second", safe=""))
    assert _waitUntil(lambda: "/second" in frameRequests)

    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        _frame(rig, elsewhere + "go=" + quote(
            f"http://127.0.0.1:{port}/from-frame", safe=""))
        _frame(rig, elsewhere + "nest=" + quote(
            f"http://127.0.0.1:{port}/nested", safe=""))
        assert _waitUntil(
            lambda: caplog.text.count("refused a frame of this server") >= 2)

    _waitUntil(lambda: False, timeout=0.5)
    assert "/from-frame" not in rig.requests
    assert "/nested" not in rig.requests
    # The page itself is as it was: loaded, no failed load reported.
    assert rig.loads == loads
    assert rig.host._loaded
    assert not rig.page.refusedLast


# ── a new window goes to the default browser (ADR-005 S4r) ──────────────

# What a page of this server's asks from, to _OpenOutside called on its own.
PAGE_URL = "http://127.0.0.1:5107/web/board?desktop=1&lng=ja"
DESIGNER_URL = "http://127.0.0.1:5107/web?view=designer"


def _pageAsking():
    page = SimpleNamespace(url=lambda: QUrl(PAGE_URL), designer=[])
    page.designerRequested = SimpleNamespace(
        emit=lambda: page.designer.append(True))
    return page


def _handedOutside(monkeypatch):
    # What the host gives the operating system: a spy, so no browser opens.
    opened = []
    monkeypatch.setattr(
        HostModule, "QDesktopServices",
        SimpleNamespace(openUrl=lambda url: not opened.append(
            bytes(url.toEncoded()).decode())))
    return opened


def _clickView(rig):
    # A real click on the view, the only thing a new window answers to. One sent
    # before the page's first frame is lost, so until the page has seen one.
    def clicked():
        QTest.mouseClick(rig.host.view.focusProxy(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
        return _waitUntil(lambda: rig.js("window.__clicks") > 0, timeout=0.5)
    assert _waitUntil(clicked), "the page saw no click"


def _clickOpening(rig, url):
    rig.js("window.__clicks = 0, window.addEventListener('click', function () {"
           " window.__clicks += 1; window.open("
           + json.dumps(url) + ", '_blank', 'noopener'); }, {once: true}), 0")
    _clickView(rig)


def test_a_click_gives_a_web_link_to_the_default_browser(buildHost, monkeypatch):
    # The post on X (decision 11): the intent URL, in the operator's browser
    # where they are signed in. The view itself goes nowhere.
    opened = _handedOutside(monkeypatch)
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    url = "https://example.test/post?text=a%20b"

    _clickOpening(rig, url)

    assert _waitUntil(lambda: opened)
    assert opened == [url]
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")
    assert rig.js("window.__loadId") == document
    assert len(rig.loads) == 1


def test_a_click_on_a_real_link_goes_to_the_default_browser(buildHost, monkeypatch):
    # What the page's "open the image" is, on a computer that cannot copy it.
    opened = _handedOutside(monkeypatch)
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    url = "https://example.test/out/results/a.png?v=2"

    rig.js("(function () { var link = document.createElement('a'); link.href = "
           + json.dumps(url) + "; link.target = '_blank';"
           " link.rel = 'noopener noreferrer'; link.textContent = 'open';"
           " link.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;"
           "display:block'; document.body.appendChild(link);"
           " window.__clicks = 0; link.addEventListener('click', function () {"
           " window.__clicks += 1; }, {once: true}); return 0; })()")
    _clickView(rig)

    assert _waitUntil(lambda: opened)
    assert opened == [url]
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")
    assert rig.js("window.__loadId") == document
    assert len(rig.loads) == 1


def test_script_alone_opens_nothing(buildHost, monkeypatch):
    opened = _handedOutside(monkeypatch)
    rig = _shown(buildHost)

    rig.js("void window.open('https://example.test/no-click', '_blank',"
           " 'noopener')")
    rig.js("void window.open('https://example.test/no-click-either')")
    # The click after them says they have had their chance.
    _clickOpening(rig, "https://example.test/clicked")

    assert _waitUntil(lambda: opened)
    assert opened == ["https://example.test/clicked"]


@pytest.mark.parametrize("url", [
    "file:///c:/windows/system32/calc.exe", "ms-calculator:",
])
def test_a_click_opens_no_other_scheme(buildHost, monkeypatch, url):
    # A page on the LAN starts no application here. A spy on the page's own
    # route, as Chromium may not even ask for these.
    opened = _handedOutside(monkeypatch)
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")

    _clickOpening(rig, url)
    _clickOpening(rig, "https://example.test/clicked")

    assert _waitUntil(lambda: opened)
    assert opened == ["https://example.test/clicked"]
    assert rig.js("window.__loadId") == document


@pytest.mark.parametrize("url, handed", [
    ("https://example.test/a", True),
    ("http://192.168.0.2/a", True),
    ("file:///c:/windows/system32/calc.exe", False),
    ("ms-calculator:", False),
    ("javascript:alert(1)", False),
    ("data:text/html,x", False),
    ("", False),
])
def test_only_http_and_https_are_handed_outside(monkeypatch, url, handed):
    # The rule itself, whatever reaches it.
    opened = _handedOutside(monkeypatch)

    HostModule._PanelPage._OpenOutside(
        _pageAsking(), SimpleNamespace(requestedUrl=lambda: QUrl(url),
                                       isUserInitiated=lambda: True))

    assert opened == ([url] if handed else [])


def test_a_window_no_click_asked_for_is_not_handed_outside(monkeypatch, caplog):
    # A layout's script can open what it likes in a click of its own; without
    # one, whatever got past the view's settings stops here.
    opened = _handedOutside(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        HostModule._PanelPage._OpenOutside(
            _pageAsking(), SimpleNamespace(
                requestedUrl=lambda: QUrl("https://example.test/a?text=secret"),
                isUserInitiated=lambda: False))

    assert opened == []
    assert "example.test" in caplog.text
    assert "secret" not in caplog.text


def test_a_link_the_system_does_not_open_is_logged_without_its_text(
        monkeypatch, caplog):
    # The operator sees no browser come up: the log is where it says why. The
    # query of the post on X is theirs, and not for the log.
    monkeypatch.setattr(HostModule, "QDesktopServices",
                        SimpleNamespace(openUrl=lambda url: False))

    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        HostModule._PanelPage._OpenOutside(
            _pageAsking(), SimpleNamespace(
                requestedUrl=lambda: QUrl("https://example.test/a?text=secret"),
                isUserInitiated=lambda: True))

    assert "could not open" in caplog.text
    assert "example.test" in caplog.text
    assert "secret" not in caplog.text


# ── the Board page's Designer (ADR-005 S4s) ─────────────────────────────

def _designers(monkeypatch):
    # Stands in for AutonaDesignerWindow, whose real page and token are not what
    # this is about: built, its parent, how many times it was brought forward.
    built = []

    class Window(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.fronted = 0
            built.append(self)

        def activateWindow(self):
            self.fronted += 1
            super().activateWindow()

    monkeypatch.setattr(HostModule, "AutonaDesignerWindow", Window)
    return built


def test_a_click_on_the_designer_url_opens_its_window_on_the_main_window(
        buildHost, monkeypatch):
    opened = _handedOutside(monkeypatch)
    built = _designers(monkeypatch)
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")

    _clickOpening(rig, "/web?view=designer")

    assert _waitUntil(lambda: built), "the Designer's window did not open"
    assert built[0].parent() is rig.window
    assert built[0].isVisible()
    assert built[0].fronted == 1
    # Not the default browser's, and the view stayed on its page.
    assert opened == []
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")
    assert rig.js("window.__loadId") == document
    assert len(rig.loads) == 1


def test_a_designer_window_already_open_is_brought_to_the_front(
        buildHost, monkeypatch):
    _handedOutside(monkeypatch)
    built = _designers(monkeypatch)
    rig = _shown(buildHost)

    _clickOpening(rig, "/web?view=designer")
    assert _waitUntil(lambda: built)
    _clickOpening(rig, "/web?view=designer")

    assert _waitUntil(lambda: built[0].fronted == 2)
    assert len(built) == 1


def test_a_closed_designer_window_is_built_anew(buildHost, monkeypatch):
    built = _designers(monkeypatch)
    rig = buildHost()
    rig.host._OpenDesigner()
    first = built[0]

    first.close()
    # At loop level 0 DeferredDelete is only delivered on request (see
    # conftest's _dropBoard): WA_DeleteOnClose's deleteLater.
    QCoreApplication.sendPostedEvents(first, QEvent.Type.DeferredDelete)
    assert _waitUntil(lambda: rig.host._designerWindow is None)

    rig.host._OpenDesigner()

    assert len(built) == 2
    assert rig.host._designerWindow is built[1]


def test_script_alone_opens_no_designer(buildHost, monkeypatch):
    opened = _handedOutside(monkeypatch)
    built = _designers(monkeypatch)
    rig = _shown(buildHost)

    rig.js("void window.open('/web?view=designer', '_blank', 'noopener')")
    _clickOpening(rig, "https://example.test/clicked")

    assert _waitUntil(lambda: opened)
    assert opened == ["https://example.test/clicked"]
    assert built == []


def test_the_designer_url_opens_the_host_only(monkeypatch):
    opened = _handedOutside(monkeypatch)
    page = _pageAsking()

    HostModule._PanelPage._OpenOutside(
        page, SimpleNamespace(requestedUrl=lambda: QUrl(DESIGNER_URL),
                              isUserInitiated=lambda: True))

    assert page.designer == [True]
    assert opened == []


def test_the_designer_url_without_a_click_opens_nothing(monkeypatch):
    opened = _handedOutside(monkeypatch)
    page = _pageAsking()

    HostModule._PanelPage._OpenOutside(
        page, SimpleNamespace(requestedUrl=lambda: QUrl(DESIGNER_URL),
                              isUserInitiated=lambda: False))

    assert page.designer == []
    assert opened == []


@pytest.mark.parametrize("url", [
    "http://localhost:5107/web?view=designer",          # another host name
    "http://192.168.0.2:5107/web?view=designer",        # another computer
    "http://127.0.0.1:5108/web?view=designer",          # another port
    "https://127.0.0.1:5107/web?view=designer",         # another scheme
    "http://127.0.0.1:5107/web/?view=designer",         # another path
    "http://127.0.0.1:5107/web/board?view=designer",
    "http://127.0.0.1:5107/web",                        # another query
    "http://127.0.0.1:5107/web?view=designer&desktop=1",
    "http://127.0.0.1:5107/web?view=designer&token=x",
    "http://127.0.0.1:5107/web?view=Designer",
    "http://127.0.0.1:5107/web?view=designer#x",        # a fragment
    "http://me@127.0.0.1:5107/web?view=designer",       # a user
])
def test_only_the_designers_exact_url_is_kept_from_the_browser(monkeypatch, url):
    opened = _handedOutside(monkeypatch)
    page = _pageAsking()

    HostModule._PanelPage._OpenOutside(
        page, SimpleNamespace(requestedUrl=lambda: QUrl(url),
                              isUserInitiated=lambda: True))

    assert page.designer == []
    assert opened == [url]


# ── the SCORE page's "ブラウザ版を開く" (ADR-005 S4aa) ───────────────────

# A made-up token: looked for in the URL handed outside, and not in the log.
FAKE_TOKEN = "Made_Up_Token_For_The_Test_0123"


def _webServerSettings(monkeypatch, *, lan, token=FAKE_TOKEN,
                       host="192.168.0.5", listening=None):
    # What the URL is built from at the click: the port and LAN access (what
    # the server listens on, the settings while none runs), this PC's LAN
    # address, and the token.
    from src.AutonaWebServer import WebServer
    from src.Helpers import AutonaNetworkHelper

    settings = {"general.webserver_port": 5107,
                "general.webserver_listen_lan": lan}
    monkeypatch.setattr(SettingsManager, "Get",
                        lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(HostModule, "GetLocalIP", lambda: host)
    # The Qt board's address (GetWebServerHost) too.
    monkeypatch.setattr(AutonaNetworkHelper, "GetLocalIP", lambda: host)
    monkeypatch.setattr(WebServer, "_listening", listening)
    monkeypatch.setattr(HostModule.WebAccess, "Token", lambda: token)


def _askedFor(path, clicked=True, page=None):
    HostModule._PanelPage._OpenOutside(
        page or _pageAsking(), SimpleNamespace(
            requestedUrl=lambda: QUrl("http://127.0.0.1:5107" + path),
            isUserInitiated=lambda: clicked))


@pytest.mark.parametrize("path, lan, token, host, handed", [
    # LAN access off: no token, which a page on this PC does not need.
    ("/scoreboard", False, FAKE_TOKEN, "127.0.0.1",
     "http://127.0.0.1:5107/scoreboard"),
    ("/team", False, FAKE_TOKEN, "127.0.0.1", "http://127.0.0.1:5107/team"),
    # LAN access on: the address other devices reach, with the token, so the
    # same URL works copied to one of them.
    ("/scoreboard", True, FAKE_TOKEN, "192.168.0.5",
     f"http://192.168.0.5:5107/scoreboard?token={FAKE_TOKEN}"),
    ("/team", True, FAKE_TOKEN, "192.168.0.5",
     f"http://192.168.0.5:5107/team?token={FAKE_TOKEN}"),
    # No usable token: none to carry.
    ("/scoreboard", True, "", "192.168.0.5",
     "http://192.168.0.5:5107/scoreboard"),
])
def test_the_browser_scoreboard_is_built_by_the_host_at_the_click(
        monkeypatch, path, lan, token, host, handed):
    opened = _handedOutside(monkeypatch)
    _webServerSettings(monkeypatch, lan=lan, token=token, host=host)
    page = _pageAsking()

    _askedFor(path, page=page)

    assert opened == [handed]
    assert page.designer == []


@pytest.mark.parametrize("lan, token", [
    (False, FAKE_TOKEN), (True, FAKE_TOKEN), (True, ""), (False, ""),
])
@pytest.mark.parametrize("listening", [False, True])
def test_the_browser_scoreboard_is_the_url_the_qt_boards_button_opened(
        monkeypatch, lan, token, listening):
    # With the server listening as the settings say (or none running), the
    # same URL as the Qt board's button opened until ADR-005 S4ac-3 (its
    # _WebScoreboardOpenUrl, spelled out here): the address read as the board
    # did when it was built, and the token only with LAN access on.
    import urllib.parse

    from src.Helpers.AutonaNetworkHelper import GetWebServerHost

    _webServerSettings(
        monkeypatch, lan=lan, token=token,
        listening=("0.0.0.0" if lan else "127.0.0.1", 5107, lan)
        if listening else None)
    for path in ("/scoreboard", "/team"):
        board = f"http://{GetWebServerHost()}:5107{path}"
        if token and lan:
            board += "?" + urllib.parse.urlencode({"token": token})
        assert HostModule.BrowserScoreboardUrl(path) == board


@pytest.mark.parametrize("lan, listening, handed", [
    # LAN access turned on in the settings, the server not restarted yet:
    # still this PC's address, without the token.
    (True, ("127.0.0.1", 5107, False), "http://127.0.0.1:5107/team"),
    # Turned off, not restarted: still the LAN address and the token.
    (False, ("0.0.0.0", 5107, True),
     f"http://192.168.0.5:5107/team?token={FAKE_TOKEN}"),
    # The port changed in the settings: the one listened on.
    (False, ("127.0.0.1", 5108, False), "http://127.0.0.1:5108/team"),
])
def test_the_browser_scoreboard_is_where_the_server_really_listens(
        monkeypatch, lan, listening, handed):
    _webServerSettings(monkeypatch, lan=lan, listening=listening)

    assert HostModule.BrowserScoreboardUrl("/team") == handed


def test_the_browser_scoreboard_without_a_click_opens_nothing(monkeypatch):
    opened = _handedOutside(monkeypatch)
    _webServerSettings(monkeypatch, lan=True)

    _askedFor("/scoreboard", clicked=False)
    _askedFor("/team", clicked=False)

    assert opened == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5107/scoreboard?token=x",         # a query of its own
    "http://127.0.0.1:5107/scoreboard?board=1",
    "http://127.0.0.1:5107/scoreboard#x",               # a fragment
    "http://me@127.0.0.1:5107/team",                    # a user
    "http://127.0.0.1:5107/scoreboard/",                # another path
    "http://127.0.0.1:5107/Scoreboard",
    "http://127.0.0.1:5107/web/scoreboard",
    "http://localhost:5107/scoreboard",                 # another host name
    "http://192.168.0.2:5107/team",                     # another computer
    "http://127.0.0.1:5108/team",                       # another port
    "https://127.0.0.1:5107/team",                      # another scheme
])
def test_only_the_browser_scoreboards_exact_url_gets_the_token(
        monkeypatch, url):
    # Anything else is the web's link, handed on as it was asked for: the
    # token goes to no other URL, and nothing of the page's own is carried.
    opened = _handedOutside(monkeypatch)
    _webServerSettings(monkeypatch, lan=True)

    HostModule._PanelPage._OpenOutside(
        _pageAsking(), SimpleNamespace(requestedUrl=lambda: QUrl(url),
                                       isUserInitiated=lambda: True))

    assert opened == [url]
    assert FAKE_TOKEN not in opened[0]


def test_a_browser_scoreboard_the_system_does_not_open_is_logged_without_the_token(
        monkeypatch, caplog):
    monkeypatch.setattr(HostModule, "QDesktopServices",
                        SimpleNamespace(openUrl=lambda url: False))
    _webServerSettings(monkeypatch, lan=True)

    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        _askedFor("/scoreboard")

    assert "could not open" in caplog.text
    assert "192.168.0.5" in caplog.text
    assert FAKE_TOKEN not in caplog.text


def test_the_score_page_asks_for_the_browser_scoreboard_bare():
    # The token is the host's to add (above): the page opens the bare paths,
    # and the page the host loads carries none.
    import re
    from tests.conftest import PROJECT_ROOT

    source = (PROJECT_ROOT / "web" / "src" / "ScorePage"
              / "index.tsx").read_text(encoding="utf-8")
    asked = set(re.findall(r'"(/(?:scoreboard|team)[^"]*)"', source))
    assert asked == set(HostModule._BROWSER_SCOREBOARD_PATHS)
    assert "token=" not in source
    assert "token" not in PanelPath("scoreboard")


def test_the_view_asks_the_system_for_nothing_by_itself(buildHost):
    settings = _shown(buildHost).host.view.settings()

    assert not settings.testAttribute(
        QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows)
    assert settings.unknownUrlSchemePolicy() == (
        QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)


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


def test_minimizing_the_window_freezes_the_host(buildHost):
    # The view still claims to be visible; only the page knows better. That is
    # why the lifecycle keys off the page's own visibility.
    rig = _shown(buildHost)

    rig.window.showMinimized()
    assert rig.host.view.isVisible()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.window.showNormal()
    assert rig.state() == ACTIVE
    assert _waitUntil(lambda: rig.js("window.__refreshes") > 0)


def test_a_host_shown_again_before_the_delay_is_not_discarded(buildHost):
    rig = _shown(buildHost, discardAfterMs=800)
    document = rig.js("window.__loadId")

    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.reveal()

    # Well past the delay, still the same document.
    assert not _waitUntil(lambda: rig.state() != ACTIVE, timeout=1.6)
    assert rig.js("window.__loadId") == document


def test_a_load_that_ends_while_hidden_freezes_the_page_again(buildHost):
    # Qt's recommended state settles while the load holds the freeze back, so
    # nothing else would ask again: the page would stay Active on no screen.
    # _OnLoadFinished asks (HiddenPageLifecycle.ScheduleFreeze).
    rig = _shown(buildHost)
    loading = [True]
    rig.host._lifecycle._busy = lambda: loading[0]
    rig.hide()
    assert not _waitUntil(lambda: rig.state() == FROZEN, timeout=0.5)

    loading[0] = False
    rig.host._OnLoadFinished(True)

    assert _waitUntil(lambda: rig.state() == FROZEN)


def test_a_load_on_a_hidden_host_shows_its_page_and_freezes_again(buildHost):
    rig = _shown(buildHost)
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.host._loader.load(PanelPath("second"))

    assert _waitUntil(lambda: rig.path()[0] == "/web/second")
    assert _waitUntil(lambda: rig.state() == FROZEN)


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


# ── SCORE's set selection from the hotkey / legacy route (ADR-005 S4ab-2) ─
#
# Window.OpenScoreSets shows SCORE (ShowPanel) and then asks the host, which
# hands the request to the page's panel shell after the navigation, when the
# page can take script -- now, or after the view's first load, a thaw, the
# reload of a discarded page -- and holds it until a panel shell has it.

def _scoreShown(buildHost, **kwargs):
    rig = buildHost(**kwargs)
    rig.host.ShowPanel("scoreboard")
    rig.reveal()
    rig.waitLoaded()
    return rig


def _scoreSets(rig):
    return rig.js("window.__scoreSets.join()")


def _taken(rig):
    return _waitUntil(lambda: rig.host._scoreSets is None)


def test_the_score_sets_reach_the_page_shown_once(buildHost):
    rig = _scoreShown(buildHost)

    rig.host.OpenScoreSets(2)

    assert _waitUntil(lambda: _scoreSets(rig) == "2@/web/scoreboard")
    assert _taken(rig)
    # Taken: not handed again by a later reveal or a click on SCORE.
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.reveal()
    rig.host.ShowPanel("scoreboard")
    assert _waitUntil(lambda: rig.js("window.__refreshes") > 0)
    assert not _waitUntil(lambda: _scoreSets(rig) != "2@/web/scoreboard",
                          timeout=0.5)


def test_the_score_sets_follow_the_navigation_to_score(buildHost):
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")

    rig.host.ShowPanel("scoreboard")
    rig.host.OpenScoreSets(1)

    # On SCORE when the page has it: the same document, navigated first.
    assert _waitUntil(lambda: _scoreSets(rig) == "1@/web/scoreboard")
    assert rig.js("window.__navigations.join()") == "scoreboard"
    assert rig.js("window.__loadId") == document
    assert _taken(rig)


def test_the_score_sets_asked_before_the_view_is_built_reach_its_first_load(
        buildHost):
    # SCORE opened for the first time by the hotkey: the view is built, the
    # page loaded, then handed the request.
    rig = buildHost()
    rig.host.ShowPanel("scoreboard")
    rig.host.OpenScoreSets(1)
    assert rig.host.view is None
    assert rig.host._scoreSets is not None

    rig.reveal()
    rig.waitLoaded()

    assert _waitUntil(lambda: _scoreSets(rig) == "1@/web/scoreboard")
    assert rig.requests == ["/web/scoreboard?desktop=1&lng=ja"]
    assert _taken(rig)


def test_the_score_sets_asked_while_frozen_reach_the_page_on_reveal(buildHost):
    rig = _scoreShown(buildHost)
    document = rig.js("window.__loadId")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.host.OpenScoreSets(1)
    # A newer one replaces it.
    rig.host.OpenScoreSets(2)
    assert rig.host._scoreSets is not None
    rig.reveal()

    assert _waitUntil(lambda: _scoreSets(rig) == "2@/web/scoreboard")
    assert rig.js("window.__loadId") == document
    assert _taken(rig)


def test_the_score_sets_asked_while_discarded_reach_the_reloaded_page(
        buildHost):
    rig = _scoreShown(buildHost, discardAfterMs=200)
    document = rig.js("window.__loadId")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == DISCARDED)

    rig.host.OpenScoreSets(2)
    rig.reveal()

    rig.waitLoaded(count=2)
    assert _waitUntil(lambda: _scoreSets(rig) == "2@/web/scoreboard")
    assert rig.js("window.__loadId") != document
    assert _taken(rig)


def test_the_score_sets_are_forgotten_when_another_panel_is_shown_first(
        buildHost):
    # The operator went elsewhere before the page could have it: not opened
    # later, out of the blue, when SCORE is shown again.
    rig = _scoreShown(buildHost)
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.host.OpenScoreSets(1)

    rig.host.ShowPanel("second")
    assert rig.host._scoreSets is None
    rig.host.ShowPanel("scoreboard")
    rig.reveal()

    assert _waitUntil(lambda: rig.js("window.__refreshes") > 0)
    assert not _waitUntil(lambda: _scoreSets(rig) != "", timeout=0.5)


def test_the_score_sets_are_not_asked_of_another_panel(buildHost):
    rig = _shown(buildHost)

    rig.host.OpenScoreSets(1)

    assert rig.host._scoreSets is None
    assert not _waitUntil(lambda: _scoreSets(rig) != "", timeout=0.5)
    assert rig.path()[0] == "/web/shell-check"


def test_the_score_sets_wait_for_a_page_that_can_take_them(buildHost):
    # A Web UI built before the call existed: held (each reveal tries again)
    # until a panel shell has it.
    rig = _scoreShown(buildHost)
    rig.js("void (window.__open = window.autonaPanelShell.openScoreSets,"
           " delete window.autonaPanelShell.openScoreSets)")

    rig.host.OpenScoreSets(1)
    assert not _waitUntil(lambda: rig.host._scoreSets is None, timeout=0.5)

    rig.js("void (window.autonaPanelShell.openScoreSets = window.__open)")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.reveal()

    assert _waitUntil(lambda: _scoreSets(rig) == "1@/web/scoreboard")
    assert _taken(rig)


def test_the_score_sets_are_run_into_a_document_once_until_answered(buildHost):
    # A click on SCORE (or a reveal) before the page's answer is back: not
    # handed over a second time.
    rig = _scoreShown(buildHost)

    rig.host.OpenScoreSets(1)
    rig.host.ShowPanel("scoreboard")
    rig.host.ShowPanel("scoreboard")

    assert _taken(rig)
    assert _scoreSets(rig) == "1@/web/scoreboard"


def test_a_late_answer_to_an_older_request_leaves_the_newer_one(buildHost):
    # Request 1 is run; before its "taken" is back, request 2 is run into a
    # page that cannot take it (its answer is ""). Request 1's answer does not
    # drop request 2, which reaches the page once it can.
    rig = _scoreShown(buildHost)

    rig.host.OpenScoreSets(1)
    # In order after request 1's script, before request 2's.
    rig.page.runJavaScript(
        "void (window.__open = window.autonaPanelShell.openScoreSets,"
        " delete window.autonaPanelShell.openScoreSets)")
    rig.host.OpenScoreSets(2)

    # Both answers back: request 2's ("") clears what was running.
    assert _waitUntil(lambda: rig.host._scoreSetsRunning is None)
    assert _scoreSets(rig) == "1@/web/scoreboard"
    assert rig.host._scoreSets is not None
    assert rig.host._scoreSets[1] == 2

    rig.js("void (window.autonaPanelShell.openScoreSets = window.__open)")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.reveal()

    assert _waitUntil(
        lambda: _scoreSets(rig) == "1@/web/scoreboard,2@/web/scoreboard")
    assert _taken(rig)


def test_the_score_sets_are_dropped_when_the_view_fails_to_load(
        buildHost, monkeypatch, refusedPort, caplog):
    # Asked for on SCORE's first opening while the server does not answer:
    # the load fails for good. A later load that succeeds -- the operator's
    # next click on an item -- does not open the dialog out of the blue.
    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        rig = _failedFirstLoad(buildHost, monkeypatch, refusedPort,
                               panel="scoreboard", scoreSets=1)
    failures = len(rig.loads)
    assert rig.host._scoreSets is None
    assert "dropped: the view failed to load" in caplog.text

    rig.host.ShowPanel("scoreboard")

    rig.waitLoaded(count=failures + 1)
    assert rig.requests == ["/web/scoreboard?desktop=1&lng=ja"]
    assert rig.path()[0] == "/web/scoreboard"
    assert not _waitUntil(lambda: _scoreSets(rig) != "", timeout=0.5)


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
    realGet = SettingsManager.Get
    monkeypatch.setattr(
        SettingsManager, "Get",
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


def test_the_thumbnail_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateThumbnailPanel"))

    # The React page is the only Thumbnail Settings panel (ADR-005 S4p).
    assert calls == [("self._WebPanel", ["'thumbnail'"])]

    # Still the lazy factory in Display, keyed by the React panel's name,
    # with its icon and label.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateThumbnailPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'thumbnail.svg'"
    assert "Thumbnail Settings" in ast.unparse(entries[0].elts[2])
    assert ast.unparse(entries[0].elts[-1]) == "'thumbnail'"
    assert "thumbnail" in WebAccess.WEB_PANELS
    # The battle format no longer reaches a thumbnail panel: the page shows
    # the team-name toggle in every format.
    source = _autonaSource()
    assert "thumbnailSetting" not in source


def test_the_scoreboard_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateScoreboardPanel"))

    # The React page is the only SCORE panel (ADR-005 S4aa; the way back to
    # the Qt panel went with the Qt boards in S4ac-3).
    assert calls == [("self._WebPanel", ["'scoreboard'"])]

    # A lazy factory where the eager dock was, keyed by the React panel's
    # name, with its icon and label.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateScoreboardPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'scoreboard.svg'"
    assert "Scoreboard" in ast.unparse(entries[0].elts[2])
    assert ast.unparse(entries[0].elts[-1]) == "'scoreboard'"
    assert "scoreboard" in WebAccess.WEB_PANELS
    assert not hasattr(HostModule, "QT_FALLBACK_SETTINGS")
    # No dock of the boards left on the window.
    assert "self.scoreboard" not in _autonaSource()


def test_the_board_item_opens_the_react_page():
    import ast
    calls = _calls(_windowMethod("_CreateBoardPanel"))

    # The React page is the only Board panel (ADR-005 S4s).
    assert calls == [("self._WebPanel", ["'board'"])]

    # Still the lazy factory after the scoreboard, keyed by the React panel's
    # name, with its icon and label.
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateBoardPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[1]) == "'board.svg'"
    assert "Board" in ast.unparse(entries[0].elts[2])
    assert ast.unparse(entries[0].elts[-1]) == "'board'"
    assert "board" in WebAccess.WEB_PANELS


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
    --, Top 8 S4i, Statistics S4k, the Player List S4n, Thumbnail S4p, Board
    S4s and SCORE S4aa -- its key is the React page's name now): no
    STATE_VERSION bump (ADR-005 S4 方針 5, S4 Event 論点 1)."""
    import ast
    build = _windowMethod("_BuildUi")
    keys = [ast.unparse(node.elts[-1]) for node in ast.walk(build)
            if isinstance(node, ast.Tuple) and len(node.elts) == 4
            and ast.unparse(node.elts[1]).endswith(".svg'")]
    assert keys == [
        "'event'", "'stream'", "'scoreboard'", "'board'", "'thumbnail'",
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


def test_no_page_is_a_dock():
    """Every page is the React panels' host or a placeholder for it since
    ADR-005 S4ac-3 (the boards' dock was the last Qt panel): the window has
    no dock chrome to prepare and keeps no docks."""
    import ast
    tree = ast.parse(_autonaSource())
    methods = {node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)}
    assert "_PreparePanel" not in methods
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "QDockWidget" not in names
    attributes = {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
    assert "dockWidgets" not in attributes


def test_the_qt_commentary_panel_and_its_switch_are_gone():
    # ADR-005 S4c: the React page is the only Commentary panel. Neither the
    # widget nor the `general.qt_commentary_panel` way back (a stale key in
    # a user's settings.json is simply never read) may come back in src/.
    # (The way-back mechanism went with SCORE's Qt panel in S4ac-3.)
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not (src / "AutonaCommentaryWidget.py").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for path in src.rglob("*.py")
            if any(word in path.read_text(encoding="utf-8")
                   for word in ("AutonaCommentaryWidget",
                                "qt_commentary_panel"))]
    assert left == []
    assert not hasattr(HostModule, "QT_FALLBACK_SETTINGS")


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

    # The player card that had the editor mode went with the Qt SCORE
    # (ADR-005 S4ac-3).
    assert not (src / "AutonaScoreboardPlayerWidget.py").exists()


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


def test_the_qt_thumbnail_panel_is_gone():
    # ADR-005 S4p: the React page is the only Thumbnail Settings panel. The
    # widget and its .ui may not come back in src/: no import, no
    # construction (comments recording where code came from still name it),
    # and none of the translations only they used. SCORE no longer takes
    # anything from it (decision 10: it imported the module with *).
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    name = "AutonaThumbnailSettingsWidget"
    assert not (src / f"{name}.py").exists()
    assert not (src / "layout" / "AutonaThumbnailSettings.ui").exists()
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui", "*.ts")
            for path in src.rglob(pattern)
            if any(word in path.read_text(encoding="utf-8")
                   for word in (f"import {name}", f"{name}(",
                                f"{name}.instance", f"{name}.py",
                                f"{name} import",
                                "AutonaThumbnailSettings.ui"))]
    assert left == []

    for path in (src / "i18n").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        back = [source for source in (
                    "Copy Video Title", "Smooth scaling", "Nothing to copy.",
                    "Copied to clipboard.", "Flip P1 assets?",
                    "Flip P2 assets?", "Select Thumbnail Design")
                if f"<source>{source}</source>" in text]
        assert back == [], path.name

    # The translations core still uses stay. ("The thumbnail has been
    # generated here:" was the Qt SCORE's GenerateThumbnail's, and went with
    # it in ADR-005 S4ac-3.)
    for path in (src / "i18n").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        for source in ("Standard", "Center Panel", "Thumbnail Settings"):
            assert f"<source>{source}</source>" in text, (path.name, source)


def test_the_qt_result_panel_is_gone():
    # ADR-005 S4r: the React page is the only Result panel. Its widget, its .ui,
    # its renderer and its generator may not come back in src/, nor the
    # translations only they used. The layouts' names are core's now.
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    for gone in ("AutonaResultSettingsWidget.py",
                 "layout/AutonaResultSettings.ui",
                 "result/main_generate_result.py",
                 "result/ResultRenderer.py"):
        assert not (src / gone).exists(), gone
    words = ("import AutonaResultSettingsWidget", "AutonaResultSettingsWidget(",
             "AutonaResultSettingsWidget.py", "AutonaResultSettings.ui",
             "import main_generate_result", "main_generate_result.py",
             "import ResultRenderer", "ResultRenderer(", "DragCardList(")
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui", "*.ts")
            for path in src.rglob(pattern)
            if path.name != "ResultService.py"
            and any(word in path.read_text(encoding="utf-8")
                    for word in words)]
    assert left == []

    for path in (src / "i18n").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        back = [source for source in (
                    "Post Result to X", "Select Result Layout",
                    "Copy Preview to Clipboard", "Save Preview as Image",
                    "Save player characters", "Autona - Result")
                if f"<source>{source}</source>" in text]
        assert back == [], path.name
        for source in ("Horizontal (1920x1080)", "Sample Tournament",
                       "Result Settings"):
            assert f"<source>{source}</source>" in text, (path.name, source)


def test_the_qt_board_panel_is_gone():
    # ADR-005 S4s: the React page is the only Board panel. Its widget, its
    # preview, the colour cards only it still had and the option panels nothing
    # built may not come back in src/, nor the translations only they used, nor
    # the options' store and action (nothing called them, no overlay read them).
    # (The colour button, which the crew board and the Qt settings window
    # used, went with the latter in S4af: test_the_qt_settings_window_is_gone.)
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    for gone in ("AutonaBoardWidget.py", "AutonaLayoutPreview.py",
                 "AutonaFeatureColors.py", "LayoutOptions",
                 "core/LayoutOptionsManager.py"):
        assert not (src / gone).exists(), gone
    for kept in ("AutonaDesignerWindow.py", "AutonaWebPageLifecycle.py"):
        assert (src / kept).exists(), kept
    words = ("import AutonaBoardWidget", "AutonaBoardWidget(",
             "AutonaLayoutPreview(", "import AutonaLayoutPreview",
             "AutonaFeatureColors import", "MakeFeatureColorCard",
             "LayoutOptions.", "LayoutOptionsManager", "def SetLayoutOption",
             "QFileSystemWatcher")
    left = [str(path.relative_to(PROJECT_ROOT))
            for pattern in ("*.py", "*.ui")
            for path in src.rglob(pattern)
            if any(word in path.read_text(encoding="utf-8") for word in words)]
    assert left == []

    for path in (src / "i18n").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        for name in ("AutonaBoardWidget", "AutonaLayoutPreview",
                     "AutonaFeatureColors", "LayoutOptions"):
            assert name not in text, (path.name, name)
        assert "<name>layout_options" not in text, path.name
        # The sidebar's label is the Window's.
        assert "<source>Board</source>" in text, path.name


def test_the_layout_catalog_has_no_watcher_to_serve():
    # The page reads the list again when it is back (decision 8). Nothing in core
    # lists directories for a QFileSystemWatcher any more.
    from src.core import LayoutCatalog

    assert not hasattr(LayoutCatalog, "WatchDirs")
    assert not hasattr(LayoutCatalog, "Discover")


# The Qt SCORE's modules (ADR-005 S4ac-3): the board widgets, the player
# card and its base, the crew board, the stage tracker, the dock that held
# them and the set selection / station windows. Their .ui files were the
# whole of src/layout.
QT_SCOREBOARD_MODULES = (
    "AutonaScoreboardManager", "AutonaScoreboardWidget",
    "AutonaScoreboardPlayerWidget", "AutonaBasePlayerWidget",
    "AutonaCrewBattleWidget", "AutonaIndividualGameTracker",
    "AutonaSelectSetWindow", "AutonaSelectStationWindow",
)


def _importedModules(path):
    """Every module an import in `path` names, as its last dotted segment:
    `import a.b` and `from a.b import c` give b (and c, which may be a
    module: `from src import AutonaScoreboardManager`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.rsplit(".", 1)[-1]
                         for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.rsplit(".", 1)[-1])
            names.update(alias.name for alias in node.names)
    return names


def _stringConstants(path, docstrings=True):
    """The string constants of a module. `docstrings=False` leaves out the
    docstrings (a module's, class's or function's leading string), which
    record a name without reading it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped = set()
    if not docstrings:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr)                         and isinstance(first.value, ast.Constant)                         and isinstance(first.value.value, str):
                    skipped.add(id(first.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in skipped]


def test_the_qt_scoreboard_panel_and_its_switch_are_gone():
    # ADR-005 S4ac-3: the React page is the only SCORE panel. Neither the
    # Qt board and the windows that went with it, their .ui files, nor the
    # `general.qt_scoreboard_panel` way back to them (the last of the
    # way-back switches: a stale key in a user's settings.json is simply
    # never read) may come back.
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    for name in QT_SCOREBOARD_MODULES:
        assert not (src / f"{name}.py").exists(), name
    assert not (src / "layout").exists()

    # The switch: no table of way-back settings, no reader of the key (as
    # a string anywhere in src -- comments recording that it went are not
    # code -- nor in the React pages).
    assert not hasattr(HostModule, "QT_FALLBACK_SETTINGS")
    assert not hasattr(HostModule, "UsesQtPanel")
    readers = [str(path.relative_to(PROJECT_ROOT))
               for path in sorted(src.rglob("*.py"))
               if any("qt_scoreboard_panel" in value
                      for value in _stringConstants(path))]
    assert readers == []
    web = PROJECT_ROOT / "web" / "src"
    pages = [str(path.relative_to(PROJECT_ROOT))
             for pattern in ("*.js", "*.jsx", "*.ts", "*.tsx")
             for path in web.rglob(pattern)
             if "qt_scoreboard_panel" in path.read_text(encoding="utf-8")]
    assert pages == []

    # Nothing imports what went: the app, its entry point and the tests
    # (the recorders under tests/fixtures run against an older checkout,
    # which still had these modules, and are not scanned).
    scanned = [*sorted(src.rglob("*.py")), PROJECT_ROOT / "main.py",
               *sorted((PROJECT_ROOT / "tests").glob("*.py")),
               *sorted((PROJECT_ROOT / "tests" / "live").glob("*.py"))]
    importers = {str(path.relative_to(PROJECT_ROOT)):
                 sorted(_importedModules(path) & set(QT_SCOREBOARD_MODULES))
                 for path in scanned}
    assert {path: names for path, names in importers.items() if names} == {}

    # The build no longer collects src/layout.
    spec = (PROJECT_ROOT / "dependencies" / "autona.spec").read_text(
        encoding="utf-8")
    assert "src/layout" not in spec


def test_the_game_tracker_and_its_switch_are_gone():
    # ADR-005 S4ac-4 (the owner's go-ahead, 2026-11-11): the per-game
    # tracker went from core, v1 and the settings after its Qt rows went
    # with the Qt SCORE (S4ac-3). None of it may come back: the service,
    # the board's `stages` rows, v1's two routes and the switch
    # `general.disable_individual_game_tracker` (a stale key in a user's
    # settings.json is never read, a stale `score.{n}.stages` in state.json
    # goes with the launch's ResetScoreRoot).
    from src.AutonaApiV1 import api_v1
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    from src.AutonaWebServer import _build_web_app
    from src.core import AppSettings
    from src.core import StateSchema as schema
    from src.core.Actions import AutonaActions
    from src.core.GameAssetCatalog import GameAssetCatalog
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not (src / "core" / "GameTrackerService.py").exists()
    scanned = [*sorted(src.rglob("*.py")), PROJECT_ROOT / "main.py",
               *sorted((PROJECT_ROOT / "tests").glob("*.py")),
               *sorted((PROJECT_ROOT / "tests" / "live").glob("*.py"))]
    importers = [str(path.relative_to(PROJECT_ROOT)) for path in scanned
                 if "GameTrackerService" in _importedModules(path)]
    assert importers == []
    assert not hasattr(AutonaActions, "GameTracker")

    # The rows: no key of the board's, nothing that builds their combos
    # (the asset adapter's models are instance attributes, so the source is
    # read: no definition or attribute of those names).
    assert [key for key in schema.SCORE_BOARD_KEYS
            if key.split(".")[0] == "stages"] == []
    gone = {"StagesPayload", "StageRow", "stageRows", "stageRowsSorted",
            "stageModel", "stageModelWithBlank", "UpdateStageModel",
            "LoadStageImage", "LoadStageImageComplete",
            "_stageIconGeneration"}
    for module in (GameAssetCatalog, AutonaGameAssetManager):
        path = PROJECT_ROOT / module.__module__.replace(".", "/")
        tree = ast.parse(path.with_suffix(".py").read_text(encoding="utf-8"))
        names = {node.name for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        names |= {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
        names |= {node.target.id for node in ast.walk(tree)
                  if isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name)}
        assert sorted(names & gone) == [], module.__module__

    # v1: neither route is served or classified.
    app, _socketio = _build_web_app()
    app.register_blueprint(api_v1)
    rules = [str(rule.rule) for rule in app.url_map.iter_rules()]
    assert [rule for rule in rules if "/stages" in rule] == []
    for endpoint in ("api_v1.get_stages", "api_v1.stage_update"):
        assert endpoint not in WebAccess.ROUTE_ACCESS, endpoint

    # The switch: not a setting, no reader of the key (as a string anywhere
    # in src's code -- comments and docstrings recording that it went do not
    # read it -- nor in the React pages).
    assert "disable_individual_game_tracker" not in [
        setting.name for setting in AppSettings.SETTINGS]
    readers = [str(path.relative_to(PROJECT_ROOT))
               for path in sorted(src.rglob("*.py"))
               if any("individual_game_tracker" in value
                      for value in _stringConstants(path, docstrings=False))]
    assert readers == []
    web = PROJECT_ROOT / "web" / "src"
    pages = [str(path.relative_to(PROJECT_ROOT))
             for pattern in ("*.js", "*.jsx", "*.ts", "*.tsx")
             for path in web.rglob(pattern)
             if "individual_game_tracker" in path.read_text(encoding="utf-8")]
    assert pages == []


def test_the_qt_score_s_leftovers_are_gone():
    # ADR-005 S4ad: what lost its last consumer with the Qt SCORE (S4ac-3,
    # its acceptance review's leftovers) went: the player-name completers'
    # proxy over the player DB's model, the set follow's notice to the Qt
    # board's timer row (core's emit and the Qt signal), the panels' dock
    # QSS and the typed-text rule of the Qt fields. (The Qt models
    # themselves -- the player DB's, the asset adapter's, the countries' --
    # are a later stage's.)
    from src import AutonaPlayerDB as playerDb
    from src import AutonaSetFollowUtil as followUtil
    from src.Helpers import AutonaQtHelper
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not hasattr(playerDb.AutonaPlayerDB, "completerModel")
    assert not hasattr(playerDb.AutonaPlayerDB, "GetCompleterModel")
    assert not hasattr(playerDb, "_TournamentEntrantsProxy")
    assert not hasattr(followUtil.AutonaSetFollowUtil, "FollowChanged")
    assert not hasattr(followUtil._FollowSignals, "followChanged")
    calls = [node.attr for node in ast.walk(ast.parse(
                 (src / "core" / "SetFollowService.py").read_text(
                     encoding="utf-8")))
             if isinstance(node, ast.Attribute)
             and node.attr == "FollowChanged"]
    assert calls == []
    assert not hasattr(AutonaQtHelper, "HeldBackText")
    assert not (PROJECT_ROOT / "tests" / "test_held_back_text.py").exists()
    assert [value for value in _stringConstants(
                src / "ui_constants.py", docstrings=False)
            if "QDockWidget" in value] == []


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
        WebPanelPages().Add(1, "lazyQt")


class _WindowRig:
    """The Window's real page-switching methods (read out of src/Autona.py,
    which cannot be imported: agent-pitfalls #1) on a stack built the way
    _BuildUi builds it, with a few sidebar items: six React ones and a
    made-up lazy Qt one (the sidebar has no Qt item left since S4aa, and the
    factories' path for a Qt panel is still there), in the real order, and
    the Settings page after them (S4af). Off screen, so the host builds no
    view; what it was asked to show is its _panel. The sidebar is a stand-in
    (_FakeSidebar) that choose() highlights as a click does."""

    METHODS = ("_OnNavSelected", "_OnStackPageChanged",
               "_EnsurePanelAt", "_WebPanel", "_AdoptWebPanelHost",
               "_CreateEventPanel", "_CreateStreamPanel",
               "_CreateScoreboardPanel", "_CreateTop8Panel",
               "_CreateCommentaryPanel", "_CreateStatsPanel",
               "OpenScoreSets", "OpenSettings", "_NavToRemember",
               "_CreateSettingsPanel")

    def __init__(self):
        import ast

        from loguru import logger

        tree = ast.parse(_autonaSource())
        window = ast.parse("class Window:\n    pass\n").body[0]
        window.body = [node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name in self.METHODS]
        assert sorted(node.name for node in window.body) == sorted(
            self.METHODS)
        window.body.append(ast.parse(
            "def _ApplyTabBarPolicy(self, panel):\n    pass\n").body[0])

        namespace = {"QWidget": QWidget, "logger": logger,
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
        w._lazyPanels = {}
        w._panelIndexes = {}
        w._webPanelHost = None
        w._webPanelPages = WebPanelPages()
        w._buildingPanel = False
        w._shuttingDown = False
        # As _BuildUi: event, stream, scoreboard, ..., top8, commentary, ...,
        # stats. (The lazy Qt item is last here so the indexes below stay one
        # per kind of item.)
        for page, key in ((w._CreateEventPanel, "event"),
                          (w._CreateStreamPanel, "stream"),
                          (w._CreateScoreboardPanel, "scoreboard"),
                          (w._CreateTop8Panel, "top8"),
                          (w._CreateCommentaryPanel, "commentary"),
                          (w._CreateStatsPanel, "stats"),
                          (qtPanel("lazyQt"), "lazyQt")):
            index = w.stack.addWidget(QWidget())
            w._lazyPanels[index] = (page, key)
            w._panelIndexes[key] = index
            if WebAccess.IsWebPanel(key):
                w._webPanelPages.Add(index, key)
        # As _BuildUi: the Settings page after the items, no item's.
        index = w.stack.addWidget(QWidget())
        w._lazyPanels[index] = (w._CreateSettingsPanel, "settings")
        w._panelIndexes["settings"] = index
        w._webPanelPages.Add(index, "settings")
        w._navBeforeSettings = 0
        w.sidebar = _FakeSidebar()
        w.stack.currentChanged.connect(w._OnStackPageChanged)
        self.placeholders = [w.stack.widget(index)
                             for index in range(w.stack.count())]

    def choose(self, nav):
        # What a sidebar click does (the item highlighted, navSelected ->
        # _OnNavSelected).
        if isinstance(self.window.sidebar, _FakeSidebar):
            self.window.sidebar.setCurrentIndex(nav)
        self.window._OnNavSelected(nav)
        return self.window.stack.currentIndex()

    def hosts(self):
        return [index for index in range(self.window.stack.count())
                if isinstance(self.window.stack.widget(index),
                              AutonaWebPanelHost)]


class _FakeSidebar:
    """AutonaSidebar's highlight: the item checked, -1 for none."""

    def __init__(self):
        self.current = -1

    def currentIndex(self):
        return self.current

    def setCurrentIndex(self, nav):
        self.current = nav

    def clearCurrent(self):
        self.current = -1


EVENT, STREAM, SCOREBOARD, TOP8, COMMENTARY, STATS, LAZY_QT, SETTINGS = range(8)


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
    assert rig.choose(LAZY_QT) == LAZY_QT
    assert rig.choose(STREAM) == STREAM
    assert host._panel == "stream"
    assert rig.choose(LAZY_QT) == LAZY_QT
    assert rig.choose(COMMENTARY) == STREAM
    assert host._panel == "commentary"
    assert w._webPanelHost is host and rig.hosts() == [STREAM]
    # The SCORE page too (S4aa), its placeholder never built.
    assert rig.choose(SCOREBOARD) == STREAM
    assert host._panel == "scoreboard"
    assert w.stack.widget(SCOREBOARD) is rig.placeholders[SCOREBOARD]
    assert SCOREBOARD not in w._lazyPanels
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
    # The Qt item was built once.
    assert rig.choose(LAZY_QT) == LAZY_QT
    assert rig.built == ["lazyQt"]


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

    def failing(page):
        raise RuntimeError("injected")

    # The host is made (the factory returned), then the swap fails before
    # the stack changes.
    w.stack.removeWidget = failing
    assert rig.choose(STREAM) == STREAM
    assert w._webPanelHost is None
    assert w._webPanelPages.hostIndex is None
    assert COMMENTARY in w._lazyPanels
    assert rig.hosts() == []
    assert w.stack.widget(STREAM) is rig.placeholders[STREAM]

    del w.stack.removeWidget
    assert rig.choose(COMMENTARY) == COMMENTARY
    assert rig.hosts() == [COMMENTARY]
    assert w._webPanelHost._panel == "commentary"
    # The item whose build failed shows the host from then on.
    assert rig.choose(STREAM) == COMMENTARY
    assert w._webPanelHost._panel == "stream"
    assert rig.hosts() == [COMMENTARY]


# ── SCORE, a React item like the others (ADR-005 S4aa / S4ac-3) ─────────

def test_the_scoreboard_item_opened_first_builds_the_host_on_its_page(qapp):
    rig = _WindowRig()
    w = rig.window

    assert rig.choose(SCOREBOARD) == SCOREBOARD
    assert rig.hosts() == [SCOREBOARD]
    assert w._webPanelHost._panel == "scoreboard"
    assert STREAM not in w._lazyPanels
    # The other React items show it from then on, and SCORE its panel again.
    assert rig.choose(STREAM) == SCOREBOARD
    assert w._webPanelHost._panel == "stream"
    assert rig.choose(SCOREBOARD) == SCOREBOARD
    assert w._webPanelHost._panel == "scoreboard"


def test_a_host_built_on_another_item_takes_the_scoreboard_item_with_it(qapp):
    # No way back to settle on the first choice since S4ac-3: SCORE's
    # placeholder leaves _lazyPanels with the other React items' when the
    # host is built (it used to wait for the item's first choice).
    rig = _WindowRig()
    w = rig.window
    rig.choose(STREAM)
    assert SCOREBOARD not in w._lazyPanels

    assert rig.choose(SCOREBOARD) == STREAM
    assert w._webPanelHost._panel == "scoreboard"
    assert w.stack.widget(SCOREBOARD) is rig.placeholders[SCOREBOARD]
    assert rig.hosts() == [STREAM]


# ── SCORE's set selection asked for from outside (ADR-005 S4ab-2) ───────

def _askingScoreSets(rig, monkeypatch):
    """What OpenScoreSets does, in order: the window brought to the front,
    the sidebar's item, the host asked (with the panel it shows then)."""
    order = []
    w = rig.window
    w._BringToFront = lambda: order.append("front")
    w.sidebar = SimpleNamespace(
        setCurrentIndex=lambda nav: order.append(("sidebar", nav)))
    monkeypatch.setattr(
        AutonaWebPanelHost, "OpenScoreSets",
        lambda host, board: order.append(("asked", host._panel, board)))
    return order


def test_the_set_selection_asked_for_shows_score_and_asks_its_page(
        qapp, monkeypatch):
    rig = _WindowRig()
    w = rig.window
    order = _askingScoreSets(rig, monkeypatch)
    rig.choose(STREAM)

    w.OpenScoreSets(2)

    # As a click on SCORE (the same host page, told SCORE), then the host.
    assert order == ["front", ("sidebar", SCOREBOARD),
                     ("asked", "scoreboard", 2)]
    assert w.stack.currentIndex() == STREAM
    assert rig.hosts() == [STREAM]
    assert SCOREBOARD not in w._lazyPanels


def test_the_set_selection_asked_for_first_builds_the_host_on_score(
        qapp, monkeypatch):
    # The hotkey before any React item was opened (EVENT restored on a Qt
    # item): the host is built on SCORE's page, then asked.
    rig = _WindowRig()
    w = rig.window
    order = _askingScoreSets(rig, monkeypatch)
    rig.choose(LAZY_QT)

    w.OpenScoreSets(1)

    assert order == ["front", ("sidebar", SCOREBOARD),
                     ("asked", "scoreboard", 1)]
    assert w.stack.currentIndex() == SCOREBOARD
    assert rig.hosts() == [SCOREBOARD]


def test_the_set_selection_asked_for_without_a_host_asks_nothing(
        qapp, monkeypatch):
    # The host failed to be built (logged by _EnsurePanelAt): no page to ask,
    # and nothing raised to the hotkey or the route.
    rig = _WindowRig()
    w = rig.window
    order = _askingScoreSets(rig, monkeypatch)

    def failing(page):
        raise RuntimeError("injected")

    w.stack.removeWidget = failing

    w.OpenScoreSets(1)

    assert order == ["front", ("sidebar", SCOREBOARD)]
    assert w._webPanelHost is None


def test_the_set_selection_asked_for_while_the_window_closes_does_nothing(
        qapp, monkeypatch):
    # closeEvent has begun the teardown: not shown again, no host built for
    # the request.
    rig = _WindowRig()
    w = rig.window
    order = _askingScoreSets(rig, monkeypatch)
    rig.choose(LAZY_QT)
    w._shuttingDown = True

    w.OpenScoreSets(1)

    assert order == []
    assert w.stack.currentIndex() == LAZY_QT
    assert rig.hosts() == []


# ── the Settings page (ADR-005 S4af) ────────────────────────────────────

def _askingSettings(rig):
    """What OpenSettings does to the window: brought to the front."""
    order = []
    rig.window._BringToFront = lambda: order.append("front")
    return order


def test_the_settings_page_shows_the_host_with_no_item_highlighted(qapp):
    rig = _WindowRig()
    w = rig.window
    order = _askingSettings(rig)
    rig.choose(STREAM)
    host = w._webPanelHost

    w.OpenSettings()

    # The same host page, told the Settings panel; no item highlighted.
    assert order == ["front"]
    assert host._panel == "settings"
    assert w.stack.currentIndex() == STREAM
    assert rig.hosts() == [STREAM]
    assert w.stack.widget(SETTINGS) is rig.placeholders[SETTINGS]
    assert SETTINGS not in w._lazyPanels
    assert w.sidebar.currentIndex() == -1
    # The window reopens on the item chosen before.
    assert w._NavToRemember() == STREAM

    # An item chosen leaves it, and is the one to reopen on.
    assert rig.choose(COMMENTARY) == STREAM
    assert host._panel == "commentary"
    assert w._NavToRemember() == COMMENTARY


def test_the_settings_page_opened_first_builds_the_host_on_its_page(qapp):
    rig = _WindowRig()
    w = rig.window
    _askingSettings(rig)
    rig.choose(LAZY_QT)

    w.OpenSettings()

    assert w.stack.currentIndex() == SETTINGS
    assert rig.hosts() == [SETTINGS]
    assert w._webPanelHost._panel == "settings"
    assert w._NavToRemember() == LAZY_QT
    # The React items show it from then on.
    assert STREAM not in w._lazyPanels
    assert rig.choose(STREAM) == SETTINGS
    assert w._webPanelHost._panel == "stream"
    assert rig.choose(LAZY_QT) == LAZY_QT
    assert rig.built == ["lazyQt"]


def test_opening_the_settings_page_again_keeps_the_item_before_it(qapp):
    # The second request (the menu while it shows): no item is highlighted
    # then, so the one chosen before stays the one to reopen on.
    rig = _WindowRig()
    w = rig.window
    _askingSettings(rig)
    rig.choose(TOP8)
    w.OpenSettings()
    asked = []
    w._webPanelHost.ShowPanel = asked.append

    w.OpenSettings()

    assert asked == ["settings"]
    assert w._NavToRemember() == TOP8


def test_the_settings_page_asked_for_while_the_window_closes_does_nothing(qapp):
    rig = _WindowRig()
    w = rig.window
    order = _askingSettings(rig)
    rig.choose(LAZY_QT)
    w._shuttingDown = True

    w.OpenSettings()

    assert order == []
    assert w.stack.currentIndex() == LAZY_QT
    assert w.sidebar.currentIndex() == LAZY_QT
    assert rig.hosts() == []


def test_the_sidebar_highlights_no_item_and_then_the_next_chosen(qapp):
    # AutonaSidebar.clearCurrent: an exclusive group never unchecks its
    # checked button; a click afterwards checks one item as usual.
    from qtpy.QtGui import QIcon

    from src.AutonaSidebar import AutonaSidebar

    sidebar = AutonaSidebar()
    try:
        for title in ("A", "B", "C"):
            sidebar.addNavItem(QIcon(), title)
        sidebar.setCurrentIndex(1)

        sidebar.clearCurrent()
        assert sidebar.currentIndex() == -1
        sidebar.clearCurrent()
        assert sidebar.currentIndex() == -1

        sidebar._navButtons[2].click()
        assert sidebar.currentIndex() == 2
        sidebar._navButtons[0].click()
        assert [button.isChecked() for button in sidebar._navButtons] == [
            True, False, False]
    finally:
        sidebar.deleteLater()


def test_the_settings_page_is_no_sidebar_item_to_reopen_on():
    # The window remembers an item, never the Settings page (closeEvent
    # saves _NavToRemember), and restores an item only.
    tree = ast.parse(_autonaSource())
    close = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "closeEvent")
    assert any(isinstance(node, ast.Attribute)
               and node.attr == "_NavToRemember" for node in ast.walk(close))
    assert 'savedIndex < self._panelIndexes["settings"]' in _autonaSource()
    assert "settings" in WebAccess.WEB_PANELS


# The Qt settings window's modules (ADR-005 S4af) and the colour button only
# it used.
QT_SETTINGS_MODULES = ("AutonaSettingsWindow", "SettingsWidget",
                       "AutonaColorButton")
# What went with them: the window's accessor and handle, the key recorder,
# the hotkeys' switch hook only the window called.
QT_SETTINGS_NAMES = ("AutonaSettingsWindow", "SettingsWidget",
                     "AutonaColorButton", "EnsureSettingsWindow",
                     "_settingsWindow", "HotkeyCaptureEdit",
                     "ApplyEnabledSetting", "_HotkeyWarningText")
# The window's translation contexts (both .ts files).
QT_SETTINGS_CONTEXTS = (
    "settings", "settings.bsky", "settings.disable_autoupdate",
    "settings.disable_export", "settings.disable_overwrite",
    "settings.disable_scoreupdate", "settings.disable_thumbnail_widget",
    "settings.general", "settings.webserver_listen_lan", "settings.hotkeys",
    "settings.team_1_default_color", "settings.team_2_default_color")


def _codeNames(path):
    """The identifiers a module's code uses: names, attributes, definitions
    and imports (comments and docstrings that record a name do not)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.update(node.name.split("."))
    return names


# What the Qt model cleanup (ADR-005, PR1) removed: the provider adapter's set /
# station loads and signals (their only consumers, the Qt set selection and
# station windows, went in S4ac-3), and the proxy model that stripped icons for
# the Qt combos.
QT_MODEL_CLEANUP_NAMES = (
    "LoadSets", "CancelSetLoad", "_OnSetsProgress", "_OnSetsLoaded",
    "LoadStations", "_OnStationsLoaded", "_setLoadGeneration",
    "_setLoadCancel", "get_sets_finished", "get_stations_finished",
    "sets_data_updated", "twitch_username_updated", "NoIconProxyModel",
    # PR2: the country model adapter (the catalog is read directly)
    "AutonaCountryHelper", "countriesUpdated", "countriesAboutToReload",
    "LoadCountries",
    # PR3: the player DB's Qt model (its build's mains normalisation is
    # core's PlayerDB.NormaliseMains; `model` itself is too common a name)
    "EnableModel", "modelEnabled", "_BuildModel", "modelLock",
    "_retiredModel")

# PR4: the asset adapter's Qt models and their icons (the React pages read
# the catalog through the web operations; no combo was left). Its own names,
# so looked for where they lived and were used -- the adapter and the
# modules that import it -- not in all of src: an unrelated module may well
# have an `_iconCache` or a `_HasGame` of its own.
QT_ASSET_MODEL_NAMES = (
    "characterModel", "skinModels", "variantModel", "colorModel",
    "_retiredModels", "_modelGeneration", "_RebuildModels",
    "UpdateCharacterModel", "UpdateSkinModel", "UpdateVariantModel",
    "UpdateColorModel", "_HasGame", "_DecodeIcons", "LoadSkinImages",
    "LoadSkinImagesComplete", "_skinIconGeneration", "_WaitForDecodes",
    "GetStockIcon", "_StockIcon", "_iconCache", "_iconCacheGeneration",
    "GetCharacterAssets")

# PR4: the asset adapter's properties that read the catalog for the Qt side
# (common words: looked for as the adapter's attributes only).
QT_ASSET_MODEL_PROPERTIES = (
    "characters", "skins", "variants", "colors", "biggestCompletePack")

# PR4: what the asset adapter must not build or name any more -- the QtGui
# types of its models and icons.
QT_GUI_MODEL_TYPES = (
    "QStandardItemModel", "QStandardItem", "QIcon", "QPixmap", "QImage",
    "QColor")


def _importsTheAssetAdapter(tree):
    """Whether a module imports src/AutonaGameAssetManager.py (`from
    .AutonaGameAssetManager import ...`, `from . import
    AutonaGameAssetManager`, `import src.AutonaGameAssetManager`)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "AutonaGameAssetManager":
                return True
            if any(alias.name == "AutonaGameAssetManager"
                   for alias in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.split(".")[-1] == "AutonaGameAssetManager"
                   for alias in node.names):
                return True
    return False


def test_the_unused_qt_model_leftovers_are_gone():
    import inspect

    from src.core.PlayerService import PlayerService
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    users = {str(path.relative_to(PROJECT_ROOT)):
             sorted(_codeNames(path) & set(QT_MODEL_CLEANUP_NAMES))
             for path in sorted(src.rglob("*.py"))}
    assert {path: names for path, names in users.items() if names} == {}

    # The statistics are asked for by an explicit Announce, not by a flag.
    for method in (PlayerService.SetSeed, PlayerService.SetPlayerId):
        assert "announce" not in inspect.signature(method).parameters

    # PR3: the player DB adapter holds no Qt model, and needs no QtGui.
    from src.AutonaPlayerDB import AutonaPlayerDB
    assert not hasattr(AutonaPlayerDB, "model")
    tree = ast.parse((src / "AutonaPlayerDB.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)}
    assert not imported & {"qtpy.QtGui", "qtpy.QtWidgets",
                           "AutonaGameAssetManager"}, imported

    # PR4: the asset adapter holds no Qt model, builds no icon and needs no
    # QtGui (nor PIL, which decoded the icons): QtCore's QObject / Signal
    # for its signals, and the loads.
    from src.AutonaGameAssetManager import AutonaGameAssetManager
    adapter = src / "AutonaGameAssetManager.py"
    tree = ast.parse(adapter.read_text(encoding="utf-8"))
    # Its names, in it and in the modules that import it.
    scope = {str(path.relative_to(PROJECT_ROOT)): path
             for path in sorted(src.rglob("*.py"))
             if path == adapter or _importsTheAssetAdapter(
                 ast.parse(path.read_text(encoding="utf-8")))}
    # (Its importers today: the window, the asset guide, the composition
    # root. A detection that found none would check nothing.)
    assert {"src/AutonaGameAssetManager.py", "src/Autona.py",
            "src/bootstrap.py"} <= {key.replace("\\", "/") for key in scope}
    users = {key: sorted(_codeNames(path) & set(QT_ASSET_MODEL_NAMES))
             for key, path in scope.items()}
    assert {key: names for key, names in users.items() if names} == {}
    # Its properties: neither on the class nor set on an instance
    # (`self.characters = ...`) anywhere in it.
    for name in QT_ASSET_MODEL_PROPERTIES:
        assert not hasattr(AutonaGameAssetManager, name), name
    assigned = sorted(
        part.attr for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        for target in (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
        for part in ast.walk(target)  # a, self.x = ...: the tuple's parts
        if isinstance(part, ast.Attribute)
        and isinstance(part.value, ast.Name) and part.value.id == "self"
        and part.attr in QT_ASSET_MODEL_PROPERTIES)
    assert assigned == [], assigned
    assert list(inspect.signature(AutonaGameAssetManager._Publish)
                .parameters) == ["self", "snapshot", "cycle"]
    imported = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree)
                 if isinstance(node, ast.Import) for alias in node.names}
    assert not imported & {"qtpy.QtGui", "qtpy.QtWidgets", "PIL"}, imported
    assert _codeNames(adapter) & set(QT_GUI_MODEL_TYPES) == set()
    # A load that is not announced is settled on one path only: the
    # shutdown's (STOPPED, in _Load). _Publish settled a failed model
    # rebuild (FAILED) too, until the models went. Every call of the name
    # counts, whatever it is called on (self, the instance, a local).
    settlers = sorted(
        function.name for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (isinstance(node.func, ast.Attribute)
             and node.func.attr == "_SettleUnannounced"
             or isinstance(node.func, ast.Name)
             and node.func.id == "_SettleUnannounced"))
    assert settlers == ["_Load"], settlers


# ADR-005 S-final SF-1: what the dead-code sweep removed. The Alert
# notification (nothing ever emitted its `alerts` signal), `ui_layout`
# (no caller left), the combo helper `ShowComboText`, and the catalog's
# snapshot fields / readers whose only consumer was the Qt models (gone in
# the Qt model clean-up's PR4).
SF1_REMOVED_MODULES = ("AutonaAlertNotification", "ui_layout")
SF1_REMOVED_NAMES = (
    "AutonaAlertNotification", "ShowComboText", "colorRows",
    "stockIconPaths", "biggestCompletePack", "PresetColor", "StockIconPath")
# Translation contexts of the deleted .ui files / widgets.
SF1_REMOVED_TS_CONTEXTS = ("Form", "GroupBox", "DockWidget", "notes")


def test_the_sf1_dead_code_is_gone():
    import xml.etree.ElementTree as ET

    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    for module in SF1_REMOVED_MODULES:
        assert not (src / f"{module}.py").exists(), module
    users = {str(path.relative_to(PROJECT_ROOT)):
             sorted(_codeNames(path) & set(SF1_REMOVED_NAMES))
             for path in sorted(src.rglob("*.py"))}
    assert {path: names for path, names in users.items() if names} == {}

    for ts in sorted((src / "i18n").glob("Autona_*.ts")):
        contexts = {node.findtext("name")
                    for node in ET.parse(ts).getroot().findall("context")}
        assert contexts.isdisjoint(SF1_REMOVED_TS_CONTEXTS), (
            ts.name, contexts & set(SF1_REMOVED_TS_CONTEXTS))


def test_the_qt_settings_window_is_gone():
    # ADR-005 S4af (the owner's go-ahead: no way back): the React page
    # (/web/settings, the menu's Window.OpenSettings) is the only settings
    # UI. Neither the Qt window, its rows' widget, the colour button only it
    # used, the key recorder, the hotkeys' switch hook nor the window's
    # translations may come back.
    import re

    from src import AutonaHotkeys
    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    assert not (src / "Settings").exists()
    assert not (src / "AutonaColorButton.py").exists()
    assert not hasattr(AutonaHotkeys.AutonaHotkeys, "ApplyEnabledSetting")

    # Nothing imports or names what went: the app, its entry point and the
    # tests (the recorders under tests/fixtures run against an older
    # checkout, and are not scanned).
    scanned = [*sorted(src.rglob("*.py")), PROJECT_ROOT / "main.py",
               *sorted((PROJECT_ROOT / "tests").glob("*.py")),
               *sorted((PROJECT_ROOT / "tests" / "live").glob("*.py"))]
    importers = {str(path.relative_to(PROJECT_ROOT)):
                 sorted(_importedModules(path) & set(QT_SETTINGS_MODULES))
                 for path in scanned}
    assert {path: names for path, names in importers.items() if names} == {}
    users = {str(path.relative_to(PROJECT_ROOT)):
             sorted(_codeNames(path) & set(QT_SETTINGS_NAMES))
             for path in sorted(src.rglob("*.py"))}
    assert {path: names for path, names in users.items() if names} == {}

    # The translations: none of the window's contexts, nothing located in
    # its files. The menu's "Settings" (the app context) stays.
    for name in ("Autona_en.ts", "Autona_ja.ts"):
        text = (src / "i18n" / name).read_text(encoding="utf-8")
        contexts = set(re.findall(r"<name>([^<]+)</name>", text))
        assert contexts & set(QT_SETTINGS_CONTEXTS) == set(), name
        assert "Settings\\" not in text, name
        assert "AutonaColorButton" not in text, name
        assert "<source>Settings</source>" in text, name

    # The build collects nothing of it.
    spec = (PROJECT_ROOT / "dependencies" / "autona.spec").read_text(
        encoding="utf-8")
    assert "src/Settings" not in spec and "src\\Settings" not in spec


@pytest.mark.parametrize("state", ["minimized", "maximized-then-minimized",
                                   "behind"])
def test_bringing_the_window_to_the_front_restores_it(qapp, state):
    import ast

    from qtpy.QtWidgets import QMainWindow

    method = _windowMethod("_BringToFront")
    cls = ast.parse("class Front(QMainWindow):\n    pass\n").body[0]
    cls.body = [method]
    namespace = {"QMainWindow": QMainWindow, "Qt": Qt}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[cls], type_ignores=[])), "src/Autona.py", "exec"), namespace)
    window = namespace["Front"]()
    try:
        window.show()
        if state == "minimized":
            window.showMinimized()
        elif state == "maximized-then-minimized":
            window.showMaximized()
            window.setWindowState(window.windowState()
                                  | Qt.WindowState.WindowMinimized)
        assert _waitUntil(lambda: window.isVisible(), timeout=5)
        assert window.isMinimized() is (state != "behind")

        window._BringToFront()

        assert not window.isMinimized()
        assert window.isVisible()
        assert window.isMaximized() is (state == "maximized-then-minimized")
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
