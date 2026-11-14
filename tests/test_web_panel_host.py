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
import functools
import json
import logging
import socket
import threading
import time
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, Qt, QTimer, QUrl
from qtpy.QtGui import QGuiApplication
from qtpy.QtTest import QTest
from qtpy.QtWebEngineCore import (
    QWebEnginePage, QWebEnginePermission, QWebEngineProfile, QWebEngineSettings)
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import (
    QDialog, QLabel, QLineEdit, QStackedWidget, QVBoxLayout, QWidget)

from src import AutonaWebPageLifecycle as WPL
from src import AutonaWebPanelHost as HostModule
from src.AutonaWebPanelHost import (
    CLIPBOARD_PERMISSION, AutonaWebPanelHost, ClipboardPermitted, PanelPath,
    WebPanelPages)
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


def _pageJs(page, expr):
    # Only on an Active page: a frozen one answers after it thaws. The value
    # is round-tripped through JSON: PySide6 hands null, arrays and objects
    # back as ''.
    box = []
    page.runJavaScript(
        "(function(){try{var v=(" + expr + ");"
        "return JSON.stringify({value:v===undefined?null:v});}"
        "catch(e){return JSON.stringify({error:String(e)});}})()",
        0, box.append)
    assert _waitUntil(lambda: box), f"no reply to {expr!r}"
    reply = json.loads(box[0])
    assert "error" not in reply, f"{expr!r} threw {reply['error']}"
    return reply["value"]


# Every request the two servers below took, with the shell's header
# (ADR-005 S-final SF-4a) and the headers the server's guard reads with it
# (src/AutonaWebServer.py's _authorize: Sec-Fetch-Site, Host, Origin; None
# when absent). "panel" is the shared view's server (_ServerUrl points at
# it), "frame" another origin's (the design previews' stand-in).
Seen = namedtuple("Seen", "server method path shell site host origin")
SEEN = []


def _record(server, handler):
    headers = handler.headers
    SEEN.append(Seen(server, handler.command, handler.path,
                     headers.get(WebAccess.SHELL_HEADER),
                     headers.get("Sec-Fetch-Site"), headers.get("Host"),
                     headers.get("Origin")))


# The panel server hands the access token's routes to the real app when a
# test puts its Flask test client here (test_the_guard_takes_the_shared_
# views_fetch_as_the_shells): the engine's own request, through the real
# _guard_http_request.
RELAY = {}
RELAY_PREFIX = "/api/v1/access-token"


def _relayed(handler, body=b""):
    client = RELAY.get("client")
    if client is None or not handler.path.startswith(RELAY_PREFIX):
        return False
    port = handler.server.server_address[1]
    headers = [(name, value) for name, value in handler.headers.items()
               if name.lower() not in ("host", "content-length")]
    # As werkzeug's server makes the environ: SERVER_PORT the port it
    # listens on, the Host header as the client sent it.
    answer = client.open(
        handler.path, method=handler.command, headers=headers, data=body,
        environ_overrides={"REMOTE_ADDR": handler.client_address[0],
                           "SERVER_PORT": str(port),
                           "HTTP_HOST": handler.headers.get("Host", "")})
    payload = answer.get_data()
    handler.send_response(answer.status_code)
    handler.send_header("Content-Type", answer.headers.get(
        "Content-Type", "application/json"))
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
    return True


# What the panel server redirects (SF-4a: the header goes along with a
# redirect, so the shell blocks a hop away from /api/v1/).
REDIRECT_AWAY = "/api/v1/redirect-away?to="
REDIRECT_HERE = "/api/v1/redirect-here"


@pytest.fixture(scope="module")
def panelServer():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _record("panel", self)
            if _relayed(self) or self._Redirected():
                return
            if not self.path.startswith("/favicon"):
                requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            _record("panel", self)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if _relayed(self, body):
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def _Redirected(self):
            if self.path.startswith(REDIRECT_AWAY):
                location = unquote(self.path[len(REDIRECT_AWAY):])
            elif self.path == REDIRECT_HERE:
                location = "/api/v1/landing-here"
            else:
                return False
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

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
        return _pageJs(self.page, expr)

    def state(self):
        return self.page.lifecycleState()


@pytest.fixture
def buildHost(qapp, panelServer, monkeypatch):
    port, requests = panelServer
    requests.clear()
    SEEN.clear()
    # Each test sees the interceptor's first failure of a kind as a warning.
    monkeypatch.setattr(HostModule, "_shellFailures",
                        WebAccess.RefusalLog(limit=16))
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


def test_the_page_does_not_leave_this_server(buildHost, refusedPort):
    # What the clipboard permission rests on (S4e review; granted to the
    # page's own origin since SF-8b, below): a
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


