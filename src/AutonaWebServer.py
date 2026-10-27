import functools
import html
import json
import os
import socket
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

from .core.StateManager import StateManager
from .StateManagerBridge import StateManagerBridge
from . import AutonaWebOperations as WebOps
from .core.SettingsManager import SettingsManager, DEFAULT_WEBSERVER_PORT
from .core import WebAccess
from .Helpers.AutonaNetworkHelper import GetLocalIPs
from .AutonaDesignerService import (
    AutonaDesignerService,
    DesignerProjectError,
)
import traceback

import logging
log = logging.getLogger('socketio.server')
log.setLevel(logging.ERROR)
# werkzeug's per-request access log (OBS polls many times a second). Set
# here, beside the server it belongs to: it used to sit in
# AutonaWebServerActions, which the headless shell never imports (S3c).
logging.getLogger('werkzeug').setLevel(logging.ERROR)

class SocketioJson:
    def default(obj):
        if isinstance(obj, type(type(1))):
            return str(obj)
        return obj

    def dumps(*args, **kwargs):
        return json.dumps(*args, **kwargs, default=SocketioJson.default)

    def loads(*args, **kwargs):
        return json.loads(*args, **kwargs)


def _build_web_app():
    """Create the Flask app + SocketIO server and register every route/event.

    This used to run in the WebServer class body, i.e. as an import side
    effect: importing this module built a Flask app and registered ~90
    route/event handlers before main() had decided anything. Step 3e of the
    startup redesign moves it into a factory that WebServer.__init__ calls
    once (idempotently), so import stays cheap and observable.

    The handlers below are the former class-body definitions, unchanged apart
    from dropping @staticmethod: they now close over the local `app` /
    `socketio` instead of the class-body names of the same spelling, so their
    bodies -- and every URL, HTTP method and event name -- are identical.
    Three of them (ws_program_state / ws_get_characters / ws_playerdb) had to
    stay WebServer attributes because other code reaches them by name; they
    are registered here explicitly. tests/test_webserver_routes.py pins both
    the resulting route/event ledger and that class-attribute contract.

    Since ADR-005 S3c the handlers call the shared operations
    (src/AutonaWebOperations.py, `WebOps`) for everything that is core's, and
    the board-dependent rest through WebServer._board_actions(): the desktop
    shell's WebServerActions, or -- in the headless shell, which has none --
    NotAvailable, answered 503 here (the routes) and as the event's ack (the
    Socket.IO events).
    """
    # No Flask static folder (ADR-005 S3a): it served every file below the
    # working directory under /./, beside file_request. file_request is the
    # one file route now, and it serves only WebAccess.StaticPath()'s list.
    app = Flask(__name__, static_folder=None)
    CORS(app)
    socketio = SocketIO(
        app,
        # Any page may still connect and receive the state (OBS loads local
        # files from file:// and http://absolute). What a connection may do
        # beyond reading is decided per connection in ws_connect.
        cors_allowed_origins='*',
        # Uncomment to enable SocketIO logging (As logging is unuseful, we'll make this a dev flag)
        # logger=logger,
        async_mode='threading',
        json=SocketioJson
    )
    app.config['CORS_HEADERS'] = 'Content-Type'

    # Every Socket.IO handler below is registered through ws_on() rather
    # than socketio.on() (ADR-005 S3a): a WRITE / PLAYERDB event
    # (WebAccess.EVENT_ACCESS) is wrapped in WebServer._ws_guard, which
    # refuses it on a connection that was not let in to write. Read events
    # register unchanged. Every event also answers an operation this shell
    # cannot run with the 503 body as its ack (WebServer._ws_unavailable,
    # ADR-005 S3c). Returns the handler itself, like socketio.on().
    def ws_on(event):
        def register(handler):
            served = WebServer._ws_unavailable(event, handler)
            if WebAccess.EventAccess(event) in WebAccess.GUARDED:
                socketio.on(event)(WebServer._ws_guard(event, served))
            else:
                socketio.on(event)(served)
            return handler
        return register

    @app.before_request
    def log_request():
        # Debug level: OBS / Web UI poll dozens of endpoints per second.
        # At INFO this dominates the log file and stdout, drowning real signal.
        logger.debug(f"[FLASK] → {request.method} {request.path} from {request.remote_addr}")

    # Writes and the player DB need the token from another computer, and a
    # browser page that is not this server's own (ADR-005 S3a). Registered
    # after log_request, so a refused request is still logged as arriving.
    @app.before_request
    def guard_request():
        return WebServer._guard_http_request()

    @app.after_request
    def log_response(response):
        logger.debug(f"[FLASK] ← {request.method} {request.path} [{response.status_code}]")
        return response

    # A board-dependent operation in the headless shell (ADR-005 S3c): 503 in
    # the /api/v1 error shape, from every legacy route. /api/v1 answers it
    # itself (its wrappers turn every exception into an envelope first).
    @app.errorhandler(WebOps.NotAvailable)
    def not_available(error):
        response = jsonify(WebServer.UnavailableBody(error))
        response.status_code = 503
        return response

    # ------------------------------------------------------------------
    # DEPRECATED legacy routes (/program-state, /score, /scoreboard<N>-*,
    # /update-*, /get-*, /load-*, ... down to /states below).
    # The canonical external-control API is /api/v1/ (src/AutonaApiV1.py,
    # ADR-001). These legacy routes are kept unchanged for existing OBS /
    # Stream Deck setups, are frozen (no new routes here), and are scheduled
    # for removal in v2.0. Do not change their behavior.
    # /api/designer/* and the static-file routes further below are NOT part
    # of the deprecation.
    # ------------------------------------------------------------------
    @app.route('/program-state')
    def program_state():
        return WebOps.program_state()

    @ws_on('program-state-update')
    def ws_program_state_update(message):
        WebServer.ws_emit('program_state_update', {})

    # `message` is the client's `auth` payload here (flask_socketio passes
    # it to the connect handler). The connection is told what it may do
    # once, now: reading always; writing and the player DB only when
    # Authorize lets it in (ADR-005 S3a).
    @ws_on('connect')
    def ws_connect(message):
        WebServer._authorize_ws_connection(message)
        WebServer.ws_program_state(message)

    # `ws_program_state` cannot become a closure-local: it is also connected to
    # StateManagerBridge.instance.state_big_change and called as
    # WebServer.ws_program_state() from _flush_pending_deltas / ws_connect.
    # It stays on the class and is registered for the same event here.
    # Same for ws_get_characters / ws_playerdb further below.
    ws_on('program_state')(WebServer.ws_program_state)

    @socketio.on_error_default
    def ws_on_error(e):
        logger.error(traceback.format_exc())

    @app.route('/score', methods=['POST'])
    def post_score():
        return WebServer._board_actions().post_score(request.get_data())

    @ws_on('score')
    def ws_post_score(message):
        WebServer.ws_emit('score', WebServer._board_actions().post_score(message))

    # Ticks score of Team specified up by 1 point
    @app.route('/scoreboard<scoreboardNumber>-team<team>-scoreup')
    def team_scoreup(scoreboardNumber, team):
        return WebOps.team_scoreup(scoreboardNumber, team)

    @ws_on('team_scoreup')
    def ws_team_scoreup(message):
        info = orjson.loads(message)
        WebServer.ws_emit('team_scoreup',
             WebOps.team_scoreup(info.get("scoreboardNumber", "1"), info.get("team")))

    # Ticks score of Team specified down by 1 point
    @app.route('/scoreboard<scoreboardNumber>-team<team>-scoredown')
    def team_scoredown(scoreboardNumber, team):
        return WebOps.team_scoredown(scoreboardNumber, team)

    @ws_on('team_scoredown')
    def ws_team_scoredown(message):
        info = orjson.loads(message)
        WebServer.ws_emit('team_scoredown',
             WebOps.team_scoredown(info.get("scoreboardNumber", "1"), info.get("team")))

    # Set color of team
    @app.route('/scoreboard<scoreboardNumber>-team<team>-color-<color>')
    def team_color(scoreboardNumber, team, color):
        return WebOps.team_color(scoreboardNumber, team, "#" + color)

    @ws_on('team_color')
    def ws_team_color(message):
        info = orjson.loads(message)
        WebServer.ws_emit('team_scoredown',
             WebOps.team_color(info.get("scoreboardNumber", "1"), info.get("team"), "#" + info.get("color")))

    # ── Crewbattle (board 3) member controls ──
    # Bump a crew member's remaining stock up/down by 1.
    @app.route('/scoreboard<scoreboardNumber>-crew-member<team>-<member>-stockup')
    def crew_member_stockup(scoreboardNumber, team, member):
        return WebOps.crew_member_stock(scoreboardNumber, team, member, 1)

    @app.route('/scoreboard<scoreboardNumber>-crew-member<team>-<member>-stockdown')
    def crew_member_stockdown(scoreboardNumber, team, member):
        return WebOps.crew_member_stock(scoreboardNumber, team, member, -1)

    @ws_on('crew_member_stock')
    def ws_crew_member_stock(message):
        info = orjson.loads(message)
        WebServer.ws_emit('crew_member_stock',
             WebOps.crew_member_stock(
                 info.get("scoreboardNumber", "3"), info.get("team"),
                 info.get("member"), info.get("change", 1)))

    # Set which crew member is currently in (0 = none).
    @app.route('/scoreboard<scoreboardNumber>-crew-active<team>-<member>')
    def crew_set_active(scoreboardNumber, team, member):
        return WebOps.crew_set_active(scoreboardNumber, team, member)

    @ws_on('crew_set_active')
    def ws_crew_set_active(message):
        info = orjson.loads(message)
        WebServer.ws_emit('crew_set_active',
             WebOps.crew_set_active(
                 info.get("scoreboardNumber", "3"), info.get("team"),
                 info.get("member")))


    @app.route('/scoreboard<scoreboardNumber>-get')
    def get_route(scoreboardNumber):
        return WebOps.get_scoreboard(scoreboardNumber)

    # Dynamic endpoint to allow flexible sets of information
    # Ex. http://192.168.1.2:5107/set?best-of=5
    #
    # Test Scenario that was used
    # Ex. http://192.168.4.34:5107/set?best-of=5&phase=Top 32&match=Winners Finals
    @app.route('/scoreboard<scoreboardNumber>-set')
    def set_route(scoreboardNumber):
        return WebOps.set_route(
            scoreboardNumber,
            bestOf=request.args.get('best-of'),
            phase=request.args.get('phase'),
            pool=request.args.get('pool'),
            match=request.args.get('match'),
            players=request.args.get('players'),
            characters=request.args.get('characters'),
            losers=request.args.get('losers'),
            team=request.args.get('team'),
            teamName=request.args.get('teamName')
        )

    # Toggle a scoreboard field's visibility (Web UI eye menu). Body:
    # {"option": "show_twitter"-style key, "value": bool}. Mirrors the
    # desktop eye menu into SettingsManager + StateManager on every board.
    @app.post('/display-option')
    def post_display_option():
        return WebOps.set_display_option(
            orjson.loads(request.get_data()))

    @ws_on('set')
    def ws_set_route(message):
        parsed = orjson.loads(message)
        WebServer.ws_emit('set', WebOps.set_route(
            parsed.get("scoreboardNumber", "1"),
            bestOf=parsed.get('best_of'),
            phase=parsed.get('phase'),
            pool=parsed.get('pool'),
            match=parsed.get('match'),
            players=parsed.get('players'),
            characters=parsed.get('characters'),
            losers=parsed.get('losers'),
            team=parsed.get('team')
        ))

    # Set player data (core since ADR-005 S3e-4, so it works headless too)
    @app.post('/scoreboard<scoreboardNumber>-update-team-<team>-<player>')
    def set_team_data(scoreboardNumber, team, player):
        data = request.get_json()
        return WebOps.set_team_data(scoreboardNumber, team, player, data)

    @ws_on('update_team')
    def ws_set_team_data(message):
        data = orjson.loads(message)
        WebServer.ws_emit('update_team',
             WebOps.set_team_data(
                 data.get("scoreboardNumber", "1"),
                 data.get("team"),
                 data.get("player"),
                 data
            ))

    @app.post('/update-commentary-<caster>')
    def set_commentary_data(caster):
        data = request.get_json()
        return WebOps.set_commentary_data(caster, data)

    @ws_on('update_commentary')
    def ws_set_commentary_data(message):
        data = orjson.loads(message)
        WebServer.ws_emit('update_commentary',
            WebOps.set_commentary_data(
                data.get("commentator"),
                data
            ))
        
    # Set game
    @app.post('/update-game')
    def set_game():
        data = request.get_json()
        return WebOps.set_game(data)
    
    @ws_on('update_game')
    def ws_set_game_data(message):
        if type(message) in {str, bytes}:
            data = orjson.loads(message)
        else:
            data = message

        WebServer.ws_emit('update_game',
            WebOps.set_game(
                data
            ))

    # Get games
    @app.route('/games')
    def get_games():
        return WebOps.get_games()

    @ws_on('games')
    def ws_get_games(message):
        WebServer.ws_emit('games', WebOps.get_games(), json=True)

    # Get current game
    @app.route('/current-game')
    def get_current_game():
        return WebOps.get_current_game()

    @ws_on('current_game')
    def ws_get_current_game(message):
        WebServer.ws_emit('get_current_game', WebOps.get_current_game(), json=True)

    # Get match and phase names
    @app.route('/match-names')
    def get_match_names():
        return WebOps.get_match_names()

    @ws_on('match_names')
    def ws_get_match_names(message):
        WebServer.ws_emit('match_names', WebOps.get_match_names(), json=True)

    # Get characters
    @app.route('/characters')
    def get_characters():
        return WebOps.get_characters()

    # Stays on the class: ws_game_changed (which the shells connect to their
    # game-change signal, bootstrap.wire_web_server) calls it by name.
    ws_on('characters')(WebServer.ws_get_characters)

    # Get variants
    @app.route('/variants')
    def get_variants():
        return WebOps.get_variants()

    @ws_on('variants')
    def ws_get_variants(message):
        WebServer.ws_emit('variants', WebOps.get_variants(), json=True)

    # Swaps teams
    @app.route('/scoreboard<scoreboardNumber>-swap-teams')
    def swap_teams(scoreboardNumber):
        return WebOps.swap_teams(scoreboardNumber)

    @ws_on('swap_teams')
    def ws_swap_teams(message):
        info = orjson.loads(message)
        WebServer.ws_emit('swap_teams', WebOps.swap_teams(
            info.get("scoreboardNumber", "1")))

    # Are the teams currently swapped?
    @app.route('/scoreboard<scoreboardNumber>-get-swap')
    def get_swap(scoreboardNumber):
        return WebOps.get_swap(scoreboardNumber)

    @ws_on('get_swap')
    def ws_get_swap(message):
        info = orjson.loads(message)
        WebServer.ws_emit('get_swap', WebOps.get_swap(
            info.get("scoreboardNumber", "1")))

    # Opens Set Selector Window
    @app.route('/scoreboard<scoreboardNumber>-open-set')
    def open_sets(scoreboardNumber):
        return WebServer._board_actions().open_sets(scoreboardNumber)

    @ws_on('open_set')
    def ws_open_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('open_set', WebServer._board_actions().open_sets(
            info.get("scoreboardNumber", "1")))

    # Pulls Current Stream Set
    @app.route('/scoreboard<scoreboardNumber>-pull-stream')
    def pull_stream_set(scoreboardNumber):
        return WebServer._board_actions().pull_stream_set(scoreboardNumber)

    @ws_on('pull_stream')
    def ws_pull_stream_set(message):
        info = orjson.loads(message)
        WebServer.ws_emit('pull_stream', WebServer._board_actions().pull_stream_set(
            info.get("scoreboardNumber", "1")))

    # Resubmits Call for Recent Sets
    @app.route('/scoreboard<scoreboardNumber>-stats-recent-sets')
    def stats_recent_sets(scoreboardNumber):
        return WebOps.stats_recent_sets(scoreboardNumber)

    @ws_on('stats_recent_sets')
    def ws_stats_recent_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_recent_sets',
             WebOps.stats_recent_sets(info.get("scoreboardNumber", "1")))

    # Resubmits Call for Upset Factor
    @app.route('/scoreboard<scoreboardNumber>-stats-upset-factor')
    def stats_upset_factor(scoreboardNumber):
        return WebOps.stats_upset_factor(scoreboardNumber)

    @ws_on('stats_upset_factor')
    def ws_stats_upset_factor(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_upset_factor',
             WebOps.stats_upset_factor(info.get("scoreboardNumber", "1")))

    # Resubmits Call for Last Sets
    @app.route('/scoreboard<scoreboardNumber>-stats-last-sets-<player>')
    def stats_last_sets(scoreboardNumber, player):
        return WebOps.stats_last_sets(scoreboardNumber, player)

    @ws_on('stats_last_sets')
    def ws_stats_last_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_last_sets',
             WebOps.stats_last_sets(info.get("scoreboardNumber", "1"), info.get("player")))

   # Resubmits Call for History Sets
    @app.route('/scoreboard<scoreboardNumber>-stats-history-sets-<player>')
    def stats_history_sets(scoreboardNumber, player):
        return WebOps.stats_history_sets(scoreboardNumber, player)

    @ws_on('stats_history_sets')
    def ws_stats_history_sets(message):
        info = orjson.loads(message)
        WebServer.ws_emit('stats_history_sets',
             WebOps.stats_history_sets(info.get("scoreboardNumber", "1"), info.get("player")))

    # Resets scores
    @app.route('/scoreboard<scoreboardNumber>-reset-scores')
    def reset_scores(scoreboardNumber):
        return WebOps.reset_scores(scoreboardNumber)

    @ws_on('reset_scores')
    def ws_reset_scores(message):
        info = orjson.loads(message)
        WebServer.ws_emit('reset_scores',
             WebOps.reset_scores(info.get("scoreboardNumber", "1")))

    # Resets scores, match, phase, and losers status
    @app.route('/scoreboard<scoreboardNumber>-reset-match')
    def reset_match(scoreboardNumber):
        return WebOps.reset_match(scoreboardNumber)

    @ws_on('reset_match')
    def ws_reset_match(message):
        info = orjson.loads(message)
        WebServer.ws_emit('reset_match',
             WebOps.reset_match(info.get("scoreboardNumber", "1")))

    # Resets scores, match, phase, and losers status
    @app.route('/scoreboard<scoreboardNumber>-reset-players')
    def reset_players(scoreboardNumber):
        return WebOps.reset_players(scoreboardNumber)

    @ws_on('reset_players')
    def ws_reset_players(message):
        info = orjson.loads(message)
        WebServer.ws_emit('reset_players',
             WebOps.reset_players(info.get("scoreboardNumber", "1")))

    # Resets all values
    @app.route('/scoreboard<scoreboardNumber>-clear-all')
    def clear_all(scoreboardNumber):
        return WebOps.clear_all(scoreboardNumber)

    @ws_on('clear_all')
    def ws_clear_all(message):
        info = orjson.loads(message)
        WebServer.ws_emit('clear_all',
             WebOps.clear_all(info.get("scoreboardNumber", "1")))
        
    # Get thumbnail
    @app.route('/scoreboard<scoreboardNumber>-get-thumbnail-<fileFormat>')
    def get_thumbnail(scoreboardNumber, fileFormat):
        if fileFormat.lower() in ["png", "jpg"]:
            result = WebServer._board_actions().get_thumbnail(scoreboardNumber, fileFormat.lower())
            if result:
                return send_file(result, mimetype=f"image/{fileFormat.lower()}")
            else:
                return "An error has occured, please check Autona logs for more information"
        else:
            return f"File format {fileFormat} not recognized"

    # Get the sets to be played
    @app.route('/get-sets')
    def get_sets():
        return WebOps.get_sets(request.args)

    @ws_on('get_sets')
    def ws_get_sets(message):
        WebServer.ws_emit('get_sets', WebOps.get_sets(orjson.loads(message)))
        
    # Loads info on a match
    @app.route('/get-match-<setId>')
    def get_match(setId):
        return WebOps.get_match(setId)

    @ws_on('get_match')
    def ws_get_match(message):
        info = orjson.loads(message)
        WebServer.ws_emit('get_match', WebOps.get_match(info.get("setId")))

    # Get the commentators
    @app.route('/get-comms')
    def get_comms():
        return WebOps.get_comms()

    @ws_on('get_comms')
    def ws_get_comms():
        WebServer.ws_emit('get_comms', WebOps.get_comms())

    # Loads a set remotely by providing a set ID to pull from the data provider
    @app.route('/scoreboard<scoreboardNumber>-load-set')
    def load_set(scoreboardNumber):
        if request.args.get('no-mains') is not None:
            return WebServer._board_actions().load_set(scoreboardNumber, request.args.get("set"), no_mains=True)
        else:
            return WebServer._board_actions().load_set(scoreboardNumber, request.args.get("set"))

    @ws_on('load_set')
    def ws_load_set(message):
        info = orjson.loads(message)
        if info.get('no-mains') is not None:
            WebServer.ws_emit('load_set', WebServer._board_actions().load_set(
                info.get("scoreboardNumber", "1"), info.get("set"), no_mains=True))
        else:
            WebServer.ws_emit('load_set', WebServer._board_actions().load_set(
                info.get("scoreboardNumber", "1"), info.get("set")))

    # Loads a set remotely by providing a set ID to pull from the data provider
    @app.route('/scoreboard<scoreboardNumber>-get-set')
    def get_set(scoreboardNumber):
        return WebOps.get_set(scoreboardNumber)

    @ws_on('get_set')
    def ws_get_set(message):
        WebServer.ws_emit('get_set', WebOps.get_set(
            orjson.loads(message).get('scoreboardNumber', '1')))
        
    @app.route('/playerdb')
    def playerdb():
        return WebOps.get_playerdb()

    # Stays on the class: bootstrap.wire_web_server connects
    # AutonaPlayerDB.signals.db_updated to the instance's ws_playerdb.
    ws_on('playerdb')(WebServer.ws_playerdb)


    # Re-import the Top 8 from Start.gg (core since ADR-005 S3e-8, so it
    # works headless too). Answers "OK" whether the import started, one was
    # already running or no tournament is loaded -- as it always did.
    @app.route('/update-bracket')
    def update_bracket():
        WebOps.update_bracket()
        return "OK"

    @ws_on('update_bracket')
    def ws_update_bracket(message):
        WebOps.update_bracket()
        WebServer.ws_emit('update_bracket', "OK")

    # Load player from tag (core since ADR-005 S3e-4, so it works headless)
    @app.route('/scoreboard<scoreboardNumber>-load-player-from-tag-<team>-<player>')
    def load_player_from_tag(scoreboardNumber, team, player):
        if request.args.get('tag') is None:
            return "No tag provided"
        no_mains = request.args.get('no-mains') is not None
        return WebOps.load_player_from_tag(scoreboardNumber, html.unescape(request.args.get('tag')), team, player, no_mains)

    @ws_on('load_player_from_tag')
    def ws_load_player_from_tag(message):
        args = orjson.loads(message)
        if args.get('tag') is None:
            WebServer.ws_emit('load_player_from_tag', 'No tag provided')
            return
        no_mains = args.get('no-mains') is not None
        team = args.get('team')
        player = args.get('player')
        scoreboardNumber = args.get('scoreboardNumber', '1')
        WebServer.ws_emit('load_player_from_tag', WebOps.load_player_from_tag(
            scoreboardNumber, html.unescape(args.get('tag')), team, player, no_mains))

    @app.route('/load-commentator-from-tag-<caster>')
    def load_commentator_from_tag(caster):
        if request.args.get('tag') is None:
            return "No tag provided"
        no_mains = request.args.get('no-mains') is not None
        return WebOps.load_commentator_from_tag(caster, html.unescape(request.args.get('tag')), no_mains)

    @ws_on('load_commentator_from_tag')
    def ws_load_commentator_from_tag(message):
        args = orjson.loads(message)
        if args.get('tag') is None:
            WebServer.ws_emit('load_commentator_from_tag', 'No tag provided')
            return
        no_mains = args.get('no-mains') is not None
        caster = args.get('commentator')
        WebServer.ws_emit('load_commentator_from_tag', WebOps.load_commentator_from_tag(caster, html.unescape(args.get('tag')), no_mains))

    # Update bracket
    @app.route('/set-tournament')
    def set_tournament():
        return WebOps.load_tournament(request.args.get('url'))

    @ws_on('set_tournament')
    def ws_set_tournament(message):
        WebServer.ws_emit('set_tournament', WebOps.load_tournament(request.args.get('url')))

    @app.route('/states')
    def get_states():
        countryCode = request.args.get('countryCode', None)
        if not countryCode:
            abort(400, "countryCode not specified")

        return WebOps.get_states(countryCode)


    @ws_on('states')
    def ws_get_states(message):
        args = orjson.loads(message)
        return WebOps.get_states(args.get('countryCode', ''))

    # ── Desktop Scoreboard Designer ────────────────────────────────
    # These endpoints are intentionally local-only and require the
    # short-lived token issued to AutonaDesignerWindow. The external Web
    # Scoreboard therefore cannot write files on the host machine.
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
        if request.content_length and request.content_length > 250 * 1024 * 1024:
            return jsonify({"error": "保存データがサイズ上限を超えています。"}), 413
        try:
            payload = request.get_json(force=False, silent=False)
            result = AutonaDesignerService.save_project(payload)
            WebServer.socketio.emit("designer_project_saved", result)
            return jsonify(result)
        except DesignerProjectError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logger.error(traceback.format_exc())
            return jsonify({"error": "デザインの保存に失敗しました。"}), 500

    # The Figma plugin's pairing code (AutonaDesignerService.
    # check_import_pairing). Shown and replaced only through the Designer
    # window's token, and never cached: it is a secret.
    @app.get('/api/designer/pairing')
    def designer_pairing_code():
        WebServer._authorize_designer_request()
        response = jsonify({"code": AutonaDesignerService.pairing_code()})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post('/api/designer/pairing/regenerate')
    def designer_regenerate_pairing_code():
        WebServer._authorize_designer_request()
        response = jsonify(
            {"code": AutonaDesignerService.regenerate_pairing_code()})
        response.headers["Cache-Control"] = "no-store"
        return response

    # The Figma plugin posts here from its own origin, so unlike the token
    # routes above this one answers CORS -- but only to the origins
    # AutonaDesignerService.is_import_origin_allowed() admits, and its
    # preflight admits the pairing-code header. The route's own
    # cross_origin() decides every response it returns (the app-wide
    # CORS(app) skips one it has evaluated), and it handles the preflight
    # itself so a refused page gets a 403 there too. Refusals are returned,
    # never abort()ed: an aborted request would bypass cross_origin() and
    # get CORS(app)'s allow-any-origin headers -- and the plugin could not
    # read a 401/429 without this route's CORS headers.
    @app.route('/api/designer/imports', methods=['POST', 'OPTIONS'])
    @cross_origin(
        origins=sorted(AutonaDesignerService.IMPORT_ORIGINS),
        methods=['POST'],
        allow_headers=['Content-Type', AutonaDesignerService.PAIRING_HEADER],
        automatic_options=False,
        always_send=False)
    def designer_receive_import():
        refusal = WebServer._authorize_designer_import_request()
        if refusal is not None:
            return refusal
        if request.method == 'OPTIONS':
            return app.make_default_options_response()
        if request.content_length and request.content_length > 70 * 1024 * 1024:
            return jsonify({"error": "インポートデータがサイズ上限を超えています。"}), 413
        try:
            payload = request.get_json(force=False, silent=False)
            result = AutonaDesignerService.receive_import(payload)
            WebServer.socketio.emit("designer_import_received", result)
            return jsonify(result)
        except DesignerProjectError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logger.error(traceback.format_exc())
            return jsonify({"error": "Figmaインポートの受信に失敗しました。"}), 500

    @app.get('/api/designer/imports/<import_id>')
    def designer_get_import(import_id):
        WebServer._authorize_designer_request()
        try:
            return jsonify(AutonaDesignerService.get_import(import_id))
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DesignerProjectError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get('/api/designer/imports/latest')
    def designer_latest_import():
        WebServer._authorize_designer_request()
        try:
            return jsonify(AutonaDesignerService.latest_import())
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except DesignerProjectError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logger.error(traceback.format_exc())
            return jsonify({"error": "Figmaインポートを読み込めませんでした。"}), 500


    @app.route('/')
    @app.route('/scoreboard')
    @app.route('/team')
    @app.route('/web')
    @cross_origin()
    def web_index():
        return send_file(os.path.join(os.path.abspath('.'), 'web/build/index.html'))


    # GET only (ADR-005 S3a). The route also took POST, inherited from the
    # upstream TSH fork's first commit: nothing here posts to a file, and a
    # POST only read the file back -- or answered 404 instead of 405 to a
    # POST aimed at a route that has no POST.
    @app.route('/<path:filename>')
    @cross_origin()
    def file_request(filename):
        # Only the overlays', the Web UI's and the state's files: settings,
        # the player DB and the rest of the working directory are not
        # served. A refusal answers like a missing file.
        served = WebAccess.StaticPath(filename)
        if served is None:
            WebServer._log_static_refusal(filename)
            return "File not found", 404
        filename = served
        try:
            mimetype = None
            if filename.endswith('.js'):
                mimetype = "text/javascript"
            if filename.lower().endswith('.png'):
                mimetype = "image/png"

            # Case-insensitive: the Windows filesystem serves
            # /Layout/Custom/... too, and it must not be cached either.
            is_designer_file = filename.lower().startswith(
                ("layout/custom/", "layout/designer_runtime/"))
            response = send_from_directory(
                os.path.abspath('.'),
                filename,
                as_attachment=filename.endswith('.gz'),
                mimetype=mimetype,
                max_age=0 if is_designer_file else 86400
            )
            if is_designer_file:
                # Designer saves overwrite project.json and PNG assets in
                # place, and an app update replaces the shared runtime every
                # design loads. OBS must revalidate all of them.
                response.cache_control.no_store = True
                response.cache_control.max_age = 0
            return response

        except Exception as e:
            logger.error(f"File not found: {e}")
            return "File not found", 404

    return app, socketio


