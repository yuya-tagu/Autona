"""The desktop's page for the panels moved to React (ADR-005 S4).

One QWebEngineView, shared by every React panel, as one page of the main
window's QStackedWidget. Showing another React panel does not reload the
view: the Web UI's router is told to navigate (``window.autonaPanelShell``,
web/src/PanelShell/hostApi.ts), so however many panels move, there is one
renderer process -- the shape S-final (the window as a single web view)
grows into.

The page is the Web UI's ``/web/<panel>`` on the app's own web server
(core.WebAccess.WEB_PANELS), with ``desktop=1`` (the Web UI hides what only
a browser needs) and ``lng=`` the Qt UI language. It is opened from this
PC, so it writes without the LAN token, like any of this server's own pages
here; there is no privileged channel (no QWebChannel) -- the page uses the
same API as a browser. Besides navigating and the full state, the host asks
a page for one thing through the panel shell: SCORE's set selection dialog,
for the load_set hotkey and the legacy open-set route (OpenScoreSets,
ADR-005 S4ab-2). It writes nothing; the page opens it as its own button.

Built lazily: constructing the host costs a plain QWidget; the view is
created the first time the host is on screen with a panel to show, once
the web server is ready -- and then from the event loop, not inside the
show or the readiness call that allows it (_ScheduleView; ADR-005 S4g
review M4). Its page cannot be shown before the server is up anyway, and
the view's construction is Chromium's start-up: the sidebar's default item
is the Event page, so a fresh install's window is shown with the host as
its current page, and a view built then would hold up the window's first
paint, the loading spinner and the web server's start (_StartWebServer,
which marks the server ready: STARTUP phase=webserver-ready is logged
before the view is built). Nothing waits for a paint, so a window started
minimized builds it too (S4g review L14). From then on it lives for the
whole process. Through AutonaWebPageLifecycle, loads wait for the web server
and retry a refused connection, and while the host is off screen the page is
frozen, then discarded after DISCARD_AFTER_MS. On reveal the page asks the
server for the full state again; a discarded page is reloaded by Qt and
starts from the full state.

Discarding is Qt's call, and Qt withholds it from a page the operator has
typed into (Chromium's form-interaction rule: a reload would lose the
input). The host never reloads its document, so once any React panel's
field has been edited the page is only ever frozen from then on: its
renderer stays for the rest of the process. That is one renderer however
many panels moved, using no CPU while frozen; not measured yet, but less
than an overlay's ~200 MB, most of which is the kuromoji dictionary the Web
UI does not load. A page nobody typed into is discarded as above.

A load that fails for good (the loader's retries used up, or Qt's reload of
a discarded page refused) leaves the view on an error page. The next
ShowPanel() or return to Active loads the panel's page again rather than
navigating a page that has no panel shell.

The right-click menu offers editing only (ContextActions): the view is a
panel, not a browser, so Chromium's back / reload / view-source entries are
not on it (S4b). Page script may write the clipboard (S4e: the Stream
page's thumbnail copy), so the view shows this server's pages only: the
page refuses to navigate itself anywhere else (_PanelPage). A panel links
outside by a new window (target="_blank", window.open from a click): the view
makes none, and hands an http(s) one to the default browser, the X post's
composer among them (S4r). The one new window the view keeps to itself is the
Board page's Designer (S4s): this server's /web?view=designer, from a click,
opens AutonaDesignerWindow, which holds the Designer's token. No token reaches
the page and there is no shell operation for it. The SCORE page's "ブラウザ版を
開く" (S4aa) is the other: this server's /scoreboard or /team, from a click,
goes to the default browser as the Qt board's button opened it -- the address
other devices reach and, with LAN access on, the token -- built here at the
click, so the token does not reach the page either. The Window builds the host
from the factory of the first React panel opened and calls ShowPanel() on every
reveal of a React panel's sidebar item; WebPanelPages keeps the items' pages
straight (S4e: several items, one host page). Every sidebar item is a React
panel: the last Qt version kept behind a setting, SCORE's (S4aa), went with
the Qt boards in ADR-005 S4ac-3 (Commentary's in S4c). So is the Settings
page (S4af), which is not a sidebar item: the Help & Settings menu opens it
(Window.OpenSettings), as a page of its own after the items'.

Importing it creates nothing.
"""

