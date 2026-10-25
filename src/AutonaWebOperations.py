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
  card still publishes a typed edit in two writes there, ADR-005 S2f), lets
  the field being typed in finish first, and answers for the crew board's
  operations, which are still its widget's.

  That difference is one object the shell registers, `boardHost`: the
  headless default (`BoardHost`) runs the operation on the calling thread,
  one at a time, with no board -- the write is published at once, which is
  right there: there is no 反映 button -- and raises `NotAvailable` for
  what only a widget can do. bootstrap_gui() registers `DesktopBoardHost`.

Imports nothing that builds anything; the registrations are plain module
attributes, filled in by the shell (agent-pitfalls #1).
"""
import re
import threading

from loguru import logger

from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from .core.CountryCatalog import CountryCatalog
from .core.GameAssetCatalog import GameAssetCatalog
from .core.SettingsManager import SettingsManager
from .core.SetLoadService import SetLoadService
from .AutonaPlayerDB import AutonaPlayerDB
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
    is not held. What only a widget can do -- the crew board's own
    operations, the player / character counts -- raises NotAvailable.
    """

    def __init__(self):
        # One operation at a time. An S2 operation is several core calls
        # (clear_all is four save cycles, /api/v1's PATCH set up to three
        # calls in one task); the desktop queues them on its GUI thread, and
        # with none, two requests' threads would interleave them. Re-entrant:
        # an operation run here runs others here (PATCH set's task calls
        # set_route). Taken only by the web layer's request threads, never
        # with StateManager.lock held, and nothing run under it waits for
        # the main thread (headless, nothing marshals there): it is always
        # the outermost lock, so it cannot close a cycle.
        self._lock = threading.RLock()

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
        """Finish the field being typed in on board `board` (1/2)."""

    def ClearPlayers(self, board, no_mains=False):
        """Empty every player slot of board `board`."""
        if AutonaActions._asInt(board, 0) not in AutonaActions.SCORE_BOARDS:
            raise NotAvailable()
        AutonaActions.instance.ClearPlayers(board, no_mains)

    def ResetCounts(self, board):
        """Back to one player per team and one character per player.

        The counts are still a built board's spinboxes (ADR-005 S2g); with
        no board there is nothing to reset, and the roster stays as it is.
        """

    def LastSet(self, board):
        """The provider id of the set board `board` loaded last, or None.
        Kept by the board's SetLoadService; no board, no set."""
        return None


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
        return gui_thread_sync(op)()

    def Prepare(self, board):
        self._Board(board)

    def Widget(self, board):
        return self._Board(board)

    def CommitEdit(self, board):
        self._Board(board).CommitFocusedEdit()

    def ClearPlayers(self, board, no_mains=False):
        # The board's clear: AutonaActions.ClearPlayers plus what the board
        # owns around it (the field being typed in, the set loaded last,
        # the statistics) -- the crew's is its own clear.
        self._Board(board).CommandClearAll(no_mains)

    def ResetCounts(self, board):
        widget = self._Board(board)
        widget.playerNumber.setValue(1)
        widget.charNumber.setValue(1)

    def LastSet(self, board):
        # The crew board has no SetLoadService, and raises as it did.
        return self._Board(board).setLoad.lastSetSelected


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
    # Clients receive this on (re)connect.
    tags = AutonaPlayerDB.tournamentTags
    if tags:
        return {tag: player
                for tag, player in AutonaPlayerDB.database.items()
                if tag in tags}
    return AutonaPlayerDB.database


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
        # The counts are a board's (S2g): asked for first, so a shell with
        # no board refuses the request before anything else is written.
        widget = None
        if players is not None or characters is not None:
            widget = host.Widget(scoreboard)

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

        # players=<Amount of Players>
        if players is not None:
            widget.playerNumber.setValue(int(players))

        # characters=<Amount of Characters>
        if characters is not None:
            widget.charNumber.setValue(int(characters))

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


def _apply_set_fields(host, scoreboard, payload):
    """One set_route field, as the set payload it has always been.

    On boards 1/2 that payload is applied by core (ADR-005 S2d), as the
    board's ChangeSetData applied it: the match bar fields and the English
    round text (blanked by every payload that carries none --
    SetLoadService.ApplyMatchInfo), a best of (AutonaActions.SetBestOf, S2e;
    0 is not carried -- SetLoadService.BestOfFromSetData) and a losers flag
    (ApplyLosers). The board is prepared first, so on the desktop the write
    waits for 反映 like any other and a best of resizes the board's game rows
    with it. The crew board's ChangeSetData is its widget's.
    """
    host.Prepare(scoreboard)
    board = _board(scoreboard)
    if board not in AutonaActions.SCORE_BOARDS:
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
    # ADR-005 S2c) and its flag is `score.{n}.teamsSwapped`; the board lets
    # the field being typed in finish first. The crew board still swaps
    # itself, through its widget.
    host = boardHost

    def run():
        if _board(scoreboard) in AutonaActions.SCORE_BOARDS:
            host.Prepare(scoreboard)
            host.CommitEdit(scoreboard)
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
    if _board(scoreboard) in AutonaActions.SCORE_BOARDS:
        return str(bool(StateManager.Get(
            f"score.{_board(scoreboard)}.teamsSwapped", False)))
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
    # every member's stocks -- widget-owned state (see core/Actions.py).
    host = boardHost

    def run():
        if AutonaActions.instance.ResetScore(scoreboard):
            return "OK"
        host.Widget(scoreboard).ResetScore()
        return "OK"

    return host.Run(run)


def _reset_match(host, scoreboard):
    # The operator's reset (scores + losers flags + round name / phase /
    # pool, ADR-005 S2d), not the set-load pre-pass ClearScore, which keeps
    # the match bar (S-17); then the best of, in a cycle of its own as the
    # spinbox's was. The crew board (3) answers False to the core operation
    # and keeps its own reset and best of spinbox.
    if AutonaActions.instance.ResetMatch(scoreboard):
        AutonaActions.instance.SetBestOf(scoreboard, 0)
        return
    widget = host.Widget(scoreboard)
    widget.ResetMatch()
    best_of = getattr(widget, "_bestOfSpin", None)
    if best_of is not None:
        best_of.setValue(0)


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
        host.ClearPlayers(scoreboard)
        return "OK"

    return host.Run(run)


def clear_all(scoreboard):
    # Not AutonaActions.ClearBoard (the desktop's reset-all): this keeps the
    # team names, and the player / character counts (a built board's
    # spinboxes, S2g) change between the reset and the players' clear -- in
    # this order, which decides what the mains end up as.
    host = boardHost

    def run():
        host.Prepare(scoreboard)
        _reset_match(host, scoreboard)
        host.ResetCounts(scoreboard)
        host.ClearPlayers(scoreboard)
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
