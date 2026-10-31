"""Who may use which part of the web server (ADR-005 S3a).

The web server (src/AutonaWebServer.py, src/AutonaApiV1.py) used to serve
every file below the working directory and accept every operation from
anyone who could reach the port. This module is the policy that closes that
surface; the server only asks it questions. It decides three things:

* **Which files are served** (StaticPath). Only what the overlays, the Web UI
  and the state's image paths load: layout/, assets/, web/build/, out/ and
  the image folders of user_data/. settings.json, local_players.json and the
  rest of user_data/ are not.
* **Which requests need the token** (ROUTE_ACCESS / EVENT_ACCESS +
  Authorize). Reads are open to anyone who can reach the port -- OBS on a
  second PC keeps reading the overlays without one. Writes and the player DB
  (personal data) need, from another computer, the token kept in
  settings.json (``general.webserver_token``).
* **Which requests count as "from this computer".** A request from this
  computer needs no token -- from the loopback or from one of this PC's own
  LAN addresses (a URL with the LAN IP opened here), and not relayed by a
  proxy (``X-Forwarded-For`` / ``Forwarded`` / ``X-Real-IP``: a local
  reverse proxy or tunnel forwards other computers' requests from the
  loopback). But every page open in a browser here is such a request, so
  without a token a write must also be addressed to this server under one of
  its own names (``Host``: a loopback name or a LAN address, at the port it
  listens on -- not a DNS-rebound name) and come from its own pages:
  ``Sec-Fetch-Site`` other than ``same-origin`` / ``none`` and a foreign
  ``Origin`` are refused (CSRF). Non-browser clients (Companion, Stream
  Deck, curl, requests) send neither header and are unaffected.

A request that carries the right token passes all of it: the token is never
sent by a browser on its own (it is not a cookie), so a page that sends it
was given it by the user, and the request is not a forgery. That is what
lets the Web UI keep working under any address of this PC (a second network
card, a host name) once it has the token.

The Designer token (/api/designer/*, loopback only) and the Figma pairing
code are separate mechanisms with their own guards (AutonaDesignerService);
they are classified DESIGNER here and left alone.

Core module: no Qt (tests/test_core_qt_free.py), no Flask, and nothing
happens at import time -- the token is read, or issued and saved, on first
use (Token()), and then kept in memory: only RegenerateToken() changes it.
"""

import hmac
import ipaddress
import re
import secrets
import threading
from typing import NamedTuple
from urllib.parse import urlsplit

from loguru import logger

from .SettingsManager import SettingsManager


# ── Access classes ──────────────────────────────────────────────────────
READ = "read"            # anyone who can reach the port
WRITE = "write"          # token from another computer; CSRF-checked
PLAYERDB = "playerdb"    # the player DB: personal data, guarded like WRITE
DESIGNER = "designer"    # /api/designer/*: Designer token / pairing code
STATIC = "static"        # files: StaticPath()'s allowlist

# The classes Authorize() guards.
GUARDED = frozenset({WRITE, PLAYERDB})