import json
import logging
from urllib.parse import quote, urlencode

from qtpy.QtCore import Qt, QTimer, QUrl, Signal
from qtpy.QtGui import QColor, QDesktopServices
from qtpy.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import QMenu, QVBoxLayout, QWidget

from .AutonaDesignerWindow import AutonaDesignerWindow
from .AutonaWebPageLifecycle import (
    HiddenPageLifecycle, IsWebServerReady, LocalPageLoader,
    WhenWebServerReady)
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaNetworkHelper import GetLocalIP, GetLocalIPs
from .core import WebAccess
from . import ui_constants as UI

logger = logging.getLogger(__name__)

# The Web UI's side (web/src/PanelShell/hostApi.ts). Both are no-ops on a
# page without the panel shell (a load that failed, an error page).
_NAVIGATE_JS = (
    "(function(p){var s=window.autonaPanelShell;"
    "if(s){s.navigate(p);}})(%s)")
_REQUEST_FULL_STATE_JS = (
    "(function(){var s=window.autonaPanelShell;"
    "if(s){s.refresh();}})()")
# SCORE's set selection dialog for a board (ADR-005 S4ab-2): "taken" once a
# panel shell had it (it keeps it for the page until the page takes it), ""
# where there is none to take it -- no document yet, or a Web UI built before
# the call existed.
_OPEN_SCORE_SETS_JS = (
    "(function(b){var s=window.autonaPanelShell;"
    "if(s&&s.openScoreSets){s.openScoreSets(b);return 'taken';}"
    "return '';})(%s)")
_SCORE_SETS_TAKEN = "taken"

# The React panel whose set selection the desktop opens from outside the page
# (the load_set hotkey, the legacy open-set route).
SCORE_PANEL = "scoreboard"


def PanelPath(panel):
    """The path + query the host opens for ``panel``."""
    query = urlencode({"desktop": "1",
                       "lng": AutonaLocaleHelper.programLocale})
    return f"/web/{quote(panel, safe='')}?{query}"


def ContextActions(editable, hasSelection):
    """The page actions the right-click menu offers (None: a separator).

    Editing in a text field, copying a selection elsewhere, and nothing
    otherwise -- no menu. Chromium enables each action for the click's
    context (Paste with something on the clipboard, Cut with a selection).
    """
    W = QWebEnginePage.WebAction
    if editable:
        return [W.Undo, W.Redo, None, W.Cut, W.Copy, W.Paste, None,
                W.SelectAll]
    if hasSelection:
        return [W.Copy]
    return []


def _Origin(url):
    return (url.scheme(), url.host(), url.port())


# This PC's LAN addresses, looked up on first use and kept (as the web server
# keeps its own: WebServer._local_addresses).
_lanHosts = None


def _LanHosts():
    global _lanHosts
    if _lanHosts is None:
        _lanHosts = GetLocalIPs()
    return _lanHosts


def _Port(url):
    # The port, the scheme's default spelled out (-1 for one without).
    return url.port({"http": 80, "https": 443}.get(url.scheme(), -1))


def _IsServerOrigin(url, pageUrls):
    """Whether ``url`` is on this server's own origin: that of one of
    ``pageUrls`` (the page's), or the server's port under a loopback name or
    this PC's LAN address (WebAccess.OwnOrigins) -- what passes as its own
    page there."""
    # Only http(s) is served (an about: / data: frame is never the server's,
    # even while the page itself is still about:blank).
    if url.scheme() not in _OUTSIDE_SCHEMES:
        return False
    if any(_Origin(url) == _Origin(page) for page in pageUrls):
        return True
    port = _Port(url)
    if port <= 0 or port not in {_Port(page) for page in pageUrls}:
        return False
    # The origin alone: a userinfo ("x@127.0.0.1") does not make it another.
    # The LAN addresses are looked up only for the server's port.
    host = url.host()
    if ":" in host:
        host = f"[{host}]"
    return (WebAccess.NormalizeOrigin(f"{url.scheme()}://{host}:{port}")
            in WebAccess.OwnOrigins(port, _LanHosts()))


