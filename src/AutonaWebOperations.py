"""The web layer's operations (ADR-005 S3c), the same in both shells.

What the legacy routes, the Socket.IO events and /api/v1 call for every
operation. They used to be methods of the web server's actions class, which
needed the scoreboard dock to exist; ADR-005 S3c moved what was core's here,
S3e-1..8 moved the rest as it became core's ("board-dependent" in
docs/changes/architecture-modernization.md, S3c-2), and S3e-9 deleted the
class. What is left of the desktop's is two shell operations, which the
shell registers (the last item below).

These kinds live here:

- core-direct: read or write core on the calling (Flask / Socket.IO) thread,
  the same in both shells -- the scores, 反映, the colours, the display
  options, the game / country / player DB readers, the tournament URL.
- the operations ADR-005 S2 moved into core (swap, the clears, the set
  fields, losers, the team name, reset score, and the readers get_swap /
  get_set / get_scoreboard). They call core in both shells too; what the
  desktop adds around that call is its GUI thread, where it runs them (see
  DesktopBoardHost.Run). Until ADR-005 S4ac-3 removed the Qt SCORE it also
  let the field being typed in on a board's widgets finish first. The board
  itself is core's, made at launch in both shells, and held on the desktop
  (ADR-005 S4ac-1); until then the desktop built a board first, because an
  unbuilt one was not held and the seed of its build wiped the write. The
  player / character counts of
  boards 1/2 are core's too since S3e-6 (AutonaActions.SetRoster; they were
  the board's spinboxes, and a shell without the board refused them).

  That difference is one object the shell registers, `boardHost`: the
  headless default (`BoardHost`) runs the operation on the calling thread,
  one at a time -- the write is published at once, which is right there:
  there is no 反映 button. bootstrap_gui() registers `DesktopBoardHost`.
- the crew board's operations (its members' stocks and active member, and
  its swap / resets / clears / set fields) and the caster line-up's
  (ADR-005 S3e-2): core's CrewBattleService and CommentaryService, in both
  shells. The crew ones go through the board host like the S2 ones (the
  desktop runs them on its GUI thread); the
  line-up has no board and is written on the calling thread. The board and
  the Commentary page render what was written.
- 配信設定's (ADR-005 S4e): core's StreamService -- the channels, the stream
  org's assets, the thumbnail options and the thumbnail job -- and the
  destination / title through AutonaActions, in both shells, on the calling
  thread (no board). The Stream page renders what was written.
- 大会情報's (ADR-005 S4g): core's TournamentService (the set / clear and
  its status), TournamentInfoService (the five fields), TournamentMedia
  Service (the icon / banner) and ColorService (the tournament colours), in
  both shells, on the calling thread -- but the set / clear, which core
  runs on the main thread. The Event page renders what was written.
- the player slots' (ADR-005 S3e-4): the Web's player edit (v1's PATCH
  players and the legacy update-team) and the tag lookup -- core's
  PlayerService.ApplyData, through the board host like the S2 ones. The
  desktop's host adds what the board's cards do around a load (the typed
  combo text).
- the statistics of boards 1/2 (ADR-005 S3e-5): core's StatsService,
  through the board host like the S2 ones, in both shells. The fetches are
  the shell's (StatsService.fetcher) and answer into the state later.
- the Top 8 import (ADR-005 S3e-8): core's Top8Service, in both shells. It
  runs on the tournament provider's worker; the Top 8 page renders what it
  wrote.
- the Top 8 page's (ADR-005 S4i): core's Top8Service -- one player's
  fields, the overlay's caption, the scores and their reset -- in both
  shells, on the calling thread (no board). The page renders what was
  written.
- set loading and following (ADR-005 S3e-7): load-set, POST /score and
  pull-stream -- core's SetFollowService, through the board host like the
  S2 ones, in both shells. The fetches and the 5-second poll are the
  shell's (SetFollowService.fetcher) and answer into the state later.
- the player list's (ADR-005 S4m): core's PlayerDirectory -- its rows, and a
  record saved or deleted -- in both shells, on the calling thread (no board,
  no slot: the database's write waits for nothing under a lock).
- the match thumbnail's (ADR-005 S4o): core's ThumbnailService -- its
  settings, their defaults, the uploaded background, the preview's file -- in
  both shells, on the calling thread. Starting a render (`preview_thumbnail`
  / `generate_thumbnail`) needs the shell's image renderer, below.
- the shell operations (ADR-005 S3e-9): opening a board's set selection (the
  SCORE page's dialog in the desktop's window; S4ab-2 -- the Qt window on
  the way back to the Qt panel until S4ac-3), rendering its thumbnail (the legacy route,
  synchronously) and starting an image's render off-screen (S4o). Not core's:
  one opens something in the desktop's window, the others draw with a
  WebEngine view, so they need the desktop's QApplication and widgets. The
  desktop registers its shell adapter's (AutonaShellOperations:
  `openSetSelection` / `renderThumbnail` / `startImageRender`,
  bootstrap_gui(); run on its GUI thread), the headless shell registers none,
  and they raise NotAvailable: 503. No other operation needs a shell.

Imports nothing that builds anything; the registrations are plain module
attributes, filled in by the shell (agent-pitfalls #1).
"""
import copy
import threading

import orjson
from loguru import logger

from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from .core import ColorService
from .core import CommentaryModel as comms
from .core import CommentaryService
from .core import LayoutCatalog
from .core import PlayerDirectory
from .core import Top8Service
from .core import StatsOverlays
from .core import AnalysisService as analysis
from .core.AnalysisService import AnalysisService
from .core.AppSettings import AppSettings
from .core import HotkeyRules
from .core.CountryCatalog import CountryCatalog
from .core.CrewBattleService import CrewBattleService
from .core.DictHelper import qvariantmap_copy
from .core.GameAssetCatalog import GameAssetCatalog
from .core.MatchNameCandidates import MatchNameCandidates
from .core.SettingsManager import DEFAULT_WEBSERVER_PORT, SettingsManager
from .core.SetFollowService import SaveRecord, SetFollowService
from .core.SetLoadService import SetLoadService
from .core import SetSelectionService as selection
from .core.SetSelectionService import SetSelectionService
from .core.PlayerDB import PlayerDB
from .core.PlayerService import NOT_SENT, PlayerService
from .core import ResultService as results
from .core.ResultService import ResultService
from .core.StatsService import StatsService
from .core.StreamService import StreamService
from .core.ThumbnailService import ThumbnailService
from .core.TournamentInfoService import TournamentInfoService
from .core.TournamentMediaService import TournamentMediaService
from .core.TournamentService import TournamentService
from .core import StateSchema as schema
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaQtHelper import (
    GuiMarshalAborted, gui_thread_sync)


NOT_AVAILABLE_MESSAGE = "not available in headless"
# What an operation that needed the GUI thread answers once that thread will
# not run it any more (the app is shutting down; ADR-005 S3f-4 review).
SHUTTING_DOWN_MESSAGE = "the app is shutting down"
# What an operation on a board no shell has (neither 1/2 nor the crew board)
# answers, in both shells (`_require_board`, ADR-005 S4x review).
NO_SUCH_BOARD_MESSAGE = "scoreboard {board} does not exist"


class NotAvailable(Exception):
    """This shell cannot run the operation: a shell operation it did not
    register (the headless shell, ADR-005 S3e-9), or a board other than
    1/2/3, which no shell has.

    The web server answers it with 503 in the /api/v1 error shape (the
    legacy routes and the Socket.IO acks too); /api/v1 maps it to its
    UNAVAILABLE code.
    """

    def __init__(self, message=NOT_AVAILABLE_MESSAGE):
        super().__init__(message)
        self.message = message


class NoSuchSlot(IndexError):
    """The board has no such player slot (load_player_from_tag /
    load_player_from_key, and the field and character writes of ADR-005
    S4z). An IndexError, as the legacy route's 500 has always been; /api/v1
    tells it from an IndexError out of the load itself and answers 404."""


def _on_gui_thread(call, what):
    """`call()` -- a wait for the GUI thread (gui_thread_sync, or what makes
    one) -- or, once that thread will not run it any more (the app is
    shutting down: GuiMarshalAborted), NotAvailable: the 503 answer, logged
    at INFO, instead of a 500 with a traceback in the error log for a
    request that only came late (ADR-005 S3f-4 review)."""
    try:
        return call()
    except GuiMarshalAborted as aborted:
        logger.info(f"web operations: {what} not run: {aborted}")
        raise NotAvailable(SHUTTING_DOWN_MESSAGE) from None


class BoardHost:
    """Where the S2 operations run: the headless shell's, the default until
    a shell registers another, and all of the desktop's but Run since
    ADR-005 S4ac-3 (DesktopBoardHost).

    The operation runs on the calling thread; nothing is built. A board no
    shell has (neither 1/2 nor the crew board) is refused by the operations
    themselves (NotAvailable), in both shells. The hooks below that do
    nothing (Prepare, CommitEdit, DiscardTypedText, PlayerLoaded) were the
    desktop's board widgets' until S4ac-3, and go in S4ad.
    """

    def __init__(self):
        # One operation at a time. An S2 operation is several core calls
        # (clear_all is four save cycles, /api/v1's PATCH set up to three
        # calls in one task); the desktop queues them on its GUI thread, and
        # with none, two requests' threads would interleave them. Re-entrant:
        # an operation run here runs others here (PATCH set's task calls
        # set_route).
        #
        # Taken by the web layer's request threads and, headless, by the
        # main thread too: a game load's roster runs here
        # (bootstrap.wire_game_roster, ADR-005 S3e-6). Never with
        # StateManager.lock held -- it is always the outermost lock, so it
        # cannot close a cycle with that one. INVARIANT: nothing run under
        # it waits for the main thread (a gui_thread_sync, a queued call
        # waited on, a join of something that needs it). The main thread
        # may be the one waiting for this lock, so such a wait never ends.
        # What would wait is kept out of it -- the player database's write,
        # whose change relay runs on the main thread (`_save_record`,
        # ADR-005 S3e-4 review).
        self._lock = threading.RLock()

    def Run(self, op):
        """Run `op()` where this shell runs board operations."""
        with self._lock:
            return op()

    def Prepare(self, board):
        """Make board `board` ready to be written or read: nothing, as both
        shells make all three at launch (AutonaActions.MakeBoards). The
        desktop made a board core did not have again, with its widgets,
        until ADR-005 S4ac-3."""

    def Built(self, board):
        """Whether board `board` is there without Prepare: always, as both
        shells make all three at launch (AutonaActions.MakeBoards:
        bootstrap_gui(), src/headless._SeedBoards). The desktop read core's
        flag (AutonaActions.BoardEnsured) until ADR-005 S4ac-3."""
        return True

    def CommitEdit(self, board):
        """Finish the field being typed in on board `board`: nothing, with
        no board widget (the desktop's, until ADR-005 S4ac-3)."""

    def DiscardTypedText(self, board):
        """Drop, on every card of board `board`, the text typed into its
        combos and never picked (after a players' clear): nothing, with no
        card (the desktop's, until ADR-005 S4ac-3)."""

    def ClearPlayers(self, board, no_mains=False):
        """Empty every player slot of board `board` (1/2), and forget the set
        it loaded last -- loading it again loads it again (ADR-005 S3e-7: a
        set loads here too). The same in both shells since ADR-005 S4x (the
        desktop's ran the board's CommandClearAll), in its order: the field
        being typed in is finished first, so the clear takes a card's typed
        text with it (CommitEdit), and after the clear every card drops the
        text typed into its combos and never picked -- the combo whose list
        is open included, unlike after a load from elsewhere: the clear is
        the operator's operation on the board (DiscardTypedText). Both are
        nothing with no widget -- in both shells since ADR-005 S4ac-3, which
        removed the Qt SCORE. Prepared first, as every board operation. The
        statistics are the operation's (AutonaActions.ClearPlayers, ADR-005
        S3e-5). Any other board is refused before anything is written."""
        number = _require_board(board)
        if number not in AutonaActions.SCORE_BOARDS:
            # The crew board's members are CrewBattleService.ClearCrew's.
            raise NotAvailable(f"scoreboard {number} has no player slots")
        self.Prepare(board)
        self.CommitEdit(board)
        AutonaActions.instance.ClearPlayers(board, no_mains)
        self.DiscardTypedText(board)
        SetLoadService(number).lastSetSelected = None

    def ResetCounts(self, board):
        """Back to one player per team and one character per player.

        Core's in both shells (AutonaActions.SetRoster, ADR-005 S3e-6): the
        desktop's board follows the state. Until then the counts were a
        built board's spinboxes, which the desktop's host drove, and with no
        board the roster stayed as it was.
        """
        AutonaActions.instance.SetRoster(board, 1, 1)

    def LastSet(self, board):
        """The provider id of the set board `board` loaded last, or None.
        Kept by SetLoadService, by board (ADR-005 S3e-7: it was the board's
        and there was none here); only boards 1/2 load sets. The same in
        both shells since ADR-005 S4x: the desktop's read it from the built
        board (building it first) and raised for the crew board."""
        if _board(board) not in AutonaActions.SCORE_BOARDS:
            return None
        return SetLoadService(_board(board)).lastSetSelected

    def PlayerLoaded(self, board, team, player, cleared):
        """A player was loaded into slot `player` of state team `team` on
        board `board` (1/2); `cleared`: whether the load cleared the slot
        first. Called inside the load's save block. With no card there is
        nothing to tidy up (the desktop's cards dropped their typed combo
        text here until ADR-005 S4ac-3). (The statistics the load calls for are its own
        -- PlayerService.Announce, ADR-005 S3e-5 -- in both shells.)
        """


