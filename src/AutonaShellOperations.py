"""What only the desktop's windows answer, without a board in between
(ADR-005 S4y).

The web layer's three shell operations -- the set selection, the legacy
thumbnail and the off-screen image renderer (AutonaWebOperations.
openSetSelection / renderThumbnail / startImageRender, ADR-005 S3e-9 / S4o)
-- were the scoreboard dock's (AutonaScoreboardManager), and the first two
went through a board's widget: its signal opened its own set window, its
GenerateThumbnail drew. They moved here in ADR-005 S4y, so the dock and its
boards could go (S4ac-3) and leave them working. bootstrap_gui() builds the
one instance and registers them; the headless shell has none (503).

Also here:

- **a board's set selection asked for from outside the SCORE page**: the
  load_set hotkey (AutonaHotkeys, the active board's) and the legacy
  open-set route open the SCORE page's set selection dialog (ADR-005
  S4ab-2): the main window is brought to the front on SCORE and its shared
  view asked to (Window.OpenScoreSets). The page shows the board of the
  battle format only, so a board other than that one opens nothing. The Qt
  set selection and station windows of boards 1 and 2 were here (and each
  board's before S4y) until ADR-005 S4ac-3 removed them with the Qt SCORE
  and its way back;
- **a board's thumbnail drawn now** (DrawThumbnail): core's
  ThumbnailService.DrawBoard with the renderer's synchronous draw, for the
  legacy route and SCORE's X post;
- **the shared view's shell requests** (ShellRequest / ShellState; ADR-005
  S-final SF-5a-1): what a page asks of the desktop in a click -- the main
  window's "always on top" (its Help & Settings menu's check follows), the
  update check, one of the fixed outside addresses (AutonaExternalLinks.
  EXTERNAL_LINKS). The shared view (AutonaWebPanelHost, given this instance
  by the Window) decides a request is one and calls these.

THREADING: the main window and the renderers are the GUI thread's. The web
layer's operations are wrapped in gui_thread_sync (the request waits for the
answer); the rest is called on the GUI thread.

Import side effects: none (agent-pitfalls #1).
"""
import os

from qtpy.QtCore import QBuffer, QIODevice, QUrl
from qtpy.QtGui import QDesktopServices
from loguru import logger

