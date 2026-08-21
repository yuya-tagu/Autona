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
EXPECTED_ROUTES = [
    ('/', 'web_index', 'GET,HEAD,OPTIONS'),
    ('/./<path:filename>', 'static', 'GET,HEAD,OPTIONS'),
    ('/<path:filename>', 'file_request', 'GET,HEAD,OPTIONS,POST'),
    ('/api/designer/projects', 'designer_list_projects', 'GET,HEAD,OPTIONS'),
    ('/api/designer/projects', 'designer_save_project', 'OPTIONS,POST'),
    ('/api/designer/projects/<slug>', 'designer_delete_project', 'DELETE,OPTIONS'),
    ('/api/designer/projects/<slug>', 'designer_load_project', 'GET,HEAD,OPTIONS'),
    ('/api/designer/imports', 'designer_receive_import', 'OPTIONS,POST'),
    ('/api/designer/imports/<import_id>', 'designer_get_import', 'GET,HEAD,OPTIONS'),
    ('/api/designer/imports/latest', 'designer_latest_import', 'GET,HEAD,OPTIONS'),
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
# before/after hooks that used to carry a redundant @staticmethod.
EXPECTED_CLASS_HANDLERS = {
    # SocketIO handlers that must stay reachable as WebServer attributes,
    # because something outside the factory reaches them by name:
    #   ws_program_state    -> StateManager.signals.state_big_change and
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
    "_flush_pending_deltas",
    "_adaptive_debounce_sec",
}

EXPECTED_EXTRA = {
    "blueprints": [],
    "static_url_path": "/.",
    "static_folder_tail": ".",
    "config_CORS_HEADERS": "Content-Type",
    "socketio_async_mode": "threading",
    "socketio_default_exception_handler": "ws_on_error",
    "socketio_exception_handlers": [],
    "before_request_funcs": {"None": ["log_request"]},
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
assert WebServer.actions is None, "import built WebServer.actions"

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
