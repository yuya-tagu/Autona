from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import time
from .StateManager import StateManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .SettingsManager import SettingsManager
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaDirHelper import AutonaResolve
from .AutonaColorButton import AutonaColorButton
import traceback
import shutil
from pathlib import Path
from loguru import logger
import requests

DEFAULT_THEME_COLOR = "#1D1B18"
DEFAULT_ACCENT_COLOR = "#f0a500"
DEFAULT_BANNER_PATH = "./assets/thumbnail_base/default_banner.png"
BANNER_PATH = "./user_data/tournament_banner.png"


class AutonaTournamentInfoWidget(QDockWidget):
    def __init__(self, *args):
        super().__init__(*args)

        uic.loadUi(AutonaResolve("src/layout/AutonaTournamentInfo.ui"), self)

        # Wrap the dock contents in a QScrollArea so the tab doesn't
        # force the whole window to grow vertically.
        contents = self.widget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(contents)
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
            if widget.objectName() != "qt_spinbox_lineedit":
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

        self.btLoadInfoFromTournament: QPushButton = self.findChild(
            QPushButton, "btLoadInfoFromTournament")
        self.btLoadInfoFromTournament.clicked.connect(lambda: [
            AutonaTournamentDataProvider.instance.GetTournamentData()
        ])

        self.btClearInfo: QPushButton = self.findChild(
            QPushButton, "btClearInfo")
        self.btClearInfo.clicked.connect(lambda: [
            self.UpdateData({})
        ])

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

        self.btClearIcon: QPushButton = self.findChild(
            QPushButton, "btClearIcon")
        self.btClearIcon.clicked.connect(lambda: [
            self.SetDefaultIcon()
        ])

        self.iconView: QLabel = self.findChild(QLabel, "iconView")

        self.UpdateIcon()

        # Banner UI
        self.btLoadBannerFile: QPushButton = self.findChild(
            QPushButton, "btLoadBannerFile")
        self.btLoadBannerFile.clicked.connect(self.LoadBannerFile)

        self.btDownloadBanner: QPushButton = self.findChild(
            QPushButton, "btDownloadBanner")
        self.btDownloadBanner.clicked.connect(self.DownloadBanner)

        self.btClearBanner: QPushButton = self.findChild(
            QPushButton, "btClearBanner")
        self.btClearBanner.clicked.connect(self.SetDefaultBanner)

        self.bannerView: QLabel = self.findChild(QLabel, "bannerView")

        self.UpdateBanner()

        # Theme color button
        saved_theme = SettingsManager.Get("general.theme_color", DEFAULT_THEME_COLOR)
        placeholder = self.findChild(QToolButton, "btThemeColor")
        self.btThemeColor = AutonaColorButton(color=saved_theme)
        self.btThemeColor.setMinimumSize(32, 32)
        placeholder.parentWidget().layout().replaceWidget(placeholder, self.btThemeColor)
        placeholder.deleteLater()
        self.btThemeColor.colorChanged.connect(self._onThemeColorChanged)
        StateManager.Set("tournamentInfo.themeColor", saved_theme)

        # Accent color button
        saved_accent = SettingsManager.Get("general.accent_color", DEFAULT_ACCENT_COLOR)
        placeholder2 = self.findChild(QToolButton, "btAccentColor")
        self.btAccentColor = AutonaColorButton(color=saved_accent)
        self.btAccentColor.setMinimumSize(32, 32)
        placeholder2.parentWidget().layout().replaceWidget(placeholder2, self.btAccentColor)
        placeholder2.deleteLater()
        self.btAccentColor.colorChanged.connect(self._onAccentColorChanged)
        StateManager.Set("tournamentInfo.accentColor", saved_accent)

    def _onThemeColorChanged(self, color):
        if color:
            SettingsManager.Set("general.theme_color", color)
            StateManager.Set("tournamentInfo.themeColor", color)

    def _onAccentColorChanged(self, color):
        if color:
            SettingsManager.Set("general.accent_color", color)
            StateManager.Set("tournamentInfo.accentColor", color)

    def UpdateIcon(self):
        try:
            # Load icon
            icon = QPixmap("./layout/logo.png").scaledToWidth(64)
        except:
            # Something's wrong with the icon
            icon = QPixmap("./assets/icons/cancel.svg").scaledToWidth(32)

        self.iconView.setPixmap(icon)

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
                pix.save("./layout/logo.png")
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

            pix.save("./layout/logo.png")
        except Exception as e:
            logger.error(traceback.format_exc())

        self.UpdateIcon()

        # Also download tournament banner for thumbnail background
        self.DownloadBanner()

    def UpdateBanner(self):
        bg_path = SettingsManager.Get("thumbnail_config.background_path", DEFAULT_BANNER_PATH)
        try:
            pix = QPixmap(bg_path)
            if pix.isNull():
                pix = QPixmap(DEFAULT_BANNER_PATH)
            banner = pix.scaledToWidth(178, Qt.TransformationMode.SmoothTransformation)
        except:
            banner = QPixmap(DEFAULT_BANNER_PATH).scaledToWidth(178, Qt.TransformationMode.SmoothTransformation)
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
                SettingsManager.Set("thumbnail_config.background_path", BANNER_PATH)
        except Exception as e:
            logger.error(traceback.format_exc())

        self.UpdateBanner()

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

            SettingsManager.Set("thumbnail_config.background_path", BANNER_PATH)
            logger.info(f"Tournament banner saved and set as thumbnail background")
        except Exception as e:
            logger.error(traceback.format_exc())

        self.UpdateBanner()

    def SetDefaultBanner(self):
        try:
            SettingsManager.Set("thumbnail_config.background_path", DEFAULT_BANNER_PATH)
        except:
            logger.error(traceback.format_exc())

        self.UpdateBanner()

    def SetDefaultIcon(self):
        try:
            shutil.copy("./assets/icons/icon.png", "./layout/logo.png")
        except:
            logger.error(traceback.format_exc())

        self.UpdateIcon()

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