class DesktopBoardHost(BoardHost):
    """The desktop's board host: the operations on its GUI thread.
    Registered by bootstrap_gui().

    Only Run is the desktop's own since ADR-005 S4ac-3, which removed the Qt
    SCORE: the rest is the headless host's (BoardHost). Core makes every
    board at launch (AutonaActions.MakeBoards, bootstrap_gui(); S4ac-1), so
    there is nothing to prepare -- until S4ac-3 Prepare made a board core did
    not have again, with its widgets (AutonaScoreboardManager), which only a
    test's harness could bring about -- and nothing is built: no field being
    typed in to finish, no card to tidy up. The hooks left empty that way
    (Prepare, CommitEdit, DiscardTypedText, PlayerLoaded) go in S4ad, with
    Run's GUI thread."""

    def Run(self, op):
        # On the GUI thread, not under the headless host's lock (reviewed in
        # ADR-005 S3e-4, kept): a card committed a typed sponsor / tag in
        # two writes on that thread -- the field, then the display name,
        # images and id -- and a swap, a clear or a load landing between the
        # two from another thread left the display name on the wrong slot.
        # Queued on the GUI thread, an operation runs between two events,
        # never inside one. The cards went with the Qt SCORE (ADR-005
        # S4ac-3); taking the operations off the GUI thread is S4ad's.
        return _on_gui_thread(gui_thread_sync(op), "a board operation")


# ── Registrations (filled in by the shell) ───────────────────────────────

# Where the S2 operations run: BoardHost (no board) unless the desktop
# registered its DesktopBoardHost (bootstrap_gui()).
boardHost = BoardHost()


def current_board_host():
    """The board host registered now -- what core's set follow runs its
    board operations through (SetFollowService.boardHost, bootstrap_core();
    read when used, as the desktop registers its own afterwards)."""
    return boardHost

# How a game is loaded by codename, with the asset adapter's signature:
# (codename, async_mode=True, mods_active=False, mods_reload_mode=False,
# done=None). The desktop registers AutonaGameAssetManager.LoadGameByCodename
# (bootstrap_gui()), the headless shell its catalog-only loader
# (src/headless.py). None: nothing can load a game.
loadGameByCodename = None

# The shell operations (ADR-005 S3e-9; see open_sets / get_thumbnail): the
# desktop registers its AutonaShellOperations' (bootstrap_gui(); the
# scoreboard dock's until ADR-005 S4y), the headless shell none. None: the
# operation answers NotAvailable (503).
# Open board `scoreboard`'s set selection (the SCORE page's dialog):
# (scoreboard) -> "OK".
openSetSelection = None
# Render board `scoreboard`'s thumbnail: (scoreboard, file_format) -> the
# image's absolute path, or None when none was made (a failed render raises).
renderThumbnail = None
# Start drawing an image off-screen and return, without waiting for it:
# (kind, payload, done). One operation for every generated image -- the match
# thumbnail (core/ThumbnailService) and the result (core/ResultService), by
# `kind` -- rather than one per image: what differs between them is core's, and
# the shell only has
# the view. `done(encode, error)` comes later on the GUI thread. ADR-005 S4o.
startImageRender = None


def _board(scoreboard):
    return AutonaActions._asInt(scoreboard, 0)


# The crew battle board: its operations are CrewBattleService's.
CREW_BOARD = 3


def _is_crew(scoreboard):
    return _board(scoreboard) == CREW_BOARD


def _require_board(scoreboard):
    """Board `scoreboard`'s number -- or, for one that is neither 1/2 nor the
    crew board, NotAvailable (503), raised before anything is built or
    written: no shell has such a board. The same in both shells since
    ADR-005 S4x; the desktop's host used to ask a board widget it does not
    have (500), the headless one refused it from the same call. The message
    names the board, not the shell (NOT_AVAILABLE_MESSAGE is a shell
    operation's: the desktop has no such board either)."""
    board = _board(scoreboard)
    if board not in AutonaActions.SCORE_BOARDS and board != CREW_BOARD:
        raise NotAvailable(NO_SUCH_BOARD_MESSAGE.format(board=scoreboard))
    return board


# ═══════════════════════════════════════════════════════════════════════
# core-direct
# ═══════════════════════════════════════════════════════════════════════

def program_state():
    return StateManager.GetFullState()


# The writes below used to emit a signal into a board widget, which did the
# writing (until headless core Step 3). They call AutonaActions directly:
# no widget is built to serve them, the write happens synchronously on the
# caller's thread (so /api/v1's `revision` is accurate for them too), and a
# board -- if one exists -- updates its display from the state notification.

def team_scoreup(scoreboard, team):
    AutonaActions.instance.AdjustScore(
        scoreboard, 1 if str(team) == "1" else 2, 1)
    return "OK"


def team_scoredown(scoreboard, team):
    AutonaActions.instance.AdjustScore(
        scoreboard, 1 if str(team) == "1" else 2, -1)
    return "OK"


def team_color(scoreboard, team, color):
    AutonaActions.instance.SetTeamColor(
        scoreboard, 1 if str(team) == "1" else 2, color)
    return "OK"


def set_display_option(data):
    """Toggle a scoreboard field's visibility from a remote client.

    One write, to the one place the option lives (headless core Step 3);
    boards that exist mirror the change from the state notification.
    Reported from the operation's own answer rather than assumed: the
    accepted key set is the schema's and Actions is the one that consults
    it, so a rejection there must not come back to the client as "OK". The
    legacy route's error string is this layer's contract.
    """
    option = (data or {}).get("option")
    if not AutonaActions.instance.SetDisplayOption(
            option, bool((data or {}).get("value"))):
        return f"ERROR: unknown display option {option}"
    return "OK"


def set_scores(scoreboard, team1, team2):
    # The v1 score:set: both *state* teams in one save cycle. Pure state, so
    # no GUI hop: the board follows from the state notification.
    with StateManager.SaveBlock():
        AutonaActions.instance.SetScore(scoreboard, 1, team1)
        AutonaActions.instance.SetScore(scoreboard, 2, team2)
    return "OK"


def apply_changes(scoreboard):
    # 反映: publish the board's pending (held) edits -- the commit the
    # desktop 反映 button / hotkey performs. Pure state.
    AutonaActions.instance.ApplyChanges(scoreboard)
    return "OK"


# ── readers ──────────────────────────────────────────────────────────────
# The game asset readers read the Qt-free catalog (ADR-005 S1b), each
# payload from one reference the catalog takes once, so a load or rescan
# landing mid-request cannot mix two of them.

def get_games():
    return GameAssetCatalog.instance.GamesPayload()


def get_current_game():
    return StateManager.Get("game")


def get_match_names():
    return {
        "match": AutonaLocaleHelper.matchNames,
        "phase": AutonaLocaleHelper.phaseNames
    }


def get_match_name_candidates(board=None):
    # What board `board`'s three editable combos offer, in their order
    # (without their blank first row): on boards 1/2 the loaded tournament's
    # pools and entrant rounds too, on the crew board -- and with no board --
    # the terms alone (core's MatchNameCandidates, ADR-005 S4x).
    return MatchNameCandidates.instance.Candidates(board)


def get_characters():
    return GameAssetCatalog.instance.CharactersPayload()


def get_variants():
    return GameAssetCatalog.instance.VariantsPayload()


def get_comms():
    return StateManager.Get("commentary")


def get_states(countryCode: str):
    # The country catalog, read on the calling thread (ADR-005 S1c). Each
    # region comes with its flag `asset`.
    return CountryCatalog.instance.States(countryCode)


def get_countries():
    # The country combo's rows, custom flags last (ADR-005 S4i: the Top 8
    # page's country picker), read on the calling thread like get_states.
    return CountryCatalog.instance.CountriesPayload()


def get_sets(args):
    provider = AutonaTournamentDataProvider.instance.GetProvider()
    if provider is None:
        return []
    if args.get('getFinished') is not None:
        return provider.GetMatches(getFinished=True)
    return provider.GetMatches(getFinished=False)


def get_match(setId=None):
    provider = AutonaTournamentDataProvider.instance.GetProvider()
    return provider.GetMatch(setId=int(setId))


def get_playerdb():
    # Same rule as the desktop name completers: only the loaded
    # tournament's entrants when one is loaded, the whole DB otherwise.
    # Clients receive this on (re)connect and on every db_updated. A deep
    # copy taken under the DB's lock: the entrants worker and the model
    # build change player dicts in place, and this is serialised (Flask /
    # Socket.IO) after the lock is released.
    return PlayerDB.instance.TournamentPlayers()


# ── the player list (ADR-005 S4m) ────────────────────────────────────────
# core's PlayerDirectory, on the calling thread. A save is not run on the
# board host: nothing here touches a slot, and the database's write goes with
# no lock held (PlayerService.SaveRecordToDB).

def get_players():
    """{"game", "players"}: the codename the rows' mains are of, and the rows."""
    return PlayerDirectory.Registered()


