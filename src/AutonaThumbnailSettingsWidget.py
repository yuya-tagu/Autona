from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
from pathlib import Path
import os
from loguru import logger
import traceback
import json

from .thumbnail import main_generate_thumbnail as thumbnail
from .StateManager import StateManager
from .SettingsManager import *
from .AutonaGameAssetManager import *
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers.AutonaDictHelper import deep_get, deep_set
from .Helpers.AutonaDirHelper import AutonaResolve
from .AutonaFeatureColors import FEATURE_THUMBNAIL, MakeFeatureColorCard
from . import ui_constants as UI
from . import ui_layout as L


# Video title format:
#   「{tournament_name} {round_name_en} | 1P({fighter1}) vs 2P({fighter2}) | {game_name}」
VIDEO_TITLE_TEMPLATE = "{tournament} {round} | 1P({fighter1}) vs 2P({fighter2}) | {game}"


class PreviewWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMinimumSize(1, 1)
        self._pixmap: QPixmap = None

    def setPixmap(self, pixmap):
        self._pixmap = pixmap
        self._updateMaxHeight()
        self.repaint()

    def resizeEvent(self, QResizeEvent):
        self._updateMaxHeight()
        self.repaint()

    def _updateMaxHeight(self):
        # Fix widget height to the pixmap aspect ratio so the wrapping scroll
        # area scrolls when the viewport is shorter than the preview.
        if self._pixmap and self._pixmap.width() > 0 and self.width() > 0:
            desired = int(self.width() * self._pixmap.height() / self._pixmap.width())
            if self.minimumHeight() != desired or self.maximumHeight() != desired:
                self.setFixedHeight(desired)

    def paintEvent(self, e):
        qp = QPainter()
        qp.begin(self)
        self.drawWidget(qp)
        qp.end()

    def drawWidget(self, qp: QPainter):
        if self._pixmap:
            pw = self._pixmap.width()
            ph = self._pixmap.height()
            if pw <= 0 or ph <= 0:
                return
            # Use target-rect drawing so the DIP coordinate system is honoured
            # regardless of the source pixmap's devicePixelRatio.
            target_w = self.width()
            target_h = int(target_w * ph / pw)
            qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            qp.drawPixmap(
                QRectF(0, 0, target_w, target_h),
                self._pixmap,
                QRectF(0, 0, pw, ph),
            )