# Every Flask endpoint, by its endpoint name. tests/test_webserver_routes.py
# checks that this table and the route ledger name the same endpoints, so a
# new route has to be classified here before its test passes. An endpoint
# missing from the table is guarded like a write (RouteAccess), never open.
#
# Legacy GET routes that change something are WRITE although they are GETs:
# what counts is the effect (a Stream Deck button pastes the URL), not the
# method. get_thumbnail renders the board and writes the image to out/, so
# it is WRITE too.
ROUTE_ACCESS = {
    # ── legacy (deprecated, frozen) ──
    "program_state": READ,
    "post_score": WRITE,
    "team_scoreup": WRITE,
    "team_scoredown": WRITE,
    "team_color": WRITE,
    "crew_member_stockup": WRITE,
    "crew_member_stockdown": WRITE,
    "crew_set_active": WRITE,
    "get_route": READ,
    "set_route": WRITE,
    "post_display_option": WRITE,
    "set_team_data": WRITE,
    "set_commentary_data": WRITE,
    "set_game": WRITE,
    "get_games": READ,
    "get_current_game": READ,
    "get_match_names": READ,
    "get_characters": READ,
    "get_variants": READ,
    "swap_teams": WRITE,
    "get_swap": READ,
    "open_sets": WRITE,
    "pull_stream_set": WRITE,
    "stats_recent_sets": WRITE,
    "stats_upset_factor": WRITE,
    "stats_last_sets": WRITE,
    "stats_history_sets": WRITE,
    "reset_scores": WRITE,
    "reset_match": WRITE,
    "reset_players": WRITE,
    "clear_all": WRITE,
    "get_thumbnail": WRITE,
    "get_sets": READ,
    "get_match": READ,
    "get_comms": READ,
    "load_set": WRITE,
    "get_set": READ,
    "playerdb": PLAYERDB,
    "update_bracket": WRITE,
    "load_player_from_tag": WRITE,
    "load_commentator_from_tag": WRITE,
    "set_tournament": WRITE,
    "get_states": READ,
    # ── /api/designer (own guards) ──
    "designer_list_projects": DESIGNER,
    "designer_load_project": DESIGNER,
    "designer_delete_project": DESIGNER,
    "designer_save_project": DESIGNER,
    "designer_pairing_code": DESIGNER,
    "designer_regenerate_pairing_code": DESIGNER,
    "designer_receive_import": DESIGNER,
    "designer_get_import": DESIGNER,
    "designer_latest_import": DESIGNER,
    # ── entry page and files ──
    "web_index": STATIC,
    "file_request": STATIC,
    # ── /api/v1 (GET = READ, POST / PATCH / PUT / DELETE = WRITE) ──
    "api_v1.state": READ,
    "api_v1.health": READ,
    "api_v1.get_scoreboard": READ,
    "api_v1.get_games": READ,
    "api_v1.get_current_game": READ,
    "api_v1.get_match_names": READ,
    "api_v1.get_characters": READ,
    "api_v1.get_variants": READ,
    "api_v1.get_sets": READ,
    "api_v1.get_match": READ,
    "api_v1.get_commentary": READ,
    "api_v1.get_country_states": READ,
    "api_v1.get_layouts": READ,
    "api_v1.get_commentary_options": READ,
    "api_v1.get_selected_layout": READ,
    "api_v1.get_stream_channels": READ,
    "api_v1.get_stream_thumbnail_options": READ,
    "api_v1.score_adjust": WRITE,
    "api_v1.score_reset": WRITE,
    "api_v1.stats_refresh": WRITE,
    "api_v1.swap_teams": WRITE,
    "api_v1.apply_changes": WRITE,
    "api_v1.team_color": WRITE,
    "api_v1.set_update": WRITE,
    "api_v1.score_set": WRITE,
    "api_v1.player_update": WRITE,
    "api_v1.display_options_update": WRITE,
    "api_v1.clear": WRITE,
    "api_v1.reset_players": WRITE,
    "api_v1.reset_match": WRITE,
    "api_v1.load_set": WRITE,
    "api_v1.pull_stream": WRITE,
    "api_v1.tournament_load": WRITE,
    "api_v1.game_set": WRITE,
    "api_v1.bracket_refresh": WRITE,
    "api_v1.commentary_update": WRITE,
    "api_v1.commentary_reorder": WRITE,
    "api_v1.commentary_player": WRITE,
    "api_v1.select_layout": WRITE,
    "api_v1.colors_update": WRITE,
    "api_v1.stream_update": WRITE,
    "api_v1.stream_channel_add": WRITE,
    "api_v1.stream_channel_update": WRITE,
    "api_v1.stream_channel_delete": WRITE,
    "api_v1.stream_org_import": WRITE,
    "api_v1.stream_org_reset": WRITE,
    "api_v1.stream_thumbnail_options_update": WRITE,
    "api_v1.stream_thumbnail_generate": WRITE,
}

