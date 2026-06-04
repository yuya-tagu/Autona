from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from .AutonaColorButton import AutonaColorButton
from .StateManager import StateManager
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from . import ui_constants as UI
from . import ui_layout as L


# LatteCon crew battles support 1v1 up to 9v9.
MAX_MEMBERS = 9
# Stocks a fresh member row starts with (typical Smash crew battle).
DEFAULT_STOCK = 3
# Default member count shown on a clean board (both teams share the count,
# mirroring LatteCon's single `cb_memberNum`).
DEFAULT_MEMBER_NUM = 3
# LatteCon's left/right crew colours.
DEFAULT_COLORS = ("#fe0000", "#0071bc")
# Accent gold for the "currently playing" marker — matches the OBS crew
# layout's --accent-color (layout/main.css) so the app and overlay agree.
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

        StateManager.Set(f"score.{self.scoreboardNumber}", {})

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
        self._bestOfSpin = QSpinBox(self.scoreColumn)
        self._bestOfSpin.setObjectName("best_of")
        self.charNumber = QSpinBox(self)          # crew has no characters
        self.charNumber.setVisible(False)

        self._building = True
        self._buildUi()
        self._building = False

        self._writeInitialState()

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

        self.btSwap = QPushButton()
        self.btSwap.setIcon(QIcon(UI.icon_path("swap.svg")))
        self.btSwap.setToolTip(QApplication.translate("app", "Swap Teams"))
        self.btSwap.clicked.connect(self.SwapTeams)
        controls.addWidget(self.btSwap)

        self.btReset = QPushButton(QApplication.translate("app", "Reset Stocks"))
        L.tag_button(self.btReset, L.BUTTON_DANGER)
        self.btReset.clicked.connect(self.ResetScore)
        controls.addWidget(self.btReset)

        outer.addLayout(controls)

        # Phase / round inputs (editable; written to score.3.phase / .match).
        # The OBS crew overlay shows these next to the tournament name.
        phaseRow = QHBoxLayout()
        phaseRow.addWidget(QLabel(QApplication.translate("app", "Phase")))
        self.phaseCombo = self._makePhaseCombo(
            "phase", AutonaLocaleHelper.phaseNames)
        phaseRow.addWidget(self.phaseCombo, 1)
        phaseRow.addSpacing(12)
        phaseRow.addWidget(QLabel(QApplication.translate("app", "Match")))
        self.matchCombo = self._makePhaseCombo(
            "match", AutonaLocaleHelper.matchNames)
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
                " border-radius: 8px; border: 2px solid #777;"
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

    def _makePhaseCombo(self, state_key: str, names: dict) -> QComboBox:
        # Editable combo seeded with the localized phase/round terms (so the
        # user can pick "決勝" / "準決勝" etc. or type a custom value).
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(110)
        combo.addItem("")
        for key, val in (names or {}).items():
            if not isinstance(val, str) or "{0}" in val or "indicator" in str(key):
                continue
            if combo.findText(val) < 0:
                combo.addItem(val)
        # Common "Top N" phases as a convenience (phaseNames only).
        top_n = (names or {}).get("top_n")
        if isinstance(top_n, str) and "{0}" in top_n:
            for n in (8, 16, 32, 48, 64):
                txt = top_n.format(n)
                if combo.findText(txt) < 0:
                    combo.addItem(txt)
        combo.setCurrentText("")

        def write():
            if not self._building:
                StateManager.Set(self._key(state_key), combo.currentText())
        combo.lineEdit().editingFinished.connect(write)
        combo.currentIndexChanged.connect(lambda _i: write())
        return combo

    # ── State helpers ──────────────────────────────────────────────────

    def _key(self, *parts) -> str:
        return ".".join([f"score.{self.scoreboardNumber}", *map(str, parts)])

    def _writeInitialState(self):
        with StateManager.SaveBlock():
            # Board-level phase / round (shown on the OBS crew overlay).
            StateManager.Set(self._key("phase"), "")
            StateManager.Set(self._key("match"), "")
            n = self.playerNumber.value()
            for side in range(2):
                team_no = side + 1
                StateManager.Set(self._key("team", team_no, "teamName"), "")
                StateManager.Set(
                    self._key("team", team_no, "color"), DEFAULT_COLORS[side])
                StateManager.Set(self._key("team", team_no, "memberNum"), n)
                StateManager.Set(self._key("team", team_no, "activeMember"), "0")
                for m in range(1, MAX_MEMBERS + 1):
                    rows = self.memberRows[side][m - 1]
                    StateManager.Set(
                        self._key("team", team_no, "player", m, "name"), "")
                    StateManager.Set(
                        self._key("team", team_no, "player", m, "stock"),
                        rows["stock"].value())
                self._recalcTotal(side)

    def _teamTotal(self, side: int) -> int:
        # Sum remaining stocks only for visible members that have a name
        # entered — unnamed roster slots don't count toward the team total.
        n = self.playerNumber.value()
        return sum(
            self.memberRows[side][m]["stock"].value()
            for m in range(n)
            if self.memberRows[side][m]["name"].text().strip())

    def _recalcTotal(self, side: int):
        team_no = side + 1
        total = self._teamTotal(side)
        self.totalLabels[side].setText(str(total))
        if not self._building:
            # Write the changed member stock(s) + the recomputed team total.
            with StateManager.SaveBlock():
                for m in range(MAX_MEMBERS):
                    StateManager.Set(
                        self._key("team", team_no, "player", m + 1, "stock"),
                        self.memberRows[side][m]["stock"].value())
                StateManager.Set(self._key("team", team_no, "score"), total)
        else:
            StateManager.Set(self._key("team", team_no, "score"), total)

    # ── UI signal handlers ─────────────────────────────────────────────

    def _onMemberNumChanged(self, value: int):
        value = int(value)
        for side in range(2):
            for m in range(MAX_MEMBERS):
                self.memberRows[side][m]["row"].setVisible(m < value)
        if not self._building:
            with StateManager.SaveBlock():
                for side in range(2):
                    StateManager.Set(
                        self._key("team", side + 1, "memberNum"), value)
                    self._recalcTotal(side)

    def _onTeamNameChanged(self, side: int):
        StateManager.Set(
            self._key("team", side + 1, "teamName"),
            self.teamNameEdits[side].text())

    def _onColorChanged(self, side: int, color):
        if self._building:
            return
        StateManager.Set(self._key("team", side + 1, "color"), color or "")

    def _onMemberNameChanged(self, side: int, member: int):
        with StateManager.SaveBlock():
            StateManager.Set(
                self._key("team", side + 1, "player", member, "name"),
                self.memberRows[side][member - 1]["name"].text())
            # Naming/clearing a slot changes which stocks count toward the
            # team total, so recompute it.
            self._recalcTotal(side)

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
        StateManager.Set(
            self._key("team", side + 1, "activeMember"), str(active))

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
        # set_route() can route best-of/phase/match here. Crew v1 has no such
        # UI, so persist the generic fields if present and ignore the rest —
        # tolerant by design so generic callers don't crash board 3.
        if not isinstance(data, dict):
            return
        with StateManager.SaveBlock():
            if data.get("round_name") is not None:
                StateManager.Set(self._key("match"), data.get("round_name"))
            if data.get("tournament_phase") is not None:
                StateManager.Set(self._key("phase"), data.get("tournament_phase"))
            if data.get("bestOf") is not None:
                StateManager.Set(self._key("best_of"), data.get("bestOf"))

    # ── internal ───────────────────────────────────────────────────────

    def _flushAllState(self):
        # Write the full board snapshot from the widgets to StateManager.
        # Call inside a SaveBlock for a single broadcast.
        n = self.playerNumber.value()
        for side in range(2):
            team_no = side + 1
            StateManager.Set(
                self._key("team", team_no, "teamName"),
                self.teamNameEdits[side].text())
            StateManager.Set(
                self._key("team", team_no, "color"),
                self.colorButtons[side].color() or "")
            StateManager.Set(self._key("team", team_no, "memberNum"), n)
            active = 0
            for m, rows in enumerate(self.memberRows[side], start=1):
                StateManager.Set(
                    self._key("team", team_no, "player", m, "name"),
                    rows["name"].text())
                StateManager.Set(
                    self._key("team", team_no, "player", m, "stock"),
                    rows["stock"].value())
                if rows["active"].isChecked():
                    active = m
            StateManager.Set(
                self._key("team", team_no, "activeMember"), str(active))
            total = self._teamTotal(side)
            self.totalLabels[side].setText(str(total))
            StateManager.Set(self._key("team", team_no, "score"), total)
