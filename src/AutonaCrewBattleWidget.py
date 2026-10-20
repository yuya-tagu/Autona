import webbrowser
import urllib.parse

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from .AutonaColorButton import AutonaColorButton
from .core.StateManager import StateManager
from .core import StateSchema as schema
from .core.Actions import AutonaActions
from .core.CrewBattleService import CrewBattleService, CrewMember, CrewTeam
from .core.TournamentInfoService import TournamentInfoService
from .StateManagerBridge import StateManagerBridge
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from . import ui_constants as UI
from . import ui_layout as L


# The crew battle's own rules (roster size, starting stocks, default crew
# colours) belong to the board's state, not to its widgets, so they live with
# the writer — core/CrewBattleService.py — and are re-exported here because
# this module's readers (and tests) have always reached them by these names.
MAX_MEMBERS = CrewBattleService.MAX_MEMBERS
DEFAULT_STOCK = CrewBattleService.DEFAULT_STOCK
DEFAULT_MEMBER_NUM = CrewBattleService.DEFAULT_MEMBER_NUM
DEFAULT_COLORS = CrewBattleService.DEFAULT_COLORS
# Accent gold for the "currently playing" marker — matches the OBS crew
# layout's --accent-color (layout/main.css) so the app and overlay agree.
# Purely a paint colour, so unlike the four above it stays on the Qt side.
ACCENT_GOLD = "#f0a500"


class AutonaCrewBattleWidgetSignals(QObject):
    # Names are deliberately kept compatible with AutonaScoreboardWidget so
    # the WebServer's generic team_* actions can emit into board 3 without
    # special-casing the widget type.
    CommandScoreChange = Signal(int, int)      # team(0/1), change (no-op for crew)
    CommandTeamColor = Signal(int, str)        # team(0/1), color hex
    SwapTeams = Signal()
    ChangeSetData = Signal(dict)
    # Crew-specific.
    CommandMemberStock = Signal(int, int, int)  # team(0/1), member(1-based), change
    SetActiveMember = Signal(int, int)          # team(0/1), member(1-based; 0 = none)


