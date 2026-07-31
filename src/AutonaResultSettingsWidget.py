import urllib.parse
import webbrowser

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
from pathlib import Path
from loguru import logger

from .result import main_generate_result as result_gen
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaPlayerDB import AutonaPlayerDB
from .Helpers.AutonaDirHelper import AutonaResolve
from .AutonaFeatureColors import FEATURE_RESULT, MakeFeatureColorCard
from . import ui_constants as UI
from . import ui_layout as L


class NoIconProxyModel(QSortFilterProxyModel):
    """Proxy model that strips icons (DecorationRole) from the source model."""
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole:
            return None
        return super().data(index, role)


class AutonaResultSettingsWidgetSignals(QObject):
    StandingsLoaded = Signal(object)


class ResultPreviewWidget(QWidget):
    """Preview widget that scales pixmap while keeping aspect ratio.
    Matches the PreviewWidget in AutonaThumbnailSettingsWidget.
    """

    def __init__(self):
        super().__init__()
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)
        self._pixmap = None
        # Re-entrancy guard: setFixedHeight() below re-enters resizeEvent
        # synchronously. With buttons rendered under the preview, the scroll
        # area's scrollbar can toggle and shift our width on each pass, so the
        # aspect-ratio recompute never converges and recurses into a stack
        # overflow. Skip the nested recompute and let the next top-level
        # resizeEvent settle the height.
        self._updatingHeight = False

    def setPixmap(self, pixmap):
        self._pixmap = pixmap
        self._updateMaxHeight()
        self.repaint()

    def resizeEvent(self, event):
        self._updateMaxHeight()
        self.repaint()

    def _updateMaxHeight(self):
        if self._updatingHeight:
            return
        if self._pixmap and self._pixmap.width() > 0 and self.width() > 0:
            desired = int(
                self.width() * self._pixmap.height() / self._pixmap.width())
            # Fix height to aspect ratio so the wrapping scroll area scrolls
            # when the viewport is shorter than the preview.
            if self.minimumHeight() != desired or self.maximumHeight() != desired:
                self._updatingHeight = True
                try:
                    self.setFixedHeight(desired)
                finally:
                    self._updatingHeight = False

    def paintEvent(self, event):
        if not self._pixmap:
            return
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return
        painter = QPainter(self)
        target_w = self.width()
        target_h = int(target_w * ph / pw)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(
            QRectF(0, 0, target_w, target_h),
            self._pixmap,
            QRectF(0, 0, pw, ph))
        painter.end()


