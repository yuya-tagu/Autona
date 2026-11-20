"""The desktop's shared web view for the React panels (ADR-005 S4a).

AutonaWebPanelHost is one QStackedWidget page holding one QWebEngineView
for every panel moved to React. What has to hold:

* the view is not built until the host is on screen with a panel to show
  and the web server is ready (startup does not pay for it), and then from
  the event loop, not inside the show or the readiness call that allows it
  (S4g review M4: the host is the main window's one widget since S-final
  SF-5b-3, so at startup it is shown by the main window's self.show(),
  ahead of STARTUP phase=core-shown, and the server is marked ready inside
  _StartWebServer(), ahead of phase=webserver-ready); no paint is waited
  for (S4g review L14: a window started minimized is not painted);
* it opens ``/web/app/<panel>?desktop=1&lng=<Qt language>`` (the AppShell,
  ADR-005 S-final SF-5b-3: the host is the main window's one widget) once the
  web server is up, and showing another panel navigates the page's router
  instead of reloading the view; the panel the page's router moves to
  (``/web/app/<panel>``, the AppShell's sidebar) is the panel the host
  follows: it is the one a reload opens and the one panelChanged names, with
  the URL's ``nav`` (the sidebar collapsed) and ``told`` (the tournament load
  failure the page has told);
* off screen it is frozen / discarded (AutonaWebPageLifecycle), and on reveal
  it asks for the full state
  and shows the panel chosen meanwhile -- after a thaw and after the reload
  of a discarded page alike;
* a load that failed for good does not leave the view blank for the rest
  of the process: a retry view covers it, whose button -- as the next
  ShowPanel() or reveal -- loads the page again; a view that cannot be built
  is told as the start failure is;
* a shell request (ADR-005 S-final SF-5a-1) is run only when a click opens
  exactly one with the ticket, never reaches the default browser, and its
  answer reaches the page that asked (held while it is frozen, dropped with
  its document).

These drive a real QWebEngineView (offscreen) against a throwaway local
HTTP server, because the behaviour under test is Qt's. The served page
stands in for the Web UI's panel shell (``window.autonaPanelShell``,
web/src/PanelShell/hostApi.ts): it records the navigations, full-state
requests and SCORE set selections (S4ab-2, with the path the page was on
then) the host makes, and the shell requests' answers and the shell's state
(SF-5a-1), and navigates the way the shell does
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
from qtpy import shiboken
from qtpy.QtCore import (
    QCoreApplication, QEvent, QEventLoop, QPoint, QSettings, Qt, QTimer, QUrl)
from qtpy.QtGui import QGuiApplication
from qtpy.QtTest import QTest
from qtpy.QtWebEngineCore import (
    QWebEnginePage, QWebEnginePermission, QWebEngineProfile, QWebEngineSettings)
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import (
    QDialog, QLabel, QLineEdit, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget)

from src import AutonaWebPageLifecycle as WPL
from src import AutonaWebPanelHost as HostModule
from src.AutonaWebPanelHost import (
    CLIPBOARD_PERMISSION, AutonaWebPanelHost, ClipboardPermitted, PanelPath,
    ShellRequest)
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
window.__shellResults = [];
window.__shellStates = [];
window.autonaPanelShell = {
  navigate: function (p) {
    var prefix = location.pathname.indexOf('/web/app/') === 0
      ? '/web/app/' : '/web/';
    var target = prefix + encodeURIComponent(p);
    if (location.pathname === target) { return; }
    window.__navigations.push(p);
    history.replaceState(null, '', target + location.search);
  },
  refresh: function () { window.__refreshes += 1; },
  openScoreSets: function (b) {
    window.__scoreSets.push(b + '@' + location.pathname);
  },
  shellResult: function (i, r) { window.__shellResults.push([i, r]); },
  shellState: function (v) { window.__shellStates.push(v); }
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


# The panel server hands the access token's routes and the asset upload to
# the real app when a test puts its Flask test client here (test_the_guard_
# takes_the_shared_views_fetch_as_the_shells, test_the_shared_views_xhr_
# uploads_a_zip_as_its_bytes): the engine's own request, through the real
# _guard_http_request. RELAY["sent"], when a test puts a list there: each
# relayed request's (path, Content-Length, Transfer-Encoding) as it came.
RELAY = {}
RELAY_PREFIX = ("/api/v1/access-token", "/api/v1/assets/uploads",
                "/api/v1/shell-session")


def _relayed(handler, body=b""):
    client = RELAY.get("client")
    if client is None or not handler.path.startswith(RELAY_PREFIX):
        return False
    if "sent" in RELAY:
        RELAY["sent"].append((handler.path,
                              handler.headers.get("Content-Length"),
                              handler.headers.get("Transfer-Encoding")))
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

    def __init__(self, shell=None, windowClass=QWidget):
        self.window = windowClass()
        self.stack = QStackedWidget(self.window)
        QVBoxLayout(self.window).addWidget(self.stack)
        self.host = AutonaWebPanelHost(shell=shell)
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

    def build(discardAfterMs=60_000, shell=None, windowClass=QWidget):
        monkeypatch.setattr(AutonaWebPanelHost, "DISCARD_AFTER_MS",
                            discardAfterMs)
        rig = _Rig(shell, windowClass)
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
    assert PanelPath("shell-check") == "/web/app/shell-check?desktop=1&lng=en-US"
    # The AppShell's sidebar collapsed, and the tournament load failure it
    # has told (SF-5b-3): carried to the page a reload opens.
    assert PanelPath("shell-check", navCollapsed=True) == (
        "/web/app/shell-check?desktop=1&lng=en-US&nav=collapsed")
    assert PanelPath("event", navCollapsed=True, told=12) == (
        "/web/app/event?desktop=1&lng=en-US&nav=collapsed&told=12")
    assert PanelPath("event", told=0) == (
        "/web/app/event?desktop=1&lng=en-US&told=0")


def test_the_followed_panel_is_the_app_shells(monkeypatch):
    AppPanel = HostModule.AppPanel
    monkeypatch.setattr(WebAccess, "WEB_PANELS",
                        frozenset({"shell-check", "event"}))
    page = "http://127.0.0.1:5107"
    assert HostModule.AppPanelOf(QUrl(
        f"{page}/web/app/event?desktop=1&lng=ja")) == AppPanel(
            "event", False, None)
    assert HostModule.AppPanelOf(QUrl(
        f"{page}/web/app/event?desktop=1&nav=collapsed&told=7")) == AppPanel(
            "event", True, 7)
    # The first value of each; another value of nav is not collapsed; a told
    # that is not a revision is none.
    assert HostModule.AppPanelOf(QUrl(
        f"{page}/web/app/event?nav=collapsed&nav=open&told=a&told=2"
    )) == AppPanel("event", True, None)
    assert HostModule.AppPanelOf(QUrl(
        f"{page}/web/app/event?nav=open")) == AppPanel("event", False, None)
    # Not the AppShell's page, or not a panel: not followed.
    for path in ("/web/event", "/web/app/", "/web/app/unknown",
                 "/web/app/event/x", "/web/appx/event", "/layout/app/event",
                 "/web/app/shell-check%2Fx"):
        assert HostModule.AppPanelOf(QUrl(page + path)) is None, path


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
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")


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
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")
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
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")
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
                 "/web/app/shell-check?desktop=1&lng=ja"):   # the navigation
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


def test_the_shared_views_xhr_uploads_a_zip_as_its_bytes(buildHost,
                                                         monkeypatch,
                                                         tmp_path):
    # SF-4b-1: the page sends the ZIP as an XHR's Blob. It reaches the real
    # route (the panel server relays it, as werkzeug would serve it) with a
    # Content-Length -- the route refuses a chunked body unread -- and the
    # shell's secret, its file name percent-encoded: held, 201.
    import base64
    import io
    import zipfile

    from src.AutonaApiV1 import api_v1
    from src.AutonaWebServer import WebServer, _build_web_app
    from src.core.AssetPackService import AssetPackService

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
    monkeypatch.setitem(RELAY, "sent", [])
    service = AssetPackService()
    monkeypatch.setattr(AssetPackService, "instance", service)

    rig = _shown(buildHost)
    # The upload lands under user_data/ of the working directory.
    monkeypatch.chdir(tmp_path)
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as z:
        z.writestr("base_files/config.json", json.dumps({"name": "X"}))
        z.writestr("full/config.json", "{}")
    data = packed.getvalue()
    rig.js("window.__upload = null, (function () {"
           f" var raw = atob({json.dumps(base64.b64encode(data).decode())}),"
           "   bytes = new Uint8Array(raw.length);"
           " for (var i = 0; i < raw.length; i++)"
           "   bytes[i] = raw.charCodeAt(i);"
           " var x = new XMLHttpRequest();"
           " x.open('POST', '/api/v1/assets/uploads');"
           " x.setRequestHeader('X-Autona-File-Name',"
           f"   encodeURIComponent({json.dumps('Ünï game.zip')}));"
           " x.onload = function () {"
           "   window.__upload = [x.status, JSON.parse(x.responseText)]; };"
           " x.onerror = function () { window.__upload = ['error']; };"
           " x.send(new Blob([bytes], {type: 'application/zip'}));"
           " })(), 0")
    assert _waitUntil(lambda: rig.js("window.__upload"))
    status, body = rig.js("window.__upload")
    assert status == 201, body
    assert body["data"]["games"] == ["Ünï game"]
    assert body["data"]["conflicts"] == []
    assert RELAY["sent"] == [
        ("/api/v1/assets/uploads", str(len(data)), None)]
    assert _seen("/api/v1/assets/uploads") == [WebAccess._shellSecret]
    assert len(list(
        (tmp_path / "user_data" / ".autona-uploads").iterdir())) == 1


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


def _pageAsking(url=PAGE_URL):
    page = SimpleNamespace(url=lambda: QUrl(url),
                           requestedUrl=lambda: QUrl(url),
                           designer=[], shell=[])
    page.designerRequested = SimpleNamespace(
        emit=lambda: page.designer.append(True))
    page.shellRequested = SimpleNamespace(emit=page.shell.append)
    page._TakeShellRequest = functools.partial(
        HostModule._PanelPage._TakeShellRequest, page)
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
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")
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
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")
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


# ── shell requests (ADR-005 S-final SF-5a-1) ────────────────────────────
#
# A page asks the desktop itself by opening, in a click, a new window of this
# server's /web/shell-request with the verb, an id and the ticket
# (web/src/PanelShell/hostApi.ts). _PanelPage takes exactly that and nothing
# else (ParseShellRequest), hands nothing shaped like one to the default
# browser and logs no ticket; the host runs the verb from the event loop and
# answers the page that asked (window.autonaPanelShell.shellResult). The
# ticket's route is tests/test_web_access.py's.

SHELL_TICKET = "Made_Up_Ticket_For_The_Test_0123456789abcde"
SHELL_URL = "http://127.0.0.1:5107/web/shell-request"
_T = SHELL_TICKET
_B = "http://127.0.0.1:5107"
_Q = f"verb=checkUpdates&request=r1&ticket={_T}"


@pytest.fixture
def issuedTicket(monkeypatch):
    """The ticket this process issued (WebAccess.IssueShellTicket's)."""
    monkeypatch.setattr(WebAccess, "_shellTicket", SHELL_TICKET)
    return SHELL_TICKET


@pytest.fixture
def serverAt5107(monkeypatch):
    # The server of PAGE_URL (_ServerUrl), for the page stand-ins.
    monkeypatch.setattr(HostModule, "_ServerUrl",
                        lambda: QUrl("http://127.0.0.1:5107/"))


def _checkShellRequest(url, pages=(PAGE_URL, PAGE_URL)):
    return HostModule.ParseShellRequest(
        QUrl(url), tuple(QUrl(page) for page in pages), SHELL_SERVER,
        lambda value: value == SHELL_TICKET)


VALID_SHELL_REQUESTS = [
    (_Q, ShellRequest("checkUpdates", "r1", {})),
    (f"verb=alwaysOnTop&on=1&request=r1&ticket={_T}",
     ShellRequest("alwaysOnTop", "r1", {"on": "1"})),
    # Any order; the id's whole alphabet.
    (f"on=0&ticket={_T}&request=A-z_09&verb=alwaysOnTop",
     ShellRequest("alwaysOnTop", "A-z_09", {"on": "0"})),
    (f"verb=openExternal&target=discord&request=r2&ticket={_T}",
     ShellRequest("openExternal", "r2", {"target": "discord"})),
    (f"verb=openExternal&target=inquiry&request={'x' * 64}&ticket={_T}",
     ShellRequest("openExternal", "x" * 64, {"target": "inquiry"})),
    # SF-5a-2: no argument -- the operator names the folder in the dialog.
    (f"verb=pickFolder&request=r3&ticket={_T}",
     ShellRequest("pickFolder", "r3", {})),
]


REFUSED_SHELL_REQUESTS = [
    # Not this server's origin: another name of this PC, another port, the
    # default one, https, a LAN address, elsewhere, a file.
    (f"http://localhost:5107/web/shell-request?{_Q}", "origin"),
    (f"http://127.0.0.1:5108/web/shell-request?{_Q}", "origin"),
    (f"http://127.0.0.1/web/shell-request?{_Q}", "origin"),
    (f"https://127.0.0.1:5107/web/shell-request?{_Q}", "origin"),
    (f"http://192.168.0.2:5107/web/shell-request?{_Q}", "origin"),
    (f"https://evil.example/web/shell-request?{_Q}", "origin"),
    (f"file:///web/shell-request?{_Q}", "origin"),
    # Userinfo, an empty one too.
    (f"http://a@127.0.0.1:5107/web/shell-request?{_Q}", "userinfo"),
    (f"http://@127.0.0.1:5107/web/shell-request?{_Q}", "userinfo"),
    (f"http://:@127.0.0.1:5107/web/shell-request?{_Q}", "userinfo"),
    # Not the path as sent.
    (f"{_B}/web/shell-request/?{_Q}", "path"),
    (f"{_B}/web//shell-request?{_Q}", "path"),
    (f"{_B}/web/./shell-request?{_Q}", "path"),
    (f"{_B}/web/x/../shell-request?{_Q}", "path"),
    (f"{_B}/web/shell-request%2F?{_Q}", "path"),
    (f"{_B}/WEB/shell-request?{_Q}", "path"),
    (f"{_B}/shell-request?{_Q}", "path"),
    (f"{_B}/web/shell-requests?{_Q}", "path"),
    # A fragment, an empty one too.
    (f"{_B}/web/shell-request?{_Q}#", "fragment"),
    (f"{_B}/web/shell-request?{_Q}#x", "fragment"),
    # No query, a malformed one, a key twice, too long, too many fields.
    (f"{_B}/web/shell-request", "query"),
    (f"{_B}/web/shell-request?{_Q}&", "query"),
    (f"{_B}/web/shell-request?{_Q}&verb=checkUpdates", "query"),
    (f"{_B}/web/shell-request?{_Q}&ticket={_T}", "query"),
    (f"{_B}/web/shell-request?{_Q}&pad={'x' * 512}", "query"),
    (f"{_B}/web/shell-request?{_Q}&a=1&b=2", "query"),
    # No verb, or not one of them.
    (f"{_B}/web/shell-request?", "verb"),
    (f"{_B}/web/shell-request?request=r1&ticket={_T}", "verb"),
    (f"{_B}/web/shell-request?verb=PickFolder&request=r1&ticket={_T}", "verb"),
    (f"{_B}/web/shell-request?verb=CheckUpdates&request=r1&ticket={_T}",
     "verb"),
    (f"{_B}/web/shell-request?verb=openUrl&url=https://evil.example"
     f"&request=r1&ticket={_T}", "verb"),
    # Not exactly the verb's keys.
    (f"{_B}/web/shell-request?verb=checkUpdates&request=r1", "keys"),
    (f"{_B}/web/shell-request?verb=checkUpdates&ticket={_T}", "keys"),
    (f"{_B}/web/shell-request?{_Q}&on=1", "keys"),
    (f"{_B}/web/shell-request?verb=alwaysOnTop&request=r1&ticket={_T}",
     "keys"),
    (f"{_B}/web/shell-request?verb=openExternal&url=https://evil.example"
     f"&request=r1&ticket={_T}", "keys"),
    # A page never names the folder (SF-5a-2).
    (f"{_B}/web/shell-request?verb=pickFolder&path=C%3A%5CGames"
     f"&request=r1&ticket={_T}", "keys"),
    (f"{_B}/web/shell-request?verb=pickFolder&folder=..&request=r1"
     f"&ticket={_T}", "keys"),
    # An id the answer's script could not carry as it is.
    (f"{_B}/web/shell-request?verb=checkUpdates&request=&ticket={_T}",
     "request"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request={'x' * 65}"
     f"&ticket={_T}", "request"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request=r.1&ticket={_T}",
     "request"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request=r+1&ticket={_T}",
     "request"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request=%27%29%3B"
     f"&ticket={_T}", "request"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request=%C3%A9&ticket={_T}",
     "request"),
    # An argument off its values: never a URL.
    (f"{_B}/web/shell-request?verb=alwaysOnTop&on=2&request=r1&ticket={_T}",
     "argument"),
    (f"{_B}/web/shell-request?verb=alwaysOnTop&on=true&request=r1"
     f"&ticket={_T}", "argument"),
    (f"{_B}/web/shell-request?verb=alwaysOnTop&on=&request=r1&ticket={_T}",
     "argument"),
    (f"{_B}/web/shell-request?verb=openExternal"
     f"&target=https%3A%2F%2Fevil.example&request=r1&ticket={_T}",
     "argument"),
    (f"{_B}/web/shell-request?verb=openExternal&target=Discord&request=r1"
     f"&ticket={_T}", "argument"),
    # Not the ticket.
    (f"{_B}/web/shell-request?verb=checkUpdates&request=r1&ticket=",
     "ticket"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request=r1&ticket=wrong",
     "ticket"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request=r1&ticket={_T}x",
     "ticket"),
    (f"{_B}/web/shell-request?verb=checkUpdates&request=r1&ticket=%20{_T}",
     "ticket"),
]


@pytest.mark.parametrize("query, expected", VALID_SHELL_REQUESTS)
def test_a_shell_request_is_its_verb_id_arguments_and_ticket(query, expected):
    assert _checkShellRequest(f"{SHELL_URL}?{query}") == (expected, None)


@pytest.mark.parametrize("url, reason", REFUSED_SHELL_REQUESTS)
def test_anything_but_exactly_a_shell_request_is_refused(url, reason):
    assert _checkShellRequest(url) == (None, reason)


@pytest.mark.parametrize("pages", [
    # Another origin's page, layout/, the Designer's view of the Web UI, no
    # page at all, one of the two off the Web UI.
    ("http://localhost:5107/web/board",) * 2,
    ("http://127.0.0.1:5108/web/board",) * 2,
    ("http://127.0.0.1:5107/layout/x/index.html",) * 2,
    ("http://127.0.0.1:5107/web?view=designer",) * 2,
    ("http://127.0.0.1:5107/web/board?desktop=1&view=designer",) * 2,
    ("about:blank", ""),
    (PAGE_URL, "http://127.0.0.1:5107/layout/x/index.html"),
    ("http://127.0.0.1:5107/layout/x/index.html", PAGE_URL),
])
def test_a_shell_request_is_taken_from_this_servers_web_ui_only(pages):
    assert _checkShellRequest(f"{SHELL_URL}?{_Q}", pages) == (None, "page")


@pytest.mark.parametrize("pages", [
    (PAGE_URL, PAGE_URL), (PAGE_URL, "about:blank"), ("", PAGE_URL),
])
def test_the_pages_document_is_what_the_page_shows_or_loads(pages):
    request, reason = _checkShellRequest(f"{SHELL_URL}?{_Q}", pages)
    assert (request.verb, reason) == ("checkUpdates", None)


def test_the_ticket_is_checked_as_the_query_decodes_it():
    seen = []
    HostModule.ParseShellRequest(
        QUrl(f"{SHELL_URL}?verb=checkUpdates&request=r1&ticket=a%2Db"),
        (QUrl(PAGE_URL),), SHELL_SERVER, lambda value: seen.append(value))
    assert seen == ["a-b"]


SHAPED_SHELL_REQUESTS = [
    # This server's own origin: as the host loads it, the page's, another
    # name of this PC at its port; any case, the path encoded, no query.
    (f"{SHELL_URL}?{_Q}", True),
    ("http://127.0.0.1:5107/web/shell-request", True),
    ("http://127.0.0.1:5107/WEB/Shell-Request", True),
    ("http://127.0.0.1:5107/web/shell%2Drequest", True),
    ("http://127.0.0.1:5107/web/x/shell-request-lib", True),
    ("http://localhost:5107/web/shell-request", True),
    ("http://[::1]:5107/web/shell-request", True),
    # A ticket key, on any origin and scheme, however it is spelled.
    ("https://evil.example/x/shell-request/y?ticket=t", True),
    ("https://evil.example/shell-request?ticket", True),
    ("https://evil.example/shell-request?a=1&TICKET=t", True),
    ("https://evil.example/shell-request?%74icket=t", True),
    ("https://evil.example/shell-request?a=1;ticket=t", True),
    ("https://evil.example/shell-request?%20ticket%20=t", True),
    ("http://127.0.0.1:5108/web/shell-request?ticket=t", True),
    ("file:///c:/shell-request?ticket=t", True),
    (f"javascript:void('/shell-request?{_Q}')", True),
    # Elsewhere without one: an outside link as any.
    ("https://github.com/x/shell-request-lib", False),
    ("https://github.com/x/shell-request-lib?tab=readme", False),
    ("https://example.test/shell-request?tickets=1", False),
    ("https://example.test/shell-request?x=ticket", False),
    ("https://example.test/shell-request#ticket=t", False),
    ("http://127.0.0.1:5108/web/shell-request", False),
    ("file:///c:/shell-request", False),
    ("ms-calculator:shell-request", False),
    # No shell-request in the path: never, a ticket key or not.
    ("http://127.0.0.1:5107/web/board?x=shell-request", False),
    ("http://127.0.0.1:5107/web/shellrequest", False),
    ("https://example.test/post?text=shell-request", False),
    ("https://example.test/post?ticket=t", False),
]


@pytest.mark.parametrize("url, shaped", SHAPED_SHELL_REQUESTS)
def test_what_is_taken_as_a_shell_request(url, shaped):
    # Kept from the default browser, refused or run.
    assert HostModule.IsShellRequestShaped(
        QUrl(url), (QUrl(PAGE_URL), QUrl(PAGE_URL)), SHELL_SERVER) is shaped


def test_a_ticket_key_is_taken_before_the_lan_is_looked_up(monkeypatch):
    # The origin is checked only without a ticket key, and only for a path
    # naming shell-request: most URLs never cost the LAN's lookup.
    asked = []
    monkeypatch.setattr(HostModule, "_IsServerOrigin",
                        lambda url, pages: asked.append(url) or False)
    for url in ("https://evil.example/shell-request?ticket=t",
                "https://example.test/post?text=a"):
        HostModule.IsShellRequestShaped(QUrl(url), (QUrl(PAGE_URL),),
                                        SHELL_SERVER)
    assert asked == []
    assert HostModule.IsShellRequestShaped(
        QUrl("https://github.com/x/shell-request-lib"), (QUrl(PAGE_URL),),
        SHELL_SERVER) is False
    assert len(asked) == 1


def _openedInClick(url, clicked=True, page=None):
    page = _pageAsking() if page is None else page
    HostModule._PanelPage._OpenOutside(
        page, SimpleNamespace(requestedUrl=lambda: QUrl(url),
                              isUserInitiated=lambda: clicked))
    return page


def test_a_shell_request_in_a_click_goes_to_the_host_not_the_browser(
        monkeypatch, issuedTicket, serverAt5107, caplog):
    opened = _handedOutside(monkeypatch)
    url = (f"{SHELL_URL}?verb=openExternal&target=discord&request=r1"
           f"&ticket={issuedTicket}")

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick(url)

    assert page.shell == [ShellRequest("openExternal", "r1",
                                       {"target": "discord"})]
    assert opened == [] and page.designer == []
    assert "takes the shell request openExternal" in caplog.text
    assert issuedTicket not in caplog.text
    assert "request=r1" not in caplog.text


@pytest.mark.parametrize("url, reason", REFUSED_SHELL_REQUESTS + [
    (f"javascript:void('/shell-request?{_Q}')", "origin"),
    ("https://evil.example/shell-request?ticket=t", "origin"),
    ("http://localhost:5107/web/shell-request", "origin"),
])
def test_a_refused_shell_request_reaches_neither_the_browser_nor_the_log(
        monkeypatch, issuedTicket, serverAt5107, caplog, url, reason):
    opened = _handedOutside(monkeypatch)

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick(url)

    assert opened == [] and page.shell == [] and page.designer == []
    assert f"refused a shell request ({reason})" in caplog.text
    assert issuedTicket not in caplog.text
    assert "shell-request" not in caplog.text


@pytest.mark.parametrize("url", [
    "https://github.com/x/shell-request-lib",
    "https://github.com/x/shell-request-lib/issues?q=is%3Aopen",
])
def test_an_outside_link_naming_shell_request_is_a_link_as_any(
        monkeypatch, issuedTicket, serverAt5107, caplog, url):
    opened = _handedOutside(monkeypatch)

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick(url)

    assert opened == [url] and page.shell == []
    assert "refused a shell request" not in caplog.text


def test_an_unknown_scheme_naming_shell_request_is_refused_as_any(
        monkeypatch, issuedTicket, serverAt5107, caplog):
    opened = _handedOutside(monkeypatch)

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick("ms-calculator:shell-request")

    assert opened == [] and page.shell == []
    assert "refused to open ms-calculator:" in caplog.text
    assert "shell-request" not in caplog.text


def test_a_shell_request_no_click_asked_for_is_refused(
        monkeypatch, issuedTicket, serverAt5107, caplog):
    opened = _handedOutside(monkeypatch)

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick(f"{SHELL_URL}?{_Q}", clicked=False)

    assert opened == [] and page.shell == []
    assert "refused a shell request (click)" in caplog.text
    assert issuedTicket not in caplog.text


@pytest.mark.parametrize("pageUrl", [
    "http://127.0.0.1:5107/web?view=designer",
    "http://127.0.0.1:5107/layout/x/index.html",
])
def test_a_shell_request_of_a_page_off_the_web_ui_is_refused(
        monkeypatch, issuedTicket, serverAt5107, caplog, pageUrl):
    opened = _handedOutside(monkeypatch)

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick(f"{SHELL_URL}?{_Q}", page=_pageAsking(pageUrl))

    assert opened == [] and page.shell == []
    assert "refused a shell request (page)" in caplog.text


def test_no_ticket_issued_refuses_every_shell_request(
        monkeypatch, serverAt5107, caplog):
    # Headless, or before the page has read it: none matches.
    monkeypatch.setattr(WebAccess, "_shellTicket", None)
    opened = _handedOutside(monkeypatch)

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick(f"{SHELL_URL}?{_Q}")

    assert opened == [] and page.shell == []
    assert "refused a shell request (ticket)" in caplog.text


def test_a_check_that_raises_refuses_and_logs_no_message(
        monkeypatch, issuedTicket, serverAt5107, caplog):
    opened = _handedOutside(monkeypatch)

    def raising(url, *args):
        raise ValueError(url.toString())
    monkeypatch.setattr(HostModule, "ParseShellRequest", raising)

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        page = _openedInClick(f"{SHELL_URL}?{_Q}")

    assert opened == [] and page.shell == []
    assert "refused a shell request (check failed: ValueError)" in caplog.text
    assert issuedTicket not in caplog.text


def test_no_navigation_goes_to_a_shell_request(buildHost, issuedTicket,
                                               caplog):
    # Only a new window in a click is one; the main frame (it is in /web,
    # which would let it through) and a frame never go there.
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    served = len(rig.requests)
    port = rig.servingPort
    query = f"verb=checkUpdates&request=r1&ticket={issuedTicket}"

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        rig.js("void (location.href = "
               + json.dumps(f"/web/shell-request?{query}") + ")")
        _waitUntil(lambda: False, timeout=1.0)
    assert rig.js("window.__loadId") == document
    assert len(rig.requests) == served
    assert rig.page.refusedLast
    assert "refused a navigation to a shell request" in caplog.text
    assert issuedTicket not in caplog.text
    assert not [seen for seen in SEEN if "shell-request" in seen.path]

    N = QWebEnginePage.NavigationType
    kinds = (N.NavigationTypeLinkClicked, N.NavigationTypeOther,
             N.NavigationTypeRedirect, N.NavigationTypeFormSubmitted,
             N.NavigationTypeBackForward, N.NavigationTypeTyped,
             N.NavigationTypeReload)
    urls = (f"http://127.0.0.1:{port}/web/shell-request?{query}",
            f"http://127.0.0.1:{port}/web/Shell-Request",
            f"https://evil.example/shell-request?{query}")
    for kind in kinds:
        for url in urls:
            rig.page.refusedLast = False
            assert rig.page.acceptNavigationRequest(
                QUrl(url), kind, True) is False, (url, kind)
            assert rig.page.refusedLast
            # A frame's: refused too, the main frame's document untouched.
            rig.page.refusedLast = False
            assert rig.page.acceptNavigationRequest(
                QUrl(url), kind, False) is False, (url, kind)
            assert not rig.page.refusedLast

    # An outside page naming shell-request is not one (IsShellRequestShaped):
    # a frame of it passes as any outside frame does, and the main frame
    # keeps to the Web UI -- refused as leaving it, logged by its scheme and
    # host only.
    outside = "https://github.com/x/shell-request-lib?tab=readme"
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        assert rig.page.acceptNavigationRequest(
            QUrl(outside), N.NavigationTypeLinkClicked, False) is True
        assert rig.page.acceptNavigationRequest(
            QUrl(outside), N.NavigationTypeLinkClicked, True) is False
    assert "refused a navigation to a shell request" not in caplog.text
    assert "the Web UI of this server for https://github.com" in caplog.text
    assert "tab=readme" not in caplog.text
    assert "shell-request" not in caplog.text


def test_a_refused_url_is_logged_by_its_scheme_and_host_only(
        monkeypatch, buildHost, caplog):
    # Its query may carry a ticket, a token, the operator's text.
    opened = _handedOutside(monkeypatch)
    secret = "Not_For_The_Log_0123"
    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        # No http(s): not opened.
        _openedInClick(f"ms-settings:x?ticket={secret}")
        _openedInClick(f"file:///c:/x/y.html?ticket={secret}")
    assert opened == []
    assert "refused to open ms-settings:" in caplog.text
    assert "refused to open file:" in caplog.text

    rig = _shown(buildHost)
    port = rig.servingPort
    N = QWebEnginePage.NavigationType
    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        # A frame of this server's own origin; the main frame off the Web UI.
        assert rig.page.acceptNavigationRequest(
            QUrl(f"http://127.0.0.1:{port}/layout/x.html?ticket={secret}"),
            N.NavigationTypeLinkClicked, False) is False
        assert rig.page.acceptNavigationRequest(
            QUrl(f"https://example.test/a/b?ticket={secret}"),
            N.NavigationTypeLinkClicked, True) is False
    assert "server's own origin, http://127.0.0.1" in caplog.text
    assert "this server for https://example.test" in caplog.text
    assert secret not in caplog.text
    assert "/layout/" not in caplog.text and "/a/b" not in caplog.text


class _Shell:
    """Stands in for AutonaShellOperations (the host's ``shell``): what it
    was asked, in order; its answer and its state."""

    def __init__(self):
        self.asked = []
        self.state = {"alwaysOnTop": False}
        self.answer = None
        # True: it answers later, through the `answer` it was handed (kept
        # in `answers`), as pickFolder does.
        self.later = False
        self.answers = []

    def ShellRequest(self, verb, args, answer=None):
        self.asked.append((verb, args))
        if self.later:
            self.answers.append(answer)
            return None
        if self.answer is not None:
            return self.answer
        return {"ok": True, "verb": verb, **args}

    def ShellState(self):
        return dict(self.state)


def _shellShown(buildHost, shell=None, **kwargs):
    rig = buildHost(shell=_Shell() if shell is None else shell, **kwargs)
    rig.shell = rig.host._shell
    rig.host.ShowPanel("shell-check")
    rig.reveal()
    rig.waitLoaded()
    return rig


def _clickInto(view, page):
    # _clickView's, for any view: until the page has seen a click.
    def clicked():
        QTest.mouseClick(view.focusProxy(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(50, 50))
        return _waitUntil(lambda: _pageJs(page, "window.__clicks") > 0,
                          timeout=0.5)
    assert _waitUntil(clicked), "the page saw no click"


def test_a_click_runs_the_shell_request_once_and_answers_the_page(
        buildHost, monkeypatch, issuedTicket, caplog):
    opened = _handedOutside(monkeypatch)
    rig = _shellShown(buildHost)
    document = rig.js("window.__loadId")
    served = len(rig.requests)
    url = (f"http://127.0.0.1:{rig.servingPort}/web/shell-request"
           f"?verb=alwaysOnTop&on=1&request=r1&ticket={issuedTicket}")

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        _clickOpening(rig, url)
        assert _waitUntil(lambda: rig.js("window.__shellResults.length"))
        _waitUntil(lambda: False, timeout=0.5)

    assert rig.shell.asked == [("alwaysOnTop", {"on": "1"})]
    assert rig.js("window.__shellResults") == [
        ["r1", {"ok": True, "verb": "alwaysOnTop", "on": "1"}]]
    # Nothing opened, loaded or asked of the server.
    assert opened == []
    assert rig.js("window.__loadId") == document
    assert len(rig.requests) == served and rig.loads == [True]
    assert not [seen for seen in SEEN if "shell-request" in seen.path]
    assert issuedTicket not in caplog.text


def test_script_alone_runs_no_shell_request(buildHost, monkeypatch,
                                            issuedTicket):
    opened = _handedOutside(monkeypatch)
    rig = _shellShown(buildHost)
    url = (f"http://127.0.0.1:{rig.servingPort}/web/shell-request"
           f"?verb=checkUpdates&request=r1&ticket={issuedTicket}")

    rig.js(f"void window.open({json.dumps(url)}, '_blank', 'noopener')")
    rig.js(f"void window.open({json.dumps(url)})")
    # The click after them says they have had their chance.
    _clickOpening(rig, "https://example.test/clicked")

    assert _waitUntil(lambda: opened)
    assert opened == ["https://example.test/clicked"]
    assert rig.shell.asked == []
    assert rig.js("window.__shellResults") == []


def test_a_click_without_the_ticket_runs_nothing_and_opens_nothing(
        buildHost, monkeypatch, issuedTicket, caplog):
    opened = _handedOutside(monkeypatch)
    rig = _shellShown(buildHost)
    port = rig.servingPort
    asked = (
        (f"http://127.0.0.1:{port}/web/shell-request"
         "?verb=checkUpdates&request=r1", "keys"),
        (f"http://127.0.0.1:{port}/web/shell-request"
         "?verb=checkUpdates&request=r2&ticket=wrong", "ticket"),
        (f"http://localhost:{port}/web/shell-request"
         f"?verb=checkUpdates&request=r3&ticket={issuedTicket}", "origin"),
    )

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        for url, _reason in asked:
            _clickOpening(rig, url)
        _clickOpening(rig, "https://example.test/clicked")
        assert _waitUntil(lambda: opened)

    assert opened == ["https://example.test/clicked"]
    assert rig.shell.asked == []
    assert rig.js("window.__shellResults") == []
    for _url, reason in asked:
        assert f"refused a shell request ({reason})" in caplog.text
    assert issuedTicket not in caplog.text


def _relayTheApp(monkeypatch):
    # The real guard and routes behind the panel server's relayed paths
    # (RELAY_PREFIX), as werkzeug would serve them on that port.
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


def test_the_shared_view_reads_the_ticket_and_its_click_runs_the_verb(
        buildHost, monkeypatch, caplog):
    # The whole way, through the real guard: the shared view's fetch reads
    # the ticket (issued then), the click's window carries it, the verb runs,
    # the answer reaches the page. The Designer's view on the same profile
    # reads none (403), and a click there opening a request -- the ticket and
    # all -- is no window of the host's.
    _relayTheApp(monkeypatch)
    monkeypatch.setattr(WebAccess, "_shellTicket", None)
    opened = _handedOutside(monkeypatch)
    rig = _shellShown(buildHost)
    port = rig.servingPort

    rig.js("window.__session = null, fetch('/api/v1/shell-session')"
           ".then(function (r) { return r.json().then(function (b) {"
           " window.__session = [r.status, b.data ? b.data.ticket : null];"
           " }); }), 0")
    assert _waitUntil(lambda: rig.js("window.__session"))
    status, ticket = rig.js("window.__session")
    assert status == 200
    assert WebAccess.ShellTicketMatches(ticket)
    assert _seen("/api/v1/shell-session") == [WebAccess._shellSecret]

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        rig.js("window.__clicks = 0, window.addEventListener('click',"
               " function () { window.__clicks += 1; window.open("
               "location.origin + '/web/shell-request?' + new URLSearchParams("
               "{verb: 'checkUpdates', request: 'e2e',"
               " ticket: window.__session[1]}), '_blank', 'noopener'); },"
               " {once: true}), 0")
        _clickView(rig)
        assert _waitUntil(lambda: rig.js("window.__shellResults.length"))
    assert rig.shell.asked == [("checkUpdates", {})]
    assert rig.js("window.__shellResults") == [
        ["e2e", {"ok": True, "verb": "checkUpdates"}]]
    assert ticket not in caplog.text

    url = (f"http://127.0.0.1:{port}/web/shell-request"
           f"?verb=openExternal&target=discord&request=d1&ticket={ticket}")
    other = _otherView(f"http://127.0.0.1:{port}/web?view=designer")
    try:
        _pageJs(other.page(),
                "window.__refused = null, fetch('/api/v1/shell-session')"
                ".then(function (r) { window.__refused = r.status; }), 0")
        assert _waitUntil(lambda: _pageJs(other.page(), "window.__refused"))
        assert _pageJs(other.page(), "window.__refused") == 403
        _pageJs(other.page(),
                "window.__clicks = 0, window.addEventListener('click',"
                " function () { window.__clicks += 1; window.open("
                + json.dumps(url) + ", '_blank', 'noopener'); },"
                " {once: true}), 0")
        _clickInto(other, other.page())
        _waitUntil(lambda: False, timeout=0.5)
    finally:
        other.close()
        other.deleteLater()
        QCoreApplication.sendPostedEvents(other, QEvent.Type.DeferredDelete)
    assert _seen("/api/v1/shell-session") == [WebAccess._shellSecret, None]
    assert rig.shell.asked == [("checkUpdates", {})]
    assert opened == []


def test_a_sandboxed_frame_reads_no_ticket_and_its_click_runs_nothing(
        buildHost, monkeypatch, caplog):
    # A frame the Web UI might make for another party's HTML, sandboxed:
    # its fetch carries no secret (an opaque origin), so no ticket is
    # issued, and the window its click opens is refused.
    _relayTheApp(monkeypatch)
    monkeypatch.setattr(WebAccess, "_shellTicket", None)
    opened = _handedOutside(monkeypatch)
    rig = _shellShown(buildHost)
    target = (f"http://127.0.0.1:{rig.servingPort}/web/shell-request"
              "?verb=checkUpdates&request=f1")
    frame = ("<style>html,body{margin:0;height:100%}</style><script>"
             "fetch('/api/v1/shell-session').catch(function () {});"
             "document.addEventListener('click', function () {"
             " parent.postMessage('clicked', '*');"
             f" window.open({json.dumps(target)}, '_blank', 'noopener');"
             " }, {once: true});</script>")

    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        rig.js("window.__clicks = 0, window.addEventListener('message',"
               " function (e) { if (e.data === 'clicked') {"
               " window.__clicks += 1; } }),"
               " (function (html) { var f = document.createElement('iframe');"
               " f.setAttribute('sandbox', 'allow-scripts allow-popups');"
               " f.style.cssText = 'position:fixed;left:0;top:0;width:100%;"
               "height:100%;border:0'; f.srcdoc = html;"
               " document.body.appendChild(f); return 0; })("
               + json.dumps(frame) + ")")
        _waitSeen("/api/v1/shell-session")
        _clickView(rig)
        assert _waitUntil(lambda: "refused a shell request" in caplog.text)

    (record,) = _records("/api/v1/shell-session")
    assert record.shell is None and record.site != "same-origin"
    assert WebAccess._shellTicket is None
    assert "refused a shell request (keys)" in caplog.text
    assert rig.shell.asked == [] and opened == []


def test_the_verb_is_run_from_the_event_loop(buildHost):
    # Not inside Chromium's new-window call (pitfalls #9).
    rig = _shellShown(buildHost)

    rig.page.shellRequested.emit(ShellRequest("checkUpdates", "r1", {}))

    assert rig.shell.asked == []
    assert _waitUntil(lambda: rig.shell.asked == [("checkUpdates", {})])
    assert _waitUntil(lambda: rig.js("window.__shellResults.length"))


def test_the_answer_goes_into_the_page_as_data(buildHost):
    rig = _shellShown(buildHost)
    rig.shell.answer = {"ok": False,
                        "error": "x'); window.__pwned = 1; ('</script>"}
    odd = "a'b\"c</script>"

    rig.page.shellRequested.emit(ShellRequest("checkUpdates", odd, {}))

    assert _waitUntil(lambda: rig.js("window.__shellResults.length"))
    assert rig.js("window.__shellResults") == [[odd, rig.shell.answer]]
    assert rig.js("window.__pwned === undefined") is True


@pytest.mark.parametrize("answer, expected", [
    (ValueError("x"), {"ok": False, "error": "failed"}),
    ({"ok": True, "value": object()}, {"ok": False, "error": "failed"}),
])
def test_a_verb_that_fails_answers_that_it_failed(buildHost, answer,
                                                  expected):
    rig = _shellShown(buildHost)
    if isinstance(answer, Exception):
        def raising(verb, args):
            raise answer
        rig.shell.ShellRequest = raising
    else:
        rig.shell.answer = answer

    rig.page.shellRequested.emit(ShellRequest("checkUpdates", "r1", {}))

    assert _waitUntil(lambda: rig.js("window.__shellResults.length"))
    assert rig.js("window.__shellResults") == [["r1", expected]]


def test_a_host_without_a_shell_answers_unavailable(buildHost):
    rig = _shown(buildHost)

    rig.page.shellRequested.emit(ShellRequest("checkUpdates", "r1", {}))

    assert _waitUntil(lambda: rig.js("window.__shellResults.length"))
    assert rig.js("window.__shellResults") == [
        ["r1", {"ok": False, "error": "unavailable"}]]
    assert rig.js("window.__shellStates") == []


def test_an_answer_for_a_frozen_page_is_held_until_it_thaws(buildHost):
    rig = _shellShown(buildHost)
    document = rig.js("window.__loadId")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.page.shellRequested.emit(ShellRequest("checkUpdates", "r1", {}))
    # A newer one of the verb replaces it; another verb's stays.
    rig.page.shellRequested.emit(ShellRequest("checkUpdates", "r2", {}))
    rig.page.shellRequested.emit(
        ShellRequest("openExternal", "r3", {"target": "discord"}))
    assert _waitUntil(lambda: len(rig.shell.asked) == 3)
    assert sorted(rig.host._shellResults) == ["checkUpdates", "openExternal"]
    assert rig.state() == FROZEN

    rig.reveal()

    assert _waitUntil(lambda: rig.js("window.__shellResults.length") == 2)
    assert rig.js("window.__shellResults") == [
        ["r2", {"ok": True, "verb": "checkUpdates"}],
        ["r3", {"ok": True, "verb": "openExternal", "target": "discord"}]]
    assert rig.js("window.__loadId") == document
    assert rig.host._shellResults == {}


def test_an_answer_held_for_a_discarded_page_is_dropped(buildHost):
    rig = _shellShown(buildHost, discardAfterMs=1000)
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.page.shellRequested.emit(ShellRequest("checkUpdates", "r1", {}))
    assert _waitUntil(lambda: rig.shell.asked)
    assert rig.host._shellResults

    assert _waitUntil(lambda: rig.state() == DISCARDED)
    assert rig.host._shellResults == {}
    rig.reveal()
    rig.waitLoaded(count=2)
    _waitUntil(lambda: False, timeout=0.5)

    assert rig.js("window.__shellResults") == []


def test_an_answer_for_a_document_gone_since_is_dropped(buildHost):
    rig = _shellShown(buildHost)
    asked = rig.host._documents

    rig.js("void location.reload()")
    rig.waitLoaded(count=2)
    rig.host._RunShellRequest(ShellRequest("checkUpdates", "r1", {}), asked)

    assert rig.shell.asked == [("checkUpdates", {})]
    assert rig.host._shellResults == {}
    _waitUntil(lambda: False, timeout=0.3)
    assert rig.js("window.__shellResults") == []


def test_an_answer_the_shell_gives_later_reaches_the_page_that_asked(
        buildHost):
    # pickFolder answers once its dialog closed and the folder is probed:
    # through the `answer` the host handed it, for the document that asked.
    rig = _shellShown(buildHost)
    rig.shell.later = True
    rig.host._RunShellRequest(ShellRequest("pickFolder", "p1", {}),
                              rig.host._documents)
    assert len(rig.shell.answers) == 1
    _waitUntil(lambda: False, timeout=0.2)
    assert rig.js("window.__shellResults") == []

    rig.shell.answers[0]({"ok": True, "status": "cancelled"})
    assert _waitUntil(lambda: rig.js("window.__shellResults") == [
        ["p1", {"ok": True, "status": "cancelled"}]])


def test_a_later_answer_once_the_view_is_gone_or_the_app_stops_is_dropped(
        qapp, monkeypatch, caplog):
    # By the time pickFolder answers, the main window may have taken the
    # host with it, or the shutdown begun: nothing is done (no RuntimeError
    # from the deleted widget), nothing held.
    from qtpy import shiboken

    from src.Helpers import AutonaQtHelper

    shell = _Shell()
    shell.later = True
    stopping = AutonaWebPanelHost(shell=shell)
    stopping._RunShellRequest(ShellRequest("pickFolder", "p1", {}),
                              stopping._documents)
    gone = AutonaWebPanelHost(shell=shell)
    gone._RunShellRequest(ShellRequest("pickFolder", "p2", {}),
                          gone._documents)
    alive = AutonaWebPanelHost(shell=shell)
    alive._RunShellRequest(ShellRequest("pickFolder", "p3", {}),
                           alive._documents)
    later, lateGone, lateAlive = shell.answers

    shiboken.delete(gone)
    with caplog.at_level(logging.INFO, logger=HostModule.__name__):
        lateGone({"ok": True, "status": "cancelled"})
        monkeypatch.setattr(AutonaQtHelper, "gui_unavailable_reason",
                            lambda: "the app is shutting down")
        later({"ok": True, "status": "cancelled"})
        monkeypatch.undo()
    assert caplog.text.count("is dropped: the view is gone or the app is "
                             "shutting down") == 2
    assert stopping._shellResults == {}

    # Alive and running: held for its page (no view yet to hand it to).
    lateAlive({"ok": True, "status": "cancelled"})
    assert alive._shellResults == {"pickFolder": (
        "p3", json.dumps({"ok": True, "status": "cancelled"}))}
    for host in (stopping, alive):
        host.deleteLater()
        QCoreApplication.sendPostedEvents(host, QEvent.Type.DeferredDelete)


def test_the_shells_state_reaches_the_page_on_its_load_and_each_change(
        buildHost):
    rig = _shellShown(buildHost)
    assert _waitUntil(lambda: rig.js("window.__shellStates.length") == 1)
    assert rig.js("window.__shellStates") == [{"alwaysOnTop": False}]

    rig.shell.state = {"alwaysOnTop": True}
    rig.host.PublishShellState()
    assert _waitUntil(lambda: rig.js("window.__shellStates.length") == 2)

    # While frozen: told once it thaws, once.
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.shell.state = {"alwaysOnTop": False}
    rig.host.PublishShellState()
    rig.host.PublishShellState()
    rig.reveal()
    assert _waitUntil(lambda: rig.js("window.__shellStates.length") == 3)
    _waitUntil(lambda: False, timeout=0.3)
    assert rig.js("window.__shellStates") == [
        {"alwaysOnTop": False}, {"alwaysOnTop": True}, {"alwaysOnTop": False}]

    # A new document is told again.
    rig.js("void location.reload()")
    rig.waitLoaded(count=2)
    assert _waitUntil(lambda: rig.js("window.__shellStates.length") == 1)


def test_a_state_the_shell_cannot_read_is_logged_and_not_told(
        buildHost, caplog):
    # The page hears nothing then (no made-up state), and is not stuck: its
    # answers still reach it, and the next change is told.
    shell = _Shell()
    failing = {"now": True}
    readable = shell.ShellState

    def state():
        if failing["now"]:
            raise RuntimeError("not readable")
        return readable()
    shell.ShellState = state

    with caplog.at_level(logging.ERROR, logger=HostModule.__name__):
        rig = _shellShown(buildHost, shell=shell)
        _waitUntil(lambda: False, timeout=0.3)
    assert "The shell's state could not be read" in caplog.text
    assert rig.js("window.__shellStates") == []

    rig.host._RunShellRequest(ShellRequest("checkUpdates", "r1", {}),
                              rig.host._documents)
    assert _waitUntil(lambda: rig.js("window.__shellResults") == [
        ["r1", {"ok": True, "verb": "checkUpdates"}]])

    failing["now"] = False
    rig.host.PublishShellState()
    assert _waitUntil(lambda: rig.js("window.__shellStates") == [
        {"alwaysOnTop": False}])


def _alwaysOnTopWindowClass():
    """A top-level QWidget with the Window's real always-on-top methods
    (read out of src/Autona.py, which cannot be imported: pitfalls
    #1)."""
    cls = ast.parse("class Window(QWidget):\n    pass\n").body[0]
    cls.body = [_windowMethod(name) for name in
                ("SetAlwaysOnTop", "IsAlwaysOnTop")]
    namespace = {"QWidget": QWidget, "Qt": Qt}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[cls], type_ignores=[])), "src/Autona.py", "exec"), namespace)
    return namespace["Window"]