class AutonaCrewBattleWidget(QWidget):
    """Dedicated SCORE widget for the Crewbattle (board 3) format.

    Unlike Team mode (board 2), where a team carries a single score, a crew
    battle tracks an individual remaining-stock count per member; the team
    total is the auto-summed remaining stocks (LatteCon "autocalc"). Each team
    also tracks which member is currently in (the active indicator), and both
    teams share a single member count.

    Deliberately NOT derived from AutonaScoreboardWidget: crew needs neither
    Start.gg pulls nor character UI, so a focused widget keeps boards 1/2
    untouched. A handful of attributes/methods are intentionally name-shaped
    after AutonaScoreboardWidget so the generic WebServer actions (clear_all,
    reset_match, swap_teams, set_route, ...) don't break if they land on
    board 3.
    """

    def __init__(self, scoreboardNumber=3, *args):
        super().__init__(*args)
        self.scoreboardNumber = scoreboardNumber

        # Compat flag read by WebServerActions.get_swap / SwapTeams logic.
        self.teamsSwapped = False

        # Every score.3 write this widget makes goes through here (headless
        # core Step 5, instalment 4). One instance per board, like
        # AutonaScoreboardWidget's SetLoadService.
        self.crew = CrewBattleService(self.scoreboardNumber)

        # Board 3's skeleton is just the subtree root; the rest is written by
        # _writeInitialState() below. Seeded through the shared schema helper
        # so this and AutonaScoreboardManager's placeholder stay identical
        # (headless core Step 2).
        schema.SeedBoard(self.scoreboardNumber)

        self.signals = AutonaCrewBattleWidgetSignals()
        self.signals.CommandScoreChange.connect(self.CommandScoreChange)
        self.signals.CommandTeamColor.connect(self.CommandTeamColor)
        self.signals.SwapTeams.connect(self.SwapTeams)
        self.signals.ChangeSetData.connect(self.ChangeSetData)
        self.signals.CommandMemberStock.connect(self.CommandMemberStock)
        self.signals.SetActiveMember.connect(self.SetActiveMember)

        # Per-team widget registries (index 0 = team 1, 1 = team 2).
        self.teamNameEdits: list[QLineEdit] = []
        self.colorButtons: list[AutonaColorButton] = []
        self.totalLabels: list[QLabel] = []
        # memberRows[team] -> list of {"row","active","name","stock"} dicts.
        self.memberRows: list[list[dict]] = [[], []]

        # Hidden compat widgets so generic WebServer actions that poke a
        # scoreboard's internals don't AttributeError on board 3.
        self.scoreColumn = QWidget(self)
        self.scoreColumn.setVisible(False)
        # Crew has no best-of UI, but the spinbox is still the single source
        # of truth for score.{N}.best_of, exactly like boards 1/2: every
        # writer (ChangeSetData / set_route) goes through setValue, and the
        # generic reset (WebServerActions._reset_best_of -> setValue(0))
        # therefore clears state instead of leaving a stale BO on the overlay.
        self._bestOfSpin = QSpinBox(self.scoreColumn)
        self._bestOfSpin.setObjectName("best_of")
        self._bestOfSpin.valueChanged.connect(self._onBestOfChanged)
        self.charNumber = QSpinBox(self)          # crew has no characters
        self.charNumber.setVisible(False)

        self._building = True
        self._buildUi()
        self._building = False

        self._writeInitialState()

        # A flat delta renders its key path as a Python list, so
        # str(delta) contains "['score', '3', 'team', '1', 'color']"
        # verbatim -- an exact containment test with no false positives.
        # Matching plain "color" instead also caught colors.{feature}.*,
        # tournamentInfo's themeColor and the *other* boards' team colours,
        # any of which made this board re-read its colour buttons for nothing.
        self._colorPathMarkers = tuple(
            str(["score", str(self.scoreboardNumber), "team", str(team),
                 "color"])
            for team in (1, 2))

        # tournamentInfo.* lives outside score.{N}, so EVENT tab edits and
        # edits made on boards 1/2 arrive here as regular state_updated
        # deltas; state_big_change covers wholesale loads (startup restore,
        # Start.gg import). Mirrors AutonaScoreboardWidget's equivalent hook.
        StateManagerBridge.instance.state_updated.connect(
            self._onStateDelta)
        StateManagerBridge.instance.state_big_change.connect(
            self._onStateBigChange)

    # ── UI construction ────────────────────────────────────────────────

    def _buildUi(self):
        outer = QVBoxLayout()
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        self.setLayout(outer)

        # Shared controls: member count + swap + reset.
        controls = QHBoxLayout()
        controls.addWidget(QLabel(QApplication.translate("app", "Members")))
        # Named `playerNumber` for WebServer compat (set_route players=, clear_all).
        self.playerNumber = QSpinBox()
        self.playerNumber.setRange(1, MAX_MEMBERS)
        self.playerNumber.setValue(DEFAULT_MEMBER_NUM)
        self.playerNumber.setFixedWidth(60)
        self.playerNumber.valueChanged.connect(self._onMemberNumChanged)
        controls.addWidget(self.playerNumber)
        controls.addStretch(1)

        # Negative action leftmost in the trailing cluster (app-wide
        # button-pair convention), the neutral actions to its right.
        self.btReset = QPushButton(QApplication.translate("app", "Reset Stocks"))
        L.tag_button(self.btReset, L.BUTTON_DANGER)
        self.btReset.clicked.connect(self.ResetScore)
        controls.addWidget(self.btReset)

        # X-post: same flow as the 1v1 board (AutonaScoreboardWidget) — opens
        # X's web intent with a prefilled body. Neutral (secondary) styling.
        self.xpostBtn = QPushButton(QApplication.translate("app", "Xに投稿する"))
        self.xpostBtn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.xpostBtn.clicked.connect(self.PostToX)
        controls.addWidget(self.xpostBtn)

        self.btSwap = QPushButton()
        self.btSwap.setIcon(QIcon(UI.icon_path("swap.svg")))
        self.btSwap.setToolTip(QApplication.translate("app", "Swap Teams"))
        self.btSwap.clicked.connect(self.SwapTeams)
        controls.addWidget(self.btSwap)

        outer.addLayout(controls)

        # Display tournament name row: backed by tournamentInfo.
        # displayTournamentName, not score.{N}.* — board-agnostic, immediate
        # (no 反映 gate), and shared with boards 1/2. See
        # AutonaScoreboardWidget's equivalent row for the full rationale.
        displayNameRow = QHBoxLayout()
        displayNameRow.addWidget(
            QLabel(QApplication.translate("app", "表示大会名")))
        self.displayTournamentNameEdit = QLineEdit()
        self.displayTournamentNameEdit.setText(
            StateManager.Get(
                TournamentInfoService.Key(
                    schema.TOURNAMENT_DISPLAY_NAME_KEY), "") or "")
        # tournamentInfo.* is TournamentInfoService's, EVENT panel or no EVENT
        # panel (headless core Step 4b), and typing here is the strongest of
        # its three 表示大会名 writers: it does not disturb the "name the
        # display last followed" ledger, so a later tournament-name edit
        # cannot overwrite what was typed. Same wiring as the SCORE panel's
        # row (AutonaScoreboardWidget), which is the point — the two fields
        # are the same field.
        self.displayTournamentNameEdit.editingFinished.connect(
            lambda: TournamentInfoService.instance.SetDisplayName(
                self.displayTournamentNameEdit.text()))
        displayNameRow.addWidget(self.displayTournamentNameEdit, 1)
        outer.addLayout(displayNameRow)

        # Phase / round inputs (editable; written to score.3.phase / .match).
        # The OBS crew overlay shows these next to the tournament name.
        phaseRow = QHBoxLayout()
        phaseRow.addWidget(QLabel(QApplication.translate("app", "Phase")))
        self.phaseCombo = self._makePhaseCombo(
            schema.PHASE_KEY, AutonaLocaleHelper.phaseNames)
        phaseRow.addWidget(self.phaseCombo, 1)
        phaseRow.addSpacing(12)
        # Pool (phase group) is a separate state key (score.3.pool), same as
        # the 1v1/team boards. Candidates come from the "group" template
        # (_makePhaseCombo skips templated entries in the names dict).
        phaseRow.addWidget(QLabel(QApplication.translate("app", "Pool")))
        groupTemplate = AutonaLocaleHelper.phaseNames.get("group", "")
        poolCandidates = ([groupTemplate.format(letter)
                           for letter in ["A", "B", "C", "D"]]
                          if "{0}" in groupTemplate else [])
        self.poolCombo = self._makePhaseCombo(
            schema.POOL_KEY, candidates=poolCandidates)
        phaseRow.addWidget(self.poolCombo, 1)
        phaseRow.addSpacing(12)
        phaseRow.addWidget(QLabel(QApplication.translate("app", "Match")))
        self.matchCombo = self._makePhaseCombo(
            schema.MATCH_KEY,
            candidates=AutonaLocaleHelper.GetMatchNameCandidates())
        phaseRow.addWidget(self.matchCombo, 1)
        outer.addLayout(phaseRow)

        # Two team columns side by side.
        columns = QHBoxLayout()
        columns.setSpacing(8)
        for side in range(2):
            columns.addWidget(self._buildTeamColumn(side), 1)
        outer.addLayout(columns)
        outer.addStretch(1)

        self._onMemberNumChanged(self.playerNumber.value())

    def _buildTeamColumn(self, side: int) -> QWidget:
        team_no = side + 1
        group = L.make_card(
            QApplication.translate("app", "TEAM {0}").format(team_no))
        L.apply_card_layout(group)
        vbox = group.layout()

        # Header: team name + color.
        header = QHBoxLayout()
        nameEdit = QLineEdit()
        nameEdit.setPlaceholderText(
            QApplication.translate("app", "Team {0}").format(team_no))
        nameEdit.editingFinished.connect(
            lambda s=side: self._onTeamNameChanged(s))
        self.teamNameEdits.append(nameEdit)
        header.addWidget(nameEdit, 1)

        colorBtn = AutonaColorButton(color=DEFAULT_COLORS[side])
        colorBtn.setFixedWidth(36)
        colorBtn.setToolTip(QApplication.translate("app", "Team Color"))
        colorBtn.colorChanged.connect(
            lambda c, s=side: self._onColorChanged(s, c))
        self.colorButtons.append(colorBtn)
        header.addWidget(colorBtn)
        vbox.addLayout(header)

        # Member rows (all created up-front; only `memberNum` shown).
        activeGroup = QButtonGroup(group)
        activeGroup.setExclusive(False)
        for m in range(1, MAX_MEMBERS + 1):
            row = QHBoxLayout()
            row.setSpacing(4)

            active = QRadioButton()
            active.setAutoExclusive(False)
            # Gold "currently playing" dot, matching the OBS crew overlay's ●
            # (overrides the theme's accent-red indicator).
            active.setStyleSheet(
                "QRadioButton::indicator { width: 14px; height: 14px;"
                f" border-radius: 8px; border: 2px solid {UI.palette()['border_strong']};"
                " background: transparent; }"
                "QRadioButton::indicator:checked {"
                f" border: 2px solid {ACCENT_GOLD};"
                f" background-color: {ACCENT_GOLD}; image: none; }}")
            active.setToolTip(QApplication.translate("app", "Currently playing"))
            active.toggled.connect(
                lambda checked, s=side, mm=m: self._onActiveToggled(s, mm, checked))
            activeGroup.addButton(active, m)
            row.addWidget(active)

            name = QLineEdit()
            name.setPlaceholderText(
                QApplication.translate("app", "Member {0}").format(m))
            name.editingFinished.connect(
                lambda s=side, mm=m: self._onMemberNameChanged(s, mm))
            row.addWidget(name, 1)

            stock = QSpinBox()
            stock.setRange(0, 99)
            stock.setValue(DEFAULT_STOCK)
            stock.setFixedWidth(56)
            stock.valueChanged.connect(
                lambda _v, s=side: self._recalcTotal(s))
            row.addWidget(stock)

            container = QWidget()
            container.setLayout(row)
            container.layout().setContentsMargins(0, 0, 0, 0)
            vbox.addWidget(container)
            self.memberRows[side].append(
                {"row": container, "active": active, "name": name, "stock": stock})

        # Team total (read-only, auto-summed).
        totalRow = QHBoxLayout()
        totalRow.addWidget(QLabel(QApplication.translate("app", "Total")))
        totalRow.addStretch(1)
        total = QLabel("0")
        _f = total.font()
        _f.setBold(True)
        _f.setPointSize(_f.pointSize() + 3)
        total.setFont(_f)
        self.totalLabels.append(total)
        totalRow.addWidget(total)
        vbox.addLayout(totalRow)

        return group

    def _makePhaseCombo(self, field: str, names: dict = None,
                        candidates: list = None) -> QComboBox:
        # Editable combo seeded with the localized phase/round terms (so the
        # user can pick a term or type a custom value). Pass a prebuilt
        # `candidates` list (match names) or a `names` dict (phase names).
        # `field` is one of StateSchema.SCORE_MATCH_FIELDS — the same three
        # keys the 1v1 / team boards' match bar writes, through the same
        # operation, so board 3 is not a second reading of what "phase" means.
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(110)
        combo.addItem("")
        for val in (candidates or []):
            if combo.findText(val) < 0:
                combo.addItem(val)
        for key, val in (names or {}).items():
            if not isinstance(val, str) or "{0}" in val or "indicator" in str(key):
                continue
            if combo.findText(val) < 0:
                combo.addItem(val)
        combo.setCurrentText("")

        def write():
            if not self._building:
                AutonaActions.instance.SetMatchField(
                    self.scoreboardNumber, field, combo.currentText())
        combo.lineEdit().editingFinished.connect(write)
        combo.currentIndexChanged.connect(lambda _i: write())
        return combo

    # ── State helpers ──────────────────────────────────────────────────

    def _key(self, *parts) -> str:
        return ".".join([f"score.{self.scoreboardNumber}", *map(str, parts)])

    def _onStateDelta(self, delta):
        # Same cheap containment filter AutonaScoreboardWidget uses on deltas.
        # One slot for both subtrees so str(delta) is paid once.
        text = str(delta)
        if "tournamentInfo" in text:
            self._syncDisplayTournamentNameEdit()
        if any(marker in text for marker in self._colorPathMarkers):
            self._SyncTeamColors()

    def _SyncTeamColors(self):
        """Follow score.{n}.team.{t}.color into the two colour buttons.

        The team colour is written by AutonaActions now (headless core
        Step 3) rather than by CommandTeamColor poking this button, so an
        /api/v1 or legacy colour change reaches the board here. Signals are
        blocked: colorChanged is what writes the state on a local pick, and
        letting it fire would echo the value straight back.
        """
        for side, button in enumerate(self.colorButtons):
            value = StateManager.Get(self._key("team", side + 1, "color"), None)
            if not isinstance(value, str) or not value:
                continue
            if button.color() == value:
                continue
            with QSignalBlocker(button):
                button.setColor(value)

    def _onStateBigChange(self):
        self._syncDisplayTournamentNameEdit()
        self._SyncTeamColors()

    def _syncDisplayTournamentNameEdit(self):
        # Reflect other-board / EVENT-tab edits to displayTournamentName into
        # this board's own field, without fighting an in-progress edit here
        # (hasFocus) or resetting the cursor on a no-op update (same text).
        edit = getattr(self, "displayTournamentNameEdit", None)
        if edit is None or edit.hasFocus():
            return
        value = StateManager.Get(
            TournamentInfoService.Key(
                schema.TOURNAMENT_DISPLAY_NAME_KEY), "") or ""
        if edit.text() != value:
            edit.setText(value)

    def _onBestOfChanged(self, value):
        # _bestOfSpin.valueChanged handler. The spinbox stays the entry point
        # (same arrangement as boards 1/2); what gets written is the
        # service's business — and for crew that is the raw key only. See
        # CrewBattleService.SetBestOf for why the derived texts are omitted.
        self.crew.SetBestOf(value)

    def _writeInitialState(self):
        # The launch write is the same pass as _flushAllState plus the
        # board-level fields, so a fresh board and a just-cleared board
        # cannot drift apart. Everything below is read off the freshly built
        # rows, i.e. the defaults they were constructed with.
        self._applyTotals(
            self.crew.SeedBoardState(
                self._snapshotTeams(), self.playerNumber.value(),
                self._bestOfSpin.value()))

    def _snapshotTeams(self) -> list:
        """The two crews exactly as the rows currently show them.

        Resolving the widget values is the Qt half; deciding what that means
        in the state (which stocks count, what the team total is) is
        CrewBattleService's.
        """
        return [
            CrewTeam(
                name=self.teamNameEdits[side].text(),
                color=self.colorButtons[side].color() or "",
                members=tuple(
                    CrewMember(
                        name=rows["name"].text(),
                        stock=rows["stock"].value(),
                        active=rows["active"].isChecked())
                    for rows in self.memberRows[side]))
            for side in range(2)]

    def _stocks(self, side: int) -> list:
        return [rows["stock"].value() for rows in self.memberRows[side]]

    def _applyTotals(self, totals):
        for side, total in enumerate(totals):
            self.totalLabels[side].setText(str(total))

    def _recalcTotal(self, side: int):
        # Stock spinbox handler: publish the column and render the total the
        # service computed from it.
        if self._building:
            # Unreachable today — every stock spinbox is given its default
            # before its valueChanged is connected — but kept as the
            # no-broadcast counterpart of the branch below.
            self.totalLabels[side].setText(
                str(self.crew.PublishTotal(side + 1)))
            return
        self.totalLabels[side].setText(
            str(self.crew.PublishStocks(side + 1, self._stocks(side))))

    # ── UI signal handlers ─────────────────────────────────────────────

    def _onMemberNumChanged(self, value: int):
        value = int(value)
        for side in range(2):
            for m in range(MAX_MEMBERS):
                self.memberRows[side][m]["row"].setVisible(m < value)
        if not self._building:
            self._applyTotals(
                self.crew.SetMemberNum(
                    value, [self._stocks(side) for side in range(2)]))

    def _onTeamNameChanged(self, side: int):
        # The shared operation: score.{n}.team.{t}.teamName means the same
        # thing on all three boards.
        AutonaActions.instance.SetTeamName(
            self.scoreboardNumber, side + 1, self.teamNameEdits[side].text())

    def _onColorChanged(self, side: int, color):
        if self._building:
            return
        AutonaActions.instance.SetTeamColor(
            self.scoreboardNumber, side + 1, color)

    def _onMemberNameChanged(self, side: int, member: int):
        # Naming/clearing a slot changes which stocks count toward the team
        # total; the service owns that pairing.
        self.totalLabels[side].setText(
            str(self.crew.SetMemberName(
                side + 1, member,
                self.memberRows[side][member - 1]["name"].text(),
                self._stocks(side))))

    def _onActiveToggled(self, side: int, member: int, checked: bool):
        if self._building:
            return
        if checked:
            # Enforce single active member per team (manual, so the active
            # radio can also be toggled back off to mean "no one in").
            for j, rows in enumerate(self.memberRows[side]):
                if j + 1 != member and rows["active"].isChecked():
                    rows["active"].blockSignals(True)
                    rows["active"].setChecked(False)
                    rows["active"].blockSignals(False)
            active = member
        else:
            active = 0
        self.crew.SetActiveMember(side + 1, active)

    # ── Crew command slots ─────────────────────────────────────────────

    def CommandMemberStock(self, side: int, member: int, change: int):
        if side not in (0, 1) or not (1 <= member <= MAX_MEMBERS):
            return
        spin = self.memberRows[side][member - 1]["stock"]
        spin.setValue(spin.value() + change)  # valueChanged -> _recalcTotal

    def SetActiveMember(self, side: int, member: int):
        if side not in (0, 1):
            return
        if member <= 0:
            # Clear active for this team.
            for rows in self.memberRows[side]:
                if rows["active"].isChecked():
                    rows["active"].setChecked(False)
            return
        if 1 <= member <= MAX_MEMBERS:
            self.memberRows[side][member - 1]["active"].setChecked(True)

    # ── AutonaScoreboardWidget-compatible slots (used by WebServer) ─────

    def CommandScoreChange(self, team: int, change: int):
        # Team-level score is derived from member stocks in crew mode, so a
        # generic team score nudge has no meaningful target. Kept as a safe
        # no-op so /scoreboard3-teamN-scoreup never crashes board 3.
        logger.debug(
            "Crewbattle: ignoring team-level CommandScoreChange "
            f"(team={team}, change={change}); crew score is auto-summed")

    def CommandTeamColor(self, team: int, color, force_opponent=False):
        if team in (0, 1) and isinstance(color, str) and color:
            self.colorButtons[team].setColor(color)  # colorChanged -> state

    def SwapTeams(self):
        with StateManager.SaveBlock():
            for side in (0, 1):
                self.colorButtons[side].blockSignals(True)
            try:
                # Team names.
                t0, t1 = self.teamNameEdits
                t0v, t1v = t0.text(), t1.text()
                t0.setText(t1v)
                t1.setText(t0v)
                # Colors.
                c0, c1 = self.colorButtons[0].color(), self.colorButtons[1].color()
                self.colorButtons[0].setColor(c1)
                self.colorButtons[1].setColor(c0)
                # Member rows (name + stock + active).
                for m in range(MAX_MEMBERS):
                    r0 = self.memberRows[0][m]
                    r1 = self.memberRows[1][m]
                    n0, n1 = r0["name"].text(), r1["name"].text()
                    r0["name"].setText(n1)
                    r1["name"].setText(n0)
                    s0, s1 = r0["stock"].value(), r1["stock"].value()
                    r0["stock"].blockSignals(True)
                    r1["stock"].blockSignals(True)
                    r0["stock"].setValue(s1)
                    r1["stock"].setValue(s0)
                    r0["stock"].blockSignals(False)
                    r1["stock"].blockSignals(False)
                    a0, a1 = r0["active"].isChecked(), r1["active"].isChecked()
                    for rr, av in ((r0, a1), (r1, a0)):
                        rr["active"].blockSignals(True)
                        rr["active"].setChecked(av)
                        rr["active"].blockSignals(False)
                self.teamsSwapped = not self.teamsSwapped
            finally:
                for side in (0, 1):
                    self.colorButtons[side].blockSignals(False)
                # Persist the swapped state in one shot.
                self._flushAllState()

    def ResetScore(self):
        # Restore every member to full stocks and clear the active markers.
        with StateManager.SaveBlock():
            for side in range(2):
                for rows in self.memberRows[side]:
                    rows["stock"].blockSignals(True)
                    rows["stock"].setValue(DEFAULT_STOCK)
                    rows["stock"].blockSignals(False)
                    rows["active"].blockSignals(True)
                    rows["active"].setChecked(False)
                    rows["active"].blockSignals(False)
            self._flushAllState()

    def ClearScore(self):
        # Mirrors AutonaScoreboardWidget.ClearScore (reset live scores). For
        # crew that means full stocks + no active member.
        self.ResetScore()

    def CommandClearAll(self, no_mains=False):
        # Mirrors AutonaScoreboardWidget.CommandClearAll (wipe player data).
        with StateManager.SaveBlock():
            for side in range(2):
                for rows in self.memberRows[side]:
                    rows["name"].setText("")
                    rows["stock"].blockSignals(True)
                    rows["stock"].setValue(DEFAULT_STOCK)
                    rows["stock"].blockSignals(False)
                    rows["active"].blockSignals(True)
                    rows["active"].setChecked(False)
                    rows["active"].blockSignals(False)
            self._flushAllState()

    def ChangeSetData(self, data):
        # set_route() / post_score() can route best-of/phase/match here. Crew
        # has no set-loading UI, so the generic fields are persisted if
        # present and the rest is ignored — tolerant by design so generic
        # callers don't crash board 3.
        #
        # The rules are CrewBattleService's, and they are deliberately NOT
        # SetLoadService's: see that module's docstring for the five readings
        # that differ and why unifying them would be a silent change. A
        # payload carrying nothing board 3 understands (a set load's
        # entrants, a score) leaves the block empty, which costs no cycle.
        with StateManager.SaveBlock():
            self.crew.ApplyMatchInfo(data)
            bestOf = self.crew.BestOfFromSetData(data)
            if bestOf is not None:
                # Route through the spinbox (boards 1/2 do the same) so it
                # stays in sync and a later reset actually clears the value.
                self._bestOfSpin.setValue(bestOf)

    # ── X (Twitter) post ───────────────────────────────────────────────

    def _teamMemberNames(self, team_n: int) -> list[str]:
        # Named members within the team's visible roster (memberNum), in order.
        member_num = int(
            StateManager.Get(
                self._key("team", team_n, schema.CREW_MEMBER_NUM_KEY))
            or MAX_MEMBERS)
        names = []
        for m in range(1, member_num + 1):
            name = (StateManager.Get(
                self._key("team", team_n, "player", m,
                          schema.PLAYER_TAG_KEY)) or "").strip()
            if name:
                names.append(name)
        return names

    def PostToX(self):
        # Mirror AutonaScoreboardWidget.PostToX: build a tweet body from the
        # current crew state and open X's web intent. Text-only — crew has no
        # thumbnail generator, so (unlike board 1) no image is copied first.
        try:
            tournament_name = StateManager.Get(
                "tournamentInfo.tournamentName", "") or ""
            phase = (StateManager.Get(
                self._key(schema.PHASE_KEY)) or "").strip()
            match = (StateManager.Get(
                self._key(schema.MATCH_KEY)) or "").strip()

            # Validate: both teams need a member name and a round name is set.
            missing = []
            if not self._teamMemberNames(1):
                missing.append(QApplication.translate("app", "Team 1 player name"))
            if not self._teamMemberNames(2):
                missing.append(QApplication.translate("app", "Team 2 player name"))
            if not (phase or match):
                missing.append(QApplication.translate("app", "Round name"))
            if missing:
                msgBox = QMessageBox()
                msgBox.setWindowIcon(QIcon(UI.icon_path("icon.png")))
                msgBox.setWindowTitle(QApplication.translate("app", "Post to X"))
                msgBox.setText(QApplication.translate(
                    "app",
                    "Cannot post to X: the following fields are empty."))
                msgBox.setInformativeText("\n".join(f"- {m}" for m in missing))
                msgBox.setIcon(QMessageBox.Warning)
                msgBox.exec()
                return

            def team_line(team_n):
                team_name = (StateManager.Get(
                    self._key("team", team_n,
                              schema.TEAM_NAME_KEY)) or "").strip()
                member_str = " / ".join(self._teamMemberNames(team_n))
                if team_name and member_str:
                    return f"{team_name} ({member_str})"
                return team_name or member_str or "???"

            round_line = f"[{phase}]" if phase else ""
            if match:
                round_line = f"{round_line} - {match}" if round_line else match

            parts = []
            if tournament_name:
                parts.append(tournament_name)
            if round_line:
                parts.append(round_line)
            parts.append("")
            parts.append(team_line(1))
            parts.append("vs")
            parts.append(team_line(2))

            text = "\n".join(parts)
            url = "https://twitter.com/intent/tweet?text=" + \
                urllib.parse.quote(text)
            webbrowser.open(url)
        except Exception as e:
            logger.error(f"PostToX error: {e}")

    # ── internal ───────────────────────────────────────────────────────

    def _flushAllState(self):
        # Hand the finished board to the service after a bulk operation
        # (swap / reset / clear) rearranged the rows with their signals
        # blocked. One broadcast, so a swap never puts a half-swapped board
        # on the stream.
        self._applyTotals(
            self.crew.PublishBoard(
                self._snapshotTeams(), self.playerNumber.value()))
