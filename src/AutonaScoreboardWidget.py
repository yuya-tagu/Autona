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
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper

from . import ui_constants as UI
from . import ui_layout as L
from .AutonaSelectSetWindow import AutonaSelectSetWindow
from .AutonaSelectStationWindow import AutonaSelectStationWindow
from .AutonaIndividualGameTracker import AutonaIndividualGameTracker

from .AutonaScoreboardPlayerWidget import AutonaScoreboardPlayerWidget
from .core.SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT
from .core.StateManager import StateManager
from .core import StateSchema as schema
from .core import WebAccess
from .core.Actions import AutonaActions
from .core.PlayerService import (
    DEFAULT_CHARACTERS_PER_PLAYER, DefaultPlayersPerTeam)
from .core.SetLoadService import SetLoadService
from .core.TournamentInfoService import TournamentInfoService
from .StateManagerBridge import StateManagerBridge
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaStatsUtil import AutonaStatsUtil
from .AutonaHotkeys import AutonaHotkeys
from .core.PlayerDB import PlayerDB

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
    ChangeSetData = Signal(dict)


class AutonaScoreboardWidget(QWidget):
    stationQueueCache = QueueSetsCache()

    def __init__(self, scoreboardNumber=1, singlePlayer=True, *args):
        super().__init__(*args)

        self.scoreboardNumber = scoreboardNumber
        self.singlePlayer = singlePlayer
        # StateManager subtree owned by this board. Publication of the whole
        # subtree is deferred (HoldSubtree at the end of __init__): edits stay
        # local until the 反映 button commits them. StateSchema.STATS_KEYS are
        # exempt — they are fetched derived data, not operator edits, so they
        # publish immediately (stats overlays / preview would otherwise stay
        # empty until 反映 was pressed).
        self.statePrefix = f"score.{self.scoreboardNumber}"

        # Re-entrancy guard for _SyncScoreDisplays (agent-pitfalls #9). The
        # sync is driven by a state notification and can itself cause writes
        # (the individual game tracker clears/sets stage rows), which produce
        # another notification. Set before anything can notify us.
        self._syncingScore = False

        # The player cards render their slots from the state (ADR-005 S2a):
        # one pass, queued by the first held save cycle of an event-loop turn
        # (_SchedulePlayerRender), with a re-entrancy guard on the pass itself.
        self._playerRenderPending = False
        self._renderingPlayers = False

        # The last `score.{n}.teamsSwapped` a held cycle showed this board.
        # Which way round the sides are is the state's (ADR-005 S2c:
        # AutonaActions.SwapTeams toggles it, from the button, the hotkey or
        # the Web UI alike); this copy is only how _onHeldStateUpdated tells
        # that a cycle was a swap (_NoticeSwap).
        self._seenTeamsSwapped = False
        # Teams whose statistics a swap / move asked to refetch, emitted
        # once control is back in the event loop (_EmitPlayerStats).
        self._pendingStatsTeams = set()
        # {team: text}: what a team-name field showed when _SyncSetFields
        # had to leave it alone because the operator was in it. Finishing
        # the edit with that very text is not an edit (_OnTeamNameFinished),
        # and once the focus has left the field it is rendered from the
        # state (_OnTeamNameFocusChanged) -- the player cards' rule
        # (AutonaScoreboardPlayerWidget._heldBackText).
        self._heldBackTeamName = {}
        # The value _SyncBestOf left on the BEST OF spinbox because the
        # operator was in it (None when it left nothing): shown once they
        # leave it, and not written back over the newer one meanwhile
        # (_onBestOfChanged).
        self._heldBackBestOf = None

        # Set loading (Start.gg payloads, /post_data, set_route): what a
        # loaded set means for this board's state lives in core since
        # headless core Step 4b. This widget renders the result -- see
        # _SyncSetFields / _SyncScoreDisplays.
        self.setLoad = SetLoadService(self.scoreboardNumber)

        # Same skeleton AutonaScoreboardManager writes for the boards that are
        # not built yet, from the same place (headless core Step 2).
        schema.SeedBoard(self.scoreboardNumber)

        self.signals = AutonaScoreboardWidgetSignals()
        self.signals.UpdateSetData.connect(self.UpdateSetData)
        self.signals.NewSetSelected.connect(self.NewSetSelected)
        self.signals.StationSetsLoaded.connect(self.StationSetsLoaded)
        self.signals.SetSelection.connect(self.LoadSetClicked)
        self.signals.StationSelected.connect(self.LoadStationSets)
        self.signals.StationSelection.connect(self.LoadStationSetClicked)
        self.signals.ChangeSetData.connect(self.ChangeSetData)

        # Only the hotkey that drives a *panel* (the set-selection window) is
        # still connected here. The score / reset / 反映 hotkeys were moved
        # to AutonaHotkeys -> AutonaActions (headless core Step 3), and the
        # swap joined them in ADR-005 S2c: connected per board, they answered
        # from every board that happened to be built -- both of them after a
        # singles/team switch, and none at all when the saved format was
        # crew.
        #
        # Bound methods, never lambdas: AutonaHotkeys.signals is a class-level
        # object that lives for the whole process, while a board is destroyed
        # on every battle-format switch. A bound method tells Qt who the
        # receiver is, so the connection dies with the board; a lambda does
        # not, and nothing ever disconnects it -- the destroyed board would
        # keep answering hotkeys and rewriting score.{n} (the "ghost board").
        AutonaHotkeys.signals.load_set.connect(self.LoadSetClicked)

        self.signals.CommandScoreChange.connect(self.CommandScoreChange)
        self.signals.CommandTeamColor.connect(self.CommandTeamColor)

        self.stats = AutonaStatsUtil(self.scoreboardNumber, self)

        # (The set loaded last is self.setLoad.lastSetSelected since ADR-005
        # S2d: the clears that forget it are core's.)

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
        self.webScoreboardUrl = f"http://{GetWebServerHost()}:{SettingsManager.Get('general.webserver_port', DEFAULT_WEBSERVER_PORT)}/{webScoreboardPath}"
        self.btOpenWebScoreboard = QPushButton("ブラウザ版を開く")
        self.btOpenWebScoreboard.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btOpenWebScoreboard.clicked.connect(
            lambda: webbrowser.open(self._WebScoreboardOpenUrl()))

        # The eye (field-visibility) menu was removed from the desktop UI:
        # the details dialog always shows every field. Nothing adds these
        # QActions to a menu any more, so their text is never rendered —
        # they survive as invisible state carriers, and show_sponsor alone
        # still drives the inline sponsor field's Qt visibility.
        #
        # They no longer *write* anything: the toggle is
        # AutonaActions.SetDisplayOption, and this board mirrors the result
        # back into these actions from the state notification
        # (_SyncDisplayOptions, headless core Step 3). That is what keeps the
        # local checkbox, a Web UI eye-menu click and the OBS overlays showing
        # the same thing without a second writer.
        #
        # The keys, their defaults and the fields each one hides live in
        # StateSchema.DISPLAY_OPTIONS (headless core Step 2) — they used to be
        # spelled out here and again in WebServerActions. Only the labels are
        # Qt's business, so only they stay. They stay *translated* despite
        # never being rendered because most of these source strings also
        # label visible UI elsewhere (player browser columns, commentary,
        # result settings): removing them from the .ts files would blank
        # those too.
        labels = {
            "show_sponsor":    QApplication.translate("app", "Sponsor"),
            "show_name":       QApplication.translate("app", "Player Name"),
            "show_seed":       QApplication.translate("app", "Seed"),
            "show_characters": QApplication.translate("app", "Characters"),
            "show_social":     QApplication.translate("app", "Twitter"),
            "show_real_name":  QApplication.translate("app", "Real Name"),
            "show_location":   QApplication.translate("app", "Location"),
            "show_additional": QApplication.translate(
                "app", "Additional information"),
        }
        self._sponsorAction: QAction = None
        # settings key -> QAction. Read-only carriers: nothing is connected to
        # their toggled signal, so setting one can never write state back.
        self._eyeActions = {}
        for option in schema.DISPLAY_OPTIONS:
            # Falls back to the raw key rather than raising: these actions are
            # invisible state carriers, so a schema entry added without a
            # label here must not take the whole board down with a KeyError.
            action = QAction(labels.get(option.key, option.key), self)
            action.setCheckable(True)
            action.setChecked(SettingsManager.Get(
                f"display_options.{option.key}", option.default))
            self._eyeActions[option.key] = action
            if option.key == "show_sponsor":
                self._sponsorAction = action

        # Mirror every display option into StateManager so remote clients
        # (React Web UI, OBS) can render the same field visibility. Before
        # this existed only show_sponsor was mirrored; the other toggles were
        # session-local Qt visibility. The loop itself is the schema's now
        # (headless core Step 5) -- it read this widget's own copy of the
        # option list, which is exactly the drift StateSchema exists to end.
        schema.PublishDisplayOptions()

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
        # setValue() and SetDefaultsFromAssets re-asserts it. Default:
        # core's DEFAULT_CHARACTERS_PER_PLAYER (the headless seed's too).
        self.charNumber = QSpinBox(self)
        self.charNumber.setMaximum(100)
        self.charNumber.valueChanged.connect(self.SetCharacterNumber)
        self.charNumber.setValue(DEFAULT_CHARACTERS_PER_PLAYER)
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

        self._WireTeamColumn(self.team1column, 1)

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

        self._WireTeamColumn(self.team2column, 2)
        # A bound method: Qt drops the connection with the board.
        QApplication.instance().focusChanged.connect(
            self._OnTeamNameFocusChanged)

        # The restored state (user_data/state.json) can carry a roster from
        # a session that ran with more players per team; drop both before
        # the first player widget is built, so the slots that come back are
        # the ones this board actually has.
        for team in (1, 2):
            AutonaActions.instance.ClearTeamRoster(self.scoreboardNumber, team)
        self.playerNumber.setValue(1)
        # Re-assert the characters-per-player default so the freshly-built
        # player widgets pick it up instead of getting pinned to 1.
        self.charNumber.setValue(DEFAULT_CHARACTERS_PER_PLAYER)

        self._applyScoreboardMode(self.singlePlayer)

        # Match bar (フェーズ / プール名 / ラウンド). Both signals are wired
        # because either can change the text: currentIndexChanged for a pick
        # from the list, editingFinished for something typed into the
        # editable line edit. Which key each combo writes comes from
        # StateSchema now rather than from its own objectName -- see
        # _WireTeamColumn for the whole story.
        for field in schema.SCORE_MATCH_FIELDS:
            combo = self.scoreColumn.findChild(QComboBox, field)
            if combo is None:
                # A field the .ui no longer carries would otherwise fail one
                # line later on None, with nothing naming the missing widget.
                logger.error(
                    f"AutonaScoreboardScore.ui has no '{field}' combo; the "
                    f"score.{self.scoreboardNumber}.{field} key will not be "
                    "written")
                continue
            combo.lineEdit().editingFinished.connect(
                lambda field=field, element=combo:
                    AutonaActions.instance.SetMatchField(
                        self.scoreboardNumber, field, element.currentText()))
            combo.currentIndexChanged.connect(
                lambda _index, field=field, element=combo:
                    AutonaActions.instance.SetMatchField(
                        self.scoreboardNumber, field, element.currentText()))
            combo.lineEdit().editingFinished.emit()
            combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Add stage order widget
        self.individualGameTracker = AutonaIndividualGameTracker(self.scoreboardNumber)
        self.individualGameTracker.signals.stageResultsUpdate.connect(self.StageResultsToScore)
        # The set length resizes the rows in core (AutonaActions.SetBestOf,
        # ADR-005 S2e), through the tracker's service registered here for as
        # long as this board exists. Undone from the board's own `destroyed`:
        # the tracker sits in the score column, which can outlive the board,
        # and the lambda holds the number and the service, not the board.
        trackerService = self.individualGameTracker.service
        AutonaActions.instance.RegisterGameTracker(
            self.scoreboardNumber, trackerService)
        self.destroyed.connect(
            lambda _obj=None, number=self.scoreboardNumber,
            service=trackerService:
                AutonaActions.instance.UnregisterGameTracker(number, service))

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
        self.phaseCombo = self.scoreColumn.findChild(QComboBox, schema.PHASE_KEY)
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
        self.poolCombo = self.scoreColumn.findChild(QComboBox, schema.POOL_KEY)
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
        self.matchCombo = self.scoreColumn.findChild(QComboBox, schema.MATCH_KEY)
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
        # tournamentInfo.* is TournamentInfoService's, EVENT panel or no
        # EVENT panel (headless core Step 4b). Typing here is the strongest
        # of the three writers of 表示大会名: it does not disturb the
        # service's "name the display last followed" ledger, which is what
        # stops a later tournament-name edit from overwriting what was typed.
        self.displayTournamentNameEdit.editingFinished.connect(
            lambda: TournamentInfoService.instance.SetDisplayName(
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
            # Stated rather than inherited from QSpinBox's defaults: the same
            # bounds are what AutonaActions clamps a score adjustment to, and
            # a spinbox that silently disagreed would either refuse a value
            # the state already holds or let one through that the headless
            # path rejects.
            display.setRange(AutonaActions.SCORE_MIN, AutonaActions.SCORE_MAX)

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

        # QGridLayout derives each column's minimum width from the widest
        # item placed in it across ALL rows, not just the team card in row
        # 0 — setColumnStretch only distributes space *above* that minimum.
        # btResetAll's long label ("すべての入力値をリセット" + icon) is far
        # wider than btApply's ("反映"), so without this it silently pins
        # column 0 (the 1P/left card) wide while column 1 (2P/right card)
        # is left free to shrink, breaking the equal-stretch symmetry the
        # two team columns rely on. Same fix as save_bt/delete_bt/clear_bt
        # in AutonaScoreboardPlayerWidget.
        self.btResetAll.setMinimumWidth(1)
        self.btApply.setMinimumWidth(1)

        columnsGrid.addWidget(self.btResetAll, 2, 0)
        columnsGrid.addWidget(self.btApply, 2, 1)

        self.innerWidget.layout().removeWidget(self.scoreColumn)
        self.scoreColumn.findChild(QGroupBox, "scoreGroupBox").setTitle("")

        # The spinbox asks for the change and renders the state
        # (_SyncBestOf); the range is the operation's, so the two cannot
        # drift apart. The construction's 0 is core's too: the seeded
        # subtree has no best_of, so this writes the six keys and the empty
        # rows, as the emitted valueChanged(0) did.
        self.bestOfSpin.setRange(
            AutonaActions.BEST_OF_MIN, AutonaActions.BEST_OF_MAX)
        self.bestOfSpin.valueChanged.connect(self._onBestOfChanged)
        self.bestOfSpin.editingFinished.connect(self._OnBestOfEditingFinished)
        AutonaActions.instance.SetBestOf(self.scoreboardNumber, 0)
        self._SyncBestOf()

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

        # (The team-name -> crest connection used to be made here, after the
        # generic objectName loops above had already seeded the names. It is
        # part of _WireTeamColumn now, so both halves of "the operator named
        # a team" are wired in one place -- see that method.)

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

        # Bound method, not a lambda: the asset manager outlives every board
        # (see the AutonaHotkeys block in __init__ for the full reasoning).
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            self._onGameAssetsLoaded)

        self.scoreColumn.findChild(QVBoxLayout, "verticalLayout").addWidget(self.individualGameTracker)

        self._groupSectionsIntoFrames(topOptions, bottomOptions)

        # tournamentInfo.* and display_options.* live outside this board's
        # held subtree, so edits to either arrive as regular state_updated
        # deltas; state_big_change covers wholesale loads (startup state
        # restore, Start.gg import).
        StateManagerBridge.instance.state_updated.connect(
            self._onStateDelta)
        StateManagerBridge.instance.state_big_change.connect(
            self._onStateBigChange)
        self._updateTournamentInfoLine()
        self._syncDisplayTournamentNameEdit()

        # From here on, edits to this board's subtree are held back from the
        # broadcast/export pipeline until 反映 commits them. Everything Set
        # during construction above was published normally, so the committed
        # baseline matches the widgets' initial values.
        schema.HoldBoard(self.scoreboardNumber)
        StateManagerBridge.instance.held_dirty_changed.connect(
            self._onHeldDirtyChanged)
        # The score is written by AutonaActions now, not by these spinboxes
        # (headless core Step 3), so the displays follow the state instead of
        # leading it. held_state_updated is the notification for a held
        # subtree: it fires once per save cycle while the edits are still
        # pending behind 反映.
        StateManagerBridge.instance.held_state_updated.connect(
            self._onHeldStateUpdated)

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

    def _WireTeamColumn(self, column, team: int):
        """Bind one team card's inputs to the operations they stand for.

        This used to be two `findChildren` loops per column -- every
        QLineEdit and every QCheckBox connected to a lambda that built
        `score.{N}.team.{T}.{widget.objectName()}` and wrote it. That is the
        pattern that made the .ui file the state schema (known-smells S-14):
        the keys existed only as a side effect of what a designer happened to
        name a widget, and no catalog could be trusted because the widget
        tree was the catalog.

        Now the names come from `StateSchema.SCORE_TEAM_INPUT_KEYS` and are
        used to *find* the widgets; the writes are AutonaActions'. The
        objectNames still have to match those keys, which is the same
        backward-compatibility constraint as before (an existing
        program_state.json is keyed by them) -- pinned by
        tests/test_objectname_keypath_contract.py.

        The name's handler also publishes the team crest that follows it
        (ExportTeamLogo, via _OnTeamNameFinished), and that is load-bearing:
        it may not be replaced by a state notification -- `_SyncSetFields`
        sets these two widgets with their signals blocked precisely because
        it is the render side. The losers box used to have
        one too (every player card's `SetLosers`, re-exporting its name with
        the " [L]" suffix); since ADR-005 S2b that is part of
        `AutonaActions.SetTeamLosers`, so the box has a single handler.

        The initial emit seeds the two keys, as it always did: a board starts
        with an empty name and an unchecked losers box, and the overlays read
        both from launch.
        """
        name = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
        if name is not None:
            name.editingFinished.connect(
                lambda team=team, edit=name:
                    self._OnTeamNameFinished(team, edit))
            name.editingFinished.emit()

        losers = column.findChild(QCheckBox, schema.TEAM_LOSERS_KEY)
        if losers is not None:
            losers.toggled.connect(
                lambda state, team=team: AutonaActions.instance.SetTeamLosers(
                    self.scoreboardNumber, team, state))
            losers.toggled.emit(False)

        if name is None or losers is None:
            logger.error(
                f"AutonaScoreboardTeam.ui is missing "
                f"{schema.TEAM_NAME_KEY!r} / {schema.TEAM_LOSERS_KEY!r}; "
                f"score.{self.scoreboardNumber}.team.{team} will be "
                "incomplete")

    def _OnTeamNameFinished(self, team, edit):
        """A team-name field finished an edit: the name, then the crest
        that follows it (ExportTeamLogo), in the order the two used to be
        connected in -- a set load writes the pair together through
        SetLoadService.ApplyTeamName.

        Unless the text is the one a render had to leave in the field while
        the operator was in it (_heldBackTeamName) and the state has moved
        on since: the Web / API wrote a name underneath the edit, and
        finishing it -- the focus-out, Enter -- would put the old text back
        over it. Any other text is the operator's edit and is written. The
        name and the crest are each written only when they change
        (SetTeamName / PublishTeamLogo), so re-finishing the name the state
        holds writes nothing.
        """
        text = edit.text()
        held = self._heldBackTeamName.get(team)
        if held is not None and held == text and StateManager.Get(
                f"{self.statePrefix}.team.{team}.{schema.TEAM_NAME_KEY}",
                None) != text:
            return
        self._heldBackTeamName.pop(team, None)
        AutonaActions.instance.SetTeamName(self.scoreboardNumber, team, text)
        self.ExportTeamLogo(team, text)

    def _OnTeamNameFocusChanged(self, old, new):
        """QApplication.focusChanged: once the operator has left a team-name
        field a render had to skip, show the state in it. Deferred, so that
        nothing is redrawn inside the focus change (agent-pitfalls #9) and
        the field's own editingFinished has run first."""
        if not self._heldBackTeamName:
            return
        for team in self._heldBackTeamName:
            column = self.team1column if team == 1 else self.team2column
            edit = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
            if edit is not None and edit.hasFocus():
                return
        QTimer.singleShot(0, self, self._SyncSetFields)

    def ApplyChanges(self):
        # 反映 button: publish every pending edit on this board at once.
        AutonaActions.instance.ApplyChanges(self.scoreboardNumber)

    def _WebScoreboardOpenUrl(self):
        """The "ブラウザ版を開く" URL (ADR-005 S3a).

        With LAN access on, it carries the access token, so the same URL
        also works when copied to another device (the page takes the token
        off the address bar: web/src/autonaToken.ts). Read at click time, so
        a regenerated token is the one sent. With LAN access off it has
        none: a page on this PC needs none, and one opened without it is not
        cut off when the token is regenerated.
        """
        token = WebAccess.Token()
        if not token or not SettingsManager.Get(
                "general.webserver_listen_lan", False):
            return self.webScoreboardUrl
        return (f"{self.webScoreboardUrl}?"
                + urllib.parse.urlencode({"token": token}))

    def _onGameAssetsLoaded(self):
        # AutonaGameAssetManager.signals.onLoad handler.
        # First, while nothing below can have raised: the asset manager
        # replaced its models, so no stage row may stay on the retired one.
        self.individualGameTracker.RebindStageModel()
        self.SetDefaultsFromAssets()
        # The set length again, rows included (they recorded games of the
        # previous game's stages): what re-emitting the spinbox's
        # valueChanged did, now core's. The rows are rebuilt from its held
        # cycle.
        AutonaActions.instance.RepublishBestOf(self.scoreboardNumber)
        # A game (re)load rewrites characters/defaults wholesale; publish the
        # fresh baseline instead of leaving it pending behind the 反映 button.
        StateManager.CommitHeld(self.statePrefix)

    def _onBestOfChanged(self, value):
        """bestOfSpin.valueChanged handler: the operator set the length.

        One call, AutonaActions.SetBestOf: the six keys and the stage rows,
        in one save cycle (ADR-005 S2e). The spinbox and the rows then show
        what the state holds (_SyncBestOf, from the held cycle) -- the
        spinbox used to be the length's source of truth, and every other
        entry point (a set load, the Web, a game load, the resets) drove it.
        A render sets it with its signals blocked, so this only ever runs
        for the operator's own edit -- or for Return, which makes Qt emit
        valueChanged for the value the spinbox already holds. That one is
        not written while a render left it there over a newer length (the
        Web's, say): the operator did not change it, as _OnTeamNameFinished
        does not write back a held-back team name.
        """
        if self._heldBackBestOf is not None and value == self._heldBackBestOf:
            return
        AutonaActions.instance.SetBestOf(self.scoreboardNumber, value)

    def _OnBestOfEditingFinished(self):
        """bestOfSpin.editingFinished (Return, or the focus leaving): show
        the state in the spinbox if a render had to skip it while the
        operator was in it. Deferred, as the team name's is, so that nothing
        is redrawn inside the focus change (agent-pitfalls #9). After Return
        the operator is still in it, and the render leaves it alone again."""
        if self._heldBackBestOf is not None:
            QTimer.singleShot(0, self, self._SyncBestOf)

    def _onStateDelta(self, delta):
        # Same cheap containment filter AutonaStatsWidget uses on deltas. One
        # slot for both subtrees so the (not free) str(delta) is paid once.
        text = str(delta)
        if "tournamentInfo" in text:
            self._updateTournamentInfoLine()
            self._syncDisplayTournamentNameEdit()
        if "display_options" in text:
            self._SyncDisplayOptions()

    def _onStateBigChange(self):
        self._updateTournamentInfoLine()
        self._syncDisplayTournamentNameEdit()
        self._SyncDisplayOptions()

    def _onHeldStateUpdated(self, prefix: str):
        # Live-but-unpublished data under a held prefix changed. Only this
        # board's own subtree is of interest here.
        if prefix == self.statePrefix:
            # First: it only queues, and a sync below that raises must not
            # leave the cards behind the state.
            self._SchedulePlayerRender()
            swapped = self._NoticeSwap()
            self._SyncSetFields()
            # Before the score: a cycle that resized the rows (a set load's
            # best of with its score) has its transition followed onto the
            # new ones, as when the spinbox rebuilt them inside the write.
            self._SyncBestOf()
            # A swap exchanged the scores and mirrored the game rows in the
            # same cycle (AutonaActions.SwapTeams): the rows already show the
            # new scores, so the tracker is not told about the transition.
            self._SyncScoreDisplays(followStages=not swapped)

    def _SchedulePlayerRender(self):
        """Have every player card show its slot, once control is back in
        the event loop (ADR-005 S2a).

        Deferred, unlike the two syncs in _onHeldStateUpdated, and that is
        the point rather than an optimisation. The player slots are still
        written by the cards themselves, an operation at a time, and an
        operation can take several save cycles -- a country pick publishes
        the rebuilt region before the country, SetData and Clear publish
        field by field. In
        between, the card is legitimately ahead of the state; a render run
        right there would put the old country back on the combo, and the
        country handler still to come would then publish *that*. Once the
        operation has returned, card and state agree again, and a render
        only changes what was written somewhere else.

        Coalesced: however many cycles a turn produces, one pass is queued.
        The board is the timer's context, so a board torn down first takes
        the pending pass with it. A card also asks for a pass once the
        operator has left a field the last one had to skip
        (render_requested).
        """
        if self._playerRenderPending:
            return
        self._playerRenderPending = True
        QTimer.singleShot(0, self, self._RenderPlayerCards)

    def _RenderPlayerCards(self):
        self._playerRenderPending = False
        if self._renderingPlayers:
            # Nothing in a pass can bring us back here (it writes nothing,
            # agent-pitfalls #9), but a pass must never stack on another.
            logger.warning(
                f"{self.statePrefix}: re-entrant player render, skipping")
            return
        self._renderingPlayers = True
        try:
            for card in list(self.playerWidgets):
                try:
                    card.RenderFromState()
                except Exception:
                    logger.exception(
                        f"{self.statePrefix}: rendering {card.path} failed")
        finally:
            self._renderingPlayers = False

    def _SyncSetFields(self):
        """Render the set-level state into the match bar and team cards.

        The counterpart of _SyncScoreDisplays for everything a loaded set
        carries besides the score: round / phase / pool, the losers flags and
        the team names. Since headless core Step 4b the writer is
        core/SetLoadService (a Start.gg set load, /post_data, set_route), and
        these widgets show what it wrote instead of being poked into writing
        it themselves.

        This method writes nothing -- that is what makes it safe to run on
        every held notification without a re-entrancy guard. Each widget is
        updated inside a QSignalBlocker, because the handlers it would
        otherwise fire (the objectName-to-keypath lambdas, still the write
        path for a human editing the field) would write the value straight
        back. That is a real echo for the combos' currentIndexChanged; for
        the teamName QLineEdit it is defensive rather than closing a live
        path -- setText() does not itself emit editingFinished (only
        Return/focus-out do) -- but the blocker is kept for symmetry with
        the rest of this method.

        A field the operator currently has focus in is left alone: a
        notification arriving mid-edit must not yank the text out from under
        the caret. Same rule as _syncDisplayTournamentNameEdit. A team name
        left alone is also remembered (_heldBackTeamName): finishing the
        edit with that text does not write it back over the newer name, and
        the field shows the state once the focus has left it -- as a player
        card's fields do (ADR-005 S2d review).
        """
        for combo, key in ((self.matchCombo, schema.MATCH_KEY),
                           (self.phaseCombo, schema.PHASE_KEY),
                           (self.poolCombo, schema.POOL_KEY)):
            value = StateManager.Get(f"{self.statePrefix}.{key}", None)
            if not isinstance(value, str) or combo.currentText() == value:
                continue
            edit = combo.lineEdit()
            if edit is not None and edit.hasFocus():
                continue
            # Both objects: currentIndexChanged is the combo's, while
            # editingFinished belongs to the line edit, and each of them is
            # connected to the same write.
            with QSignalBlocker(combo):
                if edit is not None:
                    edit.blockSignals(True)
                try:
                    combo.setCurrentText(value)
                finally:
                    if edit is not None:
                        edit.blockSignals(False)

        for team, column in ((1, self.team1column), (2, self.team2column)):
            # findChild rather than a cached reference: the losers checkbox
            # is re-homed into the solo player's row on the singles board
            # (_placeLosersOnDetailsRow), and a cached wrapper would outlive
            # the C++ object if its host were ever torn down.
            losers = StateManager.Get(
                f"{self.statePrefix}.team.{team}.{schema.TEAM_LOSERS_KEY}",
                None)
            check = column.findChild(QCheckBox, schema.TEAM_LOSERS_KEY)
            if (isinstance(losers, bool) and check is not None
                    and check.isChecked() != losers):
                with QSignalBlocker(check):
                    check.setChecked(losers)

            name = StateManager.Get(
                f"{self.statePrefix}.team.{team}.{schema.TEAM_NAME_KEY}", None)
            edit = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
            if not isinstance(name, str) or edit is None:
                continue
            if edit.text() == name:
                self._heldBackTeamName.pop(team, None)
            elif edit.hasFocus():
                # Left alone, and remembered: finishing the edit with this
                # text must not write it back over the newer name
                # (_OnTeamNameFinished).
                self._heldBackTeamName[team] = edit.text()
            else:
                self._heldBackTeamName.pop(team, None)
                with QSignalBlocker(edit):
                    edit.setText(name)

    def _SyncBestOf(self):
        """Render the set length: the BEST OF spinbox and the game
        tracker's rows (ADR-005 S2e). Writes nothing.

        `score.{n}.best_of` and `score.{n}.stages` are written together by
        AutonaActions.SetBestOf, from every entry point. The spinbox is set
        with its signals blocked -- its valueChanged is the operator's edit
        -- and left alone while the operator is in it, as _SyncSetFields
        leaves a focused field; it shows the state once they leave it
        (_OnBestOfEditingFinished). The rows follow whatever the state
        holds, focus or not.
        """
        value = StateManager.Get(f"{self.statePrefix}.best_of", None)
        if (isinstance(value, int) and not isinstance(value, bool)
                and self.bestOfSpin.value() != value):
            if self.bestOfSpin.hasFocus():
                # Left alone, and remembered (_onBestOfChanged).
                self._heldBackBestOf = self.bestOfSpin.value()
            else:
                self._heldBackBestOf = None
                with QSignalBlocker(self.bestOfSpin):
                    self.bestOfSpin.setValue(value)
        else:
            self._heldBackBestOf = None
        self.individualGameTracker.RenderStageRows()

    def _SyncScoreDisplays(self, followStages=True):
        """Bring the two score spinboxes in line with the state.

        The state is the source of truth for the score since Step 3: an
        adjustment made through AutonaActions -- from the +1 button, a
        hotkey, the Web UI or the API -- lands in `score.{n}.team.{t}.score`
        and arrives here as a notification.

        Two things make this safe to run on every held notification:

        - the spinbox is updated inside a QSignalBlocker, so valueChanged
          (which still writes the score for the one path that drives the
          spinbox directly -- a number typed into it) cannot fire and write
          the value straight back;
        - the individual game tracker is then told about the transition
          explicitly, with the *old* spinbox value, because that is what it
          needs to decide which stage rows to award or clear. It is the one
          thing in here that can write state, hence the re-entrancy guard.

        The one transition the tracker must NOT be told about is the one it
        produced itself. A stage button click sums the rows and publishes the
        result, and that write lands here with the spinbox still on the old
        value -- indistinguishable from an external +1 unless the tracker says
        so, which is what `writingScore` is for. Without it a single click on
        "game 1: T1" also awarded game 2.

        Nor is it told about a swap's (`followStages=False`, ADR-005 S2c):
        the swap mirrored the rows along with the scores, and following the
        exchanged scores re-awarded games on top of the mirror -- T2 T1 T1
        (2-1) came out as T1 T2 T1.

        The rows a cycle moves, for both teams, are one save cycle (ADR-005
        S2e): a score cleared with three games recorded used to follow in
        three, a row at a time. Nothing is written when nothing follows, so
        the block costs no cycle then.
        """
        if self._syncingScore:
            return
        self._syncingScore = True
        try:
            tracker = self.individualGameTracker
            with StateManager.SaveBlock():
                for team, display in ((1, self.scoreDisplay1),
                                      (2, self.scoreDisplay2)):
                    value = StateManager.Get(
                        f"{self.statePrefix}.team.{team}.score", None)
                    if not isinstance(value, int) or isinstance(value, bool):
                        # Nothing renders a non-numeric score, so the spinbox
                        # is left alone -- but a writer that put one there is
                        # a bug worth a breadcrumb rather than a silent skip.
                        if value is not None:
                            logger.warning(
                                f"{self.statePrefix}.team.{team}.score is "
                                f"{value!r}, not an int; leaving the display "
                                f"at {display.value()}")
                        continue
                    previous = display.value()
                    if previous == value:
                        continue
                    with QSignalBlocker(display):
                        display.setValue(value)
                    if tracker.writingScore or not followStages:
                        continue
                    # setValue clamps to the spinbox range, so read back what
                    # it actually took rather than assuming the state value
                    # fit.
                    tracker.SyncStagesToScore(
                        team - 1, previous, display.value())
        finally:
            self._syncingScore = False

    def _SyncDisplayOptions(self):
        """Mirror display_options.* into this board's Qt carriers.

        The write path is AutonaActions.SetDisplayOption (local checkbox, Web
        UI eye menu and POST /display-option all go through it), so everything
        here is one-way: read the state, set the widgets with their signals
        blocked. show_sponsor is the only option with a desktop effect left --
        the inline sponsor field's visibility and the "所属タグを…しない"
        checkbox.
        """
        sponsorChanged = False
        for key, action in self._eyeActions.items():
            value = StateManager.Get(f"display_options.{key}", None)
            if not isinstance(value, bool) or action.isChecked() == value:
                continue
            with QSignalBlocker(action):
                action.setChecked(value)
            if action is self._sponsorAction:
                sponsorChanged = True
        # Everything below re-derives the sponsor field's Qt visibility, which
        # means walking every player widget. This runs on every
        # display_options delta -- including the seven options with no desktop
        # effect at all -- so it is worth doing only when show_sponsor is the
        # option that moved. The initial application is not this method's job:
        # _setPlayersPerTeamImpl re-asserts it whenever the player widgets are
        # (re)built.
        if self._sponsorAction is None or not sponsorChanged:
            return
        show = self._sponsorAction.isChecked()
        checkBox = getattr(self, "hideSponsorCheck", None)
        if checkBox is not None and checkBox.isChecked() == show:
            with QSignalBlocker(checkBox):
                checkBox.setChecked(not show)
        self.ToggleElements(self._sponsorAction, ["team"])

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
        # すべての入力値をリセット: clear players, scores, round name / phase /
        # pool, BEST OF and team names in one shot. Values only — the
        # team-mode player-count layout is kept. The Web UI's "全クリア"
        # (WebServerActions.clear_all) is its counterpart but differs on
        # purpose: it resets the player / character counts and keeps the
        # team names unless that shrinks the roster to one player (ADR-005
        # S2 decision 3). The cleared state still goes through the 反映 gate
        # like any edit.
        answer = QMessageBox.question(
            self,
            QApplication.translate("app", "すべての入力値をリセット"),
            QApplication.translate(
                "app", "スコアボードの内容を全てクリアしますか？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        # The clear is core's (AutonaActions.ClearBoard, ADR-005 S2d), the
        # best of included since S2e. The block also takes in the focused
        # field _ClearingPlayers commits first, as it always has.
        with StateManager.SaveBlock():
            self._ClearingPlayers(lambda: AutonaActions.instance.ClearBoard(
                self.scoreboardNumber))

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
        """Show the score the tracker just summed from its stage rows.

        Display only, since the review of headless core Step 4a. Both keys
        are already written -- GameTrackerService.PublishStageResults() does
        it in one save cycle before this signal is emitted -- so re-Setting
        them here bought nothing and cost two more save cycles per click,
        outside the writingScore guard at that (six per click before Step 4a,
        four after, two now; pinned by
        test_a_stage_click_is_exactly_two_save_cycles).

        The spinboxes are still set here rather than left to
        _SyncScoreDisplays. Every board built from this class holds its
        subtree (see HoldSubtree in __init__), so today that handler does run
        first and this is a no-op refresh -- but it is the one path that does
        not depend on the notification arriving, and it is what the tracker's
        signal has always meant.
        """
        with QSignalBlocker(self.scoreDisplay1):
            self.scoreDisplay1.setValue(team_1_score)

        with QSignalBlocker(self.scoreDisplay2):
            self.scoreDisplay2.setValue(team_2_score)

    def closeEvent(self, event):
        # Both timers stay None until NewSetSelected() creates them (BUG-5:
        # closing a board that never had a set loaded used to raise
        # AttributeError here; Qt swallows exceptions from event handlers so
        # it was silent, but it is still an unhandled exception on the path).
        if self.autoUpdateTimer is not None:
            self.autoUpdateTimer.stop()
        if self.timeLeftTimer is not None:
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
            teamName = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
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
        # findChild(QCheckBox, "losers") lookup (state export, the render,
        # reset, SetData) keeps resolving the same instance. Safe on this
        # board because the player count is pinned to 1 — the hosting
        # player widget is never torn down by SetPlayersPerTeam.
        for column, players in ((self.team1column, self.team1playerWidgets),
                                (self.team2column, self.team2playerWidgets)):
            losersCheck = column.findChild(QCheckBox, schema.TEAM_LOSERS_KEY)
            if losersCheck is None or not players:
                continue
            if losersCheck.parentWidget() is players[0]:
                continue
            linkRow = getattr(players[0], "linkRow", None)
            if linkRow is not None:
                linkRow.addWidget(losersCheck)

    def ExportTeamLogo(self, team, value):
        # The team-name QLineEdit's editingFinished handler (a human typing).
        # The rule itself -- which file backs which name -- is core's, so a
        # name written by a set load resolves its crest the same way
        # (SetLoadService.ApplyTeamName).
        self.setLoad.PublishTeamLogo(team, value)

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

            # Generate thumbnail and copy to clipboard (best-effort: a
            # thumbnail failure must not block the post itself, so this is
            # isolated in its own try/except and the failure is surfaced
            # via a modal after the post completes).
            thumbnail_error = None
            try:
                thumbnailPath = self.GenerateThumbnail(quiet_mode=True, disable_msgbox=True)
                if thumbnailPath:
                    image = QImage(thumbnailPath)
                    if not image.isNull():
                        QApplication.clipboard().setImage(image)
            except Exception as e:
                logger.exception("PostToX: thumbnail generation failed")
                thumbnail_error = str(e)

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

            # Notify the user after posting, so a thumbnail failure never
            # blocks or delays the post itself.
            if thumbnail_error:
                msgBox = QMessageBox()
                msgBox.setWindowIcon(QIcon(UI.icon_path('icon.png')))
                msgBox.setWindowTitle(QApplication.translate("app", "Post to X"))
                msgBox.setText(QApplication.translate(
                    "app",
                    "Thumbnail generation failed, so no image was copied to the clipboard."))
                msgBox.setInformativeText(thumbnail_error)
                msgBox.setIcon(QMessageBox.Warning)
                msgBox.exec()
        except Exception as e:
            logger.exception(f"PostToX error: {e}")
            msgBox = QMessageBox()
            msgBox.setWindowIcon(QIcon(UI.icon_path('icon.png')))
            msgBox.setWindowTitle(QApplication.translate("app", "Post to X"))
            msgBox.setText(QApplication.translate("app", "Failed to post to X."))
            msgBox.setInformativeText(str(e))
            msgBox.setIcon(QMessageBox.Warning)
            msgBox.exec()

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

    def _onHideSponsorToggled(self, hide: bool):
        # The one display option with a desktop control. It only asks for the
        # operation; the QAction, this checkbox on every other built board and
        # the inline sponsor field all follow from _SyncDisplayOptions when
        # the state change comes back (headless core Step 3).
        AutonaActions.instance.SetDisplayOption("show_sponsor", not hide)

    def UpdateBottomButtons(self):
        if AutonaTournamentDataProvider.instance.provider and AutonaTournamentDataProvider.instance.provider.url:
            self.btSelectSet.setText(
                QApplication.translate("app", "大会の試合を選ぶ"))
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

            p.btMoveUp.clicked.connect(
                lambda checked=False, p=p: self._MovePlayer(p, -1))
            p.btMoveDown.clicked.connect(
                lambda checked=False, p=p: self._MovePlayer(p, 1))

            p.instanceSignals.playerId_changed.connect(
                self.stats.signals.RecentSetsSignal.emit)
            p.instanceSignals.player1Id_changed.connect(
                self.stats.signals.LastSetsP1Signal.emit)
            p.instanceSignals.player1Id_changed.connect(
                self.stats.signals.PlayerHistoryStandingsP1Signal.emit)
            p.instanceSignals.player_seed_changed.connect(
                self.stats.signals.UpsetFactorCalculation.emit)
            p.instanceSignals.render_requested.connect(
                self._SchedulePlayerRender)

            self.team1playerWidgets.append(p)

            p = AutonaScoreboardPlayerWidget(
                index=len(self.team2playerWidgets)+1,
                teamNumber=2,
                path=f'score.{self.scoreboardNumber}.team.{2}.player.{len(self.team2playerWidgets)+1}')
            self.playerWidgets.append(p)

            self.team2column.findChild(
                QGroupBox, "groupBox").layout().addWidget(p)
            p.SetCharactersPerPlayer(self.charNumber.value())

            p.btMoveUp.clicked.connect(
                lambda checked=False, p=p: self._MovePlayer(p, -1))
            p.btMoveDown.clicked.connect(
                lambda checked=False, p=p: self._MovePlayer(p, 1))

            p.instanceSignals.playerId_changed.connect(
                self.stats.signals.RecentSetsSignal.emit)
            p.instanceSignals.player2Id_changed.connect(
                self.stats.signals.LastSetsP2Signal.emit)
            p.instanceSignals.player2Id_changed.connect(
                self.stats.signals.PlayerHistoryStandingsP2Signal.emit)
            p.instanceSignals.player_seed_changed.connect(
                self.stats.signals.UpsetFactorCalculation.emit)
            p.instanceSignals.render_requested.connect(
                self._SchedulePlayerRender)

            self.team2playerWidgets.append(p)

        while len(self.team1playerWidgets) > number:
            team1player = self.team1playerWidgets[-1]
            team1player.player.Unset()
            team1player.setParent(None)
            self.playerWidgets.remove(team1player)
            self.team1playerWidgets.remove(team1player)
            team1player.deleteLater()

            team2player = self.team2playerWidgets[-1]
            team2player.player.Unset()
            team2player.setParent(None)
            self.playerWidgets.remove(team2player)
            self.team2playerWidgets.remove(team2player)
            team2player.deleteLater()

        # ...and the slots the state carries without a widget: a roster
        # restored from a previous session, or one left behind by a board
        # that shrank while it was not built.
        for team in (1, 2):
            AutonaActions.instance.TrimTeamRoster(
                self.scoreboardNumber, team, number)

        multiPlayer = number > 1

        # The card header label is always visible (text set by
        # _applyScoreboardMode); only the team-name field follows the
        # player count.
        if multiPlayer:
            self.team1column.findChild(QLineEdit, schema.TEAM_NAME_KEY).setVisible(True)
            self.team2column.findChild(QLineEdit, schema.TEAM_NAME_KEY).setVisible(True)
        else:
            # The names are emptied here, not finished by the operator: a
            # text a render left in a field must not hold the clear back.
            self._heldBackTeamName.clear()
            self.team1column.findChild(QLineEdit, schema.TEAM_NAME_KEY).setVisible(False)
            self.team1column.findChild(QLineEdit, schema.TEAM_NAME_KEY).setText("")
            self.team1column.findChild(
                QLineEdit, schema.TEAM_NAME_KEY).editingFinished.emit()
            self.team2column.findChild(QLineEdit, schema.TEAM_NAME_KEY).setVisible(False)
            self.team2column.findChild(QLineEdit, schema.TEAM_NAME_KEY).setText("")
            self.team2column.findChild(
                QLineEdit, schema.TEAM_NAME_KEY).editingFinished.emit()

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
        """The SWAP button, and the Web UI's swap of a 1v1 / team board. The
        operation is core's (ADR-005 S2c): it exchanges the two sides in the
        state, and the cards, the score and the team cards follow from the
        held cycle like any other write. The hotkey calls the same operation
        (AutonaHotkeys._onSwapTeams), committing the field first as here."""
        self.CommitFocusedEdit()
        AutonaActions.instance.SwapTeams(self.scoreboardNumber)

    def CommitFocusedEdit(self):
        """Finish the edit in progress on this board before a swap / move
        / a players' clear (_ClearingPlayers).

        The copy that used to do the swap read the fields as they stood, so
        a tag still being typed travelled with its player. The swap now
        exchanges the state, and a field being typed in is one the render
        leaves alone -- the typed text would stay where it is and be
        committed, on focus-out, into the player who arrived there. So the
        field is left first: Qt then finishes it (editingFinished) exactly
        as when the operator clicks elsewhere, and the text is in its
        player's slot before the slots move. Clicking SWAP already did that
        when the button takes the focus; the hotkey and the Web UI do not.

        A field in a window of its own (a card's details dialog) counts, so
        the parent chain is walked rather than asking isAncestorOf (which
        stops at the window). A typed-in combo is not committed by losing
        the focus, here as anywhere (ADR-005 S2a).

        The field's completer popup, when it is open (the tag's candidates
        open on focus), is closed first: losing the focus with it open
        finishes nothing (no editingFinished) and leaves the popup on
        screen, to complete into whoever arrives in the field. Closing it
        picks no candidate, so the field keeps what was typed.
        """
        focus = QApplication.focusWidget()
        widget = focus
        while widget is not None and widget is not self:
            widget = widget.parentWidget()
        if focus is None or widget is not self:
            return
        popup = QApplication.activePopupWidget()
        # popup() builds one when the completer has none yet, so it is only
        # asked while some popup is open.
        completer = focus.completer() if popup is not None \
            and isinstance(focus, (QLineEdit, QComboBox)) else None
        if completer is not None and completer.popup() is popup:
            popup.hide()
        focus.clearFocus()

    def _MovePlayer(self, card, step):
        """A card's move up (-1) / move down (+1): exchange its slot with
        the neighbouring one in the state (AutonaActions.SwapPlayers). The
        ends of the list are not a move."""
        team = 1 if card in self.team1playerWidgets else 2
        widgets = self.team1playerWidgets if team == 1 \
            else self.team2playerWidgets
        index = widgets.index(card)
        other = max(0, min(len(widgets) - 1, index + step))
        if other == index:
            return
        self.CommitFocusedEdit()
        if AutonaActions.instance.SwapPlayers(
                self.scoreboardNumber, team, index + 1, other + 1):
            if self._SlotIdsDiffer(team, (index + 1, other + 1)):
                self._RequestPlayerStats((team,))

    # Statistics after a swap / move (ADR-005 S2c). A card fetches its
    # statistics when *it* changes the player id (ExportPlayerId). A swap or
    # a move exchanges the ids in the state without a card being involved,
    # so the board asks instead -- once per operation, where the widget copy
    # that used to do the swap fired every fetch several times (for the id
    # it dropped, for the id it restored, and once per card).

    def _SlotIdsDiffer(self, team, slots):
        """True when the given slots of `team` hold different player ids,
        i.e. when exchanging them changed who is in each."""
        ids = [StateManager.Get(
            f"{self.statePrefix}.team.{team}.player.{slot}.id")
            for slot in slots]
        return any(value != ids[0] for value in ids[1:])

    def _NoticeSwap(self):
        """From the held cycle: was it a swap (the flag flipped)? Then show
        the mirrored game results, and if it put other players on the
        teams, refetch both teams' stats. True for a swap.

        Read from the state rather than told by the swap, because the swap
        has three entry points (this board's button, the hotkey, the Web UI)
        and only the first one goes through the board.

        The game results need saying: the tracker's buttons are redrawn when
        the score moves (_SyncScoreDisplays), and a swap of a level score
        mirrors the rows without moving it.
        """
        swapped = bool(StateManager.Get(
            f"{self.statePrefix}.teamsSwapped", False))
        if swapped == self._seenTeamsSwapped:
            return False
        self._seenTeamsSwapped = swapped
        self.individualGameTracker.RenderStageResults()
        rosters = []
        for team in (1, 2):
            roster = StateManager.Get(
                f"{self.statePrefix}.team.{team}.player")
            rosters.append(roster if isinstance(roster, dict) else {})
        # A team swap moves slot N of one team to slot N of the other, so
        # both teams changed exactly when some slot pair holds two ids.
        for slot in set(rosters[0]) & set(rosters[1]):
            ids = [roster.get(slot).get("id")
                   if isinstance(roster.get(slot), dict) else None
                   for roster in rosters]
            if ids[0] != ids[1]:
                self._RequestPlayerStats((1, 2))
                break
        return True

    def _RequestPlayerStats(self, teams):
        """Queue the fetches a change of player in `teams` asks for: the
        head-to-head once, and each team's last sets / standings. Emitted
        from the event loop (_EmitPlayerStats), never from inside the state
        notification that noticed the change (agent-pitfalls #9)."""
        if not self._pendingStatsTeams:
            QTimer.singleShot(0, self, self._EmitPlayerStats)
        self._pendingStatsTeams.update(teams)

    def _EmitPlayerStats(self):
        teams, self._pendingStatsTeams = self._pendingStatsTeams, set()
        if not teams:
            return
        signals = self.stats.signals
        signals.RecentSetsSignal.emit()
        if 1 in teams:
            signals.LastSetsP1Signal.emit()
            signals.PlayerHistoryStandingsP1Signal.emit()
        if 2 in teams:
            signals.LastSetsP2Signal.emit()
            signals.PlayerHistoryStandingsP2Signal.emit()

    def ResetScore(self):
        # One operation, one save cycle: driving the two spinboxes in turn
        # (as this used to) made a reset two separate export cycles.
        AutonaActions.instance.ResetScore(self.scoreboardNumber)

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
        logger.info(
            f"Station sets: {len(data) if data is not None and hasattr(data, '__len__') else 0} items")
        self.setLoad.SetStationQueue(data)

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

            if data.get("id") != None and \
                    data.get("id") != self.setLoad.lastSetSelected:
                # Clear previous scores
                # Important because when we receive scores as 0 we don't update based on that
                # Otherwise an offline set which is only updated after it's complete would reset the score
                # all the time since it would be 0-0 until then
                # ClearScore, not ResetMatch: the round name / phase / pool
                # stay until the new set brings its own (S-17).
                # Both are core operations (ADR-005 S2d), written inside the
                # BlockSaving above, so they reach the board in the one save
                # cycle the set load ends with; the clear forgets the set
                # loaded last, which is why it is recorded only after it.
                self.CommandClearAll(no_mains=data.get(
                    "no_mains") if data.get("no_mains") != None else False)
                self.ClearScore()

                # A new set was loaded
                self.setLoad.lastSetSelected = data.get("id")

                # Which set this board is following, and how (id, auto
                # update type, station/stream identifier).
                self.setLoad.BeginSet(
                    data.get("id"),
                    data.get("auto_update"),
                    self.lastStationSelected.get('identifier')
                    if self.lastStationSelected else None)

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
            self.lastStationSelected = None
            # Forgets the set loaded last too.
            self.setLoad.ClearSetTracking()

        self.timerLayout.setVisible(False)

    def UpdateTimeLeftTimer(self):
        if self.autoUpdateTimer:
            self.timerTime.setText(
                str(int(self.autoUpdateTimer.remainingTime()/1000)))

    def LoadSetClicked(self):
        # Restores the cached set list; fetching is the operator's call from
        # inside the window.
        self.selectSetWindow.PrepareForDisplay()
        self.selectSetWindow.show()

    def LoadStationSets(self, station):
        self.setLoad.lastSetSelected = None
        self.lastStationSelected = station
        AutonaTournamentDataProvider.instance.LoadStationSets(self)

    def LoadStationSetClicked(self):
        self.selectStationWindow.LoadStations()
        self.selectStationWindow.show()

    # Used for score change commands
    # Change <team>(0, 1) score by <change>(+X, -X)
    def CommandScoreChange(self, team: int, change: int):
        # Kept as the widget-side name for the operation (the +1 / -1 buttons
        # and the CommandScoreChange signal use it), but the write is
        # AutonaActions': the spinbox follows through _SyncScoreDisplays.
        # `team` is 0-based here and 1-based in the state, hence the +1.
        AutonaActions.instance.AdjustScore(
            self.scoreboardNumber, team + 1, change)

    def CommandClearAll(self, no_mains=False):
        """Empty every player of the board (AutonaActions.ClearPlayers).

        The clear is core's since ADR-005 S2d -- one save cycle, where each
        card used to empty its own fields (a cycle per card) -- and the
        cards show the emptied slots from the held cycle. What stays here
        is the board's part of it, for every entry point (the set load's
        pre-pass, the Web's reset_players / clear_all; ResetAllInputs does
        the same around ClearBoard): see _ClearingPlayers.
        """
        self._ClearingPlayers(lambda: AutonaActions.instance.ClearPlayers(
            self.scoreboardNumber, no_mains))

    def _ClearingPlayers(self, clear):
        """Run `clear` (a core operation that empties the players) with what
        the board adds to it:

        - the field being typed in is finished first (CommitFocusedEdit),
          so that a card's typed text is cleared with the rest instead of
          being left on the card, to be committed into the emptied slot on
          focus-out (the render skips a focused field). That holds for the
          player fields only: a match bar / team name / score field being
          typed in is committed when it loses the focus, as it was before
          S2d -- after the clear, unless the clear itself empties it
          (ClearBoard does the team names and the match bar). Text typed
          into a character / country combo is not committed by losing the
          focus at all (CommitFocusedEdit) and may stay on the combo after
          the clear -- unverified, recorded in the S2d ledger;
        - the set loaded last is forgotten, so loading it again does load
          it (NewSetSelected skips the set it already has);
        - the statistics are fetched again for each team whose player ids
          the clear dropped -- once per operation. A card used to ask for
          them when its own Clear() dropped its id (ExportPlayerId), once
          per card; the upset factor is recalculated by the operation.
        """
        self.CommitFocusedEdit()
        before = {team: self._TeamPlayerIds(team) for team in (1, 2)}
        clear()
        self.setLoad.lastSetSelected = None
        changed = [team for team in (1, 2)
                   if self._TeamPlayerIds(team) != before[team]]
        if changed:
            self._RequestPlayerStats(changed)

    def _TeamPlayerIds(self, team):
        roster = StateManager.Get(f"{self.statePrefix}.team.{team}.player")
        if not isinstance(roster, dict):
            return {}
        return {slot: value.get("id") for slot, value in roster.items()
                if isinstance(value, dict)}

    def ClearScore(self):
        """Zero both scores and both losers flags -- and nothing else
        (AutonaActions.ClearScore).

        This is the clear NewSetSelected runs before a new set arrives, so it
        must leave the match bar alone: SetLoadService.ApplyMatchInfo keeps
        the round name / phase for a payload that carries none, and blanking
        them here would wipe the round mid-stream for exactly those sets. The
        operator's reset is ResetMatch(), which adds the match bar (S-17).

        The spinboxes, the losers boxes and the stage rows follow from the
        held cycle (ADR-005 S2d; they used to be driven to 0 / unchecked
        here, a save cycle per score and per stage row).
        """
        AutonaActions.instance.ClearScore(self.scoreboardNumber)

    def ResetMatch(self):
        """The operator's reset: ClearScore plus the match bar (S-17).
        AutonaActions.ResetMatch, one save cycle.

        Behind the すべての入力値をリセット button (ResetAllInputs, through
        AutonaActions.ClearBoard) and WebServerActions' reset_match /
        clear_all (legacy REST, Socket.IO and /api/v1 :reset-match /
        :clear), which call the operation directly. A set load does not: see
        ClearScore.
        """
        AutonaActions.instance.ResetMatch(self.scoreboardNumber)

    def CommandTeamColor(self, team: int, color, force_opponent=False):
        # Resolving a game's preset color *index* to a hex string stays here:
        # it reads AutonaGameAssetManager's color model, which is Qt. What is
        # written, and where, is AutonaActions' business (headless core
        # Step 3) -- this method only produces the value.
        if not color:
            return
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

        AutonaActions.instance.SetTeamColor(
            self.scoreboardNumber, team + 1, value)


    # Modifies the current set data. Does not check for id, so do not call this with data that may lead to another hbox incident
    def ChangeSetData(self, data):
        """Apply a set payload to this board.

        The 140 lines this used to be were a form being poked: a combo's
        text set so its editingFinished lambda would write the round name,
        the score spinboxes driven so their valueChanged would write the
        score, each of them re-deriving "which side is which after a swap"
        from its own reversed list. All of that is core/SetLoadService since
        headless core Step 4b, and the widgets follow the state through
        _SyncSetFields / _SyncScoreDisplays.

        The best of is AutonaActions.SetBestOf's since ADR-005 S2e (it was
        the BEST OF spinbox driven from here), the rows it resizes included.
        What is still done here is what still needs Qt: **player data**,
        which SetData resolves against the country, character and skin
        models (Step 5). Which team an entrant lands on is core's answer
        (SetLoadService.DisplayTeam), so the swap rule is stated once
        instead of four times.

        One save cycle for the whole payload, as before.
        """
        with StateManager.SaveBlock():
            service = self.setLoad
            service.ApplyMatchInfo(data)
            service.ApplyScores(data)

            bestOf = service.BestOfFromSetData(data)
            if bestOf is not None:
                AutonaActions.instance.SetBestOf(self.scoreboardNumber, bestOf)

            # Stream URL is user-managed via the dropdown; do not overwrite
            # it when loading a set from StartGG.
            # The flag and its team's " [L]" names, in core (ADR-005 S2b).
            service.ApplyLosers(data)

            if data.get("entrants"):
                self._ApplyEntrants(data)
            elif not self._ApplySinglePlayerData(data):
                # Neither entrants nor a team/player target: nothing to
                # apply, and the old code returned here -- before
                # bracket_type. Kept as it was; a payload carrying a bracket
                # type always carries entrants too.
                return

            if service.ApplyBracketType(data):
                self.stats.signals.UpsetFactorCalculation.emit()

    def _ApplyEntrants(self, data):
        """Pour a set's two entrant rosters into the player widgets."""
        entrants = data.get("entrants")
        self.playerNumber.setValue(len(max(entrants, key=lambda x: len(x))))

        # Lock all player widgets
        for p in self.playerWidgets:
            p.dataLock.acquire()

        try:
            # Kept exactly as it was, `!=` included: no_mains reaches here
            # from a provider payload, and `1 != True` is False in Python --
            # an `is True` here would quietly change how a non-bool truthy
            # value behaves in the two branches below.
            noMains = data.get("no_mains")
            if noMains is None:
                noMains = False
            for t, team in enumerate(entrants):
                stateTeam = self.setLoad.DisplayTeam(t + 1)
                teamInstance = (self.team1playerWidgets if stateTeam == 1
                                else self.team2playerWidgets)

                if len(team) > 1:
                    # The team name and the crest that follows from it are
                    # written together by the service; the QLineEdit picks
                    # the name up from _SyncSetFields.
                    self.setLoad.ApplyTeamName(
                        t + 1, data.get(f"p{t + 1}_name"))

                for p, player in enumerate(team):
                    if data.get("overwrite"):
                        # Overwrite path already applies mains via the full
                        # player dict; the old code followed this with a
                        # redundant mains-only SetData that doubled the
                        # per-player cost on set load. Using elif so the
                        # mains-only path only runs in the non-overwrite case.
                        teamInstance[p].SetData(player, False, True, noMains)
                    elif data.get("has_selection_data") and noMains != True:
                        teamInstance[p].SetData(
                            {"mains": player.get("mains")}, True, False)
        except Exception as e:
            logger.error(f"Error while setting entrants: {e}")
        finally:
            for p in self.playerWidgets:
                p.dataLock.release()

    def ApplyPlayerData(self, team, player, data):
        """Apply one player's fields from a remote client (/api/v1 PATCH).

        Just the player half of ChangeSetData. Run through ChangeSetData (as
        the legacy update-team route does), a player edit also went through
        the set-level writers, and `SetLoadService.ApplyMatchInfo` writes
        `match_en` unconditionally -- so every remote player edit blanked
        the English round text. Same SaveBlock, same player write, nothing
        else.

        Unlike the legacy path, a failure is reported instead of logged and
        swallowed: False when the team has no such slot (`player` is 1-based
        and must be within the team's current roster -- checked here, on the
        GUI thread, against the widgets that actually exist), and an
        exception from the write itself propagates. True when applied.
        GUI thread only (the player widgets are written).
        """
        team = int(team)
        player = int(player)
        if team not in (1, 2):
            return False
        widgets = (self.team1playerWidgets if team == 1
                   else self.team2playerWidgets)
        if not 1 <= player <= len(widgets):
            return False
        with StateManager.SaveBlock():
            self._WritePlayerSlot(team, player, data)
        return True

    def _ApplySinglePlayerData(self, data):
        """Apply a one-player payload (set_team_data). False if it had none.

        Unlike the entrants path this addresses the state teams directly:
        the caller names the team it means, and a swap has already been
        applied to what it is looking at. A failed write -- an out-of-range
        slot included -- is logged and ignored, which is what the legacy
        update-team route has always answered "OK" to.
        """
        if not data.get("team") or not data.get("player"):
            return False
        try:
            self._WritePlayerSlot(
                data.get("team"), data.get("player"), data.get("data"))
        except Exception as e:
            logger.error(f"Error while setting entrants: {e}")
        return True

    def _WritePlayerSlot(self, team, player, data):
        """Write one player slot (1-based team/player). Raises on failure."""
        for p in self.playerWidgets:
            p.dataLock.acquire()

        try:
            team = int(team) - 1
            player = int(player) - 1

            teamInstances = [self.team1playerWidgets,
                             self.team2playerWidgets]
            teamInstance = teamInstances[team]

            teamInstance[player].SetData(data, False, False)
            if data.get("savePlayerToDb", False):
                teamInstance[player].SavePlayerToDB()
        finally:
            for p in self.playerWidgets:
                p.dataLock.release()

    def UpdateSetData(self, data):
        # An empty payload, or one answering for a set the operator has
        # already navigated away from (an in-flight fetch resolving late).
        if SetLoadService.IsStaleUpdate(data, self.setLoad.lastSetSelected):
            return

        if SettingsManager.Get("general.disable_overwrite", False):
            for entrant in data.get("entrants"):
                dbPlayer = PlayerDB.instance.Get(entrant[0].get("gamerTag"))
                if dbPlayer is not None:
                    entrant[0] = entrant[0] | dbPlayer

        self.ChangeSetData(data)

    def LoadPlayerFromTag(self, tag, team, player, no_mains=False):
        team = int(team)-1
        player = int(player)-1
        teamInstances = [self.team1playerWidgets,
                         self.team2playerWidgets]

        if StateManager.Get(f"{self.statePrefix}.teamsSwapped", False):
            teamInstances.reverse()

        playerData = PlayerDB.instance.GetPlayerFromTag(tag)
        if playerData:
            teamInstances[team][player].SetData(
                    playerData, False, True, no_mains)
            return True
        return False

    def SetDefaultsFromAssets(self):
        # Propagate the game's default players-per-team; characters-per-player
        # is forced back to its default (3) regardless of game asset config.
        # Both come from core, where the headless shell's launch seed reads
        # them too (PlayerService.SeedEmptyRoster, ADR-005 S3d).
        self.playerNumber.setValue(DefaultPlayersPerTeam())
        self.charNumber.setValue(DEFAULT_CHARACTERS_PER_PLAYER)

    pass