def get_player_entrants():
    """{"game", "entrants"}, as get_players."""
    return PlayerDirectory.Entrants()


def save_player(fields):
    """{"key", "created"}, or what PlayerDirectory.Save refused."""
    return PlayerDirectory.Save(fields)


def delete_player(key):
    return PlayerDirectory.Delete(key)


# ── the caster line-up (ADR-005 S3e-2) ───────────────────────────────────
# core's CommentaryService, on the calling thread: no panel is built to
# serve them (the Commentary panel renders the state when it exists). The
# caster number arrives 1-based, as the route has it.

def _caster_slot(index):
    """The caster slot for a 1-based caster number, or None. A number that
    is not one raises (ValueError), as the operations always did."""
    slot = int(index)
    if not 1 <= slot <= len(comms.SLOTS):
        return None
    return str(slot)


def set_commentary_data(index, data):
    slot = _caster_slot(index)
    if slot is None:
        return f"ERROR : index must be 1..{len(comms.SLOTS)}"
    # A `data` that is not a dict is ignored (logged), and still "OK": the
    # answer has never depended on it.
    CommentaryService.ApplyPlayerData(slot, data)
    return "OK"


def load_commentator_from_tag(index, tag, no_mains=False):
    # `no_mains` is accepted for the route's sake; casters carry no
    # characters. "OK" once the caster is filled, "ERROR" when the local
    # player DB has no such tag -- until S3e-2 both answered None (the
    # legacy route a 500, the Socket.IO ack null), and the range checked
    # was 1..8 against twelve slots.
    slot = _caster_slot(index)
    if slot is None:
        return f"ERROR : index must be 1..{len(comms.SLOTS)}"
    if not CommentaryService.LoadFromTag(slot, tag):
        return "ERROR"
    return "OK"


# The Commentary page's (ADR-005 S4b): the core operations the page
# asks for, one each. The v1 routes validate; these apply. Slots are
# CommentaryModel.SLOTS strings ("1".."12").

def get_commentary_options():
    """What the page offers: the role candidates (free text; only
    suggestions), the icon stems with their labels, the slots and how many
    may be shown. From core's constants, so the page holds no copy."""
    return {
        "roles": list(comms.ROLES),
        "icons": [{"id": stem, "label": label}
                  for stem, label in comms.ICONS.items()],
        "slots": list(comms.SLOTS),
        "maxDisplay": comms.MAX_DISPLAY,
    }


def update_caster(slot, fields):
    """Write `fields` (field -> value, already validated) to caster `slot`
    in one save cycle. False, writing none of them, when they would show a
    caster past MAX_DISPLAY others; True otherwise (an unchanged field is
    not written, as in the panel).

    The lock is taken before the save block (agent-pitfalls #17): the
    publish that closes the block then runs in the same hold, so no other
    thread's write joins this cycle between these writes and their
    publish. The check and the writes hold the lock together, so two
    requests showing a caster at once cannot both pass the check past
    MAX_DISPLAY."""
    with StateManager.lock:
        with StateManager.SaveBlock():
            if fields.get("display") and not CommentaryService.CanShow(slot):
                return False
            for field, value in fields.items():
                CommentaryService.SetField(slot, field, value)
    return True


def reorder_casters(order):
    return CommentaryService.Reorder(order)


def apply_caster_player(slot, data):
    return CommentaryService.ApplyPlayerData(slot, data)


# ── the panels' layout selection and colours (ADR-005 S4b) ─────────────
# Settings, not state: what the Qt panels keep in settings.json, under the
# same keys and in the same form.

def get_selected_layout(kind):
    return LayoutCatalog.SelectedLayout(kind)


def select_layout(kind, layoutId):
    return LayoutCatalog.SelectLayout(kind, layoutId)


def get_board_files():
    return LayoutCatalog.BoardFiles()


def reveal_board_file(target, designId=None):
    return LayoutCatalog.RevealBoardFile(target, designId)


def set_feature_colors(feature, colors):
    """Set one feature's theme / accent colours (ColorService kind ->
    "#rrggbb"), both in one save cycle when both come -- the Qt card's
    "match the tournament" writes the two."""
    with StateManager.SaveBlock():
        for kind, color in colors.items():
            ColorService.SetFeatureColor(feature, kind, color)


# ── 統計情報表示設定 (ADR-005 S4l) ─────────────────────────────────────
# The Stats Display Settings page's: core's StatsOverlays (the table and the
# kept selection, settings.json) and the Companion macro's request.

STATS_REFRESH_METHOD = "POST"
STATS_REFRESH_PATH = "/scoreboards/1/stats:refresh"


def get_stats_overlays():
    from .Helpers.AutonaNetworkHelper import GetLocalIP
    from .AutonaWebServer import WebServer
    # What the server really listens on (a change in the settings takes
    # effect at the next start); the settings only when none runs.
    bound = WebServer.Listening()
    if bound is None:
        bound = (SettingsManager.Get("general.webserver_port",
                                     DEFAULT_WEBSERVER_PORT),
                 SettingsManager.Get("general.webserver_listen_lan", False))
    port, lan = bound
    host = GetLocalIP() if lan else "127.0.0.1"
    return {
        "overlays": StatsOverlays.Overlays(),
        "selected": StatsOverlays.Selected(),
        # Built here, not by the page: only the server knows the LAN IP.
        "refresh": {
            "method": STATS_REFRESH_METHOD,
            "path": STATS_REFRESH_PATH,
            "url": f"http://{host}:{port}/api/v1"
                   + STATS_REFRESH_PATH,
        },
    }


def select_stats_overlay(key):
    return StatsOverlays.Select(key)


# ── 設定 (ADR-005 S4ae) ──────────────────────────────────────────────────
# The settings page's: core's AppSettings (the general switches and colours,
# the web server's port and LAN access, the languages) and HotkeyRules, in
# both shells, on the calling thread (settings.json, no board). A hotkey
# save re-registers through StateManagerBridge.hotkeys_changed where a shell
# registers them (the desktop's, bootstrap_gui()). The v1 routes answer the
# refusals; these apply.

def get_settings():
    """{"general", "network", "language", "hotkeys"}: an allow-list
    (AppSettings.Describe + HotkeyRules.Describe), never the access token."""
    return dict(AppSettings.instance.Describe(),
                hotkeys=HotkeyRules.Describe())


def update_general_settings(fields):
    return AppSettings.instance.UpdateGeneral(fields)


def update_network_settings(fields):
    return AppSettings.instance.UpdateNetwork(fields)


def update_language_settings(fields):
    return AppSettings.instance.UpdateLanguage(fields)


def update_hotkey_settings(enabled=None, keys=None):
    return HotkeyRules.Update(enabled=enabled, keys=keys)


# ── 配信設定 (ADR-005 S4e) ─────────────────────────────────────────────
# The Stream page's: core's StreamService (and AutonaActions for the two
# state fields), in both shells, on the calling thread -- there is no board.
# The v1 routes validate; these apply.

def update_stream(fields):
    """Write the stream destination and / or the broadcast title (`url` /
    `title` -> str, already validated) in one save cycle. The lock before
    the save block, as update_caster takes it (agent-pitfalls #17)."""
    with StateManager.lock:
        with StateManager.SaveBlock():
            if "url" in fields:
                AutonaActions.instance.SetStreamUrl(fields["url"])
            if "title" in fields:
                AutonaActions.instance.SetStreamTitle(fields["title"])


def get_stream_channels():
    return StreamService.instance.Channels()


def add_stream_channel(name, url):
    return StreamService.instance.AddChannel(name, url)


def update_stream_channel(index, name, url):
    return StreamService.instance.UpdateChannel(index, name, url)


def delete_stream_channel(index):
    return StreamService.instance.DeleteChannel(index)


def import_stream_org_asset(kind, data):
    return StreamService.instance.ImportOrgAsset(kind, data)


def reset_stream_org_assets():
    StreamService.instance.ResetOrgAssets()


def get_stream_thumbnail_options():
    return StreamService.instance.ThumbnailOptions()


def set_stream_thumbnail_options(changes):
    return StreamService.instance.SetThumbnailOptions(changes)


def generate_stream_thumbnail():
    return StreamService.instance.GenerateThumbnail()


# ── the match thumbnail (ADR-005 S4o) ────────────────────────────────────

def describe_thumbnail():
    """The settings, the game they are for, the templates, the video title
    and whether this shell can draw (canRender)."""
    return dict(ThumbnailService.instance.Describe(),
                canRender=startImageRender is not None)


def get_thumbnail_settings():
    return ThumbnailService.instance.Settings()


def update_thumbnail_settings(changes):
    return ThumbnailService.instance.Update(changes)


def reset_thumbnail_settings():
    ThumbnailService.instance.Reset()


def import_thumbnail_background(data):
    return ThumbnailService.instance.ImportBackground(data)


def preview_thumbnail():
    """Draw the preview image in the background: the revision of the run that
    will. Only a shell with a renderer can (NotAvailable: 503)."""
    if startImageRender is None:
        raise NotAvailable()
    return ThumbnailService.instance.Preview()


def generate_thumbnail(board=1):
    """Draw and save board `board`'s (1 / 2) thumbnail in the background: the
    revision of the run that will, or BUSY. Only a shell with a renderer can
    (NotAvailable: 503)."""
    if startImageRender is None:
        raise NotAvailable()
    return ThumbnailService.instance.Generate(board)


def get_thumbnail_preview():
    """The preview image's bytes (a PNG); None while there is none."""
    return ThumbnailService.instance.PreviewImage()


# ── the tournament result (ADR-005 S4q) ──────────────────────────────────

def describe_result():
    """What the page shows (ResultService.Describe) and whether this shell can
    draw (canRender)."""
    return dict(ResultService.instance.Describe(),
                canRender=startImageRender is not None)


def get_result_post():
    return ResultService.instance.PostText()


def get_result_layout():
    return ResultService.instance.Layout()


def set_result_layout(layout):
    return ResultService.instance.SetLayout(layout)


def update_result_player(slot, fields):
    return results.UpdatePlayer(slot, fields)


def update_result_team(slot, fields):
    return results.UpdateTeam(slot, fields)


def reorder_result(kind, order):
    return results.Reorder(kind, order)


def clear_result(kind):
    return results.Clear(kind)


def load_result_standings(confirmed):
    return ResultService.instance.LoadStandings(confirmed)


def save_result_to_db():
    return ResultService.instance.SaveToDb()


def preview_result():
    """Draw the preview image in the background: the revision of the run that
    will. Only a shell with a renderer can (NotAvailable: 503)."""
    if startImageRender is None:
        raise NotAvailable()
    return ResultService.instance.Preview()


def generate_result():
    """Draw and save the result in the background: the revision of the run
    that will, BUSY or NO_DATA. Only a shell with a renderer can (NotAvailable:
    503)."""
    if startImageRender is None:
        raise NotAvailable()
    return ResultService.instance.Generate()


def get_result_preview():
    """The preview image's bytes (a PNG); None while there is none."""
    return ResultService.instance.PreviewImage()


def start_image_render(kind, payload, done):
    """Hand an image to the shell's renderer (`render` of core's
    ThumbnailService and ResultService). Waits for the GUI thread to take it,
    not for the image."""
    if startImageRender is None:
        raise NotAvailable()
    return _on_gui_thread(lambda: startImageRender(kind, payload, done),
                          "the image render")


