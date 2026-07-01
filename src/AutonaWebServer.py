import html
import json
import os
import subprocess
import sys
import threading
import time
import traceback

import flask
import flask_socketio
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *
from flask import Flask, send_from_directory, request, send_file, abort, jsonify
from flask_cors import CORS, cross_origin
from flask_socketio import SocketIO, emit
import orjson
from loguru import logger

from .StateManager import StateManager
from .AutonaWebServerActions import WebServerActions
from .AutonaScoreboardManager import AutonaScoreboardManager
from .AutonaCommentaryWidget import AutonaCommentaryWidget
from .SettingsManager import SettingsManager
from .AutonaDesignerService import (
    AutonaDesignerService,
    DesignerProjectError,
)
import traceback

import logging
log = logging.getLogger('socketio.server')
log.setLevel(logging.ERROR)

class SocketioJson:
    def default(obj):
        if isinstance(obj, type(type(1))):
            return str(obj)
        return obj

    def dumps(*args, **kwargs):
        return json.dumps(*args, **kwargs, default=SocketioJson.default)

    def loads(*args, **kwargs):
        return json.loads(*args, **kwargs)


class WebServer(QThread):
    app = Flask(__name__, static_folder=os.path.curdir)
    cors = CORS(app)
    socketio = SocketIO(
        app,
        cors_allowed_origins='*',
        # Uncomment to enable SocketIO logging (As logging is unuseful, we'll make this a dev flag)
        # logger=logger,
        async_mode='threading',
        json=SocketioJson
    )
    app.config['CORS_HEADERS'] = 'Content-Type'
    actions = None

    def __init__(self, parent=None, commentaryWidget: AutonaCommentaryWidget=None) -> None:
        super().__init__(parent)
        WebServer.actions = WebServerActions(
            parent=parent,
            scoreboard=AutonaScoreboardManager.instance,
            commentaryWidget=commentaryWidget
        )

        StateManager.signals.state_updated.connect(WebServer.on_program_state_update)
        StateManager.signals.state_big_change.connect(WebServer.ws_program_state)

        # External Control API (/api/v1/). Added as a thin Blueprint that
        # reuses WebServer.actions; existing routes/events are untouched
        # (ADR-001 D3 / .claude/design/external-control-api.md §9). Imported
        # lazily here to keep singleton init order intact (agent-pitfalls #2).
        from .AutonaApiV1 import api_v1, init_api_v1
        if "api_v1" not in WebServer.app.blueprints:
            WebServer.app.register_blueprint(api_v1)
        init_api_v1(WebServer.actions)

        self.host_name = "0.0.0.0"
        self.port = SettingsManager.Get("general.webserver_port", 5000)

    @staticmethod
    @app.before_request
    def log_request():
        # Debug level: OBS / Web UI poll dozens of endpoints per second.
        # At INFO this dominates the log file and stdout, drowning real signal.
        logger.debug(f"[FLASK] → {request.method} {request.path} from {request.remote_addr}")

    @staticmethod
    @app.after_request
    def log_response(response):
        logger.debug(f"[FLASK] ← {request.method} {request.path} [{response.status_code}]")
        return response

    @app.route('/program-state')
    def program_state():
        return WebServer.actions.program_state()

    @socketio.on('program-state-update')
    def ws_program_state_update(message):
        WebServer.ws_emit('program_state_update', {})

    # ── Delta coalescing ─────────────────────────────────────────
    # Without throttling, every StateManager.Set outside a SaveBlock
    # broadcasts a websocket event. Loading a set / phase group can
    # generate dozens of small deltas in quick succession; the per-emit
    # serialization + flush cost adds up and competes with OBS for
    # network/CPU. Coalesce on an adaptive interval — short for small
    # bursts (snappy UI), longer for large bursts (CPU breathing room).
    _delta_lock = threading.Lock()
    _delta_pending = []          # list of merged delta-flat-dicts
    _delta_pending_revision = -1 # latest revision in batch (Design.md §3.2.3 R-Sync-6)
    _delta_timer = None          # threading.Timer awaiting flush

    @staticmethod
    def _adaptive_debounce_sec(pending_count: int) -> float:
        # < 10 deltas: keep latency low for real-time feel.
        # 10-99 deltas: medium burst (typical SetFromSet path).
        # 100+ deltas: large burst (full reload) — let the OS catch up.
        if pending_count < 10:
            return 0.05
        if pending_count < 100:
            return 0.1
        return 0.2

    @staticmethod
    def _flush_pending_deltas():
        with WebServer._delta_lock:
            if not WebServer._delta_pending:
                WebServer._delta_timer = None
                return
            # Emit both `revision` (canonical) and `delta_index` (legacy
            # alias) so old OBS templates / not-yet-rebuilt clients keep
            # working (Design.md §3.2.3 R-Sync-6).
            payload = {
                'revision': WebServer._delta_pending_revision,
                'delta_index': WebServer._delta_pending_revision,
                'delta': WebServer._delta_pending,
            }
            WebServer._delta_pending = []
            WebServer._delta_pending_revision = -1
            WebServer._delta_timer = None
        try:
            WebServer.ws_emit('program_state_update', payload)
        except TypeError:
            logger.warning("Unserializable program state update")
            WebServer.ws_program_state()
        except Exception:
            logger.error(traceback.format_exc())

    def on_program_state_update(changes):
        if not changes:
            return

        delta = changes.get('delta') or []
        # Prefer `revision` (Design.md §3.2.3) and fall back to legacy
        # `delta_index` so this stays compatible with older payloads.
        rev = changes.get('revision', changes.get('delta_index', -1))

        with WebServer._delta_lock:
            WebServer._delta_pending.extend(delta)
            if rev > WebServer._delta_pending_revision:
                WebServer._delta_pending_revision = rev

            if WebServer._delta_timer is None:
                interval = WebServer._adaptive_debounce_sec(
                    len(WebServer._delta_pending))
                WebServer._delta_timer = threading.Timer(
                    interval, WebServer._flush_pending_deltas)
                WebServer._delta_timer.daemon = True
                WebServer._delta_timer.start()

    @socketio.on('connect')
    def ws_connect(message):
        WebServer.ws_program_state(message)

    @socketio.on('program_state')
    def ws_program_state(message=None):
        WebServer.ws_emit('program_state', WebServer.actions.program_state())

    @socketio.on_error_default
    def ws_on_error(e):
        logger.error(traceback.format_exc())

    # Don't override the QObject emit() method
    def ws_emit(event, *args, **kwargs):
        # flask_socketio doesn't know how to broadcast things outside the context
        # of a flask request. But since we're potentially broadcasting state changes
        # that don't originate from a request, we can handle both cases transparently
        # here.
        if flask.has_request_context():
            flask_socketio.emit(event, *args, **kwargs)
        else:
            # socketio.emit() doesn't know about the json parameter.
            if 'json' in kwargs:
                del kwargs['json']
            WebServer.socketio.emit(event, *args, **kwargs)

    @app.route('/score', methods=['POST'])
    def post_score():
        return WebServer.actions.post_score(request.get_data())

    @socketio.on('score')
    def ws_post_score(message):
        WebServer.ws_emit('score', WebServer.actions.post_score(message))

    # Ticks score of Team specified up by 1 point
    @app.route('/scoreboard<scoreboardNumber>-team<team>-scoreup')
    def team_scoreup(scoreboardNumber, team):
        return WebServer.actions.team_scoreup(scoreboardNumber, team)

    @socketio.on('team_scoreup')
    def ws_team_scoreup(message):
        info = orjson.loads(message)
        WebServer.ws_emit('team_scoreup',
             WebServer.actions.team_scoreup(info.get("scoreboardNumber", "1"), info.get("team")))

    # Ticks score of Team specified down by 1 point
    @app.route('/scoreboard<scoreboardNumber>-team<team>-scoredown')
    def team_scoredown(scoreboardNumber, team):
        return WebServer.actions.team_scoredown(scoreboardNumber, team)

    @socketio.on('team_scoredown')
    def ws_team_scoredown(message):
        info = orjson.loads(message)
        WebServer.ws_emit('team_scoredown',
             WebServer.actions.team_scoredown(info.get("scoreboardNumber", "1"), info.get("team")))

    # Set color of team
    @app.route('/scoreboard<scoreboardNumber>-team<team>-color-<color>')
    def team_color(scoreboardNumber, team, color):
        return WebServer.actions.team_color(scoreboardNumber, team, "#" + color)

    @socketio.on('team_color')
    def ws_team_color(message):
        info = orjson.loads(message)
        WebServer.ws_emit('team_scoredown',
             WebServer.actions.team_color(info.get("scoreboardNumber", "1"), info.get("team"), "#" + info.get("color")))

    # ── Crewbattle (board 3) member controls ──
    # Bump a crew member's remaining stock up/down by 1.
    @app.route('/scoreboard<scoreboardNumber>-crew-member<team>-<member>-stockup')
    def crew_member_stockup(scoreboardNumber, team, member):
        return WebServer.actions.crew_member_stock(scoreboardNumber, team, member, 1)

    @app.route('/scoreboard<scoreboardNumber>-crew-member<team>-<member>-stockdown')
    def crew_member_stockdown(scoreboardNumber, team, member):
        return WebServer.actions.crew_member_stock(scoreboardNumber, team, member, -1)

    @socketio.on('crew_member_stock')
    def ws_crew_member_stock(message):
        info = orjson.loads(message)
        WebServer.ws_emit('crew_member_stock',
             WebServer.actions.crew_member_stock(
                 info.get("scoreboardNumber", "3"), info.get("team"),
                 info.get("member"), info.get("change", 1)))

    # Set which crew member is currently in (0 = none).
    @app.route('/scoreboard<scoreboardNumber>-crew-active<team>-<member>')
    def crew_set_active(scoreboardNumber, team, member):
        return WebServer.actions.crew_set_active(scoreboardNumber, team, member)

    @socketio.on('crew_set_active')
    def ws_crew_set_active(message):
        info = orjson.loads(message)
        WebServer.ws_emit('crew_set_active',
             WebServer.actions.crew_set_active(
                 info.get("scoreboardNumber", "3"), info.get("team"),
                 info.get("member")))


    @app.route('/scoreboard<scoreboardNumber>-get')
    def get_route(scoreboardNumber):
        return WebServer.actions.get_scoreboard(scoreboardNumber)

    # Dynamic endpoint to allow flexible sets of information
    # Ex. http://192.168.1.2:5000/set?best-of=5
    #
    # Test Scenario that was used
    # Ex. http://192.168.4.34:5000/set?best-of=5&phase=Top 32&match=Winners Finals
    @app.route('/scoreboard<scoreboardNumber>-set')
    def set_route(scoreboardNumber):
        return WebServer.actions.set_route(
            scoreboardNumber,
            bestOf=request.args.get('best-of'),
            phase=request.args.get('phase'),
            match=request.args.get('match'),
            players=request.args.get('players'),
            characters=request.args.get('characters'),
            losers=request.args.get('losers'),
            team=request.args.get('team')
        )

    @socketio.on('set')
    def ws_set_route(message):
        parsed = orjson.loads(message)
        WebServer.ws_emit('set', WebServer.actions.set_route(
            parsed.get("scoreboardNumber", "1"),
            bestOf=parsed.get('best_of'),
            phase=parsed.get('phase'),
            match=parsed.get('match'),
            players=parsed.get('players'),
            characters=parsed.get('characters'),
            losers=parsed.get('losers'),
            team=parsed.get('team')
        ))

    # Set player data
    @app.post('/scoreboard<scoreboardNumber>-update-team-<team>-<player>')
    def set_team_data(scoreboardNumber, team, player):
        data = request.get_json()
        return WebServer.actions.set_team_data(scoreboardNumber, team, player, data)

    @socketio.on('update_team')
    def ws_set_team_data(message):
        data = orjson.loads(message)
        WebServer.ws_emit('update_team',
             WebServer.actions.set_team_data(
                 data.get("scoreboardNumber", "1"),
                 data.get("team"),
                 data.get("player"),
                 data
            ))

    @app.post('/update-commentary-<caster>')
    def set_commentary_data(caster):
        data = request.get_json()
        return WebServer.actions.set_commentary_data(caster, data)

    @socketio.on('update_commentary')
    def ws_set_commentary_data(message):
        data = orjson.loads(message)
        WebServer.ws_emit('update_commentary',
            WebServer.actions.set_commentary_data(
                data.get("commentator"),
                data
            ))
        
    # Set game
    @app.post('/update-game')
    def set_game():
        data = request.get_json()
        return WebServer.actions.set_game(data)
    
    @socketio.on('update_game')
    def ws_set_game_data(message):
        if type(message) in {str, bytes}:
            data = orjson.loads(message)
        else:
            data = message

        WebServer.ws_emit('update_game',
            WebServer.actions.set_game(
                data
            ))

    # Get games
    @app.route('/games')
    def get_games():
        return WebServer.actions.get_games()

    @socketio.on('games')
    def ws_get_games(message):
        WebServer.ws_emit('games', WebServer.actions.get_games(), json=True)

    # Get current game
    @app.route('/current-game')
    def get_current_game():
        return WebServer.actions.get_current_game()

    @socketio.on('current_game')
    def ws_get_current_game(message):
        WebServer.ws_emit('get_current_game', WebServer.actions.get_current_game(), json=True)

    # Get match and phase names
    @app.route('/match-names')
    def get_match_names():
        return WebServer.actions.get_match_names()

    @socketio.on('match_names')
    def ws_get_match_names(message):
        WebServer.ws_emit('match_names', WebServer.actions.get_match_names(), json=True)

    # Get characters
    @app.route('/characters')
    def get_characters():
        return WebServer.actions.get_characters()

    @socketio.on('characters')
    def ws_get_characters(message):
        WebServer.ws_emit('characters', WebServer.actions.get_characters(), json=True)

    # Get variants
    @app.route('/variants')
    def get_variants():
        return WebServer.actions.get_variants()

    @socketio.on('variants')
    def ws_get_variants(message):
        WebServer.ws_emit('variants', WebServer.actions.get_variants(), json=True)

    # Get controllers
    @app.route('/controllers')
    def get_controllers():
        return WebServer.actions.get_controllers()

    @socketio.on('controllers')
    def ws_get_controllers(message):
        WebServer.ws_emit('controllers', WebServer.actions.get_controllers(), json=True)

    # Swaps teams
    @app.route('/scoreboard<scoreboardNumber>-swap-teams')
    def swap_teams(scoreboardNumber):
        return WebServer.actions.swap_teams(scoreboardNumber)

    @socketio.on('swap_teams')
    def ws_swap_teams(message):
        info = orjson.loads(message)
        WebServer.ws_emit('swap_teams', WebServer.actions.swap_teams(
            info.get("scoreboardNumber", "1")))

    # Are the teams currently swapped?
    @app.route('/scoreboard<scoreboardNumber>-get-swap')
    def get_swap(scoreboardNumber):
        return WebServer.actions.get_swap(scoreboardNumber)

    @socketio.on('get_swap')
    def ws_get_swap(message):
        info = orjson.loads(message)
        WebServer.ws_emit('get_swap', WebServer.actions.get_swap(
            info.get("scoreboardNumber", "1")))

    # Opens Set Selector Window
    @app.route('/scoreboard<scoreboardNumber>-open-set')
    def open_sets(scoreboardNumber):
        return WebServer.actions.open_sets(scoreboardNumber)

    @socketio.on('open_set')
    def ws_open_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('open_set', WebServer.actions.open_sets(
            info.get("scoreboardNumber", "1")))

    # Pulls Current Stream Set
    @app.route('/scoreboard<scoreboardNumber>-pull-stream')
    def pull_stream_set(scoreboardNumber):
        return WebServer.actions.pull_stream_set(scoreboardNumber)

    @socketio.on('pull_stream')
    def ws_pull_stream_set(message):
        info = orjson.loads(message)
        WebServer.ws_emit('pull_stream', WebServer.actions.pull_stream_set(
            info.get("scoreboardNumber", "1")))

    # Resubmits Call for Recent Sets
    @app.route('/scoreboard<scoreboardNumber>-stats-recent-sets')
    def stats_recent_sets(scoreboardNumber):
        return WebServer.actions.stats_recent_sets(scoreboardNumber)

    @socketio.on('stats_recent_sets')
    def ws_stats_recent_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_recent_sets',
             WebServer.actions.stats_recent_sets(info.get("scoreboardNumber", "1"), info.get("player")))

    # Resubmits Call for Upset Factor
    @app.route('/scoreboard<scoreboardNumber>-stats-upset-factor')
    def stats_upset_factor(scoreboardNumber):
        return WebServer.actions.stats_upset_factor(scoreboardNumber)

    @socketio.on('stats_upset_factor')
    def ws_stats_upset_factor(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_upset_factor',
             WebServer.actions.stats_upset_factor(info.get("scoreboardNumber", "1"), info.get("player")))

    # Resubmits Call for Last Sets
    @app.route('/scoreboard<scoreboardNumber>-stats-last-sets-<player>')
    def stats_last_sets(scoreboardNumber, player):
        return WebServer.actions.stats_last_sets(scoreboardNumber, player)

    @socketio.on('stats_last_sets')
    def ws_stats_last_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_last_sets',
             WebServer.actions.stats_last_sets(info.get("scoreboardNumber", "1"), info.get("player")))

   # Resubmits Call for History Sets
    @app.route('/scoreboard<scoreboardNumber>-stats-history-sets-<player>')
    def stats_history_sets(scoreboardNumber, player):
        return WebServer.actions.stats_history_sets(scoreboardNumber, player)

    @socketio.on('stats_history_sets')
    def ws_stats_history_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_history_sets',
             WebServer.actions.stats_history_sets(info.get("scoreboardNumber", "1"), info.get("player")))

    # Resets scores
    @app.route('/scoreboard<scoreboardNumber>-reset-scores')
    def reset_scores(scoreboardNumber):
        return WebServer.actions.reset_scores(scoreboardNumber)

    @socketio.on('reset_scores')
    def ws_reset_scores(message):
        info = orjson.loads(message)
        WebServer.ws_emit('reset_scores',
             WebServer.actions.reset_scores(info.get("scoreboardNumber", "1")))

    # Resets scores, match, phase, and losers status
    @app.route('/scoreboard<scoreboardNumber>-reset-match')
    def reset_match(scoreboardNumber):
        return WebServer.actions.reset_match(scoreboardNumber)

    @socketio.on('reset_match')
    def ws_reset_match(message):
        info = orjson.loads(message)
        WebServer.ws_emit('reset_match',
             WebServer.actions.reset_match(info.get("scoreboardNumber", "1")))

    # Resets scores, match, phase, and losers status
    @app.route('/scoreboard<scoreboardNumber>-reset-players')
    def reset_players(scoreboardNumber):
        return WebServer.actions.reset_players(scoreboardNumber)

    @socketio.on('reset_players')
    def ws_reset_players(message):
        info = orjson.loads(message)
        WebServer.ws_emit('reset_players',
             WebServer.actions.reset_players(info.get("scoreboardNumber", "1")))

    # Resets all values
    @app.route('/scoreboard<scoreboardNumber>-clear-all')
    def clear_all(scoreboardNumber):
        return WebServer.actions.clear_all(scoreboardNumber)

    @socketio.on('clear_all')
    def ws_clear_all(message):
        info = orjson.loads(message)
        WebServer.ws_emit('clear_all',
             WebServer.actions.clear_all(info.get("scoreboardNumber", "1")))
        
    # Get thumbnail
    @app.route('/scoreboard<scoreboardNumber>-get-thumbnail-<fileFormat>')
    def get_thumbnail(scoreboardNumber, fileFormat):
        if fileFormat.lower() in ["png", "jpg"]:
            result = WebServer.actions.get_thumbnail(scoreboardNumber, fileFormat.lower())
            if result:
                return send_file(result, mimetype=f"image/{fileFormat.lower()}")
            else:
                return "An error has occured, please check Autona logs for more information"
        else:
            return f"File format {fileFormat} not recognized"

    # Get the sets to be played
    @app.route('/get-sets')
    def get_sets():
        return WebServer.actions.get_sets(request.args)

    @socketio.on('get_sets')
    def ws_get_sets(message):
        WebServer.ws_emit('get_sets', WebServer.actions.get_sets(orjson.loads(message)))
        
    # Loads info on a match
    @app.route('/get-match-<setId>')
    def get_match(setId):
        return WebServer.actions.get_match(setId)

    @socketio.on('get_match')
    def ws_get_match(message):
        info = orjson.loads(message)
        WebServer.ws_emit('get_match', WebServer.actions.get_match(info.get("setId")))

    # Get the commentators
    @app.route('/get-comms')
    def get_comms():
        return WebServer.actions.get_comms()

    @socketio.on('get_comms')
    def ws_get_comms():
        WebServer.ws_emit('get_comms', WebServer.actions.get_comms())

    # Loads a set remotely by providing a set ID to pull from the data provider
    @app.route('/scoreboard<scoreboardNumber>-load-set')
    def load_set(scoreboardNumber):
        if request.args.get('no-mains') is not None:
            return WebServer.actions.load_set(scoreboardNumber, request.args.get("set"), no_mains=True)
        else:
            return WebServer.actions.load_set(scoreboardNumber, request.args.get("set"))

    @socketio.on('load_set')
    def ws_load_set(message):
        info = orjson.loads(message)
        if info.get('no-mains') is not None:
            WebServer.ws_emit('load_set', WebServer.actions.load_set(
                info.get("scoreboardNumber", "1"), info.get("set"), no_mains=True))
        else:
            WebServer.ws_emit('load_set', WebServer.actions.load_set(
                info.get("scoreboardNumber", "1"), info.get("set")))

    # Loads a set remotely by providing a set ID to pull from the data provider
    @app.route('/scoreboard<scoreboardNumber>-get-set')
    def get_set(scoreboardNumber):
        return WebServer.actions.get_set(scoreboardNumber)

    @socketio.on('get_set')
    def ws_get_set(message):
        WebServer.ws_emit('get_set', WebServer.actions.get_set(
            orjson.loads(message).get('scoreboardNumber', '1')))
        
    @app.route('/playerdb')
    def playerdb():
        return WebServer.actions.get_playerdb()

    @socketio.on('playerdb')
    def ws_playerdb(message):
        logger.debug("Emitting playerdb info.")
        WebServer.ws_emit('playerdb', WebServer.actions.get_playerdb())


    # Update bracket
    @app.route('/update-bracket')
    def update_bracket():
        return WebServer.actions.update_bracket()

    @socketio.on('update_bracket')
    def ws_update_bracket(message):
        WebServer.ws_emit('update_bracket', WebServer.actions.update_bracket())

    # Load player from tag
    @app.route('/scoreboard<scoreboardNumber>-load-player-from-tag-<team>-<player>')
    def load_player_from_tag(scoreboardNumber, team, player):
        if request.args.get('tag') is None:
            return "No tag provided"
        no_mains = request.args.get('no-mains') is not None
        return WebServer.actions.load_player_from_tag(scoreboardNumber, html.unescape(request.args.get('tag')), team, player, no_mains)

    @socketio.on('load_player_from_tag')
    def ws_load_player_from_tag(message):
        args = orjson.loads(message)
        if args.get('tag') is None:
            WebServer.ws_emit('load_player_from_tag', 'No tag provided')
            return
        no_mains = args.get('no-mains') is not None
        team = args.get('team')
        player = args.get('player')
        scoreboardNumber = args.get('scoreboardNumber', '1')
        WebServer.ws_emit('load_player_from_tag', WebServer.actions.load_player_from_tag(
            scoreboardNumber, html.unescape(args.get('tag')), team, player, no_mains))

    @app.route('/load-commentator-from-tag-<caster>')
    def load_commentator_from_tag(caster):
        if request.args.get('tag') is None:
            return "No tag provided"
        no_mains = request.args.get('no-mains') is not None
        return WebServer.actions.load_commentator_from_tag(caster, html.unescape(request.args.get('tag')), no_mains)

    @socketio.on('load_commentator_from_tag')
    def ws_load_commentator_from_tag(message):
        args = orjson.loads(message)
        if args.get('tag') is None:
            WebServer.ws_emit('load_commentator_from_tag', 'No tag provided')
            return
        no_mains = args.get('no-mains') is not None
        caster = args.get('commentator')
        WebServer.ws_emit('load_commentator_from_tag', WebServer.actions.load_commentator_from_tag(caster, html.unescape(args.get('tag')), no_mains))

    # Update bracket
    @app.route('/set-tournament')
    def set_tournament():
        return WebServer.actions.load_tournament(request.args.get('url'))

    @socketio.on('set_tournament')
    def ws_set_tournament(message):
        WebServer.ws_emit('set_tournament', WebServer.actions.load_tournament(request.args.get('url')))

    @app.route('/states')
    def get_states():
        countryCode = request.args.get('countryCode', None)
        if not countryCode:
            abort(400, "countryCode not specified")

        return WebServer.actions.get_states(countryCode)


    @socketio.on('states')
    def ws_get_states(message):
        args = orjson.loads(message)
        return WebServer.actions.get_states(args.get('countryCode', ''))

    # ── Desktop Scoreboard Designer ────────────────────────────────
    # These endpoints are intentionally local-only and require the
    # short-lived token issued to AutonaDesignerWindow. The external Web
    # Scoreboard therefore cannot write files on the host machine.
    @staticmethod
    def _authorize_designer_request():
        token = request.headers.get("X-Autona-Designer-Token")
        if not AutonaDesignerService.is_authorized(token, request.remote_addr):
            abort(403, "Desktop Designer authorization required")

    @app.get('/api/designer/projects')
    def designer_list_projects():
        WebServer._authorize_designer_request()
        return jsonify({"projects": AutonaDesignerService.list_projects()})

    @app.get('/api/designer/projects/<slug>')
    def designer_load_project(slug):
        WebServer._authorize_designer_request()
        try:
            return jsonify(AutonaDesignerService.load_project(slug))
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DesignerProjectError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.delete('/api/designer/projects/<slug>')
    def designer_delete_project(slug):
        WebServer._authorize_designer_request()
        try:
            return jsonify(AutonaDesignerService.delete_project(slug))
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DesignerProjectError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logger.error(traceback.format_exc())
            return jsonify({"error": "デザインの削除に失敗しました。"}), 500

    @app.post('/api/designer/projects')
    def designer_save_project():
        WebServer._authorize_designer_request()
        if request.content_length and request.content_length > 70 * 1024 * 1024:
            return jsonify({"error": "保存データがサイズ上限を超えています。"}), 413
        try:
            payload = request.get_json(force=False, silent=False)
            return jsonify(AutonaDesignerService.save_project(payload))
        except DesignerProjectError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logger.error(traceback.format_exc())
            return jsonify({"error": "デザインの保存に失敗しました。"}), 500


    @app.route('/')
    @app.route('/scoreboard')
    @app.route('/team')
    @app.route('/web')
    @cross_origin()
    def web_index():
        return send_file(os.path.join(os.path.abspath('.'), 'web/build/index.html'))


    @app.route('/<path:filename>', methods=['GET', 'POST'])
    @cross_origin()
    def file_request(filename):
        try:
            filename = filename or 'web/build/index.html'
            mimetype = None
            if filename.endswith('.js'):
                mimetype = "text/javascript"
            if filename.lower().endswith('.png'):
                mimetype = "image/apng"

            return send_from_directory(
                os.path.abspath('.'),
                filename,
                as_attachment=filename.endswith('.gz'),
                mimetype=mimetype,
                max_age=86400
            )

        except Exception as e:
            logger.error(f"File not found: {e}")
            return "File not found", 404

    def run(self):
        try:
            self.socketio.run(app=self.app, host=self.host_name, port=self.port,
                              debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
        except Exception as e:
            logger.error(traceback.format_exc())


# ── Port collision guard ─────────────────────────────────────────
# On Windows, two processes can bind the same TCP port simultaneously
# (no SO_EXCLUSIVEADDRUSE on Werkzeug's socket). Stale `python main.py`
# runs then silently steal every other request, making the web UI
# appear blank. Detect this at startup and offer to kill the offenders.

def _netstat_listeners(port: int) -> set:
    if sys.platform != 'win32':
        return set()
    own_pid = os.getpid()
    pids = set()
    try:
        out = subprocess.check_output(
            ['netstat', '-ano', '-p', 'TCP'],
            text=True, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except Exception as e:
        logger.warning(f"netstat failed during port-guard: {e}")
        return set()
    suffix = f':{port}'
    for line in out.splitlines():
        if 'LISTENING' not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        if not parts[1].endswith(suffix):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid != own_pid:
            pids.add(pid)
    return pids


def _process_name(pid: int) -> str:
    try:
        # Image Name is at a fixed column in non-localized default output,
        # so pick the token before the PID to stay locale-neutral.
        out = subprocess.check_output(
            ['tasklist', '/FO', 'CSV', '/NH'],
            timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        for raw in out.splitlines():
            try:
                line = raw.decode('utf-8', errors='replace')
            except Exception:
                line = str(raw)
            fields = [f.strip().strip('"') for f in line.split(',')]
            if len(fields) >= 2:
                try:
                    if int(fields[1]) == pid:
                        return fields[0]
                except ValueError:
                    continue
    except Exception:
        pass
    return '(unknown)'


def check_webserver_port(port: int, parent=None) -> bool:
    """Return True if the port is free (or was freed by killing stale procs).

    Shows a QMessageBox when another process is already listening so the user
    can choose to terminate it. Safe to call from the GUI thread.
    """
    conflicts = _netstat_listeners(port)
    if not conflicts:
        return True

    details = ', '.join(f"{pid} ({_process_name(pid)})" for pid in sorted(conflicts))
    logger.warning(f"Port {port} already held by PIDs: {details}")

    text = (
        QApplication.translate(
            "portguard",
            "Port {port} is already in use by another process:\n\n{details}\n\n"
            "If this is a stale Autona instance, the Web UI will appear blank "
            "because Windows randomly routes connections to whichever process answers.\n\n"
            "Terminate the existing process(es) and continue?",
        )
        .replace("{port}", str(port))
        .replace("{details}", details)
    )
    title = QApplication.translate("portguard", "Port conflict")

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    if box.exec() != QMessageBox.StandardButton.Yes:
        return False

    for pid in conflicts:
        try:
            subprocess.run(
                ['taskkill', '/F', '/PID', str(pid)],
                timeout=5, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except Exception as e:
            logger.error(f"taskkill {pid} failed: {e}")

    # Wait up to ~2s for the OS to release the port.
    for _ in range(10):
        time.sleep(0.2)
        if not _netstat_listeners(port):
            return True

    QMessageBox.critical(
        parent,
        title,
        QApplication.translate(
            "portguard",
            "Failed to free port {port}. Please terminate the conflicting process manually.",
        ).replace("{port}", str(port)),
    )
    return False
