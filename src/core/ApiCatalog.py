"""The External Control API's reference catalogue (ADR-005 S-final SF-3a).

What ``GET /api/v1/api-catalog`` returns for the API reference & tester
page (web/src/GuidePages/ApiPage.tsx, /web/api; SF-4a-2): a curated row per
route a person operates by hand, the error codes and the placeholders a
copy-pasteable path fills in. Until SF-3a the rows were the Qt API Settings
window's own (``_ENDPOINTS``); the window went in SF-4a-2.

Each row's ``id`` is the name of its /api/v1 route (the Flask endpoint
``api_v1.<id>``), a key that does not change with the wording: the page
translates by it (i18next: ``guide_api_endpoint_<id>``, the error codes'
``guide_api_error_<code>``). ``desc`` is the English text, the page's when a
row has no translation. tests/test_api_v1_guides.py checks that every row's
method and example path reach the route its id names, and that every id
and code has its translation in en and ja.

The access token's routes (``/access-token``, ``/access-token:regenerate``;
WebAccess.SHELL, SF-4a-1) are not listed: nothing but the app's own window
may call them, so they are not for a person to operate by hand. Nor are the
game asset routes (``/assets/job``, ``/assets/uploads``,
``/assets:importZip``, ``/assets/packs:install``, ``/assets/job:cancel``,
``/assets/games-folder:reveal``; SHELL, SF-4b-1) for the same reason.

Core module: pure data and a copy of it -- no Qt (tests/test_core_qt_free
.py), nothing read or built at import time.
"""

import copy
from typing import NamedTuple


class Endpoint(NamedTuple):
    """One row. ``body`` is an example payload or None; ``destructive``
    makes the page's tester ask first; ``logged`` is the write op-log's;
    ``confirm`` means the API itself refuses the call without
    {"confirm": true}."""
    id: str
    method: str
    path: str
    desc: str
    body: object
    destructive: bool
    logged: bool
    confirm: bool


class ErrorCode(NamedTuple):
    code: str
    status: int
    desc: str


# Placeholder substitutions that turn a template path into a concrete,
# copy-pasteable one (the page's curl and tester preset).
PATH_DEFAULTS = {"{n}": "1", "{t}": "1", "{p}": "1", "{m}": "1",
                 "{setId}": "12345", "{code}": "JP", "{slot}": "1",
                 "{kind}": "commentary", "{feature}": "commentary"}


def ConcretePath(path):
    """``path`` with its {n}/{t}/{setId}... placeholders filled in."""
    for token, value in PATH_DEFAULTS.items():
        path = path.replace(token, value)
    return path


E = Endpoint