class AutonaThumbnailSettingsWidget(QDockWidget):
    def __init__(self, *args):
        super().__init__(*args)

        if not SettingsManager.Get("thumbnail_config"):
            SettingsManager.Set("thumbnail_config", {})



        self.setWindowTitle(QApplication.translate(
            "app", "Thumbnail Settings"))
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget = QWidget()
        self.setWidget(self.widget)
        self.widget.setLayout(QVBoxLayout())
        self.widget.layout().setContentsMargins(
            UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN, UI.PANEL_MARGIN)

        self.settings = uic.loadUi(AutonaResolve("src/layout/AutonaThumbnailSettings.ui"))
        self.widget.layout().addWidget(self.settings)

        # ── Video title (YouTube upload) ─────────────────────────────
        self.videoTitleEdit: QLineEdit = self.settings.findChild(
            QLineEdit, "videoTitleEdit")
        self.videoTitleStatus: QLabel = self.settings.findChild(
            QLabel, "videoTitleStatus")
        self.btCopyVideoTitle: QPushButton = self.settings.findChild(
            QPushButton, "btCopyVideoTitle")
        self.btCopyVideoTitle.clicked.connect(self.CopyVideoTitle)
        # Tracks the last auto-generated title so user edits aren't
        # clobbered on refresh.
        self._lastAutoVideoTitle = ""
        # Auto-refresh whenever any input to the title could have changed.
        # Bound-method slot so Qt delivers the signal on the GUI thread
        # (a lambda would run on the emitting worker thread and touch
        # widgets there).
        AutonaTournamentDataProvider.instance.signals.tournament_data_updated.connect(
            self._onTournamentDataUpdated)
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._RefreshVideoTitle)
        StateManager.signals.state_big_change.connect(
            self._RefreshVideoTitle)
        self._RefreshVideoTitle()

        # SET DEFAULTS
        self.setDefaultsButton = self.settings.findChild(
            QPushButton, "resetToDefault")
        # Reset to Defaults wipes the thumbnail_config block — flag as
        # destructive so it stands apart from the neutral preview/save row.
        L.tag_button(self.setDefaultsButton, L.BUTTON_DANGER)
        self.setDefaultsButton.clicked.connect(lambda: [
            SettingsManager.Unset("thumbnail_config"),
            self.updateFromSettings(),
            self.GeneratePreview()
        ])

        # BACKGROUND IMAGE SOURCE — three-way selector (default / tournament
        # banner / custom file). The effective image is resolved at render time
        # in thumbnail.prepare_data() from `thumbnail_config.background_source`;
        # `background_path` now holds only the user's custom image.
        self.bgSourceDefault = self.settings.findChild(
            QRadioButton, "bgSourceDefault")
        self.bgSourceBanner = self.settings.findChild(
            QRadioButton, "bgSourceBanner")
        self.bgSourceCustom = self.settings.findChild(
            QRadioButton, "bgSourceCustom")
        self.btSelectCustomBg = self.settings.findChild(
            QPushButton, "btSelectCustomBg")
        self.customBgName = self.settings.findChild(QLabel, "customBgName")

        for rb in (self.bgSourceDefault, self.bgSourceBanner, self.bgSourceCustom):
            rb.toggled.connect(self._onBgSourceToggled)
        self.btSelectCustomBg.clicked.connect(self._chooseCustomBackground)

        # Template selector (type_a / type_b)
        # NOTE: must use `is not None` — empty QComboBox is falsy via __len__
        self.templateSelect = self.settings.findChild(QComboBox, "templateSelect")
        if self.templateSelect is not None:
            self._template_options = [
                ("type_a", QApplication.translate("app", "Standard")),
                ("type_b", QApplication.translate("app", "Center Panel")),
            ]
            self.templateSelect.blockSignals(True)
            self.templateSelect.clear()
            for tid, label in self._template_options:
                self.templateSelect.addItem(label, tid)
            self.templateSelect.blockSignals(False)
            self.templateSelect.currentIndexChanged.connect(
                lambda: self.SaveSettings(
                    key="template_id",
                    val=self.templateSelect.currentData(),
                    generatePreview=True,
                )
            )

        # CHECKBOX
        self.phase_name = self.settings.findChild(QCheckBox, "phaseNameCheck")
        self.team_name = self.settings.findChild(QCheckBox, "teamNameCheck")
        self.sponsor = self.settings.findChild(QCheckBox, "sponsorCheck")
        self.smooth_scale = self.settings.findChild(QCheckBox, "smoothScaling")
        self.flip_p2 = self.settings.findChild(QCheckBox, "flipP2Check")
        self.flip_p1 = self.settings.findChild(QCheckBox, "flipP1Check")

        self.zoom = self.settings.findChild(QSpinBox, "zoom")

        self.horizontalAlign = self.settings.findChild(
            QSpinBox, "horizontalAlign")
        self.verticalAlign = self.settings.findChild(QSpinBox, "verticalAlign")

        self.phase_name.stateChanged.connect(lambda: self.SaveSettings(
            key="display_phase", val=self.phase_name.isChecked(), generatePreview=True))

        self.team_name.stateChanged.connect(lambda: self.SaveSettings(
            key="use_team_names", val=self.team_name.isChecked(), generatePreview=True))

        self.sponsor.stateChanged.connect(lambda: self.SaveSettings(
            key="use_sponsors", val=self.sponsor.isChecked(), generatePreview=True))

        self.flip_p2.stateChanged.connect(lambda: self.SaveSettings(
            key=f"game.{AutonaGameAssetManager.instance.selectedGame.get('codename')}.flip_p2", val=self.flip_p2.isChecked(), generatePreview=True))

        self.flip_p1.stateChanged.connect(lambda: self.SaveSettings(
            key=f"game.{AutonaGameAssetManager.instance.selectedGame.get('codename')}.flip_p1", val=self.flip_p1.isChecked(), generatePreview=True))

        self.smooth_scale.stateChanged.connect(lambda: self.SaveSettings(
            key=f"game.{AutonaGameAssetManager.instance.selectedGame.get('codename')}.smooth_scale", val=self.smooth_scale.isChecked(), generatePreview=True))

        self.zoom.valueChanged.connect(lambda:
                                       AutonaThumbnailSettingsWidget.SaveSettings(
                                           self,
                                           key=f"game.{AutonaGameAssetManager.instance.selectedGame.get('codename')}.zoom",
                                           val=self.zoom.value(),
                                           generatePreview=True
                                       )
                                       )

        self.horizontalAlign.valueChanged.connect(lambda val: [
            AutonaThumbnailSettingsWidget.SaveSettings(
                self,
                key=f"game.{AutonaGameAssetManager.instance.selectedGame.get('codename')}.align.horizontal",
                val=val,
                generatePreview=True
            )]
        )

        self.verticalAlign.valueChanged.connect(lambda val: [
            AutonaThumbnailSettingsWidget.SaveSettings(
                self,
                key=f"game.{AutonaGameAssetManager.instance.selectedGame.get('codename')}.align.vertical",
                val=val,
                generatePreview=True
            )]
        )

        # PREVIEW
        previewContainer = self.settings.findChild(QWidget, "thumbnailPreview")
        # Remove default layout margins so the pixmap reaches the container edges
        previewContainer.layout().setContentsMargins(0, 0, 0, 0)
        # Frame the preview slot so the empty/loading state stays visible
        # even when no pixmap has been generated yet. objectName is already
        # "thumbnailPreview" from the .ui file — re-asserting it would risk
        # clobbering a future rename, so we scope the QSS by walking the
        # palette directly.
        _p = UI.palette()
        previewContainer.setStyleSheet(
            f"QWidget#thumbnailPreview {{"
            f"background-color: {_p['bg_tertiary']};"
            f"border: 1px solid {_p['border_subtle']};"
            f"border-radius: {UI.RADIUS_SM}px;"
            f"}}"
        )
        self.preview = PreviewWidget()
        previewContainer.layout().addWidget(self.preview)

        self.copyToClipboard = self.settings.findChild(
            QPushButton, "btCopyToClipboard")
        self.copyToClipboard.clicked.connect(self._copyThumbnailToClipboard)

        self.saveThumbnail = self.settings.findChild(
            QPushButton, "btSaveThumbnail")
        self.saveThumbnail.clicked.connect(self._saveThumbnail)

        self.generateThumbnail = self.settings.findChild(
            QPushButton, "btGenerate")
        self.generateThumbnail.setHidden(True)

        # Shared settings-pane width (fixed in code, not the .ui) so the
        # left column lines up with the other preview panels.
        generalGroup = self.settings.findChild(
            QGroupBox, "groupBoxGeneralSettings")
        generalGroup.setFixedWidth(L.LEFT_PANE_WIDTH)

        # Normalise card padding / spacing across all top-level groups.
        # Runs before the color card is added: MakeFeatureColorCard already
        # builds on L.make_card, so it needs no normalisation pass.
        for group in self.settings.findChildren(QGroupBox):
            L.apply_card_layout(group)

        # Thumbnail-scoped theme / accent (colors.thumbnail.*), inheriting the
        # tournament colors while unset. The .ui puts groupBoxGeneralSettings
        # straight into horizontalLayout_8, so wrap it in a column here to get
        # a second card underneath it without editing the .ui.
        settingsRow = self.settings.findChild(
            QHBoxLayout, "horizontalLayout_8")
        leftCol = QVBoxLayout()
        leftCol.setContentsMargins(0, 0, 0, 0)
        leftCol.setSpacing(UI.PANEL_MARGIN)
        settingsRow.removeWidget(generalGroup)
        leftCol.addWidget(generalGroup)
        leftCol.addWidget(
            MakeFeatureColorCard(FEATURE_THUMBNAIL, "サムネイルの色"))
        leftCol.addStretch(1)
        settingsRow.insertLayout(0, leftCol)

        # Apply the design-system section-title style to in-card sub-headings
        # so they read as quiet uppercase labels (Design.md §5) instead of the
        # mismatched bold 10pt the .ui used to hard-code.
        for name in ["label_videoTitle", "label_17", "label_11",
                     "label_13", "label_19"]:
            lbl = self.settings.findChild(QLabel, name)
            if lbl:
                L.apply_section_title(lbl)

        # Zoom / Horizontal Align / Vertical Align are a [label | spinbox]
        # form grid — normalise its spacing and give the label column a fixed
        # width so the spinboxes line up regardless of label length / locale.
        transformGrid = self.settings.findChild(QGridLayout, "gridLayoutTransform")
        if transformGrid is not None:
            L.apply_form_grid(transformGrid)
            transformGrid.setColumnMinimumWidth(0, L.FORM_LABEL_WIDTH)
            transformGrid.setColumnStretch(1, 1)

        # Reload preview when game changes
        AutonaGameAssetManager.instance.signals.onLoad.connect(lambda: [
            self.updateFromSettings(),
            self.GeneratePreview()
        ])

        # Reload preview when banner is changed in EVENT tab
        AutonaTournamentDataProvider.instance.signals.media_updated.connect(
            self.GeneratePreview)

        # Debounce timer for preview generation
        self._previewTimer = QTimer()
        self._previewTimer.setSingleShot(True)
        self._previewTimer.setInterval(300)
        self._previewTimer.timeout.connect(self._doGeneratePreview)

        # Renderer is built on first reveal, not here: the off-screen
        # QWebEngineView plus its initial page load and first render cost ~3s
        # of startup for a panel most launches never open (startup Step
        # 4b-1). See _EnsureRenderer.
        self._renderer = None

        self.updateFromSettings()

    def _EnsureRenderer(self):
        """Build the off-screen renderer on first panel reveal; return it.

        Deliberately NOT called from the background signal handlers wired in
        __init__: letting a tournament fetch or a game reload spin the
        renderer up behind the scenes would give back the startup time this
        deferral buys. Those paths skip rendering while _renderer is None and
        showEvent re-renders from the latest state, so nothing is lost.
        """
        if self._renderer is None:
            from .thumbnail.ThumbnailRenderer import ThumbnailRenderer
            self._renderer = ThumbnailRenderer.instance()
            self._renderer.renderComplete.connect(self._onRenderComplete)
        return self._renderer

    def showEvent(self, e):
        super().showEvent(e)
        # No manual update button: small scoreboard edits (player names,
        # scores) don't emit the signals connected above, so re-render with
        # the latest state whenever the panel is revealed. Non-forced title
        # refresh preserves any manual edit to the video title.
        self._EnsureRenderer()
        self.GeneratePreview()
        self._RefreshVideoTitle()

    # ── Video title (YouTube) ──────────────────────────────────────

    # Fallback map from smashgg game id → English abbreviation, used when
    # the per-game config.json does not provide one (e.g. games not
    # installed under user_data/games/).
    _GAME_ABBREVIATION_FALLBACK = {
        1386: "SSBU",   # Super Smash Bros. Ultimate
        43868: "SF6",   # Street Fighter 6
    }

    def _BuildVideoTitle(self, scoreboard_id: int = 1) -> str:
        tournament = StateManager.Get(
            "tournamentInfo.tournamentName", "") or ""
        round_en = StateManager.Get(
            f"score.{scoreboard_id}.match_en", "") or ""
        game = StateManager.Get("game", {}) or {}
        game_abbrev = game.get("abbreviation")
        if not game_abbrev:
            try:
                game_abbrev = self._GAME_ABBREVIATION_FALLBACK.get(
                    int(game.get("smashgg_id")))
            except (TypeError, ValueError):
                game_abbrev = None
        game_abbrev = game_abbrev or ""

        side1 = self._PlayerWithCharacter(scoreboard_id, team=1)
        side2 = self._PlayerWithCharacter(scoreboard_id, team=2)

        # Build, stripping segments that resolved to empty so we don't emit
        # stray separators like "|  vs  |".
        parts_left = " ".join(p for p in (tournament, round_en) if p)
        if side1 or side2:
            vs_part = f"{side1} vs {side2}".strip()
        else:
            vs_part = ""
        segments = [s for s in (parts_left, vs_part, game_abbrev) if s]
        return " | ".join(segments)

    @classmethod
    def _PlayerWithCharacter(cls, scoreboard_id: int, team: int) -> str:
        """Return `"{player_name}({character_name})"` for team {team}'s
        first player. Drops the parenthesised character when no main
        character is set, and returns an empty string when the player
        itself is not set either."""
        player = cls._PlayerDisplayName(scoreboard_id, team)
        character = cls._MainCharacterName(scoreboard_id, team)
        if player and character:
            return f"{player}({character})"
        return player or character

    @staticmethod
    def _PlayerDisplayName(scoreboard_id: int, team: int) -> str:
        """Return team {team}'s first player's display name ("prefix |
        gamerTag" when a sponsor prefix exists, else just gamerTag).
        Empty string if no player data is set yet."""
        base = f"score.{scoreboard_id}.team.{team}.player.1"
        merged = StateManager.Get(f"{base}.mergedName", "") or ""
        if merged:
            # Strip the "[L]" losers-bracket indicator sometimes appended
            # by the scoreboard so titles stay clean.
            return merged.replace(" [L]", "")
        return StateManager.Get(f"{base}.gamerTag", "") or ""

    @staticmethod
    def _MainCharacterName(scoreboard_id: int, team: int) -> str:
        """Return the first (primary) character's name for team {team}'s
        first player, or empty string. Looks at the scoreboard's current
        `character` dict first, then falls back to the player's saved
        `mains`. The dict is keyed by **integer** slot numbers (1, 2, ...)
        because AutonaBasePlayerWidget stores them that way, so
        StateManager's dot-path lookup can't reach them directly — we
        fetch the whole dict and pick the smallest key."""
        base = f"score.{scoreboard_id}.team.{team}.player.1"
        for subkey in ("character", "mains"):
            chars = StateManager.Get(f"{base}.{subkey}")
            if not isinstance(chars, dict) or not chars:
                continue
            try:
                first_key = sorted(chars.keys(), key=lambda k: int(k))[0]
            except (TypeError, ValueError):
                first_key = next(iter(chars))
            entry = chars.get(first_key) or {}
            name = entry.get("name") or ""
            if name:
                return name
        return ""

    def _onTournamentDataUpdated(self, _data):
        self._RefreshVideoTitle()

    def _RefreshVideoTitle(self, force: bool = False):
        """Regenerate the auto title. When `force` is True (e.g. the
        preview button was pressed), overwrite any manual edits the user
        may have made. Otherwise preserve them."""
        new_title = self._BuildVideoTitle()
        current = self.videoTitleEdit.text()
        if force or current == self._lastAutoVideoTitle or not current:
            self.videoTitleEdit.setText(new_title)
        self._lastAutoVideoTitle = new_title

    def CopyVideoTitle(self):
        title = self.videoTitleEdit.text().strip()
        if not title:
            self.videoTitleStatus.setText(
                QApplication.translate("app", "Nothing to copy."))
            return
        QApplication.clipboard().setText(title)
        self.videoTitleStatus.setText(
            QApplication.translate("app", "Copied to clipboard."))

    def GetSetting(self, key, default=0):
        setting = SettingsManager.Get(
            f"thumbnail_config.{key}",
            default=None
        )
        if setting == None:
            setting = default
            SettingsManager.Set(f"thumbnail_config.{key}", default)
        return setting

    def SetBattleFormat(self, fmt):
        # Driven by the battle-format selector (Window.SetBattleFormat).
        # The team-name toggle only applies to team/crew boards: in 1v1 the
        # renderer falls back to player names because teamName is empty
        # (layout/thumbnail*/thumbnail.js setPlayerNames), so hide it there.
        # Accepts a bool for backward compatibility with older callers.
        if isinstance(fmt, bool):
            fmt = "team" if fmt else "singles"
        self.team_name.setVisible(fmt in ("team", "crew"))

    def updateFromSettings(self):
        game_codename = AutonaGameAssetManager.instance.selectedGame.get(
            'codename')

        self.GetSetting("main_icon_path", "./layout/logo.png")

        self._migrateBackgroundSource()
        self._updateBackgroundControls()

        # Template selector — see note above re: `is not None`
        if getattr(self, "templateSelect", None) is not None:
            current_tid = self.GetSetting("template_id", "type_a")
            idx = self.templateSelect.findData(current_tid)
            if idx >= 0:
                self.templateSelect.blockSignals(True)
                self.templateSelect.setCurrentIndex(idx)
                self.templateSelect.blockSignals(False)

        # Upper checkboxes
        self.phase_name.blockSignals(True)
        self.phase_name.setChecked(self.GetSetting("display_phase", True))
        self.phase_name.blockSignals(False)
        self.team_name.blockSignals(True)
        self.team_name.setChecked(self.GetSetting("use_team_names", True))
        self.team_name.blockSignals(False)
        self.sponsor.blockSignals(True)
        self.sponsor.setChecked(self.GetSetting("use_sponsors", True))
        self.sponsor.blockSignals(False)

        # Flip player assets
        self.flip_p2.blockSignals(True)
        self.flip_p2.setChecked(self.GetSetting(
            f"game.{game_codename}.flip_p2", True))
        self.flip_p2.blockSignals(False)

        self.flip_p1.blockSignals(True)
        self.flip_p1.setChecked(self.GetSetting(
            f"game.{game_codename}.flip_p1", False))
        self.flip_p1.blockSignals(False)

        self.smooth_scale.blockSignals(True)
        self.smooth_scale.setChecked(self.GetSetting(
            f"game.{game_codename}.smooth_scale", True))
        self.smooth_scale.blockSignals(False)

        # Eyesight alignment
        self.horizontalAlign.blockSignals(True)
        self.horizontalAlign.setValue(self.GetSetting(
            f"game.{game_codename}.align.horizontal",
            50
        ))
        self.horizontalAlign.blockSignals(False)

        self.verticalAlign.blockSignals(True)
        self.verticalAlign.setValue(self.GetSetting(
            f"game.{game_codename}.align.vertical",
            40
        ))
        self.verticalAlign.blockSignals(False)

        # Zoom
        self.zoom.blockSignals(True)
        self.zoom.setValue(self.GetSetting(
            f"game.{game_codename}.zoom",
            100
        ))
        self.zoom.blockSignals(False)

    def SaveIcons(self, key, side):
        path = QFileDialog.getOpenFileName()[0]
        if path:
            self.SaveSettings(key=f"{key}.{side}",
                              val=path, generatePreview=True)

    # ── Background image source (default / banner / custom) ────────────

    def _migrateBackgroundSource(self):
        """One-time migration from the legacy single `background_path` model
        to the explicit `background_source` selector. Runs until the key
        exists, so it is a no-op on every subsequent call."""
        if SettingsManager.Get("thumbnail_config.background_source") is not None:
            return
        legacy = SettingsManager.Get("thumbnail_config.background_path") or ""
        if legacy in (thumbnail.BANNER_PATH, thumbnail.DEFAULT_BANNER_PATH):
            # Was pointing at the tournament banner — free the custom slot.
            SettingsManager.Set("thumbnail_config.background_source", "banner")
            SettingsManager.Set("thumbnail_config.background_path", "")
        elif legacy:
            SettingsManager.Set("thumbnail_config.background_source", "custom")
        else:
            SettingsManager.Set("thumbnail_config.background_source", "default")

    def _updateBackgroundControls(self):
        """Reflect the persisted background_source into the radio group and the
        custom-file row (signals blocked so this never re-triggers a save)."""
        source = SettingsManager.Get(
            "thumbnail_config.background_source", "default")
        radios = {
            "default": self.bgSourceDefault,
            "banner": self.bgSourceBanner,
            "custom": self.bgSourceCustom,
        }
        for rb in radios.values():
            rb.blockSignals(True)
        for key, rb in radios.items():
            rb.setChecked(key == source)
        for rb in radios.values():
            rb.blockSignals(False)
        self._setCustomBgRowVisible(source == "custom")
        self._updateCustomBgName()

    def _setCustomBgRowVisible(self, visible):
        self.btSelectCustomBg.setVisible(visible)
        self.customBgName.setVisible(visible)

    def _updateCustomBgName(self):
        path = SettingsManager.Get("thumbnail_config.background_path") or ""
        self.customBgName.setText(os.path.basename(path) if path else "")

    def _onBgSourceToggled(self, checked):
        # toggled fires for both the newly-off and newly-on radio; act once.
        if not checked:
            return
        if self.bgSourceCustom.isChecked():
            source = "custom"
        elif self.bgSourceBanner.isChecked():
            source = "banner"
        else:
            source = "default"
        self._setCustomBgRowVisible(source == "custom")
        self.SaveSettings(key="background_source", val=source,
                          generatePreview=True)
        # Prompt for a file immediately when custom is picked with none set.
        # Defer the modal out of the signal handler (agent-pitfalls #9).
        if source == "custom" and not (
                SettingsManager.Get("thumbnail_config.background_path") or ""):
            QTimer.singleShot(0, self._chooseCustomBackground)

    def _chooseCustomBackground(self):
        path = QFileDialog.getOpenFileName(
            self,
            QApplication.translate("app", "Open Image"),
            ".",
            QApplication.translate("app", "Image Files") + " (*.png *.jpg *.jpeg *.bmp)"
        )[0]
        if path:
            self.SaveSettings(key="background_path", val=path,
                              generatePreview=True)
            self._updateCustomBgName()

    def SaveSettings(self, key, val, generatePreview=False):
        try:
            SettingsManager.Set(f"thumbnail_config.{key}", val)
        except Exception as e:
            logger.error(traceback.format_exc())

        if generatePreview:
            self.GeneratePreview()

    def GeneratePreview(self, manual=False):
        if manual:
            self._doGeneratePreview()
        else:
            # Debounce: restart the 300ms timer on each call
            self._previewTimer.start()

    def _doGeneratePreview(self):
        """Prepare data and start async render (non-blocking)."""
        # Skip entirely until the panel has been shown once (see
        # _EnsureRenderer). Every caller reaches here again via showEvent, so
        # a skipped update is re-rendered on reveal.
        if self._renderer is None:
            return
        try:
            SettingsManager.LoadSettings()
            self.updateFromSettings()

            data, config, template_data = thumbnail.prepare_data(
                settingsManager=SettingsManager,
                isPreview=True,
                gameAssetManager=AutonaGameAssetManager)

            # Async: renderer will emit renderComplete when done
            self._renderer.render(
                data=data,
                config=config,
                template=template_data)
        except Exception as e:
            logger.warning(f"Preview generation failed: {e}")

    def _onRenderComplete(self, pixmap):
        """Called when ThumbnailRenderer finishes rendering (async)."""
        if pixmap.isNull():
            return
        # Save temp file and update preview widget
        out_path = AutonaResolve("tmp/thumbnail")
        Path(out_path).mkdir(parents=True, exist_ok=True)
        tmp_file = f"{out_path}/template.png"
        pixmap.save(tmp_file)
        self.preview.setPixmap(pixmap)

    def _copyThumbnailToClipboard(self):
        """Generate thumbnail and copy to clipboard."""
        try:
            thumbnailPath = thumbnail.generate(
                settingsManager=SettingsManager)
            if thumbnailPath:
                image = QImage(thumbnailPath)
                if not image.isNull():
                    QApplication.clipboard().setImage(image)
                    logger.info("Thumbnail copied to clipboard")
        except Exception as e:
            logger.exception("Failed to copy thumbnail to clipboard")
            msgBox = QMessageBox()
            msgBox.setText(QApplication.translate("app", "Warning"))
            msgBox.setInformativeText(str(e))
            msgBox.setIcon(QMessageBox.Warning)
            msgBox.exec()

    def _saveThumbnail(self):
        """Generate thumbnail and save to out/thumbnails/."""
        try:
            thumbnailPath = thumbnail.generate(
                settingsManager=SettingsManager)
            if thumbnailPath:
                msgBox = QMessageBox()
                msgBox.setWindowIcon(QIcon(AutonaResolve('src/layout/icon.png')))
                msgBox.setWindowTitle(QApplication.translate(
                    "thumb_app", "Autona - Thumbnail"))
                msgBox.setText(QApplication.translate(
                    "thumb_app", "The thumbnail has been generated here:") + " " + thumbnailPath)
                msgBox.setIcon(QMessageBox.NoIcon)
                msgBox.exec()
        except Exception as e:
            logger.exception("Failed to save thumbnail")
            msgBox = QMessageBox()
            msgBox.setText(QApplication.translate("app", "Warning"))
            msgBox.setInformativeText(str(e))
            msgBox.setIcon(QMessageBox.Warning)
            msgBox.exec()