def _IsDesignerUrl(url, pageUrl):
    """Whether a new window asks for this server's Designer: exactly the URL the
    Board page opens (web/src/BoardPage), on the origin of the page asking."""
    return (_Origin(url) == _Origin(pageUrl)
            and url.path() == "/web"
            and url.query() == "view=designer"
            and not url.hasFragment()
            and not url.userInfo())


# The browser version of the scoreboards the SCORE page opens (S4aa): the
# paths the Qt boards' "ブラウザ版を開く" opened (board 2 "team", board 1
# "scoreboard"; the Qt boards went in ADR-005 S4ac-3).
_BROWSER_SCOREBOARD_PATHS = ("/scoreboard", "/team")


def _IsBrowserScoreboardUrl(url, pageUrl):
    """Whether a new window asks for this server's browser scoreboard: exactly
    the URL the SCORE page opens (web/src/ScorePage), on the origin of the page
    asking -- no query, so nothing of the page's own is carried along."""
    return (_Origin(url) == _Origin(pageUrl)
            and url.path() in _BROWSER_SCOREBOARD_PATHS
            and not url.hasQuery()
            and not url.hasFragment()
            and not url.userInfo())


def BrowserScoreboardUrl(path):
    """The URL the SCORE page's "ブラウザ版を開く" opens for ``path``
    ("/scoreboard" or "/team"; the shape of the Qt board's button until
    ADR-005 S4ac-3), read at the click: the address the web server is
    reached at (the LAN address with LAN access on), and the access token only
    with LAN access on, so the same URL works when copied to another device
    (the page takes it off the address bar: web/src/autonaToken.ts). With LAN
    access off it has none: a page on this PC needs none.

    The port and LAN access are what the server really listens on (a change
    in the settings takes effect at its next start), the settings only while
    none runs: WebServer.Serving(), as every URL that names the server
    (ADR-005 S4af). (The Qt board's button read the settings when the board
    was built.)"""
    from .AutonaWebServer import WebServer
    port, lan = WebServer.Serving()
    url = f"http://{GetLocalIP() if lan else '127.0.0.1'}:{port}{path}"
    token = WebAccess.Token()
    if not token or not lan:
        return url
    return f"{url}?" + urlencode({"token": token})


# Loads the host makes itself (load(), the reload of a discarded page).
_HOST_NAVIGATIONS = (
    QWebEnginePage.NavigationType.NavigationTypeTyped,
    QWebEnginePage.NavigationType.NavigationTypeReload,
)

# What a new window of a panel's may be opened with: the web. Never file: or a
# scheme an installed application registered, which would have a page of the
# network's start whatever it names.
_OUTSIDE_SCHEMES = ("http", "https")