def test_the_page_does_not_leave_the_web_ui(buildHost):
    # The review of SF-8b: this server's own origin serves third parties'
    # layouts too (layout/), and a page there would be granted the clipboard
    # as the panels are. The main frame stays in /web: script, a link -- or a
    # link dropped on the view -- to the rest of the origin is refused, and
    # the server never sees it.
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    served = len(rig.requests)
    elsewhere = ("/layout/scoreboard/index.html", "/", "/api/v1/health",
                 "/out/program_state.json", "/webx", "/web/../layout/a.html",
                 "/web/%2e%2e/layout/a.html")

    for path in elsewhere:
        rig.js(f"void (location.href = {json.dumps(path)})")
    rig.js("(function(u){var a=document.createElement('a');a.href=u;"
           "document.body.appendChild(a);a.click();})("
           "'/layout/scoreboard/index.html')")

    _waitUntil(lambda: False, timeout=1.0)
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")
    assert rig.js("window.__loadId") == document
    assert len(rig.requests) == served
    assert rig.page.refusedLast
    assert rig.host._loaded

    # What acceptNavigationRequest says, for each kind: off the Web UI never,
    # in it on this server's origin always, on any other origin never -- not
    # even for the host's own kinds (load(), the reload of a discarded page):
    # a link dropped on the view arrives as Typed too (SF-8b's second review).
    port = rig.servingPort
    N = QWebEnginePage.NavigationType
    pageKinds = (N.NavigationTypeLinkClicked, N.NavigationTypeOther,
                 N.NavigationTypeRedirect, N.NavigationTypeFormSubmitted,
                 N.NavigationTypeBackForward)
    hostKinds = (N.NavigationTypeTyped, N.NavigationTypeReload)
    for kind in pageKinds + hostKinds:
        for path in elsewhere + ("/web/%2e%2e%2flayout/a.html",):
            assert rig.page.acceptNavigationRequest(
                QUrl(f"http://127.0.0.1:{port}{path}"), kind, True) is False, (
                    path, kind)
        for path in ("/web", "/web/second?desktop=1&lng=ja", "/web/board#x"):
            assert rig.page.acceptNavigationRequest(
                QUrl(f"http://127.0.0.1:{port}{path}"), kind, True) is True, (
                    path, kind)
        for url in (f"http://127.0.0.1:{port + 1}/web/second",
                    "http://evil.example/web/x",
                    f"http://evil.example:{port}/web/x",
                    f"http://localhost:{port}/web/x",
                    f"https://127.0.0.1:{port}/web/x",
                    "file:///C:/web/x.html"):
            assert rig.page.acceptNavigationRequest(
                QUrl(url), kind, True) is False, (url, kind)


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
if (q.get('clip')) {
  document.addEventListener('click', function () {
    var asked = q.get('clip') === 'read' ? navigator.clipboard.readText()
      : navigator.clipboard.writeText('from the frame');
    asked.then(function () { parent.postMessage('clip ok', '*'); },
               function (e) { parent.postMessage('clip ' + e.name, '*'); });
  });
}
if (q.get('fetch')) { fetch(q.get('fetch'), {mode: 'no-cors'}); }
</script></body></html>"""


@pytest.fixture(scope="module")
def frameServer():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _record("frame", self)
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


# ── the clipboard: the page's own origin only (ADR-005 S-final SF-8b) ───
# The panels copy with navigator.clipboard (the Stream / thumbnail images,
# the OBS URLs, the post on X's image). No setting hands it to every frame's
# script (JavascriptCanAccessClipboard did until SF-8b, the design previews'
# included); the page grants Qt's ClipboardReadWrite per request to its own
# origin while it is in the Web UI (/web), and the (default) profile keeps no
# grant, so that none reaches the Designer's window, a view of the same origin
# on the same profile. (The off-screen renderers are on it too, but load
# file:// pages: another origin.)

SERVER = "http://127.0.0.1:5107/"


def _permitted(kind, origin, pages, server=SERVER):
    return ClipboardPermitted(kind, QUrl(origin), tuple(map(QUrl, pages)),
                              QUrl(server))


def test_the_clipboard_is_granted_to_the_pages_own_origin_only():
    page = "http://127.0.0.1:5107"
    pages = (f"{page}/web/board?desktop=1&lng=ja", "about:blank")
    granted = (page, page + "/", f"{page}/layout/a.html",
               "HTTP://127.0.0.1:5107/", "http://x@127.0.0.1:5107/",
               "http://127.0.0.1:5107/web/board")
    refused = (
        "http://127.0.0.1:61234/",    # the preview listener's port
        "http://127.0.0.1:5108/",     # another port
        "http://127.0.0.1/",          # http's default port
        "http://localhost:5107/",     # another name for this PC
        "http://[::1]:5107/",
        "http://192.0.2.10:5107/",    # a LAN address of this PC's
        "https://127.0.0.1:5107/",    # another scheme
        "https://example.com/",
        "about:blank", "about:srcdoc", "data:text/html,a",
        "file:///C:/a.html", "qrc:/a.html", "chrome-error://chromewebdata/",
        "", "not a url", "http://127.0.0.1:99999/")

    for origin in granted:
        assert _permitted(CLIPBOARD_PERMISSION, origin, pages), origin
    for origin in refused:
        assert not _permitted(CLIPBOARD_PERMISSION, origin, pages), origin
    # No other permission, even to the page's own origin.
    others = set(QWebEnginePermission.PermissionType.__members__) - {
        CLIPBOARD_PERMISSION}
    assert others
    for name in others | {"", "clipboardreadwrite"}:
        assert not _permitted(name, page, pages), name
    # The name is Qt's (the page passes PermissionType's member name).
    assert (QWebEnginePermission.PermissionType.ClipboardReadWrite.name
            == CLIPBOARD_PERMISSION)


def test_the_clipboard_is_granted_on_this_servers_origin_only():
    # SF-8b's second review: a page of another origin in /web (a link dropped
    # on the view arrives as the host's own Typed) asking for its own origin
    # is not granted. The origin is this server's: A exactly.
    for other in ("http://evil.example", "http://evil.example:5107",
                  "http://127.0.0.1:5108", "http://127.0.0.1:61234",
                  "http://localhost:5107", "http://[::1]:5107",
                  "https://127.0.0.1:5107", "http://127.0.0.1"):
        assert not _permitted(CLIPBOARD_PERMISSION, other,
                              (f"{other}/web/x",)), other
        assert not _permitted(CLIPBOARD_PERMISSION, other,
                              (f"{other}/web/x", "about:blank")), other
    # The server's URL is what LocalUrl gives (127.0.0.1 at the port it
    # listens on); its path does not matter, its port does.
    own = "http://127.0.0.1:5107"
    for server in (own, own + "/", own + "/web/board"):
        assert _permitted(CLIPBOARD_PERMISSION, own, (own + "/web/x",),
                          server), server
    for server in ("http://127.0.0.1:5108/", "http://localhost:5107/",
                   "https://127.0.0.1:5107/", "", "about:blank",
                   "http://127.0.0.1:99999/"):
        assert not _permitted(CLIPBOARD_PERMISSION, own, (own + "/web/x",),
                              server), server
    # The page follows the server when its port changes.
    moved = "http://127.0.0.1:5108"
    assert _permitted(CLIPBOARD_PERMISSION, moved, (moved + "/web/x",),
                      moved + "/")


def test_the_clipboard_is_granted_only_while_the_page_is_in_the_web_ui():
    # The review of SF-8b: the page's own origin also serves layout/ (third
    # parties' layouts), out/, assets/ and the API. A page there is not one of
    # the panels, and _PanelPage does not let the main frame go there either.
    own = "http://127.0.0.1:5107"
    for path in ("/web", "/web/", "/web/board?desktop=1&lng=ja",
                 "/web/scoreboard#x", "/web?view=designer", "/web/./board",
                 "/web//board", "/a/../web/board"):
        assert _permitted(CLIPBOARD_PERMISSION, own, (own + path,)), path
    for path in ("", "/", "/layout/scoreboard/index.html", "/layout/web/a",
                 "/api/v1/health", "/out/program_state.json",
                 "/assets/a.png", "/scoreboard", "/webx", "/web-ui/board",
                 "/WEB/board", "/x/web/board",
                 "/web/../layout/a.html", "/web/%2e%2e/layout/a.html",
                 "/web/%2E%2E/layout/a.html", "/web/.%2e/layout/a.html",
                 "/web/x/../../layout/a.html", "/web/..%2flayout/a.html",
                 "/web/%2e%2e%2flayout/a.html", r"/web\..\layout/a.html"):
        assert not _permitted(CLIPBOARD_PERMISSION, own, (own + path,)), path

    board = f"{own}/web/board"
    # Either of the page's URLs alone, while the other names no document
    # (url() before the first commit, a requested about:blank)...
    for pages in ((board, "about:blank"), ("about:blank", board),
                  (board, ""), (board, board)):
        assert _permitted(CLIPBOARD_PERMISSION, own, pages), pages
    # ...but one off the Web UI or on another origin refuses, and none at all.
    for pages in ((board, f"{own}/layout/a.html"),
                  (f"{own}/layout/a.html", board),
                  (board, "http://127.0.0.1:5108/web/board"),
                  ("http://localhost:5107/web/board", board),
                  (), ("about:blank",), ("about:blank", "")):
        assert not _permitted(CLIPBOARD_PERMISSION, own, pages), pages


def test_no_setting_hands_script_the_clipboard(buildHost):
    rig = _shown(buildHost)
    settings = rig.host.view.settings()

    assert not settings.testAttribute(
        QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard)
    assert not settings.testAttribute(
        QWebEngineSettings.WebAttribute.JavascriptCanPaste)
    # No grant is kept, on the default profile: the one the Designer's window
    # and the off-screen renderers use too (QWebEngineView() without one).
    assert rig.page.profile() == QWebEngineProfile.defaultProfile()
    assert rig.page.profile().persistentPermissionsPolicy() == (
        QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime)


class _Permission:
    """A permission request as _AnswerPermission reads it."""

    def __init__(self, kind, origin):
        self._kind, self._origin = kind, origin
        self.answers = []

    def permissionType(self):
        return SimpleNamespace(name=self._kind)

    def origin(self):
        return QUrl(self._origin)

    def grant(self):
        self.answers.append("grant")

    def deny(self):
        self.answers.append("deny")


def test_the_page_answers_every_request_at_once(buildHost, caplog):
    rig = _shown(buildHost)
    port = rig.servingPort
    own = f"http://127.0.0.1:{port}/"

    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        caplog.clear()
        for kind, origin, answer in (
                (CLIPBOARD_PERMISSION, own, "grant"),
                (CLIPBOARD_PERMISSION, f"http://127.0.0.1:{port + 1}/", "deny"),
                (CLIPBOARD_PERMISSION, f"http://localhost:{port}/", "deny"),
                ("Notifications", own, "deny"),
                ("Geolocation", own, "deny"),
                ("MediaAudioVideoCapture", own, "deny"),
                ("DesktopVideoCapture", own, "deny"),
                ("LocalFontsAccess", own, "deny")):
            request = _Permission(kind, origin)
            rig.page._AnswerPermission(request)
            assert request.answers == [answer], (kind, origin)

    # Its own refusals (not the page's other warnings), one per denial.
    refusals = [record.getMessage() for record in caplog.records
                if record.name == HostModule.__name__
                and record.msg == "The web panel view refused %s to %s"]
    assert len(refusals) == 7
    assert (f"The web panel view refused Notifications to {own}"
            in refusals)
    assert (f"The web panel view refused {CLIPBOARD_PERMISSION} to {own}"
            not in refusals)


def _askedOf(page):
    # The requests the page is asked (after it has answered them).
    asked = []
    page.permissionRequested.connect(lambda permission: asked.append(
        (permission.permissionType().name,
         HostModule._HttpOrigin(permission.origin()))))
    return asked


def _clickRunning(view, call):
    # Runs `call` -- script giving the clipboard's promise -- in a click on
    # `view`, as the panels' buttons do: "ok", or the error's name. A click
    # sent before the page's first frame is lost, so until the page has seen
    # one (the listener runs once).
    page = view.page()
    _pageJs(page, "window.__clip = null, window.__clicks = 0,"
                  " window.addEventListener('click', function () {"
                  " window.__clicks += 1; (" + call + ").then("
                  "function () { window.__clip = 'ok'; },"
                  " function (e) { window.__clip = e.name; }); },"
                  " {once: true}), 0")

    def clicked():
        QTest.mouseClick(view.focusProxy(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
        return _waitUntil(lambda: _pageJs(page, "window.__clicks") > 0,
                          timeout=0.5)
    assert _waitUntil(clicked), "the page saw no click"
    assert _waitUntil(lambda: _pageJs(page, "window.__clip") is not None), \
        "the clipboard did not answer"
    return _pageJs(page, "window.__clip")


def _imageWrite(later=False):
    # An image the way the panels copy one: the clipboard asked with the PNG
    # still to come. `later`: made once the test calls window.__makeImage()
    # (the post on X's image, made after the click).
    make = "window.__makeImage = make;" if later else "make();"
    return ("navigator.clipboard.write([new ClipboardItem({'image/png':"
            " new Promise(function (done) {"
            " var canvas = document.createElement('canvas');"
            " canvas.width = 4; canvas.height = 4;"
            " function make() { window.__focusedAtImage = document.hasFocus();"
            " canvas.toBlob(done, 'image/png'); } " + make + " })})])")


def test_the_page_is_granted_the_clipboard(buildHost):
    rig = _shown(buildHost)
    own = HostModule._HttpOrigin(rig.host.view.url())
    asked = _askedOf(rig.page)
    clipboard = QGuiApplication.clipboard()

    for text in ("first copy", "second copy"):
        assert _clickRunning(
            rig.host.view,
            f"navigator.clipboard.writeText({json.dumps(text)})") == "ok"
        assert clipboard.text() == text
    assert _clickRunning(rig.host.view, _imageWrite()) == "ok"
    assert clipboard.mimeData().hasImage()

    # Through a request (no setting), of the page's origin; none kept.
    assert asked
    assert set(asked) == {(CLIPBOARD_PERMISSION, own)}
    assert rig.page.profile().listAllPermissions() == []


def test_an_image_asked_in_a_click_lands_after_another_window_took_the_focus(
        buildHost):
    # The post on X (S4r): the image is asked for in the click, the default
    # browser takes the focus, and the image, made later, still lands.
    rig = _shown(buildHost)
    clipboard = QGuiApplication.clipboard()
    clipboard.setText("before")
    elsewhere = QLineEdit()
    rig.js("window.__clip = null, window.__clicks = 0,"
           " window.__focusedAtImage = null, window.__makeImage = null,"
           " window.addEventListener('click', function () {"
           " window.__clicks += 1; (" + _imageWrite(later=True) + ").then("
           "function () { window.__clip = 'ok'; },"
           " function (e) { window.__clip = e.name; }); },"
           " {once: true}), 0")
    try:
        _clickView(rig)
        # Another window takes the focus (what the browser does), and then
        # the image is made.
        elsewhere.show()
        elsewhere.activateWindow()
        elsewhere.setFocus()
        assert _waitUntil(lambda: rig.js("document.hasFocus()") is False)
        assert rig.js("window.__clip") is None
        rig.js("window.__makeImage(), 0")

        assert _waitUntil(lambda: rig.js("window.__clip") is not None)
        assert rig.js("window.__clip") == "ok"
        assert rig.js("window.__focusedAtImage") is False
        assert clipboard.mimeData().hasImage()
    finally:
        elsewhere.close()
        elsewhere.deleteLater()
        QCoreApplication.sendPostedEvents(elsewhere, QEvent.Type.DeferredDelete)


def test_a_grant_is_not_kept_for_another_view(buildHost):
    # The Designer's window and the off-screen renderers are views on the
    # same profile that answer no request: a grant kept from the shared view
    # (Qt's StoreInMemory) would answer theirs, on the same origin.
    rig = _shown(buildHost)
    assert _clickRunning(rig.host.view,
                         "navigator.clipboard.writeText('granted')") == "ok"
    own = HostModule._HttpOrigin(rig.host.view.url())

    other = QWebEngineView()    # as AutonaDesignerWindow builds its own
    try:
        assert other.page().profile() == rig.page.profile()
        asked = []

        def answer(permission):
            asked.append((permission.permissionType().name,
                          HostModule._HttpOrigin(permission.origin())))
            permission.deny()
        other.page().permissionRequested.connect(answer)
        loads = []
        other.loadFinished.connect(loads.append)
        other.resize(400, 300)
        other.load(rig.host.view.url())
        other.show()
        other.activateWindow()
        assert _waitUntil(lambda: loads) and loads[-1] is True
        QGuiApplication.clipboard().setText("before")

        assert _clickRunning(
            other, "navigator.clipboard.writeText('leaked')") == "NotAllowedError"
        # Asked (not answered from a kept grant), and refused here.
        assert asked == [(CLIPBOARD_PERMISSION, own)]
        assert QGuiApplication.clipboard().text() == "before"
    finally:
        other.close()
        other.deleteLater()
        QCoreApplication.sendPostedEvents(other, QEvent.Type.DeferredDelete)


def test_a_preview_frame_is_refused_the_clipboard(buildHost, frameServer,
                                                  caplog):
    # A design preview: a frame of the preview listener's origin. As
    # DesignPane frames it (no allow=), Chromium's permissions policy refuses
    # it before anything is asked; delegated (allow="clipboard-read;
    # clipboard-write"), the request reaches the page, which refuses it.
    rig = _shown(buildHost)
    framePort, _ = frameServer
    frameOrigin = ("http", "127.0.0.1", framePort)
    asked = _askedOf(rig.page)
    clipboard = QGuiApplication.clipboard()
    rig.js("window.__messages = [], window.addEventListener('message',"
           " function (e) { window.__messages.push(String(e.data)); }), 0")

    for delegated in (False, True):
        for use in ("write", "read"):
            asked.clear()
            clipboard.setText("before")
            rig.js("document.querySelectorAll('iframe').forEach("
                   "function (f) { f.remove(); }), window.__messages = [], 0")
            rig.js("(function (u, allow) {"
                   " var f = document.createElement('iframe'); f.src = u;"
                   " f.setAttribute('sandbox', 'allow-scripts allow-same-origin');"
                   " if (allow) { f.setAttribute('allow', allow); }"
                   " f.style.cssText = 'position:fixed;left:0;top:0;"
                   "width:400px;height:300px;border:0';"
                   " document.body.appendChild(f); return 1; })("
                   + json.dumps(f"http://127.0.0.1:{framePort}/frame?clip={use}")
                   + ", " + json.dumps("clipboard-read; clipboard-write"
                                       if delegated else "") + ")")

            def answered():
                QTest.mouseClick(
                    rig.host.view.focusProxy(), Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
                return _waitUntil(lambda: rig.js("window.__messages.length"),
                                  timeout=0.5)
            with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
                caplog.clear()
                assert _waitUntil(answered), (delegated, use)

            assert rig.js("window.__messages[0]") == "clip NotAllowedError", (
                delegated, use)
            assert clipboard.text() == "before", (delegated, use)
            if delegated:
                assert asked and set(asked) == {
                    (CLIPBOARD_PERMISSION, frameOrigin)}, use
                assert f"refused {CLIPBOARD_PERMISSION}" in caplog.text
            else:
                assert asked == [], use


# ── the shell's secret (ADR-005 S-final SF-4a-1) ────────────────────────
# The server's SHELL routes (the access token) take only a request that
# carries core.WebAccess.SHELL_HEADER (tests/test_web_access.py). The page's
# own request interceptor adds it to the fetch / XHR to /api/v1/ of this
# server made by a document of this server's origin -- the main frame, and a
# frame that inherits its origin (srcdoc, about:blank) -- while the main
# frame is the Web UI there; not to its pictures, scripts or navigations, not
# to a design preview's fetch, not to a fetch of another origin's, never in
# another view on the same profile (the Designer's); and blocks a request
# that carries it anywhere else (a redirect away).

def _seen(path, server="panel"):
    """The shell's header of each request of `path` the server took."""
    return [s.shell for s in _records(path, server)]


