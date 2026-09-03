"""One settings control per option an OBS layout exposes.

Each control mirrors its value into two places: LayoutOptionsManager
(./user_data/layout_options.json, the persisted source of truth this widget
reads back when it is rebuilt) and StateManager (``layout_options.*``, what
the overlay renders from). Both writes are one call --
``AutonaActions.SetLayoutOption`` -- since headless core Step 5; the pair used
to be spelled out in each of the four handlers below, which meant the widget
owned half of a mirror.
"""
from qtpy.QtWidgets import *
from ..AutonaColorButton import AutonaColorButton
from ..core.LayoutOptionsManager import LayoutOptionsManager
import textwrap
from ..core.Actions import AutonaActions
from loguru import logger


class LayoutOptionsWidget(QWidget):
    def __init__(self, settingsBase="", settings=[]):
        super().__init__()

        self.settingsBase = settingsBase

        # Create a layout for the widget and add the label
        layout = QGridLayout()
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMaximumSize)

        # Set the layout for the widget
        self.setLayout(layout)

        for setting in settings:
            self.AddSetting(*setting)

    def AddSetting(self, name: str, setting: str, type: str, defaultValue, callback=lambda: None, tooltip=None):
        lastRow = self.layout().rowCount()

        self.layout().addWidget(QLabel(name), lastRow, 0)

        resetButton = QPushButton(
            QApplication.translate("settings", "Default"))
        resetButton.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

        if type == "checkbox":
            settingWidget = QCheckBox()
            settingWidget.stateChanged.connect(
                lambda val=None: AutonaActions.instance.SetLayoutOption(
                    self.settingsBase, setting, settingWidget.isChecked()))
            settingWidget.setChecked(LayoutOptionsManager.Get(self.settingsBase+"."+setting, defaultValue))
            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget:
                settingWidget.setChecked(defaultValue)
            )
        elif type == "textbox" or type == "password":
            settingWidget = QLineEdit()
            if type == "password":
                settingWidget.setEchoMode(QLineEdit.Password)
            settingWidget.textChanged.connect(
                lambda val=None: AutonaActions.instance.SetLayoutOption(
                    self.settingsBase, setting, settingWidget.text()))
            settingWidget.setText(LayoutOptionsManager.Get(
                self.settingsBase+"."+setting, defaultValue))
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setText(defaultValue),
                    callback()
                ]
            )
        elif type == "color":
            settingWidget = AutonaColorButton(color="#000000", disable_right_click=True, enable_alpha_selection=True, ignore_same_color=False)
            settingWidget.colorChanged.connect(
                lambda val=None: AutonaActions.instance.SetLayoutOption(
                    self.settingsBase, setting, settingWidget.color()))
            settingWidget.setColor(LayoutOptionsManager.Get(self.settingsBase+"."+setting, defaultValue))
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setColor(defaultValue),
                    callback()
                ]
            )
        elif type == "dropdown":
            settingWidget = QComboBox()
            for item in defaultValue:
                settingWidget.addItem(item)
            settingWidget.currentIndexChanged.connect(
                lambda val=None: AutonaActions.instance.SetLayoutOption(
                    self.settingsBase, setting, settingWidget.currentText()))
            index = LayoutOptionsManager.Get(self.settingsBase+"."+setting, "")
            settingWidget.setCurrentIndex(defaultValue.index(index) if index != "" and index in defaultValue else 0)
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setCurrentText(settingWidget.setCurrentIndex(0)),
                    callback()
                ]
            )
        
        if tooltip:
            settingWidget.setToolTip('\n'.join(textwrap.wrap(tooltip, 40)))

        self.layout().addWidget(settingWidget, lastRow, 1)
        self.layout().addWidget(resetButton, lastRow, 2)
