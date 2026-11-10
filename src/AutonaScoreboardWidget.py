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
from .Helpers.AutonaQtHelper import HeldBackText, ShowComboText

from . import ui_constants as UI
from . import ui_layout as L
from .AutonaSelectSetWindow import AutonaSelectSetWindow
from .AutonaSelectStationWindow import AutonaSelectStationWindow
from .AutonaIndividualGameTracker import AutonaIndividualGameTracker

from .AutonaScoreboardPlayerWidget import (
    AutonaScoreboardPlayerWidget, _releaseLayoutItemWrappers)
from .core.SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT
from .core.StateManager import StateManager
from .core import StateSchema as schema
from .core import WebAccess
from .core.Actions import AutonaActions
from .core.CrewBattleService import CrewBattleService
from .core.MatchNameCandidates import MatchNameCandidates
from .core.PlayerService import DEFAULT_CHARACTERS_PER_PLAYER
from .core.SetFollowService import SetFollowService
from .core.SetLoadService import SetLoadService
from .core.StatsService import StatsService
from .core.TournamentInfoService import TournamentInfoService
from .StateManagerBridge import StateManagerBridge
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaHotkeys import AutonaHotkeys

from loguru import logger

from .AutonaGameAssetManager import AutonaGameAssetManager
# SCORE's thumbnail button and X post render synchronously through the thin
# wrapper; the settings page is the React one (ADR-005 S4p decision 10).
from .thumbnail import main_generate_thumbnail as thumbnail


class AutonaScoreboardWidgetSignals(QObject):
    NewSetSelected = Signal(object)
    SetSelection = Signal()
    StationSelection = Signal()
    StationSelected = Signal(object)


