"""Commentary tab: edit the caster lineup and get it onto the stream.

Full rework mirroring AutonaTop8Widget's list + live-preview pattern:

* **Casters** — 12 cards (name + team / role free text with a candidate
  completer + icon pulldown / note + Twitter), drag-reorderable by
  their hamburger grip (result-settings pattern). Slot = card position:
  a drop moves the data between slots. A per-slot "配信に表示" toggle
  picks which casters (max 4) the ``layout/commentators/`` overlay
  renders, top card leftmost.
* **Preview / Add to OBS** — design pulldown (immediate reload, Board-tab
  spec) over ``layout/commentators*/``, embedded live preview of the
  selected overlay, and the browser-source URL.

State contract: everything under ``commentary.{1..12}`` (see
AutonaCommentaryModel). All writes go through explicit StateManager.Set
inside SaveBlock — no objectName-derived keypaths. The state persists
across restarts; Window._SeedPanelState() normalizes whatever LoadState
restored (including the legacy pre-rework schema) at launch, before this
panel is built.

ChangeCommDataSignal / LoadCommFromTagSignal keep their historical names
and signatures: AutonaWebServerActions emits them for the
``/update-commentary-<N>`` and ``/load-commentator-from-tag-<N>`` routes
(index arrives 0-based, already converted from the 1-based route).
"""

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from loguru import logger

from .StateManager import StateManager
from .SettingsManager import SettingsManager
from .AutonaPlayerDB import AutonaPlayerDB
from .AutonaLayoutPreview import (
    AutonaLayoutPreview, ObsUrl, DiscoverLayoutDesigns,
    LayoutDesignDisplayName)
from .Helpers.AutonaBadWordFilter import AutonaBadWordFilter
from . import AutonaCommentaryModel as comms
from . import ui_constants as UI
from . import ui_layout as L

# Design folders: layout/commentators/ plus any layout/commentators_*/
# variant (discovered at launch; the pulldown spec matches the Board tab).
LAYOUT_PREFIX = "commentators"
DEFAULT_DESIGN = "commentators"

CASTER_CARD_WIDTH = 280

# card field <- PlayerDB-shaped dict key (completer pick, web SetData and
# load-from-tag all deliver this shape; see AutonaPlayerDB.fieldnames).
PLAYERDB_FIELD_MAP = [
    ("name", "gamerTag"),
    ("team", "prefix"),
    ("twitter", "twitter"),
    ("note", "custom_textbox"),
]


