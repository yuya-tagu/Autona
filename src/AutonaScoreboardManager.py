from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .AutonaScoreboardWidget import AutonaScoreboardWidget
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

        # Registry of scoreboards keyed by number. board 1 = 1v1 (SCORE),
        # board 2 = team (TEAM). Both live in this (SCORE) dock; the "対戦形式"
        # menu toggles which one is visible via SetTeamMode(). Registered here
        # so GetScoreboard() / the WebServer can reach either board by number.
        self.scoreboards: dict[int, AutonaScoreboardWidget] = {}

    def CreateScoreboard(self, singlePlayer: bool = True) -> bool:
        # Idempotent: creates both the 1v1 board (1) and the team board (2),
        # both hosted in this dock's layout. The team board starts hidden;
        # SetTeamMode() toggles which board is shown.
        if self.scoreboard is not None:
            return True

        with StateManager.SaveBlock():
            logger.info("Scoreboard Manager - Creating scoreboards (1v1 + team)")
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
        return True

    def SetTeamMode(self, team: bool):
        # Toggle which board the SCORE dock shows: 1v1 (board 1) or team
        # (board 2). Driven by the "対戦形式" menu.
        board1 = self.scoreboards.get(1)
        board2 = self.scoreboards.get(2)
        if board1 is not None:
            board1.setVisible(not team)
        if board2 is not None:
            board2.setVisible(team)
        logger.info(
            f"Scoreboard Manager - Battle format: {'Team' if team else '1v1'}")

    def GetScoreboard(self, number=1):
        number = int(number)
        scoreboard = self.scoreboards.get(number)
        if scoreboard is None:
            logger.error(
                f"Scoreboard Manager - Scoreboard {number} not yet created")
        return scoreboard


AutonaScoreboardManager.instance = AutonaScoreboardManager()
