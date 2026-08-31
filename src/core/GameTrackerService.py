"""Per-game (stage) result tracking for one board.

The individual game tracker answers two questions that keep having to agree
with each other: which game of the set each row records, and what the team
score is that follows from those rows. Both used to be computed from the Qt
widgets themselves -- ``stageTeam1Check_{i}.isChecked()`` was the source of
truth and ``score.{n}.stages.{i}`` a mirror written after the fact -- so the
whole consistency argument lived inside a QWidget and could not be exercised
without one.

Since headless core Step 4a it is the other way round, matching what Step 3
did to the score: **the state is the record, the buttons render it**. Every
method here reads ``score.{n}.stages.*`` and writes it back; the widget
(``src/AutonaIndividualGameTracker.py``) builds the rows, turns a click into
one call, and re-reads the state to set its buttons afterwards. Setting a
QPushButton's checked state emits ``toggled``, never ``clicked``, so that
refresh cannot echo back into another call.

Row shape. ``score.{n}.stages`` is keyed "1".."N" (strings -- agent-pitfalls
#3) and each row is a dict carrying the three result flags:

    {"t1_win": bool, "t2_win": bool, "tie": bool, ...stage asset fields}

The stage combo overwrites the *whole* row with the selected stage's asset
payload (see SetStageAsset), which is why every *read* below tolerates a row
that is not a dict at all. The *writes* do not: ``_writeResult`` sets keys
*below* the row path, and ``deep_set`` raises TypeError when it has to walk
into a non-dict. The only writer that can put one there is SetStageAsset,
whose very next statement is that restore -- so a non-dict stage payload
would raise, not corrupt. Unreachable today because the combo's blank item
carries ``{}`` rather than None.

Two writers, still. ``score.{n}.team.{t}.score`` is written both by
``AutonaActions`` (the +1 buttons, hotkeys, Web UI, API) and by
``PublishStageResults`` below, which sums the stage rows. They coordinate
through the state, and the board tells them apart with ``writingScore`` --
see that property. Collapsing the two into one writer is Step 4b/5.

Import side effects: none (agent-pitfalls #1). One instance per board,
created by that board's AutonaIndividualGameTracker.
"""
from loguru import logger

from .SettingsManager import SettingsManager
from .StateManager import StateManager

# What GameWinner() reports for one row.
UNREPORTED = -1
TIE = 0
TEAM_1 = 1
TEAM_2 = 2


