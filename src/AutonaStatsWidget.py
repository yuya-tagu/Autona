from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from .StateManager import StateManager
from . import ui_constants as UI
from loguru import logger

ROW_HEIGHT = 40


class AutonaStatsWidget(QDockWidget):
    """Statistics panel with tabbed view: Head-to-Head, Last Sets,
    Tournament History, plus the absorbed ANLYSE ("この大会の試合一覧")
    panel passed in by Autona.py."""

    def __init__(self, *args, analysisPanel=None):
        super().__init__(*args)

        self.setWindowTitle(QApplication.translate("app", "Statistics"))
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.setFloating(False)

        container = QWidget()
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(container)
        container.setLayout(QVBoxLayout())
        container.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        container.layout().setSpacing(UI.SPACE_XS)

        # Top bar
        topBar = QHBoxLayout()
        refreshBtn = QPushButton(QApplication.translate("app", "統計情報を取得する"))
        refreshBtn.clicked.connect(self._refresh)
        topBar.addWidget(refreshBtn)
        self.upsetLabel = QLabel()
        self.upsetLabel.setStyleSheet("font-weight: bold;")
        topBar.addWidget(self.upsetLabel, 1)
        container.layout().addLayout(topBar)

        # Tabs
        self.tabs = QTabWidget()
        container.layout().addWidget(self.tabs)

        # --- Tab 1: Head-to-Head (Recent Sets) ---
        h2hInner = QWidget()
        h2hInner.setLayout(QVBoxLayout())
        h2hInner.layout().setContentsMargins(0, 4, 0, 0)
        self.h2hRecordLabel = QLabel()
        self.h2hRecordLabel.setStyleSheet("font-size: 13px; font-weight: bold; margin-bottom: 4px;")
        h2hInner.layout().addWidget(self.h2hRecordLabel)
        self.recentSetsTable = self._makeTable([
            QApplication.translate("app", "Date"),
            QApplication.translate("app", "Event"),
            QApplication.translate("app", "Round"),
            QApplication.translate("app", "Score"),
            QApplication.translate("app", "Winner"),
        ])
        h2hInner.layout().addWidget(self.recentSetsTable)
        h2hInner.layout().addStretch()
        self.tabs.addTab(
            self._wrapInScroll(h2hInner),
            QApplication.translate("app", "Head-to-Head (Recent Sets)"),
        )

        # --- Tab 2: Last Sets ---
        lastSetsInner = QWidget()
        lastSetsInner.setLayout(QVBoxLayout())
        lastSetsInner.layout().setContentsMargins(0, 4, 0, 0)

        self.p1LastLabel = QLabel()
        self.p1LastLabel.setStyleSheet("font-weight: bold; margin-top: 4px;")
        lastSetsInner.layout().addWidget(self.p1LastLabel)
        self.lastSetsP1Table = self._makeTable([
            QApplication.translate("app", "Phase"),
            QApplication.translate("app", "Round"),
            QApplication.translate("app", "Opponent"),
            QApplication.translate("app", "Score"),
        ])
        lastSetsInner.layout().addWidget(self.lastSetsP1Table)

        self.p2LastLabel = QLabel()
        self.p2LastLabel.setStyleSheet("font-weight: bold; margin-top: 8px;")
        lastSetsInner.layout().addWidget(self.p2LastLabel)
        self.lastSetsP2Table = self._makeTable([
            QApplication.translate("app", "Phase"),
            QApplication.translate("app", "Round"),
            QApplication.translate("app", "Opponent"),
            QApplication.translate("app", "Score"),
        ])
        lastSetsInner.layout().addWidget(self.lastSetsP2Table)
        lastSetsInner.layout().addStretch()
        self.tabs.addTab(
            self._wrapInScroll(lastSetsInner),
            QApplication.translate("app", "Last Sets"),
        )

        # --- Tab 3: Tournament History ---
        histInner = QWidget()
        histInner.setLayout(QVBoxLayout())
        histInner.layout().setContentsMargins(0, 4, 0, 0)

        self.p1HistLabel = QLabel()
        self.p1HistLabel.setStyleSheet("font-weight: bold; margin-top: 4px;")
        histInner.layout().addWidget(self.p1HistLabel)
        self.historyP1Table = self._makeTable([
            QApplication.translate("app", "Tournament"),
            QApplication.translate("app", "Event"),
            QApplication.translate("app", "Placement"),
            QApplication.translate("app", "Entrants"),
            QApplication.translate("app", "Date"),
        ])
        histInner.layout().addWidget(self.historyP1Table)

        self.p2HistLabel = QLabel()
        self.p2HistLabel.setStyleSheet("font-weight: bold; margin-top: 8px;")
        histInner.layout().addWidget(self.p2HistLabel)
        self.historyP2Table = self._makeTable([
            QApplication.translate("app", "Tournament"),
            QApplication.translate("app", "Event"),
            QApplication.translate("app", "Placement"),
            QApplication.translate("app", "Entrants"),
            QApplication.translate("app", "Date"),
        ])
        histInner.layout().addWidget(self.historyP2Table)
        histInner.layout().addStretch()
        self.tabs.addTab(
            self._wrapInScroll(histInner),
            QApplication.translate("app", "Tournament History"),
        )

        # --- Tab 4: この大会の試合一覧 (absorbed ANLYSE panel) ---
        # Added as a direct tab page (it manages its own scrolling/table).
        if analysisPanel is not None:
            self.tabs.addTab(
                analysisPanel,
                QApplication.translate("app", "この大会の試合一覧"),
            )

        # Connect signals
        StateManager.signals.state_big_change.connect(self._onStateChanged)
        StateManager.signals.state_updated.connect(self._onStateUpdated)
        # Scoreboard edits are held (not broadcast) until the 反映 button
        # commits them, so they never arrive as state_updated deltas. This
        # panel renders from the live state via Get(), so refresh on the
        # coalesced held-write notification instead.
        StateManager.signals.held_state_updated.connect(
            self._onHeldStateUpdated)

    def _wrapInScroll(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def _makeTable(self, headers):
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setWordWrap(True)

        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return table

    def _resizeTableToContents(self, table):
        """Resize table height to fit all rows without scrollbar."""
        if table.rowCount() == 0:
            table.setFixedHeight(table.horizontalHeader().height() + 4)
            return
        total = table.horizontalHeader().height() + 4
        for r in range(table.rowCount()):
            total += ROW_HEIGHT
        table.setFixedHeight(total)

    def _refresh(self):
        """Trigger stats recalculation via AutonaStatsUtil signals."""
        from .AutonaScoreboardManager import AutonaScoreboardManager
        sb = AutonaScoreboardManager.instance.GetScoreboard(1)
        if hasattr(sb, "stats") and sb.stats:
            sb.stats.signals.RecentSetsSignal.emit()
            sb.stats.signals.LastSetsP1Signal.emit()
            sb.stats.signals.LastSetsP2Signal.emit()
            sb.stats.signals.PlayerHistoryStandingsP1Signal.emit()
            sb.stats.signals.PlayerHistoryStandingsP2Signal.emit()
            sb.stats.signals.UpsetFactorCalculation.emit()

    def _onStateChanged(self):
        try:
            self._updateDisplay()
        except Exception as e:
            logger.debug(f"Stats widget update error: {e}")

    def _onStateUpdated(self, delta):
        """Called on incremental state updates (small deltas)."""
        try:
            delta_str = str(delta)
            if any(k in delta_str for k in ("recent_sets", "last_sets", "history_sets", "upset_factor", "team")):
                self._updateDisplay()
        except Exception as e:
            logger.debug(f"Stats widget update error: {e}")

    def _onHeldStateUpdated(self, prefix):
        """Called when live-but-unpublished data under a held prefix changed."""
        if prefix.startswith("score."):
            self._onStateChanged()

    def _updateDisplay(self):
        n = 1

        p1_name = StateManager.Get(f"score.{n}.team.1.player.1.name", "P1") or "P1"
        p2_name = StateManager.Get(f"score.{n}.team.2.player.1.name", "P2") or "P2"

        # Upset factor
        uf = StateManager.Get(f"score.{n}.upset_factor", 0)
        self.upsetLabel.setText(
            QApplication.translate("app", "Upset Factor") + f": {uf}    |    {p1_name} vs {p2_name}"
        )

        # Labels
        self.p1LastLabel.setText(p1_name)
        self.p2LastLabel.setText(p2_name)
        self.p1HistLabel.setText(p1_name)
        self.p2HistLabel.setText(p2_name)

        # Recent sets (H2H)
        recent = StateManager.Get(f"score.{n}.recent_sets.sets", [])
        self._fillRecentSetsTable(recent or [])

        # Last sets
        last1 = StateManager.Get(f"score.{n}.last_sets.1", {})
        self._fillLastSetsTable(self.lastSetsP1Table, last1 or {})
        last2 = StateManager.Get(f"score.{n}.last_sets.2", {})
        self._fillLastSetsTable(self.lastSetsP2Table, last2 or {})

        # History
        hist1 = StateManager.Get(f"score.{n}.history_sets.1", {})
        self._fillHistoryTable(self.historyP1Table, hist1 or {})
        hist2 = StateManager.Get(f"score.{n}.history_sets.2", {})
        self._fillHistoryTable(self.historyP2Table, hist2 or {})

    def _fillRecentSetsTable(self, sets):
        from datetime import datetime
        if isinstance(sets, dict):
            sets = list(sets.values())
        self.recentSetsTable.setRowCount(len(sets))
        n = 1
        p1_name = StateManager.Get(f"score.{n}.team.1.player.1.name", "P1") or "P1"
        p2_name = StateManager.Get(f"score.{n}.team.2.player.1.name", "P2") or "P2"

        # Win counts
        p1_wins = sum(1 for s in sets if s.get("winner") == 0)
        p2_wins = sum(1 for s in sets if s.get("winner") == 1)
        total = p1_wins + p2_wins
        p1_rate = f"{p1_wins / total * 100:.0f}%" if total > 0 else "-"
        p2_rate = f"{p2_wins / total * 100:.0f}%" if total > 0 else "-"
        self.h2hRecordLabel.setText(
            f"{p1_name}  {p1_wins}W ({p1_rate})    -    {p2_wins}W ({p2_rate})  {p2_name}    [{total} sets]"
        )

        for i, s in enumerate(sets):
            # Date
            ts = s.get("timestamp")
            if ts:
                try:
                    date_str = datetime.fromtimestamp(ts).strftime("%Y/%m/%d")
                except Exception:
                    date_str = ""
            else:
                date_str = ""
            self.recentSetsTable.setItem(i, 0, QTableWidgetItem(date_str))

            # Event
            event_str = s.get("tournament", "") or ""
            event_name = s.get("event", "")
            if event_name:
                event_str += f"\n{event_name}"
            self.recentSetsTable.setItem(i, 1, QTableWidgetItem(event_str))

            # Round
            round_str = s.get("round", "") or s.get("phase_name", "")
            self.recentSetsTable.setItem(i, 2, QTableWidgetItem(str(round_str)))

            # Score
            score = s.get("score", [0, 0])
            self.recentSetsTable.setItem(i, 3, QTableWidgetItem(f"{score[0]} - {score[1]}"))

            # Winner
            winner = p1_name if s.get("winner") == 0 else p2_name
            self.recentSetsTable.setItem(i, 4, QTableWidgetItem(str(winner)))

            self.recentSetsTable.setRowHeight(i, ROW_HEIGHT)
        self._resizeTableToContents(self.recentSetsTable)

    def _fillLastSetsTable(self, table, data):
        keys = sorted(data.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
        table.setRowCount(len(keys))
        for i, k in enumerate(keys):
            s = data[k]
            table.setItem(i, 0, QTableWidgetItem(str(s.get("phase_name", ""))))
            table.setItem(i, 1, QTableWidgetItem(str(s.get("round_name", ""))))
            opp = s.get("oponent_name", "")
            if s.get("oponent_team"):
                opp = f"{s.get('oponent_team')}\n{opp}"
            table.setItem(i, 2, QTableWidgetItem(str(opp)))
            score = f"{s.get('player_score', 0)} - {s.get('oponent_score', 0)}"
            table.setItem(i, 3, QTableWidgetItem(score))
            table.setRowHeight(i, ROW_HEIGHT)
        self._resizeTableToContents(table)

    def _fillHistoryTable(self, table, data):
        keys = sorted(data.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
        table.setRowCount(len(keys))
        for i, k in enumerate(keys):
            s = data[k]
            table.setItem(i, 0, QTableWidgetItem(str(s.get("tournament_name", ""))))
            table.setItem(i, 1, QTableWidgetItem(str(s.get("event_name", ""))))
            table.setItem(i, 2, QTableWidgetItem(str(s.get("placement", ""))))
            table.setItem(i, 3, QTableWidgetItem(str(s.get("entrants", ""))))
            date = f"{s.get('event_date_year', '')}/{s.get('event_date_month', '')}/{s.get('event_date_day', '')}"
            table.setItem(i, 4, QTableWidgetItem(date))
            table.setRowHeight(i, ROW_HEIGHT)
        self._resizeTableToContents(table)
