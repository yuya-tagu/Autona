"""The per-game result rows: buttons in, service calls out.

Everything this widget used to decide -- which game a +1 awards, what the
team score adds up to, how a swap maps onto the rows -- moved to
`src/core/GameTrackerService.py` (headless core Step 4a), together with the
`score.{n}.stages.*` reads and writes. The inversion that came with it is
the one Step 3 applied to the score: the *state* is the record and these
buttons render it, where before `stageTeam1Check_{i}.isChecked()` was the
truth and the state a mirror.

So every handler below has the same three steps: turn the click into one
service call, re-read the state into the buttons, and (where the score
follows from the rows) tell the board. Re-reading is safe because
`QAbstractButton.setChecked()` emits `toggled`, never `clicked` -- nothing
here is connected to `toggled`, so a refresh cannot echo back into another
write.

What is deliberately still here: the rows themselves, the stage combo bound
to AutonaGameAssetManager's shared model, and `signals.stageResultsUpdate`,
which is how the board learns the score changed. How many rows there are is
the state's as well since ADR-005 S2e (AutonaActions.SetBestOf resizes them
with the set length); RenderStageRows builds the buttons for them.
"""

from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from .AutonaGameAssetManager import AutonaGameAssetManager
from .core.GameTrackerService import (
    GameTrackerService, TEAM_1, TEAM_2, TIE, UNREPORTED)
from .Helpers.AutonaQtHelper import assert_gui_thread
from .Helpers.AutonaVersionHelper import add_beta_label
from .core.StateManager import StateManager


class AutonaIndividualGameTrackerSignals(QObject):
    stageResultsUpdate = Signal(int, int)