def _records(path, server="panel"):
    return [s for s in SEEN if s.server == server and s.path == path]


def _waitSeen(*paths, server="panel"):
    assert _waitUntil(lambda: all(_seen(p, server) for p in paths)), (
        [p for p in paths if not _seen(p, server)], SEEN)


def test_the_secret_goes_with_the_main_frames_fetches_only(buildHost,
                                                           frameServer):
    rig = _shown(buildHost)
    port = rig.servingPort
    framePort, _ = frameServer
    rig.js("fetch('/api/v1/fetch-get'),"
           " fetch('/api/v1/fetch-post', {method: 'POST', body: '{}'}),"
           " (function () { var x = new XMLHttpRequest();"
           "   x.open('GET', '/api/v1/xhr'); x.send(); })(),"
           " (function () { var i = new Image(); i.src = '/api/v1/img';"
           "   document.body.appendChild(i); })(),"
           " (function () { var s = document.createElement('script');"
           "   s.src = '/api/v1/script.js'; document.body.appendChild(s); })(),"
           " fetch('/web/not-the-api'),"
           f" fetch('http://127.0.0.1:{framePort}/api/v1/to-another-origin',"
           "   {mode: 'no-cors'}), 0")
    # A design preview's frame (another origin) fetching this server's API.
    _frame(rig, f"http://127.0.0.1:{framePort}/frame?fetch=" + quote(
        f"http://127.0.0.1:{port}/api/v1/from-a-preview", safe=""))
    _waitSeen("/api/v1/fetch-get", "/api/v1/fetch-post", "/api/v1/xhr",
              "/api/v1/img", "/api/v1/script.js", "/web/not-the-api",
              "/api/v1/from-a-preview")
    _waitSeen("/api/v1/to-another-origin", server="frame")
    secret = WebAccess._shellSecret
    assert secret and WebAccess.ShellSecretMatches(secret)

    for path in ("/api/v1/fetch-get", "/api/v1/fetch-post", "/api/v1/xhr"):
        assert _seen(path) == [secret], path
    for path in ("/api/v1/img", "/api/v1/script.js", "/web/not-the-api",
                 "/api/v1/from-a-preview",
                 "/web/shell-check?desktop=1&lng=ja"):   # the navigation
        assert _seen(path) == [None], path
    assert _seen("/api/v1/to-another-origin", "frame") == [None]
    # Nothing the frame server took carried it.
    assert not [s for s in SEEN if s.server == "frame" and s.shell is not None]

    # And what carries it is what the server's guard takes as the shell's
    # (WebAccess.ShellRefusalReason / _AmbientRefusal): a same-origin fetch,
    # Host this server's name and port, Origin absent or this server's.
    own = f"http://127.0.0.1:{port}"
    for path in ("/api/v1/fetch-get", "/api/v1/fetch-post", "/api/v1/xhr"):
        (record,) = _records(path)
        assert record.site == "same-origin", path
        assert record.host == f"127.0.0.1:{port}", path
        assert record.origin in (None, own), path
    # Chromium sends Origin with a POST.
    assert _records("/api/v1/fetch-post")[0].origin == own


