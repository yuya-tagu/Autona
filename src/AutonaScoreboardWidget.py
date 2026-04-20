import math
import platform
import socket
import subprocess
import urllib.parse
import webbrowser

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
from typing import List
from src.AutonaColorButton import AutonaColorButton
from .Helpers.AutonaDirHelper import AutonaResolve
from .Helpers.AutonaVersionHelper import add_beta_label
from .Helpers.AutonaBskyHelper import post_to_bsky

from . import ui_constants as UI
from .AutonaSelectSetWindow import AutonaSelectSetWindow
from .AutonaSelectStationWindow import AutonaSelectStationWindow
from .AutonaIndividualGameTracker import AutonaIndividualGameTracker

from .AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaStatsUtil import AutonaStatsUtil
from .AutonaHotkeys import AutonaHotkeys
from .AutonaPlayerDB import AutonaPlayerDB

from .thumbnail import main_generate_thumbnail as thumbnail
from .AutonaThumbnailSettingsWidget import *


empty = {}


class QueueSetsCache:
    queue = []

    def UpdateQueue(self, q):
        self.queue = q

    def CheckQueue(self, q):
        logger.info("----------------- CHECKING QUEUES -------------------")
        if q is None:
            return False

        if len(self.queue) != len(q):
            return False

        for i in range(len(q)):
            savedSet = self.queue[i]
            incSet = q[i]

            if savedSet.get("id") != incSet.get("id"):
                return False

            if savedSet.get("state") != incSet.get("state"):
                return False

            savedSlots = savedSet.get("slots", [empty, empty])
            incSlots = incSet.get("slots", [empty, empty])

            if deep_get(savedSlots[0], "entrant.id") != deep_get(incSlots[0], "entrant.id"):
                return False

            if deep_get(savedSlots[1], "entrant.id") != deep_get(incSlots[1], "entrant.id"):
                return False

        logger.info("----------------- QUEUES CHECK OK -------------------")
        return True


class AutonaScoreboardWidgetSignals(QObject):
    UpdateSetData = Signal(object)
    NewSetSelected = Signal(object)
    SetSelection = Signal()
    StreamSetSelection = Signal()
    StationSelection = Signal()
    StationSelected = Signal(object)
    StationSetsLoaded = Signal(object)
    UserSetSelection = Signal()
    CommandScoreChange = Signal(int, int)
    CommandTeamColor = Signal(int, str)
    SwapTeams = Signal()
    ChangeSetData = Signal(dict)