# ── the tournament ───────────────────────────────────────────────────────

def load_tournament(url=None):
    """Set the tournament at `url`, or clear it when empty (the legacy
    route and Socket.IO). Through core's TournamentService (ADR-005 S4f),
    like every other entrance: the URL normalised and saved there, the
    loaded URL not re-fetched, the media not fetched, and a clear forgets
    the selected game. A URL that is not a start.gg event / admin URL
    changes nothing (it used to be saved and then load no tournament).
    "OK" whichever it was, as it always answered; on the GUI thread the
    load is done when this returns."""
    logger.info(f"Tournament URL requested: {url}")
    if url is None or url == "":
        TournamentService.instance.ClearTournament()
        return "OK"
    TournamentService.instance.SetTournament(url)
    return "OK"


def set_tournament(url, force=False, fetchMedia=False):
    """v1's POST /tournament (ADR-005 S4g): set the tournament at `url` (a
    start.gg event / admin URL, already checked), the loaded URL re-fetched
    only with `force`, its icon / banner fetched once loaded with
    `fetchMedia`; or clear it when `url` is empty. On the calling thread,
    which publishes the request's tournamentInfo.load before this returns
    (the load itself runs on the main thread). Its revision -- or a later
    request's, when another one came in between: the one whose result the
    state then shows anyway."""
    logger.info(f"Tournament URL requested: {url}")
    if not url:
        TournamentService.instance.ClearTournament()
    else:
        TournamentService.instance.SetTournament(
            url, force=force, fetchMedia=fetchMedia)
    return TournamentService.Load()["revision"]


def clear_tournament():
    """Unload the tournament (v1's DELETE /tournament, ADR-005 S4g): core's
    ClearTournament, which forgets the selected game too. The revision of
    tournamentInfo.load it was published at, as set_tournament's."""
    TournamentService.instance.ClearTournament()
    return TournamentService.Load()["revision"]


def get_tournament():
    """The tournament URL last set, whether a provider is there, and how
    the newest set / clear is going: tournamentInfo.url / load as published
    (TournamentService.Load()). `loaded` is the provider's presence, not a
    load that worked (S4g review L6): a tournament whose load failed has
    one too -- `status` tells how it went."""
    return {
        "url": TournamentService.TournamentUrl(),
        "loaded": AutonaTournamentDataProvider.instance.GetProvider()
        is not None,
        **TournamentService.Load(),
    }


def update_tournament_info(fields):
    """Write some of the six fields (v1 field name -> value, already
    validated: the three texts, numEntrants, startAtDate, and the 表示大会名)
    in one save cycle, through TournamentInfoService. The lock before the save
    block, as update_stream takes it (agent-pitfalls #17). False, with nothing
    written, when the day cannot be made a time on this PC
    (TournamentInfoService.SetDate): checked first. The 表示大会名 goes last,
    whatever the request's order: a tournament name in the same request may
    carry over into it (FollowDisplayName), and what was asked for wins."""
    service = TournamentInfoService.instance
    with StateManager.lock:
        day = fields.get(schema.TOURNAMENT_START_AT_DATE_KEY)
        if day is not None and service.DayTime(day) is None:
            return False
        with StateManager.SaveBlock():
            for field, value in fields.items():
                if field in schema.TOURNAMENT_TEXT_FIELDS:
                    service.SetTextField(field, value)
                elif field in schema.TOURNAMENT_NUMBER_FIELDS:
                    service.SetNumberField(field, value)
                elif field == schema.TOURNAMENT_START_AT_DATE_KEY:
                    service.SetDate(schema.TOURNAMENT_START_AT_KEY, value)
            if schema.TOURNAMENT_DISPLAY_NAME_KEY in fields:
                service.SetDisplayName(
                    fields[schema.TOURNAMENT_DISPLAY_NAME_KEY])
    return True


def import_tournament_media(kind, data):
    return TournamentMediaService.instance.ImportMedia(kind, data)


def reset_tournament_media():
    return TournamentMediaService.instance.ResetMedia()


def fetch_tournament_media():
    return TournamentMediaService.instance.FetchMedia()


def set_tournament_colors(colors):
    """Set the tournament's theme / accent colours (ColorService kind ->
    "#rrggbb"), both in one save cycle when both come. Each clears the
    feature overrides that followed it (SetTournamentColor's cascade), as
    the Qt card did."""
    with StateManager.SaveBlock():
        for kind, color in colors.items():
            ColorService.SetTournamentColor(kind, color)


def update_bracket(done=None):
    """Re-import the Top 8 from Start.gg (core: Top8Service, ADR-005 S3e-8).

    Starts the import on the provider's worker and returns at once, in
    either shell (no panel is involved; the Top 8 page renders it). Answers
    what RefreshFromProvider answers (``Top8Service.STARTED`` / ``BUSY`` /
    ``NO_TOURNAMENT``): /api/v1 maps it to 202 / 409; the legacy route and
    the Socket.IO event answer "OK" whichever it was, as they always did.
    `done()` as in RefreshFromProvider.
    """
    return Top8Service.RefreshFromProvider(done=done)


# ── The tournament's match list (ADR-005 S4k) ────────────────────────────
# The Statistics page's この大会の試合一覧: core's AnalysisService, in both
# shells, on the calling thread. Its progress is the state's `analysis`;
# the rows are only read here (too many for the state, S4j decision 2).

def refresh_analysis():
    """Start fetching the loaded tournament's match list
    (AnalysisService.Refresh): ``analysis.STARTED`` / ``BUSY`` /
    ``NO_TOURNAMENT`` (also when the shell built no service). /api/v1 maps
    it to 202 / 409."""
    service = AnalysisService.instance
    if service is None:
        return analysis.NO_TOURNAMENT
    return service.Refresh()


def get_analysis():
    """`analysis`'s status, and the rows of the newest fetch that made them
    with the URL of the tournament they are from -- when that tournament is
    the one loaded now; else no rows and no URL. The service drops its rows
    when the tournament changes (Forget); comparing the URL as well keeps a
    reader from ever being handed another tournament's rows.

    The rows, the provider and the status are read in one hold of
    StateManager.lock: the service changes its rows and publishes
    `analysis` in one hold of it (_Publish / Forget), so the three are of
    the same moment -- not a fetch's rows with the status before it ended,
    or idle with the rows Forget is about to drop. The lock order is the
    service's: StateManager.lock, then its own (Result)."""
    service = AnalysisService.instance
    with StateManager.lock:
        result = service.Result() if service is not None else {
            "url": "", "rows": [], "revision": None}
        provider = AutonaTournamentDataProvider.instance.GetProvider()
        current = (getattr(provider, "url", None)
                   if provider is not None else None)
        status = AnalysisService.Progress()["status"]
    if not result["url"] or result["url"] != current:
        result = {"url": "", "rows": []}
    return {"status": status, "url": result["url"], "rows": result["rows"]}


# ── the set selection's lists (ADR-005 S4v) ──────────────────────────────
# core's SetSelectionService, in both shells, on the calling thread. Their
# progress is the state's `set_selection`; the rows are only read here. Which
# set is then loaded and which station followed is below ("set loading and
# following").

def refresh_tournament_phases():
    """Start fetching the tournament's phases (selection.STARTED / BUSY /
    NO_TOURNAMENT, also when the shell built no service). /api/v1 maps it to
    202 / 409."""
    service = SetSelectionService.instance
    if service is None:
        return selection.NO_TOURNAMENT
    return service.RefreshPhases()


def refresh_tournament_stations():
    """Start fetching the tournament's stations and streams, likewise."""
    service = SetSelectionService.instance
    if service is None:
        return selection.NO_TOURNAMENT
    return service.RefreshStations()


def refresh_tournament_sets(phase=None, finished=False):
    """Start fetching the set list of `phase`: those, or PHASE_REQUIRED /
    UNKNOWN_PHASE (SetSelectionService.RefreshSets)."""
    service = SetSelectionService.instance
    if service is None:
        return selection.NO_TOURNAMENT
    return service.RefreshSets(phase, finished)


def cancel_tournament_sets():
    """Stop the set list's fetch: whether one was running."""
    service = SetSelectionService.instance
    return service is not None and service.CancelSets()


def _get_selection_list(kind):
    """List `kind`'s status and rows -- the rows, with when they were fetched,
    only when they are of the tournament loaded now. Read in one hold of
    StateManager.lock, as get_analysis' are: the service changes its rows and
    publishes their status in one hold of it."""
    service = SetSelectionService.instance
    with StateManager.lock:
        result = service.Result(kind) if service is not None else None
        provider = AutonaTournamentDataProvider.instance.GetProvider()
        current = (getattr(provider, "url", None)
                   if provider is not None else None)
        status = SetSelectionService.Progress(kind)["status"]
    if result is None or not result["url"] or result["url"] != current:
        return {"status": status, "url": "", "fetchedAt": None, "rows": [],
                "query": None}
    return {"status": status, "url": result["url"],
            "fetchedAt": result["fetchedAt"], "rows": result["rows"],
            "query": result["query"]}


def get_tournament_phases():
    answer = _get_selection_list(selection.PHASES)
    del answer["query"]
    return answer


def get_tournament_stations():
    answer = _get_selection_list(selection.STATIONS)
    del answer["query"]
    return answer


def get_tournament_sets():
    """As the others, and the `query` the rows were asked for: the window's
    cache brings back its phase and its checkbox with its rows."""
    return _get_selection_list(selection.SETS)


# ── Top 8 (ADR-005 S4i) ──────────────────────────────────────────────────
# The Top 8 page's: core's Top8Service, in both shells, on the calling
# thread -- there is no board. The v1 routes validate the shapes; these
# apply.

def update_top8_player(slot, fields):
    """Write some of one Top 8 player's fields in one save cycle (core:
    Top8Service.UpdatePlayer): name / sponsor (str), country (a code -- a
    custom flag's name -- or None), character ((en_name, skin row,
    codename or None) or None), already shaped by the route. Every
    identifier is resolved against one reference to each catalog before
    anything is written; nothing is written when one names nothing or the
    character was picked on another game's list. Answers which
    (Top8Service.UNKNOWN_* / OTHER_GAME), else None."""
    return Top8Service.UpdatePlayer(slot, fields)


def set_top8_phase_label(text):
    """The overlay's caption; empty is the default ("TOP 8"). Answers what
    was published."""
    return Top8Service.SetPhaseLabel(text)


def apply_top8_scores(scores):
    """Apply some matches' scores ({match id: [s1, s2]}, already
    validated): a merge, the other matches kept (Top8Service.ApplyScores).
    Answers the matches published, or None when core refused them."""
    return Top8Service.ApplyScores(scores)


def reset_top8_scores():
    """Every score and result back to none (全リセット). Answers the
    matches published."""
    return Top8Service.ResetMatches()


# ── the game ─────────────────────────────────────────────────────────────

