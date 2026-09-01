"""Domain operations: where a write to the state originates.

Until now the answer to "who changes the score?" was "whichever Qt widget
owns the spinbox". `AutonaWebServerActions` looked like a write hub but never
touched `StateManager` once -- it emitted signals into a board, and the board
did the writing -- and the global hotkeys skipped even that, calling bound
methods on whatever boards happened to be built. Four entry points, one
implementation, and all of it inside a QWidget lifetime.

This module is the other end of that (ADR-004 D2, headless core Step 3): the
operation itself lives here, writes `StateManager` directly, and every entry
point -- Qt handler, REST v1, legacy REST / Socket.IO, global hotkey -- calls
it. Widgets are downgraded to "render the state, ask for an operation".

Like everything under `src/core` it must stay Qt-free (pinned by
tests/test_core_qt_free.py), which is also the acceptance test for whether an
operation belongs here yet: an operation whose *domain* rule needs a Qt object
to be expressed is not ready to move. Step 3 therefore migrated the writes
that are self-evident state edits and deliberately left two behind:

- **Swap teams.** The player half of a swap is a widget-to-widget data copy
  (`AutonaScoreboardPlayerWidget.SwapWith`), and `teamsSwapped` is read off
  the widget by `WebServerActions.get_swap`. It needs the player subtree to
  be expressible in core first (Step 4/5).
- **Best of.** `best_of_text` / `first_to_text` are formatted from
  `AutonaLocaleHelper.matchNames`, a Qt-importing module, and the same
  handler resizes the individual-game tracker's stage rows. Faithful
  reproduction needs the locale tables in core, which Step 3 does not do.
- **The spinbox-driven half of the score.** `AdjustScore` / `ResetScore`
  below are not yet the *only* writers of `score.{n}.team.{t}.score`. Three
  writers live inside core now -- these, the stage tally in
  `GameTrackerService.UpdateScore` / `PublishStageResults`, and a loaded
  set's `SetLoadService.ApplyScores` (Step 4b) -- plus the paths that have
  not moved at all: `ClearScore`, `SwapTeams` and `ResetAllInputs` still
  drive the score spinbox, whose valueChanged runs the tracker's
  `UpdateScore`. So the *implementation* is core in every case; what is
  still outside is those three entry points, and folding them in needs the
  player subtree (Step 5).
  The writers coordinate through the state rather than through each
  other; a board reconciles its display in
  `AutonaScoreboardWidget._SyncScoreDisplays`, and tells them apart with
  `GameTrackerService.writingScore`. Nothing outside core re-Sets the score:
  `StageResultsToScore` used to echo the published value back and no longer
  does.

Import side effects: none (agent-pitfalls #1). `bootstrap_core()` creates the
single instance; nothing is created here at module level.

THREADING: every method may be called from any thread. `StateManager` guards
its own state with an RLock, and the read-modify-write in `AdjustScore` takes
that lock explicitly so two concurrent `+1`s cannot both read the same value.
Nothing here touches a Qt object, so there is no GUI-thread requirement --
that is the point of the layer. The notification that follows a write is
delivered on the calling thread and crosses to the GUI thread in
`StateManagerBridge` (Qt's AutoConnection), exactly as before.
"""
from types import MappingProxyType

from loguru import logger

from . import StateSchema as schema
from .SettingsManager import SettingsManager
from .StateManager import StateManager