class AutonaScoreboardWidget(QWidget):
    stationQueueCache = QueueSetsCache()

    def __init__(self, scoreboardNumber=1, singlePlayer=True, *args):
        super().__init__(*args)

        self.scoreboardNumber = scoreboardNumber
        self.singlePlayer = singlePlayer

        StateManager.Set(f"score.{self.scoreboardNumber}", {})
        StateManager.Set(f"score.{self.scoreboardNumber}.last_sets.1", {})
        StateManager.Set(f"score.{self.scoreboardNumber}.last_sets.2", {})
        StateManager.Set(f"score.{self.scoreboardNumber}.history_sets.1", {})
        StateManager.Set(f"score.{self.scoreboardNumber}.history_sets.2", {})
        StateManager.Set(f"score.{self.scoreboardNumber}.station_queue", {})

        self.signals = AutonaScoreboardWidgetSignals()
        self.signals.UpdateSetData.connect(self.UpdateSetData)
        self.signals.NewSetSelected.connect(self.NewSetSelected)
        self.signals.StationSetsLoaded.connect(self.StationSetsLoaded)
        self.signals.SetSelection.connect(self.LoadSetClicked)
        self.signals.StationSelected.connect(self.LoadStationSets)
        self.signals.StationSelection.connect(self.LoadStationSetClicked)
        self.signals.UserSetSelection.connect(self.LoadUserSetClicked)
        self.signals.ChangeSetData.connect(self.ChangeSetData)

        AutonaHotkeys.signals.load_set.connect(self.LoadSetClicked)
        AutonaHotkeys.signals.swap_teams.connect(self.SwapTeams)
        AutonaHotkeys.signals.reset_scores.connect(self.ResetScore)

        AutonaHotkeys.signals.team1_score_up.connect(
            lambda: self.CommandScoreChange(0, 1))
        AutonaHotkeys.signals.team1_score_down.connect(
            lambda: self.CommandScoreChange(0, -1))
        AutonaHotkeys.signals.team2_score_up.connect(
            lambda: self.CommandScoreChange(1, 1))
        AutonaHotkeys.signals.team2_score_down.connect(
            lambda: self.CommandScoreChange(1, -1))

        self.signals.CommandScoreChange.connect(self.CommandScoreChange)
        self.signals.SwapTeams.connect(self.SwapTeams)
        self.signals.CommandTeamColor.connect(self.CommandTeamColor)

        self.stats = AutonaStatsUtil(self.scoreboardNumber, self)

        self.lastSetSelected = None

        self.lastStationSelected = None

        self.autoUpdateTimer: QTimer = None
        self.timeLeftTimer: QTimer = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setLayout(QVBoxLayout())
        # Narrow outer margins so the scroll viewport gets as much width
        # as possible — the column content is dense and the vertical
        # scrollbar already eats into the usable area.
        self.layout().setContentsMargins(1, UI.SPACE_XS, 1, UI.SPACE_XS)
        self.layout().setSpacing(UI.SPACE_XS)

        self.innerWidget = QWidget()
        self.innerWidget.setLayout(QVBoxLayout())
        self.innerWidget.layout().setContentsMargins(
            UI.SPACE_XS, UI.SPACE_SM, UI.SPACE_XS, UI.SPACE_SM)
        self.innerWidget.layout().setSpacing(UI.SPACE_SM)
        self.innerWidget.setMinimumWidth(0)
        self.innerWidget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.scrollArea = QScrollArea()
        self.scrollArea.setWidget(self.innerWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setFrameShape(QFrame.Shape.NoFrame)
        # Disallow horizontal scrolling — the layout should collapse into
        # the viewport width instead of letting team columns overflow to
        # the right.
        self.scrollArea.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollArea.setStyleSheet(
            "QTabWidget::pane { margin: 0px,0px,0px,0px }")

        self.layout().addWidget(self.scrollArea)

        topOptions = QWidget()
        topOptions.setLayout(QHBoxLayout())
        topOptions.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        topOptions.layout().setContentsMargins(0, 0, 0, 0)
        topOptions.layout().setSpacing(UI.SPACE_SM)

        self.innerWidget.layout().addWidget(topOptions)

        self.webScoreboardUrl = f"http://{self.GetIP()}:{SettingsManager.Get('general.webserver_port', 5000)}/scoreboard"
        self.btOpenWebScoreboard = QPushButton("ブラウザ版を開く")
        self.btOpenWebScoreboard.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btOpenWebScoreboard.clicked.connect(lambda: webbrowser.open(self.webScoreboardUrl))
        topOptions.layout().addWidget(self.btOpenWebScoreboard)

        topOptions.layout().addStretch()

        if not SettingsManager.Get("general.disable_thumbnail_widget", False):
            self.thumbnailBtn = QPushButton(
                QApplication.translate("app", "Generate Thumbnail") + " ")
            self.thumbnailBtn.setIcon(QIcon(UI.icon_path('png_file.svg')))
            self.thumbnailBtn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            topOptions.layout().addWidget(self.thumbnailBtn)
            self.thumbnailBtn.clicked.connect(self.GenerateThumbnail)
            self.thumbnailBtn.setVisible(False)

        self.xpostBtn = QPushButton(
            QApplication.translate("app", "Post to X") + " ")
        self.xpostBtn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        topOptions.layout().addWidget(self.xpostBtn)
        self.xpostBtn.clicked.connect(self.PostToX)

        self.eyeBt = QToolButton()
        self.eyeBt.setIcon(QIcon(UI.icon_path('eye.svg')))
        self.eyeBt.setSizePolicy(
            QSizePolicy.Maximum, QSizePolicy.Fixed)
        topOptions.layout().addWidget(self.eyeBt)
        self.eyeBt.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu()
        self.eyeBt.setMenu(menu)

        menu.addSection("Players")

        self.elements = [
            [QApplication.translate("app", "Twitter"),                ["twitter"],                           "show_social"],
            [QApplication.translate("app", "Seed"),                   ["seed", "seedLabel"],                 "show_seed"],
            [QApplication.translate("app", "Location"),               ["locationLabel", "state", "country"], "show_location"],
            [QApplication.translate("app", "Characters"),             ["characters"],                        "show_characters"],
            [QApplication.translate("app", "Pronouns"),               ["pronoun"],                           "show_pronouns"],
            [QApplication.translate("app", "Additional information"), ["custom_textbox"],                    "show_additional"],
        ]
        for element in self.elements:
            action: QAction = self.eyeBt.menu().addAction(element[0])
            action.setCheckable(True)
            action.setChecked(SettingsManager.Get(f"display_options.{element[2]}", True))
            action.toggled.connect(
                lambda toggled, action=action, element=element: self.ToggleElements(action, element[1]))

        self.playerWidgets: List[AutonaScoreboardPlayerWidget] = []
        self.team1playerWidgets: List[AutonaScoreboardPlayerWidget] = []
        self.team2playerWidgets: List[AutonaScoreboardPlayerWidget] = []

        self.team1swaps = []
        self.team2swaps = []

        self.columns = QWidget()
        self.columns.setLayout(QHBoxLayout())
        self.columns.layout().setContentsMargins(0, 0, 0, 0)
        self.columns.layout().setSpacing(UI.SPACE_SM)
        self.innerWidget.layout().addWidget(self.columns)

        # --- Stream / set loading section (above phase/match) ---
        self.streamSection = QWidget()
        self.streamSection.setLayout(QVBoxLayout())
        self.streamSection.layout().setContentsMargins(0, 0, 0, 0)
        self.streamSection.layout().setSpacing(UI.SPACE_SM)
        self.streamSection.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)

        if SettingsManager.Get("bsky_account.enable_bluesky", True):
            self.streamUrl = QHBoxLayout()
            self.streamUrl.setContentsMargins(0, 0, 0, 0)
            self.streamUrl.setSpacing(UI.SPACE_SM)
            self.streamUrlLabel = QLabel(QApplication.translate("app", "Stream URL") + " ")
            self.streamUrl.layout().addWidget(self.streamUrlLabel)
            self.streamUrlCombo = QComboBox()
            self.streamUrlCombo.setEditable(True)
            self.streamUrlCombo.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._loadStreamChannels()
            self.streamUrlCombo.currentIndexChanged.connect(self._onStreamChannelChanged)
            self.streamUrlCombo.lineEdit().editingFinished.connect(
                lambda: StateManager.Set(
                    f"score.{self.scoreboardNumber}.stream_url",
                    self.streamUrlCombo.currentData() or self.streamUrlCombo.currentText()))
            self.streamUrl.layout().addWidget(self.streamUrlCombo)
            self.addStreamChannelBtn = QPushButton("+")
            self.addStreamChannelBtn.setFixedWidth(28)
            self.addStreamChannelBtn.setToolTip(
                QApplication.translate("app", "Add stream channel"))
            self.addStreamChannelBtn.clicked.connect(self._addStreamChannel)
            self.streamUrl.layout().addWidget(self.addStreamChannelBtn)
            # Export initial value
            StateManager.Set(
                f"score.{self.scoreboardNumber}.stream_url",
                self.streamUrlCombo.currentData() or self.streamUrlCombo.currentText())
            self.streamSection.layout().addLayout(self.streamUrl)

        loadSetRow = QHBoxLayout()
        loadSetRow.setContentsMargins(0, 0, 0, 0)
        loadSetRow.setSpacing(UI.SPACE_SM)
        self.streamSection.layout().addLayout(loadSetRow)

        self.btSelectSet = QPushButton(
            QApplication.translate("app", "Load set"))
        self.btSelectSet.setIcon(QIcon(UI.icon_path("list.svg")))
        self.btSelectSet.setEnabled(False)
        loadSetRow.addWidget(self.btSelectSet)
        self.btSelectSet.clicked.connect(self.signals.SetSelection.emit)

        SettingsManager.Set("general.disable_autoupdate",
                            SettingsManager.Get("general.disable_autoupdate", True))

        self.autoUpdateToggle = QPushButton()
        self.autoUpdateToggle.setIcon(QIcon(UI.icon_path("update_circle.svg")))
        self.autoUpdateToggle.setCheckable(True)
        self.autoUpdateToggle.setChecked(
            not SettingsManager.Get("general.disable_autoupdate", True)
        )
        self.autoUpdateToggle.setFixedSize(UI.BTN_MD, UI.BTN_MD)
        self.autoUpdateToggle.setStyleSheet(UI.auto_update_toggle_style())
        self.autoUpdateToggle.toggled.connect(self.ToggleAutonaUpdate)
        loadSetRow.addWidget(self.autoUpdateToggle)

        self._autoUpdateLabel = QLabel(
            QApplication.translate("app", "5秒ごとに自動取得")
        )
        self._autoUpdateLabel.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._autoUpdateLabel.setStyleSheet(UI.secondary_label_style())
        loadSetRow.addWidget(self._autoUpdateLabel)

        UI.on_theme_change(self._refresh_theme_styles)

        # --- Separator (inserted into layout later via insertWidget) ---
        self.streamScoreSeparator = QFrame()
        self.streamScoreSeparator.setFrameShape(QFrame.Shape.HLine)
        self.streamScoreSeparator.setFrameShadow(QFrame.Shadow.Sunken)

        # --- Bottom options (settings, timer) below team columns ---
        bottomOptions = QWidget()
        bottomOptions.setLayout(QVBoxLayout())
        bottomOptions.layout().setContentsMargins(0, 0, 0, 0)
        bottomOptions.layout().setSpacing(UI.SPACE_SM)
        bottomOptions.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)

        self.innerWidget.layout().addWidget(bottomOptions)

        settingsRow = QHBoxLayout()
        settingsRow.setContentsMargins(0, 0, 0, 0)
        settingsRow.setSpacing(4)

        self.charNumber = QSpinBox()
        self.charNumber.setFixedWidth(120)
        settingsRow.addWidget(QLabel(QApplication.translate("app", "Characters per player")))
        settingsRow.addWidget(self.charNumber)
        self.charNumber.valueChanged.connect(self.SetCharacterNumber)

        self.playerNumber = QSpinBox()
        self.playerNumber.setFixedWidth(120)
        self._playerNumberLabel = QLabel(QApplication.translate("app", "Players per team"))
        settingsRow.addWidget(self._playerNumberLabel)
        settingsRow.addWidget(self.playerNumber)
        self.playerNumber.valueChanged.connect(self.SetPlayersPerTeam)

        bottomOptions.layout().addLayout(settingsRow)

        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            self.UpdateBottomButtons)
        AutonaTournamentDataProvider.instance.signals.tournament_changed.emit()

        self.selectSetWindow = AutonaSelectSetWindow(self)
        self.selectStationWindow = AutonaSelectStationWindow(self)

        self.timerLayout = QWidget()
        self.timerLayout.setLayout(QHBoxLayout())
        self.timerLayout.layout().setContentsMargins(0, 0, 0, 0)
        self.timerLayout.layout().setSpacing(UI.SPACE_SM)
        self.timerLayout.setSizePolicy(
            QSizePolicy.Minimum, QSizePolicy.Maximum)
        self.timerLayout.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.streamSection.layout().addWidget(self.timerLayout)
        self.labelAutonaUpdate = QLabel(QApplication.translate("app", "Auto update"))
        self.timerLayout.layout().addWidget(self.labelAutonaUpdate)
        self.timerTime = QLabel("0")
        self.timerLayout.layout().addWidget(self.timerTime)
        self.timerCancelBt = QPushButton()
        self.timerCancelBt.setIcon(QIcon(UI.icon_path('cancel.svg')))
        self.timerCancelBt.setIconSize(QSize(12, 12))
        self.timerCancelBt.clicked.connect(
            lambda: self.StopAutonaUpdate(clear_variables=True))
        self.timerLayout.layout().addWidget(self.timerCancelBt)
        self.timerLayout.setVisible(False)

        self.team1column = uic.loadUi(AutonaResolve("src/layout/AutonaScoreboardTeam.ui"))
        self.team1column.setMinimumWidth(0)
        self.team1column.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.columns.layout().addWidget(self.team1column)
        self.team1column.findChild(QLabel, "teamLabel").setText(
            QApplication.translate("app", "TEAM {0}").format(1))
        self.team1column.findChild(QLabel, "teamLabel").setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)

        colorGroup1 = QWidget()
        colorGroup1.setLayout(QHBoxLayout())
        colorGroup1.layout().setContentsMargins(0, 0, 0, 0)
        colorGroup1.layout().setSpacing(UI.SPACE_SM)
        colorGroup1.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        DEFAULT_TEAM1_COLOR = SettingsManager.Get("general.team_1_default_color", "#fe3636")
        self.colorButton1 = AutonaColorButton(color=DEFAULT_TEAM1_COLOR)
        # self.colorButton1.setText(QApplication.translate("app", "COLOR"))
        self.colorButton1.colorChanged.connect(
            lambda color: [
                StateManager.BlockSaving(),
                StateManager.Set(f"score.{self.scoreboardNumber}.team.1.color", color),
                StateManager.ReleaseSaving()
            ])
        self.CommandTeamColor(0, DEFAULT_TEAM1_COLOR)

        colorGroup1.layout().addWidget(self.colorButton1)

        self.team1column.findChild(QHBoxLayout, "horizontalLayout_2").layout().insertWidget(0, colorGroup1)
        self.team1column.findChild(QScrollArea).setWidget(QWidget())
        self.team1column.findChild(
            QScrollArea).widget().setLayout(QVBoxLayout())

        for c in self.team1column.findChildren(QLineEdit):
            c.editingFinished.connect(
                lambda element=c: [
                    StateManager.Set(
                        f"score.{self.scoreboardNumber}.team.1.{element.objectName()}", element.text())
                ])
            c.editingFinished.emit()

        for c in self.team1column.findChildren(QCheckBox):
            c.toggled.connect(
                lambda state, element=c: [
                    StateManager.Set(
                        f"score.{self.scoreboardNumber}.team.1.{element.objectName()}", state)
                ])
            c.toggled.emit(False)

        self.scoreColumn = uic.loadUi(AutonaResolve("src/layout/AutonaScoreboardScore.ui"))

        colorGroup2 = QWidget()
        colorGroup2.setLayout(QHBoxLayout())
        colorGroup2.layout().setContentsMargins(0, 0, 0, 0)
        colorGroup2.layout().setSpacing(UI.SPACE_SM)
        colorGroup2.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        self.team2column = uic.loadUi(AutonaResolve("src/layout/AutonaScoreboardTeam.ui"))
        self.team2column.setMinimumWidth(0)
        self.team2column.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.columns.layout().addWidget(self.team2column)
        self.team2column.findChild(QLabel, "teamLabel").setText(
            QApplication.translate("app", "TEAM {0}").format(2))
        self.team2column.findChild(QLabel, "teamLabel").setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)

        DEFAULT_TEAM2_COLOR = SettingsManager.Get("general.team_2_default_color", "#2e89ff")
        self.colorButton2 = AutonaColorButton(color=DEFAULT_TEAM2_COLOR)
        self.colorButton2.colorChanged.connect(
            lambda color: [
                StateManager.BlockSaving(),
                StateManager.Set(f"score.{self.scoreboardNumber}.team.2.color", color),
                StateManager.ReleaseSaving()
            ])
        # self.colorButton2.setText(QApplication.translate("app", "COLOR"))
        self.CommandTeamColor(1, DEFAULT_TEAM2_COLOR)

        colorGroup2.layout().addWidget(self.colorButton2)

        self.team2column.findChild(QHBoxLayout, "horizontalLayout_2").layout().insertWidget(0, colorGroup2)

        self.team2column.findChild(QScrollArea).setWidget(QWidget())
        self.team2column.findChild(
            QScrollArea).widget().setLayout(QVBoxLayout())

        for c in self.team2column.findChildren(QLineEdit):
            c.editingFinished.connect(
                lambda element=c: [
                    StateManager.Set(
                        f"score.{self.scoreboardNumber}.team.2.{element.objectName()}", element.text())
                ])
            c.editingFinished.emit()

        for c in self.team2column.findChildren(QCheckBox):
            c.toggled.connect(
                lambda state, element=c: [
                    StateManager.Set(
                        f"score.{self.scoreboardNumber}.team.2.{element.objectName()}", state)
                ])
            c.toggled.emit(False)

        StateManager.Unset(f'score.{self.scoreboardNumber}.team.1.player')
        StateManager.Unset(f'score.{self.scoreboardNumber}.team.2.player')
        StateManager.Unset(f'score.{self.scoreboardNumber}.stage_strike')
        self.playerNumber.setValue(1)
        self.charNumber.setValue(1)

        self._applyScoreboardMode(self.singlePlayer)

        for c in self.scoreColumn.findChildren(QComboBox):
            c.lineEdit().editingFinished.connect(
                lambda element=c: [
                    StateManager.Set(
                        f"score.{self.scoreboardNumber}.{element.objectName()}", element.currentText())
                ]
            )
            c.currentIndexChanged.connect(
                lambda x, element=c: [
                    StateManager.Set(
                        f"score.{self.scoreboardNumber}.{element.objectName()}", element.currentText())
                ]
            )
            c.lineEdit().editingFinished.emit()
            c.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Add stage order widget
        self.individualGameTracker = AutonaIndividualGameTracker(self.scoreboardNumber)
        self.individualGameTracker.signals.stageResultsUpdate.connect(self.StageResultsToScore)

        # Score bar: phase/match/bestof extracted from .ui, placed above columns
        self.scoreBar = QWidget()
        self.scoreBar.setLayout(QHBoxLayout())
        self.scoreBar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        self.scoreBar.layout().setContentsMargins(0, 0, 0, 0)
        self.scoreBar.layout().setSpacing(8)

        phaseGroup = QWidget()
        phaseGroup.setLayout(QHBoxLayout())
        phaseGroup.layout().setContentsMargins(0, 0, 0, 0)
        phaseGroup.layout().setSpacing(4)
        phaseLabel = self.scoreColumn.findChild(QLabel, "label")
        self.phaseCombo = self.scoreColumn.findChild(QComboBox, "phase")
        phaseCombo = self.phaseCombo
        phaseLabel.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        phaseGroup.layout().addWidget(phaseLabel)
        phaseGroup.layout().addWidget(phaseCombo, 1)
        self.scoreBar.layout().addWidget(phaseGroup, 2)

        matchGroup = QWidget()
        matchGroup.setLayout(QHBoxLayout())
        matchGroup.layout().setContentsMargins(0, 0, 0, 0)
        matchGroup.layout().setSpacing(4)
        matchLabel = self.scoreColumn.findChild(QLabel, "label_2")
        self.matchCombo = self.scoreColumn.findChild(QComboBox, "match")
        matchCombo = self.matchCombo
        matchLabel.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        matchGroup.layout().addWidget(matchLabel)
        matchGroup.layout().addWidget(matchCombo, 1)
        self.scoreBar.layout().addWidget(matchGroup, 2)

        bestOfGroup = QWidget()
        bestOfGroup.setLayout(QHBoxLayout())
        bestOfGroup.layout().setContentsMargins(0, 0, 0, 0)
        bestOfGroup.layout().setSpacing(4)
        bestOfLabel = self.scoreColumn.findChild(QLabel, "label_3")
        self.bestOfSpin = self.scoreColumn.findChild(QSpinBox, "best_of")
        bestOfSpin = self.bestOfSpin
        bestOfLabel.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        bestOfGroup.layout().addWidget(bestOfLabel)
        bestOfGroup.layout().addWidget(bestOfSpin)
        self.scoreBar.layout().addWidget(bestOfGroup, 1)

        columnsIndex = self.innerWidget.layout().indexOf(self.columns)
        # Insert order: streamSection, separator, scoreBar, then columns
        self.innerWidget.layout().insertWidget(columnsIndex, self.scoreBar)
        self.innerWidget.layout().insertWidget(columnsIndex, self.streamScoreSeparator)
        self.innerWidget.layout().insertWidget(columnsIndex, self.streamSection)
        self.scoreColumn.hide()

        # Score widget: buttons + displays built in Python
        self.scoreWidget = QWidget()
        self.scoreWidget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        scoreVBox = QVBoxLayout()
        scoreVBox.setContentsMargins(0, 0, 0, 0)
        scoreVBox.setSpacing(2)
        scoreVBox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scoreWidget.setLayout(scoreVBox)

        SZ = int(UI.BTN_LG * 1.25)
        SP = 4
        ICON = int(UI.ICON_SM * 1.25)
        scoreBtnStyle = UI.score_btn_style(scale=1.0)

        # Row 1: Swap
        row1 = QHBoxLayout()
        row1.setSpacing(SP)
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btSwap = QPushButton()
        self.btSwap.setIcon(QIcon(UI.icon_path("swap.svg")))
        self.btSwap.setIconSize(QSize(ICON, ICON))
        self.btSwap.setFixedSize(SZ * 2 + SP, SZ)
        self.btSwap.setToolTip(QApplication.translate("app", "Swap Teams"))
        self.btSwap.clicked.connect(self.SwapTeams)
        row1.addWidget(self.btSwap)
        scoreVBox.addLayout(row1)

        # Row 2: Score up
        row2 = QHBoxLayout()
        row2.setSpacing(SP)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btScoreUp1 = QPushButton("+1")
        self.btScoreUp1.setFixedSize(SZ, SZ)
        self.btScoreUp1.setStyleSheet(scoreBtnStyle)
        self.btScoreUp1.clicked.connect(lambda: self.CommandScoreChange(0, 1))
        self.btScoreUp2 = QPushButton("+1")
        self.btScoreUp2.setFixedSize(SZ, SZ)
        self.btScoreUp2.setStyleSheet(scoreBtnStyle)
        self.btScoreUp2.clicked.connect(lambda: self.CommandScoreChange(1, 1))
        row2.addWidget(self.btScoreUp1)
        row2.addWidget(self.btScoreUp2)
        scoreVBox.addLayout(row2)

        # Row 3: Score displays
        row3 = QHBoxLayout()
        row3.setSpacing(SP)
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scoreDisplay1 = self.scoreColumn.findChild(QSpinBox, "score_left")
        self.scoreDisplay1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scoreDisplay1.setFixedSize(SZ, SZ)
        self.scoreDisplay1.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.scoreDisplay1.setStyleSheet(UI.score_display_style(scale=1.15))
        self.scoreDisplay2 = self.scoreColumn.findChild(QSpinBox, "score_right")
        self.scoreDisplay2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scoreDisplay2.setFixedSize(SZ, SZ)
        self.scoreDisplay2.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.scoreDisplay2.setStyleSheet(UI.score_display_style(scale=1.15))
        row3.addWidget(self.scoreDisplay1)
        row3.addWidget(self.scoreDisplay2)
        scoreVBox.addLayout(row3)

        # Row 4: Score down
        row4 = QHBoxLayout()
        row4.setSpacing(SP)
        row4.setContentsMargins(0, 0, 0, 0)
        row4.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btScoreDown1 = QPushButton("-1")
        self.btScoreDown1.setFixedSize(SZ, SZ)
        self.btScoreDown1.setStyleSheet(scoreBtnStyle)
        self.btScoreDown1.clicked.connect(lambda: self.CommandScoreChange(0, -1))
        self.btScoreDown2 = QPushButton("-1")
        self.btScoreDown2.setFixedSize(SZ, SZ)
        self.btScoreDown2.setStyleSheet(scoreBtnStyle)
        self.btScoreDown2.clicked.connect(lambda: self.CommandScoreChange(1, -1))
        row4.addWidget(self.btScoreDown1)
        row4.addWidget(self.btScoreDown2)
        scoreVBox.addLayout(row4)

        # Row 5: Score reset
        row5 = QHBoxLayout()
        row5.setSpacing(SP)
        row5.setContentsMargins(0, 0, 0, 0)
        row5.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btScoreReset = QPushButton()
        self.btScoreReset.setIcon(QIcon(UI.icon_path("cancel.svg")))
        self.btScoreReset.setIconSize(QSize(ICON, ICON))
        self.btScoreReset.setFixedSize(SZ * 2 + SP, SZ)
        self.btScoreReset.setToolTip(QApplication.translate("app", "Score Reset"))
        self.btScoreReset.clicked.connect(lambda: self.ResetScore())
        row5.addWidget(self.btScoreReset)
        scoreVBox.addLayout(row5)

        self.columns.layout().insertWidget(1, self.scoreWidget)

        self.innerWidget.layout().removeWidget(self.scoreColumn)
        self.scoreColumn.findChild(QGroupBox, "scoreGroupBox").setTitle("")

        self.bestOfSpin.valueChanged.connect(
            lambda value: [
                StateManager.BlockSaving(),
                StateManager.Set(
                    f"score.{self.scoreboardNumber}.best_of", value),
                StateManager.Set(
                    f"score.{self.scoreboardNumber}.best_of_short_text", f"BO{value}"),
                StateManager.Set(f"score.{self.scoreboardNumber}.best_of_text", AutonaLocaleHelper.matchNames.get(
                    "best_of").format(value) if value > 0 else ""),
                StateManager.Set(
                    f"score.{self.scoreboardNumber}.first_to", math.ceil(value/2)),
                StateManager.Set(
                    f"score.{self.scoreboardNumber}.first_to_short_text", f"FT{math.ceil(value/2)}"),
                StateManager.Set(f"score.{self.scoreboardNumber}.first_to_text", AutonaLocaleHelper.matchNames.get(
                    "first_to").format(math.ceil(value/2)) if value > 0 else ""),
                self.individualGameTracker.SetStageCount(value),
                StateManager.ReleaseSaving()
            ]
        )
        self.bestOfSpin.valueChanged.emit(0)

        self.scoreDisplay1.valueChanged.connect(
            lambda value: [
                self.individualGameTracker.UpdateScore(0, value)
            ]
        )
        self.scoreDisplay1.valueChanged.emit(0)

        self.scoreDisplay2.valueChanged.connect(
            lambda value: [
                self.individualGameTracker.UpdateScore(1, value)
            ]
        )
        self.scoreDisplay2.valueChanged.emit(0)

        self.team1column.findChild(QLineEdit, "teamName").editingFinished.connect(
            lambda: self.ExportTeamLogo(
                "1", self.team1column.findChild(QLineEdit, "teamName").text())
        )
        self.team2column.findChild(QLineEdit, "teamName").editingFinished.connect(
            lambda: self.ExportTeamLogo(
                "2", self.team2column.findChild(QLineEdit, "teamName").text())
        )

        self.teamsSwapped = False

        # Add default and user tournament phase title files
        self.phaseCombo.addItem("")

        for key in AutonaLocaleHelper.phaseNames.keys():
            phaseString = AutonaLocaleHelper.phaseNames[key]

            if "{0}" in phaseString:
                if "top" not in key:
                    for letter in ["A", "B", "C", "D"]:
                        if self.phaseCombo.findText(phaseString.format(letter)) < 0:
                            self.phaseCombo.addItem(phaseString.format(letter))
            else:
                if self.phaseCombo.findText(phaseString) < 0:
                    self.phaseCombo.addItem(phaseString)

        self.matchCombo.addItem("")

        for key in AutonaLocaleHelper.matchNames.keys():
            matchString = AutonaLocaleHelper.matchNames[key]

            try:
                if "{0}" in matchString and ("qualifier" in key):
                    couples = [
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(8), AutonaLocaleHelper.matchNames.get("qualifier_winners_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(16), AutonaLocaleHelper.matchNames.get("qualifier_winners_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(32), AutonaLocaleHelper.matchNames.get("qualifier_winners_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(6), AutonaLocaleHelper.matchNames.get("qualifier_losers_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(8), AutonaLocaleHelper.matchNames.get("qualifier_losers_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(12), AutonaLocaleHelper.matchNames.get("qualifier_losers_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(16), AutonaLocaleHelper.matchNames.get("qualifier_losers_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(24), AutonaLocaleHelper.matchNames.get("qualifier_losers_indicator")),
                        (AutonaLocaleHelper.phaseNames.get("top_n").format(32), AutonaLocaleHelper.matchNames.get("qualifier_losers_indicator"))
                    ]

                    for couple in couples:
                        print(couple)
                        self.matchCombo.addItem(matchString.format(*couple))
                elif "{0}" in matchString and ("qualifier" not in key):
                    for number in range(5):
                        if key == "best_of":
                            if self.matchCombo.findText(matchString.format(str(2*number+1))) < 0:
                                self.matchCombo.addItem(matchString.format(str(2*number+1)))
                        else:
                            if self.matchCombo.findText(matchString.format(str(number+1))) < 0:
                                self.matchCombo.addItem(matchString.format(str(number+1)))
                elif "indicator" in key:
                    pass
                else:
                    if self.matchCombo.findText(matchString) < 0:
                        self.matchCombo.addItem(matchString)
            except:
                logger.error(
                    f"Unable to generate match strings for {matchString}")

        AutonaGameAssetManager.instance.signals.onLoad.connect(
            lambda: [
                self.SetDefaultsFromAssets(),
                self.bestOfSpin.valueChanged.emit(self.bestOfSpin.value()),
            ]
        )

        self.scoreColumn.findChild(QVBoxLayout, "verticalLayout").addWidget(self.individualGameTracker)


    def StageResultsToScore(self, team_1_score, team_2_score):
        with QSignalBlocker(self.scoreDisplay1):
            self.scoreDisplay1.setValue(team_1_score)
            StateManager.Set(f"score.{self.scoreboardNumber}.team.1.score", team_1_score)

        with QSignalBlocker(self.scoreDisplay2):
            self.scoreDisplay2.setValue(team_2_score)
            StateManager.Set(f"score.{self.scoreboardNumber}.team.2.score", team_2_score)

    def closeEvent(self, event):
        self.autoUpdateTimer.stop()
        self.timeLeftTimer.stop()

    def _applyScoreboardMode(self, singlePlayer: bool):
        # Toggle team-centric UI. In single-player mode, collapse team naming
        # and player-count controls; in team mode, restore the defaults.
        # Also drop the player-wrapping QGroupBox chrome so the solo player
        # isn't double-nested inside a redundant team frame.
        self.singlePlayer = singlePlayer
        for column, side in ((self.team1column, 1), (self.team2column, 2)):
            teamLabel = column.findChild(QLabel, "teamLabel")
            if teamLabel is not None:
                if singlePlayer:
                    teamLabel.setText(
                        QApplication.translate("app", "{0}P").format(side))
                else:
                    teamLabel.setText(
                        QApplication.translate("app", "TEAM {0}").format(side))
            teamName = column.findChild(QLineEdit, "teamName")
            if teamName is not None:
                teamName.setVisible(not singlePlayer)

            groupBox = column.findChild(QGroupBox, "groupBox")
            if groupBox is not None:
                if singlePlayer:
                    # Strip both the border AND the bg_secondary fill that the
                    # global QGroupBox QSS applies — otherwise the solo
                    # player sits inside a redundant dark panel.
                    groupBox.setStyleSheet(
                        "QGroupBox { border: none; background: transparent;"
                        " margin: 0; padding: 0; }")
                else:
                    groupBox.setStyleSheet(
                        "QGroupBox { padding-top: 0px; margin-top: 0px; }")
                scrollArea = groupBox.findChild(QScrollArea)
                if scrollArea is not None:
                    scrollArea.setFrameShape(
                        QFrame.Shape.NoFrame if singlePlayer
                        else QFrame.Shape.Panel)
                    # Match the parent's transparency so no second fill leaks.
                    scrollArea.setStyleSheet(
                        "QScrollArea { background: transparent; border: none; }"
                        " QScrollArea > QWidget > QWidget { background: transparent; }"
                        if singlePlayer else "")
        if getattr(self, "_playerNumberLabel", None) is not None:
            self._playerNumberLabel.setVisible(not singlePlayer)
        self.playerNumber.setVisible(not singlePlayer)
        if singlePlayer:
            self.playerNumber.setValue(1)

    def ExportTeamLogo(self, team, value):
        if os.path.exists(f"./user_data/team_logo/{value.lower()}.png"):
            StateManager.Set(f"score.{self.scoreboardNumber}.team.{team}.logo",
                             f"./user_data/team_logo/{value.lower()}.png")
        else:
            StateManager.Set(
                f"score.{self.scoreboardNumber}.team.{team}.logo", None)

    def GenerateThumbnail(self, quiet_mode=False, disable_msgbox=False):
        if not disable_msgbox:
            msgBox = QMessageBox()
            msgBox.setWindowIcon(QIcon(UI.icon_path('icon.png')))
            msgBox.setWindowTitle(QApplication.translate(
                "thumb_app", "Autona - Thumbnail"))
        try:
            thumbnailPath = thumbnail.generate(
                settingsManager=SettingsManager, scoreboardNumber=self.scoreboardNumber)
            if not disable_msgbox:
                msgBox.setText(QApplication.translate(
                    "thumb_app", "The thumbnail has been generated here:") + " " + thumbnailPath + "\n\n" + QApplication.translate(
                    "thumb_app", "The video title and description have also been generated."))
                msgBox.setIcon(QMessageBox.NoIcon)
                # msgBox.setInformativeText(thumbnailPath)

            thumbnail_settings = SettingsManager.Get("thumbnail_config")
            if not quiet_mode:
                if thumbnail_settings.get("open_explorer"):
                    outThumbDir = f"{os.getcwd()}/out/thumbnails/"
                    if platform.system() == "Windows":
                        thumbnailPath = thumbnailPath[2:].replace("/", "\\")
                        outThumbDir = f"{os.getcwd()}\\{thumbnailPath}"
                        # os.startfile(outThumbDir)
                        subprocess.Popen(r'explorer /select,"'+outThumbDir+'"')
                    elif platform.system() == "Darwin":
                        subprocess.Popen(["open", outThumbDir])
                    else:
                        subprocess.Popen(["xdg-open", outThumbDir])
                else:
                    if not disable_msgbox:
                        msgBox.exec()
            else:
                return(thumbnailPath)
        except Exception as e:
            # Log the full traceback so silent failures (e.g. template
            # prepare_data errors, renderer timeouts) can be diagnosed
            # without reproducing the UI flow.
            logger.exception("GenerateThumbnail failed")
            if not disable_msgbox:
                msgBox.setText(QApplication.translate("app", "Warning"))
                msgBox.setInformativeText(str(e))
                msgBox.setIcon(QMessageBox.Warning)
                msgBox.exec()
            else:
                raise e
    
    # --- Stream channel management ---

    def _loadStreamChannels(self):
        """Populate the stream URL combo from saved channels."""
        self.streamUrlCombo.blockSignals(True)
        self.streamUrlCombo.clear()
        self.streamUrlCombo.addItem("", "")  # empty default
        channels = SettingsManager.Get("stream_channels", [])
        for ch in channels:
            self.streamUrlCombo.addItem(ch.get("name", ""), ch.get("url", ""))
        self.streamUrlCombo.blockSignals(False)

    def _onStreamChannelChanged(self, index):
        url = self.streamUrlCombo.currentData() or self.streamUrlCombo.currentText()
        StateManager.Set(f"score.{self.scoreboardNumber}.stream_url", url)

    def _addStreamChannel(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(QApplication.translate("app", "Add stream channel"))
        dialog.setMinimumWidth(350)
        layout = QFormLayout(dialog)

        nameEdit = QLineEdit()
        nameEdit.setPlaceholderText("BST Stream")
        layout.addRow(QApplication.translate("app", "Channel name"), nameEdit)

        urlEdit = QLineEdit()
        urlEdit.setPlaceholderText("https://www.youtube.com/@B_ST_Stream/live")
        layout.addRow("URL", urlEdit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = nameEdit.text().strip()
            url = urlEdit.text().strip()
            if name and url:
                channels = SettingsManager.Get("stream_channels", [])
                channels.append({"name": name, "url": url})
                SettingsManager.Set("stream_channels", channels)
                self._loadStreamChannels()
                # Select the newly added channel
                self.streamUrlCombo.setCurrentIndex(self.streamUrlCombo.count() - 1)

    def PostToBsky(self):
        thumbnailPath = self.GenerateThumbnail(quiet_mode=True)
        if thumbnailPath:
            msgBox = QMessageBox()
            msgBox.setWindowIcon(QIcon(UI.icon_path('icon.png')))
            msgBox.setWindowTitle(QApplication.translate(
                "app", "Autona - Bluesky"))

            try:
                post_to_bsky(scoreboardNumber=self.scoreboardNumber, image_path=thumbnailPath.replace(".png", ".jpg"))
                username = SettingsManager.Get("bsky_account", {}).get("username")
                msgBox.setText(QApplication.translate("app", "The post has successfully been sent to account {0}").format(username))
                msgBox.setIcon(QMessageBox.NoIcon)
                msgBox.exec()
            except Exception as e:
                msgBox.setText(QApplication.translate("app", "Warning"))
                msgBox.setInformativeText(str(e))
                msgBox.setIcon(QMessageBox.Warning)
                msgBox.exec()
            for rm_path in [thumbnailPath, thumbnailPath.replace(".png", ".jpg"), thumbnailPath.replace(".png", "_desc.txt"), thumbnailPath.replace(".png", "_title.txt")]:
                if os.path.exists(rm_path):
                    os.remove(rm_path)
    
    def PostToX(self):
        try:
            n = self.scoreboardNumber

            tournament_name = StateManager.Get("tournamentInfo.tournamentName", "") or ""
            phase = StateManager.Get(f"score.{n}.phase", "") or ""
            top_n = StateManager.Get(f"score.{n}.top_n", "") or ""
            match = StateManager.Get(f"score.{n}.match", "") or ""
            stream_url = StateManager.Get(f"score.{n}.stream_url", "") or ""

            # Validate required fields: player names (both teams) and round name.
            def _team_has_name(team_n):
                team_state = StateManager.Get(f"score.{n}.team.{team_n}.player") or {}
                for k in team_state.keys():
                    name = (StateManager.Get(f"score.{n}.team.{team_n}.player.{k}.name") or "").strip()
                    if name:
                        return True
                return False

            missing = []
            if not _team_has_name(1):
                missing.append(QApplication.translate("app", "Team 1 player name"))
            if not _team_has_name(2):
                missing.append(QApplication.translate("app", "Team 2 player name"))
            if not (phase.strip() or top_n.strip() or match.strip()):
                missing.append(QApplication.translate("app", "Round name"))

            if missing:
                msgBox = QMessageBox()
                msgBox.setWindowIcon(QIcon(UI.icon_path('icon.png')))
                msgBox.setWindowTitle(QApplication.translate("app", "Post to X"))
                msgBox.setText(QApplication.translate(
                    "app",
                    "Cannot post to X: the following fields are empty."))
                msgBox.setInformativeText("\n".join(f"- {m}" for m in missing))
                msgBox.setIcon(QMessageBox.Warning)
                msgBox.exec()
                return

            # Generate thumbnail and copy to clipboard
            thumbnailPath = self.GenerateThumbnail(quiet_mode=True, disable_msgbox=True)
            if thumbnailPath:
                image = QImage(thumbnailPath)
                if not image.isNull():
                    QApplication.clipboard().setImage(image)

            def get_team_line(team_n):
                players = []
                team_state = StateManager.Get(f"score.{n}.team.{team_n}.player") or {}
                for k in sorted(team_state.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
                    name = (StateManager.Get(f"score.{n}.team.{team_n}.player.{k}.name") or "").strip()
                    if not name:
                        continue
                    seed = StateManager.Get(f"score.{n}.team.{team_n}.player.{k}.seed")
                    chars = StateManager.Get(f"score.{n}.team.{team_n}.player.{k}.character") or {}
                    char_names = []
                    for ck in sorted(chars.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
                        char_name = chars[ck].get("name", "")
                        if not char_name:
                            continue
                        char_names.append(char_name)
                    entry = name
                    if char_names:
                        entry += f"({'/'.join(char_names)})"
                    if seed:
                        entry += f" [Seed:{seed}]"
                    players.append(entry)
                return " / ".join(players) if players else "???"

            round_parts = []
            if phase:
                round_parts.append(f"[{phase}]")
            if top_n:
                round_parts.append(top_n)
            round_line = " ".join(round_parts)
            if match:
                if round_line:
                    round_line += f" - {match}"
                else:
                    round_line = match

            parts = []
            if tournament_name:
                parts.append(tournament_name)
            if round_line:
                parts.append(round_line)
            parts.append("")
            parts.append(get_team_line(1))
            parts.append("vs")
            parts.append(get_team_line(2))
            parts.append("")
            if stream_url:
                parts.append(f"\U0001f534STREAM: {stream_url}")

            text = "\n".join(parts)
            url = "https://twitter.com/intent/tweet?text=" + urllib.parse.quote(text)
            webbrowser.open(url)
        except Exception as e:
            logger.error(f"PostToX error: {e}")

    def ToggleElements(self, action: QAction, elements):
        for pw in self.playerWidgets:
            for element in elements:
                pw.findChild(QWidget, element).setVisible(action.isChecked())

    def UpdateBottomButtons(self):
        if AutonaTournamentDataProvider.instance.provider and AutonaTournamentDataProvider.instance.provider.url:
            url = AutonaTournamentDataProvider.instance.provider.url
            if "start.gg" in url:
                providerName = "Start.gg"
            else:
                providerName = url.split("/")[2] if "/" in url else url
            self.btSelectSet.setText(
                QApplication.translate("app", "Load set from {0}").format(providerName))
            self.btSelectSet.setEnabled(True)
        else:
            self.btSelectSet.setText(
                QApplication.translate("app", "Load set"))
            self.btSelectSet.setEnabled(False)

    def SetCharacterNumber(self, value):
        # logger.info(f"AutonaScoreboardWidget#SetCharacterNumber({value})")
        for pw in self.playerWidgets:
            pw.SetCharactersPerPlayer(value)

    def SetPlayersPerTeam(self, number):
        # logger.info(f"AutonaScoreboardWidget#SetPlayersPerTeam({number})")
        while len(self.team1playerWidgets) < number:
            p = AutonaScoreboardPlayerWidget(
                index=len(self.team1playerWidgets)+1,
                teamNumber=1,
                path=f'score.{self.scoreboardNumber}.team.{1}.player.{len(self.team1playerWidgets)+1}')
            self.playerWidgets.append(p)

            self.team1column.findChild(
                QScrollArea).widget().layout().addWidget(p)
            p.SetCharactersPerPlayer(self.charNumber.value())
            self.team1column.findChild(
                QCheckBox, "losers").toggled.connect(p.SetLosers)

            p.btMoveUp.clicked.connect(lambda index, p=p: p.SwapWith(
                self.team1playerWidgets[max(0, self.team1playerWidgets.index(p) - 1)]))
            p.btMoveDown.clicked.connect(lambda index, p=p: p.SwapWith(
                self.team1playerWidgets[min(len(self.team1playerWidgets) - 1, self.team1playerWidgets.index(p) + 1)]))

            p.instanceSignals.playerId_changed.connect(
                self.stats.signals.RecentSetsSignal.emit)
            p.instanceSignals.player1Id_changed.connect(
                self.stats.signals.LastSetsP1Signal.emit)
            p.instanceSignals.player1Id_changed.connect(
                self.stats.signals.PlayerHistoryStandingsP1Signal.emit)
            p.instanceSignals.player_seed_changed.connect(
                self.stats.signals.UpsetFactorCalculation.emit)

            self.team1playerWidgets.append(p)

            p = AutonaScoreboardPlayerWidget(
                index=len(self.team2playerWidgets)+1,
                teamNumber=2,
                path=f'score.{self.scoreboardNumber}.team.{2}.player.{len(self.team2playerWidgets)+1}')
            self.playerWidgets.append(p)

            self.team2column.findChild(
                QScrollArea).widget().layout().addWidget(p)
            p.SetCharactersPerPlayer(self.charNumber.value())
            self.team2column.findChild(
                QCheckBox, "losers").toggled.connect(p.SetLosers)

            p.btMoveUp.clicked.connect(lambda index, p=p: p.SwapWith(
                self.team2playerWidgets[max(0, self.team2playerWidgets.index(p) - 1)]))
            p.btMoveDown.clicked.connect(lambda index, p=p: p.SwapWith(
                self.team2playerWidgets[min(len(self.team2playerWidgets) - 1, self.team2playerWidgets.index(p) + 1)]))

            p.instanceSignals.playerId_changed.connect(
                self.stats.signals.RecentSetsSignal.emit)
            p.instanceSignals.player2Id_changed.connect(
                self.stats.signals.LastSetsP2Signal.emit)
            p.instanceSignals.player2Id_changed.connect(
                self.stats.signals.PlayerHistoryStandingsP2Signal.emit)
            p.instanceSignals.player_seed_changed.connect(
                self.stats.signals.UpsetFactorCalculation.emit)

            self.team2playerWidgets.append(p)

        while len(self.team1playerWidgets) > number:
            team1player = self.team1playerWidgets[-1]
            StateManager.Unset(team1player.path)
            team1player.setParent(None)
            self.playerWidgets.remove(team1player)
            self.team1playerWidgets.remove(team1player)
            team1player.deleteLater()

            team2player = self.team2playerWidgets[-1]
            StateManager.Unset(team2player.path)
            team2player.setParent(None)
            self.playerWidgets.remove(team2player)
            self.team2playerWidgets.remove(team2player)
            team2player.deleteLater()

        for team in [1, 2]:
            if StateManager.Get(f'score.{self.scoreboardNumber}.team.{team}'):
                for k in list(StateManager.Get(f'score.{self.scoreboardNumber}.team.{team}.player').keys()):
                    if int(k) > number:
                        StateManager.Unset(
                            f'score.{self.scoreboardNumber}.team.{team}.player.{k}')

        multiPlayer = number > 1

        if multiPlayer:
            self.team1column.findChild(QLineEdit, "teamName").setVisible(True)
            self.team2column.findChild(QLineEdit, "teamName").setVisible(True)
            self.team1column.findChild(QLabel, "teamLabel").setVisible(False)
            self.team2column.findChild(QLabel, "teamLabel").setVisible(False)
        else:
            self.team1column.findChild(QLineEdit, "teamName").setVisible(False)
            self.team1column.findChild(QLineEdit, "teamName").setText("")
            self.team1column.findChild(
                QLineEdit, "teamName").editingFinished.emit()
            self.team2column.findChild(QLineEdit, "teamName").setVisible(False)
            self.team2column.findChild(QLineEdit, "teamName").setText("")
            self.team2column.findChild(
                QLineEdit, "teamName").editingFinished.emit()
            self.team1column.findChild(QLabel, "teamLabel").setVisible(True)
            self.team2column.findChild(QLabel, "teamLabel").setVisible(True)

        for pw in self.playerWidgets:
            pw.findChild(QLabel, "title").setVisible(multiPlayer)
            pw.btMoveUp.setVisible(multiPlayer)
            pw.btMoveDown.setVisible(multiPlayer)

        for x, element in enumerate(self.elements, start=1):
            action: QAction = self.eyeBt.menu().actions()[x]
            self.ToggleElements(action, element[1])

        for pw in self.playerWidgets:
            for name in ('real_name', 'birthday', 'controller', 'controllerLabel', 'twitterLabel'):
                w = pw.findChild(QWidget, name)
                if w:
                    w.hide()

    def SwapTeams(self):
        StateManager.BlockSaving()

        # Lock all player widgets
        for p in self.playerWidgets:
            p.dataLock.acquire()

        try:
            for i, p in enumerate(self.team1playerWidgets):
                p.SwapWith(self.team2playerWidgets[i])

            # Scores
            scoreLeft = self.scoreDisplay1.value()
            self.scoreDisplay1.setValue(self.scoreDisplay2.value())
            self.scoreDisplay2.setValue(scoreLeft)

            # Losers
            losersLeft = self.team1column.findChild(
                QCheckBox, "losers").isChecked()
            self.team1column.findChild(QCheckBox, "losers").setChecked(
                self.team2column.findChild(QCheckBox, "losers").isChecked())
            self.team2column.findChild(
                QCheckBox, "losers").setChecked(losersLeft)

            # Team Names
            teamNameLeft = self.team1column.findChild(
                QLineEdit, "teamName").text()
            self.team1column.findChild(QLineEdit, "teamName").setText(
                self.team2column.findChild(QLineEdit, "teamName").text())
            self.team2column.findChild(
                QLineEdit, "teamName").setText(teamNameLeft)
            self.team1column.findChild(
                QLineEdit, "teamName").editingFinished.emit()
            self.team2column.findChild(
                QLineEdit, "teamName").editingFinished.emit()
            
            self.individualGameTracker.SwapStageResults()
            self.teamsSwapped = not self.teamsSwapped
        finally:
            StateManager.Set(
                f"score.{self.scoreboardNumber}.teamsSwapped", self.teamsSwapped)

            for p in self.playerWidgets:
                p.dataLock.release()

            StateManager.ReleaseSaving()

    def ResetScore(self):
        self.scoreDisplay1.setValue(0)
        self.scoreDisplay2.setValue(0)

    def ToggleAutonaUpdate(self, checked):
        SettingsManager.Set("general.disable_autoupdate", not checked)
        if not checked:
            self.StopAutonaUpdate(clear_variables=True)

    def _refresh_theme_styles(self):
        self.autoUpdateToggle.setStyleSheet(UI.auto_update_toggle_style())
        self._autoUpdateLabel.setStyleSheet(UI.secondary_label_style())
        self.scoreDisplay1.setStyleSheet(UI.score_display_style(scale=1.15))
        self.scoreDisplay2.setStyleSheet(UI.score_display_style(scale=1.15))
        for pw in self.playerWidgets:
            pw.refresh_theme_styles()

    def AutonaUpdate(self, data):
        AutonaTournamentDataProvider.instance.GetMatch(
            self, data.get("id"), overwrite=False)
        AutonaTournamentDataProvider.instance.GetStreamQueue()

    def StationSetsLoaded(self, data):
        # Ici peut être lancer le chargement des sets voire même trigger un autre signal ?

        logger.info("STATION SETS LOADED -----------------------------")
        logger.info(data)
        StateManager.BlockSaving()

        StateManager.Set(f"score.{self.scoreboardNumber}.station_queue", data)

        StateManager.ReleaseSaving()

    def NewSetSelected(self, data):
        if not SettingsManager.Get("general.disable_autoupdate", False):
            self.StopAutonaUpdate()
            self.autoUpdateTimer = QTimer()
            self.autoUpdateTimer.start(5000)
            self.timeLeftTimer = QTimer()
            self.timeLeftTimer.start(100)
            self.timeLeftTimer.timeout.connect(self.UpdateTimeLeftTimer)
            self.timerLayout.setVisible(True)

            if data.get("auto_update") == "set":
                self.labelAutonaUpdate.setText(
                    QApplication.translate("app", "Auto update (Set)")
                    )
            elif data.get("auto_update") == "stream":
                self.labelAutonaUpdate.setText(
                    QApplication.translate("app", "Auto update (Stream [{0}])").format(self.lastStationSelected.get('identifier'))
                    )
            elif data.get("auto_update") == "station":
                self.labelAutonaUpdate.setText(
                    QApplication.translate("app", "Auto update (Station [{0}])").format(self.lastStationSelected.get('identifier'))
                    )
            elif data.get("auto_update") == "user":
                self.labelAutonaUpdate.setText(QApplication.translate("app", "Auto update (User)"))
            else:
                self.labelAutonaUpdate.setText(QApplication.translate("app", "Auto update"))

        # Lock all player widgets
        for p in self.playerWidgets:
            p.dataLock.acquire()
        StateManager.BlockSaving()

        try:
            AutonaTournamentDataProvider.instance.GetStreamQueue()

            if data.get("id") != None and data.get("id") != self.lastSetSelected:
                # Clear previous scores
                # Important because when we receive scores as 0 we don't update based on that
                # Otherwise an offline set which is only updated after it's complete would reset the score
                # all the time since it would be 0-0 until then
                self.CommandClearAll(no_mains=data.get(
                    "no_mains") if data.get("no_mains") != None else False)
                self.ClearScore()

                # A new set was loaded
                self.lastSetSelected = data.get("id")

                # Clear stage strike data
                StateManager.Unset(
                    f'score.{self.scoreboardNumber}.stage_strike')

                # Add general set data to object: id, auto update type, station/stream identifier, etc
                StateManager.Set(
                    f'score.{self.scoreboardNumber}.set_id', data.get("id"))

                StateManager.Set(
                    f'score.{self.scoreboardNumber}.auto_update', data.get("auto_update"))

                if self.lastStationSelected:
                    StateManager.Set(
                        f'score.{self.scoreboardNumber}.station', self.lastStationSelected.get('identifier'))
                else:
                    StateManager.Set(
                        f'score.{self.scoreboardNumber}.station', None)

                # Force user to be P1 on set change
                if data.get("auto_update") == "user":
                    if (data.get("reverse") and not self.teamsSwapped) or \
                            not data.get("reverse") and self.teamsSwapped:
                        self.teamsSwapped = not self.teamsSwapped
                        StateManager.Set(
                            f"score.{self.scoreboardNumber}.teamsSwapped", self.teamsSwapped)

                AutonaTournamentDataProvider.instance.GetMatch(
                    self, data["id"], overwrite=True, no_mains=data.get("no_mains") if data.get("no_mains") != None else False)

            if not SettingsManager.Get("general.disable_autoupdate", False):
                self.autoUpdateTimer.timeout.connect(
                    lambda setId=data: self.AutonaUpdate(data))

                if data.get("auto_update") in ("stream", "station"):
                    self.autoUpdateTimer.timeout.connect(
                        lambda setId=data: AutonaTournamentDataProvider.instance.LoadStationSets(self))

                if data.get("auto_update") == "user":
                    self.autoUpdateTimer.timeout.connect(
                        lambda setId=data: AutonaTournamentDataProvider.instance.LoadUserSet(
                            self, SettingsManager.Get(AutonaTournamentDataProvider.instance.provider.name+"_user")))
        finally:
            for p in self.playerWidgets:
                p.dataLock.release()
            StateManager.ReleaseSaving()

    def StopAutonaUpdate(self, clear_variables=False):
        if self.autoUpdateTimer != None:
            self.autoUpdateTimer.stop()
            self.autoUpdateTimer = None
        if self.timeLeftTimer != None:
            self.timeLeftTimer.stop()
            self.timeLeftTimer = None

        if clear_variables:
            self.lastSetSelected = None
            self.lastStationSelected = None

            StateManager.Set(
                f'score.{self.scoreboardNumber}.auto_update', None)
            StateManager.Set(f'score.{self.scoreboardNumber}.set_id', None)
            StateManager.Set(f'score.{self.scoreboardNumber}.station', None)

        self.timerLayout.setVisible(False)

    def UpdateTimeLeftTimer(self):
        if self.autoUpdateTimer:
            self.timerTime.setText(
                str(int(self.autoUpdateTimer.remainingTime()/1000)))

    def LoadSetClicked(self):
        self.selectSetWindow.LoadSets()
        self.selectSetWindow.show()

    def LoadStationSets(self, station):
        self.lastSetSelected = None
        self.lastStationSelected = station
        AutonaTournamentDataProvider.instance.LoadStationSets(self)

    def LoadStationSetClicked(self):
        self.selectStationWindow.LoadStations()
        self.selectStationWindow.show()

    def UpdateUserSetButton(self):
        provider = None
        if AutonaTournamentDataProvider.instance.provider:
            provider = AutonaTournamentDataProvider.instance.provider.name
        if provider and SettingsManager.Get(provider+"_user"):
            self.btLoadPlayerSet.setText(
                QApplication.translate("app", "Load user set ({0})").format(SettingsManager.Get(provider+'_user')))
            self.btLoadPlayerSet.setEnabled(True)
        else:
            self.btLoadPlayerSet.setText(
                QApplication.translate("app", "Load user set"))
            self.btLoadPlayerSet.setEnabled(False)

    def LoadUserSetClicked(self):
        self.lastSetSelected = None
        provider = None
        if AutonaTournamentDataProvider.instance.provider:
            provider = AutonaTournamentDataProvider.instance.provider.name
        if provider and SettingsManager.Get(provider+"_user"):
            AutonaTournamentDataProvider.instance.LoadUserSet(
                self, SettingsManager.Get(provider+"_user"))

    def LoadUserSetOptionsClicked(self):
        AutonaTournamentDataProvider.instance.SetUserAccount(self)

    # Used for score change commands
    # Change <team>(0, 1) score by <change>(+X, -X)
    def CommandScoreChange(self, team: int, change: int):
        if team in (0, 1):
            scoreContainers = [self.scoreDisplay1, self.scoreDisplay2]
            scoreContainers[team].setValue(
                scoreContainers[team].value()+change)
            
    def CommandClearAll(self, no_mains=False):
        for t, team in enumerate([self.team1playerWidgets, self.team2playerWidgets]):
            for i, p in enumerate(team):
                p.Clear(no_mains)
        self.lastSetSelected = None

    def ClearScore(self):
        for c in self.scoreColumn.findChildren(QComboBox):
            c.setCurrentText("")
            c.lineEdit().editingFinished.emit()

        self.scoreDisplay1.setValue(0)
        self.scoreDisplay2.setValue(0)

        self.team1column.findChild(QCheckBox, "losers").setChecked(False)
        self.team2column.findChild(QCheckBox, "losers").setChecked(False)

    def CommandTeamColor(self, team: int, color, force_opponent=False):
        if color:
            value = None
            if type(color) is str:
                value = color
            if type(color) is int and color > 0:
                value = AutonaGameAssetManager.instance.colorModel.item(color).data(Qt.ItemDataRole.UserRole)
                if force_opponent:
                    value = value.get("force_opponent")
                else:
                    value = value.get("value")
                if value:
                    value = "#" + value

            if value:
                if team == 0:
                    self.colorButton1.setColor(value)
                if team == 1:
                    self.colorButton2.setColor(value)
                if team in (0, 1):
                    StateManager.BlockSaving()
                    StateManager.Set(
                        f"score.{self.scoreboardNumber}.team.{team + 1}.color", value)
                    StateManager.ReleaseSaving()


    # Modifies the current set data. Does not check for id, so do not call this with data that may lead to another hbox incident
    def ChangeSetData(self, data):
        StateManager.BlockSaving()

        try:
            round_name = data.get("round_name")
            if round_name:
                self.matchCombo.setCurrentText(round_name)
                self.matchCombo.lineEdit().editingFinished.emit()
                StateManager.Set(f"score.{self.scoreboardNumber}.match", round_name)
            # Also persist the raw English long-form round text (e.g.
            # "Winners Final"); used for English-only outputs like the
            # YouTube video title generator.
            StateManager.Set(
                f"score.{self.scoreboardNumber}.match_en",
                data.get("round_name_en") or "",
            )

            tournament_phase = data.get("tournament_phase")
            if tournament_phase:
                round_division = data.get("roundDivision", 0)
                top_n_value = ""

                if round_division and data.get("isPools", False) is False and round_division > 6:
                    top_n_value = AutonaLocaleHelper.phaseNames.get("top_n", "Top {0}").format(round_division)

                StateManager.Set(f"score.{self.scoreboardNumber}.top_n", top_n_value)

                self.phaseCombo.setCurrentText(tournament_phase)
                self.phaseCombo.lineEdit().editingFinished.emit()
                StateManager.Set(f"score.{self.scoreboardNumber}.phase", tournament_phase)

            scoreContainers = [self.scoreDisplay1, self.scoreDisplay2]
            if self.teamsSwapped:
                scoreContainers.reverse()

            if data.get("reset_score"):
                scoreContainers[0].setValue(0)
                scoreContainers[1].setValue(0)
            if not SettingsManager.Get("general.disable_scoreupdate", False):
                if data.get("team1score") is not None:
                    if data.get("team1score") != 0:
                        scoreContainers[0].setValue(data.get("team1score"))
                    else:
                        scoreContainers[0].setValue(0)
                if data.get("team2score") is not None:
                    if data.get("team2score") != 0:
                        scoreContainers[1].setValue(data.get("team2score"))
                    else:
                        scoreContainers[1].setValue(0)
            
            if data.get("bestOf"):
                self.bestOfSpin.setValue(data.get("bestOf"))

            losersContainers = [
                self.team1column.findChild(QCheckBox, "losers"),
                self.team2column.findChild(QCheckBox, "losers")
            ]
            if self.teamsSwapped:
                losersContainers.reverse()

            # Stream URL is user-managed via the dropdown; do not overwrite
            # it when loading a set from StartGG.

            if data.get("team1losers") is not None:
                losersContainers[0].setChecked(data.get("team1losers"))
            if data.get("team2losers") is not None:
                losersContainers[1].setChecked(data.get("team2losers"))

            if data.get("entrants"):
                self.playerNumber.setValue(
                    len(max(data.get("entrants"), key=lambda x: len(x))))

                # Lock all player widgets
                for p in self.playerWidgets:
                    p.dataLock.acquire()

                try:
                    for t, team in enumerate(data.get("entrants")):
                        teamInstances = [self.team1playerWidgets,
                                         self.team2playerWidgets]
                        if self.teamsSwapped:
                            teamInstances.reverse()
                        teamInstance = teamInstances[t]

                        if len(team) > 1:
                            teamColumns = [self.team1column, self.team2column]
                            teamNames = [
                                data.get("p1_name"), data.get("p2_name")]
                            if self.teamsSwapped:
                                teamNames.reverse()
                            teamColumns[t].findChild(
                                QLineEdit, "teamName").setText(teamNames[t])
                            teamColumns[t].findChild(
                                QLineEdit, "teamName").editingFinished.emit()

                        for p, player in enumerate(team):
                            if data.get("overwrite"):
                                # Overwrite path already applies mains via the full
                                # player dict; the old code followed this with a
                                # redundant mains-only SetData that doubled the
                                # per-player cost on set load. Using elif so the
                                # mains-only path only runs in the non-overwrite case.
                                teamInstance[p].SetData(player, False, True, data.get(
                                    "no_mains") if data.get("no_mains") != None else False)
                            elif data.get("has_selection_data") and data.get("no_mains") != True:
                                player_mains_only = {
                                    "mains": player.get("mains")
                                }
                                teamInstance[p].SetData(player_mains_only, True, False)
                except Exception as e:
                    logger.error(f"Error while setting entrants: {e}")
                finally:
                    for p in self.playerWidgets:
                        p.dataLock.release()
            else:
                try:
                    # Lock all player widgets
                    for p in self.playerWidgets:
                        p.dataLock.acquire()

                    if not data.get("team") or not data.get("player"):
                        return

                    team = int(data.get("team"))-1
                    player = int(data.get("player"))-1

                    teamInstances = [self.team1playerWidgets,
                                     self.team2playerWidgets]
                    teamInstance = teamInstances[team]

                    teamInstance[player].SetData(
                        data.get("data"), False, False)
                    if data.get("data", {}).get("savePlayerToDb", False):
                        teamInstance[player].SavePlayerToDB()
                except Exception as e:
                    logger.error(f"Error while setting entrants: {e}")
                finally:
                    for p in self.playerWidgets:
                        p.dataLock.release()

            if data.get("stage_strike"):
                StateManager.Set(f"score.{self.scoreboardNumber}.stage_strike",
                                 data.get("stage_strike"))
                StateManager.Set(f"score.ruleset", data.get("ruleset"))

            if data.get("bracket_type"):
                StateManager.Set(f"score.bracket_type",
                                 data.get("bracket_type"))
                self.stats.signals.UpsetFactorCalculation.emit()

            if data.get("top_n"):
                StateManager.Set(
                    f"score.{self.scoreboardNumber}.top_n", data.get("top_n"))
        finally:
            StateManager.ReleaseSaving()

    def UpdateSetData(self, data):
        # Do not update the scoreboard on empty data
        if data is None or len(data) == 0 or data.get("id") == None:
            return

        # If you switched sets and it was still finishing an async update call
        # Avoid loading data from the previous set
        if str(data.get("id")) != str(self.lastSetSelected):
            return

        if SettingsManager.Get("general.disable_overwrite", False):
            for entrant in data.get("entrants"):
                if (entrant[0].get("gamerTag") in AutonaPlayerDB.database):
                    entrant[0] = entrant[0] | AutonaPlayerDB.database[entrant[0].get(
                        "gamerTag")]

        self.ChangeSetData(data)

    def LoadPlayerFromTag(self, tag, team, player, no_mains=False):
        team = int(team)-1
        player = int(player)-1
        teamInstances = [self.team1playerWidgets,
                         self.team2playerWidgets]

        if self.teamsSwapped:
            teamInstances.reverse()

        playerData = AutonaPlayerDB.GetPlayerFromTag(tag)
        if playerData:
            teamInstances[team][player].SetData(
                    playerData, False, True, no_mains)
            return True
        return False

    def SetDefaultsFromAssets(self):
        if StateManager.Get(f'game.defaults'):
            players, characters = StateManager.Get(f'game.defaults.players_per_team', 1), StateManager.Get(f'game.defaults.characters_per_player', 1)
        else:
            players, characters = 1, 1
        print(players, "players", characters, "characters")
        self.playerNumber.setValue(players)
        self.charNumber.setValue(characters)

    def GetIP(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP
    pass
