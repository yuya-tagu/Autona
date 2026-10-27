"""The web layer's board-dependent operations (ADR-005 S3c).

What is left here needs a board or panel *widget* to answer: the player
cards (update_player, load_player_from_tag), the set payloads a board's
ChangeSetData applies (post_score, set_team_data), the set selection and
stream-set follow (open_sets, pull_stream_set, load_set), the statistics
and the thumbnail. Every operation that is core's moved to
src/AutonaWebOperations.py, which both shells share (the crew board's
members and the caster line-up with ADR-005 S3e-2, the Top 8 import with
S3e-8); this object
exists only in the desktop shell (the window hands it to the WebServer), and
in the headless shell the routes behind it answer 503
(AutonaWebOperations.NotAvailable). It goes in S3e, once these are core
operations too.
"""
import os
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import orjson

from .Helpers.AutonaQtHelper import gui_thread_sync
from loguru import logger


class WebServerActions(QObject):
    def __init__(self, parent=None, scoreboard=None) -> None:
        super().__init__(parent)
        self.scoreboard = scoreboard

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

    def set_team_data(self, scoreboard, team, player, data):
        self.scoreboard.GetScoreboard(scoreboard).signals.ChangeSetData.emit({
            "team": team,
            "player": player,
            "data": data
        })
        return "OK"

    # ── /api/v1 only (Web UI migration, ADR-001 Addendum 2026-10-21) ──
    # The v1 counterpart of `set_team_data` (score:set's, set_scores, and
    # set_team_losers are core's: AutonaWebOperations). The legacy route
    # keeps the method above unchanged; this addresses the *state* team and
    # skips the set-payload side effect it carries (match_en being blanked
    # by ChangeSetData).

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

    @gui_thread_sync
    def load_player_from_tag(self, scoreboard, tag, team, player, no_mains=False):
        result = self.scoreboard.GetScoreboard(scoreboard).LoadPlayerFromTag(str(tag), int(team), int(player), no_mains)
        if result == True:
            return "OK"
        else:
            return "ERROR"
