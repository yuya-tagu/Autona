from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import os
from pathlib import Path
from .core.StateManager import StateManager
from .StateManagerBridge import StateManagerBridge
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaGameAssetManager import AutonaGameAssetManager
from .core import StateSchema as schema
from .core import TournamentMediaService as media
from .core.TournamentInfoService import TournamentInfoService
from .core.TournamentMediaService import TournamentMediaService
from .Helpers.AutonaDirHelper import AutonaResolve
from .Helpers.AutonaStatusManager import AutonaStatusManager
from .AutonaFeatureColors import MakeTournamentColorCard
from . import ui_layout as L
from . import ui_constants as UI
from loguru import logger

# The files the previews show. Read only, to draw them: writing them is
# core/TournamentMediaService.py's (ADR-005 S4f).
DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"
BANNER_PATH = media.MEDIA[media.BANNER][0]
LOGO_PATH = media.MEDIA[media.ICON][0]

_LOGO_VERSION_PATH = media.MEDIA[media.ICON][1]
_BANNER_VERSION_PATH = media.MEDIA[media.BANNER][1]


class AutonaTournamentInfoWidget(QDockWidget):
    """The EVENT tab: the tournament's metadata, icon and banner.

    Since headless core Step 4b this panel no longer *owns*
    ``tournamentInfo.*``. A loaded tournament is applied by
    ``core/TournamentInfoService``, which is wired from ``bootstrap_core()``
    / ``Window.__init__`` and therefore runs whether or not this page has
    ever been revealed -- which is what let the page become lazy
    (startup-architecture-redesign.md §3).

    Since ADR-005 S4f it only asks core and draws the state:

    - **out**: a human editing a field calls
      ``TournamentInfoService.Set{Text,Number}Field`` / ``SetDate``; the
      tournament is set / cleared through ``core/TournamentService`` (the
      URL dialog and the confirmation are the provider adapter's, shared
      with the sidebar); the icon / banner are imported, fetched and reset
      by ``core/TournamentMediaService``; a game is picked by its codename;
    - **in**: ``_SyncFieldsFromState`` renders the state into the fields
      (the date from ``tournamentInfo.startAtDate``, which survives a
      restart), and ``_RenderMedia`` the previews (by their versions) and
      the fetch (``tournamentInfo.media``).
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
        # The write direction only. The field names come from StateSchema and
        # are used *as* the findChild keys; nothing here reads an objectName
        # back to build a key path with (S-14, headless core Step 5). The
        # restore that used to sit in these loops is _SyncFieldsFromState()
        # at the end of __init__ -- one place, and one that keeps working
        # when the state changes later.
        #
        # Each list is (field name, widget) so the render pass below has the
        # key without asking the widget for its name.
        #
        # 表示大会名 rides along with the tournament name rather than having
        # its own connection here: `SetTextField` applies the follow rule
        # (the weaker of the two -- a manual rename carries into the display
        # name only while it has not been customised), which is what the
        # provider path has always done too.
        self._dateFields = []
        self._textFields = []
        self._numberFields = []

        for field in schema.TOURNAMENT_TEXT_FIELDS:
            widget = self._input(field, QLineEdit)
            if widget is None:
                continue
            self._textFields.append((field, widget))
            widget.editingFinished.connect(
                lambda field=field, widget=widget:
                    TournamentInfoService.instance.SetTextField(
                        field, widget.text()))

        for field in schema.TOURNAMENT_NUMBER_FIELDS:
            widget = self._input(field, QSpinBox)
            if widget is None:
                continue
            self._numberFields.append((field, widget))
            widget.valueChanged.connect(
                lambda value, field=field:
                    TournamentInfoService.instance.SetNumberField(
                        field, value))

        # The day, as YYYY-MM-DD: its timestamp (local midnight) is core's
        # (ADR-005 S4f). The .ui's minimum (2000-01-01) is SetDate's too.
        for field in schema.TOURNAMENT_DATE_FIELDS:
            widget = self._input(field, QDateEdit)
            if widget is None:
                continue
            self._dateFields.append((field, widget))
            widget.dateChanged.connect(
                lambda date, field=field:
                    TournamentInfoService.instance.SetDate(
                        field, date.toString("yyyy-MM-dd")))

        # Game title combo: each item carries its game's codename, which is
        # what a game is loaded by (the v1 POST /game's key; the position in
        # the scan used to be, and a rescan could move it).
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
        # download + 1 unified reset.
        self.btLoadIconFile: QPushButton = self.findChild(
            QPushButton, "btLoadIconFile")
        self.btLoadIconFile.clicked.connect(self.LoadIcon)

        self.iconView: QLabel = self.findChild(QLabel, "iconView")
        L.apply_media_preview(self.iconView)

        self.btLoadBannerFile: QPushButton = self.findChild(
            QPushButton, "btLoadBannerFile")
        self.btLoadBannerFile.clicked.connect(self.LoadBannerFile)

        self.bannerView: QLabel = self.findChild(QLabel, "bannerView")
        L.apply_media_preview(self.bannerView)

        # Unified download: icon and banner always come from the same
        # tournament, so they are fetched in one job (core's, which also
        # runs after the "Set tournament" button above).
        self.btDownloadMedia: QPushButton = self.findChild(
            QPushButton, "btDownloadMedia")
        L.tag_button(self.btDownloadMedia, L.BUTTON_PRIMARY)
        self.btDownloadMedia.clicked.connect(self._OnDownloadMedia)

        # Unified reset: restore both icon and banner to Autona defaults.
        # Tagged danger because it discards both saved assets at once.
        self.btResetMediaToDefault: QPushButton = self.findChild(
            QPushButton, "btResetMediaToDefault")
        L.tag_button(self.btResetMediaToDefault, L.BUTTON_DANGER)
        self.btResetMediaToDefault.clicked.connect(self._OnResetMedia)

        # What the media previews / the fetch last rendered (None: never),
        # and the footer status shown while a fetch runs.
        self._renderedMedia = None
        self._mediaRenderQueued = False
        self._mediaStatusToken = None

        # Normalise card padding / spacing across all top-level groups so
        # the EVENT tab reads consistently with the rest of the app.
        for group in self.findChildren(QGroupBox):
            L.apply_card_layout(group)

        # 大会カラー — the tournament-wide theme / accent every feature falls
        # back to (SettingsManager general.* / StateManager tournamentInfo.*).
        # Built in code rather than in the .ui (originally so the color
        # buttons stayed out of the findChildren keypath loops this tab used
        # to run; the card is shared with the six per-feature panels now),
        # and inserted right after 大会情報 so the media group keeps the bottom
        # of the tab. The card writes through its own service —
        # core/ColorService.SetTournamentColor owns tournamentInfo.themeColor
        # / accentColor, which is the FeatureColors instalment of Step 5, not
        # this one; TournamentInfoService never touches those two. Per-feature
        # overrides live on their own panels.
        contentsLayout = self._contents.layout()
        infoGroup = self.findChild(QGroupBox, "groupBox_2")
        insertAt = (contentsLayout.indexOf(infoGroup) + 1) if infoGroup \
            else contentsLayout.count()
        contentsLayout.insertWidget(insertAt, MakeTournamentColorCard())

        # Stream destination / title / thumbnail generation are not
        # tournament-scoped — they belong to the broadcast and now live in
        # their own 配信設定 panel (the React Stream page). This tab keeps
        # only what a tournament owns: its metadata and its icon / banner.

        # Render whatever the state already holds (this page is lazy, so a
        # tournament may well have been loaded before it existed), and keep
        # following it. tournamentInfo.* is not held, so edits arrive as
        # ordinary deltas; state_big_change covers wholesale loads.
        StateManagerBridge.instance.state_updated.connect(self._onStateDelta)
        StateManagerBridge.instance.state_big_change.connect(
            self._SyncFieldsFromState)
        self._SyncFieldsFromState()
        self._RenderMedia()

    def _input(self, field, cls):
        """The tab's widget for one schema field, or None with a loud log.

        The schema name and the .ui objectName have to be the same string
        (tests/test_objectname_keypath_contract.py pins that they are). If
        they ever stop being, the field would silently go unwired and its
        key would never be written again -- so say so rather than skip
        quietly.
        """
        widget = self.findChild(cls, field)
        if widget is None:
            logger.error(
                f"AutonaTournamentInfo.ui is missing {cls.__name__} "
                f"{field!r}; tournamentInfo.{field} will never be written")
        return widget

    def _onStateDelta(self, delta):
        # Same cheap containment filter the scoreboard uses on deltas.
        if "tournamentInfo" in str(delta):
            self._SyncFieldsFromState()

    def _SyncFieldsFromState(self):
        """Render tournamentInfo.* into this tab's inputs.

        Writes nothing — that is what makes it safe on every delta. Each
        widget is set inside a QSignalBlocker so the handler that would fire
        (the SetTextField / SetNumberField / SetDate call wired above)
        cannot echo the value back. For the number and date fields this
        closes a real echo: QSpinBox.setValue() and QDateEdit.setDate() emit
        even when set programmatically. For the text fields it is defensive
        rather than closing a live path -- QLineEdit.setText() does not
        itself emit editingFinished (only Return/focus-out do) -- but the
        blocker is kept so the loops read the same either way. A field the
        operator has focus in is left alone so a notification cannot yank
        text out from under the caret.

        The media (previews, fetch) are drawn by _RenderMedia, queued to the
        event loop: this runs inside StateManager.lock when the write was
        made on the GUI thread (agent-pitfalls #17), and a preview reads an
        image file.
        """
        service = TournamentInfoService.instance
        for field, widget in self._textFields:
            value = StateManager.Get(
                service.Key(field), None)
            if (isinstance(value, str) and widget.text() != value
                    and not widget.hasFocus()):
                with QSignalBlocker(widget):
                    widget.setText(value)

        for field, widget in self._numberFields:
            value = StateManager.Get(
                service.Key(field), None)
            if (isinstance(value, int) and not isinstance(value, bool)
                    and widget.value() != value and not widget.hasFocus()):
                with QSignalBlocker(widget):
                    widget.setValue(value)

        # The day the state holds beside the formatted date (ADR-005 S4f:
        # tournamentInfo.startAtDate, so a restart keeps it too). Reached
        # unguarded, like AutonaActions.instance and
        # StateManagerBridge.instance elsewhere in this layer: bootstrap_core()
        # creates it before any widget exists, and a null check here would
        # only turn a broken bootstrap into a panel that silently renders
        # nothing.
        for field, widget in self._dateFields:
            day = service.DateOf(field)
            if day is None or widget.hasFocus():
                continue
            date = QDate.fromString(day, "yyyy-MM-dd")
            if date.isValid() and widget.date() != date:
                with QSignalBlocker(widget):
                    widget.setDate(date)

        if self._MediaShown() != self._renderedMedia \
                and not self._mediaRenderQueued:
            self._mediaRenderQueued = True
            QTimer.singleShot(0, self, self._RenderMedia)

    # ── the icon / banner ────────────────────────────────────────────────

    @staticmethod
    def _MediaShown():
        """What the media part of the tab draws: the two versions and the
        fetch's status."""
        fetch = StateManager.Get(media.MEDIA_PATH, None)
        status = fetch.get("status") if isinstance(fetch, dict) else None
        return (StateManager.Get(_LOGO_VERSION_PATH, 0),
                StateManager.Get(_BANNER_VERSION_PATH, 0),
                status)

    def _RenderMedia(self):
        """Draw the previews (when a version moved) and the fetch: its
        buttons are disabled while it runs, and the footer says so. Out of
        StateManager.lock (queued, or from __init__)."""
        self._mediaRenderQueued = False
        shown = self._MediaShown()
        rendered = self._renderedMedia
        self._renderedMedia = shown
        logoVersion, bannerVersion, status = shown
        if rendered is None or rendered[0] != logoVersion:
            self.UpdateIcon()
        if rendered is None or rendered[1] != bannerVersion:
            self.UpdateBanner()

        running = status == media.RUNNING
        self.btDownloadMedia.setEnabled(not running)
        self.btLoadInfoFromTournament.setEnabled(not running)
        if running and self._mediaStatusToken is None:
            self._mediaStatusToken = AutonaStatusManager.push(
                "大会アイコン / バナーを取得中...")
        elif not running and self._mediaStatusToken is not None:
            AutonaStatusManager.pop(self._mediaStatusToken)
            self._mediaStatusToken = None

    def UpdateIcon(self):
        # iconView slot is now 60×60 (see AutonaTournamentInfo.ui).
        # Scale the source to fit the slot while keeping aspect. The
        # version (tournamentInfo.logoVersion) is core's to publish.
        icon = QPixmap(LOGO_PATH)
        if icon.isNull():
            icon = QPixmap("./assets/icons/cancel.svg").scaledToWidth(32)
        else:
            icon = icon.scaled(
                60, 60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        self.iconView.setPixmap(icon)

    def UpdateBanner(self):
        # bannerView slot is now 160×90 (see AutonaTournamentInfo.ui).
        # Scale source to fit while keeping aspect. Shows the saved tournament
        # banner, falling back to the bundled default when none is saved.
        pix = QPixmap(BANNER_PATH) if os.path.exists(BANNER_PATH) \
            else QPixmap()
        if pix.isNull():
            pix = QPixmap(DEFAULT_BANNER_PATH)
        self.bannerView.setPixmap(pix.scaled(
            160, 90,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def LoadIcon(self):
        self._ImportFromFile(media.ICON)

    def LoadBannerFile(self):
        self._ImportFromFile(media.BANNER)

    def _ImportFromFile(self, kind):
        """Pick an image and hand its bytes to core, which checks, scales
        and saves it (the previews follow its version)."""
        fileName = QFileDialog.getOpenFileName(
            self,
            QApplication.translate("app", "Open Image"),
            ".",
            QApplication.translate("app", "Image Files")
            + " (*.png *.jpg *.bmp)"
        )[0]
        if not fileName:
            return
        try:
            data = Path(fileName).read_bytes()
        except OSError:
            logger.opt(exception=True).error(f"could not read {fileName}")
            data = b""
        result = TournamentMediaService.instance.ImportMedia(kind, data)
        if result == media.IMPORTED:
            return
        if result == media.SAVE_FAILED:
            message = QApplication.translate(
                "app", "画像を保存できませんでした。ほかのアプリがファイルを開いていないか確認してください。")
        else:
            message = QApplication.translate(
                "app", "画像を読み込めませんでした。PNG / JPEG / BMP（5000 万画素まで）の画像を選んでください。")
        QMessageBox.warning(
            self, QApplication.translate("app", "Error"), message)

    def _OnDownloadMedia(self):
        # The button is disabled while a fetch runs (_RenderMedia); core
        # answers BUSY anyway.
        if TournamentMediaService.instance.FetchMedia() == \
                media.NO_TOURNAMENT:
            logger.info("No tournament loaded, nothing to download")

    def _OnResetMedia(self):
        TournamentMediaService.instance.ResetMedia()

    # ── the game ─────────────────────────────────────────────────────────

    def _PopulateGameCombo(self):
        games = AutonaGameAssetManager.instance.games
        self.gameTitleCombo.clear()
        self.gameTitleCombo.addItem("", "")
        for codename, game in games.items():
            logo_path = game.get("logo_path")
            icon = QIcon(QPixmap(QImage(logo_path).scaled(
                32, 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))) if logo_path else QIcon()
            name = game.get("name") or codename
            self.gameTitleCombo.addItem(icon, name, codename)
        self.gameTitleCombo.setIconSize(QSize(32, 32))
        self._SyncGameCombo()

    def _SyncGameCombo(self):
        selected = AutonaGameAssetManager.instance.selectedGame
        codename = selected.get("codename") if selected else None
        index = self.gameTitleCombo.findData(codename) if codename else -1
        self.gameTitleCombo.setCurrentIndex(max(index, 0))

    def _OnGameSelected(self, index):
        codename = self.gameTitleCombo.itemData(index)
        if codename:
            # By codename, as /api/v1's POST /game loads it; the loaded
            # game picked again is not reloaded (the catalog skips it).
            AutonaGameAssetManager.instance.LoadGameByCodename(codename)

    # ── the tournament ───────────────────────────────────────────────────

    def _OnClearInfo(self):
        # Asks first; core empties the fields and forgets the selected game
        # (the sidebar's "Unset tournament" is the same call).
        AutonaTournamentDataProvider.ConfirmClearTournament(self)

    def _OnSelectTournament(self):
        # The tournament-select dialog (prefilled with the current URL,
        # forces a re-fetch of tournament data/phases/entrants). Accepted,
        # core also fetches the icon and banner once the tournament is
        # loaded, so this one button loads everything from the tournament;
        # the fetch is a job, drawn from tournamentInfo.media.
        AutonaTournamentDataProvider.instance.SetStartggEventSlug(
            self.window(), fetchMedia=True)
