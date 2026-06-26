"""Base class for AutonaScoreboardPlayerWidget and AutonaTeamPlayerWidget.

Extracts methods that are identical (or functionally equivalent) between
both widget classes, eliminating ~200 lines of code duplication.

Subclasses are responsible for:
- __init__ (different .ui files, different widget setup)
- ComboBoxIndexChanged (Scoreboard emits dataChanged, Team does not)
- NameChanged (Scoreboard calls ExportPlayerId, Team does not)
- SetData, SetCharactersPerPlayer, ReloadCharacters, SetIndex,
  SetupAutocomplete (genuinely divergent: different .ui widgets, icon
  sizes, fonts, proxy models, and per-mode fields — kept per-subclass)
- _swapRestoreTail / _styleCountryStateWidgets (small hooks that customise
  the otherwise-shared SwapWith / LoadCountries defined here)
"""
import os
import re
import threading
import traceback

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *

from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Helpers.AutonaSponsorHelper import AutonaSponsorHelper
from .StateManager import StateManager
from .AutonaGameAssetManager import AutonaGameAssetManager
from loguru import logger


class AutonaBasePlayerWidget(QGroupBox):
    countries = None
    countryModel = None
    characterModel = None
    _deleted = False

    # NOTE: dataLock is now an *instance* attribute initialised in
    # _initBasePlayerWidget(). Keeping it as a class attribute meant every
    # player widget shared a single RLock — bulk SetData calls on different
    # players serialised on the same lock for no good reason.

    def _initBasePlayerWidget(self):
        """Subclasses MUST call this from their __init__ after uic.loadUi.
        Initialises per-instance lock + findChild cache."""
        self.dataLock = threading.RLock()
        # Lazy widget cache — populated by _w(); avoids O(n) findChild walks
        # on hot paths (SetData, ExportMergedName, ExportPlayerImages...).
        self._widget_cache = {}

    def _w(self, name, klass=QWidget):
        """Cached findChild. Same lookup name always returns the same
        widget instance, so we can memoise by objectName regardless of
        the requested klass (caller-side type hint only)."""
        cache = self.__dict__.get("_widget_cache")
        if cache is None:
            # Subclass forgot to call _initBasePlayerWidget — fall back to
            # uncached findChild rather than crash.
            return self.findChild(klass, name)
        cached = cache.get(name)
        if cached is not None:
            return cached
        found = self.findChild(klass, name)
        if found is not None:
            cache[name] = found
        return found

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
            team = self._w("team", QLineEdit).text()
            name = self._w("name", QLineEdit).text()
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
        with self.dataLock, StateManager.SaveBlock():
            team = self._w("team", QLineEdit).text()
            name = self._w("name", QLineEdit).text()
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

            AutonaSponsorHelper.ExportValidSponsors(team, self.path)

    def ExportPlayerCity(self, city=None):
        with self.dataLock:
            if StateManager.Get(f"{self.path}.city") != city:
                StateManager.Set(
                    f"{self.path}.city", city)

    def SwapCharacters(self, index1: int, index2: int):
        with StateManager.SaveBlock():
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

    def LoadStates(self, index):
        country: QComboBox = self._w("country", QComboBox)

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
            countryData = AutonaCountryHelper.countries.get(new_code, {})

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

        state: QComboBox = self._w("state", QComboBox)
        state.setModel(stateModel)
        state.setCurrentIndex(0)

    def LoadSkinOptions(self, element, target):
        characterData = element.currentData()

        if characterData:
            target.setModel(AutonaGameAssetManager.instance.skinModels.get(
                characterData.get("en_name")))
        else:
            target.setModel(QStandardItemModel())

    def GetCurrentPlayerTag(self):
        gamerTag = self._w("name").text()
        prefix = self._w("team").text()
        return prefix+" "+gamerTag if prefix else gamerTag

    def Clear(self, no_mains=False):
        with StateManager.SaveBlock(), self.dataLock:
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

    def SwapWith(self, other):
        """Swap all field values + seed/city/avatar/id state between this
        widget and another. The per-widget restore action after the fields are
        copied differs by subclass (see _swapRestoreTail)."""
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
                        w._swapRestoreTail(tmpData[i])
                        StateManager.Set(f"{w.path}.seed", tmpData[i]["seed"])
                        StateManager.Set(f"{w.path}.city", tmpData[i]["city"])
        finally:
            StateManager.ReleaseSaving()

    def _swapRestoreTail(self, data):
        """Subclass hook: per-widget restore action run after fields are copied
        and before seed/city are re-set. Default: no-op."""
        pass

    def LoadCountries(self):
        try:
            if AutonaCountryHelper.countryModel == None:
                AutonaCountryHelper.LoadCountries()

            countryCompleter = QCompleter(
                AutonaCountryHelper.countryModel)

            country: QComboBox = self._w("country", QComboBox)
            country.setCompleter(countryCompleter)
            country.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            country.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            country.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            country.view().setMinimumWidth(60)
            country.completer().setCompletionMode(QCompleter.PopupCompletion)
            country.completer().popup().setMinimumWidth(300)
            country.setModel(AutonaCountryHelper.countryModel)

            country.currentIndexChanged.connect(self.LoadStates)

            state: QComboBox = self._w("state", QComboBox)
            state.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            state.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            state.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            state.view().setMinimumWidth(60)
            state.completer().setCompletionMode(QCompleter.PopupCompletion)
            state.completer().popup().setMinimumWidth(300)

            self._styleCountryStateWidgets(country, state)

        except Exception as e:
            logger.error(traceback.format_exc())
            exit()

    def _styleCountryStateWidgets(self, country, state):
        """Subclass hook: per-skin styling of the country/state combos
        (Scoreboard mirrors placeholders, Team applies fonts). Default: no-op."""
        pass
