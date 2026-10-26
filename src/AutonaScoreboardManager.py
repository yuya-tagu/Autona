import os

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .AutonaScoreboardWidget import AutonaScoreboardWidget
from .AutonaCrewBattleWidget import AutonaCrewBattleWidget
from .Helpers.AutonaQtHelper import gui_thread_sync, in_gui_thread
from .core.StateManager import StateManager
from .core import StateSchema as schema
from .core.Actions import AutonaActions
from . import ui_constants as UI


class AutonaScoreboardManager(QDockWidget):
    instance: "AutonaScoreboardManager" = None

    # battle format -> board number, used by the startup build
    # (CreateScoreboard) and the runtime switch (SetBattleFormat). Aliased
    # from core rather than spelled out again: AutonaActions resolves the
    # same mapping to decide which board a hotkey acts on, and two copies
    # would let the headless answer drift from the one the window shows
    # (headless core Step 3). The core definition is already a read-only
    # MappingProxyType, so this alias cannot be edited for both holders.
    FORMAT_BOARDS = AutonaActions.FORMAT_BOARDS
    BOARD_NUMBERS = (1, 2, 3)
    BOARD_LABELS = {1: "1v1", 2: "team", 3: "crew"}

    def __init__(self, *args):
        super().__init__(*args)

        # Wipe any residual score.* keys from an older multi-scoreboard
        # state.json so we start from a clean single-scoreboard layout.
        # The subtree lifecycle op lives with SeedBoard() in the schema
        # (headless core Step 5) -- `score` is a schema name, and this class
        # is a QDockWidget.
        schema.ResetScoreRoot()

        logger.info("Scoreboard Manager - Initializing")

        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget = QWidget()
        self.widget.setMinimumWidth(0)
        self.widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(self.widget)
        self.widget.setLayout(QVBoxLayout())
        self.widget.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)

        self.scoreboard: AutonaScoreboardWidget | None = None
        self.teamScoreboard: AutonaScoreboardWidget | None = None
        self.crewScoreboard: AutonaCrewBattleWidget | None = None

        # Registry of scoreboards keyed by number. board 1 = 1v1 (SCORE),
        # board 2 = team (TEAM), board 3 = crewbattle (CREW). All live in this
        # (SCORE) dock; the "Rules" menu toggles which one is visible via
        # SetBattleFormat(). Registered here so GetScoreboard() / the WebServer
        # can reach any board by number. Boards are built lazily (startup
        # Step 4e), so a number missing from this dict just means "not built
        # yet" — EnsureScoreboard() creates it on demand.
        self.scoreboards: dict[int, object] = {}

        # Board numbers currently inside EnsureScoreboard(): the widget is not
        # registered in `scoreboards` until construction finishes, so this
        # guards against a re-entrant build of the same board.
        self._ensuring: set[int] = set()

    def EnsureScoreboard(self, number: int):
        # Build board `number` once and return it. Idempotent and GUI-thread
        # only (it constructs widgets) — callers on the web server thread go
        # through GetScoreboard(), which marshals the build. The board starts
        # hidden; SetBattleFormat() decides what is shown.
        number = int(number)
        board = self.scoreboards.get(number)
        if board is not None:
            return board
        if number not in self.BOARD_NUMBERS:
            logger.error(f"Scoreboard Manager - Unknown scoreboard {number}")
            return None
        if number in self._ensuring:
            # Re-entered from inside this board's own construction: building
            # again would put a second widget on the same state subtree.
            logger.error(
                f"Scoreboard Manager - Re-entrant build of scoreboard {number}")
            return None

        self._ensuring.add(number)
        try:
            with StateManager.SaveBlock():
                logger.info(
                    f"Scoreboard Manager - Creating scoreboard {number} "
                    f"({self.BOARD_LABELS[number]})")
                if number == 1:
                    board = AutonaScoreboardWidget(
                        scoreboardNumber=1, singlePlayer=True)
                    self.scoreboard = board
                elif number == 2:
                    board = AutonaScoreboardWidget(
                        scoreboardNumber=2, singlePlayer=False)
                    self.teamScoreboard = board
                else:
                    board = AutonaCrewBattleWidget(scoreboardNumber=3)
                    self.crewScoreboard = board
                self.widget.layout().addWidget(board)
                self.scoreboards[number] = board
                board.setVisible(False)
                # A board built after the initial game load missed
                # AutonaGameAssetManager's onLoad broadcast, which is what
                # applies the game defaults (players per team / characters per
                # player). Character models are read when the player widgets
                # are built, so only these board-level defaults need catching
                # up. Commit them like the onLoad handler does so the fresh
                # baseline is not left waiting behind the 反映 button.
                if hasattr(board, "SetDefaultsFromAssets"):
                    board.SetDefaultsFromAssets()
                    StateManager.CommitHeld(board.statePrefix)
        finally:
            self._ensuring.discard(number)
        return board

    def CreateScoreboard(self, fmt: str = None) -> bool:
        # Startup path: build only the board the persisted battle format
        # actually shows (startup Step 4e). The other boards are built on
        # demand — by SetBattleFormat() when the operator switches format, or
        # by GetScoreboard() when a web/API call addresses them — and their
        # score.{N} subtree is seeded here so nothing reads a missing key in
        # the meantime. Idempotent: already-built boards are left alone and
        # re-seeding an unbuilt one rewrites the same empty skeleton.
        # StateSchema.SeedBoard is the same function the board's own __init__
        # calls, so the placeholder and the built board can no longer drift
        # apart (headless core Step 2).
        number = self.FORMAT_BOARDS.get(fmt, 1)
        with StateManager.SaveBlock():
            for other in self.BOARD_NUMBERS:
                if other != number and other not in self.scoreboards:
                    schema.SeedBoard(other)
            self.EnsureScoreboard(number)
        return True

    def SetBattleFormat(self, fmt: str):
        # Toggle which board the SCORE dock shows. fmt: "singles" (board 1),
        # "team" (board 2) or "crew" (board 3). Driven by the "Rules" menu.
        # The target board may not exist yet (lazy startup build), so make
        # sure it does before switching visibility.
        visible = self.FORMAT_BOARDS.get(fmt, 1)
        self.EnsureScoreboard(visible)
        for number, board in self.scoreboards.items():
            board.setVisible(number == visible)
        logger.info(f"Scoreboard Manager - Battle format: {fmt}")

    def GetScoreboard(self, number=1):
        number = int(number)
        scoreboard = self.scoreboards.get(number)
        if scoreboard is not None:
            return scoreboard

        # Not built yet (only the active format's board is built at startup).
        # Build it here so every caller — the web operations' desktop board
        # host (AutonaWebOperations.DesktopBoardHost), the shell operations
        # below, the desktop panels — gets a board. Construction is
        # GUI-thread only, so calls arriving on the Flask thread (the board
        # host's Prepare / Widget outside its Run: get_swap, get_scoreboard)
        # are marshalled to it (gui_thread_sync).
        try:
            if in_gui_thread():
                scoreboard = self.EnsureScoreboard(number)
            else:
                scoreboard = gui_thread_sync(self.EnsureScoreboard)(number)
        except Exception:
            logger.opt(exception=True).error(
                f"Scoreboard Manager - Failed to create scoreboard {number}")
            return None

        if scoreboard is None:
            logger.error(
                f"Scoreboard Manager - Scoreboard {number} not available")
        return scoreboard

    # ── The web layer's shell operations (ADR-005 S3e-9) ─────────────────
    # What only the desktop's windows answer. bootstrap_gui() registers them
    # as AutonaWebOperations.openSetSelection / renderThumbnail; the headless
    # shell has neither, and the web layer answers them 503. On the GUI
    # thread, where the boards and their windows live (an unbuilt board is
    # built there first); the request waits for the answer. Moved unchanged
    # from the web server's former actions class, apart from the window
    # being opened on the GUI thread itself instead of through a signal
    # queued from the request's thread (what opening it raises is still
    # only logged: OpenSetSelection).

    @gui_thread_sync
    def OpenSetSelection(self, number):
        """Open board `number`'s set selection window: "OK". Another board
        than 1/2 has none and raises (the legacy route's 500, as before).

        Opening the window itself never fails the request: until S3e-9 it
        was queued from the request's thread, so what it raised was only
        reported and the answer was "OK". It still is. PySide6 hands a
        slot's exception to sys.excepthook (logged as uncaught) instead of
        raising it into emit(); one a binding raises here is logged."""
        selection = self.GetScoreboard(number).signals.SetSelection
        try:
            selection.emit()
        except Exception:
            logger.opt(exception=True).error(
                f"Scoreboard Manager - The set selection of scoreboard "
                f"{number} did not open")
        return "OK"

    @gui_thread_sync
    def RenderThumbnail(self, number, file_format):
        """Render board `number`'s thumbnail (quietly: no message box): the
        image's absolute path -- its .jpg sibling for "jpg" -- or None when
        it failed."""
        thumbnailPath = self.GetScoreboard(number).GenerateThumbnail(
            quiet_mode=True, disable_msgbox=True)
        if thumbnailPath:
            if file_format == "jpg":
                thumbnailPath = thumbnailPath.replace(".png", ".jpg")
            return os.path.abspath(thumbnailPath)
        return None
