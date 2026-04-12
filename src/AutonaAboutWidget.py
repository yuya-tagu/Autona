from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
import json
from .Helpers.AutonaDirHelper import AutonaResolve


class AutonaAboutWidget(QDialog):
    def __init__(self, *args):
        super().__init__(*args)
        uic.loadUi(AutonaResolve('src/layout/AutonaAbout.ui'), self)

        try:
            version = json.load(
                open(AutonaResolve('assets/versions.json'), encoding='utf-8')).get("program", "?")
        except Exception as e:
            version = "?"

        self.findChild(QLabel, "tsh").setText(
            f"Autona v{version}")

        try:
            icon = QPixmap("./assets/icons/icon.png").scaledToWidth(128)
        except:
            icon = QPixmap()

        self.findChild(QLabel, "icon").setPixmap(icon)

        try:
            contributors = open(AutonaResolve('assets/contributors.txt'),
                                encoding='utf-8').readlines()
        except Exception as e:
            contributors = ["?"]

        self.findChild(QTextEdit, "contributors").setMarkdown(
            "\n".join(contributors))