class WebServer:
    """The Flask/SocketIO server, served from a plain Python thread.

    It used to be a QThread running socketio.run(), which could not be
    stopped -- the window hard-killed its accept loop with
    QThread.terminate() on close (TerminateThread, which can catch the
    thread holding the GIL or a CRT / interpreter lock and hang the exit;
    requests themselves ran on werkzeug's daemon request threads). It is
    shell-agnostic now (ADR-005 S3c): start() serves werkzeug's threaded server -- the one
    socketio.run() builds in threading mode -- from a threading.Thread, stop()
    shuts it down cleanly, and a start failure is reported to the
    `on_start_failed(error: str)` callback, called on the server thread (the
    shell marshals it where it needs to: the desktop to its GUI thread for a
    dialog, the headless shell only logs).
    """

    # Built by _build_web_app() on the first instantiation (Step 3e). Class
    # level access -- WebServer.app / WebServer.socketio -- keeps working for
    # every existing caller once __init__ has run.
    app = None
    socketio = None
    # The board-dependent operations (WebServerActions), handed in by the
    # desktop shell; None in the headless shell (see _board_actions).
    actions = None

    def __init__(self, on_start_failed=None, actions=None) -> None:
        self._on_start_failed = on_start_failed
        self._serve_lock = threading.Lock()
        self._server = None
        self._thread = None
        self._stopping = False

        # Idempotent, same shape as the existing singleton guards: the Flask
        # app and its route/event handlers are registered exactly once, no
        # matter how many WebServer instances get built. Must run before the
        # blueprint registration below, which needs WebServer.app.
        if WebServer.app is None:
            WebServer.app, WebServer.socketio = _build_web_app()

        # The desktop window builds its WebServerActions (bound to itself,
        # for the lazily-built panels, and to the scoreboard dock) and hands
        # it in; the headless shell has no widget to answer them and passes
        # nothing (ADR-005 S3c). The server itself no longer knows the dock.
        WebServer.actions = actions

        # Through the bridge rather than subscribing to the core channels
        # directly. Both handlers are plain functions, and PySide6 still
        # routes those through Qt's connection machinery (a worker-thread
        # write is queued onto the GUI thread, same as for a bound method),
        # so going via the bridge is what keeps the delivery these two had
        # before the state layer moved to core. Subscribing straight to the
        # channels would run them inline on the writer's thread instead --
        # a real change, deferred to a later step.
        StateManagerBridge.instance.state_updated.connect(
            WebServer.on_program_state_update)
        StateManagerBridge.instance.state_big_change.connect(
            WebServer.ws_program_state)

        # External Control API (/api/v1/). Added as a thin Blueprint that
        # reuses the same operations (AutonaWebOperations) and board actions;
        # existing routes/events are untouched (ADR-001 D3 /
        # .claude/design/external-control-api.md §9). Imported lazily here to
        # keep singleton init order intact (agent-pitfalls #2).
        from .AutonaApiV1 import api_v1, init_api_v1
        if "api_v1" not in WebServer.app.blueprints:
            WebServer.app.register_blueprint(api_v1)
        init_api_v1(WebServer.actions)

        # Issue the access token on the first launch (saved to settings.json),
        # so API Settings and "ブラウザ版を開く" have it before anyone asks
        # (ADR-005 S3a). Later launches just read it.
        WebAccess.Token()

        # LAN exposure is opt-in (ADR-001 D5 revisit): default to loopback so
        # a fresh install never exposes the control surface to the LAN.
        self.listen_lan = SettingsManager.Get("general.webserver_listen_lan", False)
        self.host_name = "0.0.0.0" if self.listen_lan else "127.0.0.1"
        self.port = SettingsManager.Get("general.webserver_port", DEFAULT_WEBSERVER_PORT)

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
    # First revision in the batch. StateManager bumps the revision on every
    # state_updated emit, so a coalesced payload spans the contiguous range
    # [first_revision, revision]. Clients validate continuity against that
    # range; without it every coalesced batch looked like a revision gap and
    # triggered a spurious full-snapshot refetch (Design.md §3.2.3 R-Sync-8).
    _delta_pending_first_revision = -1
    _delta_timer = None          # threading.Timer awaiting flush
    _delta_last_flush_ts = 0.0   # time.monotonic() of the last flush

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
                'first_revision': WebServer._delta_pending_first_revision,
                'delta': WebServer._delta_pending,
            }
            WebServer._delta_pending = []
            WebServer._delta_pending_revision = -1
            WebServer._delta_pending_first_revision = -1
            WebServer._delta_timer = None
            WebServer._delta_last_flush_ts = time.monotonic()
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
            was_empty = not WebServer._delta_pending
            WebServer._delta_pending.extend(delta)
            if was_empty:
                WebServer._delta_pending_first_revision = rev
            if rev > WebServer._delta_pending_revision:
                WebServer._delta_pending_revision = rev

            if WebServer._delta_timer is None:
                # Leading-edge flush: a delta arriving after a quiet period
                # (>= 50ms since the last flush) goes out immediately, so a
                # single operation (score +1 etc.) is not held back by the
                # debounce. Bursts keep the adaptive trailing debounce and
                # the large-burst path (100+ -> 200ms, R-Sync-10) unchanged.
                quiet = (time.monotonic()
                         - WebServer._delta_last_flush_ts) >= 0.05
                if was_empty and quiet:
                    interval = 0.0
                else:
                    interval = WebServer._adaptive_debounce_sec(
                        len(WebServer._delta_pending))
                WebServer._delta_timer = threading.Timer(
                    interval, WebServer._flush_pending_deltas)
                WebServer._delta_timer.daemon = True
                WebServer._delta_timer.start()

    # ── SocketIO handlers that are also referenced by name ───────────
    # These three are registered on the socketio server by _build_web_app(),
    # but they cannot live there as closures: each is additionally reached
    # through a name -- WebServer.ws_program_state() internally and from the
    # bridge, ws_get_characters from ws_game_changed below, and the
    # instance's ws_playerdb, which bootstrap.wire_web_server connects to
    # AutonaPlayerDB.signals.db_updated. Keeping them as plain class-body
    # functions preserves that access, including the bound call with the
    # instance standing in for `message`.
    def ws_program_state(message=None):
        WebServer.ws_emit('program_state', WebOps.program_state())

    def ws_get_characters(message):
        WebServer.ws_emit('characters', WebOps.get_characters(), json=True)

    def ws_game_changed(message=None):
        """A game was loaded: every client gets the full state and the new
        character list, in that order. What the shells connect their
        game-change signal to (bootstrap.wire_web_server): the window's
        GameChanged on the desktop, the headless loader's (ADR-005 S3c)."""
        WebServer.ws_program_state()
        WebServer.ws_get_characters(None)

    def ws_playerdb(message):
        logger.debug("Emitting playerdb info.")
        data = WebOps.get_playerdb()
        if flask.has_request_context():
            # Asked for over Socket.IO: answered to the asker alone, whom
            # _ws_guard has already let through.
            WebServer.ws_emit('playerdb', data)
        else:
            # The DB changed (AutonaPlayerDB.signals.db_updated): sent only
            # to the connections let in to read it, not to every client
            # (OBS, a read-only LAN page) as before (ADR-005 S3a).
            WebServer.socketio.emit(
                'playerdb', data, to=WebServer._ws_trusted_rooms())

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

    # ── Board-dependent operations (ADR-005 S3c) ─────────────────────

    @staticmethod
    def _board_actions():
        """The board-dependent operations (WebServerActions), or
        NotAvailable when this shell has no board / panel widget to run
        them (headless) -- a 503 from the routes, the ack of an event."""
        actions = WebServer.actions
        if actions is None:
            raise WebOps.NotAvailable()
        return actions

    @staticmethod
    def UnavailableBody(error=None):
        """The 503 body, in the /api/v1 error shape (WebAccess.ErrorBody)."""
        message = getattr(error, "message", None) or WebOps.NOT_AVAILABLE_MESSAGE
        return {"ok": False,
                "error": {"code": "UNAVAILABLE", "message": message}}

    @staticmethod
    def _ws_unavailable(event, handler):
        """``handler`` answering NotAvailable with the 503 body as the
        event's ack (ws_on) -- python-socketio sends a handler's return
        value to the client's callback, as for _ws_guard's refusals."""
        @functools.wraps(handler)
        def served(*args):
            try:
                return handler(*args)
            except WebOps.NotAvailable as error:
                logger.debug(
                    f"webserver: Socket.IO event {event!r} is not available "
                    "in this shell")
                return WebServer.UnavailableBody(error)
        return served

    # ── Access guard (ADR-005 S3a) ───────────────────────────────────
    # The policy is src/core/WebAccess.py; these only feed it the request.
    # Kept on the class next to the Designer guards below.

    # Where a Socket.IO connection's verdict is kept: in its environ, which
    # python-socketio keeps for the connection's lifetime and hands back as
    # flask.request.environ for each of its events -- so it goes away with
    # the connection, no disconnect handler needed.
    WS_GRANT_KEY = "autona.ws_grant"
    WS_REFUSAL_KEY = "autona.ws_refusal"
    # The rooms the player DB is broadcast to: connections let in without a
    # token (loopback, this server's own page) and those let in with the
    # current token. A regenerated token starts a new room, so connections
    # that proved the old one stop receiving it.
    WS_AMBIENT_ROOM = "autona:trusted"

    _refusals = WebAccess.RefusalLog()
    # This PC's LAN addresses: a request from one of them is from this PC,
    # and a page under one of them is this server's own (OwnOrigins). Looked
    # up on first use and kept (an address that appears later is not one).
    _lan_hosts = None

    @staticmethod
    def _ws_token_room(generation):
        return f"autona:token:{generation}"

    @staticmethod
    def _ws_trusted_rooms():
        return [WebServer.WS_AMBIENT_ROOM,
                WebServer._ws_token_room(WebAccess.TokenGeneration())]

    @staticmethod
    def _local_addresses():
        if WebServer._lan_hosts is None:
            WebServer._lan_hosts = GetLocalIPs()
        return WebServer._lan_hosts

    @staticmethod
    def _authorize(auth=None):
        """WebAccess.Authorize() for the current request / connection."""
        localAddresses = WebServer._local_addresses()
        # SERVER_PORT is the port the server listens on (not the client's
        # Host header, which is checked against it): what this server's own
        # pages have as their origin.
        return WebAccess.Authorize(
            remoteAddr=request.remote_addr,
            tokens=WebAccess.SuppliedTokens(request.headers, request.args, auth),
            origin=request.headers.get("Origin"),
            fetchSite=request.headers.get("Sec-Fetch-Site"),
            host=request.headers.get("Host"),
            ownOrigins=WebAccess.OwnOrigins(
                request.environ.get("SERVER_PORT"), localAddresses),
            localAddresses=localAddresses,
            forwarded=WebAccess.IsForwarded(request.headers),
        )

    @staticmethod
    def _log_refusal(key, what, refusal):
        # The path only, never the query string: it may hold the token.
        message = (f"webserver: refused {what} from {request.remote_addr}: "
                   f"{refusal.reason}")
        if WebServer._refusals.First(key):
            logger.warning(message)
        else:
            logger.debug(message)

    # Asked for by browsers and tools on their own, not by a layout: a
    # refusal of these is not worth a warning.
    _QUIET_STATIC = ("favicon.ico", ".well-known/")

    @staticmethod
    def _log_static_refusal(filename):
        message = (f"webserver: not serving {filename!r} to "
                   f"{request.remote_addr} (outside the served folders)")
        folded = filename.replace("\\", "/").lstrip("/").lower()
        if folded.startswith(WebServer._QUIET_STATIC):
            logger.debug(message)
        elif WebServer._refusals.First(("static", filename.lower())):
            logger.warning(message)
        else:
            logger.debug(message)

    @staticmethod
    def _guard_http_request():
        """None to let the request through, else the refusal to send.

        Only WRITE / PLAYERDB endpoints are checked. A preflight (OPTIONS)
        passes: it carries no token and runs no handler. An unmatched URL
        (endpoint None) is left to Flask's 404 / 405.
        """
        if request.method == "OPTIONS" or request.endpoint is None:
            return None
        if WebAccess.RouteAccess(request.endpoint) not in WebAccess.GUARDED:
            return None
        _grant, refusal = WebServer._authorize()
        if refusal is None:
            return None
        WebServer._log_refusal(
            (request.endpoint, request.remote_addr, refusal.reason),
            f"{request.method} {request.path}", refusal)
        response = jsonify(WebAccess.ErrorBody(refusal))
        response.status_code = refusal.status
        if refusal.status == 401:
            response.headers["WWW-Authenticate"] = 'Bearer realm="Autona"'
        return response

    @staticmethod
    def _authorize_ws_connection(auth):
        """Record what a new Socket.IO connection may do (see ws_connect).

        Every connection is accepted and receives the state. One that
        Authorize lets in also gets the guarded events and the player DB
        broadcast; any other is read-only, and _ws_guard refuses its writes.
        """
        # Read before authorizing: if the token is regenerated meanwhile, the
        # grant carries the old generation and fails closed. A connection that
        # qualifies without the token gets the ambient grant even when it
        # also sent the token (Authorize), so regenerating does not cut off
        # this PC's own pages -- only the token was keeping the others in.
        generation = WebAccess.TokenGeneration()
        grant, refusal = WebServer._authorize(
            auth if isinstance(auth, dict) else None)
        environ = request.environ
        if grant == WebAccess.GRANT_TOKEN:
            environ[WebServer.WS_GRANT_KEY] = (grant, generation)
            flask_socketio.join_room(WebServer._ws_token_room(generation))
        elif grant == WebAccess.GRANT_AMBIENT:
            environ[WebServer.WS_GRANT_KEY] = (grant, None)
            flask_socketio.join_room(WebServer.WS_AMBIENT_ROOM)
        else:
            # Normal for OBS on another PC and for file:// overlays, which
            # only read; their writes are what gets logged (_ws_guard).
            environ[WebServer.WS_GRANT_KEY] = None
            environ[WebServer.WS_REFUSAL_KEY] = refusal
            logger.debug(
                f"webserver: Socket.IO connection from {request.remote_addr} "
                f"is read-only ({refusal.reason})")

    @staticmethod
    def _ws_guard(event, handler):
        """``handler`` behind the connection's verdict (ws_on)."""
        @functools.wraps(handler)
        def guarded(*args):
            grant = request.environ.get(WebServer.WS_GRANT_KEY)
            if WebAccess.GrantStillValid(grant):
                return handler(*args)
            refusal = (request.environ.get(WebServer.WS_REFUSAL_KEY)
                       or WebAccess.TOKEN_REPLACED)
            WebServer._log_refusal(
                ("ws", event, request.remote_addr, refusal.reason),
                f"Socket.IO event {event!r}", refusal)
            # Returned as the event's ack (python-socketio sends a handler's
            # return value to the client's callback), in the v1 error shape.
            return WebAccess.ErrorBody(refusal)
        return guarded

    # Called by the /api/designer/* routes in _build_web_app(); kept on the
    # class so the guard has one obvious home next to the rest of the state.
    @staticmethod
    def _authorize_designer_request():
        token = request.headers.get("X-Autona-Designer-Token")
        if not AutonaDesignerService.is_authorized(token, request.remote_addr):
            abort(403, "Desktop Designer authorization required")

    @staticmethod
    def _authorize_designer_import_request():
        """None if the Figma import may go ahead, else the refusal to return.

        403: not this computer, or not a page the plugin runs in. 401: no
        or a wrong pairing code. 429: locked after repeated wrong codes.
        A preflight carries no code, so it stops after the Origin check.
        Returned, not raised: see the comment on the import route.
        """
        if not AutonaDesignerService.is_loopback(request.remote_addr):
            return jsonify({
                "error": "Figma import is only allowed from this computer"
            }), 403
        origin = request.headers.get("Origin")
        if not AutonaDesignerService.is_import_origin_allowed(origin):
            logger.warning(
                "designer: refused a Figma import from origin {!r}", origin)
            return jsonify({
                "error": "Figma import is not allowed from this page"
            }), 403
        if request.method == "OPTIONS":
            return None
        status, retry_after = AutonaDesignerService.check_import_pairing(
            request.headers.get(AutonaDesignerService.PAIRING_HEADER))
        if status == "locked":
            return jsonify({
                "error": "ペアリングコードの誤りが続いたため、"
                         "Figmaインポートを一時的に停止しています。",
                "reason": "pairing_locked",
                "retryAfter": retry_after,
            }), 429, {"Retry-After": str(retry_after)}
        if status == "missing":
            return jsonify({
                "error": "ペアリングコードがありません。",
                "reason": "pairing_required",
            }), 401
        if status != "ok":
            return jsonify({
                "error": "ペアリングコードが正しくありません。",
                "reason": "pairing_invalid",
            }), 401
        return None

    # ── Serving thread ───────────────────────────────────────────

    def start(self):
        """Start serving on a daemon thread. Returns immediately.

        Daemon, so a server that is never stopped (a crash, a test) cannot
        keep the process alive; the shells stop it on the way out anyway.
        A second call, or a call after stop(), does nothing.
        """
        with self._serve_lock:
            if self._thread is not None or self._stopping:
                return
            self._thread = threading.Thread(
                target=self._Serve, name="AutonaWebServer", daemon=True)
            self._thread.start()

    def _Serve(self):
        # What socketio.run() does in threading mode, minus what only a
        # command-line dev server wants (the banner, .env loading, the
        # reloader's WERKZEUG_SERVER_FD): Flask.run() -> werkzeug's
        # run_simple() -> make_server(threaded=True).serve_forever(). The
        # Socket.IO middleware is already wrapped around app.wsgi_app, and
        # simple-websocket upgrades the WebSocket transport on this server,
        # so the wire behaviour is the same. Building the server ourselves is
        # what makes it stoppable: serve_forever() returns on shutdown().
        from werkzeug.serving import make_server
        try:
            try:
                server = make_server(
                    self.host_name, self.port, self.app, threaded=True)
            except SystemExit as exit_:
                # werkzeug reports a bind failure by printing the OSError to
                # stderr and calling sys.exit(1) -- from this thread that
                # never reached the old `except Exception`, so the failure
                # was silent. The OSError is the exit's context.
                reason = exit_.__context__ or exit_
                raise OSError(
                    f"could not listen on {self.host_name}:{self.port}: "
                    f"{reason}") from None
            with self._serve_lock:
                if self._stopping:
                    server.server_close()
                    return
                self._server = server
            logger.info(
                f"Web server listening on {self.host_name}:{self.port}")
            server.serve_forever()
        except Exception as e:
            logger.error(traceback.format_exc())
            with self._serve_lock:
                stopping = self._stopping
            if stopping or self._on_start_failed is None:
                return
            try:
                self._on_start_failed(str(e))
            except Exception:
                logger.opt(exception=True).error(
                    "web server start-failure callback raised")

    def stop(self, timeout: float = 10.0) -> bool:
        """Stop serving and wait up to `timeout` seconds for the thread.

        Returns True when the server thread is gone (or never started).
        Connections already being handled run on werkzeug's daemon request
        threads and are not drained -- the server is local, and the process
        is on its way out (the old terminate() did not drain them either: it
        only killed the accept loop).
        """
        with self._serve_lock:
            self._stopping = True
            server = self._server
            thread = self._thread
        deadline = time.monotonic() + timeout
        if server is not None:
            # shutdown() blocks until serve_forever() has returned; run it
            # on a helper so a wedged loop cannot hold the caller (the
            # window's close) beyond the timeout.
            stopper = threading.Thread(
                target=server.shutdown, name="AutonaWebServerStop",
                daemon=True)
            stopper.start()
            stopper.join(timeout)
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        if server is not None:
            try:
                server.server_close()
            except Exception:
                logger.opt(exception=True).warning(
                    "error closing the web server socket")
        stopped = thread is None or not thread.is_alive()
        if not stopped:
            logger.warning(f"web server thread still alive after {timeout}s")
        return stopped


