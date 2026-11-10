"""What only the desktop's windows answer, without a board in between
(ADR-005 S4y).

The web layer's three shell operations -- the set selection window, the
legacy thumbnail and the off-screen image renderer (AutonaWebOperations.
openSetSelection / renderThumbnail / startImageRender, ADR-005 S3e-9 / S4o)
-- were the scoreboard dock's (AutonaScoreboardManager), and the first two
went through a board's widget: its signal opened its own set window, its
GenerateThumbnail drew. They are here now, so the dock and its boards can go
(ADR-005 S4, order 7) and leave them working. bootstrap_gui() builds the one
instance and registers them; the headless shell has none (503).

Also here:

- **the set selection and station windows** of boards 1 and 2, one each
  (they were each board's): the board's "Load set" button, the load_set
  hotkey (AutonaHotkeys, the active board's) and the legacy open-set route
  open them. The windows load what they pick through core
  (SetFollowService.LoadSet / FollowStation), not through a board;
- **a board's thumbnail drawn now** (DrawThumbnail): core's
  ThumbnailService.DrawBoard with the renderer's synchronous draw, for the
  legacy route and SCORE's X post.

THREADING: the windows and the renderers are the GUI thread's. The web
layer's operations are wrapped in gui_thread_sync (the request waits for the
answer); the rest is called on the GUI thread.

Import side effects: none (agent-pitfalls #1).
"""
import os

from qtpy.QtCore import QBuffer, QIODevice
from loguru import logger

from .AutonaSelectSetWindow import AutonaSelectSetWindow
from .AutonaSelectStationWindow import AutonaSelectStationWindow
from .Helpers.AutonaQtHelper import gui_thread_sync
from .core.Actions import AutonaActions
from .core.ThumbnailService import ThumbnailService


# The preview's PNG (ADR-005 S4o judgment 23). Qt maps a PNG quality to the
# zlib level as (100 - quality) * 9 / 91, so 85 is level 1: lossless like
# every PNG, about five times faster than the default level (6) on a busy
# 1920x1080 picture, for a few percent more bytes. 90 and up is level 0,
# faster again but two and a half times the bytes.
PREVIEW_PNG_QUALITY = 85