def set_game(data):
    # The legacy route's answer is "OK" once the load's attempt is over: the
    # game in the state, its models rebuilt and what follows a load
    # (onLoad, the roster) run -- or the attempt skipped or failed (logged),
    # answered "OK" too when this ran on the GUI thread, or not run because
    # the app is shutting down (that wait used to hang). A newer request
    # overtaking it: once that one's attempt is over. The synchronous load below waits on the calling thread (ADR-005
    # S3f-4; this ran on the GUI thread until then): on the desktop the
    # request thread waits for the asset adapter's loader, which marshals
    # its models onto the GUI thread; headless the load runs here and hands
    # what follows it to the main thread. Not serialized with the board
    # operations any more: one can run between the selection and what
    # follows it, as an async load (the GAME menu) always allowed.
    set_codename = data.get("codename")
    if set_codename not in GameAssetCatalog.instance.GameCodenames():
        return f"Could not find game {set_codename}"
    if loadGameByCodename is None:
        logger.error(f"no game loader registered; {set_codename} not loaded")
        return f"Could not find game {set_codename}"

    # By codename, not by its position in the scan, which the load would
    # re-read: a rescan in between could load another game.
    loadGameByCodename(
        set_codename, async_mode=False,
        mods_active=data.get("mods_active", False), mods_reload_mode=True)
    return "OK"


def load_game(codename, mods_active=False, done=None):
    """Start loading `codename` (reloading it if it is the loaded one) in
    the background (the desktop's load job, the headless loader's thread).
    `done()` is called once the attempt is over, whichever way it went
    (/api/v1's in-progress flag)."""
    if loadGameByCodename is None:
        logger.error(f"no game loader registered; {codename} not loaded")
        if done is not None:
            done()
        return
    loadGameByCodename(
        codename, async_mode=True, mods_active=mods_active,
        mods_reload_mode=True, done=done)


# ── the battle format (ADR-005 S4x) ──────────────────────────────────────

def get_battle_format():
    """{"format", "board"}: the battle format in force ("singles" / "team" /
    "crew", AutonaActions.ResolveBattleFormat -- the legacy team_mode
    fallback included) and the board it shows (ActiveBoard, /api/v1/health's
    activeBoard). A setting: read on the calling thread."""
    actions = AutonaActions.instance
    fmt = actions.ResolveBattleFormat()
    return {"format": fmt, "board": AutonaActions.FORMAT_BOARDS[fmt]}


def set_battle_format(fmt):
    """Switch the battle format (AutonaActions.SetBattleFormat, the sidebar's
    operation): True if it switched, False for the format in force (nothing
    written, nothing notified). ValueError for an unknown format, before
    anything is written. Run where the board operations run: on the desktop
    that is the GUI thread, so the notice reaches the window there and the
    format's board is built (and shown) before this returns."""
    return boardHost.Run(lambda: AutonaActions.instance.SetBattleFormat(fmt))


# ═══════════════════════════════════════════════════════════════════════
# The S2 operations (see the module docstring)
# ═══════════════════════════════════════════════════════════════════════

def set_route(scoreboard, bestOf=None, phase=None, pool=None, match=None,
              players=None, characters=None, losers=None, team=None,
              teamName=None):
    """The legacy scoreboard<n>-set route and the Socket.IO set event (and
    /api/v1's PATCH set): each field given, in the order below, on boards
    1/2 -- the crew board where it has the field. "OK".

    A board other than 1/2/3 has none of them: its set fields (best of,
    phase, pool, match, losers) and counts are refused before anything is
    written (NotAvailable: 503, legacy / Socket.IO / v1 alike), and a team
    name for it writes nothing, as before. The board is not asked for
    either: the desktop's host would log an unknown scoreboard.
    """
    host = boardHost

    def run():
        # The counts are core's in both shells: boards 1/2's roster
        # (AutonaActions.SetRoster, ADR-005 S3e-6 -- the board's two
        # spinboxes until then, so a shell with no board refused them), the
        # crew board's member count (S3e-2). The board is prepared first (on
        # the desktop: made again only if core does not have it, ADR-005
        # S4ac-1; built until then, so the change was held for 反映 and not
        # wiped by the seed of a later build).
        crew = _is_crew(scoreboard)
        if players is not None or characters is not None:
            # Boards 1/2 and the crew board have counts; any other is
            # refused before anything is written, as it was when the counts
            # were the board's widgets (503, legacy / Socket.IO / v1 alike)
            # -- not answered OK having changed nothing.
            _require_board(scoreboard)
            host.Prepare(scoreboard)

        # best-of=<Best Of Amount>
        if bestOf is not None:
            _apply_set_fields(host, scoreboard, {'bestOf': int(bestOf)})

        # phase=<Phase Name>
        if phase is not None:
            _apply_set_fields(host, scoreboard, {'tournament_phase': phase})

        # pool=<Pool Name>
        if pool is not None:
            _apply_set_fields(host, scoreboard, {'tournament_pool': pool})

        # match=<Match Name>
        if match is not None:
            _apply_set_fields(host, scoreboard, {'round_name': match})

        # players=<Amount of Players> (the crew board: its member count).
        # Each count is a save cycle of its own, as each spinbox's was.
        if players is not None:
            if crew:
                CrewBattleService(CREW_BOARD).SetMemberCount(int(players))
            else:
                _set_players(host, scoreboard, int(players))

        # characters=<Amount of Characters> (none on the crew board; the
        # number is still parsed, as the board's spinbox parsed it)
        if characters is not None:
            count = int(characters)
            if not crew:
                _set_characters(host, scoreboard, count)

        # losers=<True/False>&team=<Team Number>
        # Set-shaped: `team` is the set's team, which a swap moves to the
        # other column (SetLoadService.ApplyLosers).
        if losers is not None:
            _apply_set_fields(host, scoreboard, {
                'team' + str(team) + 'losers':
                    False if losers.lower() == 'false' else True})

        # teamName=<Name>&team=<Team Number> (team-mode boards)
        # The literal column (no swap flip), as the operator typing the name
        # into it: the name and the crest that follows it, written only when
        # the name changes. Boards 1/2 only -- the crew board has never had
        # the column this addressed.
        if teamName is not None and team is not None:
            board = _board(scoreboard)
            if crew or board in AutonaActions.SCORE_BOARDS:
                host.Prepare(scoreboard)
            idx = int(team)
            if idx in (1, 2) and board in AutonaActions.SCORE_BOARDS:
                with StateManager.SaveBlock():
                    if AutonaActions.instance.SetTeamName(
                            scoreboard, idx, teamName):
                        SetLoadService(board).PublishTeamLogo(idx, teamName)
        return "OK"

    return host.Run(run)


def _set_players(host, scoreboard, players):
    """set_route's players on board 1/2: the operation of the SCORE page's
    players field (AutonaActions.SetPlayersPerTeam; the Qt board's PLAYERS
    spinbox's until ADR-005 S4ac-3), with the characters
    per player core keeps for a board with no character slot left. Players=0,
    then characters=2, then players=2 is two players a team with two
    characters each. The count kept was the board host's (headless) and the
    board's hidden spinbox (desktop) until ADR-005 S4x: one memory now, in
    both shells."""
    AutonaActions.instance.SetPlayersPerTeam(scoreboard, players)


def _set_characters(host, scoreboard, count):
    """set_route's characters on board 1/2: the character slots of every
    player, or -- on a board with none -- the count its next players get
    (AutonaActions.SetCharactersPerPlayer, the SCORE page's characters
    field's operation; the Qt board's spinbox's until ADR-005 S4ac-3)."""
    AutonaActions.instance.SetCharactersPerPlayer(scoreboard, count)


def _apply_set_fields(host, scoreboard, payload):
    """One set_route field, as the set payload it has always been.

    On boards 1/2 that payload is applied by core (ADR-005 S2d), as the
    board's ChangeSetData applied it: the match bar fields and the English
    round text (blanked by every payload that carries none --
    SetLoadService.ApplyMatchInfo), a best of (AutonaActions.SetBestOf, S2e;
    0 is not carried -- SetLoadService.BestOfFromSetData) and a losers flag
    (ApplyLosers). The board is prepared first, so on the desktop the write
    waits for 反映 like any other. The crew board reads the payload by its
    own rules (CrewBattleService.ApplySetData, S3e-2). Any other board has
    none to apply it to: refused before anything is written (NotAvailable),
    as the counts are. It was emitted on that board's signal: the headless host
    refused it the same way, and the desktop failed on the board it does
    not have (500).
    """
    board = _require_board(scoreboard)
    host.Prepare(scoreboard)
    if board == CREW_BOARD:
        CrewBattleService(CREW_BOARD).ApplySetData(payload)
        return
    setLoad = SetLoadService(board)
    with StateManager.SaveBlock():
        setLoad.ApplyMatchInfo(payload)
        bestOf = SetLoadService.BestOfFromSetData(payload)
        if bestOf is not None:
            AutonaActions.instance.SetBestOf(scoreboard, bestOf)
        setLoad.ApplyLosers(payload)


def set_team_losers(scoreboard, team, value):
    """The v1 counterpart of `set_route`'s losers (ADR-005 S2b).

    `team` is the *state* team: AutonaActions.SetTeamLosers, which also puts
    the " [L]" on that team's player names -- not set_route's set-shaped
    `team{N}losers` payload, which lands on the other side of a swapped
    board. What that payload did besides is kept, because it is visible
    from outside: the board is prepared first (built and held on the
    desktop), and the match-info half of the payload blanks match_en for a
    payload without round_name_en -- also when the flag does not change.
    Applied first, in the same save block: one save cycle, as before.
    """
    host = boardHost

    def run():
        board = _board(scoreboard)
        if board not in AutonaActions.SCORE_BOARDS:
            return f"ERROR: scoreboard {scoreboard} is not available"
        host.Prepare(scoreboard)
        with StateManager.SaveBlock():
            SetLoadService(board).ApplyMatchInfo({})
            AutonaActions.instance.SetTeamLosers(scoreboard, team, value)
        return "OK"

    return host.Run(run)


def swap_teams(scoreboard):
    # A 1v1 / team board's swap is a state operation (AutonaActions.SwapTeams,
    # ADR-005 S2c); the crew board's is CrewBattleService.SwapTeams (S3e-2).
    # The flag is `score.{n}.teamsSwapped` on all three (the crew board's since
    # S4w). Either way the board lets the field being typed in finish first,
    # so the text travels with the swap as it did when the widget swapped its
    # rows. Any other board: NotAvailable (`_require_board`).
    host = boardHost

    def run():
        board = _require_board(scoreboard)
        host.Prepare(scoreboard)
        host.CommitEdit(scoreboard)
        if board == CREW_BOARD:
            CrewBattleService(CREW_BOARD).SwapTeams()
        else:
            AutonaActions.instance.SwapTeams(scoreboard)
        return "OK"

    return host.Run(run)


def _swap_flag(board):
    """Whether board `board` (1/2/3) is swapped, live."""
    return bool(StateManager.Get(f"score.{board}.teamsSwapped", False))


def get_swap(scoreboard):
    # The live flag: a swap is reported as soon as it is made, before 反映
    # publishes it. The board is prepared first, as the route always did
    # (nothing to do: every board is made at launch, ADR-005 S4ac-1). Any
    # other board: NotAvailable, before anything is read.
    board = _require_board(scoreboard)
    boardHost.Prepare(scoreboard)
    return str(_swap_flag(board))