# Every Socket.IO event on "/". Same rule as ROUTE_ACCESS (pinned against the
# event ledger; an unlisted event is guarded like a write). `connect` is READ:
# every client may connect and receive the state; the connect handler records
# whether the connection may also write (see Authorize).
EVENT_ACCESS = {
    "connect": READ,
    "program_state": READ,
    "program-state-update": READ,
    "games": READ,
    "current_game": READ,
    "match_names": READ,
    "characters": READ,
    "variants": READ,
    "get_swap": READ,
    "get_sets": READ,
    "get_match": READ,
    "get_comms": READ,
    "get_set": READ,
    "states": READ,
    "playerdb": PLAYERDB,
    "score": WRITE,
    "team_scoreup": WRITE,
    "team_scoredown": WRITE,
    "team_color": WRITE,
    "crew_member_stock": WRITE,
    "crew_set_active": WRITE,
    "set": WRITE,
    "update_team": WRITE,
    "update_commentary": WRITE,
    "update_game": WRITE,
    "swap_teams": WRITE,
    "open_set": WRITE,
    "pull_stream": WRITE,
    "stats_recent_sets": WRITE,
    "stats_upset_factor": WRITE,
    "stats_last_sets": WRITE,
    "stats_history_sets": WRITE,
    "reset_scores": WRITE,
    "reset_match": WRITE,
    "reset_players": WRITE,
    "clear_all": WRITE,
    "load_set": WRITE,
    "update_bracket": WRITE,
    "load_player_from_tag": WRITE,
    "load_commentator_from_tag": WRITE,
    "set_tournament": WRITE,
}


def RouteAccess(endpoint):
    """The access class of a Flask endpoint; unknown ones are WRITE."""
    return ROUTE_ACCESS.get(endpoint, WRITE)


def EventAccess(event):
    """The access class of a Socket.IO event; unknown ones are WRITE."""
    return EVENT_ACCESS.get(event, WRITE)


# ── Static files ────────────────────────────────────────────────────────
# What the overlays (layout/), the Web UI (web/build/, its fonts and icons in
# assets/), the file:// fallback (out/program_state.json) and the state's
# image paths (./assets/..., ./user_data/games/..., team / sponsor logos,
# avatars, custom flags, the tournament banner) load over HTTP. Compared
# case-insensitively: the Windows filesystem serves /User_Data/... too.
STATIC_DIRECTORIES = (
    "layout",
    "assets",
    "web/build",
    "out",
    "user_data/games",
    "user_data/team_logo",
    "user_data/sponsor_logo",
    "user_data/player_avatar",
    "user_data/additional_flag",
)
STATIC_FILES = (
    "user_data/tournament_banner.png",
)

_STATIC_DIRECTORY_PARTS = tuple(tuple(d.split("/")) for d in STATIC_DIRECTORIES)


def StaticPath(filename):
    """The served path for ``filename``, or None when it is not served.

    ``filename`` is the URL path below ``/`` as Flask decoded it. The answer
    is the same path, normalized (``/`` separators, no empty or ``.``
    segments), when it lies in one of STATIC_DIRECTORIES or is one of
    STATIC_FILES. Refused outright, before any comparison: ``..`` segments,
    absolute paths, backslash tricks (they are separators on Windows, so
    they are read as such), drive letters and NTFS streams (any ``:``) and
    NUL. A segment Windows would read differently (``layout.``, a short
    ``8.3`` name) does not equal an allowed name, so it is refused too: the
    comparison only ever lets a path *into* an allowed folder.
    """
    if not isinstance(filename, str) or not filename:
        return None
    path = filename.replace("\\", "/")
    if "\x00" in path or ":" in path or path.startswith("/"):
        return None
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    if not parts:
        return None
    folded = tuple(part.lower() for part in parts)
    for directory in _STATIC_DIRECTORY_PARTS:
        if len(folded) > len(directory) and folded[:len(directory)] == directory:
            return "/".join(parts)
    if "/".join(folded) in STATIC_FILES:
        return "/".join(parts)
    return None


# ── React panels (ADR-005 S4) ───────────────────────────────────────────
# The desktop panels moved to React, served at /web/<panel>: web_index
# answers these names with the Web UI's page and any other with 404. The
# page is a file like /web (STATIC): what it then does through the API is
# held to the rules above -- no token from this PC (the desktop's embedded
# view, AutonaWebPanelHost), the token from another computer. The Web UI
# routes the same names (web/src/PanelShell/registry.tsx; the two lists
# are compared by tests/test_web_access.py). "shell-check" is the empty page
# that checks the panel shell itself (S4a); "commentary" is the caster
# line-up (S4b); "stream" is 配信設定 (S4e).
WEB_PANELS = frozenset({
    "shell-check",
    "commentary",
    "stream",
})


