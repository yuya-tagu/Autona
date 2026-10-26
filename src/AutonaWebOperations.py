"""The web layer's operations that do not need a board widget (ADR-005 S3c).

What the legacy routes, the Socket.IO events and /api/v1 call for every
operation that is core's. They used to be methods of `WebServerActions`
(which needed the scoreboard dock to exist); that class keeps only the
operations a board or panel widget still answers -- the "board-dependent"
ones, listed in docs/changes/architecture-modernization.md (S3c-2) and
removed in S3e.

Two kinds live here:

- core-direct: read or write core on the calling (Flask / Socket.IO) thread,
  the same in both shells -- the scores, 反映, the colours, the display
  options, the game / country / player DB readers, the tournament URL.
- the operations ADR-005 S2 moved into core (swap, the clears, the set
  fields, losers, the team name, reset score, and the readers get_swap /
  get_set / get_scoreboard). They call core in both shells too; what the
  desktop adds around that call is its board: it builds the board first (an
  unbuilt board is not held, so the write would be published at once and
  then wiped by the seed the build writes), runs on the GUI thread (a player
  card still commits a typed name in two writes there; see
  DesktopBoardHost.Run), lets
  the field being typed in finish first. The player / character counts of
  boards 1/2 are core's too since S3e-6 (AutonaActions.SetRoster; they were
  the board's spinboxes, and a shell without the board refused them).

  That difference is one object the shell registers, `boardHost`: the
  headless default (`BoardHost`) runs the operation on the calling thread,
  one at a time, with no board -- the write is published at once, which is
  right there: there is no 反映 button -- and raises `NotAvailable` for
  what only a widget can do. bootstrap_gui() registers `DesktopBoardHost`.
- the crew board's operations (its members' stocks and active member, and
  its swap / resets / clears / set fields) and the caster line-up's
  (ADR-005 S3e-2): core's CrewBattleService and CommentaryService, in both
  shells. The crew ones go through the board host like the S2 ones (the
  desktop builds the board first and runs them on its GUI thread); the
  line-up has no board and is written on the calling thread. The board and
  the Commentary panel render what was written.
- the player slots' (ADR-005 S3e-4): the Web's player edit (v1's PATCH
  players and the legacy update-team) and the tag lookup -- core's
  PlayerService.ApplyData, through the board host like the S2 ones. The
  desktop's host adds what the board's cards do around a load (the typed
  combo text).
- the statistics of boards 1/2 (ADR-005 S3e-5): core's StatsService,
  through the board host like the S2 ones, in both shells. The fetches are
  the shell's (StatsService.fetcher) and answer into the state later.
- the Top 8 import (ADR-005 S3e-8): core's Top8Service, in both shells. It
  runs on the tournament provider's worker; the Top 8 panel, built or not,
  renders what it wrote.

Imports nothing that builds anything; the registrations are plain module
attributes, filled in by the shell (agent-pitfalls #1).
"""
import re
import threading

from loguru import logger

from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from .core import CommentaryModel as comms
from .core import CommentaryService
from .core import Top8Service
from .core.CountryCatalog import CountryCatalog
from .core.CrewBattleService import CrewBattleService
from .core.GameAssetCatalog import GameAssetCatalog
from .core.SettingsManager import SettingsManager
from .core.SetLoadService import SetLoadService
from .core.PlayerDB import PlayerDB
from .core.PlayerService import DEFAULT_CHARACTERS_PER_PLAYER, PlayerService
from .core.StatsService import StatsService
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Helpers.AutonaQtHelper import gui_thread_sync, in_gui_thread


NOT_AVAILABLE_MESSAGE = "not available in headless"


class NotAvailable(Exception):
    """The operation needs a board or panel widget, and this shell has none.

    The web server answers it with 503 in the /api/v1 error shape (the
    legacy routes and the Socket.IO acks too); /api/v1 maps it to its
    UNAVAILABLE code.
    """

    def __init__(self, message=NOT_AVAILABLE_MESSAGE):
        super().__init__(message)
        self.message = message


