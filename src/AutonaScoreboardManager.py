from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .AutonaScoreboardWidget import AutonaScoreboardWidget
from .AutonaCrewBattleWidget import AutonaCrewBattleWidget
from .StateManager import StateManager


class AutonaScoreboardManager(QDockWidget):
    instance: "AutonaScoreboardManager" = None

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

        self.scoreboard: AutonaScoreboardWidget | None = None
        self.teamScoreboard: AutonaScoreboardWidget | None = None
        self.crewScoreboard: AutonaCrewBattleWidget | None = None

        # Registry of scoreboards keyed by number. board 1 = 1v1 (SCORE),
        # board 2 = team (TEAM), board 3 = crewbattle (CREW). All live in this
        # (SCORE) dock; the "Rules" menu toggles which one is visible via
        # SetBattleFormat(). Registered here so GetScoreboard() / the WebServer
        # can reach any board by number.
        self.scoreboards: dict[int, object] = {}

    def CreateScoreboard(self, singlePlayer: bool = True) -> bool:
        # Idempotent: creates the 1v1 board (1), the team board (2) and the
        # crewbattle board (3), all hosted in this dock's layout. Boards 2 and
        # 3 start hidden; SetBattleFormat() toggles which board is shown.
        if self.scoreboard is not None:
            return True

        with StateManager.SaveBlock():
            logger.info(
                "Scoreboard Manager - Creating scoreboards (1v1 + team + crew)")
            # board 1 = 1v1 (SCORE)
            self.scoreboard = AutonaScoreboardWidget(
                scoreboardNumber=1, singlePlayer=True)
            self.widget.layout().addWidget(self.scoreboard)
            self.scoreboards[1] = self.scoreboard
            # board 2 = team (TEAM), same dock, hidden until "Team" is picked
            self.teamScoreboard = AutonaScoreboardWidget(
                scoreboardNumber=2, singlePlayer=False)
            self.widget.layout().addWidget(self.teamScoreboard)
            self.scoreboards[2] = self.teamScoreboard
            self.teamScoreboard.setVisible(False)
            # board 3 = crewbattle (CREW), same dock, hidden until picked
            self.crewScoreboard = AutonaCrewBattleWidget(scoreboardNumber=3)
            self.widget.layout().addWidget(self.crewScoreboard)
            self.scoreboards[3] = self.crewScoreboard
            self.crewScoreboard.setVisible(False)
        return True

    def SetBattleFormat(self, fmt: str):
        # Toggle which board the SCORE dock shows. fmt: "singles" (board 1),
        # "team" (board 2) or "crew" (board 3). Driven by the "Rules" menu.
        visible = {"singles": 1, "team": 2, "crew": 3}.get(fmt, 1)
        for number, board in self.scoreboards.items():
            board.setVisible(number == visible)
        logger.info(f"Scoreboard Manager - Battle format: {fmt}")

    def SetTeamMode(self, team: bool):
        # Backward-compatible shim: the format menu now uses SetBattleFormat().
        self.SetBattleFormat("team" if team else "singles")

    def GetScoreboard(self, number=1):
        number = int(number)
        scoreboard = self.scoreboards.get(number)
        if scoreboard is None:
            logger.error(
                f"Scoreboard Manager - Scoreboard {number} not yet created")
        return scoreboard


AutonaScoreboardManager.instance = AutonaScoreboardManager()