def IsWebPanel(name):
    """Whether /web/``name`` is a React panel's page."""
    return isinstance(name, str) and name in WEB_PANELS


class RefusalLog:
    """Whether a refusal is the first of its kind (a warning) or a repeat.

    A browser source retries a missing file on every reload and a Stream
    Deck button may be pressed all evening; one warning per key keeps the
    log readable. The set is bounded: past ``limit`` keys every refusal is a
    repeat (the caller logs those at debug), and the overflow is said once.
    """

    def __init__(self, limit=256):
        self._lock = threading.Lock()
        self._seen = set()
        self._limit = limit
        self._overflowed = False

    def First(self, key):
        with self._lock:
            if key in self._seen:
                return False
            if len(self._seen) >= self._limit:
                if not self._overflowed:
                    self._overflowed = True
                    logger.warning(
                        "webserver: too many distinct refused requests; "
                        "further ones are logged at debug level")
                return False
            self._seen.add(key)
            return True


# ── Token ───────────────────────────────────────────────────────────────
TOKEN_SETTING = "general.webserver_token"
TOKEN_HEADER = "X-Autona-Token"
TOKEN_QUERY = "token"
# What Token() keeps when found in settings.json: token_urlsafe's alphabet
# (it travels in ?token= unescaped), long enough to be a secret. Anything
# else -- a hand-edited blank or a short word -- is replaced.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,256}")
# A supplied value longer than this is not compared (bounded work).
_TOKEN_MAX_INPUT = 512

_tokenLock = threading.Lock()
# The token in use, once read from settings.json or issued; "" when there is
# none to use (see _LoadToken). Kept in memory so that settings.json being
# unreadable or edited later never changes it behind the user's back: only
# RegenerateToken() does.
_token = None
# Bumped by every issue. A Socket.IO connection that proved itself with the
# token remembers the generation, so a regenerated token cuts it off at its
# next event instead of at its next reconnect (GrantStillValid).
_tokenGeneration = 0


def Token():
    """The access token: read from settings.json on first use (issued and
    saved there when settings.json has none), then kept.

    "" when there is no usable token -- settings.json could not be read, or
    holds a malformed one. Nothing is issued then (the user's devices hold
    the real one); with no token every guarded LAN request is refused until
    RegenerateToken() (API Settings) issues one.
    """
    global _token
    with _tokenLock:
        if _token is None:
            _token = _LoadToken()
        return _token


def RegenerateToken():
    """Replace the token; the old one stops working at once."""
    global _token
    with _tokenLock:
        _token = _IssueToken()
        token = _token
    logger.info("webserver: issued a new access token")
    return token


def TokenGeneration():
    return _tokenGeneration


def _LoadToken():
    # Caller holds _tokenLock.
    if SettingsManager.loadFailed:
        # settings.json is there but could not be read: it looks empty, but
        # the token in it is the one the user's devices hold. Issuing (and
        # saving) one now would replace it -- and the whole file with it.
        logger.warning(
            "webserver: settings.json could not be read, so the access token "
            "is unknown; requests that need it are refused until it is "
            "regenerated in API Settings or Autona is restarted")
        return ""
    token = SettingsManager.Get(TOKEN_SETTING)
    if token is None:
        logger.info("webserver: issuing the access token (first launch)")
        return _IssueToken()
    if isinstance(token, str) and _TOKEN_PATTERN.fullmatch(token):
        return token
    logger.warning(
        f"webserver: {TOKEN_SETTING} in settings.json is malformed (not a "
        "token Autona issued); requests that need the token are refused "
        "until it is regenerated in API Settings")
    return ""


def _IssueToken():
    # Caller holds _tokenLock.
    global _tokenGeneration
    token = secrets.token_urlsafe(24)
    SettingsManager.Set(TOKEN_SETTING, token)
    _tokenGeneration += 1
    return token