class _PanelPage(QWebEnginePage):
    """The shared view's page: it stays on this server's pages.

    The invariant the clipboard rests on (S4e): page script may write the
    clipboard (JavascriptCanAccessClipboard), so the view must never show
    a page that is not this server's. The panels keep to it -- an outside
    link opens a new window, which the view does not make -- and this enforces
    it: a navigation the page starts itself (a link, a form, script, a
    redirect) of the main frame is refused unless it stays on the page's own
    origin (that of the URL the host loaded). The host's own loads go through.

    A new window goes to the default browser instead, when it is the web's
    (S4r), but for the Designer's URL, which is the host's (designerRequested,
    S4s), and the browser scoreboard's, which goes to the default browser as
    built at the click (BrowserScoreboardUrl, S4aa: the shape of the Qt
    board's button, removed in ADR-005 S4ac-3). Script
    without the operator's click does not get here (JavascriptCanOpenWindows,
    _EnsureView).

    Qt reports a refused navigation as a failed load (loadFinished(False))
    although the document stays: ``refusedLast`` tells the host so, until
    the next main-frame navigation goes through.

    A frame's navigation goes through, except to this server's own origin
    (ADR-005 S-final SF-8a review): the page's frames are the design
    previews, on the preview listener's origin, and whatever a layout frames
    of its own (a widget elsewhere). A layout's script there could navigate
    its frame -- or a frame it makes -- to this server's port, and run as
    one of its pages: the panels' DOM, the writes without the token. Qt does
    not say which frame asks, and no page of the panels frames this server's
    own origin, so every such navigation of a frame is refused. The server
    refuses it as well (WebAccess.FramedElsewhere, from Sec-Fetch-*), and
    that is what holds where this page is not the one framing (a browser's
    Web UI)."""

    designerRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.refusedLast = False
        self.newWindowRequested.connect(self._OpenOutside)

    def _OpenOutside(self, request):
        # Not opened in the view: nothing calls request.openIn(), and the
        # view's createWindow() makes no page. Only for a click: a page of the
        # panels asks in one, and the operating system's browser is not to be
        # started by a script alone. The host only in the log: the query of the
        # post on X is the operator's text.
        url = request.requestedUrl()
        if not (url.isValid() and url.scheme() in _OUTSIDE_SCHEMES):
            logger.warning("The web panel view refused to open %s",
                           url.toDisplayString())
        elif not request.isUserInitiated():
            logger.warning("The web panel view refused a window no click "
                           "asked for, of %s", url.host())
        elif _IsDesignerUrl(url, self.url()):
            self.designerRequested.emit()
        elif _IsBrowserScoreboardUrl(url, self.url()):
            # Rebuilt here, not the page's: the token is never the page's.
            # The host only in the log (the URL may carry the token).
            outside = QUrl(BrowserScoreboardUrl(url.path()))
            if not QDesktopServices.openUrl(outside):
                logger.warning("The system could not open a link of %s",
                               outside.host())
        elif not QDesktopServices.openUrl(url):
            logger.warning("The system could not open a link of %s", url.host())

    def acceptNavigationRequest(self, url, navigationType, isMainFrame):
        if not isMainFrame:
            if not _IsServerOrigin(url, (self.url(), self.requestedUrl())):
                return True
            # Not refusedLast: the main frame's document is untouched.
            logger.warning("The web panel view refused a frame of this "
                           "server's own origin, %s", url.toDisplayString())
            return False
        if navigationType in _HOST_NAVIGATIONS or _Origin(url) in (
                _Origin(self.url()), _Origin(self.requestedUrl())):
            self.refusedLast = False
            return True
        self.refusedLast = True
        logger.warning("The web panel view refused to leave this server "
                       "for %s", url.toDisplayString())
        return False


class _PanelView(QWebEngineView):
    """The shared view, with the editing menu instead of the browser's."""

    def contextMenuEvent(self, event):
        # Qt calls this for the page's context menu request; the request
        # says what was clicked.
        request = self.lastContextMenuRequest()
        actions = ContextActions(
            bool(request is not None and request.isContentEditable()),
            bool(request is not None and request.selectedText()))
        event.accept()
        if not actions:
            return
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        for action in actions:
            if action is None:
                menu.addSeparator()
            else:
                menu.addAction(self.pageAction(action))
        menu.popup(event.globalPos())


