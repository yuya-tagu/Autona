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
        # StateManager subtree owned by this board. Publication of the whole
        # subtree is deferred (HoldSubtree at the end of __init__): edits stay
        # local until the 反映 button commits them.
        self.statePrefix = f"score.{self.scoreboardNumber}"

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
        AutonaHotkeys.signals.apply_changes.connect(self.ApplyChanges)

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

        # The eye (field-visibility) button was removed from the desktop UI:
        # the details dialog always shows every field. The checkable actions
        # below survive as invisible state carriers so the display_options
        # broadcast (Web UI / OBS overlay rendering) and the remote toggle
        # path (SetDisplayOption ← POST /display-option) keep working.
        # On the desktop, only show_sponsor still drives widget visibility
        # (the inline sponsor field, via hideSponsorCheck).
        # [label, [widget objectNames to toggle], settings key, default visible]
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
            action = QAction(element[0], self)
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

        # One shared 2-column grid for the team cards, the score panel and
        # the apply/reset action row. Every row referencing the same grid
        # columns keeps all rows on one axis — separate per-row HBoxes each
        # computed their own "center" from their own content and drifted
        # apart. Equal stretch keeps the two team cards symmetric
        # regardless of their content minimums.
        self.columns = QWidget()
        columnsGrid = QGridLayout()
        columnsGrid.setContentsMargins(0, 0, 0, 0)
        columnsGrid.setHorizontalSpacing(UI.SPACE_SM)
        columnsGrid.setVerticalSpacing(UI.SPACE_SM)
        columnsGrid.setColumnStretch(0, 1)
        columnsGrid.setColumnStretch(1, 1)
        self.columns.setLayout(columnsGrid)
        self.innerWidget.layout().addWidget(self.columns)

        # --- Stream / set loading section (above phase/match) ---
        self.streamSection = QWidget()
        self.streamSection.setLayout(QVBoxLayout())
        self.streamSection.layout().setContentsMargins(0, 0, 0, 0)
        self.streamSection.layout().setSpacing(UI.SPACE_SM)
        self.streamSection.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)

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
        # Kept as attribute: _applyScoreboardMode hides the whole row in
        # single-player mode (its only remaining content, the players-per-
        # team spinbox, is team-mode-only — an empty visible row still eats
        # one layout-spacing step).
        self._bottomOptions = bottomOptions

        self.innerWidget.layout().addWidget(bottomOptions)

        settingsRow = QHBoxLayout()
        settingsRow.setContentsMargins(0, 0, 0, 0)
        settingsRow.setSpacing(UI.SPACE_SM)

        # Characters-per-player is owned by the scoreboard itself (the former
        # PLAYERS tab that used to drive it was removed). The spinbox is no
        # longer user-facing — it survives as a hidden data holder because
        # external WebServer actions still poke self.charNumber.value()/
        # setValue() and SetDefaultsFromAssets re-asserts it. Default is 3.
        self.charNumber = QSpinBox(self)
        self.charNumber.setMaximum(100)
        self.charNumber.valueChanged.connect(self.SetCharacterNumber)
        self.charNumber.setValue(3)
        self.charNumber.setVisible(False)

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
        self.team1column.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        self._styleTeamCard(self.team1column)
        self.columns.layout().addWidget(
            self.team1column, 0, 0, Qt.AlignmentFlag.AlignTop)

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
        self._styleTeamCard(self.team2column)
        self.columns.layout().addWidget(
            self.team2column, 0, 1, Qt.AlignmentFlag.AlignTop)

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

        # Pool (phase group) sits between phase and match. Kept as its own
        # state key (`score.N.pool`) instead of being concatenated into the
        # phase text; overlays join them for display.
        poolGroup = QWidget()
        poolGroup.setLayout(QHBoxLayout())
        poolGroup.layout().setContentsMargins(0, 0, 0, 0)
        poolGroup.layout().setSpacing(UI.SPACE_SM)
        poolLabel = self.scoreColumn.findChild(QLabel, "label_pool")
        self.poolCombo = self.scoreColumn.findChild(QComboBox, "pool")
        poolLabel.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        poolGroup.layout().addWidget(poolLabel)
        poolGroup.layout().addWidget(self.poolCombo, 1)
        # Same stretch as phase / match so the three combos come out equally
        # wide (their labels — フェーズ / プール名 / ラウンド — are all 4
        # characters, so equal group stretch means equal combo width).
        self.scoreBar.layout().addWidget(poolGroup, 2)

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

        # BEST OF no longer rides the phase/pool/match bar — it is re-homed
        # into the sponsor-checkbox row (right-aligned) by
        # _groupSectionsIntoFrames, which leaves the three combos above to
        # split the full width evenly.
        self.bestOfGroup = QWidget()
        self.bestOfGroup.setLayout(QHBoxLayout())
        self.bestOfGroup.layout().setContentsMargins(0, 0, 0, 0)
        self.bestOfGroup.layout().setSpacing(UI.SPACE_SM)
        bestOfLabel = self.scoreColumn.findChild(QLabel, "label_3")
        self.bestOfSpin = self.scoreColumn.findChild(QSpinBox, "best_of")
        bestOfLabel.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.bestOfGroup.layout().addWidget(bestOfLabel)
        self.bestOfGroup.layout().addWidget(self.bestOfSpin)

        # Display tournament name row: separate from the phase/round/best-of
        # bar above. Backed by tournamentInfo.displayTournamentName (not
        # score.{N}.*), so it is board-agnostic, immediate (no 反映 gating),
        # and survives the score subtree wipe on startup.
        self.displayTournamentNameRow = QWidget()
        self.displayTournamentNameRow.setLayout(QHBoxLayout())
        self.displayTournamentNameRow.layout().setContentsMargins(0, 0, 0, 0)
        self.displayTournamentNameRow.layout().setSpacing(UI.SPACE_SM)
        displayTournamentNameLabel = QLabel(
            QApplication.translate("app", "表示大会名"))
        self.displayTournamentNameEdit = QLineEdit()
        self.displayTournamentNameEdit.setText(
            StateManager.Get("tournamentInfo.displayTournamentName", "") or "")
        self.displayTournamentNameEdit.editingFinished.connect(
            lambda: StateManager.Set(
                "tournamentInfo.displayTournamentName",
                self.displayTournamentNameEdit.text()))
        self.displayTournamentNameRow.layout().addWidget(displayTournamentNameLabel)
        self.displayTournamentNameRow.layout().addWidget(
            self.displayTournamentNameEdit, 1)

        columnsIndex = self.innerWidget.layout().indexOf(self.columns)
        # Construction-time order only — _groupSectionsIntoFrames re-homes
        # these rows into the scoreboard section frame in their final
        # display order.
        self.innerWidget.layout().insertWidget(columnsIndex, self.scoreBar)
        self.innerWidget.layout().insertWidget(
            columnsIndex, self.displayTournamentNameRow)
        self.innerWidget.layout().insertWidget(columnsIndex, self.streamSection)
        self.scoreColumn.hide()

        # Score controls: one full-width card below the team cards, rows
        # stacked top to bottom —
        #   [ +1 ]  [ +1 ]
        #   [  0 ]  [  0 ]   <- gold, monospace (QSpinBox[scoreDisplay])
        #   [ -1 ]  [ -1 ]
        #   [ スコアリセット ]
        #   [ ⇄ チーム入れ替え ]
        self.btSwap = QPushButton(QApplication.translate("app", "Swap Teams"))
        self.btSwap.setIcon(QIcon(UI.icon_path("swap.svg")))
        self.btSwap.clicked.connect(self.SwapTeams)

        # Score displays (spinboxes extracted from the .ui score column).
        # Arrow-less: the +1/-1 buttons are the increment controls. The gold
        # mono look comes from the shared QSpinBox[scoreDisplay="true"] QSS
        # rule (a property tag, not objectName — those stay keypath-safe).
        self.scoreDisplay1 = self.scoreColumn.findChild(QSpinBox, "score_left")
        self.scoreDisplay2 = self.scoreColumn.findChild(QSpinBox, "score_right")
        for display in (self.scoreDisplay1, self.scoreDisplay2):
            display.setAlignment(Qt.AlignmentFlag.AlignCenter)
            display.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            display.setProperty("scoreDisplay", True)

        self.btScoreUp1 = QPushButton("+1")
        self.btScoreUp1.clicked.connect(lambda: self.CommandScoreChange(0, 1))
        self.btScoreUp2 = QPushButton("+1")
        self.btScoreUp2.clicked.connect(lambda: self.CommandScoreChange(1, 1))

        self.btScoreDown1 = QPushButton("-1")
        self.btScoreDown1.clicked.connect(lambda: self.CommandScoreChange(0, -1))
        self.btScoreDown2 = QPushButton("-1")
        self.btScoreDown2.clicked.connect(lambda: self.CommandScoreChange(1, -1))

        self.btScoreReset = QPushButton(
            QApplication.translate("app", "Score Reset"))
        self.btScoreReset.clicked.connect(lambda: self.ResetScore())

        self.scorePanel = QFrame()
        # Card chrome via the shared QSS rule (teamCard / scorePanelCard).
        self.scorePanel.setObjectName("scorePanelCard")
        panelLayout = QVBoxLayout()
        panelLayout.setContentsMargins(
            UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM)
        panelLayout.setSpacing(UI.SPACE_SM)
        self.scorePanel.setLayout(panelLayout)

        for left, right in ((self.btScoreUp1, self.btScoreUp2),
                            (self.scoreDisplay1, self.scoreDisplay2),
                            (self.btScoreDown1, self.btScoreDown2)):
            pairRow = QHBoxLayout()
            pairRow.setSpacing(UI.SPACE_SM)
            pairRow.addWidget(left, 1)
            pairRow.addWidget(right, 1)
            panelLayout.addLayout(pairRow)
        panelLayout.addWidget(self.btScoreReset)
        panelLayout.addWidget(self.btSwap)

        columnsGrid = self.columns.layout()
        columnsGrid.addWidget(self.scorePanel, 1, 0, 1, 2)

        # Action row (grid row 2): reset-all-inputs and apply side by side,
        # each spanning its team column. Negative action left, positive
        # action right — the app-wide button-pair convention.
        self.btApply = QPushButton(QApplication.translate("app", "反映"))
        self.btApply.setObjectName("primary")
        self.btApply.setToolTip(QApplication.translate(
            "app", "入力内容を配信画面に反映する"))
        self.btApply.setEnabled(False)
        self.btApply.clicked.connect(self.ApplyChanges)

        self.btResetAll = QPushButton(
            QApplication.translate("app", "すべての入力値をリセット"))
        self.btResetAll.setIcon(QIcon(UI.icon_path("undo.svg")))
        # Destructive action: danger styling, same pattern as the player
        # Clear button.
        L.tag_button(self.btResetAll, L.BUTTON_DANGER)
        self.btResetAll.clicked.connect(self.ResetAllInputs)

        columnsGrid.addWidget(self.btResetAll, 2, 0)
        columnsGrid.addWidget(self.btApply, 2, 1)

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

        # Add default and user tournament phase title files. Templated
        # entries (the "group" pool template, "予選プール{0}") belong to the
        # pool combo now that the pool is a separate state key.
        self.phaseCombo.addItem("")

        for key in AutonaLocaleHelper.phaseNames.keys():
            phaseString = AutonaLocaleHelper.phaseNames[key]

            if "{0}" in phaseString:
                continue
            if self.phaseCombo.findText(phaseString) < 0:
                self.phaseCombo.addItem(phaseString)

        # Generic pool candidates from the "group" template; the real pool
        # names of the loaded tournament are prepended on
        # tournament_phases_updated (see _UpdatePoolCandidates).
        self.poolCombo.addItem("")
        groupTemplate = AutonaLocaleHelper.phaseNames.get("group", "")
        if "{0}" in groupTemplate:
            for letter in ["A", "B", "C", "D"]:
                if self.poolCombo.findText(groupTemplate.format(letter)) < 0:
                    self.poolCombo.addItem(groupTemplate.format(letter))
        AutonaTournamentDataProvider.instance.signals.tournament_phases_updated.connect(
            self._UpdatePoolCandidates)

        self.matchCombo.addItem("")

        for matchString in AutonaLocaleHelper.GetMatchNameCandidates():
            if self.matchCombo.findText(matchString) < 0:
                self.matchCombo.addItem(matchString)

        # The real first rounds (e.g. "Winners TOP300") depend on the entrant
        # count, so they are inserted when a tournament loads.
        self._entrantsMatchItems = []
        AutonaTournamentDataProvider.instance.signals.tournament_data_updated.connect(
            self._UpdateEntrantsMatchItem)

        AutonaGameAssetManager.instance.signals.onLoad.connect(
            lambda: [
                self.SetDefaultsFromAssets(),
                self.bestOfSpin.valueChanged.emit(self.bestOfSpin.value()),
                # A game (re)load rewrites characters/defaults wholesale;
                # publish the fresh baseline instead of leaving it pending
                # behind the 反映 button.
                StateManager.CommitHeld(self.statePrefix),
            ]
        )

        self.scoreColumn.findChild(QVBoxLayout, "verticalLayout").addWidget(self.individualGameTracker)

        self._groupSectionsIntoFrames(topOptions, bottomOptions)

        # tournamentInfo.* lives outside this board's held subtree, so EVENT
        # tab edits arrive as regular state_updated deltas; state_big_change
        # covers wholesale loads (startup state restore, Start.gg import).
        StateManager.signals.state_updated.connect(
            self._onTournamentInfoDelta)
        StateManager.signals.state_big_change.connect(
            self._updateTournamentInfoLine)
        StateManager.signals.state_big_change.connect(
            self._syncDisplayTournamentNameEdit)
        self._updateTournamentInfoLine()
        self._syncDisplayTournamentNameEdit()

        # From here on, edits to this board's subtree are held back from the
        # broadcast/export pipeline until 反映 commits them. Everything Set
        # during construction above was published normally, so the committed
        # baseline matches the widgets' initial values.
        StateManager.HoldSubtree(self.statePrefix)
        StateManager.signals.held_dirty_changed.connect(
            self._onHeldDirtyChanged)

    @staticmethod
    def _styleTeamCard(column):
        # Web Team.jsx parity: each team column is a Paper-like card — the
        # "1P"/"TEAM N" overline label left and the losers checkbox right in
        # the header row, card chrome via the shared QSS rule
        # (QWidget#teamCard, ui_constants).
        column.setObjectName("teamCard")
        column.layout().setContentsMargins(
            UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM, UI.SPACE_SM)
        teamLabel = column.findChild(QLabel, "teamLabel")
        teamLabel.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        teamLabel.setStyleSheet(
            f"color: {UI.palette()['text_secondary']};"
            " background: transparent; border: none;")

    def ApplyChanges(self):
        # 反映 button: publish every pending edit on this board at once.
        StateManager.CommitHeld(self.statePrefix)

    def _onTournamentInfoDelta(self, delta):
        # Same cheap containment filter AutonaStatsWidget uses on deltas.
        if "tournamentInfo" in str(delta):
            self._updateTournamentInfoLine()
            self._syncDisplayTournamentNameEdit()

    def _syncDisplayTournamentNameEdit(self):
        # Reflect other-board / EVENT-tab edits to displayTournamentName into
        # this board's own field, without fighting an in-progress edit here
        # (hasFocus) or resetting the cursor on a no-op update (same text).
        edit = getattr(self, "displayTournamentNameEdit", None)
        if edit is None or edit.hasFocus():
            return
        value = StateManager.Get("tournamentInfo.displayTournamentName", "") or ""
        if edit.text() != value:
            edit.setText(value)

    def _updateTournamentInfoLine(self):
        name = StateManager.Get("tournamentInfo.tournamentName", "") or ""
        event = StateManager.Get("tournamentInfo.eventName", "") or ""
        parts = []
        if name:
            parts.append(
                QApplication.translate("app", "大会名") + f": {name}")
        if event:
            parts.append(
                QApplication.translate("app", "種目") + f": {event}")
        label = getattr(self, "tournamentInfoLabel", None)
        if label is None:
            return
        label.setText("　".join(parts))
        label.setVisible(bool(parts))

    def ResetAllInputs(self):
        # すべての入力値をリセット: clear players, scores, phase/round,
        # BEST OF and team names in one shot (mirrors the Web UI "全クリア"
        # action). Values only — the team-mode player-count layout is kept.
        # The cleared state still goes through the 反映 gate like any edit.
        answer = QMessageBox.question(
            self,
            QApplication.translate("app", "すべての入力値をリセット"),
            QApplication.translate(
                "app", "スコアボードの内容を全てクリアしますか？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        with StateManager.SaveBlock():
            self.CommandClearAll()
            self.ClearScore()
            self.bestOfSpin.setValue(0)
            for column in (self.team1column, self.team2column):
                teamName = column.findChild(QLineEdit, "teamName")
                teamName.setText("")
                teamName.editingFinished.emit()

    def _onHeldDirtyChanged(self, prefix: str, dirty: bool):
        if prefix == self.statePrefix:
            # Query the current truth instead of trusting the (possibly
            # queued/stale) payload: a worker-thread write can land between
            # the commit and this slot running.
            self.btApply.setEnabled(
                StateManager.HasPendingChanges(self.statePrefix))

    def _UpdateEntrantsMatchItem(self, data):
        """Keep the 'Winners TOP{entrants}' / 'Losers TOP{entrants}' candidates
        (the real first-round headcount, e.g. TOP300 — both bracket sides open
        with the full field alive) at the top of the match combo, replacing
        those from the previously loaded tournament."""
        if not isinstance(data, dict):
            return
        # An empty dict means "tournament cleared" -> drop the old candidates.
        numEntrants = data.get("numEntrants") or 0
        newItems = [
            AutonaLocaleHelper.matchNames.get(key, fallback).format(numEntrants)
            for key, fallback in (("winners_top_n", "Winners TOP{0}"),
                                  ("losers_top_n", "Losers TOP{0}"))
        ] if numEntrants > 0 else []
        oldItems = self._entrantsMatchItems
        if oldItems == newItems:
            return
        self.matchCombo.blockSignals(True)
        try:
            for oldItem in oldItems:
                index = self.matchCombo.findText(oldItem)
                # Keep the entry the operator currently has selected.
                if index >= 0 and self.matchCombo.currentText() != oldItem:
                    self.matchCombo.removeItem(index)
            self._entrantsMatchItems = []
            for offset, newItem in enumerate(newItems):
                if self.matchCombo.findText(newItem) < 0:
                    self.matchCombo.insertItem(1 + offset, newItem)
                    self._entrantsMatchItems.append(newItem)
        finally:
            self.matchCombo.blockSignals(False)

    def _UpdatePoolCandidates(self, phases):
        """Prepend the loaded tournament's real pool names to the pool combo
        (after the leading empty item), replacing those from the previously
        loaded tournament. Generic template candidates (予選プールA-D) stay."""
        newItems = []
        for phase in (phases or []):
            groups = phase.get("groups") or []
            # Single-group phases have no meaningful pool name.
            if len(groups) < 2:
                continue
            for group in groups:
                name = group.get("name") or ""
                if name and name not in newItems:
                    newItems.append(name)
        oldItems = getattr(self, "_tournamentPoolItems", [])
        if oldItems == newItems:
            return
        self.poolCombo.blockSignals(True)
        try:
            for item in oldItems:
                index = self.poolCombo.findText(item)
                # Keep the entry the operator currently has selected.
                if index >= 0 and self.poolCombo.currentText() != item:
                    self.poolCombo.removeItem(index)
            self._tournamentPoolItems = []
            for offset, item in enumerate(newItems):
                if self.poolCombo.findText(item) < 0:
                    self.poolCombo.insertItem(1 + offset, item)
                    self._tournamentPoolItems.append(item)
        finally:
            self.poolCombo.blockSignals(False)

    def _groupSectionsIntoFrames(self, topOptions, bottomOptions):
        """Re-home the construction-time rows into the single scoreboard
        section frame in their final display order (display tournament name,
        the Start.gg load row, then phase/round/BEST OF, player columns, score
        controls and the apply/reset action row).

        The construction-time order below is incidental — this method is what
        fixes the display order, so add rows here rather than reshuffling the
        insertWidget calls in __init__.

        Current innerWidget child order is:
            topOptions, streamSection, displayTournamentNameRow, scoreBar,
            columns, bottomOptions
        """
        inner = self.innerWidget.layout()

        # Strip every previously-added child out of innerWidget so we
        # can re-home them into the frame without leaking duplicates.
        for w in (
            topOptions,
            self.streamSection,
            self.displayTournamentNameRow,
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
        matchLayout.addWidget(titleRow)

        # Read-only mirror of the EVENT tab's tournament name / event name,
        # so the operator sees which tournament the board is feeding without
        # switching tabs. Kept in sync via StateManager signals (the EVENT
        # tab publishes tournamentInfo.* immediately, not behind 反映).
        self.tournamentInfoLabel = QLabel()
        self.tournamentInfoLabel.setStyleSheet(
            f"color: {UI.palette()['text_secondary']};"
            " background: transparent; border: none;")
        self.tournamentInfoLabel.setWordWrap(True)
        self.tournamentInfoLabel.setVisible(False)
        matchLayout.addWidget(self.tournamentInfoLabel)

        matchLayout.addWidget(self.displayTournamentNameRow)

        # Start.gg load row (with its auto-update timer strip) sits right above
        # the phase/round/BEST OF bar — loading a set fills that bar in, so the
        # button sits next to what it populates.
        matchLayout.addWidget(self.streamSection)

        matchLayout.addWidget(self.scoreBar)

        # Sponsor toggle left, BEST OF right — one row, so the short controls
        # share a line instead of each eating a full-width strip.
        self.hideSponsorCheck = QCheckBox(
            QApplication.translate("app", "所属タグをスコアボードに表示しない"))
        self.hideSponsorCheck.setChecked(
            not SettingsManager.Get("display_options.show_sponsor", True))
        self.hideSponsorCheck.toggled.connect(self._onHideSponsorToggled)

        sponsorBestOfRow = QWidget()
        sponsorBestOfRowLayout = QHBoxLayout()
        sponsorBestOfRowLayout.setContentsMargins(0, 0, 0, 0)
        sponsorBestOfRowLayout.setSpacing(UI.SPACE_SM)
        sponsorBestOfRow.setLayout(sponsorBestOfRowLayout)
        sponsorBestOfRowLayout.addWidget(self.hideSponsorCheck)
        sponsorBestOfRowLayout.addStretch(1)
        sponsorBestOfRowLayout.addWidget(self.bestOfGroup)
        matchLayout.addWidget(sponsorBestOfRow)

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

        # self.columns is the shared 3-column grid carrying the player
        # columns, the score controls and the apply/reset action row.
        matchLayout.addWidget(self.columns)
        matchLayout.addWidget(bottomOptions)

        inner.addWidget(self.matchSectionFrame)
        inner.addStretch(1)
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
            # Card header label: hidden on the singles board (the 1P/2P
            # identity lives in the player-name placeholder instead);
            # "TEAM N" on the team board, where the team-name field also
            # shows.
            teamLabel = column.findChild(QLabel, "teamLabel")
            if teamLabel is not None:
                teamLabel.setVisible(not singlePlayer)
                if not singlePlayer:
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
        if getattr(self, "_playerNumberLabel", None) is not None:
            self._playerNumberLabel.setVisible(not singlePlayer)
        self.playerNumber.setVisible(not singlePlayer)
        if getattr(self, "_bottomOptions", None) is not None:
            self._bottomOptions.setVisible(not singlePlayer)
        if singlePlayer:
            self.playerNumber.setValue(1)
            self._placeLosersOnDetailsRow()

    def _placeLosersOnDetailsRow(self):
        # Singles board only: the card header this checkbox lived in is
        # otherwise empty (1P/2P label and team name are hidden), so the
        # LOSERS checkbox joins the solo player's ">>詳細" row instead,
        # right-aligned after the row's stretch. Reparenting keeps the
        # widget a descendant of its team column, so every existing
        # findChild(QCheckBox, "losers") lookup (state export, SwapTeams,
        # reset, SetData) keeps resolving the same instance. Safe on this
        # board because the player count is pinned to 1 — the hosting
        # player widget is never torn down by SetPlayersPerTeam.
        for column, players in ((self.team1column, self.team1playerWidgets),
                                (self.team2column, self.team2playerWidgets)):
            losersCheck = column.findChild(QCheckBox, "losers")
            if losersCheck is None or not players:
                continue
            if losersCheck.parentWidget() is players[0]:
                continue
            linkRow = getattr(players[0], "linkRow", None)
            if linkRow is not None:
                linkRow.addWidget(losersCheck)

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
            match = StateManager.Get(f"score.{n}.match", "") or ""
            stream_url = StateManager.Get("stream.url", "") or ""

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
            if not (phase.strip() or match.strip()):
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

            round_line = f"[{phase}]" if phase else ""
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
        # Desktop-side field hiding only applies to the inline sponsor field
        # nowadays (the details dialog always shows every field); this stays
        # generic because the widget lookup is shared with the team columns.
        visible = action.isChecked()
        for pw in self.playerWidgets:
            for element in elements:
                w = pw.findChild(QWidget, element)
                # Team-mode variant of the player widget may not ship with
                # every label objectName; silently skip missing widgets
                # rather than crashing the toggle.
                if w is not None:
                    w.setVisible(visible)

    def _onEyeActionToggled(self, action: QAction, element):
        # Non-sponsor display option: persist to SettingsManager (local
        # default) and StateManager (broadcast to Web UI / OBS) only. The
        # desktop details dialog always shows every field, so no Qt
        # visibility is applied here.
        show = action.isChecked()
        SettingsManager.Set(f"display_options.{element[2]}", show)
        StateManager.Set(f"display_options.{element[2]}", show)

    def SetDisplayOption(self, key: str, visible: bool):
        """Remote toggle entry point (Web UI eye menu -> POST /display-option).

        Drives the (invisible) display-option QAction so SettingsManager and
        StateManager stay in sync through the same handlers as before. Only
        show_sponsor still affects desktop widget visibility (inline sponsor
        field); the details dialog always shows every field.
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
            self.btSelectSet.setText(
                QApplication.translate("app", "大会の試合を読み込む"))
            self.btSelectSet.setEnabled(True)
            # Only actionable once a tournament is set — otherwise the button
            # has nothing to load, so hide it entirely rather than showing a
            # disabled control.
            self.btSelectSet.setVisible(True)
        else:
            self.btSelectSet.setEnabled(False)
            self.btSelectSet.setVisible(False)

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

        # The card header label is always visible (text set by
        # _applyScoreboardMode); only the team-name field follows the
        # player count.
        if multiPlayer:
            self.team1column.findChild(QLineEdit, "teamName").setVisible(True)
            self.team2column.findChild(QLineEdit, "teamName").setVisible(True)
        else:
            self.team1column.findChild(QLineEdit, "teamName").setVisible(False)
            self.team1column.findChild(QLineEdit, "teamName").setText("")
            self.team1column.findChild(
                QLineEdit, "teamName").editingFinished.emit()
            self.team2column.findChild(QLineEdit, "teamName").setVisible(False)
            self.team2column.findChild(QLineEdit, "teamName").setText("")
            self.team2column.findChild(
                QLineEdit, "teamName").editingFinished.emit()

        for pw in self.playerWidgets:
            pw.findChild(QLabel, "title").setVisible(multiPlayer)
            pw.btMoveUp.setVisible(multiPlayer)
            pw.btMoveDown.setVisible(multiPlayer)

        # Only the inline sponsor field still follows a display option on
        # the desktop; the details dialog always shows every other field.
        if self._sponsorAction is not None:
            self.ToggleElements(self._sponsorAction, ["team"])

        # birthday is not surfaced anywhere yet; keep it hidden regardless
        # of toggle state.
        for pw in self.playerWidgets:
            w = pw.findChild(QWidget, 'birthday')
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
                self.phaseCombo.setCurrentText(tournament_phase)
                self.phaseCombo.lineEdit().editingFinished.emit()
                StateManager.Set(f"score.{self.scoreboardNumber}.phase", tournament_phase)

            # Key-presence check (not truthiness): sets from a pool-less phase
            # carry tournament_pool = "" and must CLEAR the previous pool,
            # while a phase-only web edit (no tournament_pool key) must not
            # touch it.
            if "tournament_pool" in data:
                tournament_pool = data.get("tournament_pool") or ""
                self.poolCombo.setCurrentText(tournament_pool)
                self.poolCombo.lineEdit().editingFinished.emit()
                StateManager.Set(f"score.{self.scoreboardNumber}.pool", tournament_pool)

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