class AutonaActions:
    # Created once by bootstrap_core(); callers reach it as
    # AutonaActions.instance, matching the other singletons.
    instance: "AutonaActions" = None

    # Battle format -> board number. The Qt-side registry
    # (AutonaScoreboardManager.FORMAT_BOARDS) aliases this mapping rather than
    # spelling it out again: the hotkeys resolve the active board here, in
    # core, and a second copy is exactly the drift that would make the
    # headless answer differ from the one the window shows. Read-only,
    # because an alias of a plain dict is a shared mutable that any one of
    # its holders could edit for all the others.
    FORMAT_BOARDS = MappingProxyType({"singles": 1, "team": 2, "crew": 3})

    # Boards 1 (1v1) and 2 (team) carry a per-team score that an operator
    # edits directly, and their subtree is under the 反映 hold (R-Sync-11).
    # Board 3 (crew) derives its team score by summing member stocks, so a
    # team-level nudge has no target there -- the crew widget has always
    # logged and ignored it, and so does this layer.
    SCORE_BOARDS = (1, 2)
    CREW_BOARD = 3

    # The score spinboxes are plain QSpinBoxes with Qt's default range, and
    # that range has always been the only guard against a negative or absurd
    # score reaching an overlay. Now that the write no longer goes through
    # the widget, the clamp has to live with the operation.
    # AutonaScoreboardWidget applies these to the spinboxes so the two cannot
    # drift apart.
    SCORE_MIN = 0
    SCORE_MAX = 99

    # ── helpers ──────────────────────────────────────────────────────────

    def ResolveBattleFormat(self) -> str:
        """The persisted battle format: "singles", "team" or "crew".

        `scoreboard.battle_format` is the source of truth. It is not always
        present: it was introduced after `scoreboard.team_mode` (a bool), and
        an operator who has run in team mode since before that release still
        has only the old key until the next time the format is set. Falling
        back to it is what keeps their hotkeys aimed at the board they can
        actually see.

        This lives here rather than on the window because both answers have
        to be the same one: `Window._resolveBattleFormat()` decides which
        board is built and shown, `ActiveBoard()` below decides which board a
        hotkey or /api/v1/health talks about, and a second copy of the rule
        is how those two come apart.
        """
        fmt = SettingsManager.Get("scoreboard.battle_format", None)
        # isinstance first: settings come from JSON, and `in` on a mapping
        # hashes its argument -- a list left there by a corrupted file would
        # raise TypeError instead of falling through to the default.
        if isinstance(fmt, str) and fmt in self.FORMAT_BOARDS:
            return fmt
        return "team" if SettingsManager.Get(
            "scoreboard.team_mode", False) else "singles"

    def ActiveBoard(self) -> int:
        """The board the SCORE panel is currently showing.

        The global hotkeys used to be connected by each board to itself, so
        "which board answers Ctrl+Shift+1" was decided by which boards
        happened to be built: none when the operator's saved format was crew,
        and *both* of them after a singles/team switch, silently scoring a
        hidden board. Resolved from the persisted format instead, which is
        the same answer /api/v1/health reports as `activeBoard`.
        """
        return self.FORMAT_BOARDS.get(self.ResolveBattleFormat(), 1)

    def _clampScore(self, value: int) -> int:
        return max(self.SCORE_MIN, min(self.SCORE_MAX, value))

    @staticmethod
    def _asInt(value, default=0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ── score ────────────────────────────────────────────────────────────

    def AdjustScore(self, board, team, delta):
        """Nudge one team's score by `delta`; returns the new value or None.

        `team` is 1-based, like the key path it writes -- the Qt side counts
        teams from 0 and converts at the call site.

        The read-modify-write is taken under StateManager.lock: two web
        clients pressing +1 at the same moment now both run here (before,
        both were funnelled through a queued Qt signal on the GUI thread),
        and without the lock one of the two increments would be lost.
        """
        board = self._asInt(board, 0)
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"AdjustScore: ignoring team {team}")
            return None
        if board not in self.SCORE_BOARDS:
            logger.debug(
                f"AdjustScore: board {board} has no team-level score "
                "(crew scores are summed from member stocks); ignoring")
            return None

        key = f"score.{board}.team.{team}.score"
        with StateManager.lock:
            value = self._clampScore(
                self._asInt(StateManager.Get(key, 0)) + self._asInt(delta))
            # No SaveBlock: a single write is already a single save cycle,
            # so a block would only add a pair of counter operations. (It
            # used to be worse than pointless -- an empty changed-key list
            # made DeepDiff run unfiltered, i.e. over the whole state, on
            # every key press. SaveState skips that cycle outright since
            # Step 4a.)
            StateManager.Set(key, value)
        return value

    def ResetScore(self, board):
        """Zero both teams' scores in one save cycle. True if it applied.

        The one operation here that writes more than a single key, so the one
        that needs the SaveBlock: driving the two spinboxes in turn (as the
        old path did) notified twice, and on an unheld board would have shown
        a half-reset scoreboard for one broadcast.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            # Crew "reset" means restoring every member's stocks, which is
            # widget-owned state (see the module docstring); the caller keeps
            # routing board 3 to the widget.
            logger.debug(f"ResetScore: board {board} has no team-level score")
            return False
        with StateManager.SaveBlock():
            for team in (1, 2):
                StateManager.Set(
                    f"score.{board}.team.{team}.score", self.SCORE_MIN)
        return True

    # ── team color ───────────────────────────────────────────────────────

    def SetTeamColor(self, board, team, color):
        """Set a team's color (a "#rrggbb" string). True if it applied.

        A falsy color is ignored rather than written, matching what the
        widget command did: the color is used by overlays as a fill, and
        clearing it would render a transparent team strip nobody asked for.
        Resolving a game's preset color index to a hex string stays on the Qt
        side -- that lookup reads AutonaGameAssetManager's color model.
        """
        board = self._asInt(board, 0)
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"SetTeamColor: ignoring team {team}")
            return False
        if not isinstance(color, str) or not color:
            return False
        # One key, so no SaveBlock -- see AdjustScore.
        StateManager.Set(f"score.{board}.team.{team}.color", color)
        return True

    # ── display options ──────────────────────────────────────────────────

    def SetDisplayOption(self, key, value):
        """Toggle one scoreboard field's visibility. True if it applied.

        Both halves of the mirror are written here, because both are what the
        toggle means: `SettingsManager.display_options.{key}` is the persisted
        local default a board re-reads when it is built, and
        `StateManager.display_options.{key}` is what the Web UI eye menu and
        the OBS overlays render from. This subtree is not held, so the write
        publishes immediately -- unlike the score, it is not an operator edit
        waiting on 反映.
        """
        if key not in schema.DISPLAY_OPTION_KEYS:
            logger.warning(f"SetDisplayOption: unknown option {key}")
            return False
        value = bool(value)
        path = f"display_options.{key}"
        SettingsManager.Set(path, value)
        # One key, so no SaveBlock -- see AdjustScore.
        StateManager.Set(path, value)
        return True

    # ── 反映 (deferred publication) ───────────────────────────────────────

    def ApplyChanges(self, board):
        """Publish a board's pending held edits (the 反映 button / hotkey).

        Safe on a board that was never built or is not held at all (board 3):
        CommitHeld queues the prefix and the save cycle finds nothing pending.
        """
        StateManager.CommitHeld(f"score.{self._asInt(board, 0)}")