def get_scoreboard(scoreboard):
    # Read from the state. The board is prepared first: core made every
    # board at launch, whole, in both shells (AutonaActions.MakeBoards,
    # ADR-005 S4ac-1), so there is nothing to do (until ADR-005 S4ac-3 the
    # desktop made again a board core did not have). Until S4ac-1 the
    # desktop built the board here: building wrote its full initial subtree,
    # and an unbuilt board's was only the skeleton (StateSchema.SeedBoard).
    # Committed view: pending (not yet 反映-applied) desktop edits must not
    # leak to remote clients ahead of the delta stream. A board no shell
    # has is refused first (503, ADR-005 S4x review): the desktop built
    # nothing for it and the route answered 500 for the None it read.
    _require_board(scoreboard)
    boardHost.Prepare(scoreboard)
    return StateManager.GetCommitted(f'score.{_board(scoreboard)}')


def get_pending(scoreboard):
    """What board `scoreboard` (1/2/3) holds back, for a client drawing its
    form from the live values (the SCORE page, ADR-005 S4u): (held, dirty,
    the live subtree, the swap flag, the revision), all from one hold of the
    lock. Whether it is held is the shell's: the desktop's boards 1/2 are, the
    crew board and every headless board are not, and those have nothing
    pending. get_scoreboard stays the published view; the route answering this
    one is guarded, not READ. Prepared first, like get_scoreboard -- never
    with the lock held: the desktop's Prepare waited for the GUI thread when
    it could build (until ADR-005 S4ac-3)."""
    boardHost.Prepare(scoreboard)
    board = _board(scoreboard)
    prefix = f"score.{board}"
    with StateManager.lock:
        return (prefix in StateManager.heldPrefixes,
                StateManager.HasPendingChanges(prefix),
                copy.deepcopy(StateManager.Get(prefix)),
                _swap_flag(board),
                StateManager.revision)


def reset_scores(scoreboard):
    # Boards 1/2 go through Actions; a crew board's "reset score" restores
    # every member's stocks (CrewBattleService.ResetStocks, S3e-2). Any other
    # board: NotAvailable (`_require_board`).
    host = boardHost

    def run():
        board = _require_board(scoreboard)
        if board != CREW_BOARD:
            AutonaActions.instance.ResetScore(scoreboard)
            return "OK"
        host.Prepare(scoreboard)
        CrewBattleService(CREW_BOARD).ResetStocks()
        return "OK"

    return host.Run(run)


def _reset_match(scoreboard):
    # The operator's reset (scores + losers flags + round name / phase /
    # pool, ADR-005 S2d), not the set-load pre-pass ClearScore, which keeps
    # the match bar (S-17); then the best of, in a cycle of its own as the
    # spinbox's was. The crew board (3) answers False to the core operation:
    # its reset is full stocks and nobody playing plus the same match bar,
    # in one cycle, then its raw best of (S3e-2). The callers refused any
    # other board first (`_require_board`).
    if AutonaActions.instance.ResetMatch(scoreboard):
        AutonaActions.instance.SetBestOf(scoreboard, 0)
        return
    if _is_crew(scoreboard):
        crew = CrewBattleService(CREW_BOARD)
        with StateManager.SaveBlock():
            crew.ResetStocks()
            AutonaActions.instance.ClearMatchInfo(scoreboard)
        crew.ApplyBestOf(0)
        return
    _require_board(scoreboard)


def _clear_players(host, scoreboard):
    # Every member row of the crew board emptied (core's, S3e-2); a 1v1 /
    # team board's players through its host (BoardHost.ClearPlayers, which
    # refuses any other board). The field being typed in finishes first, as
    # boards 1/2's clear finishes it: the clear then takes the typed text
    # with it, instead of leaving it in the field to be written once the
    # operator leaves it.
    if _is_crew(scoreboard):
        host.Prepare(scoreboard)
        host.CommitEdit(scoreboard)
        CrewBattleService(CREW_BOARD).ClearCrew()
        return
    host.ClearPlayers(scoreboard)


def _reset_counts(host, scoreboard):
    # Back to one member a crew (core's, S3e-2), or one player a team and
    # one character a player (core's since S3e-6, through the host).
    if _is_crew(scoreboard):
        CrewBattleService(CREW_BOARD).SetMemberCount(1)
        return
    host.ResetCounts(scoreboard)


def reset_match(scoreboard):
    host = boardHost

    def run():
        _require_board(scoreboard)
        host.Prepare(scoreboard)
        _reset_match(scoreboard)
        return "OK"

    return host.Run(run)


def reset_players(scoreboard):
    host = boardHost

    def run():
        _clear_players(host, scoreboard)
        return "OK"

    return host.Run(run)


def clear_all(scoreboard):
    # Not AutonaActions.ClearBoard (the desktop's reset-all): this keeps the
    # team names (unless the roster shrinks to one player: SetRoster), and
    # the player / character counts change between the reset and the
    # players' clear -- in
    # this order, which decides what the mains end up as. On the crew board
    # the field being typed in finishes before anything is reset (see
    # `_clear_players`), so the match bar's reset takes its text too.
    host = boardHost

    def run():
        _require_board(scoreboard)
        host.Prepare(scoreboard)
        if _is_crew(scoreboard):
            host.CommitEdit(scoreboard)
        _reset_match(scoreboard)
        _reset_counts(host, scoreboard)
        _clear_players(host, scoreboard)
        return "OK"

    return host.Run(run)


def reset_all(scoreboard):
    """すべての入力値をリセット on board 1/2 (AutonaActions.ClearBoard): the
    players, the score, the match bar, the team names with their crests and
    the best of, in one save cycle; the counts stay. Not clear_all, which
    resets the counts and keeps the team names (ADR-005 S2 decision 3). What
    the desktop's button adds around it, through the host: the field being
    typed in finishes first, so the clear takes its text; the cards drop what
    was typed into their combos; and the set loaded last is forgotten, so
    loading it again loads it."""
    host = boardHost

    def run():
        board = _board(scoreboard)
        host.Prepare(scoreboard)
        with StateManager.SaveBlock():
            host.CommitEdit(scoreboard)
            AutonaActions.instance.ClearBoard(board)
            for team in (1, 2):
                roster = StateManager.Get(
                    f"score.{board}.team.{team}.player")
                for slot in (roster if isinstance(roster, dict) else ()):
                    host.PlayerLoaded(board, team, int(slot), True)
            SetLoadService(board).lastSetSelected = None
        return "OK"

    return host.Run(run)


# ── the crew board's members (ADR-005 S3e-2) ─────────────────────────────
# Core's (CrewBattleService), run where the board operations run: on the
# desktop the write is made on the GUI thread, as the board's own inputs
# make theirs (the board prepared first; ADR-005 S4ac-1). Another board has
# no members, and answers "OK" having done nothing, as it always did
# (without reading the numbers). `team` and `member` are 1-based; on the crew board a number
# that is not one raises (ValueError), as the board's signal did.

def crew_member_stock(scoreboard, team, member, change):
    host = boardHost

    def run():
        host.Prepare(scoreboard)
        if _is_crew(scoreboard):
            CrewBattleService(CREW_BOARD).AdjustMemberStock(
                int(team), int(member), int(change))
        return "OK"

    return host.Run(run)


def crew_set_active(scoreboard, team, member):
    # 0 (or less) marks nobody as playing.
    host = boardHost

    def run():
        host.Prepare(scoreboard)
        if _is_crew(scoreboard):
            CrewBattleService(CREW_BOARD).SelectActiveMember(
                int(team), int(member))
        return "OK"

    return host.Run(run)


# /api/v1's, with the numbers and values already checked by the route (ADR-005
# S4w). The same values again write nothing.

def crew_member_count():
    """How many members the crew board shows: the state's count, or on a
    board core does not have (its launch failed; every board is made at
    launch since ADR-005 S4ac-1) the count making it will give it, its
    seed's. Read without building anything."""
    if not boardHost.Built(CREW_BOARD):
        return CrewBattleService.DEFAULT_MEMBER_NUM
    return CrewBattleService(CREW_BOARD).MemberNum()


def crew_update_member(team, member, fields):
    """Some of one member's `name` / `stock` / `active`, in one save cycle."""
    host = boardHost

    def run():
        host.Prepare(CREW_BOARD)
        CrewBattleService(CREW_BOARD).UpdateMember(team, member, **fields)

    host.Run(run)


def crew_update_set(fields):
    """Some of the board's `phase` / `pool` / `match` (the operations its
    combos call), `bestOf`, `players` and `teamName` ((team, name), its name
    field's operation), in one save cycle. Not set_route: that is the legacy
    reading of a set payload, which writes no team name on this board."""
    host = boardHost

    def run():
        host.Prepare(CREW_BOARD)
        crew = CrewBattleService(CREW_BOARD)
        actions = AutonaActions.instance
        with StateManager.SaveBlock():
            for field in schema.SCORE_MATCH_FIELDS:
                if field in fields:
                    actions.SetMatchField(CREW_BOARD, field, fields[field])
            if "bestOf" in fields:
                crew.ApplyBestOf(fields["bestOf"])
            if "players" in fields:
                crew.SetMemberCount(fields["players"])
            if "teamName" in fields:
                actions.SetTeamName(CREW_BOARD, *fields["teamName"])

    host.Run(run)


def crew_team_color(team, color):
    """One crew's colour, the operation its colour button calls. Unlike
    `team_color`, the board is prepared first (made again if core does not
    have it; until ADR-005 S4ac-1 built, as an unbuilt board's seed wiped the
    write), and a colour it already has is not written."""
    host = boardHost

    def run():
        host.Prepare(CREW_BOARD)
        with StateManager.lock:
            if StateManager.Get(
                    f"score.{CREW_BOARD}.team.{team}.color") != color:
                AutonaActions.instance.SetTeamColor(CREW_BOARD, team, color)

    host.Run(run)


def get_board_post(scoreboard):
    """Board `scoreboard`'s X post as its button builds it: {"text",
    "missing"} -- the crew board's CrewBattleService.PostText (ADR-005 S4w),
    boards 1/2's AutonaActions.PostText (S4x; the button adds the thumbnail
    on the clipboard). Reads the state, so no board is built for it. The
    route validated the board (1..3)."""
    if _is_crew(scoreboard):
        text, missing = CrewBattleService(CREW_BOARD).PostText()
    else:
        text, missing = AutonaActions.instance.PostText(scoreboard)
    return {"text": text, "missing": missing}


# ── the player slots of boards 1/2 (ADR-005 S3e-4) ───────────────────────
# Core's (PlayerService.ApplyData, S3e-3), run where the board operations
# run: on the desktop the load is made on the GUI thread (where the board's
# cards were, until ADR-005 S4ac-3: the host's PlayerLoaded). A slot is one
# the *state* has -- core seeds them as it makes the board
# (AutonaActions.SeedFreshBoard), at launch in both shells since ADR-005
# S4ac-1 -- so the range is checked the same way in both shells. The SCORE
# page draws the load from the state, like any write.

def _player_path(board, team, player):
    """The path of player `player` of state team `team` on board `board`
    (ints), or None when the state has no such slot."""
    if team not in (1, 2) or player < 1:
        return None
    path = f"score.{board}.team.{team}.player.{player}"
    if not isinstance(StateManager.Get(path), dict):
        return None
    return path


