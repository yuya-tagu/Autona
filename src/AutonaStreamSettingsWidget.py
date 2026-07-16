from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import os
import datetime
from io import BytesIO
from .StateManager import StateManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaGameAssetManager import AutonaGameAssetManager
from .SettingsManager import SettingsManager
from .Helpers.AutonaDirHelper import AutonaResolve
from . import ui_layout as L
from . import ui_constants as UI
import traceback
from pathlib import Path
from loguru import logger
import requests
from PIL import Image, ImageFilter, ImageEnhance

# Tournament-scoped assets, owned by the EVENT tab — read here only as
# thumbnail material.
DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"
BANNER_PATH = "./user_data/tournament_banner.png"

# Stream-org-scoped assets, owned by this panel. They live under ./layout/
# (served statically by AutonaWebServer, same as the tournament logo) so OBS
# overlays can fetch them by URL with the published version as a cache-bust.
ORG_ICON_PATH = "./layout/stream_org_icon.png"
ORG_THUMB_PATH = "./layout/stream_org_thumbnail.png"

STREAM_OUTPUT_DIR = "./out/stream"
STREAM_TITLE_MAX_LENGTH = 100
# Fallback abbreviation by smashgg_game_id when the per-game config.json
# does not specify one (games not installed in user_data/games/)
GAME_ABBREVIATION_FALLBACK = {
    1386: "SSBU",   # Super Smash Bros. Ultimate
    43868: "SF6",   # Street Fighter 6
}