def _otherView(url):
    # As AutonaDesignerWindow builds its own: a view on the default profile.
    view = QWebEngineView()
    loads = []
    view.loadFinished.connect(loads.append)
    view.resize(400, 300)
    view.load(QUrl(url))
    view.show()
    assert _waitUntil(lambda: loads) and loads[-1] is True
    return view


def test_another_view_on_the_same_profile_never_gets_the_secret(buildHost):
    # The interceptor is the page's, not the profile's: the Designer's view
    # (/web?view=designer) and any other view of this server's Web UI on the
    # default profile fetch without it.
    rig = _shown(buildHost)
    port = rig.servingPort
    rig.js("fetch('/api/v1/shared-view'), 0")
    _waitSeen("/api/v1/shared-view")
    assert _seen("/api/v1/shared-view") == [WebAccess._shellSecret]

    for url, path in ((f"http://127.0.0.1:{port}/web?view=designer",
                       "/api/v1/designer-view"),
                      (f"http://127.0.0.1:{port}/web/second?desktop=1",
                       "/api/v1/another-view")):
        other = _otherView(url)
        try:
            assert other.page().profile() == rig.page.profile()
            _pageJs(other.page(), f"fetch({json.dumps(path)}), 0")
            _waitSeen(path)
            assert _seen(path) == [None], url
        finally:
            other.close()
            other.deleteLater()
            QCoreApplication.sendPostedEvents(other, QEvent.Type.DeferredDelete)