def test_always_on_top_asked_in_a_click_keeps_the_view_and_answers(
        buildHost, monkeypatch, issuedTicket):
    # The real shell adapter and the Window's own methods: the window's bool
    # is set (the state since SF-5b-3: no Qt menu check), Qt hides and shows
    # the window for the flag, and the view -- the same document -- gets the
    # answer and the state.
    from src.AutonaShellOperations import AutonaShellOperations

    opened = _handedOutside(monkeypatch)
    operations = AutonaShellOperations()
    rig = buildHost(shell=operations, windowClass=_alwaysOnTopWindowClass())
    window = rig.window
    # As Window._BuildUi sets them.
    window._alwaysOnTop = False
    window._webPanelHost = rig.host
    operations.UiMounted(window)
    rig.host.ShowPanel("shell-check")
    rig.reveal()
    rig.waitLoaded()
    document = rig.js("window.__loadId")
    assert _waitUntil(lambda: rig.js("window.__shellStates") == [
        {"alwaysOnTop": False}])

    for on, expected in (("1", True), ("0", False)):
        answered = rig.js("window.__shellResults.length")
        _clickOpening(rig, f"http://127.0.0.1:{rig.servingPort}"
                           f"/web/shell-request?verb=alwaysOnTop&on={on}"
                           f"&request=t{on}&ticket={issuedTicket}")
        assert _waitUntil(
            lambda: rig.js("window.__shellResults.length") == answered + 1)
        assert rig.js("window.__shellResults")[-1] == [
            f"t{on}", {"ok": True, "alwaysOnTop": expected}]
        assert window.IsAlwaysOnTop() is expected
        assert bool(window.windowFlags()
                    & Qt.WindowType.WindowStaysOnTopHint) is expected
        assert window.isVisible()
        assert rig.js("window.__shellStates")[-1] == {"alwaysOnTop": expected}
        assert rig.js("window.__loadId") == document
        assert rig.state() == ACTIVE

    # Set from the window itself, the page is told too; set to what it is
    # already, nothing is told again.
    window.SetAlwaysOnTop(True)
    assert _waitUntil(lambda: rig.js("window.__shellStates")[-1] == {
        "alwaysOnTop": True})
    told = rig.js("window.__shellStates.length")
    window.SetAlwaysOnTop(True)
    assert not _waitUntil(
        lambda: rig.js("window.__shellStates.length") > told, timeout=0.5)
    assert rig.loads == [True]
    assert opened == []