def _load_player(host, board, team, player, data, loadFromDB, clear,
                 no_mains=False):
    """Load `data` into the slot (an existing one: _player_path) and tell
    the host, inside the load's save block. The load asks for its
    statistics itself (PlayerService.Announce): `recent_sets` /
    `upset_factor` are written into the same cycle, as the cards' were.
    Raises what the load raises (the host is told all the same)."""
    service = PlayerService(_player_path(board, team, player))
    with StateManager.SaveBlock():
        try:
            service.ApplyData(data, loadFromDB, clear, no_mains)
        finally:
            host.PlayerLoaded(board, team, player, clear)


def update_player(scoreboard, team, player, data):
    """/api/v1's PATCH .../teams/{t}/players/{p}: the player write of the
    legacy update-team route without the set payload around it (which
    blanks match_en). `team` is the state team (no swap flip).

    True when applied; False when the state team has no such slot (the
    route's 404) -- checked after the board host's Prepare, since making a
    board core does not have is what writes its slots. An exception from the write propagates (500).
    One save cycle, `savePlayerToDb`'s state write included; its database
    write follows, outside the host (_save_record).
    """
    host = boardHost

    def run():
        board = _board(scoreboard)
        team_, player_ = int(team), int(player)
        if board not in AutonaActions.SCORE_BOARDS:
            return False, None
        host.Prepare(scoreboard)
        path = _player_path(board, team_, player_)
        if path is None:
            return False, None
        record = None
        with StateManager.SaveBlock():
            _load_player(host, board, team_, player_, data, True, False)
            if data.get("savePlayerToDb", False):
                record = PlayerService(path).PrepareSaveToDB()
        return True, record

    applied, record = host.Run(run)
    _save_record(record)
    return applied


def _edit_slot(scoreboard, team, player, edit):
    """`edit(service)` on the PlayerService of slot `player` of state team
    `team` on board 1/2, where the board operations run, the board prepared
    first (made again if core does not have it; ADR-005 S4ac-1); what it
    returns. NoSuchSlot when the state team has no such slot, checked after
    the preparation, since making a board is what writes its slots."""
    host = boardHost

    def run():
        board = _require_board(scoreboard)
        if board not in AutonaActions.SCORE_BOARDS:
            raise NotAvailable(f"scoreboard {scoreboard} has no player slots")
        host.Prepare(scoreboard)
        path = _player_path(board, int(team), int(player))
        if path is None:
            raise NoSuchSlot(
                f"scoreboard {board} team {team} has no player {player}")
        return edit(PlayerService(path))

    return host.Run(run)


def update_player_fields(scoreboard, team, player, texts,
                         country=NOT_SENT, region=NOT_SENT):
    """/api/v1's PATCH .../teams/{t}/players/{p}/fields (ADR-005 S4z): some
    of the card's fields, written as the card writes them
    (PlayerService.Edit: `texts` by state key, the country / region codes,
    NOT_SENT for one not sent). `team` is the state team. None, or Edit's
    refusal (nothing written). One save cycle, held on the desktop: no load,
    no database."""
    return _edit_slot(scoreboard, team, player,
                      lambda service: service.Edit(texts, country, region))


def update_player_character(scoreboard, team, player, row,
                            character=NOT_SENT, skin=NOT_SENT,
                            variant=NOT_SENT, game=None):
    """/api/v1's PATCH .../players/{p}/characters/{row} (ADR-005 S4z): one
    character row, picked as on the card (PlayerService.EditCharacter).
    `team` is the state team. None, or EditCharacter's refusal (nothing
    written)."""
    return _edit_slot(
        scoreboard, team, player,
        lambda service: service.EditCharacter(
            row, character, skin, variant, game))


def update_match(scoreboard, fields):
    """/api/v1's PATCH /scoreboards/{n}/match (ADR-005 S4z): some of board
    1/2's `phase` / `pool` / `match` (the operation the match bar's combos
    call: AutonaActions.SetMatchField, "" empties) and `bestOf` (its BEST OF
    spinbox's: SetBestOf), in one save cycle, held on the desktop. Not
    set_route: that is a set payload's reading (SetLoadService
    .ApplyMatchInfo), which empties `match_en` and reads a blank phase or
    round as "not carried"."""
    host = boardHost

    def run():
        board = _require_board(scoreboard)
        if board not in AutonaActions.SCORE_BOARDS:
            raise NotAvailable(f"scoreboard {scoreboard} has no match bar")
        host.Prepare(scoreboard)
        actions = AutonaActions.instance
        with StateManager.SaveBlock():
            for field in schema.SCORE_MATCH_FIELDS:
                if field in fields:
                    actions.SetMatchField(board, field, fields[field])
            if "bestOf" in fields:
                actions.SetBestOf(board, fields["bestOf"])

    host.Run(run)


def _save_record(prepared):
    """The player database half of `savePlayerToDb` (PlayerService
    .SaveRecordToDB) for the record a load prepared (None: none). Out of
    the host on purpose: the database's change relay waits for the main
    thread, which the headless host's lock is never held across (ADR-005
    S3e-4 review). The record is what the slot held inside the host, and
    it is numbered there: a save that arrives after a newer one of the same
    player is dropped."""
    if prepared is not None:
        PlayerService.SaveRecordToDB(prepared)


def set_team_data(scoreboard, team, player, data):
    """The legacy update-team route and the Socket.IO update_team event: a
    set payload carrying one player, `{"team", "player", "data"}`, applied
    as a set payload is (SetFollowService.ApplySetPayload).

    Its set half on boards 1/2 is the match information, which blanks
    match_en (the payload never carries round_name_en) -- kept, as visible
    from outside; on the crew board, its own reading of a payload, which
    finds nothing to write here (`_apply_set_fields`). The player half
    addresses the state team (no swap flip), and only when the payload
    names both a team and a player. Without the clear (an update), and
    with `savePlayerToDb` (its database write after the host, as
    update_player's). A slot the state does not have, or a load or a save
    that raises, is logged and ignored: "OK" whatever happened, as the
    route always answered. One save cycle.

    A board other than 1/2/3 has no set fields to apply the payload to: it
    is refused before anything is written and before the board is asked
    for (NotAvailable: 503, as `_apply_set_fields` refuses it; the
    desktop's host would log an unknown scoreboard).
    """
    host = boardHost
    payload = {"team": team, "player": player, "data": data}

    def fail(board, e):
        logger.error(f"update-team: scoreboard {board} team {team} "
                     f"player {player} not written: {e}")

    def run():
        board = _require_board(scoreboard)
        record = None
        # Built before the save block opens: the build is what holds the
        # board for 反映 (S2c 判断 7).
        host.Prepare(scoreboard)
        with StateManager.SaveBlock():
            _apply_set_fields(host, scoreboard, payload)
            if board in AutonaActions.SCORE_BOARDS and team and player:
                try:
                    team_, player_ = int(team), int(player)
                    path = _player_path(board, team_, player_)
                    if path is None:
                        raise IndexError(
                            f"team {team} has no player {player}")
                    _load_player(host, board, team_, player_, data, True,
                                 False)
                    if data.get("savePlayerToDb", False):
                        record = PlayerService(path).PrepareSaveToDB()
                except Exception as e:
                    fail(board, e)
        return board, record

    board, record = host.Run(run)
    try:
        _save_record(record)
    except Exception as e:
        fail(board, e)
    return "OK"


def load_player_from_tag(scoreboard, tag, team, player, no_mains=False):
    """Load the local player DB's `tag` into a slot, clearing it first:
    "OK", or "ERROR" when the DB has no such tag.

    `team` is the set's team: after a swap team 1 is shown on the right, so
    it lands on state team 2 (SetLoadService.DisplayTeam, from the state's
    `teamsSwapped`). As the route always did, numbers that are not numbers
    raise before the lookup, a slot the board does not have raises after a
    tag that was found (the legacy route's 500), and only boards 1/2 have
    slots -- the crew board raised too (it has no player cards). A board no
    shell has is refused before the lookup (NotAvailable: 503, since the
    ADR-005 S4x review; it raised as the crew board does).
    """
    return _load_player_record(
        scoreboard, lambda db: db.GetPlayerFromTag(str(tag)), team, player,
        no_mains)


def load_player_from_key(scoreboard, key, team, player, no_mains=False):
    """load_player_from_tag, of the record stored under `key` -- the local
    player DB's own key, sponsor and tag (PlayerDB.PlayerTag), matched
    exactly (ADR-005 S4z: v1's `:load` with a `key`). "ERROR" when the DB
    has no record under it (or what it has there is no record). `team` is
    the set's team, and the rest is the same, as load_player_from_tag's."""
    def lookup(db):
        record = db.Get(str(key))
        return record if isinstance(record, dict) else None

    return _load_player_record(scoreboard, lookup, team, player, no_mains)


def _load_player_record(scoreboard, lookup, team, player, no_mains):
    """load_player_from_tag / load_player_from_key: `lookup(db)` is the
    record (the live dict, or None), called under the DB's lock."""
    host = boardHost

    def run():
        # A board no shell has: 503 (ADR-005 S4x review); the crew board
        # raises, as it always did.
        board = _require_board(scoreboard)
        if board not in AutonaActions.SCORE_BOARDS:
            raise ValueError(f"scoreboard {scoreboard} has no player slots")
        host.Prepare(scoreboard)
        team_, player_ = int(team), int(player)
        # The live dict (PlayerDB.Get): copied under the lock it is written
        # under, as the load is not.
        db = PlayerDB.instance
        with db.lock:
            playerData = copy.deepcopy(lookup(db))
        if not playerData:
            return "ERROR"
        path = None
        if team_ in (1, 2):
            team_ = SetLoadService(board).DisplayTeam(team_)
            path = _player_path(board, team_, player_)
        if path is None:
            raise NoSuchSlot(
                f"scoreboard {board} team {team} has no player {player}")
        _load_player(host, board, team_, player_, playerData, True, True,
                     no_mains)
        return "OK"

    return host.Run(run)


def clear_player(scoreboard, team, player):
    """Empty one player slot of state team `team` on board 1/2, as the card's
    clear button does (PlayerService.ClearSlot, statistics included). False
    when the team has no such slot. The field being typed in finishes first,
    so the clear takes its text; the card then drops what was typed into its
    combos, as after any load that clears."""
    host = boardHost

    def run():
        board = _board(scoreboard)
        host.Prepare(scoreboard)
        host.CommitEdit(scoreboard)
        path = _player_path(board, team, player)
        if path is None:
            return False
        with StateManager.SaveBlock():
            try:
                PlayerService(path).ClearSlot()
            finally:
                host.PlayerLoaded(board, team, player, True)
        return True

    return host.Run(run)


def swap_players(scoreboard, team, first, second):
    """Exchange two player slots of state team `team` on board 1/2, as a
    card's move up / down does (AutonaActions.SwapPlayers). False when the
    team lacks either: SwapPlayers would move the slot it has into the gap.
    The field being typed in finishes first, so its text moves with the slot.
    """
    host = boardHost

    def run():
        board = _board(scoreboard)
        host.Prepare(scoreboard)
        host.CommitEdit(scoreboard)
        with StateManager.lock:
            if (_player_path(board, team, first) is None
                    or _player_path(board, team, second) is None):
                return False
            AutonaActions.instance.SwapPlayers(board, team, first, second)
        return True

    return host.Run(run)