def test_the_secret_goes_on_after_the_page_is_discarded_and_reloaded(
        buildHost):
    rig = _shown(buildHost, discardAfterMs=200)
    document = rig.js("window.__loadId")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == DISCARDED)
    rig.reveal()
    rig.waitLoaded(count=2)
    assert rig.js("window.__loadId") != document

    rig.js("fetch('/api/v1/after-the-reload'), 0")
    _waitSeen("/api/v1/after-the-reload")
    assert _seen("/api/v1/after-the-reload") == [WebAccess._shellSecret]


def test_a_request_goes_on_without_the_secret_when_the_decision_raises(
        buildHost, monkeypatch, caplog):
    rig = _shown(buildHost)

    def broken(*args):
        raise RuntimeError("the decision broke")
    monkeypatch.setattr(HostModule, "ShellHeaderWanted", broken)
    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        for path in ("/api/v1/still-sent", "/api/v1/still-sent-again"):
            rig.js(f"window.__answered = null, fetch({json.dumps(path)})"
                   ".then(function (r) { window.__answered = r.status; }), 0")
            assert _waitUntil(lambda: rig.js("window.__answered") == 200)
    assert _seen("/api/v1/still-sent") == [None]
    assert _seen("/api/v1/still-sent-again") == [None]
    # One warning, however many requests it fails for (Qt asks for each).
    assert _warnings(caplog, "could not tell whether a request is the "
                             "shell's") == 1


def _warnings(caplog, text):
    return len([r for r in caplog.records
                if r.levelno == logging.WARNING and text in r.getMessage()])


def test_a_request_is_blocked_when_telling_whether_it_carries_the_secret_fails(
        buildHost, monkeypatch, caplog):
    # The safe side: a request not to carry the secret, which the shell
    # cannot tell is free of it, does not go out. One wanted still carries
    # it (that path never asks).
    rig = _shown(buildHost)

    def broken(info):
        raise RuntimeError("the check broke")
    monkeypatch.setattr(HostModule, "_CarriesShellHeader", broken)
    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        rig.js("window.__failed = [],"
               " ['/web/blocked-one', '/web/blocked-two'].forEach("
               "   function (p) { fetch(p).catch(function (e) {"
               "     window.__failed.push(p); }); }),"
               " fetch('/api/v1/still-carried'), 0")
        _waitSeen("/api/v1/still-carried")
        assert _waitUntil(lambda: rig.js("window.__failed.length") == 2)
    assert _seen("/api/v1/still-carried") == [WebAccess._shellSecret]
    _waitUntil(lambda: False, timeout=0.5)
    assert _seen("/web/blocked-one") == [] and _seen("/web/blocked-two") == []
    assert _warnings(caplog, "could not keep the shell's secret off a "
                             "request; blocking it") == 1


def test_the_interceptor_runs_on_the_ui_thread(buildHost, monkeypatch):
    # Measured on PySide6 6.11 and relied on: nothing in it waits or locks.
    rig = _shown(buildHost)
    real = HostModule.ShellHeaderWanted
    onMain = []

    def recorded(*args):
        onMain.append(threading.current_thread() is threading.main_thread())
        return real(*args)
    monkeypatch.setattr(HostModule, "ShellHeaderWanted", recorded)
    rig.js("fetch('/api/v1/on-which-thread'), fetch('/web/also-asked'), 0")
    _waitSeen("/api/v1/on-which-thread", "/web/also-asked")
    assert len(onMain) >= 2 and all(onMain), onMain
    assert _seen("/api/v1/on-which-thread") == [WebAccess._shellSecret]


def _fetchIn(path):
    return f"<script>fetch({json.dumps(path)})</script>"


def test_a_frame_of_this_origin_gets_the_secret_too(buildHost):
    # Recorded, not wished for: a frame that inherits the page's origin --
    # srcdoc, the initial about:blank -- is a document of this server's
    # origin, and its fetch carries the secret as the main frame's does (Qt
    # does not say which frame asks; the initiator is that origin). So the
    # Web UI never puts another party's HTML into one unsandboxed
    # (.claude/rules/state-and-webserver.md). A sandboxed srcdoc (an opaque
    # origin) gets none, and its fetch is not a same-origin one either.
    rig = _shown(buildHost)
    rig.js("(function (open, closed) {"
           " var a = document.createElement('iframe'); a.srcdoc = open;"
           " document.body.appendChild(a);"
           " var s = document.createElement('iframe');"
           " s.setAttribute('sandbox', 'allow-scripts'); s.srcdoc = closed;"
           " document.body.appendChild(s);"
           " var b = document.createElement('iframe');"
           " document.body.appendChild(b);"
           " b.contentWindow.eval(\"fetch('/api/v1/blank-child')\");"
           " return 1; })("
           f"{json.dumps(_fetchIn('/api/v1/srcdoc-child'))},"
           f" {json.dumps(_fetchIn('/api/v1/sandboxed-child'))})")
    _waitSeen("/api/v1/srcdoc-child", "/api/v1/sandboxed-child",
              "/api/v1/blank-child")
    secret = WebAccess._shellSecret
    assert _seen("/api/v1/srcdoc-child") == [secret]
    assert _seen("/api/v1/blank-child") == [secret]
    (sandboxed,) = _records("/api/v1/sandboxed-child")
    assert sandboxed.shell is None
    assert sandboxed.site != "same-origin"


