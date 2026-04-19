from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import json
from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Helpers.AutonaDirHelper import AutonaResolve
from .StateManager import StateManager
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaPlayerDB import AutonaPlayerDB
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaPlayerListSlotWidget import AutonaPlayerListSlotWidget
from .AutonaBracketView import AutonaBracketView
from .AutonaPlayerList import AutonaPlayerList
from .AutonaBracket import *
from .AutonaHotkeys import AutonaHotkeys
import traceback
from loguru import logger

# Checks if a number is power of 2


def is_power_of_two(n):
    return (n != 0) and (n & (n-1) == 0)


class AutonaBracketWidgetSignals(QObject):
    UpdateData = Signal(object)


class AutonaBracketWidget(QDockWidget):
    instance: "AutonaBracketWidget" = None
    def __init__(self, *args):
        StateManager.BlockSaving()
        super().__init__(*args)

        uic.loadUi(AutonaResolve("src/layout/AutonaBracket.ui"), self)

        StateManager.Set("bracket", {})

        AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.connect(
            self.UpdatePhases)
        AutonaTournamentDataProvider.instance.signals.tournament_phasegroup_updated.connect(
            self.UpdatePhaseGroup)

        self.signals = AutonaBracketWidgetSignals()

        self.bracket = Bracket(8, 0)

        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

        self.mainLayout = self.findChild(QWidget, "bracket")
        self.mainLayout.setLayout(QVBoxLayout())

        outerLayout: QWidget = self.findChild(QWidget, "bracket")
        self.playerList = AutonaPlayerList(base="bracket.players")
        list: QWidget = self.findChild(QWidget, "listContainer")

        # Add player list settings on the top
        row = QWidget()
        row.setLayout(QHBoxLayout())
        list.layout().addWidget(row)

        col = QWidget()
        col.setLayout(QVBoxLayout())

        self.slotNumber = QSpinBox()
        self.slotNumber.setMinimum(2)
        col.layout().addWidget(QLabel(QApplication.translate("app", "Number of slots")))
        col.layout().addWidget(self.slotNumber)
        self.slotNumber.valueChanged.connect(lambda val: [
            self.playerList.SetSlotNumber(val),
            self.RebuildBracket(val),
        ])
        row.layout().addWidget(col)

        col = QWidget()
        col.setLayout(QVBoxLayout())
        self.playerPerTeam = QSpinBox()
        col.layout().addWidget(QLabel(QApplication.translate("app", "Players per slot")))
        col.layout().addWidget(self.playerPerTeam)
        self.playerPerTeam.valueChanged.connect(
            lambda val: self.playerList.SetPlayersPerTeam(val))
        row.layout().addWidget(col)

        col = QWidget()
        col.setLayout(QVBoxLayout())
        self.charNumber = QSpinBox()
        col.layout().addWidget(QLabel(QApplication.translate("app", "Characters per player")))
        col.layout().addWidget(self.charNumber)
        self.charNumber.valueChanged.connect(
            self.playerList.SetCharactersPerPlayer)
        row.layout().addWidget(col)

        list.layout().addWidget(self.playerList)

        # Heavy initialisation (BracketView creation + player-list slot
        # population, ~2.5s) is deferred until the user first opens this
        # tab. We insert a placeholder label now so the tab has *some*
        # visible content in case Qt paints before showEvent fires.
        self._outerLayout = outerLayout
        self._bracketReady = False
        self.bracketView = None

        self._bracketPlaceholder = QLabel(
            QApplication.translate("app", "Loading bracket..."))
        self._bracketPlaceholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bracketPlaceholder.setStyleSheet(
            "color: #B5ADA0; font-size: 11pt; padding: 40px;")
        outerLayout.layout().addWidget(self._bracketPlaceholder)
        self.phaseSelection: QComboBox = self.findChild(
            QComboBox, "phaseSelection")
        self.phaseSelection.currentIndexChanged.connect(self.UpdatePhaseGroups)

        self.phaseGroupSelection: QComboBox = self.findChild(
            QComboBox, "phaseGroupSelection")
        self.phaseGroupSelection.currentIndexChanged.connect(
            self.PhaseGroupChanged)

        self.btRefreshPhase: QPushButton = self.findChild(
            QPushButton, "btRefreshPhase")
        self.btRefreshPhase.setIcon(QIcon("./assets/icons/undo.svg"))
        self.btRefreshPhase.clicked.connect(lambda: [
            AutonaTournamentDataProvider.instance.GetTournamentPhases()
        ])

        self.btRefreshPhaseGroup: QPushButton = self.findChild(
            QPushButton, "btRefreshPhaseGroup")
        self.btRefreshPhaseGroup.setIcon(QIcon("./assets/icons/undo.svg"))
        self.btRefreshPhaseGroup.clicked.connect(self.PhaseGroupChanged)
        AutonaHotkeys.signals.refresh_phase_group.connect(self.PhaseGroupChanged)

        self.progressionsIn: QSpinBox = self.findChild(
            QSpinBox, "progressionsIn")
        self.progressionsIn.valueChanged.connect(lambda val: [
            self.bracketView.SetBracket(
                self.bracket,
                progressionsIn=self.progressionsIn.value(),
                progressionsOut=self.progressionsOut.value(),
                winnersOnlyProgressions=self.winnersOnly.isChecked(),
                customSeeding=self.bracket.customSeeding
            ),
            self.bracketView.Update()
        ])
        StateManager.Set("bracket.bracket.progressionsIn", 0)

        self.winnersOnly: QCheckBox = self.findChild(QCheckBox, "winnersOnly")
        self.winnersOnly.toggled.connect(lambda newVal: [
            self.bracketView.SetBracket(
                self.bracket,
                progressionsIn=self.progressionsIn.value(),
                progressionsOut=self.progressionsOut.value(),
                winnersOnlyProgressions=self.winnersOnly.isChecked(),
                customSeeding=self.bracket.customSeeding
            ),
            self.bracketView.Update()
        ])
        StateManager.Set("bracket.bracket.winnersOnly", True)

        self.progressionsOut: QSpinBox = self.findChild(
            QSpinBox, "progressionsOut")
        self.progressionsOut.valueChanged.connect(lambda val: [
            self.bracketView.SetBracket(
                self.bracket,
                progressionsIn=self.progressionsIn.value(),
                progressionsOut=self.progressionsOut.value(),
                winnersOnlyProgressions=self.winnersOnly.isChecked(),
                customSeeding=self.bracket.customSeeding
            ),
            self.bracketView.Update()
        ])
        StateManager.Set("bracket.bracket.progressionsOut", 0)

        self.limitExport: QCheckBox = self.findChild(QCheckBox, "limitExport")
        self.limitExport.stateChanged.connect(lambda newVal: [
            self.bracketView.SetBracket(
                self.bracket,
                progressionsIn=self.progressionsIn.value(),
                progressionsOut=self.progressionsOut.value(),
                winnersOnlyProgressions=self.winnersOnly.isChecked(),
                customSeeding=self.bracket.customSeeding
            ),
            self.bracketView.Update()
        ])
        AutonaHotkeys.signals.limit_export.connect(lambda: self.limitExport.setChecked(not self.limitExport.isChecked()))

        self.limitExportNumber: QSpinBox = self.findChild(
            QSpinBox, "limitExportNumber")
        self.limitExportNumber.valueChanged.connect(lambda val: [
            self.bracketView.SetBracket(
                self.bracket,
                progressionsIn=self.progressionsIn.value(),
                progressionsOut=self.progressionsOut.value(),
                winnersOnlyProgressions=self.winnersOnly.isChecked(),
                customSeeding=self.bracket.customSeeding
            ),
            self.bracketView.Update()
        ])

        # Store the initial populate values; applied during lazy init.
        self._initialSlotCount = 8
        self._initialPlayersPerTeam = 1
        self._initialCharactersPerPlayer = 1
        for sb, val in (
            (self.slotNumber, self._initialSlotCount),
            (self.playerPerTeam, self._initialPlayersPerTeam),
            (self.charNumber, self._initialCharactersPerPlayer),
        ):
            sb.blockSignals(True)
            sb.setValue(val)
            sb.blockSignals(False)

        self.splitter = self.findChild(QSplitter, "splitter")
        self.splitter.setSizes([1, 1])

        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self.SetDefaultsFromAssets
        )

        StateManager.ReleaseSaving()

        AutonaBracketWidget.instance = self

    def showEvent(self, event):
        super().showEvent(event)
        # On first show, defer the heavy BracketView + slot population
        # one event-loop turn so the placeholder label paints first and
        # the tab-switch feels instant. Subsequent shows are no-ops.
        if not self._bracketReady:
            self._bracketReady = True
            QTimer.singleShot(0, self._buildBracketView)

    def _buildBracketView(self):
        """Heavy construction: AutonaBracketView (~1.3s) + PlayerList
        slot/player widgets (~1.1s). Runs once on first tab show."""
        try:
            self.bracketView = AutonaBracketView(
                self.bracket, self.playerList, self)
            # Replace the placeholder with the real view
            self._bracketPlaceholder.hide()
            self._outerLayout.layout().removeWidget(self._bracketPlaceholder)
            self._bracketPlaceholder.deleteLater()
            self._outerLayout.layout().addWidget(self.bracketView)

            self.playerList.signals.DataChanged.connect(
                self.bracketView.Update)
            self.playerList.SetSlotNumber(self._initialSlotCount)
            self.playerList.SetPlayersPerTeam(self._initialPlayersPerTeam)
            self.playerList.SetCharactersPerPlayer(
                self._initialCharactersPerPlayer)
            self.bracketView.Update()
        except Exception:
            logger.error(traceback.format_exc())

    def UpdatePhases(self, phases):
        logger.info("Phases: " + str(phases))
        self.phaseSelection.clear()
        self.phaseSelection.addItem("", {})

        for phase in phases:
            self.phaseSelection.addItem(phase.get("name"), phase)

    def UpdatePhaseGroups(self):
        try:
            selectedGroup = self.phaseSelection.currentData()
            StateManager.Set("bracket.phase", selectedGroup.get("name", ""))
        except:
            StateManager.Set("bracket.phase", "")

        self.phaseGroupSelection.clear()

        if self.phaseSelection.currentData() != None:
            logger.info(
                str(self.phaseSelection.currentData().get("groups", [])))
            for phaseGroup in self.phaseSelection.currentData().get("groups", []):
                self.phaseGroupSelection.addItem(
                    phaseGroup.get("name"), phaseGroup)

                # Let's only allow double elimination for now
                if phaseGroup.get("bracketType") != "DOUBLE_ELIMINATION":
                    itemModel: QStandardItemModel = self.phaseGroupSelection.model()
                    item = itemModel.item(itemModel.rowCount()-1)
                    item.setEnabled(False)

    def PhaseGroupChanged(self):
        try:
            # Do not export phaseGroup name if there's only one phaseGroup
            if len(self.phaseSelection.currentData().get("groups", [])) > 1:
                selectedGroup = self.phaseGroupSelection.currentData()
                StateManager.Set("bracket.phaseGroup",
                                 selectedGroup.get("name"))
            else:
                StateManager.Set("bracket.phaseGroup", "")
        except:
            StateManager.Set("bracket.phaseGroup", "")

        if self.phaseGroupSelection.currentData() != None:
            AutonaTournamentDataProvider.instance.GetTournamentPhaseGroup(
                self.phaseGroupSelection.currentData().get("id"))

    def RebuildBracket(self, playerNumber, seedMap=None, customSeeding=False):
        self.bracket = Bracket(playerNumber, self.progressionsIn.value(
        ), seedMap, self.winnersOnly.isChecked(), progressionsOut=self.progressionsOut.value())

        # bracketView may still be None if the Bracket tab hasn't been
        # opened yet (lazy init). The new bracket model is saved on
        # self.bracket; _buildBracketView() will pick it up on first show.
        if self.bracketView is not None:
            self.bracketView.SetBracket(
                self.bracket,
                progressionsIn=self.progressionsIn.value(),
                progressionsOut=self.progressionsOut.value(),
                winnersOnlyProgressions=self.winnersOnly.isChecked(),
                customSeeding=customSeeding
            )

        if self.progressionsIn.value() > 0:
            for _set in self.bracket.rounds["1"]:
                _set.score[0] = -1
                _set.score[1] = -1
                _set.finished = False

    def UpdatePhaseGroup(self, phaseGroupData):
        StateManager.BlockSaving()
        self.playerList.signals.DataChanged.disconnect()

        try:
            logger.info("Phase Group Data: " + str(phaseGroupData))

            if phaseGroupData.get("progressionsIn", {}) != None:
                self.progressionsIn.setValue(
                    len(phaseGroupData.get("progressionsIn", {})))
            else:
                self.progressionsIn.setValue(0)

            if phaseGroupData.get("progressionsOut", {}) != None:
                self.progressionsOut.setValue(
                    len(phaseGroupData.get("progressionsOut", {})))
            else:
                self.progressionsOut.setValue(0)

            if phaseGroupData.get("winnersOnlyProgressions", False) != None:
                self.winnersOnly.setChecked(
                    phaseGroupData.get("winnersOnlyProgressions", False))
            else:
                self.winnersOnly.setChecked(False)

            self.bracket.customSeeding = phaseGroupData.get(
                "customSeeding", False)

            # Make sure progressions are exported
            QGuiApplication.processEvents()

            self.playerList.LoadFromStandings(phaseGroupData.get("entrants"))

            # Wait for the player list to update
            QGuiApplication.processEvents()

            self.slotNumber.blockSignals(True)
            self.slotNumber.setValue(len(self.playerList.slotWidgets))
            self.slotNumber.blockSignals(False)

            self.playerPerTeam.blockSignals(True)
            self.playerPerTeam.setValue(self.playerList.playersPerTeam)
            self.playerPerTeam.blockSignals(False)

            self.RebuildBracket(
                len(phaseGroupData.get("entrants")),
                phaseGroupData.get("seedMap"),
                phaseGroupData.get("customSeeding", False)
            )

            for r, round in phaseGroupData.get("sets", {}).items():
                for s, _set in enumerate(round):
                    try:
                        score = _set.get("score")
                        if score[0] == None:
                            score[0] = 0
                        if score[1] == None:
                            score[1] = 0

                        roundIndex = str(r)

                        self.bracket.rounds[roundIndex][s].score = score
                        self.bracket.rounds[roundIndex][s].finished = _set.get(
                            "finished")
                    except Exception as e:
                        logger.error(traceback.format_exc())

            QGuiApplication.processEvents()
            self.bracket.UpdateBracket()
            if self.bracketView is not None:
                self.bracketView.Update()
        except:
            logger.error(traceback.format_exc())
        finally:
            StateManager.ReleaseSaving()
            if self.bracketView is not None:
                self.playerList.signals.DataChanged.connect(
                    self.bracketView.Update)

    def SetDefaultsFromAssets(self):
        if StateManager.Get(f'game.defaults'):
            players, characters = StateManager.Get(f'game.defaults.players_per_team', 1), StateManager.Get(f'game.defaults.characters_per_player', 1)
        else:
            players, characters = 1, 1
        self.playerPerTeam.setValue(players)
        self.charNumber.setValue(characters)
