
from loguru import logger
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
from .AutonaPlayerListSlotWidget import AutonaPlayerListSlotWidget

from .StateManager import StateManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider


class AutonaPlayerListWidgetSignals(QObject):
    UpdateData = Signal(object)
    DataChanged = Signal()


class AutonaPlayerList(QWidget):
    def __init__(self, *args, base="player_list"):
        with StateManager.SaveBlock():
            super().__init__(*args)

            self.signals = AutonaPlayerListWidgetSignals()

            self.base = base

            self.slotWidgets: list[AutonaPlayerListSlotWidget] = []

            self.playersPerTeam = 0
            self.charactersPerPlayer = 0

            self.setLayout(QVBoxLayout())

            self.childDataChangedLock = False

            scrollArea = QScrollArea()
            scrollArea.setFrameShadow(QFrame.Shadow.Plain)
            scrollArea.setFrameShape(QFrame.Shape.NoFrame)
            scrollArea.setWidgetResizable(True)

            self.widgetArea = QWidget()
            self.widgetArea.setLayout(QVBoxLayout())
            self.widgetArea.setSizePolicy(
                QSizePolicy.Preferred, QSizePolicy.Maximum)
            scrollArea.setWidget(self.widgetArea)

            self.layout().addWidget(scrollArea)

            StateManager.Set(base, {})

    def ChildDataChangedEmit(self):
        if not self.childDataChangedLock:
            self.signals.DataChanged.emit()

    def LoadFromStandingsClicked(self):
        AutonaTournamentDataProvider.instance.GetStandings(
            self.slotNumber.value(), self.signals.UpdateData)

    def LoadFromStandings(self, data):
        with StateManager.SaveBlock():
            if len(data) > 0:
                self.SetSlotNumber(len(data))
                playerNumber = len(data[0].get("players"))
                self.SetPlayersPerTeam(playerNumber)

                self.childDataChangedLock = True
                try:
                    for i, slot in enumerate(self.slotWidgets):
                        slot.SetTeamData(data[i])
                finally:
                    self.childDataChangedLock = False

    def SetSlotNumber(self, number):
        with StateManager.SaveBlock():
            while len(self.slotWidgets) < number:
                s = AutonaPlayerListSlotWidget(
                    len(self.slotWidgets)+1, self, base=self.base)
                self.slotWidgets.append(s)
                self.widgetArea.layout().addWidget(s)
                s.SetPlayersPerTeam(self.playersPerTeam)
                s.SetCharacterNumber(self.charactersPerPlayer)
                s.signals.dataChanged.connect(self.ChildDataChangedEmit)

                # s.SetCharactersPerPlayer(self.charNumber.value())

                # index = len(self.team1playerWidgets)

                # p.btMoveUp.clicked.connect(lambda x, index=index, p=p: p.SwapWith(
                #     self.team1playerWidgets[index-1 if index > 0 else 0]))
                # p.btMoveDown.clicked.connect(lambda x, index=index, p=p: p.SwapWith(
                #     self.team1playerWidgets[index+1 if index < len(self.team1playerWidgets) - 1 else index]))

            while len(self.slotWidgets) > number:
                s = self.slotWidgets[-1]
                s.setParent(None)
                self.slotWidgets.remove(s)
                StateManager.Unset(f'{self.base}.slot.{s.index}')

            self.signals.DataChanged.emit()

    def SetCharactersPerPlayer(self, value):
        # logger.info("AutonaPlayerList#SetCharactersPerPlayer")
        self.charactersPerPlayer = value
        with StateManager.SaveBlock():
            self.childDataChangedLock = True
            try:
                for s in self.slotWidgets:
                    s.SetCharacterNumber(value)
            finally:
                self.childDataChangedLock = False
            self.signals.DataChanged.emit()


    def SetPlayersPerTeam(self, number):
        # logger.info("AutonaPlayerList#SetPlayersPerTeam")
        self.playersPerTeam = number
        with StateManager.SaveBlock():
            self.childDataChangedLock = True
            try:
                for s in self.slotWidgets:
                    s.SetPlayersPerTeam(number)
            finally:
                self.childDataChangedLock = False
            self.signals.DataChanged.emit()