def test_a_folder_picked_in_a_click_answers_the_page_without_its_path(
        buildHost, monkeypatch, issuedTicket, tmp_path):
    # The real shell adapter in the real view: the click's window.open is
    # taken, the native dialog (patched) opened once over the main window
    # from the event loop -- not inside Chromium's call (pitfalls #9)
    # -- and the page gets the pick, not the folder.
    import inspect

    from src.AutonaShellOperations import AutonaShellOperations
    from src.core import AssetPackService as assets

    folder = str(tmp_path / "private-folder-7Q")
    stacks = []

    def dialog():
        stacks.append([frame.function for frame in inspect.stack()])
        return folder

    from src.Helpers import AutonaQtHelper
    from src.core.TaskExecutor import TaskExecutor

    # The probe's lane and the GUI thread's executor, as the app has them.
    lane = TaskExecutor()
    monkeypatch.setattr(TaskExecutor, "instance", lane)
    monkeypatch.setattr(AutonaQtHelper, "gui_executor",
                        AutonaQtHelper.GuiExecutor())
    opened = _handedOutside(monkeypatch)
    calls = _folderDialog(monkeypatch, dialog)
    held = _pickService(monkeypatch)
    operations = AutonaShellOperations()
    rig = _shellShown(buildHost, shell=operations)
    operations.UiMounted(rig.window)
    document = rig.js("window.__loadId")

    _clickOpening(rig, f"http://127.0.0.1:{rig.servingPort}"
                       f"/web/shell-request?verb=pickFolder&request=p1"
                       f"&ticket={issuedTicket}")
    assert _waitUntil(lambda: rig.js("window.__shellResults.length"))
    _waitUntil(lambda: False, timeout=0.3)

    assert [call[0] for call in calls] == [rig.window]
    assert held == [folder]
    assert held.threads[0] is not threading.main_thread()
    assert lane.shutdown(WAIT_SECS)
    assert "_RunShellRequest" in stacks[0]
    assert "_OpenOutside" not in stacks[0]
    results = rig.js("window.__shellResults")
    assert results == [["p1", {
        "ok": True, "status": "picked", "pick": PICK_ID, "games": ["aaa"],
        "conflicts": [], "expiresInSec": assets.PICK_EXPIRY_SECS}]]
    assert "private-folder" not in json.dumps(results)
    assert opened == []
    assert rig.js("window.__loadId") == document


