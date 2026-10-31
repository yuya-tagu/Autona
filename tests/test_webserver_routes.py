"""Route/event ledger snapshot for AutonaWebServer.

Step 3e of the startup redesign moved the Flask app, the SocketIO server and
all ~90 route/event handlers out of the ``WebServer`` class body (where they
ran as an import side effect) into the module-level ``_build_web_app()``
factory, which ``WebServer.__init__`` calls once. Nothing about the exposed
surface may change in that move -- OBS browser sources, the React Web UI and
the external control API all address these URLs and event names verbatim.

The ledger below was dumped from the pre-refactor code and is pinned here, so
a dropped, duplicated or renamed handler fails loudly instead of silently
returning 404 to a stream overlay. Update it only together with a deliberate,
documented API change (external routes: ADR-001 / ADR-003).

Like tests/test_import_side_effects.py this runs in a subprocess, so the
module is observed in a fresh interpreter that no other test has imported it
in. The seeded temp cwd is needed for the same reason as there -- several
modules in the import chain read data files relative to Path.cwd().
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

# (rule, endpoint, comma-joined sorted methods)
#
# ADR-005 S3a changed two rows on purpose: the Flask static folder (which
# served the whole working directory under /./) is gone, and file_request no
# longer takes POST (nothing posted to a file; see the route's comment).
# ADR-005 S4a added /web/<panel> on web_index (the React panels' pages).
EXPECTED_ROUTES = [
    ('/', 'web_index', 'GET,HEAD,OPTIONS'),
    ('/<path:filename>', 'file_request', 'GET,HEAD,OPTIONS'),
    ('/api/designer/projects', 'designer_list_projects', 'GET,HEAD,OPTIONS'),
    ('/api/designer/projects', 'designer_save_project', 'OPTIONS,POST'),
    ('/api/designer/projects/<slug>', 'designer_delete_project', 'DELETE,OPTIONS'),
    ('/api/designer/projects/<slug>', 'designer_load_project', 'GET,HEAD,OPTIONS'),
    ('/api/designer/imports', 'designer_receive_import', 'OPTIONS,POST'),
    ('/api/designer/imports/<import_id>', 'designer_get_import', 'GET,HEAD,OPTIONS'),
    ('/api/designer/imports/latest', 'designer_latest_import', 'GET,HEAD,OPTIONS'),
    ('/api/designer/pairing', 'designer_pairing_code', 'GET,HEAD,OPTIONS'),
    ('/api/designer/pairing/regenerate', 'designer_regenerate_pairing_code', 'OPTIONS,POST'),
    ('/characters', 'get_characters', 'GET,HEAD,OPTIONS'),
    ('/current-game', 'get_current_game', 'GET,HEAD,OPTIONS'),
    ('/display-option', 'post_display_option', 'OPTIONS,POST'),
    ('/games', 'get_games', 'GET,HEAD,OPTIONS'),
    ('/get-comms', 'get_comms', 'GET,HEAD,OPTIONS'),
    ('/get-match-<setId>', 'get_match', 'GET,HEAD,OPTIONS'),
    ('/get-sets', 'get_sets', 'GET,HEAD,OPTIONS'),
    ('/load-commentator-from-tag-<caster>', 'load_commentator_from_tag', 'GET,HEAD,OPTIONS'),
    ('/match-names', 'get_match_names', 'GET,HEAD,OPTIONS'),
    ('/playerdb', 'playerdb', 'GET,HEAD,OPTIONS'),
    ('/program-state', 'program_state', 'GET,HEAD,OPTIONS'),
    ('/score', 'post_score', 'OPTIONS,POST'),
    ('/scoreboard', 'web_index', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-clear-all', 'clear_all', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-crew-active<team>-<member>', 'crew_set_active', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-crew-member<team>-<member>-stockdown', 'crew_member_stockdown', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-crew-member<team>-<member>-stockup', 'crew_member_stockup', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-get', 'get_route', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-get-set', 'get_set', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-get-swap', 'get_swap', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-get-thumbnail-<fileFormat>', 'get_thumbnail', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-load-player-from-tag-<team>-<player>', 'load_player_from_tag', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-load-set', 'load_set', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-open-set', 'open_sets', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-pull-stream', 'pull_stream_set', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-reset-match', 'reset_match', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-reset-players', 'reset_players', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-reset-scores', 'reset_scores', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-set', 'set_route', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-stats-history-sets-<player>', 'stats_history_sets', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-stats-last-sets-<player>', 'stats_last_sets', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-stats-recent-sets', 'stats_recent_sets', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-stats-upset-factor', 'stats_upset_factor', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-swap-teams', 'swap_teams', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-team<team>-color-<color>', 'team_color', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-team<team>-scoredown', 'team_scoredown', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-team<team>-scoreup', 'team_scoreup', 'GET,HEAD,OPTIONS'),
    ('/scoreboard<scoreboardNumber>-update-team-<team>-<player>', 'set_team_data', 'OPTIONS,POST'),
    ('/set-tournament', 'set_tournament', 'GET,HEAD,OPTIONS'),
    ('/states', 'get_states', 'GET,HEAD,OPTIONS'),
    ('/team', 'web_index', 'GET,HEAD,OPTIONS'),
    ('/update-bracket', 'update_bracket', 'GET,HEAD,OPTIONS'),
    ('/update-commentary-<caster>', 'set_commentary_data', 'OPTIONS,POST'),
    ('/update-game', 'set_game', 'OPTIONS,POST'),
    ('/variants', 'get_variants', 'GET,HEAD,OPTIONS'),
    ('/web', 'web_index', 'GET,HEAD,OPTIONS'),
    # ADR-005 S4a: the React panels' pages (WebAccess.WEB_PANELS only).
    ('/web/<panel>', 'web_index', 'GET,HEAD,OPTIONS'),
]

# The /api/v1 blueprint (src/AutonaApiV1.py, ADR-001). WebServer.__init__
# registers it, not _build_web_app() -- hence "blueprints": [] below -- so it
# is pinned separately by test_api_v1_route_ledger. Same rule: update only
# with a documented API change (.claude/design/external-control-api.md).
# score:set / players/<p> / display-options / countries/<code>/states were
# added for the Web UI's move off the legacy routes (ADR-001 Addendum
# 2026-10-21); commentary/* / layouts/selected/<kind> / colors/<feature> for
# the Commentary page (ADR-005 S4b).
EXPECTED_API_V1_ROUTES = [
    ('/api/v1/bracket:refresh', 'api_v1.bracket_refresh', 'OPTIONS,POST'),
    ('/api/v1/characters', 'api_v1.get_characters', 'GET,HEAD,OPTIONS'),
    ('/api/v1/colors/<feature>', 'api_v1.colors_update', 'OPTIONS,PATCH'),
    ('/api/v1/commentary', 'api_v1.get_commentary', 'GET,HEAD,OPTIONS'),
    ('/api/v1/commentary/<int:slot>', 'api_v1.commentary_update', 'OPTIONS,PATCH'),
    ('/api/v1/commentary/<int:slot>/player', 'api_v1.commentary_player', 'OPTIONS,POST'),
    ('/api/v1/commentary/options', 'api_v1.get_commentary_options', 'GET,HEAD,OPTIONS'),
    ('/api/v1/commentary/reorder', 'api_v1.commentary_reorder', 'OPTIONS,POST'),
    ('/api/v1/countries/<code>/states', 'api_v1.get_country_states', 'GET,HEAD,OPTIONS'),
    ('/api/v1/display-options', 'api_v1.display_options_update', 'OPTIONS,PATCH'),
    ('/api/v1/game', 'api_v1.game_set', 'OPTIONS,POST'),
    ('/api/v1/games', 'api_v1.get_games', 'GET,HEAD,OPTIONS'),
    ('/api/v1/games/current', 'api_v1.get_current_game', 'GET,HEAD,OPTIONS'),
    ('/api/v1/health', 'api_v1.health', 'GET,HEAD,OPTIONS'),
    ('/api/v1/layouts', 'api_v1.get_layouts', 'GET,HEAD,OPTIONS'),
    ('/api/v1/layouts/selected/<kind>', 'api_v1.get_selected_layout', 'GET,HEAD,OPTIONS'),
    ('/api/v1/layouts/selected/<kind>', 'api_v1.select_layout', 'OPTIONS,PUT'),
    ('/api/v1/match-names', 'api_v1.get_match_names', 'GET,HEAD,OPTIONS'),
    ('/api/v1/scoreboards/<int:n>', 'api_v1.get_scoreboard', 'GET,HEAD,OPTIONS'),
    ('/api/v1/scoreboards/<int:n>/score:reset', 'api_v1.score_reset', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>/score:set', 'api_v1.score_set', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>/set', 'api_v1.set_update', 'OPTIONS,PATCH'),
    ('/api/v1/scoreboards/<int:n>/stats:refresh', 'api_v1.stats_refresh', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>/teams/<int:t>/color', 'api_v1.team_color', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>/teams/<int:t>/players/<int:p>', 'api_v1.player_update', 'OPTIONS,PATCH'),
    ('/api/v1/scoreboards/<int:n>/teams/<int:t>/score:adjust', 'api_v1.score_adjust', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>:apply', 'api_v1.apply_changes', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>:clear', 'api_v1.clear', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>:load-set', 'api_v1.load_set', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>:pull-stream', 'api_v1.pull_stream', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>:reset-match', 'api_v1.reset_match', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>:reset-players', 'api_v1.reset_players', 'OPTIONS,POST'),
    ('/api/v1/scoreboards/<int:n>:swap-teams', 'api_v1.swap_teams', 'OPTIONS,POST'),
    ('/api/v1/sets', 'api_v1.get_sets', 'GET,HEAD,OPTIONS'),
    ('/api/v1/sets/<setId>', 'api_v1.get_match', 'GET,HEAD,OPTIONS'),
    ('/api/v1/state', 'api_v1.state', 'GET,HEAD,OPTIONS'),
    ('/api/v1/tournament', 'api_v1.tournament_load', 'OPTIONS,POST'),
    ('/api/v1/variants', 'api_v1.get_variants', 'GET,HEAD,OPTIONS'),
]

# SocketIO handlers, all on the default namespace "/".
EXPECTED_EVENTS = [
    'characters',
    'clear_all',
    'connect',
    'crew_member_stock',
    'crew_set_active',
    'current_game',
    'games',
    'get_comms',
    'get_match',
    'get_set',
    'get_sets',
    'get_swap',
    'load_commentator_from_tag',
    'load_player_from_tag',
    'load_set',
    'match_names',
    'open_set',
    'playerdb',
    'program-state-update',
    'program_state',
    'pull_stream',
    'reset_match',
    'reset_players',
    'reset_scores',
    'score',
    'set',
    'set_tournament',
    'states',
    'stats_history_sets',
    'stats_last_sets',
    'stats_recent_sets',
    'stats_upset_factor',
    'swap_teams',
    'team_color',
    'team_scoredown',
    'team_scoreup',
    'update_bracket',
    'update_commentary',
    'update_game',
    'update_team',
    'variants',
]

# Everything the factory configures besides the rules themselves. CORS
# installs `cors_after_request`; `log_request` / `log_response` are the
# before/after hooks that used to carry a redundant @staticmethod;
# `guard_request` is the ADR-005 S3a access guard (src/core/WebAccess.py).
EXPECTED_CLASS_HANDLERS = {
    # SocketIO handlers that must stay reachable as WebServer attributes,
    # because something outside the factory reaches them by name:
    #   ws_program_state    -> StateManagerBridge.instance.state_big_change and
    #                          WebServer.ws_program_state() calls in-module
    #   ws_get_characters   -> Autona.py: Window.signals.GameChanged
    #   ws_playerdb         -> Autona.py: AutonaPlayerDB.signals.db_updated
    # Turning any of these into a closure inside _build_web_app() raises
    # AttributeError at Window construction, i.e. at every launch.
    "ws_program_state",
    "ws_get_characters",
    "ws_playerdb",
    # Not registered on a route/event, but called by name from handlers or
    # from Autona.py / StateManager wiring.
    "ws_emit",
    "on_program_state_update",
    "_authorize_designer_request",
    "_authorize_designer_import_request",
    # The ADR-005 S3a guard, reached from guard_request / ws_on / ws_connect
    # / file_request inside the factory.
    "_guard_http_request",
    "_authorize_ws_connection",
    "_ws_guard",
    "_log_static_refusal",
    "_flush_pending_deltas",
    "_adaptive_debounce_sec",
}

EXPECTED_EXTRA = {
    "blueprints": [],
    "static_url_path": None,
    "static_folder_tail": "None",
    "config_CORS_HEADERS": "Content-Type",
    "socketio_async_mode": "threading",
    "socketio_default_exception_handler": "ws_on_error",
    "socketio_exception_handlers": [],
    "before_request_funcs": {"None": ["guard_request", "log_request"]},
    "after_request_funcs": {"None": ["cors_after_request", "log_response"]},
}

_DUMP_SCRIPT = r'''
import importlib
import json
import sys

sys.dont_write_bytecode = True

mod = importlib.import_module("src.AutonaWebServer")
WebServer = mod.WebServer

# The whole point of Step 3e: importing the module builds nothing.
assert WebServer.app is None, "import built WebServer.app"
assert WebServer.socketio is None, "import built WebServer.socketio"
# ADR-005 S3e-9: the desktop's actions object is gone; the shell operations
# are AutonaWebOperations' registrations.
assert not hasattr(WebServer, "actions"), "WebServer.actions is back"

from qtpy.QtWidgets import QApplication
assert QApplication.instance() is None, "import created a QApplication"

app, socketio = mod._build_web_app()

routes = sorted(
    (str(r.rule), r.endpoint, ",".join(sorted(r.methods)))
    for r in app.url_map.iter_rules()
)

events = []
server = getattr(socketio, "server", None)
if server is not None:
    for ns, handlers in getattr(server, "handlers", {}).items():
        events.extend((ns, name) for name in handlers)
# Handlers queue on the SocketIO object only when no server exists yet; they
# would silently never fire, so surface them instead of ignoring them.
for entry in getattr(socketio, "handlers", []):
    events.append((entry[0], "QUEUED:" + entry[1]))
events.sort()

deh = getattr(socketio, "default_exception_handler", None)
extra = {
    "blueprints": sorted(app.blueprints.keys()),
    "static_url_path": app.static_url_path,
    "static_folder_tail": str(app.static_folder).replace("\\", "/").split("/")[-1],
    "config_CORS_HEADERS": app.config.get("CORS_HEADERS"),
    "socketio_async_mode": getattr(socketio, "async_mode", None),
    "socketio_default_exception_handler": deh.__name__ if deh else None,
    "socketio_exception_handlers": sorted(
        getattr(socketio, "exception_handlers", {}).keys()),
    "before_request_funcs": {
        str(k): sorted(f.__name__ for f in v)
        for k, v in app.before_request_funcs.items()},
    "after_request_funcs": {
        str(k): sorted(f.__name__ for f in v)
        for k, v in app.after_request_funcs.items()},
}

class_handlers = sorted(
    name for name in sys.argv[1].split(",") if callable(getattr(WebServer, name, None))
)

print("<<<LEDGER>>>" + json.dumps(
    {"routes": routes, "events": events, "extra": extra,
     "class_handlers": class_handlers}))
'''

# Same seeding as tests/test_import_side_effects.py; see its docstring.
_SEED_FILES = [
    "src/i18n/mapping.json",
    "assets/data_countries.json",
]
_SEED_DIRS = [
    "assets/ngword",
]


def _seed_cwd(tmp_path):
    (tmp_path / "user_data").mkdir()

    for rel in _SEED_FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / rel, dest)

    for rel in _SEED_DIRS:
        shutil.copytree(PROJECT_ROOT / rel, tmp_path / rel)

    query_dir = tmp_path / "src" / "TournamentDataProvider"
    query_dir.mkdir(parents=True, exist_ok=True)
    for query_file in (SRC_ROOT / "TournamentDataProvider").glob("StartGG*Query.txt"):
        shutil.copyfile(query_file, query_dir / query_file.name)


def _dump_ledger(tmp_path):
    _seed_cwd(tmp_path)

    env = dict(os.environ)
    env["QT_API"] = "pyside6"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    result = subprocess.run(
        [sys.executable, "-B", "-c", _DUMP_SCRIPT,
         ",".join(sorted(EXPECTED_CLASS_HANDLERS))],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ledger dump failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}")

    marker = "<<<LEDGER>>>"
    assert marker in result.stdout, f"no ledger in output:\n{result.stdout}"
    return json.loads(result.stdout.split(marker, 1)[1])


def test_webserver_route_and_event_ledger(tmp_path):
    ledger = _dump_ledger(tmp_path)

    routes = [tuple(r) for r in ledger["routes"]]
    assert routes == sorted(EXPECTED_ROUTES), (
        "URL rules changed.\n"
        f"missing: {sorted(set(EXPECTED_ROUTES) - set(routes))}\n"
        f"unexpected: {sorted(set(routes) - set(EXPECTED_ROUTES))}")

    events = [tuple(e) for e in ledger["events"]]
    expected_events = sorted(("/", name) for name in EXPECTED_EVENTS)
    assert events == expected_events, (
        "SocketIO events changed.\n"
        f"missing: {sorted(set(expected_events) - set(events))}\n"
        f"unexpected: {sorted(set(events) - set(expected_events))}")

    assert ledger["extra"] == EXPECTED_EXTRA

    missing = EXPECTED_CLASS_HANDLERS - set(ledger["class_handlers"])
    assert not missing, (
        "these must stay callable WebServer attributes (see the set's "
        f"comment); they are no longer reachable: {sorted(missing)}")


def test_api_v1_route_ledger():
    # In-process: importing src.AutonaApiV1 only declares the Blueprint
    # (tests/test_import_side_effects.py), and registering it on a bare app
    # is all WebServer.__init__ does with it.
    from flask import Flask

    from src.AutonaApiV1 import api_v1

    app = Flask(__name__)
    app.register_blueprint(api_v1)
    routes = sorted(
        (str(r.rule), r.endpoint, ",".join(sorted(r.methods)))
        for r in app.url_map.iter_rules()
        if r.endpoint.startswith("api_v1.")
    )
    assert routes == sorted(EXPECTED_API_V1_ROUTES), (
        "/api/v1 routes changed.\n"
        f"missing: {sorted(set(EXPECTED_API_V1_ROUTES) - set(routes))}\n"
        f"unexpected: {sorted(set(routes) - set(EXPECTED_API_V1_ROUTES))}")


# ── Access classification (ADR-005 S3a) ──────────────────────────────────
# src/core/WebAccess.py classifies every endpoint and event as READ / WRITE /
# PLAYERDB / DESIGNER / STATIC; the server guards WRITE and PLAYERDB. The
# ledgers above are pinned against the live app, so comparing the tables to
# them covers every route and event the server really registers: a new one
# fails here until it is classified (an unclassified one would be guarded
# like a write, but silently).


def _ledger_endpoints():
    return ({endpoint for _rule, endpoint, _methods in EXPECTED_ROUTES}
            | {endpoint for _rule, endpoint, _methods in EXPECTED_API_V1_ROUTES})


def test_every_route_is_classified():
    from src.core import WebAccess

    endpoints = _ledger_endpoints()
    assert set(WebAccess.ROUTE_ACCESS) == endpoints, (
        f"unclassified: {sorted(endpoints - set(WebAccess.ROUTE_ACCESS))}; "
        f"stale:{sorted(set(WebAccess.ROUTE_ACCESS) - endpoints)}")


def test_every_event_is_classified():
    from src.core import WebAccess

    events = set(EXPECTED_EVENTS)
    assert set(WebAccess.EVENT_ACCESS) == events, (
        f"unclassified: {sorted(events - set(WebAccess.EVENT_ACCESS))}; "
        f"stale:{sorted(set(WebAccess.EVENT_ACCESS) - events)}")


def test_the_classification_follows_the_rules():
    from src.core import WebAccess

    access = WebAccess.ROUTE_ACCESS
    # /api/v1: the method says it (spec: GET reads, POST / PATCH operate).
    for _rule, endpoint, methods in EXPECTED_API_V1_ROUTES:
        expected = WebAccess.READ if "GET" in methods.split(",") else WebAccess.WRITE
        assert access[endpoint] == expected, endpoint
    # A legacy route that takes a body changes something.
    for _rule, endpoint, methods in EXPECTED_ROUTES:
        if "POST" in methods.split(",") and not endpoint.startswith("designer_"):
            assert access[endpoint] == WebAccess.WRITE, endpoint
        if endpoint.startswith("designer_"):
            assert access[endpoint] == WebAccess.DESIGNER, endpoint
    # The player DB is personal data on both channels.
    assert access["playerdb"] == WebAccess.PLAYERDB
    assert WebAccess.EVENT_ACCESS["playerdb"] == WebAccess.PLAYERDB
    # A Socket.IO event and the legacy route it mirrors agree, where both
    # exist under one name.
    for event, kind in WebAccess.EVENT_ACCESS.items():
        if event in access and not event.startswith("get_"):
            assert access[event] == kind, event
    # The counts the S3a inventory found (legacy 29 writes of 43, Socket.IO
    # 26 of 41, v1 18 of 31), plus the Commentary page's 5 writes and 2
    # reads (ADR-005 S4b: v1 23 of 38). A change here is a change of the
    # policy.
    legacy = [e for _r, e, _m in EXPECTED_ROUTES
              if not e.startswith("designer_") and access[e] != WebAccess.STATIC]
    assert len(legacy) == 43
    assert sum(access[e] == WebAccess.WRITE for e in legacy) == 29
    assert sum(k == WebAccess.WRITE
               for k in WebAccess.EVENT_ACCESS.values()) == 26
    assert sum(access[e] == WebAccess.WRITE
               for _r, e, _m in EXPECTED_API_V1_ROUTES) == 23
    assert len(EXPECTED_API_V1_ROUTES) == 38


def test_an_unknown_endpoint_or_event_is_guarded():
    from src.core import WebAccess

    assert WebAccess.RouteAccess("not_a_route") == WebAccess.WRITE
    assert WebAccess.EventAccess("not_an_event") == WebAccess.WRITE
