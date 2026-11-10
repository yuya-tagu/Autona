"""Domain operations: where a write to the state originates.

Until now the answer to "who changes the score?" was "whichever Qt widget
owns the spinbox". The web server's actions class (deleted in ADR-005 S3e-9)
looked like a write hub but never
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
`SetBestOf`), which is what took the last
`StateManager.Set` out of `AutonaScoreboardWidget`, and then the team roster
operations below (`ClearTeamRoster` / `TrimTeamRoster`; the count itself,
`SetRoster`, since ADR-005 S3e-6) alongside the player
subtree's own writer, `core/PlayerService.py`. The final instalment added the
operations of the panels that are not a scoreboard at all -- `SetLayoutOption`
(gone with its panel and its store, ADR-005 S4s: nothing called it, and no
overlay read what it wrote), `SetStreamUrl` / `SetStreamTitle`, `PublishCommentary` (moved to
core/CommentaryService.py by ADR-005 S3e-2) / `PublishResultPlayers` (gone with
the Qt panel, ADR-005 S4r: core/ResultService.py writes the rows) -- which is
what took the last `StateManager.Set` out of a Qt widget (the ledger of what
remains is in
docs/changes/headless-core-redesign.md). ADR-005 S2c then moved the swap
(`SwapTeams` / `SwapPlayers`): since S2a the player cards render their slot
from the state, so a swap exchanges the slots here instead of copying field
values from one card into another, and `score.{n}.teamsSwapped` became the
flag itself rather than the mirror of a board attribute. S2d moved the
clears (`ClearScore` / `ResetMatch` / `ClearPlayers` / `ClearBoard`), which
used to drive the board's widgets back to empty -- the score spinboxes, the
losers boxes, every player card's Clear() -- a save cycle per widget. S2e
made the set length the state's (`SetBestOf`): it was the BEST OF spinbox's,
whose valueChanged published it and rebuilt the game tracker's rows, and
every other entry point drove that spinbox. What is still outside:

- **The other writers of the score.** `AdjustScore` / `ResetScore`
  below are not the *only* writers of `score.{n}.team.{t}.score`. Three
  writers live inside core -- this class (`SetScore` / `AdjustScore` /
  `ResetScore`, `SwapTeams` since S2c and the clears since S2d), the stage
  tally in `GameTrackerService.PublishStageResults`, and a loaded set's
  `SetLoadService.ApplyScores` (Step 4b). A number typed into the score
  display is `SetScore` since ADR-005 S4x, as v1's score:set is (it was the
  tracker's own `UpdateScore` until then), so no entry point is outside.
  The game rows follow the score in the operation that moved it
  (`GameTrackerService.FollowScores`, ADR-005 S4t), and a board only renders
  the result (`AutonaScoreboardWidget._SyncScoreDisplays`): the follow used
  to be that method's, with the old value on its spinbox and the tracker's
  own writes told apart by a flag. Nothing outside core re-Sets the score:
  `StageResultsToScore` used to echo the published value back and no longer
  does.

(`best_of` used to be listed here as an inversion left on purpose: making
the state lead would have put `GameTrackerService.SetStageCount`, which
writes, inside the notification for the write that triggered it -- a second,
re-entrant held cycle per change. S2e resolved it the other way round: the
rows are resized *here*, in the same block as the set length, through the
board's tracker (`GameTracker`), and the board only renders the result. The
notification writes nothing.)

A fresh board's launch write is here too (`SeedFreshBoard`, ADR-005 S4t): the
board's widgets used to write it as they were built. So is making a board
ready to be used (`EnsureBoard`, S4y: the launch write, and on the desktop the
反映 hold the board widget's constructor registered), which is what a board
host's Built() answers -- every board at launch since S4ac-1 (`MakeBoards`).

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
from .CrewBattleService import CrewBattleService
from .DictHelper import deep_clone
from .GameTrackerService import GameTrackerService
from .GameAssetCatalog import GameAssetCatalog
from .Observable import ObserverChannel
from .PlayerService import (
    DEFAULT_CHARACTERS_PER_PLAYER, CharacterSlot, DefaultPlayersPerTeam,
    PlayerService, RepublishTeamMergedNames)
from .SettingsManager import SettingsManager
from .StateManager import StateManager
from .StatsService import StatsService

# "This dict has no such key", for the key-by-key exchange of SwapTeams /
# SwapPlayers (None is a value there: no city, no avatar).
_MISSING = object()

# The player slot keys a swap writes in a fixed order -- where a key is new
# to a slot, this is the order the old widget-to-widget copy appended it in
# (its line edits in .ui order with the merged names behind the tag, the
# note, then the id and the avatar it restored afterwards, then the seed and
# the city it set last). The slot's other keys follow in their own order.
SLOT_ORDER = (
    schema.PLAYER_SPONSOR_KEY, schema.PLAYER_TAG_KEY,
    "mergedName", "mergedOnlyName", "avatar",
    schema.PLAYER_REAL_NAME_KEY, schema.PLAYER_TWITTER_KEY,
    schema.PLAYER_NOTE_KEY, "id", "online_avatar", schema.PLAYER_SEED_KEY,
    "city")
# What a slot holds for a key it does not have: what the copy published for
# an empty field (an unseeded player is seed 0). The other keys (birthday,
# country, state, character, mains, the sponsor crests) move by presence.
SLOT_DEFAULTS = MappingProxyType(
    {key: "" for key in schema.PLAYER_TEXT_KEYS}
    | {"mergedName": "", "mergedOnlyName": "", schema.PLAYER_SEED_KEY: 0,
       "id": None, "city": None, "online_avatar": None, "avatar": None})
# Re-published on both cards by the copy whatever they held (every text
# field, the names derived from them, the two avatar keys and the seed), so
# both slots carry them after a swap even when neither had them before.
SLOT_ALWAYS = frozenset(
    schema.PLAYER_TEXT_KEYS
    + ("mergedName", "mergedOnlyName", "online_avatar", "avatar",
       schema.PLAYER_SEED_KEY))
# Written in the type the copy wrote: the seed went through
# PlayerService.SetSeed, which makes anything non-numeric 0.
SLOT_CONVERT = MappingProxyType({
    schema.PLAYER_SEED_KEY: lambda value: AutonaActions._asInt(value, 0)})
# Written by the copy only when the value changed, with a missing key read
# as None (PlayerService.SetCity / SetPlayerId keep that guard), so a None
# never creates one of these keys on a slot that lacks it.
SLOT_NONE_ABSENT = frozenset(("id", "city"))

# The team keys that cross over with the players (not `color`: the colors
# belong to the sides), in the order the old swap wrote them.
TEAM_SWAP_ORDER = ("score", schema.TEAM_LOSERS_KEY, schema.TEAM_NAME_KEY,
                   "logo")
TEAM_DEFAULTS = MappingProxyType({
    "score": 0, schema.TEAM_LOSERS_KEY: False, schema.TEAM_NAME_KEY: "",
    "logo": None})
# The team name was re-set on both teams whatever it held.
TEAM_ALWAYS = frozenset((schema.TEAM_NAME_KEY,))


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
    # Every board a shell has (EnsureBoard).
    BOARDS = SCORE_BOARDS + (CREW_BOARD,)

    # A fresh board's team colours when the settings name none
    # (general.team_{t}_default_color; the settings window's defaults).
    DEFAULT_TEAM_COLORS = MappingProxyType({1: "#fe3636", 2: "#2e89ff"})

    # The score spinboxes are plain QSpinBoxes with Qt's default range, and
    # that range has always been the only guard against a negative or absurd
    # score reaching an overlay. Now that the write no longer goes through
    # the widget, the clamp has to live with the operation.
    # AutonaScoreboardWidget applies these to the spinboxes so the two cannot
    # drift apart.
    SCORE_MIN = 0
    SCORE_MAX = 99
    # The same for the set length: the BEST OF spinbox's range (Qt's default
    # 0..99) was what clamped a Web or set-load value before ADR-005 S2e.
    BEST_OF_MIN = 0
    BEST_OF_MAX = 99

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
        # {board: GameTrackerService}: the game rows of each 1v1 / team board
        # (GameTracker). One per board for the life of the app, whichever
        # shell it is and whether or not a widget renders them (ADR-005 S4t).
        self._gameTrackers = {
            board: GameTrackerService(board) for board in self.SCORE_BOARDS}
        # {board: count}: the characters per player a 1v1 / team board with
        # no character slot gives the players it gets next -- asked for while
        # it had none, else what it had when it lost its last player
        # (SetPlayersPerTeam / SetCharactersPerPlayer). Not in the state, as
        # it never was. Guarded by StateManager.lock.
        self._emptyCharacters = {}
        # The boards EnsureBoard made ready (their launch write, and on the
        # desktop the 反映 hold): what a board host's Built() answers. Not in
        # the state. Guarded by StateManager.lock.
        self._ensuredBoards = set()
        # The boards an EnsureBoard is making right now (not ready yet): a
        # second call returns at once instead of making them again. Guarded
        # by StateManager.lock.
        self._makingBoards = set()

    # ── the battle format ────────────────────────────────────────────────

    # The battle format was switched (SetBattleFormat): (fmt). Notified on the
    # thread that switched it, after the settings are written and before the
    # result's layout follows -- the order the window's switch had. The
    # desktop shows the format's board and the sidebar segment for it
    # (StateManagerBridge.battle_format_changed -> Window), and in both shells
    # the web server tells its clients (`battle_format`, ADR-005 S4z).
    # Class-level, as SetFollowService.changes: the bridge subscribes once
    # for the process.
    battleFormatChanges = ObserverChannel("battle_format_changes")

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

    def SetBattleFormat(self, fmt) -> bool:
        """Switch the battle format: "singles" (board 1), "team" (board 2) or
        "crew" (board 3); a bool is team / singles, as the window's switch
        took one. True if it switched; False for the format already in force
        (ResolveBattleFormat), which writes and notifies nothing. Raises
        ValueError for anything else, before anything is written.

        Persists `scoreboard.battle_format` (the source of truth) and mirrors
        the legacy `scoreboard.team_mode` bool, so rolling back to an older
        build still restores 1v1 / team. Then tells the shell
        (battleFormatChanges: the desktop shows the format's board -- built
        first if it is not -- and the sidebar segment), and the result's
        layout follows (ResultService.FollowBattleFormat), in that order.

        The window's SetBattleFormat until ADR-005 S4x, which the sidebar
        drove; the sidebar and v1's PUT /battle-format call this now, so the
        switch works headless too. Re-choosing the format in force switched
        the board again and re-ran the result's layout rule there; it is a
        no-op now (the board already is the one shown, and the layout rule
        ran when that format was chosen).
        """
        if isinstance(fmt, bool):
            fmt = "team" if fmt else "singles"
        # isinstance first: `in` on a mapping hashes its argument.
        if not isinstance(fmt, str) or fmt not in self.FORMAT_BOARDS:
            raise ValueError(f"unknown battle format: {fmt!r}")
        if fmt == self.ResolveBattleFormat():
            return False
        SettingsManager.Set("scoreboard.battle_format", fmt)
        SettingsManager.Set("scoreboard.team_mode", fmt == "team")
        logger.info(f"Battle format: {fmt}")
        self.battleFormatChanges.notify(fmt)
        # Imported here: ResultService imports this module.
        from .ResultService import ResultService
        if ResultService.instance is not None:
            ResultService.instance.FollowBattleFormat(fmt)
        return True

    # ── helpers ──────────────────────────────────────────────────────────

    def _clampScore(self, value: int) -> int:
        return max(self.SCORE_MIN, min(self.SCORE_MAX, value))

    @staticmethod
    def _asInt(value, default=0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # Write `key` only when it would actually change; True if written. The
    # shared rule is `StateManager.SetIfChanged` (one definition for every
    # core writer). Why it matters here: every write under a board's held
    # prefix marks it dirty and lights the 反映 button, and the field writes
    # below are reached from paths that deliberately re-assert values
    # (the clears, a re-loaded set) -- the Qt path got the
    # guard for free from `setChecked` / `setValue` / `setCurrentText`.
    # Board 3 (crew) is not held, but `SetMatchField` / `SetTeamName` land
    # here for it too, and there the guard saves a wasted save cycle (an
    # ./out rewrite plus an export thread) rather than a false-dirty prefix.
    _SetIfChanged = staticmethod(StateManager.SetIfChanged)

    def _matchText(self, key, number) -> str:
        """The localized set-length text, or the English default.

        The default stands in for a formatter that raises, too -- the Qt one
        does while the locale tables are not loaded (an empty matchNames),
        and a custom term file can carry a template .format() rejects. Until
        ADR-005 S2e that raised inside the spinbox's slot, where PySide6
        reported it and the caller carried on; SetBestOf is called directly
        now (a set load, the Web's set_route, a game load), and an exception
        here would abandon the rest of the caller's work -- the set's losers
        and players, the HTTP answer, the game load's commit.
        """
        if self._formatMatchText is not None:
            try:
                return self._formatMatchText(key, number)
            except Exception as error:
                logger.warning(
                    f"Match text {key!r} for {number} failed ({error!r}); "
                    "using the English default")
        return self.DEFAULT_MATCH_TEXTS[key].format(number)

    # ── a board's life ───────────────────────────────────────────────────

    def EnsureBoard(self, board, panel=False) -> bool:
        """Make board `board` (1/2/3) ready to be used, once: its launch
        write (SeedFreshBoard), and with `panel` what the desktop's SCORE
        panel adds to a 1v1 / team board -- the display options brought
        back to the settings (StateSchema.PublishDisplayOptions), the
        polling turned off (SetFollowService.TurnOffPolling) and, last, the
        board put behind its 反映 button (StateSchema.HoldBoard). One save
        cycle. True if this call did it; False when the board was ready
        already, or there is no such board (nothing is written then).

        Both shells make all three at launch, before anything else writes a
        board (MakeBoards, ADR-005 S4ac-1): the desktop with `panel`
        (bootstrap_gui()), the headless shell without
        (src/headless._SeedBoards): it holds nothing (it has no 反映 button),
        and the polling setting and the display options it restored are the
        operator's. The desktop's widgets are built later, and only render
        what they find (AutonaScoreboardWidget); their build still calls
        this (AutonaScoreboardManager.EnsureScoreboard), which does nothing
        for a board made at launch. Until S4ac-1 the desktop made a board as
        it first built its widgets; until ADR-005 S4y the three were the
        board build's and the hold the board widget's constructor's, after
        the seed, in this order: the save cycle and what it publishes are
        the same. A writer on another thread between the seed and the hold
        is published, not held.

        What a board host's Built() answers (BoardEnsured): the desktop's
        widgets are drawn after this, by the shell. Board 3 (crew) is never
        held, with or without `panel` (it publishes as it goes); neither
        are its display options rewritten nor the polling turned off, as
        its build never did.

        Called where the shell makes its boards (both launches, and the
        desktop's widget build: the GUI thread). The board counts as ready
        once it is: at the end of
        the save cycle's writes, under the lock that registers its hold, so
        a reader sees it ready and held together or neither. A second call
        while one is making it returns False at once, as for a ready board.
        When this raised the board is not ready and not held (ForgetBoard):
        the next call makes it again from its launch write.
        """
        board = self._asInt(board, 0)
        if board not in self.BOARDS:
            logger.debug(f"EnsureBoard: no board {board}")
            return False
        with StateManager.lock:
            if board in self._ensuredBoards or board in self._makingBoards:
                return False
            self._makingBoards.add(board)
        try:
            with StateManager.SaveBlock():
                self.SeedFreshBoard(board)
                held = panel and board in self.SCORE_BOARDS
                if held:
                    schema.PublishDisplayOptions()
                    # Imported here: SetFollowService imports this module.
                    from .SetFollowService import SetFollowService
                    SetFollowService.TurnOffPolling()
                with StateManager.lock:
                    if held:
                        # Last: a hold registered first would hold back the
                        # seed.
                        schema.HoldBoard(board)
                    self._ensuredBoards.add(board)
        except BaseException:
            # Raised by a write, or by the publish closing the block after
            # the hold was in: neither half may stay.
            self.ForgetBoard(board)
            raise
        finally:
            with StateManager.lock:
                self._makingBoards.discard(board)
        return True

    def ForgetBoard(self, board):
        """Undo EnsureBoard for a board that could not be made or drawn: it
        is no longer ready (BoardEnsured, a board host's Built()), and a
        1v1 / team board leaves its 反映 hold (StateSchema.ReleaseBoard),
        what was pending published -- a hold with no board to apply it would
        keep its edits from the outputs. The state is left as it is; the
        next EnsureBoard writes the launch state again.

        Called by EnsureBoard when it raised, and by the desktop when the
        board's widgets could not be built right after the call that made it
        (AutonaScoreboardManager.EnsureScoreboard): a board made in core
        with no widget would answer Built() with nothing to render it, and
        the swap / load-set hotkeys would act on it. Not for a board made
        before (at launch since ADR-005 S4ac-1): forgotten, the next build
        would seed it again over what was written to it since."""
        board = self._asInt(board, 0)
        with StateManager.lock:
            self._ensuredBoards.discard(board)
            if board in self.SCORE_BOARDS:
                schema.ReleaseBoard(board)

    def BoardEnsured(self, board) -> bool:
        """Whether EnsureBoard has made board `board` ready."""
        board = self._asInt(board, 0)
        with StateManager.lock:
            return board in self._ensuredBoards

    def ResetBoards(self):
        """Drop the whole `score` subtree (StateSchema.ResetScoreRoot): no
        board is ready after it. Once per launch, before any board is
        ensured -- the start of MakeBoards, in both shells (ADR-005 S4ac-1;
        the desktop's scoreboard dock's construction until then) -- so a
        program_state.json from an older build cannot leave boards behind.
        Nothing may call it after that: a board's widgets would be left
        rendering a subtree that is no longer there, and its hold would
        stay."""
        with StateManager.lock:
            self._ensuredBoards.clear()
        schema.ResetScoreRoot()

    def MakeBoards(self, panel=False):
        """Make every board a shell has (BOARDS), in one save cycle: the
        `score` subtree dropped first (ResetBoards), then each board made
        (EnsureBoard, with `panel`). Once per launch, before anything else
        writes a board, in both shells (ADR-005 S4ac-1): the desktop with
        `panel` (bootstrap_gui()), so boards 1 / 2 are behind their 反映
        button from launch -- whichever board the window shows, and with or
        without widgets -- and the headless shell without
        (src/headless._SeedBoards). The crew board (3) is never held, with
        or without `panel` (EnsureBoard). True if it made them.

        Once: while any board is made (BoardEnsured), it logs a warning and
        writes nothing, False. A second launch write would drop what was
        written to the boards since, and on a board already held its seed
        would be held back (ResetBoards does not release a hold). Decided by
        the boards, not by a call count, so it runs again once they are
        forgotten (a test's harness drops them between tests)."""
        made = [board for board in self.BOARDS if self.BoardEnsured(board)]
        if made:
            logger.warning(
                f"Actions - MakeBoards ignored: boards {made} are made "
                "already (once a launch)")
            return False
        with StateManager.SaveBlock():
            self.ResetBoards()
            for board in self.BOARDS:
                self.EnsureBoard(board, panel)
        return True

    def SeedFreshBoard(self, board) -> bool:
        """Write board `board`'s launch state, in one save cycle: the skeleton
        and everything a fresh board publishes. True if it applied.

        One function for both shells (ADR-005 S4t), run by EnsureBoard (S4y)
        for all three at launch (MakeBoards, S4ac-1; the desktop's when it
        first built a board's widgets until then): the desktop's widgets
        only render it.
        The board's widgets used to write most of this as they were built, by
        emitting their own signals, so the headless shell seeded only the
        player slots and a headless board had no colours, names, score or set
        length. tests/test_headless_seed.py pins the two against each other,
        whole.

        The order is that of the construction it replaced: it is the key
        order of a fresh program_state.json. `SeedBoard` replaces the
        subtree, so nothing of what it held is left to clear (a roster, a
        crest), and it must not run on a live board.

        The hold (`StateSchema.HoldBoard`) is not here, nor the desktop's
        polling off (SetFollowService.TurnOffPolling), which is the headless
        shell's to leave alone, nor its rewrite of the display options from
        the settings (StateSchema.PublishDisplayOptions): the headless shell
        keeps what it restored and fills in what is missing
        (StateSchema.SeedPanelState), and its own launches never rewrote
        them. All three are EnsureBoard's `panel`.
        """
        board = self._asInt(board, 0)
        if board == self.CREW_BOARD:
            with StateManager.SaveBlock():
                schema.SeedBoard(board)
                CrewBattleService(board).SeedDefaults()
            return True
        if board not in self.SCORE_BOARDS:
            logger.debug(f"SeedFreshBoard: no board {board}")
            return False
        with StateManager.SaveBlock():
            with StateManager.lock:
                schema.SeedBoard(board)
                for team in (1, 2):
                    self.SetTeamColor(board, team, SettingsManager.Get(
                        f"general.team_{team}_default_color",
                        self.DEFAULT_TEAM_COLORS[team]))
                    self.SetTeamName(board, team, "")
                    self.SetTeamLosers(board, team, False)
                # Coerced here, unlike a game load's (ApplyGameRoster): None
                # means "keep" to SetRoster, and a new board has nothing to
                # keep. A game whose players_per_team is null or not a number
                # would get no card at all.
                self.SetRoster(board, self._asInt(DefaultPlayersPerTeam(), 1),
                               DEFAULT_CHARACTERS_PER_PLAYER)
                for field in schema.SCORE_MATCH_FIELDS:
                    self.SetMatchField(board, field, "")
                self.SetBestOf(board, 0)
                for team in (1, 2):
                    self.SetScore(board, team, self.SCORE_MIN)
        return True

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
        tracker = self.GameTracker(board)
        # One save cycle with the game row it awards or clears (ADR-005 S4t;
        # the board's held cycle followed in a second one until then).
        with StateManager.SaveBlock():
            with StateManager.lock:
                before = tracker.Scores()
                value = self._clampScore(
                    self._asInt(StateManager.Get(key, 0))
                    + self._asInt(delta))
                StateManager.Set(key, value)
                tracker.FollowScores(before)
        return value

    def ResetScore(self, board):
        """Zero both teams' scores in one save cycle, with the game rows that
        follow them. True if it applied.

        Driving the two spinboxes in turn (as the old path did) notified
        twice, and on an unheld board would have shown a half-reset
        scoreboard for one broadcast.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            # Crew "reset" means restoring every member's stocks, which is
            # widget-owned state (see the module docstring); the caller keeps
            # routing board 3 to the widget.
            logger.debug(f"ResetScore: board {board} has no team-level score")
            return False
        tracker = self.GameTracker(board)
        with StateManager.SaveBlock():
            with StateManager.lock:
                before = tracker.Scores()
                for team in (1, 2):
                    StateManager.Set(
                        f"score.{board}.team.{team}.score", self.SCORE_MIN)
                tracker.FollowScores(before)
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
        tracker = self.GameTracker(board)
        with StateManager.SaveBlock():
            with StateManager.lock:
                before = tracker.Scores()
                self._SetIfChanged(f"score.{board}.team.{team}.score", value)
                tracker.FollowScores(before)
        return value

    # ── team color ───────────────────────────────────────────────────────

    def SetTeamColor(self, board, team, color):
        """Set a team's color (a "#rrggbb" string). True if it applied.

        A falsy color is ignored rather than written, matching what the
        widget command did: the color is used by overlays as a fill, and
        clearing it would render a transparent team strip nobody asked for.
        The color arrives as the hex string: nothing resolves a game's
        preset color index into one any more.

        `board` is not validated (unlike `AdjustScore` / `ResetScore`'s
        `SCORE_BOARDS` check): this, `SetTeamName` and `SetMatchField` are
        the entry points a crew (board 3) uses too. The callers are the crew
        board's color button, a fresh board's seed (SeedFreshBoard), v1's
        crew team color and the Web's legacy team_color (route and event).
        The last passes on whatever board it is given, and writes the color
        of a board no shell has (known, and answered "OK" as it always was;
        the ADR-005 S4x review left it).
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
    # the state exactly as the score writers do (see the module docstring).
    # The swap flag they both depend on is core's since ADR-005 S2c
    # (`score.{n}.teamsSwapped`, toggled by `SwapTeams`); folding the two
    # writers together was not part of that step.

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

        Overlays render the flag, and every player of the team carries it as
        a " [L]" suffix on `mergedName` / `mergedOnlyName` (the thumbnail
        generator reads it). Both halves are written here, in one save cycle
        (ADR-005 S2b): the names are rebuilt from the slots' own state by
        `PlayerService.RepublishTeamMergedNames`. They used to be the
        checkbox's second handler, one `SetLosers` per player card -- a save
        cycle each, from each card's copy of the flag. Only on a change, as
        that handler was: re-asserting the flag must not mark the board
        dirty. A loaded set writes the same pair through
        `SetLoadService.ApplyLosers`.
        """
        team = self._asInt(team, 0)
        if team not in (1, 2):
            logger.warning(f"SetTeamLosers: ignoring team {team}")
            return False
        board = self._asInt(board, 0)
        with StateManager.SaveBlock():
            changed = self._SetIfChanged(
                f"score.{board}.team.{team}.{schema.TEAM_LOSERS_KEY}",
                bool(value))
            if changed:
                RepublishTeamMergedNames(board, team)
        return changed

    # ── swapping sides / moving a player (ADR-005 S2c) ────────────────────
    #
    # Both used to be a widget-to-widget copy of field values
    # (`AutonaBasePlayerWidget.SwapWith`, now gone): each card's line edits
    # and combo indexes were read and typed into the other card, whose
    # handlers then published them. Since S2a the cards render their slot
    # from the state, so the slots are exchanged here, in the state, and the
    # cards follow.
    #
    # A slot is exchanged key by key over the union of both slots' keys --
    # not by putting one slot's dict in place of the other's. A key both
    # slots have keeps its place in each dict, so program_state.json and the
    # Socket.IO delta keep the key order the copy produced. What a slot gets
    # for a key only the other one has is `SLOT_DEFAULTS` (an empty text, no
    # city / id / avatar); a key with no default moves by presence (the
    # sponsor crests have always been either present or unset). The keys in
    # `SLOT_ALWAYS` were re-published on both cards by the copy whatever they
    # held, so a swap leaves them on both slots even when neither had them.
    # New keys are appended in `SLOT_ORDER`, the order the copy wrote them.
    #
    # The team swap exchanges the slots both teams have, as the copy did --
    # it paired the cards, and both teams always have as many. A slot only
    # one team has (a roster the state carries without a card) stays put.

    def SwapTeams(self, board) -> bool:
        """Exchange the two sides of a board in one save cycle. True if it
        applied; False for the crew board, which swaps itself.

        What crosses over: every player slot, the score, the losers flag,
        the team name and crest, and the game tracker's per-game winners
        (mirrored: a game team 1 won is team 2's after the swap). The team
        colors stay where they are -- they are the sides' colors, not the
        players'. `score.{n}.teamsSwapped` is toggled: it is what tells a set
        load which side a set's first entrant goes to
        (`SetLoadService.DisplayTeam`), so clearing a board never resets it.

        The names do not need re-deriving: a player's " [L]" comes from the
        flag of the team the name was published on, and that flag crosses
        over with the name.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(
                f"SwapTeams: board {board} is not a score board; the crew "
                "board swaps its own members")
            return False
        prefix = f"score.{board}"
        with StateManager.SaveBlock():
            with StateManager.lock:
                one = StateManager.Get(f"{prefix}.team.1.player")
                two = StateManager.Get(f"{prefix}.team.2.player")
                one = one if isinstance(one, dict) else {}
                two = two if isinstance(two, dict) else {}
                slots = [slot for slot in one if slot in two]
                for slot in sorted(slots, key=lambda s: self._asInt(s, 0)):
                    self._ExchangeSlots(
                        f"{prefix}.team.1.player.{slot}",
                        f"{prefix}.team.2.player.{slot}")
                self._UpdateUpsetFactor(board)
                self._ExchangeLeaves(
                    f"{prefix}.team.1", f"{prefix}.team.2", TEAM_SWAP_ORDER,
                    TEAM_DEFAULTS, TEAM_ALWAYS, listedOnly=True)
                self.GameTracker(board).SwapStageResults()
                StateManager.Set(f"{prefix}.teamsSwapped", not bool(
                    StateManager.Get(f"{prefix}.teamsSwapped", False)))
                # A slot pair holding two ids: both teams have other
                # players now.
                moved = any(
                    StateManager.Get(f"{prefix}.team.1.player.{slot}.id")
                    != StateManager.Get(f"{prefix}.team.2.player.{slot}.id")
                    for slot in slots)
        if moved:
            StatsService(board).TeamsChanged((1, 2))
        return True

    def SwapPlayers(self, board, team, first, second) -> bool:
        """Exchange two player slots of one team (the card's move up / move
        down). True if it applied.

        `first` / `second` are 1-based slot numbers. The same slot twice --
        the top card moved up, the bottom one down -- is not a move.
        """
        board = self._asInt(board, 0)
        team = self._asInt(team, 0)
        first = self._asInt(first, 0)
        second = self._asInt(second, 0)
        if team not in (1, 2):
            logger.warning(f"SwapPlayers: ignoring team {team}")
            return False
        if board not in self.SCORE_BOARDS:
            logger.debug(f"SwapPlayers: board {board} is not a score board")
            return False
        if first < 1 or second < 1 or first == second:
            return False
        prefix = f"score.{board}.team.{team}.player"
        with StateManager.SaveBlock():
            with StateManager.lock:
                self._ExchangeSlots(f"{prefix}.{first}", f"{prefix}.{second}")
                self._UpdateUpsetFactor(board)
                moved = (StateManager.Get(f"{prefix}.{first}.id")
                         != StateManager.Get(f"{prefix}.{second}.id"))
        if moved:
            StatsService(board).TeamsChanged((team,))
        return True

    def _ExchangeSlots(self, first, second):
        """Exchange two player slots. Caller holds the lock and the block."""
        a = StateManager.Get(first)
        b = StateManager.Get(second)
        if not isinstance(a, dict) and not isinstance(b, dict):
            return
        if not isinstance(a, dict) or not isinstance(b, dict):
            # Only one of the two slots exists. Reached from SwapPlayers
            # only -- SwapTeams passes just the slots both teams have and
            # leaves a slot only one team has where it is (see above): the
            # existing slot moves whole to the other position in its team.
            source, target, value = \
                (second, first, b) if isinstance(b, dict) else (first, second, a)
            StateManager.Set(target, value)
            StateManager.Unset(source)
            return
        self._ExchangeLeaves(first, second, SLOT_ORDER, SLOT_DEFAULTS,
                             SLOT_ALWAYS, noneAbsent=SLOT_NONE_ABSENT,
                             convert=SLOT_CONVERT)

    @staticmethod
    def _ExchangeLeaves(first, second, order, defaults, always,
                        listedOnly=False, noneAbsent=frozenset(),
                        convert=MappingProxyType({})):
        """Exchange the keys of two dicts in the state, one key at a time.

        `order` first (where a key is new to a dict, this is the order it is
        appended in), then -- unless `listedOnly` -- every other key either
        dict has. A key one side lacks takes `defaults[key]` there, or is
        unset when it has no default; a key in `always` is written on both
        even when neither has it, and a key in `noneAbsent` is not created
        to hold None. A value written for a key in `convert` goes through
        that function first. Caller holds the lock and the block.

        The values are moved, not copied: each object read from one side is
        put on the other, so nothing in the state is shared afterwards.
        """
        a = StateManager.Get(first)
        b = StateManager.Get(second)
        if not isinstance(a, dict) and not isinstance(b, dict):
            return
        a = a if isinstance(a, dict) else {}
        b = b if isinstance(b, dict) else {}
        keys = list(order)
        if not listedOnly:
            keys += [key for key in a if key not in keys]
            keys += [key for key in b if key not in keys]
        moves = []
        for key in keys:
            mine = a.get(key, _MISSING)
            theirs = b.get(key, _MISSING)
            if mine is _MISSING and theirs is _MISSING:
                if key not in always:
                    continue
            if mine is _MISSING:
                mine = defaults.get(key, _MISSING)
            if theirs is _MISSING:
                theirs = defaults.get(key, _MISSING)
            if key in convert:
                mine, theirs = (value if value is _MISSING
                                else convert[key](value)
                                for value in (mine, theirs))
            moves.append((key, theirs, mine))
        for key, toFirst, toSecond in moves:
            for path, held, value in ((first, a, toFirst),
                                      (second, b, toSecond)):
                if value is not _MISSING:
                    if value is None and key in noneAbsent \
                            and key not in held:
                        continue
                    StateManager.Set(f"{path}.{key}", value)
                elif key in held:
                    StateManager.Unset(f"{path}.{key}")

    def _UpdateUpsetFactor(self, board):
        """Recalculate `upset_factor` after the seeds moved.

        The card's seed spinbox used to trigger it (`player_seed_changed`)
        when the copy typed the other player's seed into it; a slot the
        state exchanges has no such edit. Recalculated in the operation's
        save cycle, where the other statistics a swap asks for come after
        it (StatsService.TeamsChanged).
        """
        StatsService(board).UpdateUpsetFactor()

    # ── clearing (ADR-005 S2d) ────────────────────────────────────────────
    #
    # The clears used to be the board's widgets being driven back to empty:
    # the score spinboxes set to 0 (their valueChanged wrote the score), the
    # losers boxes unchecked, and every player card's Clear() emptying its
    # fields one by one so that their handlers would publish -- a save cycle
    # per card. The operations below write the same state from the state,
    # one save cycle each, and the cards / spinboxes / boxes follow from the
    # held cycle (S2a). What a card's Clear() published is reproduced key by
    # key in `PlayerService._Clear`, including what it left alone.
    #
    # Board 3 (crew) is not one of these boards: each returns False there,
    # and the caller hands the operation to the crew widget (decision 6).

    def ClearScore(self, board) -> bool:
        """Zero both scores and both losers flags. True if it applied.

        The clear a set load runs before the next set arrives
        (`SetFollowService.LoadSet`, ADR-005 S3e-7), and the first half of an
        operator's reset (`ResetMatch`). It leaves the match bar alone:
        `SetLoadService.ApplyMatchInfo` keeps the round name for a payload
        that carries none, so blanking it here would wipe the round
        mid-stream (S-17).

        Only what changes is written, as the spinboxes and the checkboxes
        did (neither emits for its current value). A losers flag that goes
        takes its team's " [L]" with it (`SetTeamLosers`). The game
        tracker's rows follow the score in the same block, as they do for
        `ResetScore`.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(f"ClearScore: board {board} is not a score board")
            return False
        tracker = self.GameTracker(board)
        with StateManager.SaveBlock():
            with StateManager.lock:
                before = tracker.Scores()
                for team in (1, 2):
                    self._SetIfChanged(
                        f"score.{board}.team.{team}.score", self.SCORE_MIN)
                tracker.FollowScores(before)
            for team in (1, 2):
                self.SetTeamLosers(board, team, False)
        return True

    def ResetMatch(self, board) -> bool:
        """The operator's reset of the match: `ClearScore` plus the match
        bar (`ClearMatchInfo`), in one save cycle. True if it applied.

        Behind the board's reset-match button, the すべての入力値をリセット
        button (`ClearBoard`) and the Web's reset_match / clear_all. The best
        of is not part of it: the reset-match button has never reset it, the
        Web's two set it to 0 after this in a cycle of their own
        (`SetBestOf`), as they did, and `ClearBoard` does in its block.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(f"ResetMatch: board {board} is not a score board")
            return False
        with StateManager.SaveBlock():
            self.ClearScore(board)
            self.ClearMatchInfo(board)
        return True

    def ClearPlayers(self, board, no_mains=False) -> bool:
        """Empty every player slot of both teams in one save cycle. True if
        it applied.

        What each slot loses is what a player card's Clear() emptied
        (`PlayerService.ClearSlot`); the birthday, the city and the mains
        stay, as they did -- the city is a known gap (ADR-005 S2 decision
        5), recorded rather than fixed. `no_mains` keeps the characters,
        with the old clear's limits (see `PlayerService._Clear`). The upset
        factor is recalculated when a seed went, as the seed spinbox's
        change used to ask for; the other statistics are fetched again for
        each team whose player ids the clear changed, once for the whole
        clear (StatsService.TeamsChanged) -- a card used to ask for them
        when its own Clear() dropped its id, once per card, and the board
        asked once per clear since ADR-005 S2d.

        Every slot the state carries for the two teams, which on a built
        board is one per player card (the roster is trimmed to the cards).
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(f"ClearPlayers: board {board} is not a score board")
            return False
        seedsChanged = False
        changedTeams = []
        with StateManager.SaveBlock():
            with StateManager.lock:
                for team in (1, 2):
                    prefix = f"score.{board}.team.{team}.player"
                    roster = StateManager.Get(prefix)
                    if not isinstance(roster, dict):
                        continue
                    before = self._TeamPlayerIds(prefix)
                    for slot, value in list(roster.items()):
                        if isinstance(value, dict):
                            seedsChanged |= PlayerService(
                                f"{prefix}.{slot}").ClearSlot(
                                no_mains, announce=False).seedChanged
                    if self._TeamPlayerIds(prefix) != before:
                        changedTeams.append(team)
                if seedsChanged:
                    self._UpdateUpsetFactor(board)
        if changedTeams:
            StatsService(board).TeamsChanged(changedTeams)
        return True

    @staticmethod
    def _TeamPlayerIds(prefix):
        """{slot: id} of the team whose player roster is at `prefix`."""
        roster = StateManager.Get(prefix)
        if not isinstance(roster, dict):
            return {}
        return {slot: deep_clone(value.get("id"))
                for slot, value in roster.items() if isinstance(value, dict)}

    def ClearBoard(self, board) -> bool:
        """The すべての入力値をリセット button: the players, the score, the
        losers flags, the match bar, the team names (with their crests) and
        the best of, in one save cycle. True if it applied.

        The best of comes last, as the board's spinbox reset it after the
        rest until ADR-005 S2e; the player / character counts stay as they
        are.
        The Web's clear_all is not this: it has always kept the team names
        (unless it shrinks the roster to one player) and changes the counts
        between the reset and the players' clear, so it calls those
        operations in its own order (ADR-005 S2 decision 3).
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(f"ClearBoard: board {board} is not a score board")
            return False
        with StateManager.SaveBlock():
            self.ClearPlayers(board)
            self.ResetMatch(board)
            for team in (1, 2):
                self.SetTeamName(board, team, "")
                # The crest of no name: what SetLoadService.PublishTeamLogo
                # resolves for an empty one.
                self._SetIfChanged(f"score.{board}.team.{team}.logo", None)
            self.SetBestOf(board, 0)
        return True

    # ── the team roster (how many player slots a team has) ────────────────
    #
    # The player slots themselves are `PlayerService`'s; what belongs here
    # is the *count* -- how many slots a team has, and how many character
    # slots a player has. Since ADR-005 S3e-6 the count is the state's: the
    # slots themselves, set by `SetRoster` from every entry point (the
    # board's two spinboxes, a new board's seed, a game
    # load's players-per-team, a set's entrants, the Web's set_route /
    # clear_all), and a board builds and removes player cards to follow it.
    # It used to be the board's spinboxes (playerNumber / charNumber), whose
    # cards published the slots as they were built and unset them as they
    # were torn down -- which a shell without the board could not do at all.

    # The bounds of the two counts: the ranges the board's spinboxes had
    # (a QSpinBox's default maximum, and charNumber's).
    MAX_PLAYERS_PER_TEAM = 99
    MAX_CHARACTERS_PER_PLAYER = 100

    def SetRoster(self, board, players=None, characters=None,
                  forgetPicks=False) -> bool:
        """Give each team of board `board` `players` player slots, and each
        player `characters` character slots, in one save cycle. True if it
        applied (boards 1/2).

        None keeps a count. `players` None leaves the slots as they are --
        none goes, none is added, the team names stay -- and only resizes
        the character slots of every slot each team has (a roster with a
        gap, or teams of different sizes, included: a request for the
        characters is not one for the players). `characters` None keeps the
        character slots of the first slot there is (the default, 3, on a
        board with none -- SetPlayersPerTeam passes the count it keeps for
        such a board, EmptyRosterCharacters). Both are clamped to the ranges the
        board's spinboxes had. What it writes is what the board's cards
        wrote:

        - the slots above `players` go (both teams; a slot the state
          carries without a card -- a restored roster -- included);
        - a kept slot whose character slots are another number republishes
          them, and its mains with them (PlayerService.PublishCharacterSlots,
          as a card's slot-count change did): its first picks kept, blank
          ones after;
        - with `forgetPicks` (a game load that replaced another game:
          ApplyGameRoster), every kept slot's characters and mains are
          republished blank instead, whatever their number -- unless they
          already are;
        - a missing slot is seeded as an empty card seeded it
          (PlayerService.SeedEmpty), one index at a time, team 1 first;
        - going from another count (the larger team's, counted as the board
          counts its cards: RosterCount) to one player a team (or none)
          empties the team names and their crests, as the board's team-name
          fields did when they were hidden.

        The same counts as before, without `forgetPicks`, write nothing.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(f"SetRoster: board {board} is not a score board")
            return False
        with StateManager.SaveBlock():
            with StateManager.lock:
                rosters = {team: self._Roster(board, team) for team in (1, 2)}
                current = max(self._Contiguous(rosters[team])
                              for team in (1, 2))
                target = None if players is None else max(
                    0, min(self._asInt(players, current),
                           self.MAX_PLAYERS_PER_TEAM))
                count = self._CharacterCount(rosters) if characters is None \
                    else max(0, min(self._asInt(
                        characters, DEFAULT_CHARACTERS_PER_PLAYER),
                        self.MAX_CHARACTERS_PER_PLAYER))

                if target is not None:
                    for team in (1, 2):
                        self.TrimTeamRoster(board, team, target)

                # The slots kept: 1..target, or -- the players kept --
                # every one each team has, in order.
                keys = {team: [str(index) for index in range(1, target + 1)]
                        if target is not None else sorted(
                            rosters[team], key=lambda key: self._asInt(key, 0))
                        for team in (1, 2)}

                # One game for every slot: what a pick is resolved against
                # is what its value is built from.
                snap = GameAssetCatalog.instance.Current()
                for position in range(max(len(keys[1]), len(keys[2]))):
                    for team in (1, 2):
                        if position >= len(keys[team]):
                            continue
                        key = keys[team][position]
                        slot = rosters[team].get(key)
                        if not isinstance(slot, dict):
                            continue
                        service = PlayerService(
                            f"score.{board}.team.{team}.player.{key}")
                        picks = slot.get("character")
                        if forgetPicks:
                            if self._IsBlank(service, slot, count, snap):
                                continue
                            slots = [CharacterSlot()] * count
                        elif isinstance(picks, dict) and len(picks) == count:
                            continue
                        else:
                            slots = self._ResizedPicks(
                                service, picks, count, snap)
                        service.PublishCharacterSlots(
                            slots, includeMains=True, snapshot=snap)

                for index in range(1, (target or 0) + 1):
                    for team in (1, 2):
                        if not isinstance(
                                rosters[team].get(str(index)), dict):
                            PlayerService(
                                f"score.{board}.team.{team}.player.{index}"
                            ).SeedEmpty(count)

                if target is not None and target != current and target <= 1:
                    for team in (1, 2):
                        self.SetTeamName(board, team, "")
                        # The crest of no name, as in ClearBoard.
                        self._SetIfChanged(
                            f"score.{board}.team.{team}.logo", None)
        return True

    def SetPlayersPerTeam(self, board, players) -> bool:
        """`players` player slots a team on board `board` (SetRoster), with
        the characters per player the board keeps for a roster with none: a
        board with no character slot left gives its next players the count
        it kept (EmptyRosterCharacters) rather than SetRoster's default, and
        one that loses its last player keeps the count it had there. As the
        board's two spinboxes had it: players=0, then characters=2, then
        players=2 is two players a team with two characters each. True if it
        applied (boards 1/2).

        The players-per-team of every entry point: the board's spinbox, the
        Web's set_route, a set's entrants (SetFollowService). The count kept
        was the board's hidden spinbox on the desktop and the board host's
        headless until ADR-005 S4x; it is one memory, in both shells, now.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            return False
        with StateManager.SaveBlock():
            with StateManager.lock:
                current = self.CharacterSlots(board)
                characters = None
                if current is None:
                    characters = self.EmptyRosterCharacters(board)
                elif players is not None and self._asInt(players, 1) <= 0:
                    self._KeepEmptyRosterCharacters(board, current)
                return self.SetRoster(board, players, characters)

    def SetCharactersPerPlayer(self, board, characters) -> bool:
        """The character slots of every player of board `board` (SetRoster),
        or -- on a board with none -- the count its next players get
        (SetPlayersPerTeam), as the board's spinbox kept it. True if it
        applied (boards 1/2)."""
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            return False
        with StateManager.SaveBlock():
            with StateManager.lock:
                if self.CharacterSlots(board) is None:
                    self._KeepEmptyRosterCharacters(board, characters)
                return self.SetRoster(board, characters=characters)

    def EmptyRosterCharacters(self, board) -> int:
        """The characters per player the next players of board `board` get
        while it has no character slot: the count it kept, else the default
        (DEFAULT_CHARACTERS_PER_PLAYER)."""
        return self._emptyCharacters.get(
            self._asInt(board, 0), DEFAULT_CHARACTERS_PER_PLAYER)

    def _KeepEmptyRosterCharacters(self, board, count):
        # Clamped as SetRoster clamps a count (the spinbox's range).
        self._emptyCharacters[board] = max(0, min(
            self._asInt(count, DEFAULT_CHARACTERS_PER_PLAYER),
            self.MAX_CHARACTERS_PER_PLAYER))

    def ApplyGameRoster(self, replaced=None) -> list:
        """A game was loaded: every 1v1 / team board that has a roster goes
        to the game's players-per-team (`game.defaults`) and the default
        characters per player, in one save cycle. Returns the boards it
        applied to.

        `replaced`: the game that load replaced -- the `replaced` of the
        snapshot the load itself returned (GameAssetSnapshot), which the
        shell passes on. Not read here from the catalog's Current(): run
        after the load is over (queued), that can be a later load's. When
        it is set, every slot's characters and mains are emptied too,
        whatever their number (SetRoster's `forgetPicks`): the previous
        game's picks mean nothing in the new one, and left in the slots
        they would go out with the next 反映. Not on the first load of a
        session, nor on a reload of the same game -- the rule the Top 8
        players' characters are emptied by (Top8Service.ForgetCharacters,
        from the same snapshot field).

        What each built board did on the asset adapter's onLoad until ADR-005
        S3e-6, and what the headless shell did not do at all. Both shells run
        it on a game load, through
        the same wiring (bootstrap.wire_game_roster). A board without a
        roster -- one not made yet (every board is made at launch since
        ADR-005 S4ac-1; the desktop made a board as it built it until then)
        -- has nothing to follow: it gets the game's count when it is seeded.
        """
        forget = bool(replaced)
        applied = []
        with StateManager.SaveBlock():
            for board in self.SCORE_BOARDS:
                if not isinstance(StateManager.Get(
                        f"score.{board}.team.1.player"), dict):
                    continue
                self.SetRoster(board, DefaultPlayersPerTeam(),
                               DEFAULT_CHARACTERS_PER_PLAYER,
                               forgetPicks=forget)
                applied.append(board)
        return applied

    @classmethod
    def RosterCount(cls, board, team) -> int:
        """The players team `team` of board `board` has: its slots counted
        from 1 up to the first one missing (a card stands for slot 1..n).
        The one count of a roster -- SetRoster's and the board's."""
        return cls._Contiguous(cls._Roster(cls._asInt(board, 0), team))

    @staticmethod
    def _Contiguous(roster) -> int:
        count = 0
        while isinstance(roster.get(str(count + 1)), dict):
            count += 1
        return count

    @staticmethod
    def _Roster(board, team) -> dict:
        """{slot: value} of one team's player slots ({} for none)."""
        roster = StateManager.Get(f"score.{board}.team.{team}.player")
        return dict(roster) if isinstance(roster, dict) else {}

    def _CharacterCount(self, rosters) -> int:
        """The character slots the board's players have: those of its first
        slot, the default for a board with none."""
        count = self._FirstCharacterSlots(rosters)
        return DEFAULT_CHARACTERS_PER_PLAYER if count is None else count

    def CharacterSlots(self, board):
        """The character slots each player of board `board` has -- the
        count SetRoster keeps when it is not given one -- or None on a board
        with none, where SetRoster falls back to the default and
        SetPlayersPerTeam passes the count it keeps (EmptyRosterCharacters)."""
        board = self._asInt(board, 0)
        return self._FirstCharacterSlots(
            {team: self._Roster(board, team) for team in (1, 2)})

    def _FirstCharacterSlots(self, rosters):
        for team in (1, 2):
            for slot in sorted(rosters[team],
                               key=lambda key: self._asInt(key, 0)):
                value = rosters[team][slot]
                if isinstance(value, dict) and isinstance(
                        value.get("character"), dict):
                    return len(value["character"])
        return None

    @staticmethod
    def _ResizedPicks(service, characters, count, snap) -> list:
        """The `count` character slots of a slot that had `characters`:
        its first picks, blank ones after."""
        if not isinstance(characters, dict):
            return [CharacterSlot()] * count
        picks = [service.SlotFromValue(
                     characters.get(index, characters.get(str(index))), snap)
                 for index in range(1, min(len(characters), count) + 1)]
        return picks + [CharacterSlot()] * (count - len(picks))

    @staticmethod
    def _IsBlank(service, slot, count, snap) -> bool:
        """Whether a slot's characters and mains already are `count` blank
        character slots (keys compared as text: a restored state has them
        as strings)."""
        blank = {str(index): service.CharacterValue(CharacterSlot(), snap)
                 for index in range(1, count + 1)}
        for key in ("character", "mains"):
            value = slot.get(key)
            if not isinstance(value, dict) or {
                    str(k): v for k, v in value.items()} != blank:
                return False
        return True

    def ClearTeamRoster(self, board, team) -> bool:
        """Drop every player slot of one team. True if it applied.

        Run once per team while a board is built, before the first player
        widget is created: the restored state (user_data/state.json) can
        carry a roster from a session that ran with more players per team,
        and nothing else would ever remove the surplus.
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

        SetRoster's first step (ADR-005 S3e-6). It used to be the pass after
        the board had torn its surplus cards down (each unsetting its own
        slot), for the slots the state carried without a card; since the
        count is the state's, it is the removal itself, and the board's
        cards follow it.

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
        previous set's round into the next title.

        Each field only where the board has the key: a reset blanks what is
        there and adds nothing. Board 3 never carries `match_en`
        (CrewBattleService does not publish it), nor `pool` until something
        writes one (its seed does not: CrewBattleService.SeedDefaults), so a
        reset of a fresh crew board writes nothing (ADR-005 S4ac-1 review: it
        wrote `pool` = "" until then, which made the v1 resets of a fresh
        crew board a write). Boards 1 / 2 have every field from their seed.

        One save cycle for all of it, and nothing written for a field that is
        already blank (see `_SetIfChanged`).
        """
        prefix = f"score.{self._asInt(board, 0)}"
        written = False
        with StateManager.SaveBlock():
            for field in (*schema.SCORE_MATCH_FIELDS, "match_en"):
                key = f"{prefix}.{field}"
                if StateManager.Get(key, None) is not None:
                    written |= self._SetIfChanged(key, "")
        return written

    # ── the X post (ADR-005 S4x) ──────────────────────────────────────────

    def PostText(self, board):
        """Board `board`'s (1/2) X post: (its text, what is missing for it),
        read under one hold of the lock -- or None for any other board (the
        crew board's is CrewBattleService.PostText). `missing` is a list of
        CrewBattleService's POST_MISSING_* in this order: a team with no named
        player in its slots, no phase and no round. The board's button
        refuses to post while it has any, and says which.

        The live values, 反映 or not, as the button always read them (on the
        desktop the board's edits wait behind 反映). What the board's PostToX
        built until ADR-005 S4x, word for word: the tournament name, "[phase]
        - round", each team's named players in slot order with their @handle,
        their characters and their seed, and the stream URL. A value the
        state holds as something else than text is read as its text (the
        button raised on it).
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            return None

        def text(value):
            return "" if value is None else str(value)

        def ordered(keys):
            return sorted(keys, key=lambda key: int(key)
                          if str(key).isdigit() else 0)

        with StateManager.lock:
            # Not stripped, as the post always took them; the round's
            # presence is judged on the stripped text.
            tournament = text(StateManager.Get(
                f"{schema.TOURNAMENT_INFO_ROOT}."
                f"{schema.TOURNAMENT_NAME_KEY}") or "")
            phase = text(StateManager.Get(f"score.{board}.phase") or "")
            match = text(StateManager.Get(f"score.{board}.match") or "")
            streamUrl = text(StateManager.Get("stream.url") or "")
            teams = {}
            for team in (1, 2):
                roster = StateManager.Get(f"score.{board}.team.{team}.player")
                entries = []
                for key in ordered(roster if isinstance(roster, dict) else ()):
                    slot = roster.get(key)
                    if not isinstance(slot, dict):
                        continue
                    name = text(slot.get(schema.PLAYER_TAG_KEY) or "").strip()
                    if not name:
                        continue
                    picks = slot.get("character")
                    picks = picks if isinstance(picks, dict) else {}
                    characters = []
                    for pick in ordered(picks):
                        value = picks[pick]
                        characterName = text(value.get("name", "")) \
                            if isinstance(value, dict) else ""
                        if characterName:
                            characters.append(characterName)
                    twitter = text(
                        slot.get(schema.PLAYER_TWITTER_KEY) or "").strip()
                    seed = slot.get(schema.PLAYER_SEED_KEY)
                    entry = name
                    if twitter:
                        # Normalized to an @handle so the post mentions it.
                        handle = twitter.lstrip("@").strip()
                        if handle:
                            entry += f" @{handle}"
                    if characters:
                        entry += f"({'/'.join(characters)})"
                    if seed:
                        entry += f" [Seed:{seed}]"
                    entries.append(entry)
                teams[team] = entries

        missing = []
        if not teams[1]:
            missing.append(CrewBattleService.POST_MISSING_TEAM_1)
        if not teams[2]:
            missing.append(CrewBattleService.POST_MISSING_TEAM_2)
        if not (phase.strip() or match.strip()):
            missing.append(CrewBattleService.POST_MISSING_ROUND)

        roundLine = f"[{phase}]" if phase else ""
        if match:
            roundLine = f"{roundLine} - {match}" if roundLine else match

        parts = []
        if tournament:
            parts.append(tournament)
        if roundLine:
            parts.append(roundLine)
        parts.extend(["", " / ".join(teams[1]) or "???", "vs",
                      " / ".join(teams[2]) or "???", ""])
        if streamUrl:
            parts.append(f"\U0001f534STREAM: {streamUrl}")
        return "\n".join(parts), missing

    # ── best of ──────────────────────────────────────────────────────────

    def GameTracker(self, board):
        """The GameTrackerService of 1v1 / team board `board`: its game rows.

        Core's, in every shell and with or without a built board (ADR-005
        S4t). The board's widget renders this one. Until then the board
        built it and registered it here while it existed, so the rows, and
        the score following onto them, were a built board's alone.
        """
        return self._gameTrackers[self._asInt(board, 0)]

    def SetBestOf(self, board, value) -> bool:
        """Set a board's set length. True if it changed.

        The six derived keys and the game tracker's rows (one unreported row
        per game, none with the tracker switched off), in one save cycle --
        see `_PublishBestOf`. The board's BEST OF
        spinbox and its rows render the result from the held cycle (ADR-005
        S2e); every entry point comes here: the spinbox, a set load, the
        Web's set_route / reset_match / clear_all, /api/v1's PATCH set and
        the reset-all button (`ClearBoard`).

        A value the board already has is not a write, and it does not reset
        the rows: the spinbox's valueChanged, which all of those used to go
        through, does not fire for its current value. A game load re-asserts
        it on purpose (`RepublishBestOf`). Clamped to the spinbox's range, as
        setValue() clamped it; a value that is not a number at all is
        refused with a warning rather than read as 0, which would clear the
        set length (the rule SetLoadService applies to a set's numbers).
        Boards 1/2 only: the crew board's set length is its own
        (CrewBattleService.SetBestOf).
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(f"SetBestOf: board {board} is not a score board")
            return False
        try:
            number = int(value)
        except (TypeError, ValueError):
            logger.warning(f"SetBestOf: ignoring non-numeric best of {value!r}")
            return False
        value = max(self.BEST_OF_MIN, min(self.BEST_OF_MAX, number))
        with StateManager.SaveBlock():
            with StateManager.lock:
                if StateManager.Get(f"score.{board}.best_of", None) == value:
                    return False
                self._PublishBestOf(board, value)
        return True

    def RepublishBestOf(self, board) -> bool:
        """Publish a board's current set length again, rows included. True
        if it applied.

        What a game load does (`RepublishBestOfs`), as the spinbox's
        re-emitted valueChanged did: the localized texts are derived again (a
        reload can be a locale change) and the rows start over -- they
        recorded games of the previous game's stages, and whether the
        tracker is on is read again.
        """
        board = self._asInt(board, 0)
        if board not in self.SCORE_BOARDS:
            logger.debug(
                f"RepublishBestOf: board {board} is not a score board")
            return False
        with StateManager.SaveBlock():
            with StateManager.lock:
                self._PublishBestOf(board, self._asInt(
                    StateManager.Get(f"score.{board}.best_of", 0), 0))
        return True

    def RepublishBestOfs(self) -> list:
        """A game load's `RepublishBestOf`, on every board that has a set
        length, in one save cycle; returns those boards. Both shells run it
        with the load's roster (bootstrap.wire_game_roster); until ADR-005
        S4t it was each built board's onLoad handler, so the headless shell
        did without it. A board not made yet has none, and gets one when it
        is made (`SeedFreshBoard`; every board at launch since ADR-005
        S4ac-1)."""
        applied = []
        with StateManager.SaveBlock():
            for board in self.SCORE_BOARDS:
                if StateManager.Get(f"score.{board}.best_of", None) is None:
                    continue
                self.RepublishBestOf(board)
                applied.append(board)
        return applied

    def _PublishBestOf(self, board, value):
        """Write a set length and everything derived from it. Caller holds
        the block.

        Six keys, because they are one fact: the raw `best_of`, the short
        forms overlays use in tight spaces (`BO5` / `FT3`), and the
        localized long forms. 0 means "not set", and the long forms are then
        blank rather than "Best of 0". Then the rows, after the keys as the
        spinbox handler wrote them: `GameTrackerService.SetStageCount()` is
        the only thing allowed to resize them, and it is reached here through
        the board's tracker.

        A formatter that raises gets the English default instead
        (`_matchText`), so the rows and the other keys are written all the
        same. Anything else that raises does so inside the caller's block
        rather than around it, so a failure cannot leave `saveBlocked`
        positive -- which would freeze ./out and the WebSocket feed for the
        rest of the session (agent-pitfalls #6).
        """
        firstTo = math.ceil(value / 2)
        prefix = f"score.{board}"
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
        tracker = self.GameTracker(board)
        tracker.SetStageCount(tracker.ResolveStageCount(value))

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

    # ── 反映 (deferred publication) ───────────────────────────────────────

    def ApplyChanges(self, board):
        """Publish a board's pending held edits (the 反映 button / hotkey).

        Safe on a board that was never built or is not held at all (board 3):
        CommitHeld queues the prefix and the save cycle finds nothing pending.
        """
        StateManager.CommitHeld(f"score.{self._asInt(board, 0)}")

    def CommitGameLoad(self):
        """Publish what a game load wrote on the 1v1 / team boards: it
        rewrites characters and defaults wholesale, so that is the fresh
        baseline rather than edits waiting behind the 反映 button.

        Called by the desktop's asset adapter after the load's slots
        (AutonaGameAssetManager._Publish), not with the roster they follow:
        a player card's ReloadCharacters still writes what its rows pick when
        it rebinds them, on onLoad. Each built board's onLoad handler did
        this until ADR-005 S4t. The headless shell holds nothing, so it has
        nothing to commit.
        """
        for board in self.SCORE_BOARDS:
            self.ApplyChanges(board)
