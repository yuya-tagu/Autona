"""Analysis panel — tabular view of every completed/scheduled match in
the tournament (この大会の試合一覧).

Columns (flat, sortable):
    Round | P1 Seed | P1 Name | Score | P2 Name | P2 Seed | UpSet Factor | Losers Run | SeedOver

- UpSet Factor: r(winner) - r(loser), the expected round of elimination of
  each seed.
- Losers Run:  number of LB matches the *winner* of this match won
  consecutively before reaching this match. 0 for UB matches or when the
  winner arrived directly from the UB.
- SeedOver: how many seed positions the loser outperformed expectations
  by (positive = upset victim finishing higher than their seed).

Since ADR-005 S4j the panel only asks and draws: the fetch (every phase
group of the tournament, two at a time), the rows and their drop on a
tournament switch are core's (core/AnalysisService.py), which the headless
shell runs too. The button asks AnalysisService.Refresh(); the status label
and the table follow `analysis` (the fetch's progress) and
AnalysisService.Result() (the rows), drawn once the event loop is back --
the state's notifications run inside StateManager.lock (agent-pitfalls
#17). The rows are structures; the cells' wording ("TBD", "(seed N)", "-")
is the panel's (RowCells). The filter box is a purely local view over the
rows shown.
"""

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .StateManagerBridge import StateManagerBridge
from .core import AnalysisService as analysis
from .core import StateSchema as schema
from .core.AnalysisService import AnalysisService
from .core.DictHelper import deep_clone
from .core.StateManager import StateManager
from . import ui_constants as UI


COLUMNS = [
    "Round",
    "P1 Seed",
    "P1 Name",
    "Score",
    "P2 Name",
    "P2 Seed",
    "UpSet",
    "LR",
    "SeedOver",
]

TEXT_COLS = (0, 2, 4)
NUM_COLS = (1, 3, 5, 6, 7, 8)
NUM_COL_WIDTH = 70
TEXT_COL_MIN_WIDTH = 120


def _SeedCell(side):
    seed = side.get("seed")
    return (str(seed) if seed else "-", seed or 9999)


def _NameCell(side):
    seed = side.get("seed")
    name = side.get("name") or (f"(seed {seed})" if seed else "TBD")
    return (name, name.lower())


def RowCells(row):
    """A core row (AnalysisService.BracketRows) as the table's nine cells,
    (text, sort key) each: an unknown name is its seed's "(seed N)", or
    "TBD"; an unknown seed "-" (sorted last); an unfinished set's score "-"
    (sorted as 0); no upset factor / seed over, and no losers run, an empty
    cell (sorted as 0)."""
    p1, p2 = row.get("p1") or {}, row.get("p2") or {}
    score = row.get("score")
    upset = row.get("upset")
    losersRun = row.get("losersRun") or 0
    seedOver = row.get("seedOver")
    return [
        (row.get("round") or "", tuple(row.get("roundOrder") or ())),
        _SeedCell(p1),
        _NameCell(p1),
        (f"{score[0]}-{score[1]}" if score is not None else "-",
         sum(score) if score is not None else 0),
        _NameCell(p2),
        _SeedCell(p2),
        (str(upset) if upset is not None else "", upset or 0),
        (str(losersRun) if losersRun > 0 else "", losersRun),
        (str(seedOver) if seedOver is not None else "", seedOver or 0),
    ]


class NumericItem(QTableWidgetItem):
    """Sorts by the numeric value stashed in UserRole rather than the
    displayed text — critical for negative values (e.g. "-2") and for
    multi-digit numbers ("10" < "2" lexicographically otherwise)."""

    def __lt__(self, other):
        try:
            a = self.data(Qt.ItemDataRole.UserRole)
            b = other.data(Qt.ItemDataRole.UserRole)
            if a is None:
                a = 0
            if b is None:
                b = 0
            return float(a) < float(b)
        except (TypeError, ValueError):
            return super().__lt__(other)