class WebPanelPages:
    """Which stack page each sidebar item shows, when several items are
    React panels sharing the one host (ADR-005 S4e; S4a decision 1).

    Every sidebar item keeps a stack page of its own -- nav index == stack
    index, as for every Qt panel, so the saved sidebar index and the item
    count do not change. The React items' pages are placeholders, and the
    first one opened becomes the host's page (its factory builds the host
    there). From then on every React item shows that page, and the host is
    told which panel: the one the operator chose last (``selected``). The
    other React items' placeholders are never built (``HostBuilt``).

    The Settings page (ADR-005 S4af) is kept the same way, though no
    sidebar item opens it: its page comes after the items' (the menu's
    Window.OpenSettings asks for it as a click asks for an item).

    Plain bookkeeping (no Qt): the Window asks it on every sidebar choice
    and every page reveal.
    """

    def __init__(self):
        self._panels = {}       # nav index -> WebAccess.WEB_PANELS name
        self.hostIndex = None   # the host's stack page, once it is built
        self.selected = None    # the React panel chosen last

    def Add(self, index, panel):
        """Sidebar item / stack page `index` is React panel `panel`."""
        if not WebAccess.IsWebPanel(panel):
            raise ValueError(f"not a React panel: {panel!r}")
        self._panels[index] = panel

    def StackIndex(self, nav):
        """The stack page that shows sidebar item `nav`: its own, but for a
        React item once the host is built (the host's). Choosing a React
        item records it as the panel to show."""
        panel = self._panels.get(nav)
        if panel is None:
            return nav
        self.selected = panel
        return nav if self.hostIndex is None else self.hostIndex

    def HostBuilt(self, index):
        """The host was built on stack page `index`: the other React items'
        pages, which are never to be built (a second host)."""
        self.hostIndex = index
        return sorted(other for other in self._panels if other != index)

    def Shown(self, index):
        """The React panel stack page `index` shows: the one chosen last on
        the host's page (that page's own before any choice), None on any
        other page."""
        if index != self.hostIndex:
            return None
        return self.selected or self._panels.get(index)