# ── Port collision guard ─────────────────────────────────────────
# On Windows, two processes can bind the same TCP port simultaneously
# (no SO_EXCLUSIVEADDRUSE on Werkzeug's socket) and the OS then splits the
# incoming connections between them, so every other request reaches a
# process that is not serving the live state and the web UI appears blank.
# Self-collisions (a stale `python main.py` beside the exe) are prevented
# upstream now by the single-instance mutex in main.py (startup Step 1), so
# what is left to detect here is a *foreign* listener on our port. The check
# runs from Window._StartWebServer(), i.e. after the window is on screen
# (startup Step 4c) — not during startup as it originally did. The headless
# shell runs the same probe and only logs it (log_webserver_port).

def _port_is_free(port: int, host: str) -> bool:
    """True when we can bind `host:port` ourselves right now.

    This is the fast path of the guard below: one syscall pair instead of a
    ~100-300 ms synchronous `netstat` subprocess on every single launch.

    SO_REUSEADDR is deliberately NOT set — on Windows it lets a bind succeed
    while another process is already listening, i.e. it would report exactly
    the collision we are here to detect as "free".

    `host` must match the interface the server will actually listen on
    (0.0.0.0 vs 127.0.0.1), otherwise the probe tests a different binding.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


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


def check_webserver_port(port: int, parent=None, host: str = "127.0.0.1") -> bool:
    """Return True if the port is free (or was freed by killing stale procs).

    Shows a QMessageBox when another process is already listening so the user
    can choose to terminate it. Called on the GUI thread from the event loop
    (Window._StartWebServer), so the exec() below is an ordinary top-level
    modal and not a nested one (agent-pitfalls #9).

    The common case (port free) costs a bind attempt; the netstat/tasklist
    subprocesses only run when something really holds the port. Since the
    single-instance guard removed self-collisions, that is now genuinely rare.

    NOTE: the return value is advisory — the only caller starts the server
    either way and surfaces a genuine bind failure through the server's
    start-failure callback. Answering "No" therefore does not stop the server: on Windows it
    can still bind the already-held port (Werkzeug sets SO_REUSEADDR), which
    is why the dialog asks the user to change the port and restart.
    """
    if _port_is_free(port, host):
        return True

    conflicts = _netstat_listeners(port)
    if not conflicts:
        # Bind failed but no other TCP listener owns the port (a socket in
        # TIME_WAIT, or a holder netstat can't attribute). There is nothing
        # to offer terminating — let the server start and report a real
        # bind error through its start-failure callback if it still can't
        # listen.
        logger.warning(
            f"Port {port} could not be bound but no listening process was found")
        return True

    details = ', '.join(f"{pid} ({_process_name(pid)})" for pid in sorted(conflicts))
    logger.warning(f"Port {port} already held by PIDs: {details}")

    text = (
        QApplication.translate(
            "portguard",
            "Port {port} is already in use by another process:\n\n{details}\n\n"
            "If this is a stale Autona instance, the Web UI will appear blank "
            "because Windows randomly routes connections to whichever process answers.\n\n"
            "Terminate the existing process(es) and continue?\n\n"
            "Alternatively, choose No and change the port under "
            "Settings → General → Webserver Port, then restart Autona.",
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
    # No is the default: killing the other process is destructive, and a
    # stray Enter here used to terminate a healthy instance holding live
    # tournament state.
    box.setDefaultButton(QMessageBox.StandardButton.No)
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
        if _port_is_free(port, host):
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


def log_webserver_port(port: int, host: str = "127.0.0.1") -> bool:
    """The headless shell's port check (ADR-005 S3c): the probe
    check_webserver_port() runs, reported to the log instead of a dialog.

    Nothing is terminated -- there is nobody to ask -- and the server is
    started either way, as on the desktop (a real bind failure is reported
    through its start-failure callback). Returns True when the port is free
    or no other listener holds it.
    """
    if _port_is_free(port, host):
        return True

    conflicts = _netstat_listeners(port)
    if not conflicts:
        logger.warning(
            f"Port {port} could not be bound but no listening process was found")
        return True

    details = ', '.join(f"{pid} ({_process_name(pid)})" for pid in sorted(conflicts))
    logger.warning(
        f"Port {port} already held by PIDs: {details}. Windows may route "
        "connections to that process instead of this one; stop it, or change "
        "general.webserver_port in user_data/settings.json and restart.")
    return False
