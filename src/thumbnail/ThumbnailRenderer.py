from qtpy.QtCore import QEventLoop, Qt
from qtpy.QtGui import QPixmap
from qtpy.QtWebEngineCore import QWebEngineSettings

from ..AutonaOffscreenRenderer import AutonaOffscreenRenderer


class ThumbnailRenderer(AutonaOffscreenRenderer):
    """Renders thumbnail HTML templates to images: how, and in what order, is
    AutonaOffscreenRenderer's (split from here by ADR-005 S4q, for Result).

    Two ways in, both on the GUI thread:
    - renderAsync(): core/ThumbnailService's. The result goes to its callback.
    - renderSync(): SCORE's and the legacy route's. Waits in a nested event
      loop.
    The Qt panel's render() (only the newest of its renders drawn, the result
    a renderComplete signal) went with the panel (ADR-005 S4p).
    """

    LOG_NAME = "Thumbnail"
    # Maps template id -> subdirectory under layout/ (each contains thumbnail.html).
    # Both templates currently share the Center Panel design as a starting
    # point; they are crafted into distinct designs over time.
    TEMPLATE_SUBDIRS = {
        "type_a": "thumbnail",
        "type_b": "thumbnail_type_b",
    }
    DEFAULT_TEMPLATE_ID = "type_a"
    PAGE_FILE = "thumbnail.html"
    INIT_FUNCTION = "initThumbnail"

    def _configure_view(self):
        # Enable local file access
        settings = self._view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        # Transparent background
        self._view.page().setBackgroundColor(Qt.GlobalColor.transparent)

    def _resolve_template_id(self, template: dict) -> str:
        tid = (template or {}).get("template_id")
        if tid not in self.TEMPLATE_SUBDIRS:
            return self.DEFAULT_TEMPLATE_ID
        return tid

    @staticmethod
    def _payload(data, config, template) -> dict:
        return {"data": data, "config": config, "template": template}

    def renderAsync(self, data: dict, config: dict, template: dict, done):
        """Start a render whose result only `done(pixmap, error)` gets, later
        on this thread; `pixmap` is None when it failed (`error` says why).
        Never waits, and is never drawn over: one in progress ends first."""
        self._submit(self._payload(data, config, template),
                     self._resolve_template_id(template), done)

    def renderSync(self, data: dict, config: dict, template: dict) -> QPixmap:
        """Synchronous render. Blocks until result is ready.

        Only use for user-initiated final generation (no re-entrancy risk).
        Page is already loaded so this blocks for well under a second, plus
        what a render in progress has left: it ends first.
        """
        outcome = []
        loop = QEventLoop()

        def done(pixmap, _error):
            outcome.append(pixmap)
            loop.quit()

        self._submit(self._payload(data, config, template),
                     self._resolve_template_id(template), done)
        if not outcome:
            loop.exec()
        return outcome[0] if outcome and outcome[0] is not None else QPixmap()
