from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import os
import time
from .StateManager import StateManager
from .StateManagerBridge import StateManagerBridge
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaGameAssetManager import AutonaGameAssetManager
from .core.TournamentInfoService import (
    TournamentInfoService, TOURNAMENT_LOGO_PATH)
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
# Aliased rather than respelled: core/TournamentInfoService publishes this
# file's mtime as tournamentInfo.logoVersion whether or not this panel is
# ever built, so the two must be the same path.
LOGO_PATH = TOURNAMENT_LOGO_PATH


class AutonaTournamentInfoWidget(QDockWidget):
    """The EVENT tab: the tournament's metadata, icon and banner.

    Since headless core Step 4b this panel no longer *owns*
    ``tournamentInfo.*``. A loaded tournament is applied by
    ``core/TournamentInfoService``, which is wired from ``bootstrap_core()``
    / ``Window.__init__`` and therefore runs whether or not this page has
    ever been revealed -- which is what let the page become lazy
    (startup-architecture-redesign.md §3).

    Two directions remain here, and only two:

    - **out**: a human editing a field still writes
      ``tournamentInfo.{objectName}`` through the historical binding
      (known-smells S-14; migrating it is Step 5);
    - **in**: ``_SyncFieldsFromState`` renders the state into the fields, so
      a panel built after the tournament was loaded shows it, and a name
      changed elsewhere (the service, another panel) appears here.
    """

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

        # ── EVENT tab fields ─────────────────────────────────────────────
        # The write direction only: every input still writes
        # tournamentInfo.{objectName} when a human edits it (S-14, Step 5).
        # The restore that used to sit in these loops is
        # _SyncFieldsFromState() at the end of __init__ -- one place, and one
        # that keeps working when the state changes later.
        self._dateFields = []
        self._textFields = []
        self._numberFields = []

        for widget in self.findChildren(QDateEdit):
            self._dateFields.append(widget)
            widget.dateChanged.connect(lambda value, element=widget: [
                StateManager.Set(
                    f"tournamentInfo.{element.objectName()}", value.toString(QLocale(AutonaLocaleHelper.exportLocale).dateFormat(QLocale.FormatType.ShortFormat)))
            ])

        for widget in self.findChildren(QLineEdit):
            # Qt's own line edit inside a QSpinBox, and an editable combo's:
            # neither carries a key of its own (S-14 fallback guard).
            if widget.objectName() == "qt_spinbox_lineedit":
                continue
            if isinstance(widget.parent(), QComboBox):
                continue
            self._textFields.append(widget)
            widget.editingFinished.connect(
                lambda element=widget: [
                    StateManager.Set(
                        f"tournamentInfo.{element.objectName()}", element.text())
                ])

        for widget in self.findChildren(QSpinBox):
            self._numberFields.append(widget)
            widget.valueChanged.connect(lambda value, element=widget: [
                StateManager.Set(
                    f"tournamentInfo.{element.objectName()}", value)
            ])

        # 表示大会名 auto-copy. displayTournamentName is its own
        # tournamentInfo.* key (not score.{N}.*) so it survives the startup
        # score-subtree wipe and is shared across every board. Two sources
        # feed it with different override semantics, and both rules -- plus
        # the "last tournament name we saw" bookkeeping they share -- live in
        # core/TournamentInfoService now. What is connected here is only the
        # weaker one: a manual edit of the 大会情報 name field follows into
        # the display name while it has not been customised. Connected after
        # the loop above so the name is already written when it runs.
        self.tournamentNameEdit: QLineEdit = self.findChild(
            QLineEdit, "tournamentName")
        self.tournamentNameEdit.editingFinished.connect(
            lambda: TournamentInfoService.instance.FollowDisplayName(
                self.tournamentNameEdit.text()))

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
        # Catch up on the asset load that already happened. The combo used to
        # be filled by the onLoadAssets broadcast above, which this panel was
        # always built in time for; now that it is lazy (headless core Step
        # 4b) the games are loaded long before it exists and the combo would
        # come up empty. Same catch-up the other lazy factories do.
        self._PopulateGameCombo()

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
        # (see AutonaTournamentInfo.ui). Four buttons sit beside the two
        # stacked previews: 2 per-asset local-file loads + 1 unified
        # download + 1 unified reset. The previous per-asset Default buttons
        # (btClearIcon / btClearBanner) were folded into
        # btResetMediaToDefault, and the per-asset download buttons
        # (btDownloadIcon / btDownloadBanner) into btDownloadMedia.
        self.btLoadIconFile: QPushButton = self.findChild(
            QPushButton, "btLoadIconFile")
        self.btLoadIconFile.clicked.connect(lambda: [
            self.LoadIcon()
        ])

        self.iconView: QLabel = self.findChild(QLabel, "iconView")
        L.apply_media_preview(self.iconView)

        self.UpdateIcon()

        # Banner UI
        self.btLoadBannerFile: QPushButton = self.findChild(
            QPushButton, "btLoadBannerFile")
        self.btLoadBannerFile.clicked.connect(self.LoadBannerFile)

        self.bannerView: QLabel = self.findChild(QLabel, "bannerView")
        L.apply_media_preview(self.bannerView)

        self.UpdateBanner()

        # Unified download: icon and banner always come from the same
        # tournament, so they are fetched in one shot by the shared async
        # helper (also used by "load from tournament").
        self.btDownloadMedia: QPushButton = self.findChild(
            QPushButton, "btDownloadMedia")
        L.tag_button(self.btDownloadMedia, L.BUTTON_PRIMARY)
        self.btDownloadMedia.clicked.connect(self._OnDownloadMedia)

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

        # Render whatever the state already holds (this page is lazy, so a
        # tournament may well have been loaded before it existed), and keep
        # following it. tournamentInfo.* is not held, so edits arrive as
        # ordinary deltas; state_big_change covers wholesale loads.
        StateManagerBridge.instance.state_updated.connect(self._onStateDelta)
        StateManagerBridge.instance.state_big_change.connect(
            self._SyncFieldsFromState)
        self._SyncFieldsFromState()

    def _onStateDelta(self, delta):
        # Same cheap containment filter the scoreboard uses on deltas.
        if "tournamentInfo" in str(delta):
            self._SyncFieldsFromState()

    def _SyncFieldsFromState(self):
        """Render tournamentInfo.* into this tab's inputs.

        Writes nothing — that is what makes it safe on every delta. Each
        widget is set inside a QSignalBlocker so the handler that would fire
        (the objectName-to-keypath write above) cannot echo the value back.
        For the number fields this closes a real echo: QSpinBox.setValue()
        emits valueChanged even when set programmatically. For the text
        fields it is defensive rather than closing a live path --
        QLineEdit.setText() does not itself emit editingFinished (only
        Return/focus-out do) -- but the blocker is kept so the two loops
        read the same either way. A field the operator has focus in is left
        alone so a notification cannot yank text out from under the caret.
        """
        for widget in self._textFields:
            value = StateManager.Get(
                f"tournamentInfo.{widget.objectName()}", None)
            if (isinstance(value, str) and widget.text() != value
                    and not widget.hasFocus()):
                with QSignalBlocker(widget):
                    widget.setText(value)

        for widget in self._numberFields:
            value = StateManager.Get(
                f"tournamentInfo.{widget.objectName()}", None)
            if (isinstance(value, int) and not isinstance(value, bool)
                    and widget.value() != value and not widget.hasFocus()):
                with QSignalBlocker(widget):
                    widget.setValue(value)

        # The date fields are the one thing the state cannot restore: it
        # holds the *formatted* short date, which does not parse back into a
        # QDate. The service keeps the raw timestamp it published, so a panel
        # built after the tournament was loaded still shows the right day.
        # Reached unguarded, like AutonaActions.instance and
        # StateManagerBridge.instance elsewhere in this layer: bootstrap_core()
        # creates it before any widget exists, and a null check here would
        # only turn a broken bootstrap into a panel that silently renders
        # nothing.
        service = TournamentInfoService.instance
        for widget in self._dateFields:
            raw = service.RawDate(widget.objectName())
            if raw is None or widget.hasFocus():
                continue
            try:
                localDate = time.strftime(
                    "%Y-%m-%d", time.localtime(float(raw)))
            except (TypeError, ValueError):
                continue
            date = QDate.fromString(localDate, "yyyy-MM-dd")
            if date.isValid() and widget.date() != date:
                with QSignalBlocker(widget):
                    widget.setDate(date)

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
        # SetTournament(None) emits an empty tournament_data_updated, which
        # core/TournamentInfoService clears the fields from. The explicit
        # second clear this used to do was that same pass, run twice.
        AutonaTournamentDataProvider.instance.SetTournament(None)

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

    def _OnDownloadMedia(self):
        # btDownloadMedia: icon and banner always belong to the same
        # tournament, so both are fetched in one shot. Disable the button
        # while the fetch is in flight so repeat clicks don't queue up
        # parallel workers writing the same two files.
        if getattr(self, "_downloadMediaInProgress", False):
            return
        if AutonaTournamentDataProvider.instance.provider is None:
            logger.info("No tournament loaded, nothing to download")
            return

        self._downloadMediaInProgress = True
        self.btDownloadMedia.setEnabled(False)

        def _finished():
            self._downloadMediaInProgress = False
            self.btDownloadMedia.setEnabled(True)

        self._DownloadIconAndBannerAsync(on_finished=_finished)

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
        # The publication itself is core's (it also has to happen at launch,
        # whether or not this lazy page is ever opened); this call is the
        # "the file just changed" half.
        TournamentInfoService.instance.PublishLogoVersion()

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
