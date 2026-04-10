"""Base class for TSHScoreboardPlayerWidget and TSHTeamPlayerWidget.

Extracts methods that are identical (or functionally equivalent) between
both widget classes, eliminating ~200 lines of code duplication.

Subclasses are responsible for:
- __init__ (different .ui files, different widget setup)
- ComboBoxIndexChanged (Scoreboard emits dataChanged, Team does not)
- NameChanged (Scoreboard calls ExportPlayerId, Team does not)
- SwapWith (different state restoration approach)
- SetData, SetCharactersPerPlayer, ReloadCharacters, SetIndex
- SetupAutocomplete, LoadCountries (minor styling differences)
"""
import os
import re
import threading

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *

from .Helpers.TSHCountryHelper import TSHCountryHelper
from .Helpers.TSHSponsorHelper import TSHSponsorHelper
from .StateManager import StateManager
from .TSHGameAssetManager import TSHGameAssetManager
from loguru import logger


class TSHBasePlayerWidget(QGroupBox):
    countries = None
    countryModel = None
    characterModel = None
    _deleted = False

    dataLock = threading.RLock()

    def CharactersChanged(self, includeMains=False):
        if self._deleted:
            logger.warning(f"CharactersChanged called on deleted widget for {self.path}")
            return

        with self.dataLock:
            characters = {}

            for i, (element, character, color, variant) in enumerate(self.character_elements):
                data = character.currentData()

                if data == None:
                    data = {}

                if character.currentData() == None:
                    data = {"name": character.currentText()}

                if color.currentData() and color.currentData().get("name", ""):
                    data["name"] = color.currentData().get("name", "")

                if color.currentData() and color.currentData().get("en_name", ""):
                    data["en_name"] = color.currentData().get("en_name", "")

                if color.currentData() and character.currentData():
                    data["assets"] = color.currentData().get("assets", {})

                if data.get("assets") == None:
                    data["assets"] = {}

                data["skin"] = color.currentIndex()
                if variant.currentData():
                    data["variant"] = variant.currentData()
                else:
                    data["variant"] = {}

                characters[i+1] = data

            StateManager.Set(
                f"{self.path}.character", characters)

            if includeMains:
                StateManager.Set(
                    f"{self.path}.mains", characters)

    def ExportMergedName(self):
        with self.dataLock:
            team = self.findChild(QLineEdit, "team").text()
            name = self.findChild(QLineEdit, "name").text()
            merged = ""
            nameOnlyMerged = ""

            if team != "":
                merged += team+" | "

            merged += name
            nameOnlyMerged += name

            if self.losers:
                merged += " [L]"
                nameOnlyMerged += " [L]"

            StateManager.Set(
                f"{self.path}.mergedName", merged)
            StateManager.Set(
                f"{self.path}.mergedOnlyName", nameOnlyMerged)

    def ExportPlayerImages(self, onlineAvatar=None):
        with self.dataLock:
            team = self.findChild(QLineEdit, "team").text()
            name = self.findChild(QLineEdit, "name").text()
            merged = ""

            if team != "":
                merged += team+" "

            merged += name

            merged = re.sub(r"[,/|;:<>\\?*]", "_", merged)

            # Online avatar
            StateManager.Set(
                f"{self.path}.online_avatar", onlineAvatar)

            # Local avatar
            localPath = f"./user_data/player_avatar/{merged}.png"
            if os.path.exists(localPath):
                StateManager.Set(
                    f"{self.path}.avatar", localPath)
            else:
                StateManager.Set(
                    f"{self.path}.avatar", None)

            TSHSponsorHelper.ExportValidSponsors(team, self.path)

    def ExportPlayerCity(self, city=None):
        with self.dataLock:
            if StateManager.Get(f"{self.path}.city") != city:
                StateManager.Set(
                    f"{self.path}.city", city)

    def SwapCharacters(self, index1: int, index2: int):
        StateManager.BlockSaving()

        if index2 > len(self.character_elements)-1:
            index2 = 0

        char1 = self.character_elements[index1]
        char2 = self.character_elements[index2]

        # Save index1 settings
        tmp = [char1[1].currentText(), char1[2].currentIndex()]

        # Set index1 to index2
        # Character
        found = char1[1].findText(char2[1].currentText())
        if found != -1:
            char1[1].setCurrentIndex(found)
        else:
            char1[1].setCurrentText(char2[1].currentText())

        # Color
        char1[2].setCurrentIndex(char2[2].currentIndex())

        # Set index2 to temp (index1)
        # Character
        found = char2[1].findText(tmp[0])
        if found != -1:
            char2[1].setCurrentIndex(found)
        else:
            char2[1].setCurrentText(tmp[0])

        # Color
        char2[2].setCurrentIndex(tmp[1])

        self.CharactersChanged()

        StateManager.ReleaseSaving()

    def LoadStates(self, index):
        country: QComboBox = self.findChild(QComboBox, "country")

        new_code = None
        if country.currentData(Qt.ItemDataRole.UserRole) != None:
            new_code = country.currentData(
                Qt.ItemDataRole.UserRole).get("code")

        # Skip rebuild if the country code hasn't changed — the existing state
        # comboBox model is still valid and rebuilding is expensive (QIcon
        # decoding for every state flag). This matters during set loads where
        # the country often stays the same across successive SetData calls.
        if hasattr(self, "_loadedStatesCountryCode") and self._loadedStatesCountryCode == new_code:
            return
        self._loadedStatesCountryCode = new_code

        countryData = {}
        if new_code is not None:
            countryData = TSHCountryHelper.countries.get(new_code, {})

        stateModel = QStandardItemModel()

        noState = QStandardItem()
        noState.setData({}, Qt.ItemDataRole.UserRole)
        stateModel.appendRow(noState)

        states = countryData.get("states")

        if states is not None:
            for i, state_code in enumerate(states.keys()):
                item = QStandardItem()
                # Windows has some weird thing with files named CON.png. In case a state code is CON,
                # we try to load _CON.png instead
                path = f'./assets/state_flag/{countryData.get("code")}/{"_CON" if state_code == "CON" else state_code}.png'

                if not os.path.exists(path):
                    path = None

                states[state_code].update({
                    "asset": path
                })
                item.setIcon(QIcon(path))
                item.setData(states[state_code], Qt.ItemDataRole.UserRole)
                item.setData(
                    f'{states[state_code]["name"]} ({state_code})', Qt.ItemDataRole.EditRole)
                stateModel.appendRow(item)

        state: QComboBox = self.findChild(QComboBox, "state")
        state.setModel(stateModel)
        state.setCurrentIndex(0)

    def LoadSkinOptions(self, element, target):
        characterData = element.currentData()

        if characterData:
            target.setModel(TSHGameAssetManager.instance.skinModels.get(
                characterData.get("en_name")))
        else:
            target.setModel(QStandardItemModel())

    def GetCurrentPlayerTag(self):
        gamerTag = self.findChild(QWidget, "name").text()
        prefix = self.findChild(QWidget, "team").text()
        return prefix+" "+gamerTag if prefix else gamerTag

    def Clear(self, no_mains=False):
        StateManager.BlockSaving()
        with self.dataLock:
            for c in self.findChildren(QLineEdit):
                if c.objectName() != "" and c.objectName() != 'qt_spinbox_lineedit':
                    if c.text() != "":
                        c.setText("")
                        c.editingFinished.emit()

            for c in self.findChildren(QSpinBox):
                c.setValue(0)
                c.lineEdit().editingFinished.emit()

            for c in self.findChildren(QPlainTextEdit):
                if c.toPlainText() != "":
                    c.clear()
                    c.textChanged.emit()

            for c in self.findChildren(QComboBox):
                if (no_mains):
                    for charelem in self.character_elements:
                        for i in range(len(charelem)):
                            if charelem[i] == c:
                                break
                        else:
                            c.setCurrentIndex(0)
                        continue  # only executed if the inner loop DID break
                else:
                    c.setCurrentIndex(0)

        StateManager.ReleaseSaving()
