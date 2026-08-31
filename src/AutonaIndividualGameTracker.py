
from loguru import logger
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from .AutonaGameAssetManager import AutonaGameAssetManager
from .Helpers.AutonaQtHelper import assert_gui_thread
from .Helpers.AutonaVersionHelper import add_beta_label
from .SettingsManager import SettingsManager
from .StateManager import StateManager


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
        self._layout.addWidget(label)
        self.setLayout(self._layout)
        self.stage_widget_list = []
        self.stage_order_list_widget = QWidget()

        # True while StageResultsChanged is publishing the team scores it
        # summed from the stage rows. See the property below.
        self._writingScore = False

    @property
    def writingScore(self) -> bool:
        """True while this tracker is itself the author of the team score.

        The scoreboard reads this from its held_state_updated handler. A
        score write notifies synchronously on the same thread, so the board's
        _SyncScoreDisplays runs *inside* StageResultsChanged -- with the
        spinbox still holding the previous value. Left to itself it would
        read that as a score transition and call SyncStagesToScore, which
        awards a stage for it: one click on "game 1: T1" would mark game 2 as
        won as well. While this is set the board syncs its spinbox and stops
        there, because the stage rows are already exactly what the operator
        clicked.
        """
        return self._writingScore

    @assert_gui_thread
    def CreateStage(self, index=0):
        """ Creates a new stage in the list of stage results. """

        def uncheck_buttons_if_true(value, list_buttons):
            if value:
                for button in list_buttons:
                    button.setChecked(False)

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

        # Add Logic
        stageMenu.currentIndexChanged.connect(
            lambda: [
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index+1}", stageMenu.currentData()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t1_win", stageTeam1Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t2_win", stageTeam2Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.tie", stageTieCheck.isChecked()),
            ]
        )
        with StateManager.SaveBlock():
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t1_win", False)
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t2_win", False)
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.tie", False)

        stageTeam1Check.clicked.connect(
            lambda: [
                uncheck_buttons_if_true(stageTeam1Check.isChecked(), [stageTeam2Check, stageTieCheck]),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t1_win", stageTeam1Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t2_win", stageTeam2Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.tie", stageTieCheck.isChecked()),
                self.StageResultsChanged()
            ]
        )
        stageTeam2Check.clicked.connect(
            lambda: [
                uncheck_buttons_if_true(stageTeam2Check.isChecked(), [stageTeam1Check, stageTieCheck]),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t1_win", stageTeam1Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t2_win", stageTeam2Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.tie", stageTieCheck.isChecked()),
                self.StageResultsChanged()
            ]
        )
        stageTieCheck.clicked.connect(
            lambda: [
                uncheck_buttons_if_true(stageTieCheck.isChecked(), [stageTeam1Check, stageTeam2Check]),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t1_win", stageTeam1Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.t2_win", stageTeam2Check.isChecked()),
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{index + 1}.tie", stageTieCheck.isChecked()),
                self.StageResultsChanged()
            ]
        )

        stageLayout.addWidget(gameLabel)
        if StateManager.Get("game.has_stages", False): # Only add stage column if the game supports stage
            stageLayout.addWidget(stageMenu)
        stageLayout.addWidget(stageTeam1Check)
        stageLayout.addWidget(stageTieCheck)
        stageLayout.addWidget(stageTeam2Check)

        stageWidget.setLayout(stageLayout)
        return stageWidget

    @assert_gui_thread
    def SetStageCount(self, stage_count=5):
        """Sets the max number of stages in the set"""

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
        # Coalesce all per-stage Set calls into a single state save / WS broadcast.
        with StateManager.SaveBlock():
            StateManager.Set(f"score.{self.scoreboard_number}.stages", {})
            if stage_count == 0 or SettingsManager.Get('general.disable_individual_game_tracker', True):
                self.setVisible(False)
            else:
                self.setVisible(True)
                for i in range(stage_count):
                    self.stage_widget_list.append(self.CreateStage(i))
                    self.stage_order_list_layout.addWidget(self.stage_widget_list[-1])
        self._layout.addWidget(self.stage_order_list_widget)

    @assert_gui_thread
    def GetFirstEmptyStage(self, start_idx=None) -> int:
        idx = start_idx if start_idx is not None else 0

        while idx < len(self.stage_widget_list):
            game_winner = self._get_game_winner(idx)
            if game_winner == -1:
                return idx
            idx += 1

        return -1

    @assert_gui_thread
    def GetWonStages(self, team) -> list[int]:
        idx = len(self.stage_widget_list)-1

        return [
            idx
            for idx
            in range(len(self.stage_widget_list))
            if self._get_game_winner(idx) == (team + 1)
        ]

    def _get_game_winner(self, game_idx):
        """
        -1 = unreported, 0 = draw, 1 = team 1, 2 = team 2
        """
        current_stage_widget = self.stage_widget_list[game_idx]
        stageTeam1Check: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTeam1Check_{game_idx}")
        stageTeam2Check: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTeam2Check_{game_idx}")
        stageTieCheck: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTieCheck_{game_idx}")

        if stageTeam1Check is None:
            raise RuntimeError(f"stageTeam1Check_{game_idx} not found")
        if stageTeam2Check is None:
            raise RuntimeError(f"stageTeam2Check_{game_idx} not found")
        if stageTieCheck is None:
            raise RuntimeError(f"stageTieCheck_{game_idx} not found")

        if stageTieCheck.isChecked(): return 0
        elif stageTeam1Check.isChecked(): return 1
        elif stageTeam2Check.isChecked(): return 2
        else: return -1

    @Slot(int, int)
    @assert_gui_thread
    def UpdateScore(self, team, new_value):
        """Responds to the score being modified in a non-per-game context.

        Still the handler for the paths that drive the score spinbox
        directly (set loading, ClearScore, SwapTeams): they change the widget
        and this writes the result. The paths that go through
        AutonaActions.AdjustScore / ResetScore instead write the state first
        and call SyncStagesToScore() below with the transition, so the two
        halves were split apart (headless core Step 3).
        """

        # Read-modify-write under the lock, same as AutonaActions.AdjustScore:
        # the score is written from the web/API threads through Actions now, so
        # a concurrent write between the Get and the Set here would be read
        # back as this widget's own transition and desynchronise the stages.
        key = f"score.{self.scoreboard_number}.team.{team + 1}.score"
        with StateManager.lock:
            old_value = StateManager.Get(key, 0)
            StateManager.Set(key, new_value)

        self.SyncStagesToScore(team, old_value, new_value)

    @assert_gui_thread
    def SyncStagesToScore(self, team, old_value, new_value):
        """Award / clear as many stage rows as the score moved by.

        Writes only `score.{n}.stages.*` -- the team score itself is the
        caller's business either way.
        """
        logger.info(f"UpdateScore: old[{old_value}] new[{new_value}]")

        # Disable individual game tracker logic if ties were reported
        has_ties = False
        game_data = StateManager.Get(f"score.{self.scoreboard_number}.stages")
        for key in (game_data or {}).keys():
            if game_data[key].get("tie"):
                has_ties = True

        if has_ties:
            logger.warning("Disabling individual game tracker due to detected tie results.")
            return

        counter = old_value
        for _ in range(abs(new_value - old_value)):
            if new_value > old_value:
                self.IncrementScore(team)
                counter += 1
            else:
                self.DecrementScore(team, counter)
                counter -= 1

    def IncrementScore(self, team, current_stage=None):
        """Sets the next stage to be won by the provided team."""

        if not current_stage:
            current_stage = 0

        current_stage = self.GetFirstEmptyStage(current_stage)

        if current_stage == -1:
            logger.warning(f"Attempted to increment score for team {team+1}, but all stages are completed.")
            return

        logger.info(f"Setting a win for team {team+1} on game {current_stage}")

        if current_stage >= len(self.stage_widget_list):
            logger.warning(f"Attempted to increment score for team {team+1} on stage {current_stage+1}, but there are only {len(self.stage_widget_list)} stages.")

        current_stage_widget = self.stage_widget_list[current_stage]
        stageTeam1Check: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTeam1Check_{current_stage}")
        stageTeam2Check: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTeam2Check_{current_stage}")
        stageTieCheck: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTieCheck_{current_stage}")
        if team == 0:
            stageTeam1Check.setChecked(True)
            stageTeam2Check.setChecked(False)
        else:
            stageTeam2Check.setChecked(True)
            stageTeam1Check.setChecked(False)

        stageTieCheck.setChecked(False)
        with StateManager.SaveBlock():
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{current_stage+1}.t1_win", stageTeam1Check.isChecked())
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{current_stage+1}.t2_win", stageTeam2Check.isChecked())
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{current_stage+1}.tie", stageTieCheck.isChecked())

    @assert_gui_thread
    def DecrementScore(self, team=None, original_score=None):
        """ Removes the last reported stage result. Optionally bounded to team. """

        won_stages = self.GetWonStages(team)

        if original_score and len(won_stages) < original_score:
            logger.warning(f"Failed to decrement score for team {team} because they have won fewer stages than their score. {len(won_stages)} < {original_score}")
            return

        current_stage = won_stages[-1]
        if current_stage == -1:
            logger.warning(f"Could not decrement score for team {team}, could not find a stage to clear.")
            return

        logger.info(f"Clearing result for stage {current_stage+1}")

        current_stage_widget = self.stage_widget_list[current_stage]
        stageTeam1Check: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTeam1Check_{current_stage}")
        stageTeam2Check: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTeam2Check_{current_stage}")
        stageTieCheck: QPushButton = current_stage_widget.findChild(QPushButton, f"stageTieCheck_{current_stage}")
        stageTeam1Check.setChecked(False)
        stageTeam2Check.setChecked(False)
        stageTieCheck.setChecked(False)
        with StateManager.SaveBlock():
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{current_stage+1}.t1_win", stageTeam1Check.isChecked())
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{current_stage+1}.t2_win", stageTeam2Check.isChecked())
            StateManager.Set(f"score.{self.scoreboard_number}.stages.{current_stage+1}.tie", stageTieCheck.isChecked())

    @assert_gui_thread
    def StageResultsChanged(self):
        team_1_score, team_2_score = 0, 0
        for i in range(len(self.stage_widget_list)):
            stageTeam1Check = self.stage_widget_list[i].findChild(QPushButton, f"stageTeam1Check_{i}")
            stageTeam2Check = self.stage_widget_list[i].findChild(QPushButton, f"stageTeam2Check_{i}")
            if stageTeam1Check.isChecked():
                team_1_score += 1
            if stageTeam2Check.isChecked():
                team_2_score += 1

        # The flag has to cover the SaveBlock's *exit* as well as its body:
        # a held write only notifies when the block is released, so that is
        # when the board's _SyncScoreDisplays runs (see the writingScore
        # property). Cleared before the signal below, which is the board's cue
        # to update the spinboxes for the ordinary, non-held case.
        self._writingScore = True
        try:
            with StateManager.SaveBlock():
                StateManager.Set(f"score.{self.scoreboard_number}.team.1.score", team_1_score)
                StateManager.Set(f"score.{self.scoreboard_number}.team.2.score", team_2_score)
        finally:
            self._writingScore = False
        self.signals.stageResultsUpdate.emit(team_1_score, team_2_score)

    @assert_gui_thread
    def SwapStageResults(self):
        # Coalesce all 3*N stage Sets into a single broadcast.
        with StateManager.SaveBlock():
            for i in range(len(self.stage_widget_list)):
                stageTeam1Check = self.stage_widget_list[i].findChild(QPushButton, f"stageTeam1Check_{i}")
                stageTeam2Check = self.stage_widget_list[i].findChild(QPushButton, f"stageTeam2Check_{i}")
                stageTieCheck = self.stage_widget_list[i].findChild(QPushButton, f"stageTieCheck_{i}")
                team_1_old_state, team_2_old_state = stageTeam1Check.isChecked(), stageTeam2Check.isChecked()
                stageTeam1Check.setChecked(team_2_old_state)
                stageTeam2Check.setChecked(team_1_old_state)
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{i+1}.t1_win", stageTeam1Check.isChecked())
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{i+1}.t2_win", stageTeam2Check.isChecked())
                StateManager.Set(f"score.{self.scoreboard_number}.stages.{i+1}.tie", stageTieCheck.isChecked())