from .AutonaExternalLinks import EXTERNAL_LINKS
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
    crew board -- which has no set selection nor thumbnail -- ValueError, the
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
        # The main window, which does not exist yet (UiMounted).
        self._parent = None

    def UiMounted(self, window):
        """The main window is there (Window.__init__): what opens the SCORE
        page's set selection (Window.OpenScoreSets). Until ADR-005 S4ac-3 the
        Qt set windows were built here too, parented to it, so that they
        followed the tournament's phases from the start."""
        self._parent = window

    # ── the set selection ────────────────────────────────────────────────

    def ShowSetSelection(self, board):
        """Open board `board`'s (1/2) set selection: the SCORE page's dialog
        (_Open). The load_set hotkey's; the legacy route is
        OpenSetSelection. Until ADR-005 S4ac-3 the way back to the Qt panel
        (and an ask before the main window was there) opened the Qt
        window."""
        self._Open(_SetBoard(board))

    def _Open(self, board):
        """Have the SCORE page open board `board`'s (1/2) set selection
        (_OpenOnPage), once the main window is there: before that (the
        shell is bootstrapped, the window not built yet) nothing opens, and
        that is logged. What it raises is logged, not raised: neither the
        hotkey's handler nor the legacy route's answer fails on it (as an
        open of the Qt window that failed did not)."""
        try:
            if self._parent is None:
                logger.info(
                    f"Shell operations - The set selection of scoreboard "
                    f"{board} was asked for before the main window is there: "
                    "nothing opened")
                return
            self._OpenOnPage(board)
        except Exception:
            logger.opt(exception=True).error(
                f"Shell operations - The set selection of scoreboard {board} "
                "did not open")

    def _OpenOnPage(self, board):
        """Have the main window show SCORE and its page open board `board`'s
        set selection dialog (Window.OpenScoreSets): only for the board of
        the battle format, the one the page shows. Another one -- the legacy
        /scoreboard2-open-set in 1v1 -- opens nothing and is logged (the
        owner's call, 2026-11-11): the page would not show it, and switching
        the format is not the route's to do."""
        active = AutonaActions.instance.ActiveBoard()
        if board != active:
            logger.info(
                f"Shell operations - The set selection of scoreboard {board} "
                f"was asked for while SCORE shows scoreboard {active}: "
                "nothing opened")
            return
        self._parent.OpenScoreSets(board)

    # ── a board's thumbnail ──────────────────────────────────────────────

    def DrawThumbnail(self, board):
        """Board `board`'s match thumbnail, drawn now and saved
        (ThumbnailService.DrawBoard): its PNG's path, the .jpg beside it.
        On the GUI thread (DrawThumbnailNow). Raises what DrawBoard raises:
        ThumbnailDataMissing with no match to draw, the renderer's failure.
        Needs no board widget (there are none since ADR-005 S4ac-3)."""
        return ThumbnailService.instance.DrawBoard(board, DrawThumbnailNow)

    # ── The shared view's shell requests (ADR-005 S-final SF-5a-1) ───────
    # AutonaWebPanelHost calls these on the GUI thread, from the event loop
    # (not inside Chromium's new-window call), for a request it took
    # (ParseShellRequest: the verb and its arguments are SHELL_VERBS'
    # values). Each answer is handed to the page as it is (JSON):
    # {"ok": True, ...} or {"ok": False, "error": <code>}.

    def ShellState(self):
        """What the pages show of the desktop: {"alwaysOnTop"} (False
        before the main window is there)."""
        window = self._parent
        return {"alwaysOnTop": bool(window is not None
                                    and window.IsAlwaysOnTop())}

    def ShellRequest(self, verb, args):
        """Run shell request `verb` with `args` (its arguments, str values):
        its answer."""
        if verb == "alwaysOnTop":
            return self._AlwaysOnTop(args.get("on"))
        if verb == "checkUpdates":
            return self._CheckUpdates()
        if verb == "openExternal":
            return self._OpenExternal(args.get("target"))
        logger.warning(f"Shell operations - Unknown shell request {verb!r}")
        return {"ok": False, "error": "unknown_verb"}

    def _AlwaysOnTop(self, on):
        """The main window's "always on top", set to `on` ("0" / "1") as its
        menu's check would be (Window.SetAlwaysOnTop: the check and the
        window flag; the window is hidden and shown again by Qt): the state
        it is in then."""
        if on not in ("0", "1"):
            return {"ok": False, "error": "bad_argument"}
        window = self._parent
        if window is None:
            return {"ok": False, "error": "not_ready"}
        window.SetAlwaysOnTop(on == "1")
        return {"ok": True, "alwaysOnTop": bool(window.IsAlwaysOnTop())}

    def _CheckUpdates(self):
        """The update check, as the Help & Settings menu's "Check for
        updates" starts it (not silent: the updater shows its answer in a
        dialog of its own). Started, not waited for. While a check is still
        running (the startup's silent one, or one asked before) the updater
        starts nothing, so the answer says so ("busy") instead of "ok"."""
        # Desktop-only, and nothing of it is needed before the first ask.
        from .AutonaUpdater import AutonaUpdater
        updater = AutonaUpdater.instance
        if updater is None:
            return {"ok": False, "error": "not_ready"}
        # Read only: the updater's own guard in CheckForUpdates is the same
        # test, and the updater is not changed for the page (SF-5a-1).
        running = getattr(updater, "_check_thread", None)
        if running is not None and running.isRunning():
            return {"ok": False, "error": "busy"}
        updater.CheckForUpdates(silent=False)
        return {"ok": True}

    def _OpenExternal(self, target):
        """Open outside address `target` (a key of EXTERNAL_LINKS; never a
        URL from the page) as the Help & Settings menu does: the default
        browser, or the mail program for the inquiry address."""
        address = EXTERNAL_LINKS.get(target)
        if address is None:
            return {"ok": False, "error": "bad_argument"}
        if not QDesktopServices.openUrl(QUrl(address)):
            logger.warning(f"Shell operations - The system could not open "
                           f"the {target} address")
            return {"ok": False, "error": "open_failed"}
        return {"ok": True}

    # ── The web layer's shell operations (ADR-005 S3e-9) ─────────────────
    # bootstrap_gui() registers them as AutonaWebOperations.openSetSelection
    # / renderThumbnail / startImageRender. On the GUI thread, where the
    # main window and the renderers live; the request waits for the answer.
    # They were the scoreboard dock's until ADR-005 S4y, which built the
    # board first and asked its widget. Neither builds nor makes a board.

    @gui_thread_sync
    def OpenSetSelection(self, number):
        """Open board `number`'s set selection: "OK". The crew board has none
        and raises (the legacy route's 500, as before).

        The SCORE page's dialog (ADR-005 S4ab-2): the main window is brought
        to the front on SCORE and its page opens it (_Open). No board is made
        ready for it -- what the dialog loads goes through /api/v1's
        :load-set -- and a board other than the format's (the one the page
        shows) opens nothing and is logged. Until ADR-005 S4ac-3 the way back
        to the Qt panel opened the Qt window instead, its board made ready
        first through the board host.

        Opening it never fails the request: until S3e-9 the window was
        queued from the request's thread, so what it raised was only
        reported and the answer was "OK". It still is: logged here. So is a
        board the page does not show, and an ask before the main window is
        there: "OK" as well, as an open that the legacy route's callers
        (Companion's buttons) cannot tell apart from one that opened -- they
        never could: the answer never said whether a window came up."""
        self._Open(_SetBoard(number))
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
