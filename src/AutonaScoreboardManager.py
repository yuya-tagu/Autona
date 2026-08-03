from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .AutonaScoreboardWidget import AutonaScoreboardWidget
from .AutonaCrewBattleWidget import AutonaCrewBattleWidget
from .Helpers.AutonaQtHelper import gui_thread_sync, in_gui_thread
from .StateManager import StateManager
from . import ui_constants as UI


class AutonaScoreboardManager(QDockWidget):
    instance: "AutonaScoreboardManager" = None

    # battle format -> board number. Single source of truth for both the
    # startup build (CreateScoreboard) and the runtime switch
    # (SetBattleFormat).
    FORMAT_BOARDS = {"singles": 1, "team": 2, "crew": 3}
    BOARD_NUMBERS = (1, 2, 3)
    BOARD_LABELS = {1: "1v1", 2: "team", 3: "crew"}

    def __init__(self, *args):
        super().__init__(*args)

        # Wipe any residual score.* keys from an older multi-scoreboard
        # state.json so we start from a clean single-scoreboard layout.
        StateManager.Unset("score")

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

    def _SeedBoardState(self, number: int):
        # Boards seed their own score.{N} subtree from their __init__; a board
        # that is not built yet would leave the key missing entirely and the
        # Web UI / OBS overlays would read "no such board" instead of an empty
        # one. Seed the same skeleton the board writes so a later build only
        # ever rewrites keys that already exist.
        StateManager.Set(f"score.{number}", {})
        if number == 3:
            # AutonaCrewBattleWidget seeds nothing beyond the subtree root
            # (the rest is written by its _writeInitialState()).
            return
        StateManager.Set(f"score.{number}.last_sets.1", {})
        StateManager.Set(f"score.{number}.last_sets.2", {})
        StateManager.Set(f"score.{number}.history_sets.1", {})
        StateManager.Set(f"score.{number}.history_sets.2", {})
        StateManager.Set(f"score.{number}.station_queue", {})

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
        number = self.FORMAT_BOARDS.get(fmt, 1)
        with StateManager.SaveBlock():
            for other in self.BOARD_NUMBERS:
                if other != number and other not in self.scoreboards:
                    self._SeedBoardState(other)
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
        # Build it here so every caller — the ~40 web server actions, the REST
        # API, the desktop panels — keeps working unchanged. Construction is
        # GUI-thread only, so calls arriving on the Flask thread are
        # marshalled the same way WebServerActions._ensure_panel does it.
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