class _WindowStandIn:
    """The main window as AutonaShellOperations sees it."""

    def __init__(self):
        self.on = False
        self.set = []

    def SetAlwaysOnTop(self, on):
        self.set.append(on)
        self.on = on

    def IsAlwaysOnTop(self):
        return self.on


def _operations(window=None):
    from src.AutonaShellOperations import AutonaShellOperations

    operations = AutonaShellOperations()
    if window is not None:
        operations.UiMounted(window)
    return operations


def test_the_shell_sets_always_on_top_as_the_menu_does():
    window = _WindowStandIn()
    operations = _operations(window)

    assert operations.ShellState() == {"alwaysOnTop": False}
    assert operations.ShellRequest("alwaysOnTop", {"on": "1"}) == {
        "ok": True, "alwaysOnTop": True}
    assert operations.ShellState() == {"alwaysOnTop": True}
    assert operations.ShellRequest("alwaysOnTop", {"on": "0"}) == {
        "ok": True, "alwaysOnTop": False}
    for bad in ({}, {"on": "2"}, {"on": 1}, {"on": True}):
        assert operations.ShellRequest("alwaysOnTop", bad) == {
            "ok": False, "error": "bad_argument"}
    assert window.set == [True, False]
    # Before the main window: nothing to set or tell.
    assert _operations().ShellRequest("alwaysOnTop", {"on": "1"}) == {
        "ok": False, "error": "not_ready"}
    assert _operations().ShellState() == {"alwaysOnTop": False}


def test_the_shell_starts_the_update_check_as_the_menu_does(monkeypatch):
    from src.AutonaUpdater import AutonaUpdater

    checks = []
    monkeypatch.setattr(AutonaUpdater, "instance", SimpleNamespace(
        CheckForUpdates=lambda silent: checks.append(silent)))
    assert _operations().ShellRequest("checkUpdates", {}) == {"ok": True}
    # Not silent: the updater's own dialog answers the operator.
    assert checks == [False]

    monkeypatch.setattr(AutonaUpdater, "instance", None)
    assert _operations().ShellRequest("checkUpdates", {}) == {
        "ok": False, "error": "not_ready"}


def test_a_check_still_running_answers_busy_and_starts_nothing(monkeypatch):
    """The updater starts nothing while its check thread runs (the
    startup's silent check, a menu check asked before). The shell reads that
    thread and says so instead of "ok"; the updater is not changed."""
    from src.AutonaUpdater import AutonaUpdater

    thread = SimpleNamespace(running=True)
    thread.isRunning = lambda: thread.running
    checks = []
    updater = AutonaUpdater()
    # The attribute the shell reads is the updater's own (renaming it there
    # would leave the shell reading nothing).
    assert "_check_thread" in vars(updater)
    updater._check_thread = thread
    monkeypatch.setattr(updater, "CheckForUpdates",
                        lambda silent: checks.append(silent))
    monkeypatch.setattr(AutonaUpdater, "instance", updater)
    operations = _operations()

    assert operations.ShellRequest("checkUpdates", {}) == {
        "ok": False, "error": "busy"}
    assert checks == []

    # Finished: the next ask starts a check.
    thread.running = False
    assert operations.ShellRequest("checkUpdates", {}) == {"ok": True}
    assert checks == [False]


def test_the_shell_opens_its_own_outside_addresses_only(monkeypatch):
    from src import AutonaShellOperations as ShellModule
    from src.AutonaExternalLinks import BUSINESS_INQUIRY_URL, DISCORD_URL

    opened = []
    works = {"value": True}
    monkeypatch.setattr(ShellModule, "QDesktopServices", SimpleNamespace(
        openUrl=lambda url: opened.append(url.toString()) or works["value"]))
    operations = _operations()

    assert operations.ShellRequest("openExternal", {"target": "discord"}) == {
        "ok": True}
    assert operations.ShellRequest("openExternal", {"target": "inquiry"}) == {
        "ok": True}
    assert opened == [DISCORD_URL, BUSINESS_INQUIRY_URL]
    for bad in ({}, {"target": "https://evil.example"}, {"target": "Discord"},
                {"target": ""}):
        assert operations.ShellRequest("openExternal", bad) == {
            "ok": False, "error": "bad_argument"}
    assert len(opened) == 2

    works["value"] = False
    assert operations.ShellRequest("openExternal", {"target": "discord"}) == {
        "ok": False, "error": "open_failed"}
    assert operations.ShellRequest("openUrl", {}) == {
        "ok": False, "error": "unknown_verb"}


def test_every_verb_the_view_takes_is_one_the_shell_runs(monkeypatch):
    from src import AutonaShellOperations as ShellModule
    from src.AutonaExternalLinks import EXTERNAL_LINKS
    from src.AutonaUpdater import AutonaUpdater

    monkeypatch.setattr(AutonaUpdater, "instance", SimpleNamespace(
        CheckForUpdates=lambda silent: None))
    monkeypatch.setattr(ShellModule, "QDesktopServices", SimpleNamespace(
        openUrl=lambda url: True))
    _folderDialog(monkeypatch, "")
    _pickService(monkeypatch)
    operations = _operations(_WindowStandIn())
    unused = []
    for verb, arguments in HostModule.SHELL_VERBS.items():
        for name, values in arguments.items():
            for value in values:
                assert operations.ShellRequest(
                    verb, {name: value}, unused.append)["ok"], (
                    verb, name, value)
        if not arguments:
            assert operations.ShellRequest(verb, {}, unused.append)["ok"], \
                verb
    assert unused == []
    assert HostModule.SHELL_VERBS["openExternal"] == {
        "target": tuple(EXTERNAL_LINKS)}


# ── pickFolder (SF-5a-2) ───────────────────────────────────────────────
# The native folder dialog over the main window; core holds the folder
# (AssetPackService.HoldFolderPick) and the page gets its id, never its path.

PICK_ID = "pick-id-0123456789abcdef"


def _folderDialog(monkeypatch, answer):
    """QFileDialog as AutonaShellOperations sees it: getExistingDirectory
    records its calls -- (parent, title, the folder it opens on) -- and
    answers ``answer`` (or calls it, when callable)."""
    from src import AutonaShellOperations as ShellModule

    calls = []

    def getExistingDirectory(parent, title, folder):
        calls.append((parent, title, folder))
        return answer() if callable(answer) else answer

    monkeypatch.setattr(ShellModule, "QFileDialog", SimpleNamespace(
        getExistingDirectory=getExistingDirectory))
    return calls


def _pickService(monkeypatch, hold=None):
    """AssetPackService.instance standing in: HoldFolderPick records the
    path and answers (PICK_ID, ["aaa"], []), or runs ``hold``. The threads
    it ran on: ``held.threads``."""
    from src.core import AssetPackService as assets

    class Held(list):
        threads = []

    held = Held()
    held.threads = []

    def HoldFolderPick(path):
        held.append(path)
        held.threads.append(threading.current_thread())
        if hold is not None:
            return hold(path)
        return PICK_ID, ["aaa"], []

    monkeypatch.setattr(assets.AssetPackService, "instance",
                        SimpleNamespace(HoldFolderPick=HoldFolderPick))
    return held


@pytest.fixture
def pickLane(monkeypatch):
    """pickFolder's probe on a stepped job lane, and what it hands to the
    GUI thread (GuiExecutor.later) kept: ``settle()`` runs both."""
    from src.Helpers import AutonaQtHelper
    from src.core.TaskExecutor import TaskExecutor

    executor = TaskExecutor(stepped=True)
    monkeypatch.setattr(TaskExecutor, "instance", executor)
    laters = []
    monkeypatch.setattr(AutonaQtHelper, "gui_executor",
                        SimpleNamespace(later=laters.append))

    def settle():
        while executor.queued():
            assert executor.run_next(WAIT_SECS)
        while laters:
            laters.pop(0)()

    try:
        yield SimpleNamespace(executor=executor, laters=laters,
                              settle=settle)
    finally:
        executor.shutdown(WAIT_SECS)


def _pick(operations):
    """pickFolder asked: (what ShellRequest returned, the answers given
    later -- a list, filled once settled)."""
    answers = []
    return operations.ShellRequest("pickFolder", {}, answers.append), answers


def _loguru(level="DEBUG"):
    from loguru import logger

    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level=level)
    return lines, lambda: logger.remove(sink)


def test_a_picked_folder_is_held_and_answered_without_its_path(
        qapp, monkeypatch, tmp_path, pickLane):
    from src.core import AssetPackService as assets

    folder = str(tmp_path / "private-folder-7Q")
    window = _WindowStandIn()
    calls = _folderDialog(monkeypatch, folder)
    held = _pickService(monkeypatch)
    lines, stop = _loguru()
    try:
        returned, answers = _pick(_operations(window))
        # Probed on the job lane, not here (the folder may be on a slow
        # drive): answered later.
        assert returned is None and held == [] and answers == []
        pickLane.settle()
    finally:
        stop()

    assert answers == [{"ok": True, "status": "picked", "pick": PICK_ID,
                        "games": ["aaa"], "conflicts": [],
                        "expiresInSec": assets.PICK_EXPIRY_SECS}]
    assert held == [folder]
    assert held.threads[0] is not threading.main_thread()
    # The dialog over the main window, once, with the translated title.
    assert [call[:2] for call in calls] == [(window, QCoreApplication.translate(
        "app", "Select a game folder to import"))]
    assert "private-folder" not in json.dumps(answers)
    assert "private-folder" not in "\n".join(lines)


def test_the_folder_dialog_opens_on_the_documents_not_the_working_folder(
        qapp, monkeypatch, tmp_path, pickLane):
    from qtpy.QtCore import QStandardPaths

    from src import AutonaShellOperations as ShellModule

    monkeypatch.chdir(tmp_path)
    calls = _folderDialog(monkeypatch, "")
    _pickService(monkeypatch)
    _pick(_operations(_WindowStandIn()))
    documents = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation)
    expected = documents if documents and Path(documents).is_dir() \
        else str(Path.home())
    assert [call[2] for call in calls] == [expected]
    assert calls[0][2] != str(tmp_path)

    # No Documents folder: the home folder.
    monkeypatch.setattr(ShellModule, "QStandardPaths", SimpleNamespace(
        StandardLocation=QStandardPaths.StandardLocation,
        writableLocation=lambda where: ""))
    _pick(_operations(_WindowStandIn()))
    assert calls[1][2] == str(Path.home())


def test_a_closed_folder_dialog_holds_nothing(qapp, monkeypatch, pickLane):
    calls = _folderDialog(monkeypatch, "")
    held = _pickService(monkeypatch)

    returned, answers = _pick(_operations(_WindowStandIn()))

    assert returned == {"ok": True, "status": "cancelled", "pick": None,
                        "games": [], "conflicts": [], "expiresInSec": 0}
    assert len(calls) == 1 and held == []
    assert pickLane.executor.queued() == 0
    pickLane.settle()
    assert answers == []


def test_no_folder_dialog_before_the_window_and_the_service(
        qapp, monkeypatch, pickLane):
    from src.core import AssetPackService as assets

    calls = _folderDialog(monkeypatch, "C:/x")
    _pickService(monkeypatch)
    assert _pick(_operations())[0] == {"ok": False, "error": "not_ready"}
    monkeypatch.setattr(assets.AssetPackService, "instance", None)
    assert _pick(_operations(_WindowStandIn()))[0] == {
        "ok": False, "error": "not_ready"}
    assert calls == []


def test_pick_folder_needs_somewhere_to_answer(qapp, monkeypatch):
    calls = _folderDialog(monkeypatch, "C:/x")
    with pytest.raises(TypeError):
        _operations(_WindowStandIn()).ShellRequest("pickFolder", {})
    assert calls == []


def test_no_folder_dialog_nor_probe_once_the_app_is_shutting_down(
        qapp, monkeypatch, tmp_path, pickLane):
    # Not while it shuts down: no dialog; the shutdown begun while the
    # dialog was open: no probe; begun before the probe ran: nothing held.
    folder = str(tmp_path / "games")
    operations = _operations(_WindowStandIn())
    held = _pickService(monkeypatch)

    pickLane.executor.stopping.set()
    calls = _folderDialog(monkeypatch, folder)
    assert _pick(operations)[0] == {"ok": False, "error": "unavailable"}
    assert calls == []
    pickLane.executor.stopping.clear()

    def shutDownMeanwhile():
        pickLane.executor.stopping.set()
        return folder

    _folderDialog(monkeypatch, shutDownMeanwhile)
    assert _pick(operations)[0] == {"ok": False, "error": "unavailable"}
    assert pickLane.executor.queued() == 0
    pickLane.executor.stopping.clear()

    calls = _folderDialog(monkeypatch, folder)
    returned, answers = _pick(operations)
    assert returned is None and pickLane.executor.queued() == 1
    pickLane.executor.stopping.set()
    pickLane.settle()
    assert held == []
    assert answers == [{"ok": False, "error": "unavailable"}]
    # The request is over: the next one is not "busy".
    pickLane.executor.stopping.clear()
    _folderDialog(monkeypatch, "")
    assert _pick(operations)[0]["status"] == "cancelled"