ENDPOINTS = (
    # ── Read (GET, not op-logged) ──
    E("state", "GET", "/state", "Full state + revision", None, False, False, False),
    E("health", "GET", "/health", "Liveness / version / activeBoard", None, False, False, False),
    E("get_scoreboard", "GET", "/scoreboards/{n}", "Scoreboard n state (incl. swapped)", None, False, False, False),
    E("get_scoreboard_pending", "GET", "/scoreboards/{n}/pending", "Scoreboard n live values, not applied yet: held, dirty, board (a token from another computer)", None, False, False, False),
    E("get_scoreboard_follow", "GET", "/scoreboards/{n}/follow", "What scoreboard n follows: station, poll, pollingEnabled, setId", None, False, False, False),
    E("get_scoreboard_post", "GET", "/scoreboards/{n}/post", "Scoreboard n's X post: text, and what is missing for it (a token from another computer)", None, False, False, False),
    E("get_battle_format", "GET", "/battle-format", "Battle format (singles | team | crew) and the board it shows", None, False, False, False),
    E("get_games", "GET", "/games", "Game list", None, False, False, False),
    E("get_current_game", "GET", "/games/current", "Current game", None, False, False, False),
    E("get_match_names", "GET", "/match-names", "Match / phase name candidates", None, False, False, False),
    E("get_match_name_candidates", "GET", "/match-names/candidates?board={n}", "What board n's phase, pool and match combos offer (boards 1/2: the tournament's names too)", None, False, False, False),
    E("get_characters", "GET", "/characters", "Character list", None, False, False, False),
    E("get_variants", "GET", "/variants", "Variant list", None, False, False, False),
    E("get_sets", "GET", "/sets?finished=true", "Set list (synchronous start.gg call)", None, False, False, False),
    E("get_match", "GET", "/sets/{setId}", "Set detail (synchronous start.gg call)", None, False, False, False),
    E("get_tournament_phases", "GET", "/tournament/phases", "The tournament's phases, as last fetched (never fetches)", None, False, False, False),
    E("get_tournament_sets", "GET", "/tournament/sets", "The set list, as last fetched (never fetches)", None, False, False, False),
    E("get_tournament_stations", "GET", "/tournament/stations", "Stations and streams, as last fetched (never fetches)", None, False, False, False),
    E("get_commentary", "GET", "/commentary", "Commentary state", None, False, False, False),
    E("get_country_states", "GET", "/countries/{code}/states", "States / regions of a country", None, False, False, False),
    E("get_layouts", "GET", "/layouts?kind=scoreboard", "Layout list (?kind=scoreboard|designer|commentary|top8)", None, False, False, False),
    E("get_selected_layout", "GET", "/layouts/selected/{kind}", "Layout a panel shows (kind=commentary|top8|board)", None, False, False, False),
    E("get_board_files", "GET", "/layouts/selected/board/obs-path", "The Board's OBS files, as absolute paths (a token from another computer)", None, False, False, False),
    E("get_commentary_options", "GET", "/commentary/options", "Commentary role / icon candidates", None, False, False, False),
    E("get_settings", "GET", "/settings", "Settings: general, network, language, hotkeys (never the access token)", None, False, False, False),
    # ── Safe write (op-logged, no confirm) ──
    E("score_adjust", "POST", "/scoreboards/{n}/teams/{t}/score:adjust", "Adjust score by delta", {"delta": 1}, False, True, False),
    E("score_reset", "POST", "/scoreboards/{n}/score:reset", "Reset scores", None, False, True, False),
    E("score_set", "POST", "/scoreboards/{n}/score:set", "Set both scores (absolute)", {"team1": 2, "team2": 1}, False, True, False),
    E("apply_changes", "POST", "/scoreboards/{n}:apply", "Apply pending edits (反映)", None, False, True, False),
    E("swap_teams", "POST", "/scoreboards/{n}:swap-teams", "Swap teams", None, False, True, False),
    E("stats_refresh", "POST", "/scoreboards/{n}/stats:refresh", "Refresh statistics (async fetch)", None, False, True, False),
    E("team_color", "POST", "/scoreboards/{n}/teams/{t}/color", "Set team color", {"color": "#FF0000"}, False, True, False),
    E("set_update", "PATCH", "/scoreboards/{n}/set", "Patch set info (partial)", {"bestOf": 5, "phase": "Top 8", "pool": "Pool A1", "match": "Winners Finals"}, False, True, False),
    E("match_update", "PATCH", "/scoreboards/{n}/match", "Set the match bar as its inputs do (partial; \"\" empties a field, match_en untouched)", {"phase": "Top 8", "pool": "", "match": "Winners Finals", "bestOf": 5}, False, True, False),
    E("player_update", "PATCH", "/scoreboards/{n}/teams/{t}/players/{p}", "Update player fields (partial)", {"gamerTag": "Player", "twitter": "player_x"}, False, True, False),
    E("player_fields_update", "PATCH", "/scoreboards/{n}/teams/{t}/players/{p}/fields", "Write player fields as the card does (partial; \"\" empties a field)", {"prefix": "TM", "gamerTag": "Player", "country_code": "JP"}, False, True, False),
    E("player_character_update", "PATCH", "/scoreboards/{n}/teams/{t}/players/{p}/characters/1", "Pick a player's character row (en_name \"\" empties it)", {"en_name": "Mario", "skin": 0}, False, True, False),
    E("player_load", "POST", "/scoreboards/{n}/teams/{t}/players/{p}:load", "Load a player DB entry into a slot by key (or tag); t is the set's team", {"key": "TM Player"}, False, True, False),
    E("player_swap", "POST", "/scoreboards/{n}/teams/{t}/players:swap", "Exchange two players of a team", {"first": 1, "second": 2}, False, True, False),
    E("crew_member_update", "PATCH", "/scoreboards/3/teams/{t}/members/{m}", "Update a crew member (partial): name, stock, active", {"name": "Player", "stock": 3, "active": True}, False, True, False),
    E("crew_member_stock_adjust", "POST", "/scoreboards/3/teams/{t}/members/{m}/stock:adjust", "Adjust a crew member's stock by delta", {"delta": -1}, False, True, False),
    E("tournament_info_update", "PATCH", "/tournament/info", "Update tournament fields (partial)", {"displayTournamentName": "Tournament"}, False, True, False),
    E("display_options_update", "PATCH", "/display-options", "Toggle display options (partial)", {"show_seed": False}, False, True, False),
    E("battle_format_set", "PUT", "/battle-format", "Switch the battle format (the same one changes nothing)", {"format": "team"}, False, True, False),
    E("commentary_update", "PATCH", "/commentary/{slot}", "Update caster fields (partial; a 5th shown is 409)", {"name": "Caster", "display": True}, False, True, False),
    E("commentary_reorder", "POST", "/commentary/reorder", "Reorder casters (slot order)", {"order": ["2", "1", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]}, False, True, False),
    E("commentary_player", "POST", "/commentary/{slot}/player", "Fill a caster from a player DB entry", {"gamerTag": "Player", "prefix": "TM", "twitter": "player_x"}, False, True, False),
    E("select_layout", "PUT", "/layouts/selected/{kind}", "Select a panel's layout", {"id": "commentators"}, False, True, False),
    E("reveal_board_file", "POST", "/layouts/selected/board:reveal", "Show a Board OBS file in Explorer (this PC only; a design no longer selected is 409)", {"target": "design", "designId": "scoreboard_simple"}, False, True, False),
    E("colors_update", "PATCH", "/colors/{feature}", "Set a feature's colors", {"theme": "#112233", "accent": "#445566"}, False, True, False),
    E("settings_general_update", "PATCH", "/settings/general", "Update general settings (partial)", {"profanity_filter": True}, False, True, False),
    E("settings_network_update", "PATCH", "/settings/network", "Set the web server's port / LAN access (this PC only; applied by a restart)", {"port": 5107}, False, True, False),
    E("settings_language_update", "PATCH", "/settings/language", "Set the languages (this PC only; applied by a restart)", {"program": "default"}, False, True, False),
    E("settings_hotkeys_update", "PATCH", "/settings/hotkeys", "Set hotkeys (this PC only; a shortcut another action has is 409)", {"keys": {"load_set": "Ctrl+Shift+O"}}, False, True, False),
    E("stop_follow", "POST", "/scoreboards/{n}:stop-follow", "Stop following, forget the station and the followed set", None, False, True, False),
    E("tournament_sets_cancel", "POST", "/tournament/sets:cancel", "Stop the set list's fetch (the rows stay)", None, False, True, False),
    # ── Destructive (confirm required) ──
    E("clear", "POST", "/scoreboards/{n}:clear", "Clear board (confirm required)", {"confirm": True}, True, True, True),
    E("reset_all", "POST", "/scoreboards/{n}:reset-all", "Reset every input, team names too, keeping the counts (confirm required)", {"confirm": True}, True, True, True),
    E("player_clear", "POST", "/scoreboards/{n}/teams/{t}/players/{p}:clear", "Clear one player (confirm required)", {"confirm": True}, True, True, True),
    E("reset_players", "POST", "/scoreboards/{n}:reset-players", "Reset players (confirm required)", {"confirm": True}, True, True, True),
    E("reset_match", "POST", "/scoreboards/{n}:reset-match", "Reset match (confirm required)", {"confirm": True}, True, True, True),
    # ── Board overwrite (confirm recommended) ──
    E("load_set", "POST", "/scoreboards/{n}:load-set", "Load a set onto the board", {"setId": "12345"}, False, True, False),
    E("pull_stream", "POST", "/scoreboards/{n}:pull-stream", "Pull current stream set", None, False, True, False),
    E("follow", "POST", "/scoreboards/{n}:follow", "Follow a station or stream: its current set is loaded", {"type": "stream", "identifier": "channel"}, False, True, False),
    # ── Heavy (202 accepted + in-progress flag) ──
    E("tournament_load", "POST", "/tournament", "Load tournament (202, async)", {"url": "https://www.start.gg/tournament/.../event/..."}, False, True, False),
    E("game_set", "POST", "/game", "Set game (202, async)", {"codename": "ssbu", "modsActive": False}, False, True, False),
    E("bracket_refresh", "POST", "/bracket:refresh", "Refresh bracket (202, async)", None, False, True, False),
    E("tournament_phases_refresh", "POST", "/tournament/phases:refresh", "Fetch the tournament's phases (202, async)", None, False, True, False),
    E("tournament_sets_refresh", "POST", "/tournament/sets:refresh", "Fetch the set list of a phase (202, async; another query replaces the one running)", {"phase": "all", "finished": False}, False, True, False),
    E("tournament_stations_refresh", "POST", "/tournament/stations:refresh", "Fetch the stations and streams (202, async)", None, False, True, False),
)

