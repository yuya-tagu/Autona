import math
import urllib.parse
import webbrowser

from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy import uic
from typing import List
from .Helpers.AutonaDirHelper import AutonaResolve
from .Helpers.AutonaVersionHelper import add_beta_label
from .Helpers.AutonaNetworkHelper import GetWebServerHost

from . import ui_constants as UI
from . import ui_layout as L
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

        topOptions.layout().addStretch()

        self.thumbnailBtn = QPushButton(
            QApplication.translate("app", "Generate Thumbnail") + " ")
        self.thumbnailBtn.setIcon(QIcon(UI.icon_path('png_file.svg')))
        self.thumbnailBtn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        topOptions.layout().addWidget(self.thumbnailBtn)
        self.thumbnailBtn.clicked.connect(self.GenerateThumbnail)
        self.thumbnailBtn.setVisible(False)

        # X-post and browser-preview buttons are created here but placed in
        # the scoreboard section's title row (see _groupSectionsIntoFrames)
        # so they sit at the top-right of the scoreboard itself, not the
        # match-info ("対戦情報") section.
        self.xpostBtn = QPushButton(
            QApplication.translate("app", "Xに投稿する"))
        # Neutral (secondary) styling — no longer a primary action.
        self.xpostBtn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.xpostBtn.clicked.connect(self.PostToX)

        # board 1 (SCORE/1v1) → /scoreboard, board 2 (TEAM) → /team. Both
        # render from the same React page parameterized by scoreboardNumber.
        webScoreboardPath = "team" if self.scoreboardNumber == 2 else "scoreboard"
        self.webScoreboardUrl = f"http://{GetWebServerHost()}:{SettingsManager.Get('general.webserver_port', 5000)}/{webScoreboardPath}"
        self.btOpenWebScoreboard = QPushButton("ブラウザ版を開く")
        self.btOpenWebScoreboard.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btOpenWebScoreboard.clicked.connect(lambda: webbrowser.open(self.webScoreboardUrl))

        self.eyeBt = QToolButton()
        self.eyeBt.setIcon(QIcon(UI.icon_path('eye.svg')))
        self.eyeBt.setSizePolicy(
            QSizePolicy.Maximum, QSizePolicy.Fixed)
        # Placed beside the BEST OF spinbox in the scoreBar (see bestOfGroup
        # setup below) so the visibility toggles live next to the match
        # metadata instead of at the top of the tab.
        self.eyeBt.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu()
        self.eyeBt.setMenu(menu)

        menu.addSection("Players")

        # [label, [widget objectNames to toggle], settings key, default visible]
        # Defaults: only sponsor / name / seed / characters are shown on a
        # fresh install; the other fields stay off until the user enables
        # them from the eye menu.
        # Per-field rows no longer carry inline labels (the field name lives in
        # the placeholder), so the toggle lists reference the inputs only.
        self.elements = [
            [QApplication.translate("app", "Sponsor"),                ["team"],                 "show_sponsor",    True],
            [QApplication.translate("app", "Player Name"),            ["name"],                 "show_name",       True],
            [QApplication.translate("app", "Seed"),                   ["seedDisplay"],          "show_seed",       True],
            [QApplication.translate("app", "Characters"),             ["characters"],           "show_characters", True],
            [QApplication.translate("app", "Twitter"),                ["twitter"],              "show_social",     True],
            [QApplication.translate("app", "Real Name"),              ["real_name"],            "show_real_name",  False],
            [QApplication.translate("app", "Location"),               ["country", "state"],     "show_location",   True],
            [QApplication.translate("app", "Additional information"), ["custom_textbox"],       "show_additional", False],
        ]
        self._sponsorAction: QAction = None
        # settings key -> QAction, so remote clients (React Web UI) can drive
        # the same toggle path via SetDisplayOption().
        self._eyeActions = {}
        for element in self.elements:
            action: QAction = self.eyeBt.menu().addAction(element[0])
            action.setCheckable(True)
            action.setChecked(SettingsManager.Get(f"display_options.{element[2]}", element[3]))
            self._eyeActions[element[2]] = action
            if element[2] == "show_sponsor":
                self._sponsorAction = action
                action.toggled.connect(
                    lambda toggled, action=action, element=element: self._onSponsorActionToggled(action, element[1]))
            else:
                action.toggled.connect(
                    lambda toggled, action=action, element=element: self._onEyeActionToggled(action, element))

        # Mirror every display option into StateManager so remote clients
        # (React Web UI, OBS) can render the same field visibility. Before
        # this loop only show_sponsor was mirrored; the other toggles were
        # session-local Qt visibility.
        for element in self.elements:
            StateManager.Set(f"display_options.{element[2]}",
                SettingsManager.Get(f"display_options.{element[2]}", element[3]))

        self.playerWidgets: List[AutonaScoreboardPlayerWidget] = []
        self.team1playerWidgets: List[AutonaScoreboardPlayerWidget] = []
        self.team2playerWidgets: List[AutonaScoreboardPlayerWidget] = []

        self.team1swaps = []
        self.team2swaps = []

        self.columns = QWidget()
        self.columns.setLayout(QHBoxLayout())
        self.columns.layout().setContentsMargins(0, 0, 0, 0)
        self.columns.layout().setSpacing(UI.SPACE_SM)
        # Keep team and score columns flush with the top of the available
        # area. Without this, a window taller than the columns' preferred
        # height leaves a visible gap between the color/1P row and the
        # player fields because the HBoxLayout centers its items by default.
        self.columns.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
        self.innerWidget.layout().addWidget(self.columns)

        # --- Stream / set loading section (above phase/match) ---
        self.streamSection = QWidget()
        self.streamSection.setLayout(QVBoxLayout())
        self.streamSection.layout().setContentsMargins(0, 0, 0, 0)
        self.streamSection.layout().setSpacing(UI.SPACE_SM)
        self.streamSection.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)

        # The stream-destination ("配信先") row used to live here. It moved to
        # the EVENT tab's "Stream Settings" section so the broadcast channel is
        # configured once at the event level (see AutonaTournamentInfoWidget).
        # The chosen URL is still mirrored to score.{N}.stream_url, so the
        # X-post builder below keeps reading its per-board key.

        loadSetRow = QHBoxLayout()
        loadSetRow.setContentsMargins(0, 0, 0, 0)
        loadSetRow.setSpacing(UI.SPACE_SM)
        self.streamSection.layout().addLayout(loadSetRow)

        self.btSelectSet = QPushButton(
            QApplication.translate("app", "Load set"))
        # Primary action: accent (red) background, white text — opt-in via the
        # objectName the global QSS targets (ui_constants QPushButton#primary).
        self.btSelectSet.setObjectName("primary")
        self.btSelectSet.setIcon(QIcon(UI.icon_path("list.svg")))
        self.btSelectSet.setEnabled(False)
        loadSetRow.addWidget(self.btSelectSet)
        self.btSelectSet.clicked.connect(self.signals.SetSelection.emit)

        # Auto-update (5-second polling) feature has been removed from the
        # UI. Force-disable the setting so any stale True value from an
        # older session doesn't silently keep the timer running.
        SettingsManager.Set("general.disable_autoupdate", True)

        # --- Bottom options (settings, timer) below team columns ---
        bottomOptions = QWidget()
        bottomOptions.setLayout(QVBoxLayout())
        bottomOptions.layout().setContentsMargins(0, 0, 0, 0)
        bottomOptions.layout().setSpacing(UI.SPACE_SM)
        bottomOptions.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)

        self.innerWidget.layout().addWidget(bottomOptions)

        settingsRow = QHBoxLayout()
        settingsRow.setContentsMargins(0, 0, 0, 0)
        settingsRow.setSpacing(UI.SPACE_SM)

        # Characters-per-player is owned by the scoreboard itself (the former
        # PLAYERS tab that used to drive it was removed). External WebServer
        # actions still poke self.charNumber.value()/setValue(). Default is 3.
        self.charNumber = QSpinBox()
        self.charNumber.setMaximum(100)
        self.charNumber.setFixedWidth(120)
        self.charNumber.valueChanged.connect(self.SetCharacterNumber)
        self.charNumber.setValue(3)

        self.playerNumber = QSpinBox()
        self.playerNumber.setFixedWidth(120)
        self._playerNumberLabel = QLabel(QApplication.translate("app", "Players per team"))
        settingsRow.addWidget(self._playerNumberLabel)
        settingsRow.addWidget(self.playerNumber)
        self.playerNumber.valueChanged.connect(self.SetPlayersPerTeam)
        settingsRow.addWidget(
            QLabel(QApplication.translate("app", "Characters per player")))
        settingsRow.addWidget(self.charNumber)

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
        self.team1column.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        self.columns.layout().addWidget(self.team1column)
        self.team1column.findChild(QLabel, "teamLabel").setText(
            QApplication.translate("app", "TEAM {0}").format(1))
        self.team1column.findChild(QLabel, "teamLabel").setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)

        colorGroup1 = QWidget()
        self.colorGroup1 = colorGroup1
        colorGroup1.setLayout(QHBoxLayout())
        colorGroup1.layout().setContentsMargins(0, 0, 0, 0)
        colorGroup1.layout().setSpacing(UI.SPACE_SM)
        colorGroup1.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        # Team color is no longer edited inline (the SCORE-tab swatch was
        # removed). It is configured via Edit → Settings → General and applied
        # here at build time. colorGroup1 stays as an (empty) layout spacer that
        # keeps the 1P/TEAM label centered (see _applyScoreboardMode).
        DEFAULT_TEAM1_COLOR = SettingsManager.Get("general.team_1_default_color", "#fe3636")
        self.CommandTeamColor(0, DEFAULT_TEAM1_COLOR)

        team1HeaderRow = self.team1column.findChild(QHBoxLayout, "horizontalLayout_2")
        team1HeaderRow.layout().insertWidget(0, colorGroup1)
        # Pad the right edge so the losers checkbox ends flush with where
        # the player-widget inputs below terminate (grid right margin
        # inside AutonaScoreboardPlayer.ui is 5px).
        team1HeaderRow.layout().addSpacing(UI.SPACE_MD)

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
        self.colorGroup2 = colorGroup2
        colorGroup2.setLayout(QHBoxLayout())
        colorGroup2.layout().setContentsMargins(0, 0, 0, 0)
        colorGroup2.layout().setSpacing(UI.SPACE_SM)
        colorGroup2.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        self.team2column = uic.loadUi(AutonaResolve("src/layout/AutonaScoreboardTeam.ui"))
        self.team2column.setMinimumWidth(0)
        self.team2column.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        self.columns.layout().addWidget(self.team2column)
        self.team2column.findChild(QLabel, "teamLabel").setText(
            QApplication.translate("app", "TEAM {0}").format(2))
        self.team2column.findChild(QLabel, "teamLabel").setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)

        # See team-1 note above: swatch removed, color comes from Settings.
        DEFAULT_TEAM2_COLOR = SettingsManager.Get("general.team_2_default_color", "#2e89ff")
        self.CommandTeamColor(1, DEFAULT_TEAM2_COLOR)

        team2HeaderRow = self.team2column.findChild(QHBoxLayout, "horizontalLayout_2")
        team2HeaderRow.layout().insertWidget(0, colorGroup2)
        team2HeaderRow.layout().addSpacing(UI.SPACE_MD)

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
        self.playerNumber.setValue(1)
        # Re-assert the characters-per-player default so the freshly-built
        # player widgets pick it up instead of getting pinned to 1.
        self.charNumber.setValue(3)

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
        phaseGroup.layout().setSpacing(UI.SPACE_SM)
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
        matchGroup.layout().setSpacing(UI.SPACE_SM)
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
        bestOfGroup.layout().setSpacing(UI.SPACE_SM)
        bestOfLabel = self.scoreColumn.findChild(QLabel, "label_3")
        self.bestOfSpin = self.scoreColumn.findChild(QSpinBox, "best_of")
        bestOfSpin = self.bestOfSpin
        bestOfLabel.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        bestOfGroup.layout().addWidget(bestOfLabel)
        bestOfGroup.layout().addWidget(bestOfSpin)
        self.scoreBar.layout().addWidget(bestOfGroup, 1)

        columnsIndex = self.innerWidget.layout().indexOf(self.columns)
        # Insert order: streamSection, scoreBar, then columns
        self.innerWidget.layout().insertWidget(columnsIndex, self.scoreBar)
        self.innerWidget.layout().insertWidget(columnsIndex, self.streamSection)
        self.scoreColumn.hide()

        # Score widget: buttons + displays built in Python
        self.scoreWidget = QWidget()
        self.scoreWidget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        scoreVBox = QVBoxLayout()
        L.apply_score_panel_layout(scoreVBox)
        self.scoreWidget.setLayout(scoreVBox)

        SZ = int(UI.BTN_LG * 1.25)
        SP = L.SCORE_PANEL_BUTTON_SPACING
        ICON = int(UI.ICON_SM * 1.25)
        scoreBtnStyle = UI.score_btn_style(scale=1.0)

        # Row 1: Swap label + button
        self.btSwapLabel = QLabel(
            QApplication.translate("app", "Swap 1P and 2P"))
        _swapFont = self.btSwapLabel.font()
        _swapFont.setPointSize(7)
        _swapFont.setBold(False)
        self.btSwapLabel.setFont(_swapFont)
        self.btSwapLabel.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.btSwapLabel.setStyleSheet(self._smallAnnotationStyle())
        scoreVBox.addWidget(self.btSwapLabel)
        row1 = L.score_panel_row()
        # Slightly downsize the glyphs on the swap / reset action buttons
        # so the icon doesn't dominate the pill — the wide buttons read
        # better with a more proportionate symbol.
        _ACTION_ICON = int(ICON * 0.7)
        self.btSwap = QPushButton()
        self.btSwap.setIcon(QIcon(UI.icon_path("swap.svg")))
        self.btSwap.setIconSize(QSize(_ACTION_ICON, _ACTION_ICON))
        self.btSwap.setFixedSize(SZ * 2 + SP, SZ)
        # Height must be pinned via QSS (setFixedSize's height is ignored under
        # the global stylesheet); the neutral fill still comes from the app QSS.
        self.btSwap.setStyleSheet(UI.score_ctrl_height_qss())
        self.btSwap.setToolTip(QApplication.translate("app", "Swap Teams"))
        self.btSwap.clicked.connect(self.SwapTeams)
        row1.addWidget(self.btSwap)
        scoreVBox.addLayout(row1)

        # Row 2: Score up
        row2 = L.score_panel_row()
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
        row3 = L.score_panel_row()
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
        row4 = L.score_panel_row()
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

        # Row 5: Score reset button + label
        row5 = L.score_panel_row()
        self.btScoreReset = QPushButton()
        self.btScoreReset.setIcon(QIcon(UI.icon_path("cancel.svg")))
        self.btScoreReset.setIconSize(QSize(_ACTION_ICON, _ACTION_ICON))
        self.btScoreReset.setFixedSize(SZ * 2 + SP, SZ)
        self.btScoreReset.setToolTip(QApplication.translate("app", "Score Reset"))
        # Reset clears live broadcast state — flag it as a destructive
        # action so it stands apart from the +1/-1 score controls.
        L.tag_button(self.btScoreReset, L.BUTTON_DANGER)
        # Match the other score-panel controls' height (see score_ctrl_height_qss);
        # the danger fill still comes from the QPushButton#danger global rule.
        self.btScoreReset.setStyleSheet(UI.score_ctrl_height_qss())
        self.btScoreReset.clicked.connect(lambda: self.ResetScore())
        row5.addWidget(self.btScoreReset)
        scoreVBox.addLayout(row5)
        self.btScoreResetLabel = QLabel(
            QApplication.translate("app", "Reset Score"))
        _resetFont = self.btScoreResetLabel.font()
        _resetFont.setPointSize(7)
        _resetFont.setBold(False)
        self.btScoreResetLabel.setFont(_resetFont)
        self.btScoreResetLabel.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.btScoreResetLabel.setStyleSheet(self._smallAnnotationStyle())
        scoreVBox.addWidget(self.btScoreResetLabel)

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

        self._groupSectionsIntoFrames(topOptions, bottomOptions)


    def _groupSectionsIntoFrames(self, topOptions, bottomOptions):
        """Wrap the Start.gg-load/stream-URL area and the phase/score
        area in two bordered frames so each section reads as its own
        visual group.

        Current innerWidget child order is:
            topOptions, streamSection, scoreBar, columns, bottomOptions
        """
        inner = self.innerWidget.layout()

        # Strip every previously-added child out of innerWidget so we
        # can re-home them into the frames without leaking duplicates.
        for w in (
            topOptions,
            self.streamSection,
            self.scoreBar,
            self.columns,
            bottomOptions,
        ):
            if w is None:
                continue
            inner.removeWidget(w)

        # topOptions is now empty (the thumbnail button is permanently
        # hidden; the X-post / browser buttons moved into the scoreboard
        # title row), so it only contributed dead vertical space above the
        # Start.gg load button. Keep it alive (it still parents the hidden
        # thumbnail button) but out of the layout.
        self._topOptionsHolder = topOptions
        topOptions.hide()
        topOptions.setParent(None)

        self.topSectionFrame = QFrame()
        self.topSectionFrame.setObjectName("scoreTopSection")
        self.topSectionFrame.setFrameShape(QFrame.Shape.NoFrame)
        self.topSectionFrame.setFrameShadow(QFrame.Shadow.Plain)
        # QFrame defaults to (Preferred, Preferred) which lets it stretch
        # vertically inside innerWidget's QVBoxLayout. When that happens
        # the children (all Maximum vsize) keep their sizeHint and the
        # excess height pools inside the frame — visible as dead space
        # between rows. Pin to Maximum so any surplus falls through to
        # innerWidget's trailing stretch instead.
        self.topSectionFrame.setSizePolicy(
            QSizePolicy.MinimumExpanding, QSizePolicy.Maximum)
        self.topSectionFrame.setStyleSheet(
            self._sectionFrameStyle("scoreTopSection"))
        topLayout = QVBoxLayout()
        topLayout.setContentsMargins(
            UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD)
        topLayout.setSpacing(UI.SPACE_SM)
        topLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.topSectionFrame.setLayout(topLayout)

        topLayout.addWidget(self.streamSection)

        self.matchSectionFrame = QFrame()
        self.matchSectionFrame.setObjectName("scoreMatchSection")
        self.matchSectionFrame.setFrameShape(QFrame.Shape.NoFrame)
        self.matchSectionFrame.setFrameShadow(QFrame.Shadow.Plain)
        self.matchSectionFrame.setSizePolicy(
            QSizePolicy.MinimumExpanding, QSizePolicy.Maximum)
        self.matchSectionFrame.setStyleSheet(
            self._sectionFrameStyle("scoreMatchSection"))
        matchLayout = QVBoxLayout()
        matchLayout.setContentsMargins(
            UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD, UI.SPACE_MD)
        matchLayout.setSpacing(UI.SPACE_SM)
        matchLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.matchSectionFrame.setLayout(matchLayout)

        # Title + action buttons + eye-toggle share a single row at the top
        # of the scoreboard section: title on the left; X-post / browser /
        # visibility-toggle buttons grouped at the top-right.
        titleRow = QWidget()
        titleRowLayout = QHBoxLayout()
        titleRowLayout.setContentsMargins(0, 0, 0, 0)
        titleRowLayout.setSpacing(UI.SPACE_SM)
        titleRow.setLayout(titleRowLayout)

        self.matchSectionTitle = QLabel(
            QApplication.translate("app", "スコアボード"))
        self.matchSectionTitle.setStyleSheet(self._sectionTitleStyle())
        titleRowLayout.addWidget(self.matchSectionTitle)
        titleRowLayout.addStretch(1)
        titleRowLayout.addWidget(self.xpostBtn)
        titleRowLayout.addWidget(self.btOpenWebScoreboard)
        titleRowLayout.addWidget(self.eyeBt)
        matchLayout.addWidget(titleRow)

        matchLayout.addWidget(self.scoreBar)

        self.hideSponsorCheck = QCheckBox(
            QApplication.translate("app", "スポンサー名をスコアボードに表示しない"))
        self.hideSponsorCheck.setChecked(
            not SettingsManager.Get("display_options.show_sponsor", True))
        self.hideSponsorCheck.toggled.connect(self._onHideSponsorToggled)
        matchLayout.addWidget(self.hideSponsorCheck)

        # Separator line below the phase/round/BEST OF row to visually
        # delimit it from the team columns beneath.
        self._scoreBarSeparator = QFrame()
        self._scoreBarSeparator.setFrameShape(QFrame.Shape.HLine)
        self._scoreBarSeparator.setFrameShadow(QFrame.Shadow.Sunken)
        scoreBarSepWrap = QWidget()
        _sbSepLayout = QVBoxLayout()
        _sbSepLayout.setContentsMargins(0, UI.SPACE_SM, 0, UI.SPACE_SM)
        _sbSepLayout.setSpacing(0)
        _sbSepLayout.addWidget(self._scoreBarSeparator)
        scoreBarSepWrap.setLayout(_sbSepLayout)
        scoreBarSepWrap.setSizePolicy(
            QSizePolicy.Minimum, QSizePolicy.Maximum)
        matchLayout.addWidget(scoreBarSepWrap)

        matchLayout.addWidget(self.columns)
        matchLayout.addWidget(bottomOptions)

        # Scoreboard section on top, match-info ("対戦情報") section below it.
        inner.addWidget(self.matchSectionFrame)
        inner.addWidget(self.topSectionFrame)
        inner.addStretch(1)
        # Tight gap between the scoreboard and Start.gg-load sections so the
        # player "Clear" buttons and the "Load set" button read as one cluster.
        inner.setSpacing(UI.SPACE_XS)

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

            # Center the 1P/2P label over the column. The label sits between the
            # color swatch (left) and the losers checkbox (right); since those
            # flanks differ in width, an internally-centered label lands left of
            # the column center. Widen the (left) color-swatch container to match
            # the right flank so the centered label aligns with the column.
            colorGroup = self.colorGroup1 if side == 1 else self.colorGroup2
            losers = column.findChild(QCheckBox, "losers")
            if colorGroup is not None and losers is not None:
                if singlePlayer:
                    colorGroup.setMinimumWidth(
                        losers.sizeHint().width() + UI.SPACE_MD)
                else:
                    colorGroup.setMinimumWidth(0)

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
                    "thumb_app", "The thumbnail has been generated here:") + " " + thumbnailPath)
                msgBox.setIcon(QMessageBox.NoIcon)
                # msgBox.setInformativeText(thumbnailPath)

            if not quiet_mode:
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
                    twitter = (StateManager.Get(
                        f"score.{n}.team.{team_n}.player.{k}.twitter") or "").strip()
                    entry = name
                    if twitter:
                        # Normalize to an @handle so the tweet auto-mentions.
                        handle = twitter.lstrip("@").strip()
                        if handle:
                            entry += f" @{handle}"
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
        visible = action.isChecked()
        for pw in self.playerWidgets:
            for element in elements:
                w = pw.findChild(QWidget, element)
                # Team-mode variant of the player widget may not ship with
                # every label objectName; silently skip missing widgets
                # rather than crashing the toggle.
                if w is not None:
                    if element == "seedDisplay":
                        # Reserve the trailing seed slot only when a seed is
                        # actually present; an empty-but-visible label leaves
                        # a gap that misaligns the GamerTag field with the
                        # rows below.
                        w.setVisible(visible and bool(w.text()))
                    else:
                        w.setVisible(visible)

    def _onEyeActionToggled(self, action: QAction, element):
        # Unified eye-menu handler (non-sponsor): apply visibility and
        # persist to both SettingsManager (local default) and StateManager
        # (broadcast to Web UI / OBS).
        self.ToggleElements(action, element[1])
        show = action.isChecked()
        SettingsManager.Set(f"display_options.{element[2]}", show)
        StateManager.Set(f"display_options.{element[2]}", show)

    def SetDisplayOption(self, key: str, visible: bool):
        """Remote toggle entry point (Web UI eye menu -> POST /display-option).

        Drives the eye-menu QAction so widget visibility, SettingsManager and
        StateManager stay in sync through the same handlers as a local click.
        Must run on the GUI thread (callers use @gui_thread_sync).
        """
        action = self._eyeActions.get(key)
        if action is None:
            return
        if action.isChecked() == visible:
            # Handlers won't fire; still mirror the state key so the remote
            # caller sees a consistent echo.
            StateManager.Set(f"display_options.{key}", visible)
            return
        action.setChecked(visible)

    def _onSponsorActionToggled(self, action: QAction, elements):
        self.ToggleElements(action, elements)
        if hasattr(self, 'hideSponsorCheck'):
            self.hideSponsorCheck.blockSignals(True)
            self.hideSponsorCheck.setChecked(not action.isChecked())
            self.hideSponsorCheck.blockSignals(False)
        show = action.isChecked()
        SettingsManager.Set("display_options.show_sponsor", show)
        StateManager.Set("display_options.show_sponsor", show)

    def _onHideSponsorToggled(self, hide: bool):
        if self._sponsorAction is not None:
            self._sponsorAction.blockSignals(True)
            self._sponsorAction.setChecked(not hide)
            self._sponsorAction.blockSignals(False)
            self.ToggleElements(self._sponsorAction, ["team"])
        show = not hide
        SettingsManager.Set("display_options.show_sponsor", show)
        StateManager.Set("display_options.show_sponsor", show)

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
        # Wrap the whole reconfiguration so player-widget construction Sets +
        # the cleanup Unsets coalesce into one state save / WS broadcast.
        with StateManager.SaveBlock():
            self._setPlayersPerTeamImpl(number)

    def _setPlayersPerTeamImpl(self, number):
        while len(self.team1playerWidgets) < number:
            p = AutonaScoreboardPlayerWidget(
                index=len(self.team1playerWidgets)+1,
                teamNumber=1,
                path=f'score.{self.scoreboardNumber}.team.{1}.player.{len(self.team1playerWidgets)+1}')
            self.playerWidgets.append(p)

            self.team1column.findChild(
                QGroupBox, "groupBox").layout().addWidget(p)
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
                QGroupBox, "groupBox").layout().addWidget(p)
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

        # birthday + controller are not surfaced anywhere yet; keep them
        # hidden regardless of toggle state. real_name / twitterLabel used
        # to be force-hidden here — those are now driven by the
        # Real Name / Twitter eye-menu toggles instead.
        for pw in self.playerWidgets:
            for name in ('birthday', 'controller', 'controllerLabel'):
                w = pw.findChild(QWidget, name)
                if w:
                    w.hide()

    def SwapTeams(self):
        with StateManager.SaveBlock():
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

    def ResetScore(self):
        self.scoreDisplay1.setValue(0)
        self.scoreDisplay2.setValue(0)

    def ToggleAutonaUpdate(self, checked):
        SettingsManager.Set("general.disable_autoupdate", not checked)
        if not checked:
            self.StopAutonaUpdate(clear_variables=True)

    @staticmethod
    def _sectionTitleStyle() -> str:
        # Section header label ("対戦情報" / "スコアボード"). Reads as body
        # text — same size and colour as the content beneath it — set apart
        # only by its bold weight (Design.md §5.2.3: 見出し=700 / 本文=400).
        return (
            f"font-weight: bold; font-size: {UI.FONT_SM}pt;"
            f" color: {UI.palette()['text_primary']};"
        )

    @staticmethod
    def _smallAnnotationStyle() -> str:
        # Tiny caption labels above the swap / reset buttons.
        return f"color: {UI.palette()['text_muted']};"

    @staticmethod
    def _sectionFrameStyle(object_name: str) -> str:
        # Flat (no fill / no border) layout container. The section already
        # sits on the scroll area's bg_secondary panel; a second filled,
        # bordered card here would nest a box-in-a-box. Other tabs lay
        # content flat on the panel, so match them — grouping is carried by
        # the section title label + separator, not a card frame.
        return f"QFrame#{object_name} {{ background: transparent; border: none; }}"

    def AutonaUpdate(self, data):
        AutonaTournamentDataProvider.instance.GetMatch(
            self, data.get("id"), overwrite=False)
        AutonaTournamentDataProvider.instance.GetStreamQueue()

    def StationSetsLoaded(self, data):
        # Ici peut être lancer le chargement des sets voire même trigger un autre signal ?

        logger.info("STATION SETS LOADED -----------------------------")
        logger.info(data)
        with StateManager.SaveBlock():
            StateManager.Set(f"score.{self.scoreboardNumber}.station_queue", data)

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

                AutonaTournamentDataProvider.instance.GetMatch(
                    self, data["id"], overwrite=True, no_mains=data.get("no_mains") if data.get("no_mains") != None else False)

            if not SettingsManager.Get("general.disable_autoupdate", False):
                self.autoUpdateTimer.timeout.connect(
                    lambda setId=data: self.AutonaUpdate(data))

                if data.get("auto_update") in ("stream", "station"):
                    self.autoUpdateTimer.timeout.connect(
                        lambda setId=data: AutonaTournamentDataProvider.instance.LoadStationSets(self))
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

            with StateManager.SaveBlock():
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
                if team in (0, 1):
                    with StateManager.SaveBlock():
                        StateManager.Set(
                            f"score.{self.scoreboardNumber}.team.{team + 1}.color", value)


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
        # Propagate the game's default players-per-team; characters-per-player
        # is forced back to its default (3) regardless of game asset config.
        if StateManager.Get('game.defaults'):
            players = StateManager.Get('game.defaults.players_per_team', 1)
        else:
            players = 1
        self.playerNumber.setValue(players)
        self.charNumber.setValue(3)

    pass
