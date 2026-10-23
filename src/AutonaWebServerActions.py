import os
import re
from flask import abort
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import orjson

from .Helpers.AutonaQtHelper import gui_thread_sync
from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from .core.CountryCatalog import CountryCatalog
from .core.SettingsManager import SettingsManager
from loguru import logger
from .AutonaGameAssetManager import AutonaGameAssetManager
from .core.GameAssetCatalog import GameAssetCatalog
from .AutonaPlayerDB import AutonaPlayerDB
from .AutonaScoreboardWidget import AutonaScoreboardWidget
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaCommentaryWidget import AutonaCommentaryWidget
from .core import CommentaryModel as comms
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Workers import Worker
import os

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


class WebServerActions(QObject):
    def __init__(self, parent=None, scoreboard=None) -> None:
        super().__init__(parent)
        self.scoreboard = scoreboard

    # ── Lazily-built panels (startup Step 4b-2) ──────────────────────
    # The Commentary and Top 8 panels are constructed the first time their
    # sidebar page is revealed, so an API call can arrive while they do not
    # exist yet. The class `instance` attribute is the fast path; when it is
    # None the main window is asked to build the panel. `parent` is that
    # window (see WebServer.__init__).

    @gui_thread_sync
    def _ensure_panel(self, accessor):
        """Build a lazy main-window panel by accessor name; return it.

        Dispatched to the GUI thread because it may construct widgets.
        Returns None when the window has no such accessor (nothing to do
        but log — the caller reports an error to the client).
        """
        ensure = getattr(self.parent(), accessor, None)
        if ensure is None:
            logger.warning(f"[WebServer] {accessor} is not available")
            return None
        return ensure()

    def program_state(self):
        return StateManager.GetFullState()

    @gui_thread_sync
    def post_score(self, data):
        score = orjson.loads(data)
        scoreboard_number = 1

        if "scoreboard" in score:
            try:
                scoreboard_number = int(score["scoreboard"])
            except ValueError:
                logger.warning(f"Couldn't parse scoreboard [${score['scoreboard']}] from /post_data as int, falling back to scoreboard 1")
                scoreboard_number = 1

        score.update({"reset_score": True})
        self.scoreboard.GetScoreboard(scoreboard_number).signals.ChangeSetData.emit(score)
        return "OK"

    # ── Migrated to the core Actions layer (headless core Step 3) ──────
    # These four used to emit a signal into a board widget, which did the
    # writing. They call AutonaActions directly now: no widget is built to
    # serve them, the write happens synchronously on the caller's thread
    # (so /api/v1's `revision` is accurate for them too), and the board --
    # if one exists -- updates its display from the state notification.
    # No @gui_thread_sync: nothing here touches a Qt object any more.

    def team_scoreup(self, scoreboard, team):
        AutonaActions.instance.AdjustScore(
            scoreboard, 1 if str(team) == "1" else 2, 1)
        return "OK"

    def team_scoredown(self, scoreboard, team):
        AutonaActions.instance.AdjustScore(
            scoreboard, 1 if str(team) == "1" else 2, -1)
        return "OK"

    def team_color(self, scoreboard, team, color):
        AutonaActions.instance.SetTeamColor(
            scoreboard, 1 if str(team) == "1" else 2, color)
        return "OK"

    # ── Crewbattle (board 3) ──────────────────────────────────────────
    # Crew members each hold their own remaining-stock count; the team total
    # is auto-summed by the widget. These actions only make sense for the
    # crew board, so they no-op gracefully on boards that lack the signals.
    def crew_member_stock(self, scoreboard, team, member, change):
        sb = self.scoreboard.GetScoreboard(scoreboard)
        sig = getattr(sb, "signals", None)
        if sig is not None and hasattr(sig, "CommandMemberStock"):
            sig.CommandMemberStock.emit(int(team) - 1, int(member), int(change))
        return "OK"

    def crew_set_active(self, scoreboard, team, member):
        sb = self.scoreboard.GetScoreboard(scoreboard)
        sig = getattr(sb, "signals", None)
        if sig is not None and hasattr(sig, "SetActiveMember"):
            sig.SetActiveMember.emit(int(team) - 1, int(member))
        return "OK"

    def get_scoreboard(self, scoreboard):
        # Still builds a board that is not built yet (GetScoreboard), although
        # the answer is read from the state: building is what writes a board's
        # full initial subtree, and an unbuilt board's is only the skeleton
        # (StateSchema.SeedBoard). Dropping the build would change what this
        # returns for that board, so it stays until the subtree is seeded
        # without the widget.
        sb_widget: AutonaScoreboardWidget = self.scoreboard.GetScoreboard(scoreboard)
        # Committed view: pending (not yet 反映-applied) desktop edits must
        # not leak to remote clients ahead of the delta stream.
        return StateManager.GetCommitted(f'score.{sb_widget.scoreboardNumber}')

    def set_display_option(self, data):
        """Toggle a scoreboard field's visibility from a remote client.

        One write, to the one place the option lives (headless core Step 3).
        This used to walk boards 1 and 2 and poke each one's QAction --
        which *built* both boards as a side effect of a Web UI eye-menu
        click, and needed @gui_thread_sync to do it. Boards that exist mirror
        the change from the state notification instead.
        """
        option = (data or {}).get("option")
        # Reported from the operation's own answer rather than assumed: the
        # accepted key set is the schema's (headless core Step 2) and Actions
        # is the one that consults it, so a rejection there must not come back
        # to the client as "OK". The legacy route's error string is this
        # layer's contract, hence the wording rather than a bare False.
        if not AutonaActions.instance.SetDisplayOption(
                option, bool((data or {}).get("value"))):
            return f"ERROR: unknown display option {option}"
        return "OK"

    @gui_thread_sync
    def set_route(self,
                  scoreboard,
                  bestOf=None,
                  phase=None,
                  pool=None,
                  match=None,
                  players=None,
                  characters=None,
                  losers=None,
                  team=None,
                  teamName=None):
        # Best Of argument
        # best-of=<Best Of Amount>
        if bestOf is not None:
            self.scoreboard.GetScoreboard(scoreboard).signals.ChangeSetData.emit(
                orjson.loads(
                    orjson.dumps({'bestOf': int(bestOf)})
                )
            )

        # Phase argument
        # phase=<Phase Name>
        if phase is not None:
            self._apply_set_fields(scoreboard, {'tournament_phase': phase})

        # Pool argument
        # pool=<Pool Name>
        if pool is not None:
            self._apply_set_fields(scoreboard, {'tournament_pool': pool})

        # Match argument
        # match=<Match Name>
        if match is not None:
            self._apply_set_fields(scoreboard, {'round_name': match})

        # Players argument
        # players=<Amount of Players>
        if players is not None:
            self.scoreboard.GetScoreboard(scoreboard).playerNumber.setValue(int(players))

        # Characters argument
        # characters=<Amount of Characters>
        if characters is not None:
            self.scoreboard.GetScoreboard(scoreboard).charNumber.setValue(int(characters))

        # Losers argument
        # losers=<True/False>&team=<Team Number>
        # Set-shaped: `team` is the set's team, which a swap moves to the
        # other column (SetLoadService.ApplyLosers).
        if losers is not None:
            self._apply_set_fields(scoreboard, {
                'team' + str(team) + 'losers':
                    False if losers.lower() == 'false' else True})

        # Team name argument (team-mode boards)
        # teamName=<Name>&team=<Team Number>
        # The literal column (no swap flip), as the operator typing the name
        # into it: the name and the crest that follows it, written only when
        # the name changes. Boards 1/2 only -- the crew board has never had
        # the column this addressed.
        if teamName is not None and team is not None:
            board = self.scoreboard.EnsureScoreboard(scoreboard)
            idx = int(team)
            if idx in (1, 2) and int(scoreboard) in AutonaActions.SCORE_BOARDS:
                with StateManager.SaveBlock():
                    if AutonaActions.instance.SetTeamName(
                            scoreboard, idx, teamName):
                        board.setLoad.PublishTeamLogo(idx, teamName)
        return "OK"

    def _apply_set_fields(self, scoreboard, payload):
        """One set_route field, as the set payload it has always been.

        On boards 1/2 that payload is applied by core (ADR-005 S2d), as the
        board's ChangeSetData applied it: the match bar fields and the
        English round text (blanked by every payload that carries none --
        SetLoadService.ApplyMatchInfo), and a losers flag (ApplyLosers). The
        board is built first, as GetScoreboard() did, so the write waits for
        反映 like any other. The crew board's ChangeSetData is its own.
        """
        board = self.scoreboard.EnsureScoreboard(scoreboard)
        if int(scoreboard) not in AutonaActions.SCORE_BOARDS:
            board.signals.ChangeSetData.emit(payload)
            return
        with StateManager.SaveBlock():
            board.setLoad.ApplyMatchInfo(payload)
            board.setLoad.ApplyLosers(payload)

    def set_team_data(self, scoreboard, team, player, data):
        self.scoreboard.GetScoreboard(scoreboard).signals.ChangeSetData.emit({
            "team": team,
            "player": player,
            "data": data
        })
        return "OK"

    # ── /api/v1 only (Web UI migration, ADR-001 Addendum 2026-10-21) ──
    # The v1 counterparts of `post_score` / `set_team_data`. The legacy
    # routes keep the two methods above unchanged; these address the *state*
    # team and skip the set-payload side effects those carry (the score's
    # DisplayTeam flip and general.disable_scoreupdate, and match_en being
    # blanked by ChangeSetData). Except set_team_losers, which replaced a v1
    # route that already went through that payload: it keeps the match_en
    # blanking and the board build (see there).

    def set_scores(self, scoreboard, team1, team2):
        # Both teams in one save cycle. Pure state, so no GUI hop (headless
        # core Step 3): the board follows from the state notification.
        with StateManager.SaveBlock():
            AutonaActions.instance.SetScore(scoreboard, 1, team1)
            AutonaActions.instance.SetScore(scoreboard, 2, team2)
        return "OK"

    @gui_thread_sync
    def set_team_losers(self, scoreboard, team, value):
        """The v1 counterpart of `set_route`'s losers (ADR-005 S2b).

        `team` is the *state* team: AutonaActions.SetTeamLosers, which also
        puts the " [L]" on that team's player names. Not set_route's
        set-shaped `team{N}losers` payload, which SetLoadService.ApplyLosers
        runs through DisplayTeam() and so lands on the other side of a
        swapped board.

        What that payload did besides is kept, because it is visible from
        outside:

        - it built the board when it was not built yet (GetScoreboard), and
          building it is what holds score.{n} for 反映 -- without it the flag
          would be published at once (an unbuilt board is not held: moving
          that registration to startup was ADR-005 S2 decision 7, withdrawn
          in S2c);
        - it went through ChangeSetData, whose match-info half
          (SetLoadService.ApplyMatchInfo) blanks match_en for a payload that
          carries no round_name_en -- also when the flag does not change.
          Applied first, as ChangeSetData did, in the same save block: one
          save cycle, as before.

        On the GUI thread: it builds the board, and the cards publish their
        names from it.
        """
        board = self.scoreboard.EnsureScoreboard(scoreboard)
        if board is None:
            return f"ERROR: scoreboard {scoreboard} is not available"
        with StateManager.SaveBlock():
            board.setLoad.ApplyMatchInfo({})
            AutonaActions.instance.SetTeamLosers(scoreboard, team, value)
        return "OK"

    @gui_thread_sync
    def update_player(self, scoreboard, team, player, data):
        # The player widgets are the writers here (SetData resolves country,
        # character and skin models), so this runs on the GUI thread -- and
        # synchronously, unlike set_team_data's queued signal, so the caller
        # gets the outcome: False when the team has no such player slot (the
        # roster is checked on the GUI thread, against the live widgets);
        # an exception from the write propagates to the caller.
        return self.scoreboard.GetScoreboard(scoreboard).ApplyPlayerData(
            team, player, data)

    def set_commentary_data(self, index, data):
        # Caster slots are fixed (core.CommentaryModel.SLOTS); the old
        # auto-grow behavior is gone.
        index = int(index) - 1
        if not 0 <= index < len(comms.SLOTS):
            return f"ERROR : index must be 1..{len(comms.SLOTS)}"
        widget = (AutonaCommentaryWidget.instance
                  or self._ensure_panel("EnsureCommentaryPanel"))
        if widget is None:
            return "ERROR : commentary panel unavailable"
        widget.ChangeCommDataSignal.emit(index, data)

        return "OK"

    @gui_thread_sync
    def set_game(self, data):
        # Not actually sure if this needs to be in the GUI thread but the asset manager is complex enough that it seems
        # worthwhile to dispatch it like this.
        set_codename = data.get("codename")
        if set_codename not in GameAssetCatalog.instance.GameCodenames():
            return f"Could not find game {set_codename}"

        # By codename, not by its position in the scan, which the load would
        # re-read: a rescan in between could load another game.
        AutonaGameAssetManager.instance.LoadGameByCodename(
            set_codename, async_mode=False,
            mods_active=data.get("mods_active", False), mods_reload_mode=True)

        return "OK"

    # ── Game asset readers (ADR-005 S1b) ──────────────────────────────
    # Read the Qt-free catalog, not the Qt models, so no @gui_thread_sync:
    # they answer on the calling (Flask / Socket.IO) thread. Each payload is
    # built from one reference the catalog takes once (the scan for games,
    # the loaded snapshot for characters / variants), so a load or rescan
    # landing mid-request cannot mix two of them -- get_games used to
    # re-read the scan per key, so a rescan in between could raise KeyError.

    def get_games(self):
        return GameAssetCatalog.instance.GamesPayload()

    def get_current_game(self):
        return StateManager.Get("game")
    
    def get_match_names(self):
        response = {
            "match": AutonaLocaleHelper.matchNames,
            "phase": AutonaLocaleHelper.phaseNames
        }
        return response

    def get_characters(self):
        return GameAssetCatalog.instance.CharactersPayload()

    def get_variants(self):
        return GameAssetCatalog.instance.VariantsPayload()

    # ── Swap (ADR-005 S2c) ───────────────────────────────────────────────
    # A 1v1 / team board's swap is a state operation (AutonaActions.SwapTeams)
    # and its flag is `score.{n}.teamsSwapped`. Both routes still build a
    # board that is not built yet (GetScoreboard), as they always did: an
    # unbuilt board is not held, so a swap made there would be published at
    # once -- to a board no 反映 button shows -- and then wiped by the seed
    # the board's construction writes. Built first, the swap waits for 反映
    # on the board like any other edit. The crew board still swaps itself,
    # through its widget.

    @gui_thread_sync
    def swap_teams(self, scoreboard):
        # On the GUI thread although the operation is core's: the player
        # cards still publish a typed edit in two writes on the GUI thread
        # (the field, then the merged name), and a swap run from the Flask
        # thread could land between them and leave one slot's name on the
        # other. Until those writes are a single operation (ADR-005 S2f).
        # It also makes the swap synchronous -- the old queued signal
        # answered "OK" before anything had moved -- and lets the board
        # finish a field being typed in first (SwapTeams ->
        # CommitFocusedEdit).
        board = self.scoreboard.GetScoreboard(scoreboard)
        if int(scoreboard) in AutonaActions.SCORE_BOARDS:
            board.SwapTeams()
        else:
            board.signals.SwapTeams.emit()
        return "OK"

    def get_swap(self, scoreboard):
        # The live flag, like the widget attribute it replaces: a swap is
        # reported as soon as it is made, before 反映 publishes it.
        board = self.scoreboard.GetScoreboard(scoreboard)
        if int(scoreboard) in AutonaActions.SCORE_BOARDS:
            return str(bool(StateManager.Get(
                f"score.{int(scoreboard)}.teamsSwapped", False)))
        return str(board.teamsSwapped)

    def open_sets(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).signals.SetSelection.emit()
        return "OK"

    def pull_stream_set(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).signals.StreamSetSelection.emit()
        return "OK"

    def stats_recent_sets(self, scoreboard):
        self.scoreboard.GetScoreboard(
            scoreboard).stats.signals.RecentSetsSignal.emit()
        return "OK"

    def stats_upset_factor(self, scoreboard):
        self.scoreboard.GetScoreboard(
            scoreboard).stats.signals.UpsetFactorCalculation.emit()
        return "OK"

    def stats_last_sets(self, scoreboard, player):
        if str(player) == "1":
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.LastSetsP1Signal.emit()
        elif str(player) == "2":
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.LastSetsP2Signal.emit()
        elif player == "both":
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.LastSetsP1Signal.emit()
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.LastSetsP2Signal.emit()
        else:
            logger.error(
                "[Last Sets] Unable to find player defined. Allowed values are: 1, 2, or both")
        return "OK"

    @gui_thread_sync
    def stats_history_sets(self, scoreboard, player):
        if str(player) == "1":
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.PlayerHistoryStandingsP1Signal.emit()
        elif str(player) == "2":
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.PlayerHistoryStandingsP2Signal.emit()
        elif player == "both":
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.PlayerHistoryStandingsP1Signal.emit()
            self.scoreboard.GetScoreboard(
                scoreboard).stats.signals.PlayerHistoryStandingsP2Signal.emit()
        else:
            logger.error(
                "[History Standings] Unable to find player defined. Allowed values are: 1, 2, or both")
        return "OK"

    @gui_thread_sync
    def refresh_stats(self, scoreboard):
        """Recalculate every statistic for a board.

        Emits the same signal set as the STATS panel's
        「統計情報を取得する」 button (AutonaStatsWidget._refresh), so both
        entry points stay identical. The fetches themselves are async
        (start.gg), so this returns as soon as the signals are queued; the
        results land in the state a moment later. The stats util already
        no-ops on non-1v1 boards, so no extra guard is needed here.
        """
        sb = self.scoreboard.GetScoreboard(scoreboard)
        stats = getattr(sb, "stats", None)
        if stats is None:
            logger.warning(
                f"[Stats] scoreboard {scoreboard} has no stats util")
            return f"ERROR: scoreboard {scoreboard} does not support stats"
        stats.signals.RecentSetsSignal.emit()
        stats.signals.LastSetsP1Signal.emit()
        stats.signals.LastSetsP2Signal.emit()
        stats.signals.PlayerHistoryStandingsP1Signal.emit()
        stats.signals.PlayerHistoryStandingsP2Signal.emit()
        stats.signals.UpsetFactorCalculation.emit()
        return "OK"

    # @gui_thread_sync is still required: boards 1/2 go through Actions, but
    # a crew board's "reset score" means restoring every member's stocks —
    # widget-owned state that Step 3 did not migrate (see core/Actions.py).
    @gui_thread_sync
    def reset_scores(self, scoreboard):
        if AutonaActions.instance.ResetScore(scoreboard):
            return "OK"
        self.scoreboard.GetScoreboard(scoreboard).ResetScore()
        return "OK"

    def apply_changes(self, scoreboard):
        # 反映: publish the board's pending (held) edits — same commit the
        # desktop 反映 button / hotkey performs. Pure state, so no GUI hop
        # and no board is built to serve it (headless core Step 3).
        AutonaActions.instance.ApplyChanges(scoreboard)
        return "OK"

    def _reset_best_of(self, board):
        # The best_of QSpinBox is reparented out of `scoreColumn` while the
        # 1v1 / team board is built (AutonaScoreboardWidget moves it into the
        # score bar), so the historical scoreColumn.findChild(...) lookup
        # returns None there and raised AttributeError. Prefer the reference
        # the widget already stores; fall back to findChild for any board that
        # still nests the spinbox under scoreColumn (crew uses `_bestOfSpin`).
        # Still the spinbox: it is the best of's entry point until S2e.
        best_of = (getattr(board, "bestOfSpin", None)
                   or getattr(board, "_bestOfSpin", None)
                   or board.scoreColumn.findChild(QSpinBox, "best_of"))
        if best_of is not None:
            best_of.setValue(0)

    # The clears are core operations (ADR-005 S2d); the crew board (3)
    # answers False to them and keeps its own. Every one of these builds the
    # board first, as the GetScoreboard() they used to start with did: an
    # unbuilt board is not held, so a clear made there would be published at
    # once and then wiped by the seed that builds it -- and the best of /
    # counts below are still the board's spinboxes. Kept on the GUI thread
    # until S2f, like swap_teams: a player card's edit is two writes there
    # (the field, then the merged name), and a clear must not land between.
    #
    # reset_match / clear_all are operator resets (scores + losers flags +
    # round name / phase / pool), not the set-load pre-pass ClearScore,
    # which keeps the match bar (S-17).
    @gui_thread_sync
    def reset_match(self, scoreboard):
        board = self.scoreboard.EnsureScoreboard(scoreboard)
        if not AutonaActions.instance.ResetMatch(scoreboard):
            board.ResetMatch()
        self._reset_best_of(board)
        return "OK"

    @gui_thread_sync
    def reset_players(self, scoreboard):
        # The board's CommandClearAll is AutonaActions.ClearPlayers plus what
        # the board owns around it (the field being typed in, the set loaded
        # last, the statistics) -- the crew's is its own clear.
        self.scoreboard.EnsureScoreboard(scoreboard).CommandClearAll()
        return "OK"

    @gui_thread_sync
    def clear_all(self, scoreboard):
        # Not AutonaActions.ClearBoard (the desktop's reset-all): this keeps
        # the team names, and the player / character counts (still the
        # board's spinboxes, S2g) change between the reset and the players'
        # clear -- in this order, which decides what the mains end up as.
        board = self.scoreboard.EnsureScoreboard(scoreboard)
        if not AutonaActions.instance.ResetMatch(scoreboard):
            board.ResetMatch()
        self._reset_best_of(board)
        board.playerNumber.setValue(1)
        board.charNumber.setValue(1)
        board.CommandClearAll()
        return "OK"

    @gui_thread_sync
    def get_thumbnail(self, scoreboard, file_format):
        thumbnailPath = self.scoreboard.GetScoreboard(scoreboard).GenerateThumbnail(quiet_mode=True, disable_msgbox=True)
        if thumbnailPath:
            if file_format == "jpg":
                thumbnailPath = thumbnailPath.replace(".png", ".jpg")
            return os.path.abspath(thumbnailPath)
        else:
            return None

    @gui_thread_sync
    def update_bracket(self):
        # /bracket:refresh — kept for external-macro compatibility. The Top 8
        # panel resolves the fixed target (the tournament's final double
        # elimination phase) and re-fetches it, queuing behind a phases fetch
        # when needed.
        #
        # The panel is built on demand rather than left to the fallback below
        # (startup Step 4b-2): before that step it always existed, so taking
        # the fallback for an unopened panel would change what this route
        # does — the raw fetch below emits tournament_phasegroup_updated,
        # which with no Top 8 panel listening updates nothing. The fallback
        # stays for the case where the panel cannot be built at all.
        from .AutonaTop8Widget import AutonaTop8Widget
        panel = (AutonaTop8Widget.instance
                 or self._ensure_panel("EnsureTop8Panel"))
        if panel is not None:
            panel.RefreshPhaseGroup()
            return "OK"
        id = AutonaTournamentDataProvider.instance.provider.GetTournamentPhases()[0].get("groups")[0].get("id")
        data = AutonaTournamentDataProvider.instance.provider.GetTournamentPhaseGroup(id)
        AutonaTournamentDataProvider.instance.signals.tournament_phasegroup_updated.emit(data)
        return "OK"

    @gui_thread_sync
    def load_set(self, scoreboard, set=None, no_mains=False):
        if set is not None:
            if not isinstance(set, str):
                set = '0'
            self.scoreboard.GetScoreboard(scoreboard).signals.NewSetSelected.emit(
                orjson.loads(
                    orjson.dumps({
                        'id': set,
                        'auto_update': "set",
                        'no_mains': no_mains
                    })
                )
            )
        return "OK"

    def get_comms(self):
        return StateManager.Get("commentary")

    # On the GUI thread now that it builds the board itself (EnsureScoreboard
    # is GUI-thread only); it was built before through GetScoreboard, which
    # marshals the build. The set is the board's SetLoadService's (ADR-005
    # S2d); the crew board has none, and raises as it did.
    @gui_thread_sync
    def get_set(self, scoreboard):
        lastSet = self.scoreboard.EnsureScoreboard(
            scoreboard).setLoad.lastSetSelected
        if lastSet is None:
            return "0"
        else:
            return str(lastSet)

    def get_sets(self, args):
        provider = AutonaTournamentDataProvider.instance.GetProvider()
        if provider is None:
            return []

        if args.get('getFinished') is not None:
            sets = provider.GetMatches(getFinished=True)
            return sets
        else:
            sets = provider.GetMatches(getFinished=False)
            return sets

    def get_playerdb(self):
        # Same rule as the desktop name completers: only the loaded
        # tournament's entrants when one is loaded, the whole DB
        # otherwise. Clients receive this on (re)connect.
        tags = AutonaPlayerDB.tournamentTags
        if tags:
            return {tag: player
                    for tag, player in AutonaPlayerDB.database.items()
                    if tag in tags}
        return AutonaPlayerDB.database

    def get_match(self, setId=None):
        provider = AutonaTournamentDataProvider.instance.GetProvider()
        return provider.GetMatch(setId=int(setId))

    @gui_thread_sync
    def load_player_from_tag(self, scoreboard, tag, team, player, no_mains=False):
        result = self.scoreboard.GetScoreboard(scoreboard).LoadPlayerFromTag(str(tag), int(team), int(player), no_mains)
        if result == True:
            return "OK"
        else:
            return "ERROR"

    def load_commentator_from_tag(self, index, tag, no_mains=False):
        index = int(index) - 1
        if not 0 <= index < 8:
            return "ERROR : index must be 1..8"
        widget = (AutonaCommentaryWidget.instance
                  or self._ensure_panel("EnsureCommentaryPanel"))
        if widget is None:
            return "ERROR : commentary panel unavailable"
        widget.LoadCommFromTagSignal.emit(index, tag, no_mains)

    def load_tournament(self, url=None):
        logger.error(f"URL PROVIDED: {url}")
        if url is None or url == "":
            AutonaTournamentDataProvider.instance.signals.tournament_url_update.emit(None)
            return "OK"
        else:
            validators = [
                QRegularExpression("start.gg/tournament/[^/]+/event[s]?/[^/]+")
            ]

            for validator in validators:
                    match = validator.match(url).capturedTexts()
                    if len(match) > 0:
                        continue
            
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

    def get_states(self, countryCode: str):
        # The country catalog, read on the calling (Flask / Socket.IO)
        # thread: no GUI hop (ADR-005 S1c). Each region comes with its flag
        # `asset`, whichever countries the desktop has shown so far.
        return CountryCatalog.instance.States(countryCode)
