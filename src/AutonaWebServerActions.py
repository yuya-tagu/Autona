"""The web layer's board-dependent operations (ADR-005 S3c).

What is left here needs a board or panel *widget* to answer: the player
cards (update_player, load_player_from_tag), the set payloads a board's
ChangeSetData applies (post_score, set_team_data), the set selection and
stream-set follow (open_sets, pull_stream_set, load_set), the statistics,
the thumbnail, the crew board's members, and the Commentary / Top 8 panels.
Every operation that is core's moved to src/AutonaWebOperations.py, which
both shells share; this object exists only in the desktop shell (the window
hands it to the WebServer), and in the headless shell the routes behind it
answer 503 (AutonaWebOperations.NotAvailable). It goes in S3e, once these
are core operations too.
"""
import os
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import orjson

from .Helpers.AutonaQtHelper import gui_thread_sync
from loguru import logger
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaCommentaryWidget import AutonaCommentaryWidget
from .core import CommentaryModel as comms


class WebServerActions(QObject):
    def __init__(self, parent=None, scoreboard=None) -> None:
        super().__init__(parent)
        self.scoreboard = scoreboard

    # ── Lazily-built panels (startup Step 4b-2) ──────────────────────
    # The Commentary and Top 8 panels are constructed the first time their
    # sidebar page is revealed, so an API call can arrive while they do not
    # exist yet. The class `instance` attribute is the fast path; when it is
    # None the main window is asked to build the panel. `parent` is that
    # window (see Window._StartWebServer).

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
