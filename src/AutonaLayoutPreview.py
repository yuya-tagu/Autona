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
    """Keeps the preview QWebEngineView at a 16:9 letterbox, zooming the
    1920x1080 overlay down to the panel width.

    Zooming must use setZoomFactor (not a CSS transform): the zoom factor
    keeps the page's LAYOUT viewport at 1920x1080 logical pixels, which
    viewport-relative overlays depend on — a CSS transform shrinks the
    layout viewport itself and breaks them. Chromium resets the zoom
    factor when a page finishes loading (leaving a top-left crop), so it
    is re-applied on loadFinished.

    Chromium clamps the zoom factor to [0.25, 5.0], so the view itself is
    never allowed to shrink below 480px (= 1920 * 0.25); when the pane is
    narrower, the view keeps that minimum size inside a scroll area and
    the preview becomes scrollable instead of cropping."""

    # 1920 * Chromium's minimum zoom factor (0.25).
    MIN_VIEW_WIDTH = 480

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

    def _layoutView(self):
        inset = 1
        w = max(1, self.width() - 2 * inset)
        view_w = max(w, self.MIN_VIEW_WIDTH)
        view_h = int(view_w * 9 / 16)
        self.view.setFixedSize(view_w, view_h)

        extra = (self.scroll.horizontalScrollBar().sizeHint().height()
                 if view_w > w else 0)
        h = min(max(1, self.height() - 2 * inset), view_h + extra)
        y = max(inset, (self.height() - h) // 2)
        self.scroll.setGeometry(inset, y, w, h)
        self._applyScale()

    def _applyScale(self):
        self.view.setZoomFactor(max(1, self.view.width()) / 1920.0)

    def load(self, layout_name=None):
        if layout_name is not None:
            self.layout_name = layout_name
        port = SettingsManager.Get("general.webserver_port", 5000)
        url = (f"http://127.0.0.1:{port}/{LAYOUT_DIR}/"
               f"{self.layout_name}/index.html")
        self.view.setUrl(QUrl(url))

    def obs_url(self):
        return ObsUrl(self.layout_name)
