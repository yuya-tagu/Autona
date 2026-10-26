import os
import re
import traceback

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
# PySide6-only, and the binding is pinned (CLAUDE.md / build-and-release.md).
# Used by _releaseLayoutItemWrappers below; see tests/test_wrapper_lifetime.py.
from shiboken6 import Shiboken
from .core import StateSchema as schema
from .core.Actions import AutonaActions
from .core.CountryCatalog import CountryCatalog
from .core.DictHelper import deep_clone
from .core.PlayerService import CharacterSlot
from .core.StateManager import StateManager
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaPlayerDB import AutonaPlayerDB
from .core.PlayerDB import PlayerDB
from .Helpers.AutonaDirHelper import AutonaResolve
from .Helpers.AutonaQtHelper import HeldBackText, NoIconProxyModel
from . import ui_constants as UI
from . import ui_layout as L
from .Workers import Worker
from .AutonaBasePlayerWidget import AutonaBasePlayerWidget
import threading
from loguru import logger


def _releaseLayoutItemWrappers(layout):
    """Drop PySide6's registrations for `layout`'s items, recursively.

    QWidgetItem and QSpacerItem are not QObjects, so PySide6 has no way to
    notice when Qt frees them -- and Qt frees them constantly: moving a widget
    into another layout deletes the QWidgetItem that used to hold it, and
    destroying a layout deletes all of its items. Any Python wrapper handed
    out for one of those items (itemAt(), spacerItem()) is registered in
    Shiboken's binding manager and, worse, kept alive there as a child of the
    layout -- so it is still flagged valid, and still pointing at the freed
    memory, long after the item is gone.

    That stale registration is what kills the process. The next C++ object
    allocated at the same address is looked up through it (Qt calls the
    virtual metaObject() from inside a QObject constructor, before Shiboken
    has registered the new object) and PySide reads a QMetaObject out of the
    dead wrapper's type: PySide::SignalManager::retrieveMetaObject
    dereferences a null TypeUserData and the process dies with an access
    violation, in unrelated code, much later.

    Calling this *before* the items are destroyed clears the registrations
    while they are still valid, so nothing is left behind. A caller that
    still holds one of the wrappers afterwards gets a RuntimeError from
    PySide instead of corrupting the heap, which is the outcome we want.

    Sub-layouts are skipped: a QLayout is a QObject, and PySide does track
    those. Recursing into them reaches the widget items they hold.
    """
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item is None:
            continue
        sub = item.layout()
        if sub is not None:
            _releaseLayoutItemWrappers(sub)
            continue
        Shiboken.invalidate(item)


class AutonaScoreboardPlayerWidgetSignals(QObject):
    # `dataChanged` used to sit here and was emitted from the three input
    # handlers below. Nothing ever connected to it -- the state delta is how
    # a change reaches anything interested -- so it went with the write path
    # it was announcing (headless core Step 5, instalment 3). The four
    # statistics triggers (playerId_changed, player{1,2}Id_changed,
    # player_seed_changed) went with ADR-005 S3e-5: the statistics are
    # asked for by core (PlayerService.Announce).
    # A render had to leave a field alone because the operator was in it,
    # and focus has now left it (ADR-005 S2a). The board answers with a
    # render pass.
    render_requested = Signal()



