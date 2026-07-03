"""Commentary tab: edit the caster lineup and get it onto the stream.

Full rework mirroring AutonaTop8Widget's list + live-preview pattern:

* **Casters** — 8 slot-fixed cards (name / team / real name / Twitter /
  role prefix / free note). A per-slot "配信に表示" toggle picks which
  casters (max 4) the ``layout/commentators/`` overlay renders; they are
  numbered in the order they were checked and rendered left to right.
* **Preview / Add to OBS** — embedded live preview of the overlay plus
  the browser-source URL.

State contract: everything under ``commentary.{1..8}`` (see
AutonaCommentaryModel). All writes go through explicit StateManager.Set
inside SaveBlock — no objectName-derived keypaths. The state persists
across restarts; the constructor normalizes whatever LoadState restored
(including the legacy pre-rework schema).

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
from .AutonaPlayerDB import AutonaPlayerDB
from .AutonaLayoutPreview import AutonaLayoutPreview, ObsUrl
from .Helpers.AutonaBadWordFilter import AutonaBadWordFilter
from . import AutonaCommentaryModel as comms
from . import ui_constants as UI
from . import ui_layout as L

LAYOUT_NAME = "commentators"

CASTER_CARD_WIDTH = 240

# card field <- PlayerDB-shaped dict key (completer pick, web SetData and
# load-from-tag all deliver this shape; see AutonaPlayerDB.fieldnames).
PLAYERDB_FIELD_MAP = [
    ("name", "gamerTag"),
    ("team", "prefix"),
    ("real_name", "name"),
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
        # Slots in the order their "配信に表示" toggle was checked; the
        # position in this list is the caster's overlay position
        # (commentary.{N}.display = index + 1).
        self._displayOrder = []

        with StateManager.SaveBlock():
            StateManager.Set(
                "commentary",
                comms.NormalizeState(StateManager.Get("commentary")))

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
        leftScroll.setFixedWidth(300)
        leftScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        rightContent = QWidget()
        rightCol = QVBoxLayout(rightContent)
        rightCol.setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        rightCol.setSpacing(UI.PANEL_MARGIN)
        rightCol.addWidget(self._buildPreviewGroup(), 1)

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
        group = L.make_card("実況・解説者（スロット固定 1-8）")

        note = QLabel(
            "「配信に表示」をチェックした順（最大 4 名）に、配信オーバーレイの"
            "左から表示されます。名前はローカル選手 DB から補完できます。")
        note.setWordWrap(True)
        note.setStyleSheet(UI.secondary_label_style())
        group.layout().addWidget(note)

        _pc = UI.palette()
        cardStyle = (
            f"QFrame#commentaryCasterCard {{"
            f" background-color: {_pc['bg_tertiary']};"
            f" border: 1px solid {_pc['border_subtle']};"
            f" border-radius: {UI.RADIUS_MD}px;"
            f" }}")

        cards = QVBoxLayout()
        cards.setSpacing(6)
        group.layout().addLayout(cards)

        # slot(str) -> {"name", "team", "real_name", "twitter", "role",
        #               "note", "display"}
        self.casterInputs = {}

        for slot in comms.SLOTS:
            card = QFrame()
            card.setObjectName("commentaryCasterCard")
            card.setStyleSheet(cardStyle)
            card.setFixedWidth(CASTER_CARD_WIDTH)
            fields = QGridLayout(card)
            fields.setContentsMargins(8, 8, 8, 8)
            fields.setVerticalSpacing(2)
            fields.setHorizontalSpacing(6)

            header = QLabel(slot)
            header.setStyleSheet(UI.secondary_label_style())

            # Overlay position ("No.1"), sitting left of the checkbox.
            order_label = QLabel("")
            order_label.setStyleSheet(
                f"color: {_pc['accent']}; font-weight: 700;")

            display_check = QCheckBox("配信に表示")

            # The header spans both grid columns so the appearing "No.N"
            # label never affects the input columns' width split.
            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            header_row.setSpacing(6)
            header_row.addWidget(header)
            header_row.addStretch(1)
            header_row.addWidget(order_label)
            header_row.addWidget(display_check)

            role_combo = QComboBox()
            for role in comms.ROLES:
                role_combo.addItem(role if role else "役割なし", role)
            role_combo.setFixedHeight(22)
            role_combo.setFixedWidth(80)

            name_edit = QLineEdit()
            name_edit.setPlaceholderText(
                QApplication.translate("app", "Player Name"))

            team_edit = QLineEdit()
            team_edit.setPlaceholderText("チーム / 所属")
            team_edit.setFixedWidth(80)

            real_name_edit = QLineEdit()
            real_name_edit.setPlaceholderText(
                QApplication.translate("app", "Real Name"))

            twitter_edit = QLineEdit()
            twitter_edit.setPlaceholderText(
                QApplication.translate("app", "Twitter"))
            twitter_edit.setFixedWidth(80)

            note_edit = QLineEdit()
            note_edit.setPlaceholderText("コメント（例: 本年度ランキング2位）")

            fields.addLayout(header_row,     0, 0, 1, 2)
            fields.addWidget(name_edit,      1, 0)
            fields.addWidget(role_combo,     1, 1)
            fields.addWidget(real_name_edit, 2, 0)
            fields.addWidget(team_edit,      2, 1)
            fields.addWidget(note_edit,      3, 0)
            fields.addWidget(twitter_edit,   3, 1)
            fields.setColumnStretch(0, 1)

            cards.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)

            for edit in (name_edit, team_edit, real_name_edit,
                         twitter_edit, note_edit):
                edit.editingFinished.connect(self._syncToState)
            role_combo.currentIndexChanged.connect(self._onFieldChanged)
            display_check.toggled.connect(
                lambda checked, slot=slot:
                self._onDisplayToggled(slot, checked))

            self.casterInputs[slot] = {
                "name": name_edit,
                "team": team_edit,
                "real_name": real_name_edit,
                "twitter": twitter_edit,
                "role": role_combo,
                "note": note_edit,
                "display": display_check,
                "order": order_label,
            }

        return group

    def _onFieldChanged(self, *_args):
        self._syncToState()

    def _onDisplayToggled(self, slot, checked):
        if self._updating:
            return
        if checked and slot not in self._displayOrder:
            self._displayOrder.append(slot)
        elif not checked and slot in self._displayOrder:
            self._displayOrder.remove(slot)
        self._syncToState()

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
                "real_name": w["real_name"].text().strip(),
                "twitter": w["twitter"].text().strip().lstrip("@"),
                "role": w["role"].currentData() or "",
                "note": w["note"].text().strip(),
                "display": (self._displayOrder.index(slot) + 1
                            if slot in self._displayOrder else 0),
            }
        with StateManager.SaveBlock():
            StateManager.Set("commentary", slots)
        self._refreshDisplayLocks()

    def _refreshDisplayLocks(self):
        """Show each checked slot's overlay position ("No.N") left of its
        checkbox, and once MAX_DISPLAY toggles are ON disable the
        unchecked ones (no modal — this runs inside signal handlers)."""
        lock = len(self._displayOrder) >= comms.MAX_DISPLAY
        for slot, w in self.casterInputs.items():
            box = w["display"]
            if slot in self._displayOrder:
                w["order"].setText(f"No.{self._displayOrder.index(slot) + 1}")
            else:
                w["order"].setText("")
            box.setEnabled(box.isChecked() or not lock)

    # ── State -> UI ──────────────────────────────────────────────────
    def _loadFromState(self):
        state = StateManager.Get("commentary") or {}
        shown = sorted(
            ((data.get("display"), int(slot), slot)
             for slot, data in state.items()
             if isinstance(data, dict) and data.get("display")))
        self._displayOrder = [slot for _order, _n, slot in shown]
        self._updating = True
        try:
            for slot, w in self.casterInputs.items():
                data = state.get(slot) or comms.DefaultSlot()
                w["name"].setText(data.get("name", ""))
                w["team"].setText(data.get("team", ""))
                w["real_name"].setText(data.get("real_name", ""))
                w["twitter"].setText(data.get("twitter", ""))
                w["note"].setText(data.get("note", ""))
                index = w["role"].findData(data.get("role", ""))
                w["role"].setCurrentIndex(max(index, 0))
                w["display"].setChecked(slot in self._displayOrder)
        finally:
            self._updating = False
        self._refreshDisplayLocks()

    # ── PlayerDB autocomplete / external data ────────────────────────
    def _rebindCompleters(self):
        """(Re-)attach a PlayerDB completer to every name field. SetupModel
        replaces the model object wholesale, so db_updated must rebind."""
        if AutonaPlayerDB.model is None:
            return
        for slot, w in self.casterInputs.items():
            edit = w["name"]
            completer = QCompleter()
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setModel(AutonaPlayerDB.model)
            edit.setCompleter(completer)
            # Queued: let the completer finish its own text insertion
            # before we overwrite the fields (scoreboard widget pattern).
            completer.activated[QModelIndex].connect(
                lambda x, slot=slot: self._onCompleterPick(slot, x),
                Qt.QueuedConnection)

    def _onCompleterPick(self, slot, model_index):
        if model_index is None:
            return
        player = model_index.data(Qt.ItemDataRole.UserRole)
        if player:
            self._applyPlayerData(slot, player)

    def _applyPlayerData(self, slot, player):
        """Fill one card from a PlayerDB-shaped dict. Only string fields
        present in the dict are written; role/display are left alone."""
        w = self.casterInputs[slot]
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
        self._applyPlayerData(slot, data)

    def LoadCommentatorFromTag(self, index, tag, no_mains=False):
        """LoadCommFromTagSignal handler. no_mains is accepted for
        signature compatibility; casters no longer carry characters."""
        slot = str(index + 1)
        if slot not in self.casterInputs:
            return False
        player = AutonaPlayerDB.GetPlayerFromTag(str(tag))
        if not player:
            return False
        self._applyPlayerData(slot, player)
        return True

    # ── Preview ──────────────────────────────────────────────────────
    def _buildPreviewGroup(self):
        group = L.make_card("プレビュー")
        self.preview = AutonaLayoutPreview(LAYOUT_NAME)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        group.layout().addWidget(self.preview)
        btRow = QHBoxLayout()
        btRow.addStretch(1)
        btReload = QPushButton("プレビューを再読み込み")
        btReload.clicked.connect(self._reloadPreview)
        btRow.addWidget(btReload)
        group.layout().addLayout(btRow)
        return group

    def _reloadPreview(self):
        self._previewLoaded = True
        self.preview.load()

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
        self.obsUrlField.setText(ObsUrl(LAYOUT_NAME))
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
