import json
import traceback

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from loguru import logger

from .core import StateSchema as schema
from .core.PlayerDB import PlayerDB
from .AutonaPlayerDB import AutonaPlayerDB
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
from . import ui_constants as UI

# StateManager base path the editor dialog writes to while it is open
# (StateSchema.PLAYER_EDITOR_ROOT). It is unset again when the dialog closes,
# and the launch seed purges it in case a crash left a stale subtree in the
# persisted state (user_data/state.json).
_EDITOR_PATH = f"{schema.PLAYER_EDITOR_ROOT}.1"

# Seed value GetEntrantsWorker assigns to unseeded entrants.
_UNSEEDED = 999999


class _PlayerEditorDialog(QDialog):
    """Modal editor for one local-DB player entry.

    Embeds the shared AutonaScoreboardPlayerWidget form. Because its path
    does not start with "score.", the widget shows its own
    Save new player / Update player / Delete player entry buttons, so this
    dialog only adds a Close button. The list views refresh themselves via
    AutonaPlayerDB.signals.db_updated after every save/delete."""

    def __init__(self, parent=None, playerData=None):
        super().__init__(parent)

        if playerData is None:
            self.setWindowTitle(QApplication.translate("app", "Add new player"))
        else:
            self.setWindowTitle(QApplication.translate("app", "Edit player"))

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        self.layout().setSpacing(UI.SPACE_SM)

        self.editor = AutonaScoreboardPlayerWidget(
            index=1, teamNumber=1, path=_EDITOR_PATH)
        # Move up/down only makes sense inside a multi-player column.
        self.editor.btMoveUp.hide()
        self.editor.btMoveDown.hide()
        # Allow registering up to 3 mains (matches the scoreboard default).
        self.editor.SetCharactersPerPlayer(3)

        if playerData is not None:
            # playerData IS the DB record — skip the redundant DB lookup.
            self.editor.SetData(playerData, dontLoadFromDB=True, clear=True)
        # Sync the Save/Update button label and Delete enablement with the
        # current tag (SetData only wires these to editingFinished).
        self.editor.ManageSavePlayerToDBText()
        self.editor.ManageDeletePlayerFromDBActive()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        self.layout().addWidget(scroll)

        closeBt = QPushButton(QApplication.translate("app", "Close"))
        closeBt.clicked.connect(self.accept)
        self.layout().addWidget(closeBt)

        self.resize(440, 640)

    def done(self, result):
        # The editor mirrors every field into player_editor.* — scrub the
        # temporary subtree so it doesn't linger in the persisted state.
        schema.ClearPlayerEditor()
        # _deleted flag stops the widget's global-signal handlers
        # (db_updated / onLoad) from touching a dying C++ object.
        self.editor.deleteLater()
        super().done(result)


