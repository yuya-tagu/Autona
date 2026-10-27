"""Commentary tab: edit the caster lineup and get it onto the stream.

Full rework mirroring AutonaTop8Widget's list + live-preview pattern:

* **Casters** — 12 cards (name + team / role free text with a candidate
  completer + icon pulldown / note + Twitter), drag-reorderable by
  their hamburger grip (result-settings pattern). Slot = card position:
  a drop moves the data between slots. A per-slot "配信に表示" toggle
  picks which casters (max 4) the ``layout/commentators/`` overlay
  renders, top card leftmost.
* **Preview / Add to OBS** — design pulldown (immediate reload, Board-tab
  spec) over the layout catalog's ``commentary`` entries
  (``src/core/LayoutCatalog.py``), embedded live preview of the selected
  overlay, and the browser-source URL.

State contract: everything under ``commentary.{1..12}`` (see
src/core/CommentaryModel.py). Every write goes through
``core/CommentaryService.py`` (ADR-005 S3e-2): an input asks it for the one
field it edits (``SetField``), a drag for the new order (``Reorder``), a
completer pick for the player's fields (``ApplyPlayerData``), and the cards
show the state as it is (``_RenderFromState``), whoever wrote it -- the
Web's ``/update-commentary-<N>`` and ``/load-commentator-from-tag-<N>`` call
the same service with or without this panel. A field the operator is in is
left alone until they leave it, and finishing it does not put back the text
a render had to leave there -- what the operator typed is their edit, and is
written, unless that very field was written elsewhere meanwhile (the player
cards' rule, Helpers.AutonaQtHelper.HeldBackText). The state persists
across restarts; Window._SeedPanelState() normalizes whatever LoadState
restored (including the legacy pre-rework schema) at launch, before this
panel is built.
"""

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from shiboken6 import Shiboken

from .core.StateManager import StateManager
from .core.SettingsManager import SettingsManager
from .core import LayoutCatalog
from .AutonaPlayerDB import AutonaPlayerDB
from .AutonaLayoutPreview import AutonaLayoutPreview, ObsUrl
from .AutonaFeatureColors import FEATURE_COMMENTARY, MakeFeatureColorCard
from .StateManagerBridge import StateManagerBridge
from .Helpers.AutonaQtHelper import HeldBackText
from .core import StateSchema as schema
from .core import CommentaryService
from .core import CommentaryModel as comms
from .core.DictHelper import deep_clone
from . import ui_constants as UI
from . import ui_layout as L

# Designs: the catalog's commentary layouts (layout/commentators/ and any
# variant declaring the kind; read at launch, the pulldown spec matches the
# Board tab).
LAYOUT_KIND = LayoutCatalog.KIND_COMMENTARY
DEFAULT_DESIGN = "commentators"

CASTER_CARD_WIDTH = 280


