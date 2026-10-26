"""Analysis dock — tabular view of every completed/scheduled match in
the tournament.

Columns (flat, sortable):
    Round | P1 Seed | P1 Name | Score | P2 Name | P2 Seed | UpSet Factor | Losers Run | SeedOver

- UpSet Factor: same metric the Bracket dock computes (r(winner) - r(loser)).
- Losers Run:  number of LB matches the *winner* of this match won
  consecutively before reaching this match. 0 for UB matches or when the
  winner arrived directly from the UB.
- SeedOver: how many seed positions the loser outperformed expectations
  by (positive = upset victim finishing higher than their seed).

Single data path: the refresh button sequentially fetches ALL phase
groups from Start.gg via a lightweight GQL query and rebuilds the table
from the combined result. (The old auto-rebuild from
StateManager["bracket.bracket.rounds"] was removed together with the
Bracket dock — the Top 8 panel no longer maintains that state.) The
filter box is a purely local view over the fetched data.
"""

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .core.Bracket import Bracket, BracketSet
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


def _round_index(p):
    """Double-elimination placement → expected round-of-elimination index.
    Mirrors AutonaBracketView._roundIndex."""
    if p is None or p < 1:
        return 0
    if p == 1:
        return 0
    k = (p - 1).bit_length()
    if 4 * p <= 3 * (1 << k):
        return 2 * (k - 1)
    return 2 * (k - 1) + 1


