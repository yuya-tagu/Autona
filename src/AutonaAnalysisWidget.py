"""Analysis dock — tabular view of every completed/scheduled match in
the currently loaded phase group.

Columns (flat, sortable):
    Round | P1 Seed | P1 Name | Score | P2 Name | P2 Seed | UpSet Factor | Losers Run

- UpSet Factor: same metric the Bracket dock computes (r(winner) - r(loser)).
- Losers Run:  number of LB matches the *winner* of this match won
  consecutively before reaching this match. 0 for UB matches or when the
  winner arrived directly from the UB.
"""

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from loguru import logger

from .StateManager import StateManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider


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

# Columns 0, 2, 4 are text (Round / P1 Name / P2 Name). The rest are
# numeric / short codes that look better in a fixed-width column.
TEXT_COLS = (0, 2, 4)
NUM_COLS = (1, 3, 5, 6, 7, 8)
NUM_COL_WIDTH = 70   # px — tight column for numbers
TEXT_COL_MIN_WIDTH = 120  # px — matches the header-label visual weight


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


def _compute_placements(rounds):
    """Return a dict mapping (round_num, set_idx) → placement (the
    final tournament rank of the loser of that set).

    Losers of LB matches are eliminated at that point, so their rank is
    determined by how many rounds of LB remain below them. GF / GF
    Reset losers place 2nd. Returns nothing for sets whose loser is not
    yet eliminated (e.g. UB matches that feed into LB)."""
    result = {}

    positive = []
    negative = []
    for k in rounds.keys():
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        (positive if n > 0 else negative).append(n)

    # GF / GF Reset losers take 2nd. Structural: positive rounds whose
    # sets have neither nextWin nor nextLose (terminal matches).
    for round_num in positive:
        sets = (rounds[str(round_num)] or {}).get("sets") or {}
        for set_idx_str, s in sets.items():
            try:
                set_idx = int(set_idx_str)
            except (TypeError, ValueError):
                continue
            if s.get("nextWin") is None and s.get("nextLose") is None:
                result[(round_num, set_idx)] = 2

    # LB — walk from the innermost round (LF, largest abs) outward.
    # Each LB round's losers all share a placement range; we store the
    # start of that range (e.g. 5 for a 2-set LQF that covers 5th/6th).
    sorted_neg = sorted(negative, key=abs, reverse=True)
    next_start = 3
    for round_num in sorted_neg:
        sets = (rounds[str(round_num)] or {}).get("sets") or {}
        # Drop bye-only sets — they don't eliminate anyone.
        real = [(int(k), s) for k, s in sets.items()
                if _is_real_set(s)]
        if not real:
            continue
        for set_idx, _s in real:
            result[(round_num, set_idx)] = next_start
        next_start += len(real)

    return result


def _is_real_set(s):
    ids = (s or {}).get("playerId") or [-1, -1]
    return not any(i == -1 for i in ids)