class AutonaCommentaryWidget(QDockWidget):
    instance: "AutonaCommentaryWidget" = None

    def __init__(self, *args):
        super().__init__(*args)
        self.setWindowTitle(QApplication.translate("app", "Commentary"))

        # State -> cards (_RenderFromState): one pass queued at a time; by
        # field, what each field last showed in step with the state and the
        # text a pass had to leave in one the operator was in
        # (HeldBackText); the fields it left, to come back to once they are
        # left; the fields the next pass renders even though they have the
        # focus (a completer pick: the operator's own action).
        self._renderPending = False
        self._rendering = False
        self._heldBack = HeldBackText()
        self._focusSkipped = []
        self._forceRender = set()
        self._previewLoaded = False
        self._designs = LayoutCatalog.Layouts([LAYOUT_KIND])

        # NOTE: the commentary.* normalisation that used to run here moved to
        # Window._SeedPanelState() (startup Step 4b-2). This panel is built on
        # first reveal now, and the overlay / the external control API read
        # commentary.* from launch, so the state must be normalised whether or
        # not the panel is ever opened. _RenderFromState() below reads it.

        # Build the design selector (and then the preview) first: the OBS
        # group below reads the selected design for its URL field.
        designSelector = self._buildDesignSelector()
        preview = self._buildPreview()

        leftContent = QWidget()
        leftCol = QVBoxLayout(leftContent)
        leftCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        leftCol.setSpacing(UI.PANEL_MARGIN)
        leftCol.addWidget(self._buildCastersGroup())
        leftCol.addWidget(
            MakeFeatureColorCard(FEATURE_COMMENTARY, "実況・解説の色"))
        leftCol.addWidget(self._buildObsGroup())
        leftCol.addStretch(1)

        leftScroll = QScrollArea()
        leftScroll.setWidgetResizable(True)
        leftScroll.setWidget(leftContent)
        leftScroll.setFixedWidth(L.LEFT_PANE_WIDTH)
        leftScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

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
        panesLayout.addWidget(leftScroll)
        panesLayout.addWidget(rightScroll, 1)
        self.setWidget(panes)

        self._RenderFromState()

        # Bound methods: Qt drops the connections with the panel.
        StateManagerBridge.instance.state_updated.connect(self._onStateDelta)
        StateManagerBridge.instance.state_big_change.connect(
            self._ScheduleRender)
        QApplication.instance().focusChanged.connect(self._onFocusChanged)

        AutonaPlayerDB.signals.db_updated.connect(self._rebindCompleters)
        self._rebindCompleters()

        AutonaCommentaryWidget.instance = self

    # ── Casters ──────────────────────────────────────────────────────
    def _buildCastersGroup(self):
        group = L.make_card("実況・解説者（最大 12 名）")

        note = QLabel(
            "「配信に表示」をチェックした人（最大 4 名）が、上から順に配信"
            "オーバーレイの左から表示されます。並び替えは ≡ をドラッグ。"
            "名前はローカル選手 DB から補完できます。")
        note.setWordWrap(True)
        note.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(note)

        _pc = UI.palette()
        # `onair` variant (配信に表示 checked) is two steps brighter
        # (bg_quinary) with a stronger border, following the panel's
        # elevation grammar. The `dragging` variant brightens one step;
        # L.DragGrip toggles that property around grab() so only the
        # drag pixmap uses it.
        cardStyle = (
            f"QFrame#commentaryCasterCard {{"
            f" background-color: {_pc['bg_tertiary']};"
            f" border: 1px solid {_pc['border_subtle']};"
            f" border-radius: {UI.RADIUS_MD}px;"
            f" }}"
            f"QFrame#commentaryCasterCard[onair=\"true\"] {{"
            f" background-color: {_pc['bg_quinary']};"
            f" border: 1px solid {_pc['border_strong']};"
            f" }}"
            f"QFrame#commentaryCasterCard[dragging=\"true\"] {{"
            f" background-color: {_pc['bg_quaternary']};"
            f" }}"
            f"QLabel#cardGrip {{ padding: 2px; }}")

        self.casterCardList = L.DragCardList(
            mime="application/x-autona-commentary-card")
        self.casterCardList.orderChanged.connect(self._onCardsReordered)
        group.layout().addWidget(self.casterCardList)

        # Card widget-dicts in VISUAL order; position i edits slot i+1.
        # casterInputs maps slot(str) -> widget dict and is rebuilt by
        # _remapSlots after every drag reorder.
        # dict keys: "card", "header", "name", "team", "twitter",
        #            "role", "icon", "note", "display"
        self._cards = []
        self._cardWidgets = {}   # card QFrame -> widget dict
        self.casterInputs = {}

        for slot in comms.SLOTS:
            card = QFrame()
            card.setObjectName("commentaryCasterCard")
            card.setStyleSheet(cardStyle)
            card.setFixedWidth(CASTER_CARD_WIDTH)
            cardOuter = QHBoxLayout(card)
            cardOuter.setContentsMargins(4, 8, 8, 8)
            cardOuter.setSpacing(4)

            # Drag grip centred over the card's full height.
            grip = L.DragGrip(card, self.casterCardList)
            cardOuter.addWidget(grip, 0, Qt.AlignmentFlag.AlignVCenter)

            fields = QGridLayout()
            fields.setContentsMargins(0, 0, 0, 0)
            fields.setVerticalSpacing(2)
            fields.setHorizontalSpacing(6)
            cardOuter.addLayout(fields, 1)

            header = QLabel(slot)
            header.setStyleSheet(UI.secondary_label_style())

            display_check = QCheckBox("配信に表示")

            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(6)
            header_row.addWidget(header)
            header_row.addStretch(1)
            header_row.addWidget(display_check)

            # Role: free-text box with the ROLES candidates offered by a
            # completer (same UX as the player-name field below: case
            # insensitive, contains match, queued pick handling).
            role_edit = QLineEdit()
            role_edit.setPlaceholderText("役割")
            role_completer = QCompleter(comms.ROLES, role_edit)
            role_completer.setCaseSensitivity(
                Qt.CaseSensitivity.CaseInsensitive)
            role_completer.setFilterMode(Qt.MatchFlag.MatchContains)
            role_edit.setCompleter(role_completer)
            L.attach_popup_on_focus(role_edit)

            # Icon: pulldown, stored as the stem ("" = no icon). No
            # fixed height — the global QSS min-height (26px) keeps it
            # level with the QLineEdits sharing the row.
            icon_combo = QComboBox()
            icon_combo.addItem("アイコンなし", "")
            for stem, label in comms.ICONS.items():
                icon_combo.addItem(
                    QIcon(UI.icon_path(f"{stem}.svg")), label, stem)
            icon_combo.setFixedWidth(120)

            name_edit = QLineEdit()
            name_edit.setPlaceholderText(
                QApplication.translate("app", "Player Name"))

            team_edit = QLineEdit()
            team_edit.setPlaceholderText("チーム / 所属")
            team_edit.setFixedWidth(120)

            twitter_edit = QLineEdit()
            twitter_edit.setPlaceholderText(
                QApplication.translate("app", "Twitter"))
            twitter_edit.setFixedWidth(120)

            note_edit = QLineEdit()
            note_edit.setPlaceholderText("コメント（例: 本年度ランキング2位）")

            fields.addLayout(header_row,     0, 0, 1, 2)
            fields.addWidget(name_edit,      1, 0)
            fields.addWidget(team_edit,      1, 1)
            fields.addWidget(role_edit,      2, 0)
            fields.addWidget(icon_combo,     2, 1)
            fields.addWidget(note_edit,      3, 0)
            fields.addWidget(twitter_edit,   3, 1)
            fields.setColumnStretch(0, 1)

            self.casterCardList.addCard(card)

            widgets = {
                "card": card,
                "header": header,
                "name": name_edit,
                "team": team_edit,
                "twitter": twitter_edit,
                "role": role_edit,
                "icon": icon_combo,
                "note": note_edit,
                "display": display_check,
            }
            self._cards.append(widgets)
            self._cardWidgets[card] = widgets
            self.casterInputs[slot] = widgets

            # The widget dict (not the slot) is captured: the card's slot
            # changes when it is drag-reordered.
            for field in comms.TEXT_FIELDS:
                widgets[field].editingFinished.connect(
                    lambda w=widgets, f=field: self._onTextFinished(w, f))
            # Queued: let the completer finish its own text insertion
            # before we read the field (player-name pattern).
            role_completer.activated.connect(
                lambda _text, w=widgets: self._onTextFinished(w, "role"),
                Qt.QueuedConnection)
            icon_combo.currentIndexChanged.connect(
                lambda _index, w=widgets: self._onIconChanged(w))
            display_check.toggled.connect(
                lambda checked, w=widgets: self._onDisplayToggled(w, checked))

        return group

    def _slotOf(self, w):
        return next((slot for slot, card in self.casterInputs.items()
                     if card is w), None)

    def _onTextFinished(self, w, field):
        """A caster field finished an edit: write that one field, unless the
        text is not news -- what the state already holds, or the text a
        render had to leave in the field while the operator was in it (the
        state moved on underneath it, and writing it back would put the old
        value over the new one). The player cards' rule (HeldBackText)."""
        slot = self._slotOf(w)
        if slot is None:
            return
        edit = w[field]
        text = edit.text()
        stored = StateManager.Get(f"{schema.COMMENTARY_ROOT}.{slot}.{field}")
        if self._heldBack.IsEdit(
                edit, text, stored == CommentaryService.FieldValue(field, text)):
            CommentaryService.SetField(slot, field, text)

    def _onIconChanged(self, w):
        slot = self._slotOf(w)
        if slot is not None and not CommentaryService.SetField(
                slot, "icon", w["icon"].currentData() or ""):
            self._ScheduleRender()

    def _onDisplayToggled(self, w, checked):
        slot = self._slotOf(w)
        if slot is not None and not CommentaryService.SetField(
                slot, "display", 1 if checked else 0):
            # Refused (the overlay shows MAX_DISPLAY at most): the box goes
            # back to what the state holds before the locks are counted --
            # counted with the refused tick still in it, the boxes would be
            # locked (and this one left enabled) from a count the state does
            # not have. Its signals are blocked: putting it back is not an
            # edit.
            stored = StateManager.Get(
                f"{schema.COMMENTARY_ROOT}.{slot}.display")
            with QSignalBlocker(w["display"]):
                w["display"].setChecked(bool(stored))
        self._refreshDisplayLocks()

    def _onCardsReordered(self):
        """DragCardList drop handler: the visual order changed. Slot =
        position, so remap the slot bindings, renumber the headers and move
        the casters between slots in the state (the data travels with the
        cards)."""
        previous = {id(w): slot for slot, w in self.casterInputs.items()}
        self._cards = [self._cardWidgets[card]
                       for card in self.casterCardList.cards()]
        self._remapSlots()
        CommentaryService.Reorder([previous[id(w)] for w in self._cards])

    def _remapSlots(self):
        self.casterInputs = {
            str(i + 1): w for i, w in enumerate(self._cards)}
        for slot, w in self.casterInputs.items():
            w["header"].setText(slot)

    def _refreshDisplayLocks(self):
        """Once MAX_DISPLAY toggles are ON, disable the unchecked ones
        (no modal — this runs inside signal handlers). Also mirrors each
        toggle onto its card's `onair` QSS variant (brighter card)."""
        checked = sum(
            1 for w in self._cards if w["display"].isChecked())
        lock = checked >= comms.MAX_DISPLAY
        for w in self._cards:
            box = w["display"]
            box.setEnabled(box.isChecked() or not lock)
            card = w["card"]
            if card.property("onair") != box.isChecked():
                card.setProperty("onair", box.isChecked())
                card.style().unpolish(card)
                card.style().polish(card)

    # ── State -> UI ──────────────────────────────────────────────────
    def _onStateDelta(self, payload):
        # A flat delta renders its key path as a Python list, so the line-up's
        # changes contain "['commentary'" verbatim.
        if "['commentary'" in str(payload):
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
        """Show the line-up as the state holds it. Writes nothing: every
        input is set with its signals blocked. A field the operator is in
        is left alone (see _RenderText) and shown once they leave it."""
        self._renderPending = False
        if self._rendering:
            return
        self._rendering = True
        try:
            self._focusSkipped = []
            with StateManager.lock:
                state = deep_clone(
                    StateManager.Get(schema.COMMENTARY_ROOT) or {})
            for slot, w in self.casterInputs.items():
                data = state.get(slot) if isinstance(state, dict) else None
                data = data if isinstance(data, dict) else comms.DefaultSlot()
                for field in comms.TEXT_FIELDS:
                    value = data.get(field, "")
                    self._RenderText(
                        w[field], value if isinstance(value, str) else "")
                index = max(w["icon"].findData(data.get("icon", "")), 0)
                if w["icon"].currentIndex() != index:
                    with QSignalBlocker(w["icon"]):
                        w["icon"].setCurrentIndex(index)
                shown = bool(data.get("display"))
                if w["display"].isChecked() != shown:
                    with QSignalBlocker(w["display"]):
                        w["display"].setChecked(shown)
        finally:
            self._forceRender.clear()
            self._rendering = False
        self._refreshDisplayLocks()

    def _RenderText(self, edit, text):
        if edit.text() == text:
            self._heldBack.Shown(edit, text)
            return
        if edit.hasFocus() and edit not in self._forceRender:
            # Left as it is while the operator is in it. Its text is held
            # back -- not written back when the field is finished -- if it
            # is still what the field last showed (the value the state has
            # moved on from), or if this field's own value was just written
            # elsewhere while they typed (HeldBackText). Any other typing is
            # their edit, written when they finish it.
            self._heldBack.LeftAlone(edit, edit.text(), text)
            if edit not in self._focusSkipped:
                self._focusSkipped.append(edit)
            return
        with QSignalBlocker(edit):
            edit.setText(text)
        self._heldBack.Shown(edit, text)

    def _onFocusChanged(self, old, new):
        """QApplication.focusChanged: once the operator has left every field
        the last pass had to skip, show the state in them (deferred: the
        field's own editingFinished runs first)."""
        if not self._focusSkipped:
            return
        skipped = [w for w in self._focusSkipped if Shiboken.isValid(w)]
        if any(w.hasFocus() for w in skipped):
            self._focusSkipped = skipped
            return
        self._focusSkipped = []
        self._ScheduleRender()

    # ── PlayerDB autocomplete / external data ────────────────────────
    def _rebindCompleters(self):
        """(Re-)attach a PlayerDB completer to every name field. SetupModel
        replaces the model object wholesale, so db_updated must rebind."""
        if AutonaPlayerDB.model is None:
            return
        for w in self._cards:
            edit = w["name"]
            completer = QCompleter()
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            # Entrants of the loaded tournament, or the whole DB when no
            # tournament is loaded.
            completer.setModel(AutonaPlayerDB.GetCompleterModel())
            edit.setCompleter(completer)
            L.attach_popup_on_focus(edit)
            # Queued: let the completer finish its own text insertion
            # before we overwrite the fields (scoreboard widget pattern).
            # The widget dict (not the slot) is captured — the card's
            # slot changes when it is drag-reordered.
            completer.activated[QModelIndex].connect(
                lambda x, w=w: self._onCompleterPick(w, x),
                Qt.QueuedConnection)

    def _onCompleterPick(self, w, model_index):
        if model_index is None:
            return
        player = model_index.data(Qt.ItemDataRole.UserRole)
        slot = self._slotOf(w)
        if not player or slot is None:
            return
        CommentaryService.ApplyPlayerData(slot, player)
        # The name the pick stored is shown in the field the operator is
        # still in: the pick is their own edit (the completer had put its
        # own text there).
        self._forceRender.add(w["name"])
        self._ScheduleRender()

    # ── Preview ──────────────────────────────────────────────────────
    def _buildDesignSelector(self):
        # Right-column selector above the preview: section-title label +
        # full-width combo, matching the RESULT tab. Selecting a design
        # reloads the preview immediately, no update button. Single entry
        # today; further commentary layouts appear automatically.
        col = QVBoxLayout()
        col.setSpacing(UI.SPACE_SM)
        label = QLabel("実況・解説のデザインを選択")
        L.apply_section_title(label)
        col.addWidget(label)

        self.designCombo = QComboBox()
        for entry in self._designs:
            self.designCombo.addItem(entry["displayName"], entry["id"])
        saved = SettingsManager.Get(
            "commentary.selected_design", DEFAULT_DESIGN)
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
        SettingsManager.Set("commentary.selected_design", design)
        self.obsUrlField.setText(ObsUrl(design, self._currentEntry()))
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
        self.obsUrlField.setText(
            ObsUrl(self._currentDesign() or DEFAULT_DESIGN,
                   self._currentEntry()))
        urlRow.addWidget(self.obsUrlField, 1)
        btCopy = QPushButton("コピー")
        btCopy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.obsUrlField.text()))
        urlRow.addWidget(btCopy)
        group.layout().addLayout(urlRow)

        note = QLabel(
            "Autona を起動したままにすると、実況・解説者の編集が OBS 上の"
            "オーバーレイへリアルタイムに反映されます。")
        note.setWordWrap(True)
        group.layout().addWidget(note)
        return group
