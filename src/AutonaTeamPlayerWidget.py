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
from .AutonaTeamBattleModeEnum import AutonaTeamBattleModeEnum
from .Helpers.AutonaDirHelper import AutonaResolve
from .AutonaBasePlayerWidget import AutonaBasePlayerWidget
from . import ui_constants as UI
from . import ui_layout as L
import threading
from .Helpers.AutonaBadWordFilter import AutonaBadWordFilter
from loguru import logger


class AutonaTeamPlayerWidgetSignals(QObject):
    playerId_changed = Signal()
    dynamicSpinner_changed = Signal()
    activeStatus_changed = Signal(int)
    deathStatus_changed = Signal(int)
    toggleDeathTrigger = Signal(bool)


class AutonaTeamPlayerWidget(AutonaBasePlayerWidget):
    countries = None
    countryModel = None
    characterModel = None

    # dataLock is set per-instance in _initBasePlayerWidget(); see base class.

    defaultSpinnerValue = 0
    battleMode: AutonaTeamBattleModeEnum = AutonaTeamBattleModeEnum.STOCK_POOL
    dynamicSpinner: QSpinBox = None

    def __init__(self, index=0, teamNumber=0, path="", *args):
        super().__init__(*args)

        self.instanceSignals = AutonaTeamPlayerWidgetSignals()

        self.path = path

        self.index = index
        self.teamNumber = teamNumber

        self.losers = False

        uic.loadUi(AutonaResolve("src/layout/AutonaTeamPlayer.ui"), self)
        self._initBasePlayerWidget()

        self.dynamicSpinner = self.findChild(QSpinBox, "dynamicSpinner")

        # custom_textbox_layout = QHBoxLayout()
        # self.custom_textbox = QPlainTextEdit()
        # custom_textbox_layout.addWidget(self.custom_textbox)
        # self.layout().addLayout(custom_textbox_layout, 98, 2, 1, 1)
        # self.custom_textbox.setObjectName("custom_textbox")
        # self.custom_textbox.setMaximumHeight(100)
        # self.custom_textbox.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        # self.custom_textbox.setPlaceholderText(QApplication.translate("app", "Additional information"))
        # self.custom_textbox.textChanged.connect(
        #         lambda element=self.custom_textbox: [
        #             StateManager.Set(
        #                 f"{self.path}.{element.objectName()}", element.toPlainText()),
        #             self.instanceSignals.dataChanged.emit()
        #         ])

        self.character_container = self.findChild(QWidget, "characters")

        self.LoadCountries()

        AutonaCountryHelper.signals.countriesUpdated.connect(self.LoadCountries)

        self.character_elements = []

        bottom_buttons_layout = QHBoxLayout()
        bottom_buttons_layout.setSpacing(UI.SPACE_SM)
        self.layout().addLayout(bottom_buttons_layout, 99, 0, 1, 3)

        self.clear_bt = QPushButton(QApplication.translate("app", "Clear"))
        self.clear_bt.setFont(QFont(self.clear_bt.font().family(), 9))
        # self.clear_bt.setFont(self.parent.font_small)
        self.clear_bt.setIcon(QIcon('assets/icons/undo.svg'))
        bottom_buttons_layout.addWidget(self.clear_bt)
        self.clear_bt.clicked.connect(self.Clear)
        self.clear_bt.setMinimumWidth(1)

        # Move up/down
        titleContainer = self.findChild(QHBoxLayout, "titleContainer")
        titleContainer.setSpacing(UI.SPACE_SM)
        self.btMoveUp = QPushButton()
        self.btMoveUp.setFixedSize(24, 24)
        self.btMoveUp.setIcon(QIcon("./assets/icons/arrow_up.svg"))
        titleContainer.addWidget(self.btMoveUp)
        self.btMoveDown = QPushButton()
        self.btMoveDown.setFixedSize(24, 24)
        self.btMoveDown.setIcon(QIcon("./assets/icons/arrow_down.svg"))
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
                    # self.instanceSignals.dataChanged.emit()
                ])

        for c in self.findChildren(QComboBox):
            c.currentIndexChanged.connect(
                lambda text, element=c: [
                    self.ComboBoxIndexChanged(element)
                ]
            )
            c.currentIndexChanged.emit(0)
        
        self.dynamicSpinner.valueChanged.connect(self.instanceSignals.dynamicSpinner_changed.emit)
        self.dynamicSpinner.valueChanged.connect(self.SpinnerHandling)

        self.SetCharactersPerPlayer(1)

        AutonaPlayerDB.signals.db_updated.connect(
            self.SetupAutocomplete)
        self.SetupAutocomplete()

        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self.ReloadCharacters)

        self.ToggleSponsorDisplay()

    def GetIndex(self):
        return self.index
    
    # =====================================================
    # BATTLE SPECIFIC CALLS
    # =====================================================
    def ToggleSponsorDisplay(self):
        if self.findChild(QLineEdit, "team").isHidden():
            self.findChild(QLineEdit, "team").show()
        else:
            self.findChild(QLineEdit, "team").hide()
    
    def SetBattleMode(self, mode: AutonaTeamBattleModeEnum):
        self.battleMode = mode
        if self.battleMode is AutonaTeamBattleModeEnum.STOCK_POOL:
            self.SetDynamicSpinnerLabelText(QApplication.translate("app", "STOCKS/LIVES"))
        elif self.battleMode is AutonaTeamBattleModeEnum.FIRST_TO:
            self.SetDynamicSpinnerLabelText(QApplication.translate("app", "GAMES WON"))
    
    def SetDefaultSpinnerValue(self, value: int):
        self.defaultSpinnerValue = value
        self.dynamicSpinner.setMaximum(value)
        self.ResetDynamicSpinner()
    
    # Changes based on battle mode
    def SetDynamicSpinnerLabelText(self, text: str):
        self.findChild(QLabel, "dynamicLabel").setText(text)
    
    def GetSpinnerValue(self):
        return self.dynamicSpinner.value()
    
    def ResetDynamicSpinner(self):
        if self.battleMode is AutonaTeamBattleModeEnum.STOCK_POOL:
            self.dynamicSpinner.setValue(self.defaultSpinnerValue)
        elif self.battleMode is AutonaTeamBattleModeEnum.FIRST_TO:
            self.dynamicSpinner.setValue(0)
        self.findChild(QCheckBox, "dead").setChecked(False)

    def IsActive(self):
        return self.findChild(QCheckBox, "activePlayer").isChecked()
    
    def SetActiveStatus(self, status: bool):
        self.findChild(QCheckBox, "activePlayer").setChecked(status)
        self.ExportActiveStatus()
        return
    
    def IsDead(self):
        return self.findChild(QCheckBox, "dead").isChecked()
    
    def SetDeathStatus(self, status: bool):
        self.findChild(QCheckBox, "dead").setChecked(status)
        self.ExportDeathStatus()
        return
    
    def SpinnerHandling(self):
        value = self.dynamicSpinner
        StateManager.Set(f"{self.path}.dynamic_spinner", value.value())
        if self.battleMode is AutonaTeamBattleModeEnum.STOCK_POOL:
            if value.value() <= 0:
                self.SetDeathStatus(True)
            else:
                self.SetDeathStatus(False)

        elif self.battleMode is AutonaTeamBattleModeEnum.FIRST_TO:
            if value.value() >= self.defaultSpinnerValue:
                self.instanceSignals.toggleDeathTrigger.emit(True)
            else:
                self.instanceSignals.toggleDeathTrigger.emit(False)

    def IncreaseCall(self):
        value = self.dynamicSpinner

        if self.battleMode is AutonaTeamBattleModeEnum.STOCK_POOL:
            if value.value() <= 0:
                return
            value.setValue(value.value() - 1)

        elif self.battleMode is AutonaTeamBattleModeEnum.FIRST_TO:
            if value.value() >= self.defaultSpinnerValue:
                return
            value.setValue(value.value() + 1)

    def DecreaseCall(self):
        value = self.dynamicSpinner

        if self.battleMode is AutonaTeamBattleModeEnum.STOCK_POOL:
            if value.value() >= self.defaultSpinnerValue:
                return
            value.setValue(value.value() + 1)
        elif self.battleMode is AutonaTeamBattleModeEnum.FIRST_TO:
            if value.value() <= 0:
                return
            value.setValue(value.value() - 1)
    
    # =====================================================
    # EXPORT CALLS
    # =====================================================
    def ExportActiveStatus(self):
        StateManager.Set(f"{self.path}.active", self.findChild(QCheckBox, "activePlayer").isChecked())
        return
    
    def ExportDeathStatus(self):
        StateManager.Set(f"{self.path}.dead", self.findChild(QCheckBox, "dead").isChecked())
        return
    
    def ComboBoxIndexChanged(self, element: QComboBox):
        StateManager.Set(
            f"{self.path}.{element.objectName()}", element.currentData())

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
                # self.ExportPlayerId()

            self.lastExportedName = merged

    def _swapRestoreTail(self, data):
        # Team battle restores the avatar (which also re-derives sponsors);
        # processEvents lets the just-set text settle before the image export.
        QCoreApplication.processEvents()
        self.ExportPlayerImages(data["online_avatar"])

    def SetIndex(self, index: int, team: int):
        self.index = index
        self.teamNumber = team

    def SetStocksPerPlayer(self, number):
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
            player_character.setModel(
                AutonaGameAssetManager.instance.characterModel)
            player_character.setIconSize(QSize(24, 24))
            player_character.setFixedHeight(32)
            player_character.setFont(
                QFont(player_character.font().family(), 9))
            player_character.lineEdit().setFont(QFont(player_character.font().family(), 9))

            # Add line to characters
            self.character_container.layout().addWidget(character_element)

            player_character.setCurrentIndex(0)

            player_character.setObjectName(
                f"character_{len(self.character_elements)}")

        while len(self.character_elements) > number:
            self.character_elements[-1][0].setParent(None)
            self.character_elements.pop()
        
        if self.character_container.findChild(QComboBox, "variants") is not None:
            if len(AutonaGameAssetManager.instance.variants) <= 0:
                for container in self.findChildren(QComboBox, "variants"):
                    container.setVisible(False)
            else:
                for container in self.findChildren(QComboBox, "variants"):
                    container.setVisible(True)

        self.CharactersChanged(includeMains=True)

    def SetCharactersPerPlayer(self, number):
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
            player_character.setModel(
                AutonaGameAssetManager.instance.characterModel)
            player_character.setIconSize(QSize(24, 24))
            player_character.setFixedHeight(32)
            player_character.setFont(
                QFont(player_character.font().family(), 9))
            player_character.lineEdit().setFont(QFont(player_character.font().family(), 9))

            player_character_color = QComboBox()
            character_element.layout().addWidget(player_character_color)
            player_character_color.setIconSize(QSize(48, 48))
            player_character_color.setFixedHeight(32)
            player_character_color.setMinimumWidth(64)
            player_character_color.setMaximumWidth(120)
            player_character_color.setFont(
                QFont(player_character_color.font().family(), 9))
            view = QListView()
            view.setIconSize(QSize(128, 128))
            player_character_color.setView(view)
            player_character_color.setEditable(True)
            player_character_color.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            player_character_color.completer().setCompletionMode(QCompleter.PopupCompletion)
            # self.player_character_color.activated.connect(self.CharacterChanged)
            # self.CharacterChanged()

            # Add variant
            player_variant = QComboBox()
            player_variant.setObjectName("variants")
            character_element.layout().addWidget(player_variant)
            player_variant.setIconSize(QSize(24, 24))
            player_variant.setFixedHeight(32)
            player_variant.setMinimumWidth(60)
            player_variant.setMaximumWidth(120)
            player_variant.setFont(
                QFont(player_variant.font().family(), 9))
            player_variant.setModel(
                AutonaGameAssetManager.instance.variantModel)
            view = QListView()
            view.setIconSize(QSize(24, 24))
            player_variant.setView(view)
            player_variant.setEditable(True)
            player_variant.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            player_variant.completer().setCompletionMode(QCompleter.PopupCompletion)

            if len(AutonaGameAssetManager.instance.variants) <= 0:
                player_variant.setVisible(False)

            # Move up/down
            btMoveUp = QPushButton()
            btMoveUp.setFixedSize(24, 24)
            btMoveUp.setIcon(QIcon("./assets/icons/arrow_up.svg"))
            character_element.layout().addWidget(btMoveUp)
            btMoveUp.clicked.connect(lambda x=None, index=len(
                self.character_elements): self.SwapCharacters(index, index-1))
            btMoveDown = QPushButton()
            btMoveDown.setFixedSize(24, 24)
            btMoveDown.setIcon(QIcon("./assets/icons/arrow_down.svg"))
            character_element.layout().addWidget(btMoveDown)
            btMoveDown.clicked.connect(lambda x=None, index=len(
                self.character_elements): self.SwapCharacters(index, index+1))

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
            else:
                for container in self.findChildren(QComboBox, "variants"):
                    container.setVisible(True)

        self.CharactersChanged(includeMains=True)

    def _styleCountryStateWidgets(self, country, state):
        country.setFont(QFont(country.font().family(), 9))
        country.lineEdit().setFont(QFont(country.font().family(), 9))
        state.setFont(QFont(state.font().family(), 9))
        state.lineEdit().setFont(QFont(state.font().family(), 9))

    def ReloadCharacters(self):
        if len(AutonaGameAssetManager.instance.variants) <= 0:
            for container in self.findChildren(QComboBox, "variants"):
                container.setVisible(False)
        else:
            for container in self.findChildren(QComboBox, "variants"):
                container.setVisible(True)
        for c in self.character_elements:
            c[1].setModel(AutonaGameAssetManager.instance.characterModel)
            c[1].setIconSize(QSize(24, 24))
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
            # Entrants of the loaded tournament, or the whole DB when no
            # tournament is loaded.
            self.findChild(QLineEdit, "name").completer().setModel(
                AutonaPlayerDB.GetCompleterModel())
            L.attach_popup_on_focus(self.findChild(QLineEdit, "name"))

    def SetData(self, data, dontLoadFromDB=False, clear=True, no_mains=False):
        self.dataLock.acquire()
        StateManager.BlockSaving()
        was_bulk = getattr(self, '_bulk_updating', False)
        self._bulk_updating = True

        logger.debug(f"Setting data for {self.path}: {data}")

        try:
            if clear:
                self.Clear(no_mains=no_mains)

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

            if data.get("avatar"):
                self.ExportPlayerImages(data.get("avatar"))

            # if data.get("id"):
            #     self.ExportPlayerId(data.get("id"))

            if data.get("city"):
                self.ExportPlayerCity(data.get("city"))

            twitter = self.findChild(QWidget, "twitter")
            if data.get("twitter") and data.get("twitter") != twitter.text():
                data["twitter"] = AutonaBadWordFilter.Censor(
                    data["twitter"], data.get("country_code"))
                twitter.setText(
                    f'{data.get("twitter")}')
                twitter.editingFinished.emit()
            
            # if data.get("custom_textbox") and data.get("custom_textbox") != self.custom_textbox.toPlainText():
            #     data["custom_textbox"] = AutonaBadWordFilter.Censor(
            #         data["custom_textbox"], data.get("country_code"))
            #     self.custom_textbox.setPlainText(
            #         f'{data.get("custom_textbox")}'.replace("\\n", "\n"))
            #     self.custom_textbox.textChanged.emit()

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
                # Only when the value is not already an iso2 the combo knows:
                # a state_code stored by an older build (or typed by hand) can
                # be a free-form name like "Tokyo". FindState is what resolves
                # it -- but its first call parses the pre-computed table, and
                # this runs inside dataLock + BlockSaving on the GUI thread, so
                # the normal path must never pay for it.
                if stateIndex == 0 and data.get("country_code"):
                    resolved = AutonaCountryHelper.FindState(
                        data.get("country_code"), data.get("state_code"))
                    if resolved:
                        for i in range(stateElement.model().rowCount()):
                            item = stateElement.model().item(i).data(
                                Qt.ItemDataRole.UserRole)
                            if item and resolved == item.get("original_code"):
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
                            item = character_element.model().item(i).data(Qt.ItemDataRole.UserRole)
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
                                item = character_element.model().item(i).data(Qt.ItemDataRole.UserRole)
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
                                    item = variant_element.model().item(i).data(Qt.ItemDataRole.UserRole)
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

            if data.get("city"):
                StateManager.Set(f"{self.path}.city", data.get("city"))
        finally:
            self._bulk_updating = was_bulk
            if not was_bulk:
                # Deferred: run name exports once.
                # No argument = keep online_avatar: it was either written
                # above from data["avatar"] or cleared by Clear(). Passing
                # None here (the old default) undid the write and left every
                # start.gg set with no avatar at all (agent-pitfalls #5 --
                # the same bug lived in AutonaScoreboardPlayerWidget).
                self.ExportMergedName()
                self.ExportPlayerImages()
                team = self.findChild(QLineEdit, "team").text()
                name = self.findChild(QLineEdit, "name").text()
                self.lastExportedName = team + " " + name
            StateManager.ReleaseSaving()
            self.dataLock.release()

