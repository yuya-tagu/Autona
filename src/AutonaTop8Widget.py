"""TOP 8 tab: edit the Top 8 double elimination bracket and get it into OBS.

Replaces the old free-form Bracket dock (AutonaBracketWidget /
AutonaBracketView). The bracket is now a fixed FGC-standard Top 8
(4 winners-side + 4 losers-side starters) whose topology lives in
core/Top8Model.py; this panel only edits players and match results.

* **Players** — 8 slot-fixed cards (name / sponsor / country / character).
* **Matches** — one score row per match; the staged scores are applied
  (反映) and the winners propagated by Top8Service.ApplyScores.
* **Start.gg** — the load button asks core to import the tournament's final
  double elimination phase group (Top8Service.RefreshFromProvider).
* **Preview / Add to OBS** — design pulldown (immediate reload, Board-tab
  spec) over ``layout/bracket_top8*/``, embedded live preview of the
  selected overlay plus the browser-source URL, mirroring
  AutonaBoardWidget's workflow (URL instead of local file: the overlay
  needs the web server's Socket.IO feed).

State contract: everything under ``top8.*`` (see src/core/Top8Model.py). Every
write goes through ``src/core/Top8Service.py``, and the panel shows the state
as it is (``_RenderFromState``), whoever wrote it (ADR-005 S3e-8): the
Start.gg import -- from this panel's button, the hotkey or the web routes,
built panel or not -- runs in core on the provider's worker, and an input
asks the service for the one field it edits (``SetPlayerText`` /
``SetPlayerCountry`` / ``SetPlayerCharacter``). A field the operator is in is
left alone until they leave it, and finishing it does not put back the text
a render had to leave there (the player cards' rule,
Helpers.AutonaQtHelper.HeldBackText). The country and character still come
out of the combos' Qt rows (S4 replaces them with identifiers). The score
spin boxes are staged: they show the state's scores when the matches change,
and are published by 反映 only.
"""

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from shiboken6 import Shiboken

from .core.StateManager import StateManager
from .core.SettingsManager import SettingsManager
from .core.DictHelper import deep_clone
from .core import LayoutCatalog
from .AutonaLayoutPreview import AutonaLayoutPreview, ObsUrl
from .AutonaGameAssetManager import AutonaGameAssetManager
from .StateManagerBridge import StateManagerBridge
from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Helpers.AutonaQtHelper import HeldBackText, NoIconProxyModel
from .AutonaFeatureColors import FEATURE_TOP8, MakeFeatureColorCard
from .core import Top8Service
from .core import Top8Model as top8
from .core import StateSchema as schema
from . import ui_constants as UI
from . import ui_layout as L

# Designs: the catalog's top8 layouts (layout/bracket_top8/ and any variant
# declaring the kind; read at launch, the pulldown spec matches the Board
# tab).
LAYOUT_KIND = LayoutCatalog.KIND_TOP8
DEFAULT_DESIGN = "bracket_top8"

# Sidebar section -> match ids, in display order.
MATCH_SECTIONS = [
    ("Winners", ["wsf1", "wsf2", "wf"]),
    ("Losers", ["lr1a", "lr1b", "lqf1", "lqf2", "lsf", "lf"]),
    ("Grand Final", ["gf", "gf_reset"]),
]

# Compact row labels for the score table. The long names stay in
# top8.matches.{id}.name (the OBS overlay renders those).
SHORT_MATCH_LABELS = {
    "wsf1": "WSF1", "wsf2": "WSF2", "wf": "WF",
    "lr1a": "LR1A", "lr1b": "LR1B",
    "lqf1": "LQF1", "lqf2": "LQF2", "lsf": "LSF", "lf": "LF",
    "gf": "GF", "gf_reset": "RESET",
}

PLAYER_CARD_WIDTH = 240


