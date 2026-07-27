"""Shared live-preview frame for stream overlay layouts.

Hosts a QWebEngineView pointed at the local web server's copy of a
``layout/{name}/index.html`` overlay so a panel can embed a live 16:9
preview (used by the Top 8, Commentary and Board panels). Extracted from
AutonaTop8Widget's former private _BracketPreview.

Import-light on purpose (qtpy / SettingsManager / ui_constants only):
this module must not pull the singleton chain, so it can sit anywhere in
Autona.py's import order.
"""

import logging
import math
import os

from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy.QtGui import QPainter, QPixmap
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWebEngineCore import QWebEngineSettings

from .SettingsManager import SettingsManager
from . import ui_constants as UI

LAYOUT_DIR = "layout"

# Overlay folders normally expose a single index.html. A few (last_sets,
# tournament_history) ship one entry file per player, so the entry name is
# a parameter that defaults to the usual case — every existing caller keeps
# working unchanged.
DEFAULT_ENTRY = "index.html"

logger = logging.getLogger(__name__)


def ObsUrl(layout_name, entry=DEFAULT_ENTRY):
    """Browser-source URL for a layout, for panels that show it before
    (or without) instantiating a preview."""
    port = SettingsManager.Get("general.webserver_port", 5000)
    return (f"http://localhost:{port}/{LAYOUT_DIR}/"
            f"{layout_name}/{entry}")


def DiscoverLayoutDesigns(prefix):
    """List ``layout/{prefix}`` and ``layout/{prefix}_*`` folders that have
    an ``index.html``, sorted (the base folder first).

    Shared by the panels whose preview offers a design pulldown (Commentary
    / Top 8): dropping a ``layout/{prefix}_{variant}/`` folder adds it to
    the pulldown on the next launch, no code change needed. The Board tab
    keeps its own richer discovery (custom/ designs, live re-scan)."""
    designs = []
    try:
        base = os.path.abspath(LAYOUT_DIR)
        for name in sorted(os.listdir(base)):
            if name != prefix and not name.startswith(prefix + "_"):
                continue
            if os.path.isfile(os.path.join(base, name, "index.html")):
                designs.append(name)
    except OSError as e:
        logger.warning("Failed to list %s layout designs: %s", prefix, e)
    return designs


