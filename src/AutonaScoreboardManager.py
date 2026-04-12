from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .AutonaScoreboardWidget import AutonaScoreboardWidget
from .SettingsManager import SettingsManager
from .StateManager import StateManager


class AutonaScoreboardManagerSignals(QObject):
    ScoreboardAmountChanged = Signal(int)
    EditRequested = Signal()


class AutonaScoreboardManager(QDockWidget):
    instance: "AutonaScoreboardManager" = None

    MAX_SCOREBOARDS = 10

    def __init__(self, *args):
        super().__init__(*args)

        StateManager.Unset("score")

        self.signals: AutonaScoreboardManagerSignals = AutonaScoreboardManagerSignals()
        logger.info("Scoreboard Manager - Initializing")

        self.setFloating(True)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget = QWidget()
        self.widget.setMinimumWidth(0)
        self.widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(self.widget)
        self.widget.setLayout(QVBoxLayout())

        self.tabs = QTabWidget()
        self.widget.layout().addWidget(self.tabs)

        self.scoreboardholder = []

    def CreateScoreboard(self, singlePlayer: bool = True) -> bool:
        # Append a new scoreboard tab. Returns False when the limit is hit.
        if len(self.scoreboardholder) >= self.MAX_SCOREBOARDS:
            logger.warning(
                f"Scoreboard Manager - Max scoreboards ({self.MAX_SCOREBOARDS}) reached")
            return False

        StateManager.BlockSaving()
        try:
            amount = len(self.scoreboardholder) + 1
            logger.info(f"Scoreboard Manager - Creating Scoreboard {amount} (single={singlePlayer})")

            # When we go from 1 to 2 scoreboards, the global "track player"
            # button on scoreboard 1 needs to be hidden per existing behavior.
            if amount == 2 and not SettingsManager.Get("general.hide_track_player", False):
                self.GetScoreboard(1).btLoadPlayerSet.setHidden(True)
                self.GetScoreboard(1).btLoadPlayerSetOptions.setHidden(True)

            scoreboard = QWidget()
            scoreboard.setLayout(QVBoxLayout())
            scoreboardObj = AutonaScoreboardWidget(
                scoreboardNumber=amount, singlePlayer=singlePlayer)
            self.scoreboardholder.append(scoreboardObj)
            scoreboard.layout().addWidget(scoreboardObj)
            self.tabs.addTab(
                scoreboard,
                QApplication.translate("app", "Scoreboard") + " " + str(amount))
        finally:
            StateManager.ReleaseSaving()

        self.signals.ScoreboardAmountChanged.emit(len(self.scoreboardholder))
        return True

    def RemoveScoreboard(self, index: int) -> bool:
        # index is 1-based. Scoreboard 1 cannot be removed.
        if index <= 1 or index > len(self.scoreboardholder):
            logger.warning(f"Scoreboard Manager - Refusing to remove scoreboard {index}")
            return False

        StateManager.BlockSaving()
        try:
            logger.info(f"Scoreboard Manager - Removing Scoreboard {index}")
            tabIndex = index - 1
            self.tabs.removeTab(tabIndex)
            self.scoreboardholder[tabIndex].deleteLater()
            self.scoreboardholder.pop(tabIndex)

            if len(self.scoreboardholder) == 1 and not SettingsManager.Get(
                    "general.hide_track_player", False):
                self.GetScoreboard(1).btLoadPlayerSet.setHidden(False)
                self.GetScoreboard(1).btLoadPlayerSetOptions.setHidden(False)

            StateManager.Unset(f"score.{index}")
        finally:
            StateManager.ReleaseSaving()

        self.signals.ScoreboardAmountChanged.emit(len(self.scoreboardholder))
        return True

    def GetScoreboard(self, number):
        if int(number)-1 < len(self.scoreboardholder):
            return self.scoreboardholder[int(number)-1]
        else:
            logger.error(
                f"Scoreboard Manager - Unable to retrieve scoreboard {number}, defaulting to scoreboard 1")
            return self.scoreboardholder[0]

    def SetTabName(self, index, name):
        if int(index)-1 < self.tabs.count():
            if name != "":
                self.tabs.setTabText(int(index)-1, name)
            else:
                self.tabs.setTabText(int(index)-1,
                                     QApplication.translate("app", "Scoreboard") + " " + str(index))
        else:
            logger.error(f"Invalid Scoreboard ID provided: {index}")
            logger.error(
                f"Please provide an ID between 1 and {self.tabs.count()}")

    def GetTabName(self, index) -> str:
        if int(index)-1 < self.tabs.count():
            return self.tabs.tabText(int(index)-1)
        return ""

    def GetTabAmount(self):
        return self.tabs.count()


AutonaScoreboardManager.instance = AutonaScoreboardManager()
