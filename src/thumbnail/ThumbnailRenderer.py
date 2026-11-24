from qtpy.QtCore import QEventLoop, Qt
from qtpy.QtGui import QPixmap

from ..AutonaOffscreenRenderer import AutonaOffscreenRenderer
from ..core import LayoutCatalog
from ..core.ThumbnailService import DEFAULT_TEMPLATE_ID


class ThumbnailRenderer(AutonaOffscreenRenderer):
    """Renders thumbnail HTML templates to images: how, and in what order, is
    AutonaOffscreenRenderer's (split from here by ADR-005 S4q, for Result).
    A template is one of the layout catalog's thumbnail packages (ADR-007 T1):
    layout/<folder>/<entry> (thumbnail.html for the shipped ones) at its size.

    Two ways in, both on the GUI thread:
    - renderAsync(): core/ThumbnailService's. The result goes to its callback.
    - renderSync(): SCORE's and the legacy route's. Waits in a nested event
      loop.
    The Qt panel's render() (only the newest of its renders drawn, the result
    a renderComplete signal) went with the panel (ADR-005 S4p).
    """

    LOG_NAME = "Thumbnail"
    CATALOG_KIND = LayoutCatalog.KIND_THUMBNAIL
    DEFAULT_TEMPLATE_ID = DEFAULT_TEMPLATE_ID
    INIT_FUNCTION = "initThumbnail"

    def _configure_view(self):
        # What a template may do: the base's (ADR-007 D4). Until T2 this
        # view let a template read local files and remote URLs (file://).
        super()._configure_view()
        # Transparent background
        self._view.page().setBackgroundColor(Qt.GlobalColor.transparent)

    def _resolve_template_id(self, template: dict) -> str:
        return self._template_for((template or {}).get("template_id"))

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

        An empty picture when it failed; why is then ``syncError`` (the
        render's error: PREVIEW_UNAVAILABLE when there is no preview
        listener), "" after one that drew.
        """
        outcome = []
        loop = QEventLoop()

        def done(pixmap, error):
            outcome.append(pixmap)
            self.syncError = error or ("" if pixmap is not None
                                       else "the render failed")
            loop.quit()

        self._submit(self._payload(data, config, template),
                     self._resolve_template_id(template), done)
        if not outcome:
            loop.exec()
        return outcome[0] if outcome and outcome[0] is not None else QPixmap()