def LayoutDesignDisplayName(prefix, design):
    """Pulldown label for a discovered design: the base folder is 標準,
    variants use their prettified suffix (``{prefix}_dark_gold`` →
    ``Dark Gold``)."""
    if design == prefix:
        return "標準"
    suffix = design[len(prefix):].lstrip("_")
    return suffix.replace("_", " ").replace("-", " ").title() or design


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
    the panel. There is deliberately no scrollable structure anywhere in
    this widget: an earlier design used a QScrollArea around the view, and
    manually scrolling it re-ran zoom/geometry math derived from the
    scrolled viewport, which visibly rescaled the overlay ("resolution
    changes on scroll"). That bug's root cause was the scrollable structure
    itself, not any particular calculation, so this version removes
    scrolling entirely instead of patching around it:

    - The view is a direct, geometry-managed child of this QFrame (no
      QScrollArea, no QAbstractScrollArea ancestor anywhere).
    - ``setMinimumSize`` is derived from ``MIN_FIT``, the smallest scale
      the preview supports. Chromium clamps the zoom factor at ``MIN_ZOOM``
      (0.25); between MIN_FIT and MIN_ZOOM the remainder is bridged by a
      CSS ``transform: scale()`` injected on ``<body>`` (see
      ``_applyExtraScale``). The layout viewport then shrinks below
      1920x1080, but the body is pinned to 1920x1080 and scaled to fit, so
      px-positioned overlays render whole. (Overlays must not rely on
      vw/vh units or ``position: fixed`` — none of the previewed layouts
      do.)
    - As a defense in depth, if the panel is ever forced smaller than that
      minimum anyway (e.g. a parent layout override), ``_layoutView``
      centres the (fixed-zoom) view and lets it clip against the frame's
      bounds rather than reach for a scrollbar. Zoom never changes because
      of this; only how much of the already-rendered canvas is visible
      does.
    - A transparent "glass pane" sits on top of the QWebEngineView and
      absorbs mouse/wheel input before it reaches the page, so the overlay
      page itself can never be scrolled (or clicked) from the preview.
      This preview is display-only, so swallowing input here is fine.

    Chromium clamps the zoom factor to [0.25, 5.0].

    This widget also supports an optional preview-only background image
    (``setBackgroundImage``), painted behind the (transparent-background)
    web view so operators can see how an overlay reads against real
    stream footage. This is purely cosmetic for the desktop preview: it
    never touches the layout HTML/CSS that OBS actually renders, and it
    has no effect on the browser-source output.
    """

    # Native OBS canvas the overlays are authored against.
    NATIVE_WIDTH = 1920
    NATIVE_HEIGHT = 1080

    # Chromium's zoom-factor clamp.
    MIN_ZOOM = 0.25
    MAX_ZOOM = 5.0

    # Smallest supported preview scale (defines the widget minimum size).
    # Below MIN_ZOOM the remainder is a CSS scale — see class docstring.
    MIN_FIT = 0.125

    def __init__(self, layout_name=None, parent=None, entry=DEFAULT_ENTRY):
        # layout_name may be deferred to load() for panels that switch
        # between designs on one preview (Board tab). `entry` is the html
        # file inside the layout folder (see DEFAULT_ENTRY).
        super().__init__(parent)
        self.layout_name = layout_name
        self.entry = entry or DEFAULT_ENTRY
        self._bgSource = None
        self._bgScaled = None

        p = UI.palette()
        self.setStyleSheet(
            f"QFrame {{"
            f"background-color: {p['bg_tertiary']};"
            f"border-radius: {UI.RADIUS_SM}px;"
            f"}}"
        )
        # Keep the panel large enough that the canvas never shrinks below
        # MIN_FIT — see class docstring. +2 accounts for the 1px inset kept
        # on each side in _fitScale/_layoutView.
        self.setMinimumSize(
            int(self.NATIVE_WIDTH * self.MIN_FIT) + 2,
            int(self.NATIVE_HEIGHT * self.MIN_FIT) + 2,
        )
        # Last CSS extra-scale pushed into the page (None = unknown, e.g.
        # right after a load wiped the injected style).
        self._lastExtra = None

        self.view = QWebEngineView(self)
        s = self.view.settings()
        s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.view.loadFinished.connect(lambda _ok: self._applyScale())

        # Transparent input shield above the view: swallows wheel/click
        # events so the page can never be scrolled or interacted with from
        # the preview (see class docstring). Must be created after
        # self.view so it stacks on top of it.
        self._glass = QWidget(self)
        self._glass.setStyleSheet("background: transparent;")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layoutView()

    def paintEvent(self, e):
        # Draw the frame's own background (rounded rect etc.) first, then
        # the optional preview-only background image behind the web view.
        # Painted here (not on a child widget) so it always sits below the
        # QWebEngineView, which is drawn by its own native surface.
        super().paintEvent(e)
        if self._bgScaled is not None and not self._bgScaled.isNull():
            painter = QPainter(self)
            painter.drawPixmap(self.view.geometry(), self._bgScaled)
            painter.end()

    def _fitScale(self):
        # Scale that fits the whole canvas in the panel, clamped to the
        # supported range. Depends ONLY on the panel size, so nothing
        # (including the removed scroll area) can change it out from under
        # the layout math.
        inset = 1
        avail_w = max(1, self.width() - 2 * inset)
        avail_h = max(1, self.height() - 2 * inset)
        scale = min(avail_w / self.NATIVE_WIDTH, avail_h / self.NATIVE_HEIGHT)
        return max(self.MIN_FIT, min(scale, self.MAX_ZOOM))

    def _layoutView(self):
        scale = self._fitScale()
        # Chromium refuses zoom factors below MIN_ZOOM; the remainder is
        # bridged by a CSS scale on <body> (see class docstring).
        zoom = max(scale, self.MIN_ZOOM)
        # Round the view size up (not to nearest) so the CSS layout
        # viewport (view_px / zoom) is never fractionally short of the
        # scaled canvas — a viewport that undershoots lets the page's own
        # content overflow it. Rounding up guarantees viewport >= scaled
        # size, at the cost of at most 1px of clipping against the frame.
        view_w = math.ceil(self.NATIVE_WIDTH * scale)
        view_h = math.ceil(self.NATIVE_HEIGHT * scale)

        # Centre the view in the panel. With setMinimumSize enforcing the
        # zoom floor, this fits within the panel at every reachable size;
        # the (x, y) can still go negative under an externally-forced
        # undersized panel, in which case the view is simply clipped
        # against the frame's bounds rather than scrolled.
        x = (self.width() - view_w) // 2
        y = (self.height() - view_h) // 2
        self.view.setGeometry(x, y, view_w, view_h)
        self._glass.setGeometry(x, y, view_w, view_h)
        self.view.setZoomFactor(zoom)
        self._applyExtraScale(scale / zoom)

        # Rescale the cached background only when the view size actually
        # changed: _layoutView runs on every resizeEvent, and a smooth
        # 1920x1080 rescale per event is too costly during a window drag.
        if self._bgSource is not None and not self._bgSource.isNull():
            if (self._bgScaled is None
                    or self._bgScaled.size() != QSize(view_w, view_h)):
                self._bgScaled = self._bgSource.scaled(
                    view_w, view_h,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.update()

    def _applyExtraScale(self, extra):
        """Push the sub-MIN_ZOOM remainder into the page as a CSS scale on
        <body> (pinned to the native 1920x1080 canvas). ``extra`` is 1.0
        whenever Chromium's zoom factor covers the whole scale; the injected
        style is then removed so normal-size rendering is untouched."""
        # Round so resize-drag noise doesn't spam runJavaScript.
        extra = min(round(extra, 4), 1.0)
        if extra == self._lastExtra:
            return
        self._lastExtra = extra
        js = (
            "(function(s){"
            "var st=document.getElementById('__autona_preview_scale');"
            "if(s>=1){if(st)st.remove();return;}"
            "if(!st){st=document.createElement('style');"
            "st.id='__autona_preview_scale';"
            "document.head.appendChild(st);}"
            "st.textContent='body{transform:scale('+s+');"
            "transform-origin:0 0;"
            f"width:{self.NATIVE_WIDTH}px;height:{self.NATIVE_HEIGHT}px;"
            "overflow:hidden;}';"
            "})(%s)" % extra)
        self.view.page().runJavaScript(js)

    def _applyScale(self):
        # Re-apply after a load: Chromium resets the zoom factor on
        # loadFinished, and a (re)load discards the injected scale style.
        self._lastExtra = None
        self._layoutView()

    # ── Copy / save actions ─────────────────────────────────────────
    def _snapshot(self):
        """Snapshot of the canvas area as currently displayed (background
        image + overlay composited, letterbox bars excluded)."""
        return self.grab(self.view.geometry())

    def CopyToClipboard(self):
        QApplication.clipboard().setPixmap(self._snapshot())

    def SaveImage(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "プレビューを画像として保存", "preview.png",
            "PNG (*.png)")
        if path:
            self._snapshot().save(path)

    def BuildActionButtons(self):
        """Two stacked full-width buttons for the panel to place under the
        preview. The labels are the unified wording shared by every
        preview panel (RESULT-tab structure as the reference)."""
        col = QVBoxLayout()
        col.setSpacing(UI.SPACE_SM)
        btCopy = QPushButton("プレビューをクリップボードにコピー")
        btCopy.clicked.connect(self.CopyToClipboard)
        col.addWidget(btCopy)
        btSave = QPushButton("プレビューを画像として保存")
        btSave.clicked.connect(self.SaveImage)
        col.addWidget(btSave)
        return col

    def load(self, layout_name=None, entry=None):
        if layout_name is not None:
            self.layout_name = layout_name
        if entry is not None:
            self.entry = entry
        port = SettingsManager.Get("general.webserver_port", 5000)
        url = (f"http://127.0.0.1:{port}/{LAYOUT_DIR}/"
               f"{self.layout_name}/{self.entry}")
        self.view.setUrl(QUrl(url))

    def obs_url(self):
        return ObsUrl(self.layout_name, self.entry)

    def setBackgroundImage(self, path):
        """Set a preview-only background image shown behind the (already
        transparent-background) web view. Purely cosmetic for the desktop
        preview; never affects the layout HTML/CSS or the OBS browser
        source output."""
        pixmap = QPixmap(path)
        if pixmap.isNull():
            logger.warning("Failed to load preview background image: %s", path)
            self._bgSource = None
            self._bgScaled = None
            return
        self._bgSource = pixmap
        self._layoutView()