def _upset_factor(winner_seed, loser_seed):
    if winner_seed is None or loser_seed is None:
        return 0
    if winner_seed <= 0 or loser_seed <= 0:
        return 0
    return _round_index(winner_seed) - _round_index(loser_seed)


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

        # ── Multi-PG fetch state ──
        self._phases = []               # from tournament_phases_updated
        self._fetchInProgress = False
        self._fetchQueue = []           # list of (phase_name, pg_id, pg_label) waiting to dispatch
        self._fetchTotal = 0
        self._fetchResults = {}         # pg_id -> {"phase_name","pg_label","data"}
        self._fetchInFlight = 0         # workers currently running
        self._fetchDone = 0             # completed (success or failure)
        # True once a multi-PG fetch has populated the table. Auto-triggered
        # state rebuilds are suppressed in that mode so the fetched
        # snapshot isn't clobbered by a single-PG refresh from the Bracket
        # dock. Reset when the tournament changes.
        self._multiPgActive = False
        # Set when the user clicks 更新 before phases have arrived — we
        # trigger GetTournamentPhases() and resume the refresh once
        # _onPhasesUpdated fires.
        self._refreshQueuedOnPhases = False

        # ── Data reload triggers ──
        # Fetch is the only data path (no auto-rebuild from state), so the
        # table stays empty until the user presses the fetch button.
        signals = AutonaTournamentDataProvider.instance.signals
        signals.tournament_phases_updated.connect(self._onPhasesUpdated)
        signals.tournament_changed.connect(self._onTournamentChanged)

        AutonaAnalysisWidget.instance = self
        self._showEmptyPrompt()

    # ── Signal handlers ─────────────────────────────────────────

    def _onPhasesUpdated(self, phases):
        self._phases = list(phases or [])
        # If the user clicked 更新 while phases were still loading, we
        # queued the refresh. Resume it now.
        if self._refreshQueuedOnPhases and self._phases:
            self._refreshQueuedOnPhases = False
            QTimer.singleShot(0, self._onRefreshClicked)

    def _onTournamentChanged(self):
        # New tournament → discard any cached multi-PG snapshot.
        self._multiPgActive = False
        self._fetchResults = {}
        self._refreshQueuedOnPhases = False
        self._showEmptyPrompt()

    def _showEmptyPrompt(self):
        self._fillTable([])
        self.summaryLabel.setText(QApplication.translate(
            "app", "「Startggからデータを取得」を押してください"))

    # ── Refresh button ──────────────────────────────────────────

    def _onRefreshClicked(self):
        # Button is explicitly labeled "Startggからデータを取得" — it always
        # triggers a server-side fetch. The filter box is purely a local
        # view over whatever data the table currently holds and never
        # influences fetch behavior.
        if self._fetchInProgress:
            return

        provider = AutonaTournamentDataProvider.instance.provider
        if provider is None:
            self.summaryLabel.setText(
                QApplication.translate("app", "大会が選択されていません"))
            return

        pg_entries = []
        for phase in self._phases:
            for pg in (phase.get("groups") or []):
                pg_id = pg.get("id")
                if pg_id is None:
                    continue
                pg_entries.append((
                    phase.get("name") or "",
                    pg_id,
                    pg.get("name") or "",
                ))

        if not pg_entries:
            # Phases haven't arrived yet (common right after tournament
            # load). Trigger a phases fetch and queue the refresh so it
            # resumes as soon as _onPhasesUpdated fires.
            logger.info(
                "Analysis refresh: phases not yet cached, fetching...")
            self.summaryLabel.setText(
                QApplication.translate("app", "フェーズ情報を取得中..."))
            self._refreshQueuedOnPhases = True
            AutonaTournamentDataProvider.instance.GetTournamentPhases()
            return

        self._fetchInProgress = True
        self._fetchQueue = list(pg_entries)
        self._fetchTotal = len(pg_entries)
        self._fetchResults = {}
        self._fetchInFlight = 0
        self._fetchDone = 0
        self.btRefresh.setEnabled(False)
        self._dispatchMore()

    # Cap concurrent Start.gg requests. Each fetch hits the public GraphQL
    # API; running too many in parallel risks rate limiting and saturates
    # the shared Qt thread pool used elsewhere (asset loads, set fetches).
    _PARALLEL_FETCHES = 4

    def _dispatchMore(self):
        # Fill in-flight slots up to the parallelism cap. Returns
        # immediately when the queue is empty.
        while self._fetchQueue and self._fetchInFlight < self._PARALLEL_FETCHES:
            phase_name, pg_id, pg_label = self._fetchQueue.pop(0)
            self._fetchInFlight += 1
            AutonaTournamentDataProvider.instance.GetAnalysisPhaseGroup(
                pg_id,
                lambda data, pid=pg_id, pn=phase_name, pl=pg_label:
                    self._onPgFetched(pid, pn, pl, data))
        self.summaryLabel.setText(
            QApplication.translate("app", "取得中 {0}/{1}").format(
                self._fetchDone, self._fetchTotal))

    def _onPgFetched(self, pg_id, phase_name, pg_label, data):
        self._fetchResults[pg_id] = {
            "phase_name": phase_name,
            "pg_label": pg_label,
            "data": data or {},
        }
        self._fetchInFlight -= 1
        self._fetchDone += 1
        if not self._fetchQueue and self._fetchInFlight == 0:
            self._onAllFetchesDone()
            return
        # Defer next dispatch via the event loop so progress paints
        # between hops and the call stack stays shallow.
        QTimer.singleShot(0, self._dispatchMore)

    def _onAllFetchesDone(self):
        self._fetchInProgress = False
        self.btRefresh.setEnabled(True)
        successful = sum(1 for v in self._fetchResults.values()
                         if v.get("data"))
        if successful == 0:
            self.summaryLabel.setText(
                QApplication.translate("app", "取得に失敗しました"))
            return
        self._multiPgActive = True
        self._rebuildFromMultiPg()

    # ── Data plumbing (multi-PG, fetched) ───────────────────────

    def _rebuildFromMultiPg(self):
        multi = len(self._fetchResults) > 1
        all_rows = []
        empty_pgs = 0
        for pg_id, entry in self._fetchResults.items():
            data = entry.get("data") or {}
            if not data or not data.get("sets"):
                empty_pgs += 1
                continue
            try:
                # The round names its rows are labelled with (GetRoundName)
                # are the locale's, handed in (core.Bracket is Qt-free).
                bracket = Bracket(
                    data, matchNames=AutonaLocaleHelper.matchNames)
            except Exception:
                logger.opt(exception=True).warning(
                    f"Analysis: Bracket build failed for PG {pg_id}")
                empty_pgs += 1
                continue

            prefix = ""
            if multi:
                phase_name = entry.get("phase_name") or ""
                pg_label = entry.get("pg_label") or ""
                if phase_name and pg_label and pg_label not in phase_name:
                    prefix = f"{phase_name} / {pg_label} · "
                else:
                    prefix = f"{phase_name or pg_label} · "

            all_rows.extend(self._collectRowsFromBracket(
                bracket, data.get("entrants") or [], prefix, pg_id))

        self._fillTable(all_rows)
        summary = QApplication.translate(
            "app", "{0} 試合").format(len(all_rows))
        if empty_pgs:
            summary += " " + QApplication.translate(
                "app", "({0} 件取得失敗)").format(empty_pgs)
        self.summaryLabel.setText(summary)
        self._applyFilter()

    def _collectRowsFromBracket(self, bracket: Bracket, entrants, round_prefix, pg_id):
        def entrant_name(seed):
            if not isinstance(seed, int) or seed <= 0 or seed > len(entrants):
                return ""
            ent = entrants[seed - 1] or {}
            if ent.get("name"):
                return ent["name"]
            players = ent.get("players") or []
            if players:
                return (players[0] or {}).get("gamerTag") or ""
            return ""

        # Reverse map: (id(next_set), next_slot) → source BracketSet. Used
        # to walk backwards from a completed LB set to the chain of
        # previous LB wins its winner went through.
        win_source = {}
        for bsList in bracket.rounds.values():
            for bs in bsList:
                if bs.winNext is not None:
                    win_source[(id(bs.winNext), bs.winNextSlot)] = bs

        placements = {}
        pos_rounds = sorted(
            (int(r) for r in bracket.rounds.keys() if int(r) > 0))
        neg_rounds = sorted(
            (int(r) for r in bracket.rounds.keys() if int(r) < 0),
            key=abs, reverse=True)

        for rnum in pos_rounds:
            for bs in bracket.rounds.get(str(rnum), []):
                if bs.winNext is None and bs.loseNext is None:
                    placements[id(bs)] = 2

        next_start = 3
        for rnum in neg_rounds:
            sets = bracket.rounds.get(str(rnum), [])
            real = [bs for bs in sets
                    if not bs.isByeSet
                    and bs.playerIds[0] != BracketSet.BYE
                    and bs.playerIds[1] != BracketSet.BYE]
            if not real:
                continue
            for bs in real:
                placements[id(bs)] = next_start
            next_start += len(real)

        def losers_run(bs):
            if bs.pos[0] >= 0 or not bs.finished:
                return 0
            scores = bs.score or [0, 0]
            if scores[0] == scores[1]:
                return 0
            winner_slot = 0 if scores[0] > scores[1] else 1
            winner_id = bs.playerIds[winner_slot]
            run = 0
            current = bs
            cur_slot = winner_slot
            visited = set()
            while id(current) not in visited:
                visited.add(id(current))
                src = win_source.get((id(current), cur_slot))
                if src is None or not src.finished:
                    break
                s = src.score or [0, 0]
                if s[0] == s[1]:
                    break
                src_winner_slot = 0 if s[0] > s[1] else 1
                if src.playerIds[src_winner_slot] != winner_id:
                    break
                if src.pos[0] < 0:
                    run += 1
                    current = src
                    cur_slot = src_winner_slot
                else:
                    break
            return run

        rows = []
        for rk, bsList in bracket.rounds.items():
            try:
                round_num = int(rk)
            except (TypeError, ValueError):
                continue
            round_name = bracket.GetRoundName(rk) or str(round_num)
            for set_idx, bs in enumerate(bsList):
                ids = bs.playerIds or [-1, -1]
                if any(i == BracketSet.BYE for i in ids):
                    continue
                if ids[0] == BracketSet.PENDING and ids[1] == BracketSet.PENDING:
                    continue

                p1_seed = ids[0] if isinstance(ids[0], int) and ids[0] > 0 else None
                p2_seed = ids[1] if isinstance(ids[1], int) and ids[1] > 0 else None
                p1_name = entrant_name(ids[0]) or (
                    f"(seed {p1_seed})" if p1_seed else "TBD")
                p2_name = entrant_name(ids[1]) or (
                    f"(seed {p2_seed})" if p2_seed else "TBD")
                scores = bs.score or [0, 0]
                completed = bool(bs.finished)
                score_text = f"{scores[0]}-{scores[1]}" if completed else "-"

                uf_text, uf_sort = "", 0
                if completed and scores[0] != scores[1]:
                    winner_seed = p1_seed if scores[0] > scores[1] else p2_seed
                    loser_seed = p2_seed if scores[0] > scores[1] else p1_seed
                    uf = _upset_factor(winner_seed, loser_seed)
                    uf_text = str(uf)
                    uf_sort = uf

                lr = losers_run(bs)
                lr_text = str(lr) if lr > 0 else ""

                so_text, so_sort = "", 0
                placement = placements.get(id(bs))
                if completed and scores[0] != scores[1] and placement is not None:
                    loser_seed = p2_seed if scores[0] > scores[1] else p1_seed
                    if loser_seed and loser_seed > 0 and loser_seed > placement:
                        so_val = loser_seed - placement
                        so_text = str(so_val)
                        so_sort = so_val

                display_round = f"{round_prefix}{round_name}" if round_prefix else str(round_name)
                rows.append([
                    (display_round, (str(pg_id), round_num, set_idx)),
                    (str(p1_seed) if p1_seed else "-", p1_seed or 9999),
                    (p1_name, p1_name.lower()),
                    (score_text, sum(scores)),
                    (p2_name, p2_name.lower()),
                    (str(p2_seed) if p2_seed else "-", p2_seed or 9999),
                    (uf_text, uf_sort),
                    (lr_text, lr),
                    (so_text, so_sort),
                ])
        return rows

    # ── Table + filter plumbing ─────────────────────────────────

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