class AutonaPlayerBrowserWidget(QDockWidget):
    """Player List panel with two tabs:

    * Tournament Entrants — read-only, seed-ordered view of the entrants of
      the currently loaded tournament (provider.top_entrants joined with the
      local player DB), plus "Set as P1/P2" quick actions.
    * Registered Players — browse/search the local player DB
      (user_data/local_players.json) with add / edit / delete management.
    """

    def __init__(self, *args):
        super().__init__(*args)

        self.setWindowTitle(QApplication.translate("app", "Player List"))
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.setFloating(False)

        # NOTE: this panel is built on first reveal (startup Step 4b-3), so
        # the purge of state left behind by removed features / crashed
        # sessions (player_list.* from the retired PLAYERS roster editor,
        # player_editor.* from the editor dialog) moved to
        # Window._SeedPanelState() — it has to happen at every launch, not
        # only on the launches where the operator opens this panel.

        self._entrantsDirty = True
        self._registeredDirty = True

        container = QWidget()
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWidget(container)
        container.setLayout(QVBoxLayout())
        container.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        container.layout().setSpacing(UI.SPACE_XS)

        self.tabs = QTabWidget()
        container.layout().addWidget(self.tabs)

        self.tabs.addTab(
            self._buildEntrantsTab(),
            QApplication.translate("app", "Tournament Entrants"))
        self.tabs.addTab(
            self._buildRegisteredTab(),
            QApplication.translate("app", "Registered Players"))

        # Connect signals (in __init__, per project rules). Handlers mark the
        # affected tab dirty and rebuild immediately only while visible; the
        # showEvent below picks up deferred rebuilds.
        AutonaTournamentDataProvider.instance.signals.entrants_updated.connect(
            self._OnEntrantsUpdated)
        AutonaPlayerDB.signals.db_updated.connect(self._OnDbUpdated)
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._OnGameChanged)

    # ── Tab construction ───────────────────────────────────────────

    def _makeView(self, model):
        view = QTableView()
        view.setModel(model)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.verticalHeader().setVisible(False)
        view.verticalHeader().setDefaultSectionSize(26)
        view.horizontalHeader().setStretchLastSection(True)
        return view

    def _buildEntrantsTab(self):
        self.entrantsStack = QStackedWidget()

        # Page 0 — empty state (no tournament / no entrants yet).
        emptyLabel = QLabel(QApplication.translate(
            "app", "No tournament loaded. Set a tournament URL in the EVENT panel."))
        emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLabel.setWordWrap(True)
        self.entrantsStack.addWidget(emptyLabel)

        # Page 1 — action row + table.
        content = QWidget()
        content.setLayout(QVBoxLayout())
        content.layout().setContentsMargins(0, UI.SPACE_XS, 0, 0)
        content.layout().setSpacing(UI.SPACE_XS)

        actionRow = QHBoxLayout()
        actionRow.setContentsMargins(0, 0, 0, 0)
        actionRow.setSpacing(UI.SPACE_SM)

        self.entrantsSearchEdit = QLineEdit()
        self.entrantsSearchEdit.setPlaceholderText(
            QApplication.translate("app", "Search players"))
        self.entrantsSearchEdit.setClearButtonEnabled(True)
        actionRow.addWidget(self.entrantsSearchEdit, 1)

        teamTip = QApplication.translate(
            "app", "In team battle formats, this sets the first player of the team.")
        self.btSetP1 = QPushButton(QApplication.translate("app", "Set as P1"))
        self.btSetP1.setToolTip(teamTip)
        self.btSetP1.setEnabled(False)
        self.btSetP1.clicked.connect(lambda: self._SetToScoreboard(1))
        actionRow.addWidget(self.btSetP1)
        self.btSetP2 = QPushButton(QApplication.translate("app", "Set as P2"))
        self.btSetP2.setToolTip(teamTip)
        self.btSetP2.setEnabled(False)
        self.btSetP2.clicked.connect(lambda: self._SetToScoreboard(2))
        actionRow.addWidget(self.btSetP2)
        self.btEditEntrant = QPushButton(
            QApplication.translate("app", "Edit player"))
        self.btEditEntrant.setEnabled(False)
        self.btEditEntrant.clicked.connect(self._EditSelectedEntrant)
        actionRow.addWidget(self.btEditEntrant)
        content.layout().addLayout(actionRow)

        self.entrantsModel = QStandardItemModel(self)
        self.entrantsProxy = QSortFilterProxyModel(self)
        self.entrantsProxy.setSourceModel(self.entrantsModel)
        self.entrantsProxy.setFilterCaseSensitivity(
            Qt.CaseSensitivity.CaseInsensitive)
        self.entrantsProxy.setFilterKeyColumn(-1)  # match any column
        self.entrantsSearchEdit.textChanged.connect(
            self.entrantsProxy.setFilterFixedString)

        self.entrantsView = self._makeView(self.entrantsProxy)
        self.entrantsView.selectionModel().selectionChanged.connect(
            self._OnEntrantSelectionChanged)
        self.entrantsView.doubleClicked.connect(
            lambda _index: self._EditSelectedEntrant())
        content.layout().addWidget(self.entrantsView)

        self.entrantsStack.addWidget(content)
        return self.entrantsStack

    def _buildRegisteredTab(self):
        page = QWidget()
        page.setLayout(QVBoxLayout())
        page.layout().setContentsMargins(0, UI.SPACE_XS, 0, 0)
        page.layout().setSpacing(UI.SPACE_XS)

        topRow = QHBoxLayout()
        topRow.setContentsMargins(0, 0, 0, 0)
        topRow.setSpacing(UI.SPACE_SM)

        self.searchEdit = QLineEdit()
        self.searchEdit.setPlaceholderText(
            QApplication.translate("app", "Search players"))
        self.searchEdit.setClearButtonEnabled(True)
        topRow.addWidget(self.searchEdit, 1)

        # Negative action leftmost in the button cluster (app-wide
        # button-pair convention), positive actions to its right.
        self.deleteBt = QPushButton(
            QApplication.translate("app", "Delete selected player"))
        self.deleteBt.setObjectName("danger")
        self.deleteBt.setEnabled(False)
        self.deleteBt.clicked.connect(self._DeleteSelectedPlayer)
        topRow.addWidget(self.deleteBt)

        addBt = QPushButton(QApplication.translate("app", "Add new player"))
        addBt.setIcon(QIcon(UI.icon_path("add_user.svg")))
        addBt.clicked.connect(self._AddPlayer)
        topRow.addWidget(addBt)

        self.editBt = QPushButton(QApplication.translate("app", "Edit player"))
        self.editBt.setEnabled(False)
        self.editBt.clicked.connect(self._EditSelectedPlayer)
        topRow.addWidget(self.editBt)

        page.layout().addLayout(topRow)

        self.registeredModel = QStandardItemModel(self)
        self.registeredProxy = QSortFilterProxyModel(self)
        self.registeredProxy.setSourceModel(self.registeredModel)
        self.registeredProxy.setFilterCaseSensitivity(
            Qt.CaseSensitivity.CaseInsensitive)
        self.registeredProxy.setFilterKeyColumn(-1)  # match any column
        self.searchEdit.textChanged.connect(
            self.registeredProxy.setFilterFixedString)

        self.registeredView = self._makeView(self.registeredProxy)
        self.registeredView.setSortingEnabled(True)
        self.registeredView.selectionModel().selectionChanged.connect(
            self._OnRegisteredSelectionChanged)
        self.registeredView.doubleClicked.connect(
            lambda _index: self._EditSelectedPlayer())
        page.layout().addWidget(self.registeredView)

        return page

    # ── Refresh triggers ───────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if self._entrantsDirty:
            self._SafeRebuild(self._RebuildEntrants)
        if self._registeredDirty:
            self._SafeRebuild(self._RebuildRegistered)

    def _OnEntrantsUpdated(self):
        self._entrantsDirty = True
        if self.isVisible():
            self._SafeRebuild(self._RebuildEntrants)

    def _OnDbUpdated(self):
        # Entrant rows also show DB-derived data (country, mains), so a DB
        # change invalidates both tabs.
        self._entrantsDirty = True
        self._registeredDirty = True
        if self.isVisible():
            self._SafeRebuild(self._RebuildEntrants)
            self._SafeRebuild(self._RebuildRegistered)

    def _OnGameChanged(self):
        # Character icons/names are per-game.
        self._entrantsDirty = True
        self._registeredDirty = True
        if self.isVisible():
            self._SafeRebuild(self._RebuildEntrants)
            self._SafeRebuild(self._RebuildRegistered)

    def _SafeRebuild(self, fn):
        try:
            fn()
        except Exception:
            logger.error(traceback.format_exc())

    # ── Model building ─────────────────────────────────────────────

    @staticmethod
    def _MainsForCurrentGame(player):
        """Return the player's mains for the currently selected game as a
        list of [character, skin, variant] entries (defensively parsed —
        old DB rows may store mains as a JSON string)."""
        if not player:
            return []
        mains = player.get("mains")
        if isinstance(mains, str):
            try:
                mains = json.loads(mains)
            except Exception:
                return []
        if not isinstance(mains, dict):
            return []
        game = AutonaGameAssetManager.instance.selectedGame or {}
        codename = game.get("codename")
        if not codename:
            return []
        result = []
        for main in mains.get(codename, []) or []:
            if isinstance(main, list) and len(main) > 0 and main[0]:
                result.append(main)
        return result

    @classmethod
    def _CharacterItems(cls, player):
        """(icon, text) for the player's mains in the current game."""
        mains = cls._MainsForCurrentGame(player)
        if not mains:
            return QIcon(), ""
        icon = QIcon()
        try:
            skin = int(mains[0][1]) if len(mains[0]) > 1 else 0
        except (TypeError, ValueError):
            skin = 0
        try:
            icon = AutonaGameAssetManager.instance.GetStockIcon(
                mains[0][0], skin)
        except Exception:
            logger.error(traceback.format_exc())
        return icon, ", ".join(str(m[0]) for m in mains)

    @staticmethod
    def _Item(text, icon=None, userData=None):
        item = QStandardItem(str(text) if text else "")
        if icon is not None and not icon.isNull():
            item.setIcon(icon)
        if userData is not None:
            item.setData(userData, Qt.ItemDataRole.UserRole)
        return item

    def _RebuildEntrants(self):
        self._entrantsDirty = False

        provider = AutonaTournamentDataProvider.instance.provider
        entrants = (getattr(provider, "top_entrants", []) or []) if provider else []

        if not entrants:
            self.entrantsStack.setCurrentIndex(0)
            self.entrantsModel.setRowCount(0)
            return
        self.entrantsStack.setCurrentIndex(1)

        self.entrantsModel.setRowCount(0)
        self.entrantsModel.setColumnCount(6)
        self.entrantsModel.setHorizontalHeaderLabels([
            QApplication.translate("app", "Seed"),
            QApplication.translate("app", "Sponsor"),
            QApplication.translate("app", "GamerTag"),
            QApplication.translate("app", "Real Name"),
            QApplication.translate("app", "Country"),
            QApplication.translate("app", "Characters"),
        ])

        for entrant in entrants:
            try:
                tag = entrant.get("gamerTag") or ""
                seed = entrant.get("seed")
                seedText = "-" if not seed or seed >= _UNSEEDED else str(seed)
                player = PlayerDB.instance.GetPlayerFromTag(tag) if tag else None
                player = player or {}
                charIcon, charText = self._CharacterItems(player)
                self.entrantsModel.appendRow([
                    self._Item(seedText, userData=tag),
                    self._Item(player.get("prefix")),
                    self._Item(tag),
                    self._Item(player.get("name")),
                    self._Item(player.get("country_code")),
                    self._Item(charText, icon=charIcon),
                ])
            except Exception:
                logger.error(traceback.format_exc())

        # Hide the Real Name column (index 3) — kept in the model so search
        # still matches on it, but not shown.
        self.entrantsView.setColumnHidden(3, True)
        self.entrantsView.resizeColumnsToContents()
        self._OnEntrantSelectionChanged()

    def _RebuildRegistered(self):
        self._registeredDirty = False

        self.registeredModel.setRowCount(0)
        self.registeredModel.setColumnCount(6)
        self.registeredModel.setHorizontalHeaderLabels([
            QApplication.translate("app", "GamerTag"),
            QApplication.translate("app", "Sponsor"),
            QApplication.translate("app", "Real Name"),
            QApplication.translate("app", "Country"),
            QApplication.translate("app", "Characters"),
            QApplication.translate("app", "Twitter"),
        ])

        for fullTag, player in sorted(
                PlayerDB.instance.Items(), key=lambda kv: kv[0].lower()):
            if player is None:
                continue
            try:
                charIcon, charText = self._CharacterItems(player)
                # UserRole holds the full DB key ("prefix gamerTag") — the
                # only safe handle for edit/delete (DeletePlayer matches dict
                # keys, unlike GetPlayerFromTag's gamerTag-only search).
                self.registeredModel.appendRow([
                    self._Item(player.get("gamerTag"), userData=fullTag),
                    self._Item(player.get("prefix")),
                    self._Item(player.get("name")),
                    self._Item(player.get("country_code")),
                    self._Item(charText, icon=charIcon),
                    self._Item(player.get("twitter")),
                ])
            except Exception:
                logger.error(traceback.format_exc())

        # Hide the Real Name column (index 2) — kept in the model so search
        # still matches on it, but not shown.
        self.registeredView.setColumnHidden(2, True)
        self.registeredView.resizeColumnsToContents()
        self._OnRegisteredSelectionChanged()

    # ── Selection / actions ────────────────────────────────────────

    def _OnEntrantSelectionChanged(self, *args):
        hasSelection = self.entrantsView.selectionModel().hasSelection()
        self.btSetP1.setEnabled(hasSelection)
        self.btSetP2.setEnabled(hasSelection)
        self.btEditEntrant.setEnabled(hasSelection)

    def _OnRegisteredSelectionChanged(self, *args):
        hasSelection = self.registeredView.selectionModel().hasSelection()
        self.editBt.setEnabled(hasSelection)
        self.deleteBt.setEnabled(hasSelection)

    def _SelectedEntrantTag(self):
        rows = self.entrantsView.selectionModel().selectedRows(0)
        if not rows:
            return None
        return rows[0].data(Qt.ItemDataRole.UserRole)

    def _SelectedRegisteredTag(self):
        rows = self.registeredView.selectionModel().selectedRows(0)
        if not rows:
            return None
        return rows[0].data(Qt.ItemDataRole.UserRole)

    def _SetToScoreboard(self, team):
        tag = self._SelectedEntrantTag()
        if not tag:
            return
        # Deferred import: AutonaScoreboardManager pulls in the full
        # scoreboard stack (same pattern as AutonaStatsWidget._refresh).
        from .AutonaScoreboardManager import AutonaScoreboardManager
        scoreboard = AutonaScoreboardManager.instance.GetScoreboard(1)
        if not scoreboard.LoadPlayerFromTag(tag, team, 1):
            logger.warning(
                f"Player Browser: '{tag}' not found in the local player DB")

    def _EditSelectedEntrant(self):
        tag = self._SelectedEntrantTag()
        if not tag:
            return
        # Entrant rows only carry the gamerTag; look up the full DB record so
        # the editor pre-fills sponsor / mains / country. GetEntrantsWorker
        # merges every entrant into the DB, but fall back to a bare record if
        # the player somehow isn't there yet. Saving writes back to the DB
        # (overwrite=True) and db_updated refreshes both tabs.
        player = PlayerDB.instance.GetPlayerFromTag(tag) or {"gamerTag": tag}
        dialog = _PlayerEditorDialog(self, playerData=player)
        dialog.exec()

    def _AddPlayer(self):
        dialog = _PlayerEditorDialog(self)
        dialog.exec()

    def _EditSelectedPlayer(self):
        fullTag = self._SelectedRegisteredTag()
        if not fullTag:
            return
        player = PlayerDB.instance.Get(fullTag)
        if player is None:
            logger.warning(f"Player Browser: stale row, '{fullTag}' not in DB")
            return
        dialog = _PlayerEditorDialog(self, playerData=player)
        dialog.exec()

    def _DeleteSelectedPlayer(self):
        fullTag = self._SelectedRegisteredTag()
        if not fullTag:
            return
        answer = QMessageBox.question(
            self,
            QApplication.translate("app", "Delete selected player"),
            QApplication.translate(
                "app", "Are you sure you want to delete this player?")
            + f"\n\n{fullTag}",
        )
        if answer == QMessageBox.StandardButton.Yes:
            PlayerDB.instance.DeletePlayer(fullTag)