def test_the_guard_takes_the_shared_views_fetch_as_the_shells(buildHost,
                                                              monkeypatch):
    # The engine's own requests through the real guard and routes
    # (_guard_http_request, api_v1's access-token routes; the panel server
    # relays them, as werkzeug would serve them on that port): the shared
    # view reads the token and regenerates it; another view on the same
    # profile is refused.
    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", ("192.0.2.10",))
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())
    app, socketio = _build_web_app()
    app.register_blueprint(api_v1)
    monkeypatch.setattr(WebServer, "socketio", socketio)
    monkeypatch.setitem(RELAY, "client", app.test_client())

    rig = _shown(buildHost)
    port = rig.servingPort
    rig.js("window.__answers = [], (function () {"
           " function ask(path, init) {"
           "   return fetch(path, init).then(function (r) {"
           "     return r.json().then(function (b) {"
           "       window.__answers.push({status: r.status,"
           "         token: b.data ? b.data.token : null}); }); }); }"
           " ask('/api/v1/access-token').then(function () {"
           "   return ask('/api/v1/access-token:regenerate', {method: 'POST',"
           "     headers: {'Content-Type': 'application/json'},"
           "     body: JSON.stringify({confirm: true})}); });"
           " })(), 0")
    assert _waitUntil(lambda: rig.js("window.__answers.length") == 2)
    assert rig.js("window.__answers.map(function (a) { return a.status; })"
                  ) == [200, 200]
    assert rig.js("!!window.__answers[0].token && window.__answers[0].token"
                  " !== window.__answers[1].token") is True
    assert rig.js("window.__answers[1].token") == WebAccess.Token()

    other = _otherView(f"http://127.0.0.1:{port}/web/second?desktop=1")
    try:
        _pageJs(other.page(),
                "window.__refused = null, fetch('/api/v1/access-token')"
                ".then(function (r) { return r.json().then(function (b) {"
                " window.__refused = [r.status, b.error.code]; }); }), 0")
        assert _waitUntil(lambda: _pageJs(other.page(), "window.__refused"))
        assert _pageJs(other.page(), "window.__refused") == [403, "FORBIDDEN"]
    finally:
        other.close()
        other.deleteLater()
        QCoreApplication.sendPostedEvents(other, QEvent.Type.DeferredDelete)
    assert _seen("/api/v1/access-token") == [WebAccess._shellSecret, None]


def test_a_redirect_away_does_not_carry_the_secret(buildHost, frameServer,
                                                   caplog):
    # Measured on PySide6 6.11: a header the interceptor added follows a
    # redirect, to another origin as well. A hop that is not one to carry it
    # is blocked; one that is (this server's /api/v1/) carries it again.
    rig = _shown(buildHost)
    framePort, frameRequests = frameServer
    frameRequests.clear()
    away = f"http://127.0.0.1:{framePort}/api/v1/landing-away"
    with caplog.at_level(logging.WARNING, logger=HostModule.__name__):
        rig.js("window.__failed = null,"
               f" fetch({json.dumps(REDIRECT_AWAY + quote(away, safe=''))},"
               "   {mode: 'no-cors'}).catch(function (e) {"
               "   window.__failed = String(e); }),"
               f" fetch({json.dumps(REDIRECT_HERE)}), 0")
        _waitSeen("/api/v1/landing-here")
        assert _waitUntil(lambda: rig.js("window.__failed"))
        assert "blocked a request that carried the shell's secret" in (
            caplog.text)
    secret = WebAccess._shellSecret
    assert _seen(REDIRECT_AWAY + quote(away, safe="")) == [secret]
    assert _seen(REDIRECT_HERE) == [secret]
    assert _seen("/api/v1/landing-here") == [secret]
    _waitUntil(lambda: False, timeout=0.5)
    assert "/api/v1/landing-away" not in frameRequests
    assert secret not in caplog.text


def test_the_main_frame_does_not_go_to_the_designers_view(buildHost):
    # The Designer opens in its own window (a click: designerRequested); the
    # shared view's main frame never shows it, so the secret never goes
    # with its fetches.
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    served = len(rig.requests)
    port = rig.servingPort
    rig.js("void (location.href = '/web?view=designer')")
    _waitUntil(lambda: False, timeout=1.0)
    assert rig.js("window.__loadId") == document
    assert len(rig.requests) == served
    assert rig.page.refusedLast

    N = QWebEnginePage.NavigationType
    kinds = (N.NavigationTypeLinkClicked, N.NavigationTypeOther,
             N.NavigationTypeRedirect, N.NavigationTypeFormSubmitted,
             N.NavigationTypeBackForward, N.NavigationTypeTyped,
             N.NavigationTypeReload)
    for kind in kinds:
        for path in ("/web?view=designer", "/web/board?view=designer",
                     "/web/x?desktop=1&view=designer", "/web?vi%65w=designer",
                     "/web?view=designer#x", "/web?view=other&view=designer"):
            assert rig.page.acceptNavigationRequest(
                QUrl(f"http://127.0.0.1:{port}{path}"), kind, True) is False, (
                    path, kind)
        for path in ("/web?view=other", "/web/board?desktop=1&lng=ja",
                     "/web?viewer=designer"):
            assert rig.page.acceptNavigationRequest(
                QUrl(f"http://127.0.0.1:{port}{path}"), kind, True) is True, (
                    path, kind)


SHELL_SERVER = QUrl("http://127.0.0.1:5107/")
WEB_UI = "http://127.0.0.1:5107/web/board?desktop=1&lng=ja"
OWN = "http://127.0.0.1:5107"


def _wanted(request, initiator=OWN, firstParty=WEB_UI,
            kind=HostModule.SHELL_RESOURCE_TYPE, server=SHELL_SERVER):
    return HostModule.ShellHeaderWanted(
        QUrl(request), QUrl(initiator), QUrl(firstParty), kind, server)


def test_the_secret_is_wanted_for_this_servers_api_fetches_only():
    api = "http://127.0.0.1:5107/api/v1/access-token"
    assert _wanted(api) is True
    assert _wanted("http://127.0.0.1:5107/api/v1/access-token:regenerate")
    assert _wanted(api, firstParty="http://127.0.0.1:5107/web") is True
    assert _wanted(api, firstParty="http://127.0.0.1:5107/web?view=other")
    # The default port is no port.
    assert _wanted("http://127.0.0.1/api/v1/x", initiator="http://127.0.0.1:80",
                   firstParty="http://127.0.0.1/web/board",
                   server=QUrl("http://127.0.0.1:80/")) is True

    # Only a fetch / XHR.
    for kind in ("ResourceTypeMainFrame", "ResourceTypeSubFrame",
                 "ResourceTypeImage", "ResourceTypeScript",
                 "ResourceTypeStylesheet", "ResourceTypePing",
                 "ResourceTypeServiceWorker", "ResourceTypeUnknown", ""):
        assert _wanted(api, kind=kind) is False, kind
    # Only this server's origin, as the host loads it, and its /api/v1/.
    for request in ("http://localhost:5107/api/v1/x",
                    "http://192.168.1.5:5107/api/v1/x",
                    "http://127.0.0.1:5108/api/v1/x",
                    "https://127.0.0.1:5107/api/v1/x",
                    "http://evil.example/api/v1/x",
                    "http://127.0.0.1:5107/api/v1",
                    "http://127.0.0.1:5107/api/v2/x",
                    "http://127.0.0.1:5107/api/v1/../../layout/a.js",
                    "http://127.0.0.1:5107/layout/a/index.html",
                    "http://127.0.0.1:5107/web/board",
                    "http://127.0.0.1:5107/",
                    "file:///C:/api/v1/x", "data:text/plain,a", ""):
        assert _wanted(request) is False, request
    # Started by a document of this origin: not a preview's frame.
    for initiator in ("http://127.0.0.1:5108", "http://localhost:5107",
                      "https://127.0.0.1:5107", "null", "", "about:blank"):
        assert _wanted(api, initiator=initiator) is False, initiator
    # While the main frame is the Web UI on this origin, not the Designer.
    for firstParty in ("http://127.0.0.1:5107/layout/a/index.html",
                       "http://127.0.0.1:5107/",
                       "http://127.0.0.1:5107/webx",
                       "http://127.0.0.1:5107/web/%2e%2e/layout/a.html",
                       "http://127.0.0.1:5107/web?view=designer",
                       "http://127.0.0.1:5107/web/board?a=1&view=designer",
                       "http://127.0.0.1:5107/web?vi%65w=design%65r",
                       "http://127.0.0.1:5108/web/board",
                       "http://localhost:5107/web/board",
                       "about:blank", ""):
        assert _wanted(api, firstParty=firstParty) is False, firstParty
    # No server to compare with: nothing.
    assert _wanted(api, server=QUrl()) is False


