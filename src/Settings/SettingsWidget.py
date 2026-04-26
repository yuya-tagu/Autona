from qtpy.QtWidgets import *
from ..AutonaColorButton import AutonaColorButton
from ..SettingsManager import SettingsManager
from ..Helpers.AutonaHotkeyValidator import (
    collision_summary,
    find_app_duplicates,
    normalize_shortcut,
    os_warning_for,
)
import textwrap


class SettingsWidget(QWidget):
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
            settingWidget.setChecked(SettingsManager.Get(
                self.settingsBase+"."+setting, defaultValue))
            settingWidget.stateChanged.connect(
                lambda val=None: SettingsManager.Set(self.settingsBase+"."+setting, settingWidget.isChecked()))
            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget:
                settingWidget.setChecked(defaultValue)
            )
        elif type == "spinbox":
            settingWidget = QSpinBox()
            settingWidget.setMinimum(1)
            settingWidget.setMaximum(99999)
            settingWidget.setValue(SettingsManager.Get(
                self.settingsBase+"."+setting, defaultValue))
            settingWidget.valueChanged.connect(
                lambda val=None: SettingsManager.Set(self.settingsBase+"."+setting, settingWidget.value()))
            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget:
                settingWidget.setValue(defaultValue)
            )
        elif type == "hotkey":
            settingWidget = QKeySequenceEdit()
            # Trim any extra chord recorded after the first key.
            settingWidget.keySequenceChanged.connect(
                lambda keySequence, settingWidget=settingWidget:
                settingWidget.setKeySequence(keySequence.toString().split(",")[
                    0]) if keySequence.count() > 0 else None
            )
            initial = SettingsManager.Get(
                self.settingsBase+"."+setting, defaultValue)
            settingWidget.setKeySequence(initial)
            # Track the last value we accepted so we can revert on
            # validation failure (Design.md §6.4.2 / §8 T-17).
            settingWidget._lastAcceptedSequence = initial or ""

            settingWidget.keySequenceChanged.connect(
                self._makeHotkeyValidator(settingWidget, setting, callback)
            )
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget,
                       defaultValue=defaultValue: [
                    settingWidget.setKeySequence(defaultValue),
                    callback()
                ]
            )
        elif type == "textbox" or type == "password":
            settingWidget = QLineEdit()
            if type == "password":
                settingWidget.setEchoMode(QLineEdit.Password)
            settingWidget.textChanged.connect(
                lambda val=None: SettingsManager.Set(self.settingsBase+"."+setting, settingWidget.text()))
            settingWidget.setText(SettingsManager.Get(
                self.settingsBase+"."+setting, defaultValue))
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setText(defaultValue),
                    callback()
                ]
            )
        elif type == "color":
            settingWidget = AutonaColorButton(color=SettingsManager.Get(self.settingsBase+"."+setting, defaultValue), disable_right_click=True)
            settingWidget.colorChanged.connect(
                lambda val=None: SettingsManager.Set(self.settingsBase+"."+setting, settingWidget.color()))
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setColor(defaultValue),
                    callback()
                ]
            )
        
        if tooltip:
            settingWidget.setToolTip('\n'.join(textwrap.wrap(tooltip, 40)))

        self.layout().addWidget(settingWidget, lastRow, 1)
        self.layout().addWidget(resetButton, lastRow, 2)

    def _makeHotkeyValidator(self, settingWidget, setting, callback):
        """Build a keySequenceChanged handler that enforces:
        - hard error on duplicates within Autona (revert + dialog)
        - soft warning for OS / OBS clashes (dialog, but save anyway)
        See Design.md §6.4.2 / §8 T-17.
        """
        def _handler(sequence):
            new_str = sequence.toString() if sequence is not None else ""
            previous = getattr(settingWidget, "_lastAcceptedSequence", "") or ""

            # No-op: identical to last accepted value (suppresses the
            # echo when we revert via setKeySequence below).
            if normalize_shortcut(new_str) == normalize_shortcut(previous):
                return

            # Build a hypothetical hotkey set with the new value applied
            # so duplicate detection sees the user's intent, not the
            # already-persisted value.
            current = dict(SettingsManager.Get(self.settingsBase, {}) or {})
            current[setting] = new_str
            duplicates = find_app_duplicates(current)

            collision_actions = None
            for canon, actions in duplicates.items():
                if canon == normalize_shortcut(new_str) and setting in actions:
                    collision_actions = [a for a in actions if a != setting]
                    break

            if collision_actions:
                QMessageBox.critical(
                    settingWidget,
                    QApplication.translate(
                        "settings.hotkeys", "Hotkey conflict"),
                    QApplication.translate(
                        "settings.hotkeys",
                        "'{shortcut}' is already assigned to: {others}.\n"
                        "Choose a different combination or clear the "
                        "conflicting assignment first."
                    ).format(
                        shortcut=new_str,
                        others=collision_summary(collision_actions),
                    ),
                )
                # Revert UI without re-triggering the handler.
                settingWidget.blockSignals(True)
                try:
                    settingWidget.setKeySequence(previous)
                finally:
                    settingWidget.blockSignals(False)
                return

            warning = os_warning_for(new_str)
            if warning:
                QMessageBox.warning(
                    settingWidget,
                    QApplication.translate(
                        "settings.hotkeys", "Possible hotkey conflict"),
                    warning,
                )

            SettingsManager.Set(self.settingsBase+"."+setting, new_str)
            settingWidget._lastAcceptedSequence = new_str
            callback()

        return _handler