class BoardHost:
    """Where the S2 operations run when there is no board (the headless
    shell, and the default until a shell registers another).

    The operation runs on the calling thread; nothing is built, so the write
    is not held. What only a widget can do raises NotAvailable.
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
        # The characters per player a board with no character slot gives
        # the players it gets next ({board: count}): what the Web asked for
        # while it had none, else what it had when it lost its last player
        # (EmptyRosterCharacters).
        self._emptyCharacters = {}

    def Run(self, op):
        """Run `op()` where this shell runs board operations."""
        with self._lock:
            return op()

    def Prepare(self, board):
        """Make board `board` ready to be written (desktop: build it)."""

    def Widget(self, board):
        """Board `board`'s widget, for the operations only it answers."""
        raise NotAvailable()

    def CommitEdit(self, board):
        """Finish the field being typed in on board `board`."""

    def ClearPlayers(self, board, no_mains=False):
        """Empty every player slot of board `board`."""
        if AutonaActions._asInt(board, 0) not in AutonaActions.SCORE_BOARDS:
            raise NotAvailable()
        AutonaActions.instance.ClearPlayers(board, no_mains)

    def EmptyRosterCharacters(self, board):
        """The characters per player the next players of board `board` get
        while it has no character slot (SetRoster would give them the
        default). The desktop's is the board's spinbox; with no board, what
        KeepEmptyRosterCharacters kept, else the default."""
        return self._emptyCharacters.get(
            _board(board), DEFAULT_CHARACTERS_PER_PLAYER)

    def KeepEmptyRosterCharacters(self, board, count):
        """Remember `count` as board `board`'s characters per player for
        the players it gets next: asked for while it has no character slot,
        or what it had when its last player went -- what the board's
        spinbox kept, the desktop's in both cases. Boards 1/2 only: no
        other board has a roster to give it to."""
        if _board(board) not in AutonaActions.SCORE_BOARDS:
            return
        self._emptyCharacters[_board(board)] = max(0, min(
            AutonaActions._asInt(count, DEFAULT_CHARACTERS_PER_PLAYER),
            AutonaActions.MAX_CHARACTERS_PER_PLAYER))

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
        Kept by the board's SetLoadService; no board, no set."""
        return None

    def PlayerLoaded(self, board, team, player, cleared):
        """A player was loaded into slot `player` of state team `team` on
        board `board` (1/2); `cleared`: whether the load cleared the slot
        first. Called inside the load's save block. With no board there is
        no card to tidy up. (The statistics the load calls for are its own
        -- PlayerService.Announce, ADR-005 S3e-5 -- in both shells.)
        """


class DesktopBoardHost(BoardHost):
    """The desktop's board around an S2 operation. Registered by
    bootstrap_gui() with the scoreboard dock (AutonaScoreboardManager)."""

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def _Board(self, board):
        # On the GUI thread, the build itself (it raises if it fails, as
        # the operations' EnsureScoreboard did); elsewhere GetScoreboard,
        # which marshals the build there.
        if in_gui_thread():
            return self.manager.EnsureScoreboard(board)
        return self.manager.GetScoreboard(board)

    def Run(self, op):
        # On the GUI thread, not under the headless host's lock (reviewed in
        # ADR-005 S3e-4, kept): a card still commits a typed sponsor / tag
        # in two writes on that thread -- the field (_OnTextChanged), then
        # the display name, images and id (NameChanged) -- and a swap, a
        # clear or a load landing between the two from another thread
        # leaves the display name on the wrong slot. Queued on the GUI
        # thread, an operation runs between two events, never inside one.
        # The board's own part of an operation (building it, the field
        # being typed in, the cards after a load) is GUI work besides.
        return gui_thread_sync(op)()

    def Prepare(self, board):
        self._Board(board)

    def Widget(self, board):
        return self._Board(board)

    def EmptyRosterCharacters(self, board):
        return self._Board(board).EmptyRosterCharacters()

    def KeepEmptyRosterCharacters(self, board, count):
        self._Board(board).KeepEmptyRosterCharacters(count)

    def CommitEdit(self, board):
        self._Board(board).CommitFocusedEdit()

    def ClearPlayers(self, board, no_mains=False):
        # The board's clear: AutonaActions.ClearPlayers plus what the board
        # owns around it (the field being typed in, the set loaded last).
        # The statistics are the operation's (StatsService.TeamsChanged,
        # ADR-005 S3e-5). Boards 1/2: the crew board's clear is core's
        # (`_clear_players`).
        self._Board(board).CommandClearAll(no_mains)

    def LastSet(self, board):
        # The crew board has no SetLoadService, and raises as it did.
        return self._Board(board).setLoad.lastSetSelected

    def PlayerLoaded(self, board, team, player, cleared):
        # After a load that cleared, the slot's card drops the text typed
        # into its combos -- what the board does around its own loads
        # (AutonaScoreboardWidget._ApplyPlayer).
        self._Board(board).PlayerLoaded(team, player, cleared)


# ── Registrations (filled in by the shell) ───────────────────────────────