@pytest.mark.parametrize("code", ["no_game", "not_a_folder", "bad_name"])
def test_a_folder_core_refuses_answers_its_code_alone(
        qapp, monkeypatch, tmp_path, pickLane, code):
    from src.core.AssetPackService import AssetPackError

    folder = str(tmp_path / "private-folder-7Q")

    def refuse(path):
        raise AssetPackError(code, f"refused: {path}")

    _folderDialog(monkeypatch, folder)
    _pickService(monkeypatch, refuse)
    lines, stop = _loguru()
    try:
        _returned, answers = _pick(_operations(_WindowStandIn()))
        pickLane.settle()
    finally:
        stop()

    assert answers == [{"ok": False, "error": code}]
    assert "private-folder" not in "\n".join(lines)


def test_a_folder_that_cannot_be_read_fails_without_its_path(
        qapp, monkeypatch, tmp_path, pickLane):
    folder = str(tmp_path / "private-folder-7Q")

    def broken(path):
        raise PermissionError(13, "denied", path)

    _folderDialog(monkeypatch, folder)
    _pickService(monkeypatch, broken)
    lines, stop = _loguru()
    try:
        _returned, answers = _pick(_operations(_WindowStandIn()))
        pickLane.settle()
    finally:
        stop()

    assert answers == [{"ok": False, "error": "failed"}]
    text = "\n".join(lines)
    assert "private-folder" not in text and "PermissionError" in text


def test_one_folder_request_at_a_time(qapp, monkeypatch, tmp_path,
                                      pickLane):
    # The dialog runs an event loop of its own: a pickFolder that reaches
    # the shell meanwhile is answered busy and opens no second dialog; so
    # is one while the folder picked is probed (two probes could hold the
    # older pick last).
    operations = _operations(_WindowStandIn())
    inner = []

    def whileOpen():
        inner.append(_pick(operations)[0])
        return str(tmp_path)

    calls = _folderDialog(monkeypatch, whileOpen)
    _pickService(monkeypatch)

    returned, answers = _pick(operations)
    assert returned is None
    assert inner == [{"ok": False, "error": "busy"}]
    assert _pick(operations)[0] == {"ok": False, "error": "busy"}
    assert len(calls) == 1
    pickLane.settle()
    assert answers[0]["status"] == "picked"
    # Answered: the next one opens.
    _folderDialog(monkeypatch, "")
    assert _pick(operations)[0]["status"] == "cancelled"


def test_a_folder_dialog_that_raises_does_not_keep_the_shell_busy(
        qapp, monkeypatch, pickLane):
    operations = _operations(_WindowStandIn())

    def broken():
        raise RuntimeError("no dialog")

    _folderDialog(monkeypatch, broken)
    _pickService(monkeypatch)
    with pytest.raises(RuntimeError):
        _pick(operations)
    _folderDialog(monkeypatch, "")
    assert _pick(operations)[0]["status"] == "cancelled"


def test_only_the_shells_pick_opens_a_native_folder_dialog():
    # The one QFileDialog of src/ (ADR-005: new Qt UI is not added): the
    # shell request pickFolder's, run from the event loop by the host.
    from tests.conftest import PROJECT_ROOT

    users = []
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "QFileDialog" \
                    and isinstance(node.ctx, ast.Load):
                owner = node
                while owner in parents and not isinstance(
                        owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner = parents[owner]
                name = owner.name if isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
                users.append((path.relative_to(PROJECT_ROOT).as_posix(),
                              name))
    assert users == [("src/AutonaShellOperations.py", "_PickFolder")]


def test_the_page_and_the_view_agree_on_the_shell_requests():
    import re

    from tests.conftest import PROJECT_ROOT

    source = (PROJECT_ROOT / "web" / "src" / "PanelShell" / "hostApi.ts"
              ).read_text(encoding="utf-8")
    assert (f'SHELL_REQUEST_PATH = "{HostModule.SHELL_REQUEST_PATH}";'
            in source)
    assert 'SHELL_SESSION_PATH = "/api/v1/shell-session";' in source
    verbs = re.search(r"export type ShellVerb = ([^;]+);", source).group(1)
    assert sorted(verb.strip().strip('"') for verb in verbs.split("|")) == (
        sorted(HostModule.SHELL_VERBS))


def test_the_window_gives_the_host_the_shell_adapter():
    # Window._BuildUi builds the one host with AutonaShellOperations, and
    # SetAlwaysOnTop tells the host's page.
    def built(node):
        return [call for call in ast.walk(node) if isinstance(call, ast.Call)
                and ast.unparse(call.func) == "AutonaWebPanelHost"]
    assert [ast.unparse(keyword) for call in built(_windowClass())
            for keyword in call.keywords] == [
                "shell=AutonaShellOperations.instance"]
    assert len(built(_windowMethod("_BuildUi"))) == 1
    assert ("self._webPanelHost.PublishShellState", []) in _calls(
        _windowMethod("SetAlwaysOnTop"))


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
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")
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
    its one widget (ADR-005 S-final SF-5b-3), on the page it reopens on,
    before the web server is started: no view is built then, nor queued
    (core-shown follows self.show()). _StartWebServer() marks the server
    ready and logs webserver-ready once that returns: no view is built
    inside it either. It is built from the event loop afterwards, so
    Chromium's start-up holds up neither the window's first paint and the
    loading spinner nor the server's start (the page could not be shown
    before the server anyway); its first load's end is phase=panel-shown
    (the host's pageLoadFinished)."""
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
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]


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
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]


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
    assert rig.path() == ("/web/app/second", "desktop=1&lng=ja")
    assert rig.js("window.__loadId") == document
    assert rig.loads == [True]
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]

    # Back, and the same panel twice: the page skips the second.
    rig.host.ShowPanel("shell-check")
    rig.host.ShowPanel("shell-check")
    assert _waitUntil(lambda: rig.path()[0] == "/web/app/shell-check")
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
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]


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

    assert _waitUntil(lambda: rig.path()[0] == "/web/app/second")
    assert _waitUntil(lambda: rig.state() == FROZEN)


def test_a_panel_chosen_while_frozen_is_shown_on_reveal(buildHost):
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    rig.host.ShowPanel("second")
    rig.reveal()

    assert _waitUntil(lambda: rig.js("window.__navigations.join()") == "second")
    assert rig.path()[0] == "/web/app/second"
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
    assert _waitUntil(lambda: rig.path()[0] == "/web/app/second")
    assert rig.js("window.__loadId") != document
    assert rig.js("window.__navigations.join()") == "second"
    assert rig.path()[1] == "desktop=1&lng=ja"


# ── SCORE's set selection from the hotkey / legacy route (ADR-005 S4ab-2) ─
#
# AutonaShellOperations has the Window show SCORE (Window.OpenPanel:
# ShowPanel) and then asks the host it returns (OpenScoreSets), which
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

    assert _waitUntil(lambda: _scoreSets(rig) == "2@/web/app/scoreboard")
    assert _taken(rig)
    # Taken: not handed again by a later reveal or a click on SCORE.
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.reveal()
    rig.host.ShowPanel("scoreboard")
    assert _waitUntil(lambda: rig.js("window.__refreshes") > 0)
    assert not _waitUntil(lambda: _scoreSets(rig) != "2@/web/app/scoreboard",
                          timeout=0.5)


def test_the_score_sets_follow_the_navigation_to_score(buildHost):
    rig = _shown(buildHost)
    document = rig.js("window.__loadId")

    rig.host.ShowPanel("scoreboard")
    rig.host.OpenScoreSets(1)

    # On SCORE when the page has it: the same document, navigated first.
    assert _waitUntil(lambda: _scoreSets(rig) == "1@/web/app/scoreboard")
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

    assert _waitUntil(lambda: _scoreSets(rig) == "1@/web/app/scoreboard")
    assert rig.requests == ["/web/app/scoreboard?desktop=1&lng=ja"]
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

    assert _waitUntil(lambda: _scoreSets(rig) == "2@/web/app/scoreboard")
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
    assert _waitUntil(lambda: _scoreSets(rig) == "2@/web/app/scoreboard")
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
    assert rig.path()[0] == "/web/app/shell-check"


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

    assert _waitUntil(lambda: _scoreSets(rig) == "1@/web/app/scoreboard")
    assert _taken(rig)


def test_the_score_sets_are_run_into_a_document_once_until_answered(buildHost):
    # A click on SCORE (or a reveal) before the page's answer is back: not
    # handed over a second time.
    rig = _scoreShown(buildHost)

    rig.host.OpenScoreSets(1)
    rig.host.ShowPanel("scoreboard")
    rig.host.ShowPanel("scoreboard")

    assert _taken(rig)
    assert _scoreSets(rig) == "1@/web/app/scoreboard"


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
    assert _scoreSets(rig) == "1@/web/app/scoreboard"
    assert rig.host._scoreSets is not None
    assert rig.host._scoreSets[1] == 2

    rig.js("void (window.autonaPanelShell.openScoreSets = window.__open)")
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)
    rig.reveal()

    assert _waitUntil(
        lambda: _scoreSets(rig) == "1@/web/app/scoreboard,2@/web/app/scoreboard")
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
    assert rig.requests == ["/web/app/scoreboard?desktop=1&lng=ja"]
    assert rig.path()[0] == "/web/app/scoreboard"
    assert not _waitUntil(lambda: _scoreSets(rig) != "", timeout=0.5)


def test_a_failed_load_is_loaded_again_on_the_next_panel_shown(
        buildHost, monkeypatch, refusedPort):
    rig = _failedFirstLoad(buildHost, monkeypatch, refusedPort)
    failures = len(rig.loads)
    assert _loadFailure(rig.host) is not None

    rig.host.ShowPanel("second")

    # Loaded again, straight on the panel asked for: there was no page
    # whose router could be told. The retry view goes with the failure.
    rig.waitLoaded(count=failures + 1)
    assert _loadFailure(rig.host) is None
    assert rig.requests == ["/web/app/second?desktop=1&lng=ja"]
    assert rig.path() == ("/web/app/second", "desktop=1&lng=ja")
    assert rig.js("window.__navigations.join()") == ""

    # From then on it navigates as usual, without a reload.
    rig.host.ShowPanel("shell-check")
    assert _waitUntil(lambda: rig.path()[0] == "/web/app/shell-check")
    assert rig.requests == ["/web/app/second?desktop=1&lng=ja"]
    assert rig.loads == [False] * failures + [True]


def test_a_failed_load_is_loaded_again_when_the_host_comes_back(
        buildHost, monkeypatch, refusedPort):
    rig = _failedFirstLoad(buildHost, monkeypatch, refusedPort)
    failures = len(rig.loads)
    rig.hide()
    assert _waitUntil(lambda: rig.state() == FROZEN)

    # The retry view over the view leaves its return to Active as it was.
    rig.reveal()

    rig.waitLoaded(count=failures + 1)
    assert _loadFailure(rig.host) is None
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")


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
# app's startup (pitfalls #1).
def _autonaSource():
    from tests.conftest import PROJECT_ROOT
    return (PROJECT_ROOT / "src" / "Autona.py").read_text(encoding="utf-8")


def _windowClass():
    for node in ast.walk(ast.parse(_autonaSource())):
        if isinstance(node, ast.ClassDef) and node.name == "Window":
            return node
    raise AssertionError("src/Autona.py: no class Window")


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


def _windowClassConstant(name):
    for node in _windowClass().body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"src/Autona.py: Window has no {name}")


# What the window lost to the AppShell (ADR-005 S-final SF-5b-3): the Qt
# sidebar and its stack of pages, the menus (Help & Settings, the tournament
# and game buttons with the mods switch, the battle format), the status bar,
# the tournament load failure's QMessageBox -- and the page bookkeeping that
# came with them (a page's index, STATE_VERSION, MENU_PAGES: the window keeps
# a page's name now, src/AutonaWindowState.py).
GONE_WINDOW_NAMES = (
    "QStackedWidget", "QToolButton", "QStatusBar", "QMenuBar", "QMenu",
    "QTabBar", "QCheckBox", "QProgressBar", "AutonaSidebar", "WebPanelPages",
    "StatusService", "MENU_PAGES", "STATE_VERSION")
GONE_WINDOW_MEMBERS = (
    # methods
    "HideSidebar", "ShowSidebar", "OpenMenuPage", "OpenScoreSets",
    "OpenSettings", "ReloadGames", "SetGame", "Signal_GameChange",
    "ToggleAlwaysOnTop", "UpdateTournamentButton", "_AdoptWebPanelHost",
    "_ApplyTabBarPolicy", "_EnsurePanelAt", "_NavToRemember",
    "_OnNavSelected", "_OnStackPageChanged", "_ReportTournamentLoadFailure",
    "_SetPulldownAttention", "_ShowBattleFormat", "_ShowModdedContent",
    "_ToggleMods", "_WebPanel", "_on_status_changed", "_pickGame",
    "_resolveBattleFormat",
    # attributes, and QMainWindow's bars
    "_alwaysOnTopAction", "_lazyPanels", "_navBeforeMenuPage", "_navSections",
    "_panelIndexes", "_statusBar", "_webPanelPages", "_webserverErrorBox",
    "gameMenu", "gameSelect", "moddedContentCheck", "setTournamentAction",
    "sidebar", "sidebarExpandButton", "stack", "statusBar", "setStatusBar",
    "menuBar", "setMenuBar",
    # what nothing read any more (the upstream's state-keeping, the player
    # lists of the Qt panels)
    "programState", "savedProgramState", "programStateDiff", "saveMutex",
    "player_layouts", "allplayers", "local_players")


def test_the_window_is_the_shared_view_alone():
    from tests.conftest import PROJECT_ROOT

    tree = ast.parse(_autonaSource())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert names.isdisjoint(GONE_WINDOW_NAMES), names & set(GONE_WINDOW_NAMES)
    window = _windowClass()
    members = {node.name for node in window.body
               if isinstance(node, ast.FunctionDef)}
    members |= {node.attr for node in ast.walk(window)
                if isinstance(node, ast.Attribute)}
    assert members.isdisjoint(GONE_WINDOW_MEMBERS), (
        members & set(GONE_WINDOW_MEMBERS))
    assert not [name for name in members
                if name.startswith("_Create") and name.endswith("Panel")]
    # The window's signals are the one something connects to (its
    # GameChanged relay of the asset adapter's onLoad went in ADR-005 S-final
    # SF-6b: the web server took the adapter's signal itself, and takes the
    # bridge's game_loaded since SF-6c); the timing helper nothing called
    # went with the rest.
    signals = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef)
                   and node.name == "WindowSignals")
    assert sorted(target.id for node in signals.body
                  if isinstance(node, ast.Assign)
                  for target in node.targets) == ["UiMounted"]
    assert not [node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "catchtime"]
    # Its one widget is the host (built with the shell adapter:
    # test_the_window_gives_the_host_the_shell_adapter).
    assert [call for call in _calls(_windowMethod("_BuildUi"))
            if call[0] == "self.setCentralWidget"] == [
                ("self.setCentralWidget", ["host"])]
    # The sidebar's module went, and nothing imports it.
    assert not (PROJECT_ROOT / "src" / "AutonaSidebar.py").exists()
    for path in [*sorted((PROJECT_ROOT / "src").rglob("*.py")),
                 PROJECT_ROOT / "main.py"]:
        assert "AutonaSidebar" not in _importedModules(path), path
    assert not hasattr(HostModule, "WebPanelPages")
    # What earlier steps took out stays out: the battle format reaching a
    # thumbnail panel (S4p), the boards' dock (S4ac-3), SCORE's way back to
    # the Qt panel (S4aa).
    source = _autonaSource()
    assert "thumbnailSetting" not in source
    assert "self.scoreboard" not in source
    assert not hasattr(HostModule, "QT_FALLBACK_SETTINGS")


def test_the_windows_one_message_box_is_the_lan_question():
    # The tournament load failure's (the pages tell it since SF-5b-2) and
    # the web server start failure's (the host's error view) went in
    # SF-5b-3: the LAN opt-in, asked once before the server starts, is the
    # window's one dialog. (The port guard's and the updater's are their own
    # modules'.)
    window = _windowClass()
    used = [method.name for method in window.body
            if isinstance(method, ast.FunctionDef)
            for node in ast.walk(method)
            if isinstance(node, ast.Name) and node.id == "QMessageBox"]
    assert set(used) == {"_StartWebServer"}
    assert [call for call, _args in _calls(_windowMethod("_StartWebServer"))
            if call.startswith("QMessageBox.")] == ["QMessageBox.question"]
    # None outside the window's methods either.
    assert len([node for node in ast.walk(ast.parse(_autonaSource()))
                if isinstance(node, ast.Name)
                and node.id == "QMessageBox"]) == len(used)


