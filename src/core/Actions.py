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
that are self-evident state edits; Step 5 added the SCORE panel's remaining
field writes (`SetMatchField` / `SetTeamName` / `SetTeamLosers` /
`SetTeamsSwapped` / `SetBestOf`), which is what took the last
`StateManager.Set` out of `AutonaScoreboardWidget`, and then the team roster
operations below (`ClearTeamRoster` / `TrimTeamRoster`) alongside the player
subtree's own writer, `core/PlayerService.py`. The final instalment added the
operations of the panels that are not a scoreboard at all -- `SetLayoutOption`,
`SetStreamUrl` / `SetStreamTitle`, `PublishCommentary` /
`PublishResultPlayers` -- which is what took the last `StateManager.Set` out
of a Qt widget (the ledger of what remains is in
docs/changes/headless-core-redesign.md). What is still outside:

- **Swap teams**, as an operation. The player half of a swap is a
  widget-to-widget copy of *field values*
  (`AutonaBasePlayerWidget.SwapWith`), and it stays that way for as long as
  those fields live in the widgets: the player subtree is written from Qt
  now rather than by Qt, but the state is still the output, not the source
  of truth, so there is nothing in core to exchange. Inverting that needs a
  render side for the player card (a `_SyncPlayerFields` to match the
  board's `_SyncSetFields`), which is a bigger change than the write-path
  move was. The board's own `teamsSwapped` attribute likewise remains the
  authority that `SetLoadService` and `WebServerActions.get_swap` read --
  `SetTeamsSwapped` below only publishes the mirror.
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
- **`best_of` as an inversion.** `SetBestOf` owns the six derived keys, but
  the board's BEST OF spinbox is still the *entry point*: its `valueChanged`
  calls here and then resizes the stage rows, in one save cycle. Making the
  state lead instead would put `GameTrackerService.SetStageCount` (which
  writes) inside the notification for the write that triggered it, i.e. a
  second held cycle per BO change and a re-entrant one. Left as it is
  deliberately.

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
import math
from types import MappingProxyType

from loguru import logger

from . import StateSchema as schema
from .LayoutOptionsManager import LayoutOptionsManager
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

    # Fallback for the two localized round texts below when nothing injected
    # a formatter -- i.e. a headless run. Verbatim from the shipped English
    # table (src/i18n/tournament_term/en.json), so a headless publish has the
    # same shape as a GUI one instead of a blank key.
    DEFAULT_MATCH_TEXTS = MappingProxyType({
        "best_of": "Best of {0}",
        "first_to": "First to {0}",
    })

    def __init__(self, formatMatchText=None):
        # `formatMatchText(key, number) -> str` produces the localized
        # "Best of 5" / "First to 3" strings. Injected rather than imported
        # for the same reason TournamentInfoService takes its date format:
        # the tables live on AutonaLocaleHelper, which imports Qt, and core
        # may not (tests/test_core_qt_free.py). bootstrap_core() passes
        # AutonaLocaleHelper.FormatMatchText.
        self._formatMatchText = formatMatchText

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

    @staticmethod
    def _SetIfChanged(key, value) -> bool:
        """Write `key` only when it would actually change. True if written.

        Same rule, and the same reason, as `SetLoadService._SetIfChanged`
        (Step 4b): every write under a board's held prefix marks it dirty and
        lights the 反映 button, so re-writing a value the state already holds
        would announce an edit the operator did not make. The Qt path used to
        get this for free -- `setChecked` / `setValue` / `setCurrentText` emit
        nothing when the value is unchanged -- and the field writes below are
        reached from paths that deliberately re-assert values (ClearScore,
        ResetAllInputs, a re-loaded set), so it has to be explicit here.

        Board 3 (crew) has none of that -- score.3 is not held -- but calls
        into `SetMatchField` / `SetTeamName` land here too, and for that board
        the same guard earns a different thing: an unchanged write still
        costs a full save cycle (an ./out rewrite plus an export thread), so
        skipping it avoids a wasted cycle rather than a false-dirty held
        prefix. `CrewBattleService._SetIfChanged` states this reading
        directly; this docstring only had the board 1/2 one.
        """
        if StateManager.Get(key, None) == value:
            return False
        StateManager.Set(key, value)
        return True

    def _matchText(self, key, number) -> str:
        if self._formatMatchText is not None:
            return self._formatMatchText(key, number)
        return self.DEFAULT_MATCH_TEXTS[key].format(number)

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

    def SetScore(self, board, team, value):
        """Set one team's score; returns the resulting score, or None.

        None means the call was ignored (a team other than 1/2, or a board
        without a team-level score). Otherwise the return value is `value`
        clamped to SCORE_MIN..SCORE_MAX -- the score the team now has,
        whether or not the state had to be written to get there.

        The absolute counterpart of `AdjustScore`, with the same addressing:
        `team` is the *state* team (the column the operator sees, already
        resolved for a swap), not a set's team. That is what separates it
        from the set-load writer, `SetLoadService.ApplyScores`, which the
        legacy POST /score reaches -- that one runs the team through
        `DisplayTeam()`, honours `general.disable_scoreupdate` (a provider
        setting) and rides along with the rest of a set payload. A remote
        client typing a score is none of those things.

        Written only when it changes (`_SetIfChanged`): re-asserting the
        value the state already holds would light the 反映 button for an
        edit nobody made. The compare-and-write is taken under
        StateManager.lock, as `AdjustScore`'s read-modify-write is, so it
        cannot interleave with a concurrent +1. Wrap two calls in a SaveBlock
        to set both teams in one save cycle.
        """
        board = self._asInt(board, 0)
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"SetScore: ignoring team {team}")
            return None
        if board not in self.SCORE_BOARDS:
            logger.debug(
                f"SetScore: board {board} has no team-level score "
                "(crew scores are summed from member stocks); ignoring")
            return None
        value = self._clampScore(self._asInt(value))
        with StateManager.lock:
            self._SetIfChanged(f"score.{board}.team.{team}.score", value)
        return value

    # ── team color ───────────────────────────────────────────────────────

    def SetTeamColor(self, board, team, color):
        """Set a team's color (a "#rrggbb" string). True if it applied.

        A falsy color is ignored rather than written, matching what the
        widget command did: the color is used by overlays as a fill, and
        clearing it would render a transparent team strip nobody asked for.
        Resolving a game's preset color index to a hex string stays on the Qt
        side -- that lookup reads AutonaGameAssetManager's color model.

        `board` is not validated (unlike `AdjustScore` / `ResetScore`'s
        `SCORE_BOARDS` check): this, `SetTeamName` and `SetMatchField` are
        the entry points a crew (board 3) uses too, so an out-of-range board
        would have to mean a caller bug rather than "not this board's turn",
        and every caller is a widget that already knows which board it is.
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

    # ── team card fields (name / losers flag) ────────────────────────────
    #
    # Both keys were written by a `findChildren` loop that used each widget's
    # objectName as the final key segment, which is what made the .ui file
    # the schema (S-14). The names are `StateSchema.SCORE_TEAM_INPUT_KEYS`
    # now and these are the operations behind them.
    #
    # `team` is the *display* team, i.e. the column the operator is looking
    # at, already resolved for a swap. `SetLoadService.ApplyTeamName` /
    # `ApplyLosers` are the set-load counterparts: they take the *set's*
    # team and run it through `DisplayTeam()` first, and the name one also
    # re-resolves the team crest. Two writers per key, coordinating through
    # the state exactly as the three score writers do (see the module
    # docstring); folding them together needs the swap flag itself to live in
    # core, which is what the swap operation is still waiting on.

    def SetTeamName(self, board, team, name) -> bool:
        """Set one team's displayed name. True if the state changed.

        The team crest that goes with it is *not* written here: on the
        desktop the QLineEdit's second handler
        (`AutonaScoreboardWidget.ExportTeamLogo` ->
        `SetLoadService.PublishTeamLogo`) does that, which is the arrangement
        this replaced and the one the set-load path pairs into
        `ApplyTeamName`. That second handler exists only on boards 1/2 --
        board 3's team name field has always called just this one, and a
        crew has no logo to resolve.
        """
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"SetTeamName: ignoring team {team}")
            return False
        text = "" if name is None else str(name)
        return self._SetIfChanged(
            f"score.{self._asInt(board, 0)}.team.{team}."
            f"{schema.TEAM_NAME_KEY}", text)

    def SetTeamLosers(self, board, team, value) -> bool:
        """Flag one team as coming from the losers bracket. True if changed.

        Overlays render the flag, and the desktop additionally suffixes
        " [L]" onto each player's `mergedName`. That second half is the
        checkbox's *other* handler (`AutonaScoreboardPlayerWidget.SetLosers`,
        connected per player) and is deliberately not done here: the suffix
        is part of a *name*, and which name a slot currently holds is the
        widget's -- `PlayerService.PublishMergedName` is handed both halves
        by the card that knows them.
        """
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"SetTeamLosers: ignoring team {team}")
            return False
        return self._SetIfChanged(
            f"score.{self._asInt(board, 0)}.team.{team}."
            f"{schema.TEAM_LOSERS_KEY}", bool(value))

    def SetTeamsSwapped(self, board, swapped):
        """Publish which way round a board's two sides currently are.

        A mirror, not the authority: the swap itself is still
        `AutonaScoreboardWidget.SwapTeams`, whose `teamsSwapped` attribute is
        what `SetLoadService` and `WebServerActions.get_swap` read. Written
        unconditionally, because the one caller has just toggled it.
        """
        StateManager.Set(
            f"score.{self._asInt(board, 0)}.teamsSwapped", bool(swapped))

    # ── the team roster (how many player slots a team has) ────────────────
    #
    # The player slots themselves are `PlayerService`'s (one instance per
    # player widget); what belongs here is the *count* -- an operation on the
    # board, reached from the PLAYERS-per-team spinbox and from a set load
    # whose entrants are more numerous than the current roster. Both used to
    # be bare `StateManager.Unset` calls inside
    # `AutonaScoreboardWidget._setPlayersPerTeamImpl`.

    def ClearTeamRoster(self, board, team) -> bool:
        """Drop every player slot of one team. True if it applied.

        Run once per team while a board is built, before the first player
        widget is created: a restored program_state.json can carry a roster
        from a session that ran with more players per team, and nothing else
        would ever remove the surplus.
        """
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"ClearTeamRoster: ignoring team {team}")
            return False
        StateManager.Unset(
            f"score.{self._asInt(board, 0)}.team.{team}.player")
        return True

    def TrimTeamRoster(self, board, team, count) -> list:
        """Drop the player slots numbered above `count`; returns their keys.

        The caller has already torn the surplus *widgets* down (each one
        unsets its own slot through `PlayerService.Unset`); this is the pass
        that also catches slots the state carries without a widget -- a
        restored roster, or a board that shrank while it was not built.

        Null-safe where the loop it replaced was not: that one read
        `team.{t}.player` as a dict whenever `team.{t}` was truthy at all, so
        a team with a name but no roster would have raised inside a save
        block.
        """
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"TrimTeamRoster: ignoring team {team}")
            return []
        prefix = f"score.{self._asInt(board, 0)}.team.{team}.player"
        players = StateManager.Get(prefix)
        if not isinstance(players, dict):
            return []
        count = self._asInt(count, 0)
        removed = []
        with StateManager.SaveBlock():
            for slot in list(players.keys()):
                index = self._asInt(slot, 0)
                if index > count:
                    StateManager.Unset(f"{prefix}.{slot}")
                    removed.append(slot)
        return removed

    # ── the match bar (round name / phase / pool) ─────────────────────────

    def SetMatchField(self, board, field, value) -> bool:
        """Set one of the match bar's three fields. True if it applied.

        `field` is one of `StateSchema.SCORE_MATCH_FIELDS`, which is the
        board-level half of what used to be objectName-derived. Boards 1/2's
        set-loading path writes the same keys through
        `SetLoadService.ApplyMatchInfo`; board 3's does through
        `CrewBattleService.ApplyMatchInfo`. Both additionally decide
        *whether* a payload's blank value means "clear" or "not carried" --
        a question an operator typing into the combo does not have -- and the
        two disagree with each other on that reading (see the module
        docstrings), which is exactly why they stayed separate rather than
        folding into this method.
        """
        if field not in schema.SCORE_MATCH_FIELDS:
            logger.warning(f"SetMatchField: unknown field {field}")
            return False
        text = "" if value is None else str(value)
        return self._SetIfChanged(
            f"score.{self._asInt(board, 0)}.{field}", text)

    def ClearMatchInfo(self, board) -> bool:
        """Blank the match bar (round name / phase / pool). True if changed.

        The match-bar half of an operator's *reset* -- the すべての入力値を
        リセット button, `reset_match` and `clear_all` (S-17). It is NOT part
        of the clear a set load runs first (`ClearScore`): SetLoadService's
        `ApplyMatchInfo` keeps the round name for a payload that carries
        none, so clearing ahead of it would blank the round mid-stream for
        exactly those payloads.

        `match_en` goes with it: it is the English form of the same round
        name (the YouTube title reads it), and leaving it would put the
        previous set's round into the next title. Only where a set load has
        written one, though -- board 3 never carries the key
        (CrewBattleService does not publish it), and a reset must not add it.

        One save cycle for all of it, and nothing written for a field that is
        already blank (see `_SetIfChanged`).
        """
        prefix = f"score.{self._asInt(board, 0)}"
        written = False
        with StateManager.SaveBlock():
            for field in schema.SCORE_MATCH_FIELDS:
                written |= self._SetIfChanged(f"{prefix}.{field}", "")
            if StateManager.Get(f"{prefix}.match_en", None) is not None:
                written |= self._SetIfChanged(f"{prefix}.match_en", "")
        return written

    # ── best of ──────────────────────────────────────────────────────────

    def SetBestOf(self, board, value):
        """Publish a set length and everything derived from it.

        Six keys, one save cycle, because they are one fact: the raw
        `best_of`, the short forms overlays use in tight spaces (`BO5` /
        `FT3`), and the localized long forms. 0 means "not set", and the long
        forms are then blank rather than "Best of 0".

        The caller is expected to resize the individual game tracker's stage
        rows in the same cycle -- `GameTrackerService.SetStageCount()` is the
        only thing allowed to, and `AutonaScoreboardWidget._onBestOfChanged`
        wraps both in one `SaveBlock`. See the module docstring for why the
        spinbox is still the entry point.

        Raises whatever the injected formatter raises (the Qt one throws when
        the locale tables have not been loaded yet), inside the block rather
        than around it, so a failure cannot leave `saveBlocked` positive --
        which would freeze ./out and the WebSocket feed for the rest of the
        session (agent-pitfalls #6).
        """
        board = self._asInt(board, 0)
        value = self._asInt(value, 0)
        firstTo = math.ceil(value / 2)
        prefix = f"score.{board}"
        with StateManager.SaveBlock():
            self._SetIfChanged(f"{prefix}.best_of", value)
            self._SetIfChanged(f"{prefix}.best_of_short_text", f"BO{value}")
            self._SetIfChanged(
                f"{prefix}.best_of_text",
                self._matchText("best_of", value) if value > 0 else "")
            self._SetIfChanged(f"{prefix}.first_to", firstTo)
            self._SetIfChanged(f"{prefix}.first_to_short_text", f"FT{firstTo}")
            self._SetIfChanged(
                f"{prefix}.first_to_text",
                self._matchText("first_to", firstTo) if value > 0 else "")

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
        path = f"{schema.DISPLAY_OPTIONS_ROOT}.{key}"
        SettingsManager.Set(path, value)
        # One key, so no SaveBlock -- see AdjustScore.
        StateManager.Set(path, value)
        return True

    # ── layout options (per-overlay settings) ─────────────────────────────

    def SetLayoutOption(self, base, setting, value):
        """Persist and republish one OBS layout's option.

        The same two-sided mirror as SetDisplayOption above, over a different
        pair of files: `LayoutOptionsManager` (./user_data/layout_options.json)
        is what the LAYOUT panel re-reads when it rebuilds its controls, and
        `layout_options.{base}.{setting}` is what the overlay renders from.
        `base` and `setting` come from the layout's own options definition,
        not from a Qt objectName, so there is no catalog of them here --
        KEY_CATALOG records the root as open-ended for that reason.

        Not guarded by _SetIfChanged: building the LAYOUT panel assigns every
        control its stored value, and those assignments emit, which is how a
        layout's options reach the state at all on a fresh install. A guard
        would keep the settings file and the state from agreeing after a
        manual edit of program_state.json.
        """
        path = f"{base}.{setting}"
        LayoutOptionsManager.Set(path, value)
        # One state key, so no SaveBlock -- see AdjustScore.
        StateManager.Set(f"{schema.LAYOUT_OPTIONS_ROOT}.{path}", value)

    # ── stream (配信設定) ─────────────────────────────────────────────────

    def SetStreamUrl(self, url) -> bool:
        """Publish the stream destination URL. True if it changed.

        `stream.*` is not held, so this reaches the overlays immediately.
        """
        return self._SetIfChanged(
            f"{schema.STREAM_ROOT}.{schema.STREAM_URL_KEY}", url or "")

    def SetStreamTitle(self, title) -> bool:
        """Publish the broadcast title. True if it changed.

        Reached both from the operator's edit and from the auto-refresh that
        rebuilds the title out of the tournament name -- the latter runs on
        every tournament / entrant / game-asset change, and usually produces
        the string that is already there.
        """
        return self._SetIfChanged(
            f"{schema.STREAM_ROOT}.{schema.STREAM_TITLE_KEY}", title or "")

    # ── whole-subtree publishes (commentary / result rosters) ─────────────
    #
    # Both panels hold their data in Qt inputs and republish the whole subtree
    # on every edit, which is the shape of the operation rather than an
    # accident: an overlay reads one dict of slots, and a partial write would
    # leave it rendering a mix of two edits. What moved here is the key and
    # the fact that the write happens at all; assembling the dict stays with
    # the panel, because the values come out of combos whose rows are Qt
    # models (the same line PlayerService.SetData draws).

    def PublishCommentary(self, slots):
        """Publish the caster line-up (`commentary.{slot}.*`)."""
        StateManager.Set(schema.COMMENTARY_ROOT, slots)

    def PublishResultPlayers(self, players):
        """Publish the RESULT panel's placement list (`result_players.*`).

        One key for both layouts: the panel writes either the singles entries
        or the team entries, whichever its current layout uses.
        """
        StateManager.Set(schema.RESULT_PLAYERS_ROOT, players)

    # ── 反映 (deferred publication) ───────────────────────────────────────

    def ApplyChanges(self, board):
        """Publish a board's pending held edits (the 反映 button / hotkey).

        Safe on a board that was never built or is not held at all (board 3):
        CommitHeld queues the prefix and the save cycle finds nothing pending.
        """
        StateManager.CommitHeld(f"score.{self._asInt(board, 0)}")