def SuppliedTokens(headers, args, auth=None):
    """Every token a request offers, in any of the accepted places.

    ``Authorization: Bearer <token>``, the ``X-Autona-Token`` header, the
    ``?token=`` query parameter, and for a Socket.IO connection its
    ``auth`` payload (``{"token": ...}``). ``headers`` / ``args`` are
    mappings with ``.get`` (Flask's request.headers / request.args).
    """
    found = []
    authorization = (headers.get("Authorization") or "").strip()
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        found.append(value.strip())
    for value in (headers.get(TOKEN_HEADER), args.get(TOKEN_QUERY)):
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    if isinstance(auth, dict):
        value = auth.get("token")
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return found


def TokenMatches(supplied):
    """Whether any of ``supplied`` is the token. Constant-time per value."""
    if not supplied:
        return False
    expected = Token().encode("ascii")
    if not expected:
        return False   # no usable token: nothing matches
    matched = False
    for value in supplied:
        given = (value.encode("utf-8")
                 if isinstance(value, str) and len(value) <= _TOKEN_MAX_INPUT
                 else b"")
        # Bytes: compare_digest refuses non-ASCII str, and the value is
        # whatever the client sent.
        matched |= hmac.compare_digest(given, expected)
    return matched


# ── Origins ─────────────────────────────────────────────────────────────
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _Address(value):
    try:
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address


def IsLoopback(remote_addr):
    """127.0.0.0/8, ::1 and their IPv4-mapped form. Anything else is not."""
    address = _Address(remote_addr)
    return address is not None and address.is_loopback


def IsThisComputer(remote_addr, localAddresses=()):
    """Whether a peer is this PC: the loopback, or one of ``localAddresses``
    (this PC's own LAN addresses -- a packet from one of them was sent by
    this PC, e.g. a browser here opening the LAN-IP URL)."""
    address = _Address(remote_addr)
    if address is None:
        return False
    if address.is_loopback:
        return True
    return any(address == _Address(local) for local in localAddresses)


# A request relayed by a proxy (a local reverse proxy, a tunnel) reaches us
# from the loopback for a client that may be anywhere: it is not "this PC".
FORWARDING_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP")


def IsForwarded(headers):
    """Whether ``headers`` (a mapping with case-insensitive ``in``, like
    Flask's request.headers) carry any proxy forwarding header."""
    return any(name in headers for name in FORWARDING_HEADERS)