# The Qt widgets src/ defines (ADR-005: no new Qt UI; S-final SF-5b-3: the
# window is the shared view alone): the main window, the shared view's host
# and its view, the Designer's window, the updater's dialog, and the loading
# spinner over the window (src/autona_splash.py, which lived outside src/).
QT_WIDGET_CLASSES = {"Window", "AutonaWebPanelHost", "_PanelView",
                     "AutonaDesignerWindow", "_UpdateDialog",
                     "LoadingOverlay"}


def test_the_only_qt_widgets_are_the_shells():
    from qtpy import QtWebEngineWidgets, QtWidgets

    from tests.conftest import PROJECT_ROOT

    # Every class of src/ by name, with its bases' names: a base defined in
    # src/ is followed to its own bases.
    bases = {}
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    ast.unparse(base).rsplit(".", 1)[-1]
                    for base in node.bases)

    def qtWidget(name):
        for module in (QtWidgets, QtWebEngineWidgets):
            cls = getattr(module, name, None)
            if isinstance(cls, type) and issubclass(cls, QWidget):
                return True
        return False

    def isWidget(name, seen=frozenset()):
        if qtWidget(name):
            return True
        return name not in seen and any(
            isWidget(base, seen | {name}) for base in bases.get(name, ()))

    widgets = {name for name, named in bases.items()
               if any(isWidget(base, frozenset({name})) for base in named)}
    assert widgets <= QT_WIDGET_CLASSES, widgets - QT_WIDGET_CLASSES
    # The check sees a widget at all (the window's, a QMainWindow).
    assert "Window" in widgets


def test_the_window_starts_the_server_under_the_spinner_and_marks_the_page():
    """STARTUP (ADR-003; ADR-005 S-final SF-5b-3): phase=core-shown right
    after self.show(), with the page to reopen on given to the host before
    it; the loading spinner over the window then; the web server built once
    that is painted (_StartWebServer: phase=webserver-ready after the
    readiness that releases the host's view, whose build and first load come
    after -- test_the_view_waits_for_the_web_server); and the host's first
    load's end takes the spinner away (phase=panel-shown)."""
    tokens = "".join(_autonaSource().split())
    order = ["host.ShowPanel(window_state.RestorePanel(self.qtSettings))",
             "self._windowStateRestored=True",
             "self.show()", 'log_startup_phase("core-shown")',
             "self._loading_overlay=LoadingOverlay(self)",
             "self._loading_overlay.start()",
             # The spinner's cap, with it (_OnSpinnerCapReached).
             "self._spinnerCap.start()",
             "self._loading_overlay.call_when_painted(self._StartWebServer)"]
    positions = [tokens.index(token) for token in order]
    assert positions == sorted(positions)
    built = _calls(_windowMethod("_BuildUi"))
    assert ("self._spinnerCap.setInterval", ["self.SPINNER_CAP_MS"]) in built
    assert ("self._spinnerCap.timeout.connect",
            ["self._OnSpinnerCapReached"]) in built
    assert 0 < _windowClassConstant("SPINNER_CAP_MS") <= 30_000
    # The spinner no longer waits for the Qt panels' queue to drain.
    assert "finish_when_ready" not in tokens
    assert "call_when_finished" not in tokens
    names = [call for call, _args in _calls(_windowMethod("_StartWebServer"))]
    assert names.index("MarkWebServerReady") < names.index("log_startup_phase")
    assert ("host.pageLoadFinished.connect", ["self._OnPageLoadFinished"]) in (
        _calls(_windowMethod("_BuildUi")))
    loaded = _calls(_windowMethod("_OnPageLoadFinished"))
    assert ("log_startup_phase", ["'panel-shown'"]) in loaded
    assert ("self._DismissLoadingOverlay", []) in loaded
    # The start failure: the host's error view and the spinner away, no
    # dialog (test_the_windows_one_message_box_is_the_lan_question).
    failed = _calls(_windowMethod("OnWebServerStartFailed"))
    assert "self._webPanelHost.ShowStartFailure" in [
        call for call, _args in failed]
    assert ("self._DismissLoadingOverlay", []) in failed


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

    # The words core still uses are core's own table since ADR-005 S-final
    # SF-7 (core/CoreWords, not the .ts: test_the_ts_files_hold_the_native_
    # dialogs_words_alone); the sidebar's label is the AppShell's.
    from src.core import CoreWords
    assert (CoreWords.WORDS["thumbnail_template_type_a"]["en"],
            CoreWords.WORDS["thumbnail_template_type_b"]["en"]) == (
        "Standard", "Center Panel")


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
    # The layouts' names and the sample's are core's own table since ADR-005
    # S-final SF-7 (core/CoreWords); the sidebar's label is the AppShell's.
    from src.core import CoreWords
    assert (CoreWords.WORDS["result_layout_horizontal"]["en"],
            CoreWords.WORDS["sample_tournament"]["en"]) == (
        "Horizontal (1920x1080)", "Sample Tournament")


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
        # The sidebar's label is the AppShell's (i18next; the Qt frame's
        # translation went from the .ts in ADR-005 S-final SF-7).
        assert "<source>Board</source>" not in text, path.name


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
    spec = (PROJECT_ROOT / "packaging" / "autona.spec").read_text(
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
    from src.AutonaWebServer import _build_web_app
    from src.core import AppSettings
    from src.core import StateSchema as schema
    from src.core.Actions import AutonaActions
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.GameLoader import GameLoader
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
    # (the asset adapter's models were instance attributes, so the source is
    # read: no definition or attribute of those names; the adapter itself
    # went in ADR-005 S-final SF-6c, its loader is core's).
    assert [key for key in schema.SCORE_BOARD_KEYS
            if key.split(".")[0] == "stages"] == []
    gone = {"StagesPayload", "StageRow", "stageRows", "stageRowsSorted",
            "stageModel", "stageModelWithBlank", "UpdateStageModel",
            "LoadStageImage", "LoadStageImageComplete",
            "_stageIconGeneration"}
    for module in (GameAssetCatalog, GameLoader):
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


# ── the window: the shared view alone (ADR-005 S-final SF-5b-3) ─────────

class _OverlayStandIn:
    """The loading spinner (autona_splash.LoadingOverlay) as the window
    takes it away."""

    def __init__(self):
        self.finished = 0

    def finish(self):
        self.finished += 1


class _CountingQSettings(QSettings):
    """The window's QSettings in an .ini file, recording each write and
    each flush to disk (window_state.SaveWindowState and _SaveWindowState
    call them from Python)."""

    def __init__(self, path):
        super().__init__(path, QSettings.Format.IniFormat)
        self.writes = []
        self.syncs = 0

    def setValue(self, key, value):
        self.writes.append((key, value))
        super().setValue(key, value)

    def sync(self):
        self.syncs += 1
        super().sync()


class _WindowRig:
    """The Window's real page, startup, saving and failure methods (read out
    of src/Autona.py, which cannot be imported: pitfalls #1) on a
    QMainWindow whose one widget is a real host, wired as _BuildUi wires it,
    after the saved state's restore (_windowStateRestored), its QSettings an
    .ini file. Not shown, and the web server not ready: the host builds no
    view; what it was asked to show is its CurrentPanel().
    log_startup_phase records the phases; _StartWebServer's server, port
    guard, wiring and readiness are stand-ins that record their calls into
    ``calls`` (and raise what ``raises`` holds for their name), and the LAN
    question is answered already."""

    METHODS = ("_OnPanelChanged", "_OnNavCollapsedChanged",
               "_SaveWindowState", "OpenPanel", "_BringToFront",
               "_StartWebServer", "_OnPageLoadFinished",
               "_OnSpinnerCapReached", "_DismissLoadingOverlay",
               "OnWebServerStartFailed")

    def __init__(self, settingsPath):
        from loguru import logger
        from qtpy.QtWidgets import QApplication, QMainWindow

        from src import AutonaWindowState

        window = ast.parse("class Window(QMainWindow):\n    pass\n").body[0]
        window.body = [node for node in _windowClass().body
                       if isinstance(node, ast.FunctionDef)
                       and node.name in self.METHODS]
        assert sorted(node.name for node in window.body) == sorted(
            self.METHODS)
        self.phases = []
        self.calls = []
        self.raises = {}
        rig = self

        class ServerStandIn:
            port = 5107
            host_name = "127.0.0.1"

            def __init__(self, on_start_failed=None):
                rig._Step("WebServer")

            def start(self):
                rig._Step("start")

            @staticmethod
            def Serving():
                # The settings' port while no server runs.
                return (5111, False)

        # Only what the methods may use: a QMessageBox there (but for the
        # LAN question, answered here) would be a NameError.
        namespace = {
            "QMainWindow": QMainWindow, "QApplication": QApplication,
            "Qt": Qt, "logger": logger, "window_state": AutonaWindowState,
            "log_startup_phase": self.phases.append,
            "SettingsManager": SimpleNamespace(
                loadFailed=False, Get=lambda key, default=None: False),
            "WebServer": ServerStandIn,
            "check_webserver_port": lambda port, parent=None, host=None: (
                self._Step("check_webserver_port")),
            "wire_web_server": lambda *args: self._Step("wire_web_server"),
            "MarkWebServerReady": lambda: self._Step("MarkWebServerReady"),
            "AutonaQtHelper": SimpleNamespace(gui_executor=lambda f: f())}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[window], type_ignores=[])), "src/Autona.py", "exec"),
            namespace)
        self.window = w = namespace["Window"]()
        # As _BuildUi.
        w.SPINNER_CAP_MS = _windowClassConstant("SPINNER_CAP_MS")
        w.signals = SimpleNamespace(UiMounted=None)
        self.host = host = AutonaWebPanelHost()
        w._webPanelHost = host
        w.setCentralWidget(host)
        w.webserver = SimpleNamespace(port=5107)
        w._shuttingDown = False
        w._panelToRemember = AutonaWindowState.DEFAULT_PANEL
        w._windowStateRestored = False
        host.panelChanged.connect(w._OnPanelChanged)
        host.navCollapsedChanged.connect(w._OnNavCollapsedChanged)
        w._panelShown = False
        w._spinnerCap = QTimer(w)
        w._spinnerCap.setSingleShot(True)
        w._spinnerCap.setInterval(w.SPINNER_CAP_MS)
        w._spinnerCap.timeout.connect(w._OnSpinnerCapReached)
        host.pageLoadFinished.connect(w._OnPageLoadFinished)
        self.settingsPath = settingsPath
        w.qtSettings = self.settings = _CountingQSettings(settingsPath)
        w._windowStateRestored = True
        self.overlay = _OverlayStandIn()
        w._loading_overlay = self.overlay
        w._spinnerCap.start()
        # What _BringToFront would do to the window (shown, it would build
        # the host's view): recorded, with the page shown then.
        self.fronted = []
        w._BringToFront = lambda: self.fronted.append(host.CurrentPanel())

    def _Step(self, name):
        self.calls.append(name)
        if name in self.raises:
            raise self.raises[name]

    def onDisk(self):
        # The .ini file's own text: what was written and synced.
        path = Path(self.settingsPath)
        if not path.exists():
            return {}
        lines = path.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)


@pytest.fixture
def windowRig(qapp, monkeypatch, tmp_path):
    # The web server is not ready (a host asked for a page registers for the
    # readiness: the list is this test's).
    monkeypatch.setattr(WPL, "_webServerReady", False)
    monkeypatch.setattr(WPL, "_pendingLoaders", [])
    monkeypatch.setattr(WPL, "_readyCallbacks", [])
    rigs = []

    def build():
        rig = _WindowRig(str(tmp_path / f"qt{len(rigs)}.ini"))
        rigs.append(rig)
        return rig

    yield build

    for rig in rigs:
        rig.window._spinnerCap.stop()
        rig.window.deleteLater()
        QCoreApplication.sendPostedEvents(rig.window, QEvent.Type.DeferredDelete)


def test_the_window_reopens_on_the_page_shown_not_a_menu_page(windowRig):
    # closeEvent saves _panelToRemember (AutonaWindowState.SaveWindowState).
    rig = windowRig()
    w, host = rig.window, rig.host
    assert w._panelToRemember == "event"

    host.ShowPanel("stream")
    assert w._panelToRemember == "stream"
    # The AppShell's Help & Settings menu's pages: the page before them
    # stays, as the Qt sidebar kept its item while a menu page showed.
    for menu in ("settings", "api", "assets"):
        host.ShowPanel(menu)
        assert host.CurrentPanel() == menu
        assert w._panelToRemember == "stream"
    host.ShowPanel("top8")
    assert w._panelToRemember == "top8"


def test_the_window_follows_the_app_shells_own_navigation(windowRig):
    # The AppShell moves its router itself (its sidebar, its menu): the
    # host follows the URL (AutonaWebPanelHost._OnUrlChanged), and the
    # window with it. The real view: test_the_host_follows_the_app_shells_
    # router_and_reloads_on_it.
    rig = windowRig()
    w, host = rig.window, rig.host
    page = "http://127.0.0.1:5107/web/app/"
    host.ShowPanel("event")

    host._OnUrlChanged(QUrl(f"{page}stats?desktop=1&lng=ja&nav=collapsed"
                            "&told=4"))
    assert host.CurrentPanel() == "stats"
    assert w._panelToRemember == "stats"
    assert host.NavCollapsed() is True
    assert host._Path() == PanelPath("stats", navCollapsed=True, told=4)

    host._OnUrlChanged(QUrl(f"{page}about?desktop=1&lng=ja"))
    assert host.CurrentPanel() == "about"
    assert w._panelToRemember == "stats"
    assert host.NavCollapsed() is False
    # Not the AppShell on a panel (an error page): nothing follows.
    host._OnUrlChanged(QUrl("chrome-error://chromewebdata/"))
    host._OnUrlChanged(QUrl("http://127.0.0.1:5107/web/stats"))
    assert host.CurrentPanel() == "about"


def test_a_page_asked_for_from_outside_fronts_the_window_on_it(windowRig):
    # The load_set hotkey and the legacy open-set route (AutonaShellOperations
    # asks the host it gets for SCORE's set selection).
    rig = windowRig()
    w, host = rig.window, rig.host
    host.ShowPanel("stream")

    assert w.OpenPanel("scoreboard") is host

    assert rig.fronted == ["stream"]
    assert host.CurrentPanel() == "scoreboard"
    assert w._panelToRemember == "scoreboard"


def test_a_page_asked_for_while_the_window_closes_does_nothing(windowRig):
    # closeEvent has begun the teardown: not shown again, nothing to ask.
    rig = windowRig()
    w, host = rig.window, rig.host
    host.ShowPanel("stream")
    w._shuttingDown = True

    assert w.OpenPanel("scoreboard") is None

    assert rig.fronted == []
    assert host.CurrentPanel() == "stream"


@pytest.mark.parametrize("ok", [True, False])
def test_the_first_load_takes_the_spinner_away(windowRig, ok):
    # STARTUP phase=panel-shown on the page there; a first load that failed
    # for good takes the spinner away too (the view shows its error page,
    # loaded again on the next page asked for), unlogged. Once.
    rig = windowRig()

    rig.host.pageLoadFinished.emit(ok)
    rig.host.pageLoadFinished.emit(True)

    assert rig.overlay.finished == 1
    assert rig.window._loading_overlay is None
    assert rig.phases == (["panel-shown"] if ok else [])
    # The spinner's cap goes with it.
    assert not rig.window._spinnerCap.isActive()


def test_the_window_shows_a_start_failure_in_place_of_its_page(windowRig):
    # No dialog over the spinner (a QMessageBox until SF-5b-3): the host's
    # error view says why, and the spinner goes.
    rig = windowRig()
    rig.host.ShowPanel("event")

    rig.window.OnWebServerStartFailed("[Errno 10048] address in use")

    label = rig.host.findChild(QLabel, "webPanelStartFailure")
    assert label is not None
    assert "5107" in label.text()
    assert "[Errno 10048] address in use" in label.text()
    assert rig.overlay.finished == 1
    assert rig.window._loading_overlay is None
    assert rig.phases == []
    assert not rig.window._spinnerCap.isActive()


def _loadFailure(host):
    # The retry view while it is the host's (ShowLoadFailure), else None.
    return host._loadFailure


@pytest.mark.parametrize("step, port", [
    ("WebServer", "5111"),            # not even built: the settings' port
    ("check_webserver_port", "5107"),
    ("start", "5107"),
    ("wire_web_server", "5107")])
def test_a_web_server_that_cannot_be_started_is_shown_as_a_start_failure(
        windowRig, step, port):
    # Raised from the event loop (call_when_painted), nothing above would
    # catch it: the spinner would stay over a page that never comes. It is
    # the start failure the server thread's bind failure is.
    rig = windowRig()
    w = rig.window
    w.webserver = None
    rig.host.ShowPanel("event")
    rig.raises[step] = OSError("the port guard broke")

    w._StartWebServer()

    label = rig.host.findChild(QLabel, "webPanelStartFailure")
    assert label is not None
    assert "OSError: the port guard broke" in label.text()
    assert port in label.text()
    assert rig.overlay.finished == 1
    assert not w._spinnerCap.isActive()
    assert "MarkWebServerReady" not in rig.calls
    assert rig.phases == []