class AutonaWebPanelHost(QWidget):
    """The shared web view for the React panels, as a stack page."""

    DISCARD_AFTER_MS = HiddenPageLifecycle.DISCARD_AFTER_MS

    def __init__(self, parent=None):
        super().__init__(parent)
        # The panel the operator asked for last. The page catches up to it
        # whenever it can run script (see _Navigate).
        self._panel = None
        # Built on first show with a panel (_ScheduleView -> _EnsureView).
        self.view = None
        # Whether a build is queued for the event loop, and whether the
        # host waits for the web server to queue one (_ScheduleView).
        self._viewPending = False
        self._awaitingServer = False
        self._loader = None
        self._lifecycle = None
        # Whether the view's last finished load succeeded: False until the
        # first one does, and after one that failed for good (_ReloadIfBlank).
        self._loaded = False
        # The Designer's window while it is open (_OpenDesigner).
        self._designerWindow = None
        # SCORE's set selection asked for and not yet taken by a panel shell
        # (OpenScoreSets): (request number, board), or None. And the number of
        # the one run into the view's document whose answer has not come back
        # (_DeliverScoreSets), or None.
        self._scoreSets = None
        self._scoreSetsAsked = 0
        self._scoreSetsRunning = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def OpenScoreSets(self, board):
        """Have the SCORE page open board ``board``'s set selection dialog
        (ADR-005 S4ab-2: the load_set hotkey and the legacy open-set route,
        through Window.OpenScoreSets, which shows SCORE first).

        Handed to the page's panel shell (``window.autonaPanelShell.
        openScoreSets``, web/src/PanelShell/hostApi.ts) as a navigation is:
        now, into an Active, loaded page, or else when the view's first load,
        a thaw or Qt's reload of a discarded page lets it (_Navigate), after
        the navigation to SCORE. Held until a panel shell answers that it has
        it (a script run into a document going away is lost): run once into
        a document, and again only after an answer that it was not taken (a
        Web UI built before the call) or into the next document (a load). A
        newer request replaces it; another panel shown before it got there
        forgets it (ShowPanel), and so does a load that failed (_OnLoadFinished):
        the dialog is not opened later, out of the blue. Not a Socket.IO
        notice: only this PC's view is to open it, not every client's page.
        """
        if self._panel != SCORE_PANEL:
            logger.warning("The set selection was asked of the web panel "
                           "view while it shows %s", self._panel)
            return
        self._scoreSetsAsked += 1
        self._scoreSets = (self._scoreSetsAsked, board)
        self._DeliverScoreSets(self._ScriptablePage())

    def _DeliverScoreSets(self, page):
        # Into `page` (an Active, loaded page, or None: not now), once SCORE is
        # the panel it shows.
        if (page is None or self._scoreSets is None
                or self._panel != SCORE_PANEL):
            return
        asked, board = self._scoreSets
        if self._scoreSetsRunning == asked:
            # Run into this document already; its answer decides (a reveal or
            # a click on SCORE before it comes would hand it over twice).
            return
        self._scoreSetsRunning = asked
        page.runJavaScript(
            _OPEN_SCORE_SETS_JS % json.dumps(board), 0,
            lambda result, asked=asked: self._ScoreSetsTaken(asked, result))

    def _ScoreSetsTaken(self, asked, result):
        # The answer to request `asked`: no longer running, and taken by a
        # panel shell (unless a newer request came since, which stays).
        if self._scoreSetsRunning == asked:
            self._scoreSetsRunning = None
        if (result == _SCORE_SETS_TAKEN and self._scoreSets is not None
                and self._scoreSets[0] == asked):
            self._scoreSets = None

    def ShowPanel(self, panel):
        """Show React panel ``panel`` (a WebAccess.WEB_PANELS name).

        Before the view exists this only records the panel: the view is
        built, on that panel's page, once the host is on screen and the web
        server is ready (from the event loop: _ScheduleView). Afterwards
        the Web UI's router navigates to it; the view is not reloaded --
        unless it holds no loaded page (a load that failed), which is then
        loaded again, on this panel.
        """
        if not WebAccess.IsWebPanel(panel):
            raise ValueError(f"not a React panel: {panel!r}")
        self._panel = panel
        if panel != SCORE_PANEL:
            # The operator went elsewhere before SCORE's set selection got
            # to the page: not to be opened later (OpenScoreSets).
            self._scoreSets = None
        if self.view is None:
            self._ScheduleView()
            return
        if not self._ReloadIfBlank():
            self._Navigate()

    def showEvent(self, event):
        super().showEvent(event)
        if self.view is None:
            self._ScheduleView()

    def _ScheduleView(self):
        # The view is built from the event loop once the host is on screen
        # with a panel and the web server is ready (see the module
        # docstring): until then this registers for the readiness, once.
        # Queued, not built here: at startup the readiness comes inside
        # _StartWebServer() (MarkWebServerReady), and the zero timer runs
        # once it has returned. Queued once; a host hidden again before the
        # build runs builds nothing, and queues anew on its next show.
        # (Pure scheduling: nothing here waits.)
        if (self.view is not None or self._panel is None
                or self._viewPending):
            return
        if not IsWebServerReady():
            if not self._awaitingServer:
                self._awaitingServer = True
                WhenWebServerReady(self._OnWebServerReady)
            return
        if not self.isVisible():
            return
        self._viewPending = True
        QTimer.singleShot(0, self, self._BuildQueuedView)

    def _OnWebServerReady(self):
        self._awaitingServer = False
        self._ScheduleView()

    def _BuildQueuedView(self):
        self._viewPending = False
        if self.view is None and self._panel is not None and self.isVisible():
            self._EnsureView()

    def _EnsureView(self):
        self.view = _PanelView(self)
        page = _PanelPage(self.view)
        page.designerRequested.connect(self._QueueDesigner)
        self.view.setPage(page)
        # The Web UI's background (dark only), instead of a white flash
        # while the first page loads.
        page.setBackgroundColor(QColor(UI.palette()["canvas"]))
        # The Stream page copies the made thumbnail to the clipboard
        # (navigator.clipboard.write, an image -- what the Qt panel's copy
        # button did, S4e). Qt refuses page script the clipboard by
        # default, and the write then never settles. Safe as the view only
        # ever shows this server's own pages (_PanelPage).
        self.view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        # On by default: script could then ask for new windows by itself, and
        # each would be a page of the default browser's (_PanelPage). From a
        # click it still may (measured on PySide6 6.11: window.open in a click
        # reaches newWindowRequested with this off, without a click it does
        # not), which is all a panel does.
        self.view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        # From a click Qt would otherwise hand a link of a scheme it does not
        # know (ms-calculator:) to the operating system by itself, a frame's
        # without asking _PanelPage.
        self.view.settings().setUnknownUrlSchemePolicy(
            QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)
        self._layout.addWidget(self.view)
        self._loader = LocalPageLoader(self.view, self)
        self._loader.finished.connect(self._OnLoadFinished)
        self._lifecycle = HiddenPageLifecycle(
            page,
            busy=self._loader.IsLoading,
            onActive=self._OnActive,
            discardAfterMs=self.DISCARD_AFTER_MS,
            parent=self)
        logger.debug("Built the web panel view (%s)", self._panel)
        self._loader.load(PanelPath(self._panel))

    def _QueueDesigner(self):
        # Not inside Chromium's new-window call: building the window starts a
        # web view of its own.
        QTimer.singleShot(0, self, self._OpenDesigner)

    def _OpenDesigner(self):
        # One window, brought to the front when it is open already. The main
        # window's, as it lived under the Qt Board panel: closed with it. It
        # deletes itself on close, revoking the Designer's token.
        if self._designerWindow is None:
            self._designerWindow = AutonaDesignerWindow(self.window())
            self._designerWindow.destroyed.connect(self._OnDesignerDestroyed)
        self._designerWindow.show()
        self._designerWindow.raise_()
        self._designerWindow.activateWindow()

    def _OnDesignerDestroyed(self):
        self._designerWindow = None

    def _ScriptablePage(self):
        # The view's page when script can be run into it -- an Active, loaded
        # page -- else None. A frozen page is thawed by Qt when shown and
        # caught up in _OnActive; a discarded one is reloaded by Qt and caught
        # up in _OnLoadFinished; a load in flight is caught up when it
        # finishes.
        if self.view is None or self._loader.IsLoading():
            return None
        page = self.view.page()
        if (page.lifecycleState() != QWebEnginePage.LifecycleState.Active
                or self.view.url().isEmpty()):
            return None
        return page

    def _Navigate(self):
        # Only into an Active, loaded page (_ScriptablePage). The page skips a
        # navigation to the panel it already shows. SCORE's set selection
        # asked for meanwhile follows the navigation (OpenScoreSets).
        page = self._ScriptablePage()
        if page is None:
            return
        page.runJavaScript(_NAVIGATE_JS % json.dumps(self._panel))
        self._DeliverScoreSets(page)

    def _ReloadIfBlank(self):
        # The last load failed for good (the loader gave up, or Qt's reload
        # of a discarded page was refused): the view shows an error page
        # with no panel shell to navigate, and nothing else would load it
        # again before the process ends. Not from _OnLoadFinished, so a
        # failing server is retried once per operator action, not in a
        # loop. While a load is parked behind the readiness gate this only
        # updates the path it will load.
        if self._loaded or self._loader.IsLoading():
            return False
        logger.info("Reloading the web panel view (%s)", self._panel)
        self._loader.load(PanelPath(self._panel))
        return True

    def _OnLoadFinished(self, ok):
        if not ok and self.view.page().refusedLast:
            # A navigation the page started and _PanelPage refused: the
            # document is still there, loaded (not to be reloaded).
            return
        # Ours, or Qt's reload of a discarded page: that document may show
        # a panel other than the one asked for since. A new document: what
        # was run into the last one is not waited for (its answer may never
        # come).
        self._loaded = ok
        self._scoreSetsRunning = None
        if ok:
            self._Navigate()
        else:
            logger.warning("The web panel view failed to load (%s)",
                           self._panel)
            if self._scoreSets is not None:
                # Not handed to a later load that succeeds for another
                # reason (a reveal, another panel's click): the operator did
                # not ask for the dialog then (OpenScoreSets).
                logger.warning("The set selection of scoreboard %s asked "
                               "for is dropped: the view failed to load",
                               self._scoreSets[1])
                self._scoreSets = None
        self._lifecycle.ScheduleFreeze()

    def _OnActive(self):
        # Back on screen. From Frozen, the deltas broadcast meanwhile were
        # queued, or lost with a connection the server dropped on a missed
        # ping (socket.io then reconnects and gets the full state); ask for
        # the full state either way. From Discarded, Qt is reloading the
        # page and this runs into the old document or none (harmless); the
        # new document starts from the full state. A view whose last load
        # failed is loaded again instead.
        if self._ReloadIfBlank():
            return
        self._Navigate()
        self.view.page().runJavaScript(_REQUEST_FULL_STATE_JS)