# Where the S2 operations run: BoardHost (no board) unless the desktop
# registered its DesktopBoardHost (bootstrap_gui()).
boardHost = BoardHost()

# How a game is loaded by codename, with the asset adapter's signature:
# (codename, async_mode=True, mods_active=False, mods_reload_mode=False,
# done=None). The desktop registers AutonaGameAssetManager.LoadGameByCodename
# (bootstrap_gui()), the headless shell its catalog-only loader
# (src/headless.py). None: nothing can load a game.
loadGameByCodename = None


def _board(scoreboard):
    return AutonaActions._asInt(scoreboard, 0)


# The crew battle board: its operations are CrewBattleService's.
CREW_BOARD = 3


def _is_crew(scoreboard):
    return _board(scoreboard) == CREW_BOARD


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


# ── the tournament ───────────────────────────────────────────────────────

def load_tournament(url=None):
    logger.error(f"URL PROVIDED: {url}")
    if url is None or url == "":
        AutonaTournamentDataProvider.instance.signals.tournament_url_update.emit(None)
        return "OK"

    if "start.gg" in url:
        matches = re.match(
            "(.*start.gg/tournament/[^/]*/event[s]?/[^/]*)", url)
        if matches:
            url = matches.group(0)

            # Some URLs in startgg have eventS but the API doesn't work with that format
            url = url.replace("/events/", "/event/")

    SettingsManager.Set("TOURNAMENT_URL", url)
    AutonaTournamentDataProvider.instance.signals.tournament_url_update.emit(url)
    return "OK"


def update_bracket(done=None):
    """Re-import the Top 8 from Start.gg (core: Top8Service, ADR-005 S3e-8).

    Starts the import on the provider's worker and returns at once, in
    either shell, whether or not the Top 8 panel was ever built. Answers
    what RefreshFromProvider answers (``Top8Service.STARTED`` / ``BUSY`` /
    ``NO_TOURNAMENT``): /api/v1 maps it to 202 / 409; the legacy route and
    the Socket.IO event answer "OK" whichever it was, as they always did.
    `done()` as in RefreshFromProvider.
    """
    return Top8Service.RefreshFromProvider(done=done)


# ── the game ─────────────────────────────────────────────────────────────

@gui_thread_sync
def set_game(data):
    # On the GUI thread, as it always was: the desktop's asset adapter
    # builds its loader thread and models there, and the synchronous load
    # below is the legacy route's answer ("OK" once loaded).
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
    """Start loading `codename` (reloading it if it is the loaded one) on
    the loader's thread. `done()` is called once the attempt is over,
    whichever way it went (/api/v1's in-progress flag)."""
    if loadGameByCodename is None:
        logger.error(f"no game loader registered; {codename} not loaded")
        if done is not None:
            done()
        return
    loadGameByCodename(
        codename, async_mode=True, mods_active=mods_active,
        mods_reload_mode=True, done=done)


# ═══════════════════════════════════════════════════════════════════════
# The S2 operations (see the module docstring)
# ═══════════════════════════════════════════════════════════════════════

def set_route(scoreboard, bestOf=None, phase=None, pool=None, match=None,
              players=None, characters=None, losers=None, team=None,
              teamName=None):
    host = boardHost

    def run():
        # The counts are core's in both shells: boards 1/2's roster
        # (AutonaActions.SetRoster, ADR-005 S3e-6 -- the board's two
        # spinboxes until then, so a shell with no board refused them), the
        # crew board's member count (S3e-2). The desktop's board is built
        # first, as before, so the change is held for 反映 rather than
        # published and then wiped by the seed the build writes.
        crew = _is_crew(scoreboard)
        if players is not None or characters is not None:
            # Boards 1/2 and the crew board have counts; any other is
            # refused before anything is written, as it was when the counts
            # were the board's widgets (503, legacy / Socket.IO / v1 alike)
            # -- not answered OK having changed nothing.
            if not crew and _board(scoreboard) \
                    not in AutonaActions.SCORE_BOARDS:
                raise NotAvailable()
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
            host.Prepare(scoreboard)
            idx = int(team)
            board = _board(scoreboard)
            if idx in (1, 2) and board in AutonaActions.SCORE_BOARDS:
                with StateManager.SaveBlock():
                    if AutonaActions.instance.SetTeamName(
                            scoreboard, idx, teamName):
                        SetLoadService(board).PublishTeamLogo(idx, teamName)
        return "OK"

    return host.Run(run)