def _srcTrees():
    root = Path(__file__).resolve().parent.parent / "src"
    for path in sorted(root.rglob("*.py")):
        yield path.relative_to(root.parent).as_posix(), ast.parse(
            path.read_text(encoding="utf-8"))


def _calledName(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else None)


def test_only_the_shared_views_interceptor_issues_the_secret():
    # Headless never imports the shared view, so it never issues one and
    # every SHELL request is refused there (tests/test_web_access.py).
    callers = set()
    for path, tree in _srcTrees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and _calledName(node) == "IssueShellSecret"):
                callers.add(path)
    assert callers == {"src/AutonaWebPanelHost.py"}
    # ... from the interceptor alone.
    tree = ast.parse(Path(HostModule.__file__).read_text(encoding="utf-8"))
    owners = set()
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for node in ast.walk(cls):
            if (isinstance(node, ast.Call)
                    and _calledName(node) == "IssueShellSecret"):
                owners.add(cls.name)
    assert owners == {"_ShellRequestInterceptor"}


def test_the_interceptor_is_the_pages_and_nothing_injects_script():
    # The page's own (self.setUrlRequestInterceptor in _PanelPage), never a
    # profile's -- the Designer's view shares the default profile -- and no
    # QWebEngineScript hands a page anything (SF-4a's rejected way).
    setters = []
    for path, tree in _srcTrees():
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for node in ast.walk(cls):
                if (isinstance(node, ast.Call) and _calledName(node)
                        == "setUrlRequestInterceptor"):
                    target = node.func.value
                    setters.append((path, cls.name, isinstance(
                        target, ast.Name) and target.id == "self"))
        for node in ast.walk(tree):
            assert not (isinstance(node, (ast.Name, ast.Attribute)) and (
                getattr(node, "id", None) == "QWebEngineScript"
                or getattr(node, "attr", None) == "QWebEngineScript")), path
            assert not (isinstance(node, ast.alias)
                        and node.name == "QWebEngineScript"), path
    assert setters == [("src/AutonaWebPanelHost.py", "_PanelPage", True)]
    for path, tree in _srcTrees():
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Call) and _calledName(node)
                        == "setUrlRequestInterceptor"
                        and not isinstance(node.func.value, ast.Name)), path


def test_the_page_holds_its_interceptor(buildHost):
    rig = _shown(buildHost)
    interceptor = rig.page._shellInterceptor
    assert isinstance(interceptor, HostModule._ShellRequestInterceptor)
    assert interceptor.parent() is rig.page


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


