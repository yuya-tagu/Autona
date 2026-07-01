from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
from .Helpers.AutonaDirHelper import AutonaResolve
from .StateManager import StateManager
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaBracketView import AutonaBracketView
from .AutonaPlayerList import AutonaPlayerList
from .AutonaBracket import Bracket
from .AutonaHotkeys import AutonaHotkeys
from .Workers import Worker
from . import ui_constants as UI
import traceback
from loguru import logger


class AutonaBracketWidgetSignals(QObject):
    UpdateData = Signal(object)


class AutonaBracketWidget(QDockWidget):
    instance: "AutonaBracketWidget" = None

    def __init__(self, *args):
        StateManager.BlockSaving()
        super().__init__(*args)

        uic.loadUi(AutonaResolve("src/layout/AutonaBracket.ui"), self)
        self.widget().layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)

        StateManager.Set("bracket", {})

        AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.connect(
            self.UpdatePhases)
        AutonaTournamentDataProvider.instance.signals.tournament_phasegroup_updated.connect(
            self.UpdatePhaseGroup)

        self.signals = AutonaBracketWidgetSignals()

        # Empty bracket until a phase group is loaded from Start.gg.
        self.bracket = Bracket()

        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

        self.mainLayout = self.findChild(QWidget, "bracket")
        self.mainLayout.setLayout(QVBoxLayout())

        outerLayout: QWidget = self.findChild(QWidget, "bracket")
        self.playerList = AutonaPlayerList(base="bracket.players")
        listContainer: QWidget = self.findChild(QWidget, "listContainer")

        # Slot / players-per-team / characters controls. With the Start.gg
        # driven bracket these only resize the player list side panel — they
        # no longer rebuild the bracket topology.
        row = QWidget()
        row.setLayout(QHBoxLayout())
        listContainer.layout().addWidget(row)

        col = QWidget()
        col.setLayout(QVBoxLayout())
        self.slotNumber = QSpinBox()
        self.slotNumber.setMinimum(2)
        self.slotNumber.setMaximum(128)
        col.layout().addWidget(QLabel(QApplication.translate("app", "Number of slots")))
        col.layout().addWidget(self.slotNumber)
        self.slotNumber.valueChanged.connect(self.playerList.SetSlotNumber)
        row.layout().addWidget(col)

        col = QWidget()
        col.setLayout(QVBoxLayout())
        self.playerPerTeam = QSpinBox()
        col.layout().addWidget(QLabel(QApplication.translate("app", "Players per slot")))
        col.layout().addWidget(self.playerPerTeam)
        self.playerPerTeam.valueChanged.connect(
            self.playerList.SetPlayersPerTeam)
        row.layout().addWidget(col)

        col = QWidget()
        col.setLayout(QVBoxLayout())
        self.charNumber = QSpinBox()
        col.layout().addWidget(QLabel(QApplication.translate("app", "Characters per player")))
        col.layout().addWidget(self.charNumber)
        self.charNumber.valueChanged.connect(
            self.playerList.SetCharactersPerPlayer)
        row.layout().addWidget(col)

        listContainer.layout().addWidget(self.playerList)

        # Deferred heavy init — BracketView creation and slot widget population
        # are ~2.5s combined, so we leave a placeholder until the tab is shown.
        self._outerLayout = outerLayout
        self._bracketReady = False
        self.bracketView = None

        self._bracketPlaceholder = QLabel(
            QApplication.translate("app", "Loading bracket..."))
        self._bracketPlaceholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bracketPlaceholder.setStyleSheet(
            f"color: {UI.palette()['text_secondary']}; "
            "font-size: 11pt; padding: 40px;")
        outerLayout.layout().addWidget(self._bracketPlaceholder)

        self.phaseSelection: QComboBox = self.findChild(
            QComboBox, "phaseSelection")
        self.phaseSelection.currentIndexChanged.connect(self.UpdatePhaseGroups)

        self.phaseGroupSelection: QComboBox = self.findChild(
            QComboBox, "phaseGroupSelection")
        self.phaseGroupSelection.currentIndexChanged.connect(
            self.PhaseGroupChanged)

        # The old per-combobox refresh buttons are replaced by a single
        # "Update bracket info" button (added below the grid). Hide the
        # original buttons rather than removing them from the .ui to keep
        # findChild lookups stable if any hotkey / external code references
        # them.
        self.btRefreshPhase: QPushButton = self.findChild(
            QPushButton, "btRefreshPhase")
        if self.btRefreshPhase is not None:
            self.btRefreshPhase.hide()

        self.btRefreshPhaseGroup: QPushButton = self.findChild(
            QPushButton, "btRefreshPhaseGroup")
        if self.btRefreshPhaseGroup is not None:
            self.btRefreshPhaseGroup.hide()

        # Unified refresh action — re-fetches the currently selected phase
        # group so matches / scores reflect Start.gg's latest state.
        self.btUpdateBracket = QPushButton(
            QApplication.translate("app", "Update bracket info"))
        self.btUpdateBracket.setIcon(QIcon("./assets/icons/undo.svg"))
        self.btUpdateBracket.clicked.connect(self.PhaseGroupChanged)
        # Insert between the top control grid and the splitter (index 1 of
        # the dock contents' vertical layout: 0 = gridLayout, 2 = splitter).
        dockContents = self.findChild(QWidget, "dockWidgetContents")
        if dockContents is not None and dockContents.layout() is not None:
            dockContents.layout().insertWidget(1, self.btUpdateBracket)

        AutonaHotkeys.signals.refresh_phase_group.connect(self.PhaseGroupChanged)

        # progressionsIn / progressionsOut / winnersOnly used to participate in
        # bracket topology generation. The Start.gg-driven bracket no longer
        # needs them, but they remain in the .ui file and in state output for
        # overlay compatibility. Hide the UI affordances.
        self._hideLegacyControls()
        StateManager.Set("bracket.bracket.progressionsIn", 0)
        StateManager.Set("bracket.bracket.progressionsOut", 0)
        StateManager.Set("bracket.bracket.winnersOnly", True)

        self.limitExport: QCheckBox = self.findChild(QCheckBox, "limitExport")
        self.limitExport.stateChanged.connect(self._onLimitExportChanged)
        AutonaHotkeys.signals.limit_export.connect(
            lambda: self.limitExport.setChecked(not self.limitExport.isChecked()))

        self.limitExportNumber: QSpinBox = self.findChild(
            QSpinBox, "limitExportNumber")
        self.limitExportNumber.valueChanged.connect(self._onLimitExportChanged)

        # Re-entrancy guard: UpdatePhaseGroup handles the
        # tournament_phasegroup_updated signal. If Qt ever pumps the event
        # loop during the handler (e.g. via a nested processEvents), the
        # same signal can fire again and re-enter this method. That leads
        # to a Qt/C++ access violation. This flag keeps the second call
        # from running concurrently.
        self._updatingPhaseGroup = False

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
        # Hide the left-hand player list panel — it's redundant once the
        # bracket is Start.gg-driven (entrants flow into bracket.players.slot.*
        # automatically via LoadFromStandings). The AutonaPlayerList instance
        # and its slot/players/characters spinboxes stay alive as parents of
        # the hidden scrollArea so the state-writing path keeps working.
        scrollArea = self.findChild(QScrollArea, "scrollArea")
        if scrollArea is not None:
            scrollArea.hide()
        self.splitter.setSizes([0, 1])

        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self.SetDefaultsFromAssets
        )

        StateManager.ReleaseSaving()

        AutonaBracketWidget.instance = self

    def _hideLegacyControls(self):
        for name in ("progressionsIn", "progressionsOut", "winnersOnly",
                     "label_3", "label_4"):
            w = self.findChild(QWidget, name)
            if w is not None:
                w.hide()

    def _onLimitExportChanged(self, *args):
        if self.bracketView is not None:
            self.bracketView.SetBracket(self.bracket)
            self.bracketView.Update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._bracketReady:
            self._bracketReady = True
            QTimer.singleShot(0, self._buildBracketView)

    def _buildBracketView(self):
        try:
            self.bracketView = AutonaBracketView(
                self.bracket, self.playerList, self)
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
        except Exception:
            StateManager.Set("bracket.phase", "")

        self.phaseGroupSelection.clear()

        if self.phaseSelection.currentData() is not None:
            logger.info(
                str(self.phaseSelection.currentData().get("groups", [])))
            for phaseGroup in self.phaseSelection.currentData().get("groups", []):
                self.phaseGroupSelection.addItem(
                    phaseGroup.get("name"), phaseGroup)

                # Only double elimination is supported
                if phaseGroup.get("bracketType") != "DOUBLE_ELIMINATION":
                    itemModel: QStandardItemModel = self.phaseGroupSelection.model()
                    item = itemModel.item(itemModel.rowCount()-1)
                    item.setEnabled(False)

    def PhaseGroupChanged(self):
        with StateManager.SaveBlock():
            try:
                if len(self.phaseSelection.currentData().get("groups", [])) > 1:
                    selectedGroup = self.phaseGroupSelection.currentData()
                    StateManager.Set("bracket.phaseGroup",
                                     selectedGroup.get("name"))
                else:
                    StateManager.Set("bracket.phaseGroup", "")
            except Exception:
                StateManager.Set("bracket.phaseGroup", "")

        if self.phaseGroupSelection.currentData() is not None:
            # Surface a "取得中" message in the bracket area while the
            # Start.gg fetch is in flight. UpdatePhaseGroup overwrites
            # this once the response arrives.
            if self.bracketView is not None:
                self.bracketView.ShowLoading()
            AutonaTournamentDataProvider.instance.GetTournamentPhaseGroup(
                self.phaseGroupSelection.currentData().get("id"))

    def UpdatePhaseGroup(self, phaseGroupData):
        from .Helpers.AutonaStatusManager import AutonaStatusManager
        # Skip if we're already inside a handler — Qt can queue multiple
        # emits of tournament_phasegroup_updated (refresh button, websocket
        # action, initial load) and re-entering this path has crashed the
        # app with an access violation.
        if self._updatingPhaseGroup:
            logger.warning(
                "UpdatePhaseGroup: skipping re-entrant call")
            return

        self._updatingPhaseGroup = True
        status_token = AutonaStatusManager.push("ブラケットを構築中...")
        StateManager.BlockSaving()
        try:
            self.playerList.signals.DataChanged.disconnect()
        except (TypeError, RuntimeError):
            pass

        # GUI-thread phase: list refresh + control updates (cheap).
        try:
            entrants = phaseGroupData.get("entrants") or []
            setsByRound = phaseGroupData.get("sets") or {}
            logger.info(
                f"Phase Group: {len(entrants)} entrants, {len(setsByRound)} rounds")

            self.playerList.LoadFromStandings(entrants)

            self.slotNumber.blockSignals(True)
            self.slotNumber.setValue(len(self.playerList.slotWidgets))
            self.slotNumber.blockSignals(False)

            self.playerPerTeam.blockSignals(True)
            self.playerPerTeam.setValue(self.playerList.playersPerTeam)
            self.playerPerTeam.blockSignals(False)
        except Exception:
            logger.error(traceback.format_exc())
            self._finalizeBracketUpdate(None, status_token)
            return

        # Worker phase: Bracket topology build is O(rounds × sets) and
        # blocks the UI for ~hundreds of ms on large pools. Run it off-GUI
        # so the spinner / status bar stay responsive.
        worker = Worker(self._buildBracketAsync, phaseGroupData=phaseGroupData)
        worker.signals.result.connect(
            lambda bracket, t=status_token: self._finalizeBracketUpdate(bracket, t))
        worker.signals.error.connect(
            lambda _err, t=status_token: self._finalizeBracketUpdate(None, t))
        AutonaTournamentDataProvider.instance.threadPool.start(worker)

    def _buildBracketAsync(self, phaseGroupData, progress_callback=None, cancel_event=None):
        # Pure data work — no Qt widgets touched.
        return Bracket(phaseGroupData)

    def _finalizeBracketUpdate(self, bracket, status_token):
        from .Helpers.AutonaStatusManager import AutonaStatusManager
        try:
            if bracket is not None:
                self.bracket = bracket
                if self.bracketView is not None:
                    self.bracketView.SetBracket(self.bracket)
                    self.bracketView.Update()
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            StateManager.ReleaseSaving()
            if self.bracketView is not None:
                self.playerList.signals.DataChanged.connect(
                    self.bracketView.Update)
            self._updatingPhaseGroup = False
            AutonaStatusManager.pop(status_token)

    def SetDefaultsFromAssets(self):
        if StateManager.Get('game.defaults'):
            players, characters = (
                StateManager.Get('game.defaults.players_per_team', 1),
                StateManager.Get('game.defaults.characters_per_player', 1),
            )
        else:
            players, characters = 1, 1
        self.playerPerTeam.setValue(players)
        self.charNumber.setValue(characters)
