import os
import re
import traceback

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Helpers.AutonaSponsorHelper import AutonaSponsorHelper
from .StateManager import StateManager
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaPlayerDB import AutonaPlayerDB
from .Helpers.AutonaDirHelper import AutonaResolve
from . import ui_constants as UI
from . import ui_layout as L
from .Workers import Worker
from .AutonaBasePlayerWidget import AutonaBasePlayerWidget
import threading
from .Helpers.AutonaBadWordFilter import AutonaBadWordFilter
from loguru import logger


class NoIconProxyModel(QSortFilterProxyModel):
    """Proxy model that strips icons (DecorationRole) from the source model."""
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole:
            return None
        return super().data(index, role)


class AutonaScoreboardPlayerWidgetSignals(QObject):
    playerId_changed = Signal()
    player1Id_changed = Signal()
    player2Id_changed = Signal()
    player_seed_changed = Signal()
    dataChanged = Signal()



class AutonaScoreboardPlayerWidget(AutonaBasePlayerWidget):
    countries = None
    countryModel = None
    characterModel = None
    _deleted = False

    # dataLock is set per-instance in _initBasePlayerWidget(); see base class.

    def __init__(self, index=0, teamNumber=0, path="", *args):
        super().__init__(*args)

        self.instanceSignals = AutonaScoreboardPlayerWidgetSignals()

        self.path = path

        self.index = index
        self.teamNumber = teamNumber

        self.losers = False

        uic.loadUi(AutonaResolve("src/layout/AutonaScoreboardPlayer.ui"), self)
        self._initBasePlayerWidget()

        # Apply grid spacing from UI constants
        grid = self.layout()
        grid.setContentsMargins(UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM)
        grid.setHorizontalSpacing(UI.SPACE_SM)
        grid.setVerticalSpacing(UI.SPACE_SM)
        # Single-column layout: every row lives in column 0 and stretches to
        # the full width, so all elements share the same left/right edges.
        # (Columns 1-3 are legacy from the upstream avatar layout and unused.)
        grid.setColumnStretch(0, 1)
        for i in range(grid.count()):
            item = grid.itemAt(i)
            if not item:
                continue
            if not item.layout():
                continue
            item.layout().setContentsMargins(0, 0, 0, 0)
            item.layout().setSpacing(UI.SPACE_SM)

        seed = self.findChild(QSpinBox, "seed")
        seed.setMaximumWidth(seed.sizeHint().width() // 2)

        # The Twitter field has no inline label anymore; carry the field name
        # in the placeholder. "Twitter" only exists in the "app" translation
        # context (not the .ui Form context), so set it here rather than in
        # the .ui.
        twitter = self.findChild(QLineEdit, "twitter")
        twitter.setPlaceholderText(QApplication.translate("app", "Twitter"))

        # Birthday flag is not surfaced anywhere downstream yet; keep it
        # hidden unconditionally. Visibility of twitter / twitterLabel and
        # the other per-field rows is driven by the eye-menu toggles in
        # AutonaScoreboardWidget.
        self.findChild(QCheckBox, "birthday").setVisible(False)

        # Seed is not user-editable from the widget anymore. The spinbox
        # stays in the layout as a data holder (other code paths read /
        # write its value), but both it and its label are permanently
        # hidden. What the user sees is the seedDisplay label bolted onto
        # the sponsor row, updated whenever the spinbox value changes.
        self.findChild(QLabel, "seedLabel").setVisible(False)
        seed.setVisible(False)
        seedRowLayout = self.findChild(QHBoxLayout, "horizontalLayout")
        for i in range(seedRowLayout.count()):
            it = seedRowLayout.itemAt(i)
            sp = it.spacerItem() if it is not None else None
            if sp is not None:
                sp.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Minimum)

        self.seedDisplay = self.findChild(QLabel, "seedDisplay")
        # The .ui used to hardcode rgba(255,255,255,0.75) which became
        # invisible on the light palette. Drive the colour from the
        # current theme tokens instead (Design.md §5.4 / §8 T-14).
        self.seedDisplay.setStyleSheet(self._seedDisplayStyle())

        def _syncSeedDisplay(value):
            try:
                v = int(value or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                self.seedDisplay.setText(f"Seed: {v}")
                self.seedDisplay.setVisible(True)
            else:
                # Empty seed: hide the trailing slot so it stops reserving
                # width and the GamerTag field reaches the column's right
                # edge, staying flush with the rows below.
                self.seedDisplay.setText("")
                self.seedDisplay.setVisible(False)
        seed.valueChanged.connect(_syncSeedDisplay)
        _syncSeedDisplay(seed.value())

        country = self.findChild(QComboBox, "country")
        state = self.findChild(QComboBox, "state")

        # Custom textbox
        custom_textbox_layout = QHBoxLayout()
        custom_textbox_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_textbox = QPlainTextEdit()
        custom_textbox_layout.addWidget(self.custom_textbox)
        self.layout().addLayout(custom_textbox_layout, 98, 0, 1, 1)
        # Kept as attribute so _buildCompactDetailsUI can strip the row.
        self._customTextboxRow = custom_textbox_layout
        self.custom_textbox.setObjectName("custom_textbox")
        self.custom_textbox.setFixedHeight(36)
        self.custom_textbox.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.custom_textbox.setPlaceholderText(QApplication.translate("app", "Additional information"))
        self.custom_textbox.textChanged.connect(
                lambda element=self.custom_textbox: [
                    StateManager.Set(
                        f"{self.path}.{element.objectName()}", element.toPlainText()),
                    self.instanceSignals.dataChanged.emit()
                ])

        self.character_container = self.findChild(QWidget, "characters")
        # On the scoreboard the player block is chrome-free (the team column's
        # wrapping group box is already flattened in single-player mode), so
        # drop the CHARACTERS card framing and its 10px side padding too. The
        # character rows then sit flush with the sponsor/name row and the reset
        # button — every element shares one left/right edge. Top padding is kept
        # for the "CHARACTERS" title.
        if self.path.startswith("score."):
            self.character_container.setStyleSheet(
                "QGroupBox { border: none; background: transparent;"
                " margin-top: 0px; padding: 24px 0px 0px 0px; }"
                " QGroupBox::title { padding-left: 0px; }")
            # The .ui leaves the inner layout's side margins at the style
            # default (~9px); zero them so the character rows are flush with
            # the column edge. Top clearance for the title comes from the
            # group box padding above.
            self.character_container.layout().setContentsMargins(0, 0, 0, 0)

        self.LoadCountries()

        AutonaCountryHelper.signals.countriesUpdated.connect(self.LoadCountries)

        self.character_elements = []

        # Save + Delete share the first row (Delete only appears for the
        # PlayerDB editor, not the in-match scoreboard). Clear gets its
        # own row below so it reads as a distinct destructive action.
        save_row_layout = QHBoxLayout()
        save_row_layout.setSpacing(UI.SPACE_SM)
        self.layout().addLayout(save_row_layout, 99, 0, 1, 1)

        clear_row_layout = QHBoxLayout()
        clear_row_layout.setSpacing(UI.SPACE_SM)
        self.layout().addLayout(clear_row_layout, 100, 0, 1, 1)
        # Kept as attributes so _buildCompactDetailsUI can strip the rows.
        self._saveRow = save_row_layout
        self._clearRow = clear_row_layout

        self.save_bt = QPushButton(
            QApplication.translate("app", "Save new player"))
        self.save_bt.setIcon(QIcon(UI.icon_path("save.svg")))
        self.save_bt.clicked.connect(self.SavePlayerToDB)
        self.save_bt.setMinimumWidth(1)
        # Save/Update-player-to-DB is a PlayerDB-editor action; the in-match
        # scoreboard hides it (same policy as delete_bt below).
        if self.path.startswith("score."):
            self.save_bt.hide()
        else:
            save_row_layout.addWidget(self.save_bt)
            self.findChild(QLineEdit, "name").editingFinished.connect(
                self.ManageSavePlayerToDBText)
            self.findChild(QLineEdit, "team").editingFinished.connect(
                self.ManageSavePlayerToDBText)

        self.delete_bt = QPushButton(
            QApplication.translate("app", "Delete player entry"))
        self.delete_bt.setIcon(QIcon(UI.icon_path("cancel.svg")))
        self.delete_bt.setEnabled(False)
        self.delete_bt.clicked.connect(self.DeletePlayerFromDB)
        self.delete_bt.setMinimumWidth(1)
        if self.path.startswith("score."):
            self.delete_bt.hide()
        else:
            save_row_layout.addWidget(self.delete_bt)
            self.findChild(QLineEdit, "name").editingFinished.connect(
                self.ManageDeletePlayerFromDBActive)
            self.findChild(QLineEdit, "team").editingFinished.connect(
                self.ManageDeletePlayerFromDBActive)

        self.clear_bt = QPushButton(QApplication.translate("app", "Clear"))
        # Destructive action: danger styling (red outline) — opt-in via the
        # objectName the global QSS targets (ui_constants QPushButton#danger).
        self.clear_bt.setObjectName("danger")
        self.clear_bt.setIcon(QIcon(UI.icon_path("undo.svg")))
        clear_row_layout.addWidget(self.clear_bt)
        self.clear_bt.clicked.connect(self.Clear)
        self.clear_bt.setMinimumWidth(1)

        # Move up/down
        titleContainer = self.findChild(QHBoxLayout, "titleContainer")
        self.btMoveUp = QPushButton()
        self.btMoveUp.setFixedSize(UI.BTN_SM, UI.BTN_SM)
        self.btMoveUp.setIcon(QIcon(UI.icon_path("arrow_up.svg")))
        titleContainer.addWidget(self.btMoveUp)
        self.btMoveDown = QPushButton()
        self.btMoveDown.setFixedSize(UI.BTN_SM, UI.BTN_SM)
        self.btMoveDown.setIcon(QIcon(UI.icon_path("arrow_down.svg")))
        titleContainer.addWidget(self.btMoveDown)

        self.SetIndex(index, teamNumber)

        self.lastExportedName = ""

        self.findChild(QLineEdit, "name").editingFinished.connect(
            self.NameChanged)
        self.findChild(QLineEdit, "team").editingFinished.connect(
            self.NameChanged)

        for c in self.findChildren(QLineEdit):
            c.editingFinished.connect(
                lambda element=c: [
                    StateManager.Set(
                        f"{self.path}.{element.objectName() if element.objectName() != 'qt_spinbox_lineedit' else element.parent().objectName()}", element.text()),
                    self.instanceSignals.dataChanged.emit()
                ])

        seed.valueChanged.connect(
            lambda value: StateManager.Set(f"{self.path}.seed", value)
        )
        seed.valueChanged.connect(
            lambda value: self.instanceSignals.player_seed_changed.emit()
        )

        seed.setValue(0)
        seed.valueChanged.emit(seed.value())

        self.findChild(QCheckBox, "birthday").toggled.connect(
            lambda state, element=c: [
                StateManager.Set(
                    f"{self.path}.birthday", state)
        ])
        self.findChild(QCheckBox, "birthday").toggled.emit(False)

        for c in self.findChildren(QComboBox):
            c.currentIndexChanged.connect(
                lambda text, element=c: [
                    self.ComboBoxIndexChanged(element)
                ]
            )
            c.currentIndexChanged.emit(0)

        self.SetCharactersPerPlayer(1)

        AutonaPlayerDB.signals.db_updated.connect(
            self.SetupAutocomplete)
        self.SetupAutocomplete()

        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self.ReloadCharacters)

        # Scoreboard rows mirror the Web UI player card: only sponsor + tag
        # stay inline; every other field moves into a per-player details
        # dialog. The PlayerDB editor (path "player_editor.*") keeps the
        # full inline form.
        if self.path.startswith("score."):
            self._buildCompactDetailsUI()

    def deleteLater(self):
        self._deleted = True
        super().deleteLater()

    def _buildCompactDetailsUI(self):
        """Re-home every detail field into a modal QDialog (Web UI parity).

        The dialog is a QObject child of this widget, so findChild /
        findChildren lookups (eye-menu toggles, Clear, SwapWith, SetData)
        keep resolving the same widget instances — only their visual home
        changes. The hidden data holders (seed spinbox, seedLabel,
        birthday) stay parented to the widget without a layout.
        """
        grid = self.layout()

        # The app-wide QSS dresses every QGroupBox as a titled section
        # (margin-top 4px + padding 28px 10px 10px 10px, reserving a header
        # zone). This widget is a QGroupBox by inheritance only; on the
        # compact two-row card that chrome is ~42px of dead space per player
        # and dominated the scoreboard once the detail fields moved into the
        # dialog. Zero it here — the characters group box keeps its own
        # stylesheet (a widget's own sheet outranks an ancestor's).
        self.setStyleSheet(
            "QGroupBox { border: none; background: transparent;"
            " margin: 0; padding: 0; }")

        self.detailsDialog = QDialog(self)
        dlg = QVBoxLayout()
        dlg.setContentsMargins(
            UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD)
        dlg.setSpacing(UI.SPACE_SM)
        self.detailsDialog.setLayout(dlg)
        self.detailsDialog.setMinimumWidth(400)

        # Attach each row to the dialog BEFORE moving widgets into it, so
        # addWidget() reparents (and detaches from the old grid row)
        # immediately instead of deferring to layout installation.
        # Header: read-only seed badge (web dialog shows it as a chip).
        seedRow = QHBoxLayout()
        seedRow.setSpacing(UI.SPACE_SM)
        dlg.addLayout(seedRow)
        seedRow.addWidget(self.seedDisplay)
        seedRow.addStretch(1)

        identityRow = QHBoxLayout()
        identityRow.setSpacing(UI.SPACE_SM)
        dlg.addLayout(identityRow)
        identityRow.addWidget(self._w("real_name", QLineEdit))
        identityRow.addWidget(self._w("twitter", QLineEdit))

        locationRow = QHBoxLayout()
        locationRow.setSpacing(UI.SPACE_SM)
        dlg.addLayout(locationRow)
        locationRow.addWidget(self._w("country", QComboBox))
        locationRow.addWidget(self._w("state", QComboBox))

        dlg.addWidget(self.character_container)
        dlg.addWidget(self.custom_textbox)

        buttonRow = QHBoxLayout()
        buttonRow.setSpacing(UI.SPACE_SM)
        dlg.addLayout(buttonRow)
        buttonRow.addWidget(self.clear_bt)
        buttonRow.addStretch(1)
        self.btCloseDetails = QPushButton(
            QApplication.translate("app", "Close"))
        self.btCloseDetails.clicked.connect(self.detailsDialog.accept)
        buttonRow.addWidget(self.btCloseDetails)

        # Strip the emptied rows out of the original grid. The seed row and
        # identity row still hold permanently-hidden data holders (seed,
        # seedLabel, birthday); those stay as layout-less hidden children.
        for row in (
            self.findChild(QHBoxLayout, "horizontalLayout"),
            self.findChild(QHBoxLayout, "identityRow"),
            self.findChild(QHBoxLayout, "locationRow"),
            self._customTextboxRow,
            self._saveRow,
            self._clearRow,
        ):
            self._takeLayoutFromGrid(grid, row)

        # Web Player.jsx (narrow) parity: sponsor and player name stack
        # vertically at full width. The tag placeholder carries the column
        # identity ("1Pプレイヤー名" / "2Pプレイヤー名") — the 1P/2P header
        # labels were dropped.
        team = self._w("team", QLineEdit)
        name = self._w("name", QLineEdit)
        name.setPlaceholderText(QApplication.translate(
            "app", "{0}Pプレイヤー名").format(self.teamNumber))
        # Full-width rows: neutralize the .ui's one-row styling (sponsor was
        # content-sized and right-aligned to hug the tag).
        team.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        team.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(team, 1, 0)
        grid.addWidget(name, 2, 0)
        self._takeLayoutFromGrid(grid, self.findChild(QHBoxLayout, "teamRow"))

        # Details link below the name field, left-aligned (web card's
        # ">>詳細"). Slim link look comes from the shared QSS rule
        # QPushButton#linkButton (ui_constants).
        self.btDetails = QPushButton(QApplication.translate("app", ">>詳細"))
        self.btDetails.setObjectName("linkButton")
        self.btDetails.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btDetails.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btDetails.clicked.connect(self.ShowDetails)
        # Kept as an attribute: on the singles board the scoreboard moves
        # the team-level LOSERS checkbox onto this row (right-aligned after
        # the stretch), next to the details link.
        self.linkRow = QHBoxLayout()
        self.linkRow.setContentsMargins(0, 0, 0, 0)
        self.linkRow.setSpacing(0)
        self.linkRow.addWidget(self.btDetails)
        self.linkRow.addStretch(1)
        grid.addLayout(self.linkRow, 3, 0, 1, 1)

    @staticmethod
    def _takeLayoutFromGrid(grid, sub_layout):
        if sub_layout is None:
            return
        for i in range(grid.count()):
            item = grid.itemAt(i)
            if item is not None and item.layout() is sub_layout:
                grid.takeAt(i)
                sub_layout.deleteLater()
                return

    def ShowDetails(self):
        name = self._w("name", QLineEdit).text().strip()
        self.detailsDialog.setWindowTitle(
            name if name else QApplication.translate("app", "プレイヤー詳細"))
        # Window-modal, non-blocking (no exec(): モーダル入れ子禁止).
        self.detailsDialog.open()

    @staticmethod
    def _seedDisplayStyle() -> str:
        return f"color: {UI.palette()['text_secondary']};"

    def ComboBoxIndexChanged(self, element: QComboBox):
        StateManager.Set(
            f"{self.path}.{element.objectName()}", element.currentData())
        self.instanceSignals.dataChanged.emit()

    def SetLosers(self, value):
        with self.dataLock:
            self.losers = value
            self.ExportMergedName()

    def NameChanged(self):
        # Skip during bulk SetData; will be called once at the end
        if getattr(self, '_bulk_updating', False):
            return
        with self.dataLock:
            team = self.findChild(QLineEdit, "team").text()
            name = self.findChild(QLineEdit, "name").text()
            merged = team + " " + name

            if merged != self.lastExportedName:
                self.ExportMergedName()
                self.ExportPlayerImages()
                self.ExportPlayerId()

            self.lastExportedName = merged

    def ExportPlayerId(self, id=None):
        with self.dataLock:
            if StateManager.Get(f"{self.path}.id") != id:
                StateManager.Set(
                    f"{self.path}.id", id)
                if "score" in self.path:
                    self.instanceSignals.playerId_changed.emit()
                    if "team.1" in self.path:
                        self.instanceSignals.player1Id_changed.emit()
                    else:
                        self.instanceSignals.player2Id_changed.emit()

    def _swapRestoreTail(self, data):
        # Scoreboard restores the player id directly (NameChanged would reset
        # it to None).
        self.ExportPlayerId(data["id"])

    def SetIndex(self, index: int, team: int):
        self.findChild(QWidget, "title").setText(
            QApplication.translate("app", "Player {0}").format(index))
        self.index = index
        self.teamNumber = team

    def SetCharactersPerPlayer(self, number):
        # logger.info(f"AutonaScoreboardPlayerWidget#SetCharactersPerPlayer({number})")
        while len(self.character_elements) < number:
            character_element = QWidget()
            character_element.setLayout(QHBoxLayout())
            character_element.layout().setSpacing(UI.SPACE_SM)
            character_element.layout().setContentsMargins(0, 0, 0, 0)
            player_character = QComboBox()
            player_character.setEditable(True)
            character_element.layout().addWidget(player_character)
            player_character.setMinimumWidth(60)
            player_character.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            player_character.view().setMinimumWidth(60)
            player_character.completer().setCompletionMode(QCompleter.PopupCompletion)
            player_character.completer().popup().setMinimumWidth(250)
            proxy = NoIconProxyModel(player_character)
            proxy.setSourceModel(AutonaGameAssetManager.instance.characterModel)
            player_character.setModel(proxy)
            player_character.setIconSize(QSize(0, 0))
            player_character.setFixedHeight(32)

            player_character_color = QComboBox()
            character_element.layout().addWidget(player_character_color)
            player_character_color.setIconSize(QSize(16, 16))
            player_character_color.setFixedHeight(32)
            player_character_color.setMinimumWidth(48)
            player_character_color.setMaximumWidth(80)
            view = QListView()
            view.setIconSize(QSize(24, 24))
            player_character_color.setView(view)
            player_character_color.setEditable(True)
            player_character_color.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            player_character_color.completer().setCompletionMode(QCompleter.PopupCompletion)

            # Add variant
            player_variant = QComboBox()
            player_variant.setObjectName("variants")
            character_element.layout().addWidget(player_variant)
            player_variant.setIconSize(QSize(UI.ICON_SM, UI.ICON_SM))
            player_variant.setFixedHeight(32)
            player_variant.setMinimumWidth(60)
            player_variant.setMaximumWidth(120)
            player_variant.setModel(
                AutonaGameAssetManager.instance.variantModel)
            view = QListView()
            view.setIconSize(QSize(UI.ICON_SM, UI.ICON_SM))
            player_variant.setView(view)
            player_variant.setEditable(True)
            player_variant.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            player_variant.completer().setCompletionMode(QCompleter.PopupCompletion)

            if len(AutonaGameAssetManager.instance.variants) <= 0:
                player_variant.setVisible(False)

            # Move up/down
            btMoveUp = QPushButton()
            btMoveUp.setFixedSize(UI.BTN_SM, UI.BTN_SM)
            btMoveUp.setIcon(QIcon(UI.icon_path("arrow_up.svg")))
            character_element.layout().addWidget(btMoveUp)
            btMoveUp.clicked.connect(lambda x=None, index=len(
                self.character_elements): self.SwapCharacters(index, index-1))
            btMoveDown = QPushButton()
            btMoveDown.setFixedSize(UI.BTN_SM, UI.BTN_SM)
            btMoveDown.setIcon(QIcon(UI.icon_path("arrow_down.svg")))
            character_element.layout().addWidget(btMoveDown)
            btMoveDown.clicked.connect(lambda x=None, index=len(
                self.character_elements): self.SwapCharacters(index, index+1))

            if self.path.startswith("score."):
                btMoveUp.hide()
                btMoveDown.hide()

            # Add line to characters
            self.character_container.layout().addWidget(character_element)

            self.character_elements.append(
                [character_element, player_character, player_character_color, player_variant])

            player_character.currentIndexChanged.connect(
                lambda x, element=player_character, target=player_character_color: [
                    self.LoadSkinOptions(element, target),
                    self.CharactersChanged()
                ]
            )

            player_character_color.currentIndexChanged.connect(
                lambda index, element=player_character: [
                    self.CharactersChanged()
                ]
            )

            player_variant.currentIndexChanged.connect(
                lambda index, element=player_character: [
                    self.CharactersChanged()
                ]
            )

            player_character.setCurrentIndex(0)
            player_character_color.setCurrentIndex(0)
            player_variant.setCurrentIndex(0)

            player_character.setObjectName(
                f"character_{len(self.character_elements)}")
            player_character_color.setObjectName(
                f"character_color_{len(self.character_elements)}")

        while len(self.character_elements) > number:
            self.character_elements[-1][0].setParent(None)
            self.character_elements.pop()

        if self.character_container.findChild(QComboBox, "variants") is not None:
            if len(AutonaGameAssetManager.instance.variants) <= 0:
                for container in self.findChildren(QComboBox, "variants"):
                    container.setVisible(False)
                for container in self.findChildren(QComboBox):
                    if "character_color_" in container.objectName():
                        container.setMaximumWidth(210)
            else:
                for container in self.findChildren(QComboBox, "variants"):
                    container.setVisible(True)
                for container in self.findChildren(QComboBox):
                    if "character_color_" in container.objectName():
                        container.setMaximumWidth(120)

        self.CharactersChanged(includeMains=True)

    def _styleCountryStateWidgets(self, country, state):
        # Editable combos select the blank first model row (index 0), so the
        # QComboBox placeholderText (shown only at index -1) never appears.
        # Mirror it onto the line edit, which shows its placeholder whenever
        # the text is empty. (The only "Country"/"Region" hint, since the
        # inline labels were removed.)
        if country.lineEdit() is not None:
            country.lineEdit().setPlaceholderText(country.placeholderText())
        if state.lineEdit() is not None:
            state.lineEdit().setPlaceholderText(state.placeholderText())

    def ReloadCharacters(self):
        if len(AutonaGameAssetManager.instance.variants) <= 0:
            for container in self.findChildren(QComboBox, "variants"):
                container.setVisible(False)
            for container in self.findChildren(QComboBox):
                if "character_color_" in container.objectName():
                    container.setMaximumWidth(80)
        else:
            for container in self.findChildren(QComboBox, "variants"):
                container.setVisible(True)
            for container in self.findChildren(QComboBox):
                if "character_color_" in container.objectName():
                    container.setMaximumWidth(80)
        for c in self.character_elements:
            proxy = NoIconProxyModel(c[1])
            proxy.setSourceModel(AutonaGameAssetManager.instance.characterModel)
            c[1].setModel(proxy)
            c[1].setIconSize(QSize(0, 0))
            c[1].setFixedHeight(32)
            c[3].setModel(AutonaGameAssetManager.instance.variantModel)

    def SetupAutocomplete(self):
        if AutonaPlayerDB.model:
            name_edit = self._w("name", QLineEdit)
            name_edit.setCompleter(QCompleter())
            name_edit.completer().activated[QModelIndex].connect(
                lambda x: self.SetData(x.data(Qt.ItemDataRole.UserRole)) if x is not None else None, Qt.QueuedConnection)
            name_edit.completer().setCaseSensitivity(
                Qt.CaseSensitivity.CaseInsensitive)
            name_edit.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            # Entrants of the loaded tournament, or the whole DB when no
            # tournament is loaded.
            name_edit.completer().setModel(AutonaPlayerDB.GetCompleterModel())
            L.attach_popup_on_focus(name_edit)

            self.ManageSavePlayerToDBText()
            self.ManageDeletePlayerFromDBActive()

    def SetData(self, data, dontLoadFromDB=False, clear=True, no_mains=False):
        self.dataLock.acquire()
        StateManager.BlockSaving()
        was_bulk = getattr(self, '_bulk_updating', False)
        self._bulk_updating = True

        logger.debug(f"Setting data for {self.path}: {data}")

        try:
            if clear:
                # Block country signals during Clear: Clear() calls
                # setCurrentIndex(0) on every QComboBox, which would trigger
                # a LoadStates rebuild for the empty-country state. That
                # rebuild is immediately discarded by the subsequent
                # country_code assignment, so suppressing it saves one
                # LoadStates pass per SetData.
                country_element = self._w("country", QComboBox)
                country_was_blocked = False
                if country_element is not None:
                    country_was_blocked = country_element.signalsBlocked()
                    country_element.blockSignals(True)
                try:
                    self.Clear(no_mains=no_mains)
                finally:
                    if country_element is not None:
                        country_element.blockSignals(country_was_blocked)

            # Load player data from DB; will be overwriten by incoming data
            if not dontLoadFromDB:
                tag = data.get(
                    "prefix")+" "+data.get("gamerTag") if data.get("prefix") else data.get("gamerTag")

                dbPlayer = AutonaPlayerDB.database.get(tag)
                if dbPlayer is not None:
                    self.SetData(dbPlayer, dontLoadFromDB=True,
                                 clear=False, no_mains=no_mains)

            name = self._w("name")
            if data.get("gamerTag") and data.get("gamerTag") != name.text():
                data["gamerTag"] = AutonaBadWordFilter.Censor(
                    data["gamerTag"], data.get("country_code"))
                name.setText(f'{data.get("gamerTag")}')
                name.editingFinished.emit()

            team = self._w("team")
            if data.get("prefix") and data.get("prefix") != team.text():
                data["prefix"] = AutonaBadWordFilter.Censor(
                    data["prefix"], data.get("country_code"))
                team.setText(f'{data.get("prefix")}')
                team.editingFinished.emit()

            real_name = self._w("real_name")
            if data.get("name") and data.get("name") != real_name.text():
                data["name"] = AutonaBadWordFilter.Censor(
                    data["name"], data.get("country_code"))
                real_name.setText(f'{data.get("name")}')
                real_name.editingFinished.emit()

            if data.get("avatar"):
                self.ExportPlayerImages(data.get("avatar"))

            if data.get("id"):
                self.ExportPlayerId(data.get("id"))

            if data.get("city"):
                self.ExportPlayerCity(data.get("city"))

            twitter = self._w("twitter")
            if data.get("twitter") and data.get("twitter") != twitter.text():
                data["twitter"] = AutonaBadWordFilter.Censor(
                    data["twitter"], data.get("country_code"))
                twitter.setText(
                    f'{data.get("twitter")}')
                twitter.editingFinished.emit()
            
            if data.get("custom_textbox") and data.get("custom_textbox") != self.custom_textbox.toPlainText():
                data["custom_textbox"] = AutonaBadWordFilter.Censor(
                    data["custom_textbox"], data.get("country_code"))
                self.custom_textbox.setPlainText(
                    f'{data.get("custom_textbox")}'.replace("\\n", "\n"))
                self.custom_textbox.textChanged.emit()

            if data.get("country_code"):
                countryElement: QComboBox = self._w("country", QComboBox)
                countryIndex = 0
                for i in range(AutonaCountryHelper.countryModel.rowCount()):
                    item = AutonaCountryHelper.countryModel.item(
                        i).data(Qt.ItemDataRole.UserRole)
                    if item:
                        if data.get("country_code") == item.get("code"):
                            countryIndex = i
                            break
                if countryElement.currentIndex() != countryIndex:
                    countryElement.setCurrentIndex(countryIndex)

            if data.get("state_code"):
                countryElement: QComboBox = self._w("country", QComboBox)
                stateElement: QComboBox = self._w("state", QComboBox)
                stateIndex = 0
                for i in range(stateElement.model().rowCount()):
                    item = stateElement.model().item(i).data(Qt.ItemDataRole.UserRole)
                    if item:
                        if data.get("state_code") == item.get("original_code"):
                            stateIndex = i
                            break
                if stateElement.currentIndex() != stateIndex:
                    stateElement.setCurrentIndex(stateIndex)

            if data.get("mains") and no_mains != True:
                if type(data.get("mains")) == list:
                    # Legacy old-API single-character format: ["Mario", skin].
                    # Only slot 0 should receive it — the previous code
                    # iterated *all* character_elements and set every slot
                    # to mains[0], so a 3-character UI would show the same
                    # character three times.
                    main_list = data.get("mains")
                    if main_list and self.character_elements:
                        main_name = main_list[0]
                        try:
                            main_skin = int(main_list[1]) if len(main_list) > 1 else 0
                        except (TypeError, ValueError):
                            main_skin = 0
                        character_element = self.character_elements[0][1]
                        color_element = self.character_elements[0][2]
                        characterIndex = 0
                        for j in range(character_element.model().rowCount()):
                            item = character_element.model().data(
                                character_element.model().index(j, 0), Qt.ItemDataRole.UserRole)
                            if item and item.get("en_name") == main_name:
                                characterIndex = j
                                break
                        if character_element.currentIndex() != characterIndex:
                            character_element.setCurrentIndex(characterIndex)
                        if color_element.currentIndex() != main_skin:
                            color_element.setCurrentIndex(main_skin)
                elif type(data.get("mains")) == dict:
                    mains = data.get("mains").get(
                        AutonaGameAssetManager.instance.selectedGame.get("codename"), [])

                    for i, main in enumerate(mains):
                        if i < len(self.character_elements):
                            character_element = self.character_elements[i][1]
                            color_element = self.character_elements[i][2]
                            variant_element = self.character_elements[i][3]
                            characterIndex = 0
                            for i in range(character_element.model().rowCount()):
                                item = character_element.model().data(
                                    character_element.model().index(i, 0), Qt.ItemDataRole.UserRole)
                                if item:
                                    if item.get("en_name") == main[0]:
                                        characterIndex = i
                                        break
                            if character_element.currentIndex() != characterIndex:
                                character_element.setCurrentIndex(
                                    characterIndex)
                            if len(main) > 1:
                                if color_element.currentIndex() != int(main[1]):
                                    color_element.setCurrentIndex(int(main[1]))
                            else:
                                if color_element.currentIndex() != 0:
                                    color_element.setCurrentIndex(0)
                            
                            variantIndex = 0
                            if variant_element:
                                for i in range(variant_element.model().rowCount()):
                                    item = variant_element.model().data(
                                        variant_element.model().index(i, 0), Qt.ItemDataRole.UserRole)
                                    if item:
                                        if len(main) >= 3 :
                                            if item.get("en_name") == main[2]:
                                                variantIndex = i
                                                break
                                        else:
                                            variantIndex = 0
                                            break
                            else:
                                variantIndex = 0
                            if variant_element.currentIndex() != variantIndex:
                                variant_element.setCurrentIndex(variantIndex)

            if data.get("seed") is not None:
                if data.get("seed") > 0:
                    StateManager.Set(f"{self.path}.seed", data.get("seed"))
                    self._w("seed", QSpinBox).setValue(int(data.get("seed")))
                else:
                    self._w("seed", QSpinBox).setValue(0)
            if data.get("city"):
                StateManager.Set(f"{self.path}.city", data.get("city"))
        finally:
            self._bulk_updating = was_bulk
            if not was_bulk:
                # Deferred: run name exports once.
                # ExportPlayerId is handled directly above; calling
                # NameChanged here would reset the id to None.
                self.ExportMergedName()
                self.ExportPlayerImages()
                team = self._w("team", QLineEdit).text()
                name = self._w("name", QLineEdit).text()
                self.lastExportedName = team + " " + name
            StateManager.ReleaseSaving()
            self.dataLock.release()

    def SavePlayerToDB(self):
        tag = self.GetCurrentPlayerTag()

        playerData = {
            "prefix": self._w("team").text(),
            "gamerTag": self._w("name").text(),
            "name": self._w("real_name").text(),
            "twitter": self._w("twitter").text(),
            "custom_textbox": "\\n".join(self.custom_textbox.toPlainText().splitlines())
        }

        if AutonaGameAssetManager.instance.selectedGame.get("codename"):
            mains = []

            for i, (element, character, color, variant) in enumerate(self.character_elements):
                data = {}

                if character.currentData() is not None:
                    data["name"] = character.currentData().get("en_name")
                else:
                    data["name"] = ""

                data["skin"] = color.currentIndex()

                if data["skin"] == None:
                    data["skin"] = 0
                    
                if variant.currentData():
                    data["variant"] = variant.currentData().get("en_name", "")
                else:
                    data["variant"] = ""

                if data["name"] != "":
                    mains.append([data.get("name"), data.get("skin"), data.get("variant")])

            playerData["mains"] = {
                AutonaGameAssetManager.instance.selectedGame.get("codename"): mains
            }

        if self._w("country", QComboBox).currentData(Qt.ItemDataRole.UserRole):
            playerData["country_code"] = self._w(
                "country", QComboBox).currentData(Qt.ItemDataRole.UserRole).get("code")

        if self._w("state", QComboBox).currentData(Qt.ItemDataRole.UserRole):
            playerData["state_code"] = self._w(
                "state", QComboBox).currentData(Qt.ItemDataRole.UserRole).get("code")

        AutonaPlayerDB.AddPlayers([playerData], overwrite=True)

        self.CharactersChanged(includeMains=True)

    def ManageSavePlayerToDBText(self):
        tag = self.GetCurrentPlayerTag()

        if tag in AutonaPlayerDB.database:
            self.save_bt.setText(
                QApplication.translate("app", "Update player"))
        else:
            self.save_bt.setText(
                QApplication.translate("app", "Save new player"))

    def ManageDeletePlayerFromDBActive(self):
        tag = self.GetCurrentPlayerTag()

        if tag in AutonaPlayerDB.database:
            self.delete_bt.setEnabled(True)
        else:
            self.delete_bt.setEnabled(False)

    def DeletePlayerFromDB(self):
        tag = self.GetCurrentPlayerTag()
        AutonaPlayerDB.DeletePlayer(tag)