def _set_players(host, scoreboard, players):
    """set_route's players on board 1/2, with the characters per player the
    board's spinbox gave them: a board with no character slot left takes
    its count from the host (EmptyRosterCharacters) rather than the
    default, and one that loses its last player keeps the count it had
    there for the next ones. As the two spinboxes did: players=0, then
    characters=2, then players=2 is two players a team with two characters
    each."""
    actions = AutonaActions.instance
    current = actions.CharacterSlots(scoreboard)
    characters = None
    if current is None:
        characters = host.EmptyRosterCharacters(scoreboard)
    elif players <= 0:
        host.KeepEmptyRosterCharacters(scoreboard, current)
    actions.SetRoster(scoreboard, players=players, characters=characters)


def _set_characters(host, scoreboard, count):
    """set_route's characters on board 1/2: the character slots of every
    player, or -- on a board with none -- the count its next players get
    (KeepEmptyRosterCharacters), as the spinbox kept it."""
    actions = AutonaActions.instance
    if actions.CharacterSlots(scoreboard) is None:
        host.KeepEmptyRosterCharacters(scoreboard, count)
    actions.SetRoster(scoreboard, characters=count)


def _apply_set_fields(host, scoreboard, payload):
    """One set_route field, as the set payload it has always been.

    On boards 1/2 that payload is applied by core (ADR-005 S2d), as the
    board's ChangeSetData applied it: the match bar fields and the English
    round text (blanked by every payload that carries none --
    SetLoadService.ApplyMatchInfo), a best of (AutonaActions.SetBestOf, S2e;
    0 is not carried -- SetLoadService.BestOfFromSetData) and a losers flag
    (ApplyLosers). The board is prepared first, so on the desktop the write
    waits for 反映 like any other and a best of resizes the board's game rows
    with it. The crew board reads the payload by its own rules
    (CrewBattleService.ApplySetData, S3e-2).
    """
    host.Prepare(scoreboard)
    board = _board(scoreboard)
    if board not in AutonaActions.SCORE_BOARDS:
        if board == CREW_BOARD:
            CrewBattleService(CREW_BOARD).ApplySetData(payload)
            return
        host.Widget(scoreboard).signals.ChangeSetData.emit(payload)
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
    # ADR-005 S2c) and its flag is `score.{n}.teamsSwapped`; the crew board's
    # is CrewBattleService.SwapTeams (S3e-2), its flag kept by core outside
    # the state. Either way the board lets the field being typed in finish
    # first, so the text travels with the swap as it did when the widget
    # swapped its rows.
    host = boardHost

    def run():
        board = _board(scoreboard)
        if board in AutonaActions.SCORE_BOARDS or board == CREW_BOARD:
            host.Prepare(scoreboard)
            host.CommitEdit(scoreboard)
            if board == CREW_BOARD:
                CrewBattleService(CREW_BOARD).SwapTeams()
            else:
                AutonaActions.instance.SwapTeams(scoreboard)
        else:
            host.Widget(scoreboard).signals.SwapTeams.emit()
        return "OK"

    return host.Run(run)


def get_swap(scoreboard):
    # The live flag: a swap is reported as soon as it is made, before 反映
    # publishes it. The board is prepared first, as the route always did.
    host = boardHost
    host.Prepare(scoreboard)
    board = _board(scoreboard)
    if board in AutonaActions.SCORE_BOARDS:
        return str(bool(StateManager.Get(
            f"score.{board}.teamsSwapped", False)))
    if board == CREW_BOARD:
        return str(CrewBattleService(CREW_BOARD).teamsSwapped)
    return str(host.Widget(scoreboard).teamsSwapped)


def get_scoreboard(scoreboard):
    # The desktop still builds a board that is not built yet, although the
    # answer is read from the state: building is what writes a board's full
    # initial subtree, and an unbuilt board's is only the skeleton
    # (StateSchema.SeedBoard). Headless, the skeleton is the board.
    # Committed view: pending (not yet 反映-applied) desktop edits must not
    # leak to remote clients ahead of the delta stream.
    boardHost.Prepare(scoreboard)
    return StateManager.GetCommitted(f'score.{_board(scoreboard)}')


def reset_scores(scoreboard):
    # Boards 1/2 go through Actions; a crew board's "reset score" restores
    # every member's stocks (CrewBattleService.ResetStocks, S3e-2).
    host = boardHost

    def run():
        if AutonaActions.instance.ResetScore(scoreboard):
            return "OK"
        if _is_crew(scoreboard):
            host.Prepare(scoreboard)
            CrewBattleService(CREW_BOARD).ResetStocks()
            return "OK"
        host.Widget(scoreboard).ResetScore()
        return "OK"

    return host.Run(run)


