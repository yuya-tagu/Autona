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

More than one writer, still. ``score.{n}.team.{t}.score`` is written by
``AutonaActions`` (the +1 buttons, hotkeys, Web UI, API), by
``SetLoadService.ApplyScores`` (a loaded set, Step 4b) and by
``PublishStageResults`` below, which sums the stage rows. Since ADR-005 S4t
the rows follow the score in the operation that moved it, in its own block:
a writer reads ``Scores()`` first and hands them to ``FollowScores()``. Until
then the board did it from its held cycle (``_SyncScoreDisplays``, with the
old value on its spinbox), which is why a board with no widget had no rows.
The rows' own writes (``PublishStageResults``) and a swap, which mirrors the
rows with the scores, do not follow.

Import side effects: none (agent-pitfalls #1). One instance per 1v1 / team
board, built by AutonaActions and reached through ``GameTracker(board)``
(ADR-005 S4t: the board's AutonaIndividualGameTracker used to build it and
register it while the board existed), so that every shell has the rows and a
set length set from any entry point resizes them in the same cycle.
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
        # Bumped by every SetStageCount(): how the widget tells that the rows
        # were rebuilt, which a same-sized rebuild (a game load) does not say
        # through the row count (ADR-005 S2e).
        self._rowsVersion = 0

    @property
    def rowsVersion(self) -> int:
        """How many times SetStageCount() has rebuilt the rows.

        The widget (AutonaIndividualGameTracker.RenderStageRows) rebuilds
        its buttons when this moved since it last did: the rows are fresh
        then even when their number is not -- a game load resets them to the
        same set length, and its stage combos must start over with the new
        game's stages, as they did when the spinbox's re-emitted valueChanged
        rebuilt them.
        """
        return self._rowsVersion

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

        INVARIANT: this counts the state, and SetStageCount() is the single
        place that resizes it -- reached from AutonaActions.SetBestOf, which
        rebuilds the rows in the same cycle as the set length (ADR-005 S2e).
        The widget renders `stage_widget_list` from it on the board's held
        cycle (RenderStageRows). Everything else here indexes rows the widget
        is assumed to have a button for, so a writer that grows `stages` on its
        own (a set load flowing `stagesWon` in -- Step 4b) would leave rows
        nothing renders and clicks that address the wrong game. Such a path
        must go through SetStageCount().
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

    def _HasRow(self, index, operation) -> bool:
        """Whether row `index` exists; a warning when it does not. Caller
        holds StateManager.lock, so the answer stands for its write.

        The rows are resized by SetStageCount() alone, and a set length set
        from another thread (the Web, the API) resizes them before the
        board's held cycle has rebuilt its buttons. A click on a button of a
        row that is gone in between must not write one back: it would add a
        row nothing renders, out of sequence with the others
        (`stages.{n}` without the rows before it).
        """
        if isinstance(index, int) and 0 <= index < self.StageCount():
            return True
        logger.warning(
            f"{self.prefix}: {operation} on game {index}, but the set has "
            f"{self.StageCount()} game(s); ignored")
        return False

    def SetStageResult(self, index, winner) -> bool:
        """Record who won one game. `winner` is UNREPORTED/TIE/TEAM_1/TEAM_2.
        True if written; a row that does not exist is not (`_HasRow`).

        Exclusive by construction: the three flags are always written
        together, so no row can ever come out with two of them set. The
        widget used to rely on unchecking the sibling buttons for that.
        """
        with StateManager.SaveBlock():
            with StateManager.lock:
                if not self._HasRow(index, "a result"):
                    return False
                self._writeResult(index, winner)
        return True

    def SetStageAsset(self, index, data) -> bool:
        """Replace one row with the stage asset the combo selected. True if
        written; a row that does not exist is not (`_HasRow`).

        Faithful to what the combo handler has always done, oddity included:
        the row *is* the stage payload, so writing it drops the three result
        flags and they have to be put back immediately. They are restored
        from the state (read before the overwrite) rather than from the
        buttons, which is the same value and one less thing the widget has
        to be right about.
        """
        with StateManager.SaveBlock():
            with StateManager.lock:
                if not self._HasRow(index, "a stage"):
                    return False
                winner = self.GameWinner(index)
                StateManager.Set(self._stageKey(index), data)
                self._writeResult(index, winner)
        return True

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
            # Inside the block: the held cycle its release notifies is where
            # the widget reads it.
            self._rowsVersion += 1

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
        game result and the set score follows. Nothing follows it back: the
        rows are already exactly what the operator clicked (FollowScores is
        for the writers that move the score on their own).

        The lock runs from the sum to the writes: a +1 on another thread,
        with the row it follows onto, would otherwise fall between the two
        and be written over with the older sum. Only state reads and writes
        run inside, and the block closes outside it, so this holds the lock
        no longer than a +1 does (agent-pitfalls #17).
        """
        with StateManager.SaveBlock():
            with StateManager.lock:
                team_1_score, team_2_score = self.SumStageResults()
                StateManager.Set(f"{self.prefix}.team.1.score", team_1_score)
                StateManager.Set(f"{self.prefix}.team.2.score", team_2_score)
        return team_1_score, team_2_score

    def Scores(self) -> tuple:
        """(team 1, team 2) as the state holds them, None for a value that
        is not a number. What a writer reads before it writes, for
        FollowScores()."""
        return tuple(self._Score(team) for team in (1, 2))

    def _Score(self, team):
        value = StateManager.Get(f"{self.prefix}.team.{team}.score", 0)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        return value

    def FollowScores(self, before):
        """Move the rows by what the team scores moved since `before`
        (Scores()), both teams in one save cycle. Nothing is written when
        nothing follows, so the block costs no cycle then.

        For the writers that move the score on their own: AutonaActions'
        score operations and a loaded set. Each calls it in its own block,
        with the lock held from the read of `before` on, so that a +1 from
        another thread is either in `before` or not started yet, and is
        never followed twice. Never from a state notification: the rows would
        be written a cycle later, in a shell that has a board to notify.

        A loaded set follows after its set length (SetFollowService
        .ApplySetPayload): the transition goes onto the rows that length
        made, the net one from the score the set found.
        """
        with StateManager.SaveBlock():
            with StateManager.lock:
                for team, old in enumerate(before):
                    new = self._Score(team + 1)
                    if old is None or new is None:
                        # Nothing writes one; a breadcrumb rather than a
                        # transition made up from it.
                        logger.warning(
                            f"{self.prefix}.team.{team + 1}.score is not a "
                            f"number ({old!r} -> {new!r}); the games do not "
                            "follow it")
                        continue
                    self.SyncStagesToScore(team, old, new)

    def SyncStagesToScore(self, team, old_value, new_value):
        """Award / clear as many rows as the score moved by.

        The score -> stages direction. Writes only ``stages.*``; the team
        score itself is the caller's business either way. With no rows (the
        tracker switched off, the best of unset) nothing follows.
        """
        if old_value == new_value or not self.StageCount():
            return
        logger.info(f"SyncStagesToScore: old[{old_value}] new[{new_value}]")

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