class AutonaStreamSettingsWidget(QDockWidget):
    """配信設定 — everything scoped to the broadcast, not to the tournament.

    Stream destination, stream title, the stream org's own icon / thumbnail,
    and the YouTube thumbnail generator that consumes them. Kept apart from
    the EVENT tab (tournament-scoped: name, date, venue, tournament icon /
    banner) and from the SCORE tab (match-scoped, and gated behind 反映) so
    each panel owns one level of the data hierarchy.

    State lives under the `stream.*` subtree — ungated and board-agnostic.
    """

    def __init__(self, *args):
        super().__init__(*args)

        uic.loadUi(AutonaResolve("src/layout/AutonaStreamSettings.ui"), self)

        # Wrap the dock contents in a QScrollArea so the panel doesn't force
        # the whole window to grow vertically (same as the EVENT tab).
        self._contents = self.widget()
        self._contents.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._contents)
        self.setWidget(scroll)

        # Drop residual keys from the pre-配信設定 layouts so an upgraded
        # program_state.json doesn't keep broadcasting them:
        #  - tournamentInfo.streamTitleEdit: the stream title used to live on
        #    the EVENT tab, where the generic "every QLineEdit becomes
        #    tournamentInfo.{objectName}" loop published it. Nothing read it.
        #  - tournamentInfo.streamUrl: the destination's previous home.
        # Guarded so a fresh install doesn't fire two no-op save cycles at
        # startup (Unset queues a changed key unconditionally).
        for stale in ("tournamentInfo.streamTitleEdit", "tournamentInfo.streamUrl"):
            if StateManager.Get(stale, None) is not None:
                StateManager.Unset(stale)

        self._buildStreamDestination()
        self._buildStreamTitle()
        self._buildOrgMedia()
        self._buildStreamThumbnail()

        # Normalise card padding / spacing across all top-level groups.
        for group in self.findChildren(QGroupBox):
            L.apply_card_layout(group)

    # ---- 配信先 ----

    def _buildStreamDestination(self):
        self.streamUrlCombo: QComboBox = self.findChild(
            QComboBox, "streamUrlCombo")
        self.btEditStreamChannel: QPushButton = self.findChild(
            QPushButton, "btEditStreamChannel")
        self.btAddStreamChannel: QPushButton = self.findChild(
            QPushButton, "btAddStreamChannel")

        self.btEditStreamChannel.clicked.connect(self._editStreamChannel)
        self.btAddStreamChannel.clicked.connect(self._addStreamChannel)

        # Populate and wire the combo only once every widget the handlers
        # touch exists — _onStreamChannelChanged reaches for the edit button.
        self._loadStreamChannels()
        self.streamUrlCombo.currentIndexChanged.connect(
            self._onStreamChannelChanged)
        self.streamUrlCombo.lineEdit().editingFinished.connect(
            self._onStreamChannelChanged)

        # Restore the saved selection rather than exporting the combo's empty
        # default — stream.url is persisted in program_state.json, so the
        # destination outlives the session.
        self._syncStreamDestinationCombo()
        self._updateEditStreamChannelBtn()
        StateManager.signals.state_big_change.connect(
            self._syncStreamDestinationCombo)

    def _loadStreamChannels(self):
        """Populate the stream URL combo from the saved channels."""
        self.streamUrlCombo.blockSignals(True)
        self.streamUrlCombo.clear()
        self.streamUrlCombo.addItem("", "")  # empty default
        channels = SettingsManager.Get("stream_channels", [])
        for ch in channels:
            self.streamUrlCombo.addItem(ch.get("name", ""), ch.get("url", ""))
        self.streamUrlCombo.blockSignals(False)

    def _currentStreamUrl(self):
        # Row 0 carries empty data, so a free-typed URL falls through to the
        # combo's edit text.
        return self.streamUrlCombo.currentData() or self.streamUrlCombo.currentText()

    def _onStreamChannelChanged(self, *args):
        url = self._currentStreamUrl()
        if StateManager.Get("stream.url", None) != url:
            StateManager.Set("stream.url", url)
        self._updateEditStreamChannelBtn()

    def _updateEditStreamChannelBtn(self):
        # Editing only applies to a saved channel — row 0 is the empty
        # default and free-typed text has no saved entry behind it.
        self.btEditStreamChannel.setEnabled(
            self.streamUrlCombo.currentIndex() > 0)

    def _syncStreamDestinationCombo(self):
        """Point the combo at the persisted stream.url without re-emitting."""
        url = StateManager.Get("stream.url", "") or ""
        combo = self.streamUrlCombo
        # Don't fight an edit in progress. The combo is editable, so its line
        # edit is the focus proxy and hasFocus() covers both.
        if combo.hasFocus() or self._currentStreamUrl() == url:
            return
        combo.blockSignals(True)
        try:
            index = combo.findData(url)
            if index >= 0:
                combo.setCurrentIndex(index)
                # setCurrentIndex is a no-op when that row is already current,
                # which would strand stale free-typed text in the line edit.
                combo.setEditText(combo.itemText(index))
            else:
                # A URL typed straight into the combo has no saved row.
                combo.setCurrentIndex(0)
                combo.setEditText(url)
        finally:
            combo.blockSignals(False)
        self._updateEditStreamChannelBtn()

    def _promptStreamChannel(self, title, name="", url="", allowDelete=False):
        """Show the name/URL form dialog shared by the add and edit flows.
        Returns ("save", name, url), ("delete", None, None), or None when
        cancelled."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(350)
        layout = QFormLayout(dialog)

        nameEdit = QLineEdit(name)
        nameEdit.setPlaceholderText("BST Stream")
        layout.addRow(QApplication.translate("app", "Channel name"), nameEdit)

        urlEdit = QLineEdit(url)
        urlEdit.setPlaceholderText("https://www.youtube.com/@B_ST_Stream/live")
        layout.addRow("URL", urlEdit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        result = {"action": None}

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
            result["action"] = "save"
            dialog.accept()

        buttons.accepted.connect(tryAccept)
        buttons.rejected.connect(dialog.reject)

        if allowDelete:
            # DestructiveRole emits neither accepted nor rejected, so the
            # confirm handler below fully owns closing the dialog.
            deleteBtn = buttons.addButton(
                QApplication.translate("app", "Delete"),
                QDialogButtonBox.ButtonRole.DestructiveRole)

            def onDelete():
                reply = QMessageBox.question(
                    dialog,
                    QApplication.translate("app", "Delete stream channel"),
                    QApplication.translate(
                        "app", "Delete this stream channel?"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    result["action"] = "delete"
                    dialog.accept()

            deleteBtn.clicked.connect(onDelete)

        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted or result["action"] is None:
            return None
        if result["action"] == "delete":
            return ("delete", None, None)
        return ("save", nameEdit.text().strip(), urlEdit.text().strip())

    def _addStreamChannel(self):
        result = self._promptStreamChannel(
            QApplication.translate("app", "Add stream channel"))
        if result is None:
            return
        _, name, url = result
        channels = SettingsManager.Get("stream_channels", [])
        channels.append({"name": name, "url": url})
        SettingsManager.Set("stream_channels", channels)
        self._loadStreamChannels()
        # Select the newly added channel (emits, exporting the URL).
        self.streamUrlCombo.setCurrentIndex(self.streamUrlCombo.count() - 1)

    def _editStreamChannel(self):
        idx = self.streamUrlCombo.currentIndex() - 1  # row 0 is the empty default
        channels = SettingsManager.Get("stream_channels", [])
        if not (0 <= idx < len(channels)):
            return
        result = self._promptStreamChannel(
            QApplication.translate("app", "Edit stream channel"),
            name=channels[idx].get("name", ""),
            url=channels[idx].get("url", ""),
            allowDelete=True)
        if result is None:
            return
        action, name, url = result
        if action == "delete":
            channels.pop(idx)
            SettingsManager.Set("stream_channels", channels)
            self._loadStreamChannels()
            # The reload leaves row 0 current without emitting, so clear the
            # line edit by hand and push the empty URL out explicitly.
            self.streamUrlCombo.blockSignals(True)
            self.streamUrlCombo.setCurrentIndex(0)
            self.streamUrlCombo.setEditText("")
            self.streamUrlCombo.blockSignals(False)
            self._onStreamChannelChanged()
        else:
            channels[idx] = {"name": name, "url": url}
            SettingsManager.Set("stream_channels", channels)
            self._loadStreamChannels()
            # Re-select the edited channel (emits and re-exports the URL).
            self.streamUrlCombo.setCurrentIndex(idx + 1)

    # ---- 配信タイトル ----

    def _buildStreamTitle(self):
        self.streamTitleEdit: QLineEdit = self.findChild(
            QLineEdit, "streamTitleEdit")
        self.btCopyStreamTitle: QPushButton = self.findChild(
            QPushButton, "btCopyStreamTitle")
        self.btCopyStreamTitle.clicked.connect(self.CopyStreamTitle)

        self.streamTitleEdit.editingFinished.connect(
            lambda: StateManager.Set(
                "stream.title", self.streamTitleEdit.text()))

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
            StateManager.Set("stream.title", new_title)
        self._lastAutoStreamTitle = new_title

    def CopyStreamTitle(self):
        title = self.streamTitleEdit.text().strip()
        if not title:
            return
        QApplication.clipboard().setText(title)

    # ---- 配信団体アイコン / サムネイル ----

    def _buildOrgMedia(self):
        self.orgIconView: QLabel = self.findChild(QLabel, "orgIconView")
        L.apply_media_preview(self.orgIconView)
        self.orgThumbView: QLabel = self.findChild(QLabel, "orgThumbView")
        L.apply_media_preview(self.orgThumbView)

        self.btLoadOrgIcon: QPushButton = self.findChild(
            QPushButton, "btLoadOrgIcon")
        self.btLoadOrgIcon.clicked.connect(self.LoadOrgIcon)

        self.btLoadOrgThumb: QPushButton = self.findChild(
            QPushButton, "btLoadOrgThumb")
        self.btLoadOrgThumb.clicked.connect(self.LoadOrgThumbnail)

        # Tagged danger: it discards both registered assets at once. Unlike
        # the tournament icon / banner there is no bundled default to fall
        # back to — the org assets are opt-in, so reset means "unregister".
        self.btResetOrgMedia: QPushButton = self.findChild(
            QPushButton, "btResetOrgMedia")
        L.tag_button(self.btResetOrgMedia, L.BUTTON_DANGER)
        self.btResetOrgMedia.clicked.connect(self.ResetOrgMedia)

        self.UpdateOrgIcon()
        self.UpdateOrgThumbnail()

    @staticmethod
    def _publishAssetVersion(key: str, path: str):
        """Publish an asset file's mtime as a cache-bust version.

        Mirrors tournamentInfo.logoVersion: OBS overlays append it as a query
        string to bypass the Cache-Control: max-age served by AutonaWebServer.
        mtime keeps the value stable across restarts when the file hasn't
        changed, so overlays don't re-fetch every launch. 0 means "no asset".
        """
        try:
            version = int(os.path.getmtime(path) * 1000)
        except OSError:
            version = 0
        if StateManager.Get(key, None) != version:
            StateManager.Set(key, version)

    def _LoadImageInto(self, path: str, max_dim: int) -> bool:
        """Prompt for an image and save it to `path`, scaled to fit max_dim.
        Returns True when a file was saved."""
        try:
            fileName = QFileDialog.getOpenFileName(
                self,
                QApplication.translate("app", "Open Image"),
                ".",
                QApplication.translate(
                    "app", "Image Files") + " (*.png *.jpg *.bmp)"
            )[0]
            if not fileName:
                return False
            pix = QPixmap(fileName)
            if pix.isNull():
                return False
            if pix.width() > max_dim or pix.height() > max_dim:
                pix = pix.scaled(
                    max_dim, max_dim,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            return bool(pix.save(path))
        except Exception:
            logger.error(traceback.format_exc())
            return False

    def LoadOrgIcon(self):
        # Same 512px cap as the tournament icon: oversized sources choke the
        # off-screen QWebEngineView used by the thumbnail generator.
        if self._LoadImageInto(ORG_ICON_PATH, 512):
            self.UpdateOrgIcon()

    def LoadOrgThumbnail(self):
        # 1920 keeps a full-HD thumbnail source usable without re-encoding.
        if self._LoadImageInto(ORG_THUMB_PATH, 1920):
            self.UpdateOrgThumbnail()

    def UpdateOrgIcon(self):
        pix = QPixmap(ORG_ICON_PATH) if os.path.exists(ORG_ICON_PATH) else QPixmap()
        if pix.isNull():
            self.orgIconView.setPixmap(QPixmap())
            self.orgIconView.setText(QApplication.translate("app", "未登録"))
        else:
            self.orgIconView.setText("")
            self.orgIconView.setPixmap(pix.scaled(
                60, 60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self._publishAssetVersion("stream.orgIconVersion", ORG_ICON_PATH)

    def UpdateOrgThumbnail(self):
        pix = QPixmap(ORG_THUMB_PATH) if os.path.exists(ORG_THUMB_PATH) else QPixmap()
        if pix.isNull():
            self.orgThumbView.setPixmap(QPixmap())
            self.orgThumbView.setText(QApplication.translate("app", "未登録"))
        else:
            self.orgThumbView.setText("")
            self.orgThumbView.setPixmap(pix.scaled(
                160, 90,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self._publishAssetVersion("stream.orgThumbVersion", ORG_THUMB_PATH)

    def ResetOrgMedia(self):
        reply = QMessageBox.question(
            self,
            QApplication.translate("app", "Confirm"),
            QApplication.translate(
                "app", "登録した配信団体のアイコンとサムネイルを削除します。よろしいですか？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        for path in (ORG_ICON_PATH, ORG_THUMB_PATH):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                logger.error(traceback.format_exc())
        self.UpdateOrgIcon()
        self.UpdateOrgThumbnail()
        # The org thumbnail is gone; fall back to the tournament banner so
        # generation doesn't silently produce nothing.
        if self.rbStreamThumbSourceOrg.isChecked():
            self.rbStreamThumbSourceBanner.setChecked(True)

    # ---- 配信サムネイル生成 ----

    def _buildStreamThumbnail(self):
        self.rbStreamThumbSourceBanner: QRadioButton = self.findChild(
            QRadioButton, "rbStreamThumbSourceBanner")
        self.rbStreamThumbSourceOrg: QRadioButton = self.findChild(
            QRadioButton, "rbStreamThumbSourceOrg")
        self.rbStreamThumbCenter: QRadioButton = self.findChild(
            QRadioButton, "rbStreamThumbCenter")
        self.rbStreamThumbNetflix: QRadioButton = self.findChild(
            QRadioButton, "rbStreamThumbNetflix")
        self.cbStreamThumbOverlayOrgIcon: QCheckBox = self.findChild(
            QCheckBox, "cbStreamThumbOverlayOrgIcon")

        self.btGenerateStreamThumbnail: QPushButton = self.findChild(
            QPushButton, "btGenerateStreamThumbnail")
        L.tag_button(self.btGenerateStreamThumbnail, L.BUTTON_SECONDARY)
        self.btGenerateStreamThumbnail.clicked.connect(
            self.GenerateStreamThumbnail)

        self.btCopyStreamThumbnail: QPushButton = self.findChild(
            QPushButton, "btCopyStreamThumbnail")
        self.btCopyStreamThumbnail.clicked.connect(self.CopyStreamThumbnail)
        # Tracks the most recent generated thumbnail file so the copy
        # button can push it to the clipboard without regenerating.
        self._lastStreamThumbPath = None

        self.streamThumbPreview: QLabel = self.findChild(
            QLabel, "streamThumbPreview")
        # The .ui pins this slot to 160×90 via maximumSize — don't pass
        # min_* here or Qt resolves min>max in favour of min and the
        # label balloons to the requested minimum.
        L.apply_media_preview(self.streamThumbPreview)
        self.streamThumbPath: QLabel = self.findChild(QLabel, "streamThumbPath")

    def _LoadOrgThumbImage(self):
        """Load the registered org thumbnail as a PIL Image, or None."""
        if not os.path.exists(ORG_THUMB_PATH):
            return None
        try:
            return Image.open(ORG_THUMB_PATH).convert("RGB")
        except Exception:
            logger.error(traceback.format_exc())
            return None

    def _LoadBannerImage(self):
        """Load the source banner as a PIL Image. Prefers the provider's
        remote banner when a tournament is connected, otherwise falls back
        to the locally saved banner (set via the EVENT tab). Returns None
        when nothing usable is available."""
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

        # 2) Fall back to the locally saved banner so manual file loads and
        #    offline / non-Start.gg flows still work.
        for candidate in (BANNER_PATH, DEFAULT_BANNER_PATH):
            if candidate and os.path.exists(candidate):
                try:
                    return Image.open(candidate).convert("RGB")
                except Exception:
                    logger.error(traceback.format_exc())
        return None

    def GenerateStreamThumbnail(self):
        if self.rbStreamThumbSourceOrg.isChecked():
            source = self._LoadOrgThumbImage()
            if source is None:
                self.streamThumbPath.setText(QApplication.translate(
                    "app", "配信団体サムネイルが登録されていません。"))
                return
            source_tag = "org"
        else:
            source = self._LoadBannerImage()
            if source is None:
                self.streamThumbPath.setText(QApplication.translate(
                    "app", "No tournament banner available."))
                return
            source_tag = "banner"

        try:
            if self.rbStreamThumbNetflix.isChecked():
                out_img = self._MakeNetflixStyleThumbnail(source)
                style_tag = "netflix"
            else:
                out_img = self._MakeCenterCropThumbnail(source)
                style_tag = "center"

            if self.cbStreamThumbOverlayOrgIcon.isChecked():
                out_img = self._OverlayOrgIcon(out_img)

            out_dir = Path(STREAM_OUTPUT_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"thumbnail_{source_tag}_{style_tag}_{timestamp}.jpg"
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

    def _OverlayOrgIcon(self, img):
        """Composite the registered org icon into the bottom-right corner.
        No-op when nothing is registered."""
        if not os.path.exists(ORG_ICON_PATH):
            return img
        try:
            icon = Image.open(ORG_ICON_PATH).convert("RGBA")
        except Exception:
            logger.error(traceback.format_exc())
            return img
        # 12% of the canvas height, inset by half that — big enough to read
        # at YouTube's grid size without covering the artwork.
        target_h = int(img.height * 0.12)
        scale = target_h / icon.height
        icon = icon.resize(
            (max(1, int(icon.width * scale)), max(1, target_h)), Image.LANCZOS)
        margin = target_h // 2
        pos = (img.width - icon.width - margin, img.height - icon.height - margin)
        # Paste with the icon's own alpha as the mask so PNG transparency
        # doesn't come out as a black box.
        img.paste(icon, pos, icon)
        return img

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