class AutonaScoreboardWidget(QWidget):

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

        # The player cards render their slots from the state (ADR-005 S2a):
        # one pass, queued by the first held save cycle of an event-loop turn
        # (_SchedulePlayerRender), with a re-entrancy guard on the pass itself.
        self._playerRenderPending = False
        self._renderingPlayers = False
        # The cards follow the roster in the state (ADR-005 S3e-6:
        # _SyncPlayerCards builds the missing ones in the held cycle and
        # retires the surplus, destroyed once control is back in the event
        # loop: _DestroyRetiredCards). The players per team the chrome last
        # followed (None before the first sync).
        self._syncingCards = False
        self._retiredCards = []
        self._shownPlayerCount = None

        # The teams whose name field _SyncSetFields had to leave alone
        # because the operator was in it: rendered from the state once the
        # focus has left it (_OnTeamNameFocusChanged). And, by team, what
        # each field last showed in step with the state and the text a
        # render held back in it: finishing the edit with that text is not
        # an edit (_OnTeamNameFinished) -- the player cards' rule
        # (HeldBackText; ADR-005 S3e-2 split the two, which were one dict).
        self._teamNameSkipped = set()
        self._teamNameHeldBack = HeldBackText()
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

        # The board's launch write is core's and is already in the state
        # (AutonaActions.SeedFreshBoard, run by AutonaScoreboardManager before
        # this, ADR-005 S4t): nothing below writes while the board is built,
        # and the widgets' initial values are the seed's -- empty.

        self.signals = AutonaScoreboardWidgetSignals()
        self.signals.NewSetSelected.connect(self.NewSetSelected)
        self.signals.SetSelection.connect(self.LoadSetClicked)
        self.signals.StationSelected.connect(self.LoadStationSets)
        self.signals.StationSelection.connect(self.LoadStationSetClicked)

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

        # (The statistics are core's since ADR-005 S3e-5: StatsService,
        # asked for by the operations that change a player -- this board
        # used to own a stats util relaying its cards' signals.)

        # (The set loaded last is self.setLoad.lastSetSelected since ADR-005
        # S2d: the clears that forget it are core's.)

        # Loading a set, following a station / stream and the 5-second poll
        # are core's since ADR-005 S3e-7 (SetFollowService, whose clock is
        # the shell's AutonaSetFollowUtil): this board asks for them (the set
        # and station windows, the timer row's cancel button) and shows what
        # it follows (the timer row: _OnFollowChanged). The station followed
        # and the two QTimers were this board's until then. `_followClock`
        # redraws the countdown while the row is shown (None until then).
        self.setFollow = SetFollowService(self.scoreboardNumber)
        self._followClock: QTimer = None

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
        # spelled out here and again in the web layer. Only the labels are
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

        # The two counts are the state's (ADR-005 S3e-6): the player slots
        # and their character slots, set by AutonaActions.SetRoster. These
        # spinboxes ask for a change and show the count (_SyncRosterSpins,
        # signals blocked); they were the counts until then, and every
        # other entry point drove them. Characters-per-player is not
        # user-facing (the PLAYERS tab that drove it went). The count a
        # board with no player slot left gives its next players is core's
        # (AutonaActions.EmptyRosterCharacters, ADR-005 S4x), shared with
        # the Web and headless; this hidden spinbox kept it until then and
        # shows it now. Default: core's DEFAULT_CHARACTERS_PER_PLAYER (the
        # headless seed's too).
        self.charNumber = QSpinBox(self)
        self.charNumber.setMaximum(AutonaActions.MAX_CHARACTERS_PER_PLAYER)
        self.charNumber.setValue(DEFAULT_CHARACTERS_PER_PLAYER)
        self.charNumber.valueChanged.connect(self.SetCharacterNumber)
        self.charNumber.setVisible(False)

        self.playerNumber = QSpinBox()
        self.playerNumber.setMaximum(AutonaActions.MAX_PLAYERS_PER_TEAM)
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
        # What the board follows is core's: the row follows its
        # announcements. A bound method, so the connection dies with the
        # board (the fetcher lives as long as the app).
        if SetFollowService.fetcher is not None:
            SetFollowService.fetcher.signals.followChanged.connect(
                self._OnFollowChanged)

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
        # removed). It is configured via Edit → Settings → General and written
        # with the board's launch state (AutonaActions.SeedFreshBoard).
        # colorGroup1 stays as an (empty) layout spacer that keeps the 1P/TEAM
        # label centered (see _applyScoreboardMode).

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

        team2HeaderRow = self.team2column.findChild(QHBoxLayout, "horizontalLayout_2")
        team2HeaderRow.layout().insertWidget(0, colorGroup2)
        team2HeaderRow.layout().addSpacing(UI.SPACE_MD)

        self._WireTeamColumn(self.team2column, 2)
        # A bound method: Qt drops the connection with the board.
        QApplication.instance().focusChanged.connect(
            self._OnTeamNameFocusChanged)

        # The cards of the seeded roster (AutonaActions.SeedFreshBoard): the
        # game's players per team and the default characters. Now, since
        # nothing is held yet, so no held cycle would build them.
        self._FollowRoster()

        self._applyScoreboardMode(self.singlePlayer)

        # Match bar (フェーズ / プール名 / ラウンド). Both signals are wired
        # because either can change the text: currentIndexChanged for a pick
        # from the list, editingFinished for something typed into the
        # editable line edit. Which key each combo writes comes from
        # StateSchema now rather than from its own objectName -- see
        # _WireTeamColumn for the whole story. Their empty start is in the
        # seed.
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
            combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)

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
        # two team columns rely on. Same fix as clear_bt in
        # AutonaScoreboardPlayerWidget.
        self.btResetAll.setMinimumWidth(1)
        self.btApply.setMinimumWidth(1)

        columnsGrid.addWidget(self.btResetAll, 2, 0)
        columnsGrid.addWidget(self.btApply, 2, 1)

        self.innerWidget.layout().removeWidget(self.scoreColumn)
        self.scoreColumn.findChild(QGroupBox, "scoreGroupBox").setTitle("")

        # The spinbox asks for the change and renders the state
        # (_SyncBestOf); the range is the operation's, so the two cannot
        # drift apart. The construction's 0 is in the seed.
        self.bestOfSpin.setRange(
            AutonaActions.BEST_OF_MIN, AutonaActions.BEST_OF_MAX)
        self.bestOfSpin.valueChanged.connect(self._onBestOfChanged)
        self.bestOfSpin.editingFinished.connect(self._OnBestOfEditingFinished)
        self._SyncBestOf()

        # A number typed into a score display: the operation v1's score:set
        # calls (AutonaActions.SetScore, ADR-005 S4x) -- one save cycle with
        # the game rows following, nothing written for the score the state
        # has. The left display is state team 1 (a swap exchanges the state).
        # It went through the tracker's own UpdateScore until then.
        self.scoreDisplay1.valueChanged.connect(
            lambda value: AutonaActions.instance.SetScore(
                self.scoreboardNumber, 1, value))
        self.scoreDisplay2.valueChanged.connect(
            lambda value: AutonaActions.instance.SetScore(
                self.scoreboardNumber, 2, value))

        # (The team-name -> crest connection used to be made here, after the
        # generic objectName loops above had already seeded the names. It is
        # part of _WireTeamColumn now, so both halves of "the operator named
        # a team" are wired in one place -- see that method.)

        # What the phase / pool / round combos offer is core's
        # (MatchNameCandidates, ADR-005 S4x; GET /api/v1/match-names/
        # candidates?board=N answers the same lists): the localized terms,
        # with the loaded tournament's pools and entrant rounds ("Winners
        # TOP300") ahead of them. Core keeps the tournament's part from the
        # provider's two payloads, connected before this board's (bootstrap_
        # core()), so on the same signals the combos read what it kept. A
        # board built after the tournament loaded offers its names too.
        self._RenderMatchCandidates()
        signals = AutonaTournamentDataProvider.instance.signals
        signals.tournament_phases_updated.connect(
            self._OnTournamentCandidates)
        signals.tournament_data_updated.connect(self._OnTournamentCandidates)

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
        # broadcast/export pipeline until 反映 commits them. The launch write
        # ahead of this was published normally, so the committed baseline
        # matches the widgets' initial values.
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
        # A writer on another thread does not wait for a board being built:
        # what it wrote after the seed and ahead of the hold made no held
        # notification, and the widgets rendered what they found as they were
        # built. Shown once, as the notification would have. Writes nothing.
        self._onHeldStateUpdated(self.statePrefix)

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

        A board starts with an empty name and an unchecked losers box, and
        the overlays read both from launch: the seed writes them
        (AutonaActions.SeedFreshBoard). Their first emit did until ADR-005
        S4t.
        """
        name = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
        if name is not None:
            name.editingFinished.connect(
                lambda team=team, edit=name:
                    self._OnTeamNameFinished(team, edit))

        losers = column.findChild(QCheckBox, schema.TEAM_LOSERS_KEY)
        if losers is not None:
            losers.toggled.connect(
                lambda state, team=team: AutonaActions.instance.SetTeamLosers(
                    self.scoreboardNumber, team, state))

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

        Unless the text is held back -- a render had to leave it in the
        field while the operator was in it (HeldBackText) -- and the state
        has moved on since: the Web / API wrote a name underneath the edit,
        and finishing it -- the focus-out, Enter -- would put the old text
        back over it. Any other text is the operator's edit and is written,
        renders or not (a point scored from the Web while they typed used to
        hold the typing back, and lost it; ADR-005 S3e-2). The name and the
        crest are each written only when they change (SetTeamName /
        PublishTeamLogo), so re-finishing the name the state holds writes
        nothing.
        """
        text = edit.text()
        if self._teamNameHeldBack.IsHeldBack(team, text) and StateManager.Get(
                f"{self.statePrefix}.team.{team}.{schema.TEAM_NAME_KEY}",
                None) != text:
            return
        self._teamNameSkipped.discard(team)
        self._teamNameHeldBack.Shown(team, text)
        AutonaActions.instance.SetTeamName(self.scoreboardNumber, team, text)
        self.ExportTeamLogo(team, text)

    def _OnTeamNameFocusChanged(self, old, new):
        """QApplication.focusChanged: once the operator has left a team-name
        field a render had to skip, show the state in it. Deferred, so that
        nothing is redrawn inside the focus change (agent-pitfalls #9) and
        the field's own editingFinished has run first."""
        if not self._teamNameSkipped:
            return
        for team in self._teamNameSkipped:
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
        # AutonaGameAssetManager.signals.onLoad handler. Renders the load;
        # what it writes is core's, on the shared wiring connected ahead of
        # this (bootstrap.wire_game_roster: AutonaActions.ApplyGameRoster and
        # RepublishBestOfs) and last of all on the adapter's
        # (AutonaActions.CommitGameLoad). The commit takes the held cycle
        # that would have shown all this along with it, and whatever else
        # was written while the load's save cycle was open (a score, a set's
        # fields, from the Web), so it is all shown here. The order is the
        # held cycle's (_onHeldStateUpdated).
        # First, while nothing below can have raised: the asset manager
        # replaced its models, so no stage row may stay on the retired one.
        self.individualGameTracker.RebindStageModel()
        self._FollowRoster()
        self._SchedulePlayerRender()
        self._SyncSetFields()
        # The rows started over (they recorded games of the previous game's
        # stages), their number unchanged: built again for the new game.
        self._SyncBestOf()
        self._SyncScoreDisplays()

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
        # A cheap containment filter on the delta's text. One slot for both
        # subtrees so the (not free) str(delta) is paid once.
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
            # The cards the roster has (ADR-005 S3e-6), before anything
            # below looks for one. Built now, destroyed later.
            self._FollowRoster()
            self._SyncSetFields()
            # Before the score: the buttons it redraws are the rows a cycle
            # that resized them (a set load's best of with its score) left.
            self._SyncBestOf()
            self._SyncScoreDisplays()

    def _SchedulePlayerRender(self):
        """Have every player card show its slot, once control is back in
        the event loop (ADR-005 S2a).

        Deferred, unlike the two syncs in _onHeldStateUpdated, and that is
        the point rather than an optimisation. A card's own edits are
        written by its handlers, an edit at a time, and an edit can take
        several save cycles -- a country pick publishes the rebuilt region
        before the country. (A load and a clear are one core write since
        ADR-005 S3e-3.) In
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
        left alone is shown once the focus has left it (_teamNameSkipped),
        and its text is held back by the player cards' rule
        (_teamNameHeldBack, HeldBackText; ADR-005 S2d review, S3e-2):
        finishing the edit with it does not write it back over the newer
        name.
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
            # connected to the same write. The value's row is selected, or
            # none (ShowComboText): a pick of the row the index was left on
            # wrote nothing (ADR-005 S4x review).
            with QSignalBlocker(combo):
                if edit is not None:
                    edit.blockSignals(True)
                try:
                    ShowComboText(combo, value)
                finally:
                    if edit is not None:
                        edit.blockSignals(False)

        for team, column in ((1, self.team1column), (2, self.team2column)):
            # findChild rather than a cached reference: the losers checkbox
            # is re-homed onto a row of its own on the singles board
            # (_placeLosersOnOwnRow).
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
                self._teamNameSkipped.discard(team)
                self._teamNameHeldBack.Shown(team, name)
            elif edit.hasFocus():
                # Left alone, to be shown once the focus has left it; what
                # it holds is held back (finishing it must not write it over
                # the newer name, _OnTeamNameFinished) only as HeldBackText
                # says -- typing that no write of the name met is the
                # operator's.
                self._teamNameSkipped.add(team)
                self._teamNameHeldBack.LeftAlone(team, edit.text(), name)
            else:
                self._teamNameSkipped.discard(team)
                with QSignalBlocker(edit):
                    edit.setText(name)
                self._teamNameHeldBack.Shown(team, name)

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

    def _SyncScoreDisplays(self):
        """Render the score: the two spinboxes and the game rows' buttons.
        Writes nothing.

        The state is the source of truth for the score since Step 3: an
        adjustment made through AutonaActions -- from the +1 button, a
        hotkey, the Web UI or the API -- lands in `score.{n}.team.{t}.score`
        and arrives here as a notification. The spinbox is updated inside a
        QSignalBlocker, so valueChanged (which asks AutonaActions.SetScore
        for a number typed into it) cannot fire and write the value straight
        back.

        The rows have followed the score by now, in the operation that moved
        it (GameTrackerService.FollowScores, ADR-005 S4t), or moved with it
        (a swap mirrors them, and a swap of a level score moves no number):
        this used to be where they followed, from the old value on the
        spinbox, with the tracker's own writes and the swap told apart.
        """
        for team, display in ((1, self.scoreDisplay1),
                              (2, self.scoreDisplay2)):
            value = StateManager.Get(
                f"{self.statePrefix}.team.{team}.score", None)
            if not isinstance(value, int) or isinstance(value, bool):
                # Nothing renders a non-numeric score, so the spinbox is left
                # alone -- but a writer that put one there is a bug worth a
                # breadcrumb rather than a silent skip.
                if value is not None:
                    logger.warning(
                        f"{self.statePrefix}.team.{team}.score is "
                        f"{value!r}, not an int; leaving the display "
                        f"at {display.value()}")
                continue
            if display.value() != value:
                with QSignalBlocker(display):
                    display.setValue(value)
        self.individualGameTracker.RenderStageResults()

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
        # _FollowCards re-asserts it whenever player cards are built or
        # retired.
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
        # (AutonaWebOperations.clear_all) is its counterpart but differs on
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
            if not dirty:
                # A commit drops the held cycle this subtree still had owed
                # (StateManager.CommitHeld: 反映 is not an edit), and with
                # it any write of another thread's save block that was
                # waiting in it -- a roster change from the Web, say. The
                # cards are brought to the state here as well, or they
                # would stay a slot behind it until the next edit.
                self._SchedulePlayerRender()
                self._FollowRoster()

    def _OnTournamentCandidates(self, _payload):
        # tournament_data_updated / tournament_phases_updated: core has kept
        # the payload by now (connected first, bootstrap_core()).
        self._RenderMatchCandidates()

    def _RenderMatchCandidates(self):
        """Give the phase / pool / round combos the lists core offers for
        this board (MatchNameCandidates.Candidates), each after a blank row.
        Writes nothing: both the combo's and its line edit's signals are
        blocked, and the text the combo shows -- the operator's own, or a
        name the tournament no longer offers -- stays, with the caret, and
        with its row selected again when the new list has it, no row when
        it has not (ShowComboText, ADR-005 S4x review: the index follows the
        text after every render, so that a pick is compared against the row
        shown). A combo whose list has not changed is left alone.

        Until ADR-005 S4x the board kept the tournament's names itself
        (_UpdatePoolCandidates / _UpdateEntrantsMatchItem), inserting and
        removing them around the terms, and kept a name of the previous
        tournament in the list while it was the one selected; now the list is
        core's, and that name stays as the combo's text only."""
        candidates = MatchNameCandidates.instance.Candidates(
            self.scoreboardNumber)
        for combo, kind in ((self.phaseCombo, "phase"),
                            (self.poolCombo, "pool"),
                            (self.matchCombo, "match")):
            items = [""] + candidates[kind]
            if [combo.itemText(i) for i in range(combo.count())] == items:
                continue
            edit = combo.lineEdit()
            text = combo.currentText()
            cursor = edit.cursorPosition() if edit is not None else None
            with QSignalBlocker(combo):
                if edit is not None:
                    edit.blockSignals(True)
                try:
                    combo.clear()
                    combo.addItems(items)
                    ShowComboText(combo, text)
                    if edit is not None:
                        edit.setCursorPosition(cursor)
                finally:
                    if edit is not None:
                        edit.blockSignals(False)

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
        them here bought nothing and cost two more save cycles per click
        (six per click before Step 4a, four after, two now; pinned by
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
        # Closing the board stops its poll (core's since ADR-005 S3e-7) and
        # the countdown, as stopping the board's own two timers did. The
        # clock stays None until the row is first shown (BUG-5: closing a
        # board that never had a set loaded used to raise AttributeError
        # here; Qt swallows exceptions from event handlers so it was silent,
        # but it is still an unhandled exception on the path).
        self.setFollow.StopFollowing()
        if self._followClock is not None:
            self._followClock.stop()

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
            # Built with more than one player a team (a game's players per
            # team), the singles board shows the team names as well: what the
            # cards' chrome says (_FollowCards), which ran before this.
            teamName = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
            if teamName is not None:
                teamName.setVisible(
                    not singlePlayer or len(self.team1playerWidgets) > 1)

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
            self._placeLosersOnOwnRow()

    def _placeLosersOnOwnRow(self):
        """Singles board only: the card header the LOSERS checkbox lives in
        is otherwise empty (1P/2P label and team name are hidden), so it
        gets a row of its own, right-aligned, directly below the player
        cards' group box.

        A row of the team column, not of a card. It used to join the solo
        card's ">>詳細" row, which was safe only while that card could never
        go -- and it can (ADR-005 S3e-6: the Web takes the singles board to
        no players at all, and the card is destroyed with its slot), and the
        box went with it, for good. Moved once, while the board is built:
        never inside a held cycle (agent-pitfalls #14), and the header's
        layout items are released from PySide first, since the move frees
        the one that held the box. It stays a descendant of its team column,
        so every findChild(QCheckBox, "losers") lookup (the render, reset,
        the handler wired in _WireTeamColumn) keeps resolving the same
        instance.
        """
        for column in (self.team1column, self.team2column):
            losersCheck = column.findChild(QCheckBox, schema.TEAM_LOSERS_KEY)
            groupBox = column.findChild(QGroupBox, "groupBox")
            if losersCheck is None or groupBox is None:
                continue
            if column.findChild(QHBoxLayout, "losersRow") is not None:
                continue
            columnLayout = column.layout()
            row = QHBoxLayout()
            row.setObjectName("losersRow")
            row.setContentsMargins(0, 0, 0, 0)
            columnLayout.insertLayout(columnLayout.indexOf(groupBox) + 1, row)
            row.addStretch()
            header = column.findChild(QHBoxLayout, "horizontalLayout_2")
            if header is not None:
                _releaseLayoutItemWrappers(header)
            row.addWidget(losersCheck)
            # Flush with the inputs of the card above, as in the header.
            row.addSpacing(UI.SPACE_MD)

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
        # The post's text and what it misses are core's (AutonaActions.PostText,
        # ADR-005 S4x: GET /api/v1/scoreboards/{n}/post answers the same);
        # what stays here is the desktop's part: the dialog naming what is
        # empty, the thumbnail copied to the clipboard and the browser.
        try:
            n = self.scoreboardNumber

            _text, missing = AutonaActions.instance.PostText(n)
            if missing:
                # Not `labels`: tests/test_state_schema.py finds the eye
                # menu's labels by that name.
                missingLabels = {
                    CrewBattleService.POST_MISSING_TEAM_1:
                        QApplication.translate("app", "Team 1 player name"),
                    CrewBattleService.POST_MISSING_TEAM_2:
                        QApplication.translate("app", "Team 2 player name"),
                    CrewBattleService.POST_MISSING_ROUND:
                        QApplication.translate("app", "Round name"),
                }
                msgBox = QMessageBox()
                msgBox.setWindowIcon(QIcon(UI.icon_path('icon.png')))
                msgBox.setWindowTitle(QApplication.translate("app", "Post to X"))
                msgBox.setText(QApplication.translate(
                    "app",
                    "Cannot post to X: the following fields are empty."))
                msgBox.setInformativeText(
                    "\n".join(f"- {missingLabels[m]}" for m in missing))
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

            # Read again after the thumbnail, as the text always was: the
            # render can take a while, and the post is of the values then.
            text, _missing = AutonaActions.instance.PostText(n)
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
        """charNumber.valueChanged: the characters per player
        (AutonaActions.SetCharactersPerPlayer -- on a board with no
        character slot, the count its next players get). The cards follow
        from the held cycle."""
        AutonaActions.instance.SetCharactersPerPlayer(
            self.scoreboardNumber, value)

    def SetPlayersPerTeam(self, number):
        """playerNumber.valueChanged, the operator's PLAYERS per team: the
        roster in one save cycle (AutonaActions.SetPlayersPerTeam, the
        operation of the Web's players too). New players on a board with no
        character slot left get the count core keeps for it -- the one the
        board had when its last player went, or one asked for since -- as
        charNumber's value was until ADR-005 S4x. The cards follow from the
        held cycle (_SyncPlayerCards)."""
        AutonaActions.instance.SetPlayersPerTeam(self.scoreboardNumber, number)

    # ── the player cards follow the roster (ADR-005 S3e-6) ────────────────
    #
    # The roster is the state's: `score.{n}.team.{t}.player` has the slots,
    # and each slot its `character` slots. This board used to own both
    # counts -- its two spinboxes built and tore down the cards, and the
    # cards published and unset the slots -- so every other entry point (a
    # game load, a set's entrants, the Web) drove a spinbox, and a shell
    # without the board could not change a count at all. Now every entry
    # point calls AutonaActions.SetRoster and the board follows:
    #
    # - in the held cycle (_onHeldStateUpdated), synchronously: a card per
    #   slot is built, with as many character rows as the slot has
    #   character slots, and writes nothing as it is built (its slot is
    #   already seeded); a card whose slot is gone is retired -- hidden,
    #   silenced, out of the lists;
    # - once control is back in the event loop, the retired cards are
    #   destroyed (_DestroyRetiredCards), their layout items released from
    #   PySide first (agent-pitfalls #14). Nothing is torn down inside the
    #   notification that noticed the slots go.
    #
    # The few places whose held cycle is taken by a CommitHeld in the same
    # block (the build, a game load) call _SyncPlayerCards themselves.

    def _RosterCount(self, team) -> int:
        """The player slots team `team` has, counted as SetRoster counts
        them (AutonaActions.RosterCount): a card stands for slot 1..n."""
        return AutonaActions.RosterCount(self.scoreboardNumber, team)

    def _FollowRoster(self):
        """_SyncPlayerCards, from a caller that goes on whatever happens
        here (the rest of the load's render or of the held cycle): a sync
        that raises is logged, and the next one tries again."""
        try:
            self._SyncPlayerCards()
        except Exception:
            logger.exception(
                f"{self.statePrefix}: following the roster failed")

    def _SyncPlayerCards(self):
        """Build and retire player cards until each team has one per slot of
        the state's roster, and show the counts. Writes nothing."""
        if self._syncingCards:
            # Nothing below writes, so nothing brings us back here; a sync
            # must never stack on another all the same.
            logger.warning(
                f"{self.statePrefix}: re-entrant player card sync, skipping")
            return
        self._syncingCards = True
        try:
            counts = {team: self._RosterCount(team) for team in (1, 2)}
            teams = ((1, self.team1playerWidgets),
                     (2, self.team2playerWidgets))
            changed = False
            # Last first, team 1's then team 2's: the order the cards used
            # to be torn down in.
            for index in range(max(len(cards) for _t, cards in teams), 0, -1):
                for team, cards in teams:
                    if len(cards) >= index > counts[team]:
                        self._RetireCard(cards[index - 1], cards)
                        changed = True
            # One index at a time, team 1's then team 2's: the order they
            # are built in, and the order of playerWidgets.
            for index in range(1, max(counts.values()) + 1):
                for team, cards in teams:
                    if len(cards) < index <= counts[team]:
                        self._AddCard(team, cards)
                        changed = True
            for card in self.playerWidgets:
                card.SyncCharacterRows()
            if changed or self._shownPlayerCount is None:
                self._FollowCards()
            self._SyncRosterSpins()
        finally:
            self._syncingCards = False

    def _AddCard(self, team, cards):
        index = len(cards) + 1
        card = AutonaScoreboardPlayerWidget(
            index=index, teamNumber=team,
            path=f"{self.statePrefix}.team.{team}.player.{index}")
        column = self.team1column if team == 1 else self.team2column
        column.findChild(QGroupBox, "groupBox").layout().addWidget(card)

        card.btMoveUp.clicked.connect(
            lambda checked=False, card=card: self._MovePlayer(card, -1))
        card.btMoveDown.clicked.connect(
            lambda checked=False, card=card: self._MovePlayer(card, 1))
        card.instanceSignals.render_requested.connect(
            self._SchedulePlayerRender)

        cards.append(card)
        self.playerWidgets.append(card)

    def _RetireCard(self, card, cards):
        cards.remove(card)
        self.playerWidgets.remove(card)
        card.Retire()
        if not self._retiredCards:
            QTimer.singleShot(0, self, self._DestroyRetiredCards)
        self._retiredCards.append(card)

    def _DestroyRetiredCards(self):
        """The cards _SyncPlayerCards retired, destroyed from the event loop
        (AutonaScoreboardPlayerWidget.Destroy releases their layout items
        first). The board is the timer's context: a board torn down first
        takes the retired cards with it."""
        cards, self._retiredCards = self._retiredCards, []
        for card in cards:
            try:
                card.Destroy()
            except RuntimeError:
                # Already gone with something else.
                logger.debug(f"{self.statePrefix}: retired card already gone")
            except Exception:
                # The rest are destroyed all the same.
                logger.exception(
                    f"{self.statePrefix}: destroying a retired card failed")

    def _FollowCards(self):
        """The chrome that follows the number of players: the team names
        (shown for a team, emptied and hidden for one player -- the state's
        names were emptied with the roster, SetRoster), the cards' titles
        and move buttons, the sponsor field's display option and the
        birthday, on every card."""
        number = len(self.team1playerWidgets)
        multiPlayer = number > 1
        if number != self._shownPlayerCount:
            self._shownPlayerCount = number
            for team, column in ((1, self.team1column), (2, self.team2column)):
                edit = column.findChild(QLineEdit, schema.TEAM_NAME_KEY)
                if edit is None:
                    continue
                if multiPlayer:
                    edit.setVisible(True)
                    continue
                # Blocked: hiding the field the operator is in finishes it,
                # and nothing is written from the held cycle. What a render
                # left in it is not kept for later either.
                self._teamNameSkipped.discard(team)
                self._teamNameHeldBack.Shown(team, "")
                with QSignalBlocker(edit):
                    edit.setVisible(False)
                    edit.setText("")

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

    def _SyncRosterSpins(self):
        """Show the counts on the two spinboxes, signals blocked. A board
        with no player slot shows the characters per player core keeps for
        its next players (AutonaActions.EmptyRosterCharacters; this hidden
        spinbox was that memory until ADR-005 S4x)."""
        players = len(self.team1playerWidgets)
        if self.playerNumber.value() != players:
            with QSignalBlocker(self.playerNumber):
                self.playerNumber.setValue(players)
        characters = None
        for card in self.playerWidgets:
            picks = StateManager.Get(card.player.Key("character"))
            if isinstance(picks, dict):
                characters = len(picks)
                break
        if characters is None:
            characters = AutonaActions.instance.EmptyRosterCharacters(
                self.scoreboardNumber)
        if self.charNumber.value() != characters:
            with QSignalBlocker(self.charNumber):
                self.charNumber.setValue(characters)

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
        the neighbouring one in the state (AutonaActions.SwapPlayers, which
        also asks for the statistics when other players moved: ADR-005
        S3e-5). The ends of the list are not a move."""
        team = 1 if card in self.team1playerWidgets else 2
        widgets = self.team1playerWidgets if team == 1 \
            else self.team2playerWidgets
        index = widgets.index(card)
        other = max(0, min(len(widgets) - 1, index + step))
        if other == index:
            return
        self.CommitFocusedEdit()
        AutonaActions.instance.SwapPlayers(
            self.scoreboardNumber, team, index + 1, other + 1)

    def ResetScore(self):
        # One operation, one save cycle: driving the two spinboxes in turn
        # (as this used to) made a reset two separate export cycles.
        AutonaActions.instance.ResetScore(self.scoreboardNumber)

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

    def NewSetSelected(self, data):
        """A set to show: the set window's (its `auto_update` is "set").
        Core's since ADR-005 S3e-7 (SetFollowService.LoadSet: the clears,
        the set recorded as followed, the fetch, the poll); the timer row
        follows what it starts (_OnFollowChanged)."""
        self.setFollow.LoadSet(data)

    def StopAutonaUpdate(self, clear_variables=False):
        """Stop polling; the cancel button (`clear_variables`) also forgets
        the station and the followed set (SetFollowService.StopFollowing)."""
        self.setFollow.StopFollowing(forget=clear_variables)

    def _OnFollowChanged(self, board):
        """What a board follows changed (SetFollowService): this board's
        timer row shows its poll -- what it follows and the countdown -- or
        hides. The label is left as it was when the row hides, as before."""
        if board != self.scoreboardNumber:
            return
        following = self.setFollow.Following()
        if following is None:
            if self._followClock is not None:
                self._followClock.stop()
            self.timerLayout.setVisible(False)
            return
        mode, identifier = following
        if mode == "set":
            label = QApplication.translate("app", "Auto update (Set)")
        elif mode == "stream":
            label = QApplication.translate(
                "app", "Auto update (Stream [{0}])").format(identifier)
        elif mode == "station":
            label = QApplication.translate(
                "app", "Auto update (Station [{0}])").format(identifier)
        else:
            label = QApplication.translate("app", "Auto update")
        self.labelAutonaUpdate.setText(label)
        self.timerLayout.setVisible(True)
        if self._followClock is None:
            self._followClock = QTimer(self)
            self._followClock.timeout.connect(self.UpdateTimeLeftTimer)
        self._followClock.start(100)

    def UpdateTimeLeftTimer(self):
        seconds = self.setFollow.SecondsLeft()
        if seconds is not None:
            self.timerTime.setText(str(seconds))

    def LoadSetClicked(self):
        # Restores the cached set list; fetching is the operator's call from
        # inside the window.
        self.selectSetWindow.PrepareForDisplay()
        self.selectSetWindow.show()

    def LoadStationSets(self, station):
        """The station window's station or stream: followed by core
        (SetFollowService.FollowStation), which loads its current set."""
        self.setFollow.FollowStation(station)

    def LoadStationSetClicked(self):
        self.selectStationWindow.LoadStations()
        self.selectStationWindow.show()

    # Used for score change commands
    # Change <team>(0, 1) score by <change>(+X, -X)
    def CommandScoreChange(self, team: int, change: int):
        # Kept as the widget-side name for the operation (the +1 / -1 buttons
        # use it), but the write is AutonaActions': the spinbox follows
        # through _SyncScoreDisplays.
        # `team` is 0-based here and 1-based in the state, hence the +1.
        AutonaActions.instance.AdjustScore(
            self.scoreboardNumber, team + 1, change)

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
          focus at all (CommitFocusedEdit); the cards discard it after the
          clear (DiscardTypedText, ADR-005 S3e-3), where it used to stay on
          a combo that already showed the emptied row;
        - the set loaded last is forgotten, so loading it again does load
          it (SetFollowService.LoadSet skips the set it already has).

        The statistics the clear calls for are the operation's
        (AutonaActions.ClearPlayers, ADR-005 S3e-5; this board asked for
        them here until then).
        """
        self.CommitFocusedEdit()
        clear()
        self.DiscardTypedText()
        self.setLoad.lastSetSelected = None

    def DiscardTypedText(self):
        """Every card drops the text typed into its combos and never picked,
        the combo whose list is open included (a combo keeps its typed text
        through CommitFocusedEdit, which closes a completer popup but not a
        combo's own list). What a players' clear does after the clear: this
        board's (_ClearingPlayers) and the board host's (ClearPlayers, the
        Web's). A load from elsewhere leaves the combo being typed in alone
        instead (_CardLoaded)."""
        for card in self.playerWidgets:
            card.DiscardTypedText()

    def ClearScore(self):
        """Zero both scores and both losers flags -- and nothing else
        (AutonaActions.ClearScore).

        The same clear a set load runs before a new set arrives
        (SetFollowService.LoadSet -> AutonaActions.ClearScore), so it must
        leave the match bar alone: SetLoadService.ApplyMatchInfo keeps
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
        AutonaActions.ClearBoard) and AutonaWebOperations' reset_match /
        clear_all (legacy REST, Socket.IO and /api/v1 :reset-match /
        :clear), which call the operation directly. A set load does not: see
        ClearScore.
        """
        AutonaActions.instance.ResetMatch(self.scoreboardNumber)

    def PlayerLoaded(self, team, player, cleared):
        """A player was loaded into slot `player` of state team `team` (the
        load is core's and the slot is the state's, ADR-005 S3e-4: a web
        operation, or a set's entrants through SetFollowService): the card
        showing that slot, if there is one yet, does what it does after a
        load (_CardLoaded). Called by the desktop's board host inside the
        load's save block, on the GUI thread."""
        cards = (self.team1playerWidgets if int(team) == 1
                 else self.team2playerWidgets)
        if 1 <= int(player) <= len(cards):
            self._CardLoaded(cards[int(player) - 1], cleared)

    @staticmethod
    def _CardLoaded(card, cleared):
        # A load that clears also discards the text typed into the card's
        # combos and never picked (DiscardTypedText), but for the combo the
        # operator is in: the card's SetData started with its Clear(), which
        # put every combo back on its row, and the render only moves a combo
        # whose row is wrong -- the typed text stayed, and the next pick on
        # the card published it (S3e-3 review). The statistics are the load's
        # own (PlayerService.Announce, ADR-005 S3e-5).
        if cleared:
            card.DiscardTypedText(notEditing=True)

    pass
