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
        self.setFloating(True)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget_container = QWidget()
        self.setWidget(self.widget_container)
        self.widget_container.setLayout(QVBoxLayout())

        self.settings = uic.loadUi(
            AutonaResolve("src/layout/AutonaResultSettings.ui"))
        self.widget_container.layout().addWidget(self.settings)

        # --- Player input rows (2 rows per player) ---
        # Row 1: rank, name, sponsor
        # Row 2: (empty), character, color
        # Each entry: (rank_label, name_edit, sponsor_edit, char_combo, color_combo)
        self.playerInputs = []
        grid = self.settings.findChild(QGridLayout, "gridPlayers")
        grid.setVerticalSpacing(2)

        for i in range(16):
            idx = i
            base = i * 2  # 2 grid rows per player
            row1 = base      # name row
            row2 = base + 1  # character row

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

            # Character selection combo
            char_combo = QComboBox()
            char_combo.setEditable(True)
            char_combo.setMinimumWidth(100)
            char_combo.completer().setFilterMode(
                Qt.MatchFlag.MatchContains)
            char_combo.completer().setCompletionMode(
                QCompleter.CompletionMode.PopupCompletion)
            proxy = NoIconProxyModel(char_combo)
            proxy.setSourceModel(
                AutonaGameAssetManager.instance.characterModel)
            char_combo.setModel(proxy)
            char_combo.setIconSize(QSize(0, 0))
            char_combo.setFixedHeight(28)
            char_combo.setCurrentIndex(-1)

            # Color/skin selection combo
            color_combo = QComboBox()
            color_combo.setIconSize(QSize(16, 16))
            color_combo.setFixedHeight(28)
            color_combo.setMinimumWidth(48)
            color_combo.setMaximumWidth(80)
            view = QListView()
            view.setIconSize(QSize(24, 24))
            color_combo.setView(view)

            # Row 1: rank spans 2 rows, name + sponsor
            grid.addWidget(rank_label, row1, 0, 2, 1)
            grid.addWidget(name_edit, row1, 1)
            grid.addWidget(sponsor_edit, row1, 2)
            # Row 2: character + color
            grid.addWidget(char_combo, row2, 1)
            grid.addWidget(color_combo, row2, 2)

            # Update state on edit
            name_edit.editingFinished.connect(self._syncPlayersToState)
            sponsor_edit.editingFinished.connect(self._syncPlayersToState)
            char_combo.currentIndexChanged.connect(
                lambda _, i=idx: self._onCharacterChanged(i))
            color_combo.currentIndexChanged.connect(self._syncPlayersToState)

            self.playerInputs.append(
                (rank_label, name_edit, sponsor_edit, char_combo, color_combo))

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

        # --- Layout selector ---
        layoutRow = QHBoxLayout()
        layoutRow.addWidget(QLabel(
            QApplication.translate("app", "Layout:")))
        self.layoutCombo = QComboBox()
        self.layoutCombo.addItem(
            QApplication.translate("app", "Horizontal (1920x1080)"), "horizontal")
        self.layoutCombo.addItem(
            QApplication.translate("app", "Horizontal TOP16 (1920x1080)"), "horizontal_16")
        self.layoutCombo.addItem(
            QApplication.translate("app", "Vertical (960x1080)"), "vertical")
        self.layoutCombo.currentIndexChanged.connect(self._onLayoutChanged)
        layoutRow.addWidget(self.layoutCombo)
        leftLayout.insertLayout(insertIdx, layoutRow)

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

    def _onGameChanged(self):
        """Re-bind character models when game changes (model object is replaced)."""
        for _, _, _, char_combo, color_combo in self.playerInputs:
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

    def _syncPlayersToState(self):
        """Write player inputs to StateManager so prepare_data can read them."""
        players = {}
        for i, (rank_label, name_edit, sponsor_edit, char_combo, color_combo) in enumerate(
                self.playerInputs):
            # Read rank from label (may be set by Start.gg placement)
            try:
                rank = int(rank_label.text())
            except ValueError:
                rank = i + 1
            name = name_edit.text().strip()
            sponsor = sponsor_edit.text().strip()

            # Use color combo's asset data if available
            char_data = {}
            skin_data = color_combo.currentData(Qt.ItemDataRole.UserRole)
            if skin_data and skin_data.get("assets"):
                char_data = skin_data
            else:
                # Fall back to character combo data
                char_combo_data = char_combo.currentData(
                    Qt.ItemDataRole.UserRole)
                if char_combo_data:
                    char_data = char_combo_data
                else:
                    char_data = self._findCharacterForPlayer(name)

            players[str(i + 1)] = {
                "rank": rank,
                "name": name,
                "sponsor": sponsor,
                "character": char_data,
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

        for i, (rank_label, name_edit, sponsor_edit, char_combo, color_combo) in enumerate(
                self.playerInputs):
            if i < len(data):
                team = data[i]
                placement = team.get("placement", i + 1)
                rank_label.setText(str(placement) if placement else str(i + 1))

                players = team.get("players", [])
                if players:
                    player = players[0]
                    name_edit.setText(player.get("gamerTag", ""))
                    sponsor_edit.setText(player.get("prefix", "") or "")

                    # Resolve character and populate combos
                    self._setCharacterFromMains(i, player)
                else:
                    name_edit.setText("")
                    sponsor_edit.setText("")
                    char_combo.setCurrentIndex(-1)
                    color_combo.setModel(QStandardItemModel())
            else:
                rank_label.setText(str(i + 1))
                name_edit.setText("")
                sponsor_edit.setText("")
                char_combo.setCurrentIndex(-1)
                color_combo.setModel(QStandardItemModel())

        self._syncPlayersToState()
        self.GeneratePreview(True)

    def _setCharacterFromMains(self, index, playerData):
        """Set character and color combos from Start.gg mains data."""
        char_combo = self.playerInputs[index][3]
        color_combo = self.playerInputs[index][4]
        mains = playerData.get("mains", {})

        game = AutonaGameAssetManager.instance.selectedGame
        if not game or not mains:
            char_combo.setCurrentIndex(-1)
            color_combo.setModel(QStandardItemModel())
            return

        codename = game.get("codename", "")
        game_mains = mains.get(codename, [])
        if not game_mains or len(game_mains) == 0:
            char_combo.setCurrentIndex(-1)
            color_combo.setModel(QStandardItemModel())
            return

        # Take the most-used character (first in list)
        char_codename = game_mains[0][0] if game_mains[0] else None
        if not char_codename:
            char_combo.setCurrentIndex(-1)
            color_combo.setModel(QStandardItemModel())
            return

        # Set character combo selection
        self._selectCharacterInCombo(index, char_codename)
        self._loadSkinModel(index, char_codename)

    def _loadSkinModel(self, index, char_codename):
        """Load skin model into the color combo for a given character."""
        color_combo = self.playerInputs[index][4]
        color_combo.blockSignals(True)

        # Find the character's en_name to look up the skin model
        char_entry = AutonaGameAssetManager.instance.characters.get(char_codename)
        if not char_entry:
            # Try matching by codename
            for key, val in AutonaGameAssetManager.instance.characters.items():
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

    def _onCharacterChanged(self, index):
        """Called when character combo selection changes for a player row."""
        char_combo = self.playerInputs[index][3]
        color_combo = self.playerInputs[index][4]

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

    def _selectCharacterInCombo(self, index, char_codename):
        """Select a character in the combo by codename."""
        char_combo = self.playerInputs[index][3]
        char_combo.blockSignals(True)

        model = char_combo.model()
        for row in range(model.rowCount()):
            data = model.data(
                model.index(row, 0), Qt.ItemDataRole.UserRole)
            if data and data.get("codename") == char_codename:
                char_combo.setCurrentIndex(row)
                char_combo.blockSignals(False)
                return

        # Fallback: try matching by en_name or key
        char_entry = AutonaGameAssetManager.instance.characters.get(char_codename)
        if char_entry:
            en_name = char_entry.get("en_name", "")
            for row in range(model.rowCount()):
                data = model.data(
                    model.index(row, 0), Qt.ItemDataRole.UserRole)
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
        """Search scoreboard state for a player's character data."""
        if not player_name:
            return {}
        try:
            # Search across all scoreboards for the player name
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
                                # Return first character
                                first_key = next(iter(char), None)
                                if first_key:
                                    return char[first_key]
        except Exception as e:
            logger.debug(f"Character lookup failed: {e}")
        return {}

    def _selectedLayout(self):
        return self.layoutCombo.currentData() or "horizontal"

    def _maxPlayersForLayout(self):
        return 16 if self._selectedLayout() == "horizontal_16" else 8

    def _onLayoutChanged(self):
        """Update Start.gg button label and show/hide player rows to match layout."""
        n = self._maxPlayersForLayout()
        label = QApplication.translate("app", "Load top {0} from StartGG").format(n)
        self.btLoadFromStartgg.setText(label)

        for i, (rank_label, name_edit, sponsor_edit, char_combo, color_combo) in enumerate(
                self.playerInputs):
            visible = i < n
            rank_label.setVisible(visible)
            name_edit.setVisible(visible)
            sponsor_edit.setVisible(visible)
            char_combo.setVisible(visible)
            color_combo.setVisible(visible)

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
