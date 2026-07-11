"""Shared live-preview frame for stream overlay layouts.

Hosts a QWebEngineView pointed at the local web server's copy of a
``layout/{name}/index.html`` overlay so a panel can embed a live 16:9
preview (used by the Top 8, Commentary and Board panels). Extracted from
AutonaTop8Widget's former private _BracketPreview.

Import-light on purpose (qtpy / SettingsManager / ui_constants only):
this module must not pull the singleton chain, so it can sit anywhere in
Autona.py's import order.
"""

from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWebEngineCore import QWebEngineSettings

from .SettingsManager import SettingsManager
from . import ui_constants as UI

LAYOUT_DIR = "layout"


def ObsUrl(layout_name):
    """Browser-source URL for a layout, for panels that show it before
    (or without) instantiating a preview."""
    port = SettingsManager.Get("general.webserver_port", 5000)
    return (f"http://localhost:{port}/{LAYOUT_DIR}/"
            f"{layout_name}/index.html")


class AutonaLayoutPreview(QFrame):
    """Fits the whole 1920x1080 overlay inside the panel, scaling the zoom
    to the panel size while the LAYOUT viewport stays fixed at 1920x1080.

    Zooming must use setZoomFactor (not a CSS transform): the zoom factor
    keeps the page's LAYOUT viewport at 1920x1080 logical pixels, which
    viewport-relative overlays depend on — a CSS transform shrinks the
    layout viewport itself and breaks them. Chromium resets the zoom
    factor when a page finishes loading (leaving a top-left crop), so it
    is re-applied on loadFinished.

    The zoom fits BOTH dimensions (min of the width/height ratios), so the
    entire 16:9 canvas is always visible and the inner view never overflows
    the panel — there is nothing to scroll. This is deliberate: the earlier
    fit-to-width sizing let a tall panel clip the canvas into a scroll area,
    and manually scrolling it re-ran the width-based zoom and visibly
    rescaled the overlay ("resolution changes on scroll"). Deriving the zoom
    from the panel size alone (never from a scrollbar-affected view width)
    keeps the logical resolution pinned at 1920x1080 at every zoom level.

    Chromium clamps the zoom factor to [0.25, 5.0]. Only when the panel is
    smaller than the clamped-minimum canvas (1920*0.25 = 480 wide) does the
    scroll area show bars — and even then the zoom is fixed, so scrolling
    still never rescales the overlay."""

    # Native OBS canvas the overlays are authored against.
    NATIVE_WIDTH = 1920
    NATIVE_HEIGHT = 1080

    # Chromium's zoom-factor clamp.
    MIN_ZOOM = 0.25
    MAX_ZOOM = 5.0

    def __init__(self, layout_name=None, parent=None):
        # layout_name may be deferred to load() for panels that switch
        # between designs on one preview (Board tab).
        super().__init__(parent)
        self.layout_name = layout_name

        p = UI.palette()
        self.setStyleSheet(
            f"QFrame {{"
            f"background-color: {p['bg_tertiary']};"
            f"border-radius: {UI.RADIUS_SM}px;"
            f"}}"
        )
        self.setMinimumHeight(220)

        self.view = QWebEngineView()
        s = self.view.settings()
        s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.view.loadFinished.connect(lambda _ok: self._applyScale())

        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.view)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layoutView()

    def _fitZoom(self):
        # Zoom that fits the whole canvas in the panel, clamped to Chromium's
        # range. Depends ONLY on the panel size, never on the view/scrollbar
        # width, so scrolling can never change it.
        inset = 1
        avail_w = max(1, self.width() - 2 * inset)
        avail_h = max(1, self.height() - 2 * inset)
        zoom = min(avail_w / self.NATIVE_WIDTH, avail_h / self.NATIVE_HEIGHT)
        return max(self.MIN_ZOOM, min(zoom, self.MAX_ZOOM))

    def _layoutView(self):
        inset = 1
        zoom = self._fitZoom()
        view_w = round(self.NATIVE_WIDTH * zoom)
        view_h = round(self.NATIVE_HEIGHT * zoom)
        self.view.setFixedSize(view_w, view_h)

        # Centre the scaled canvas in the panel. It fits within the panel at
        # every zoom except the clamped minimum, where the scroll area shows
        # bars without affecting the (fixed) zoom.
        sw = min(max(1, self.width() - 2 * inset), view_w)
        sh = min(max(1, self.height() - 2 * inset), view_h)
        x = max(inset, (self.width() - sw) // 2)
        y = max(inset, (self.height() - sh) // 2)
        self.scroll.setGeometry(x, y, sw, sh)
        self.view.setZoomFactor(zoom)

    def _applyScale(self):
        # Re-apply after a load (Chromium resets zoom on loadFinished).
        self.view.setZoomFactor(self._fitZoom())

    def load(self, layout_name=None):
        if layout_name is not None:
            self.layout_name = layout_name
        port = SettingsManager.Get("general.webserver_port", 5000)
        url = (f"http://127.0.0.1:{port}/{LAYOUT_DIR}/"
               f"{self.layout_name}/index.html")
        self.view.setUrl(QUrl(url))

    def obs_url(self):
        return ObsUrl(self.layout_name)
