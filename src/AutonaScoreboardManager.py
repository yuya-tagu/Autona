from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .AutonaScoreboardWidget import AutonaScoreboardWidget
from .AutonaCrewBattleWidget import AutonaCrewBattleWidget
from .Helpers.AutonaQtHelper import (
    GuiMarshalAborted, gui_thread_sync, in_gui_thread)
from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from . import ui_constants as UI


# The web layer's shell operations (the set selection window, the legacy
# thumbnail, the off-screen renders) and the image encoders were this dock's
# until ADR-005 S4y: src/AutonaShellOperations.py.


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

        # The score subtree's reset and the boards themselves are core's,
        # made at launch right after this dock (AutonaActions.MakeBoards,
        # bootstrap_gui(); ADR-005 S4ac-1). This constructor reset the
        # subtree until then.

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
                # Core made the board at launch (AutonaActions.MakeBoards,
                # ADR-005 S4ac-1): its launch write and -- the desktop's,
                # `panel` -- the display options, polling turned off and the
                # hold. The widgets are built on it and render it, and write
                # nothing as they are. EnsureBoard does nothing then; it
                # makes the board here only when core does not have it (its
                # launch failed, and the board host's Prepare asked; a test's
                # harness drops a board between tests), in one cycle with the
                # build, as every build did until S4ac-1.
                made = AutonaActions.instance.EnsureBoard(number, panel=True)
                try:
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
                except BaseException:
                    # No widget to render the board core made just now: core
                    # forgets it (not ready, its hold released), or Built()
                    # would say it is there -- the swap and load-set hotkeys
                    # would act on it -- and the next build would skip its
                    # launch write. Not a board made before (at launch): it
                    # stays made and held, for the page that renders it
                    # without widgets, and the next build would otherwise
                    # seed it again over what was written to it since.
                    self.scoreboards.pop(number, None)
                    if made:
                        AutonaActions.instance.ForgetBoard(number)
                    raise
        finally:
            self._ensuring.discard(number)
        return board

    def CreateScoreboard(self, fmt: str = None) -> bool:
        # Startup path: build only the widgets of the board the persisted
        # battle format actually shows (startup Step 4e). The other boards'
        # widgets are built on demand — by SetBattleFormat() when the
        # operator switches format, or by GetScoreboard() when a web/API call
        # addresses them. Every board is already in the state, whole: core
        # made all three at launch (AutonaActions.MakeBoards, ADR-005
        # S4ac-1). Until then this seeded the others' skeleton
        # (StateSchema.SeedBoard) as a placeholder; now that would replace
        # boards already made -- what was written to them since, and their
        # launch write.
        number = self.FORMAT_BOARDS.get(fmt, 1)
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
        # Built here for the web operations' desktop board host
        # (AutonaWebOperations.DesktopBoardHost), whose Prepare asks for it
        # only for a board core does not have since ADR-005 S4ac-1 (every
        # board is made at launch; a made board's widgets are built by the
        # window and by a format switch). The shell operations no longer
        # need one (ADR-005 S4y, AutonaShellOperations). Construction is
        # GUI-thread only, so calls arriving on the Flask thread (the board
        # host's Prepare outside its Run: get_swap, get_scoreboard) are
        # marshalled to it (gui_thread_sync). Once the app is shutting
        # down that marshal gives up (GuiMarshalAborted): not a failure to
        # build, so it is raised on for the caller to answer (the board host
        # answers 503), logged at INFO here (ADR-005 S3f-4 review).
        try:
            if in_gui_thread():
                scoreboard = self.EnsureScoreboard(number)
            else:
                scoreboard = gui_thread_sync(self.EnsureScoreboard)(number)
        except GuiMarshalAborted as aborted:
            logger.info(
                f"Scoreboard Manager - Scoreboard {number} not built: "
                f"{aborted}")
            raise
        except Exception:
            logger.opt(exception=True).error(
                f"Scoreboard Manager - Failed to create scoreboard {number}")
            return None

        if scoreboard is None:
            logger.error(
                f"Scoreboard Manager - Scoreboard {number} not available")
        return scoreboard
