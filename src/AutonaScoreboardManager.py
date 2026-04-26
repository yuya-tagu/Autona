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

    def CreateScoreboard(self, singlePlayer: bool = True) -> bool:
        # Idempotent: Autona now runs with a single scoreboard. Subsequent
        # calls are no-ops.
        if self.scoreboard is not None:
            return True

        with StateManager.SaveBlock():
            logger.info(
                f"Scoreboard Manager - Creating Scoreboard (single={singlePlayer})")
            self.scoreboard = AutonaScoreboardWidget(
                scoreboardNumber=1, singlePlayer=singlePlayer)
            self.widget.layout().addWidget(self.scoreboard)
        return True

    def GetScoreboard(self, number=1):
        # `number` kept for call-site compatibility but always resolves to
        # the single scoreboard.
        if self.scoreboard is None:
            logger.error("Scoreboard Manager - Scoreboard not yet created")
        return self.scoreboard


AutonaScoreboardManager.instance = AutonaScoreboardManager()