class AutonaTop8Widget(QDockWidget):
    instance: "AutonaTop8Widget" = None

    def __init__(self, *args):
        super().__init__(*args)
        # windowTitle is the visible tab label (objectName is set in Autona.py).
        self.setWindowTitle(QApplication.translate("app", "Top 8"))

        # State -> inputs (_RenderFromState): one pass queued at a time;
        # by text field, what it last showed in step with the state and the
        # text a pass had to leave in one the operator was in
        # (HeldBackText); the inputs a pass left, to come back to once they
        # are left; the matches the score spin boxes last showed (they are
        # staged, so they follow the state only when the matches change).
        self._renderPending = False
        self._rendering = False
        self._heldBack = HeldBackText()
        self._focusSkipped = []
        self._shownMatches = None
        # The operator emptied the caption field (it then stands for the
        # default, shown as its placeholder) -- see _RenderFromState.
        self._phaseLabelCleared = False
        # True while the shared country model is cleared and refilled (see
        # _onCountriesAboutToReload / _rebindCountryModels).
        self._holdCountryWrites = False
        self._previewLoaded = False
        self._designs = LayoutCatalog.Layouts([LAYOUT_KIND])
        # The colour combo's model when its character has no skins: one,
        # kept, instead of a new empty model per render (agent-pitfalls #13).
        self._noSkins = QStandardItemModel(self)

        # NOTE: the top8.* seed / normalisation that used to run here moved
        # to Window._SeedPanelState() (startup Step 4b-2). This panel is
        # built on first reveal now, and the OBS overlay reads top8.* from
        # launch, so the state must be normalised whether or not the panel
        # is ever opened. _RenderFromState() below reads the seeded state.

        # Two panes, each with its own scroll: editing (Start.gg / players /
        # scores / OBS) on the left, the live preview on the right.
        # Build the design selector (and then the preview) first: the OBS
        # group below reads the selected design for its URL field.
        designSelector = self._buildDesignSelector()
        preview = self._buildPreview()

        leftContent = QWidget()
        leftCol = QVBoxLayout(leftContent)
        leftCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        leftCol.setSpacing(UI.PANEL_MARGIN)
        leftCol.addWidget(self._buildPlayersGroup())
        leftCol.addWidget(self._buildMatchesGroup())
        leftCol.addWidget(MakeFeatureColorCard(FEATURE_TOP8, "TOP 8 の色"))
        leftCol.addWidget(self._buildObsGroup())
        leftCol.addStretch(1)

        leftScroll = QScrollArea()
        leftScroll.setWidgetResizable(True)
        leftScroll.setWidget(leftContent)
        leftScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Load button pinned above the scrolling area (RESULT-tab
        # structure, same label): stays visible while the cards scroll.
        # It asks core to import the tournament's final double elimination
        # phase (Top8Service.RefreshFromProvider); the cards show the result
        # when it lands.
        self.btLoadTop8 = QPushButton(
            QApplication.translate("app", "Load tournament results"))
        L.tag_button(self.btLoadTop8, L.BUTTON_PRIMARY)
        self.btLoadTop8.clicked.connect(self._onLoadTop8)
        loadRow = QVBoxLayout()
        loadRow.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, 0)
        loadRow.addWidget(self.btLoadTop8)

        leftPane = QWidget()
        leftPane.setFixedWidth(L.LEFT_PANE_WIDTH)
        leftPaneCol = QVBoxLayout(leftPane)
        leftPaneCol.setContentsMargins(0, 0, 0, 0)
        leftPaneCol.setSpacing(0)
        leftPaneCol.addLayout(loadRow)
        leftPaneCol.addWidget(leftScroll, 1)

        rightContent = QWidget()
        rightCol = QVBoxLayout(rightContent)
        rightCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        rightCol.setSpacing(UI.PANEL_MARGIN)
        rightCol.addLayout(designSelector)
        rightCol.addWidget(preview, 1)
        rightCol.addLayout(preview.BuildActionButtons())

        rightScroll = QScrollArea()
        rightScroll.setWidgetResizable(True)
        rightScroll.setWidget(rightContent)

        panes = QWidget()
        panesLayout = QHBoxLayout(panes)
        panesLayout.setContentsMargins(0, 0, 0, 0)
        panesLayout.setSpacing(0)
        panesLayout.addWidget(leftPane)
        panesLayout.addWidget(rightScroll, 1)
        self.setWidget(panes)

        self._RenderFromState()

        # Bound methods: Qt drops the connections with the panel.
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._onGameChanged)
        AutonaCountryHelper.signals.countriesAboutToReload.connect(
            self._onCountriesAboutToReload)
        AutonaCountryHelper.signals.countriesUpdated.connect(
            self._rebindCountryModels)
        StateManagerBridge.instance.state_updated.connect(self._onStateDelta)
        StateManagerBridge.instance.state_big_change.connect(
            self._ScheduleRender)
        QApplication.instance().focusChanged.connect(self._onFocusChanged)

        # NOTE: Ctrl+Shift+P (refresh_phase_group) is routed to the core
        # import by AutonaHotkeys.ConnectActions() (ADR-005 S3e-8): it does
        # not need this panel, and this panel renders what it writes.

        AutonaTop8Widget.instance = self

    # ── Players ──────────────────────────────────────────────────────
    def _buildPlayersGroup(self):
        group = L.make_card("選手（スロット固定）")

        note = QLabel(
            "スロット 1-4 は Winners、5-8 は Losers スタートです。"
            "対戦カードを変えるには選手を入れ替えてください。")
        note.setWordWrap(True)
        note.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(note)

        _pc = UI.palette()
        cardStyle = (
            f"QFrame#top8PlayerCard {{"
            f" background-color: {_pc['bg_tertiary']};"
            f" border: 1px solid {_pc['border_subtle']};"
            f" border-radius: {UI.RADIUS_MD}px;"
            f" }}")

        cards = QVBoxLayout()
        cards.setSpacing(6)
        group.layout().addLayout(cards)

        # slot(str) -> {"name", "sponsor", "country", "char", "color"}
        self.playerInputs = {}

        for slot in top8.PLAYER_SLOTS:
            card = QFrame()
            card.setObjectName("top8PlayerCard")
            card.setStyleSheet(cardStyle)
            card.setFixedWidth(PLAYER_CARD_WIDTH)
            fields = QGridLayout(card)
            fields.setContentsMargins(8, 8, 8, 8)
            fields.setVerticalSpacing(2)
            fields.setHorizontalSpacing(6)

            header = QLabel(f"{slot} · {top8.SLOT_POSITIONS[slot]}")
            header.setStyleSheet(UI.secondary_label_style())

            name_edit = QLineEdit()
            name_edit.setPlaceholderText(
                QApplication.translate("app", "Player Name"))

            sponsor_edit = QLineEdit()
            sponsor_edit.setPlaceholderText(
                QApplication.translate("app", "Sponsor"))
            sponsor_edit.setFixedWidth(80)

            country_combo = self._makeCountryCombo()
            char_combo = self._makeCharCombo()
            color_combo = self._makeColorCombo()

            fields.addWidget(header,        0, 0, 1, 2)
            # Sponsor tag left of the player name ("[Sponsor] PlayerName"),
            # matching the scoreboard / result settings display order. Nested
            # HBox spanning both columns so the fixed-width sponsor doesn't
            # inherit the stretching char-combo column below.
            nameRow = QHBoxLayout()
            nameRow.setSpacing(6)
            nameRow.addWidget(sponsor_edit)
            nameRow.addWidget(name_edit, 1)
            fields.addLayout(nameRow,       1, 0, 1, 2)
            fields.addWidget(country_combo, 2, 0, 1, 2)
            fields.addWidget(char_combo,    3, 0)
            fields.addWidget(color_combo,   3, 1)
            fields.setColumnStretch(0, 1)

            cards.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)

            # Each input asks for its own field only (Top8Service), so an
            # edit never writes back what another input is still showing.
            name_edit.editingFinished.connect(
                lambda s=slot: self._onTextFinished(s, "name"))
            sponsor_edit.editingFinished.connect(
                lambda s=slot: self._onTextFinished(s, "sponsor"))
            country_combo.currentIndexChanged.connect(
                lambda _index, s=slot: self._onCountryChanged(s))
            char_combo.currentIndexChanged.connect(
                lambda _index, s=slot: self._onCharComboChanged(s))
            color_combo.currentIndexChanged.connect(
                lambda _index, s=slot: self._onSkinChanged(s))

            self.playerInputs[slot] = {
                "name": name_edit,
                "sponsor": sponsor_edit,
                "country": country_combo,
                "char": char_combo,
                "color": color_combo,
            }

        return group

    def _makeCountryCombo(self):
        combo = QComboBox()
        combo.setEditable(True)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.completer().setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        # countryModel is loaded synchronously in Autona.py before panels are
        # built, but its loader swallows failures (model stays None) —
        # countriesUpdated re-binds once data arrives.
        if AutonaCountryHelper.countryModel is not None:
            combo.setModel(AutonaCountryHelper.countryModel)
        combo.setIconSize(QSize(16, 16))
        combo.setFixedHeight(22)
        combo.setCurrentIndex(0)
        combo.lineEdit().setPlaceholderText(
            QApplication.translate("app", "Country"))
        return combo

    def _onCountriesAboutToReload(self):
        """The shared country model is about to be cleared and refilled in
        place. Each country combo reports that as index changes, and each of
        those would write its slot's momentary (blank) country. Hold the
        country writes until _rebindCountryModels has put the combos back on
        the state."""
        self._holdCountryWrites = True

    def _rebindCountryModels(self):
        """countriesUpdated: attach the shared model where it is not yet (a
        panel built before the first load) and re-select every slot's country
        from top8.* -- the state decides what the combos show, never the
        other way round, so neither a first load nor a reload writes."""
        try:
            model = AutonaCountryHelper.countryModel
            players = StateManager.Get(Top8Service.PLAYERS_PATH) or {}
            for slot, w in self.playerInputs.items():
                combo = w["country"]
                if model is not None and combo.model() is not model:
                    with QSignalBlocker(combo):
                        combo.setModel(model)
                player = players.get(slot) or {}
                self._selectCountry(combo,
                                    (player.get("country") or {}).get("code"))
        finally:
            self._holdCountryWrites = False

    def _makeCharCombo(self):
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(100)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.completer().setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        proxy = NoIconProxyModel(combo)
        proxy.setSourceModel(AutonaGameAssetManager.instance.characterModel)
        combo.setModel(proxy)
        combo.setIconSize(QSize(0, 0))
        combo.setFixedHeight(22)
        combo.setCurrentIndex(-1)
        combo.lineEdit().setPlaceholderText(
            QApplication.translate("app", "Main character"))
        return combo

    def _makeColorCombo(self):
        combo = QComboBox()
        combo.setIconSize(QSize(16, 16))
        combo.setFixedHeight(22)
        combo.setMinimumWidth(48)
        combo.setMaximumWidth(80)
        view = QListView()
        view.setIconSize(QSize(24, 24))
        combo.setView(view)
        return combo

    def _onGameChanged(self):
        """Re-bind character models when the game changes (the model object
        is replaced). Mirrors AutonaResultSettingsWidget._onGameChanged.
        Writes nothing: the combos are emptied with their signals blocked,
        and the render that follows shows the state's characters where the
        new game has them."""
        for w in self.playerInputs.values():
            char_combo = w["char"]
            with QSignalBlocker(char_combo):
                proxy = NoIconProxyModel(char_combo)
                proxy.setSourceModel(
                    AutonaGameAssetManager.instance.characterModel)
                char_combo.setModel(proxy)
                char_combo.setIconSize(QSize(0, 0))
                char_combo.setCurrentIndex(-1)
            with QSignalBlocker(w["color"]):
                w["color"].setModel(self._noSkins)
        self._ScheduleRender()

    def _loadSkins(self, char_combo, color_combo, char=None):
        """Point the colour combo at the selected character's skins and pick
        the one whose data is `char` (the first when none is), with its
        signals blocked: choosing what to show is not an edit."""
        data = char_combo.currentData(Qt.ItemDataRole.UserRole)
        skin_model = None
        if data and data.get("en_name"):
            skin_model = AutonaGameAssetManager.instance.skinModels.get(
                data.get("en_name"))
        with QSignalBlocker(color_combo):
            if not skin_model:
                color_combo.setModel(self._noSkins)
                return
            color_combo.setModel(skin_model)
            index = 0
            if char:
                for row in range(skin_model.rowCount()):
                    if skin_model.index(row, 0).data(
                            Qt.ItemDataRole.UserRole) == char:
                        index = row
                        break
            color_combo.setCurrentIndex(index)

    def _onTextFinished(self, slot, field):
        """A name / sponsor field finished an edit: write that one field,
        unless the text is not news -- what the state already holds, or the
        text a render had to leave in the field while the operator was in it
        (the state moved on underneath it, and writing it back would put the
        old value over the new one). The player cards' rule (HeldBackText)."""
        if self._rendering:
            return
        edit = self.playerInputs[slot][field]
        text = edit.text()
        stored = StateManager.Get(f"{Top8Service.PLAYERS_PATH}.{slot}.{field}")
        if self._heldBack.IsEdit(edit, text, stored == text.strip()):
            Top8Service.SetPlayerText(slot, field, text)

    def _onCountryChanged(self, slot):
        if self._rendering or self._holdCountryWrites:
            return
        combo = self.playerInputs[slot]["country"]
        Top8Service.SetPlayerCountry(
            slot, combo.currentData(Qt.ItemDataRole.UserRole) or {})

    def _onCharComboChanged(self, slot):
        if self._rendering:
            return
        w = self.playerInputs[slot]
        self._loadSkins(w["char"], w["color"])
        Top8Service.SetPlayerCharacter(
            slot, self._resolveCharData(w["char"], w["color"]))

    def _onSkinChanged(self, slot):
        if self._rendering:
            return
        w = self.playerInputs[slot]
        Top8Service.SetPlayerCharacter(
            slot, self._resolveCharData(w["char"], w["color"]))

    def _resolveCharData(self, char_combo, color_combo):
        skin_data = color_combo.currentData(Qt.ItemDataRole.UserRole)
        if skin_data and skin_data.get("assets"):
            return skin_data
        char_data = char_combo.currentData(Qt.ItemDataRole.UserRole)
        return char_data or {}

    def _onLoadTop8(self):
        # Answers at once; the import runs on the provider's worker and the
        # cards follow the state when it lands (nothing happens when no
        # tournament is loaded, or one is already being imported).
        Top8Service.RefreshFromProvider()

    # ── Matches ──────────────────────────────────────────────────────
    def _buildMatchesGroup(self):
        group = L.make_card("試合スコア")

        labelRow = QHBoxLayout()
        labelRow.addWidget(QLabel("表示ラベル"))
        self.phaseLabelEdit = QLineEdit()
        self.phaseLabelEdit.setPlaceholderText(top8.DEFAULT_PHASE_LABEL)
        self.phaseLabelEdit.editingFinished.connect(self._onPhaseLabelChanged)
        labelRow.addWidget(self.phaseLabelEdit, 1)
        group.layout().addLayout(labelRow)

        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        grid.setHorizontalSpacing(6)
        group.layout().addLayout(grid)

        # match_id -> {"round", "p1", "s1", "s2", "p2"}
        self.matchRows = {}
        row = 0
        for section, match_ids in MATCH_SECTIONS:
            sectionLabel = QLabel(section)
            L.apply_section_title(sectionLabel)
            grid.addWidget(sectionLabel, row, 0, 1, 5)
            row += 1
            for match_id in match_ids:
                roundLabel = QLabel(SHORT_MATCH_LABELS[match_id])
                roundLabel.setToolTip(top8.DEFAULT_ROUND_NAMES[match_id])
                roundLabel.setStyleSheet(UI.secondary_label_style())
                roundLabel.setFixedWidth(48)

                # Ignored horizontal policy: long names clip instead of
                # widening the grid past the fixed-width pane
                # (L.LEFT_PANE_WIDTH).
                p1 = QLabel("TBD")
                p1.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                p2 = QLabel("TBD")
                for p in (p1, p2):
                    p.setMinimumWidth(40)
                    p.setSizePolicy(
                        QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

                s1 = QSpinBox()
                s2 = QSpinBox()
                for s in (s1, s2):
                    s.setRange(0, 99)
                    s.setFixedWidth(40)
                    s.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    # The 40px box has no room for the stepper arrows —
                    # they were drawn over the digits. Type / arrow keys /
                    # wheel still step the value.
                    s.setButtonSymbols(
                        QAbstractSpinBox.ButtonSymbols.NoButtons)

                grid.addWidget(roundLabel, row, 0)
                grid.addWidget(p1, row, 1)
                grid.addWidget(s1, row, 2)
                grid.addWidget(s2, row, 3)
                grid.addWidget(p2, row, 4)
                grid.setColumnStretch(1, 1)
                grid.setColumnStretch(4, 1)
                row += 1

                self.matchRows[match_id] = {
                    "round": roundLabel,
                    "p1": p1, "s1": s1, "s2": s2, "p2": p2,
                }

        # Negative action left, positive action right — the app-wide
        # button-pair convention.
        btRow = QHBoxLayout()
        btRow.addStretch(1)
        self.btResetAll = QPushButton("全リセット")
        L.tag_button(self.btResetAll, L.BUTTON_DANGER)
        self.btResetAll.clicked.connect(self._onResetAll)
        btRow.addWidget(self.btResetAll)
        self.btApplyScores = QPushButton(
            QApplication.translate("app", "Apply"))
        L.tag_button(self.btApplyScores, L.BUTTON_PRIMARY)
        self.btApplyScores.clicked.connect(self._onApplyScores)
        btRow.addWidget(self.btApplyScores)
        group.layout().addLayout(btRow)

        return group

    def _onPhaseLabelChanged(self):
        """The phase caption finished an edit. The "an empty field means
        TOP 8" rule is the service's; the field is left as the operator
        typed it (an empty one shows the placeholder), as before. Written
        unless it is not news (HeldBackText, as the player fields)."""
        if self._rendering:
            return
        edit = self.phaseLabelEdit
        text = edit.text()
        self._phaseLabelCleared = not text.strip()
        stored = StateManager.Get(Top8Service.PHASE_LABEL_PATH)
        holds = stored == (text.strip() or top8.DEFAULT_PHASE_LABEL)
        if self._heldBack.IsEdit(edit, text, holds):
            Top8Service.SetPhaseLabel(text)

    def _onApplyScores(self):
        """反映 button: hand the staged spin values to the service.

        Merging them into the published matches, deciding what counts as
        finished and propagating the winners is Top8Service.ApplyScores. The
        rows show the state afterwards even when it did not change: Recompute
        may have reset what was staged on a match that is not reachable.
        """
        Top8Service.ApplyScores({
            match_id: [row["s1"].value(), row["s2"].value()]
            for match_id, row in self.matchRows.items()
        })
        self._RefreshScores()

    def _onResetAll(self):
        # Button-click context, so a modal confirm is safe here (the
        # no-modal-in-signal rule targets QSpinBox.valueChanged etc.).
        answer = QMessageBox.question(
            self,
            QApplication.translate("app", "Top 8"),
            "全試合のスコアと確定状態をリセットします。よろしいですか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        Top8Service.ResetMatches()
        self._RefreshScores()

    def _RefreshScores(self):
        """Have the next render put the state's scores in the spin boxes,
        whether or not the matches changed."""
        self._shownMatches = None
        self._ScheduleRender()

    @staticmethod
    def _playerLabel(slot, players):
        if not slot:
            return "TBD"
        player = players.get(str(slot)) if isinstance(players, dict) else None
        name = ((player or {}).get("name") or "").strip()
        return name if name else f"({slot})"

    def _RenderMatches(self, matches, players):
        """The score rows: the names of whoever the state puts in each match
        and, when the matches changed (an import, 反映, リセット, another
        client), their scores. The spin boxes are staged otherwise -- a
        player edit must not clobber scores not yet applied."""
        updateScores = matches != self._shownMatches
        for match_id, row in self.matchRows.items():
            m = matches.get(match_id) or top8.DefaultMatch(match_id)
            slots = m.get("slots", [0, 0])
            row["p1"].setText(self._playerLabel(slots[0], players))
            row["p2"].setText(self._playerLabel(slots[1], players))
            if updateScores:
                score = m.get("score", [0, 0])
                for key, value in (("s1", score[0]), ("s2", score[1])):
                    with QSignalBlocker(row[key]):
                        row[key].setValue(value)

            enabled = True
            if match_id == "gf_reset":
                enabled = bool(m.get("active"))
            for key in ("p1", "s1", "s2", "p2", "round"):
                row[key].setEnabled(enabled)
        self._shownMatches = matches

    # ── State -> UI ──────────────────────────────────────────────────
    def _onStateDelta(self, payload):
        # A flat delta renders its key path as a Python list, so the Top 8's
        # changes contain "['top8'" verbatim.
        if "['top8'" in str(payload):
            self._ScheduleRender()

    def _ScheduleRender(self):
        """Queue one render pass. Deferred, so nothing is redrawn inside the
        notification or the edit that caused it (agent-pitfalls #9), and
        coalesced. The panel is the timer's context, so a panel torn down
        first takes the pending pass with it."""
        if self._renderPending:
            return
        self._renderPending = True
        QTimer.singleShot(0, self, self._RenderFromState)

    def _RenderFromState(self):
        """Show top8.* as the state holds it, whoever wrote it (an import
        from any shell entry point, this panel's own edits). Writes nothing:
        every input is set with its signals blocked. An input the operator
        is in is left alone and shown once they leave it."""
        self._renderPending = False
        if self._rendering:
            return
        self._rendering = True
        try:
            self._focusSkipped = []
            with StateManager.lock:
                state = deep_clone(StateManager.Get(schema.TOP8_ROOT) or {})
            if not isinstance(state, dict):
                state = {}

            label = state.get(schema.TOP8_PHASE_LABEL_KEY)
            label = label if isinstance(label, str) and label \
                else top8.DEFAULT_PHASE_LABEL
            edit = self.phaseLabelEdit
            if label == top8.DEFAULT_PHASE_LABEL and self._phaseLabelCleared                     and not edit.text().strip():
                # A field the operator emptied stands for the default (its
                # placeholder shows it) and stays empty, as it always did.
                # Anywhere else -- a panel just built, included -- the
                # caption is the field's text, "TOP 8" too.
                self._heldBack.Shown(edit, edit.text())
            else:
                self._phaseLabelCleared = False
                self._RenderText(edit, label)

            players = state.get(schema.TOP8_PLAYERS_KEY)
            players = players if isinstance(players, dict) else {}
            for slot, w in self.playerInputs.items():
                player = players.get(slot)
                player = player if isinstance(player, dict) else {}
                for field in Top8Service.PLAYER_TEXT_FIELDS:
                    value = player.get(field, "")
                    self._RenderText(
                        w[field], value if isinstance(value, str) else "")
                country = player.get("country")
                self._RenderCountry(
                    w["country"], country if isinstance(country, dict) else {})
                characters = player.get("characters")
                char = characters.get("1") \
                    if isinstance(characters, dict) else None
                self._RenderCharacter(
                    w["char"], w["color"], char if isinstance(char, dict) else {})

            matches = state.get(schema.TOP8_MATCHES_KEY)
            self._RenderMatches(
                matches if isinstance(matches, dict) else {}, players)
        finally:
            self._rendering = False

    def _RenderText(self, edit, text):
        if edit.text() == text:
            self._heldBack.Shown(edit, text)
            return
        if edit.hasFocus():
            # Left as it is while the operator is in it. Its text is held
            # back -- not written back when the field is finished -- if it
            # is still what the field last showed (the value the state has
            # moved on from), or if this field's own value was just written
            # elsewhere while they typed (HeldBackText). Any other typing is
            # their edit, written when they finish it.
            self._heldBack.LeftAlone(edit, edit.text(), text)
            self._SkipFocused(edit)
            return
        with QSignalBlocker(edit):
            edit.setText(text)
        self._heldBack.Shown(edit, text)

    def _RenderCountry(self, combo, country):
        code = country.get("code") or None
        shown = combo.currentData(Qt.ItemDataRole.UserRole)
        shownCode = (shown.get("code") if isinstance(shown, dict) else None) \
            or None
        if shownCode == code:
            return
        if self._HasFocus(combo):
            self._SkipFocused(combo)
            return
        self._selectCountry(combo, code)

    def _RenderCharacter(self, char_combo, color_combo, char):
        """Select the stored character and its skin, unless the combos
        already resolve to it (the panel's own pick, written as it shows)."""
        if self._resolveCharData(char_combo, color_combo) == char:
            return
        if self._HasFocus(char_combo) or self._HasFocus(color_combo):
            self._SkipFocused(char_combo)
            self._SkipFocused(color_combo)
            return
        self._selectCharacter(char_combo, color_combo, char)

    def _HasFocus(self, widget):
        """The widget or something inside it (an editable combo's line edit,
        its popup) has the focus."""
        focus = QApplication.focusWidget()
        while focus is not None:
            if focus is widget:
                return True
            focus = focus.parentWidget()
        return False

    def _SkipFocused(self, widget):
        if widget not in self._focusSkipped:
            self._focusSkipped.append(widget)

    def _onFocusChanged(self, old, new):
        """QApplication.focusChanged: once the operator has left every input
        the last pass had to skip, show the state in them (deferred: the
        field's own editingFinished runs first)."""
        if not self._focusSkipped:
            return
        skipped = [w for w in self._focusSkipped if Shiboken.isValid(w)]
        if any(self._HasFocus(w) for w in skipped):
            self._focusSkipped = skipped
            return
        self._focusSkipped = []
        self._ScheduleRender()

    @staticmethod
    def _selectCountry(combo, code):
        model = AutonaCountryHelper.countryModel
        if model is None:
            return
        combo.blockSignals(True)
        index = 0
        if code:
            for i in range(model.rowCount()):
                data = model.item(i).data(Qt.ItemDataRole.UserRole)
                if data and data.get("code") == code:
                    index = i
                    break
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _selectCharacter(self, char_combo, color_combo, char):
        """Select the stored character (matched by en_name/codename) and load
        its skins, on the stored skin when the state holds one."""
        with QSignalBlocker(char_combo):
            target = None
            if char:
                target = char.get("en_name") or char.get("codename")
            index = -1
            if target:
                model = char_combo.model()
                for row in range(model.rowCount()):
                    data = model.data(
                        model.index(row, 0), Qt.ItemDataRole.UserRole)
                    if data and (data.get("en_name") == target
                                 or data.get("codename") == target):
                        index = row
                        break
            char_combo.setCurrentIndex(index)
        if index >= 0:
            self._loadSkins(char_combo, color_combo, char)
        else:
            with QSignalBlocker(color_combo):
                color_combo.setModel(self._noSkins)

    # ── Preview ──────────────────────────────────────────────────────
    def _buildDesignSelector(self):
        # Right-column selector above the preview: section-title label +
        # full-width combo, matching the RESULT tab. Selecting a design
        # reloads the preview immediately, no update button. Single entry
        # today; further top8 layouts appear automatically.
        col = QVBoxLayout()
        col.setSpacing(UI.SPACE_SM)
        label = QLabel("TOP 8 のデザインを選択")
        L.apply_section_title(label)
        col.addWidget(label)

        self.designCombo = QComboBox()
        for entry in self._designs:
            self.designCombo.addItem(entry["displayName"], entry["id"])
        saved = SettingsManager.Get("top8.selected_design", DEFAULT_DESIGN)
        idx = self.designCombo.findData(saved)
        if idx < 0:
            idx = self.designCombo.findData(DEFAULT_DESIGN)
        if idx < 0 and self.designCombo.count() > 0:
            idx = 0
        if idx >= 0:
            self.designCombo.setCurrentIndex(idx)
        # Connect after the initial selection so it doesn't fire on startup.
        self.designCombo.currentIndexChanged.connect(self._onDesignChanged)
        col.addWidget(self.designCombo)
        return col

    def _buildPreview(self):
        # Bare preview widget, no card wrapper — the RESULT tab's right-pane
        # structure is the reference for every preview panel.
        self.preview = AutonaLayoutPreview(
            self._currentDesign(), entry=self._currentEntry())
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return self.preview

    def _currentDesign(self):
        if self.designCombo.count() == 0:
            return None
        return self.designCombo.currentData()

    def _currentEntry(self):
        """Entry file of the selected design (catalog ``entry``)."""
        design = self._currentDesign()
        return next((entry["entry"] for entry in self._designs
                     if entry["id"] == design), LayoutCatalog.DEFAULT_ENTRY)

    def _onDesignChanged(self, _idx):
        design = self._currentDesign()
        if not design:
            return
        SettingsManager.Set("top8.selected_design", design)
        self.obsUrlField.setText(self._obsUrl())
        if self._previewLoaded:
            self.preview.load(design, self._currentEntry())

    def showEvent(self, e):
        super().showEvent(e)
        # Lazy-load on first reveal: by then the web server is up, so the
        # preview's HTTP fetch + Socket.IO connect succeed.
        if not self._previewLoaded:
            self._previewLoaded = True
            self.preview.load()

    # ── Add to OBS ───────────────────────────────────────────────────
    def _buildObsGroup(self):
        group = L.make_card("OBS に追加する方法")

        steps = [
            "OBS で「ソース」→「ブラウザ」を追加します。",
            "「URL」に下のアドレスを指定します（「コピー」を使用）。",
            "サイズを 幅 1920 / 高さ 1080 に設定します。",
        ]
        for i, step in enumerate(steps, start=1):
            label = QLabel(f"{i}. " + step)
            label.setWordWrap(True)
            group.layout().addWidget(label)

        urlRow = QHBoxLayout()
        self.obsUrlField = QLineEdit()
        self.obsUrlField.setReadOnly(True)
        self.obsUrlField.setText(self._obsUrl())
        urlRow.addWidget(self.obsUrlField, 1)
        btCopy = QPushButton("コピー")
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.obsUrlField.text()))
        urlRow.addWidget(btCopy)
        group.layout().addLayout(urlRow)

        note = QLabel(
            "Autona を起動したままにすると、スコアボードと同様に選手やスコアの"
            "編集が OBS 上のブラケットへリアルタイムに反映されます。")
        note.setWordWrap(True)
        group.layout().addWidget(note)
        return group

    def _obsUrl(self):
        return ObsUrl(self._currentDesign() or DEFAULT_DESIGN,
                      self._currentEntry())
