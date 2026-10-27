import webbrowser
import urllib.parse

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from .AutonaColorButton import AutonaColorButton
from shiboken6 import Shiboken

from .core.StateManager import StateManager
from .core import StateSchema as schema
from .core.Actions import AutonaActions
from .core.CrewBattleService import CrewBattleService
from .core.TournamentInfoService import TournamentInfoService
from .StateManagerBridge import StateManagerBridge
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaQtHelper import HeldBackText
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
    # A set payload for board 3: the board-dependent web action that still
    # emits into whichever board it addresses (post_score) lands here. Every
    # other operation of this board is core's (CrewBattleService, ADR-005
    # S3e-2), called by the Web directly -- set_team_data's payload too
    # since S3e-4 (AutonaWebOperations._apply_set_fields).
    ChangeSetData = Signal(dict)


class AutonaCrewBattleWidget(QWidget):
    """Dedicated SCORE widget for the Crewbattle (board 3) format.

    Unlike Team mode (board 2), where a team carries a single score, a crew
    battle tracks an individual remaining-stock count per member; the team
    total is the auto-summed remaining stocks (LatteCon "autocalc"). Each team
    also tracks which member is currently in (the active indicator), and both
    teams share a single member count.

    Deliberately NOT derived from AutonaScoreboardWidget: crew needs neither
    Start.gg pulls nor character UI, so a focused widget keeps boards 1/2
    untouched.

    The board writes nothing itself and keeps nothing the state does not
    (ADR-005 S3e-2): every input asks CrewBattleService (or AutonaActions,
    for what board 3 shares with boards 1/2) for the change, and the rows
    show score.3 as it is (_RenderFromState), whoever wrote it -- an input
    here, the Web, the headless shell's twin of the same call. A field the
    operator is typing in is left alone until they leave it, and finishing
    it does not put back the text a render had to leave there -- what the
    operator typed is their edit, and is written, unless that very field
    was written elsewhere meanwhile. The player cards' and the commentary
    panel's rule (HeldBackText).
    """

    def __init__(self, scoreboardNumber=3, *args):
        super().__init__(*args)
        self.scoreboardNumber = scoreboardNumber

        # Every score.3 write this board asks for goes through here (headless
        # core Step 5, instalment 4; the operations read the board from the
        # state since ADR-005 S3e-2). The service is per board, like
        # AutonaScoreboardWidget's SetLoadService; the Web makes its own for
        # the same board, which is the same thing (only the swap flag is
        # kept outside the state, and it is the board's, not the instance's).
        self.crew = CrewBattleService(self.scoreboardNumber)

        # Board 3's skeleton is just the subtree root; the rest is the
        # launch write below. Seeded through the shared schema helper so
        # this and AutonaScoreboardManager's placeholder stay identical
        # (headless core Step 2).
        schema.SeedBoard(self.scoreboardNumber)

        self.signals = AutonaCrewBattleWidgetSignals()
        self.signals.ChangeSetData.connect(self.ChangeSetData)

        # Per-team widget registries (index 0 = team 1, 1 = team 2).
        self.teamNameEdits: list[QLineEdit] = []
        self.colorButtons: list[AutonaColorButton] = []
        self.totalLabels: list[QLabel] = []
        # memberRows[team] -> list of {"row","active","name","stock"} dicts.
        self.memberRows: list[list[dict]] = [[], []]

        # State -> widgets (_RenderFromState): one pass queued at a time;
        # by field, what each field last showed in step with the state and
        # the text a pass had to leave in one the operator was in
        # (HeldBackText); the fields it left, to come back to once they are
        # left.
        self._renderPending = False
        self._rendering = False
        self._heldBack = HeldBackText()
        self._focusSkipped = []

        self._building = True
        self._buildUi()
        self._building = False

        # The launch write: the board as freshly built rows show it, which
        # is the defaults (CrewBattleService.SeedDefaults -- the headless
        # shell writes the same, tests/test_headless_seed.py).
        self.crew.SeedDefaults()
        self._RenderFromState()

        # A flat delta renders its key path as a Python list, so str(delta)
        # contains "['score', '3', ..." verbatim for every change under
        # score.3 -- an exact containment test, which a plain "score" (every
        # board) or "color" (colors.{feature}.*, the other boards' teams)
        # would not be.
        self._boardPathMarker = str(["score", str(self.scoreboardNumber)])[:-1]

        # tournamentInfo.* lives outside score.{N}, so EVENT tab edits and
        # edits made on boards 1/2 arrive here as regular state_updated
        # deltas; state_big_change covers wholesale loads (startup restore,
        # Start.gg import). Mirrors AutonaScoreboardWidget's equivalent hook.
        StateManagerBridge.instance.state_updated.connect(
            self._onStateDelta)
        StateManagerBridge.instance.state_big_change.connect(
            self._onStateBigChange)
        # The fields a render had to skip are shown once the operator leaves
        # them. A bound method: Qt drops the connection with the board.
        QApplication.instance().focusChanged.connect(self._onFocusChanged)

    @property
    def teamsSwapped(self) -> bool:
        """Whether the crews are shown swapped (core's, see
        CrewBattleService._swapped)."""
        return self.crew.teamsSwapped

    # ── UI construction ────────────────────────────────────────────────

    def _buildUi(self):
        outer = QVBoxLayout()
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        self.setLayout(outer)

        # Shared controls: member count + swap + reset.
        controls = QHBoxLayout()
        controls.addWidget(QLabel(QApplication.translate("app", "Members")))
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
        # field -> its combo, for the render.
        self._matchCombos = {}
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
            lambda s=side: self._onTeamNameFinished(s))
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
                lambda s=side, mm=m: self._onMemberNameFinished(s, mm))
            row.addWidget(name, 1)

            stock = QSpinBox()
            stock.setRange(0, 99)
            stock.setValue(DEFAULT_STOCK)
            stock.setFixedWidth(56)
            stock.valueChanged.connect(
                lambda value, s=side, mm=m: self._onStockChanged(s, mm, value))
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
                self._onMatchFieldFinished(field, combo)
        combo.lineEdit().editingFinished.connect(write)
        combo.currentIndexChanged.connect(lambda _i: write())
        self._matchCombos[field] = combo
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
        if self._boardPathMarker in text:
            self._ScheduleRender()

    def _onStateBigChange(self):
        self._syncDisplayTournamentNameEdit()
        self._ScheduleRender()

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

    # ── State -> widgets ───────────────────────────────────────────────

    def _ScheduleRender(self):
        """Queue one render pass. Deferred, so nothing is redrawn inside the
        notification or in the middle of the operation that caused it
        (agent-pitfalls #9), and coalesced: however many cycles a turn
        produces, one pass runs. The board is the timer's context, so a
        board torn down first takes the pending pass with it."""
        if self._renderPending:
            return
        self._renderPending = True
        QTimer.singleShot(0, self, self._RenderFromState)

    def _RenderFromState(self):
        """Show score.3 as the state holds it. Writes nothing.

        Every widget is set with its signals blocked, so no handler runs and
        nothing is echoed back. A field the operator is in is left alone --
        its text held back if they have not changed it (_LeaveForFocus) --
        and the pass is asked for again once they leave it (_onFocusChanged).
        """
        self._renderPending = False
        if self._rendering:
            return
        self._rendering = True
        try:
            self._focusSkipped = []
            with StateManager.lock:
                teams = [self.crew.ReadTeam(team) for team in (1, 2)]
                memberNum = self.crew.MemberNum()
                totals = [StateManager.Get(self._key("team", team, "score"), 0)
                          for team in (1, 2)]
                matchBar = {field: StateManager.Get(self._key(field), "")
                            for field in self._matchCombos}

            with QSignalBlocker(self.playerNumber):
                self.playerNumber.setValue(memberNum)
            shown = self.playerNumber.value()
            for side, crew in enumerate(teams):
                self._RenderText(self.teamNameEdits[side], crew.name)
                button = self.colorButtons[side]
                if crew.color and button.color() != crew.color:
                    with QSignalBlocker(button):
                        button.setColor(crew.color)
                for index, member in enumerate(crew.members):
                    rows = self.memberRows[side][index]
                    rows["row"].setVisible(index < shown)
                    self._RenderText(rows["name"], member.name)
                    if rows["stock"].value() != member.stock:
                        with QSignalBlocker(rows["stock"]):
                            rows["stock"].setValue(member.stock)
                    if rows["active"].isChecked() != member.active:
                        with QSignalBlocker(rows["active"]):
                            rows["active"].setChecked(member.active)
                self.totalLabels[side].setText(str(totals[side] or 0))
            for field, combo in self._matchCombos.items():
                value = matchBar[field]
                self._RenderCombo(combo, "" if value is None else str(value))
        finally:
            self._rendering = False

    @staticmethod
    def _Editing(widget) -> bool:
        """The operator is in `widget`: it has focus, or -- a combo -- its
        line edit has, or its drop-down is open."""
        if widget.hasFocus():
            return True
        if not isinstance(widget, QComboBox):
            return False
        edit = widget.lineEdit()
        if edit is not None and edit.hasFocus():
            return True
        view = widget.view()
        return view is not None and view.isVisible()

    def _LeaveForFocus(self, widget, current, text):
        """Leave `widget` as it is while the operator is in it (the state
        holds `text`). Its text is held back -- not to be written back when
        the field is finished -- if it is still what the field last showed
        (the value the state has moved on from), or if this field's own
        value was just written elsewhere while they typed (HeldBackText).
        Any other typing is their edit, and is written when they finish it,
        as any edit is."""
        self._heldBack.LeftAlone(widget, current, text)
        if widget not in self._focusSkipped:
            self._focusSkipped.append(widget)

    def _RenderText(self, edit, text):
        if edit.text() == text:
            self._heldBack.Shown(edit, text)
            return
        if self._Editing(edit):
            self._LeaveForFocus(edit, edit.text(), text)
            return
        with QSignalBlocker(edit):
            edit.setText(text)
        self._heldBack.Shown(edit, text)

    def _RenderCombo(self, combo, text):
        if combo.currentText() == text:
            self._heldBack.Shown(combo, text)
            return
        if self._Editing(combo):
            self._LeaveForFocus(combo, combo.currentText(), text)
            return
        with QSignalBlocker(combo), QSignalBlocker(combo.lineEdit()):
            combo.setCurrentText(text)
        self._heldBack.Shown(combo, text)

    def _onFocusChanged(self, old, new):
        """QApplication.focusChanged: once the operator has left every field
        the last pass had to skip, show the state in them (deferred: the
        field's own editingFinished runs first)."""
        if not self._focusSkipped:
            return
        skipped = [w for w in self._focusSkipped if Shiboken.isValid(w)]
        if any(self._Editing(w) for w in skipped):
            self._focusSkipped = skipped
            return
        self._focusSkipped = []
        self._ScheduleRender()

    def _Finished(self, widget, text, stateValue) -> bool:
        """Whether a finished edit of `widget` is the operator's: not the
        text the state already holds, and not the text a render had to
        leave in the field over a newer value (finishing that -- a
        focus-out, Enter -- would put the old value back). The player
        cards' rule (HeldBackText).

        Nor a field the render itself finished: hiding a member row the
        operator is typing in (the member count dropped) moves the focus out
        of it, and Qt finishes it there and then. The render writes nothing,
        so the typed text is not written; the same pass shows the state in
        the hidden field."""
        if self._rendering:
            return False
        return self._heldBack.IsEdit(widget, text, stateValue == text)

    # ── UI signal handlers ─────────────────────────────────────────────

    def _onMemberNumChanged(self, value: int):
        if self._building:
            return
        if not self.crew.SetMemberCount(value):
            self._ScheduleRender()

    def _onTeamNameFinished(self, side: int):
        # The shared operation: score.{n}.team.{t}.teamName means the same
        # thing on all three boards.
        edit = self.teamNameEdits[side]
        text = edit.text()
        if self._Finished(edit, text, StateManager.Get(
                self._key("team", side + 1, schema.TEAM_NAME_KEY))):
            AutonaActions.instance.SetTeamName(
                self.scoreboardNumber, side + 1, text)

    def _onColorChanged(self, side: int, color):
        if self._building:
            return
        AutonaActions.instance.SetTeamColor(
            self.scoreboardNumber, side + 1, color)

    def _onMemberNameFinished(self, side: int, member: int):
        # Naming/clearing a slot changes which stocks count toward the team
        # total; the service owns that pairing.
        edit = self.memberRows[side][member - 1]["name"]
        text = edit.text()
        if self._Finished(edit, text, StateManager.Get(
                self._key("team", side + 1, "player", member,
                          schema.PLAYER_TAG_KEY))):
            self.crew.SetMemberName(side + 1, member, text)

    def _onStockChanged(self, side: int, member: int, value: int):
        if self._building:
            return
        if not self.crew.SetMemberStock(side + 1, member, value):
            self._ScheduleRender()

    def _onActiveToggled(self, side: int, member: int, checked: bool):
        # One member per crew is playing (CrewBattleService): marking one
        # replaces whoever was, and the render unmarks them. Turning the
        # marker off means nobody is in.
        if self._building:
            return
        if checked:
            written = self.crew.SelectActiveMember(side + 1, member)
        else:
            written = self.crew.ReleaseActiveMember(side + 1, member)
        if not written:
            self._ScheduleRender()

    def _onMatchFieldFinished(self, field, combo):
        # `field` is one of StateSchema.SCORE_MATCH_FIELDS -- the same three
        # keys the 1v1 / team boards' match bar writes, through the same
        # operation, so board 3 is not a second reading of what "phase" means.
        text = combo.currentText()
        if self._Finished(combo, text, StateManager.Get(self._key(field))):
            AutonaActions.instance.SetMatchField(
                self.scoreboardNumber, field, text)

    def CommitFocusedEdit(self):
        """Finish the edit in progress on this board before a swap (the
        SWAP button, and the Web's through the board host).

        The swap exchanges the state, and a field being typed in is one the
        render leaves alone -- the typed text would stay where it is. So the
        field is left first: Qt then finishes it (editingFinished) exactly as
        when the operator clicks elsewhere, and the text is in its member's
        slot before the crews move, as when the board swapped its rows (and
        what AutonaScoreboardWidget.CommitFocusedEdit does for boards 1/2).
        An open completer popup is closed first: losing the focus with it
        open finishes nothing.
        """
        focus = QApplication.focusWidget()
        widget = focus
        while widget is not None and widget is not self:
            widget = widget.parentWidget()
        if focus is None or widget is not self:
            return
        popup = QApplication.activePopupWidget()
        completer = focus.completer() if popup is not None \
            and isinstance(focus, (QLineEdit, QComboBox)) else None
        if completer is not None and completer.popup() is popup:
            popup.hide()
        focus.clearFocus()

    # ── Board operations (the buttons, and the set payload signal) ─────

    def SwapTeams(self):
        self.CommitFocusedEdit()
        self.crew.SwapTeams()

    def ResetScore(self):
        # Every member back to full stocks and nobody playing.
        self.crew.ResetStocks()

    def ChangeSetData(self, data):
        # post_score() can route a set payload here. Crew
        # has no set-loading UI: the generic fields are persisted if present
        # and the rest is ignored -- tolerant by design so generic callers
        # don't crash board 3. The rules are CrewBattleService's, and they
        # are deliberately NOT SetLoadService's (see that module's
        # docstring).
        self.crew.ApplySetData(data)

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