class AutonaScoreboardPlayerWidget(AutonaBasePlayerWidget):
    countries = None
    countryModel = None
    characterModel = None
    _deleted = False

    def __init__(self, index=0, teamNumber=0, path="", *args):
        super().__init__(*args)

        self.instanceSignals = AutonaScoreboardPlayerWidgetSignals()

        self.path = path

        self.index = index
        self.teamNumber = teamNumber

        # The slot as of the last complete RenderFromState (None: render
        # everything next time).
        self._renderedSlot = None
        # True while a render shows the slot in the fields the operator is
        # in as well: a load the operator asked this card for (SetData) or
        # its clear button.
        self._renderingOver = False
        # The widgets the last render left alone because the operator was
        # editing them; once focus has left all of them, the card asks for
        # another render (_OnFocusChanged).
        self._focusSkipped = []
        # By field: the text each line edit (and the note) last showed in
        # step with the state, and the text a render had to leave in one the
        # operator was in. Finishing the edit with the latter is not an edit
        # (_OnTextChanged / _OnNoteChanged); text typed since is
        # (HeldBackText).
        self._heldBack = HeldBackText()

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
        # Reached through the QObject tree, NOT through grid.itemAt(i).
        # itemAt() registers the QLayoutItem it hands out as a Shiboken child
        # of the layout, so the wrapper outlives this loop -- and a
        # QWidgetItem is not a QObject, so when Qt destroys it PySide6 is
        # never told. _buildCompactDetailsUI() below destroys exactly these
        # items, by re-homing the widgets they hold into the details dialog,
        # which would leave the wrappers registered in Shiboken's binding
        # manager still flagged valid and pointing at freed memory. The next
        # QObject allocated at one of those addresses is then looked up
        # through a dead wrapper and the process dies with an access
        # violation inside PySide::SignalManager::retrieveMetaObject -- in
        # unrelated code, minutes later. Sub-layouts are QObjects, whose
        # destruction PySide does track. See tests/test_wrapper_lifetime.py.
        for sub in grid.findChildren(
                QLayout, options=Qt.FindChildOption.FindDirectChildrenOnly):
            sub.setContentsMargins(0, 0, 0, 0)
            sub.setSpacing(UI.SPACE_SM)

        seed = self.findChild(QSpinBox, schema.PLAYER_SEED_KEY)
        seed.setMaximumWidth(seed.sizeHint().width() // 2)

        # The Twitter field has no inline label anymore; carry the field name
        # in the placeholder. "Twitter" only exists in the "app" translation
        # context (not the .ui Form context), so set it here rather than in
        # the .ui.
        twitter = self.findChild(QLineEdit, schema.PLAYER_TWITTER_KEY)
        twitter.setPlaceholderText(QApplication.translate("app", "Twitter"))

        # Birthday flag is not surfaced anywhere downstream yet; keep it
        # hidden unconditionally. Visibility of twitter / twitterLabel and
        # the other per-field rows is driven by the eye-menu toggles in
        # AutonaScoreboardWidget.
        self.findChild(QCheckBox, schema.PLAYER_BIRTHDAY_KEY).setVisible(False)

        # Seed is not user-editable from the widget anymore. The spinbox
        # stays in the layout as a data holder (other code paths read /
        # write its value), but both it and its label are permanently
        # hidden. What the user sees is the seedDisplay label bolted onto
        # the sponsor row, updated whenever the spinbox value changes.
        self.findChild(QLabel, "seedLabel").setVisible(False)
        seed.setVisible(False)
        # Only for the PlayerDB editor, which keeps this row. The scoreboard
        # card strips it out entirely in _buildCompactDetailsUI() below, so
        # collapsing its spacers there is dead work -- and not free: itemAt()
        # registers the QLayoutItem it returns as a Shiboken child of the
        # layout, so the wrapper outlives this loop, and QSpacerItem /
        # QWidgetItem are not QObjects, so PySide6 is never told when Qt
        # frees them. The registrations would be left in Shiboken's binding
        # manager still flagged valid and pointing at freed memory, and the
        # next QObject allocated at one of those addresses dies inside
        # PySide::SignalManager::retrieveMetaObject -- an access violation in
        # unrelated code, much later. See tests/test_wrapper_lifetime.py.
        if not self.path.startswith("score."):
            seedRowLayout = self.findChild(QHBoxLayout, "horizontalLayout")
            for i in range(seedRowLayout.count()):
                it = seedRowLayout.itemAt(i)
                sp = it.spacerItem() if it is not None else None
                if sp is not None:
                    sp.changeSize(0, 0, QSizePolicy.Minimum,
                                  QSizePolicy.Minimum)

        self.seedDisplay = self.findChild(QLabel, "seedDisplay")
        # The .ui used to hardcode rgba(255,255,255,0.75) which became
        # invisible on the light palette. Drive the colour from the
        # current theme tokens instead (Design.md §5.4 / §8 T-14).
        self.seedDisplay.setStyleSheet(self._seedDisplayStyle())

        # A bound method rather than the closure it used to be: the render
        # side (RenderFromState) sets the spinbox with its signals blocked
        # and has to put the badge right itself.
        seed.valueChanged.connect(self._ShowSeed)
        self._ShowSeed(seed.value())

        country = self.findChild(QComboBox, schema.PLAYER_COUNTRY_KEY)
        state = self.findChild(QComboBox, schema.PLAYER_STATE_KEY)

        # Custom textbox
        custom_textbox_layout = QHBoxLayout()
        custom_textbox_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_textbox = QPlainTextEdit()
        custom_textbox_layout.addWidget(self.custom_textbox)
        self.layout().addLayout(custom_textbox_layout, 98, 0, 1, 1)
        # Kept as attribute so _buildCompactDetailsUI can strip the row.
        self._customTextboxRow = custom_textbox_layout
        # The objectName is what the eye menu's "show_additional" toggle
        # finds this box by, and it is also its state key
        # (StateSchema.PLAYER_NOTE_KEY) -- the literal is kept here, and the
        # two spellings are pinned together in
        # tests/test_objectname_keypath_contract.py.
        self.custom_textbox.setObjectName("custom_textbox")
        self.custom_textbox.setFixedHeight(36)
        self.custom_textbox.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.custom_textbox.setPlaceholderText(QApplication.translate("app", "Additional information"))
        self.custom_textbox.textChanged.connect(self._OnNoteChanged)

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

        # Once: completer, country model, country -> regions link, and the
        # countriesAboutToReload / countriesUpdated connections.
        self._WireCountryState()

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
            self.findChild(QLineEdit, schema.PLAYER_TAG_KEY).editingFinished.connect(
                self.ManageSavePlayerToDBText)
            self.findChild(QLineEdit, schema.PLAYER_SPONSOR_KEY).editingFinished.connect(
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
            # Delete goes LEFT of Save (negative action left, positive
            # action right — the app-wide button-pair convention).
            save_row_layout.insertWidget(0, self.delete_bt)
            self.findChild(QLineEdit, schema.PLAYER_TAG_KEY).editingFinished.connect(
                self.ManageDeletePlayerFromDBActive)
            self.findChild(QLineEdit, schema.PLAYER_SPONSOR_KEY).editingFinished.connect(
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

        self._WireInputs(seed)

        # After _WireInputs, so on the same editingFinished the tag / sponsor
        # reaches the state (_OnTextChanged) before the display name is
        # published (NameChanged; ADR-005 S2b). AutonaActions.SetTeamLosers
        # rebuilds the team's names from the state, from any thread: with
        # NameChanged first, a flip landing between the two rebuilt the name
        # from the previous text after the card's own publish, so " [L]" was
        # left on the old name.
        self.findChild(QLineEdit, schema.PLAYER_TAG_KEY).editingFinished.connect(
            self.NameChanged)
        self.findChild(QLineEdit, schema.PLAYER_SPONSOR_KEY).editingFinished.connect(
            self.NameChanged)

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
            # Only board cards render from the state (RenderFromState), so
            # only they have fields to come back to. A bound method: Qt drops
            # the connection with the card.
            QApplication.instance().focusChanged.connect(self._OnFocusChanged)

    def deleteLater(self):
        self._deleted = True
        super().deleteLater()

    def _buildCompactDetailsUI(self):
        """Re-home every detail field into a modal QDialog (Web UI parity).

        The dialog is a QObject child of this widget, so findChild /
        findChildren lookups (eye-menu toggles, Clear, SetData, the render)
        keep resolving the same widget instances — only their visual home
        changes. The hidden data holders (seed spinbox, seedLabel,
        birthday) stay parented to the widget without a layout.
        """
        grid = self.layout()

        # Everything below re-homes the detail fields into the dialog and
        # strips the emptied rows, which frees the QWidgetItems and
        # QSpacerItems that held them. Clear PySide6's registrations for those
        # items first, while they are still alive -- otherwise they are left
        # behind pointing at freed memory and kill an unrelated QObject
        # construction later (see _releaseLayoutItemWrappers).
        _releaseLayoutItemWrappers(grid)

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
        identityRow.addWidget(self._w(schema.PLAYER_REAL_NAME_KEY, QLineEdit))
        identityRow.addWidget(self._w(schema.PLAYER_TWITTER_KEY, QLineEdit))

        locationRow = QHBoxLayout()
        locationRow.setSpacing(UI.SPACE_SM)
        dlg.addLayout(locationRow)
        locationRow.addWidget(self._w(schema.PLAYER_COUNTRY_KEY, QComboBox))
        locationRow.addWidget(self._w(schema.PLAYER_STATE_KEY, QComboBox))

        dlg.addWidget(self.character_container)
        dlg.addWidget(self.custom_textbox)

        buttonRow = QHBoxLayout()
        buttonRow.setSpacing(UI.SPACE_SM)
        dlg.addLayout(buttonRow)
        buttonRow.addWidget(self.clear_bt)
        buttonRow.addStretch(1)
        # 反映: commits the owning board's held subtree — the same publish as
        # the board-level 反映 button, so detail edits can be applied without
        # closing the dialog. Always enabled: a commit with nothing pending
        # is a no-op.
        self.btApplyDetails = QPushButton(
            QApplication.translate("app", "反映"))
        self.btApplyDetails.setObjectName("primary")
        self.btApplyDetails.setToolTip(QApplication.translate(
            "app", "入力内容を配信画面に反映する"))
        self.btApplyDetails.clicked.connect(self._ApplyBoardChanges)
        buttonRow.addWidget(self.btApplyDetails)
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
        team = self._w(schema.PLAYER_SPONSOR_KEY, QLineEdit)
        name = self._w(schema.PLAYER_TAG_KEY, QLineEdit)
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
        """Take an emptied row layout out of `grid`, destroying it via PySide.

        This used to be `grid.takeAt(i)` followed by
        `sub_layout.deleteLater()`, and that combination corrupted the
        process. What matters is *who* frees the layout.

        takeAt() and removeItem() both hand C++ ownership of the removed item
        to Python. deleteLater() then hands it straight back to Qt, so Qt is
        the one that frees it -- behind Shiboken's back. Destroying a QLayout
        destroys the QLayoutItems it holds, and QLayoutItem / QWidgetItem /
        QSpacerItem are not QObjects, so PySide6 has no way to notice they
        are gone. Wrappers for those items already exist by this point:
        __init__ walks `grid.itemAt(i)` to zero the row margins and the seed
        row's `itemAt(i)` / `spacerItem()` to collapse its spacers, and
        itemAt() registers what it returns as a Shiboken child of the layout,
        so the wrappers live as long as the layout's own wrapper does. They
        were left in Shiboken's binding manager, still flagged valid,
        pointing at freed memory.

        A stale registration there is a landmine somewhere else entirely. The
        next C++ object allocated at that address gets looked up through it --
        Qt calls the virtual metaObject() from inside a QObject constructor,
        before Shiboken has registered the new object -- and PySide reads a
        QMetaObject out of the dead wrapper's type:
        PySide::SignalManager::retrieveMetaObject dereferences a null
        TypeUserData and the process dies with an access violation in
        whatever widget happened to be under construction. In this codebase
        that landed, run after run, on the QCompleter built by
        AutonaBasePlayerWidget.LoadCountries (now _WireCountryState) one or
        two player widgets later.

        So the layout is never handed back to Qt: removeItem() leaves Python
        the owner, and dropping the last Python reference makes PySide do the
        delete -- which invalidates the layout's wrapper and the item wrappers
        registered under it. Do not add a deleteLater() (or a setParent(),
        which shuffles ownership again) back into this function.

        The presence check is indexOf(), not a grid.itemAt(i) scan, for the
        same reason: the scan wrapped every QWidgetItem in the grid on its way
        past, and the caller re-homes those widgets a few lines later, which
        frees the items behind PySide's back.
        """
        if sub_layout is None or grid.indexOf(sub_layout) < 0:
            return
        grid.removeItem(sub_layout)

    def ShowDetails(self):
        name = self._w(schema.PLAYER_TAG_KEY, QLineEdit).text().strip()
        self.detailsDialog.setWindowTitle(
            name if name else QApplication.translate("app", "プレイヤー詳細"))
        # Window-modal, non-blocking (no exec(): モーダル入れ子禁止).
        self.detailsDialog.open()

    def _ApplyBoardChanges(self):
        # The details dialog is only built for score paths, so the slot
        # always resolves to a board here; ApplyChanges is the same
        # operation the board-level 反映 button runs.
        board = self.player.BoardNumber()
        if board is None:
            logger.warning(
                f"反映 pressed on a player card with no board: {self.path}")
            return
        AutonaActions.instance.ApplyChanges(board)

    @staticmethod
    def _seedDisplayStyle() -> str:
        return f"color: {UI.palette()['text_secondary']};"

    @staticmethod
    def _SeedNumber(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _ShowSeed(self, value):
        """The seedDisplay badge for seed `value` (0 / none: hidden)."""
        seed = self._SeedNumber(value)
        if seed > 0:
            self.seedDisplay.setText(f"Seed: {seed}")
            self.seedDisplay.setVisible(True)
        else:
            # Empty seed: hide the trailing slot so it stops reserving
            # width and the GamerTag field reaches the column's right
            # edge, staying flush with the rows below.
            self.seedDisplay.setText("")
            self.seedDisplay.setVisible(False)

    def _WireInputs(self, seed: QSpinBox):
        """Bind the card's inputs to the operations they stand for.

        This used to be three loops over `findChildren`, each connecting a
        lambda that built `{self.path}.{element.objectName()}` and wrote it
        (known-smells S-14, and the last instance of the pattern outside
        AutonaTournamentInfoWidget). The names come from
        `StateSchema.PLAYER_INPUT_KEYS` now and are used to *find* the
        widgets; the writes are `PlayerService`'s. The objectNames still have
        to equal those keys -- an existing program_state.json is keyed by
        them and the eye menu finds these same widgets by them -- which is
        what tests/test_objectname_keypath_contract.py pins.

        Two things the old loops did that this deliberately does not:

        - `findChildren(QLineEdit)` also reached the *internal* line edits of
          the editable country/state combos, whose objectName is "". Finishing
          an edit in either of them wrote a key with an empty final segment
          under the player (`score.1.team.1.player.1.` -> a "" key), which
          nothing reads and every export carried.
        - the seed spinbox's internal line edit went through the same loop
          via the `qt_spinbox_lineedit` -> parent fallback and published the
          spinbox's *text*, so a Clear() left the seed as the string "0". It
          is wired explicitly below and publishes the value, because it is
          not redundant: a slot written in the state (a swap, ADR-005 S2c)
          can carry a seed the spinbox does not show yet, so the two can
          disagree and Clear() has to be able to reset the state from a
          spinbox that is already at 0.

        The initial emits are kept exactly where they were: the seed, the
        birthday flag and the two location combos publish as the card is
        built, the four line edits do not (they reach the state on the first
        editingFinished, or through SetData). An empty card therefore starts
        with `seed`, `birthday`, `country` and `state` and gains the rest as
        it is filled -- unchanged, and relied on by the overlays that read
        the location from launch.
        """
        for field in schema.PLAYER_LINE_EDIT_KEYS:
            edit = self._w(field, QLineEdit)
            if edit is None:
                logger.error(
                    f"AutonaScoreboardPlayer.ui is missing {field!r}; "
                    f"{self.path}.{field} will never be written")
                continue
            edit.editingFinished.connect(
                lambda field=field, edit=edit: self._OnTextChanged(
                    field, edit.text()))

        # A changed seed recalculates the upset factor (announce) -- not the
        # publish of the card being built just below, which nothing heard
        # when it was a signal the board connected afterwards.
        self._seedAnnounces = False
        seed.valueChanged.connect(
            lambda value: self.player.SetSeed(
                value, announce=self._seedAnnounces))
        seedEdit = seed.lineEdit()
        if seedEdit is not None:
            seedEdit.editingFinished.connect(
                lambda seed=seed: self.player.SetSeed(seed.value()))

        seed.setValue(0)
        seed.valueChanged.emit(seed.value())
        self._seedAnnounces = True

        birthday = self._w(schema.PLAYER_BIRTHDAY_KEY, QCheckBox)
        birthday.toggled.connect(self.player.SetBirthday)
        birthday.toggled.emit(False)

        for field in schema.PLAYER_LOCATION_KEYS:
            combo = self._w(field, QComboBox)
            if combo is None:
                logger.error(
                    f"AutonaScoreboardPlayer.ui is missing {field!r}; "
                    f"{self.path}.{field} will never be written")
                continue
            combo.currentIndexChanged.connect(
                lambda index, field=field, combo=combo:
                    self._OnLocationChanged(field, combo))
            combo.currentIndexChanged.emit(0)

    def _StateValue(self, field):
        return StateManager.Get(self.player.Key(field))

    def _OnTextChanged(self, field, text):
        """A line edit finished an edit: publish its text, unless that text
        is not news (ADR-005 S2a).

        Compared here rather than in PlayerService, which writes whatever it
        is handed (its module docstring). Two finishes carry nothing:

        - the text the state already holds. Qt 6 fires editingFinished on a
          focus-out whenever the text changed since its last emission, and a
          render's setText -- signals blocked or not -- counts as a change,
          so tabbing through a field the card was rendered into would
          otherwise re-write it.
        - the text a render had to leave in place because the operator was
          in the field, if it is still what the field last showed
          (HeldBackText), however it is finished: a focus-out, Enter, or
          typing that ends where it started. The state moved on underneath
          it; writing it back would put the old value over the new one. The
          render the focus-out asks for (_OnFocusChanged) then shows the new
          value. Any other text is the operator's edit and is published --
          what they typed before a render came is theirs too (ADR-005
          S3e-2; it used to be held back, and was lost).

        An absent key is not "": a field the slot has never had is still
        written on its first finish, as before.
        """
        if self._heldBack.IsEdit(field, text, self._StateValue(field) == text):
            self.player.SetText(field, text)

    def _OnNoteChanged(self):
        # textChanged, i.e. every keystroke (a render's setPlainText is
        # blocked), under _OnTextChanged's rule: the state already holding
        # the text is not news, and neither is the text a render left in
        # the note while the operator was in it (HeldBackText) -- a change
        # that ends on it (a selected character typed over with itself)
        # used to put the old note back over a load. Typing on is theirs
        # (ADR-005 S3e-3 review).
        text = self.custom_textbox.toPlainText()
        field = schema.PLAYER_NOTE_KEY
        if self._heldBack.IsEdit(field, text, self._StateValue(field) == text):
            self.player.SetText(field, text)

    def _OnLocationChanged(self, field, combo: QComboBox):
        # Which row is shown has just been decided here (LoadStates / the
        # operator); only its code leaves the widget, and PlayerService
        # builds the published value from the country catalog (ADR-005
        # S1d). No code -- the blank row, a typed name that matches no row,
        # the custom-flag separator -- publishes "no location".
        data = combo.currentData()
        code = data.get("code") if isinstance(data, dict) else None
        if field == schema.PLAYER_COUNTRY_KEY:
            self.player.SetCountry(code)
        else:
            # The region index points into the rows LoadStates (or a render)
            # built, so the code is resolved against the country they were
            # built for, which is what the index means. The one region
            # publish LoadStates makes itself, before it records the code,
            # is the blank row, which needs no country.
            self.player.SetRegion(
                getattr(self, "_loadedStatesCountryCode", None), code)

    def NameChanged(self):
        # The operator finished the sponsor or the tag. A load no longer
        # comes through here (SetData is core's since ADR-005 S3e-3 and
        # sets no field), so there is no load to wait for.
        team = self.findChild(
            QLineEdit, schema.PLAYER_SPONSOR_KEY).text()
        name = self.findChild(QLineEdit, schema.PLAYER_TAG_KEY).text()
        merged = team + " " + name

        if merged != self.lastExportedName:
            self.ExportMergedName()
            self.ExportPlayerImages()
            self.ExportPlayerId()

        self.lastExportedName = merged

    def ExportPlayerId(self, id=None):
        # The service asks for the statistics when the slot actually
        # changed hands (announce), not for a re-asserted id.
        self.player.SetPlayerId(id, announce=True)

    # ── render: the slot, from the state (ADR-005 S2a) ───────────────────
    #
    # The other direction from everything above. ADR-005 S2 makes the state
    # the source of truth for a player slot -- the swap and clear operations
    # become core operations that never touch a widget -- so the card has to
    # be able to show a slot it did not write. RenderFromState is that, and
    # it is held to one rule: it WRITES NOTHING. Every widget it changes is
    # changed with its signals blocked, because each of them is still
    # connected to the PlayerService write it stands for (an operator's
    # edit). tests/test_player_card_render.py pins both halves: the card
    # shows its slot after every board operation, and rendering makes no
    # state write at all.
    #
    # The board calls it (AutonaScoreboardWidget._RenderPlayerCards), once
    # control is back in the event loop after a held save cycle -- never in
    # the middle of an operation, when the card can legitimately be ahead of
    # the state (a country pick publishes the region before the country).
    #
    # Two more rules, both for the operator's sake:
    #
    # - a field being edited is left alone (a notification must not yank
    #   text out from under the caret -- the rule _SyncSetFields follows).
    #   Once focus has left every such field the card asks for another pass
    #   (_OnFocusChanged), and a focus-out that merely finishes the text the
    #   render left behind writes nothing (_OnTextChanged).
    # - what to show is decided before a widget is touched. A value no row
    #   of a combo reproduces changes nothing at all, so a card is never
    #   left half-way between its old slot and the new one; the next pass
    #   tries again.

    # The leaves this card displays. The rest of the slot is derived
    # (mergedName / mergedOnlyName, avatar, sponsor crests, mains) or has no
    # input on the card (id, city, online_avatar).
    RENDERED_KEYS = schema.PLAYER_INPUT_KEYS + ("character",)

    def RenderFromState(self) -> bool:
        """Show what the state holds for this card's slot. Writes nothing.

        True when the card now shows the whole slot. False when something
        was left alone -- a field the operator is editing, or a value no row
        of a combo reproduces -- and the next pass tries again.

        A slot that has not changed since the last complete render is not
        looked at again: the card can only have moved since by an edit,
        and every edit publishes.
        """
        if self._deleted or self.player.BoardNumber() is None:
            # Board cards only. The PLAYERS panel's editor (player_editor.1)
            # is a draft the operator types into, and only the editor itself
            # writes it -- its loads (SetData) and its clear show what they
            # wrote themselves (_RenderSlot).
            return True
        return self._RenderSlot()

    def _RenderSlot(self, over=False) -> bool:
        """RenderFromState for any card, the editor included. `over`: show
        the slot in the fields the operator is in too -- for an operation
        the operator ran on this card (a name picked from the completer
        while still in the tag field, the clear button), whose result is
        what they asked to see."""
        if self._deleted:
            return True
        slot = self._ReadRenderedSlot()
        if slot == self._renderedSlot:
            return True

        self._focusSkipped = []
        sponsor = self._w(schema.PLAYER_SPONSOR_KEY, QLineEdit)
        tag = self._w(schema.PLAYER_TAG_KEY, QLineEdit)
        shownName = (sponsor.text(), tag.text())
        self._renderingOver = over
        try:
            results = [self._RenderText(field, slot.get(field))
                       for field in schema.PLAYER_TEXT_KEYS]
            results.append(
                self._RenderSeed(slot.get(schema.PLAYER_SEED_KEY)))
            results.append(
                self._RenderBirthday(slot.get(schema.PLAYER_BIRTHDAY_KEY)))
            results.append(self._RenderLocation(
                slot.get(schema.PLAYER_COUNTRY_KEY),
                slot.get(schema.PLAYER_STATE_KEY)))
            results.append(self._RenderCharacters(slot.get("character")))
        finally:
            self._renderingOver = False
        if ((sponsor.text(), tag.text()) != shownName
                or sponsor in self._focusSkipped
                or tag in self._focusSkipped):
            # The name the slot already has a display name for:
            # NameChanged compares against this to tell a new player in
            # the slot (it then re-derives the images and drops the id)
            # from a focus-out that changed nothing -- and a name field
            # left with held-back text in it from one that must publish
            # nothing either.
            self.lastExportedName = (
                self._ExportedNameText(
                    schema.PLAYER_SPONSOR_KEY, sponsor, slot)
                + " "
                + self._ExportedNameText(schema.PLAYER_TAG_KEY, tag, slot))

        complete = all(results)
        self._renderedSlot = slot if complete else None
        return complete

    def _ExportedNameText(self, field, edit, slot) -> str:
        """One half of the name the slot has a display name for, after a
        render. A field the render left alone counts with the text it held
        back there -- finishing that publishes nothing -- and otherwise with
        the slot's value: what the operator typed before the render came is
        a new name, published (with its images, the id dropped) when they
        finish it (ADR-005 S3e-2). A rendered field shows the slot's value."""
        text = edit.text()
        if self._heldBack.IsHeldBack(field, text):
            return text
        value = slot.get(field)
        return "" if value is None else str(value)

    def _ReadRenderedSlot(self) -> dict:
        """A copy of the rendered leaves of this slot, read in one go."""
        with StateManager.lock:
            slot = StateManager.Get(self.path)
            if not isinstance(slot, dict):
                return {}
            return {key: deep_clone(slot[key])
                    for key in self.RENDERED_KEYS if key in slot}

    @staticmethod
    def _Editing(widget) -> bool:
        """The operator is in `widget`: it has focus, or -- a combo -- its
        line edit has, or its drop-down is open."""
        if widget.hasFocus():
            return True
        if not isinstance(widget, QComboBox):
            return False
        edit = widget.lineEdit()
        if edit is not None and edit.hasFocus():
            return True
        view = widget.view()
        return view is not None and view.isVisible()

    def _LeftAlone(self, widget) -> bool:
        """The render in progress leaves `widget` alone: the operator is in
        it, and the render is not one over their fields (_RenderSlot)."""
        return not self._renderingOver and self._Editing(widget)

    def _LeaveForFocus(self, *widgets):
        """Note widgets a render left alone because they were being edited."""
        for widget in widgets:
            if widget not in self._focusSkipped:
                self._focusSkipped.append(widget)

    def _OnFocusChanged(self, old, new):
        """QApplication.focusChanged: once the operator has left every field
        the last render had to skip, ask the board for the pass that shows
        them. The pass is the board's to schedule (deferred and coalesced),
        so nothing is redrawn inside the focus change (agent-pitfalls #9)."""
        if self._deleted or not self._focusSkipped:
            return
        skipped = [w for w in self._focusSkipped if Shiboken.isValid(w)]
        if any(self._Editing(w) for w in skipped):
            self._focusSkipped = skipped
            return
        self._focusSkipped = []
        self.instanceSignals.render_requested.emit()

    def _RenderText(self, field, value) -> bool:
        # A field the card has never published is absent, and shows empty.
        text = "" if value is None else str(value)
        note = field == schema.PLAYER_NOTE_KEY
        widget = self.custom_textbox if note else self._w(field, QLineEdit)
        if widget is None:
            return True
        shown = widget.toPlainText() if note else widget.text()
        if shown == text:
            self._heldBack.Shown(field, text)
            return True
        if self._LeftAlone(widget):
            self._LeaveForFocus(widget)
            self._heldBack.LeftAlone(field, shown, text)
            return False
        with QSignalBlocker(widget):
            if note:
                widget.setPlainText(text)
            else:
                widget.setText(text)
        self._heldBack.Shown(field, text)
        return True

    def _RenderSeed(self, value) -> bool:
        seed = self._SeedNumber(value)
        spin = self._w(schema.PLAYER_SEED_KEY, QSpinBox)
        if spin is not None and spin.value() != seed:
            with QSignalBlocker(spin):
                spin.setValue(seed)
        # The badge from the state's number, not the spinbox's: the spinbox
        # clamps, and its valueChanged (which normally drives the badge) is
        # what was just blocked.
        if self.seedDisplay.text() != (f"Seed: {seed}" if seed > 0 else ""):
            self._ShowSeed(seed)
        return True

    def _RenderBirthday(self, value) -> bool:
        check = self._w(schema.PLAYER_BIRTHDAY_KEY, QCheckBox)
        if check is not None and check.isChecked() != bool(value):
            with QSignalBlocker(check):
                check.setChecked(bool(value))
        return True

    @staticmethod
    def _ComboCode(combo):
        data = combo.currentData()
        return data.get("code") if isinstance(data, dict) else None

    @staticmethod
    def _RowWithCode(combo, code):
        """The first row whose data carries `code` -- for no code, the blank
        row (data without a code: {}). None when the combo has no such row.
        Rows without data (the custom-flag separator, a typed name) are
        never picked."""
        model = combo.model()
        for row in range(model.rowCount() if model is not None else 0):
            data = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("code") == code:
                return row
        return None

    def _RenderLocation(self, countryValue, regionValue) -> bool:
        """The country and region, by code. Both values are what
        PlayerService.CountryValue / RegionValue built from a code, so the
        code is the whole of what has to be shown."""
        countryCode = countryValue.get("code") \
            if isinstance(countryValue, dict) else None
        regionCode = regionValue.get("code") \
            if isinstance(regionValue, dict) else None
        country = self._w(schema.PLAYER_COUNTRY_KEY, QComboBox)
        region = self._w(schema.PLAYER_STATE_KEY, QComboBox)
        rowsFor = getattr(self, "_loadedStatesCountryCode", None)
        if (self._ComboCode(country) == countryCode and rowsFor == countryCode
                and self._ComboCode(region) == regionCode):
            return True
        editing = [combo for combo in (country, region)
                   if self._LeftAlone(combo)]
        if editing:
            self._LeaveForFocus(*editing)
            return False

        # Both rows are settled before either combo changes.
        countryRow = None
        if self._ComboCode(country) != countryCode:
            countryRow = self._RowWithCode(country, countryCode)
            if countryRow is None:
                logger.debug(f"{self.path}: no country row for {countryCode!r}")
                return False
        refill = rowsFor != countryCode
        if refill:
            # The rows a refill builds: the blank one, and the catalog's
            # regions of that country (_FillStateRows).
            reproducible = regionCode is None or CountryCatalog.instance \
                .State(countryCode, regionCode).get("code") == regionCode
        else:
            reproducible = self._ComboCode(region) == regionCode \
                or self._RowWithCode(region, regionCode) is not None
        if not reproducible:
            logger.debug(f"{self.path}: no region row for "
                         f"{countryCode!r}/{regionCode!r}")
            return False

        if countryRow is not None:
            with QSignalBlocker(country):
                country.setCurrentIndex(countryRow)
        if refill:
            # The region rows belong to the country shown. A pick rebuilds
            # them through LoadStates, which also publishes the blank region;
            # this is the same refill without that publish.
            self._FillStateRows(countryCode)
            self._loadedStatesCountryCode = countryCode
        if self._ComboCode(region) != regionCode:
            row = self._RowWithCode(region, regionCode)
            if row is None:
                # The catalog said the refill would have it.
                logger.warning(f"{self.path}: region {regionCode!r} of "
                               f"{countryCode!r} is not among the rows built")
                return False
            with QSignalBlocker(region):
                region.setCurrentIndex(row)
        return True

    def _ShownCharacter(self, character, color, variant) -> dict:
        """What the slot holds for what these three combos show -- the value
        a pick would publish (PlayerService builds it from the identifiers,
        ADR-005 S1d)."""
        return self.player.CharacterValue(
            self._CharacterSlot(character, color, variant))

    def _RenderCharacters(self, characters) -> bool:
        # The number of slots is still the board's (charNumber), not the
        # state's: a slot the card has no combos for is not shown, and a
        # combo row the state has no slot for (an emptied player) shows the
        # blank slot -- what an empty row publishes.
        if not isinstance(characters, dict):
            characters = {}
        blank = None
        complete = True
        for index, element in enumerate(self.character_elements):
            value = characters.get(index + 1, characters.get(str(index + 1)))
            if not isinstance(value, dict):
                if blank is None:
                    blank = self.player.CharacterValue(CharacterSlot())
                value = blank
            complete = self._RenderCharacterSlot(element, value) and complete
        return complete

    def _RenderCharacterSlot(self, element, value) -> bool:
        _element, character, color, variant = element
        if self._ShownCharacter(character, color, variant) == value:
            return True
        editing = [combo for combo in (character, color, variant)
                   if self._LeftAlone(combo)]
        if editing:
            self._LeaveForFocus(*editing)
            return False

        plan = self._CharacterPlan(character, color, variant, value)
        if plan is None:
            logger.debug(f"{self.path}: no combo rows reproduce {value!r}")
            return False
        row, skin, variantRow = plan
        with QSignalBlocker(character), QSignalBlocker(color), \
                QSignalBlocker(variant):
            before = character.currentIndex()
            if row is None:
                # Not a row of the loaded game: the name as typed.
                character.setCurrentIndex(-1)
                character.setEditText(value.get("name") or "")
            elif before != row:
                character.setCurrentIndex(row)
            if character.currentIndex() != before:
                # The skin rows are the character's; the pick handler
                # rebinds them the same way (unblocked, so it publishes).
                self.LoadSkinOptions(character, color)
            color.setCurrentIndex(skin)
            variant.setCurrentIndex(variantRow)

        if self._ShownCharacter(character, color, variant) == value:
            return True
        # _CharacterPlan said these rows show the value and they do not:
        # the plan and the combos disagree, which is a bug here.
        logger.warning(f"{self.path}: rendering {value!r} did not "
                       f"reproduce it")
        return False

    def _CharacterPlan(self, character, color, variant, value):
        """The rows that make one slot's three combos show `value`, as
        (character row -- None for a typed name --, skin row, variant row),
        or None when no rows do.

        Worked out from the models alone, before anything changes, and
        checked with the value builder a pick publishes through: the
        identifiers these rows would hand `_CharacterSlot` must build
        `value` itself.
        """
        variantRow = self._VariantRowFor(variant, value.get("variant"))
        if variantRow is None:
            return None
        skin = value.get("skin")
        if not isinstance(skin, int) or isinstance(skin, bool):
            skin = -1

        row = self._CharacterRowFor(character, value)
        if row is None:
            data, text, index = None, value.get("name") or "", -1
        else:
            item = character.model().index(row, 0)
            data = item.data(Qt.ItemDataRole.UserRole)
            text = item.data(Qt.ItemDataRole.DisplayRole) or ""
            index = row
        picked = data if isinstance(data, dict) else None
        if picked is not None and picked.get("name"):
            text = picked.get("name")

        # The skin rows the color combo will offer: its own if the
        # character stays, else the ones LoadSkinOptions binds.
        skinRows = color.count() if index == character.currentIndex() \
            else self._SkinRowCount(data)
        if skin != -1 and not 0 <= skin < skinRows:
            return None

        variantData = variant.model().index(variantRow, 0).data(
            Qt.ItemDataRole.UserRole) if variantRow >= 0 else None
        slot = CharacterSlot(
            character=picked.get("en_name") if picked is not None else None,
            skin=skin,
            variant=(variantData.get("en_name")
                     if isinstance(variantData, dict) else None),
            text=text)
        if self.player.CharacterValue(slot) != value:
            return None
        return row, skin, variantRow

    @staticmethod
    def _SkinRowCount(characterData) -> int:
        """How many rows LoadSkinOptions gives the skin combo for a
        character row's data."""
        if not characterData:
            return 0
        model = AutonaGameAssetManager.instance.skinModels.get(
            characterData.get("en_name"))
        return model.rowCount() if model is not None else 0

    def _CharacterRowFor(self, combo, value):
        """The character combo's row for a slot value, None for a name that
        is not a row (typed, or the blank row of an empty roster).

        Matched through the value itself rather than its `en_name`: a skin
        that is a different character (Dr. Mario) puts that character's
        name and en_name into the slot over the picked row's. `codename` is
        the row's own, so the rows sharing it (or, without one, the en_name)
        are the candidates, and the one whose value is the slot's wins.
        """
        model = combo.model()
        rows = model.rowCount() if model is not None else 0
        if "en_name" not in value:
            if value.get("name"):
                return None
            for row in range(rows):
                if not isinstance(
                        model.index(row, 0).data(Qt.ItemDataRole.UserRole),
                        dict):
                    return row      # the blank first row
            return None

        variantValue = value.get("variant")
        variantName = variantValue.get("en_name") \
            if isinstance(variantValue, dict) else None
        skin = value.get("skin")
        candidates = []
        for row in range(rows):
            data = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict):
                continue
            if ("codename" in value
                    and data.get("codename") == value.get("codename")) \
                    or data.get("en_name") == value.get("en_name"):
                candidates.append((row, data))
        for row, data in candidates:
            slot = CharacterSlot(
                character=data.get("en_name"),
                skin=skin if isinstance(skin, int) else -1,
                variant=variantName, text=data.get("name") or "")
            if self.player.CharacterValue(slot) == value:
                return row
        return candidates[0][0] if candidates else None

    @staticmethod
    def _VariantRowFor(combo, value):
        """The variant combo's row for a slot's `variant` ({} = none: the
        blank first row, -1 without one). None when no row has it."""
        model = combo.model()
        rows = model.rowCount() if model is not None else 0
        name = value.get("en_name") if isinstance(value, dict) else None
        if not name:
            return 0 if rows else -1
        for row in range(rows):
            data = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("en_name") == name:
                return row
        return None

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
            # Combo prefixes ("ファイター" / "カラー") only on the scoreboard,
            # where the row lives in the roomy details dialog — the inline
            # rows elsewhere (commentary etc.) have no width to spare.
            if self.path.startswith("score."):
                character_element.layout().addWidget(
                    QLabel(QApplication.translate("app", "ファイター")))
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

            if self.path.startswith("score."):
                character_element.layout().addWidget(
                    QLabel(QApplication.translate("app", "カラー")))
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
            name_edit = self._w(schema.PLAYER_TAG_KEY, QLineEdit)
            name_edit.setCompleter(QCompleter())
            # Queued: the completer finishes putting its own text into the
            # field before the pick is loaded over it. A bound method: Qt
            # drops the connection with the card.
            name_edit.completer().activated[QModelIndex].connect(
                self._OnCompleterPick, Qt.QueuedConnection)
            name_edit.completer().setCaseSensitivity(
                Qt.CaseSensitivity.CaseInsensitive)
            name_edit.completer().setFilterMode(Qt.MatchFlag.MatchContains)
            # Entrants of the loaded tournament, or the whole DB when no
            # tournament is loaded.
            name_edit.completer().setModel(AutonaPlayerDB.GetCompleterModel())
            L.attach_popup_on_focus(name_edit)

            self.ManageSavePlayerToDBText()
            self.ManageDeletePlayerFromDBActive()

    def _OnCompleterPick(self, index):
        """A player picked from the name completer: loaded into this slot
        (SetData), the tag field the operator is still in included."""
        data = index.data(Qt.ItemDataRole.UserRole) \
            if index is not None else None
        if isinstance(data, dict):
            self.SetData(data)

    def SetData(self, data, dontLoadFromDB=False, clear=True, no_mains=False):
        """Load a player into this card's slot, and show it.

        The load is core's since ADR-005 S3e-3 (PlayerService.ApplyData):
        it used to be this method filling the card in like a form -- the
        line edits' text set and their editingFinished emitted, rows picked
        on the country / region / character / skin / variant combos by
        scanning their models -- and the handlers publishing what it had
        filled in. Now the slot is written from the data alone, and the
        card renders what was written (over the field the operator is in:
        this is the load they asked for, from the name completer or the
        PLAYERS panel). The board's own loads -- a set's entrants, the
        Web's player edit, a tag lookup -- call the core without the card
        and the card follows from the held cycle like any other write.
        """
        logger.debug(f"Setting data for {self.path}: {data}")
        try:
            # The load asks for its statistics itself (ApplyData), also
            # when it raised half-way, for what it did write.
            self.player.ApplyData(data, not dontLoadFromDB, clear, no_mains)
        finally:
            self._ShowOwnOperation()

    def Clear(self, no_mains=False):
        """The card's clear button: empty the slot (PlayerService.ClearSlot,
        as the board's clear does; ADR-005 S3e-3) and show it."""
        try:
            self.player.ClearSlot(no_mains)
        finally:
            self._ShowOwnOperation()

    def _ShowOwnOperation(self):
        """Show what an operation the operator ran on this card wrote: the
        slot, over the fields they are in (_RenderSlot), with no name typed
        into a combo left over it (DiscardTypedText)."""
        self.DiscardTypedText()
        self._renderedSlot = None
        self._RenderSlot(over=True)
        # The name the slot now has a display name for, whether or not the
        # render had to change the fields: a name finished afterwards is
        # compared against it (NameChanged), and a stale one would re-derive
        # the images and drop the id the load just wrote.
        self.lastExportedName = (
            self._w(schema.PLAYER_SPONSOR_KEY, QLineEdit).text() + " "
            + self._w(schema.PLAYER_TAG_KEY, QLineEdit).text())
        if not self.path.startswith("score."):
            # The editor's buttons follow its tag, which used to be
            # announced by the tag's editingFinished.
            self.ManageSavePlayerToDBText()
            self.ManageDeletePlayerFromDBActive()

    def DiscardTypedText(self, notEditing=False):
        """Put back, on every editable combo of the card, the text of the
        row it shows over a name typed into it and never picked.

        Typing into a combo publishes nothing until a row is picked, and a
        render only moves a combo whose row is wrong -- so after an
        operation that empties the slot, text typed into a combo that
        already showed the right row (the blank one, typically) used to
        stay on the card over the emptied slot (ADR-005 S2d). Writes
        nothing: the text is the combo's own, put back with its signals
        blocked. A slot's typed character (no row, index -1) is its value,
        and is left to the render.

        `notEditing`: leave the combo the operator is in alone -- for a
        load that came from elsewhere (the board's, ADR-005 S3e-3 review),
        which does not take what they are typing, as a render does not.
        """
        combos = [self._w(field, QComboBox)
                  for field in schema.PLAYER_LOCATION_KEYS]
        for _element, character, color, variant in self.character_elements:
            combos += [character, color, variant]
        for combo in combos:
            if combo is None or not combo.isEditable():
                continue
            if notEditing and self._Editing(combo):
                continue
            index = combo.currentIndex()
            if index < 0:
                continue
            text = combo.itemText(index)
            if combo.currentText() != text:
                with QSignalBlocker(combo):
                    combo.setEditText(text)

    def SavePlayerToDB(self):
        """The editor's save button (and a Web player edit's
        savePlayerToDb): the slot as the database stores it,
        PlayerService.SavePlayerToDB (ADR-005 S3e-3) -- read from the state,
        where it used to be read off the card's fields and combos."""
        self.player.SavePlayerToDB()

    def ManageSavePlayerToDBText(self):
        tag = self.GetCurrentPlayerTag()

        if PlayerDB.instance.Contains(tag):
            self.save_bt.setText(
                QApplication.translate("app", "Update player"))
        else:
            self.save_bt.setText(
                QApplication.translate("app", "Save new player"))

    def ManageDeletePlayerFromDBActive(self):
        tag = self.GetCurrentPlayerTag()

        if PlayerDB.instance.Contains(tag):
            self.delete_bt.setEnabled(True)
        else:
            self.delete_bt.setEnabled(False)

    def DeletePlayerFromDB(self):
        tag = self.GetCurrentPlayerTag()
        PlayerDB.instance.DeletePlayer(tag)