# NOTE: Formerly a QDockWidget (the "ANLYSE" tab). Demoted to a plain
# QWidget panel so it can be embedded as a sub-tab inside the STATS dock
# ("この大会の試合一覧").
class AutonaAnalysisWidget(QWidget):
    instance: "AutonaAnalysisWidget" = None

    @staticmethod
    def _summaryLabelStyle() -> str:
        return f"color: {UI.palette()['text_muted']};"

    def __init__(self, *args):
        super().__init__(*args)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(6, 6, 6, 6)
        self.layout().setSpacing(6)

        # ── Top bar: [fetch button] [status] ──(stretch)── [search] ──
        # Fetch and filter are independent actions — fetch always refreshes
        # from Start.gg regardless of the search term, filter is applied
        # locally on top of whatever data is currently in the table.
        topBar = QHBoxLayout()
        topBar.setSpacing(8)

        self.btRefresh = QPushButton(
            QApplication.translate("app", "Startggからデータを取得"))
        self.btRefresh.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btRefresh.clicked.connect(self._onRefreshClicked)
        topBar.addWidget(self.btRefresh)

        self.summaryLabel = QLabel("")
        self.summaryLabel.setStyleSheet(self._summaryLabelStyle())
        topBar.addWidget(self.summaryLabel)

        topBar.addStretch(1)

        self.filterEdit = QLineEdit()
        self.filterEdit.setPlaceholderText(
            QApplication.translate("app", "選手名 / ラウンドで検索"))
        self.filterEdit.textChanged.connect(self._applyFilter)
        self.filterEdit.setFixedWidth(240)
        self.filterEdit.setClearButtonEnabled(True)
        topBar.addWidget(self.filterEdit)

        self.layout().addLayout(topBar)

        # ── Table ──
        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)

        header: QHeaderView = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(NUM_COL_WIDTH)

        for col in NUM_COLS:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, NUM_COL_WIDTH)

        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, TEXT_COL_MIN_WIDTH)

        for col in (2, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            self.table.setColumnWidth(col, TEXT_COL_MIN_WIDTH)

        self.layout().addWidget(self.table)

        # ── What is drawn ──
        # `analysis` as last seen (a copy), and the revision of the rows on
        # the table (AnalysisService.Result: None for none) and how many.
        self._seen = deep_clone(StateManager.Get(schema.ANALYSIS_ROOT, None))
        self._shownRevision = None
        self._shownCount = 0
        self._renderQueued = False

        # Through the bridge (bound methods: dropped with this widget).
        bridge = StateManagerBridge.instance
        bridge.state_updated.connect(self._OnState)
        bridge.state_big_change.connect(self._OnState)

        AutonaAnalysisWidget.instance = self
        self._Render()

    # ── State ───────────────────────────────────────────────────────────

    def _OnState(self, *_payload):
        # On the GUI thread, inside StateManager.lock when the write was
        # made there (agent-pitfalls #17): reads one key, and draws once the
        # event loop is back, out of the lock.
        value = StateManager.Get(schema.ANALYSIS_ROOT, None)
        if value == self._seen:
            return
        self._seen = deep_clone(value)
        if self._renderQueued:
            return
        self._renderQueued = True
        QTimer.singleShot(0, self, self._Render)

    def _Render(self):
        self._renderQueued = False
        progress = AnalysisService.Progress()
        service = AnalysisService.instance

        # The rows of the newest fetch that made them: kept while a fetch
        # runs and when one fails, gone with the tournament. Copied out of
        # the service only when they are other rows than the table's.
        revision = service.ResultRevision() if service is not None else None
        if revision != self._shownRevision:
            result = service.Result() if service is not None else {
                "rows": [], "revision": None}
            self._shownRevision = result["revision"]
            self._shownCount = len(result["rows"])
            self._fillTable([RowCells(row) for row in result["rows"]])
            self._applyFilter()

        status = progress["status"]
        self.btRefresh.setEnabled(status != analysis.RUNNING)
        if status == analysis.RUNNING:
            if progress["total"] == 0:
                text = QApplication.translate("app", "フェーズ情報を取得中...")
            else:
                text = QApplication.translate("app", "取得中 {0}/{1}").format(
                    progress["done"], progress["total"])
        elif status == analysis.DONE:
            text = QApplication.translate("app", "{0} 試合").format(
                self._shownCount)
            if progress["failed"]:
                text += " " + QApplication.translate(
                    "app", "({0} 件取得失敗)").format(progress["failed"])
        elif status == analysis.ERROR:
            text = QApplication.translate("app", "取得に失敗しました")
        else:
            text = QApplication.translate(
                "app", "「Startggからデータを取得」を押してください")
        self.summaryLabel.setText(text)

    # ── Refresh button ──────────────────────────────────────────────────

    def _onRefreshClicked(self):
        # Button is explicitly labeled "Startggからデータを取得" — it always
        # triggers a server-side fetch. The filter box is purely a local
        # view over whatever data the table currently holds and never
        # influences fetch behavior.
        service = AnalysisService.instance
        try:
            answer = service.Refresh() if service is not None \
                else analysis.NO_TOURNAMENT
        except Exception:
            # Nothing was started (the fetcher raised): told here, as a
            # failed fetch, until the next change.
            logger.exception("Analysis: the fetch could not be started")
            self.summaryLabel.setText(
                QApplication.translate("app", "取得に失敗しました"))
            return
        if answer == analysis.NO_TOURNAMENT:
            # Nothing is published for it: told here, until the next change.
            self.summaryLabel.setText(
                QApplication.translate("app", "大会が選択されていません"))

    # ── Table + filter plumbing ─────────────────────────────────────────

    def _fillTable(self, rows):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, (text, sort_key) in enumerate(row):
                if c in NUM_COLS:
                    item = NumericItem(text)
                else:
                    item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, sort_key)
                if c in NUM_COLS:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.table.resizeRowsToContents()

    def _applyFilter(self):
        needle = self.filterEdit.text().strip().lower()
        if not needle:
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
            return
        match_cols = (0, 2, 4)
        for r in range(self.table.rowCount()):
            hit = False
            for c in match_cols:
                item = self.table.item(r, c)
                if item and needle in item.text().lower():
                    hit = True
                    break
            self.table.setRowHidden(r, not hit)
