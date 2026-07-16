import traceback
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src import ui_constants as UI


class AutonaSelectSetWindow(QDialog):
    # Hidden column carrying the raw set dict (UserRole); keep in sync with
    # the appendRow order in SetsUpdated.
    DATA_COLUMN = 7

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)

        self.setWindowTitle(
            QApplication.translate("app", "Select a set"))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.proxyModel = QSortFilterProxyModel()
        self.proxyModel.setFilterKeyColumn(-1)
        self.proxyModel.setFilterCaseSensitivity(
            Qt.CaseSensitivity.CaseInsensitive)

        def filterList(text):
            self.proxyModel.setFilterFixedString(text)

        searchBar = QLineEdit()
        searchBar.setPlaceholderText(QApplication.translate("app", "Filter..."))
        layout.addWidget(searchBar)
        searchBar.textEdited.connect(filterList)

        # Phase selector: fetch only the chosen phase's sets instead of
        # paging through the whole event. Pool (phase group) narrowing was
        # dropped on purpose — phases are the operator-meaningful unit.
        self.phaseRow = QWidget()
        phaseRowLayout = QHBoxLayout()
        phaseRowLayout.setContentsMargins(0, 0, 0, 0)
        self.phaseRow.setLayout(phaseRowLayout)
        phaseRowLayout.addWidget(
            QLabel(QApplication.translate("app", "Phase")))
        self.phaseCombo = QComboBox()
        self.phaseCombo.addItem(
            QApplication.translate("app", "Select a phase..."), None)
        # `activated` (not currentIndexChanged) so re-picking the same phase
        # re-fetches it.
        self.phaseCombo.activated.connect(self._onPhaseChosen)
        phaseRowLayout.addWidget(self.phaseCombo, 1)
        layout.addWidget(self.phaseRow)
        self.phaseRow.setVisible(False)
        self.phases = []

        options = QHBoxLayout()

        self.showFinished = QCheckBox(
            QApplication.translate("app", "Show completed sets"))
        options.addWidget(self.showFinished)
        self.showFinished.clicked.connect(lambda check: self.LoadSets())
        self.showCompletePairs = QCheckBox(
            QApplication.translate("app", "Show complete pairs"))
        options.addWidget(self.showCompletePairs)
        self.showCompletePairs.clicked.connect(lambda check: self.LoadSets())

        layout.layout().addLayout(options)

        self.startggSetSelectionItemList = QTableView()
        self.startggSetSelectionItemList.doubleClicked.connect(
            lambda x: self.LoadSelectedSet())
        self.startggSetSelectionItemList.installEventFilter(self)
        layout.addWidget(self.startggSetSelectionItemList)
        self.startggSetSelectionItemList.setSortingEnabled(True)
        self.startggSetSelectionItemList.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        self.startggSetSelectionItemList.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.startggSetSelectionItemList.setModel(self.proxyModel)
        self.startggSetSelectionItemList.setColumnHidden(
            self.DATA_COLUMN, True)
        self.startggSetSelectionItemList.horizontalHeader(
        ).setSectionResizeMode(QHeaderView.Stretch)
        self.startggSetSelectionItemList.resizeColumnsToContents()

        bottomRow = QHBoxLayout()
        layout.addLayout(bottomRow)

        btOk = QPushButton("OK")
        bottomRow.addWidget(btOk)
        btOk.clicked.connect(
            lambda x: self.LoadSelectedSet()
        )

        self.btTrackStation = QPushButton(
            QApplication.translate("app", "Track sets from a stream or station"))
        bottomRow.addWidget(self.btTrackStation)
        self.btTrackStation.clicked.connect(self._openStationSelection)

        # Footer: fetch progress (red bar filling left to right, one step
        # per fetched page) above the status text.
        self.progressBar = QProgressBar()
        self.progressBar.setTextVisible(False)
        self.progressBar.setFixedHeight(6)
        self.progressBar.setStyleSheet(
            "QProgressBar {{ background: {bg}; border: none;"
            " border-radius: 3px; }}"
            "QProgressBar::chunk {{ background-color: {chunk};"
            " border-radius: 3px; }}".format(
                bg=UI.palette()["bg_tertiary"],
                chunk=UI.palette()["brand"]))
        self.progressBar.setVisible(False)
        layout.addWidget(self.progressBar)

        self.labelStatus = QLabel()
        layout.addWidget(self.labelStatus)

        self.resize(1200, 500)

        qr = self.frameGeometry()
        cp = QApplication.primaryScreen().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())

        AutonaTournamentDataProvider.instance.signals.sets_data_updated.connect(
            self.SetsUpdated)
        AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.connect(
            self._onPhasesUpdated)

        self.model = QStandardItemModel()

    def eventFilter(self, obj, event):
        if obj is self.startggSetSelectionItemList and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.LoadSelectedSet()
        return super().eventFilter(obj, event)

    def _onPhasesUpdated(self, phases):
        """Rebuild the phase selector from the tournament's phases (fetched
        once on tournament load)."""
        self.phases = phases or []
        self.phaseCombo.blockSignals(True)
        self.phaseCombo.clear()
        self.phaseCombo.addItem(
            QApplication.translate("app", "Select a phase..."), None)
        allGroupIds = [group.get("id")
                       for phase in self.phases
                       for group in (phase.get("groups") or [])]
        self.phaseCombo.addItem(
            QApplication.translate("app", "All phases"), allGroupIds)
        for phase in self.phases:
            self.phaseCombo.addItem(phase.get("name") or "", phase)
        self.phaseCombo.setCurrentIndex(0)
        self.phaseCombo.blockSignals(False)
        self.phaseRow.setVisible(bool(self.phases))

    def _selectedGroupIds(self):
        """Group ids to fetch, from the chosen phase (or the all-phases
        list). None = no restriction (fetch the whole event)."""
        phaseData = self.phaseCombo.currentData()
        if isinstance(phaseData, dict):
            return [group.get("id")
                    for group in (phaseData.get("groups") or [])]
        if isinstance(phaseData, list):
            return phaseData
        return None

    def _onPhaseChosen(self, index):
        if index <= 0:
            return
        self._FetchSets()

    def LoadSets(self):
        """Entry point when the window opens. Events with known phases wait
        for a phase choice instead of paging through every set of the event."""
        if not self.phases:
            # The phases fetch may have raced this window or failed on
            # tournament load; re-request so the selector can populate
            # (the tournament_phases_updated signal repopulates the combo)
            # and fall back to the unfiltered fetch meanwhile.
            AutonaTournamentDataProvider.instance.GetTournamentPhases()
            self.phaseRow.setVisible(False)
            self._FetchSets()
            return
        self.phaseRow.setVisible(True)
        if self.phaseCombo.currentIndex() <= 0:
            # A single-phase event has nothing to choose — load it directly.
            if len(self.phases) == 1:
                self.phaseCombo.setCurrentIndex(2)
                self._FetchSets()
                return
            self.model.clear()
            self.progressBar.setVisible(False)
            self.labelStatus.setText(QApplication.translate(
                "app", "Select a phase to load sets"))
            return
        self._FetchSets()

    def _FetchSets(self):
        phaseGroupIds = self._selectedGroupIds() if self.phaseRow.isVisibleTo(
            self) else None
        self.proxyModel.setSourceModel(QStandardItemModel())
        AutonaTournamentDataProvider.instance.LoadSets(
            showFinished=self.showFinished.isChecked(),
            phaseGroupIds=phaseGroupIds)
        self.model.clear()
        # Busy (indeterminate) until the first page reports the page count.
        self.progressBar.setRange(0, 0)
        self.progressBar.setVisible(True)
        self.labelStatus.setText(
            QApplication.translate("app", "Fetching sets..."))

    def SetsUpdated(self, data):
        if data == None:
            return

        if data["progress"] >= data["totalPages"]:
            self.labelStatus.setText("")
            self.progressBar.setVisible(False)
        else:
            self.labelStatus.setText(
                f"Fetching sets...({data['progress']}/{data['totalPages']})")
            self.progressBar.setRange(0, data["totalPages"])
            self.progressBar.setValue(data["progress"])

        horizontal_labels = [
            QApplication.translate("app", "Stream"),
            QApplication.translate("app", "Station"),
            QApplication.translate("app", "Phase"),
            QApplication.translate("app", "Pool"),
            QApplication.translate("app", "Match"),
            QApplication.translate("app", "Player {0}").format(1),
            QApplication.translate("app", "Player {0}").format(2),
        ]
        self.model.setHorizontalHeaderLabels(horizontal_labels)

        sets = data["sets"]

        if sets is not None:
            for s in sets:
                dataItem = QStandardItem(str(s.get("id")))
                dataItem.setData(s, Qt.ItemDataRole.UserRole)

                if self.showCompletePairs.isChecked():
                    if s.get("p1_name") == "" or s.get("p2_name") == "":
                        continue
                player_names = [s.get("p1_name"), s.get("p2_name")]

                try:
                    # For doubles, use team name + entrants names
                    if len(s.get("entrants", [[]])[0]) > 1:
                        for t, team in enumerate(s.get("entrants")):
                            pnames = []
                            for p, player in enumerate(s.get("entrants")[t]):
                                pnames.append(player.get("gamerTag"))
                            player_names[t] += " "+QApplication.translate(
                                "punctuation", "(")+", ".join(pnames)+QApplication.translate("punctuation", ")")
                except Exception as e:
                    logger.error(traceback.format_exc())

                self.model.appendRow([
                    QStandardItem(s.get("stream", "")),
                    QStandardItem(str(s.get("station", "")) if s.get(
                        "station", "") != None else ""),
                    QStandardItem(s.get("tournament_phase", "")),
                    QStandardItem(s.get("tournament_pool", "")),
                    QStandardItem(s["round_name"]),
                    QStandardItem(player_names[0]),
                    QStandardItem(player_names[1]),
                    dataItem
                ])

        self.proxyModel.setSourceModel(self.model)
        self.startggSetSelectionItemList.setColumnHidden(
            self.DATA_COLUMN, True)
        self.startggSetSelectionItemList.resizeColumnsToContents()
        self.startggSetSelectionItemList.horizontalHeader(
        ).setSectionResizeMode(QHeaderView.Stretch)
        QApplication.processEvents()
        self.resize(self.width(), self.height())

    def LoadSelectedSet(self):
        row = 0

        if len(self.startggSetSelectionItemList.selectionModel().selectedRows()) > 0:
            row = self.startggSetSelectionItemList.selectionModel().selectedRows()[
                0].row()
        setId = self.startggSetSelectionItemList.model().index(
            row, self.DATA_COLUMN).data(Qt.ItemDataRole.UserRole)
        self.close()

        if setId:
            setId["auto_update"] = "set"
            # Defer past the double-click signal delivery: the set-load
            # cascade (CommandClearAll -> Clear -> LoadStates -> setModel)
            # is too heavy to run re-entrantly inside the table view's
            # signal emission (agent-pitfalls #9; 2026-07-12 crash).
            scoreboard = self.parent()
            QTimer.singleShot(
                0, lambda: scoreboard.signals.NewSetSelected.emit(setId))

        if AutonaTournamentDataProvider.instance.setLoadingWorker:
            # If there was a previous set loading worker,
            # block its signals
            AutonaTournamentDataProvider.instance.setLoadingWorker.cancel()
            AutonaTournamentDataProvider.instance.setLoadingWorker.signals.blockSignals(
                True)

    def _openStationSelection(self):
        self.close()
        self.parent().signals.StationSelection.emit()
