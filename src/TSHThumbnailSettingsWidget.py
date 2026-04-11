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
from .SettingsManager import *
from .TSHGameAssetManager import *
from .Helpers.TSHDictHelper import deep_get, deep_set
from .Helpers.TSHDirHelper import TSHResolve


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
        # Clamp widget height to the pixmap aspect ratio so excess vertical
        # space is returned to neighbouring widgets instead of padding the pixmap.
        if self._pixmap and self._pixmap.width() > 0 and self.width() > 0:
            desired = int(self.width() * self._pixmap.height() / self._pixmap.width())
            if self.maximumHeight() != desired:
                self.setMaximumHeight(desired)

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


class TSHThumbnailSettingsWidget(QDockWidget):
    def __init__(self, *args):
        super().__init__(*args)

        if not SettingsManager.Get("thumbnail_config"):
            SettingsManager.Set("thumbnail_config", {})



        self.setWindowTitle(QApplication.translate(
            "app", "Thumbnail Settings"))
        self.setFloating(True)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.widget = QWidget()
        self.setWidget(self.widget)
        self.widget.setLayout(QVBoxLayout())

        self.settings = uic.loadUi(TSHResolve("src/layout/TSHThumbnailSettings.ui"))
        self.widget.layout().addWidget(self.settings)

        # SET DEFAULTS
        self.setDefaultsButton = self.settings.findChild(
            QPushButton, "resetToDefault")
        self.setDefaultsButton.clicked.connect(lambda: [
            SettingsManager.Unset("thumbnail_config"),
            self.updateFromSettings(),
            self.GeneratePreview()
        ])

        # IMG
        self.background = self.settings.findChild(
            QPushButton, "customBackground")
        self.backgroundReset = self.settings.findChild(
            QPushButton, "customBackgroundReset")
        self.backgroundReset.setIcon(QIcon('assets/icons/undo.svg'))
        self.backgroundReset.clicked.connect(lambda: [
            SettingsManager.Unset("thumbnail_config.background_path"),
            self.GeneratePreview()
        ])

        # Hide the Custom Icon section — icons are managed via
        # tournament info (DownloadIcon) and not user-customizable.
        for name in ["label_2", "customMainIcon", "customMainIconReset",
                      "customTopLeftIcon", "customTopLeftIconReset",
                      "customTopRightIcon", "customTopRightIconReset"]:
            w = self.settings.findChild(QWidget, name)
            if w:
                w.hide()

        self.background.clicked.connect(
            lambda: self.SaveImage("background_path"))

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
        self.open_explorer = self.settings.findChild(
            QCheckBox, "openExplorerCheck")

        self.zoom = self.settings.findChild(QSpinBox, "zoom")

        self.horizontalAlign = self.settings.findChild(
            QSpinBox, "horizontalAlign")
        self.verticalAlign = self.settings.findChild(QSpinBox, "verticalAlign")

        self.scaleToFillX = self.settings.findChild(QCheckBox, "scaleToFillX")
        self.scaleToFillY = self.settings.findChild(QCheckBox, "scaleToFillY")

        self.proportionalScaling = self.settings.findChild(
            QCheckBox, "proportionalScaling")


        self.phase_name.stateChanged.connect(lambda: self.SaveSettings(
            key="display_phase", val=self.phase_name.isChecked(), generatePreview=True))

        self.team_name.stateChanged.connect(lambda: self.SaveSettings(
            key="use_team_names", val=self.team_name.isChecked(), generatePreview=True))

        self.sponsor.stateChanged.connect(lambda: self.SaveSettings(
            key="use_sponsors", val=self.sponsor.isChecked(), generatePreview=True))

        self.open_explorer.stateChanged.connect(lambda: self.SaveSettings(
            key="open_explorer", val=self.open_explorer.isChecked(), generatePreview=True))

        self.flip_p2.stateChanged.connect(lambda: self.SaveSettings(
            key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.flip_p2", val=self.flip_p2.isChecked(), generatePreview=True))

        self.flip_p1.stateChanged.connect(lambda: self.SaveSettings(
            key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.flip_p1", val=self.flip_p1.isChecked(), generatePreview=True))

        self.smooth_scale.stateChanged.connect(lambda: self.SaveSettings(
            key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.smooth_scale", val=self.smooth_scale.isChecked(), generatePreview=True))

        self.zoom.valueChanged.connect(lambda:
                                       TSHThumbnailSettingsWidget.SaveSettings(
                                           self,
                                           key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.zoom",
                                           val=self.zoom.value(),
                                           generatePreview=True
                                       )
                                       )

        self.horizontalAlign.valueChanged.connect(lambda val: [
            TSHThumbnailSettingsWidget.SaveSettings(
                self,
                key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.align.horizontal",
                val=val,
                generatePreview=True
            )]
        )

        self.verticalAlign.valueChanged.connect(lambda val: [
            TSHThumbnailSettingsWidget.SaveSettings(
                self,
                key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.align.vertical",
                val=val,
                generatePreview=True
            )]
        )

        self.scaleToFillX.stateChanged.connect(lambda val: [
            TSHThumbnailSettingsWidget.SaveSettings(
                self,
                key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.scaleFillX",
                val=self.scaleToFillX.isChecked(),
                generatePreview=True
            )]
        )

        self.scaleToFillY.stateChanged.connect(lambda val: [
            TSHThumbnailSettingsWidget.SaveSettings(
                self,
                key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.scaleFillY",
                val=self.scaleToFillY.isChecked(),
                generatePreview=True
            )]
        )

        self.proportionalScaling.stateChanged.connect(lambda val: [
            TSHThumbnailSettingsWidget.SaveSettings(
                self,
                key=f"game.{TSHGameAssetManager.instance.selectedGame.get('codename')}.proportionalScaling",
                val=self.proportionalScaling.isChecked(),
                generatePreview=True
            )]
        )

        # PREVIEW
        previewContainer = self.settings.findChild(QWidget, "thumbnailPreview")
        # Remove default layout margins so the pixmap reaches the container edges
        previewContainer.layout().setContentsMargins(0, 0, 0, 0)
        self.preview = PreviewWidget()
        previewContainer.layout().addWidget(self.preview)

        self.updatePreview = self.settings.findChild(
            QPushButton, "btUpdatePreview")
        self.updatePreview.clicked.connect(lambda: self.GeneratePreview(True))

        self.copyToClipboard = self.settings.findChild(
            QPushButton, "btCopyToClipboard")
        self.copyToClipboard.clicked.connect(self._copyThumbnailToClipboard)

        self.saveThumbnail = self.settings.findChild(
            QPushButton, "btSaveThumbnail")
        self.saveThumbnail.clicked.connect(self._saveThumbnail)

        self.generateThumbnail = self.settings.findChild(
            QPushButton, "btGenerate")
        self.generateThumbnail.setHidden(True)

        # Reload preview when game changes
        TSHGameAssetManager.instance.signals.onLoad.connect(lambda: [
            self.updateFromSettings(),
            self.GeneratePreview()
        ])

        # Debounce timer for preview generation
        self._previewTimer = QTimer()
        self._previewTimer.setSingleShot(True)
        self._previewTimer.setInterval(300)
        self._previewTimer.timeout.connect(self._doGeneratePreview)

        # Connect renderer signal for async preview updates
        from .thumbnail.ThumbnailRenderer import ThumbnailRenderer
        self._renderer = ThumbnailRenderer.instance()
        self._renderer.renderComplete.connect(self._onRenderComplete)

        self.updateFromSettings()
        self.GeneratePreview()

    def GetSetting(self, key, default=0):
        setting = SettingsManager.Get(
            f"thumbnail_config.{key}",
            default=None
        )
        if setting == None:
            setting = default
            SettingsManager.Set(f"thumbnail_config.{key}", default)
        return setting

    def updateFromSettings(self):
        game_codename = TSHGameAssetManager.instance.selectedGame.get(
            'codename')

        self.GetSetting("main_icon_path", "./layout/logo.png")

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
        self.open_explorer.blockSignals(True)
        self.open_explorer.setChecked(self.GetSetting("open_explorer", False))
        self.open_explorer.blockSignals(False)

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

        # Find biggest asset pack for the current game
        biggest_pack = self._getBiggestAssetPack()

        # Proportional scaling
        self.proportionalScaling.blockSignals(True)
        if biggest_pack.get("rescaling_factor", []):
            self.proportionalScaling.setEnabled(True)
            self.proportionalScaling.setChecked(self.GetSetting(
                f"game.{game_codename}.proportionalScaling",
                True
            ))
        else:
            self.proportionalScaling.setEnabled(False)
            self.proportionalScaling.setChecked(False)
        self.proportionalScaling.blockSignals(False)

        # Scale to fill X/Y
        self.scaleToFillX.blockSignals(True)
        self.scaleToFillX.setChecked(self.GetSetting(
            f"game.{game_codename}.scaleFillX",
            False
        ))
        self.scaleToFillX.blockSignals(False)

        self.scaleToFillY.blockSignals(True)
        self.scaleToFillY.setChecked(self.GetSetting(
            f"game.{game_codename}.scaleFillY",
            False
        ))
        self.scaleToFillY.blockSignals(False)

        # Disable scale to fill options if it doesn't make sense with uncropped edge
        uncropped_edge = biggest_pack.get("uncropped_edge", [])

        if 'l' in uncropped_edge or 'r' in uncropped_edge:
            self.scaleToFillX.setEnabled(True)
            self.scaleToFillX.setChecked(
                self.GetSetting(f"game.{game_codename}.scaleFillX", False))
        else:
            self.scaleToFillX.setEnabled(False)

        if 'u' in uncropped_edge or 'd' in uncropped_edge:
            self.scaleToFillY.setEnabled(True)
            self.scaleToFillY.setChecked(
                self.GetSetting(f"game.{game_codename}.scaleFillY", False))
        else:
            self.scaleToFillY.setEnabled(False)

    def _getBiggestAssetPack(self):
        """Return the asset pack dict with the largest average image size."""
        assets = TSHGameAssetManager.instance.selectedGame.get("assets", {})
        biggest = {}
        biggest_size = 0
        for key, val in assets.items():
            if key == "base_files":
                continue
            avg = val.get("average_size", {})
            size = avg.get("x", 0) * avg.get("y", 0)
            if size > biggest_size:
                biggest_size = size
                biggest = val
        return biggest

    def SaveIcons(self, key, side):
        path = QFileDialog.getOpenFileName()[0]
        if path:
            self.SaveSettings(key=f"{key}.{side}",
                              val=path, generatePreview=True)

    def SaveImage(self, key):
        path = QFileDialog.getOpenFileName()[0]
        if path:
            self.SaveSettings(key=key, val=path, generatePreview=True)

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
        try:
            SettingsManager.LoadSettings()
            self.updateFromSettings()

            data, config, template_data = thumbnail.prepare_data(
                settingsManager=SettingsManager,
                isPreview=True,
                gameAssetManager=TSHGameAssetManager)

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
        out_path = TSHResolve("tmp/thumbnail")
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
                msgBox.setWindowIcon(QIcon(TSHResolve('src/layout/icon.png')))
                msgBox.setWindowTitle(QApplication.translate(
                    "thumb_app", "TSH - Thumbnail"))
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

