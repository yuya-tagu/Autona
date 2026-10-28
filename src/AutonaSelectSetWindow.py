import traceback
from datetime import datetime

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src import ui_constants as UI


class AutonaSelectSetWindow(QDialog):
    # Hidden column carrying the raw set dict (UserRole); keep in sync with
    # the appendRow order in _RenderSets.
    DATA_COLUMN = 7

    # Last fetched set list, shared by every scoreboard's window and kept for
    # the lifetime of the process (closing Autona is expected to start clean).
    # Opening the window renders this instead of hitting Start.gg again; the
    # query it came from is stored alongside it so a tournament change can
    # never show rows that belong to another event.
    # {"url", "showFinished", "phaseIndex", "sets", "fetchedAt"}
    _cache = None
    # (column, Qt.SortOrder) of the sets table, likewise remembered until exit.
    _sortState = None

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)

        self.setWindowTitle(
            QApplication.translate("app", "Select a set"))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        # A fetch is only ever started from this window, so a flag is enough
        # to tell our own provider signals from another window's.
        self._fetchPending = False
        self._pendingQuery = None
        self._suppressSortSave = False

        layout = QVBoxLayout()
        self.setLayout(layout)

        # One model for the window's lifetime: rows are cleared and refilled
        # in place, never swapped for a fresh model (agent-pitfalls #13).
        self.model = QStandardItemModel()
        self.proxyModel = QSortFilterProxyModel()
        self.proxyModel.setFilterKeyColumn(-1)
        self.proxyModel.setFilterCaseSensitivity(
            Qt.CaseSensitivity.CaseInsensitive)
        self.proxyModel.setSourceModel(self.model)

        def filterList(text):
            self.proxyModel.setFilterFixedString(text)

        searchBar = QLineEdit()
        searchBar.setPlaceholderText(QApplication.translate("app", "Filter..."))
        layout.addWidget(searchBar)
        searchBar.textEdited.connect(filterList)

        # Phase selector: fetch only the chosen phase's sets instead of
        # paging through the whole event. Pool (phase group) narrowing was
        # dropped on purpose — phases are the operator-meaningful unit.
        # The fetch button sits next to it: opening this window never hits
        # the network, the operator decides when to refresh.
        self.phaseRow = QWidget()
        phaseRowLayout = QHBoxLayout()
        phaseRowLayout.setContentsMargins(0, 0, 0, 0)
        self.phaseRow.setLayout(phaseRowLayout)
        self.phaseLabel = QLabel(QApplication.translate("app", "Phase"))
        phaseRowLayout.addWidget(self.phaseLabel)
        self.phaseCombo = QComboBox()
        self.phaseCombo.addItem(
            QApplication.translate("app", "Select a phase..."), None)
        # `activated` (not currentIndexChanged) so re-picking the same phase
        # re-fetches it, and so restoring the cached phase stays silent.
        self.phaseCombo.activated.connect(self._onPhaseChosen)
        phaseRowLayout.addWidget(self.phaseCombo, 1)
        self.btFetchSets = QPushButton(
            QApplication.translate("app", "最新の試合情報を取得"))
        self.btFetchSets.setIcon(QIcon(UI.icon_path("download.svg")))
        self.btFetchSets.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btFetchSets.clicked.connect(lambda x=None: self._FetchSets())
        phaseRowLayout.addWidget(self.btFetchSets)
        layout.addWidget(self.phaseRow)
        self._SetPhaseSelectorVisible(False)
        self.phases = []

        options = QHBoxLayout()

        # Changes the Start.gg query -> refetch; the pairs filter is applied
        # locally, so it only re-renders what is already cached.
        self.showFinished = QCheckBox(
            QApplication.translate("app", "Show completed sets"))
        options.addWidget(self.showFinished)
        self.showFinished.clicked.connect(lambda check: self._FetchSets())
        self.showCompletePairs = QCheckBox(
            QApplication.translate("app", "Show complete pairs"))
        options.addWidget(self.showCompletePairs)
        self.showCompletePairs.clicked.connect(
            lambda check: self._RenderSets(self._CachedSets()))

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
        self.startggSetSelectionItemList.horizontalHeader(
        ).sortIndicatorChanged.connect(self._onSortIndicatorChanged)

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

        # Progress reporting and the fetched rows arrive separately:
        # sets_data_updated fires per page, get_sets_finished once, with the
        # full list.
        AutonaTournamentDataProvider.instance.signals.sets_data_updated.connect(
            self._OnFetchProgress)
        AutonaTournamentDataProvider.instance.signals.get_sets_finished.connect(
            self._OnSetsFetched)
        AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.connect(
            self._onPhasesUpdated)

    def eventFilter(self, obj, event):
        if obj is self.startggSetSelectionItemList and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.LoadSelectedSet()
        return super().eventFilter(obj, event)

    def _TournamentUrl(self):
        provider = AutonaTournamentDataProvider.instance.provider
        return getattr(provider, "url", None) if provider else None

    def _CacheForCurrentTournament(self):
        """The cached list, but only while it still belongs to the tournament
        that is loaded right now."""
        cache = AutonaSelectSetWindow._cache
        if cache and cache.get("url") == self._TournamentUrl():
            return cache
        return None

    def _CachedSets(self):
        cache = self._CacheForCurrentTournament()
        return cache["sets"] if cache else []

    def _SetPhaseSelectorVisible(self, visible):
        """Hide the phase picker (not the whole row) when the event has no
        phases — the fetch button stays reachable either way."""
        self.phaseLabel.setVisible(visible)
        self.phaseCombo.setVisible(visible)

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
        self.phaseCombo.setCurrentIndex(self._PreferredPhaseIndex())
        self.phaseCombo.blockSignals(False)
        self._SetPhaseSelectorVisible(bool(self.phases))

    def _PreferredPhaseIndex(self):
        """Phase to preselect after a rebuild: the one the cached list was
        fetched with, otherwise the only phase of a single-phase event
        (index 0 = placeholder, 1 = all phases, 2+ = the phases themselves)."""
        cache = self._CacheForCurrentTournament()
        if cache and 0 <= cache["phaseIndex"] < self.phaseCombo.count():
            return cache["phaseIndex"]
        if len(self.phases) == 1:
            return 2
        return 0

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

    def PrepareForDisplay(self):
        """Entry point when the window opens. Deliberately does not fetch
        sets: the previously fetched list is restored from the cache and
        refreshing is the operator's call (btFetchSets)."""
        if not self.phases:
            # The phases fetch may have raced this window or failed on
            # tournament load; re-request it so the selector can populate
            # (the tournament_phases_updated signal repopulates the combo).
            # Phases only — this never pulls sets.
            AutonaTournamentDataProvider.instance.GetTournamentPhases()

        if self._fetchPending:
            # A fetch started from a previous open is still running; leave its
            # progress on screen instead of overwriting it with stale rows.
            return

        cache = self._CacheForCurrentTournament()
        if cache:
            self.showFinished.setChecked(cache["showFinished"])
            if 0 <= cache["phaseIndex"] < self.phaseCombo.count():
                self.phaseCombo.setCurrentIndex(cache["phaseIndex"])
            self._RenderSets(cache["sets"])
            self._ShowCacheStatus(cache)
        else:
            self._RenderSets([])
            self.labelStatus.setText(QApplication.translate(
                "app", "「最新の試合情報を取得」を押すと一覧を読み込みます"))

    def _ShowCacheStatus(self, cache):
        self.labelStatus.setText(QApplication.translate(
            "app", "{0}件（{1} 取得）").format(
                len(cache["sets"]), cache["fetchedAt"]))

    def _FetchSets(self):
        if AutonaTournamentDataProvider.instance.provider is None:
            # Reachable through the external open-set API, which opens this
            # window without checking for a tournament. LoadSets would return
            # silently and leave the button stuck on "fetching".
            self.labelStatus.setText(QApplication.translate(
                "app", "大会が選択されていません"))
            return

        if self.phases and self.phaseCombo.currentIndex() <= 0:
            # Fetching every phase of a large event is exactly what the phase
            # selector exists to avoid — "All phases" is there for that.
            self.labelStatus.setText(QApplication.translate(
                "app", "Select a phase to load sets"))
            return

        phaseGroupIds = self._selectedGroupIds() if self.phases else None
        # What the result will be cached as — captured now, because the
        # selection may have moved on by the time the fetch lands.
        self._pendingQuery = {
            "url": self._TournamentUrl(),
            "showFinished": self.showFinished.isChecked(),
            "phaseIndex": self.phaseCombo.currentIndex(),
        }
        # The current rows stay on screen while the refresh runs; they are
        # replaced once the new list arrives.
        self._SetFetching(True)
        AutonaTournamentDataProvider.instance.LoadSets(
            showFinished=self._pendingQuery["showFinished"],
            phaseGroupIds=phaseGroupIds)

    def _SetFetching(self, fetching):
        self._fetchPending = fetching
        self.btFetchSets.setEnabled(not fetching)
        if fetching:
            # Busy (indeterminate) until the first page reports the page count.
            self.progressBar.setRange(0, 0)
            self.progressBar.setVisible(True)
            self.labelStatus.setText(
                QApplication.translate("app", "Fetching sets..."))
        else:
            self.progressBar.setVisible(False)

    def _OnFetchProgress(self, data):
        """Per-page progress of a fetch this window started. The rows come
        from get_sets_finished, so the payload's `sets` is ignored here."""
        if not self._fetchPending or not data:
            return
        totalPages = data.get("totalPages") or 0
        if totalPages <= 0:
            return
        progress = data.get("progress") or 0
        self.progressBar.setRange(0, totalPages)
        self.progressBar.setValue(progress)
        self.labelStatus.setText(
            QApplication.translate("app", "Fetching sets...")
            + f"({progress}/{totalPages})")

    def _OnSetsFetched(self, sets):
        if not self._fetchPending:
            return
        query = self._pendingQuery or {}
        self._pendingQuery = None
        self._SetFetching(False)

        AutonaSelectSetWindow._cache = {
            "url": query.get("url"),
            "showFinished": query.get("showFinished", False),
            "phaseIndex": query.get("phaseIndex", 0),
            "sets": sets or [],
            "fetchedAt": datetime.now().strftime("%H:%M"),
        }
        cache = AutonaSelectSetWindow._cache
        self._RenderSets(cache["sets"])
        self._ShowCacheStatus(cache)

    def _RenderSets(self, sets):
        """Refill the table from a set list (cached or freshly fetched)."""
        # Clearing/refilling flaps the sort indicator; keep the operator's
        # own choice from being overwritten by those intermediate states.
        self._suppressSortSave = True
        try:
            self.model.clear()
            self.model.setHorizontalHeaderLabels([
                QApplication.translate("app", "Stream"),
                QApplication.translate("app", "Station"),
                QApplication.translate("app", "Phase"),
                QApplication.translate("app", "Pool"),
                QApplication.translate("app", "Match"),
                QApplication.translate("app", "Player {0}").format(1),
                QApplication.translate("app", "Player {0}").format(2),
            ])

            for s in (sets or []):
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

            self.startggSetSelectionItemList.setColumnHidden(
                self.DATA_COLUMN, True)
            self.startggSetSelectionItemList.resizeColumnsToContents()
            self.startggSetSelectionItemList.horizontalHeader(
            ).setSectionResizeMode(QHeaderView.Stretch)
            self._ApplySortState()
        finally:
            self._suppressSortSave = False

    def _onSortIndicatorChanged(self, section, order):
        if self._suppressSortSave or section < 0:
            return
        AutonaSelectSetWindow._sortState = (section, order)

    def _ApplySortState(self):
        state = AutonaSelectSetWindow._sortState
        if not state:
            return
        section, order = state
        if section < self.model.columnCount():
            self.startggSetSelectionItemList.sortByColumn(section, order)

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
            # cascade (NewSetSelected: the board's focused field finished,
            # the core clear of the players and the score, then each
            # entrant's core load (PlayerService.ApplyData, ADR-005 S3e-3)
            # and the cards rendering the slots, their region lists rebuilt
            # for a new country) is too heavy to run re-entrantly inside the
            # table view's signal emission (agent-pitfalls #9; 2026-07-12
            # crash, which was a setModel() swap back then).
            scoreboard = self.parent()
            QTimer.singleShot(
                0, lambda: scoreboard.signals.NewSetSelected.emit(setId))

        # A set list still loading is no longer wanted: stop it and drop
        # its answers (ADR-005 S3f-2; it was the adapter's last set loading
        # Worker, cancelled with its signals blocked).
        AutonaTournamentDataProvider.instance.CancelSetLoad()
        if self._fetchPending:
            # That load was ours: its result will never arrive, so drop the
            # pending state instead of leaving the button disabled.
            self._pendingQuery = None
            self._SetFetching(False)

    def _openStationSelection(self):
        self.close()
        self.parent().signals.StationSelection.emit()
