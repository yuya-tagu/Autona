from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import os
import time
import datetime
from io import BytesIO
from .StateManager import StateManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaGameAssetManager import AutonaGameAssetManager
from .SettingsManager import SettingsManager
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaDirHelper import AutonaResolve
from . import ui_layout as L
from . import ui_constants as UI
import traceback
import shutil
from pathlib import Path
from loguru import logger
import requests
from PIL import Image, ImageFilter, ImageEnhance

DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"
BANNER_PATH = "./user_data/tournament_banner.png"
LOGO_PATH = "./layout/logo.png"
STREAM_OUTPUT_DIR = "./out/stream"
STREAM_TITLE_MAX_LENGTH = 100
# Fallback abbreviation by smashgg_game_id when the per-game config.json
# does not specify one (games not installed in user_data/games/)
GAME_ABBREVIATION_FALLBACK = {
    1386: "SSBU",   # Super Smash Bros. Ultimate
    43868: "SF6",   # Street Fighter 6
}


class AutonaTournamentInfoWidget(QDockWidget):
    def __init__(self, *args):
        super().__init__(*args)

        uic.loadUi(AutonaResolve("src/layout/AutonaTournamentInfo.ui"), self)

        # Wrap the dock contents in a QScrollArea so the tab doesn't
        # force the whole window to grow vertically.
        self._contents = self.widget()
        self._contents.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._contents)
        self.setWidget(scroll)

        AutonaTournamentDataProvider.instance.signals.tournament_data_updated.connect(
            self.UpdateData)

        for widget in self.findChildren(QDateEdit):
            widget.dateChanged.connect(lambda value, element=widget: [
                StateManager.Set(
                    f"tournamentInfo.{element.objectName()}", value.toString(QLocale(AutonaLocaleHelper.exportLocale).dateFormat(QLocale.FormatType.ShortFormat)))
            ])
            # widget.setDate(d)
            # widget.setValue(StateManager.Get(
            #    f"tournamentInfo.{widget.objectName()}", 0))

        for widget in self.findChildren(QLineEdit):
            if widget.objectName() == "qt_spinbox_lineedit":
                continue
            if isinstance(widget.parent(), QComboBox):
                continue
            widget.editingFinished.connect(
                lambda element=widget: [
                    StateManager.Set(
                        f"tournamentInfo.{element.objectName()}", element.text())
                ])
            widget.editingFinished.emit()

        for widget in self.findChildren(QSpinBox):
            widget.valueChanged.connect(lambda value, element=widget: [
                StateManager.Set(
                    f"tournamentInfo.{element.objectName()}", value)
            ])

        # Game title combo
        self.gameTitleCombo: QComboBox = self.findChild(
            QComboBox, "gameTitleCombo")
        self.gameTitleCombo.setEditable(True)
        self.gameTitleCombo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.gameTitleCombo.completer().setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        self.gameTitleCombo.activated.connect(self._OnGameSelected)
        AutonaGameAssetManager.instance.signals.onLoadAssets.connect(
            self._PopulateGameCombo)
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._SyncGameCombo)
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            self._SyncGameCombo)

        self.btLoadInfoFromTournament: QPushButton = self.findChild(
            QPushButton, "btLoadInfoFromTournament")
        # This single button now both selects the tournament and loads
        # everything from it, so override the .ui label ("Load from
        # tournament") with the "Set tournament" string. Reusing the existing
        # (context "app", "Set tournament") translation avoids touching the
        # .ts/.qm files. objectName is left untouched for findChild stability.
        self.btLoadInfoFromTournament.setText(
            QApplication.translate("app", "Set tournament"))
        self.btLoadInfoFromTournament.setIcon(
            QIcon(UI.icon_path('startgg.svg')))
        L.tag_button(self.btLoadInfoFromTournament, L.BUTTON_PRIMARY)
        self.btLoadInfoFromTournament.clicked.connect(self._OnSelectTournament)

        self.btClearInfo: QPushButton = self.findChild(
            QPushButton, "btClearInfo")
        L.tag_button(self.btClearInfo, L.BUTTON_DANGER)
        self.btClearInfo.clicked.connect(self._OnClearInfo)

        # Tournament Icon / Banner are now grouped under groupBoxMedia
        # (see AutonaTournamentInfo.ui). Five buttons sit beside the two
        # stacked previews: 4 explicit load/download actions + 1 unified
        # reset. The previous per-asset Default buttons (btClearIcon /
        # btClearBanner) were folded into btResetMediaToDefault.
        self.btLoadIconFile: QPushButton = self.findChild(
            QPushButton, "btLoadIconFile")
        self.btLoadIconFile.clicked.connect(lambda: [
            self.LoadIcon()
        ])

        self.btDownloadIcon: QPushButton = self.findChild(
            QPushButton, "btDownloadIcon")
        self.btDownloadIcon.clicked.connect(lambda: [
            self.DownloadIcon()
        ])

        self.iconView: QLabel = self.findChild(QLabel, "iconView")
        L.apply_media_preview(self.iconView)

        self.UpdateIcon()

        # Banner UI
        self.btLoadBannerFile: QPushButton = self.findChild(
            QPushButton, "btLoadBannerFile")
        self.btLoadBannerFile.clicked.connect(self.LoadBannerFile)

        self.btDownloadBanner: QPushButton = self.findChild(
            QPushButton, "btDownloadBanner")
        self.btDownloadBanner.clicked.connect(self.DownloadBanner)

        self.bannerView: QLabel = self.findChild(QLabel, "bannerView")
        L.apply_media_preview(self.bannerView)

        self.UpdateBanner()

        # Unified reset: restore both icon and banner to Autona defaults.
        # Tagged danger because it discards both saved assets at once.
        self.btResetMediaToDefault: QPushButton = self.findChild(
            QPushButton, "btResetMediaToDefault")
        L.tag_button(self.btResetMediaToDefault, L.BUTTON_DANGER)
        self.btResetMediaToDefault.clicked.connect(lambda: [
            self.SetDefaultIcon(),
            self.SetDefaultBanner(),
        ])

        # Normalise card padding / spacing across all top-level groups so
        # the EVENT tab reads consistently with the rest of the app.
        for group in self.findChildren(QGroupBox):
            L.apply_card_layout(group)

        # Theme / accent color editing moved to the BOARD tab
        # (AutonaBoardWidget). The SettingsManager / StateManager keys are
        # unchanged, so overlays and out/ export are unaffected.

        # Stream settings
        # Stream destination ("配信先") — moved here from the SCORE tab so the
        # broadcast channel is configured once at the event level. The selected
        # URL is mirrored to every scoreboard's score.{N}.stream_url, so the
        # per-board X-post builder keeps working unchanged.
        self._buildStreamDestinationRow()

        self.streamTitleEdit: QLineEdit = self.findChild(
            QLineEdit, "streamTitleEdit")

        self.btCopyStreamTitle: QPushButton = self.findChild(
            QPushButton, "btCopyStreamTitle")
        self.btCopyStreamTitle.clicked.connect(self.CopyStreamTitle)

        self.rbStreamThumbCenter: QRadioButton = self.findChild(
            QRadioButton, "rbStreamThumbCenter")
        self.rbStreamThumbNetflix: QRadioButton = self.findChild(
            QRadioButton, "rbStreamThumbNetflix")

        self.btGenerateStreamThumbnail: QPushButton = self.findChild(
            QPushButton, "btGenerateStreamThumbnail")
        L.tag_button(self.btGenerateStreamThumbnail, L.BUTTON_SECONDARY)
        self.btGenerateStreamThumbnail.clicked.connect(
            self.GenerateStreamThumbnail)

        self.btCopyStreamThumbnail: QPushButton = self.findChild(
            QPushButton, "btCopyStreamThumbnail")
        self.btCopyStreamThumbnail.clicked.connect(
            self.CopyStreamThumbnail)
        # Tracks the most recent generated thumbnail file so the copy
        # button can push it to the clipboard without regenerating.
        self._lastStreamThumbPath = None

        self.streamThumbPreview: QLabel = self.findChild(
            QLabel, "streamThumbPreview")
        # The .ui pins this slot to 160×90 via maximumSize — don't pass
        # min_* here or Qt resolves min>max in favour of min and the
        # label balloons to the requested minimum.
        L.apply_media_preview(self.streamThumbPreview)
        self.streamThumbPath: QLabel = self.findChild(
            QLabel, "streamThumbPath")

        # Tracks the last auto-generated title; used to detect whether the
        # user has manually edited the field so auto-refreshes don't clobber
        # their edits.
        self._lastAutoStreamTitle = ""

        # Auto-refresh the title whenever tournament info, entrants, or game
        # assets change.
        # Bound-method slot so Qt delivers the signal on the GUI thread
        # (a lambda would run on the emitting worker thread and touch
        # widgets there).
        AutonaTournamentDataProvider.instance.signals.tournament_data_updated.connect(
            self._onTournamentDataUpdated)
        AutonaTournamentDataProvider.instance.signals.entrants_updated.connect(
            self._RefreshStreamTitle)
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._RefreshStreamTitle)

        self._RefreshStreamTitle()

    def _PopulateGameCombo(self):
        games = AutonaGameAssetManager.instance.games
        self.gameTitleCombo.clear()
        self.gameTitleCombo.addItem("", 0)
        for i, (codename, game) in enumerate(games.items()):
            logo_path = game.get("logo_path")
            icon = QIcon(QPixmap(QImage(logo_path).scaled(
                32, 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))) if logo_path else QIcon()
            name = game.get("name") or codename
            self.gameTitleCombo.addItem(icon, name, i + 1)
        self.gameTitleCombo.setIconSize(QSize(32, 32))
        self._SyncGameCombo()

    def _SyncGameCombo(self):
        selected = AutonaGameAssetManager.instance.selectedGame
        if not selected:
            self.gameTitleCombo.setCurrentIndex(0)
            return
        name = selected.get("name") or selected.get("codename")
        for i in range(self.gameTitleCombo.count()):
            if self.gameTitleCombo.itemText(i) == name:
                self.gameTitleCombo.setCurrentIndex(i)
                return
        self.gameTitleCombo.setCurrentIndex(0)

    def _OnGameSelected(self, index):
        data = self.gameTitleCombo.itemData(index)
        if data:
            AutonaGameAssetManager.instance.LoadGameAssets(data)

    def _OnClearInfo(self):
        reply = QMessageBox.question(
            self,
            QApplication.translate("app", "Confirm"),
            QApplication.translate(
                "app", "Start.gg との接続を解除します。よろしいですか？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        AutonaTournamentDataProvider.instance.SetTournament(None)
        self.UpdateData({})

    def _emitMediaUpdated(self):
        AutonaTournamentDataProvider.instance.signals.media_updated.emit()

    def _OnSelectTournament(self):
        # Open the tournament-select dialog (prefilled with the current URL,
        # forces a re-fetch of tournament data/phases/entrants). On accept,
        # also pull the icon and banner so this one button loads everything
        # from the tournament.
        #
        # The icon + banner downloads each fire two synchronous HTTP
        # requests (GraphQL for the URL, then GET for the image). Run
        # the whole chain on a worker so the GUI thread stays
        # responsive, and disable the button while it's in flight so a
        # frustrated repeat-click doesn't queue up another download
        # cycle and freeze the app harder.
        if getattr(self, "_loadInProgress", False):
            return

        accepted = AutonaTournamentDataProvider.instance.SetStartggEventSlug(
            self.window())
        if not accepted:
            return
        if AutonaTournamentDataProvider.instance.provider is None:
            return

        self._loadInProgress = True
        self.btLoadInfoFromTournament.setEnabled(False)

        self._DownloadIconAndBannerAsync(
            on_finished=self._finishLoadFromTournament)

    def _finishLoadFromTournament(self):
        self._loadInProgress = False
        self.btLoadInfoFromTournament.setEnabled(True)

    @staticmethod
    def _imageHeaders():
        return {
            "client-version": "20",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
        }

    def _DownloadIconAndBannerAsync(self, on_finished=None):
        from .Workers import Worker
        from .Helpers.AutonaStatusManager import AutonaStatusManager

        status_token = AutonaStatusManager.push(
            "大会アイコン / バナーを取得中...")

        def _fetch(progress_callback=None, cancel_event=None):
            provider = AutonaTournamentDataProvider.instance.provider
            iconBytes = bannerBytes = None
            if provider is None:
                return (None, None)
            try:
                iconUrl = provider.GetIconURL()
                if iconUrl:
                    iconBytes = requests.get(
                        iconUrl, headers=self._imageHeaders(), timeout=20).content
            except Exception:
                logger.error(traceback.format_exc())
            try:
                bannerUrl = provider.GetBannerURL()
                if bannerUrl:
                    bannerBytes = requests.get(
                        bannerUrl, headers=self._imageHeaders(), timeout=20).content
            except Exception:
                logger.error(traceback.format_exc())
            return (iconBytes, bannerBytes)

        def _apply(result):
            try:
                iconBytes, bannerBytes = result if result else (None, None)
                if iconBytes:
                    try:
                        self._applyIconBytes(iconBytes)
                    except Exception:
                        logger.error(traceback.format_exc())
                if bannerBytes:
                    try:
                        self._applyBannerBytes(bannerBytes)
                    except Exception:
                        logger.error(traceback.format_exc())
            finally:
                AutonaStatusManager.pop(status_token)
                if on_finished:
                    on_finished()

        def _onError(_err):
            AutonaStatusManager.pop(status_token)
            if on_finished:
                on_finished()

        worker = Worker(_fetch)
        worker.signals.result.connect(_apply)
        worker.signals.error.connect(_onError)
        AutonaTournamentDataProvider.instance.threadPool.start(worker)

    def _applyIconBytes(self, data: bytes):
        # GUI thread: convert bytes → QPixmap, scale, persist, refresh label.
        pix = QPixmap()
        pix.loadFromData(data)
        max_dim = 512
        if pix.width() > max_dim or pix.height() > max_dim:
            pix = pix.scaled(
                max_dim, max_dim,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        pix.save(LOGO_PATH)
        self.UpdateIcon()

    def _applyBannerBytes(self, data: bytes):
        Path("./user_data").mkdir(parents=True, exist_ok=True)
        pix = QPixmap()
        pix.loadFromData(data)
        pix.save(BANNER_PATH)
        # The banner is now consumed by the thumbnail only when the user picks
        # "Use tournament banner" in Thumbnail Settings — no longer written into
        # thumbnail_config.background_path here (that key is the custom image).
        logger.info("Tournament banner saved")
        self.UpdateBanner()
        self._emitMediaUpdated()

    def UpdateIcon(self):
        # iconView slot is now 60×60 (see AutonaTournamentInfo.ui).
        # Scale the source to fit the slot while keeping aspect.
        try:
            icon = QPixmap(LOGO_PATH).scaled(
                60, 60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        except:
            icon = QPixmap("./assets/icons/cancel.svg").scaledToWidth(32)

        self.iconView.setPixmap(icon)

        # Publish the logo file's mtime as a cache-bust version so the OBS
        # browser source and the off-screen WebEngine renderer (thumbnail /
        # result) can append it as a query string and bypass the
        # Cache-Control: max-age=86400 served by AutonaWebServer.
        # Using mtime keeps the value stable across app restarts when the
        # logo file hasn't changed (avoids re-fetching every launch).
        try:
            mtime = int(os.path.getmtime(LOGO_PATH) * 1000)
        except OSError:
            mtime = 0
        if StateManager.Get("tournamentInfo.logoVersion", None) != mtime:
            StateManager.Set("tournamentInfo.logoVersion", mtime)

    def LoadIcon(self):
        try:
            fileName = QFileDialog.getOpenFileName(
                self,
                QApplication.translate("app", "Open Image"),
                ".",
                QApplication.translate(
                    "app", "Image Files")+" (*.png *.jpg *.bmp)"
            )[0]

            if fileName != None:
                pix = QPixmap(fileName)
                max_dim = 512
                if pix.width() > max_dim or pix.height() > max_dim:
                    pix = pix.scaled(
                        max_dim, max_dim,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                pix.save(LOGO_PATH)
        except Exception as e:
            logger.error(traceback.format_exc())

        self.UpdateIcon()

    def DownloadIcon(self):
        try:
            url = AutonaTournamentDataProvider.instance.provider.GetIconURL()

            logger.info(f"Icon URL: {url}")

            headers = {
                "client-version": "20",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36"
            }

            response = requests.get(url, headers=headers)
            data = response.content

            pix = QPixmap()
            pix.loadFromData(data)

            # Scale down large icons to prevent rendering issues
            # in the off-screen QWebEngineView (thumbnail generator).
            max_dim = 512
            if pix.width() > max_dim or pix.height() > max_dim:
                pix = pix.scaled(
                    max_dim, max_dim,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)

            pix.save(LOGO_PATH)
        except Exception as e:
            logger.error(traceback.format_exc())

        self.UpdateIcon()

        # Also download tournament banner for thumbnail background
        self.DownloadBanner()

    def UpdateBanner(self):
        # bannerView slot is now 160×90 (see AutonaTournamentInfo.ui).
        # Scale source to fit while keeping aspect. Shows the saved tournament
        # banner, falling back to the bundled default when none is saved.
        bg_path = BANNER_PATH if os.path.exists(BANNER_PATH) else DEFAULT_BANNER_PATH
        try:
            pix = QPixmap(bg_path)
            if pix.isNull():
                pix = QPixmap(DEFAULT_BANNER_PATH)
            banner = pix.scaled(
                160, 90,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        except:
            banner = QPixmap(DEFAULT_BANNER_PATH).scaled(
                160, 90,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        self.bannerView.setPixmap(banner)

    def LoadBannerFile(self):
        try:
            fileName = QFileDialog.getOpenFileName(
                self,
                QApplication.translate("app", "Open Image"),
                ".",
                QApplication.translate("app", "Image Files") + " (*.png *.jpg *.bmp)"
            )[0]

            if fileName:
                Path("./user_data").mkdir(parents=True, exist_ok=True)
                pix = QPixmap(fileName)
                pix.save(BANNER_PATH)
        except Exception as e:
            logger.error(traceback.format_exc())

        self.UpdateBanner()
        self._emitMediaUpdated()

    def DownloadBanner(self):
        try:
            url = AutonaTournamentDataProvider.instance.provider.GetBannerURL()
            if not url:
                logger.info("No tournament banner available")
                return

            logger.info(f"Banner URL: {url}")

            headers = {
                "client-version": "20",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36"
            }

            response = requests.get(url, headers=headers)

            Path("./user_data").mkdir(parents=True, exist_ok=True)

            pix = QPixmap()
            pix.loadFromData(response.content)
            pix.save(BANNER_PATH)
            logger.info("Tournament banner saved")
        except Exception as e:
            logger.error(traceback.format_exc())

        self.UpdateBanner()
        self._emitMediaUpdated()

    def SetDefaultBanner(self):
        # Reset the banner to the bundled default by removing the saved file;
        # UpdateBanner and the thumbnail's "Use tournament banner" both fall
        # back to DEFAULT_BANNER_PATH when BANNER_PATH is absent.
        try:
            if os.path.exists(BANNER_PATH):
                os.remove(BANNER_PATH)
        except:
            logger.error(traceback.format_exc())

        self.UpdateBanner()
        self._emitMediaUpdated()

    def SetDefaultIcon(self):
        try:
            shutil.copy("./assets/icons/icon.png", LOGO_PATH)
        except:
            logger.error(traceback.format_exc())

        self.UpdateIcon()

    # ---- Stream title / thumbnail ----

    def _GetGameAbbreviation(self):
        game = StateManager.Get("game", {}) or {}
        abbrev = game.get("abbreviation")
        if abbrev:
            return abbrev
        smashgg_id = game.get("smashgg_id")
        try:
            return GAME_ABBREVIATION_FALLBACK.get(int(smashgg_id))
        except (TypeError, ValueError):
            return None

    def _GenerateStreamTitle(self, top_entrants):
        tournament_name = StateManager.Get("tournamentInfo.tournamentName", "") or ""
        abbrev = self._GetGameAbbreviation()
        suffix = f" - {abbrev}" if abbrev else ""

        names = [e["gamerTag"] for e in top_entrants if e.get("gamerTag")]

        # Reduce the number of player names from the tail until the title fits
        # within STREAM_TITLE_MAX_LENGTH. Keep "ft. ... and more!" framing
        # while any names remain.
        while names:
            candidate = f"{tournament_name} ft. " + \
                " ".join(names) + " ... and more!" + suffix
            if len(candidate) <= STREAM_TITLE_MAX_LENGTH:
                return candidate
            names.pop()

        # No names fit; drop the ft. framing entirely
        return f"{tournament_name}{suffix}".strip()

    def _onTournamentDataUpdated(self, _data):
        self._RefreshStreamTitle()

    def _RefreshStreamTitle(self):
        """Recompute the auto-generated stream title and apply it unless the
        user has manually edited the field."""
        provider = AutonaTournamentDataProvider.instance.provider
        top_entrants = getattr(provider, "top_entrants", []) if provider else []
        top_entrants = top_entrants or []

        has_valid_seed = any(
            e.get("seed", 999999) < 999999 for e in top_entrants)

        if top_entrants and has_valid_seed:
            new_title = self._GenerateStreamTitle(top_entrants)
        else:
            # Fallback title when entrants or seeds aren't ready yet.
            # The "waiting for entrants" / "seeds not finalized" hints
            # used to render here; the footer status bar covers progress
            # surfacing so we no longer duplicate that inline.
            name = StateManager.Get("tournamentInfo.tournamentName", "") or ""
            abbrev = self._GetGameAbbreviation()
            suffix = f" - {abbrev}" if abbrev else ""
            new_title = f"{name}{suffix}".strip()

        # Preserve user's manual edits: only overwrite when the field still
        # matches the last auto-generated value (or is empty).
        current = self.streamTitleEdit.text()
        if current == self._lastAutoStreamTitle or not current:
            self.streamTitleEdit.setText(new_title)
        self._lastAutoStreamTitle = new_title

    def CopyStreamTitle(self):
        title = self.streamTitleEdit.text().strip()
        if not title:
            return
        QApplication.clipboard().setText(title)

    # --- Stream destination ("配信先") ---
    # Scoreboards whose score.{N}.stream_url is consumed by the X-post builder
    # (board 3 = crew battle does not use it).
    _STREAM_BOARDS = (1, 2)

    def _buildStreamDestinationRow(self):
        """Build the 配信先 (stream destination) row and insert it at the top
        of the EVENT tab's Stream Settings group, above the stream title.
        """
        self.streamUrlCombo = None

        streamLayout = self.findChild(QVBoxLayout, "verticalLayoutStream")
        if streamLayout is None:
            return

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(L.CARD_INNER_SPACING)

        label = QLabel(QApplication.translate("app", "配信先"))
        row.addWidget(label)

        self.streamUrlCombo = QComboBox()
        self.streamUrlCombo.setEditable(True)
        self.streamUrlCombo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._loadStreamChannels()
        self.streamUrlCombo.currentIndexChanged.connect(self._onStreamChannelChanged)
        self.streamUrlCombo.lineEdit().editingFinished.connect(
            self._onStreamChannelChanged)
        row.addWidget(self.streamUrlCombo)

        self.addStreamChannelBtn = QPushButton(
            QApplication.translate("app", "配信先を追加する"))
        self.addStreamChannelBtn.setSizePolicy(
            QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.addStreamChannelBtn.clicked.connect(self._addStreamChannel)
        row.addWidget(self.addStreamChannelBtn)

        # Insert above the "Stream Title" label (index 0 of the group layout).
        streamLayout.insertLayout(0, row)

        # Export the initial value to every consuming board.
        self._onStreamChannelChanged()

    def _loadStreamChannels(self):
        """Populate the stream URL combo from saved channels."""
        self.streamUrlCombo.blockSignals(True)
        self.streamUrlCombo.clear()
        self.streamUrlCombo.addItem("", "")  # empty default
        channels = SettingsManager.Get("stream_channels", [])
        for ch in channels:
            self.streamUrlCombo.addItem(ch.get("name", ""), ch.get("url", ""))
        self.streamUrlCombo.blockSignals(False)

    def _onStreamChannelChanged(self, *args):
        url = self.streamUrlCombo.currentData() or self.streamUrlCombo.currentText()
        # Mirror to every board so the per-board post builders read it unchanged.
        with StateManager.SaveBlock():
            for n in self._STREAM_BOARDS:
                StateManager.Set(f"score.{n}.stream_url", url)

    def _addStreamChannel(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(QApplication.translate("app", "Add stream channel"))
        dialog.setMinimumWidth(350)
        layout = QFormLayout(dialog)

        nameEdit = QLineEdit()
        nameEdit.setPlaceholderText("BST Stream")
        layout.addRow(QApplication.translate("app", "Channel name"), nameEdit)

        urlEdit = QLineEdit()
        urlEdit.setPlaceholderText("https://www.youtube.com/@B_ST_Stream/live")
        layout.addRow("URL", urlEdit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        def tryAccept():
            # Reject empty input with a warning instead of silently discarding
            # the entry after the dialog closes.
            if not nameEdit.text().strip() or not urlEdit.text().strip():
                QMessageBox.warning(
                    dialog,
                    QApplication.translate("app", "Input required"),
                    QApplication.translate(
                        "app", "Please enter both the channel name and URL."))
                return
            dialog.accept()

        buttons.accepted.connect(tryAccept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = nameEdit.text().strip()
            url = urlEdit.text().strip()
            channels = SettingsManager.Get("stream_channels", [])
            channels.append({"name": name, "url": url})
            SettingsManager.Set("stream_channels", channels)
            self._loadStreamChannels()
            # Select the newly added channel
            self.streamUrlCombo.setCurrentIndex(self.streamUrlCombo.count() - 1)

    def _LoadBannerImage(self):
        """Load the source banner as a PIL Image. Prefers the provider's
        remote banner when a tournament is connected, otherwise falls back
        to the locally saved banner (set via LoadBannerFile / DownloadBanner
        / default). Returns None when nothing usable is available."""
        # 1) Try the live tournament banner first — keeps behaviour
        #    identical for users who already loaded a Start.gg tournament.
        provider = AutonaTournamentDataProvider.instance.provider
        if provider is not None:
            try:
                url = provider.GetBannerURL()
            except Exception:
                logger.error(traceback.format_exc())
                url = None
            if url:
                try:
                    headers = {
                        "client-version": "20",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36"
                    }
                    response = requests.get(url, headers=headers, timeout=20)
                    response.raise_for_status()
                    return Image.open(BytesIO(response.content)).convert("RGB")
                except Exception:
                    logger.error(traceback.format_exc())

        # 2) Fall back to the locally saved banner so manual file loads
        #    (LoadBannerFile) and offline / non-Start.gg flows still work.
        for candidate in (BANNER_PATH, DEFAULT_BANNER_PATH):
            if candidate and os.path.exists(candidate):
                try:
                    return Image.open(candidate).convert("RGB")
                except Exception:
                    logger.error(traceback.format_exc())
        return None

    def GenerateStreamThumbnail(self):
        banner = self._LoadBannerImage()
        if banner is None:
            self.streamThumbPath.setText(QApplication.translate(
                "app", "No tournament banner available."))
            return

        try:
            if self.rbStreamThumbNetflix.isChecked():
                out_img = self._MakeNetflixStyleThumbnail(banner)
                style_tag = "netflix"
            else:
                out_img = self._MakeCenterCropThumbnail(banner)
                style_tag = "center"

            out_dir = Path(STREAM_OUTPUT_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"thumbnail_{style_tag}_{timestamp}.jpg"
            out_path = out_dir / filename
            out_img.save(out_path, "JPEG", quality=90)
            self._lastStreamThumbPath = str(out_path)

            # Show a scaled preview in the UI
            pix = QPixmap(str(out_path))
            if not pix.isNull():
                label_size = self.streamThumbPreview.size()
                self.streamThumbPreview.setPixmap(pix.scaled(
                    label_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))

            self.streamThumbPath.setText(
                QApplication.translate("app", "Saved: ") + str(out_path))
        except Exception as e:
            logger.error(traceback.format_exc())
            self.streamThumbPath.setText(
                QApplication.translate("app", "Error: ") + str(e))

    def CopyStreamThumbnail(self):
        """Copy the most recently generated stream thumbnail to the
        clipboard. Generates one on demand if none exists yet."""
        path = self._lastStreamThumbPath
        if not path or not os.path.exists(path):
            self.GenerateStreamThumbnail()
            path = self._lastStreamThumbPath
        if not path or not os.path.exists(path):
            return
        try:
            image = QImage(path)
            if image.isNull():
                self.streamThumbPath.setText(QApplication.translate(
                    "app", "Failed to load thumbnail for copy."))
                return
            QApplication.clipboard().setImage(image)
            self.streamThumbPath.setText(
                QApplication.translate("app", "Copied to clipboard: ") + path)
        except Exception as e:
            logger.error(traceback.format_exc())
            self.streamThumbPath.setText(
                QApplication.translate("app", "Error: ") + str(e))

    def _MakeCenterCropThumbnail(self, img):
        # Crop center to 16:9, then resize to 1920x1080
        w, h = img.size
        target_ratio = 16 / 9
        current_ratio = w / h
        if current_ratio > target_ratio:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            img = img.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            img = img.crop((0, top, w, top + new_h))
        return img.resize((1920, 1080), Image.LANCZOS)

    def _MakeNetflixStyleThumbnail(self, img):
        # Blurred, darkened, center-cropped banner as background
        bg = self._MakeCenterCropThumbnail(img.copy())
        bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
        bg = ImageEnhance.Brightness(bg).enhance(0.6)

        # Foreground: original banner scaled to fit within 1920x1080
        w, h = img.size
        scale = min(1920 / w, 1080 / h)
        fw, fh = int(w * scale), int(h * scale)
        fg = img.resize((fw, fh), Image.LANCZOS)
        bg.paste(fg, ((1920 - fw) // 2, (1080 - fh) // 2))
        return bg

    def DisplayErrorMessage(self, message):
        msgBox = QMessageBox()
        msgBox.setWindowIcon(QIcon('assets/icons/icon.png'))
        msgBox.setWindowTitle(QApplication.translate("app", "Error"))
        msgBox.setText(QApplication.translate("app", "Error"))
        msgBox.setInformativeText(message)
        msgBox.setIcon(QMessageBox.Critical)
        msgBox.exec()

    def UpdateData(self, data):
        logger.info("Tournament Data: "+ str(data))

        if not data.get("initial_load"):
            for widget in self.findChildren(QLineEdit):
                if isinstance(widget.parent(), QComboBox):
                    continue
                widget.setText("")
                widget.editingFinished.emit()
            for widget in self.findChildren(QSpinBox):
                widget.setValue(0)

        for key in data.keys():
            try:
                widget: QWidget = self.findChild(QWidget, key)

                if widget and data[key] is not None:
                    if type(widget) == QLineEdit:
                        widget.setText(data[key])
                        widget.editingFinished.emit()

                    if type(widget) == QSpinBox:
                        widget.setValue(data[key])

                    if type(widget) == QDateEdit:
                        # This is how to put a date into QDateEdit Element using a timestamp
                        #        aTime = '2018-07-24 19:24:11.272000'
                        unix_timestamp = float(data[key])
                        #        tmToDate = datetime.datetime.utcfromtimestamp(unix_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                        #        utc_time = time.gmtime(unix_timestamp)
                        local_datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(
                            unix_timestamp))  # converts timestampt to local time and apply the format
                        local_date = local_datetime.split(
                            " ")[0]  # get only the date
                        # Convert date into QDate object
                        d = QDate.fromString(local_date, "yyyy-MM-dd")
                        widget.setDate(d)
                else:
                    exceptions = ["endAt", "eventStartAt", "eventEndAt"]
                    info = data.get(key)

                    if key in exceptions and data.get(key) != "":
                        unix_timestamp = float(data[key])
                        local_datetime = time.strftime("%x", time.localtime(
                            unix_timestamp))
                        info = local_datetime
                    
                    StateManager.Set(f"tournamentInfo.{key}", info)
            except:
                logger.error(traceback.format_exc())
                SettingsManager.Set("TOURNAMENT_URL", '')
                error_message = QApplication.translate("app", "The tournament URL could not be loaded.") + "\n" + QApplication.translate(
                    "app", "Make sure that your tournament URL is correctly formatted and points to an existing event, and try again.")
                self.DisplayErrorMessage(error_message)
                break
