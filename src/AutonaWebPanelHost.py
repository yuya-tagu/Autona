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
same API as a browser.

Built lazily: constructing the host costs a plain QWidget; the view is
created the first time the host is on screen with a panel to show, so
startup (``STARTUP phase=``) does not change. From then on it lives for the
whole process and shares the overlay previews' plumbing
(AutonaWebPageLifecycle): loads wait for the web server and retry a refused
connection, and while the host is off screen the page is frozen, then
discarded after DISCARD_AFTER_MS. On reveal the page asks the server for
the full state again; a discarded page is reloaded by Qt and starts from
the full state.

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
page refuses to navigate itself anywhere else (_PanelPage), and a panel
links outside with target="_blank" only (a new window, which the view does
not make). The Window builds the host from the factory of the first
React panel opened and calls ShowPanel() on every reveal of a React
panel's sidebar item; WebPanelPages keeps the items' pages straight (S4e:
several items, one host page).

Import-light (qtpy / core / AutonaWebPageLifecycle / AutonaLocaleHelper /
ui_constants): importing it creates nothing.
"""

import json
import logging
from urllib.parse import quote, urlencode

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import QMenu, QVBoxLayout, QWidget

from .AutonaWebPageLifecycle import HiddenPageLifecycle, LocalPageLoader
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
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


# Loads the host makes itself (load(), the reload of a discarded page).
_HOST_NAVIGATIONS = (
    QWebEnginePage.NavigationType.NavigationTypeTyped,
    QWebEnginePage.NavigationType.NavigationTypeReload,
)


class _PanelPage(QWebEnginePage):
    """The shared view's page: it stays on this server's pages.

    The invariant the clipboard rests on (S4e): page script may write the
    clipboard (JavascriptCanAccessClipboard), so the view must never show
    a page that is not this server's. The panels keep to it -- an outside
    link opens a new window (target="_blank"), which the view does not
    make -- and this enforces it: a navigation the page starts itself (a
    link, a form, script, a redirect) of the main frame is refused unless it
    stays on the page's own origin (that of the URL the host loaded). The
    host's own loads go through.

    Qt reports a refused navigation as a failed load (loadFinished(False))
    although the document stays: ``refusedLast`` tells the host so, until
    the next main-frame navigation goes through."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.refusedLast = False

    def acceptNavigationRequest(self, url, navigationType, isMainFrame):
        if not isMainFrame:
            return True
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
        # Built on first show with a panel (_EnsureView).
        self.view = None
        self._loader = None
        self._lifecycle = None
        # Whether the view's last finished load succeeded: False until the
        # first one does, and after one that failed for good (_ReloadIfBlank).
        self._loaded = False
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def ShowPanel(self, panel):
        """Show React panel ``panel`` (a WebAccess.WEB_PANELS name).

        Before the view exists this only records the panel: the view is
        built, on that panel's page, once the host is on screen. Afterwards
        the Web UI's router navigates to it; the view is not reloaded --
        unless it holds no loaded page (a load that failed), which is then
        loaded again, on this panel.
        """
        if not WebAccess.IsWebPanel(panel):
            raise ValueError(f"not a React panel: {panel!r}")
        self._panel = panel
        if self.view is None:
            if self.isVisible():
                self._EnsureView()
            return
        if not self._ReloadIfBlank():
            self._Navigate()

    def showEvent(self, event):
        super().showEvent(event)
        if self.view is None and self._panel is not None:
            self._EnsureView()

    def _EnsureView(self):
        self.view = _PanelView(self)
        page = _PanelPage(self.view)
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

    def _Navigate(self):
        # Only into an Active, loaded page. A frozen page is thawed by Qt
        # when shown and caught up in _OnActive; a discarded one is
        # reloaded by Qt and caught up in _OnLoadFinished; a load in flight
        # is caught up when it finishes. The page skips a navigation to the
        # panel it already shows.
        if self.view is None or self._loader.IsLoading():
            return
        page = self.view.page()
        if (page.lifecycleState() != QWebEnginePage.LifecycleState.Active
                or self.view.url().isEmpty()):
            return
        page.runJavaScript(_NAVIGATE_JS % json.dumps(self._panel))

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
        # a panel other than the one asked for since.
        self._loaded = ok
        if ok:
            self._Navigate()
        else:
            logger.warning("The web panel view failed to load (%s)",
                           self._panel)
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
