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
            self.scoreboard.GetScoreboard(scoreboard).signals.ChangeSetData.emit(
                orjson.loads(
                    orjson.dumps({'tournament_phase': phase})
                )
            )

        # Pool argument
        # pool=<Pool Name>
        if pool is not None:
            self.scoreboard.GetScoreboard(scoreboard).signals.ChangeSetData.emit(
                orjson.loads(
                    orjson.dumps({'tournament_pool': pool})
                )
            )

        # Match argument
        # match=<Match Name>
        if match is not None:
            self.scoreboard.GetScoreboard(scoreboard).signals.ChangeSetData.emit(
                orjson.loads(
                    orjson.dumps({'round_name': match})
                )
            )

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
        if losers is not None:
            self.scoreboard.GetScoreboard(scoreboard).signals.ChangeSetData.emit(
                orjson.loads(
                    orjson.dumps({'team' + str(team) + 'losers': False if losers.lower() == 'false' else True})
                )
            )

        # Team name argument (team-mode boards)
        # teamName=<Name>&team=<Team Number>
        # Drives the Qt QLineEdit like the set-load path does, so the widget,
        # StateManager and the broadcast all stay in sync.
        if teamName is not None and team is not None:
            sb = self.scoreboard.GetScoreboard(scoreboard)
            columns = [getattr(sb, "team1column", None),
                       getattr(sb, "team2column", None)]
            idx = int(team) - 1
            if 0 <= idx < len(columns) and columns[idx] is not None:
                edit = columns[idx].findChild(QLineEdit, "teamName")
                if edit is not None and edit.text() != teamName:
                    edit.setText(teamName)
                    edit.editingFinished.emit()
        return "OK"

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
          would be published at once. Until the held registration moves to
          bootstrap (ADR-005 S2, decision 7);
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

    def swap_teams(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).signals.SwapTeams.emit()
        return "OK"
    
    def get_swap(self, scoreboard):
        return str(self.scoreboard.GetScoreboard(scoreboard).teamsSwapped)

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

    def _reset_best_of(self, scoreboard):
        # The best_of QSpinBox is reparented out of `scoreColumn` while the
        # 1v1 / team board is built (AutonaScoreboardWidget moves it into the
        # score bar), so the historical scoreColumn.findChild(...) lookup
        # returns None there and raised AttributeError. Prefer the reference
        # the widget already stores; fall back to findChild for any board that
        # still nests the spinbox under scoreColumn (crew uses `_bestOfSpin`).
        sb = self.scoreboard.GetScoreboard(scoreboard)
        best_of = (getattr(sb, "bestOfSpin", None)
                   or getattr(sb, "_bestOfSpin", None)
                   or sb.scoreColumn.findChild(QSpinBox, "best_of"))
        if best_of is not None:
            best_of.setValue(0)

    # reset_match / clear_all are operator resets, so they go through the
    # board's ResetMatch (scores + losers flags + round name / phase / pool),
    # not the set-load pre-pass ClearScore, which keeps the match bar (S-17).
    @gui_thread_sync
    def reset_match(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).ResetMatch()
        self._reset_best_of(scoreboard)
        return "OK"

    @gui_thread_sync
    def reset_players(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).CommandClearAll()
        return "OK"

    @gui_thread_sync
    def clear_all(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).ResetMatch()
        self._reset_best_of(scoreboard)
        self.scoreboard.GetScoreboard(scoreboard).playerNumber.setValue(1)
        self.scoreboard.GetScoreboard(scoreboard).charNumber.setValue(1)
        self.scoreboard.GetScoreboard(scoreboard).CommandClearAll()
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

    def get_set(self, scoreboard):
        if self.scoreboard.GetScoreboard(scoreboard).lastSetSelected is None:
            return "0"
        else:
            return str(self.scoreboard.GetScoreboard(scoreboard).lastSetSelected)

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