class AutonaIndividualGameTracker(QWidget):
    def __init__(self, scoreboard_number, parent=None):
        super().__init__()

        self._layout = QVBoxLayout()
        self.signals = AutonaIndividualGameTrackerSignals(self)

        label = QLabel(
            text=add_beta_label(
                QApplication.translate(
                    "app",
                    "Individual game data").upper(),
                "game_tracker"
            )
        )

        label_font = QFont()
        label_font.setPointSize(10)
        label_font.setBold(True)
        label.setFont(label_font)
        self.scoreboard_number = scoreboard_number
        self.service = GameTrackerService(scoreboard_number)
        self._layout.addWidget(label)
        self.setLayout(self._layout)
        self.stage_widget_list = []
        self.stage_order_list_widget = QWidget()
        # The service's rowsVersion the rows were last built for; None until
        # the first RenderStageRows(), which always builds.
        self._renderedRowsVersion = None

    @property
    def writingScore(self) -> bool:
        """True while the tracker is itself the author of the team score.

        Read by AutonaScoreboardWidget._SyncScoreDisplays; the reasoning is
        on GameTrackerService.writingScore, which owns the flag now.
        """
        return self.service.writingScore

    # ── the rows ─────────────────────────────────────────────────────────

    @assert_gui_thread
    def CreateStage(self, index=0):
        """ Creates a new stage in the list of stage results. """

        stageWidget = QWidget()
        stageLayout = QHBoxLayout()

        gameLabel = QLabel()
        gameLabel.setText(QApplication.translate("app", "Game {0}").format(index + 1))

        stageMenu = QComboBox()
        stageMenu.setMaximumWidth(300)
        stageMenu.setEditable(True)
        stageMenu.setObjectName(f"stageMenu_{index}")
        stageMenu.setModel(AutonaGameAssetManager.instance.stageModelWithBlank)
        stageMenu.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        stageMenu.completer().setCompletionMode(QCompleter.PopupCompletion)
        stageTeam1Check = QPushButton()
        stageTeam1Check.setMaximumWidth(40)
        stageTeam1Check.setObjectName(f"stageTeam1Check_{index}")
        stageTeam1Check.setText(QApplication.translate("app", "T{0}").format(1))
        stageTeam1Check.setCheckable(True)
        stageTeam2Check = QPushButton()
        stageTeam2Check.setMaximumWidth(40)
        stageTeam2Check.setObjectName(f"stageTeam2Check_{index}")
        stageTeam2Check.setText(QApplication.translate("app", "T{0}").format(2))
        stageTeam2Check.setCheckable(True)
        stageTieCheck = QPushButton()
        stageTieCheck.setMaximumWidth(40)
        stageTieCheck.setObjectName(f"stageTieCheck_{index}")
        stageTieCheck.setText(QApplication.translate("app", "Tie"))
        stageTieCheck.setCheckable(True)

        # Add Logic. Connected after setModel() above, so binding the shared
        # stage model does not count as a selection.
        stageMenu.currentIndexChanged.connect(
            lambda: self._StageAssetChanged(index, stageMenu))

        # A checkable QPushButton has already toggled by the time `clicked`
        # arrives, so "still checked" means this result was set and "no
        # longer checked" means the operator cleared the game. The siblings
        # are not touched here: exclusivity is the service's business, and
        # the refresh below brings them into line.
        for button, result in ((stageTeam1Check, TEAM_1),
                               (stageTeam2Check, TEAM_2),
                               (stageTieCheck, TIE)):
            button.clicked.connect(
                lambda checked=False, b=button, r=result:
                    self._StageResultClicked(index, r, b))

        stageLayout.addWidget(gameLabel)
        if StateManager.Get("game.has_stages", False): # Only add stage column if the game supports stage
            stageLayout.addWidget(stageMenu)
        stageLayout.addWidget(stageTeam1Check)
        stageLayout.addWidget(stageTieCheck)
        stageLayout.addWidget(stageTeam2Check)

        stageWidget.setLayout(stageLayout)
        return stageWidget

    @assert_gui_thread
    def _StageButtons(self, index):
        """The (T1, T2, Tie) buttons of one row.

        Raises rather than returning None for a missing button: the object
        names are built from the same index the row was created with, so a
        miss means the row is not what this widget thinks it is, and every
        caller would otherwise fail one line later on None.
        """
        row = self.stage_widget_list[index]
        buttons = []
        for name in ("stageTeam1Check", "stageTeam2Check", "stageTieCheck"):
            button = row.findChild(QPushButton, f"{name}_{index}")
            if button is None:
                raise RuntimeError(f"{name}_{index} not found")
            buttons.append(button)
        return tuple(buttons)

    @assert_gui_thread
    def _SyncStageButtons(self):
        """Render the recorded results. The one direction state -> buttons."""
        for index in range(len(self.stage_widget_list)):
            winner = self.service.GameWinner(index)
            team1, team2, tie = self._StageButtons(index)
            team1.setChecked(winner == TEAM_1)
            team2.setChecked(winner == TEAM_2)
            tie.setChecked(winner == TIE)

    @assert_gui_thread
    def RebindStageModel(self):
        """Point every row's stage combo at the current stage model.

        The asset manager replaces its models on every game load and keeps
        the previous generation alive for one more load only
        (agent-pitfalls #13). The board normally rebuilds the rows on the
        same onLoad (AutonaActions.RepublishBestOf resets them, and
        RenderStageRows rebuilds the buttons from the held cycle), but a row
        that survived it would be left bound to a model that the next load
        deletes. Signals are blocked: this is not a stage selection, and
        must not write score.{n}.stages.*.

        Called from the board's onLoad handler rather than connected here:
        this widget sits in the board's score column, which is parentless
        and outlives a destroyed board, so a connection of its own would
        keep answering onLoad as a ghost (tests/test_signal_lifetime.py).
        """
        model = AutonaGameAssetManager.instance.stageModelWithBlank
        for index, row in enumerate(self.stage_widget_list):
            combo = row.findChild(QComboBox, f"stageMenu_{index}")
            if combo is None or combo.model() is model:
                continue
            text = combo.currentText()
            with QSignalBlocker(combo):
                combo.setModel(model)
                found = combo.findText(text) if text else -1
                combo.setCurrentIndex(found if found >= 0 else 0)

    # ── handlers ─────────────────────────────────────────────────────────

    @assert_gui_thread
    def _StageResultClicked(self, index, result, button):
        written = self.service.SetStageResult(
            index, result if button.isChecked() else UNREPORTED)
        self._SyncStageButtons()
        # A row the set no longer has (resized from another thread, its
        # buttons not rebuilt yet) is refused: the score is not summed from
        # a click that recorded nothing.
        if written:
            self.StageResultsChanged()

    @assert_gui_thread
    def _StageAssetChanged(self, index, combo):
        # No button refresh: the service carries the result flags across the
        # overwrite itself, so there is nothing here for the buttons to
        # learn.
        self.service.SetStageAsset(index, combo.currentData())

    @assert_gui_thread
    def RenderStageRows(self):
        """Show one row per game the state records. Writes nothing.

        The rows are resized by core with the set length
        (AutonaActions.SetBestOf -> GameTrackerService.SetStageCount, ADR-005
        S2e); this used to be the method that did both, from the BEST OF
        spinbox's valueChanged. Called by the board on every held cycle,
        before the score is followed onto the rows, so the buttons a
        transition is rendered on are the ones it was made for.

        Rebuilt when core rebuilt the rows -- also to the same number, which
        is what a game load does: the stage combos start over with the new
        game's stages -- and otherwise left alone.
        """
        count = self.service.StageCount()
        if (self._renderedRowsVersion == self.service.rowsVersion
                and len(self.stage_widget_list) == count):
            return
        self._renderedRowsVersion = self.service.rowsVersion

        self._layout.removeWidget(self.stage_order_list_widget)
        # ...and actually destroy it. removeWidget() only detaches it from the
        # layout -- the container stays a child of this tracker, so rebuilding
        # on every best-of change left a dead container behind for the rest of
        # the session, with its stage rows and their currentIndexChanged
        # lambdas still writing score.{n}.stages.*.
        #
        # deleteLater() is correct here because this is a QWidget. What
        # agent-pitfalls #14 bans is takeAt()/removeItem() followed by
        # deleteLater() on a QLayoutItem: those are not QObjects, so Qt frees
        # them where Shiboken cannot see it. A QObject-derived widget is
        # tracked by PySide6, and deleteLater() is the ordinary way to end one.
        self.stage_order_list_widget.setParent(None)
        self.stage_order_list_widget.deleteLater()
        self.stage_widget_list = []
        self.stage_order_list_layout = QVBoxLayout()
        self.stage_order_list_widget = QWidget()
        self.stage_order_list_widget.setLayout(self.stage_order_list_layout)

        # 0 when the feature is switched off or the best-of is unset
        # (GameTrackerService.ResolveStageCount), which is also what hides
        # the widget.
        self.setVisible(count > 0)
        for i in range(count):
            self.stage_widget_list.append(self.CreateStage(i))
            self.stage_order_list_layout.addWidget(self.stage_widget_list[-1])
        self._layout.addWidget(self.stage_order_list_widget)
        self._SyncStageButtons()

    @Slot(int, int)
    @assert_gui_thread
    def UpdateScore(self, team, new_value):
        """Responds to the score being modified in a non-per-game context.

        The score spinbox's valueChanged handler. Only a number typed into
        the score display drives the spinbox now (the clears write the state
        since ADR-005 S2d); this publishes the result and moves the rows to
        match.
        """
        self.service.UpdateScore(team, new_value)
        self._SyncStageButtons()

    @assert_gui_thread
    def SyncStagesToScore(self, team, old_value, new_value):
        """Award / clear as many stage rows as the score moved by.

        Called by the board when the score was written by somebody else
        (AutonaActions: the +1 buttons, the hotkeys, the Web UI, the API).
        """
        self.service.SyncStagesToScore(team, old_value, new_value)
        self._SyncStageButtons()

    @assert_gui_thread
    def StageResultsChanged(self):
        team_1_score, team_2_score = self.service.PublishStageResults()
        # Emitted after the flag inside the service is cleared: this is the
        # board's cue to update the spinboxes for the ordinary, non-held case.
        self.signals.stageResultsUpdate.emit(team_1_score, team_2_score)

    @assert_gui_thread
    def RenderStageResults(self):
        """Redraw the buttons from the recorded results, for a write that
        did not come through here -- a swap mirrors the rows in core
        (AutonaActions.SwapTeams, ADR-005 S2c). Writes nothing."""
        self._SyncStageButtons()
