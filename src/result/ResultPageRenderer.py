"""The tournament result's off-screen renderer (ADR-005 S4q).

core/ResultService's way to a picture: how, and in what order, is
AutonaOffscreenRenderer's. What is the result's own is that a layout is one of
the layout catalog's result packages (ADR-007 T1), whose size is its picture's
(960 wide for the shipped vertical ones) and whose page draws it
(layout/<folder>/<entry>, result.html for the shipped ones, whose initResult
gets {data, config, layout}).
"""
from qtpy.QtCore import Qt

from ..AutonaOffscreenRenderer import AutonaOffscreenRenderer
from ..core import LayoutCatalog
from ..core.ResultService import DEFAULT_LAYOUT


class ResultPageRenderer(AutonaOffscreenRenderer):
    LOG_NAME = "Result"
    CATALOG_KIND = LayoutCatalog.KIND_RESULT
    DEFAULT_TEMPLATE_ID = DEFAULT_LAYOUT
    INIT_FUNCTION = "initResult"
    # It only matters for a picture of another shape, which the view having
    # been given the size does not make; the thumbnail's IgnoreAspectRatio is
    # that renderer's, as it always was.
    ASPECT_MODE = Qt.AspectRatioMode.KeepAspectRatio
    # The first render says which layout it wants: loading the default first
    # would load a page only to replace it.
    LOAD_AT_START = False

    def _resolve_template_id(self, layout) -> str:
        return self._template_for(layout)

    def renderAsync(self, data: dict, config: dict, layout: str, done):
        """Start a render whose result only `done(pixmap, error)` gets, later
        on this thread; `pixmap` is None when it failed (`error` says why).
        Never waits, and is never drawn over: one in progress ends first."""
        # `layout` as asked: initResult switches between the singles' and the
        # teams' drawing by it.
        self._submit({"data": data, "config": config, "layout": layout},
                     self._resolve_template_id(layout), done)