def _reset_match(host, scoreboard):
    # The operator's reset (scores + losers flags + round name / phase /
    # pool, ADR-005 S2d), not the set-load pre-pass ClearScore, which keeps
    # the match bar (S-17); then the best of, in a cycle of its own as the
    # spinbox's was. The crew board (3) answers False to the core operation:
    # its reset is full stocks and nobody playing plus the same match bar,
    # in one cycle, then its raw best of (S3e-2).
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
    host.Widget(scoreboard).ResetMatch()


def _clear_players(host, scoreboard):
    # Every member row of the crew board emptied (core's, S3e-2); a 1v1 /
    # team board's players through its host (the board owns what goes
    # around the clear on the desktop). The field being typed in finishes
    # first, as boards 1/2's clear finishes it (CommandClearAll): the clear
    # then takes the typed text with it, instead of leaving it in the field
    # to be written once the operator leaves it.
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
        host.Prepare(scoreboard)
        _reset_match(host, scoreboard)
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
        host.Prepare(scoreboard)
        if _is_crew(scoreboard):
            host.CommitEdit(scoreboard)
        _reset_match(host, scoreboard)
        _reset_counts(host, scoreboard)
        _clear_players(host, scoreboard)
        return "OK"

    return host.Run(run)


# ── the crew board's members (ADR-005 S3e-2) ─────────────────────────────
# Core's (CrewBattleService), run where the board operations run: on the
# desktop the board is built first and the write made on the GUI thread,
# as the board's own inputs make theirs. Another board has no members, and
# answers "OK" having done nothing, as it always did (after being built,
# on the desktop, as asking for it always did -- and without reading the
# numbers). `team` and `member` are 1-based; on the crew board a number
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


# ── the player slots of boards 1/2 (ADR-005 S3e-4) ───────────────────────
# Core's (PlayerService.ApplyData, S3e-3), run where the board operations
# run: on the desktop the board is built first and the load made on the GUI
# thread, which is also where the board's cards are (the host's
# PlayerLoaded). A slot is one the *state* has -- each card of a built
# board publishes its own, and the headless shell seeds them (S3d) -- so the
# range is checked the same way in both shells. The board's cards follow
# the load from the held cycle, like any write they did not make (S2a).

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
    route's 404) -- checked after the board is built, since building it is
    what writes its slots. An exception from the write propagates (500).
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
    as the board's ChangeSetData applied it.

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
    """
    host = boardHost
    payload = {"team": team, "player": player, "data": data}

    def fail(board, e):
        logger.error(f"update-team: scoreboard {board} team {team} "
                     f"player {player} not written: {e}")

    def run():
        board = _board(scoreboard)
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
    slots -- the crew board raised too (it has no player cards).
    """
    host = boardHost

    def run():
        board = _board(scoreboard)
        if board not in AutonaActions.SCORE_BOARDS:
            raise ValueError(f"scoreboard {scoreboard} has no player slots")
        host.Prepare(scoreboard)
        team_, player_ = int(team), int(player)
        playerData = PlayerDB.instance.GetPlayerFromTag(str(tag))
        if not playerData:
            return "ERROR"
        path = None
        if team_ in (1, 2):
            team_ = SetLoadService(board).DisplayTeam(team_)
            path = _player_path(board, team_, player_)
        if path is None:
            raise IndexError(
                f"scoreboard {board} team {team} has no player {player}")
        _load_player(host, board, team_, player_, playerData, True, True,
                     no_mains)
        return "OK"

    return host.Run(run)


# ── the statistics of boards 1/2 (ADR-005 S3e-5) ─────────────────────────
# Core's (StatsService), run where the board operations run: the desktop
# builds the board first, as asking its stats util for them always did.
# A statistic is fetched or recalculated now; a fetch answers into the
# state later (the provider's worker), which is all the routes ever waited
# for. "OK" whatever there was to fetch -- a fetch that raised included
# (logged: StatsService.RefreshQuietly), as when the routes only emitted
# the board's signals. Only boards 1/2 have statistics:
# another board raises (ValueError: the legacy route's 500, as its missing
# stats util did), without being built; a board number that is not a
# number raises too.

def _stats_board(host, scoreboard):
    board = int(scoreboard)
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
    """Every statistic of a board, as the STATS panel's button asks for
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
    # The set the board loaded last (its SetLoadService's, ADR-005 S2d).
    # Headless there is none: "0", the answer for no set.
    host = boardHost
    lastSet = host.Run(lambda: host.LastSet(scoreboard))
    if lastSet is None:
        return "0"
    return str(lastSet)
