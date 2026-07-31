from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import os
import time
from .StateManager import StateManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaGameAssetManager import AutonaGameAssetManager
from .SettingsManager import SettingsManager
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaDirHelper import AutonaResolve
from .AutonaFeatureColors import MakeTournamentColorCard
from . import ui_layout as L
from . import ui_constants as UI
import traceback
import shutil
from pathlib import Path
from loguru import logger
import requests

DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"
BANNER_PATH = "./user_data/tournament_banner.png"
LOGO_PATH = "./layout/logo.png"


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
            # Restore the persisted value BEFORE seeding the key. This loop
            # used to emit editingFinished on the still-empty .ui fields,
            # writing "" over the tournamentInfo.* keys just restored from
            # program_state.json. The sidebar tournament button
            # (Window.UpdateTournamentButton) reads tournamentName from that
            # state, so a cached tournament came up as "Set tournament" and
            # stayed there for the whole session.
            saved = StateManager.Get(
                f"tournamentInfo.{widget.objectName()}", None)
            if isinstance(saved, str):
                widget.setText(saved)
            widget.editingFinished.connect(
                lambda element=widget: [
                    StateManager.Set(
                        f"tournamentInfo.{element.objectName()}", element.text())
                ])
            widget.editingFinished.emit()

        # 表示大会名 auto-copy. displayTournamentName is its own
        # tournamentInfo.* key (not score.{N}.*) so it survives the startup
        # score-subtree wipe and is shared across every board. Two sources
        # feed it with different override semantics:
        #  - Manual edits to the "大会情報" tournamentName field (its
        #    editingFinished below) follow into the display name only while
        #    it is unmodified (empty, or still equal to the last-seen
        #    tournamentName) — mirroring the _lastAutoStreamTitle pattern.
        #  - A Start.gg tournament (re)selection is authoritative: it
        #    re-applies the Start.gg name even over a manual edit (handled
        #    in _onTournamentDataForDisplayName). The startup restore of the
        #    previous tournament (initial_load) is not a user selection and
        #    keeps manual edits.
        self.tournamentNameEdit: QLineEdit = self.findChild(
            QLineEdit, "tournamentName")
        if StateManager.Get("tournamentInfo.displayTournamentName", None) is None:
            StateManager.Set(
                "tournamentInfo.displayTournamentName",
                StateManager.Get("tournamentInfo.tournamentName", "") or "")
        self._lastTournamentName = StateManager.Get(
            "tournamentInfo.tournamentName", "") or ""

        def _onTournamentNameEditingFinished():
            new = self.tournamentNameEdit.text()
            old = self._lastTournamentName
            disp = StateManager.Get("tournamentInfo.displayTournamentName", "")
            if disp == "" or disp == old:
                StateManager.Set("tournamentInfo.displayTournamentName", new)
            self._lastTournamentName = new

        self.tournamentNameEdit.editingFinished.connect(
            _onTournamentNameEditingFinished)

        # Bound-method slot so Qt delivers the signal on the GUI thread
        # (same rationale as the tournament_data_updated hooks below).
        AutonaTournamentDataProvider.instance.signals.tournament_data_updated.connect(
            self._onTournamentDataForDisplayName)

        for widget in self.findChildren(QSpinBox):
            # Same one-way restore as the QLineEdit loop above (state -> UI),
            # done before connecting so it is a plain restore and not a write
            # back into the state we are reading.
            saved = StateManager.Get(
                f"tournamentInfo.{widget.objectName()}", None)
            if isinstance(saved, int) and not isinstance(saved, bool):
                widget.setValue(saved)
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

        # 大会カラー — the tournament-wide theme / accent every feature falls
        # back to (SettingsManager general.* / StateManager tournamentInfo.*).
        # Built in code rather than in the .ui so the color buttons stay out of
        # the objectName -> keypath auto-wiring loops above, and inserted right
        # after 大会情報 so the media group keeps the bottom of the tab.
        # Per-feature overrides live on their own panels (AutonaFeatureColors).
        contentsLayout = self._contents.layout()
        infoGroup = self.findChild(QGroupBox, "groupBox_2")
        insertAt = (contentsLayout.indexOf(infoGroup) + 1) if infoGroup \
            else contentsLayout.count()
        contentsLayout.insertWidget(insertAt, MakeTournamentColorCard())

        # Stream destination / title / thumbnail generation are not
        # tournament-scoped — they belong to the broadcast and now live in
        # their own 配信設定 panel (AutonaStreamSettingsWidget). This tab keeps
        # only what a tournament owns: its metadata and its icon / banner.

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

    def _onTournamentDataForDisplayName(self, data):
        # A Start.gg tournament (re)selection re-applies its name to the
        # 表示大会名 even over a manual edit — picking a tournament is an
        # explicit "use this one" action. Skipped for the startup restore
        # of the previous tournament (initial_load) and for the empty
        # payload emitted when the tournament is cleared, neither of which
        # is a user selection.
        name = data.get("tournamentName", "") or ""
        if not name or data.get("initial_load"):
            return
        StateManager.Set("tournamentInfo.displayTournamentName", name)
        # Keep the follow-guard's bookkeeping consistent with the state we
        # just wrote, whatever order the editingFinished hook runs in.
        self._lastTournamentName = name

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