class AutonaAnalysisWidget(QDockWidget):
    instance: "AutonaAnalysisWidget" = None

    def __init__(self, *args):
        super().__init__(*args)
        self.setWindowTitle(QApplication.translate("app", "Analysis"))
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

        root = QWidget()
        root.setLayout(QVBoxLayout())
        root.layout().setContentsMargins(6, 6, 6, 6)
        root.layout().setSpacing(6)
        self.setWidget(root)

        # ── Top bar: filter input + refresh ──
        topBar = QHBoxLayout()
        topBar.setSpacing(8)

        self.filterEdit = QLineEdit()
        self.filterEdit.setPlaceholderText(
            QApplication.translate("app", "選手名 / ラウンドで絞り込み"))
        self.filterEdit.textChanged.connect(self._applyFilter)
        topBar.addWidget(self.filterEdit, 1)

        self.btRefresh = QPushButton(
            QApplication.translate("app", "更新"))
        self.btRefresh.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btRefresh.clicked.connect(self._reload)
        topBar.addWidget(self.btRefresh)

        self.summaryLabel = QLabel("")
        self.summaryLabel.setStyleSheet("color: rgba(255,255,255,0.55);")
        topBar.addWidget(self.summaryLabel)

        root.layout().addLayout(topBar)

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
        # Global minimum — keeps even text columns from collapsing
        # narrower than the numeric columns.
        header.setMinimumSectionSize(NUM_COL_WIDTH)

        # Numeric columns: fixed narrow width.
        for col in NUM_COLS:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, NUM_COL_WIDTH)

        # Round column: interactive, minimum width tied to text baseline.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, TEXT_COL_MIN_WIDTH)

        # Player-name columns: stretch but respect the minimum.
        for col in (2, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            self.table.setColumnWidth(col, TEXT_COL_MIN_WIDTH)

        root.layout().addWidget(self.table)

        # ── Data reload triggers ──
        signals = AutonaTournamentDataProvider.instance.signals
        signals.tournament_phasegroup_updated.connect(
            lambda _payload=None: self._reload())
        signals.tournament_changed.connect(self._reload)
        StateManager.signals.state_big_change.connect(self._reload)

        AutonaAnalysisWidget.instance = self
        # Defer the first reload until StateManager is primed — running
        # before the phase-group data lands produces an empty table, and a
        # manual refresh triggers the populated one anyway.
        QTimer.singleShot(500, self._reload)

    # ── Data plumbing ───────────────────────────────────────────

    def _reload(self):
        try:
            rounds = StateManager.Get("bracket.bracket.rounds") or {}
        except Exception:
            logger.opt(exception=True).warning(
                "Analysis: failed to read bracket.bracket.rounds")
            rounds = {}

        rows = self._collect_rows(rounds)

        # Populating rows fights sorting; freeze it until the fill is done.
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

        self.summaryLabel.setText(f"{len(rows)} 試合")
        self._applyFilter()

    def _collect_rows(self, rounds: dict):
        """Walk every set in `rounds`, produce per-row data as a list of
        (display_text, sort_key) tuples so QTableWidget can sort numerics
        correctly alongside strings."""
        if not isinstance(rounds, dict) or not rounds:
            return []

        # Build reverse map: (dest_round, dest_idx, dest_slot) -> source_pos.
        # Used by losers-run traversal to find "which prior set fed into
        # this slot" without having to scan every time.
        pos_to_set = {}        # (round, idx) -> set_data
        win_source = {}        # (dest_round, dest_idx, dest_slot) -> (src_round, src_idx)
        for round_key, round_data in rounds.items():
            try:
                round_num = int(round_key)
            except (TypeError, ValueError):
                continue
            for set_idx_str, set_data in (round_data.get("sets") or {}).items():
                try:
                    set_idx = int(set_idx_str)
                except (TypeError, ValueError):
                    continue
                pos_to_set[(round_num, set_idx)] = set_data
                nw = set_data.get("nextWin")
                if nw and isinstance(nw, (list, tuple)) and len(nw) == 2:
                    win_slot = set_data.get("winSlot", 0) or 0
                    win_source[(int(nw[0]), int(nw[1]), int(win_slot))] = (
                        round_num, set_idx)

        # Placement map for loser-of-set → final tournament rank. Only
        # populated for sets whose loser is actually eliminated.
        placements = _compute_placements(rounds)

        rows = []
        for round_key, round_data in rounds.items():
            try:
                round_num = int(round_key)
            except (TypeError, ValueError):
                continue
            round_name = round_data.get("name") or str(round_num)

            for set_idx_str, set_data in (round_data.get("sets") or {}).items():
                try:
                    set_idx = int(set_idx_str)
                except (TypeError, ValueError):
                    continue
                ids = set_data.get("playerId") or [-1, -1]
                # Skip bye-bearing sets (they're display-hidden in the
                # Bracket dock too).
                if any(i == -1 for i in ids):
                    continue
                # Skip fully-pending (both sides unresolved).
                if ids[0] == -2 and ids[1] == -2:
                    continue

                names = set_data.get("playerName") or ["", ""]
                scores = set_data.get("score") or [0, 0]
                completed = bool(set_data.get("completed"))

                p1_seed = ids[0] if isinstance(ids[0], int) else None
                p2_seed = ids[1] if isinstance(ids[1], int) else None
                p1_name = names[0] or (
                    f"(seed {p1_seed})" if (p1_seed and p1_seed > 0) else "TBD")
                p2_name = names[1] or (
                    f"(seed {p2_seed})" if (p2_seed and p2_seed > 0) else "TBD")

                score_text = f"{scores[0]}-{scores[1]}" if completed else "-"
                # Upset factor: only meaningful once completed with a
                # clear winner.
                uf_text = ""
                uf_sort = 0
                if completed and scores[0] != scores[1]:
                    if scores[0] > scores[1]:
                        winner_seed, loser_seed = p1_seed, p2_seed
                    else:
                        winner_seed, loser_seed = p2_seed, p1_seed
                    uf = _upset_factor(winner_seed, loser_seed)
                    uf_text = str(uf)
                    uf_sort = uf

                # Losers run for the winner of this set.
                lr = self._losers_run(
                    round_num, set_idx, set_data, pos_to_set, win_source)
                lr_text = str(lr) if lr > 0 else ""
                lr_sort = lr

                # SeedOver: only meaningful for sets where the loser is
                # eliminated and placed better than their seed.
                so_text = ""
                so_sort = 0
                placement = placements.get((round_num, set_idx))
                if completed and scores[0] != scores[1] and placement is not None:
                    loser_seed = p2_seed if scores[0] > scores[1] else p1_seed
                    if loser_seed and loser_seed > 0 and loser_seed > placement:
                        so_val = loser_seed - placement
                        so_text = str(so_val)
                        so_sort = so_val

                rows.append([
                    (str(round_name), (round_num, set_idx)),
                    (str(p1_seed) if p1_seed and p1_seed > 0 else "-",
                     p1_seed if (p1_seed and p1_seed > 0) else 9999),
                    (p1_name, p1_name.lower()),
                    (score_text, sum(scores)),
                    (p2_name, p2_name.lower()),
                    (str(p2_seed) if p2_seed and p2_seed > 0 else "-",
                     p2_seed if (p2_seed and p2_seed > 0) else 9999),
                    (uf_text, uf_sort),
                    (lr_text, lr_sort),
                    (so_text, so_sort),
                ])
        return rows

    @staticmethod
    def _losers_run(round_num, set_idx, set_data, pos_to_set, win_source):
        """Count how many LB matches the winner of this set won
        consecutively before arriving here. UB matches always return 0,
        and a winner who came directly from the UB (with no LB chain)
        also yields 0."""
        # Only LB matches can have a non-zero losers-run — a UB winner
        # has no LB chain by definition.
        if round_num >= 0:
            return 0
        if not set_data.get("completed"):
            return 0
        scores = set_data.get("score") or [0, 0]
        if scores[0] == scores[1]:
            return 0
        winner_slot = 0 if scores[0] > scores[1] else 1
        ids = set_data.get("playerId") or [-1, -1]
        winner_seed = ids[winner_slot]

        run = 0
        current = (round_num, set_idx, winner_slot)
        visited = set()
        while current not in visited:
            visited.add(current)
            source_pos = win_source.get(current)
            if source_pos is None:
                break
            src = pos_to_set.get(source_pos)
            if not src or not src.get("completed"):
                break
            sscores = src.get("score") or [0, 0]
            if sscores[0] == sscores[1]:
                break
            src_winner_slot = 0 if sscores[0] > sscores[1] else 1
            sids = src.get("playerId") or [-1, -1]
            if sids[src_winner_slot] != winner_seed:
                break
            # Only keep counting while we're still on the LB side; the
            # first UB ancestor stops the streak.
            if source_pos[0] < 0:
                run += 1
                current = (source_pos[0], source_pos[1], src_winner_slot)
            else:
                break
        return run

    # ── Filter ──────────────────────────────────────────────────

    def _applyFilter(self):
        needle = self.filterEdit.text().strip().lower()
        if not needle:
            for r in range(self.table.rowCount()):
                self.table.setRowHidden(r, False)
            return
        # Match against round (col 0), p1 name (col 2), p2 name (col 4).
        match_cols = (0, 2, 4)
        for r in range(self.table.rowCount()):
            hit = False
            for c in match_cols:
                item = self.table.item(r, c)
                if item and needle in item.text().lower():
                    hit = True
                    break
            self.table.setRowHidden(r, not hit)
