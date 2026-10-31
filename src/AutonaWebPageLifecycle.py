"""Page plumbing shared by the embedded web views (ADR-005 S4a).

Two kinds of embedded QWebEngineView load pages from the app's own web
server and live for the whole process: the overlay previews
(AutonaLayoutPreview) and the React panel host (AutonaWebPanelHost). What
they have in common lives here, extracted from AutonaLayoutPreview so that
neither keeps its own copy:

* the web server readiness gate (MarkWebServerReady): a load issued before
  the server is up is parked and replayed once it is;
* LocalPageLoader: loads a page of the local web server into one view,
  retrying a refused connection a bounded number of times;
* HiddenPageLifecycle: freezes the page while nothing of it is on screen,
  discards it after a delay, and calls the owner back when it is Active
  again so the owner can bring it up to date.

Import-light on purpose (qtpy / SettingsManager only): this module must not
pull the singleton chain, so it can sit anywhere in Autona.py's import
order, and importing it creates nothing.
"""

import logging

from qtpy.QtCore import QObject, QTimer, QUrl, Signal
from qtpy.QtWebEngineCore import QWebEnginePage

from .core.SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT

logger = logging.getLogger(__name__)


# ── Web server readiness gate (startup Step 4c) ──────────────────────────
# The web server is started from the event loop after the main window is
# shown, so the page restored as the startup page issues its first load()
# while nothing is listening on the port yet. QWebEngineView does not retry
# a refused connection, which would leave that page permanently blank, so
# early load() calls are parked here and replayed once the server is up.
_webServerReady = False
_pendingLoaders = []


def MarkWebServerReady():
    """Announce that the web server thread has been started.

    Called once from Window._StartWebServer(). Idempotent.
    """
    global _webServerReady
    if _webServerReady:
        return
    _webServerReady = True
    pending = list(_pendingLoaders)
    _pendingLoaders.clear()
    for loader in pending:
        try:
            loader._LoadNow()
        except RuntimeError:
            # Underlying C++ view already deleted — nothing to load.
            logger.debug("Skipped deferred page load: view is gone")


def LocalUrl(path):
    """The URL of ``path`` (``/...``, query included) on the local web
    server, at the port configured now."""
    port = SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)
    return f"http://127.0.0.1:{port}{path}"


class LocalPageLoader(QObject):
    """Loads pages of the local web server into one QWebEngineView.

    ``load(path)`` waits for the readiness gate above, then loads; the last
    path asked for wins, including one asked for while parked. A load that
    fails is retried MAX_LOAD_RETRIES times: MarkWebServerReady() fires
    right after the server thread is spawned, so the first request can
    still lose a race of a few milliseconds against the bind; without this
    the page would stay blank until the operator switched pages.

    ``finished(ok)`` is emitted when a load of the view is over and will not
    be retried -- including a load Qt started itself (the reload of a
    discarded page), so owners redo their per-document work there.
    """

    MAX_LOAD_RETRIES = 2
    LOAD_RETRY_MS = 400

    finished = Signal(bool)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self._view = view
        self._path = None
        # Retry state for the short window between WebServer.start() (the
        # thread is spawned) and the socket actually being bound.
        self._loadRetries = 0
        self._pendingUrl = None
        view.loadFinished.connect(self._OnLoadFinished)

    @property
    def pendingUrl(self):
        """The URL being loaded (retries included), or None. None while a
        load is parked behind the readiness gate: nothing is loading yet."""
        return self._pendingUrl

    def IsLoading(self):
        return self._pendingUrl is not None

    def load(self, path):
        self._path = path
        if not _webServerReady:
            # Nothing can serve the URL yet — remember this loader and let
            # MarkWebServerReady() replay it with whatever path is current
            # by then (see the module-level gate).
            if self not in _pendingLoaders:
                _pendingLoaders.append(self)
            return
        self._LoadNow()

    def _LoadNow(self):
        url = LocalUrl(self._path)
        self._loadRetries = 0
        self._pendingUrl = url
        self._view.setUrl(QUrl(url))

    def _OnLoadFinished(self, ok):
        if not ok and self._pendingUrl and self._loadRetries < self.MAX_LOAD_RETRIES:
            self._loadRetries += 1
            logger.debug(
                "Page load failed (%s), retry %d",
                self._pendingUrl, self._loadRetries)
            # `self` as the timer context: the callback is dropped if the
            # owner is destroyed while the retry is pending.
            QTimer.singleShot(
                self.LOAD_RETRY_MS * self._loadRetries, self, self._RetryLoad)
            return
        self._pendingUrl = None
        self.finished.emit(ok)

    def _RetryLoad(self):
        if self._pendingUrl:
            self._view.setUrl(QUrl(self._pendingUrl))