def test_the_web_server_start_holds_the_spinners_cap_and_counts_it_again(
        windowRig):
    # The operator's answers to the startup's dialogs (the LAN question,
    # the port guard's) and the server's construction are not the page's
    # wait: the cap is held while they run, and counts from the server's
    # readiness on.
    rig = windowRig()
    w = rig.window
    w.webserver = None
    held = []
    rig.raises = {}
    real = rig._Step

    def step(name):
        held.append((name, w._spinnerCap.isActive()))
        real(name)
    rig._Step = step

    w._StartWebServer()

    assert rig.calls == ["WebServer", "check_webserver_port", "start",
                         "wire_web_server", "MarkWebServerReady"]
    assert all(not active for _name, active in held), held
    assert w._spinnerCap.isActive()
    assert w._spinnerCap.remainingTime() > w.SPINNER_CAP_MS - 5_000
    assert rig.phases == ["webserver-ready"]
    assert rig.overlay.finished == 0
    # Once only.
    w._StartWebServer()
    assert rig.calls.count("WebServer") == 1


def test_the_spinners_cap_takes_it_away_and_offers_to_load_again(windowRig):
    # Whatever holds the first page (a load that never ends, a path nothing
    # foresaw): past SPINNER_CAP_MS the spinner goes and the host covers
    # itself with the retry view. A first load that ends later is still
    # logged (the host's own load end takes the retry view away:
    # test_a_load_that_failed_for_good_offers_to_load_it_again).
    rig = windowRig()
    w, host = rig.window, rig.host
    host.ShowPanel("event")

    w._OnSpinnerCapReached()

    assert rig.overlay.finished == 1
    assert w._loading_overlay is None
    cover = _loadFailure(host)
    assert cover is not None and not cover.isHidden()
    assert cover.findChild(QPushButton, "webPanelRetry") is not None
    assert rig.phases == []
    # No view to load again yet (the server is not ready here): its button
    # has it built as soon as it can be.
    scheduled = []
    host._ScheduleView = lambda: scheduled.append(True)
    cover.findChild(QPushButton, "webPanelRetry").click()
    assert scheduled == [True]
    assert host.view is None
    # Twice: nothing more.
    w._OnSpinnerCapReached()
    assert rig.overlay.finished == 1
    host.pageLoadFinished.emit(True)
    assert rig.phases == ["panel-shown"]


def test_the_spinners_cap_does_nothing_once_the_spinner_is_gone(windowRig):
    rig = windowRig()
    rig.host.ShowPanel("event")
    rig.host.pageLoadFinished.emit(True)

    rig.window._OnSpinnerCapReached()

    assert _loadFailure(rig.host) is None
    assert rig.overlay.finished == 1


def test_the_retry_view_gives_way_to_a_start_failure(windowRig):
    # Nothing to load again once the page can never load; and nothing to
    # offer while a loaded page is there.
    rig = windowRig()
    host = rig.host
    host.ShowLoadFailure()
    cover = _loadFailure(host)
    assert cover is not None
    host.ShowLoadFailure()
    assert _loadFailure(host) is cover

    host.ShowStartFailure("Web server failed to start")

    assert _loadFailure(host) is None
    assert not shiboken.isValid(cover) or cover.isHidden()
    host.ShowLoadFailure()
    assert _loadFailure(host) is None
    loaded = windowRig().host
    loaded._loaded = True
    loaded.ShowLoadFailure()
    assert _loadFailure(loaded) is None


def test_the_retry_and_failure_views_follow_the_programs_language(windowRig):
    # The .qm the app loads holds the host's words (the .ts is edited by
    # hand and compiled by scripts/compile_translations.ps1).
    from qtpy.QtCore import QTranslator

    from tests.conftest import PROJECT_ROOT
    translator = QTranslator()
    assert translator.load(str(PROJECT_ROOT / "src" / "i18n" / "Autona_ja.qm"))
    # The last one installed is asked first, whatever the harness installed.
    assert QCoreApplication.installTranslator(translator)
    try:
        host = windowRig().host
        host.ShowLoadFailure()
        cover = _loadFailure(host)
        assert cover.findChild(QPushButton, "webPanelRetry").text() == "再試行"
        assert cover.findChild(QLabel).text().startswith(
            "ページを読み込めませんでした\n\n")
        host._ViewNotBuilt(RuntimeError("x"))
        label = host.findChild(QLabel, "webPanelStartFailure")
        assert label.text().startswith("ページを表示できませんでした\n\n")
        assert "RuntimeError: x" in label.text()
        assert "Autona を再起動してください。" in label.text()
    finally:
        QCoreApplication.removeTranslator(translator)


def test_the_window_saves_its_page_and_sidebar_as_they_change(windowRig):
    # Not only on close (a crash or a forced end skips closeEvent): each
    # change is written and flushed to disk at once -- what differs only.
    rig = windowRig()
    w, host, settings = rig.window, rig.host, rig.settings
    page = "http://127.0.0.1:5107/web/app/"

    host.ShowPanel("stream")
    assert rig.onDisk() == {"lastPanel": "stream", "navCollapsed": "false"}
    assert settings.syncs == 1

    # A menu page: the page before it stays saved; nothing written.
    host.ShowPanel("settings")
    host._OnUrlChanged(QUrl(f"{page}about?desktop=1&lng=ja"))
    # The tournament load failure the AppShell told of: not saved.
    host._OnUrlChanged(QUrl(f"{page}about?desktop=1&lng=ja&told=5"))
    assert settings.syncs == 1
    assert len(settings.writes) == 2

    # The AppShell's sidebar hidden (its URL): saved.
    host._OnUrlChanged(QUrl(f"{page}about?desktop=1&lng=ja&nav=collapsed"
                            "&told=5"))
    assert rig.onDisk() == {"lastPanel": "stream", "navCollapsed": "true"}
    assert settings.syncs == 2
    # The saved page again: nothing written.
    host.ShowPanel("stream")
    assert settings.syncs == 2
    host._OnUrlChanged(QUrl(f"{page}top8?desktop=1&lng=ja"))
    assert rig.onDisk() == {"lastPanel": "top8", "navCollapsed": "false"}
    assert settings.syncs == 3
    assert settings.writes[2:] == [("navCollapsed", True),
                                   ("lastPanel", "top8"),
                                   ("navCollapsed", False)]

    # Once the window closes, closeEvent saves (with the geometry): not
    # here any more.
    w._shuttingDown = True
    host.ShowPanel("stats")
    host.SetNavCollapsed(True)
    assert settings.syncs == 3
    assert rig.onDisk()["lastPanel"] == "top8"


def test_the_window_saves_nothing_before_the_saved_state_is_read(windowRig):
    # The old keys are read into the new ones only while those are absent
    # (AutonaWindowState.RestorePanel): nothing may be written before.
    rig = windowRig()
    rig.window._windowStateRestored = False

    rig.host.SetNavCollapsed(True)
    rig.host.ShowPanel("stream")

    assert rig.settings.writes == []
    assert rig.settings.syncs == 0
    assert rig.window._panelToRemember == "stream"


def test_the_host_tells_when_the_sidebar_is_hidden_or_back(windowRig):
    rig = windowRig()
    host = rig.host
    told = []
    host.navCollapsedChanged.connect(told.append)
    page = "http://127.0.0.1:5107/web/app/event?desktop=1&lng=ja"

    host.SetNavCollapsed(True)
    host.SetNavCollapsed(True)
    host._OnUrlChanged(QUrl(f"{page}&nav=collapsed"))
    host._OnUrlChanged(QUrl(f"{page}&nav=collapsed&told=3"))
    assert told == [True]
    host._OnUrlChanged(QUrl(page))
    assert told == [True, False]
    host.SetNavCollapsed(False)
    assert told == [True, False]


def test_the_window_saves_where_the_page_or_sidebar_changes_and_on_close():
    # The call points (the rig above runs the methods; these are what
    # _BuildUi and closeEvent wire).
    built = _calls(_windowMethod("_BuildUi"))
    assert ("host.panelChanged.connect", ["self._OnPanelChanged"]) in built
    assert ("host.navCollapsedChanged.connect",
            ["self._OnNavCollapsedChanged"]) in built
    for handler in ("_OnPanelChanged", "_OnNavCollapsedChanged"):
        assert ("self._SaveWindowState", []) in _calls(_windowMethod(handler))
    saving = [call for call, _args in _calls(_windowMethod("_SaveWindowState"))]
    assert "window_state.SaveWindowState" in saving
    assert "self.qtSettings.sync" in saving
    closing = _windowMethod("closeEvent")
    assert "window_state.SaveWindowState" in [
        call for call, _args in _calls(closing)]
    # closeEvent saves itself, past _SaveWindowState's _shuttingDown guard.
    tokens = "".join(ast.unparse(closing).split())
    assert tokens.index("self._shuttingDown=True") < tokens.index(
        "window_state.SaveWindowState(")
    # No other writer of the saved page.
    writers = {method.name for method in _windowClass().body
               if isinstance(method, ast.FunctionDef)
               and "window_state.SaveWindowState" in [
                   call for call, _args in _calls(method)]}
    assert writers == {"_SaveWindowState", "closeEvent"}


def test_a_start_failure_shows_its_text_and_builds_no_view(buildHost,
                                                           monkeypatch):
    # The page can never load: the label in the view's place (plain text,
    # once), and no view built for a page asked for afterwards or the
    # readiness that would release it.
    monkeypatch.setattr(WPL, "_webServerReady", False)
    rig = buildHost()
    rig.host.ShowPanel("shell-check")
    rig.host.ShowStartFailure("Web server failed to start\n\n<b>5107</b>")
    rig.host.ShowStartFailure("again")
    rig.stack.setCurrentIndex(0)

    WPL.MarkWebServerReady()
    rig.host.ShowPanel("second")

    assert not _waitUntil(lambda: rig.host.view is not None, timeout=0.5)
    labels = rig.host.findChildren(QLabel, "webPanelStartFailure")
    assert [label.text() for label in labels] == [
        "Web server failed to start\n\n<b>5107</b>"]
    assert labels[0].textFormat() == Qt.TextFormat.PlainText
    assert labels[0].isVisible()
    assert rig.requests == []


def test_a_start_failure_after_the_view_was_built_hides_it(buildHost):
    rig = _shown(buildHost)

    rig.host.ShowStartFailure("Web server failed to start")
    rig.host.ShowPanel("second")

    assert not rig.host.view.isVisible()
    assert rig.host.findChild(QLabel, "webPanelStartFailure").isVisible()
    # Nothing loaded again (_ReloadIfBlank).
    assert not _waitUntil(lambda: len(rig.requests) > 1, timeout=0.5)
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]


def _gone(widget):
    # Taken away: hidden, or deleted already (deleteLater).
    return not shiboken.isValid(widget) or widget.isHidden()


def _hostWords(text):
    # The host's words as the program's language says them: a run may have
    # a translator installed by an earlier test (the .qm's own words:
    # test_the_retry_and_failure_views_follow_the_programs_language).
    return QCoreApplication.translate("webpanel", text)


def test_a_load_that_failed_for_good_offers_to_load_it_again(
        buildHost, monkeypatch, refusedPort):
    # Chromium's error page has no navigation of the AppShell's to leave it
    # by: the retry view covers it, and its button loads the page again --
    # once per click, not in a loop -- until a load succeeds.
    monkeypatch.setattr(WPL.LocalPageLoader, "LOAD_RETRY_MS", 50)
    rig = buildHost()
    finished = []
    rig.host.pageLoadFinished.connect(finished.append)
    rig.usePort(refusedPort)
    rig.host.ShowPanel("shell-check")
    rig.reveal()
    attempts = 1 + WPL.LocalPageLoader.MAX_LOAD_RETRIES

    assert _waitUntil(lambda: finished == [False])
    cover = _loadFailure(rig.host)
    assert cover is not None and cover.isVisible()
    assert cover.geometry() == rig.host.rect()
    # Over the view, which stays on screen (its return to Active loads it
    # again too).
    assert rig.host.view.isVisible()
    text = "\n".join(label.text() for label in cover.findChildren(QLabel))
    # In the program's language (whichever translator the run installed).
    assert _hostWords("The page could not be loaded") in text
    button = cover.findChild(QPushButton, "webPanelRetry")
    assert button.text() == _hostWords("Retry")
    assert not _waitUntil(lambda: len(rig.loads) > attempts, timeout=0.5)

    # Still nothing listening: loaded again, failed again, still offered.
    button.click()
    assert _waitUntil(lambda: finished == [False, False])
    assert rig.loads == [False] * (2 * attempts)
    assert _loadFailure(rig.host) is cover and cover.isVisible()

    # The server is back: the page is there and the retry view goes.
    rig.usePort(rig.servingPort)
    button.click()
    assert _waitUntil(lambda: finished == [False, False, True])
    assert rig.loads[-1] is True
    assert _loadFailure(rig.host) is None
    assert _gone(cover)
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]
    assert rig.path() == ("/web/app/shell-check", "desktop=1&lng=ja")


def test_a_first_load_refused_is_a_load_that_failed_for_good(
        buildHost, monkeypatch):
    # The host's own load refused (its URL off the Web UI): no document is
    # behind it, so it is not a page's navigation refused from a loaded
    # document. The window's spinner must go (pageLoadFinished), and the
    # retry view comes.
    monkeypatch.setattr(WPL.LocalPageLoader, "LOAD_RETRY_MS", 50)
    realPath = HostModule.PanelPath
    monkeypatch.setattr(HostModule, "PanelPath",
                        lambda *args, **kwargs: "/not-the-web-ui")
    rig = buildHost()
    finished = []
    rig.host.pageLoadFinished.connect(finished.append)
    rig.host.ShowPanel("shell-check")
    rig.reveal()

    assert _waitUntil(lambda: finished)
    assert finished == [False]
    assert rig.host.view.page().refusedLast
    assert rig.requests == []
    cover = _loadFailure(rig.host)
    assert cover is not None and cover.isVisible()

    monkeypatch.setattr(HostModule, "PanelPath", realPath)
    cover.findChild(QPushButton, "webPanelRetry").click()
    assert _waitUntil(lambda: finished == [False, True])
    assert _loadFailure(rig.host) is None
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]


def test_a_page_navigation_refused_from_a_loaded_page_keeps_it(buildHost):
    # The early return the refused first load no longer takes: the loaded
    # document is still there, nothing is told and nothing offered.
    rig = _shown(buildHost)
    finished = []
    rig.host.pageLoadFinished.connect(finished.append)
    document = rig.js("window.__loadId")

    rig.js("void (location.href = 'http://example.invalid/')")

    assert _waitUntil(lambda: rig.host.view.page().refusedLast)
    assert not _waitUntil(lambda: finished, timeout=0.5)
    assert _loadFailure(rig.host) is None
    assert rig.js("window.__loadId") == document


def test_a_view_that_cannot_be_built_is_shown_as_a_start_failure(
        buildHost, monkeypatch):
    # Built from the event loop (_BuildQueuedView): nothing above would
    # catch it, and the window's spinner would wait for a first load that
    # never comes. The start failure's label says so, the first load's end
    # is told as a failure, and no view is built again.
    def broken(*args, **kwargs):
        raise RuntimeError("no renderer")
    monkeypatch.setattr(HostModule, "LocalPageLoader", broken)
    rig = buildHost()
    finished = []
    rig.host.pageLoadFinished.connect(finished.append)
    rig.host.ShowPanel("shell-check")
    rig.stack.setCurrentIndex(0)

    assert _waitUntil(lambda: finished)
    assert finished == [False]
    assert rig.host.view is None and rig.host._loader is None
    label = rig.host.findChild(QLabel, "webPanelStartFailure")
    assert label is not None and label.isVisible()
    assert _hostWords("The page could not be shown") in label.text()
    assert "RuntimeError: no renderer" in label.text()

    rig.hide()
    rig.host.ShowPanel("second")
    rig.stack.setCurrentIndex(0)
    assert not _waitUntil(lambda: rig.host.view is not None, timeout=0.5)
    assert finished == [False]
    assert rig.requests == []


def test_the_host_follows_the_app_shells_router_and_reloads_on_it(buildHost):
    rig = _shown(buildHost)
    followed = []
    rig.host.panelChanged.connect(followed.append)
    document = rig.js("window.__loadId")

    # The AppShell's sidebar: its router moves (history.replaceState, no
    # load), its sidebar hidden and the failure it told in the query.
    rig.js("void history.replaceState(null, '',"
           " '/web/app/second?desktop=1&lng=ja&nav=collapsed&told=9')")

    assert _waitUntil(lambda: rig.host.CurrentPanel() == "second")
    assert followed == ["second"]
    assert rig.host.NavCollapsed() is True
    assert rig.js("window.__loadId") == document
    assert rig.requests == ["/web/app/shell-check?desktop=1&lng=ja"]

    # The host's own next load opens that page in that state (as after a
    # load that failed for good: _ReloadIfBlank on the next ask).
    rig.host._loaded = False
    rig.host.ShowPanel("second")
    rig.waitLoaded(count=2)
    assert rig.requests[-1] == (
        "/web/app/second?desktop=1&lng=ja&nav=collapsed&told=9")
    assert followed == ["second"]

    # Not the AppShell (another of this server's paths): not followed.
    rig.js("void history.replaceState(null, '', '/web/shell-check')")
    assert not _waitUntil(lambda: rig.host.CurrentPanel() != "second",
                          timeout=0.5)
    assert followed == ["second"]