def NormalizeOrigin(origin):
    """``scheme://host:port`` with the default port spelled out, or None.

    None for anything that is not a tuple origin: ``null`` (sandboxed
    iframes, file:// pages, data: URLs), an empty header, garbage.
    """
    if not isinstance(origin, str):
        return None
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    # An origin is scheme://host[:port] and nothing else. Userinfo, a path or
    # a query would let "evil@127.0.0.1:5107" read as this server.
    if (parts.username is not None or parts.password is not None
            or parts.path or parts.query or parts.fragment):
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS[scheme]
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def OwnOrigins(port, extraHosts=()):
    """The origins of this server's own pages: loopback names + ``extraHosts``
    (this PC's LAN addresses), all at ``port`` -- the port the server listens
    on. Also what an ambient request's ``Host`` must be (as ``http://Host``)."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return frozenset()
    origins = set()
    for host in tuple(LOOPBACK_HOSTS) + tuple(h for h in extraHosts if h):
        normalized = NormalizeOrigin(f"http://{host}:{port}")
        if normalized:
            origins.add(normalized)
    return frozenset(origins)


# ── The decision ────────────────────────────────────────────────────────
UNAUTHENTICATED = "UNAUTHENTICATED"   # 401
FORBIDDEN = "FORBIDDEN"               # 403

# How a request was let in (Authorize's first value).
GRANT_TOKEN = "token"       # it carried the token
GRANT_AMBIENT = "ambient"   # from this PC, to this server, by its own page

# The Sec-Fetch-Site values an ambient write may carry (an allowlist). No
# header at all (a non-browser client, an older browser) is allowed too.
# same-site is not: it is another origin of the same site, e.g. a page on
# another port of this PC, whose no-cors GETs carry no Origin.
_AMBIENT_FETCH_SITES = ("same-origin", "none")


class Refusal(NamedTuple):
    status: int
    code: str
    message: str
    reason: str   # short, for the log


# A Socket.IO connection let in with a token that has since been replaced.
TOKEN_REPLACED = Refusal(
    401, UNAUTHENTICATED,
    "the token was regenerated; reconnect with the new one", "token_replaced")


def Authorize(*, remoteAddr, tokens, origin, fetchSite, host, ownOrigins,
              localAddresses=(), forwarded=False):
    """``(grant, None)`` when a guarded request may go ahead, else
    ``(None, refusal)``.

    ``tokens`` is SuppliedTokens(); ``origin`` / ``fetchSite`` / ``host`` are
    the ``Origin`` / ``Sec-Fetch-Site`` / ``Host`` headers (None when
    absent); ``ownOrigins`` is OwnOrigins(); ``localAddresses`` are this PC's
    LAN addresses; ``forwarded`` is IsForwarded(). Only call this for
    GUARDED requests -- reads need nothing.

    A request that may go ahead without the token is let in that way
    (GRANT_AMBIENT) even when it also carries the token, so a Socket.IO
    connection from this PC's own page outlives a regenerated token. The
    token is only read when the request needs and offers it.
    """
    refusal = _AmbientRefusal(remoteAddr, origin, fetchSite, host, ownOrigins,
                              localAddresses, forwarded)
    if refusal is None:
        return GRANT_AMBIENT, None
    if tokens and TokenMatches(tokens):
        return GRANT_TOKEN, None
    if tokens and refusal.status == 401:
        return None, Refusal(
            401, UNAUTHENTICATED,
            "invalid token (see API Settings in Autona)", "invalid_token")
    return None, refusal


def _AmbientRefusal(remoteAddr, origin, fetchSite, host, ownOrigins,
                    localAddresses, forwarded):
    """None when a request may write without the token, else why not."""
    if forwarded or not IsThisComputer(remoteAddr, localAddresses):
        return Refusal(
            401, UNAUTHENTICATED,
            "a token is required for this request from another computer "
            "(see API Settings in Autona)",
            "forwarded" if forwarded else "token_required")
    # From this PC. Any page open in a browser here is such a request, so it
    # has to be addressed to this server by one of its own names (a page on
    # a DNS-rebound name sends that name as Host) ...
    if not isinstance(host, str) or NormalizeOrigin(
            f"http://{host.strip()}") not in ownOrigins:
        return Refusal(
            401, UNAUTHENTICATED,
            "a token is required for requests under this host name "
            "(see API Settings in Autona)", "foreign_host")
    # ... and come from one of this server's own pages.
    if isinstance(fetchSite, str):
        site = fetchSite.strip().lower()
        if site not in _AMBIENT_FETCH_SITES:
            return Refusal(
                403, FORBIDDEN, "requests from other sites are not allowed",
                {"cross-site": "cross_site",
                 "same-site": "same_site"}.get(site, "fetch_site"))
    if origin is not None and NormalizeOrigin(origin) not in ownOrigins:
        return Refusal(
            403, FORBIDDEN, "requests from other pages are not allowed",
            "foreign_origin")
    return None


def GrantStillValid(grant):
    """Whether a Socket.IO connection's recorded grant still holds.

    ``grant`` is what the connection was let in with: ``(GRANT_AMBIENT,
    None)`` holds for its lifetime, ``(GRANT_TOKEN, generation)`` until the
    token is regenerated. Anything else (None: the connection was refused
    writing at connect) does not hold.
    """
    if not isinstance(grant, tuple) or len(grant) != 2:
        return False
    kind, generation = grant
    if kind == GRANT_AMBIENT:
        return True
    return kind == GRANT_TOKEN and generation == _tokenGeneration


def ErrorBody(refusal):
    """The /api/v1 error envelope for a refusal (spec §2.5)."""
    return {"ok": False,
            "error": {"code": refusal.code, "message": refusal.message}}