class HiddenPageLifecycle(QObject):
    """Freezes a page while it is off screen and discards it past a delay.

    Active --(hidden)--> Frozen --(discardAfterMs)--> Discarded, and
    straight back to Active when shown. Frozen, Chromium suspends the page's
    JavaScript and timers (it already stops painting a hidden page, infinite
    CSS animations included); discarded, the renderer process itself is
    shut down. Qt enforces the rules this relies on: a visible page cannot
    leave Active, a discarded one can only return to Active, and Qt itself
    returns a page to Active before it reports it visible again, and on
    setUrl() (a navigation of a hidden page) -- so neither revealing nor
    loading needs a thaw here, only the catch-up: ``onActive`` is called,
    out of Qt's notification, whenever the page is Active again and the
    owner is not navigating (``busy()``). Back from Discarded, Qt reloads
    the page; the owner's own load-finished handling covers that document.

    "Hidden" is the page's own visibility, which covers an inactive stack
    page and a minimized main window alike (QWidget.isVisible() stays True
    for the latter, so the view's visibility cannot be used).

    The owner calls ScheduleFreeze() after each load it finishes: a load
    that finished while the page was hidden may find Qt's recommended state
    already settled while ``busy()`` still held the freeze back, so no
    further change would come to trigger it.
    """

    # How long a frozen (hidden) page is kept before it is discarded. A
    # frozen page still holds its renderer (~200 MB for an overlay, most of
    # it the kuromoji dictionary every overlay loads); a discarded one costs
    # a reload of roughly a second when it is shown again. Pages the
    # operator flips between during a set come back well within this, so
    # they reveal instantly; one left alone this long releases its memory.
    DISCARD_AFTER_MS = 5 * 60 * 1000

    def __init__(self, page, *, busy, onActive, discardAfterMs=None,
                 parent=None):
        super().__init__(parent)
        self._page = page
        self._busy = busy
        self._onActive = onActive
        self._discardTimer = QTimer(self)
        self._discardTimer.setSingleShot(True)
        self._discardTimer.setInterval(
            self.DISCARD_AFTER_MS if discardAfterMs is None else discardAfterMs)
        self._discardTimer.timeout.connect(self._DiscardIfIdle)
        page.recommendedStateChanged.connect(self._OnRecommendedStateChanged)
        page.lifecycleStateChanged.connect(self._OnLifecycleStateChanged)

    def _OnRecommendedStateChanged(self, state):
        # Qt recommends leaving Active only for a page that is hidden,
        # finished loading and not under DevTools.
        if state != QWebEnginePage.LifecycleState.Active:
            self.ScheduleFreeze()

    def ScheduleFreeze(self):
        # Deferred: both callers run inside Qt's own notifications, and the
        # transition is re-validated when it actually happens.
        QTimer.singleShot(0, self, self._FreezeIfIdle)

    def _FreezeIfIdle(self):
        page = self._page
        if (page.isVisible()
                or self._busy()
                or page.lifecycleState() != QWebEnginePage.LifecycleState.Active
                or page.recommendedState() == QWebEnginePage.LifecycleState.Active):
            return
        page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)
        if page.lifecycleState() == QWebEnginePage.LifecycleState.Frozen:
            self._discardTimer.start()

    def _DiscardIfIdle(self):
        # Only on Qt's own recommendation: it withholds Discarded from a
        # page whose state a reload would lose (form input, unload hooks).
        page = self._page
        if (page.isVisible()
                or self._busy()
                or page.lifecycleState() != QWebEnginePage.LifecycleState.Frozen
                or page.recommendedState() != QWebEnginePage.LifecycleState.Discarded):
            return
        page.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)

    def _OnLifecycleStateChanged(self, state):
        if state != QWebEnginePage.LifecycleState.Active:
            return
        self._discardTimer.stop()
        # Deferred out of Qt's notification, like freezing.
        QTimer.singleShot(0, self, self._CatchUp)

    def _CatchUp(self):
        if self._busy():
            # Navigating: the new document starts from the full state.
            return
        self._onActive()