def _menuPages():
    """Window.MENU_PAGES, read out of src/Autona.py."""
    for node in ast.walk(ast.parse(_autonaSource())):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "MENU_PAGES"):
            return ast.literal_eval(node.value)
    raise AssertionError("src/Autona.py: no MENU_PAGES")


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
    the menu's pages after them (MENU_PAGES: Settings, S4af; the API /
    Companion / OBS guides and About, S-final SF-4a-2). Off screen, so the
    host builds no
    view; what it was asked to show is its _panel. The sidebar is a stand-in
    (_FakeSidebar) that choose() highlights as a click does."""

    METHODS = ("_OnNavSelected", "_OnStackPageChanged",
               "_EnsurePanelAt", "_WebPanel", "_AdoptWebPanelHost",
               "_CreateEventPanel", "_CreateStreamPanel",
               "_CreateScoreboardPanel", "_CreateTop8Panel",
               "_CreateCommentaryPanel", "_CreateStatsPanel",
               "OpenScoreSets", "OpenSettings", "_NavToRemember",
               "OpenMenuPage")

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
        window.body.append(ast.parse(
            f"MENU_PAGES = {_menuPages()!r}\n").body[0])

        namespace = {"QWidget": QWidget, "logger": logger,
                     "AutonaWebPanelHost": AutonaWebPanelHost,
                     "functools": functools}
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
        # As _BuildUi: the menu's pages after the items, no item's.
        for key in w.MENU_PAGES:
            index = w.stack.addWidget(QWidget())
            w._lazyPanels[index] = (functools.partial(w._WebPanel, key), key)
            w._panelIndexes[key] = index
            w._webPanelPages.Add(index, key)
        w._navBeforeMenuPage = 0
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


(EVENT, STREAM, SCOREBOARD, TOP8, COMMENTARY, STATS, LAZY_QT,
 SETTINGS, API, COMPANION, OBS, ABOUT) = range(12)


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


# ── the menu's other pages (ADR-005 S-final SF-4a-2) ────────────────────
# The API reference & tester, the Companion and OBS guides and About: Qt
# windows (About a QMessageBox) until then, now pages as Settings is.

@pytest.mark.parametrize("name, page", [("api", API),
                                        ("companion", COMPANION),
                                        ("obs", OBS), ("about", ABOUT)])
def test_a_menu_page_shows_the_host_with_no_item_highlighted(qapp, name,
                                                             page):
    rig = _WindowRig()
    w = rig.window
    order = _askingSettings(rig)
    rig.choose(SCOREBOARD)
    host = w._webPanelHost

    w.OpenMenuPage(name)

    assert order == ["front"]
    assert host._panel == name
    assert w.stack.currentIndex() == SCOREBOARD
    assert rig.hosts() == [SCOREBOARD]
    assert w.stack.widget(page) is rig.placeholders[page]
    assert page not in w._lazyPanels
    assert w.sidebar.currentIndex() == -1
    assert w._NavToRemember() == SCOREBOARD

    assert rig.choose(STATS) == SCOREBOARD
    assert host._panel == "stats"
    assert w._NavToRemember() == STATS


def test_a_menu_page_opened_first_builds_the_host_on_its_page(qapp):
    rig = _WindowRig()
    w = rig.window
    _askingSettings(rig)
    rig.choose(LAZY_QT)

    w.OpenMenuPage("obs")

    assert w.stack.currentIndex() == OBS
    assert rig.hosts() == [OBS]
    assert w._webPanelHost._panel == "obs"
    assert w._NavToRemember() == LAZY_QT
    # The other menu pages and the React items show it from then on.
    assert SETTINGS not in w._lazyPanels
    assert ABOUT not in w._lazyPanels
    assert EVENT not in w._lazyPanels
    w.OpenMenuPage("about")
    assert w.stack.currentIndex() == OBS
    assert w._webPanelHost._panel == "about"
    assert w._NavToRemember() == LAZY_QT
    assert rig.choose(EVENT) == OBS
    assert w._webPanelHost._panel == "event"
    assert rig.hosts() == [OBS]


def test_going_from_one_menu_page_to_another_keeps_the_item_before_them(
        qapp):
    rig = _WindowRig()
    w = rig.window
    _askingSettings(rig)
    rig.choose(TOP8)
    w.OpenMenuPage("api")
    host = w._webPanelHost

    w.OpenSettings()
    assert host._panel == "settings"
    w.OpenMenuPage("companion")

    assert host._panel == "companion"
    assert w.sidebar.currentIndex() == -1
    assert w._NavToRemember() == TOP8
    assert rig.hosts() == [TOP8]


def test_only_a_menu_page_is_opened_as_one(qapp):
    rig = _WindowRig()
    w = rig.window
    order = _askingSettings(rig)
    rig.choose(STREAM)

    for name in ("stream", "lazyQt", "playerBrowser", "nope"):
        with pytest.raises(ValueError):
            w.OpenMenuPage(name)

    assert order == []
    assert w.sidebar.currentIndex() == STREAM
    assert w._webPanelHost._panel == "stream"


@pytest.mark.parametrize("name", ["api", "companion", "obs", "about"])
def test_a_menu_page_asked_for_while_the_window_closes_does_nothing(
        qapp, name):
    rig = _WindowRig()
    w = rig.window
    order = _askingSettings(rig)
    rig.choose(LAZY_QT)
    w._shuttingDown = True

    w.OpenMenuPage(name)

    assert order == []
    assert w.stack.currentIndex() == LAZY_QT
    assert w.sidebar.currentIndex() == LAZY_QT
    assert rig.hosts() == []


def test_the_menu_opens_its_pages():
    # Settings first (the restore's bound: every page from it on is a menu
    # page), each a React panel, registered as a lazy page after the items
    # (the rig above does what _BuildUi does) and none a sidebar item: the
    # saved sidebar index does not move, so STATE_VERSION stays.
    pages = _menuPages()
    assert pages == ("settings", "api", "companion", "obs", "about")
    assert all(WebAccess.IsWebPanel(name) for name in pages)
    source = _autonaSource()
    assert "for key in self.MENU_PAGES:" in source
    assert "functools.partial(self._WebPanel, key), key)" in source
    assert "    STATE_VERSION = 22\n" in source.replace("\r\n", "\n")
    tree = ast.parse(source)
    sections = next(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute)
            and target.attr == "_navSections" for target in node.targets))
    named = {node.value for node in ast.walk(sections)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert named.isdisjoint(pages), named & set(pages)

    # The menu's entries: each asks for its page by name (Settings through
    # OpenSettings), nothing else opens one.
    opened = sorted(
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "OpenMenuPage"
        and node.args and isinstance(node.args[0], ast.Constant))
    assert opened == sorted(pages), opened
    # The API Settings submenu's entries are the app's strings now (the
    # "api" context went with the API window).
    for text in ("API Settings", "Open reference & tester",
                 "Companion settings", "OBS / Advanced Scene Switcher",
                 "About Autona"):
        assert f'QApplication.translate("app", "{text}")' in source, text
    assert 'translate("api"' not in source


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
    # (/web/settings, the menu's Window.OpenSettings -> OpenMenuPage) is the
    # only settings
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

# The Qt guide windows (ADR-005 S-final SF-4a-2): the API reference &
# tester, the Companion and OBS guides; and the About box. Pages now
# (web/src/GuidePages/, the menu's Window.OpenMenuPage).
QT_GUIDE_MODULES = ("AutonaApiInfoWindow", "AutonaIntegrationGuideWindow",
                    "AutonaObsGuideWindow")
# What went with them: the window's accessors and slots, the About box, and
# the Settings page's own opener and memory (OpenMenuPage's now).
QT_GUIDE_NAMES = (*QT_GUIDE_MODULES, "EnsureApiInfoWindow",
                  "EnsureCompanionGuideWindow", "EnsureObsGuideWindow",
                  "_apiInfoWindow", "_companionGuideWindow",
                  "_obsGuideWindow", "ShowAboutDialog",
                  "_CreateSettingsPanel", "_navBeforeSettings")
# The menu's API Settings entries, now the app context's (en, ja).
GUIDE_MENU_STRINGS = {
    "Open reference & tester": "リファレンス・テスターを開く",
    "Companion settings": "Companion の設定",
    "OBS / Advanced Scene Switcher": "OBS / Advanced Scene Switcher",
}


def test_the_qt_guide_windows_are_gone():
    import re
    import xml.etree.ElementTree as ET

    from tests.conftest import PROJECT_ROOT

    src = PROJECT_ROOT / "src"
    for module in QT_GUIDE_MODULES:
        assert not (src / f"{module}.py").exists(), module
    scanned = [*sorted(src.rglob("*.py")), PROJECT_ROOT / "main.py",
               *sorted((PROJECT_ROOT / "tests").glob("*.py")),
               *sorted((PROJECT_ROOT / "tests" / "live").glob("*.py"))]
    importers = {str(path.relative_to(PROJECT_ROOT)):
                 sorted(_importedModules(path) & set(QT_GUIDE_MODULES))
                 for path in scanned}
    assert {path: names for path, names in importers.items() if names} == {}
    users = {str(path.relative_to(PROJECT_ROOT)):
             sorted(_codeNames(path) & set(QT_GUIDE_NAMES))
             for path in sorted(src.rglob("*.py"))}
    assert {path: names for path, names in users.items() if names} == {}
    # The game asset guide stays a lazy Qt window.
    assert (src / "AutonaGameAssetGuideWindow.py").exists()
    assert "EnsureGameAssetGuideWindow" in _codeNames(src / "Autona.py")

    # The translations: no "api" context, nothing located in the windows'
    # files; the menu's entries in the app context, translated in ja.
    for name in ("Autona_en.ts", "Autona_ja.ts"):
        path = src / "i18n" / name
        text = path.read_text(encoding="utf-8")
        assert "<name>api</name>" not in text, name
        for module in QT_GUIDE_MODULES:
            assert module not in text, (name, module)
        app = next(context for context in ET.parse(path).getroot()
                   .findall("context") if context.findtext("name") == "app")
        messages = {message.findtext("source"):
                    message.findtext("translation")
                    for message in app.findall("message")}
        for source, ja in GUIDE_MENU_STRINGS.items():
            assert source in messages, (name, source)
            if name == "Autona_ja.ts":
                assert messages[source] == ja, (source, messages[source])
        assert not re.search(r"<location[^>]*AutonaApiInfoWindow", text)

    # The build collects none of it.
    spec = (PROJECT_ROOT / "dependencies" / "autona.spec").read_text(
        encoding="utf-8")
    for module in QT_GUIDE_MODULES:
        assert module not in spec, module


def test_the_about_page_names_the_inquiry_address_the_menu_mails():
    # The About box showed BUSINESS_INQUIRY_EMAIL; the page writes it out
    # (a mailto link in the shared view would go nowhere: _OpenOutside hands
    # http(s) on only), and the Help menu's "Event support inquiries" still
    # mails it.
    from tests.conftest import PROJECT_ROOT

    tree = ast.parse(_autonaSource())
    email = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name)
                         and target.id == "BUSINESS_INQUIRY_EMAIL"
                         for target in node.targets))
    page = (PROJECT_ROOT / "web" / "src" / "GuidePages" / "AboutPage.tsx"
            ).read_text(encoding="utf-8")
    assert f'BUSINESS_INQUIRY_EMAIL = "{email}";' in page



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
