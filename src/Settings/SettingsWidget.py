from qtpy.QtWidgets import *
from ..AutonaColorButton import AutonaColorButton
from ..core.SettingsManager import SettingsManager
from ..AutonaHotkeys import AutonaHotkeys
from ..core import HotkeyRules
import textwrap


def _HotkeyWarningText(shortcut, code):
    """The dialog text for a HotkeyRules.WarningFor code (Design.md §6.4.2 /
    §8 T-17). The rules are core's since ADR-005 S4ae; the text stays this
    window's, as it was. None (no dialog) for any other code: this window
    has no words for WARNING_NO_MODIFIER, and goes in S4af."""
    if code == HotkeyRules.WARNING_OS_RESERVED:
        return (
            f"'{shortcut}' は OS や一般的なアプリ "
            "(コピー・保存・ウィンドウ操作など) で広く予約されています。"
            "他アプリにフォーカスがあると Autona に届きません。"
        )
    if code == HotkeyRules.WARNING_OBS_LIKELY:
        return (
            f"'{shortcut}' は OBS Studio で録画開始 / シーン切替などに "
            "割り当てられがちです。配信中の意図しない動作を避けるため、"
            "OBS 側のホットキー設定と重複しないか確認してください。"
        )
    return None


class HotkeyCaptureEdit(QKeySequenceEdit):
    """QKeySequenceEdit that releases the global hotkeys while focused.

    Win32 RegisterHotKey swallows the combination system-wide, so typing an
    already-assigned shortcut here would fire the action and record nothing.
    Suspending on focus in / resuming on focus out keeps the grab off exactly
    while the user is recording.
    """

    def _hotkeys(self):
        # Null-safe: no bootstrap_gui() means no instance to suspend.
        return AutonaHotkeys.instance

    def focusInEvent(self, event):
        hotkeys = self._hotkeys()
        if hotkeys is not None:
            hotkeys.SuspendCapture()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        hotkeys = self._hotkeys()
        if hotkeys is not None:
            hotkeys.ResumeCapture()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        # Safety net: closing/hiding the settings page while this field still
        # holds focus must not leave the hotkeys suspended. ResumeCapture()
        # is idempotent, so the usual focus-out path is unaffected.
        hotkeys = self._hotkeys()
        if hotkeys is not None:
            hotkeys.ResumeCapture()
        super().hideEvent(event)


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

    def _key(self, setting):
        # Nest under settingsBase unless it is empty. An empty base lets a
        # SettingsWidget drive top-level keys (e.g. program_language) that live
        # at the state root rather than under a section prefix.
        return f"{self.settingsBase}.{setting}" if self.settingsBase else setting

    def AddSetting(self, name: str, setting: str, type: str, defaultValue, callback=lambda: None, tooltip=None, options=None):
        lastRow = self.layout().rowCount()

        self.layout().addWidget(QLabel(name), lastRow, 0)

        resetButton = QPushButton(
            QApplication.translate("settings", "Default"))
        resetButton.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

        if type == "checkbox":
            settingWidget = QCheckBox()
            settingWidget.setChecked(SettingsManager.Get(
                self._key(setting), defaultValue))
            settingWidget.stateChanged.connect(
                lambda val=None: [
                    SettingsManager.Set(
                        self._key(setting), settingWidget.isChecked()),
                    callback()
                ])
            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget:
                settingWidget.setChecked(defaultValue)
            )
        elif type == "spinbox":
            settingWidget = QSpinBox()
            # The only spinbox is the web server's port: 1-65535, core's
            # AppSettings.PORT_RANGE (99999 let a port no server can listen
            # on be saved).
            settingWidget.setMinimum(1)
            settingWidget.setMaximum(65535)
            settingWidget.setValue(SettingsManager.Get(
                self._key(setting), defaultValue))
            settingWidget.valueChanged.connect(
                lambda val=None: SettingsManager.Set(self._key(setting), settingWidget.value()))
            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget:
                settingWidget.setValue(defaultValue)
            )
        elif type == "hotkey":
            settingWidget = HotkeyCaptureEdit()
            # Trim any extra chord recorded after the first key.
            settingWidget.keySequenceChanged.connect(
                lambda keySequence, settingWidget=settingWidget:
                settingWidget.setKeySequence(keySequence.toString().split(",")[
                    0]) if keySequence.count() > 0 else None
            )
            initial = SettingsManager.Get(
                self._key(setting), defaultValue)
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
                lambda val=None: SettingsManager.Set(self._key(setting), settingWidget.text()))
            settingWidget.setText(SettingsManager.Get(
                self._key(setting), defaultValue))
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setText(defaultValue),
                    callback()
                ]
            )
        elif type == "color":
            settingWidget = AutonaColorButton(color=SettingsManager.Get(self._key(setting), defaultValue), disable_right_click=True)
            settingWidget.colorChanged.connect(
                lambda val=None: SettingsManager.Set(self._key(setting), settingWidget.color()))
            resetButton.clicked.connect(
                lambda bt=None, setting=setting, settingWidget=settingWidget: [
                    settingWidget.setColor(defaultValue),
                    callback()
                ]
            )
        elif type == "combo":
            # options: list of (label, value). Populate and select the current
            # value BEFORE connecting the signal so building the widget does not
            # fire the callback (which, for language settings, pops a restart
            # dialog). The callback runs after persisting; callers that need a
            # modal there must defer it (QTimer.singleShot) to avoid modal
            # nesting inside this value-changed handler.
            settingWidget = QComboBox()
            for label, value in (options or []):
                settingWidget.addItem(label, value)
            current = SettingsManager.Get(self._key(setting), defaultValue)
            foundIndex = settingWidget.findData(current)
            settingWidget.setCurrentIndex(foundIndex if foundIndex >= 0 else 0)
            settingWidget.currentIndexChanged.connect(
                lambda i=None, settingWidget=settingWidget, setting=setting, callback=callback: [
                    SettingsManager.Set(self._key(setting), settingWidget.currentData()),
                    callback()
                ])
            resetButton.clicked.connect(
                lambda bt=None, settingWidget=settingWidget, defaultValue=defaultValue:
                settingWidget.setCurrentIndex(max(0, settingWidget.findData(defaultValue)))
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
            if (HotkeyRules.NormalizeShortcut(new_str)
                    == HotkeyRules.NormalizeShortcut(previous)):
                return

            # Build a hypothetical hotkey set with the new value applied
            # so duplicate detection sees the user's intent, not the
            # already-persisted value. Core's merge over the defaults (an
            # action never saved still takes its default's key) and core's
            # check, the settings page's: the same key however spelled
            # (ADR-005 S4ae).
            current = HotkeyRules.CurrentKeys()
            current[setting] = new_str
            collision_actions = HotkeyRules.Conflicts(current, setting)

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
                        others=HotkeyRules.CollisionSummary(
                            collision_actions),
                    ),
                )
                # Revert UI without re-triggering the handler.
                settingWidget.blockSignals(True)
                try:
                    settingWidget.setKeySequence(previous)
                finally:
                    settingWidget.blockSignals(False)
                return

            warning = _HotkeyWarningText(
                new_str, HotkeyRules.WarningFor(new_str))
            if warning:
                QMessageBox.warning(
                    settingWidget,
                    QApplication.translate(
                        "settings.hotkeys", "Possible hotkey conflict"),
                    warning,
                )

            SettingsManager.Set(self._key(setting), new_str)
            settingWidget._lastAcceptedSequence = new_str
            callback()

        return _handler
