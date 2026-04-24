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
from .Helpers.AutonaDirHelper import AutonaResolve


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

    def setPixmap(self, pixmap):
        self._pixmap = pixmap
        self._updateMaxHeight()
        self.repaint()

    def resizeEvent(self, event):
        self._updateMaxHeight()
        self.repaint()

    def _updateMaxHeight(self):
        if self._pixmap and self._pixmap.width() > 0 and self.width() > 0:
            desired = int(
                self.width() * self._pixmap.height() / self._pixmap.width())
            # Fix height to aspect ratio so the wrapping scroll area scrolls
            # when the viewport is shorter than the preview.
            if self.minimumHeight() != desired or self.maximumHeight() != desired:
                self.setFixedHeight(desired)

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
    def __init__(self, *args):
        super().__init__(*args)

        self.setWindowTitle(
            QApplication.translate("app", "Result Settings"))
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget_container = QWidget()
        self.setWidget(self.widget_container)
        self.widget_container.setLayout(QVBoxLayout())

        self.settings = uic.loadUi(
            AutonaResolve("src/layout/AutonaResultSettings.ui"))
        self.widget_container.layout().addWidget(self.settings)

        # --- Player input rows (4 rows per player) ---
        # Row 1: rank(col 0, spans 4), name(col 1), sponsor(col 2)
        # Row 2: main char combo, main color combo
        # Row 3: secondary char combo, secondary color combo
        # Row 4: tertiary char combo, tertiary color combo
        # playerInputs entry:
        #   (rank_label, name_edit, sponsor_edit,
        #    char1_combo, color1_combo,
        #    char2_combo, color2_combo,
        #    char3_combo, color3_combo)
        self.playerInputs = []
        grid = self.settings.findChild(QGridLayout, "gridPlayers")
        grid.setVerticalSpacing(2)

        for i in range(16):
            idx = i
            base = i * 4  # 4 grid rows per player
            row1 = base
            row2 = base + 1
            row3 = base + 2
            row4 = base + 3

            rank_label = QLabel(f"{i + 1}")
            rank_label.setFixedWidth(30)
            rank_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop)

            name_edit = QLineEdit()
            name_edit.setPlaceholderText(
                QApplication.translate("app", "Player Name"))

            sponsor_edit = QLineEdit()
            sponsor_edit.setPlaceholderText(
                QApplication.translate("app", "Sponsor"))
            sponsor_edit.setFixedWidth(80)

            char1_combo  = self._makeCharCombo()
            color1_combo = self._makeColorCombo()
            char2_combo  = self._makeCharCombo(sub=True)
            color2_combo = self._makeColorCombo()
            char3_combo  = self._makeCharCombo(sub=True)
            color3_combo = self._makeColorCombo()

            # Row 1: rank spans all 4 rows, name + sponsor
            grid.addWidget(rank_label,  row1, 0, 4, 1)
            grid.addWidget(name_edit,   row1, 1)
            grid.addWidget(sponsor_edit, row1, 2)
            # Row 2–4: char + color per slot
            grid.addWidget(char1_combo,  row2, 1)
            grid.addWidget(color1_combo, row2, 2)
            grid.addWidget(char2_combo,  row3, 1)
            grid.addWidget(color2_combo, row3, 2)
            grid.addWidget(char3_combo,  row4, 1)
            grid.addWidget(color3_combo, row4, 2)

            # Connect signals
            name_edit.editingFinished.connect(self._syncPlayersToState)
            sponsor_edit.editingFinished.connect(self._syncPlayersToState)
            char1_combo.currentIndexChanged.connect(
                lambda _, i=idx: self._onAnyCharChanged(i, 0))
            char2_combo.currentIndexChanged.connect(
                lambda _, i=idx: self._onAnyCharChanged(i, 1))
            char3_combo.currentIndexChanged.connect(
                lambda _, i=idx: self._onAnyCharChanged(i, 2))
            color1_combo.currentIndexChanged.connect(self._syncPlayersToState)
            color2_combo.currentIndexChanged.connect(self._syncPlayersToState)
            color3_combo.currentIndexChanged.connect(self._syncPlayersToState)

            self.playerInputs.append(
                (rank_label, name_edit, sponsor_edit,
                 char1_combo, color1_combo,
                 char2_combo, color2_combo,
                 char3_combo, color3_combo))

        # --- Load from Start.gg ---
        self._standingsSignals = AutonaResultSettingsWidgetSignals()
        self._standingsSignals.StandingsLoaded.connect(self._onStandingsLoaded)

        # Place load / layout controls above the Top 8 Players group box.
        leftLayout = self.settings.findChild(QVBoxLayout, "verticalLayout_left")
        insertIdx = 0

        self.btLoadFromStartgg = QPushButton(
            QApplication.translate("app", "Load top 8 from StartGG"))
        leftLayout.insertWidget(insertIdx, self.btLoadFromStartgg)
        insertIdx += 1
        self.btLoadFromStartgg.clicked.connect(self._loadFromStartgg)

        # --- Load tournament info ---
        self.btLoadTournamentInfo = QPushButton(
            QApplication.translate("app", "Load tournament info"))
        leftLayout.insertWidget(insertIdx, self.btLoadTournamentInfo)
        insertIdx += 1
        self.btLoadTournamentInfo.clicked.connect(self._loadTournamentInfo)

        # --- Layout selector (placed above the preview, matching thumbnail UX) ---
        rightLayout = self.settings.findChild(QVBoxLayout, "verticalLayout_right")
        rightInsertIdx = 0
        layoutLabel = QLabel(
            QApplication.translate("app", "Select Result Layout"))
        _labelFont = layoutLabel.font()
        _labelFont.setPointSize(10)
        _labelFont.setBold(True)
        layoutLabel.setFont(_labelFont)
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
        self.layoutCombo.currentIndexChanged.connect(self._onLayoutChanged)
        rightLayout.insertWidget(rightInsertIdx, self.layoutCombo)

        # --- Preview ---
        previewContainer = self.settings.findChild(QWidget, "resultPreview")
        previewContainer.layout().setContentsMargins(0, 0, 0, 0)
        self.preview = ResultPreviewWidget()
        previewContainer.layout().addWidget(self.preview)

        # --- Buttons ---
        self.btUpdatePreview = self.settings.findChild(
            QPushButton, "btUpdatePreview")
        self.btUpdatePreview.clicked.connect(lambda: self.GeneratePreview(True))

        self.btCopyToClipboard = self.settings.findChild(
            QPushButton, "btCopyToClipboard")
        self.btCopyToClipboard.clicked.connect(self._copyToClipboard)

        self.btSaveResult = self.settings.findChild(
            QPushButton, "btSaveResult")
        self.btSaveResult.clicked.connect(self._saveResult)

        # --- Debounce timer ---
        self._previewTimer = QTimer()
        self._previewTimer.setSingleShot(True)
        self._previewTimer.setInterval(300)
        self._previewTimer.timeout.connect(self._doGeneratePreview)

        # --- Renderer ---
        from .result.ResultRenderer import ResultRenderer
        self._renderer = ResultRenderer.instance()
        self._renderer.renderComplete.connect(self._onRenderComplete)

        # Reload character models and preview when game changes
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._onGameChanged)

        # Apply initial row visibility and generate preview (slots 9-16 hidden by default)
        self._onLayoutChanged()

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
        combo.setFixedHeight(22 if sub else 28)
        combo.setCurrentIndex(-1)
        if sub:
            combo.lineEdit().setPlaceholderText(
                QApplication.translate("app", "Sub character"))
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

            try:
                rank = int(rank_label.text())
            except ValueError:
                rank = i + 1
            name    = name_edit.text().strip()
            sponsor = sponsor_edit.text().strip()

            char1 = self._resolveCharData(char1_combo, color1_combo, name)
            char2 = self._resolveCharData(char2_combo, color2_combo)
            char3 = self._resolveCharData(char3_combo, color3_combo)

            # characters list: non-empty entries only, in order
            characters = [c for c in [char1, char2, char3] if c]

            players[str(i + 1)] = {
                "rank": rank,
                "name": name,
                "sponsor": sponsor,
                "character": char1,
                "characters": characters,
            }

        StateManager.Set("result_players", players)

    def _loadFromStartgg(self):
        """Fetch standings from Start.gg (count depends on selected layout)."""
        if not AutonaTournamentDataProvider.instance.provider:
            logger.warning("No tournament provider set")
            return
        self.btLoadFromStartgg.setEnabled(False)
        self.btLoadFromStartgg.setText(
            QApplication.translate("app", "Loading..."))
        AutonaTournamentDataProvider.instance.GetStandings(
            self._maxPlayersForLayout(), self._standingsSignals.StandingsLoaded)

    def _onStandingsLoaded(self, data):
        """Callback when standings data arrives from Start.gg."""
        self.btLoadFromStartgg.setEnabled(True)
        n = self._maxPlayersForLayout()
        self.btLoadFromStartgg.setText(
            QApplication.translate("app", "Load top {0} from StartGG").format(n))

        if not data:
            logger.warning("No standings data received")
            return

        for i, entry in enumerate(self.playerInputs):
            rank_label, name_edit, sponsor_edit = entry[0], entry[1], entry[2]
            char1_combo, color1_combo = entry[3], entry[4]
            char2_combo, color2_combo = entry[5], entry[6]
            char3_combo, color3_combo = entry[7], entry[8]

            if i < len(data):
                team = data[i]
                placement = team.get("placement", i + 1)
                rank_label.setText(str(placement) if placement else str(i + 1))

                players = team.get("players", [])
                if players:
                    player = players[0]
                    name_edit.setText(player.get("gamerTag", ""))
                    sponsor_edit.setText(player.get("prefix", "") or "")
                    self._setCharacterFromMains(i, player)
                else:
                    name_edit.setText("")
                    sponsor_edit.setText("")
                    for cc, cv in [(char1_combo, color1_combo),
                                   (char2_combo, color2_combo),
                                   (char3_combo, color3_combo)]:
                        cc.setCurrentIndex(-1)
                        cv.setModel(QStandardItemModel())
            else:
                rank_label.setText(str(i + 1))
                name_edit.setText("")
                sponsor_edit.setText("")
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

    def _onAnyCharChanged(self, player_index, slot):
        """Called when any character combo (0=main, 1=2nd, 2=3rd) changes."""
        # slot maps to tuple indices: 0→(3,4), 1→(5,6), 2→(7,8)
        ci = 3 + slot * 2
        vi = 4 + slot * 2
        char_combo  = self.playerInputs[player_index][ci]
        color_combo = self.playerInputs[player_index][vi]
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

    def _loadTournamentInfo(self):
        """Fetch tournament info from Start.gg and update state."""
        if not AutonaTournamentDataProvider.instance.provider:
            logger.warning("No tournament provider set")
            return
        try:
            AutonaTournamentDataProvider.instance.GetTournamentData()
            logger.info("Tournament info loaded")
        except Exception as e:
            logger.warning(f"Failed to load tournament info: {e}")

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

    def _maxPlayersForLayout(self):
        return 16 if self._selectedLayout() == "horizontal_16" else 8

    def _onLayoutChanged(self):
        """Update Start.gg button label and show/hide player rows to match layout."""
        n = self._maxPlayersForLayout()
        label = QApplication.translate("app", "Load top {0} from StartGG").format(n)
        self.btLoadFromStartgg.setText(label)

        for i, entry in enumerate(self.playerInputs):
            visible = i < n
            for widget in entry:
                widget.setVisible(visible)

        self.GeneratePreview(True)

    def GeneratePreview(self, manual=False):
        if manual:
            self._syncPlayersToState()
            self._doGeneratePreview()
        else:
            self._previewTimer.start()

    def _doGeneratePreview(self):
        try:
            data, config = result_gen.prepare_data(
                settingsManager=SettingsManager,
                isPreview=True,
                gameAssetManager=AutonaGameAssetManager,
                max_players=self._maxPlayersForLayout())

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
            self._syncPlayersToState()
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
            self._syncPlayersToState()
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
