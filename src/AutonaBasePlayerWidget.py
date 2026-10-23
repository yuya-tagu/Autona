"""Base class for AutonaScoreboardPlayerWidget.

Originally extracted to share the methods that were identical (or
functionally equivalent) between this class and AutonaTeamPlayerWidget,
eliminating ~200 lines of duplication. AutonaTeamPlayerWidget was dead code
and was deleted on 2026-08-28 (Headless Core redesign Step 0), so
AutonaScoreboardPlayerWidget is now the only subclass.

Since the player instalment of headless core Step 5 this class no longer
writes the state itself: `self.player` is a `core.PlayerService` bound to
this widget's path, and the methods below read Qt (combo rows, line-edit
text, the losers flag) and hand the answer to it. What each of them decided
-- how a display name is merged, where an avatar file is looked for, the
shape of a character slot -- lives in that service.

Subclasses are responsible for:
- __init__ (own .ui file, own widget setup)
- NameChanged, and wiring the card's inputs to `self.player`
- SetData, SetCharactersPerPlayer, ReloadCharacters, SetIndex,
  SetupAutocomplete (widget-specific: .ui widgets, icon sizes, fonts,
  proxy models — kept per-subclass)
- _swapRestoreTail / _styleCountryStateWidgets (small hooks that customise
  the otherwise-shared SwapWith / _WireCountryState defined here)
"""
import threading
import traceback

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *

from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .core import StateSchema as schema
from .core.CountryCatalog import CountryCatalog
from .core.PlayerService import KEEP_ONLINE_AVATAR, PlayerService
from .core.StateManager import StateManager
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
        Initialises per-instance lock + findChild cache + state service."""
        self.dataLock = threading.RLock()
        # Everything this widget publishes goes through here (headless core
        # Step 5). Bound to self.path, which the subclass sets before
        # uic.loadUi and never rewrites.
        self.player = PlayerService(self.path)
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

            self.player.PublishCharacters(characters, includeMains)

    def ExportMergedName(self):
        with self.dataLock:
            self.player.PublishMergedName(
                self._w(schema.PLAYER_SPONSOR_KEY, QLineEdit).text(),
                self._w(schema.PLAYER_TAG_KEY, QLineEdit).text(),
                self.losers)

    def ExportPlayerImages(self, onlineAvatar=KEEP_ONLINE_AVATAR):
        """Re-publish the image state derived from the player's name.

        Reads the two name fields and hands them to
        `PlayerService.PublishImages`, whose docstring carries the
        online_avatar semantics (published / cleared / left alone) and the
        LIVE-BUG-1 history behind them.
        """
        with self.dataLock:
            self.player.PublishImages(
                self._w(schema.PLAYER_SPONSOR_KEY, QLineEdit).text(),
                self._w(schema.PLAYER_TAG_KEY, QLineEdit).text(),
                onlineAvatar=onlineAvatar)

    def ExportPlayerCity(self, city=None):
        with self.dataLock:
            self.player.SetCity(city)

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

    def _EnsureStateModel(self) -> QStandardItemModel:
        """Return the state combo's model, creating it on first use.

        The combo keeps ONE model for the widget's lifetime: rows are cleared
        and refilled in place, never swapped for a fresh model (same pattern as
        AutonaSelectSetWindow).

        Building a new QStandardItemModel per country change leaked. Measured
        on this widget: a set load changes the country twice (Clear() empties
        it, SetData puts it back), and each load left one model of ~50
        icon-bearing rows per player alive for the rest of the session --
        ~106 QStandardItems per loaded set, growing without bound.

        What holds the discarded model has NOT been pinned down: a bare
        QComboBox in isolation does release the model it is handed, so this is
        not the general rule "a combo keeps every model given to setModel()".
        Something about this widget's configuration keeps them, and rather than
        chase it, the model is never replaced -- which is safe whatever the
        mechanism turns out to be. Refilling in place also removes the
        setModel() swap that agent-pitfalls #13 had to guard with a retired
        generation: setModel() now runs exactly once, at construction, when the
        outgoing model is the combo's own empty default and no completer or
        popup can still be reading from it.
        """
        model = getattr(self, "_stateModel", None)
        if model is None:
            # Parented, so it dies with the widget rather than relying on the
            # attribute below being the last reference.
            model = QStandardItemModel(self)
            self._stateModel = model
            self._w(schema.PLAYER_STATE_KEY, QComboBox).setModel(model)
        return model

    def LoadStates(self, index):
        country: QComboBox = self._w(schema.PLAYER_COUNTRY_KEY, QComboBox)

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

        # A fresh dict per region with its flag `asset` already resolved
        # (the catalog checks the disk). This used to write `asset` into the
        # shared country table itself, so what the Web's get_states returned
        # depended on which countries some player widget had shown (S1c).
        states = None
        if new_code is not None:
            states = CountryCatalog.instance.States(new_code)

        # Build the rows first, then put them into the live model in one
        # blocked stretch (see below) — the flag lookups above touch the disk
        # and have no business happening between two model notifications.
        rows = []

        noState = QStandardItem()
        noState.setData({}, Qt.ItemDataRole.UserRole)
        rows.append(noState)

        if states is not None:
            for state_code, stateData in states.items():
                item = QStandardItem()
                item.setIcon(QIcon(stateData["asset"]))
                item.setData(stateData, Qt.ItemDataRole.UserRole)
                item.setData(
                    f'{stateData["name"]} ({state_code})', Qt.ItemDataRole.EditRole)
                rows.append(item)

        stateModel = self._EnsureStateModel()
        state: QComboBox = self._w(schema.PLAYER_STATE_KEY, QComboBox)

        # Refill in place, one row at a time through the model's own
        # appendRow(). invisibleRootItem().appendRows(rows) would hand the
        # whole list over in a single call, but PySide6 registers every item
        # appended that way on the root item's wrapper and never releases it --
        # the rows would then pile up for the widget's lifetime, exactly where
        # the discarded models used to. appendRow() on the model does not.
        #
        # Signals stay blocked for the whole clear + refill so consumers see a
        # single index change for the rebuild, exactly as they did when a
        # finished model was attached with setModel(). NOTE: the old setModel()
        # path also emitted currentTextChanged/editTextChanged; nothing is
        # connected to either on this combo, so only currentIndexChanged is
        # replayed here.
        with QSignalBlocker(state):
            stateModel.clear()
            for item in rows:
                stateModel.appendRow(item)
            state.setCurrentIndex(0)
        state.currentIndexChanged.emit(state.currentIndex())

        # Last: a country whose rebuild raised half-way must not be recorded as
        # loaded, or the guard above would keep serving the broken list.
        self._loadedStatesCountryCode = new_code

    def LoadSkinOptions(self, element, target):
        characterData = element.currentData()

        if characterData:
            target.setModel(AutonaGameAssetManager.instance.skinModels.get(
                characterData.get("en_name")))
        else:
            target.setModel(QStandardItemModel())

    def GetCurrentPlayerTag(self):
        gamerTag = self._w(schema.PLAYER_TAG_KEY).text()
        prefix = self._w(schema.PLAYER_SPONSOR_KEY).text()
        return prefix+" "+gamerTag if prefix else gamerTag

    def Clear(self, no_mains=False):
        with StateManager.SaveBlock(), self.dataLock:
            # online_avatar belongs to whoever was in this slot, and nothing
            # below derives it: the fields are what get emptied here, and the
            # avatar came from the provider, not from them. Clearing it used
            # to fall out of NameChanged -> ExportPlayerImages() defaulting to
            # None, which is exactly the accident that also wiped avatars that
            # were supposed to stay. Now it is stated where it is meant.
            self.player.SetOnlineAvatar(None)

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
                        # Neither field is a widget value the loop above
                        # could have carried across: the seed spinbox is not
                        # one of the three copied types and the city has no
                        # input at all, so both are moved through the state
                        # they were read from.
                        w.player.SetSeed(tmpData[i]["seed"])
                        w.player.SetCity(tmpData[i]["city"])
        finally:
            StateManager.ReleaseSaving()

    def _swapRestoreTail(self, data):
        """Subclass hook: per-widget restore action run after fields are copied
        and before seed/city are re-set. Default: no-op."""
        pass

    def _WireCountryState(self):
        """One-time wiring of the country / region pair. Subclasses call this
        exactly once, from __init__.

        Everything here is a connection or a widget that must exist once per
        card: the country completer, the country -> LoadStates link, the
        region combo's lifetime model, and the two country-model signals.
        Repeating any of it used to happen on every `countriesUpdated` (the
        old LoadCountries did all of this and was also that signal's slot),
        which stacked one more LoadStates connection per emit.
        """
        try:
            country: QComboBox = self._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
            # QCompleter() + setModel(), not QCompleter(model): the
            # constructor form keeps no Python reference to the model
            # (agent-pitfalls #15). The model is attached in LoadCountries.
            self._countryCompleter = QCompleter()
            country.setCompleter(self._countryCompleter)
            country.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            country.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            country.view().setMinimumWidth(60)
            country.completer().setCompletionMode(QCompleter.PopupCompletion)
            country.completer().popup().setMinimumWidth(300)

            # Attach the shared model before LoadStates is connected, as the
            # construction order always was: the index change that setModel()
            # causes here is not a user's choice and must not rebuild regions.
            self.LoadCountries()

            country.currentIndexChanged.connect(self.LoadStates)

            state: QComboBox = self._w(schema.PLAYER_STATE_KEY, QComboBox)
            # Attach the lifetime model first, while we are outside any signal
            # handler and nothing is connected to this combo yet: setModel()
            # re-points the completer and the view, so it belongs before they
            # are configured, not after. Every later LoadStates() only refills
            # its rows.
            self._EnsureStateModel()
            state.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            state.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            state.view().setMinimumWidth(60)
            state.completer().setCompletionMode(QCompleter.PopupCompletion)
            state.completer().popup().setMinimumWidth(300)

            self._styleCountryStateWidgets(country, state)

            signals = AutonaCountryHelper.signals
            signals.countriesAboutToReload.connect(
                self._OnCountriesAboutToReload)
            # Kept for the fallback below: a card built while the model is
            # still None gets it attached when the first load lands.
            signals.countriesUpdated.connect(self.LoadCountries)

        except Exception as e:
            logger.error(traceback.format_exc())
            exit()

    def LoadCountries(self):
        """Attach the shared country model to this card. Idempotent.

        Runs once from _WireCountryState and then on every
        `countriesUpdated`. When the combo already shows the shared model
        there is nothing to attach (the model is never swapped, only refilled
        -- see AutonaCountryHelper); the one thing left to do then is to
        finish a reload this card held (_OnCountriesAboutToReload).
        """
        try:
            if AutonaCountryHelper.countryModel is None:
                # Fallback: the window loads the countries before any card is
                # built, so this only runs when that load did not happen or
                # failed. If it fails again the model stays None and the
                # countriesUpdated connection attaches it later.
                AutonaCountryHelper.LoadCountries()

            model = AutonaCountryHelper.countryModel
            country: QComboBox = self._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
            if model is not None and country.model() is not model:
                self._countryCompleter.setModel(model)
                country.setModel(model)

            self._FinishCountryReload(country)

        except Exception as e:
            logger.error(traceback.format_exc())
            exit()

    def _OnCountriesAboutToReload(self):
        """The shared model is about to be cleared and refilled in place.

        The country combo would report that as -1 and then the first row,
        and each report goes on to LoadStates and to the state (the country
        and region of this slot published as blank). A reload is not a
        choice anyone made, so the combo is silenced until LoadCountries puts
        it back on the country it showed. The region combo is not touched:
        its rows belong to that same country.

        Idempotent: a second notice before the reload finishes (e.g. a load
        that failed after announcing itself, then another) keeps the first
        record. Recording again would capture the combo as already blocked
        and leave it blocked for good once the reload finished.
        """
        if "_countryReload" in self.__dict__:
            return
        country: QComboBox = self._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
        data = country.currentData(Qt.ItemDataRole.UserRole)
        code = data.get("code") if isinstance(data, dict) else None
        self._countryReload = (code, country.blockSignals(True))

    def _FinishCountryReload(self, country: QComboBox):
        pending = self.__dict__.pop("_countryReload", None)
        if pending is None:
            return
        code, wasBlocked = pending
        index = 0
        found = code is None
        try:
            model = country.model()
            if code is not None:
                for i in range(model.rowCount()):
                    data = model.index(i, 0).data(Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict) and data.get("code") == code:
                        index, found = i, True
                        break
            country.setCurrentIndex(index)
        finally:
            # Whatever happened above, the combo must not stay silenced.
            country.blockSignals(wasBlocked)
        if not found and not wasBlocked:
            # The country this card showed is gone from the reloaded data.
            # The combo now shows the blank row; let that through as the
            # ordinary change it is, so the state does not keep naming a
            # country the card no longer shows.
            country.currentIndexChanged.emit(index)

    def _styleCountryStateWidgets(self, country, state):
        """Subclass hook: per-skin styling of the country/state combos
        (Scoreboard mirrors placeholders, Team applies fonts). Default: no-op."""
        pass
