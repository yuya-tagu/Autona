from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import traceback
from loguru import logger

from .AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
from .StateManager import StateManager


class AutonaPlayerListSlotWidgetSignals(QObject):
    dataChanged = Signal()


class AutonaPlayerListSlotWidget(QGroupBox):
    def __init__(self, index, playerList, base="player_list", *args):
        super().__init__(*args)
        self.index = index
        self.playerList = playerList

        self.base = base

        self.signals = AutonaPlayerListSlotWidgetSignals()

        self.setLayout(QVBoxLayout())

        # Rank/slot header label — mirrors the scoreboard's teamLabel pattern
        # ("1P"/"TEAM 1"). Always visible; text flips with player-per-team mode.
        self.slotHeaderLabel = QLabel()
        self.slotHeaderLabel.setObjectName("slotHeaderLabel")
        self.layout().addWidget(self.slotHeaderLabel)

        self.slotNameWidget = QWidget()
        self.slotNameWidget.setLayout(QHBoxLayout())

        self.slotName = QLineEdit()
        slotNameLabel = QLabel()
        slotNameLabel.setText(QApplication.translate("Form", "Team Name"))
        slotNameLabel.setMaximumWidth(150)
        slotNameLabel.setMinimumWidth(slotNameLabel.maximumWidth())
        self.slotNameWidget.layout().addWidget(slotNameLabel)
        self.slotNameWidget.layout().addWidget(self.slotName)
        self.layout().addWidget(self.slotNameWidget)
        # Default to single-player presentation until SetPlayersPerTeam runs.
        # Child widgets referenced by _UpdateSlotHeader (compactRankLabel) are
        # created later; SetPlayersPerTeam will apply the real mode once they
        # exist.
        self.slotNameWidget.setVisible(False)

        self.childDataChangedLock = False

        self.slotName.editingFinished.connect(
            lambda: [
                StateManager.Set(
                    f"{self.base}.slot.{self.index}.name", self.slotName.text())
            ]
        )
        self.slotName.editingFinished.emit()

        self.list = QWidget()
        self.list.setLayout(QHBoxLayout())
        self.list.layout().setContentsMargins(0, 0, 0, 0)
        # Compact inline rank label — shown only in single-player mode so the
        # slot can collapse into a single row without losing rank context.
        self.compactRankLabel = QLabel()
        self.compactRankLabel.setObjectName("compactRankLabel")
        self.compactRankLabel.setMinimumWidth(28)
        self.compactRankLabel.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.list.layout().addWidget(self.compactRankLabel)
        self.compactRankLabel.setVisible(False)
        self.layout().addWidget(self.list)


        self.playerWidgets = []

    def SetPlayersPerTeam(self, number):
        # logger.info(f"AutonaPlayerListSlotWidget#SetPlayersPerTeam({number})")
        if number != len(self.playerWidgets):
            with StateManager.SaveBlock():
                while len(self.playerWidgets) < number:
                    p = AutonaScoreboardPlayerWidget(
                        index=len(self.playerWidgets)+1, teamNumber=1, path=f'{self.base}.slot.{self.index}.player.{len(self.playerWidgets)+1}')
                    self.playerWidgets.append(p)
                    self.list.layout().addWidget(p)

                    # PlayerList-specific compaction (does not affect Scoreboard,
                    # which uses the same .ui but instantiates widgets elsewhere).
                    self._ApplyPlayerListCompaction(p)

                    p.SetCharactersPerPlayer(self.playerList.charactersPerPlayer)

                    index = len(self.playerWidgets)-1

                    p.btMoveUp.clicked.connect(lambda x=None, index=index, p=p: p.SwapWith(
                        self.playerWidgets[max(0, self.playerWidgets.index(p) - 1)]))
                    p.btMoveDown.clicked.connect(lambda x=None, index=index, p=p: p.SwapWith(
                        self.playerWidgets[min(len(self.playerWidgets) - 1, self.playerWidgets.index(p) + 1)]))

                    p.instanceSignals.dataChanged.connect(
                        self.ChildDataChangedEmit)

                while len(self.playerWidgets) > number:
                    p = self.playerWidgets[-1]
                    p.setParent(None)
                    self.playerWidgets.remove(p)
                    StateManager.Unset(p.path)
                    p.deleteLater()

        # Apply the team/single-player presentation: hide the team-name row
        # when each slot holds exactly one player, mirroring the scoreboard's
        # 1v1 mode layout.
        single_player = number == 1
        self.slotNameWidget.setVisible(not single_player)
        if single_player and self.slotName.text():
            # Team name is meaningless for single-player slots; clear any
            # leftover value so it doesn't leak into StateManager.
            self.slotName.clear()
            self.slotName.editingFinished.emit()
        self._UpdateSlotHeader(single_player=single_player)

    def _ApplyPlayerListCompaction(self, playerWidget):
        """Compact the shared AutonaScoreboardPlayer.ui for PlayerList use:
        - Collapse the CHARACTERS group-box inner top/bottom margins so the
          label sits tight against the first character row.
        - Merge the LOCATION row into the SEED row to save one grid row.
        """
        try:
            characters = playerWidget.findChild(QGroupBox, "characters")
            if characters is not None and characters.layout() is not None:
                m = characters.layout().contentsMargins()
                characters.layout().setContentsMargins(
                    m.left(), 0, m.right(), 0)

            seed_row = playerWidget.findChild(QHBoxLayout, "horizontalLayout")
            location_row = playerWidget.findChild(
                QHBoxLayout, "horizontalLayout_2")
            if seed_row is not None and location_row is not None:
                while location_row.count() > 0:
                    item = location_row.takeAt(0)
                    if item is None:
                        continue
                    w = item.widget()
                    if w is not None:
                        seed_row.addWidget(w)
        except Exception:
            logger.error(traceback.format_exc())

    def _UpdateSlotHeader(self, single_player: bool):
        if single_player:
            # Hide the full-width header label; use the compact inline rank
            # indicator instead so the slot collapses into a single row.
            self.slotHeaderLabel.setVisible(False)
            self.compactRankLabel.setText(f"#{self.index}")
            self.compactRankLabel.setVisible(True)
            self.setProperty("compact", True)
        else:
            self.slotHeaderLabel.setText(QApplication.translate(
                "app", "Slot {0} - Team").format(self.index))
            self.slotHeaderLabel.setVisible(True)
            self.compactRankLabel.setVisible(False)
            self.setProperty("compact", False)

        # Qt doesn't re-evaluate property selectors unless we bounce the style
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def ChildDataChangedEmit(self):
        if not self.childDataChangedLock:
            self.signals.dataChanged.emit()

    def SetCharacterNumber(self, value):
        # logger.info(f"AutonaPlayerListSlotWidget#SetCharacterNumber({value})")
        with StateManager.SaveBlock():
            for pw in self.playerWidgets:
                pw.SetCharactersPerPlayer(value)

    def SetTeamData(self, data):
        with StateManager.SaveBlock():
            self.childDataChangedLock = True
            try:
                if (data.get("name")):
                    self.slotName.setText(data.get("name"))
                    self.slotName.editingFinished.emit()
                else:
                    self.slotName.setText("")
                    self.slotName.editingFinished.emit()

                for i, pw in enumerate(self.playerWidgets):
                    if data.get("players"):
                        try:
                            pw.SetData(data.get("players")[i])
                        except:
                            pw.Clear()
                            logger.error(traceback.format_exc())
                    else:
                        pw.Clear()
            finally:
                self.childDataChangedLock = False
        self.signals.dataChanged.emit()

    def Clear(self):
        with StateManager.SaveBlock():
            self.childDataChangedLock = True
            try:
                for i, pw in enumerate(self.playerWidgets):
                    pw.Clear()
            finally:
                self.childDataChangedLock = False
        self.signals.dataChanged.emit()