def _EncodeImage(image, file_format, quality=-1):
    """A QImage's bytes as "PNG" or "JPG", the encoders QPixmap.save() uses
    (`quality` as QImage.save() takes it; -1 is its default); b"" when it
    failed. Not bound to the GUI thread, unlike a QPixmap."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, file_format, quality):
        return b""
    return bytes(buffer.data())


def _ImageEncoder(image):
    """A render's `encode(format, fast=False)`: what QPixmap.save() writes,
    or with `fast` the preview's PNG (PREVIEW_PNG_QUALITY). `fast` changes
    only a PNG: a JPG is the same either way."""
    def encode(file_format, fast=False):
        quality = PREVIEW_PNG_QUALITY if fast and file_format == "PNG" else -1
        return _EncodeImage(image, file_format, quality)
    return encode


def DrawThumbnailNow(payload):
    """Draw a match thumbnail's (data, config, template) at once: its
    `encode(format, fast=False)` (_ImageEncoder), as ThumbnailService.
    DrawBoard takes it. On the GUI thread, waiting in a nested event loop
    (ThumbnailRenderer.renderSync): only for what an operator asked for and
    waits on. Raises when the renderer gave no image."""
    # Built by the first render, not at startup (see StartImageRender).
    from .thumbnail.ThumbnailRenderer import ThumbnailRenderer
    data, config, template = payload
    pixmap = ThumbnailRenderer.instance().renderSync(
        data=data, config=config, template=template)
    if pixmap.isNull():
        logger.error("Thumbnail rendering produced an empty image")
        raise Exception("Thumbnail rendering failed")
    return _ImageEncoder(pixmap.toImage())


def _SetBoard(board):
    """Board `board`'s number when it is one that loads sets (1/2); for the
    crew board -- which has no set window nor thumbnail -- ValueError, the
    legacy routes' 500 (it always was). AutonaWebOperations refuses a board
    no shell has before this is asked."""
    number = AutonaActions._asInt(board, 0)
    if number not in AutonaActions.SCORE_BOARDS:
        raise ValueError(f"scoreboard {board} has no set selection nor "
                         "thumbnail")
    return number


class AutonaShellOperations:
    """See the module docstring. One per process (bootstrap_gui())."""

    instance: "AutonaShellOperations" = None

    def __init__(self):
        # Builds no window: the parent is the main window, which does not
        # exist yet (UiMounted).
        self._parent = None
        # {board: window}: boards 1 and 2's.
        self._setWindows = {}
        self._stationWindows = {}

    def UiMounted(self, window):
        """The main window is there (Window.__init__, before the boards are
        built): the windows' parent. The set windows are built now, as the
        board shown at startup built its own, so that both follow the
        tournament's phases from the start (a window built later asks for
        them again when it opens). The station windows are built when first
        opened."""
        self._parent = window
        for board in AutonaActions.SCORE_BOARDS:
            self.SetWindow(board)

    # ── the windows ──────────────────────────────────────────────────────

    def SetWindow(self, board) -> AutonaSelectSetWindow:
        """Board `board`'s (1/2) set selection window, built on first use."""
        number = _SetBoard(board)
        window = self._setWindows.get(number)
        if window is None:
            window = AutonaSelectSetWindow(
                self._parent, number,
                lambda number=number: self.ShowStationSelection(number))
            self._setWindows[number] = window
        return window

    def StationWindow(self, board) -> AutonaSelectStationWindow:
        """Board `board`'s (1/2) station window, built on first use."""
        number = _SetBoard(board)
        window = self._stationWindows.get(number)
        if window is None:
            window = AutonaSelectStationWindow(self._parent, number)
            self._stationWindows[number] = window
        return window

    def ShowSetSelection(self, board):
        """Open board `board`'s (1/2) set selection window. It restores the
        cached set list; fetching is the operator's call from inside it."""
        window = self.SetWindow(board)
        window.PrepareForDisplay()
        window.show()

    def ShowStationSelection(self, board):
        """Open board `board`'s (1/2) station window (the set window's "Track
        sets from a stream or station"), with the stations asked for again."""
        window = self.StationWindow(board)
        window.LoadStations()
        window.show()

    # ── a board's thumbnail ──────────────────────────────────────────────

    def DrawThumbnail(self, board):
        """Board `board`'s match thumbnail, drawn now and saved
        (ThumbnailService.DrawBoard): its PNG's path, the .jpg beside it.
        On the GUI thread (DrawThumbnailNow). Raises what DrawBoard raises:
        ThumbnailDataMissing with no match to draw, the renderer's failure.
        Neither builds nor needs board `board`'s widget."""
        return ThumbnailService.instance.DrawBoard(board, DrawThumbnailNow)

    # ── The web layer's shell operations (ADR-005 S3e-9) ─────────────────
    # bootstrap_gui() registers them as AutonaWebOperations.openSetSelection
    # / renderThumbnail / startImageRender. On the GUI thread, where the
    # windows and the renderers live; the request waits for the answer.
    # They were the scoreboard dock's until ADR-005 S4y, which built the
    # board first and asked its widget. The thumbnail builds no board now;
    # the set selection still makes its board ready, through the board host.

    @gui_thread_sync
    def OpenSetSelection(self, number):
        """Open board `number`'s set selection window: "OK". The crew board
        has none and raises (the legacy route's 500, as before).

        The board is made ready first, through the board host (an unbuilt
        board is built, as this route always did): what the window loads
        goes to a board that is held for its 反映 button, and is not wiped by
        the board's build later. The windows of the board's button and of
        the hotkey are a built board's already.

        Opening the window itself never fails the request: until S3e-9 it
        was queued from the request's thread, so what it raised was only
        reported and the answer was "OK". It still is: logged here."""
        board = _SetBoard(number)
        # Imported here: the web layer is not this module's to pull in when
        # it is imported (the board widget imports this module).
        from . import AutonaWebOperations
        AutonaWebOperations.boardHost.Prepare(board)
        try:
            self.ShowSetSelection(board)
        except Exception:
            logger.opt(exception=True).error(
                f"Shell operations - The set selection of scoreboard "
                f"{number} did not open")
        return "OK"

    @gui_thread_sync
    def RenderThumbnail(self, number, file_format):
        """Render board `number`'s thumbnail (quietly: no message box): the
        image's absolute path -- its .jpg sibling for "jpg" -- or None when
        none was made. The crew board has none and raises (500, as before);
        so does a render that failed, logged first."""
        board = _SetBoard(number)
        try:
            thumbnailPath = self.DrawThumbnail(board)
        except Exception:
            logger.opt(exception=True).error(
                f"Shell operations - The thumbnail of scoreboard {number} "
                "failed")
            raise
        if thumbnailPath:
            if file_format == "jpg":
                thumbnailPath = thumbnailPath.replace(".png", ".jpg")
            return os.path.abspath(thumbnailPath)
        return None

    @gui_thread_sync
    def StartImageRender(self, kind, payload, done):
        """Start drawing an image off-screen, on the GUI thread, and return:
        the wait for the GUI thread ends here, not at the image. `kind` says
        which renderer ("thumbnail" or "result": payload is its (data, config,
        template) or (data, config, layout), each with a view of its own);
        `done(encode, error)` comes on the GUI thread, once, with
        `encode(format, fast=False)` -> the image's bytes (callable from any
        thread: a QImage, not the QPixmap; `fast`: _ImageEncoder), or None
        when it failed (`error`)."""
        if kind not in ("thumbnail", "result"):
            raise ValueError(f"unknown kind of image: {kind}")
        # Built by the first render, not at startup: the off-screen view and
        # its page load cost seconds most launches do not need.
        if kind == "thumbnail":
            from .thumbnail.ThumbnailRenderer import ThumbnailRenderer as Renderer
        else:
            from .result.ResultPageRenderer import ResultPageRenderer as Renderer

        def finished(pixmap, error):
            if pixmap is None:
                done(None, error)
                return
            done(_ImageEncoder(pixmap.toImage()), "")

        data, config, template = payload
        Renderer.instance().renderAsync(data, config, template, finished)