# ── the statistics of boards 1/2 (ADR-005 S3e-5) ─────────────────────────
# Core's (StatsService), run where the board operations run: prepared
# first (a board core does not have is made), as the desktop built the
# board when asking its stats util for them did until ADR-005 S4ac-1.
# A statistic is fetched or recalculated now; a fetch answers into the
# state later (the provider's worker), which is all the routes ever waited
# for. "OK" whatever there was to fetch -- a fetch that raised included
# (logged: StatsService.RefreshQuietly), as when the routes only emitted
# the board's signals. Only boards 1/2 have statistics: the crew board
# raises (ValueError: the legacy route's 500, as its missing stats util
# did), without being built; a board no shell has is refused (NotAvailable:
# 503, since the ADR-005 S4x review); a board number that is not a number
# raises.

def _stats_board(host, scoreboard):
    board = int(scoreboard)
    # A board no shell has: 503, as every board operation (ADR-005 S4x
    # review). The crew board raises below (500), as it always did.
    _require_board(board)
    if board not in AutonaActions.SCORE_BOARDS:
        raise ValueError(f"scoreboard {scoreboard} has no statistics")
    host.Prepare(board)
    return StatsService(board)


def stats_recent_sets(scoreboard):
    host = boardHost

    def run():
        _stats_board(host, scoreboard).RefreshQuietly(
            StatsService.RECENT_SETS)
        return "OK"

    return host.Run(run)


def stats_upset_factor(scoreboard):
    host = boardHost

    def run():
        _stats_board(host, scoreboard).RefreshQuietly(
            StatsService.UPSET_FACTOR)
        return "OK"

    return host.Run(run)


def _stats_players(player, what):
    """The teams `player` ("1", "2" or "both") names, or None (logged)."""
    if str(player) == "1":
        return (1,)
    if str(player) == "2":
        return (2,)
    if player == "both":
        return (1, 2)
    logger.error(f"[{what}] Unable to find player defined. "
                 "Allowed values are: 1, 2, or both")
    return None


def stats_last_sets(scoreboard, player):
    # A player that is none of the three is logged and answered "OK",
    # before the board is looked at, as it always was.
    teams = _stats_players(player, "Last Sets")
    if teams is None:
        return "OK"
    host = boardHost

    def run():
        stats = _stats_board(host, scoreboard)
        for team in teams:
            stats.RefreshQuietly(StatsService.LAST_SETS, team)
        return "OK"

    return host.Run(run)


def stats_history_sets(scoreboard, player):
    teams = _stats_players(player, "History Standings")
    if teams is None:
        return "OK"
    host = boardHost

    def run():
        stats = _stats_board(host, scoreboard)
        for team in teams:
            stats.RefreshQuietly(StatsService.HISTORY_SETS, team)
        return "OK"

    return host.Run(run)


def refresh_stats(scoreboard):
    """Every statistic of a board, as the Statistics page's button asks for
    them (StatsService.RefreshAll). The fetches are async: this returns
    once they are started, and the results land in the state a moment
    later. A board other than 1/2 answers an error string (not built)."""
    host = boardHost

    def run():
        board = int(scoreboard)
        if board not in AutonaActions.SCORE_BOARDS:
            logger.warning(f"[Stats] scoreboard {scoreboard} has no stats")
            return f"ERROR: scoreboard {scoreboard} does not support stats"
        host.Prepare(board)
        StatsService(board).RefreshAll()
        return "OK"

    return host.Run(run)


def get_set(scoreboard):
    # The set the board loaded last (its SetLoadService's, ADR-005 S2d; kept
    # by board, headless too, since S3e-7), or "0", the answer for no set.
    host = boardHost
    lastSet = host.Run(lambda: host.LastSet(scoreboard))
    if lastSet is None:
        return "0"
    return str(lastSet)


# ── set loading and following (ADR-005 S3e-7) ────────────────────────────
# Core's (SetFollowService), run where the board operations run: the
# desktop runs them on its GUI thread, as the board's own set window does
# (a board core does not have is made first; ADR-005 S4ac-1). The set is fetched, and followed, from the
# provider's worker and the shell's clock; what they answer lands in the
# state later, which is all the routes ever waited for. Only boards 1/2
# load sets: the crew board raises (ValueError: the legacy route's 500, as
# the board's missing signal did) without being built, and a board no shell
# has is refused (NotAvailable: 503, since the ADR-005 S4x review).

def _set_board(host, scoreboard):
    # A board no shell has: 503, as every board operation (ADR-005 S4x
    # review). The crew board raises below (500), as it always did.
    board = _require_board(scoreboard)
    if board not in AutonaActions.SCORE_BOARDS:
        raise ValueError(f"scoreboard {scoreboard} does not load sets")
    host.Prepare(board)
    return SetFollowService(board)


def load_set(scoreboard, set=None, no_mains=False):
    """Load set `set` (a provider id) onto board 1/2, as the set window
    does (SetFollowService.LoadSet: the clears, the set recorded as
    followed, the fetch, the poll). "OK"; nothing happens without a set or
    without a tournament loaded (/api/v1 answers NOT_READY for the latter),
    and a set that is not a str is set '0', as the route always did."""
    if set is None:
        return "OK"
    if not isinstance(set, str):
        set = '0'
    # Through JSON and back, as the board's signal was handed it.
    data = orjson.loads(orjson.dumps({
        'id': set,
        'auto_update': "set",
        'no_mains': no_mains
    }))
    host = boardHost

    def run():
        _set_board(host, scoreboard).LoadSet(data)
        return "OK"

    return host.Run(run)


def post_score(data):
    """POST /score and the Socket.IO score event: a set payload (JSON) for
    board `scoreboard` (1 if the payload names none, or a board that is not
    a number), with both scores reset first (`reset_score`).

    Boards 1/2 apply it as a fetched set is applied
    (SetFollowService.ApplySetPayload: the match information, the scores,
    the set length, the losers flags, the entrants or one player, the
    bracket type), the crew board by its own rules
    (CrewBattleService.ApplySetData). The payload is the copy the boards'
    `dict` signal handed them (DictHelper.qvariantmap_copy: every object's
    keys in QVariantMap order), so what lands in the state is unchanged.
    A payload the board fails to apply is logged and answered "OK", as
    when it was the board's slot; a one-player payload's `savePlayerToDb`
    reaches the player database after the host (`SaveRecord`). One save
    cycle.
    """
    score = orjson.loads(data)
    scoreboard_number = 1

    if "scoreboard" in score:
        try:
            scoreboard_number = int(score["scoreboard"])
        except ValueError:
            logger.warning(f"Couldn't parse scoreboard [${score['scoreboard']}] from /post_data as int, falling back to scoreboard 1")
            scoreboard_number = 1

    score.update({"reset_score": True})
    payload = qvariantmap_copy(score)
    host = boardHost

    def run():
        if scoreboard_number == CREW_BOARD:
            host.Prepare(CREW_BOARD)
            try:
                CrewBattleService(CREW_BOARD).ApplySetData(payload)
            except Exception:
                logger.exception("post_score: the crew board's payload "
                                 "was not applied")
            return None
        follow = _set_board(host, scoreboard_number)
        try:
            return follow.ApplySetPayload(payload)
        except Exception:
            logger.exception(f"post_score: scoreboard {scoreboard_number}'s "
                             "payload was not applied")
            return None

    record = host.Run(run)
    try:
        SaveRecord(record)
    except Exception:
        logger.exception("post_score: the player was not saved")
    return "OK"


def stream_channel():
    """The streamer's channel the stream follow reads (`twitch_username`),
    or None when it is not set."""
    return SettingsManager.Get("twitch_username") or None


def pull_stream_set(scoreboard):
    """Load the set the streamer's channel is showing next onto board 1/2
    and follow the stream (SetFollowService.PullStream): "OK". The Web's
    auto-update switch repeats it every few seconds, so a pull while the
    board follows the channel only looks its set up again -- the set shown
    stays unless the channel moved on. It emitted a board signal nothing
    was connected to until ADR-005 S3e-7 (論点 3). With no channel set, or
    no tournament loaded, there is nothing to follow: "OK" all the same
    (/api/v1 answers NOT_READY)."""
    host = boardHost

    def run():
        follow = _set_board(host, scoreboard)
        channel = stream_channel()
        if channel is None:
            return "OK"
        follow.PullStream(channel)
        return "OK"

    return host.Run(run)


def get_follow(scoreboard):
    """What board 1/2 follows (SetFollowService.Snapshot). On the calling
    thread, and builds nothing: a board never built follows nothing."""
    board = _board(scoreboard)
    if board not in AutonaActions.SCORE_BOARDS:
        raise ValueError(f"scoreboard {scoreboard} does not load sets")
    return SetFollowService(board).Snapshot()


def follow_station(scoreboard, station):
    """Follow a station or a stream on board 1/2, as the station window does
    (SetFollowService.FollowStation: its current set is looked up and loaded,
    whatever the board shows). `station` is what a row of the list names:
    {"type", "id", "identifier"}."""
    host = boardHost
    host.Run(lambda: _set_board(host, scoreboard).FollowStation(station))


def stop_follow(scoreboard):
    """The board's timer row's cancel button: stop polling, forget the station
    and the followed set (SetFollowService.StopFollowing(forget=True)), on a
    board that follows nothing writing nothing (its writes are SetIfChanged).
    A board core does not have (its launch failed; every board is made at
    launch since ADR-005 S4ac-1, the desktop's as it was built until then)
    follows nothing -- a load makes it -- and is not made for this: making it
    seeds it and turns the polling setting off. Nothing is built for it
    either."""
    board = _board(scoreboard)
    if board not in AutonaActions.SCORE_BOARDS:
        raise ValueError(f"scoreboard {scoreboard} does not load sets")
    host = boardHost

    def run():
        if host.Built(board):
            SetFollowService(board).StopFollowing(forget=True)

    host.Run(run)


# ═══════════════════════════════════════════════════════════════════════
# The shell operations (ADR-005 S3e-9)
# ═══════════════════════════════════════════════════════════════════════
# What only the desktop's windows do (see the module docstring), through
# what the shell registered (openSetSelection / renderThumbnail). With
# nothing registered -- the headless shell -- NotAvailable: the legacy
# routes' 503, the 503 body as the Socket.IO event's ack. /api/v1 has
# neither operation.

def open_sets(scoreboard):
    """Open board `scoreboard`'s set selection: "OK". The SCORE page's
    dialog while SCORE is the React page -- the desktop's window brought to
    the front on SCORE, nothing opened for a board other than the format's
    -- else the Qt window (AutonaShellOperations.OpenSetSelection; ADR-005
    S4ab-2). A board no shell has is refused first, whatever the shell
    registered (503, ADR-005 S4x review: the desktop's window answered
    500)."""
    _require_board(scoreboard)
    if openSetSelection is None:
        raise NotAvailable()
    return _on_gui_thread(lambda: openSetSelection(scoreboard),
                          "the set selection")


def get_thumbnail(scoreboard, file_format):
    """Render board `scoreboard`'s thumbnail as `file_format` ("png" /
    "jpg"): the image's absolute path, or None when it failed. A board no
    shell has is refused first, as by open_sets."""
    _require_board(scoreboard)
    if renderThumbnail is None:
        raise NotAvailable()
    return _on_gui_thread(lambda: renderThumbnail(scoreboard, file_format),
                          "the thumbnail")
