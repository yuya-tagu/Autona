import os
import json

from PyQt6.QtCore import QObject, QUrl, QEventLoop, QTimer, pyqtSignal, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from loguru import logger


class _ThumbnailPage(QWebEnginePage):
    """Custom page to capture JS console messages."""

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        source = sourceID.split("/")[-1] if sourceID else "unknown"
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            logger.error(f"[Thumbnail JS] {source}:{lineNumber} {message}")
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            logger.warning(f"[Thumbnail JS] {source}:{lineNumber} {message}")
        else:
            logger.debug(f"[Thumbnail JS] {source}:{lineNumber} {message}")

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"


class ThumbnailRenderer(QObject):
    """Renders thumbnail HTML templates to images using QWebEngineView.

    Architecture (modeled after scoreboard layout):
    - HTML page for the active template is loaded once
    - Each render injects new data via JS (no page reload) until the
      template id changes — then the new template's HTML is loaded
    - Async by default: result emitted via renderComplete signal
    - Sync wrapper available for final thumbnail generation
    """

    # Maps template id -> subdirectory under layout/ (each contains thumbnail.html)
    TEMPLATE_SUBDIRS = {
        "type_a": "thumbnail",
        "type_b": "thumbnail_type_b",
    }
    DEFAULT_TEMPLATE_ID = "type_a"

    renderComplete = pyqtSignal(QPixmap)

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = _ThumbnailPage()
        self._view = QWebEngineView()
        self._view.setPage(self._page)
        self._view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self._view.resize(1920, 1080)
        self._view.show()

        # Enable local file access
        settings = self._view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        # Transparent background
        self._view.page().setBackgroundColor(Qt.GlobalColor.transparent)

        self._template_id = None       # Currently loaded template id
        self._page_loaded = False
        self._pending_payload = None   # Payload waiting for initial page load
        self._rendering = False
        self._queued_payload = None    # Next payload queued during active render
        self._sync_result = None
        self._sync_loop = None
        self._page_load_loop = None

        self._view.loadFinished.connect(self._on_load_finished)
        self._view.page().titleChanged.connect(self._on_title_changed)

        # Load default template at init
        self._load_template(self.DEFAULT_TEMPLATE_ID)

    def _resolve_template_id(self, template: dict) -> str:
        tid = (template or {}).get("template_id")
        if tid not in self.TEMPLATE_SUBDIRS:
            return self.DEFAULT_TEMPLATE_ID
        return tid

    def _load_template(self, template_id: str):
        """Load (or reload) the HTML for the given template id."""
        subdir = self.TEMPLATE_SUBDIRS.get(
            template_id, self.TEMPLATE_SUBDIRS[self.DEFAULT_TEMPLATE_ID])
        html_path = os.path.abspath(
            os.path.join(os.getcwd(), "layout", subdir, "thumbnail.html"))
        url = QUrl.fromLocalFile(html_path)
        logger.info(
            f"Loading thumbnail template '{template_id}': {url.toString()}")
        self._template_id = template_id
        self._page_loaded = False
        self._rendering = False
        self._view.load(url)

    def render(self, data: dict, config: dict, template: dict):
        """Start async render. Result emitted via renderComplete signal.

        If called while a render is in progress, the new payload replaces
        the queued one — only the latest data is rendered.
        """
        payload = {"data": data, "config": config, "template": template}
        template_id = self._resolve_template_id(template)

        if template_id != self._template_id:
            # Switch template — queue the payload and reload HTML
            self._pending_payload = payload
            self._load_template(template_id)
            return

        if not self._page_loaded:
            self._pending_payload = payload
            return

        if self._rendering:
            # Replace queued payload — only latest matters
            self._queued_payload = payload
            return

        self._start_render(payload)

    def renderSync(self, data: dict, config: dict, template: dict) -> QPixmap:
        """Synchronous render. Blocks until result is ready.

        Only use for user-initiated final generation (no re-entrancy risk).
        Page is already loaded so this blocks for ~50-100ms, not seconds.
        """
        payload = {"data": data, "config": config, "template": template}
        template_id = self._resolve_template_id(template)
        self._sync_result = None

        if template_id != self._template_id:
            self._load_template(template_id)

        if not self._page_loaded:
            loop = QEventLoop()
            self._page_load_loop = loop
            QTimer.singleShot(10000, loop.quit)
            loop.exec()
            self._page_load_loop = None
            if not self._page_loaded:
                logger.error("Thumbnail page failed to load")
                return QPixmap()

        self._start_render(payload)

        loop = QEventLoop()
        self._sync_loop = loop
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        self._sync_loop = None

        return self._sync_result or QPixmap()

    def _start_render(self, payload):
        self._rendering = True
        self._queued_payload = None

        json_str = json.dumps(payload, ensure_ascii=False)
        # Escape backticks and backslashes for JS template literal
        json_str_escaped = json_str.replace("\\", "\\\\").replace("`", "\\`")

        self._view.page().runJavaScript('document.title = "LOADING"')
        self._view.page().runJavaScript(
            f"initThumbnail(JSON.parse(`{json_str_escaped}`))")

    def _on_load_finished(self, ok: bool):
        if not ok:
            logger.error("Failed to load thumbnail HTML template")
            if self._page_load_loop:
                self._page_load_loop.quit()
            return

        logger.info("Thumbnail HTML template loaded (one-time init complete)")
        self._page_loaded = True

        if self._page_load_loop:
            self._page_load_loop.quit()

        if self._pending_payload:
            payload = self._pending_payload
            self._pending_payload = None
            self._start_render(payload)

    def _on_title_changed(self, title: str):
        if title != "RENDER_READY":
            return

        # Delay grab() so the off-screen QWebEngineView compositor
        # finishes painting the new frame.  Without this delay the
        # very first grab() after a content change often captures the
        # previous frame, requiring a second click to see the update.
        # _rendering stays True during the wait so concurrent render()
        # calls queue their payloads instead of starting immediately.
        QTimer.singleShot(100, self._finalize_render)

    def _finalize_render(self):
        self._rendering = False

        pixmap = self._view.grab()
        if pixmap.width() != 1920 or pixmap.height() != 1080:
            pixmap = pixmap.scaled(
                1920, 1080,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

        logger.info("Thumbnail render complete")

        # Store for sync callers
        self._sync_result = pixmap

        # Emit signal for async listeners
        self.renderComplete.emit(pixmap)

        # Unblock sync caller if waiting
        if self._sync_loop:
            self._sync_loop.quit()

        # If a newer payload was queued during this render, start it now
        if self._queued_payload:
            payload = self._queued_payload
            self._start_render(payload)
