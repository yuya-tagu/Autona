"""Off-screen QWebEngineView renderer for result images (Top 8).

Supports multiple layouts (e.g. 1920x1080 horizontal, 960x1080 vertical).
"""

from qtpy.QtCore import QObject, Signal, QTimer, QUrl, QEventLoop, Qt
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import QApplication, QWidget
from qtpy.QtWebEngineWidgets import QWebEngineView
from qtpy.QtWebEngineCore import QWebEnginePage
from loguru import logger
import json
import os


# Layout definitions: name -> (html_subdir, width, height)
LAYOUTS = {
    "horizontal": ("result", 1920, 1080),
    "horizontal_16": ("result_top16", 1920, 1080),
    "vertical": ("result_vertical", 960, 1080),
    "vertical_geki": ("result_vertical_geki", 960, 1080),
    "team": ("result_team", 1920, 1080),
    "team_split": ("result_team_split", 1920, 1080),
}

DEFAULT_LAYOUT = "horizontal"


class _ResultPage(QWebEnginePage):
    """Captures JS console messages for debugging."""

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            logger.error(f"[ResultRenderer JS] {message} (line {line})")
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            logger.warning(f"[ResultRenderer JS] {message} (line {line})")
        else:
            logger.debug(f"[ResultRenderer JS] {message} (line {line})")


class ResultRenderer(QObject):
    """Singleton off-screen renderer for result images.

    Usage (async — for preview):
        renderer = ResultRenderer.instance()
        renderer.renderComplete.connect(on_pixmap)
        renderer.render(data=..., config=..., layout="horizontal")

    Usage (sync — for final generation):
        pixmap = renderer.renderSync(data=..., config=..., layout="vertical")
    """

    renderComplete = Signal(object)  # QPixmap

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()

        self._page = _ResultPage()
        # Parent the view to a hidden host widget. Without a parent, the
        # view is a top-level window; even with WA_DontShowOnScreen the
        # OpenGL init can flash a native window on screen at startup.
        self._host = QWidget()
        self._host.setAttribute(
            Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self._host.resize(1920, 1080)
        self._view = QWebEngineView(self._host)
        self._view.setPage(self._page)
        self._view.resize(1920, 1080)
        self._host.show()

        self._current_layout = None
        self._page_loaded = False
        self._rendering = False
        self._pending_payload = None
        self._queued_payload = None
        self._sync_result = None
        self._sync_loop = None
        self._target_w = 1920
        self._target_h = 1080

        self._page.loadFinished.connect(self._on_load_finished)
        self._page.titleChanged.connect(self._on_title_changed)

        self._load_layout(DEFAULT_LAYOUT)

    def _load_layout(self, layout_name):
        layout = LAYOUTS.get(layout_name, LAYOUTS[DEFAULT_LAYOUT])
        subdir, w, h = layout

        self._current_layout = layout_name
        self._target_w = w
        self._target_h = h
        self._view.resize(w, h)

        html_path = os.path.abspath(
            os.path.join(os.getcwd(), "layout", subdir, "result.html"))
        url = QUrl.fromLocalFile(html_path)
        logger.info(f"ResultRenderer loading layout '{layout_name}': {url.toString()}")
        self._page_loaded = False
        self._page.load(url)

    def _on_load_finished(self, ok):
        if not ok:
            logger.error("ResultRenderer: page load failed")
            return
        self._page_loaded = True
        logger.info("ResultRenderer: page loaded")
        if self._pending_payload:
            payload = self._pending_payload
            self._pending_payload = None
            self._start_render(payload)

    def render(self, data, config, layout=None):
        """Start async render. Emits renderComplete(QPixmap) when done."""
        layout = layout or DEFAULT_LAYOUT
        # layout is forwarded so initResult() can switch between the singles
        # standings renderer and the team (per-member fighters) renderer.
        payload = {"data": data, "config": config, "layout": layout}

        if layout != self._current_layout:
            self._pending_payload = payload
            self._load_layout(layout)
            return

        if not self._page_loaded:
            self._pending_payload = payload
            return

        if self._rendering:
            self._queued_payload = payload
            return

        self._start_render(payload)

    def renderSync(self, data, config, layout=None):
        """Blocking render. Returns QPixmap."""
        layout = layout or DEFAULT_LAYOUT
        payload = {"data": data, "config": config, "layout": layout}

        if layout != self._current_layout:
            self._load_layout(layout)

        if not self._page_loaded:
            loop = QEventLoop()
            self._page.loadFinished.connect(loop.quit)
            QTimer.singleShot(10000, loop.quit)
            loop.exec()

        self._sync_result = None
        self._sync_loop = QEventLoop()

        self._start_render(payload)

        QTimer.singleShot(10000, self._sync_loop.quit)
        self._sync_loop.exec()
        self._sync_loop = None
        return self._sync_result or QPixmap()

    def _start_render(self, payload):
        self._rendering = True
        json_str = json.dumps(payload, ensure_ascii=False)
        json_str = json_str.replace("\\", "\\\\").replace("`", "\\`")
        self._page.runJavaScript("document.title = 'LOADING';")
        self._page.runJavaScript(
            f"initResult(JSON.parse(`{json_str}`))")

    def _on_title_changed(self, title):
        if title != "RENDER_READY":
            return
        QTimer.singleShot(100, self._finalize_render)

    def _finalize_render(self):
        self._rendering = False
        pixmap = self._view.grab()
        w, h = self._target_w, self._target_h
        if pixmap.width() != w or pixmap.height() != h:
            pixmap = pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

        if self._sync_loop and self._sync_loop.isRunning():
            self._sync_result = pixmap
            self._sync_loop.quit()
        else:
            self.renderComplete.emit(pixmap)

        if self._queued_payload:
            payload = self._queued_payload
            self._queued_payload = None
            self._start_render(payload)