class AutonaCommentaryWidget(QDockWidget):
    instance: "AutonaCommentaryWidget" = None

    ChangeCommDataSignal = Signal(int, object)
    LoadCommFromTagSignal = Signal(int, str, bool)

    def __init__(self, *args):
        super().__init__(*args)
        self.setWindowTitle(QApplication.translate("app", "Commentary"))

        # _updating suppresses input handlers while the UI is being
        # repopulated from state (same guard pattern as AutonaTop8Widget).
        self._updating = False
        self._previewLoaded = False
        self._designs = DiscoverLayoutDesigns(LAYOUT_PREFIX)

        # NOTE: the commentary.* normalisation that used to run here moved to
        # Window._SeedPanelState() (startup Step 4b-2). This panel is built on
        # first reveal now, and the overlay / the external control API read
        # commentary.* from launch, so the state must be normalised whether or
        # not the panel is ever opened. _loadFromState() below reads it.

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

        self._loadFromState()

        self.ChangeCommDataSignal.connect(self.SetData)
        self.LoadCommFromTagSignal.connect(self.LoadCommentatorFromTag)

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
            # Queued: let the completer finish its own text insertion
            # before we read the field (player-name pattern).
            role_completer.activated.connect(
                self._onFieldChanged, Qt.QueuedConnection)

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

            for edit in (name_edit, team_edit, role_edit,
                         twitter_edit, note_edit):
                edit.editingFinished.connect(self._syncToState)
            icon_combo.currentIndexChanged.connect(self._onFieldChanged)
            display_check.toggled.connect(self._onFieldChanged)

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

        return group

    def _onFieldChanged(self, *_args):
        self._syncToState()

    def _onCardsReordered(self):
        """DragCardList drop handler: the visual order changed. Slot =
        position, so remap the slot bindings, renumber the headers and
        rewrite the whole state (the data travels with the cards)."""
        self._cards = [self._cardWidgets[card]
                       for card in self.casterCardList.cards()]
        self._remapSlots()
        self._syncToState()

    def _remapSlots(self):
        self.casterInputs = {
            str(i + 1): w for i, w in enumerate(self._cards)}
        for slot, w in self.casterInputs.items():
            w["header"].setText(slot)

    def _syncToState(self):
        if self._updating:
            return
        # Fresh dicts on purpose: StateManager.Get returns a live
        # reference, and mutating it in place would make DeepDiff see no
        # change (no delta broadcast).
        slots = {}
        for slot, w in self.casterInputs.items():
            slots[slot] = {
                "name": w["name"].text().strip(),
                "team": w["team"].text().strip(),
                "twitter": w["twitter"].text().strip().lstrip("@"),
                "role": w["role"].text().strip(),
                "icon": w["icon"].currentData() or "",
                "note": w["note"].text().strip(),
                "display": 1 if w["display"].isChecked() else 0,
            }
        with StateManager.SaveBlock():
            StateManager.Set("commentary", slots)
        self._refreshDisplayLocks()

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
    def _loadFromState(self):
        state = StateManager.Get("commentary") or {}
        self._updating = True
        try:
            for slot, w in self.casterInputs.items():
                data = state.get(slot) or comms.DefaultSlot()
                w["name"].setText(data.get("name", ""))
                w["team"].setText(data.get("team", ""))
                w["twitter"].setText(data.get("twitter", ""))
                w["note"].setText(data.get("note", ""))
                w["role"].setText(data.get("role", ""))
                index = w["icon"].findData(data.get("icon", ""))
                w["icon"].setCurrentIndex(max(index, 0))
                w["display"].setChecked(bool(data.get("display")))
        finally:
            self._updating = False
        self._refreshDisplayLocks()

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
        if player:
            self._applyPlayerData(w, player)

    def _applyPlayerData(self, w, player):
        """Fill one card (widget dict) from a PlayerDB-shaped dict. Only
        string fields present in the dict are written; role/icon/display
        are left alone."""
        self._updating = True
        try:
            for field, source in PLAYERDB_FIELD_MAP:
                value = player.get(source)
                if isinstance(value, str):
                    w[field].setText(AutonaBadWordFilter.Censor(value) or "")
        finally:
            self._updating = False
        self._syncToState()

    def SetData(self, index, data):
        """ChangeCommDataSignal handler (web /update-commentary-<N>).
        index is 0-based — AutonaWebServerActions already converted the
        1-based caster number."""
        slot = str(index + 1)
        if slot not in self.casterInputs or not isinstance(data, dict):
            logger.warning(
                f"Commentary: ignoring SetData for slot {index + 1}")
            return
        self._applyPlayerData(self.casterInputs[slot], data)

    def LoadCommentatorFromTag(self, index, tag, no_mains=False):
        """LoadCommFromTagSignal handler. no_mains is accepted for
        signature compatibility; casters no longer carry characters."""
        slot = str(index + 1)
        if slot not in self.casterInputs:
            return False
        player = AutonaPlayerDB.GetPlayerFromTag(str(tag))
        if not player:
            return False
        self._applyPlayerData(self.casterInputs[slot], player)
        return True

    # ── Preview ──────────────────────────────────────────────────────
    def _buildDesignSelector(self):
        # Right-column selector above the preview: section-title label +
        # full-width combo, matching the RESULT tab. Selecting a design
        # reloads the preview immediately, no update button. Single entry
        # today; future layout/commentators_* variants appear automatically.
        col = QVBoxLayout()
        col.setSpacing(UI.SPACE_SM)
        label = QLabel("実況・解説のデザインを選択")
        L.apply_section_title(label)
        col.addWidget(label)

        self.designCombo = QComboBox()
        for d in self._designs:
            self.designCombo.addItem(
                LayoutDesignDisplayName(LAYOUT_PREFIX, d), d)
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
        self.preview = AutonaLayoutPreview(self._currentDesign())
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return self.preview

    def _currentDesign(self):
        if self.designCombo.count() == 0:
            return None
        return self.designCombo.currentData()

    def _onDesignChanged(self, _idx):
        design = self._currentDesign()
        if not design:
            return
        SettingsManager.Set("commentary.selected_design", design)
        self.obsUrlField.setText(ObsUrl(design))
        if self._previewLoaded:
            self.preview.load(design)

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
            ObsUrl(self._currentDesign() or DEFAULT_DESIGN))
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