class GameTrackerService:
    """Stage results of one board. Bound to ``score.{scoreboardNumber}``."""

    def __init__(self, scoreboardNumber):
        self.scoreboardNumber = scoreboardNumber
        # True while PublishStageResults is writing the team scores it summed
        # from the rows. See the property below.
        self._writingScore = False

    # ── the two-writer guard ─────────────────────────────────────────────

    @property
    def writingScore(self) -> bool:
        """True while this tracker is itself the author of the team score.

        The scoreboard reads this (through the widget) from its
        held_state_updated handler. A score write notifies synchronously on
        the same thread, so the board's _SyncScoreDisplays runs *inside*
        PublishStageResults -- with the spinbox still holding the previous
        value. Left to itself it would read that as a score transition and
        call SyncStagesToScore, which awards a stage for it: one click on
        "game 1: T1" would mark game 2 as won as well. While this is set the
        board syncs its spinbox and stops there, because the stage rows are
        already exactly what the operator clicked.
        """
        return self._writingScore

    # ── key paths ────────────────────────────────────────────────────────

    @property
    def prefix(self) -> str:
        return f"score.{self.scoreboardNumber}"

    def _stageKey(self, index) -> str:
        """State path of row `index` (0-based here, 1-based in the state)."""
        return f"{self.prefix}.stages.{index + 1}"

    # ── reads ────────────────────────────────────────────────────────────

    def StageCount(self) -> int:
        """How many game rows the set currently has.

        INVARIANT: this counts the state, while the widget renders its own
        `stage_widget_list`, and only SetStageCount() keeps the two equal --
        it is the single place that resizes either. Everything else here
        indexes rows the widget is assumed to have a button for, so a writer
        that grows `stages` on its own (a set load flowing `stagesWon` in --
        Step 4b) would leave rows nothing renders and clicks that address
        the wrong game. Such a path must go through SetStageCount().
        """
        stages = StateManager.Get(f"{self.prefix}.stages")
        return len(stages) if isinstance(stages, dict) else 0

    def GameWinner(self, index) -> int:
        """UNREPORTED / TIE / TEAM_1 / TEAM_2 for one row."""
        row = StateManager.Get(self._stageKey(index))
        if not isinstance(row, dict):
            return UNREPORTED
        if row.get("tie"):
            return TIE
        if row.get("t1_win"):
            return TEAM_1
        if row.get("t2_win"):
            return TEAM_2
        return UNREPORTED

    def FirstEmptyStage(self, start_idx=None) -> int:
        """Index of the first unreported row at or after `start_idx`, or -1."""
        idx = start_idx if start_idx is not None else 0
        count = self.StageCount()
        while idx < count:
            if self.GameWinner(idx) == UNREPORTED:
                return idx
            idx += 1
        return -1

    def WonStages(self, team) -> list:
        """Row indices won by `team` (0-based team, as the widget counts)."""
        return [index for index in range(self.StageCount())
                if self.GameWinner(index) == (team + 1)]

    # ── writes ───────────────────────────────────────────────────────────

    def _writeResult(self, index, winner):
        """Write one row's three flags. Caller owns the save cycle."""
        key = self._stageKey(index)
        StateManager.Set(f"{key}.t1_win", winner == TEAM_1)
        StateManager.Set(f"{key}.t2_win", winner == TEAM_2)
        StateManager.Set(f"{key}.tie", winner == TIE)

    def SetStageResult(self, index, winner):
        """Record who won one game. `winner` is UNREPORTED/TIE/TEAM_1/TEAM_2.

        Exclusive by construction: the three flags are always written
        together, so no row can ever come out with two of them set. The
        widget used to rely on unchecking the sibling buttons for that.
        """
        with StateManager.SaveBlock():
            self._writeResult(index, winner)

    def SetStageAsset(self, index, data):
        """Replace one row with the stage asset the combo selected.

        Faithful to what the combo handler has always done, oddity included:
        the row *is* the stage payload, so writing it drops the three result
        flags and they have to be put back immediately. They are restored
        from the state (read before the overwrite) rather than from the
        buttons, which is the same value and one less thing the widget has
        to be right about.
        """
        winner = self.GameWinner(index)
        with StateManager.SaveBlock():
            StateManager.Set(self._stageKey(index), data)
            self._writeResult(index, winner)

    def ResolveStageCount(self, requested) -> int:
        """How many rows to actually show for a best-of of `requested`.

        The tracker ships disabled, and a best-of of 0 means "not set", so
        both collapse to "no rows at all" -- which is also what makes the
        widget invisible.
        """
        if not requested or SettingsManager.Get(
                'general.disable_individual_game_tracker', True):
            return 0
        return requested

    def SetStageCount(self, count):
        """Reset the stage rows to `count` unreported games, in one cycle."""
        with StateManager.SaveBlock():
            StateManager.Set(f"{self.prefix}.stages", {})
            for index in range(count):
                self._writeResult(index, UNREPORTED)

    def IncrementScore(self, team, current_stage=None):
        """Award the next unreported game to `team` (0-based)."""
        if not current_stage:
            current_stage = 0

        current_stage = self.FirstEmptyStage(current_stage)

        if current_stage == -1:
            logger.warning(
                f"Attempted to increment score for team {team+1}, but all "
                "stages are completed.")
            return

        logger.info(
            f"Setting a win for team {team+1} on game {current_stage}")

        if current_stage >= self.StageCount():
            logger.warning(
                f"Attempted to increment score for team {team+1} on stage "
                f"{current_stage+1}, but there are only {self.StageCount()} "
                "stages.")

        self.SetStageResult(current_stage, team + 1)

    def DecrementScore(self, team=None, original_score=None):
        """Clear the last game `team` won. Optionally bounded to team."""
        won_stages = self.WonStages(team)

        if original_score and len(won_stages) < original_score:
            logger.warning(
                f"Failed to decrement score for team {team} because they "
                "have won fewer stages than their score. "
                f"{len(won_stages)} < {original_score}")
            return

        current_stage = won_stages[-1]
        if current_stage == -1:
            logger.warning(
                f"Could not decrement score for team {team}, could not find "
                "a stage to clear.")
            return

        logger.info(f"Clearing result for stage {current_stage+1}")

        self.SetStageResult(current_stage, UNREPORTED)

    def SwapStageResults(self):
        """Mirror every row's winner, keeping ties and blanks as they are."""
        swapped = {TEAM_1: TEAM_2, TEAM_2: TEAM_1}
        # One save cycle for all 3*N keys, as this always was: a swap is one
        # operation and must never be observed half-applied.
        with StateManager.SaveBlock():
            for index in range(self.StageCount()):
                winner = self.GameWinner(index)
                self._writeResult(index, swapped.get(winner, winner))

    # ── the two directions between stages and score ──────────────────────

    def SumStageResults(self):
        """(team 1, team 2) game wins, counted from the rows."""
        team_1_score, team_2_score = 0, 0
        for index in range(self.StageCount()):
            winner = self.GameWinner(index)
            if winner == TEAM_1:
                team_1_score += 1
            elif winner == TEAM_2:
                team_2_score += 1
        return team_1_score, team_2_score

    def PublishStageResults(self):
        """Write the team scores the rows add up to; returns them.

        This is the stages -> score direction, i.e. the operator clicked a
        game result and the set score follows.
        """
        team_1_score, team_2_score = self.SumStageResults()

        # The flag has to cover the SaveBlock's *exit* as well as its body:
        # a held write only notifies when the block is released, so that is
        # when the board's _SyncScoreDisplays runs (see the writingScore
        # property). Cleared before the caller emits its Qt signal, which is
        # the board's cue to update the spinboxes for the ordinary,
        # non-held case.
        self._writingScore = True
        try:
            with StateManager.SaveBlock():
                StateManager.Set(f"{self.prefix}.team.1.score", team_1_score)
                StateManager.Set(f"{self.prefix}.team.2.score", team_2_score)
        finally:
            self._writingScore = False
        return team_1_score, team_2_score

    def SyncStagesToScore(self, team, old_value, new_value):
        """Award / clear as many rows as the score moved by.

        The score -> stages direction. Writes only ``stages.*``; the team
        score itself is the caller's business either way.
        """
        logger.info(f"UpdateScore: old[{old_value}] new[{new_value}]")

        # Disable individual game tracker logic if ties were reported
        game_data = StateManager.Get(f"{self.prefix}.stages")
        has_ties = any(
            isinstance(row, dict) and row.get("tie")
            for row in (game_data or {}).values())

        if has_ties:
            logger.warning(
                "Disabling individual game tracker due to detected tie "
                "results.")
            return

        counter = old_value
        for _ in range(abs(new_value - old_value)):
            if new_value > old_value:
                self.IncrementScore(team)
                counter += 1
            else:
                self.DecrementScore(team, counter)
                counter -= 1

    def UpdateScore(self, team, new_value):
        """Publish a score set from outside, and follow it with the stages.

        Still the entry point for the paths that drive the score spinbox
        directly (set loading, ClearScore, SwapTeams): the widget changes,
        and this writes the result. The paths that go through
        AutonaActions.AdjustScore / ResetScore instead write the state first
        and call SyncStagesToScore() with the transition, which is why the
        two halves are separate methods (headless core Step 3).

        Read-modify-write under the lock, same as AutonaActions.AdjustScore:
        the score is written from the web/API threads through Actions, so a
        concurrent write between the Get and the Set here would be read back
        as this tracker's own transition and desynchronise the stages.
        """
        key = f"{self.prefix}.team.{team + 1}.score"
        with StateManager.lock:
            old_value = StateManager.Get(key, 0)
            StateManager.Set(key, new_value)

        self.SyncStagesToScore(team, old_value, new_value)
