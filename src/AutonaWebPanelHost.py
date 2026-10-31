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
not on it (S4b). The Window builds the host from a panel's factory and
calls ShowPanel() on every reveal of that panel's sidebar item; until S4c a
panel can be switched back to its Qt version by one setting
(QT_FALLBACK_SETTINGS, read when the panel is first built).

Import-light (qtpy / core / AutonaWebPageLifecycle / AutonaLocaleHelper /
ui_constants): importing it creates nothing.
"""

import json
import logging
from urllib.parse import quote, urlencode

from qtpy.QtCore import Qt
from qtpy.QtGui import QColor
from qtpy.QtWebEngineCore import QWebEnginePage
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWidgets import QMenu, QVBoxLayout, QWidget

from .AutonaWebPageLifecycle import HiddenPageLifecycle, LocalPageLoader
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .core import WebAccess
from .core.SettingsManager import SettingsManager
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


# The way back to a panel's Qt version while the IME gate is open (ADR-005
# S4b; S4c removes these with the Qt panels): React panel -> the boolean
# general setting that shows the Qt panel instead. Off by default.
QT_FALLBACK_SETTINGS = {
    "commentary": "general.qt_commentary_panel",
}


def PanelPath(panel):
    """The path + query the host opens for ``panel``."""
    query = urlencode({"desktop": "1",
                       "lng": AutonaLocaleHelper.programLocale})
    return f"/web/{quote(panel, safe='')}?{query}"


def UsesQtPanel(panel):
    """Whether the desktop shows ``panel``'s Qt version instead of the React
    page (the operator's QT_FALLBACK_SETTINGS switch). False for a panel
    without one."""
    key = QT_FALLBACK_SETTINGS.get(panel)
    return bool(key and SettingsManager.Get(key, False) is True)


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
        page = self.view.page()
        # The Web UI's background (dark only), instead of a white flash
        # while the first page loads.
        page.setBackgroundColor(QColor(UI.palette()["canvas"]))
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
