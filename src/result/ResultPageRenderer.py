"""The tournament result's off-screen renderer (ADR-005 S4q).

core/ResultService's way to a picture: how, and in what order, is
AutonaOffscreenRenderer's. What is the result's own is that a layout says the
size of its picture (960 wide for the vertical ones), and which page draws it
(layout/<subdir>/result.html, whose initResult gets {data, config, layout}).

result/ResultRenderer stays beside it until the Qt panel, its only user, goes
(ADR-005 S4r).
"""
from qtpy.QtCore import Qt

from ..AutonaOffscreenRenderer import AutonaOffscreenRenderer
from ..core.ResultService import DEFAULT_LAYOUT, LAYOUT_SIZES, LAYOUT_SUBDIRS


class ResultPageRenderer(AutonaOffscreenRenderer):
    LOG_NAME = "Result"
    TEMPLATE_SUBDIRS = LAYOUT_SUBDIRS
    DEFAULT_TEMPLATE_ID = DEFAULT_LAYOUT
    PAGE_FILE = "result.html"
    INIT_FUNCTION = "initResult"
    # As result/ResultRenderer has it. It only matters for a picture of
    # another shape, which the view having been given the size does not make;
    # the thumbnail's IgnoreAspectRatio is that renderer's, as it always was.
    ASPECT_MODE = Qt.AspectRatioMode.KeepAspectRatio
    # The first render says which layout it wants: loading the default first
    # would load a page only to replace it.
    LOAD_AT_START = False

    def _size(self, template_id):
        return LAYOUT_SIZES.get(template_id, LAYOUT_SIZES[DEFAULT_LAYOUT])

    def _resolve_template_id(self, layout) -> str:
        return layout if layout in self.TEMPLATE_SUBDIRS \
            else self.DEFAULT_TEMPLATE_ID

    def renderAsync(self, data: dict, config: dict, layout: str, done):
        """Start a render whose result only `done(pixmap, error)` gets, later
        on this thread; `pixmap` is None when it failed (`error` says why).
        Never waits, and is never drawn over: one in progress ends first."""
        # `layout` as asked: initResult switches between the singles' and the
        # teams' drawing by it.
        self._submit({"data": data, "config": config, "layout": layout},
                     self._resolve_template_id(layout), done)