del E

# The error envelope's codes (spec §2.6 / AutonaApiV1.py).
ERROR_CODES = (
    ErrorCode("INVALID_ARGUMENT", 400, "Bad parameter"),
    ErrorCode("NOT_FOUND", 404, "Unknown resource"),
    ErrorCode("CONFIRMATION_REQUIRED", 409, "Missing confirm:true"),
    ErrorCode("CONFLICT", 409, "Heavy operation already running"),
    ErrorCode("NOT_READY", 409, "Precondition unmet"),
    ErrorCode("UPSTREAM_TIMEOUT", 504, "Upstream (start.gg) timed out"),
    ErrorCode("INTERNAL", 500, "Unexpected internal error"),
    # The access guard (ADR-005 S3a, src/core/WebAccess.py).
    ErrorCode("UNAUTHENTICATED", 401, "Token missing or wrong (writes from another device)"),
    ErrorCode("FORBIDDEN", 403, "Refused browser page (cross-site / another origin)"),
    # A desktop shell operation in the headless shell (ADR-005 S3c / S3e-9).
    ErrorCode("UNAVAILABLE", 503, "Not available in headless (needs the desktop window)"),
)


def Payload():
    """What GET /api/v1/api-catalog returns: {"endpoints": [{id, method,
    path, example (the path with its placeholders filled in), description
    (the English source; a reader translates by id), body, destructive,
    logged, confirm}], "errorCodes": [{code, status, description}]}, in the
    catalogue's order. A copy: nothing a caller does to it reaches the
    catalogue."""
    return {
        "endpoints": [
            {"id": entry.id,
             "method": entry.method,
             "path": entry.path,
             "example": ConcretePath(entry.path),
             "description": entry.desc,
             "body": copy.deepcopy(entry.body),
             "destructive": entry.destructive,
             "logged": entry.logged,
             "confirm": entry.confirm}
            for entry in ENDPOINTS],
        "errorCodes": [
            {"code": error.code, "status": error.status,
             "description": error.desc}
            for error in ERROR_CODES],
    }
