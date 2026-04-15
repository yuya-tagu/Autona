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

    dataLock = threading.RLock()

    def __init__(self, index=0, teamNumber=0, path="", customName="", *args):
        super().__init__(*args)

        self.instanceSignals = AutonaScoreboardPlayerWidgetSignals()

        self.path = path

        self.index = index
        self.teamNumber = teamNumber
        self.customName = customName

        self.losers = False

        uic.loadUi(AutonaResolve("src/layout/AutonaScoreboardPlayer.ui"), self)

        # Apply grid spacing from UI constants
        grid = self.layout()
        grid.setContentsMargins(UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM)
        grid.setHorizontalSpacing(UI.SPACE_SM)
        grid.setVerticalSpacing(UI.SPACE_SM)
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

        pronoun = self.findChild(QLineEdit, "pronoun")

        twitter = self.findChild(QLineEdit, "twitter")
        twitter.setPlaceholderText("@")

        # Hide unused UI elements
        self.findChild(QLabel, "twitterLabel").setVisible(False)
        self.findChild(QCheckBox, "birthday").setVisible(False)
        seedRow = self.findChild(QHBoxLayout, "horizontalLayout")
        twitter.setParent(None)
        seedRow.addWidget(twitter)

        country = self.findChild(QComboBox, "country")
        state = self.findChild(QComboBox, "state")

        # Theme-aware style tracking (fields are now editable, no readonly/disabled styles)
        self._themed_readonly = []
        self._themed_disabled = []

        # Custom textbox
        custom_textbox_layout = QHBoxLayout()
        custom_textbox_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_textbox = QPlainTextEdit()
        custom_textbox_layout.addWidget(self.custom_textbox)
        self.layout().addLayout(custom_textbox_layout, 98, 2, 1, 1)
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

        self.LoadCountries()

        AutonaCountryHelper.signals.countriesUpdated.connect(self.LoadCountries)

        self.character_elements = []

        bottom_buttons_layout = QHBoxLayout()
        bottom_buttons_layout.setSpacing(4)
        self.layout().addLayout(bottom_buttons_layout, 99, 0, 1, 3)

        self.save_bt = QPushButton(
            QApplication.translate("app", "Save new player"))
        self.save_bt.setIcon(QIcon(UI.icon_path("save.svg")))
        bottom_buttons_layout.addWidget(self.save_bt)
        self.save_bt.clicked.connect(self.SavePlayerToDB)
        self.findChild(QLineEdit, "name").editingFinished.connect(
            self.ManageSavePlayerToDBText)
        self.findChild(QLineEdit, "team").editingFinished.connect(
            self.ManageSavePlayerToDBText)
        self.save_bt.setMinimumWidth(1)

        self.delete_bt = QPushButton(
            QApplication.translate("app", "Delete player entry"))
        self.delete_bt.setIcon(QIcon(UI.icon_path("cancel.svg")))
        self.delete_bt.setEnabled(False)
        self.delete_bt.clicked.connect(self.DeletePlayerFromDB)
        self.delete_bt.setMinimumWidth(1)
        if self.path.startswith("score."):
            self.delete_bt.hide()
        else:
            bottom_buttons_layout.addWidget(self.delete_bt)
            self.findChild(QLineEdit, "name").editingFinished.connect(
                self.ManageDeletePlayerFromDBActive)
            self.findChild(QLineEdit, "team").editingFinished.connect(
                self.ManageDeletePlayerFromDBActive)

        self.clear_bt = QPushButton(QApplication.translate("app", "Clear"))
        self.clear_bt.setIcon(QIcon(UI.icon_path("undo.svg")))
        bottom_buttons_layout.addWidget(self.clear_bt)
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

        self.pronoun_completer = QCompleter()
        self.findChild(QLineEdit, "pronoun").setCompleter(
            self.pronoun_completer)
        self.pronoun_list = []
        for file in ('./user_data/pronouns_list.txt',):
            try:
                with open(file, 'r') as f:
                    for l in f.readlines():
                        processed_line = l.replace("\n", "").strip()
                        if processed_line and processed_line not in self.pronoun_list:
                            self.pronoun_list.append(processed_line)
            except FileNotFoundError:
                with open('./user_data/pronouns_list.txt', 'w') as f:
                    logger.info('creating ./user_data/pronouns_list.txt')
            except Exception as e:
                logger.error(traceback.format_exc())
        self.pronoun_model = QStringListModel()
        self.pronoun_completer.setModel(self.pronoun_model)
        self.pronoun_model.setStringList(self.pronoun_list)

    def deleteLater(self):
        self._deleted = True
        super().deleteLater()

    def refresh_theme_styles(self):
        """Re-apply theme-aware styles after light/dark mode toggle."""
        for w in self._themed_readonly:
            w.setStyleSheet(UI.readonly_style())
        for w in self._themed_disabled:
            w.setStyleSheet(UI.disabled_style())

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

    def SwapWith(self, other: "AutonaScoreboardPlayerWidget"):
        if self == other:
            logger.info("Swapping player with themselves")
            return
        try:
            StateManager.BlockSaving()
            with self.dataLock:
                with other.dataLock:
                    tmpData = []

                    # Save state
                    for w in [self, other]:
                        data = {}
                        for widget in w.findChildren(QWidget):
                            if type(widget) == QLineEdit:
                                data[widget.objectName()] = widget.text()
                            if type(widget) == QComboBox:
                                data[widget.objectName()] = widget.currentIndex()
                            if type(widget) == QPlainTextEdit:
                                data[widget.objectName()] = widget.toPlainText()
                        data["online_avatar"] = StateManager.Get(
                            f"{w.path}.online_avatar")
                        data["id"] = StateManager.Get(
                            f"{w.path}.id")
                        data["seed"] = StateManager.Get(
                            f"{w.path}.seed")
                        data["city"] = StateManager.Get(
                            f"{w.path}.city")
                        tmpData.append(data)

                    # Load state
                    for i, w in enumerate([other, self]):
                        for objName in tmpData[i]:
                            widget = w.findChild(QWidget, objName)
                            if widget:
                                if type(widget) == QLineEdit:
                                    widget.setText(tmpData[i][objName])
                                    widget.editingFinished.emit()
                                if type(widget) == QComboBox:
                                    widget.setCurrentIndex(tmpData[i][objName])
                                if type(widget) == QPlainTextEdit:
                                    widget.setPlainText(tmpData[i][objName])
                        w.ExportPlayerId(tmpData[i]["id"])
                        StateManager.Set(f"{w.path}.seed", tmpData[i]["seed"])
                        StateManager.Set(f"{w.path}.city", tmpData[i]["city"])
        finally:
            StateManager.ReleaseSaving()

    def SetIndex(self, index: int, team: int):
        if self.customName == "":
            self.findChild(QWidget, "title").setText(
                QApplication.translate("app", "Player {0}").format(index))
        else:
            title = self.customName + " {0}"
            self.findChild(QWidget, "title").setText(
                QApplication.translate("app", title).format(index))
        self.index = index
        self.teamNumber = team

    def SetCharactersPerPlayer(self, number):
        # logger.info(f"AutonaScoreboardPlayerWidget#SetCharactersPerPlayer({number})")
        while len(self.character_elements) < number:
            character_element = QWidget()
            character_element.setLayout(QHBoxLayout())
            character_element.layout().setSpacing(4)
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

    def LoadCountries(self):
        try:
            if AutonaCountryHelper.countryModel == None:
                AutonaCountryHelper.LoadCountries()

            countryCompleter = QCompleter(
                AutonaCountryHelper.countryModel)

            country: QComboBox = self.findChild(QComboBox, "country")
            country.setCompleter(countryCompleter)
            country.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            country.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            country.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            country.view().setMinimumWidth(60)
            country.completer().setCompletionMode(QCompleter.PopupCompletion)
            country.completer().popup().setMinimumWidth(300)
            country.setModel(AutonaCountryHelper.countryModel)

            country.currentIndexChanged.connect(self.LoadStates)

            state: QComboBox = self.findChild(QComboBox, "state")
            state.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            state.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            state.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            state.view().setMinimumWidth(60)
            state.completer().setCompletionMode(QCompleter.PopupCompletion)
            state.completer().popup().setMinimumWidth(300)

        except Exception as e:
            logger.error(traceback.format_exc())
            exit()

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
            self.findChild(QLineEdit, "name").setCompleter(QCompleter())
            self.findChild(QLineEdit, "name").completer().activated[QModelIndex].connect(
                lambda x: self.SetData(x.data(Qt.ItemDataRole.UserRole)) if x is not None else None, Qt.QueuedConnection)
            self.findChild(QLineEdit, "name").completer().setCaseSensitivity(
                Qt.CaseSensitivity.CaseInsensitive)
            self.findChild(QLineEdit, "name").completer(
            ).setFilterMode(Qt.MatchFlag.MatchContains)
            self.findChild(QLineEdit, "name").completer().setModel(
                AutonaPlayerDB.model)

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
                country_element = self.findChild(QComboBox, "country")
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

            name = self.findChild(QWidget, "name")
            if data.get("gamerTag") and data.get("gamerTag") != name.text():
                data["gamerTag"] = AutonaBadWordFilter.Censor(
                    data["gamerTag"], data.get("country_code"))
                name.setText(f'{data.get("gamerTag")}')
                name.editingFinished.emit()

            team = self.findChild(QWidget, "team")
            if data.get("prefix") and data.get("prefix") != team.text():
                data["prefix"] = AutonaBadWordFilter.Censor(
                    data["prefix"], data.get("country_code"))
                team.setText(f'{data.get("prefix")}')
                team.editingFinished.emit()

            real_name = self.findChild(QWidget, "real_name")
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

            twitter = self.findChild(QWidget, "twitter")
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

            pronoun = self.findChild(QWidget, "pronoun")
            if data.get("pronoun") and data.get("pronoun") != pronoun.text():
                data["pronoun"] = AutonaBadWordFilter.Censor(
                    data["pronoun"], data.get("country_code"))
                pronoun.setText(
                    f'{data.get("pronoun")}')
                pronoun.editingFinished.emit()

            if data.get("country_code"):
                countryElement: QComboBox = self.findChild(
                    QComboBox, "country")
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
                countryElement: QComboBox = self.findChild(
                    QComboBox, "country")
                stateElement: QComboBox = self.findChild(QComboBox, "state")
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
                    for element in self.character_elements:
                        character_element = element[1]
                        characterIndex = 0
                        for i in range(character_element.model().rowCount()):
                            item = character_element.model().data(
                                character_element.model().index(i, 0), Qt.ItemDataRole.UserRole)
                            if item:
                                if item.get("en_name") == data.get("mains")[0]:
                                    characterIndex = i
                                    break
                        character_element.setCurrentIndex(characterIndex)
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
                    self.findChild(QSpinBox, "seed").setValue(int(data.get("seed")))
                else:
                    self.findChild(QSpinBox, "seed").setValue(0)
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
                team = self.findChild(QLineEdit, "team").text()
                name = self.findChild(QLineEdit, "name").text()
                self.lastExportedName = team + " " + name
            StateManager.ReleaseSaving()
            self.dataLock.release()

    def SavePlayerToDB(self):
        tag = self.GetCurrentPlayerTag()

        playerData = {
            "prefix": self.findChild(QWidget, "team").text(),
            "gamerTag": self.findChild(QWidget, "name").text(),
            "name": self.findChild(QWidget, "real_name").text(),
            "twitter": self.findChild(QWidget, "twitter").text(),
            "pronoun": self.findChild(QWidget, "pronoun").text(),
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

        if self.findChild(QComboBox, "country").currentData(Qt.ItemDataRole.UserRole):
            playerData["country_code"] = self.findChild(
                QComboBox, "country").currentData(Qt.ItemDataRole.UserRole).get("code")

        if self.findChild(QComboBox, "state").currentData(Qt.ItemDataRole.UserRole):
            playerData["state_code"] = self.findChild(
                QComboBox, "state").currentData(Qt.ItemDataRole.UserRole).get("code")

        AutonaPlayerDB.AddPlayers([playerData], overwrite=True)

        if playerData.get("pronoun") and playerData["pronoun"] not in self.pronoun_list:
            with open("./user_data/pronouns_list.txt", 'at') as pronouns_file:
                pronouns_file.write(playerData["pronoun"] + "\n")
                self.pronoun_list.append(playerData["pronoun"])
                self.pronoun_model.setStringList(self.pronoun_list)

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