def test_a_discarded_page_reloads_in_the_app_shells_state(buildHost):
    # Qt reloads the URL it had: the AppShell's sidebar stays hidden and the
    # failure it told is not told again.
    rig = _shown(buildHost, discardAfterMs=200)
    rig.js("void history.replaceState(null, '',"
           " '/web/app/shell-check?desktop=1&lng=ja&nav=collapsed&told=3')")
    assert _waitUntil(lambda: rig.host.NavCollapsed())

    rig.hide()
    assert _waitUntil(lambda: rig.state() == DISCARDED)
    rig.reveal()
    rig.waitLoaded(count=2)

    assert rig.requests[-1] == (
        "/web/app/shell-check?desktop=1&lng=ja&nav=collapsed&told=3")
    assert rig.host.NavCollapsed() is True
    assert rig.host._told == 3
    assert rig.host.CurrentPanel() == "shell-check"


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

    # PR4: the asset adapter held no Qt model, built no icon and needed no
    # QtGui (nor PIL, which decoded the icons) after it; ADR-005 S-final
    # SF-6c removed the adapter itself (its loader is core's GameLoader,
    # bootstrap_core()'s, and its notifications the bridge's). Neither the
    # module nor an import of it may come back, nor its models' names
    # anywhere in src.
    assert not (src / "AutonaGameAssetManager.py").exists()
    # (tests/fixtures' recorders run against an old checkout, which has it.)
    scanned = [*sorted(src.rglob("*.py")), PROJECT_ROOT / "main.py",
               *sorted((PROJECT_ROOT / "tests").glob("*.py")),
               *sorted((PROJECT_ROOT / "tests" / "live").glob("*.py"))]
    importers = [str(path.relative_to(PROJECT_ROOT)) for path in scanned
                 if _importsTheAssetAdapter(
                     ast.parse(path.read_text(encoding="utf-8")))]
    assert importers == []
    users = {str(path.relative_to(PROJECT_ROOT)):
             sorted(_codeNames(path) & set(QT_ASSET_MODEL_NAMES))
             for path in sorted(src.rglob("*.py"))}
    assert {path: names for path, names in users.items() if names} == {}
    from src.core.GameLoader import GameLoader
    for name in QT_ASSET_MODEL_PROPERTIES:
        assert not hasattr(GameLoader, name), name
    # What follows a load's selection is the loader's (core's GameLoader,
    # both shells' since ADR-005 S-final SF-6b; the adapter's until then),
    # on the loader's thread since SF-6c.
    assert list(inspect.signature(GameLoader._Publish)
                .parameters) == ["self", "snapshot", "cycle"]
    # A load the shutdown came to after its selection is published on the
    # one path every load takes (_Publish, its board operation run
    # atShutdown, telling no one): the separate settles are gone -- the
    # loader's _SettleStopped (ADR-005 S-final SF-6b; SF-6c removed it),
    # the adapter's _SettleUnannounced before it. Every call of the name
    # counts, whatever it is called on.
    loader = src / "core" / "GameLoader.py"
    assert not _codeNames(loader) & {"_SettleStopped", "_SettleUnannounced",
                                     "MarshalAborted", "_marshal"}
    publishers = sorted(
        function.name for function in ast.walk(ast.parse(
            loader.read_text(encoding="utf-8")))
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_Publish")
    assert publishers == ["_Load"], publishers


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


# ADR-005 S-final SF-7: what src/i18n/*.ts may hold. The Qt UI left is the
# native dialogs (the LAN prompt, the port guard, the folder picker, the
# shared view's retry and failure views) and the Updater's; every other word
# is a page's (i18next) or core's (core/CoreWords). Each context, with the
# modules that ask for it.
TS_CONTEXTS = {
    "app": ("src/AutonaShellOperations.py", "src/AutonaUpdater.py"),
    "portguard": ("src/Autona.py", "src/AutonaWebServer.py"),
    "updater": ("src/AutonaUpdater.py",),
    "webpanel": ("src/AutonaWebPanelHost.py",),
}
# What SF-7 took out of both .ts files (66 each: the Qt frame's sidebar,
# menus and tournament dialog, the Qt footer's "status", core's old words),
# frozen: none of it may come back translated.
SF7_REMOVED_TS_MESSAGES = (
    ("Settings", "Settings"),
    ("app", "Open reference & tester"),
    ("app", "Companion settings"),
    ("app", "OBS / Advanced Scene Switcher"),
    ("app", "Top 8 Display Settings"),
    ("app", "Help & Settings"),
    ("app", "Show sidebar"),
    ("app", "Select game title"),
    ("app", "Set tournament"),
    ("app", "Tournament Info"),
    ("app", "Setup"),
    ("app", "Display"),
    ("app", "Data"),
    ("app", "ScoreboardCategory"),
    ("app", "Stream Settings"),
    ("app", "Thumbnail Settings"),
    ("app", "Result Settings"),
    ("app", "Bracket"),
    ("app", "Board"),
    ("app", "Scoreboard"),
    ("app", "Commentary"),
    ("app", "Player List"),
    ("app", "Statistics"),
    ("app", "Always on top"),
    ("app", "Add game assets"),
    ("app", "Check for updates"),
    ("app", "Join the Discord community"),
    ("app", "Event support inquiries"),
    ("app", "About Autona"),
    ("app", "Unset tournament"),
    ("app", "Modded content"),
    ("app", "API Settings"),
    ("app", "Team {0}"),
    ("app", "Horizontal (1920x1080)"),
    ("app", "Horizontal TOP16 (1920x1080)"),
    ("app", "Vertical (960x1080)"),
    ("app", "Vertical Dark Gold 激 (960x1080)"),
    ("app", "Team / Crew (1920x1080)"),
    ("app", "Team / Crew 縦並び (1920x1080)"),
    ("app", "Stats Display Settings"),
    ("app", "Standard"),
    ("app", "Center Panel"),
    ("app", "Paste the tournament URL."),
    ("app", "For StartGG, the link must contain the /event/ part"),
    ("app", "Supported providers:"),
    ("app", "Set tournament URL"),
    ("app", "Confirm"),
    ("app", "Start.gg との接続を解除します。よろしいですか？"),
    ("app", "Error"),
    ("app", "The tournament URL could not be loaded."),
    ("app", "Make sure that your tournament URL is correctly formatted and "
            "points to an existing event, and try again."),
    ("app", "Sample Tournament"),
    ("punctuation", "("),
    ("punctuation", ")"),
    ("status", "Fetching bracket data from Start.gg..."),
    ("status", "Fetching set data from Start.gg..."),
    ("status", "Fetching the set list from Start.gg..."),
    ("status", "Fetching the set list from Start.gg... ({page}/{pages})"),
    ("status", "Some pages of the set list could not be fetched "
               "({failed}/{pages})"),
    ("status", "Fetching entrants from Start.gg..."),
    ("status", "Fetching entrants from Start.gg... ({page}/{pages})"),
    ("status", "Loading game assets... ({codename})"),
    ("status", "Building the TOP 8..."),
    ("status", "Fetching the results..."),
    ("thumb_app", "Please select a game first"),
    ("thumb_app", "Player {0} tag missing"),
)


def _askedTranslations():
    """{(context, source): {module}} of every Qt translation src/ and main.py
    ask for (tests/test_core_words.py's translationCalls: each names its
    context and source as constants, so the .ts can be checked against it)."""
    from tests.conftest import PROJECT_ROOT
    from tests.test_core_words import translationCalls

    asked = {}
    for path in [*sorted((PROJECT_ROOT / "src").rglob("*.py")),
                 PROJECT_ROOT / "main.py"]:
        for _line, context, source in translationCalls(path):
            asked.setdefault((context, source), set()).add(
                path.relative_to(PROJECT_ROOT).as_posix())
    return asked


def _tsMessages(path):
    """{(context, source): translation} of a .ts file."""
    import xml.etree.ElementTree as ET

    return {(context.findtext("name"), message.findtext("source")):
            message.findtext("translation")
            for context in ET.parse(path).getroot().findall("context")
            for message in context.findall("message")}


def test_the_ts_files_hold_the_native_dialogs_words_alone():
    from tests.conftest import PROJECT_ROOT

    asked = _askedTranslations()
    contexts = {}
    for (context, _source), modules in asked.items():
        contexts.setdefault(context, set()).update(modules)
    assert {context: tuple(sorted(modules))
            for context, modules in contexts.items()} == TS_CONTEXTS

    for language in ("en", "ja"):
        path = PROJECT_ROOT / "src" / "i18n" / f"Autona_{language}.ts"
        messages = _tsMessages(path)
        # Exactly what the code asks for, every word translated: nothing the
        # code stopped asking for stays (update_translations.ps1 runs
        # PySide6's bundled lupdate with -no-obsolete).
        assert set(messages) == set(asked), path.name
        assert all(messages.values()), path.name
        # No <location>: kept by hand, it would only drift from the code.
        assert "<location" not in path.read_text(encoding="utf-8"), path.name


def test_the_qm_files_are_compiled_from_the_ts_files():
    # The .qm the app loads (scripts/compile_translations.ps1) holds the .ts's
    # words and no other: a word taken out of the .ts is not translated.
    from qtpy.QtCore import QTranslator

    from tests.conftest import PROJECT_ROOT

    i18n = PROJECT_ROOT / "src" / "i18n"
    for language in ("en", "ja"):
        translator = QTranslator()
        assert translator.load(str(i18n / f"Autona_{language}.qm")), language
        for (context, source), translation in _tsMessages(
                i18n / f"Autona_{language}.ts").items():
            assert translator.translate(context, source) == translation, (
                language, context, source)
        translated = [(context, source)
                      for context, source in SF7_REMOVED_TS_MESSAGES
                      if translator.translate(context, source)]
        assert translated == [], language


def test_the_sf7_removed_words_are_the_frozen_ones():
    # 66 distinct entries, none of them asked for by the code again.
    assert len(set(SF7_REMOVED_TS_MESSAGES)) == len(SF7_REMOVED_TS_MESSAGES) == 66
    assert set(SF7_REMOVED_TS_MESSAGES).isdisjoint(_askedTranslations())


def test_the_qt_settings_window_is_gone():
    # ADR-005 S4af (the owner's go-ahead: no way back): the React page
    # (/web/settings; /web/app/settings from the AppShell's Help & Settings
    # menu since S-final SF-5b-3) is the only settings
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
    # its files. The menu's "Settings" is the AppShell's (i18next; the Qt
    # frame's translation went from the .ts in ADR-005 S-final SF-7).
    for name in ("Autona_en.ts", "Autona_ja.ts"):
        text = (src / "i18n" / name).read_text(encoding="utf-8")
        contexts = set(re.findall(r"<name>([^<]+)</name>", text))
        assert contexts & set(QT_SETTINGS_CONTEXTS) == set(), name
        assert "Settings\\" not in text, name
        assert "AutonaColorButton" not in text, name
        assert "<source>Settings</source>" not in text, name

    # The build collects nothing of it.
    spec = (PROJECT_ROOT / "packaging" / "autona.spec").read_text(
        encoding="utf-8")
    assert "src/Settings" not in spec and "src\\Settings" not in spec

# The Qt guide windows (ADR-005 S-final SF-4a-2): the API reference &
# tester, the Companion and OBS guides; and the About box. Pages now
# (web/src/GuidePages/, the AppShell's menu pages since SF-5b-3). The Add
# game assets dialog went the same way in SF-4b-2 (web/src/AssetsPage/,
# /web/assets).
QT_GUIDE_MODULES = ("AutonaApiInfoWindow", "AutonaIntegrationGuideWindow",
                    "AutonaObsGuideWindow", "AutonaGameAssetGuideWindow")
# What went with them: the window's accessors and slots, the About box, and
# the Settings page's own opener and memory (OpenMenuPage's then, the
# AppShell's since SF-5b-3).
QT_GUIDE_NAMES = (*QT_GUIDE_MODULES, "EnsureApiInfoWindow",
                  "EnsureCompanionGuideWindow", "EnsureObsGuideWindow",
                  "_apiInfoWindow", "_companionGuideWindow",
                  "_obsGuideWindow", "ShowAboutDialog",
                  "_CreateSettingsPanel", "_navBeforeSettings",
                  "EnsureGameAssetGuideWindow", "_gameAssetGuideWindow",
                  "_EnsureLazyWindow")
# The menu's API Settings entries (en, ja): the app context's in the Qt
# frame, the AppShell's i18next app_* since SF-5b-3 (against the frame's
# translations, frozen in tests/test_app_shell.py's QT_WORDS when ADR-005
# S-final SF-7 took them out of the .ts).
GUIDE_MENU_STRINGS = {
    "Open reference & tester": "リファレンス・テスターを開く",
    "Companion settings": "Companion の設定",
    "OBS / Advanced Scene Switcher": "OBS / Advanced Scene Switcher",
}


def test_the_qt_guide_windows_are_gone():
    import re

    from tests.conftest import PROJECT_ROOT
    from tests.test_app_shell import QT_WORDS

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
    # Add game assets is a menu page now; the window's translations went
    # with it, but the menu's two entries keep their app-context text.
    from src import AutonaWindowState
    assert "assets" in AutonaWindowState.MENU_PAGES

    # The translations: no "api" context, nothing located in the windows'
    # files. The menu's entries keep the frame's words (the AppShell's
    # test_the_shared_words_are_the_qt_frames holds it to them).
    for name in ("Autona_en.ts", "Autona_ja.ts"):
        path = src / "i18n" / name
        text = path.read_text(encoding="utf-8")
        assert "<name>api</name>" not in text, name
        for module in QT_GUIDE_MODULES:
            assert module not in text, (name, module)
        assert not re.search(r"<location[^>]*AutonaApiInfoWindow", text)
    for source, ja in GUIDE_MENU_STRINGS.items():
        assert QT_WORDS[("app", source)] == (source, ja), source

    # The build collects none of it.
    spec = (PROJECT_ROOT / "packaging" / "autona.spec").read_text(
        encoding="utf-8")
    for module in QT_GUIDE_MODULES:
        assert module not in spec, module


def test_the_about_page_names_the_inquiry_address_the_menu_mails():
    # The About box showed BUSINESS_INQUIRY_EMAIL; the page writes it out
    # (a mailto link in the shared view would go nowhere: _OpenOutside hands
    # http(s) on only), and the Help menu's "Event support inquiries" still
    # mails it. Its one place is src/AutonaExternalLinks.py since ADR-005
    # S-final SF-5a-1 (the shell request openExternal's table too).
    from src.AutonaExternalLinks import (
        BUSINESS_INQUIRY_EMAIL, BUSINESS_INQUIRY_URL, DISCORD_URL,
        EXTERNAL_LINKS)
    from tests.conftest import PROJECT_ROOT

    # The page's address is web/src/AppShell/externalLinks.ts's (the
    # AppShell's Help menu's too, S-final SF-5a-3), which the page imports.
    page = (PROJECT_ROOT / "web" / "src" / "GuidePages" / "AboutPage.tsx"
            ).read_text(encoding="utf-8")
    links = (PROJECT_ROOT / "web" / "src" / "AppShell" / "externalLinks.ts"
             ).read_text(encoding="utf-8")
    assert ('import {BUSINESS_INQUIRY_EMAIL} from "../AppShell/externalLinks";'
            in page)
    assert "BUSINESS_INQUIRY_EMAIL =" not in page
    assert (f'export const BUSINESS_INQUIRY_EMAIL = "{BUSINESS_INQUIRY_EMAIL}";'
            in links)
    assert BUSINESS_INQUIRY_URL == f"mailto:{BUSINESS_INQUIRY_EMAIL}"
    assert EXTERNAL_LINKS == {"discord": DISCORD_URL,
                              "inquiry": BUSINESS_INQUIRY_URL}
    # Defined nowhere else: the window has no menu of its own since S-final
    # SF-5b-3 (the AppShell's Help menu opens them through the shell request
    # openExternal, which reads the table).
    tree = ast.parse(_autonaSource())
    assert not [node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name)
                        and target.id in ("DISCORD_URL",
                                          "BUSINESS_INQUIRY_EMAIL",
                                          "BUSINESS_INQUIRY_URL")
                        for target in node.targets)]
    assert "AutonaExternalLinks" not in _autonaSource()
    shell = (PROJECT_ROOT / "src" / "AutonaShellOperations.py").read_text(
        encoding="utf-8")
    assert "from .AutonaExternalLinks import EXTERNAL_LINKS" in shell



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
