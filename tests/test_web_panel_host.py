"""The desktop's shared web view for the React panels (ADR-005 S4a).

AutonaWebPanelHost is one QStackedWidget page holding one QWebEngineView
for every panel moved to React. What has to hold:

* the view is not built until the host is on screen with a panel to show
  (startup does not pay for it);
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
from qtpy.QtWebEngineCore import QWebEnginePage
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from src import AutonaWebPageLifecycle as WPL
from src.AutonaWebPanelHost import AutonaWebPanelHost, PanelPath
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
        self._watching = False
        self.window.resize(640, 400)
        self.window.show()

    @property
    def page(self):
        return self.host.view.page()

    def reveal(self):
        self.stack.setCurrentIndex(0)
        # The view is built on the first reveal with a panel.
        if self.host.view is not None and not self._watching:
            self._watching = True
            self.host.view.loadFinished.connect(self.loads.append)

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
    assert _waitUntil(lambda: len(rig.loads) >= attempts, timeout=15.0)
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

    rig.reveal()

    assert rig.host.view is not None
    rig.waitLoaded()
    assert rig.requests == ["/web/shell-check?desktop=1&lng=ja"]
    assert rig.path() == ("/web/shell-check", "desktop=1&lng=ja")


def test_a_host_shown_without_a_panel_builds_nothing(buildHost):
    rig = buildHost()
    rig.reveal()

    assert rig.host.view is None
    rig.host.ShowPanel("shell-check")
    assert rig.host.view is not None


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
    rig.reveal()

    # Built, but parked behind the gate: no request reaches the port.
    assert rig.host.view is not None
    assert not _waitUntil(lambda: rig.requests, timeout=0.3)
    assert rig.host.view.url().isEmpty()

    WPL.MarkWebServerReady()

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
        loader = WPL.LocalPageLoader(view, view)
        attempts, finished = [], []
        view.loadFinished.connect(attempts.append)
        loader.finished.connect(finished.append)

        loader.load("/web/shell-check")

        assert _waitUntil(lambda: finished, timeout=15.0)
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


@pytest.mark.parametrize("stored, qt", [
    (None, False), (False, False), (True, True), ("true", False), (1, False),
])
def test_the_qt_panel_is_shown_only_when_switched_back(
        monkeypatch, stored, qt):
    from src import AutonaWebPanelHost as Host

    settings = {} if stored is None else {"qt_commentary_panel": stored}
    monkeypatch.setattr(
        Host.SettingsManager, "Get",
        lambda key, default=None: (
            settings.get(key.split(".", 1)[1], default)
            if key.startswith("general.") else default))

    assert Host.UsesQtPanel("commentary") is qt
    # A React panel without a Qt version has nothing to go back to.
    assert Host.UsesQtPanel("shell-check") is False


def test_every_fallback_is_a_react_panel_the_settings_window_offers():
    import ast
    from src import AutonaWebPanelHost as Host
    from tests.conftest import PROJECT_ROOT

    assert set(Host.QT_FALLBACK_SETTINGS) <= WebAccess.WEB_PANELS
    source = (PROJECT_ROOT / "src" / "Settings"
              / "AutonaSettingsWindow.py").read_text(encoding="utf-8")
    offered = {node.elts[1].value for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Tuple) and len(node.elts) == 4
               and isinstance(node.elts[1], ast.Constant)
               and isinstance(node.elts[2], ast.Constant)
               and node.elts[2].value == "checkbox"}
    for key in Host.QT_FALLBACK_SETTINGS.values():
        section, name = key.split(".", 1)
        assert section == "general" and name in offered, key


# The Window (src/Autona.py) is read, not imported: that import is the
# app's startup (agent-pitfalls #1).
def _windowMethod(name):
    import ast
    from tests.conftest import PROJECT_ROOT

    source = (PROJECT_ROOT / "src" / "Autona.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"src/Autona.py: no method {name}")


def _calls(node):
    import ast
    return [(ast.unparse(call.func),
             [ast.unparse(arg) for arg in call.args])
            for call in ast.walk(node) if isinstance(call, ast.Call)]


def test_the_commentary_item_opens_the_react_page_unless_switched_back():
    import ast
    factory = _windowMethod("_CreateCommentaryPanel")
    calls = _calls(factory)

    # First the switch, then the web page, and the Qt panel stays reachable.
    assert ("UsesQtPanel", ["'commentary'"]) in calls
    assert ("self._WebPanel", ["'commentary'"]) in calls
    assert ("AutonaCommentaryWidget", []) in calls
    guard = factory.body[0] if not isinstance(
        factory.body[0], ast.Expr) else factory.body[1]
    assert isinstance(guard, ast.If)
    assert ast.unparse(guard.test) == "not UsesQtPanel('commentary')"
    assert ast.unparse(guard.body[0]) == "return self._WebPanel('commentary')"

    # The sidebar item is still the lazy factory, keyed by the React
    # panel's name (_WebPanel finds the page by it).
    build = _windowMethod("_BuildUi")
    entries = [node for node in ast.walk(build) if isinstance(node, ast.Tuple)
               and node.elts
               and ast.unparse(node.elts[0]) == "self._CreateCommentaryPanel"]
    assert len(entries) == 1
    assert ast.unparse(entries[0].elts[-1]) == "'commentary'"
    assert "commentary" in WebAccess.WEB_PANELS


def test_every_reveal_of_a_react_item_tells_the_host():
    calls = _calls(_windowMethod("_OnStackPageChanged"))
    assert ("self._EnsurePanelAt", ["index"]) in calls
    assert ("page.ShowPanel", ["panel"]) in calls
    # The host is told its panel before it is first shown.
    assert ("self._webPanelHost.ShowPanel", ["panel"]) in _calls(
        _windowMethod("_WebPanel"))


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
