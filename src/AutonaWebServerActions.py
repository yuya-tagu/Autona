import os
import re
from flask import abort
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
import orjson

from .Helpers.AutonaCountryHelper import AutonaCountryHelper
from .Helpers.AutonaQtHelper import gui_thread_async, gui_thread_sync
from .StateManager import StateManager
from .AutonaStatsUtil import AutonaStatsUtil
from .SettingsManager import SettingsManager
from loguru import logger
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaPlayerDB import AutonaPlayerDB
from .AutonaScoreboardWidget import AutonaScoreboardWidget
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .AutonaCommentaryWidget import AutonaCommentaryWidget
from .Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from .Workers import Worker
import os

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


class WebServerActions(QThread):
    def __init__(self, parent=None, scoreboard=None, commentaryWidget: AutonaCommentaryWidget=None) -> None:
        super().__init__(parent)
        self.scoreboard = scoreboard
        self.commentaryWidget = commentaryWidget
        self.threadPool = QThreadPool()

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

    def team_scoreup(self, scoreboard, team):
        if str(team) == "1":
            self.scoreboard.GetScoreboard(scoreboard).signals.CommandScoreChange.emit(0, 1)
        else:
            self.scoreboard.GetScoreboard(scoreboard).signals.CommandScoreChange.emit(1, 1)
        return "OK"

    def team_scoredown(self, scoreboard, team):
        if str(team) == "1":
            self.scoreboard.GetScoreboard(scoreboard).signals.CommandScoreChange.emit(0, -1)
        else:
            self.scoreboard.GetScoreboard(scoreboard).signals.CommandScoreChange.emit(1, -1)
        return "OK"

    def team_color(self, scoreboard, team, color):
        if str(team) == "1":
            self.scoreboard.GetScoreboard(scoreboard).signals.CommandTeamColor.emit(0, color)
        else:
            self.scoreboard.GetScoreboard(scoreboard).signals.CommandTeamColor.emit(1, color)
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
        return StateManager.Get(f'score.{sb_widget.scoreboardNumber}')

    # Eye-menu keys accepted by set_display_option (mirrors
    # AutonaScoreboardWidget.elements settings keys).
    _DISPLAY_OPTION_KEYS = (
        "show_sponsor", "show_name", "show_seed", "show_characters",
        "show_social", "show_real_name", "show_location", "show_additional")

    @gui_thread_sync
    def set_display_option(self, data):
        """Toggle a scoreboard field's visibility from a remote client.

        Drives the same eye-menu action path as a local click so Qt widget
        visibility, SettingsManager and StateManager stay consistent on
        every board (Design.md §3.2.2 R-Sync-1: writes go through Actions).
        """
        option = (data or {}).get("option")
        if option not in WebServerActions._DISPLAY_OPTION_KEYS:
            return f"ERROR: unknown display option {option}"
        value = bool((data or {}).get("value"))
        for n in (1, 2):
            sb = self.scoreboard.GetScoreboard(n)
            if hasattr(sb, "SetDisplayOption"):
                sb.SetDisplayOption(option, value)
        return "OK"

    @gui_thread_sync
    def set_route(self,
                  scoreboard,
                  bestOf=None,
                  phase=None,
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

    def set_commentary_data(self, index, data):
        # Caster slots are fixed 1..8 (AutonaCommentaryModel.SLOTS); the
        # old auto-grow behavior is gone.
        index = int(index) - 1
        if not 0 <= index < 8:
            return "ERROR : index must be 1..8"
        self.commentaryWidget.ChangeCommDataSignal.emit(index, data)

        return "OK"

    @gui_thread_sync
    def set_game(self, data):
        # Not actually sure if this needs to be in the GUI thread but the asset manager is complex enough that it seems
        # worthwhile to dispatch it like this.
        set_codename = data.get("codename")
        found_game = False
        for i, codename in enumerate(AutonaGameAssetManager.instance.games.keys()):
            if codename == set_codename:
                # AutonaGameAssetManager.instance.selectedGame = AutonaGameAssetManager.instance.games[codename]
                # self.parent().SetGame()
                AutonaGameAssetManager.instance.LoadGameAssets(i+1, async_mode=False, mods_active=data.get("mods_active", False), mods_reload_mode=True)
                found_game = True
                break

        if not found_game:
            return f"Could not find game {set_codename}"

        return "OK"

    def get_games(self):
        data = {}
        for key in AutonaGameAssetManager.instance.games.keys():
            data[key] = {
                "name": AutonaGameAssetManager.instance.games[key].get("name"),
                "locale": AutonaGameAssetManager.instance.games[key].get("locale"),
                "smashgg_game_id": AutonaGameAssetManager.instance.games[key].get("smashgg_game_id"),
                "has_stages": bool(AutonaGameAssetManager.instance.games[key].get("stage_to_codename")),
                "has_variants": bool(AutonaGameAssetManager.instance.games[key].get("variant_to_codename")),
                "has_colors": bool(AutonaGameAssetManager.instance.games[key].get("preset_colors"))
            }

        return data
    
    def get_current_game(self):
        return StateManager.Get("game")
    
    def get_match_names(self):
        response = {
            "match": AutonaLocaleHelper.matchNames,
            "phase": AutonaLocaleHelper.phaseNames
        }
        return response

    @gui_thread_sync
    def get_characters(self):
        data = {}
        for row in range(AutonaGameAssetManager.instance.characterModel.rowCount()):
            item: QStandardItem = AutonaGameAssetManager.instance.characterModel.index(
                row, 0)
            item_data = item.data(Qt.ItemDataRole.UserRole)

            if item_data is not None:
                skin_models = AutonaGameAssetManager.instance.skinModels.get(item_data.get("en_name"))
                item_data["skins"] = []
                if skin_models is not None:
                    for skindex in range(skin_models.rowCount()):
                        item_data["skins"].append(skin_models.index(skindex, 0).data(Qt.ItemDataRole.UserRole))
                data[item_data.get("name")] = item_data

        return data

    @gui_thread_sync
    def get_variants(self):
        data = {}
        for row in range(AutonaGameAssetManager.instance.variantModel.rowCount()):
            item: QStandardItem = AutonaGameAssetManager.instance.variantModel.index(
                row, 0)
            item_data = item.data(Qt.ItemDataRole.UserRole)

            if item_data is not None:
                data[item_data.get("name")] = item_data
        return data

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
        AutonaStatsUtil.instance.signals.RecentSetsSignal.emit()
        return "OK"

    def stats_upset_factor(self, scoreboard):
        AutonaStatsUtil.instance.signals.UpsetFactorCalculation.emit()
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
    def reset_scores(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).ResetScore()
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

    @gui_thread_sync
    def reset_match(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).ClearScore()
        self._reset_best_of(scoreboard)
        return "OK"

    @gui_thread_sync
    def reset_players(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).CommandClearAll()
        return "OK"

    @gui_thread_sync
    def clear_all(self, scoreboard):
        self.scoreboard.GetScoreboard(scoreboard).ClearScore()
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
        from .AutonaTop8Widget import AutonaTop8Widget
        panel = AutonaTop8Widget.instance
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
        self.commentaryWidget.LoadCommFromTagSignal.emit(index, tag, no_mains)

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

    @gui_thread_sync
    def get_states(self, countryCode: str):
        return AutonaCountryHelper.GetStates(countryCode)
