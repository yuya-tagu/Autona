from qtpy.QtCore import *
from qtpy.QtWidgets import *
from .SettingsWidget import SettingsWidget
from ..AutonaHotkeys import AutonaHotkeys
from ..Helpers.AutonaVersionHelper import add_beta_label
from ..Helpers.AutonaLocaleHelper import AutonaLocaleHelper


class AutonaSettingsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent=parent)

    def UiMounted(self):
        self.setWindowTitle(QApplication.translate("Settings", "Settings"))

        # Create a list widget for the selection
        self.selection_list = QListWidget()
        self.selection_list.currentRowChanged.connect(
            self.on_selection_changed)

        # Create a stacked widget for the settings widgets
        self.settings_stack = QStackedWidget()

        # Create a scroll area for the settings stack
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.settings_stack)

        # Create a splitter for the selection and settings
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.selection_list)
        splitter.addWidget(scroll_area)

        # Set the layout for the dialog
        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        # Add general settings
        generalSettings = []

        generalSettings.append((
            QApplication.translate(
                "settings.general", "Webserver Port (takes effect on next restart)"),
            "webserver_port",
            "spinbox",
            5000
        ))

        generalSettings.append((
            QApplication.translate(
                "settings.webserver_listen_lan",
                "Allow access from other devices on the LAN (takes effect on next restart)"),
            "webserver_listen_lan",
            "checkbox",
            False
        ))

        generalSettings.append((
            QApplication.translate(
                "settings.general", "Enable profanity filter"),
            "profanity_filter",
            "checkbox",
            True
        ))

        generalSettings.append((
            QApplication.translate(
                "settings.disable_autoupdate", "Disable automatic set updating for the scoreboard"),
            "disable_autoupdate",
            "checkbox",
            False
        ))

        generalSettings.append((
            QApplication.translate(
                "settings.disable_scoreupdate", "Disable automatic score updating for the scoreboard"),
            "disable_scoreupdate",
            "checkbox",
            False
        ))

        generalSettings.append((
            QApplication.translate(
                "settings.disable_export", "Disable Autona file exporting"),
            "disable_export",
            "checkbox",
            False
        ))
        
        generalSettings.append((
            QApplication.translate(
                "settings.disable_overwrite", "Do not override existing values in the local player database (takes effect on next restart)"),
            "disable_overwrite",
            "checkbox",
            False
        ))

        generalSettings.append((
            add_beta_label(QApplication.translate(
                "settings.disable_individual_game_tracker", "Disables the individual game tracker (takes effect on next restart)"), "game_tracker"),
            "disable_individual_game_tracker",
            "checkbox",
            True
        ))

        generalSettings.append((
            QApplication.translate(
                "settings.team_1_default_color", "Default Color of Team 1"),
            "team_1_default_color",
            "color",
            "#fe3636"
        ))

        generalSettings.append((
            QApplication.translate(
                "settings.team_2_default_color", "Default Color of Team 2"),
            "team_2_default_color",
            "color",
            "#2e89ff"
        ))

        self.add_setting_widget(QApplication.translate(
            "settings", "General"), SettingsWidget("general", generalSettings))

        # Add language settings (moved here from the Help & Settings menu).
        # These keys are top-level (program_language / game_asset_language /
        # fg_term_language), so the SettingsWidget uses an empty base. Each
        # change requires a restart, shown via a deferred messagebox so the
        # modal never nests inside the combo's currentIndexChanged handler.
        languageSettings = []

        languageChoices = [
            (f"{language[0]} / {language[1]}", code)
            for code, language in AutonaLocaleHelper.languages.items()
        ]

        programMsg = self._make_restart_messagebox(QApplication.translate(
            "app", "Program language changed successfully."))
        languageSettings.append((
            QApplication.translate("app", "Program Language"),
            "program_language",
            "combo",
            "default",
            lambda mb=programMsg: QTimer.singleShot(0, mb.exec),
            None,
            [(QApplication.translate("app", "System language"), "default")]
            + languageChoices,
        ))

        assetMsg = self._make_restart_messagebox(QApplication.translate(
            "app", "Game Asset Language changed successfully."))
        languageSettings.append((
            QApplication.translate("app", "Game Asset Language"),
            "game_asset_language",
            "combo",
            "default",
            lambda mb=assetMsg: QTimer.singleShot(0, mb.exec),
            None,
            [(QApplication.translate("app", "Same as program language"), "default")]
            + languageChoices,
        ))

        fgTermMsg = self._make_restart_messagebox(QApplication.translate(
            "app", "Tournament term language changed successfully."))
        languageSettings.append((
            QApplication.translate("app", "Tournament term language"),
            "fg_term_language",
            "combo",
            "default",
            lambda mb=fgTermMsg: QTimer.singleShot(0, mb.exec),
            None,
            [(QApplication.translate("app", "Same as program language"), "default")]
            + languageChoices,
        ))

        self.add_setting_widget(QApplication.translate(
            "settings", "Language"), SettingsWidget("", languageSettings))

        # Add hotkey settings
        hotkeySettings = []

        hotkeySettings.append((
            QApplication.translate("settings.hotkeys", "Enable hotkeys"),
            "hotkeys_enabled",
            "checkbox",
            True,
            AutonaHotkeys.instance.ApplyEnabledSetting
        ))

        key_names = {
            "load_set": QApplication.translate("settings.hotkeys", "Load set"),
            "team1_score_up": QApplication.translate("settings.hotkeys", "Team 1 score up"),
            "team1_score_down": QApplication.translate("settings.hotkeys", "Team 1 score down"),
            "team2_score_up": QApplication.translate("settings.hotkeys", "Team 2 score up"),
            "team2_score_down": QApplication.translate("settings.hotkeys", "Team 2 score down"),
            "reset_scores": QApplication.translate("settings.hotkeys", "Reset scores"),
            "swap_teams": QApplication.translate("settings.hotkeys", "Swap teams"),
            "refresh_phase_group": QApplication.translate("settings.hotkeys", "Reload Top 8 from Start.gg"),
            "apply_changes": QApplication.translate("settings.hotkeys", "Apply changes"),
        }

        for i, (setting, value) in enumerate(AutonaHotkeys.instance.keys.items()):
            hotkeySettings.append((
                key_names[setting],
                setting,
                "hotkey",
                value,
                AutonaHotkeys.instance.ReloadHotkeys
            ))

        self.add_setting_widget(QApplication.translate(
            "settings", "Hotkeys"), SettingsWidget("hotkeys", hotkeySettings))

        # Display-option visibility toggles live on the scoreboard "eye" menu
        # / the Web UI DISPLAY_OPTIONS, which own the display_options.show_*
        # keys (defined in src/core/StateSchema.py). The former Settings section
        # here duplicated those keys (and carried two dead ones:
        # show_birthday / show_controller) so it was removed.

        # Split 200px selection list / rest for the settings pane. The width
        # is taken from the resize target instead of reading self.width()
        # after a QApplication.processEvents() call: UiMounted() now runs from
        # the menu action handler on first open, and re-entering the event
        # loop inside a signal handler is forbidden (agent-pitfalls #9).
        width = 1000
        self.resize(width, 500)
        splitter.setSizes([200, width - 200])

    def on_selection_changed(self, index):
        # Get the selected item and its associated widget
        item = self.selection_list.item(index)
        widget = item.data(Qt.UserRole)

        # Set the current widget in the stack
        self.settings_stack.setCurrentWidget(widget)

    def add_setting_widget(self, name, widget):
        # Create a list widget item for the selection
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, widget)
        self.selection_list.addItem(item)

        # Add the setting widget to the stack
        self.settings_stack.addWidget(widget)

    def _make_restart_messagebox(self, main_txt):
        # Language changes take effect on restart, so inform then close.
        # Mirrors Autona.generate_restart_messagebox; shown deferred from the
        # combo's currentIndexChanged handler (QTimer.singleShot) to avoid
        # modal nesting inside a value-changed signal (CLAUDE.md
        # "モーダル入れ子禁止").
        mb = QMessageBox(self)
        mb.setWindowTitle(QApplication.translate("app", "Warning"))
        mb.setText(main_txt + "\n" + QApplication.translate(
            "app", "The program will now close."))
        mb.finished.connect(QApplication.exit)
        return mb