class AutonaResultSettingsWidget(QDockWidget):
    # Team layout: max members shown per rank (each = name + main fighter).
    MAX_TEAM_MEMBERS = 5

    def __init__(self, *args):
        super().__init__(*args)

        self.setWindowTitle(
            QApplication.translate("app", "Result Settings"))
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget_container = QWidget()
        self.setWidget(self.widget_container)
        self.widget_container.setLayout(QVBoxLayout())
        self.widget_container.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)

        self.settings = uic.loadUi(
            AutonaResolve("src/layout/AutonaResultSettings.ui"))
        # The .ui Form's own root layout still carries Qt's default margin;
        # widget_container already provides PANEL_MARGIN, so zero this one
        # out to avoid stacking both.
        self.settings.layout().setContentsMargins(0, 0, 0, 0)
        self.widget_container.layout().addWidget(self.settings)

        # --- Player cards (one draggable card per player) ---
        # Each player is a QFrame card (brighter background) laid out in a
        # L.DragCardList that supports drag-to-reorder. Card body:
        #   left column : drag grip + rank spin box
        #   fields grid : sponsor/name, X (Twitter), then 3× char+color rows
        # playerInputs entry (unchanged 10-tuple, index contract preserved):
        #   (rank_edit, name_edit, sponsor_edit,
        #    char1_combo, color1_combo,
        #    char2_combo, color2_combo,
        #    char3_combo, color3_combo,
        #    twitter_edit)
        # playerInputs / playerCards are kept in VISUAL order and stay parallel;
        # _onPlayersReordered rebuilds both from the card list after a drag.
        self.playerInputs = []
        self.playerCards = []
        self._entryByCard = {}

        self.playerCardList = L.DragCardList(
            mime="application/x-autona-result-player-card")
        self.playerCardList.orderChanged.connect(self._onPlayersReordered)
        # One step brighter than the page so each player reads as its own card.
        _pc = UI.palette()
        # `dragging` variant is one step brighter (bg_quaternary); L.DragGrip
        # toggles that property around grab() so only the drag pixmap uses it.
        self.playerCardList.setStyleSheet(
            f"QFrame#resultPlayerCard {{"
            f" background-color: {_pc['bg_tertiary']};"
            f" border: 1px solid {_pc['border_subtle']};"
            f" border-radius: {UI.RADIUS_MD}px;"
            f" }}"
            f"QFrame#resultPlayerCard[dragging=\"true\"] {{"
            f" background-color: {_pc['bg_quaternary']};"
            f" }}"
            f"QLabel#cardGrip {{ padding: 2px; }}")

        grid = self.settings.findChild(QGridLayout, "gridPlayers")
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self.playerCardList, 0, 0)

        # --- Save the entered players/characters to the local player DB ---
        # RESULT-tab inputs are otherwise write-only into `result_players`
        # (image rendering); this persists them so the same players carry their
        # mains into autocomplete / the Player List going forward.
        saveDbContainer = QWidget()
        saveDbCol = QVBoxLayout(saveDbContainer)
        saveDbCol.setContentsMargins(0, UI.SPACE_SM, 0, 0)
        saveDbCol.setSpacing(2)
        self.btSavePlayersToDB = QPushButton(
            QApplication.translate("app", "Save player characters"))
        self.btSavePlayersToDB.setIcon(QIcon(UI.icon_path("save.svg")))
        self.btSavePlayersToDB.clicked.connect(self._savePlayersToDB)
        saveDbCol.addWidget(self.btSavePlayersToDB)
        saveDbNote = QLabel(QApplication.translate(
            "app",
            "Saves player data so you can easily reuse it in future results."))
        saveDbNote.setWordWrap(True)
        saveDbNote.setStyleSheet(UI.secondary_label_style())
        saveDbCol.addWidget(saveDbNote)
        grid.addWidget(saveDbContainer, 1, 0)

        for i in range(16):
            rank_edit = self._makeRankEdit(i + 1)

            name_edit = QLineEdit()
            name_edit.setPlaceholderText(
                QApplication.translate("app", "Player Name"))

            sponsor_edit = QLineEdit()
            sponsor_edit.setPlaceholderText(
                QApplication.translate("app", "Sponsor"))
            sponsor_edit.setFixedWidth(80)

            # X (Twitter) handle, manually editable, on its own row between the
            # player name and the character combos. Feeds result_players["twitter"]
            # which _buildResultPostText reads for the @mention.
            twitter_edit = QLineEdit()
            twitter_edit.setPlaceholderText(
                QApplication.translate("app", "Twitter"))

            char1_combo  = self._makeCharCombo()
            color1_combo = self._makeColorCombo()
            char2_combo  = self._makeCharCombo(sub=True)
            color2_combo = self._makeColorCombo()
            char3_combo  = self._makeCharCombo(sub=True)
            color3_combo = self._makeColorCombo()

            # --- Build the card ---
            card = QFrame()
            card.setObjectName("resultPlayerCard")
            cardOuter = QHBoxLayout(card)
            cardOuter.setContentsMargins(8, 8, 8, 8)
            cardOuter.setSpacing(8)

            # Left column: rank spin box pinned top-left, drag grip alone at the
            # true vertical centre. Both share one grid cell (rank AlignTop,
            # grip AlignVCenter) so the grip centres over the full card height
            # regardless of the rank's height.
            leftCol = QGridLayout()
            leftCol.setContentsMargins(0, 0, 0, 0)
            grip = L.DragGrip(card, self.playerCardList)
            leftCol.addWidget(
                rank_edit, 0, 0,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            leftCol.addWidget(
                grip, 0, 0,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
            cardOuter.addLayout(leftCol, 0)

            fields = QGridLayout()
            fields.setVerticalSpacing(2)
            fields.setHorizontalSpacing(6)
            # Sponsor tag left of the player name ("[Sponsor] PlayerName"),
            # matching the scoreboard's display order. Nested HBox spanning
            # both columns so the fixed-width sponsor doesn't inherit the
            # stretching char-combo column below.
            nameRow = QHBoxLayout()
            nameRow.setSpacing(6)
            nameRow.addWidget(sponsor_edit)
            nameRow.addWidget(name_edit, 1)
            fields.addLayout(nameRow, 0, 0, 1, 2)
            fields.addWidget(twitter_edit, 1, 0, 1, 2)
            fields.addWidget(char1_combo,  2, 0)
            fields.addWidget(color1_combo, 2, 1)
            fields.addWidget(char2_combo,  3, 0)
            fields.addWidget(color2_combo, 3, 1)
            fields.addWidget(char3_combo,  4, 0)
            fields.addWidget(color3_combo, 4, 1)
            fields.setColumnStretch(0, 1)
            cardOuter.addLayout(fields, 1)

            self.playerCardList.addCard(card)

            # Connect signals. Char combos capture their own widgets (not a row
            # index) so the skin-model reload stays correct after reordering.
            name_edit.editingFinished.connect(self._syncPlayersToState)
            sponsor_edit.editingFinished.connect(self._syncPlayersToState)
            twitter_edit.editingFinished.connect(self._syncPlayersToState)
            char1_combo.currentIndexChanged.connect(
                lambda _, cc=char1_combo, vc=color1_combo:
                self._onCharComboChanged(cc, vc))
            char2_combo.currentIndexChanged.connect(
                lambda _, cc=char2_combo, vc=color2_combo:
                self._onCharComboChanged(cc, vc))
            char3_combo.currentIndexChanged.connect(
                lambda _, cc=char3_combo, vc=color3_combo:
                self._onCharComboChanged(cc, vc))
            color1_combo.currentIndexChanged.connect(self._syncPlayersToState)
            color2_combo.currentIndexChanged.connect(self._syncPlayersToState)
            color3_combo.currentIndexChanged.connect(self._syncPlayersToState)
            rank_edit.editingFinished.connect(self._syncPlayersToState)

            entry = (rank_edit, name_edit, sponsor_edit,
                     char1_combo, color1_combo,
                     char2_combo, color2_combo,
                     char3_combo, color3_combo,
                     twitter_edit)
            self.playerInputs.append(entry)
            self.playerCards.append(card)
            self._entryByCard[card] = entry

        # --- Team input rows (team layout) ---
        # Each rank is a team: a team name plus up to MAX_TEAM_MEMBERS members,
        # where each member is (player name, main fighter combo, color combo).
        # This group is shown only for the "team" layout; the singles group
        # above is shown otherwise (toggled in _onLayoutChanged).
        self.playersGroup = self.settings.findChild(
            QGroupBox, "groupBoxPlayers")
        self.teamInputs = []
        self.teamGroup = QGroupBox(
            QApplication.translate("app", "Top 8 Teams"))
        teamGroupLayout = QVBoxLayout()
        self.teamGroup.setLayout(teamGroupLayout)
        teamGrid = QGridLayout()
        teamGrid.setVerticalSpacing(2)
        teamGroupLayout.addLayout(teamGrid)

        rows_per_team = 1 + self.MAX_TEAM_MEMBERS  # header row + member rows
        for i in range(16):
            header_row = i * rows_per_team

            rank_edit = self._makeRankEdit(i + 1)

            name_edit = QLineEdit()
            name_edit.setPlaceholderText(
                QApplication.translate("app", "Team Name"))

            # rank spans the header + all member rows; team name fills cols 1-3
            teamGrid.addWidget(rank_edit, header_row, 0, rows_per_team, 1,
                               Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            teamGrid.addWidget(name_edit, header_row, 1, 1, 3)
            name_edit.editingFinished.connect(self._syncTeamsToState)
            rank_edit.editingFinished.connect(self._syncTeamsToState)

            members = []
            for m in range(self.MAX_TEAM_MEMBERS):
                row = header_row + 1 + m
                member_name = QLineEdit()
                member_name.setPlaceholderText(
                    QApplication.translate("app", "Player Name"))
                member_name.setFixedWidth(90)
                char_combo = self._makeCharCombo(sub=(m > 0))
                color_combo = self._makeColorCombo()

                teamGrid.addWidget(member_name, row, 1)
                teamGrid.addWidget(char_combo, row, 2)
                teamGrid.addWidget(color_combo, row, 3)

                member_name.editingFinished.connect(self._syncTeamsToState)
                char_combo.currentIndexChanged.connect(
                    lambda _, i=i, m=m: self._onTeamCharChanged(i, m))
                color_combo.currentIndexChanged.connect(
                    self._syncTeamsToState)

                members.append((member_name, char_combo, color_combo))

            self.teamInputs.append({
                "rank_label": rank_edit,
                "name_edit": name_edit,
                "members": members,
            })

        # Team rows live inside the scrolling area (verticalLayout_left_scroll)
        # alongside the singles group; verticalLayout_left is the always-visible
        # outer layout that hosts the "Load tournament results" button.
        leftScrollLayout = self.settings.findChild(
            QVBoxLayout, "verticalLayout_left_scroll")
        leftScrollLayout.addWidget(self.teamGroup)

        # Result-scoped theme / accent (colors.result.*), inheriting the
        # tournament colors while unset. Drives both the OBS result layouts and
        # the rendered image (main_generate_result.prepare_data).
        leftScrollLayout.addWidget(
            MakeFeatureColorCard(FEATURE_RESULT, "リザルトの色"))

        # QScrollArea (widgetResizable=True) doesn't reliably propagate its
        # content's natural width up through the layout chain — its own
        # minimumSizeHint() ignores the contained widget entirely, so the
        # left column was collapsing to the width of its narrowest sibling
        # (the "Load tournament results" button) instead of the player grid.
        # Pin the shared settings-pane width (also prevents the collapse).
        leftScrollArea = self.settings.findChild(QScrollArea, "scrollArea")
        leftScrollArea.setFixedWidth(L.LEFT_PANE_WIDTH)

        # --- Load from Start.gg ---
        self._standingsSignals = AutonaResultSettingsWidgetSignals()
        self._standingsSignals.StandingsLoaded.connect(self._onStandingsLoaded)

        # Insert the load button into the always-visible outer layout, above
        # the scrolling player area, so it stays on screen while the rows scroll.
        leftLayout = self.settings.findChild(QVBoxLayout, "verticalLayout_left")
        insertIdx = 0

        # Single "Load tournament results" button: pulls tournament info and
        # the top-N standings (8 or 16 by layout) in one action. Replaces the
        # former separate "Load tournament info" / "Load top 8/16 from StartGG"
        # buttons.
        self.btLoadResult = QPushButton(
            QApplication.translate("app", "Load tournament results"))
        L.tag_button(self.btLoadResult, L.BUTTON_PRIMARY)
        leftLayout.insertWidget(insertIdx, self.btLoadResult)
        insertIdx += 1
        self.btLoadResult.clicked.connect(self._loadResult)

        # --- Layout selector (placed above the preview, matching thumbnail UX) ---
        rightLayout = self.settings.findChild(QVBoxLayout, "verticalLayout_right")
        rightInsertIdx = 0
        layoutLabel = QLabel(
            QApplication.translate("app", "Select Result Layout"))
        # Match the VIDEO tab's template-selector heading (Design.md §5).
        L.apply_section_title(layoutLabel)
        layoutLabel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        rightLayout.insertWidget(rightInsertIdx, layoutLabel)
        rightInsertIdx += 1
        self.layoutCombo = QComboBox()
        self.layoutCombo.addItem(
            QApplication.translate("app", "Horizontal (1920x1080)"), "horizontal")
        self.layoutCombo.addItem(
            QApplication.translate("app", "Horizontal TOP16 (1920x1080)"), "horizontal_16")
        self.layoutCombo.addItem(
            QApplication.translate("app", "Vertical (960x1080)"), "vertical")
        self.layoutCombo.addItem(
            QApplication.translate("app", "Vertical Dark Gold 激 (960x1080)"), "vertical_geki")
        self.layoutCombo.addItem(
            QApplication.translate("app", "Team / Crew (1920x1080)"), "team")
        self.layoutCombo.addItem(
            QApplication.translate(
                "app", "Team / Crew 縦並び (1920x1080)"), "team_split")
        self.layoutCombo.currentIndexChanged.connect(self._onLayoutChanged)
        rightLayout.insertWidget(rightInsertIdx, self.layoutCombo)

        # --- Preview ---
        previewContainer = self.settings.findChild(QWidget, "resultPreview")
        previewContainer.layout().setContentsMargins(0, 0, 0, 0)
        # Frame the preview slot so the empty state stays visible. Mirrors
        # the thumbnailPreview treatment in AutonaThumbnailSettingsWidget.
        _p = UI.palette()
        previewContainer.setStyleSheet(
            f"QWidget#resultPreview {{"
            f"background-color: {_p['bg_tertiary']};"
            f"border: 1px solid {_p['border_subtle']};"
            f"border-radius: {UI.RADIUS_SM}px;"
            f"}}"
        )
        self.preview = ResultPreviewWidget()
        previewContainer.layout().addWidget(self.preview)
        # Reserve the vertical scrollbar gutter permanently. The preview fixes
        # its height to the pixmap aspect ratio in resizeEvent; if the scrollbar
        # could toggle, the viewport width would oscillate around the toggle
        # threshold and Qt's C++ layout would recurse into a stack overflow on a
        # single setFixedHeight call. AlwaysOn keeps the width stable so the
        # aspect-ratio recompute converges.
        scrollAreaRight = self.settings.findChild(QScrollArea, "scrollArea_right")
        if scrollAreaRight is not None:
            scrollAreaRight.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        # --- Buttons ---
        self.btCopyToClipboard = self.settings.findChild(
            QPushButton, "btCopyToClipboard")
        self.btCopyToClipboard.clicked.connect(self._copyToClipboard)

        self.btSaveResult = self.settings.findChild(
            QPushButton, "btSaveResult")
        self.btSaveResult.clicked.connect(self._saveResult)

        self.btPostResultToX = self.settings.findChild(
            QPushButton, "btPostResultToX")
        L.tag_button(self.btPostResultToX, L.BUTTON_PRIMARY)
        self.btPostResultToX.clicked.connect(self._postResultToX)

        # Normalise card padding / spacing across all top-level groups.
        for group in self.settings.findChildren(QGroupBox):
            L.apply_card_layout(group)

        # --- Debounce timer ---
        self._previewTimer = QTimer()
        self._previewTimer.setSingleShot(True)
        self._previewTimer.setInterval(300)
        self._previewTimer.timeout.connect(self._doGeneratePreview)

        # --- Renderer ---
        # Built on first reveal, not here: the off-screen QWebEngineView plus
        # its initial page load cost ~3s of startup for a panel most launches
        # never open (startup Step 4b-1). See _EnsureRenderer.
        self._renderer = None

        # Reload character models and preview when game changes
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._onGameChanged)

        # Reload preview when banner is changed in EVENT tab
        AutonaTournamentDataProvider.instance.signals.media_updated.connect(
            self.GeneratePreview)

        # Apply initial row visibility and generate preview (slots 9-16 hidden by default)
        self._onLayoutChanged()

    def _makeRankEdit(self, default_rank):
        """Create an editable rank field (small integer spin box).

        The rank is read back by _syncPlayersToState / _syncTeamsToState via
        value() and flows through to result_players[*]['rank'], so editing it
        here is reflected in the rendered result image. Start.gg standings
        overwrite it via setValue().
        """
        spin = QSpinBox()
        spin.setRange(1, 999)
        spin.setValue(default_rank)
        spin.setFixedWidth(52)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return spin

    def _makeCharCombo(self, sub=False):
        """Create a character selection combo box."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(100)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.completer().setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        proxy = NoIconProxyModel(combo)
        proxy.setSourceModel(AutonaGameAssetManager.instance.characterModel)
        combo.setModel(proxy)
        combo.setIconSize(QSize(0, 0))
        # Uniform 22px across main and sub combos so each character combo lines
        # up with its 22px color combo in the same grid row (the former 28px
        # main-combo left slot-1's color combo vertically offset by 3px).
        combo.setFixedHeight(22)
        combo.setCurrentIndex(-1)
        combo.lineEdit().setPlaceholderText(
            QApplication.translate("app", "Sub character") if sub
            else QApplication.translate("app", "Main character"))
        return combo

    def _makeColorCombo(self):
        """Create a color/skin selection combo box."""
        combo = QComboBox()
        combo.setIconSize(QSize(16, 16))
        combo.setFixedHeight(22)
        combo.setMinimumWidth(48)
        combo.setMaximumWidth(80)
        view = QListView()
        view.setIconSize(QSize(24, 24))
        combo.setView(view)
        return combo

    def _onGameChanged(self):
        """Re-bind character models when game changes (model object is replaced)."""
        for entry in self.playerInputs:
            # char combos are at indices 3, 5, 7
            for char_idx in (3, 5, 7):
                combo = entry[char_idx]
                combo.blockSignals(True)
                proxy = NoIconProxyModel(combo)
                proxy.setSourceModel(
                    AutonaGameAssetManager.instance.characterModel)
                combo.setModel(proxy)
                combo.setIconSize(QSize(0, 0))
                combo.setCurrentIndex(-1)
                combo.blockSignals(False)
            # color combos are at indices 4, 6, 8
            for color_idx in (4, 6, 8):
                entry[color_idx].setModel(QStandardItemModel())

        # Team member combos (mirror the singles re-bind above).
        for team in getattr(self, "teamInputs", []):
            for member_name, char_combo, color_combo in team["members"]:
                char_combo.blockSignals(True)
                proxy = NoIconProxyModel(char_combo)
                proxy.setSourceModel(
                    AutonaGameAssetManager.instance.characterModel)
                char_combo.setModel(proxy)
                char_combo.setIconSize(QSize(0, 0))
                char_combo.setCurrentIndex(-1)
                char_combo.blockSignals(False)
                color_combo.setModel(QStandardItemModel())

        self.GeneratePreview()

    def _resolveCharData(self, char_combo, color_combo, player_name=""):
        """Resolve character data from combo widgets, with scoreboard fallback."""
        skin_data = color_combo.currentData(Qt.ItemDataRole.UserRole)
        if skin_data and skin_data.get("assets"):
            return skin_data
        char_combo_data = char_combo.currentData(Qt.ItemDataRole.UserRole)
        if char_combo_data:
            return char_combo_data
        if player_name:
            return self._findCharacterForPlayer(player_name)
        return {}

    def _syncPlayersToState(self):
        """Write player inputs to StateManager so prepare_data can read them."""
        players = {}
        for i, entry in enumerate(self.playerInputs):
            rank_label, name_edit, sponsor_edit = entry[0], entry[1], entry[2]
            char1_combo, color1_combo = entry[3], entry[4]
            char2_combo, color2_combo = entry[5], entry[6]
            char3_combo, color3_combo = entry[7], entry[8]
            twitter_edit = entry[9]

            rank = rank_label.value()
            name    = name_edit.text().strip()
            sponsor = sponsor_edit.text().strip()
            twitter = twitter_edit.text().strip()

            char1 = self._resolveCharData(char1_combo, color1_combo, name)
            char2 = self._resolveCharData(char2_combo, color2_combo)
            char3 = self._resolveCharData(char3_combo, color3_combo)

            # characters list: non-empty entries only, in order
            characters = [c for c in [char1, char2, char3] if c]

            players[str(i + 1)] = {
                "rank": rank,
                "name": name,
                "sponsor": sponsor,
                "twitter": twitter,
                "character": char1,
                "characters": characters,
            }

        StateManager.Set("result_players", players)
        # No manual update button: every edit path lands here, so schedule a
        # debounced re-render (the manual GeneratePreview(True) path cancels
        # the timer and renders immediately).
        self._previewTimer.start()

    def _syncCurrentToState(self):
        """Sync whichever input set matches the active layout."""
        if self._isTeamLayout():
            self._syncTeamsToState()
        else:
            self._syncPlayersToState()

    @staticmethod
    def _mainsFromResultCombos(combo_pairs):
        """Build a mains list [[char_en_name, skin, variant], ...] from
        (char_combo, color_combo) pairs, skipping empty character slots.
        RESULT-tab combos carry no variant, so variant is always "" — the
        same [char, skin, variant] shape AutonaScoreboardPlayerWidget saves."""
        mains = []
        for char_combo, color_combo in combo_pairs:
            char_data = char_combo.currentData(Qt.ItemDataRole.UserRole)
            en_name = char_data.get("en_name") if char_data else None
            if not en_name:
                continue
            skin = color_combo.currentIndex()
            if skin is None or skin < 0:
                skin = 0
            mains.append([en_name, skin, ""])
        return mains

    def _persistPlayersToDB(self):
        """Persist the entered RESULT players (name/sponsor/twitter/mains) into
        the local player DB and return the number saved. Silent (no dialogs) so
        it can be reused both by the explicit button and as a side effect of
        saving / posting the result. Uses overwrite=False so it MERGES —
        existing country/real-name/etc. and other games' mains are preserved;
        only the current game's mains and the fields we carry are updated."""
        game = AutonaGameAssetManager.instance.selectedGame
        codename = game.get("codename", "") if game else ""

        records = []
        if self._isTeamLayout():
            for team in self.teamInputs:
                for member_name, char_combo, color_combo in team["members"]:
                    nm = member_name.text().strip()
                    if not nm:
                        continue
                    rec = {"gamerTag": nm}
                    mains = self._mainsFromResultCombos([(char_combo, color_combo)])
                    if mains and codename:
                        rec["mains"] = {codename: mains}
                    records.append(rec)
        else:
            for entry in self.playerInputs:
                nm = entry[1].text().strip()
                if not nm:
                    continue
                rec = {"gamerTag": nm}
                sponsor = entry[2].text().strip()
                if sponsor:
                    rec["prefix"] = sponsor
                twitter = entry[9].text().strip()
                if twitter:
                    rec["twitter"] = twitter
                mains = self._mainsFromResultCombos(
                    [(entry[3], entry[4]), (entry[5], entry[6]), (entry[7], entry[8])])
                if mains and codename:
                    rec["mains"] = {codename: mains}
                records.append(rec)

        if not records:
            return 0

        AutonaPlayerDB.AddPlayers(records, overwrite=False)
        logger.info(f"RESULT: saved {len(records)} player(s) to the local DB")
        return len(records)

    def _autoPersistPlayersToDB(self):
        """Best-effort DB persist as a side effect of saving/posting the
        result. Never interrupts the primary action with a dialog; failures are
        logged only so a DB hiccup can't block the image save / X post."""
        try:
            self._persistPlayersToDB()
        except Exception:
            logger.exception("RESULT: auto-save players to DB failed")

    def _savePlayersToDB(self):
        """Explicit 'Save player characters' button: persist + confirm."""
        count = self._persistPlayersToDB()
        if count == 0:
            QMessageBox.information(
                self,
                QApplication.translate("app", "Save player characters"),
                QApplication.translate(
                    "app", "There are no players to save."))
            return
        QMessageBox.information(
            self,
            QApplication.translate("app", "Save player characters"),
            QApplication.translate(
                "app", "Saved {0} player(s) to the local database.").format(
                    count))

    def _syncTeamsToState(self):
        """Write team inputs to StateManager as members[] per rank.

        Each rank entry: { rank, name (team name), members: [{name, character}] }
        prepare_data passes members through; result.js placeTeams renders them.
        """
        players = {}
        for i, team in enumerate(self.teamInputs):
            rank = team["rank_label"].value()
            team_name = team["name_edit"].text().strip()

            members = []
            for member_name, char_combo, color_combo in team["members"]:
                nm = member_name.text().strip()
                char = self._resolveCharData(char_combo, color_combo, nm)
                # Keep a member only when it has a name or a resolved fighter.
                if nm or char:
                    members.append({"name": nm, "character": char})

            players[str(i + 1)] = {
                "rank": rank,
                "name": team_name,
                "sponsor": "",
                "members": members,
            }

        StateManager.Set("result_players", players)
        # Debounced re-render on every edit (see _syncPlayersToState).
        self._previewTimer.start()

    def _onTeamCharChanged(self, team_index, member_index):
        """Load the skin model when a team member's fighter changes."""
        _, char_combo, color_combo = \
            self.teamInputs[team_index]["members"][member_index]
        char_data = char_combo.currentData(Qt.ItemDataRole.UserRole)
        if char_data and char_data.get("en_name"):
            skin_model = AutonaGameAssetManager.instance.skinModels.get(
                char_data.get("en_name"))
            if skin_model:
                color_combo.setModel(skin_model)
                color_combo.setCurrentIndex(0)
            else:
                color_combo.setModel(QStandardItemModel())
        else:
            color_combo.setModel(QStandardItemModel())
        self._syncTeamsToState()

    def _populateTeamsFromStandings(self, data):
        """Fill team inputs from Start.gg team standings.

        Each standings entry is { placement, name (team name), players: [roster
        with mains] }. Member m takes roster[m]'s gamerTag and primary main.
        """
        game = AutonaGameAssetManager.instance.selectedGame
        codename = game.get("codename", "") if game else ""

        # Capture member X (Twitter) handles by gamerTag for _buildResultPostText.
        self._standingsTwitter = {}
        for i, team in enumerate(self.teamInputs):
            rank_label = team["rank_label"]
            name_edit = team["name_edit"]

            if i < len(data):
                entry = data[i]
                placement = entry.get("placement", i + 1)
                rank_label.setValue(int(placement) if placement else i + 1)
                roster = entry.get("players", []) or []
                team_name = entry.get("name") or (
                    roster[0].get("gamerTag", "") if roster else "")
                name_edit.setText(team_name or "")

                for m, (member_name, char_combo, color_combo) in \
                        enumerate(team["members"]):
                    if m < len(roster):
                        pl = roster[m]
                        member_name.setText(pl.get("gamerTag", "") or "")
                        tw = (pl.get("twitter") or "").strip()
                        if pl.get("gamerTag") and tw:
                            self._standingsTwitter[pl.get("gamerTag")] = tw
                        game_mains = pl.get("mains", {}).get(codename, []) \
                            if codename else []
                        if game_mains and game_mains[0]:
                            char_codename = game_mains[0][0]
                            self._selectCharInCombo(char_combo, char_codename)
                            self._loadSkinModelInto(color_combo, char_codename)
                        else:
                            char_combo.setCurrentIndex(-1)
                            color_combo.setModel(QStandardItemModel())
                    else:
                        member_name.setText("")
                        char_combo.setCurrentIndex(-1)
                        color_combo.setModel(QStandardItemModel())
            else:
                rank_label.setValue(i + 1)
                name_edit.setText("")
                for member_name, char_combo, color_combo in team["members"]:
                    member_name.setText("")
                    char_combo.setCurrentIndex(-1)
                    color_combo.setModel(QStandardItemModel())

        self._syncTeamsToState()
        self.GeneratePreview(True)

    def _loadResult(self):
        """Load tournament info + top-N standings (8/16 by layout) at once."""
        if not AutonaTournamentDataProvider.instance.provider:
            logger.warning("No tournament provider set")
            return
        self.btLoadResult.setEnabled(False)
        self.btLoadResult.setText(
            QApplication.translate("app", "Loading..."))
        # Tournament info (name/date/etc.) — fires its own update signal.
        try:
            AutonaTournamentDataProvider.instance.GetTournamentData()
        except Exception as e:
            logger.warning(f"Failed to load tournament info: {e}")
        # Standings; the button is re-enabled in _onStandingsLoaded.
        AutonaTournamentDataProvider.instance.GetStandings(
            self._maxPlayersForLayout(), self._standingsSignals.StandingsLoaded)

    def _onStandingsLoaded(self, data):
        """Callback when standings data arrives from Start.gg."""
        self.btLoadResult.setEnabled(True)
        self.btLoadResult.setText(
            QApplication.translate("app", "Load tournament results"))

        if not data:
            logger.warning("No standings data received")
            return

        # Team layouts: standings carry full rosters → populate team inputs.
        if self._isTeamLayout():
            self._populateTeamsFromStandings(data)
            return

        # Capture each player's X (Twitter) handle keyed by gamerTag; the result
        # image / result_players don't store it, so _buildResultPostText reads
        # this freshly-fetched map (falling back to the local player DB).
        self._standingsTwitter = {}
        for i, entry in enumerate(self.playerInputs):
            rank_label, name_edit, sponsor_edit = entry[0], entry[1], entry[2]
            char1_combo, color1_combo = entry[3], entry[4]
            char2_combo, color2_combo = entry[5], entry[6]
            char3_combo, color3_combo = entry[7], entry[8]
            twitter_edit = entry[9]

            if i < len(data):
                team = data[i]
                placement = team.get("placement", i + 1)
                rank_label.setValue(int(placement) if placement else i + 1)

                players = team.get("players", [])
                if players:
                    player = players[0]
                    name_edit.setText(player.get("gamerTag", ""))
                    sponsor_edit.setText(player.get("prefix", "") or "")
                    tw = (player.get("twitter") or "").strip()
                    twitter_edit.setText(tw)
                    if player.get("gamerTag") and tw:
                        self._standingsTwitter[player.get("gamerTag")] = tw
                    self._setCharacterFromMains(i, player)
                else:
                    name_edit.setText("")
                    sponsor_edit.setText("")
                    twitter_edit.setText("")
                    for cc, cv in [(char1_combo, color1_combo),
                                   (char2_combo, color2_combo),
                                   (char3_combo, color3_combo)]:
                        cc.setCurrentIndex(-1)
                        cv.setModel(QStandardItemModel())
            else:
                rank_label.setValue(i + 1)
                name_edit.setText("")
                sponsor_edit.setText("")
                twitter_edit.setText("")
                for cc, cv in [(char1_combo, color1_combo),
                               (char2_combo, color2_combo),
                               (char3_combo, color3_combo)]:
                    cc.setCurrentIndex(-1)
                    cv.setModel(QStandardItemModel())

        self._syncPlayersToState()
        self.GeneratePreview(True)

    def _setCharacterFromMains(self, index, playerData):
        """Set char/color combos (1st–3rd) from Start.gg mains data."""
        mains = playerData.get("mains", {})
        game = AutonaGameAssetManager.instance.selectedGame
        # Combo pairs: (char_combo_idx, color_combo_idx) in playerInputs tuple
        slot_pairs = [(3, 4), (5, 6), (7, 8)]

        if not game or not mains:
            for ci, vi in slot_pairs:
                self.playerInputs[index][ci].setCurrentIndex(-1)
                self.playerInputs[index][vi].setModel(QStandardItemModel())
            return

        codename = game.get("codename", "")
        game_mains = mains.get(codename, [])

        for slot, (ci, vi) in enumerate(slot_pairs):
            char_combo  = self.playerInputs[index][ci]
            color_combo = self.playerInputs[index][vi]
            if slot < len(game_mains) and game_mains[slot]:
                char_codename = game_mains[slot][0]
                self._selectCharInCombo(char_combo, char_codename)
                self._loadSkinModelInto(color_combo, char_codename)
            else:
                char_combo.setCurrentIndex(-1)
                color_combo.setModel(QStandardItemModel())

    def _loadSkinModelInto(self, color_combo, char_codename):
        """Load skin model into a color combo for the given character codename."""
        color_combo.blockSignals(True)
        char_entry = AutonaGameAssetManager.instance.characters.get(char_codename)
        if not char_entry:
            for val in AutonaGameAssetManager.instance.characters.values():
                if val.get("codename") == char_codename:
                    char_entry = val
                    break
        en_name = char_entry.get("en_name", char_codename) if char_entry else char_codename
        skin_model = AutonaGameAssetManager.instance.skinModels.get(en_name)
        if skin_model:
            color_combo.setModel(skin_model)
            color_combo.setCurrentIndex(0)
        else:
            color_combo.setModel(QStandardItemModel())
        color_combo.blockSignals(False)

    def _onCharComboChanged(self, char_combo, color_combo):
        """Load the skin model for a character combo when it changes.

        Takes the specific combo widgets (not a row index) so it stays correct
        after cards are reordered by drag.
        """
        char_data = char_combo.currentData(Qt.ItemDataRole.UserRole)
        if char_data and char_data.get("en_name"):
            skin_model = AutonaGameAssetManager.instance.skinModels.get(
                char_data.get("en_name"))
            if skin_model:
                color_combo.setModel(skin_model)
                color_combo.setCurrentIndex(0)
            else:
                color_combo.setModel(QStandardItemModel())
        else:
            color_combo.setModel(QStandardItemModel())
        self._syncPlayersToState()

    def _onPlayersReordered(self):
        """Rebuild playerInputs/playerCards to the new visual card order, then
        renumber ranks 1..N by position and resync + refresh the preview."""
        order = self.playerCardList.cards()
        self.playerCards = list(order)
        self.playerInputs = [self._entryByCard[c] for c in order]
        for i, entry in enumerate(self.playerInputs):
            spin = entry[0]
            spin.blockSignals(True)
            spin.setValue(i + 1)
            spin.blockSignals(False)
        self._applyPlayerVisibility()
        self._syncPlayersToState()
        self.GeneratePreview(True)

    def _applyPlayerVisibility(self):
        """Show the top-N player cards (N = 8/16 by layout); hide the rest.
        Team mode hides all singles cards."""
        is_team = self._isTeamLayout()
        n = self._maxPlayersForLayout()
        for i, card in enumerate(self.playerCards):
            card.setVisible((not is_team) and i < n)

    def _selectCharInCombo(self, char_combo, char_codename):
        """Select a character in a combo box by codename."""
        char_combo.blockSignals(True)
        model = char_combo.model()
        for row in range(model.rowCount()):
            data = model.data(model.index(row, 0), Qt.ItemDataRole.UserRole)
            if data and data.get("codename") == char_codename:
                char_combo.setCurrentIndex(row)
                char_combo.blockSignals(False)
                return
        # Fallback: match by en_name
        char_entry = AutonaGameAssetManager.instance.characters.get(char_codename)
        if char_entry:
            en_name = char_entry.get("en_name", "")
            for row in range(model.rowCount()):
                data = model.data(model.index(row, 0), Qt.ItemDataRole.UserRole)
                if data and data.get("en_name") == en_name:
                    char_combo.setCurrentIndex(row)
                    break

        char_combo.blockSignals(False)

    def _findCharacterForPlayer(self, player_name):
        """Search scoreboard state for a player's first character data."""
        chars = self._findAllCharactersForPlayer(player_name)
        return chars[0] if chars else {}

    def _findAllCharactersForPlayer(self, player_name):
        """Search scoreboard state for all characters of a player (ordered list)."""
        if not player_name:
            return []
        try:
            scores = StateManager.Get("score") or {}
            for sb_key, sb in scores.items():
                for team_key in ["1", "2"]:
                    players = (sb.get("team", {}).get(team_key, {})
                               .get("player", {}))
                    for p_key, p_data in players.items():
                        if (p_data.get("name", "").strip().lower()
                                == player_name.lower()):
                            char = p_data.get("character", {})
                            if char:
                                return [char[k] for k in sorted(char.keys())
                                        if char.get(k)]
        except Exception as e:
            logger.debug(f"Character lookup failed: {e}")
        return []

    def _selectedLayout(self):
        return self.layoutCombo.currentData() or "horizontal"

    def _isTeamLayout(self):
        """True for any team / Crew layout (shares the Teams input + members[])."""
        return self._selectedLayout() in ("team", "team_split")

    def SetBattleFormat(self, fmt):
        # Driven by the "Rules" menu. Team and Crewbattle both use the team
        # standings layout (crew reuses it). 1v1 → leave the team layout
        # (preserving any other manual choice for individual standings).
        # Accepts a bool for backward compatibility with older callers.
        if isinstance(fmt, bool):
            fmt = "team" if fmt else "singles"
        if fmt in ("team", "crew"):
            # Only auto-switch to the default team layout when not already on a
            # team layout, so a manual "team_split" choice is preserved.
            if not self._isTeamLayout():
                idx = self.layoutCombo.findData("team")
                if idx >= 0:
                    self.layoutCombo.setCurrentIndex(idx)
        elif self._isTeamLayout():
            idx = self.layoutCombo.findData("horizontal")
            if idx >= 0:
                self.layoutCombo.setCurrentIndex(idx)

    def _maxPlayersForLayout(self):
        return 16 if self._selectedLayout() == "horizontal_16" else 8

    def _onLayoutChanged(self):
        """Toggle singles/team input groups and row visibility to match layout."""
        is_team = self._isTeamLayout()
        self.teamGroup.setVisible(is_team)
        self.playersGroup.setVisible(not is_team)

        n = self._maxPlayersForLayout()

        # Singles cards (hidden entirely in team mode).
        self._applyPlayerVisibility()

        # Team rows (hidden entirely in singles mode).
        for i, team in enumerate(self.teamInputs):
            visible = is_team and i < n
            team["rank_label"].setVisible(visible)
            team["name_edit"].setVisible(visible)
            for member_name, char_combo, color_combo in team["members"]:
                member_name.setVisible(visible)
                char_combo.setVisible(visible)
                color_combo.setVisible(visible)

        self.GeneratePreview(True)

    def GeneratePreview(self, manual=False):
        if manual:
            self._syncCurrentToState()
            # The sync above schedules a debounced render; cancel it since
            # we render immediately here.
            self._previewTimer.stop()
            self._doGeneratePreview()
        else:
            self._previewTimer.start()

    def _EnsureRenderer(self):
        """Build the off-screen renderer on first panel reveal; return it.

        Deliberately NOT called from the background signal handlers wired in
        __init__: letting a tournament fetch or a game reload spin the
        renderer up behind the scenes would give back the startup time this
        deferral buys. Those paths skip rendering while _renderer is None and
        showEvent re-renders from the latest state, so nothing is lost.

        The layout is resolved here rather than left to the renderer's
        default: by the time the panel is first shown, Window.SetBattleFormat
        has already put layoutCombo on the right entry, so the first page
        load is the correct one and the old load-default-then-reload pair is
        gone.
        """
        if self._renderer is None:
            from .result.ResultRenderer import ResultRenderer
            self._renderer = ResultRenderer.instance(self._selectedLayout())
            self._renderer.renderComplete.connect(self._onRenderComplete)
        return self._renderer

    def showEvent(self, e):
        super().showEvent(e)
        # No manual update button: tournament info edited elsewhere feeds the
        # rendered image, so re-render when the panel is revealed.
        self._EnsureRenderer()
        self._previewTimer.start()

    def _doGeneratePreview(self):
        # Skip entirely until the panel has been shown once (see
        # _EnsureRenderer). Every caller reaches here again via showEvent's
        # timer, so a skipped update is re-rendered on reveal.
        if self._renderer is None:
            return
        try:
            data, config = result_gen.prepare_data(
                settingsManager=SettingsManager,
                isPreview=True,
                gameAssetManager=AutonaGameAssetManager,
                max_players=self._maxPlayersForLayout(),
                layout=self._selectedLayout())

            self._renderer.render(
                data=data, config=config, layout=self._selectedLayout())
        except Exception as e:
            logger.warning(f"Result preview generation failed: {e}")

    def _onRenderComplete(self, pixmap):
        if pixmap.isNull():
            return
        out_path = AutonaResolve("tmp/result")
        Path(out_path).mkdir(parents=True, exist_ok=True)
        tmp_file = f"{out_path}/preview.png"
        pixmap.save(tmp_file)
        self.preview.setPixmap(pixmap)

    def _copyToClipboard(self):
        try:
            self._syncCurrentToState()
            path = result_gen.generate(
                settingsManager=SettingsManager,
                layout=self._selectedLayout())
            if path:
                image = QImage(path)
                if not image.isNull():
                    QApplication.clipboard().setImage(image)
                    logger.info("Result image copied to clipboard")
        except Exception as e:
            logger.exception("Failed to copy result to clipboard")
            msgBox = QMessageBox()
            msgBox.setText(QApplication.translate("app", "Warning"))
            msgBox.setInformativeText(str(e))
            msgBox.setIcon(QMessageBox.Warning)
            msgBox.exec()

    def _saveResult(self):
        try:
            self._syncCurrentToState()
            # Persist the entered players to the local DB alongside the save.
            self._autoPersistPlayersToDB()
            path = result_gen.generate(
                settingsManager=SettingsManager,
                layout=self._selectedLayout())
            if path:
                msgBox = QMessageBox()
                msgBox.setWindowIcon(
                    QIcon(AutonaResolve("src/layout/icon.png")))
                msgBox.setWindowTitle(QApplication.translate("app", "Autona - Result"))
                msgBox.setText(
                    QApplication.translate(
                        "app",
                        "The result image has been saved here:")
                    + " " + path)
                msgBox.setIcon(QMessageBox.NoIcon)
                msgBox.exec()
        except Exception as e:
            logger.exception("Failed to save result image")
            msgBox = QMessageBox()
            msgBox.setText(QApplication.translate("app", "Warning"))
            msgBox.setInformativeText(str(e))
            msgBox.setIcon(QMessageBox.Warning)
            msgBox.exec()

    def _postResultToX(self):
        """Post the result to X: copy the result image to the clipboard and
        open the X compose window prefilled with descriptive standings text.

        Mirrors the SCORE tab's PostToX (clipboard + intent URL). The text is
        built from result_players (RESULT tab) by _buildResultPostText, so the
        posted text always describes the same standings as the posted image.
        X has no image URL param, so the image rides on the clipboard for the
        user to paste into the compose window.
        """
        try:
            # 1. Render the result image and put it on the clipboard.
            self._syncCurrentToState()
            # Persist the entered players to the local DB alongside the post.
            self._autoPersistPlayersToDB()
            path = result_gen.generate(
                settingsManager=SettingsManager,
                layout=self._selectedLayout())
            if path:
                image = QImage(path)
                if not image.isNull():
                    QApplication.clipboard().setImage(image)
                    logger.info("Result image copied to clipboard for X post")

            # 2. Build the post text from the RESULT tab standings.
            text = self._buildResultPostText()

            # 3. Open X compose prefilled with the text.
            url = ("https://twitter.com/intent/tweet?text="
                   + urllib.parse.quote(text))
            webbrowser.open(url)
        except Exception as e:
            logger.exception("Failed to post result to X")
            msgBox = QMessageBox()
            msgBox.setText(QApplication.translate("app", "Warning"))
            msgBox.setInformativeText(str(e))
            msgBox.setIcon(QMessageBox.Warning)
            msgBox.exec()

    def _buildResultPostText(self):
        """Build X-post text from result_players (RESULT tab data).

        Reads the live result_players that the result image is rendered from,
        so the posted text and image always describe the same standings. Uses
        the explicit per-slot rank instead of placement math. Appends the
        Startgg / Stream URLs from tournament info + stream settings.
        """
        def num_key(x):
            return int(x) if str(x).isdigit() else 0

        tournament_name = StateManager.Get("tournamentInfo.tournamentName") or ""
        event_name = StateManager.Get("tournamentInfo.eventName") or ""
        event_date = StateManager.Get("tournamentInfo.startAt") or ""
        num_entrants = StateManager.Get("tournamentInfo.numEntrants", 0) or 0

        lines = []
        if tournament_name:
            lines.append(tournament_name.upper())
        info_parts = [event_name, event_date]
        if num_entrants:
            info_parts.append(str(num_entrants))
        info_line = " - ".join([s for s in info_parts if s])
        if info_line:
            lines.append(info_line)
        lines.append("")

        # Resolve a player's X handle as " @handle" (empty when unknown).
        # Priority: manual result_players["twitter"] → freshly-fetched standings
        # map → local player DB keyed by "prefix gamerTag" / gamerTag.
        twitter_map = getattr(self, "_standingsTwitter", {}) or {}

        def x_handle(name, sponsor="", manual=""):
            raw = (manual or "").strip()
            if not raw and name:
                raw = twitter_map.get(name, "")
            if not raw and name:
                key = f"{sponsor} {name}".strip() if sponsor else name
                rec = (AutonaPlayerDB.database.get(key)
                       or AutonaPlayerDB.database.get(name) or {})
                raw = rec.get("twitter", "") or ""
            handle = raw.lstrip("@").strip()
            return f" @{handle}" if handle else ""

        # Only the TOP 4 standings are listed in the post text.
        MAX_RANKS = 4
        players = StateManager.Get("result_players") or {}
        is_team = self._isTeamLayout()
        emitted = 0
        for key in sorted(players.keys(), key=num_key):
            if emitted >= MAX_RANKS:
                break
            p = players[key] or {}
            rank = p.get("rank", key)
            if is_team:
                team_name = (p.get("name") or "").strip()
                member_entries = []
                for m in p.get("members", []) or []:
                    nm = (m.get("name") or "").strip()
                    if not nm:
                        continue
                    member_entries.append(f"{nm}{x_handle(nm)}")
                if not team_name and not member_entries:
                    continue
                members_text = " / ".join(member_entries)
                if team_name:
                    lines.append(f"{rank}/ {team_name} [{members_text}]")
                else:
                    lines.append(f"{rank}/ {members_text}")
                emitted += 1
            else:
                name = (p.get("name") or "").strip()
                if not name:
                    continue
                sponsor = (p.get("sponsor") or "").strip()
                base = f"{sponsor} {name}" if sponsor else name
                base = f"{base}{x_handle(name, sponsor, p.get('twitter'))}"
                lines.append(f"{rank}/ {base}")
                emitted += 1

        # Startgg / Stream URLs. The 配信先 combo (配信設定 panel) publishes to
        # the broadcast-scoped stream.* subtree.
        startgg_url = SettingsManager.Get("TOURNAMENT_URL", "") or ""
        stream_url = StateManager.Get("stream.url", "") or ""
        if startgg_url:
            lines.append("")
            lines.append("■Startgg")
            lines.append(startgg_url)
        if stream_url:
            lines.append("")
            lines.append("■Stream")
            lines.append(stream_url)

        return "\n".join(lines).strip("\n")
